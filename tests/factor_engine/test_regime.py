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
    HMMRegimeDetector,
    SectorRegimeSelector,
    RegimeTransitionWarner,
    AdaptiveRegimeConfig,
    _detect_by_rule,
    _compute_current_vol_estimate,
    _HMM_AVAILABLE,
    _MSM_AVAILABLE,
)
from fts.factor_engine.regime_hmm import MSMRegimeDetector
from fts.factor_engine import regime as _regime_mod


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def selector() -> RegimeAwareSelector:
    # 规则方法测试禁用 HMM 和 MultiHMM，避免概率模型对短数据产生不稳定结果
    return RegimeAwareSelector(lookback_days=60, use_hmm=False, use_multi_hmm=False)


@pytest.fixture
def n_days() -> int:
    return 200


def _make_ohlcv(close_series: np.ndarray, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """从收盘价序列构造 OHLCV DataFrame。"""
    n = len(close_series)
    return pd.DataFrame(
        {
            "open": close_series * (1 + np.random.randn(n) * 0.002),
            "high": close_series * (1 + np.abs(np.random.randn(n)) * 0.005),
            "low": close_series * (1 - np.abs(np.random.randn(n)) * 0.005),
            "close": close_series,
            "volume": np.random.randint(800, 1200, n).astype(float),
        },
        index=dates,
    )


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
    # 强下跌趋势 + 低噪音，确保波动率不超标
    # drift -0.15/day × 200 = -30，收盘价从 100 降至 ~70，仍为正数
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 - 0.15)
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
    """大幅震荡、无明显趋势 → regime='high_vol'。

    使用两阶段数据：前 150 天低波动，后 130 天高波动，
    使当前 volatility 显著高于历史 80% 分位数。
    """
    np.random.seed(42)
    n = 280
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    # 前 150 天低波动，后 130 天高波动
    low = 100 + np.random.randn(150) * 0.5
    high = 100 + np.random.randn(130) * 8.0
    close = np.concatenate([low, high])
    ohlcv = _make_ohlcv(close, dates)
    # 人为放大高波动段的高低价差
    ohlcv["high"] = close + np.abs(np.random.randn(n)) * 4.0
    ohlcv["low"] = close - np.abs(np.random.randn(n)) * 4.0

    result = selector.detect(ohlcv)
    assert result["regime"] == "high_vol", f"预期 high_vol，实际 {result['regime']}"
    # 检查 volatility_ewma（EWMA 波动率）
    vol_val = result["features"].get("volatility_ewma", 0)
    assert vol_val > 0.03, f"EWMA vol={vol_val} 偏低"


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
    """空 DataFrame → regime='oscillate', confidence=0.5。"""
    empty = pd.DataFrame()
    result = selector.detect(empty)
    assert result["regime"] == "oscillate"
    assert result["confidence"] == 0.5


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
        100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5),  # bull
        100 + np.cumsum(np.random.randn(n) * 0.3 - 0.5),  # bear
        100 + np.random.randn(n) * 2.0,  # oscillate
        100 + np.sin(np.linspace(0, 8 * np.pi, n)) * 8,  # high_vol
        100 + np.random.randn(n) * 0.2,  # low_vol
    ]
    for prices in scenarios:
        ohlcv = _make_ohlcv(prices, dates)
        result = selector.detect(ohlcv)
        assert 0 <= result["confidence"] <= 1, f"confidence={result['confidence']} 超出 [0,1]"


# ─── 10. features 包含预期键 ─────────────────────────────


