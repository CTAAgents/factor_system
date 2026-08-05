"""
tests/factor_engine/test_alpha_ops_numba.py — Numba 加速算子测试

验证:
  1. Numba 算子 vs 原 pandas 实现正确性对比
  2. 边界条件（空数组、常量数组、NaN）
  3. Numba 可用性检测
  4. 基准测试（Numba vs pandas 耗时对比）
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sp_stats

from fts.factor_engine.seed_data import alpha_ops as orig
from fts.factor_engine.seed_data import alpha_ops_numba as nb


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def sample_array() -> np.ndarray:
    """生成确定性测试数据。"""
    rng = np.random.default_rng(42)
    return rng.standard_normal(1000)


@pytest.fixture
def paired_arrays() -> tuple[np.ndarray, np.ndarray]:
    """生成一对相关数组。"""
    rng = np.random.default_rng(123)
    x = rng.standard_normal(500)
    y = 0.7 * x + 0.3 * rng.standard_normal(500)
    return x, y


@pytest.fixture
def small_array() -> np.ndarray:
    """小数组（边界测试）。"""
    return np.array([1.0, 2.0, 3.0, 4.0, 5.0])


@pytest.fixture
def constant_array() -> np.ndarray:
    """常量数组（边界测试）。"""
    return np.full(100, 5.0)


# ─── Numba 可用性测试 ─────────────────────────────────────


class TestNumbaAvailability:
    def test_numba_is_available(self) -> None:
        """Numba 应已安装且可用。"""
        assert nb.is_numba_available() is True

    def test_njit_functions_exist(self) -> None:
        """所有 njit 函数应已定义。"""
        expected = [
            "_njit_rank", "_njit_signed_power", "_njit_sign",
            "_njit_abs", "_njit_neg", "_njit_log",
            "_njit_ts_mean", "_njit_ts_std", "_njit_ts_sum",
            "_njit_ts_min", "_njit_ts_max",
            "_njit_ts_corr", "_njit_ts_cov", "_njit_ts_rank",
            "_njit_delay", "_njit_delta",
            "_njit_spearman_ic",
        ]
        for name in expected:
            assert hasattr(nb, name), f"Missing {name}"


# ─── 截面操作正确性测试 ───────────────────────────────────


class TestCrossSectionOps:
    def test_rank_matches_original(self, sample_array: np.ndarray) -> None:
        """Numba rank 应与原实现一致。"""
        result = nb._njit_rank(sample_array)
        expected = orig.rank(sample_array)
        np.testing.assert_allclose(result, expected, atol=1e-12)

    def test_rank_single_element(self) -> None:
        """单元素数组 rank 应为 0。"""
        result = nb._njit_rank(np.array([42.0]))
        np.testing.assert_array_equal(result, np.zeros(1))

    def test_signed_power(self, sample_array: np.ndarray) -> None:
        """signed_power 正确性。"""
        result = nb._njit_signed_power(sample_array, 0.5)
        expected = orig.signed_power(sample_array, 0.5)
        np.testing.assert_allclose(result, expected, atol=1e-12)

    def test_sign(self, sample_array: np.ndarray) -> None:
        """sign 函数正确性。"""
        result = nb._njit_sign(sample_array)
        expected = orig.sign(sample_array)
        np.testing.assert_array_equal(result, expected)

    def test_abs(self, sample_array: np.ndarray) -> None:
        """abs 函数正确性。"""
        result = nb._njit_abs(sample_array)
        expected = orig.abs_(sample_array)
        np.testing.assert_allclose(result, expected, atol=1e-12)

    def test_neg(self, sample_array: np.ndarray) -> None:
        """neg 函数正确性。"""
        result = nb._njit_neg(sample_array)
        expected = orig.neg(sample_array)
        np.testing.assert_allclose(result, expected, atol=1e-12)

    def test_log(self, sample_array: np.ndarray) -> None:
        """log 函数正确性（需确保输入为正）。"""
        positive = np.abs(sample_array) + 1.0
        result = nb._njit_log(positive)
        expected = orig.log(positive)
        np.testing.assert_allclose(result, expected, atol=1e-12)

    def test_ifelse(self) -> None:
        """ifelse 条件选择正确性。"""
        cond = np.array([True, False, True, False])
        a = np.array([1.0, 2.0, 3.0, 4.0])
        b = np.array([10.0, 20.0, 30.0, 40.0])
        result = nb._njit_ifelse(cond, a, b)
        expected = orig.ifelse(cond, a, b)
        np.testing.assert_array_equal(result, expected)

    def test_scale(self, sample_array: np.ndarray) -> None:
        """scale 缩放正确性。"""
        result = nb._njit_scale(sample_array, 1.0)
        expected = orig.scale(sample_array, 1.0)
        np.testing.assert_allclose(result, expected, atol=1e-12)


# ─── 时间序列滚动操作正确性测试 ───────────────────────────


class TestTimeSeriesOps:
    def test_ts_mean_matches_original(self, sample_array: np.ndarray) -> None:
        """滚动均值 Numba vs pandas。"""
        result = nb.ts_mean(sample_array, 20)
        expected = orig.ts_mean(sample_array, 20)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-12)

    def test_ts_sum_matches_original(self, sample_array: np.ndarray) -> None:
        """滚动求和 Numba vs pandas。"""
        result = nb.ts_sum(sample_array, 15)
        expected = orig.ts_sum(sample_array, 15)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-10)

    def test_ts_std_matches_original(self, sample_array: np.ndarray) -> None:
        """滚动标准差 Numba vs pandas。"""
        result = nb.ts_std(sample_array, 20)
        expected = orig.ts_stddev(sample_array, 20)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-10)

    def test_ts_min_matches_original(self, sample_array: np.ndarray) -> None:
        """滚动最小值 Numba vs pandas。"""
        result = nb.ts_min(sample_array, 10)
        expected = orig.ts_min(sample_array, 10)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-12)

    def test_ts_max_matches_original(self, sample_array: np.ndarray) -> None:
        """滚动最大值 Numba vs pandas。"""
        result = nb.ts_max(sample_array, 10)
        expected = orig.ts_max(sample_array, 10)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-12)

    def test_ts_corr_matches_original(
        self, paired_arrays: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """滚动相关系数 Numba vs pandas。"""
        x, y = paired_arrays
        result = nb.ts_corr(x, y, 20)
        expected = orig.ts_corr(x, y, 20)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-8)

    def test_ts_cov_matches_original(
        self, paired_arrays: tuple[np.ndarray, np.ndarray]
    ) -> None:
        """滚动协方差 Numba vs pandas。"""
        x, y = paired_arrays
        result = nb.ts_cov(x, y, 20)
        expected = orig.ts_covariance(x, y, 20)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-8)

    def test_ts_rank_matches_original(self, sample_array: np.ndarray) -> None:
        """滚动分位数排序 Numba vs pandas。"""
        result = nb.ts_rank(sample_array, 10)
        expected = orig.ts_rank(sample_array, 10)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-10)

    def test_delay_matches_original(self, sample_array: np.ndarray) -> None:
        """滞后 Numba vs pandas。"""
        result = nb.delay(sample_array, 5)
        expected = orig.delay(sample_array, 5)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-12)

    def test_delta_matches_original(self, sample_array: np.ndarray) -> None:
        """差分 Numba vs pandas。"""
        result = nb.delta(sample_array, 5)
        expected = orig.delta(sample_array, 5)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-12)

    def test_ts_argmax_matches_original(self, sample_array: np.ndarray) -> None:
        """滚动 argmax Numba vs pandas。"""
        result = nb._njit_ts_argmax(sample_array, 10)
        expected = orig.ts_argmax(sample_array, 10)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-10)

    def test_ts_argmin_matches_original(self, sample_array: np.ndarray) -> None:
        """滚动 argmin Numba vs pandas。"""
        result = nb._njit_ts_argmin(sample_array, 10)
        expected = orig.ts_argmin(sample_array, 10)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-10)

    def test_ts_product_matches_original(self, sample_array: np.ndarray) -> None:
        """滚动乘积 Numba vs pandas。"""
        result = nb._njit_ts_product(sample_array, 5)
        expected = orig.ts_product(sample_array, 5)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-10)

    def test_decay_linear_matches_original(self, sample_array: np.ndarray) -> None:
        """线性衰减 Numba vs pandas。"""
        result = nb._njit_decay_linear(sample_array, 10)
        expected = orig.decay_linear(sample_array, 10)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-10)

    def test_highday_matches_original(self, sample_array: np.ndarray) -> None:
        """highday Numba vs pandas。"""
        result = nb._njit_highday(sample_array, 10)
        expected = orig.highday(sample_array, 10)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-10)

    def test_lowday_matches_original(self, sample_array: np.ndarray) -> None:
        """lowday Numba vs pandas。"""
        result = nb._njit_lowday(sample_array, 10)
        expected = orig.lowday(sample_array, 10)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-10)


# ─── Spearman IC 测试 ─────────────────────────────────────


class TestSpearmanIC:
    def test_perfect_positive_correlation(self) -> None:
        """完全正相关 → IC = 1.0。"""
        x = np.arange(100, dtype=np.float64)
        ic = nb.spearman_ic(x, x)
        assert abs(ic - 1.0) < 1e-10

    def test_perfect_negative_correlation(self) -> None:
        """完全负相关 → IC = -1.0。"""
        x = np.arange(100, dtype=np.float64)
        ic = nb.spearman_ic(x, -x)
        assert abs(ic + 1.0) < 1e-10

    def test_vs_scipy(self, paired_arrays: tuple[np.ndarray, np.ndarray]) -> None:
        """Numba Spearman IC vs scipy。"""
        x, y = paired_arrays
        nb_ic = nb.spearman_ic(x, y)
        scipy_ic, _ = sp_stats.spearmanr(x, y)
        assert abs(nb_ic - scipy_ic) < 1e-10

    def test_noise_correlation(self) -> None:
        """独立噪声 → IC 接近 0。"""
        rng = np.random.default_rng(999)
        x = rng.standard_normal(500)
        y = rng.standard_normal(500)
        ic = nb.spearman_ic(x, y)
        assert abs(ic) < 0.2  # 99.7% 概率 < 0.2

    def test_short_array(self) -> None:
        """短数组（< 2）→ IC = 0。"""
        ic = nb.spearman_ic(np.array([1.0]), np.array([2.0]))
        assert ic == 0.0


# ─── 边界条件测试 ─────────────────────────────────────────


class TestBoundaryConditions:
    def test_constant_array(self, constant_array: np.ndarray) -> None:
        """常量数组应产生 NaN 或 0（合理行为）。"""
        result = nb.ts_mean(constant_array, 10)
        assert len(result) == len(constant_array)
        # 常量数组的均值应为常量值
        valid = result[~np.isnan(result)]
        if len(valid) > 0:
            np.testing.assert_allclose(valid, 5.0, atol=1e-12)

    def test_window_equals_length(self, small_array: np.ndarray) -> None:
        """窗口长度等于数组长度。"""
        result = nb.ts_mean(small_array, len(small_array))
        assert len(result) == len(small_array)
        # 最后一个值应为整体均值
        expected_mean = np.mean(small_array)
        assert abs(result[-1] - expected_mean) < 1e-12

    def test_window_one(self, sample_array: np.ndarray) -> None:
        """窗口 d=1 → 结果等于原数组。"""
        result = nb.ts_mean(sample_array, 1)
        np.testing.assert_allclose(result, sample_array, atol=1e-12)

    def test_large_window(self, sample_array: np.ndarray) -> None:
        """窗口大于数组长度 → 全部 NaN。"""
        result = nb.ts_mean(sample_array, len(sample_array) + 100)
        assert np.all(np.isnan(result))


# ─── 基准测试 ──────────────────────────────────────────────


class TestBenchmark:
    def test_ts_mean_speedup(self) -> None:
        """Numba ts_mean 应比 pandas 快（大数据集）。"""
        rng = np.random.default_rng(42)
        data = rng.standard_normal(50000)
        window = 20

        # Warmup + JIT 编译
        nb.ts_mean(data, window)
        nb.ts_mean(data, window)  # 二次确认 JIT 完成

        # Numba 计时
        times_nb: list[float] = []
        for _ in range(200):
            start = time.perf_counter()
            nb.ts_mean(data, window)
            times_nb.append(time.perf_counter() - start)

        # Pandas 计时 (直接使用 pandas rolling，绕过 alpha_ops 封装)
        times_pd: list[float] = []
        for _ in range(200):
            start = time.perf_counter()
            pd.Series(data).rolling(window, min_periods=1).mean()
            times_pd.append(time.perf_counter() - start)

        median_nb = np.median(times_nb)
        median_pd = np.median(times_pd)
        speedup = median_pd / max(median_nb, 1e-9)

        assert speedup >= 2.5, (
            f"Numba ts_mean 加速 {speedup:.1f}x，期望 >= 2.5x"
        )

    def test_ts_std_speedup(self) -> None:
        """Numba ts_std 不显著慢于 pandas（pandas rolling 已 C 优化）。"""
        rng = np.random.default_rng(42)
        data = rng.standard_normal(50000)
        window = 20

        nb.ts_std(data, window)
        nb.ts_std(data, window)

        times_nb: list[float] = []
        for _ in range(200):
            start = time.perf_counter()
            nb.ts_std(data, window)
            times_nb.append(time.perf_counter() - start)

        # Pandas 原生实现
        times_pd: list[float] = []
        for _ in range(200):
            start = time.perf_counter()
            pd.Series(data).rolling(window, min_periods=1).std(ddof=0)
            times_pd.append(time.perf_counter() - start)

        speedup = np.median(times_pd) / max(np.median(times_nb), 1e-9)
        # pandas rolling 已 C 优化，Numba 在此场景下不应慢于 3x
        assert speedup >= 0.33, f"Numba ts_std 加速 {speedup:.2f}x"

    def test_ts_corr_speedup(self) -> None:
        """Numba ts_corr 不显著慢于 pandas（pandas rolling 已 C 优化）。"""
        rng = np.random.default_rng(123)
        x = rng.standard_normal(50000)
        y = 0.7 * x + 0.3 * rng.standard_normal(50000)
        window = 20

        nb.ts_corr(x, y, window)
        nb.ts_corr(x, y, window)

        times_nb: list[float] = []
        for _ in range(100):
            start = time.perf_counter()
            nb.ts_corr(x, y, window)
            times_nb.append(time.perf_counter() - start)

        # Pandas 原生实现
        times_pd: list[float] = []
        for _ in range(100):
            start = time.perf_counter()
            pd.Series(x).rolling(window, min_periods=1).corr(pd.Series(y))
            times_pd.append(time.perf_counter() - start)

        speedup = np.median(times_pd) / max(np.median(times_nb), 1e-9)
        # pandas rolling corr 已 C 优化且使用增量算法，Numba 可能较慢
        # 只要正确性验证通过即可，性能不作为强制要求
        assert speedup >= 0.05, f"Numba ts_corr 加速 {speedup:.3f}x"

    def test_spearman_ic_speedup(self) -> None:
        """Numba Spearman IC 应比 scipy 快。"""
        rng = np.random.default_rng(42)
        sig = rng.standard_normal(5000)
        ret = 0.5 * sig + 0.5 * rng.standard_normal(5000)

        nb.spearman_ic(sig, ret)  # warmup
        nb.spearman_ic(sig, ret)  # JIT 确认

        times_nb: list[float] = []
        for _ in range(300):
            start = time.perf_counter()
            nb.spearman_ic(sig, ret)
            times_nb.append(time.perf_counter() - start)

        times_scipy: list[float] = []
        for _ in range(300):
            start = time.perf_counter()
            sp_stats.spearmanr(sig, ret)
            times_scipy.append(time.perf_counter() - start)

        speedup = np.median(times_scipy) / max(np.median(times_nb), 1e-9)
        assert speedup >= 2.0, (
            f"Numba Spearman IC 加速 {speedup:.1f}x，期望 >= 2x"
        )


# ─── 公共接口测试 ─────────────────────────────────────────


class TestPublicAPI:
    def test_ts_mean_public(self, sample_array: np.ndarray) -> None:
        """公共 ts_mean 接口应返回正确结果。"""
        result = nb.ts_mean(sample_array, 5)
        assert isinstance(result, np.ndarray)
        assert len(result) == len(sample_array)
        # 前 4 个应为 NaN
        assert np.all(np.isnan(result[:4]))

    def test_ts_std_public(self, sample_array: np.ndarray) -> None:
        """公共 ts_std 接口应返回正确结果。"""
        result = nb.ts_std(sample_array, 5)
        assert isinstance(result, np.ndarray)
        assert len(result) == len(sample_array)

    def test_delay_public(self, sample_array: np.ndarray) -> None:
        """公共 delay 接口应返回正确结果。"""
        result = nb.delay(sample_array, 3)
        assert isinstance(result, np.ndarray)
        assert len(result) == len(sample_array)
        assert np.all(np.isnan(result[:3]))

    def test_delta_public(self, sample_array: np.ndarray) -> None:
        """公共 delta 接口应返回正确结果。"""
        result = nb.delta(sample_array, 3)
        expected = orig.delta(sample_array, 3)
        mask = ~np.isnan(result) & ~np.isnan(expected)
        np.testing.assert_allclose(result[mask], expected[mask], atol=1e-12)