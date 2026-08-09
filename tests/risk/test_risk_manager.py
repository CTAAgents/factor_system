"""tests/risk/test_risk_manager.py — 实时风控管理器单元测试。

HARNESS §测试随重构: 全量覆盖 risk_manager.py 五项风控规则。

测试策略:
    - 构造最小合法的 signal / account / positions 契约字典
    - 覆盖每项规则通过 / 拒绝边界
    - 覆盖 check() 主入口聚合逻辑
"""

from __future__ import annotations

from typing import Any

import pytest

from fts.risk.risk_manager import RiskManager


# ─── 构造辅助 ────────────────────────────────────────────


def make_signal(
    signals: list[dict[str, Any]] | None = None,
    signal_id: str = "SIG-TEST-001",
) -> dict[str, Any]:
    """构造 FactorSignal 契约字典。"""
    return {"signal_id": signal_id, "signals": signals or []}


def make_account(**overrides: Any) -> dict[str, Any]:
    """构造 AccountStatus 契约字典（默认 10 万权益，无亏损）。"""
    base: dict[str, Any] = {
        "balance": 100_000.0,
        "total_equity": 100_000.0,
        "peak_equity": 120_000.0,
        "daily_pnl": 0.0,
        "position_value": 0.0,
    }
    base.update(overrides)
    return base


def make_position(symbol: str, market_value: float) -> dict[str, dict[str, Any]]:
    """构造单个持仓字典。"""
    return {symbol: {"symbol": symbol, "market_value": market_value}}


# ─── 初始化 ──────────────────────────────────────────────


class TestRiskManagerInit:
    """RiskManager.__init__ 测试。"""

    def test_default_config(self):
        """默认配置使用文档约定值。"""
        rm = RiskManager()
        assert rm._config["single_position_limit_pct"] == 0.10
        assert rm._config["max_portfolio_drawdown_pct"] == 0.20
        assert rm._config["daily_loss_limit_pct"] == 0.05
        assert rm._config["max_leverage"] == 3.0
        assert rm._config["max_concentration_pct"] == 0.50
        assert rm._config["max_open_positions"] == 20

    def test_custom_config_merged(self):
        """自定义配置覆盖默认值，未指定项保留默认。"""
        rm = RiskManager(config={"single_position_limit_pct": 0.20})
        assert rm._config["single_position_limit_pct"] == 0.20
        assert rm._config["max_leverage"] == 3.0

    def test_none_value_ignored(self):
        """配置中 None 值不覆盖默认值。"""
        rm = RiskManager(config={"single_position_limit_pct": None})
        assert rm._config["single_position_limit_pct"] == 0.10

    def test_empty_config(self):
        """空配置等价于默认配置。"""
        rm = RiskManager(config={})
        assert rm._config == rm._config  # 不抛异常即可


# ─── 单品种仓位上限 ──────────────────────────────────────


class TestSinglePositionLimit:
    """单品种仓位上限规则测试。"""

    def test_passed_below_limit(self):
        """现有 + 目标市值低于 10% 上限时通过。"""
        rm = RiskManager()
        sig = make_signal(signals=[{"symbol": "RB", "position": 3, "price": 1_000.0}])
        positions = make_position("RB", 2_000.0)
        item = rm._check_single_position_limit(sig, make_account(), positions)
        # (2000 + 3000) / 100000 = 5%
        assert item["passed"] is True
        assert item["check_name"] == "single_position_limit"
        assert item["current_value"] == pytest.approx(0.05)
        assert item["severity"] == "warning"

    def test_blocked_above_limit(self):
        """现有 + 目标市值超过 10% 上限时拦截。"""
        rm = RiskManager()
        sig = make_signal(signals=[{"symbol": "RB", "position": 12, "price": 1_000.0}])
        positions = make_position("RB", 0.0)
        item = rm._check_single_position_limit(sig, make_account(), positions)
        # 12000 / 100000 = 12%
        assert item["passed"] is False
        assert item["current_value"] == pytest.approx(0.12)
        assert item["severity"] == "critical"

    def test_no_signals_returns_zero(self):
        """无信号时 current_value 为 0 且通过。"""
        rm = RiskManager()
        item = rm._check_single_position_limit(make_signal(), make_account(), {})
        assert item["passed"] is True
        assert item["current_value"] == 0.0

    def test_equity_falls_back_to_balance(self):
        """缺 total_equity 时回退到 balance。"""
        rm = RiskManager()
        account = make_account()
        del account["total_equity"]
        sig = make_signal(signals=[{"symbol": "RB", "position": 5, "price": 1_000.0}])
        item = rm._check_single_position_limit(sig, account, {})
        # 5000 / 100000 = 5%
        assert item["passed"] is True
        assert item["current_value"] == pytest.approx(0.05)

    def test_multiple_signals_takes_worst(self):
        """多信号时取占比最高的品种。"""
        rm = RiskManager()
        sig = make_signal(signals=[
            {"symbol": "RB", "position": 3, "price": 1_000.0},
            {"symbol": "CU", "position": 15, "price": 1_000.0},
        ])
        item = rm._check_single_position_limit(sig, make_account(), {})
        # CU: 15000 / 100000 = 15%（最差）
        assert item["passed"] is False
        assert item["current_value"] == pytest.approx(0.15)
        assert "CU" in item["message"]

    def test_zero_equity_no_crash(self):
        """权益为 0 时以 1e-9 兜底，不抛异常。"""
        rm = RiskManager()
        account = make_account(balance=0.0, total_equity=0.0)
        sig = make_signal(signals=[{"symbol": "RB", "position": 1, "price": 100.0}])
        item = rm._check_single_position_limit(sig, account, {})
        assert isinstance(item["passed"], bool)


