"""tests/core/test_enums.py — FTS 核心枚举测试。

HARNESS §契约优先: 枚举变更必须 bump 版本号，本测试同步验证。
"""

from __future__ import annotations

from fts.core.enums import (
    EvolutionStage,
    FactorPriority,
    CandidateStatus,
    __all__ as enums_all,
)


# ─── EvolutionStage ─────────────────────────────────────


class TestEvolutionStage:
    """EvolutionStage 枚举：4 个成员，值正确。"""

    def test_members_count(self):
        assert len(EvolutionStage) == 4

    def test_members_values(self):
        assert EvolutionStage.L0_HUMAN.value == "l0_human"
        assert EvolutionStage.L1_META_LOOP.value == "l1_meta_loop"
        assert EvolutionStage.L2_EVOLUTION.value == "l2_evolution"
        assert EvolutionStage.L3_PORTFOLIO.value == "l3_portfolio"

    def test_members_names(self):
        assert EvolutionStage.L0_HUMAN.name == "L0_HUMAN"
        assert EvolutionStage.L1_META_LOOP.name == "L1_META_LOOP"
        assert EvolutionStage.L2_EVOLUTION.name == "L2_EVOLUTION"
        assert EvolutionStage.L3_PORTFOLIO.name == "L3_PORTFOLIO"

    def test_str(self):
        # str() on (str, Enum) mixin returns "ClassName.MEMBER" format
        assert str(EvolutionStage.L0_HUMAN) == "EvolutionStage.L0_HUMAN"
        assert str(EvolutionStage.L1_META_LOOP) == "EvolutionStage.L1_META_LOOP"
        assert str(EvolutionStage.L2_EVOLUTION) == "EvolutionStage.L2_EVOLUTION"
        assert str(EvolutionStage.L3_PORTFOLIO) == "EvolutionStage.L3_PORTFOLIO"

    def test_unique_values(self):
        values = [m.value for m in EvolutionStage]
        assert len(values) == len(set(values))


# ─── FactorPriority ─────────────────────────────────────


class TestFactorPriority:
    """FactorPriority 枚举：3 个成员。"""

    def test_members_count(self):
        assert len(FactorPriority) == 3

    def test_members_values(self):
        assert FactorPriority.HIGH.value == "high"
        assert FactorPriority.MEDIUM.value == "medium"
        assert FactorPriority.LOW.value == "low"

    def test_members_names(self):
        assert FactorPriority.HIGH.name == "HIGH"
        assert FactorPriority.MEDIUM.name == "MEDIUM"
        assert FactorPriority.LOW.name == "LOW"

    def test_str(self):
        assert str(FactorPriority.HIGH) == "FactorPriority.HIGH"
        assert str(FactorPriority.MEDIUM) == "FactorPriority.MEDIUM"
        assert str(FactorPriority.LOW) == "FactorPriority.LOW"

    def test_unique_values(self):
        values = [m.value for m in FactorPriority]
        assert len(values) == len(set(values))


# ─── CandidateStatus ────────────────────────────────────


class TestCandidateStatus:
    """CandidateStatus 枚举：4 个成员（种子池候选状态）。"""

    def test_members_count(self):
        assert len(CandidateStatus) == 4

    def test_members_values(self):
        assert CandidateStatus.PENDING.value == "pending"
        assert CandidateStatus.INJECTED.value == "injected"
        assert CandidateStatus.DECAYED.value == "decayed"
        assert CandidateStatus.REJECTED.value == "rejected"

    def test_members_names(self):
        assert CandidateStatus.PENDING.name == "PENDING"
        assert CandidateStatus.INJECTED.name == "INJECTED"
        assert CandidateStatus.DECAYED.name == "DECAYED"
        assert CandidateStatus.REJECTED.name == "REJECTED"

    def test_str(self):
        assert str(CandidateStatus.PENDING) == "CandidateStatus.PENDING"
        assert str(CandidateStatus.INJECTED) == "CandidateStatus.INJECTED"
        assert str(CandidateStatus.DECAYED) == "CandidateStatus.DECAYED"
        assert str(CandidateStatus.REJECTED) == "CandidateStatus.REJECTED"

    def test_unique_values(self):
        values = [m.value for m in CandidateStatus]
        assert len(values) == len(set(values))


# ─── 跨枚举 / 模块级 ───────────────────────────────────


def test_no_duplicate_values_across_enums():
    """跨枚举无重复值（验证设计无冲突）。"""
    stage_values = {m.value for m in EvolutionStage}
    priority_values = {m.value for m in FactorPriority}
    status_values = {m.value for m in CandidateStatus}
    all_values = stage_values | priority_values | status_values
    total = len(stage_values) + len(priority_values) + len(status_values)
    assert len(all_values) == total  # 无交集


def test_all_exports():
    """__all__ 正确导出 5 个枚举类。"""
    assert "EvolutionStage" in enums_all
    assert "FactorPriority" in enums_all
    assert "CandidateStatus" in enums_all
    assert "DataSource" in enums_all
    assert "FusionStrategy" in enums_all
    assert len(enums_all) == 5
