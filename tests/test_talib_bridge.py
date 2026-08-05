"""
tests/test_talib_bridge.py — TA-Lib 桥接层测试

验证:
  1. TA-Lib 可用性检测
  2. 降级实现正确性
  3. SMA/EMA/RSI/MACD/ATR/Bollinger 计算正确性
  4. 降级路径（TA-Lib 不可用时仍可工作）
"""

from __future__ import annotations

import numpy as np
import pytest

from fts.talib_bridge import TalibBridge, is_talib_available


# ─── Fixtures ─────────────────────────────────────────────

@pytest.fixture
def bridge() -> TalibBridge:
    return TalibBridge()


@pytest.fixture
def sample_close() -> np.ndarray:
    """确定性收盘价数据。"""
    rng = np.random.default_rng(42)
    n = 200
    t = np.arange(n, dtype=np.float64)
    return 100 + 10 * np.sin(t / 20) + rng.standard_normal(n)


@pytest.fixture
def sample_ohlc() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """确定性 OHLC 数据。"""
    rng = np.random.default_rng(123)
    n = 200
    t = np.arange(n, dtype=np.float64)
    close = 100 + 10 * np.sin(t / 20) + rng.standard_normal(n)
    high = close + np.abs(rng.standard_normal(n)) * 2
    low = close - np.abs(rng.standard_normal(n)) * 2
    return high, low, close


# ─── TA-Lib 可用性测试 ───────────────────────────────────


class TestTalibAvailability:
    def test_bridge_creation(self) -> None:
        """桥接层应可创建。"""
        bridge = TalibBridge()
        assert bridge is not None

    def test_is_talib_available(self) -> None:
        """TA-Lib 可用性检测。"""
        # 无论 TA-Lib 是否安装，函数应返回 bool
        result = is_talib_available()
        assert isinstance(result, bool)


# ─── SMA 测试 ────────────────────────────────────────────


class TestSMA:
    def test_sma_output_shape(self, bridge: TalibBridge, sample_close: np.ndarray) -> None:
        """SMA 输出形状正确。"""
        result = bridge.sma(sample_close, timeperiod=20)
        assert len(result) == len(sample_close)
        # 前 timeperiod-1 应为 NaN
        assert np.all(np.isnan(result[:19]))
        # 之后应有值
        assert not np.any(np.isnan(result[19:]))

    def test_sma_values(self, bridge: TalibBridge) -> None:
        """SMA 值计算正确。"""
        close = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = bridge.sma(close, timeperiod=3)
        # 第 3 个值 = (1+2+3)/3 = 2.0
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert abs(result[2] - 2.0) < 1e-10
        # 第 4 个值 = (2+3+4)/3 = 3.0
        assert abs(result[3] - 3.0) < 1e-10
        # 第 5 个值 = (3+4+5)/3 = 4.0
        assert abs(result[4] - 4.0) < 1e-10


# ─── EMA 测试 ────────────────────────────────────────────


class TestEMA:
    def test_ema_output_shape(self, bridge: TalibBridge, sample_close: np.ndarray) -> None:
        """EMA 输出形状正确。"""
        result = bridge.ema(sample_close, timeperiod=20)
        assert len(result) == len(sample_close)
        assert np.all(np.isnan(result[:19]))

    def test_ema_values(self, bridge: TalibBridge) -> None:
        """EMA 值计算正确。"""
        close = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = bridge.ema(close, timeperiod=3)
        # alpha = 2/(3+1) = 0.5
        # result[2] = mean(1,2,3) = 2.0
        assert abs(result[2] - 2.0) < 1e-10
        # result[3] = 0.5*4 + 0.5*2 = 3.0
        assert abs(result[3] - 3.0) < 1e-10
        # result[4] = 0.5*5 + 0.5*3 = 4.0
        assert abs(result[4] - 4.0) < 1e-10


# ─── RSI 测试 ────────────────────────────────────────────


class TestRSI:
    def test_rsi_output_range(self, bridge: TalibBridge, sample_close: np.ndarray) -> None:
        """RSI 输出在 0-100 范围内。"""
        result = bridge.rsi(sample_close, timeperiod=14)
        valid = result[~np.isnan(result)]
        assert len(valid) > 0
        assert np.all(valid >= 0)
        assert np.all(valid <= 100)

    def test_rsi_monotone_increase(self, bridge: TalibBridge) -> None:
        """持续上涨 → RSI 接近 100。"""
        close = np.arange(1.0, 50.0)  # 持续上涨
        result = bridge.rsi(close, timeperiod=14)
        valid = result[~np.isnan(result)]
        # 持续上涨应导致 RSI 接近 100
        assert np.all(valid > 90)

    def test_rsi_monotone_decrease(self, bridge: TalibBridge) -> None:
        """持续下跌 → RSI 接近 0。"""
        close = np.arange(50.0, 1.0, -1.0)  # 持续下跌
        result = bridge.rsi(close, timeperiod=14)
        valid = result[~np.isnan(result)]
        assert np.all(valid < 10)


