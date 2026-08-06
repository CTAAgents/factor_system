"""
fts.risk — 实时风控与信号路由层（C.2）。

提供:
    - ``RiskManager``: 五项风控规则检查（单品种仓位/组合回撤/单日亏损/杠杆/集中度）

FTS 角色边界: 本包仅做风控检查与信号路由，真实交易执行由下游系统（FDT）负责。

版本: v1.0.0
"""

from .risk_manager import (
    RiskManager,
    RiskConfig,
    RiskCheckItem,
    RiskCheckResult,
)

__all__ = [
    "RiskManager",
    "RiskConfig",
    "RiskCheckItem",
    "RiskCheckResult",
]