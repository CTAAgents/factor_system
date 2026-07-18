"""
fts.data — FTS 数据层集成入口

包装 Data-Core 的 UnifiedDataProvider，为 FTS 因子引擎提供统一数据访问接口。
遵循 HARNESS §契约优先：所有数据接口通过本模块定义，不直接调用 Data-Core。

依赖: datacore (optional, data extra)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 数据不可用异常 ───────────────────────────────────────

class DataUnavailableError(RuntimeError):
    """数据不可用 — 所有数据源均失效时抛出。"""
    pass


# ─── 数据引用 ─────────────────────────────────────────────

@dataclass
class DataRef:
    """数据引用 — 记录数据的来源和质量信息。

    Attributes:
        source: 数据源名称（如 "eastmoney", "tdx_lc"）
        grade: 数据质量等级（PRIMARY/DAILY/CACHED/STALE/UNAVAILABLE）
        fetched_at: 获取时间 ISO 8601
        trace_id: HARNESS trace_id
    """
    source: str = ""
    grade: str = "UNAVAILABLE"
    fetched_at: str = ""
    trace_id: str = ""


# ─── FTS 数据提供者 ───────────────────────────────────────

class FTSDataProvider:
    """FTS 统一数据提供者 — 包装 Data-Core UnifiedDataProvider。

    职责:
        - 提供因子计算所需的 OHLCV / 基本面 / 宏观 / 新闻数据
        - 所有数据以 pandas DataFrame 格式返回（兼容 factor_program 接口）
        - 多源降级：PRIMARY → DAILY → CACHED → STALE → UNAVAILABLE
        - 全链路 trace_id 传播

    用法:
        provider = FTSDataProvider()
        ohlcv = provider.get_ohlcv("RB", days=500)
        df = provider.get_fundamental("RB", indicator="operating_income")
    """

    def __init__(self, datacore_provider: Optional[Any] = None,
                 data_dir: Optional[str] = None):
        self._dc = datacore_provider
        self._data_dir = Path(data_dir) if data_dir else Path.cwd() / "data"

    # ── Data-Core 延迟导入 ──

    @property
    def _provider(self) -> Any:
        """获取 UnifiedDataProvider 实例（延迟导入）。"""
        if self._dc is not None:
            return self._dc
        try:
            from datacore import UnifiedDataProvider
            self._dc = UnifiedDataProvider()
            return self._dc
        except ImportError:
            raise DataUnavailableError(
                "Data-Core 未安装。请执行: pip install fts[data]"
            )

    # ── 核心获取接口 ──

    def get_ohlcv(self, symbol: str, *,
                  days: int = 500,
                  period: str = "daily",
                  trace_id: str = "",
                  ) -> pd.DataFrame:
        """获取 OHLCV K线数据。

        Args:
            symbol: 品种代码
            days: 回溯天数
            period: 周期（daily / hourly / minute）
            trace_id: HARNESS trace_id

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume
            Index: DatetimeIndex

        Raises:
            DataUnavailableError: 所有数据源不可用
        """
        try:
            payload = self._provider.get(
                symbol, "ohlcv",
                params={"days": days, "period": period},
            )
            return self._payload_to_ohlcv_df(payload)
        except Exception as e:
            raise DataUnavailableError(f"OHLCV 获取失败 [{symbol}]: {e}")

    def get_fundamental(self, symbol: str, *,
                        indicator: str = "",
                        trace_id: str = "",
                        ) -> dict[str, Any]:
        """获取基本面数据。

        Args:
            symbol: 品种代码
            indicator: 指标名称（如 "operating_income", "equity"）, 空=全部
            trace_id: trace_id

        Returns:
            dict {indicator_name: value_or_series}
        """
        try:
            payload = self._provider.get(
                symbol, "financial",
                params={"indicator": indicator} if indicator else None,
            )
            return self._payload_to_dict(payload)
        except Exception as e:
            logger.warning(f"基本面数据不可用 [{symbol}/{indicator}]: {e}")
            return {}

    def get_macro(self, indicator: str = "",
                  trace_id: str = "") -> dict[str, Any]:
        """获取宏观数据。

        Args:
            indicator: "pmi" / "lpr" / "cpi" / "gdp" / ""(全部)
            trace_id: trace_id

        Returns:
            dict {indicator: value_or_series}
        """
        try:
            payload = self._provider.get(
                "*", "macro",
                params={"indicator": indicator} if indicator else None,
            )
            return self._payload_to_dict(payload)
        except Exception as e:
            logger.warning(f"宏观数据不可用 [{indicator}]: {e}")
            return {}

    def get_news(self, symbol: str = "", *,
                 days: int = 7,
                 trace_id: str = "") -> list[dict]:
        """获取新闻资讯（Data-Core 数据加工层已分类）。

        Args:
            symbol: 品种代码（""=全市场）
            days: 回溯天数
            trace_id: trace_id

        Returns:
            list[dict]: 每条含 title, content, published_at, tags, related_symbols
        """
        try:
            payload = self._provider.get(
                symbol or "*", "news",
                params={"days": days},
            )
            data = self._extract_data(payload)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("items", data.get("news", []))
            return []
        except Exception as e:
            logger.warning(f"新闻数据不可用 [{symbol}]: {e}")
            return []

    def get_sentiment(self, symbol: str = "", *,
                      days: int = 30,
                      trace_id: str = "") -> dict[str, Any]:
        """获取情绪数据（Data-Core 数据加工层产出）。

        Args:
            symbol: 品种代码
            days: 回溯天数
            trace_id: trace_id

        Returns:
            dict {date: {score, volume, topics}}
        """
        try:
            payload = self._provider.get(
                symbol or "*", "sentiment",
                params={"days": days},
            )
            return self._payload_to_dict(payload)
        except Exception as e:
            logger.warning(f"情绪数据不可用 [{symbol}]: {e}")
            return {}

    def get_market_state(self, symbol: str = "",
                         trace_id: str = "") -> dict[str, Any]:
        """获取市场制度状态（Data-Core 数据加工层产出）。

        Returns:
            dict {regime, confidence, features}
        """
        try:
            payload = self._provider.get(
                symbol or "*", "market_state",
            )
            return self._payload_to_dict(payload)
        except Exception as e:
            logger.warning(f"市场制度数据不可用 [{symbol}]: {e}")
            return {}

    def list_symbols(self, market: str = "futures") -> list[str]:
        """列出可用品种代码。"""
        try:
            return self._provider.list_symbols(market=market)
        except Exception as e:
            logger.warning(f"列表不可用 [{market}]: {e}")
            return []

    # ── 批量获取 ──

    def get_batch_ohlcv(self, symbols: list[str], *,
                        days: int = 500,
                        trace_id: str = "",
                        ) -> dict[str, pd.DataFrame]:
        """批量获取 OHLCV。"""
        result: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                result[sym] = self.get_ohlcv(sym, days=days, trace_id=trace_id)
            except DataUnavailableError:
                continue
        return result

    # ── 内置降级数据生成（开发/测试用） ──

    def synthesize_ohlcv(self, n_days: int = 500,
                         base_price: float = 100.0,
                         seed: int = 42) -> pd.DataFrame:
        """合成 OHLCV 数据（无 Data-Core 时使用）。"""
        np.random.seed(seed)
        dates = pd.date_range(
            datetime.now() - timedelta(days=n_days),
            periods=n_days, freq="D",
        )
        close = base_price + np.cumsum(np.random.randn(n_days) * 0.5)
        return pd.DataFrame({
            "open": close + np.random.randn(n_days) * 0.1,
            "high": close + np.abs(np.random.randn(n_days)) * 0.3,
            "low": close - np.abs(np.random.randn(n_days)) * 0.3,
            "close": close,
            "volume": np.random.randint(1000, 10000, n_days).astype(float),
        }, index=dates)

    # ── 内部 helper ──

    @staticmethod
    def _extract_data(payload: Any) -> Any:
        """提取载荷中的实际数据。

        兼容 DataPayload（.data 属性）和原始 dict/DataFrame/list 格式。
        """
        if hasattr(payload, "data"):
            return payload.data
        if hasattr(payload, "payload"):
            return payload.payload
        return payload

    @staticmethod
    def _payload_to_ohlcv_df(payload: Any) -> pd.DataFrame:
        """Data-Core 载荷 → OHLCV DataFrame。

        兼容 DataPayload 和原始 dict/DataFrame 格式。
        """
        data = FTSDataProvider._extract_data(payload)

        if isinstance(data, pd.DataFrame):
            return data

        if isinstance(data, dict):
            df = pd.DataFrame(data)
            if "date" in df.columns:
                df.set_index("date", inplace=True)
            return df

        if isinstance(data, list):
            df = pd.DataFrame(data)
            if "date" in df.columns:
                df.set_index("date", inplace=True)
            return df

        # 回退：返回空的 DataFrame
        logger.warning(f"无法解析 OHLCV 载荷类型: {type(data)}")
        return pd.DataFrame()

    @staticmethod
    def _payload_to_dict(payload: Any) -> dict:
        """Data-Core 载荷 → dict。"""
        data = FTSDataProvider._extract_data(payload)
        if isinstance(data, dict):
            return data
        return {"raw": data}


# ─── 缺省实例（全局单例）───────────────────────────────────

_default_provider: Optional[FTSDataProvider] = None


def get_data_provider(datacore_provider: Any = None) -> FTSDataProvider:
    """获取全局 FTSDataProvider 实例。"""
    global _default_provider
    if _default_provider is None:
        _default_provider = FTSDataProvider(datacore_provider=datacore_provider)
    return _default_provider
