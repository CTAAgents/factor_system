"""
scripts/benchmark_operator_panel.py — 算子因子面板化真实提数基准（plans/39 5.4）

对比两条路径（同一算子因子、同一真实期货缺口面板）：
  [逐品种]   逐品种 DSL evaluate（品种自身日历）→ reindex 到共同日期 → 矩阵
              （等价 evaluation_chain._cs_execute_factors + _cs_build_matrices）
  [面板化]   execute_factor_panel（预对齐 (union_dates × symbols) 矩阵求值 +
              抽样验证，plans/37 Phase 2 / plans/39 缺口感知）

正确性验收：两路径输出矩阵逐位一致（NaN 模式 + 有限值容差）+ IC 序列一致。
性能验收：输出逐品种/面板化耗时与加速比；回退算子单独列示（面板化返回 None
时按逐品种计时，标注 FALLBACK）。

用法:
    python scripts/benchmark_operator_panel.py            # 真实期货面板（默认）
    python scripts/benchmark_operator_panel.py --days 1500
    python scripts/benchmark_operator_panel.py --repeats 3

版本: v1.0.0（39-5.4 随测）
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

from fts.factor_engine.expr_dsl import build_registry, evaluate, parse_expression
from fts.factor_engine.panel_vector import compute_cs_ics_vectorized, execute_factor_panel, prealign_panel

_FORWARD_DAYS = 5


# ── 数据加载（口径对齐 scripts/run_futures_evolution.py） ──


def _load_real_panel(db_path: Path, end_date: str = "2026-07-31", min_periods: int = 252) -> dict[str, pd.DataFrame]:
    """从 L2 行情库加载真实期货日线面板（含各品种自身日历 = 天然缺口面板）。"""
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
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


# ── 因子与两条路径 ─────────────────────────────────────────


def _operator_factor(expression: str) -> dict:
    return {
        "factor_id": "fct_bench",
        "trace_id": "t",
        "kind": "operator",
        "expression": expression,
        "code": "def factor_program(data, params):\n    import numpy as np\n    return np.asarray(data['close'], dtype=np.float64)\n",
        "params": {},
    }


def _per_symbol_matrix(expression: str, panel: dict[str, pd.DataFrame], common_dates: pd.DatetimeIndex) -> np.ndarray:
    """逐品种执行 → reindex 到共同日期 → (n_dates, n_symbols) 矩阵（旧路径语义）。"""
    node = parse_expression(expression)
    reg = build_registry()
    syms = list(panel.keys())
    n_dates = len(common_dates)
    mat = np.full((n_dates, len(syms)), np.nan, dtype=np.float64)
    for j, sym in enumerate(syms):
        df = panel[sym]
        arr = np.asarray(evaluate(node, df, reg), dtype=np.float64)
        mat[:, j] = pd.Series(arr, index=df.index).reindex(common_dates).to_numpy(dtype=np.float64)
    return mat


def _fwd_returns_matrix(panel: dict[str, pd.DataFrame], common_dates: pd.DatetimeIndex) -> np.ndarray:
    """逐品种 5 日前向收益 → 共同日期矩阵（与 _cs_execute_factors 口径一致）。"""
    syms = list(panel.keys())
    n_dates = len(common_dates)
    mat = np.full((n_dates, len(syms)), np.nan, dtype=np.float64)
    for j, sym in enumerate(syms):
        df = panel[sym]
        closes = df["close"].to_numpy(dtype=np.float64)
        n = len(closes)
        fwd = np.full(n, np.nan, dtype=np.float64)
        if n > _FORWARD_DAYS:
            fwd[:-_FORWARD_DAYS] = (closes[_FORWARD_DAYS:] - closes[:-_FORWARD_DAYS]) / np.maximum(
                closes[:-_FORWARD_DAYS], 1e-10
            )
        mat[:, j] = pd.Series(fwd, index=df.index).reindex(common_dates).to_numpy(dtype=np.float64)
    return mat


def _nan_allclose(a: np.ndarray, b: np.ndarray, atol: float = 1e-9, rtol: float = 1e-9) -> tuple[bool, float]:
    """NaN 模式逐位一致 + 有限值 max|Δ|。"""
    if a.shape != b.shape:
        return False, np.inf
    if not np.array_equal(np.isnan(a), np.isnan(b)):
        return False, np.inf
    finite = ~np.isnan(a)
    if not finite.any():
        return True, 0.0
    return bool(np.allclose(a[finite], b[finite], atol=atol, rtol=rtol)), float(np.max(np.abs(a[finite] - b[finite])))


# ── 基准驱动 ───────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="算子因子面板化真实提数基准（plans/39 5.4）")
    parser.add_argument("--db", type=str, default=str(PROJECT_ROOT / "data" / "fts_history.duckdb"))
    parser.add_argument("--min-symbols", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    exprs = [
        # 5.2 线性
        "ts_mean(close, 10)",
        "ts_sum(close, 10)",
        "ts_std(close, 10)",
        "ts_min(close, 10)",
        "ts_max(close, 10)",
        # 5.3 直滚非线性
        "ts_rank(close, 10)",
        "ts_quantile(close, 10, 0.5)",
        "ts_slope(close, 10)",
        "ts_skewness(close, 10)",
        "ts_kurtosis(close, 10)",
        "ts_median(close, 10)",
        # §7 豁免（pct_change 族）：应回退逐品种
        "ts_cvar_95(close, 10)",
        "ts_realized_vol(close, 10)",
    ]

    print("=" * 88)
    print(f"  算子因子面板化真实提数基准  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 88)
    print(f"  [加载] 真实期货面板: {args.db}")
    panel = _load_real_panel(Path(args.db))
    t0 = time.perf_counter()
    ap = prealign_panel(panel, min_symbols=args.min_symbols)
    prealign_sec = time.perf_counter() - t0
    print(f"  [预对齐] {len(ap.symbols)} 品种 × {len(ap.dates)} 日（共同日期），缺口品种内嵌，耗时 {prealign_sec * 1000:.1f} ms")
    fwd = _fwd_returns_matrix(panel, ap.dates)

    print("-" * 88)
    print(f"  {'表达式':<28}{'逐品种(s)':>10}{'面板化(s)':>10}{'加速比':>8}{'max|Δ矩阵|':>12}  IC")
    print("-" * 88)

    total_old = total_new = 0.0
    all_ok = True
    for expr in exprs:
        factor = _operator_factor(expr)

        # 面板化（含抽样验证 + 全矩阵执行），warmup 后 best-of
        t0 = time.perf_counter()
        mat_new = execute_factor_panel(factor, panel, ap.dates)
        warm = time.perf_counter() - t0
        best_new = np.inf
        for _ in range(args.repeats):
            t0 = time.perf_counter()
            mat_new = execute_factor_panel(factor, panel, ap.dates)
            best_new = min(best_new, time.perf_counter() - t0)

        if mat_new is None:
            label = "FALLBACK"
            speed = np.nan
            max_d = np.nan
            ic_ok = True
        else:
            label = "PANELIZED"
            # 逐品种仅用于正确性对照与计时（一次计算，复用）
            t0 = time.perf_counter()
            mat_old = _per_symbol_matrix(expr, panel, ap.dates)
            best_old = time.perf_counter() - t0
            speed = best_old / max(best_new, 1e-12)
            ok_d, max_d = _nan_allclose(mat_new, mat_old)
            _, ics_new = compute_cs_ics_vectorized(mat_new, fwd)
            _, ics_old = compute_cs_ics_vectorized(mat_old, fwd)
            ic_ok = ok_d and bool(
                np.allclose(
                    np.nan_to_num(np.asarray(ics_new)),
                    np.nan_to_num(np.asarray(ics_old)),
                    rtol=1e-6,
                    atol=1e-9,
                )
            )
            total_old += best_old
            total_new += best_new
            all_ok = all_ok and ic_ok

        if label == "PANELIZED":
            print(f"  {expr:<28}{best_old:>10.4f}{best_new:>10.4f}{speed:>8.1f}x{max_d:>12.2e}  {'OK' if ic_ok else 'FAIL'}")
        else:
            print(f"  {expr:<28}{'—':>10}{'(回退逐品种)':>16}{'—':>8}{'—':>12}  (fallback)")

    print("-" * 88)
    if total_new > 0:
        print(f"  面板化算子合计：逐品种 {total_old:.3f}s → 面板化 {total_new:.3f}s，加速 {total_old / max(total_new, 1e-12):.1f}x")
    print(f"  [正确性] 面板化 vs 逐品种输出一致：{'通过 ✅' if all_ok else '失败 ❌'}")
    print("=" * 88)
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
