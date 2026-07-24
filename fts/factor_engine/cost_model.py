"""
fts.factor_engine.cost_model — 交易成本模型。

在 BacktestMetrics 中扣除滑点、手续费、冲击成本，
计算成本调整后的净夏普比率。

用法:
    model = TransactionCostModel()
    adjusted_metrics = model.adjust(backtest_metrics, signal, volume, market="futures")

版本: v0.1.0
"""

from __future__ import annotations

from typing import TypedDict

import numpy as np

from .contracts import BacktestMetrics


class CostConfig(TypedDict, total=False):
    slippage_bps: float       # 滑点（基点，默认 1.0）
    commission_bps: float     # 手续费（基点，默认 0.3）
    impact_bps_per_pct: float # 冲击成本（每 1% 日成交量占比，默认 2.0）
    min_cost_bps: float       # 最低成本（基点，默认 0.5）
    market: str               # "futures" / "stock" / "etf"


class AdjustedMetrics(TypedDict, total=False):
    gross_sharpe: float       # 调整前夏普
    net_sharpe: float         # 成本调整后夏普
    total_cost_bps: float     # 总成本（基点）
    turnover: float           # 月度换手率
    cost_adjusted_ic: float   # 成本调整后 IC（近似）


# ─── 默认市场成本配置 ─────────────────────────────────────

_DEFAULT_FUTURES: CostConfig = CostConfig(
    slippage_bps=0.5,
    commission_bps=0.2,
    impact_bps_per_pct=1.0,
    min_cost_bps=0.5,
    market="futures",
)

_DEFAULT_STOCK: CostConfig = CostConfig(
    slippage_bps=1.0,
    commission_bps=0.8,
    impact_bps_per_pct=2.0,
    min_cost_bps=0.5,
    market="stock",
)

_DEFAULT_ETF: CostConfig = CostConfig(
    slippage_bps=0.5,
    commission_bps=0.3,
    impact_bps_per_pct=1.0,
    min_cost_bps=0.5,
    market="etf",
)

_DEFAULT_MARKET_CONFIGS: dict[str, CostConfig] = {
    "futures": _DEFAULT_FUTURES,
    "stock": _DEFAULT_STOCK,
    "etf": _DEFAULT_ETF,
}

# 假设的年化波动率（用于夏普成本惩罚估算）
_ASSUMED_ANNUAL_VOL = 0.15
# 月度换手率转年化系数
_MONTHS_PER_YEAR = 12


class TransactionCostModel:
    """交易成本模型。

    管理不同市场的成本参数，并提供 adjust() 方法
    在 BacktestMetrics 基础上扣除交易成本。
    """

    def __init__(
        self,
        config: CostConfig | None = None,
        market_configs: dict[str, CostConfig] | None = None,
    ) -> None:
        """初始化交易成本模型。

        Args:
            config: 全局默认配置。为 None 时使用 "futures" 默认值。
            market_configs: 各市场专属配置字典。
                未提供的市场将回退到全局默认配置或内置默认值。
        """
        self._market_configs: dict[str, CostConfig] = {}

        # 加载外部覆盖
        if market_configs:
            self._market_configs.update(market_configs)

        # 应用全局默认配置（覆盖对应市场的配置项）
        if config is not None:
            market = config.get("market", "futures")
            self._market_configs[market] = config
            self._default_config = config
        else:
            self._default_config = CostConfig(**_DEFAULT_FUTURES)

        # 补充未定义的市场使用内置默认值
        for market, cfg in _DEFAULT_MARKET_CONFIGS.items():
            if market not in self._market_configs:
                self._market_configs[market] = cfg

    def get_cost_bps(self, market: str = "stock") -> CostConfig:
        """获取指定市场的成本配置。

        Args:
            market: 市场名称（"futures" / "stock" / "etf"）。

        Returns:
            该市场的 CostConfig。
        """
        return self._market_configs.get(
            market,
            self._default_config,
        )

    def adjust(
        self,
        metrics: BacktestMetrics,
        signal: np.ndarray,
        volume: np.ndarray | None = None,
        avg_price: float = 100.0,
        market: str = "futures",
    ) -> AdjustedMetrics:
        """对回测指标执行交易成本调整。

        步骤:
            1. 从信号变化估算月度换手率
            2. 查询市场成本参数
            3. 计算总成本（滑点 + 手续费 + 冲击）
            4. 应用最低成本下限
            5. 计算成本调整后夏普

        Args:
            metrics: 原始回测指标（必须包含 sharpe）。
            signal: 因子信号数组（-1~+1）。
            volume: 日成交量数组（用于冲击成本估算）。
            avg_price: 平均价格（用于冲击成本缩放）。
            market: 市场类型。

        Returns:
            AdjustedMetrics。
        """
        gross_sharpe = metrics.get("sharpe", 0.0)
        config = self.get_cost_bps(market)

        # 1. 从信号变化估算月度换手率
        if len(signal) > 1:
            signal_changes = np.abs(np.diff(signal))
            # 信号变化均值 * 252 交易日 / 2（双边）≈ 月度换手率
            turnover = float(np.mean(signal_changes)) * 252 / 2
        else:
            turnover = 0.0

        # 2. 计算冲击成本
        impact_extra = 0.0
        if volume is not None and len(volume) > 0:
            impact_extra = self._estimate_impact(
                signal, config.get("impact_bps_per_pct", 2.0),
            )

        # 3. 总成本估算（基点）
        slippage = config.get("slippage_bps", 0.5)
        commission = config.get("commission_bps", 0.3)
        impact = config.get("impact_bps_per_pct", 2.0)
        min_cost = config.get("min_cost_bps", 0.5)

        # total_cost_bps = 换手率 * 每笔成本 + 额外冲击
        raw_cost = turnover * (slippage + commission + impact) + impact_extra
        total_cost_bps = max(raw_cost, min_cost)

        # 4. 成本调整后夏普
        #    cost_decimal = total_cost_bps / 10000（基点转小数）
        #    年化成本 = cost_decimal * 12
        #    夏普惩罚 = 年化成本 / 假设年化波动率
        cost_decimal = total_cost_bps / 10000.0
        cost_penalty = cost_decimal * _MONTHS_PER_YEAR / _ASSUMED_ANNUAL_VOL
        net_sharpe = gross_sharpe - cost_penalty

        # 5. 成本调整后 IC（近似: 成本从 return 中扣除，IC 等比例缩放）
        gross_ic = metrics.get("ic", 0.0)
        if gross_ic != 0:
            cost_adjusted_ic = gross_ic * (net_sharpe / gross_sharpe) if gross_sharpe != 0 else gross_ic
        else:
            cost_adjusted_ic = 0.0

        return AdjustedMetrics(
            gross_sharpe=gross_sharpe,
            net_sharpe=net_sharpe,
            total_cost_bps=total_cost_bps,
            turnover=turnover,
            cost_adjusted_ic=cost_adjusted_ic,
        )

    @staticmethod
    def _estimate_impact(
        volume_signal: np.ndarray,
        impact_coeff: float,
    ) -> float:
        """估算市场冲击成本。

        Args:
            volume_signal: 信号数组（反映交易规模）。
            impact_coeff: 冲击系数（基点）。

        Returns:
            额外冲击成本（基点）。
        """
        if len(volume_signal) == 0:
            return 0.0
        # 用信号绝对值的均值近似交易规模占比
        avg_abs_signal = float(np.mean(np.abs(volume_signal)))
        # 假设信号 = 0.5 对应成交量的 5%
        pct_of_volume = avg_abs_signal * 0.1
        return pct_of_volume * impact_coeff
