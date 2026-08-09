"""tests/factor_engine/test_standardizer.py — 因子标准化模块测试。

覆盖:
    1. StandardizerConfig 契约默认值
    2. Standardizer 六种方法 fit/transform/fit_transform 全路径
    3. standardize 便捷函数
    4. 内部辅助函数（NaN 跳过 / 零分母兜底 / 边界）
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.standardizer import (  # noqa: E402
    SUPPORTED_METHODS,
    Standardizer,
    StandardizerConfig,
    standardize,
    _apply_minmax,
    _apply_quantile,
    _apply_rank,
    _apply_winsorize_zscore,
    _apply_zscore,
    _nanmax,
    _nanmean,
    _nanmin,
    _nanpercentile,
    _nanstd,
)


# ─── 常量与配置 ────────────────────────────────────────────


class TestConstantsAndConfig:
    def test_supported_methods_complete(self):
        assert set(SUPPORTED_METHODS) == {
            "zscore", "rank", "quantile", "minmax", "winsorize_then_zscore", "none",
        }

    def test_config_defaults(self):
        cfg = StandardizerConfig()
        assert cfg.method == "zscore"
        assert cfg.clip == 3.0
        assert cfg.winsorize_lower == 0.01
        assert cfg.winsorize_upper == 0.99
        assert cfg.axis == 0
        assert cfg.skipna is True


# ─── Standardizer ──────────────────────────────────────────


class TestStandardizerInit:
    def test_invalid_method_raises(self):
        with pytest.raises(ValueError, match="不支持的标准化方法"):
            Standardizer("not_a_method")

    def test_kwargs_passed_to_config(self):
        std = Standardizer("zscore", clip=2.0, axis=1, skipna=False)
        assert std.config.clip == 2.0
        assert std.config.axis == 1
        assert std.config.skipna is False

    def test_properties(self):
        std = Standardizer("minmax")
        assert std.config.method == "minmax"
        assert std.method == "minmax"
        assert std.is_fitted is False


class TestFit:
    def test_fit_zscore_computes_mean_std(self):
        std = Standardizer("zscore")
        data = np.array([1.0, 2.0, 3.0, 4.0])
        std.fit(data)
        assert std.is_fitted is True
        np.testing.assert_allclose(std._params["mean"], np.array([2.5]))
        assert std._params["std"] > 0

    def test_fit_none_sets_empty_params(self):
        std = Standardizer("none")
        std.fit(np.array([1.0, 2.0]))
        assert std._params == {}

    def test_fit_rank_quantile_empty_params(self):
        for m in ("rank", "quantile"):
            std = Standardizer(m)
            std.fit(np.array([1.0, 2.0, 3.0]))
            assert std._params == {}

    def test_fit_minmax_computes_bounds(self):
        std = Standardizer("minmax")
        std.fit(np.array([1.0, 5.0, 9.0]))
        assert std._params["min"] == 1.0
        assert std._params["max"] == 9.0

    def test_fit_winsorize_computes_percentiles(self):
        std = Standardizer("winsorize_then_zscore", winsorize_lower=0.1, winsorize_upper=0.9)
        std.fit(np.arange(11, dtype=float))
        assert std._params["lower"] == pytest.approx(1.0)
        assert std._params["upper"] == pytest.approx(9.0)
        assert std._params["mean"] is None
        assert std._params["std"] is None

    def test_fit_skipna_false_path(self):
        std = Standardizer("zscore", skipna=False)
        std.fit(np.array([1.0, np.nan, 3.0]))
        assert std._fitted is True


class TestTransform:
    def test_transform_none_returns_copy(self):
        std = Standardizer("none")
        data = np.array([1.0, 2.0])
        out = std.transform(data)
        np.testing.assert_array_equal(out, data)
        assert out is not data

    def test_transform_zscore_requires_fit(self):
        # 未 fit 时自动用数据自身参数（mean/std 为 None → 就地计算）
        std = Standardizer("zscore")
        data = np.array([1.0, 2.0, 3.0, 4.0])
        out = std.transform(data)
        expected = (data - 2.5) / np.std(data)
        np.testing.assert_allclose(out, expected)

    def test_transform_zscore_clip(self):
        std = Standardizer("zscore", clip=1.0)
        data = np.array([0.0, 100.0, 200.0, 300.0])
        out = std.transform(data)
        assert out.max() <= 1.0
        assert out.min() >= -1.0

    def test_transform_zscore_nan_position_zeroed(self):
        std = Standardizer("zscore")
        data = np.array([1.0, np.nan, 3.0])
        out = std.transform(data)
        assert out[1] == 0.0  # NaN 位置置零
        assert np.isfinite(out).all()

    def test_transform_rank_1d(self):
        std = Standardizer("rank")
        out = std.transform(np.array([5.0, 1.0, 3.0]))
        # 排序: 1→1/3, 3→2/3, 5→3/3
        np.testing.assert_allclose(out, [1.0, 1 / 3, 2 / 3])

    def test_transform_rank_all_nan_zeros(self):
        std = Standardizer("rank")
        out = std.transform(np.array([np.nan, np.nan]))
        np.testing.assert_array_equal(out, np.zeros(2))

    def test_transform_rank_2d_axis0(self):
        std = Standardizer("rank", axis=0)
        data = np.array([[3.0, 1.0], [1.0, 3.0], [2.0, 2.0]])
        out = std.transform(data)
        assert out.shape == data.shape
        # 每列独立 rank: 列0 = [3,1,2] → [1, 1/3, 2/3]
        np.testing.assert_allclose(out[:, 0], [1.0, 1 / 3, 2 / 3])

    def test_transform_rank_2d_axis1(self):
        std = Standardizer("rank", axis=1)
        data = np.array([[3.0, 1.0, 2.0]])
        out = std.transform(data)
        np.testing.assert_allclose(out[0], [1.0, 1 / 3, 2 / 3])

    def test_transform_rank_2d_axis_none(self):
        std = Standardizer("rank", axis=None)
        data = np.array([[3.0, 1.0], [2.0, 4.0]])
        out = std.transform(data)
        # 展平 [3,1,2,4] → rank [3/4, 1/4, 2/4, 1]
        np.testing.assert_allclose(out.ravel(), [3 / 4, 1 / 4, 2 / 4, 1.0])

    def test_transform_quantile_1d(self):
        std = Standardizer("quantile")
        out = std.transform(np.array([5.0, 1.0, 3.0]))
        # order/(n-1): 排序 1→0/2, 3→1/2, 5→2/2
        np.testing.assert_allclose(out, [1.0, 0.0, 0.5])

    def test_transform_quantile_single_element(self):
        std = Standardizer("quantile")
        out = std.transform(np.array([7.0]))
        assert out[0] == 0.0  # max(n-1, 1) = 1 → 0/1

    def test_transform_quantile_2d(self):
        std = Standardizer("quantile", axis=0)
        data = np.array([[5.0, 1.0], [1.0, 5.0]])
        out = std.transform(data)
        np.testing.assert_allclose(out[:, 0], [1.0, 0.0])

    def test_transform_minmax(self):
        std = Standardizer("minmax")
        out = std.transform(np.array([1.0, 3.0, 5.0]))
        np.testing.assert_allclose(out, [0.0, 0.5, 1.0])

    def test_transform_minmax_clips_out_of_range(self):
        std = Standardizer("minmax")
        out = std.transform(np.array([-5.0, 1.0, 3.0, 5.0, 99.0]))
        assert out.min() >= 0.0
        assert out.max() <= 1.0

    def test_transform_winsorize_zscore(self):
        std = Standardizer("winsorize_then_zscore", winsorize_lower=0.1, winsorize_upper=0.9)
        data = np.arange(11, dtype=float)
        out = std.transform(data)
        assert out.shape == data.shape
        assert np.isfinite(out).all()

    def test_transform_unknown_method_raises(self):
        std = Standardizer("none")
        std._config.method = "bogus"  # type: ignore[assignment]
        with pytest.raises(ValueError, match="未知标准化方法"):
            std.transform(np.array([1.0]))


class TestFitTransformAndHelper:
    def test_fit_transform_zscore(self):
        std = Standardizer("zscore")
        data = np.array([1.0, 2.0, 3.0, 4.0])
        out = std.fit_transform(data)
        assert std.is_fitted is True
        assert out.shape == data.shape

    def test_standardize_convenience(self):
        data = np.array([1.0, 2.0, 3.0, 4.0])
        out = standardize(data, "zscore")
        expected = (data - 2.5) / np.std(data)
        np.testing.assert_allclose(out, expected)

    def test_standardize_invalid_method(self):
        with pytest.raises(ValueError):
            standardize(np.array([1.0]), "nope")

    def test_standardize_all_methods_ran(self):
        data = np.array([1.0, 2.0, 3.0, np.nan, 5.0])
        for m in SUPPORTED_METHODS:
            out = standardize(data, m)
            assert out.shape == data.shape

    # ── 内部辅助函数 ──

    def test_nan_stats_skipna_variants(self):
        data = np.array([1.0, np.nan, 3.0])
        assert _nanmean(data, skipna=True) == 2.0
        assert np.isnan(_nanmean(data, skipna=False))
        assert _nanstd(data, skipna=True) == pytest.approx(1.0)
        assert _nanmin(data, skipna=True) == 1.0
        assert _nanmax(data, skipna=True) == 3.0
        assert _nanpercentile(data, 50, skipna=True) == 2.0

    def test_apply_zscore_zero_std_guard(self):
        data = np.array([5.0, 5.0, 5.0])
        out = _apply_zscore(data, None, None)
        assert np.isfinite(out).all()  # std=0 → 兜底 1.0

    def test_apply_rank_axis_none_2d(self):
        data = np.array([[3.0, 1.0], [2.0, 4.0]])
        out = _apply_rank(data, axis=None)
        np.testing.assert_allclose(out.ravel(), [3 / 4, 1 / 4, 2 / 4, 1.0])

    def test_apply_rank_skipna_false(self):
        data = np.array([2.0, np.nan, 1.0])
        out = _apply_rank(data, axis=None, skipna=False)
        # NaN 参与 rank → argsort(NaN) 结果 undefined，但应不崩溃
        assert out.shape == data.shape

    def test_apply_quantile_all_nan(self):
        out = _apply_quantile(np.array([np.nan, np.nan]), axis=None)
        np.testing.assert_array_equal(out, np.zeros(2))

    def test_apply_minmax_zero_denom_guard(self):
        data = np.array([3.0, 3.0, 3.0])
        out = _apply_minmax(data, None, None)
        np.testing.assert_array_equal(out, np.zeros(3))  # (3-3)/1 = 0

    def test_apply_minmax_explicit_bounds(self):
        data = np.array([2.0, 4.0, 6.0])
        out = _apply_minmax(data, 0.0, 10.0)
        np.testing.assert_allclose(out, [0.2, 0.4, 0.6])

    def test_apply_winsorize_zscore_default_percentiles(self):
        data = np.arange(101, dtype=float)
        out = _apply_winsorize_zscore(data, None, None, clip=None)
        assert out.shape == data.shape
        assert np.isfinite(out).all()
        # 缩尾后范围应受控
        assert out.min() >= -3.5
        assert out.max() <= 3.5
