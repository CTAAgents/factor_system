"""tests/factor_engine/test_operator_expansion_d12.py — D12 动量/趋势族算子测试。

覆盖（2026-08-11 扩容二期，DSL 247→302 / GP 226→281）:
    1. 55 个动量/趋势算子有限性与常数兜底
    2. 上升/下降序列方向性（趋势方向/动量符号）
    3. 双注册表一致性（DSL 302 项 / GP d12 55 项 / required_shared 全覆盖）
"""

# ruff: noqa: E741  # OHLC 低价用 l 命名（o/h/l/c），属领域标准命名

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.expr_dsl.registry import build_registry, verify_registry_consistency
from fts.factor_engine.feature_ops import OperatorRegistry
from fts.factor_engine.ops_library import D12Ops

RNG_SEED = 20260811


@pytest.fixture
def rising() -> pd.Series:
    return pd.Series(np.linspace(100.0, 200.0, 200))


@pytest.fixture
def falling() -> pd.Series:
    return pd.Series(np.linspace(200.0, 100.0, 200))


@pytest.fixture
def series() -> pd.Series:
    r = np.random.default_rng(RNG_SEED).normal(0, 0.01, 200)
    return pd.Series(np.cumsum(r) + 100.0)


@pytest.fixture
def constant() -> pd.Series:
    return pd.Series(np.full(200, 100.0))


D12_NAMES = [n for n in dir(D12Ops) if n.startswith("ts_")]


def _finite(out: pd.Series) -> bool:
    return bool(np.isfinite(out.dropna()).all()) and not out.isna().all()


def _call(name, c, h, l):
    """按签名调用 D12 算子。"""
    if name in ("ts_donchian_break", "ts_donchian_mid", "ts_directional_up", "ts_directional_down"):
        return getattr(D12Ops, name)(h, l, 20)
    if name in ("ts_adx_pos", "ts_adx_neg", "ts_adx", "ts_adx_wilder", "ts_atr_ratio"):
        return getattr(D12Ops, name)(h, l, c, 14)
    if name == "ts_supertrend_signal":
        return getattr(D12Ops, name)(c, h, l, 10, 3.0)
    if name == "ts_psar_position":
        return getattr(D12Ops, name)(h, l, 0.02, 0.2)
    if name == "ts_fractal_up":
        return getattr(D12Ops, name)(h, 5)
    if name == "ts_fractal_down":
        return getattr(D12Ops, name)(l, 5)
    if name in ("ts_trend_strength_ma", "ts_cross_momentum"):
        return getattr(D12Ops, name)(c, 5, 20)
    if name == "ts_multi_tf_trend":
        return getattr(D12Ops, name)(c, 10, 30, 60)
    if name == "ts_velocity":
        return getattr(D12Ops, name)(c)
    if name in ("ts_acceleration", "ts_jerk", "ts_slope_change", "ts_curvature"):
        return getattr(D12Ops, name)(c, 5)
    return getattr(D12Ops, name)(c, 20)


class TestD12Finite:
    """55 个算子：随机序列有限 + 常数序列不抛异常。"""

    @pytest.mark.parametrize("name", D12_NAMES)
    def test_finite(self, name, series):
        h = series + 0.5
        l = series - 0.5
        assert _finite(_call(name, series, h, l)), name

    @pytest.mark.parametrize("name", D12_NAMES)
    def test_constant_no_exception(self, name, constant):
        if name == "ts_adx_wilder":
            # 常数序列下与 RD _adx_series 精确一致：DI 分母为 0 → dx 全 NaN
            # （plans/57 §4.3 映射保真，禁止 fillna 造成真实数据漂移）
            pytest.skip("ts_adx_wilder 常数序列全 NaN（对齐 RD 精确口径）")
        h = constant + 0.5
        l = constant - 0.5
        assert _finite(_call(name, constant, h, l)), name


class TestD12Direction:
    """方向性：上升序列趋势信号为正，下降序列为负。"""

    def test_uptrend_flag(self, rising):
        assert float(D12Ops.ts_uptrend_flag(rising, 20).tail(30).mean()) > 0.9

    def test_downtrend_flag(self, falling):
        assert float(D12Ops.ts_downtrend_flag(falling, 20).tail(30).mean()) > 0.9

    def test_momentum_ratio(self, rising, falling):
        assert float(D12Ops.ts_momentum_ratio(rising, 20).iloc[-1]) > 1.0
        assert float(D12Ops.ts_momentum_ratio(falling, 20).iloc[-1]) < 1.0

    def test_up_down_strength(self, rising, falling):
        assert float(D12Ops.ts_up_down_strength(rising, 20).iloc[-1]) > 0.0
        assert float(D12Ops.ts_up_down_strength(falling, 20).iloc[-1]) < 0.0

    def test_trend_strength_pct(self, rising, falling):
        assert float(D12Ops.ts_trend_strength_pct(rising, 20).iloc[-1]) > 0.5
        assert float(D12Ops.ts_trend_strength_pct(falling, 20).iloc[-1]) < -0.5

    def test_above_ma_ratio(self, rising, falling):
        assert float(D12Ops.ts_above_ma_ratio(rising, 20).iloc[-1]) > 0.9
        assert float(D12Ops.ts_below_ma_ratio(falling, 20).iloc[-1]) > 0.9

    def test_adx_high_in_trend(self, rising):
        """趋势序列 ADX 高（>30）。"""
        h = rising + 0.5
        l = rising - 0.5
        assert float(D12Ops.ts_adx(h, l, rising, 14).iloc[-1]) > 30.0


class TestD12Registry:
    """D12 双注册表强制共享。"""

    def test_dsl_count_ge_302(self):
        assert len(build_registry()) >= 302

    def test_gp_contains_d12(self):
        gp = OperatorRegistry()
        gp_names = {op.name for op in gp.list_operators()}
        assert set(D12_NAMES) <= gp_names

    def test_verify_consistent(self):
        v = verify_registry_consistency()
        assert v["consistent"] is True
        assert len(v.get("mismatched", [])) == 0
        assert len(v.get("errors", [])) == 0

    def test_metadata_bound(self):
        dsl = build_registry()
        for n in D12_NAMES:
            meta = dsl[n]
            assert meta.economic_meaning, f"{n} 缺经济语义"
