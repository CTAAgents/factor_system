"""fts.data_sources.ifind_source — 同花顺 iFinD MCP 适配器（v2.3.0）。

iFinD 是字段增强层（不参与 K 线主路径），核心价值:
    - EDB 宏观/产业链数据（独家 — 写入 edb_cache 表）
    - 期货全字段补充（hold/settle/pre_settle/oi_change）

调用方式: 通过 _call_mcp 委托 MCP 工具:
    - 期货/债券行情: bond_market_data
    - EDB 宏观/行业: get_edb_data

iFinD 代码格式（与 TQ/Wind 不同）:
    RB2509.SHF    上期所 (注意 SHF 而非 SHFE)
    M2509.DCE     大商所
    TA509.CZC     郑商所 (注意 CZC 而非 CZCE)
    IF2509.CFX    中金所 (注意 CFX 而非 CFFEX)

字段映射（iFinD → FTS）:
    openInterest / open_interest / OI → hold
    openInterestChg / oi_chg / open_interest_change → oi_change
    preSettle / pre_settle / prev_settle → pre_settle
    amt / amount / turnover → amount
    vol / volume → volume

HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

from fts.data_sources.base import BaseFuturesSource, SourceUnavailable

logger = logging.getLogger(__name__)


# ─── MCP 调用入口（可被外部注入/mock）─────────────────────


def _call_mcp(query: str) -> Any:
    """调用 iFinD MCP 工具（bond_market_data / get_edb_data）。

    本实现使用自然语言查询（与 iFinD MCP 工具规范一致）。
    生产环境需通过 FTS 启动钩子注入真实 MCP 客户端。
    当前默认抛 RuntimeError，迫使 is_available() 返回 False（生产部署前需注入）。

    Args:
        query: 自然语言查询字符串

    Returns:
        MCP 工具返回的 JSON dict（结构由 MCP 工具决定）

    Raises:
        RuntimeError: MCP 客户端未注入
    """
    # 默认实现：未注入时显式抛错，避免静默失败
    raise RuntimeError(
        "iFinD MCP 客户端未注入。请在生产环境通过 fts.bootstrap 注入 run_mcp 客户端。"
    )


# ─── 期货代码 → 交易所后缀映射（iFinD 特有）─────────────


# iFinD 代码后缀: SHF（注意不是 SHFE）/ DCE / CZC（不是 CZCE）/ CFX（不是 CFFEX）
_IFIND_EXCHANGE_MAP: list[tuple[tuple[str, ...], str]] = [
    # CFFEX 2 字母 (优先，避免与 T 误判)
    (("IF", "IH", "IC", "TF", "TS", "TL"), "CFX"),
    # CZCE 2 字母
    (("SR", "CF", "CJ", "CY", "AP", "LR", "LW", "MR", "ME", "PM", "RI", "RM",
      "RS", "SF", "SM", "TA", "TM", "UR", "WH", "WS", "WT", "ZC", "GN", "RO",
      "PF", "PK", "PX", "SA", "SH", "TC", "JR", "OI", "MA", "FG"), "CZC"),
    # SHFE 2 字母
    (("RB", "CU", "AL", "ZN", "AU", "AG", "PB", "NI", "SN", "SS", "BU", "RU",
      "FU", "SP", "WR", "HC", "FB", "BB", "AO", "AD", "BC", "EC"), "SHF"),
    # DCE 1 字母
    (("A", "B", "C", "CS", "M", "Y", "P", "L", "JD", "JM", "I", "J", "R", "RR",
      "V", "EG", "EB", "PG", "LH"), "DCE"),
]


def _infer_exchange(symbol: str) -> str:
    """根据品种代码推断 iFinD 交易所后缀。

    匹配顺序（关键 — 避免 TA 误判为 T+CFFEX）:
        1. CFFEX 2 字母 (IF/IH/IC/TF/TS/TL) → CFX
        2. CZCE 2 字母 (TA/MA/SR/...) → CZC
        3. SHFE 2 字母 (RB/CU/AU/...) → SHF
        4. DCE 1 字母 (A/B/C/M/Y/...) → DCE
        5. CFFEX 1 字母 T → CFX（兜底）
    """
    sym = symbol.upper()
    for prefixes, suffix in _IFIND_EXCHANGE_MAP:
        for p in prefixes:
            if sym.startswith(p):
                return suffix
    # 1 字母 T 兜底（CFFEX 10 年期国债）
    if sym.startswith("T"):
        return "CFX"
    raise ValueError(f"未知交易所: {symbol}")


# ─── 字段映射辅助 ────────────────────────────────────────


# iFinD 字段名 → FTS 字段名（多对一，处理 camelCase/snake_case 别名）
_FIELD_ALIASES: dict[str, list[str]] = {
    "hold": ["openInterest", "open_interest", "OI", "hold"],
    "settle": ["settle", "settlement"],
    "pre_settle": ["preSettle", "pre_settle", "prev_settle", "preSettlement"],
    "oi_change": ["openInterestChg", "oi_chg", "oi_change",
                  "open_interest_change", "openInterestChange"],
    "amount": ["amount", "amt", "turnover"],
    "volume": ["volume", "vol"],
}


def _pick(row: dict, fts_field: str) -> Any:
    """从 iFinD 行 dict 中按别名列表取第一个存在的字段。

    注: 严格判 None（不用 is not None 兼容 NaN）—— 直接在原始 dict 上调用，
    不经过 DataFrame 行合并，避免 NaN 干扰别名降级。
    """
    for alias in _FIELD_ALIASES.get(fts_field, [fts_field]):
        if alias in row and row[alias] is not None:
            return row[alias]
    return None


# ─── 适配器 ──────────────────────────────────────────────


class IFindSource(BaseFuturesSource):
    """同花顺 iFinD MCP 适配器（v2.3.0）。

    字段增强层 — 主要补充:
        - hold / settle / pre_settle / oi_change  期货全字段
        - EDB 宏观/产业链数据（独家能力，写入 edb_cache）
    不参与 K 线主路径（DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE）。
    """

    source_name: str = "IFIND"

    # ─── 期货代码转换（公开静态方法）──

    @staticmethod
    def _symbol_to_ifind(symbol: str) -> str:
        """FTS 代码 → iFinD 代码（RB2509 → RB2509.SHF）。"""
        return f"{symbol.upper()}.{_infer_exchange(symbol)}"

    # ─── 探活（不抛异常）──

    def is_available(self) -> bool:
        """探活：通过 _call_mcp 发送轻量查询。失败/超时 → False。"""
        try:
            _call_mcp("iFinD 健康检查")
            return True
        except Exception:  # noqa: BLE001
            return False

    # ─── K 线（抛 SourceUnavailable 供熔断）──

    def fetch_ohlcv(
        self,
        symbol: str,
        days: int,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """通过 MCP 拉取 K 线数据，委托给 parse_ohlcv 解析。

        Args:
            symbol: FTS 品种代码（如 "RB2509"）
            days: 回溯天数
            trace_id: 链路追踪 ID

        Returns:
            17 列 FTS schema DataFrame；失败返回 None。

        Raises:
            SourceUnavailable: MCP 连接错误或响应异常（供主路径熔断判定）
        """
        ifind_sym = self._symbol_to_ifind(symbol)
        query = (
            f"获取 {ifind_sym} 期货品种最近 {days} 天的日 K 线数据，"
            f"返回 JSON 格式，含 open/high/low/close/volume/amount/"
            f"openInterest/settle/preSettle/openInterestChg"
        )
        try:
            raw = _call_mcp(query)
        except (ConnectionError, TimeoutError) as e:
            raise SourceUnavailable(self.source_name, str(e))
        except Exception as e:
            raise SourceUnavailable(self.source_name, f"MCP error: {e}")

        return self.parse_ohlcv(raw, symbol, trace_id=trace_id)

    def parse_ohlcv_or_none(
        self,
        raw: Any,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """parse_ohlcv 的 None 包装版：异常时返回 None。"""
        try:
            return self.parse_ohlcv(raw, symbol, trace_id=trace_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] parse_ohlcv 异常 [%s]: %s",
                           self.source_name, symbol, e)
            return None

    def fetch_ohlcv_or_none(
        self,
        symbol: str,
        days: int,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """fetch_ohlcv 的 None 包装版 — 字段增强层不参与主路径熔断。

        覆写基类默认实现：捕获 SourceUnavailable 也返回 None（而非向上传播）。
        原因: iFinD 是字段增强层，MCP 失败不应熔断 K 线主路径（TQ/AKShare）。
        """
        try:
            return self.fetch_ohlcv(symbol, days, trace_id=trace_id)
        except SourceUnavailable as e:
            logger.warning("[%s] MCP 不可用，字段增强降级 [%s]: %s",
                           self.source_name, symbol, e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] fetch_ohlcv 异常 [%s]: %s",
                           self.source_name, symbol, e)
            return None

    def parse_ohlcv(
        self,
        raw: Any,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[pd.DataFrame]:
        """从 iFinD 原始响应解析为 FTS DataFrame（公开方法，可独立测试）。

        期望的输入格式:
            {"data": [{"date": ..., "open": ..., ..., "openInterest": ...,
                       "settle": ..., "preSettle": ..., "openInterestChg": ...}, ...]}

        Args:
            raw: MCP 工具返回的 JSON dict
            symbol: 品种代码（如 "RB2509"）
            trace_id: 链路追踪 ID

        Returns:
            17 列 FTS schema DataFrame，失败返回 None（供 parse_ohlcv_or_none 包装）
        """
        if not isinstance(raw, dict):
            return None
        rows = raw.get("data")
        if not isinstance(rows, list) or not rows:
            # 空数据 — 返回带 schema 的空 DataFrame
            return pd.DataFrame(columns=self._expected_columns())

        df = pd.DataFrame(rows)

        # 必填字段校验
        required = ("date", "open", "high", "low", "close", "volume")
        missing = [c for c in required if c not in df.columns]
        if missing:
            return None

        # 元数据
        df["symbol"] = symbol
        df["period"] = "daily"
        df["date"] = pd.to_datetime(df["date"]).dt.date

        # iFinD 字段映射 → FTS 字段
        # 注：直接在原始 dict 上迭代，避免 DataFrame 行合并多行字段别名时
        # 产生 NaN（NaN 通过 `is not None` 检查但语义为缺失）导致别名降级失败。
        df["hold"] = [_pick(r, "hold") for r in rows]
        df["settle"] = [_pick(r, "settle") for r in rows]
        df["pre_settle"] = [_pick(r, "pre_settle") for r in rows]
        df["oi_change"] = [_pick(r, "oi_change") for r in rows]
        df["amount"] = [_pick(r, "amount") for r in rows]

        # vwap 计算
        typical = (df["high"] + df["low"] + df["close"]) / 3
        if df["volume"].gt(0).any():
            vwap = df["amount"] / df["volume"].replace(0, pd.NA)
        else:
            vwap = typical
        df["vwap"] = pd.Series(vwap).fillna(typical)

        # 元数据列
        df["source"] = self.source_name
        df["fetched_at"] = pd.Timestamp.now()
        df["trace_id"] = trace_id

        return df[self._expected_columns()]

    # ─── 实时快照 ──

    def fetch_quote(
        self,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """通过 MCP 拉取实时快照，委托给 parse_quote 解析。"""
        ifind_sym = self._symbol_to_ifind(symbol)
        query = f"获取 {ifind_sym} 期货品种的最新报价快照，返回 JSON 格式"
        try:
            raw = _call_mcp(query)
        except (ConnectionError, TimeoutError) as e:
            raise SourceUnavailable(self.source_name, str(e))
        except Exception as e:
            raise SourceUnavailable(self.source_name, f"MCP error: {e}")

        return self.parse_quote(raw, symbol, trace_id=trace_id)

    def parse_quote(
        self,
        raw: Any,
        symbol: str,
        trace_id: str = "",
    ) -> Optional[dict[str, Any]]:
        """从 iFinD 原始响应解析为统一 dict 格式（公开方法，可独立测试）。"""
        if not isinstance(raw, dict):
            return None
        data = raw.get("data")
        if not isinstance(data, dict) or not data:
            return None

        # iFinD → FTS 字段映射
        quote = {
            "symbol": symbol,
            "last": data.get("last"),
            "bid": data.get("bid"),
            "ask": data.get("ask"),
            "open": data.get("open"),
            "high": data.get("high"),
            "low": data.get("low"),
            "volume": _pick(data, "volume"),
            "amount": _pick(data, "amount"),
            "hold": _pick(data, "hold"),
            "settle": _pick(data, "settle"),
            "pre_settle": _pick(data, "pre_settle"),
            "oi_change": _pick(data, "oi_change"),
            "source": self.source_name,
            "trace_id": trace_id,
            "fetched_at": pd.Timestamp.now().isoformat(),
        }
        return quote

    # ─── EDB 宏观/产业链（iFinD 独家能力）──

    def fetch_edb(
        self,
        indicator: str,
        start_date: str = "",
        end_date: str = "",
        trace_id: str = "",
    ) -> Optional[list[dict[str, Any]]]:
        """通过 MCP 拉取 EDB 宏观/行业经济指标（iFinD 独家）。

        调用工具: get_edb_data（自然语言查询）。
        数据落点: edb_cache 表（indicator, date, source, value, unit, ...）。

        Args:
            indicator: 指标代码（如 "M0001396"）或中文名称（如 "PTA产量"）
            start_date: 起始日期 YYYY-MM-DD（可选）
            end_date: 截止日期 YYYY-MM-DD（可选）
            trace_id: 链路追踪 ID

        Returns:
            EDB 数据点列表（每项含 indicator/date/value/unit 等），失败返回 None。
        """
        date_range = ""
        if start_date and end_date:
            date_range = f"（{start_date}-{end_date}）"
        elif start_date:
            date_range = f"（{start_date}起）"

        query = f"{indicator}{date_range}"
        try:
            raw = _call_mcp(query)
        except (ConnectionError, TimeoutError) as e:
            logger.warning("[%s] EDB MCP 不可用 [%s]: %s",
                           self.source_name, indicator, e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] EDB MCP 异常 [%s]: %s",
                           self.source_name, indicator, e)
            return None

        if not isinstance(raw, dict):
            return None
        data = raw.get("data")
        if not isinstance(data, list):
            return []

        # 标准化 EDB 数据点（含 trace_id 注入）
        result = []
        for row in data:
            if not isinstance(row, dict):
                continue
            result.append({
                "indicator": row.get("indicator", indicator),
                "indicator_name": row.get("indicator_name", ""),
                "date": row.get("date", ""),
                "value": row.get("value"),
                "unit": row.get("unit", ""),
                "yoy": row.get("yoy"),
                "source": self.source_name,
                "trace_id": trace_id,
                "fetched_at": pd.Timestamp.now().isoformat(),
            })
        return result

    # ─── 内部辅助 ──

    @staticmethod
    def _expected_columns() -> list[str]:
        """FTS kline_cache 完整 17 列。"""
        return [
            "symbol", "period", "date", "open", "high", "low", "close",
            "volume", "amount", "hold", "settle", "pre_settle", "oi_change",
            "vwap", "source", "fetched_at", "trace_id",
        ]


__all__ = ["IFindSource", "_call_mcp", "_infer_exchange"]
