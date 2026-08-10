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
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from fts.data_sources.base import BaseFuturesSource, SourceUnavailable

logger = logging.getLogger(__name__)


# ─── MCP 调用入口（可被外部注入/mock）─────────────────────


# 注入的 MCP 客户端（生产环境通过 fts.bootstrap 调用 set_mcp_handler 注入）
_mcp_handler: Optional[Any] = None


def set_mcp_handler(handler: Any) -> None:
    """注入 iFinD MCP 客户端（v2.60.0，GAP-F04）。

    Args:
        handler: 可调用对象，签名 ``handler(query: str) -> Any``（返回 MCP JSON dict）
    """
    global _mcp_handler
    _mcp_handler = handler
    logger.info("[IFIND] MCP 客户端已注入")


def _call_mcp(query: str) -> Any:
    """调用 iFinD MCP 工具（bond_market_data / get_edb_data）。

    行为分级（v2.60.0，GAP-F04）:
        - 已注入客户端（set_mcp_handler）→ 直接调用
        - 配置 mcp_enabled=true 但未注入 → 抛 RuntimeError（显式初始化报错提示）
        - 未启用（mcp_enabled=false）→ 返回 None（明确降级，跳过增强字段）

    Args:
        query: 自然语言查询字符串

    Returns:
        MCP 工具返回的 JSON dict（结构由 MCP 工具决定）；未启用时返回 None
    """
    if _mcp_handler is not None:
        return _mcp_handler(query)
    from fts.config.settings import get_config

    if get_config().mcp_enabled:
        raise RuntimeError(
            "iFinD MCP 已启用但客户端未注入。请调用 fts.data_sources.ifind_source.set_mcp_handler(handler) 初始化。"
        )
    logger.debug("[IFIND] MCP 未启用，跳过增强字段查询: %s", query[:50])
    return None


# ─── 期货代码 → 交易所后缀映射（iFinD 特有）─────────────


