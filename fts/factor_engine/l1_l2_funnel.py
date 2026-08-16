"""
fts/factor_engine/l1_l2_funnel.py — plans/44 Phase 3 D1/D2 L1→L2 闭环漏斗

D1 转化率统计: state_kv 新增 `meta_loop/{market}/l1_l2_funnel` 键
    {injected, l2_consumed, l2_promoted, *_at 时间戳}，
    L1 注入回写 injected、L2 消费 l1_injected_* 回写 consumed、晋升回写 promoted。

D2 消费速率监控: `funnel_report` 计算转化率与积压 warning——
    当存在未消费候选（injected - consumed > 0）且最近一次消费距今超过
    `l1_l2_backlog_days`（默认 7 天）时输出 warning（防 L1 无限注入）。

存储: fts/store/state_db.py StateKVStore（进程级 get_state_store 单例，SQLite WAL）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FUNNEL_NS = "meta_loop"
_FUNNEL_KEYS = ("injected", "l2_consumed", "l2_promoted")


def funnel_key(market: str) -> str:
    """state_kv 键：`{market}/l1_l2_funnel`。"""
    return f"{market}/l1_l2_funnel"


def _empty_funnel() -> dict[str, Any]:
    return {
        "injected": 0,
        "l2_consumed": 0,
        "l2_promoted": 0,
        "injected_at": "",
        "l2_consumed_at": "",
        "l2_promoted_at": "",
        "updated_at": "",
    }


def _get_store() -> Any:
    """进程级 state.db 单例（与 meta_loop/evolution 状态存储同源 SSOT）。"""
    from fts.store.state_db import get_state_store

    return get_state_store()


def funnel_record(
    store: Optional[Any] = None,
    market: str = "futures",
    *,
    injected: int = 0,
    consumed: int = 0,
    promoted: int = 0,
    run_id: str = "",
) -> dict[str, Any]:
    """读-改-写累计漏斗统计（增量累加，幂等可重入）。返回更新后的漏斗 dict。"""
    store = store or _get_store()
    key = funnel_key(market)
    cur = dict(store.get(_FUNNEL_NS, key) or {})
    if not cur:
        cur = _empty_funnel()
    now = datetime.now().isoformat()
    if injected:
        cur["injected"] = int(cur.get("injected", 0)) + injected
        cur["injected_at"] = now
    if consumed:
        cur["l2_consumed"] = int(cur.get("l2_consumed", 0)) + consumed
        cur["l2_consumed_at"] = now
    if promoted:
        cur["l2_promoted"] = int(cur.get("l2_promoted", 0)) + promoted
        cur["l2_promoted_at"] = now
    cur["updated_at"] = now
    store.upsert(_FUNNEL_NS, key, cur, run_id=run_id)
    return cur


def funnel_report(
    store: Optional[Any] = None,
    markets: tuple[str, ...] = ("futures", "energy"),
    backlog_days: int = 7,
) -> list[dict[str, Any]]:
    """读取全市场漏斗并计算转化率/积压 warning（D1/D2 报告数据源）。

    Returns:
        list[dict]: market/injected/l2_consumed/l2_promoted/consume_rate/
                    promote_rate/backlog/warning/updated_at；无记录市场跳过。
    """
    store = store or _get_store()
    now = datetime.now()
    out: list[dict[str, Any]] = []
    for m in markets:
        cur = dict(store.get(_FUNNEL_NS, funnel_key(m)) or {})
        if not cur:
            continue
        injected = int(cur.get("injected", 0))
        consumed = int(cur.get("l2_consumed", 0))
        promoted = int(cur.get("l2_promoted", 0))
        backlog = injected - consumed
        warning = ""
        if backlog > 0:
            consumed_at = cur.get("l2_consumed_at", "")
            last_consume = now
            if consumed_at:
                try:
                    last_consume = datetime.fromisoformat(consumed_at)
                except ValueError:
                    last_consume = now
            if (now - last_consume) > timedelta(days=backlog_days):
                warning = (
                    f"积压 {backlog} 个 L1 候选超过 {backlog_days} 天未被 L2 消费"
                    f"（最后消费 {last_consume.date().isoformat()}），建议核查 L2 消费链路"
                )
        out.append(
            {
                "market": m,
                "injected": injected,
                "l2_consumed": consumed,
                "l2_promoted": promoted,
                "consume_rate": round(consumed / injected, 4) if injected else 0.0,
                "promote_rate": round(promoted / consumed, 4) if consumed else 0.0,
                "backlog": backlog,
                "warning": warning,
                "updated_at": cur.get("updated_at", ""),
            }
        )
    return out
