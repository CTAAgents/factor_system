"""tests/live_trade/test_simulated_portfolio.py — 模拟仓模块测试（D.1，v2.102.0）。

覆盖设计文档 §8 测试要点:
1. 开多头仓位（持仓/现金/保证金正确）
2. 加仓/平仓/反手（数量与已实现盈亏正确）
3. 逐日盯市（权益/未实现盈亏正确）
4. 风控拦截（approved=False 且 blocked_reasons 非空）
5. 干预拦截（暂停后信号被拦）
6. 因子归因（position_return 与手算一致）
7. 回放引擎（权益曲线有序、无未来函数）
8. 合约乘数/市场推断
"""

import pandas as pd
import pytest

from fts.factor_engine.cost_model import CostConfig, TransactionCostModel
from fts.live_trade import (
    InterventionController,
    SimDailyRecord,
    SimFill,
    SimPosition,
    SimSQLiteStore,
    SimulatedPaperTrader,
    SimulatedPortfolio,
    SimulatedReplayEngine,
    contract_multiplier,
    infer_market,
)
from fts.risk.risk_manager import RiskManager, RiskConfig

# 零滑点/零手续费/10% 保证金，简化手算
ZERO_COST = TransactionCostModel(
    config=CostConfig(
        slippage_bps=0.0,
        commission_bps=0.0,
        margin_rate=0.10,
        market="futures",
    )
)


def _signal(symbol: str = "RB0", direction: str = "long", position: float = 2.0, **kwgs) -> dict:
    """构造最小 FactorSignal。"""
    leg: dict = {"symbol": symbol, "direction": direction, "position": position, "confidence": 0.9}
    if "contributing_factors" in kwgs:
        leg["contributing_factors"] = kwgs.pop("contributing_factors")
    leg.update(kwgs)
    return {
        "signal_id": "sig_test",
        "timestamp": "2026-01-05T10:00:00",
        "universe": [symbol],
        "signals": [leg],
        "meta": {"trace_id": "trace_test"},
    }


# ─── 1. 开多头仓位 ──────────────────────────────────────


def test_open_long_position():
    """开多头：持仓/现金/保证金正确。"""
    pf = SimulatedPortfolio(
        config={"initial_cash": 100_000.0, "default_margin_rate": 0.10},
        cost_model=ZERO_COST,
    )
    result = pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    assert result["approved"] is True
    pos = pf.positions()["RB0"]
    assert pos["quantity"] == 2.0
    assert pos["direction"] == "long"
    assert pos["avg_price"] == 3000.0
    assert pos["multiplier"] == 10.0
    assert pos["margin_rate"] == 0.10
    # 现金 = 100000 - 保证金(3000*10*2*0.10=6000)
    assert pf.account_status()["cash"] == 94_000.0


# ─── 2. 加仓/平仓/反手 ──────────────────────────────────


def test_add_position():
    """同向加仓：数量与加权均价正确。"""
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST)
    pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    pf.mark_to_market("2026-01-06", {"RB0": 3100.0})
    pf.apply_signal(_signal("RB0", "long", 3.0), {"RB0": 3100.0}, "2026-01-06")
    pos = pf.positions()["RB0"]
    assert pos["quantity"] == 3.0
    assert pos["avg_price"] == pytest.approx((3000 * 2 + 3100 * 1) / 3)


def test_close_and_realized_pnl():
    """平仓：释放保证金 + 结转已实现盈亏。"""
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST)
    pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    pf.apply_signal(_signal("RB0", "flat", 0.0), {"RB0": 3200.0}, "2026-01-06")
    assert "RB0" not in pf.positions()
    # 已实现 = (3200-3000)*10*2 = 4000
    # 现金 = 100000 - 6000(保证金) + 3200*10*2*0.10(释放6400) + 4000(盈亏) = 104400
    assert pf.account_status()["cash"] == pytest.approx(104_400.0)
    assert pf.account_status()["realized_pnl_total"] == pytest.approx(4000.0)


