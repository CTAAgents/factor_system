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

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd

from fts.data_sources.tdx_local_source import fetch_stock_ohlcv

logger = logging.getLogger(__name__)

# ─── 异常 ──────────────────────────────────────────────────


class MCPDataError(RuntimeError):
    """MCP 数据获取失败。"""


# ─── TQ 可用性探活缓存（进程内，避免 TQ 离线时每次调用都超时）────
# 进程级重试机制：探活失败进入冷却期（_TQ_PROBE_COOLDOWN），冷却期后自动重探
# 并带瞬时重试（_TQ_PROBE_RETRIES），TQ 短暂抖动不再导致整进程永久降级。


_TQ_STOCK_AVAILABLE: Optional[bool] = None
_TQ_LAST_PROBE_TS: float = 0.0
_TQ_PROBE_COOLDOWN: float = 30.0  # 探活失败后的冷却期（秒），期间不重复探活
_TQ_PROBE_RETRIES: int = 2  # 每次探活周期内重试次数（吸收瞬时抖动）
_TQ_PROBE_RETRY_INTERVAL: float = 1.0  # 重试间隔（秒）


def _probe_tq_once() -> bool:
    """单次 TQ 探活：HTTP POST get_market_data（000001.SZ，5s 超时）。

    Returns:
        True 表示 TQ 可返回合法行情结构（result.Value 为 dict）。
    """
    import json
    import urllib.error
    import urllib.request

    payload = {
        "id": int(time.time() * 1000),
        "method": "get_market_data",
        "params": {"stock_list": ["000001.SZ"], "count": 1, "period": "1d"},
    }
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:17709/",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        result = body.get("result") if isinstance(body, dict) else None
        return bool(isinstance(result, dict) and isinstance(result.get("Value"), dict))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False
    except Exception:  # noqa: BLE001
        return False


def _tq_stock_available() -> bool:
    """通达信 TQ（17709）股票行情是否可用（进程内缓存 + 失败冷却重试）。

    直接用股票端点 000001.SZ 探测（与 fetch_stock_ohlcv 同协议），
    短超时（5s）避免 TQ 离线时拖慢降级链；期货探活对股票路径无意义。

    进程级重试机制：
    - 探活成功 → 缓存 True，进程内不再探活
    - 探活失败 → 记录失败时间戳，冷却期（_TQ_PROBE_COOLDOWN=30s）内保持
      不可用且不重复探活（TQ 离线时避免每次调用都阻塞）
    - 冷却期后自动重新探活（带 _TQ_PROBE_RETRIES 次瞬时重试，间隔 1s），
      成功即恢复 True——TQ 短暂抖动不再导致整个进程永久降级
    """
    global _TQ_STOCK_AVAILABLE, _TQ_LAST_PROBE_TS  # pylint: disable=global-statement
    now = time.time()
    if _TQ_STOCK_AVAILABLE is True:
        return True
    if _TQ_STOCK_AVAILABLE is False and now - _TQ_LAST_PROBE_TS < _TQ_PROBE_COOLDOWN:
        return False  # 冷却期内不重复探活
    _TQ_LAST_PROBE_TS = now
    ok = False
    for _ in range(_TQ_PROBE_RETRIES + 1):
        if _probe_tq_once():
            ok = True
            break
        time.sleep(_TQ_PROBE_RETRY_INTERVAL)
    _TQ_STOCK_AVAILABLE = ok
    return ok


# ─── 交易所代码前缀 ────────────────────────────────────────

_SSE = "sh"  # 上海
_SZE = "sz"  # 深圳


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
            raw = raw[len(prefix) :]
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
        rows.append(
            {
                "open": float(item[1]),
                "high": float(item[3]),
                "low": float(item[4]),
                "close": float(item[2]),
                "volume": float(item[5]) if item[5] else 0,
                "date": item[0],
            }
        )

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
        strict: bool = False,
    ) -> pd.DataFrame:
        """获取单只股票/ETF 的 OHLCV 日 K 线数据。

        Args:
            symbol: 代码（支持 510300 / sh510300 格式）
            days: 回溯天数
            adjust: 复权方式 ("qfq"前复权 / "hfq"后复权 / ""不复权)
            trace_id: HARNESS trace_id
            strict: 严格模式。True 时数据获取失败抛 MCPDataError，
                不降级为合成数据（供缓存同步等对数据真实性有硬要求的场景）。

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume
            Index: DatetimeIndex

        Raises:
            MCPDataError: strict=True 且数据获取失败时抛出。
        """
        code = _to_tencent_code(symbol)
        # TQ 首源：通达信本地 TQ（127.0.0.1:17709）真实行情，
        # 失败/不可用时降级腾讯 API → 合成数据（数据源优先级 TQ → 腾讯 → 合成）。
        if _tq_stock_available():
            df = fetch_stock_ohlcv(symbol, days=days, trace_id=trace_id, adjust=adjust)
            if df is not None and not df.empty:
                return df
        try:
            raw = _fetch_kline_json(code, days, adjust)
            df = _kline_to_df(raw)
            if not df.empty:
                return df
            if strict:
                raise MCPDataError(f"MCP OHLCV 无数据 [{symbol}]")
        except MCPDataError as e:
            if strict:
                raise
            logger.warning(f"MCP OHLCV 获取失败 [{symbol}]: {e}")
        except Exception as e:
            if strict:
                raise MCPDataError(f"MCP OHLCV 异常 [{symbol}]: {e}") from e
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
            return panel, pd.DatetimeIndex(df.index)

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
        # 起点归一化到日界（无时间分量），保证同日内多次调用索引一致，
        # 避免面板交集因微秒时间戳漂移而为空（0 交易日崩溃）。
        dates = pd.date_range(
            (datetime.now() - timedelta(days=n_days)).date(),
            periods=n_days,
            freq="D",
        )
        close = base_price + np.cumsum(np.random.randn(n_days) * 0.5)
        return pd.DataFrame(
            {
                "open": close + np.random.randn(n_days) * 0.1,
                "high": close + np.abs(np.random.randn(n_days)) * 0.3,
                "low": close - np.abs(np.random.randn(n_days)) * 0.3,
                "close": close,
                "volume": np.random.randint(1000, 10000, n_days).astype(float),
            },
            index=dates,
        )


