"""tests/scheduler/test_tasks.py — FTS 定时任务注册表测试。

HARNESS §测试随重构: 全量覆盖 tasks.py，目标 100% line coverage。
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest

from fts.scheduler.tasks import (
    REGISTRY,
    TaskRegistry,
    TaskSpec,
    get_task,
    list_tasks,
    make_trace_id,
    register_default_tasks,
)


# ─── Fixtures ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个测试前清空全局 REGISTRY（避免测试间状态污染）。"""
    keys = list(REGISTRY._tasks.keys())
    for k in keys:
        REGISTRY.unregister(k)
    assert len(REGISTRY) == 0
    yield
    # 测试后同样清理
    keys = list(REGISTRY._tasks.keys())
    for k in keys:
        REGISTRY.unregister(k)


# 优先导入以确保 register_default_tasks 测试在干净状态运行
@pytest.fixture
def fresh_registry() -> TaskRegistry:
    """返回一个新的空 TaskRegistry（不触全局 REGISTRY）。"""
    return TaskRegistry()


# ─── TaskSpec ───────────────────────────────────────────

DEFAULT_TASKS = {
    "l1_meta_loop": {
        "cron": "0 9 * * *",
        "callable": "fts.factor_engine.meta_loop.MetaLoop.run",
        "desc": "L1 Meta-Loop：每日知识补给 + Bootstrapping + debate_round 分析",
        "prefix": "fts.l1",
    },
    "l2_evolution_loop": {
        "cron": "0 23 * * *",
        "callable": "fts.factor_engine.evolution_loop.EvolutionLoop.run",
        "desc": "L2 Evolution Loop：夜间因子演化（LLM 改逻辑 + optuna 调参）",
        "prefix": "fts.l2",
    },
    "l3_portfolio_loop": {
        "cron": "0 6 * * 1",
        "callable": "fts.factor_engine.portfolio_loop.PortfolioLoop.run",
        "desc": "L3 Portfolio Loop：组合构建 + 正交化 + 衰减检验",
        "prefix": "fts.l3",
    },
    "health_check": {
        "cron": "*/10 * * * *",
        "callable": "fts.factor_engine.monitor.check_all",
        "desc": "健康检查：监控所有循环状态",
        "prefix": "fts.health",
    },
}


def test_taskspec_all_fields():
    """TaskSpec 所有字段均可赋值。"""
    spec = TaskSpec(
        name="test_task",
        cron_expression="0 9 * * *",
        callable_path="some.module.func",
        description="test description",
        enabled=False,
        trace_id_prefix="custom.prefix",
    )
    assert spec.name == "test_task"
    assert spec.cron_expression == "0 9 * * *"
    assert spec.callable_path == "some.module.func"
    assert spec.description == "test description"
    assert spec.enabled is False
    assert spec.trace_id_prefix == "custom.prefix"


def test_taskspec_defaults():
    """TaskSpec 默认值：description="", enabled=True, trace_id_prefix="fts.task"。"""
    spec = TaskSpec(
        name="minimal",
        cron_expression="*/5 * * * *",
        callable_path="mod.fn",
    )
    assert spec.description == ""
    assert spec.enabled is True
    assert spec.trace_id_prefix == "fts.task"


# ─── TaskRegistry ───────────────────────────────────────


class TestTaskRegistry:
    """TaskRegistry 单元测试（使用 fresh_registry 避免污染全局）。"""

    def test_register_normal(self, fresh_registry: TaskRegistry):
        spec = TaskSpec("t1", "* * * * *", "mod.fn")
        fresh_registry.register(spec)
        assert "t1" in fresh_registry
        assert len(fresh_registry) == 1

    def test_register_duplicate_raises(self, fresh_registry: TaskRegistry):
        spec = TaskSpec("t1", "* * * * *", "mod.fn")
        fresh_registry.register(spec)
        with pytest.raises(ValueError, match="task already registered: t1"):
            fresh_registry.register(spec)

    def test_unregister_existing(self, fresh_registry: TaskRegistry):
        spec = TaskSpec("t1", "* * * * *", "mod.fn")
        fresh_registry.register(spec)
        result = fresh_registry.unregister("t1")
        assert result is spec
        assert "t1" not in fresh_registry
        assert len(fresh_registry) == 0

    def test_unregister_nonexistent(self, fresh_registry: TaskRegistry):
        result = fresh_registry.unregister("nonexistent")
        assert result is None

    def test_get_existing(self, fresh_registry: TaskRegistry):
        spec = TaskSpec("t1", "* * * * *", "mod.fn")
        fresh_registry.register(spec)
        assert fresh_registry.get("t1") is spec

    def test_get_nonexistent(self, fresh_registry: TaskRegistry):
        assert fresh_registry.get("nonexistent") is None

    def test_list_all_sorted(self, fresh_registry: TaskRegistry):
        fresh_registry.register(TaskSpec("z_task", "* * * * *", "mod.z"))
        fresh_registry.register(TaskSpec("a_task", "* * * * *", "mod.a"))
        fresh_registry.register(TaskSpec("m_task", "* * * * *", "mod.m"))
        all_tasks = fresh_registry.list_all()
        names = [t.name for t in all_tasks]
        assert names == sorted(names)
        assert names == ["a_task", "m_task", "z_task"]

    def test_list_enabled(self, fresh_registry: TaskRegistry):
        fresh_registry.register(TaskSpec("e1", "* * * * *", "mod.e1", enabled=True))
        fresh_registry.register(TaskSpec("e2", "* * * * *", "mod.e2", enabled=True))
        fresh_registry.register(TaskSpec("d1", "* * * * *", "mod.d1", enabled=False))
        enabled = fresh_registry.list_enabled()
        assert [t.name for t in enabled] == ["e1", "e2"]

    def test_len(self, fresh_registry: TaskRegistry):
        assert len(fresh_registry) == 0
        fresh_registry.register(TaskSpec("t1", "* * * * *", "mod.fn"))
        assert len(fresh_registry) == 1

    def test_contains(self, fresh_registry: TaskRegistry):
        fresh_registry.register(TaskSpec("t1", "* * * * *", "mod.fn"))
        assert "t1" in fresh_registry
        assert "nope" not in fresh_registry


