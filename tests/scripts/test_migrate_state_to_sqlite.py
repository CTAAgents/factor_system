"""E.3（S2）：scripts/migrate_state_to_sqlite.py 测试。

覆盖:
- 迁移闭环：DuckDB 源库 → SQLite 目标，行数一致、值 JSON 可解析
- seq 保序 + AUTOINCREMENT 接续（新增 upsert 从 max+1 继续）
- 幂等保护：目标已存在非空且未 --force → 拒绝
- --force 覆盖重建（脏数据被清空重建）
- 源库被写锁占用 → 降级拒绝（明确提示，不破坏目标）
- 源库缺失 → FileNotFoundError
"""

from __future__ import annotations

import json

import duckdb
import pytest

from fts.store.state_db import StateKVStore
from scripts.migrate_state_to_sqlite import migrate_state_db


def _build_duckdb_source(src, n_kv: int = 3, n_hist: int = 4) -> None:
    """构造与真实 state.duckdb 同构的源库。"""
    con = duckdb.connect(str(src))
    try:
        con.execute(
            """
            CREATE TABLE state_kv (
                namespace VARCHAR NOT NULL,
                key VARCHAR NOT NULL,
                value JSON,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                run_id VARCHAR DEFAULT '',
                PRIMARY KEY (namespace, key)
            )
            """
        )
        con.execute(
            """
            CREATE TABLE state_history (
                seq BIGINT PRIMARY KEY,
                namespace VARCHAR NOT NULL,
                key VARCHAR NOT NULL,
                value JSON,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                run_id VARCHAR DEFAULT ''
            )
            """
        )
        for i in range(n_kv):
            con.execute(
                "INSERT INTO state_kv (namespace, key, value, run_id) VALUES (?, ?, ?, ?)",
                ["portfolio", f"k{i}", json.dumps({"i": i}), f"r{i}"],
            )
        for i in range(n_hist):
            con.execute(
                "INSERT INTO state_history (seq, namespace, key, value, run_id) VALUES (?, ?, ?, ?, ?)",
                [i + 1, "portfolio", f"k{i % n_kv}", json.dumps({"i": i}), f"r{i}"],
            )
    finally:
        con.close()


class TestMigrate:
    def test_roundtrip_rows_and_values(self, tmp_path):
        src = tmp_path / "state.duckdb"
        tgt = tmp_path / "state.db"
        _build_duckdb_source(src, n_kv=3, n_hist=4)
        stats = migrate_state_db(source=src, target=tgt, trace_id="t1")
        assert stats["verified"] is True
        assert stats["kv_rows"] == 3
        assert stats["history_rows"] == 4
        assert stats["seq_range"] == [1, 4]
        # 目标可经 StateKVStore 读取，值 JSON 可解析
        store = StateKVStore(tgt)
        assert store.get("portfolio", "k0") == {"i": 0}
        assert store.get("portfolio", "k2") == {"i": 2}
        assert len(store.history(namespace="portfolio", limit=100)) == 4
        store.close()

    def test_seq_continuation_after_migrate(self, tmp_path):
        """迁移后新增 upsert 从 max seq+1 继续，不与历史冲突。"""
        src = tmp_path / "state.duckdb"
        tgt = tmp_path / "state.db"
        _build_duckdb_source(src, n_kv=1, n_hist=2)
        migrate_state_db(source=src, target=tgt, trace_id="t1")
        store = StateKVStore(tgt)
        seq = store.upsert("portfolio", "new_key", {"v": 1}, run_id="t2")
        assert seq == 3  # 历史 seq 为 1,2，接续到 3
        hist = store.history("portfolio", "new_key")
        assert hist[0]["seq"] == 3
        store.close()

    def test_idempotent_protection_without_force(self, tmp_path):
        """目标已存在非空且未 --force → 拒绝，不改动目标。"""
        src = tmp_path / "state.duckdb"
        tgt = tmp_path / "state.db"
        _build_duckdb_source(src, n_kv=2, n_hist=0)
        migrate_state_db(source=src, target=tgt, trace_id="t1")
        with pytest.raises(RuntimeError, match="--force"):
            migrate_state_db(source=src, target=tgt, trace_id="t2")
        store = StateKVStore(tgt)
        assert len(store.get_all("portfolio")) == 2  # 未被改动
        store.close()

    def test_force_rebuild_clears_dirty_data(self, tmp_path):
        """--force 覆盖重建：目标脏数据被清空并恢复迁移结果。"""
        src = tmp_path / "state.duckdb"
        tgt = tmp_path / "state.db"
        _build_duckdb_source(src, n_kv=2, n_hist=0)
        migrate_state_db(source=src, target=tgt, trace_id="t1")
        store = StateKVStore(tgt)
        store.upsert("dirty", "extra", {"x": 1}, run_id="dirty")  # 污染目标
        store.close()
        stats = migrate_state_db(source=src, target=tgt, force=True, trace_id="t2")
        assert stats["verified"] is True
        assert stats["kv_rows"] == 2
        store = StateKVStore(tgt)
        assert store.get("dirty", "extra") is None  # 脏数据已清除
        store.close()

    def test_source_locked_degrades(self, tmp_path, monkeypatch):
        """源库被写锁占用（read_only 打开报 already open）→ 降级拒绝并提示。"""
        src = tmp_path / "state.duckdb"
        src.write_bytes(b"placeholder")
        tgt = tmp_path / "state.db"

        real_connect = duckdb.connect

        def fake_connect(path, *args, **kwargs):
            if kwargs.get("read_only"):
                raise duckdb.IOException(
                    'IO Error: Cannot open file "state.duckdb": 另一个程序正在使用此文件，进程无法访问。'
                )
            return real_connect(path, *args, **kwargs)

        monkeypatch.setattr(duckdb, "connect", fake_connect)
        with pytest.raises(RuntimeError, match="写锁占用"):
            migrate_state_db(source=src, target=tgt, trace_id="t1")
        assert not tgt.exists()  # 目标未被创建/破坏

    def test_source_missing_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            migrate_state_db(source=tmp_path / "nope.duckdb", target=tmp_path / "state.db", trace_id="t1")
