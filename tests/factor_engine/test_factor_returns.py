"""
tests/factor_engine/test_factor_returns.py — 因子收益序列 + 风险模型测试（GAP-L301/L302）

覆盖范围:
    - FactorReturnsBuilder.build_from_panel 多空收益序列构建（方向正确性/边界）
    - align_to_factors / portfolio_returns / annualized_sharpe / max_abs_correlation
    - RiskModelEstimator.estimate 收缩协方差（正定性/条件数/边界）

版本: v1.0.0（A 阶段，随 v2.61.0）
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.factor_returns import (
    FactorReturnsBuilder,
    FactorReturnsConfig,
)
from fts.factor_engine.risk_model import RiskModelConfig, RiskModelEstimator


# ─── 工具 ─────────────────────────────────────────────────


def _make_panel(
    n_dates: int = 60,
    n_stocks: int = 50,
    n_factors: int = 2,
    seed: int = 42,
    signal_beta: float = 0.05,
) -> tuple[np.ndarray, np.ndarray, list[str], list[str]]:
    """构造合成面板：信号与收益存在已知正相关。"""
    rng = np.random.default_rng(seed)
    dates = [f"2026-01-{i:02d}" for i in range(1, n_dates + 1)]
    factor_ids = [f"fct_{i}" for i in range(n_factors)]

    signal = rng.normal(size=(n_dates, n_stocks, n_factors))
    fwd = np.zeros((n_dates, n_stocks))
    for j in range(n_factors):
        fwd += signal_beta * signal[:, :, j]
    fwd += rng.normal(scale=0.02, size=(n_dates, n_stocks))
    return signal, fwd, dates, factor_ids


# ════════════════════════════════════════════════════════════
# 1. FactorReturnsBuilder 测试
# ════════════════════════════════════════════════════════════


class TestFactorReturnsBuilder:
    """因子收益序列构建 — 多空组合法。"""

    def test_build_direction_positive(self):
        """信号与收益正相关 → 多空收益序列均值应为正。"""
        signal, fwd, dates, fids = _make_panel()
        builder = FactorReturnsBuilder()
        result = builder.build_from_panel(signal, fwd, dates, fids)

        assert result.returns.shape == (60, 2)
        for fid in fids:
            assert np.nanmean(result.returns[fid].values) > 0

    def test_coverage_full(self):
        """无缺失数据时覆盖率应为 1.0。"""
        signal, fwd, dates, fids = _make_panel()
        result = FactorReturnsBuilder().build_from_panel(signal, fwd, dates, fids)
        for fid in fids:
            assert result.coverage[fid] == pytest.approx(1.0, abs=1e-6)

    def test_insufficient_stocks_produces_nan(self):
        """每日有效股票 < min_stocks 时该日该因子置 NaN。"""
        signal, fwd, dates, fids = _make_panel(n_dates=30, n_stocks=30)
        # 第 0 因子在第 5 日后全部置 NaN（模拟覆盖不足）
        signal[5:, :, 0] = np.nan
        builder = FactorReturnsBuilder(FactorReturnsConfig(min_stocks=10, min_dates=5))
        result = builder.build_from_panel(signal, fwd, dates, fids)
        # 前 5 日有效，其余 NaN
        assert np.isfinite(result.returns.iloc[:5, 0]).all()
        assert np.isnan(result.returns.iloc[5:, 0]).all()

    def test_dimension_mismatch_raises(self):
        """信号矩阵与收益矩阵维度不匹配应抛 ValueError。"""
        signal, fwd, dates, fids = _make_panel()
        with pytest.raises(ValueError, match="维度"):
            FactorReturnsBuilder().build_from_panel(signal, fwd[:-1], dates, fids)

    def test_invalid_quantile_raises(self):
        """quantile 越界应抛 ValueError。"""
        with pytest.raises(ValueError, match="quantile"):
            FactorReturnsBuilder(FactorReturnsConfig(quantile=0.6))

    def test_align_to_factors(self):
        """对齐只保留指定列且剔除缺失行。"""
        rng = np.random.default_rng(0)
        fr = pd.DataFrame(
            rng.normal(size=(30, 3)),
            columns=["a", "b", "c"],
            index=pd.date_range("2026-01-01", periods=30),
        )
        fr.loc[fr.index[0], "b"] = np.nan
        aligned = FactorReturnsBuilder.align_to_factors(fr, ["a", "b", "c"])
        assert list(aligned.columns) == ["a", "b", "c"]
        assert len(aligned) == 29
        # 无交集 → 空 DataFrame
        assert FactorReturnsBuilder.align_to_factors(fr, ["x"]).empty

    def test_portfolio_returns_weighted(self):
        """组合收益 = w·R，权重归一化。"""
        rng = np.random.default_rng(1)
        fr = pd.DataFrame(rng.normal(size=(20, 2)), columns=["a", "b"])
        pf = FactorReturnsBuilder.portfolio_returns(fr, [1.0, 1.0])
        expected = (fr["a"] * 0.5 + fr["b"] * 0.5).rename("portfolio_returns")
        pd.testing.assert_series_equal(pf, expected)

    def test_annualized_sharpe(self):
        """年化夏普 = mean/std × sqrt(252)。"""
        rng = np.random.default_rng(2)
        returns = pd.Series(rng.normal(0.001, 0.01, size=252))
        sharpe = FactorReturnsBuilder.annualized_sharpe(returns)
        manual = returns.mean() / returns.std(ddof=1) * np.sqrt(252)
        assert sharpe == pytest.approx(manual, abs=1e-9)
        # 样本不足 → 0.0
        assert FactorReturnsBuilder.annualized_sharpe(pd.Series([1.0])) == 0.0

    def test_max_abs_correlation(self):
        """组合内最大 |相关性|（对角剔除）。"""
        rng = np.random.default_rng(3)
        x = rng.normal(size=(100,))
        fr = pd.DataFrame(
            {
                "a": x,
                "b": x + 0.01 * rng.normal(size=100),  # 与 a 高相关
                "c": rng.normal(size=100),  # 独立
            }
        )
        max_corr = FactorReturnsBuilder.max_abs_correlation(fr)
        assert max_corr > 0.9
        # 单因子 → 0.0
        assert FactorReturnsBuilder.max_abs_correlation(fr[["a"]]) == 0.0


# ════════════════════════════════════════════════════════════
# 1.5 GAP-I306: 自动构建矩阵质量修正（quantile/方向校准/MAD 缩尾）
# ════════════════════════════════════════════════════════════


def test_auto_build_factor_returns_robust_config_and_winsorize(tmp_path, monkeypatch):
    """_auto_build_factor_returns 使用 quantile=0.3+directional=True，并对收益做 MAD 缩尾。

    覆盖 GAP-I306：25 品种 quantile=0.2 每腿仅 5 只致 Sharpe 虚高 20.06——
    修复后分位腿加宽、方向校准、极端日收益截断。
    """
    from types import SimpleNamespace

    import fts.factor_engine.factor_returns as fr_mod
    import fts.factor_engine.l3_signal_service as l3_mod
    from fts.factor_engine.portfolio_loop import _auto_build_factor_returns

    n = 30
    idx = pd.date_range("2026-01-01", periods=n)
    panel = {
        sym: pd.DataFrame({"close": np.linspace(3000 + i * 1000, 4000 + i * 1000, n)}, index=idx)
        for i, sym in enumerate(("RB0", "CU0", "AU0", "M0"))
    }
    factors = [
        {
            "factor_id": "f1",
            "code": (
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    return np.tanh(data['close'].values / 1000.0)\n"
            ),
        },
        {
            "factor_id": "f2",
            "code": (
                "def factor_program(data, params):\n"
                "    import numpy as np\n"
                "    return np.tanh(data['close'].values / 2000.0)\n"
            ),
        },
    ]

    captured: dict = {}

    class FakeBuilder:
        def __init__(self, config=None):
            captured["config"] = config

        def build_from_panel(self, **kw):
            # 0.1/0.2 交替 29 行 + 1 个极端离群 10.0 → MAD>0 缩尾应截断离群
            base = [0.1 if i % 2 == 0 else 0.2 for i in range(n)]
            base[-1] = 10.0
            return SimpleNamespace(
                returns=pd.DataFrame({"f1": base, "f2": [0.15] * n}, index=idx)
            )

    monkeypatch.setattr(fr_mod, "FactorReturnsBuilder", FakeBuilder)
    monkeypatch.setattr(
        l3_mod,
        "build_signal_matrix",
        lambda *a, **k: SimpleNamespace(
            signal_matrix=np.zeros((n, 4, 2)),
            forward_returns=np.zeros((n, 4)),
            dates=list(idx),
            factor_ids=["f1", "f2"],
        ),
    )
    out = _auto_build_factor_returns(panel, factors, tmp_path, market="futures")
    assert out is not None
    assert captured["config"].quantile == 0.3
    assert captured["config"].directional is True
    # MAD 缩尾生效：极端离群 10.0 被截断（中位数 0.15、MAD=0.05 → 上界≈0.37）
    assert float(out["f1"].max()) < 1.0


# ════════════════════════════════════════════════════════════
# 2. RiskModelEstimator 测试
# ════════════════════════════════════════════════════════════


class TestRiskModelEstimator:
    """风险模型 — Ledoit-Wolf 收缩协方差。"""

    def _make_returns(self, corr: float = 0.9, n: int = 200) -> pd.DataFrame:
        rng = np.random.default_rng(7)
        x = rng.normal(size=n)
        y = corr * x + np.sqrt(1 - corr**2) * rng.normal(size=n)
        z = rng.normal(size=n)
        return pd.DataFrame({"a": x, "b": y, "c": z})

    def test_cov_positive_definite(self):
        """收缩协方差必须正定（全部特征值 > 0）。"""
        fr = self._make_returns()
        result = RiskModelEstimator().estimate(fr)
        assert result.cov.shape == (3, 3)
        assert np.all(result.eigenvalues > 0)

    def test_shrinkage_bounds(self):
        """收缩强度在 [0, 1] 内。"""
        fr = self._make_returns()
        result = RiskModelEstimator().estimate(fr)
        assert 0.0 <= result.shrinkage <= 1.0

    def test_condition_number_improves(self):
        """收缩后条件数 ≤ 样本协方差条件数（高相关数据）。"""
        fr = self._make_returns(corr=0.99, n=50)
        est = RiskModelEstimator()
        result = est.estimate(fr)
        sample_cond = np.linalg.cond(result.sample_cov)
        assert result.condition_number <= sample_cond + 1e-6

    def test_realized_vol_positive(self):
        """年化波动率为正。"""
        fr = self._make_returns()
        result = RiskModelEstimator().estimate(fr)
        assert np.all(result.realized_vol > 0)

    def test_too_few_factors_raises(self):
        """因子数 < 2 抛 ValueError。"""
        fr = pd.DataFrame({"a": [0.1, 0.2, 0.3, 0.4]})
        with pytest.raises(ValueError, match="因子数"):
            RiskModelEstimator().estimate(fr)

    def test_too_few_obs_raises(self):
        """有效观测不足抛 ValueError。"""
        fr = pd.DataFrame({"a": [0.1, 0.2], "b": [0.3, 0.4]})
        with pytest.raises(ValueError, match="观测"):
            RiskModelEstimator().estimate(fr)

    def test_none_shrinkage(self):
        """shrinkage=none 时返回样本协方差。"""
        fr = self._make_returns()
        result = RiskModelEstimator(RiskModelConfig(shrinkage="none")).estimate(fr)
        np.testing.assert_allclose(result.cov, result.sample_cov, atol=1e-8)

    def test_numpy_ledoit_wolf_shrinks(self):
        """numpy Ledoit-Wolf 收缩生效：收缩协方差 ≠ 样本协方差且正定。"""
        fr = self._make_returns(corr=0.8, n=100)
        result = RiskModelEstimator().estimate(fr)
        assert result.shrinkage > 0.0
        assert np.all(np.linalg.eigvalsh(result.cov) > 0)
        assert not np.allclose(result.cov, result.sample_cov)
