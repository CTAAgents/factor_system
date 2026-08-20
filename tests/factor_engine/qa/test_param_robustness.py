"""test_param_robustness — plans/59 OPT-07（GAP-167）参数稳健区动态化测试。"""

from __future__ import annotations

import pytest

from fts.factor_engine.evolution_promote import build_qa_review
from fts.factor_engine.qa.monthly_check import monthly_recheck
from fts.factor_engine.qa.param_robustness import (
    VERDICT_FRAGILE,
    VERDICT_ROBUST,
    ParamRobustnessConfig,
    compute_param_robustness,
    param_perturbations,
    robust_ratio_verdict,
)
from fts.factor_engine.qa.quarterly_check import quarterly_recheck


# ─── param_perturbations ────────────────────────────────────


def test_perturbations_numeric_grid() -> None:
    """单数值参数 → 三档网格（含原始值）。"""
    combos = param_perturbations({"window": 10})
    assert len(combos) == 3
    windows = sorted(c["window"] for c in combos)
    assert windows == pytest.approx([8.0, 10.0, 12.0])  # ±20%


def test_perturbations_non_numeric_kept() -> None:
    """非数值参数原样保留。"""
    combos = param_perturbations({"window": 10, "direction": "long", "symbols": ["SC0"]})
    assert all(c["direction"] == "long" for c in combos)
    assert all(c["symbols"] == ["SC0"] for c in combos)


def test_perturbations_original_included() -> None:
    """原始参数保证在集合中。"""
    combos = param_perturbations({"a": 1, "b": 2, "c": 3})
    assert {"a": 1, "b": 2, "c": 3} in combos


def test_perturbations_max_samples_cap() -> None:
    """组合数超上限截断（含原始值兜底）。"""
    cfg = ParamRobustnessConfig(max_samples=5)
    combos = param_perturbations({"a": 1, "b": 2, "c": 3, "d": 4, "e": 5}, cfg)
    assert len(combos) <= 6  # max_samples + 原始值兜底


def test_perturbations_empty_params() -> None:
    """空参数 → 空列表。"""
    assert param_perturbations({}) == []
    assert param_perturbations(None) == []


# ─── compute_param_robustness ───────────────────────────────


def test_robust_ratio_high() -> None:
    """多数网格点绩效衰减小 → 高鲁棒区。"""
    r = compute_param_robustness(
        0.05,
        [{"params": {"w": 8}, "metric": 0.045}, {"params": {"w": 10}, "metric": 0.05}, {"params": {"w": 12}, "metric": 0.040}],
    )
    # 衰减: 10% / 0% / 20% → 全部 ≤ 30% → robust_ratio 1.0
    assert r["robust_ratio"] == pytest.approx(1.0)
    assert r["verdict"] == VERDICT_ROBUST


def test_narrow_peak_fragile() -> None:
    """窄峰参数（邻域剧烈衰减）→ 鲁棒区低 → fragile。"""
    r = compute_param_robustness(
        0.05,
        [{"params": {"w": 8}, "metric": 0.005}, {"params": {"w": 10}, "metric": 0.05}, {"params": {"w": 12}, "metric": 0.003}],
    )
    # 衰减: 90% / 0% / 94% → 仅 1/3 达标 → 0.33 < 0.60 → fragile
    assert r["robust_ratio"] == pytest.approx(1 / 3, abs=1e-4)
    assert r["verdict"] == VERDICT_FRAGILE


def test_no_grid_metrics_fragile() -> None:
    """网格数据缺失 → 保守 fragile。"""
    r = compute_param_robustness(0.05, [])
    assert r["verdict"] == VERDICT_FRAGILE
    assert r["robust_count"] == 0


def test_custom_config() -> None:
    """自定义衰减线/合格线生效。"""
    cfg = ParamRobustnessConfig(perf_decay_threshold=0.1, min_robust_ratio=0.5)
    r = compute_param_robustness(
        0.05,
        [{"params": {}, "metric": 0.045}, {"params": {}, "metric": 0.05}, {"params": {}, "metric": 0.040}],
        cfg,
    )
    # 衰减 10%/0%/20% → 仅 2/3 ≤ 10% → 0.67 ≥ 0.5 → robust
    assert r["verdict"] == VERDICT_ROBUST


# ─── robust_ratio_verdict ───────────────────────────────────


