"""tests/factor_engine/test_operator_expansion_d13_d17.py — D13~D17 算子族综合测试。

覆盖（2026-08-11 扩容二期，DSL 437→512 / GP 416→491）:
    D13 截面/排名 45 / D14 条件/事件 40 / D15 组合/跨序列 50 /
    D16 量价/流动性 40 / D17 市场结构/分布 35
    1. 全部算子：随机序列有限 + 常数序列不抛异常（NaN 兜底）
    2. 关键方向性（每族代表算子）
    3. 双注册表一致性（DSL 512 项 / required_shared 全覆盖 / verify consistent）
"""

# ruff: noqa: E741  # OHLC 低价用 l 命名（o/h/l/c），属领域标准命名

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.expr_dsl.registry import build_registry, verify_registry_consistency
from fts.factor_engine.feature_ops import OperatorRegistry
from fts.factor_engine.ops_library import D13Ops, D14Ops, D15Ops, D16Ops, D17Ops

RNG_SEED = 20260811

FAMILIES = {
    "D13Ops": D13Ops,
    "D14Ops": D14Ops,
    "D15Ops": D15Ops,
    "D16Ops": D16Ops,
    "D17Ops": D17Ops,
}

ALL_NAMES = [n for f in FAMILIES.values() for n in dir(f) if n.startswith("cs_") or n.startswith("ts_")]


def _make_args(fn, c, h, l, v, fs):
    """按函数签名自动构造位置参数（覆盖 D13~D17 全部签名）。"""
    out = []
    for p in inspect.signature(fn).parameters.values():
        n = p.name
        has_default = p.default is not inspect.Parameter.empty
        if n in ("series", "x", "close", "price", "spot", "near", "open_p", "open"):
            out.append(c)
        elif n == "volume":
            out.append(v)
        elif n == "y":
            out.append(c)
        elif n in ("future", "far"):
            out.append(c)
        elif n == "high":
            out.append(h)
        elif n == "low":
            out.append(l)
        elif n == "amount":
            out.append(c * v)
        elif n == "float_shares":
            out.append(fs)
        elif n in ("window", "span"):
            out.append(20)
        elif n == "short":
            out.append(5)
        elif n == "long":
            out.append(20)
        elif n == "mid":
            out.append(14)
        elif n == "signal":
            out.append(9)
        elif n == "smooth":
            out.append(3)
        elif n in ("threshold", "lo", "hi", "k", "w", "step", "max_step", "mult",
                   "n_buckets", "q", "fast", "slow"):
            out.append(p.default if has_default else 1.0)
        else:
            out.append(p.default if has_default else 20)
    return tuple(out)


@pytest.fixture
def series() -> pd.Series:
    r = np.random.default_rng(RNG_SEED).normal(0, 0.01, 200)
    return pd.Series(np.cumsum(r) + 100.0)


@pytest.fixture
def rising() -> pd.Series:
    return pd.Series(np.linspace(100.0, 200.0, 200))


@pytest.fixture
def falling() -> pd.Series:
    return pd.Series(np.linspace(200.0, 100.0, 200))


@pytest.fixture
def constant() -> pd.Series:
    return pd.Series(np.full(200, 100.0))


def _finite(out) -> bool:
    if isinstance(out, pd.Series):
        return bool(np.isfinite(out.dropna()).all()) and not out.isna().all()
    return bool(np.isfinite(out))


def _run(name, c, h, l, v, fs):
    fn = getattr(next(f for f in FAMILIES.values() if hasattr(f, name)), name)
    return fn(*_make_args(fn, c, h, l, v, fs))


class TestAllFamiliesFinite:
    """D13~D17 全部算子：随机序列有限 + 常数序列不抛异常。"""

    @pytest.mark.parametrize("name", ALL_NAMES)
    def test_finite(self, name, series):
        h = series + 0.5
        l = series - 0.5
        v = pd.Series(np.abs(np.random.default_rng(1).normal(1e4, 1e3, 200)))
        fs = pd.Series(np.full(200, 1e8))
        assert _finite(_run(name, series, h, l, v, fs)), name

    @pytest.mark.parametrize("name", ALL_NAMES)
    def test_constant_no_exception(self, name, constant):
        h = constant + 0.5
        l = constant - 0.5
        v = pd.Series(np.full(200, 1e4))
        fs = pd.Series(np.full(200, 1e8))
        assert _finite(_run(name, constant, h, l, v, fs)), name


class TestDirectional:
    """方向性代表算子。"""

    def test_rank_pct_bounded(self, rising):
        pct = D13Ops.cs_rank_pct(rising, 20)
        assert float(pct.dropna().min()) >= 0.0 and float(pct.dropna().max()) <= 1.0

    def test_condition_ratio(self, rising):
        assert float(D14Ops.ts_condition_ratio(rising, 0.0, 20).iloc[-1]) > 0.9

    def test_cross_signal_binary(self, series):
        """金叉信号 ∈ {0,1}（事件算子输出有限）。"""
        out = D14Ops.ts_golden_cross_event(series, 5, 20)
        assert set(np.unique(out)).issubset({0.0, 1.0})

    def test_ratio_positive(self, series):
        r = D15Ops.cs_ratio(series + 1.0, series.abs() + 1.0)
        assert (r.dropna() >= 0).all()

    def test_spread_zscore_finite(self, series):
        assert _finite(D15Ops.ts_spread_zscore(series, series + 1.0, 20))

    def test_liquidity_zscore_finite(self, series):
        v = pd.Series(np.abs(np.random.default_rng(1).normal(1e4, 1e3, 200)))
        assert _finite(D16Ops.ts_liquidity_zscore(v, 20))

    def test_market_breadth_bounded(self, rising):
        b = D17Ops.ts_market_breadth(rising, 20)
        assert float(b.dropna().min()) >= 0.0 and float(b.dropna().max()) <= 1.0

    def test_fear_greed_bounded(self, series):
        fg = D17Ops.ts_fear_greed_index(series, 20)
        assert float(fg.dropna().min()) >= 0.0 and float(fg.dropna().max()) <= 1.0


class TestRegistryConsistency:
    """双注册表强制共享（DSL 512 / required_shared 全覆盖 / verify consistent）。"""

    def test_dsl_count_ge_512(self):
        assert len(build_registry()) >= 512

    def test_gp_contains_all_d_families(self):
        gp = OperatorRegistry()
        gp_names = {op.name for op in gp.list_operators()}
        missing = set(ALL_NAMES) - gp_names
        assert not missing, f"GP 缺 {len(missing)} 个: {sorted(missing)[:10]}"

    def test_verify_consistent(self):
        v = verify_registry_consistency()
        assert v["consistent"] is True
        assert len(v.get("mismatched", [])) == 0
        assert len(v.get("errors", [])) == 0

    def test_metadata_bound(self):
        dsl = build_registry()
        for n in ALL_NAMES:
            meta = dsl[n]
            assert meta.economic_meaning, f"{n} 缺经济语义"
