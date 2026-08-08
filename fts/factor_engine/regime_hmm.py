"""
fts.factor_engine.regime_hmm — HMM 增强模块（STEP3 P1.2/P1.3/P3.1）

包含:
  - MultiHorizonHMMDetector: 多周期 HMM 集成检测器
  - MSMRegimeDetector:      马尔可夫切换模型（statsmodels）
  - StateMapStabilizer:     状态映射稳定性增强

用法:
    from fts.factor_engine.regime_hmm import MultiHorizonHMMDetector
    detector = MultiHorizonHMMDetector(horizons=[63, 126, 252])
    regime, confidence, features = detector.predict(ohlcv)

版本: v0.1.0
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── hmmlearn 可选 ────────────────────────────────────────
_HMM_AVAILABLE: bool = False
try:
    from hmmlearn import hmm
    _HMM_AVAILABLE = True
except ImportError:
    pass

# ─── statsmodels 可选（MSM） ──────────────────────────────
_MSM_AVAILABLE: bool = False
try:
    from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression
    _MSM_AVAILABLE = True
except ImportError:
    pass


# ─── 默认参数 ──────────────────────────────────────────────

_DEFAULT_HORIZONS = [63, 126, 252]          # 短/中/长 周期
_DEFAULT_WEIGHTS = {63: 0.2, 126: 0.5, 252: 0.3}
_DEFAULT_N_STATES = 4
_DEFAULT_MIN_DATA = 126
_DEFAULT_RANDOM_SEED = 42

# 状态映射稳定性
_STATE_HISTORY_MAXLEN = 5                    # 保存最近 5 次映射
_STATE_FREEZE_CONFIDENCE = 0.8              # 置信度 > 此值冻结映射
_STATE_FUSION_ALPHA = 0.7                   # 历史映射融合权重


# ─── 状态映射稳定器 ────────────────────────────────────────

class StateMapStabilizer:
    """HMM 状态映射稳定性增强。

    问题: 每次 refit 后 HMM 的状态标签可能翻转（state 0 上次是 bull，
    这次变成 bear），导致检测结果跳变。

    解决:
      1. 记录最近 N 次映射的历史
      2. 新映射与历史映射加权融合（alpha=0.7）
      3. 置信度 > 0.8 时冻结当前映射
    """

    def __init__(
        self,
        history_maxlen: int = _STATE_HISTORY_MAXLEN,
        fusion_alpha: float = _STATE_FUSION_ALPHA,
        freeze_confidence: float = _STATE_FREEZE_CONFIDENCE,
    ) -> None:
        self.history_maxlen = history_maxlen
        self.fusion_alpha = fusion_alpha
        self.freeze_confidence = freeze_confidence

        # history: list of dict[int, tuple[float, float]]  state → (mean_ret, mean_vol)
        self._history: list[dict[int, tuple[float, float]]] = []
        self._frozen_map: dict[int, str] | None = None
        self._last_map: dict[int, str] | None = None

    def stabilize(
        self,
        new_map: dict[int, str],
        state_stats: list[dict[str, Any]],
        current_confidence: float,
    ) -> dict[int, str]:
        """对 HMM 的新状态映射进行稳定性处理。

        参数:
            new_map:           HMM 新训练的 state → regime 映射
            state_stats:       每个状态的统计信息 [{state, mean_ret, mean_vol}]
            current_confidence: 当前检测置信度

        返回:
            稳定后的映射。
        """
        # 高置信度 → 冻结
        if current_confidence >= self.freeze_confidence and self._frozen_map is not None:
            return self._frozen_map

        # 记录历史状态统计
        stats_dict: dict[int, tuple[float, float]] = {}
        for s in state_stats:
            stats_dict[s["state"]] = (s["mean_ret"], s["mean_vol"])
        self._history.append(stats_dict)
        if len(self._history) > self.history_maxlen:
            self._history.pop(0)

        # 没有历史映射 → 直接使用新映射
        if self._last_map is None:
            self._last_map = dict(new_map)
            self._frozen_map = None
            return dict(new_map)

        # 检测映射是否翻转过
        # 计算新旧映射的"一致性": 每个 regime 对应的 state 是否变化过大
        if self._is_map_flipped(new_map, self._last_map, stats_dict):
            logger.info("HMM 状态映射检测到翻转，应用加权融合")
            new_map = self._fuse_with_history(new_map, self._last_map)

        self._last_map = dict(new_map)
        self._frozen_map = dict(new_map) if current_confidence >= self.freeze_confidence else None
        return new_map

    def _is_map_flipped(
        self,
        new_map: dict[int, str],
        old_map: dict[int, str],
        stats: dict[int, tuple[float, float]],
    ) -> bool:
        """检测映射是否翻转：同一个 regime 对应的 state 均值收益符号变化。"""
        # 对每个 regime，检查新旧映射对应的 state 的 mean_ret 符号
        for old_state, regime in old_map.items():
            new_state = next((s for s, r in new_map.items() if r == regime), None)
            if new_state is None:
                continue
            old_ret = stats.get(old_state, (0, 0))[0]
            new_ret = stats.get(new_state, (0, 0))[0]
            # 如果符号相反，说明翻转了
            if old_ret * new_ret < -1e-6:
                return True
        return False

    def _fuse_with_history(
        self,
        new_map: dict[int, str],
        old_map: dict[int, str],
    ) -> dict[int, str]:
        """加权融合新旧映射。"""
        fused: dict[int, str] = {}
        # 以旧映射为主，对新映射中翻转的 state 进行纠正
        for state, regime in new_map.items():
            # 如果旧映射中同一个 state 是另一个 regime
            if state in old_map:
                old_regime = old_map[state]
                if old_regime == regime:
                    fused[state] = regime  # 一致 → 信任
                else:
                    # 不一致 → 用权重决定
                    if self.fusion_alpha >= 0.5:
                        fused[state] = old_regime  # 偏向历史
                    else:
                        fused[state] = regime
            else:
                fused[state] = regime
        return fused

    def reset(self) -> None:
        """重置状态（用于测试）。"""
        self._history.clear()
        self._frozen_map = None
        self._last_map = None


# ─── 多周期 HMM 集成检测器 ────────────────────────────────

class MultiHorizonHMMDetector:
    """多周期 HMM 集成检测器（P1.2）。

    在不同时间窗口上独立训练 HMM，然后通过加权投票融合结果。
    短周期快速捕捉切换，长周期定位宏观定位。

    参数:
        horizons:       训练窗口列表（交易日数）。
        weights:        每个窗口的投票权重。
        n_states:       HMM 状态数。
        min_data:       最少数据量要求。
        random_seed:    随机种子。
    """

    def __init__(
        self,
        horizons: list[int] | None = None,
        weights: dict[int, float] | None = None,
        n_states: int = _DEFAULT_N_STATES,
        min_data: int = _DEFAULT_MIN_DATA,
        random_seed: int = _DEFAULT_RANDOM_SEED,
    ) -> None:
        if not _HMM_AVAILABLE:
            logger.warning("hmmlearn 不可用，MultiHorizonHMMDetector 将始终返回 unknown")
            self._available = False
            return

        self._available = True
        self.horizons = horizons or _DEFAULT_HORIZONS
        self.weights = weights or _DEFAULT_WEIGHTS
        self.n_states = n_states
        self.min_data = min_data
        self.random_seed = random_seed

        # 为每个 horizon 创建独立 HMM 检测器
        # 复用 regime.py 中的 HMMRegimeDetector 逻辑，但在本模块内建轻量版本
        self._detectors: dict[int, _LightHMM] = {}
        for h in self.horizons:
            self._detectors[h] = _LightHMM(
                n_states=n_states,
                lookback=h,
                min_data=min_data,
                random_seed=random_seed,
            )

        self._stabilizer = StateMapStabilizer()

    def predict(self, ohlcv: pd.DataFrame) -> tuple[str, float, dict]:
        """多周期 HMM 集成预测。

        参数:
            ohlcv: OHLCV DataFrame。

        返回:
            (regime, confidence, features) 三元组。
        """
        if not self._available or ohlcv is None or ohlcv.empty:
            return "unknown", 0.0, {}

        votes: dict[str, float] = {}
        confidences: list[float] = []
        horizon_details: dict[int, dict] = {}

        for h, det in self._detectors.items():
            w = self.weights.get(h, 1.0)
            try:
                det.maybe_fit(ohlcv)
                regime, conf, feats = det.predict(ohlcv)
                if regime != "unknown" and conf >= 0.3:
                    votes[regime] = votes.get(regime, 0.0) + w
                    confidences.append(conf * w)
                    horizon_details[h] = {"regime": regime, "confidence": conf, "weight": w}
                else:
                    horizon_details[h] = {"regime": "unknown", "confidence": 0.0, "weight": w}
            except Exception as e:
                logger.debug("Horizon %d HMM 预测失败: %s", h, e)
                horizon_details[h] = {"regime": "error", "confidence": 0.0, "weight": w}

        if not votes:
            return "unknown", 0.0, {"horizon_details": horizon_details}

        # 加权投票决定 regime
        total_weight = sum(self.weights.values())
        best_regime = max(votes, key=votes.get)
        best_vote = votes[best_regime]

        # 置信度 = 最佳得票率 × 平均置信度
        vote_share = best_vote / total_weight
        avg_conf = np.mean(confidences) if confidences else 0.0
        confidence = float(np.clip(vote_share * avg_conf * 2.0, 0.0, 0.99))

        features = {
            "multi_hmm_votes": {k: round(v, 2) for k, v in votes.items()},
            "multi_hmm_vote_share": round(vote_share, 4),
            "multi_hmm_avg_confidence": round(avg_conf, 4),
            "horizon_details": {
                str(h): v for h, v in horizon_details.items()
            },
        }

        return best_regime, confidence, features

    @property
    def is_available(self) -> bool:
        return self._available


class _LightHMM:
    """轻量 HMM 检测器（内部使用，避免循环依赖 regime.py）。"""

    def __init__(
        self,
        n_states: int = 4,
        lookback: int = 252,
        min_data: int = 126,
        random_seed: int = 42,
    ) -> None:
        self.n_states = n_states
        self.lookback = lookback
        self.min_data = min_data
        self.random_seed = random_seed

        self._model: hmm.GaussianHMM | None = None
        self._state_map: dict[int, str] = {}
        self._is_fitted = False
        self._call_count = 0
        self._last_fit_len = 0

    def maybe_fit(self, ohlcv: pd.DataFrame) -> bool:
        """按需训练/重训练 HMM。"""
        self._call_count += 1
        if not self._is_fitted or self._call_count % 10 == 0:
            return self._fit(ohlcv)
        return self._is_fitted

    def _fit(self, ohlcv: pd.DataFrame) -> bool:
        close = ohlcv["close"].dropna()
        if len(close) < self.min_data:
            return False

        rets = close.pct_change().dropna().values.reshape(-1, 1)
        if len(rets) < self.min_data:
            return False

        rets_series = pd.Series(close.pct_change().dropna())
        vol = rets_series.rolling(20).std().fillna(0).values.reshape(-1, 1)
        features = np.column_stack([rets, vol])
        train_features = features[-min(self.lookback, len(features)):]

        try:
            self._model = hmm.GaussianHMM(
                n_components=self.n_states,
                covariance_type="diag",
                n_iter=100,
                tol=1e-4,
                random_state=self.random_seed,
            )
            self._model.fit(train_features)
            self._infer_state_map(train_features)
            self._is_fitted = True
            self._last_fit_len = len(train_features)
            return True
        except Exception:
            self._is_fitted = False
            return False

    def _infer_state_map(self, features: np.ndarray) -> None:
        if self._model is None:
            return
        states = self._model.predict(features)
        state_stats: list[dict] = []
        for s in range(self.n_states):
            mask = states == s
            if mask.sum() == 0:
                state_stats.append({"state": s, "mean_ret": 0.0, "mean_vol": 0.0})
                continue
            state_stats.append({
                "state": s,
                "mean_ret": float(features[mask, 0].mean()),
                "mean_vol": float(features[mask, 1].mean()),
            })

        sorted_by_ret = sorted(state_stats, key=lambda x: x["mean_ret"], reverse=True)
        assignment: list[tuple[int, str]] = []
        used: set[int] = set()

        if len(sorted_by_ret) >= 1:
            assignment.append((sorted_by_ret[0]["state"], "bull"))
            used.add(sorted_by_ret[0]["state"])
        if len(sorted_by_ret) >= 2:
            assignment.append((sorted_by_ret[-1]["state"], "bear"))
            used.add(sorted_by_ret[-1]["state"])

        remaining = [s for s in sorted_by_ret if s["state"] not in used]
        if remaining:
            remaining.sort(key=lambda x: x["mean_vol"], reverse=True)
            assignment.append((remaining[0]["state"], "high_vol"))
            used.add(remaining[0]["state"])
            for s in remaining[1:]:
                assignment.append((s["state"], "oscillate"))
                used.add(s["state"])

        self._state_map = dict(assignment)

    def predict(self, ohlcv: pd.DataFrame) -> tuple[str, float, dict]:
        if not self._is_fitted or self._model is None:
            return "unknown", 0.0, {}
        close = ohlcv["close"].dropna()
        if len(close) < 20:
            return "unknown", 0.0, {}
        rets = close.pct_change().dropna()
        rets_vals = rets.values.reshape(-1, 1)
        vol = rets.rolling(20).std().fillna(0).values.reshape(-1, 1)
        features = np.column_stack([rets_vals, vol])
        try:
            state = int(self._model.predict(features)[-1])
            probs = self._model.predict_proba(features)[-1]
            regime = self._state_map.get(state, "oscillate")
            confidence = float(min(1.0, max(0.0, probs[state])))
            return regime, confidence, {"hmm_state": state}
        except Exception:
            return "unknown", 0.0, {}


# ─── MSM 马尔可夫切换模型（P3.1） ─────────────────────────

class MSMRegimeDetector:
    """马尔可夫切换模型检测器（P3.1）。

    基于 statsmodels 的 MarkovRegression，对收益率序列进行
    状态切换建模，输出每个状态的概率、均值、方差。

    参数:
        k_regimes: 状态数（默认 4）。
        min_data:  最少数据量要求。
    """

    def __init__(
        self,
        k_regimes: int = 4,
        min_data: int = 252,
    ) -> None:
        if not _MSM_AVAILABLE:
            logger.warning("statsmodels 不可用，MSMRegimeDetector 将始终返回 unknown")
            self._available = False
            return

        self._available = True
        self.k_regimes = k_regimes
        self.min_data = min_data
        self._model: Any = None
        self._is_fitted = False
        self._state_map: dict[int, str] = {}

    def fit(self, ohlcv: pd.DataFrame) -> bool:
        """训练 MSM 模型。"""
        if not self._available:
            return False
        close = ohlcv["close"].dropna()
        if len(close) < self.min_data:
            return False
        rets = close.pct_change().dropna() * 100  # 百分比
        if len(rets) < self.min_data:
            return False
        try:
            self._model = MarkovRegression(
                rets,
                k_regimes=self.k_regimes,
                trend="c",
                switching_variance=True,
            )
            result = self._model.fit(disp=False)
            # 推断状态
            smoothed = result.smoothed_marginal_probabilities
            states = smoothed.idxmax(axis=1).values
            self._infer_state_map(states, rets.values)
            self._is_fitted = True
            return True
        except Exception as e:
            logger.warning("MSM 拟合失败: %s", e)
            self._is_fitted = False
            return False

    def _infer_state_map(self, states: np.ndarray, rets: np.ndarray) -> None:
        """与 HMM 相同的状态映射策略。"""
        state_stats: list[dict] = []
        for s in range(self.k_regimes):
            mask = states == s
            if mask.sum() == 0:
                state_stats.append({"state": s, "mean_ret": 0.0, "mean_vol": 0.0})
                continue
            s_rets = rets[mask]
            state_stats.append({
                "state": s,
                "mean_ret": float(s_rets.mean()),
                "mean_vol": float(s_rets.std()),
            })

        sorted_by_ret = sorted(state_stats, key=lambda x: x["mean_ret"], reverse=True)
        assignment: list[tuple[int, str]] = []
        used: set[int] = set()

        if len(sorted_by_ret) >= 1:
            assignment.append((sorted_by_ret[0]["state"], "bull"))
            used.add(sorted_by_ret[0]["state"])
        if len(sorted_by_ret) >= 2:
            assignment.append((sorted_by_ret[-1]["state"], "bear"))
            used.add(sorted_by_ret[-1]["state"])

        remaining = [s for s in sorted_by_ret if s["state"] not in used]
        if remaining:
            remaining.sort(key=lambda x: x["mean_vol"], reverse=True)
            assignment.append((remaining[0]["state"], "high_vol"))
            used.add(remaining[0]["state"])
            for s in remaining[1:]:
                assignment.append((s["state"], "oscillate"))

        self._state_map = dict(assignment)

    def predict(self, ohlcv: pd.DataFrame) -> tuple[str, float, dict]:
        """预测当前市场制度。"""
        if not self._is_fitted or self._model is None:
            return "unknown", 0.0, {}
        close = ohlcv["close"].dropna()
        if len(close) < 20:
            return "unknown", 0.0, {}
        rets = close.pct_change().dropna() * 100
        if len(rets) < 20:
            return "unknown", 0.0, {}
        try:
            # 使用最后 20 个数据点预测
            test_data = rets.iloc[-20:]
            # 用模型预测最后一个点的状态
            pred = self._model.predict(test_data)
            # 过滤 NaN
            pred_clean = pred.dropna()
            if pred_clean.empty:
                return "unknown", 0.0, {}
            last_state = int(pred_clean.idxmax(axis=1).iloc[-1])
            regime = self._state_map.get(last_state, "oscillate")
            n_states_in_last = len(pred_clean.columns)
            if last_state < n_states_in_last:
                confidence = float(min(1.0, pred_clean.iloc[-1, last_state]))
            else:
                confidence = 0.5
            return regime, confidence, {"msm_state": last_state}
        except Exception as e:
            logger.debug("MSM 预测失败: %s", e)
            return "unknown", 0.0, {}

    @property
    def is_available(self) -> bool:
        return self._available


# ─── 集成到 RegimeAwareSelector 的辅助函数 ─────────────────

def create_multi_hmm_selector(
    base_lookback: int = 60,
    horizons: list[int] | None = None,
    weights: dict[int, float] | None = None,
) -> tuple[MultiHorizonHMMDetector | None, MSMRegimeDetector | None]:
    """创建多周期 HMM + MSM 检测器（用于 RegimeAwareSelector 注入）。

    返回:
        (multi_hmm, msm) 元组，不可用时为 None。
    """
    multi_hmm: MultiHorizonHMMDetector | None = None
    msm: MSMRegimeDetector | None = None

    if _HMM_AVAILABLE:
        multi_hmm = MultiHorizonHMMDetector(
            horizons=horizons,
            weights=weights,
        )
        logger.info("MultiHorizonHMMDetector 已创建（horizons=%s）", horizons or _DEFAULT_HORIZONS)

    if _MSM_AVAILABLE:
        msm = MSMRegimeDetector(k_regimes=4)
        logger.info("MSMRegimeDetector 已创建")

    return multi_hmm, msm