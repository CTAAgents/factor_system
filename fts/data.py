"""
fts.data — FTS 数据层集成入口

基于腾讯自选股 MCP (akshare) 为 FTS 因子引擎提供统一数据访问接口。
替换原 Data-Core (datacore) 依赖，支持 A 股/ETF/期货因子演化。

数据流:
    因子引擎 → FTSDataProvider → MCPDataProvider(akshare) → 腾讯/东方财富 API
                              → FundamentalProvider → MCP westock API
                              → FuturesDataProvider → DuckDB kline_cache

HARNESS §契约优先: 所有数据接口通过本模块定义。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional

import numpy as np
import pandas as pd

from .data_mcp import MCPDataProvider, MCPDataError
from .data_fundamental import FundamentalProvider, FundamentalDataError
from .data_futures import FuturesDataProvider, FuturesDataError

logger = logging.getLogger(__name__)


# ─── 数据不可用异常 ───────────────────────────────────────

class DataUnavailableError(RuntimeError):
    """数据不可用 — 所有数据源均失效时抛出。"""


# ─── FTS 统一数据提供者 ───────────────────────────────────

class FTSDataProvider:
    """FTS 统一数据提供者 — 基于 MCP (akshare) 的数据访问层。

    职责:
        - 提供因子计算所需的 A 股和 ETF 的 OHLCV 数据
        - 所有数据以 pandas DataFrame 格式返回（兼容 factor_program 接口）
        - 自动降级：MCP → 合成数据
        - 全链路 trace_id 传播

    用法:
        provider = FTSDataProvider()
        ohlcv = provider.get_ohlcv("000001", days=500)
        df = provider.get_etf_ohlcv("510050", days=500)
    """

    def __init__(self, mcp_provider: Optional[MCPDataProvider] = None,
                 fundamental_provider: Optional[FundamentalProvider] = None,
                 futures_provider: Optional[FuturesDataProvider] = None):
        self._mcp = mcp_provider or MCPDataProvider()
        self._fundamental = fundamental_provider or FundamentalProvider(mcp_available=False)
        self._futures = futures_provider or FuturesDataProvider()
        # 期货基本面 provider（库存/基差/仓单），当前无实现，注入点为 None
        self._futures_fundamental = None

    # ── 基本面注入 ──

    def set_fundamental_provider(self, provider: FundamentalProvider) -> None:
        """设置基本面数据提供者（外部注入用）。"""
        self._fundamental = provider

    def enrich_with_fundamental(self, df: pd.DataFrame, symbol: str, *,
                                trace_id: str = "") -> pd.DataFrame:
        """将基本面字段注入 OHLCV DataFrame。

        Args:
            df: OHLCV DataFrame
            symbol: 股票代码
            trace_id: HARNESS trace_id

        Returns:
            DataFrame — 新增 pe_ttm, pb, total_market_cap 等基本面列。
        """
        return self._fundamental.enrich_ohlcv(df, symbol, trace_id=trace_id)

    # ── 单标的 OHLCV ──

    def get_ohlcv(self, symbol: str, *,
                  days: int = 500,
                  adjust: str = "qfq",
                  trace_id: str = "",
                  fundamental: bool = False,
                  ) -> pd.DataFrame:
        """获取股票/ETF OHLCV K线数据。

        Args:
            symbol: 股票/ETF 代码（如 "000001" / "510050"）
            days: 回溯天数
            adjust: 复权方式 ("qfq"前复权 / "hfq"后复权 / ""不复权)
            trace_id: HARNESS trace_id
            fundamental: 是否注入基本面字段（pe_ttm, pb 等）

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume
            If fundamental=True, also includes pe_ttm, pb, etc.

        Raises:
            DataUnavailableError: 所有数据源不可用
        """
        try:
            df = self._mcp.get_ohlcv(symbol, days=days, adjust=adjust, trace_id=trace_id)
            if df is not None and not df.empty and "close" in df.columns:
                if fundamental:
                    df = self._fundamental.enrich_ohlcv(df, symbol, trace_id=trace_id)
                return df
        except (MCPDataError, Exception) as e:
            logger.warning(f"MCP OHLCV 获取失败 [{symbol}]: {e}")

        # 回退：合成数据（确保系统可运行）
        logger.warning(f"使用合成数据回退 [{symbol}]")
        df = self.synthesize_ohlcv(n_days=days, base_price=15.0, seed=42)
        if fundamental:
            df = self._fundamental.enrich_ohlcv(df, symbol, trace_id=trace_id)
        return df

    # ── ETF 专用接口 ──

    def get_etf_ohlcv(self, symbol: str, *,
                      days: int = 500,
                      adjust: str = "qfq",
                      trace_id: str = "",
                      ) -> pd.DataFrame:
        """获取 ETF OHLCV 数据。"""
        return self.get_ohlcv(symbol, days=days, adjust=adjust, trace_id=trace_id)

    # ── 面板数据 ──

    def get_csi300_panel(self, days: int = 500,
                         max_stocks: int = 50,
                         trace_id: str = "",
                         fundamental: bool = False,
                         ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取沪深 300 成分股批量 OHLCV 面板数据。

        Args:
            days: 回溯天数
            max_stocks: 最大成分股数（0 = 使用全部）
            trace_id: HARNESS trace_id
            fundamental: 是否注入基本面字段

        Returns:
            (panel, common_dates)
            panel: dict[symbol, OHLCV DataFrame]（含基本面列如果 fundamental=True）
            common_dates: 所有股票共有日期
        """
        from .data_mcp import CSI300_SUBSET
        symbols = CSI300_SUBSET[:max_stocks] if max_stocks > 0 else CSI300_SUBSET

        panel: dict[str, pd.DataFrame] = {}
        dates_set: set[pd.Timestamp] = set()
        first = True

        for sym in symbols:
            try:
                df = self.get_ohlcv(sym, days=days, adjust="qfq",
                                    trace_id=trace_id, fundamental=fundamental)
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
            df = self.synthesize_ohlcv(n_days=days, base_price=15.0, seed=42)
            if fundamental:
                df = self._fundamental.enrich_ohlcv(df, "SYNTHETIC", trace_id=trace_id)
            panel["SYNTHETIC"] = df
            return panel, df.index

        common_dates = pd.DatetimeIndex(sorted(dates_set))
        return panel, common_dates

    def get_etf_panel(self, days: int = 500,
                      trace_id: str = "") -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取常见 ETF 批量 OHLCV 面板数据。"""
        from .data_mcp import ETF_SUBSET
        return self._mcp.get_stock_panel(
            ETF_SUBSET, days=days, adjust="qfq", trace_id=trace_id,
        )

    # ── 期货接口 ──

    def get_futures_ohlcv(self, symbol: str, *,
                          days: int = 500,
                          trace_id: str = "",
                          ) -> pd.DataFrame:
        """获取期货连续合约 OHLCV 数据。

        Args:
            symbol: 期货连续合约代码（如 "RB0" / "CU0" / "IF0"）
            days: 回溯天数
            trace_id: HARNESS trace_id

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume, hold, settle
        """
        return self._futures.get_ohlcv(symbol, days=days, trace_id=trace_id)

    def get_futures_panel(
        self,
        symbols: list[str] | None = None,
        days: int = 500,
        trace_id: str = "",
    ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取多期货品种 OHLCV 面板数据。

        Args:
            symbols: 期货合约列表，默认使用 FUTURES_CORE_SUBSET
            days: 回溯天数
            trace_id: HARNESS trace_id

        Returns:
            (panel, common_dates)
        """
        if symbols is None:
            from .data_futures import FUTURES_CORE_SUBSET
            symbols = FUTURES_CORE_SUBSET
        return self._futures.get_futures_panel(symbols, days=days, trace_id=trace_id)

    # ── 期货基本面（库存 / 基差 / 仓单）──

    def enrich_futures_fundamental(self, df: pd.DataFrame, symbol: str, *,
                                   trace_id: str = "") -> pd.DataFrame:
        """将期货基本面字段注入 OHLCV DataFrame。

        注入字段（可用时）:
            - inventory: 库存
            - inventory_change: 库存增减
            - spot_price: 现货价格
            - near_basis: 近月基差
            - dom_basis: 主力基差
            - near_basis_rate: 近月基差率
            - dom_basis_rate: 主力基差率

        Args:
            df: OHLCV DataFrame
            symbol: 期货连续合约代码（如 "RB0"）
            trace_id: HARNESS trace_id

        Returns:
            DataFrame — 新增基本面列（无数据时用 NaN 填充）。
        """
        # 缺失列用 NaN 填充（无 provider 时也执行，保证列结构一致）
        provider = self._futures_fundamental
        if provider is not None:
            # 注入库存
            try:
                inv_df = provider.get_inventory(symbol)
                if not inv_df.empty and "inventory" in inv_df.columns:
                    df = df.join(inv_df[["inventory", "change"]].rename(
                        columns={"inventory": "fut_inventory", "change": "fut_inventory_chg"}
                    ), how="left")
            except Exception:  # noqa: BLE001
                pass

            # 注入基差
            try:
                basis_df = provider.get_basis(symbol, days=60)
                if not basis_df.empty:
                    basis_cols = ["spot_price", "near_basis", "dom_basis",
                                  "near_basis_rate", "dom_basis_rate"]
                    available = [c for c in basis_cols if c in basis_df.columns]
                    if available:
                        rename = {c: f"fut_{c}" for c in available}
                        df = df.join(basis_df[available].rename(columns=rename), how="left")
            except Exception:  # noqa: BLE001
                pass

        # 缺失列用 NaN 填充
        for col in ["fut_inventory", "fut_inventory_chg", "fut_spot_price",
                     "fut_near_basis", "fut_dom_basis",
                     "fut_near_basis_rate", "fut_dom_basis_rate"]:
            if col not in df.columns:
                df[col] = float("nan")

        return df

    def get_stock_panel(self, symbols: list[str], days: int = 500,
                        trace_id: str = "") -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取任意股票列表的 OHLCV 面板数据。"""
        return self._mcp.get_stock_panel(
            symbols, days=days, adjust="qfq", trace_id=trace_id,
        )

    # ── 搜索接口 ──

    def search_symbol(self, query: str, limit: int = 10) -> list[dict[str, str]]:
        """搜索股票/ETF 代码。"""
        return self._mcp.search_symbol(query, limit=limit)

    # ── 合成数据 ──

    @staticmethod
    def synthesize_ohlcv(n_days: int = 500,
                         base_price: float = 15.0,
                         seed: int = 42) -> pd.DataFrame:
        """合成 OHLCV 数据（无网络时使用）。"""
        return MCPDataProvider.synthesize_ohlcv(
            n_days=n_days, base_price=base_price, seed=seed,
        )


# ─── 缺省实例（全局单例）───────────────────────────────────

_default_provider: Optional[FTSDataProvider] = None


def get_data_provider() -> FTSDataProvider:
    """获取全局 FTSDataProvider 实例（惰性初始化）。"""
    global _default_provider
    if _default_provider is None:
        _default_provider = FTSDataProvider()
    return _default_provider


__all__ = [
    "FTSDataProvider",
    "DataUnavailableError",
    "FundamentalProvider",
    "FundamentalDataError",
    "FuturesDataProvider",
    "FuturesDataError",
    "get_data_provider",
    "get_fundamental_provider",
]
