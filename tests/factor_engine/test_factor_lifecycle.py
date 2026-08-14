"""test_factor_lifecycle — 因子生命周期管理测试（CTA 手册阶段11.3）。"""

from __future__ import annotations

import numpy as np

from fts.factor_engine.factor_lifecycle import (
    factor_lifecycle_plan,
    factor_lifecycle_review,
)


def test_healthy_factor_holds() -> None:
    """IC 未衰减、IR 达标 → hold。"""
    rng = np.random.RandomState(1)
    ic = rng.standard_normal(120) * 0.02 + 0.06  # 年化 IR ≈ 0.06/0.02*√252 ≈ 4.8
    r = factor_lifecycle_review(ic, oos_baseline_ic=0.06)
    assert r["action"] == "hold"
    assert r["ic_triggered"] is False
    assert r["ir_triggered"] is False


def test_ic_decay_triggers_review() -> None:
    """滚动 60 日 IC 均值较基准衰减 >30% → 归零权重复审。"""
    rng = np.random.RandomState(2)
    ic = np.concatenate(
        [
            rng.standard_normal(80) * 0.01 + 0.06,  # 训练期水平
            rng.standard_normal(80) * 0.01 + 0.03,  # 近期腰斩
        ]
    )
    r = factor_lifecycle_review(ic, oos_baseline_ic=0.06)
    assert r["action"] == "zero_weight_review"
    assert r["ic_triggered"] is True
    assert r["decay_ratio"] is not None and r["decay_ratio"] > 0.30


def test_ir_below_floor_triggers_review() -> None:
    """滚动 IR 跌破 0.3 → 归零权重复审（IC 衰减不触发，单验 IR 路径）。"""
    t = np.arange(120)
    # 交替 ±0.25 + 0.004：mean=0.004, std=0.25 精确可控
    ic = 0.004 + 0.25 * (2.0 * ((t % 2) == 0) - 1.0)
    # 年化 IR = 0.004/0.25*√252 ≈ 0.25 < 0.3；baseline=0.005 → decay=0.2 < 0.3
    r = factor_lifecycle_review(ic, oos_baseline_ic=0.005)
    assert r["action"] == "zero_weight_review"
    assert r["ir_triggered"] is True
    assert r["ic_triggered"] is False


def test_insufficient_samples_conservative() -> None:
    """样本不足 → 保守保活，不判退役。"""
    r = factor_lifecycle_review(np.array([0.05, 0.04, 0.06, 0.05]), oos_baseline_ic=0.05)
    assert r["action"] == "hold"
    assert "样本不足" in r["reasons"][0]


def test_baseline_zero_no_divide_error() -> None:
    """基准 IC 为 0 → 不崩溃、不误判衰减。"""
    rng = np.random.RandomState(4)
    ic = rng.standard_normal(120) * 0.01 + 0.02
    r = factor_lifecycle_review(ic, oos_baseline_ic=0.0)
    assert r["decay_ratio"] == 0.0
    assert r["action"] in ("hold", "zero_weight_review")


def test_nan_ic_skipped() -> None:
    """NaN IC 值被忽略，不影响滚动统计。"""
    rng = np.random.RandomState(5)
    ic = rng.standard_normal(120) * 0.01 + 0.06
    ic[10:20] = np.nan
    r = factor_lifecycle_review(ic, oos_baseline_ic=0.06)
    assert r["action"] == "hold"


def test_custom_window_and_thresholds() -> None:
    """自定义窗口与阈值生效。"""
    rng = np.random.RandomState(6)
    ic = np.concatenate(
        [
            rng.standard_normal(80) * 0.01 + 0.06,
            rng.standard_normal(80) * 0.01 + 0.05,
        ]
    )
    # 宽松阈值 → 不触发；严格阈值 → 触发
    r_loose = factor_lifecycle_review(ic, 0.06, window=30, ic_decay_ratio=0.60)
    r_strict = factor_lifecycle_review(ic, 0.06, window=30, ic_decay_ratio=0.10)
    assert r_loose["ic_triggered"] is False
    assert r_strict["ic_triggered"] is True


def test_lifecycle_plan_contains_rules() -> None:
    """生命周期机制说明包含退役标准。"""
    plan = factor_lifecycle_plan()
    assert plan["window"] == 60
    assert plan["retire_action"] == "zero_weight_review"
    assert any("60日" in r for r in plan["rules"])
    assert any("IR跌破0.3" in r for r in plan["rules"])
