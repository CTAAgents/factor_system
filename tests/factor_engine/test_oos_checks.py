"""test_oos_checks — 过拟合排查与绩效归因测试（CTA 手册阶段9）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.oos_checks import (
    annual_returns,
    performance_decay_check,
    period_consistency_check,
    sector_returns_contribution,
)


# ─── 样本内外衰减率 ───────────────────────────────────────


def test_decay_within_limit() -> None:
    """训练 1.5 / 验证 1.3 → 衰减 13% ≤ 30% → 通过。"""
    r = performance_decay_check(1.5, 1.3)
    assert r["passed"] is True
    assert r["decay_ratio"] == pytest.approx(0.2 / 1.5)


def test_decay_exceeds_limit() -> None:
    """训练 2.0 / 验证 1.0 → 衰减 50% > 30% → 不通过。"""
    r = performance_decay_check(2.0, 1.0)
    assert r["passed"] is False
    assert r["decay_ratio"] == pytest.approx(0.5)


def test_decay_negative_zero_denom() -> None:
    """分母为 0 → 不崩溃。"""
    r = performance_decay_check(0.0, 0.5)
    assert "passed" in r


# ─── 时间分段一致性 ───────────────────────────────────────


def test_period_consistency_all_positive() -> None:
    """三段全正 → 通过。"""
    n = 400
    dates = pd.date_range("2019-01-01", periods=n, freq="B")
    rng = np.random.RandomState(1)
    ret = rng.standard_normal(n) * 0.01 + 0.002  # 稳定正漂移
    periods = [
        ("p1", "2019-01-01", "2020-12-31"),
        ("p2", "2021-01-01", "2022-12-31"),
        ("p3", "2023-01-01", "2026-12-31"),
    ]
    r = period_consistency_check(ret, dates, periods=periods)
    assert r["positive_ratio"] == pytest.approx(1.0)
    assert r["passed"] is True


def test_period_consistency_mixed() -> None:
    """两正一负 → ratio=2/3 ≥ 0.5 → 通过。"""
    n = 1050  # 2019-01-01 起 ~1046 工作日覆盖到 2022 年末
    dates = pd.date_range("2019-01-01", periods=n, freq="B")
    rng = np.random.RandomState(2)
    ret = rng.standard_normal(n) * 0.01
    periods = [
        ("p1", "2019-01-01", "2019-12-31"),
        ("p2", "2020-01-01", "2020-12-31"),
        ("p3", "2021-01-01", "2022-12-31"),
    ]
    # 按时间区间注入漂移，保证与 periods 严格对齐、符号明确
    drifts = {"p1": 0.003, "p2": -0.003, "p3": 0.003}
    for name, start, end in periods:
        mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
        ret[mask] += drifts[name]
    r = period_consistency_check(ret, dates, periods=periods)
    assert r["positive_ratio"] == pytest.approx(2 / 3)
    assert r["passed"] is True


def test_period_consistency_insufficient_data() -> None:
    """样本与分段区间不重叠 → 全部 skipped，不判失败。"""
    r = period_consistency_check(
        np.ones(10),
        pd.date_range("2024-01-01", periods=10, freq="B"),
        periods=[("p1", "2015-01-01", "2015-12-31"), ("p2", "2016-01-01", "2016-12-31")],
    )
    assert all(p["status"] == "skipped" for p in r["periods"].values())
    assert r["passed"] is False  # 无有效分段


# ─── 分年度绩效 ───────────────────────────────────────────


def test_annual_returns_by_year() -> None:
    """分年度收益正确。"""
    dates = pd.date_range("2020-01-01", periods=500, freq="B")
    rng = np.random.RandomState(3)
    ret = rng.standard_normal(500) * 0.01
    out = annual_returns(ret, dates)
    assert set(out.keys()) == {"2020", "2021"}
    assert out["2020"]["n_days"] >= 250
    assert "annual_return" in out["2020"]


# ─── 分板块收益贡献 ───────────────────────────────────────


def test_sector_returns_contribution() -> None:
    """分板块收益贡献正确。"""
    rng = np.random.RandomState(4)
    by_symbol = {
        "RB": rng.standard_normal(100) * 0.01 + 0.002,
        "HC": rng.standard_normal(100) * 0.01 + 0.002,
        "I": rng.standard_normal(100) * 0.01 - 0.001,
    }
    sector_map = {"RB": "黑色", "HC": "黑色", "I": "黑色"}
    r = sector_returns_contribution(by_symbol, sector_map)
    assert r["passed"] is True
    assert "黑色" in r["sectors"]
    assert r["sectors"]["黑色"]["n_symbols"] == 3
    assert r["sectors"]["黑色"]["contribution_share"] == pytest.approx(1.0)


def test_sector_returns_multi_sector() -> None:
    """多板块收益贡献占比。"""
    by_symbol = {"RB": np.ones(10) * 0.01, "CU": np.ones(10) * 0.01}
    sector_map = {"RB": "黑色", "CU": "有色"}
    r = sector_returns_contribution(by_symbol, sector_map)
    assert set(r["sectors"].keys()) == {"黑色", "有色"}
    assert r["sectors"]["黑色"]["contribution_share"] == pytest.approx(0.5)


def test_sector_returns_empty_safe() -> None:
    """空输入 → 不崩溃。"""
    r = sector_returns_contribution({}, {})
    assert r["passed"] is False
