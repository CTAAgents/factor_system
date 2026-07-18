"""
fts/scheduler/engine.py — FTS 调度器引擎。

将 TaskRegistry 中的任务接入 APScheduler 定时执行。

用法:
    from fts.scheduler.engine import SchedulerEngine
    engine = SchedulerEngine()
    engine.start()  # 后台运行所有已注册任务
    engine.stop()

HARNESS §trace_id 全链路: 每个任务执行生成独立 trace_id。
HARNESS §降级/熔断: APScheduler 不可用时静默回退到空操作。

版本: v0.1.0
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from .tasks import REGISTRY, TaskSpec, register_default_tasks, make_trace_id

logger = logging.getLogger(__name__)


class SchedulerEngine:
    """FTS 调度器引擎 — 包装 APScheduler BackgroundScheduler。

    如果 APScheduler 未安装，所有方法静默降级（无操作）。
    """

    def __init__(self, task_registry: Any = None):
        self._registry = task_registry or REGISTRY
        self._scheduler: Any = None
        self._running = False

    # ── 生命周期 ──

    def start(self, daemon: bool = True) -> bool:
        """启动调度器。

        Args:
            daemon: 是否后台运行

        Returns:
            True=启动成功, False=APScheduler 未安装
        """
        if self._running:
            logger.warning("SchedulerEngine 已在运行")
            return True

        scheduler = self._create_scheduler(daemon=daemon)
        if scheduler is None:
            return False

        register_default_tasks()
        tasks = self._registry.list_enabled()

        for task in tasks:
            self._add_job(scheduler, task)

        scheduler.start()
        self._scheduler = scheduler
        self._running = True
        logger.info("SchedulerEngine 已启动, 载入 %d 个任务", len(tasks))
        return True

    def stop(self) -> None:
        """停止调度器。"""
        if self._scheduler is not None and self._running:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception as e:
                logger.warning("调度器关闭异常: %s", e)
        self._running = False
        self._scheduler = None
        logger.info("SchedulerEngine 已停止")

    @property
    def running(self) -> bool:
        """调度器是否在运行。"""
        return self._running

    # ── 内部方法 ──

    def _create_scheduler(self, daemon: bool = True) -> Any:
        """创建 APScheduler BackgroundScheduler 实例。

        Returns:
            scheduler 实例, 或 None（未安装）
        """
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            return BackgroundScheduler(daemon=daemon)
        except ImportError:
            logger.warning(
                "APScheduler 未安装。调度器不可用。"
                "请执行: pip install fts[dev] 或 pip install apscheduler"
            )
            return None

    def _add_job(self, scheduler: Any, task: TaskSpec) -> None:
        """将单个任务添加到调度器。

        Args:
            scheduler: APScheduler 实例
            task: 任务规格
        """
        try:
            scheduler.add_job(
                self._make_job_fn(task),
                trigger="cron",
                # 解析 5 字段 cron: minute hour day month day_of_week
                minute=self._cron_field(task.cron_expression, 0, "*"),
                hour=self._cron_field(task.cron_expression, 1, "*"),
                day=self._cron_field(task.cron_expression, 2, "*"),
                month=self._cron_field(task.cron_expression, 3, "*"),
                day_of_week=self._cron_field(task.cron_expression, 4, "*"),
                id=task.name,
                name=task.description or task.name,
                replace_existing=False,
            )
            logger.debug("任务已注册: %s [%s]", task.name, task.cron_expression)
        except Exception as e:
            logger.error("任务注册失败 [%s]: %s", task.name, e)

    def _make_job_fn(self, task: TaskSpec):
        """创建可调用的任务执行函数。"""
        trace_id = make_trace_id(task.name)

        def job_fn() -> None:
            logger.info("[%s] 任务开始: %s (trace_id=%s)", task.name, task.description, trace_id)
            # 实际执行：导入并调用 callable
            try:
                module_path, _, func_name = task.callable_path.rpartition(".")
                import importlib
                module = importlib.import_module(module_path)
                func = getattr(module, func_name)
                func()
                logger.info("[%s] 任务完成: %s", task.name, task.description)
            except Exception as e:
                logger.error("[%s] 任务失败: %s", task.name, e)

        return job_fn

    @staticmethod
    def _cron_field(expression: str, index: int, default: str) -> str:
        """从 5 字段 cron 表达式中提取指定位置的字段。"""
        parts = expression.strip().split()
        if len(parts) > index:
            return parts[index]
        return default


# ─── CLI 入口 ────────────────────────────────────────────

def run_scheduler(daemon: bool = True) -> None:
    """启动调度器并阻塞（daemon=False 时）或后台运行（daemon=True 时）。"""
    engine = SchedulerEngine()
    started = engine.start(daemon=daemon)
    if not started:
        logger.error("调度器启动失败（APScheduler 未安装）")
        return
    if not daemon:
        try:
            while engine.running:
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("收到中断信号，停止调度器")
        finally:
            engine.stop()


__all__ = [
    "SchedulerEngine",
    "run_scheduler",
]
