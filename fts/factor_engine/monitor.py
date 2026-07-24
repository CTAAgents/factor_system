"""
loop_engine/monitor.py — Loop Engineering 监控状态查询

提供统一的 CLI 接口检查 L1/L2/L3 三层循环的运行状态、熔断情况、最新运行结果。

用法:
    python -m loop_engine.monitor status          # 查看全部状态
    python -m loop_engine.monitor status --json   # JSON 格式输出

版本: v1.1.0（与 FTS 同步）
"""
# pylint: disable=too-many-instance-attributes

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional




@dataclass
class LoopStatus:
    """单层循环的状态摘要。"""
    name: str                              # L1 / L2 / L3
    state_file: str                        # 状态文件路径
    exists: bool                           # 状态文件是否存在
    run_id: str = ""                       # 最后运行 ID
    status: str = "unknown"                # running/paused/completed/circuit_broken
    last_updated: str = ""                 # ISO 8601
    tokens_consumed: int = 0
    budget_limit: int = 0
    last_error: Optional[str] = None
    age_hours: float = 0.0                 # 距上次更新的小时数
    healthy: bool = True


@dataclass
class AllStatus:
    """全部三层循环的状态汇总。"""
    loops: list[LoopStatus] = field(default_factory=list)
    any_circuit_broken: bool = False
    any_stale: bool = False                # 超过 24 小时未更新
    total_tokens_today: int = 0
    checked_at: str = ""


def read_state(file_path: Path) -> dict[str, Any]:
    """安全读取 JSON 状态文件。"""
    if not file_path.exists():
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def check_loop(
    name: str,
    state_dir: str | Path,
    max_stale_hours: float = 24.0,
) -> LoopStatus:
    """检查单层循环的状态。"""
    sp = Path(state_dir) / "state.json"
    data = read_state(sp)

    status = data.get("status", "unknown")
    last_updated = data.get("last_updated", "")

    age_hours = 0.0
    if last_updated:
        try:
            last_dt = datetime.fromisoformat(last_updated)
            age_hours = (datetime.now() - last_dt).total_seconds() / 3600
        except (ValueError, TypeError):
            pass

    healthy = (
        status not in ("circuit_broken",)
        and age_hours < max_stale_hours
    )

    return LoopStatus(
        name=name,
        state_file=str(sp.resolve() if sp.exists() else sp),
        exists=sp.exists(),
        run_id=data.get("run_id", ""),
        status=status,
        last_updated=last_updated,
        tokens_consumed=data.get("tokens_consumed", 0),
        budget_limit=data.get("budget_limit", 0),
        last_error=data.get("last_error"),
        age_hours=round(age_hours, 1),
        healthy=healthy,
    )


def check_all(
    fdt_root: str | Path = "",
    max_stale_hours: float = 24.0,
) -> AllStatus:
    """检查全部三层循环状态。"""
    root = Path(fdt_root) if fdt_root else Path(".")
    memory = root / "memory"

    confs = [
        ("L1", memory / "meta_loop"),
        ("L2", memory / "evolution"),
        ("L3", memory / "portfolio"),
    ]

    loops: list[LoopStatus] = []
    for name, state_dir in confs:
        loop = check_loop(name, state_dir, max_stale_hours)
        loops.append(loop)

    any_circuit_broken = any(l.status == "circuit_broken" for l in loops)
    any_stale = any(l.age_hours > max_stale_hours for l in loops if l.exists)
    total_tokens = sum(l.tokens_consumed for l in loops)

    return AllStatus(
        loops=loops,
        any_circuit_broken=any_circuit_broken,
        any_stale=any_stale,
        total_tokens_today=total_tokens,
        checked_at=datetime.now().isoformat(),
    )


def print_status_table(status: AllStatus) -> None:
    """打印格式化状态表。"""
    print(f"{'='*70}")
    print(f"  Loop Engineering — 状态总览 @ {status.checked_at[:19]}")
    print(f"{'='*70}")

    header = f"{'Loop':<5} {'状态':<16} {'运行ID':<28} {'Token':<10} {'已过(h)':<8} {'健康':<6}"
    print(header)
    print("-" * 70)

    for loop in status.loops:
        status_icon = "🟢" if loop.healthy else ("🔴" if loop.status == "circuit_broken" else "🟡")
        rid = loop.run_id[:24] if loop.run_id else "-"
        tok = f"{loop.tokens_consumed}/{loop.budget_limit}" if loop.budget_limit else str(loop.tokens_consumed)
        age = f"{loop.age_hours:.1f}h"
        health = "OK" if loop.healthy else "WARN"
        print(f"{loop.name:<5} {status_icon}{' '+loop.status:<15} {rid:<28} {tok:<10} {age:<8} {health:<6}")

    print("-" * 70)

    if status.any_circuit_broken:
        print("\n⚠️  检测到熔断状态！")
        for loop in status.loops:
            if loop.status == "circuit_broken":
                print(f"  🔴 {loop.name}: {loop.last_error or '未知原因'}")
                print(f"     状态文件: {loop.state_file}")
                print("     解决方案: 审查原因后更新 program.md 并确认熔断恢复")

    if status.any_stale:
        print("\n⚠️  检测到过期状态（超过 24h 未更新）！")
        for loop in status.loops:
            if loop.age_hours > 24 and loop.exists:
                print(f"  🟡 {loop.name}: 最后更新 {loop.age_hours:.0f}h 前")

    if not any(l.exists for l in status.loops):
        print("\n 没有找到任何状态文件。系统尚未运行过。")


def main() -> None:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="Loop Engineering 监控")
    parser.add_argument("command", nargs="?", default="status", choices=["status"])
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")
    parser.add_argument("--fdt-root", default="", help="FDT 项目根目录")
    args = parser.parse_args()

    root = Path(args.fdt_root) if args.fdt_root else Path.cwd()
    status = check_all(root)

    if args.json:
        print(json.dumps(asdict(status), ensure_ascii=False, indent=2))
    else:
        print_status_table(status)

    sys.exit(0)


if __name__ == "__main__":
    main()


__all__ = [
    "LoopStatus",
    "AllStatus",
    "check_loop",
    "check_all",
    "print_status_table",
    "main",
]
