"""fts.data_sources.tqsdk_tick_source — 天勤 TQSDK tick 逐笔数据适配器（v2.31.0）。

通过 TQSDK `get_tick_serial` 获取期货逐笔行情（tick 级），包含 5 档盘口。
正序返回（旧→新），聚合器统一按 datetime 升序排序。

⚠️ v3.0.0+1 起已从默认聚合器移除（K 线唯一数据源 QuantData，因子生命周期管理
不需要 tick）；本类保留供显式使用/兼容。

注意:
- 免费账号 tick 历史极短（实测 ≈42 分钟 / 5000 行），适合近实时分析
- 与 TQSDKSource（K 线）不同，本适配器面向 tick 逐笔数据

HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法（fetch_ohlcv 返回 tick 数据）。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import pandas as pd

from fts.data_sources.base import BaseFuturesSource, SourceUnavailable
from fts.data_sources.tqsdk_source import _SYMBOL_MAP, _import_tqsdk_safe

logger = logging.getLogger(__name__)

# tick 返回列（tick_cache 对齐，见 docs/harness/plans/16-tick-data-source-plan.md §1.3）
TICK_COLUMNS: list[str] = [
    "symbol",
    "datetime",
    "last_price",
    "average",
    "highest",
    "lowest",
    "volume",
    "amount",
    "open_interest",
    "bid_price1",
    "bid_volume1",
    "ask_price1",
    "ask_volume1",
    "bid_price2",
    "bid_volume2",
    "ask_price2",
    "ask_volume2",
    "bid_price3",
    "bid_volume3",
    "ask_price3",
    "ask_volume3",
    "bid_price4",
    "bid_volume4",
    "ask_price4",
    "ask_volume4",
    "bid_price5",
    "bid_volume5",
    "ask_price5",
    "ask_volume5",
    "source",
    "fetched_at",
    "trace_id",
]

# TQSDK 免费账号 tick 最大行数
TICK_MAX_LENGTH: int = 5000


class TQSDKTickSource(BaseFuturesSource):
    """天勤 TQSDK tick 逐笔数据适配器。

    通过 `get_tick_serial` 获取逐笔行情，含 5 档盘口。
    认证: TqAuth(TQSDK_USERNAME, TQSDK_PASSWORD)，取自环境变量。
    """

    source_name: str = "TQSDK_TICK"

    def is_available(self) -> bool:
        """探活：检查 tqsdk 包是否已安装 + 账号配置。"""
        try:
            _import_tqsdk_safe()
        except ImportError:
            return False
        return bool(os.environ.get("TQSDK_USERNAME") and os.environ.get("TQSDK_PASSWORD"))

    def _resolve_symbol(self, symbol: str) -> str:
        """将 FTS 品种代码解析为 TQSDK 连续合约格式（复用 TQSDKSource 映射）。"""
        sym_upper = symbol.upper()
        if sym_upper in _SYMBOL_MAP:
            return _SYMBOL_MAP[sym_upper]
        logger.warning("[%s] 未知品种映射: %s，尝试直接使用", self.source_name, symbol)
        return symbol

    def fetch_ticks(
        self,
        symbol: str,
        count: int = TICK_MAX_LENGTH,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """获取 tick 逐笔数据。

        Args:
            symbol: 品种代码（如 "RB0"）
            count: tick 行数（免费账号上限 5000）
            trace_id: 链路追踪 ID

        Returns:
            含 TICK_COLUMNS schema 的 DataFrame，失败返回 None
        """
        try:
            tqsdk = _import_tqsdk_safe()
        except ImportError:
            logger.warning("[%s] tqsdk 未安装，请执行 pip install tqsdk", self.source_name)
            return None

        tq_sym = self._resolve_symbol(symbol)
        data_length = min(max(count, 1), TICK_MAX_LENGTH)

        try:
            from tqsdk import TqAuth

            api = tqsdk.TqApi(
                auth=TqAuth(
                    os.environ.get("TQSDK_USERNAME", ""),
                    os.environ.get("TQSDK_PASSWORD", ""),
                )
            )
            try:
                tick_data = api.get_tick_serial(tq_sym, data_length=data_length)
                # 等待数据更新（最多 15 秒）
                deadline = time.time() + 15
                api.wait_update(deadline=deadline)

                if tick_data is None or tick_data.empty:
                    logger.debug("[%s] %s 返回空 tick 数据", self.source_name, symbol)
                    return None
            finally:
                api.close()
        except Exception as e:
            raise SourceUnavailable(self.source_name, f"获取失败: {e}")

        df = tick_data.copy()

        # 时间列处理: datetime 为 int64 纳秒时间戳
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], unit="ns")
        else:
            logger.warning("[%s] 响应缺 datetime 字段", self.source_name)
            return None
        df = df.dropna(subset=["datetime"])

        # 统一数据类型（含 5 档盘口）
        df = df.copy()
        numeric_cols = (
            (
                "last_price",
                "average",
                "highest",
                "lowest",
                "volume",
                "amount",
                "open_interest",
            )
            + tuple(f"{side}_price{lvl}" for side in ("bid", "ask") for lvl in range(1, 6))
            + tuple(f"{side}_volume{lvl}" for side in ("bid", "ask") for lvl in range(1, 6))
        )
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 按时间排序（正序 旧→新）
        df = df.sort_values("datetime").reset_index(drop=True)

        # 添加元数据
        df["symbol"] = symbol
        df["source"] = self.source_name
        df["fetched_at"] = pd.Timestamp.now()
        df["trace_id"] = trace_id

        # 截取最近 count 行
        if len(df) > count:
            df = df.tail(count).reset_index(drop=True)

        # 返回 tick schema 列
        return df[[c for c in TICK_COLUMNS if c in df.columns]]

    def fetch_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """兼容 BaseFuturesSource 契约：返回 tick 数据（本适配器面向逐笔）。"""
        return self.fetch_ticks(symbol, count=min(days, TICK_MAX_LENGTH), trace_id=trace_id)

    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """获取最新 tick 快照。"""
        df = self.fetch_ticks(symbol, count=1, trace_id=trace_id)
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        return {
            "symbol": symbol,
            "source": self.source_name,
            "trace_id": trace_id,
            "fetched_at": pd.Timestamp.now().isoformat(),
            "last_price": float(row["last_price"]) if pd.notna(row["last_price"]) else None,
            "bid_price1": float(row["bid_price1"]) if pd.notna(row["bid_price1"]) else None,
            "ask_price1": float(row["ask_price1"]) if pd.notna(row["ask_price1"]) else None,
        }


__all__ = ["TQSDKTickSource", "TICK_COLUMNS", "TICK_MAX_LENGTH"]
