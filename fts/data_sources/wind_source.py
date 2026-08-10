"""fts.data_sources.wind_source — 万得 Wind MCP 适配器（v2.3.0）。

Wind 是字段增强层（不参与 K 线主路径），主要用于补充：
    - settle / pre_settle    (结算价)
    - oi_change              (日增仓)
    - 期权 IV / PCR
调用方式: 通过 _call_mcp 委托 mx_comprehensive_finance_data 或 mx_ashare_finance_data。

字段映射（Wind → FTS）:
    oi / open_interest       → hold
    oi_chg / open_interest_change → oi_change
    amt / amount             → amount
    vol / volume             → volume
    settle                   → settle
    pre_settle               → pre_settle

HARNESS §5.3 契约优先: 实现 BaseFuturesSource 抽象方法。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

from fts.data_sources.base import BaseFuturesSource, SourceUnavailable

logger = logging.getLogger(__name__)


# ─── MCP 调用入口（可被外部注入/mock）─────────────────────


# 注入的 MCP 客户端（生产环境通过 fts.bootstrap 调用 set_mcp_handler 注入）
_mcp_handler: Optional[Any] = None


def set_mcp_handler(handler: Any) -> None:
    """注入 Wind MCP 客户端（v2.60.0，GAP-F04）。

    Args:
        handler: 可调用对象，签名 ``handler(query: str) -> Any``（返回 MCP JSON dict）
    """
    global _mcp_handler
    _mcp_handler = handler
    logger.info("[WIND] MCP 客户端已注入")


def _call_mcp(query: str) -> Any:
    """调用 Wind MCP 工具（mx_ashare_finance_data / mx_comprehensive_finance_data）。

    行为分级（v2.60.0，GAP-F04）:
        - 已注入客户端（set_mcp_handler）→ 直接调用
        - 配置 mcp_enabled=true 但未注入 → 抛 RuntimeError（显式初始化报错提示）
        - 未启用（mcp_enabled=false）→ 返回 None（明确降级，跳过增强字段）

    Args:
        query: 自然语言查询字符串

    Returns:
        MCP 工具返回的 JSON dict（结构由 mx 工具决定）；未启用时返回 None
    """
    if _mcp_handler is not None:
        return _mcp_handler(query)
    from fts.config.settings import get_config

    if get_config().mcp_enabled:
        raise RuntimeError(
            "Wind MCP 已启用但客户端未注入。请调用 fts.data_sources.wind_source.set_mcp_handler(handler) 初始化。"
        )
    logger.debug("[WIND] MCP 未启用，跳过增强字段查询: %s", query[:50])
    return None


# ─── 字段映射辅助 ────────────────────────────────────────


# Wind 字段名 → FTS 字段名（多对一，处理别名）
_FIELD_ALIASES: dict[str, list[str]] = {
    "hold": ["oi", "open_interest", "hold"],
    "settle": ["settle", "settlement"],
    "pre_settle": ["pre_settle", "prev_settle", "pre_settlement"],
    "oi_change": ["oi_chg", "oi_change", "open_interest_change", "oi_diff"],
    "amount": ["amount", "amt", "turnover"],
    "volume": ["volume", "vol"],
}


def _pick(row: dict, fts_field: str) -> Any:
    """从 Wind 行 dict 中按别名列表取第一个存在的字段。"""
    for alias in _FIELD_ALIASES.get(fts_field, [fts_field]):
        if alias in row and row[alias] is not None:
            return row[alias]
    return None


# ─── 适配器 ──────────────────────────────────────────────


class WindSource(BaseFuturesSource):
    """万得 Wind MCP 适配器（v2.3.0）。

    字段增强层 — 主要补充 settle/oi_change/期权 IV 等 TQ 不全的字段。
    不参与 K 线主路径（DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE）。
    """

    source_name: str = "WIND"

    # ─── 探活（不抛异常）──

    def is_available(self) -> bool:
        """探活：通过 _call_mcp 发送轻量查询。失败/超时/未启用 → False。"""
        try:
            raw = _call_mcp("Wind 健康检查")
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
            symbol: 品种代码（如 "RB2509.SHFE"）
            days: 回溯天数
            trace_id: 链路追踪 ID

        Returns:
            17 列 FTS schema DataFrame；失败返回 None。

        Raises:
            SourceUnavailable: MCP 连接错误或响应异常（供主路径熔断判定）
        """
        query = (
            f"获取 {symbol} 期货品种最近 {days} 天的日 K 线数据，"
            f"返回 JSON 格式，含 open/high/low/close/volume/amount/"
            f"open_interest/settle/pre_settle/open_interest_change"
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
        原因: Wind 是字段增强层，MCP 失败不应熔断 K 线主路径（TQ/AKShare）。
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
        """从 Wind 原始响应解析为 FTS DataFrame（公开方法，可独立测试）。

        期望的输入格式:
            {"data": [{"date": ..., "open": ..., ..., "oi": ..., "settle": ...}, ...]}

        Args:
            raw: MCP 工具返回的 JSON dict
            symbol: 品种代码（如 "RB2509.SHFE"）
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

        # Wind 字段映射 → FTS 字段
        # 注：直接在原始 dict 上迭代，避免 DataFrame 行合并多行字段别名时
        # 产生 NaN（NaN 通过 `is not None` 检查但语义为缺失）导致别名降级失败。
        df["hold"] = [_pick(r, "hold") for r in rows]
        df["settle"] = [_pick(r, "settle") for r in rows]
        df["pre_settle"] = [_pick(r, "pre_settle") for r in rows]
        df["oi_change"] = [_pick(r, "oi_change") for r in rows]
        df["amount"] = [_pick(r, "amount") for r in rows]

        # vwap 计算
        typical = (df["high"] + df["low"] + df["close"]) / 3
        if "amount" in df.columns and df["volume"].gt(0).any():
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
        query = f"获取 {symbol} 期货品种的最新报价快照，返回 JSON 格式"
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
        """从 Wind 原始响应解析为统一 dict 格式（公开方法，可独立测试）。"""
        if not isinstance(raw, dict):
            return None
        data = raw.get("data")
        if not isinstance(data, dict) or not data:
            return None

        # Wind → FTS 字段映射
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


__all__ = ["WindSource", "_call_mcp", "set_mcp_handler"]
