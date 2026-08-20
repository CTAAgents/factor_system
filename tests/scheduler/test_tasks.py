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
        "cron": "0 0 * * *",
        "callable": "fts.scheduler.jobs.l1_meta_loop_job",
        "desc": "L1 Meta-Loop：每日知识补给 + Bootstrapping + 种子注入",
        "prefix": "fts.l1",
    },
    "l2_evolution_weekday": {
        "cron": "0 3 * * 1-5",
        "callable": "fts.scheduler.jobs.l2_evolution_weekday_job",
        "desc": "L2 Evolution Loop（工作日 03:00 小预算 max_generation≈10，45 计划调度基线）：先种子后演化",
        "prefix": "fts.l2",
    },
    "l2_evolution_weekend": {
        "cron": "0 3 * * 6",
        "callable": "fts.scheduler.jobs.l2_evolution_weekend_job",
        "desc": "L2 Evolution Loop（周六 03:00 大预算 max_generation≈50，45 计划调度基线）：周末集中大规模演化",
        "prefix": "fts.l2",
    },
    "l2_seed_promotion": {
        "cron": "0 2 * * *",
        "callable": "fts.scheduler.jobs.l2_seed_promotion_job",
        "desc": "L2 种子评估晋升（45 计划候选①）：种子相关性预检 + 评估晋升入 elite 池，供当日 04:00 演化消费为父因子（不重置演化状态计数器）",
        "prefix": "fts.l2_seed",
    },
    "l2_batch_mining": {
        "cron": "0 6 * * 0",
        "callable": "fts.scheduler.jobs.l2_batch_mining_job",
        "desc": "L2 批量挖掘（45 计划候选②）：BatchMiner 批量漏斗（同父多后代→并行粗筛→准入链），熔断隔离不污染演化状态",
        "prefix": "fts.l2_batch",
    },
    "l3_portfolio_loop": {
        "cron": "0 6 * * 1-5",
        "callable": "fts.scheduler.jobs.l3_portfolio_loop_job",
        "desc": "L3 Portfolio Loop（期货路径：futures_elite + market=futures）：工作日每日 06:00 开盘前重算组合权重（基于截至昨收数据；equal_weight 信号合成 + Verifier 校验，v2.103.0+23 默认；--force-recompute 保证每日全量重算），与期货信号管道解绑",
        "prefix": "fts.l3",
    },
    "futures_signal_pipeline": {
        "cron": "0 20 * * 1-5",
        "callable": "fts.scheduler.jobs.futures_signal_pipeline_job",
        "desc": "期货信号管道（每日独立运行）：Ridge 权重周五重算并存快照，其余日冻结复用快照仅刷新因子值 → reports/futures/{date}/futures_signals_*.md",
        "prefix": "fts.signal",
    },
    "sync_futures_data": {
        "cron": "30 17 * * 1-5",
        "callable": "fts.scheduler.jobs.sync_futures_data_job",
        "desc": "Phase 14.5 期货多源数据同步（DUCKDB 缓存 + TQ 源 → DuckDB）",
        "prefix": "fts.sync",
    },
    "health_check": {
        "cron": "*/10 * * * *",
        "callable": "fts.scheduler.jobs.health_check_job",
        "desc": "健康检查：监控所有循环状态",
        "prefix": "fts.health",
    },
    "l2_review": {
        "cron": "0 10 * * 0",
        "callable": "fts.scheduler.jobs.l2_review_job",
        "desc": "L2 周度评审（45 计划候选③）：Step A 新标准准入重审（audit/robustness/评分卡复检 active elite，不合格降级观察或淘汰，FTS_MONTHLY_REAUDIT_ENABLED=0 关闭）+ Step B 因子衰减评估（A.2 增量评估 + 状态机 + 自动淘汰）；v3.0.0 起由每日 04:00 统一任务周日重量级分支调用（TRAE Schedule 3f5d5da3）",
        "prefix": "fts.l2_review",
    },
    "data_quality_eval": {
        "cron": "*/5 * * * *",
        "callable": "fts.scheduler.jobs.data_quality_eval_job",
        "desc": "数据质量周期评估（B.1）：质量快照 + 告警检查",
        "prefix": "fts.dq",
    },
    "data_level_monitor": {
        "cron": "0 5 * * *",
        "callable": "fts.scheduler.jobs.data_level_monitor_job",
        "desc": "数据级质量监控（GAP-F06）：缺失率/异常值/多源分歧检查",
        "prefix": "fts.dlm",
    },
    "logic_monitor": {
        "cron": "30 4 * * *",
        "callable": "fts.scheduler.jobs.logic_monitor_job",
        "desc": "逻辑监控（B.2）：因子行为漂移 + 极端预测 + 换月日异常检测",
        "prefix": "fts.logic",
    },
    "factor_inspector": {
        "cron": "0 4 * * *",
        "callable": "fts.scheduler.jobs.factor_inspector_job",
        "desc": "因子巡检与自动降级（B.2）：扫描精英因子，检测退化并降级",
        "prefix": "fts.inspector",
    },
    "sync_liquidity_pool": {
        "cron": "0 8 * * 6",
        "callable": "fts.scheduler.jobs.sync_liquidity_pool_job",
        "desc": "数据驱动动态池刷新（GAP-054）：TqSdk 流动性快照 → 渐进式替换 → 落盘动态池缓存",
        "prefix": "fts.lpool",
    },
    "l2_subchain_quality": {
        "cron": "0 9 * * 0",
        "callable": "fts.scheduler.jobs.l2_subchain_quality_job",
        "desc": "批量子链质量评估（2026-08-19 沉淀标准工作流）：全部 active 因子逐品种 IC → 子链画像 → 落库 subchain_factor_quality 质量矩阵，供退化检测/子链调制消费；无有效链因子标记 pending_validation 不自动降级",
        "prefix": "fts.subchain_eval",
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


def test_register_default_tasks_registers_five():
    """register_default_tasks 注册默认任务。"""
    register_default_tasks()
    assert len(REGISTRY) == 18


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
    # v2.104.0+98：内部调度停用，以 TRAE Schedule 为唯一调度源
    assert spec.enabled is False, f"任务 {name} 默认应停用（内部调度 disabled）"


def test_internal_scheduler_env_switch(monkeypatch):
    """FTS_INTERNAL_SCHEDULER_ENABLED 一键开关解析（默认停用 / 置 1 启用）。"""
    from fts.scheduler.tasks import _load_internal_scheduler_enabled

    monkeypatch.delenv("FTS_INTERNAL_SCHEDULER_ENABLED", raising=False)
    assert _load_internal_scheduler_enabled() is False  # 默认停用
    monkeypatch.setenv("FTS_INTERNAL_SCHEDULER_ENABLED", "1")
    assert _load_internal_scheduler_enabled() is True  # 一键启用
    monkeypatch.setenv("FTS_INTERNAL_SCHEDULER_ENABLED", "0")
    assert _load_internal_scheduler_enabled() is False  # 显式停用
    monkeypatch.setenv("FTS_INTERNAL_SCHEDULER_ENABLED", "true")
    assert _load_internal_scheduler_enabled() is False  # 仅字符串 "1" 启用


def test_register_default_tasks_idempotent():
    """register_default_tasks 幂等：重复调用不抛异常，任务数不变。"""
    register_default_tasks()
    first_len = len(REGISTRY)
    # 第二次调用不应抛 ValueError
    register_default_tasks()
    assert len(REGISTRY) == first_len


def test_default_task_callables_importable():
    """每个默认任务的 callable_path 必须真实可导入（防止注册了不存在的函数）。

    回归用例：v2.80.0 曾注册 sync_liquidity_pool 任务指向 jobs.py 缺失的
    sync_liquidity_pool_job，导致内部调度器与自动化任务运行时 ImportError。
    """
    register_default_tasks()
    import importlib

    for name in list(DEFAULT_TASKS):
        spec = REGISTRY.get(name)
        assert spec is not None, f"任务 {name} 未注册"
        module_name, _, attr = spec.callable_path.rpartition(".")
        module = importlib.import_module(module_name)
        assert hasattr(module, attr), f"任务 {name} 的 callable 不存在: {spec.callable_path}"


# ─── list_tasks ─────────────────────────────────────────


def test_list_tasks_returns_sorted():
    """list_tasks 返回按 name 排序的列表，自动注册默认任务。"""
    tasks = list_tasks()
    assert len(tasks) == 18
    names = [t.name for t in tasks]
    assert names == [
        "data_level_monitor",
        "data_quality_eval",
        "factor_inspector",
        "futures_signal_pipeline",
        "health_check",
        "import_external_factors",
        "l1_meta_loop",
        "l2_batch_mining",
        "l2_evolution_weekday",
        "l2_evolution_weekend",
        "l2_review",
        "l2_seed_promotion",
        "l2_subchain_quality",
        "l3_portfolio_loop",
        "logic_monitor",
        "mhf_signal",
        "sync_futures_data",
        "sync_liquidity_pool",
    ]


def test_list_tasks_after_manual_register():
    """list_tasks 包含手动注册的任务 + 默认任务。"""
    register_default_tasks()
    REGISTRY.register(TaskSpec("custom_job", "0 12 * * *", "mod.custom"))
    tasks = list_tasks()
    assert len(tasks) == 19
    names = [t.name for t in tasks]
    assert "custom_job" in names


# ─── get_task ───────────────────────────────────────────


def test_get_task_returns_spec():
    """get_task 返回指定任务，自动注册默认任务。"""
    spec = get_task("l1_meta_loop")
    assert spec is not None
    assert spec.name == "l1_meta_loop"
    assert spec.cron_expression == "0 0 * * *"


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


@patch(
    "fts.scheduler.tasks.generate_trace_id",
    return_value="mocked_id_123456_20260718T120000",
)
def test_make_trace_id_with_mock(mock_gen):
    """使用 mock 验证 make_trace_id 的逻辑组合。"""
    register_default_tasks()
    trace_id = make_trace_id("health_check")
    assert trace_id == "fts.health.mocked_id_123456_20260718T120000"
    mock_gen.assert_called_once()
