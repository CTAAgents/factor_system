"""fts.data_sources.tq_source — 通达信 TQ-Local HTTP 适配器（v2.3.0）。

TQ-Local: 通达信本地客户端 HTTP 服务，默认端口 7721。
协议: JSON-RPC 2.0 over HTTP POST。
端点: http://127.0.0.1:7721/rpc
方法: tq_get_kline / tq_get_quote

代码转换: FTS `RB0` (主力连续) ↔ TQ `RB0.SHFE` (上期所)。
HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import pandas as pd
import requests
from requests.exceptions import ConnectionError, Timeout, RequestException

from fts.data_sources.base import BaseFuturesSource, SourceUnavailable

logger = logging.getLogger(__name__)


# ─── 期货代码 → 交易所推断 ──────────────────────────────


# 优先 2 字母匹配（CZCE 品种多以 2 字母起，TQ 协议规则）
# CFFEX 实际合约：IF/IH/IC/TF/TS/TL（注意：没有单独的 "T"，2 字母起）
CFFEX_PREFIXES = ("IF", "IH", "IC", "TF", "TS", "TL")
CZCE_PREFIXES = (
    "SR",
    "CF",
    "CJ",
    "CY",
    "AP",
    "ER",
    "LR",
    "LW",
    "MR",
    "ME",
    "PM",
    "RI",
    "RM",
    "RS",
    "SF",
    "SM",
    "TA",
    "TM",
    "UR",
    "WH",
    "WS",
    "WT",
    "ZC",
    "GN",
    "RO",
    "PF",
    "PK",
    "PX",
    "SA",
    "SH",
    "TC",
    "JR",
    "OI",
    "MA",
    "FG",  # MA/FG/OI 是郑商所
)
# 上期所 (SHFE) — 优先按 2 字母匹配
SHFE_PREFIXES = (
    "RB",
    "CU",
    "AL",
    "ZN",
    "AU",
    "AG",
    "PB",
    "NI",
    "SN",
    "SS",
    "BU",
    "RU",
    "FU",
    "SP",
    "WR",
    "HC",
    "FB",
    "BB",
    "AO",
    "AD",
    "BC",
    "EC",
    "AU",
    "AG",
)
# 大商所 (DCE) — 1 字母品种为主（如 A/B/C/M/Y/P/I/J/L/V/R）
DCE_PREFIXES = (
    "A",
    "B",
    "C",
    "CS",
    "M",
    "Y",
    "P",
    "L",
    "JD",
    "JM",
    "I",
    "J",
    "R",
    "RR",
    "V",
    "EG",
    "EB",
    "PG",
    "LH",
    "FB",
)


def _infer_exchange(symbol: str) -> str:
    """根据品种代码前缀推断交易所。

    匹配顺序（关键 — 避免 TA 误判为 T+CFFEX）:
        1. CFFEX 2 字母（IF/IH/IC/TF/TS/TL）
        2. CZCE 2 字母（TA/MA/SR/...）
        3. SHFE 2 字母（RB/CU/AU/...）
        4. DCE 1 字母（A/B/C/M/Y/I/J/...）
        5. CFFEX 1 字母（T = 10 年期国债，最后兜底避免误覆盖 TA）
    """
    sym = symbol.upper()
    for p in CFFEX_PREFIXES:
        if sym.startswith(p):
            return "CFFEX"
    for p in CZCE_PREFIXES:
        if sym.startswith(p):
            return "CZCE"
    for p in SHFE_PREFIXES:
        if sym.startswith(p):
            return "SHFE"
    for p in DCE_PREFIXES:
        if sym.startswith(p):
            return "DCE"
    # 1 字母兜底（T = CFFEX 10 年期国债）
    if sym.startswith("T"):
        return "CFFEX"
    raise ValueError(f"未知交易所: {symbol}")


# ─── 适配器 ──────────────────────────────────────────────


class TQLocalSource(BaseFuturesSource):
    """通达信 TQ-Local HTTP 适配器（端口 7721）。

    协议: JSON-RPC 2.0 over HTTP POST。
    端点: http://127.0.0.1:7721/rpc。
    方法: tq_get_kline / tq_get_quote。

    支持周期: "day"（日线，默认）/ "1m" / "5m" / "15m" / "30m" / "60m"
    注意: TQ-Local 返回分钟数据为**倒序**（新→旧），聚合器统一反转。
    """

    source_name: str = "TQ_LOCAL"
    base_url: str = "http://127.0.0.1:7721"
    rpc_url: str = "http://127.0.0.1:7721/rpc"
    timeout: float = 5.0

    # 支持周期
    TQ_PERIOD_MAP: dict[str, str] = {
        "day": "day",
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "60m": "60m",
    }

    # ─── 代码转换（公开静态方法，供聚合器复用）──

    @staticmethod
    def _symbol_to_tq(symbol: str) -> str:
        """FTS 代码 → TQ 代码（RB0 → RB0.SHFE）。"""
        return f"{symbol.upper()}.{_infer_exchange(symbol)}"

    @staticmethod
    def _expected_columns() -> list[str]:
        """FTS kline_cache 完整 17 列。"""
        return [
            "symbol",
            "period",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "hold",
            "settle",
            "pre_settle",
            "oi_change",
            "vwap",
            "source",
            "fetched_at",
            "trace_id",
        ]

    # ─── 构造 ──

    def __init__(self, period: str = "day") -> None:
        """初始化。

        Args:
            period: 周期，支持 "day"（默认）/ "1m" / "5m" / "15m" / "30m" / "60m"
        """
        if period not in self.TQ_PERIOD_MAP:
            raise ValueError(f"不支持的周期: {period}，可选: {list(self.TQ_PERIOD_MAP.keys())}")
        self._period = period

    @property
    def period(self) -> str:
        return self._period

    # ─── 探活（不抛异常）──

    def is_available(self) -> bool:
        """探活：轻量 RPC 调用。连接失败/超时 → False。

        使用 tq_get_quote 方法查询一个已知品种，而非 GET 根路径。
        TQ-Local 是 JSON-RPC 服务，仅接受 POST 请求至 /rpc 端点。
        """
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tq_get_quote",
                "params": {"symbol": "RB0.SHFE"},
            }
            r = requests.post(self.rpc_url, json=payload, timeout=2.0)
            return r.status_code == 200
        except (ConnectionError, Timeout, RequestException):
            return False
        except Exception:  # noqa: BLE001
            return False

    # ─── K 线（抛 SourceUnavailable 供熔断）──

    def fetch_ohlcv(
        self,
        symbol: str,
        days: int,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """拉取 K 线数据。

        日线返回含 17 字段的 DataFrame（kline_cache schema）。
        分钟线返回含 11 字段的 DataFrame（minute_cache schema，datetime 列）。
        """
        tq_sym = self._symbol_to_tq(symbol)
        tq_period = self.TQ_PERIOD_MAP[self._period]
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tq_get_kline",
            "params": {"symbol": tq_sym, "days": days, "period": tq_period},
        }

        # HTTP 调用
        try:
            resp = requests.post(self.rpc_url, json=payload, timeout=self.timeout)
            if resp.status_code >= 400:
                raise SourceUnavailable(self.source_name, f"HTTP {resp.status_code}")
            data = resp.json()
        except (ConnectionError, Timeout) as e:
            raise SourceUnavailable(self.source_name, str(e))
        except RequestException as e:
            raise SourceUnavailable(self.source_name, f"HTTP error: {e}")

        result = data.get("result") if isinstance(data, dict) else None
        rows = (result or {}).get("rows", [])

        if not rows:
            # 空数据 — 返回带 schema 的空 DataFrame
            expected = self._expected_columns()
            return (
                pd.DataFrame(columns=expected)
                if self._period == "day"
                else pd.DataFrame(
                    columns=[
                        "symbol",
                        "period",
                        "datetime",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "source",
                        "fetched_at",
                        "trace_id",
                    ]
                )
            )

        df = pd.DataFrame(rows)

        # 根据 period 判断是日线还是分钟线
        if self._period == "day":
            return self._process_daily(df, tq_sym, trace_id)
        else:
            return self._process_minute(df, tq_sym, symbol, trace_id)

    def _process_daily(
        self,
        df: pd.DataFrame,
        tq_sym: str,
        trace_id: str,
    ) -> pd.DataFrame:
        """处理日线返回数据（17 列 kline_cache schema）。"""
        # 必填字段校验
        required = ("date", "open", "high", "low", "close", "volume")
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"响应缺必填字段: {missing}")

        # 元数据
        df["symbol"] = tq_sym
        df["period"] = "daily"
        df["date"] = pd.to_datetime(df["date"]).dt.date

        # 期货专属字段（允许缺失 → 留空）
        for col in ("hold", "settle", "pre_settle", "oi_change"):
            if col not in df.columns:
                df[col] = pd.NA

        # vwap 计算：amount / volume，缺失时回退到 (h+l+c)/3
        typical = (df["high"] + df["low"] + df["close"]) / 3
        if "amount" in df.columns and df["volume"].gt(0).any():
            df["vwap"] = df["amount"] / df["volume"].replace(0, pd.NA)
        else:
            df["vwap"] = typical
        # 残余 NA 仍回退到典型价
        df["vwap"] = df["vwap"].fillna(typical)

        # amount 列可能不存在 — 补空列保证 schema 一致
        if "amount" not in df.columns:
            df["amount"] = pd.NA

        df["source"] = self.source_name
        df["fetched_at"] = pd.Timestamp.now()
        df["trace_id"] = trace_id

        return df[self._expected_columns()]

    def _process_minute(
        self,
        df: pd.DataFrame,
        tq_sym: str,
        original_symbol: str,
        trace_id: str,
    ) -> Optional[pd.DataFrame]:
        """处理分钟线返回数据（11 列 minute_cache schema）。

        注意: TQ-Local 分钟数据为**倒序**（新→旧），需反转。
        """
        # 必填字段校验
        required = ("datetime", "open", "high", "low", "close", "volume")
        # TQ-Local 分钟数据可能使用 "date" 列而非 "datetime"
        if "datetime" not in df.columns and "date" in df.columns:
            df["datetime"] = df["date"]
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning("[%s] 分钟数据缺必填字段: %s", self.source_name, missing)
            return None

        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.dropna(subset=["datetime"])

        # 反转（TQ-Local 分钟数据倒序返回）
        df = df.sort_values("datetime").reset_index(drop=True)

        # 统一数据类型
        df = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # 元数据
        df["symbol"] = original_symbol
        df["period"] = self._period
        df["source"] = self.source_name
        df["fetched_at"] = pd.Timestamp.now()
        df["trace_id"] = trace_id

        # 返回分钟级 schema 列
        cols = [
            "symbol",
            "period",
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source",
            "fetched_at",
            "trace_id",
        ]
        return df[[c for c in cols if c in df.columns]]

    # ─── 实时快照 ──

    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """拉取实时快照，返回统一格式 dict。"""
        tq_sym = self._symbol_to_tq(symbol)
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000),
            "method": "tq_get_quote",
            "params": {"symbol": tq_sym},
        }

        try:
            resp = requests.post(self.rpc_url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
        except (ConnectionError, Timeout, RequestException) as e:
            raise SourceUnavailable(self.source_name, str(e))

        result = data.get("result") if isinstance(data, dict) else None
        if not result:
            return None

        quote = dict(result)
        quote["symbol"] = tq_sym
        quote["source"] = self.source_name
        quote["trace_id"] = trace_id
        quote["fetched_at"] = pd.Timestamp.now().isoformat()
        return quote


__all__ = ["TQLocalSource", "_infer_exchange"]
