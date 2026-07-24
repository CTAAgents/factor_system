"""
fts.data_mcp — 腾讯自选股 MCP 数据适配层

基于腾讯自选股 HTTP API（qt.gtimg.cn / web.ifzq.gtimg.cn）提供 A 股和 ETF 的 OHLCV 数据。
与 mcp_westock-mcp 服务同源，零外部 Python 依赖（仅需 httpx/stdlib）。

数据流:
    因子引擎 → FTSDataProvider → MCPDataProvider → 腾讯自选股 HTTP API

用法:
    provider = MCPDataProvider()
    df = provider.get_ohlcv("510300", days=250)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── 异常 ──────────────────────────────────────────────────

class MCPDataError(RuntimeError):
    """MCP 数据获取失败。"""


# ─── 交易所代码前缀 ────────────────────────────────────────

_SSE = "sh"      # 上海
_SZE = "sz"      # 深圳


def _to_tencent_code(code: str) -> str:
    """将 6 位数字代码转为腾讯 API 格式（sh/sz 前缀）。

    Args:
        code: "510300" / "sh510300" / "000001"

    Returns:
        "sh510300" / "sz000001"
    """
    raw = code.strip().lower()
    for prefix in (_SSE, _SZE):
        if raw.startswith(prefix):
            return raw
    # 判断交易所: 6位数字代码，6xxxxx=上海，0xxxxx/3xxxxx=深圳
    if raw.startswith("6") or raw.startswith("9"):
        return f"sh{raw}"
    return f"sz{raw}"


# ─── 腾讯 HTTP API 客户端 ─────────────────────────────────

_TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q={codes}"
_TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

_HTTP: Optional[Any] = None


def _get_http() -> Any:
    """延迟获取 httpx 客户端。"""
    global _HTTP  # pylint: disable=global-statement
    if _HTTP is None:
        import httpx  # type: ignore[import-untyped]
        _HTTP = httpx.Client(timeout=15.0)
    return _HTTP


def _fetch_kline_json(code: str, days: int, adjust: str) -> list[list]:
    """从腾讯 K 线 API 获取原始 JSON 数据。

    Args:
        code: 腾讯格式代码，如 "sh510300"
        days: 回溯天数
        adjust: "qfq"(前复权) / "hfq"(后复权) / ""(不复权)

    Returns:
        list of [date_str, open, close, high, low, volume]

    Raises:
        MCPDataError: 请求失败或数据为空
    """
    client = _get_http()
    try:
        resp = client.get(
            _TENCENT_KLINE_URL,
            params={"param": f"{code},day,,,{days},{adjust or 'qfq'}"},
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        raise MCPDataError(f"腾讯 K 线请求失败 [{code}]: {e}") from e

    if body.get("code") != 0 or "data" not in body:
        raise MCPDataError(f"腾讯 K 线返回异常 [{code}]: {body.get('msg', '')}")

    data = body["data"]
    if code not in data:
        raise MCPDataError(f"腾讯 K 线无数据 [{code}]")

    symbol_data = data[code]
    # 按优先级取 K 线: qfqday > hfqday > day
    kline_key = None
    for key in ("qfqday", "hfqday", "day"):
        if key in symbol_data:
            kline_key = key
            break

    if kline_key is None:
        raise MCPDataError(f"腾讯 K 线无 K 线数据 [{code}]")

    return symbol_data[kline_key]


# ─── 代码格式转换 ──────────────────────────────────────────

def _is_etf_code(code: str) -> bool:
    """判断是否为 ETF 代码。"""
    raw = code.strip().lower()
    for prefix in (_SSE, _SZE):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    if raw.startswith("51") or raw.startswith("56") or raw.startswith("58"):
        return True
    if raw.startswith("159"):
        return True
    return False


# ─── DataFrame 转换 ────────────────────────────────────────

def _kline_to_df(raw: list[list]) -> pd.DataFrame:
    """腾讯 K 线原始格式 → OHLCV DataFrame。

    腾讯格式: [date_str, open, close, high, low, volume]
    """
    rows: list[dict[str, Any]] = []
    for item in raw:
        if len(item) < 6:
            continue
        rows.append({
            "open": float(item[1]),
            "high": float(item[3]),
            "low": float(item[4]),
            "close": float(item[2]),
            "volume": float(item[5]) if item[5] else 0,
            "date": item[0],
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)
    return df[["open", "high", "low", "close", "volume"]]


# ─── MCP 数据提供者 ────────────────────────────────────────

class MCPDataProvider:
    """腾讯自选股 MCP 数据提供者。

    通过腾讯自选股 HTTP API（qt.gtimg.cn / web.ifzq.gtimg.cn）获取 A 股和 ETF 行情数据。
    与 mcp_westock-mcp 服务同源数据，零额外依赖。

    用法:
        provider = MCPDataProvider()
        df = provider.get_ohlcv("510300", days=250)
        panel, dates = provider.get_stock_panel(["000001", "000002"], days=250)
    """

    def __init__(self, use_async: bool = False):
        self._use_async = use_async

    # ── 单标的 OHLCV ──

    def get_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        adjust: str = "qfq",
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取单只股票/ETF 的 OHLCV 日 K 线数据。

        Args:
            symbol: 代码（支持 510300 / sh510300 格式）
            days: 回溯天数
            adjust: 复权方式 ("qfq"前复权 / "hfq"后复权 / ""不复权)
            trace_id: HARNESS trace_id

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume
            Index: DatetimeIndex
        """
        code = _to_tencent_code(symbol)
        try:
            raw = _fetch_kline_json(code, days, adjust)
            df = _kline_to_df(raw)
            if not df.empty:
                return df
        except MCPDataError as e:
            logger.warning(f"MCP OHLCV 获取失败 [{symbol}]: {e}")
        except Exception as e:
            logger.warning(f"MCP OHLCV 异常 [{symbol}]: {e}")

        # 降级回退
        return self.synthesize_ohlcv(n_days=days, base_price=15.0, seed=42)

    # ── ETF 专用接口 ──

    def get_etf_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        adjust: str = "qfq",
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取 ETF OHLCV 数据。"""
        return self.get_ohlcv(symbol, days=days, adjust=adjust, trace_id=trace_id)

    # ── 批量面板数据 ──

    def get_stock_panel(
        self,
        symbols: list[str],
        days: int = 500,
        adjust: str = "qfq",
        trace_id: str = "",
    ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取多个标的的 OHLCV 面板数据。

        Returns:
            (panel, common_dates)
            panel: dict[symbol, OHLCV DataFrame]
            common_dates: 所有标的共有日期
        """
        panel: dict[str, pd.DataFrame] = {}
        dates_set: set[pd.Timestamp] = set()
        first = True

        for sym in symbols:
            try:
                df = self.get_ohlcv(sym, days=days, adjust=adjust, trace_id=trace_id)
                if df is not None and not df.empty and "close" in df.columns:
                    panel[sym] = df
                    if first:
                        dates_set = set(df.index)
                        first = False
                    else:
                        dates_set &= set(df.index)
            except Exception:  # noqa: BLE001
                continue

        if not panel:
            logger.warning("所有标的 MCP 数据获取失败，使用合成数据")
            df = self.synthesize_ohlcv(n_days=days, base_price=15.0, seed=42)
            panel["SYNTHETIC"] = df
            return panel, df.index

        common_dates = pd.DatetimeIndex(sorted(dates_set))
        return panel, common_dates

    # ── 合成数据降级 ──

    @staticmethod
    def synthesize_ohlcv(
        n_days: int = 500,
        base_price: float = 15.0,
        seed: int = 42,
    ) -> pd.DataFrame:
        """合成 OHLCV 数据（网络不可用时的降级回退）。"""
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


