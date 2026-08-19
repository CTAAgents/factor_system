"""
fts/scheduler/jobs.py — FTS 定时任务的工作函数

每个函数是一个独立的定时任务入口，供 SchedulerEngine 调度执行。
所有函数签名: () -> None（无参数，日志记录结果）。

HARNESS §trace_id 全链路: 每个任务执行生成独立 trace_id。
HARNESS §降级/熔断: 捕获所有异常，日志记录但不抛出。

用法:
    from fts.scheduler.jobs import l1_meta_loop_job
    l1_meta_loop_job()
"""

from __future__ import annotations

import logging
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _get_l2_panel_days() -> int:
    """L2 训练池面板回溯天数（GAP-133 参数化，config l2_panel_days，env FTS_L2_PANEL_DAYS 可覆盖）。

    Returns:
        面板回溯天数（默认 750）
    """
    from fts.config import get_config

    return get_config().l2_panel_days


# ── L1 Meta-Loop — 每日 08:30 知识补给 + 种子注入 ─────────


def _market_gate(market: str, *, task: str) -> bool:
    """全局市场门控（FTS_DEFAULT_MARKET 运行时全局切换，v2.104.0+101）。

    任务专属市场与全局默认市场不一致时 no-op（记日志跳过），使 TRAE Schedule
    外部任务在不匹配市场下自动空转，无需逐任务注册调整。

    Args:
        market: 任务专属市场（"futures"/"energy"）
        task: 任务名（日志标识）

    Returns:
        True=继续执行；False=全局市场不匹配，已记录跳过日志
    """
    from fts.config import get_config

    global_market = get_config().default_market
    if global_market == market:
        return True
    logger.info(
        "[%s] 全局市场=%s 与任务市场=%s 不匹配，跳过（FTS_DEFAULT_MARKET 全局切换）",
        task,
        global_market,
        market,
    )
    return False


def _global_market() -> str:
    """解析全局市场（FTS_DEFAULT_MARKET env → cfg.default_market，默认 futures）。"""
    from fts.config import get_config

    return get_config().default_market


def l1_meta_loop_job(market: str | None = None) -> None:
    """执行 L1 Meta-Loop（每日知识补给 + Bootstrapping + 种子注入）。

    Args:
        market: 市场类型（None=跟随全局 FTS_DEFAULT_MARKET；energy 走能源链独立 L1 输出，GAP-121 2026-08-15）。
    """
    if market is None:
        market = _global_market()
    trace_id = f"fts.l1.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L1] Meta-Loop 启动 trace_id=%s market=%s", trace_id, market)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.meta_loop import MetaLoop, _make_web_collector
        from fts.llm import get_llm_client
        from fts.config import get_config

        cfg = get_config()

        kwargs: dict[str, Any] = {}
        if market == "energy":
            from fts.data_futures import (
                ENERGY_CHAIN_L1_DEBATES_DIR,
                ENERGY_CHAIN_L1_INJECT_DIR,
                ENERGY_CHAIN_L1_MEMORY_DIR,
                ENERGY_CHAIN_L1_POOL_PATH,
            )

            kwargs = {
                "memory_dir": cfg.memory_dir + "/" + ENERGY_CHAIN_L1_MEMORY_DIR.removeprefix("memory/"),
                "factor_pool_path": PROJECT_ROOT / ENERGY_CHAIN_L1_POOL_PATH,
                "inject_dir": PROJECT_ROOT / ENERGY_CHAIN_L1_INJECT_DIR,
                "debates_dir": PROJECT_ROOT / ENERGY_CHAIN_L1_DEBATES_DIR,
            }

        loop = MetaLoop(
            memory_dir=kwargs.get("memory_dir", cfg.memory_dir + "/meta_loop"),
            llm_client=get_llm_client(),
            market=market,
            factor_pool_path=kwargs.get("factor_pool_path", "memory/knowledge/factors/factor_pool.json"),
            inject_dir=kwargs.get("inject_dir", "memory/knowledge/factors/l1_injected"),
            debates_dir=kwargs.get("debates_dir", "memory/debates"),
            # plans/41 A1: 接入 web_collector 感知（市场快照注入 bootstrap prompt）
            web_collector=_make_web_collector(market=market),
        )
        result = loop.run()
        logger.info("[L1] 完成: status=%s injected=%d", result.status, len(result.injected_candidate_ids))
    except Exception as e:
        logger.error("[L1] 运行失败: %s", e, exc_info=True)


# ── L2 Evolution Loop — 工作日 04:00 小预算 / 周六 04:00 大预算（45 计划，先种子后演化）──


def _run_l2_evolution(max_generation: int, tag: str) -> None:
    """执行 L2 Evolution Loop（因子演化 — 期货横截面）。

    Args:
        max_generation: 演化代数（工作日小预算 ≈10 / 周末大预算 ≈50）
        tag: 运行标识（weekday / weekend），用于 trace_id 与日志
    """
    trace_id = f"fts.l2.{tag}.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L2][%s] Evolution Loop 启动 trace_id=%s", tag, trace_id)
    if not _market_gate("futures", task="L2演化"):
        return

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.evolution_loop import EvolutionLoop
        from fts.factor_engine.verifier import FactorVerifier
        from fts.factor_engine.seed_pool import SeedPool
        from fts.factor_engine.contracts import DEFAULT_BUDGET_CONFIG
        from fts.llm import get_llm_client
        from fts.config import get_config
        from fts.data import FTSDataProvider
        from fts.data_futures import FUTURES_STRATIFIED_SUBSET, FUTURES_HOLDOUT

        cfg = get_config()

        # 准备期货横截面数据（分层训练集 + 排除盲测品种池）
        train_symbols = [s for s in FUTURES_STRATIFIED_SUBSET if s not in FUTURES_HOLDOUT]
        if len(train_symbols) < 10:
            logger.error("[L2][%s] 训练品种不足 (排除盲测后仅 %d 个)", tag, len(train_symbols))
            return
        logger.info("[L2][%s] 分层训练品种: %d 个 (排除 %d 个盲测品种)", tag, len(train_symbols), len(FUTURES_HOLDOUT))
        provider = FTSDataProvider()
        panel, common_dates = provider.get_futures_panel(
            symbols=train_symbols,
            days=_get_l2_panel_days(),
            trace_id=trace_id,
        )
        if not panel:
            logger.error("[L2][%s] 无期货数据，跳过", tag)
            return

        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]
        closes = data_df["close"].values
        fwd_ret = __import__("numpy").zeros(len(closes))
        if len(closes) > 5:
            fwd_ret[:-5] = (closes[5:] - closes[:-5]) / __import__("numpy").maximum(closes[:-5], 1e-10)

        llm = get_llm_client()
        seed_pool = SeedPool(market=cfg.default_market)
        verifier = FactorVerifier()

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=cfg.elite_dir,
            memory_dir=cfg.memory_dir + "/evolution",
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=30,
            cross_section_data=panel,
            cross_section_dates=common_dates,
        )
        loop.budget = DEFAULT_BUDGET_CONFIG.copy()
        loop.budget["max_generation"] = max_generation

        result = loop.run(max_generation=max_generation)
        logger.info("[L2][%s] 完成: status=%s elite=%d", tag, result.status, len(result.elite_factor_ids))
    except Exception as e:
        logger.error("[L2][%s] 运行失败: %s", tag, e, exc_info=True)


def l2_evolution_weekday_job() -> None:
    """L2 演化（工作日 04:00，小预算 max_generation≈10，45 计划调度基线）。"""
    _run_l2_evolution(10, "weekday")


def l2_evolution_weekend_job() -> None:
    """L2 演化（周六 04:00，大预算 max_generation≈50，45 计划调度基线）。"""
    _run_l2_evolution(50, "weekend")


def l2_evolution_loop_job() -> None:
    """兼容入口：默认工作日小预算（原 00:00 任务名，45.6 调度基线后由两个新入口替代）。"""
    _run_l2_evolution(10, "weekday")


# ── L2 种子评估晋升 — 每日 02:00（45 计划候选①，先种子后演化）──


