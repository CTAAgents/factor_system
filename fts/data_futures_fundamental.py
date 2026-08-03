"""
fts.data_futures_fundamental — 期货基本面数据提供者

基于 AKShare 提供期货基本面数据（库存、仓单、现货基差等），
支持降级回退（网络不可用时返回空 DataFrame）。

数据源:
    - 库存: futures_inventory_em（东方财富期货库存）
    - 仓单: futures_warehouse_receipt_czce / futures_shfe_warehouse_receipt
    - 现货基差: futures_spot_price_daily（100ppi 基差数据）
    - 期现价差: futures_to_spot_shfe / futures_to_spot_czce / futures_to_spot_dce

用法:
    provider = FuturesFundamentalProvider()
    inv = provider.get_inventory("RB")
    basis = provider.get_basis("RB", days=30)
    wr = provider.get_warehouse_receipt("RB")

HARNESS §契约优先: 数据接口通过本模块定义。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ─── 品种映射 ─────────────────────────────────────────────

# FTS 连续合约代码 → AKShare 库存API中文名
INVENTORY_SYMBOL_MAP: dict[str, str] = {
    "RB0": "螺纹钢", "HC0": "热卷", "I0": "铁矿石",
    "CU0": "沪铜", "AL0": "沪铝", "ZN0": "沪锌",
    "NI0": "镍", "SN0": "锡", "PB0": "沪铅",
    "AU0": "沪金", "AG0": "沪银",
    "SC0": "原油", "FU0": "燃油",
    "M0": "豆粕", "Y0": "豆油", "P0": "棕榈",
    "C0": "玉米", "A0": "豆一",
    "TA0": "PTA", "MA0": "甲醇", "SA0": "纯碱",
    "CF0": "郑棉", "SR0": "白糖", "RU0": "橡胶",
    "J0": "焦炭", "JM0": "焦煤",
    "FG0": "玻璃", "UR0": "尿素",
    "SI0": "工业硅", "LC0": "碳酸锂",
    "SP0": "纸浆", "EB0": "苯乙烯",
    "PG0": "液化石油气", "LH0": "生猪",
}

# FTS 连续合约代码 → AKShare 基差API品种代码
BASIS_SYMBOL_MAP: dict[str, str] = {
    "RB0": "RB", "HC0": "HC", "I0": "I",
    "CU0": "CU", "AL0": "AL", "ZN0": "ZN",
    "NI0": "NI", "SN0": "SN", "PB0": "PB",
    "AU0": "AU", "AG0": "AG",
    "SC0": "SC", "FU0": "FU",
    "M0": "M", "Y0": "Y", "P0": "P",
    "C0": "C", "A0": "A",
    "TA0": "TA", "MA0": "MA", "SA0": "SA",
    "CF0": "CF", "SR0": "SR", "RU0": "RU",
    "J0": "J", "JM0": "JM",
    "FG0": "FG", "UR0": "UR",
    "SP0": "SP", "EB0": "EB",
    "PG0": "PG",
}


# ─── 异常 ──────────────────────────────────────────────────

class FuturesFundamentalError(RuntimeError):
    """期货基本面数据获取失败。"""


# ─── 基本面数据提供者 ─────────────────────────────────────

class FuturesFundamentalProvider:
    """期货基本面数据提供者 — 基于 AKShare。

    数据源优先级:
        1. AKShare 即时获取
        2. 返回空 DataFrame（降级，不抛出异常）
    """

    # ── 库存数据 ──

    def get_inventory(self, symbol: str) -> pd.DataFrame:
        """获取期货品种库存数据。

        Args:
            symbol: FTS 连续合约代码（如 "RB0" / "CU0"）。

        Returns:
            pd.DataFrame with columns: date, inventory, change
            降级时返回空 DataFrame。
        """
        ch_name = INVENTORY_SYMBOL_MAP.get(symbol.upper())
        if ch_name is None:
            logger.debug("无库存数据映射: %s", symbol)
            return pd.DataFrame()

        try:
            import akshare as ak  # type: ignore[import-untyped]
            df = ak.futures_inventory_em(symbol=ch_name)
            if df is None or df.empty:
                return pd.DataFrame()
            df.columns = ["date", "inventory", "change"]
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)
            return df
        except Exception as e:
            logger.debug("库存获取失败 [%s]: %s", symbol, e)
            return pd.DataFrame()

    # ── 现货基差数据 ──

    def get_basis(
        self,
        symbol: str,
        days: int = 30,
    ) -> pd.DataFrame:
        """获取期货品种现货基差数据。

        Args:
            symbol: FTS 连续合约代码（如 "RB0"）。
            days: 回溯天数。

        Returns:
            pd.DataFrame with columns:
                spot_price, near_contract_price, dominant_contract_price,
                near_basis, dom_basis, near_basis_rate, dom_basis_rate
            降级时返回空 DataFrame。
        """
        code = BASIS_SYMBOL_MAP.get(symbol.upper())
        if code is None:
            logger.debug("无基差数据映射: %s", symbol)
            return pd.DataFrame()

        end = datetime.now()
        start = end - timedelta(days=days * 2)  # 多取一些，过滤非交易日

        try:
            import akshare as ak  # type: ignore[import-untyped]
            df = ak.futures_spot_price_daily(
                start_day=start.strftime("%Y%m%d"),
                end_day=end.strftime("%Y%m%d"),
                vars_list=[code],
            )
            if df is None or df.empty:
                return pd.DataFrame()
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)
            # 限制天数
            if len(df) > days:
                df = df.iloc[-days:]
            return df
        except Exception as e:
            logger.debug("基差获取失败 [%s]: %s", symbol, e)
            return pd.DataFrame()

    # ── 仓单数据 ──

    def get_warehouse_receipt(self, symbol: str) -> pd.DataFrame:
        """获取期货品种仓单数据。

        目前支持: 上期所、郑商所、大商所、广期所品种。
        返回最近一个交易日的仓单数据。

        Args:
            symbol: FTS 连续合约代码（如 "RB0"）。

        Returns:
            pd.DataFrame 仓单数据，降级时返回空 DataFrame。
        """
        # 简单的交易所映射
        _exchange_map: dict[str, str] = {
            # 上期所
            "CU0": "shfe", "AL0": "shfe", "ZN0": "shfe", "PB0": "shfe",
            "NI0": "shfe", "SN0": "shfe", "AU0": "shfe", "AG0": "shfe",
            "RB0": "shfe", "HC0": "shfe", "RU0": "shfe", "BU0": "shfe",
            "FU0": "shfe", "SP0": "shfe", "SS0": "shfe", "AO0": "shfe",
            "BR0": "shfe",
            # 郑商所
            "TA0": "czce", "MA0": "czce", "SA0": "czce", "CF0": "czce",
            "SR0": "czce", "FG0": "czce", "UR0": "czce", "RM0": "czce",
            "OI0": "czce", "PF0": "czce", "PK0": "czce", "AP0": "czce",
            "CJ0": "czce", "CY0": "czce", "SF0": "czce", "SM0": "czce",
            "SH0": "czce", "PX0": "czce", "PR0": "czce", "PL0": "czce",
            # 大商所
            "M0": "dce", "Y0": "dce", "P0": "dce", "C0": "dce",
            "A0": "dce", "B0": "dce", "J0": "dce", "JM0": "dce",
            "I0": "dce", "L0": "dce", "PP0": "dce", "V0": "dce",
            "EG0": "dce", "EB0": "dce", "PG0": "dce", "JD0": "dce",
            "RR0": "dce", "CS0": "dce", "LH0": "dce", "LG0": "dce",
            "BZ0": "dce",
            # 广期所
            "SI0": "gfex", "LC0": "gfex", "PS0": "gfex",
        }
        exchange = _exchange_map.get(symbol.upper())
        if exchange is None:
            return pd.DataFrame()

        try:
            import akshare as ak  # type: ignore[import-untyped]
            today_str = datetime.now().strftime("%Y%m%d")
            if exchange == "shfe":
                data = ak.futures_shfe_warehouse_receipt(date=today_str)
            elif exchange == "czce":
                data = ak.futures_warehouse_receipt_czce(date=today_str)
            elif exchange == "dce":
                data = ak.futures_warehouse_receipt_dce(date=today_str)
            elif exchange == "gfex":
                data = ak.futures_gfex_warehouse_receipt(date=today_str)
            else:
                return pd.DataFrame()

            if isinstance(data, dict):
                # 尝试从 dict 中提取 DataFrame
                for v in data.values():
                    if isinstance(v, pd.DataFrame):
                        return v
            return pd.DataFrame()
        except Exception as e:
            logger.debug("仓单获取失败 [%s]: %s", symbol, e)
            return pd.DataFrame()

    # ── 批量获取（用于因子面板） ──

    def get_inventory_panel(
        self, symbols: list[str], days: int = 30
    ) -> dict[str, pd.DataFrame]:
        """批量获取多个品种的库存数据。

        Returns:
            dict[symbol, inventory_df]
        """
        panel: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                df = self.get_inventory(sym)
                if not df.empty:
                    panel[sym] = df
            except Exception:  # noqa: BLE001
                continue
        return panel

    def get_basis_panel(
        self, symbols: list[str], days: int = 30
    ) -> dict[str, pd.DataFrame]:
        """批量获取多个品种的基差数据。

        Returns:
            dict[symbol, basis_df]
        """
        panel: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            try:
                df = self.get_basis(sym, days=days)
                if not df.empty:
                    panel[sym] = df
            except Exception:  # noqa: BLE001
                continue
        return panel


# ─── 缺省实例 ─────────────────────────────────────────────

_default_fundamental_provider: Optional[FuturesFundamentalProvider] = None


def get_fundamental_provider() -> FuturesFundamentalProvider:
    """获取全局 FuturesFundamentalProvider 实例（惰性初始化）。"""
    global _default_fundamental_provider  # noqa: PLW0603
    if _default_fundamental_provider is None:
        _default_fundamental_provider = FuturesFundamentalProvider()
    return _default_fundamental_provider


__all__ = [
    "FuturesFundamentalProvider",
    "FuturesFundamentalError",
    "get_fundamental_provider",
    "INVENTORY_SYMBOL_MAP",
    "BASIS_SYMBOL_MAP",
]