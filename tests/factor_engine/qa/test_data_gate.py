"""test_data_gate — plans/59 OPT-05（GAP-165）数据质量-评审联动门禁测试。"""

from __future__ import annotations

import pytest

from fts.factor_engine.factor_db import FactorRepository, init_database
from fts.factor_engine.factor_inspector import FactorReviewWorkflow
from fts.factor_engine.qa.data_gate import (
    GATE_DEFER,
    GATE_PROCEED,
    STATUS_CRITICAL,
    STATUS_OK,
    STATUS_WARNING,
    DataQualityGateConfig,
    assess_data_quality,
    data_gate_decision,
)


# ─── assess_data_quality ────────────────────────────────────


def test_ok_when_clean() -> None:
    """数据干净 → ok。"""
    r = assess_data_quality(missing_ratio=0.01, outlier_ratio=0.02, source_disagreement=0.001)
    assert r["status"] == STATUS_OK
    assert r["critical_hits"] == []


def test_warning_on_missing_over_warning_line() -> None:
    """缺失率超警告线 → warning。"""
    r = assess_data_quality(missing_ratio=0.08, outlier_ratio=0.01)
    assert r["status"] == STATUS_WARNING


def test_critical_on_missing_high() -> None:
    """缺失率超严重线 → critical。"""
    r = assess_data_quality(missing_ratio=0.25)
    assert r["status"] == STATUS_CRITICAL
    assert "缺失率" in r["detail"]


def test_critical_on_outlier_high() -> None:
    """异常值超严重线 → critical。"""
    r = assess_data_quality(missing_ratio=0.01, outlier_ratio=0.15)
    assert r["status"] == STATUS_CRITICAL
    assert "异常值" in r["detail"]


def test_critical_on_disagreement() -> None:
    """多源分歧超严重线 → critical。"""
    r = assess_data_quality(missing_ratio=0.01, source_disagreement=0.02)
    assert r["status"] == STATUS_CRITICAL
    assert "多源分歧" in r["detail"]


def test_missing_values_no_false_positive() -> None:
    """数据不可得（None）→ 不误报 critical。"""
    r = assess_data_quality(missing_ratio=None, outlier_ratio=None, source_disagreement=None)
    assert r["status"] == STATUS_OK


def test_custom_config() -> None:
    """自定义阈值生效。"""
    cfg = DataQualityGateConfig(critical_missing_ratio=0.5)
    r = assess_data_quality(missing_ratio=0.3, config=cfg)
    assert r["status"] == STATUS_WARNING  # 0.3 > warning 0.05 但 < critical 0.5


# ─── data_gate_decision ─────────────────────────────────────


def test_gate_proceed_on_ok() -> None:
    """数据正常 → proceed。"""
    q = assess_data_quality(missing_ratio=0.01)
    assert data_gate_decision(q) == GATE_PROCEED


def test_gate_defer_on_critical() -> None:
    """数据严重异常 → defer（不写 approved）。"""
    q = assess_data_quality(missing_ratio=0.3)
    assert data_gate_decision(q) == GATE_DEFER


def test_gate_proceed_on_warning() -> None:
    """warning → proceed（仅标记不阻断）。"""
    q = assess_data_quality(missing_ratio=0.08)
    assert data_gate_decision(q) == GATE_PROCEED


def test_gate_disabled_proceeds() -> None:
    """enabled=False → 恒 proceed。"""
    cfg = DataQualityGateConfig(enabled=False)
    q = assess_data_quality(missing_ratio=0.5)
    assert data_gate_decision(q, cfg) == GATE_PROCEED


# ─── review_inplace 集成 ────────────────────────────────────


@pytest.fixture
def wf(tmp_path) -> FactorReviewWorkflow:
    db_path = tmp_path / "test_data_gate.db"
    init_database(str(db_path))
    repo = FactorRepository(str(db_path))
    return FactorReviewWorkflow(repo=repo)


def _create_factor_with_qa(repo: FactorRepository, fid: str) -> None:
    meta = {
        "qa_review": {
            "audit_passed": True,
            "quality_grade": "A",
            "high_ic_grade": "A",
            "multiple_passed": True,
            "walk_forward_windows": 3,
            "q1_q10_passed": True,
        }
    }
    repo.create_factor(
        {
            "factor_id": fid,
            "name": f"Factor {fid}",
            "code": "close",
            "market": "futures",
            "is_elite": True,
            "status": "active",
            "ic": 0.050,
            "sharpe": 1.0,
            "metadata": meta,
        }
    )


def test_review_inplace_approved_with_clean_data(wf: FactorReviewWorkflow) -> None:
    """数据正常 → 正常 approved。"""
    _create_factor_with_qa(wf._repo, "f_dq_ok")
    r = wf.review_inplace("f_dq_ok", data_quality_provider=lambda fid: {"missing_ratio": 0.01})
    assert r["decision"] == "approved"
    assert r.get("data_degraded") is None


def test_review_inplace_deferred_on_critical_data(wf: FactorReviewWorkflow) -> None:
    """数据严重异常 → 不写 approved，转人审（data_degraded）。"""
    _create_factor_with_qa(wf._repo, "f_dq_bad")
    r = wf.review_inplace(
        "f_dq_bad", data_quality_provider=lambda fid: {"missing_ratio": 0.35}
    )
    assert r["decision"] is None
    assert r["data_degraded"] is True
    assert "数据质量异常延迟评审" in r["reason"]


def test_review_inplace_no_provider_proceeds(wf: FactorReviewWorkflow) -> None:
    """无 provider（数据质量不可得）→ 不阻断（无法判定不误伤）。"""
    _create_factor_with_qa(wf._repo, "f_dq_noprov")
    r = wf.review_inplace("f_dq_noprov")
    assert r["decision"] == "approved"


def test_review_inplace_reject_ignores_data_gate(wf: FactorReviewWorkflow) -> None:
    """reject 路径不受数据门禁影响（宁缺毋滥）。"""
    meta = {
        "qa_review": {
            "audit_passed": True,
            "quality_grade": "A",
            "high_ic_grade": "A",
            "multiple_passed": True,
            "walk_forward_windows": 3,
            "q1_q10_passed": True,
        }
    }
    wf._repo.create_factor(
        {
            "factor_id": "f_dq_low",
            "name": "F dq low",
            "code": "close",
            "market": "futures",
            "is_elite": True,
            "status": "active",
            "ic": 0.010,  # 低于 min_ic → rejected
            "sharpe": 1.0,
            "metadata": meta,
        }
    )
    r = wf.review_inplace("f_dq_low", data_quality_provider=lambda fid: {"missing_ratio": 0.5})
    assert r["decision"] == "rejected"
    assert r.get("data_degraded") is None
