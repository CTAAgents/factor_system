"""
fts.live_trade.orders — 订单生命周期状态机契约（GAP-F01，v2.60.0）。

定义订单从创建到终态的生命周期：
    PENDING → SUBMITTED → PARTIAL → FILLED
                         → CANCELED / REJECTED（任意提交后状态可拒绝/撤销）

- ``OrderState``: 订单状态枚举（PENDING/SUBMITTED/PARTIAL/FILLED/CANCELED/REJECTED）
- ``Order``: 订单数据契约（含方向/数量/价格/已成交量/成交均价/错误信息）
- ``OrderLifecycle``: 状态转移校验器（非法转移拦截 + 异常回滚）

FTS 角色边界: 本模块为信号侧订单契约，真实网关撮合由下游（FDT）负责。

版本: v1.0.0（GAP-F01）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class OrderState(str, Enum):
    """订单状态枚举。"""

    PENDING = "PENDING"  # 已创建，待提交
    SUBMITTED = "SUBMITTED"  # 已提交网关，等待成交
    PARTIAL = "PARTIAL"  # 部分成交
    FILLED = "FILLED"  # 全部成交（终态）
    CANCELED = "CANCELED"  # 已撤销（终态）
    REJECTED = "REJECTED"  # 被拒/异常（终态）


# 终态集合
TERMINAL_STATES: frozenset[OrderState] = frozenset({OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED})


@dataclass
class Order:
    """订单数据契约。"""

    order_id: str
    symbol: str
    direction: str  # long | short | flat
    quantity: float
    price: float
    state: OrderState = OrderState.PENDING
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    error: str = ""

    def as_dict(self) -> dict:
        """序列化为字典（状态机/审计用）。"""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "quantity": self.quantity,
            "price": self.price,
            "state": self.state.value,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
        }


class OrderLifecycle:
    """订单生命周期状态机。

    校验状态转移合法性；非法转移抛 ``ValueError`` 并记录审计日志；
    提供异常回滚（任意非终态 → REJECTED）。
    """

    # 合法转移表：{from_state: set(to_state)}
    _TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
        # PENDING 可直达 PARTIAL/FILLED（瞬时成交路径：市价单提交即部分/全部成交）
        OrderState.PENDING: frozenset(
            {
                OrderState.SUBMITTED,
                OrderState.PARTIAL,
                OrderState.FILLED,
                OrderState.CANCELED,
                OrderState.REJECTED,
            }
        ),
        OrderState.SUBMITTED: frozenset(
            {
                OrderState.PARTIAL,
                OrderState.FILLED,
                OrderState.CANCELED,
                OrderState.REJECTED,
            }
        ),
        OrderState.PARTIAL: frozenset(
            {
                OrderState.PARTIAL,
                OrderState.FILLED,
                OrderState.CANCELED,
                OrderState.REJECTED,
            }
        ),
        OrderState.FILLED: frozenset(),  # 终态
        OrderState.CANCELED: frozenset(),  # 终态
        OrderState.REJECTED: frozenset(),  # 终态
    }

    @classmethod
    def can_transition(cls, current: OrderState, target: OrderState) -> bool:
        """判断状态转移是否合法。"""
        return target in cls._TRANSITIONS.get(current, frozenset())

    @classmethod
    def transition(cls, order: Order, target: OrderState, **updates) -> Order:
        """执行状态转移（原地更新并返回订单）。

        Args:
            order: 目标订单
            target: 目标状态
            **updates: 伴随更新（如 filled_quantity / avg_fill_price / error）

        Returns:
            更新后的订单

        Raises:
            ValueError: 非法状态转移
        """
        if not cls.can_transition(order.state, target):
            logger.warning(
                "[OrderLifecycle] 非法状态转移 [order_id=%s, %s → %s]",
                order.order_id,
                order.state.value,
                target.value,
            )
            raise ValueError(f"非法订单状态转移: {order.state.value} → {target.value}")
        for key, value in updates.items():
            if hasattr(order, key):
                setattr(order, key, value)
        order.state = target
        order.updated_at = datetime.now(timezone.utc).isoformat()
        return order

    @classmethod
    def rollback(cls, order: Order, error: str) -> Order:
        """异常回滚：非终态订单置为 REJECTED 并记录错误。"""
        if order.state in TERMINAL_STATES:
            logger.warning(
                "[OrderLifecycle] 终态订单不可回滚 [order_id=%s, state=%s]",
                order.order_id,
                order.state.value,
            )
            return order
        order.state = OrderState.REJECTED
        order.error = error
        order.updated_at = datetime.now(timezone.utc).isoformat()
        logger.error(
            "[OrderLifecycle] 订单异常回滚 [order_id=%s, error=%s]",
            order.order_id,
            error,
        )
        return order


__all__ = [
    "OrderState",
    "Order",
    "OrderLifecycle",
    "TERMINAL_STATES",
]
