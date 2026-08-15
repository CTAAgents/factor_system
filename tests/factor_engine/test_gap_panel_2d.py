"""tests/factor_engine/test_gap_panel_2d.py — plans/39 5.1 缺口感知滚动内核对照测试。

39 号计划（缺口面板 2D 化）5.1 阶段：``_rolling_apply_native`` 在
``gap_aware_mode`` 作用域内把含缺口（NaN）列压缩为密集序列（= 品种自身日历上
的观测序列）计算后散射回原位置，与逐品种（逐列 pandas rolling）语义逐位一致；
无缺口列走既有快路径逐位不变（零漂移）。

本测试覆盖 39-gap-panel-2d-plan.md §5.1 验收：
- 无缺口零漂移：on/off 输出逐位一致 + 与 pandas oracle 一致；
- 合成缺口面板与逐品种一致：内部/头部/尾部缺口 → 压缩-散射 == 逐品种；
- 边界：全 NaN 列 / 窗口大于数据长度 / n < min_periods。

版本: v1.0.0（39-5.1t 随测）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.feature_ops import _rolling_apply_native, gap_aware_mode

_MP = 2  # 与 ops_library._MINP 对齐（真实算子统一最小窗口期）


# ─── 测试内核（复用真实 row_fn/batch_fn 语义） ──────────────


def _kernel_mean(arr: np.ndarray, window: int, min_periods: int = _MP) -> np.ndarray:
    """缺口感知滚动均值内核（线性统计代表）。"""
    row_fn = lambda v: float(np.nanmean(v))
    batch_fn = lambda rows: np.nanmean(rows, axis=-1)
    with gap_aware_mode():
        return _rolling_apply_native(arr, window, min_periods, row_fn, batch_fn)


def _kernel_cvar(arr: np.ndarray, window: int, min_periods: int = _MP) -> np.ndarray:
    """缺口感知滚动 CVaR(5%) 内核（非线性统计代表，复用 ts_cvar_95 语义）。"""

    def row_fn(v: np.ndarray) -> float:
        q = np.nanquantile(v, 0.05)
        tail = v[v <= q]
        return float(np.mean(tail)) if tail.size else float(q)

    def batch_fn(rows: np.ndarray) -> np.ndarray:
        s = np.sort(rows, axis=-1)
        pos = 0.05 * (rows.shape[1] - 1)
        lo, hi = int(np.floor(pos)), int(np.ceil(pos))
        q = s[:, lo] + (s[:, hi] - s[:, lo]) * (pos - lo)
        mask = rows <= q[:, None]
        cnt = mask.sum(axis=-1)
        return np.where(cnt > 0, np.where(mask, rows, 0.0).sum(axis=-1) / cnt, q)

    with gap_aware_mode():
        return _rolling_apply_native(arr, window, min_periods, row_fn, batch_fn)


# ─── 逐品种 oracle：对非 NaN 观测序列滚动后散射回原位置 ────


def _per_symbol_oracle(
    arr: np.ndarray,
    window: int,
    min_periods: int,
    pandas_fn,
) -> np.ndarray:
    """逐品种语义：dense = 非 NaN 观测（品种自身日历）→ pandas rolling → 散射回原位置。"""
    idx = np.flatnonzero(~np.isnan(arr))
    out = np.full(len(arr), np.nan, dtype=float)
    if len(idx) == 0:
        return out
    dense = pd.Series(arr[idx], dtype=float)
    r = pandas_fn(dense, window, min_periods).to_numpy(dtype=float)
    out[idx] = r
    return out


def _roll_mean(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    return s.rolling(window, min_periods=min_periods).mean()


def _roll_cvar(s: pd.Series, window: int, min_periods: int) -> pd.Series:
    def _cvar(v: np.ndarray) -> float:
        q = np.nanquantile(v, 0.05)
        tail = v[v <= q]
        return float(np.mean(tail)) if tail.size else float(q)

    return s.rolling(window, min_periods=min_periods).apply(_cvar, raw=True)


def _assert_nan_eq(a: np.ndarray, b: np.ndarray, atol: float = 1e-9, rtol: float = 1e-9) -> None:
    """NaN 位置逐位一致 + 有限值容差一致。"""
    assert a.shape == b.shape, f"形状不一致: {a.shape} vs {b.shape}"
    nan_a, nan_b = np.isnan(a), np.isnan(b)
    assert (nan_a == nan_b).all(), f"NaN 位置不一致: {(nan_a != nan_b).sum()} 处"
    finite = ~nan_a
    np.testing.assert_allclose(a[finite], b[finite], atol=atol, rtol=rtol)


# ─── 无缺口零漂移 ───────────────────────────────────────────


@pytest.mark.parametrize("window", [5, 20, 60])
def test_gap_free_zero_drift_on_off(window: int) -> None:
    """无缺口列：gap_aware_mode on/off 输出逐位一致（零漂移铁律 §6.1）。"""
    rng = np.random.default_rng(0)
    arr = rng.standard_normal(120)

    def off(arr_in: np.ndarray) -> np.ndarray:
        row_fn = lambda v: float(np.nanmean(v))
        batch_fn = lambda rows: np.nanmean(rows, axis=-1)
        return _rolling_apply_native(arr_in, window, _MP, row_fn, batch_fn)

    on = _kernel_mean(arr, window)
    _assert_nan_eq(on, off(arr))
    # 且 on 结果 == 面板化 on 的散列语义 == 面板 off（零漂移跨模式一致）
    _assert_nan_eq(on, _kernel_mean(arr.copy(), window))


@pytest.mark.parametrize("window", [5, 20, 60])
def test_gap_free_matches_pandas_oracle(window: int) -> None:
    """无缺口列：on 输出与 pandas rolling oracle 一致（回归既有 native 对照）。"""
    rng = np.random.default_rng(1)
    arr = rng.standard_normal(120)
    _assert_nan_eq(_kernel_mean(arr, window), _roll_mean(pd.Series(arr), window, _MP).to_numpy())
    _assert_nan_eq(_kernel_cvar(arr, window), _roll_cvar(pd.Series(arr), window, _MP).to_numpy())


# ─── 合成缺口面板与逐品种一致 ───────────────────────────────


@pytest.mark.parametrize("window", [5, 20, 60])
def test_internal_gap_matches_per_symbol_mean(window: int) -> None:
    """内部随机删行缺口：压缩-散射 == 逐品种滚动均值。"""
    rng = np.random.default_rng(2)
    arr = rng.standard_normal(200)
    arr[rng.random(200) < 0.15] = np.nan  # 内部+随机缺口
    got = _kernel_mean(arr, window)
    exp = _per_symbol_oracle(arr, window, _MP, _roll_mean)
    _assert_nan_eq(got, exp)


@pytest.mark.parametrize("window", [5, 20, 60])
def test_internal_gap_matches_per_symbol_cvar(window: int) -> None:
    """内部随机删行缺口：压缩-散射 == 逐品种滚动 CVaR（非线性统计）。"""
    rng = np.random.default_rng(3)
    arr = rng.standard_normal(200)
    arr[rng.random(200) < 0.15] = np.nan
    got = _kernel_cvar(arr, window)
    exp = _per_symbol_oracle(arr, window, _MP, _roll_cvar)
    _assert_nan_eq(got, exp)


@pytest.mark.parametrize("window", [5, 20, 60])
def test_head_gap_matches_per_symbol(window: int) -> None:
    """头部缺口（前 5 个 NaN）：压缩-散射与逐品种一致（头部提前输出）。"""
    rng = np.random.default_rng(4)
    arr = rng.standard_normal(120)
    arr[:5] = np.nan
    _assert_nan_eq(_kernel_mean(arr, window), _per_symbol_oracle(arr, window, _MP, _roll_mean))
    _assert_nan_eq(_kernel_cvar(arr, window), _per_symbol_oracle(arr, window, _MP, _roll_cvar))


@pytest.mark.parametrize("window", [5, 20, 60])
def test_tail_gap_matches_per_symbol(window: int) -> None:
    """尾部缺口（后 3 个 NaN）：压缩-散射与逐品种一致（尾部散射回 NaN）。"""
    rng = np.random.default_rng(5)
    arr = rng.standard_normal(120)
    arr[-3:] = np.nan
    _assert_nan_eq(_kernel_mean(arr, window), _per_symbol_oracle(arr, window, _MP, _roll_mean))
    _assert_nan_eq(_kernel_cvar(arr, window), _per_symbol_oracle(arr, window, _MP, _roll_cvar))


# ─── 边界场景 ───────────────────────────────────────────────


def test_all_nan_column() -> None:
    """全 NaN 列：压缩后为空 → 输出全 NaN。"""
    arr = np.full(100, np.nan)
    _assert_nan_eq(_kernel_mean(arr, 20), np.full(100, np.nan))
    _assert_nan_eq(_kernel_cvar(arr, 20), np.full(100, np.nan))


@pytest.mark.parametrize("window", [5, 20])
def test_window_larger_than_dense_len(window: int) -> None:
    """窗口大于缺口列有效观测数：按 min_periods 判定，与逐品种一致。"""
    arr = np.full(100, np.nan)
    arr[10:25] = np.arange(15, dtype=float)  # 仅 15 个有效观测
    _assert_nan_eq(_kernel_mean(arr, window), _per_symbol_oracle(arr, window, _MP, _roll_mean))
    _assert_nan_eq(_kernel_cvar(arr, window), _per_symbol_oracle(arr, window, _MP, _roll_cvar))


def test_n_below_min_periods() -> None:
    """有效观测数 < min_periods：输出全 NaN（与逐品种一致）。"""
    arr = np.full(100, np.nan)
    arr[40:41] = [3.0]  # 仅 1 个有效观测 < _MP
    _assert_nan_eq(_kernel_mean(arr, 20), _per_symbol_oracle(arr, 20, _MP, _roll_mean))


@pytest.mark.parametrize("window", [5, 20])
def test_panel_dataframe_matches_per_symbol(window: int) -> None:
    """面板 DataFrame（多列含缺口）：逐列压缩-散射与逐品种一致。"""
    from fts.factor_engine.ops_library import _native_apply

    rng = np.random.default_rng(6)
    n = 120
    df = pd.DataFrame(
        {
            "A": rng.standard_normal(n),
            "B": np.concatenate([np.full(6, np.nan), rng.standard_normal(n - 6)]),
            "C": np.where(rng.random(n) < 0.2, np.nan, rng.standard_normal(n)),
        },
        dtype=float,
    )

    row_fn = lambda v: float(np.nanmean(v))
    batch_fn = lambda rows: np.nanmean(rows, axis=-1)
    with gap_aware_mode():
        got = _native_apply(df, window, _MP, row_fn, batch_fn)
    for col in df.columns:
        exp = _per_symbol_oracle(df[col].to_numpy(dtype=float), window, _MP, _roll_mean)
        _assert_nan_eq(got[col].to_numpy(dtype=float), exp)


# ─── 5.2/5.3 算子级面板验证（execute_factor_panel 端到端） ──


def _operator_factor(expression: str) -> dict:
    return {
        "factor_id": "fct_gap_probe",
        "trace_id": "t",
        "kind": "operator",
        "expression": expression,
        "code": "def factor_program(data, params):\n    import numpy as np\n    return np.asarray(data['close'], dtype=np.float64)\n",
        "params": {},
    }


def _make_panel_with_gap(n_symbols: int, n_dates: int, seed: int) -> dict[str, pd.DataFrame]:
    """构造含内部缺口的合成面板：每品种随机删 15% 内部行（模拟停牌/未上市）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2026-07-31", periods=n_dates)
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_symbols):
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n_dates)))
        df = pd.DataFrame(
            {
                "open": close * (1.0 + rng.normal(0.0, 0.002, n_dates)),
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": rng.lognormal(11.0, 0.5, n_dates),
                "amount": close,
            },
            index=dates,
        )
        drop_idx = rng.choice(range(20, n_dates - 20), size=int((n_dates - 40) * 0.15), replace=False)
        keep = dates.delete(drop_idx)
        panel[f"S{i}"] = df.loc[keep]
    return panel


