"""tests/factor_engine/test_robustness_g56.py — G5 Bootstrap / G6 平稳性检验测试（35-gap-closure-plan §4.2/4.3）。

HARNESS §测试随重构。
"""

from __future__ import annotations

import numpy as np

from fts.factor_engine.robustness import bootstrap_ic_ci, check_stationarity


# ─── G5 Bootstrap 自助抽样 ─────────────────────────────────


def test_bootstrap_passes_strong_predictive():
    """强正相关信号 → IC 95%CI 下界 >0 → passed。"""
    n = 400
    x = np.linspace(0, 1, n) + np.random.default_rng(1).normal(0, 0.05, n)
    y = x * 2 + np.random.default_rng(2).normal(0, 0.2, n)
    res = bootstrap_ic_ci(x, y, n_bootstrap=100, seed=42)
    assert res["n_bootstrap"] >= 30
    assert res["ci_lower"] > 0
    assert res["passed"] is True


def test_bootstrap_rejects_noise():
    """随机信号 → IC≈0，CI 跨 0 → 不通过。"""
    rng = np.random.default_rng(7)
    x = rng.normal(size=400)
    y = rng.normal(size=400)
    res = bootstrap_ic_ci(x, y, n_bootstrap=100, seed=42)
    assert res["passed"] is False


def test_bootstrap_reproducible():
    """固定 seed → 两次结果一致（可复现）。"""
    rng = np.random.default_rng(3)
    x = rng.normal(size=300)
    y = x + rng.normal(size=300)
    r1 = bootstrap_ic_ci(x, y, n_bootstrap=80, seed=42)
    r2 = bootstrap_ic_ci(x, y, n_bootstrap=80, seed=42)
    assert r1["ci_lower"] == r2["ci_lower"]
    assert r1["ci_upper"] == r2["ci_upper"]
    assert r1["passed"] == r2["passed"]


def test_bootstrap_short_sample_rejected():
    """样本不足（<2 块）→ passed=False。"""
    rng = np.random.default_rng(0)
    x = rng.normal(size=30)
    y = rng.normal(size=30)
    res = bootstrap_ic_ci(x, y, n_bootstrap=50, block_size=20, seed=42)
    assert res["passed"] is False
    assert res["n_bootstrap"] == 0


def test_bootstrap_nan_safe():
    """NaN 值剔除，不崩溃。"""
    rng = np.random.default_rng(5)
    x = rng.normal(size=300)
    y = x + rng.normal(size=300)
    x[::7] = np.nan
    y[::11] = np.nan
    res = bootstrap_ic_ci(x, y, n_bootstrap=80, seed=42)
    assert "passed" in res


# ─── G6 分布平稳性检验 ────────────────────────────────────


def test_stationarity_white_noise_passes():
    """白噪声收益序列 → ADF 平稳 → passed。"""
    rng = np.random.default_rng(11)
    rets = rng.normal(0, 1, 500)
    res = check_stationarity(rets)
    assert res["passed"] is True


def test_stationarity_drift_rejected():
    """均值漂移序列（前+后-）→ 漂移比高 → 不通过。"""
    rng = np.random.default_rng(13)
    rets = np.concatenate([rng.normal(1.0, 1.0, 250), rng.normal(-1.0, 1.0, 250)])
    res = check_stationarity(rets)
    assert res["drift_ratio"] > 0.2
    assert res["passed"] is False


def test_stationarity_no_adf_uses_drift():
    """use_adf=False → 仅漂移比判定（白噪声通过 / 漂移拒绝）。"""
    rng = np.random.default_rng(17)
    wn = rng.normal(0, 1, 500)
    assert check_stationarity(wn, use_adf=False)["passed"] is True
    drift = np.concatenate([rng.normal(2.0, 1.0, 250), rng.normal(-2.0, 1.0, 250)])
    assert check_stationarity(drift, use_adf=False)["passed"] is False


def test_stationarity_short_sample_rejected():
    """样本 <20 → 不通过。"""
    res = check_stationarity(np.array([0.1, -0.1, 0.05] * 4))
    assert res["passed"] is False


def test_stationarity_nan_safe():
    """NaN 剔除后判定，不崩溃。"""
    rng = np.random.default_rng(19)
    rets = rng.normal(0, 1, 400)
    rets[::13] = np.nan
    res = check_stationarity(rets)
    assert "passed" in res
