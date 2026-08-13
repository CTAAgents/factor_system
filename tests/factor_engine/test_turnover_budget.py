"""tests/factor_engine/test_turnover_budget.py — 组合换手预算分配测试（G3，35-gap-closure-plan）。

覆盖: 未超限直通 / 超限剔除最弱信号 / drop_weakest=False 告警 / 禁用直通 / 空输入 / 归一化。
HARNESS §测试随重构。
"""

from __future__ import annotations

from fts.factor_engine.portfolio_turnover import TurnoverBudgetConfig, allocate_turnover_budget


def _single_side_turnover(target: dict[str, float], current: dict[str, float]) -> float:
    """单边换手率 = Σ|Δw| / 2。"""
    return sum(abs(target.get(s, 0.0) - current.get(s, 0.0)) for s in target) / 2.0


def test_budget_passthrough_within_cap():
    """换手 ≤ cap → 原样返回，不剔除。"""
    current = {"a": 0.5, "b": 0.5}
    target = {"a": 0.6, "b": 0.4}  # turnover = (0.1+0.1)/2 = 0.1 ≤ 0.3
    out = allocate_turnover_budget(target, current, {"a": 1.0, "b": 1.0})
    assert out == target


def test_budget_cuts_weakest_signal_when_over():
    """全换仓超限 → 剔除最弱（最低 sharpe）信号直至达标。"""
    current = {"a": 1.0, "b": 0.0, "c": 0.0}
    target = {"a": 0.33, "b": 0.33, "c": 0.34}  # turnover = (0.67+0.33+0.34)/2 ≈ 0.67 > 0.3
    scores = {"a": 2.0, "b": 1.5, "c": 0.5}  # c 最弱
    out = allocate_turnover_budget(target, current, scores, TurnoverBudgetConfig(daily_turnover_cap=0.30))
    assert _single_side_turnover(out, current) <= 0.30 + 1e-9
    # 最弱 c 被回退当前持仓（0）
    assert abs(out["c"] - 0.0) < 1e-9


def test_budget_keeps_strongest_signal():
    """最强信号保留（不被剔除）。"""
    current = {"a": 1.0, "b": 0.0, "c": 0.0}
    target = {"a": 0.33, "b": 0.33, "c": 0.34}
    scores = {"a": 3.0, "b": 2.0, "c": 1.0}
    out = allocate_turnover_budget(target, current, scores, TurnoverBudgetConfig(daily_turnover_cap=0.30))
    # a 最强：其目标未被回退
    assert out["a"] > 0.3


def test_budget_no_drop_when_disabled_flag():
    """drop_weakest=False → 告警不裁剪，原样返回。"""
    current = {"a": 1.0, "b": 0.0}
    target = {"a": 0.0, "b": 1.0}  # turnover = 1.0 > cap
    out = allocate_turnover_budget(
        target, current, {"a": 1.0, "b": 1.0}, TurnoverBudgetConfig(drop_weakest=False)
    )
    assert out == target


def test_budget_disabled_passthrough():
    """enabled=False → 原样返回。"""
    current = {"a": 1.0, "b": 0.0}
    target = {"a": 0.0, "b": 1.0}
    out = allocate_turnover_budget(target, current, {"a": 1.0, "b": 1.0}, TurnoverBudgetConfig(enabled=False))
    assert out == target


def test_budget_empty_no_crash():
    """空目标权重 → 不崩溃返回空。"""
    assert allocate_turnover_budget({}, {}, {}) == {}


def test_budget_renormalized_after_cut():
    """剔除后保留项重新归一化，权重和 = 1。"""
    current = {"a": 0.5, "b": 0.5, "c": 0.0}
    target = {"a": 0.4, "b": 0.4, "c": 0.2}  # turnover = (0.1+0.1+0.2)/2 = 0.2 ≤ 0.3 不触发
    scores = {"a": 1.0, "b": 1.0, "c": 0.1}
    out = allocate_turnover_budget(target, current, scores, TurnoverBudgetConfig(daily_turnover_cap=0.15))
    # c 被剔除回退 0，a/b 重归一化 → 和 = 1
    assert abs(sum(out.values()) - 1.0) < 1e-9


def test_budget_exact_cap_no_cut():
    """换手恰等于 cap → 不剔除（≤ 判定）。"""
    current = {"a": 0.5, "b": 0.5}
    target = {"a": 0.5 + 0.15, "b": 0.5 - 0.15}  # turnover = 0.15
    out = allocate_turnover_budget(target, current, {"a": 1.0, "b": 1.0}, TurnoverBudgetConfig(daily_turnover_cap=0.15))
    assert out == target


def test_budget_score_drives_cut_order():
    """剔除顺序由边际收益决定：score 越低越先被剔除。"""
    current = {"a": 0.5, "b": 0.5, "c": 0.0}
    target = {"a": 0.5, "b": 0.5, "c": 0.0}  # 初始 turnover=0
    # 构造高换手：全部翻转为逆向
    target = {"a": 0.0, "b": 0.0, "c": 1.0}
    scores = {"a": 5.0, "b": 0.2, "c": 0.1}
    out = allocate_turnover_budget(target, current, scores, TurnoverBudgetConfig(daily_turnover_cap=0.10))
    assert _single_side_turnover(out, current) <= 0.10 + 1e-9
    # 最弱 c 保持 0（不引入新换手），a 最强保留目标
    assert abs(out["c"] - 0.0) < 1e-9
    assert out["a"] > 0.0


def test_budget_deterministic():
    """同输入多次调用结果一致（无随机性）。"""
    current = {"a": 1.0, "b": 0.0, "c": 0.0}
    target = {"a": 0.33, "b": 0.33, "c": 0.34}
    scores = {"a": 2.0, "b": 1.5, "c": 0.5}
    cfg = TurnoverBudgetConfig(daily_turnover_cap=0.30)
    r1 = allocate_turnover_budget(target, current, scores, cfg)
    r2 = allocate_turnover_budget(target, current, scores, cfg)
    assert r1 == r2


def test_budget_returns_copy_not_mutation():
    """返回新 dict，不修改输入。"""
    current = {"a": 1.0, "b": 0.0}
    target = {"a": 0.0, "b": 1.0}
    original = dict(target)
    allocate_turnover_budget(target, current, {"a": 1.0, "b": 1.0})
    assert target == original


def test_budget_scores_nan_safe():
    """score 含 NaN/None → 不崩溃（NaN 视为最低优先剔除）。"""
    current = {"a": 1.0, "b": 0.0, "c": 0.0}
    target = {"a": 0.33, "b": 0.33, "c": 0.34}
    scores = {"a": 2.0, "b": float("nan"), "c": None}  # type: ignore[dict-item]
    out = allocate_turnover_budget(target, current, scores, TurnoverBudgetConfig(daily_turnover_cap=0.30))
    assert _single_side_turnover(out, current) <= 0.30 + 1e-9
