"""fts.workflow.stages — 阶段定义测试（11 阶段 + 质检闭环）。"""

from __future__ import annotations

from fts.workflow import STAGES, get_stage, get_stages


def test_stage_count_11_plus_qa() -> None:
    """手册 11 阶段 + 质检闭环 = 12 个节点。"""
    assert len(STAGES) == 12
    assert [s.id for s in STAGES] == [f"s{i}" for i in range(1, 12)] + ["qa"]


def test_stage_indexes_sequential() -> None:
    indexes = [s.index for s in STAGES]
    assert indexes == list(range(1, 13))


def test_dependencies_reference_valid_stages() -> None:
    ids = {s.id for s in STAGES}
    for s in STAGES:
        for dep in s.depends_on:
            assert dep in ids, f"{s.id} 依赖未知阶段 {dep}"


def test_actions_valid() -> None:
    for s in STAGES:
        assert s.actions, f"{s.id} 至少一个动作"
        a_ids = [a.id for a in s.actions]
        assert len(a_ids) == len(set(a_ids))
        for a in s.actions:
            assert a.kind in {"cli", "script", "info"}
            assert a.label
            assert a.timeout > 0
            if a.kind in {"cli", "script"}:
                assert a.cmd, f"{s.id}/{a.id} 缺少命令"


def test_qa_stage_has_script_action() -> None:
    qa = get_stage("qa")
    assert qa is not None
    assert qa.actions[0].kind == "script"
    assert "verify_qa_workflow.py" in qa.actions[0].cmd[0]


def test_get_stages_api_shape() -> None:
    data = get_stages()
    assert isinstance(data, list) and len(data) == 12
    first = data[0]
    for key in ("id", "index", "name", "desc", "depends_on", "actions"):
        assert key in first
    for a in first["actions"]:
        assert set(a) >= {"id", "label", "kind", "timeout", "cmd"}


def test_get_stage_unknown_returns_none() -> None:
    assert get_stage("nope") is None
