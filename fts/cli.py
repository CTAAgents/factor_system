"""
fts.cli — FTS 统一命令行入口。

提供:
    - python -m fts.cli evolution run    : 启动 L2 因子演化主循环
    python -m fts.cli meta-loop run          : 启动 L1 Meta-Loop
    python -m fts.cli portfolio run          : 启动 L3 组合构建
    python -m fts.cli monitor                : 检查所有循环健康状态
    python -m fts.cli factor list            : 列出 elite 因子
    python -m fts.cli factor show <factor_id>: 查看单个因子详情
    python -m fts.cli version                : 打印版本号

HARNESS §trace_id 全链路: 所有子命令启动时生成 trace_id 并贯穿整个执行流程。

版本: v0.1.0
"""
# pylint: disable=broad-exception-caught,too-many-locals

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__ as FTS_VERSION
from .factor_engine import (
    EVOLUTION_VERSION,
    generate_run_id,
    generate_trace_id,
)
from .monitor import (
    check_all_status,
    format_status_report,
    status_report_to_json,
)


def _cmd_version(_args: argparse.Namespace) -> int:
    """打印版本号。"""
    print(f"FTS version: {FTS_VERSION}")
    print(f"Factor engine version: {EVOLUTION_VERSION}")
    return 0


def _cmd_monitor(args: argparse.Namespace) -> int:
    """检查所有循环健康状态。"""
    try:
        report = check_all_status()
        if args.json:
            print(status_report_to_json(report))
        else:
            print(format_status_report(report))
        return 0 if report.healthy else 1
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] monitor failed: {e}", file=sys.stderr)
        return 2


def _cmd_evolution_run(args: argparse.Namespace) -> int:
    """启动 L2 因子演化主循环。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    print(f"[evolution] trace_id={trace_id} run_id={run_id}")
    print(f"[evolution] max_generations={args.max_generations}")
    print("[evolution] L2 Evolution Loop 启动占位（实际实现见 factor_engine.evolution_loop）")
    # 实际启动逻辑由 factor_engine.EvolutionLoop 承担
    # 此处仅提供入口和 trace_id 注入，避免 CLI 与核心循环强耦合
    return 0


def _cmd_meta_loop_run(_args: argparse.Namespace) -> int:
    """启动 L1 Meta-Loop。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    print(f"[meta-loop] trace_id={trace_id} run_id={run_id}")
    print("[meta-loop] L1 Meta-Loop 启动占位（实际实现见 factor_engine.meta_loop）")
    return 0


def _cmd_portfolio_run(_args: argparse.Namespace) -> int:
    """启动 L3 组合构建。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    print(f"[portfolio] trace_id={trace_id} run_id={run_id}")
    print("[portfolio] L3 Portfolio Loop 启动占位（实际实现见 factor_engine.portfolio_loop）")
    return 0


def _cmd_factor_list(args: argparse.Namespace) -> int:
    """列出 elite 因子。"""
    elite_dir = Path(args.elite_dir or "memory/knowledge/factors/elite")
    if not elite_dir.exists():
        print(f"[factor list] elite 目录不存在: {elite_dir}")
        return 0
    factors = sorted(elite_dir.glob("*.json"))
    if not factors:
        print(f"[factor list] 无 elite 因子（{elite_dir}）")
        return 0
    print(f"=== Elite Factors ({len(factors)}) ===")
    for p in factors:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            print(f"  - {data.get('factor_id', p.stem)} | {data.get('name', '<unnamed>')} | gen={data.get('generation', '?')}")
        except Exception as e:  # noqa: BLE001
            print(f"  - {p.stem} [读取失败: {e}]")
    return 0


def _cmd_factor_show(args: argparse.Namespace) -> int:
    """查看单个因子详情。"""
    factor_id = args.factor_id
    elite_dir = Path(args.elite_dir or "memory/knowledge/factors/elite")
    candidates = list(elite_dir.glob(f"*{factor_id}*.json"))
    if not candidates:
        print(f"[factor show] 未找到因子: {factor_id} (搜索目录: {elite_dir})")
        return 1
    p = candidates[0]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[factor show] 读取失败: {e}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    """构建 CLI parser。"""
    parser = argparse.ArgumentParser(
        prog="fts",
        description="FTS — Factor Intelligence System（因子智能系统）",
    )
    parser.add_argument("--version", action="store_true", help="打印版本号并退出")
    sub = parser.add_subparsers(dest="command", required=False)

    # version
    p_version = sub.add_parser("version", help="打印版本号")
    p_version.set_defaults(func=_cmd_version)

    # monitor
    p_monitor = sub.add_parser("monitor", help="检查所有循环健康状态")
    p_monitor.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_monitor.set_defaults(func=_cmd_monitor)

    # evolution run
    p_evo = sub.add_parser("evolution", help="L2 因子演化主循环")
    evo_sub = p_evo.add_subparsers(dest="subcommand", required=False)
    p_evo_run = evo_sub.add_parser("run", help="启动 L2 演化")
    p_evo_run.add_argument("--max-generations", type=int, default=10,
                           help="最大演化代数（默认 10）")
    p_evo_run.set_defaults(func=_cmd_evolution_run)

    # meta-loop run
    p_meta = sub.add_parser("meta-loop", help="L1 Meta-Loop")
    meta_sub = p_meta.add_subparsers(dest="subcommand", required=False)
    p_meta_run = meta_sub.add_parser("run", help="启动 L1 Meta-Loop")
    p_meta_run.set_defaults(func=_cmd_meta_loop_run)

    # portfolio run
    p_port = sub.add_parser("portfolio", help="L3 组合构建")
    port_sub = p_port.add_subparsers(dest="subcommand", required=False)
    p_port_run = port_sub.add_parser("run", help="启动 L3 组合构建")
    p_port_run.set_defaults(func=_cmd_portfolio_run)

    # factor
    p_factor = sub.add_parser("factor", help="因子管理")
    factor_sub = p_factor.add_subparsers(dest="subcommand", required=False)
    p_factor_list = factor_sub.add_parser("list", help="列出 elite 因子")
    p_factor_list.add_argument("--elite-dir", default=None, help="elite 因子目录")
    p_factor_list.set_defaults(func=_cmd_factor_list)

    p_factor_show = factor_sub.add_parser("show", help="查看因子详情")
    p_factor_show.add_argument("factor_id", help="因子 ID（支持部分匹配）")
    p_factor_show.add_argument("--elite-dir", default=None, help="elite 因子目录")
    p_factor_show.set_defaults(func=_cmd_factor_show)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        return _cmd_version(args)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
