"""fts.data_sources.tqsdk_enhance_source — 天勤 TQSDK 字段增强源（GAP-083 阶段 C 真实数据通路）。

背景:
    AKShare 回填已将 kline_cache 的 hold 覆盖 97.9%、settle 覆盖 86.6%（阶段 B）。
    阶段 C 用天勤 TQSDK 主连 K 线的 `close_oi`（收盘持仓量）提供**权威实时**持仓数据:
      - hold      ← close_oi（天勤为期货行情一手数据源）
      - oi_change ← close_oi 一阶差分（持仓变动）
    天勤 K 线**不提供** settle / pre_settle / amount → 本源不输出这些列，
    `_enhance_fields` 对不存在的列保持主路径值（AKShare 回填的 settle 不被覆盖）。

    与既有 `fts.data_sources.tqsdk_source.TQSDKSource` 的区别:
      - TQSDKSource 是 K 线主路径兜底源（分钟/日线，丢弃持仓量字段）
      - TQSDKEnhanceSource 是字段增强层（只补 hold/oi_change，不接管 K 线主路径）

HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法（fetch_ohlcv / fetch_quote / is_available）。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import pandas as pd

from fts.data_sources.base import BaseFuturesSource
from fts.data_sources.tqsdk_source import _import_tqsdk_safe

logger = logging.getLogger(__name__)

# 天勤主连 K 线持仓量字段名（get_kline_serial 返回 close_oi）
_OHLCV_COLS = ("open", "high", "low", "close", "volume")


class TQSDKEnhanceSource(BaseFuturesSource):
    """天勤 TQSDK 字段增强源（补充 hold/oi_change）。

    ⚠️ v3.0.0+1 起已从默认聚合器移除（K 线唯一数据源 QuantData）：
    hold 由 QuantData continuous_daily open_interest 权威覆盖（L0），
    oi_change 由 QuantDataProvider 从 hold 差分自算；本类保留供显式使用/兼容。
    不参与 K 线主路径（v3.0.0+1 起 K 线唯一数据源 QuantData）。
    """

    source_name: str = "TQSDK_ENHANCE"

    def __init__(self, duration_seconds: int = 86400) -> None:
        """初始化。

        Args:
            duration_seconds: 天勤 K 线周期（默认 86400 = 日线）
        """
        self._duration_seconds = duration_seconds

    # ─── 探活（不抛异常）──

    def is_available(self) -> bool:
        """探活：tqsdk 已安装且 .env 配置了天勤账号。"""
        try:
            _import_tqsdk_safe()
        except ImportError:
            return False
        return bool(os.environ.get("TQSDK_USERNAME") and os.environ.get("TQSDK_PASSWORD"))

    # ─── 代码映射 ──

    def _resolve_symbol(self, symbol: str) -> str:
        """FTS 品种代码 → 天勤主连格式（KQ.m@EXCHANGE.symbol）。

        映射键为带 0 后缀格式（如 RB0）；symbol 不带 0 时自动补 0 再查。
        未知品种返回原值（由天勤自行解析，失败降级）。
        """
        from fts.data_sources.tqsdk_source import _SYMBOL_MAP

        sym_upper = symbol.strip().upper()
        if sym_upper in _SYMBOL_MAP:
            return _SYMBOL_MAP[sym_upper]
        if f"{sym_upper}0" in _SYMBOL_MAP:
            return _SYMBOL_MAP[f"{sym_upper}0"]
        logger.warning("[%s] 未知品种映射: %s，尝试直接使用", self.source_name, symbol)
        return symbol

    # ─── 主接口 ──

    def fetch_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """拉取天勤主连日 K 线，返回含 hold/oi_change 的 DataFrame。

        返回列: date/open/high/low/close/volume/hold/oi_change + 元数据列。
        不输出 settle/pre_settle/amount（天勤无此数据，避免覆盖主路径回填值）。

        Args:
            symbol: FTS 品种代码（如 "RB0"）
            days: 回溯交易日数量
            trace_id: 链路追踪 ID

        Returns:
            DataFrame 或 None（失败/降级）
        """
        try:
            tqsdk = _import_tqsdk_safe()
        except ImportError:
            logger.warning("[%s] tqsdk 未安装，跳过字段增强", self.source_name)
            return None

        tq_sym = self._resolve_symbol(symbol)
        if tq_sym == symbol and "KQ.m@" not in symbol:
            logger.debug("[%s] %s 无天勤主连映射，跳过", self.source_name, symbol)
            return None

        try:
            from tqsdk import TqAuth

            tq_user = os.environ.get("TQSDK_USERNAME", "")
            tq_pass = os.environ.get("TQSDK_PASSWORD", "")
            if not (tq_user and tq_pass):
                logger.debug("[%s] 未配置 TQSDK_USERNAME/PASSWORD，跳过", self.source_name)
                return None
            api = tqsdk.TqApi(auth=TqAuth(tq_user, tq_pass))
            try:
                kline = api.get_kline_serial(tq_sym, duration_seconds=self._duration_seconds, data_length=days)
                api.wait_update(deadline=time.time() + 15)
                if kline is None or kline.empty:
                    logger.debug("[%s] %s 返回空数据", self.source_name, symbol)
                    return None
            finally:
                api.close()
        except Exception as e:
            logger.warning("[%s] TQSDK 获取失败 [%s]: %s", self.source_name, symbol, e)
            return None

        missing = [c for c in _OHLCV_COLS if c not in kline.columns]
        if missing:
            logger.warning("[%s] 响应缺必填字段: %s", self.source_name, missing)
            return None
        if "close_oi" not in kline.columns and "open_oi" not in kline.columns:
            logger.warning("[%s] %s 无持仓量字段（close_oi/open_oi），跳过", self.source_name, symbol)
            return None

        df = kline.copy()
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], unit="ns")
        df = df.dropna(subset=["datetime"]).sort_values("datetime")

        out = pd.DataFrame(index=df.index)
        out["date"] = df["datetime"].dt.date
        for col in _OHLCV_COLS:
            out[col] = pd.to_numeric(df[col], errors="coerce")
        # 持仓量：优先 close_oi（收盘持仓），缺失回退 open_oi；无效值（NaN/≤0）填 NaN
        if "close_oi" in df.columns:
            oi_raw = df["close_oi"].copy()
            if "open_oi" in df.columns:
                oi_raw = oi_raw.fillna(df["open_oi"])
        else:
            oi_raw = df["open_oi"]
        oi = pd.to_numeric(oi_raw, errors="coerce").replace(0, float("nan"))
        out["hold"] = oi
        # oi_change = 当日持仓 - 前日持仓；首行/断裂处 NaN → 0
        out["oi_change"] = oi.diff().fillna(0.0)

        out["symbol"] = symbol
        out["period"] = "daily"
        out["source"] = self.source_name
        out["fetched_at"] = pd.Timestamp.now()
        out["trace_id"] = trace_id
        out.reset_index(drop=True, inplace=True)

        if len(out) > days:
            out = out.tail(days).reset_index(drop=True)
        return out

    # ─── 快照（不支持）──

    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """实时快照：本源不支持，返回 None（增强层只补日频字段）。"""
        return None


__all__ = ["TQSDKEnhanceSource"]
