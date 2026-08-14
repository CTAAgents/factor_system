"""test_capital_ramp — 资金三级爬坡测试（CTA 手册阶段11.1）。"""

from __future__ import annotations

import pytest

from fts.live_trade.capital_ramp import (
    RAMP_STAGES,
    can_advance,
    capital_scale,
    ramp_plan,
    ramp_status,
)


def test_ramp_plan_three_stages() -> None:
    """三级爬坡计划表：10% / 50% / 100%。"""
    plan = ramp_plan()
    assert [p["capital_scale"] for p in plan] == [0.10, 0.50, 1.00]
    assert plan[0]["label"] == "小仓测试"


def test_capital_scale_known_stages() -> None:
    """资金比例映射正确。"""
    assert capital_scale("small") == pytest.approx(0.10)
    assert capital_scale("half") == pytest.approx(0.50)
    assert capital_scale("full") == pytest.approx(1.00)


def test_capital_scale_unknown_safe() -> None:
    """未知阶段 → 0.0 兜底，禁止按全额运行。"""
    assert capital_scale("mega") == 0.0


def test_small_advance_after_30_days() -> None:
    """小仓满 30 天 → 可升级半仓。"""
    assert can_advance("small", 29) is False
    assert can_advance("small", 30) is True
    assert can_advance("small", 45) is True


def test_small_advance_no_extra_condition() -> None:
    """小仓升级不依赖月度稳定指标。"""
    assert can_advance("small", 30, monthly_stable=False) is True


def test_half_advance_requires_monthly_stable() -> None:
    """半仓需连续月度稳定才能升级全额。"""
    assert can_advance("half", 200, monthly_stable=False) is False
    assert can_advance("half", 200, monthly_stable=True) is True


def test_full_never_advances() -> None:
    """全额上线不再升级。"""
    assert can_advance("full", 999, monthly_stable=True) is False


def test_unknown_stage_never_advances() -> None:
    """未知阶段不升级。"""
    assert can_advance("unknown", 999, monthly_stable=True) is False


def test_ramp_status_small_pending() -> None:
    """小仓未满 30 天 → 维持并给出原因。"""
    st = ramp_status("small", 12, monthly_stable=False)
    assert st.stage == "small"
    assert st.capital_scale == pytest.approx(0.10)
    assert st.advance_ready is False
    assert st.next_stage == "half"
    assert "30" in st.reason


def test_ramp_status_half_ready() -> None:
    """半仓连续月度稳定 → 可升级全额。"""
    st = ramp_status("half", 90, monthly_stable=True)
    assert st.advance_ready is True
    assert st.next_stage == "full"


def test_ramp_status_full_normal() -> None:
    """全额上线 → 常态化。"""
    st = ramp_status("full", 365, monthly_stable=True)
    assert st.advance_ready is False
    assert st.next_stage is None
    assert "常态化" in st.reason


def test_ramp_status_unknown_safe() -> None:
    """未知阶段 → 0 资金比例兜底。"""
    st = ramp_status("mega", 1, monthly_stable=True)
    assert st.capital_scale == 0.0
    assert st.advance_ready is False


def test_stages_config_consistent() -> None:
    """配置表阶段名唯一且递增。"""
    names = [s[0] for s in RAMP_STAGES]
    assert len(names) == len(set(names))
    scales = [s[2] for s in RAMP_STAGES]
    assert scales == sorted(scales)
