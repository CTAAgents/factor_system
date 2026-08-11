"""tests/factor_engine/test_executor_dask.py — C4 多节点分布式挖掘工厂（单机 LocalCluster 验证）测试。

覆盖（实施设计 2026-08-11，策略：全部先实现、部署后置）:
    1. DaskBackend 本地集群 map 语义（顺序保真/结果正确/worker 诊断）
    2. cluster 句柄注入 / address 优先 / 工厂创建
    3. 故障注入（kill_worker 后任务不中断、alive>=1、后续结果正确）
    4. 缺 dask 依赖降级 ProcessBackend（不阻断主流程）
    5. 一致性（dask vs thread 浮点对齐 < 1e-9）
    6. BatchMiner.filter_batch 集成（executor_backend="dask" 批量粗筛与 process 一致）
"""

from __future__ import annotations

import math
import time

import pytest

from fts.factor_engine.batch_mining import BatchMiner, BatchMiningConfig
from fts.factor_engine.executor_backend import (
    DaskBackend,
    ProcessBackend,
    ThreadBackend,
    create_executor_backend,
)

_AVAILABLE = True
try:  # noqa: SIM105
    import distributed  # noqa: F401
except ImportError:
    _AVAILABLE = False


def _make_proposal(factor_id: str) -> dict:
    """最小合法 BatchedProposal（对齐 test_batch_mining.py）。"""
    return {
        "factor": {"factor_id": factor_id, "name": f"f_{factor_id}", "code": "close - close.shift(1)", "params": {}},
        "parent_id": "parent_1",
        "method": "gp_evolution",
        "summary": "GP Gen=1",
        "tokens": 0,
        "prefilter_ok": False,
        "prefilter_reason": "",
        "prefilter_ic": 0.0,
    }


@pytest.fixture
def dask_backend():
    """本地集群 DaskBackend（dask 不可用时跳过）。"""
    if not _AVAILABLE:
        pytest.skip("dask 未安装，跳过 LocalCluster 用例")
    be = DaskBackend(max_workers=2)
    if be._degraded is not None:
        pytest.skip("dask 集群创建失败（降级），跳过 LocalCluster 用例")
    yield be
    be.shutdown()


@pytest.mark.skipif(not _AVAILABLE, reason="dask 未安装")
class TestDaskBackendLocal:
    """本地集群基础语义。"""

    def test_map_ordered(self, dask_backend):
        """map 结果按输入顺序保真。"""
        r = list(dask_backend.map(lambda x: x * 2, range(8)))
        assert r == [x * 2 for x in range(8)]

    def test_map_result_correct(self, dask_backend):
        """计算任务结果正确。"""
        r = list(dask_backend.map(lambda x: x**2 + 1, [1, 2, 3, 4]))
        assert r == [2, 5, 10, 17]

    def test_worker_count_matches_config(self, dask_backend):
        """worker_count 反映调度器活跃 worker 数（=配置 n_workers）。"""
        assert dask_backend.worker_count >= 2

    def test_shutdown_idempotent(self, dask_backend):
        """shutdown 幂等不抛。"""
        dask_backend.shutdown()
        dask_backend.shutdown()

    def test_worker_count_zero_after_shutdown(self, dask_backend):
        """shutdown 后 worker_count 归 0。"""
        dask_backend.shutdown()
        assert dask_backend.worker_count == 0

    def test_cluster_handle_injection(self):
        """注入外部 LocalCluster 句柄 → Client 正常连接并 map。"""
        from distributed import LocalCluster

        cluster = LocalCluster(n_workers=2, threads_per_worker=1, processes=True)
        try:
            be = DaskBackend(cluster=cluster)
            try:
                assert be._degraded is None
                assert be.worker_count >= 2
                assert list(be.map(lambda x: x + 1, [1, 2, 3])) == [2, 3, 4]
            finally:
                be.shutdown()
        finally:
            cluster.close()

    def test_address_priority_over_cluster(self):
        """address 优先于 cluster（构造不抛且非降级）。"""
        from distributed import LocalCluster

        cluster = LocalCluster(n_workers=1, processes=True)
        try:
            be = DaskBackend(max_workers=1, address=cluster.scheduler_address, cluster=cluster)
            try:
                assert be._degraded is None
                # address 模式 client.cluster 为 None，worker_count 仍可获取
                assert be.worker_count >= 1
            finally:
                be.shutdown()
        finally:
            cluster.close()

    def test_factory_creates_dask(self):
        """create_executor_backend("dask") 返回 DaskBackend 实例。"""
        be = create_executor_backend("dask", max_workers=1)
        assert isinstance(be, DaskBackend)
        be.shutdown()


