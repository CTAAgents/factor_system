"""
loop_engine/state.py — 演化状态管理 + trace_id 全链路

HARNESS §trace_id 全链路: trace_id 必须贯穿所有模块、文档和日志。

存储:
    memory/evolution/state.json            当前状态
    memory/evolution/state.json.backup     自动备份

版本: v8.10.0
"""

from __future__ import annotations

import json
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from .contracts import (
    DEFAULT_BUDGET_CONFIG,
    EVOLUTION_VERSION,
    EvolutionState,
)


# ─── 常量 ─────────────────────────────────────────────────

STATE_FILE_NAME: str = "state.json"
BACKUP_FILE_NAME: str = "state.json.backup"


class StateError(Exception):
    """状态文件操作失败。"""


# ─── trace_id 生成 ────────────────────────────────────────

def generate_trace_id(prefix: str = "l2") -> str:
    """生成全局唯一 trace_id: <prefix>_<8hex>_<timestamp>。

    格式: l2_3f9a2b1c_20260718T001230
    """
    rand = secrets.token_hex(4)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"{prefix}_{rand}_{ts}"


def generate_run_id() -> str:
    """生成演化运行 ID: run_<8hex>_<timestamp>。"""
    rand = secrets.token_hex(4)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"run_{rand}_{ts}"


# ─── 状态管理器 ───────────────────────────────────────────

class EvolutionStateManager:
    """演化状态文件管理器。

    约束:
        1. 每次写入前自动备份到 state.json.backup
        2. 状态文件损坏时从 backup 恢复；若无 backup 则冷启动
        3. version 字段必须等于 EVOLUTION_VERSION，否则报错
        4. trace_id 必须贯穿所有写入

    Usage:
        manager = EvolutionStateManager("memory/evolution")
        state = manager.load_or_init()
        state["last_generation"] += 1
        manager.save(state)
    """

    def __init__(self, memory_dir: str | Path = "memory/evolution"):
        self.memory_dir = Path(memory_dir)
        self.state_file = self.memory_dir / STATE_FILE_NAME
        self.backup_file = self.memory_dir / BACKUP_FILE_NAME
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def load_or_init(
        self,
        budget_limit: Optional[int] = None,
    ) -> EvolutionState:
        """加载状态文件；若不存在或损坏则初始化新状态。

        Args:
            budget_limit: 预算上限（仅初始化时生效）

        Returns:
            EvolutionState
        """
        # 优先加载主状态文件
        state = self._try_load(self.state_file)
        if state is None:
            # 主文件不可用，尝试备份
            state = self._try_load(self.backup_file)
            if state is not None:
                # 从备份恢复
                self._write(state)
            else:
                # 冷启动
                state = self._init_state(budget_limit)
                self._write(state)
        return state

    def save(self, state: EvolutionState) -> None:
        """保存状态 — 先写主文件，再镜像到 backup。

        backup 始终反映最新已知良好状态，主文件外部损坏时可从 backup 恢复最新数据。
        """
        # 版本一致性检查
        if state.get("version") != EVOLUTION_VERSION:
            raise StateError(
                f"状态版本不匹配: {state.get('version')} != {EVOLUTION_VERSION}"
            )
        # 更新时间戳
        state["last_updated"] = datetime.now().isoformat()
        # 先写主文件
        self._write(state)
        # 再镜像到 backup（保证 backup 始终反映最新已知良好状态）
        try:
            shutil.copy2(self.state_file, self.backup_file)
        except OSError as e:
            raise StateError(f"备份失败: {e}") from e

    def mark_running(self, run_id: Optional[str] = None) -> EvolutionState:
        """标记状态为 running（演化开始）。"""
        state = self.load_or_init()
        state["run_id"] = run_id or generate_run_id()
        state["started_at"] = datetime.now().isoformat()
        state["status"] = "running"
        state["last_error"] = None
        self.save(state)
        return state

    def mark_completed(self, state: EvolutionState) -> None:
        """标记状态为 completed。"""
        state["status"] = "completed"
        self.save(state)

    def mark_paused(self, state: EvolutionState, reason: str = "") -> None:
        """标记状态为 paused。"""
        state["status"] = "paused"
        if reason:
            state["last_error"] = reason
        self.save(state)

    def mark_circuit_broken(self, state: EvolutionState, reason: str) -> None:
        """标记状态为 circuit_broken — 熔断。"""
        state["status"] = "circuit_broken"
        state["last_error"] = reason
        self.save(state)

    def add_tokens(self, state: EvolutionState, tokens: int) -> None:
        """累加 token 消耗。"""
        state["tokens_consumed"] = state.get("tokens_consumed", 0) + tokens
        self.save(state)

    def increment_evaluated(self, state: EvolutionState, count: int = 1) -> None:
        """累加评估因子数。"""
        state["total_factors_evaluated"] = (
            state.get("total_factors_evaluated", 0) + count
        )
        self.save(state)

    def increment_promoted(self, state: EvolutionState, count: int = 1) -> None:
        """累加晋级因子数。"""
        state["total_factors_promoted"] = (
            state.get("total_factors_promoted", 0) + count
        )
        self.save(state)

    def add_experience_ref(self, state: EvolutionState, trace_id: str) -> None:
        """添加经验链 trace_id 引用。"""
        refs = state.get("experience_chain_ref", [])
        if trace_id not in refs:
            refs.append(trace_id)
            state["experience_chain_ref"] = refs
            self.save(state)

    # ─── 内部方法 ───

    def _try_load(self, fp: Path) -> Optional[EvolutionState]:
        """尝试加载 JSON 状态文件。失败返回 None。"""
        if not fp.exists():
            return None
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            # 版本检查（缺失视为不匹配）
            if data.get("version") != EVOLUTION_VERSION:
                return None
            return EvolutionState(**data)  # type: ignore[typeddict-item]
        except (json.JSONDecodeError, TypeError, ValueError):
            return None

    def _write(self, state: EvolutionState) -> None:
        """写入状态文件（不备份）。"""
        self.state_file.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _init_state(budget_limit: Optional[int]) -> EvolutionState:
        """初始化新状态。"""
        return EvolutionState(
            run_id=generate_run_id(),
            started_at=datetime.now().isoformat(),
            last_generation=0,
            total_factors_evaluated=0,
            total_factors_promoted=0,
            tokens_consumed=0,
            budget_limit=budget_limit or DEFAULT_BUDGET_CONFIG["nightly_token_limit"],
            status="running",
            last_error=None,
            experience_chain_ref=[],
            last_updated=datetime.now().isoformat(),
            version=EVOLUTION_VERSION,
        )


__all__ = [
    "STATE_FILE_NAME",
    "BACKUP_FILE_NAME",
    "StateError",
    "EvolutionStateManager",
    "generate_trace_id",
    "generate_run_id",
]
