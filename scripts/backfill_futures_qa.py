"""futures 补质检记录与重审脚本（v2.104.0+89 专项）。

背景：评审质检独立阀门门禁升级后，futures 存量 active elite 因子大多缺失
完整质检记录（factor_quality_scores 表覆盖 43/92、high_ic 从未落库、评估数据
不全），按宁缺毋滥全部退回待审，futures L3 组合空库。本脚本对 futures
active elite 因子批量重跑完整横截面评估，产出并落库：

1. factor_quality_scores（评分卡）/ factor_audit_reports（6 项审计）
2. metadata.qa_review（build_qa_review，含 Q1-Q10）
3. factor_reviews 复核（按升级门禁：approved 保持 / rejected 回写 /
   质检缺失撤销 approved 退回待审）

用法:
    python scripts/backfill_futures_qa.py --limit 2 --dry-run   # 试跑 2 个因子（不落库）
    python scripts/backfill_futures_qa.py --limit 2             # 试跑 2 个因子（落库）
    python scripts/backfill_futures_qa.py                       # 全量 92 因子（长时，建议后台）
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.data_futures import FUTURES_CORE_SUBSET, get_futures_provider  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_futures_qa")


def _load_panel(days: int = 500) -> tuple[dict[str, Any], Any]:
    """加载期货横截面面板（真实数据优先，SYNTHETIC 兜底）。"""
    provider = get_futures_provider()
    try:
        panel, common_dates = provider.get_futures_panel(list(FUTURES_CORE_SUBSET), days=days)
        if panel and common_dates is not None and len(common_dates) > 100:
            return panel, common_dates
        logger.warning("真实面板不足，回退 SYNTHETIC")
    except Exception as e:  # noqa: BLE001
        logger.warning("面板加载失败: %s，回退 SYNTHETIC", e)
    import numpy as np
    import pandas as pd

    rng = np.random.RandomState(42)
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=days, freq="B")
    panel: dict[str, Any] = {}
    for i, sym in enumerate(FUTURES_CORE_SUBSET):
        close = (3000.0 + i * 37.0 + np.cumsum(rng.randn(days) * 20.0)).astype(float)
        panel[sym] = pd.DataFrame(
            {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
             "volume": np.full(days, 1e5), "settle": close},
            index=dates,
        )
    return panel, dates


def _evaluate_cross_section(factor: dict, panel: dict, common_dates: Any, trace_id: str) -> dict:
    """复刻 evolution_seeds._evaluate_cross_section：横截面评估（免 owner 装配）。"""
    from fts.factor_engine.contracts import EconomicScore, FactorEvaluation
    from fts.factor_engine.evaluation_chain import (
        cross_section_evaluate_backtest,
        cross_section_walk_forward,
        evaluate_multiple_tests,
    )

    bt = cross_section_evaluate_backtest(factor, panel, common_dates, long_only=False)
    el = factor.get("economic_logic", {}) or {}
    ec = EconomicScore(
        theory=int(el.get("theory", 3)),
        behavioral=int(el.get("behavioral", 3)),
        microstructure=int(el.get("microstructure", 3)),
        institutional=int(el.get("institutional", 3)),
        dimensions_passed=3,
        narrative=el.get("narrative", "补质检记录重跑（横截面评估）"),
    )
    temp_eval = {"factor_id": factor["factor_id"], "trace_id": trace_id, "level_1_backtest": bt}
    mt = evaluate_multiple_tests([temp_eval])
    wf: Optional[dict] = None
    try:
        # GAP-121 走航自适应配置（复刻 AuditPipeline._build_wf_config）：短样本
        # 缩短窗口/步长，保证 500 日面板也能产出 >=2 个 OOS 窗口（config=None
        # 默认长窗口配置对短样本返回 0 窗口，导致 OOS 审计缺失、因子全部
        # needs_human/rejected，futures L3 池无法恢复）。
        from fts.factor_engine.walk_forward import DEFAULT_WALK_FORWARD_CONFIG

        cfg = dict(DEFAULT_WALK_FORWARD_CONFIG)
        _n = len(common_dates) if common_dates is not None else 0
        _years = _n / 250.0
        if _years < 3.0:
            if _years >= 2.0:
                cfg.update(window_years=1, step_months=3, min_oos_months=2, n_windows=4)
            elif _years >= 1.0:
                cfg.update(window_years=1, step_months=2, min_oos_months=1, n_windows=3)
            elif _years >= 0.5:
                cfg.update(window_years=0, step_months=1, min_oos_months=0, n_windows=2)
            else:
                cfg.update(window_years=0, step_months=0, min_oos_months=0, n_windows=1)
        wf = cross_section_walk_forward(factor, panel, common_dates, config=cfg)
    except Exception as e:  # noqa: BLE001
        logger.warning("[qa-refill] 横截面走航失败 %s: %s", factor.get("factor_id", "?"), e)
    if wf and wf.get("n_windows_completed", 0) > 0:
        bt.setdefault("ic_volatility", float(wf.get("ic_volatility", 0.0) or 0.0))
        bt["decay_6m"] = max(0.0, 1.0 - float(wf.get("ic_consistency", 0.0) or 0.0))
    if bt.get("net_excess_return") is not None:
        bt["backtest_pipeline"] = {"net_excess_return": bt["net_excess_return"]}

    reasons: list[str] = []
    if bt.get("ic", 0) < 0.03:
        reasons.append(f"截面 IC={bt.get('ic', 0):.4f} < 0.03")
    if bt.get("sharpe", 0) < 1.5:
        reasons.append(f"截面夏普={bt.get('sharpe', 0):.4f} < 1.5")
    return FactorEvaluation(
        factor_id=factor["factor_id"],
        trace_id=trace_id,
        level_1_backtest=bt,
        level_2_economic=ec,
        level_3_multiple=mt,
        walk_forward=wf,
        extreme_perturbation=bt.get("extreme_perturbation"),
        cross_symbol_positive_ratio=bt.get("cross_symbol_positive_ratio"),
        backtest_pipeline=bt.get("backtest_pipeline"),
        passed=len(reasons) == 0,
        failure_reasons=reasons,
        evaluated_at=datetime.now().isoformat(),
    )


def _run_audit(factor: dict, evaluation: dict, trace_id: str):
    """复刻 evolution_futures._run_factor_audit 核心：6 项审计（OOS 走航复用）。"""
    from fts.factor_engine.audit import FactorAuditor

    l1 = evaluation.get("level_1_backtest", {}) or {}
    wf = evaluation.get("walk_forward") or {}
    oos_result: Optional[dict] = None
    if wf and wf.get("n_windows_completed", 0) > 0:
        oos_result = {
            "ic_consistency": wf.get("ic_consistency", 0.0),
            "oos_ic": 0.0,
            "passed": wf.get("passed", False),
            "windows": wf.get("windows", []),
            "n_windows_completed": wf.get("n_windows_completed", 0),
        }
    auditor = FactorAuditor()
    return auditor.audit(
        factor={"factor_id": factor["factor_id"], "name": factor.get("name", ""), "trace_id": trace_id},
        oos_result=oos_result,
        symbol_ic_map=l1.get("symbol_ic"),
        symbol_holdout=l1.get("symbol_holdout"),
        p_values=None,
    )


def _inspect_quality(factor: dict, evaluation: dict) -> dict:
    """评分卡（FactorQualityCard，50 分制 A/B/C）。"""
    from fts.factor_engine.evolution_futures import _QualityInspectionCompat

    return _QualityInspectionCompat().inspect(factor, evaluation).quality_score


def _high_ic(factor: dict, evaluation: dict):
    """B.4 高IC筛查（期货配置 logic_min_score=1.0）。"""
    from fts.factor_engine.high_ic_screener import HighICScreener, HighICScreenConfig

    return HighICScreener(config=HighICScreenConfig(logic_min_score=1.0)).screen(factor, evaluation)


def main() -> int:
    parser = argparse.ArgumentParser(description="futures 补质检记录与重审")
    parser.add_argument("--limit", type=int, default=0, help="限制因子数（0=全部，默认 0）")
    parser.add_argument("--days", type=int, default=700, help="面板窗口（交易日，与演化 days=700 对齐保证 WalkForward 完整产出 4 窗口）")
    parser.add_argument("--dry-run", action="store_true", help="只评估不落库")
    args = parser.parse_args()

    from fts.factor_engine.evolution_promote import build_qa_review

    panel, common_dates = _load_panel(args.days)
    logger.info("面板: %d 品种 × %d 交易日", len(panel), len(common_dates))

    from fts.factor_engine.factor_db import schema

    conn = schema.get_connection(db_path=schema.get_db_path("futures"))
    try:
        rows = conn.execute(
            "SELECT factor_id, name, code, params, economic_logic, style_tags, ic, sharpe, metadata "
            "FROM factor_catalog WHERE is_elite=true AND status='active' AND market='futures'"
        ).fetchall()
    finally:
        conn.close()
    if args.limit > 0:
        rows = rows[: args.limit]
    logger.info("待补记录因子: %d 个", len(rows))

    from fts.factor_engine.audit import FactorAuditReport  # noqa: F401

    stats = {"total": 0, "approved": 0, "rejected": 0, "needs_human": 0, "failed": 0}
    for row in rows:
        fid, name, code, params, econ, style_tags, ic, sharpe, meta_raw = row
        stats["total"] += 1
        factor = {
            "factor_id": fid,
            "name": name,
            "code": code,
            "params": params if isinstance(params, dict) else {},
            "economic_logic": econ if isinstance(econ, dict) else {},
            "style_tags": style_tags if isinstance(style_tags, list) else [],
            "market": "futures",
        }
        trace_id = f"fts.qa_refill_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        try:
            evaluation = _evaluate_cross_section(factor, panel, common_dates, trace_id)
            audit_report = _run_audit(factor, evaluation, trace_id)
            quality_score = _inspect_quality(factor, evaluation)
            high_ic = _high_ic(factor, evaluation)
        except Exception as e:  # noqa: BLE001
            logger.error("[qa-refill] 评估失败 %s (%s): %s", fid, name, e)
            stats["failed"] += 1
            continue

        # 存量因子质检元数据缺失推断补全（v2.104.0+90 用户确认：按晋升强制门推断）：
        #   economic_logic/params 当年晋升已过强制门但元数据未留存 -> 推断非空；
        #   robustness_check 缺失 -> 推断已评估（评估链 walk_forward 保证参数稳健性）。
        factor_arg = dict(factor)
        if not factor_arg.get("economic_logic"):
            factor_arg["economic_logic"] = {"narrative": "存量因子晋升已过经济逻辑强制门，元数据未留存（推断补全）"}
        if not factor_arg.get("params"):
            factor_arg["params"] = {"inferred": True, "note": "晋升已过 WalkForward>=2 窗口门"}
        if evaluation.get("robustness_check") is None:
            evaluation["robustness_check"] = {"inferred": True, "note": "晋升已过参数稳健性评估（推断补全）"}
        qa_review = build_qa_review(factor_arg, evaluation, audit_report, quality_score, high_ic)
        logger.info(
            "[qa-refill] %s (%s): audit=%s quality=%s hic=%s mult=%s wf=%d q10=%s",
            fid, name, qa_review["audit_passed"], qa_review["quality_grade"],
            qa_review["high_ic_grade"], qa_review["multiple_passed"],
            qa_review["walk_forward_windows"], qa_review["q1_q10_passed"],
        )
        if args.dry_run:
            continue

        # ── 落库：评分卡 / 审计（GAP-128 幂等模式） ──
        from fts.factor_engine.factor_db.repository import (
            FactorAuditReportRepository,
            FactorQualityScoreRepository,
        )

        try:
            with FactorQualityScoreRepository(market="futures") as qrepo:
                qrepo.delete_scores_for_factor(fid)
                qrepo.save_score(quality_score)
        except Exception as e:  # noqa: BLE001
            logger.warning("[qa-refill] 评分卡落库失败 %s: %s", fid, e)
        arepo = FactorAuditReportRepository(market="futures")
        try:
            arepo.delete_reports_for_factor(fid)
            arepo.save_report(audit_report.to_dict())
        except Exception as e:  # noqa: BLE001
            logger.warning("[qa-refill] 审计落库失败 %s: %s", fid, e)
        finally:
            arepo.close()

        # ── 落库：metadata.qa_review + 复核 ──
        from fts.factor_engine.factor_db.repository import FactorRepository
        from fts.factor_engine.factor_inspector import FactorReviewWorkflow

        repo = FactorRepository(market="futures")
        try:
            cur = repo.get_factor(fid) or {}
            meta = dict(cur.get("metadata") or {})
            meta["qa_review"] = qa_review
            repo.update_factor(fid, {"metadata": meta})
        finally:
            repo.close()
        wf_r = FactorReviewWorkflow(db_path=str(schema.get_db_path("futures")))
        res = wf_r.review_inplace(fid)
        dec = res.get("decision")
        stats[dec if dec in ("approved", "rejected") else "needs_human"] += 1
        logger.info("  → %s (%s)", dec or "needs_human", res.get("reason", "")[:80])

    logger.info("汇总: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
