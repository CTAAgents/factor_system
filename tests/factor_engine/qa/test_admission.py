"""test_qa_admission — 三级准入分类测试（CTA 手册 6.3）。"""

from __future__ import annotations

import pytest

from fts.factor_engine.qa.admission import (
    admission_summary,
    classify_admission,
    level_label,
    max_weight_for,
)


def test_core_when_score_high_and_ir_ok() -> None:
    """综合得分 ≥4 且 IR 达标 → 核心库。"""
    assert classify_admission(4.5, True) == "CORE"
    assert classify_admission(4.0, True) == "CORE"  # 边界含入


def test_candidate_when_mid_score() -> None:
    """3 ≤ 得分 < 4 且 IR 达标 → 候选库。"""
    assert classify_admission(3.5, True) == "CANDIDATE"
    assert classify_admission(3.0, True) == "CANDIDATE"


def test_rejected_when_score_low() -> None:
    """得分 < 3 → 淘汰。"""
    assert classify_admission(2.9, True) == "REJECTED"


def test_rejected_when_ir_not_ok() -> None:
    """IR 未达分类门槛 → 即使得分高也淘汰。"""
    assert classify_admission(4.5, False) == "REJECTED"


def test_max_weight_mapping() -> None:
    """权重上限：核心 30% / 候选 15% / 淘汰 0。"""
    assert max_weight_for("CORE") == pytest.approx(0.30)
    assert max_weight_for("CANDIDATE") == pytest.approx(0.15)
    assert max_weight_for("REJECTED") == 0.0
    assert max_weight_for("UNKNOWN") == 0.0  # 未知兜底


def test_level_label() -> None:
    """等级中文名映射。"""
    assert level_label("CORE") == "核心库"
    assert level_label("CANDIDATE") == "候选库"
    assert level_label("REJECTED") == "淘汰"


def test_admission_summary() -> None:
    """准入评估汇总结构完整。"""
    s = admission_summary(4.2, True)
    assert s["level"] == "CORE"
    assert s["label"] == "核心库"
    assert s["max_weight"] == pytest.approx(0.30)
    assert s["status"] == "正式服役"


def test_admission_summary_rejected() -> None:
    """淘汰档汇总。"""
    s = admission_summary(2.0, True)
    assert s["level"] == "REJECTED"
    assert s["max_weight"] == 0.0
