"""
fts.risk.simulated_adapter — 模拟交易适配器（C.2 §3.3）。

用于测试和仿真的模拟交易实现：按信号方向模拟成交，
记录持仓与账户状态。

用法:
    from fts.risk import SimulatedTradeAdapter

    adapter = SimulatedTradeAdapter(balance=1_000_000)
    adapter.connect({})
    order = adapter.submit_signal(signal_dict)

版本: v1.0.0
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .trade_adapter import (
    AccountStatus,
    PositionInfo,
    TradeAdapter,
    TradeOrderResult,
)


class SimulatedTradeAdapter(TradeAdapter):
    """模拟交易适配器（测试与仿真）。"""

    def __init__(self, balance: float = 1_000_000.0) -> None:
        """初始化模拟适配器。

        Args:
            balance: 初始账户余额
        """
        self._connected = False
        self._balance = float(balance)
        self._positions: dict[str, PositionInfo] = {}

    # ─── 连接管理 ────────────────────────────────────────

    def connect(self, config: dict[str, Any]) -> bool:
        """建立连接（模拟，始终成功）。"""
        self._connected = True
        return True

    def disconnect(self) -> bool:
        """断开连接。"""
        self._connected = False
        return True

    def is_connected(self) -> bool:
        """检查连接状态。"""
        return self._connected

    # ─── 交易 ────────────────────────────────────────────

    def submit_signal(self, signal: dict[str, Any]) -> TradeOrderResult:
        """提交信号，模拟按市价成交。"""
        if not self._connected:
            return TradeOrderResult(
                status="rejected", error_message="Not connected",
            )

        details = signal.get("signals", [])
        if not details:
            return TradeOrderResult(
                status="rejected", error_message="Empty signals",
            )

        first = details[0]
        symbol = first.get("symbol", "")
        direction = "long" if first.get("direction") == "long" else "short"
        quantity = float(first.get("position", 0.0) or 0.0)
        price = float(first.get("price", 0.0) or 0.0)

        # 模拟成交
        result = TradeOrderResult(
            order_id=str(uuid.uuid4()),
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            price=price,
            status="filled",
            fill_price=price,
            fill_quantity=quantity,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # 更新模拟持仓（简单覆盖式）
        self._positions[symbol] = PositionInfo(
            symbol=symbol,
            direction=direction,
            quantity=quantity,
            avg_price=price,
            market_value=quantity * price,
        )
        # 更新余额（扣除保证金近似）
        self._balance -= quantity * price * 0.1
        return result

    def get_position(self, symbol: str) -> PositionInfo:
        """查询当前持仓。"""
        return self._positions.get(symbol, PositionInfo(symbol=symbol))

    def get_account_status(self) -> AccountStatus:
        """查询账户状态。"""
        position_value = sum(
            float(p.get("market_value", 0.0) or 0.0)
            for p in self._positions.values()
        )
        return AccountStatus(
            balance=self._balance,
            available=self._balance,
            margin_used=self._balance * 0.0,
            position_value=position_value,
            total_equity=self._balance + position_value,
        )


__all__ = ["SimulatedTradeAdapter"]
