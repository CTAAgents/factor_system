"""
fts.scheduler.tasks — FTS 定时任务注册表。

任务清单（默认）:
    - l1_meta_loop          : 工作日每日 07:59 触发 L1 Meta-Loop（知识补给 + 种子注入，对齐 TRAE Schedule 期货 L1）
    - l2_evolution_loop     : 工作日每日 00:00 触发 L2 因子演化（夜间演化，对齐 TRAE Schedule 期货 L2）
    - l3_portfolio_loop     : 每周五 19:00 触发 L3 组合权重重算（期货，与信号管道解绑，GAP-072，对齐 TRAE Schedule）
    - l3_portfolio_loop_stock : 每周五 19:30 触发 L3 组合权重重算（股票，GAP-072，对齐 TRAE Schedule）
    - futures_signal_pipeline : 工作日每日 20:00 期货信号管道（独立运行，权重周五重算其余日冻结，GAP-072）
    - daily_signal_pipeline   : 工作日每日 08:45 股票/ETF 信号管道（独立运行，GAP-072）
    - health_check          : 每 10 分钟触发健康检查

cron 表达式格式（5 字段）: minute hour day-of-month month day-of-week

HARNESS §trace_id 全链路: 每个 task 启动时生成独立 trace_id。

版本: v0.3.0
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
            cron_expression="59 7 * * 1-5",  # 工作日每日 07:59（对齐 TRAE Schedule 期货 L1 96848d52）
            callable_path="fts.scheduler.jobs.l1_meta_loop_job",
            description="L1 Meta-Loop：每日知识补给 + Bootstrapping + 种子注入",
            trace_id_prefix="fts.l1",
        ),
        TaskSpec(
            name="l2_evolution_loop",
            cron_expression="0 0 * * 1-5",  # 工作日每日 00:00（对齐 TRAE Schedule 期货 L2 e4d82f5b）
            callable_path="fts.scheduler.jobs.l2_evolution_loop_job",
            description="L2 Evolution Loop：夜间因子演化（LLM 改逻辑 + optuna 调参 + 横截面评估）",
            trace_id_prefix="fts.l2",
        ),
        TaskSpec(
            name="l3_portfolio_loop",
            cron_expression="0 19 * * 5",  # 每周五 19:00（对齐 TRAE Schedule 期货 L3 4ad19ae6，GAP-072）
            callable_path="fts.scheduler.jobs.l3_portfolio_loop_job",
            description="L3 Portfolio Loop（期货路径：futures_elite + market=futures）：每周五收盘后重算组合权重（Elastic Net 信号合成 + Verifier 校验），与期货信号管道解绑",
            trace_id_prefix="fts.l3",
        ),
        TaskSpec(
            name="l3_portfolio_loop_stock",
            cron_expression="30 19 * * 5",  # 每周五 19:30（对齐 TRAE Schedule 股票 L3 32745e2b，GAP-072）
            callable_path="fts.scheduler.jobs.l3_portfolio_loop_stock_job",
            description="L3 Portfolio Loop（股票路径：elite_dir + market=stock）：每周五重算股票组合权重（GAP-063 组合质检前置接入），与股票信号管道解绑",
            trace_id_prefix="fts.l3.stock",
        ),
        TaskSpec(
            name="futures_signal_pipeline",
            cron_expression="0 20 * * 1-5",  # 工作日每日 20:00（GAP-072 解绑后独立运行）
            callable_path="fts.scheduler.jobs.futures_signal_pipeline_job",
            description="期货信号管道（每日独立运行）：Ridge 权重周五重算并存快照，其余日冻结复用快照仅刷新因子值 → reports/futures/{date}/futures_signals_*.md",
            trace_id_prefix="fts.signal",
        ),
        TaskSpec(
            name="daily_signal_pipeline",
            cron_expression="45 8 * * 1-5",  # 工作日每日 08:45（开盘前，GAP-072 解绑后独立运行）
            callable_path="fts.scheduler.jobs.daily_signal_pipeline_job",
            description="股票/ETF 信号管道（每日独立运行）：Ridge 权重周五重算并存快照，其余日冻结复用快照仅刷新因子值 → reports/stock/{date}/daily_signals_*.md",
            trace_id_prefix="fts.signal.stock",
        ),
        TaskSpec(
            name="sync_futures_data",
            cron_expression="30 17 * * 1-5",  # 工作日 17:30
            callable_path="fts.scheduler.jobs.sync_futures_data_job",
            description="Phase 14.5 期货多源数据同步（DUCKDB 缓存 + TQ 源 → DuckDB）",
            trace_id_prefix="fts.sync",
        ),
        TaskSpec(
            name="sync_stock_data",
            cron_expression="0 17 * * 1-5",  # 工作日 17:00
            callable_path="fts.scheduler.jobs.sync_stock_data_job",
            description="股票/ETF 日 K 线缓存同步（腾讯 API → DuckDB stock_kline_cache，供次日信号管道/演化）",
            trace_id_prefix="fts.sync.stock",
        ),
        TaskSpec(
            name="health_check",
            cron_expression="*/10 * * * *",  # 每 10 分钟
            callable_path="fts.scheduler.jobs.health_check_job",
            description="健康检查：监控所有循环状态",
            trace_id_prefix="fts.health",
        ),
        TaskSpec(
            name="monthly_decay_eval",
            cron_expression="0 4 1 * *",  # 每月 1 日 04:00（对齐 TRAE Schedule 月度衰减 a6f69113）
            callable_path="fts.scheduler.jobs.monthly_decay_eval_job",
            description="月度因子衰减评估（A.2）：精英池增量评估 + 状态机 + 自动淘汰",
            trace_id_prefix="fts.decay",
        ),
        TaskSpec(
            name="data_quality_eval",
            cron_expression="*/5 * * * *",  # 每 5 分钟
            callable_path="fts.scheduler.jobs.data_quality_eval_job",
            description="数据质量周期评估（B.1）：质量快照 + 告警检查",
            trace_id_prefix="fts.dq",
        ),
        TaskSpec(
            name="data_level_monitor",
            cron_expression="0 4 * * *",  # 每日 04:00
            callable_path="fts.scheduler.jobs.data_level_monitor_job",
            description="数据级质量监控（GAP-F06）：缺失率/异常值/多源分歧检查",
            trace_id_prefix="fts.dlm",
        ),
        TaskSpec(
            name="logic_monitor",
            cron_expression="0 22 * * *",  # 每日 22:00
            callable_path="fts.scheduler.jobs.logic_monitor_job",
            description="逻辑监控（B.2）：因子行为漂移 + 极端预测 + 换月日异常检测",
            trace_id_prefix="fts.logic",
        ),
        TaskSpec(
            name="factor_inspector",
            cron_expression="0 3 * * *",  # 每日 03:00
            callable_path="fts.scheduler.jobs.factor_inspector_job",
            description="因子巡检与自动降级（B.2）：扫描精英因子，检测退化并降级",
            trace_id_prefix="fts.inspector",
        ),
        TaskSpec(
            name="sync_liquidity_pool",
            cron_expression="0 8 * * 6",  # 每周六 08:00
            callable_path="fts.scheduler.jobs.sync_liquidity_pool_job",
            description="数据驱动动态池刷新（GAP-054）：TqSdk 流动性快照 → 渐进式替换 → 落盘动态池缓存",
            trace_id_prefix="fts.lpool",
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
