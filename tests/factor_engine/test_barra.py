"""
tests/factor_engine/test_barra.py — Barra 风格因子体系测试（GAP-S02）。

覆盖:
    1. BarraStyleEngine: 10 风格因子暴露计算（含字段缺失降级）
    2. barra_neutralize_matrix: 残差与风格暴露正交 / 合成面板暴露剥离 / 降级
    3. cross_section_evaluate_backtest 集成: style_exposures 参数生效
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.barra.barra_neutralizer import barra_neutralize_matrix
from fts.factor_engine.barra.barra_style import (
    STYLE_FACTOR_NAMES,
    BarraStyleEngine,
)


# ─── 测试面板构造 ─────────────────────────────────────────

def _make_fundamental_panel(
    n_stocks: int = 20,
    n_dates: int = 300,
    seed: int = 42,
) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    """构造带基本面字段的股票面板。

    - size 暴露：市值与行业正相关（构造行业 beta 污染）
    - momentum 暴露：市值相关（构造 size 污染）
    """
    np.random.seed(seed)
    dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
    panel: dict[str, pd.DataFrame] = {}
    for i in range(n_stocks):
        base_cap = 1e10 + i * 1e9  # 单调市值（构造 size 差异）
        close = 50 + np.cumsum(np.random.randn(n_dates) * 0.3)
        df = pd.DataFrame({
            "open": close + np.random.randn(n_dates) * 0.1,
            "high": close + np.abs(np.random.randn(n_dates)) * 0.3,
            "low": close - np.abs(np.random.randn(n_dates)) * 0.3,
            "close": close,
            "volume": np.random.randint(1000, 10000, n_dates).astype(float),
            # 基本面
            "total_market_cap": np.full(n_dates, base_cap),
            "pb": np.full(n_dates, 1.0 + (i % 5) * 0.5),
            "turnover_rate": np.full(n_dates, 0.01 + (i % 7) * 0.005),
            "pe_ttm": np.full(n_dates, 8.0 + (i % 10) * 3.0),
            "roe": np.full(n_dates, 0.08 + (i % 6) * 0.02),
            "revenue_growth": np.full(n_dates, 0.05 + (i % 8) * 0.03),
            "profit_growth": np.full(n_dates, 0.03 + (i % 8) * 0.02),
            "debt_to_equity": np.full(n_dates, 0.3 + (i % 9) * 0.1),
        }, index=dates)
        panel[f"STK_{i}"] = df
    return panel, dates


def _make_signal_matrix(
    n_dates: int,
    symbols: list[str],
    seed: int = 7,
) -> np.ndarray:
    """构造与市值/行业相关的污染信号（便于验证剥离）。"""
    np.random.seed(seed)
    n = len(symbols)
    mat = np.random.randn(n_dates, n)
    # 注入强 size 暴露（第 1 列市值单调 → 信号与市值相关）
    size_vec = np.arange(n, dtype=float) / n
    mat = mat + 2.0 * size_vec[None, :]
    return mat


# ─── BarraStyleEngine ─────────────────────────────────────

class TestBarraStyleEngine:
    """BarraStyleEngine 风格暴露计算。"""

    def test_style_factor_names_complete(self):
        """应包含 Barra 10 大风格因子。"""
        assert len(STYLE_FACTOR_NAMES) == 10
        for expected in (
            "size", "beta", "momentum", "residual_vol", "nonlinear_size",
            "book_to_price", "liquidity", "earnings_yield", "growth", "leverage",
        ):
            assert expected in STYLE_FACTOR_NAMES

    def test_compute_exposures_shapes(self):
        """每个风格暴露应返回 (n_dates, n_stocks) DataFrame。"""
        panel, dates = _make_fundamental_panel()
        engine = BarraStyleEngine()
        exposures = engine.compute_exposures(panel, dates)
        assert set(exposures.keys()) == set(STYLE_FACTOR_NAMES)
        for style, df in exposures.items():
            assert df.shape == (len(dates), len(panel))
            assert isinstance(df.index, pd.DatetimeIndex)
            # size/momentum 等应有非 NaN 值
            assert not df.isna().all().all(), f"{style} 应至少含一个有效暴露"

    def test_size_exposure_monotonic(self):
        """size 暴露应与市值单调正相关（构造的市值差异）。"""
        panel, dates = _make_fundamental_panel()
        engine = BarraStyleEngine(style_names=["size"])
        exposures = engine.compute_exposures(panel, dates)
        size_df = exposures["size"]
        # 取中间某日，size 暴露应与市值序一致（Spearman 正相关）
        mid = len(dates) // 2
        row = size_df.iloc[mid]
        caps = [panel[sym]["total_market_cap"].iloc[0] for sym in size_df.columns]
        corr = pd.Series(row.values).corr(pd.Series(caps), method="spearman")
        assert not np.isnan(corr)
        assert corr > 0.5

    def test_unknown_style_raises(self):
        """未知风格因子应抛 ValueError。"""
        with pytest.raises(ValueError):
            BarraStyleEngine(style_names=["not_a_style"])

    def test_missing_fundamental_fields_degrade(self):
        """基本面字段缺失时对应风格应全 NaN 而非抛异常。"""
        panel, dates = _make_fundamental_panel()
        # 移除全部基本面列 → size/book_to_price 等应全 NaN
        clean_panel = {
            sym: df[["open", "high", "low", "close", "volume"]]
            for sym, df in panel.items()
        }
        engine = BarraStyleEngine(style_names=["size", "momentum", "book_to_price"])
        exposures = engine.compute_exposures(clean_panel, dates)
        assert exposures["size"].isna().all().all()
        assert exposures["book_to_price"].isna().all().all()
        # momentum 只依赖 close → 应有效
        assert not exposures["momentum"].isna().all().all()


# ─── barra_neutralize_matrix ──────────────────────────────

class TestBarraNeutralizeMatrix:
    """Barra 风格中性化（横截面回归残差）。"""

    def _setup(self, n_stocks: int = 20, n_dates: int = 60):
        panel, dates = _make_fundamental_panel(n_stocks=n_stocks, n_dates=n_dates)
        engine = BarraStyleEngine()
        exposures = engine.compute_exposures(panel, dates)
        symbols = sorted(panel.keys())
        signal = _make_signal_matrix(n_dates, symbols)
        return signal, symbols, exposures

    def test_residual_shape(self):
        """残差应保持原矩阵形状。"""
        signal, symbols, exposures = self._setup()
        residual = barra_neutralize_matrix(signal, symbols, exposures)
        assert residual.shape == signal.shape

    def test_residual_orthogonal_to_style(self):
        """残差应与各风格暴露正交（截面相关性≈0，GAP-S02 核心断言）。"""
        signal, symbols, exposures = self._setup()
        residual = barra_neutralize_matrix(signal, symbols, exposures)
        t = 30
        res_t = residual[t, :]
        for style, df in exposures.items():
            exp_t = df.iloc[t].values
            valid = ~(np.isnan(res_t) | np.isnan(exp_t))
            if valid.sum() < 5:
                continue
            corr = np.corrcoef(res_t[valid], exp_t[valid])[0, 1]
            assert abs(corr) < 0.15, f"{style} 残差未正交: corr={corr:.3f}"

    def test_style_exposure_removed(self):
        """合成面板注入 size 暴露后，残差与 size 相关性应显著下降（剥离验证）。"""
        from scipy import stats as sp_stats

        signal, symbols, exposures = self._setup()
        # 原始信号与 size 暴露相关性强（污染；Spearman 与 rank-zscore 匹配）
        size_vec = exposures["size"].iloc[30].values
        raw_sig = signal[30, :]
        valid = ~np.isnan(size_vec)
        corr_before = abs(
            sp_stats.spearmanr(raw_sig[valid], size_vec[valid]).statistic
        )

        residual = barra_neutralize_matrix(signal, symbols, exposures)
        res_t = residual[30, :]
        valid_r = ~(np.isnan(res_t) | np.isnan(size_vec))
        corr_after = abs(
            sp_stats.spearmanr(res_t[valid_r], size_vec[valid_r]).statistic
        )

        assert corr_before > 0.15, f"测试前置条件失败: corr_before={corr_before:.3f}"
        assert corr_after < corr_before / 2, f"size 暴露剥离不充分: {corr_before:.3f}→{corr_after:.3f}"

    def test_no_exposures_returns_signal(self):
        """无风格暴露（空 dict）时应原样返回信号。"""
        signal, symbols, _ = self._setup()
        residual = barra_neutralize_matrix(signal, symbols, {})
        assert residual.shape == signal.shape
        # 无行业、无风格 → 不修改（nan 相等处理）
        np.testing.assert_allclose(residual, signal, equal_nan=True)

    def test_industry_map_combined(self):
        """行业虚拟变量与风格暴露可叠加回归。"""
        signal, symbols, exposures = self._setup()
        industry_map = {f"STK_{i}": ("A" if i % 2 == 0 else "B") for i in range(len(symbols))}
        residual = barra_neutralize_matrix(signal, symbols, exposures, industry_map=industry_map)
        assert residual.shape == signal.shape
        assert not np.isnan(residual).all()

    def test_small_sample_degrade(self):
        """样本过少时应降级为去均值而非抛异常。"""
        signal, symbols, exposures = self._setup(n_stocks=4, n_dates=20)
        residual = barra_neutralize_matrix(signal, symbols, exposures)
        assert residual.shape == signal.shape


# ─── cross_section_evaluate_backtest 集成 ─────────────────

class TestCrossSectionBarraIntegration:
    """GAP-S02: cross_section_evaluate_backtest 集成 style_exposures。"""

    @staticmethod
    def _make_factor():
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.factor_program import create_factor_program

        return create_factor_program(
            name="cross_barra_integ",
            code=(
                "import numpy as np\n"
                "def factor_program(data, params):\n"
                "    close = data['close'].values\n"
                "    n = len(close)\n"
                "    sig = np.zeros(n)\n"
                "    for i in range(5, n):\n"
                "        sig[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
                "    return np.clip(sig * 10, -1.0, 1.0)\n"
            ),
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
            economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="Barra集成"),
            source="manual",
        )

    def test_style_exposures_accepted(self):
        """传入 style_exposures 时应正常评估并返回有效指标。"""
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

        panel, dates = _make_fundamental_panel(n_stocks=10, n_dates=200)
        engine = BarraStyleEngine()
        exposures = engine.compute_exposures(panel, dates)
        fp = self._make_factor()
        bt = cross_section_evaluate_backtest(
            fp, panel, dates, style_exposures=exposures,
        )
        assert "ic" in bt
        assert "sharpe" in bt
        assert "max_drawdown" in bt

    def test_industry_and_style_combined(self):
        """行业中性化 + 风格中性化可叠加。"""
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest

        panel, dates = _make_fundamental_panel(n_stocks=10, n_dates=200)
        symbols = sorted(panel.keys())
        industry_map = {sym: ("A" if i % 2 == 0 else "B") for i, sym in enumerate(symbols)}
        engine = BarraStyleEngine()
        exposures = engine.compute_exposures(panel, dates)
        fp = self._make_factor()
        bt = cross_section_evaluate_backtest(
            fp, panel, dates, industry_map=industry_map, style_exposures=exposures,
        )
        assert "ic" in bt
        assert "sharpe" in bt