def l2_seed_promotion_job() -> None:
    """执行 L2 种子评估晋升（每日 02:00，L1 00:00 注入后消费）。

    45 计划候选①：种子评估从 run() 拆出独立调度——L1 注入的种子 + 种子池
    评估晋升入 elite 池，晋升结果当日被 L2 演化（04:00）消费为父因子。
    本任务仅运行 run_seed_stage，不重置演化状态计数器（不调用 mark_running）。
    """
    trace_id = f"fts.l2_seed.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L2种子] 启动 trace_id=%s", trace_id)
    if not _market_gate("futures", task="L2种子"):
        return

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.evolution_loop import EvolutionLoop
        from fts.factor_engine.verifier import FactorVerifier
        from fts.factor_engine.seed_pool import SeedPool
        from fts.llm import get_llm_client
        from fts.config import get_config
        from fts.data import FTSDataProvider
        from fts.data_futures import FUTURES_STRATIFIED_SUBSET, FUTURES_HOLDOUT

        cfg = get_config()

        # 准备期货横截面数据（与 L2 演化同口径：分层训练集 + 排除盲测品种池）
        train_symbols = [s for s in FUTURES_STRATIFIED_SUBSET if s not in FUTURES_HOLDOUT]
        if len(train_symbols) < 10:
            logger.error("[L2种子] 训练品种不足 (排除盲测后仅 %d 个)", len(train_symbols))
            return
        provider = FTSDataProvider()
        panel, common_dates = provider.get_futures_panel(
            symbols=train_symbols,
            days=_get_l2_panel_days(),
            trace_id=trace_id,
        )
        if not panel:
            logger.error("[L2种子] 无期货数据，跳过")
            return

        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]
        closes = data_df["close"].values
        fwd_ret = __import__("numpy").zeros(len(closes))
        if len(closes) > 5:
            fwd_ret[:-5] = (closes[5:] - closes[:-5]) / __import__("numpy").maximum(closes[:-5], 1e-10)

        llm = get_llm_client()
        seed_pool = SeedPool(market=cfg.default_market)
        verifier = FactorVerifier()

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=cfg.elite_dir,
            memory_dir=cfg.memory_dir + "/evolution",
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=30,
            cross_section_data=panel,
            cross_section_dates=common_dates,
        )

        # 仅读状态（不 mark_running 重置演化计数器），避免污染夜间演化统计
        state = loop.state_manager.load_or_init(loop.budget.get("nightly_token_limit", 1_000_000))
        elite_ids: list[str] = []
        promoted, _seed_corr, parent_seeds = loop.run_seed_stage(
            trace_id,
            state,
            elite_ids,
        )
        logger.info(
            "[L2种子] 完成: 晋升=%d elite=%d 父因子=%d (trace_id=%s)",
            promoted,
            len(elite_ids),
            len(parent_seeds),
            trace_id,
        )
    except Exception as e:
        logger.error("[L2种子] 运行失败: %s", e, exc_info=True)


def l2_seed_promotion_energy_job() -> None:
    """执行能化产业链 L2 种子评估晋升（每日 02:00，L1 energy 00:00 注入后消费）。

    45 计划候选① 的 energy 链路由（GAP-121 独立工作流）：L1 energy 注入候选 +
    energy 种子池评估晋升入 energy elite 池（elite_dir=energy_chain_elite、
    factor_db=factor_catalog_energy.duckdb），晋升结果当日被 energy L2 演化
    （04:00，`fts.cli evolution run --chain energy`）消费为父因子。
    仅运行 run_seed_stage，不重置演化状态计数器（不调用 mark_running）。
    """
    trace_id = f"fts.l2_seed_energy.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L2种子][energy] 启动 trace_id=%s", trace_id)
    if not _market_gate("energy", task="L2种子[energy]"):
        return

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.evolution_loop import EvolutionLoop
        from fts.factor_engine.verifier import FactorVerifier
        from fts.factor_engine.contracts import FUTURES_VERIFIER_CONFIG
        from fts.factor_engine.seed_pool import SeedPool
        from fts.llm import get_llm_client
        from fts.config import get_config
        from fts.data import FTSDataProvider
        from fts.data_futures import (
            ENERGY_CHAIN_L1_INJECT_DIR,
            ENERGY_CHAIN_L1_POOL_PATH,
            ENERGY_CHAIN_MARKET,
            ENERGY_CHAIN_SYMBOLS,
        )

        cfg = get_config()

        # energy 链训练链（12 化工品种，SSOT: config/futures_universe.yaml，data_futures 内置兜底）
        chain_symbols = list(ENERGY_CHAIN_SYMBOLS)
        if len(chain_symbols) < 10:
            logger.error("[L2种子][energy] 训练品种不足 (仅 %d 个)", len(chain_symbols))
            return
        provider = FTSDataProvider()
        panel, common_dates = provider.get_futures_panel(
            symbols=chain_symbols,
            days=_get_l2_panel_days(),
            trace_id=trace_id,
        )
        if not panel:
            logger.error("[L2种子][energy] 无期货数据，跳过")
            return

        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]
        closes = data_df["close"].values
        fwd_ret = __import__("numpy").zeros(len(closes))
        if len(closes) > 5:
            fwd_ret[:-5] = (closes[5:] - closes[:-5]) / __import__("numpy").maximum(closes[:-5], 1e-10)

        llm = get_llm_client()
        seed_pool = SeedPool(market="energy")
        # energy 链保持期货验证配置（与 CLI `evolution run --chain energy` 口径一致）
        verifier = FactorVerifier(FUTURES_VERIFIER_CONFIG)

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=cfg.get_elite_dir(ENERGY_CHAIN_MARKET),
            memory_dir=cfg.memory_dir + "/evolution/energy_chain",
            inject_dir=PROJECT_ROOT / ENERGY_CHAIN_L1_INJECT_DIR,
            factor_pool_path=PROJECT_ROOT / ENERGY_CHAIN_L1_POOL_PATH,
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=30,
            cross_section_data=panel,
            cross_section_dates=common_dates,
            market=ENERGY_CHAIN_MARKET,
        )

        # 仅读状态（不 mark_running 重置演化计数器），避免污染 energy 演化统计
        state = loop.state_manager.load_or_init(loop.budget.get("nightly_token_limit", 1_000_000))
        elite_ids: list[str] = []
        promoted, _seed_corr, parent_seeds = loop.run_seed_stage(
            trace_id,
            state,
            elite_ids,
        )
        logger.info(
            "[L2种子][energy] 完成: 晋升=%d elite=%d 父因子=%d (trace_id=%s)",
            promoted,
            len(elite_ids),
            len(parent_seeds),
            trace_id,
        )
    except Exception as e:
        logger.error("[L2种子][energy] 运行失败: %s", e, exc_info=True)


# ── L3 Portfolio Loop — 工作日每日 19:00 组合权重重算（GAP-072 与信号管道解绑）───


def l3_portfolio_loop_job() -> None:
    """执行 L3 Portfolio Loop（期货因子筛选 + 信号合成 + Verifier 校验）。
    显式走期货路径：elite_dir=futures_elite_dir + market="futures"，与
    CLI `fts portfolio run --universe futures` 对齐（此前误用股票 elite 目录，
    与下游期货信号管道不一致，v2.73.0 修复）。

    权重计算模式（portfolio_loop.py）:
        - equal_weight: 等权 1/N（默认，v2.103.0+23；可选 --enable-pca 以 PCA 载荷权重
          替换均匀等权，v2.103.0+24）
        - elastic_net: Elastic Net 截面回归（CSI300 面板，L1+L2 自动变量选择）
        - sharpe_weight: 按 Sharpe 比率归一化加权

    与期货信号管道解绑（GAP-072）：本任务仅重算组合权重，不触发信号管道；
    信号由独立每日任务（futures_signal_pipeline，工作日 20:00）生成。
    """
    trace_id = f"fts.l3.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L3] Portfolio Loop 启动 trace_id=%s", trace_id)
    if not _market_gate("futures", task="L3组合"):
        return

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.portfolio_loop import PortfolioLoop
        from fts.config import get_config

        cfg = get_config()

        loop = PortfolioLoop(
            elite_dir=cfg.futures_elite_dir,
            memory_dir=cfg.memory_dir + "/portfolio",
            market="futures",
        )
        result = loop.run()
        logger.info(
            "[L3] 完成: status=%s retained=%d sharpe=%.4f",
            result.status,
            result.n_factors_retained,
            result.combo_sharpe,
        )

    except Exception as e:
        logger.error("[L3] 运行失败: %s", e, exc_info=True)


# ── 期货信号管道 — 工作日每日 20:00（独立调度，与 L3 解绑，GAP-072）──


def _run_futures_signal_pipeline() -> None:
    """生成期货信号报告（独立每日任务；权重周五重算，其余日冻结复用快照）。

    使用全量商品期货池（--universe all）：
    - 覆盖 FUTURES_SUBSET 中所有非僵尸品种（剔除停更/陈旧品种后参与排名）
    - 报告输出品种中文名称、主力合约代码、盘中实时价

    权重计算方法（v2.105.0 起 — L3 组合权威源）:
    - 因子选择与基础权重: 由 L3 组合层负责（factor_weights.json）
    - Regime 调整: 信号管道按市场制度做因子权重档位缩放
    - 输出: 多空双向信号排名 → reports/{date}/futures_signals_*.md
    """
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from scripts.futures_signal_pipeline import main

        exit_code = main(max_symbols=82, days=120, universe="all")
        logger.info("[信号管道] 完成: exit_code=%d", exit_code)
    except Exception as e:
        logger.error("[信号管道] 失败: %s", e, exc_info=True)


