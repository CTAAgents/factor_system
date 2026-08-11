"""
fts.live_trade.matching — tick 盘口逐档撮合引擎（D.2 §4 新增）。

市价单按对手盘档位逐档消耗，成交均价 = 加权均价，滑点由盘口缺口自然产生
（不再人为施加固定 bps）。深度不足时部分成交（``unfilled_qty > 0``）。

降级语义（FTS 通用兜底原则）:
    - 盘口为 None / 空档 / 对手盘量为 0 → 返回 ``book_used=False``，由调用方回退 bps 路径
    - 引擎内部 try/except 不抛错，异常一律返回降级标志

与 ``book.py`` / ``gateway.py`` / ``simulated_portfolio.py`` 分层解耦：
撮合引擎不感知订单生命周期，只产出成交价格/数量。

FTS 角色边界: 只做仿真撮合核算，真实撮合由下游（FDT）负责。
"""

from __future__ import annotations

from typing import TypedDict

from .book import OrderBookSnapshot


class MatchResult(TypedDict, total=False):
    filled_qty: float       # 实际成交数量
    avg_price: float        # 加权成交均价
    unfilled_qty: float     # 深度不足剩余量（>0 = 部分成交）
    slippage_bps: float     # 实际滑点（avg_price vs base_price，基点）
    book_used: bool         # 是否走盘口（False = 需降级 bps）


class OrderBookMatchingEngine:
    """盘口逐档撮合引擎（市价单）。

    Args:
        depth: 最大撮合档位数（默认 5）
    """

    def __init__(self, depth: int = 5) -> None:
        self._depth = max(1, int(depth))

    def match_market(
        self,
        book: OrderBookSnapshot | None,
        side: str,
        qty: float,
        base_price: float,
    ) -> MatchResult:
        """市价单逐档撮合。

        算法（buy 为例）:
            remaining = qty; cost = 0; filled = 0
            for lv in ask_levels（价格升序）:
                take = min(remaining, lv.quantity)
                cost += take * lv.price; filled += take; remaining -= take
                if remaining <= 1e-9: break
            avg_price = cost / filled（filled=0 → 降级）
            unfilled = remaining

        Args:
            book: 盘口快照（None/异常 → book_used=False）
            side: "buy" / "sell"
            qty: 下单数量（>0）
            base_price: 基准价（t+1 开盘/实时价），仅用于滑点计算

        Returns:
            MatchResult（book_used=False 时 avg_price 为 0，调用方降级）
        """
        if qty <= 0:
            return MatchResult(filled_qty=0.0, avg_price=0.0, unfilled_qty=0.0, slippage_bps=0.0, book_used=False)
        if book is None:
            return MatchResult(filled_qty=0.0, avg_price=0.0, unfilled_qty=qty, slippage_bps=0.0, book_used=False)
        try:
            levels = book.get("ask_levels", []) if side == "buy" else book.get("bid_levels", [])
            if side == "buy":
                levels = sorted([lv for lv in levels if _qty(lv) > 0 or _price(lv) > 0], key=lambda lv: _price(lv))
            else:
                levels = sorted(
                    [lv for lv in levels if _qty(lv) > 0 or _price(lv) > 0], key=lambda lv: _price(lv), reverse=True
                )
            if not levels:
                return MatchResult(
                    filled_qty=0.0, avg_price=0.0, unfilled_qty=qty, slippage_bps=0.0, book_used=False
                )
            remaining, cost, filled = float(qty), 0.0, 0.0
            for lv in levels:
                if remaining <= 1e-9:
                    break
                take = min(remaining, _qty(lv))
                cost += take * _price(lv)
                filled += take
                remaining -= take
            if filled <= 1e-9:
                # 对手盘量全 0（单档兜底盘口）→ 按 last_price 成交
                last = float(book.get("last_price", 0.0) or 0.0)
                if last > 0:
                    return MatchResult(
                        filled_qty=float(qty),
                        avg_price=last,
                        unfilled_qty=0.0,
                        slippage_bps=_slip(last, base_price),
                        book_used=True,
                    )
                return MatchResult(
                    filled_qty=0.0, avg_price=0.0, unfilled_qty=qty, slippage_bps=0.0, book_used=False
                )
            avg = cost / filled
            return MatchResult(
                filled_qty=filled,
                avg_price=avg,
                unfilled_qty=max(remaining, 0.0),
                slippage_bps=_slip(avg, base_price),
                book_used=True,
            )
        except Exception:  # noqa: BLE001 — 任何异常降级，不抛错
            return MatchResult(filled_qty=0.0, avg_price=0.0, unfilled_qty=qty, slippage_bps=0.0, book_used=False)


def _price(level: dict) -> float:
    try:
        return float(level.get("price", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _qty(level: dict) -> float:
    try:
        return float(level.get("quantity", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _slip(avg_price: float, base_price: float) -> float:
    """实际滑点（基点）：(avg − base) / base × 10000。"""
    if base_price <= 0 or avg_price <= 0:
        return 0.0
    return round((avg_price - base_price) / base_price * 10000.0, 4)


__all__ = ["MatchResult", "OrderBookMatchingEngine"]
