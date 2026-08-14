"""test_qa_monthly_check — 月度复检 M1-M5 测试（CTA 手册 6.5）。"""

from __future__ import annotations

import numpy as np

from fts.factor_engine.qa.monthly_check import monthly_recheck


def _healthy_ic(n: int = 90) -> np.ndarray:
    """高 IC 低波动 → IR 高、衰减小。"""
    rng = np.random.RandomState(7)
    return rng.standard_normal(n) * 0.01 + 0.06


def test_all_healthy_normal() -> None:
    """M1-M5 全健康 → normal，权重不变。"""
    r = monthly_recheck(
        ic_series=_healthy_ic(),
        oos_baseline_ic=0.06,
        ir_gate=0.30,
        month_layered_return=0.02,
        prev_month_layered_negative=False,
        rank_deviation=0.02,
    )
    assert r["action"] == "normal"
    assert r["weight_scale"] == 1.0
    assert r["warn_count"] == 0


def test_one_warning_observe_50() -> None:
    """1 项预警 → 降权 50%，进入观察期。"""
    rng = np.random.RandomState(8)
    # M1 触发（|IC| ≤ 0.02），基准同水平使 M3 衰减不触发
    ic = rng.standard_normal(90) * 0.01 + 0.005
    r = monthly_recheck(ic, oos_baseline_ic=0.005, ir_gate=0.30, month_layered_return=0.02, rank_deviation=0.02)
    assert r["warn_count"] == 1
    assert r["indicators"]["M1"]["warned"] is True
    assert r["action"] == "observe_50"
    assert r["weight_scale"] == 0.5


def test_two_warnings_observe_30() -> None:
    """2 项预警 → 降权 30%。"""
    rng = np.random.RandomState(9)
    ic = rng.standard_normal(90) * 0.01 + 0.005  # M1 + M2(IR 低) 预警
    r = monthly_recheck(ic, oos_baseline_ic=0.06, ir_gate=0.30, month_layered_return=0.02, rank_deviation=0.02)
    assert r["warn_count"] == 2
    assert r["action"] == "observe_30"
    assert r["weight_scale"] == 0.3


def test_three_warnings_suspend() -> None:
    """3 项及以上预警 → 权重归零，暂停服役。"""
    rng = np.random.RandomState(10)
    ic = rng.standard_normal(90) * 0.01 + 0.005  # M1 + M2 预警
    r = monthly_recheck(
        ic,
        oos_baseline_ic=0.06,
        ir_gate=0.30,
        month_layered_return=-0.01,
        prev_month_layered_negative=True,  # M4
        rank_deviation=0.15,
    )  # M5
    assert r["warn_count"] >= 3
    assert r["action"] == "suspend"
    assert r["weight_scale"] == 0.0


def test_consecutive_3_months_retire_review() -> None:
    """连续 3 月预警 → 触发退役判定。"""
    r = monthly_recheck(
        ic_series=_healthy_ic(),
        oos_baseline_ic=0.06,
        ir_gate=0.30,
        month_layered_return=-0.01,
        prev_month_layered_negative=True,  # M4 连续
        rank_deviation=0.02,
        prev_warn_months=2,
    )
    assert r["consecutive_warn_months"] == 3
    assert r["action"] == "retire_review"
    assert r["weight_scale"] == 0.0


def test_insufficient_ic_not_flagged() -> None:
    """IC 样本不足 → 不误判预警（无法判定项不计数）。"""
    r = monthly_recheck(np.array([0.05, 0.04]), oos_baseline_ic=0.06, ir_gate=0.30)
    assert r["warn_count"] == 0
    assert r["action"] == "normal"


def test_ir_below_80pct_gate_warns() -> None:
    """M2：60 日 IR 低于分类门槛×80% → 预警。"""
    t = np.arange(90)
    # 交替 ±0.3 + 0.005：mean=0.005, std=0.3 精确可控，IR ≈ 0.26 < 0.32
    ic = 0.005 + 0.3 * (2.0 * ((t % 2) == 0) - 1.0)
    r = monthly_recheck(
        ic,
        oos_baseline_ic=0.05,
        ir_gate=0.40,  # 基本面门槛
        month_layered_return=0.02,
        rank_deviation=0.02,
    )
    assert r["indicators"]["M2"]["warned"] is True


def test_ic_decay_30pct_warns() -> None:
    """M3：IC 衰减 ≥ 30% → 预警。"""
    rng = np.random.RandomState(12)
    ic = rng.standard_normal(90) * 0.01 + 0.04  # 较基准 0.06 衰减 33%
    r = monthly_recheck(ic, oos_baseline_ic=0.06, ir_gate=0.30, month_layered_return=0.02, rank_deviation=0.02)
    assert r["indicators"]["M3"]["warned"] is True
