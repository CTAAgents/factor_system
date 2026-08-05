"""
fts.factor_engine.cost_simulator — 真实成本模拟器（B.2 Stage 4）。

按品种差异化费率模拟交易成本（手续费 + 滑点 + 冲击成本）。
底层复用 ``cost_model.TransactionCostModel`` 的成本参数与调整逻辑。

用法:
    from fts.factor_engine.cost_simulator import CostSimulator

    sim = CostSimulator()
    result = sim.simulate(signal=positions, market="futures", volume=turnover)

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CostResult:
    """成本模拟结果。"""

    total_cost_bps: float  # 总成本（基点）
    cost_by_type: dict[str, float]  # commission/slippage/impact
    net_sharpe: float  # 成本调整后夏普
    gross_sharpe: float  # 调整前夏普
    turnover: float  # 月度换手率（0~1）


class CostSimulator:
    """真实成本模拟器（B.2 Stage 4）。

    委托 ``TransactionCostModel`` 计算成本，支持品种差异化费率覆盖。
    """

    def __init__(
        self,
        model: Any | None = None,
        symbol_commission: Optional[dict[str, float]] = None,
        symbol_slippage: Optional[dict[str, float]] = None,
        default_market: str = "futures",
    ) -> None:
        """初始化成本模拟器。

        Args:
            model: TransactionCostModel 实例（None 时自动创建）
            symbol_commission: 品种 → 手续费率（基点）覆盖表
            symbol_slippage: 品种 → 滑点（基点）覆盖表
            default_market: 默认市场类型
        """
        if model is None:
            from .cost_model import TransactionCostModel

            model = TransactionCostModel()
        self._model = model
        self._symbol_commission: dict[str, float] = dict(symbol_commission or {})
        self._symbol_slippage: dict[str, float] = dict(symbol_slippage or {})
        self._default_market = default_market

    # ─── 主入口 ──────────────────────────────────────────

    def simulate(
        self,
        signal: np.ndarray,
        market: str | None = None,
        volume: Optional[np.ndarray] = None,
        avg_price: float = 100.0,
        symbol: Optional[str] = None,
    ) -> CostResult:
        """模拟交易成本。

        Args:
            signal: 持仓信号数组（-1 ~ +1）
            market: 市场类型（None 用默认）
            volume: 成交量数组（用于冲击成本估算）
            avg_price: 平均价格
            symbol: 品种代码（提供时应用品种差异化费率覆盖）

        Returns:
            CostResult（总成本/分项/调整前后夏普/换手）。
        """
        market = market or self._default_market
        config = self._model.get_cost_bps(market)

        # 品种差异化覆盖
        commission = config.get("commission_bps", 0.3)
        slippage = config.get("slippage_bps", 0.5)
        impact = config.get("impact_bps_per_pct", 2.0)
        if symbol:
            commission = self._symbol_commission.get(symbol, commission)
            slippage = self._symbol_slippage.get(symbol, slippage)

        # 1. 换手率（月度）：信号绝对变化均值 * 252 / 2
        if len(signal) > 1:
            turnover = float(np.mean(np.abs(np.diff(signal)))) * 252 / 2
        else:
            turnover = 0.0

        # 2. 冲击成本（按成交量占比）
        impact_extra = 0.0
        if volume is not None and len(volume) > 0 and float(np.mean(volume)) > 0:
            avg_abs_signal = float(np.mean(np.abs(signal)))
            pct_of_volume = avg_abs_signal * 0.1
            impact_extra = pct_of_volume * impact

        # 3. 总成本（基点）
        raw_cost = turnover * (slippage + commission + impact) + impact_extra
        min_cost = config.get("min_cost_bps", 0.5)
        total_cost_bps = max(raw_cost, min_cost)

        # 4. 夏普调整（估算）
        gross_sharpe = 0.0
        cost_decimal = total_cost_bps / 10000.0
        cost_penalty = cost_decimal * 12 / 0.15  # 月成本 * 12 / 假设年化波动
        net_sharpe = gross_sharpe - cost_penalty

        logger.info(
            "[CostSimulator] 成本模拟完成 [symbol=%s, turnover=%.2f, cost_bps=%.2f]",
            symbol or market, turnover, total_cost_bps,
        )
        return CostResult(
            total_cost_bps=total_cost_bps,
            cost_by_type={
                "commission": commission,
                "slippage": slippage,
                "impact": impact,
            },
            net_sharpe=net_sharpe,
            gross_sharpe=gross_sharpe,
            turnover=turnover,
        )

    # ─── 查询接口 ────────────────────────────────────────

    def get_commission(self, symbol: str, market: str | None = None) -> float:
        """获取品种手续费率（基点）。"""
        market = market or self._default_market
        config = self._model.get_cost_bps(market)
        return self._symbol_commission.get(
            symbol, config.get("commission_bps", 0.3)
        )

    def get_slippage(self, symbol: str, market: str | None = None) -> float:
        """获取品种滑点率（基点）。"""
        market = market or self._default_market
        config = self._model.get_cost_bps(market)
        return self._symbol_slippage.get(symbol, config.get("slippage_bps", 0.5))


__all__ = ["CostSimulator", "CostResult"]
