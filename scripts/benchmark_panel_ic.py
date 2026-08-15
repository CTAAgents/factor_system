"""
scripts/benchmark_panel_ic.py — 预对齐面板 + 全矩阵化 IC 基准（L2 演化提速验证）

对比两条路径（同一候选因子、同一数据）：
  [旧路径] 逐品种 1D 执行 + pd.Series/reindex 对齐 + 逐日 spearmanr 循环
           （等价 fts/factor_engine/evaluation_chain._cs_execute_factors +
             _cs_build_matrices + _cs_compute_ics 的组合语义）
  [新路径] 一次性预对齐 2D 面板（(dates, symbols) 连续 float64）+ 因子 2D 向量化执行
           + 委托生产模块 fts/factor_engine/panel_vector 计算全矩阵化截面 IC

正确性验收：两条路径逐日 IC 序列完全一致（含跳期模式，rtol/atol 断言）。
性能验收：输出旧/新路径耗时与加速比；预对齐与预计算的一次性成本单独列示。

作用域说明：
    - 仅覆盖「面板对齐 + 因子执行 + 截面 IC」核心数学路径；
    - 因子 2D 执行（pandas 向量化）为基准脚本内实现；IC 计算直接调用生产模块
      panel_vector.compute_cs_ics_vectorized（验证的是生产代码）；
    - 不含沙箱 exec 编译、optuna、走航、审计等外围环节（正交优化，另行基准）；
    - 不改动生产评估主链路（panel_vector 为独立候选实现）。

用法:
    python scripts/benchmark_panel_ic.py                          # 合成数据（默认 104 品种 × 5163 日）
    python scripts/benchmark_panel_ic.py --symbols 59 --days 2500
    python scripts/benchmark_panel_ic.py --real                   # 从 data/fts_history.duckdb 加载真实面板
    python scripts/benchmark_panel_ic.py --candidates 20          # 多候选批量对比（预计算摊薄收益）

版本: v0.2.0（接入生产模块 panel_vector）
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 全矩阵化生产实现（基准验证的是生产代码，而非脚本内副本）
from fts.factor_engine.panel_vector import (
    AlignedPanel,
    compute_cs_ics_vectorized,
    prealign_panel,
)

# ── 面板尺寸默认值（对齐生产规模：104 品种 × 5163 日，日线） ──
_DEFAULT_SYMBOLS: int = 104
_DEFAULT_DAYS: int = 5163
_FORWARD_DAYS: int = 5  # 与 _cs_execute_factors 的 5 日前向收益口径一致


# ══════════════════════════════════════════════════════════
# 1. 面板数据（合成 / 真实）
# ══════════════════════════════════════════════════════════

def _synth_panel(n_symbols: int, n_dates: int, seed: int) -> dict[str, pd.DataFrame]:
    """生成确定性合成面板：dict[symbol -> OHLCV DataFrame]。

    随机游走收盘价 + 量价相关成交量 + 噪声 OHLC，与真实日线统计特征近似。
    所有品种共享同一日期索引（合成场景无缺口，对齐成本为零下界）。
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2026-07-31", periods=n_dates)
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        # 品种差异化波动率与漂移，避免截面同构
        drift = rng.normal(0.0, 0.0004)
        vol = 0.008 + 0.008 * (i % 7) / 7.0
        rets = rng.normal(drift, vol, n_dates)
        close = 100.0 * np.exp(np.cumsum(rets))
        open_ = close * (1.0 + rng.normal(0.0, 0.0015, n_dates))
        high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.002, n_dates)))
        low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.002, n_dates)))
        volume = rng.lognormal(11.5, 0.6, n_dates).astype(np.float64)
        amount = volume * close
        panel[f"SYM{i:04d}"] = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "amount": amount,
            },
            index=dates,
        )
    return panel


def _load_real_panel(db_path: str, end_date: str = "2026-07-31", min_periods: int = 252) -> dict[str, pd.DataFrame]:
    """从 L2 行情库加载真实期货日线面板（口径对齐 scripts/run_futures_evolution.py）。"""
    import duckdb

    con = duckdb.connect(str(db_path))
    try:
        symbols = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT symbol FROM kline_cache WHERE period='daily' ORDER BY symbol"
            ).fetchall()
        ]
        panel: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            rows = con.execute(
                "SELECT date, open, high, low, close, volume, amount FROM kline_cache "
                "WHERE symbol = ? AND period = 'daily' ORDER BY date",
                [sym],
            ).fetchall()
            if len(rows) < min_periods:
                continue
            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df = df.astype(float)
            df = df[df.index <= end_date]
            panel[sym] = df
    finally:
        con.close()
    return panel


