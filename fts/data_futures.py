"""
fts.data_futures — 期货数据提供者

基于 DuckDB（data/fts_history.duckdb）的 kline_cache 表提供期货连续合约 OHLCV 数据。
数据来源: AKShare futures_zh_daily_sina API → DuckDB 持久化。

数据流:
    因子引擎 → FTSDataProvider → FuturesDataProvider → DuckDB (kline_cache)
                                                     ↘ akshare 即时获取（降级）

期货特有字段:
    - hold: 持仓量（open interest），日线和分钟线均有
    - settle: 结算价（仅日线）

期货截面含义:
    横截面是"不同品种 × 同一日期"，可做跨品种因子（如跨商品动量、品种间强弱）。

HARNESS §契约优先: 数据接口通过本模块定义。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── DuckDB 路径 ───────────────────────────────────────────

_DUCKDB_PATH = Path(__file__).resolve().parent.parent / "data" / "fts_history.duckdb"


# ─── 异常 ──────────────────────────────────────────────────

class FuturesDataError(RuntimeError):
    """期货数据获取失败。"""


# ─── DuckDB 连接管理 ───────────────────────────────────────

_CONN: Optional[Any] = None


def _get_db() -> Any:
    """延迟获取 DuckDB 连接。"""
    global _CONN  # pylint: disable=global-statement
    if _CONN is None:
        try:
            import duckdb  # type: ignore[import-untyped]
            _CONN = duckdb.connect(str(_DUCKDB_PATH))
        except Exception as e:
            raise FuturesDataError(f"DuckDB 连接失败: {e}") from e
    return _CONN


# ─── 期货数据提供者 ───────────────────────────────────────

class FuturesDataProvider:
    """期货数据提供者 — 基于 DuckDB kline_cache 表。

    数据源优先级:
        1. DuckDB kline_cache（连续合约，已持久化）
        2. AKShare 即时获取（futures_zh_daily_sina API）
        3. 合成数据降级（保证系统可运行）

    用法:
        provider = FuturesDataProvider()
        df = provider.get_ohlcv("RB0", days=500)
        panel, dates = provider.get_futures_panel(["RB0", "CU0", "AU0"], days=500)
    """

    def __init__(self, use_akshare_fallback: bool = True):
        """
        Args:
            use_akshare_fallback: 是否在 DuckDB 无数据时尝试 AKShare 即时获取。
        """
        self._use_akshare = use_akshare_fallback

    # ── 单标的 OHLCV ──

    def get_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取期货连续合约 OHLCV 日 K 线数据。

        Args:
            symbol: 期货连续合约代码（如 "RB0" / "CU0" / "IF0"）。
            days: 回溯天数。
            trace_id: HARNESS trace_id。

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume, hold, settle
            Index: DatetimeIndex

        Raises:
            FuturesDataError: 所有数据源不可用
        """
        # 1. DuckDB kline_cache
        try:
            df = self._from_kline_cache(symbol, days)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug(f"DuckDB kline_cache 获取失败 [{symbol}]: {e}")

        # 2. AKShare 即时获取
        if self._use_akshare:
            try:
                df = self._from_akshare(symbol, days)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                logger.debug(f"AKShare 获取失败 [{symbol}]: {e}")

        # 3. 合成数据降级
        logger.warning(f"使用合成数据回退 [期货 {symbol}]")
        return self.synthesize_ohlcv(n_days=days, base_price=3000.0, seed=42)

    # ── 批量面板数据 ──

    def get_futures_panel(
        self,
        symbols: list[str],
        days: int = 500,
        trace_id: str = "",
    ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取多个期货品种的 OHLCV 面板数据。

        Returns:
            (panel, common_dates)
            panel: dict[symbol, OHLCV DataFrame]（含 hold/settle 列）
            common_dates: 所有品种共有日期
        """
        panel: dict[str, pd.DataFrame] = {}
        dates_set: set[pd.Timestamp] = set()
        first = True

        for sym in symbols:
            try:
                df = self.get_ohlcv(sym, days=days, trace_id=trace_id)
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
            logger.warning("所有期货品种数据获取失败，使用合成数据")
            df = self.synthesize_ohlcv(n_days=days, base_price=3000.0, seed=42)
            panel["SYNTHETIC"] = df
            return panel, df.index

        common_dates = pd.DatetimeIndex(sorted(dates_set))
        return panel, common_dates

    # ── DuckDB 读取 ──

    def _from_kline_cache(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """从 DuckDB kline_cache 表读取连续合约数据。

        kline_cache 表结构:
            symbol: 品种代码（如 "RB"）
            period: 周期（如 "daily"）
            date: 日期字符串
            open/high/low/close: 价格
            volume: 成交量
            amount: 成交额

        Args:
            symbol: 期货代码（支持 "RB0" / "RB" 两种格式）
            days: 回溯天数

        Returns:
            OHLCV DataFrame（含 hold/settle 列，DuckDB 无持仓量时设为 NaN）
        """
        db = _get_db()

        # 标准化: 去掉末尾的 "0" 连续合约标记
        raw = symbol.strip().upper()
        sym = raw[:-1] if raw.endswith("0") else raw

        # 查询 kline_cache
        result = db.execute(
            "SELECT date, open, high, low, close, volume, amount "
            "FROM kline_cache WHERE symbol = ? AND period = 'daily' "
            "ORDER BY date DESC LIMIT ?",
            [sym, days],
        )
        rows = result.fetchall()
        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)

        # 添加期货特有字段（DuckDB 无持仓量/结算价，设为 NaN）
        df["hold"] = np.nan
        df["settle"] = np.nan

        # 标准列顺序
        return df[["open", "high", "low", "close", "volume", "hold", "settle"]]

    # ── AKShare 即时获取 ──

    def _from_akshare(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """从 AKShare futures_zh_daily_sina 即时获取数据。

        AKShare 返回字段:
            date, open, high, low, close, volume, hold, settle

        Args:
            symbol: 期货连续合约代码（如 "RB0"）
            days: 回溯天数

        Returns:
            OHLCV DataFrame（含 hold/settle 列）
        """
        try:
            import akshare as ak  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("akshare 未安装，无法即时获取期货数据")
            return None

        # 确保 symbol 是 "RB0" 格式（连续合约）
        sym = symbol.strip().upper()
        if not sym.endswith("0"):
            sym = f"{sym}0"

        try:
            df = ak.futures_zh_daily_sina(symbol=sym)
        except Exception as e:
            raise FuturesDataError(f"AKShare 获取失败 [{sym}]: {e}") from e

        if df is None or df.empty:
            return None

        # 重命名列: AKShare 返回的列名是中文或英文
        # 实际返回: date, open, high, low, close, volume, hold, settle
        col_map = {
            "hold": "hold",
            "settle": "settle",
        }
        # 确保必要列存在
        required = ["date", "open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                return None

        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)

        # 只保留需要的列
        cols = ["open", "high", "low", "close", "volume"]
        extra_cols = ["hold", "settle"]
        for c in extra_cols:
            if c in df.columns:
                cols.append(c)
            else:
                df[c] = np.nan
                cols.append(c)

        # 限制天数
        if len(df) > days:
            df = df.iloc[-days:]

        return df[cols]

    # ── 合成数据降级 ──

    @staticmethod
    def synthesize_ohlcv(
        n_days: int = 500,
        base_price: float = 3000.0,
        seed: int = 42,
    ) -> pd.DataFrame:
        """合成期货 OHLCV 数据（网络不可用时的降级回退）。

        Args:
            n_days: 天数
            base_price: 起始价格
            seed: 随机种子

        Returns:
            OHLCV DataFrame（含 hold/settle 列）
        """
        np.random.seed(seed)
        dates = pd.date_range(
            datetime.now() - timedelta(days=n_days),
            periods=n_days, freq="D",
        )
        close = base_price + np.cumsum(np.random.randn(n_days) * 30)
        close = np.maximum(close, base_price * 0.5)  # 防止负价格
        hold = np.random.randint(100000, 1000000, n_days).astype(float)
        return pd.DataFrame({
            "open": close + np.random.randn(n_days) * 10,
            "high": close + np.abs(np.random.randn(n_days)) * 30,
            "low": close - np.abs(np.random.randn(n_days)) * 30,
            "close": close,
            "volume": np.random.randint(10000, 500000, n_days).astype(float),
            "hold": hold,
            "settle": close + np.random.randn(n_days) * 5,
        }, index=dates)


# ─── 期货品种子集（82 个连续合约）───────────────────────────

# 来自 AKShare futures_display_main_sina() 的完整列表
FUTURES_SUBSET: list[str] = [
    # 大商所 (dce) — 22 个
    "V0", "P0", "B0", "M0", "I0", "JD0", "L0", "PP0", "FB0",
    "Y0", "C0", "A0", "J0", "JM0", "CS0", "EG0", "RR0", "EB0",
    "PG0", "LH0", "LG0", "BZ0",
    # 郑商所 (czce) — 25 个
    "TA0", "OI0", "RS0", "RM0", "WH0", "JR0", "SR0", "CF0",
    "RI0", "MA0", "FG0", "LR0", "SF0", "SM0", "CY0", "AP0",
    "CJ0", "UR0", "SA0", "PF0", "PK0", "SH0", "PX0", "PR0", "PL0",
    # 上期所 (shfe) — 19 个
    "FU0", "AL0", "RU0", "ZN0", "CU0", "AU0", "RB0", "PB0",
    "AG0", "BU0", "HC0", "SN0", "NI0", "SP0", "SS0", "AO0",
    "BR0", "AD0", "OP0",
    # 能源中心 (ine) — 5 个
    "SC0", "NR0", "LU0", "BC0", "EC0",
    # 中金所 (cffex) — 6 个
    "IF0", "TF0", "IH0", "IC0", "TS0", "IM0",
    # 广期所 (gfex) — 5 个
    "SI0", "LC0", "PS0", "PT0", "PD0",
]

# 常用期货品种子集（流动性好的品种，用于快速测试）
FUTURES_CORE_SUBSET: list[str] = [
    "RB0",  # 螺纹钢
    "CU0",  # 铜
    "AU0",  # 黄金
    "AG0",  # 白银
    "I0",   # 铁矿石
    "M0",   # 豆粕
    "TA0",  # PTA
    "MA0",  # 甲醇
    "SC0",  # 原油
    "HC0",  # 热卷
    "NI0",  # 镍
    "SN0",  # 锡
    "P0",   # 棕榈油
    "Y0",   # 豆油
    "C0",   # 玉米
    "A0",   # 豆一
    "CF0",  # 棉花
    "SR0",  # 白糖
    "SA0",  # 纯碱
    "IF0",  # 沪深300股指
    "IC0",  # 中证500股指
    "IH0",  # 上证50股指
    "IM0",  # 中证1000股指
    "LC0",  # 碳酸锂
    "SI0",  # 工业硅
]


# ─── 缺省实例 ─────────────────────────────────────────────

_default_futures_provider: Optional[FuturesDataProvider] = None


def get_futures_provider() -> FuturesDataProvider:
    """获取全局 FuturesDataProvider 实例（惰性初始化）。"""
    global _default_futures_provider  # noqa: PLW0603
    if _default_futures_provider is None:
        _default_futures_provider = FuturesDataProvider()
    return _default_futures_provider


__all__ = [
    "FuturesDataProvider",
    "FuturesDataError",
    "get_futures_provider",
    "FUTURES_SUBSET",
    "FUTURES_CORE_SUBSET",
]