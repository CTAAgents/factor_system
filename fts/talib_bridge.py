"""
fts/talib_bridge — TA-Lib 桥接层

提供 TA-Lib 常用技术指标的封装，优先使用 TA-Lib C 库加速，
不可用时优雅降级到 numpy/pandas 自实现。

支持指标: SMA, EMA, RSI, MACD, ATR, Bollinger Bands

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ─── TA-Lib 可用性检测 ─────────────────────────────────────

try:
    import talib  # type: ignore[import-untyped]

    _TALIB_AVAILABLE = True
except ImportError:  # pragma: no cover
    _TALIB_AVAILABLE = False
    talib = None  # type: ignore


def is_talib_available() -> bool:
    """检测 TA-Lib 是否可用。"""
    return _TALIB_AVAILABLE


# ─── TA-Lib 桥接 ───────────────────────────────────────────


class TalibBridge:
    """TA-Lib 桥接 — 优先使用 TA-Lib，降级到 numpy。

    用法:
        bridge = TalibBridge()
        sma = bridge.sma(close_prices, timeperiod=20)
        rsi = bridge.rsi(close_prices, timeperiod=14)
    """

    def __init__(self) -> None:
        self._available = _TALIB_AVAILABLE
        if not self._available:
            logger.info("TA-Lib 不可用，使用 numpy 降级实现")

    # ─── SMA / EMA ────────────────────────────────────────

    def sma(self, close: np.ndarray, timeperiod: int = 20) -> np.ndarray:
        """简单移动平均。"""
        close = np.ascontiguousarray(close, dtype=np.float64)
        if self._available:
            result = talib.SMA(close, timeperiod=timeperiod)
            return result
        return self._fallback_sma(close, timeperiod)

    def ema(self, close: np.ndarray, timeperiod: int = 20) -> np.ndarray:
        """指数移动平均。"""
        close = np.ascontiguousarray(close, dtype=np.float64)
        if self._available:
            result = talib.EMA(close, timeperiod=timeperiod)
            return result
        return self._fallback_ema(close, timeperiod)

    # ─── RSI ──────────────────────────────────────────────

    def rsi(self, close: np.ndarray, timeperiod: int = 14) -> np.ndarray:
        """相对强弱指数。"""
        close = np.ascontiguousarray(close, dtype=np.float64)
        if self._available:
            result = talib.RSI(close, timeperiod=timeperiod)
            return result
        return self._fallback_rsi(close, timeperiod)

    # ─── MACD ─────────────────────────────────────────────

    def macd(
        self, close: np.ndarray,
        fastperiod: int = 12, slowperiod: int = 26, signalperiod: int = 9,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """MACD 指标 — 返回 (macd, signal, hist)。"""
        close = np.ascontiguousarray(close, dtype=np.float64)
        if self._available:
            macd_line, signal_line, hist = talib.MACD(
                close,
                fastperiod=fastperiod,
                slowperiod=slowperiod,
                signalperiod=signalperiod,
            )
            return macd_line, signal_line, hist
        return self._fallback_macd(close, fastperiod, slowperiod, signalperiod)

    # ─── ATR ──────────────────────────────────────────────

    def atr(
        self, high: np.ndarray, low: np.ndarray, close: np.ndarray,
        timeperiod: int = 14,
    ) -> np.ndarray:
        """平均真实波幅。"""
        high = np.ascontiguousarray(high, dtype=np.float64)
        low = np.ascontiguousarray(low, dtype=np.float64)
        close = np.ascontiguousarray(close, dtype=np.float64)
        if self._available:
            return talib.ATR(high, low, close, timeperiod=timeperiod)
        return self._fallback_atr(high, low, close, timeperiod)

    # ─── Bollinger Bands ──────────────────────────────────

    def bollinger(
        self, close: np.ndarray,
        timeperiod: int = 20, nbdevup: float = 2.0, nbdevdn: float = 2.0,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """布林带 — 返回 (upper, middle, lower)。"""
        close = np.ascontiguousarray(close, dtype=np.float64)
        if self._available:
            upper, middle, lower = talib.BBANDS(
                close,
                timeperiod=timeperiod,
                nbdevup=nbdevup,
                nbdevdn=nbdevdn,
            )
            return upper, middle, lower
        return self._fallback_bollinger(close, timeperiod, nbdevup, nbdevdn)

    # ─── 降级实现 ─────────────────────────────────────────

    @staticmethod
    def _fallback_sma(close: np.ndarray, timeperiod: int) -> np.ndarray:
        """numpy SMA 降级实现。"""
        n = len(close)
        result = np.full(n, np.nan)
        if timeperiod > n:
            return result
        window_sum = np.sum(close[:timeperiod])
        result[timeperiod - 1] = window_sum / timeperiod
        for i in range(timeperiod, n):
            window_sum += close[i] - close[i - timeperiod]
            result[i] = window_sum / timeperiod
        return result

    @staticmethod
    def _fallback_ema(close: np.ndarray, timeperiod: int) -> np.ndarray:
        """numpy EMA 降级实现。"""
        n = len(close)
        result = np.full(n, np.nan)
        if timeperiod > n:
            return result
        alpha = 2.0 / (timeperiod + 1)
        result[timeperiod - 1] = np.mean(close[:timeperiod])
        for i in range(timeperiod, n):
            result[i] = alpha * close[i] + (1 - alpha) * result[i - 1]
        return result

    @staticmethod
    def _fallback_rsi(close: np.ndarray, timeperiod: int) -> np.ndarray:
        """numpy RSI 降级实现。"""
        n = len(close)
        result = np.full(n, np.nan)
        if timeperiod >= n:
            return result
        delta = np.diff(close)
        gains = np.where(delta > 0, delta, 0.0)
        losses = np.where(delta < 0, -delta, 0.0)
        # 初始平均
        avg_gain = np.mean(gains[:timeperiod])
        avg_loss = np.mean(losses[:timeperiod])
        if avg_loss < 1e-10:
            result[timeperiod] = 100.0
        else:
            rs = avg_gain / avg_loss
            result[timeperiod] = 100.0 - 100.0 / (1.0 + rs)
        # 平滑
        for i in range(timeperiod, len(delta)):
            avg_gain = (avg_gain * (timeperiod - 1) + gains[i]) / timeperiod
            avg_loss = (avg_loss * (timeperiod - 1) + losses[i]) / timeperiod
            if avg_loss < 1e-10:
                result[i + 1] = 100.0
            else:
                rs = avg_gain / avg_loss
                result[i + 1] = 100.0 - 100.0 / (1.0 + rs)
        return result

    @staticmethod
    def _fallback_macd(
        close: np.ndarray,
        fastperiod: int, slowperiod: int, signalperiod: int,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """numpy MACD 降级实现。"""
        n = len(close)
        ema_fast = TalibBridge._fallback_ema(close, fastperiod)
        ema_slow = TalibBridge._fallback_ema(close, slowperiod)
        macd_line = ema_fast - ema_slow
        # Signal = EMA of MACD
        valid_start = max(fastperiod, slowperiod) - 1
        signal_line = np.full(n, np.nan)
        if valid_start < n:
            signal_line[valid_start] = macd_line[valid_start]
            alpha = 2.0 / (signalperiod + 1)
            for i in range(valid_start + 1, n):
                signal_line[i] = alpha * macd_line[i] + (1 - alpha) * signal_line[i - 1]
        hist = macd_line - signal_line
        return macd_line, signal_line, hist

    @staticmethod
    def _fallback_atr(
        high: np.ndarray, low: np.ndarray, close: np.ndarray, timeperiod: int,
    ) -> np.ndarray:
        """numpy ATR 降级实现。"""
        n = len(close)
        result = np.full(n, np.nan)
        if timeperiod >= n:
            return result
        # True Range
        tr = np.zeros(n)
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            tr[i] = max(
                high[i] - low[i],
                abs(high[i] - close[i - 1]),
                abs(low[i] - close[i - 1]),
            )
        # ATR = SMA of TR
        result = TalibBridge._fallback_sma(tr, timeperiod)
        return result

    @staticmethod
    def _fallback_bollinger(
        close: np.ndarray, timeperiod: int, nbdevup: float, nbdevdn: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """numpy Bollinger Bands 降级实现。"""
        n = len(close)
        middle = TalibBridge._fallback_sma(close, timeperiod)
        upper = np.full(n, np.nan)
        lower = np.full(n, np.nan)
        for i in range(timeperiod - 1, n):
            window = close[i - timeperiod + 1 : i + 1]
            std = np.std(window)
            upper[i] = middle[i] + nbdevup * std
            lower[i] = middle[i] - nbdevdn * std
        return upper, middle, lower


__all__ = ["TalibBridge", "is_talib_available"]