"""
fts.factor_engine.qa.status_board — 因子 7 状态机 + 质检状态看板（CTA 手册 6.8）。

对照《期货CTA多因子策略标准化作业手册》6.8 质检状态看板:
    状态: DRAFT → PENDING_QA → CORE ⇄ CANDIDATE → OBSERVATION ⇄ SUSPENDED → RETIRED
    权重: DRAFT 0% / PENDING_QA 0% / CORE ≤30% / CANDIDATE ≤15% /
          OBSERVATION ≤50%原权重 / SUSPENDED 0% / RETIRED 0%

状态流转图（手册 6.8）:
    DRAFT → PENDING_QA → CORE ⇄ CANDIDATE
                              ↓
                        OBSERVATION ⇄ SUSPENDED
                              ↓
                           RETIRED → (复审重新有效) → PENDING_QA

看板输出: 各状态因子数量统计 + 状态变动记录 + 预警因子清单。

落库: ``apply_status_transition`` 封装 factor_db.FactorStatusRepository
（log_transition 写 history + update_factor_status 更新 catalog），
流转不合法时拒绝并返回原因，零未来函数。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FactorStatus(str, Enum):
    """因子生命周期 7 状态（手册 6.8）。"""

    DRAFT = "DRAFT"
    PENDING_QA = "PENDING_QA"
    CORE = "CORE"
    CANDIDATE = "CANDIDATE"
    OBSERVATION = "OBSERVATION"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


# 状态权重上限（手册 6.8）
STATUS_MAX_WEIGHT: dict[str, float] = {
    "DRAFT": 0.0,
    "PENDING_QA": 0.0,
    "CORE": 0.30,
    "CANDIDATE": 0.15,
    "OBSERVATION": 0.50,
    "SUSPENDED": 0.0,
    "RETIRED": 0.0,
}

# 状态中文名（看板/文档展示，契约层统一命名）
STATUS_LABELS: dict[str, str] = {
    "DRAFT": "草稿",
    "PENDING_QA": "待质检",
    "CORE": "核心服役",
    "CANDIDATE": "候选服役",
    "OBSERVATION": "观察期",
    "SUSPENDED": "暂停",
    "RETIRED": "退役",
}

# 合法状态流转（手册 6.8 流转图）
STATUS_TRANSITIONS: dict[str, list[str]] = {
    "DRAFT": ["PENDING_QA"],
    "PENDING_QA": ["CORE", "CANDIDATE", "RETIRED"],
    "CORE": ["CANDIDATE", "OBSERVATION", "RETIRED"],
    "CANDIDATE": ["CORE", "OBSERVATION", "RETIRED"],
    "OBSERVATION": ["CORE", "CANDIDATE", "SUSPENDED", "RETIRED"],
    "SUSPENDED": ["OBSERVATION", "RETIRED"],
    "RETIRED": ["PENDING_QA"],
}

# 全量别名 → 唯一状态（契约层统一，v2.104.0+95）：
# 各模块历史命名（主表 factor_catalog.status / reaudit 处置 / EliteFactorTracker 快照）
# 统一归一到 FactorStatus 唯一状态，消除"一义多名"与"一名多义"混淆。
STATUS_ALIAS_MAP: dict[str, str] = {
    # 主表 factor_catalog.status（存量小写值）
    "ACTIVE": "CORE",
    "DEGRADED": "OBSERVATION",
    # reaudit 处置（status_history）
    "ACTIVE(SHADOW)": "OBSERVATION",  # 历史拼接怪名（v2.104.0+95 起新写入为 OBSERVATION）
    "SHADOW": "OBSERVATION",
    "RETAIN": "CORE",
    "RETIRE": "RETIRED",
    # EliteFactorTracker 衰减快照（A.2）
    "OBSERVING": "OBSERVATION",
    "DECAYING": "OBSERVATION",
    "CRITICAL_DECAY": "OBSERVATION",
    "DEPRECATED": "RETIRED",
}


def normalize_status(status: str) -> str:
    """规范化状态值：历史命名按 STATUS_ALIAS_MAP 归一到唯一状态，未知值原样返回。"""
    s = (status or "").upper()
    return STATUS_ALIAS_MAP.get(s, s)


def can_transition(from_status: str, to_status: str) -> bool:
    """状态流转是否合法（手册 6.8 流转图）。"""
    src = normalize_status(from_status)
    dst = normalize_status(to_status)
    return dst in STATUS_TRANSITIONS.get(src, [])


def max_weight_for_status(status: str) -> float:
    """返回状态对应权重上限（未知状态 0 兜底）。"""
    return STATUS_MAX_WEIGHT.get(normalize_status(status), 0.0)


def status_board(factors: list[dict[str, Any]]) -> dict:
    """质检状态看板统计（手册 6.8 看板输出）。

    Args:
        factors: 因子列表，每项含 status 字段（'active' 兼容归入 CORE）

    Returns:
        dict: {
            counts: {状态: 数量},
            total, active_serving: int,
            obs_warning: [{name, status}],
            report: str,
        }
    """
    counts: dict[str, int] = {}
    obs_warning: list[dict] = []
    for f in factors:
        st = normalize_status(f.get("status", ""))
        counts[st] = counts.get(st, 0) + 1
        if st in ("OBSERVATION", "SUSPENDED"):
            obs_warning.append({"name": f.get("name") or f.get("factor_id") or "?", "status": st})

    serving = counts.get("CORE", 0) + counts.get("CANDIDATE", 0)
    lines = ["质检状态看板（因子状态统计）"]
    for st in FactorStatus:
        label = STATUS_LABELS.get(st.value, "")
        lines.append(f"  {st.value}({label}): {counts.get(st.value, 0)}")
    lines.append(f"  服役中（CORE+CANDIDATE）: {serving}")
    if obs_warning:
        lines.append("  预警因子清单: " + ", ".join(f"{w['name']}({w['status']})" for w in obs_warning))

    return {
        "counts": counts,
        "total": len(factors),
        "serving": serving,
        "obs_warning": obs_warning,
        "report": "\n".join(lines),
    }


def apply_status_transition(
    status_repo: Any,
    factor_id: str,
    to_status: str,
    reason: str,
    from_status: Optional[str] = None,
    snapshot: Optional[dict] = None,
) -> dict:
    """执行一次状态流转并落库（手册 6.8 + factor_db 接入）。

    Args:
        status_repo: factor_db.FactorStatusRepository 实例
        factor_id: 因子 ID
        to_status: 目标状态
        reason: 流转原因
        from_status: 源状态（None 时从 repo 读取当前 status）
        snapshot: 流转时快照（可选）

    Returns:
        dict: {ok, from_status, to_status, reason, history_id | error}
    """
    dst = normalize_status(to_status)
    src = normalize_status(from_status) if from_status else None
    if src is None:
        cur = status_repo.get_history(factor_id)
        src = cur[-1]["to_status"] if cur else "DRAFT"

    if not can_transition(src, dst):
        return {
            "ok": False,
            "from_status": src,
            "to_status": dst,
            "reason": reason,
            "error": f"非法状态流转: {src} → {dst}",
        }

    history_id = status_repo.log_transition(factor_id, src, dst, reason, snapshot)
    status_repo.update_factor_status(factor_id, dst)
    logger.info("[QA] 状态流转 %s: %s → %s (%s)", factor_id, src, dst, reason)
    return {"ok": True, "from_status": src, "to_status": dst, "reason": reason, "history_id": history_id}


__all__ = [
    "FactorStatus",
    "STATUS_MAX_WEIGHT",
    "STATUS_LABELS",
    "STATUS_TRANSITIONS",
    "STATUS_ALIAS_MAP",
    "normalize_status",
    "can_transition",
    "max_weight_for_status",
    "status_board",
    "apply_status_transition",
]
