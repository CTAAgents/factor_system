"""fts.data_sources.tqsdk_source — 天勤 TQSDK 数据源适配器（v2.30.0）。

TQSDK（天勤量化）: 通过 Python SDK 获取期货行情数据。
支持分钟/日线 K 线，正序返回（旧→新）。

注意: TQSDK 与 TQ-Local（通达信本地 HTTP 7721）是两个完全不同的数据源。
- TQSDK: pip install tqsdk，通过 tqsdk 包连接天勤服务器
- TQ-Local: 通达信本地客户端 HTTP 服务，端口 7721

HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

import pandas as pd

from fts.data_sources.base import BaseFuturesSource, SourceUnavailable

logger = logging.getLogger(__name__)

# 天勤 TQSDK 支持的周期（秒）
SUPPORTED_PERIODS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "60m": 3600,
    "day": 86400,
}

# 品种代码 → TQSDK 连续合约格式映射
# 天勤连续合约格式: "KQ.m@{EXCHANGE}.{symbol}"
# 交易所代码: SHFE / DCE / CZCE / INE / CFFEX
# 品种代码小写
_SYMBOL_MAP: dict[str, str] = {
    # 上期所 (shfe)
    "CU0": "KQ.m@SHFE.cu", "AL0": "KQ.m@SHFE.al", "ZN0": "KQ.m@SHFE.zn",
    "NI0": "KQ.m@SHFE.ni", "SN0": "KQ.m@SHFE.sn", "PB0": "KQ.m@SHFE.pb",
    "AU0": "KQ.m@SHFE.au", "AG0": "KQ.m@SHFE.ag", "RB0": "KQ.m@SHFE.rb",
    "HC0": "KQ.m@SHFE.hc", "SS0": "KQ.m@SHFE.ss", "RU0": "KQ.m@SHFE.ru",
    "BU0": "KQ.m@SHFE.bu", "FU0": "KQ.m@SHFE.fu", "SP0": "KQ.m@SHFE.sp",
    "WR0": "KQ.m@SHFE.wr",
    # 大商所 (dce)
    "V0": "KQ.m@DCE.v", "P0": "KQ.m@DCE.p", "B0": "KQ.m@DCE.b",
    "M0": "KQ.m@DCE.m", "I0": "KQ.m@DCE.i", "JD0": "KQ.m@DCE.jd",
    "L0": "KQ.m@DCE.l", "PP0": "KQ.m@DCE.pp", "FB0": "KQ.m@DCE.fb",
    "BB0": "KQ.m@DCE.bb", "Y0": "KQ.m@DCE.y", "A0": "KQ.m@DCE.a",
    "C0": "KQ.m@DCE.c", "CS0": "KQ.m@DCE.cs", "J0": "KQ.m@DCE.j",
    "JM0": "KQ.m@DCE.jm", "EG0": "KQ.m@DCE.eg", "EB0": "KQ.m@DCE.eb",
    "PG0": "KQ.m@DCE.pg", "LH0": "KQ.m@DCE.lh", "RR0": "KQ.m@DCE.rr",
    "PK0": "KQ.m@DCE.pk",
    # 郑商所 (czce)
    "TA0": "KQ.m@CZCE.TA", "MA0": "KQ.m@CZCE.MA", "CF0": "KQ.m@CZCE.CF",
    "SR0": "KQ.m@CZCE.SR", "OI0": "KQ.m@CZCE.OI", "RM0": "KQ.m@CZCE.RM",
    "ZC0": "KQ.m@CZCE.ZC", "AP0": "KQ.m@CZCE.AP", "SF0": "KQ.m@CZCE.SF",
    "SM0": "KQ.m@CZCE.SM", "CY0": "KQ.m@CZCE.CY", "FG0": "KQ.m@CZCE.FG",
    "JR0": "KQ.m@CZCE.JR", "LR0": "KQ.m@CZCE.LR", "RI0": "KQ.m@CZCE.RI",
    "WH0": "KQ.m@CZCE.WH", "PM0": "KQ.m@CZCE.PM", "UR0": "KQ.m@CZCE.UR",
    "SA0": "KQ.m@CZCE.SA", "PF0": "KQ.m@CZCE.PF", "CJ0": "KQ.m@CZCE.CJ",
    "PX0": "KQ.m@CZCE.PX", "SH0": "KQ.m@CZCE.SH",
    # 能源中心 (ine)
    "SC0": "KQ.m@INE.sc", "LU0": "KQ.m@INE.lu", "NR0": "KQ.m@INE.nr",
    "BC0": "KQ.m@INE.bc",
    # 中金所 (cffex)
    "IF0": "KQ.m@CFFEX.IF", "IH0": "KQ.m@CFFEX.IH", "IC0": "KQ.m@CFFEX.IC",
    "TF0": "KQ.m@CFFEX.TF", "T0": "KQ.m@CFFEX.T", "TS0": "KQ.m@CFFEX.TS",
    "TL0": "KQ.m@CFFEX.TL",
}


class TQSDKSource(BaseFuturesSource):
    """天勤 TQSDK 数据源适配器（Python SDK）。

    通过 tqsdk 包直接获取期货行情数据。
    支持分钟/日线 K 线，正序返回（旧→新）。
    取数完成后关闭 TQSDK 连接以释放资源。
    """

    source_name: str = "TQSDK"

    def __init__(self, period: str = "day") -> None:
        """初始化。

        Args:
            period: 周期，支持 "day" / "1m" / "5m" / "15m" / "30m" / "60m"
        """
        if period not in SUPPORTED_PERIODS:
            raise ValueError(
                f"不支持的周期: {period}，可选: {list(SUPPORTED_PERIODS.keys())}"
            )
        self._period = period
        self._duration_seconds = SUPPORTED_PERIODS[period]

    @property
    def period(self) -> str:
        return self._period

    def is_available(self) -> bool:
        """探活：检查 tqsdk 包是否已安装。"""
        try:
            import tqsdk  # noqa: F401
            return True
        except ImportError:
            return False

    def _resolve_symbol(self, symbol: str) -> str:
        """将 FTS 品种代码解析为 TQSDK 连续合约格式。

        Args:
            symbol: FTS 品种代码（如 "RB0"）

        Returns:
            TQSDK 连续合约代码（如 "KQ.m@SHFE.rb"）
        """
        sym_upper = symbol.upper()
        if sym_upper in _SYMBOL_MAP:
            return _SYMBOL_MAP[sym_upper]
        # 未知品种，尝试直接使用
        logger.warning("[%s] 未知品种映射: %s，尝试直接使用", self.source_name, symbol)
        return symbol

    def fetch_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """获取 K 线数据。

        TQSDK v3.x 使用 get_kline_serial() 接口获取 K 线。
        注意: TQSDK 返回数据为**正序**（旧→新），与 TQ-Local 的倒序不同。
        聚合器统一按 datetime 升序排序，无需额外处理。

        Args:
            symbol: 品种代码（如 "RB0"）
            days: 返回的 K 线数量
            trace_id: 链路追踪 ID

        Returns:
            DataFrame 或 None
        """
        try:
            import tqsdk  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("[%s] tqsdk 未安装，请执行 pip install tqsdk", self.source_name)
            return None

        tq_sym = self._resolve_symbol(symbol)

        try:
            # 从环境变量读取天勤账号密码
            tq_user = os.environ.get("TQSDK_USERNAME", "")
            tq_pass = os.environ.get("TQSDK_PASSWORD", "")
            if tq_user and tq_pass:
                from tqsdk import TqAuth
                auth = TqAuth(tq_user, tq_pass)
                api = tqsdk.TqApi(auth=auth)
            else:
                logger.warning("[%s] 未配置 TQSDK_USERNAME/PASSWORD，尝试匿名访问", self.source_name)
                api = tqsdk.TqApi()

            try:
                # 获取 K 线序列（get_kline_serial 返回 DataFrame）
                kline_data = api.get_kline_serial(
                    tq_sym,
                    duration_seconds=self._duration_seconds,
                    data_length=days,
                )

                # 等待数据更新（最多 15 秒）
                deadline = time.time() + 15
                api.wait_update(deadline=deadline)

                if kline_data is None or kline_data.empty:
                    logger.debug("[%s] %s 返回空数据", self.source_name, symbol)
                    return None
            finally:
                api.close()
        except Exception as e:
            raise SourceUnavailable(self.source_name, f"获取失败: {e}")

        df = kline_data.copy()

        # 必填字段校验
        required = ("open", "high", "low", "close", "volume")
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning("[%s] 响应缺必填字段: %s", self.source_name, missing)
            return None

        # 时间列处理: get_kline_serial 返回的 datetime 为 int64 纳秒时间戳
        if "datetime" in df.columns:
            # 纳秒 → datetime
            df["datetime"] = pd.to_datetime(df["datetime"], unit="ns")
        elif "symbol" in df.columns and "datetime" not in df.columns:
            # 尝试从 index 获取
            df["datetime"] = pd.to_datetime(df.index)
        df = df.dropna(subset=["datetime"])

        # 统一数据类型
        df = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # 按时间排序（TQSDK 正序返回，但为保险仍排序）
        df = df.sort_values("datetime").reset_index(drop=True)

        # 添加元数据
        df["symbol"] = symbol
        df["period"] = self._period
        df["source"] = self.source_name
        df["fetched_at"] = pd.Timestamp.now()
        df["trace_id"] = trace_id

        # 截取最近 days 行
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)

        # 返回分钟级 schema 列
        cols = ["symbol", "period", "datetime", "open", "high", "low",
                "close", "volume", "source", "fetched_at", "trace_id"]
        return df[[c for c in cols if c in df.columns]]

    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """获取实时快照（TQSDK 暂不支持，通过 TQ-Local 替代）。"""
        return None


__all__ = ["TQSDKSource", "SUPPORTED_PERIODS"]