def test_verdict_threshold() -> None:
    """鲁棒区占比阈值判定。"""
    assert robust_ratio_verdict(0.7) == VERDICT_ROBUST
    assert robust_ratio_verdict(0.4) == VERDICT_FRAGILE
    assert robust_ratio_verdict(0.6) == VERDICT_ROBUST  # 边界 ≥ 合格线


def test_verdict_disabled_passes() -> None:
    """enabled=False → 恒放行（向后兼容）。"""
    cfg = ParamRobustnessConfig(enabled=False)
    assert robust_ratio_verdict(0.0, cfg) == VERDICT_ROBUST


# ─── Q3 集成（build_qa_review） ─────────────────────────────


def _build_eval(param_robustness: dict | None = None) -> dict:
    return {
        "level_1_backtest": {"ic": 0.05, "icir": 0.5, "monotonicity": True},
        "level_3_multiple": {"passed": True},
        "walk_forward": {"n_windows_completed": 3},
        "robustness_check": {},
        "cross_symbol_positive_ratio": 0.8,
        "param_robustness": param_robustness,
    }


def _factor() -> dict:
    return {"economic_logic": {"theory": "x"}, "params": {"window": 10}, "style_tags": []}


def test_q3_passes_without_robustness_report() -> None:
    """评估链未产出 param_robustness → 回退 params 非空（向后兼容）。"""
    qa = build_qa_review(_factor(), _build_eval(None), None, None, None)
    q3 = next(i for i in qa["q1_q10"]["items"] if i["qid"] == "Q3")
    assert q3["passed"] is True


def test_q3_passes_robust() -> None:
    """param_robustness=robust → 通过。"""
    qa = build_qa_review(_factor(), _build_eval({"verdict": "robust", "detail": "鲁棒区占比 100%"}), None, None, None)
    q3 = next(i for i in qa["q1_q10"]["items"] if i["qid"] == "Q3")
    assert q3["passed"] is True


def test_q3_fails_fragile() -> None:
    """param_robustness=fragile（窄峰参数）→ Q3 一票否决拦截。"""
    qa = build_qa_review(
        _factor(), _build_eval({"verdict": "fragile", "detail": "鲁棒区占比 33%"}), None, None, None
    )
    q3 = next(i for i in qa["q1_q10"]["items"] if i["qid"] == "Q3")
    assert q3["passed"] is False
    assert "参数稳健区" in q3["detail"]


# ─── F3 集成（quarterly_recheck） ───────────────────────────


def test_f3_param_robust_ratio_fragile_flagged() -> None:
    """F3：参数鲁棒区占比 < 0.60 → 窄峰参数标记。"""
    r = quarterly_recheck(param_robust_ratio=0.4)
    assert r["indicators"]["F3"]["flagged"] is True
    assert "窄峰参数" in r["indicators"]["F3"]["detail"]


def test_f3_param_robust_ratio_ok() -> None:
    """F3：参数鲁棒区占比 ≥ 0.60 → 通过。"""
    r = quarterly_recheck(param_robust_ratio=0.8)
    assert r["indicators"]["F3"]["flagged"] is False


def test_f3_param_steps_fallback_kept() -> None:
    """F3：无 param_robust_ratio 时档位偏移判定保留（向后兼容）。"""
    r = quarterly_recheck(param_steps=2)
    assert r["indicators"]["F3"]["flagged"] is True
    r2 = quarterly_recheck(param_steps=0)
    assert r2["indicators"]["F3"]["flagged"] is False


# ─── 月度复检附加预警 ──────────────────────────────────────


def test_monthly_param_robust_attached() -> None:
    """月度复检附加 param_robust 字段（不进 M1-M5 warn_count）。"""
    import numpy as np

    ic = np.concatenate([np.full(30, -0.048), np.full(30, 0.052)])
    r = monthly_recheck(ic, oos_baseline_ic=0.04, ir_gate=0.30, param_robust_ratio=0.4)
    assert r["param_robust"]["warned"] is True
    # 不影响 M1-M5 处置（warn_count 不变）
    r_base = monthly_recheck(ic, oos_baseline_ic=0.04, ir_gate=0.30)
    assert r["warn_count"] == r_base["warn_count"]


def test_monthly_param_robust_missing_no_warn() -> None:
    """无 param_robust_ratio → 附加项不预警。"""
    import numpy as np

    ic = np.concatenate([np.full(30, -0.048), np.full(30, 0.052)])
    r = monthly_recheck(ic, oos_baseline_ic=0.04, ir_gate=0.30)
    assert r["param_robust"]["warned"] is False
