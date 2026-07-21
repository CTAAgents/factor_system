"""
tests/factor_engine/test_regime.py — RegimeAwareSelector 测试

覆盖范围:
    - detect() 各种市场制度判定（bull/bear/oscillate/high_vol/low_vol）
    - 异常/边界情况（空数据、NaN、常量价格、短数据）
    - confidence 范围（0~1）
    - features 格式
    - profile_factor() / select_factors() / regime_report()

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

from fts.factor_engine.regime import (
    RegimeAwareSelector,
    MarketRegime,
)


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def selector() -> RegimeAwareSelector:
    return RegimeAwareSelector(lookback_days=60)


@pytest.fixture
def n_days() -> int:
    return 200


def _make_ohlcv(close_series: np.ndarray, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """从收盘价序列构造 OHLCV DataFrame。"""
    n = len(close_series)
    return pd.DataFrame({
        "open": close_series * (1 + np.random.randn(n) * 0.002),
        "high": close_series * (1 + np.abs(np.random.randn(n)) * 0.005),
        "low": close_series * (1 - np.abs(np.random.randn(n)) * 0.005),
        "close": close_series,
        "volume": np.random.randint(800, 1200, n).astype(float),
    }, index=dates)


# ─── 1. detect: bull ─────────────────────────────────────

def test_detect_bull_trend(selector: RegimeAwareSelector) -> None:
    """趋势明确向上 → regime='bull'。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    assert result["regime"] == "bull", f"预期 bull，实际 {result['regime']}"


# ─── 2. detect: bear ─────────────────────────────────────

def test_detect_bear_trend(selector: RegimeAwareSelector) -> None:
    """趋势明确向下 → regime='bear'。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 - 0.5)
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    assert result["regime"] == "bear", f"预期 bear，实际 {result['regime']}"


# ─── 3. detect: oscillate ────────────────────────────────

def test_detect_oscillate(selector: RegimeAwareSelector) -> None:
    """水平震荡，波动适中 → regime='oscillate'。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 2.0  # 无趋势，中等波动
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    assert result["regime"] == "oscillate", f"预期 oscillate，实际 {result['regime']}"


# ─── 4. detect: high_vol ─────────────────────────────────

def test_detect_high_vol(selector: RegimeAwareSelector) -> None:
    """大幅震荡、无明显趋势 → regime='high_vol'。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    # 围绕 100 大幅震荡（无趋势）
    close = 100 + np.sin(np.linspace(0, 8 * np.pi, n)) * 8
    ohlcv = _make_ohlcv(close, dates)
    # 人为放大高低价差确保高波
    ohlcv["high"] = close + np.abs(np.random.randn(n)) * 3.0
    ohlcv["low"] = close - np.abs(np.random.randn(n)) * 3.0

    result = selector.detect(ohlcv)
    # 无趋势 → 进入 vol 判定
    assert result["regime"] == "high_vol", f"预期 high_vol，实际 {result['regime']}"
    assert result["features"]["volatility"] > 0.03


# ─── 5. detect: low_vol ──────────────────────────────────

def test_detect_low_vol(selector: RegimeAwareSelector) -> None:
    """价格近乎恒定 → regime='low_vol'。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.random.randn(n) * 0.2  # 微小波动
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    assert result["regime"] == "low_vol", f"预期 low_vol，实际 {result['regime']}"


# ─── 6. detect: empty DataFrame ──────────────────────────

def test_detect_empty_df(selector: RegimeAwareSelector) -> None:
    """空 DataFrame → regime='oscillate', confidence=0。"""
    empty = pd.DataFrame()
    result = selector.detect(empty)
    assert result["regime"] == "oscillate"
    assert result["confidence"] == 0.0


# ─── 7. detect: NaN 值 ───────────────────────────────────

def test_detect_nan_values(selector: RegimeAwareSelector) -> None:
    """数据含 NaN → 正常检测，不抛异常。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
    ohlcv = _make_ohlcv(close, dates)
    # 在 close 中间插入 NaN
    ohlcv.loc[ohlcv.index[50:55], "close"] = float("nan")
    ohlcv.loc[ohlcv.index[100:103], "high"] = float("nan")
    ohlcv.loc[ohlcv.index[150:152], "volume"] = float("nan")

    result = selector.detect(ohlcv)
    assert isinstance(result["regime"], str)
    assert 0 <= result["confidence"] <= 1


# ─── 8. detect: 常量价格 ─────────────────────────────────

def test_detect_constant_prices(selector: RegimeAwareSelector) -> None:
    """收盘价恒定不变 → low_vol。"""
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = np.full(n, 100.0)
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    assert result["regime"] == "low_vol", f"预期 low_vol，实际 {result['regime']}"


# ─── 9. confidence 范围 ──────────────────────────────────

def test_confidence_range(selector: RegimeAwareSelector) -> None:
    """所有检测结果的 confidence 都在 0~1 之间。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")

    scenarios = [
        100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5),   # bull
        100 + np.cumsum(np.random.randn(n) * 0.3 - 0.5),   # bear
        100 + np.random.randn(n) * 2.0,                     # oscillate
        100 + np.sin(np.linspace(0, 8 * np.pi, n)) * 8,     # high_vol
        100 + np.random.randn(n) * 0.2,                     # low_vol
    ]
    for prices in scenarios:
        ohlcv = _make_ohlcv(prices, dates)
        result = selector.detect(ohlcv)
        assert 0 <= result["confidence"] <= 1, (
            f"confidence={result['confidence']} 超出 [0,1]"
        )