def futures_signal_pipeline_job() -> None:
    """独立的期货信号管道任务（可单独调度）。"""
    trace_id = f"fts.signal.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[信号管道] 启动 trace_id=%s", trace_id)
    if not _market_gate("futures", task="信号管道"):
        return
    _run_futures_signal_pipeline()


# ── 健康检查 — 每 10 分钟 ────────────────────────────────


def health_check_job() -> None:
    """健康检查：监控所有循环状态。"""
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.monitor import check_all_status

        report = check_all_status()
        if not report.healthy:
            logger.warning("[健康检查] 不健康: %s", report)
        else:
            logger.info("[健康检查] 正常")
    except Exception as e:
        logger.error("[健康检查] 失败: %s", e, exc_info=True)


# ── L2 批量挖掘 — 周日 06:00（45 计划候选②，周末错峰）──


def l2_batch_mining_job() -> None:
    """执行 L2 批量挖掘（周日 06:00，CPU 密集错峰）。

    45 计划候选②：batch 批量漏斗从 run() 拆出独立调度——读 elite 池选父 →
    BatchMiner 批量生成（同父多后代，方法轮换）→ 并行粗筛 → 通过者逐个走
    准入链。熔断隔离：本任务经 run_batch_stage（保存/恢复 _consecutive_low_ic），
    batch 失败不污染工作日/周末 L2 演化的熔断状态。
    """
    trace_id = f"fts.l2_batch.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L2批量] 启动 trace_id=%s", trace_id)
    if not _market_gate("futures", task="L2批量"):
        return

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.evolution_loop import EvolutionLoop
        from fts.factor_engine.verifier import FactorVerifier
        from fts.factor_engine.seed_pool import SeedPool
        from fts.llm import get_llm_client
        from fts.config import get_config
        from fts.data import FTSDataProvider
        from fts.data_futures import FUTURES_STRATIFIED_SUBSET, FUTURES_HOLDOUT

        cfg = get_config()

        # 准备期货横截面数据（与 L2 演化同口径）
        train_symbols = [s for s in FUTURES_STRATIFIED_SUBSET if s not in FUTURES_HOLDOUT]
        if len(train_symbols) < 10:
            logger.error("[L2批量] 训练品种不足 (排除盲测后仅 %d 个)", len(train_symbols))
            return
        provider = FTSDataProvider()
        panel, common_dates = provider.get_futures_panel(
            symbols=train_symbols,
            days=_get_l2_panel_days(),
            trace_id=trace_id,
        )
        if not panel:
            logger.error("[L2批量] 无期货数据，跳过")
            return

        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]
        closes = data_df["close"].values
        fwd_ret = __import__("numpy").zeros(len(closes))
        if len(closes) > 5:
            fwd_ret[:-5] = (closes[5:] - closes[:-5]) / __import__("numpy").maximum(closes[:-5], 1e-10)

        llm = get_llm_client()
        seed_pool = SeedPool(market=cfg.default_market)
        verifier = FactorVerifier()

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=cfg.elite_dir,
            memory_dir=cfg.memory_dir + "/evolution",
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=30,
            cross_section_data=panel,
            cross_section_dates=common_dates,
        )

        # 读 elite 池选父（UCT 树搜索；无父因子则跳过）
        parent_seeds = loop._load_elite_parent_factors()
        if not parent_seeds:
            logger.info("[L2批量] elite 池无父因子，跳过")
            return
        parent = loop._select_parent_uct(parent_seeds)

        state = loop.state_manager.load_or_init(loop.budget.get("nightly_token_limit", 1_000_000))
        elite_ids: list[str] = []
        seed_correlations = loop._load_seed_correlation_index()

        ok = loop.run_batch_stage(
            parent,
            0,  # 独立任务从第 0 代批量
            trace_id,
            state,
            elite_ids,
            seed_correlations,
        )
        logger.info("[L2批量] 完成: 晋升=%s elite=%d (trace_id=%s)", ok, len(elite_ids), trace_id)
    except Exception as e:
        logger.error("[L2批量] 运行失败: %s", e, exc_info=True)


def l2_batch_mining_energy_job() -> None:
    """执行能化产业链 L2 批量挖掘（周日 06:00，CPU 密集错峰，45 计划候选②）。

    energy 链路由（GAP-121 独立工作流）：读 energy elite 池选父（UCT）→
    BatchMiner 批量生成（同父多后代，方法轮换）→ 并行粗筛 → 通过者逐个走准入链。
    熔断隔离：本任务经 run_batch_stage（保存/恢复 _consecutive_low_ic），
    batch 失败不污染工作日/周末 energy L2 演化的熔断状态。
    """
    trace_id = f"fts.l2_batch_energy.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L2批量][energy] 启动 trace_id=%s", trace_id)
    if not _market_gate("energy", task="L2批量[energy]"):
        return

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.evolution_loop import EvolutionLoop
        from fts.factor_engine.verifier import FactorVerifier
        from fts.factor_engine.contracts import FUTURES_VERIFIER_CONFIG
        from fts.factor_engine.seed_pool import SeedPool
        from fts.llm import get_llm_client
        from fts.config import get_config
        from fts.data import FTSDataProvider
        from fts.data_futures import (
            ENERGY_CHAIN_L1_INJECT_DIR,
            ENERGY_CHAIN_L1_POOL_PATH,
            ENERGY_CHAIN_MARKET,
            ENERGY_CHAIN_SYMBOLS,
        )

        cfg = get_config()

        # energy 链训练链（12 化工品种，SSOT: config/futures_universe.yaml，data_futures 内置兜底）
        chain_symbols = list(ENERGY_CHAIN_SYMBOLS)
        if len(chain_symbols) < 10:
            logger.error("[L2批量][energy] 训练品种不足 (仅 %d 个)", len(chain_symbols))
            return
        provider = FTSDataProvider()
        panel, common_dates = provider.get_futures_panel(
            symbols=chain_symbols,
            days=_get_l2_panel_days(),
            trace_id=trace_id,
        )
        if not panel:
            logger.error("[L2批量][energy] 无期货数据，跳过")
            return

        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]
        closes = data_df["close"].values
        fwd_ret = __import__("numpy").zeros(len(closes))
        if len(closes) > 5:
            fwd_ret[:-5] = (closes[5:] - closes[:-5]) / __import__("numpy").maximum(closes[:-5], 1e-10)

        llm = get_llm_client()
        seed_pool = SeedPool(market="energy")
        # energy 链保持期货验证配置（与 CLI `evolution run --chain energy` 口径一致）
        verifier = FactorVerifier(FUTURES_VERIFIER_CONFIG)

        loop = EvolutionLoop(
            data=data_df,
            forward_returns=fwd_ret,
            elite_dir=cfg.get_elite_dir(ENERGY_CHAIN_MARKET),
            memory_dir=cfg.memory_dir + "/evolution/energy_chain",
            inject_dir=PROJECT_ROOT / ENERGY_CHAIN_L1_INJECT_DIR,
            factor_pool_path=PROJECT_ROOT / ENERGY_CHAIN_L1_POOL_PATH,
            llm_client=llm,
            seed_pool=seed_pool,
            verifier=verifier,
            n_trials_micro=30,
            cross_section_data=panel,
            cross_section_dates=common_dates,
            market=ENERGY_CHAIN_MARKET,
        )

        # 读 energy elite 池选父（UCT 树搜索；无父因子则跳过）
        parent_seeds = loop._load_elite_parent_factors()
        if not parent_seeds:
            logger.info("[L2批量][energy] elite 池无父因子，跳过")
            return
        parent = loop._select_parent_uct(parent_seeds)

        # 仅读状态（不 mark_running 重置演化计数器），避免污染 energy 演化统计
        state = loop.state_manager.load_or_init(loop.budget.get("nightly_token_limit", 1_000_000))
        elite_ids: list[str] = []
        seed_correlations = loop._load_seed_correlation_index()

        ok = loop.run_batch_stage(
            parent,
            0,  # 独立任务从第 0 代批量
            trace_id,
            state,
            elite_ids,
            seed_correlations,
        )
        logger.info("[L2批量][energy] 完成: 晋升=%s elite=%d (trace_id=%s)", ok, len(elite_ids), trace_id)
    except Exception as e:
        logger.error("[L2批量][energy] 运行失败: %s", e, exc_info=True)


# ── L2 周度评审 — 每周日 10:00（45 计划：评审周度化，替代月度衰减 + run() 每日调用）───


