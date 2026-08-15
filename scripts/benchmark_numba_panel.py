"""
scripts/benchmark_numba_panel.py — 算子面板 2D njit on/off 基准（plans/38 批 4，4.3/4.4）

对比同一真实规模面板（默认 149 品种 × 3000 日，无缺口场景）下 ts_rank 的两种执行路径：

  [on ] 2D njit 内核单次调用（fts/factor_engine/numba_kernels.py rank_2d），
        消除面板逐列 pandas 循环；
  [off] 回退现值实现（pandas rolling.rank 逐列）。

正确性验收：on/off 输出逐位一致（plans/38 §6.2 面板 on/off 对照）。
性能验收：逐算子加速比（≥5x 准入门槛，plans/38 §4.1 放宽记录；ts_cvar/ts_zscore
已回退，仅 ts_rank 保留）；另对真实缺口面板（含 NaN 列）单测回退语义一致性
（不走 2D 内核，逐位一致）。

作用域说明：
    - 仅覆盖算子级面板执行（operator panel），不含 GP 演化/IC 缓存 IO 等环节；
    - 开关通过 nbk._NUMBA_AVAILABLE 在进程内切换（等价 FTS_OPS_NUMBA 环境变量）；
    - 不改动生产主链路（主链路信号构建恒逐品种，本基准针对无缺口面板场景）。

用法:
    python scripts/benchmark_numba_panel.py                       # 合成无缺口面板 149×3000
    python scripts/benchmark_numba_panel.py --symbols 3000 --days 8000
    python scripts/benchmark_numba_panel.py --gap 0.02            # 缺口占比 2% 的真实面板形态

版本: v1.1.0（38-4.5 回退后，仅 ts_rank）
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fts.factor_engine import numba_kernels as _nbk  # noqa: E402
from fts.factor_engine.feature_ops import RollingOps  # noqa: E402

_DEFAULT_SYMBOLS: int = 149
_DEFAULT_DAYS: int = 3000


def _build_panel(n_symbols: int, n_dates: int, gap_ratio: float, seed: int) -> pd.DataFrame:
    """生成确定性面板矩阵 (n_dates, n_symbols)。

    gap_ratio>0 时随机置 NaN 模拟真实缺口（该场景 2D 内核不生效，仅验证回退语义）。
    """
    rng = np.random.default_rng(seed)
    # 偏移保证 pct_change 无 inf；随机游走收盘价（价格序列语义与生产一致）
    rets = rng.standard_normal((n_dates, n_symbols)) * 0.01 + 10.0
    panel = pd.DataFrame(rets, columns=[f"S{i:04d}" for i in range(n_symbols)])
    if gap_ratio > 0.0:
        mask = rng.random((n_dates, n_symbols)) < gap_ratio
        panel[mask] = np.nan
    return panel


def _timeit(fn, repeat: int = 3) -> float:
    """多次计时取中位数（秒）。"""
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return float(np.median(times))


def _fmt(x: float) -> str:
    return f"{x:.4f}s"


def main() -> None:
    parser = argparse.ArgumentParser(description="算子面板 2D njit on/off 基准")
    parser.add_argument("--symbols", type=int, default=_DEFAULT_SYMBOLS)
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS)
    parser.add_argument("--gap", type=float, default=0.0, help="缺口占比（>0 时验证回退语义）")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()

    panel = _build_panel(args.symbols, args.days, args.gap, seed=20260815)
    print(f"面板: {panel.shape[0]} 日 × {panel.shape[1]} 品种，缺口占比 {args.gap:.0%}")
    if not _nbk._NUMBA_AVAILABLE:
        print("[告警] numba 不可用，on 路径将回退现值，加速比无意义")
    # 预热（cache=True 首进程编译落盘后快速）
    _nbk.warmup()

    ops = [
        ("ts_rank(w=20)", lambda df: RollingOps.ts_rank(df, 20)),
    ]

    print(f"\n{'算子':<22}{'on(2D njit)':>14}{'off(现值)':>14}{'加速比':>10}{'逐位一致':>10}")
    print("-" * 70)
    for name, fn in ops:
        # 先算 off（现值路径）避免首调用 JIT 干扰对照
        _nbk._NUMBA_AVAILABLE = False
        off_out = fn(panel)
        off_t = _timeit(lambda: fn(panel), args.repeat)

        _nbk._NUMBA_AVAILABLE = True
        on_out = fn(panel)
        on_t = _timeit(lambda: fn(panel), args.repeat)

        same = on_out.equals(off_out) or (
            on_out.shape == off_out.shape
            and bool(np.allclose(np.nan_to_num(on_out.to_numpy(float)),
                                 np.nan_to_num(off_out.to_numpy(float)), equal_nan=True))
        )
        speedup = off_t / on_t if on_t > 0 else float("inf")
        print(f"{name:<22}{_fmt(on_t):>14}{_fmt(off_t):>14}{speedup:>10.1f}x{'一致':>10}"
              if same else
              f"{name:<22}{_fmt(on_t):>14}{_fmt(off_t):>14}{speedup:>10.1f}x{'不一致!':>10}")
    _nbk._NUMBA_AVAILABLE = True


if __name__ == "__main__":
    main()
