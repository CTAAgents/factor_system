"""
fts.workflow.store — WorkFlow 运行状态持久化（SQLite WAL）。

存储:
    - workflow_runs: 运行批次（run_id / 状态 / 当前阶段）
    - stage_runs:    阶段动作执行记录（状态 / 退出码 / 日志 / 产物）

线程安全: SQLite WAL（多读单写），写操作短事务，单进程内多线程经锁串行化。
零未来函数 / 崩溃可回放。

版本: v1.0.0
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "workflow.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id          TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    status          TEXT NOT NULL,          -- running | success | failed | aborted
    current_stage   TEXT,
    started_stage   TEXT,
    updated_at      TEXT
);
CREATE TABLE IF NOT EXISTS stage_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL,
    stage_id    TEXT NOT NULL,
    action_id   TEXT NOT NULL,
    status      TEXT NOT NULL,              -- pending | running | success | failed | skipped
    exit_code   INTEGER,
    started_at  TEXT,
    ended_at    TEXT,
    log         TEXT DEFAULT '',
    output      TEXT DEFAULT ''             -- JSON 产物
);
CREATE INDEX IF NOT EXISTS idx_stage_runs_run ON stage_runs(run_id, stage_id);
"""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowStore:
    """WorkFlow 运行状态存储（SQLite WAL）。"""

    def __init__(self, db_path: str | Path | None = None):
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(SCHEMA)

    # ─── 运行批次 ───────────────────────────────────────────────
    def create_run(self, started_stage: str) -> str:
        run_id = f"wf_{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO workflow_runs (run_id, created_at, status, started_stage) VALUES (?,?,?,?)",
                (run_id, _utc_now(), "running", started_stage),
            )
        logger.info("[WorkflowStore] 创建运行: %s (start=%s)", run_id, started_stage)
        return run_id

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [run_id]
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE workflow_runs SET {cols} WHERE run_id=?", vals)

    def get_run(self, run_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT run_id, created_at, status, current_stage, started_stage, updated_at "
                "FROM workflow_runs WHERE run_id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row[0],
            "created_at": row[1],
            "status": row[2],
            "current_stage": row[3],
            "started_stage": row[4],
            "updated_at": row[5],
        }

    def list_runs(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT run_id, created_at, status, current_stage FROM workflow_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{"run_id": r[0], "created_at": r[1], "status": r[2], "current_stage": r[3]} for r in rows]

    # ─── 阶段动作记录 ───────────────────────────────────────────
    def create_stage_run(self, run_id: str, stage_id: str, action_id: str) -> int:
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO stage_runs (run_id, stage_id, action_id, status) VALUES (?,?,?,?)",
                (run_id, stage_id, action_id, "pending"),
            )
            return int(cur.lastrowid)

    def update_stage_run(self, stage_run_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [stage_run_id]
        with self._lock, self._conn() as conn:
            conn.execute(f"UPDATE stage_runs SET {cols} WHERE id=?", vals)

    def append_log(self, stage_run_id: int, text: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE stage_runs SET log = log || ? WHERE id=?",
                (text, stage_run_id),
            )

    def get_stage_runs(self, run_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, stage_id, action_id, status, exit_code, started_at, ended_at, log, output "
                "FROM stage_runs WHERE run_id=? ORDER BY id",
                (run_id,),
            ).fetchall()
        out = []
        for r in rows:
            item = {
                "id": r[0],
                "stage_id": r[1],
                "action_id": r[2],
                "status": r[3],
                "exit_code": r[4],
                "started_at": r[5],
                "ended_at": r[6],
                "log": r[7] or "",
                "output": _parse_json(r[8]),
            }
            out.append(item)
        return out

    def get_stage_run(self, stage_run_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, stage_id, action_id, status, exit_code, started_at, ended_at, log, output "
                "FROM stage_runs WHERE id=?",
                (stage_run_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "stage_id": row[1],
            "action_id": row[2],
            "status": row[3],
            "exit_code": row[4],
            "started_at": row[5],
            "ended_at": row[6],
            "log": row[7] or "",
            "output": _parse_json(row[8]),
        }


def _parse_json(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


__all__ = ["WorkflowStore", "SCHEMA"]
