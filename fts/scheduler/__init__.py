"""
fts.scheduler — FTS 调度层。

定时任务注册表，支持:
    - 注册 task（cron 风格触发器 + 可调用）
    - 列出所有 task
    - 手动触发 task（用于测试和调试）

实际调度执行由外部调度器（cron / APScheduler / 永驻进程）承担，
本模块仅提供任务注册和触发器元数据。

版本: v0.1.0
"""

from .tasks import (
    TaskSpec,
    TaskRegistry,
    REGISTRY,
    register_default_tasks,
    list_tasks,
    get_task,
)

__version__ = "0.1.0"
__all__ = [
    "TaskSpec",
    "TaskRegistry",
    "REGISTRY",
    "register_default_tasks",
    "list_tasks",
    "get_task",
]
