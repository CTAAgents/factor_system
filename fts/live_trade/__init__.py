"""
fts.live_trade — 实盘执行链路（GAP-F01，v2.60.0）。

提供信号侧完备的执行契约（AGENTS.md 4.3 实盘红线，FTS 角色边界）:
    - ``orders``: 订单生命周期状态机（PENDING/SUBMITTED/PARTIAL/FILLED/CANCELED/REJECTED）
    - ``stop_orders``: 持仓级止损止盈单管理
    - ``intervention``: 人工干预接口（紧急暂停/一键平仓，权限最高）
    - ``gateway``: 网关抽象 + 模拟实现 + 下单重试/超时兜底

真实券商/交易所网关由下游系统（FDT）实现，本包只定义契约与仿真。

版本: v1.0.0（GAP-F01）
"""

from .contracts import (
    ReplayResult,
    SimAccount,
    SimApplyResult,
    SimDailyRecord,
    SimFill,
    SimPosition,
    contract_multiplier,
    infer_market,
)
from .gateway import AbstractGateway, SimulatedGateway, submit_with_retry
from .intervention import (
    AllCloseInstruction,
    InterventionController,
    InterventionRecord,
    InterventionState,
)
from .orders import Order, OrderLifecycle, OrderState, TERMINAL_STATES
from .simulated_engine import SimulatedPaperTrader, SimulatedReplayEngine
from .simulated_portfolio import SimPortfolioConfig, SimulatedPortfolio
from .sqlite_store import SimSQLiteStore
from .stop_orders import (
    CloseInstruction,
    StopOrder,
    StopOrderManager,
    StopSide,
    StopStatus,
)

__all__ = [
    # orders
    "Order",
    "OrderState",
    "OrderLifecycle",
    "TERMINAL_STATES",
    # stop_orders
    "StopOrder",
    "StopSide",
    "StopStatus",
    "CloseInstruction",
    "StopOrderManager",
    # intervention
    "InterventionController",
    "InterventionState",
    "InterventionRecord",
    "AllCloseInstruction",
    # gateway
    "AbstractGateway",
    "SimulatedGateway",
    "submit_with_retry",
    # contracts (D.1)
    "SimPosition",
    "SimAccount",
    "SimDailyRecord",
    "SimFill",
    "SimApplyResult",
    "ReplayResult",
    "contract_multiplier",
    "infer_market",
    # simulated portfolio (D.1)
    "SimPortfolioConfig",
    "SimulatedPortfolio",
    # simulated engine (D.1)
    "SimulatedReplayEngine",
    "SimulatedPaperTrader",
    # sqlite store (D.1)
    "SimSQLiteStore",
]
