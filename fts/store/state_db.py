"""
fts/store/state_db.py — 运行状态库（State KV Store）

plans/29 Phase 2：将分散的运行时状态 JSON（evolution/portfolio/meta_loop/
extractors/loop 的 state.json、combo_history、drift_history、权重快照等）
收敛至 `data/state.duckdb`，按「当前状态表 + 历史追加表」建模（机构
「状态与数据分离 + 事件追加可回放」实践）。

Schema:
    state_kv      当前状态（UPSERT，namespace+key 主键，JSON 值）
    state_history 历史追加（seq 自增，每条写入留痕，可回放/审计）

设计要点:
    - 状态为异构扁平 JSON，统一以 (namespace, key) 路由，避免按域建表爆炸
    - 每次 upsert 同时写 history（追加语义，同 (ns,key) 可多版本）
    - snapshot()/get() 支持「无 state.json 从 DuckDB 冷启动」验收

用法:
    from fts.store.state_db import StateKVStore

    store = StateKVStore("data/state.duckdb")
    store.upsert("portfolio", "futures_dynamic_pool", {...})
    val = store.get("portfolio", "futures_dynamic_pool")   # dict | None
    snap = store.snapshot()                                 # 全量 dump
    store.close()
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE_DB = PROJECT_ROOT / "data" / "state.duckdb"

# ─── DDL ─────────────────────────────────────────────────

_CREATE_STATE_KV = """
CREATE TABLE IF NOT EXISTS state_kv (
    namespace   VARCHAR NOT NULL,
    key         VARCHAR NOT NULL,
    value       JSON,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    run_id      VARCHAR DEFAULT '',
    PRIMARY KEY (namespace, key)
);
"""

_CREATE_STATE_HISTORY = """
CREATE TABLE IF NOT EXISTS state_history (
    seq         BIGINT PRIMARY KEY,
    namespace   VARCHAR NOT NULL,
    key         VARCHAR NOT NULL,
    value       JSON,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    run_id      VARCHAR DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_state_history_ns_key
    ON state_history(namespace, key, seq);
"""


class StateKVStore:
    """运行状态 KV 存储（DuckDB 双表：当前 + 历史追加）。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        import duckdb

        self._db_path = Path(db_path or DEFAULT_STATE_DB)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = duckdb.connect(str(self._db_path))
        self.init_schema()

    def init_schema(self) -> None:
        """幂等建表。"""
        self._conn.execute(_CREATE_STATE_KV)
        self._conn.execute(_CREATE_STATE_HISTORY)

    def upsert(
        self,
        namespace: str,
        key: str,
        value: dict[str, Any] | list[Any],
        run_id: str = "",
        ts: str | None = None,
    ) -> int:
        """写入当前状态并追加历史记录，返回 history seq。

        Args:
            namespace: 状态域（evolution/portfolio/meta_loop/...）
            key: 状态键（state / combo_history/{id} / futures_dynamic_pool / ...）
            value: 状态内容（dict 或 list，JSON 序列化存储）
            run_id: 产生该状态的运行标识（trace_id 贯穿）
            ts: 显式时间戳（ISO），缺省当前时间
        """
        value_json = json.dumps(value, ensure_ascii=False, default=str)
        ts_val = ts or datetime.now().isoformat()
        self._conn.execute(
            """
            INSERT INTO state_kv (namespace, key, value, updated_at, run_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (namespace, key)
            DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, run_id = excluded.run_id
            """,
            [namespace, key, value_json, ts_val, run_id],
        )
        seq = int(self._conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM state_history").fetchone()[0])
        self._conn.execute(
            """
            INSERT INTO state_history (seq, namespace, key, value, recorded_at, run_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [seq, namespace, key, value_json, ts_val, run_id],
        )
        return seq

    def get(self, namespace: str, key: str) -> dict[str, Any] | list[Any] | None:
        """读取当前状态值（不存在返回 None）。"""
        row = self._conn.execute(
            "SELECT value FROM state_kv WHERE namespace = ? AND key = ?",
            [namespace, key],
        ).fetchone()
        if not row or row[0] is None:
            return None
        return json.loads(row[0])

    def get_all(self, namespace: str) -> dict[str, Any]:
        """读取某域全部当前状态 {key: value}。"""
        rows = self._conn.execute("SELECT key, value FROM state_kv WHERE namespace = ?", [namespace]).fetchall()
        return {k: json.loads(v) for k, v in rows if v is not None}

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """全量当前状态 {namespace: {key: value}}（冷启动/对账用）。"""
        result: dict[str, dict[str, Any]] = {}
        for ns in [r[0] for r in self._conn.execute("SELECT DISTINCT namespace FROM state_kv").fetchall()]:
            result[ns] = self.get_all(ns)
        return result

    def history(self, namespace: str | None = None, key: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        """查询历史追加记录（可回放/审计）。"""
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
            self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> "StateKVStore":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
