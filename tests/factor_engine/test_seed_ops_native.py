"""tests/factor_engine/test_seed_ops_native.py — seed 模板算子向量化改写对照测试。

37 号计划 Phase 2 Step 2 批 2：seed_loader._EXPRESSION_OPS_SOURCE 与
seed_data.loader._ALPHA_OPS_SOURCE 中的 Alpha101 语义算子
（ts_argmax/ts_argmin/ts_rank/ts_product/decay_linear/highday/lowday）由
rolling.apply（Python 回调）改写为 sliding_window_view 自包含向量化，
逐算子与旧实现（oracle）在随机 / 含 NaN / 常数 / 单调 / 头部缺口 / 短序列
场景上逐位对照，保证零语义漂移。
"""

from __future__ import annotations

import textwrap

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine import seed_loader
from fts.factor_engine.seed_data import loader as seed_data_loader


# ─── oracle：改写前的 rolling.apply 实现 ─────────────────────


def _to_array(x):
    return x.values if isinstance(x, pd.Series) else np.asarray(x)


def _oracle_ts_argmax(x, d):
    def _f(v):
        return np.argmax(v) if len(v) > 0 else 0

    return _to_array(pd.Series(x).rolling(d, min_periods=1).apply(_f, raw=True))


def _oracle_ts_argmin(x, d):
    def _f(v):
        return np.argmin(v) if len(v) > 0 else 0

    return _to_array(pd.Series(x).rolling(d, min_periods=1).apply(_f, raw=True))


def _oracle_ts_rank(x, d):
    def _f(v):
        if len(v) <= 1:
            return 0.5
        return np.argsort(np.argsort(v))[-1] / (len(v) - 1)

    return _to_array(pd.Series(x).rolling(d, min_periods=1).apply(_f, raw=True))


def _oracle_ts_product(x, d):
    return _to_array(pd.Series(x).rolling(d, min_periods=1).apply(np.prod, raw=True))


def _oracle_decay_linear(x, d):
    w = np.arange(1, d + 1, dtype=float)
    w = w / w.sum()

    def _f(v):
        if len(v) < d:
            return np.nan
        return np.sum(v[-d:] * w)

    return _to_array(pd.Series(x).rolling(d, min_periods=d).apply(_f, raw=True))


def _oracle_highday(x, d):
    def _f(v):
        if len(v) <= 1:
            return 0.0
        return float(len(v) - 1 - np.argmax(v))

    return _to_array(pd.Series(x).rolling(d, min_periods=1).apply(_f, raw=True))


def _oracle_lowday(x, d):
    def _f(v):
        if len(v) <= 1:
            return 0.0
        return float(len(v) - 1 - np.argmin(v))

    return _to_array(pd.Series(x).rolling(d, min_periods=1).apply(_f, raw=True))


_OPS = [
    ("ts_argmax", _oracle_ts_argmax),
    ("ts_argmin", _oracle_ts_argmin),
    ("ts_rank", _oracle_ts_rank),
    ("ts_product", _oracle_ts_product),
    ("decay_linear", _oracle_decay_linear),
    ("highday", _oracle_highday),
    ("lowday", _oracle_lowday),
]

_OPS_SOURCES = [
    pytest.param(seed_loader._EXPRESSION_OPS_SOURCE, id="seed_loader"),
    pytest.param(seed_data_loader._ALPHA_OPS_SOURCE, id="seed_data_loader"),
]


# ─── 测试场景构造 ───────────────────────────────────────────


def _seq(rng: np.random.Generator, kind: str, n: int, nan_frac: float) -> np.ndarray:
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


_CASES = [
    ("random", 80, 0.0),
    ("random_nan", 80, 0.05),
    ("head_nan", 80, 0.0),
    ("constant", 80, 0.0),
    ("mono_up", 80, 0.0),
    ("mono_down", 80, 0.0),
    ("short", 8, 0.0),
]

_WINDOWS = [5, 20, 60]


def _load_ops(source: str) -> dict:
    ns: dict = {}
    exec(textwrap.dedent(source), ns)  # noqa: S102 — 仅 exec 项目内模板源码（与因子沙箱同源）
    return ns


def _assert_equiv(a, b, atol: float = 1e-9, rtol: float = 1e-9) -> None:
    a_arr = np.asarray(a, dtype=float)
    b_arr = np.asarray(b, dtype=float)
    assert a_arr.shape == b_arr.shape, f"长度不一致: {a_arr.shape} vs {b_arr.shape}"
    nan_a, nan_b = np.isnan(a_arr), np.isnan(b_arr)
    assert (nan_a == nan_b).all(), f"NaN 位置不一致: {(nan_a != nan_b).sum()} 处"
    finite = ~nan_a
    np.testing.assert_allclose(a_arr[finite], b_arr[finite], atol=atol, rtol=rtol)


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(0)


# ─── 逐算子对照测试 ─────────────────────────────────────────


@pytest.mark.parametrize("ops_source", _OPS_SOURCES)
@pytest.mark.parametrize("op_name,oracle_fn", _OPS, ids=[o[0] for o in _OPS])
@pytest.mark.parametrize("window", _WINDOWS)
def test_seed_op_matches_oracle(ops_source, op_name, oracle_fn, window, rng):
    ns = _load_ops(ops_source)
    new_fn = ns[op_name]
    for kind, n, nan_frac in _CASES:
        arr = _seq(rng, kind, n, nan_frac)
        _assert_equiv(new_fn(arr, window), oracle_fn(arr, window))


@pytest.mark.parametrize("ops_source", _OPS_SOURCES)
@pytest.mark.parametrize("op_name,oracle_fn", _OPS, ids=[o[0] for o in _OPS])
def test_seed_op_edge_empty_single(ops_source, op_name, oracle_fn):
    """空序列与单元素序列：新旧实现一致。"""
    ns = _load_ops(ops_source)
    new_fn = ns[op_name]
    for arr in ([], [5.0]):
        _assert_equiv(new_fn(arr, 20), oracle_fn(arr, 20))
