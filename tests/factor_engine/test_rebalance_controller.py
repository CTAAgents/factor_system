"""test_rebalance_controller — 五层调仓控制器单元测试（手册阶段7）。"""

from __future__ import annotations

import numpy as np
import pytest

from fts.factor_engine.rebalance_controller import (
    RebalanceConfig,
    RebalanceController,
    auto_buffer_k,
    plan_turnover_control,
)


def _scores(m: int, seed: int = 1) -> dict[str, float]:
    """构造 M 个品种得分：分数从 10 单调递减到 10-m+1。"""
    return {f"S{i}": float(10 - i) for i in range(m)}


def _scores_bottom(m: int) -> dict[str, float]:
    """构造 M 个品种得分：最后一个品种分数极低（进入核心空头）。"""
    return {f"S{i}": float(10 - i) for i in range(m - 1)} | {f"S{m - 1}": -99.0}


# ─── 第一层：缓冲带 ───────────────────────────────────────


def test_auto_buffer_k_three_tiers() -> None:
    """缓冲带宽度 k 按品种池规模差异化（M<30 → 2；30≤M<60 → max(2,round(N*0.25))；M≥60 → round(N*0.3)）。"""
    assert auto_buffer_k(pool_size=20, n=8) == 2
    assert auto_buffer_k(pool_size=40, n=8) == max(2, round(8 * 0.25))
    assert auto_buffer_k(pool_size=60, n=8) == round(8 * 0.3)
    assert auto_buffer_k(pool_size=100, n=10) == round(10 * 0.3)


def test_build_zone_partition() -> None:
    """区域划分：核心多头 N 个 / 多头缓冲 k 个 / 不持仓 / 空头缓冲 k 个 / 核心空头 N 个。"""
    m, n, k = 20, 2, 2
    cfg = RebalanceConfig(n_long=n, n_short=n, buffer_k=k, pool_size=m)
    ctrl = RebalanceController(cfg)
    zone = ctrl.build_zone(_scores(m))
    assert zone["S0"] == "long_core"
    assert zone["S1"] == "long_core"
    assert zone["S2"] == "long_buffer"
    assert zone["S3"] == "long_buffer"
    assert zone["S10"] == "flat"
    assert zone["S16"] == "short_buffer"
    assert zone["S17"] == "short_buffer"
    assert zone["S18"] == "short_core"
    assert zone["S19"] == "short_core"


def test_zone_to_target_buffer_hold() -> None:
    """缓冲区：已持有同方向则继续持有，未持有则不新开。"""
    assert RebalanceController.zone_to_target("long_core", 0) == 1
    assert RebalanceController.zone_to_target("short_core", 0) == -1
    assert RebalanceController.zone_to_target("long_buffer", 1) == 1
    assert RebalanceController.zone_to_target("long_buffer", 0) == 0
    assert RebalanceController.zone_to_target("short_buffer", -1) == -1
    assert RebalanceController.zone_to_target("short_buffer", 0) == 0
    assert RebalanceController.zone_to_target("flat", 1) == 0


def test_build_zone_nan_dropout() -> None:
    """NaN 得分品种不参与排名（视为不持仓区）。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10)
    ctrl = RebalanceController(cfg)
    scores = _scores(10)
    scores["S0"] = np.nan
    zone = ctrl.build_zone(scores)
    assert "S0" not in zone  # NaN 品种被剔除
    assert zone["S1"] == "long_core"  # 排名顺移


# ─── 第二层：混合触发 ─────────────────────────────────────


def test_evaluate_trigger_periodic() -> None:
    """周期性再平衡：距上次完整再平衡 ≥ T 触发。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, rebalance_period=3)
    ctrl = RebalanceController(cfg)
    ctrl.state.last_rebalance_days_ago = cfg.rebalance_period  # 已到期
    trigger = ctrl.evaluate_trigger({"S0": 1}, {"S0": 1}, event=None)
    assert trigger == "periodic"


