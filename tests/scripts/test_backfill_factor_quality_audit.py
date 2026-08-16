"""GAP-128：质检结果存量回填脚本测试（隔离 DuckDB）。

覆盖: 两表落库 / 幂等重跑 / 伪影 factor_id 强制对齐 / dry-run 不写库。
"""
from __future__ import annotations

from pathlib import Path

import duckdb

from fts.factor_engine.factor_db.repository import (
    FactorAuditReportRepository,
    FactorQualityScoreRepository,
    FactorRepository,
)
from scripts.backfill_factor_quality_audit import backfill_market


def _count(db: Path, table: str, factor_id: str) -> int:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table} WHERE factor_id = ?", [factor_id]).fetchone()[0]
    finally:
        con.close()


def _make_db(tmp_path: Path) -> Path:
    db = tmp_path / "iso.duckdb"
    repo = FactorRepository(db_path=db, market="futures")
    repo.create_factor(
        {
            "factor_id": "fct_1",
            "name": "a",
            "code": "close",
            "market": "futures",
            "status": "active",
            "metadata": {
                "quality_score": {
                    "score_id": "qsc_fct_1",
                    "factor_id": "fct_1",
                    "total_score": 80.0,
                    "dimension_scores": [],
                    "grade": "A",
                    "evaluated_at": "2026-08-16T00:00:00",
                    "score_version": "v1",
                },
                "audit_report": {
                    "report_id": "far_fct_1",
                    "factor_id": "fct_1",
                    "passed": True,
                    "overall_score": 90.0,
                    "summary": {"total": 7, "passed": 6, "failed_items": []},
                    "audited_at": "2026-08-16T00:00:00",
                    "audit_version": "v1",
                },
            },
        }
    )
    # fct_2: 内嵌 factor_id 缺失/伪影（旧晋升路径产物），须强制对齐 catalog 主键
    repo.create_factor(
        {
            "factor_id": "fct_2",
            "name": "b",
            "code": "close",
            "market": "futures",
            "status": "active",
            "metadata": {
                "quality_score": {
                    "score_id": "qsc_fct_2",
                    "factor_id": None,
                    "total_score": 70.0,
                    "dimension_scores": [],
                    "grade": "B",
                    "evaluated_at": "2026-08-16T00:00:00",
                    "score_version": "v1",
                },
                "audit_report": {
                    "report_id": "far_fct_2",
                    "factor_id": "test_factor",
                    "passed": True,
                    "overall_score": 85.0,
                    "summary": {"total": 7, "passed": 6, "failed_items": []},
                    "audited_at": "2026-08-16T00:00:00",
                    "audit_version": "v1",
                },
            },
        }
    )
    # fct_3: 无质检记录（旧因子未经历质检卡），如实跳过
    repo.create_factor(
        {
            "factor_id": "fct_3",
            "name": "c",
            "code": "close",
            "market": "futures",
            "status": "active",
            "metadata": {},
        }
    )
    repo.close()
    return db


def test_backfill_populates_tables(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    stats = backfill_market("futures", False, "t", db_path=db)
    assert stats["scanned"] == 3
    assert stats["score_backfilled"] == 2
    assert stats["report_backfilled"] == 2
    assert stats["no_record"] == 1
    assert _count(db, "factor_quality_scores", "fct_1") == 1
    assert _count(db, "factor_quality_scores", "fct_2") == 1
    assert _count(db, "factor_audit_reports", "fct_1") == 1
    assert _count(db, "factor_audit_reports", "fct_2") == 1
    assert _count(db, "factor_quality_scores", "fct_3") == 0


def test_backfill_idempotent(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    backfill_market("futures", False, "t", db_path=db)
    backfill_market("futures", False, "t", db_path=db)
    assert _count(db, "factor_quality_scores", "fct_1") == 1
    assert _count(db, "factor_quality_scores", "fct_2") == 1
    assert _count(db, "factor_audit_reports", "fct_1") == 1
    assert _count(db, "factor_audit_reports", "fct_2") == 1


def test_backfill_forces_catalog_factor_id(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    backfill_market("futures", False, "t", db_path=db)
    with FactorQualityScoreRepository(db_path=db, market="futures") as q:
        latest = q.get_latest_score("fct_2")
        assert latest is not None and latest["factor_id"] == "fct_2"
        assert q.get_latest_score("unknown") is None  # 伪影 id 已被强制纠正
    a = FactorAuditReportRepository(db_path=db, market="futures")
    try:
        latest = a.get_latest_report("fct_2")
        assert latest is not None and latest["factor_id"] == "fct_2"
        assert a.get_latest_report("test_factor") is None
    finally:
        a.close()


def test_backfill_dry_run_no_write(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    stats = backfill_market("futures", True, "t", db_path=db)
    assert stats["score_backfilled"] == 2
    assert stats["report_backfilled"] == 2
    assert _count(db, "factor_quality_scores", "fct_1") == 0
    assert _count(db, "factor_audit_reports", "fct_1") == 0