# ─── MACD 测试 ───────────────────────────────────────────


class TestMACD:
    def test_macd_output_shape(self, bridge: TalibBridge, sample_close: np.ndarray) -> None:
        """MACD 输出形状正确。"""
        macd_line, signal_line, hist = bridge.macd(sample_close)
        assert len(macd_line) == len(sample_close)
        assert len(signal_line) == len(sample_close)
        assert len(hist) == len(sample_close)

    def test_macd_hist_is_diff(self, bridge: TalibBridge, sample_close: np.ndarray) -> None:
        """Histogram = MACD - Signal。"""
        macd_line, signal_line, hist = bridge.macd(sample_close)
        valid = ~np.isnan(macd_line) & ~np.isnan(signal_line) & ~np.isnan(hist)
        if np.any(valid):
            np.testing.assert_allclose(
                hist[valid],
                macd_line[valid] - signal_line[valid],
                atol=1e-10,
            )


# ─── ATR 测试 ───────────────────────────────────────────


class TestATR:
    def test_atr_output_shape(
        self, bridge: TalibBridge,
        sample_ohlc: tuple[np.ndarray, np.ndarray, np.ndarray],
    ) -> None:
        """ATR 输出形状正确。"""
        high, low, close = sample_ohlc
        result = bridge.atr(high, low, close, timeperiod=14)
        assert len(result) == len(close)
        valid = result[~np.isnan(result)]
        assert np.all(valid >= 0)

    def test_atr_constant_range(self, bridge: TalibBridge) -> None:
        """当 high=low=close 时 ATR = 0。"""
        n = 50
        high = np.full(n, 100.0)
        low = np.full(n, 100.0)
        close = np.full(n, 100.0)
        result = bridge.atr(high, low, close, timeperiod=14)
        valid = result[~np.isnan(result)]
        if len(valid) > 0:
            np.testing.assert_allclose(valid, 0.0, atol=1e-10)


# ─── Bollinger Bands 测试 ────────────────────────────────


class TestBollinger:
    def test_bollinger_output_shape(
        self, bridge: TalibBridge, sample_close: np.ndarray,
    ) -> None:
        """Bollinger Bands 输出形状正确。"""
        upper, middle, lower = bridge.bollinger(sample_close)
        assert len(upper) == len(sample_close)
        assert len(middle) == len(sample_close)
        assert len(lower) == len(sample_close)

    def test_bollinger_ordering(
        self, bridge: TalibBridge, sample_close: np.ndarray,
    ) -> None:
        """Upper >= Middle >= Lower。"""
        upper, middle, lower = bridge.bollinger(sample_close)
        valid = ~np.isnan(upper) & ~np.isnan(middle) & ~np.isnan(lower)
        if np.any(valid):
            assert np.all(upper[valid] >= middle[valid] - 1e-10)
            assert np.all(middle[valid] >= lower[valid] - 1e-10)

    def test_bollinger_middle_is_sma(
        self, bridge: TalibBridge, sample_close: np.ndarray,
    ) -> None:
        """Bollinger 中轨 = SMA。"""
        upper, middle, lower = bridge.bollinger(sample_close, timeperiod=20)
        sma = bridge.sma(sample_close, timeperiod=20)
        valid = ~np.isnan(middle) & ~np.isnan(sma)
        if np.any(valid):
            np.testing.assert_allclose(middle[valid], sma[valid], atol=1e-10)


# ─── 降级路径测试 ───────────────────────────────────────


class TestFallback:
    def test_fallback_sma_independent(self) -> None:
        """独立验证 SMA 降级实现。"""
        close = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        result = TalibBridge._fallback_sma(close, 3)
        expected = np.array([np.nan, np.nan, 20.0, 30.0, 40.0])
        np.testing.assert_allclose(result, expected, atol=1e-10, equal_nan=True)

    def test_fallback_ema_independent(self) -> None:
        """独立验证 EMA 降级实现。"""
        close = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        result = TalibBridge._fallback_ema(close, 3)
        # alpha = 0.5
        # result[2] = mean(10,20,30) = 20
        # result[3] = 0.5*40 + 0.5*20 = 30
        # result[4] = 0.5*50 + 0.5*30 = 40
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        assert abs(result[2] - 20.0) < 1e-10
        assert abs(result[3] - 30.0) < 1e-10
        assert abs(result[4] - 40.0) < 1e-10

    def test_fallback_atr_independent(self) -> None:
        """独立验证 ATR 降级实现。"""
        high = np.array([12.0, 15.0, 14.0, 16.0, 18.0])
        low = np.array([10.0, 13.0, 12.0, 14.0, 16.0])
        close = np.array([11.0, 14.0, 13.0, 15.0, 17.0])
        result = TalibBridge._fallback_atr(high, low, close, 3)
        assert len(result) == 5
        # 前 2 个应为 NaN
        assert np.isnan(result[0])
        assert np.isnan(result[1])
        # 之后应有值
        assert not np.isnan(result[4])