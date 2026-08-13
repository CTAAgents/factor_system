"""阶段3 分钟级模拟盘单元测试（paper_trader_mhf）。

覆盖：成交执行、止损、时间止损、日内止损、品种上限、成本扣除。
纯逻辑测试（合成数据）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.live_trade.paper_trader_mhf import (  # noqa: E402
    MhfPaperTrader,
    MhfRiskConfig,
)


def _mk_panel(n: int = 30, start: float = 100.0, step: float = 0.0,
              symbols: tuple[str, ...] = ("A",)) -> dict[str, pd.DataFrame]:
    """构造确定性分钟面板（价格按 step 递增）。"""
    idx = pd.date_range("2026-01-05 09:00", periods=n, freq="30min")
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        close = [start + step * i for i in range(n)]
        out[sym] = pd.DataFrame(
            {
                "open": close,
                "high": [c * 1.002 for c in close],
                "low": [c * 0.998 for c in close],
                "close": close,
                "volume": [1000.0] * n,
            },
            index=idx,
        )
    return out


def _long_signal(n: int, idx: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(1.0, index=idx)


class TestPaperTrader:
    def test_basic_long_uptrend(self) -> None:
        """持续多头 + 单边上涨：期末净值 > 1，有成交记录。"""
        panel = _mk_panel(step=0.2)
        idx = panel["A"].index
        sig = _long_signal(len(idx), idx)
        res = MhfPaperTrader(panel, {"A": sig}, MhfRiskConfig(cost_bps_map={})).run()
        assert res.metrics["final_equity"] > 1.0
        assert res.metrics["n_fills"] >= 2  # 开仓 + 强制平仓
        assert not res.equity.empty

    def test_cost_deducted(self) -> None:
        """高成本品种开平仓扣成本：净值低于零成本。"""
        panel = _mk_panel(step=0.0)  # 价格不动
        idx = panel["A"].index
        sig = _long_signal(len(idx), idx)
        r0 = MhfPaperTrader(panel, {"A": sig}, MhfRiskConfig(cost_bps_map={})).run()
        r1 = MhfPaperTrader(
            panel, {"A": sig}, MhfRiskConfig(cost_bps_map={"A": 50.0})
        ).run()
        assert r1.metrics["final_equity"] < r0.metrics["final_equity"]

    def test_stop_loss_triggered(self) -> None:
        """单品种止损：价格下跌触发 stop_loss 平仓，事件记录。"""
        panel = _mk_panel(step=-0.5)  # 持续下跌
        idx = panel["A"].index
        sig = _long_signal(len(idx), idx)
        res = MhfPaperTrader(
            panel, {"A": sig},
            MhfRiskConfig(stop_loss_pct=0.01, cost_bps_map={}),
        ).run()
        kinds = [e.kind for e in res.events]
        assert "stop_loss" in kinds

    def test_time_stop_triggered(self) -> None:
        """持仓时限：超过 holding_bars 平仓。"""
        panel = _mk_panel(step=0.01)
        idx = panel["A"].index
        sig = _long_signal(len(idx), idx)
        res = MhfPaperTrader(
            panel, {"A": sig},
            MhfRiskConfig(holding_bars=3, cost_bps_map={}),
        ).run()
        kinds = [e.kind for e in res.events]
        assert "time_stop" in kinds

    def test_daily_loss_halt(self) -> None:
        """日内组合止损：触发后当日停止开新仓（单品种止损禁用，聚焦日内止损）。"""
        panel = _mk_panel(step=-0.8)  # 首日大跌
        idx = panel["A"].index
        sig = _long_signal(len(idx), idx)
        res = MhfPaperTrader(
            panel, {"A": sig},
            MhfRiskConfig(daily_loss_pct=0.004, stop_loss_pct=1.0,
                          cost_bps_map={}),
        ).run()
        kinds = [e.kind for e in res.events]
        assert "daily_loss_halt" in kinds

    def test_max_positions_cap(self) -> None:
        """品种上限：超过 max_positions 不再开新仓。"""
        symbols = ("A", "B", "C")
        panel = _mk_panel(step=0.1, symbols=symbols)
        signals = {s: pd.Series(1.0, index=panel[s].index) for s in symbols}
        res = MhfPaperTrader(
            panel, signals, MhfRiskConfig(max_positions=2, cost_bps_map={})
        ).run()
        # 最多同时持仓 2 个
        max_pos = 0
        for t in res.equity.index:
            held = 0
            for s in symbols:
                has = any(f.time <= t and f.symbol == s and f.side != "close"
                          and not (pd.Timestamp(f.time) <= t and f.side == "close")
                          for f in res.fills)
                if has:
                    held += 1
            max_pos = max(max_pos, held)
        assert max_pos <= 2

    def test_empty_panel(self) -> None:
        res = MhfPaperTrader({}, {}).run()
        assert res.equity.empty
        assert res.metrics["n_fills"] == 0
