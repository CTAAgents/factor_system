"""
策略进化测试 — 市场制度自适应 / 动态因子权重 / 多周期信号融合。

覆盖：
  - RegimeAdaptiveStrategy: detect_regime, 权重切换, compute/score
  - DynamicWeightStrategy: 因子历史记录, 动态权重计算, compute/score
  - MultiPeriodSignalFusion: 多周期因子计算, 方向一致性, compute/score

版本: v0.1.0
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.strategies.base_v2 import RawSignal, ScoredSignal
from fts.strategies.strategy_evolution import (
    REGIME_WEIGHT_MAP,
    BULL_WEIGHTS,
    BEAR_WEIGHTS,
    HIGH_VOL_WEIGHTS,
    LOW_VOL_WEIGHTS,
    OSCILLATE_WEIGHTS,
    RegimeAdaptiveStrategy,
    DynamicWeightStrategy,
    MultiPeriodSignalFusion,
)


# ─── 辅助函数 ─────────────────────────────────────────────

def _tech(**overrides) -> dict:
    """便捷构建 tech dict。"""
    base = {
        "symbol": "RB", "price": 3500.0, "change_pct": 1.0,
        "ma_slope": 0.1, "macd_cross": "none", "atr": 50.0,
        "bb": 0.5, "bb_width": 0.05, "vol_ratio": 1.0,
    }
    base.update(overrides)
    return base


def _build_kline_series(n: int = 200, bull: bool = True) -> dict:
    """构造 K 线数据（用于制度检测）。"""
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    if bull:
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
    else:
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 - 0.5)
    bars = []
    for i, d in enumerate(dates):
        bars.append({
            "date": d,
            "open": float(close[i] * 0.998),
            "high": float(close[i] * 1.005),
            "low": float(close[i] * 0.995),
            "close": float(close[i]),
            "volume": float(np.random.randint(800, 1200)),
        })
    return {"RB": ("RB", bars)}


def _compute_ctx() -> dict:
    """提供足够的数据上下文以确保 compute 产出信号。"""
    return {
        "extra": {
            "oi_data": {"RB": {"oi_ratio": 0.1, "top5_ratio": 0.5}},
            "basis_data": {"RB": {"basis_pct": 3.0}},
            "warrant_data": {"RB": {"total": 1000.0, "daily_change": -50.0}},
            "inventory_data": {},
            "supply_data": {},
            "macro_data": {"available": False},
        },
        "macro_signal": "bull",
    }


# ════════════════════════════════════════════════════════════
# 权重配置常量
# ════════════════════════════════════════════════════════════

class TestRegimeWeights:
    """制度权重配置完整性检查。"""

    def test_all_regimes_defined(self):
        assert "bull" in REGIME_WEIGHT_MAP
        assert "bear" in REGIME_WEIGHT_MAP
        assert "high_vol" in REGIME_WEIGHT_MAP
        assert "low_vol" in REGIME_WEIGHT_MAP
        assert "oscillate" in REGIME_WEIGHT_MAP

    def test_weights_normalized(self):
        for regime, weights in REGIME_WEIGHT_MAP.items():
            total = sum(weights.values())
            assert total == pytest.approx(1.0, abs=0.01), f"{regime} 权重未归一化: {total}"

    def test_weights_have_all_keys(self):
        expected_keys = {
            "momentum", "volatility_reversion", "volume_flow", "oi_change",
            "basis", "inventory_pct", "capacity", "macro_regime",
            "rate_proxy", "pmi_proxy", "position_rank", "warrant_change",
        }
        for regime, weights in REGIME_WEIGHT_MAP.items():
            assert set(weights.keys()) == expected_keys, f"{regime} 缺少因子: {expected_keys - set(weights.keys())}"


# ════════════════════════════════════════════════════════════
# RegimeAdaptiveStrategy
# ════════════════════════════════════════════════════════════

class TestRegimeAdaptiveStrategyInit:
    def test_default_init(self):
        s = RegimeAdaptiveStrategy()
        assert s.name == "regime_adaptive"
        assert s._mode == "pure_momentum"
        assert s.current_regime is None
        assert s.regime_confidence == 0.0

    def test_custom_lookback(self):
        s = RegimeAdaptiveStrategy(lookback_days=30)
        assert s._regime_selector.lookback_days == 30

    def test_display_name_before_detect(self):
        s = RegimeAdaptiveStrategy()
        assert "unknown" in s.display_name

    def test_signal_type(self):
        s = RegimeAdaptiveStrategy("long_short")
        assert s.signal_type == "regime_adaptive.long_short"


class TestRegimeAdaptiveStrategyDetect:
    def test_detect_bull(self):
        s = RegimeAdaptiveStrategy()
        kline = _build_kline_series(n=200, bull=True)
        regime = s.detect_regime(kline)
        assert regime == "bull"
        assert s.current_regime == "bull"
        assert s.regime_confidence > 0

    def test_detect_bear(self):
        s = RegimeAdaptiveStrategy()
        kline = _build_kline_series(n=200, bull=False)
        regime = s.detect_regime(kline)
        assert regime == "bear"
        assert s.current_regime == "bear"

    def test_detect_empty_kline(self):
        s = RegimeAdaptiveStrategy()
        regime = s.detect_regime({})
        assert regime == "oscillate"
        assert s.current_regime == "oscillate"

    def test_detect_oscillate_fallback(self):
        s = RegimeAdaptiveStrategy()
        # 空 bars
        kline = {"RB": ("RB", [])}
        regime = s.detect_regime(kline)
        assert regime == "oscillate"

    def test_build_ohlcv_none_kline(self):
        s = RegimeAdaptiveStrategy()
        df = s._build_ohlcv_from_kline(None)
        assert df is None

    def test_build_ohlcv_empty_kline(self):
        s = RegimeAdaptiveStrategy()
        df = s._build_ohlcv_from_kline({})
        assert df is None


class TestRegimeAdaptiveStrategyCompute:
    def test_compute_bull_regime(self):
        """bull 制度下使用 BULL_WEIGHTS。"""
        s = RegimeAdaptiveStrategy()
        kline = _build_kline_series(n=200, bull=True)
        tech_list = [_tech()]
        signals = s.compute(tech_list, kline, _compute_ctx())
        assert s._weights == BULL_WEIGHTS
        if signals:
            assert signals[0].meta["regime"] == "bull"
            assert signals[0].meta["regime_confidence"] > 0

    def test_compute_bear_regime(self):
        """bear 制度下使用 BEAR_WEIGHTS。"""
        s = RegimeAdaptiveStrategy()
        kline = _build_kline_series(n=200, bull=False)
        tech_list = [_tech(change_pct=-2.0, ma_slope=-0.1)]
        signals = s.compute(tech_list, kline, _compute_ctx())
        assert s._weights == BEAR_WEIGHTS
        if signals:
            assert signals[0].meta["regime"] == "bear"

    def test_compute_empty_kline(self):
        """空 kline 时使用默认权重。"""
        s = RegimeAdaptiveStrategy()
        signals = s.compute([], {}, _compute_ctx())
        assert signals == []
        assert s.current_regime == "oscillate"

    def test_compute_regime_features_in_meta(self):
        s = RegimeAdaptiveStrategy()
        kline = _build_kline_series(n=200, bull=True)
        tech_list = [_tech()]
        signals = s.compute(tech_list, kline, _compute_ctx())
        if signals:
            assert "regime_features" in signals[0].meta
            assert "trend_strength" in signals[0].meta["regime_features"]


class TestRegimeAdaptiveStrategyScore:
    def test_score_contains_regime_info(self):
        s = RegimeAdaptiveStrategy()
        kline = _build_kline_series(n=200, bull=True)
        tech_list = [_tech()]
        raw_signals = s.compute(tech_list, kline, _compute_ctx())
        if raw_signals:
            scored = s.score(raw_signals, tech_list)
            assert scored[0].extra.get("regime") == "bull"
            assert scored[0].extra.get("regime_confidence", 0) > 0

    def test_score_empty(self):
        s = RegimeAdaptiveStrategy()
        assert s.score([], []) == []


# ════════════════════════════════════════════════════════════
# DynamicWeightStrategy
# ════════════════════════════════════════════════════════════

class TestDynamicWeightStrategyInit:
    def test_default_init(self):
        s = DynamicWeightStrategy()
        assert s.name == "dynamic_weight"
        assert s._lookback_windows == 20
        assert s._decay_factor == 0.9

    def test_custom_params(self):
        s = DynamicWeightStrategy(lookback_windows=10, decay_factor=0.8)
        assert s._lookback_windows == 10
        assert s._decay_factor == 0.8

    def test_factor_history_initialized(self):
        s = DynamicWeightStrategy()
        assert len(s._factor_history) > 0
        for fname, hist in s._factor_history.items():
            assert hist.maxlen == 20

    def test_factor_history_property(self):
        s = DynamicWeightStrategy()
        hist = s.factor_history
        assert isinstance(hist, dict)
        assert len(hist) > 0


class TestDynamicWeightStrategyPerformance:
    def test_update_factor_performance(self):
        s = DynamicWeightStrategy()
        s.update_factor_performance({"momentum": 0.5, "basis": -0.2}, {"RB": 1.0})
        assert len(s._factor_history["momentum"]) == 1
        assert len(s._factor_history["basis"]) == 1

    def test_update_factor_performance_empty_returns(self):
        s = DynamicWeightStrategy()
        s.update_factor_performance({"momentum": 0.5}, {})
        # 空 returns 不更新
        assert len(s._factor_history["momentum"]) == 0

    def test_update_factor_performance_new_factor(self):
        s = DynamicWeightStrategy()
        s.update_factor_performance({"new_factor": 0.5}, {"RB": 1.0})
        assert "new_factor" in s._factor_history

    def test_calc_approx_ic(self):
        ic = DynamicWeightStrategy._calc_approx_ic(0.5, {"RB": 1.0})
        assert ic == 0.5

    def test_calc_approx_ic_empty_returns(self):
        ic = DynamicWeightStrategy._calc_approx_ic(0.5, {})
        assert ic == 0.0

    def test_calc_dynamic_weights_no_history(self):
        s = DynamicWeightStrategy()
        # 无历史 → 返回基础权重
        weights = s._calc_dynamic_weights()
        assert weights == s._weights

    def test_calc_dynamic_weights_with_history(self):
        s = DynamicWeightStrategy()
        s._factor_history["momentum"].append(0.5)
        s._factor_history["momentum"].append(0.6)
        s._factor_history["volatility_reversion"].append(0.1)
        s._factor_history["volume_flow"].append(0.2)
        weights = s._calc_dynamic_weights()
        # momentum 表现好 → 权重应增加
        assert weights["momentum"] > s._weights["momentum"] or abs(weights["momentum"] - s._weights["momentum"]) < 0.01

    def test_calc_dynamic_weights_normalized(self):
        s = DynamicWeightStrategy()
        s._factor_history["momentum"].append(0.5)
        s._factor_history["basis"].append(-0.2)
        weights = s._calc_dynamic_weights()
        total = sum(weights.values())
        assert total == pytest.approx(1.0, abs=0.01)


class TestDynamicWeightStrategyCompute:
    def test_compute_dynamic_weights_applied(self):
        s = DynamicWeightStrategy()
        tech_list = [_tech()]
        signals = s.compute(tech_list, {}, _compute_ctx())
        # 首次调用：权重更新但无历史 → 使用基础权重
        if signals:
            assert signals[0].meta.get("is_dynamic") is True
            assert "dynamic_weights" in signals[0].meta

    def test_compute_multiple_calls_accumulate_history(self):
        s = DynamicWeightStrategy()
        tech_list = [_tech()]
        # 多次调用积累历史
        s.compute(tech_list, {}, _compute_ctx())
        s.compute(tech_list, {}, _compute_ctx())
        s.compute(tech_list, {}, _compute_ctx())
        # 应有历史记录
        has_history = any(len(v) > 0 for v in s._factor_history.values())
        assert has_history

    def test_compute_empty_tech_list(self):
        s = DynamicWeightStrategy()
        assert s.compute([], {}) == []

    def test_compute_context_none(self):
        s = DynamicWeightStrategy()
        tech_list = [_tech()]
        signals = s.compute(tech_list, {}, None)
        assert isinstance(signals, list)


class TestDynamicWeightStrategyScore:
    def test_score_contains_dynamic_weights(self):
        s = DynamicWeightStrategy()
        tech_list = [_tech()]
        raw_signals = s.compute(tech_list, {}, _compute_ctx())
        if raw_signals:
            scored = s.score(raw_signals, tech_list)
            assert "dynamic_weights" in scored[0].extra

    def test_score_empty(self):
        s = DynamicWeightStrategy()
        assert s.score([], []) == []


# ════════════════════════════════════════════════════════════
# MultiPeriodSignalFusion
# ════════════════════════════════════════════════════════════

class TestMultiPeriodSignalFusionInit:
    def test_default_init(self):
        s = MultiPeriodSignalFusion()
        assert s.name == "multi_period_fusion"
        assert s._mode == "pure_momentum"
        assert s.SHORT_WEIGHT == 0.3
        assert s.MEDIUM_WEIGHT == 0.4
        assert s.LONG_WEIGHT == 0.3

    def test_custom_mode(self):
        s = MultiPeriodSignalFusion("long_short")
        assert s._mode == "long_short"

    def test_properties(self):
        s = MultiPeriodSignalFusion()
        assert s.validators == ["stability"]
        assert s.weight == 0.8
        assert s.signal_type == "multi_period_fusion.pure_momentum"


class TestMultiPeriodSignalFusionHelpers:
    def test_calc_factor_scores(self):
        s = MultiPeriodSignalFusion()
        scores = s._calc_factor_scores(_tech(), {}, {})
        assert "momentum" in scores
        assert "volatility_reversion" in scores
        assert "basis" in scores

    def test_calc_period_score(self):
        score = MultiPeriodSignalFusion._calc_period_score(
            {"momentum": 0.5, "basis": -0.2},
            {"momentum": 0.4, "basis": 0.3},
        )
        assert score == pytest.approx(0.5 * 0.4 + (-0.2) * 0.3)

    def test_extract_period_data_short(self):
        t = {"ma_slope": 0.1, "ma_slope_short": 0.2, "vol_ratio": 1.0, "vol_ratio_short": 1.5}
        short_t = MultiPeriodSignalFusion._extract_period_data(t, "short")
        assert short_t["ma_slope"] == 0.2
        assert short_t["vol_ratio"] == 1.5

    def test_extract_period_data_long(self):
        t = {"ma_slope": 0.1, "ma_slope_long": 0.05, "vol_ratio": 1.0, "vol_ratio_long": 0.8}
        long_t = MultiPeriodSignalFusion._extract_period_data(t, "long")
        assert long_t["ma_slope"] == 0.05
        assert long_t["vol_ratio"] == 0.8

    def test_extract_period_data_medium(self):
        t = {"ma_slope": 0.1, "vol_ratio": 1.0}
        medium_t = MultiPeriodSignalFusion._extract_period_data(t, "medium")
        assert medium_t["ma_slope"] == 0.1
        assert medium_t["vol_ratio"] == 1.0

    def test_extract_period_data_short_fallback(self):
        """短周期缺少专用字段时使用默认值。"""
        t = {"ma_slope": 0.1, "vol_ratio": 1.0}
        short_t = MultiPeriodSignalFusion._extract_period_data(t, "short")
        assert short_t["ma_slope"] == 0.1
        assert short_t["vol_ratio"] == 1.0


class TestMultiPeriodSignalFusionCompute:
    def test_compute_returns_signals(self):
        s = MultiPeriodSignalFusion()
        tech_list = [_tech()]
        signals = s.compute(tech_list, {}, _compute_ctx())
        if signals:
            assert signals[0].strategy_name == "multi_period_fusion"
            assert "short_score" in signals[0].meta
            assert "medium_score" in signals[0].meta
            assert "long_score" in signals[0].meta
            assert "fused_score" in signals[0].meta
            assert "consensus" in signals[0].meta
            assert "period_directions" in signals[0].meta

    def test_compute_empty_tech_list(self):
        s = MultiPeriodSignalFusion()
        assert s.compute([], {}) == []

    def test_compute_price_zero_skipped(self):
        s = MultiPeriodSignalFusion()
        tech_list = [_tech(price=0)]
        assert s.compute(tech_list, {}) == []

    def test_compute_context_none(self):
        s = MultiPeriodSignalFusion()
        tech_list = [_tech()]
        signals = s.compute(tech_list, {}, None)
        assert isinstance(signals, list)

    def test_compute_long_short_mode(self):
        """long_short 模式裁剪信号。"""
        s = MultiPeriodSignalFusion("long_short")
        symbols = ["RB", "CU", "AL", "ZN", "NI", "AU", "AG", "PB", "SN", "SS"]
        tech_list = [
            _tech(symbol=sym, change_pct=2.0 - i * 0.4)
            for i, sym in enumerate(symbols)
        ]
        ctx = {
            "extra": {
                "oi_data": {sym: {"oi_ratio": 0.1, "top5_ratio": 0.5} for sym in symbols},
                "basis_data": {sym: {"basis_pct": 3.0} for sym in symbols},
                "warrant_data": {sym: {"total": 1000.0, "daily_change": -50.0} for sym in symbols},
                "inventory_data": {},
                "supply_data": {},
                "macro_data": {"available": False},
            },
            "macro_signal": "bull",
        }
        signals = s.compute(tech_list, {}, ctx)
        # long_short: 最多 2 bull + 2 bear
        assert len(signals) <= 4
        if signals:
            bull_count = sum(1 for sig in signals if sig.direction == "bull")
            bear_count = sum(1 for sig in signals if sig.direction == "bear")
            assert bull_count <= 2
            assert bear_count <= 2

    def test_compute_consensus_check(self):
        """三个周期方向一致时 consensus=True。"""
        s = MultiPeriodSignalFusion()
        # 强趋势信号，各周期方向一致
        tech_list = [_tech(
            change_pct=5.0, ma_slope=0.5, macd_cross="gold_cross",
            ma_slope_short=0.6, ma_slope_long=0.3,
        )]
        signals = s.compute(tech_list, {}, _compute_ctx())
        if signals:
            assert signals[0].meta["consensus"] is True


class TestMultiPeriodSignalFusionScore:
    def test_score_returns_scored_signals(self):
        s = MultiPeriodSignalFusion()
        raw = RawSignal("RB", "bull", "multi_period_fusion.pure_momentum.composite",
                        0.5, "multi_period_fusion",
                        meta={"factor_scores": {"momentum": 0.5},
                              "short_score": 0.4, "medium_score": 0.5, "long_score": 0.3,
                              "fused_score": 0.5, "consensus": True,
                              "active_factors": 5, "mode": "pure_momentum",
                              "price": 3500.0, "period_directions": ["bull", "bull", "bull"]})
        scored = s.score([raw], [])
        assert len(scored) == 1
        assert scored[0].grade == "STRONG"
        assert scored[0].weight == 0.8

    def test_score_empty(self):
        s = MultiPeriodSignalFusion()
        assert s.score([], []) == []

    def test_score_bear_direction(self):
        s = MultiPeriodSignalFusion()
        raw = RawSignal("RB", "bear", "test", 0.5, "multi_period_fusion",
                        meta={"factor_scores": {}, "short_score": -0.4, "medium_score": -0.5,
                              "long_score": -0.3, "fused_score": -0.5, "consensus": True,
                              "active_factors": 5, "mode": "pure_momentum",
                              "price": 3500.0, "period_directions": ["bear", "bear", "bear"]})
        scored = s.score([raw], [])
        assert scored[0].total == -50.0