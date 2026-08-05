"""
fts.cli — FTS 统一命令行入口。

提供:
    - python -m fts.cli evolution run    : 启动 L2 因子演化主循环
    python -m fts.cli meta-loop run          : 启动 L1 Meta-Loop
    python -m fts.cli portfolio run          : 启动 L3 组合构建
    python -m fts.cli monitor                : 检查所有循环健康状态
    python -m fts.cli factor list [filters] : 列出 elite 因子（支持筛选/多样性）
    python -m fts.cli factor show <id>       : 查看单个因子详情
    python -m fts.cli factor stats           : 因子家族分布统计
    python -m fts.cli factor lineage <id>    : 因子演化血缘查询
    python -m fts.cli factor seeds           : 列出种子因子
    python -m fts.cli version                : 打印版本号

HARNESS §trace_id 全链路: 所有子命令启动时生成 trace_id 并贯穿整个执行流程。

版本: v0.2.0
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
    DEFAULT_L3_VERIFIER_CONFIG,
    L3VerifierConfig,
    EvolutionLoop,
    FactorVerifier,
    SeedPool,
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
    FTSDashboardServer,
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

    # 注入基本面数据（MCP 缓存 → 合成数据降级）
    try:
        from .data_fundamental import get_fundamental_provider
        fp = get_fundamental_provider(mcp_available=True)
        panel = fp.enrich_panel(panel, trace_id="cli_prepare")
        print(f"[prepare] 基本面数据注入完成: {len(panel)} 只股票")
    except Exception as e:
        print(f"[prepare] 基本面注入失败（使用合成数据）: {e}")

    first_sym = list(panel.keys())[0]
    first_df = panel[first_sym]
    closes = first_df["close"].values
    fwd_ret = np.zeros(len(closes))
    if len(closes) > 5:
        fwd_ret[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)

    return panel, common_dates, fwd_ret


def _prepare_futures_data(
    days: int = 500,
    max_symbols: int = 0,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, np.ndarray]:
    """准备期货横截面演化所需的面板数据。

    Args:
        days: 回溯天数
        max_symbols: 最大品种数（0 = 使用全部 FUTURES_CORE_SUBSET）

    Returns:
        (panel, common_dates, forward_returns — 使用第一个品种作为微参参考)
    """
    from .data_futures import FUTURES_CORE_SUBSET
    symbols = FUTURES_CORE_SUBSET[:max_symbols] if max_symbols > 0 else FUTURES_CORE_SUBSET

    provider = FTSDataProvider()
    panel, common_dates = provider.get_futures_panel(symbols, days=days, trace_id="cli_prepare")

    print(f"[prepare] 期货品种数={len(panel)}, 共同日期={len(common_dates)}")
    for sym, df in sorted(panel.items()):
        print(f"  {sym}: {len(df)} 行, 最新 close={df['close'].iloc[-1]:.2f}")

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

        seed_pool = get_default_seed_pool(market="stock")
        verifier = FactorVerifier()

        # 用第一个品种构造常规 data/forward_returns（微参优化用）
        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=cfg.get_elite_dir("stock"),
            memory_dir=cfg.memory_dir + "/evolution",
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=min(args.max_generations * 3, 30),
            cross_section_data=panel,
            cross_section_dates=common_dates,
            market="stock",
        )
    elif args.universe == "futures":
        # ── 期货横截面模式（使用期货专用种子因子） ──
        print(f"[evolution] universe=futures (max_symbols={args.max_stocks})")
        panel, common_dates, fwd_ret = _prepare_futures_data(
            days=500, max_symbols=args.max_stocks,
        )
        print(f"[evolution] 期货 panel symbols={len(panel)}, common_dates={len(common_dates)}")

        llm = get_default_llm_client()
        print(f"[evolution] LLM backend: {type(llm).__name__}")

        # 期货模式使用期货专用种子因子（13个期货特有因子）
        seed_pool = SeedPool(market="futures")
        verifier = FactorVerifier()

        # 用第一个品种构造常规 data/forward_returns（微参优化用）
        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=cfg.get_elite_dir("futures"),
            memory_dir=cfg.memory_dir + "/evolution",
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=min(args.max_generations * 3, 30),
            cross_section_data=panel,
            cross_section_dates=common_dates,
            market="futures",
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
            elite_dir=cfg.get_elite_dir("stock"),
            memory_dir=cfg.memory_dir + "/evolution",
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=min(args.max_generations * 5, cfg.micro_trials_per_generation),
            market="stock",
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
    market = getattr(args, "market", None) or cfg.default_market
    print(f"[meta-loop] trace_id={trace_id} run_id={run_id} market={market}")

    llm = get_default_llm_client()
    print(f"[meta-loop] LLM backend: {type(llm).__name__}")

    try:
        # MetaLoop
        loop = MetaLoop(
            memory_dir=cfg.memory_dir + "/meta_loop",
            llm_client=llm,
            market=market,
        )
        result = loop.run()
        print(f"[meta-loop] 完成: status={result.status} injected={len(result.injected_candidate_ids)}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[meta-loop] 运行失败: {e}", file=sys.stderr)
        return 2


def _cmd_portfolio_run(args: argparse.Namespace) -> int:
    """启动 L3 组合构建 → 期货信号管道（L3 完成后自动触发）。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    cfg = get_config()
    print(f"[portfolio] trace_id={trace_id} run_id={run_id}")

    # 根据 universe 选择 elite 目录和合成模式
    universe = getattr(args, "universe", "stock")
    synthesis_mode = getattr(args, "synthesis_mode", None)
    if universe == "futures":
        elite_dir = cfg.get_elite_dir("futures")
        if synthesis_mode is None:
            synthesis_mode = "sharpe_weight"
    else:
        elite_dir = cfg.get_elite_dir("stock")
        if synthesis_mode is None:
            synthesis_mode = "elastic_net"
    print(f"[portfolio] universe={universe} elite_dir={elite_dir} mode={synthesis_mode}")

    # 从配置加载 Verifier 配置
    verifier_cfg = L3VerifierConfig(DEFAULT_L3_VERIFIER_CONFIG)
    if hasattr(cfg, 'verifier') and isinstance(cfg.verifier, dict):
        merged = {**DEFAULT_L3_VERIFIER_CONFIG, **cfg.verifier}
        verifier_cfg = L3VerifierConfig(merged)
        print(f"[portfolio] Verifier 配置已加载: max_correlation={verifier_cfg.get('max_correlation', 0.5)}")

    try:
        loop = PortfolioLoop(
            elite_dir=elite_dir,
            memory_dir=cfg.memory_dir + "/portfolio",
            verifier_config=verifier_cfg,
            synthesis_mode=synthesis_mode,
            market=universe,
        )
        result = loop.run()
        print(f"[portfolio] 完成: status={result.status} "
              f"factors={result.n_factors_retained} "
              f"sharpe={result.combo_sharpe:.4f}")

        # L3 完成后自动触发期货信号管道
        if universe == "futures" and result.status in ("passed", "verifier_warning", "completed"):
            print("[portfolio] 触发期货信号生成管道...")
            from scripts.futures_signal_pipeline import main as signal_main
            rc = signal_main(max_symbols=82, days=120, universe="all")
            if rc != 0:
                print(f"[portfolio] 信号管道异常退出: rc={rc}", file=sys.stderr)
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[portfolio] 运行失败: {e}", file=sys.stderr)
        return 2