def _per_symbol_factor(expression: str, panel: dict[str, pd.DataFrame], common: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    from fts.factor_engine.expr_dsl import build_registry, evaluate, parse_expression

    node = parse_expression(expression)
    reg = build_registry()
    out: dict[str, np.ndarray] = {}
    for sym, df in panel.items():
        arr = np.asarray(evaluate(node, df, reg), dtype=np.float64)
        out[sym] = pd.Series(arr, index=df.index).reindex(common).to_numpy(dtype=np.float64)
    return out


# 5.2 线性 + 5.3 直滚非线性：面板化且与逐品种一致
_PANELIZED_EXPRS = [
    "ts_mean(close, 10)",
    "ts_sum(close, 10)",
    "ts_std(close, 10)",
    "ts_min(close, 10)",
    "ts_max(close, 10)",
    "ts_rank(close, 10)",
    "ts_quantile(close, 10, 0.5)",
    "ts_slope(close, 10)",
    "ts_skewness(close, 10)",
    "ts_kurtosis(close, 10)",
    "ts_median(close, 10)",
]


@pytest.mark.parametrize("expr", _PANELIZED_EXPRS)
def test_operator_panelized_matches_per_symbol(expr: str) -> None:
    """5.2/5.3 算子：缺口面板 execute_factor_panel 面板化且与逐品种逐位一致。"""
    from fts.factor_engine.panel_vector import execute_factor_panel, prealign_panel

    panel = _make_panel_with_gap(12, 300, seed=55)
    common = prealign_panel(panel, min_symbols=5).dates
    mat = execute_factor_panel(_operator_factor(expr), panel, common)
    assert mat is not None, f"{expr} 应面板化但回退"
    ref = _per_symbol_factor(expr, panel, common)
    for j, sym in enumerate(panel):
        _assert_nan_eq(mat[:, j], ref[sym])


# 5.3 §7 豁免：pct_change 变换族（.fillna(0) 把缺口填 0，与逐品种 reindex 的
# NaN 语义结构性冲突）→ 保留面板化回退（逐品种路径，无回归，登记豁免）。
_FALLBACK_EXPRS = [
    "ts_cvar_95(close, 10)",
    "ts_var_95(close, 10)",
    "ts_zscore(close, 10)",
    "ts_realized_vol(close, 10)",
]


@pytest.mark.parametrize("expr", _FALLBACK_EXPRS)
def test_pct_change_family_keeps_fallback(expr: str) -> None:
    """§7 豁免登记：pct_change 预处理族保留面板化回退（逐品种路径，无回归）。"""
    from fts.factor_engine.panel_vector import execute_factor_panel, prealign_panel

    panel = _make_panel_with_gap(12, 300, seed=55)
    common = prealign_panel(panel, min_symbols=5).dates
    assert execute_factor_panel(_operator_factor(expr), panel, common) is None
