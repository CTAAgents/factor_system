"""
fts.factor_engine.regime — 市场制度感知与因子选择性激活。

检测当前市场制度（bull/bear/oscillate/high_vol/low_vol），
记录因子在各 regime 下的历史表现，
仅选择在当前制度下有效的因子参与组合构建。

检测方法（v2.2 — 机构级增强版 + HMM 集成 + 扩展特征）:
  - 主检测: MultiHorizonHMMDetector（多周期 HMM 集成，P1.2）
  - 次检测: HMMRegimeDetector（单周期 HMM，状态映射稳定 P1.3 + 扩展特征 P2.1）
  - 第三检测: MSMRegimeDetector（马尔可夫切换模型，P3.1）
  - 回退检测: 多周期加权趋势投票 + 分位数/绝对波动率 + 软投票判定
  - 制度平滑: 转移概率矩阵（HMM）或指数衰减（规则）

用法:
    selector = RegimeAwareSelector()
    regime = selector.detect(ohlcv_df)
    active_factors = selector.select_factors(regime, elite_factors)

版本: v2.3.0
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, TypedDict

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# hmmlearn 是可选依赖，HMM 检测失败时自动回退到规则方法
_HMM_AVAILABLE: bool = False
try:
    from hmmlearn import hmm

    _HMM_AVAILABLE = True
except ImportError:
    pass

# MSM 可选依赖
_MSM_AVAILABLE: bool = False
try:
    from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression  # noqa: F401

    _MSM_AVAILABLE = True
except ImportError:
    pass

# 导入 HMM 增强模块（STEP3 P1.2/P1.3/P3.1）
from fts.factor_engine.regime_hmm import (  # noqa: E402 — MSM 可选依赖探测后导入
    MultiHorizonHMMDetector,
    MSMRegimeDetector,
    StateMapStabilizer,
)

# 导入扩展特征模块（STEP3 P2.1）
from fts.factor_engine.regime_features import compute_hmm_feature_vector  # noqa: E402

# 导入规则伪概率构造（28-T1）
from fts.factor_engine.regime_calibration import build_rule_regime_probs  # noqa: E402

# 导入 BIC 状态数选择与特征标准化（28-T8）
from fts.factor_engine.regime_model_selection import fit_standardizer, select_n_states  # noqa: E402


# ─── 契约 ─────────────────────────────────────────────────


class MarketRegime(TypedDict):
    """市场制度检测结果。"""

    regime: str  # bull / bear / oscillate / high_vol / low_vol
    confidence: float  # 置信度 0~1
    detected_at: str  # ISO 8601
    features: dict  # 检测特征
    method: str  # 检测方法: "hmm" / "rule" / "fallback"
    regime_probs: dict[str, float]  # 新增：全制度概率分布（和为 1）


class RegimePerformance(TypedDict, total=False):
    """因子在某 regime 下的历史表现。"""

    ic_mean: float
    sharpe: float
    n_windows: int


class RegimeFactorProfile(TypedDict, total=False):
    """因子在各 regime 下的表现记录。"""

    factor_id: str
    regime_performance: dict[str, RegimePerformance]  # regime -> performance


# ─── 默认阈值 ─────────────────────────────────────────────

# 趋势检测（多周期收益率）
_TREND_SHORT_DAYS = 20  # 短期趋势窗口
_TREND_MEDIUM_DAYS = 60  # 中期趋势窗口
_TREND_LONG_DAYS = 200  # 长期趋势窗口
_TREND_THRESHOLD = 0.02  # 基准确率阈值（将被波动率自适应调整）
_TREND_WEIGHTS = {"short": 1, "medium": 2, "long": 3}  # 加权投票权重

# 波动率检测
_VOL_HISTORY_DAYS = 252  # 波动率历史窗口（约 1 年交易日）
_VOL_HIGH_PERCENTILE = 0.80  # 高波动阈值：历史 80% 分位数以上
_VOL_LOW_PERCENTILE = 0.20  # 低波动阈值：历史 20% 分位数以下
_VOL_ABSOLUTE_LOW = 0.10  # 绝对值低波阈值（年化 10%）
_VOL_ABSOLUTE_HIGH = 0.40  # 绝对值高波阈值（年化 40%）
_VOL_HIGH_FLOOR = 0.15  # 相对高波阈值的绝对下限（年化 15%）：分位数过小（近恒定序列 q80<0.15）
# 时兜底到该值，避免 effective_high≈current_vol 导致 vol_score 虚高误判 high_vol

# ADX 指标
_ADX_PERIOD = 14  # ADX 计算周期
_ADX_TREND_THRESHOLD = 25  # ADX > 25 表示有趋势

# 制度平滑（规则方法）
_REGIME_PERSISTENCE_FACTOR = 0.7  # 上次制度置信度保留比例 0~1，越大越平滑

# HMM 参数
_HMM_N_STATES = 4  # 状态数: bull / bear / high_vol / oscillate
_HMM_LOOKBACK = 252  # 训练窗口
_HMM_REFIT_INTERVAL = 20  # 每 N 次调用 refit 一次
_HMM_MIN_DATA = 126  # 最少数据要求（半年交易日）
_HMM_RANDOM_SEED = 42  # 确定性种子


# ─── 辅助函数 ─────────────────────────────────────────────


def _compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = _ADX_PERIOD,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """计算 ADX（平均趋向指数）及 ±DI。

    返回:
        (adx, plus_di, minus_di) — 均为 Series。
    """
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - close.shift(1)).abs(),
            (low - close.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0),
        index=low.index,
    )

    # 平滑
    tr_smooth = tr.rolling(period, min_periods=period).mean()
    plus_di = 100 * plus_dm.rolling(period, min_periods=period).mean() / (tr_smooth + 1e-10)
    minus_di = 100 * minus_dm.rolling(period, min_periods=period).mean() / (tr_smooth + 1e-10)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    adx = dx.rolling(period, min_periods=period).mean()
    return adx, plus_di, minus_di


def _ewma_volatility(rets: pd.Series, span: int = 30) -> pd.Series:
    """EWMA 波动率估计（RiskMetrics 风格, lambda ≈ 0.94）。

    参数:
        rets:  收益率序列。
        span:  EWMA 跨度（span=30 对应 lambda ≈ 0.935）。

    返回:
        年化波动率 Series。
    """
    return rets.pow(2).ewm(span=span).mean().pow(0.5) * np.sqrt(252)


# ─── HMMRegimeDetector ────────────────────────────────────


def _states_to_regime_probs(state_probs: np.ndarray, state_map: dict[int, str]) -> dict[str, float]:
    """HMM 状态概率数组 → 制度概率分布（同制度多状态求和）。

    参数:
        state_probs: HMM 后验概率数组（每状态一概率，和为 1）。
        state_map:   HMM 状态索引 → 制度名映射。

    返回:
        制度名 → 概率 的分布 dict，和为 1（28-T1）。
    """
    agg: dict[str, float] = {}
    for s, p in enumerate(state_probs):
        r = state_map.get(s, "oscillate")
        agg[r] = agg.get(r, 0.0) + float(p)
    total = sum(agg.values()) or 1.0
    return {r: p / total for r, p in agg.items()}


class HMMRegimeDetector:
    """HMM-based 市场制度检测器。

    使用隐马尔可夫模型对收益率和波动率特征进行概率化制度分类。
    训练后可通过后验概率直接获得置信度。

    参数:
        n_states:   状态数（默认 4: bull/bear/high_vol/oscillate）。
        lookback:   训练窗口大小（交易日数）。
        refit_interval: 每 N 次调用 refit 一次。
        min_data:   最少数据量要求。
        random_seed: 随机种子（保证确定性）。
    """

    def __init__(
        self,
        n_states: int = _HMM_N_STATES,
        lookback: int = _HMM_LOOKBACK,
        refit_interval: int = _HMM_REFIT_INTERVAL,
        min_data: int = _HMM_MIN_DATA,
        random_seed: int = _HMM_RANDOM_SEED,
        use_stabilizer: bool = True,
    ) -> None:
        self.n_states = n_states
        self.lookback = lookback
        self.refit_interval = refit_interval
        self.min_data = min_data
        self.random_seed = random_seed

        self._model: hmm.GaussianHMM | None = None
        self._state_map: dict[int, str] = {}  # HMM state index → regime name
        self._call_count = 0
        self._last_fit_data_len = 0
        self._is_fitted = False

        # P1.3: 状态映射稳定性增强
        self._stabilizer = StateMapStabilizer() if use_stabilizer else None
        self._last_confidence = 0.0  # 上次预测的置信度，用于 stabilizer

    # ── 训练 ──────────────────────────────────────────────

    def fit(self, ohlcv: pd.DataFrame) -> bool:
        """在历史数据上训练 HMM 模型（v2.1 — 使用扩展特征）。

        参数:
            ohlcv: OHLCV DataFrame。

        返回:
            是否训练成功。
        """
        if not _HMM_AVAILABLE:
            return False

        close = ohlcv["close"].dropna()
        if len(close) < self.min_data:
            return False

        rets = close.pct_change().dropna()
        if len(rets) < self.min_data:
            return False

        # v2.1: 使用扩展特征模块构建增强特征向量
        # 基础特征 [收益率, 20d 已实现波动率] + 扩展特征
        base_features = np.column_stack(
            [
                rets.to_numpy().reshape(-1, 1),
                rets.rolling(20).std().fillna(0).to_numpy().reshape(-1, 1),
            ]
        )
        features = compute_hmm_feature_vector(ohlcv, base_features=base_features)
        if features.size == 0:
            features = base_features

        # 取最近 lookback 窗口
        train_features = features[-min(self.lookback, len(features)) :]

        # 28-T8: 特征标准化（只 fit 训练段，防数据窥探；predict 用同一参数 transform）
        self._scaler_mean, self._scaler_std = fit_standardizer(train_features)
        train_features = (train_features - self._scaler_mean) / self._scaler_std

        # 28-T8: BIC 状态数选择（状态数变化时重建模型，重置 _state_map 重新推断）
        if getattr(self, "_last_n_states", None) != self.n_states:
            self.n_states = select_n_states(train_features, candidates=(2, 3, 4))
            self._last_n_states = self.n_states
            self._state_map = {}  # 状态数变化 → 映射失效，重新推断

        try:
            self._model = hmm.GaussianHMM(
                n_components=self.n_states,
                covariance_type="diag",
                n_iter=100,
                tol=1e-4,
                random_state=self.random_seed,
                init_params="stmc",
                params="stmc",
            )
            self._model.fit(train_features)
            self._infer_state_map(train_features)
            self._is_fitted = True
            self._last_fit_data_len = len(train_features)
            return True
        except Exception:
            self._is_fitted = False
            return False

    def _infer_state_map(self, features: np.ndarray) -> None:
        """根据状态的统计特征推断制度映射（v2.1 — 集成 StateMapStabilizer）。

        方法:
            - 按均值收益率排序
            - 最高收益 → "bull"
            - 最低收益 → "bear"
            - 最高波动（剩余中）→ "high_vol"
            - 剩余 → "oscillate"
            - 通过 StateMapStabilizer 防止状态标签翻转（P1.3）
        """
        if self._model is None:
            return

        states = self._model.predict(features)

        # 计算每个状态的统计量
        state_stats: list[dict[str, Any]] = []
        for s in range(self.n_states):
            mask = states == s
            if mask.sum() == 0:
                state_stats.append({"state": s, "mean_ret": 0.0, "mean_vol": 0.0})
                continue
            s_ret = features[mask, 0].mean()
            s_vol = features[mask, 1].mean()
            state_stats.append({"state": s, "mean_ret": float(s_ret), "mean_vol": float(s_vol)})

        # 按均值收益率排序（降序）
        sorted_by_ret = sorted(state_stats, key=lambda x: x["mean_ret"], reverse=True)

        # 分配
        assignment: list[tuple[int, str]] = []
        used: set[int] = set()

        if len(sorted_by_ret) >= 1:
            assignment.append((sorted_by_ret[0]["state"], "bull"))
            used.add(sorted_by_ret[0]["state"])
        if len(sorted_by_ret) >= 2:
            assignment.append((sorted_by_ret[-1]["state"], "bear"))
            used.add(sorted_by_ret[-1]["state"])

        # 剩余状态中，最高波动 → high_vol
        remaining = [s for s in sorted_by_ret if s["state"] not in used]
        if remaining:
            remaining.sort(key=lambda x: x["mean_vol"], reverse=True)
            assignment.append((remaining[0]["state"], "high_vol"))
            used.add(remaining[0]["state"])
            for rs in remaining[1:]:
                assignment.append((rs["state"], "oscillate"))
                used.add(rs["state"])

        raw_map = dict(assignment)

        # P1.3: 通过 StateMapStabilizer 防止状态标签翻转
        if self._stabilizer is not None:
            raw_map = self._stabilizer.stabilize(raw_map, state_stats, self._last_confidence)

        self._state_map = raw_map

    # ── 预测 ──────────────────────────────────────────────

    def predict(self, ohlcv: pd.DataFrame) -> tuple[str, float, dict]:
        """预测当前市场制度（v2.1 — 使用扩展特征）。

        参数:
            ohlcv: OHLCV DataFrame。

        返回:
            (regime, confidence, features) 三元组。
            检测失败时返回 ("unknown", 0.0, {})。
        """
        if not self._is_fitted or self._model is None:
            return "unknown", 0.0, {}

        close = ohlcv["close"].dropna()
        if len(close) < 20:
            return "unknown", 0.0, {}

        rets = close.pct_change().dropna()
        rets_vals = rets.to_numpy().reshape(-1, 1)
        vol = rets.rolling(20).std().fillna(0).to_numpy().reshape(-1, 1)
        base_features = np.column_stack([rets_vals, vol])
        features = compute_hmm_feature_vector(ohlcv, base_features=base_features)
        if features.size == 0:
            features = base_features

        # 28-T8: 用训练段 fit 的标准化参数 transform（同一套参数，防数据泄露）
        if hasattr(self, "_scaler_mean"):
            features = (features - self._scaler_mean) / self._scaler_std

        try:
            state = int(self._model.predict(features)[-1])
            probs = self._model.predict_proba(features)[-1]
            regime = self._state_map.get(state, "oscillate")
            confidence = float(min(1.0, max(0.0, probs[state])))
            regime_probs = _states_to_regime_probs(probs, self._state_map)  # 28-T1
            # 存储置信度，供 stabilizer 在下一次 _infer_state_map 使用
            self._last_confidence = confidence
            return regime, confidence, {"hmm_state": state, "hmm_probs": probs.tolist(), "regime_probs": regime_probs}
        except Exception:
            return "unknown", 0.0, {}

    # ── 生命周期 ──────────────────────────────────────────

    def maybe_refit(self, ohlcv: pd.DataFrame) -> bool:
        """按间隔检查是否需要重新训练。

        参数:
            ohlcv: 最新 OHLCV 数据。

        返回:
            是否重新训练（或初次训练）成功。
        """
        self._call_count += 1
        close_len = len(ohlcv["close"].dropna())

        # 初次训练
        if not self._is_fitted:
            return self.fit(ohlcv)

        # 定期 refit（数据量增加时）
        if self._call_count % self.refit_interval == 0 and close_len > self._last_fit_data_len:
            return self.fit(ohlcv)

        return True


# ─── 规则方法 ─────────────────────────────────────────────


def _detect_by_rule(ohlcv: pd.DataFrame, prev_regime: MarketRegime | None) -> MarketRegime:
    """基于规则的市场制度检测（Step 1 增强版）。

    检测逻辑:
        1. 加权多周期趋势投票（1:2:3）+ ADX 辅助指标
        2. EWMA 波动率 + 历史分位数 + 绝对值双阈值
        3. 软投票融合判定制度
        4. 指数衰减平滑

    参数:
        ohlcv:       OHLCV DataFrame。
        prev_regime: 上次检测结果（用于平滑）。

    返回:
        MarketRegime。
    """
    # ── 空/不足 20 行 → 兜底 ─────────────────────────
    if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
        return MarketRegime(
            regime="oscillate",
            confidence=0.5,
            detected_at=datetime.now().isoformat(),
            features={},
            method="fallback",
            regime_probs={"oscillate": 1.0},
        )

    close = ohlcv["close"].dropna()
    if len(close) < 20:
        return MarketRegime(
            regime="oscillate",
            confidence=0.5,
            detected_at=datetime.now().isoformat(),
            features={},
            method="fallback",
            regime_probs={"oscillate": 1.0},
        )

    close_filled = close.ffill()
    rets = close_filled.pct_change().dropna()
    high = ohlcv["high"].dropna()
    low = ohlcv["low"].dropna()

    # ── 1. 多周期趋势检测（加权投票） ────────────────
    def _period_return(n: int) -> float:
        if len(close_filled) < n + 1:
            return 0.0
        return float(close_filled.iloc[-1] / close_filled.iloc[-n - 1] - 1.0)

    trend_short = _period_return(_TREND_SHORT_DAYS)
    trend_medium = _period_return(_TREND_MEDIUM_DAYS)
    trend_long = _period_return(_TREND_LONG_DAYS)

    # 波动率自适应阈值
    current_vol_est = _compute_current_vol_estimate(rets)
    adaptive_threshold = _TREND_THRESHOLD * (1 + 2 * (current_vol_est - 0.15) / 0.15)
    adaptive_threshold = float(np.clip(adaptive_threshold, 0.01, 0.05))

    # 加权投票
    trend_votes = {"bull": 0.0, "bear": 0.0, "flat": 0.0}
    for label, tr, w in [
        ("short", trend_short, _TREND_WEIGHTS.get("short", 1)),
        ("medium", trend_medium, _TREND_WEIGHTS.get("medium", 2)),
        ("long", trend_long, _TREND_WEIGHTS.get("long", 3)),
    ]:
        if tr > adaptive_threshold:
            trend_votes["bull"] += w
        elif tr < -adaptive_threshold:
            trend_votes["bear"] += w
        else:
            trend_votes["flat"] += w

    total_weight = sum(_TREND_WEIGHTS.values())
    trend_direction = max(trend_votes, key=trend_votes.get)
    trend_score = (trend_votes["bull"] - trend_votes["bear"]) / total_weight  # -1 ~ 1
    trend_confidence = max(trend_votes.values()) / total_weight

    # ── 2. ADX 辅助趋势强度 ──────────────────────────
    adx_val = 0.0
    try:
        if len(high) > _ADX_PERIOD * 2 and len(low) > _ADX_PERIOD * 2 and len(close) > _ADX_PERIOD * 2:
            adx_series, plus_di, minus_di = _compute_adx(high, low, close)
            adx_val = float(adx_series.iloc[-1]) if len(adx_series) > 0 else 0.0
    except Exception:
        adx_val = 0.0

    # ADX 修正趋势强度
    has_trend = adx_val > _ADX_TREND_THRESHOLD
    if has_trend and trend_direction != "flat":
        trend_confidence = min(1.0, trend_confidence * 1.2)
    elif not has_trend and trend_direction != "flat":
        trend_confidence = trend_confidence * 0.8  # ADX 不支持趋势时降低信心

    # ── 3. 波动率检测（EWMA + 分位数 + 绝对值） ──────
    ewma_vol = _ewma_volatility(rets)
    current_vol = float(ewma_vol.iloc[-1]) if len(ewma_vol) > 0 else 0.0

    # 历史分位数
    rolling_vol = rets.rolling(20).std() * np.sqrt(252)
    vol_history = rolling_vol.dropna()
    if len(vol_history) > 20:
        # P2：高波阈值以 sector 自身历史 80% 分位为主，`_VOL_HIGH_FLOOR` 绝对下限兜底
        # （近恒定序列 q80 极小 → 阈值≈current → vol_score 虚高误判 high_vol，用 0.15 兜底）。
        # 修复原 `max(quantile, _VOL_ABSOLUTE_HIGH=0.40)` 使低波 sector（如化工链
        # vol_80≈0.15 < 0.40）永远到不了 high_vol、波动率相对定位失效；低波阈值保留
        # 原 `min(quantile, _VOL_ABSOLUTE_LOW)` 兜底（防中等波动序列误判 low_vol）。
        effective_high = float(max(vol_history.quantile(_VOL_HIGH_PERCENTILE), _VOL_HIGH_FLOOR))
        effective_low = float(min(vol_history.quantile(_VOL_LOW_PERCENTILE), _VOL_ABSOLUTE_LOW))
    else:
        effective_high = _VOL_ABSOLUTE_HIGH
        effective_low = _VOL_ABSOLUTE_LOW

    # 波动率分位数
    vol_percentile = float((vol_history <= current_vol).mean()) if len(vol_history) > 2 else 0.5

    # 波动率得分（0~1，越高越波动）
    if current_vol >= effective_high:
        vol_score = min(1.0, (current_vol - effective_high) / (effective_high + 1e-6) + 0.8)
    elif current_vol <= effective_low:
        vol_score = current_vol / (effective_low + 1e-6) * 0.3
    else:
        vol_score = 0.3 + 0.5 * (current_vol - effective_low) / (effective_high - effective_low + 1e-6)
    vol_score = float(np.clip(vol_score, 0.0, 1.0))

    # ── 4. 成交量比率 ────────────────────────────────
    volume = ohlcv["volume"].fillna(0)
    vol_ma = float(volume.rolling(20).mean().iloc[-1])
    vol_ratio = float(volume.iloc[-1]) / vol_ma if vol_ma > 1e-12 else 1.0

    # ── 5. 广度（收益率自相关） ──────────────────────
    breadth = float(rets.autocorr()) if len(rets) > 2 else 0.0

    features: dict = {
        "trend_short": round(trend_short, 6),
        "trend_medium": round(trend_medium, 6),
        "trend_long": round(trend_long, 6),
        "trend_votes": {k: round(v, 1) for k, v in trend_votes.items()},
        "trend_direction": trend_direction,
        "trend_score": round(trend_score, 4),
        "adaptive_threshold": round(adaptive_threshold, 4),
        "adx": round(adx_val, 2),
        "volatility_ewma": round(current_vol, 6),
        "volatility_rolling": round(float(rolling_vol.iloc[-1]) if len(rolling_vol) > 0 else 0.0, 6),
        "vol_percentile": round(vol_percentile, 4),
        "vol_score": round(vol_score, 4),
        "vol_high_threshold": round(effective_high, 6),
        "vol_low_threshold": round(effective_low, 6),
        "volume_ratio": round(vol_ratio, 4),
        "breadth": round(breadth, 4),
    }

    # ── 6. 软投票决定制度 ────────────────────────────
    # 趋势得分: trend_score (-1~1)
    # 波动得分: vol_score (0~1)
    # 综合得分: 趋势权重 * trend_score + 波动惩罚

    regime: str
    confidence: float

    # 有趋势的熊市（趋势得分显著为负，且波动不过高）
    if trend_score < -0.3 and vol_score < 0.7:
        regime = "bear"
        confidence = abs(trend_score) * (1.0 - vol_score * 0.3)
        if has_trend:
            confidence = min(1.0, confidence * 1.15)
    # 有趋势的牛市（趋势得分显著为正，且波动不过高）
    elif trend_score > 0.3 and vol_score < 0.7:
        regime = "bull"
        confidence = trend_score * (1.0 - vol_score * 0.3)
        if has_trend:
            confidence = min(1.0, confidence * 1.15)
    # 高波动（波动得分高）
    elif vol_score > 0.7:
        regime = "high_vol"
        confidence = vol_score
    # 低波动且无趋势
    elif vol_score < 0.35 and abs(trend_score) < 0.3:
        regime = "low_vol"
        confidence = 1.0 - vol_score
    # 默认震荡
    else:
        regime = "oscillate"
        confidence = 0.5

    confidence = float(np.clip(confidence, 0.05, 0.99))

    # ── 7. 制度平滑 ──────────────────────────────────
    if prev_regime is not None:
        prev_r = prev_regime["regime"]
        prev_c = prev_regime["confidence"]
        if regime == prev_r:
            confidence = max(confidence, prev_c * _REGIME_PERSISTENCE_FACTOR)
        else:
            if confidence < prev_c * (1 - _REGIME_PERSISTENCE_FACTOR):
                regime = prev_r
                confidence = prev_c * _REGIME_PERSISTENCE_FACTOR

    return MarketRegime(
        regime=regime,
        confidence=round(confidence, 4),
        detected_at=datetime.now().isoformat(),
        features=features,
        method="rule",
        regime_probs=build_rule_regime_probs(trend_score, vol_score),
    )


def _compute_current_vol_estimate(rets: pd.Series) -> float:
    """计算当前波动率估计值（用于自适应阈值）。"""
    try:
        rolling_vol = rets.rolling(20).std() * np.sqrt(252)
        vol = float(rolling_vol.iloc[-1]) if len(rolling_vol) > 0 else 0.15
        return float(np.clip(vol, 0.05, 0.60))
    except Exception:
        return 0.15


def high_vol_premise_check(
    ohlcv: pd.DataFrame,
    *,
    vol_window: int = _VOL_HISTORY_DAYS,
    vol_min_percentile: float = 0.5,
    ewma_span: int = 30,
) -> dict[str, Any]:
    """high_vol 标签前提交叉验证（plans/54 P0-3 落地，GAP-155）。

    以规则法（_detect_rule 波动率段）同口径复核 high_vol 标签的市场前提——
    "波动结构是否仍在"（监控前提而非结果）：
      - absolute 判据: 当前 EWMA vol ≥ 历史 q80（_VOL_HIGH_PERCENTILE，_VOL_HIGH_FLOOR 兜底）；
      - relative 判据: 当前 20d 波动分位（固定 vol_window 窗口）≥ vol_min_percentile；
    任一成立即前提有效（防误杀）；数据不足 → 前提视为健康（不误报）。

    Args:
        ohlcv: 含 open/high/low/close/volume 列的 DataFrame（DatetimeIndex）。
        vol_window: 波动分位历史窗口（默认 _VOL_HISTORY_DAYS=252）。
        vol_min_percentile: 相对分位下限（默认 0.5，中位数以上）。
        ewma_span: EWMA 波动跨度（默认 30，对齐 _ewma_volatility）。

    Returns:
        {"ok": bool, "ewma_vol": float, "eff_high": float,
         "vol_percentile": float, "reason": str}——ok=True 表示前提有效。
    """
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return {"ok": True, "ewma_vol": 0.0, "eff_high": 0.0, "vol_percentile": 0.5, "reason": "insufficient"}
    close = ohlcv["close"].dropna()
    if len(close) < 20:
        return {"ok": True, "ewma_vol": 0.0, "eff_high": 0.0, "vol_percentile": 0.5, "reason": "insufficient"}
    rets = close.pct_change().dropna()
    ewma_vol = _ewma_volatility(rets, span=ewma_span)
    current_ewma = float(ewma_vol.iloc[-1]) if len(ewma_vol) > 0 else 0.0
    rolling_vol = rets.rolling(20).std() * np.sqrt(252)
    vol_h = rolling_vol.dropna()
    if vol_window > 0:
        vol_h = vol_h.iloc[-vol_window:]
    if len(vol_h) > 20:
        eff_high = float(max(vol_h.quantile(_VOL_HIGH_PERCENTILE), _VOL_HIGH_FLOOR))
    else:
        eff_high = float(_VOL_ABSOLUTE_HIGH)
    roll_cur = float(rolling_vol.iloc[-1]) if not rolling_vol.empty else 0.0
    vol_percentile = float((vol_h <= roll_cur).mean()) if len(vol_h) > 2 else 0.5
    ewma_ok = current_ewma >= eff_high
    pct_ok = vol_percentile >= vol_min_percentile
    ok = bool(ewma_ok or pct_ok)
    parts = []
    if not ewma_ok:
        parts.append(f"EWMA vol {current_ewma:.3f}<q80 {eff_high:.3f}")
    if not pct_ok:
        parts.append(f"vol 分位 {vol_percentile:.2f}<{vol_min_percentile:.2f}")
    reason = "premise_ok" if ok else "premise_lost: " + "; ".join(parts)
    return {
        "ok": ok,
        "ewma_vol": round(current_ewma, 6),
        "eff_high": round(eff_high, 6),
        "vol_percentile": round(vol_percentile, 4),
        "reason": reason,
    }


# ─── SectorRegimeSelector ─────────────────────────────────


class SectorRegimeSelector:
    """产业链级市场制度检测器。

    对每个产业链独立检测市场制度，避免信号抵消。

    参数:
        lookback_days: 趋势斜率计算的回看天数（默认 60）。
    """

    def __init__(self, lookback_days: int = 60, use_hmm: bool = True) -> None:
        self._selectors: dict[str, RegimeAwareSelector] = {}
        self._variety_selectors: dict[str, RegimeAwareSelector] = {}
        self.lookback_days = lookback_days
        self._use_hmm = use_hmm

    def detect_all(
        self,
        panel: dict[str, pd.DataFrame],
        sector_map: dict[str, list[str]] | None = None,
    ) -> dict[str, MarketRegime]:
        """对每个产业链独立检测市场制度。

        参数:
            panel:      品种行情面板 (symbol → OHLCV DataFrame)。
            sector_map: 产业链映射 {产业链名 → [品种代码列表]}。
                        默认使用 FUTURES_SECTOR_MAP。

        返回:
            dict[产业链名, MarketRegime] — 每个产业链的检测结果。
            无足够数据的产业链不在结果中。
        """
        if sector_map is None:
            from fts.data_futures import FUTURES_SECTOR_MAP as _FSM

            sector_map = _FSM
        if not panel:
            return {}

        result: dict[str, MarketRegime] = {}
        for sector, symbols in sector_map.items():
            sector_ohlcv = self._build_sector_ohlcv(panel, symbols)
            if sector_ohlcv.empty:
                continue
            if sector not in self._selectors:
                # use_hmm=False 时同时禁用 multi_hmm 和 msm，只保留规则方法
                self._selectors[sector] = RegimeAwareSelector(
                    self.lookback_days,
                    use_hmm=self._use_hmm,
                    use_multi_hmm=self._use_hmm,
                    use_msm=False,
                )
            result[sector] = self._selectors[sector].detect(sector_ohlcv)
        return result

    def compute_alignment(
        self,
        panel: dict[str, pd.DataFrame],
        sector_regimes: dict[str, MarketRegime],
        sector_map: dict[str, list[str]] | None = None,
    ) -> dict[str, float]:
        """计算品种与所属产业链制度的对齐度。

        对每个品种独立检测其市场制度，与所属产业链的综合制度比较，
        计算对齐度评分（0~1）。对齐度高的品种 => 与产业链趋势一致，
        信号权重应上调；对齐度低的品种 => 偏差于产业链趋势，信号权重应下调。

        参数:
            panel:          品种行情面板 (symbol → OHLCV DataFrame)。
            sector_regimes: detect_all() 返回的产业链制度检测结果。
            sector_map:     产业链映射 {产业链名 → [品种代码列表]}。
                           默认使用 FUTURES_SECTOR_MAP。

        返回:
            dict[品种代码, 对齐度 (0~1)]。
            数据不足时默认返回 0.5。
        """
        if sector_map is None:
            from fts.data_futures import FUTURES_SECTOR_MAP as _FSM

            sector_map = _FSM

        alignment: dict[str, float] = {}

        for sector, symbols in sector_map.items():
            sector_regime = sector_regimes.get(sector)
            if not sector_regime:
                continue
            sector_r = sector_regime["regime"]

            for sym in symbols:
                ohlcv = panel.get(sym)
                if ohlcv is None or len(ohlcv) < 20:
                    alignment[sym] = 0.5  # 数据不足，默认中等对齐度
                    continue

                # 复用 RegimeAwareSelector 检测单品种制度
                if sym not in self._variety_selectors:
                    self._variety_selectors[sym] = RegimeAwareSelector(
                        lookback_days=self.lookback_days,
                        use_hmm=self._use_hmm,
                        use_multi_hmm=self._use_hmm,
                        use_msm=False,
                    )
                variety_regime = self._variety_selectors[sym].detect(ohlcv)
                variety_r = variety_regime["regime"]

                # 计算对齐度
                if variety_r == sector_r:
                    # 制度相同，对齐度 = 两者置信度的乘积
                    alignment[sym] = round(
                        variety_regime["confidence"] * sector_regime["confidence"],
                        4,
                    )
                else:
                    # 制度不同，对齐度 = (1 - |置信度差|) * 0.5
                    conf_diff = abs(variety_regime["confidence"] - sector_regime["confidence"])
                    alignment[sym] = round((1 - conf_diff) * 0.5, 4)

        return alignment

    @staticmethod
    def _build_sector_ohlcv(
        panel: dict[str, pd.DataFrame],
        symbols: list[str],
    ) -> pd.DataFrame:
        """从产业链内品种构建合成 OHLCV（P1-1：等权收益率指数 + 真实波幅）。

        方法（P1-1 修复）：
        - close：各品种先归一化到"首个有效值=100"再取截面均值（等权收益率指数）。
          修复原 close 截面均值被高价品种主导的问题（如 PP0≈8494 的拉动是
          SC0≈555 的 15 倍，而原油 SC0 才是能源链宏观锚点）。
        - high/low：各品种 high/low 用同除数归一化后取截面 max/min，恢复真实波幅。
          修复原 high=low=close 导致 TR 恒 0、ADX 恒 0 的问题（规则法 ADX>25
          增强分支恒走 0.80 折扣，sector 置信度被系统性压低）。
        - volume：截面和（保留原逻辑）。

        参数:
            panel:   品种行情面板 (symbol → DataFrame)。
            symbols: 产业链内的品种代码列表。

        返回:
            合成 OHLCV DataFrame；品种不足 2 个或数据不足 20 行时返回空 DataFrame。
        """
        close_matrix: dict[str, pd.Series] = {}
        for sym in symbols:
            df = panel.get(sym)
            if df is None or df.empty or "close" not in df.columns:
                continue
            close_matrix[sym] = df["close"]

        if len(close_matrix) < 2:
            return pd.DataFrame()

        close_df = pd.DataFrame(close_matrix)
        # 等权收益率指数：各品种归一化到首值=100 后取截面均值
        first_valid = close_df.apply(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)
        norm_df = close_df.div(first_valid, axis=1) * 100.0
        composite_close = norm_df.mean(axis=1).dropna()

        if len(composite_close) < 20:
            return pd.DataFrame()

        # 真实波幅：high/low 用各品种截面 max/min（同除数归一化，与 close 同单位）
        high_lo: dict[str, pd.Series] = {}
        low_lo: dict[str, pd.Series] = {}
        for sym in symbols:
            df = panel.get(sym)
            if df is None or df.empty:
                continue
            div = first_valid.get(sym)
            if div is None or not np.isfinite(div) or div <= 0:
                continue
            if "high" in df.columns:
                high_lo[sym] = df["high"] / div * 100.0
            if "low" in df.columns:
                low_lo[sym] = df["low"] / div * 100.0
        composite_high: pd.Series
        composite_low: pd.Series
        if high_lo:
            composite_high = pd.DataFrame(high_lo).max(axis=1).reindex(composite_close.index).fillna(composite_close)
        else:
            composite_high = composite_close
        if low_lo:
            composite_low = pd.DataFrame(low_lo).min(axis=1).reindex(composite_close.index).fillna(composite_close)
        else:
            composite_low = composite_close

        volume_df = pd.DataFrame(
            {
                sym: df["volume"].reindex(composite_close.index)
                for sym, df in panel.items()
                if sym in symbols and "volume" in df.columns
            }
        )
        composite_volume = volume_df.sum(axis=1).fillna(0)

        return pd.DataFrame(
            {
                "open": composite_close.shift(1).fillna(composite_close),
                "high": composite_high,
                "low": composite_low,
                "close": composite_close,
                "volume": composite_volume,
            },
            index=composite_close.index,
        )


class RegimeAwareSelector:
    """市场制度感知的选择器。

    检测策略（v2.1）:
        - 主检测: MultiHorizonHMMDetector（多周期 HMM 集成，P1.2）
        - 次检测: HMMRegimeDetector（单周期 HMM，状态映射稳定 P1.3）
        - 第三检测: MSMRegimeDetector（马尔可夫切换模型，P3.1，默认关闭）
        - 回退: 规则方法（多周期加权趋势投票 + 分位数/绝对波动率）
        - 最后兜底: oscillate/0.5

    参数:
        lookback_days: 趋势斜率计算的回看天数（默认 60）。
        use_hmm:       是否启用单周期 HMM 检测（默认 True）。
        use_multi_hmm: 是否启用多周期 HMM 集成检测（默认 True）。
        use_msm:       是否启用 MSM 检测（默认 False，P3.1 原型）。
    """

    def __init__(
        self,
        lookback_days: int = 60,
        use_hmm: bool = True,
        use_multi_hmm: bool = True,
        use_msm: bool = False,
        observe_days: int = 0,
    ) -> None:
        self.lookback_days = lookback_days
        self._profiles: dict[str, RegimeFactorProfile] = {}
        self._prev_regime: MarketRegime | None = None
        # plans/54 P1-3: 观察期机制（0=关闭兼容现状；>0 时状态跳变须连续保持 N 次才切换）
        self.observe_days = int(observe_days)
        self._observe_candidate: str | None = None
        self._observe_count: int = 0

        # 单周期 HMM 检测器
        self._hmm_detector = HMMRegimeDetector() if use_hmm and _HMM_AVAILABLE else None
        self._use_hmm = use_hmm and _HMM_AVAILABLE

        # P1.2: 多周期 HMM 集成检测器（主检测）
        self._multi_hmm: MultiHorizonHMMDetector | None = None
        self._use_multi_hmm = False
        if use_multi_hmm and _HMM_AVAILABLE:
            try:
                self._multi_hmm = MultiHorizonHMMDetector()
                self._use_multi_hmm = True
            except Exception:
                self._multi_hmm = None

        # P3.1: MSM 马尔可夫切换模型（默认关闭，原型）
        self._msm: MSMRegimeDetector | None = None
        self._use_msm = False
        if use_msm and _MSM_AVAILABLE:
            try:
                self._msm = MSMRegimeDetector()
                self._use_msm = True
            except Exception:
                self._msm = None
        self._msm_fitted = False

    # ── 检测 ──────────────────────────────────────────────

    def detect(self, ohlcv: pd.DataFrame) -> MarketRegime:
        """从 OHLCV 数据检测当前市场制度（v2.1 — 多检测器集成）。

        检测流程（按优先级）:
            1. MultiHorizonHMMDetector（多周期 HMM 集成，P1.2）
            2. MSMRegimeDetector（马尔可夫切换模型，P3.1，仅 use_msm=True 时）
            3. HMMRegimeDetector（单周期 HMM + 状态映射稳定 P1.3）
            4. 规则方法（多周期加权趋势投票 + 分位数/绝对波动率）
            5. 均失败时返回 oscillate/0.5

        参数:
            ohlcv: 含 open/high/low/close/volume 列的 DataFrame，DatetimeIndex。

        返回:
            MarketRegime — 制度名、置信度、检测时间、特征字典、检测方法。
        """
        # ── 空/不足 20 行 → 兜底 ─────────────────────────
        if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
            result: MarketRegime | None = MarketRegime(
                regime="oscillate",
                confidence=0.5,
                detected_at=datetime.now().isoformat(),
                features={},
                method="fallback",
                regime_probs={"oscillate": 1.0},
            )
            self._prev_regime = result
            assert result is not None
            return result

        close = ohlcv["close"].dropna()
        if len(close) < 20:
            result = MarketRegime(
                regime="oscillate",
                confidence=0.5,
                detected_at=datetime.now().isoformat(),
                features={},
                method="fallback",
                regime_probs={"oscillate": 1.0},
            )
            self._prev_regime = result
            assert result is not None
            return result

        # ── 1. 主检测: MultiHorizonHMMDetector（P1.2） ──
        result = None
        if self._use_multi_hmm and self._multi_hmm is not None:
            try:
                regime, conf, feats = self._multi_hmm.predict(ohlcv)
                if regime != "unknown" and conf >= 0.3:
                    result = MarketRegime(
                        regime=regime,
                        confidence=round(conf, 4),
                        detected_at=datetime.now().isoformat(),
                        features=feats,
                        method="multi_hmm",
                        regime_probs={},
                    )
            except Exception:
                pass  # 多周期 HMM 失败，尝试下一个

        # ── 2. MSMRegimeDetector（P3.1，仅 use_msm=True） ──
        if result is None and self._use_msm and self._msm is not None:
            try:
                if not self._msm_fitted:
                    self._msm_fitted = self._msm.fit(ohlcv)
                if self._msm_fitted:
                    regime, conf, feats = self._msm.predict(ohlcv)
                    if regime != "unknown" and conf >= 0.3:
                        result = MarketRegime(
                            regime=regime,
                            confidence=round(conf, 4),
                            detected_at=datetime.now().isoformat(),
                            features=feats,
                            method="msm",
                            regime_probs={},
                        )
            except Exception:
                pass  # MSM 失败，尝试下一个

        # ── 3. 单周期 HMM 检测 + 状态映射稳定（P1.3） ──
        if result is None and self._use_hmm and self._hmm_detector is not None:
            try:
                self._hmm_detector.maybe_refit(ohlcv)
                hmm_regime, hmm_conf, hmm_features = self._hmm_detector.predict(ohlcv)
                if hmm_regime != "unknown" and hmm_conf >= 0.3:
                    result = MarketRegime(
                        regime=hmm_regime,
                        confidence=round(hmm_conf, 4),
                        detected_at=datetime.now().isoformat(),
                        features=hmm_features,
                        method="hmm",
                        regime_probs={},
                    )
            except Exception:
                pass  # HMM 失败，回退到规则方法

        # ── 4. 回退: 规则方法 ──────────────────────────
        if result is None:
            result = _detect_by_rule(ohlcv, self._prev_regime)

        # ── 28 补充: 将 HMM/MSM 路径 features 内的 regime_probs 提升到顶层 ──
        # （multi_hmm/hmm/msm 的 predict 将 regime_probs 放入 features，此处统一提升，
        #   使 regime blend / 熵标定可消费；rule/fallback 路径已在构造时直接输出）
        if result is not None and not result.get("regime_probs"):
            _feats_rp = (result.get("features") or {}).get("regime_probs")
            if isinstance(_feats_rp, dict) and _feats_rp:
                result["regime_probs"] = _feats_rp

        # ── plans/54 P1-3: 观察期机制（状态跳变不立即切换） ──
        # 文档 §7.1："跳变违背持续性，默认怀疑需证据"——候选新制度须连续
        # observe_days 次保持才确认切换；观察期内维持旧制度（不放大仓位）。
        # 与既有概率平滑（0.7 保留）/防抖（同日）互补；observe_days=0 关闭（兼容现状）。
        if (
            self.observe_days > 0
            and result is not None
            and self._prev_regime is not None
            and result["regime"] != self._prev_regime["regime"]
        ):
            if self._observe_candidate == result["regime"]:
                self._observe_count += 1
            else:
                self._observe_candidate = result["regime"]
                self._observe_count = 1
            if self._observe_count < self.observe_days:
                # 观察期内维持旧制度（保留置信度，标记 observed 可追溯）
                result = dict(self._prev_regime)
                result["observed"] = True
                result["candidate_regime"] = self._observe_candidate
                result["observe_count"] = self._observe_count
            else:
                # 连续达标 → 确认切换（清空观察状态）
                self._observe_candidate = None
                self._observe_count = 0
                result["observed"] = True
                result["confirmed"] = True
        elif result is not None:
            self._observe_candidate = None
            self._observe_count = 0

        # ── 更新上次结果 ────────────────────────────────
        self._prev_regime = result
        return result

    # ── 因子表现记录 ──────────────────────────────────────

    def profile_factor(
        self,
        factor_id: str,
        history: dict[str, RegimePerformance],
    ) -> None:
        """记录因子在各制度下的历史表现。

        参数:
            factor_id: 因子唯一标识。
            history:   regime 名称 → 表现指标（ic_mean, sharpe, n_windows）。
        """
        self._profiles[factor_id] = RegimeFactorProfile(
            factor_id=factor_id,
            regime_performance=history,
        )

    # ── 因子选择 ──────────────────────────────────────────

    def select_factors(
        self,
        regime: MarketRegime,
        elite_pool: list[dict],
    ) -> list[dict]:
        """根据当前制度筛选精英因子。

        选择逻辑:
            - 有 profile 数据的因子：IC_mean > 0 或 sharpe > 0 才保留
            - 无 profile 数据的因子：默认保留（中性权重）
            - 空 elite_pool：返回空列表

        参数:
            regime:     detect() 返回的当前制度。
            elite_pool: 精英因子列表，每项至少含 factor_id 键。

        返回:
            筛选后的因子列表。
        """
        current_regime = regime["regime"]
        result: list[dict] = []

        for factor in elite_pool:
            fid = factor.get("factor_id", "")
            profile = self._profiles.get(fid)
            if profile is not None:
                perf = profile.get("regime_performance", {}).get(current_regime)
                if perf is not None:
                    ic_mean = perf.get("ic_mean", 0.0)
                    sharpe = perf.get("sharpe", 0.0)
                    if ic_mean > 0 or sharpe > 0:
                        result.append(factor)
                else:
                    result.append(factor)
            else:
                result.append(factor)

        return result

    # ── 报告 ──────────────────────────────────────────────

    def regime_report(self) -> str:
        """生成当前制度与各因子表现的人类可读报告。

        返回:
            多行字符串报告。
        """
        lines: list[str] = [
            "=== RegimeAwareSelector 报告 ===",
            f"  已记录的因子数: {len(self._profiles)}",
        ]

        for fid, profile in self._profiles.items():
            perfs = profile.get("regime_performance", {})
            lines.append(f"  因子 [{fid}]:")
            for regime_name, perf in perfs.items():
                ic = perf.get("ic_mean", float("nan"))
                sp = perf.get("sharpe", float("nan"))
                nw = perf.get("n_windows", 0)
                lines.append(f"    {regime_name}: IC={ic:.4f}, Sharpe={sp:.4f}, windows={nw}")

        if not self._profiles:
            lines.append("  （无因子表现数据）")

        return "\n".join(lines)


# ─── P4.2: 制度迁移预警 ──────────────────────────────────


class RegimeTransitionWarner:
    """制度迁移预警系统（P4.2）。

    在上次制度的基础上，监控以下信号：
      1. 后验概率熵: H = -sum(p * log(p))，熵 > 0.8 时触发预警
      2. 转移概率突变: 转移矩阵对角元下降 > 20% 时预警
      3. 特征分布偏移: 新特征分布与参考分布的 KL 散度 > 阈值

    预警等级:
      - 黄色: 仅记录日志，不改变策略
      - 橙色: 降低置信度，缩小仓位
      - 红色: 强行切换回规则方法，人工介入

    用法:
        warner = RegimeTransitionWarner()
        level = warner.evaluate(probs, current_regime, transition_matrix, features)
    """

    # 预警阈值
    ENTROPY_YELLOW: float = 0.6
    ENTROPY_ORANGE: float = 0.8
    ENTROPY_RED: float = 0.95

    TRANSITION_DROP_YELLOW: float = 0.10  # 对角元下降 10%
    TRANSITION_DROP_ORANGE: float = 0.20  # 对角元下降 20%
    TRANSITION_DROP_RED: float = 0.35  # 对角元下降 35%

    KL_DIVERGENCE_YELLOW: float = 0.5
    KL_DIVERGENCE_ORANGE: float = 1.0
    KL_DIVERGENCE_RED: float = 2.0

    def __init__(self, n_features: int = 2) -> None:
        self._prev_transition: np.ndarray | None = None
        self._ref_feature_mean: np.ndarray | None = None
        self._ref_feature_std: np.ndarray | None = None
        self._n_features = n_features
        self._call_count = 0
        self._last_alert: str | None = None  # yellow/orange/red

    def evaluate(
        self,
        probs: np.ndarray | list[float],
        current_regime: str,
        transition_matrix: np.ndarray | None = None,
        features: np.ndarray | None = None,
    ) -> str:
        """评估当前制度迁移风险，返回预警等级。

        参数:
            probs:             后验概率数组（每个状态的概率）。
            current_regime:    当前制度名称。
            transition_matrix: 当前 HMM 转移矩阵（可选）。
            features:          当前特征向量（可选，用于分布偏移检测）。

        返回:
            "none" / "yellow" / "orange" / "red"
        """
        self._call_count += 1
        probs_arr = np.asarray(probs, dtype=np.float64)
        probs_arr = np.clip(probs_arr, 1e-12, 1.0)
        probs_arr /= probs_arr.sum()

        # ── 信号1: 后验概率熵 ──
        entropy: float = float(-np.sum(probs_arr * np.log(probs_arr)))
        # 归一化: 对 N 个状态，最大熵为 log(N)
        n_states = len(probs_arr)
        max_entropy = np.log(max(n_states, 2))
        norm_entropy = entropy / max_entropy if max_entropy > 0 else 0.0

        # ── 信号2: 转移概率突变 ──
        trans_drop = 0.0
        if transition_matrix is not None and self._prev_transition is not None:
            # 检查对角元下降
            if transition_matrix.shape[0] == self._prev_transition.shape[0]:
                curr_diag = np.diag(transition_matrix)
                prev_diag = np.diag(self._prev_transition)
                # 避免除以零
                denom = np.maximum(prev_diag, 1e-6)
                drop = (prev_diag - curr_diag) / denom
                trans_drop = float(np.max(np.clip(drop, 0, 1)))
        if transition_matrix is not None:
            self._prev_transition = transition_matrix.copy()

        # ── 信号3: 特征分布偏移 ──
        kl_div = 0.0
        if features is not None and self._ref_feature_mean is not None and self._ref_feature_std is not None:
            # 简化的 KL 散度估计（假设高斯分布）
            feat = np.asarray(features, dtype=np.float64).flatten()
            min_len = min(len(feat), len(self._ref_feature_mean))
            if min_len > 0:
                diff = feat[:min_len] - self._ref_feature_mean[:min_len]
                var = np.maximum(self._ref_feature_std[:min_len] ** 2, 1e-10)
                kl_div = float(np.mean(diff**2 / var) / 2.0)

        # 更新参考分布
        if features is not None and self._ref_feature_mean is None:
            feat = np.asarray(features, dtype=np.float64).flatten()
            self._ref_feature_mean = feat.copy()
            self._ref_feature_std = np.ones_like(feat) * 0.1
        elif features is not None and self._ref_feature_mean is not None:
            # 指数衰减更新参考分布
            alpha = 0.05
            feat = np.asarray(features, dtype=np.float64).flatten()
            min_len = min(len(feat), len(self._ref_feature_mean))
            if min_len > 0:
                self._ref_feature_mean[:min_len] = (1 - alpha) * self._ref_feature_mean[:min_len] + alpha * feat[
                    :min_len
                ]

        # ── 综合判定 ──
        red = (
            (norm_entropy >= self.ENTROPY_RED)
            or (trans_drop >= self.TRANSITION_DROP_RED)
            or (kl_div >= self.KL_DIVERGENCE_RED)
        )
        orange = (
            (norm_entropy >= self.ENTROPY_ORANGE)
            or (trans_drop >= self.TRANSITION_DROP_ORANGE)
            or (kl_div >= self.KL_DIVERGENCE_ORANGE)
        )
        yellow = (
            (norm_entropy >= self.ENTROPY_YELLOW)
            or (trans_drop >= self.TRANSITION_DROP_YELLOW)
            or (kl_div >= self.KL_DIVERGENCE_YELLOW)
        )

        if red:
            self._last_alert = "red"
            logger.warning(
                "制度迁移预警: RED (entropy=%.4f, trans_drop=%.4f, kl=%.4f)", norm_entropy, trans_drop, kl_div
            )
            return "red"
        if orange:
            self._last_alert = "orange"
            logger.info(
                "制度迁移预警: ORANGE (entropy=%.4f, trans_drop=%.4f, kl=%.4f)", norm_entropy, trans_drop, kl_div
            )
            return "orange"
        if yellow:
            self._last_alert = "yellow"
            logger.debug(
                "制度迁移预警: YELLOW (entropy=%.4f, trans_drop=%.4f, kl=%.4f)", norm_entropy, trans_drop, kl_div
            )
            return "yellow"

        self._last_alert = None
        return "none"

    @property
    def last_alert(self) -> str | None:
        return self._last_alert

    def reset(self) -> None:
        """重置状态（用于测试）。"""
        self._prev_transition = None
        self._ref_feature_mean = None
        self._ref_feature_std = None
        self._call_count = 0
        self._last_alert = None


# ─── P4.3: 自适应参数调整 ────────────────────────────────


class AdaptiveRegimeConfig:
    """自适应参数调整模块（P4.3）。

    根据历史表现动态调整制度检测阈值参数，
    每 20 个交易日重新评估一次。

    用法:
        config = AdaptiveRegimeConfig()
        config.record(regime, confidence, forward_return)
        thresholds = config.get_thresholds()  # 获取当前最优阈值
    """

    DEFAULT_THRESHOLDS: dict[str, float] = {
        "trend_slope_bull": 0.0001,  # 看涨趋势斜率阈值
        "trend_slope_bear": -0.0001,  # 看跌趋势斜率阈值
        "volatility_high": 0.02,  # 高波动阈值
        "volatility_low": 0.005,  # 低波动阈值
        "confidence_min": 0.3,  # 最低置信度
        "entropy_yellow": 0.6,  # 迁移预警黄色阈值
        "entropy_orange": 0.8,  # 迁移预警橙色阈值
    }

    # 可调参数搜索空间
    PARAM_GRID: dict[str, list[float]] = {
        "trend_slope_bull": [0.00005, 0.0001, 0.0002, 0.0005],
        "trend_slope_bear": [-0.0005, -0.0002, -0.0001, -0.00005],
        "volatility_high": [0.015, 0.02, 0.025, 0.03],
        "volatility_low": [0.003, 0.005, 0.008, 0.01],
        "confidence_min": [0.2, 0.3, 0.4, 0.5],
    }

    def __init__(
        self,
        eval_interval: int = 20,
        lookback_windows: int = 10,
    ) -> None:
        self.eval_interval = eval_interval
        self.lookback_windows = lookback_windows
        self._thresholds: dict[str, float] = dict(self.DEFAULT_THRESHOLDS)
        self._history: list[dict[str, Any]] = []
        self._eval_count = 0

    def record(
        self,
        regime: str,
        confidence: float,
        forward_return: float,
    ) -> None:
        """记录一次制度检测结果和后续收益。

        参数:
            regime:         检测到的制度。
            confidence:     检测置信度。
            forward_return: 后续收益率（如次日收益）。
        """
        self._history.append(
            {
                "regime": regime,
                "confidence": confidence,
                "forward_return": forward_return,
                "timestamp": pd.Timestamp.now(),
            }
        )
        self._eval_count += 1

        # 每 eval_interval 次重新优化
        if self._eval_count % self.eval_interval == 0:
            self._reoptimize()

    def get_thresholds(self) -> dict[str, float]:
        """获取当前最优阈值。"""
        return dict(self._thresholds)

    def _reoptimize(self) -> None:
        """基于历史表现重新优化阈值参数。

        使用网格搜索找到使制度-收益匹配度最高的阈值组合。
        """
        if len(self._history) < self.eval_interval:
            return

        recent = self._history[-self.eval_interval :]

        best_score = -float("inf")
        best_params: dict[str, float] = dict(self.DEFAULT_THRESHOLDS)

        # 只对部分关键参数进行网格搜索（避免组合爆炸）
        param_keys = list(self.PARAM_GRID.keys())
        param_values = list(self.PARAM_GRID.values())

        from itertools import product

        total = 1
        for v in param_values:
            total *= len(v)
        if total > 500:
            # 采样搜索
            param_values = [v[:: max(1, len(v) // 3)] for v in param_values]

        for combo in product(*param_values):
            params = dict(zip(param_keys, combo))
            score = self._score_params(params, recent)
            if score > best_score:
                best_score = score
                best_params = {k: float(v) for k, v in params.items()}

        # 更新阈值（仅更新搜索过的参数）
        for k, val in best_params.items():
            self._thresholds[k] = float(val)

        logger.info(
            "AdaptiveRegimeConfig 已更新阈值 (score=%.4f): %s",
            best_score,
            {k: f"{val:.5f}" for k, val in best_params.items()},
        )

    def _score_params(
        self,
        params: dict[str, float],
        history: list[dict[str, Any]],
    ) -> float:
        """评估一组阈值参数的表现。

        策略: 预测为 bull 时做多，bear 时做空，其他空仓。
        评分: 夏普比率。
        """
        if not history:
            return 0.0

        from collections import Counter

        regime_counts = Counter(h["regime"] for h in history)
        n_entries = len(history)

        # 使用置信度加权
        weighted_returns = 0.0
        total_weight = 0.0

        for h in history:
            regime = h["regime"]
            confidence = h["confidence"]
            ret = h["forward_return"]

            if regime == "bull" and confidence >= params["confidence_min"]:
                weight = confidence
                weighted_returns += weight * ret
                total_weight += weight
            elif regime == "bear" and confidence >= params["confidence_min"]:
                weight = confidence
                weighted_returns += weight * (-ret)  # 做空
                total_weight += weight
            # oscillate/high_vol → 空仓

        if total_weight == 0:
            return 0.0

        # 评分 = 制度分布均匀度 × 平均收益
        # 均匀度用归一化熵
        n_regimes = len(regime_counts)
        if n_regimes <= 1:
            diversity = 0.0
        else:
            probs = np.array([c / n_entries for c in regime_counts.values()])
            entropy: float = float(-np.sum(probs * np.log(np.clip(probs, 1e-10, 1.0))))
            diversity = entropy / np.log(n_regimes)

        avg_return = weighted_returns / total_weight
        score = avg_return * 100 + diversity * 0.5
        return float(score)

    def reset(self) -> None:
        """重置历史数据（用于测试）。"""
        self._history.clear()
        self._eval_count = 0
        self._thresholds = dict(self.DEFAULT_THRESHOLDS)
