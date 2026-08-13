"""阶段2 分钟事件驱动回测引擎单元测试（mhf_backtest）。

覆盖：零未来（信号 shift 成交）、成本扣除、隔夜衔接、极端 bar 过滤、空输入降级。
纯逻辑测试（合成数据，不依赖 TDX / DuckDB）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fts.factor_engine.mhf_backtest import (  # noqa: E402
    MhfBacktestConfig,
    _extreme_mask,
    _per_symbol,
    run_mhf_backtest,
)


def _panel(n: int = 40, seed: int = 1) -> dict[str, pd.DataFrame]:
    """构造多品种分钟面板（确定性可手算）。"""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2026-01-05 09:00", periods=n, freq="5min")
    out: dict[str, pd.DataFrame] = {}
    for sym in ("A", "B"):
        close = 100 + np.cumsum(rng.normal(0, 0.3, n))
        open_ = close * (1 + rng.normal(0, 0.001, n))
        out[sym] = pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum(open_, close) * 1.001,
                "low": np.minimum(open_, close) * 0.999,
                "close": close,
                "volume": rng.integers(100, 500, n).astype(float),
            },
            index=idx,
        )
    return out


class TestPerSymbol:
    """单品种事件驱动：零未来 + 成本。"""

    def test_signal_lag_one_bar(self) -> None:
        """t 期持仓 = sign(sig_{t-1})：信号产生次 bar 开盘成交。"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.0, 10.0, 10.0, 10.0],
                "high": [10.1] * 5,
                "low": [9.9] * 5,
                "close": [10.0, 10.2, 10.4, 10.6, 10.8],
                "volume": [100.0] * 5,
            },
            index=pd.date_range("2026-01-05 09:00", periods=5, freq="5min"),
        )
        sig = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0], index=df.index, name="A")
        ret, turnover, pos, trades = _per_symbol(df, sig, MhfBacktestConfig(cost_bps=0.0))
        # pos_t = sign(sig_{t-1})：首 bar 0，之后全 1
        assert pos.iloc[0] == 0.0
        assert pos.iloc[1:].all() == 1.0
        # 首笔成交在 bar 1 开盘（open=10.0），换手 1.0
        assert len(trades) == 1
        assert trades[0].bar_time == df.index[1]
        assert trades[0].price == 10.0
        # bar1 收益 = pos × (close/open - 1) = 1 × (10.2/10 - 1) = 0.02
        assert ret.iloc[1] == pytest.approx(0.02)

    def test_cost_deducted_on_turnover(self) -> None:
        """换仓扣单边成本（cost_bps）。"""
        df = pd.DataFrame(
            {
                "open": [10.0, 10.0, 10.0],
                "high": [10.1] * 3,
                "low": [9.9] * 3,
                "close": [10.0, 10.0, 10.0],
                "volume": [100.0] * 3,
            },
            index=pd.date_range("2026-01-05 09:00", periods=3, freq="5min"),
        )
        sig = pd.Series([1.0, 1.0, 1.0], index=df.index, name="A")
        ret, _, _, _ = _per_symbol(df, sig, MhfBacktestConfig(cost_bps=10.0))
        # bar1 换手 1.0，成本 10bps = 0.001
        assert ret.iloc[1] == pytest.approx(-0.001)


class TestBacktest:
    """多品种回测汇总。"""

    def test_empty_panel(self) -> None:
        r = run_mhf_backtest({}, {})
        assert r.equity.empty
        assert r.metrics == {}

    def test_missing_signal_skipped(self) -> None:
        panel = _panel()
        r = run_mhf_backtest(panel, {"A": pd.Series([1.0] * len(panel["A"]), index=panel["A"].index)})
        assert r.metrics.get("n_bars", 0) > 0

    def test_all_long_upmarket_positive(self) -> None:
        """全多头 + 单边上涨：净值 > 1（零成本）。"""
        panel = _panel(n=50, seed=3)
        signals = {
            sym: pd.Series(1.0, index=df.index) for sym, df in panel.items()
        }
        r = run_mhf_backtest(panel, signals, MhfBacktestConfig(cost_bps=0.0))
        assert r.equity.iloc[-1] > 1.0
        assert r.metrics["sharpe"] > 0

    def test_cost_reduces_return(self) -> None:
        panel = _panel(n=50, seed=4)
        signals = {
            sym: pd.Series(
                [1.0, -1.0] * (len(df) // 2) + [1.0] * (len(df) % 2),
                index=df.index,
            )
            for sym, df in panel.items()
        }
        r0 = run_mhf_backtest(panel, signals, MhfBacktestConfig(cost_bps=0.0))
        r1 = run_mhf_backtest(panel, signals, MhfBacktestConfig(cost_bps=20.0))
        assert r1.metrics["total_return"] < r0.metrics["total_return"]
        assert r1.metrics["cost_ratio"] > 0.0

    def test_overnight_carry_continuous(self) -> None:
        """跨日（含夜盘）收益连续，允许隔夜持仓。"""
        idx = pd.date_range("2026-01-05 09:00", periods=10, freq="5min").union(
            pd.date_range("2026-01-06 09:00", periods=10, freq="5min")
        )
        df = pd.DataFrame(
            {
                "open": [10.0] * len(idx),
                "high": [10.1] * len(idx),
                "low": [9.9] * len(idx),
                "close": [10.0 + 0.01 * i for i in range(len(idx))],
                "volume": [100.0] * len(idx),
            },
            index=idx,
        )
        sig = pd.Series(1.0, index=df.index, name="A")
        r = run_mhf_backtest({"A": df}, {"A": sig}, MhfBacktestConfig(cost_bps=0.0))
        assert len(r.daily_equity) == 2  # 两个交易日
        assert r.equity.iloc[-1] > 1.0   # 持仓跨日获利

    def test_extreme_mask(self) -> None:
        idx = pd.date_range("2026-01-05 09:00", periods=3, freq="5min")
        df = pd.DataFrame(
            {
                "open": [100.0, 100.0, 130.0],   # bar2 跳空 +30%
                "high": [101.0, 101.0, 131.0],
                "low": [99.0, 99.0, 129.0],
                "close": [100.0, 101.0, 130.0],
                "volume": [100.0] * 3,
            },
            index=idx,
        )
        mask = _extreme_mask(df, limit_pct=0.2)
        assert bool(mask.iloc[2]) is True
        assert bool(mask.iloc[1]) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"])
