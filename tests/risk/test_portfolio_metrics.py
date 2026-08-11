"""
tests/risk/test_portfolio_metrics.py — 组合级风控指标测试（D.2 §3）。

覆盖:
    - compute_portfolio_metrics: 杠杆/仓位/保证金/有效持仓/回撤/日亏损/连续亏损
    - 波动尾部: 年化波动/VaR/CVaR/突变（权益曲线收益）
    - evaluate_metrics: 三级预警（WARN/BLOCK/FORCE_CLOSE）与动作（block_new_open/force_close）
    - 降级: 空数据/除零/异常输入不抛错
"""

from __future__ import annotations

import numpy as np
import pytest

from fts.risk.portfolio_metrics import (
    compute_portfolio_metrics,
    evaluate_metrics,
)


def _account(**over: float) -> dict:
    base = {
        "total_equity": 1_000_000.0,
        "cash": 200_000.0,
        "peak_equity": 1_000_000.0,
        "daily_pnl": 0.0,
        "margin_used": 100_000.0,
        "position_value": 500_000.0,
    }
    base.update(over)
    return base


def _position(symbol: str, qty: float, price: float, mult: float = 10.0, direction: str = "long") -> dict:
    return {
        "symbol": symbol,
        "market": "futures",
        "direction": direction,
        "quantity": qty,
        "avg_price": price,
        "multiplier": mult,
        "margin_rate": 0.12,
        "realized_pnl": 0.0,
    }


def _equity_curve(equities: list[float], pnls: list[float]) -> list[dict]:
    return [{"equity": e, "daily_pnl": p} for e, p in zip(equities, pnls)]


class TestComputePortfolioMetrics:
    def test_leverage_and_positions(self) -> None:
        """杠杆/总仓位/单标的最大仓位计算正确。"""
        positions = {
            "RB0": _position("RB0", 100, 500.0),     # 100*500*10=500k
            "AU0": _position("AU0", 20, 400.0, mult=1000.0),  # 20*400*1000=8,000k
        }
        m = compute_portfolio_metrics(_account(position_value=0.0), positions)
        # 总名义 = 500k + 8,000k = 8,500k；equity=1,000k
        assert m["leverage"] == pytest.approx(8.5, abs=1e-6)
        assert m["max_single_position_ratio"] == pytest.approx(8.0, abs=1e-6)

    def test_margin_usage(self) -> None:
        m = compute_portfolio_metrics(_account(margin_used=500_000.0), {})
        assert m["margin_usage"] == pytest.approx(0.5)

    def test_effective_positions_concentration(self) -> None:
        """集中持仓 → 有效持仓数小；分散持仓 → 大。"""
        concentrated = {
            "A": _position("A", 90, 100.0, 1.0),
            "B": _position("B", 10, 100.0, 1.0),
        }
        spread = {s: _position(s, 25, 100.0, 1.0) for s in ("A", "B", "C", "D")}
        m1 = compute_portfolio_metrics(_account(position_value=0.0), concentrated)
        m2 = compute_portfolio_metrics(_account(position_value=0.0), spread)
        assert m1["effective_positions"] < m2["effective_positions"]
        assert m2["effective_positions"] == pytest.approx(4.0, abs=1e-6)

    def test_vol_tail_metrics(self) -> None:
        """权益曲线收益 → 年化波动/VaR/CVaR 有限且非负。"""
        rng = np.random.default_rng(5)
        rets = rng.normal(0.0005, 0.01, 80)
        eq = [1_000_000.0]
        for r in rets:
            eq.append(eq[-1] * (1 + r))
        pnls = [eq[i] - eq[i - 1] for i in range(1, len(eq))]
        m = compute_portfolio_metrics(_account(), {}, equity_curve=_equity_curve(eq, pnls))
        assert m["annual_vol"] > 0
        assert m["var95"] > 0
        assert m["cvar95"] >= m["var95"] - 1e-9
        assert m["vol_spike_ratio"] > 0

    def test_drawdown_and_loss_history(self) -> None:
        """回撤/连续亏损/盈亏比正确。"""
        eq = [1_000_000.0, 990_000.0, 980_000.0, 970_000.0, 960_000.0]
        pnls = [-10_000.0, -10_000.0, -10_000.0, -10_000.0]
        acct = _account(peak_equity=1_000_000.0, total_equity=960_000.0)
        m = compute_portfolio_metrics(acct, {}, equity_curve=_equity_curve(eq, pnls))
        assert m["drawdown"] == pytest.approx(-0.04)
        assert m["consecutive_losses"] == 4
        assert m["win_loss_ratio"] == pytest.approx(0.0)

    def test_daily_loss_ratio(self) -> None:
        acct = _account(total_equity=1_000_000.0, daily_pnl=-80_000.0)
        m = compute_portfolio_metrics(acct, {})
        assert m["daily_loss_ratio"] == pytest.approx(0.08)

    def test_empty_input_degrades(self) -> None:
        """空输入不抛错，指标为 0/None 安全值。"""
        m = compute_portfolio_metrics({}, {}, equity_curve=None)
        assert m["leverage"] == 0.0
        assert m["annual_vol"] == 0.0
        assert m["effective_positions"] == 0.0
        assert m["drawdown"] == 0.0

    def test_nan_values_safe(self) -> None:
        """NaN/Inf 账户字段兜底为 0。"""
        acct = _account(total_equity=float("nan"), position_value=float("inf"))
        m = compute_portfolio_metrics(acct, {})
        assert m["leverage"] == 0.0


