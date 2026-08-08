"""fts.data_sources.tdx_minute_source — 通达信 TQ-Local 分钟 K 线适配器（v2.29.0）。

通过通达信本地客户端 HTTP 服务（端口 17709）获取期货分钟级 K 线数据。
使用 get_market_data 接口，支持 1m/5m/15m/30m/60m 五个周期。

HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import pandas as pd
import urllib.request
import urllib.error

from fts.data_sources.base import BaseFuturesSource, SourceUnavailable

logger = logging.getLogger(__name__)

# 通达信 TQ-Local HTTP 服务地址
TDX_RPC_URL = "http://127.0.0.1:17709/"

# 支持的分钟周期 → get_market_data period 参数
SUPPORTED_PERIODS: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "1h",
}

# 年化因子（按 trading days × 每分钟 bars 数）
FREQUENCY_ANNUALIZATION: dict[str, float] = {
    "daily": 252.0,
    "1m": 252.0 * 390.0,
    "5m": 252.0 * 78.0,
    "15m": 252.0 * 26.0,
    "30m": 252.0 * 13.0,
    "60m": 252.0 * 6.5,
}


def get_annualization_factor(frequency: str) -> float:
    """获取指定频率的年化因子。"""
    return FREQUENCY_ANNUALIZATION.get(frequency, 252.0)


def get_default_zscore_window(frequency: str) -> int:
    """获取指定频率的默认 z-score 窗口（对应约 20 个交易日）。"""
    annual = get_annualization_factor(frequency)
    return max(20, int(20 * annual / 252.0))


# 期货代码 → 交易所后缀（TQ-Local 要求）
_EXCHANGE_SUFFIX: dict[str, str] = {
    "SHFE": "SHF",
    "DCE": "DCE",
    "CZCE": "CZC",
    "CFFEX": "CFF",
    "INE": "INE",
    "GFE": "GFE",
}

# 中金所品种（主力连续用 L0，而非商品期货的 L8）
_CFFEX_PRODUCTS = ("IF", "IH", "IC", "IM", "TF", "TS", "TL", "T")

# 优先 2 字母匹配
_CFFEX_PREFIXES = ("IF", "IH", "IC", "TF", "TS", "TL")
_CZCE_PREFIXES = (
    "SR", "CF", "CJ", "CY", "AP", "ER", "LR", "LW", "MR", "ME",
    "PM", "RI", "RM", "RS", "SF", "SM", "TA", "TM", "UR", "WH",
    "WS", "WT", "ZC", "GN", "RO", "PF", "PK", "PX", "SA", "SH",
    "TC", "JR", "OI", "MA", "FG",
)
_SHFE_PREFIXES = (
    "RB", "CU", "AL", "ZN", "AU", "AG", "PB", "NI", "SN", "SS",
    "BU", "RU", "FU", "SP", "WR", "HC", "FB", "BB", "AO", "AD",
    "BC", "EC",
)
_DCE_PREFIXES = (
    "A", "B", "C", "CS", "M", "Y", "P", "L", "JD", "JM",
    "I", "J", "R", "RR", "V", "EG", "EB", "PG", "LH", "FB",
)


def _infer_exchange_suffix(symbol: str) -> str:
    """推断品种交易所后缀。"""
    sym = symbol.upper().replace("0", "")
    for p in _CFFEX_PREFIXES:
        if sym.startswith(p):
            return _EXCHANGE_SUFFIX["CFFEX"]
    for p in _CZCE_PREFIXES:
        if sym.startswith(p):
            return _EXCHANGE_SUFFIX["CZCE"]
    for p in _SHFE_PREFIXES:
        if sym.startswith(p):
            return _EXCHANGE_SUFFIX["SHFE"]
    for p in _DCE_PREFIXES:
        if sym.startswith(p):
            return _EXCHANGE_SUFFIX["DCE"]
    if sym.startswith("T"):
        return _EXCHANGE_SUFFIX["CFFEX"]
    return _EXCHANGE_SUFFIX["SHFE"]


def _symbol_to_tdx(symbol: str) -> str:
    """FTS 代码 → 通达信主力连续代码（RB0 → RBL8.SHF, IF0 → IFL0.CFF）。

    通达信期货主力连续格式:
      - 商品期货: {品种大写}L8.{交易所后缀}（L8 = 主力连续）
      - 中金所:   {品种大写}L0.{交易所后缀}（L0 = 主力连续）
    """
    suffix = _infer_exchange_suffix(symbol)
    product = symbol.upper().rstrip("0")
    # 中金所品种（含 T 国债），注意 TA 是郑商所 PTA，不能误判
    if product in _CFFEX_PRODUCTS:
        return f"{product}L0.{suffix}"
    return f"{product}L8.{suffix}"


class TDXMinuteSource(BaseFuturesSource):
    """通达信 TQ-Local 分钟 K 线适配器（端口 17709）。

    通过 get_market_data 接口获取期货分钟级 K 线数据。
    支持 1m / 5m / 15m / 30m / 60m 五个周期。
    """

    source_name: str = "TDX_MINUTE"

    def __init__(self, period: str = "1m") -> None:
        """初始化。

        Args:
            period: 分钟周期，支持 "1m" / "5m" / "15m" / "30m" / "60m"
        """
        if period not in SUPPORTED_PERIODS:
            raise ValueError(
                f"不支持的分钟周期: {period}，可选: {list(SUPPORTED_PERIODS.keys())}"
            )
        self._period = period
        self._tdx_period = SUPPORTED_PERIODS[period]

    @property
    def period(self) -> str:
        return self._period

    def is_available(self) -> bool:
        """探活：向通达信 TQ-Local HTTP 服务发送轻量请求。"""
        try:
            payload = {
                "id": int(time.time() * 1000),
                "method": "get_market_data",
                "params": {
                    "stock_list": ["RBL8.SHF"],
                    "count": 1,
                    "period": "1d",
                },
            }
            req = urllib.request.Request(
                TDX_RPC_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                return resp.status == 200
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
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
            days: 返回的 K 线数量
            trace_id: 链路追踪 ID

        Returns:
            DataFrame 或 None
        """
        tdx_sym = _symbol_to_tdx(symbol)
        payload = {
            "id": int(time.time() * 1000),
            "method": "get_market_data",
            "params": {
                "stock_list": [tdx_sym],
                "count": days,
                "period": self._tdx_period,
                "dividend_type": "none",
            },
        }

        try:
            req = urllib.request.Request(
                TDX_RPC_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            raise SourceUnavailable(self.source_name, f"HTTP 请求失败: {e}")
        except OSError as e:
            raise SourceUnavailable(self.source_name, f"连接失败: {e}")
        except json.JSONDecodeError as e:
            raise SourceUnavailable(self.source_name, f"JSON 解析失败: {e}")

        result = raw.get("result") if isinstance(raw, dict) else None
        if result is None:
            return None

        # TQ-Local 返回列字典格式: Value: {代码: {列名: [值...]}}
        value = result.get("Value") if isinstance(result, dict) else result
        if not isinstance(value, dict):
            return None

        # 取目标代码的数据块（兼容返回多个代码）
        block = value.get(tdx_sym) or next(iter(value.values()), None)
        if not isinstance(block, dict):
            return None

        # 转为 DataFrame（列: Date/Time/Open/High/Low/Close/Volume/Amount）
        df = pd.DataFrame(block)

        # 必填字段校验（兼容大写/小写字段名）
        col_map = {c.lower(): c for c in df.columns}
        required = ("open", "high", "low", "close", "volume")
        missing = [c for c in required if c not in col_map]
        if missing:
            logger.warning("[%s] 响应缺必填字段: %s", self.source_name, missing)
            return None

        # 统一字段名为小写
        df = df.rename(columns={v: k for k, v in col_map.items()})
        df["time_str"] = df.get("time", pd.Series(dtype=str)).astype(str)
        df["date_str"] = df.get("date", pd.Series(dtype=str)).astype(str)

        # 构建 datetime 列
        if df["date_str"].notna().any() and df["time_str"].notna().any():
            # 同时有 Date 和 Time（日期时间）
            df["datetime"] = pd.to_datetime(
                df["date_str"] + df["time_str"].str.zfill(6),
                format="%Y%m%d%H%M%S",
                errors="coerce",
            )
        elif df["time_str"].notna().any():
            # 仅有 Time（日内分钟数据）
            now = pd.Timestamp.now().normalize()
            df["datetime"] = pd.to_datetime(
                now.strftime("%Y%m%d") + df["time_str"].str.zfill(6),
                format="%Y%m%d%H%M%S",
                errors="coerce",
            )
        elif df["date_str"].notna().any():
            # 仅有 Date
            df["datetime"] = pd.to_datetime(
                df["date_str"], format="%Y%m%d", errors="coerce"
            )
        else:
            logger.warning("[%s] 缺少日期/时间字段", self.source_name)
            return None

        # 过滤无效 datetime
        df = df.dropna(subset=["datetime"])

        # 统一数据类型
        df = df.copy()
        df["open"] = pd.to_numeric(df["open"], errors="coerce")
        df["high"] = pd.to_numeric(df["high"], errors="coerce")
        df["low"] = pd.to_numeric(df["low"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

        # 按时间排序
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
        """获取实时快照。"""
        tdx_sym = _symbol_to_tdx(symbol)
        payload = {
            "id": int(time.time() * 1000),
            "method": "get_market_snapshot",
            "params": {"stock_code": tdx_sym},
        }

        try:
            req = urllib.request.Request(
                TDX_RPC_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        result = raw.get("result") if isinstance(raw, dict) else None
        if not result:
            return None

        quote = {
            "symbol": symbol,
            "source": self.source_name,
            "trace_id": trace_id,
            "fetched_at": pd.Timestamp.now().isoformat(),
        }
        if isinstance(result, dict):
            quote["last_price"] = float(result.get("Now", 0))
            quote["open"] = float(result.get("Open", 0))
            quote["high"] = float(result.get("Max", 0))
            quote["low"] = float(result.get("Min", 0))
            quote["volume"] = float(result.get("Volume", 0))
        return quote


__all__ = [
    "TDXMinuteSource",
    "SUPPORTED_PERIODS",
    "FREQUENCY_ANNUALIZATION",
    "get_annualization_factor",
    "get_default_zscore_window",
]