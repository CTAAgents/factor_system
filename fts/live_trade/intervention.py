"""
fts.live_trade.intervention — 人工干预接口（GAP-F01，v2.60.0）。

``InterventionController`` 提供人工干预通道（AGENTS.md 4.3）：
    - pause: 紧急暂停（拦截一切新信号下单）
    - resume: 恢复自动交易
    - request_all_close: 一键平仓（生成全仓平仓指令）
    - should_block: 信号下发前检查是否被干预拦截

权限最高：干预状态高于所有自动化逻辑，风控检查不得覆盖人工决定。

版本: v1.0.0（GAP-F01）
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class InterventionState(str, Enum):
    """干预状态。"""

    NORMAL = "NORMAL"            # 正常自动交易
    PAUSED = "PAUSED"            # 紧急暂停（拦截新信号）
    FLATTENING = "FLATTENING"    # 一键平仓执行中
    FLATTENED = "FLATTENED"      # 已全仓平仓（等待人工恢复）


@dataclass
class InterventionRecord:
    """干预事件记录。"""

    action: str  # pause | resume | all_close
    operator: str  # 操作人（人工）
    state: InterventionState
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    note: str = ""


@dataclass
class AllCloseInstruction:
    """一键平仓指令（覆盖全部持仓）。"""

    instruction_id: str = field(
        default_factory=lambda: f"ac_{uuid.uuid4().hex[:12]}"
    )
    scope: str = "all"  # 全仓
    reason: str = "manual all-close intervention"
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class InterventionController:
    """人工干预控制器（权限高于自动化）。"""

    # 权限标识：高于所有自动化逻辑
    AUTHORITY = "highest"

    def __init__(self) -> None:
        self._state: InterventionState = InterventionState.NORMAL
        self._history: list[InterventionRecord] = []

    @property
    def state(self) -> InterventionState:
        return self._state

    def is_paused(self) -> bool:
        """是否处于暂停/平仓中（拦截新信号）。"""
        return self._state != InterventionState.NORMAL

    def pause(self, operator: str = "manual", note: str = "") -> InterventionRecord:
        """紧急暂停：拦截一切新信号下单。"""
        record = InterventionRecord(
            action="pause", operator=operator, state=InterventionState.PAUSED, note=note,
        )
        self._state = InterventionState.PAUSED
        self._history.append(record)
        logger.warning("[Intervention] 紧急暂停 [operator=%s, note=%s]", operator, note)
        return record

    def resume(self, operator: str = "manual", note: str = "") -> InterventionRecord:
        """恢复自动交易。"""
        record = InterventionRecord(
            action="resume", operator=operator, state=InterventionState.NORMAL, note=note,
        )
        self._state = InterventionState.NORMAL
        self._history.append(record)
        logger.info("[Intervention] 恢复自动交易 [operator=%s, note=%s]", operator, note)
        return record

    def request_all_close(
        self, operator: str = "manual", note: str = ""
    ) -> tuple[InterventionRecord, AllCloseInstruction]:
        """一键平仓：生成全仓平仓指令并进入 FLATTENING。

        Returns:
            (干预记录, 全仓平仓指令)
        """
        record = InterventionRecord(
            action="all_close", operator=operator,
            state=InterventionState.FLATTENING, note=note,
        )
        self._state = InterventionState.FLATTENING
        self._history.append(record)
        logger.warning("[Intervention] 一键平仓请求 [operator=%s, note=%s]", operator, note)
        return record, AllCloseInstruction(reason=note or "manual all-close intervention")

    def mark_flattened(self, operator: str = "manual") -> None:
        """标记全仓平仓完成（状态 → FLATTENED，仍拦截新信号直至人工恢复）。"""
        self._state = InterventionState.FLATTENED
        self._history.append(InterventionRecord(
            action="flattened", operator=operator, state=InterventionState.FLATTENED,
        ))
        logger.warning("[Intervention] 全仓平仓完成，等待人工恢复 [operator=%s]", operator)

    def should_block(self) -> bool:
        """信号下发前检查：干预激活时拦截一切新信号。"""
        return self.is_paused()

    def history(self) -> list[InterventionRecord]:
        """干预历史（审计留痕）。"""
        return list(self._history)


__all__ = [
    "InterventionState",
    "InterventionRecord",
    "AllCloseInstruction",
    "InterventionController",
]
