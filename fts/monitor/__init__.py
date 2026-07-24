"""
fts.monitor — FTS 健康监控 + 精英因子跟踪。

提供:
    - check_loop_status(): 检查单个循环状态
    - check_all_status():  检查所有循环状态
    - format_status_report(): 格式化状态报告（人类可读）
    - EliteFactorTracker: 精英因子样本外跟踪
    - AutoRetireManager: 自动淘汰管理

HARNESS §可观测性: 监控数据应包含 trace_id、运行时间、状态、错误信息。

底层调用 factor_engine.monitor（从 FDT loop_engine/monitor.py 迁移），
本模块提供 FTS 项目级的封装接口。

版本: v0.1.0
"""
# pylint: disable=too-many-instance-attributes

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from ..factor_engine import EVOLUTION_VERSION
from ..factor_engine.monitor import AllStatus, LoopStatus, check_all, check_loop

from .elite_tracker import (
    AutoRetireConfig,
    AutoRetireManager,
    EliteFactorTracker,
    TrackingSnapshot,
)
from .http_server import FTSDashboardServer


# ─── 监控数据契约 ─────────────────────────────────────────

@dataclass
class LoopStatusReport:
    """单个循环状态报告（FTS 项目级封装）。

    Attributes:
        loop_name: 循环名（L1 / L2 / L3）
        healthy: 是否健康
        last_run_at: 最近运行时间 ISO 8601（空字符串 = 从未运行）
        status: 状态字符串（running / paused / completed / circuit_broken / unknown）
        last_error: 最近错误（None = 无错误）
        version: 引擎版本号
        run_id: 最近一次运行的 run_id
        tokens_consumed: 本次运行消耗的 token 数
        age_hours: 距上次更新的小时数
    """
    loop_name: str
    healthy: bool
    last_run_at: str = ""
    status: str = "unknown"
    last_error: Optional[str] = None
    version: str = EVOLUTION_VERSION
    run_id: str = ""
    tokens_consumed: int = 0
    age_hours: float = 0.0


@dataclass
class SystemStatusReport:
    """系统级状态报告。"""
    healthy: bool
    loops: list[LoopStatusReport] = field(default_factory=list)
    checked_at: str = ""
    fts_version: str = ""
    any_circuit_broken: bool = False
    any_stale: bool = False
    total_tokens_today: int = 0


# ─── 监控接口 ─────────────────────────────────────────────

def _loop_status_to_report(status: LoopStatus) -> LoopStatusReport:
    """LoopStatus → LoopStatusReport。"""
    return LoopStatusReport(
        loop_name=status.name,
        healthy=bool(status.healthy),
        last_run_at=status.last_updated,
        status=str(status.status),
        last_error=status.last_error,
        run_id=status.run_id,
        tokens_consumed=status.tokens_consumed,
        age_hours=float(status.age_hours),
    )


def check_loop_status(loop_name: str,
                      project_root: Optional[Path] = None,
                      ) -> LoopStatusReport:
    """检查单个循环状态。

    Args:
        loop_name: 循环名（L1 / L2 / L3）
        project_root: 项目根目录（None = 当前工作目录）

    Returns:
        LoopStatusReport
    """
    root = Path(project_root) if project_root else Path.cwd()
    memory = root / "memory"
    state_dir_map = {
        "L1": memory / "meta_loop",
        "L2": memory / "evolution",
        "L3": memory / "portfolio",
        "meta_loop": memory / "meta_loop",
        "evolution": memory / "evolution",
        "portfolio": memory / "portfolio",
    }
    state_dir = state_dir_map.get(loop_name)
    if state_dir is None:
        return LoopStatusReport(
            loop_name=loop_name,
            healthy=False,
            status="unknown",
            last_error=f"unknown loop name: {loop_name}",
        )
    try:
        status = check_loop(loop_name, state_dir)
        return _loop_status_to_report(status)
    except Exception as e:  # noqa: BLE001
        return LoopStatusReport(
            loop_name=loop_name,
            healthy=False,
            status="error",
            last_error=str(e),
        )


def check_all_status(project_root: Optional[Path] = None,
                     ) -> SystemStatusReport:
    """检查所有循环状态。

    Args:
        project_root: 项目根目录（None = 当前工作目录）

    Returns:
        SystemStatusReport
    """
    root = Path(project_root) if project_root else Path.cwd()
    try:
        all_status: AllStatus = check_all(root)
        loops = [_loop_status_to_report(l) for l in all_status.loops]
        return SystemStatusReport(
            healthy=all_status.loops and all(l.healthy for l in all_status.loops),
            loops=loops,
            checked_at=all_status.checked_at,
            fts_version=EVOLUTION_VERSION,
            any_circuit_broken=all_status.any_circuit_broken,
            any_stale=all_status.any_stale,
            total_tokens_today=all_status.total_tokens_today,
        )
    except Exception:  # noqa: BLE001
        return SystemStatusReport(
            healthy=False,
            loops=[],
            checked_at="",
            fts_version=EVOLUTION_VERSION,
            any_circuit_broken=False,
            any_stale=False,
            total_tokens_today=0,
        )


def format_status_report(report: SystemStatusReport) -> str:
    """格式化状态报告（人类可读）。"""
    lines = [
        "=== FTS System Status ===",
        f"Overall healthy : {'YES' if report.healthy else 'NO'}",
        f"Checked at      : {report.checked_at or '-'}",
        f"FTS version     : {report.fts_version}",
        f"Circuit broken  : {'YES' if report.any_circuit_broken else 'NO'}",
        f"Stale (>24h)    : {'YES' if report.any_stale else 'NO'}",
        f"Tokens today    : {report.total_tokens_today}",
        "",
        "=== Loop Status ===",
    ]
    if not report.loops:
        lines.append("(no loop status available)")
    for loop in report.loops:
        icon = "[OK]  " if loop.healthy else "[FAIL]"
        lines.append(
            f"{icon} {loop.loop_name:<3} | status={loop.status:<16} | "
            f"run_id={loop.run_id or '-':<24} | "
            f"age={loop.age_hours:.1f}h"
        )
        if loop.last_error:
            lines.append(f"       error: {loop.last_error}")
    return "\n".join(lines)


def status_report_to_json(report: SystemStatusReport) -> str:
    """转 JSON 字符串。"""
    return json.dumps(asdict(report), indent=2, ensure_ascii=False, default=str)


# ─── Elite 因子跟踪导出 ────────────────────────────────────

__all__ = [
    # 监控
    "LoopStatusReport",
    "SystemStatusReport",
    "check_loop_status",
    "check_all_status",
    "format_status_report",
    "status_report_to_json",
    # Web UI
    "FTSDashboardServer",
    # 因子跟踪
    "TrackingSnapshot",
    "EliteFactorTracker",
    "AutoRetireConfig",
    "AutoRetireManager",
]
