"""
fts.cli — FTS 统一命令行入口。

提供:
    - python -m fts.cli evolution run    : 启动 L2 因子演化主循环
    python -m fts.cli meta-loop run          : 启动 L1 Meta-Loop
    python -m fts.cli portfolio run          : 启动 L3 组合构建
    python -m fts.cli monitor                : 检查所有循环健康状态
    python -m fts.cli factor list [filters] : 列出 elite 因子（支持筛选/多样性）
    python -m fts.cli factor show <id>       : 查看单个因子详情
    python -m fts.cli factor stats           : 因子分布统计（信号聚类 + 表达类型）
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
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional, cast

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
    generate_run_id,
    generate_trace_id,
    generate_session_id,
    MetaLoop,
    PortfolioLoop,
)
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

logger = logging.getLogger(__name__)


def _prepare_futures_data(
    days: int = 750,
    max_symbols: int = 0,
    symbols: Optional[list[str]] = None,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex, np.ndarray]:
    """准备期货横截面演化所需的面板数据。

    Args:
        days: 回溯天数
        max_symbols: 最大品种数（0 = 使用全部 FUTURES_CORE_SUBSET）
        symbols: 显式品种列表（能源产业链等专属工作流使用；None = 走动态池/max_symbols）

    Returns:
        (panel, common_dates, forward_returns — 使用第一个品种作为微参参考)
    """
    if symbols is None:
        from .data_futures import get_dynamic_core_subset

        dyn = get_dynamic_core_subset()
        symbols = dyn[:max_symbols] if max_symbols > 0 else dyn

    provider = FTSDataProvider()
    panel, common_dates = provider.get_futures_panel(symbols, days=days, trace_id="cli_prepare")

    # 注入宏观字段（GAP-088 v2.103.0）：fut_macro_cpi/interest_rate/export/us_bond
    # 等横截面种子因子读取 export/import_data/cpi/rate/us_bond 5 列真实数据，
    # 拉取失败降级不阻断（因子走 close 趋势代理），与回测管线注入语义一致
    try:
        from .data_sources.macro_aligner import inject_macro_fields_to_panel

        panel = inject_macro_fields_to_panel(panel, trace_id="cli_prepare_futures")
        print(f"[prepare] 宏观字段注入完成: {len(panel)} 个品种")
    except Exception as e:  # noqa: BLE001
        print(f"[prepare] 宏观注入失败（因子走 close 代理）: {e}")

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


def _prepare_data(symbol: str = "000001", days: int = 750) -> tuple[pd.DataFrame, np.ndarray]:
    """准备单标的回测数据（期货主连，backtest run/batch/compare 使用）。

    Args:
        symbol: 期货连续合约代码（如 "RB0"）
        days: 回溯天数

    Returns:
        (OHLCV DataFrame, forward_returns np.ndarray)
    """
    provider = FTSDataProvider()
    df = provider.get_futures_ohlcv(symbol, days=days, trace_id="cli_prepare")

    forward_returns = np.zeros(len(df))
    closes = df["close"].values
    if len(closes) > 5:
        forward_returns[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
    return df, forward_returns


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

    if args.universe in ("futures", "energy"):
        # ── 期货横截面模式（使用期货专用种子因子） ──
        # 能源产业链专属工作流：--chain energy 或 --universe energy 或 --symbols 显式列表 → 链路由
        chain = getattr(args, "chain", "") or ("energy" if args.universe == "energy" else "")
        symbols_raw = getattr(args, "symbols", "") or ""
        if symbols_raw:
            chain_symbols = [s.strip().upper() for s in symbols_raw.split(",") if s.strip()]
            market = "futures"
            elite_dir = cfg.get_elite_dir("futures")
            memory_dir = cfg.memory_dir + "/evolution/futures"
        elif chain == "energy":
            from .data_futures import (
                ENERGY_CHAIN_L1_INJECT_DIR,
                ENERGY_CHAIN_L1_POOL_PATH,
                ENERGY_CHAIN_MARKET,
                ENERGY_CHAIN_SYMBOLS,
            )

            chain_symbols = list(ENERGY_CHAIN_SYMBOLS)
            market = ENERGY_CHAIN_MARKET
            elite_dir = cfg.get_elite_dir(market)
            memory_dir = cfg.memory_dir + "/evolution/energy_chain"
            inject_dir = Path(__file__).resolve().parent.parent / ENERGY_CHAIN_L1_INJECT_DIR
            factor_pool_path = Path(__file__).resolve().parent.parent / ENERGY_CHAIN_L1_POOL_PATH
        else:
            chain_symbols = None
            market = "futures"
            elite_dir = cfg.get_elite_dir("futures")
            memory_dir = cfg.memory_dir + "/evolution/futures"

        print(f"[evolution] universe=futures chain={chain or '-'} market={market} (max_symbols={args.max_stocks})")
        if chain_symbols:
            # 链/显式列表模式：按用户指定品种数回溯（PR0 等新品历史短，面板自动对齐共同窗口）
            panel, common_dates, fwd_ret = _prepare_futures_data(
                days=args.days,
                symbols=chain_symbols,
            )
        else:
            # 默认期货路径（GAP-073 v2.98.0: 500→700 行保证 WalkForward 完整 4 窗口，
            # 勿超 750 行否则落入 3 年分支产出 0 窗口）
            panel, common_dates, fwd_ret = _prepare_futures_data(
                days=700,
                max_symbols=args.max_stocks,
            )
        print(f"[evolution] 期货 panel symbols={len(panel)}, common_dates={len(common_dates)}")

        llm = get_default_llm_client()
        print(f"[evolution] LLM backend: {type(llm).__name__}")

        # 期货模式使用期货专用种子因子（13个期货特有因子）；
        # energy 链模式使用"通用期货 + 能化专属"双知识源种子（GAP-121）
        seed_pool = SeedPool(market="energy" if market == "energy" else "futures")

        # 用第一个品种构造常规 data/forward_returns（微参优化用）
        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]

        # energy 链模式保持期货验证配置（EvolutionLoop 按 market 路由，非 futures 落全局验证器）
        extra_kwargs: dict[str, Any] = {}
        if market != "futures":
            from .factor_engine.contracts import FUTURES_VERIFIER_CONFIG

            extra_kwargs["verifier"] = FactorVerifier(FUTURES_VERIFIER_CONFIG)

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=elite_dir,
            memory_dir=memory_dir,
            inject_dir=inject_dir if market == "energy" else "memory/knowledge/factors/l1_injected",
            factor_pool_path=(
                factor_pool_path if market == "energy" else "memory/knowledge/factors/factor_pool.json"
            ),
            llm_client=llm,
            seed_pool=seed_pool,
            n_trials_micro=min(args.max_generations * 3, 30),
            cross_section_data=panel,
            cross_section_dates=common_dates,
            market=market,
            # 期货专用质检配置：降低 IC/Sharpe 阈值以适配日频期货低信噪比
            quality_card_config=_relaxed_futures_quality_config(),
            # 期货专用审计配置：放宽 OOS 一致性阈值（|ICIR| ≥ 0.5 → ≥ 0.3），
            # 缓解 500 日短样本下种子审计 oos_consistency 全灭、父因子池过小的问题
            audit_config=_relaxed_futures_audit_config(),
            **extra_kwargs,
        )

    # 熔断预算：每个因子最多 4000 token
    budget = DEFAULT_BUDGET_CONFIG.copy()
    budget["max_generation"] = args.max_generations
    # 失败率熔断默认禁用（默认 1.0，2026-08-13 用户指令：夜间演化需强制跑满世代数），
    # 保留 token 与连续低 IC 熔断兜底；
    # 阈值支持 FTS_EVOLUTION_CB_FAILURE_RATE 环境变量覆盖（如 0.99 = 恢复失败率熔断）。
    budget["circuit_breaker_failure_rate"] = float(os.getenv("FTS_EVOLUTION_CB_FAILURE_RATE", "1.0"))
    loop.budget = budget

    # 执行演化
    try:
        result = loop.run(max_generation=args.max_generations)
        print(
            f"[evolution] 完成: status={result.status} "
            f"generations={result.generations_completed} "
            f"elite_count={len(result.elite_factor_ids)}"
        )
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

        web_collector = _make_web_collector(FTSDataProvider(), market=market)
        print("[meta-loop] web_collector 已就绪 — 市场快照感知已启用")

        # MetaLoop
        loop_kwargs: dict[str, Any] = {}
        if market == "energy":
            # 能源链 L1 独立输出（GAP-121 2026-08-15）：memory/factor_pool/inject/debates 全部隔离
            from .data_futures import (
                ENERGY_CHAIN_L1_DEBATES_DIR,
                ENERGY_CHAIN_L1_INJECT_DIR,
                ENERGY_CHAIN_L1_MEMORY_DIR,
                ENERGY_CHAIN_L1_POOL_PATH,
            )

            loop_kwargs = {
                "memory_dir": cfg.memory_dir + "/" + ENERGY_CHAIN_L1_MEMORY_DIR.removeprefix("memory/"),
                "factor_pool_path": Path(__file__).resolve().parent.parent / ENERGY_CHAIN_L1_POOL_PATH,
                "inject_dir": Path(__file__).resolve().parent.parent / ENERGY_CHAIN_L1_INJECT_DIR,
                "debates_dir": Path(__file__).resolve().parent.parent / ENERGY_CHAIN_L1_DEBATES_DIR,
            }
        loop = MetaLoop(
            memory_dir=loop_kwargs.get("memory_dir", cfg.memory_dir + f"/meta_loop/{market}"),
            llm_client=llm,
            market=market,
            web_collector=web_collector,
            sample_symbols=sample_symbols,
            factor_pool_path=loop_kwargs.get("factor_pool_path", "memory/knowledge/factors/factor_pool.json"),
            inject_dir=loop_kwargs.get("inject_dir", "memory/knowledge/factors/l1_injected"),
            debates_dir=loop_kwargs.get("debates_dir", "memory/debates"),
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
    from fts.data_sources.tdx_local_source import TdxLocalSource

    sources: list = []
    try:
        sources.append(TdxLocalSource())
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
        print(
            f"  {name}: success={st['total_success']} failure={st['total_failure']} "
            f"consecutive={st['consecutive_failures']} circuit_open={st['circuit_open']}"
        )
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
        print(
            json.dumps(
                {
                    "trace_id": trace_id,
                    "symbol": args.symbol,
                    "date": args.date,
                    "disagreements": disagreements,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if disagreements else 0

    if not disagreements:
        print("无分歧")
        return 0
    for d in disagreements:
        print(
            f"⚠️ {d.get('symbol', '')} @ {d.get('date', '')} "
            f"max_diff_pct={d.get('max_diff_pct', 0):.4f} outliers={d.get('outliers', [])}"
        )
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
    rows: list[FusedOHLCV] = cast(list[FusedOHLCV], fused_df.to_dict("records")) if not fused_df.empty else []

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
        print(
            f"[data fuse] symbol={args.symbol} strategy={strategy.name} rows={len(rows)} "
            f"sources={sorted(source_dfs.keys())}"
        )
    return 0


def _cmd_portfolio_run(args: argparse.Namespace) -> int:
    """启动 L3 组合构建（GAP-072 与信号管道解绑：L3 仅组合权重，信号管道每日独立运行）。"""
    trace_id = generate_trace_id()
    run_id = generate_run_id()
    session_id = getattr(args, "session_id", "") or ""
    cfg = get_config()
    print(f"[portfolio] session_id={session_id} trace_id={trace_id} run_id={run_id}")

    # 根据 universe 选择 elite 目录和合成模式
    universe = getattr(args, "universe", "futures")
    synthesis_mode = getattr(args, "synthesis_mode", None)
    optimizer_mode = getattr(args, "optimizer_mode", None) or getattr(cfg, "portfolio_optimizer_mode", "risk_parity")
    if universe == "futures":
        elite_dir = cfg.get_elite_dir("futures")
        if synthesis_mode is None:
            synthesis_mode = "equal_weight"  # v2.103.0+23 默认：等权 1/N 分散组合
    elif universe == "energy":
        # 能源产业链逻辑市场：独立精英目录 + 独立组合记忆（GAP-121）
        elite_dir = cfg.get_elite_dir("energy")
        if synthesis_mode is None:
            synthesis_mode = "equal_weight"
    print(f"[portfolio] universe={universe} elite_dir={elite_dir} mode={synthesis_mode} optimizer={optimizer_mode}")

    # 从配置加载 Verifier 配置
    verifier_cfg: L3VerifierConfig = cast(L3VerifierConfig, dict(DEFAULT_L3_VERIFIER_CONFIG))
    if hasattr(cfg, "verifier") and isinstance(cfg.verifier, dict):
        merged = {**DEFAULT_L3_VERIFIER_CONFIG, **cfg.verifier}
        verifier_cfg = cast(L3VerifierConfig, merged)
        print(f"[portfolio] Verifier 配置已加载: max_correlation={verifier_cfg.get('max_correlation', 0.5)}")

    try:
        loop = PortfolioLoop(
            elite_dir=elite_dir,
            memory_dir=cfg.memory_dir + f"/portfolio/{universe}",
            verifier_config=verifier_cfg,
            synthesis_mode=synthesis_mode,
            optimizer_mode=optimizer_mode,
            market=universe,
        )
        # GAP-I302: optimizer 模式与实测化输入（returns-matrix CSV）
        factor_returns = None
        returns_matrix = getattr(args, "returns_matrix", None)
        if returns_matrix:
            import pandas as pd

            try:
                factor_returns = pd.read_csv(returns_matrix, index_col=0, parse_dates=True)
                print(f"[portfolio] 加载 returns-matrix: {factor_returns.shape}")
            except Exception as e:
                print(f"[portfolio] returns-matrix 读取失败（跳过 optimizer/实测化输入）: {e}", file=sys.stderr)
        result = loop.run(
            factor_returns=factor_returns,
            recompute_weights=(True if getattr(args, "force_recompute", False) else None),
        )
        # signal_sharpe 为 Optional（frozen/completed 分支为 None），非数值时兜底 0.0
        sig_sharpe = result.signal_sharpe if isinstance(result.signal_sharpe, (int, float)) else 0.0
        print(
            f"[portfolio] 完成: status={result.status} "
            f"factors={result.n_factors_retained} "
            f"signal_sharpe={sig_sharpe:.4f} "
            f"combo_sharpe={result.combo_sharpe:.4f}"
        )
        if result.status == "frozen":
            print("[portfolio] 权重冻结日：跳过组合重算（复用上次组合，GAP-072）；信号管道每日独立运行")
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
    """启动调度器后台运行（常驻：进程保持存活，调度器 daemon 线程持续触发任务）。"""
    engine = SchedulerEngine()
    started = engine.start(daemon=True)
    if not started:
        print("[scheduler] 调度器启动失败（APScheduler 未安装）", file=sys.stderr)
        return 1
    print(f"[scheduler] 调度器已启动（{len(list_scheduler_tasks())} 个任务）")
    # 主线程阻塞保活（Windows 计划任务 / 后台运行场景），Ctrl+C 优雅停止
    import threading

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        engine.stop()
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


def _load_factor_repo(market: str = "futures"):
    """延迟加载 FactorRepository（避免 CLI 启动依赖 DuckDB）。

    Args:
        market: 市场类型（"futures"），用于路由到对应分库文件。
    """
    from .factor_engine.factor_db.repository import FactorRepository

    return FactorRepository(market=market)


def _get_catalog_db_path(market: str = "futures") -> Path:
    """获取因子目录数据库路径（分市场）。

    Args:
        market: 市场类型（"futures"）。

    Returns:
        对应市场的 DuckDB 文件路径。
    """
    from .factor_engine.factor_db.schema import get_db_path

    return Path(get_db_path(market))


def _cmd_catalog_stats(args: argparse.Namespace) -> int:
    """查看因子存储统计（DuckDB + JSON 文件）。"""
    cfg = get_config()

    # DuckDB 统计（分市场）
    stats: dict[str, Any] = {}
    for mkt in ("futures",):
        db_path = _get_catalog_db_path(mkt)
        mkt_key = f"{mkt}_database"
        stats[mkt_key] = {"path": str(db_path), "exists": db_path.exists()}
        if db_path.exists():
            try:
                repo = _load_factor_repo(market=mkt)
                duck_stats = repo.get_stats()
                stats[mkt_key].update(duck_stats)
                stats[mkt_key]["size_mb"] = round(db_path.stat().st_size / (1024 * 1024), 2)
            except Exception as e:
                print(f"[catalog stats] {mkt} DuckDB 读取失败: {e}", file=sys.stderr)
                stats[mkt_key]["error"] = str(e)

    # JSON 文件统计
    for market in ("futures",):
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
    for mkt in ("futures",):
        mkt_key = f"{mkt}_database"
        db_info = stats.get(mkt_key, {})
        db_path = db_info.get("path", "?")
        print(f"\n{mkt.upper()} 数据库: {db_path}")
        if db_info.get("exists"):
            size_mb = float(db_info.get("size_mb", 0) or 0)
            print(f"  大小: {size_mb:.1f} MB")
            print(
                f"  总因子: {db_info.get('total_factors', '?')}  "
                f"活跃: {db_info.get('active_factors', '?')}  "
                f"精英: {db_info.get('elite_factors', '?')}"
            )
            print(f"  平均 Sharpe: {db_info.get('avg_sharpe', '?')}  平均 IC: {db_info.get('avg_ic', '?')}")
        else:
            print("  (不存在)")
    for market in ("futures",):
        print(f"\n{market.upper()} JSON 文件:")
        print(f"  目录: {stats.get(f'{market}_json_dir', '?')}")
        print(
            f"  文件数: {stats.get(f'{market}_json_files', '?')}  "
            f"大小: {stats.get(f'{market}_json_size_mb', '?'):.2f} MB  "
            f"已淘汰: {stats.get(f'{market}_retired_files', '?')}"
        )
    return 0


def _cmd_catalog_verify(args: argparse.Namespace) -> int:
    """验证 JSON ↔ DuckDB 一致性（分市场校验）。"""
    cfg = get_config()

    # 分市场校验
    all_consistent = True
    combined_result: dict[str, Any] = {"markets": {}}

    for mkt in ("futures",):
        db_path = _get_catalog_db_path(mkt)
        mkt_result: dict[str, Any] = {"database_path": str(db_path)}

        if not db_path.exists():
            mkt_result["error"] = "数据库不存在"
            combined_result["markets"][mkt] = mkt_result
            if getattr(args, "json", False):
                combined_result["markets"][mkt] = mkt_result
            print(f"[catalog verify] {mkt} DuckDB 数据库不存在: {db_path}", file=sys.stderr)
            all_consistent = False
            continue

        try:
            repo = _load_factor_repo(market=mkt)
        except Exception as e:
            print(f"[catalog verify] {mkt} DuckDB 连接失败: {e}", file=sys.stderr)
            mkt_result["error"] = str(e)
            combined_result["markets"][mkt] = mkt_result
            all_consistent = False
            continue

        # 收集 DuckDB 中的 factor_id 集合
        duck_ids: set[str] = set()
        try:
            conn = repo._get_conn()
            rows = conn.execute("SELECT factor_id, name, market, is_elite, status FROM factor_catalog").fetchall()
            for r in rows:
                duck_ids.add(str(r[0]))
        except Exception as e:
            print(f"[catalog verify] {mkt} DuckDB 查询失败: {e}", file=sys.stderr)
            mkt_result["error"] = str(e)
            combined_result["markets"][mkt] = mkt_result
            all_consistent = False
            continue

        # 收集 JSON 文件中的 factor_id 集合
        elite_dir = Path(cfg.get_elite_dir(mkt))
        json_ids = _scan_json_snapshots(elite_dir, mkt)

        # 比对（DuckDB SSOT 权威；JSON elite 为兼容只读快照层）
        only_in_duckdb = duck_ids - json_ids.keys()
        only_in_json = json_ids.keys() - duck_ids
        common = duck_ids & json_ids.keys()

        # --backfill：从 DuckDB SSOT 回填缺失 JSON 快照（幂等），随后重扫重比
        backfilled = 0
        if getattr(args, "backfill", False) and only_in_duckdb:
            backfilled = _backfill_json_snapshots(repo, elite_dir, only_in_duckdb)
            if backfilled:
                json_ids = _scan_json_snapshots(elite_dir, mkt)
                only_in_duckdb = duck_ids - json_ids.keys()
                only_in_json = json_ids.keys() - duck_ids
                common = duck_ids & json_ids.keys()

        consistent = len(only_in_duckdb) == 0 and len(only_in_json) == 0

        mkt_result.update(
            {
                "duckdb_total": len(duck_ids),
                "json_total": len(json_ids),
                "common": len(common),
                "only_in_duckdb": len(only_in_duckdb),
                "only_in_json": len(only_in_json),
                "consistent": consistent,
                "backfilled": backfilled,
                "duckdb_only_samples": sorted(only_in_duckdb)[:10] if only_in_duckdb else [],
                "json_only_samples": sorted(only_in_json)[:10] if only_in_json else [],
            }
        )
        combined_result["markets"][mkt] = mkt_result
        if not consistent:
            all_consistent = False

        if getattr(args, "json", False):
            continue

        print(f"\n=== {mkt.upper()} JSON ↔ DuckDB 一致性校验 ===")
        print(f"  DuckDB 因子数: {mkt_result['duckdb_total']}")
        print(f"  JSON 文件数:   {mkt_result['json_total']}")
        print(f"  交集:          {mkt_result['common']}")
        print(f"  仅 DuckDB 有:  {mkt_result['only_in_duckdb']}")
        print(f"  仅 JSON 有:    {mkt_result['only_in_json']}")
        if backfilled:
            print(f"  🔄 已回填 JSON 快照: {backfilled}")
        if consistent:
            print("  ✅ 一致")
        else:
            print("  ⚠️ 不一致")
            if mkt_result["duckdb_only_samples"]:
                print(f"  DuckDB 独有 (前10): {', '.join(mkt_result['duckdb_only_samples'])}")
            if mkt_result["json_only_samples"]:
                for fid in mkt_result["json_only_samples"]:
                    info = json_ids.get(fid, {})
                    print(f"  JSON 独有: {fid} ({info.get('file', '?')})")

    combined_result["all_consistent"] = all_consistent
    if getattr(args, "json", False):
        print(json.dumps(combined_result, indent=2, ensure_ascii=False, default=str))
    return 0 if all_consistent else 1


def _scan_json_snapshots(elite_dir: Path, mkt: str) -> dict[str, dict[str, str]]:
    """扫描 elite JSON 快照目录，返回 {factor_id: {market, status, file}}。"""
    json_ids: dict[str, dict[str, str]] = {}
    if not elite_dir.exists():
        return json_ids
    for fp in sorted(elite_dir.glob("*.json")):
        if fp.name.startswith("_"):
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            fid = data.get("factor_id", fp.stem)
            json_ids[fid] = {
                "market": data.get("market", mkt),
                "status": data.get("status", "active"),
                "file": str(fp),
            }
        except Exception:  # noqa: BLE001 — 单文件损坏跳过，不阻断整体校验
            pass
    return json_ids


def _backfill_json_snapshots(repo: Any, elite_dir: Path, factor_ids: set[str]) -> int:
    """从 DuckDB SSOT 回填缺失的 JSON elite 快照（幂等，不覆盖既有）。

    与 `_promote_to_elite` 快照格式对齐：factor 字段展开顶层 + 最新 evaluation。
    原子写（tmp + replace），失败跳过不影响其余回填。

    Args:
        repo: FactorRepository 实例（已连接）
        elite_dir: elite JSON 快照目录
        factor_ids: 待回填的 factor_id 集合

    Returns:
        实际写出的快照数
    """
    elite_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for fid in sorted(factor_ids):
        target = elite_dir / f"{fid}.json"
        if target.exists():
            continue
        factor = repo.get_factor(fid)
        if not factor:
            logger.warning("[catalog verify] 回填跳过（DuckDB 无记录）: %s", fid)
            continue
        record = dict(factor)
        if record.get("market", "multi") in ("multi", "other"):
            record["market"] = "futures"
        try:
            evals = repo.get_evaluations(fid, limit=1)
        except Exception:  # noqa: BLE001 — evaluation 缺失不影响快照
            evals = []
        if evals:
            record["evaluation"] = evals[0]
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp.replace(target)
        written += 1
    return written


def _cmd_catalog_backup(args: argparse.Namespace) -> int:
    """备份因子存储（DuckDB + JSON 文件，分市场）。"""
    from datetime import datetime
    import shutil

    cfg = get_config()
    backup_dir = Path("data/backups")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Any] = {"timestamp": timestamp, "backup_dir": str(backup_dir)}

    # 1. 备份 DuckDB（分市场）
    for mkt in ("futures",):
        db_path = _get_catalog_db_path(mkt)
        if db_path.exists():
            db_backup = backup_dir / f"factor_catalog_{mkt}.duckdb.{timestamp}"
            try:
                shutil.copy2(str(db_path), str(db_backup))
                db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)
                results[f"{mkt}_duckdb_backup"] = str(db_backup)
                results[f"{mkt}_duckdb_size_mb"] = db_size_mb
                print(f"  ✅ {mkt.upper()} DuckDB: {db_backup.name} ({db_size_mb:.1f} MB)")
            except Exception as e:
                print(f"  ❌ {mkt} DuckDB 备份失败: {e}", file=sys.stderr)
                results[f"{mkt}_duckdb_error"] = str(e)
        else:
            print(f"  ⚠️ {mkt} DuckDB 数据库不存在，跳过备份")

    # 2. 备份 JSON 文件
    for market in ("futures",):
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


def _filter_factors_by_cluster(
    eligible: list[dict[str, Any]],
    cluster_id: int,
) -> list[dict[str, Any]]:
    """按信号聚类簇 ID 筛选因子（聚类不可用时返回空列表）。

    Args:
        eligible: 含 factor_id/code/params 的因子列表
        cluster_id: 目标簇 ID

    Returns:
        属于该簇的因子（含 cluster_id 标注）
    """
    from fts.factor_engine.factor_clustering import cluster_factors_by_signal

    code_factors = [
        {
            "factor_id": f.get("factor_id"),
            "code": f.get("code") or "",
            "params": f.get("params") or {},
        }
        for f in eligible
    ]
    result = cluster_factors_by_signal(code_factors, cluster_threshold=0.5)
    if not result:
        return []
    members = result["cluster_members"].get(int(cluster_id), [])
    mset = set(members)
    selected = [f for f in eligible if f.get("factor_id") in mset]
    for f in selected:
        f["cluster_id"] = int(cluster_id)
    return selected


def _cmd_factor_list(args: argparse.Namespace) -> int:
    """列出 elite 因子（支持目录直读 + DuckDB 查询两种模式）。"""
    cfg = get_config()
    market = getattr(args, "market", "futures")

    # 筛选参数决定是否走 DuckDB 查询
    cluster_id = getattr(args, "cluster", None)
    min_ic = getattr(args, "min_ic", None)
    min_sharpe = getattr(args, "min_sharpe", None)
    use_diverse = getattr(args, "diverse", False)
    total_count = getattr(args, "total_count", 10)
    # DuckDB 为 SSOT，默认优先查询；JSON 目录仅作回退（plans/29 P4 读路径切换）
    use_db = True

    if use_db:
        try:
            repo = _load_factor_repo(market=market)
            if use_diverse:
                factors = repo.get_diverse_factors(
                    market=market,
                    total_count=total_count,
                    max_per_cluster=getattr(args, "max_per_cluster", 3),
                    min_ic=min_ic if min_ic is not None else 0.02,
                    min_sharpe=min_sharpe if min_sharpe is not None else 0.5,
                )
            elif cluster_id is not None:
                # 按信号聚类簇筛选（DuckDB 模式）
                eligible = repo.get_eligible(
                    market=market,
                    min_ic=min_ic if min_ic is not None else 0.02,
                    min_sharpe=min_sharpe if min_sharpe is not None else 0.5,
                    require_elite=True,
                )
                factors = _filter_factors_by_cluster(eligible, cluster_id)
            elif min_ic is not None or min_sharpe is not None:
                factors = repo.get_eligible(
                    market=market,
                    min_ic=min_ic if min_ic is not None else 0.02,
                    min_sharpe=min_sharpe if min_sharpe is not None else 0.5,
                    require_elite=True,
                )
            else:
                # 无筛选 → 全量列表（对应原 elite JSON glob 默认路径）
                factors = repo.list_factors(
                    market=market,
                    limit=getattr(args, "limit", 500),
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
        print("[factor list] 无符合条件的因子")
        return 0

    market_label = "期货"
    print(f"=== {market_label} Factors ({len(factors)}) ===")

    # JSON 模式输出
    if getattr(args, "json", False):
        print(json.dumps(factors, indent=2, ensure_ascii=False, default=str))
        return 0

    # 文本表格输出
    if isinstance(factors[0], dict):
        has_cluster = any(f.get("cluster_id") is not None for f in factors)
        keys = (["cluster_id"] if has_cluster else []) + [
            "factor_id",
            "name",
            "market",
            "generation",
            "ic",
            "sharpe",
        ]
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


def _compute_expr_type_distribution(
    market: str | None = None,
    elite_dir: Path | None = None,
) -> dict[str, Any]:
    """从 elite 因子文件计算表达类型分布（GAP-S13）。

    Returns:
        { "operator": N, "code": N, "hybrid": N, "total": N, "operator_pct": float }
    """
    cfg = get_config()
    if elite_dir is None:
        elite_dir = Path(cfg.get_elite_dir(market or "futures"))

    if not elite_dir.exists():
        return {"operator": 0, "code": 0, "hybrid": 0, "total": 0, "operator_pct": 0.0}

    counts: dict[str, Any] = {"operator": 0, "code": 0, "hybrid": 0}
    for p in elite_dir.glob("*.json"):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            kind = data.get("kind", "code")
            if kind in counts:
                counts[kind] += 1
            else:
                counts["code"] += 1
        except Exception:  # noqa: BLE001
            pass

    total = sum(counts.values())
    operator_pct = (counts["operator"] / total * 100) if total > 0 else 0.0
    counts["total"] = total
    counts["operator_pct"] = round(operator_pct, 1)
    return counts


def _cmd_factor_stats(args: argparse.Namespace) -> int:
    """统计因子聚类分布（信号相关性）+ 表达类型分布（GAP-S13）。"""
    market = getattr(args, "market", "futures")
    min_sharpe = getattr(args, "min_sharpe", 0.0)
    try:
        repo = _load_factor_repo(market=market)
        factors = repo.get_eligible(
            market=market,
            min_ic=0.0,
            min_sharpe=min_sharpe,
            require_elite=True,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[factor stats] 查询失败: {e}", file=sys.stderr)
        return 1

    if not factors:
        print("[factor stats] 无符合条件的因子")
        return 0

    # 信号相关性聚类分布
    from fts.factor_engine.factor_clustering import cluster_factors_by_signal

    code_factors = [
        {
            "factor_id": f.get("factor_id"),
            "code": f.get("code") or "",
            "params": f.get("params") or {},
        }
        for f in factors
    ]
    cluster_result = cluster_factors_by_signal(code_factors, cluster_threshold=0.5)
    if not cluster_result:
        print("[factor stats] 聚类不可用（信号数据缺失或数据源不可用）")
        return 0

    fid_to_factor = {f.get("factor_id"): f for f in factors}
    dist: list[dict[str, Any]] = []
    for cid in cluster_result["cluster_order"]:
        members = cluster_result["cluster_members"].get(cid, [])
        ic_sum = sharpe_sum = 0.0
        best: tuple[str, float] | None = None
        for mfid in members:
            f = fid_to_factor.get(mfid)
            if f is None:
                continue
            ic = float(f.get("ic", 0) or 0)
            sh = float(f.get("sharpe", 0) or 0)
            ic_sum += ic
            sharpe_sum += sh
            if best is None or sh > best[1]:
                best = (str(f.get("name", mfid)), sh)
        n = len(members)
        dist.append(
            {
                "cluster_id": cid,
                "count": n,
                "rep_name": best[0] if best else "-",
                "avg_ic": round(ic_sum / n, 4) if n else 0,
                "avg_sharpe": round(sharpe_sum / n, 2) if n else 0,
            }
        )

    if getattr(args, "json", False):
        expr_dist = _compute_expr_type_distribution(market="futures")
        output = {
            "cluster_distribution": dist,
            "expr_type_distribution": expr_dist,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
        return 0

    total = sum(row.get("count", 0) for row in dist)
    scope = "期货"
    print(f"=== 因子聚类分布 ({scope}, min_sharpe={min_sharpe}) ===")
    print(f"{'簇':<6} {'代表因子':<28} {'数量':>6}  {'占比':>8} {'平均IC':>8} {'平均Sharpe':>10}")
    print("-" * 78)
    for row in dist:
        pct = (row.get("count", 0) / total * 100) if total > 0 else 0
        print(
            f"{row.get('cluster_id'):<6} {str(row.get('rep_name')):<28} {row.get('count'):>6}  "
            f"{pct:>7.1f}%  {row.get('avg_ic'):>8.4f} {row.get('avg_sharpe'):>10.2f}"
        )
    print("-" * 78)
    print(f"{'合计':<6} {'':28} {total:>6}")

    # GAP-S13: 表达类型分布
    expr_dist = _compute_expr_type_distribution(market="futures")
    print("\n=== 表达类型分布 (GAP-S13) ===")
    print(f"{'类型':<16} {'数量':>6}  {'占比':>8}")
    print("-" * 34)
    for kind in ("operator", "code", "hybrid"):
        count = expr_dist.get(kind, 0)
        et = expr_dist.get("total", 1)
        pct = (count / et * 100) if et > 0 else 0
        if count > 0:
            print(f"{kind:<16} {count:>6}  {pct:>7.1f}%")
    print("-" * 34)
    print(f"{'合计':<16} {expr_dist.get('total', 0):>6}")
    print(f"算子化率: {expr_dist.get('operator_pct', 0.0):.1f}%")
    return 0


def _cmd_factor_lineage(args: argparse.Namespace) -> int:
    """查询单个因子的演化血缘（DuckDB 模式）。"""
    factor_id = args.factor_id
    try:
        # lineage 查询使用期货因子库（股票剥离后主系统仅期货）
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


def _cmd_factor_review_list(args: argparse.Namespace) -> int:
    """列出待审查因子队列（GAP-I102 Alpha 审查工作流）。"""
    from .factor_engine.factor_inspector import FactorReviewWorkflow

    workflow = FactorReviewWorkflow(db_path=args.db)
    try:
        queue = workflow.list_pending(market="futures", limit=args.limit)
    except Exception as e:  # noqa: BLE001
        print(f"[review] 读取审查队列失败: {e}", file=sys.stderr)
        return 1
    if not queue:
        print("[review] 审查队列为空（所有因子均已审查）")
        return 0
    print(f"=== 待审查因子队列 ({len(queue)}) ===")
    for f in queue:
        print(
            f"  - {f['factor_id']} | {f['name']} | market={f['market']} "
            f"| source={f['source']} | ic={f['ic']:.4f} | sharpe={f['sharpe']:.2f}"
        )
    return 0


def _cmd_factor_review_approve(args: argparse.Namespace) -> int:
    """批准因子（pending→approved，意见回写 DuckDB）。"""
    from .factor_engine.factor_inspector import FactorReviewWorkflow

    workflow = FactorReviewWorkflow(db_path=args.db)
    result = workflow.approve(args.factor_id, comment=args.comment)
    print(f"[review] ✅ 已批准 {result['factor_id']}: decision={result['decision']} (comment={args.comment or '-'})")
    return 0


def _cmd_factor_review_reject(args: argparse.Namespace) -> int:
    """驳回因子（pending→rejected，意见回写 DuckDB）。"""
    from .factor_engine.factor_inspector import FactorReviewWorkflow

    workflow = FactorReviewWorkflow(db_path=args.db)
    result = workflow.reject(args.factor_id, comment=args.comment)
    print(f"[review] ❌ 已驳回 {result['factor_id']}: decision={result['decision']} (comment={args.comment or '-'})")
    return 0


def _cmd_factor_review_auto(args: argparse.Namespace) -> int:
    """批量机审（C8-2）：正常自动批准、低质自动驳回、异常值转人审。

    默认机审（FTS_REVIEW_MODE=auto）；manual 模式需 --force 显式覆盖。
    """
    from .factor_engine.factor_inspector import FactorReviewWorkflow

    workflow = FactorReviewWorkflow(db_path=args.db)
    try:
        result = workflow.auto_review(limit=args.limit, force=args.force)
    except ValueError as e:  # manual 模式拒绝
        print(f"[review] ⚠️ {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[review] 机审执行失败: {e}", file=sys.stderr)
        return 1
    print(
        f"[review] 机审完成 (mode={result['mode']}): 处理 {result['total_pending']} 个待审因子 "
        f"| ✅ 自动批准 {result['auto_approved']} | ❌ 自动驳回 {result['auto_rejected']} "
        f"| 🧐 转人审 {len(result['needs_human'])}"
    )
    for f in result["needs_human"][:20]:
        print(f"  - 转人审 {f['factor_id']}: {f['reason']}")
    return 0


def _cmd_factor_recalibrate_list(args: argparse.Namespace) -> int:
    """列出待重校准因子队列（C6）。"""
    import json as _json

    from .factor_engine.recalibration import RecalibrationQueue

    queue = RecalibrationQueue(args.queue).list_pending(limit=args.limit)
    if args.json:
        print(_json.dumps([i.to_dict() for i in queue], ensure_ascii=False, indent=2))
        return 0
    if not queue:
        print("[recalibrate] 重校准队列为空（无 pending 项）")
        return 0
    print(f"=== 待重校准因子队列 ({len(queue)}) ===")
    for item in queue:
        print(f"  - {item.factor_id} | {item.name or '-'} | reason={item.reason or '-'} | created_at={item.created_at}")
    return 0


def _cmd_factor_recalibrate_run(args: argparse.Namespace) -> int:
    """处理重校准队列：微调 + 回写 elite 元数据（C6）。"""
    from .factor_engine.recalibration import (
        RecalibrationConfig,
        RecalibrationQueue,
        process_recalibration_queue,
    )

    cfg = RecalibrationConfig(
        n_trials=args.trials,
        coarse_trials=args.coarse_trials,
        min_ic_gap=args.min_ic_gap,
        queue_path=args.queue,
    )
    # 数据加载（期货主力，4 级降级链含合成兜底）
    from fts.data_futures import FuturesDataProvider

    df = FuturesDataProvider().get_ohlcv(args.symbol, days=args.days)
    if df is None or len(df) < 20:
        print(f"[recalibrate] 数据不足: symbol={args.symbol} days={args.days}", file=sys.stderr)
        return 1
    close = df["close"]
    horizon = max(1, args.horizon)
    forward_returns = (close.shift(-horizon) / close - 1.0).to_numpy(dtype=float)

    elite_dir = args.elite_dir
    if not elite_dir:
        from fts.config.settings import FTSConfig

        elite_dir = FTSConfig().futures_elite_dir
    stats = process_recalibration_queue(
        elite_dir,
        df,
        forward_returns,
        config=cfg,
        queue=RecalibrationQueue(args.queue),
        factor_db_path=args.db,
        dry_run=args.dry_run,
    )
    print(f"[recalibrate] 处理完成: {stats}" + ("（dry-run，未落盘）" if args.dry_run else ""))
    return 0


def _cmd_factor_micro_generate(args: argparse.Namespace) -> int:
    """生成微观结构因子候选（C1：tick → 日频聚合 → FactorProgram，独立候选源）。"""
    from fts.factor_engine.microstructure_generator import (
        MicrostructureFactorGenerator,
    )

    gen = MicrostructureFactorGenerator()
    cands = gen.generate_batch(symbols=args.symbols, trace_id="cli_micro_generate")
    if not cands:
        print("[micro-generate] 无候选生成（tick 数据不足，见日志）", file=sys.stderr)
        return 1
    if args.limit > 0:
        cands = cands[: args.limit]
    if args.json:
        import json

        payload = [
            {
                "factor_id": c.factor["factor_id"],
                "name": c.factor["name"],
                "symbol": c.symbol,
                "kind": c.kind,
                "n_days": c.n_days,
                "date_start": c.factor["params"]["dates"][0],
                "date_end": c.factor["params"]["dates"][-1],
            }
            for c in cands
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"=== 微观结构因子候选 ({len(cands)}) ===")
        for c in cands:
            f = c.factor
            dates = f["params"]["dates"]
            print(f"  - {f.get('name')} [{f['factor_id']}] days={c.n_days} ({dates[0]} ~ {dates[-1]})")
    return 0


def _cmd_factor_micro_evaluate(args: argparse.Namespace) -> int:
    """C1 评估晋升接线：microstructure 候选 → L2 评估链 → 审计 → elite。"""
    from .factor_engine.evolution_loop import EvolutionLoop

    panel, common_dates, fwd_ret = _prepare_futures_data(days=700, max_symbols=args.max_symbols)
    print(f"[micro-evaluate] futures panel symbols={len(panel)}, common_dates={len(common_dates)}")

    first_sym = list(panel.keys())[0]
    loop = EvolutionLoop(
        data=panel[first_sym],
        forward_returns=fwd_ret,
        elite_dir=args.elite_dir or get_config().get_elite_dir("futures"),
        memory_dir=get_config().memory_dir + "/evolution/futures",
        llm_client=get_default_llm_client(),
        seed_pool=SeedPool(market="futures"),
        n_trials_micro=10,
        cross_section_data=panel,
        cross_section_dates=common_dates,
        market="futures",
    )
    result = loop.run_microstructure_promotion(
        symbols=args.symbols or None, limit=args.limit, trace_id="cli_micro_evaluate"
    )
    print(
        f"[micro-evaluate] 生成 {result['generated']} | 评估 {result['evaluated']} "
        f"| 过门槛 {result['passed']} | 晋升 {result['promoted']} | 跳过 {result['skipped']}"
    )
    for fid in result["promoted_ids"]:
        print(f"  - ✅ 晋升 elite: {fid}")
    return 0


def _cmd_factor_senti_generate(args: argparse.Namespace) -> int:
    """生成舆情情感因子候选（C2：新闻 → 词典打分 → 日频聚合 → FactorProgram）。"""
    from fts.factor_engine.alternative_sentiment import SentimentFactorGenerator

    gen = SentimentFactorGenerator()
    cands = gen.generate_batch(symbols=args.symbols, trace_id="cli_senti_generate")
    if not cands:
        print("[senti-generate] 无候选生成（新闻数据不足，见日志）", file=sys.stderr)
        return 1
    if args.limit > 0:
        cands = cands[: args.limit]
    if args.json:
        import json

        payload = [
            {
                "factor_id": c.factor["factor_id"],
                "name": c.factor["name"],
                "symbol": c.symbol,
                "kind": c.kind,
                "n_days": c.n_days,
                "date_start": c.factor["params"]["dates"][0],
                "date_end": c.factor["params"]["dates"][-1],
            }
            for c in cands
        ]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"=== 舆情情感因子候选 ({len(cands)}) ===")
        for c in cands:
            f = c.factor
            dates = f["params"]["dates"]
            print(f"  - {f.get('name')} [{f['factor_id']}] days={c.n_days} ({dates[0]} ~ {dates[-1]})")
    return 0


def _cmd_factor_senti_consistency(args: argparse.Namespace) -> int:
    """C2 LLM 精修：词典打分与 LLM 抽样标注一致性验证（验收 ≥0.7）。"""
    from .factor_engine.alternative_sentiment import (
        EastmoneyNewsProvider,
        evaluate_lexicon_consistency,
    )

    provider = EastmoneyNewsProvider()
    symbols = list(args.symbols) if args.symbols else []
    if not symbols:
        try:
            from fts.data_futures import get_dynamic_core_subset

            symbols = get_dynamic_core_subset()
        except Exception:  # noqa: BLE001 — 动态池异常回退空
            symbols = []
    texts: list[str] = []
    for symbol in symbols[: args.max_symbols or 25]:
        try:
            df = provider.fetch_news(symbol=symbol, lookback_days=args.lookback_days)
        except Exception as e:  # noqa: BLE001 — 单品种抓取失败跳过
            print(f"[senti-consistency] {symbol} 抓取失败: {e}")
            continue
        if df is None or df.empty:
            continue
        for row in df.itertuples(index=False):
            t = str(getattr(row, "title", "") or "").strip()
            s = str(getattr(row, "summary", "") or "").strip()
            if t:
                texts.append(t)
            elif s:
                texts.append(s)
    if not texts:
        print("[senti-consistency] 无新闻样本（网络/数据不足），跳过", file=sys.stderr)
        return 1
    if args.sample > 0:
        texts = texts[: args.sample]
    llm = get_default_llm_client()
    res = evaluate_lexicon_consistency(texts, llm, min_consistency=args.min_consistency)
    print(
        f"[senti-consistency] 样本 {res['total']} | 有效标注 {res['valid']} "
        f"| 一致 {res['agreement']} | 一致率 {res['agreement_rate']:.2%} "
        f"| 阈值 {res['min_consistency']:.0%} | {'✅ 达标' if res['passed'] else '❌ 未达标'}"
    )
    return 0


def _cmd_factor_seeds(args: argparse.Namespace) -> int:
    """列出种子因子（期货）。"""
    from fts.factor_engine.seed_data_futures_full import load_futures_seeds_full

    seeds = load_futures_seeds_full(trace_id="cli_seed_list")
    print(f"=== 期货种子因子 ({len(seeds)}) ===")
    for s in seeds:
        sig = s.get("signature", {})
        params = s.get("params", {})
        print(f"  - {s.get('name', '?')}")
        print(f"      输入: {sig.get('input_fields', [])}")
        print(f"      参数: {params}")

    return 0


def _cmd_factor_show(args: argparse.Namespace) -> int:
    """查看单个因子详情（DuckDB SSOT 优先，JSON 目录回退）。"""
    cfg = get_config()
    factor_id = args.factor_id
    market = "futures"
    # DuckDB 精确匹配优先（plans/29 P4 读路径切换）
    try:
        repo = _load_factor_repo(market=market)
        data = repo.get_factor(factor_id)
        if data is None:
            # 片段匹配兜底（兼容"支持部分匹配"；elite JSON 快照已退役）
            fuzzy = repo.search_factors(factor_id, market=market, limit=1)
            if fuzzy:
                data = fuzzy[0]
        if data is not None:
            print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
            return 0
    except Exception as e:  # noqa: BLE001
        print(f"[factor show] DuckDB 查询失败，回退目录模式: {e}", file=sys.stderr)
    # 回退：JSON 目录 glob（兼容片段匹配）
    if args.elite_dir:
        elite_dir = Path(args.elite_dir)
    else:
        elite_dir = Path(cfg.get_elite_dir(market))
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
        load_all_factors,
        validate_all,
        check_duplicates,
    )

    market = "futures"
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

    market = "futures"
    all_factors = load_all_factors(market)
    print(generate_report(all_factors, market))
    return 0


def _cmd_seed_dedup(args: argparse.Namespace) -> int:
    """检查跨文件因子重复。"""
    from scripts.unified_factor_converter import load_all_factors, check_duplicates

    market = "futures"
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
        repo = _load_factor_repo(market=market)
        return repo.get_by_id(factor_id, market=market)
    except Exception as e:  # noqa: BLE001
        print(f"[backtest] 因子加载失败: {e}", file=sys.stderr)
        return None


def _cmd_backtest_run(args: argparse.Namespace) -> int:
    """单个因子回测。"""
    factor = _load_factor_by_id(args.factor_id, "futures")
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
        BacktestInput,
        BacktestPipeline,
    )
    from .factor_engine.report_generator import ReportGenerator

    result = BacktestPipeline().run(
        BacktestInput(
            factor=factor,
            data=data,
            date_range=date_range,
            initialization_capital=args.capital,
            frequency=freq,
        )
    )
    if not result.success:
        print(f"[backtest] 回测失败: {result.error}", file=sys.stderr)
        return 1

    report = result.output
    if report is None:
        print("[backtest] 回测报告为空", file=sys.stderr)
        return 1
    m = report.metrics
    freq_label = getattr(args, "frequency", "daily")
    print(f"=== 回测结果: {report.factor_id} (频率: {freq_label}) ===")
    print(f"期间: {report.start_date} ~ {report.end_date}")
    print(f"总收益: {m.total_return:.2%} | 年化: {m.annual_return:.2%} | Sharpe: {m.sharpe_ratio:.3f}")
    print(
        f"最大回撤: {m.max_drawdown:.2%} | Calmar: {m.calmar_ratio:.3f} | "
        f"胜率: {m.win_rate:.2%} | 盈亏比: {m.payoff_ratio:.2f} | 盈亏因子: {m.profit_factor:.2f}"
    )
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

    market = "futures"
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
        screened,
        data,
        initialization_capital=args.capital,
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

    market = "futures"
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
        factors,
        data,
        initialization_capital=args.capital,
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
            print(
                f"{r.rank:<5}{r.factor_id:<40}{m.sharpe_ratio:<10.3f}"
                f"{m.ic_mean:<10.4f}{m.max_drawdown:<10.2%}{m.total_return:<10.2%}"
            )
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
        print(
            json.dumps(
                [op.__dict__ for op in ops],
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    print(f"{'算子':<24}{'类别':<16}{'签名'}")
    print("-" * 70)
    for op in ops:
        print(f"{op.name:<24}{op.category:<16}{op.signature}")
    return 0


def _cmd_feature_analyze(args: argparse.Namespace) -> int:
    """特征重要性分析。"""
    from .factor_engine.feature_importance import FeatureImportanceAnalyzer

    factor = _load_factor_by_id(args.factor_id, "futures")
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
            json.dumps(
                {
                    "factor_id": args.factor_id,
                    "baseline_ic": result.baseline_ic,
                    "importance": result.feature_importance,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"结果已保存: {out}")
    return 0


def _cmd_gp_evolve(args: argparse.Namespace) -> int:
    """GP 遗传规划因子演化。"""
    from .factor_engine.feature_ops import FeatureOpsEngine
    from .factor_engine.gp_evolver import GPEvolver, GPEvolverConfig

    # 准备面板数据（GP 演化 — 期货横截面）
    panel, common_dates, _ = _prepare_futures_data(
        days=args.days,
        max_symbols=args.max_symbols,
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
        fwd[: -args.forward] = (closes[args.forward :] - closes[: -args.forward]) / np.maximum(
            closes[: -args.forward], 1e-10
        )
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
            json.dumps(
                {
                    "expression": result.best_expression,
                    "fitness": result.best_fitness,
                    "ic": result.best_ic,
                    "sharpe": result.best_sharpe,
                },
                indent=2,
                ensure_ascii=False,
            ),
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
    print("=== 反馈事件已触发 ===")
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
        print(f"  - {r['event_id']}: root_cause={r['root_cause']}, action={r['action_taken']}, success={r['success']}")
    return 0


def _cmd_feedback_report(args: argparse.Namespace) -> int:
    """生成月度迭代效果报告。"""
    from .factor_engine.feedback_loop import FeedbackLoop

    loop = FeedbackLoop()
    report = loop.generate_monthly_report(period=getattr(args, "month", None))
    print(f"=== 迭代效果月报 ({report['period']}) ===")
    print(f"新因子: {report['new_factors']} | 有效率: {report['effective_rate']:.1%}")
    print(
        f"反馈处理: {report['feedback_events_handled']} | "
        f"建议采纳: {report['recommendations_accepted']}/{report['recommendations_total']}"
    )
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


def _cmd_feedback_import(args: argparse.Namespace) -> int:
    """导入实盘反馈记录（GAP-L402）：CSV → 校验 → 落盘。"""
    from pathlib import Path

    from .factor_engine.feedback_loop import LiveFeedbackImporter

    path = Path(args.csv_path)
    if not path.exists():
        print(f"[feedback] 文件不存在: {path}")
        return 1
    importer = LiveFeedbackImporter(db_path=args.db)
    try:
        result = importer.import_csv(str(path))
    except Exception as e:  # noqa: BLE001
        print(f"[feedback] 导入失败: {e}")
        return 1
    print(f"[feedback] 导入完成: total={result['total']} valid={result['valid']} invalid={result['invalid']}")
    if result["invalid_messages"]:
        print(f"[feedback] 无效记录示例: {result['invalid_messages'][:3]}")
    return 0


def _cmd_feedback_live_ic(args: argparse.Namespace) -> int:
    """实盘 IC vs 回测 IC 对比报告（GAP-L402）。"""
    import json as _json
    from pathlib import Path

    from .factor_engine.feedback_loop import (
        LiveFeedbackImporter,
        LiveVsBacktestICReport,
    )

    importer = LiveFeedbackImporter(db_path=args.db)
    backtest_ic_map: dict[str, float] = {}
    if args.backtest_ic:
        try:
            bt_path = Path(args.backtest_ic)
            data = _json.loads(bt_path.read_text(encoding="utf-8"))
            backtest_ic_map = {k: float(v) for k, v in data.items()}
        except (OSError, ValueError) as e:
            print(f"[feedback] 读取回测 IC 失败: {e}")
            return 1

    records = importer._records  # noqa: SLF001
    if not records and args.db:
        # 从 DuckDB feedback_live 表读取
        try:
            import duckdb  # type: ignore

            con = duckdb.connect(args.db)
            try:
                rows = con.execute(
                    "SELECT factor_id, signal_date, signal_value, position_return, "
                    "turnover, slippage, market, backtest_ic, weight "
                    "FROM feedback_live"
                ).fetchall()
            finally:
                con.close()
            for r in rows:
                rec = {
                    "factor_id": r[0],
                    "signal_date": r[1],
                    "signal_value": r[2],
                    "position_return": r[3],
                    "turnover": r[4],
                }
                if r[5] is not None:
                    rec["slippage"] = r[5]
                if r[6] is not None:
                    rec["market"] = r[6]
                if r[7] is not None:
                    rec["backtest_ic"] = r[7]
                if r[8] is not None:
                    rec["weight"] = r[8]
                records.append(rec)
        except Exception as e:  # noqa: BLE001
            print(f"[feedback] 读取 DuckDB 反馈表失败: {e}")
            return 1

    if not records:
        print("[feedback] 无实盘反馈记录（请先运行 fts feedback import）")
        return 1

    live_ic = importer.compute_live_ic(records)
    report = LiveVsBacktestICReport().generate(live_ic, backtest_ic_map)
    print("=== 实盘 IC vs 回测 IC 对比 ===")
    print(
        f"因子数: {report['summary']['n_factors']} | "
        f"衰减: {report['summary']['n_decayed']} | "
        f"整体实盘 IC: {report['summary']['overall_live_ic']:.4f} | "
        f"记录数: {report['summary']['n_records']}"
    )
    for row in report["factors"]:
        bt = f"{row['backtest_ic']:.4f}" if row["backtest_ic"] is not None else "N/A"
        print(f"  {row['factor_id']}: live_ic={row['live_ic']:.4f} bt_ic={bt} status={row['status']}")
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
    p_evo_run.add_argument("--max-generations", type=int, default=10, help="最大演化代数（默认 10）")
    p_evo_run.add_argument(
        "--universe",
        type=str,
        default="futures",
        choices=["futures", "energy"],
        help="演化品种池类型: futures（期货，默认）/ energy（能源产业链逻辑市场）",
    )
    p_evo_run.add_argument("--max-stocks", type=int, default=0, help="横截面模式最大标的数（0 = 使用全部品种）")
    p_evo_run.add_argument("--days", type=int, default=750, help="回溯天数（默认 750，GAP-S08 长窗口）")
    p_evo_run.add_argument(
        "--chain",
        type=str,
        default="",
        choices=["", "energy"],
        help="产业链专属工作流: energy（能源产业链，链专属训练链+独立因子库路由）",
    )
    p_evo_run.add_argument(
        "--symbols",
        type=str,
        default="",
        help="显式品种列表（逗号分隔，如 SC0,FU0,LU0；覆盖 --chain 默认链品种）",
    )
    p_evo_run.set_defaults(func=_cmd_evolution_run)

    # meta-loop run
    p_meta = sub.add_parser("meta-loop", help="L1 Meta-Loop")
    meta_sub = p_meta.add_subparsers(dest="subcommand", required=False)
    p_meta_run = meta_sub.add_parser("run", help="启动 L1 Meta-Loop")
    p_meta_run.add_argument(
        "--market",
        type=str,
        default=None,
        choices=["futures", "energy"],
        help="市场类型: futures（期货，默认）/ energy（能源产业链，L1 输出独立隔离）",
    )
    p_meta_run.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="感知层抽样品种，逗号分隔（如 rb,i,au,sc），默认覆盖五大板块共 13 个品种",
    )
    p_meta_run.set_defaults(func=_cmd_meta_loop_run)

    # portfolio run
    p_port = sub.add_parser("portfolio", help="L3 组合构建")
    port_sub = p_port.add_subparsers(dest="subcommand", required=False)
    p_port_run = port_sub.add_parser("run", help="启动 L3 组合构建")
    p_port_run.add_argument(
        "--universe",
        type=str,
        default="futures",
        choices=["futures", "energy"],
        help="因子池类型: futures（期货）/ energy（能源产业链逻辑市场）",
    )
    p_port_run.add_argument(
        "--synthesis-mode",
        type=str,
        default=None,
        choices=["equal_weight", "sharpe_weight", "elastic_net", "adaptive", "optimizer"],
        help="信号合成模式: equal_weight（默认）/ elastic_net / adaptive（Regime 自适应双维度）/ sharpe_weight / optimizer（GAP-I302，需 returns-matrix）",
    )
    p_port_run.add_argument(
        "--optimizer-mode",
        type=str,
        default=None,
        choices=["risk_parity", "mvo", "bl"],
        help="optimizer 目标模式（GAP-I302，默认 risk_parity；mvo=均值方差；bl=Black-Litterman 观点融合，C3）",
    )
    p_port_run.add_argument(
        "--returns-matrix", type=str, default=None, help="因子收益矩阵 CSV 路径（optimizer 模式与实测化输入，可选）"
    )
    p_port_run.add_argument(
        "--force-recompute",
        action="store_true",
        help="强制全量重算组合权重（GAP-072，默认按 l3_weight_recompute_cadence 自动判定：weekly 仅周五重算，其余日冻结）",
    )
    p_port_run.add_argument(
        "--enable-pca",
        action="store_true",
        help="启用 P2 PCA 降维权重（Step 1.9；equal_weight 模式下以 PCA 载荷×解释方差权重替换均匀等权，v2.103.0+24）",
    )
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
    p_cat_verify.add_argument(
        "--backfill",
        action="store_true",
        help="从 DuckDB SSOT 回填缺失的 JSON elite 快照（幂等），随后重新校验",
    )
    p_cat_verify.set_defaults(func=_cmd_catalog_verify)

    p_cat_backup = cat_sub.add_parser("backup", help="备份因子存储（DuckDB + JSON）")
    p_cat_backup.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_cat_backup.set_defaults(func=_cmd_catalog_backup)

    # factor
    p_factor = sub.add_parser("factor", help="因子管理")
    factor_sub = p_factor.add_subparsers(dest="subcommand", required=False)

    # factor list (增强：支持 DuckDB 查询与筛选)
    p_factor_list = factor_sub.add_parser("list", help="列出 elite 因子")
    p_factor_list.add_argument(
        "--market",
        type=str,
        default="futures",
        choices=["futures", "energy"],
        help="市场类型: futures（期货）/ energy（能源产业链逻辑市场）",
    )
    p_factor_list.add_argument("--elite-dir", default=None, help="elite 因子目录（仅目录模式使用）")
    p_factor_list.add_argument("--cluster", type=int, default=None, help="按信号聚类簇 ID 筛选（DuckDB 模式）")
    p_factor_list.add_argument("--min-ic", type=float, default=None, help="最低 IC 阈值")
    p_factor_list.add_argument("--min-sharpe", type=float, default=None, help="最低 Sharpe 阈值")
    p_factor_list.add_argument("--diverse", action="store_true", help="启用多样性选择（按信号聚类簇配额）")
    p_factor_list.add_argument("--total-count", type=int, default=10, help="多样性选择总数（默认 10）")
    p_factor_list.add_argument("--max-per-cluster", type=int, default=3, help="单信号簇最大因子数（默认 3）")
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
    p_factor_seeds.set_defaults(func=_cmd_factor_seeds)

    # factor stats
    p_factor_stats = factor_sub.add_parser("stats", help="因子分布统计（信号聚类 + 表达类型）")
    p_factor_stats.add_argument(
        "--market",
        type=str,
        default="futures",
        choices=["futures", "energy"],
        help="市场类型: futures（期货）/ energy（能源产业链逻辑市场）",
    )
    p_factor_stats.add_argument("--min-sharpe", type=float, default=0.0, help="最低 Sharpe 阈值（默认 0.0）")
    p_factor_stats.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_factor_stats.set_defaults(func=_cmd_factor_stats)

    # factor lineage
    p_factor_lineage = factor_sub.add_parser("lineage", help="查询因子演化血缘")
    p_factor_lineage.add_argument("factor_id", help="因子 ID")
    p_factor_lineage.set_defaults(func=_cmd_factor_lineage)

    # factor review（GAP-I102 Alpha 审查工作流）
    p_factor_review = factor_sub.add_parser("review", help="Alpha 审查工作流（GAP-I102）")
    review_sub = p_factor_review.add_subparsers(dest="subcommand", required=True)

    p_rev_list = review_sub.add_parser("list", help="列出待审查因子队列")
    p_rev_list.add_argument("--limit", type=int, default=50, help="队列上限（默认 50）")
    p_rev_list.add_argument("--db", default=None, help="DuckDB 文件路径")
    p_rev_list.set_defaults(func=_cmd_factor_review_list)

    p_rev_approve = review_sub.add_parser("approve", help="批准因子（pending→approved）")
    p_rev_approve.add_argument("factor_id", help="因子 ID")
    p_rev_approve.add_argument("--comment", default="", help="审查意见")
    p_rev_approve.add_argument("--db", default=None, help="DuckDB 文件路径")
    p_rev_approve.set_defaults(func=_cmd_factor_review_approve)

    p_rev_reject = review_sub.add_parser("reject", help="驳回因子（pending→rejected）")
    p_rev_reject.add_argument("factor_id", help="因子 ID")
    p_rev_reject.add_argument("--comment", default="", help="审查意见")
    p_rev_reject.add_argument("--db", default=None, help="DuckDB 文件路径")
    p_rev_reject.set_defaults(func=_cmd_factor_review_reject)

    p_rev_auto = review_sub.add_parser("auto", help="批量机审（C8-2）：正常自动批准/低质自动驳回/异常转人审")
    p_rev_auto.add_argument("--limit", type=int, default=200, help="处理队列上限（默认 200）")
    p_rev_auto.add_argument("--force", action="store_true", help="manual（纯人审）模式下强制运行机审")
    p_rev_auto.add_argument("--db", default=None, help="DuckDB 文件路径")
    p_rev_auto.set_defaults(func=_cmd_factor_review_auto)

    # factor recalibrate（C6 自动重校准队列）
    p_factor_recal = factor_sub.add_parser("recalibrate", help="因子自动重校准（C6）")
    recal_sub = p_factor_recal.add_subparsers(dest="subcommand", required=True)

    p_recal_list = recal_sub.add_parser("list", help="列出待重校准队列")
    p_recal_list.add_argument("--queue", default="memory/portfolio/recalibration_queue.json", help="队列 JSON 路径")
    p_recal_list.add_argument("--limit", type=int, default=50, help="显示上限（默认 50）")
    p_recal_list.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_recal_list.set_defaults(func=_cmd_factor_recalibrate_list)

    p_recal_run = recal_sub.add_parser("run", help="处理重校准队列（微调 + 回写 elite）")
    p_recal_run.add_argument("--queue", default="memory/portfolio/recalibration_queue.json", help="队列 JSON 路径")
    p_recal_run.add_argument("--elite-dir", default=None, help="elite 因子目录（默认期货 elite）")
    p_recal_run.add_argument("--symbol", default="rb", help="数据品种（主连，默认 rb）")
    p_recal_run.add_argument("--days", type=int, default=300, help="回溯交易日数（默认 300）")
    p_recal_run.add_argument("--horizon", type=int, default=5, help="前向收益持有期（默认 5）")
    p_recal_run.add_argument("--trials", type=int, default=40, help="精筛试验数（默认 40）")
    p_recal_run.add_argument("--coarse-trials", type=int, default=20, help="粗筛试验数（默认 20）")
    p_recal_run.add_argument("--min-ic-gap", type=float, default=0.0, help="微调后 IC 提升下限（默认 0.0）")
    p_recal_run.add_argument("--db", default=None, help="DuckDB 路径（存在时同步 metadata）")
    p_recal_run.add_argument("--dry-run", action="store_true", help="只评估不落盘（不回写队列/elite）")
    p_recal_run.set_defaults(func=_cmd_factor_recalibrate_run)

    # factor micro-generate（C1 微观结构因子候选生成）
    p_factor_micro = factor_sub.add_parser("micro-generate", help="生成微观结构因子候选（C1，tick→日频聚合）")
    p_factor_micro.add_argument("--symbols", nargs="*", default=None, help="品种清单（默认动态池 25 品种）")
    p_factor_micro.add_argument("--limit", type=int, default=0, help="最多输出候选数（0=全部）")
    p_factor_micro.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_factor_micro.set_defaults(func=_cmd_factor_micro_generate)

    # factor micro-evaluate（C1 评估晋升接线）
    p_factor_micro_ev = factor_sub.add_parser(
        "micro-evaluate", help="评估并晋升微观结构候选（C1：L2 评估链 → 审计 → elite）"
    )
    p_factor_micro_ev.add_argument("--symbols", nargs="*", default=None, help="品种清单（默认动态池）")
    p_factor_micro_ev.add_argument("--limit", type=int, default=0, help="最多评估候选数（0=全部）")
    p_factor_micro_ev.add_argument("--max-symbols", type=int, default=0, help="面板最大品种数（0=全部）")
    p_factor_micro_ev.add_argument("--elite-dir", type=str, default="", help="elite 目录（默认期货 elite 目录）")
    p_factor_micro_ev.set_defaults(func=_cmd_factor_micro_evaluate)

    # factor senti-generate（C2 舆情情感因子候选生成）
    p_factor_senti = factor_sub.add_parser("senti-generate", help="生成舆情情感因子候选（C2，新闻→词典打分→日频聚合）")
    p_factor_senti.add_argument("--symbols", nargs="*", default=None, help="品种清单（默认动态池 25 品种）")
    p_factor_senti.add_argument("--limit", type=int, default=0, help="最多输出候选数（0=全部）")
    p_factor_senti.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_factor_senti.set_defaults(func=_cmd_factor_senti_generate)

    # factor senti-consistency（C2 LLM 精修：词典-LLM 一致性验收）
    p_factor_senti_cc = factor_sub.add_parser("senti-consistency", help="词典打分与 LLM 标注一致性验证（C2 验收 ≥0.7）")
    p_factor_senti_cc.add_argument("--symbols", nargs="*", default=None, help="品种清单（默认动态池）")
    p_factor_senti_cc.add_argument("--sample", type=int, default=50, help="抽样文本数（默认 50）")
    p_factor_senti_cc.add_argument("--lookback-days", type=int, default=63, help="新闻回看天数")
    p_factor_senti_cc.add_argument("--max-symbols", type=int, default=25, help="最多抓取品种数")
    p_factor_senti_cc.add_argument("--min-consistency", type=float, default=0.7, help="一致性达标阈值（默认 0.7）")
    p_factor_senti_cc.set_defaults(func=_cmd_factor_senti_consistency)

    # seed（种子因子管理）
    p_seed = sub.add_parser("seed", help="种子因子管理（验证/报告/去重）")
    seed_sub = p_seed.add_subparsers(dest="subcommand", required=False)

    p_seed_validate = seed_sub.add_parser("validate", help="验证所有种子因子（完整性/语法/跨文件重复）")
    p_seed_validate.set_defaults(func=_cmd_seed_validate)

    p_seed_report = seed_sub.add_parser("report", help="生成种子因子统计报告")
    p_seed_report.set_defaults(func=_cmd_seed_report)

    p_seed_dedup = seed_sub.add_parser("dedup", help="检查跨文件因子重复")
    p_seed_dedup.set_defaults(func=_cmd_seed_dedup)

    # backtest（B.2 回测流水线）
    p_backtest = sub.add_parser("backtest", help="回测流水线（B.2）")
    bt_sub = p_backtest.add_subparsers(dest="subcommand", required=False)

    # backtest run
    p_bt_run = bt_sub.add_parser("run", help="单个因子回测")
    p_bt_run.add_argument("--factor-id", required=True, help="因子 ID")
    p_bt_run.add_argument("--symbol", default="000001", help="回测标的代码（默认 000001）")
    p_bt_run.add_argument("--start", default=None, help="开始日期 YYYY-MM-DD")
    p_bt_run.add_argument("--end", default=None, help="结束日期 YYYY-MM-DD")
    p_bt_run.add_argument("--days", type=int, default=750, help="回溯天数（默认 750，GAP-S08 长窗口）")
    p_bt_run.add_argument("--capital", type=float, default=1_000_000.0, help="初始资金")
    p_bt_run.add_argument(
        "--frequency",
        default="daily",
        choices=["daily", "1m", "5m", "15m", "30m", "60m"],
        help="数据频率（默认 daily，v2.30.0 分钟级支持）",
    )
    p_bt_run.add_argument("--output", default=None, help="报告输出目录")
    p_bt_run.set_defaults(func=_cmd_backtest_run)

    # backtest batch
    p_bt_batch = bt_sub.add_parser("batch", help="批量回测 + 对比排名")
    p_bt_batch.add_argument("--grade", default="B", help="最低等级 A/B/C（默认 B）")
    p_bt_batch.add_argument("--min-score", type=float, default=None, help="最低质量总分")
    p_bt_batch.add_argument("--limit", type=int, default=20, help="最大回测因子数（默认 20）")
    p_bt_batch.add_argument("--symbol", default="000001", help="回测标的代码")
    p_bt_batch.add_argument("--days", type=int, default=750, help="回溯天数（默认 750，GAP-S08 长窗口）")
    p_bt_batch.add_argument("--capital", type=float, default=1_000_000.0, help="初始资金")
    p_bt_batch.set_defaults(func=_cmd_backtest_batch)

    # backtest compare
    p_bt_cmp = bt_sub.add_parser("compare", help="对比回测多个因子")
    p_bt_cmp.add_argument("--factor-ids", required=True, help="逗号分隔的因子 ID 列表")
    p_bt_cmp.add_argument("--symbol", default="000001", help="回测标的代码")
    p_bt_cmp.add_argument("--days", type=int, default=750, help="回溯天数（GAP-S08 长窗口）")
    p_bt_cmp.add_argument("--capital", type=float, default=1_000_000.0, help="初始资金")
    p_bt_cmp.set_defaults(func=_cmd_backtest_compare)

    # feature（C.1 特征工程中台）
    p_feature = sub.add_parser("feature", help="特征工程中台（C.1）")
    feat_sub = p_feature.add_subparsers(dest="subcommand", required=False)

    p_feat_list = feat_sub.add_parser("list", help="列出特征算子")
    p_feat_list.add_argument(
        "--category",
        default=None,
        help="算子类别（time_series/price/rolling/technical/cross_section/cross_symbol/composite）",
    )
    p_feat_list.add_argument("--json", action="store_true", help="JSON 格式输出")
    p_feat_list.set_defaults(func=_cmd_feature_list)

    p_feat_analyze = feat_sub.add_parser("analyze", help="特征重要性分析")
    p_feat_analyze.add_argument("--factor-id", required=True, help="因子 ID")
    p_feat_analyze.add_argument("--days", type=int, default=750, help="回溯天数（GAP-S08 长窗口）")
    p_feat_analyze.add_argument("--output", default=None, help="结果输出目录")
    p_feat_analyze.set_defaults(func=_cmd_feature_analyze)

    # gp（C.1 GP 遗传规划演化）
    p_gp = sub.add_parser("gp", help="GP 遗传规划因子演化（C.1）")
    gp_sub = p_gp.add_subparsers(dest="subcommand", required=False)

    p_gp_evolve = gp_sub.add_parser("evolve", help="运行 GP 演化")
    p_gp_evolve.add_argument("--universe", default="futures", choices=["futures"], help="品种池类型（默认：futures）")
    p_gp_evolve.add_argument("--population", type=int, default=200, help="种群大小（默认 200）")
    p_gp_evolve.add_argument("--generations", type=int, default=50, help="最大代数（默认 50）")
    p_gp_evolve.add_argument("--days", type=int, default=750, help="回溯天数（默认 750，GAP-S08 长窗口）")
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

    # GAP-L402: 实盘反馈回流（LiveFeedbackRecord 契约导入 + 实盘 IC 对比）
    p_fb_import = fb_sub.add_parser("import", help="导入实盘反馈记录（CSV，GAP-L402）")
    p_fb_import.add_argument("csv_path", help="CSV 路径（列名=LiveFeedbackRecord 字段）")
    p_fb_import.add_argument("--db", default=None, help="DuckDB 文件路径（缺省 JSONL 落盘）")
    p_fb_import.set_defaults(func=_cmd_feedback_import)

    p_fb_live_ic = fb_sub.add_parser("live-ic", help="实盘 IC vs 回测 IC 对比报告（GAP-L402）")
    p_fb_live_ic.add_argument("--backtest-ic", default=None, help="回测 IC JSON 路径 {factor_id: ic}")
    p_fb_live_ic.add_argument("--db", default=None, help="DuckDB 文件路径（读取 feedback_live 表）")
    p_fb_live_ic.set_defaults(func=_cmd_feedback_live_ic)

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
    p_data_fuse.add_argument(
        "--strategy",
        type=str,
        default="MEDIAN",
        choices=["MEDIAN", "MEAN", "WEIGHTED", "HIERARCHICAL", "TRIMMED_MEAN"],
        help="融合策略（默认 MEDIAN）",
    )
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
