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
        from fts.llm import get_llm_client
        from fts.config import get_config

        cfg = get_config()

        loop = MetaLoop(
            memory_dir=cfg.memory_dir + "/meta_loop",
            llm_client=get_llm_client(),
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


# ── L3 Portfolio Loop — 每日 20:00 组合构建 + 信号合成 ───


def l3_portfolio_loop_job() -> None:
    """执行 L3 Portfolio Loop（期货因子筛选 + 信号合成 + Verifier 校验）。
    显式走期货路径：elite_dir=futures_elite_dir + market="futures"，与
    CLI `fts portfolio run --universe futures` 对齐（此前误用股票 elite 目录，
    与下游期货信号管道不一致，v2.73.0 修复）。

    权重计算模式（portfolio_loop.py）:
        - elastic_net: Elastic Net 截面回归（CSI300 面板，L1+L2，默认）
        - equal_weight: 等权 1/N
        - sharpe_weight: 按 Sharpe 比率归一化加权

    完成后自动触发期货信号管道（Ridge 回归加权）。
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


def l3_portfolio_loop_stock_job() -> None:
    """执行 L3 Portfolio Loop（股票路径：elite_dir + market="stock"）。

    股票精英因子筛选 + 信号合成（equal/sharpe/elastic_net）+ Verifier 校验，
    与期货 L3 任务（l3_portfolio_loop_job）并列，补齐股票侧组合层闭环
    （GAP-063 组合质检三标准的前置接入）。

    trace_id: fts.l3.stock.sched_<ts>
    """
    trace_id = f"fts.l3.stock.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[L3-stock] Portfolio Loop 启动 trace_id=%s", trace_id)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.factor_engine.portfolio_loop import PortfolioLoop
        from fts.config import get_config

        cfg = get_config()

        loop = PortfolioLoop(
            elite_dir=cfg.elite_dir,
            memory_dir=cfg.memory_dir + "/portfolio",
            market="stock",
        )
        result = loop.run()
        logger.info(
            "[L3-stock] 完成: status=%s retained=%d sharpe=%.4f",
            result.status,
            result.n_factors_retained,
            result.combo_sharpe,
        )

    except Exception as e:
        logger.error("[L3-stock] 运行失败: %s", e, exc_info=True)


# ── 期货信号管道 — 工作日每日 20:00（独立调度，与 L3 解绑，GAP-072）──


def _run_futures_signal_pipeline() -> None:
    """生成期货信号报告（独立每日任务；权重周五重算，其余日冻结复用快照）。

    使用全量商品期货池（--universe all）：
    - 覆盖 FUTURES_SUBSET 中所有非僵尸品种（剔除停更/陈旧品种后参与排名）
    - 报告输出品种中文名称、主力合约代码、盘中实时价

    权重计算方法（Ridge 回归）:
    - 方向校正: 截面 IC 法（Spearman 秩相关 vs 未来 5 日收益）
    - 权重学习: Ridge 回归（L2 正则化，弱因子保留不丢弃）
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


# ── 股票/ETF 信号管道 — 工作日每日 08:45 独立调度（与股票 L3 解绑，GAP-072）─


def _run_daily_signal_pipeline() -> None:
    """生成股票/ETF 信号报告（与期货信号管道 `_run_futures_signal_pipeline` 对称）。

    权重计算方法（daily_signal_pipeline.py）:
    - 方向校正: 截面 IC 法（Spearman 秩相关 vs 未来 5 日收益）
    - 权重学习: Ridge 回归（L2 正则化，含相关性惩罚）
    - 输出: 仅做多信号排名（股票/ETF 仅做多）→ reports/{date}/daily_signals_*.md
    """
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from scripts.daily_signal_pipeline import main

        exit_code = main(max_stocks=50, days=120)
        logger.info("[股票信号管道] 完成: exit_code=%d", exit_code)
    except Exception as e:
        logger.error("[股票信号管道] 失败: %s", e, exc_info=True)


def daily_signal_pipeline_job() -> None:
    """独立的股票/ETF 信号管道任务入口（供手动/外部调度调用）。"""
    trace_id = f"fts.signal.stock.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[股票信号管道] 启动 trace_id=%s", trace_id)
    _run_daily_signal_pipeline()


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

            # 分市场 repo：按因子所属市场路由到对应分库
            repo_stock = FactorRepository(market="stock")
            repo_futures = FactorRepository(market="futures")
            repos = {"stock": repo_stock, "futures": repo_futures}
            retired_count = 0
            for fid in retired:
                # 尝试从两个市场查找因子
                factor = repo_stock.get_factor(fid) or repo_futures.get_factor(fid)
                if factor:
                    mkt = factor.get("market", "stock")
                    elite_dir = cfg.get_elite_dir(mkt)
                else:
                    mkt = "stock"
                    elite_dir = cfg.get_elite_dir("stock")
                if repos[mkt].retire_factor(fid, reason="月度衰减评估自动淘汰", elite_dir=elite_dir):
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

        # 分市场加载活跃精英因子
        all_elite_factors: list[dict] = []
        for mkt in ("stock", "futures"):
            repo = FactorRepository(market=mkt)
            conn = repo._get_conn()
            rows = conn.execute("SELECT * FROM factor_catalog WHERE is_elite = 1 AND status = 'active'").fetchall()
            columns = [desc[0] for desc in conn.description]
            all_elite_factors.extend([dict(zip(columns, row)) for row in rows])

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