# ─── 10. features 包含预期键 ─────────────────────────────

def test_features_expected_keys(selector: RegimeAwareSelector) -> None:
    """features dict 包含所有预期字段。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3)
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    expected_keys = {"trend_strength", "volatility", "volume_ratio", "breadth"}
    assert expected_keys.issubset(result["features"].keys()), (
        f"缺失键: {expected_keys - set(result['features'].keys())}"
    )


# ─── 11. profile_factor 存储与读取 ───────────────────────

def test_profile_factor_store_retrieve(selector: RegimeAwareSelector) -> None:
    """存储后可通过内部 _profiles 读取。"""
    history = {
        "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
    }
    selector.profile_factor("fct_001", history)
    assert "fct_001" in selector._profiles
    stored = selector._profiles["fct_001"]
    assert stored["factor_id"] == "fct_001"
    assert stored["regime_performance"]["bull"]["ic_mean"] == 0.05


# ─── 12. select_factors 按制度筛选 ───────────────────────

def test_select_factors_filters_by_regime(selector: RegimeAwareSelector) -> None:
    """有 profile 的因子：IC>0 才保留，IC<=0 被过滤。"""
    selector.profile_factor("fct_good", {
        "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
    })
    selector.profile_factor("fct_bad", {
        "bull": {"ic_mean": -0.03, "sharpe": -0.5, "n_windows": 10},
    })

    regime = MarketRegime(
        regime="bull", confidence=0.9, detected_at="now", features={},
    )
    pool = [
        {"factor_id": "fct_good", "name": "good"},
        {"factor_id": "fct_bad", "name": "bad"},
    ]
    result = selector.select_factors(regime, pool)
    fids = [f["factor_id"] for f in result]
    assert "fct_good" in fids, "好因子应被保留"
    assert "fct_bad" not in fids, "差因子应被过滤"


# ─── 13. select_factors 无 profile 保留 ──────────────────

def test_select_factors_no_profile_kept(selector: RegimeAwareSelector) -> None:
    """无 profile 数据的因子默认保留。"""
    regime = MarketRegime(
        regime="bull", confidence=0.9, detected_at="now", features={},
    )
    pool = [{"factor_id": "fct_new", "name": "new"}]
    result = selector.select_factors(regime, pool)
    assert len(result) == 1
    assert result[0]["factor_id"] == "fct_new"


# ─── 14. select_factors 空池 ─────────────────────────────

def test_select_factors_empty_pool(selector: RegimeAwareSelector) -> None:
    """空 elite_pool → 返回空列表。"""
    regime = MarketRegime(
        regime="bull", confidence=0.9, detected_at="now", features={},
    )
    result = selector.select_factors(regime, [])
    assert result == []


# ─── 15. regime_report 非空 ──────────────────────────────

def test_regime_report_non_empty(selector: RegimeAwareSelector) -> None:
    """regime_report 返回包含制度信息的非空字符串。"""
    selector.profile_factor("fct_001", {
        "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
    })
    report = selector.regime_report()
    assert isinstance(report, str)
    assert len(report) > 0
    assert "fct_001" in report
    assert "bull" in report


# ─── 16. bull 高置信度 ───────────────────────────────────

def test_detect_bull_high_confidence(selector: RegimeAwareSelector) -> None:
    """强上涨趋势 → 置信度高。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.2 + 0.8)  # 极强趋势
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    assert result["regime"] == "bull"
    assert result["confidence"] >= 0.5


# ─── 17. bear 带 features ────────────────────────────────

