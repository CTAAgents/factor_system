"""plans/54 P0-2 — IC 半衰期估算 + 生命周期退化维度 单元测试。

覆盖：
  - estimate_ic_half_life：公式正确（对齐 high_ic_screener 口径）/ 零衰减 inf / 完全衰减 1.0 / 无效 None
  - decide_factor 半衰期维度：过短 → shadow + 原因标记；充足/None → 不影响
"""

from __future__ import annotations

import pytest

from fts.factor_engine.energy_qa_review import EnergyQaReviewConfig, decide_factor
from fts.factor_engine.factor_lifecycle import estimate_ic_half_life


class TestEstimateHalfLife:
    """estimate_ic_half_life 半衰期公式（plans/54 P0-2）。"""

    def test_formula_matches_reference(self) -> None:
        """decay=0.5 → half_life = ln(0.5)/ln(0.5)*126 = 126 日。"""
        assert estimate_ic_half_life(0.5) == pytest.approx(126.0)

    def test_high_decay_short_half_life(self) -> None:
        """decay=0.9 → 半衰期远小于 63 日。"""
        hl = estimate_ic_half_life(0.9)
        assert hl is not None and hl < 63.0

    def test_zero_decay_inf(self) -> None:
        """零衰减 → inf（信号完全稳定，除零防护）。"""
        assert estimate_ic_half_life(0.0) == float("inf")

    def test_full_decay_one(self) -> None:
        """完全衰减（≥0.999）→ 1.0。"""
        assert estimate_ic_half_life(1.0) == 1.0

    def test_none_returns_none(self) -> None:
        """None/非数值 → None。"""
        assert estimate_ic_half_life(None) is None
        assert estimate_ic_half_life("x") is None

    def test_negative_decay_abs(self) -> None:
        """负衰减（增强）取绝对值（对齐 high_ic_screener abs）。"""
        assert estimate_ic_half_life(-0.5) == pytest.approx(126.0)


class TestDecideFactorHalfLife:
    """decide_factor 半衰期维度（plans/54 P0-2）。"""

    def _base(self, **kw):
        base = dict(
            factor_id="fct_00000001",
            name="f",
            prev_status="active",
            reaudit_fail=False,
            curr_ic=0.10,
            hist_ic=0.12,
            slope_grade="normal",
            cfg=EnergyQaReviewConfig(),
        )
        base.update(kw)
        return base

    def test_short_half_life_shadow(self) -> None:
        """半衰期过短（<63 日）→ shadow + "半衰期过短" 原因。"""
        disp = decide_factor(**self._base(half_life_days=30.0))
        assert disp.decision == "shadow"
        assert any("半衰期过短" in r for r in disp.reasons)

    def test_sufficient_half_life_no_effect(self) -> None:
        """半衰期充足（≥63 日）→ 不影响（达标仍 active）。"""
        disp = decide_factor(**self._base(half_life_days=200.0))
        assert disp.decision == "active"
        assert "半衰期过短" not in disp.reasons

    def test_none_half_life_no_effect(self) -> None:
        """half_life=None（未估计）→ 不触发（向后兼容）。"""
        disp = decide_factor(**self._base(half_life_days=None))
        assert disp.decision == "active"

    def test_short_half_life_stacks_with_others(self) -> None:
        """半衰期过短与其它退化信号叠加（宁严勿松）：ic_drop 33% + 半衰期过短 → shadow。"""
        disp = decide_factor(
            **self._base(half_life_days=30.0, curr_ic=0.08, hist_ic=0.12)
        )
        assert disp.decision == "shadow"
        assert any("IC降幅" in r for r in disp.reasons)
        assert any("半衰期过短" in r for r in disp.reasons)
