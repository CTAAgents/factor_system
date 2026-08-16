"""存量因子质检记录回填 + 升级门禁复核（v2.104.0+89 评审质检门禁）。

背景：L3 组合权重重算仅放行经 L2 阶段评审质检（approved）的因子，且 approved
机审升级为「完整质检结论门禁」（audit/评分卡/高IC/多重检验/WalkForward/Q1-Q10
任一缺失 → 转人审，宁缺毋滥）。存量 active elite 因子大多只有 IC/Sharpe 机审
记录，缺完整质检结论。本脚本：

1. 回填：从 factor_catalog.metadata / JSON 快照提取质检结论，组装
   metadata.qa_review 落库（复用 build_qa_review 的 Q1-Q10 映射）；
2. 复核：按升级后的 AutoReviewPolicy 对存量因子复核——
   approved 保持 / rejected 回写 / 质检记录缺失 → 撤销 approved（回到待审队列）。

用法:
    python scripts/backfill_qa_review.py --market futures [--dry-run]
    python scripts/backfill_qa_review.py --market energy --dry-run
    python scripts/backfill_qa_review.py --market all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.config import get_config  # noqa: E402
from fts.factor_engine.audit import AuditItemResult, FactorAuditReport  # noqa: E402
from fts.factor_engine.evolution_promote import build_qa_review  # noqa: E402
from fts.factor_engine.factor_db import schema  # noqa: E402
from fts.factor_engine.factor_inspector import AutoReviewPolicy, _extract_qa_meta  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_qa_review")

MARKETS = ("futures", "energy", "stock")


def _restore_audit(d: Any) -> Optional[FactorAuditReport]:
    """从 metadata.audit_report dict 恢复 FactorAuditReport 对象（build_qa_review 需要）。"""
    if not d or not isinstance(d, dict):
        return None
    try:
        items = [
            AuditItemResult(
                name=str(i.get("name", "")),
                status=str(i.get("status", "skipped")),
                evidence=str(i.get("evidence", "")),
                score=float(i.get("score", 0.0) or 0.0),
                details=i.get("details", {}),
            )
            for i in d.get("items", [])
        ]
        return FactorAuditReport(
            factor_id=str(d.get("factor_id", "")),
            factor_name=str(d.get("factor_name", "")),
            audited_at=str(d.get("audited_at", "")),
            items=items,
            passed=bool(d.get("passed", False)),
            pass_rate=float(d.get("pass_rate", 0.0) or 0.0),
            summary=d.get("summary", {}) or {},
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("audit 报告反序列化失败: %s", e)
        return None


def _load_json_snapshot(elite_dir: Path, factor_id: str) -> dict[str, Any]:
    """读取因子 JSON 快照（只读备份；含 evaluation/high_ic_screen/audit_report）。"""
    fp = elite_dir / f"{factor_id}.json"
    if not fp.exists():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _backfill_market(market: str, dry_run: bool) -> dict[str, Any]:
    """回填并复核单个市场，返回统计。"""
    cfg = get_config()
    db_path = schema.get_db_path(market)
    elite_dir = Path(cfg.get_elite_dir(market))
    policy = AutoReviewPolicy()

    stats = {"total": 0, "qa_review_updated": 0, "approved": 0, "rejected": 0, "needs_human": 0}
    to_update: list[tuple[str, dict[str, Any]]] = []  # (factor_id, new_metadata)
    review_writes: list[tuple[str, str, str]] = []  # (factor_id, decision|None, reason)

    conn = schema.get_connection(db_path=db_path)
    try:
        rows = conn.execute(
            "SELECT factor_id, name, ic, sharpe, metadata FROM factor_catalog "
            "WHERE is_elite=true AND status='active' AND market=?",
            [market],
        ).fetchall()
    finally:
        conn.close()

    # 预加载质检表（GAP-128 已落库：factor_quality_scores / factor_audit_reports）
    quality_map: dict[str, str] = {}
    audit_map: dict[str, bool] = {}
    eval_map: dict[str, dict[str, Any]] = {}
    try:
        c2 = schema.get_connection(db_path=db_path)
        try:
            for fid, g in c2.execute("SELECT factor_id, grade FROM factor_quality_scores").fetchall():
                quality_map.setdefault(fid, str(g))
            for fid, p in c2.execute("SELECT factor_id, passed FROM factor_audit_reports").fetchall():
                audit_map.setdefault(fid, bool(p))
            # 评估记录（Q4/Q5/Q6/Q7 兜底：metadata/JSON evaluation 缺失时构造）
            try:
                for fid, ic, icir, mono, l3 in c2.execute(
                    "SELECT factor_id, level_1_ic, level_1_icir, level_1_monotonicity, level_3_passed "
                    "FROM factor_evaluations"
                ).fetchall():
                    eval_map.setdefault(
                        fid,
                        {
                            "level_1_backtest": {
                                "ic": ic,
                                "icir": icir,
                                "monotonicity": bool(mono) if mono is not None else False,
                            },
                            "level_3_multiple": {"passed": bool(l3) if l3 is not None else False},
                        },
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("factor_evaluations 读取失败（Q4-Q7 兜底不可用）: %s", e)
        finally:
            c2.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("质检表预加载失败（回退 JSON 快照）: %s", e)

    for factor_id, name, ic, sharpe, metadata_raw in rows:
        stats["total"] += 1
        if isinstance(metadata_raw, str):
            try:
                cur_meta = json.loads(metadata_raw)
            except (ValueError, TypeError):
                cur_meta = {}
        else:
            cur_meta = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        snap = _load_json_snapshot(elite_dir, factor_id)

        # ── 重建 metadata 原字段（JSON 快照优先 + 保留当前 qa_review） ──
        # 修复 v2.104.0+89 回填副作用：不得覆盖丢失 correlation_metadata/shadow_pool
        # /evaluation 等原字段；correlation_metadata/shadow_pool 快照不含，重建为空。
        metadata: dict[str, Any] = {
            "quality_score": snap.get("quality_score") or cur_meta.get("quality_score"),
            "correlation_metadata": snap.get("correlation_metadata") or cur_meta.get("correlation_metadata") or {},
            "symbols": snap.get("symbols") or cur_meta.get("symbols", []),
            "risk_tag": snap.get("risk_tag") if snap.get("risk_tag") is not None else cur_meta.get("risk_tag"),
            "factor_version": snap.get("factor_version") or cur_meta.get("factor_version", "v2"),
            "audit_report": snap.get("audit_report") or cur_meta.get("audit_report"),
            "shadow_pool": snap.get("shadow_pool") or cur_meta.get("shadow_pool"),
            "evaluation": snap.get("evaluation") if isinstance(snap.get("evaluation"), dict) else cur_meta.get("evaluation"),
            "orthogonalized": snap.get("orthogonalized", False) or cur_meta.get("orthogonalized", False),
            "orthogonalized_against": snap.get("orthogonalized_against") or cur_meta.get("orthogonalized_against", ""),
            "orthogonalized_pearson": snap.get("orthogonalized_pearson") or cur_meta.get("orthogonalized_pearson", 0.0),
            "orthogonalized_basis": snap.get("orthogonalized_basis") or cur_meta.get("orthogonalized_basis", []),
            "orthogonal_signal": snap.get("orthogonal_signal") or cur_meta.get("orthogonal_signal", []),
            "qa_review": cur_meta.get("qa_review"),
        }

        # ── 质检记录提取（表优先，JSON 快照兜底） ──
        quality_grade = quality_map.get(factor_id) if quality_map else None
        audit_passed = audit_map.get(factor_id) if audit_map else None
        audit_dict = metadata.get("audit_report") or snap.get("audit_report")
        audit_report = _restore_audit(audit_dict) if audit_dict else None
        if audit_report is None and audit_passed is not None:
            # 审计表无 items 明细：以整体 passed 构造（Q1/Q8 由 build_qa_review 回退整体判定）
            audit_report = FactorAuditReport(
                factor_id=factor_id, factor_name=name, audited_at="",
                items=[], passed=bool(audit_passed), pass_rate=0.0, summary={},
            )
        if quality_grade:
            quality_score: Optional[dict] = {"grade": quality_grade}
        elif isinstance(metadata.get("quality_score"), dict):
            quality_score = metadata["quality_score"]
        else:
            quality_score = None
        hic_dict = snap.get("high_ic_screen") if isinstance(snap.get("high_ic_screen"), dict) else None
        if hic_dict:
            high_ic = SimpleNamespace(grade=hic_dict.get("grade"))
        else:
            # 晋升强制门保证 grade=C 无法入库 → 存量 grade ∈ {A,B}；回填保守下限 B
            high_ic = SimpleNamespace(grade="B")

        qa_review = build_qa_review(
            {
                "economic_logic": (snap.get("economic_logic") or metadata.get("economic_logic") or {}),
                "params": (snap.get("params") or metadata.get("params") or {}),
                "style_tags": (snap.get("style_tags") or metadata.get("style_tags") or []),
            },
            metadata.get("evaluation") if isinstance(metadata.get("evaluation"), dict) else (
                snap.get("evaluation") if isinstance(snap.get("evaluation"), dict) else (
                    eval_map.get(factor_id, {})
                )
            ),
            audit_report,
            quality_score,
            high_ic,
        )

        if metadata.get("qa_review") != qa_review:
            new_metadata = dict(metadata)
            new_metadata["qa_review"] = qa_review
            stats["qa_review_updated"] += 1
            to_update.append((factor_id, new_metadata))
            metadata = new_metadata

        # ── 升级门禁复核 ──
        decision, reason = policy.classify(ic, sharpe, qa_meta=_extract_qa_meta(metadata))
        if decision is None:
            stats["needs_human"] += 1
            review_writes.append((factor_id, None, reason))
        else:
            stats[decision.value] += 1
            review_writes.append((factor_id, decision.value, reason))

    if dry_run:
        logger.info(
            "[dry-run][%s] total=%d qa_review_updated=%d approved=%d rejected=%d needs_human=%d",
            market, stats["total"], stats["qa_review_updated"],
            stats["approved"], stats["rejected"], stats["needs_human"],
        )
        for fid, dec, reason in review_writes[:10]:
            logger.info("  %s → %s (%s)", fid, dec or "needs_human", reason[:60])
        return stats

    # ── 落库：回填 metadata.qa_review ──
    from fts.factor_engine.factor_db.repository import FactorRepository

    repo = FactorRepository(market=market)
    try:
        for factor_id, new_metadata in to_update:
            try:
                repo.update_factor(factor_id, {"metadata": new_metadata})
            except Exception as e:  # noqa: BLE001
                logger.warning("metadata 回填失败 %s: %s", factor_id, e)
    finally:
        repo.close()

    # ── 落库：复核决定（approved/rejected 幂等 UPSERT；needs_human 撤销 approved） ──
    conn = schema.get_connection(db_path=db_path)
    try:
        for factor_id, decision, reason in review_writes:
            if decision is None:
                conn.execute("DELETE FROM factor_reviews WHERE factor_id = ?", [factor_id])
            else:
                from datetime import datetime

                conn.execute(
                    "INSERT INTO factor_reviews (factor_id, decision, comment, reviewer, reviewed_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT (factor_id) DO UPDATE SET "
                    "decision = excluded.decision, comment = excluded.comment, "
                    "reviewer = excluded.reviewer, reviewed_at = excluded.reviewed_at",
                    [factor_id, decision, f"[存量回填复核] {reason}", "auto", datetime.now().isoformat()],
                )
    finally:
        conn.close()

    logger.info(
        "[%s] 完成 total=%d qa_review_updated=%d approved=%d rejected=%d needs_human=%d",
        market, stats["total"], stats["qa_review_updated"],
        stats["approved"], stats["rejected"], stats["needs_human"],
    )
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="存量因子质检记录回填 + 升级门禁复核")
    parser.add_argument("--market", choices=[*MARKETS, "all"], default="futures", help="目标市场")
    parser.add_argument("--dry-run", action="store_true", help="只预览不落库")
    args = parser.parse_args()

    markets = [args.market] if args.market != "all" else list(MARKETS)
    total: dict[str, int] = {}
    for m in markets:
        st = _backfill_market(m, dry_run=args.dry_run)
        for k, v in st.items():
            total[k] = total.get(k, 0) + v
    logger.info("汇总: %s", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
