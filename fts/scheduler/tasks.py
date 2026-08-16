"""
fts.scheduler.tasks — FTS 定时任务注册表。

任务清单（默认）:
    - l1_meta_loop          : 工作日每日 07:59 触发 L1 Meta-Loop（知识补给 + 种子注入，对齐 TRAE Schedule 期货 L1）
    - l2_evolution_loop     : 工作日每日 00:00 触发 L2 因子演化（夜间演化，对齐 TRAE Schedule 期货 L2）
    - l3_portfolio_loop     : 工作日每日 06:00 触发 L3 组合权重重算（期货，equal_weight 等权漂移小每日重算稳定，与信号管道解绑，GAP-072，对齐 TRAE Schedule；2026-08-14 起调整为开盘前 06:00）
    - futures_signal_pipeline : 工作日每日 20:00 期货信号管道（独立运行，权重周五重算其余日冻结，GAP-072）
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
            cron_expression="0 0 * * *",  # 每日 00:00（45 计划调度基线：每日知识补给 + 种子注入，供 02:00 种子评估消费）
            callable_path="fts.scheduler.jobs.l1_meta_loop_job",
            description="L1 Meta-Loop：每日知识补给 + Bootstrapping + 种子注入",
            trace_id_prefix="fts.l1",
        ),
        TaskSpec(
            name="l2_evolution_weekday",
            cron_expression="0 4 * * 1-5",  # 工作日 04:00（45 计划调度基线：种子评估 02:00 后小预算演化 ≈10）
            callable_path="fts.scheduler.jobs.l2_evolution_weekday_job",
            description="L2 Evolution Loop（工作日 04:00 小预算 max_generation≈10，45 计划调度基线）：先种子后演化",
            trace_id_prefix="fts.l2",
        ),
        TaskSpec(
            name="l2_evolution_weekend",
            cron_expression="0 4 * * 6",  # 周六 04:00（45 计划调度基线：周末大预算演化 ≈50）
            callable_path="fts.scheduler.jobs.l2_evolution_weekend_job",
            description="L2 Evolution Loop（周六 04:00 大预算 max_generation≈50，45 计划调度基线）：周末集中大规模演化",
            trace_id_prefix="fts.l2",
        ),
        TaskSpec(
            name="l2_seed_promotion",
            cron_expression="0 2 * * *",  # 每日 02:00（45 计划候选①：L1 00:00 注入后 2h 消费，先种子后演化）
            callable_path="fts.scheduler.jobs.l2_seed_promotion_job",
            description="L2 种子评估晋升（45 计划候选①）：种子相关性预检 + 评估晋升入 elite 池，供当日 04:00 演化消费为父因子（不重置演化状态计数器）",
            trace_id_prefix="fts.l2_seed",
        ),
        TaskSpec(
            name="l2_batch_mining",
            cron_expression="0 6 * * 0",  # 周日 06:00（45 计划候选②：周末集中，CPU 密集错峰）
            callable_path="fts.scheduler.jobs.l2_batch_mining_job",
            description="L2 批量挖掘（45 计划候选②）：BatchMiner 批量漏斗（同父多后代→并行粗筛→准入链），熔断隔离不污染演化状态",
            trace_id_prefix="fts.l2_batch",
        ),
        TaskSpec(
            name="l3_portfolio_loop",
            cron_expression="0 6 * * 1-5",  # 工作日每日 06:00（对齐 TRAE Schedule 期货 L3 4ad19ae6；equal_weight 等权漂移小，每日重算稳定，2026-08-13；2026-08-14 调整为开盘前 06:00）
            callable_path="fts.scheduler.jobs.l3_portfolio_loop_job",
            description="L3 Portfolio Loop（期货路径：futures_elite + market=futures）：工作日每日 06:00 开盘前重算组合权重（基于截至昨收数据；equal_weight 信号合成 + Verifier 校验，v2.103.0+23 默认；--force-recompute 保证每日全量重算），与期货信号管道解绑",
            trace_id_prefix="fts.l3",
        ),
        TaskSpec(
            name="futures_signal_pipeline",
            cron_expression="0 20 * * 1-5",  # 工作日每日 20:00（GAP-072 解绑后独立运行）
            callable_path="fts.scheduler.jobs.futures_signal_pipeline_job",
            description="期货信号管道（每日独立运行）：Ridge 权重周五重算并存快照，其余日冻结复用快照仅刷新因子值 → reports/futures/{date}/futures_signals_*.md",
            trace_id_prefix="fts.signal",
        ),
        TaskSpec(
            name="sync_futures_data",
            cron_expression="30 17 * * 1-5",  # 工作日 17:30
            callable_path="fts.scheduler.jobs.sync_futures_data_job",
            description="Phase 14.5 期货多源数据同步（DUCKDB 缓存 + TQ 源 → DuckDB）",
            trace_id_prefix="fts.sync",
        ),
        TaskSpec(
            name="health_check",
            cron_expression="*/10 * * * *",  # 每 10 分钟
            callable_path="fts.scheduler.jobs.health_check_job",
            description="健康检查：监控所有循环状态",
            trace_id_prefix="fts.health",
        ),
        TaskSpec(
            name="l2_review",
            cron_expression="0 10 * * 0",  # 每周日 10:00（45 计划候选③：月度衰减周度化，替代 monthly_decay_eval 注册）
            callable_path="fts.scheduler.jobs.l2_review_job",
            description="L2 周度评审（45 计划候选③）：Step A 新标准准入重审（audit/robustness/评分卡复检 active elite，不合格降级观察或淘汰，FTS_MONTHLY_REAUDIT_ENABLED=0 关闭）+ Step B 因子衰减评估（A.2 增量评估 + 状态机 + 自动淘汰）",
            trace_id_prefix="fts.l2_review",
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
        TaskSpec(
            name="mhf_signal",
            cron_expression="*/30 * * * *",  # 每 30 分钟（对齐 30m bar 收盘；最新 bar 无更新时幂等）
            callable_path="fts.scheduler.jobs.mhf_signal_job",
            description="MHF 中高频信号（plans/33 Phase 4）：30m 反转混合信号 → SignalBridge 发布（JSON）",
            trace_id_prefix="fts.mhf",
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
