"""tests.factor_engine.test_structure_constraints — 期货结构约束单测（v2.105.0+32，任务 B）。

覆盖：R1 字段可得性 / R2 子链有效性 / R3 信号去冗余 / R4 家族筛选 / 汇总评估。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.structure_constraints import (
    StructureConstraintConfig,
    check_structure_fields,
    check_structure_redundancy,
    check_subchain_effectiveness,
    evaluate_structure_constraints,
    get_seeds_by_family,
    list_families,
)


class TestR1Field:
    def test_l2_missing_blocked(self) -> None:
        res = check_structure_fields(["close", "fut_inventory"])
        assert res["blocked"] is True
        assert "fut_inventory" in res["l2_missing"]
        assert res["ok"] is False

    def test_authoritative_ok(self) -> None:
        res = check_structure_fields(["close", "hold", "settle"])
        assert res["blocked"] is False
        assert res["ok"] is True

    def test_l2_blocked_can_be_disabled(self) -> None:
        cfg = StructureConstraintConfig(hard_block_l2_fields=False)
        res = check_structure_fields(["close", "fut_spot_price"], cfg)
        assert res["blocked"] is False


class TestR2Subchain:
    def test_no_subchain_input_neutral(self) -> None:
        res = check_subchain_effectiveness("f1", {}, {})
        assert res["detail"] == "profile_unavailable"
        assert res["subchain_invalid"] is False  # 数据不足不误判

    def test_effective_chain_passes(self) -> None:
        # 构造 2 子链 × 3 品种 symbol_ic：链 A 全部正 IC（有效），链 B 无效
        symbol_ic = {
            "RB0": 0.06, "HC0": 0.05, "I0": 0.07,   # 黑色链（有效）
            "TA0": 0.01, "EG0": -0.01, "MA0": 0.00,  # 能化链（无效）
        }
        chain_symbols = {
            "黑色": ["RB0", "HC0", "I0"],
            "能化": ["TA0", "EG0", "MA0"],
        }
        res = check_subchain_effectiveness("f1", symbol_ic, chain_symbols)
        assert res["subchain_invalid"] is False
        assert "黑色" in res["effective_chains"]

    def test_no_effective_chain_marks_invalid(self) -> None:
        symbol_ic = {"TA0": 0.01, "EG0": -0.01, "MA0": 0.00}
        chain_symbols = {"能化": ["TA0", "EG0", "MA0"]}
        res = check_subchain_effectiveness("f1", symbol_ic, chain_symbols)
        # 能化链 IC 无显著 → 无 effective 链 → 标记 invalid（软约束）
        assert res["subchain_invalid"] is True
        assert res["detail"] == "no_effective_chain"


class TestR3Redundancy:
    def test_high_corr_conflict(self) -> None:
        idx = pd.date_range("2026-01-01", periods=60, freq="D")
        base = np.sin(np.arange(60) / 5.0)
        signal = pd.Series(base + 0.001, index=idx)  # 与 base 几乎相同
        res = check_structure_redundancy(
            signal,
            {"fut_roll_yield_carry": pd.Series(base, index=idx)},
            threshold=0.95,
        )
        assert res["ok"] is False
        assert res["conflict_with"] == "fut_roll_yield_carry"

    def test_low_corr_ok(self) -> None:
        idx = pd.date_range("2026-01-01", periods=60, freq="D")
        base = np.sin(np.arange(60) / 5.0)
        signal = pd.Series(np.cos(np.arange(60) / 7.0), index=idx)
        res = check_structure_redundancy(
            signal,
            {"fut_roll_yield_carry": pd.Series(base, index=idx)},
            threshold=0.95,
        )
        assert res["ok"] is True

    def test_no_baseline_ok(self) -> None:
        res = check_structure_redundancy(pd.Series([1.0, 2.0]), {})
        assert res["ok"] is True


class TestR4Family:
    def test_list_families(self) -> None:
        families = list_families()
        assert "momentum" in families
        assert "term_structure" in families
        assert len(families) >= 19

    def test_get_momentum_family(self) -> None:
        seeds = get_seeds_by_family("momentum")
        assert len(seeds) > 0
        assert all(s.get("name", "").startswith("fut_") for s in seeds)

    def test_unknown_family_empty(self) -> None:
        assert get_seeds_by_family("no_such_family") == []

    def test_stock_market_empty(self) -> None:
        assert get_seeds_by_family("momentum", market="stock") == []


class TestEvaluate:
    def test_summary_warnings(self) -> None:
        factor = {
            "factor_id": "f1",
            "name": "bad_factor",
            "signature": {"input_fields": ["close", "fut_inventory"]},
        }
        res = evaluate_structure_constraints(factor, symbol_ic={}, chain_symbols={})
        assert res["structure_ok"] is False
        assert any("L2 缺失字段禁依赖" in w for w in res["warnings"])

    def test_clean_factor_ok(self) -> None:
        factor = {
            "factor_id": "f2",
            "name": "good_factor",
            "signature": {"input_fields": ["close", "hold"]},
        }
        res = evaluate_structure_constraints(factor)
        assert res["structure_ok"] is True
        assert res["warnings"] == []