# ─── 全局 REGISTRY ──────────────────────────────────────


def test_registry_is_taskregistry():
    """REGISTRY 是 TaskRegistry 实例。"""
    assert isinstance(REGISTRY, TaskRegistry)


# ─── register_default_tasks ─────────────────────────────


def test_register_default_tasks_registers_four():
    """register_default_tasks 注册 4 个默认任务。"""
    register_default_tasks()
    assert len(REGISTRY) == 4


@pytest.mark.parametrize("name,expected", DEFAULT_TASKS.items())
def test_register_default_tasks_content(name: str, expected: dict):
    """每个默认任务的 cron / callable / description / prefix 正确。"""
    register_default_tasks()
    spec = REGISTRY.get(name)
    assert spec is not None, f"任务 {name} 未注册"
    assert spec.cron_expression == expected["cron"]
    assert spec.callable_path == expected["callable"]
    assert spec.description == expected["desc"]
    assert spec.trace_id_prefix == expected["prefix"]
    assert spec.enabled is True, f"任务 {name} 默认应启用"


def test_register_default_tasks_idempotent():
    """register_default_tasks 幂等：重复调用不抛异常，任务数不变。"""
    register_default_tasks()
    first_len = len(REGISTRY)
    # 第二次调用不应抛 ValueError
    register_default_tasks()
    assert len(REGISTRY) == first_len


# ─── list_tasks ─────────────────────────────────────────


def test_list_tasks_returns_sorted():
    """list_tasks 返回按 name 排序的列表，自动注册默认任务。"""
    tasks = list_tasks()
    assert len(tasks) == 4
    names = [t.name for t in tasks]
    assert names == sorted(names)
    assert names == ["health_check", "l1_meta_loop", "l2_evolution_loop", "l3_portfolio_loop"]


def test_list_tasks_after_manual_register():
    """list_tasks 包含手动注册的任务 + 默认任务。"""
    register_default_tasks()
    REGISTRY.register(TaskSpec("custom_job", "0 12 * * *", "mod.custom"))
    tasks = list_tasks()
    assert len(tasks) == 5
    names = [t.name for t in tasks]
    assert "custom_job" in names


# ─── get_task ───────────────────────────────────────────


def test_get_task_returns_spec():
    """get_task 返回指定任务，自动注册默认任务。"""
    spec = get_task("l1_meta_loop")
    assert spec is not None
    assert spec.name == "l1_meta_loop"
    assert spec.cron_expression == "0 9 * * *"


def test_get_task_nonexistent():
    """get_task 对不存在任务返回 None。"""
    result = get_task("nonexistent_task")
    assert result is None


# ─── make_trace_id ──────────────────────────────────────


def test_make_trace_id_format():
    """make_trace_id 返回格式 <prefix>.<generate_trace_id()>。"""
    register_default_tasks()
    trace_id = make_trace_id("l1_meta_loop")
    # 格式：fts.l1.xxxx_xxxxxxxx_YYYYMMDDTHHMMSS
    assert trace_id.startswith("fts.l1.")
    # 验证 trace_id 中有 generate_trace_id 生成的部分（即包含 "_"）
    parts = trace_id.split(".")
    assert len(parts) == 3
    assert parts[0] == "fts"
    # generate_trace_id 返回格式：prefix_8hex_timestamp
    inner = parts[2]
    assert re.match(r"^l2_[0-9a-f]{8}_\d{8}T\d{6}$", inner)


def test_make_trace_id_unknown_task():
    """make_trace_id 对未注册的任务使用默认前缀 'fts.task'。"""
    # 不调用 register_default_tasks，REGISTRY 为空
    trace_id = make_trace_id("unknown_task")
    assert trace_id.startswith("fts.task.")


def test_make_trace_id_different_calls():
    """make_trace_id 每次调用返回不同 trace_id（含时间戳+随机数）。"""
    register_default_tasks()
    t1 = make_trace_id("l1_meta_loop")
    t2 = make_trace_id("l1_meta_loop")
    # 理论上极小概率相同，但可接受
    assert t1 != t2


@patch("fts.scheduler.tasks.generate_trace_id", return_value="mocked_id_123456_20260718T120000")
def test_make_trace_id_with_mock(mock_gen):
    """使用 mock 验证 make_trace_id 的逻辑组合。"""
    register_default_tasks()
    trace_id = make_trace_id("health_check")
    assert trace_id == "fts.health.mocked_id_123456_20260718T120000"
    mock_gen.assert_called_once()
