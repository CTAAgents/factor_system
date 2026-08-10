"""tests/factor_engine/test_weight_learning.py — 机构级权重学习增强测试（v2.74.0）。

HARNESS §测试随重构: 覆盖 weight_learning.py 三项增强——
    ① 风险调整权重（Ledoit-Wolf 波动率缩放 / 风险平价）
    ② 滚动样本外验证（权重稳定性 / OOS IC / 权重衰减）
    ③ 学习面板市场自动匹配 + 跨市场迁移 IC 对比
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.weight_learning import (
    WeightLearningConfig,
    _fit_elasticnet_coefs,
    _risk_parity_weights,
    _spearman,
    alternate_market,
    cross_market_ic_check,
    resolve_panel_market,
    risk_adjust_from_panel,
    risk_adjust_weights,
    rolling_oos_validate,
)


# ─── 面板市场解析 ────────────────────────────────────────


class TestResolvePanelMarket:
    """学习面板市场自动匹配（③）。"""

    def test_auto_follows_futures_market(self):
        assert resolve_panel_market("auto", "futures") == "futures"

    def test_auto_follows_stock_market(self):
        assert resolve_panel_market("auto", "stock") == "stock"

    def test_auto_fallback_unknown_market(self):
        assert resolve_panel_market("auto", "crypto") == "stock"

    def test_explicit_override(self):
        assert resolve_panel_market("futures", "stock") == "futures"

    def test_invalid_falls_back(self):
        assert resolve_panel_market("unknown", "futures") == "stock"

    def test_alternate_market(self):
        assert alternate_market("stock") == "futures"
        assert alternate_market("futures") == "stock"


# ─── Spearman 工具 ───────────────────────────────────────


class TestSpearman:
    """纯 numpy Spearman 秩相关。"""

    def test_perfect_positive(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        assert _spearman(x, x) == pytest.approx(1.0, abs=1e-9)

    def test_perfect_negative(self):
        x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        y = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
        assert _spearman(x, y) == pytest.approx(-1.0, abs=1e-9)

    def test_ties_handled(self):
        x = np.array([1.0, 1.0, 2.0, 3.0])
        y = np.array([1.0, 2.0, 3.0, 4.0])
        rho = _spearman(x, y)
        assert np.isfinite(rho)

    def test_too_short_nan(self):
        assert not np.isfinite(_spearman(np.array([1.0, 2.0]), np.array([1.0, 2.0])))


# ─── ① 风险调整权重 ─────────────────────────────────────


def _factor_returns_df(factor_ids: list[str], n: int = 60) -> pd.DataFrame:
    """构造低相关因子收益矩阵（T×N）。"""
    rng = np.random.default_rng(42)
    base = rng.normal(0, 0.01, n)
    cols = {}
    for j, fid in enumerate(factor_ids):
        cols[fid] = base * (0.5 + 0.1 * j) + rng.normal(0, 0.002, n)
    return pd.DataFrame(cols)


class TestRiskAdjustWeights:
    """风险调整权重（①）。"""

    def test_volatility_scaling(self):
        fr = _factor_returns_df(["a", "b"], n=200)
        weights = {"a": 0.7, "b": 0.3}
        adjusted, meta = risk_adjust_weights(weights, fr, "volatility_scaling")
        assert set(adjusted.keys()) == {"a", "b"}
        assert sum(adjusted.values()) == pytest.approx(1.0, abs=1e-6)
        assert meta["method"] == "volatility_scaling"
        assert all(v > 0 for v in adjusted.values())

    def test_volatility_scaling_downsizes_volatile_factor(self):
        rng = np.random.default_rng(7)
        n = 200
        a = rng.normal(0, 0.005, n)
        b = rng.normal(0, 0.05, n)
        fr = pd.DataFrame({"a": a, "b": b})
        adjusted, _ = risk_adjust_weights({"a": 0.5, "b": 0.5}, fr, "volatility_scaling")
        assert adjusted["b"] < adjusted["a"]

    def test_risk_parity(self):
        fr = _factor_returns_df(["a", "b", "c"], n=300)
        weights = {"a": 0.6, "b": 0.3, "c": 0.1}
        adjusted, meta = risk_adjust_weights(weights, fr, "risk_parity")
        assert set(adjusted.keys()) == {"a", "b", "c"}
        assert sum(adjusted.values()) == pytest.approx(1.0, abs=1e-6)
        assert meta["method"] == "risk_parity"

    def test_risk_parity_keeps_zero_coef_zero(self):
        fr = _factor_returns_df(["a", "b"], n=200)
        adjusted, _ = risk_adjust_weights({"a": 0.0, "b": 1.0}, fr, "risk_parity")
        assert adjusted["a"] == pytest.approx(0.0, abs=1e-12)

    def test_none_unchanged(self):
        fr = _factor_returns_df(["a", "b"], n=200)
        adjusted, meta = risk_adjust_weights({"a": 0.7, "b": 0.3}, fr, "none")
        assert adjusted == {"a": 0.7, "b": 0.3}
        assert meta == {}

    def test_insufficient_obs_fallback(self):
        fr = _factor_returns_df(["a", "b"], n=5)
        weights = {"a": 0.7, "b": 0.3}
        adjusted, meta = risk_adjust_weights(weights, fr, "risk_parity")
        assert adjusted == weights
        assert meta == {}

    def test_missing_column_fallback(self):
        fr = pd.DataFrame({"a": np.random.default_rng(1).normal(0, 0.01, 100)})
        adjusted, _ = risk_adjust_weights({"a": 0.5, "b": 0.5}, fr, "risk_parity")
        assert adjusted == {"a": 0.5, "b": 0.5}


class TestRiskParitySolver:
    """等风险贡献求解器。"""

    def test_weights_sum_to_one(self):
        rng = np.random.default_rng(3)
        X = rng.normal(0, 1, (500, 4))
        cov = np.cov(X, rowvar=False)
        w = _risk_parity_weights(cov)
        assert w.shape == (4,)
        assert sum(w) == pytest.approx(1.0, abs=1e-8)
        assert np.all(w > 0)

    def test_equal_risk_contribution(self):
        rng = np.random.default_rng(4)
        X = rng.normal(0, 1, (800, 3))
        cov = np.cov(X, rowvar=False)
        w = _risk_parity_weights(cov)
        sw = cov @ w
        rc = w * sw
        rc = rc / rc.sum()
        assert np.allclose(rc, 1.0 / 3, atol=1e-2)


class TestRiskAdjustFromPanel:
    """面板 → 因子收益 → 风险调整 全链路。"""

    def test_full_flow(self):
        rng = np.random.default_rng(5)
        n_dates, n_stocks, n_factors = 60, 20, 2
        signal = rng.normal(0, 1, (n_dates, n_stocks, n_factors))
        fwd = rng.normal(0, 0.02, (n_dates, n_stocks))
        dates = list(pd.date_range("2024-01-01", periods=n_dates))
        adjusted, meta = risk_adjust_from_panel({"f0": 0.7, "f1": 0.3}, signal, fwd, dates, ["f0", "f1"], "risk_parity")
        assert set(adjusted.keys()) == {"f0", "f1"}
        assert sum(adjusted.values()) == pytest.approx(1.0, abs=1e-6)

    def test_none_mode(self):
        adjusted, meta = risk_adjust_from_panel({"a": 1.0}, None, None, [], ["a"], "none")
        assert adjusted == {"a": 1.0}


# ─── ② 滚动样本外验证 ───────────────────────────────────


class TestRollingOosValidate:
    """滚动窗口样本外验证。"""

    def test_returns_meta(self):
        rng = np.random.default_rng(6)
        n_dates, n_stocks, n_factors = 400, 30, 3
        signal = rng.normal(0, 1, (n_dates, n_stocks, n_factors))
        fwd = rng.normal(0, 0.02, (n_dates, n_stocks))
        cfg = WeightLearningConfig(rolling_windows=4, min_window_dates=40)
        meta = rolling_oos_validate(signal, fwd, ["f0", "f1", "f2"], cfg)
        assert meta["n_windows"] > 0
        assert "oos_ic_mean" in meta and "weight_stability" in meta and "weight_decay" in meta

    def test_small_data_degrades(self):
        rng = np.random.default_rng(7)
        signal = rng.normal(0, 1, (30, 10, 2))
        fwd = rng.normal(0, 0.02, (30, 10))
        cfg = WeightLearningConfig(rolling_windows=5, min_window_dates=40)
        meta = rolling_oos_validate(signal, fwd, ["f0", "f1"], cfg)
        assert meta["n_windows"] == 0
        assert not np.isfinite(meta["oos_ic_mean"])

    def test_patched_fit(self):
        rng = np.random.default_rng(8)
        n_dates, n_stocks, n_factors = 300, 20, 2
        signal = rng.normal(0, 1, (n_dates, n_stocks, n_factors))
        fwd = rng.normal(0, 0.02, (n_dates, n_stocks))
        cfg = WeightLearningConfig(rolling_windows=3, min_window_dates=20)
        with patch(
            "fts.factor_engine.weight_learning._fit_elasticnet_coefs",
            return_value=np.array([0.6, 0.4]),
        ):
            meta = rolling_oos_validate(signal, fwd, ["f0", "f1"], cfg)
        assert meta["n_windows"] == 3
        assert np.isfinite(meta["oos_ic_mean"])


class TestFitElasticnetCoefs:
    """逐日 Elastic Net 回归系数提取。"""

    def test_success_with_patched_model(self):
        rng = np.random.default_rng(9)
        X = rng.normal(0, 1, (50, 15, 2))
        y = rng.normal(0, 0.02, (50, 15))
        fake_model = MagicMock()
        fake_model.coef_ = np.array([0.6, 0.4])
        with patch("sklearn.linear_model.ElasticNetCV", return_value=fake_model):
            coefs = _fit_elasticnet_coefs(X, y)
        assert coefs is not None
        assert coefs.shape == (2,)

    def test_insufficient_valid_dates_none(self):
        X = np.full((50, 15, 2), np.nan)
        y = np.zeros((50, 15))
        assert _fit_elasticnet_coefs(X, y) is None


# ─── ③ 跨市场迁移 IC 对比验证 ───────────────────────────


def _make_signal_panel(n_stocks: int = 12, n_days: int = 30) -> tuple[dict, list]:
    """构造 {sym: DataFrame} 面板 + 共同日期。"""
    rng = np.random.default_rng(11)
    idx = pd.date_range("2024-01-01", periods=n_days)
    panel = {}
    for i in range(n_stocks):
        close = 100 + np.cumsum(rng.normal(0, 1, n_days))
        panel[f"SYM{i}"] = pd.DataFrame({"close": close}, index=idx)
    return panel, list(idx)


class TestCrossMarketIcCheck:
    """跨市场迁移 IC 对比（③）。"""

    def test_empty_alt_panel_degrades(self):
        provider = MagicMock()
        provider.get_futures_panel.return_value = ({}, [])
        signal = np.random.default_rng(1).normal(0, 1, (20, 10, 1))
        fwd = np.random.default_rng(2).normal(0, 0.02, (20, 10))
        meta = cross_market_ic_check(
            provider,
            {"f0": {"code": "close", "params": {}}},
            ["f0"],
            signal,
            fwd,
            list(pd.date_range("2024-01-01", periods=20)),
            "stock",
        )
        assert meta == {}

    def test_datetimeindex_dates_no_crash(self):
        """dates_alt 为 DatetimeIndex 时不再抛真值歧义（v2.75.0 bug 修复）。"""
        provider = MagicMock()
        idx = list(pd.date_range("2024-01-01", periods=20))
        # 替代面板为空 dict + DatetimeIndex 共同日期
        provider.get_futures_panel.return_value = ({}, pd.DatetimeIndex(idx))
        signal = np.random.default_rng(1).normal(0, 1, (20, 10, 1))
        fwd = np.random.default_rng(2).normal(0, 0.02, (20, 10))
        meta = cross_market_ic_check(
            provider,
            {"f0": {"code": "close", "params": {}}},
            ["f0"],
            signal,
            fwd,
            idx,
            "stock",
        )
        assert meta == {}

    def test_real_panels_produce_comparison(self):
        panel, dates = _make_signal_panel()
        provider = MagicMock()
        provider.get_futures_panel.return_value = (panel, dates)
        rng = np.random.default_rng(3)
        n_dates, n_stocks = len(dates), len(panel)
        signal = rng.normal(0, 1, (n_dates, n_stocks, 1))
        fwd = rng.normal(0, 0.02, (n_dates, n_stocks))
        meta = cross_market_ic_check(
            provider,
            {"f0": {"code": "close", "params": {}}},
            ["f0"],
            signal,
            fwd,
            dates,
            "stock",
        )
        assert "factor_ic" in meta
        assert "f0" in meta["factor_ic"]
        assert "migration_gap_mean" in meta


# ─── 配置默认值 ─────────────────────────────────────────


class TestWeightLearningConfigDefaults:
    """权重学习配置默认值（v2.78.1）。"""

    def test_cross_market_ic_default_off(self):
        """跨市场 IC 对比默认关闭，避免无关股票面板下载（v2.78.1）。"""
        assert WeightLearningConfig().cross_market_ic is False

    def test_cross_market_ic_can_be_enabled(self):
        """显式开启仍可用。"""
        assert WeightLearningConfig(cross_market_ic=True).cross_market_ic is True