def test_evaluate_trigger_boundary() -> None:
    """边界突破：持仓品种跌出缓冲区或非持仓进入核心区 → 触发。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, rebalance_period=3)
    ctrl = RebalanceController(cfg)
    trigger = ctrl.evaluate_trigger({"S0": 1}, {"S0": 1, "S5": 1}, event=None)
    assert trigger == "boundary"
    # 无差异 → 不触发
    assert ctrl.evaluate_trigger({"S0": 1}, {"S0": 1}, event=None) is None


def test_evaluate_trigger_forced() -> None:
    """强制再平衡：距上次强制全量对齐 ≥ force_rebalance_period 触发。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, force_rebalance_period=20)
    ctrl = RebalanceController(cfg)
    ctrl.state.last_forced_days_ago = 20
    trigger = ctrl.evaluate_trigger({}, {}, event=None)
    assert trigger == "forced"


def test_evaluate_trigger_external_event() -> None:
    """风控/交割月移仓事件立即触发。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10)
    ctrl = RebalanceController(cfg)
    assert ctrl.evaluate_trigger({}, {}, event="risk") == "risk"
    assert ctrl.evaluate_trigger({}, {}, event="rollover") == "rollover"


# ─── 第三层：换手阈值拦截 ─────────────────────────────────


def test_cost_benefit_gate_pass_high_score() -> None:
    """高得分变化：预期收益 > 成本×λ → 放行。"""
    cfg = RebalanceConfig(n_long=2, n_short=2)
    ctrl = RebalanceController(cfg)
    current = {"A": 1, "B": -1}
    target = {"A": 1, "C": 1}  # B 平仓、C 新开
    execute, detail = ctrl.cost_benefit_gate(current, target, {"A": 10.0, "B": 10.0, "C": 10.0})
    assert execute is True
    assert detail["benefit"] > detail["cost"] * cfg.cost_lambda


def test_cost_benefit_gate_intercept_low_score() -> None:
    """低得分变化：预期收益 ≤ 成本×λ → 拦截。"""
    cfg = RebalanceConfig(n_long=2, n_short=2)
    ctrl = RebalanceController(cfg)
    current = {"A": 1, "B": -1}
    target = {"A": 1, "C": 1}
    execute, detail = ctrl.cost_benefit_gate(current, target, {"A": 1e-4, "B": 1e-4, "C": 1e-4})
    assert execute is False
    assert detail["benefit"] <= detail["cost"] * cfg.cost_lambda


def test_cost_benefit_gate_no_change() -> None:
    """无持仓变化：直接放行且成本明细为空。"""
    cfg = RebalanceConfig(n_long=2, n_short=2)
    ctrl = RebalanceController(cfg)
    execute, detail = ctrl.cost_benefit_gate({"A": 1}, {"A": 1}, {"A": 10.0})
    assert execute is True
    assert detail == {}


# ─── 第四层：防僵尸 ───────────────────────────────────────


def test_zombie_guard_forced_flat() -> None:
    """持仓超期且已不在核心/缓冲区 → 强制平仓。"""
    cfg = RebalanceConfig(max_hold_days=20)
    ctrl = RebalanceController(cfg)
    ctrl.state.hold_days = {"A": 25}
    ctrl.state.positions = {"A": 1}
    target, forced = ctrl.zombie_guard({"A": 1}, {"A": "flat"})
    assert forced == ["A"]
    assert target["A"] == 0


def test_zombie_guard_keep_core() -> None:
    """持仓超期但仍在核心区 → 保留（因子仍有效）。"""
    cfg = RebalanceConfig(max_hold_days=20)
    ctrl = RebalanceController(cfg)
    ctrl.state.hold_days = {"A": 25}
    ctrl.state.positions = {"A": 1}
    target, forced = ctrl.zombie_guard({"A": 1}, {"A": "long_core"})
    assert forced == []
    assert target["A"] == 1


# ─── 第五层：分批执行 ─────────────────────────────────────


def test_plan_batches_split() -> None:
    """调仓品种数 ≥ 阈值 → 拆分为多批。"""
    cfg = RebalanceConfig(batch_threshold=5, batch_count=3)
    ctrl = RebalanceController(cfg)
    changes = {f"S{i}": (0, 1) for i in range(8)}
    batches = ctrl.plan_batches(changes)
    assert len(batches) == 3
    assert sum(len(b) for b in batches) == 8


def test_plan_batches_single() -> None:
    """调仓品种数不足阈值 → 单批。"""
    cfg = RebalanceConfig(batch_threshold=5, batch_count=3)
    ctrl = RebalanceController(cfg)
    batches = ctrl.plan_batches({"A": (0, 1), "B": (0, -1), "C": (1, 0)})
    assert len(batches) == 1
    assert len(batches[0]) == 3


# ─── 主入口全流程 ─────────────────────────────────────────


def test_step_initial_entries_and_state_reset() -> None:
    """首日无持仓 → 边界突破触发建仓，执行后状态重置。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10)
    ctrl = RebalanceController(cfg)
    decision = ctrl.step(_scores(10))
    assert decision.triggered is True
    assert decision.trigger == "boundary"
    assert decision.target_positions["S0"] == 1
    assert decision.target_positions["S9"] == -1
    assert ctrl.state.positions == {"S0": 1, "S1": 1, "S8": -1, "S9": -1}
    assert ctrl.state.last_rebalance_days_ago == 0