def _cmd_ui(args: argparse.Namespace) -> int:
    """启动 Web UI 仪表盘。"""
    try:
        server = FTSDashboardServer(host=args.host, port=args.port)
        server.start()
        # 保持主线程运行
        import time as _time
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            print("[ui] 正在关闭...")
            server.stop()
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[ui] 启动失败: {e}", file=sys.stderr)
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


def _load_factor_repo():
    """延迟加载 FactorRepository（避免 CLI 启动依赖 DuckDB）。"""
    from .factor_engine.factor_db.repository import FactorRepository
    return FactorRepository()


def _cmd_factor_list(args: argparse.Namespace) -> int:
    """列出 elite 因子（支持目录直读 + DuckDB 查询两种模式）。"""
    cfg = get_config()
    market = getattr(args, "market", "futures")

    # 筛选参数决定是否走 DuckDB 查询
    family = getattr(args, "family", None)
    min_ic = getattr(args, "min_ic", None)
    min_sharpe = getattr(args, "min_sharpe", None)
    use_diverse = getattr(args, "diverse", False)
    total_count = getattr(args, "total_count", 10)
    use_db = any([family, min_ic is not None, min_sharpe is not None, use_diverse])

    if use_db:
        try:
            repo = _load_factor_repo()
            if use_diverse:
                factors = repo.get_diverse_factors(
                    market=market,
                    total_count=total_count,
                    max_per_family=getattr(args, "max_per_family", 3),
                    min_ic=min_ic if min_ic is not None else 0.02,
                    min_sharpe=min_sharpe if min_sharpe is not None else 0.5,
                )
            elif family:
                factors = repo.get_by_family(
                    family=family,
                    market=market,
                    min_sharpe=min_sharpe,
                    min_ic=min_ic,
                    limit=getattr(args, "limit", 50),
                )
            else:
                factors = repo.get_eligible(
                    market=market,
                    min_ic=min_ic if min_ic is not None else 0.02,
                    min_sharpe=min_sharpe if min_sharpe is not None else 0.5,
                    require_elite=True,
                )
        except Exception as e:  # noqa: BLE001
            print(f"[factor list] DuckDB 查询失败，回退目录模式: {e}", file=sys.stderr)
            use_db = False

    if not use_db:
        # 根据 market 参数选择目录，默认期货
        if args.elite_dir:
            elite_dir = Path(args.elite_dir)
        else:
            elite_dir = Path(cfg.get_elite_dir(market))

        if not elite_dir.exists():
            print(f"[factor list] elite 目录不存在: {elite_dir}")
            return 0
        factors = []
        for p in sorted(elite_dir.glob("*.json")):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                factors.append(data)
            except Exception as e:  # noqa: BLE001
                print(f"  - {p.stem} [读取失败: {e}]")

    if not factors:
        print(f"[factor list] 无符合条件的因子")
        return 0

    market_label = "期货" if market == "futures" else "股票"
    print(f"=== {market_label} Factors ({len(factors)}) ===")

    # JSON 模式输出
    if getattr(args, "json", False):
        print(json.dumps(factors, indent=2, ensure_ascii=False, default=str))
        return 0

    # 文本表格输出
    if isinstance(factors[0], dict):
        keys = ["factor_id", "name", "family", "market", "generation", "ic", "sharpe"]
        header = "  ".join(f"{k:<12}" for k in keys)
        print(header)
        print("-" * len(header))
        for f in factors:
            vals = []
            for k in keys:
                v = f.get(k, "-")
                if isinstance(v, float):
                    vals.append(f"{v:<12.4f}")
                else:
                    vals.append(f"{str(v):<12}")
            print("  ".join(vals))
    return 0


