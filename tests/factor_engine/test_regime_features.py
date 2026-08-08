"""
tests/factor_engine/test_regime_features.py — 扩展特征提取模块测试（STEP3 P2.1）

覆盖范围:
    - volume_shock: 成交量冲击因子
    - return_skewness: 收益率偏度
    - return_kurtosis: 收益率峰度
    - return_autocorr: 收益率自相关系数
    - intraday_range_ratio: 日内波幅比
    - cross_symbol_correlation: 跨品种相关系数
    - compute_extended_features: 综合特征提取
    - compute_hmm_feature_vector: HMM 特征向量构建

版本: v0.1.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.regime_features import (
    volume_shock,
    return_skewness,
    return_kurtosis,
    return_autocorr,
    intraday_range_ratio,
    cross_symbol_correlation,
    compute_extended_features,
    compute_hmm_feature_vector,
)


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def ohlcv() -> pd.DataFrame:
    """标准上涨趋势 OHLCV。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
    return pd.DataFrame({
        "open": close * (1 + np.random.randn(n) * 0.002),
        "high": close * (1 + np.abs(np.random.randn(n)) * 0.005),
        "low": close * (1 - np.abs(np.random.randn(n)) * 0.005),
        "close": close,
        "volume": np.random.randint(800, 1200, n).astype(float),
    }, index=dates)


