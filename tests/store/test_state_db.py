"""plans/29 P2：fts/store/state_db.py StateKVStore 测试。"""

from __future__ import annotations

import pytest

from fts.store.state_db import StateKVStore


@pytest.fixture()
def store(tmp_path) -> StateKVStore:
    s = StateKVStore(tmp_path / "state.duckdb")
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
        db = tmp_path / "state.duckdb"
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
