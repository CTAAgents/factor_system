"""
fts.data_sources.roll_calendar — 期货换月日历与复权 (v2.58.0, GAP-046)。

解决主力连续合约（`{symbol}0`）直接拼接导致的换月跳空问题：

1. 换月日历：从 `contract_kline` 具体合约日线按每日最大成交量判定主力，
   检测主力切换事件（date / old_contract / new_contract / adj_ratio）。
2. 复权因子：比率法后复权 —— adj_ratio = 切换日新合约收盘 / 旧合约收盘，
   切换日之前的数据乘以累积因子，消除换月跳空对因子值的污染。
3. 复权序列：get_ohlcv(adjusted=True) 时应用，因子计算使用复权序列；
   展期成本在回测层单独扣除（与因子计算分离）。

降级策略（见 docs/harness/04-resilience.md）:
- contract_kline 表缺失 / 无数据 → 返回空换月日历，复权因子为 None，
  调用方回退原始拼接序列（不报错、不阻断）。
- 切换日任一价格缺失 → 跳过该换月事件（不复权），不传播 NaN。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/fts_history.duckdb")


@dataclass
class RollEvent:
    """单次主力换月事件。

    Attributes:
        date: 切换日期（新主力首次成为最大的日期）
        old_contract: 切换前主力合约代码
        new_contract: 切换后主力合约代码
        old_close: 切换日旧主力合约收盘价
        new_close: 切换日新主力合约收盘价
        adj_ratio: 复权比率 = new_close / old_close
    """

    date: date
    old_contract: str
    new_contract: str
    old_close: float
    new_close: float
    adj_ratio: float


class RollCalendar:
    """期货换月日历与后复权计算。

    Usage:
        rc = RollCalendar()
        rolls = rc.build_roll_calendar("RB0")          # 换月事件序列
        df_adj = rc.apply_adjustment(df, "RB0")        # 复权后的 OHLCV 面板
    """

    def __init__(self, db_path: str | Path = str(DEFAULT_DB_PATH)) -> None:
        self.db_path = str(db_path)

    # ─── 换月日历构建 ────────────────────────────────────

    def build_roll_calendar(
        self,
        symbol: str,
        backfill_days: int = 20,
    ) -> list[RollEvent]:
        """构建指定品种的换月事件序列。

        主力判定：每日成交量最大的合约。当日主力与前一交易日主力不同时，
        记为一次换月事件；切换日价格取两合约当日收盘——切换日任一价格缺失
        （真实数据缺口）时，向前回溯最近共同交易日（窗口 `backfill_days`，
        默认 20 个交易日）取两合约收盘价计算 adj_ratio，避免单日缺口导致
        换月事件被跳过、复权跳空未消除；回溯窗口内仍无共同价格才跳过。

        Args:
            symbol: 连续合约代码（如 "RB0" / "CU0"），内部剥离末尾 "0"。
            backfill_days: 切换日缺价时向前回溯的交易日窗口上限。
                （默认 20：全库缺价救回率 5日→63% / 20日→78%）

        Returns:
            按日期升序排列的 RollEvent 列表；数据缺失时返回空列表。
        """
        base = symbol[:-1] if symbol.endswith("0") else symbol
        df = self._load_contract_kline(base)
        if df is None or df.empty:
            logger.warning("[RollCalendar] contract_kline 无数据 [%s]，返回空换月日历", base)
            return []

        # 每日主力 = 成交量最大的合约（同日多行取 volume 最大）。
        # GAP-046 修复（v2.104.0+39）: volume 无效行（缺失/0）不参与主力判定——
        # 无成交合约不应成为主力，否则产生大量"来回切换"的假换月事件（早期数据
        # volume 大面积缺失时尤为严重）。
        tradable = df[df["volume"].fillna(0.0) > 0.0]
        if tradable.empty:
            return []
        dominant = (
            tradable.sort_values("volume", ascending=False)
            .drop_duplicates(subset=["date"], keep="first")
            .sort_values("date")
        )
        if len(dominant) < 2:
            return []

        # 当日主力与前一交易日主力变化 → 换月事件
        contracts = dominant["contract"].tolist()
        dates = dominant["date"].tolist()

        events: list[RollEvent] = []
        for i in range(1, len(dates)):
            if contracts[i] == contracts[i - 1]:
                continue
            old_contract = contracts[i - 1]
            new_contract = contracts[i]
            # DuckDB fetchdf 返回 datetime64 → 规范化回 datetime.date（与 dataclass 注解一致）
            roll_date = pd.Timestamp(dates[i]).date()

            # 切换日两合约收盘价（缺失则回溯最近共同交易日）
            old_close, new_close, used_date = self._closes_with_backfill(
                base,
                old_contract,
                new_contract,
                roll_date,
                df,
                backfill_days,
            )
            if old_close is None or new_close is None or old_close <= 0:
                logger.warning(
                    "[RollCalendar] 切换日价格缺失且回溯 %d 日内无共同价格，跳过换月事件 [%s] %s→%s @ %s",
                    backfill_days,
                    base,
                    old_contract,
                    new_contract,
                    roll_date,
                )
                continue
            if used_date != roll_date:
                logger.info(
                    "[RollCalendar] 切换日缺价，回溯 %s 取价 [%s] %s→%s @ %s",
                    used_date,
                    base,
                    old_contract,
                    new_contract,
                    roll_date,
                )

            events.append(
                RollEvent(
                    date=roll_date,
                    old_contract=old_contract,
                    new_contract=new_contract,
                    old_close=old_close,
                    new_close=new_close,
                    adj_ratio=new_close / old_close,
                )
            )

        logger.info("[RollCalendar] [%s] 构建换月日历: %d 次换月", base, len(events))
        return events

    # ─── 复权因子 ────────────────────────────────────────

    def compute_adjust_factors(
        self,
        dates: pd.DatetimeIndex,
        rolls: list[RollEvent],
    ) -> pd.Series:
        """计算后复权累积因子（保持最新价格不变，历史价格按比例调整）。

        Args:
            dates: 面板日期索引（升序）
            rolls: 换月事件序列（按日期升序）

        Returns:
            与 dates 对齐的 adj_factor Series（NaN 处理：数据缺失的日期置 1.0）
        """
        factor = pd.Series(1.0, index=dates)
        for roll in rolls:
            roll_ts = pd.Timestamp(roll.date)
            mask = dates < roll_ts
            factor[mask] = factor[mask] * roll.adj_ratio
        return factor

    def apply_adjustment(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> tuple[pd.DataFrame, list[RollEvent]]:
        """对连续合约面板应用后复权，返回 (复权后 df, 换月事件)。

        - 无换月日历（contract_kline 缺失）时返回原始 df（adj_factor 全 1.0）
        - 复权列：open/high/low/close/settle
        - 新增 adj_factor 列（供落库 kline_cache.adj_factor）

        Args:
            df: 连续合约 OHLCV 面板（DatetimeIndex + open/high/low/close/...）
            symbol: 连续合约代码（如 "RB0"）

        Returns:
            (复权后 DataFrame, 换月事件列表)
        """
        rolls = self.build_roll_calendar(symbol)
        if not rolls or df is None or df.empty:
            if df is not None and not df.empty:
                df = df.copy()
                df["adj_factor"] = 1.0
            return df, rolls

        result = df.copy()
        dates = pd.DatetimeIndex(result.index)
        factor = self.compute_adjust_factors(dates, rolls)

        for col in ("open", "high", "low", "close", "settle"):
            if col in result.columns:
                result[col] = result[col] * factor

        result["adj_factor"] = factor
        return result, rolls

    # ─── 内部工具 ────────────────────────────────────────

    def _load_contract_kline(self, base: str) -> Optional[pd.DataFrame]:
        """从 DuckDB contract_kline 加载指定品种全部具体合约日线。"""
        try:
            import duckdb

            # 与主流程（FuturesDataProvider/DuckDBReader）保持一致的打开配置：
            # 同一进程内同一 DuckDB 文件只允许一种连接配置，read_only=True 会与
            # 主连接的默认读写配置冲突（"different configuration than existing connections"）
            con = duckdb.connect(self.db_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("[RollCalendar] DuckDB 连接失败 [%s]: %s", base, e)
            return None
        try:
            # 表不存在 → 返回 None（触发降级）
            exists = con.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='main' AND table_name='contract_kline'"
            ).fetchone()
            if not exists or exists[0] == 0:
                return None
            df = con.execute(
                """
                SELECT symbol, contract, date, open, high, low, close,
                       volume, amount, hold, settle
                FROM contract_kline
                WHERE symbol = ? AND period = 'daily'
                ORDER BY date, volume DESC
                """,
                [base],
            ).fetchdf()
            # GAP-046 修复（v2.104.0+39）: 生产库 contract_kline.date 为 VARCHAR 时
            # fetchdf 返回 object(str)，_close_on 用 pd.Timestamp 比较将永远失配，
            # 导致所有换月事件被误判为"切换日价格缺失"而跳过。统一规范化为
            # datetime64 后与 Timestamp 比较稳定命中（DATE 列时 to_datetime 为幂等）。
            df["date"] = pd.to_datetime(df["date"])
            return df
        except Exception as e:  # noqa: BLE001
            logger.warning("[RollCalendar] contract_kline 读取失败 [%s]: %s", base, e)
            return None
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _closes_with_backfill(
        base: str,
        old_contract: str,
        new_contract: str,
        roll_date,
        df: pd.DataFrame,
        backfill_days: int = 20,
    ) -> tuple[Optional[float], Optional[float], date]:
        """取切换日两合约收盘价；切换日缺价时向前回溯最近共同交易日。

        真实数据源（QuantData/历史库）可能存在单日缺口：切换日某合约当日
        无记录，而相邻交易日数据完整。此时直接跳过换月事件会遗留复权跳空，
        故向前回溯最多 `backfill_days` 个交易日，取两合约共同有收盘价的
        最近一天收盘价计算 adj_ratio。

        Args:
            base: 品种代码（仅用于日志语义，查询按 contract 过滤）。
            old_contract: 切换前主力合约。
            new_contract: 切换后主力合约。
            roll_date: 切换日（date 或 datetime）。
            df: contract_kline 全量数据（已 to_datetime 规范化 date）。
            backfill_days: 回溯交易日窗口上限（含切换日）。

        Returns:
            (old_close, new_close, used_date)；回溯窗口内无共同价格时
            old_close/new_close 为 None。
        """
        roll_ts = pd.Timestamp(roll_date)

        def _close_on_day(contract: str, day) -> Optional[float]:
            sub = df[(df["contract"] == contract) & (df["date"] == pd.Timestamp(day))]
            if sub.empty:
                return None
            return float(sub["close"].iloc[0])

        # 1) 切换日本身两合约价格齐全 → 直接使用
        old_close = _close_on_day(old_contract, roll_ts)
        new_close = _close_on_day(new_contract, roll_ts)
        if old_close is not None and new_close is not None and old_close > 0:
            return old_close, new_close, roll_ts.date()

        # 2) 切换日缺价 → 向前回溯最近共同交易日（窗口内）
        prior = df[df["date"] < roll_ts].sort_values("date")
        if prior.empty:
            return None, None, roll_ts.date()
        prior_dates = prior["date"].drop_duplicates().tolist()
        # 窗口 = 切换日前最近 backfill_days 个交易日（升序）
        window = prior_dates[-backfill_days:] if backfill_days > 0 else prior_dates
        for day in reversed(window):
            oc = _close_on_day(old_contract, day)
            nc = _close_on_day(new_contract, day)
            if oc is not None and nc is not None and oc > 0:
                return oc, nc, pd.Timestamp(day).date()

        return None, None, roll_ts.date()


__all__ = ["RollCalendar", "RollEvent", "DEFAULT_DB_PATH"]
