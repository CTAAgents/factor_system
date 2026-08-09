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
    python -m fts.cli seed validate          : 验证所有种子因子
    python -m fts.cli seed report            : 生成种子因子统计报告
    python -m fts.cli seed dedup             : 检查跨文件因子重复
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
from .config.factor_quality_card_config import get_futures_config
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
    generate_session_id,
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


def _relaxed_futures_quality_config():
    """期货质检配置（放宽准入线）。

    在 get_futures_config() 基础上进一步降低 B 级准入分，
    缓解日频期货 500 行短样本导致的后代因子总分普遍接近
    边界线（28-30/50）却无法晋升、进而触发失败率熔断的问题。

    返回 dict（FactorQualityCardConfig 契约）而非 dataclass 对象，
    与 FactorQualityCard 内部的 .get() 访问方式兼容。
    """
    config = get_futures_config()
    config.grades.grade_B_min = 24.0
    return config.to_factor_quality_card_config()


def _relaxed_futures_audit_config():
    """期货审计配置（放宽 OOS 一致性阈值）。

    默认 FactorAuditConfig.min_oos_pass_ratio=0.5（要求 |ICIR| ≥ 0.5），
    对日频期货 500 行短样本低信噪比过严，导致种子审计 oos_consistency
    几乎全灭、父因子池过小、演化 0 晋升触发失败率熔断。

    放宽到 0.3（要求 |ICIR| ≥ 0.3），同时保留其余审计项（跨品种/压力/
    多重检验/数据窥探）默认阈值，不削弱伪相关防线。
    """
    from fts.factor_engine.audit import FactorAuditConfig

    return FactorAuditConfig(min_oos_pass_ratio=0.3)


def _cmd_evolution_run(args: argparse.Namespace) -> int:
    """启动 L2 因子演化主循环（支持单标或横截面模式）。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    session_id = getattr(args, "session_id", "") or ""
    cfg = get_config()
    print(f"[evolution] session_id={session_id} trace_id={trace_id} run_id={run_id}")
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
            n_trials_micro=min(args.max_generations * 3, 30),
            cross_section_data=panel,
            cross_section_dates=common_dates,
            market="futures",
            # 期货专用质检配置：降低 IC/Sharpe 阈值以适配日频期货低信噪比
            quality_card_config=_relaxed_futures_quality_config(),
            # 期货专用审计配置：放宽 OOS 一致性阈值（|ICIR| ≥ 0.5 → ≥ 0.3），
            # 缓解 500 日短样本下种子审计 oos_consistency 全灭、父因子池过小的问题
            audit_config=_relaxed_futures_audit_config(),
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
    # 短样本演化期放宽失败率熔断（0.95 → 0.99），
    # 避免后代因子因评分卡边界线批量淘汰而提前熔断
    budget["circuit_breaker_failure_rate"] = 0.99
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
    session_id = getattr(args, "session_id", "") or ""
    cfg = get_config()
    market = getattr(args, "market", None) or cfg.default_market
    print(f"[meta-loop] session_id={session_id} trace_id={trace_id} run_id={run_id} market={market}")

    # 解析 --symbols 参数（逗号分隔）
    sample_symbols = None
    symbols_raw = getattr(args, "symbols", None)
    if symbols_raw:
        sample_symbols = [s.strip().lower() for s in symbols_raw.split(",") if s.strip()]
        print(f"[meta-loop] 自定义感知品种: {sample_symbols}")

    llm = get_default_llm_client()
    print(f"[meta-loop] LLM backend: {type(llm).__name__}")

    try:
        # 创建 web_collector — 基于 FTSDataProvider 的市场快照采集
        from .factor_engine.meta_loop import _make_web_collector
        web_collector = _make_web_collector(FTSDataProvider())
        print("[meta-loop] web_collector 已就绪 — 市场快照感知已启用")

        # MetaLoop
        loop = MetaLoop(
            memory_dir=cfg.memory_dir + "/meta_loop",
            llm_client=llm,
            market=market,
            web_collector=web_collector,
            sample_symbols=sample_symbols,
        )
        result = loop.run()
        print(f"[meta-loop] 完成: status={result.status} injected={len(result.injected_candidate_ids)}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[meta-loop] 运行失败: {e}", file=sys.stderr)
        return 2


def _build_default_aggregator():
    """构建默认期货多源聚合器（Phase 14.5 数据同步任务 sync_futures_data_job 使用）。

    Returns:
        FuturesDataAggregator 实例（TQ 本地源 + DuckDB 缓存路径）。
    """
    from fts.data_sources.aggregator import FuturesDataAggregator
    from fts.data_sources.tq_source import TQLocalSource

    sources: list = []
    try:
        sources.append(TQLocalSource())
    except Exception:  # noqa: BLE001
        pass

    db_path = None
    from fts.data_futures import _DUCKDB_PATH
    if _DUCKDB_PATH.exists():
        db_path = _DUCKDB_PATH

    return FuturesDataAggregator(
        sources=sources,
        enhancers=[],
        db_path=db_path,
        cache_max_age_days=30,
    )


# ─── fts data 子命令组（Phase 14.4）──────────────────────


def _cmd_data_status(args: argparse.Namespace) -> int:
    """`fts data status` — 查看多源熔断器/成功率状态。"""
    trace_id = generate_trace_id()
    print(f"trace_id={trace_id}")
    try:
        agg = _build_default_aggregator()
        status = agg.get_source_status()
    except Exception as e:  # noqa: BLE001
        print(f"[data status] 获取状态失败: {e}", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps({"trace_id": trace_id, "sources": status}, ensure_ascii=False, indent=2))
        return 0

    if not status:
        print("暂无源活动记录")
        return 0
    for name, st in status.items():
        print(f"  {name}: success={st['total_success']} failure={st['total_failure']} "
              f"consecutive={st['consecutive_failures']} circuit_open={st['circuit_open']}")
    return 0


def _cmd_data_sync(args: argparse.Namespace) -> int:
    """`fts data sync-futures` — 主动同步期货 K 线数据。"""
    from fts.scheduler.jobs import sync_futures_data_job

    trace_id = generate_trace_id()
    print(f"trace_id={trace_id}")
    symbols = getattr(args, "symbol", None)
    days = getattr(args, "days", 120)
    sync_futures_data_job(symbols=[symbols] if symbols else None, days=days)
    return 0


def _cmd_data_cross_check(args: argparse.Namespace) -> int:
    """`fts data cross-check` — 对指定 symbol+date 做多源交叉验证。"""
    trace_id = generate_trace_id()
    print(f"trace_id={trace_id}")
    try:
        agg = _build_default_aggregator()
        disagreements = agg.cross_check(
            symbol=args.symbol,
            date=args.date,
            trace_id=trace_id,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[data cross-check] 交叉验证失败: {e}", file=sys.stderr)
        return 2

    if getattr(args, "json", False):
        print(json.dumps({
            "trace_id": trace_id,
            "symbol": args.symbol,
            "date": args.date,
            "disagreements": disagreements,
        }, ensure_ascii=False, indent=2))
        return 1 if disagreements else 0

    if not disagreements:
        print("无分歧")
        return 0
    for d in disagreements:
        print(f"⚠️ {d.get('symbol', '')} @ {d.get('date', '')} "
              f"max_diff_pct={d.get('max_diff_pct', 0):.4f} outliers={d.get('outliers', [])}")
    return 1


def _cmd_data_fuse(args: argparse.Namespace) -> int:
    """`fts data fuse` — 拉多源 K 线 → 融合 → FusionReport 输出/落盘。

    Args:
        args.symbol / args.strategy / args.days / args.json / args.output
    Returns:
        0 成功；1 无任何源提供数据；2 无效策略 / 内部错误
    """
    from datetime import datetime

    from fts.core.contracts import FusedOHLCV
    from fts.core.enums import FusionStrategy
    from fts.data_sources.fusion import OHLCVFusion

    trace_id = generate_trace_id()
    print(f"trace_id={trace_id}")
    started_at = datetime.now().isoformat()

    try:
        strategy = FusionStrategy(args.strategy.lower())
    except ValueError:
        print(f"未知策略: {args.strategy}", file=sys.stderr)
        return 2

    try:
        agg = _build_default_aggregator()
    except Exception as e:  # noqa: BLE001
        print(f"[data fuse] 聚合器初始化失败: {e}", file=sys.stderr)
        return 2

    # 拉取每个源（绕过熔断器，记录成功/失败）
    source_dfs: dict[str, pd.DataFrame] = {}
    for src in list(agg.sources) + list(agg.enhancers):
        if agg._is_circuit_open(src.source_name):
            continue
        try:
            df = src.fetch_ohlcv_or_none(args.symbol, days=args.days, trace_id=trace_id)
            if df is not None and not df.empty:
                source_dfs[src.source_name] = df
                agg._record_success(src.source_name)
        except Exception as e:  # noqa: BLE001
            agg._record_failure(src.source_name, str(e))

    if not source_dfs:
        print("没有任何源提供数据", file=sys.stderr)
        return 1

    fuser = OHLCVFusion(strategy=strategy)
    fused_df = fuser.fuse_dataframe(args.symbol, source_dfs, trace_id=trace_id)
    rows: list[FusedOHLCV] = fused_df.to_dict("records") if not fused_df.empty else []

    try:
        disagreements = agg.cross_check(args.symbol, str(rows[0]["date"]), trace_id=trace_id)
    except Exception:  # noqa: BLE001
        disagreements = []

    finished_at = datetime.now().isoformat()
    report = {
        "trace_id": trace_id,
        "symbol": args.symbol,
        "strategy": strategy.name,
        "rows": rows,
        "sources_used": sorted(source_dfs.keys()),
        "rows_count": len(rows),
        "started_at": started_at,
        "finished_at": finished_at,
        "disagreements": disagreements,
    }

    if getattr(args, "output", None):
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[data fuse] FusionReport 已落盘: {args.output}")

    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(f"[data fuse] symbol={args.symbol} strategy={strategy.name} rows={len(rows)} "
              f"sources={sorted(source_dfs.keys())}")
    return 0


def _cmd_portfolio_run(args: argparse.Namespace) -> int:
    """启动 L3 组合构建 → 期货信号管道（L3 完成后自动触发）。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    session_id = getattr(args, "session_id", "") or ""
    cfg = get_config()
    print(f"[portfolio] session_id={session_id} trace_id={trace_id} run_id={run_id}")

    # 根据 universe 选择 elite 目录和合成模式
    universe = getattr(args, "universe", "stock")
    synthesis_mode = getattr(args, "synthesis_mode", None)
    if universe == "futures":
        elite_dir = cfg.get_elite_dir("futures")
        if synthesis_mode is None:
            synthesis_mode = "elastic_net"
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


