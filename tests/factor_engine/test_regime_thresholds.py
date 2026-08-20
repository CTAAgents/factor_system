"""test_regime_thresholds — plans/59 OPT-01（GAP-161）Regime 条件化门槛测试。"""

from __future__ import annotations

import numpy as np
import pytest

from fts.factor_engine.factor_inspector import AutoReviewPolicy, ReviewDecision
from fts.factor_engine.ir_thresholds import factor_ir_threshold
from fts.factor_engine.qa.monthly_check import monthly_recheck
from fts.factor_engine.qa.quarterly_check import quarterly_recheck
from fts.factor_engine.regime_thresholds import (
    REGIME_KEYS,
    RegimeThresholdConfig,
    apply_regime_multiplier,
    normalize_regime,
)


# ─── normalize_regime ───────────────────────────────────────


def test_normalize_bull_bear_to_trend() -> None:
    """bull/bear 归一为 trend（大小写不敏感）。"""
    assert normalize_regime("bull") == "trend"
    assert normalize_regime("bear") == "trend"
    assert normalize_regime("BULL") == "trend"


def test_normalize_existing_keys_unchanged() -> None:
    """oscillate/high_vol/low_vol 原样保留。"""
    for r in ("oscillate", "high_vol", "low_vol"):
        assert normalize_regime(r) == r


def test_normalize_unknown_returns_none() -> None:
    """未知 regime / 空值返回 None。"""
    assert normalize_regime("quantum") is None
    assert normalize_regime(None) is None
    assert normalize_regime("") is None


def test_regime_keys_cover_norm_set() -> None:
    """REGIME_KEYS 恰好为归一后键集。"""
    assert set(REGIME_KEYS) == {"trend", "oscillate", "high_vol", "low_vol"}


# ─── apply_regime_multiplier ────────────────────────────────


def test_disabled_returns_base() -> None:
    """enabled=False（默认）恒返回原值（向后兼容）。"""
    cfg = RegimeThresholdConfig()
    assert apply_regime_multiplier(0.02, "oscillate", "min_ic", cfg) == 0.02
    assert apply_regime_multiplier(0.5, "high_vol", "min_sharpe", cfg) == 0.5


def test_enabled_multiplier_applied() -> None:
    """enabled=True 且有效 regime → 乘数生效。"""
    cfg = RegimeThresholdConfig(
        enabled=True,
        multipliers={
            "min_ic": {"trend": 1.0, "oscillate": 1.5, "high_vol": 1.2, "low_vol": 1.0},
            "min_sharpe": {"trend": 1.0, "oscillate": 1.5, "high_vol": 1.5, "low_vol": 1.0},
            "min_ir": {"trend": 1.0, "oscillate": 1.2, "high_vol": 1.2, "low_vol": 1.0},
            "decay_warn": {"trend": 1.0, "oscillate": 0.8, "high_vol": 0.9, "low_vol": 1.0},
        },
    )
    assert apply_regime_multiplier(0.02, "oscillate", "min_ic", cfg) == pytest.approx(0.03)
    assert apply_regime_multiplier(0.5, "high_vol", "min_sharpe", cfg) == pytest.approx(0.75)
    # trend 乘数 1.0 → 原值
    assert apply_regime_multiplier(0.02, "trend", "min_ic", cfg) == pytest.approx(0.02)


def test_enabled_unknown_regime_returns_base() -> None:
    """enabled=True 但 regime 未知/缺失 → 原值。"""
    cfg = RegimeThresholdConfig(enabled=True)
    assert apply_regime_multiplier(0.02, "quantum", "min_ic", cfg) == 0.02
    assert apply_regime_multiplier(0.02, None, "min_ic", cfg) == 0.02


def test_enabled_unknown_key_returns_base() -> None:
    """enabled=True 但 key 不在乘数表 → 原值。"""
    cfg = RegimeThresholdConfig(enabled=True)
    assert apply_regime_multiplier(1.0, "oscillate", "not_a_key", cfg) == 1.0


def test_default_multipliers_are_identity() -> None:
    """默认乘数表全 1.0（恒等）：启用但未配置时行为不变。"""
    cfg = RegimeThresholdConfig(enabled=True)
    for key in ("min_ic", "min_sharpe", "min_ir", "decay_warn"):
        for r in REGIME_KEYS:
            assert apply_regime_multiplier(0.5, r, key, cfg) == 0.5


# ─── AutoReviewPolicy.classify regime 集成 ──────────────────


def _qa_meta_all_pass() -> dict:
    return {
        "audit_passed": True,
        "multiple_passed": True,
        "walk_forward_windows": 3,
        "quality_grade": "A",
        "high_ic_grade": "A",
        "q1_q10_passed": True,
    }


def test_classify_default_regime_ignored() -> None:
    """默认配置（enabled=False）下 regime 参数不影响判定（向后兼容）。"""
    policy = AutoReviewPolicy.from_env()
    qa_meta = _qa_meta_all_pass()
    d_no = policy.classify(0.025, 1.0, qa_meta)
    d_reg = policy.classify(0.025, 1.0, qa_meta, regime="oscillate")
    assert d_no[0] == ReviewDecision.APPROVED
    assert d_reg == d_no


