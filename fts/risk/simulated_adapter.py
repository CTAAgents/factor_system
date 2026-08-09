"""
fts.risk.simulated_adapter — 模拟交易适配器（C.2 实盘对接）。

FTS 角色边界: 本适配器仅做模拟成交，真实交易执行由下游系统（FDT）负责。

设计参考: docs/harness/design/C.2-live-trading-integration-design.md §3.3
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal, Optional

logger = logging.getLogger(__name__)


class TradeOrderResult(dict):
    """交易订单结果（dict 兼容保持向后兼容）。"""


class SimulatedTradeAdapter:
    """模拟交易适配器（用于测试和仿真）。

    行为:
        - connect: 建立模拟连接
        - disconnect: 断开连接
        - submit_signal: 模拟成交，返回 status='filled' 的订单结果
        - get_position / get_account_status / is_connected: 状态查询
    """

    def __init__(self, initial_balance: float = 1_000_000.00) -> None:
        self._connected = False
        self._positions: dict[str, dict] = {}
        self._balance = initial_balance

    def connect(self, config: dict) -> bool:
        """建立与交易系统的连接。"""
        self._connected = True
        logger.info("SimulatedTradeAdapter 连接成功")
        return True

    def disconnect(self) -> bool:
        """断开连接。"""
        self._connected = False
        return True

    def is_connected(self) -> bool:
        """检查连接状态。"""
        return self._connected

    def submit_signal(self, signal: dict) -> TradeOrderResult:
        """提交信号到交易系统并模拟成交。

        Args:
            signal: FactorSignal 格式信号（含 signals 列表）

        Returns:
            TradeOrderResult: 订单结果（status='filled'）
        """
        if not self._connected:
            return TradeOrderResult(
                status="rejected",
                error_message="Not connected",
            )

        leg = signal["signals"][0]
        result = TradeOrderResult(
            order_id=str(uuid.uuid4()),
            symbol=leg["symbol"],
            direction=leg["direction"],
            quantity=float(leg["position"]),
            price=float(leg["price"]),
            status="filled",
            fill_price=float(leg["price"]),
            fill_quantity=float(leg["position"]),
            timestamp=datetime.now().isoformat(),
        )

        # 更新模拟持仓
        self._positions[leg["symbol"]] = {
            "symbol": leg["symbol"],
            "direction": leg["direction"],
            "quantity": float(leg["position"]),
            "avg_price": float(leg["price"]),
        }
        logger.info("模拟成交 [symbol=%s, qty=%s, price=%s]", leg["symbol"], leg["position"], leg["price"])
        return result

    def get_position(self, symbol: str) -> Optional[dict]:
        """查询当前持仓。"""
        return self._positions.get(symbol)

    def get_account_status(self) -> dict:
        """查询账户状态。"""
        return {
            "balance": self._balance,
            "available": self._balance,
            "margin_used": 0.0,
            "position_value": sum(
                p.get("quantity", 0) * p.get("avg_price", 0)
                for p in self._positions.values()
            ),
            "total_equity": self._balance,
        }


__all__ = [
    "SimulatedTradeAdapter",
    "TradeOrderResult",
]
