"""
tests/factor_engine/test_qa_subchain.py — 评审质检张量化测试（plans/49 §B / §五）。

覆盖:
    - judge_q10_subchain：两级判定（consistent / subchain_specific / conflicted）
      + 反向子链 avoid_chain 标记（单链特异不误杀）
    - AutoReviewPolicy 单链特异放行（B2）：全链 IC 低但 effective 子链 → APPROVED；
      Sharpe 不放行；QA 门禁不变
    - admission 子链化（B3）：单链特异受限权重 10%；兼容原 30%/15%
    - quarterly F6 两级重构（B4）：跨链不一致标记 / 子链特异不标记 / 有效链漂移标记

纯计算无 DB/IO，不触真实因子库。
"""

from __future__ import annotations

import pytest

from fts.factor_engine.factor_inspector import AutoReviewPolicy, ReviewDecision
from fts.factor_engine.qa.admission import (
    SUBCHAIN_SPECIFIC_MAX_WEIGHT,
    admission_summary,
    max_weight_for,
)
from fts.factor_engine.qa.pre_entry import judge_q10_subchain
from fts.factor_engine.qa.quarterly_check import quarterly_recheck

CHAINS: dict[str, list[str]] = {
    "能源": ["SC0", "FU0", "BU0"],
    "聚酯": ["PF0", "TA0", "EG0"],
    "油化工": ["L0", "PP0", "PG0"],
    "煤化工": ["MA0", "UR0", "SA0"],
}


def _ic(energy=0.02, polyester=0.01, oil=0.0, coal=0.01) -> dict:
    """构造四子链逐品种 IC（默认全部近零 → 无显著子链）。"""
    return {
        "SC0": energy, "FU0": energy, "BU0": energy,
        "PF0": polyester, "TA0": polyester, "EG0": polyester,
        "L0": oil, "PP0": oil, "PG0": oil,
        "MA0": coal, "UR0": coal, "SA0": coal,
    }


def _qa_ok() -> dict:
    return {
        "audit_passed": True,
        "quality_grade": "B",
        "high_ic_grade": "B",
        "multiple_passed": True,
        "walk_forward_windows": 3,
        "q1_q10_passed": True,
    }


class TestJudgeQ10Subchain:
    def test_consistent_when_no_significant_chain(self):
        v = judge_q10_subchain(_ic(), CHAINS)
        assert v["verdict"] == "consistent"
        assert v["passed"] is True

    def test_subchain_specific_accepted(self):
        # 能源三品种 0.2 一致（t 显著），其余近零 → 子链特异可接受（不误杀）
        v = judge_q10_subchain(_ic(energy=0.2), CHAINS)
        assert v["verdict"] == "subchain_specific"
        assert v["passed"] is True
        assert v["effective_chains"] == ["能源"]
        assert v["avoid_chains"] == []

    def test_reverse_chain_flagged_avoid_not_fail(self):
        # 能源 +0.2 显著、聚酯 -0.15 显著反向 → 不判失败，聚酯标记 avoid_chain
        v = judge_q10_subchain(
            {
                "SC0": 0.2, "FU0": 0.2, "BU0": 0.2,
                "PF0": -0.15, "TA0": -0.15, "EG0": -0.15,
                "L0": 0.0, "PP0": 0.0, "PG0": 0.0,
                "MA0": 0.0, "UR0": 0.0, "SA0": 0.0,
            },
            CHAINS,
        )
        assert v["verdict"] == "subchain_specific"
        assert v["passed"] is True
        assert v["effective_chains"] == ["能源"]
        assert v["avoid_chains"] == ["聚酯"]

    def test_conflicted_without_effective(self):
        # 无 effective 子链但存在 |mean_ic|≥0.10 的分散链（方向混乱，t 不显著）→ 判失败
        v = judge_q10_subchain(
            {
                "SC0": 0.62, "FU0": -0.38, "BU0": 0.12,  # mean=0.12 但 std 大 → t 不显著
                "PF0": 0.01, "TA0": 0.0, "EG0": 0.01,
                "L0": 0.0, "PP0": 0.0, "PG0": 0.0,
                "MA0": 0.0, "UR0": 0.0, "SA0": 0.0,
            },
            CHAINS,
        )
        assert v["verdict"] == "conflicted"
        assert v["passed"] is False
        assert v["avoid_chains"] == ["能源"]

    def test_empty_symbol_ic_consistent(self):
        v = judge_q10_subchain({}, CHAINS)
        assert v["verdict"] == "consistent"
        assert v["passed"] is True


