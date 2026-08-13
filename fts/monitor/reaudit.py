"""
fts/monitor/reaudit.py — 新标准准入复审（月度任务 / 手动 CLI 共用）。

背景: 2026-08-11~13 系统升级引入新准入标准（GAP-079 oos 判定修复、
GAP-096 cross_symbol A+C 双机制、鲁棒性审查 11 项测试、质量评分卡）。
存量 active elite 因子（factor_audit_reports=0、factor_quality_scores=0）
未经新标准检验。本模块对 active elite 因子复用演化准入链
（横截面评估 → Verifier → 审计 → 鲁棒性 → 质量评分卡）重新检验。

处置规则:
    retain  evaluation + verifier + audit + robustness 全过
    shadow  robustness 失败（降级观察池，L3 暂不纳入组合）
    retire  audit 失败或 evaluation 不合格
    error   因子代码不可执行（单独列出，人工核查）

消费方:
    - fts.scheduler.jobs.monthly_decay_eval_job  — 月度任务 Step A（apply=True）
    - scripts/reaudit_futures_elite.py           — 手动 CLI（薄包装）

trace_id 全链路: 调用方传入或模块自动生成。
"""

from __future__ import annotations

import json
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DECISION_RETAIN = "retain"
DECISION_SHADOW = "shadow"
DECISION_RETIRE = "retire"
DECISION_ERROR = "error"

_OUT_DIR = Path("memory") / "logs" / "evolution" / "futures"


@dataclass
class ReauditReport:
    """一次重审的结果汇总。"""

    trace_id: str
    total: int
    counts: dict[str, int]
    results: list[dict[str, Any]] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "generated_at": self.generated_at,
            "total": self.total,
            "counts": self.counts,
            "results": self.results,
        }


def load_active_elite(market: str = "futures") -> list[dict[str, Any]]:
    """读取全部 active elite 因子（DuckDB SSOT）。"""
    from fts.factor_engine.factor_db.repository import FactorRepository

    repo = FactorRepository(market=market)
    try:
        rows = repo.list_factors(
            market=market, status="active", is_elite=True, limit=10000, sort_by="sharpe", sort_order="desc"
        )
        return rows or []
    finally:
        repo.close()


def build_factor_program(f: dict[str, Any]) -> dict[str, Any] | None:
    """从 catalog 行构造 FactorProgram（code 为空返回 None）。"""
    code = f.get("code") or ""
    if not code:
        return None
    return {
        "factor_id": f["factor_id"],
        "name": f.get("name", f["factor_id"]),
        "code": code,
        "params": f.get("params") or {},
        "family": f.get("family") or "other",
        "market": "futures",
        "economic_logic": f.get("economic_logic") or {},
    }


def summarize_result(record: dict[str, Any]) -> str:
    """按处置规则给单因子判定。"""
    if record.get("error"):
        return DECISION_ERROR
    ev_passed = record["evaluation_passed"]
    vr_passed = record["verifier_passed"]
    ar_passed = record["audit_passed"]
    rr_passed = record["robustness_passed"]
    if ev_passed and vr_passed and ar_passed and rr_passed:
        return DECISION_RETAIN
    if not rr_passed:
        return DECISION_SHADOW
    return DECISION_RETIRE


def _evaluate_one(
    loop: Any,
    fp: dict[str, Any],
    trace_id: str,
) -> dict[str, Any]:
    """对单个因子执行完整准入链评估，返回结果记录。"""
    rec: dict[str, Any] = {
        "factor_id": fp["factor_id"],
        "name": fp.get("name", fp["factor_id"]),
        "trace_id": trace_id,
        "processed_at": datetime.now().isoformat(),
    }
    ev = loop._evaluate_cross_section(fp, trace_id)
    l1 = ev.get("level_1_backtest") or {}
    rec["evaluation_passed"] = bool(ev.get("passed"))
    rec["ic"] = l1.get("ic")
    rec["sharpe"] = l1.get("sharpe")
    rec["icir"] = l1.get("icir")
    rec["failure_reasons"] = ev.get("failure_reasons", [])

    vr = loop.verifier.check(ev)
    rec["verifier_passed"] = bool(vr.get("passed", True)) if isinstance(vr, dict) else True

    ar = loop._run_factor_audit(fp, ev, trace_id)
    rec["audit_passed"] = bool(ar.passed)
    rec["audit_failures"] = [it.name for it in ar.failed_items]
    rec["audit_pass_rate"] = ar.pass_rate

    rr = loop._run_robustness_check(fp, ev, trace_id)
    rsum = (rr or {}).get("summary", {})
    rec["robustness_passed"] = bool(rr.get("passed", True))
    rec["robustness_pass_rate"] = rsum.get("overall_pass_rate")
    rec["robustness_summary"] = rsum

    qi = loop.quality_inspector.inspect(factor=fp, evaluation=ev)
    rec["grade"] = getattr(qi, "grade", None) or (qi.get("grade") if isinstance(qi, dict) else None)
    rec["quality_score"] = getattr(qi, "total_score", None) or (qi.get("total_score") if isinstance(qi, dict) else None)
    rec["decision"] = summarize_result(rec)
    return rec


