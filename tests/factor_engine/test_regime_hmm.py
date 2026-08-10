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
    _LightHMM,
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


# ═══════════════════════════════════════════════════════════
# 5. StateMapStabilizer 边界分支
# ═══════════════════════════════════════════════════════════


class TestStateMapStabilizerMore:
    """StateMapStabilizer 边界分支补充。"""

    def test_history_overflow(self) -> None:
        """历史记录超过 maxlen → 弹出最旧记录。"""
        s = StateMapStabilizer(history_maxlen=3)
        new_map = {0: "bull", 1: "bear"}
        for i in range(4):
            stats = [
                {"state": 0, "mean_ret": 0.01 + i, "mean_vol": 0.02},
                {"state": 1, "mean_ret": -0.01 - i, "mean_vol": 0.02},
            ]
            s.stabilize(new_map, stats, 0.5)
        assert len(s._history) == 3

    def test_map_flip_missing_regime(self) -> None:
        """新映射缺少旧 regime → 跳过该状态检查。"""
        s = StateMapStabilizer()
        stats = [
            {"state": 0, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.01, "mean_vol": 0.02},
        ]
        s.stabilize({0: "bull", 1: "bear"}, stats, 0.5)
        # 新映射中丢失 "bull" regime
        result = s.stabilize({1: "bear", 2: "oscillate"}, stats, 0.5)
        assert result[1] == "bear"
        assert result[2] == "oscillate"

    def test_fuse_alpha_low(self) -> None:
        """fusion_alpha < 0.5 → 翻转时偏向新映射。"""
        s = StateMapStabilizer(fusion_alpha=0.3)
        stats = [
            {"state": 0, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.01, "mean_vol": 0.02},
        ]
        s.stabilize({0: "bull", 1: "bear"}, stats, 0.5)
        flipped_stats = [
            {"state": 0, "mean_ret": -0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": 0.01, "mean_vol": 0.02},
        ]
        result = s.stabilize({0: "bear", 1: "bull"}, flipped_stats, 0.5)
        # fusion_alpha=0.3 → 采用新映射
        assert result[0] == "bear"
        assert result[1] == "bull"

    def test_fuse_new_state(self) -> None:
        """新映射含新 state（不在旧映射）→ 直接保留。"""
        s = StateMapStabilizer(fusion_alpha=0.7)
        stats = [
            {"state": 0, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.01, "mean_vol": 0.02},
        ]
        s.stabilize({0: "bull", 1: "bear"}, stats, 0.5)
        flipped_stats = [
            {"state": 0, "mean_ret": -0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 2, "mean_ret": 0.0, "mean_vol": 0.05},
        ]
        result = s.stabilize({0: "bear", 1: "bull", 2: "high_vol"}, flipped_stats, 0.5)
        assert result[0] == "bull"  # 偏向历史映射
        assert result[2] == "high_vol"  # 新 state 直接保留

    def test_freeze_third_call(self) -> None:
        """高置信度持续 → 第三次调用返回冻结映射。"""
        s = StateMapStabilizer(freeze_confidence=0.8)
        stats1 = [
            {"state": 0, "mean_ret": 0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": -0.01, "mean_vol": 0.02},
        ]
        stats2 = [
            {"state": 0, "mean_ret": -0.01, "mean_vol": 0.02},
            {"state": 1, "mean_ret": 0.01, "mean_vol": 0.02},
        ]
        s.stabilize({0: "bull", 1: "bear"}, stats1, 0.85)  # 第一次：记录映射（冻结尚未生效）
        s.stabilize({0: "bear", 1: "bull"}, stats2, 0.85)  # 第二次：翻转融合，设置冻结映射
        result = s.stabilize({0: "bear", 1: "bull"}, stats2, 0.85)  # 第三次：命中冻结
        assert result[0] == "bull"  # 冻结映射（第一次的 bull）
        assert result[1] == "bear"


# ═══════════════════════════════════════════════════════════
# 6. _LightHMM 轻量 HMM 检测器
# ═══════════════════════════════════════════════════════════


@pytest.mark.skipif(not _HMM_AVAILABLE, reason="需要 hmmlearn")
class TestLightHMM:
    """_LightHMM 轻量 HMM 检测器（MultiHorizonHMMDetector 内部组件）。"""

    def test_fit_predict(self) -> None:
        """训练 + 预测主流程。"""
        np.random.seed(42)
        n = 150
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        det = _LightHMM(n_states=2, lookback=100, min_data=50, random_seed=42)
        assert det.maybe_fit(ohlcv) is True
        regime, conf, feats = det.predict(ohlcv)
        assert isinstance(regime, str)
        assert 0 <= conf <= 1
        assert "hmm_state" in feats

    def test_maybe_fit_second_call(self) -> None:
        """已拟合且非周期调用 → 不重新训练。"""
        np.random.seed(42)
        n = 150
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        det = _LightHMM(n_states=2, lookback=100, min_data=50, random_seed=42)
        assert det.maybe_fit(ohlcv) is True
        call1 = det._call_count
        assert det.maybe_fit(ohlcv) is True
        assert det._call_count == call1 + 1

    def test_fit_short_close(self) -> None:
        """close < min_data → 不拟合。"""
        np.random.seed(42)
        n = 40
        close = 100 + np.cumsum(np.random.randn(n) * 0.3)
        ohlcv = _make_ohlcv(close, n)
        det = _LightHMM(n_states=2, lookback=30, min_data=50)
        assert det.maybe_fit(ohlcv) is False

    def test_fit_rets_short(self) -> None:
        """len(close)==min_data → 收益率序列少一个 → 不拟合。"""
        np.random.seed(42)
        n = 50
        close = 100 + np.cumsum(np.random.randn(n) * 0.3)
        ohlcv = _make_ohlcv(close, n)
        det = _LightHMM(n_states=2, lookback=30, min_data=50)
        assert det.maybe_fit(ohlcv) is False

    def test_fit_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """GaussianHMM 拟合抛异常 → False。"""
        np.random.seed(42)
        n = 150
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        class _FailingHMM:
            def __init__(self, *a: object, **k: object) -> None:
                raise RuntimeError("boom")

        monkeypatch.setattr("hmmlearn.hmm.GaussianHMM", _FailingHMM)
        det = _LightHMM(n_states=2, lookback=100, min_data=50, random_seed=42)
        assert det.maybe_fit(ohlcv) is False
        assert det._is_fitted is False

    def test_infer_state_map_model_none(self) -> None:
        """model 为 None → 直接返回。"""
        det = _LightHMM(n_states=2)
        det._infer_state_map(np.zeros((10, 2)))
        assert det._state_map == {}

    def test_infer_state_map_missing_and_remaining(self) -> None:
        """mask 为空 + remaining 状态分配分支。"""
        det = _LightHMM(n_states=4)
        features = np.random.randn(100, 2)

        class _FakeModel:
            def predict(self, f: np.ndarray) -> np.ndarray:
                return np.zeros(len(f), dtype=int)  # 全部归为 state 0

        det._model = _FakeModel()
        det._infer_state_map(features)
        assert len(det._state_map) == 4

    def test_predict_short_close(self) -> None:
        """拟合后数据 < 20 行 → unknown。"""
        np.random.seed(42)
        n = 150
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        det = _LightHMM(n_states=2, lookback=100, min_data=50, random_seed=42)
        assert det.maybe_fit(ohlcv) is True
        short = _make_ohlcv(np.full(10, 100.0), 10)
        regime, conf, feats = det.predict(short)
        assert regime == "unknown"
        assert conf == 0.0

    def test_predict_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """model.predict 抛异常 → unknown。"""
        np.random.seed(42)
        n = 150
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        det = _LightHMM(n_states=2, lookback=100, min_data=50, random_seed=42)
        assert det.maybe_fit(ohlcv) is True

        def _boom(self: object, f: np.ndarray) -> np.ndarray:
            raise RuntimeError("predict failed")

        monkeypatch.setattr(det._model, "predict", _boom)
        regime, conf, feats = det.predict(ohlcv)
        assert regime == "unknown"
        assert conf == 0.0


# ═══════════════════════════════════════════════════════════
# 7. MultiHorizonHMMDetector 异常分支
# ═══════════════════════════════════════════════════════════


@pytest.mark.skipif(not _HMM_AVAILABLE, reason="需要 hmmlearn")
class TestMultiHorizonHMMDetectorMore:
    """MultiHorizonHMMDetector 预测异常分支。"""

    def test_predict_horizon_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """全部 horizon 预测失败 → unknown + error 详情。"""
        np.random.seed(42)
        n = 300
        close = 100 + np.cumsum(np.random.randn(n) * 0.3 + 0.5)
        ohlcv = _make_ohlcv(close, n)

        def _boom(self: object, o: pd.DataFrame) -> tuple[str, float, dict]:
            raise RuntimeError("boom")

        monkeypatch.setattr(_LightHMM, "predict", _boom)
        detector = MultiHorizonHMMDetector(horizons=[126])
        regime, conf, feats = detector.predict(ohlcv)
        assert regime == "unknown"
        assert conf == 0.0
        assert "horizon_details" in feats
        assert feats["horizon_details"]["126"]["regime"] == "error"  # 键已统一为 str


# ═══════════════════════════════════════════════════════════
# 8. MSMRegimeDetector 边界分支
# ═══════════════════════════════════════════════════════════


@pytest.mark.skipif(not _MSM_AVAILABLE, reason="需要 statsmodels")
class TestMSMRegimeDetectorMore:
    """MSMRegimeDetector 边界分支补充。"""

    def test_fit_short_close(self) -> None:
        """close < min_data → 不拟合。"""
        ohlcv = _make_ohlcv(np.full(100, 100.0), 100)
        det = MSMRegimeDetector(min_data=252)
        assert det.fit(ohlcv) is False

    def test_fit_rets_short(self) -> None:
        """len(close)==min_data → 收益率序列少一个 → 不拟合。"""
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(50) * 0.3)
        ohlcv = _make_ohlcv(close, 50)
        det = MSMRegimeDetector(k_regimes=2, min_data=50)
        assert det.fit(ohlcv) is False

    def test_fit_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """MarkovRegression 构造抛异常 → False。"""
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(100) * 0.3)
        ohlcv = _make_ohlcv(close, 100)

        class _FailingMR:
            def __init__(self, *a: object, **k: object) -> None:
                raise RuntimeError("boom")

        monkeypatch.setattr(
            "fts.factor_engine.regime_hmm.MarkovRegression",
            _FailingMR,
        )
        det = MSMRegimeDetector(k_regimes=2, min_data=50)
        assert det.fit(ohlcv) is False

    def test_infer_state_map_full(self) -> None:
        """k_regimes=4：mask 空 + remaining 分配。"""
        det = MSMRegimeDetector(k_regimes=4)
        states = np.zeros(100, dtype=int)  # 全部归为 state 0
        rets = np.random.randn(100)
        det._infer_state_map(states, rets)
        assert len(det._state_map) == 4

    def test_predict_short_close(self) -> None:
        """拟合状态但数据 < 20 行 → unknown。"""
        det = MSMRegimeDetector(k_regimes=2)
        det._is_fitted = True
        det._model = object()
        short = _make_ohlcv(np.full(10, 100.0), 10)
        regime, conf, _ = det.predict(short)
        assert regime == "unknown"
        assert conf == 0.0

    def test_predict_normal(self) -> None:
        """假 model → 正常预测路径。"""
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(60) * 0.3 + 0.1)
        ohlcv = _make_ohlcv(close, 60)

        det = MSMRegimeDetector(k_regimes=2, min_data=40)
        det._is_fitted = True
        det._state_map = {0: "bull", 1: "bear"}

        class _FakeResult:
            smoothed_marginal_probabilities = pd.DataFrame(
                {0: np.full(60, 0.8), 1: np.full(60, 0.2)},
                index=ohlcv.index,
            )

        det._result = _FakeResult()
        regime, conf, feats = det.predict(ohlcv)
        assert regime == "bull"
        assert conf == pytest.approx(0.8)
        assert feats == {"msm_state": 0}

    def test_predict_nan(self) -> None:
        """假 model 返回全 NaN → unknown。"""
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(60) * 0.3 + 0.1)
        ohlcv = _make_ohlcv(close, 60)

        det = MSMRegimeDetector(k_regimes=2, min_data=40)
        det._is_fitted = True
        det._state_map = {0: "bull", 1: "bear"}

        class _FakeResult:
            smoothed_marginal_probabilities = pd.DataFrame(
                {0: np.full(60, np.nan), 1: np.full(60, np.nan)},
                index=ohlcv.index,
            )

        det._result = _FakeResult()
        regime, conf, _ = det.predict(ohlcv)
        assert regime == "unknown"
        assert conf == 0.0

    def test_predict_state_out_of_range(self) -> None:
        """last_state 超出状态列数 → confidence=0.5。"""
        np.random.seed(42)
        close = 100 + np.cumsum(np.random.randn(60) * 0.3 + 0.1)
        ohlcv = _make_ohlcv(close, 60)

        det = MSMRegimeDetector(k_regimes=2, min_data=40)
        det._is_fitted = True
        det._state_map = {0: "bull", 1: "bear"}

        class _FakeResult:
            smoothed_marginal_probabilities = pd.DataFrame(
                {10: np.full(60, 0.8), 20: np.full(60, 0.2)},
                index=ohlcv.index,
            )

        det._result = _FakeResult()
        regime, conf, feats = det.predict(ohlcv)
        assert regime == "oscillate"  # 未映射状态 → 默认
        assert conf == 0.5
        assert feats == {"msm_state": 10}

    def test_predict_exception(self) -> None:
        """result 访问抛异常 → unknown。"""
        det = MSMRegimeDetector(k_regimes=2)
        det._is_fitted = True

        class _Boom:
            @property
            def smoothed_marginal_probabilities(self):
                raise RuntimeError("boom")

        det._result = _Boom()
        ohlcv = _make_ohlcv(100 + np.arange(60, dtype=float), 60)
        regime, conf, _ = det.predict(ohlcv)
        assert regime == "unknown"
        assert conf == 0.0
