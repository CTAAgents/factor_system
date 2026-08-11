"""
scripts/benchmark_executor.py — 执行器后端吞吐基准（C4 分布式挖掘工厂，2026-08-11）

对合成批量评估任务（numpy 纯计算，模拟因子粗筛的 IC 计算负载）分别用
thread / process / dask(LocalCluster) 三个后端跑 ``ExecutorBackend.map``，
输出对比表，作为 Stage 3 分布式验收报告的数据来源。

用法:
    python scripts/benchmark_executor.py                 # 全后端（thread/process/dask）
    python scripts/benchmark_executor.py --backend dask  # 仅 dask
    python scripts/benchmark_executor.py --tasks 200 --rows 2000 --cols 50

说明:
    - dask 未安装或 LocalCluster 创建失败 → 跳过 dask 并提示（不阻断）
    - 单机 LocalCluster 为"多节点调度语义"的本地模拟（部署后置，见 plans/23 C4）
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("benchmark_executor")


def _eval_task(seed: int, rows: int, cols: int) -> float:
    """合成评估任务：模拟因子粗筛负载（多列因子值 vs 前向收益的秩相关均值）。

    返回一个标量（平均 IC 值），纯 numpy 计算，CPU 密集。
    负载随 rows/cols 放大（默认 100×50k×10 列 ≈ 0.5s/任务，调度开销占比下降）。
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    factor = rng.standard_normal((rows, cols))
    fwd_ret = rng.standard_normal(rows)
    ret_rank = np.argsort(np.argsort(fwd_ret))
    n = rows
    if n <= 1:
        return 0.0
    ics = []
    for c in range(cols):
        f_rank = np.argsort(np.argsort(factor[:, c]))
        ics.append(float(np.corrcoef(f_rank, ret_rank)[0, 1]))
    return float(np.mean(ics))


def _run_backend(backend: str, tasks: int, rows: int, cols: int, workers: int) -> float:
    """跑一个后端的批量 map，返回耗时秒数。"""
    from fts.factor_engine.executor_backend import create_executor_backend

    args = [(i, rows, cols) for i in range(tasks)]
    with create_executor_backend(backend, workers) as be:
        t0 = time.perf_counter()
        list(be.map(_eval_task, *zip(*args)) if args else iter(()))
        return time.perf_counter() - t0


def _run_dask(tasks: int, rows: int, cols: int, workers: int) -> float | None:
    """跑 dask 后端；不可用时返回 None。"""
    try:
        return _run_backend("dask", tasks, rows, cols, workers)
    except Exception as e:  # noqa: BLE001 - 集群不可用降级跳过
        logger.warning("dask 基准失败，跳过: %s", e)
        return None


def main(tasks: int, rows: int, cols: int, workers: int, only: str) -> int:
    """执行吞吐基准，输出对比表。

    Args:
        tasks: 合成任务数
        rows: 每个任务的样本行数
        cols: 每个任务的因子列数
        workers: 后端并行数
        only: 仅跑指定后端（""=全部）

    Returns:
        int: 0=成功
    """
    print("=" * 64)
    print(f"  执行器后端吞吐基准（C4）— {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  任务数={tasks}  行数={rows}  列数={cols}  workers={workers}")
    print("=" * 64)

    backends = [only] if only else ["thread", "process", "dask"]
    results: dict[str, float] = {}
    for backend in backends:
        if backend == "dask":
            sec = _run_dask(tasks, rows, cols, workers)
        else:
            sec = _run_backend(backend, tasks, rows, cols, workers)
        if sec is not None:
            results[backend] = sec
            print(f"  [{backend}] {tasks} 任务完成，耗时 {sec:.3f}s")
        else:
            print("  [dask] 跳过（不可用）")

    print("-" * 64)
    print("  对比表（耗时越低越好）:")
    print(f"  {'后端':<10} {'耗时(s)':>10} {'吞吐(任务/s)':>14}  相对thread")
    base = results.get("thread")
    for name, sec in results.items():
        thr = tasks / sec if sec > 0 else 0.0
        rel = f"{base / sec:.2f}x" if base and sec > 0 else "-"
        print(f"  {name:<10} {sec:>10.3f} {thr:>14.1f}  {rel}")

    if "dask" in results and "process" in results:
        print("-" * 64)
        ratio = results["dask"] / results["process"]
        note = (
            "达标（≥ 单机 process 近似吞吐）"
            if ratio <= 1.2
            else "低于单机 process（调度/序列化开销，部署后置以真实集群为准）"
        )
        print(f"  dask vs process: {ratio:.2f}x  → {note}")

    print("=" * 64)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="执行器后端吞吐基准")
    parser.add_argument("--tasks", type=int, default=100, help="合成任务数（默认 100）")
    parser.add_argument("--rows", type=int, default=50000, help="每个任务的样本行数（默认 50000）")
    parser.add_argument("--cols", type=int, default=10, help="每个任务的因子列数（默认 10）")
    parser.add_argument("--workers", type=int, default=4, help="后端并行数（默认 4）")
    parser.add_argument("--backend", type=str, default="", help="仅跑指定后端（thread/process/dask，空=全部）")
    args = parser.parse_args()
    sys.exit(main(args.tasks, args.rows, args.cols, args.workers, args.backend))
