"""fts.data_sources.tdx_local_source — 通达信本地 HTTP 数据源适配器（v2.85.0）。

统一承载通达信量化模拟客户端 TQ 服务的全部数据能力（端口 17709）:
    - get_market_data      → 日线 + 分钟 K 线（period: 1d / 1m / 5m / 15m / 30m / 1h）
    - get_market_snapshot  → 实时快照

背景:
    v2.85.0 合并原 TQLocalSource(7721, tq_get_kline/tq_get_quote) 与
    TDXMinuteSource(17709, get_market_data/get_market_snapshot)：
    实测通达信量化模拟客户端仅监听 17709，协议为 get_market_data，
    7721 端口与 tq_get_kline 方法不存在（-32601 MCP不支持该tqcenter方法名）。
    合并后统一以 17709 + get_market_data 承担 日线/分钟/快照。

代码转换: FTS `RB0` (主力连续) ↔ 通达信 `RBL8.SHF` (商品 L8 / 中金所 L0)。
HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法。
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Optional

import pandas as pd

from fts.data_sources.base import BaseFuturesSource, SourceUnavailable

logger = logging.getLogger(__name__)

# 通达信 TQ-Local HTTP 服务地址（量化模拟客户端）
TDX_RPC_URL = "http://127.0.0.1:17709/"

# 支持的周期 → get_market_data period 参数
SUPPORTED_PERIODS: dict[str, str] = {
    "day": "1d",
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


# 期货代码 → 交易所后缀（通达信主连格式）
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
    "FG",
)
_SHFE_PREFIXES = (
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
)
_DCE_PREFIXES = (
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


def _tdx_stock_code(symbol: str) -> str:
    """A 股/ETF 6 位代码 → 通达信 TQ 代码（000001 → 000001.SZ, 600000 → 600000.SH）。

    通达信本地 TQ（17709）get_market_data 对 A 股使用 {code}.{exchange} 格式，
    与期货主连格式（RBL8.SHF）不同。无法识别时原样返回。
    """
    raw = symbol.strip().lower()
    for pfx in ("sh", "sz"):
        if raw.startswith(pfx):
            raw = raw[len(pfx):]
    if len(raw) != 6 or not raw.isdigit():
        return symbol
    # 沪市 6/9 开头、沪 ETF 5 开头；其余默认深市（0/3 开头、159 ETF）
    if raw.startswith(("6", "9", "5")):
        return f"{raw}.SH"
    return f"{raw}.SZ"


def fetch_stock_ohlcv(
    symbol: str,
    days: int = 500,
    trace_id: str = "",
    adjust: str = "qfq",
) -> Optional[pd.DataFrame]:
    """从通达信本地 TQ（17709）拉取 A 股/ETF 日 K 线（真实行情）。

    Args:
        symbol: A 股 6 位代码（如 "000001" / "600519" / "510300"）
        days: 回溯 K 线数量
        trace_id: 链路追踪 ID
        adjust: 复权方式（"qfq" 前复权 → dividend_type 'front'；"hfq" → 'back'；其余不复权）

    Returns:
        DataFrame（index=DatetimeIndex，列 open/high/low/close/volume）或 None（失败/无数据）。
    """
    tdx_code = _tdx_stock_code(symbol)
    dividend = {"qfq": "front", "hfq": "back"}.get(adjust or "", "none")
    src = TdxLocalSource(period="day")
    try:
        result = src._rpc(
            "get_market_data",
            {
                "stock_list": [tdx_code],
                "count": days,
                "period": "1d",
                "dividend_type": dividend,
            },
        )
    except SourceUnavailable:
        return None
    if not isinstance(result, dict):
        return None
    value = result.get("Value")
    if not isinstance(value, dict):
        return None
    # 目标代码数据块（兼容返回多个代码时取首个有效块）
    block = value.get(tdx_code) or next(iter(value.values()), None)
    if not isinstance(block, dict):
        return None

    df = pd.DataFrame(block)
    col_map = {c.lower(): c for c in df.columns}
    df = df.rename(columns={v: k for k, v in col_map.items()})
    required = ("open", "high", "low", "close", "volume")
    if not all(c in df.columns for c in required) or "date" not in df.columns:
        return None
    for c in required:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    if len(df) > days:
        df = df.tail(days).reset_index(drop=True)
    # 涨跌幅（小数：0.05 = +5%；首日为 NaN，供信号报告 change_pct 列）
    df["change_pct"] = pd.to_numeric(df["close"], errors="coerce").pct_change()
    df = df.set_index("date")
    return df[list(required) + ["change_pct"]]


class TdxLocalSource(BaseFuturesSource):
    """通达信本地 HTTP 数据源适配器（端口 17709）。

    协议: JSON-RPC over HTTP POST（get_market_data / get_market_snapshot）。
    端点: http://127.0.0.1:17709/

    支持周期: "day"（日线，默认）/ "1m" / "5m" / "15m" / "30m" / "60m"
    - day 周期返回 17 列 kline_cache schema（date 列）
    - 分钟周期返回 11 列 minute_cache schema（datetime 列）

    v2.87.0 起统一承载原 TQLocalSource(7721) 与 TDXMinuteSource(17709) 职责。
    """

    source_name: str = "TDX_LOCAL"
    timeout: float = 10.0

    def __init__(self, period: str = "day") -> None:
        """初始化。

        Args:
            period: 周期，支持 "day"（默认）/ "1m" / "5m" / "15m" / "30m" / "60m"
        """
        if period not in SUPPORTED_PERIODS:
            raise ValueError(f"不支持的周期: {period}，可选: {list(SUPPORTED_PERIODS.keys())}")
        self._period = period

    @property
    def period(self) -> str:
        return self._period

    # ─── 探活（不抛异常）──

    def is_available(self) -> bool:
        """探活：向通达信 TQ 服务发送轻量 get_market_data 请求。"""
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
        except Exception:  # noqa: BLE001
            return False

    # ─── 统一 RPC 调用 ──

    def _rpc(self, method: str, params: dict[str, Any]) -> Any:
        """发送 JSON-RPC 请求并返回 result。网络/解析异常抛 SourceUnavailable。"""
        payload = {
            "id": int(time.time() * 1000),
            "method": method,
            "params": params,
        }
        try:
            req = urllib.request.Request(
                TDX_RPC_URL,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            raise SourceUnavailable(self.source_name, f"HTTP 请求失败: {e}")
        except OSError as e:
            raise SourceUnavailable(self.source_name, f"连接失败: {e}")
        except json.JSONDecodeError as e:
            raise SourceUnavailable(self.source_name, f"JSON 解析失败: {e}")
        return raw.get("result") if isinstance(raw, dict) else None

    # ─── K 线 ──

    def fetch_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """获取 K 线数据。

        - day 周期: 返回 17 列 kline_cache schema（date 列，含 hold/settle 等）。
        - 分钟周期: 返回 11 列 minute_cache schema（datetime 列）。

        Args:
            symbol: 品种代码（如 "RB0"）
            days: 返回的 K 线数量
            trace_id: 链路追踪 ID

        Returns:
            DataFrame 或 None
        """
        tdx_sym = _symbol_to_tdx(symbol)
        result = self._rpc(
            "get_market_data",
            {
                "stock_list": [tdx_sym],
                "count": days,
                "period": SUPPORTED_PERIODS[self._period],
                "dividend_type": "none",
            },
        )
        if result is None:
            return None

        # TQ 返回列字典格式: Value: {代码: {列名: [值...]}}
        value = result.get("Value") if isinstance(result, dict) else result
        if not isinstance(value, dict):
            return None

        # 取目标代码的数据块（兼容返回多个代码）
        block = value.get(tdx_sym) or next(iter(value.values()), None)
        if not isinstance(block, dict):
            return None

        df = pd.DataFrame(block)

        if self._period == "day":
            return self._process_daily(df, tdx_sym, symbol, trace_id, days)
        return self._process_minute(df, tdx_sym, symbol, trace_id, days)

    def _process_daily(
        self,
        df: pd.DataFrame,
        tdx_sym: str,
        original_symbol: str,
        trace_id: str,
        days: int,
    ) -> pd.DataFrame:
        """处理日线返回数据（17 列 kline_cache schema，date 列）。"""
        # 统一字段名为小写
        col_map = {c.lower(): c for c in df.columns}
        df = df.rename(columns={v: k for k, v in col_map.items()})

        # 必填字段校验
        required = ("open", "high", "low", "close", "volume")
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning("[%s] 日线响应缺必填字段: %s", self.source_name, missing)
            return pd.DataFrame(columns=self._expected_columns())

        # date 列（get_market_data 日线返回 Date 字符串 "20260807"）
        if "date" not in df.columns:
            logger.warning("[%s] 日线响应缺 date 字段", self.source_name)
            return pd.DataFrame(columns=self._expected_columns())
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce").dt.date

        # 统一数据类型
        df = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if "amount" in df.columns:
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        else:
            df["amount"] = pd.NA

        # 剔除无效日期
        df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)

        # 截取最近 days 行
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)

        # 期货专属字段（通达信 HTTP 不提供 → 补 NA）
        for col in ("hold", "settle", "pre_settle", "oi_change"):
            if col not in df.columns:
                df[col] = pd.NA

        # vwap: amount / volume（amount>0 且 volume>0 时），否则回退 (h+l+c)/3
        typical = (df["high"] + df["low"] + df["close"]) / 3
        vol = pd.to_numeric(df["volume"], errors="coerce")
        amt = pd.to_numeric(df["amount"], errors="coerce")
        vwap = amt / vol
        df["vwap"] = vwap.where(amt.gt(0) & vol.gt(0), typical)

        # 元数据
        df["symbol"] = original_symbol
        df["period"] = "daily"
        df["source"] = self.source_name
        df["fetched_at"] = pd.Timestamp.now()
        df["trace_id"] = trace_id

        return df[self._expected_columns()]

    def _process_minute(
        self,
        df: pd.DataFrame,
        tdx_sym: str,
        original_symbol: str,
        trace_id: str,
        days: int,
    ) -> Optional[pd.DataFrame]:
        """处理分钟线返回数据（11 列 minute_cache schema）。"""
        col_map = {c.lower(): c for c in df.columns}
        df = df.rename(columns={v: k for k, v in col_map.items()})

        df["time_str"] = df.get("time", pd.Series(dtype=str)).astype(str)
        df["date_str"] = df.get("date", pd.Series(dtype=str)).astype(str)

        # 构建 datetime 列
        if df["date_str"].notna().any() and df["time_str"].notna().any():
            df["datetime"] = pd.to_datetime(
                df["date_str"] + df["time_str"].str.zfill(6),
                format="%Y%m%d%H%M%S",
                errors="coerce",
            )
        elif df["time_str"].notna().any():
            now = pd.Timestamp.now().normalize()
            df["datetime"] = pd.to_datetime(
                now.strftime("%Y%m%d") + df["time_str"].str.zfill(6),
                format="%Y%m%d%H%M%S",
                errors="coerce",
            )
        elif df["date_str"].notna().any():
            df["datetime"] = pd.to_datetime(df["date_str"], format="%Y%m%d", errors="coerce")
        else:
            logger.warning("[%s] 分钟数据缺少日期/时间字段", self.source_name)
            return None

        df = df.dropna(subset=["datetime"])

        # 必填字段校验
        required = ("open", "high", "low", "close", "volume")
        missing = [c for c in required if c not in df.columns]
        if missing:
            logger.warning("[%s] 分钟数据缺必填字段: %s", self.source_name, missing)
            return None

        # 统一数据类型
        df = df.copy()
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.sort_values("datetime").reset_index(drop=True)

        # 截取最近 days 行
        if len(df) > days:
            df = df.tail(days).reset_index(drop=True)

        # 元数据
        df["symbol"] = original_symbol
        df["period"] = self._period
        df["source"] = self.source_name
        df["fetched_at"] = pd.Timestamp.now()
        df["trace_id"] = trace_id

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

    # ─── 实时快照 ──

    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """获取实时快照（get_market_snapshot）。

        网络/解析异常返回 None（不抛异常，供上层降级）。
        """
        tdx_sym = _symbol_to_tdx(symbol)
        try:
            result = self._rpc("get_market_snapshot", {"stock_code": tdx_sym})
        except Exception:  # noqa: BLE001
            logger.warning("[%s] fetch_quote 异常 [%s]", self.source_name, symbol, exc_info=True)
            return None
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
    "TdxLocalSource",
    "SUPPORTED_PERIODS",
    "FREQUENCY_ANNUALIZATION",
    "get_annualization_factor",
    "get_default_zscore_window",
    "TDX_RPC_URL",
    "fetch_stock_ohlcv",
]
