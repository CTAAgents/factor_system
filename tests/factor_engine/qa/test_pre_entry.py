"""test_qa_pre_entry — 入库前质检 Q1-Q10 测试（CTA 手册 6.2）。"""

from __future__ import annotations

from fts.factor_engine.qa.pre_entry import QA_ITEMS, QaItem, run_pre_entry_qa


def _items(passed_q: set[str], detail: str = "ok") -> list[QaItem]:
    return [
        QaItem(
            qid=q["qid"],
            name=q["name"],
            passed=(q["qid"] in passed_q),
            detail=detail if q["qid"] in passed_q else f"{q['qid']} 未通过",
        )
        for q in QA_ITEMS
    ]


def test_all_pass_allows_admission() -> None:
    """Q1-Q10 全过 → 可进入准入评估。"""
    r = run_pre_entry_qa(_items(set(f"Q{i}" for i in range(1, 11))))
    assert r["passed"] is True
    assert r["conclusion"] == "可进入准入评估"
    assert r["one_vote_failed"] == []
    assert r["passed_count"] == 10


def test_q1_fail_is_veto() -> None:
    """Q1（未来函数检测）失败 → 一票否决，禁止入库。"""
    r = run_pre_entry_qa(_items(set(f"Q{i}" for i in range(2, 11))))
    assert r["passed"] is False
    assert r["one_vote_failed"] == ["Q1"]
    assert r["conclusion"] == "禁止入库"


def test_q3_fail_is_veto() -> None:
    """Q3（参数遍历网格）失败 → 一票否决。"""
    r = run_pre_entry_qa(_items(set(f"Q{i}" for i in range(1, 11)) - {"Q3"}))
    assert r["passed"] is False
    assert r["one_vote_failed"] == ["Q3"]


def test_scoring_ratio_below_60_fails() -> None:
    """评分项通过率 < 60%（7 项中 5 项失败）→ 待综合评定。"""
    passed = {"Q1", "Q2", "Q3", "Q4", "Q5"}
    r = run_pre_entry_qa(_items(passed))
    assert r["passed"] is False
    assert r["conclusion"] == "待综合评定"
    assert len(r["scoring_failed"]) >= 4


def test_scoring_ratio_above_60_passes() -> None:
    """评分项通过率 ≥ 60%（仅 2 项评分失败）→ 通过。"""
    passed = set(f"Q{i}" for i in range(1, 11)) - {"Q9", "Q10"}
    r = run_pre_entry_qa(_items(passed))
    assert r["passed"] is True


def test_empty_input_safe() -> None:
    """空输入 → 全部 FAIL，不崩溃。"""
    r = run_pre_entry_qa([])
    assert r["passed"] is False
    assert r["total"] == 10
    assert r["passed_count"] == 0


def test_report_contains_veto_info() -> None:
    """报告文本包含一票否决信息。"""
    r = run_pre_entry_qa(_items(set(f"Q{i}" for i in range(2, 11))))
    assert "禁止入库" in r["report"]
    assert "Q1" in r["report"]
