"""
fts.risk.trade_adapter — 交易适配器抽象基类（C.2 §3）。

定义交易系统适配器的统一接口（Liskov 替换原则）。
具体实现（CTP/XTP 等）由下游交易系统（FDT）负责，FTS 提供模拟适配器。

用法:
    from fts.risk import TradeAdapter

    class MyAdapter(TradeAdapter):
        def connect(self, config: dict) -> bool: ...
        ...

版本: v1.0.0
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict


class TradeOrderResult(TypedDict, total=False):
    """交易订单结果。"""

    order_id: str
    symbol: str
    direction: str  # Literal['long', 'short']
    quantity: float
    price: float
    status: str  # Literal['submitted', 'filled', 'rejected', 'cancelled']
    fill_price: float
    fill_quantity: float
    timestamp: str
    error_message: str


class PositionInfo(TypedDict, total=False):
    """持仓信息。"""

    symbol: str
    direction: str  # Literal['long', 'short']
    quantity: float
    avg_price: float
    market_value: float
    unrealized_pnl: float


class AccountStatus(TypedDict, total=False):
    """账户状态。"""

    balance: float
    available: float
    margin_used: float
    position_value: float
    total_equity: float


class TradeAdapter(ABC):
    """交易适配器抽象基类。

    所有交易系统适配器必须实现此接口。
    """

    @abstractmethod
    def connect(self, config: dict[str, Any]) -> bool:
        """建立与交易系统的连接。"""

    @abstractmethod
    def disconnect(self) -> bool:
        """断开连接。"""

    @abstractmethod
    def submit_signal(self, signal: dict[str, Any]) -> TradeOrderResult:
        """提交信号到交易系统。"""

    @abstractmethod
    def get_position(self, symbol: str) -> PositionInfo:
        """查询当前持仓。"""

    @abstractmethod
    def get_account_status(self) -> AccountStatus:
        """查询账户状态。"""

    @abstractmethod
    def is_connected(self) -> bool:
        """检查连接状态。"""


__all__ = [
    "TradeAdapter",
    "TradeOrderResult",
    "PositionInfo",
    "AccountStatus",
]
