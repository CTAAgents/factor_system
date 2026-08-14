"""fts.workflow.store — SQLite 状态持久化测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fts.workflow.store import WorkflowStore


@pytest.fixture
def store(tmp_path: Path) -> WorkflowStore:
    return WorkflowStore(db_path=tmp_path / "wf.db")


def test_create_and_get_run(store: WorkflowStore) -> None:
    run_id = store.create_run("s1")
    run = store.get_run(run_id)
    assert run is not None
    assert run["run_id"] == run_id
    assert run["status"] == "running"
    assert run["started_stage"] == "s1"


def test_update_run(store: WorkflowStore) -> None:
    run_id = store.create_run("s1")
    store.update_run(run_id, status="success", current_stage="s4")
    run = store.get_run(run_id)
    assert run["status"] == "success"
    assert run["current_stage"] == "s4"


def test_list_runs_ordered(store: WorkflowStore) -> None:
    r1 = store.create_run("s1")
    r2 = store.create_run("s1")
    runs = store.list_runs()
    assert [r["run_id"] for r in runs[:2]] == [r2, r1]


def test_stage_run_lifecycle(store: WorkflowStore) -> None:
    run_id = store.create_run("s1")
    sid = store.create_stage_run(run_id, "s1", "a1")
    store.update_stage_run(sid, status="success", exit_code=0)
    store.append_log(sid, "hello")
    store.append_log(sid, " world")
    rec = store.get_stage_run(sid)
    assert rec is not None
    assert rec["status"] == "success"
    assert rec["exit_code"] == 0
    assert rec["log"] == "hello world"


def test_stage_run_output_json_parsed(store: WorkflowStore) -> None:
    run_id = store.create_run("s1")
    sid = store.create_stage_run(run_id, "s1", "a1")
    store.update_stage_run(sid, status="success", output='{"n": 3}')
    rec = store.get_stage_run(sid)
    assert rec["output"] == {"n": 3}


def test_stage_run_output_plain_text_fallback(store: WorkflowStore) -> None:
    run_id = store.create_run("s1")
    sid = store.create_stage_run(run_id, "s1", "a1")
    store.update_stage_run(sid, status="success", output="not json")
    rec = store.get_stage_run(sid)
    assert rec["output"] == "not json"


def test_get_stage_runs_grouped(store: WorkflowStore) -> None:
    run_id = store.create_run("s1")
    store.create_stage_run(run_id, "s1", "a1")
    store.create_stage_run(run_id, "s2", "a1")
    recs = store.get_stage_runs(run_id)
    assert [r["stage_id"] for r in recs] == ["s1", "s2"]