# ─── 沪深 300 代表性子集 ──────────────────────────────────

CSI300_SUBSET: list[str] = [
    "000001",
    "000002",
    "000333",
    "000568",
    "000651",
    "000725",
    "000858",
    "002027",
    "002142",
    "002230",
    "002304",
    "002371",
    "002415",
    "002475",
    "002594",
    "002714",
    "300015",
    "300059",
    "300124",
    "300274",
    "300308",
    "300413",
    "300433",
    "300450",
    "300498",
    "300502",
    "300628",
    "300750",
    "300760",
    "600000",
    "600009",
    "600028",
    "600030",
    "600031",
    "600036",
    "600048",
    "600085",
    "600104",
    "600276",
    "600309",
    "600406",
    "600436",
    "600438",
    "600519",
    "600547",
    "600585",
    "600690",
    "600809",
    "600887",
    "600900",
    "600941",
    "601012",
    "601088",
    "601127",
    "601166",
    "601288",
    "601318",
    "601328",
    "601398",
    "601628",
    "601728",
    "601766",
    "601857",
    "601888",
    "601899",
    "601985",
    "603259",
    "603288",
    "603501",
    "603659",
    "688008",
    "688036",
    "688111",
    "688122",
    "688256",
    "688396",
]

# ─── 常见 ETF 子集 ─────────────────────────────────────────

ETF_SUBSET: list[str] = [
    "510050",
    "510300",
    "510500",
    "510880",
    "512100",
    "512880",
    "513050",
    "513100",
    "515050",
    "515790",
    "516160",
    "517010",
    "518880",
    "588000",
    "159915",
    "159949",
    "159992",
    "159995",
]


def get_csi300_constituents(cache_days: int = 7) -> list[str]:
    """获取沪深300 全量成分股代码（中证官网 akshare，本地缓存降级）。

    优先读本地缓存（cache_days 天内有效）；缓存缺失/过期时经 akshare
    拉取并落盘；akshare 不可用时回退 CSI300_SUBSET（代表性子集）。

    Args:
        cache_days: 缓存有效期（天）

    Returns:
        6 位股票代码列表（全量约 300 只；回退时 77 只）。
    """
    import json as _json
    from pathlib import Path as _Path

    cache = _Path("data") / "_lineage" / "csi300_constituents.json"
    if cache.exists():
        try:
            payload = _json.loads(cache.read_text(encoding="utf-8"))
            fetched = datetime.fromisoformat(payload["fetched_at"])
            if (datetime.now() - fetched).days < cache_days and payload.get("codes"):
                return payload["codes"]
        except (ValueError, KeyError, OSError, _json.JSONDecodeError):
            pass

    codes: list[str] = []
    try:
        import akshare as ak  # type: ignore[import-untyped]

        df = ak.index_stock_cons_csindex(symbol="000300")
        if df is not None and not df.empty and "成分券代码" in df.columns:
            codes = df["成分券代码"].astype(str).str.zfill(6).tolist()
    except Exception as e:  # noqa: BLE001
        logger.warning("akshare 获取沪深300成分股失败: %s", e)

    # 过滤非法代码（NaN → "nan" 等）
    codes = [c for c in codes if len(c) == 6 and c.isdigit()]
    if not codes:
        return list(CSI300_SUBSET)

    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            _json.dumps({"fetched_at": datetime.now().isoformat(), "codes": codes}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass
    return codes


__all__ = [
    "MCPDataProvider",
    "MCPDataError",
    "CSI300_SUBSET",
    "ETF_SUBSET",
    "get_csi300_constituents",
]