def _cmd_factor_stats(args: argparse.Namespace) -> int:
    """统计因子家族分布（DuckDB 模式）。"""
    market = getattr(args, "market", None)
    min_sharpe = getattr(args, "min_sharpe", 0.0)
    try:
        repo = _load_factor_repo()
        dist = repo.get_family_distribution(market=market, min_sharpe=min_sharpe)
    except Exception as e:  # noqa: BLE001
        print(f"[factor stats] 查询失败: {e}", file=sys.stderr)
        return 1

    if not dist:
        print("[factor stats] 无符合条件的因子")
        return 0

    if getattr(args, "json", False):
        print(json.dumps(dist, indent=2, ensure_ascii=False, default=str))
        return 0

    total = sum(row.get("count", 0) for row in dist)
    scope = market or "全部市场"
    print(f"=== 因子家族分布 ({scope}, min_sharpe={min_sharpe}) ===")
    print(f"{'家族':<24} {'数量':>6}  {'占比':>8}")
    print("-" * 42)
    for row in dist:
        fam = row.get("family", "unknown")
        count = row.get("count", 0)
        pct = (count / total * 100) if total > 0 else 0
        print(f"{fam:<24} {count:>6}  {pct:>7.1f}%")
    print("-" * 42)
    print(f"{'合计':<24} {total:>6}")
    return 0


def _cmd_factor_lineage(args: argparse.Namespace) -> int:
    """查询单个因子的演化血缘（DuckDB 模式）。"""
    factor_id = args.factor_id
    try:
        repo = _load_factor_repo()
        lineage = repo.get_factor_lineage(factor_id)
    except Exception as e:  # noqa: BLE001
        print(f"[factor lineage] 查询失败: {e}", file=sys.stderr)
        return 1

    if lineage is None:
        print(f"[factor lineage] 未找到因子: {factor_id}")
        return 1

    print(f"=== 因子血缘: {factor_id} ===")
    print(json.dumps(lineage, indent=2, ensure_ascii=False, default=str))
    return 0


def _cmd_factor_seeds(args: argparse.Namespace) -> int:
    """列出种子因子。"""
    market = args.market
    
    if market == "futures":
        from fts.factor_engine.seed_data_futures_full import load_futures_seeds_full
        seeds = load_futures_seeds_full(trace_id="cli_seed_list")
        print(f"=== 期货种子因子 ({len(seeds)}) ===")
        for s in seeds:
            sig = s.get("signature", {})
            params = s.get("params", {})
            print(f"  - {s.get('name', '?')}")
            print(f"      输入: {sig.get('input_fields', [])}")
            print(f"      参数: {params}")
    else:
        from fts.factor_engine.seed_data import load_stock_seeds
        seeds = load_stock_seeds(trace_id="cli_seed_list")
        print(f"=== 股票种子因子 ({len(seeds)}) ===")
        for s in seeds:
            sig = s.get("signature", {})
            params = s.get("params", {})
            print(f"  - {s.get('name', '?')}")
            print(f"      输入: {sig.get('input_fields', [])}")
            print(f"      参数: {params}")
    
    return 0


