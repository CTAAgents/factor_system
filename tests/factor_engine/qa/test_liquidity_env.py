"""test_liquidity_env — plans/59 OPT-08（GAP-168）容量/交易性评分流动性环境动态化测试。"""

from __future__ import annotations

import pytest

from fts.factor_engine.factor_quality_card import FactorQualityCard
from fts.factor_engine.qa.liquidity_env import (
    LiquidityEnvConfig,
    apply_capacity_scale,
    liquidity_env_scale,
)


# ─── liquidity_env_scale ────────────────────────────────────


def test_normal_env_scale_one() -> None:
    """正常期（非移仓、价差正常）→ 1.0。"""
    s = liquidity_env_scale({"roll_active": False, "spread_ratio": 1.0})
    assert s == 1.0


def test_roll_window_scale_min() -> None:
    """移仓窗口 → 下限系数（0.5）。"""
    s = liquidity_env_scale({"roll_active": True, "spread_ratio": 1.0})
    assert s == pytest.approx(0.5)


def test_spread_widening_scales_down() -> None:
    """价差扩大 → 按超警告线比例线性下调。"""
    # 警告线 1.5，价差 2.0 → excess = 0.5/1.5 = 0.333 → scale = 1-0.333*0.5 = 0.833
    s = liquidity_env_scale({"roll_active": False, "spread_ratio": 2.0})
    assert s == pytest.approx(1.0 - 0.5 * (0.5 / 1.5), abs=1e-4)


def test_spread_extreme_floor() -> None:
    """价差极端扩大 → 下调至下限（不破 scale_min）。"""
    s = liquidity_env_scale({"roll_active": False, "spread_ratio": 10.0})
    assert s == pytest.approx(0.5)


def test_snapshot_missing_no_penalty() -> None:
    """快照缺失/关键字段缺失 → 1.0（不误伤）。"""
    assert liquidity_env_scale(None) == 1.0
    assert liquidity_env_scale({}) == 1.0
    assert liquidity_env_scale({"roll_active": False}) == 1.0


def test_disabled_always_one() -> None:
    """enabled=False → 恒 1.0。"""
    cfg = LiquidityEnvConfig(enabled=False)
    assert liquidity_env_scale({"roll_active": True, "spread_ratio": 5.0}, cfg) == 1.0


def test_custom_scale_min() -> None:
    """自定义下限系数生效。"""
    cfg = LiquidityEnvConfig(capacity_scale_min=0.3)
    assert liquidity_env_scale({"roll_active": True, "spread_ratio": 1.0}, cfg) == pytest.approx(0.3)


# ─── apply_capacity_scale ───────────────────────────────────


def test_apply_scale_downgrades() -> None:
    """容量分 × 缩放系数。"""
    assert apply_capacity_scale(5.0, 0.5) == pytest.approx(2.5)
    assert apply_capacity_scale(4.0, 0.6) == pytest.approx(2.4)


def test_apply_no_scale_unchanged() -> None:
    """scale=None / 1.0 → 不变。"""
    assert apply_capacity_scale(4.0, None) == 4.0
    assert apply_capacity_scale(4.0, 1.0) == 4.0


def test_apply_clamped() -> None:
    """缩放结果 clamp [0,5]。"""
    assert apply_capacity_scale(5.0, 1.5) == 5.0  # 上限
    assert apply_capacity_scale(0.0, 0.5) == 0.0  # 下限


# ─── FactorQualityCard.evaluate 集成 ────────────────────────


def _card() -> FactorQualityCard:
    return FactorQualityCard()


def test_evaluate_default_scale_unchanged() -> None:
    """默认 liquidity_scale=1.0 → 容量/交易性分不变。"""
    r = _card().evaluate(
        factor_id="f1",
        ic=0.05,
        sharpe=1.5,
        capacity_estimate=100_000_000,  # → capacity 5.0
        turnover=100.0,  # → tradability 5.0
    )
    dims = {d["name"]: d["score"] for d in r["dimension_scores"]}
    assert dims["capacity_score"] == pytest.approx(5.0)
    assert dims["tradability_score"] == pytest.approx(5.0)


def test_evaluate_roll_scale_downgrades() -> None:
    """移仓期（liquidity_scale=0.5）→ 容量/交易性分下调。"""
    r = _card().evaluate(
        factor_id="f1",
        ic=0.05,
        sharpe=1.5,
        capacity_estimate=100_000_000,  # 5.0 × 0.5 = 2.5
        turnover=100.0,  # 5.0 × 0.5 = 2.5
        liquidity_scale=0.5,
    )
    dims = {d["name"]: d["score"] for d in r["dimension_scores"]}
    assert dims["capacity_score"] == pytest.approx(2.5)
    assert dims["tradability_score"] == pytest.approx(2.5)


def test_evaluate_other_dims_untouched() -> None:
    """缩放只影响容量/交易性维度，不影响 IC/Sharpe 等。"""
    r_full = _card().evaluate(
        factor_id="f1", ic=0.05, sharpe=1.5, capacity_estimate=100_000_000, turnover=100.0
    )
    r_scaled = _card().evaluate(
        factor_id="f1",
        ic=0.05,
        sharpe=1.5,
        capacity_estimate=100_000_000,
        turnover=30.0,
        liquidity_scale=0.5,
    )
    d_full = {d["name"]: d["score"] for d in r_full["dimension_scores"]}
    d_scaled = {d["name"]: d["score"] for d in r_scaled["dimension_scores"]}
    assert d_scaled["ic_score"] == d_full["ic_score"]
    assert d_scaled["sharpe_score"] == d_full["sharpe_score"]
    assert d_scaled["capacity_score"] < d_full["capacity_score"]
    assert d_scaled["tradability_score"] < d_full["tradability_score"]