def test_reverse_position():
    """反手：先平后开。"""
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST)
    pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    result = pf.apply_signal(_signal("RB0", "short", 2.0), {"RB0": 3000.0}, "2026-01-06")
    pos = pf.positions()["RB0"]
    assert pos["direction"] == "short"
    assert pos["quantity"] == 2.0
    # 反手共 2 笔：close_long + open_short
    assert len(result["fills"]) == 2


# ─── 3. 逐日盯市 ────────────────────────────────────────


def test_mark_to_market_equity():
    """盯市：权益 = 现金 + 保证金 + 未实现盈亏（期货）。"""
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST)
    pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    record = pf.mark_to_market("2026-01-06", {"RB0": 3100.0})
    # 权益 = 94000 + 3100*10*2*0.10(6200) + (3100-3000)*10*2(2000) = 102200
    assert record["equity"] == pytest.approx(102_200.0)
    assert record["margin_used"] == pytest.approx(6200.0)
    assert record["unrealized_pnl"] == pytest.approx(2000.0)


def test_mark_to_market_missing_price_skips():
    """行情缺失：跳过该标的，不中断。"""
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST)
    pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    record = pf.mark_to_market("2026-01-06", {})  # 无行情
    assert record["equity"] == pytest.approx(100_000.0)
    assert record["n_positions"] == 1


# ─── 4. 风控拦截 ────────────────────────────────────────


def test_risk_block():
    """风控：单品种仓位超限 → approved=False 且 blocked_reasons 非空。"""
    rm = RiskManager(config=RiskConfig(single_position_limit_pct=0.01))
    pf = SimulatedPortfolio(
        config={"initial_cash": 100_000.0},
        cost_model=ZERO_COST,
        risk_manager=rm,
    )
    # position=10, price=3000 → target_value=30000 > 1%*100000=1000
    result = pf.apply_signal(_signal("RB0", "long", 10.0), {"RB0": 3000.0}, "2026-01-05")
    assert result["approved"] is False
    assert result["blocked_reasons"]


# ─── 5. 干预拦截 ────────────────────────────────────────


def test_intervention_block():
    """干预：暂停后信号被拦（权限最高）。"""
    intervention = InterventionController()
    intervention.pause(operator="test")
    pf = SimulatedPortfolio(cost_model=ZERO_COST, intervention=intervention)
    result = pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    assert result["approved"] is False
    assert result["blocked_reasons"] == ["intervention"]


# ─── 6. 因子归因 ────────────────────────────────────────


def test_factor_attribution():
    """归因：position_return 与手算一致。"""
    pf = SimulatedPortfolio(cost_model=ZERO_COST)
    sig = _signal(
        "RB0",
        "long",
        2.0,
        contributing_factors=[{"factor_id": "f1", "weight": 0.5, "signal": 0.8}],
    )
    records = pf.attribute_factor_returns(sig, {"RB0": 0.05})
    assert len(records) == 1
    r = records[0]
    assert r["factor_id"] == "f1"
    assert r["signal_value"] == pytest.approx(0.8)  # 2*0.8/2
    assert r["position_return"] == pytest.approx(0.05)  # 2*(0.05*+1)/2


def test_factor_attribution_short():
    """归因：空头方向收益取负号。"""
    pf = SimulatedPortfolio(cost_model=ZERO_COST)
    sig = _signal(
        "RB0",
        "short",
        2.0,
        contributing_factors=[{"factor_id": "f1", "weight": 0.5, "signal": -0.6}],
    )
    records = pf.attribute_factor_returns(sig, {"RB0": 0.05})
    assert records[0]["signal_value"] == pytest.approx(-0.6)
    assert records[0]["position_return"] == pytest.approx(-0.05)  # 0.05 * (-1)


# ─── 7. 回放引擎 ────────────────────────────────────────


