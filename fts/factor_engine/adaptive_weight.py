"""
fts.factor_engine.adaptive_weight — 自适应动态权重（A.3）

将 ``portfolio_loop`` 中的 Regime 自适应权重逻辑封装为类接口：
    - ``AdaptiveWeightManager``: 按 Regime 对因子信号权重做自适应调整（含热更新配置）
    - ``RegimeSmoother``: Regime 切换时的权重指数平滑，避免权重剧烈跳变

底层复用 ``portfolio_loop.regime_adaptive_weight_adjustment`` 与
``REGIME_STYLE_MULTIPLIERS``（FactorStyle 倍率映射），保证行为一致。

用法:
    from fts.factor_engine.adaptive_weight import AdaptiveWeightManager

    manager = AdaptiveWeightManager()
    signals, multipliers = manager.adjust(signals, regime, factors)
    manager.update_config("bull", {"momentum": 1.5})   # 热更新
    configs = manager.list_configs()                    # 查看全部配置

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any, Optional, cast

logger = logging.getLogger(__name__)

# 风险制度集合：进入这些制度时快速降权（de-risk），离开时缓慢加仓（re-risk）
_RISK_REGIMES: tuple[str, ...] = ("bear", "high_vol")


class AdaptiveWeightManager:
    """自适应权重管理器（A.3）。

    包装 ``portfolio_loop.regime_adaptive_weight_adjustment``，
    提供按 Regime 的 FactorStyle 权重倍率调整，支持配置热更新。
    """

    def __init__(self, multipliers: Optional[dict[str, dict[str, float]]] = None) -> None:
        # 延迟导入避免循环依赖（style 维度倍率表，未传入时回退默认表）
        from .portfolio_loop import REGIME_STYLE_MULTIPLIERS

        self._multipliers: dict[str, dict[str, float]] = dict(
            multipliers if multipliers is not None else REGIME_STYLE_MULTIPLIERS
        )

    # ─── 核心调整 ──────────────────────────────────────────

    def adjust(
        self,
        signals: list[dict[str, Any]],
        regime: dict[str, Any],
        factors: list[dict[str, Any]] | None = None,
        min_weight: float = 0.01,
    ) -> list[dict[str, Any]]:
        """按当前 Regime 调整因子信号权重（委托 portfolio_loop 原函数）。

        Args:
            signals: 合成后的信号列表（含 factor_id/weight/decay_6m 等）
            regime: Regime 检测结果（dict，含 ``regime`` 字段）
            factors: 因子列表（含 style_tags/factor_id）
            min_weight: 最小权重下限

        Returns:
            调整后的 signals 列表（权重已就地更新）
        """
        from .portfolio_loop import regime_adaptive_weight_adjustment

        return cast(
            list[dict[str, Any]],
            regime_adaptive_weight_adjustment(signals, regime, factors or [], min_weight=min_weight),
        )

    def compute_weights(
        self,
        factors: list[dict[str, Any]],
        regime: dict[str, Any],
        base_weights: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """按 Regime 计算因子权重（设计风格接口）。

        将因子信号构造为默认等权，调用 adjust 后提取权重。

        Args:
            factors: 因子列表（含 factor_id/style_tags）
            regime: Regime 检测结果
            base_weights: 基础权重（缺省等权）

        Returns:
            factor_id → 权重 映射
        """
        fids = [f.get("factor_id", "") for f in factors if f.get("factor_id")]
        if not fids:
            return {}
        if base_weights is None:
            base_weights = {fid: 1.0 / len(fids) for fid in fids}
        signals = [
            {
                "factor_id": fid,
                "weight": base_weights.get(fid, 0.0),
                "decay_6m": next((f.get("decay_6m", 0.0) for f in factors if f.get("factor_id") == fid), 0.0),
            }
            for fid in fids
        ]
        adjusted = self.adjust(signals, regime, factors)
        return {s.get("factor_id", ""): s.get("weight", 0.0) for s in adjusted}

    # ─── 配置管理（热更新） ────────────────────────────────

    def get_current_config(self, regime: str) -> dict[str, float]:
        """获取指定 Regime 的倍率配置。"""
        return dict(self._multipliers.get(regime, {}))

    def update_config(self, regime: str, multipliers: dict[str, float]) -> None:
        """热更新指定 Regime 的权重倍率配置。"""
        merged = dict(self._multipliers.get(regime, {}))
        merged.update(multipliers)
        self._multipliers[regime] = merged
        logger.info("[AdaptiveWeight] 更新 Regime=%s 倍率配置: %s", regime, merged)

    def list_configs(self) -> dict[str, dict[str, float]]:
        """列出所有 Regime 的权重倍率配置。"""
        return {k: dict(v) for k, v in self._multipliers.items()}


class RegimeSmoother:
    """Regime 切换时的权重指数平滑器。

    避免 Regime 频繁切换导致权重剧烈跳变，并支持不对称切换（28-T7）：
    - Regime 变化且已稳定 min_days：直接应用新权重
    - Regime 未稳定（过渡期）：按方向选择平滑系数做指数平滑
        - 进入风险制度（bear/high_vol）：用 de_risk_alpha 快速降权（de-risk）
        - 离开风险制度：用 re_risk_alpha 缓慢加仓（re-risk）
        - 其余切换：用默认 alpha

    对标 Man AHL / PIMCO 战术配置：风险来临快降、风险消退慢加，滞后确认防震荡。
    """

    def __init__(
        self,
        alpha: float = 0.3,
        min_days: int = 3,
        de_risk_alpha: float = 0.8,  # 进入风险制度：快速降权
        re_risk_alpha: float = 0.1,  # 回归安全制度：缓慢加仓
        risk_regimes: tuple[str, ...] = _RISK_REGIMES,
    ) -> None:
        self._alpha = float(alpha)
        self._min_days = int(min_days)
        self._de_risk_alpha = float(de_risk_alpha)
        self._re_risk_alpha = float(re_risk_alpha)
        self._risk_regimes = risk_regimes
        self._current_regime: Optional[str] = None
        self._regime_since: Optional[Any] = None  # datetime

    def should_apply(
        self, detected_regime: str, current_weights: dict[str, float], new_weights: dict[str, float]
    ) -> dict[str, float]:
        """计算平滑后的权重。

        Args:
            detected_regime: 检测到的 Regime 名
            current_weights: 当前权重
            new_weights: 新权重

        Returns:
            平滑后的权重
        """
        from datetime import datetime, timezone

        prev_regime = self._current_regime
        if detected_regime != self._current_regime:
            self._current_regime = detected_regime
            self._regime_since = datetime.now(timezone.utc)

        # 计算 Regime 稳定天数
        if self._regime_since is not None:
            stable_days = (datetime.now(timezone.utc) - self._regime_since).days
        else:
            stable_days = 0

        if stable_days < self._min_days:
            # 过渡期：按方向选择不对称平滑系数
            if detected_regime in self._risk_regimes:
                eff_alpha = self._de_risk_alpha  # 进入风险 → 快速降
            elif prev_regime in self._risk_regimes:
                eff_alpha = self._re_risk_alpha  # 离开风险 → 缓慢加
            else:
                eff_alpha = self._alpha
            return {
                fid: eff_alpha * new_weights.get(fid, 0.0) + (1.0 - eff_alpha) * current_weights.get(fid, 0.0)
                for fid in set(current_weights) | set(new_weights)
            }

        # Regime 稳定：直接使用新权重
        return dict(new_weights)


__all__ = ["AdaptiveWeightManager", "RegimeSmoother"]
