"""test_futures_risk_events — 期货特有风险场景事件处理测试（CTA 手册阶段8）。"""

from __future__ import annotations

import pytest

from fts.factor_engine.futures_risk_events import (
    circuit_breaker_block,
    margin_increase_reduce,
    margin_usage_reduce_position,
    roll_anomaly_delay,
)


# ─── 盘中保证金降仓 ───────────────────────────────────────


def test_margin_usage_within_threshold_no_reduce() -> None:
    """保证金占用 ≤70% → 不触发降仓。"""
    r = margin_usage_reduce_position(margin_used=50.0, equity=100.0)
    assert r["triggered"] is False
    assert r["margin_usage"] == pytest.approx(0.5)
    assert r["reduce_factor"] == pytest.approx(1.0)


def test_margin_usage_exceeds_threshold_reduce() -> None:
    """保证金占用 80% > 70% → 触发降仓至 70%。"""
    r = margin_usage_reduce_position(margin_used=80.0, equity=100.0)
    assert r["triggered"] is True
    assert r["reduce_to_ratio"] == pytest.approx(0.7)
    assert r["reduce_factor"] == pytest.approx(0.7 / 0.8)


def test_margin_usage_zero_equity_safe() -> None:
    """权益为 0 → 不崩溃。"""
    r = margin_usage_reduce_position(10.0, 0.0)
    assert r["triggered"] is False


# ─── 交易所提保限仓 ───────────────────────────────────────


def test_margin_increase_exceeded() -> None:
    """提保后占用超限 → 按比例减仓至合规。"""
    # 原占用 60%（12%→16% 保证金比例），提保后 80% > 70%
    r = margin_increase_reduce(margin_used=60.0, equity=100.0, old_margin_rate=0.12, new_margin_rate=0.16)
    assert r["exceeded"] is True
    assert r["new_usage"] == pytest.approx(0.8)
    assert r["reduce_factor"] == pytest.approx(0.7 / 0.8)


def test_margin_increase_within_limit() -> None:
    """提保后仍合规 → 无需减仓。"""
    r = margin_increase_reduce(margin_used=50.0, equity=100.0, old_margin_rate=0.10, new_margin_rate=0.12)
    assert r["exceeded"] is False
    assert r["reduce_factor"] == pytest.approx(1.0)


def test_margin_decrease_no_effect() -> None:
    """降保 → 占用下降，不触发。"""
    r = margin_increase_reduce(margin_used=60.0, equity=100.0, old_margin_rate=0.16, new_margin_rate=0.12)
    assert r["exceeded"] is False


# ─── 熔断 ─────────────────────────────────────────────────


def test_circuit_breaker_blocks_trading() -> None:
    """熔断中 → 不可交易、暂停新开仓、允许持有现有持仓。"""
    r = circuit_breaker_block(is_circuit_breaker=True)
    assert r["tradable"] is False
    assert r["pause_new"] is True
    assert r["hold_existing"] is True


def test_circuit_breaker_released() -> None:
    """熔断解除 → 恢复可交易。"""
    r = circuit_breaker_block(is_circuit_breaker=False)
    assert r["tradable"] is True
    assert r["pause_new"] is False


# ─── 主力切换异常 ─────────────────────────────────────────


def test_roll_normal_spread_no_delay() -> None:
    """换月价差正常（<3%）→ 不延迟。"""
    r = roll_anomaly_delay(0.01)
    assert r["anomalous"] is False
    assert r["delay_recommended"] is False
    assert r["force_roll"] is False


def test_roll_anomaly_delay_recommended() -> None:
    """价差异常（>3%）→ 建议延迟移仓。"""
    r = roll_anomaly_delay(0.05)
    assert r["anomalous"] is True
    assert r["delay_recommended"] is True
    assert r["force_roll"] is False
    assert r["next_wait_days"] == 1


def test_roll_anomaly_force_after_wait() -> None:
    """等待 5 日仍未收敛 → 强制移仓并标记异常成本。"""
    r = roll_anomaly_delay(0.05, wait_days=4)
    assert r["force_roll"] is True
    assert r["abnormal_cost"] is True
    assert r["delay_recommended"] is False


def test_roll_anomaly_custom_threshold() -> None:
    """自定义异常阈值。"""
    assert roll_anomaly_delay(0.02, anomaly_threshold=0.01)["anomalous"] is True