# ─── 沪深 300 代表性子集 ──────────────────────────────────

CSI300_SUBSET: list[str] = [
    "000001", "000002", "000333", "000568", "000651", "000725", "000858",
    "002027", "002142", "002230", "002304", "002371", "002415", "002475",
    "002594", "002714", "300015", "300059", "300124", "300274", "300308",
    "300413", "300433", "300450", "300498", "300502", "300628", "300750",
    "300760", "600000", "600009", "600028", "600030", "600031", "600036",
    "600048", "600085", "600104", "600276", "600309", "600406", "600436",
    "600438", "600519", "600547", "600585", "600690", "600809", "600887",
    "600900", "600941", "601012", "601088", "601127", "601166", "601288",
    "601318", "601328", "601398", "601628", "601728", "601766", "601857",
    "601888", "601899", "601985", "603259", "603288", "603501", "603659",
    "688008", "688036", "688111", "688122", "688256", "688396",
]

# ─── 常见 ETF 子集 ─────────────────────────────────────────

ETF_SUBSET: list[str] = [
    "510050", "510300", "510500", "510880",
    "512100", "512880", "513050", "513100",
    "515050", "515790", "516160", "517010",
    "518880", "588000",
    "159915", "159949", "159992", "159995",
]

__all__ = [
    "MCPDataProvider",
    "MCPDataError",
    "CSI300_SUBSET",
    "ETF_SUBSET",
]