def test_step_noop_when_no_change() -> None:
    """次日分数不变且未到周期 → 维持持仓不触发。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, rebalance_period=3)
    ctrl = RebalanceController(cfg)
    scores = _scores(10)
    ctrl.step(scores)  # 首日建仓
    decision = ctrl.step(scores)  # 次日无变化
    assert decision.triggered is False
    assert ctrl.state.last_rebalance_days_ago == 1  # 周期计数继续累计


def test_step_hold_days_accumulate_when_noop() -> None:
    """未调仓日持仓天数递增。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, rebalance_period=3)
    ctrl = RebalanceController(cfg)
    scores = _scores(10)
    ctrl.step(scores)
    ctrl.step(scores)
    assert ctrl.state.hold_days["S0"] == 2


def test_step_cost_intercepted_keeps_position(monkeypatch: pytest.MonkeyPatch) -> None:
    """触发调仓但收益不足以覆盖成本 → 拦截，持仓不变。

    真实成本收益公式由 test_cost_benefit_gate_* 独立覆盖；
    此处 monkeypatch 门控返回「拦截」验证 step 流程正确保持持仓并记录日志。
    """
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, rebalance_period=3)
    ctrl = RebalanceController(cfg)
    ctrl.step(_scores(10))  # 首日建仓 S0/S1 多，S8/S9 空
    scores = _scores(10)
    scores["S0"] = -0.0001  # S0 分数暴跌进入核心空头（方向反转）
    monkeypatch.setattr(
        ctrl,
        "cost_benefit_gate",
        lambda c, t, s: (
            False,
            {"benefit": 0.0, "cost": 1.0, "delta_weight": {"S0": 1.0}, "unit_cost": 0.001, "lambda": cfg.cost_lambda},
        ),
    )
    decision = ctrl.step(scores)
    assert decision.triggered is True
    assert decision.trigger == "boundary"
    assert decision.cost_intercepted is True
    assert ctrl.state.positions["S0"] == 1  # 持仓保持（未平仓）
    assert decision.logs and "成本拦截" in decision.logs[0]


def test_step_periodic_rebalance_executes() -> None:
    """周期到期 → 执行再平衡并重置周期计数。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, rebalance_period=3)
    ctrl = RebalanceController(cfg)
    scores = _scores(10)
    ctrl.step(scores)
    ctrl.step(scores)
    ctrl.step(scores)
    # 三日后 last_rebalance_days_ago=3 ≥ 3 → periodic
    decision = ctrl.step(scores)
    assert decision.triggered is True
    assert decision.trigger == "periodic"
    assert ctrl.state.last_rebalance_days_ago == 0


def test_step_forced_rebalance_ignores_cost_gate() -> None:
    """强制再平衡绕过成本拦截。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, force_rebalance_period=20)
    ctrl = RebalanceController(cfg)
    ctrl.state.last_forced_days_ago = 19  # step 内 +1 → 20 触发 forced
    ctrl.state.positions = {"S0": 1, "S1": 1, "S8": -1, "S9": -1}  # 已有持仓
    decision = ctrl.step(_scores(10))
    assert decision.trigger == "forced"
    assert decision.cost_intercepted is False
    assert ctrl.state.last_forced_days_ago == 0


