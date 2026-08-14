"""
fts.workflow — CTA 手册 WorkFlow 端到端工作流包。

按《期货CTA多因子策略标准化作业手册》(v1.3) 定义 11 阶段 + 因子质检闭环:
    - :mod:`stages`   阶段定义（动作 ↔ CLI 命令映射）
    - :mod:`executor` 异步执行器（真实 subprocess 调用 fts.cli / 脚本）
    - :mod:`store`    SQLite(WAL) 运行状态持久化

对外 API::
    get_stages()            -> 阶段定义列表（供 UI / API 渲染）
    WorkflowExecutor        -> 单阶段 / 端到端执行
    WorkflowStore           -> 运行状态读写

版本: v1.0.0
"""

from __future__ import annotations

from .executor import REPORT_ROOT, WorkflowExecutor
from .stages import STAGES, Stage, StageAction, get_stage, get_stages
from .store import SCHEMA, WorkflowStore

__all__ = [
    "STAGES",
    "Stage",
    "StageAction",
    "WorkflowExecutor",
    "WorkflowStore",
    "REPORT_ROOT",
    "SCHEMA",
    "get_stage",
    "get_stages",
]
