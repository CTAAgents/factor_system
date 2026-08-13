"""G14 杠杆/止损止盈参数随 Regime 变化测试（plans/35 §5.7，v2.103.0+15）。

覆盖：
- resolve_risk_params：high_vol 杠杆 1.0 生效；未知/无 regime 回退常量
- 切换平滑：提供 prev 时指数平滑防跳变
- RiskManager 注入：regime 参数注入 max_leverage / daily_loss_limit_pct
- MhfRiskConfig.from_regime：止损/日内止损参数化
"""

from __future__ import annotations

from fts.factor_engine.regime_multipliers import REGIME_RISK_PARAMS, resolve_risk_params
from fts.live_trade.paper_trader_mhf import MhfRiskConfig
from fts.risk.risk_manager import RiskManager

_BASE = {"leverage_cap": 3.0, "stop_loss_pct": 0.012, "daily_loss_pct": 0.020}


class TestResolveRiskParams:
    """regime_multipliers.resolve_risk_params 表解析与回退。"""

    def test_high_vol_leverage_cap_applied(self):
        params = resolve_risk_params("high_vol", dict(_BASE))
        assert params["leverage_cap"] == 1.0
        assert params["stop_loss_pct"] == 0.008
        assert params["daily_loss_pct"] == 0.010

    def test_bear_tightens_params(self):
        params = resolve_risk_params("bear", dict(_BASE))
        assert params["leverage_cap"] == 1.5
        assert params["stop_loss_pct"] == 0.010

    def test_unknown_regime_fallback(self):
        params = resolve_risk_params("weird", dict(_BASE))
        assert params == _BASE

    def test_none_regime_fallback_no_mutation(self):
        base = dict(_BASE)
        params = resolve_risk_params(None, base)
        assert params == _BASE
        assert base == _BASE  # 入参不被修改

    def test_table_has_five_regimes(self):
        assert set(REGIME_RISK_PARAMS.keys()) == {"bull", "bear", "oscillate", "high_vol", "low_vol"}
        for params in REGIME_RISK_PARAMS.values():
            assert set(params.keys()) == {"leverage_cap", "stop_loss_pct", "daily_loss_pct"}


class TestSmoothing:
    """Regime 切换平滑：prev 提供时无跳变。"""

    def test_smooth_between_regimes(self):
        prev = resolve_risk_params("bull", dict(_BASE))  # leverage_cap 2.5
        new = resolve_risk_params("high_vol", dict(_BASE))  # leverage_cap 1.0
        # 过渡期平滑（α=0.3）：0.3×1.0 + 0.7×2.5 = 2.05
        blended = resolve_risk_params("high_vol", dict(_BASE), prev=prev, alpha=0.3)
        assert abs(blended["leverage_cap"] - 2.05) < 1e-9
        assert blended["leverage_cap"] != new["leverage_cap"]  # 未直接跳到 1.0

    def test_no_prev_returns_new(self):
        new = resolve_risk_params("high_vol", dict(_BASE), prev=None)
        assert new["leverage_cap"] == 1.0


class TestRiskManagerRegime:
    """RiskManager 初始化注入（不改 check() 内部逻辑）。"""

    def test_high_vol_injected(self):
        rm = RiskManager(regime="high_vol")
        assert rm._config["max_leverage"] == 1.0
        assert rm._config["daily_loss_limit_pct"] == 0.010

    def test_no_regime_uses_default(self):
        rm = RiskManager()
        assert rm._config["max_leverage"] == 3.0
        assert rm._config["daily_loss_limit_pct"] == 0.05

    def test_regime_overrides_explicit_config(self):
        """Regime 注入优先级高于显式配置（风控优先：high_vol 强制降杠杆）。"""
        rm = RiskManager(config={"max_leverage": 5.0}, regime="high_vol")
        assert rm._config["max_leverage"] == 1.0

    def test_check_still_works_with_regime(self):
        rm = RiskManager(regime="high_vol")
        result = rm.check(
            {"signal_id": "s1", "signals": []},
            {"total_equity": 100000.0, "position_value": 50000.0, "daily_pnl": 0.0},
            {},
        )
        assert result["approved"] is True


class TestMhfConfigFromRegime:
    """MhfRiskConfig.from_regime 止损参数参数化。"""

    def test_bear_overrides_stop_loss(self):
        cfg = MhfRiskConfig.from_regime("bear")
        assert cfg.stop_loss_pct == 0.010
        assert cfg.daily_loss_pct == 0.015

    def test_unknown_regime_keeps_default(self):
        cfg = MhfRiskConfig.from_regime("unknown_regime")
        assert cfg.stop_loss_pct == 0.012
        assert cfg.daily_loss_pct == 0.015

    def test_base_fields_preserved(self):
        base = MhfRiskConfig(max_positions=12, target_pct=0.1)
        cfg = MhfRiskConfig.from_regime("high_vol", base=base)
        assert cfg.max_positions == 12
        assert cfg.target_pct == 0.1
        assert cfg.stop_loss_pct == 0.008