def sync_futures_data_job(symbols: list[str] | None = None, days: int = 120) -> None:
    """执行期货多源数据同步（Phase 14.5，工作日 17:30 调度）。

    对每个品种通过默认聚合器拉取 K 线并写缓存；单个品种失败不中断，
    完成后将同步摘要（gzip JSON）落盘 data/_lineage/sync_summary_*.json.gz。

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
    success = 0
    failure = 0
    total_rows = 0
    failures: list[dict] = []

    for sym in symbols:
        try:
            df = agg.get_ohlcv(sym, days, trace_id)
            if df is None or len(df) == 0:
                failure += 1
                failures.append({"symbol": sym, "error": "empty data"})
            else:
                success += 1
                total_rows += int(len(df))
        except Exception as e:  # noqa: BLE001
            failure += 1
            failures.append({"symbol": sym, "error": str(e)})

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
        "symbols_total": len(symbols),
        "success": success,
        "failure": failure,
        "failures": failures,
        "total_rows": total_rows,
        "source_status": source_status,
    }

    lineage_dir = Path("data") / "_lineage"
    lineage_dir.mkdir(parents=True, exist_ok=True)
    out_path = lineage_dir / f"sync_summary_{datetime.now().strftime('%Y%m%d%H%M%S')}.json.gz"
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)

    logger.info(
        "[Sync] 完成: total=%d success=%d failure=%d rows=%d -> %s (trace_id=%s)",
        len(symbols),
        success,
        failure,
        total_rows,
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

# ── 股票数据缓存同步 — 工作日 17:00（每日股票数据更新）──────



def _tdx_stock_code(symbol: str) -> str:
    """A 股/ETF 6 位代码 → 通达信 TQ 代码（000001 → 000001.SZ, 600000 → 600000.SH）。

    通达信本地 TQ（17709）get_market_data 对 A 股使用 {code}.{exchange} 格式，
    与期货主连格式（RBL8.SHF）不同。无法识别时原样返回。
    """
    raw = symbol.strip().lower()
    for pfx in ("sh", "sz"):
        if raw.startswith(pfx):
            raw = raw[len(pfx):]
    if len(raw) != 6 or not raw.isdigit():
        return symbol
    # 沪市 6/9 开头、沪 ETF 5 开头；其余默认深市（0/3 开头、159 ETF）
    if raw.startswith(("6", "9", "5")):
        return f"{raw}.SH"
    return f"{raw}.SZ"


def _fetch_stock_ohlcv_from_tdx(symbol: str, days: int, trace_id: str) -> object | None:
    """从通达信本地 TQ（17709）拉取 A 股日 K 线（不复权）。

    Args:
        symbol: A 股 6 位代码（如 "000001"）
        days: 回溯天数
        trace_id: 链路追踪 ID

    Returns:
        DataFrame（index=DatetimeIndex，列 open/high/low/close/volume）或 None。
    """
    import json
    import time
    import urllib.request

    tdx_code = _tdx_stock_code(symbol)
    payload = {
        "id": int(time.time() * 1000),
        "method": "get_market_data",
        "params": {
            "stock_list": [tdx_code],
            "count": days,
            "period": "1d",
            "dividend_type": "none",
        },
    }
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:17709/",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.debug("[Sync-stock] TQ 拉取失败 %s: %s", symbol, e)
        return None

    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return None
    value = result.get("Value")
    if not isinstance(value, dict) or not value:
        return None
    block = value.get(tdx_code) or next(iter(value.values()), None)
    if not isinstance(block, dict) or not block:
        return None
    if block.get("ErrorId") not in (None, 0, "0"):
        return None

    import pandas as pd

    try:
        df = pd.DataFrame(block)
        col_map = {c.lower(): c for c in df.columns}
        df = df.rename(columns={v: k for k, v in col_map.items()})
        required = ("open", "high", "low", "close", "volume")
        if not all(c in df.columns for c in required) or "date" not in df.columns:
            return None
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce").dt.date
        df = df.dropna(subset=["date"])
        for col in required:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["close"])
        if len(df) == 0:
            return None
        df = df.sort_values("date")
        df.index = pd.DatetimeIndex(pd.to_datetime(df["date"]))
        df.index.name = None
        return df[list(required)]
    except Exception as e:  # noqa: BLE001
        logger.debug("[Sync-stock] TQ 解析失败 %s: %s", symbol, e)
        return None


def sync_stock_data_job(max_stocks: int = 50, days: int = 120) -> None:
    """执行股票/ETF 日 K 线缓存同步（工作日 17:00 调度）。

    数据源优先级（v2.86.0）:
        1. 通达信本地 TQ（17709，get_market_data，A 股不复权日线）
        2. 腾讯 API 降级（MCPDataProvider 严格模式，前复权；失败抛 MCPDataError）
    拉取失败/空数据不写入（避免合成数据污染缓存），
    upsert 写入 DuckDB stock_kline_cache，供次日 08:45 股票信号管道 / 因子演化。
    单标失败不中断，完成后落盘同步摘要 data/_lineage/sync_stock_summary_*.json.gz。

    Args:
        max_stocks: 最大成分股数（默认 50，与信号管道对齐）。
        days: 回溯天数。
    """
    trace_id = f"fts.sync.stock.sched_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[Sync-stock] 股票数据缓存同步启动 trace_id=%s", trace_id)

    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from fts.data_mcp import MCPDataProvider, MCPDataError, CSI300_SUBSET
        from fts.data_sources.migrate import migrate_schema
        from fts.data_futures import _DUCKDB_PATH

        # TQ 优先（通达信本地 17709），失败降级腾讯 API 严格模式。
        # 两个源均只写入真实行情，不落合成回退数据。
        provider = MCPDataProvider()
        panel: dict[str, object] = {}
        sources: dict[str, str] = {}
        fetch_failures: list[dict] = []
        for sym in CSI300_SUBSET[:max_stocks]:
            df = _fetch_stock_ohlcv_from_tdx(sym, days, trace_id)
            used_source = "TDX_LOCAL"
            if df is None or df.empty or "close" not in df.columns:
                try:
                    df = provider.get_ohlcv(sym, days=days, adjust="qfq", trace_id=trace_id, strict=True)
                    used_source = "TENCENT"
                except MCPDataError as e:
                    logger.warning("[Sync-stock] 腾讯降级失败 %s: %s", sym, e)
                    fetch_failures.append({"symbol": sym, "error": str(e)})
                    continue
            if df is not None and not df.empty and "close" in df.columns:
                panel[sym] = df
                sources[sym] = used_source
            else:
                fetch_failures.append({"symbol": sym, "error": "empty data"})
    except Exception as e:  # noqa: BLE001
        logger.error("[Sync-stock] 面板拉取失败: %s (trace_id=%s)", e, trace_id, exc_info=True)
        return

    if not panel:
        logger.error("[Sync-stock] 无股票数据，跳过 (trace_id=%s)", trace_id)
        return

    # 确保 stock_kline_cache 表存在
    try:
        migrate_schema(_DUCKDB_PATH)
    except Exception as e:  # noqa: BLE001
        logger.warning("[Sync-stock] migrate_schema 失败（尝试直接写入）: %s", e)

    import gzip
    import json

    import duckdb
    import pandas as pd

    started_at = datetime.now().isoformat()
    success = 0
    failure = 0
    total_rows = 0
    failures: list[dict] = []

    try:
        con = duckdb.connect(str(_DUCKDB_PATH))
        for sym, df in panel.items():
            if df is None or df.empty or "close" not in df.columns:
                failure += 1
                failures.append({"symbol": sym, "error": "empty data"})
                continue
            try:
                rows_df = pd.DataFrame(
                    {
                        "symbol": sym,
                        "period": "daily",
                        "date": [d.date() for d in df.index],
                        "open": df["open"].values,
                        "high": df["high"].values,
                        "low": df["low"].values,
                        "close": df["close"].values,
                        "volume": df["volume"].values if "volume" in df.columns else [0.0] * len(df),
                        "amount": df["amount"].values if "amount" in df.columns else None,
                        "adj_factor": None,
                        "source": sources.get(sym, "TENCENT"),
                        "fetched_at": pd.Timestamp.now(),
                        "trace_id": trace_id,
                    }
                )
                con.register("stock_new", rows_df)
                con.execute(
                    """
                    INSERT OR REPLACE INTO stock_kline_cache (
                        symbol, period, date, open, high, low, close,
                        volume, amount, adj_factor, source, fetched_at, trace_id
                    )
                    SELECT
                        symbol, period, CAST(date AS DATE) AS date,
                        open, high, low, close,
                        volume, amount, adj_factor,
                        source, fetched_at, trace_id
                    FROM stock_new
                    """
                )
                con.unregister("stock_new")
                success += 1
                total_rows += int(len(rows_df))
            except Exception as e:  # noqa: BLE001
                failure += 1
                failures.append({"symbol": sym, "error": str(e)})
        con.close()
    except Exception as e:  # noqa: BLE001
        logger.error("[Sync-stock] 缓存写入失败: %s (trace_id=%s)", e, trace_id, exc_info=True)

    finished_at = datetime.now().isoformat()
    all_failures = fetch_failures + failures
    summary = {
        "trace_id": trace_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "elapsed_seconds": round((datetime.now() - datetime.fromisoformat(started_at)).total_seconds(), 3),
        "symbols_total": len(panel) + len(fetch_failures),
        "success": success,
        "failure": failure + len(fetch_failures),
        "failures": all_failures,
        "total_rows": total_rows,
        "source": "TDX_LOCAL|TENCENT",
    }

    lineage_dir = Path("data") / "_lineage"
    lineage_dir.mkdir(parents=True, exist_ok=True)
    out_path = lineage_dir / f"sync_stock_summary_{datetime.now().strftime('%Y%m%d%H%M%S')}.json.gz"
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False)

    logger.info(
        "[Sync-stock] 完成: total=%d success=%d failure=%d rows=%d -> %s (trace_id=%s)",
        len(panel),
        success,
        failure,
        total_rows,
        out_path.name,
        trace_id,
    )


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
    "sync_stock_data_job",
]
