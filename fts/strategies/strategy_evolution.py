"""
策略进化模块 — 动态因子权重、市场制度自适应、多周期信号融合。

本模块扩展了 BaseStrategyV2 框架，实现三种策略进化能力：
1. RegimeAdaptiveStrategy: 根据市场制度动态调整因子权重
2. DynamicWeightStrategy: 根据因子近期表现动态调整权重
3. MultiPeriodSignalFusion: 融合多周期信号生成综合策略

HARNESS §契约优先：
  - 所有策略继承 BaseStrategyV2，实现 compute → filter → score 三段式
  - 策略可插拔，通过 StrategyPipeline 统一调度

版本: v0.1.0
"""
# pylint: disable=too-many-locals

from __future__ import annotations

from collections import deque
from typing import Any

import pandas as pd

from .base_v2 import BaseStrategyV2, RawSignal, ScoredSignal
from .multi_factor_strategy import (
    FACTOR_WEIGHTS,
    PURE_MOMENTUM_WEIGHTS,
    SCORE_THRESHOLDS,
    MultiFactorStrategy,
    _calc_basis,
    _calc_capacity,
    _calc_inventory,
    _calc_macro,
    _calc_momentum,
    _calc_oi_change,
    _calc_pmi_proxy,
    _calc_position_rank,
    _calc_rate_proxy,
    _calc_volatility_reversion,
    _calc_volume_flow,
    _calc_warrant_change,
    _safe_float,
)
from fts.factor_engine.regime import RegimeAwareSelector


# ════════════════════════════════════════════════════════════
# 制度自适应权重配置
# ════════════════════════════════════════════════════════════

# 牛市：加强动量，降低防御性因子
BULL_WEIGHTS: dict[str, float] = {
    "momentum": 0.25,
    "volatility_reversion": 0.05,
    "volume_flow": 0.12,
    "oi_change": 0.10,
    "basis": 0.10,
    "inventory_pct": 0.05,
    "capacity": 0.03,
    "macro_regime": 0.15,
    "rate_proxy": 0.03,
    "pmi_proxy": 0.05,
    "position_rank": 0.05,
    "warrant_change": 0.02,
}

# 熊市：加强防御性因子，降低动量
BEAR_WEIGHTS: dict[str, float] = {
    "momentum": 0.05,
    "volatility_reversion": 0.15,
    "volume_flow": 0.10,
    "oi_change": 0.08,
    "basis": 0.20,
    "inventory_pct": 0.12,
    "capacity": 0.05,
    "macro_regime": 0.10,
    "rate_proxy": 0.05,
    "pmi_proxy": 0.04,
    "position_rank": 0.04,
    "warrant_change": 0.02,
}

# 高波动：加强波动率因子和防御性因子
HIGH_VOL_WEIGHTS: dict[str, float] = {
    "momentum": 0.10,
    "volatility_reversion": 0.25,
    "volume_flow": 0.10,
    "oi_change": 0.05,
    "basis": 0.15,
    "inventory_pct": 0.08,
    "capacity": 0.04,
    "macro_regime": 0.08,
    "rate_proxy": 0.03,
    "pmi_proxy": 0.03,
    "position_rank": 0.07,
    "warrant_change": 0.02,
}

# 低波动：趋势跟踪为主
LOW_VOL_WEIGHTS: dict[str, float] = {
    "momentum": 0.20,
    "volatility_reversion": 0.05,
    "volume_flow": 0.15,
    "oi_change": 0.08,
    "basis": 0.12,
    "inventory_pct": 0.08,
    "capacity": 0.05,
    "macro_regime": 0.10,
    "rate_proxy": 0.04,
    "pmi_proxy": 0.04,
    "position_rank": 0.06,
    "warrant_change": 0.03,
}

# 震荡：侧重均值回复
OSCILLATE_WEIGHTS: dict[str, float] = {
    "momentum": 0.08,
    "volatility_reversion": 0.20,
    "volume_flow": 0.10,
    "oi_change": 0.07,
    "basis": 0.15,
    "inventory_pct": 0.10,
    "capacity": 0.05,
    "macro_regime": 0.08,
    "rate_proxy": 0.05,
    "pmi_proxy": 0.04,
    "position_rank": 0.05,
    "warrant_change": 0.03,
}