def test_detect_bear_with_features(selector: RegimeAwareSelector) -> None:
    """下跌趋势的 features 包含正确字段。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 - 0.5)
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    assert result["features"]["trend_strength"] < 0


# ─── 18. profile_factor 覆盖 ─────────────────────────────

def test_profile_factor_overwrite(selector: RegimeAwareSelector) -> None:
    """对同一 factor_id 多次 profile，后覆盖前。"""
    selector.profile_factor("fct_001", {
        "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
    })
    selector.profile_factor("fct_001", {
        "bear": {"ic_mean": 0.03, "sharpe": 0.8, "n_windows": 8},
    })
    stored = selector._profiles["fct_001"]
    # 覆盖后只有 bear 数据
    assert "bear" in stored["regime_performance"]
    assert "bull" not in stored["regime_performance"]


# ─── 19. 多制度 profile ──────────────────────────────────

def test_profile_factor_multiple_regimes(selector: RegimeAwareSelector) -> None:
    """一个因子可在多个 regime 下有表现记录。"""
    selector.profile_factor("fct_001", {
        "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
        "bear": {"ic_mean": 0.02, "sharpe": 0.6, "n_windows": 6},
        "oscillate": {"ic_mean": -0.01, "sharpe": -0.2, "n_windows": 4},
    })
    perfs = selector._profiles["fct_001"]["regime_performance"]
    assert set(perfs.keys()) == {"bull", "bear", "oscillate"}


# ─── 20. select_factors 混合场景 ─────────────────────────

def test_select_factors_mixed(selector: RegimeAwareSelector) -> None:
    """部分因子有 profile，部分无 → 有 profile 且 IC>0 的保留，无 profile 的保留。"""
    selector.profile_factor("fct_profiled_good", {
        "bull": {"ic_mean": 0.04, "sharpe": 1.0, "n_windows": 5},
    })
    selector.profile_factor("fct_profiled_bad", {
        "bull": {"ic_mean": -0.02, "sharpe": -0.3, "n_windows": 5},
    })
    # fct_unprofiled 无 profile

    regime = MarketRegime(
        regime="bull", confidence=0.9, detected_at="now", features={},
    )
    pool = [
        {"factor_id": "fct_profiled_good"},
        {"factor_id": "fct_profiled_bad"},
        {"factor_id": "fct_unprofiled"},
    ]
    result = selector.select_factors(regime, pool)
    fids = {f["factor_id"] for f in result}
    assert "fct_profiled_good" in fids
    assert "fct_profiled_bad" not in fids
    assert "fct_unprofiled" in fids


# ─── 21. regime_report 空数据 ────────────────────────────

def test_regime_report_empty(selector: RegimeAwareSelector) -> None:
    """无 profile 数据时报告包含提示信息。"""
    report = selector.regime_report()
    assert "无因子表现数据" in report


# ─── 22. 自定义 lookback ─────────────────────────────────

def test_detect_custom_lookback() -> None:
    """自定义 lookback_days 不影响检测结果类型。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
    ohlcv = _make_ohlcv(close, dates)

    s = RegimeAwareSelector(lookback_days=30)
    result = s.detect(ohlcv)
    assert isinstance(result["regime"], str)
    assert 0 <= result["confidence"] <= 1


# ─── 23. detect: 短数据 ──────────────────────────────────

def test_detect_short_data(selector: RegimeAwareSelector) -> None:
    """不足 20 行数据 → regime='oscillate', confidence=0。"""
    dates = pd.date_range("2024-01-01", periods=10, freq="D")
    close = np.ones(10) * 100
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    assert result["regime"] == "oscillate"
    assert result["confidence"] == 0.0
    assert result["features"] == {}


# ─── 24. select_factors 跨制度差异 ───────────────────────

def test_select_factors_different_regime(selector: RegimeAwareSelector) -> None:
    """因子在 regime_A 表现好、regime_B 表现差 → 在 regime_B 下被过滤。"""
    selector.profile_factor("fct_001", {
        "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
        "bear": {"ic_mean": -0.03, "sharpe": -0.5, "n_windows": 8},
    })

    pool = [{"factor_id": "fct_001"}]

    bull_regime = MarketRegime(
        regime="bull", confidence=0.9, detected_at="now", features={},
    )
    bear_regime = MarketRegime(
        regime="bear", confidence=0.8, detected_at="now", features={},
    )

    bull_result = selector.select_factors(bull_regime, pool)
    bear_result = selector.select_factors(bear_regime, pool)

    assert len(bull_result) == 1, "bull 下应保留"
    assert len(bear_result) == 0, "bear 下应过滤"


# ─── 25. select_factors 使用 sharpe 阈值 ─────────────────

def test_select_factors_sharpe_threshold(selector: RegimeAwareSelector) -> None:
    """IC=0 但 sharpe>0 → 应保留。"""
    selector.profile_factor("fct_sharpe_only", {
        "bull": {"ic_mean": 0.0, "sharpe": 0.5, "n_windows": 5},
    })
    selector.profile_factor("fct_both_zero", {
        "bull": {"ic_mean": 0.0, "sharpe": 0.0, "n_windows": 5},
    })

    regime = MarketRegime(
        regime="bull", confidence=0.9, detected_at="now", features={},
    )
    pool = [
        {"factor_id": "fct_sharpe_only"},
        {"factor_id": "fct_both_zero"},
    ]
    result = selector.select_factors(regime, pool)
    fids = {f["factor_id"] for f in result}
    assert "fct_sharpe_only" in fids, "sharpe>0 应保留"
    assert "fct_both_zero" not in fids, "IC=0 且 sharpe=0 应过滤"
