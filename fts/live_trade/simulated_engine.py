"""
fts.live_trade.simulated_engine — 模拟仓回放引擎 + 实时纸面（D.1，v2.102.0）。

- ``SimulatedReplayEngine``: 历史回放（严格时间单向，杜绝未来函数）
- ``SimulatedPaperTrader``: 实时纸面交易（每日信号应用 + 盯市，状态持久化）

回放纪律: t 日信号 → t+1 开盘价成交 → t+1 收盘盯市 → 用 t→t+1 收益做因子归因。

FTS 角色边界: 只做模拟核算，真实撮合由下游（FDT）负责。

版本: v1.0.0（D.1）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from fts.live_trade.contracts import ReplayResult, SimApplyResult, SimDailyRecord, SimFill
from fts.live_trade.simulated_portfolio import SimulatedPortfolio
from fts.live_trade.sqlite_store import SimSQLiteStore

logger = logging.getLogger(__name__)


def _signal_date(signal: dict[str, Any]) -> str:
    """提取信号日期（YYYY-MM-DD）。"""
    ts = signal.get("timestamp")
    if ts:
        return str(ts)[:10]
    return str(signal.get("signal_date", ""))[:10]


class SimulatedReplayEngine:
    """历史回放引擎。"""

    def __init__(
        self,
        portfolio: SimulatedPortfolio,
        db_path: Optional[str] = None,
    ) -> None:
        """初始化。

        Args:
            portfolio: 模拟仓实例（回放复用）
            db_path: 反馈落盘 DuckDB 路径（None 仅返回记录，不落盘）
        """
        self._portfolio = portfolio
        self._db_path = db_path

    def replay(
        self,
        signals: list[dict[str, Any]],
        panel: dict[str, pd.DataFrame],
    ) -> ReplayResult:
        """按时间顺序回放。

        Args:
            signals: FactorSignal 字典列表（按时间排序）
            panel: {symbol: DataFrame(index=date, columns=[open, close])}

        Returns:
            ReplayResult{equity_curve, feedback_records, fills, summary}。
        """
        # 归一化 panel 索引为日期
        for sym, df in panel.items():
            if not isinstance(df.index, pd.DatetimeIndex):
                panel[sym] = df.copy()
                panel[sym].index = pd.to_datetime(df.index)

        date_set: set[pd.Timestamp] = set()
        for df in panel.values():
            date_set.update(df.index)
        dates = sorted(date_set)
        if not dates:
            return ReplayResult(equity_curve=[], feedback_records=[], fills=[], summary={})

        signals_by_date: dict[str, dict[str, Any]] = {}
        for sig in signals:
            d = _signal_date(sig)
            if d:
                signals_by_date[d] = sig

        feedback: list[dict[str, Any]] = []
        all_fills: list[SimFill] = []

        for i, date in enumerate(dates):
            date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
            prev_str = ""
            if i > 0:
                prev_str = pd.Timestamp(dates[i - 1]).strftime("%Y-%m-%d")

            prev_signal = signals_by_date.get(prev_str) if prev_str else None

            # 1. t+1 开盘价成交（t 日信号）
            if prev_signal:
                open_prices = {}
                for sym in {dta.get("symbol") for dta in prev_signal.get("signals", [])}:
                    if sym in panel and date in panel[sym].index:
                        open_prices[sym] = float(panel[sym].loc[date, "open"])
                result = self._portfolio.apply_signal(prev_signal, open_prices, prev_str)
                all_fills.extend(result.get("fills", []))

            # 2. t+1 收盘盯市
            close_prices = {}
            for sym, df in panel.items():
                if date in df.index:
                    close_prices[sym] = float(df.loc[date, "close"])
            if close_prices:
                self._portfolio.mark_to_market(date_str, close_prices)

            # 3. 因子归因（t 信号 → t 到 t+1 收益）
            if prev_signal and prev_str:
                next_return: dict[str, float] = {}
                for sym in {dta.get("symbol") for dta in prev_signal.get("signals", [])}:
                    if sym in panel and date in panel[sym].index:
                        prev_close = float(panel[sym].loc[dates[i - 1], "close"])
                        this_close = float(panel[sym].loc[date, "close"])
                        if prev_close > 0:
                            next_return[sym] = this_close / prev_close - 1.0
                records = self._portfolio.attribute_factor_returns(prev_signal, next_return)
                if records:
                    feedback.extend(records)
                    if self._db_path:
                        self._portfolio.import_feedback(records, self._db_path)

        equity_curve = self._portfolio.equity_curve()
        summary = self._summarize(equity_curve, all_fills, feedback, dates)
        return ReplayResult(
            equity_curve=equity_curve,
            feedback_records=feedback,
            fills=all_fills,
            summary=summary,
        )

    @staticmethod
    def _summarize(
        curve: list[SimDailyRecord],
        fills: list[SimFill],
        feedback: list[dict[str, Any]],
        dates: list[pd.Timestamp],
    ) -> dict[str, Any]:
        """汇总回放摘要。"""
        if not curve:
            return {"n_days": 0, "n_fills": 0, "n_feedback": 0}
        initial = curve[0]["equity"]
        final = curve[-1]["equity"]
        total_return = final / initial - 1.0 if initial else 0.0
        return {
            "start_date": curve[0]["date"],
            "end_date": curve[-1]["date"],
            "n_days": len(curve),
            "n_fills": len(fills),
            "n_feedback": len(feedback),
            "initial_equity": round(initial, 2),
            "final_equity": round(final, 2),
            "total_return": round(total_return, 6),
        }


class SimulatedPaperTrader:
    """实时纸面交易包装：每日信号应用 + 盯市 + 状态持久化（SQLite）。

    账户/持仓/成交/权益曲线由注入的 ``SimSQLiteStore`` 持久化，
    替代此前 ``paper_state.json`` 轻量快照。
    """

    def __init__(
        self,
        portfolio: Optional[SimulatedPortfolio] = None,
        state_dir: str = "memory/portfolio/simulated",
    ) -> None:
        """初始化。

        Args:
            portfolio: 模拟仓实例（未注入时自动创建并挂接 SQLite store）
            state_dir: SQLite 状态库所在目录（sim_state.db）
        """
        self._state_dir = Path(state_dir)
        self._store = SimSQLiteStore(str(self._state_dir / "sim_state.db"))
        if portfolio is None:
            portfolio = SimulatedPortfolio(store=self._store)
        self._portfolio = portfolio
        self._signals: dict[str, Any] = {}  # date -> 最近信号（会话内，不持久化）
        self._load_signals_snapshot()

    def on_signal(self, signal: dict[str, Any], prices: dict[str, float]) -> SimApplyResult:
        """接收信号并撮合（账户/持仓/成交由 portfolio 经 store 自动落盘）。"""
        date = _signal_date(signal)
        result = self._portfolio.apply_signal(signal, prices, date)
        self._signals[date] = {"signal_id": signal.get("signal_id"), "result": result}
        return result

    def on_market_close(self, date: str, prices: dict[str, float]) -> SimDailyRecord:
        """收盘盯市（权益曲线由 portfolio 经 store 自动落盘）。"""
        return self._portfolio.mark_to_market(date, prices)

    def snapshot(self) -> dict[str, Any]:
        """返回当前账户/持仓快照 + 持久化明细。"""
        return {
            "account": self._portfolio.account_status(),
            "positions": self._portfolio.positions(),
            "equity_curve": self._portfolio.equity_curve(),
            "stored": self._store.snapshot(),
        }

    def close(self) -> None:
        """关闭 SQLite 连接。"""
        self._store.close()

    # ─── 持久化兼容 ─────────────────────────────────────

    def _load_signals_snapshot(self) -> None:
        """从 store 权益曲线末尾回填最近信号日期（仅用于会话展示，不持久化信号明细）。"""
        curve = self._store.load_equity_curve()
        if curve:
            self._signals.setdefault(curve[-1]["date"], {"signal_id": "", "result": {}})


__all__ = ["SimulatedReplayEngine", "SimulatedPaperTrader"]