def _panel() -> dict[str, pd.DataFrame]:
    """构造 RB0 日线 panel（open/close）。"""
    df = pd.DataFrame(
        {
            "open": [3000.0, 3060.0, 3110.0],
            "close": [3050.0, 3100.0, 3150.0],
        },
        index=pd.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07"]),
    )
    return {"RB0": df}


def test_replay_engine_no_lookahead():
    """回放：t 日信号 t+1 开盘成交（无未来函数）。"""
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST)
    engine = SimulatedReplayEngine(pf)
    sig = _signal("RB0", "long", 2.0, contributing_factors=[{"factor_id": "f1", "signal": 0.8}])
    result = engine.replay([sig], _panel())

    # 成交价应为 2026-01-06 开盘 3060（非 t 日收盘）
    assert result["fills"][0]["fill_price"] == 3060.0
    # 权益曲线 3 日
    assert len(result["equity_curve"]) == 3
    # 有归因记录
    assert len(result["feedback_records"]) == 1
    assert result["summary"]["n_days"] == 3


def test_replay_equity_curve_order():
    """回放：权益曲线按日期递增。"""
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST)
    engine = SimulatedReplayEngine(pf)
    sig = _signal("RB0", "long", 2.0)
    result = engine.replay([sig], _panel())
    dates = [r["date"] for r in result["equity_curve"]]
    assert dates == sorted(dates)


# ─── 8. 合约乘数 / 市场推断 ─────────────────────────────


def test_contract_multiplier():
    """合约乘数：AU0→1000、RB→10、未知股票→1.0。"""
    assert contract_multiplier("AU0") == 1000.0
    assert contract_multiplier("RB") == 10.0
    assert contract_multiplier("RB2610") == 10.0
    assert contract_multiplier("600519") == 1.0


def test_infer_market():
    """市场推断：6 位数字→stock，其余→futures。"""
    assert infer_market("600519") == "stock"
    assert infer_market("510300") == "stock"
    assert infer_market("RB0") == "futures"
    assert infer_market("RB0", default="futures") == "futures"


# ─── 9. SQLite 持久化（D.1 增强）─────────────────────────


def test_sqlite_store_roundtrip(tmp_path):
    """store：账户/持仓/成交/权益 落库后可读回。"""
    db = tmp_path / "sim.db"
    store = SimSQLiteStore(str(db))
    store.save_account({"cash": 90000.0, "realized_pnl_total": 1000.0})
    store.save_positions(
        {"RB0": SimPosition(symbol="RB0", market="futures", direction="long", quantity=2.0, avg_price=3000.0)}
    )
    store.append_fills([SimFill(order_id="f1", symbol="RB0", side="open_long", quantity=2.0, fill_price=3000.0)])
    store.append_equity(SimDailyRecord(date="2026-01-06", equity=102200.0, cash=94000.0))
    store.close()

    store2 = SimSQLiteStore(str(db))
    assert store2.load_account()["cash"] == 90000.0
    assert store2.load_positions()["RB0"]["quantity"] == 2.0
    assert store2.load_fills()[0]["order_id"] == "f1"
    assert store2.load_equity_curve()[0]["equity"] == 102200.0
    store2.close()


def test_sqlite_restore_account_positions(tmp_path):
    """持久化：新 portfolio 挂接同一 store 后恢复账户/持仓/权益曲线。"""
    db = str(tmp_path / "sim.db")
    store = SimSQLiteStore(db)
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST, store=store)
    pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    pf.mark_to_market("2026-01-06", {"RB0": 3100.0})
    store.close()

    store2 = SimSQLiteStore(db)
    pf2 = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST, store=store2)
    assert pf2.positions()["RB0"]["quantity"] == 2.0
    assert pf2.account_status()["cash"] == pytest.approx(94_000.0)
    assert len(pf2.equity_curve()) == 1
    assert pf2.equity_curve()[0]["equity"] == pytest.approx(102_200.0)
    store2.close()


