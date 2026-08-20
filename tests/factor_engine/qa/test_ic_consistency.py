"""test_ic_consistency — plans/59 OPT-04（GAP-164）IC 口径一致性校验测试。"""

from __future__ import annotations

import pytest

from fts.factor_engine.factor_db import FactorRepository, init_database
from fts.factor_engine.factor_inspector import FactorReviewWorkflow
from fts.factor_engine.qa.ic_consistency import DEFAULT_TOLERANCE, check_ic_consistency


# ─── check_ic_consistency 纯函数 ────────────────────────────


def test_consistent_within_tolerance() -> None:
    """偏差 ≤ 容差 → 一致。"""
    r = check_ic_consistency({"catalog": 0.032, "evaluation": 0.031, "audit": 0.033})
    assert r["consistent"] is True
    assert r["n_valid"] == 3
    assert r["inconsistent_sources"] == []


def test_inconsistent_over_tolerance() -> None:
    """偏差 > 容差 → 不一致，列出漂移来源。"""
    r = check_ic_consistency({"catalog": 0.032, "evaluation": 0.031, "audit": 0.020})
    assert r["consistent"] is False
    assert "audit" in r["inconsistent_sources"]
    assert "evaluation" not in r["inconsistent_sources"]
    assert "IC 口径漂移" in r["detail"]
    assert r["authoritative_ic"] == pytest.approx(0.032)


def test_authoritative_customizable() -> None:
    """可指定其他权威口径。"""
    r = check_ic_consistency({"catalog": 0.032, "evaluation": 0.010}, authoritative="evaluation")
    assert r["consistent"] is False
    assert "catalog" in r["inconsistent_sources"]


def test_single_source_no_false_alarm() -> None:
    """单有效来源 → 不误报。"""
    r = check_ic_consistency({"catalog": 0.032, "evaluation": None})
    assert r["consistent"] is True
    assert r["n_valid"] == 1


def test_authoritative_missing_no_false_alarm() -> None:
    """权威口径缺失 → 不误报。"""
    r = check_ic_consistency({"catalog": None, "evaluation": 0.031})
    assert r["consistent"] is True
    assert r["authoritative_ic"] is None


def test_non_numeric_and_nan_removed() -> None:
    """非数值 / NaN 剔除。"""
    r = check_ic_consistency({"catalog": 0.032, "evaluation": float("nan"), "audit": "N/A"})
    assert r["n_valid"] == 1
    assert r["consistent"] is True


def test_custom_tolerance() -> None:
    """自定义容差生效。"""
    r = check_ic_consistency({"catalog": 0.032, "evaluation": 0.040}, tolerance=0.01)
    assert r["consistent"] is True
    r2 = check_ic_consistency({"catalog": 0.032, "evaluation": 0.040}, tolerance=0.005)
    assert r2["consistent"] is False


def test_default_tolerance_value() -> None:
    """默认容差 0.005。"""
    assert DEFAULT_TOLERANCE == 0.005


def test_empty_input_no_false_alarm() -> None:
    """空输入 → 不误报。"""
    r = check_ic_consistency({})
    assert r["consistent"] is True
    assert r["n_valid"] == 0


# ─── review_inplace 集成 ────────────────────────────────────


@pytest.fixture
def wf(tmp_path) -> FactorReviewWorkflow:
    db_path = tmp_path / "test_ic_consistency.db"
    init_database(str(db_path))
    repo = FactorRepository(str(db_path))
    return FactorReviewWorkflow(repo=repo)


def _create_factor_with_qa(repo: FactorRepository, fid: str, ic: float) -> None:
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
            "ic": ic,
            "sharpe": 1.0,
            "metadata": meta,
        }
    )


def test_review_inplace_consistent_approved(wf: FactorReviewWorkflow) -> None:
    """主表 ic 与最新评估 IC 一致 → 机审通过。"""
    _create_factor_with_qa(wf._repo, "f_ic_ok", 0.050)
    wf._repo.add_evaluation(
        "f_ic_ok",
        {"trace_id": "t", "ic": 0.050, "sharpe": 1.0, "icir": 0.5, "evaluated_at": "2026-08-20"},
    )
    r = wf.review_inplace("f_ic_ok")
    assert r["decision"] == "approved"


def test_review_inplace_ic_drift_to_human(wf: FactorReviewWorkflow) -> None:
    """主表 ic 与评估历史 IC 漂移 > 容差 → 转人审（口径脱节，不静默通过）。

    add_evaluation 会回写 catalog.ic，故用 update_factor 手动构造主表与
    评估历史脱节（模拟旁路评估/回填未同步主表的场景）。
    """
    _create_factor_with_qa(wf._repo, "f_ic_drift", 0.050)
    wf._repo.add_evaluation(
        "f_ic_drift",
        {"trace_id": "t", "ic": 0.050, "sharpe": 1.0, "icir": 0.5, "evaluated_at": "2026-08-20"},
    )
    wf._repo.update_factor("f_ic_drift", {"ic": 0.032})  # 主表与评估历史脱节
    r = wf.review_inplace("f_ic_drift")
    assert r["decision"] is None
    assert "IC 口径不一致告警" in r["reason"]


def test_review_inplace_no_eval_no_drift(wf: FactorReviewWorkflow) -> None:
    """无评估历史 → 无法判定，正常评审（不误报）。"""
    _create_factor_with_qa(wf._repo, "f_ic_noeval", 0.050)
    r = wf.review_inplace("f_ic_noeval")
    assert r["decision"] == "approved"
