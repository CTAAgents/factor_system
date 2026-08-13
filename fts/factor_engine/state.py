"""
loop_engine/state.py — 演化状态管理 + trace_id 全链路

HARNESS §trace_id 全链路: trace_id 必须贯穿所有模块、文档和日志。

存储:
    memory/evolution/state.json            当前状态
    memory/evolution/state.json.backup     自动备份

版本: v1.1.0（与 FTS 同步）
"""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Optional

from .contracts import (
    DEFAULT_BUDGET_CONFIG,
    STATE_SCHEMA_VERSION,
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


def generate_session_id() -> str:
    """生成 CLI 会话 ID: session_<8hex>_<timestamp>。

    作用域为整个 CLI 会话（一次 `fts` 命令执行），
    由 CLI 入口生成并传递到各子命令作为日志聚合标识。
    """
    return generate_trace_id("session")


# ─── 状态管理器 ───────────────────────────────────────────


class EvolutionStateManager:
    """演化状态管理器（SSOT 为 state.duckdb，plans/29 P4 读路径切换）。

    约束:
        1. 每次写入落 state.duckdb（UPSERT + 历史追加），JSON 不再回写
           （旧 state.json 退役为只读历史快照）
        2. 状态缺失/损坏时冷启动 _init_state
        3. version 字段必须等于 EVOLUTION_VERSION，否则报错
        4. trace_id（run_id）贯穿所有写入

    Usage:
        manager = EvolutionStateManager("memory/evolution")
        state = manager.load_or_init()
        state["last_generation"] += 1
        manager.save(state)
    """

    def __init__(self, memory_dir: str | Path = "memory/evolution", state_store=None):
        # 保留 memory_dir 以派生 namespace/key（stock 根目录 vs futures 子目录）
        self.memory_dir = Path(memory_dir)
        self._store = state_store  # None → 全局 SSOT（供测试注入临时 store）

    def _store_conn(self):
        """返回状态存储连接（注入的或全局 SSOT）。"""
        from fts.store.state_db import get_state_store

        return self._store if self._store is not None else get_state_store()

    def _ns_key(self) -> tuple[str, str]:
        """派生 state.duckdb 的 (namespace, key)。

        与 migrate_state_to_duckdb 规则一致：
            memory/evolution/state.json        → ("evolution", "state")
            memory/evolution/{parent}/state.json → ("evolution", "{parent}/state")
        """
        if self.memory_dir.name == "evolution":
            return "evolution", "state"
        return "evolution", f"{self.memory_dir.name}/state"

    def load_or_init(
        self,
        budget_limit: Optional[int] = None,
    ) -> EvolutionState:
        """从 state.duckdb 加载状态；缺失则冷启动初始化。

        Args:
            budget_limit: 预算上限（仅初始化时生效）

        Returns:
            EvolutionState
        """
        ns, key = self._ns_key()
        data = self._store_conn().get(ns, key)
        if isinstance(data, dict) and data.get("schema_version") == STATE_SCHEMA_VERSION:
            return EvolutionState(**data)  # type: ignore[typeddict-item]
        state = self._init_state(budget_limit)
        self.save(state)
        return state

    def save(self, state: EvolutionState) -> None:
        """保存状态 → 写 state.duckdb（SSOT，UPSERT + 历史追加）。"""
        # schema 版本一致性检查（仅状态结构变更时冷启动）
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise StateError(f"状态 schema 版本不匹配: {state.get('schema_version')} != {STATE_SCHEMA_VERSION}")
        state["last_updated"] = datetime.now().isoformat()
        ns, key = self._ns_key()
        self._store_conn().upsert(ns, key, state, run_id=state.get("run_id") or "")

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
        state["total_factors_evaluated"] = state.get("total_factors_evaluated", 0) + count
        self.save(state)

    def increment_promoted(self, state: EvolutionState, count: int = 1) -> None:
        """累加晋级因子数。"""
        state["total_factors_promoted"] = state.get("total_factors_promoted", 0) + count
        self.save(state)

    def add_experience_ref(self, state: EvolutionState, trace_id: str) -> None:
        """添加经验链 trace_id 引用。"""
        refs = state.get("experience_chain_ref", [])
        if trace_id not in refs:
            refs.append(trace_id)
            state["experience_chain_ref"] = refs
            self.save(state)

    def record_evolution_method(self, state: EvolutionState, method: str) -> None:
        """记录演化方法分布计数（GAP-S11: operator/gp/macro 占比可观测）。"""
        counts = state.get("evolution_method_counts", {})
        counts[method] = counts.get(method, 0) + 1
        state["evolution_method_counts"] = counts
        self.save(state)

    # ─── 内部方法 ───

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
            schema_version=STATE_SCHEMA_VERSION,
        )


__all__ = [
    "STATE_FILE_NAME",
    "BACKUP_FILE_NAME",
    "StateError",
    "EvolutionStateManager",
    "generate_trace_id",
    "generate_run_id",
    "generate_session_id",
]