def _review_gate_weekly(market: str, trace_id: str) -> dict[str, Any]:
    """评审质检阀门周度巡检（v2.104.0+89，L2→L3 独立阀门模块功能 2）。

    周末定期巡检：
      Step C-1  review_l3_pool 复核 factor_reviews.approved（L3 池）因子——
                不合格/质检失效撤销 approved，退回 L2 冷却池；
      Step C-2  list_pending 待审因子按完整质检门禁机审兜底（review_inplace，
                宁缺毋滥：质检记录缺失转人审，不流入 L3）。
    """
    from fts.factor_engine.factor_inspector import FactorReviewWorkflow

    wf = FactorReviewWorkflow(market=market)
    pool_res = wf.review_l3_pool(market=market)
    demoted = pool_res.get("demoted", [])
    pending = wf.list_pending(market=market, limit=200)
    reviewed = 0
    for item in pending:
        fid = item.get("factor_id")
        if fid:
            wf.review_inplace(fid)
            reviewed += 1
    logger.info(
        "[L2评审][%s] Step C 阀门巡检完成: L3池扫描=%d 退回=%d pending机审=%d (trace_id=%s)",
        market,
        pool_res.get("scanned", 0),
        len(demoted),
        reviewed,
        trace_id,
    )
    return {"market": market, "scanned": pool_res.get("scanned", 0), "demoted": demoted, "auto_reviewed": reviewed}


def l2_review_job() -> None:
    """L2 周度评审：精英重审 + 衰减评估 + 自动淘汰（45 计划候选③）。

    关联设计: A.2 因子衰减追踪（EliteFactorTracker.run_monthly_evaluation）；
    plans/45 §5.3 — 由月度衰减任务周度化而来（run() 不再每日调用
    _run_periodic_factor_review，评审职责统一收敛到本任务）。
    """
    trace_id = f"fts.l2_review.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L2评审] 启动 trace_id=%s", trace_id)
    if not _market_gate("futures", task="L2评审"):
        return

    # ---- Step A: 新标准全量重审（与月度衰减合并） ----
    if os.getenv("FTS_MONTHLY_REAUDIT_ENABLED", "1") == "1":
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from fts.monitor.reaudit import run_reaudit

            rep = run_reaudit(market="futures", trace_id=f"{trace_id}.reaudit", apply=True, out_json=True)
            logger.info(
                "[L2评审] Step A 新标准重审完成: retain=%d shadow=%d retire=%d error=%d (total=%d)",
                rep.counts.get("retain", 0),
                rep.counts.get("shadow", 0),
                rep.counts.get("retire", 0),
                rep.counts.get("error", 0),
                rep.total,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[L2评审] Step A 重审失败（不阻断衰减评估）: %s", e, exc_info=True)
    else:
        logger.info("[L2评审] Step A 新标准重审已关闭（FTS_MONTHLY_REAUDIT_ENABLED=0）")

    # ---- Step B: 衰减评估（原有逻辑） ----

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.monitor.elite_tracker import EliteFactorTracker, AutoRetireManager
        from fts.config import get_config

        cfg = get_config()
        tracker = EliteFactorTracker(tracking_dir=f"{cfg.memory_dir}/tracking")
        report = tracker.run_monthly_evaluation()
        logger.info("[L2评审] 衰减评估完成: %s", report)

        # 同步衰减计数到 Prometheus 指标
        try:
            from fts.monitor.prometheus_metrics import metrics_registry

            snapshots = tracker.list_all()
            counts = {"active": 0, "decaying": 0, "critical_decay": 0, "deprecated": 0}
            for snap in snapshots:
                status = snap.get("status", "active")
                counts[status] = counts.get(status, 0) + 1
            metrics_registry.update_decay_counts(
                active=counts.get("active", 0),
                decaying=counts.get("decaying", 0),
                critical=counts.get("critical_decay", 0),
                deprecated=counts.get("deprecated", 0),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("[L2评审] 指标同步失败: %s", e)

        # 自动淘汰（EliteFactorTracker 快照标记 retired）
        retire_mgr = AutoRetireManager(tracker)
        retired = retire_mgr.run()
        if retired:
            logger.warning("[L2评审] 快照标记淘汰: %d 个因子", len(retired))

            # 同步淘汰到 DuckDB + JSON 文件（主流程中真正生效）
            from fts.factor_engine.factor_db import FactorRepository

            # 期货分库 repo：因子淘汰同步到期货因子库（股票剥离后主系统仅期货）
            repo = FactorRepository(market="futures")
            retired_count = 0
            for fid in retired:
                factor = repo.get_factor(fid)
                if factor:
                    mkt = factor.get("market", "futures")
                    elite_dir = cfg.get_elite_dir(mkt)
                else:
                    elite_dir = cfg.get_elite_dir("futures")
                if repo.retire_factor(fid, reason="L2周度评审自动淘汰", elite_dir=elite_dir):
                    retired_count += 1
            logger.warning("[L2评审] 淘汰已同步至 DuckDB + JSON: %d/%d 个因子", retired_count, len(retired))

        # ---- Step C: 评审质检阀门周度巡检（v2.104.0+89，功能 2） ----
        # L3 池巡检 + pending 因子机审兜底
        try:
            _review_gate_weekly("futures", trace_id)
        except Exception as e:  # noqa: BLE001
            logger.error("[L2评审] Step C 阀门巡检失败（不阻断）: %s", e, exc_info=True)
    except Exception as e:
        logger.error("[L2评审] 失败: %s", e, exc_info=True)


def l2_review_energy_job() -> None:
    """执行能化产业链 L2 周度评审（周日 10:00，45 计划候选③ energy 链路由）。

    energy 链独立评审（GAP-121 独立工作流）：
      Step A 新标准准入重审（market=energy，因子库 factor_catalog_energy.duckdb、
            elite_dir=energy_chain_elite）；
      Step B 衰减评估/自动淘汰（tracking 独立 memory/tracking/energy，与期货隔离），
            退役同步回写 energy 因子库 DuckDB + JSON。
    """
    trace_id = f"fts.l2_review_energy.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L2评审][energy] 启动 trace_id=%s", trace_id)
    if not _market_gate("energy", task="L2评审[energy]"):
        return

    # ---- Step A: 新标准全量重审（market=energy） ----
    if os.getenv("FTS_MONTHLY_REAUDIT_ENABLED", "1") == "1":
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from fts.monitor.reaudit import run_reaudit

            rep = run_reaudit(market="energy", trace_id=f"{trace_id}.reaudit", apply=True, out_json=True)
            logger.info(
                "[L2评审][energy] Step A 新标准重审完成: retain=%d shadow=%d retire=%d error=%d (total=%d)",
                rep.counts.get("retain", 0),
                rep.counts.get("shadow", 0),
                rep.counts.get("retire", 0),
                rep.counts.get("error", 0),
                rep.total,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[L2评审][energy] Step A 重审失败（不阻断衰减评估）: %s", e, exc_info=True)
    else:
        logger.info("[L2评审][energy] Step A 新标准重审已关闭（FTS_MONTHLY_REAUDIT_ENABLED=0）")

    # ---- Step B: 衰减评估（energy 独立 tracking） ----
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.monitor.elite_tracker import EliteFactorTracker, AutoRetireManager
        from fts.config import get_config

        cfg = get_config()
        tracker = EliteFactorTracker(tracking_dir=f"{cfg.memory_dir}/tracking/energy")
        report = tracker.run_monthly_evaluation()
        logger.info("[L2评审][energy] 衰减评估完成: %s", report)

        # 自动淘汰（EliteFactorTracker 快照标记 retired）
        retire_mgr = AutoRetireManager(tracker)
        retired = retire_mgr.run()
        if retired:
            logger.warning("[L2评审][energy] 快照标记淘汰: %d 个因子", len(retired))

            # 同步淘汰到 DuckDB + JSON（energy 因子库，主流程中真正生效）
            from fts.factor_engine.factor_db import FactorRepository

            repo = FactorRepository(market="energy")
            retired_count = 0
            for fid in retired:
                if repo.retire_factor(
                    fid,
                    reason="L2周度评审自动淘汰[energy]",
                    elite_dir=cfg.get_elite_dir("energy"),
                ):
                    retired_count += 1
            logger.warning("[L2评审][energy] 淘汰已同步至 DuckDB + JSON: %d/%d 个因子", retired_count, len(retired))

        # ---- Step C: 评审质检阀门周度巡检（v2.104.0+89，功能 2） ----
        # L3 池巡检 + pending 因子机审兜底（energy 库 factor_catalog_energy.duckdb）
        try:
            _review_gate_weekly("energy", trace_id)
        except Exception as e:  # noqa: BLE001
            logger.error("[L2评审][energy] Step C 阀门巡检失败（不阻断）: %s", e, exc_info=True)
    except Exception as e:
        logger.error("[L2评审][energy] 失败: %s", e, exc_info=True)


def l2_energy_qa_review_job() -> None:
    """执行能化链评审+质检统一管道（周日 10:00，方案 A，宁严勿松）。

    合并 l2_review_energy_job 与 energy 链定期质检三路检测为单一管道：
    [0]面板→[1]重审→[2]退化检测落库→[3]生命周期收口(冷却期30日自动回归)→[4]Inspector→[5]报告。
    灰度：环境变量 FTS_ENERGY_QA_REVIEW_APPLY 默认 "0"（dry-run 安全默认，不落库；
    v2.105.0+18 默认值反转，显式置 1 才落库），apply=True 时落库前强制影子校验
    （与上次基线逐因子判定一致，无基线/判定漂移拒绝落库）。
    """
    trace_id = f"fts.l2_qa_review.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L2评审质检][energy] 启动 trace_id=%s", trace_id)
    if not _market_gate("energy", task="L2评审质检[energy]"):
        return
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.energy_qa_review import EnergyQaReviewConfig, EnergyQaReviewPipeline

        apply = os.getenv("FTS_ENERGY_QA_REVIEW_APPLY", "0") == "1"
        pipe = EnergyQaReviewPipeline(config=EnergyQaReviewConfig(apply=apply))
        result = pipe.run(trace_id=trace_id)
        logger.info(
            "[L2评审质检][energy] 完成 status=%s apply=%s stages=%s",
            result.get("status"),
            apply,
            list((result.get("stages") or {}).keys()),
        )
    except Exception as e:
        logger.error("[L2评审质检][energy] 运行失败: %s", e, exc_info=True)


# ── 逻辑监控 — 每日 22:00（B.2 逻辑审查）───────────────────


def l2_subchain_quality_job(market: str | None = None) -> None:
    """批量子链质量评估（FTS 标准工作流，2026-08-19 沉淀）。

    对所有 active 因子批量计算逐品种 IC → 子链画像 → 落库 subchain_factor_quality
    质量矩阵，补齐画像覆盖，供 L2 评审质检退化检测（单元粒度）与 L3 子链差异化
    调制消费。三门槛经 subchain_profile.SubchainProfileConfig 参数化
    （min_chain_ic=0.02，v2.105.0+16 由 0.10 校准）。无有效链因子不自动降级，
    metadata 标记 pending_validation 交进一步验证。

    Args:
        market: 市场（None=跟随全局 FTS_DEFAULT_MARKET，默认 energy）
    """
    if market is None:
        market = _global_market()
    trace_id = f"fts.subchain_eval.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[子链评估][%s] 启动 trace_id=%s", market, trace_id)
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.subchain_eval import SubchainEvalConfig, SubchainEvalRunner

        runner = SubchainEvalRunner(config=SubchainEvalConfig(market=market))
        result = runner.run(trace_id=trace_id)
        logger.info(
            "[子链评估][%s] 完成 status=%s factors=%d rows=%d no_effective=%d",
            market,
            result.get("status"),
            result.get("factors_total"),
            result.get("rows_saved"),
            len(result.get("no_effective_chains") or []),
        )
    except Exception as e:
        logger.error("[子链评估][%s] 运行失败: %s", market, e, exc_info=True)