class TestEvaluateMetrics:
    def _metrics(self, **over: float) -> dict:
        base = {
            "leverage": 1.5,
            "total_position_ratio": 0.5,
            "max_single_position_ratio": 0.1,
            "margin_usage": 0.3,
            "effective_positions": 10.0,
            "drawdown": -0.02,
            "daily_loss_ratio": 0.01,
            "consecutive_losses": 0,
            "win_loss_ratio": 1.5,
            "annual_vol": 0.15,
            "var95": 0.01,
            "cvar95": 0.015,
            "vol_spike_ratio": 1.0,
            "liquidity_share": 0.01,
            "slippage_dev": 1.0,
            "partial_fill_ratio": 0.0,
            "fill_rate": 1.0,
        }
        base.update(over)
        return base

    def test_healthy_ok(self) -> None:
        """健康组合 → max_severity=OK，无动作。"""
        r = evaluate_metrics(self._metrics())
        assert r["max_severity"] == "OK"
        assert r["checks"] == []
        assert not r["block_new_open"] and not r["force_close"]

    def test_warn_on_high_vol(self) -> None:
        """年化波动超 WARN → WARN。"""
        r = evaluate_metrics(self._metrics(annual_vol=0.30))
        assert r["max_severity"] == "WARN"
        assert not r["block_new_open"] and not r["force_close"]

    def test_block_on_single_position(self) -> None:
        """单标仓位超上限 → BLOCK（拒绝新开仓，不强平）。"""
        r = evaluate_metrics(self._metrics(max_single_position_ratio=0.25))
        assert r["max_severity"] == "BLOCK"
        assert r["block_new_open"] and not r["force_close"]

    def test_force_close_on_drawdown(self) -> None:
        """回撤超上限 → FORCE_CLOSE。"""
        r = evaluate_metrics(self._metrics(drawdown=-0.25))
        assert r["max_severity"] == "FORCE_CLOSE"
        assert r["force_close"]

    def test_force_close_on_daily_loss(self) -> None:
        """单日亏损超上限 → FORCE_CLOSE。"""
        r = evaluate_metrics(self._metrics(daily_loss_ratio=0.08))
        assert r["max_severity"] == "FORCE_CLOSE"
        assert r["force_close"]

    def test_force_close_on_consecutive_losses(self) -> None:
        """连续亏损超限 → FORCE_CLOSE（暂停交易）。"""
        r = evaluate_metrics(self._metrics(consecutive_losses=9))
        assert r["max_severity"] == "FORCE_CLOSE"
        assert r["force_close"]

    def test_block_warn_priority(self) -> None:
        """同时 WARN 与 BLOCK → max_severity=BLOCK。"""
        r = evaluate_metrics(self._metrics(annual_vol=0.30, max_single_position_ratio=0.25))
        assert r["max_severity"] == "BLOCK"

    def test_effective_positions_low(self) -> None:
        """有效持仓数过低 → BLOCK。"""
        r = evaluate_metrics(self._metrics(effective_positions=2.0))
        assert r["max_severity"] == "BLOCK"
        assert r["block_new_open"]

    def test_custom_config(self) -> None:
        """自定义阈值覆盖默认。"""
        cfg = {"max_drawdown": 0.05}
        r = evaluate_metrics(self._metrics(drawdown=-0.06), cfg)
        assert r["max_severity"] == "FORCE_CLOSE"
