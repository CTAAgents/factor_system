"""E.3（S2）：fts/store/state_db.py StateKVStore 测试（SQLite WAL 后端）。

覆盖:
- API 行为与 DuckDB 时代一致（round-trip）
- SQLite 特性：WAL 生效 / 写连接存活期间外部只读不阻塞 / upsert 原子性 /
  seq 单调 / 多线程并发写串行
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from fts.store.state_db import StateKVStore


@pytest.fixture()
def store(tmp_path) -> StateKVStore:
    s = StateKVStore(tmp_path / "state.db")
    yield s
    s.close()


class TestStateKVStore:
    def test_upsert_and_get(self, store: StateKVStore):
        store.upsert("portfolio", "futures_dynamic_pool", {"pool": ["RB0", "CU0"]}, run_id="t1")
        assert store.get("portfolio", "futures_dynamic_pool") == {"pool": ["RB0", "CU0"]}

    def test_get_missing_returns_none(self, store: StateKVStore):
        assert store.get("nope", "nope") is None

    def test_upsert_overwrites_current(self, store: StateKVStore):
        store.upsert("evolution", "state", {"status": "running"}, run_id="t1")
        store.upsert("evolution", "state", {"status": "completed"}, run_id="t2")
        assert store.get("evolution", "state") == {"status": "completed"}

    def test_upsert_appends_history(self, store: StateKVStore):
        store.upsert("portfolio", "combo_history/cmb_a", {"combo_id": "cmb_a"}, run_id="t1")
        store.upsert("portfolio", "combo_history/cmb_a", {"combo_id": "cmb_a", "v": 2}, run_id="t2")
        hist = store.history("portfolio", "combo_history/cmb_a")
        assert len(hist) == 2
        assert hist[0]["value"]["v"] == 2  # 最近在前
        assert hist[0]["run_id"] == "t2"

    def test_get_all_by_namespace(self, store: StateKVStore):
        store.upsert("portfolio", "state", {"a": 1}, run_id="t")
        store.upsert("portfolio", "futures_dynamic_pool", {"p": 2}, run_id="t")
        store.upsert("evolution", "state", {"b": 3}, run_id="t")
        ns = store.get_all("portfolio")
        assert set(ns) == {"state", "futures_dynamic_pool"}
        assert ns["state"] == {"a": 1}

    def test_snapshot_roundtrip(self, store: StateKVStore):
        store.upsert("portfolio", "state", {"status": "frozen"}, run_id="t")
        store.upsert("meta_loop", "state", {"status": "completed"}, run_id="t")
        snap = store.snapshot()
        assert snap["portfolio"]["state"] == {"status": "frozen"}
        assert snap["meta_loop"]["state"] == {"status": "completed"}

    def test_persist_across_reopen(self, tmp_path):
        db = tmp_path / "state.db"
        with StateKVStore(db) as s:
            s.upsert("portfolio", "state", {"run_id": "r1"}, run_id="r1")
        with StateKVStore(db) as s2:
            assert s2.get("portfolio", "state") == {"run_id": "r1"}

    def test_list_value_supported(self, store: StateKVStore):
        store.upsert("portfolio", "live_feedback", [{"a": 1}, {"a": 2}], run_id="t")
        assert store.get("portfolio", "live_feedback") == [{"a": 1}, {"a": 2}]

    def test_history_filter(self, store: StateKVStore):
        store.upsert("a", "k1", {"x": 1}, run_id="t")
        store.upsert("a", "k2", {"y": 1}, run_id="t")
        store.upsert("b", "k1", {"z": 1}, run_id="t")
        assert len(store.history(namespace="a")) == 2
        assert len(store.history(namespace="a", key="k1")) == 1


class TestSQLiteConcurrency:
    def test_wal_mode_enabled(self, store: StateKVStore):
        """WAL 生效：多读单写不互斥的前提。"""
        row = store._conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0] == "wal"

    def test_reader_not_blocked_by_writer(self, store: StateKVStore, tmp_path):
        """写连接存活期间外部只读连接可打开且可读（对照：DuckDB 报 File is already open）。"""
        store.upsert("portfolio", "state", {"status": "running"}, run_id="t")
        # 写连接（store）存活中，独立连接只读
        conn = sqlite3.connect(str(store._db_path))
        try:
            rows = conn.execute("SELECT value FROM state_kv WHERE namespace=? AND key=?", ("portfolio", "state")).fetchall()
            assert len(rows) == 1
        finally:
            conn.close()

    def test_upsert_atomic_rollback(self, store: StateKVStore):
        """history 插入失败 → 事务回滚，state_kv 与 state_history 均无该次残留。"""
        store.upsert("portfolio", "state", {"v": 1}, run_id="ok")

        # SQLite 触发器在 history INSERT 时注入失败（RAISE FAIL → OperationalError）
        store._conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS _test_fail_history_insert
            BEFORE INSERT ON state_history
            BEGIN
                SELECT RAISE(FAIL, 'simulated history insert failure');
            END
            """
        )
        try:
            with pytest.raises(sqlite3.IntegrityError, match="simulated"):
                store.upsert("portfolio", "state", {"v": 2}, run_id="fail")
        finally:
            store._conn.execute("DROP TRIGGER IF EXISTS _test_fail_history_insert")

        # 当前值未被破坏（仍为 v1），history 未追加失败记录
        assert store.get("portfolio", "state") == {"v": 1}
        hist = store.history("portfolio", "state")
        assert [h["run_id"] for h in hist] == ["ok"]

    def test_seq_monotonic(self, store: StateKVStore):
        """连续 upsert 返回 seq 严格递增。"""
        seqs = [store.upsert("a", "k", {"i": i}, run_id="t") for i in range(5)]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)
        hist = store.history(namespace="a", key="k", limit=100)
        assert [h["seq"] for h in reversed(hist)] == seqs

    def test_concurrent_upserts_serialized(self, store: StateKVStore):
        """多线程并发写：进程内锁 + 事务串行，无异常、seq 不重复。"""
        n_threads = 8
        per_thread = 10
        results: list[int] = []
        errors: list[Exception] = []
        barrier = threading.Barrier(n_threads)

        def worker(tid: int) -> None:
            try:
                barrier.wait()
                for i in range(per_thread):
                    results.append(store.upsert("conc", f"k{tid}", {"t": tid, "i": i}, run_id="t"))
            except Exception as e:  # noqa: BLE001 — 收集线程异常供断言
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == n_threads * per_thread
        assert len(set(results)) == len(results)  # seq 全局唯一
        assert max(results) - min(results) == len(results) - 1  # 连续无空洞
