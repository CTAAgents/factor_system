"""
fts/cross_market/data_adapter.py — 跨市场数据适配层

解决期货和股票市场数据格式差异，让同一因子代码能在两个市场运行。

核心差异:
    - 期货数据: close/high/low/volume/open_interest
    - 股票数据: close/high/low/volume（无 open_interest），有复权因子
    - ETF 数据: 同股票格式

HARNESS §契约优先: 输出格式统一为 open/high/low/close/volume 5 个核心字段。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── 目标市场常量 ─────────────────────────────────────────

TARGET_MARKET_STOCK = "stock"
TARGET_MARKET_ETF = "etf"
TARGET_MARKET_FUTURES = "futures"

# 统一输出必须包含的核心字段
CORE_FIELDS = ["open", "high", "low", "close", "volume"]

# 期货特有字段（适配时填充默认值）
FUTURES_SPECIFIC_FIELDS = ["open_interest", "hold", "settle"]


class CrossMarketDataAdapter:
    """跨市场数据适配器 — 统一数据格式供因子执行。

    职责:
        1. 统一数据格式为 open/high/low/close/volume
        2. 填充目标市场缺失字段
        3. 处理复权差异
        4. 路由到正确的数据源
    """

    def __init__(self):
        from fts.data import FTSDataProvider

        self._provider = FTSDataProvider()

    # ── 数据获取 ──────────────────────────────────────────

    def get_panel(
        self,
        target_market: str,
        days: int = 500,
        max_stocks: int = 0,
        trace_id: str = "",
    ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取目标市场面板数据，统一格式。

        Args:
            target_market: 目标市场 (stock/etf/futures)
            days: 回溯天数
            max_stocks: 最大品种数（0=全量，仅 stock 有效）
            trace_id: HARNESS trace_id

        Returns:
            (panel, common_dates)
            panel: dict[symbol, 统一格式 DataFrame]
            common_dates: 所有品种共有日期
        """
        if target_market == TARGET_MARKET_STOCK:
            return self._get_stock_panel(days, max_stocks, trace_id)
        if target_market == TARGET_MARKET_ETF:
            return self._get_etf_panel(days, trace_id)
        if target_market == TARGET_MARKET_FUTURES:
            return self._get_futures_panel(days, trace_id)
        raise ValueError(f"不支持的目标市场: {target_market}")

    def _get_stock_panel(
        self,
        days: int,
        max_stocks: int,
        trace_id: str,
    ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取沪深300成分股面板数据。"""
        from fts.data_mcp import CSI300_SUBSET

        symbols = CSI300_SUBSET[:max_stocks] if max_stocks > 0 else CSI300_SUBSET
        panel, dates = self._provider.get_stock_panel(
            symbols,
            days=days,
            trace_id=trace_id,
        )
        return self._adapt_panel(panel, TARGET_MARKET_STOCK), dates

    def _get_etf_panel(
        self,
        days: int,
        trace_id: str,
    ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取ETF面板数据。"""
        panel, dates = self._provider.get_etf_panel(days=days, trace_id=trace_id)
        return self._adapt_panel(panel, TARGET_MARKET_ETF), dates

    def _get_futures_panel(
        self,
        days: int,
        trace_id: str,
    ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取期货面板数据。"""
        panel, dates = self._provider.get_futures_panel(days=days, trace_id=trace_id)
        return self._adapt_panel(panel, TARGET_MARKET_FUTURES), dates

    # ── 统一格式适配 ──────────────────────────────────────

    def _adapt_panel(
        self,
        panel: dict[str, pd.DataFrame],
        target_market: str,
    ) -> dict[str, pd.DataFrame]:
        """将面板数据统一为标准格式。

        对每个品种的 DataFrame:
        1. 保留核心字段 open/high/low/close/volume
        2. 填充目标市场缺失字段
        3. 确保数据类型一致
        """
        adapted: dict[str, pd.DataFrame] = {}
        for sym, df in panel.items():
            if df is None or df.empty:
                continue
            try:
                adapted[sym] = self._adapt_dataframe(df, target_market)
            except Exception as e:
                logger.warning(f"[{sym}] 数据适配失败: {e}")
                continue
        return adapted

    def _adapt_dataframe(
        self,
        df: pd.DataFrame,
        target_market: str,
    ) -> pd.DataFrame:
        """将单个 DataFrame 统一为标准格式。"""
        result = pd.DataFrame(index=df.index)

        # 1. 复制核心字段
        for field in CORE_FIELDS:
            if field in df.columns:
                result[field] = df[field].astype(float)
            else:
                # 极不可能出现，但保障健壮性
                result[field] = 0.0

        # 2. 填充期货特有字段（股票/ETF 不需要）
        if target_market == TARGET_MARKET_FUTURES:
            for field in FUTURES_SPECIFIC_FIELDS:
                if field in df.columns:
                    result[field] = df[field].astype(float)
                else:
                    result[field] = 0.0

        # 3. 确保数据类型为 float
        for col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce").fillna(0.0)

        return result

    # ── 因子执行适配 ──────────────────────────────────────

    def execute_factor_on_market(
        self,
        factor_data: dict[str, Any],
        panel: dict[str, pd.DataFrame],
        common_dates: pd.DatetimeIndex,
    ) -> dict[str, np.ndarray]:
        """在目标市场面板上执行因子，返回每个品种的信号序列。

        Args:
            factor_data: 因子定义字典
            panel: 统一格式的面板数据
            common_dates: 共有日期

        Returns:
            dict[symbol, np.ndarray] — 因子信号序列（与 common_dates 对齐）
        """
        from fts.factor_engine.factor_program import FactorExecutor

        sym_signals: dict[str, np.ndarray] = {}
        n_dates = len(common_dates)

        for sym, df in panel.items():
            if df is None or df.empty or len(df) < 20:
                continue
            try:
                executor = FactorExecutor(factor_data)
                sig = executor.execute(df, factor_data.get("params", {}))
                arr = np.array(sig, dtype=float)
                arr = np.where(np.isfinite(arr), arr, np.nan)

                # 对齐到 common_dates
                if len(arr) < n_dates:
                    arr = np.pad(arr, (0, n_dates - len(arr)), constant_values=np.nan)[:n_dates]

                sym_signals[sym] = arr[:n_dates]
            except Exception as e:
                logger.debug(f"[{sym}] 因子执行失败: {e}")
                continue

        return sym_signals