def test_sqlite_fills_persist_on_apply(tmp_path):
    """持久化：apply_signal 成交自动写入 sim_fills。"""
    store = SimSQLiteStore(str(tmp_path / "sim.db"))
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST, store=store)
    pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    fills = store.load_fills()
    assert len(fills) == 1
    assert fills[0]["symbol"] == "RB0"
    assert fills[0]["side"] == "open_long"
    store.close()


def test_paper_trader_persists_to_sqlite(tmp_path):
    """PaperTrader：on_signal + on_market_close 落库 SQLite，重启可恢复。"""
    state_dir = str(tmp_path / "simulated")
    trader = SimulatedPaperTrader(state_dir=state_dir)
    trader.on_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0})
    trader.on_market_close("2026-01-06", {"RB0": 3100.0})
    snap = trader.snapshot()
    assert snap["stored"]["fills"][0]["symbol"] == "RB0"
    assert len(snap["stored"]["equity_curve"]) == 1
    trader.close()

    # 重启后从 SQLite 恢复
    trader2 = SimulatedPaperTrader(state_dir=state_dir)
    assert trader2.snapshot()["positions"]["RB0"]["quantity"] == 2.0
    assert len(trader2.snapshot()["equity_curve"]) == 1
    trader2.close()


def test_portfolio_risk_status():
    """组合级风控（D.2 §3）：健康组合 OK；重仓/亏损触发预警。"""
    pf = SimulatedPortfolio(config={"initial_cash": 100_000.0}, cost_model=ZERO_COST)
    # 空仓 → OK
    empty = pf.portfolio_risk_status()
    assert empty["max_severity"] == "OK"
    assert empty["checks"] == []

    # 重仓单标的（杠杆远超上限）→ BLOCK/FORCE
    pf.apply_signal(_signal("RB0", "long", 2.0), {"RB0": 3000.0}, "2026-01-05")
    heavy = pf.portfolio_risk_status({"RB0": 3000.0})
    assert heavy["checks"], "重仓应产出组合级检查项"
    assert heavy["block_new_open"] or heavy["force_close"]

    # 大幅亏损 → FORCE_CLOSE
    pf.mark_to_market("2026-01-06", {"RB0": 2500.0})  # 2手*10*500=1万亏损=10%
    risked = pf.portfolio_risk_status({"RB0": 2500.0})
    assert risked["force_close"] or risked["max_severity"] in ("BLOCK", "FORCE_CLOSE")


# ─── GAP-150 写路径契约（v3.1.0+6） ────────────────────────


def test_sqlite_store_default_path_registry_ok(tmp_path, monkeypatch):
    """GAP-150：默认路径（sim_state.db 已登记 sim_portfolio 域）构造不抛。

    _connect 打桩避免真实落盘污染项目目录。
    """
    import fts.live_trade.sqlite_store as sqlite_store_mod
    from fts.store import StorageRegistry

    monkeypatch.setattr(sqlite_store_mod.SimSQLiteStore, "_connect", lambda self: None)
    monkeypatch.setattr("fts.store.get_storage_registry", StorageRegistry)
    store = SimSQLiteStore()
    assert store._db_path == "memory/portfolio/simulated/sim_state.db"


def test_sqlite_store_default_path_strict_blocks(tmp_path, monkeypatch):
    """GAP-150：registry 未登记默认路径（严格模式）→ ValueError 阻断；显式注入豁免。"""
    import fts.live_trade.sqlite_store as sqlite_store_mod
    from fts.store import StorageRegistry

    monkeypatch.setattr(sqlite_store_mod.SimSQLiteStore, "_connect", lambda self: None)
    empty = StorageRegistry(yaml_path=tmp_path / "empty_landscape.yaml")
    monkeypatch.setattr("fts.store.get_storage_registry", lambda: empty)

    with pytest.raises(ValueError, match="写路径未登记"):
        SimSQLiteStore()  # 默认路径未登记 → 阻断
    store = SimSQLiteStore(str(tmp_path / "sim.db"))  # 显式注入路径 → 豁免
    assert store._db_path == str(tmp_path / "sim.db")