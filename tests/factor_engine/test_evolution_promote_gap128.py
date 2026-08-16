"""GAP-128：晋升管线质检落库测试（隔离 DuckDB）。

覆盖: `_write_to_duckdb` 携带 quality_score/audit_report 时落两表 / 缺省不写 /
落库失败非阻塞不阻断晋升。
"""
from __future__ import annotations

import types
from pathlib import Path

from fts.factor_engine.audit import AuditItemResult, FactorAuditReport
from fts.factor_engine.evolution_promote import EliteStore
from fts.factor_engine.factor_db import repository as repo_mod
from fts.factor_engine.factor_db.repository import (
    FactorAuditReportRepository,
    FactorQualityScoreRepository,
    FactorRepository,
)

_QS = {
    "score_id": "qsc_fct_t1",
    "factor_id": "fct_t1",
    "total_score": 80.0,
    "dimension_scores": [{"name": "ic_score", "score": 8.0}],
    "grade": "A",
    "evaluated_at": "2026-08-16T00:00:00",
    "score_version": "v1",
}


def _make_owner(tmp_path: Path, repo: FactorRepository) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        market="futures",
        memory_dir=tmp_path / "memory",
        _decay_observe_slope=3,
        _decay_retire_slope=6,
        _decay_slope_min_points=5,
        _get_repo=lambda: repo,
    )


def _factor() -> dict:
    return {
        "factor_id": "fct_t1",
        "name": "t1",
        "code": "close",
        "params": {},
        "signature": {},
        "economic_logic": {},
        "source": "macro_evolution",
        "parent_id": None,
        "generation": 1,
        "trace_id": "trace1",
        "market": "futures",
        "symbols": [],
        "factor_version": "v2",
    }


def _evaluation() -> dict:
    return {
        "trace_id": "trace1",
        "passed": True,
        "level_1_backtest": {
            "ic": 0.5,
            "icir": 1.5,
            "sharpe": 12.0,
            "max_drawdown": 0.02,
            "turnover_monthly": 5.0,
        },
        "level_2_economic": {},
        "level_3_multiple": {},
        "evaluated_at": "2026-08-16T00:00:00",
    }


def _audit() -> FactorAuditReport:
    return FactorAuditReport(
        factor_id="fct_t1",
        factor_name="t1",
        audited_at="2026-08-16T00:00:00",
        items=[AuditItemResult(name="oos_consistency", status="passed", evidence="ok", score=1.0)],
        passed=True,
        pass_rate=1.0,
        summary={"total": 1, "passed": 1, "failed_items": []},
    )


def _bind_repos(monkeypatch, db: Path) -> None:
    """将 _write_to_duckdb 内局部导入的两个仓储类绑定到隔离库。"""

    def q_factory(market: str = "futures", **kwargs):
        return FactorQualityScoreRepository(db_path=db, market=market, **kwargs)

    def a_factory(market: str = "futures", **kwargs):
        return FactorAuditReportRepository(db_path=db, market=market, **kwargs)

    monkeypatch.setattr(repo_mod, "FactorQualityScoreRepository", q_factory)
    monkeypatch.setattr(repo_mod, "FactorAuditReportRepository", a_factory)


def test_write_to_duckdb_persists_quality_and_audit(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "iso.duckdb"
    _bind_repos(monkeypatch, db)
    repo = FactorRepository(db_path=db, market="futures")
    owner = _make_owner(tmp_path, repo)
    store = EliteStore(owner)

    ok = store._write_to_duckdb(
        _factor(), _evaluation(), quality_score=dict(_QS), audit_report=_audit()
    )
    assert ok is True
    with FactorQualityScoreRepository(db_path=db, market="futures") as q:
        latest = q.get_latest_score("fct_t1")
        assert latest is not None and latest["grade"] == "A"
    a = FactorAuditReportRepository(db_path=db, market="futures")
    try:
        latest = a.get_latest_report("fct_t1")
        assert latest is not None and latest["passed"] is True
    finally:
        a.close()
    repo.close()


def test_write_to_duckdb_without_qa_skips_tables(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "iso.duckdb"
    _bind_repos(monkeypatch, db)
    repo = FactorRepository(db_path=db, market="futures")
    owner = _make_owner(tmp_path, repo)
    store = EliteStore(owner)

    ok = store._write_to_duckdb(_factor(), _evaluation())
    assert ok is True
    with FactorQualityScoreRepository(db_path=db, market="futures") as q:
        assert q.get_latest_score("fct_t1") is None
    a = FactorAuditReportRepository(db_path=db, market="futures")
    try:
        assert a.get_latest_report("fct_t1") is None
    finally:
        a.close()
    repo.close()


def test_write_to_duckdb_persist_failure_nonblocking(monkeypatch, tmp_path: Path) -> None:
    db = tmp_path / "iso.duckdb"
    _bind_repos(monkeypatch, db)
    repo = FactorRepository(db_path=db, market="futures")
    owner = _make_owner(tmp_path, repo)
    store = EliteStore(owner)

    def boom(market: str = "futures", **kwargs):  # noqa: ARG001
        class _Boom:
            def __enter__(self):
                raise RuntimeError("db write failed")

            def __exit__(self, *a):
                return False

        return _Boom()

    monkeypatch.setattr(repo_mod, "FactorQualityScoreRepository", boom)
    # 质检落库失败不应阻断晋升
    ok = store._write_to_duckdb(
        _factor(), _evaluation(), quality_score=dict(_QS), audit_report=_audit()
    )
    assert ok is True
    repo.close()
