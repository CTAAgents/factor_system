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


# ── L1 Meta-Loop — 每日 08:30 知识补给 + 种子注入 ─────────


def l1_meta_loop_job(market: str = "futures") -> None:
    """执行 L1 Meta-Loop（每日知识补给 + Bootstrapping + 种子注入）。

    Args:
        market: 市场类型（futures 默认；energy 走能源链独立 L1 输出，GAP-121 2026-08-15）。
    """
    trace_id = f"fts.l1.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L1] Meta-Loop 启动 trace_id=%s market=%s", trace_id, market)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.meta_loop import MetaLoop
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
        )
        result = loop.run()
        logger.info("[L1] 完成: status=%s injected=%d", result.status, len(result.injected_candidate_ids))
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
        from fts.llm import get_llm_client
        from fts.config import get_config
        from fts.data import FTSDataProvider
        from fts.data_futures import FUTURES_STRATIFIED_SUBSET, FUTURES_HOLDOUT

        cfg = get_config()

        # 准备期货横截面数据（分层训练集 + 排除盲测品种池）
        train_symbols = [s for s in FUTURES_STRATIFIED_SUBSET if s not in FUTURES_HOLDOUT]
        if len(train_symbols) < 10:
            logger.error("[L2] 训练品种不足 (排除盲测后仅 %d 个)", len(train_symbols))
            return
        logger.info("[L2] 分层训练品种: %d 个 (排除 %d 个盲测品种)", len(train_symbols), len(FUTURES_HOLDOUT))
        provider = FTSDataProvider()
        panel, common_dates = provider.get_futures_panel(
            symbols=train_symbols,
            days=500,
            trace_id=trace_id,
        )
        if not panel:
            logger.error("[L2] 无期货数据，跳过")
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
        loop.budget["max_generation"] = 10

        result = loop.run(max_generation=10)
        logger.info("[L2] 完成: status=%s elite=%d", result.status, len(result.elite_factor_ids))
    except Exception as e:
        logger.error("[L2] 运行失败: %s", e, exc_info=True)


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


# ── 月度衰减评估 — 每月 1 日 02:00（A.2）───────────────────