def logic_monitor_job() -> None:
    """逻辑监控：对精英因子执行行为漂移、极端预测、换月日异常检测。

    从因子数据库加载活跃精英因子，逐个执行 LogicMonitor.run()，
    生成监控报告并记录日志。

    市场口径（v2.104.0+103）：跟随全局 FTS_DEFAULT_MARKET（默认 energy 能化链，
    监控 factor_catalog_energy；futures 时监控期货通用库），与因子巡检口径一致。
    """
    market = _global_market()
    trace_id = f"fts.logic.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[逻辑监控] 启动 trace_id=%s market=%s", trace_id, market)
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.monitor.logic_monitor import LogicMonitor
        from fts.factor_engine.factor_db import FactorRepository

        logic = LogicMonitor()

        # 加载活跃精英因子（市场跟随全局：energy 能化链 / futures 期货通用库）
        repo = FactorRepository(market=market)
        conn = repo._get_conn()
        rows = conn.execute("SELECT * FROM factor_catalog WHERE is_elite = 1 AND status = 'active'").fetchall()
        columns = [desc[0] for desc in conn.description]
        all_elite_factors = [dict(zip(columns, row)) for row in rows]

        if not all_elite_factors:
            logger.info("[逻辑监控] 无活跃精英因子，跳过 (trace_id=%s)", trace_id)
            return

        import numpy as np
        import pandas as pd

        drift_count = 0
        extreme_count = 0
        total = len(all_elite_factors)

        for factor in all_elite_factors:
            try:
                factor_id = factor.get("factor_id", "unknown")
                # 构建简化的 FactorProgram 用于检查（params 随因子透传，
                # 避免 code 内 params['window'] 等必传参数 KeyError）
                import json

                from fts.factor_engine.contracts import FactorProgram

                raw_params = factor.get("params")
                params = json.loads(raw_params) if isinstance(raw_params, str) and raw_params else (raw_params or {})
                fp = FactorProgram(
                    factor_id=factor_id,
                    name=factor.get("name", "unknown"),
                    code=factor.get("code", ""),
                    params=params,
                )
                # 用模拟 OHLCV 数据做行为漂移检测（补全 high/low/volume/settle 列，
                # 匹配因子代码的 K 线字段引用，避免 KeyError；settle 为期货结算价，
                # 按主链路代理公式 (H+L+C)/3 近似——TQSDK/TDX_LOCAL 主路径不提供该字段）
                n = 500
                base = 100 + np.cumsum(np.random.randn(n) * 0.5)
                mock_high = base * (1 + np.abs(np.random.randn(n)) * 0.01 + 0.001)
                mock_low = base * (1 - np.abs(np.random.randn(n)) * 0.01 - 0.001)
                mock_data = pd.DataFrame(
                    {
                        "date": pd.date_range("2020-01-01", periods=n, freq="B"),
                        "open": base * (1 + np.random.randn(n) * 0.002),
                        "high": mock_high,
                        "low": mock_low,
                        "close": base,
                        "settle": (mock_high + mock_low + base) / 3.0,
                        "volume": np.abs(np.random.randn(n)) * 1e5 + 1e4,
                        "hold": np.abs(np.random.randn(n)) * 1e6 + 1e5,
                    }
                )
                result = logic.run(fp, mock_data, switch_dates=[])
                if not result.all_healthy:
                    if result.drift.is_drifted:
                        drift_count += 1
                    if result.extreme_prediction.is_alarmed:
                        extreme_count += 1
                    logger.warning(
                        "[逻辑监控] 因子异常: %s drift=%s extreme=%s",
                        factor_id,
                        result.drift.is_drifted,
                        result.extreme_prediction.is_alarmed,
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("[逻辑监控] 因子 %s 检查失败: %s", factor.get("factor_id", "?"), e)

        logger.info(
            "[逻辑监控] 完成: total=%d drift=%d extreme=%d (trace_id=%s)",
            total,
            drift_count,
            extreme_count,
            trace_id,
        )

        # plan54 P0-3: 市场前提监控（面板级，监控前提而非结果——趋势/波动结构是否
        # 仍在激活制度内；仅 energy 市场生效，前提消失告警，失败降级不阻断）
        if market == "energy":
            try:
                from fts.data import FTSDataProvider
                from fts.data_futures import ENERGY_CHAIN_SYMBOLS
                from fts.factor_engine.regime import SectorRegimeSelector
                from fts.monitor.logic_monitor import check_market_premise

                _panel, _ = FTSDataProvider().get_futures_panel(
                    symbols=list(ENERGY_CHAIN_SYMBOLS), days=130, trace_id=trace_id
                )
                _sel = SectorRegimeSelector(lookback_days=60, use_hmm=False)
                _sector_map = {"energy": [s for s in ENERGY_CHAIN_SYMBOLS if s in _panel]}
                _regimes = _sel.detect_all(_panel, sector_map=_sector_map)
                _active = next(iter(_regimes.values()))["regime"] if _regimes else None
                prem = check_market_premise(_panel, _active)
                if not prem.premise_ok:
                    logger.warning("[逻辑监控] 市场前提告警: %s (trace_id=%s)", prem.alert, trace_id)
                else:
                    logger.info(
                        "[逻辑监控] 市场前提健康 regime=%s trend=%.4f vol_pct=%.2f (trace_id=%s)",
                        _active,
                        prem.trend_score,
                        prem.vol_percentile,
                        trace_id,
                    )
            except Exception as e:  # noqa: BLE001 — 前提监控失败降级，不阻断
                logger.warning("[逻辑监控] 市场前提监控失败（降级跳过）: %s", e)
    except Exception as e:
        logger.error("[逻辑监控] 运行失败: %s (trace_id=%s)", e, trace_id)


# ── 因子巡检与降级 — 每日 03:00（B.2 因子退化检测）─────────


def factor_inspector_job() -> None:
    """因子巡检与自动降级：扫描精英因子库，检测退化因子并自动降级。

    调用 FactorInspector.inspect_and_downgrade() 执行巡检，
    阈值默认 -0.2（Sharpe 下降 20% 触发降级）。

    GAP-132（v2.104.0+100）：巡检市场跟随全局 FTS_DEFAULT_MARKET（energy/futures
    两套独立 catalog 各自巡检，v2.104.0+101 起替代固定 energy）；
    且因评估历史不足（每因子仅 1 条评估记录）趋势检测返回 insufficient_data、
    退化检测失效，暂以 dry-run（commit=False）运行，仅输出退化候选不落库，
    待评估历史多期积累、GAP-132 关闭后恢复自动降级（commit=True）。
    """
    trace_id = f"fts.inspector.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[因子巡检] 启动 trace_id=%s", trace_id)
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.factor_inspector import FactorInspector

        market = _global_market()
        inspector = FactorInspector(market=market)
        result = inspector.inspect_and_downgrade(
            market=market,
            threshold=-0.2,
            commit=False,
        )
        summary = result.get("summary", {})
        logger.info(
            "[因子巡检] 完成: audited=%d degraded=%d downgraded=%d deferred_approved=%d skipped=%d errors=%d (trace_id=%s)",
            summary.get("total_audited", 0),
            summary.get("degraded_detected", 0),
            summary.get("downgraded", 0),
            summary.get("deferred_approved", 0),
            summary.get("skipped", 0),
            summary.get("errors", 0),
            trace_id,
        )
    except Exception as e:
        logger.error("[因子巡检] 运行失败: %s (trace_id=%s)", e, trace_id)


