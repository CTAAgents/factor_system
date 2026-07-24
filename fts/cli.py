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

import numpy as np
import pandas as pd

from . import __version__ as FTS_VERSION
from .config import get_config
from .data import FTSDataProvider
from .factor_engine import (
    EVOLUTION_VERSION,
    DEFAULT_BUDGET_CONFIG,
    EvolutionLoop,
    FactorVerifier,
    get_default_llm_client,
    get_default_seed_pool,
    generate_run_id,
    generate_trace_id,
    MacroEvolver,
    MetaLoop,
    MetaRunResult,
    PortfolioLoop,
    PortfolioRunResult,
)
from .llm import MockLLMClient
from .monitor import (
    check_all_status,
    format_status_report,
    status_report_to_json,
)
from .scheduler import (
    SchedulerEngine,
    list_tasks as list_scheduler_tasks,
)


def _prepare_data(symbol: str = "000001", days: int = 500) -> tuple[pd.DataFrame, np.ndarray]:
    """准备演化所需数据（腾讯 API 优先 → 合成数据降级）。

    Args:
        symbol: 股票/ETF 代码
        days: 回溯天数

    Returns:
        (OHLCV DataFrame, forward_returns np.ndarray)
    """
    provider = FTSDataProvider()
    df = provider.get_ohlcv(symbol, days=days)

    forward_returns = np.zeros(len(df))
    closes = df["close"].values
    if len(closes) > 5:
        forward_returns[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
    return df, forward_returns


def _prepare_cross_section_data(
    universe: str = "csi300",
    days: int = 500,
    max_stocks: int = 50,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, np.ndarray]:
    """准备横截面演化所需的沪深300成分股面板数据。

    Args:
        universe: "csi300"（沪深300成分股）
        days: 回溯天数
        max_stocks: 最大标的数量

    Returns:
        (panel, common_dates, forward_returns — 使用第一个标的作为微参参考)
    """
    provider = FTSDataProvider()
    panel, common_dates = provider.get_csi300_panel(days=days, max_stocks=max_stocks)

    first_sym = list(panel.keys())[0]
    first_df = panel[first_sym]
    closes = first_df["close"].values
    fwd_ret = np.zeros(len(closes))
    if len(closes) > 5:
        fwd_ret[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)

    return panel, common_dates, fwd_ret


def _cmd_version(_args: argparse.Namespace) -> int:
    """打印版本号。"""
    cfg = get_config()
    print(f"FTS version: {FTS_VERSION}")
    print(f"Factor engine version: {EVOLUTION_VERSION}")
    print(f"Config memory_dir: {cfg.memory_dir}")
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
    """启动 L2 因子演化主循环（支持单标或横截面模式）。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    cfg = get_config()
    print(f"[evolution] trace_id={trace_id} run_id={run_id}")
    print(f"[evolution] max_generations={args.max_generations}")

    if args.universe == "csi300":
        # ── 横截面模式（沪深300成分股） ──
        print(f"[evolution] universe={args.universe} (max_stocks={args.max_stocks})")
        panel, common_dates, fwd_ret = _prepare_cross_section_data(
            universe=args.universe, days=500, max_stocks=args.max_stocks,
        )
        print(f"[evolution] panel symbols={len(panel)}, common_dates={len(common_dates)}")

        llm = get_default_llm_client()
        print(f"[evolution] LLM backend: {type(llm).__name__}")

        seed_pool = get_default_seed_pool()
        verifier = FactorVerifier()

        # 用第一个股票构造常规 data/forward_returns（微参优化用）
        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=cfg.elite_dir,
            memory_dir=cfg.memory_dir + "/evolution",
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=min(args.max_generations * 3, 30),
            cross_section_data=panel,
            cross_section_dates=common_dates,
        )
    else:
        # ── 单标模式 ──
        print(f"[evolution] symbol={args.symbol}")
        data_df, fwd_ret = _prepare_data(symbol=args.symbol, days=500)
        print(f"[evolution] data shape: {data_df.shape}, forward_returns: {len(fwd_ret)}")

        llm = get_default_llm_client()
        print(f"[evolution] LLM backend: {type(llm).__name__}")

        seed_pool = get_default_seed_pool()
        verifier = FactorVerifier()

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=cfg.elite_dir,
            memory_dir=cfg.memory_dir + "/evolution",
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=min(args.max_generations * 5, cfg.micro_trials_per_generation),
        )

    # 熔断预算：每个因子最多 4000 token
    budget = DEFAULT_BUDGET_CONFIG.copy()
    budget["max_generation"] = args.max_generations
    loop.budget = budget

    # 执行演化
    try:
        result = loop.run(max_generation=args.max_generations)
        print(f"[evolution] 完成: status={result.status} "
              f"generations={result.generations_completed} "
              f"elite_count={len(result.elite_factor_ids)}")
        if result.circuit_breaker_reason:
            print(f"[evolution] 熔断原因: {result.circuit_breaker_reason}")
        return 0 if result.status == "completed" else 1
    except Exception as e:  # noqa: BLE001
        print(f"[evolution] 运行失败: {e}", file=sys.stderr)
        return 2


def _cmd_meta_loop_run(args: argparse.Namespace) -> int:
    """启动 L1 Meta-Loop（市场感知 + Bootstrapping）。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    cfg = get_config()
    print(f"[meta-loop] trace_id={trace_id} run_id={run_id}")

    llm = get_default_llm_client()
    print(f"[meta-loop] LLM backend: {type(llm).__name__}")

    try:
        # MetaLoop
        loop = MetaLoop(
            memory_dir=cfg.memory_dir + "/meta_loop",
            llm_client=llm,
        )
        result = loop.run()
        print(f"[meta-loop] 完成: status={result.status} injected={len(result.injected_candidate_ids)}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[meta-loop] 运行失败: {e}", file=sys.stderr)
        return 2


def _cmd_portfolio_run(args: argparse.Namespace) -> int:
    """启动 L3 组合构建（加载 elite 因子 → 正交化 → 信号合成）。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    cfg = get_config()
    print(f"[portfolio] trace_id={trace_id} run_id={run_id}")

    try:
        loop = PortfolioLoop(
            elite_dir=cfg.elite_dir,
            memory_dir=cfg.memory_dir + "/portfolio",
        )
        result = loop.run()
        print(f"[portfolio] 完成: status={result.status} "
              f"factors={result.n_factors_retained} "
              f"sharpe={result.combo_sharpe:.4f}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[portfolio] 运行失败: {e}", file=sys.stderr)
        return 2


def _cmd_scheduler_run(_args: argparse.Namespace) -> int:
    """启动调度器后台运行。"""
    engine = SchedulerEngine()
    started = engine.start(daemon=True)
    if not started:
        print("[scheduler] 调度器启动失败（APScheduler 未安装）", file=sys.stderr)
        return 1
    print(f"[scheduler] 调度器已启动（{len(list_scheduler_tasks())} 个任务）")
    return 0


def _cmd_scheduler_list(_args: argparse.Namespace) -> int:
    """列出所有已注册任务。"""
    tasks = list_scheduler_tasks()
    if not tasks:
        print("[scheduler] 无已注册任务")
        return 0
    print(f"=== Scheduler Tasks ({len(tasks)}) ===")
    for t in tasks:
        status = "✔" if t.enabled else "✘"
        print(f"  {status} {t.name:25s} | {t.cron_expression:12s} | {t.description}")
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
    p_evo_run.add_argument("--symbol", type=str, default="000001",
                           help="演化目标品种代码（默认 000001 平安银行）")
    p_evo_run.add_argument("--universe", type=str, default="single",
                           choices=["single", "csi300"],
                           help="演化股票池类型: single（单标）/ csi300（沪深300横截面）")
    p_evo_run.add_argument("--max-stocks", type=int, default=50,
                           help="横截面模式最大标的数（默认 50，0 = 使用全部品种）")
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

    # scheduler
    p_sched = sub.add_parser("scheduler", help="任务调度器")
    sched_sub = p_sched.add_subparsers(dest="subcommand", required=False)
    p_sched_run = sched_sub.add_parser("run", help="启动调度器后台运行")
    p_sched_run.set_defaults(func=_cmd_scheduler_run)
    p_sched_list = sched_sub.add_parser("list", help="列出所有已注册任务")
    p_sched_list.set_defaults(func=_cmd_scheduler_list)

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
