"""
fts.live_trade.book — tick 盘口契约（D.2 §4 新增）。

定义盘口快照数据结构，并提供从 tick 行构造盘口的辅助函数：
    - ``BookLevel``:           单档价格/数量
    - ``OrderBookSnapshot``:   盘口快照（bid/ask 档位 + last/tick_size）
    - ``build_book_from_ticks``: 从 tick 行构造盘口（同价累加、depth 截断）

降级语义: tick 行缺失盘口字段时回退 last_price 单档构造（量 0）；
无任何有效行返回 None（调用方走 bps 降级）。

FTS 角色边界: 只提供撮合输入的数据契约，真实盘口数据由数据源层提供。
"""

from __future__ import annotations

from typing import Any, TypedDict


class BookLevel(TypedDict, total=False):
    price: float
    quantity: float  # 该档位数量（股/手）


class OrderBookSnapshot(TypedDict, total=False):
    symbol: str
    ts: str                     # 快照时间（ISO）
    bid_levels: list[BookLevel]  # 买盘档（价格降序，最优在前）
    ask_levels: list[BookLevel]  # 卖盘档（价格升序，最优在前）
    last_price: float
    tick_size: float             # 最小变动价位


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f > 0 else default


def build_book_from_ticks(
    symbol: str,
    tick_rows: list[dict[str, Any]],
    depth: int = 5,
    tick_size: float = 0.01,
) -> OrderBookSnapshot | None:
    """从 tick 行构造盘口快照。

    Args:
        symbol: 标的代码
        tick_rows: tick 行列表（含 bid/ask 价量字段；常见字段名:
            bid_price1/ask_price1... 或 bid_p/ask_p 五档数组；量字段
            bid_volume1/ask_volume1...）
        depth: 保留档位数（默认 5）
        tick_size: 最小变动价位（默认 0.01，期货品种由调用方指定）

    Returns:
        OrderBookSnapshot；无任何有效行情行返回 None。
    """
    if not tick_rows:
        return None

    bid_map: dict[float, float] = {}
    ask_map: dict[float, float] = {}
    last_price = 0.0
    ts = ""

    for row in tick_rows:
        if not isinstance(row, dict):
            continue
        ts = ts or str(row.get("datetime", row.get("ts", "")))
        last_price = _to_float(row.get("last_price", row.get("close", last_price)), last_price)

        # 五档数组形式（bid_p=[...]/ask_p=[...] + bid_v/ask_v）
        bid_p = row.get("bid_p") or row.get("bid_price") or row.get("bid_prices")
        ask_p = row.get("ask_p") or row.get("ask_price") or row.get("ask_prices")
        bid_v = row.get("bid_v") or row.get("bid_volume") or row.get("bid_volumes")
        ask_v = row.get("ask_v") or row.get("ask_volume") or row.get("ask_volumes")
        if bid_p is not None and isinstance(bid_p, (list, tuple)) and len(bid_p) > 0:
            for i, p in enumerate(bid_p):
                pv = _to_float(p)
                if pv <= 0:
                    continue
                qv = _to_float(bid_v[i]) if bid_v is not None and i < len(bid_v) else 0.0
                bid_map[pv] = bid_map.get(pv, 0.0) + qv
            for i, p in enumerate(ask_p or ()):
                pv = _to_float(p)
                if pv <= 0:
                    continue
                qv = _to_float(ask_v[i]) if ask_v is not None and i < len(ask_v) else 0.0
                ask_map[pv] = ask_map.get(pv, 0.0) + qv
            continue

        # 单档字段形式（bid_price1/ask_price1 ...）
        for i in range(1, depth + 1):
            bp = _to_float(row.get(f"bid_price{i}"))
            if bp > 0:
                bq = _to_float(row.get(f"bid_volume{i}"), 0.0)
                bid_map[bp] = bid_map.get(bp, 0.0) + bq
            ap = _to_float(row.get(f"ask_price{i}"))
            if ap > 0:
                aq = _to_float(row.get(f"ask_volume{i}"), 0.0)
                ask_map[ap] = ask_map.get(ap, 0.0) + aq

    if not bid_map and not ask_map and last_price <= 0:
        return None

    # 档位排序：bid 价格降序（最优在前），ask 价格升序（最优在前）
    bids: list[BookLevel] = [
        {"price": float(p), "quantity": float(bid_map[p])} for p in sorted(bid_map.keys(), reverse=True)
    ]
    asks: list[BookLevel] = [
        {"price": float(p), "quantity": float(ask_map[p])} for p in sorted(ask_map.keys())
    ]

    # 无档位但有 last_price → 单档兜底（量 0，撮合将按该价成交）
    if not bids:
        bids = [{"price": last_price, "quantity": 0.0}]
    if not asks:
        asks = [{"price": last_price, "quantity": 0.0}]

    return OrderBookSnapshot(
        symbol=symbol,
        ts=ts,
        bid_levels=bids[:depth],
        ask_levels=asks[:depth],
        last_price=last_price,
        tick_size=float(tick_size),
    )


__all__ = ["BookLevel", "OrderBookSnapshot", "build_book_from_ticks"]
