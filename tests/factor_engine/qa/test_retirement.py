"""test_qa_retirement — 退役判定 5 条红线测试（CTA 手册 6.7）。"""

from __future__ import annotations

from fts.factor_engine.qa.retirement import (
    IC_DECAY_RETIRE,
    IR_FLOOR_RETIRE,
    RETIREMENT_REDLINES,
    check_retirement,
)


def test_no_redline_retains() -> None:
    """无红线触发 → 维持服役。"""
    r = check_retirement(
        consecutive_warn_months=2,
        current_ic60=0.05,
        entry_ic60=0.06,
        ir60=0.4,
        logic_valid=True,
        data_source_alive=True,
    )
    assert r["triggered"] is False
    assert r["action"] == "retain"
    assert r["triggered_ids"] == []


def test_r1_consecutive_3_months() -> None:
    """红线1：连续 3 月预警 → 触发退役。"""
    r = check_retirement(consecutive_warn_months=3)
    assert r["triggered"] is True
    assert "R1" in r["triggered_ids"]


def test_r2_ic_decay_50pct() -> None:
    """红线2：60 日 IC 较入库时下降 > 50% → 触发。"""
    r = check_retirement(current_ic60=0.02, entry_ic60=0.06)
    assert (0.06 - 0.02) / 0.06 > IC_DECAY_RETIRE
    assert "R2" in r["triggered_ids"]


def test_r2_boundary_not_triggered() -> None:
    """红线2：下降恰好 50% 不触发（严格大于）。"""
    r = check_retirement(current_ic60=0.03, entry_ic60=0.06)
    assert (0.06 - 0.03) / 0.06 == 0.5
    assert "R2" not in r["triggered_ids"]


def test_r3_ir_below_floor() -> None:
    """红线3：IR 跌破 0.15 → 触发。"""
    assert IR_FLOOR_RETIRE == 0.15
    r = check_retirement(ir60=0.10)
    assert "R3" in r["triggered_ids"]


def test_r4_logic_invalid() -> None:
    """红线4：经济学逻辑失效 → 触发。"""
    r = check_retirement(logic_valid=False)
    assert "R4" in r["triggered_ids"]


def test_r5_data_source_dead() -> None:
    """红线5：数据源永久中断 → 触发。"""
    r = check_retirement(data_source_alive=False)
    assert "R5" in r["triggered_ids"]


def test_multiple_redlines() -> None:
    """多条红线同时触发。"""
    r = check_retirement(consecutive_warn_months=4, ir60=0.1, logic_valid=False)
    assert {"R1", "R3", "R4"} <= set(r["triggered_ids"])


def test_nan_ic_not_trigger() -> None:
    """IC 缺失 → 红线2 不触发（保守不误退役）。"""
    r = check_retirement(current_ic60=None, entry_ic60=0.06)
    assert "R2" not in r["triggered_ids"]


def test_redlines_config_complete() -> None:
    """5 条红线定义完整。"""
    assert len(RETIREMENT_REDLINES) == 5
    assert [r["id"] for r in RETIREMENT_REDLINES] == ["R1", "R2", "R3", "R4", "R5"]


def test_report_contains_retire_flow() -> None:
    """触发退役时报告包含退役流程。"""
    r = check_retirement(consecutive_warn_months=3)
    assert "退役流程" in r["report"]