def test_features_expected_keys(selector: RegimeAwareSelector) -> None:
    """features dict 包含所有预期字段。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3)
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    expected_keys = {
        "trend_short",
        "trend_medium",
        "trend_long",
        "volatility_ewma",
        "volume_ratio",
        "breadth",
        "trend_score",
        "vol_score",
    }
    missing = expected_keys - set(result["features"].keys())
    assert not missing, f"features 缺失键: {missing}"


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
    selector.profile_factor(
        "fct_good",
        {
            "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
        },
    )
    selector.profile_factor(
        "fct_bad",
        {
            "bull": {"ic_mean": -0.03, "sharpe": -0.5, "n_windows": 10},
        },
    )

    regime = MarketRegime(
        regime="bull",
        confidence=0.9,
        detected_at="now",
        features={},
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
        regime="bull",
        confidence=0.9,
        detected_at="now",
        features={},
    )
    pool = [{"factor_id": "fct_new", "name": "new"}]
    result = selector.select_factors(regime, pool)
    assert len(result) == 1
    assert result[0]["factor_id"] == "fct_new"


# ─── 14. select_factors 空池 ─────────────────────────────


def test_select_factors_empty_pool(selector: RegimeAwareSelector) -> None:
    """空 elite_pool → 返回空列表。"""
    regime = MarketRegime(
        regime="bull",
        confidence=0.9,
        detected_at="now",
        features={},
    )
    result = selector.select_factors(regime, [])
    assert result == []


# ─── 15. regime_report 非空 ──────────────────────────────


def test_regime_report_non_empty(selector: RegimeAwareSelector) -> None:
    """regime_report 返回包含制度信息的非空字符串。"""
    selector.profile_factor(
        "fct_001",
        {
            "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
        },
    )
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
    assert result["features"]["trend_score"] < 0


# ─── 18. profile_factor 覆盖 ─────────────────────────────


def test_profile_factor_overwrite(selector: RegimeAwareSelector) -> None:
    """对同一 factor_id 多次 profile，后覆盖前。"""
    selector.profile_factor(
        "fct_001",
        {
            "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
        },
    )
    selector.profile_factor(
        "fct_001",
        {
            "bear": {"ic_mean": 0.03, "sharpe": 0.8, "n_windows": 8},
        },
    )
    stored = selector._profiles["fct_001"]
    # 覆盖后只有 bear 数据
    assert "bear" in stored["regime_performance"]
    assert "bull" not in stored["regime_performance"]


# ─── 19. 多制度 profile ──────────────────────────────────


def test_profile_factor_multiple_regimes(selector: RegimeAwareSelector) -> None:
    """一个因子可在多个 regime 下有表现记录。"""
    selector.profile_factor(
        "fct_001",
        {
            "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
            "bear": {"ic_mean": 0.02, "sharpe": 0.6, "n_windows": 6},
            "oscillate": {"ic_mean": -0.01, "sharpe": -0.2, "n_windows": 4},
        },
    )
    perfs = selector._profiles["fct_001"]["regime_performance"]
    assert set(perfs.keys()) == {"bull", "bear", "oscillate"}


# ─── 20. select_factors 混合场景 ─────────────────────────


def test_select_factors_mixed(selector: RegimeAwareSelector) -> None:
    """部分因子有 profile，部分无 → 有 profile 且 IC>0 的保留，无 profile 的保留。"""
    selector.profile_factor(
        "fct_profiled_good",
        {
            "bull": {"ic_mean": 0.04, "sharpe": 1.0, "n_windows": 5},
        },
    )
    selector.profile_factor(
        "fct_profiled_bad",
        {
            "bull": {"ic_mean": -0.02, "sharpe": -0.3, "n_windows": 5},
        },
    )
    # fct_unprofiled 无 profile

    regime = MarketRegime(
        regime="bull",
        confidence=0.9,
        detected_at="now",
        features={},
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
    """不足 20 行数据 → regime='oscillate', confidence=0.5。"""
    dates = pd.date_range("2024-01-01", periods=10, freq="D")
    close = np.ones(10) * 100
    ohlcv = _make_ohlcv(close, dates)

    result = selector.detect(ohlcv)
    assert result["regime"] == "oscillate"
    assert result["confidence"] == 0.5
    assert result["features"] == {}


# ─── 24. select_factors 跨制度差异 ───────────────────────


def test_select_factors_different_regime(selector: RegimeAwareSelector) -> None:
    """因子在 regime_A 表现好、regime_B 表现差 → 在 regime_B 下被过滤。"""
    selector.profile_factor(
        "fct_001",
        {
            "bull": {"ic_mean": 0.05, "sharpe": 1.2, "n_windows": 10},
            "bear": {"ic_mean": -0.03, "sharpe": -0.5, "n_windows": 8},
        },
    )

    pool = [{"factor_id": "fct_001"}]

    bull_regime = MarketRegime(
        regime="bull",
        confidence=0.9,
        detected_at="now",
        features={},
    )
    bear_regime = MarketRegime(
        regime="bear",
        confidence=0.8,
        detected_at="now",
        features={},
    )

    bull_result = selector.select_factors(bull_regime, pool)
    bear_result = selector.select_factors(bear_regime, pool)

    assert len(bull_result) == 1, "bull 下应保留"
    assert len(bear_result) == 0, "bear 下应过滤"


# ─── 25. select_factors 使用 sharpe 阈值 ─────────────────


def test_select_factors_sharpe_threshold(selector: RegimeAwareSelector) -> None:
    """IC=0 但 sharpe>0 → 应保留。"""
    selector.profile_factor(
        "fct_sharpe_only",
        {
            "bull": {"ic_mean": 0.0, "sharpe": 0.5, "n_windows": 5},
        },
    )
    selector.profile_factor(
        "fct_both_zero",
        {
            "bull": {"ic_mean": 0.0, "sharpe": 0.0, "n_windows": 5},
        },
    )

    regime = MarketRegime(
        regime="bull",
        confidence=0.9,
        detected_at="now",
        features={},
    )
    pool = [
        {"factor_id": "fct_sharpe_only"},
        {"factor_id": "fct_both_zero"},
    ]
    result = selector.select_factors(regime, pool)
    fids = {f["factor_id"] for f in result}
    assert "fct_sharpe_only" in fids, "sharpe>0 应保留"
    assert "fct_both_zero" not in fids, "IC=0 且 sharpe=0 应过滤"


# ═══════════════════════════════════════════════════════════
# 26. HMMRegimeDetector
# ═══════════════════════════════════════════════════════════


@pytest.mark.skipif(not _HMM_AVAILABLE, reason="需要 hmmlearn")
class TestHMMRegimeDetector:
    """HMMRegimeDetector: 单周期 HMM 检测器（v2.1 扩展特征）。"""

    def test_fit_predict_bull(self) -> None:
        """上涨趋势 → 训练成功并预测有效。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        det = HMMRegimeDetector(n_states=3, lookback=100, min_data=50, random_seed=42)
        assert det.fit(ohlcv) is True
        regime, conf, feats = det.predict(ohlcv)
        assert isinstance(regime, str)
        assert 0 <= conf <= 1
        assert isinstance(feats, dict)

    def test_fit_short_data(self) -> None:
        """数据不足 min_data → 不训练。"""
        dates = pd.date_range("2024-01-01", periods=40, freq="D")
        ohlcv = _make_ohlcv(np.full(40, 100.0), dates)
        det = HMMRegimeDetector(min_data=50)
        assert det.fit(ohlcv) is False

    def test_fit_rets_short(self) -> None:
        """len(close)==min_data → 收益率序列少一个 → 不训练。"""
        np.random.seed(42)
        n = 50
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3)
        ohlcv = _make_ohlcv(close, dates)
        det = HMMRegimeDetector(min_data=50, lookback=30)
        assert det.fit(ohlcv) is False

    def test_predict_before_fit(self) -> None:
        """未训练时 predict → unknown。"""
        dates = pd.date_range("2024-01-01", periods=100, freq="D")
        ohlcv = _make_ohlcv(np.full(100, 100.0), dates)
        det = HMMRegimeDetector(min_data=50)
        regime, conf, feats = det.predict(ohlcv)
        assert regime == "unknown"
        assert conf == 0.0
        assert feats == {}

    def test_predict_short_close(self) -> None:
        """训练后数据不足 20 行 → unknown。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)
        det = HMMRegimeDetector(n_states=3, lookback=100, min_data=50, random_seed=42)
        assert det.fit(ohlcv) is True

        short_dates = pd.date_range("2024-01-01", periods=15, freq="D")
        short = _make_ohlcv(np.full(15, 100.0), short_dates)
        regime, conf, _ = det.predict(short)
        assert regime == "unknown"
        assert conf == 0.0

    def test_maybe_refit_flow(self) -> None:
        """maybe_refit: 初次训练 / 周期 refit / 无需 refit。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        det = HMMRegimeDetector(
            n_states=3,
            lookback=100,
            refit_interval=2,
            min_data=50,
            random_seed=42,
        )
        assert det.maybe_refit(ohlcv) is True  # 初次训练
        assert det._call_count == 1
        assert det.maybe_refit(ohlcv) is True  # 2%2==0 且长度增加 → refit
        assert det._call_count == 2
        assert det.maybe_refit(ohlcv) is True  # 3%2==1 → 不 refit
        assert det._call_count == 3

    def test_infer_state_map_model_none(self) -> None:
        """model 为 None → 直接返回。"""
        det = HMMRegimeDetector()
        det._infer_state_map(np.zeros((10, 2)))  # 无异常
        assert det._state_map == {}

    def test_infer_state_map_empty_states(self) -> None:
        """部分状态无样本（mask 为空）→ 仍完成映射。"""
        det = HMMRegimeDetector(n_states=4)
        features = np.random.randn(100, 2)

        class _FakeModel:
            def predict(self, f: np.ndarray) -> np.ndarray:
                return np.zeros(len(f), dtype=int)  # 全部归为 state 0

        fake = _FakeModel()
        fake.means_ = np.zeros((4, 2))
        det._model = fake
        det._infer_state_map(features)
        assert len(det._state_map) == 4

    def test_infer_state_map_four_states(self) -> None:
        """4 状态均有样本 → high_vol / oscillate 分配完整。"""
        det = HMMRegimeDetector(n_states=4)
        features = np.random.randn(100, 2)

        class _FakeModel:
            def predict(self, f: np.ndarray) -> np.ndarray:
                return np.arange(len(f)) % 4

        fake = _FakeModel()
        fake.means_ = np.zeros((4, 2))
        det._model = fake
        det._infer_state_map(features)
        assert len(det._state_map) == 4

    def test_fit_hmm_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GaussianHMM 拟合抛异常 → fit 返回 False。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        class _FailingHMM:
            def __init__(self, *a: object, **k: object) -> None:
                raise RuntimeError("boom")

        monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _FailingHMM)
        det = HMMRegimeDetector(n_states=3, min_data=50, random_seed=42)
        assert det.fit(ohlcv) is False
        assert det._is_fitted is False

    def test_predict_model_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """预测时 model.predict 抛异常 → unknown。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        det = HMMRegimeDetector(n_states=3, lookback=100, min_data=50, random_seed=42)
        assert det.fit(ohlcv) is True

        def _boom(self: object, f: np.ndarray) -> np.ndarray:
            raise RuntimeError("predict failed")

        monkeypatch.setattr(det._model, "predict", _boom)
        regime, conf, feats = det.predict(ohlcv)
        assert regime == "unknown"
        assert conf == 0.0
        assert feats == {}


# ═══════════════════════════════════════════════════════════
# 27. _detect_by_rule 直接调用（边界分支）
# ═══════════════════════════════════════════════════════════


class TestDetectByRule:
    """_detect_by_rule 规则检测边界分支。"""

    def test_none_input(self) -> None:
        """ohlcv=None → fallback。"""
        res = _detect_by_rule(None, None)
        assert res["regime"] == "oscillate"
        assert res["confidence"] == 0.5
        assert res["method"] == "fallback"

    def test_short_close(self) -> None:
        """DataFrame ≥ 20 行但 close 有效值 < 20 → fallback。"""
        dates = pd.date_range("2024-01-01", periods=25, freq="D")
        close = np.full(25, 100.0)
        close[5:] = np.nan  # 仅前 5 个有效
        ohlcv = _make_ohlcv(close, dates)
        res = _detect_by_rule(ohlcv, None)
        assert res["method"] == "fallback"
        assert res["confidence"] == 0.5

    def test_vol_history_short(self) -> None:
        """波动率历史 ≤ 20 → 使用绝对阈值（0.4 / 0.1）。"""
        np.random.seed(42)
        n = 25
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3)
        ohlcv = _make_ohlcv(close, dates)
        res = _detect_by_rule(ohlcv, None)
        assert res["features"]["vol_high_threshold"] == pytest.approx(0.4, abs=1e-6)
        assert res["features"]["vol_low_threshold"] == pytest.approx(0.1, abs=1e-6)

    def test_smooth_same_regime(self) -> None:
        """prev 同 regime 高置信度 → 置信度平滑提升。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)
        prev = MarketRegime(regime="bull", confidence=0.9, detected_at="t", features={})
        res = _detect_by_rule(ohlcv, prev)
        assert res["regime"] == "bull"
        assert res["confidence"] >= 0.63  # 0.9 * _REGIME_PERSISTENCE_FACTOR

    def test_current_vol_estimate_exception(self) -> None:
        """异常输入 → 返回默认 0.15。"""
        assert _compute_current_vol_estimate(None) == 0.15


