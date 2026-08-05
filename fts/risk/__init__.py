"""
fts.risk — 实时风控与交易适配层（C.2）。

提供:
    - ``RiskManager``: 五项风控规则检查（单品种仓位/组合回撤/单日亏损/杠杆/集中度）
    - ``TradeAdapter``: 交易适配器抽象基类（Liskov 替换原则）
    - ``SimulatedTradeAdapter``: 模拟交易适配器（测试与仿真）

FTS 角色边界: 本包仅做风控检查与信号路由，真实交易执行由下游系统（FDT）负责。

版本: v1.0.0
"""

from .risk_manager import (
    RiskManager,
    RiskConfig,
    RiskCheckItem,
    RiskCheckResult,
)
from .trade_adapter import (
    TradeAdapter,
    TradeOrderResult,
    PositionInfo,
    AccountStatus,
)
from .simulated_adapter import SimulatedTradeAdapter

__all__ = [
    "RiskManager",
    "RiskConfig",
    "RiskCheckItem",
    "RiskCheckResult",
    "TradeAdapter",
    "TradeOrderResult",
    "PositionInfo",
    "AccountStatus",
    "SimulatedTradeAdapter",
]
