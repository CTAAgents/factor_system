"""tests/factor_engine/test_rolling_native.py — 算子 native 向量化改写对照测试。

37 号计划 Phase 2 Step 2 批 1：feature_ops 中由 rolling.apply（Python 回调）
改写为 sliding_window_view 向量化实现的算子，逐一与「旧实现（oracle）」在
随机 / 含 NaN / 常数 / 单调 / 头部缺口 / 短序列等场景上逐位对照，
保证零语义漂移。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.feature_ops import C8Ops, C9Ops, RollingOps, TechnicalOps, TimeSeriesOps


# ─── oracle：改写前的 rolling.apply 版本 ─────────────────────


def _oracle_ts_product(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window).apply(np.prod, raw=True)


def _oracle_ts_zscore(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window).apply(
        lambda x: (x.iloc[-1] - x.mean()) / x.std() if len(x) > 1 and x.std() > 0 else 0
    )


def _oracle_ts_min_max_diff(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window).apply(lambda x: x.max() - x.min())


def _oracle_ts_cum_max(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window).apply(lambda x: x.cummax().iloc[-1])


def _oracle_max_drawdown(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window=window).apply(lambda x: (x / x.cummax() - 1).min())


def _oracle_ts_argmin(s: pd.Series, window: int) -> pd.Series:
    return s.rolling(window, min_periods=2).apply(np.argmin, raw=True)


def _oracle_self_corr(s: pd.Series, window: int) -> pd.Series:
    def _lag1(arr: np.ndarray) -> float:
        if len(arr) < 3:
            return 0.0
        x0, x1 = arr[:-1], arr[1:]
        s0, s1 = float(x0.std()), float(x1.std())
        if s0 == 0 or s1 == 0:
            return 0.0
        return float(np.corrcoef(x0, x1)[0, 1])

    return s.rolling(window, min_periods=3).apply(_lag1, raw=True).fillna(0.0)


# ─── 测试场景构造 ───────────────────────────────────────────


def _seq(rng: np.random.Generator, kind: str, n: int, nan_frac: float) -> np.ndarray:
    """构造测试序列（kind 决定形态，nan_frac 注入随机 NaN）。"""
    if kind == "random":
        arr = rng.standard_normal(n)
    elif kind == "random_nan":
        arr = rng.standard_normal(n)
        arr[rng.random(n) < nan_frac] = np.nan
    elif kind == "head_nan":
        arr = rng.standard_normal(n)
        arr[:5] = np.nan
    elif kind == "constant":
        arr = np.full(n, 3.0)
    elif kind == "mono_up":
        arr = np.arange(n, dtype=float)
    elif kind == "mono_down":
        arr = np.arange(n, 0, -1, dtype=float)
    elif kind == "short":
        arr = rng.standard_normal(n)
    else:
        raise ValueError(kind)
    return arr


# 场景矩阵：(kind, n, nan_frac)
_CASES = [
    ("random", 80, 0.0),
    ("random_nan", 80, 0.05),
    ("head_nan", 80, 0.0),
    ("constant", 80, 0.0),
    ("mono_up", 80, 0.0),
    ("mono_down", 80, 0.0),
    ("short", 8, 0.0),  # n < window，覆盖 n<window 分支
]

_WINDOWS = [5, 20, 60]


def _assert_equiv(a: pd.Series, b: pd.Series, atol: float = 1e-9, rtol: float = 1e-9) -> None:
    """逐位对照：NaN 位置必须一致；有限值在容差内一致。"""
    a_arr = a.to_numpy(dtype=float)
    b_arr = b.to_numpy(dtype=float)
    assert a_arr.shape == b_arr.shape, f"长度不一致: {a_arr.shape} vs {b_arr.shape}"
    nan_a, nan_b = np.isnan(a_arr), np.isnan(b_arr)
    assert (nan_a == nan_b).all(), f"NaN 位置不一致: {(nan_a != nan_b).sum()} 处"
    finite = ~nan_a
    np.testing.assert_allclose(a_arr[finite], b_arr[finite], atol=atol, rtol=rtol)


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(0)


def _run_cases(new_fn, oracle_fn, rng, window):
    """对全场景矩阵跑新旧实现并对照（逐个 case 独立断言）。"""
    for kind, n, nan_frac in _CASES:
        arr = _seq(rng, kind, n, nan_frac)
        s = pd.Series(arr, dtype=float)
        _assert_equiv(new_fn(s, window=window), oracle_fn(s, window=window))


# ─── 逐算子对照测试 ─────────────────────────────────────────


@pytest.mark.parametrize("window", _WINDOWS)
def test_ts_product(window, rng):
    _run_cases(TimeSeriesOps.ts_product, _oracle_ts_product, rng, window)


@pytest.mark.parametrize("window", _WINDOWS)
def test_ts_zscore(window, rng):
    _run_cases(RollingOps.ts_zscore, _oracle_ts_zscore, rng, window)


@pytest.mark.parametrize("window", _WINDOWS)
def test_ts_min_max_diff(window, rng):
    _run_cases(RollingOps.ts_min_max_diff, _oracle_ts_min_max_diff, rng, window)


@pytest.mark.parametrize("window", _WINDOWS)
def test_ts_cum_max(window, rng):
    _run_cases(RollingOps.ts_cum_max, _oracle_ts_cum_max, rng, window)


@pytest.mark.parametrize("window", [20, 60, 252])
def test_max_drawdown(window, rng):
    _run_cases(TechnicalOps.max_drawdown, _oracle_max_drawdown, rng, window)


@pytest.mark.parametrize("window", _WINDOWS)
def test_ts_argmin(window, rng):
    _run_cases(C8Ops.ts_argmin, _oracle_ts_argmin, rng, window)


@pytest.mark.parametrize("window", [5, 20, 60])
def test_self_corr(window, rng):
    # self_corr 由 corrcoef 浮点路径改写，容差放宽到 1e-7（仍远小于任何语义漂移）
    for kind, n, nan_frac in _CASES:
        arr = _seq(rng, kind, n, nan_frac)
        s = pd.Series(arr, dtype=float)
        _assert_equiv(C9Ops.self_corr(s, window=window), _oracle_self_corr(s, window=window), atol=1e-7, rtol=1e-7)


# ─── registry 批 2（plans/37 Step 2 批 2）：ts_argmax / ts_decay_linear ──


def _oracle_reg_ts_argmax(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).apply(np.argmax, raw=True)


def _oracle_reg_ts_decay_linear(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n).apply(
        lambda w: float(np.dot(w, np.arange(1, n + 1)) / (n * (n + 1) / 2.0)), raw=True
    )


@pytest.mark.parametrize("window", [5, 20, 60])
def test_reg_ts_argmax(window, rng):
    from fts.factor_engine.expr_dsl.registry import build_registry

    fn = build_registry()["ts_argmax"].func
    for kind, n, nan_frac in _CASES:
        arr = _seq(rng, kind, n, nan_frac)
        s = pd.Series(arr, dtype=float)
        _assert_equiv(fn(s, window), _oracle_reg_ts_argmax(s, window))


@pytest.mark.parametrize("window", [5, 20, 60])
def test_reg_ts_decay_linear(window, rng):
    from fts.factor_engine.expr_dsl.registry import build_registry

    fn = build_registry()["ts_decay_linear"].func
    for kind, n, nan_frac in _CASES:
        arr = _seq(rng, kind, n, nan_frac)
        s = pd.Series(arr, dtype=float)
        _assert_equiv(fn(s, window), _oracle_reg_ts_decay_linear(s, window))


@pytest.mark.parametrize(
    "op_name,oracle_fn",
    [
        ("ts_argmax", _oracle_reg_ts_argmax),
        ("ts_decay_linear", _oracle_reg_ts_decay_linear),
    ],
    ids=["ts_argmax", "ts_decay_linear"],
)
def test_reg_ops_panel_2d(op_name, oracle_fn, rng):
    """registry 算子 DataFrame（面板路径）逐列与 oracle 一致。"""
    from fts.factor_engine.expr_dsl.registry import build_registry

    fn = build_registry()[op_name].func
    df = pd.DataFrame(
        {c: pd.Series(_seq(rng, "random", 80, 0.05)) for c in ("A", "B", "C")},
        dtype=float,
    )
    got = fn(df, 20)
    for col in df.columns:
        _assert_equiv(got[col], oracle_fn(df[col], 20))


# ─── 边界：窗口大于序列 / 空序列 / 单元素 ────────────────────


@pytest.mark.parametrize(
    "new_fn,oracle_fn",
    [
        (TimeSeriesOps.ts_product, _oracle_ts_product),
        (RollingOps.ts_zscore, _oracle_ts_zscore),
        (RollingOps.ts_min_max_diff, _oracle_ts_min_max_diff),
        (RollingOps.ts_cum_max, _oracle_ts_cum_max),
        (TechnicalOps.max_drawdown, _oracle_max_drawdown),
        (C8Ops.ts_argmin, _oracle_ts_argmin),
        (C9Ops.self_corr, _oracle_self_corr),
    ],
    ids=["ts_product", "ts_zscore", "ts_min_max_diff", "ts_cum_max", "max_drawdown", "ts_argmin", "self_corr"],
)
def test_empty_and_single(new_fn, oracle_fn):
    """空序列与单元素序列：新旧实现一致（全部 NaN / 默认值）。"""
    for arr in ([], [5.0]):
        s = pd.Series(arr, dtype=float)
        _assert_equiv(new_fn(s, window=20), oracle_fn(s, window=20))