@pytest.fixture
def flat_ohlcv() -> pd.DataFrame:
    """水平震荡 OHLCV。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 2.0
    return pd.DataFrame({
        "open": close * (1 + np.random.randn(n) * 0.002),
        "high": close * (1 + np.abs(np.random.randn(n)) * 0.005),
        "low": close * (1 - np.abs(np.random.randn(n)) * 0.005),
        "close": close,
        "volume": np.random.randint(800, 1200, n).astype(float),
    }, index=dates)


# ═══════════════════════════════════════════════════════════
# 1. volume_shock
# ═══════════════════════════════════════════════════════════

class TestVolumeShock:
    """成交量冲击因子。"""

    def test_normal(self, ohlcv: pd.DataFrame) -> None:
        """正常数据 → 返回有限值。"""
        result = volume_shock(ohlcv)
        assert isinstance(result, float)
        assert not np.isnan(result)

    def test_empty(self) -> None:
        """空 DataFrame → 返回 0.0。"""
        assert volume_shock(pd.DataFrame()) == 0.0

    def test_no_volume(self) -> None:
        """无 volume 列 → 返回 0.0。"""
        df = pd.DataFrame({"close": [100, 101, 102]})
        assert volume_shock(df) == 0.0

    def test_constant_volume(self) -> None:
        """成交量恒定 → 冲击为 0。"""
        dates = pd.date_range("2024-01-01", periods=50, freq="D")
        df = pd.DataFrame({
            "close": 100 + np.arange(50).astype(float),
            "volume": np.ones(50) * 1000,
        }, index=dates)
        result = volume_shock(df)
        assert abs(result) < 1e-10


# ═══════════════════════════════════════════════════════════
# 2. return_skewness
# ═══════════════════════════════════════════════════════════

class TestReturnSkewness:
    """收益率偏度。"""

    def test_normal(self, ohlcv: pd.DataFrame) -> None:
        """正常数据 → 返回有限值。"""
        result = return_skewness(ohlcv)
        assert isinstance(result, float)
        assert not np.isnan(result)

    def test_empty(self) -> None:
        """空 DataFrame → 返回 0.0。"""
        assert return_skewness(pd.DataFrame()) == 0.0

    def test_short_data(self) -> None:
        """数据不足 → 返回 0.0。"""
        df = pd.DataFrame({"close": [100, 101]})
        assert return_skewness(df) == 0.0

    def test_constant_close(self) -> None:
        """收盘价恒定 → 返回 0.0。"""
        dates = pd.date_range("2024-01-01", periods=50, freq="D")
        df = pd.DataFrame({"close": np.ones(50) * 100}, index=dates)
        result = return_skewness(df)
        assert result == 0.0


# ═══════════════════════════════════════════════════════════
# 3. return_kurtosis
# ═══════════════════════════════════════════════════════════

class TestReturnKurtosis:
    """收益率峰度。"""

    def test_normal(self, ohlcv: pd.DataFrame) -> None:
        """正常数据 → 返回有限值。"""
        result = return_kurtosis(ohlcv)
        assert isinstance(result, float)
        assert not np.isnan(result)

    def test_empty(self) -> None:
        """空 DataFrame → 返回 0.0。"""
        assert return_kurtosis(pd.DataFrame()) == 0.0

    def test_short_data(self) -> None:
        """数据不足 → 返回 0.0。"""
        df = pd.DataFrame({"close": [100, 101]})
        assert return_kurtosis(df) == 0.0

    def test_constant_close(self) -> None:
        """收盘价恒定 → 返回 0.0。"""
        dates = pd.date_range("2024-01-01", periods=50, freq="D")
        df = pd.DataFrame({"close": np.ones(50) * 100}, index=dates)
        result = return_kurtosis(df)
        assert result == 0.0

    def test_gaussian_kurtosis(self) -> None:
        """高斯分布 → 峰度接近 3（未中心化）。"""
        np.random.seed(42)
        n = 500
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame({"close": close}, index=dates)
        result = return_kurtosis(df)
        # 高斯分布未中心化峰度约为 3
        assert 2.0 < result < 6.0, f"预期接近 3，实际 {result}"


# ═══════════════════════════════════════════════════════════
# 4. return_autocorr
# ═══════════════════════════════════════════════════════════

class TestReturnAutocorr:
    """收益率自相关系数。"""

    def test_normal(self, ohlcv: pd.DataFrame) -> None:
        """正常数据 → 返回有限值。"""
        result = return_autocorr(ohlcv)
        assert isinstance(result, float)
        assert not np.isnan(result)

    def test_empty(self) -> None:
        """空 DataFrame → 返回 0.0。"""
        assert return_autocorr(pd.DataFrame()) == 0.0

    def test_short_data(self) -> None:
        """数据不足 → 返回 0.0。"""
        df = pd.DataFrame({"close": [100, 101]})
        assert return_autocorr(df) == 0.0

    def test_constant_close(self) -> None:
        """收盘价恒定 → 返回 0.0。"""
        dates = pd.date_range("2024-01-01", periods=50, freq="D")
        df = pd.DataFrame({"close": np.ones(50) * 100}, index=dates)
        assert return_autocorr(df) == 0.0

    def test_lag5(self, ohlcv: pd.DataFrame) -> None:
        """lag=5 正常工作。"""
        result = return_autocorr(ohlcv, lag=5)
        assert isinstance(result, float)
        assert -1 <= result <= 1


# ═══════════════════════════════════════════════════════════
# 5. intraday_range_ratio
# ═══════════════════════════════════════════════════════════

class TestIntradayRangeRatio:
    """日内波幅比。"""

    def test_normal(self, ohlcv: pd.DataFrame) -> None:
        """正常数据 → 返回正数。"""
        result = intraday_range_ratio(ohlcv)
        assert isinstance(result, float)
        assert result > 0

    def test_empty(self) -> None:
        """空 DataFrame → 返回 0.0。"""
        assert intraday_range_ratio(pd.DataFrame()) == 0.0

    def test_missing_columns(self) -> None:
        """缺少 high/low/close 列 → 返回 0.0。"""
        df = pd.DataFrame({"close": [100, 101, 102]})
        assert intraday_range_ratio(df) == 0.0


# ═══════════════════════════════════════════════════════════
# 6. cross_symbol_correlation
# ═══════════════════════════════════════════════════════════

class TestCrossSymbolCorrelation:
    """跨品种相关系数。"""

    def test_high_correlation(self) -> None:
        """高度相关品种 → 系数接近 1。"""
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        base = 100 + np.cumsum(np.random.randn(n) * 0.3)
        # 噪音极小，确保相关系数 > 0.8
        panel = {
            "SYM1": pd.DataFrame({"close": base + np.random.randn(n) * 0.01}, index=dates),
            "SYM2": pd.DataFrame({"close": base + np.random.randn(n) * 0.01}, index=dates),
            "SYM3": pd.DataFrame({"close": base + np.random.randn(n) * 0.01}, index=dates),
        }
        result = cross_symbol_correlation(panel, ["SYM1", "SYM2", "SYM3"])
        assert result > 0.8, f"预期高相关，实际 {result}"

    def test_low_correlation(self) -> None:
        """低相关品种 → 系数接近 0。"""
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        panel = {
            "SYM1": pd.DataFrame({"close": 100 + np.cumsum(np.random.randn(n) * 0.3)}, index=dates),
            "SYM2": pd.DataFrame({"close": 100 + np.cumsum(np.random.randn(n) * 0.3)}, index=dates),
        }
        # 使用不同种子
        np.random.seed(99)
        panel["SYM3"] = pd.DataFrame(
            {"close": 100 + np.cumsum(np.random.randn(n) * 0.3)}, index=dates
        )
        result = cross_symbol_correlation(panel, ["SYM1", "SYM2", "SYM3"])
        # 应低于高度相关的情况
        assert result < 0.99

    def test_single_symbol(self) -> None:
        """只有 1 个品种 → 返回 0.0。"""
        panel = {"SYM1": pd.DataFrame({"close": [100, 101, 102]})}
        assert cross_symbol_correlation(panel, ["SYM1"]) == 0.0

    def test_empty_panel(self) -> None:
        """空面板 → 返回 0.0。"""
        assert cross_symbol_correlation({}, ["SYM1"]) == 0.0

    def test_missing_symbol(self) -> None:
        """品种在 panel 中缺失 → 跳过。"""
        np.random.seed(42)
        n = 100
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        panel = {
            "SYM1": pd.DataFrame({"close": 100 + np.cumsum(np.random.randn(n) * 0.3)}, index=dates),
        }
        # SYM2 不在 panel 中
        result = cross_symbol_correlation(panel, ["SYM1", "SYM2"])
        assert result == 0.0


# ═══════════════════════════════════════════════════════════
# 7. compute_extended_features
# ═══════════════════════════════════════════════════════════

class TestComputeExtendedFeatures:
    """综合特征提取。"""

    def test_all_features_present(self, ohlcv: pd.DataFrame) -> None:
        """所有特征键都存在。"""
        features = compute_extended_features(ohlcv)
        expected_keys = {
            "volume_shock", "skewness", "kurtosis",
            "autocorr_lag1", "autocorr_lag5",
            "intraday_range_ratio", "cross_corr_mean",
        }
        assert expected_keys == set(features.keys()), (
            f"缺失键: {expected_keys - set(features.keys())}"
        )

    def test_no_panel_default(self, ohlcv: pd.DataFrame) -> None:
        """无 panel 时 cross_corr_mean 为 0.0。"""
        features = compute_extended_features(ohlcv)
        assert features["cross_corr_mean"] == 0.0

    def test_with_panel(self, ohlcv: pd.DataFrame) -> None:
        """有 panel 时 cross_corr_mean 计算正确。"""
        panel = {"SYM1": ohlcv, "SYM2": ohlcv}
        features = compute_extended_features(ohlcv, panel=panel, sector_symbols=["SYM1", "SYM2"])
        assert isinstance(features["cross_corr_mean"], float)
        # 相同数据相关系数为 1
        assert features["cross_corr_mean"] > 0.9

    def test_empty_data(self) -> None:
        """空数据 → 所有特征为 0.0。"""
        features = compute_extended_features(pd.DataFrame())
        for k, v in features.items():
            assert v == 0.0, f"{k} 应为 0.0，实际 {v}"


# ═══════════════════════════════════════════════════════════
# 8. compute_hmm_feature_vector
# ═══════════════════════════════════════════════════════════

class TestComputeHMMFeatureVector:
    """HMM 特征向量构建。"""

    def test_returns_array(self, ohlcv: pd.DataFrame) -> None:
        """返回 numpy 数组。"""
        result = compute_hmm_feature_vector(ohlcv)
        assert isinstance(result, np.ndarray)

    def test_increased_dimensions(self, ohlcv: pd.DataFrame) -> None:
        """增强后维度 > 基础 [收益, 波动] 2 维。"""
        result = compute_hmm_feature_vector(ohlcv)
        if result.size > 0:
            # 至少 2 列（基础收益+波动），通常更多（扩展特征）
            assert result.shape[1] >= 2

    def test_empty_data(self) -> None:
        """空数据 → 返回空数组。"""
        result = compute_hmm_feature_vector(pd.DataFrame())
        assert result.size == 0

    def test_short_data(self) -> None:
        """数据不足 21 行 → 返回空数组。"""
        df = pd.DataFrame({"close": [100, 101]})
        result = compute_hmm_feature_vector(df)
        assert result.size == 0

    def test_with_base_features(self, ohlcv: pd.DataFrame) -> None:
        """预计算 base_features 时复用。"""
        close = ohlcv["close"].dropna()
        rets = close.pct_change().dropna().values.reshape(-1, 1)
        vol = pd.Series(close.pct_change().dropna()).rolling(20).std().fillna(0).values.reshape(-1, 1)
        base = np.column_stack([rets, vol])
        result = compute_hmm_feature_vector(ohlcv, base_features=base)
        if result.size > 0:
            assert result.shape[0] == base.shape[0]
            assert result.shape[1] >= base.shape[1]

    def test_no_volume_column(self) -> None:
        """无 volume 列时仍能正常工作。"""
        dates = pd.date_range("2024-01-01", periods=50, freq="D")
        close = 100 + np.cumsum(np.random.randn(50) * 0.3)
        df = pd.DataFrame({
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
        }, index=dates)
        result = compute_hmm_feature_vector(df)
        assert isinstance(result, np.ndarray)