"""
fts.factor_engine.risk_attributor — 风险归因分析器（B.2 Stage 5）。

分析组合的风险来源：
    - 因子贡献度（各因子对组合收益的贡献）
    - 暴露分析（组合对各标的/因子的平均暴露）
    - VaR / ES 分析（历史模拟法）

用法:
    from fts.factor_engine.risk_attributor import RiskAttributor

    attr = RiskAttributor()
    report = attr.attribute(portfolio_returns=returns, factor_returns=factor_returns)

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RiskAttributionReport:
    """风险归因报告。"""

    factor_contributions: dict[str, float]  # factor_id → 收益贡献
    exposures: dict[str, float]  # 平均暴露
    var_95: float
    var_99: float
    es_95: float
    realized_vol: float  # 年化波动率
    details: dict[str, Any] = field(default_factory=dict)


class RiskAttributor:
    """风险归因分析器（B.2 Stage 5）。"""

    # ─── 主入口 ──────────────────────────────────────────

    def attribute(
        self,
        portfolio_returns: pd.Series,
        factor_returns: Optional[pd.DataFrame] = None,
        holdings: Optional[pd.DataFrame] = None,
    ) -> RiskAttributionReport:
        """执行风险归因。

        Args:
            portfolio_returns: 组合收益率序列
            factor_returns: 各因子收益率宽表（可选，用于贡献度）
            holdings: 持仓权重表（可选，用于暴露分析）

        Returns:
            RiskAttributionReport。
        """
        returns = portfolio_returns.dropna()
        if len(returns) == 0:
            return RiskAttributionReport(
                factor_contributions={}, exposures={},
                var_95=0.0, var_99=0.0, es_95=0.0, realized_vol=0.0,
            )

        # 1. 因子贡献度
        contributions: dict[str, float] = {}
        if factor_returns is not None and len(factor_returns.columns) > 0:
            contributions = self._factor_contribution(returns, factor_returns)

        # 2. 暴露分析
        exposures: dict[str, float] = {}
        if holdings is not None and len(holdings) > 0:
            exposures = self._exposure_analysis(holdings)

        # 3. VaR / ES / 波动率
        var_95, var_99, es_95 = self._var_analysis(returns)
        realized_vol = float(returns.std() * np.sqrt(252)) if len(returns) > 1 else 0.0

        logger.info(
            "[RiskAttributor] 归因完成 [var_95=%.4f, es_95=%.4f, vol=%.4f, n_contrib=%d]",
            var_95, es_95, realized_vol, len(contributions),
        )
        return RiskAttributionReport(
            factor_contributions=contributions,
            exposures=exposures,
            var_95=var_95,
            var_99=var_99,
            es_95=es_95,
            realized_vol=realized_vol,
        )

    # ─── 内部方法 ────────────────────────────────────────

    @staticmethod
    def _factor_contribution(
        portfolio_returns: pd.Series, factor_returns: pd.DataFrame
    ) -> dict[str, float]:
        """各因子贡献度：协方差分解（因子与组合的协方差 / 组合方差）。"""
        aligned = factor_returns.reindex(portfolio_returns.index).dropna()
        if len(aligned) < 20 or len(aligned.columns) == 0:
            return {}
        p = portfolio_returns.reindex(aligned.index).fillna(0.0)
        var_p = float(np.var(p))
        if var_p < 1e-12:
            return {}
        contributions: dict[str, float] = {}
        for fid in aligned.columns:
            cov = float(np.cov(aligned[fid].fillna(0.0), p)[0, 1])
            contributions[fid] = cov / var_p
        return contributions

    @staticmethod
    def _exposure_analysis(holdings: pd.DataFrame) -> dict[str, float]:
        """暴露分析：各列的绝对平均暴露。"""
        if len(holdings) == 0:
            return {}
        return {
            col: float(np.mean(np.abs(holdings[col].fillna(0.0))))
            for col in holdings.columns
        }

    @staticmethod
    def _var_analysis(
        returns: pd.Series,
    ) -> tuple[float, float, float]:
        """VaR / ES（历史模拟法，负值表示亏损）。"""
        values = returns.values
        if len(values) == 0:
            return 0.0, 0.0, 0.0
        var_95 = float(np.quantile(values, 0.05))
        var_99 = float(np.quantile(values, 0.01))
        tail = values[values <= var_95]
        es_95 = float(np.mean(tail)) if len(tail) > 0 else var_95
        return var_95, var_99, es_95


__all__ = ["RiskAttributor", "RiskAttributionReport"]
