"""
fts/live_trade/paper_trader_mhf.py — 分钟级模拟盘（Paper Trading，Phase 3）。

对 30m 分钟信号做事件驱动的模拟盘回放，叠加盘中风控：

- 撮合与回测引擎一致：t-1 信号 → t bar 开盘成交，换仓扣单边成本（品种差异化）
- 盘中风控（逐 bar 状态机）：
    * 单品种止损：持仓亏损达 stop_loss_pct 平仓
    * 日内组合止损：组合亏损达 daily_loss_pct 停止开新仓（允许平仓）
    * 持仓时限：持仓超 holding_bars 平仓（时间止损）
    * 品种上限：max_positions（多空各半）
- 允许隔夜持仓
- **内存模式**：状态仅内存维护，结果由调用方落文件（DuckDB 锁兼容）

设计文档: docs/harness/plans/33-mhf-trading-plan.md §Phase 3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, cast

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MhfRiskConfig:
    """盘中风控配置。"""

    stop_loss_pct: float = 0.012      # 单品种持仓亏损止损（比例）
    daily_loss_pct: float = 0.015     # 日内组合止损（触发后当日停止开新仓）
    holding_bars: int = 16            # 持仓最长时限（bar，30m×16≈1 交易日）
    max_positions: int = 8            # 最大持仓品种数
    target_pct: float = 0.0625        # 单品种目标仓位占比
    cost_bps_map: dict[str, float] = field(default_factory=dict)  # 品种差异化成本
    default_cost_bps: float = 3.0

    def cost_for(self, symbol: str) -> float:
        return self.cost_bps_map.get(symbol, self.default_cost_bps)


@dataclass
class PaperFill:
    """成交记录。"""

    time: pd.Timestamp
    symbol: str
    side: str            # open_long / open_short / close
    price: float
    quantity_pct: float  # 换手仓位占比
    reason: str          # signal / stop_loss / time_stop / daily_stop / hold


@dataclass
class PaperEvent:
    """风控/事件记录。"""

    time: pd.Timestamp
    symbol: str
    kind: str            # stop_loss / time_stop / daily_loss_halt / signal
    message: str


@dataclass
class PaperResult:
    """模拟盘结果。"""

    equity: pd.Series          # 分钟净值
    daily_equity: pd.Series
    fills: list[PaperFill]
    events: list[PaperEvent]
    metrics: dict[str, float]


class MhfPaperTrader:
    """分钟级模拟盘（事件驱动 + 盘中风控，内存模式）。"""

    def __init__(
        self,
        panel: dict[str, pd.DataFrame],
        signals: dict[str, pd.Series],
        risk: Optional[MhfRiskConfig] = None,
    ) -> None:
        """初始化。

        Args:
            panel: {symbol: 分钟 OHLCV DataFrame（open/high/low/close）}。
            signals: {symbol: 方向信号 Series}（±1/0，t 期收盘产生）。
            risk: 风控配置。
        """
        self.panel = panel
        self.signals = signals
        self.risk = risk or MhfRiskConfig()
        # 对齐统一时间轴
        self.time_index = self._union_index()

    def _union_index(self) -> pd.DatetimeIndex:
        idx: Optional[pd.DatetimeIndex] = None
        for df in self.panel.values():
            if df is None or df.empty:
                continue
            if idx is None:
                idx = cast(pd.DatetimeIndex, df.index)
            else:
                idx = cast(pd.DatetimeIndex, idx.union(df.index))
        return idx if idx is not None else pd.DatetimeIndex([])

    def run(self) -> PaperResult:
        """逐 bar 回放：风控 → 信号执行 → 盯市。返回净值/成交/事件/指标。"""
        risk = self.risk
        positions: dict[str, dict[str, float]] = {}   # sym -> {dir, entry, entry_bar}
        fills: list[PaperFill] = []
        events: list[PaperEvent] = []
        self._realized = 0.0
        day_start_realized = 0.0
        cur_day: Optional[pd.Timestamp] = None
        halted_today = False
        nav: list[tuple[pd.Timestamp, float]] = []

        # 信号前移（t-1 信号 → t 期持仓）
        sig_target: dict[str, pd.Series] = {}
        for sym, s in self.signals.items():
            sig_target[sym] = pd.to_numeric(s, errors="coerce").shift(1).fillna(0.0)

        for i, t in enumerate(self.time_index):
            date_ = t.normalize()
            if cur_day is None or date_ != cur_day:
                cur_day = date_
                day_start_realized = self._realized
                halted_today = False

            # ── 1. 风控扫描（先于新信号）──
            for sym in list(positions.keys()):
                df = self.panel.get(sym)
                pos = positions[sym]
                if df is None or t not in df.index:
                    continue
                px = float(df["close"].loc[t])
                pnl = (px / pos["entry"] - 1.0) * pos["dir"]
                bars_held = i - pos["entry_bar"]
                if pnl <= -risk.stop_loss_pct:
                    self._close(sym, t, px, pos, fills, positions, reason="stop_loss")
                    events.append(PaperEvent(t, sym, "stop_loss",
                                             f"亏损 {pnl:.2%} 止损平仓"))
                elif bars_held >= risk.holding_bars:
                    self._close(sym, t, px, pos, fills, positions, reason="time_stop")
                    events.append(PaperEvent(t, sym, "time_stop",
                                             f"持仓 {bars_held} bar 超时限平仓"))

            # ── 2. 日内组合止损 ──
            day_pnl = (self._realized + self._unrealized(positions, t)) \
                - day_start_realized
            if day_pnl <= -risk.daily_loss_pct and not halted_today:
                halted_today = True
                events.append(PaperEvent(t, "", "daily_loss_halt",
                                         f"日内亏损 {day_pnl:.2%}，停止开新仓"))

            # ── 3. 信号执行（t-1 信号 → t 开盘）──
            for sym, sig in sig_target.items():
                if t not in sig.index:
                    continue
                target = int(np.sign(sig.loc[t]))
                df = self.panel.get(sym)
                if df is None or t not in df.index:
                    continue
                open_px = float(df["open"].loc[t])
                has = sym in positions
                if has and positions[sym]["dir"] != target:
                    # 反向/清仓 → 平仓
                    self._close(sym, t, open_px, positions[sym], fills, positions,
                                reason="signal")
                    has = False
                if target != 0 and not has:
                    if halted_today:
                        events.append(PaperEvent(t, sym, "daily_stop",
                                                 "日内止损期间跳过开仓"))
                        continue
                    if len(positions) >= risk.max_positions:
                        continue
                    self._open(sym, t, open_px, target, i, fills, positions)

            # ── 4. 盯市净值 = 1 + 已实现 + 未实现 ──
            nav.append((t, 1.0 + self._realized + self._unrealized(positions, t)))

        # 回放结束强制平仓结算
        for sym, pos in list(positions.items()):
            df = self.panel.get(sym)
            if df is None or df.empty:
                continue
            last_t = df.index[-1]
            self._close(sym, last_t, float(df["close"].iloc[-1]), pos, fills,
                        positions, reason="end_of_run")

        equity = pd.Series({t: v for t, v in nav}, name="equity").sort_index()
        if equity.empty:
            return PaperResult(equity=equity, daily_equity=equity, fills=fills,
                               events=events, metrics={"n_fills": 0, "n_events": 0,
                                                       "n_days": 0, "final_equity": 1.0})
        daily = equity.groupby(equity.index.normalize()).last()
        metrics = {
            "n_fills": len(fills),
            "n_events": len(events),
            "n_days": int(len(daily)),
            "final_equity": round(float(equity.iloc[-1]), 4),
        }
        return PaperResult(equity=equity, daily_equity=daily, fills=fills,
                           events=events, metrics=metrics)

    # ── 内部辅助 ──

    def _unrealized(self, positions: dict[str, dict[str, float]],
                    t: pd.Timestamp) -> float:
        """当前持仓按 t 收盘盯市的未实现盈亏。"""
        total = 0.0
        for sym, pos in positions.items():
            df = self.panel.get(sym)
            if df is None or t not in df.index:
                continue
            px = float(df["close"].loc[t])
            total += (px / pos["entry"] - 1.0) * pos["dir"] * self.risk.target_pct
        return total

    def _open(self, sym: str, t: pd.Timestamp, price: float, direction: int,
              bar_idx: int, fills: list[PaperFill],
              positions: dict[str, dict[str, float]]) -> None:
        positions[sym] = {"dir": float(direction), "entry": price,
                          "entry_bar": float(bar_idx)}
        self._realized -= self.risk.target_pct * (self.risk.cost_for(sym) / 1e4)
        fills.append(PaperFill(t, sym,
                               "open_long" if direction > 0 else "open_short",
                               price, self.risk.target_pct, "signal"))

    def _close(self, sym: str, t: pd.Timestamp, price: float,
               pos: dict[str, float], fills: list[PaperFill],
               positions: dict[str, dict[str, float]], reason: str) -> None:
        if sym in positions:
            del positions[sym]
        pnl = (price / pos["entry"] - 1.0) * pos["dir"] * self.risk.target_pct
        cost = self.risk.target_pct * (self.risk.cost_for(sym) / 1e4)
        self._realized += pnl - cost
        fills.append(PaperFill(t, sym, "close", price, self.risk.target_pct, reason))
