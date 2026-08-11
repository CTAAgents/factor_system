"""
fts/factor_engine/executor_backend.py — 可插拔执行器后端（GAP-I502，v2.83.0）

Stage 3 分布式架构预留：挖掘/评估执行器可插拔（本地线程/进程池 → Dask/Ray 集群），
调用方通过统一接口 ``map(fn, *iterables)`` 提交任务，后端切换调用方无感知。

后端:
    - ThreadBackend  : ThreadPoolExecutor（默认，numpy/scipy 纯计算线程并行有效）
    - ProcessBackend : ProcessPoolExecutor（cloudpickle 序列化，CPU 密集任务多进程隔离）
    - DaskBackend    : dask.distributed Client（无 dask 依赖时降级 ProcessBackend）
    - RayBackend     : ray（无 ray 依赖时降级 ProcessBackend）

配置: ``FTSConfig.executor_backend``（"thread"/"process"/"dask"/"ray"）+ ``executor_max_workers``
接入点: ``BatchMiner.filter_batch`` 批量粗筛（GAP-I201 漏斗执行器可插拔）

版本: v1.0.0（GAP-I502，v2.83.0）
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from itertools import repeat
from typing import Any, Callable, Iterable, Iterator, Optional

logger = logging.getLogger(__name__)

# 后端名称白名单（未知名称回退 thread）
_BACKEND_NAMES = ("thread", "process", "dask", "ray")


def _process_worker(payload: bytes, args: tuple) -> Any:
    """进程池工作函数（模块级可 pickle）：反序列化目标函数后执行。"""
    from cloudpickle import loads

    fn = loads(payload)
    return fn(*args)


class ExecutorBackend(ABC):
    """执行器后端抽象（GAP-I502）。

    统一接口:
        ``map(fn, *iterables)`` — 并行执行 fn，结果按输入顺序返回
        ``shutdown()`` — 释放资源（幂等）
    """

    name: str = "base"

    @abstractmethod
    def map(self, fn: Callable[..., Any], *iterables: Iterable[Any]) -> Iterator[Any]:
        """并行执行 fn，返回结果迭代器（保持输入顺序）。

        Args:
            fn: 待并行执行的函数
            *iterables: 与 fn 位置参数对应的可迭代对象

        Returns:
            结果迭代器（惰性求值，按输入顺序产出）
        """

    def shutdown(self) -> None:
        """释放后端资源（幂等）。"""

    def __enter__(self) -> "ExecutorBackend":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.shutdown()


class ThreadBackend(ExecutorBackend):
    """线程池后端（默认，numpy/scipy 纯计算线程并行有效）。"""

    name = "thread"

    def __init__(self, max_workers: int = 4) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max(1, int(max_workers)))

    def map(self, fn: Callable[..., Any], *iterables: Iterable[Any]) -> Iterator[Any]:
        return self._pool.map(fn, *iterables)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


class ProcessBackend(ExecutorBackend):
    """进程池后端（cloudpickle 序列化，CPU 密集任务隔离，bound method/lambda 可跨进程）。"""

    name = "process"

    def __init__(self, max_workers: int = 4) -> None:
        self._pool = ProcessPoolExecutor(max_workers=max(1, int(max_workers)))

    def map(self, fn: Callable[..., Any], *iterables: Iterable[Any]) -> Iterator[Any]:
        from cloudpickle import dumps

        payload = dumps(fn)
        # ProcessPoolExecutor 对 fn 用标准 pickle，故包装为模块级 _process_worker（可 pickle）
        return self._pool.map(_process_worker, repeat(payload), zip(*iterables))

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


class _DaskResultIterator:
    """Dask Future 结果迭代器（逐项取结果，单项异常不影响后续）。

    对齐 concurrent.futures.map 契约：某任务异常在 ``next()`` 时抛出，
    迭代器位置已推进，后续 ``next()`` 继续产出剩余任务结果
    （生成器表达式无法做到——异常会关闭生成器，导致剩余任务丢失）。
    """

    def __init__(self, futures: list[Any]) -> None:
        self._futures = list(futures)
        self._idx = 0

    def __iter__(self) -> "_DaskResultIterator":
        return self

    def __next__(self) -> Any:
        if self._idx >= len(self._futures):
            raise StopIteration
        f = self._futures[self._idx]
        self._idx += 1
        return f.result()


class DaskBackend(ExecutorBackend):
    """Dask 分布式后端（Stage 3 集群；C4 2026-08-11 增强：cluster 注入/故障注入/worker 诊断）。

    无 dask 依赖或集群创建失败时降级 ProcessBackend（缺依赖不阻断主流程）。
    单机 LocalCluster（Client(n_workers)）等价多节点调度语义，真实集群通过
    ``address="tcp://scheduler:8786"`` 接入（部署后置，见 plans/23 C4 实施设计）。
    """

    name = "dask"

    def __init__(
        self,
        max_workers: int = 4,
        address: Optional[str] = None,
        cluster: Optional[Any] = None,
    ) -> None:
        self._degraded: Optional[ExecutorBackend] = None
        self._client: Optional[Any] = None
        self._cluster = cluster
        try:
            from distributed import Client
        except ImportError:
            logger.warning("[ExecutorBackend] dask 未安装，降级 ProcessBackend")
            self._degraded = ProcessBackend(max_workers)
            return
        try:
            if address:
                self._client = Client(address=address)
            elif cluster is not None:
                self._client = Client(cluster)
            else:
                self._client = Client(n_workers=max(1, int(max_workers)), threads_per_worker=1, processes=True)
        except Exception as e:  # noqa: BLE001 - 集群不可用降级
            logger.warning("[ExecutorBackend] dask Client 创建失败(%s)，降级 ProcessBackend", e)
            self._degraded = ProcessBackend(max_workers)
            self._client = None

    @property
    def worker_count(self) -> int:
        """调度器活跃 worker 数（诊断/故障注入；降级或异常返回 0）。

        以 ``client.scheduler_info()`` 为准（retire/kill 后准确反映可调度 worker），
        而非 ``cluster.workers``（LocalCluster 会保留已退休 worker 状态）。
        """
        if self._degraded is not None or self._client is None:
            return 0
        try:
            workers = self._client.scheduler_info().get("workers") or {}
            return len(workers) if workers else 0
        except Exception:  # noqa: BLE001
            return 0

    def kill_worker(self) -> int:
        """退休并关闭一个 worker（故障注入测试，close_workers=True 真正终止进程）。

        调度器将已分配任务重派给存活 worker（dask 自动重算）。返回剩余 worker 数。
        """
        if self._degraded is not None or self._client is None:
            return 0
        try:
            workers = self._client.scheduler_info().get("workers") or {}
            victims = list(workers.keys())
            if not victims:
                return self.worker_count
            import random

            victim = victims[random.randrange(len(victims))]
            self._client.retire_workers(workers=[victim], close_workers=True)
            return self.worker_count
        except Exception:  # noqa: BLE001
            return self.worker_count

    def alive_workers(self) -> int:
        """存活 worker 数（kill_worker 后剩余；降级/异常返回 0）。"""
        return self.worker_count

    def map(self, fn: Callable[..., Any], *iterables: Iterable[Any]) -> Iterator[Any]:
        if self._degraded is not None:
            return self._degraded.map(fn, *iterables)
        assert self._client is not None
        futures = [self._client.submit(fn, *args) for args in zip(*iterables)]
        return _DaskResultIterator(futures)

    def shutdown(self) -> None:
        if self._degraded is not None:
            self._degraded.shutdown()
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass


class RayBackend(ExecutorBackend):
    """Ray 分布式后端（Stage 3 集群）。无 ray 依赖时降级 ProcessBackend。"""

    name = "ray"

    def __init__(self, max_workers: int = 4) -> None:
        self._degraded: Optional[ExecutorBackend] = None
        self._ray = None
        try:
            import ray
        except ImportError:
            logger.warning("[ExecutorBackend] ray 未安装，降级 ProcessBackend")
            self._degraded = ProcessBackend(max_workers)
            return
        try:
            if not ray.is_initialized():
                ray.init(num_cpus=max(1, int(max_workers)), ignore_reinit_error=True)
            self._ray = ray
        except Exception as e:  # noqa: BLE001 - 集群不可用降级
            logger.warning("[ExecutorBackend] ray init 失败(%s)，降级 ProcessBackend", e)
            self._degraded = ProcessBackend(max_workers)
            self._ray = None

    def map(self, fn: Callable[..., Any], *iterables: Iterable[Any]) -> Iterator[Any]:
        if self._degraded is not None:
            return self._degraded.map(fn, *iterables)
        assert self._ray is not None
        from cloudpickle import dumps

        payload = dumps(fn)
        remote = self._ray.remote(_process_worker)
        futures = [remote.remote(payload, args) for args in zip(*iterables)]
        return iter(self._ray.get(futures))

    def shutdown(self) -> None:
        if self._degraded is not None:
            self._degraded.shutdown()


def create_executor_backend(backend: str = "thread", max_workers: int = 4) -> ExecutorBackend:
    """创建执行器后端（工厂，配置驱动）。

    Args:
        backend: "thread"/"process"/"dask"/"ray"（未知名称回退 thread 并告警）
        max_workers: 并行工作数

    Returns:
        ExecutorBackend 实例（dask/ray 缺依赖时自动降级 process）
    """
    name = backend if backend in _BACKEND_NAMES else "thread"
    if name != backend:
        logger.warning("[ExecutorBackend] 未知后端 %r，回退 thread", backend)
    if name == "process":
        return ProcessBackend(max_workers)
    if name == "dask":
        return DaskBackend(max_workers)
    if name == "ray":
        return RayBackend(max_workers)
    return ThreadBackend(max_workers)


__all__ = [
    "ExecutorBackend",
    "ThreadBackend",
    "ProcessBackend",
    "DaskBackend",
    "RayBackend",
    "create_executor_backend",
]
