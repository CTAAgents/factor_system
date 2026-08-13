"""
fts/data_sources/trading_calendar.py — 统一交易日历 + 断K/跳空脏数据清洗（G8，v2.103.0+15）。

功能:
    - TradingCalendar: 交易日查询（get_trading_days / is_trading_day / align）
    - mark_panel_data_gaps: 面板级断K标记（单品种缺失占比 >5% 或连续缺失 >3 日 → data_gap）
    - mark_gap_anomalies: 跳空异常标记（|隔夜跳空| > 5×ATR(20) 且无成交量 → gap_anomaly）

日历来源（按优先级）:
    1. TQ-Local trade_cal 接口（from_tq_local，尽力而为，失败返回 None）
    2. 面板多数日期推断（from_symbol_dates，频率 ≥ min_freq=0.8）——本地默认路径

约束:
    - 纯函数/类，不依赖行情源即可单测；接入失败静默降级，不阻断面板构建
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class TradingCalendar:
    """统一交易日历（G8）。

    Args:
        trading_days: 交易日序列（任意可排序时间序列，内部统一为升序 DatetimeIndex）
    """

    def __init__(self, trading_days: Any) -> None:
        idx = pd.DatetimeIndex(pd.to_datetime(list(trading_days)))
        self._days = idx.unique().sort_values()

    # ─── 工厂 ─────────────────────────────────────────────

    @classmethod
    def from_symbol_dates(
        cls,
        symbol_dates: dict[str, Any],
        min_freq: float = 0.8,
    ) -> "TradingCalendar":
        """从品种日期并集推断交易日（降级路径）。

        Args:
            symbol_dates: {symbol: 日期序列/Index}
            min_freq: 日期出现频率门槛（默认 0.8 = 至少 80% 品种共有）

        Returns:
            TradingCalendar（无输入 → 空日历）
        """
        from collections import Counter

        counter: Counter[str] = Counter()
        n_symbols = 0
        for dates in symbol_dates.values():
            if dates is None or len(dates) == 0:
                continue
            n_symbols += 1
            counter.update(pd.DatetimeIndex(pd.to_datetime(list(dates))).strftime("%Y-%m-%d"))
        if n_symbols == 0:
            return cls([])
        threshold = max(1, int(n_symbols * min_freq))
        days = [d for d, c in counter.items() if c >= threshold]
        return cls(days)

    @classmethod
    def from_tq_local(cls, timeout: float = 3.0) -> Optional["TradingCalendar"]:
        """尽力从 TQ-Local trade_cal 接口读取交易日（失败返回 None，不抛异常）。

        本地 17709 网关暴露 get_market_data 等 RPC；trade_cal 端点若不可用，
        调用方应回退 `from_symbol_dates`（本地默认路径）。
        """
        try:
            from fts.data_sources.tdx_local_source import TdxLocalSource

            src = TdxLocalSource(period="day")
            result = src._rpc("get_trade_dates", {"count": 600})  # noqa: SLF001 — 复用统一 RPC 通道
            if result is None:
                return None
            dates = result.get("dates") or result.get("list") or result.get("Value")
            if not dates:
                return None
            return cls(dates)
        except Exception as e:  # noqa: BLE001 — 尽力而为
            logger.debug("[TradingCalendar] TQ-Local trade_cal 不可用，回退多数日期推断: %s", e)
            return None

    # ─── 查询 ─────────────────────────────────────────────

    def get_trading_days(self, start: Any, end: Any) -> list[pd.Timestamp]:
        """返回 [start, end] 闭区间内的交易日列表（升序）。"""
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        return [d for d in self._days if s <= d <= e]

    def is_trading_day(self, day: Any) -> bool:
        """判断某日是否为交易日。"""
        return pd.Timestamp(day).normalize() in self._days.normalize()

    def align(self, series: pd.Series) -> pd.Series:
        """将序列对齐到交易日（G8）。

        - 剔除序列中非交易日的行（如周末误采/节假日）
        - 缺失交易日（停牌）前向填充（ffill）

        Args:
            series: 以 DatetimeIndex 索引的序列

        Returns:
            对齐后的序列（仅交易日，缺失 ffill；序列未在交易日索引上 → 原样返回）
        """
        if not isinstance(series.index, pd.DatetimeIndex) or len(self._days) == 0:
            return series
        s, e = series.index.min(), series.index.max()
        trading = pd.DatetimeIndex(self.get_trading_days(s, e))
        if len(trading) == 0:
            return series
        aligned = series.reindex(trading)
        return aligned.ffill()


def mark_panel_data_gaps(
    panel: dict[str, pd.DataFrame],
    calendar: Optional[TradingCalendar] = None,
    missing_ratio_th: float = 0.05,
    max_consecutive: int = 3,
) -> dict[str, dict[str, Any]]:
    """面板级断K标记（G8）。

    对每个品种统计其在日历[首末]区间内的缺失交易日：
    - 缺失占比 > missing_ratio_th（默认 5%）
    - 或连续缺失 > max_consecutive（默认 3 日）
    命中任一 → data_gap=True（下游不进因子计算）。

    Args:
        panel: {symbol: OHLCV DataFrame}
        calendar: 交易日历（None → 由 panel 多数日期推断）
        missing_ratio_th: 缺失占比阈值
        max_consecutive: 连续缺失天数阈值

    Returns:
        {symbol: {data_gap, missing_ratio, n_missing, n_trading_days, max_consecutive_missing}}
    """
    if not panel:
        return {}
    if calendar is None:
        calendar = TradingCalendar.from_symbol_dates({s: df.index for s, df in panel.items()})

    results: dict[str, dict[str, Any]] = {}
    for sym, df in panel.items():
        if df is None or df.empty:
            results[sym] = {
                "data_gap": True,
                "missing_ratio": 1.0,
                "n_missing": 0,
                "n_trading_days": 0,
                "max_consecutive_missing": 0,
                "reason": "empty",
            }
            continue
        idx = df.index
        trading_days = calendar.get_trading_days(idx.min(), idx.max())
        n_total = len(trading_days)
        present = set(pd.DatetimeIndex(pd.to_datetime(idx)).normalize())
        missing = [d for d in trading_days if d.normalize() not in present]
        n_missing = len(missing)
        ratio = n_missing / n_total if n_total else 0.0
        # 连续缺失最大段
        max_run = 0
        run = 0
        prev: Optional[pd.Timestamp] = None
        for d in missing:
            if prev is None or (d - prev).days <= 4:  # 相邻（跨周末容忍）
                run += 1
            else:
                run = 1
            max_run = max(max_run, run)
            prev = d
        data_gap = ratio > missing_ratio_th or max_run > max_consecutive
        results[sym] = {
            "data_gap": bool(data_gap),
            "missing_ratio": round(ratio, 4),
            "n_missing": n_missing,
            "n_trading_days": n_total,
            "max_consecutive_missing": max_run,
            "reason": (
                f"missing_ratio={ratio:.2%}> {missing_ratio_th:.0%}"
                if ratio > missing_ratio_th
                else f"consecutive_missing={max_run}> {max_consecutive}"
                if max_run > max_consecutive
                else "ok"
            ),
        }
    return results


def mark_gap_anomalies(
    df: pd.DataFrame,
    atr_n: int = 20,
    gap_mult: float = 5.0,
) -> pd.Series:
    """跳空异常标记（G8）：|隔夜跳空| > gap_mult×ATR(atr_n) 且无成交量。

    Args:
        df: OHLCV DataFrame（含 open/high/low/close，可选 volume）
        atr_n: ATR 窗口（默认 20）
        gap_mult: 跳空倍数阈值（默认 5×）

    Returns:
        bool Series（与 df 索引一致）；数据不足 → 全 False（异常不误标）。
    """
    n = len(df)
    default_false = pd.Series(False, index=df.index)
    if n < atr_n + 1:
        return default_false
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.rolling(atr_n).mean()
    gap = df["open"].astype(float) - prev_close
    volume = (
        df["volume"].astype(float)
        if "volume" in df.columns
        else pd.Series(0.0, index=df.index)
    )
    anomaly = (gap.abs() > gap_mult * atr) & (volume <= 0)
    return anomaly.fillna(False).astype(bool)


__all__ = [
    "TradingCalendar",
    "mark_panel_data_gaps",
    "mark_gap_anomalies",
]
