"""tests/factor_engine/test_horizon_analysis.py — 多持有期 IC 体系测试（GAP-060）。

HARNESS §测试随重构: 覆盖成功路径 / 边界 / 降级路径。
"""

from __future__ import annotations

import numpy as np
import pytest

from fts.factor_engine.horizon_analysis import (
    HorizonAnalysisResult,
    compute_ic_decay_curve,
    compute_multi_horizon_ic,
    select_best_horizon,
    DEFAULT_HORIZONS,
)


def _trending_series(n: int = 300, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """构造信号直接驱动未来收益的确定性序列（保证 IC 为正且随持有期衰减）。

    Returns:
        (signal, close)
    """
    rng = np.random.default_rng(seed)
    signal = rng.normal(0, 1, n)
    rets = np.zeros(n)
    rets[1:] = 0.02 * signal[:-1] + rng.normal(0, 0.005, n - 1)
    close = 100 * np.exp(np.cumsum(rets))
    return signal, close


# ─── 成功路径 ─────────────────────────────────────────────


def test_multi_horizon_result_shape():
    """各持有期 IC/ICIR/胜率/衰减曲线字段齐全，best_horizon 在 horizons 内。"""
    signal, close = _trending_series()
    result = compute_multi_horizon_ic(signal, close, horizons=(1, 5, 10, 20))
    assert result is not None
    assert result.horizons == [1, 5, 10, 20]
    assert set(result.ic_by_horizon) == {1, 5, 10, 20}
    assert set(result.icir_by_horizon) == {1, 5, 10, 20}
    assert set(result.win_rate_by_horizon) == {1, 5, 10, 20}
    assert result.best_horizon in {1, 5, 10, 20}
    assert set(result.decay_curve) == {1, 5, 10, 20}
    assert result.decay_curve[1] == pytest.approx(1.0)


def test_predictive_signal_positive_ic():
    """趋势延续信号在短持有期 IC 为正。"""
    signal, close = _trending_series()
    result = compute_multi_horizon_ic(signal, close, horizons=(1, 5))
    assert result is not None
    assert result.ic_by_horizon[1] > 0
    assert result.ic_by_horizon[5] > 0


def test_best_horizon_picks_max_icir():
    """best_horizon 为 |ICIR| 最大者。"""
    signal, close = _trending_series()
    result = compute_multi_horizon_ic(signal, close, horizons=(1, 5, 10, 20))
    assert result is not None
    best = result.best_horizon
    best_icir = abs(result.icir_by_horizon[best])
    for h in result.horizons:
        assert abs(result.icir_by_horizon[h]) <= best_icir + 1e-9


def test_decay_curve_normalized_and_monotonic():
    """衰减曲线相对第 1 持有期归一化；趋势衰减型数据判定 monotonic_decay。"""
    signal, close = _trending_series()
    result = compute_multi_horizon_ic(signal, close, horizons=(1, 5, 10, 20))
    assert result is not None
    curve = compute_ic_decay_curve(result)
    assert curve[1] == pytest.approx(1.0)
    assert all(0.0 <= v <= 1.0 + 1e-9 for v in curve.values())


# ─── 边界与降级路径 ───────────────────────────────────────


def test_constant_signal_returns_none_or_zero():
    """常数信号无区分度：要么 None，要么 IC 全为 0。"""
    n = 200
    close = 100 * np.exp(np.cumsum(np.random.default_rng(3).normal(0, 0.01, n)))
    signal = np.ones(n) * 5.0
    result = compute_multi_horizon_ic(signal, close, horizons=(1, 5))
    assert result is None or all(abs(v) < 1e-6 for v in result.ic_by_horizon.values())


def test_short_sample_returns_none():
    """样本数不足 min_samples 返回 None。"""
    signal, close = _trending_series(n=20)
    result = compute_multi_horizon_ic(signal, close, horizons=(1, 5), min_samples=30)
    assert result is None


def test_nan_signal_handled():
    """含 NaN 信号不崩溃，结果可用或 None。"""
    signal, close = _trending_series()
    signal[::3] = np.nan
    result = compute_multi_horizon_ic(signal, close, horizons=(1, 5))
    assert result is None or isinstance(result, HorizonAnalysisResult)


def test_invalid_horizons_returns_none():
    """全部无效持有期（<=0）返回 None。"""
    signal, close = _trending_series()
    assert compute_multi_horizon_ic(signal, close, horizons=(0, -1)) is None


def test_to_dict_serializable():
    """to_dict 输出可 JSON 序列化的 dict 结构。"""
    import json

    signal, close = _trending_series()
    result = compute_multi_horizon_ic(signal, close, horizons=(1, 5, 10))
    assert result is not None
    d = result.to_dict()
    json.dumps(d)
    assert d["best_horizon"] in {1, 5, 10}
    assert len(d["ic_by_horizon"]) == 3


def test_deterministic_reproducible():
    """同输入两次计算完全一致。"""
    signal, close = _trending_series(seed=42)
    r1 = compute_multi_horizon_ic(signal, close, horizons=(1, 5, 10))
    r2 = compute_multi_horizon_ic(signal, close, horizons=(1, 5, 10))
    assert r1 is not None and r2 is not None
    assert r1.to_dict() == r2.to_dict()


def test_select_best_horizon_matches():
    """select_best_horizon 与 result.best_horizon 一致。"""
    signal, close = _trending_series()
    result = compute_multi_horizon_ic(signal, close, horizons=(1, 5, 10, 20))
    assert result is not None
    assert select_best_horizon(result) == result.best_horizon


def test_default_horizons_exported():
    """DEFAULT_HORIZONS 为 4 个持有期且递增。"""
    assert len(DEFAULT_HORIZONS) == 4
    assert list(DEFAULT_HORIZONS) == sorted(DEFAULT_HORIZONS)