@pytest.mark.skipif(not _AVAILABLE, reason="dask 未安装")
class TestDaskBackendFaultInjection:
    """worker 故障注入（C4 验收：单 worker 故障不中断整批）。"""

    def _wait_workers(self, be, below: int, timeout: float = 15.0) -> int:
        """轮询等待活跃 worker 数降至 below 以下。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if be.alive_workers() < below:
                break
            time.sleep(0.3)
        return be.alive_workers()

    def test_kill_worker_reduces_and_stays_alive(self, dask_backend):
        """kill_worker 后活跃 worker 减少且 ≥1（故障隔离，调度不中断）。"""
        initial = dask_backend.worker_count
        assert initial >= 2
        dask_backend.kill_worker()
        alive = self._wait_workers(dask_backend, below=initial)
        assert alive >= 1
        assert alive < initial

    def test_map_after_kill_correct(self, dask_backend):
        """kill 一个 worker 后继续提交任务 → 结果正确（调度器重派给存活 worker）。"""
        dask_backend.kill_worker()
        r = list(dask_backend.map(lambda x: x * 3, [1, 2, 3, 4]))
        assert r == [3, 6, 9, 12]

    def test_kill_worker_single_worker_cluster(self):
        """单 worker 集群 kill 后 alive=0，后续任务由 dask 重试/降级不抛异常。"""
        be = DaskBackend(max_workers=1)
        try:
            assert be.worker_count >= 1
            be.kill_worker()
            self._wait_workers(be, below=1, timeout=10)
            assert be.alive_workers() == 0
        finally:
            be.shutdown()

    def test_kill_worker_degraded_returns_zero(self, monkeypatch):
        """降级状态 kill_worker 返回 0（不抛）。"""
        be = DaskBackend(max_workers=2)
        be._degraded = ProcessBackend(1)
        be._client = None
        try:
            assert be.kill_worker() == 0
            assert be.alive_workers() == 0
        finally:
            be._degraded.shutdown()


@pytest.mark.skipif(not _AVAILABLE, reason="dask 未安装")
class TestDaskBackendConsistency:
    """dask 与串行/thread 结果一致性（误差 < 1e-9）。"""

    def test_consistency_with_thread(self, dask_backend):
        """浮点计算 dask 与 thread 结果逐元素对齐。"""
        fn = lambda x: math.sqrt(x) + 0.5 * x  # noqa: E731
        seq = [float(i) / 10 for i in range(1, 21)]
        expected = [fn(x) for x in seq]
        got = list(dask_backend.map(fn, seq))
        assert all(abs(a - b) < 1e-9 for a, b in zip(got, expected))


@pytest.mark.skipif(not _AVAILABLE, reason="dask 未安装")
class TestDaskBackendDegradation:
    """缺 dask 依赖降级（不阻断主流程）。"""

    def test_missing_dask_degraded_to_process(self, monkeypatch):
        """distributed 导入失败 → 降级 ProcessBackend，map 仍可用。"""
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "distributed" or name.startswith("distributed."):
                raise ImportError("no dask")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        be = DaskBackend(max_workers=2)
        try:
            assert be._degraded is not None
            assert isinstance(be._degraded, ProcessBackend)
            assert be.worker_count == 0
            assert be.alive_workers() == 0
            assert list(be.map(lambda x: x + 1, [1, 2, 3])) == [2, 3, 4]
        finally:
            be.shutdown()


@pytest.mark.skipif(not _AVAILABLE, reason="dask 未安装")
class TestDaskBatchMinerIntegration:
    """BatchMiner.filter_batch 在 dask 后端下与 process 一致（C4 接线验证）。"""

    def test_filter_batch_dask_matches_process(self, monkeypatch):
        """同一批候选 dask 粗筛与 process 粗筛结果一致。"""
        from fts.factor_engine import batch_mining as bm

        proposals = [_make_proposal(f"f{i:03d}") for i in range(6)]

        # _filter_one 确定性结果（factor_id 尾部数字 → ic），模拟粗筛
        def _filter_one(p, trace_id):
            ic = int(p["factor"]["factor_id"][-2:]) / 100.0
            return {
                **p,
                "prefilter_ok": ic >= 0.03,
                "prefilter_reason": "" if ic >= 0.03 else "low ic",
                "prefilter_ic": ic,
            }

        results: dict[str, list] = {}
        for backend_name in ("dask", "process"):
            miner = BatchMiner(config=BatchMiningConfig(executor_backend=backend_name, executor_max_workers=2))
            monkeypatch.setattr(miner, "_filter_one", _filter_one)
            monkeypatch.setattr(bm, "create_executor_backend", lambda n, w: create_executor_backend(backend_name, w))
            res = miner.filter_batch([dict(p) for p in proposals], "trace-c4")
            results[backend_name] = [p["factor"]["factor_id"] for p in res.passed]

        assert results["dask"] == results["process"]
        assert len(results["dask"]) == 3  # ic >= 0.03: f000..f005 → 03/04/05

    def test_filter_batch_dask_single_task_failure_isolated(self, monkeypatch):
        """单任务异常在 dask 后端下降级为 rejected，不中断整批。"""
        from fts.factor_engine import batch_mining as bm

        proposals = [_make_proposal(f"f{i:03d}") for i in range(3)]

        def _filter_one(p, trace_id):
            if p["factor"]["factor_id"] == "f001":
                raise RuntimeError("boom")
            return {**p, "prefilter_ok": True, "prefilter_reason": "", "prefilter_ic": 0.1}

        miner = BatchMiner(config=BatchMiningConfig(executor_backend="dask", executor_max_workers=2))
        monkeypatch.setattr(miner, "_filter_one", _filter_one)
        monkeypatch.setattr(bm, "create_executor_backend", lambda n, w: create_executor_backend("dask", w))
        res = miner.filter_batch([dict(p) for p in proposals], "trace-c4-fail")
        # 失败任务被标记 rejected，其余照常通过
        assert len(res.passed) == 2
        assert len(res.rejected) == 1


@pytest.mark.skipif(not _AVAILABLE, reason="dask 未安装")
class TestDaskThreadComparison:
    """dask 与 thread 后端行为对齐（通用执行器抽象契约）。"""

    def test_same_results_as_thread(self, dask_backend):
        """dask map 与 ThreadBackend map 结果一致。"""
        fn = lambda x: (x * 7) % 13  # noqa: E731
        seq = list(range(30))
        thread = ThreadBackend(max_workers=2)
        try:
            expected = list(thread.map(fn, seq))
        finally:
            thread.shutdown()
        got = list(dask_backend.map(fn, seq))
        assert got == expected
