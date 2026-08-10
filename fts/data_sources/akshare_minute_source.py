"""fts.data_sources.akshare_minute_source — AKShare 分钟 K 线适配器（v2.29.0）。

通过 AKShare futures_zh_minute_sina 获取期货分钟级 K 线数据。
支持 1 分钟、5 分钟、15 分钟、30 分钟、60 分钟五个周期。

HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

from fts.data_sources.base import BaseFuturesSource, SourceUnavailable

logger = logging.getLogger(__name__)

# 支持的分钟周期
SUPPORTED_PERIODS: dict[str, str] = {
    "1m": "1",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "60m": "60",
}

# 年化因子（按 trading days × 每分钟 bars 数）
FREQUENCY_ANNUALIZATION: dict[str, float] = {
    "daily": 252.0,
    "1m": 252.0 * 390.0,  # 390 分钟/交易日
    "5m": 252.0 * 78.0,  # 78 个 5分/交易日
    "15m": 252.0 * 26.0,  # 26 个 15分/交易日
    "30m": 252.0 * 13.0,  # 13 个 30分/交易日
    "60m": 252.0 * 6.5,  # 6.5 个 60分/交易日
}


def get_annualization_factor(frequency: str) -> float:
    """获取指定频率的年化因子。"""
    return FREQUENCY_ANNUALIZATION.get(frequency, 252.0)


def get_default_zscore_window(frequency: str) -> int:
    """获取指定频率的默认 z-score 窗口（对应约 20 个交易日）。"""
    annual = get_annualization_factor(frequency)
    return max(20, int(20 * annual / 252.0))


class AKShareMinuteSource(BaseFuturesSource):
    """AKShare 分钟 K 线适配器。

    通过新浪财经接口获取期货分钟级 K 线数据。
    每个周期固定返回 1023 行数据。
    """

    source_name: str = "AKSHARE_MINUTE"

    def __init__(self, period: str = "1m") -> None:
        """初始化。

        Args:
            period: 分钟周期，支持 "1m" / "5m" / "15m" / "30m" / "60m"
        """
        if period not in SUPPORTED_PERIODS:
            raise ValueError(f"不支持的分钟周期: {period}，可选: {list(SUPPORTED_PERIODS.keys())}")
        self._period = period
        self._akshare_period = SUPPORTED_PERIODS[period]

    @property
    def period(self) -> str:
        return self._period

    def is_available(self) -> bool:
        """探活：尝试加载 akshare 模块。"""
        try:
            import akshare as ak  # noqa: F401

            return True
        except ImportError:
            return False

    def fetch_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """获取分钟 K 线数据。

        Args:
            symbol: 品种代码（如 "RB0"）
            days: 回溯天数（AKShare 固定返回 1023 行，忽略此参数）
            trace_id: 链路追踪 ID

        Returns:
            DataFrame 或 None
        """
        try:
            import akshare as ak
        except ImportError as e:
            raise SourceUnavailable(self.source_name, f"akshare 模块不可用: {e}")

        try:
            df = ak.futures_zh_minute_sina(symbol=symbol, period=self._akshare_period)
        except Exception as e:
            raise SourceUnavailable(self.source_name, f"AKShare 分钟数据获取失败: {e}")

        if df is None or df.empty:
            return None

        # 统一格式
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)

        # 确保数值类型
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["volume"] = df["volume"].astype(float)
        df["hold"] = df["hold"].astype(float)

        # 添加元数据列
        df["symbol"] = symbol
        df["period"] = self._period
        df["source"] = self.source_name
        df["fetched_at"] = pd.Timestamp.now()
        df["trace_id"] = trace_id

        # 截取最近 days 行
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)

        return df

    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """获取实时快照（分钟源不做快照，委托给 akshare 日线）。"""
        try:
            import akshare as ak

            df = ak.futures_zh_minute_sina(symbol=symbol, period="1")
            if df is not None and not df.empty:
                last = df.iloc[-1]
                return {
                    "symbol": symbol,
                    "datetime": str(last["datetime"]),
                    "last_price": float(last["close"]),
                    "open": float(last["open"]),
                    "high": float(last["high"]),
                    "low": float(last["low"]),
                    "volume": float(last["volume"]),
                    "hold": float(last["hold"]),
                    "source": self.source_name,
                    "trace_id": trace_id,
                    "fetched_at": pd.Timestamp.now().isoformat(),
                }
        except Exception:  # noqa: BLE001
            pass
        return None


__all__ = [
    "AKShareMinuteSource",
    "SUPPORTED_PERIODS",
    "FREQUENCY_ANNUALIZATION",
    "get_annualization_factor",
    "get_default_zscore_window",
]