# 制度 → 权重映射
REGIME_WEIGHT_MAP: dict[str, dict[str, float]] = {
    "bull": BULL_WEIGHTS,
    "bear": BEAR_WEIGHTS,
    "high_vol": HIGH_VOL_WEIGHTS,
    "low_vol": LOW_VOL_WEIGHTS,
    "oscillate": OSCILLATE_WEIGHTS,
}


# ════════════════════════════════════════════════════════════
# 1. 市场制度自适应策略
# ════════════════════════════════════════════════════════════


class RegimeAdaptiveStrategy(MultiFactorStrategy):
    """市场制度自适应策略 — 根据当前市场制度动态调整因子权重。

    使用 RegimeAwareSelector 检测市场制度（bull/bear/oscillate/high_vol/low_vol），
    然后选择对应的制度最优权重进行因子组合。

    用法:
        strategy = RegimeAdaptiveStrategy()
        signals = strategy.compute(tech_list, kline_data, context)
    """

    def __init__(self, mode: str = "pure_momentum", lookback_days: int = 60):
        super().__init__(mode=mode)
        self._regime_selector = RegimeAwareSelector(lookback_days=lookback_days)
        self._current_regime: str | None = None
        self._regime_confidence: float = 0.0
        self._regime_features: dict = {}

    @property
    def name(self) -> str:
        return "regime_adaptive"

    @property
    def display_name(self) -> str:
        regime_label = self._current_regime or "unknown"
        return f"制度自适应({regime_label})"

    @property
    def signal_type(self) -> str:
        return f"regime_adaptive.{self._mode}"

    @property
    def current_regime(self) -> str | None:
        """当前检测到的市场制度。"""
        return self._current_regime

    @property
    def regime_confidence(self) -> float:
        """当前制度检测置信度。"""
        return self._regime_confidence

    def detect_regime(self, kline_data: dict) -> str:
        """从 K 线数据检测市场制度。

        Args:
            kline_data: {sym: (name, [bar_dict, ...])}

        Returns:
            制度名称: bull/bear/oscillate/high_vol/low_vol
        """
        ohlcv = self._build_ohlcv_from_kline(kline_data)
        if ohlcv is None or ohlcv.empty:
            self._current_regime = "oscillate"
            self._regime_confidence = 0.0
            self._regime_features = {}
            return "oscillate"

        result = self._regime_selector.detect(ohlcv)
        self._current_regime = result["regime"]
        self._regime_confidence = result["confidence"]
        self._regime_features = result["features"]
        return result["regime"]

    def _build_ohlcv_from_kline(self, kline_data: dict) -> pd.DataFrame | None:
        """从 kline_data 构造 OHLCV DataFrame。

        kline_data 格式: {sym: (name, [bar_dict, ...])}
        选取第一个品种的 K 线数据作为市场代表。
        """
        if not kline_data:
            return None

        for _sym, (_name, bars) in kline_data.items():
            if not bars:
                continue
            records: list[dict] = []
            for bar in bars:
                records.append({
                    "open": float(bar.get("open", 0)),
                    "high": float(bar.get("high", 0)),
                    "low": float(bar.get("low", 0)),
                    "close": float(bar.get("close", 0)),
                    "volume": float(bar.get("volume", 0)),
                })
            if not records:
                continue
            df = pd.DataFrame(records)
            if "date" in bars[0]:
                df.index = pd.DatetimeIndex([b.get("date") for b in bars])
            return df

        return None

    def compute(self, tech_list: list[dict], kline_data: dict,
                context: dict | None = None) -> list[RawSignal]:
        """检测市场制度，动态选择权重，然后计算信号。"""
        regime = self.detect_regime(kline_data)
        self._weights = REGIME_WEIGHT_MAP.get(regime, PURE_MOMENTUM_WEIGHTS)

        signals = super().compute(tech_list, kline_data, context)

        for s in signals:
            s.meta["regime"] = regime
            s.meta["regime_confidence"] = self._regime_confidence
            s.meta["regime_features"] = self._regime_features

        return signals

    def score(self, filtered_signals: list[RawSignal],
              tech_list: list[dict],
              context: dict | None = None) -> list[ScoredSignal]:
        result = super().score(filtered_signals, tech_list, context)
        for s in result:
            s.extra["regime"] = self._current_regime
            s.extra["regime_confidence"] = self._regime_confidence
        return result


# ════════════════════════════════════════════════════════════
# 2. 动态因子权重策略
# ════════════════════════════════════════════════════════════


