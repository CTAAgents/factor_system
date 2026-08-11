"""
tests/factor_engine/test_neutralization.py — 横截面因子中性化测试（D.2 股票 L3 补齐）。

覆盖:
    - industry_neutralize: 组内去均值 / 单股行业归零 / 无映射保留 / 全 NaN 降级
    - size_neutralize: 回归残差与 log_cap 不相关 / 市值缺失保留 / 样本不足原样
    - cross_section_neutralize: 逐日行业+市值 / 映射缺失跳过 / 空映射原样
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.neutralization import (
    cross_section_neutralize,
    industry_neutralize,
    size_neutralize,
)


def _series(**vals: float) -> pd.Series:
    return pd.Series(vals, dtype=float)


class TestIndustryNeutralize:
    def test_group_mean_zero(self) -> None:
        """同行业两股：组内去均值后组均值为 0，组内差不变。"""
        sig = _series(a=1.0, b=3.0, c=5.0, d=7.0)  # a/b 同行业, c/d 同行业
        out = industry_neutralize(sig, {"a": "bank", "b": "bank", "c": "tech", "d": "tech"})
        assert out["a"] == pytest.approx(-1.0)
        assert out["b"] == pytest.approx(1.0)
        assert (out["a"] + out["b"]) == pytest.approx(0.0)
        assert out["c"] == pytest.approx(-1.0)
        assert out["d"] == pytest.approx(1.0)

    def test_single_stock_industry_zeroed(self) -> None:
        """行业组内仅 1 只：信号归零（无组内相对信息）。"""
        sig = _series(a=2.0, b=4.0)
        out = industry_neutralize(sig, {"a": "bank", "b": "tech"})
        assert out["a"] == pytest.approx(0.0)
        assert out["b"] == pytest.approx(0.0)

    def test_unmapped_symbol_kept(self) -> None:
        """无行业映射的 symbol：保留原值，不误杀。"""
        sig = _series(a=1.0, b=2.0, c=3.0)
        out = industry_neutralize(sig, {"a": "bank", "b": "bank"})
        assert out["c"] == pytest.approx(3.0)

    def test_all_nan_returns_unchanged(self) -> None:
        """全 NaN 截面：原样返回不抛错。"""
        sig = _series(a=np.nan, b=np.nan)
        out = industry_neutralize(sig, {"a": "bank", "b": "bank"})
        assert np.isnan(out["a"]) and np.isnan(out["b"])

    def test_input_not_mutated(self) -> None:
        """不修改入参 Series。"""
        sig = _series(a=1.0, b=3.0)
        industry_neutralize(sig, {"a": "bank", "b": "bank"})
        assert sig["a"] == pytest.approx(1.0)


class TestSizeNeutralize:
    def test_residual_uncorrelated_with_log_cap(self) -> None:
        """回归残差与 log 市值相关系数 ≈ 0（市值偏好剥离）。"""
        rng = np.random.default_rng(7)
        caps = rng.uniform(50.0, 5000.0, 30)
        log_caps = np.log(caps)
        sig_vals = 2.0 * log_caps + rng.normal(0, 0.5, 30)  # 信号与市值强相关
        sig = _series(**{f"s{i}": v for i, v in enumerate(sig_vals)})
        cap_map = {f"s{i}": c for i, c in enumerate(caps)}
        out = size_neutralize(sig, cap_map)
        resid = out.values
        corr = np.corrcoef(resid, log_caps)[0, 1]
        assert abs(corr) < 0.1

    def test_missing_cap_kept(self) -> None:
        """市值缺失的 symbol：保留原值。"""
        rng = np.random.default_rng(3)
        caps = rng.uniform(50.0, 5000.0, 10)
        sig = _series(**{f"s{i}": float(i) for i in range(11)})
        cap_map = {f"s{i}": c for i, c in enumerate(caps)}  # s10 缺失
        out = size_neutralize(sig, cap_map)
        assert out["s10"] == pytest.approx(10.0)

    def test_insufficient_samples_unchanged(self) -> None:
        """有效样本 < 5：不做回归，原样返回。"""
        sig = _series(a=1.0, b=2.0, c=3.0)
        out = size_neutralize(sig, {"a": 100.0, "b": 200.0, "c": 300.0})
        assert out["a"] == pytest.approx(1.0)
        assert out["b"] == pytest.approx(2.0)

    def test_constant_cap_unchanged(self) -> None:
        """市值无区分度：原样返回。"""
        sig = _series(a=1.0, b=2.0, c=3.0, d=4.0, e=5.0)
        out = size_neutralize(sig, {"a": 100.0, "b": 100.0, "c": 100.0, "d": 100.0, "e": 100.0})
        assert out["a"] == pytest.approx(1.0)


class TestCrossSectionNeutralize:
    def _panel(self) -> pd.DataFrame:
        idx = pd.DatetimeIndex(["2026-01-05", "2026-01-06"])
        return pd.DataFrame(
            {"a": [1.0, 4.0], "b": [3.0, 6.0], "c": [5.0, 8.0], "d": [2.0, 9.0]},
            index=idx,
        )

    def test_industry_applied_per_day(self) -> None:
        """逐日行业中性化：每天组内去均值。"""
        m = {"a": "bank", "b": "bank", "c": "tech", "d": "tech"}
        out = cross_section_neutralize(self._panel(), industry_map=m)
        # 2026-01-05: a/b 组均值 2 → -1/+1; c/d 组均值 3.5 → +1.5/-1.5
        assert out.loc["2026-01-05", "a"] == pytest.approx(-1.0)
        assert out.loc["2026-01-05", "b"] == pytest.approx(1.0)
        assert out.loc["2026-01-05", "c"] == pytest.approx(1.5)
        assert out.loc["2026-01-05", "d"] == pytest.approx(-1.5)

    def test_size_applied(self) -> None:
        """市值中性化后逐日残差。"""
        cap_map = {f"s{i}": float((i + 1) ** 2 * 100.0) for i in range(6)}
        panel = pd.DataFrame(
            {"s0": [1.0, 4.0], "s1": [2.0, 5.0], "s2": [3.0, 6.0],
             "s3": [4.0, 7.0], "s4": [5.0, 8.0], "s5": [6.0, 9.0]},
            index=pd.DatetimeIndex(["2026-01-05", "2026-01-06"]),
        )
        out = cross_section_neutralize(panel, cap_map=cap_map)
        # 残差之和 ≈ 0（回归含截距）
        assert abs(out.sum(axis=1).iloc[0]) < 1e-6
        assert abs(out.sum(axis=1).iloc[1]) < 1e-6

    def test_none_maps_returns_copy(self) -> None:
        """无任何映射：返回副本，数值不变。"""
        panel = self._panel()
        out = cross_section_neutralize(panel, None, None)
        assert out.equals(panel)

    def test_input_not_mutated(self) -> None:
        """不修改入参 DataFrame。"""
        panel = self._panel()
        cross_section_neutralize(panel, industry_map={"a": "bank", "b": "bank"})
        assert panel.loc["2026-01-05", "a"] == pytest.approx(1.0)

    def test_row_all_nan_skipped(self) -> None:
        """单日全 NaN：跳过不抛错。"""
        panel = pd.DataFrame(
            {"a": [np.nan, 1.0], "b": [np.nan, 2.0]},
            index=pd.DatetimeIndex(["2026-01-05", "2026-01-06"]),
        )
        out = cross_section_neutralize(panel, industry_map={"a": "x", "b": "x"})
        assert np.isnan(out.loc["2026-01-05", "a"])
        assert np.isfinite(out.loc["2026-01-06", "a"])
