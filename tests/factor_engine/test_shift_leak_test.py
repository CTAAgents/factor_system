"""test_shift_leak_test — Shift 错位泄漏校验测试（CTA 手册阶段2）。"""

from __future__ import annotations

import numpy as np

from fts.factor_engine.shift_leak_test import shift_leak_test


def _ar1(n: int, phi: float, seed: int = 7) -> np.ndarray:
    """AR(1) 序列（自相关收益，用于泄漏反例构造）。"""
    rng = np.random.RandomState(seed)
    eps = rng.standard_normal(n)
    x = np.empty(n)
    x[0] = eps[0]
    for t in range(1, n):
        x[t] = phi * x[t - 1] + eps[t]
    return x


def test_normal_factor_passes() -> None:
    """正常因子：滞后后 IC 衰减 → 通过。"""
    rng = np.random.RandomState(1)
    n = 200
    fwd = _ar1(n, phi=0.5)
    signal = 0.5 * fwd + 0.5 * rng.standard_normal(n)  # 含未来收益信息的真实预测因子
    result = shift_leak_test(signal, fwd)
    assert result["passed"] is True
    assert result["ic0"] > 0.2
    # 滞后 1 期 IC 应明显小于原始 IC（衰减）
    assert abs(result["ic_by_shift"][1]) < abs(result["ic0"]) * 0.9


def test_leaking_factor_fails() -> None:
    """未来泄漏因子：signal 直接等于未来收益（强自相关下滞后仍有效）→ 不通过。"""
    fwd = _ar1(300, phi=0.95, seed=3)  # 强自相关 AR(1)
    signal = fwd  # 信号=未来收益（泄漏）
    result = shift_leak_test(signal, fwd, threshold=0.9)
    assert result["ic0"] > 0.9  # 自身相关
    assert result["leak_shifts"]  # 滞后 1 期 IC 接近原始 → 泄漏
    assert result["passed"] is False


def test_zero_predictive_power_not_leaky() -> None:
    """无预测能力（IC≈0）→ 不判泄漏。"""
    rng = np.random.RandomState(2)
    signal = rng.standard_normal(100)
    fwd = rng.standard_normal(100)
    result = shift_leak_test(signal, fwd)
    assert abs(result["ic0"]) < 0.15
    assert result["passed"] is True


def test_nan_handling() -> None:
    """含 NaN → 剔除后正常计算。"""
    rng = np.random.RandomState(5)
    n = 150
    fwd = _ar1(n, phi=0.5)
    signal = 0.5 * fwd + 0.5 * rng.standard_normal(n)
    signal[::7] = np.nan
    result = shift_leak_test(signal, fwd)
    assert "passed" in result


def test_length_mismatch() -> None:
    """长度不匹配 → 通过（无泄漏判定，不崩溃）。"""
    result = shift_leak_test(np.ones(10), np.ones(5))
    assert result["passed"] is True


def test_custom_threshold() -> None:
    """自定义阈值：更宽松（1.0）→ 几乎不判泄漏。"""
    rng = np.random.RandomState(6)
    n = 200
    fwd = _ar1(n, phi=0.5)
    signal = 0.5 * fwd + 0.5 * rng.standard_normal(n)
    result = shift_leak_test(signal, fwd, threshold=1.0)
    assert result["passed"] is True