def test_step_zombie_forced_flat() -> None:
    """防僵尸：持仓超期且失效 → step 中强制平仓。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, max_hold_days=20)
    ctrl = RebalanceController(cfg)
    ctrl.state.positions = {"S0": 1}
    ctrl.state.hold_days = {"S0": 30}
    ctrl.state.last_rebalance_days_ago = 1  # 无周期触发，依赖边界/僵尸
    decision = ctrl.step(_scores(10))  # S0 仍在核心多头
    assert "S0" not in decision.zombie_forced  # 在核心区 → 保留


def test_step_decision_to_dict() -> None:
    """决策可序列化。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10)
    ctrl = RebalanceController(cfg)
    decision = ctrl.step(_scores(10))
    d = decision.to_dict()
    assert d["triggered"] is True
    assert isinstance(d["target_positions"], dict)
    assert isinstance(d["batches"], list)


# ─── 多空不对称缓冲（CTA 手册阶段7 可选） ────────────────


def test_build_zone_asymmetric_trend() -> None:
    """趋势 Regime：多头缓冲 k+1、空头缓冲 k-1。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=2, pool_size=20, asymmetric_buffer=True)
    ctrl = RebalanceController(cfg)
    # 趋势：k_long=3, k_short=1
    zone = ctrl.build_zone(_scores(20), k_long=3, k_short=1)
    assert zone["S4"] == "long_buffer"  # rank5 ≤ n_long+k_long(5)
    assert zone["S5"] == "flat"  # rank6 超出多头缓冲
    assert zone["S17"] == "short_buffer"  # rank18 = m-n_short(18) 空头缓冲起点
    assert zone["S18"] == "short_core"


def test_step_asymmetric_buffer_trend() -> None:
    """step 传入 regime="trend" 且启用 asymmetric → 不对称缓冲生效。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10, asymmetric_buffer=True)
    ctrl = RebalanceController(cfg)
    # 趋势：k_long=2, k_short=0
    decision = ctrl.step(_scores(10), regime="trend")
    # S2 (rank3) 在多头缓冲内 → 已持则持（当前无持仓 → 0）
    assert decision.target_positions["S2"] == 0
    assert decision.target_positions["S0"] == 1
    assert decision.target_positions["S9"] == -1


def test_step_asymmetric_disabled_default_symmetric() -> None:
    """未启用 asymmetric 或 regime 为空 → 对称缓冲。"""
    cfg = RebalanceConfig(n_long=2, n_short=2, buffer_k=1, pool_size=10)
    ctrl = RebalanceController(cfg)
    decision = ctrl.step(_scores(10), regime="trend")
    assert decision.target_positions["S0"] == 1  # 行为与默认一致


# ─── 换手率超限自动控制（CTA 手册阶段7） ────────────────


def test_turnover_control_within_target() -> None:
    """换手率未超限 → 保持基准。"""
    r = plan_turnover_control(40.0, turnover_target=50.0)
    assert r.action == "ok"
    assert r.buffer_k == 2 and r.rebalance_period == 3


def test_turnover_control_widen_buffer() -> None:
    """1~1.5 倍目标 → 扩大缓冲带。"""
    r = plan_turnover_control(60.0)
    assert r.action == "widen_buffer"
    assert r.buffer_k == 3 and r.rebalance_period == 3


def test_turnover_control_extend_period() -> None:
    """1.5~2 倍目标 → 扩大缓冲带 + 延长周期。"""
    r = plan_turnover_control(90.0)
    assert r.action == "both"
    assert r.buffer_k == 4 and r.rebalance_period == 4


def test_turnover_control_aggressive() -> None:
    """>2 倍目标 → 激进调整。"""
    r = plan_turnover_control(150.0)
    assert r.action == "both_aggressive"
    assert r.buffer_k == 5 and r.rebalance_period == 5


def test_turnover_control_zero_target_safe() -> None:
    """目标为 0 → 除零保护。"""
    r = plan_turnover_control(10.0, turnover_target=0.0)
    assert r.action == "both_aggressive"  # ratio 极大 → 激进档
