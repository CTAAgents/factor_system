"""
fts.factor_engine.portfolio_constructor — 组合构建器（B.2 Stage 3）。

将多因子信号合成为组合，支持多种权重方法：
    - equal: 等权 1/N
    - sharpe: 按因子 Sharpe 归一化加权
    - adaptive: 自适应加权（集成 A.3 AdaptiveWeightManager，按 Regime 调整）

用法:
    from fts.factor_engine.portfolio_constructor import PortfolioConstructor

    pc = PortfolioConstructor()
    result = pc.construct(signals, weight_method="sharpe", factor_metrics=metrics)

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PortfolioResult:
    """组合构建结果。"""

    weights: dict[str, float]  # factor_id → 权重
    portfolio_returns: pd.Series  # 组合收益率序列
    holdings: pd.DataFrame  # 各因子信号（持仓权重）表
    turnover: pd.Series  # 组合换手率序列


class PortfolioConstructor:
    """组合构建器（B.2 Stage 3）。

    将多因子信号按权重方法合成组合收益率。
    """

    def __init__(self, adaptive_manager: Any | None = None) -> None:
        """初始化组合构建器。

        Args:
            adaptive_manager: AdaptiveWeightManager 实例（adaptive 权重模式使用）
        """
        self._adaptive_manager = adaptive_manager

    # ─── 主入口 ──────────────────────────────────────────

    def construct(
        self,
        signals: dict[str, pd.Series],
        weights: Optional[dict[str, float]] = None,
        weight_method: str = "equal",
        regime: Optional[dict[str, Any]] = None,
        factor_metrics: Optional[dict[str, dict[str, float]]] = None,
    ) -> PortfolioResult:
        """构建组合。

        Args:
            signals: factor_id → 信号序列（索引需可对齐）
            weights: 显式权重（提供时优先于 weight_method）
            weight_method: "equal" | "sharpe" | "adaptive"
            regime: Regime 检测结果（adaptive 模式使用）
            factor_metrics: factor_id → 指标 dict（含 sharpe，sharpe 模式使用）

        Returns:
            PortfolioResult（权重/组合收益/持仓/换手）。
        """
        if not signals:
            return PortfolioResult(
                weights={},
                portfolio_returns=pd.Series(dtype=float),
                holdings=pd.DataFrame(),
                turnover=pd.Series(dtype=float),
            )

        # 1. 对齐信号为宽表
        holdings = pd.DataFrame(signals).dropna(how="all")

        # 2. 计算权重
        if weights is None:
            weights = self._compute_weights(list(signals.keys()), weight_method, regime, factor_metrics)
        weights = {k: float(v) for k, v in weights.items() if v != 0}

        # 3. 组合收益 = Σ w_i * signal_i（逐日，缺数补 0）
        weighted = pd.DataFrame({fid: holdings[fid].fillna(0.0) * weights.get(fid, 0.0) for fid in holdings.columns})
        portfolio_returns = weighted.sum(axis=1).dropna()

        # 4. 换手率 = 持仓绝对变化均值
        turnover = (weighted.diff().abs().sum(axis=1) / 2.0).dropna()

        logger.info(
            "[PortfolioConstructor] 组合构建完成 [n=%d, method=%s, weights=%s]",
            len(weights),
            weight_method,
            {k: round(v, 4) for k, v in weights.items()},
        )
        return PortfolioResult(
            weights=weights,
            portfolio_returns=portfolio_returns,
            holdings=holdings,
            turnover=turnover,
        )

    # ─── 权重计算 ────────────────────────────────────────

    def _compute_weights(
        self,
        factor_ids: list[str],
        weight_method: str,
        regime: Optional[dict[str, Any]] = None,
        factor_metrics: Optional[dict[str, dict[str, float]]] = None,
    ) -> dict[str, float]:
        """按指定方法计算权重。"""
        n = len(factor_ids)
        if n == 0:
            return {}

        if weight_method == "equal":
            return {fid: 1.0 / n for fid in factor_ids}

        if weight_method == "sharpe":
            return self._sharpe_weight(factor_ids, factor_metrics or {})

        if weight_method == "adaptive":
            return self._adaptive_weight(factor_ids, factor_metrics or {}, regime)

        # 未知方法回退等权
        logger.warning("[PortfolioConstructor] 未知权重方法 %s，回退等权", weight_method)
        return {fid: 1.0 / n for fid in factor_ids}

    @staticmethod
    def _sharpe_weight(
        factor_ids: list[str],
        factor_metrics: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """Sharpe 归一化加权（负 Sharpe 记为极小值）。"""
        sharpes = []
        for fid in factor_ids:
            m = factor_metrics.get(fid, {})
            s = float(m.get("sharpe", m.get("sharpe_ratio", 0.0)) or 0.0)
            sharpes.append(max(s, 0.01))
        total = sum(sharpes)
        if total <= 0:
            return {fid: 1.0 / len(factor_ids) for fid in factor_ids}
        return {fid: s / total for fid, s in zip(factor_ids, sharpes)}

    def _adaptive_weight(
        self,
        factor_ids: list[str],
        factor_metrics: dict[str, dict[str, float]],
        regime: Optional[dict[str, Any]] = None,
    ) -> dict[str, float]:
        """自适应加权（集成 A.3 AdaptiveWeightManager）。

        先按 Sharpe 构造基础权重，再按 Regime 调整。
        """
        from .adaptive_weight import AdaptiveWeightManager

        manager = self._adaptive_manager or AdaptiveWeightManager()
        factors = [
            {
                "factor_id": fid,
            }
            for fid in factor_ids
        ]
        base = self._sharpe_weight(factor_ids, factor_metrics)
        if regime:
            return manager.compute_weights(factors, regime, base_weights=base)
        return base


__all__ = ["PortfolioConstructor", "PortfolioResult"]
