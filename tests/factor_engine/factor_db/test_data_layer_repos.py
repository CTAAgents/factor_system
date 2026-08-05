"""factor_db 数据层补齐测试 — A.1/A.2/B.3 新表与仓储（Stage 1）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from fts.factor_engine.factor_db import (  # noqa: E402
    FactorAuditReportRepository,
    FactorQualityScoreRepository,
    FactorRepository,
    FactorStatusRepository,
    init_database,
)
from fts.factor_engine.factor_db.schema import get_connection  # noqa: E402


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """临时数据库路径（已初始化）。"""
    path = tmp_path / "test_factor_catalog.duckdb"
    init_database(path)
    return path


# ─── Schema 层 ──────────────────────────────────────────────


class TestSchemaTables:
    """Schema 新增表与幂等性。"""

    def test_new_tables_created(self, db_path: Path):
        conn = get_connection(db_path)
        try:
            tables = {t[0] for t in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main'"
            ).fetchall()}
        finally:
            conn.close()
        assert "factor_quality_scores" in tables
        assert "factor_status_history" in tables
        assert "factor_audit_reports" in tables

    def test_init_idempotent(self, db_path: Path):
        """重复 init_database 不报错（幂等）。"""
        init_database(db_path)  # 第二次调用
        init_database(db_path)  # 第三次调用

    def test_catalog_status_extensions(self, db_path: Path):
        """factor_catalog 生命周期扩展字段存在。"""
        conn = get_connection(db_path)
        try:
            cols = {r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='factor_catalog'"
            ).fetchall()}
        finally:
            conn.close()
        for col in ("status_updated_at", "consecutive_ic_negative_months",
                    "consecutive_sharpe_drop_months", "last_incremental_eval_at",
                    "decay_rate_3m", "decay_rate_6m"):
            assert col in cols, f"缺少列: {col}"


# ─── A.1 评分卡仓储 ─────────────────────────────────────────


class TestQualityScoreRepository:
    """FactorQualityScoreRepository CRUD。"""

    def test_save_and_get_latest(self, db_path: Path):
        repo = FactorQualityScoreRepository(db_path)
        try:
            score = {
                "factor_id": "fct_a1",
                "total_score": 42.0,
                "grade": "A",
                "dimension_scores": [
                    {"name": "ic_score", "raw_value": 0.05, "score": 3.0},
                    {"name": "sharpe_score", "raw_value": 2.1, "score": 4.0},
                ],
                "evaluated_at": "2026-08-06T00:00:00+00:00",
            }
            repo.save_score(score)
            latest = repo.get_latest_score("fct_a1")
            assert latest is not None
            assert latest["total_score"] == 42.0
            assert latest["grade"] == "A"
            assert latest["ic_score"] == 3.0  # 维度快捷列
            assert latest["sharpe_score"] == 4.0
            assert isinstance(latest["dimension_scores"], list)
            assert latest["dimension_scores"][0]["name"] == "ic_score"
        finally:
            repo.close()

    def test_history_kept(self, db_path: Path):
        """多次保存保留历史，get_latest 取最新。"""
        repo = FactorQualityScoreRepository(db_path)
        try:
            for i, total in enumerate((30.0, 45.0)):
                repo.save_score({
                    "factor_id": "fct_a1",
                    "total_score": total,
                    "grade": "B" if total < 40 else "A",
                    "dimension_scores": [],
                    "evaluated_at": f"2026-08-0{i + 1}T00:00:00+00:00",
                })
            history = repo.get_score_history("fct_a1")
            assert len(history) == 2
            latest = repo.get_latest_score("fct_a1")
            assert latest["total_score"] == 45.0
        finally:
            repo.close()

    def test_list_top_scores_takes_latest_only(self, db_path: Path):
        repo = FactorQualityScoreRepository(db_path)
        try:
            # fct_a: 两期评分（30 → 45），fct_b: 一期（40）
            repo.save_score({"factor_id": "fct_a", "total_score": 30.0, "grade": "B",
                             "dimension_scores": [],
                             "evaluated_at": "2026-08-01T00:00:00+00:00"})
            repo.save_score({"factor_id": "fct_a", "total_score": 45.0, "grade": "A",
                             "dimension_scores": [],
                             "evaluated_at": "2026-08-02T00:00:00+00:00"})
            repo.save_score({"factor_id": "fct_b", "total_score": 40.0, "grade": "A",
                             "dimension_scores": [],
                             "evaluated_at": "2026-08-01T00:00:00+00:00"})
            top = repo.list_top_scores(limit=10)
            ids = [s["factor_id"] for s in top]
            assert ids == ["fct_a", "fct_b"]  # 45 > 40，且 fct_a 只出现一次
            a_scores = [s for s in top if s["factor_id"] == "fct_a"]
            assert len(a_scores) == 1
            assert a_scores[0]["total_score"] == 45.0

            top_a = repo.list_top_scores(limit=10, grade="A")
            assert {s["factor_id"] for s in top_a} == {"fct_a", "fct_b"}
        finally:
            repo.close()

    def test_delete_scores(self, db_path: Path):
        repo = FactorQualityScoreRepository(db_path)
        try:
            repo.save_score({"factor_id": "fct_x", "total_score": 10.0, "grade": "C",
                             "dimension_scores": []})
            deleted = repo.delete_scores_for_factor("fct_x")
            assert deleted >= 1
            assert repo.get_latest_score("fct_x") is None
        finally:
            repo.close()


# ─── A.2 状态变迁仓储 ───────────────────────────────────────


class TestStatusRepository:
    """FactorStatusRepository CRUD。"""

    def test_log_and_get_history(self, db_path: Path):
        repo = FactorStatusRepository(db_path)
        try:
            history_id = repo.log_transition(
                "fct_s1", "active", "decaying",
                "连续 3 月 IC < 0",
                {"consecutive_ic_negative_months": 3},
            )
            assert history_id
            history = repo.get_history("fct_s1")
            assert len(history) == 1
            assert history[0]["from_status"] == "active"
            assert history[0]["to_status"] == "decaying"
            assert history[0]["snapshot"]["consecutive_ic_negative_months"] == 3
        finally:
            repo.close()

    def test_update_factor_status(self, db_path: Path):
        """更新 factor_catalog 状态字段（需先有因子）。"""
        factor_repo = FactorRepository(db_path)
        status_repo = FactorStatusRepository(db_path)
        try:
            factor_repo.create_factor({
                "factor_id": "fct_s2",
                "name": "status_test",
                "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
                "market": "futures",
            })
            ok = status_repo.update_factor_status(
                "fct_s2", "decaying",
                consecutive_ic_negative_months=3,
                decay_rate_3m=0.25,
            )
            assert ok is True
            factor = factor_repo.get_factor("fct_s2")
            assert factor["status"] == "decaying"
            assert factor["consecutive_ic_negative_months"] == 3
            assert factor["decay_rate_3m"] == 0.25
            assert factor["status_updated_at"] is not None
        finally:
            factor_repo.close()
            status_repo.close()


# ─── B.3 审计报告仓储 ───────────────────────────────────────


class TestAuditReportRepository:
    """FactorAuditReportRepository CRUD。"""

    def test_save_and_get_latest(self, db_path: Path):
        repo = FactorAuditReportRepository(db_path)
        try:
            report = {
                "report_id": "far_test1",
                "factor_id": "fct_b3",
                "passed": True,
                "overall_score": 85.0,
                "total_checks": 6,
                "passed_checks": 6,
                "results": [{"name": "causal_validity", "status": "passed"}],
                "summary": {"total": 6, "passed": 6, "skipped": 0},
                "recommendations": [],
                "audited_at": "2026-08-06T00:00:00+00:00",
            }
            repo.save_report(report)
            latest = repo.get_latest_report("fct_b3")
            assert latest is not None
            assert latest["passed"] is True
            assert latest["overall_score"] == 85.0
            assert latest["total_checks"] == 6
            assert isinstance(latest["results_json"], list)
            assert latest["results_json"][0]["name"] == "causal_validity"
        finally:
            repo.close()

    def test_get_statistics(self, db_path: Path):
        repo = FactorAuditReportRepository(db_path)
        try:
            for i, passed in enumerate((True, False, True)):
                repo.save_report({
                    "factor_id": f"fct_st{i}",
                    "passed": passed,
                    "overall_score": 80.0 if passed else 40.0,
                    "total_checks": 6,
                    "passed_checks": 6 if passed else 2,
                    "results": [],
                    "summary": {"total": 6},
                    "recommendations": [],
                })
            stats = repo.get_statistics()
            assert stats["total_audits"] == 3
            assert stats["passed_audits"] == 2
            assert stats["distinct_factors"] == 3
            assert 0 < stats["pass_rate"] < 1.0
        finally:
            repo.close()