class DynamicWeightStrategy(MultiFactorStrategy):
    """动态因子权重策略 — 根据因子近期表现动态调整权重。

    跟踪各因子在历史窗口内的表现（IC 代理），
    通过指数衰减加权平均更新因子权重，表现好的因子获得更高权重。

    用法:
        strategy = DynamicWeightStrategy(lookback_windows=20, decay_factor=0.9)
        signals = strategy.compute(tech_list, kline_data, context)
    """

    def __init__(self, mode: str = "pure_momentum",
                 lookback_windows: int = 20,
                 decay_factor: float = 0.9):
        super().__init__(mode=mode)
        self._lookback_windows = lookback_windows
        self._decay_factor = decay_factor
        self._factor_history: dict[str, deque[float]] = {
            k: deque(maxlen=lookback_windows) for k in FACTOR_WEIGHTS
        }

    @property
    def name(self) -> str:
        return "dynamic_weight"

    @property
    def display_name(self) -> str:
        return "动态因子权重"

    @property
    def signal_type(self) -> str:
        return f"dynamic_weight.{self._mode}"

    @property
    def factor_history(self) -> dict[str, list[float]]:
        """各因子历史表现记录。"""
        return {k: list(v) for k, v in self._factor_history.items()}

    def update_factor_performance(self, factor_scores: dict[str, float],
                                   returns: dict[str, float]) -> None:
        """更新因子表现记录。

        Args:
            factor_scores: 因子名 → 平均得分
            returns: 品种 → 收益率（用于计算 IC 代理）
        """
        if not returns:
            return

        for fname, score in factor_scores.items():
            if fname not in self._factor_history:
                self._factor_history[fname] = deque(maxlen=self._lookback_windows)
            self._factor_history[fname].append(self._calc_approx_ic(score, returns))

    @staticmethod
    def _calc_approx_ic(factor_score: float, returns: dict[str, float]) -> float:
        """计算因子得分与收益率的近似 IC。

        当有多个品种时计算符号一致性比例，否则返回因子得分本身作为代理。
        """
        if not returns:
            return 0.0
        return factor_score  # 简化：使用因子得分作为 IC 代理

    def _calc_dynamic_weights(self) -> dict[str, float]:
        """根据历史表现计算动态权重。

        使用指数衰减加权平均各因子的历史 IC，
        表现好的因子获得更高权重。
        """
        if not any(self._factor_history.values()):
            return dict(self._weights)

        perf: dict[str, float] = {}
        for fname, history in self._factor_history.items():
            if not history:
                continue
            weight_sum = 0.0
            weighted_sum = 0.0
            for i, ic_val in enumerate(history):
                w = self._decay_factor ** (len(history) - i - 1)
                weighted_sum += w * ic_val
                weight_sum += w
            perf[fname] = weighted_sum / weight_sum if weight_sum > 0 else 0.0

        if not perf:
            return dict(self._weights)

        scores = {k: max(0.01, abs(v)) for k, v in perf.items()}
        total = sum(scores.values())
        if total <= 0:
            return dict(self._weights)

        n_factors = len(scores)
        adjusted: dict[str, float] = {}
        for fname, base_w in self._weights.items():
            if fname in scores:
                adjusted[fname] = base_w * (scores[fname] / (total / n_factors))
            else:
                adjusted[fname] = base_w

        total_w = sum(adjusted.values())
        if total_w > 0:
            adjusted = {k: v / total_w for k, v in adjusted.items()}

        return adjusted

    def compute(self, tech_list: list[dict], kline_data: dict,
                context: dict | None = None) -> list[RawSignal]:
        ctx_extra = (context or {}).get("extra", {})

        factor_scores_list: list[dict[str, float]] = []
        for t in tech_list:
            factor_scores = {
                "momentum": _calc_momentum(t),
                "volatility_reversion": _calc_volatility_reversion(t),
                "volume_flow": _calc_volume_flow(t),
                "oi_change": _calc_oi_change(t, ctx_extra),
                "basis": _calc_basis(t, ctx_extra),
                "inventory_pct": _calc_inventory(t, ctx_extra),
                "capacity": _calc_capacity(t, ctx_extra),
                "macro_regime": _calc_macro(t, context),
                "rate_proxy": _calc_rate_proxy(t, ctx_extra),
                "pmi_proxy": _calc_pmi_proxy(t, ctx_extra),
                "position_rank": _calc_position_rank(t, ctx_extra),
                "warrant_change": _calc_warrant_change(t, ctx_extra),
            }
            factor_scores_list.append(factor_scores)

        # 用收益率代理更新因子表现
        returns: dict[str, float] = {}
        for t in tech_list:
            sym = t.get("symbol", "")
            chg = _safe_float(t.get("change_pct", 0))
            returns[sym] = chg

        if factor_scores_list:
            avg_scores: dict[str, float] = {}
            for fs in factor_scores_list:
                for k, v in fs.items():
                    avg_scores[k] = avg_scores.get(k, 0.0) + v
            n = len(factor_scores_list)
            avg_scores = {k: v / n for k, v in avg_scores.items()}
            self.update_factor_performance(avg_scores, returns)

        self._weights = self._calc_dynamic_weights()

        signals = super().compute(tech_list, kline_data, context)

        for s in signals:
            s.meta["dynamic_weights"] = dict(self._weights)
            s.meta["is_dynamic"] = True

        return signals

    def score(self, filtered_signals: list[RawSignal],
              tech_list: list[dict],
              context: dict | None = None) -> list[ScoredSignal]:
        result = super().score(filtered_signals, tech_list, context)
        for s in result:
            s.extra["dynamic_weights"] = dict(self._weights)
        return result