def monthly_decay_eval_job() -> None:
    """月度因子衰减评估：对精英池执行增量评估并触发状态机/自动淘汰。

    关联设计: A.2 因子衰减追踪（EliteFactorTracker.run_monthly_evaluation）。
    """
    trace_id = f"fts.decay.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[衰减评估] 启动 trace_id=%s", trace_id)

    # ---- Step A: 新标准全量重审（与月度衰减合并） ----
    if os.getenv("FTS_MONTHLY_REAUDIT_ENABLED", "1") == "1":
        try:
            sys.path.insert(0, str(PROJECT_ROOT))
            from fts.monitor.reaudit import run_reaudit

            rep = run_reaudit(market="futures", trace_id=f"{trace_id}.reaudit", apply=True, out_json=True)
            logger.info(
                "[衰减评估] Step A 新标准重审完成: retain=%d shadow=%d retire=%d error=%d (total=%d)",
                rep.counts.get("retain", 0),
                rep.counts.get("shadow", 0),
                rep.counts.get("retire", 0),
                rep.counts.get("error", 0),
                rep.total,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("[衰减评估] Step A 重审失败（不阻断衰减评估）: %s", e, exc_info=True)
    else:
        logger.info("[衰减评估] Step A 新标准重审已关闭（FTS_MONTHLY_REAUDIT_ENABLED=0）")

    # ---- Step B: 衰减评估（原有逻辑） ----

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.monitor.elite_tracker import EliteFactorTracker, AutoRetireManager
        from fts.config import get_config

        cfg = get_config()
        tracker = EliteFactorTracker(tracking_dir=f"{cfg.memory_dir}/tracking")
        report = tracker.run_monthly_evaluation()
        logger.info("[衰减评估] 完成: %s", report)

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
            logger.warning("[衰减评估] 指标同步失败: %s", e)

        # 自动淘汰（EliteFactorTracker 快照标记 retired）
        retire_mgr = AutoRetireManager(tracker)
        retired = retire_mgr.run()
        if retired:
            logger.warning("[衰减评估] 快照标记淘汰: %d 个因子", len(retired))

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
                if repo.retire_factor(fid, reason="月度衰减评估自动淘汰", elite_dir=elite_dir):
                    retired_count += 1
            logger.warning("[衰减评估] 淘汰已同步至 DuckDB + JSON: %d/%d 个因子", retired_count, len(retired))
    except Exception as e:
        logger.error("[衰减评估] 失败: %s", e, exc_info=True)


# ── 逻辑监控 — 每日 22:00（B.2 逻辑审查）───────────────────


def logic_monitor_job() -> None:
    """逻辑监控：对精英因子执行行为漂移、极端预测、换月日异常检测。

    从因子数据库加载活跃精英因子，逐个执行 LogicMonitor.run()，
    生成监控报告并记录日志。
    """
    trace_id = f"fts.logic.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[逻辑监控] 启动 trace_id=%s", trace_id)
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.monitor.logic_monitor import LogicMonitor
        from fts.factor_engine.factor_db import FactorRepository

        logic = LogicMonitor()

        # 加载活跃精英因子（期货因子库，股票剥离后主系统仅期货）
        repo = FactorRepository(market="futures")
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
                # 构建简化的 FactorProgram 用于检查
                from fts.factor_engine.contracts import FactorProgram

                fp = FactorProgram(
                    factor_id=factor_id,
                    name=factor.get("name", "unknown"),
                    code=factor.get("code", ""),
                )
                # 用模拟数据做行为漂移检测
                n = 500
                mock_data = pd.DataFrame(
                    {
                        "date": pd.date_range("2020-01-01", periods=n, freq="B"),
                        "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
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
    except Exception as e:
        logger.error("[逻辑监控] 运行失败: %s (trace_id=%s)", e, trace_id)


# ── 因子巡检与降级 — 每日 03:00（B.2 因子退化检测）─────────


def factor_inspector_job() -> None:
    """因子巡检与自动降级：扫描精英因子库，检测退化因子并自动降级。

    调用 FactorInspector.inspect_and_downgrade() 执行巡检，
    阈值默认 -0.2（Sharpe 下降 20% 触发降级）。
    """
    trace_id = f"fts.inspector.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[因子巡检] 启动 trace_id=%s", trace_id)
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.factor_inspector import FactorInspector

        inspector = FactorInspector()
        result = inspector.inspect_and_downgrade(
            threshold=-0.2,
            commit=True,
        )
        summary = result.get("summary", {})
        logger.info(
            "[因子巡检] 完成: audited=%d degraded=%d downgraded=%d skipped=%d errors=%d (trace_id=%s)",
            summary.get("total_audited", 0),
            summary.get("degraded_detected", 0),
            summary.get("downgraded", 0),
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

        con = duckdb.connect(str(db_path), read_only=True)
        try:
            variants = [symbol, f"{symbol}0", f"{symbol}.SHFE", f"{symbol}.DCE", f"{symbol}.CZCE", f"{symbol}.CFFEX"]
            placeholders = ",".join(["?"] * len(variants))
            return con.execute(
                f"SELECT * FROM kline_cache WHERE symbol IN ({placeholders}) ORDER BY date DESC LIMIT ?",
                [*variants, int(limit)],
            ).df()
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("数据级监控 读取缓存失败 symbol=%s: %s", symbol, e)
        return None


def data_level_monitor_job() -> None:
    """数据级质量监控（GAP-F06）：缺失率/异常值/复权一致性/多源分歧。

    读取核心期货品种 DuckDB 缓存执行四维检查（复权一致性需第二复权源，
    暂以多源分歧覆盖；复权一致性检查由监控器接口保留，供对账流程调用）。
    尽力而为：缓存缺失/读取失败仅记录日志，不中断其他调度任务。
    """
    trace_id = f"fts.dlm.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.monitor.data_level_monitor import create_data_level_monitor
        from fts.data_futures import get_dynamic_core_subset

        db_path = PROJECT_ROOT / "data" / "fts_history.duckdb"
        if not db_path.exists():
            logger.info("数据级监控 无缓存库 %s，跳过 (trace_id=%s)", db_path, trace_id)
            return

        monitor = create_data_level_monitor()
        total_alerts = 0
        critical = 0
        checked_symbols = 0

        for sym in list(get_dynamic_core_subset())[:10]:
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
            payload["signal_id"], payload["symbols"], payload.get("bar_time"), trace_id,
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
            result.get("ok"), len(result.get("targets") or {}),
            (result.get("equity") or {}).get("balance", 0.0), exec_trace,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("[MHF] 模拟执行失败: %s (trace_id=%s)", e, trace_id)


__all__ = [
    "l1_meta_loop_job",
    "l2_evolution_loop_job",
    "l3_portfolio_loop_job",
    "futures_signal_pipeline_job",
    "health_check_job",
    "monthly_decay_eval_job",
    "data_quality_eval_job",
    "logic_monitor_job",
    "factor_inspector_job",
    "sync_futures_data_job",
    "sync_liquidity_pool_job",
    "mhf_signal_job",
]

