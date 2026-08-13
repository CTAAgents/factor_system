"""G10 波动率/季节性中性化测试（plans/35 §5.3，v2.103.0+15）。

覆盖：
- 波动率截面中性化：合成「波动率单调影响信号」面板 → 残差与波动率相关归零
- 时序月度去季节化：强一月效应 → 去季节化后一月偏移消失
- 向后兼容：无 vol_map / 无 dates → 行为不变
- 管线透传：cross_section_evaluate_backtest(vol_map=...) 正常执行
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from fts.factor_engine.barra.barra_neutralizer import barra_neutralize_matrix


def _make_symbols(n: int) -> list[str]:
    return [f"SYM{i:02d}" for i in range(n)]


class TestVolNeutralization:
    """波动率截面中性化：信号与品种波动率水平的相关性被剥离。"""

    def test_vol_correlation_removed(self):
        rng = np.random.default_rng(42)
        n_dates, n_stocks = 80, 25
        symbols = _make_symbols(n_stocks)
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
        vols = rng.uniform(0.1, 0.6, n_stocks)
        alpha = rng.normal(0, 0.1, n_stocks)
        noise = rng.normal(0, 0.05, (n_dates, n_stocks))
        # 信号 = 2.0×波动率 + 品种特质 + 噪声（波动率单调影响信号）
        signal = np.tile(2.0 * vols + alpha, (n_dates, 1)) + noise

        residual = barra_neutralize_matrix(
            signal,
            symbols,
            {},
            vol_map={s: v for s, v in zip(symbols, vols)},
            dates=dates,
        )

        # 逐日截面 OLS 残差与 X 列（波动率）正交 → 时间均值残差与波动率相关归零
        res_mean = np.nanmean(residual, axis=0)
        corr = float(np.corrcoef(res_mean, vols)[0, 1])
        assert abs(corr) < 0.05

    def test_vol_column_kept_when_no_style(self):
        """style_exposures 为空时（evaluation_chain 独立路径），波动率列仍生效。"""
        rng = np.random.default_rng(5)
        n_dates, n_stocks = 60, 15
        symbols = _make_symbols(n_stocks)
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
        vols = rng.uniform(0.2, 0.8, n_stocks)
        signal = np.tile(1.5 * vols, (n_dates, 1)) + rng.normal(0, 0.05, (n_dates, n_stocks))

        residual = barra_neutralize_matrix(
            signal,
            symbols,
            {},
            vol_map={s: v for s, v in zip(symbols, vols)},
            dates=dates,
            include_season_neutral=False,
        )
        res_mean = np.nanmean(residual, axis=0)
        corr = float(np.corrcoef(res_mean, vols)[0, 1])
        assert abs(corr) < 0.05


class TestSeasonNeutralization:
    """时序月度去季节化：日历季节性（一月效应）被剥离。"""

    def test_january_effect_removed(self):
        rng = np.random.default_rng(7)
        n_stocks = 10
        symbols = _make_symbols(n_stocks)
        dates = pd.date_range("2023-01-01", periods=104, freq="W")  # ~2 年覆盖 1-12 月
        jan = dates.month.to_numpy() == 1
        base = rng.normal(0, 0.1, (len(dates), n_stocks))
        signal = base.copy()
        signal[jan, :] += 1.0  # 强一月效应

        residual = barra_neutralize_matrix(
            signal,
            symbols,
            {},
            dates=dates,
            include_vol_neutral=False,
        )

        jan_before = float(signal[jan].mean())
        jan_after = float(residual[jan].mean())
        nonjan_after = float(residual[~jan].mean())
        assert jan_before > 0.5  # 一月效应确实存在
        assert abs(jan_after - nonjan_after) < 0.15  # 去季节化后偏移消失

    def test_season_skipped_without_dates(self):
        """dates=None → 季节中性化跳过（向后兼容）。"""
        rng = np.random.default_rng(11)
        n_dates, n_stocks = 40, 6
        symbols = _make_symbols(n_stocks)
        signal = rng.normal(0, 1, (n_dates, n_stocks))

        out = barra_neutralize_matrix(signal, symbols, {}, dates=None)
        np.testing.assert_allclose(out, signal)  # 无自变量 → 原样返回


class TestBackwardCompat:
    """无任何中性化输入时行为不变。"""

    def test_no_exposures_noop(self):
        rng = np.random.default_rng(3)
        n_dates, n_stocks = 30, 8
        symbols = _make_symbols(n_stocks)
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
        signal = rng.normal(0, 1, (n_dates, n_stocks))

        out1 = barra_neutralize_matrix(
            signal, symbols, {}, dates=dates, include_season_neutral=False
        )
        np.testing.assert_allclose(out1, signal)

        out2 = barra_neutralize_matrix(
            signal,
            symbols,
            {},
            vol_map={s: 0.3 for s in symbols},
            dates=dates,
            include_season_neutral=False,
            include_vol_neutral=False,
        )
        np.testing.assert_allclose(out2, signal)

    def test_all_nan_vol_map_skipped(self):
        """vol_map 全 NaN → 波动率列自动跳过，不改变行为。"""
        rng = np.random.default_rng(9)
        n_dates, n_stocks = 30, 8
        symbols = _make_symbols(n_stocks)
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
        signal = rng.normal(0, 1, (n_dates, n_stocks))

        out = barra_neutralize_matrix(
            signal,
            symbols,
            {},
            vol_map={s: np.nan for s in symbols},
            dates=dates,
            include_season_neutral=False,
        )
        np.testing.assert_allclose(out, signal)


class TestPipelineWiring:
    """cross_section_evaluate_backtest 新增 vol_map 参数透传。"""

    @staticmethod
    def _make_panel(n_stocks: int = 12, n_dates: int = 200) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        rng = np.random.default_rng(123)
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="B")
        panel: dict[str, pd.DataFrame] = {}
        for i in range(n_stocks):
            close = 100 + np.cumsum(rng.normal(0, 0.5, n_dates))
            panel[f"STK_{i}"] = pd.DataFrame(
                {
                    "open": close + rng.normal(0, 0.1, n_dates),
                    "high": close + np.abs(rng.normal(0, 0.2, n_dates)),
                    "low": close - np.abs(rng.normal(0, 0.2, n_dates)),
                    "close": close,
                    "volume": rng.integers(1000, 10000, n_dates).astype(float),
                },
                index=dates,
            )
        return panel, dates

    def test_vol_map_threaded(self):
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        fp = create_factor_program(
            name="cross_mom_g10",
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
            economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="G10 管线"),
            source="manual",
        )
        panel, dates = self._make_panel()
        vol_map = {}
        for sym, df in panel.items():
            ret = df["close"].pct_change().dropna()
            vol_map[sym] = float(ret.std() * np.sqrt(252.0))

        bt = cross_section_evaluate_backtest(fp, panel, dates, vol_map=vol_map)
        assert np.isfinite(bt.get("ic", np.nan))
        assert np.isfinite(bt.get("sharpe", np.nan))

    def test_vol_map_optional_no_change(self):
        """不传 vol_map 时原评估路径不受影响。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        fp = create_factor_program(
            name="cross_mom_g10b",
            code=(
                "import numpy as np\n"
                "def factor_program(data, params):\n"
                "    return np.zeros(len(data['close']))\n"
            ),
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="G10 无 vol_map"),
            source="manual",
        )
        panel, dates = self._make_panel()
        bt = cross_section_evaluate_backtest(fp, panel, dates)
        assert "ic" in bt
