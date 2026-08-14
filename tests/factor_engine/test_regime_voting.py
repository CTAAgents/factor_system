"""test_regime_voting — 手册五指标投票 Regime 检测器测试（CTA 手册阶段5）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.regime_voting import (
    RegimeVotingDetector,
    classify_regime,
    compute_regime_indicators,
    conditional_ic,
    detect_regime_voting,
    hurst_exponent,
    regime_switch_benefit,
    transition_position_scale,
    unstable_review_required,
)


# ─── Hurst 指数 ───────────────────────────────────────────


def test_hurst_random_walk_around_0_5() -> None:
    """随机游走 → H ≈ 0.5。"""
    rng = np.random.RandomState(1)
    returns = rng.standard_normal(2000)
    h = hurst_exponent(returns)
    assert abs(h - 0.5) < 0.15


def test_hurst_trend_above_0_5() -> None:
    """趋势序列 → H > 0.55。"""
    rng = np.random.RandomState(2)
    drift = np.cumsum(rng.standard_normal(2000) + 0.01)
    returns = np.diff(np.log(np.abs(drift) + 100))
    h = hurst_exponent(returns)
    assert h > 0.55


def test_hurst_insufficient_data_neutral() -> None:
    """样本不足 → 中性 0.5。"""
    assert hurst_exponent(np.array([1.0, 2.0, 3.0])) == 0.5


# ─── 投票判定 ─────────────────────────────────────────────


def test_classify_trend_majority() -> None:
    """≥3 票趋势 → trend。"""
    assert classify_regime(["trend", "trend", "trend", "oscillation", "neutral"]) == "trend"


def test_classify_oscillation_majority() -> None:
    """≥3 票震荡 → oscillation。"""
    assert classify_regime(["oscillation", "oscillation", "oscillation", "trend", "neutral"]) == "oscillation"


def test_classify_transition_no_majority() -> None:
    """无 ≥3 票 → transition。"""
    assert classify_regime(["trend", "trend", "oscillation", "oscillation", "neutral"]) == "transition"


# ─── 面板指标与检测 ───────────────────────────────────────


def _panel_trend(n: int = 150, n_syms: int = 20) -> dict[str, pd.DataFrame]:
    """构造同向上涨趋势面板（漂移随机游走）。"""
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    panel = {}
    for s in range(n_syms):
        rng = np.random.RandomState(s)
        noise = rng.standard_normal(n) * 0.2
        close = 100.0 * (s + 1) + np.cumsum(noise + 0.02)  # 显著正漂移 → 单调上行
        panel[f"S{s}"] = pd.DataFrame(
            {"open": close, "high": close + 0.5, "low": close - 0.5, "close": close},
            index=idx,
        )
    return panel


def _panel_oscillation(n: int = 150, n_syms: int = 20) -> dict[str, pd.DataFrame]:
    """构造无趋势震荡面板（均值回归 AR(1) 反相，品种独立）。"""
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    panel = {}
    for s in range(n_syms):
        rng = np.random.RandomState(100 + s)
        x = np.empty(n)
        x[0] = 0.0
        eps = rng.standard_normal(n) * 0.5
        for t in range(1, n):
            x[t] = -0.5 * x[t - 1] + eps[t]  # 强均值回归 → 来回震荡无趋势
        close = 100.0 * (s + 1) + x
        panel[f"S{s}"] = pd.DataFrame(
            {"open": close, "high": close + 1, "low": close - 1, "close": close},
            index=idx,
        )
    return panel


def test_detect_trend_regime() -> None:
    """趋势面板 → 判定为 trend。"""
    result = detect_regime_voting(_panel_trend())
    assert result["regime"] == "trend"
    assert result["votes"].count("trend") >= 3


def test_detect_oscillation_regime() -> None:
    """震荡面板 → 判定为 oscillation 或 transition（非 trend）。"""
    result = detect_regime_voting(_panel_oscillation())
    assert result["regime"] in ("oscillation", "transition")


def test_compute_indicators_schema() -> None:
    """指标输出字段完整。"""
    ind = compute_regime_indicators(_panel_trend())
    for key in ("adx", "hurst", "vol_percentile", "consistency_ratio", "dispersion_direction"):
        assert key in ind


def test_empty_panel_neutral() -> None:
    """空面板 → 中性不崩溃。"""
    result = detect_regime_voting({})
    assert result["regime"] in ("transition", "oscillation")


# ─── 状态机：防抖 / 连续不稳 ──────────────────────────────


def test_debounce_same_day_switch() -> None:
    """同日重复切换 → 防抖保持原 Regime。"""
    detector = RegimeVotingDetector()
    trend = _panel_trend()
    osc = _panel_oscillation()
    d1 = detector.detect(trend, "2024-01-02")  # trend
    d2 = detector.detect(osc, "2024-01-02")  # 同日切 oscillation → 防抖
    assert d2["regime"] == d1["regime"]
    assert d2["debounced"] is True


def test_unstable_days_accumulate_and_review() -> None:
    """连续切换 → unstable_days 累积，达到 7 日触发复审。"""
    detector = RegimeVotingDetector()
    trend = _panel_trend()
    osc = _panel_oscillation()
    last: dict = {}
    for i in range(8):
        panel = trend if i % 2 == 0 else osc  # 交替面板 → 每次状态切换
        last = detector.detect(panel, f"2024-01-{10 + i:02d}")
    # 8 日交替 → 7 次切换 → unstable_days=7 → 触发复审
    assert detector._unstable_days >= 7
    assert last["review_required"] is True
    assert unstable_review_required(7) is True
    assert unstable_review_required(6) is False


# ─── 过渡降仓 ─────────────────────────────────────────────


def test_transition_position_scale() -> None:
    """过渡 Regime 仓位降至基准 70%。"""
    assert transition_position_scale("trend") == 1.0
    assert transition_position_scale("oscillation") == 1.0
    assert transition_position_scale("transition") == pytest.approx(0.7)


# ─── 条件 IC ──────────────────────────────────────────────


def test_conditional_ic_by_regime() -> None:
    """条件 IC：各 Regime 分别计算 IC。"""
    n = 120
    rng = np.random.RandomState(5)
    signal = rng.standard_normal(n)
    ret = signal * 0.5 + rng.standard_normal(n) * 0.3  # 强相关
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    regime_map = {}
    for i, d in enumerate(dates):
        regime_map[d.strftime("%Y-%m-%d")] = "trend" if i < 60 else "oscillation"
    out = conditional_ic(signal, ret, regime_map, dates)
    assert "trend" in out and "oscillation" in out
    assert out["trend"]["ic"] > 0
    assert out["trend"]["n"] >= 55


# ─── 动态 vs 固定对比 ─────────────────────────────────────


def test_regime_switch_benefit() -> None:
    """动态 vs 固定夏普提升 ≥ 0.2 → 通过。"""
    assert regime_switch_benefit(1.8, 1.5)["passed"] is True
    assert regime_switch_benefit(1.6, 1.5)["passed"] is False
    assert regime_switch_benefit(1.5, 1.5)["gain"] == pytest.approx(0.0)