# ══════════════════════════════════════════════════════════
# 2. 预对齐面板
# ══════════════════════════════════════════════════════════
# AlignedPanel / prealign_panel / compute_cs_ics_vectorized 由生产模块
# fts/factor_engine/panel_vector.py 提供（本脚本顶部已导入），此处不再重复定义。

# ══════════════════════════════════════════════════════════
# 3. 代表性因子（1D 与 2D 数学严格一致）
# ══════════════════════════════════════════════════════════

def _zscore_close_1d(df: pd.DataFrame, window: int) -> np.ndarray:
    """zscore(close, window)：1D（逐品种 Series 语义）。"""
    c = df["close"]
    mu = c.rolling(window).mean()
    sd = c.rolling(window).std()
    sig = (c - mu) / sd.replace(0.0, np.nan)
    return sig.to_numpy(dtype=np.float64)


def _zscore_close_2d(ap: AlignedPanel, window: int) -> np.ndarray:
    """zscore(close, window)：2D（逐列等价于 1D）。"""
    c = ap.col("close")
    df = pd.DataFrame(c, columns=ap.symbols)
    mu = df.rolling(window).mean()
    sd = df.rolling(window).std()
    sig = (df - mu) / sd.replace(0.0, np.nan)
    return sig.to_numpy(dtype=np.float64)


def _momentum_1d(df: pd.DataFrame, window: int) -> np.ndarray:
    """momentum(close, window)：1D。"""
    c = df["close"]
    sig = c / c.shift(window) - 1.0
    return sig.to_numpy(dtype=np.float64)


def _momentum_2d(ap: AlignedPanel, window: int) -> np.ndarray:
    """momentum(close, window)：2D。"""
    df = pd.DataFrame(ap.col("close"), columns=ap.symbols)
    sig = df / df.shift(window) - 1.0
    return sig.to_numpy(dtype=np.float64)


def _volume_ratio_1d(df: pd.DataFrame, window: int) -> np.ndarray:
    """volume/wma(volume, window) - 1：1D。"""
    v = df["volume"]
    sig = v / v.rolling(window).mean() - 1.0
    return sig.to_numpy(dtype=np.float64)


def _volume_ratio_2d(ap: AlignedPanel, window: int) -> np.ndarray:
    """volume/wma(volume, window) - 1：2D。"""
    df = pd.DataFrame(ap.col("volume"), columns=ap.symbols)
    sig = df / df.rolling(window).mean() - 1.0
    return sig.to_numpy(dtype=np.float64)


# ══════════════════════════════════════════════════════════
# 4. 旧路径（逐品种 1D + 逐日 spearmanr，忠实复刻 FTS 语义）
# ══════════════════════════════════════════════════════════

def old_execute_and_ic(
    panel: dict[str, pd.DataFrame],
    ap: AlignedPanel,
    factor_1d: Callable[[pd.DataFrame, int], np.ndarray],
    window: int,
) -> tuple[list[float], list[int]]:
    """旧路径：逐品种执行 → 对齐 → 逐日 spearmanr 循环。

    语义对齐 FTS `_cs_execute_factors` + `_cs_build_matrices` + `_cs_compute_ics`：
        - 每品种独立计算 5 日前向收益；
        - 信号/收益在共同日期轴上（与预对齐面板同一坐标）取值，缺失留 NaN；
        - 每日在截面内做 Spearman IC，有效样本 < 5 或任一输入常数则跳过该日。

    注意：为与预对齐面板保持「同一共同日期轴」，本原型先对齐后计算（FTS 原实现
    为「整段计算后 reindex」，二者仅在共同日期边界处可能略有差异；原型统一轴
    保证正确性对比纯净，边界差异属评估口径正交问题）。
    返回 (IC 序列, 对应日期行号)。
    """
    common_dates = ap.dates
    signal_dict: dict[str, pd.Series] = {}
    ret_dict: dict[str, pd.Series] = {}
    for sym, df in panel.items():
        df_a = df.reindex(common_dates)
        sig_arr = factor_1d(df_a, window)
        signal_dict[sym] = pd.Series(sig_arr, index=common_dates)
        closes = df_a["close"].to_numpy(dtype=np.float64)
        fwd_ret = np.zeros(len(closes))
        if len(closes) > _FORWARD_DAYS:
            fwd_ret[: -_FORWARD_DAYS] = (closes[_FORWARD_DAYS:] - closes[:-_FORWARD_DAYS]) / np.maximum(
                closes[:-_FORWARD_DAYS], 1e-10
            )
        ret_dict[sym] = pd.Series(fwd_ret, index=common_dates)

    symbols_list = list(signal_dict.keys())
    n_dates = len(common_dates)
    n_stocks = len(symbols_list)
    signal_matrix = np.zeros((n_dates, n_stocks))
    ret_matrix = np.zeros((n_dates, n_stocks))
    for j, sym in enumerate(symbols_list):
        signal_matrix[:, j] = signal_dict[sym].values
        ret_matrix[:, j] = ret_dict[sym].values

    ics: list[float] = []
    rows: list[int] = []
    for t in range(n_dates):
        sig_t = signal_matrix[t, :]
        ret_t = ret_matrix[t, :]
        valid = ~(np.isnan(sig_t) | np.isnan(ret_t))
        if np.sum(valid) < 5:
            continue
        sig_valid = sig_t[valid]
        ret_valid = ret_t[valid]
        if np.std(sig_valid) < 1e-10 or np.std(ret_valid) < 1e-10:
            continue
        ic_val, _ = sp_stats.spearmanr(sig_valid, ret_valid)
        if not np.isnan(ic_val):
            ics.append(float(ic_val))
            rows.append(t)
    return ics, rows


