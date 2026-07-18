"""
fts.scheduler.tasks — FTS 定时任务注册表。

任务清单（默认）:
    - l1_meta_loop      : 每日 09:00 触发 L1 Meta-Loop（知识补给）
    - l2_evolution_loop : 每日 23:00 触发 L2 因子演化（夜间演化）
    - l3_portfolio_loop : 每周一 06:00 触发 L3 组合构建
    - health_check      : 每 10 分钟触发健康检查

cron 表达式格式（5 字段）: minute hour day-of-month month day-of-week

HARNESS §trace_id 全链路: 每个 task 启动时生成独立 trace_id。

版本: v0.1.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..factor_engine import generate_trace_id


@dataclass
class TaskSpec:
    """定时任务规格。

    Attributes:
        name: 任务名（全局唯一）
        cron_expression: 5 字段 cron 表达式（minute hour dom month dow）
        callable_path: 可调用对象的完整路径（如 "fts.factor_engine.evolution_loop.EvolutionLoop.run"）
        description: 任务描述
        enabled: 是否启用（默认 True）
        trace_id_prefix: trace_id 前缀（用于日志聚合）
    """
    name: str
    cron_expression: str
    callable_path: str
    description: str = ""
    enabled: bool = True
    trace_id_prefix: str = "fts.task"


class TaskRegistry:
    """定时任务注册表 — 线程安全不保证，初始化阶段使用。"""

    def __init__(self) -> None:
        self._tasks: dict[str, TaskSpec] = {}

    def register(self, spec: TaskSpec) -> None:
        """注册任务（重名抛 ValueError）。"""
        if spec.name in self._tasks:
            raise ValueError(f"task already registered: {spec.name}")
        self._tasks[spec.name] = spec

    def unregister(self, name: str) -> Optional[TaskSpec]:
        """注销任务，返回被移除的 TaskSpec（不存在则 None）。"""
        return self._tasks.pop(name, None)

    def get(self, name: str) -> Optional[TaskSpec]:
        """获取任务规格。"""
        return self._tasks.get(name)

    def list_all(self) -> list[TaskSpec]:
        """列出所有任务（按 name 排序）。"""
        return [self._tasks[k] for k in sorted(self._tasks.keys())]

    def list_enabled(self) -> list[TaskSpec]:
        """列出所有启用的任务。"""
        return [t for t in self.list_all() if t.enabled]

    def __len__(self) -> int:
        return len(self._tasks)

    def __contains__(self, name: str) -> bool:
        return name in self._tasks


# ─── 全局注册表 ───────────────────────────────────────────

REGISTRY = TaskRegistry()


def register_default_tasks() -> None:
    """注册默认任务清单（幂等，重复调用安全）。"""
    defaults = [
        TaskSpec(
            name="l1_meta_loop",
            cron_expression="0 9 * * *",          # 每日 09:00
            callable_path="fts.factor_engine.meta_loop.MetaLoop.run",
            description="L1 Meta-Loop：每日知识补给 + Bootstrapping + debate_round 分析",
            trace_id_prefix="fts.l1",
        ),
        TaskSpec(
            name="l2_evolution_loop",
            cron_expression="0 23 * * *",         # 每日 23:00
            callable_path="fts.factor_engine.evolution_loop.EvolutionLoop.run",
            description="L2 Evolution Loop：夜间因子演化（LLM 改逻辑 + optuna 调参）",
            trace_id_prefix="fts.l2",
        ),
        TaskSpec(
            name="l3_portfolio_loop",
            cron_expression="0 6 * * 1",          # 每周一 06:00
            callable_path="fts.factor_engine.portfolio_loop.PortfolioLoop.run",
            description="L3 Portfolio Loop：组合构建 + 正交化 + 衰减检验",
            trace_id_prefix="fts.l3",
        ),
        TaskSpec(
            name="health_check",
            cron_expression="*/10 * * * *",       # 每 10 分钟
            callable_path="fts.factor_engine.monitor.check_all",
            description="健康检查：监控所有循环状态",
            trace_id_prefix="fts.health",
        ),
    ]
    for spec in defaults:
        if spec.name not in REGISTRY:
            REGISTRY.register(spec)


def list_tasks() -> list[TaskSpec]:
    """列出所有任务（自动注册默认任务）。"""
    if len(REGISTRY) == 0:
        register_default_tasks()
    return REGISTRY.list_all()


def get_task(name: str) -> Optional[TaskSpec]:
    """获取单个任务（自动注册默认任务）。"""
    if len(REGISTRY) == 0:
        register_default_tasks()
    return REGISTRY.get(name)


def make_trace_id(task_name: str) -> str:
    """为任务执行生成带前缀的 trace_id。"""
    spec = get_task(task_name)
    prefix = spec.trace_id_prefix if spec else "fts.task"
    return f"{prefix}.{generate_trace_id()}"