def run_reaudit(
    market: str = "futures",
    days: int = 750,
    trace_id: str = "",
    apply: bool = False,
    factor_ids: list[str] | None = None,
    panel: dict[str, Any] | None = None,
    common_dates: Any = None,
    fwd_ret: Any = None,
    out_json: bool = True,
) -> ReauditReport:
    """对 active elite 因子执行新标准全量重审。

    Args:
        market: 市场（期货）
        days: 回溯天数（与演化同口径，默认 750）
        trace_id: 全链路 trace_id（空则自动生成）
        apply: 是否立即按处置规则回写 DuckDB
        factor_ids: 只重审指定因子子集（测试/定向用，None=全部 active elite）
        panel/common_dates/fwd_ret: 注入面板（测试用；None 走真实数据准备）
        out_json: 是否将结果落盘 memory/logs/evolution/futures/reaudit_{date}.json

    Returns:
        ReauditReport
    """
    from fts.cli import _prepare_futures_data, _relaxed_futures_audit_config, _relaxed_futures_quality_config
    from fts.config import get_config
    from fts.factor_engine.evolution_futures import EvolutionLoop
    from fts.factor_engine.seed_pool import SeedPool

    trace_id = trace_id or f"fts.reaudit.{datetime.now().strftime('%Y%m%d%H%M%S')}"
    logger.info("[reaudit] 启动 trace_id=%s market=%s", trace_id, market)

    if panel is None:
        logger.info("[reaudit] 准备期货横截面面板 ...")
        panel, common_dates, fwd_ret = _prepare_futures_data(days=days, max_symbols=0)
        logger.info("[reaudit] 面板品种=%d 共同日期=%d", len(panel), len(common_dates))
    first_sym = list(panel.keys())[0]

    cfg = get_config()
    loop = EvolutionLoop(
        data=panel[first_sym],
        forward_returns=fwd_ret,
        elite_dir=cfg.get_elite_dir(market),
        memory_dir=cfg.memory_dir + "/evolution/" + market,
        llm_client=None,
        seed_pool=SeedPool(market=market),
        n_trials_micro=10,
        cross_section_data=panel,
        cross_section_dates=common_dates,
        market=market,
        quality_card_config=_relaxed_futures_quality_config(),
        audit_config=_relaxed_futures_audit_config(),
    )

    factors = load_active_elite(market=market)
    if factor_ids:
        id_set = set(factor_ids)
        factors = [f for f in factors if f["factor_id"] in id_set]
    logger.info("[reaudit] active elite=%d，本次处理 %d 个", len(load_active_elite(market=market)), len(factors))

    results: list[dict[str, Any]] = []
    for f in factors:
        fid = f["factor_id"]
        fp = build_factor_program(f)
        if fp is None:
            results.append(
                {"factor_id": fid, "name": f.get("name", fid), "trace_id": trace_id, "error": "catalog 无 code"}
            )
            continue
        try:
            results.append(_evaluate_one(loop, fp, trace_id))
        except Exception as e:  # noqa: BLE001
            results.append(
                {
                    "factor_id": fid,
                    "name": fp.get("name", fid),
                    "trace_id": trace_id,
                    "error": f"{type(e).__name__}: {e}",
                    "error_trace": traceback.format_exc(limit=3),
                }
            )
            logger.warning("[reaudit] 因子 %s 评估异常: %s", fid, e)

    from collections import Counter

    counts = {k: v for k, v in Counter(r.get("decision", "error") for r in results).items()}
    report = ReauditReport(
        trace_id=trace_id,
        total=len(results),
        counts=counts,
        results=results,
        generated_at=datetime.now().isoformat(),
    )
    logger.info("[reaudit] 汇总: %s", counts)

    if out_json:
        try:
            _OUT_DIR.mkdir(parents=True, exist_ok=True)
            fp = _OUT_DIR / f"reaudit_{datetime.now().strftime('%Y%m%d')}.json"
            payload: dict[str, Any] = {"trace_id": trace_id, "generated_at": report.generated_at, "results": results}
            if fp.exists():
                try:
                    old = json.loads(fp.read_text(encoding="utf-8"))
                    old_ids = {r["factor_id"] for r in old["results"]}
                    payload["results"] = old["results"] + [r for r in results if r["factor_id"] not in old_ids]
                except Exception:  # noqa: BLE001
                    pass
            fp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            logger.info("[reaudit] 结果已保存: %s", fp)
        except Exception as e:  # noqa: BLE001
            logger.warning("[reaudit] 结果落盘失败: %s", e)

    if apply:
        apply_reaudit_results(results, trace_id)
    return report