# ═══════════════════════════════════════════════════════════
# 28. SectorRegimeSelector
# ═══════════════════════════════════════════════════════════


class TestSectorRegimeSelector:
    """SectorRegimeSelector 产业链级制度检测。"""

    @staticmethod
    def _panel() -> tuple[dict[str, pd.DataFrame], dict[str, list[str]]]:
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        base = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        panel = {
            "A": _make_ohlcv(base, dates),
            "B": _make_ohlcv(base * 1.0, dates),
        }
        return panel, {"S1": ["A", "B"]}

    def test_detect_all(self) -> None:
        """正常面板 → 每个产业链返回制度。"""
        panel, smap = self._panel()
        s = SectorRegimeSelector(lookback_days=60, use_hmm=False)
        res = s.detect_all(panel, smap)
        assert set(res.keys()) == {"S1"}
        assert res["S1"]["regime"] in {"bull", "bear", "oscillate", "high_vol", "low_vol"}
        assert 0 <= res["S1"]["confidence"] <= 1

    def test_detect_all_empty_panel(self) -> None:
        """空面板 → 空结果。"""
        s = SectorRegimeSelector()
        assert s.detect_all({}, {"S1": ["A"]}) == {}

    def test_detect_all_default_map(self) -> None:
        """sector_map=None → 使用默认映射（空面板直接返回）。"""
        s = SectorRegimeSelector()
        assert s.detect_all({}, None) == {}

    def test_detect_all_insufficient_symbols(self) -> None:
        """产业链内品种不足 2 个 → 不出现在结果。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3)
        panel = {"A": _make_ohlcv(close, dates)}
        s = SectorRegimeSelector(use_hmm=False)
        res = s.detect_all(panel, {"S1": ["A"]})
        assert "S1" not in res

    def test_compute_alignment_same_regime(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """品种与产业链制度相同 → 对齐度 = 置信度乘积。"""
        panel, smap = self._panel()
        sector_regimes = {
            "S1": MarketRegime(regime="bull", confidence=0.8, detected_at="t", features={}),
        }

        def _fake_detect(self: object, ohlcv: pd.DataFrame) -> MarketRegime:
            return MarketRegime(regime="bull", confidence=0.5, detected_at="t", features={})

        monkeypatch.setattr(RegimeAwareSelector, "detect", _fake_detect)
        s = SectorRegimeSelector(use_hmm=False)
        al = s.compute_alignment(panel, sector_regimes, smap)
        assert al["A"] == pytest.approx(0.4, abs=1e-4)
        assert al["B"] == pytest.approx(0.4, abs=1e-4)

    def test_compute_alignment_diff_regime(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """品种与产业链制度不同 → 对齐度 = (1-|置信度差|) * 0.5。"""
        panel, smap = self._panel()
        sector_regimes = {
            "S1": MarketRegime(regime="bull", confidence=0.8, detected_at="t", features={}),
        }

        def _fake_detect(self: object, ohlcv: pd.DataFrame) -> MarketRegime:
            return MarketRegime(regime="bear", confidence=0.4, detected_at="t", features={})

        monkeypatch.setattr(RegimeAwareSelector, "detect", _fake_detect)
        s = SectorRegimeSelector(use_hmm=False)
        al = s.compute_alignment(panel, sector_regimes, smap)
        assert al["A"] == pytest.approx(0.3, abs=1e-4)  # (1 - 0.4) * 0.5

    def test_compute_alignment_short_data(self) -> None:
        """品种数据 < 20 行 → 对齐度 0.5。"""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        panel = {"A": _make_ohlcv(np.full(10, 100.0), dates)}
        sector_regimes = {
            "S1": MarketRegime(regime="bull", confidence=0.8, detected_at="t", features={}),
        }
        s = SectorRegimeSelector(use_hmm=False)
        al = s.compute_alignment(panel, sector_regimes, {"S1": ["A"]})
        assert al["A"] == 0.5

    def test_compute_alignment_no_sector_regime(self) -> None:
        """sector_regimes 中无该产业链 → 不产出。"""
        panel, smap = self._panel()
        s = SectorRegimeSelector(use_hmm=False)
        assert s.compute_alignment(panel, {}, smap) == {}

    def test_compute_alignment_default_map(self) -> None:
        """sector_map=None → 使用默认产业链映射。"""
        panel, _ = self._panel()
        s = SectorRegimeSelector(use_hmm=False)
        # 默认映射中的品种不在 panel → 返回空 dict，不抛异常
        assert s.compute_alignment(panel, {}, None) == {}

    def test_build_sector_ohlcv(self) -> None:
        """2 品种 → 合成 OHLCV。"""
        panel, _ = self._panel()
        df = SectorRegimeSelector._build_sector_ohlcv(panel, ["A", "B"])
        assert not df.empty
        assert {"open", "high", "low", "close", "volume"} <= set(df.columns)

    def test_build_sector_ohlcv_one_symbol(self) -> None:
        """仅 1 品种 → 空 DataFrame。"""
        panel, _ = self._panel()
        df = SectorRegimeSelector._build_sector_ohlcv(panel, ["A"])
        assert df.empty

    def test_build_sector_ohlcv_short(self) -> None:
        """合成序列不足 20 行 → 空 DataFrame。"""
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        panel = {
            "A": _make_ohlcv(np.full(10, 100.0), dates),
            "B": _make_ohlcv(np.full(10, 100.0), dates),
        }
        df = SectorRegimeSelector._build_sector_ohlcv(panel, ["A", "B"])
        assert df.empty

    def test_build_sector_ohlcv_missing_close(self) -> None:
        """品种缺 close 列 → 跳过该品种。"""
        dates = pd.date_range("2024-01-01", periods=30, freq="D")
        close = 100 + np.arange(30).astype(float)
        panel = {
            "A": pd.DataFrame({"close": close}, index=dates),
            "B": pd.DataFrame({"high": close}, index=dates),  # 无 close 列
        }
        df = SectorRegimeSelector._build_sector_ohlcv(panel, ["A", "B"])
        assert df.empty


# ═══════════════════════════════════════════════════════════
# 29. RegimeAwareSelector 集成路径与边界
# ═══════════════════════════════════════════════════════════


class TestRegimeAwareSelectorMore:
    """RegimeAwareSelector 集成检测路径。"""

    def test_detect_close_nan_short(self) -> None:
        """≥20 行但 close 有效值 < 20 → fallback。"""
        dates = pd.date_range("2024-01-01", periods=25, freq="D")
        close = np.full(25, 100.0)
        close[5:] = np.nan
        ohlcv = _make_ohlcv(close, dates)
        s = RegimeAwareSelector(use_hmm=False, use_multi_hmm=False)
        res = s.detect(ohlcv)
        assert res["method"] == "fallback"
        assert res["confidence"] == 0.5

    @pytest.mark.skipif(not _HMM_AVAILABLE, reason="需要 hmmlearn")
    def test_detect_multi_hmm_error_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """multi_hmm 预测抛异常 → 回退规则方法。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        s = RegimeAwareSelector(use_hmm=False, use_multi_hmm=True, use_msm=False)
        assert s._multi_hmm is not None

        def _boom(self: object, ohlcv: pd.DataFrame) -> tuple[str, float, dict]:
            raise RuntimeError("predict failed")

        monkeypatch.setattr(s._multi_hmm, "predict", _boom)
        res = s.detect(ohlcv)
        assert res["method"] == "rule"

    @pytest.mark.skipif(not _MSM_AVAILABLE, reason="需要 statsmodels")
    def test_detect_msm_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """use_msm=True 且 MSM 拟合成功 → method='msm'。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        monkeypatch.setattr(MSMRegimeDetector, "fit", lambda self, o: True)
        monkeypatch.setattr(
            MSMRegimeDetector,
            "predict",
            lambda self, o: ("bull", 0.8, {"msm_state": 0}),
        )
        s = RegimeAwareSelector(use_hmm=False, use_multi_hmm=False, use_msm=True)
        res = s.detect(ohlcv)
        assert res["method"] == "msm"
        assert res["regime"] == "bull"
        assert res["features"] == {"msm_state": 0}

    @pytest.mark.skipif(not _MSM_AVAILABLE, reason="需要 statsmodels")
    def test_detect_msm_fit_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MSM 拟合失败 → 回退规则方法。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        monkeypatch.setattr(MSMRegimeDetector, "fit", lambda self, o: False)
        s = RegimeAwareSelector(use_hmm=False, use_multi_hmm=False, use_msm=True)
        res = s.detect(ohlcv)
        assert res["method"] == "rule"

    @pytest.mark.skipif(not _MSM_AVAILABLE, reason="需要 statsmodels")
    def test_detect_msm_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MSM 拟合抛异常 → 回退规则方法。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        def _boom(self: object, o: pd.DataFrame) -> bool:
            raise RuntimeError("msm fit failed")

        monkeypatch.setattr(MSMRegimeDetector, "fit", _boom)
        s = RegimeAwareSelector(use_hmm=False, use_multi_hmm=False, use_msm=True)
        res = s.detect(ohlcv)
        assert res["method"] == "rule"

    @pytest.mark.skipif(not _HMM_AVAILABLE, reason="需要 hmmlearn")
    def test_detect_hmm_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """单周期 HMM 抛异常 → 回退规则方法。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        s = RegimeAwareSelector(use_hmm=True, use_multi_hmm=False)
        assert s._hmm_detector is not None

        def _boom(self: object, o: pd.DataFrame) -> bool:
            raise RuntimeError("hmm refit failed")

        monkeypatch.setattr(s._hmm_detector, "maybe_refit", _boom)
        res = s.detect(ohlcv)
        assert res["method"] == "rule"

    @pytest.mark.skipif(not _HMM_AVAILABLE, reason="需要 hmmlearn")
    def test_detect_hmm_path(self) -> None:
        """单周期 HMM 启用 → method 为 'hmm' 或规则回退。"""
        np.random.seed(42)
        n = 300
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        s = RegimeAwareSelector(use_hmm=True, use_multi_hmm=False)
        res = s.detect(ohlcv)
        assert res["method"] in {"hmm", "rule"}
        assert 0 <= res["confidence"] <= 1

    def test_multi_hmm_constructor_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MultiHorizonHMMDetector 构造失败 → 禁用多周期。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, dates)

        def _raiser(*a: object, **k: object) -> None:
            raise RuntimeError("ctor failed")

        monkeypatch.setattr(_regime_mod, "MultiHorizonHMMDetector", _raiser)
        s = RegimeAwareSelector(use_hmm=False, use_multi_hmm=True)
        assert s._multi_hmm is None
        assert s._use_multi_hmm is False
        res = s.detect(ohlcv)
        assert res["method"] == "rule"

    @pytest.mark.skipif(not _MSM_AVAILABLE, reason="需要 statsmodels")
    def test_msm_constructor_fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MSMRegimeDetector 构造失败 → 禁用 MSM。"""

        def _raiser(*a: object, **k: object) -> None:
            raise RuntimeError("ctor failed")

        monkeypatch.setattr(_regime_mod, "MSMRegimeDetector", _raiser)
        s = RegimeAwareSelector(use_hmm=False, use_multi_hmm=False, use_msm=True)
        assert s._msm is None
        assert s._use_msm is False

    def test_select_factors_profile_missing_regime(
        self,
        selector: RegimeAwareSelector,
    ) -> None:
        """有 profile 但当前 regime 无记录 → 保留。"""
        selector.profile_factor(
            "fct_001",
            {
                "bull": {"ic_mean": 0.05, "sharpe": 1.0, "n_windows": 10},
            },
        )
        regime = MarketRegime(
            regime="bear",
            confidence=0.8,
            detected_at="now",
            features={},
        )
        result = selector.select_factors(regime, [{"factor_id": "fct_001"}])
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════
# 30. RegimeTransitionWarner（P4.2）
# ═══════════════════════════════════════════════════════════


class _FakeLogger:
    """替换模块 logger（产品代码中 logger 未定义，详见报告），用于覆盖预警分支。"""

    def debug(self, *a: object, **k: object) -> None:
        pass

    def info(self, *a: object, **k: object) -> None:
        pass

    def warning(self, *a: object, **k: object) -> None:
        pass


class TestRegimeTransitionWarner:
    """RegimeTransitionWarner 制度迁移预警系统。"""

    def test_evaluate_none(self) -> None:
        """低熵 + 无转移矩阵/特征 → none。"""
        w = RegimeTransitionWarner()
        assert w.evaluate([0.99, 0.01], "bull") == "none"
        assert w.last_alert is None

    def test_evaluate_red_entropy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """均匀概率（高熵）→ red。"""
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        w = RegimeTransitionWarner()
        assert w.evaluate([0.5, 0.5], "bull") == "red"
        assert w.last_alert == "red"

    def test_evaluate_orange_entropy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """3 状态中度不均匀 → orange。"""
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        w = RegimeTransitionWarner()
        assert w.evaluate([0.55, 0.25, 0.2], "bull") == "orange"

    def test_evaluate_yellow_entropy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """4 状态轻度不均匀 → yellow。"""
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        w = RegimeTransitionWarner()
        assert w.evaluate([0.7, 0.1, 0.1, 0.1], "bull") == "yellow"

    def test_evaluate_transition_red(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """转移矩阵对角元下降 50% → red。"""
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        w = RegimeTransitionWarner()
        m1 = np.eye(2)
        w.evaluate([0.99, 0.01], "bull", m1)
        m2 = np.array([[0.5, 0.5], [0.5, 0.5]])
        assert w.evaluate([0.99, 0.01], "bull", m2) == "red"

    def test_evaluate_transition_orange(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """转移矩阵对角元下降 21% → orange。"""
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        w = RegimeTransitionWarner()
        m1 = np.eye(2)
        w.evaluate([0.99, 0.01], "bull", m1)
        m2 = np.array([[0.79, 0.21], [0.21, 0.79]])
        assert w.evaluate([0.99, 0.01], "bull", m2) == "orange"

    def test_evaluate_transition_yellow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """转移矩阵对角元下降 15% → yellow。"""
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        w = RegimeTransitionWarner()
        m1 = np.eye(2)
        w.evaluate([0.99, 0.01], "bull", m1)
        m2 = np.array([[0.85, 0.15], [0.15, 0.85]])
        assert w.evaluate([0.99, 0.01], "bull", m2) == "yellow"

    def test_evaluate_kl_red(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """特征分布大偏移 → red。"""
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        w = RegimeTransitionWarner()
        w.evaluate([0.99, 0.01], "bull", features=np.array([0.0]))
        assert w.evaluate([0.99, 0.01], "bull", features=np.array([10.0])) == "red"

    def test_evaluate_kl_yellow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """特征分布小偏移 → yellow。"""
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        w = RegimeTransitionWarner()
        w.evaluate([0.99, 0.01], "bull", features=np.array([0.0]))
        assert w.evaluate([0.99, 0.01], "bull", features=np.array([0.1])) == "yellow"

    def test_evaluate_feature_ref_update(self) -> None:
        """首次传 features → 设置参考分布，不触发预警。"""
        w = RegimeTransitionWarner()
        assert w.evaluate([0.99, 0.01], "bull", features=np.array([1.0, 2.0])) == "none"
        assert w._ref_feature_mean is not None
        assert w._ref_feature_std is not None

    def test_last_alert_and_reset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """last_alert 属性 + reset 清空状态。"""
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        w = RegimeTransitionWarner()
        w.evaluate([0.5, 0.5], "bull")
        assert w.last_alert == "red"
        w.reset()
        assert w.last_alert is None
        assert w._call_count == 0
        assert w._prev_transition is None
        assert w._ref_feature_mean is None


# ═══════════════════════════════════════════════════════════
# 31. AdaptiveRegimeConfig（P4.3）
# ═══════════════════════════════════════════════════════════


class TestAdaptiveRegimeConfig:
    """AdaptiveRegimeConfig 自适应阈值调整。"""

    def test_default_thresholds(self) -> None:
        """get_thresholds 返回默认阈值。"""
        c = AdaptiveRegimeConfig()
        th = c.get_thresholds()
        assert th["confidence_min"] == 0.3
        assert th["trend_slope_bull"] == 0.0001

    def test_record_accumulates(self) -> None:
        """record 累积历史记录。"""
        c = AdaptiveRegimeConfig(eval_interval=20)
        for _ in range(5):
            c.record("bull", 0.8, 0.01)
        assert len(c._history) == 5
        assert c._eval_count == 5

    def test_reoptimize_not_enough(self) -> None:
        """历史不足 eval_interval → 不重优化。"""
        c = AdaptiveRegimeConfig(eval_interval=3)
        c.record("bull", 0.8, 0.01)
        c.record("bull", 0.8, 0.01)
        th = c.get_thresholds()
        assert th["confidence_min"] == 0.3  # 未触发重优化，保持默认

    def test_reoptimize_insufficient_direct(self) -> None:
        """直接调用 _reoptimize 且历史不足 → 直接返回。"""
        c = AdaptiveRegimeConfig(eval_interval=3)
        c.record("bull", 0.8, 0.01)
        c._reoptimize()  # history=1 < eval_interval=3 → 内部 return
        th = c.get_thresholds()
        assert th["confidence_min"] == 0.3

    def test_reoptimize_after_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """达到 eval_interval → 触发重优化，阈值落入搜索空间。
        （产品代码 _reoptimize 引用未定义 logger，需注入假 logger 覆盖，详见报告）
        """
        monkeypatch.setattr(_regime_mod, "logger", _FakeLogger(), raising=False)
        c = AdaptiveRegimeConfig(eval_interval=3)
        for _ in range(3):
            c.record("bull", 0.9, 0.02)
        th = c.get_thresholds()
        assert th["confidence_min"] in AdaptiveRegimeConfig.PARAM_GRID["confidence_min"]
        assert th["volatility_high"] in AdaptiveRegimeConfig.PARAM_GRID["volatility_high"]

    def test_score_params_empty(self) -> None:
        """空历史 → 0.0。"""
        c = AdaptiveRegimeConfig()
        params = dict(AdaptiveRegimeConfig.DEFAULT_THRESHOLDS)
        assert c._score_params(params, []) == 0.0

    def test_score_params_all_oscillate(self) -> None:
        """全部为空仓制度 → 0.0。"""
        c = AdaptiveRegimeConfig()
        params = dict(AdaptiveRegimeConfig.DEFAULT_THRESHOLDS)
        history = [
            {"regime": "oscillate", "confidence": 0.5, "forward_return": 0.01},
        ] * 5
        assert c._score_params(params, history) == 0.0

    def test_score_params_trades(self) -> None:
        """bull/bear 记录 → 返回有限非零评分。"""
        c = AdaptiveRegimeConfig()
        params = dict(AdaptiveRegimeConfig.DEFAULT_THRESHOLDS)
        history = [
            {"regime": "bull", "confidence": 0.9, "forward_return": 0.02},
            {"regime": "bear", "confidence": 0.8, "forward_return": -0.01},
            {"regime": "oscillate", "confidence": 0.5, "forward_return": 0.0},
        ]
        score = c._score_params(params, history)
        assert isinstance(score, float)
        assert score != 0.0

    def test_reset(self) -> None:
        """reset 清空历史。"""
        c = AdaptiveRegimeConfig()
        c.record("bull", 0.8, 0.01)
        c.reset()
        assert c._history == []
        assert c._eval_count == 0


# ═══════════════════════════════════════════════════════════
# 32. regime_probs 全制度概率分布（28 计划 Task 1）
# ═══════════════════════════════════════════════════════════


def _make_trend_ohlcv(n: int = 300, trend: float = 0.0) -> pd.DataFrame:
    """生成带每日趋势漂移的 OHLCV DataFrame（28-T1 测试辅助）。

    参数:
        n:     交易日数。
        trend: 每日趋势漂移（正=上涨倾向，负=下跌倾向）。

    返回:
        含 open/high/low/close/volume 列的 DataFrame，DatetimeIndex。
    """
    np.random.seed(42)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.3 + trend)
    return _make_ohlcv(close, dates)


def test_market_regime_has_regime_probs_distribution() -> None:
    """规则方法必须输出全制度概率分布（和为 1，覆盖 5 制度）。"""
    ohlcv = _make_trend_ohlcv(n=300, trend=0.001)
    result = _detect_by_rule(ohlcv, prev_regime=None)
    probs = result["regime_probs"]
    assert set(probs.keys()) == {"bull", "bear", "oscillate", "high_vol", "low_vol"}
    assert abs(sum(probs.values()) - 1.0) < 1e-6
    assert all(0.0 <= v <= 1.0 for v in probs.values())
    # 主制度概率应为其置信度的主导项
    assert probs[result["regime"]] > 0.0


def test_fallback_regime_probs() -> None:
    """兜底路径 regime_probs 应为 {oscillate: 1.0}。"""
    result = RegimeAwareSelector().detect(pd.DataFrame())  # 空数据 → fallback
    assert result["regime_probs"] == {"oscillate": 1.0}


def test_detect_promotes_hmm_regime_probs_to_top_level() -> None:
    """HMM 路径 features 内的 regime_probs 必须提升到 MarketRegime 顶层（28 端到端修复）。

    regime blend / 熵标定从顶层 regime_probs 取数；此测试锁定 detect 的统一提升逻辑。
    """

    class _FakeMultiHMM:
        """模拟多周期 HMM：仅返回 features 内含 regime_probs，不触发真实训练。"""

        def predict(self, ohlcv: pd.DataFrame) -> tuple[str, float, dict]:
            return (
                "bull",
                0.9,
                {
                    "regime_probs": {
                        "bull": 0.9,
                        "bear": 0.05,
                        "oscillate": 0.03,
                        "high_vol": 0.01,
                        "low_vol": 0.01,
                    }
                },
            )

    sel = RegimeAwareSelector(use_hmm=False, use_multi_hmm=False)
    sel._use_multi_hmm = True
    sel._multi_hmm = _FakeMultiHMM()  # type: ignore[assignment]
    result = sel.detect(_make_trend_ohlcv(n=300, trend=0.1))
    assert result["method"] == "multi_hmm"
    assert "regime_probs" in result  # 顶层必须携带（28 修复）
    assert abs(sum(result["regime_probs"].values()) - 1.0) < 1e-6
    assert result["regime_probs"]["bull"] > 0.8