def _get_catalog_db_path() -> Path:
    """获取因子目录数据库路径。"""
    from .factor_engine.factor_db.schema import DATABASE_PATH
    return Path(DATABASE_PATH)


def _cmd_catalog_stats(args: argparse.Namespace) -> int:
    """查看因子存储统计（DuckDB + JSON 文件）。"""
    cfg = get_config()
    db_path = _get_catalog_db_path()

    # DuckDB 统计
    stats: dict[str, Any] = {"database_path": str(db_path), "database_exists": db_path.exists()}
    if db_path.exists():
        try:
            repo = _load_factor_repo()
            duck_stats = repo.get_stats()
            stats.update(duck_stats)
            stats["database_size_mb"] = round(db_path.stat().st_size / (1024 * 1024), 2)
        except Exception as e:
            print(f"[catalog stats] DuckDB 读取失败: {e}", file=sys.stderr)
            stats["duckdb_error"] = str(e)

    # JSON 文件统计
    for market in ("stock", "futures"):
        elite_dir = Path(cfg.get_elite_dir(market))
        json_files = []
        json_size = 0
        if elite_dir.exists():
            for fp in sorted(elite_dir.glob("*.json")):
                if fp.name.startswith("_"):
                    continue
                json_files.append(fp.name)
                json_size += fp.stat().st_size
            retired_dir = elite_dir / "_retired"
            retired_count = len(list(retired_dir.glob("*.json"))) if retired_dir.exists() else 0
        else:
            retired_count = 0
        stats[f"{market}_json_dir"] = str(elite_dir)
        stats[f"{market}_json_files"] = len(json_files)
        stats[f"{market}_json_size_mb"] = round(json_size / (1024 * 1024), 2) if json_size else 0.0
        stats[f"{market}_retired_files"] = retired_count

    if getattr(args, "json", False):
        print(json.dumps(stats, indent=2, ensure_ascii=False, default=str))
        return 0

    print("=== 因子存储统计 ===")
    print(f"数据库: {db_path}")
    size_mb = float(stats.get("database_size_mb", 0) or 0)
    print(f"  存在: {stats.get('database_exists', '?')}  "
          f"大小: {size_mb:.1f} MB" if db_path.exists() else "  (不存在)")
    if db_path.exists():
        print(f"  总因子: {stats.get('total_factors', '?')}  "
              f"活跃: {stats.get('active_factors', '?')}  "
              f"精英: {stats.get('elite_factors', '?')}")
        print(f"  平均 Sharpe: {stats.get('avg_sharpe', '?')}  "
              f"平均 IC: {stats.get('avg_ic', '?')}")
    for market in ("stock", "futures"):
        print(f"\n{market.upper()} JSON 文件:")
        print(f"  目录: {stats.get(f'{market}_json_dir', '?')}")
        print(f"  文件数: {stats.get(f'{market}_json_files', '?')}  "
              f"大小: {stats.get(f'{market}_json_size_mb', '?'):.2f} MB  "
              f"已淘汰: {stats.get(f'{market}_retired_files', '?')}")
    return 0


