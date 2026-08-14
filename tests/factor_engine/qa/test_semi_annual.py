"""test_qa_semi_annual — 半年度深度复检 D1-D4 测试（CTA 手册 6.6）。"""

from __future__ import annotations

from fts.factor_engine.qa.semi_annual import semi_annual_recheck


def test_all_healthy_passes() -> None:
    """D1-D4 全部健康 → 通过。"""
    r = semi_annual_recheck(
        logic_valid=True,
        backtest_sharpe_ratio=0.95,
        pool_reconstructed=False,
        retired_review={},
    )
    assert r["passed"] is True
    assert r["flagged_count"] == 0


def test_d1_logic_failed_flagged() -> None:
    """D1：经济学逻辑失效 → 标记（触发退役红线）。"""
    r = semi_annual_recheck(logic_valid=False)
    assert r["indicators"]["D1"]["flagged"] is True
    assert "失效" in r["indicators"]["D1"]["detail"]


def test_d2_backtest_decay_flagged() -> None:
    """D2：全样本回测重跑夏普明显下降 → 标记。"""
    r = semi_annual_recheck(backtest_sharpe_ratio=0.6)
    assert r["indicators"]["D2"]["flagged"] is True


def test_d3_pool_reconstructed_flagged() -> None:
    """D3：品种池重构 → 标记需评估影响。"""
    r = semi_annual_recheck(pool_reconstructed=True)
    assert r["indicators"]["D3"]["flagged"] is True


def test_d4_retired_revival_detected() -> None:
    """D4：淘汰库复审发现重新有效因子。"""
    r = semi_annual_recheck(retired_review={"fut_old_1": True, "fut_old_2": False})
    assert r["revived_factors"] == ["fut_old_1"]
    assert "fut_old_1" in r["indicators"]["D4"]["detail"]


def test_d4_no_revival() -> None:
    """D4：无重新有效因子。"""
    r = semi_annual_recheck(retired_review={"fut_old_1": False})
    assert r["revived_factors"] == []


def test_empty_input_safe() -> None:
    """空输入 → 不崩溃，无法判定项不标记。"""
    r = semi_annual_recheck()
    assert r["passed"] is True
    assert r["flagged_count"] == 0