def test_classify_regime_uses_multiplier(monkeypatch) -> None:
    """regime 乘数生效：oscillate min_ic ×2 时低 IC 因子被拒。"""
    import fts.factor_engine.regime_thresholds as rt

    policy = AutoReviewPolicy.from_env()  # min_ic=0.02
    qa_meta = _qa_meta_all_pass()

    def fake(base, regime, key, config=None):  # noqa: ARG001
        if regime == "oscillate" and key == "min_ic":
            return base * 2.0
        return base

    monkeypatch.setattr(rt, "apply_regime_multiplier", fake)
    d, reason = policy.classify(0.025, 1.0, qa_meta, regime="oscillate")
    assert d == ReviewDecision.REJECTED
    assert "0.04" in reason  # 门槛 0.02×2=0.04 > 0.025


def test_classify_sharpe_regime_ignored_default(monkeypatch) -> None:
    """Sharpe 维度同样走乘数：patch 只影响 min_sharpe。"""
    import fts.factor_engine.regime_thresholds as rt

    policy = AutoReviewPolicy.from_env()  # min_sharpe=0.5
    qa_meta = _qa_meta_all_pass()

    def fake(base, regime, key, config=None):  # noqa: ARG001
        if regime == "high_vol" and key == "min_sharpe":
            return base * 2.0  # 0.5→1.0
        return base

    monkeypatch.setattr(rt, "apply_regime_multiplier", fake)
    d, reason = policy.classify(0.05, 0.6, qa_meta, regime="high_vol")
    assert d == ReviewDecision.REJECTED
    assert "1.0" in reason  # sharpe 0.6 < 1.0


# ─── factor_ir_threshold regime ─────────────────────────────


def test_factor_ir_threshold_no_regime_unchanged() -> None:
    """无 regime → 分类静态门槛不变。"""
    assert factor_ir_threshold({"style_tags": ["trend"]}) == 0.30  # 量价
    assert factor_ir_threshold({"style_tags": ["value"]}) == 0.40  # 基本面
    assert factor_ir_threshold({"style_tags": ["carry"]}) == 0.35  # 期限结构


def test_factor_ir_threshold_regime_default_no_effect() -> None:
    """默认配置下 regime 不改变 IR 门槛（向后兼容）。"""
    assert factor_ir_threshold({"style_tags": ["trend"]}, regime="oscillate") == 0.30


def test_factor_ir_threshold_regime_enabled(monkeypatch) -> None:
    """启用 regime 乘数后 IR 门槛调整。"""
    import fts.factor_engine.regime_thresholds as rt

    def fake(base, regime, key, config=None):  # noqa: ARG001
        if regime == "oscillate" and key == "min_ir":
            return base * 1.2
        return base

    monkeypatch.setattr(rt, "apply_regime_multiplier", fake)
    assert factor_ir_threshold({"style_tags": ["value"]}, regime="oscillate") == pytest.approx(0.48)


# ─── monthly_recheck M2 regime ──────────────────────────────


def _ic_series() -> np.ndarray:
    """构造可判定且 IR 可控的 IC 序列。

    30 个 -0.048 + 30 个 +0.052 → mean=0.002, std=0.05，
    月频 IR = 0.002/0.05×√252 ≈ 0.63：基准健康下限 0.24 下通过、
    regime 乘数 5（下限 1.20）下预警（判定有区分度）。
    """
    return np.concatenate([np.full(30, -0.048), np.full(30, 0.052)])


def test_monthly_m2_default_regime_no_effect() -> None:
    """默认配置下 regime 参数不改变 M2 结果（向后兼容）。"""
    ic = _ic_series()
    r_trend = monthly_recheck(ic, oos_baseline_ic=0.04, ir_gate=0.30, regime="trend")
    r_osc = monthly_recheck(ic, oos_baseline_ic=0.04, ir_gate=0.30, regime="oscillate")
    assert r_osc["indicators"]["M2"] == r_trend["indicators"]["M2"]
    assert r_trend["indicators"]["M2"]["warned"] is False  # 可判定且健康


def test_monthly_m2_regime_enabled(monkeypatch) -> None:
    """启用 regime 乘数后 M2 健康下限随 regime 变化。"""
    import fts.factor_engine.regime_thresholds as rt

    ic = _ic_series()

    def fake(base, regime, key, config=None):  # noqa: ARG001
        if regime == "oscillate" and key == "min_ir":
            return base * 5.0  # 0.30→1.50，健康下限 1.20
        return base

    monkeypatch.setattr(rt, "apply_regime_multiplier", fake)
    r = monthly_recheck(ic, oos_baseline_ic=0.04, ir_gate=0.30, regime="oscillate")
    assert r["indicators"]["M2"]["warned"] is True
    assert "健康下限=1.20" in r["indicators"]["M2"]["detail"]


# ─── quarterly_recheck F5 regime_change ─────────────────────


def test_quarterly_f5_regime_change_alert() -> None:
    """F5：传入 regime_change 触发门槛调整告警。"""
    r = quarterly_recheck(regime_change="oscillate→trend")
    assert r["indicators"]["F5"]["flagged"] is True
    assert "门槛调整告警" in r["indicators"]["F5"]["detail"]


def test_quarterly_f5_cond_ic_parallel() -> None:
    """F5：无 regime_change 时原 cond_ic_change 逻辑保留。"""
    r_ok = quarterly_recheck(cond_ic_change=0.1)
    assert r_ok["indicators"]["F5"]["flagged"] is False
    r_bad = quarterly_recheck(cond_ic_change=0.6)
    assert r_bad["indicators"]["F5"]["flagged"] is True