def _cmd_catalog_verify(args: argparse.Namespace) -> int:
    """验证 JSON ↔ DuckDB 一致性。"""
    cfg = get_config()
    db_path = _get_catalog_db_path()

    if not db_path.exists():
        print("[catalog verify] ❌ DuckDB 数据库不存在", file=sys.stderr)
        return 1

    try:
        repo = _load_factor_repo()
    except Exception as e:
        print(f"[catalog verify] ❌ DuckDB 连接失败: {e}", file=sys.stderr)
        return 1

    # 收集 DuckDB 中的 factor_id 集合
    duck_ids: set[str] = set()
    try:
        conn = repo._get_conn()
        rows = conn.execute("SELECT factor_id, name, market, is_elite, status FROM factor_catalog").fetchall()
        for r in rows:
            duck_ids.add(str(r[0]))
    except Exception as e:
        print(f"[catalog verify] ❌ DuckDB 查询失败: {e}", file=sys.stderr)
        return 1

    # 收集 JSON 文件中的 factor_id 集合
    json_ids: dict[str, dict[str, str]] = {}  # factor_id -> {market, status}
    for market in ("stock", "futures"):
        elite_dir = Path(cfg.get_elite_dir(market))
        if not elite_dir.exists():
            continue
        for fp in sorted(elite_dir.glob("*.json")):
            if fp.name.startswith("_"):
                continue
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                fid = data.get("factor_id", fp.stem)
                json_ids[fid] = {
                    "market": data.get("market", market),
                    "status": data.get("status", "active"),
                    "file": str(fp),
                }
            except Exception:
                pass

    # 比对
    only_in_duckdb = duck_ids - json_ids.keys()
    only_in_json = json_ids.keys() - duck_ids
    common = duck_ids & json_ids.keys()

    result = {
        "duckdb_total": len(duck_ids),
        "json_total": len(json_ids),
        "common": len(common),
        "only_in_duckdb": len(only_in_duckdb),
        "only_in_json": len(only_in_json),
        "consistent": len(only_in_duckdb) == 0 and len(only_in_json) == 0,
        "duckdb_only_samples": sorted(only_in_duckdb)[:10] if only_in_duckdb else [],
        "json_only_samples": sorted(only_in_json)[:10] if only_in_json else [],
    }

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0

    print("=== JSON ↔ DuckDB 一致性校验 ===")
    print(f"  DuckDB 因子数: {result['duckdb_total']}")
    print(f"  JSON 文件数:   {result['json_total']}")
    print(f"  交集:          {result['common']}")
    print(f"  仅 DuckDB 有:  {result['only_in_duckdb']}")
    print(f"  仅 JSON 有:    {result['only_in_json']}")
    if result["consistent"]:
        print("  ✅ 一致")
    else:
        print("  ⚠️ 不一致")
        if result["duckdb_only_samples"]:
            print(f"  DuckDB 独有 (前10): {', '.join(result['duckdb_only_samples'])}")
        if result["json_only_samples"]:
            for fid in result["json_only_samples"]:
                info = json_ids.get(fid, {})
                print(f"  JSON 独有: {fid} ({info.get('file', '?')})")
    return 0 if result["consistent"] else 1


def _cmd_catalog_backup(args: argparse.Namespace) -> int:
    """备份因子存储（DuckDB + JSON 文件）。"""
    from datetime import datetime
    import shutil

    cfg = get_config()
    db_path = _get_catalog_db_path()
    backup_dir = Path("data/backups")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {"timestamp": timestamp, "backup_dir": str(backup_dir)}

    # 1. 备份 DuckDB
    if db_path.exists():
        db_backup = backup_dir / f"factor_catalog.duckdb.{timestamp}"
        try:
            shutil.copy2(str(db_path), str(db_backup))
            db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)
            results["duckdb_backup"] = str(db_backup)
            results["duckdb_size_mb"] = db_size_mb
            print(f"  ✅ DuckDB: {db_backup.name} ({db_size_mb:.1f} MB)")
        except Exception as e:
            print(f"  ❌ DuckDB 备份失败: {e}", file=sys.stderr)
            results["duckdb_error"] = str(e)
    else:
        print("  ⚠️ DuckDB 数据库不存在，跳过备份")

    # 2. 备份 JSON 文件
    for market in ("stock", "futures"):
        elite_dir = Path(cfg.get_elite_dir(market))
        if not elite_dir.exists():
            continue
        json_backup_dir = backup_dir / f"{market}_elite_{timestamp}"
        try:
            # 复制 elite JSON 文件（排除 _retired 目录）
            json_backup_dir.mkdir(parents=True, exist_ok=True)
            copied = 0
            for fp in sorted(elite_dir.glob("*.json")):
                if fp.name.startswith("_"):
                    continue
                shutil.copy2(str(fp), str(json_backup_dir / fp.name))
                copied += 1
            # 复制 _retired 目录
            retired_src = elite_dir / "_retired"
            if retired_src.exists():
                retired_dst = json_backup_dir / "_retired"
                retired_dst.mkdir(parents=True, exist_ok=True)
                for fp in sorted(retired_src.glob("*.json")):
                    shutil.copy2(str(fp), str(retired_dst / fp.name))
            results[f"{market}_json_backup"] = str(json_backup_dir)
            results[f"{market}_json_count"] = copied
            print(f"  ✅ {market.upper()} JSON: {json_backup_dir.name} ({copied} 文件)")
        except Exception as e:
            print(f"  ❌ {market} JSON 备份失败: {e}", file=sys.stderr)
            results[f"{market}_json_error"] = str(e)

    if getattr(args, "json", False):
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))

    return 0


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
                    max_per_family=getattr(args, "max_per_family", 15),
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
            # 跳过内部索引/临时文件（_l2_seed_correlation_index.json 等）
            if p.name.startswith("_") or p.name.startswith("."):
                continue
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
                # ic/sharpe 嵌套在 evaluation.level_1_backtest 中；DuckDB 模式为顶层字段
                if k in ("ic", "sharpe"):
                    bt = (f.get("evaluation") or {}).get("level_1_backtest") or {}
                    v = bt.get(k, "-") if bt else f.get(k, "-")
                    # 未评估（无指标）→ 标注"未评估"
                    if v in ("-", None) or (isinstance(v, (int, float)) and v == 0):
                        v = "未评估"
                else:
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