def _cmd_factor_show(args: argparse.Namespace) -> int:
    """查看单个因子详情。"""
    cfg = get_config()
    factor_id = args.factor_id
    if args.elite_dir:
        elite_dir = Path(args.elite_dir)
    else:
        elite_dir = Path(cfg.get_elite_dir(getattr(args, "market", "stock")))
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
    p_evo_run.add_argument("--universe", type=str, default="futures",
                           choices=["single", "csi300", "futures"],
                           help="演化品种池类型: futures（期货，默认）/ csi300（沪深300）/ single（单标）")
    p_evo_run.add_argument("--max-stocks", type=int, default=0,
                           help="横截面模式最大标的数（0 = 使用全部品种）")
    p_evo_run.set_defaults(func=_cmd_evolution_run)

    # meta-loop run
    p_meta = sub.add_parser("meta-loop", help="L1 Meta-Loop")
    meta_sub = p_meta.add_subparsers(dest="subcommand", required=False)
    p_meta_run = meta_sub.add_parser("run", help="启动 L1 Meta-Loop")
    p_meta_run.add_argument("--market", type=str, default=None,
                            choices=["stock", "futures"],
                            help="市场类型: stock（股票）/ futures（期货），默认使用 config default_market")
    p_meta_run.set_defaults(func=_cmd_meta_loop_run)

    # portfolio run
    p_port = sub.add_parser("portfolio", help="L3 组合构建")
    port_sub = p_port.add_subparsers(dest="subcommand", required=False)
    p_port_run = port_sub.add_parser("run", help="启动 L3 组合构建")
    p_port_run.add_argument("--universe", type=str, default="stock",
                            choices=["stock", "futures"],
                            help="因子池类型: stock（股票）/ futures（期货）")
    p_port_run.add_argument("--synthesis-mode", type=str, default=None,
                            choices=["equal_weight", "sharpe_weight", "elastic_net"],
                            help="信号合成模式: elastic_net（股票默认）/ sharpe_weight（期货默认）/ equal_weight")
    p_port_run.set_defaults(func=_cmd_portfolio_run)

    # ui
    p_ui = sub.add_parser("ui", help="启动 Web UI 仪表盘")
    p_ui.add_argument("--host", type=str, default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    p_ui.add_argument("--port", type=int, default=9100, help="监听端口（默认 9100）")
    p_ui.set_defaults(func=_cmd_ui)

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

    # factor list (增强：支持 DuckDB 查询与筛选)
    p_factor_list = factor_sub.add_parser("list", help="列出 elite 因子")
    p_factor_list.add_argument("--elite-dir", default=None, help="elite 因子目录（仅目录模式使用）")
    p_factor_list.add_argument("--market", default="futures", choices=["futures", "stock"], help="市场类型（默认：futures）")
    p_factor_list.add_argument("--family", default=None, help="按家族筛选（DuckDB 模式）")
    p_factor_list.add_argument("--min-ic", type=float, default=None, help="最低 IC 阈值")
    p_factor_list.add_argument("--min-sharpe", type=float, default=None, help="最低 Sharpe 阈值")
    p_factor_list.add_argument("--diverse", action="store_true", help="启用多样性选择")
    p_factor_list.add_argument("--total-count", type=int, default=10, help="多样性选择总数（默认 10）")
    p_factor_list.add_argument("--max-per-family", type=int, default=3, help="单家族最大因子数（默认 3）")
    p_factor_list.add_argument("--limit", type=int, default=50, help="查询结果上限（默认 50）")
    p_factor_list.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_factor_list.set_defaults(func=_cmd_factor_list)

    # factor show
    p_factor_show = factor_sub.add_parser("show", help="查看因子详情")
    p_factor_show.add_argument("factor_id", help="因子 ID（支持部分匹配）")
    p_factor_show.add_argument("--elite-dir", default=None, help="elite 因子目录")
    p_factor_show.set_defaults(func=_cmd_factor_show)

    # factor seeds
    p_factor_seeds = factor_sub.add_parser("seeds", help="列出种子因子")
    p_factor_seeds.add_argument("--market", default="futures", choices=["futures", "stock"], help="市场类型（默认：futures）")
    p_factor_seeds.set_defaults(func=_cmd_factor_seeds)

    # factor stats
    p_factor_stats = factor_sub.add_parser("stats", help="因子家族分布统计")
    p_factor_stats.add_argument("--market", default=None, choices=["futures", "stock"], help="市场类型（可选）")
    p_factor_stats.add_argument("--min-sharpe", type=float, default=0.0, help="最低 Sharpe 阈值（默认 0.0）")
    p_factor_stats.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_factor_stats.set_defaults(func=_cmd_factor_stats)

    # factor lineage
    p_factor_lineage = factor_sub.add_parser("lineage", help="查询因子演化血缘")
    p_factor_lineage.add_argument("factor_id", help="因子 ID")
    p_factor_lineage.set_defaults(func=_cmd_factor_lineage)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 入口。"""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "version", False):
        return _cmd_version(args)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
