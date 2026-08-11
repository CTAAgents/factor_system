"""tests/factor_engine/test_portfolio_qc.py — 组合质检三标准测试（GAP-063）。

覆盖: 合成增益 / 分散化增益 / 回撤控制（实测路径）。
HARNESS §测试随重构。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import PortfolioSignal
from fts.factor_engine.portfolio_loop import build_combo


def _signals(n: int = 3) -> list[PortfolioSignal]:
    """n 个样本信号（不同 sharpe）。"""
    sharps = [2.5, 2.0, 1.8]
    return [
        PortfolioSignal(
            factor_id=f"fct_{i:03d}",
            name=f"f_{i}",
            weight=1.0 / n,
            sharpe=sharps[i % len(sharps)],
            ic=0.05 - i * 0.01,
            turnover=0.3,
            decay_6m=0.1,
            orthogonalized=False,
            retained=True,
        )
        for i in range(n)
    ]


def test_qc_standards_present_estimated_path():
    """估算路径输出 synthesis_gain / diversification_gain（无 drawdown，因非实测）。"""
    combo = build_combo(_signals(), mode="equal_weight")
    assert "qc_standards" in combo
    qc = combo["qc_standards"]
    assert "synthesis_gain" in qc
    assert "diversification_gain" in qc
    assert "drawdown_control_ratio" not in qc


def test_synthesis_gain_definition():
    """合成增益 = 组合夏普 / 最佳单因子夏普。"""
    combo = build_combo(_signals(), mode="equal_weight")
    qc = combo["qc_standards"]
    best_single = max(s["sharpe"] for s in combo["signals"] if s.get("retained"))
    assert qc["synthesis_gain"] == pytest.approx(combo["combo_sharpe"] / best_single)


def test_diversification_gain_definition():
    """分散化增益 = 组合夏普 / 权重加权平均因子夏普。"""
    combo = build_combo(_signals(), mode="equal_weight")
    qc = combo["qc_standards"]
    retained = [s for s in combo["signals"] if s.get("retained")]
    tw = sum(s["weight"] for s in retained)
    wavg = sum(s["weight"] * s["sharpe"] for s in retained) / tw
    assert qc["diversification_gain"] == pytest.approx(combo["combo_sharpe"] / wavg)


def test_measured_path_drawdown_control():
    """实测路径（factor_returns 对齐）输出 drawdown_control_ratio。"""
    rng = np.random.default_rng(9)
    idx = pd.date_range("2024-01-01", periods=120, freq="D")
    fr = pd.DataFrame(
        {f"fct_{i:03d}": rng.normal(0.001, 0.01, len(idx)) for i in range(3)},
        index=idx,
    )
    combo = build_combo(_signals(), mode="equal_weight", factor_returns=fr)
    assert combo["metrics_source"] == "measured"
    qc = combo["qc_standards"]
    assert "drawdown_control_ratio" in qc


def test_passed_flags_are_bool():
    """passed 标记为布尔值。"""
    combo = build_combo(_signals(), mode="equal_weight")
    qc = combo["qc_standards"]
    assert isinstance(qc.get("synthesis_passed"), bool)
    assert isinstance(qc.get("diversification_passed"), bool)


def test_empty_retained_no_qc_crash():
    """无保留因子时不崩溃，qc_standards 为空 dict。"""
    signals = [PortfolioSignal(
        factor_id="f1", name="n", weight=1.0, sharpe=1.0, ic=0.0,
        turnover=0.0, decay_6m=0.0, orthogonalized=False, retained=False,
    )]
    combo = build_combo(signals, mode="equal_weight")
    assert combo["qc_standards"] == {}
