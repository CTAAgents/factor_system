"""
scripts/backfill_factor_quality_audit.py — 质检结果存量回填（GAP-128）。

背景: 晋升管线 `_write_to_duckdb`（v2.104.0+78 起）将 quality_score/audit_report
落 `factor_quality_scores`/`factor_audit_reports` 专属表；此前晋升的存量因子
质检记录仅内嵌 `factor_catalog.metadata`（JSON 字段），两表恒空。本脚本以
factor_catalog.metadata 为源（DB→DB，幂等先清后插），一次补齐全市场存量；
metadata 无质检记录的因子（旧因子未经历质检卡/审计）如实跳过不伪造。

用法:
    python scripts/backfill_factor_quality_audit.py [--market all|futures|energy]
        [--dry-run] [--json] [--trace-id TID]

market 路由: get_db_path 区分 energy / futures（股票剥离后并入 futures）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _parse_meta(raw) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def backfill_market(market: str, dry_run: bool, trace_id: str, db_path: str | Path | None = None) -> dict:
    """按 market 回填，返回统计。db_path 供测试注入隔离库（None 走 market 路由）。"""
    from fts.factor_engine.factor_db.repository import (
        FactorAuditReportRepository,
        FactorQualityScoreRepository,
        FactorRepository,
    )

    stats = {
        "market": market,
        "scanned": 0,
        "with_quality": 0,
        "with_audit": 0,
        "score_backfilled": 0,
        "report_backfilled": 0,
        "no_record": 0,
        "orphan_cleaned": 0,
    }

    repo = FactorRepository(market=market, db_path=db_path)
    try:
        # market=None 列出该库全量行（部分历史行 market 字段残留 futures/energy
        # 混存，按 market 过滤会漏掉，SSOT 以"所在库"为准）
        factors = repo.list_factors(market=None, limit=100000) or []
    finally:
        repo.close()
    stats["scanned"] = len(factors)

    for f in factors:
        meta = _parse_meta(f.get("metadata"))
        qs = meta.get("quality_score")
        ar = meta.get("audit_report")
        has_q = bool(qs and isinstance(qs, dict))
        has_a = bool(ar and isinstance(ar, dict))
        if has_q:
            stats["with_quality"] += 1
        if has_a:
            stats["with_audit"] += 1
        if not has_q and not has_a:
            stats["no_record"] += 1

    if dry_run:
        stats["score_backfilled"] = stats["with_quality"]
        stats["report_backfilled"] = stats["with_audit"]
        return stats

    qrepo = FactorQualityScoreRepository(market=market, db_path=db_path)
    arepo = FactorAuditReportRepository(market=market, db_path=db_path)
    try:
        for f in factors:
            factor_id = f.get("factor_id")
            meta = _parse_meta(f.get("metadata"))
            qs = meta.get("quality_score")
            ar = meta.get("audit_report")
            if qs and isinstance(qs, dict):
                qs = dict(qs)
                qs["factor_id"] = factor_id  # 强制对齐 catalog 主键（旧数据内嵌 id 可能缺失/伪影）
                qrepo.delete_scores_for_factor(factor_id)
                qrepo.save_score(qs)
                stats["score_backfilled"] += 1
            if ar and isinstance(ar, dict):
                ar = dict(ar)
                ar["factor_id"] = factor_id  # 同上
                arepo.delete_reports_for_factor(factor_id)
                arepo.save_report(ar)
                stats["report_backfilled"] += 1
        # 清理孤儿行：两表中 factor_id 不在 factor_catalog 的记录（旧数据
        # 内嵌 id 缺失/伪影产生的 'unknown'/'test_factor' 残留）
        try:
            import duckdb

            con = duckdb.connect(qrepo._db_path)
            try:
                n_q = con.execute(
                    "DELETE FROM factor_quality_scores WHERE factor_id NOT IN (SELECT factor_id FROM factor_catalog)"
                ).fetchone()
                n_a = con.execute(
                    "DELETE FROM factor_audit_reports WHERE factor_id NOT IN (SELECT factor_id FROM factor_catalog)"
                ).fetchone()
                con.execute("CHECKPOINT")
                stats["orphan_cleaned"] = int((n_q or (0,))[0]) + int((n_a or (0,))[0])
            finally:
                con.close()
        except Exception:  # noqa: BLE001 — 清理失败不阻断
            stats["orphan_cleaned"] = -1
    finally:
        qrepo.close()
        arepo.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="质检结果存量回填（GAP-128）")
    ap.add_argument("--market", choices=["all", "futures", "energy"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="仅统计不写库")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出统计")
    ap.add_argument(
        "--trace-id",
        default=f"backfill.qa.{datetime.now().strftime('%Y%m%d%H%M%S')}",
    )
    args = ap.parse_args()

    markets = ["futures", "energy"] if args.market == "all" else [args.market]
    results = [backfill_market(m, args.dry_run, args.trace_id) for m in markets]

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return 0

    print(f"trace_id={args.trace_id}  dry_run={args.dry_run}")
    print(
        f"{'market':<10} {'db_factors':>10} {'with_q':>7} {'with_a':>7} "
        f"{'score_backfilled':>15} {'report_backfilled':>16} {'no_record':>9} {'orphan':>7}"
    )
    for r in results:
        print(
            f"{r['market']:<10} {r['scanned']:>10} {r['with_quality']:>7} "
            f"{r['with_audit']:>7} {r['score_backfilled']:>15} "
            f"{r['report_backfilled']:>16} {r['no_record']:>9} {r['orphan_cleaned']:>7}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
