"""tests/factor_engine/test_feature_ops.py — 特征算子库测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.feature_ops import (
    CompositeOps,
    CrossSectionOps,
    CrossSymbolOps,
    FeatureOpsEngine,
    OperatorInfo,
    OperatorRegistry,
    PriceOps,
    RollingOps,
    TechnicalOps,
    TimeSeriesOps,
)


# ─── TimeSeriesOps ────────────────────────────────────────


class TestTimeSeriesOps:
    """时序算子测试。"""

    def test_ts_mean(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = TimeSeriesOps.ts_mean(s, window=3)
        assert result.iloc[-1] == pytest.approx(4.0)
        assert pd.isna(result.iloc[0])

    def test_ts_std(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = TimeSeriesOps.ts_std(s, window=3)
        assert not pd.isna(result.iloc[-1])

    def test_ts_max(self):
        s = pd.Series([1.0, 3.0, 2.0, 5.0, 4.0])
        result = TimeSeriesOps.ts_max(s, window=3)
        assert result.iloc[-1] == 5.0

    def test_ts_min(self):
        s = pd.Series([3.0, 1.0, 2.0, 4.0, 0.5])
        result = TimeSeriesOps.ts_min(s, window=3)
        assert result.iloc[-1] == 0.5

    def test_ts_sum(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = TimeSeriesOps.ts_sum(s, window=3)
        assert result.iloc[-1] == 12.0

    def test_ts_product(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = TimeSeriesOps.ts_product(s, window=3)
        assert result.iloc[-1] == 60.0


# ─── PriceOps ─────────────────────────────────────────────


class TestPriceOps:
    """价格算子测试。"""

    def test_rank(self):
        s = pd.Series([3.0, 1.0, 4.0, 2.0])
        result = PriceOps.rank(s)
        assert len(result) == 4
        assert not result.isna().any()

    def test_zscore(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = PriceOps.zscore(s)
        assert abs(result.mean()) < 1e-10
        assert abs(result.std() - 1.0) < 0.2

    def test_zscore_constant(self):
        s = pd.Series([5.0, 5.0, 5.0])
        result = PriceOps.zscore(s)
        assert (result == 5.0).all()

    def test_delta(self):
        s = pd.Series([1.0, 3.0, 6.0, 10.0])
        result = PriceOps.delta(s, periods=1)
        assert result.iloc[-1] == 4.0

    def test_pct_change(self):
        s = pd.Series([100.0, 110.0, 105.0])
        result = PriceOps.pct_change(s)
        assert result.iloc[1] == pytest.approx(0.1)

    def test_log_return(self):
        s = pd.Series([100.0, 110.0, 121.0])
        result = PriceOps.log_return(s)
        expected = np.log(110.0 / 100.0)
        assert result.iloc[1] == pytest.approx(expected, rel=1e-6)


# ─── RollingOps ───────────────────────────────────────────


class TestRollingOps:
    """滚动算子测试。"""

    def test_ts_rank(self):
        s = pd.Series([1.0, 3.0, 2.0, 5.0, 4.0])
        result = RollingOps.ts_rank(s, window=3)
        assert not pd.isna(result.iloc[-1])

    def test_ts_zscore(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = RollingOps.ts_zscore(s, window=3)
        assert not pd.isna(result.iloc[-1])

    def test_ts_momentum(self):
        s = pd.Series([100.0, 110.0, 120.0, 130.0, 140.0])
        result = RollingOps.ts_momentum(s, window=2)
        expected = 140.0 / 120.0 - 1
        assert result.iloc[-1] == pytest.approx(expected, rel=1e-6)

    def test_ts_volatility(self):
        s = pd.Series([100.0, 110.0, 105.0, 115.0, 120.0])
        result = RollingOps.ts_volatility(s, window=3)
        assert not pd.isna(result.iloc[-1])

    def test_ts_skewness(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = RollingOps.ts_skewness(s, window=5)
        assert not pd.isna(result.iloc[-1])

    def test_ts_kurtosis(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = RollingOps.ts_kurtosis(s, window=5)
        assert not pd.isna(result.iloc[-1])


# ─── CrossSectionOps ──────────────────────────────────────


class TestCrossSectionOps:
    """截面算子测试。"""

    def test_cross_rank(self):
        panel = pd.DataFrame({
            "date": ["2024-01-01"] * 3 + ["2024-01-02"] * 3,
            "value": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        })
        result = CrossSectionOps.cross_rank(panel)
        assert "cross_rank" in result.columns
        assert len(result) == 6

    def test_cross_zscore(self):
        panel = pd.DataFrame({
            "date": ["2024-01-01"] * 3 + ["2024-01-02"] * 3,
            "value": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        })
        result = CrossSectionOps.cross_zscore(panel)
        assert "cross_zscore" in result.columns
        assert len(result) == 6

    def test_industry_neutral(self):
        panel = pd.DataFrame({
            "date": ["2024-01-01"] * 4,
            "industry": ["A", "A", "B", "B"],
            "value": [10.0, 20.0, 30.0, 40.0],
        })
        result = CrossSectionOps.industry_neutral(panel)
        assert "neutralized" in result.columns
        assert "industry_mean" in result.columns


# ─── CompositeOps ──────────────────────────────────────────


class TestCompositeOps:
    """组合算子测试。"""

    def test_add(self):
        a = pd.Series([1.0, 2.0])
        b = pd.Series([3.0, 4.0])
        result = CompositeOps.add(a, b)
        assert (result == [4.0, 6.0]).all()

    def test_sub(self):
        a = pd.Series([5.0, 10.0])
        b = pd.Series([2.0, 3.0])
        result = CompositeOps.sub(a, b)
        assert (result == [3.0, 7.0]).all()

    def test_mul(self):
        a = pd.Series([2.0, 3.0])
        b = pd.Series([4.0, 5.0])
        result = CompositeOps.mul(a, b)
        assert (result == [8.0, 15.0]).all()

    def test_div(self):
        a = pd.Series([10.0, 20.0])
        b = pd.Series([2.0, 4.0])
        result = CompositeOps.div(a, b)
        assert (result == [5.0, 5.0]).all()

    def test_div_by_zero(self):
        a = pd.Series([10.0, 20.0])
        b = pd.Series([0.0, 4.0])
        result = CompositeOps.div(a, b)
        assert pd.isna(result.iloc[0])

    def test_scale(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = CompositeOps.scale(s, factor=2.0)
        assert (result == [2.0, 4.0, 6.0]).all()

    def test_if_then_else(self):
        cond = pd.Series([True, False, True])
        then_v = pd.Series([1.0, 2.0, 3.0])
        else_v = pd.Series([10.0, 20.0, 30.0])
        result = CompositeOps.if_then_else(cond, then_v, else_v)
        assert list(result) == [1.0, 20.0, 3.0]

    def test_conditional_weight(self):
        s = pd.Series([1.0, -2.0, 3.0])
        w = pd.Series([0.5, 0.5, 0.5])
        result = CompositeOps.conditional_weight(s, w, threshold=0.0)
        assert result.iloc[0] == 0.5
        assert result.iloc[1] == 0.0
        assert result.iloc[2] == 1.5


# ─── OperatorRegistry ─────────────────────────────────────


class TestOperatorRegistry:
    """算子注册表测试。"""

    def test_init_creates_operators(self):
        registry = OperatorRegistry()
        assert registry.operator_count > 0

    def test_list_categories(self):
        registry = OperatorRegistry()
        categories = registry.list_categories()
        assert "time_series" in categories
        assert "price" in categories
        assert "composite" in categories

    def test_list_operators_all(self):
        registry = OperatorRegistry()
        ops = registry.list_operators()
        assert len(ops) > 0
        assert all(isinstance(op, OperatorInfo) for op in ops)

    def test_list_operators_by_category(self):
        registry = OperatorRegistry()
        ts_ops = registry.list_operators(category="time_series")
        assert all(op.category == "time_series" for op in ts_ops)

    def test_get_operator(self):
        registry = OperatorRegistry()
        op = registry.get_operator("rank")
        assert op is not None
        assert op.name == "rank"

    def test_get_operator_not_found(self):
        registry = OperatorRegistry()
        op = registry.get_operator("nonexistent_op")
        assert op is None

    def test_call(self):
        registry = OperatorRegistry()
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        result = registry.call("ts_mean", s, window=3)
        assert not pd.isna(result.iloc[-1])

    def test_call_not_found(self):
        registry = OperatorRegistry()
        s = pd.Series([1.0, 2.0])
        with pytest.raises(KeyError):
            registry.call("nonexistent", s)

    def test_register_custom_operator(self):
        registry = OperatorRegistry()
        initial_count = registry.operator_count

        def custom_op(series: pd.Series) -> pd.Series:
            return series * 2

        registry.register(
            "custom_double", custom_op, "custom", ["series"],
            description="自定义翻倍算子",
        )
        assert registry.operator_count == initial_count + 1

        s = pd.Series([1.0, 2.0])
        result = registry.call("custom_double", s)
        assert (result == [2.0, 4.0]).all()


# ─── CrossSymbolOps ──────────────────────────────────────


class TestCrossSymbolOps:
    """跨品种算子测试。"""

    def test_industry_demean(self):
        panel = pd.DataFrame({
            "date": ["2024-01-01"] * 4,
            "industry": ["A", "A", "B", "B"],
            "value": [10.0, 20.0, 30.0, 40.0],
        })
        result = CrossSymbolOps.industry_demean(panel)
        assert "industry_mean" in result.columns
        assert len(result) == 4

    def test_cap_demean(self):
        panel = pd.DataFrame({
            "date": ["2024-01-01"] * 3,
            "market_cap": [100.0, 200.0, 300.0],
            "value": [10.0, 20.0, 30.0],
        })
        result = CrossSymbolOps.cap_demean(panel)
        assert "cap_mean" in result.columns
        assert "cap_weight" in result.columns
        assert len(result) == 3

    def test_region_demean(self):
        panel = pd.DataFrame({
            "date": ["2024-01-01"] * 4,
            "region": ["N", "N", "S", "S"],
            "value": [10.0, 20.0, 30.0, 40.0],
        })
        result = CrossSymbolOps.region_demean(panel)
        assert "region_mean" in result.columns
        assert len(result) == 4


# ─── FeatureOpsEngine ──────────────────────────────────────


class TestFeatureOpsEngine:
    """特征工程中台引擎测试。"""

    def test_init(self):
        engine = FeatureOpsEngine()
        assert engine.registry is not None
        assert engine.registry.operator_count > 0

    def test_list_operators(self):
        engine = FeatureOpsEngine()
        ops = engine.list_operators()
        assert len(ops) > 0
        assert all(isinstance(op, OperatorInfo) for op in ops)

    def test_list_operators_by_category(self):
        engine = FeatureOpsEngine()
        ts_ops = engine.list_operators(category="time_series")
        assert all(op.category == "time_series" for op in ts_ops)

    def test_get_operator(self):
        engine = FeatureOpsEngine()
        op = engine.get_operator("rank")
        assert op is not None
        assert op.name == "rank"

    def test_get_operator_not_found(self):
        engine = FeatureOpsEngine()
        op = engine.get_operator("nonexistent")
        assert op is None

    def test_list_categories(self):
        engine = FeatureOpsEngine()
        categories = engine.list_categories()
        assert "time_series" in categories
        assert "price" in categories
        assert "rolling" in categories
        assert "cross_section" in categories
        assert "composite" in categories
        assert "cross_symbol" in categories

    def test_register_operator(self):
        engine = FeatureOpsEngine()
        initial_count = engine.registry.operator_count

        def custom_op(series: pd.Series) -> pd.Series:
            return series * 3

        engine.register_operator(
            "custom_triple", custom_op, "custom", ["series"],
            description="自定义三倍算子",
        )
        assert engine.registry.operator_count == initial_count + 1

        s = pd.Series([1.0, 2.0])
        result = engine.registry.call("custom_triple", s)
        assert (result == [3.0, 6.0]).all()

    def test_run_gp_search(self):
        np.random.seed(42)
        data = pd.DataFrame({
            "close": 100 + np.cumsum(np.random.randn(100) * 0.5),
            "high": 105 + np.cumsum(np.random.randn(100) * 0.5),
            "low": 95 + np.cumsum(np.random.randn(100) * 0.5),
            "volume": np.random.randint(1000, 10000, 100).astype(float),
            "forward_return_20d": np.random.randn(100) * 0.02,
        })
        engine = FeatureOpsEngine()
        result = engine.run_gp_search(
            data,
            target="forward_return_20d",
            config={"population_size": 10, "max_generations": 2},
        )
        assert result is not None
        assert result.generations_completed > 0
        assert result.total_evaluations > 0

    def test_analyze_importance(self):
        np.random.seed(42)
        data = pd.DataFrame({
            "close": 100 + np.cumsum(np.random.randn(100) * 0.5),
            "high": 105 + np.cumsum(np.random.randn(100) * 0.5),
            "low": 95 + np.cumsum(np.random.randn(100) * 0.5),
            "volume": np.random.randint(1000, 10000, 100).astype(float),
            "forward_return_20d": np.random.randn(100) * 0.02,
        })
        close = data["close"]
        factor_series = (close - close.mean()) / close.std()

        engine = FeatureOpsEngine()
        result = engine.analyze_importance(
            factor_series=factor_series,
            data=data,
            target_col="forward_return_20d",
        )
        assert result is not None
        assert result.n_features_analyzed > 0

    def test_analyze_importance_with_custom_features(self):
        np.random.seed(42)
        data = pd.DataFrame({
            "close": 100 + np.cumsum(np.random.randn(100) * 0.5),
            "high": 105 + np.cumsum(np.random.randn(100) * 0.5),
            "low": 95 + np.cumsum(np.random.randn(100) * 0.5),
            "volume": np.random.randint(1000, 10000, 100).astype(float),
            "forward_return_20d": np.random.randn(100) * 0.02,
        })
        factor_series = data["close"]

        engine = FeatureOpsEngine()
        result = engine.analyze_importance(
            factor_series=factor_series,
            data=data,
            target_col="forward_return_20d",
            feature_names=["close", "volume"],
        )
        assert result.n_features_analyzed == 2


# ─── TechnicalOps ──────────────────────────────────────────


class TestTechnicalOps:
    """技术指标算子测试。"""

    def test_rsi(self):
        s = pd.Series([100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0, 108.0, 107.0, 109.0, 110.0, 112.0, 111.0, 113.0, 115.0])
        result = TechnicalOps.rsi(s, window=5)
        assert not pd.isna(result.iloc[-1])
        assert 0 <= result.iloc[-1] <= 100

    def test_bollinger_upper(self):
        s = pd.Series([100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0, 108.0, 107.0, 109.0, 110.0, 112.0, 111.0, 113.0, 115.0])
        result = TechnicalOps.bollinger_upper(s, window=5)
        assert not pd.isna(result.iloc[-1])

    def test_bollinger_lower(self):
        s = pd.Series([100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0, 108.0, 107.0, 109.0, 110.0, 112.0, 111.0, 113.0, 115.0])
        result = TechnicalOps.bollinger_lower(s, window=5)
        assert not pd.isna(result.iloc[-1])

    def test_bollinger_width(self):
        s = pd.Series([100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0, 108.0, 107.0, 109.0, 110.0, 112.0, 111.0, 113.0, 115.0])
        result = TechnicalOps.bollinger_width(s, window=5)
        assert not pd.isna(result.iloc[-1])

    def test_atr(self):
        high = pd.Series([102.0, 104.0, 103.0, 105.0, 107.0, 106.0, 108.0, 110.0, 109.0, 111.0])
        low = pd.Series([98.0, 100.0, 99.0, 101.0, 103.0, 102.0, 104.0, 106.0, 105.0, 107.0])
        close = pd.Series([100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0, 108.0, 107.0, 109.0])
        result = TechnicalOps.atr(high, low, close, window=5)
        assert not pd.isna(result.iloc[-1])
        assert result.iloc[-1] > 0

    def test_macd(self):
        s = pd.Series([100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0, 108.0, 107.0, 109.0, 110.0, 112.0, 111.0, 113.0, 115.0])
        result = TechnicalOps.macd(s, fast=5, slow=10, signal=3)
        assert not pd.isna(result.iloc[-1])

    def test_max_drawdown(self):
        s = pd.Series([100.0, 105.0, 103.0, 108.0, 102.0, 110.0])
        result = TechnicalOps.max_drawdown(s, window=5)
        assert not pd.isna(result.iloc[-1])
        assert result.iloc[-1] <= 0


# ─── Additional RollingOps ────────────────────────────────


class TestRollingOpsAdditional:
    """额外滚动算子测试。"""

    def test_ts_median(self):
        s = pd.Series([1.0, 3.0, 2.0, 5.0, 4.0])
        result = RollingOps.ts_median(s, window=3)
        assert not pd.isna(result.iloc[-1])
        assert result.iloc[-1] == 4.0  # median of [2.0, 5.0, 4.0] = 4.0

    def test_ts_min_max_diff(self):
        s = pd.Series([1.0, 3.0, 2.0, 5.0, 4.0])
        result = RollingOps.ts_min_max_diff(s, window=3)
        assert not pd.isna(result.iloc[-1])
        assert result.iloc[-1] == 3.0  # max(5,4,2) - min(5,4,2) = 5-2 = 3

    def test_ts_cum_max(self):
        s = pd.Series([1.0, 3.0, 2.0, 5.0, 4.0])
        result = RollingOps.ts_cum_max(s, window=3)
        assert not pd.isna(result.iloc[-1])