# ══════════════════════════════════════════════════════════
# 5. 新路径（2D 执行 + 全矩阵化 IC）
# ══════════════════════════════════════════════════════════

def new_execute_and_ic(
    ap: AlignedPanel,
    factor_2d: Callable[[AlignedPanel, int], np.ndarray],
    window: int,
) -> tuple[list[float], list[int]]:
    """新路径：2D 因子执行 → compute_cs_ics_vectorized（生产模块）。

    因子执行仍为 2D pandas 向量化（逐列等价 1D）；IC 计算委托生产模块
    fts/factor_engine/panel_vector.compute_cs_ics_vectorized（联合掩码 rank +
    行内 Pearson 全矩阵化），与旧路径逐日一致。
    返回 (IC 序列, 对应日期行号)，与旧路径逐日一致。
    """
    signal = factor_2d(ap, window)
    ics, _ = compute_cs_ics_vectorized(signal, ap.fwd_returns)

    seq: list[float] = []
    rows: list[int] = []
    for t, v in enumerate(ics):
        if not np.isnan(v):
            seq.append(float(v))
            rows.append(t)
    return seq, rows


# ══════════════════════════════════════════════════════════
# 6. 基准驱动
# ══════════════════════════════════════════════════════════

@dataclass
class Candidate:
    """单个候选因子配置（因子族 + 参数窗）。"""

    name: str
    factor_1d: Callable[[pd.DataFrame, int], np.ndarray]
    factor_2d: Callable[[AlignedPanel, int], np.ndarray]
    window: int


@dataclass
class BenchResult:
    """单候选基准结果。"""

    name: str
    old_sec: float = 0.0
    new_sec: float = 0.0
    ic_max_diff: float = 0.0
    ic_identical: bool = False
    old_ic_n: int = 0
    new_ic_n: int = 0


def _build_candidates(n_candidates: int, seed: int) -> list[Candidate]:
    """构造 n 个候选因子（因子族轮换 + 参数窗扫描，模拟演化后代多样性）。"""
    rng = np.random.default_rng(seed)
    families: list[tuple[str, Callable, Callable]] = [
        ("zscore", _zscore_close_1d, _zscore_close_2d),
        ("momentum", _momentum_1d, _momentum_2d),
        ("volume_ratio", _volume_ratio_1d, _volume_ratio_2d),
    ]
    cands: list[Candidate] = []
    for i in range(n_candidates):
        name, f1, f2 = families[i % len(families)]
        window = int(rng.integers(5, 60))
        cands.append(Candidate(name=f"{name}({window})", factor_1d=f1, factor_2d=f2, window=window))
    return cands


def _bench_single(cand: Candidate, panel: dict[str, pd.DataFrame], ap: AlignedPanel, repeats: int) -> BenchResult:
    """单候选：正确性断言 + 计时（best-of-repeats，warmup 1 次）。"""
    res = BenchResult(name=cand.name)

    # warmup（含编译/缓存冷启动，不计时）
    old_execute_and_ic(panel, ap, cand.factor_1d, cand.window)
    new_execute_and_ic(ap, cand.factor_2d, cand.window)

    best_old = best_new = np.inf
    for _ in range(repeats):
        t0 = time.perf_counter()
        old_ics, old_rows = old_execute_and_ic(panel, ap, cand.factor_1d, cand.window)
        best_old = min(best_old, time.perf_counter() - t0)
        t0 = time.perf_counter()
        new_ics, new_rows = new_execute_and_ic(ap, cand.factor_2d, cand.window)
        best_new = min(best_new, time.perf_counter() - t0)

    res.old_sec = best_old
    res.new_sec = best_new
    res.old_ic_n = len(old_ics)
    res.new_ic_n = len(new_ics)

    # 正确性：行号一一对应 + IC 值逐位近似
    if old_rows == new_rows and len(old_ics) == len(new_ics):
        arr_old = np.asarray(old_ics, dtype=np.float64)
        arr_new = np.asarray(new_ics, dtype=np.float64)
        res.ic_max_diff = float(np.max(np.abs(arr_old - arr_new))) if len(arr_old) else 0.0
        res.ic_identical = bool(np.allclose(arr_old, arr_new, rtol=1e-6, atol=1e-9))
    return res


