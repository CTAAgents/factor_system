"""
fts.data — FTS 数据层集成入口（期货因子系统）

股票剥离（A 股/ETF 迁至 fts-stock）后，FTS 主系统定位期货因子系统，
统一通过 FuturesDataProvider / AkshareFuturesFundamentalProvider
提供期货行情与基本面数据。

数据流:
    因子引擎 → FTSDataProvider → FuturesDataProvider → DuckDB kline_cache
                              → AkshareFuturesFundamentalProvider → AKShare

HARNESS §契约优先: 所有数据接口通过本模块定义。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd

from .data_futures import FuturesDataProvider, FuturesDataError
from .data_futures_fundamental import (
    AkshareFuturesFundamentalProvider,
    get_futures_fundamental_provider,
)

logger = logging.getLogger(__name__)


# ─── 数据不可用异常 ───────────────────────────────────────


class DataUnavailableError(RuntimeError):
    """数据不可用 — 所有数据源均失效时抛出。"""


# ─── FTS 统一数据提供者 ───────────────────────────────────


class FTSDataProvider:
    """FTS 统一数据提供者 — 期货因子系统的数据访问层。

    职责:
        - 提供因子计算所需的期货连续合约 OHLCV 数据
        - 所有数据以 pandas DataFrame 格式返回（兼容 factor_program 接口）
        - 自动降级：期货数据源 → 合成数据
        - 全链路 trace_id 传播

    用法:
        provider = FTSDataProvider()
        ohlcv = provider.get_futures_ohlcv("RB0", days=500)
    """

    def __init__(
        self,
        mcp_provider: Optional[Any] = None,
        fundamental_provider: Optional[Any] = None,
        futures_provider: Optional[FuturesDataProvider] = None,
        futures_fundamental_provider: Optional[AkshareFuturesFundamentalProvider] = None,
    ):
        # 注: mcp_provider / fundamental_provider 参数为兼容旧调用
        # （FTSDataProvider(mcp_provider=...)）保留，股票剥离后不再实例化。
        self._futures = futures_provider or FuturesDataProvider()
        # 期货基本面 provider（库存/基差，AKShare；仓单 SHFE/DCE 反爬不可用，见 08-gap-analysis.md GAP）
        self._futures_fundamental = futures_fundamental_provider or get_futures_fundamental_provider()

    # ── 期货接口 ──

    def get_futures_ohlcv(
        self,
        symbol: str,
        *,
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
            from .data_futures import get_dynamic_core_subset

            symbols = get_dynamic_core_subset()
        return self._futures.get_futures_panel(symbols, days=days, trace_id=trace_id)

    # ── 期货基本面（库存 / 基差 / 仓单）──

    def enrich_futures_fundamental(self, df: pd.DataFrame, symbol: str, *, trace_id: str = "") -> pd.DataFrame:
        """将期货基本面字段注入 OHLCV DataFrame。

        注入字段（可用时）:
            - inventory: 库存
            - inventory_change: 库存增减
            - warehouse_receipt: 交易所仓单（GAP-091 阶段 1 仅 CZCE/GFEX 品种）
            - warehouse_receipt_change: 仓单增减
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
                    df = df.join(
                        inv_df[["inventory", "change"]].rename(
                            columns={"inventory": "fut_inventory", "change": "fut_inventory_chg"}
                        ),
                        how="left",
                    )
            except Exception:  # noqa: BLE001
                pass

            # 注入基差
            try:
                basis_df = provider.get_basis(symbol, days=60)
                if not basis_df.empty:
                    basis_cols = ["spot_price", "near_basis", "dom_basis", "near_basis_rate", "dom_basis_rate"]
                    available = [c for c in basis_cols if c in basis_df.columns]
                    if available:
                        rename = {c: f"fut_{c}" for c in available}
                        df = df.join(basis_df[available].rename(columns=rename), how="left")
            except Exception:  # noqa: BLE001
                pass

            # 注入仓单（GAP-091 阶段 1：CZCE/GFEX 品种真实，SHFE/DCE 降级 NaN）
            try:
                wr_df = provider.get_warehouse_receipt(symbol)
                if not wr_df.empty and "warehouse_receipt" in wr_df.columns:
                    df = df.join(
                        wr_df[["warehouse_receipt", "change"]].rename(
                            columns={
                                "warehouse_receipt": "fut_warehouse_receipt",
                                "change": "fut_warehouse_receipt_chg",
                            }
                        ),
                        how="left",
                    )
            except Exception:  # noqa: BLE001
                pass

        # 缺失列用 NaN 填充
        for col in [
            "fut_inventory",
            "fut_inventory_chg",
            "fut_warehouse_receipt",
            "fut_warehouse_receipt_chg",
            "fut_spot_price",
            "fut_near_basis",
            "fut_dom_basis",
            "fut_near_basis_rate",
            "fut_dom_basis_rate",
        ]:
            if col not in df.columns:
                df[col] = float("nan")

        return df

    # ── 合成数据 ──

    @staticmethod
    def synthesize_ohlcv(n_days: int = 500, base_price: float = 15.0, seed: int = 42) -> pd.DataFrame:
        """合成 OHLCV 数据（数据源不可用时的降级回退）。"""
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
    "FuturesDataProvider",
    "FuturesDataError",
    "get_data_provider",
]
