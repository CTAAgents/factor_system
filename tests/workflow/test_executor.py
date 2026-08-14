"""fts.workflow.executor — 执行器测试（占位符解析/argv/状态同步/端到端停止）。"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from fts.workflow.executor import REPORT_ROOT, WorkflowExecutor, _extract_json
from fts.workflow.stages import StageAction
from fts.workflow.store import WorkflowStore


@pytest.fixture
def store(tmp_path: Path) -> WorkflowStore:
    return WorkflowStore(db_path=tmp_path / "wf.db")


@pytest.fixture
def executor(store: WorkflowStore) -> WorkflowExecutor:
    return WorkflowExecutor(store)


# ─── _extract_json ───────────────────────────────────────


def test_extract_json_whole() -> None:
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_line_in_output() -> None:
    text = 'info line\n{"n": 2}\n'
    assert _extract_json(text) == {"n": 2}


def test_extract_json_garbage() -> None:
    assert _extract_json("no json here") is None


def test_extract_json_empty() -> None:
    assert _extract_json("") is None


# ─── 占位符解析 / argv ──────────────────────────────────


def test_resolve_report_dir(executor: WorkflowExecutor, tmp_path: Path) -> None:
    action = StageAction("a", "t", ["backtest", "run", "--output", "{report_dir}"])
    argv = executor.resolve_cmd(action, "wf_test_1")
    assert argv[3] == str(REPORT_ROOT / "wf_test_1")


def test_resolve_factor_id_placeholder(executor: WorkflowExecutor) -> None:
    with patch.object(executor, "_latest_factor_id", return_value="fut_test_1"):
        action = StageAction("a", "t", ["backtest", "run", "--factor-id", "{factor_id}"])
        argv = executor.resolve_cmd(action, "wf_x")
        assert argv[-1] == "fut_test_1"


def test_resolve_factor_id_missing_raises(executor: WorkflowExecutor) -> None:
    with patch.object(executor, "_latest_factor_id", return_value=""):
        action = StageAction("a", "t", ["backtest", "run", "--factor-id", "{factor_id}"])
        with pytest.raises(ValueError):
            executor.resolve_cmd(action, "wf_x")


def test_build_argv_cli(executor: WorkflowExecutor) -> None:
    action = StageAction("a", "t", ["data", "status"])
    argv = executor.build_argv(action, "wf_x")
    assert argv == [sys.executable, "-m", "fts.cli", "data", "status"]


def test_build_argv_script(executor: WorkflowExecutor) -> None:
    action = StageAction("a", "t", ["scripts/x.py"], kind="script")
    argv = executor.build_argv(action, "wf_x")
    assert argv == [sys.executable, "scripts/x.py"]


# ─── 单动作真实执行 ──────────────────────────────────────


def test_execute_success(executor: WorkflowExecutor) -> None:
    run_id = executor._store.create_run("s1")
    sid = executor._store.create_stage_run(run_id, "s1", "a1")
    action = StageAction("a1", "t", ["-c", "print('hello workflow')"], kind="script")
    executor._execute(sid, run_id, "s1", action)
    rec = executor._store.get_stage_run(sid)
    assert rec["status"] == "success"
    assert rec["exit_code"] == 0
    assert "hello workflow" in rec["log"]
    assert executor._store.get_run(run_id)["status"] == "success"


def test_execute_failure_syncs_run(executor: WorkflowExecutor) -> None:
    run_id = executor._store.create_run("s1")
    sid = executor._store.create_stage_run(run_id, "s1", "a1")
    action = StageAction("a1", "t", ["-c", "import sys; sys.exit(3)"], kind="script")
    executor._execute(sid, run_id, "s1", action)
    rec = executor._store.get_stage_run(sid)
    assert rec["status"] == "failed"
    assert rec["exit_code"] == 3
    assert executor._store.get_run(run_id)["status"] == "failed"


def test_sync_run_status_uses_latest_per_stage(executor: WorkflowExecutor) -> None:
    """同阶段重复执行时取最新一条推导 run 状态（旧失败记录不主导）。"""
    run_id = executor._store.create_run("s1")
    sid_fail = executor._store.create_stage_run(run_id, "s1", "a1")
    executor._store.update_stage_run(sid_fail, status="failed", exit_code=1)
    sid_ok = executor._store.create_stage_run(run_id, "s1", "a1")
    executor._store.update_stage_run(sid_ok, status="success", exit_code=0)
    executor._sync_run_status(run_id)
    assert executor._store.get_run(run_id)["status"] == "success"


def test_sync_run_status_latest_failed_dominates(executor: WorkflowExecutor) -> None:
    """最新一条为 failed 时 run 仍判 failed（旧 success 不覆盖）。"""
    run_id = executor._store.create_run("s1")
    sid_ok = executor._store.create_stage_run(run_id, "s1", "a1")
    executor._store.update_stage_run(sid_ok, status="success", exit_code=0)
    sid_fail = executor._store.create_stage_run(run_id, "s1", "a1")
    executor._store.update_stage_run(sid_fail, status="failed", exit_code=1)
    executor._sync_run_status(run_id)
    assert executor._store.get_run(run_id)["status"] == "failed"


def test_run_stage_unknown_action(executor: WorkflowExecutor) -> None:
    run_id = executor._store.create_run("s1")
    res = executor.run_stage(run_id, "s1", "nope")
    assert res["ok"] is False


# ─── 端到端：失败停止 ────────────────────────────────────


def _wait_run(store: WorkflowStore, run_id: str, timeout: float = 10.0) -> dict:
    """等待 run 到达终态并稳定（避免轮询恰在阶段间 success 窗口提前返回）。"""
    deadline = time.time() + timeout
    stable_since: float | None = None
    while time.time() < deadline:
        run = store.get_run(run_id)
        if run and run["status"] != "running":
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= 0.2:
                return run
        else:
            stable_since = None
        time.sleep(0.1)
    raise TimeoutError(f"run {run_id} 未在 {timeout}s 内结束")


def test_run_all_stops_on_failure(executor: WorkflowExecutor, monkeypatch) -> None:
    run_id = executor._store.create_run("s1")

    def fake_execute(self, stage_run_id: int, rid: str, stage_id: str, _action) -> None:
        st = self._store
        if stage_id == "s2":
            st.update_stage_run(stage_run_id, status="failed", exit_code=1)
        else:
            st.update_stage_run(stage_run_id, status="success", exit_code=0)
        self._sync_run_status(rid)

    monkeypatch.setattr(WorkflowExecutor, "_execute", fake_execute)
    executor.run_all(run_id, start_stage="s1")
    run = _wait_run(executor._store, run_id)
    assert run["status"] == "failed"
    recs = executor._store.get_stage_runs(run_id)
    assert [r["stage_id"] for r in recs] == ["s1", "s2"]  # 停在 s2


def test_run_all_completes_success(executor: WorkflowExecutor, monkeypatch) -> None:
    run_id = executor._store.create_run("s1")

    def fake_execute(self, stage_run_id: int, rid: str, _stage_id, _action) -> None:
        self._store.update_stage_run(stage_run_id, status="success", exit_code=0)

    monkeypatch.setattr(WorkflowExecutor, "_execute", fake_execute)
    executor.run_all(run_id, start_stage="s1")
    run = _wait_run(executor._store, run_id)
    assert run["status"] == "success"
    from fts.workflow.stages import STAGES

    ran_ids = {r["stage_id"] for r in executor._store.get_stage_runs(run_id)}
    assert ran_ids == {s.id for s in STAGES}
