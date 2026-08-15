"""tests/factor_engine/test_ops_native_batch3.py — 批 3 算子向量化改写对照测试。

37 号计划 Phase 2 Step 2 批 3：ops_library 真滚动回调（ts_cvar_95/99、CCI md、
ts_aroon_up/down、ts_linear_trend_score、ts_amp/ts_amp_ratio、cs_trim_mean_diff、
cs_gini_score、cs_concentration、cs_top_bottom_spread、ts_volume_concentration、
ts_volume_cycle）+ regime_features._rolling_autocorr + gp_evolver 模板 ts_product
由 rolling.apply（Python 回调）改写为 sliding_window_view 向量化内核，
逐算子与旧实现（oracle）在随机 / 含 NaN / 常数 / 单调 / 短序列 / 空 / 单元素
场景上逐位对照，保证零语义漂移。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine import gp_evolver
from fts.factor_engine.ops_library import D10Ops, D11Ops, D12Ops, D13Ops, D16Ops
from fts.factor_engine.regime_features import _rolling_autocorr

_MINP = 2


# ─── 场景构造与断言 ─────────────────────────────────────────


def _series_cases(n: int = 300) -> dict[str, pd.Series]:
    rng = np.random.default_rng(42)
    base = rng.normal(1.0, 0.3, n)
    with_nan = base.copy()
    for idx in rng.choice(n, 5, replace=False):
        with_nan[idx] = np.nan
    return {
        "random": pd.Series(rng.normal(1.0, 0.3, n)),
        "with_nan": pd.Series(with_nan),
        "constant": pd.Series(np.full(n, 5.0)),
        "monotonic": pd.Series(np.arange(n, dtype=float)),
        "short": pd.Series(rng.normal(0.0, 1.0, 8)),
        "empty": pd.Series([], dtype=float),
        "single": pd.Series([3.0]),
    }


def _assert_same(res, oracle) -> None:
    a = np.asarray(res.to_numpy(dtype=float)) if isinstance(res, pd.Series) else np.asarray(res, dtype=float)
    b = np.asarray(oracle.to_numpy(dtype=float)) if isinstance(oracle, pd.Series) else np.asarray(oracle, dtype=float)
    assert a.shape == b.shape
    if a.size:
        assert np.allclose(a, b, rtol=1e-9, atol=1e-12, equal_nan=True)


def _check_1d(func, oracle_fn, window: int) -> None:
    for s in _series_cases().values():
        _assert_same(func(s, window), oracle_fn(s, window))


# ─── oracle：改写前的 rolling.apply 实现 ─────────────────────


def _oracle_cvar(s: pd.Series, window: int, q: float) -> pd.Series:
    r = s.pct_change().fillna(0.0)

    def _f(x: np.ndarray) -> float:
        qq = np.nanquantile(x, q)
        tail = x[x <= qq]
        return float(np.mean(tail)) if len(tail) else float(qq)

    return r.rolling(window, min_periods=_MINP).apply(_f, raw=True).fillna(0.0)


def _oracle_cci_md(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> pd.Series:
    tp = (high + low + close) / 3.0
    ma = tp.rolling(window, min_periods=_MINP).mean()
    md = tp.rolling(window, min_periods=_MINP).apply(lambda x: float(np.mean(np.abs(x - np.mean(x)))), raw=True)
    return ((tp - ma) / (0.015 * md.replace(0.0, np.nan))).fillna(0.0)


def _oracle_aroon_up(s: pd.Series, window: int) -> pd.Series:
    hh = s.rolling(window, min_periods=_MINP).apply(lambda x: float(np.argmax(x)), raw=True)
    return (100.0 * (window - hh) / window).fillna(0.0)


def _oracle_aroon_down(s: pd.Series, window: int) -> pd.Series:
    ll = s.rolling(window, min_periods=_MINP).apply(lambda x: float(np.argmin(x)), raw=True)
    return (100.0 * (window - ll) / window).fillna(0.0)


def _oracle_linear_trend(s: pd.Series, window: int) -> pd.Series:
    r2 = s.rolling(window, min_periods=5).apply(
        lambda x: float(np.corrcoef(np.arange(len(x)), x)[0, 1] ** 2) if np.std(x) > 0 else 0.0,
        raw=True,
    )
    return r2.fillna(0.0).clip(0.0, 1.0)


def _oracle_range_expansion(s: pd.Series, window: int) -> pd.Series:
    amp = s.rolling(window, min_periods=_MINP).apply(lambda x: float(np.ptp(x)), raw=True)
    prev = amp.shift(window).replace(0.0, np.nan)
    return (amp / prev).fillna(1.0)


def _oracle_sideways_flag(s: pd.Series, window: int) -> pd.Series:
    amp = s.rolling(window, min_periods=_MINP).apply(
        lambda x: float(np.ptp(x)) / max(abs(float(np.mean(x))), 1e-9), raw=True
    )
    return (amp < 0.05).astype(float)


def _oracle_trim_mean_diff(s: pd.Series, window: int) -> pd.Series:
    tm = s.rolling(window, min_periods=_MINP).apply(
        lambda x: float(np.mean(np.sort(x)[int(len(x) * 0.1) : max(int(len(x) * 0.9), 1)])), raw=True
    )
    return (s - tm).fillna(0.0)


def _oracle_gini(s: pd.Series, window: int) -> pd.Series:
    def _f(x: np.ndarray) -> float:
        x = np.sort(x[~np.isnan(x)])
        n = len(x)
        if n < 2:
            return 0.0
        return float((2.0 * np.sum(np.arange(1, n + 1) * x) / (n * np.sum(x)) - (n + 1.0) / n)) if np.sum(x) else 0.0

    return s.rolling(window, min_periods=_MINP).apply(_f, raw=True).fillna(0.0)


def _oracle_conc(s: pd.Series, window: int) -> pd.Series:
    def _f(x: np.ndarray) -> float:
        x = np.sort(x[~np.isnan(x)])[::-1]
        n = len(x)
        if n < 2 or np.sum(x) == 0:
            return 0.0
        k = max(1, n // 5)
        return float(np.sum(x[:k]) / np.sum(x))

    return s.rolling(window, min_periods=_MINP).apply(_f, raw=True).fillna(0.0)


def _oracle_spread(s: pd.Series, window: int) -> pd.Series:
    def _f(x: np.ndarray) -> float:
        x = np.sort(x[~np.isnan(x)])
        if len(x) < 10:
            return 0.0
        k = max(1, len(x) // 10)
        return float(np.mean(x[-k:]) - np.mean(x[:k]))

    return s.rolling(window, min_periods=_MINP).apply(_f, raw=True).fillna(0.0)


def _oracle_volume_cycle(volume: pd.Series, window: int) -> pd.Series:
    mu = volume.rolling(window, min_periods=_MINP).mean()
    sd = volume.rolling(window, min_periods=_MINP).std().replace(0.0, np.nan)
    vol_z = ((volume - mu) / sd).fillna(0.0)
    return (
        vol_z.rolling(window, min_periods=_MINP)
        .apply(lambda x: float(np.argmax(x)) if len(x) else 0.0, raw=True)
        .fillna(0.0)
    )


# ─── ops_library：14 处真滚动回调 ────────────────────────────


@pytest.mark.parametrize("window", [3, 10, 60])
def test_cvar_95(window: int) -> None:
    _check_1d(lambda s, w: D10Ops.ts_cvar_95(s, w), lambda s, w: _oracle_cvar(s, w, 0.05), window)


@pytest.mark.parametrize("window", [3, 10, 60])
def test_cvar_99(window: int) -> None:
    _check_1d(lambda s, w: D10Ops.ts_cvar_99(s, w), lambda s, w: _oracle_cvar(s, w, 0.01), window)


@pytest.mark.parametrize("window", [3, 10, 20])
def test_cci_md(window: int) -> None:
    rng = np.random.default_rng(7)
    high = pd.Series(rng.normal(10.0, 0.5, 300))
    low = pd.Series(rng.normal(9.0, 0.5, 300))
    close = pd.Series(rng.normal(9.5, 0.5, 300))
    high.iloc[17] = np.nan
    low.iloc[51] = np.nan
    _assert_same(D11Ops.ts_cci(high, low, close, window), _oracle_cci_md(high, low, close, window))


@pytest.mark.parametrize("window", [3, 10, 25])
def test_aroon_up(window: int) -> None:
    _check_1d(lambda s, w: D11Ops.ts_aroon_up(s, w), _oracle_aroon_up, window)


@pytest.mark.parametrize("window", [3, 10, 25])
def test_aroon_down(window: int) -> None:
    _check_1d(lambda s, w: D11Ops.ts_aroon_down(s, w), _oracle_aroon_down, window)


@pytest.mark.parametrize("window", [5, 10, 20])
def test_linear_trend_score(window: int) -> None:
    _check_1d(lambda s, w: D12Ops.ts_linear_trend_score(s, w), _oracle_linear_trend, window)


@pytest.mark.parametrize("window", [3, 10, 20])
def test_range_expansion(window: int) -> None:
    _check_1d(lambda s, w: D12Ops.ts_range_expansion(s, w), _oracle_range_expansion, window)


@pytest.mark.parametrize("window", [3, 10, 20])
def test_sideways_flag(window: int) -> None:
    _check_1d(lambda s, w: D12Ops.ts_sideways_flag(s, w), _oracle_sideways_flag, window)


@pytest.mark.parametrize("window", [3, 10, 20])
def test_trim_mean_diff(window: int) -> None:
    _check_1d(lambda s, w: D13Ops.cs_trim_mean_diff(s, w), _oracle_trim_mean_diff, window)


@pytest.mark.parametrize("window", [3, 10, 20])
def test_gini_score(window: int) -> None:
    _check_1d(lambda s, w: D13Ops.cs_gini_score(s, w), _oracle_gini, window)


@pytest.mark.parametrize("window", [3, 10, 20])
def test_concentration(window: int) -> None:
    _check_1d(lambda s, w: D13Ops.cs_concentration(s, w), _oracle_conc, window)


@pytest.mark.parametrize("window", [3, 10, 20])
def test_top_bottom_spread(window: int) -> None:
    _check_1d(lambda s, w: D13Ops.cs_top_bottom_spread(s, w), _oracle_spread, window)


@pytest.mark.parametrize("window", [3, 10, 20])
def test_volume_concentration(window: int) -> None:
    rng = np.random.default_rng(11)
    volume = pd.Series(rng.lognormal(mean=8.0, sigma=1.0, size=300))
    volume.iloc[23] = np.nan
    _assert_same(D16Ops.ts_volume_concentration(volume, window), _oracle_conc(volume, window))


@pytest.mark.parametrize("window", [3, 10, 20])
def test_volume_cycle(window: int) -> None:
    rng = np.random.default_rng(13)
    volume = pd.Series(rng.lognormal(mean=8.0, sigma=1.0, size=300))
    volume.iloc[31] = np.nan
    _assert_same(D16Ops.ts_volume_cycle(volume, window), _oracle_volume_cycle(volume, window))


# ─── DataFrame 面板路径（_native_apply 逐列包装） ─────────────


def test_panel_2d_columns_match() -> None:
    rng = np.random.default_rng(19)
    idx = pd.date_range("2020-01-01", periods=200, freq="D")
    df = pd.DataFrame(rng.normal(1.0, 0.3, (200, 3)), index=idx, columns=["A", "B", "C"])
    res = _native_apply_df_check(df)
    for col in df.columns:
        _assert_same(res[col], _oracle_gini(df[col], 10))


def _native_apply_df_check(df: pd.DataFrame) -> pd.DataFrame:
    # D13Ops.cs_gini_score 的 _native_apply 内部支持 DataFrame 逐列包装
    return D13Ops.cs_gini_score(df, 10)


# ─── regime_features._rolling_autocorr ──────────────────────


def _oracle_acf(s: pd.Series, lag: int = 1) -> pd.Series:
    return s.rolling(20, min_periods=5).apply(
        lambda x: x.autocorr(lag=lag) if len(x) > lag else 0.0,
        raw=False,
    )


@pytest.mark.parametrize("lag", [1, 3])
def test_rolling_autocorr(lag: int) -> None:
    for s in _series_cases().values():
        _assert_same(_rolling_autocorr(s, lag=lag), _oracle_acf(s, lag=lag))


# ─── gp_evolver 模板 ts_product ─────────────────────────────


def _gp_factor_output(expression: str, data: dict[str, pd.Series]):
    code = gp_evolver._GP_FACTOR_CODE_TEMPLATE.format(
        factor_id="fct_batch3_test",
        expression=gp_evolver._render_expression(expression),
    )
    ns: dict = {}
    exec(code, ns)  # noqa: S102 — 测试自生成模板代码
    return ns["factor_program"](data, {})


@pytest.mark.parametrize("window", [3, 10, 20])
def test_gp_ts_product(window: int) -> None:
    rng = np.random.default_rng(23)
    data = {"close": pd.Series(rng.normal(1.0, 0.2, 300))}
    data["close"].iloc[41] = np.nan
    out = _gp_factor_output(f"ts_product(close, {window})", data)
    oracle = data["close"].rolling(window).apply(np.prod, raw=True).to_numpy()
    assert out.shape == oracle.shape
    assert np.allclose(out, oracle, rtol=1e-12, atol=1e-12, equal_nan=True)
