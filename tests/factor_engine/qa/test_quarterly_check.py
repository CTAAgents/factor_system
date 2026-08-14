"""test_qa_quarterly — 季度全量复检 F1-F6 测试（CTA 手册 6.6）。"""

from __future__ import annotations

from fts.factor_engine.qa.quarterly_check import quarterly_recheck


def test_all_healthy_passes() -> None:
    """F1-F6 全部健康 → 通过。"""
    r = quarterly_recheck(
        ic_ir_ratio=0.95,
        layered_ratio=0.90,
        param_steps=0,
        new_high_corr_pairs=0,
        cond_ic_change=0.1,
        sector_consistent=True,
    )
    assert r["passed"] is True
    assert r["flagged_count"] == 0


def test_f1_ic_ir_decay_flagged() -> None:
    """F1：全样本 IC/IR 相对基准明显衰减 → 标记。"""
    r = quarterly_recheck(ic_ir_ratio=0.70)
    assert r["indicators"]["F1"]["flagged"] is True
    assert r["passed"] is False


def test_f3_param_shift_flagged() -> None:
    """F3：参数档位偏移 > 1 → 标记。"""
    r = quarterly_recheck(param_steps=2)
    assert r["indicators"]["F3"]["flagged"] is True


def test_f4_high_corr_flagged() -> None:
    """F4：新增高相关对 → 标记需正交化。"""
    r = quarterly_recheck(new_high_corr_pairs=1)
    assert r["indicators"]["F4"]["flagged"] is True


def test_f5_cond_ic_change_flagged() -> None:
    """F5：条件 IC 变化 > 50% → 标记。"""
    r = quarterly_recheck(cond_ic_change=0.6)
    assert r["indicators"]["F5"]["flagged"] is True


def test_f6_sector_inconsistent_flagged() -> None:
    """F6：板块方向不一致 → 标记。"""
    r = quarterly_recheck(sector_consistent=False)
    assert r["indicators"]["F6"]["flagged"] is True


def test_empty_input_not_flagged() -> None:
    """数据缺失 → 全部无法判定，不标记。"""
    r = quarterly_recheck()
    assert r["passed"] is True
    assert r["flagged_count"] == 0


def test_multiple_flags_reasons() -> None:
    """多标记时 reasons 汇总完整。"""
    r = quarterly_recheck(ic_ir_ratio=0.5, sector_consistent=False)
    assert len(r["flagged_items"]) == 2
    assert any("F1" in x for x in r["reasons"])
    assert any("F6" in x for x in r["reasons"])