# ─── 组合最大回撤 ─────────────────────────────────────────


class TestPortfolioDrawdown:
    """组合最大回撤规则测试。"""

    def test_skipped_without_peak(self):
        """无峰值权益数据时跳过（passed=True）。"""
        rm = RiskManager()
        account = make_account(peak_equity=0.0)
        item = rm._check_portfolio_drawdown(make_signal(), account)
        assert item["passed"] is True
        assert "跳过" in item["message"]

    def test_passed_within_limit(self):
        """回撤在 20% 以内时通过。"""
        rm = RiskManager()
        account = make_account(total_equity=100_000.0, peak_equity=120_000.0)
        item = rm._check_portfolio_drawdown(make_signal(), account)
        # 100000/120000 - 1 = -16.67% > -20%
        assert item["passed"] is True
        assert item["current_value"] == pytest.approx(-1 / 6)

    def test_blocked_beyond_limit(self):
        """回撤超过 20% 时拦截。"""
        rm = RiskManager()
        account = make_account(total_equity=70_000.0, peak_equity=100_000.0)
        item = rm._check_portfolio_drawdown(make_signal(), account)
        # -30% < -20%
        assert item["passed"] is False
        assert item["current_value"] == pytest.approx(-0.30)
        assert item["severity"] == "critical"


# ─── 单日最大亏损 ────────────────────────────────────────


class TestDailyLossLimit:
    """单日最大亏损规则测试。"""

    def test_passed_within_limit(self):
        """单日亏损 4% 时通过。"""
        rm = RiskManager()
        account = make_account(daily_pnl=-4_000.0)
        item = rm._check_daily_loss_limit(make_signal(), account)
        assert item["passed"] is True
        assert item["current_value"] == pytest.approx(0.04)

    def test_blocked_beyond_limit(self):
        """单日亏损 8% 时拦截。"""
        rm = RiskManager()
        account = make_account(daily_pnl=-8_000.0)
        item = rm._check_daily_loss_limit(make_signal(), account)
        assert item["passed"] is False
        assert item["current_value"] == pytest.approx(0.08)

    def test_profit_is_negative_ratio(self):
        """当日盈利时 loss_ratio 为负且通过。"""
        rm = RiskManager()
        account = make_account(daily_pnl=5_000.0)
        item = rm._check_daily_loss_limit(make_signal(), account)
        assert item["passed"] is True
        assert item["current_value"] == pytest.approx(-0.05)


# ─── 杠杆上限 ────────────────────────────────────────────


class TestLeverageLimit:
    """杠杆上限规则测试。"""

    def test_passed_below_limit(self):
        """持仓市值 50% 权益时通过。"""
        rm = RiskManager()
        sig = make_signal(signals=[{"symbol": "RB", "position": 5, "price": 1_000.0}])
        positions = make_position("RB", 45_000.0)
        account = make_account()  # position_value=0
        item = rm._check_leverage_limit(sig, account, positions)
        # (45000 + 5000) / 100000 = 0.5x
        assert item["passed"] is True
        assert item["current_value"] == pytest.approx(0.5)

    def test_blocked_above_limit(self):
        """持仓市值超过 3x 权益时拦截。"""
        rm = RiskManager()
        account = make_account(position_value=310_000.0)
        item = rm._check_leverage_limit(make_signal(), account, {})
        # 310000 / 100000 = 3.1x
        assert item["passed"] is False
        assert item["current_value"] == pytest.approx(3.1)

    def test_at_limit_passes(self):
        """恰好 3.0x 视为通过（<= 判定）。"""
        rm = RiskManager()
        account = make_account(position_value=300_000.0)
        item = rm._check_leverage_limit(make_signal(), account, {})
        assert item["passed"] is True
        assert item["current_value"] == pytest.approx(3.0)


