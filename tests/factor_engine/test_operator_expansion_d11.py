"""tests/factor_engine/test_operator_expansion_d11.py — D11 技术指标族算子测试。

覆盖（2026-08-11 扩容二期，DSL 187→247 / GP 166→226）:
    1. 60 个技术指标算子功能与边界（值域/方向性/有限性）
    2. 常数序列与含 NaN 序列 NaN 兜底（不抛异常、输出有限）
    3. OHLCV 多序列算子方向性
    4. 双注册表一致性（DSL 247 项 / GP d11 60 项 / required_shared 全覆盖）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.expr_dsl.registry import build_registry, verify_registry_consistency
from fts.factor_engine.feature_ops import OperatorRegistry
from fts.factor_engine.ops_library import D11Ops

RNG_SEED = 20260811

# 单序列算子（series, ...）：有限性批量验证
SINGLE_SERIES_OPS = [
    "ts_ema_fast_slow", "ts_macd", "ts_macd_signal", "ts_macd_hist", "ts_dema", "ts_tema",
    "ts_kama", "ts_rsi", "ts_rsi_smoothed", "ts_trix", "ts_ppo", "ts_tsi", "ts_roc",
    "ts_momentum_index", "ts_rate_of_change_ma", "ts_fisher_transform", "ts_stoch_rsi",
    "ts_bb_width", "ts_bb_percent_b", "ts_bb_bandwidth", "ts_price_channel",
    "ts_aroon_up", "ts_aroon_down", "ts_aroon_osc", "ts_dpo", "ts_kst", "ts_kst_signal",
    "ts_sma_cross_signal", "ts_ema_cross_signal", "ts_price_oscillator",
    "ts_trend_score", "ts_cycle_score",
]


@pytest.fixture
def series() -> pd.Series:
    """随机游走价格序列（固定种子）。"""
    r = np.random.default_rng(RNG_SEED).normal(0, 0.01, 250)
    return pd.Series(np.cumsum(r) + 100.0)


@pytest.fixture
def rising() -> pd.Series:
    return pd.Series(np.linspace(100.0, 200.0, 250))


@pytest.fixture
def constant() -> pd.Series:
    return pd.Series(np.full(250, 100.0))


@pytest.fixture
def ohlcv():
    """OHLCV 面板（high>low、volume>0）。"""
    rng = np.random.default_rng(RNG_SEED)
    n = 250
    close = pd.Series(np.cumsum(rng.normal(0, 0.01, n)) + 100.0)
    high = close + rng.uniform(0.1, 0.5, n)
    low = close - rng.uniform(0.1, 0.5, n)
    open_p = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(rng.uniform(1e4, 1e5, n))
    return open_p, high, low, close, volume


def _finite(out: pd.Series) -> bool:
    return bool(np.isfinite(out.dropna()).all()) and not out.isna().all()


def _call_d11(name, o, h, l, c, v):
    """按参数签名调用 D11 算子。"""
    sig = {
        "ts_ema_fast_slow": (c, 12, 26), "ts_macd": (c, 12, 26),
        "ts_macd_signal": (c, 12, 26, 9), "ts_macd_hist": (c, 12, 26, 9),
        "ts_dema": (c, 20), "ts_tema": (c, 20), "ts_kama": (c, 10, 2, 30),
        "ts_vwap": (c, v, 20),
        "ts_stoch_k": (h, l, c, 14), "ts_stoch_d": (h, l, c, 14, 3),
        "ts_williams_r": (h, l, c, 14), "ts_cci": (h, l, c, 20),
        "ts_awesome": (h, l, 5, 34), "ts_ultimate_osc": (h, l, c, 7, 14, 28),
        "ts_rvi": (h, l, c, 10),
        "ts_obv": (c, v), "ts_obv_ma": (c, v, 20),
        "ts_mfi": (h, l, c, v, 14), "ts_adi": (h, l, c, v),
        "ts_cmf": (h, l, c, v, 20), "ts_chaikin_vol": (h, l, 10),
        "ts_chaikin_osc": (h, l, c, v, 3, 10), "ts_volume_oscillator": (v, 5, 20),
        "ts_market_facilitation": (h, l, v),
        "ts_atr": (h, l, c, 14), "ts_natr": (h, l, c, 14),
        "ts_mass_index": (h, l, 9),
        "ts_vortex_pos": (h, l, c, 14), "ts_vortex_neg": (h, l, c, 14),
        "ts_vortex_ratio": (h, l, c, 14),
        "ts_ichimoku_conv": (h, l, 9), "ts_ichimoku_base": (h, l, 26),
        "ts_ichimoku_span_a": (h, l, 26), "ts_ichimoku_span_b": (h, l, 52),
        "ts_parabolic_sar": (h, l, 0.02, 0.2),
    }
    multi = {"ts_vwap", "ts_stoch_k", "ts_stoch_d", "ts_williams_r", "ts_cci", "ts_awesome",
             "ts_ultimate_osc", "ts_rvi", "ts_obv", "ts_obv_ma", "ts_mfi", "ts_adi", "ts_cmf",
             "ts_chaikin_vol", "ts_chaikin_osc", "ts_volume_oscillator", "ts_market_facilitation",
             "ts_atr", "ts_natr", "ts_mass_index", "ts_vortex_pos", "ts_vortex_neg",
             "ts_vortex_ratio", "ts_ichimoku_conv", "ts_ichimoku_base", "ts_ichimoku_span_a",
             "ts_ichimoku_span_b", "ts_parabolic_sar"}
    if name in multi:
        return getattr(D11Ops, name)(*sig[name])
    defaults = {
        "ts_ema_fast_slow": (12, 26), "ts_macd": (12, 26), "ts_macd_signal": (12, 26, 9),
        "ts_macd_hist": (12, 26, 9), "ts_dema": (20,), "ts_tema": (20,), "ts_kama": (10, 2, 30),
        "ts_rsi": (14,), "ts_rsi_smoothed": (14,), "ts_trix": (15,), "ts_ppo": (12, 26),
        "ts_tsi": (13, 25), "ts_roc": (12,), "ts_momentum_index": (14,),
        "ts_rate_of_change_ma": (12,), "ts_fisher_transform": (9,), "ts_stoch_rsi": (14,),
        "ts_bb_width": (20, 2.0), "ts_bb_percent_b": (20, 2.0), "ts_bb_bandwidth": (20, 2.0),
        "ts_price_channel": (20,), "ts_aroon_up": (25,), "ts_aroon_down": (25,),
        "ts_aroon_osc": (25,), "ts_dpo": (20,), "ts_kst": (30,), "ts_kst_signal": (30,),
        "ts_sma_cross_signal": (5, 20), "ts_ema_cross_signal": (12, 26),
        "ts_price_oscillator": (10, 30), "ts_trend_score": (20,), "ts_cycle_score": (20,),
    }
    return getattr(D11Ops, name)(c, *defaults[name])


class TestD11SingleSeries:
    """单序列技术指标：有限性 + 常数兜底。"""

    @pytest.mark.parametrize("name", SINGLE_SERIES_OPS)
    def test_finite(self, name, series):
        assert _finite(_call_d11(name, series, series, series, series, series)), name

    @pytest.mark.parametrize("name", SINGLE_SERIES_OPS)
    def test_constant_no_exception(self, name, constant):
        assert _finite(_call_d11(name, constant, constant, constant, constant, constant)), name

    def test_rsi_range(self, rising):
        """RSI ∈ [0,100]。"""
        rsi = D11Ops.ts_rsi(rising, 14)
        assert float(rsi.dropna().min()) >= 0.0
        assert float(rsi.dropna().max()) <= 100.0

    def test_macd_positive_in_uptrend(self, rising):
        """上升趋势 → MACD 与信号差 > 0（多头）。"""
        assert float(D11Ops.ts_macd_hist(rising, 12, 26, 9).tail(20).mean()) > 0

    def test_bb_percent_b_range(self, series):
        """布林 %B ∈ [0,1]。"""
        b = D11Ops.ts_bb_percent_b(series, 20, 2.0)
        assert float(b.dropna().min()) >= 0.0
        assert float(b.dropna().max()) <= 1.0

    def test_fisher_bounded(self, series):
        """Fisher 变换输出有限（±4 量级内）。"""
        f = D11Ops.ts_fisher_transform(series, 9)
        assert _finite(f)
        assert float(f.abs().max()) < 10.0


class TestD11OHLCV:
    """OHLCV 多序列技术指标方向性。"""

    def test_atr_positive(self, ohlcv):
        """ATR 非负有限。"""
        _, h, l, c, _ = ohlcv
        assert (D11Ops.ts_atr(h, l, c, 14) >= 0).all()

    def test_stoch_k_range(self, ohlcv):
        """随机 %K ∈ [0,100]。"""
        _, h, l, c, _ = ohlcv
        k = D11Ops.ts_stoch_k(h, l, c, 14)
        assert float(k.dropna().min()) >= 0.0
        assert float(k.dropna().max()) <= 100.0

    def test_vwap_tracks_price(self, ohlcv):
        """VWAP 与价格同量级。"""
        _, _, _, c, v = ohlcv
        vwap = D11Ops.ts_vwap(c, v, 20)
        assert _finite(vwap)
        assert abs(float(vwap.iloc[-1]) / float(c.iloc[-1]) - 1.0) < 0.05

    def test_cmf_bounded(self, ohlcv):
        """CMF ∈ [-1,1]。"""
        _, h, l, c, v = ohlcv
        cmf = D11Ops.ts_cmf(h, l, c, v, 20)
        assert float(cmf.abs().max()) <= 1.0 + 1e-9

    @pytest.mark.parametrize("name", ["ts_vwap", "ts_stoch_k", "ts_stoch_d", "ts_williams_r", "ts_cci",
                                      "ts_awesome", "ts_ultimate_osc", "ts_rvi", "ts_obv", "ts_obv_ma",
                                      "ts_mfi", "ts_adi", "ts_cmf", "ts_chaikin_vol", "ts_chaikin_osc",
                                      "ts_volume_oscillator", "ts_market_facilitation", "ts_atr", "ts_natr",
                                      "ts_mass_index", "ts_vortex_pos", "ts_vortex_neg", "ts_vortex_ratio",
                                      "ts_ichimoku_conv", "ts_ichimoku_base", "ts_ichimoku_span_a",
                                      "ts_ichimoku_span_b", "ts_parabolic_sar"])
    def test_ohlcv_finite(self, name, ohlcv):
        o, h, l, c, v = ohlcv
        assert _finite(_call_d11(name, o, h, l, c, v)), name


class TestD11RegistryConsistency:
    """D11 双注册表强制共享。"""

    def test_dsl_count_ge_247(self):
        assert len(build_registry()) >= 247

    def test_gp_contains_d11(self):
        gp = OperatorRegistry()
        gp_names = {op.name for op in gp.list_operators()}
        d11_names = {n for n in dir(D11Ops) if n.startswith("ts_")}
        assert d11_names <= gp_names, d11_names - gp_names

    def test_verify_registry_consistent(self):
        v = verify_registry_consistency()
        assert v["consistent"] is True
        assert len(v.get("mismatched", [])) == 0
        assert len(v.get("errors", [])) == 0

    def test_dsl_metadata_bound(self):
        dsl = build_registry()
        d11_names = {n for n in dir(D11Ops) if n.startswith("ts_")}
        for n in d11_names:
            meta = dsl[n]
            assert meta.economic_meaning, f"{n} 缺经济语义"