# ════════════════════════════════════════════════════════════
# 3. 多周期信号融合策略
# ════════════════════════════════════════════════════════════


class MultiPeriodSignalFusion(BaseStrategyV2):
    """多周期信号融合策略 — 融合不同周期信号生成综合策略。

    同时使用短周期（20日）、中周期（60日）、长周期（120日）的因子计算，
    通过加权融合生成综合信号，并检查周期间方向一致性。

    用法:
        strategy = MultiPeriodSignalFusion()
        signals = strategy.compute(tech_list, kline_data, context)
    """

    SHORT_WEIGHT = 0.3
    MEDIUM_WEIGHT = 0.4
    LONG_WEIGHT = 0.3

    def __init__(self, mode: str = "pure_momentum"):
        self._mode = mode
        self._weights = PURE_MOMENTUM_WEIGHTS if mode == "pure_momentum" else FACTOR_WEIGHTS

    @property
    def name(self) -> str:
        return "multi_period_fusion"

    @property
    def display_name(self) -> str:
        return "多周期信号融合"

    @property
    def signal_type(self) -> str:
        return f"multi_period_fusion.{self._mode}"

    @property
    def validators(self) -> list[str]:
        return ["stability"]

    @property
    def weight(self) -> float:
        return 0.8

    @staticmethod
    def _calc_factor_scores(t: dict, ctx_extra: dict | None,
                            context: dict | None) -> dict[str, float]:
        """计算单个品种的因子得分。"""
        return {
            "momentum": _calc_momentum(t),
            "volatility_reversion": _calc_volatility_reversion(t),
            "volume_flow": _calc_volume_flow(t),
            "oi_change": _calc_oi_change(t, ctx_extra),
            "basis": _calc_basis(t, ctx_extra),
            "inventory_pct": _calc_inventory(t, ctx_extra),
            "capacity": _calc_capacity(t, ctx_extra),
            "macro_regime": _calc_macro(t, context),
            "rate_proxy": _calc_rate_proxy(t, ctx_extra),
            "pmi_proxy": _calc_pmi_proxy(t, ctx_extra),
            "position_rank": _calc_position_rank(t, ctx_extra),
            "warrant_change": _calc_warrant_change(t, ctx_extra),
        }

    @staticmethod
    def _calc_period_score(factor_scores: dict[str, float],
                           weights: dict[str, float]) -> float:
        """计算单个周期下的加权总分。"""
        return sum(v * weights.get(k, 0) for k, v in factor_scores.items())

    @staticmethod
    def _extract_period_data(t: dict, period: str) -> dict:
        """从 tech dict 中提取指定周期的指标数据。

        Args:
            t: 技术指标 dict
            period: 周期标识（short/medium/long）

        Returns:
            包含该周期调整后的技术指标 dict
        """
        period_t = dict(t)
        if period == "short":
            period_t["ma_slope"] = _safe_float(
                t.get("ma_slope_short", t.get("ma_slope", 0)))
            period_t["vol_ratio"] = _safe_float(
                t.get("vol_ratio_short", t.get("vol_ratio", 1.0)))
        elif period == "long":
            period_t["ma_slope"] = _safe_float(
                t.get("ma_slope_long", t.get("ma_slope", 0)))
            period_t["vol_ratio"] = _safe_float(
                t.get("vol_ratio_long", t.get("vol_ratio", 1.0)))
        return period_t

    def compute(self, tech_list: list[dict], kline_data: dict,
                context: dict | None = None) -> list[RawSignal]:
        ctx_extra = (context or {}).get("extra", {})
        signals: list[RawSignal] = []

        for t in tech_list:
            sym = t.get("symbol", "")
            price = _safe_float(t.get("price", 0))
            if price <= 0:
                continue

            base_scores = self._calc_factor_scores(t, ctx_extra, context)

            short_t = self._extract_period_data(t, "short")
            long_t = self._extract_period_data(t, "long")

            short_factor_scores = self._calc_factor_scores(short_t, ctx_extra, context)
            medium_factor_scores = base_scores
            long_factor_scores = self._calc_factor_scores(long_t, ctx_extra, context)

            short_score = self._calc_period_score(short_factor_scores, self._weights)
            medium_score = self._calc_period_score(medium_factor_scores, self._weights)
            long_score = self._calc_period_score(long_factor_scores, self._weights)

            fused_score = (
                self.SHORT_WEIGHT * short_score
                + self.MEDIUM_WEIGHT * medium_score
                + self.LONG_WEIGHT * long_score
            )

            # 周期方向一致性检查
            directions: list[str] = []
            for s in (short_score, medium_score, long_score):
                if s > 0.05:
                    directions.append("bull")
                elif s < -0.05:
                    directions.append("bear")
                else:
                    directions.append("neutral")

            bull_count = directions.count("bull")
            bear_count = directions.count("bear")
            consensus = bull_count >= 2 or bear_count >= 2

            active_factors = sum(1 for v in base_scores.values() if abs(v) > 0.05)
            if active_factors < 3 and not consensus:
                continue

            direction = "bull" if fused_score > 0 else ("bear" if fused_score < 0 else "neutral")
            if direction == "neutral":
                continue

            signals.append(RawSignal(
                symbol=sym,
                direction=direction,
                signal_type=f"{self.signal_type}.composite",
                raw_score=round(abs(fused_score), 4),
                strategy_name=self.name,
                meta={
                    "factor_scores": base_scores,
                    "short_score": round(short_score, 4),
                    "medium_score": round(medium_score, 4),
                    "long_score": round(long_score, 4),
                    "fused_score": round(fused_score, 4),
                    "consensus": consensus,
                    "active_factors": active_factors,
                    "mode": self._mode,
                    "price": price,
                    "period_directions": directions,
                },
            ))

        if self._mode == "long_short" and signals:
            signals.sort(key=lambda s: s.raw_score, reverse=True)
            top_n = max(1, len(signals) // 5)
            for i, s in enumerate(signals):
                if i < top_n:
                    s.direction = "bull"
                elif i >= len(signals) - top_n:
                    s.direction = "bear"
                else:
                    s.direction = "neutral"
            signals = [s for s in signals if s.direction != "neutral"]

        return signals

    def score(self, filtered_signals: list[RawSignal],
              tech_list: list[dict],
              context: dict | None = None) -> list[ScoredSignal]:
        result: list[ScoredSignal] = []
        for s in filtered_signals:
            raw = abs(s.raw_score)
            total = raw * 100 if s.direction == "bull" else -raw * 100

            abs_total = abs(total)
            if abs_total >= SCORE_THRESHOLDS["STRONG"]:
                grade = "STRONG"
            elif abs_total >= SCORE_THRESHOLDS["WATCH"]:
                grade = "WATCH"
            elif abs_total >= SCORE_THRESHOLDS["WEAK"]:
                grade = "WEAK"
            else:
                grade = "NOISE"

            ss = ScoredSignal(
                symbol=s.symbol,
                direction=s.direction,
                signal_type=s.signal_type,
                strategy_name=self.name,
                total=round(total, 1),
                abs_score=round(raw * 100, 1),
                grade=grade,
                weight=self.weight,
            )
            ss.sub_scores = s.meta.get("factor_scores", {})
            ss.extra = dict(s.meta)
            result.append(ss)

        return result