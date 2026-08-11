"""tests/factor_engine/test_ic_weight.py — IC 协方差加权合成测试（GAP-064）。

覆盖: ic_covariance_weights 单元 + synthesize_signals ic_weight 模式接线。
HARNESS §测试随重构。
"""

from __future__ import annotations

import numpy as np
import pytest

from fts.factor_engine.weight_learning import ic_covariance_weights


def _ic_matrix(n_samples: int = 60, n_factors: int = 3, seed: int = 17) -> np.ndarray:
    """T×N IC 矩阵：因子均值 IC 从 0.08 线性递减。"""
    rng = np.random.default_rng(seed)
    means = np.linspace(0.08, -0.01, n_factors)
    return means[None, :] + rng.normal(0, 0.05, (n_samples, n_factors))


# ─── ic_covariance_weights 单元 ───────────────────────────


def test_weights_normalized():
    """权重 Σ|w| = 1 且形状为 N。"""
    icm = _ic_matrix()
    w = ic_covariance_weights(icm)
    assert w is not None
    assert w.shape == (3,)
    assert np.sum(np.abs(w)) == pytest.approx(1.0)


def test_high_ic_factor_gets_larger_weight():
    """高均值 IC 因子获得更大 |权重|。"""
    icm = _ic_matrix()
    w = ic_covariance_weights(icm)
    assert w is not None
    assert abs(w[0]) > abs(w[1]) >= abs(w[2])


def test_insufficient_samples_returns_none():
    """样本不足 min_samples 返回 None。"""
    icm = _ic_matrix(n_samples=10)
    assert ic_covariance_weights(icm, min_samples=20) is None


def test_single_factor_returns_none():
    """单因子（N=1）无法估计协方差，返回 None。"""
    icm = _ic_matrix(n_factors=1)
    assert ic_covariance_weights(icm) is None


def test_nan_rows_dropped():
    """含 NaN 行被剔除后仍可计算。"""
    icm = _ic_matrix(n_samples=60)
    icm[::5, 1] = np.nan  # 部分行含 NaN
    w = ic_covariance_weights(icm)
    assert w is not None
    assert np.sum(np.abs(w)) == pytest.approx(1.0)


def test_singular_protected():
    """完全共线（奇异）IC 序列经正则化仍返回有限权重。"""
    rng = np.random.default_rng(3)
    base = rng.normal(0, 0.05, (60, 1))
    icm = np.hstack([base, base, base * 2])  # 完全共线
    w = ic_covariance_weights(icm)
    assert w is not None
    assert np.all(np.isfinite(w))


def test_deterministic():
    """同输入两次结果一致。"""
    icm = _ic_matrix(seed=11)
    w1 = ic_covariance_weights(icm)
    w2 = ic_covariance_weights(icm)
    assert w1 is not None and w2 is not None
    np.testing.assert_allclose(w1, w2)


# ─── synthesize_signals ic_weight 模式 ────────────────────


def _factors(n: int = 3) -> list[dict]:
    return [
        {"factor_id": f"fct_{i:03d}", "name": f"f{i}", "sharpe": 2.0 - i * 0.3, "ic": ic, "turnover": 0.3, "decay_6m": 0.1}
        for i, ic in enumerate([0.08, 0.04, 0.02])
    ]


def test_synthesize_ic_weight_fallback():
    """无 ic_matrix 时回退 IC 均值加权（w ∝ |ic|）。"""
    from fts.factor_engine.portfolio_loop import synthesize_signals

    signals, _, _ = synthesize_signals(_factors(), mode="ic_weight")
    assert len(signals) == 3
    weights = [s["weight"] for s in signals]
    assert weights[0] > weights[1] > weights[2]
    assert sum(weights) == pytest.approx(1.0)


def test_synthesize_ic_weight_with_matrix():
    """提供 ic_matrix 时使用 IC 协方差加权。"""
    from fts.factor_engine.portfolio_loop import synthesize_signals

    icm = _ic_matrix()
    signals, _, _ = synthesize_signals(_factors(), mode="ic_weight", ic_matrix=icm)
    assert len(signals) == 3
    assert all(np.isfinite(s["weight"]) for s in signals)


def test_synthesize_ic_weight_empty_factors():
    """空因子列表返回空。"""
    from fts.factor_engine.portfolio_loop import synthesize_signals

    assert synthesize_signals([], mode="ic_weight") == ([], 0.0, 0.0)