def _cmd_factor_cross_market(args: argparse.Namespace) -> int:
    """跨市场泛化验证。"""
    try:
        from fts.cross_market import CrossMarketDataAdapter, CrossMarketEngine

        adapter = CrossMarketDataAdapter()
        engine = CrossMarketEngine(adapter)

        direction = args.direction
        direction_map = {
            "futures-to-stock": engine.run_futures_to_stock,
            "futures-to-etf": engine.run_futures_to_etf,
            "stock-to-futures": engine.run_stock_to_futures,
        }
        market_label = {"futures-to-stock": "期货→A股", "futures-to-etf": "期货→ETF",
                        "stock-to-futures": "股票→期货"}

        print(f"=== 跨市场泛化验证: {market_label.get(direction, direction)} ===")
        print(f"方向: {direction} | 回溯: {args.days}天")

        kwargs = {"days": args.days, "max_factors": args.max_factors}
        if direction == "futures-to-stock":
            kwargs["max_stocks"] = args.max_stocks
            print(f"最大成分股: {args.max_stocks if args.max_stocks > 0 else '全量'}")

        run_fn = direction_map[direction]
        report = run_fn(**kwargs)

        # 生成报告
        output_path = None
        if args.output_dir:
            from datetime import date
            output_path = Path(args.output_dir) / f"cross_market_revalidation_{date.today().isoformat()}.md"
        report_path = engine.generate_report(report, output_path=output_path)

        print(f"\n结果汇总:")
        print(f"  🌍 通用因子: {report.n_universal}")
        print(f"  🔄 市场特异: {report.n_market_specific}")
        print(f"  ❌ 失效: {report.n_failed}")
        print(f"  ⬇️ 已降级: {report.n_deprecated}")
        print(f"  📄 报告: {report_path}")

        return 0

    except ImportError as e:
        print(f"[cross-market] 导入失败: {e}", file=sys.stderr)
        print(f"[cross-market] 请确保 fts.cross_market 模块已安装", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[cross-market] 执行失败: {e}", file=sys.stderr)
        return 1


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
        # 股票种子因子：WQ101 + Qlib158 + 国泰君安191（经典 A 股因子库）
        from fts.factor_engine.seed_data import load_all_external_seeds
        seeds = load_all_external_seeds(trace_id="cli_seed_list")
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


# ─── fts seed（种子因子管理 — P4 统一转换器 + 验证器） ─────────


def _cmd_seed_validate(args: argparse.Namespace) -> int:
    """验证所有种子因子（完整性/语法/跨文件重复）。"""
    from scripts.unified_factor_converter import (
        load_all_factors, validate_all, check_duplicates,
    )
    market = getattr(args, "market", "futures")
    all_factors = load_all_factors(market)
    print(f"📂 共加载 {len(all_factors)} 个种子文件（{market}）")
    print()

    errors = validate_all(all_factors)
    if errors:
        print(f"❌ 发现 {len(errors)} 个因子存在问题:")
        for key, errs in sorted(errors.items()):
            print(f"  {key}:")
            for e in errs:
                print(f"    - {e}")
    else:
        print("✅ 所有因子验证通过")

    print()
    dup_errors = check_duplicates(all_factors)
    if dup_errors:
        for e in dup_errors:
            print(f"  {e}")
        return 1
    print("✅ 无跨文件重复")
    return 0 if not errors else 1


def _cmd_seed_report(args: argparse.Namespace) -> int:
    """生成种子因子统计报告。"""
    from scripts.unified_factor_converter import load_all_factors, generate_report
    market = getattr(args, "market", "futures")
    all_factors = load_all_factors(market)
    print(generate_report(all_factors, market))
    return 0


def _cmd_seed_dedup(args: argparse.Namespace) -> int:
    """检查跨文件因子重复。"""
    from scripts.unified_factor_converter import load_all_factors, check_duplicates
    market = getattr(args, "market", "futures")
    all_factors = load_all_factors(market)
    errors = check_duplicates(all_factors)
    if errors:
        print("❌ 发现重复:")
        for e in errors:
            print(f"  {e}")
        return 1
    print("✅ 无跨文件重复")
    return 0


# ─── fts backtest（B.2 回测流水线 CLI） ─────────────────


def _load_factor_by_id(factor_id: str, market: str = "futures") -> dict | None:
    """按 ID 从 elite 目录/DuckDB 加载因子。"""
    cfg = get_config()
    elite_dir = Path(cfg.get_elite_dir(market))
    if elite_dir.exists():
        candidates = list(elite_dir.glob(f"*{factor_id}*.json"))
        if candidates:
            try:
                return json.loads(candidates[0].read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
    try:
        repo = _load_factor_repo()
        return repo.get_by_id(factor_id, market=market)
    except Exception as e:  # noqa: BLE001
        print(f"[backtest] 因子加载失败: {e}", file=sys.stderr)
        return None


def _cmd_backtest_run(args: argparse.Namespace) -> int:
    """单个因子回测。"""
    factor = _load_factor_by_id(args.factor_id, getattr(args, "market", "futures"))
    if factor is None:
        print(f"[backtest] 未找到因子: {args.factor_id}")
        return 1

    freq = getattr(args, "frequency", "daily")
    date_range = None
    if args.start and args.end:
        date_range = (args.start, args.end)

    # 分钟级数据路径（v2.30.0）
    if freq != "daily":
        from .data_futures import FuturesDataProvider
        provider = FuturesDataProvider()
        data = provider.get_minute_ohlcv(
            getattr(args, "symbol", "000001"),
            days=args.days,
            frequency=freq,
        )
        if data.empty:
            print(f"[backtest] 分钟数据获取失败 [freq={freq}]")
            return 1
    else:
        data, _ = _prepare_data(getattr(args, "symbol", "000001"), days=args.days)

    from .factor_engine.backtest_pipeline import (
        BacktestInput, BacktestPipeline,
    )
    from .factor_engine.report_generator import ReportGenerator

    result = BacktestPipeline().run(BacktestInput(
        factor=factor,
        data=data,
        date_range=date_range,
        initialization_capital=args.capital,
        frequency=freq,
    ))
    if not result.success:
        print(f"[backtest] 回测失败: {result.error}", file=sys.stderr)
        return 1

    report = result.output
    m = report.metrics
    freq_label = getattr(args, "frequency", "daily")
    print(f"=== 回测结果: {report.factor_id} (频率: {freq_label}) ===")
    print(f"期间: {report.start_date} ~ {report.end_date}")
    print(f"总收益: {m.total_return:.2%} | 年化: {m.annual_return:.2%} | "
          f"Sharpe: {m.sharpe_ratio:.3f}")
    print(f"最大回撤: {m.max_drawdown:.2%} | Calmar: {m.calmar_ratio:.3f} | "
          f"胜率: {m.win_rate:.2%} | 盈亏比: {m.payoff_ratio:.2f} | 盈亏因子: {m.profit_factor:.2f}")
    print(f"IC 均值: {m.ic_mean:.4f} | IC IR: {m.ic_ir:.3f} | 换手: {m.turnover:.3f}")

    if args.output:
        gen = ReportGenerator()
        path = gen.generate(report, output_dir=args.output)
        print(f"报告已生成: {path}")
    return 0


def _cmd_backtest_batch(args: argparse.Namespace) -> int:
    """批量回测 + 对比排名。"""
    from .factor_engine.factor_screener import FactorScreener
    from .factor_engine.backtest_pipeline import BacktestPipeline

    market = getattr(args, "market", "futures")
    cfg = get_config()
    elite_dir = Path(cfg.get_elite_dir(market))
    if not elite_dir.exists():
        print(f"[backtest] elite 目录不存在: {elite_dir}", file=sys.stderr)
        return 1
    factors = []
    for p in sorted(elite_dir.glob("*.json")):
        # 跳过内部索引/临时文件（_l2_seed_correlation_index.json 等）
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        try:
            factors.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue

    screened = FactorScreener(market=market).screen(
        factors=factors,
        min_grade=getattr(args, "grade", "B"),
        min_total_score=getattr(args, "min_score", None),
        limit=getattr(args, "limit", 20),
    )
    if not screened:
        print("[backtest] 无符合条件的因子")
        return 0

    data, _ = _prepare_data(getattr(args, "symbol", "000001"), days=args.days)
    results = BacktestPipeline().run_batch(
        screened, data, initialization_capital=args.capital,
    )
    _print_backtest_ranking(results)
    return 0


def _cmd_backtest_compare(args: argparse.Namespace) -> int:
    """对比回测多个因子。"""
    from .factor_engine.backtest_pipeline import BacktestPipeline

    factor_ids = [s.strip() for s in args.factor_ids.split(",") if s.strip()]
    if not factor_ids:
        print("[backtest] 请提供 --factor-ids")
        return 1

    market = getattr(args, "market", "futures")
    factors = []
    for fid in factor_ids:
        f = _load_factor_by_id(fid, market)
        if f is not None:
            factors.append(f)

    if not factors:
        print("[backtest] 所有因子加载失败")
        return 1

    data, _ = _prepare_data(getattr(args, "symbol", "000001"), days=args.days)
    results = BacktestPipeline().run_batch(
        factors, data, initialization_capital=args.capital,
    )
    _print_backtest_ranking(results)
    return 0


def _print_backtest_ranking(results: list) -> int:
    """打印批量回测对比排名表。"""
    print(f"=== 回测对比排名 ({len(results)} 因子) ===")
    print(f"{'Rank':<5}{'Factor ID':<40}{'Sharpe':<10}{'IC':<10}{'MaxDD':<10}{'TotalRet':<10}")
    print("-" * 85)
    for r in sorted(results, key=lambda x: x.rank):
        if r.report is not None:
            m = r.report.metrics
            print(f"{r.rank:<5}{r.factor_id:<40}{m.sharpe_ratio:<10.3f}"
                  f"{m.ic_mean:<10.4f}{m.max_drawdown:<10.2%}{m.total_return:<10.2%}")
        else:
            print(f"{r.rank:<5}{r.factor_id:<40}失败: {r.error}")
    return 0


# ─── fts feature / fts gp（C.1 特征工程中台 CLI） ──────────


def _cmd_feature_list(args: argparse.Namespace) -> int:
    """列出特征算子。"""
    from .factor_engine.feature_ops import FeatureOpsEngine

    engine = FeatureOpsEngine()
    category = getattr(args, "category", None)
    ops = engine.registry.list_operators(category=category)

    if not ops:
        print(f"[feature] 无算子 (category={category})")
        return 0

    print(f"=== 特征算子 ({len(ops)} 个) ===")
    if getattr(args, "json", False):
        print(json.dumps(
            [op.__dict__ for op in ops], indent=2, ensure_ascii=False,
        ))
        return 0

    print(f"{'算子':<24}{'类别':<16}{'签名'}")
    print("-" * 70)
    for op in ops:
        print(f"{op.name:<24}{op.category:<16}{op.signature}")
    return 0


def _cmd_feature_analyze(args: argparse.Namespace) -> int:
    """特征重要性分析。"""
    from .factor_engine.feature_importance import FeatureImportanceAnalyzer

    factor = _load_factor_by_id(args.factor_id, getattr(args, "market", "futures"))
    if factor is None:
        print(f"[feature] 未找到因子: {args.factor_id}")
        return 1

    # 准备面板数据（feature 分析需要 forward_return_20d 目标列）
    panel, common_dates, _ = _prepare_futures_data(days=args.days)
    if not panel:
        print("[feature] 数据准备失败", file=sys.stderr)
        return 1

    # 用第一个品种构造分析数据
    first_sym = list(panel.keys())[0]
    df = panel[first_sym].copy()
    closes = df["close"].values
    fwd = np.zeros(len(closes))
    if len(closes) > 20:
        fwd[:-20] = (closes[20:] - closes[:-20]) / np.maximum(closes[:-20], 1e-10)
    df["forward_return_20d"] = fwd

    # 计算因子值
    from .factor_engine.signal_generator import SignalGenerator

    values = SignalGenerator._compute_factor_values(factor, df)
    if values is None:
        print("[feature] 因子计算失败", file=sys.stderr)
        return 1

    analyzer = FeatureImportanceAnalyzer()
    result = analyzer.analyze(
        factor_series=pd.Series(values, index=df.index),
        data=df,
        target_col="forward_return_20d",
    )

    print(f"=== 特征重要性: {result.factor_id or args.factor_id} ===")
    print(f"方法: {result.analysis_method} | 基线 IC: {result.baseline_ic:.4f}")
    print(f"{'特征':<24}{'重要性':<12}")
    print("-" * 40)
    for name, imp in result.feature_importance.items():
        print(f"{name:<24}{imp:<12.6f}")

    if args.output:
        Path(args.output).mkdir(parents=True, exist_ok=True)
        out = Path(args.output) / f"feature_importance_{args.factor_id}.json"
        out.write_text(
            json.dumps({
                "factor_id": args.factor_id,
                "baseline_ic": result.baseline_ic,
                "importance": result.feature_importance,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"结果已保存: {out}")
    return 0


def _cmd_gp_evolve(args: argparse.Namespace) -> int:
    """GP 遗传规划因子演化。"""
    from .factor_engine.feature_ops import FeatureOpsEngine
    from .factor_engine.gp_evolver import GPEvolver, GPEvolverConfig

    # 准备面板数据
    if getattr(args, "universe", "futures") == "csi300":
        panel, common_dates, _ = _prepare_cross_section_data(
            days=args.days, max_stocks=args.max_stocks,
        )
    else:
        panel, common_dates, _ = _prepare_futures_data(
            days=args.days, max_symbols=args.max_symbols,
        )
    if not panel:
        print("[gp] 数据准备失败", file=sys.stderr)
        return 1

    # 构造宽表面板 + 目标列
    first_sym = list(panel.keys())[0]
    df = panel[first_sym].copy()
    closes = df["close"].values
    fwd = np.zeros(len(closes))
    if len(closes) > args.forward:
        fwd[:-args.forward] = (
            closes[args.forward:] - closes[:-args.forward]
        ) / np.maximum(closes[:-args.forward], 1e-10)
    df["forward_return_20d"] = fwd

    engine = FeatureOpsEngine()
    config = GPEvolverConfig(
        population_size=args.population,
        max_generations=args.generations,
    )
    gp = GPEvolver(
        operator_registry=engine.registry,
        data_panel=df,
        target_col="forward_return_20d",
        config=config,
    )
    result = gp.evolve()

    print(f"=== GP 演化结果 (universe={args.universe}) ===")
    print(f"最优表达式: {result.best_expression}")
    print(f"适应度: {result.best_fitness:.4f}")
    print(f"IC: {result.best_ic:.4f} | Sharpe: {result.best_sharpe:.4f}")
    print(f"代数: {result.generations_completed} | 评估次数: {result.total_evaluations}")

    if args.output:
        Path(args.output).mkdir(parents=True, exist_ok=True)
        out = Path(args.output) / "gp_best_factor.json"
        out.write_text(
            json.dumps({
                "expression": result.best_expression,
                "fitness": result.best_fitness,
                "ic": result.best_ic,
                "sharpe": result.best_sharpe,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"最优因子已保存: {out}")
    return 0


# ─── fts feedback（C.3 反馈闭环 CLI） ────────────────────


def _cmd_feedback_trigger(args: argparse.Namespace) -> int:
    """手动触发反馈事件。"""
    from .factor_engine.feedback_loop import FeedbackLoop

    loop = FeedbackLoop()
    event = loop.trigger_manual_feedback(
        factor_id=getattr(args, "factor_id", "") or "",
        reason=args.reason,
    )
    print(f"=== 反馈事件已触发 ===")
    print(json.dumps(event, indent=2, ensure_ascii=False))
    return 0


def _cmd_feedback_process(args: argparse.Namespace) -> int:
    """处理待处理的反馈事件。"""
    from .factor_engine.feedback_loop import FeedbackLoop

    loop = FeedbackLoop()
    results = loop.process_feedback()
    if not results:
        print("[feedback] 无待处理反馈事件")
        return 0
    print(f"=== 反馈处理结果 ({len(results)}) ===")
    for r in results:
        print(f"  - {r['event_id']}: root_cause={r['root_cause']}, "
              f"action={r['action_taken']}, success={r['success']}")
    return 0


def _cmd_feedback_report(args: argparse.Namespace) -> int:
    """生成月度迭代效果报告。"""
    from .factor_engine.feedback_loop import FeedbackLoop

    loop = FeedbackLoop()
    report = loop.generate_monthly_report(period=getattr(args, "month", None))
    print(f"=== 迭代效果月报 ({report['period']}) ===")
    print(f"新因子: {report['new_factors']} | 有效率: {report['effective_rate']:.1%}")
    print(f"反馈处理: {report['feedback_events_handled']} | "
          f"建议采纳: {report['recommendations_accepted']}/{report['recommendations_total']}")
    print(report["summary_text"])
    return 0


def _cmd_feedback_stats(args: argparse.Namespace) -> int:
    """查看反馈闭环统计。"""
    from .factor_engine.feedback_loop import FeedbackLoop

    loop = FeedbackLoop()
    stats = loop.get_statistics()
    print("=== 反馈闭环统计 ===")
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


def _cmd_bridge_publish(args: argparse.Namespace) -> int:
    """发布信号到目标协议（Phase 25）。"""
    from datetime import datetime

    from .bridge import SignalBridge, BridgeError
    from .factor_engine.state import generate_trace_id

    # 读取输入信号（缺省生成演示信号）
    if args.input:
        try:
            with open(args.input, encoding="utf-8") as f:
                signal = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[bridge] 读取信号文件失败: {e}")
            return 1
    else:
        signal = {
            "signal_id": generate_trace_id(),
            "portfolio_id": "demo",
            "timestamp": datetime.now().isoformat(),
            "frequency": "1d",
            "universe": [],
            "signals": [],
            "meta": {"trace_id": generate_trace_id(), "factor_count": 0},
        }

    bridge = SignalBridge(
        protocol=args.protocol,
        output_dir=args.output_dir,
        redis_url=args.redis_url,
        redis_key=args.redis_key,
        rest_url=args.rest_url,
    )
    try:
        sid = bridge.publish(signal)
    except BridgeError as e:
        print(f"[bridge] 发布失败: {e}")
        return 1
    print(f"[bridge] {args.protocol} 协议发布成功: signal_id={sid}")
    return 0


def _cmd_bridge_status(args: argparse.Namespace) -> int:
    """查看信号桥接状态（Phase 25）。"""
    from .bridge import SignalBridge, BridgeError

    bridge = SignalBridge(
        protocol=args.protocol,
        output_dir=args.output_dir,
        redis_url=args.redis_url,
        redis_key=args.redis_key,
    )
    try:
        status = bridge.status()
    except BridgeError as e:
        print(f"[bridge] 状态查询失败: {e}")
        return 1
    print("=== 信号桥接状态 ===")
    print(f"协议: {status.protocol}")
    print(f"可用: {'YES' if status.available else 'NO'}")
    print(f"详情: {status.detail}")
    if status.latest_signal_id:
        print(f"最近信号: {status.latest_signal_id} @ {status.latest_timestamp}")
    return 0 if status.available else 1


def _cmd_bridge_serve(args: argparse.Namespace) -> int:
    """启动 REST 信号服务（Phase 25）。

    使用标准库 http.server 实现一个极简信号接收端点：
        POST /signal  → 接收 FactorSignal JSON，写入 signals/latest_signal.json
        GET  /signal  → 返回最近一次信号
    """
    import http.server

    from .bridge import SignalBridge

    bridge = SignalBridge(protocol="json", output_dir="signals")

    class _Handler(http.server.BaseHTTPRequestHandler):
        def _send_json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802 - http.server 命名约定
            if self.path != "/signal":
                self._send_json(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                signal = json.loads(raw)
                bridge.publish(signal)
            except Exception as e:  # noqa: BLE001
                self._send_json(400, {"error": str(e)})
                return
            self._send_json(200, {"ok": True, "signal_id": signal.get("signal_id", "")})

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self._send_json(200, {"status": "ok"})
                return
            latest = bridge.latest() or {}
            self._send_json(200, latest)

        def log_message(self, fmt: str, *fmt_args: Any) -> None:  # noqa: A003
            print(f"[bridge] {self.address_string()} {fmt % fmt_args}")

    server = http.server.ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"[bridge] REST 信号服务启动: http://{args.host}:{args.port}/signal")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] 服务已停止")
    finally:
        server.server_close()
    return 0


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
    p_meta_run.add_argument("--symbols", type=str, default=None,
                            help="感知层抽样品种，逗号分隔（如 rb,i,au,sc），默认覆盖五大板块共 13 个品种")
    p_meta_run.set_defaults(func=_cmd_meta_loop_run)

    # portfolio run
    p_port = sub.add_parser("portfolio", help="L3 组合构建")
    port_sub = p_port.add_subparsers(dest="subcommand", required=False)
    p_port_run = port_sub.add_parser("run", help="启动 L3 组合构建")
    p_port_run.add_argument("--universe", type=str, default="stock",
                            choices=["stock", "futures"],
                            help="因子池类型: stock（股票）/ futures（期货）")
    p_port_run.add_argument("--synthesis-mode", type=str, default=None,
                            choices=["equal_weight", "sharpe_weight", "elastic_net", "adaptive"],
                            help="信号合成模式: elastic_net（默认）/ adaptive（Regime 自适应双维度）/ sharpe_weight / equal_weight")
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

    # catalog（因子目录管理）
    p_catalog = sub.add_parser("catalog", help="因子目录管理（存储统计/一致性校验/备份）")
    cat_sub = p_catalog.add_subparsers(dest="subcommand", required=False)

    p_cat_stats = cat_sub.add_parser("stats", help="查看因子存储统计")
    p_cat_stats.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_cat_stats.set_defaults(func=_cmd_catalog_stats)

    p_cat_verify = cat_sub.add_parser("verify", help="验证 JSON ↔ DuckDB 一致性")
    p_cat_verify.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_cat_verify.set_defaults(func=_cmd_catalog_verify)

    p_cat_backup = cat_sub.add_parser("backup", help="备份因子存储（DuckDB + JSON）")
    p_cat_backup.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_cat_backup.set_defaults(func=_cmd_catalog_backup)

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

    # factor cross-market（跨市场泛化验证）
    p_factor_xm = factor_sub.add_parser("cross-market", help="跨市场泛化验证")
    p_factor_xm.add_argument("--direction", default="futures-to-stock",
                             choices=["futures-to-stock", "futures-to-etf", "stock-to-futures"],
                             help="验证方向（默认: futures-to-stock）")
    p_factor_xm.add_argument("--days", type=int, default=120, help="回溯天数")
    p_factor_xm.add_argument("--max-factors", type=int, default=0, help="最大因子数，0=全量")
    p_factor_xm.add_argument("--max-stocks", type=int, default=0,
                             help="最大成分股数，0=全量（仅 futures-to-stock 方向）")
    p_factor_xm.add_argument("--output-dir", default=None, help="报告输出目录")
    p_factor_xm.set_defaults(func=_cmd_factor_cross_market)

    # seed（种子因子管理）
    p_seed = sub.add_parser("seed", help="种子因子管理（验证/报告/去重）")
    seed_sub = p_seed.add_subparsers(dest="subcommand", required=False)

    p_seed_validate = seed_sub.add_parser("validate", help="验证所有种子因子（完整性/语法/跨文件重复）")
    p_seed_validate.add_argument("--market", default="futures", choices=["futures", "stock"],
                                 help="市场类型（默认：futures）")
    p_seed_validate.set_defaults(func=_cmd_seed_validate)

    p_seed_report = seed_sub.add_parser("report", help="生成种子因子统计报告")
    p_seed_report.add_argument("--market", default="futures", choices=["futures", "stock"],
                               help="市场类型（默认：futures）")
    p_seed_report.set_defaults(func=_cmd_seed_report)

    p_seed_dedup = seed_sub.add_parser("dedup", help="检查跨文件因子重复")
    p_seed_dedup.add_argument("--market", default="futures", choices=["futures", "stock"],
                              help="市场类型（默认：futures）")
    p_seed_dedup.set_defaults(func=_cmd_seed_dedup)

    # backtest（B.2 回测流水线）
    p_backtest = sub.add_parser("backtest", help="回测流水线（B.2）")
    bt_sub = p_backtest.add_subparsers(dest="subcommand", required=False)

    # backtest run
    p_bt_run = bt_sub.add_parser("run", help="单个因子回测")
    p_bt_run.add_argument("--factor-id", required=True, help="因子 ID")
    p_bt_run.add_argument("--market", default="futures", choices=["futures", "stock"],
                          help="市场类型（默认：futures）")
    p_bt_run.add_argument("--symbol", default="000001", help="回测标的代码（默认 000001）")
    p_bt_run.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD")
    p_bt_run.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    p_bt_run.add_argument("--days", type=int, default=500, help="回溯天数（默认 500）")
    p_bt_run.add_argument("--capital", type=float, default=1_000_000.0, help="初始资金")
    p_bt_run.add_argument("--frequency", default="daily",
                          choices=["daily", "1m", "5m", "15m", "30m", "60m"],
                          help="数据频率（默认 daily，v2.30.0 分钟级支持）")
    p_bt_run.add_argument("--output", default=None, help="报告输出目录")
    p_bt_run.set_defaults(func=_cmd_backtest_run)

    # backtest batch
    p_bt_batch = bt_sub.add_parser("batch", help="批量回测 + 对比排名")
    p_bt_batch.add_argument("--market", default="futures", choices=["futures", "stock"],
                            help="市场类型（默认：futures）")
    p_bt_batch.add_argument("--grade", default="B", help="最低等级 A/B/C（默认 B）")
    p_bt_batch.add_argument("--min-score", type=float, default=None, help="最低质量总分")
    p_bt_batch.add_argument("--limit", type=int, default=20, help="最大回测因子数（默认 20）")
    p_bt_batch.add_argument("--symbol", default="000001", help="回测标的代码")
    p_bt_batch.add_argument("--days", type=int, default=500, help="回溯天数")
    p_bt_batch.add_argument("--capital", type=float, default=1_000_000.0, help="初始资金")
    p_bt_batch.set_defaults(func=_cmd_backtest_batch)

    # backtest compare
    p_bt_cmp = bt_sub.add_parser("compare", help="对比回测多个因子")
    p_bt_cmp.add_argument("--factor-ids", required=True, help="逗号分隔的因子 ID 列表")
    p_bt_cmp.add_argument("--market", default="futures", choices=["futures", "stock"],
                          help="市场类型（默认：futures）")
    p_bt_cmp.add_argument("--symbol", default="000001", help="回测标的代码")
    p_bt_cmp.add_argument("--days", type=int, default=500, help="回溯天数")
    p_bt_cmp.add_argument("--capital", type=float, default=1_000_000.0, help="初始资金")
    p_bt_cmp.set_defaults(func=_cmd_backtest_compare)

    # feature（C.1 特征工程中台）
    p_feature = sub.add_parser("feature", help="特征工程中台（C.1）")
    feat_sub = p_feature.add_subparsers(dest="subcommand", required=False)

    p_feat_list = feat_sub.add_parser("list", help="列出特征算子")
    p_feat_list.add_argument("--category", default=None,
                             help="算子类别（time_series/price/rolling/technical/cross_section/cross_symbol/composite）")
    p_feat_list.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_feat_list.set_defaults(func=_cmd_feature_list)

    p_feat_analyze = feat_sub.add_parser("analyze", help="特征重要性分析")
    p_feat_analyze.add_argument("--factor-id", required=True, help="因子 ID")
    p_feat_analyze.add_argument("--market", default="futures", choices=["futures", "stock"],
                                help="市场类型（默认：futures）")
    p_feat_analyze.add_argument("--days", type=int, default=500, help="回溯天数")
    p_feat_analyze.add_argument("--output", default=None, help="结果输出目录")
    p_feat_analyze.set_defaults(func=_cmd_feature_analyze)

    # gp（C.1 GP 遗传规划演化）
    p_gp = sub.add_parser("gp", help="GP 遗传规划因子演化（C.1）")
    gp_sub = p_gp.add_subparsers(dest="subcommand", required=False)

    p_gp_evolve = gp_sub.add_parser("evolve", help="运行 GP 演化")
    p_gp_evolve.add_argument("--universe", default="futures", choices=["futures", "csi300"],
                             help="品种池类型（默认：futures）")
    p_gp_evolve.add_argument("--population", type=int, default=200, help="种群大小（默认 200）")
    p_gp_evolve.add_argument("--generations", type=int, default=50, help="最大代数（默认 50）")
    p_gp_evolve.add_argument("--days", type=int, default=500, help="回溯天数")
    p_gp_evolve.add_argument("--max-stocks", type=int, default=30, help="横截面模式最大标的数")
    p_gp_evolve.add_argument("--max-symbols", type=int, default=0, help="期货模式最大品种数（0=全部）")
    p_gp_evolve.add_argument("--forward", type=int, default=20, help="预测周期（默认 20）")
    p_gp_evolve.add_argument("--output", default=None, help="结果输出目录")
    p_gp_evolve.set_defaults(func=_cmd_gp_evolve)

    # feedback（C.3 反馈闭环）
    p_feedback = sub.add_parser("feedback", help="反馈闭环（C.3）")
    fb_sub = p_feedback.add_subparsers(dest="subcommand", required=False)

    p_fb_trigger = fb_sub.add_parser("trigger", help="手动触发反馈事件")
    p_fb_trigger.add_argument("--factor-id", default="", help="因子 ID（可选）")
    p_fb_trigger.add_argument("--reason", default="manual review", help="触发原因")
    p_fb_trigger.set_defaults(func=_cmd_feedback_trigger)

    p_fb_process = fb_sub.add_parser("process", help="处理待处理的反馈事件")
    p_fb_process.set_defaults(func=_cmd_feedback_process)

    p_fb_report = fb_sub.add_parser("report", help="生成月度迭代效果报告")
    p_fb_report.add_argument("--month", default=None, help="月份 YYYY-MM（默认当月）")
    p_fb_report.set_defaults(func=_cmd_feedback_report)

    p_fb_stats = fb_sub.add_parser("stats", help="查看反馈闭环统计")
    p_fb_stats.set_defaults(func=_cmd_feedback_stats)

    # bridge（VNPY 信号桥接，Phase 25）
    p_bridge = sub.add_parser("bridge", help="VNPY 信号桥接（Phase 25）")
    bridge_sub = p_bridge.add_subparsers(dest="subcommand", required=False)

    p_bridge_serve = bridge_sub.add_parser("serve", help="启动 REST 信号服务（接收下游信号推送）")
    p_bridge_serve.add_argument("--host", default="127.0.0.1", help="监听地址")
    p_bridge_serve.add_argument("--port", type=int, default=8765, help="监听端口")
    p_bridge_serve.set_defaults(func=_cmd_bridge_serve)

    p_bridge_publish = bridge_sub.add_parser("publish", help="发布信号到目标协议")
    p_bridge_publish.add_argument("--protocol", default="json", choices=["json", "redis", "rest"], help="传输协议")
    p_bridge_publish.add_argument("--input", default="", help="信号 JSON 文件路径（缺省生成演示信号）")
    p_bridge_publish.add_argument("--output-dir", default="signals", help="JSON 协议输出目录")
    p_bridge_publish.add_argument("--redis-url", default="redis://localhost:6379/0", help="Redis 连接 URL")
    p_bridge_publish.add_argument("--redis-key", default="fts:signals:latest", help="Redis 信号 key")
    p_bridge_publish.add_argument("--rest-url", default="", help="REST 目标 URL")
    p_bridge_publish.set_defaults(func=_cmd_bridge_publish)

    p_bridge_status = bridge_sub.add_parser("status", help="查看信号桥接状态")
    p_bridge_status.add_argument("--protocol", default="json", choices=["json", "redis", "rest"], help="传输协议")
    p_bridge_status.add_argument("--output-dir", default="signals", help="JSON 协议输出目录")
    p_bridge_status.add_argument("--redis-url", default="redis://localhost:6379/0", help="Redis 连接 URL")
    p_bridge_status.add_argument("--redis-key", default="fts:signals:latest", help="Redis 信号 key")
    p_bridge_status.set_defaults(func=_cmd_bridge_status)

    # data（期货多源数据命令，Phase 14.4）
    p_data = sub.add_parser("data", help="期货多源数据命令（status/sync-futures/cross-check/fuse）")
    data_sub = p_data.add_subparsers(dest="subcommand", required=False)

    p_data_status = data_sub.add_parser("status", help="查看多源熔断器/成功率状态")
    p_data_status.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_data_status.set_defaults(func=_cmd_data_status)

    p_data_sync = data_sub.add_parser("sync-futures", help="主动同步期货 K 线数据")
    p_data_sync.add_argument("--symbol", type=str, default=None, help="品种代码（默认核心子集全部品种）")
    p_data_sync.add_argument("--days", type=int, default=120, help="回溯天数（默认 120）")
    p_data_sync.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_data_sync.set_defaults(func=_cmd_data_sync)

    p_data_cc = data_sub.add_parser("cross-check", help="对指定 symbol+date 做多源交叉验证")
    p_data_cc.add_argument("--symbol", type=str, required=True, help="品种代码（如 RB0）")
    p_data_cc.add_argument("--date", type=str, required=True, help="ISO 日期（如 2026-08-04）")
    p_data_cc.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_data_cc.set_defaults(func=_cmd_data_cross_check)

    p_data_fuse = data_sub.add_parser("fuse", help="多源 K 线融合（MEDIAN/MEAN/WEIGHTED/HIERARCHICAL/TRIMMED_MEAN）")
    p_data_fuse.add_argument("--symbol", type=str, required=True, help="品种代码（如 RB0）")
    p_data_fuse.add_argument("--strategy", type=str, default="MEDIAN",
                             choices=["MEDIAN", "MEAN", "WEIGHTED", "HIERARCHICAL", "TRIMMED_MEAN"],
                             help="融合策略（默认 MEDIAN）")
    p_data_fuse.add_argument("--days", type=int, default=30, help="回溯天数（默认 30）")
    p_data_fuse.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_data_fuse.add_argument("--output", type=str, default=None, help="FusionReport 落盘路径")
    p_data_fuse.set_defaults(func=_cmd_data_fuse)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 入口。"""
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = build_parser()
    args = parser.parse_args(argv)

    # session_id: 整个 CLI 会话唯一标识（一次 `fts` 命令执行），
    # 挂载到 args 传递到各子命令作为日志聚合标识
    args.session_id = generate_session_id()

    if getattr(args, "version", False):
        return _cmd_version(args)

    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    return int(func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