# ── 数据质量评估 — 每 5 分钟（B.1）─────────────────────────


def data_quality_eval_job() -> None:
    """数据质量周期评估（B.1）。

    当前对已注册的数据源质量监控器执行 evaluate 并输出日志；
    数据获取由数据源层负责，本任务仅做质量评估与告警检查。
    """
    trace_id = f"fts.dq.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.monitor.http_server import get_data_quality_monitor

        monitor = get_data_quality_monitor()
        if monitor is None:
            logger.info("[数据质量] 无已注册监控器，跳过 (trace_id=%s)", trace_id)
            return
        snapshot = monitor.get_metrics_snapshot()
        logger.info("[数据质量] 评估完成 (trace_id=%s): %s", trace_id, snapshot)
    except Exception as e:
        logger.error("[数据质量] 评估失败: %s (trace_id=%s)", e, trace_id)


# ── 期货多源数据同步 — 工作日 17:30（Phase 14.5）───────────

# ── 数据级质量监控 ─ 每日 04:00（GAP-F06）─────────────


# kline_cache 字段完整性分级（GAP-151，v2.105.0+19）：
# - 核心字段（date/open/high/low/close/volume）：数据不可用 → 跳过（宁缺毋滥，报错级）
# - 增强字段（hold/settle/pre_settle）：走代理降级链（如 hold 20 日滚动均量），告警级
# hold 字段 100% 缺失曾静默被代理填充掩盖——现在代理填充前显式暴露缺口。
KLINE_CORE_FIELDS = ("date", "open", "high", "low", "close", "volume")
KLINE_EXTENDED_FIELDS = ("hold", "settle", "pre_settle")


def _check_kline_field_integrity(df: "object", symbol: str) -> bool:
    """数据契约字段完整性校验（GAP-151 分级）：核心字段不可用返回 False（调用方跳过），
    增强字段缺失仅告警（代理降级链保留但显式暴露）。在代理填充前调用。
    """
    core_missing = [c for c in KLINE_CORE_FIELDS if c not in df.columns or int(df[c].notna().sum()) == 0]
    ext_missing = [c for c in KLINE_EXTENDED_FIELDS if c not in df.columns or int(df[c].notna().sum()) == 0]
    if core_missing:
        logger.error(
            "数据级监控 核心字段缺失[symbol=%s] 缺失/全空列=%s —— 数据不可用，跳过（宁缺毋滥）",
            symbol,
            core_missing,
        )
        return False
    if ext_missing:
        logger.warning(
            "数据级监控 增强字段缺失[symbol=%s] 缺失/全空列=%s（下游走代理值——请优先修复数据源）",
            symbol,
            ext_missing,
        )
    return True


def _read_kline_cache(db_path: Path, symbol: str, limit: int = 120) -> "object | None":
    """从 DuckDB kline_cache 读取单个品种最近 K 线（尽力而为）。

    Args:
        db_path: DuckDB 缓存库路径
        symbol: 品种代码（兼容 RB / RB0 / RB0.SHFE 等变体）
        limit: 最近行数

    Returns:
        pandas DataFrame；库/表/数据缺失时返回 None。
    """
    try:
        import duckdb

        raw_sym = symbol.strip().upper()
        base_sym = raw_sym[:-1] if raw_sym.endswith("0") else raw_sym
        variants = [
            base_sym,
            f"{base_sym}0",
            f"{base_sym}.SHFE",
            f"{base_sym}.DCE",
            f"{base_sym}.CZCE",
            f"{base_sym}.CFFEX",
        ]

        raw_limit = max(int(limit) * 4, 480)

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            placeholders = ",".join(["?"] * len(variants))
            df = con.execute(
                f"SELECT * FROM kline_cache WHERE symbol IN ({placeholders}) ORDER BY date DESC LIMIT ?",
                [*variants, int(raw_limit)],
            ).df()
        finally:
            con.close()

        if df is None or df.empty:
            return None

        # 字段完整度优先去重（GAP-148）：同一 date 存在多 symbol 变体时优先保留
        # 扩展字段更完整的行（历史裸数据变体如 SC 的 vwap/source/oi_change 全空，
        # 与 SC0 重叠时混行导致样本缩水 + 缺失率虚高）。
        score_cols = ("hold", "settle", "vwap", "oi_change", "source", "fetched_at", "trace_id")
        completeness = sum(df[col].notna().astype(int) for col in score_cols if col in df.columns)
        df["_completeness"] = completeness
        df = df.sort_values(["date", "_completeness"], ascending=[True, False])
        df = df.drop_duplicates(subset="date", keep="first")
        df = df.drop(columns=["_completeness"])

        # 去重后截取最近 limit 个交易日（升序取尾部）
        if len(df) > int(limit):
            df = df.tail(int(limit))

        df = df.reset_index(drop=True)

        if not _check_kline_field_integrity(df, base_sym):  # GAP-151 核心字段不可用→跳过
            return None

        if "settle" in df.columns:
            mask_settle = df["settle"].isna() | (df["settle"] <= 0)
            df.loc[mask_settle, "settle"] = (df["high"] + df["low"] + df["close"]) / 3.0
        if "hold" in df.columns:
            mask_hold = df["hold"].isna() | (df["hold"] <= 0)
            df.loc[mask_hold, "hold"] = df["volume"].rolling(window=20, min_periods=1).mean()
        # pre_settle 代理填充（与 aggregator._derive_pre_settle 同款语义：前日结算价，
        # 兜底前日收盘价；TDX 主路径不产出该字段，全历史 NA 需读取端兜底）
        if "pre_settle" in df.columns:
            mask_pre = df["pre_settle"].isna() | (df["pre_settle"] <= 0)
            if mask_pre.any():
                if "settle" in df.columns:
                    df.loc[mask_pre, "pre_settle"] = df["settle"].shift(1)
                df.loc[mask_pre & df["pre_settle"].isna(), "pre_settle"] = df["close"].shift(1)

        return df
    except Exception as e:  # noqa: BLE001
        logger.debug("数据级监控 读取缓存失败 symbol=%s: %s", symbol, e)
        return None


