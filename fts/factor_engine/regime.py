"""
fts.factor_engine.regime — 市场制度感知与因子选择性激活。

检测当前市场制度（bull/bear/oscillate/high_vol/low_vol），
记录因子在各 regime 下的历史表现，
仅选择在当前制度下有效的因子参与组合构建。

检测方法（v2.0 — 机构级增强版）:
  - 主检测: HMM（隐马尔可夫模型），4状态概率化制度识别
  - 回退检测: 多周期加权趋势投票 + 分位数/绝对波动率 + 软投票判定
  - 制度平滑: 转移概率矩阵（HMM）或指数衰减（规则）

用法:
    selector = RegimeAwareSelector()
    regime = selector.detect(ohlcv_df)
    active_factors = selector.select_factors(regime, elite_factors)

版本: v2.0.0
"""

from __future__ import annotations

from datetime import datetime
from typing import TypedDict

import numpy as np
import pandas as pd

# hmmlearn 是可选依赖，HMM 检测失败时自动回退到规则方法
_HMM_AVAILABLE: bool = False
try:
    from hmmlearn import hmm

    _HMM_AVAILABLE = True
except ImportError:
    pass


# ─── 契约 ─────────────────────────────────────────────────

class MarketRegime(TypedDict):
    """市场制度检测结果。"""
    regime: str                # bull / bear / oscillate / high_vol / low_vol
    confidence: float          # 置信度 0~1
    detected_at: str           # ISO 8601
    features: dict             # 检测特征
    method: str                # 检测方法: "hmm" / "rule" / "fallback"


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
_TREND_SHORT_DAYS = 20         # 短期趋势窗口
_TREND_MEDIUM_DAYS = 60        # 中期趋势窗口
_TREND_LONG_DAYS = 200         # 长期趋势窗口
_TREND_THRESHOLD = 0.02        # 基准确率阈值（将被波动率自适应调整）
_TREND_WEIGHTS = {"short": 1, "medium": 2, "long": 3}  # 加权投票权重

# 波动率检测
_VOL_HISTORY_DAYS = 252        # 波动率历史窗口（约 1 年交易日）
_VOL_HIGH_PERCENTILE = 0.80    # 高波动阈值：历史 80% 分位数以上
_VOL_LOW_PERCENTILE = 0.20     # 低波动阈值：历史 20% 分位数以下
_VOL_ABSOLUTE_LOW = 0.10       # 绝对值低波阈值（年化 10%）
_VOL_ABSOLUTE_HIGH = 0.40      # 绝对值高波阈值（年化 40%）

# ADX 指标
_ADX_PERIOD = 14               # ADX 计算周期
_ADX_TREND_THRESHOLD = 25      # ADX > 25 表示有趋势

# 制度平滑（规则方法）
_REGIME_PERSISTENCE_FACTOR = 0.7  # 上次制度置信度保留比例 0~1，越大越平滑

# HMM 参数
_HMM_N_STATES = 4              # 状态数: bull / bear / high_vol / oscillate
_HMM_LOOKBACK = 252            # 训练窗口
_HMM_REFIT_INTERVAL = 20       # 每 N 次调用 refit 一次
_HMM_MIN_DATA = 126            # 最少数据要求（半年交易日）
_HMM_RANDOM_SEED = 42          # 确定性种子


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
    tr = pd.concat([
        (high - low).abs(),
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)

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
    ) -> None:
        self.n_states = n_states
        self.lookback = lookback
        self.refit_interval = refit_interval
        self.min_data = min_data
        self.random_seed = random_seed

        self._model: hmm.GaussianHMM | None = None
        self._state_map: dict[int, str] = {}   # HMM state index → regime name
        self._call_count = 0
        self._last_fit_data_len = 0
        self._is_fitted = False

    # ── 训练 ──────────────────────────────────────────────

    def fit(self, ohlcv: pd.DataFrame) -> bool:
        """在历史数据上训练 HMM 模型。

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

        rets = close.pct_change().dropna().values.reshape(-1, 1)
        if len(rets) < self.min_data:
            return False

        # 构建特征: [收益率, 20d 已实现波动率]
        rets_series = pd.Series(close.pct_change().dropna())
        vol = rets_series.rolling(20).std().fillna(0).values.reshape(-1, 1)
        features = np.column_stack([rets, vol])

        # 取最近 lookback 窗口
        train_features = features[-min(self.lookback, len(features)):]

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
        """根据状态的统计特征推断制度映射。

        方法:
            - 按均值收益率排序
            - 最高收益 → "bull"
            - 最低收益 → "bear"
            - 最高波动（剩余中）→ "high_vol"
            - 剩余 → "oscillate"
        """
        if self._model is None:
            return

        states = self._model.predict(features)
        means = self._model.means_  # shape (n_states, n_features)

        # 计算每个状态的统计量
        state_stats: list[dict] = []
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
            for s in remaining[1:]:
                assignment.append((s["state"], "oscillate"))
                used.add(s["state"])

        self._state_map = dict(assignment)

    # ── 预测 ──────────────────────────────────────────────

    def predict(self, ohlcv: pd.DataFrame) -> tuple[str, float, dict]:
        """预测当前市场制度。

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
        rets_vals = rets.values.reshape(-1, 1)
        vol = rets.rolling(20).std().fillna(0).values.reshape(-1, 1)
        features = np.column_stack([rets_vals, vol])

        try:
            state = int(self._model.predict(features)[-1])
            probs = self._model.predict_proba(features)[-1]
            regime = self._state_map.get(state, "oscillate")
            confidence = float(min(1.0, max(0.0, probs[state])))
            return regime, confidence, {"hmm_state": state, "hmm_probs": probs.tolist()}
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
        )

    close = ohlcv["close"].dropna()
    if len(close) < 20:
        return MarketRegime(
            regime="oscillate",
            confidence=0.5,
            detected_at=datetime.now().isoformat(),
            features={},
            method="fallback",
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
        vol_high_threshold = float(vol_history.quantile(_VOL_HIGH_PERCENTILE))
        vol_low_threshold = float(vol_history.quantile(_VOL_LOW_PERCENTILE))
    else:
        vol_high_threshold = _VOL_ABSOLUTE_HIGH
        vol_low_threshold = _VOL_ABSOLUTE_LOW

    # 综合波动率阈值（分位数 OR 绝对值，取强者）
    effective_high = max(vol_high_threshold, _VOL_ABSOLUTE_HIGH)
    effective_low = min(vol_low_threshold, _VOL_ABSOLUTE_LOW)

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
    )


