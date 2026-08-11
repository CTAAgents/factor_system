"""tests/factor_engine/test_portfolio_risk_controls.py — 组合级风控测试（GAP-067）。

覆盖: 回撤止损 / 相关性熔断 / 综合检查 / 边界降级。
HARNESS §测试随重构。
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from fts.factor_engine.portfolio_risk_controls import (
    check_correlation_circuit_breaker,
    check_drawdown_stop,
    run_portfolio_risk_controls,
    DEFAULT_CORR_THRESHOLD,
)


# ─── 回撤止损 ─────────────────────────────────────────────


def test_drawdown_stop_triggers_on_decline():
    """持续亏损序列回撤超阈值触发。"""
    returns = np.full(100, -0.002)  # 累计 -18%
    res = check_drawdown_stop(returns, threshold=0.10)
    assert res["triggered"] is True
    assert res["max_drawdown"] > 0.10


def test_drawdown_stop_not_triggered_on_rise():
    """稳步上升序列不触发。"""
    returns = np.full(100, 0.002)
    res = check_drawdown_stop(returns, threshold=0.10)
    assert res["triggered"] is False


def test_drawdown_threshold_configurable():
    """阈值可配置：大幅回撤在宽松阈值下不触发。"""
    returns = np.concatenate([np.full(50, 0.001), np.full(50, -0.004)])  # 峰值后 -18%
    assert check_drawdown_stop(returns, threshold=0.30)["triggered"] is False
    assert check_drawdown_stop(returns, threshold=0.10)["triggered"] is True


# ─── 相关性熔断 ───────────────────────────────────────────


def _member_returns(correlated: bool, seed: int = 8) -> pd.DataFrame:
    """构造成员收益面板：强相关（共同驱动）或独立。"""
    rng = np.random.default_rng(seed)
    n = 120
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    if correlated:
        common = rng.normal(0, 0.02, n)
        return pd.DataFrame(
            {"a": common + rng.normal(0, 0.002, n), "b": common + rng.normal(0, 0.002, n)},
            index=idx,
        )
    return pd.DataFrame(
        {"a": rng.normal(0, 0.02, n), "b": rng.normal(0, 0.02, n), "c": rng.normal(0, 0.02, n)},
        index=idx,
    )


def test_corr_breaker_triggers_on_crisis():
    """危机模式（成员高度联动）触发熔断。"""
    res = check_correlation_circuit_breaker(_member_returns(correlated=True))
    assert res["triggered"] is True
    assert res["mean_corr"] > DEFAULT_CORR_THRESHOLD


def test_corr_breaker_not_triggered_independent():
    """独立成员不触发熔断。"""
    res = check_correlation_circuit_breaker(_member_returns(correlated=False))
    assert res["triggered"] is False


def test_corr_breaker_short_window_no_trigger():
    """窗口样本不足不触发。"""
    returns = _member_returns(correlated=True).tail(3)
    res = check_correlation_circuit_breaker(returns)
    assert res["triggered"] is False


# ─── 综合检查 ─────────────────────────────────────────────


def test_run_risk_controls_both_alerts():
    """同时触发回撤止损与相关性熔断，notes 非空。"""
    returns = np.concatenate([np.full(60, 0.001), np.full(60, -0.003)])  # 峰值后约 -16%
    members = _member_returns(correlated=True)
    alert = run_portfolio_risk_controls(returns, members, drawdown_threshold=0.10, corr_threshold=0.8)
    assert alert.drawdown_stop is True
    assert alert.correlation_breaker is True
    assert len(alert.notes) == 2


def test_run_risk_controls_no_alerts():
    """正常组合无告警。"""
    returns = np.full(120, 0.002)
    members = _member_returns(correlated=False)
    alert = run_portfolio_risk_controls(returns, members)
    assert alert.drawdown_stop is False
    assert alert.correlation_breaker is False
    assert alert.notes == []


def test_run_risk_controls_none_inputs_no_crash():
    """combo_returns/member_returns 为 None 时不崩溃且不触发。"""
    alert = run_portfolio_risk_controls(None, None)
    assert alert.drawdown_stop is False
    assert alert.correlation_breaker is False


def test_to_dict_serializable():
    """to_dict 可 JSON 序列化。"""
    returns = np.concatenate([np.full(60, 0.001), np.full(60, -0.003)])
    alert = run_portfolio_risk_controls(returns, _member_returns(correlated=True))
    json.dumps(alert.to_dict())