def data_level_monitor_job() -> None:
    """数据级质量监控（GAP-F06）：缺失率/异常值/复权一致性/多源分歧。

    读取能化链核心品种（ENERGY_CHAIN_SYMBOLS，market=energy）DuckDB 缓存
    执行四维检查（复权一致性需第二复权源，暂以多源分歧覆盖；复权一致性
    检查由监控器接口保留，供对账流程调用）。
    尽力而为：缓存缺失/读取失败仅记录日志，不中断其他调度任务。
    """
    trace_id = f"fts.dlm.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.monitor.data_level_monitor import create_data_level_monitor
        from fts.data_futures import ENERGY_CHAIN_SYMBOLS

        db_path = PROJECT_ROOT / "data" / "fts_history.duckdb"
        if not db_path.exists():
            logger.info("数据级监控 无缓存库 %s，跳过 (trace_id=%s)", db_path, trace_id)
            return

        monitor = create_data_level_monitor()
        total_alerts = 0
        critical = 0
        checked_symbols = 0

        for sym in ENERGY_CHAIN_SYMBOLS:
            df = _read_kline_cache(db_path, sym, limit=120)
            if df is None or len(df) == 0:
                continue
            checked_symbols += 1
            alerts = monitor.run_all(df=df, scope=sym)
            total_alerts += len(alerts)
            critical += sum(1 for a in alerts if a.severity == "critical")

        logger.info(
            "数据级监控 完成: symbols=%d alerts=%d critical=%d (trace_id=%s)",
            checked_symbols,
            total_alerts,
            critical,
            trace_id,
        )
    except Exception as e:
        logger.error("数据级监控 运行失败: %s (trace_id=%s)", e, trace_id)


def factor_level_monitor_job() -> None:
    """因子级监控任务：数据质量 + 逻辑正确性 + 实盘表现监控。

    对因子库（factor_catalog_energy.duckdb）执行三维检查：
    1. 因子数据质量：完整性（必填字段缺失）、一致性（状态/指标合理性）、血缘追踪
    2. 因子逻辑正确性：代码可执行性、因子签名完整性
    3. 实盘因子表现：LiveFactorMonitor 偏离检测（如适用）

    尽力而为：因子库缺失或读取失败仅记录日志，不中断其他调度任务。
    """
    trace_id = f"fts.flm.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        import duckdb

        db_path = PROJECT_ROOT / "data" / "factor_catalog_energy.duckdb"
        if not db_path.exists():
            logger.info("因子级监控 无因子库 %s，跳过 (trace_id=%s)", db_path, trace_id)
            return

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            total_factors = con.execute("SELECT COUNT(*) FROM factor_catalog").fetchone()[0]
            if total_factors == 0:
                logger.info("因子级监控 因子库为空 (trace_id=%s)", trace_id)
                return

            integrity_issues: list[str] = []
            null_name = con.execute("SELECT COUNT(*) FROM factor_catalog WHERE name IS NULL OR name = ''").fetchone()[0]
            if null_name > 0:
                integrity_issues.append(f"name 缺失: {null_name} 条")

            null_code = con.execute("SELECT COUNT(*) FROM factor_catalog WHERE code IS NULL OR code = ''").fetchone()[0]
            if null_code > 0:
                integrity_issues.append(f"code 缺失: {null_code} 条")

            null_params = con.execute("SELECT COUNT(*) FROM factor_catalog WHERE params IS NULL").fetchone()[0]
            if null_params > 0:
                integrity_issues.append(f"params 缺失: {null_params} 条")

            consistency_issues: list[str] = []
            # 合法状态集引用权威定义（GAP-149 VALID_CATALOG_STATUS，9 状态含
            # shadow/failed/retired/archived/deprecated），禁止硬编码子集造成误报
            from fts.factor_engine.factor_db.repository import VALID_CATALOG_STATUS

            _status_ph = ", ".join(["?"] * len(VALID_CATALOG_STATUS))
            invalid_status = con.execute(
                f"SELECT COUNT(*) FROM factor_catalog WHERE status NOT IN ({_status_ph})",
                list(VALID_CATALOG_STATUS),
            ).fetchone()[0]
            if invalid_status > 0:
                consistency_issues.append(f"非法状态: {invalid_status} 条")

            negative_sharpe = con.execute(
                "SELECT COUNT(*) FROM factor_catalog WHERE sharpe < 0 AND is_elite = TRUE"
            ).fetchone()[0]
            if negative_sharpe > 0:
                consistency_issues.append(f"elite 因子 Sharpe < 0: {negative_sharpe} 条")

            lineage_issues: list[str] = []
            child_without_parent = con.execute("""
                SELECT COUNT(*) FROM factor_catalog fc
                WHERE fc.parent_id IS NOT NULL
                AND fc.parent_id NOT IN (SELECT factor_id FROM factor_catalog)
            """).fetchone()[0]
            if child_without_parent > 0:
                lineage_issues.append(f"孤儿因子（parent_id 不存在）: {child_without_parent} 条")

            logic_issues: list[str] = []
            factors_no_eval = con.execute("""
                SELECT COUNT(*) FROM factor_catalog fc
                WHERE fc.is_elite = TRUE
                AND fc.factor_id NOT IN (SELECT DISTINCT factor_id FROM factor_evaluations)
            """).fetchone()[0]
            if factors_no_eval > 0:
                logic_issues.append(f"elite 因子无评估记录: {factors_no_eval} 条")

            failed_evals = con.execute("""
                SELECT COUNT(*) FROM factor_catalog fc
                JOIN factor_evaluations fe ON fc.factor_id = fe.factor_id
                WHERE fc.is_elite = TRUE AND fe.overall_passed = FALSE
            """).fetchone()[0]
            if failed_evals > 0:
                logic_issues.append(f"elite 因子评估未通过: {failed_evals} 条")

            try:
                from fts.monitor.live_factor_monitor import LiveFactorMonitor

                monitor = LiveFactorMonitor()
                elite_factors = con.execute("""
                    SELECT factor_id, name, ic, sharpe, max_drawdown
                    FROM factor_catalog
                    WHERE is_elite = TRUE AND status = 'active'
                """).fetchall()
                if elite_factors:
                    for row in elite_factors:
                        factor_id, name, ic, sharpe, max_dd = row
                        metrics: dict[str, float] = {}
                        if ic is not None:
                            metrics["ic"] = ic
                        if sharpe is not None:
                            metrics["sharpe"] = sharpe
                        if max_dd is not None:
                            metrics["max_drawdown"] = max_dd
                        if metrics:
                            monitor.set_backtest_baseline(factor_id, metrics)
                    logger.info(
                        "因子级监控 LiveMonitor: 已加载 %d 个 elite 因子回测基线 (trace_id=%s)",
                        len(elite_factors),
                        trace_id,
                    )
            except Exception as e:
                logger.debug("因子级监控 LiveMonitor 初始化失败: %s", e)

            total_issues = len(integrity_issues) + len(consistency_issues) + len(lineage_issues) + len(logic_issues)
            logger.info(
                "因子级监控 完成: factors=%d integrity=%d consistency=%d lineage=%d logic=%d total_issues=%d (trace_id=%s)",
                total_factors,
                len(integrity_issues),
                len(consistency_issues),
                len(lineage_issues),
                len(logic_issues),
                total_issues,
                trace_id,
            )
            for issue in integrity_issues:
                logger.warning("因子级监控 [完整性] %s (trace_id=%s)", issue, trace_id)
            for issue in consistency_issues:
                logger.warning("因子级监控 [一致性] %s (trace_id=%s)", issue, trace_id)
            for issue in lineage_issues:
                logger.warning("因子级监控 [血缘] %s (trace_id=%s)", issue, trace_id)
            for issue in logic_issues:
                logger.warning("因子级监控 [逻辑] %s (trace_id=%s)", issue, trace_id)

        finally:
            con.close()

    except Exception as e:
        logger.error("因子级监控 运行失败: %s (trace_id=%s)", e, trace_id)


def _verify_field_coverage(kline_ok: bool, fundamental_ok: bool, term_ok: bool) -> dict[str, Any]:
    """按字段消费字典校验：全部登记字段必须已有产出通道（失败透明）。

    Returns:
        {"registered": 登记字段数, "produced": {组: 产出字段数},
         "missing": 缺失字段清单, "error": 字典加载失败原因（可选）}
    """
    try:
        from fts.config.futures_field_consumption import FUTURES_FIELD_CONSUMPTION

        groups = FUTURES_FIELD_CONSUMPTION.groups()
    except Exception as e:  # noqa: BLE001
        logger.error("[Sync] 字段消费字典加载失败: %s", e)
        return {"registered": 0, "produced": {}, "missing": [], "error": str(e)}

    produced = {
        "kline": set(groups["kline"]) if kline_ok else set(),
        "fundamental": set(groups["fundamental"]) if fundamental_ok else set(),
        "term_structure": set(groups["term_structure"]) if term_ok else set(),
    }
    missing: list[str] = []
    for g, fields in groups.items():
        for f in fields:
            if f not in produced[g]:
                missing.append(f)
    return {
        "registered": sum(len(fs) for fs in groups.values()),
        "produced": {g: len(fs) for g, fs in produced.items()},
        "missing": missing,
    }


