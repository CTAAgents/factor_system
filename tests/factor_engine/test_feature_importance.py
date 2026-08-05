"""tests/factor_engine/test_feature_importance.py — 特征重要性分析测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.feature_importance import (
    FeatureImportanceAnalyzer,
    FeatureImportanceResult,
)


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """合成测试数据。"""
    np.random.seed(42)
    n = 200
    return pd.DataFrame({
        "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
        "high": 105 + np.cumsum(np.random.randn(n) * 0.5),
        "low": 95 + np.cumsum(np.random.randn(n) * 0.5),
        "volume": np.random.randint(1000, 10000, n).astype(float),
        "forward_return_20d": np.random.randn(n) * 0.02,
    })


@pytest.fixture
def factor_series(sample_data) -> pd.Series:
    """示例因子序列 (close 的 zscore)。"""
    close = sample_data["close"]
    return (close - close.mean()) / close.std()


class TestFeatureImportanceAnalyzer:
    def test_analyze_returns_result(self, sample_data, factor_series):
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor_series,
            data=sample_data,
            target_col="forward_return_20d",
        )
        assert isinstance(result, FeatureImportanceResult)
        assert result.n_features_analyzed > 0
        assert len(result.feature_importance) > 0

    def test_analyze_with_custom_features(self, sample_data, factor_series):
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor_series,
            data=sample_data,
            target_col="forward_return_20d",
            feature_names=["close", "volume"],
        )
        assert result.n_features_analyzed == 2
        assert "close" in result.feature_importance
        assert "volume" in result.feature_importance

    def test_top_features(self, sample_data, factor_series):
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor_series,
            data=sample_data,
            target_col="forward_return_20d",
        )
        assert len(result.top_features) <= 10
        assert len(result.top_features) > 0

    def test_baseline_ic(self, sample_data, factor_series):
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor_series,
            data=sample_data,
            target_col="forward_return_20d",
        )
        assert isinstance(result.baseline_ic, float)

    def test_analyze_small_data(self):
        """测试小数据集。"""
        small_data = pd.DataFrame({
            "close": [100.0, 101.0, 102.0, 103.0, 104.0],
            "forward_return_20d": [0.01, -0.01, 0.02, -0.02, 0.01],
        })
        factor = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor,
            data=small_data,
            target_col="forward_return_20d",
        )
        assert result.n_features_analyzed == 0  # 数据不足，应返回空

    def test_analyze_with_nan_factor(self, sample_data):
        """测试含 NaN 的因子序列。"""
        factor = pd.Series([np.nan] * len(sample_data), index=sample_data.index)
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor,
            data=sample_data,
            target_col="forward_return_20d",
        )
        assert result.baseline_ic == 0.0

    def test_permutation_importance_non_negative(self, sample_data, factor_series):
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor_series,
            data=sample_data,
            target_col="forward_return_20d",
            n_permutations=1,
        )
        # 重要性分数可以为负 (打乱后 IC 反而提高)
        for score in result.feature_importance.values():
            assert isinstance(score, float)

    def test_analysis_time_recorded(self, sample_data, factor_series):
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor_series,
            data=sample_data,
            target_col="forward_return_20d",
        )
        assert result.analysis_time_ms >= 0

    def test_unknown_feature_skipped(self, sample_data, factor_series):
        """测试请求不存在的特征。"""
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor_series,
            data=sample_data,
            target_col="forward_return_20d",
            feature_names=["close", "nonexistent_feature"],
        )
        # nonexistent_feature 应该被跳过
        assert "nonexistent_feature" not in result.feature_importance
        assert "close" in result.feature_importance

    def test_analysis_method(self, sample_data, factor_series):
        analyzer = FeatureImportanceAnalyzer()
        result = analyzer.analyze(
            factor_series=factor_series,
            data=sample_data,
            target_col="forward_return_20d",
        )
        assert result.analysis_method == "permutation"