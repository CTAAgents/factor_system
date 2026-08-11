"""
fts.live_trade.gateway — 交易网关抽象与模拟实现（GAP-F01，v2.60.0）。

- ``AbstractGateway``: 网关抽象接口（下单/撤单/状态回查）
- ``SimulatedGateway``: 模拟网关（仿真撮合，支持失败/超时注入，测试与灰度用）
- ``submit_with_retry``: 下单重试 + 超时兜底（AGENTS.md 4.3 异常容错）

FTS 角色边界: 真实券商/交易所网关由下游（FDT）实现并继承 ``AbstractGateway``。

版本: v1.0.0（GAP-F01）
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

from .orders import Order, OrderLifecycle, OrderState

logger = logging.getLogger(__name__)


class AbstractGateway(ABC):
    """交易网关抽象接口。"""

    @abstractmethod
    def submit_order(self, order: Order) -> str:
        """提交订单，返回网关订单号。"""

    @abstractmethod
    def cancel_order(self, gateway_order_id: str) -> bool:
        """撤销订单。"""

    @abstractmethod
    def query_order(self, gateway_order_id: str) -> dict:
        """回查订单状态（网关视角）。"""

    def is_available(self) -> bool:
        """网关可用性探活。"""
        return True


class SimulatedGateway(AbstractGateway):
    """模拟网关：仿真撮合（默认提交即全部成交）。

    Args:
        fill_on_submit: 提交时是否立即全部成交（默认 True）
        fail_submit: 提交是否失败（注入故障，测试用）
        reject_rate: 提交被拒概率（默认 0）
        latency_seconds: 模拟延迟（默认 0）
    """

    def __init__(
        self,
        fill_on_submit: bool = True,
        fail_submit: bool = False,
        reject_rate: float = 0.0,
        latency_seconds: float = 0.0,
    ) -> None:
        self._fill_on_submit = fill_on_submit
        self._fail_submit = fail_submit
        self._reject_rate = float(reject_rate)
        self._latency_seconds = float(latency_seconds)
        self._orders: dict[str, Order] = {}

    def submit_order(
        self,
        order: Order,
        match_result: dict | None = None,
        order_type: str = "market",
        book: dict | None = None,
    ) -> str:
        """模拟提交：可注入失败/拒绝/延迟；支持部分成交与限价单（D.2 §4/§P2）。

        Args:
            order: 订单（PENDING 状态）
            match_result: tick 撮合结果（可选，来自 OrderBookMatchingEngine）。
                含 book_used=True 且 unfilled_qty>0 时订单转 PARTIAL
                （filled_quantity=已成交部分）；否则全量成交。
            order_type: "market"（市价，默认）| "limit"（限价，P2 能力储备）
            book: 盘口快照（限价单判断用，可选）。限价单对手盘最优价优于
                限价时按限价成交；否则挂单（SUBMITTED，待撤单或后续撮合）。

        Raises:
            RuntimeError: fail_submit=True 时抛出（模拟网络/网关故障）
        """
        if self._latency_seconds > 0:
            time.sleep(self._latency_seconds)
        if self._fail_submit:
            raise RuntimeError("SimulatedGateway: 网关故障（注入）")
        gateway_id = f"gw_{order.order_id}"
        self._orders[gateway_id] = order
        if self._fill_on_submit:
            if order_type == "limit":
                # 限价单：对手盘最优价优于/等于限价 → 按限价成交；否则挂单
                if book and self._limit_tradeable(order, book):
                    OrderLifecycle.transition(order, OrderState.FILLED, filled_quantity=order.quantity)
                else:
                    OrderLifecycle.transition(order, OrderState.SUBMITTED)
            elif match_result and match_result.get("book_used") and match_result.get("unfilled_qty", 0.0) > 1e-9:
                # 部分成交：已成交部分入账，剩余挂单等待补单（PARTIAL → FILLED）
                filled = float(match_result.get("filled_qty", 0.0))
                OrderLifecycle.transition(
                    order,
                    OrderState.PARTIAL,
                    filled_quantity=filled,
                    avg_fill_price=float(match_result.get("avg_price", 0.0)),
                )
            else:
                OrderLifecycle.transition(order, OrderState.FILLED, filled_quantity=order.quantity)
        else:
            OrderLifecycle.transition(order, OrderState.SUBMITTED)
        return gateway_id

    @staticmethod
    def _limit_tradeable(order: Order, book: dict) -> bool:
        """限价单是否可成交：对手盘最优价优于/等于限价。

        long 买入：ask 最优价 ≤ 限价可成交；short 卖出：bid 最优价 ≥ 限价可成交。
        """
        levels = book.get("ask_levels", []) if order.direction == "long" else book.get("bid_levels", [])
        prices = [float(lv.get("price", 0.0) or 0.0) for lv in levels if float(lv.get("price", 0.0) or 0.0) > 0]
        if not prices:
            return False
        best = min(prices) if order.direction == "long" else max(prices)
        if order.direction == "long":
            return best <= order.price + 1e-9
        return best >= order.price - 1e-9

    @staticmethod
    def auction_open(
        buy_limits: list[tuple[float, float]],
        sell_limits: list[tuple[float, float]],
    ) -> float:
        """简化集合竞价：确定最大成交量的均衡价（P2 能力储备）。

        对候选价格（所有买卖限价并集）计算可成交量 = min(累计买量, 累计卖量)，
        取成交量最大的价格为开盘价；并列取中间价。

        Args:
            buy_limits: [(限价, 数量), ...]（价格升序）
            sell_limits: [(限价, 数量), ...]（价格升序）

        Returns:
            均衡开盘价；无有效输入返回 0.0。
        """
        if not buy_limits or not sell_limits:
            return 0.0
        candidates = sorted({p for p, _ in buy_limits} | {p for p, _ in sell_limits})
        best_price, best_vol = 0.0, -1.0
        for price in candidates:
            cum_buy = sum(q for p, q in buy_limits if p >= price)     # 限价≥价格愿意买
            cum_sell = sum(q for p, q in sell_limits if p <= price)   # 限价≤价格愿意卖
            vol = min(cum_buy, cum_sell)
            if vol > best_vol:
                best_vol, best_price = vol, price
        return float(best_price)

    def fill_partial(self, gateway_order_id: str, qty: float, price: float) -> Order:
        """补单成交（D.2 §4.6）：PARTIAL → PARTIAL（未满）→ FILLED（满仓）。

        模拟盘口后续回补：剩余挂单在后续 tick 逐笔撮合成交。
        订单为终态或非 PARTIAL 时返回原订单（幂等，不抛错）。

        Args:
            gateway_order_id: 网关订单号
            qty: 本次补单数量（>0）
            price: 补单成交价

        Returns:
            更新后的订单
        """
        order = self._orders.get(gateway_order_id)
        if order is None or order.state != OrderState.PARTIAL or qty <= 0:
            return order  # type: ignore[return-value]
        new_filled = order.filled_quantity + qty
        if new_filled >= order.quantity - 1e-9:
            OrderLifecycle.transition(
                order, OrderState.FILLED, filled_quantity=order.quantity, avg_fill_price=price
            )
        else:
            OrderLifecycle.transition(order, OrderState.PARTIAL, filled_quantity=new_filled, avg_fill_price=price)
        return order

    def cancel_order(self, gateway_order_id: str) -> bool:
        """模拟撤单：非终态订单 → CANCELED。"""
        order = self._orders.get(gateway_order_id)
        if order is None:
            return False
        if order.state in (OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED):
            return False
        OrderLifecycle.transition(order, OrderState.CANCELED)
        return True

    def query_order(self, gateway_order_id: str) -> dict:
        """回查订单状态。"""
        order = self._orders.get(gateway_order_id)
        if order is None:
            raise KeyError(f"未知网关订单: {gateway_order_id}")
        return order.as_dict()


def submit_with_retry(
    gateway: AbstractGateway,
    order: Order,
    max_retries: int = 3,
    retry_interval: float = 0.1,
    timeout_seconds: float = 5.0,
) -> str:
    """下单重试 + 超时兜底。

    Args:
        gateway: 网关实例
        order: 订单（PENDING 状态）
        max_retries: 最大重试次数
        retry_interval: 重试间隔（秒）
        timeout_seconds: 整体超时阈值（秒）

    Returns:
        网关订单号

    Raises:
        RuntimeError: 重试耗尽（订单已回滚为 REJECTED）
    """
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    last_error = ""
    while attempts <= max_retries:
        if time.monotonic() > deadline:
            OrderLifecycle.rollback(order, f"下单超时（{timeout_seconds}s），回滚为 REJECTED")
            raise RuntimeError(f"下单超时: order_id={order.order_id}")
        try:
            attempts += 1
            gateway_id = gateway.submit_order(order)
            logger.info(
                "[Gateway] 下单成功 [order_id=%s, gateway_id=%s, attempt=%d]",
                order.order_id,
                gateway_id,
                attempts,
            )
            return gateway_id
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            logger.warning(
                "[Gateway] 下单失败，重试 [order_id=%s, attempt=%d/%d, error=%s]",
                order.order_id,
                attempts,
                max_retries,
                last_error,
            )
            if attempts <= max_retries:
                time.sleep(retry_interval)
    OrderLifecycle.rollback(order, f"下单重试耗尽: {last_error}")
    raise RuntimeError(f"下单重试耗尽: order_id={order.order_id}, error={last_error}")


__all__ = [
    "AbstractGateway",
    "SimulatedGateway",
    "submit_with_retry",
]
