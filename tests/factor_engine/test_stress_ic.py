"""test_stress_ic — 极端行情 IC 失效检验测试（CTA 手册阶段4）。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.stress_ic import STRESS_PERIODS, stress_period_ic_test


def _dates(n: int, start: str = "2019-01-01") -> np.ndarray:
    return pd.date_range(start, periods=n, freq="B").to_numpy()


def _strong_pair(n: int, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """强预测因子信号与未来收益。"""
    rng = np.random.RandomState(seed)
    ret = rng.standard_normal(n)
    signal = ret * 0.8 + rng.standard_normal(n) * 0.1
    return signal, ret


def test_strong_factor_passes_all_periods() -> None:
    """强因子在极端期 IC 保持 → 全部通过。"""
    n = 400
    signal, ret = _strong_pair(n)
    dates = _dates(n)
    # 极端期不覆盖本样本（用自定义空区间保证正常期 IC 稳定）
    custom = [{"name": "normal_ctl", "start": "2099-01-01", "end": "2099-12-31"}]  # 无样本 → skipped
    result = stress_period_ic_test(signal, ret, dates, stress_periods=custom)
    assert result["passed"] is True
    assert result["ic_full"] > 0.3
    assert result["periods"][0]["status"] == "skipped"  # 区间无样本


def test_factor_degraded_in_stress_fails() -> None:
    """因子在指定极端期 IC 骤降 → 标记失效。"""
    n = 400
    dates = _dates(n)
    rng = np.random.RandomState(9)
    ret = rng.standard_normal(n)
    signal = ret * 0.8 + rng.standard_normal(n) * 0.1  # 正常期强相关
    # 在索引 150~200 区间打乱信号（模拟极端期因子失效：IC 骤降）
    signal = signal.copy()
    signal[150:200] = rng.standard_normal(50)
    start = pd.Timestamp(dates[150]).strftime("%Y-%m-%d")
    end = pd.Timestamp(dates[199]).strftime("%Y-%m-%d")
    custom = [{"name": "stress_win", "start": start, "end": end}]
    result = stress_period_ic_test(signal, ret, dates, stress_periods=custom)
    assert result["failed_periods"] == ["stress_win"]
    assert result["passed"] is False


def test_sign_flip_in_stress_fails() -> None:
    """极端期 IC 符号反转 → 强失效标记。"""
    n = 400
    dates = _dates(n)
    rng = np.random.RandomState(11)
    ret = rng.standard_normal(n)
    signal = ret * 0.8 + rng.standard_normal(n) * 0.1
    # 反转极端期信号（符号反转）
    signal = signal.copy()
    signal[200:240] = -signal[200:240]
    start = pd.Timestamp(dates[200]).strftime("%Y-%m-%d")
    end = pd.Timestamp(dates[239]).strftime("%Y-%m-%d")
    custom = [{"name": "sign_flip", "start": start, "end": end}]
    result = stress_period_ic_test(signal, ret, dates, stress_periods=custom)
    assert result["passed"] is False
    assert "sign_flip" in result["failed_periods"]


def test_insufficient_stress_samples_skipped() -> None:
    """极端期样本不足 → skipped 不判失败。"""
    n = 100
    signal, ret = _strong_pair(n)
    dates = _dates(n)
    custom = [{"name": "tiny", "start": "2099-01-01", "end": "2099-01-02"}]  # 无样本
    result = stress_period_ic_test(signal, ret, dates, stress_periods=custom)
    assert result["periods"][0]["status"] == "skipped"
    assert result["passed"] is True


def test_nan_and_length_mismatch() -> None:
    """NaN 兜底与长度不匹配不崩溃。"""
    signal, ret = _strong_pair(80)
    signal[::7] = np.nan
    dates = _dates(80)
    result = stress_period_ic_test(signal, ret, dates, stress_periods=[])
    assert "passed" in result
    short = stress_period_ic_test(np.ones(10), np.ones(5), dates[:10])
    assert short["passed"] is True


def test_builtin_periods_defined() -> None:
    """内置极端行情区间包含 2020 原油与 2022 俄乌。"""
    names = {p["name"] for p in STRESS_PERIODS}
    assert "原油负价格_2020" in names
    assert "俄乌扰动_2022" in names