# iFinD 代码后缀: SHF（注意不是 SHFE）/ DCE / CZC（不是 CZCE）/ CFX（不是 CFFEX）
_IFIND_EXCHANGE_MAP: list[tuple[tuple[str, ...], str]] = [
    # CFFEX 2 字母 (优先，避免与 T 误判)
    (("IF", "IH", "IC", "TF", "TS", "TL"), "CFX"),
    # CZCE 2 字母
    (
        (
            "SR",
            "CF",
            "CJ",
            "CY",
            "AP",
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
        ),
        "CZC",
    ),
    # SHFE 2 字母
    (
        (
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
        ),
        "SHF",
    ),
    # DCE 1 字母
    (("A", "B", "C", "CS", "M", "Y", "P", "L", "JD", "JM", "I", "J", "R", "RR", "V", "EG", "EB", "PG", "LH"), "DCE"),
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
    "oi_change": ["openInterestChg", "oi_chg", "oi_change", "open_interest_change", "openInterestChange"],
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
    不参与 K 线主路径（DUCKDB_CACHE → TDX_LOCAL → TQ_PYTHON → AKSHARE）。
    """

    source_name: str = "IFIND"

    # ─── 期货代码转换（公开静态方法）──

    @staticmethod
    def _symbol_to_ifind(symbol: str) -> str:
        """FTS 代码 → iFinD 代码（RB2509 → RB2509.SHF）。"""
        return f"{symbol.upper()}.{_infer_exchange(symbol)}"

    # ─── 探活（不抛异常）──

    def is_available(self) -> bool:
        """探活：通过 _call_mcp 发送轻量查询。失败/超时/未启用 → False。"""
        try:
            raw = _call_mcp("iFinD 健康检查")
            return raw is not None
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
            logger.warning("[%s] parse_ohlcv 异常 [%s]: %s", self.source_name, symbol, e)
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
            logger.warning("[%s] MCP 不可用，字段增强降级 [%s]: %s", self.source_name, symbol, e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] fetch_ohlcv 异常 [%s]: %s", self.source_name, symbol, e)
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
            logger.warning("[%s] EDB MCP 不可用 [%s]: %s", self.source_name, indicator, e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning("[%s] EDB MCP 异常 [%s]: %s", self.source_name, indicator, e)
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
            result.append(
                {
                    "indicator": row.get("indicator", indicator),
                    "indicator_name": row.get("indicator_name", ""),
                    "date": row.get("date", ""),
                    "value": row.get("value"),
                    "unit": row.get("unit", ""),
                    "yoy": row.get("yoy"),
                    "source": self.source_name,
                    "trace_id": trace_id,
                    "fetched_at": pd.Timestamp.now().isoformat(),
                }
            )
        return result

    # ─── EDB 宏观时序（edb_cache 缓存 → miss 拉取 → 幂等写回）──

    def get_macro_series(
        self,
        indicator: str,
        start_date: str = "",
        end_date: str = "",
        db_path: Optional[Path] = None,
        trace_id: str = "",
    ) -> Optional[pd.Series]:
        """获取 EDB 宏观指标时序（优先读 edb_cache 缓存，miss 拉取并写回）。

        Args:
            indicator: 指标中文名（如 "中国出口金额当月值"）
            start_date: 起始日期 YYYY-MM-DD（可选）
            end_date: 截止日期 YYYY-MM-DD（可选）
            db_path: DuckDB 路径（默认 data/fts_history.duckdb）
            trace_id: 链路追踪 ID

        Returns:
            DatetimeIndex Series（date → value），缓存与拉取均失败返回 None。
        """
        if db_path is None:
            db_path = Path(__file__).resolve().parent.parent / "data" / "fts_history.duckdb"
        db_path = Path(db_path)

        # 1) 查 edb_cache 缓存
        cached = self._read_edb_cache(db_path, indicator, start_date, end_date)
        if cached is not None and not cached.empty:
            logger.debug("[%s] edb_cache 命中 [%s]: %d 点", self.source_name, indicator, len(cached))
            return cached

        # 2) miss → 拉取 EDB
        rows = self.fetch_edb(indicator, start_date, end_date, trace_id=trace_id)
        if not rows:
            return None

        # 3) 幂等写回缓存
        self._write_edb_cache(db_path, rows)

        # 4) 构造 Series
        series = self._rows_to_series(rows)
        logger.info("[%s] EDB 拉取 [%s]: %d 点，已写 edb_cache", self.source_name, indicator, len(series))
        return series

    @staticmethod
    def _read_edb_cache(
        db_path: Path,
        indicator: str,
        start_date: str = "",
        end_date: str = "",
    ) -> Optional[pd.Series]:
        """从 edb_cache 读取指标时序。表不存在/无数据 → None。"""
        if not db_path.exists():
            return None
        try:
            import duckdb  # type: ignore[import-untyped]

            con = duckdb.connect(str(db_path), read_only=True)
            try:
                sql = """
                    SELECT date, value FROM edb_cache
                    WHERE indicator = ? AND value IS NOT NULL
                """
                params: list[Any] = [indicator]
                if start_date:
                    sql += " AND date >= CAST(? AS DATE)"
                    params.append(start_date)
                if end_date:
                    sql += " AND date <= CAST(? AS DATE)"
                    params.append(end_date)
                sql += " ORDER BY date"
                df = con.execute(sql, params).df()
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("[IFIND] edb_cache 读取失败 [%s]: %s", indicator, e)
            return None
        if df.empty:
            return None
        return pd.Series(df["value"].values, index=pd.to_datetime(df["date"]))

    @staticmethod
    def _write_edb_cache(db_path: Path, rows: list[dict[str, Any]]) -> None:
        """将 EDB 数据点幂等写入 edb_cache（INSERT OR REPLACE）。失败不抛异常。"""
        if not rows:
            return
        try:
            import duckdb  # type: ignore[import-untyped]
            from fts.data_sources.migrate import migrate_schema

            # migrate_schema 接收 db_path（非连接），先建表
            migrate_schema(str(db_path))
            con = duckdb.connect(str(db_path))
            try:
                for row in rows:
                    con.execute(
                        """
                        INSERT OR REPLACE INTO edb_cache
                            (indicator, date, value, unit, source, fetched_at, trace_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            row.get("indicator", ""),
                            str(row.get("date", ""))[:10],
                            row.get("value"),
                            row.get("unit", ""),
                            row.get("source", "IFIND"),
                            pd.Timestamp.now().isoformat(),
                            row.get("trace_id", ""),
                        ],
                    )
            finally:
                con.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("[IFIND] edb_cache 写入失败: %s", e)

    @staticmethod
    def _rows_to_series(rows: list[dict[str, Any]]) -> pd.Series:
        """将 EDB 数据点列表转换为 DatetimeIndex Series（按日期升序）。"""
        valid = [r for r in rows if r.get("date") and r.get("value") is not None]
        valid.sort(key=lambda r: str(r["date"]))
        if not valid:
            return pd.Series(dtype=float)
        return pd.Series(
            [float(r["value"]) for r in valid],
            index=pd.to_datetime([str(r["date"])[:10] for r in valid]),
        )

    # ─── 内部辅助 ──

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


__all__ = ["IFindSource", "_call_mcp", "set_mcp_handler", "_infer_exchange"]