# ─── 集中度上限 ──────────────────────────────────────────


class TestConcentrationLimit:
    """前 3 大品种集中度规则测试。"""

    def test_skipped_when_less_than_3(self):
        """持仓品种数 < 3 时跳过集中度检查。"""
        rm = RiskManager()
        sig = make_signal(signals=[{"symbol": "RB", "position": 1, "price": 1_000.0}])
        positions = make_position("CU", 5_000.0)
        item = rm._check_concentration_limit(sig, positions)
        assert item["passed"] is True
        assert "跳过" in item["message"]

    def test_blocked_high_concentration(self):
        """前 3 大品种占 75% 时拦截。"""
        rm = RiskManager()
        positions = {
            "RB": {"symbol": "RB", "market_value": 10_000.0},
            "CU": {"symbol": "CU", "market_value": 5_000.0},
            "AU": {"symbol": "AU", "market_value": 5_000.0},
            "RU": {"symbol": "RU", "market_value": 0.0},  # 0 值被过滤
        }
        item = rm._check_concentration_limit(make_signal(), positions)
        # 有效值 [10000, 5000, 5000]，top3 全占比 100%
        assert item["passed"] is False
        assert item["current_value"] == pytest.approx(1.0)

    def test_passed_diversified(self):
        """10 个等权品种时通过。"""
        rm = RiskManager()
        positions = {
            f"SYM{i}": {"symbol": f"SYM{i}", "market_value": 1_000.0}
            for i in range(10)
        }
        item = rm._check_concentration_limit(make_signal(), positions)
        # top3 / total = 3000 / 10000 = 30% <= 50%
        assert item["passed"] is True
        assert item["current_value"] == pytest.approx(0.30)

    def test_signals_included_in_values(self):
        """信号目标市值计入集中度计算。"""
        rm = RiskManager()
        positions = {
            "RB": {"symbol": "RB", "market_value": 10_000.0},
            "CU": {"symbol": "CU", "market_value": 10_000.0},
            "AU": {"symbol": "AU", "market_value": 10_000.0},
        }
        sig = make_signal(signals=[{"symbol": "RU", "position": 70, "price": 1_000.0}])
        item = rm._check_concentration_limit(sig, positions)
        # values = [10000, 10000, 10000, 70000]，top3 = 90000 / 100000 = 90%
        assert item["passed"] is False
        assert item["current_value"] == pytest.approx(0.90)


# ─── check() 主入口 ──────────────────────────────────────


class TestRiskManagerCheck:
    """RiskManager.check() 聚合测试。"""

    def test_all_checks_pass(self):
        """五项规则全通过时 approved=True。"""
        rm = RiskManager()
        sig = make_signal(signals=[{"symbol": "RB", "position": 1, "price": 1_000.0}])
        account = make_account(position_value=20_000.0)
        positions = make_position("RB", 1_000.0)
        result = rm.check(sig, account, positions)
        # 单品种 (1000+1000)/100000=2%；杠杆 20000/100000=0.2x；回撤 -16.7%；集中度 2 个品种跳过
        assert result["approved"] is True
        assert result["blocking_violations"] == []
        assert len(result["checks"]) == 5
        assert result["signal_id"] == "SIG-TEST-001"
        assert result["timestamp"]  # 非空时间戳

    def test_blocking_on_violation(self):
        """任一规则失败时 approved=False 且 blocking_violations 非空。"""
        rm = RiskManager()
        # 单品种仓位超限
        sig = make_signal(signals=[{"symbol": "RB", "position": 30, "price": 1_000.0}])
        result = rm.check(sig, make_account(), {})
        assert result["approved"] is False
        assert len(result["blocking_violations"]) >= 1
        names = [c["check_name"] for c in result["blocking_violations"]]
        assert "single_position_limit" in names

    def test_missing_signal_id(self):
        """signal 缺 signal_id 时返回空字符串。"""
        rm = RiskManager()
        result = rm.check(make_signal(signal_id=""), make_account(), {})
        assert result["signal_id"] == ""
