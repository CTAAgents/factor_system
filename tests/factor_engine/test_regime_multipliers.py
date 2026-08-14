"""
tests/factor_engine/test_regime_multipliers.py — Regime 风控参数测试（G14）。

覆盖:
    - REGIME_RISK_PARAMS 表结构完整（五类 regime，字段齐全）
    - resolve_risk_params 已知 regime 覆盖基础参数
    - resolve_risk_params None/未知 regime 回退 base（不修改入参）
    - resolve_risk_params 指数平滑（prev + alpha）
"""

from __future__ import annotations

import pytest

from fts.factor_engine.regime_multipliers import (
    REGIME_RISK_PARAMS,
    RiskParams,
    resolve_risk_params,
)


class TestRegimeRiskParams:
    """REGIME_RISK_PARAMS 表结构。"""

    def test_table_contains_all_regimes(self) -> None:
        """五类 regime 齐全且字段完整。"""
        assert set(REGIME_RISK_PARAMS) == {"bull", "bear", "oscillate", "high_vol", "low_vol"}
        for params in REGIME_RISK_PARAMS.values():
            assert {"leverage_cap", "stop_loss_pct", "daily_loss_pct"} <= set(params)

    def test_bear_more_restrictive_than_bull(self) -> None:
        """风险制度（bear）杠杆上限/止损/单日亏损均严于 bull。"""
        bull = REGIME_RISK_PARAMS["bull"]
        bear = REGIME_RISK_PARAMS["bear"]
        assert bear["leverage_cap"] < bull["leverage_cap"]
        assert bear["stop_loss_pct"] < bull["stop_loss_pct"]
        assert bear["daily_loss_pct"] < bull["daily_loss_pct"]


class TestResolveRiskParams:
    """resolve_risk_params 解析逻辑。"""

    def test_known_regime_overrides_base(self) -> None:
        """已知 regime → 对应字段被覆盖，其余字段保持 base。"""
        base: RiskParams = {"leverage_cap": 3.0, "stop_loss_pct": 0.02, "daily_loss_pct": 0.03}
        out = resolve_risk_params("bear", base)
        assert out == {"leverage_cap": 1.5, "stop_loss_pct": 0.010, "daily_loss_pct": 0.015}

    def test_unknown_or_none_regime_falls_back(self) -> None:
        """None / 未知 regime → 原样返回 base 且不修改入参。"""
        base: RiskParams = {"leverage_cap": 3.0, "stop_loss_pct": 0.02, "daily_loss_pct": 0.03}
        for regime in (None, "unknown_regime"):
            out = resolve_risk_params(regime, base)
            assert out == base
        assert base == {"leverage_cap": 3.0, "stop_loss_pct": 0.02, "daily_loss_pct": 0.03}  # 未污染

    def test_smoothing_with_prev(self) -> None:
        """提供 prev → 字段按 alpha×new + (1-alpha)×prev 平滑。"""
        base: RiskParams = {"leverage_cap": 3.0, "stop_loss_pct": 0.02, "daily_loss_pct": 0.03}
        prev: RiskParams = {"leverage_cap": 2.0, "stop_loss_pct": 0.02, "daily_loss_pct": 0.03}
        out = resolve_risk_params("bear", base, prev=prev, alpha=0.5)
        # 1.5×0.5 + 2.0×0.5 = 1.75
        assert out["leverage_cap"] == pytest.approx(1.75, abs=1e-9)
        # 0.010×0.5 + 0.02×0.5 = 0.015
        assert out["stop_loss_pct"] == pytest.approx(0.015, abs=1e-9)

    def test_smoothing_disabled_with_alpha_zero(self) -> None:
        """alpha=0 → 不平滑，直接取 regime 覆盖值。"""
        base: RiskParams = {"leverage_cap": 3.0, "stop_loss_pct": 0.02, "daily_loss_pct": 0.03}
        prev: RiskParams = {"leverage_cap": 2.0, "stop_loss_pct": 0.02, "daily_loss_pct": 0.03}
        out = resolve_risk_params("bear", base, prev=prev, alpha=0.0)
        assert out["leverage_cap"] == 1.5
