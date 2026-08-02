"""tests/factor_engine/test_alpha_ops.py — 因子原始操作函数测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.seed_data.alpha_ops import (
    _to_series,
    _to_array,
    rank,
    scale,
    ifelse,
    ts_sum,
    ts_mean,
    ts_stddev,
    ts_corr,
    ts_covariance,
    ts_argmax,
    ts_argmin,
    ts_rank,
    ts_min,
    ts_max,
    ts_product,
    signed_power,
    decay_linear,
    delta,
    delay,
    log,
    sign,
    abs_,
    neg,
    highday,
    lowday,
)


# ─── _to_series ────────────────────────────────────────────

class TestToSeries:
    def test_ndarray_input(self):
        x = np.array([1.0, 2.0, 3.0])
        result = _to_series(x)
        assert isinstance(result, pd.Series)
        assert (result.values == x).all()

    def test_series_input(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = _to_series(s)
        assert result is s  # 原样返回


# ─── _to_array ─────────────────────────────────────────────

class TestToArray:
    def test_series_input(self):
        s = pd.Series([1.0, 2.0, 3.0])
        result = _to_array(s)
        assert isinstance(result, np.ndarray)
        assert (result == [1.0, 2.0, 3.0]).all()

    def test_ndarray_input(self):
        x = np.array([1.0, 2.0, 3.0])
        result = _to_array(x)
        assert isinstance(result, np.ndarray)
        assert result is x or (result == x).all()


# ─── rank ──────────────────────────────────────────────────

class TestRank:
    def test_normal(self):
        x = np.array([3.0, 1.0, 2.0, 4.0])
        result = rank(x)
        # argsort(x) = [1, 2, 0, 3] → argsort of that = [2, 0, 1, 3]
        # / (4-1) = /3
        expected = np.array([2 / 3, 0 / 3, 1 / 3, 3 / 3])
        assert np.allclose(result, expected)

    def test_n_equals_1(self):
        x = np.array([5.0])
        result = rank(x)
        assert result == [0.0]

    def test_n_equals_0(self):
        x = np.array([])
        result = rank(x)
        assert len(result) == 0

    def test_all_same(self):
        x = np.array([2.0, 2.0, 2.0])
        result = rank(x)
        # argsort(argsort(...)) for equal values = [0, 1, 2] / 2
        expected = np.array([0.0, 0.5, 1.0])
        assert np.allclose(result, expected)


# ─── scale ─────────────────────────────────────────────────

class TestScale:
    def test_normal(self):
        x = np.array([1.0, 2.0, 3.0])
        result = scale(x)
        # sum(abs(x)) = 6, a = 1.0
        expected = np.array([1.0 / 6, 2.0 / 6, 3.0 / 6])
        assert np.allclose(result, expected)

    def test_custom_a(self):
        x = np.array([1.0, -2.0, 3.0])
        result = scale(x, a=2.0)
        # sum(abs(x)) = 6, a = 2.0
        expected = np.array([2.0 / 6, -4.0 / 6, 6.0 / 6])
        assert np.allclose(result, expected)

    def test_sum_zero(self):
        x = np.array([0.0, 0.0, 0.0])
        result = scale(x)
        assert np.allclose(result, x)

    def test_negative_values(self):
        x = np.array([-1.0, -2.0, -3.0])
        result = scale(x)
        expected = np.array([-1.0 / 6, -2.0 / 6, -3.0 / 6])
        assert np.allclose(result, expected)


# ─── ifelse ────────────────────────────────────────────────

class TestIfelse:
    def test_array_condition(self):
        cond = np.array([True, False, True])
        a = np.array([1.0, 2.0, 3.0])
        b = np.array([10.0, 20.0, 30.0])
        result = ifelse(cond, a, b)
        expected = np.array([1.0, 20.0, 3.0])
        assert np.allclose(result, expected)

    def test_scalar_values(self):
        cond = np.array([True, False, True])
        result = ifelse(cond, 1.0, 0.0)
        expected = np.array([1.0, 0.0, 1.0])
        assert np.allclose(result, expected)

    def test_all_true(self):
        cond = np.array([True, True, True])
        a = np.array([1.0, 2.0, 3.0])
        result = ifelse(cond, a, 0.0)
        assert np.allclose(result, a)

    def test_all_false(self):
        cond = np.array([False, False, False])
        b = np.array([10.0, 20.0, 30.0])
        result = ifelse(cond, 0.0, b)
        assert np.allclose(result, b)


# ─── ts_sum ────────────────────────────────────────────────

class TestTsSum:
    def test_normal(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ts_sum(x, 3)
        # 前 2 个用 min_periods=1 所以是累计和
        expected = np.array([1.0, 3.0, 6.0, 9.0, 12.0])
        assert np.allclose(result, expected)

    def test_d_equals_1(self):
        x = np.array([1.0, 2.0, 3.0])
        result = ts_sum(x, 1)
        assert np.allclose(result, x)


# ─── ts_mean ───────────────────────────────────────────────

class TestTsMean:
    def test_normal(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ts_mean(x, 3)
        expected = np.array([1.0, 1.5, 2.0, 3.0, 4.0])  # [1, (1+2)/2, (1+2+3)/3, (2+3+4)/3, (3+4+5)/3]
        assert np.allclose(result, expected)

    def test_d_equals_1(self):
        x = np.array([1.0, 2.0, 3.0])
        result = ts_mean(x, 1)
        assert np.allclose(result, x)


# ─── ts_stddev ─────────────────────────────────────────────

class TestTsStddev:
    def test_normal(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ts_stddev(x, 3)
        # rolling std with ddof=0 (population std)
        # [1] → 0, [1,2] → 0.5, [1,2,3] → 0.8165, [2,3,4] → 0.8165, [3,4,5] → 0.8165
        assert np.allclose(result[:1], [0.0])
        assert np.allclose(result[1], np.std([1.0, 2.0]))
        for i in range(2, 5):
            assert np.allclose(result[i], np.std(x[i - 2:i + 1]))

    def test_constant_values(self):
        x = np.array([5.0, 5.0, 5.0, 5.0])
        result = ts_stddev(x, 3)
        assert np.allclose(result, [0.0, 0.0, 0.0, 0.0])


# ─── ts_corr ───────────────────────────────────────────────

class TestTsCorr:
    def test_perfect_correlation(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
        result = ts_corr(x, y, 3)
        # 对于完全线性相关，rolling 3 天相关系数应为 1
        for i in range(2, 5):
            assert np.allclose(result[i], 1.0)

    def test_negative_correlation(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        result = ts_corr(x, y, 3)
        for i in range(2, 5):
            assert np.allclose(result[i], -1.0)

    def test_d_equals_1(self):
        x = np.array([1.0, 2.0, 3.0])
        y = np.array([4.0, 5.0, 6.0])
        result = ts_corr(x, y, 1)
        # 单元素相关系数应为 NaN
        assert np.isnan(result[0]) or np.allclose(result[0], np.nan)
        assert np.isnan(result[1]) or np.allclose(result[1], np.nan)
        assert np.isnan(result[2]) or np.allclose(result[2], np.nan)


# ─── ts_covariance ─────────────────────────────────────────

class TestTsCovariance:
    def test_normal(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([2.0, 4.0, 6.0, 8.0, 10.0])
        result = ts_covariance(x, y, 3)
        # 正相关方差为正
        for i in range(2, 5):
            assert result[i] > 0

    def test_constant_values(self):
        x = np.array([3.0, 3.0, 3.0, 3.0])
        y = np.array([1.0, 2.0, 3.0, 4.0])
        result = ts_covariance(x, y, 3)
        # 第 1 个只有 1 个样本，协方差为 NaN；后 3 个窗口 x 为常数 → 协方差为 0
        assert np.isnan(result[0])
        assert np.allclose(result[1], 0.0)
        assert np.allclose(result[2], 0.0)
        assert np.allclose(result[3], 0.0)


# ─── ts_argmax ─────────────────────────────────────────────

class TestTsArgmax:
    def test_normal(self):
        x = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
        result = ts_argmax(x, 3)
        # [1] → 0, [1,3] → 1, [1,3,2] → 1, [3,2,5] → 2, [2,5,4] → 1
        assert result[0] == 0
        assert result[1] == 1
        assert result[2] == 1
        assert result[3] == 2
        assert result[4] == 1

    def test_decreasing(self):
        x = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        result = ts_argmax(x, 3)
        # 最大值都在第一个位置
        assert result[0] == 0
        assert result[1] == 0
        assert result[2] == 0
        assert result[3] == 0
        assert result[4] == 0


# ─── ts_argmin ─────────────────────────────────────────────

class TestTsArgmin:
    def test_normal(self):
        x = np.array([3.0, 1.0, 4.0, 2.0, 5.0])
        result = ts_argmin(x, 3)
        # [3] → 0, [3,1] → 1, [3,1,4] → 1, [1,4,2] → 0, [4,2,5] → 1
        assert result[0] == 0
        assert result[1] == 1
        assert result[2] == 1
        assert result[3] == 0
        assert result[4] == 1

    def test_increasing(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = ts_argmin(x, 3)
        # 最小值都在第一个位置
        assert result[0] == 0
        assert result[1] == 0
        assert result[2] == 0
        assert result[3] == 0
        assert result[4] == 0


# ─── ts_rank ───────────────────────────────────────────────

class TestTsRank:
    def test_normal(self):
        x = np.array([3.0, 1.0, 2.0, 4.0, 5.0])
        result = ts_rank(x, 3)
        # [3] → 0.5, [3,1] → last=0/(2-1)=0.0, [3,1,2] → last=1/(3-1)=0.5
        # [1,2,4] → last=2/2=1.0, [2,4,5] → last=2/2=1.0
        assert np.allclose(result[0], 0.5)
        assert np.allclose(result[1], 0.0)
        assert np.allclose(result[2], 0.5)
        assert np.allclose(result[3], 1.0)
        assert np.allclose(result[4], 1.0)

    def test_len_leq_1(self):
        # 窗口大小为 1，每个窗口都只有 1 个元素
        x = np.array([1.0, 2.0, 3.0])
        result = ts_rank(x, 1)
        # len(v) <= 1 → 0.5
        assert np.allclose(result, [0.5, 0.5, 0.5])

    def test_d_larger_than_array(self):
        x = np.array([1.0, 2.0])
        result = ts_rank(x, 5)
        # 窗口 5 但只有 2 个元素，min_periods=1 所以用全部
        assert np.allclose(result, [0.5, 1.0])


# ─── ts_min ────────────────────────────────────────────────

class TestTsMin:
    def test_normal(self):
        x = np.array([3.0, 1.0, 2.0, 5.0, 4.0])
        result = ts_min(x, 3)
        expected = np.array([3.0, 1.0, 1.0, 1.0, 2.0])
        assert np.allclose(result, expected)

    def test_d_equals_1(self):
        x = np.array([3.0, 1.0, 2.0])
        result = ts_min(x, 1)
        assert np.allclose(result, x)


# ─── ts_max ────────────────────────────────────────────────

class TestTsMax:
    def test_normal(self):
        x = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
        result = ts_max(x, 3)
        expected = np.array([1.0, 3.0, 3.0, 5.0, 5.0])
        assert np.allclose(result, expected)

    def test_d_equals_1(self):
        x = np.array([1.0, 3.0, 2.0])
        result = ts_max(x, 1)
        assert np.allclose(result, x)


# ─── ts_product ────────────────────────────────────────────

class TestTsProduct:
    def test_normal(self):
        x = np.array([2.0, 3.0, 4.0, 5.0])
        result = ts_product(x, 3)
        # [2] → 2, [2,3] → 6, [2,3,4] → 24, [3,4,5] → 60
        expected = np.array([2.0, 6.0, 24.0, 60.0])
        assert np.allclose(result, expected)

    def test_d_equals_1(self):
        x = np.array([2.0, 3.0, 4.0])
        result = ts_product(x, 1)
        assert np.allclose(result, x)


# ─── signed_power ──────────────────────────────────────────

class TestSignedPower:
    def test_positive_exponent(self):
        x = np.array([-2.0, 0.0, 3.0])
        result = signed_power(x, 2.0)
        # sign(x) * |x|^2 = [-4, 0, 9]
        expected = np.array([-4.0, 0.0, 9.0])
        assert np.allclose(result, expected)

    def test_fractional_exponent(self):
        x = np.array([4.0, 9.0, -4.0])
        result = signed_power(x, 0.5)
        # sign(x) * sqrt(|x|)
        expected = np.array([2.0, 3.0, -2.0])
        assert np.allclose(result, expected)

    def test_zero_exponent(self):
        x = np.array([-2.0, 0.0, 2.0])
        result = signed_power(x, 0.0)
        # sign(x) * |x|^0 = sign(x) * 1 = [ -1, 0, 1 ]
        expected = np.array([-1.0, 0.0, 1.0])
        assert np.allclose(result, expected)


# ─── decay_linear ──────────────────────────────────────────

class TestDecayLinear:
    def test_normal(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = decay_linear(x, 3)
        # d=3, w = [1,2,3], sum=6, w_norm = [1/6, 2/6, 3/6]
        # 前 2 个 min_periods=d=3 所以 nan
        # idx 2: sum([1,2,3] * [1/6,2/6,3/6]) = (1+4+9)/6 = 14/6 ≈ 2.333
        # idx 3: sum([2,3,4] * [1/6,2/6,3/6]) = (2+6+12)/6 = 20/6 ≈ 3.333
        # idx 4: sum([3,4,5] * [1/6,2/6,3/6]) = (3+8+15)/6 = 26/6 ≈ 4.333
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert np.allclose(result[2], 14.0 / 6)
        assert np.allclose(result[3], 20.0 / 6)
        assert np.allclose(result[4], 26.0 / 6)

    def test_len_less_than_d(self):
        # len(v) < d 时返回 nan
        x = np.array([1.0, 2.0])
        result = decay_linear(x, 10)
        assert np.isnan(result[0])
        assert np.isnan(result[1])


# ─── delta ─────────────────────────────────────────────────

class TestDelta:
    def test_normal(self):
        x = np.array([1.0, 3.0, 6.0, 10.0, 15.0])
        result = delta(x, 1)
        # x - delay(x, 1)
        expected = np.array([np.nan, 2.0, 3.0, 4.0, 5.0])
        assert np.isnan(result[0])
        assert np.allclose(result[1:], expected[1:])

    def test_d_equals_2(self):
        x = np.array([1.0, 3.0, 6.0, 10.0, 15.0])
        result = delta(x, 2)
        expected = np.array([np.nan, np.nan, 5.0, 7.0, 9.0])
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert np.allclose(result[2:], expected[2:])


# ─── delay ─────────────────────────────────────────────────

class TestDelay:
    def test_normal(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = delay(x, 1)
        expected = np.array([np.nan, 1.0, 2.0, 3.0, 4.0])
        assert np.isnan(result[0])
        assert np.allclose(result[1:], expected[1:])

    def test_d_equals_2(self):
        x = np.array([1.0, 2.0, 3.0, 4.0])
        result = delay(x, 2)
        expected = np.array([np.nan, np.nan, 1.0, 2.0])
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert np.allclose(result[2:], expected[2:])


# ─── log ───────────────────────────────────────────────────

class TestLog:
    def test_positive_values(self):
        x = np.array([1.0, np.e, np.e ** 2])
        result = log(x)
        expected = np.array([0.0, 1.0, 2.0])
        assert np.allclose(result, expected)

    def test_zero_and_negative(self):
        x = np.array([0.0, -1.0, 5.0])
        result = log(x)
        # log(max(0, _EPS)) = log(1e-10) ≈ -23.0258
        # log(max(-1, _EPS)) = log(1e-10) ≈ -23.0258
        # log(5) ≈ 1.6094
        assert np.allclose(result[0], np.log(1e-10))
        assert np.allclose(result[1], np.log(1e-10))
        assert np.allclose(result[2], np.log(5.0))


# ─── sign ──────────────────────────────────────────────────

class TestSign:
    def test_positive_negative_zero(self):
        x = np.array([-3.0, 0.0, 5.0])
        result = sign(x)
        expected = np.array([-1.0, 0.0, 1.0])
        assert np.allclose(result, expected)

    def test_all_positive(self):
        x = np.array([1.0, 2.0, 3.0])
        result = sign(x)
        assert np.allclose(result, [1.0, 1.0, 1.0])

    def test_all_negative(self):
        x = np.array([-1.0, -2.0, -3.0])
        result = sign(x)
        assert np.allclose(result, [-1.0, -1.0, -1.0])


# ─── abs_ ──────────────────────────────────────────────────

class TestAbs:
    def test_mixed(self):
        x = np.array([-3.0, 0.0, 5.0])
        result = abs_(x)
        expected = np.array([3.0, 0.0, 5.0])
        assert np.allclose(result, expected)

    def test_all_negative(self):
        x = np.array([-1.0, -2.0, -3.0])
        result = abs_(x)
        assert np.allclose(result, [1.0, 2.0, 3.0])


# ─── neg ───────────────────────────────────────────────────

class TestNeg:
    def test_mixed(self):
        x = np.array([-3.0, 0.0, 5.0])
        result = neg(x)
        expected = np.array([3.0, 0.0, -5.0])
        assert np.allclose(result, expected)

    def test_all_positive(self):
        x = np.array([1.0, 2.0, 3.0])
        result = neg(x)
        assert np.allclose(result, [-1.0, -2.0, -3.0])


# ─── highday ───────────────────────────────────────────────

class TestHighday:
    def test_normal(self):
        x = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
        result = highday(x, 3)
        # [1] → 0, [1,3] → 0, [1,3,2] → 1 (= 3-1-argmax), [3,2,5] → 0, [2,5,4] → 1
        assert result[0] == 0
        assert result[1] == 0
        assert result[2] == 1
        assert result[3] == 0
        assert result[4] == 1

    def test_len_leq_1(self):
        x = np.array([5.0])
        result = highday(x, 5)
        assert result[0] == 0

    def test_monotonic_increasing(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = highday(x, 3)
        # 最大值在窗口末尾，距离为 0
        assert result[0] == 0
        assert result[1] == 0
        assert result[2] == 0
        assert result[3] == 0
        assert result[4] == 0


# ─── lowday ────────────────────────────────────────────────

class TestLowday:
    def test_normal(self):
        x = np.array([3.0, 1.0, 2.0, 5.0, 4.0])
        result = lowday(x, 3)
        # [3] → 0, [3,1] → 0, [3,1,2] → 1, [1,2,5] → 2, [2,5,4] → 2
        assert result[0] == 0
        assert result[1] == 0
        assert result[2] == 1
        assert result[3] == 2
        assert result[4] == 2

    def test_len_leq_1(self):
        x = np.array([5.0])
        result = lowday(x, 5)
        assert result[0] == 0

    def test_monotonic_decreasing(self):
        x = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        result = lowday(x, 3)
        # 最小值在窗口末尾，距离为 0
        assert result[0] == 0
        assert result[1] == 0
        assert result[2] == 0
        assert result[3] == 0
        assert result[4] == 0