class TestAutoReviewSubchainPass:
    def test_weak_global_ic_released_by_effective_chain(self):
        # 全链 ic=0.01 < min_ic=0.02，但能源子链 effective → 放行（QA 门禁全过）
        policy = AutoReviewPolicy()
        subchain = {"能源": {"effective": True}, "聚酯": {"effective": False}}
        decision, reason = policy.classify(0.01, 1.5, _qa_ok(), subchain_profile=subchain)
        assert decision == ReviewDecision.APPROVED
        assert "单链特异放行" in reason

    def test_no_effective_chain_still_rejected(self):
        policy = AutoReviewPolicy()
        subchain = {"能源": {"effective": False}, "聚酯": {"effective": False}}
        decision, _ = policy.classify(0.01, 1.5, _qa_ok(), subchain_profile=subchain)
        assert decision == ReviewDecision.REJECTED

    def test_sharpe_not_released(self):
        # Sharpe 低不放行（即使子链 effective）
        policy = AutoReviewPolicy()
        subchain = {"能源": {"effective": True}}
        decision, _ = policy.classify(0.2, 0.3, _qa_ok(), subchain_profile=subchain)
        assert decision == ReviewDecision.REJECTED

    def test_qa_gate_still_enforced(self):
        # 子链放行不绕过 QA 门禁：audit_passed=False → REJECTED
        policy = AutoReviewPolicy()
        subchain = {"能源": {"effective": True}}
        qa_bad = dict(_qa_ok(), audit_passed=False)
        decision, _ = policy.classify(0.01, 1.5, qa_bad, subchain_profile=subchain)
        assert decision == ReviewDecision.REJECTED

    def test_no_profile_backcompat(self):
        # 不传 subchain_profile → 原逻辑（弱 IC 拒绝）
        policy = AutoReviewPolicy()
        decision, _ = policy.classify(0.01, 1.5, _qa_ok())
        assert decision == ReviewDecision.REJECTED


class TestAdmissionSubchain:
    def test_specific_max_weight_capped(self):
        assert max_weight_for("CORE", subchain_specific=True) == SUBCHAIN_SPECIFIC_MAX_WEIGHT
        assert max_weight_for("CANDIDATE", subchain_specific=True) == SUBCHAIN_SPECIFIC_MAX_WEIGHT
        assert SUBCHAIN_SPECIFIC_MAX_WEIGHT == pytest.approx(0.10)

    def test_non_specific_backcompat(self):
        assert max_weight_for("CORE") == pytest.approx(0.30)
        assert max_weight_for("CANDIDATE") == pytest.approx(0.15)
        assert max_weight_for("REJECTED") == 0.0

    def test_summary_passthrough(self):
        s = admission_summary(4.2, True, subchain_specific=True)
        assert s["level"] == "CORE"
        assert s["max_weight"] == pytest.approx(0.10)
        s2 = admission_summary(4.2, True)
        assert s2["max_weight"] == pytest.approx(0.30)


class TestQuarterlyF6Subchain:
    def test_sector_inconsistent_flagged(self):
        r = quarterly_recheck(ic_ir_ratio=1.0, sector_consistent=False)
        assert r["indicators"]["F6"]["flagged"] is True

    def test_subchain_specific_not_flagged(self):
        # 单链特异不再判"方向不一致"（B4 内层可接受）
        r = quarterly_recheck(
            ic_ir_ratio=1.0,
            sector_consistent=True,
            subchain_verdict={"verdict": "subchain_specific", "effective_chains": ["能源"]},
        )
        assert r["indicators"]["F6"]["flagged"] is False

    def test_conflicted_flagged(self):
        r = quarterly_recheck(
            ic_ir_ratio=1.0,
            subchain_verdict={"verdict": "conflicted", "effective_chains": []},
        )
        assert r["indicators"]["F6"]["flagged"] is True

    def test_effective_chain_drift_flagged(self):
        # 当前 {能源} vs 基准 {能源,聚酯} → 漂移标记 scope 复核
        r = quarterly_recheck(
            ic_ir_ratio=1.0,
            sector_consistent=True,
            subchain_verdict={"verdict": "subchain_specific", "effective_chains": ["能源"]},
            baseline_effective_chains=["能源", "聚酯"],
        )
        assert r["indicators"]["F6"]["flagged"] is True

    def test_no_drift_not_flagged(self):
        r = quarterly_recheck(
            ic_ir_ratio=1.0,
            sector_consistent=True,
            subchain_verdict={"verdict": "subchain_specific", "effective_chains": ["能源"]},
            baseline_effective_chains=["能源"],
        )
        assert r["indicators"]["F6"]["flagged"] is False

    def test_missing_both_skipped(self):
        r = quarterly_recheck(ic_ir_ratio=1.0)
        assert r["indicators"]["F6"]["flagged"] is False  # 数据缺失不误判
