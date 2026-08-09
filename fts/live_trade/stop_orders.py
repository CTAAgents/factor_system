"""
fts.live_trade.stop_orders — 持仓级止损止盈单管理（GAP-F01，v2.60.0）。

``StopOrderManager`` 管理持仓级止损/止盈单：
    - register: 为持仓注册止损/止盈单
    - check: 按最新价格检查触发，命中生成平仓指令
    - cancel: 撤销未触发止损单
    - list_active: 列出活跃止损单

止损单触发后生成平仓指令（direction=flat），紧急保护优先于常规风控
（AGENTS.md 4.3：禁止无止损持仓）。

版本: v1.0.0（GAP-F01）
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class StopSide(str, Enum):
    """止损单方向。"""

    STOP_LOSS = "stop_loss"      # 止损（价格跌破/涨破触发价 → 平仓）
    TAKE_PROFIT = "take_profit"  # 止盈（价格达到目标价 → 平仓）


class StopStatus(str, Enum):
    """止损单状态。"""

    ACTIVE = "ACTIVE"            # 监控中
    TRIGGERED = "TRIGGERED"      # 已触发（生成平仓指令）
    CANCELED = "CANCELED"        # 已撤销


@dataclass
class StopOrder:
    """持仓级止损止盈单。"""

    symbol: str
    side: StopSide
    trigger_price: float
    quantity: float
    stop_id: str = field(
        default_factory=lambda: f"stop_{uuid.uuid4().hex[:12]}"
    )
    status: StopStatus = StopStatus.ACTIVE
    triggered_at: str = ""
    direction: str = "long"  # 持仓方向：long（跌破止损）/ short（涨破止损）


@dataclass
class CloseInstruction:
    """止损触发后的平仓指令。"""

    stop_id: str
    symbol: str
    quantity: float
    price: float
    reason: str
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class StopOrderManager:
    """持仓级止损止盈单管理器。"""

    def __init__(self) -> None:
        self._orders: dict[str, StopOrder] = {}

    def register(
        self,
        symbol: str,
        side: StopSide,
        trigger_price: float,
        quantity: float,
        direction: str = "long",
    ) -> StopOrder:
        """注册止损/止盈单。

        Args:
            symbol: 品种
            side: 止损或止盈
            trigger_price: 触发价格
            quantity: 涉及数量
            direction: 持仓方向（long/short）

        Returns:
            已注册的 StopOrder
        """
        order = StopOrder(
            symbol=symbol, side=side,
            trigger_price=trigger_price, quantity=quantity,
            direction=direction,
        )
        self._orders[order.stop_id] = order
        logger.info(
            "[StopOrder] 注册 [stop_id=%s, symbol=%s, side=%s, trigger=%.4f]",
            order.stop_id, symbol, side.value, trigger_price,
        )
        return order

    def cancel(self, stop_id: str) -> bool:
        """撤销未触发止损单。"""
        order = self._orders.get(stop_id)
        if order is None or order.status != StopStatus.ACTIVE:
            return False
        order.status = StopStatus.CANCELED
        logger.info("[StopOrder] 撤销 [stop_id=%s]", stop_id)
        return True

    def list_active(self) -> list[StopOrder]:
        """列出全部活跃（监控中）止损单。"""
        return [o for o in self._orders.values() if o.status == StopStatus.ACTIVE]

    def check(
        self,
        latest_price: dict[str, float],
    ) -> list[CloseInstruction]:
        """按最新价格检查触发。

        Args:
            latest_price: {symbol: 最新价}

        Returns:
            触发生成的平仓指令列表（触发单状态置 TRIGGERED）
        """
        instructions: list[CloseInstruction] = []
        for stop in list(self._orders.values()):
            if stop.status != StopStatus.ACTIVE:
                continue
            price = latest_price.get(stop.symbol)
            if price is None:
                continue
            triggered = False
            if stop.side == StopSide.STOP_LOSS:
                if stop.direction == "long" and price <= stop.trigger_price:
                    triggered = True
                elif stop.direction == "short" and price >= stop.trigger_price:
                    triggered = True
            elif stop.side == StopSide.TAKE_PROFIT:
                if stop.direction == "long" and price >= stop.trigger_price:
                    triggered = True
                elif stop.direction == "short" and price <= stop.trigger_price:
                    triggered = True
            if triggered:
                stop.status = StopStatus.TRIGGERED
                stop.triggered_at = datetime.now(timezone.utc).isoformat()
                instructions.append(CloseInstruction(
                    stop_id=stop.stop_id,
                    symbol=stop.symbol,
                    quantity=stop.quantity,
                    price=price,
                    reason=f"{stop.side.value} 触发: price={price:.4f} "
                           f"trigger={stop.trigger_price:.4f}",
                ))
                logger.warning(
                    "[StopOrder] 触发平仓 [stop_id=%s, symbol=%s, reason=%s]",
                    stop.stop_id, stop.symbol, instructions[-1].reason,
                )
        return instructions


__all__ = [
    "StopSide",
    "StopStatus",
    "StopOrder",
    "CloseInstruction",
    "StopOrderManager",
]
