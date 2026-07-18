"""tests/core/test_contracts.py — FTS 核心契约入口测试。

验证 fts.core.contracts 正确 re-export fts.factor_engine.contracts 的所有符号。

HARNESS §契约优先: 契约变更必须同步更新测试。
"""

from __future__ import annotations

import importlib

import pytest

from fts.core import contracts as core_contracts
from fts.factor_engine import contracts as fe_contracts
from fts.core.contracts import __all__ as core_all


# ─── __all__ 完整性 ─────────────────────────────────────


def test_all_is_defined_and_non_empty():
    """__all__ 已定义且非空。"""
    assert core_all is not None
    assert len(core_all) > 0


def test_all_symbols_importable():
    """__all__ 中每个符号均可从 fts.core.contracts 导入。"""
    for name in core_all:
        obj = getattr(core_contracts, name, None)
        assert obj is not None, f"符号 {name} 在 core_contracts 中不存在"


def test_all_symbols_match_factor_engine():
    """__all__ 中每个符号的值与 fts.factor_engine.contracts 中的一致。"""
    for name in core_all:
        core_val = getattr(core_contracts, name)
        fe_val = getattr(fe_contracts, name, None)
        assert fe_val is not None, f"符号 {name} 在 factor_engine.contracts 中不存在"
        assert core_val is fe_val, (
            f"符号 {name} 引用不一致: core={id(core_val)}, fe={id(fe_val)}"
        )


def test_core_contracts_exports_subset_of_fe():
    """fts.core.contracts.__all__ 是 fts.factor_engine.contracts 的子集。"""
    # factor_engine.contracts 的公开成员
    fe_all = getattr(fe_contracts, "__all__", None)
    if fe_all is not None:
        core_set = set(core_all)
        fe_set = set(fe_all)
        extra_in_core = core_set - fe_set
        assert extra_in_core == set(), (
            f"core 有多余符号不在 factor_engine 中: {extra_in_core}"
        )


# ─── 具体符号验证（≥10 个目标） ────────────────────────


@pytest.mark.parametrize(
    "name,expected_type",
    [
        # 版本
        ("EVOLUTION_VERSION", str),
        # 核心契约
        ("FactorProgram", type),
        ("FactorSignature", type),
        ("EconomicLogic", type),
        ("BacktestMetrics", type),
        ("EconomicScore", type),
        ("MultipleTestResult", type),
        ("FactorEvaluation", type),
        ("ExperienceTrace", type),
        ("EvolutionState", type),
        # Verifier
        ("VerifierConfig", type),
        ("VerifierResult", type),
        # 预算
        ("BudgetConfig", type),
        # 默认配置
        ("DEFAULT_VERIFIER_CONFIG", dict),
        ("DEFAULT_BUDGET_CONFIG", dict),
        # L1
        ("L1BootstrappingSource", object),
        ("MetaLoopStatus", object),
        ("SeedCandidate", type),
        ("L1MetaLoopState", type),
        ("FactorPoolEntry", type),
        ("FactorPool", type),
        ("L1VerifierConfig", type),
        ("L1VerifierResult", type),
        ("DEFAULT_L1_VERIFIER_CONFIG", dict),
        ("DEFAULT_L1_BUDGET_CONFIG", dict),
        # L3
        ("FactorCorrelation", type),
        ("PortfolioSignal", type),
        ("PortfolioCombo", type),
        ("AgentOptimizationProposal", type),
        ("L3VerifierConfig", type),
        ("L3MetaLoopState", type),
        ("DEFAULT_L3_VERIFIER_CONFIG", dict),
        ("DEFAULT_L3_BUDGET", int),
    ],
)
def test_specific_import(name: str, expected_type):
    """每个具体符号类型正确。"""
    obj = getattr(core_contracts, name)
    assert isinstance(obj, expected_type), (
        f"{name} 期望类型 {expected_type}，实际 {type(obj)}"
    )


# ─── 动态导入 ───────────────────────────────────────────


def test_import_via_fts_core():
    """可以通过 from fts.core import contracts 导入。"""
    mod = importlib.import_module("fts.core.contracts")
    assert mod is core_contracts


def test_import_via_fts():
    """可以通过 from fts import core 后访问 core.contracts。"""
    import fts

    assert hasattr(fts.core, "contracts")
