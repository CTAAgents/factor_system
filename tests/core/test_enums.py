"""tests/core/test_enums.py — FTS 核心枚举测试。

HARNESS §契约优先: 枚举变更必须 bump 版本号，本测试同步验证。
"""

from __future__ import annotations

from fts.core.enums import (
    EvolutionStage,
    FactorPriority,
    FactorStatus,
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


# ─── FactorStatus ───────────────────────────────────────


class TestFactorStatus:
    """FactorStatus 枚举：4 个成员。"""

    def test_members_count(self):
        assert len(FactorStatus) == 4

    def test_members_values(self):
        assert FactorStatus.PENDING.value == "pending"
        assert FactorStatus.INJECTED.value == "injected"
        assert FactorStatus.DECAYED.value == "decayed"
        assert FactorStatus.REJECTED.value == "rejected"

    def test_members_names(self):
        assert FactorStatus.PENDING.name == "PENDING"
        assert FactorStatus.INJECTED.name == "INJECTED"
        assert FactorStatus.DECAYED.name == "DECAYED"
        assert FactorStatus.REJECTED.name == "REJECTED"

    def test_str(self):
        assert str(FactorStatus.PENDING) == "FactorStatus.PENDING"
        assert str(FactorStatus.INJECTED) == "FactorStatus.INJECTED"
        assert str(FactorStatus.DECAYED) == "FactorStatus.DECAYED"
        assert str(FactorStatus.REJECTED) == "FactorStatus.REJECTED"

    def test_unique_values(self):
        values = [m.value for m in FactorStatus]
        assert len(values) == len(set(values))


# ─── 跨枚举 / 模块级 ───────────────────────────────────


def test_no_duplicate_values_across_enums():
    """跨枚举无重复值（验证设计无冲突）。"""
    stage_values = {m.value for m in EvolutionStage}
    priority_values = {m.value for m in FactorPriority}
    status_values = {m.value for m in FactorStatus}
    all_values = stage_values | priority_values | status_values
    total = len(stage_values) + len(priority_values) + len(status_values)
    assert len(all_values) == total  # 无交集


def test_all_exports():
    """__all__ 正确导出 3 个枚举类。"""
    assert "EvolutionStage" in enums_all
    assert "FactorPriority" in enums_all
    assert "FactorStatus" in enums_all
    assert len(enums_all) == 3
