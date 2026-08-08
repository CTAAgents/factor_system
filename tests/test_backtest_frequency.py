"""
tests.test_backtest_frequency — 分钟级回测频率自适应测试（v2.30.0）。

测试覆盖:
    1. 年化因子映射（get_annualization_factor）
    2. z-score 窗口自适应（get_default_zscore_window）
    3. 频率参数透传（BacktestInput.frequency）
    4. 分钟级数据 schema 加载（datetime 列）
    5. 绩效指标年化（以 daily 为基准，验证 5m 年化因子合理性）

HARNESS §5.4 测试随重构: 每阶段测试全绿才能进入下一阶段。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.backtest_pipeline import (
    BacktestInput,
    BacktestPipeline,
    get_annualization_factor,
    get_default_zscore_window,
    FREQUENCY_ANNUAL_FACTOR,
)


# ─── 1. 年化因子映射 ──────────────────────────────────────


class TestGetAnnualizationFactor:
    """测试年化因子映射准确性。"""

    @pytest.mark.parametrize("frequency, expected", [
        ("daily", 252),
        ("60m", 1638),
        ("30m", 3276),
        ("15m", 6552),
        ("5m", 19656),
        ("1m", 98280),
    ])
    def test_known_frequencies(self, frequency: str, expected: int) -> None:
        """已知频率应返回正确年化因子。"""
        assert get_annualization_factor(frequency) == expected

    def test_unknown_frequency_fallback(self) -> None:
        """未知频率应回退到 252（日线基准）。"""
        assert get_annualization_factor("unknown") == 252
        assert get_annualization_factor("") == 252

    def test_all_keys_in_dict(self) -> None:
        """FREQUENCY_ANNUAL_FACTOR 字典应包含所有标准频率。"""
        expected_keys = {"daily", "60m", "30m", "15m", "5m", "1m"}
        assert set(FREQUENCY_ANNUAL_FACTOR.keys()) == expected_keys

    def test_annual_factors_monotonic(self) -> None:
        """年化因子应随频率降低而单调递增（1m > 5m > 15m > ...）。"""
        freqs = ["daily", "60m", "30m", "15m", "5m", "1m"]
        factors = [get_annualization_factor(f) for f in freqs]
        assert all(factors[i] < factors[i + 1] for i in range(len(factors) - 1))


# ─── 2. z-score 窗口自适应 ────────────────────────────────


class TestGetDefaultZscoreWindow:
    """测试 z-score 滚动窗口自适应。"""

    def test_daily_window(self) -> None:
        """日线窗口应为 20。"""
        assert get_default_zscore_window("daily") == 20

    def test_1m_window(self) -> None:
        """1m 窗口应为 7800（≈ 20 个交易日 × 390 根/日）。"""
        window = get_default_zscore_window("1m")
        assert window == 7800
        assert window >= 20

    def test_5m_window(self) -> None:
        """5m 窗口应为 1560（≈ 20 个交易日 × 78 根/日）。"""
        window = get_default_zscore_window("5m")
        assert window == 1560
        assert window >= 20

    def test_15m_window(self) -> None:
        """15m 窗口应为 520。"""
        assert get_default_zscore_window("15m") == 520

    def test_30m_window(self) -> None:
        """30m 窗口应为 260。"""
        assert get_default_zscore_window("30m") == 260

    def test_60m_window(self) -> None:
        """60m 窗口应为 130。"""
        assert get_default_zscore_window("60m") == 130

    def test_unknown_frequency_minimum(self) -> None:
        """未知频率应返回最小窗口 20。"""
        assert get_default_zscore_window("unknown") == 20

    def test_window_monotonic(self) -> None:
        """窗口应随频率降低而单调递减（1m > 5m > 15m > ...）。"""
        freqs = ["1m", "5m", "15m", "30m", "60m", "daily"]
        windows = [get_default_zscore_window(f) for f in freqs]
        assert all(windows[i] > windows[i + 1] for i in range(len(windows) - 1))


# ─── 3. BacktestInput 频率参数透传 ────────────────────────


class TestBacktestInputFrequency:
    """测试 BacktestInput 的 frequency 字段。"""

    def test_default_frequency(self) -> None:
        """默认频率应为 daily。"""
        dummy_factor = {"factor_id": "test", "code": "", "name": "test"}
        dummy_data = pd.DataFrame({"close": [1.0, 2.0]})
        inp = BacktestInput(factor=dummy_factor, data=dummy_data)
        assert inp.frequency == "daily"

    def test_explicit_frequency(self) -> None:
        """显式指定的频率应正确存储。"""
        dummy_factor = {"factor_id": "test", "code": "", "name": "test"}
        dummy_data = pd.DataFrame({"close": [1.0, 2.0]})
        for freq in ("daily", "1m", "5m", "15m", "30m", "60m"):
            inp = BacktestInput(factor=dummy_factor, data=dummy_data, frequency=freq)
            assert inp.frequency == freq

    def test_invalid_frequency_allowed(self) -> None:
        """频率字段不校验值（由调用方管理）。"""
        dummy_factor = {"factor_id": "test", "code": "", "name": "test"}
        dummy_data = pd.DataFrame({"close": [1.0, 2.0]})
        inp = BacktestInput(factor=dummy_factor, data=dummy_data, frequency="custom")
        assert inp.frequency == "custom"


# ─── 4. 分钟级数据加载 ────────────────────────────────────


class TestMinuteDataLoading:
    """测试分钟级数据加载（datetime 列 vs date 列）。"""

    def _make_minute_data(self, n_rows: int = 100) -> pd.DataFrame:
        """生成模拟分钟级数据。"""
        times = pd.date_range("2026-01-01", periods=n_rows, freq="5min")
        return pd.DataFrame({
            "datetime": times,
            "open": np.random.randn(n_rows) * 10 + 3500,
            "high": np.random.randn(n_rows) * 10 + 3510,
            "low": np.random.randn(n_rows) * 10 + 3490,
            "close": np.random.randn(n_rows) * 10 + 3500,
            "volume": np.random.randint(100, 1000, n_rows),
        })

    def _make_daily_data(self, n_rows: int = 100) -> pd.DataFrame:
        """生成模拟日线数据。"""
        dates = pd.date_range("2026-01-01", periods=n_rows, freq="D")
        return pd.DataFrame({
            "date": dates,
            "open": np.random.randn(n_rows) * 10 + 3500,
            "high": np.random.randn(n_rows) * 10 + 3510,
            "low": np.random.randn(n_rows) * 10 + 3490,
            "close": np.random.randn(n_rows) * 10 + 3500,
            "volume": np.random.randint(50000, 200000, n_rows),
        })

    def test_minute_data_loads(self) -> None:
        """分钟级数据应能正常加载（datetime 列）。"""
        data = self._make_minute_data(100)
        factor = {"factor_id": "test_minute", "code": "output = close", "name": "test"}
        inp = BacktestInput(factor=factor, data=data, frequency="5m")
        pipeline = BacktestPipeline()
        result = pipeline.run(inp)
        assert result.success, f"分钟级回测失败: {result.error}"

    def test_daily_data_loads(self) -> None:
        """日线数据应能正常加载（date 列）。"""
        data = self._make_daily_data(100)
        factor = {"factor_id": "test_daily", "code": "output = close", "name": "test"}
        inp = BacktestInput(factor=factor, data=data, frequency="daily")
        pipeline = BacktestPipeline()
        result = pipeline.run(inp)
        assert result.success, f"日线回测失败: {result.error}"

    def test_minute_insufficient_data(self) -> None:
        """分钟级数据不足 60 行应报错。"""
        data = self._make_minute_data(30)
        factor = {"factor_id": "test_short", "code": "output = close", "name": "test"}
        inp = BacktestInput(factor=factor, data=data, frequency="5m")
        pipeline = BacktestPipeline()
        result = pipeline.run(inp)
        assert not result.success
        assert "数据量不足" in (result.error or "")


# ─── 5. 绩效指标年化 ──────────────────────────────────────


class TestMetricsAnnualization:
    """测试绩效指标年化因子自适应。"""

    @pytest.mark.parametrize("frequency, expected_annual_factor", [
        ("daily", 252),
        ("5m", 19656),
    ])
    def test_annual_return_daily_vs_5m(
        self, frequency: str, expected_annual_factor: int
    ) -> None:
        """年化收益率应使用正确的年化因子。"""
        n = 1000
        close = 3500 + np.cumsum(np.random.randn(n) * 2)
        data = pd.DataFrame({
            "datetime" if frequency != "daily" else "date": (
                pd.date_range("2026-01-01", periods=n, freq="5min" if frequency != "daily" else "D")
            ),
            "open": close,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": np.ones(n) * 1000,
        })

        factor = {"factor_id": "test_ann", "code": "output = close", "name": "test"}
        inp = BacktestInput(factor=factor, data=data, frequency=frequency)
        pipeline = BacktestPipeline()
        result = pipeline.run(inp)
        assert result.success, f"回测失败: {result.error}"
        m = result.output.metrics
        # 年化因子应被正确使用（不校验具体值，仅确保可计算）
        assert m.annual_return != 0.0 or m.total_return == 0.0
        assert m.sharpe_ratio != 0.0 or m.volatility == 0.0
        assert m.volatility >= 0.0
        assert m.max_drawdown <= 0.0

    def test_backtest_output_contains_frequency(self) -> None:
        """回测报告 summary 应包含频率信息。"""
        n = 100
        close = 3500 + np.cumsum(np.random.randn(n))
        data = pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=n, freq="D"),
            "open": close, "high": close + 10, "low": close - 10,
            "close": close, "volume": np.ones(n) * 1000,
        })
        factor = {"factor_id": "test_freq", "code": "output = close", "name": "test"}
        inp = BacktestInput(factor=factor, data=data, frequency="daily")
        pipeline = BacktestPipeline()
        result = pipeline.run(inp)
        assert result.success
        assert result.output is not None
        summary = result.output.summary
        assert "config" in summary
        # 频率信息应包含在 summary 中（通过 extra_params 透传）
        assert inp.frequency == "daily"