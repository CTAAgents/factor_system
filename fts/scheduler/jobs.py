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
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ── L1 Meta-Loop — 每日 08:30 知识补给 + 种子注入 ─────────

def l1_meta_loop_job() -> None:
    """执行 L1 Meta-Loop（每日知识补给 + Bootstrapping + 种子注入）。"""
    trace_id = f"fts.l1.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L1] Meta-Loop 启动 trace_id=%s", trace_id)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.meta_loop import MetaLoop
        from fts.config import get_config
        cfg = get_config()

        loop = MetaLoop(
            memory_dir=cfg.memory_dir + "/meta_loop",
        )
        result = loop.run()
        logger.info("[L1] 完成: status=%s injected=%d",
                    result.status, len(result.injected_candidate_ids))
    except Exception as e:
        logger.error("[L1] 运行失败: %s", e, exc_info=True)


# ── L2 Evolution Loop — 每日 23:00 夜间因子演化 ──────────

def l2_evolution_loop_job() -> None:
    """执行 L2 Evolution Loop（夜间因子演化 — 期货横截面）。"""
    trace_id = f"fts.l2.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L2] Evolution Loop 启动 trace_id=%s", trace_id)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.evolution_loop import EvolutionLoop
        from fts.factor_engine.factor_verifier import FactorVerifier
        from fts.factor_engine.seed_pool import SeedPool
        from fts.factor_engine.contracts import DEFAULT_BUDGET_CONFIG
        from fts.llm import MockLLMClient, get_default_llm_client
        from fts.config import get_config
        from fts.data import FTSDataProvider
        from fts.data_futures import FUTURES_CORE_SUBSET
        cfg = get_config()

        # 准备期货横截面数据
        provider = FTSDataProvider()
        panel, common_dates = provider.get_futures_panel(
            symbols=FUTURES_CORE_SUBSET, days=500, trace_id=trace_id,
        )
        if not panel:
            logger.error("[L2] 无期货数据，跳过")
            return

        first_sym = list(panel.keys())[0]
        data_df = panel[first_sym]
        closes = data_df["close"].values
        fwd_ret = __import__("numpy").zeros(len(closes))
        if len(closes) > 5:
            fwd_ret[:-5] = (closes[5:] - closes[:-5]) / \
                __import__("numpy").maximum(closes[:-5], 1e-10)

        llm = get_default_llm_client()
        seed_pool = SeedPool(market="futures")
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
        loop.budget["max_generation"] = 10

        result = loop.run(max_generation=10)
        logger.info("[L2] 完成: status=%s elite=%d",
                    result.status, len(result.elite_factor_ids))
    except Exception as e:
        logger.error("[L2] 运行失败: %s", e, exc_info=True)


# ── L3 Portfolio Loop — 每日 20:00 组合构建 + 信号合成 ───

def l3_portfolio_loop_job() -> None:
    """执行 L3 Portfolio Loop（组合构建 + 正交化 + 衰减检验 + 信号合成）。"""
    trace_id = f"fts.l3.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L3] Portfolio Loop 启动 trace_id=%s", trace_id)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.portfolio_loop import PortfolioLoop
        from fts.config import get_config
        cfg = get_config()

        loop = PortfolioLoop(
            elite_dir=cfg.elite_dir,
            memory_dir=cfg.memory_dir + "/portfolio",
        )
        result = loop.run()
        logger.info("[L3] 完成: status=%s retained=%d sharpe=%.4f",
                    result.status, result.n_factors_retained, result.combo_sharpe)

        # 组合构建完成后，生成期货信号报告
        _run_futures_signal_pipeline()
    except Exception as e:
        logger.error("[L3] 运行失败: %s", e, exc_info=True)


# ── 期货信号管道 — 每日 20:00（L3 完成后执行）────────────

def _run_futures_signal_pipeline() -> None:
    """生成期货信号报告（L3 组合构建后自动触发）。

    使用全量商品期货池（--universe all）：
    - 覆盖 FUTURES_SUBSET 中所有非僵尸品种（剔除停更/陈旧品种后参与排名）
    - 报告输出品种中文名称、主力合约代码、盘中实时价
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


__all__ = [
    "l1_meta_loop_job",
    "l2_evolution_loop_job",
    "l3_portfolio_loop_job",
    "futures_signal_pipeline_job",
    "health_check_job",
]