def apply_reaudit_results(
    results: list[dict[str, Any]], trace_id: str = "", db_path: str | Path | None = None
) -> dict[str, int]:
    """按处置规则回写 DuckDB（retain/shadow/retire + status_history 留痕）。

    Args:
        results: run_reaudit 的结果记录
        trace_id: 审计留痕用 trace_id
        db_path: 注入测试用隔离库路径（None 走 market 路由）

    Returns:
        {retain: n, shadow: n, retire: n, error: n, failed: n}
    """
    import numpy as np

    from fts.factor_engine.factor_db.repository import FactorRepository, FactorStatusRepository

    repo = FactorRepository(market="futures", db_path=db_path)
    srepo = FactorStatusRepository(market="futures", db_path=db_path)
    ok: dict[str, int] = {"retain": 0, "shadow": 0, "retire": 0}
    failed = 0
    try:
        for r in results:
            fid = r["factor_id"]
            decision = r.get("decision")
            if decision == DECISION_ERROR:
                continue
            try:
                f = repo.get_factor(fid)
                if not f:
                    logger.warning("[reaudit] 因子不存在: %s", fid)
                    failed += 1
                    continue
                meta = f.get("metadata") or {}
                if isinstance(meta, str):
                    meta = json.loads(meta)
                meta["reaudit"] = {
                    "at": r.get("processed_at"),
                    "trace_id": r.get("trace_id", trace_id),
                    "decision": decision,
                    "ic": r.get("ic"),
                    "sharpe": r.get("sharpe"),
                    "audit_passed": r.get("audit_passed"),
                    "audit_failures": r.get("audit_failures", []),
                    "robustness_pass_rate": r.get("robustness_pass_rate"),
                    "grade": r.get("grade"),
                }
                if decision == DECISION_RETAIN:
                    repo.update_factor(fid, {"metadata": meta})
                    srepo.log_transition(
                        fid,
                        "active",
                        "active",
                        f"新标准重审通过（reaudit {trace_id}）",
                        snapshot={"reaudit": meta["reaudit"]},
                    )
                    ok["retain"] += 1
                elif decision == DECISION_SHADOW:
                    now = datetime.now()
                    end = np.busday_offset(now.date(), 5, roll="forward")
                    meta["shadow_pool"] = {
                        "promoted_at": now.isoformat(),
                        "observe_trading_days": 5,
                        "observe_until": datetime.combine(end.astype(object), datetime.min.time()).isoformat(),
                        "reason": "reaudit_robustness_failed",
                    }
                    repo.update_factor(fid, {"metadata": meta})
                    srepo.log_transition(
                        fid,
                        "active(shadow)",
                        "active(shadow)",
                        f"重审鲁棒性未达标，降级观察池（robust={r.get('robustness_pass_rate')}）",
                        snapshot={"reaudit": meta["reaudit"], "shadow_pool": meta["shadow_pool"]},
                    )
                    ok["shadow"] += 1
                elif decision == DECISION_RETIRE:
                    srepo.update_factor_status(fid, "retired")
                    repo.update_factor(fid, {"metadata": meta})
                    srepo.log_transition(
                        fid,
                        "active",
                        "retired",
                        f"重审不合格淘汰（audit={r.get('audit_passed')} failed={r.get('audit_failures')}）",
                        snapshot={"reaudit": meta["reaudit"]},
                    )
                    ok["retire"] += 1
            except Exception as e:  # noqa: BLE001
                failed += 1
                logger.error("[reaudit] 处置失败 %s: %s", fid, e)
    finally:
        repo.close()
        srepo.close()
    logger.info("[reaudit] 处置完成: %s", ok)
    return ok