def _run_benchmark(
    panel: dict[str, pd.DataFrame],
    n_candidates: int,
    repeats: int,
    seed: int,
    min_symbols: int = 10,
) -> tuple[list[BenchResult], float]:
    """跑完整基准：预对齐计时 + 逐候选对比。"""
    t0 = time.perf_counter()
    ap = prealign_panel(panel, min_symbols=min_symbols)
    prealign_sec = time.perf_counter() - t0
    print(f"  [预对齐+预计算] {len(ap.symbols)} 品种 × {len(ap.dates)} 日，耗时 {prealign_sec * 1000:.1f} ms")

    cands = _build_candidates(n_candidates, seed)
    results = [ _bench_single(c, panel, ap, repeats) for c in cands ]
    return results, prealign_sec


def _print_report(results: list[BenchResult], prealign_sec: float, n_candidates: int) -> None:
    """输出对比表 + 汇总。"""
    total_old = sum(r.old_sec for r in results)
    total_new = sum(r.new_sec for r in results)
    amd = total_old / max(total_new, 1e-12)
    print("-" * 78)
    print(f"  {'候选因子':<22}{'旧路径(s)':>10}{'新路径(s)':>10}{'加速比':>8}{'max|ΔIC|':>12}")
    print("-" * 78)
    for r in results:
        speed = r.old_sec / max(r.new_sec, 1e-12)
        flag = "OK" if r.ic_identical else f"FAIL(n_old={r.old_ic_n},n_new={r.new_ic_n})"
        print(f"  {r.name:<22}{r.old_sec:>10.4f}{r.new_sec:>10.4f}{speed:>8.1f}x{max(r.ic_max_diff, 0.0):>12.2e}  {flag}")
    print("-" * 78)
    print(f"  {n_candidates} 个候选合计：旧 {total_old:.3f}s → 新 {total_new:.3f}s，总加速 {amd:.1f}x")
    if n_candidates > 1:
        print(f"  （含预对齐/预计算摊薄后净加速 {total_old / max(total_new + prealign_sec, 1e-12):.1f}x）")
    ic_ok = all(r.ic_identical for r in results)
    print(f"\n  [正确性] 新旧 IC 逐日一致：{'通过 ✅' if ic_ok else '失败 ❌'}")
    if not ic_ok:
        sys.exit(2)


def main() -> int:
    """基准入口。"""
    parser = argparse.ArgumentParser(description="预对齐面板 + 全矩阵化 IC 原型基准")
    parser.add_argument("--symbols", type=int, default=_DEFAULT_SYMBOLS, help="合成品种数（默认 104）")
    parser.add_argument("--days", type=int, default=_DEFAULT_DAYS, help="合成天数（默认 5163）")
    parser.add_argument("--real", action="store_true", help="从 data/fts_history.duckdb 加载真实面板")
    parser.add_argument("--candidates", type=int, default=5, help="候选因子数（默认 5）")
    parser.add_argument("--repeats", type=int, default=3, help="每候选计时轮数（取最优，默认 3）")
    parser.add_argument("--min-symbols", type=int, default=10, help="共同日期最少品种数（默认 10）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42）")
    args = parser.parse_args()

    print("=" * 78)
    print(f"  预对齐面板 + 全矩阵化 IC 原型基准  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 78)

    if args.real:
        panel = _load_real_panel(PROJECT_ROOT / "data" / "fts_history.duckdb")
        print(f"  [数据] 真实期货面板：{len(panel)} 品种")
    else:
        panel = _synth_panel(args.symbols, args.days, args.seed)
        print(f"  [数据] 合成面板：{len(panel)} 品种 × {len(next(iter(panel.values())))} 日（seed={args.seed}）")

    results, prealign_sec = _run_benchmark(panel, args.candidates, args.repeats, args.seed, args.min_symbols)
    _print_report(results, prealign_sec, args.candidates)
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
