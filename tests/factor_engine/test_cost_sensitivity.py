"""tests/factor_engine/test_cost_sensitivity.py — 可交易性压力层测试（GAP-061）。

HARNESS §测试随重构: 覆盖成功路径 / 边界 / 降级路径。
"""

from __future__ import annotations

import json

import numpy as np

from fts.factor_engine.cost_sensitivity import (
    CostSensitivityResult,
    run_cost_sensitivity,
    run_slippage_stress,
    DEFAULT_SLIPPAGE_MULTS,
)


def _predictive_series(n: int = 400, alpha: float = 0.02, seed: int = 11) -> tuple[np.ndarray, np.ndarray]:
    """信号直接驱动未来收益（alpha 越大预测性越强）。

    Returns:
        (signal, close)
    """
    rng = np.random.default_rng(seed)
    signal = rng.normal(0, 1, n)
    rets = np.zeros(n)
    rets[1:] = alpha * signal[:-1] + rng.normal(0, 0.005, n - 1)
    close = 100 * np.exp(np.cumsum(rets))
    return signal, close


# ─── 成功路径 ─────────────────────────────────────────────


def test_stress_result_shape():
    """各滑点倍数净夏普/净IC字段齐全，倍数集合一致。"""
    signal, close = _predictive_series()
    result = run_slippage_stress(signal, close)
    assert result is not None
    assert result.slippage_mults == list(DEFAULT_SLIPPAGE_MULTS)
    assert set(result.net_sharpe_by_mult) == {1.0, 2.0, 4.0, 8.0}
    assert set(result.net_ic_by_mult) == {1.0, 2.0, 4.0, 8.0}
    assert set(result.total_cost_bps_by_mult) == {1.0, 2.0, 4.0, 8.0}


def test_net_sharpe_decreases_with_slippage():
    """净夏普随滑点倍数单调不增，且低于毛夏普。"""
    signal, close = _predictive_series()
    result = run_slippage_stress(signal, close)
    assert result is not None
    assert result.gross_sharpe > 0
    sharpe_by_mult = [result.net_sharpe_by_mult[m] for m in DEFAULT_SLIPPAGE_MULTS]
    assert all(sharpe_by_mult[i] >= sharpe_by_mult[i + 1] - 1e-9 for i in range(len(sharpe_by_mult) - 1))
    assert sharpe_by_mult[-1] <= result.gross_sharpe


def test_weak_signal_breakeven_identified():
    """弱信号 + 高滑点：净夏普转负，breakeven_mult 落在倍数区间内。"""
    signal, close = _predictive_series(alpha=0.003)  # 弱预测性
    result = run_slippage_stress(signal, close, mults=(1.0, 2.0, 4.0, 8.0, 16.0))
    assert result is not None
    assert result.net_sharpe_by_mult[16.0] < 0  # 16 倍滑点下净夏普为负
    assert result.breakeven_mult is not None
    assert 1.0 < result.breakeven_mult <= 16.0
    assert result.positive_at_max_stress is False


def test_strong_signal_survives_stress():
    """强信号在最大倍数下滑点下净夏普仍为正。"""
    signal, close = _predictive_series(alpha=0.05)
    result = run_slippage_stress(signal, close)
    assert result is not None
    assert result.positive_at_max_stress is True
    assert result.net_sharpe_by_mult[8.0] > 0


def test_market_param_affects_cost():
    """不同市场基础成本不同：futures 默认滑点低于 stock。"""
    signal, close = _predictive_series(alpha=0.01)
    r_fut = run_slippage_stress(signal, close, market="futures")
    r_stk = run_slippage_stress(signal, close, market="stock")
    assert r_fut is not None and r_stk is not None
    assert r_fut.total_cost_bps_by_mult[1.0] <= r_stk.total_cost_bps_by_mult[1.0] + 1e-9


def test_to_dict_serializable():
    """to_dict 可 JSON 序列化。"""
    signal, close = _predictive_series()
    result = run_slippage_stress(signal, close)
    assert result is not None
    json.dumps(result.to_dict())


def test_cost_sensitivity_alias():
    """run_cost_sensitivity 与 run_slippage_stress 结果一致。"""
    signal, close = _predictive_series()
    r1 = run_slippage_stress(signal, close)
    r2 = run_cost_sensitivity(signal, close)
    assert r1 is not None and r2 is not None
    assert r1.to_dict() == r2.to_dict()


def test_deterministic():
    """同输入两次运行完全一致。"""
    signal, close = _predictive_series(seed=5)
    r1 = run_slippage_stress(signal, close)
    r2 = run_slippage_stress(signal, close)
    assert r1 is not None and r2 is not None
    assert r1.to_dict() == r2.to_dict()


# ─── 边界与降级路径 ───────────────────────────────────────


def test_short_sample_returns_none():
    """样本不足返回 None。"""
    signal, close = _predictive_series(n=20)
    assert run_slippage_stress(signal, close) is None


def test_constant_signal_no_crash():
    """常数信号不崩溃，返回结果或 None。"""
    n = 300
    close = 100 * np.exp(np.cumsum(np.random.default_rng(1).normal(0, 0.01, n)))
    signal = np.ones(n) * 3.0
    result = run_slippage_stress(signal, close)
    assert result is None or isinstance(result, CostSensitivityResult)


def test_nan_signal_no_crash():
    """含 NaN 信号不崩溃。"""
    signal, close = _predictive_series()
    signal[::4] = np.nan
    result = run_slippage_stress(signal, close)
    assert result is None or isinstance(result, CostSensitivityResult)


def test_empty_mults_returns_result_with_empty_dicts():
    """空倍数列表返回空映射结果（不崩溃）。"""
    signal, close = _predictive_series()
    result = run_slippage_stress(signal, close, mults=())
    assert result is not None
    assert result.net_sharpe_by_mult == {}