def _compute_current_vol_estimate(rets: pd.Series) -> float:
    """计算当前波动率估计值（用于自适应阈值）。"""
    try:
        rolling_vol = rets.rolling(20).std() * np.sqrt(252)
        vol = float(rolling_vol.iloc[-1]) if len(rolling_vol) > 0 else 0.15
        return float(np.clip(vol, 0.05, 0.60))
    except Exception:
        return 0.15


# ─── SectorRegimeSelector ─────────────────────────────────

class SectorRegimeSelector:
    """产业链级市场制度检测器。

    对每个产业链独立检测市场制度，避免信号抵消。

    参数:
        lookback_days: 趋势斜率计算的回看天数（默认 60）。
    """

    def __init__(self, lookback_days: int = 60, use_hmm: bool = True) -> None:
        self._selectors: dict[str, RegimeAwareSelector] = {}
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
                self._selectors[sector] = RegimeAwareSelector(self.lookback_days, use_hmm=self._use_hmm)
            result[sector] = self._selectors[sector].detect(sector_ohlcv)
        return result

    @staticmethod
    def _build_sector_ohlcv(
        panel: dict[str, pd.DataFrame],
        symbols: list[str],
    ) -> pd.DataFrame:
        """从产业链内品种构建合成 OHLCV。

        方法：取所有品种 close 的截面均值作为产业链综合价格序列，
        构建合成 OHLCV（open/high/low 用 close 近似，volume 取截面和）。

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
        composite_close = close_df.mean(axis=1).dropna()

        if len(composite_close) < 20:
            return pd.DataFrame()

        volume_df = pd.DataFrame({
            sym: df["volume"].reindex(composite_close.index)
            for sym, df in panel.items()
            if sym in symbols and "volume" in df.columns
        })
        composite_volume = volume_df.sum(axis=1).fillna(0)

        return pd.DataFrame({
            "open": composite_close.shift(1).fillna(composite_close),
            "high": composite_close,
            "low": composite_close,
            "close": composite_close,
            "volume": composite_volume,
        }, index=composite_close.index)


class RegimeAwareSelector:
    """市场制度感知的选择器。

    检测策略（v2.0）:
        - 主检测: HMM（隐马尔可夫模型），概率化制度识别
        - 回退: 规则方法（多周期加权趋势投票 + 分位数/绝对波动率）
        - 两者均失败时返回 oscillate/0.5

    参数:
        lookback_days: 趋势斜率计算的回看天数（默认 60）。
        use_hmm:       是否启用 HMM 检测（默认 True）。
    """

    def __init__(self, lookback_days: int = 60, use_hmm: bool = True) -> None:
        self.lookback_days = lookback_days
        self._profiles: dict[str, RegimeFactorProfile] = {}
        self._prev_regime: MarketRegime | None = None
        self._hmm_detector = HMMRegimeDetector() if use_hmm and _HMM_AVAILABLE else None
        self._use_hmm = use_hmm and _HMM_AVAILABLE

    # ── 检测 ──────────────────────────────────────────────

    def detect(self, ohlcv: pd.DataFrame) -> MarketRegime:
        """从 OHLCV 数据检测当前市场制度（v2.0 机构级增强版）。

        检测流程:
            1. 尝试 HMM 检测（需 hmmlearn 可用 + 数据充足）
            2. 若 HMM 不可用或失败，回退到规则方法
            3. 若规则方法也失败，返回 oscillate/0.5

        参数:
            ohlcv: 含 open/high/low/close/volume 列的 DataFrame，DatetimeIndex。

        返回:
            MarketRegime — 制度名、置信度、检测时间、特征字典、检测方法。
        """
        # ── 空/不足 20 行 → 兜底 ─────────────────────────
        if ohlcv is None or ohlcv.empty or len(ohlcv) < 20:
            result = MarketRegime(
                regime="oscillate",
                confidence=0.5,
                detected_at=datetime.now().isoformat(),
                features={},
                method="fallback",
            )
            self._prev_regime = result
            return result

        close = ohlcv["close"].dropna()
        if len(close) < 20:
            result = MarketRegime(
                regime="oscillate",
                confidence=0.5,
                detected_at=datetime.now().isoformat(),
                features={},
                method="fallback",
            )
            self._prev_regime = result
            return result

        # ── 主检测: HMM ─────────────────────────────────
        result: MarketRegime | None = None
        if self._use_hmm and self._hmm_detector is not None:
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
                    )
            except Exception:
                pass  # HMM 失败，回退到规则方法

        # ── 回退: 规则方法 ──────────────────────────────
        if result is None:
            result = _detect_by_rule(ohlcv, self._prev_regime)

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
                lines.append(
                    f"    {regime_name}: IC={ic:.4f}, Sharpe={sp:.4f}, windows={nw}"
                )

        if not self._profiles:
            lines.append("  （无因子表现数据）")

        return "\n".join(lines)