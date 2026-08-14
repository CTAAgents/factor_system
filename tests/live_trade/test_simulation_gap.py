"""test_simulation_gap — 仿真 vs 回测净值偏差对比测试（CTA 手册阶段10）。"""

from __future__ import annotations

import pytest

from fts.live_trade.simulation_gap import simulation_backtest_gap_check


def test_gap_within_limit_passes() -> None:
    """仿真与回测走势接近 → 通过。"""
    dates = [f"2026-01-{d:02d}" for d in range(1, 21)]
    sim = {d: 1.0 + i * 0.001 for i, d in enumerate(dates)}
    bt = {d: 1.0 + i * 0.001 + (0.002 if i >= 1 else 0.0) for i, d in enumerate(dates)}  # 首日后恒定 0.2% 偏移
    r = simulation_backtest_gap_check(sim, bt)
    assert r["overlap_n"] == 20
    assert r["passed"] is True
    assert r["max_gap"] == pytest.approx(0.002)


def test_gap_exceeds_limit_fails() -> None:
    """仿真与回测偏差 >5% → 不通过。"""
    dates = [f"2026-01-{d:02d}" for d in range(1, 21)]
    sim = {d: 1.0 + i * 0.001 for i, d in enumerate(dates)}
    bt = {d: 1.0 + i * 0.01 for i, d in enumerate(dates)}  # 放大 10 倍斜率
    r = simulation_backtest_gap_check(sim, bt)
    assert r["passed"] is False
    assert r["max_gap"] > 0.05


def test_different_initial_capital_normalized() -> None:
    """不同初始资金但收益相同 → 归一化后 gap=0。"""
    dates = [f"2026-01-{d:02d}" for d in range(1, 11)]
    sim = {d: 200000.0 * (1.0 + i * 0.001) for i, d in enumerate(dates)}  # 初始 20 万
    bt = {d: 100000.0 * (1.0 + i * 0.001) for i, d in enumerate(dates)}  # 初始 10 万
    r = simulation_backtest_gap_check(sim, bt)
    assert r["passed"] is True
    assert r["max_gap"] == pytest.approx(0.0)


def test_insufficient_overlap_safe() -> None:
    """重叠期不足 → 不判通过且不崩溃。"""
    sim = {f"2026-01-{d:02d}": 1.0 for d in range(1, 4)}
    bt = {f"2026-01-{d:02d}": 1.0 for d in range(1, 20)}
    r = simulation_backtest_gap_check(sim, bt)
    assert r["passed"] is False
    assert r["overlap_n"] < 5


def test_list_input_supported() -> None:
    """[(date, equity)] 序列输入同样支持。"""
    dates = [f"2026-01-{d:02d}" for d in range(1, 11)]
    sim = [(d, 1.0 + i * 0.001) for i, d in enumerate(dates)]
    bt = [(d, 1.0 + i * 0.001) for i, d in enumerate(dates)]
    r = simulation_backtest_gap_check(sim, bt)
    assert r["passed"] is True
    assert r["max_gap"] == pytest.approx(0.0)


def test_empty_curve_safe() -> None:
    """空曲线 → 不崩溃。"""
    r = simulation_backtest_gap_check({}, {"2026-01-01": 1.0})
    assert r["passed"] is False


def test_final_gap_reported() -> None:
    """最终偏差字段正确。"""
    dates = [f"2026-01-{d:02d}" for d in range(1, 8)]
    sim = {d: 1.0 + i * 0.001 for i, d in enumerate(dates)}
    bt = {d: 1.0 + i * 0.001 for i, d in enumerate(dates)}
    bt[dates[-1]] += 0.02
    r = simulation_backtest_gap_check(sim, bt)
    assert r["final_gap"] == pytest.approx(0.02)


def test_nan_equity_ignored() -> None:
    """NaN 净值按字符串保留比较（不引入异常）。"""
    dates = [f"2026-01-{d:02d}" for d in range(1, 8)]
    sim = {d: 1.0 + i * 0.001 for i, d in enumerate(dates)}
    bt = {d: 1.0 + i * 0.001 for i, d in enumerate(dates)}
    bt["2026-01-03"] = float("nan")  # 仅测试不崩溃，NaN 参与比较不影响通过
    r = simulation_backtest_gap_check(sim, bt)
    assert isinstance(r["passed"], bool)
