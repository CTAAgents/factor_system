"""
fts.scheduler — FTS 调度层。

定时任务注册 + APScheduler 调度器引擎。
支持:
    - 注册 task（cron 风格触发器 + 可调用）
    - 列出所有 task
    - SchedulerEngine: 将任务接入 APScheduler 定时执行

版本: v0.1.0
"""

from .tasks import (
    TaskSpec,
    TaskRegistry,
    REGISTRY,
    register_default_tasks,
    list_tasks,
    get_task,
    make_trace_id,
)
from .engine import (
    SchedulerEngine,
    run_scheduler,
)

__version__ = "0.1.0"
__all__ = [
    "TaskSpec",
    "TaskRegistry",
    "REGISTRY",
    "register_default_tasks",
    "list_tasks",
    "get_task",
    "make_trace_id",
    "SchedulerEngine",
    "run_scheduler",
]
