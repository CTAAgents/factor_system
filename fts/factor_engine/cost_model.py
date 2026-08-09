"""
fts.factor_engine.cost_model — 交易成本模型。

在 BacktestMetrics 中扣除滑点、手续费、冲击成本，
计算成本调整后的净夏普比率。

用法:
    model = TransactionCostModel()
    adjusted_metrics = model.adjust(backtest_metrics, signal, volume, market="futures")

版本: v0.2.0（GAP-F11: 展期成本联动换月日历实际价差）
"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

import numpy as np
import pandas as pd

from .contracts import BacktestMetrics


class CostConfig(TypedDict, total=False):
    slippage_bps: float       # 滑点（基点，默认 1.0）
    commission_bps: float     # 手续费（基点，默认 0.3）
    impact_bps_per_pct: float # 冲击成本（每 1% 日成交量占比，默认 2.0）
    min_cost_bps: float       # 最低成本（基点，默认 0.5）
    roll_cost_bps: float      # 展期成本（基点/次，期货主力换月穿越时扣除，v2.58.0 GAP-046）
    market: str               # "futures" / "stock" / "etf"


class AdjustedMetrics(TypedDict, total=False):
    gross_sharpe: float       # 调整前夏普
    net_sharpe: float         # 成本调整后夏普
    total_cost_bps: float     # 总成本（基点）
    turnover: float           # 月度换手率
    cost_adjusted_ic: float   # 成本调整后 IC（近似）
    roll_cost_bps: float      # 展期成本合计（基点，持仓穿越换月日）


# ─── 默认市场成本配置 ─────────────────────────────────────

_DEFAULT_FUTURES: CostConfig = CostConfig(
    slippage_bps=0.5,
    commission_bps=0.2,
    impact_bps_per_pct=1.0,
    min_cost_bps=0.5,
    roll_cost_bps=2.0,  # v2.58.0 GAP-046: 期货主力换月展期成本（与 FTSConfig.roll_cost_bps 默认一致）
    market="futures",
)

_DEFAULT_STOCK: CostConfig = CostConfig(
    slippage_bps=1.0,
    commission_bps=0.8,
    impact_bps_per_pct=2.0,
    min_cost_bps=0.5,
    roll_cost_bps=0.0,  # 股票/ETF 无主力换月
    market="stock",
)

_DEFAULT_ETF: CostConfig = CostConfig(
    slippage_bps=0.5,
    commission_bps=0.3,
    impact_bps_per_pct=1.0,
    min_cost_bps=0.5,
    roll_cost_bps=0.0,  # 股票/ETF 无主力换月
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

    @staticmethod
    def _roll_events_to_spread_map(
        roll_events: list[Any],
    ) -> dict[str, float]:
        """将 RollEvent 列表转换为 {date_str: actual_spread_bps} 映射。

        RollEvent 需包含 date（date 对象）、old_close、new_close 属性。
        价差 = |new_close / old_close - 1| × 10000（基点）。
        old_close 为 0 或缺失时跳过该事件。

        Args:
            roll_events: RollEvent 对象列表（来自 RollCalendar.build_roll_calendar）。

        Returns:
            {date_str: spread_bps} 字典，spread_bps 为正值。
        """
        spread_map: dict[str, float] = {}
        for ev in roll_events:
            old_close = getattr(ev, "old_close", None)
            new_close = getattr(ev, "new_close", None)
            ev_date = getattr(ev, "date", None)
            if old_close is None or new_close is None or ev_date is None:
                continue
            old_close_f = float(old_close)
            if old_close_f <= 0:
                continue
            spread_bps = abs(float(new_close) / old_close_f - 1.0) * 10000.0
            spread_map[str(ev_date)] = spread_bps
        return spread_map

    def adjust(
        self,
        metrics: BacktestMetrics,
        signal: np.ndarray,
        volume: np.ndarray | None = None,
        avg_price: float = 100.0,
        market: str = "futures",
        dates: np.ndarray | None = None,
        roll_dates: set[str] | None = None,
        roll_events: list[Any] | None = None,
    ) -> AdjustedMetrics:
        """对回测指标执行交易成本调整。

        步骤:
            1. 从信号变化估算月度换手率
            2. 查询市场成本参数
            3. 计算总成本（滑点 + 手续费 + 冲击 + 展期）
            4. 应用最低成本下限
            5. 计算成本调整后夏普

        Args:
            metrics: 原始回测指标（必须包含 sharpe）。
            signal: 因子信号数组（-1~+1）。
            volume: 日成交量数组（用于冲击成本估算）。
            avg_price: 平均价格（用于冲击成本缩放）。
            market: 市场类型。
            dates: 日期索引数组（与 signal 对齐，用于匹配换月日；v2.58.0 GAP-046）。
            roll_dates: 换月日期集合（ISO 字符串）；持仓穿越换月日时扣除展期成本
                = |position| × roll_cost_bps（v2.58.0 GAP-046）。
            roll_events: RollEvent 列表（GAP-F11，v2.67.0）。
                提供时优先使用实际价差计算展期成本；未提供时回退到 roll_dates + 固定 bps。

        Returns:
            AdjustedMetrics（含展期成本统计）。
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

        # 2.5 展期成本（v2.58.0 GAP-046 / v2.67.0 GAP-F11）
        # 优先使用 roll_events 实际价差；未提供时回退 roll_dates + 固定 bps
        roll_cost_bps = config.get("roll_cost_bps", 0.0)
        if roll_events:
            spread_map = self._roll_events_to_spread_map(roll_events)
            roll_cost_total = self._estimate_roll_cost(
                signal, dates, roll_dates, roll_cost_bps,
                roll_events=roll_events, spread_map=spread_map,
            )
        else:
            roll_cost_total = self._estimate_roll_cost(
                signal, dates, roll_dates, roll_cost_bps,
            )

        # 3. 总成本估算（基点）
        slippage = config.get("slippage_bps", 0.5)
        commission = config.get("commission_bps", 0.3)
        impact = config.get("impact_bps_per_pct", 2.0)
        min_cost = config.get("min_cost_bps", 0.5)

        # total_cost_bps = 换手率 * 每笔成本 + 额外冲击 + 展期成本
        raw_cost = turnover * (slippage + commission + impact) + impact_extra
        total_cost_bps = max(raw_cost, min_cost) + roll_cost_total

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
            roll_cost_bps=roll_cost_total,
        )

    @staticmethod
    def _estimate_roll_cost(
        signal: np.ndarray,
        dates: np.ndarray | None,
        roll_dates: set[str] | None,
        roll_cost_bps: float,
        roll_events: list[Any] | None = None,
        spread_map: dict[str, float] | None = None,
    ) -> float:
        """估算展期成本（基点）。

        持仓穿越换月日时，扣除展期成本。
        有 roll_events + spread_map 时优先使用实际价差（超出固定 bps 时用价差），
        否则用 |position| × roll_cost_bps 固定 bps（v2.58.0 GAP-046 兼容）。
        dates / roll_dates 缺失或 roll_cost_bps=0 时返回 0。

        Args:
            signal: 持仓信号数组（-1~+1）。
            dates: 日期索引数组（与 signal 对齐）。
            roll_dates: 换月日期集合（ISO 字符串，回退用）。
            roll_cost_bps: 固定展期成本（基点/次，回退用）。
            roll_events: RollEvent 列表（GAP-F11，价差联动，可选）。
            spread_map: {date_str: actual_spread_bps} 映射（GAP-F11，可选）。

        Returns:
            展期成本合计（基点）。
        """
        if dates is None or roll_cost_bps <= 0 or len(signal) == 0 or len(dates) != len(signal):
            return 0.0
        # 用 spread_map 确定每个换月日的 bps
        # 当 roll_events 提供且对应日期有 spread_map 时，若实际价差 > 固定 bps 则用价差
        effective_bps_map: dict[str, float] = {}
        if roll_events and spread_map:
            for ev_date_str, spread_bps in spread_map.items():
                if spread_bps > roll_cost_bps:
                    effective_bps_map[ev_date_str] = spread_bps
                else:
                    effective_bps_map[ev_date_str] = roll_cost_bps
        # 无 roll_events 回退到 roll_dates（所有换月日都用固定 bps）
        if not effective_bps_map:
            if not roll_dates:
                return 0.0
            for d in roll_dates:
                effective_bps_map[d] = roll_cost_bps

        total = 0.0
        for t in range(len(signal)):
            if abs(signal[t]) > 1e-8:
                d_str = str(pd.Timestamp(dates[t]).date())
                bps = effective_bps_map.get(d_str)
                if bps is not None:
                    total += abs(signal[t]) * bps
        return float(total)

    @staticmethod
    def impact_cost(
        volume_pct: float,
        impact_bps_per_pct: float,
        ref_pct: float = 0.01,
    ) -> float:
        """square-root 冲击成本模型（GAP-L305，衔接总纲 GAP-I501/I303）。

        冲击成本与成交量占比呈平方根关系：
            cost_bps = impact_bps_per_pct * sqrt(volume_pct / ref_pct)

        Args:
            volume_pct: 持仓占日均成交额比例（0~1，如 0.05 = 占 5%）
            impact_bps_per_pct: 参考成交量占比（ref_pct）对应的冲击成本（基点）
            ref_pct: 参考成交量占比（默认 0.01 = 1%）

        Returns:
            冲击成本（基点，单调递增且非负）。
        """
        if volume_pct <= 0.0 or impact_bps_per_pct <= 0.0:
            return 0.0
        ratio = float(volume_pct) / float(ref_pct)
        return float(impact_bps_per_pct) * np.sqrt(max(ratio, 1e-12))

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
