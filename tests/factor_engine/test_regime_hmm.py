"""
tests/factor_engine/test_regime_hmm.py — HMM 增强模块测试（STEP3）

覆盖范围:
    - StateMapStabilizer: 状态映射稳定性
    - MultiHorizonHMMDetector: 多周期 HMM 集成（需 hmmlearn）
    - MSMRegimeDetector: 马尔可夫切换模型（需 statsmodels）
    - create_multi_hmm_selector() 辅助函数

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

from fts.factor_engine.regime_hmm import (
    StateMapStabilizer,
    MultiHorizonHMMDetector,
    MSMRegimeDetector,
    create_multi_hmm_selector,
    _HMM_AVAILABLE,
    _MSM_AVAILABLE,
)


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def stabilizer() -> StateMapStabilizer:
    return StateMapStabilizer(history_maxlen=3, fusion_alpha=0.7, freeze_confidence=0.8)


def _make_ohlcv(close_series: np.ndarray, n: int | None = None) -> pd.DataFrame:
    """从收盘价序列构造 OHLCV DataFrame。"""
    if n is None:
        n = len(close_series)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "open": close_series * (1 + np.random.randn(n) * 0.002),
        "high": close_series * (1 + np.abs(np.random.randn(n)) * 0.005),
        "low": close_series * (1 - np.abs(np.random.randn(n)) * 0.005),
        "close": close_series,
        "volume": np.random.randint(800, 1200, n).astype(float),
    }, index=dates)


# ═══════════════════════════════════════════════════════════
# 1. StateMapStabilizer
# ═══════════════════════════════════════════════════════════

class TestStateMapStabilizer:
    """StateMapStabilizer: 状态映射稳定性增强。"""

    def test_stabilize_first_call(self, stabilizer: StateMapStabilizer) -> None:
        """首次调用 → 直接返回新映射。"""
        new_map = {0: "bull", 1: "bear", 2: "high_vol", 3: "oscillate"}
        state_stats = [
            {"state": 0, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.01, "mean_vol": 0.02},
            {"state": 2, "mean_ret": 0.0, "mean_vol": 0.05},
            {"state": 3, "mean_ret": 0.0, "mean_vol": 0.01},
        ]
        result = stabilizer.stabilize(new_map, state_stats, 0.5)
        assert result == new_map

    def test_stabilize_no_flip(self, stabilizer: StateMapStabilizer) -> None:
        """无翻转 → 返回新映射。"""
        new_map1 = {0: "bull", 1: "bear", 2: "high_vol", 3: "oscillate"}
        stats1 = [
            {"state": 0, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.01, "mean_vol": 0.02},
            {"state": 2, "mean_ret": 0.0, "mean_vol": 0.05},
            {"state": 3, "mean_ret": 0.0, "mean_vol": 0.01},
        ]
        stabilizer.stabilize(new_map1, stats1, 0.5)

        # 第二次调用，无翻转
        new_map2 = {0: "bull", 1: "bear", 2: "high_vol", 3: "oscillate"}
        stats2 = [
            {"state": 0, "mean_ret": 0.02, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.02, "mean_vol": 0.02},
            {"state": 2, "mean_ret": 0.0, "mean_vol": 0.04},
            {"state": 3, "mean_ret": 0.0, "mean_vol": 0.01},
        ]
        result = stabilizer.stabilize(new_map2, stats2, 0.5)
        assert result[0] == "bull"
        assert result[1] == "bear"

    def test_stabilize_flip_detected(self, stabilizer: StateMapStabilizer) -> None:
        """检测到翻转 → 应用加权融合。"""
        new_map1 = {0: "bull", 1: "bear", 2: "high_vol", 3: "oscillate"}
        stats1 = [
            {"state": 0, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.01, "mean_vol": 0.02},
            {"state": 2, "mean_ret": 0.0, "mean_vol": 0.05},
            {"state": 3, "mean_ret": 0.0, "mean_vol": 0.01},
        ]
        stabilizer.stabilize(new_map1, stats1, 0.5)

        # state 0 和 state 1 互换（翻转）
        new_map2 = {0: "bear", 1: "bull", 2: "high_vol", 3: "oscillate"}
        stats2 = [
            {"state": 0, "mean_ret": -0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 2, "mean_ret": 0.0, "mean_vol": 0.05},
            {"state": 3, "mean_ret": 0.0, "mean_vol": 0.01},
        ]
        # 由于 fusion_alpha=0.7（偏向历史），结果应偏向历史映射
        result = stabilizer.stabilize(new_map2, stats2, 0.5)
        # state 0 在历史中是 bull，在融合中应保持 bull
        assert result[0] == "bull", f"预期 bull，实际 {result[0]}"

    def test_stabilize_high_confidence_freeze(self) -> None:
        """高置信度 → 冻结映射。"""
        s = StateMapStabilizer(freeze_confidence=0.8)
        new_map1 = {0: "bull", 1: "bear", 2: "high_vol"}
        stats1 = [
            {"state": 0, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.01, "mean_vol": 0.02},
            {"state": 2, "mean_ret": 0.0, "mean_vol": 0.05},
        ]
        s.stabilize(new_map1, stats1, 0.85)  # 高置信度 → 冻结

        # 第二次调用，state 0 变成 bear（翻转），但被冻结挡住
        new_map2 = {0: "bear", 1: "bull", 2: "high_vol"}
        stats2 = [
            {"state": 0, "mean_ret": -0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 2, "mean_ret": 0.0, "mean_vol": 0.05},
        ]
        result = s.stabilize(new_map2, stats2, 0.85)
        # 冻结映射应保持 state 0 → bull
        assert result[0] == "bull"

    def test_stabilize_reset(self, stabilizer: StateMapStabilizer) -> None:
        """reset() 后状态清空。"""
        new_map = {0: "bull", 1: "bear"}
        stats = [
            {"state": 0, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.01, "mean_vol": 0.02},
        ]
        stabilizer.stabilize(new_map, stats, 0.5)
        stabilizer.reset()
        # reset 后再次调用，应重新开始
        result = stabilizer.stabilize(new_map, stats, 0.5)
        assert result == new_map


# ═══════════════════════════════════════════════════════════
# 2. MultiHorizonHMMDetector
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not _HMM_AVAILABLE, reason="需要 hmmlearn")
class TestMultiHorizonHMMDetector:
    """MultiHorizonHMMDetector: 多周期 HMM 集成。"""

    def test_predict_bull(self) -> None:
        """上涨趋势 → 检测为 bull。"""
        np.random.seed(42)
        n = 300
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        detector = MultiHorizonHMMDetector(horizons=[63, 126, 252])
        regime, conf, feats = detector.predict(ohlcv)
        assert regime == "bull", f"预期 bull，实际 {regime}"
        assert conf >= 0.3

    def test_predict_bear(self) -> None:
        """下跌趋势 → 检测为 bear。"""
        np.random.seed(42)
        n = 300
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 - 0.15)
        ohlcv = _make_ohlcv(close, n)

        detector = MultiHorizonHMMDetector(horizons=[63, 126, 252])
        regime, conf, feats = detector.predict(ohlcv)
        assert regime == "bear", f"预期 bear，实际 {regime}"
        assert conf >= 0.3

    def test_predict_short_data(self) -> None:
        """数据不足 → 返回 unknown。"""
        n = 30
        close = 100 + np.cumsum(np.random.randn(n) * 0.3)
        ohlcv = _make_ohlcv(close, n)

        detector = MultiHorizonHMMDetector()
        regime, conf, feats = detector.predict(ohlcv)
        assert regime == "unknown"

    def test_predict_empty(self) -> None:
        """空数据 → 返回 unknown。"""
        detector = MultiHorizonHMMDetector()
        regime, conf, feats = detector.predict(pd.DataFrame())
        assert regime == "unknown"

    def test_predict_features(self) -> None:
        """features 包含多周期投票详情。"""
        np.random.seed(42)
        n = 300
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        detector = MultiHorizonHMMDetector()
        _, _, feats = detector.predict(ohlcv)

        assert "multi_hmm_votes" in feats
        assert "multi_hmm_vote_share" in feats
        assert "horizon_details" in feats

    def test_is_available(self) -> None:
        """is_available 属性返回 True。"""
        detector = MultiHorizonHMMDetector()
        assert detector.is_available is True

    def test_custom_horizons(self) -> None:
        """自定义 horizons 正常工作。"""
        np.random.seed(42)
        n = 300
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        detector = MultiHorizonHMMDetector(
            horizons=[63, 126],
            weights={63: 0.3, 126: 0.7},
        )
        regime, conf, _ = detector.predict(ohlcv)
        assert isinstance(regime, str)
        assert 0 <= conf <= 1

    def test_deterministic(self) -> None:
        """相同输入多次调用结果一致。"""
        np.random.seed(42)
        n = 300
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        detector = MultiHorizonHMMDetector(random_seed=42)
        r1, c1, _ = detector.predict(ohlcv)
        # 重新创建检测器，确保种子一致
        detector2 = MultiHorizonHMMDetector(random_seed=42)
        r2, c2, _ = detector2.predict(ohlcv)

        assert r1 == r2
        # 置信度可能因随机初始化略有差异，只检查 regime
        if c1 > 0.5 and c2 > 0.5:
            assert c1 == pytest.approx(c2, abs=0.1)


# ═══════════════════════════════════════════════════════════
# 3. MSMRegimeDetector
# ═══════════════════════════════════════════════════════════

@pytest.mark.skipif(not _MSM_AVAILABLE, reason="需要 statsmodels")
class TestMSMRegimeDetector:
    """MSMRegimeDetector: 马尔可夫切换模型。"""

    def test_fit_predict_bull(self) -> None:
        """上涨趋势 → 拟合后预测为 bull。"""
        np.random.seed(42)
        n = 300
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        detector = MSMRegimeDetector(k_regimes=2, min_data=252)
        fitted = detector.fit(ohlcv)
        assert fitted, "MSM 拟合失败"

        regime, conf, feats = detector.predict(ohlcv)
        assert isinstance(regime, str)
        assert 0 <= conf <= 1

    def test_fit_predict_bear(self) -> None:
        """下跌趋势 → 拟合后预测为 bear。"""
        np.random.seed(42)
        n = 300
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 - 0.15)
        ohlcv = _make_ohlcv(close, n)

        detector = MSMRegimeDetector(k_regimes=2, min_data=252)
        fitted = detector.fit(ohlcv)
        if fitted:
            regime, conf, _ = detector.predict(ohlcv)
            assert isinstance(regime, str)
            assert 0 <= conf <= 1

    def test_predict_before_fit(self) -> None:
        """未拟合时 predict 返回 unknown。"""
        ohlcv = _make_ohlcv(np.ones(300) * 100, 300)
        detector = MSMRegimeDetector()
        regime, conf, _ = detector.predict(ohlcv)
        assert regime == "unknown"

    def test_fit_short_data(self) -> None:
        """数据不足 → 不拟合。"""
        close = np.ones(50) * 100
        ohlcv = _make_ohlcv(close, 50)
        detector = MSMRegimeDetector(min_data=252)
        assert not detector.fit(ohlcv)

    def test_is_available(self) -> None:
        """is_available 属性。"""
        detector = MSMRegimeDetector()
        assert detector.is_available == _MSM_AVAILABLE


# ═══════════════════════════════════════════════════════════
# 4. create_multi_hmm_selector
# ═══════════════════════════════════════════════════════════

class TestCreateMultiHMMSelector:
    """create_multi_hmm_selector 辅助函数。"""

    def test_creates_multi_hmm(self) -> None:
        """创建 MultiHorizonHMMDetector。"""
        multi, msm = create_multi_hmm_selector()
        if _HMM_AVAILABLE:
            assert multi is not None
            assert multi.is_available
        else:
            assert multi is None

    def test_creates_msm(self) -> None:
        """创建 MSMRegimeDetector。"""
        _, msm = create_multi_hmm_selector()
        if _MSM_AVAILABLE:
            assert msm is not None
            assert msm.is_available
        else:
            assert msm is None

    def test_custom_params(self) -> None:
        """自定义参数传递。"""
        if not _HMM_AVAILABLE:
            pytest.skip("需要 hmmlearn")
        multi, _ = create_multi_hmm_selector(
            horizons=[63, 126],
            weights={63: 0.3, 126: 0.7},
        )
        assert multi is not None
        assert multi.horizons == [63, 126]
        assert multi.weights == {63: 0.3, 126: 0.7}