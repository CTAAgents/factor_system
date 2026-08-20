"""test_review_sla — plans/59 OPT-06（GAP-166）人审 SLA 自动降级测试。"""

from __future__ import annotations

from datetime import date, timedelta

from fts.factor_engine.factor_db import FactorRepository, init_database
from fts.factor_engine.factor_inspector import FactorReviewWorkflow
from fts.factor_engine.qa.review_sla import (
    META_KEY,
    PHASE_ACTIVE,
    PHASE_ESCALATED,
    PHASE_WARNED,
    ReviewSlaConfig,
    sla_marker,
    sla_phase,
)

_TODAY = date(2026, 8, 20)


# ─── sla_phase ──────────────────────────────────────────────


def test_active_within_sla() -> None:
    """未超时 → active。"""
    cfg = ReviewSlaConfig(sla_days=5, escalation_days=10)
    assert sla_phase(_TODAY - timedelta(days=2), _TODAY, cfg) == PHASE_ACTIVE


def test_warned_over_sla() -> None:
    """超 sla_days 未超 escalation → warned。"""
    cfg = ReviewSlaConfig(sla_days=5, escalation_days=10)
    assert sla_phase(_TODAY - timedelta(days=6), _TODAY, cfg) == PHASE_WARNED
    assert sla_phase(_TODAY - timedelta(days=9), _TODAY, cfg) == PHASE_WARNED


def test_escalated_over_2n() -> None:
    """超 escalation_days → escalated。"""
    cfg = ReviewSlaConfig(sla_days=5, escalation_days=10)
    assert sla_phase(_TODAY - timedelta(days=10), _TODAY, cfg) == PHASE_ESCALATED
    assert sla_phase(_TODAY - timedelta(days=30), _TODAY, cfg) == PHASE_ESCALATED


def test_missing_time_no_false_positive() -> None:
    """时间缺失 → active（不误伤）。"""
    assert sla_phase(None, _TODAY, ReviewSlaConfig()) == PHASE_ACTIVE
    assert sla_phase("", _TODAY, ReviewSlaConfig()) == PHASE_ACTIVE


def test_iso_string_parsing() -> None:
    """ISO 字符串解析。"""
    cfg = ReviewSlaConfig(sla_days=5, escalation_days=10)
    assert sla_phase("2026-08-10T09:00:00", _TODAY, cfg) == PHASE_ESCALATED  # 10 天前
    assert sla_phase("2026-08-14", _TODAY, cfg) == PHASE_WARNED  # 6 天前
    assert sla_phase("2026-08-18", _TODAY, cfg) == PHASE_ACTIVE  # 2 天前


# ─── sla_marker ─────────────────────────────────────────────


def test_marker_warned_scale() -> None:
    """warned 标记带 0.5 权重缩放。"""
    m = sla_marker(PHASE_WARNED, ReviewSlaConfig(weight_scale=0.5))
    assert m["status"] == PHASE_WARNED
    assert m["weight_scale"] == 0.5


def test_marker_escalated_zero_scale() -> None:
    """escalated 标记权重缩放归零。"""
    m = sla_marker(PHASE_ESCALATED)
    assert m["status"] == PHASE_ESCALATED
    assert m["weight_scale"] == 0.0


# ─── enforce_review_sla 集成 ────────────────────────────────


def _make_wf(tmp_path) -> FactorReviewWorkflow:
    db_path = tmp_path / "test_review_sla.db"
    init_database(str(db_path))
    repo = FactorRepository(str(db_path))
    return FactorReviewWorkflow(repo=repo)


def _create_pending(wf: FactorReviewWorkflow, fid: str, created_days_ago: int) -> None:
    """创建因子并回填 created_at（create_factor 固定默认时间，需 SQL 直更）。"""
    wf._repo.create_factor(
        {
            "factor_id": fid,
            "name": f"Factor {fid}",
            "code": "close",
            "market": "futures",
            "is_elite": True,
            "status": "active",
            "ic": 0.050,
            "sharpe": 1.0,
        }
    )
    created = (_TODAY - timedelta(days=created_days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = wf._conn()
    try:
        conn.execute(
            "UPDATE factor_catalog SET created_at = ? WHERE factor_id = ?",
            [created, fid],
        )
    finally:
        conn.close()


def test_enforce_active_no_op(tmp_path) -> None:
    """未超时因子不受影响。"""
    wf = _make_wf(tmp_path)
    _create_pending(wf, "f_sla_new", 2)
    stats = wf.enforce_review_sla(market="futures", commit=True, today=_TODAY)
    assert stats["active"] == 1
    assert stats["warned"] == 0
    assert stats["escalated"] == 0
    row = wf._repo.get_factor("f_sla_new")
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    assert META_KEY not in meta


def test_enforce_warned_downgrades(tmp_path) -> None:
    """超 sla_days → warned：metadata.review_sla 降权 50%。"""
    wf = _make_wf(tmp_path)
    _create_pending(wf, "f_sla_warn", 7)
    stats = wf.enforce_review_sla(market="futures", commit=True, today=_TODAY)
    assert stats["warned"] == 1
    row = wf._repo.get_factor("f_sla_warn")
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    assert meta[META_KEY]["status"] == PHASE_WARNED
    assert meta[META_KEY]["weight_scale"] == 0.5


def test_enforce_escalated_to_cooldown(tmp_path) -> None:
    """超 escalation_days → escalated：status=degraded + review 留痕 rejected。"""
    wf = _make_wf(tmp_path)
    _create_pending(wf, "f_sla_esc", 15)
    stats = wf.enforce_review_sla(market="futures", commit=True, today=_TODAY)
    assert stats["escalated"] == 1
    row = wf._repo.get_factor("f_sla_esc")
    assert row["status"] == "degraded"  # 退冷却池
    meta = row["metadata"] if isinstance(row["metadata"], dict) else {}
    assert meta[META_KEY]["status"] == PHASE_ESCALATED
    review = wf.get_status("f_sla_esc")
    assert review is not None
    assert review["decision"] == "rejected"
    assert "冷却池" in review["comment"]


def test_enforce_no_duplicate_disposition(tmp_path) -> None:
    """已处置因子跳过（防重复）。"""
    wf = _make_wf(tmp_path)
    _create_pending(wf, "f_sla_done", 7)
    wf.enforce_review_sla(market="futures", commit=True, today=_TODAY)
    stats = wf.enforce_review_sla(market="futures", commit=True, today=_TODAY)
    assert stats["warned"] == 0
    assert stats["skipped"] == 1


def test_enforce_dry_run_no_commit(tmp_path) -> None:
    """dry-run（commit=False）不落库。"""
    wf = _make_wf(tmp_path)
    _create_pending(wf, "f_sla_dry", 15)
    stats = wf.enforce_review_sla(market="futures", commit=False, today=_TODAY)
    assert stats["escalated"] == 1  # 判定发生
    row = wf._repo.get_factor("f_sla_dry")
    assert row["status"] == "active"  # 未落库
