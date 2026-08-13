"""
fts/store/state_db.py — 运行状态库（State KV Store）

E.3（S2）将 L4 运行状态库后端由 DuckDB 切换为 SQLite（WAL 模式），
解决「演化进程持有写锁时外部只读亦被锁」的并发阻塞问题：

    - SQLite WAL：多读单写不互斥，写连接存活期间外部只读不受阻塞
    - 写事务短促（BEGIN IMMEDIATE ... COMMIT），跨进程写冲突由
      busy_timeout 等待而非立即失败
    - 每次 upsert 单事务包裹（state_kv + state_history 双表原子），
      消除旧实现「双语句无事务」的半写入风险
    - seq 由 INTEGER PRIMARY KEY AUTOINCREMENT 分配（单调、删除不重用），
      返回 last_insert_rowid()

Schema（与旧 DuckDB 表结构同构）:
    state_kv      当前状态（UPSERT，namespace+key 主键，JSON 值 TEXT）
    state_history 历史追加（seq 自增，每条写入留痕，可回放/审计）

API 契约与调用方完全兼容（E.3 §3.2），5 个调用模块零改动。

用法:
    from fts.store.state_db import StateKVStore

    store = StateKVStore("data/state.db")
    store.upsert("portfolio", "futures_dynamic_pool", {...})
    val = store.get("portfolio", "futures_dynamic_pool")   # dict | None
    snap = store.snapshot()                                 # 全量 dump
    store.close()
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DB = PROJECT_ROOT / "data" / "state.db"

# ─── DDL ─────────────────────────────────────────────────

_CREATE_STATE_KV = """
CREATE TABLE IF NOT EXISTS state_kv (
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    run_id      TEXT DEFAULT '',
    PRIMARY KEY (namespace, key)
);
"""

_CREATE_STATE_HISTORY = """
CREATE TABLE IF NOT EXISTS state_history (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    namespace   TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP,
    run_id      TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_state_history_ns_key
    ON state_history(namespace, key, seq);
"""

# 写冲突等待上限（ms）：跨进程并发写时等待而非立即报 busy
_BUSY_TIMEOUT_MS = 5000


class StateKVStore:
    """运行状态 KV 存储（SQLite WAL 双表：当前 + 历史追加）。

    并发模型（E.3 §2.4）:
        - 进程内：单连接 + threading.Lock，写操作（含 seq 分配、双表
          upsert）在锁内显式事务串行执行
        - 跨进程：WAL 下读写不互斥；写冲突由 busy_timeout 等待 + 短事务
          控制
        - check_same_thread=False：进程级单例被多模块共享，线程安全由
          threading.Lock 保证
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path or DEFAULT_STATE_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = sqlite3.connect(
            str(self._db_path), check_same_thread=False
        )
        self._conn.execute("PRAGMA journal_mode=WAL")  # 多读单写不互斥
        self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self.init_schema()

    def init_schema(self) -> None:
        """幂等建表。"""
        assert self._conn is not None
        # executescript 支持多语句（CREATE TABLE + CREATE INDEX）
        self._conn.executescript(_CREATE_STATE_KV + "\n" + _CREATE_STATE_HISTORY)

    def upsert(
        self,
        namespace: str,
        key: str,
        value: dict[str, Any] | list[Any],
        run_id: str = "",
        ts: str | None = None,
    ) -> int:
        """写入当前状态并追加历史记录（单事务原子），返回 history seq。

        Args:
            namespace: 状态域（evolution/portfolio/meta_loop/...）
            key: 状态键（state / combo_history/{id} / futures_dynamic_pool / ...）
            value: 状态内容（dict 或 list，JSON 序列化存储）
            run_id: 产生该状态的运行标识（trace_id 贯穿）
            ts: 显式时间戳（ISO），缺省当前时间
        """
        assert self._conn is not None
        value_json = json.dumps(value, ensure_ascii=False, default=str)
        ts_val = ts or datetime.now().isoformat()
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    """
                    INSERT INTO state_kv (namespace, key, value, updated_at, run_id)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT (namespace, key)
                    DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at,
                                  run_id = excluded.run_id
                    """,
                    [namespace, key, value_json, ts_val, run_id],
                )
                cur = self._conn.execute(
                    """
                    INSERT INTO state_history (namespace, key, value, recorded_at, run_id)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [namespace, key, value_json, ts_val, run_id],
                )
                seq = int(cur.lastrowid)
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
        return seq

    def get(self, namespace: str, key: str) -> dict[str, Any] | list[Any] | None:
        """读取当前状态值（不存在返回 None）。"""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT value FROM state_kv WHERE namespace = ? AND key = ?",
            [namespace, key],
        ).fetchone()
        if not row or row[0] is None:
            return None
        return json.loads(row[0])

    def get_all(self, namespace: str) -> dict[str, Any]:
        """读取某域全部当前状态 {key: value}。"""
        assert self._conn is not None
        rows = self._conn.execute("SELECT key, value FROM state_kv WHERE namespace = ?", [namespace]).fetchall()
        return {k: json.loads(v) for k, v in rows if v is not None}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """全量当前状态 {namespace: {key: value}}（冷启动/对账用）。"""
        assert self._conn is not None
        result: dict[str, dict[str, Any]] = {}
        for ns in [r[0] for r in self._conn.execute("SELECT DISTINCT namespace FROM state_kv").fetchall()]:
            result[ns] = self.get_all(ns)
        return result

    def history(self, namespace: str | None = None, key: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        """查询历史追加记录（可回放/审计）。"""
        assert self._conn is not None
        sql = "SELECT seq, namespace, key, value, recorded_at, run_id FROM state_history"
        conds: list[str] = []
        params: list[Any] = []
        if namespace:
            conds.append("namespace = ?")
            params.append(namespace)
        if key:
            conds.append("key = ?")
            params.append(key)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY seq DESC LIMIT ?"
        params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "seq": r[0],
                "namespace": r[1],
                "key": r[2],
                "value": json.loads(r[3]) if r[3] else None,
                "recorded_at": str(r[4]),
                "run_id": r[5],
            }
            for r in rows
        ]

    def close(self) -> None:
        """关闭连接（幂等）。"""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "StateKVStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ─── 进程级单例连接 ───────────────────────────────────────

_state_store_singleton: StateKVStore | None = None


def get_state_store() -> StateKVStore:
    """返回进程级懒加载的 state.db 连接（复用，进程退出自动关闭）。

    state 管理器（evolution/portfolio/meta_loop 等）高频 save() 复用同一连接，
    避免每次写入重新 connect 的性能退化；同进程多管理器共享。
    """
    global _state_store_singleton
    if _state_store_singleton is None:
        _state_store_singleton = StateKVStore()
    return _state_store_singleton