def sync_futures_data_job(symbols: list[str] | None = None, days: int = 120) -> None:
    """执行期货多源数据同步（Phase 14.5，工作日 17:30 调度）。

    按字段消费字典（fts/config/futures_field_consumption.py）三组字段每日同步:
      Stage 1 kline（17 字段）        → kline_cache（DuckDB，多源聚合器）
      Stage 2 fundamental（9 字段）   → futures_fundamental（Parquet，含现货价 WebSearch 补充）
      Stage 3 term_structure（4 字段）→ futures_term_structure（Parquet，多合约截面）
    单品种失败不中断；完成后做字典字段覆盖校验，
    同步摘要（gzip JSON）落盘 data/_lineage/sync_summary_*.json.gz。

    Args:
        symbols: 品种代码列表；None 时使用 FUTURES_SUBSET（全品种 82 个）。
        days: 回溯天数。
    """
    trace_id = f"fts.sync.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[Sync] 期货多源数据同步启动 trace_id=%s", trace_id)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.cli import _build_default_aggregator

        agg = _build_default_aggregator()
    except Exception as e:
        logger.error("[Sync] 聚合器初始化失败: %s (trace_id=%s)", e, trace_id, exc_info=True)
        return

    if symbols is None:
        from fts.data_futures import FUTURES_SUBSET

        symbols = list(FUTURES_SUBSET)

    import gzip
    import json

    started_at = datetime.now().isoformat()

    # ── Stage 1: 行情 17 字段 → kline_cache（DuckDB）──
    kline_success = 0
    kline_failure = 0
    kline_rows = 0
    kline_failures: list[dict] = []
    for sym in symbols:
        try:
            df = agg.get_ohlcv(sym, days, trace_id)
            if df is None or len(df) == 0:
                kline_failure += 1
                kline_failures.append({"symbol": sym, "error": "empty data"})
            else:
                kline_success += 1
                kline_rows += int(len(df))
        except Exception as e:  # noqa: BLE001
            kline_failure += 1
            kline_failures.append({"symbol": sym, "error": str(e)})

    # ── Stage 2: 基本面 9 字段 → futures_fundamental（Parquet）──
    try:
        from fts.data_futures_fundamental_sync import sync_fundamental_fields

        fund_result = sync_fundamental_fields(symbols, days=days, trace_id=trace_id)
    except Exception as e:  # noqa: BLE001
        logger.error("[Sync] Stage2 基本面同步失败: %s (trace_id=%s)", e, trace_id, exc_info=True)
        fund_result = {
            "success": 0,
            "failure": len(symbols),
            "rows": 0,
            "failures": [{"symbol": "*", "error": str(e)}],
            "missing_spot": [],
        }

    # ── Stage 3: 期限结构 4 字段 → futures_term_structure（Parquet）──
    try:
        from fts.data_futures_term_structure import sync_term_structure_fields

        ts_result = sync_term_structure_fields(symbols, days=days, trace_id=trace_id)
    except Exception as e:  # noqa: BLE001
        logger.error("[Sync] Stage3 期限结构同步失败: %s (trace_id=%s)", e, trace_id, exc_info=True)
        ts_result = {
            "success": 0,
            "failure": len(symbols),
            "rows": 0,
            "failures": [{"symbol": "*", "error": str(e)}],
            "no_section": [],
        }

    # ── 字段覆盖校验（字典全部字段必须有产出通道）──
    coverage = _verify_field_coverage(
        kline_ok=kline_success > 0,
        fundamental_ok=fund_result.get("success", 0) > 0,
        term_ok=ts_result.get("success", 0) > 0,
    )

    try:
        source_status = agg.get_source_status()
    except Exception:  # noqa: BLE001
        source_status = {}

    finished_at = datetime.now().isoformat()
    summary = {
        "trace_id": trace_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round((datetime.now() - datetime.fromisoformat(started_at)).total_seconds(), 3),
        # 顶层兼容字段（v2.103.0 前=整体统计，现=Stage1 行情统计）
        "symbols_total": len(symbols),
        "success": kline_success,
        "failure": kline_failure,
        "failures": kline_failures,
        "total_rows": kline_rows,
        # 分阶段统计（v2.103.0+）
        "kline": {"success": kline_success, "failure": kline_failure, "rows": kline_rows, "failures": kline_failures},
        "fundamental": fund_result,
        "term_structure": ts_result,
        "coverage": coverage,
        "source_status": source_status,
    }

    lineage_dir = Path("data") / "_lineage"
    lineage_dir.mkdir(parents=True, exist_ok=True)
    out_path = lineage_dir / f"sync_summary_{datetime.now().strftime('%Y%m%d%H%M%S')}.json.gz"
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)

    logger.info(
        "[Sync] 完成: kline total=%d success=%d failure=%d rows=%d | fund rows=%d | ts rows=%d | coverage_missing=%d -> %s (trace_id=%s)",
        len(symbols),
        kline_success,
        kline_failure,
        kline_rows,
        fund_result.get("rows", 0),
        ts_result.get("rows", 0),
        len(coverage.get("missing", [])),
        out_path.name,
        trace_id,
    )


def sync_liquidity_pool_job() -> None:
    """每周刷新数据驱动动态池（GAP-054）：TqSdk 流动性快照 → 渐进式替换 → 落盘缓存。"""
    trace_id = f"fts.lpool.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L-Pool] 动态池刷新启动 trace_id=%s", trace_id)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from scripts.sync_liquidity_pool import main as _run_pool

        _run_pool()
        logger.info("[L-Pool] 动态池刷新完成 trace_id=%s", trace_id)
    except Exception as e:  # noqa: BLE001
        logger.error("[L-Pool] 动态池刷新失败: %s (trace_id=%s)", e, trace_id)


def mhf_signal_job() -> None:
    """MHF 中高频信号生成（plans/33 Phase 4）：30m 反转混合信号 → SignalBridge 发布。

    每 30 分钟执行；最新 bar 无更新时信号幂等（信号内容不变），交易日自然驱动。
    """
    trace_id = f"fts.mhf.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[MHF] 信号任务启动 trace_id=%s", trace_id)
    if not _market_gate("futures", task="MHF信号"):
        return

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.bridge.signal_bridge import SignalBridge
        from scripts.mhf_signal_pipeline import generate_mhf_signals

        payload = generate_mhf_signals(trace_id=trace_id)
        if not payload.get("ok"):
            logger.warning("[MHF] 无有效信号（trace_id=%s）", trace_id)
            return
        SignalBridge(protocol="json", output_dir=str(PROJECT_ROOT / "signals")).publish(payload)
        logger.info(
            "[MHF] 信号发布完成: %s symbols=%d bar=%s (trace_id=%s)",
            payload["signal_id"],
            payload["symbols"],
            payload.get("bar_time"),
            trace_id,
        )
        self_serial_exec(payload, trace_id)
    except Exception as e:  # noqa: BLE001
        logger.error("[MHF] 信号任务失败: %s (trace_id=%s)", e, trace_id)


def self_serial_exec(payload: dict, trace_id: str) -> None:
    """信号后串行执行 TqSdk 模拟执行（plans/33 Phase 4 扩展），独立 try 不影响信号任务。"""
    try:
        from fts.live_trade.tqsdk_mhf_executor import (
            ExecConfig,
            TqSdkMhfExecutor,
            is_trading_time,
        )

        if not is_trading_time(datetime.now()):
            logger.info("[MHF] 非交易时段，跳过模拟执行 (trace_id=%s)", trace_id)
            return

        exec_trace = f"{trace_id}_exec"
        result = TqSdkMhfExecutor(ExecConfig(), trace_id=exec_trace).run_once(payload)
        # 留痕落盘（reports/mhf/tqsdk_exec_job_*.json）
        import json as _json

        out_dir = PROJECT_ROOT / "reports" / "mhf"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"tqsdk_exec_job_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json").write_text(
            _json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "[MHF] 模拟执行串行完成: ok=%s targets=%d equity=%.2f (trace_id=%s)",
            result.get("ok"),
            len(result.get("targets") or {}),
            (result.get("equity") or {}).get("balance", 0.0),
            exec_trace,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[MHF] 模拟执行失败: %s (trace_id=%s)", e, trace_id)


__all__ = [
    "l1_meta_loop_job",
    "l2_evolution_loop_job",
    "l2_evolution_weekday_job",
    "l2_evolution_weekend_job",
    "l2_seed_promotion_job",
    "l2_seed_promotion_energy_job",
    "l2_batch_mining_job",
    "l2_batch_mining_energy_job",
    "l3_portfolio_loop_job",
    "futures_signal_pipeline_job",
    "health_check_job",
    "l2_review_job",
    "l2_review_energy_job",
    "l2_energy_qa_review_job",
    "l2_subchain_quality_job",
    "data_quality_eval_job",
    "logic_monitor_job",
    "factor_inspector_job",
    "sync_futures_data_job",
    "sync_liquidity_pool_job",
    "mhf_signal_job",
]
