"""tests/factor_engine/test_regime_conditional_weight.py — Regime 条件化权重测试（plans/53 §B）。

HARNESS §测试随重构。
"""

from __future__ import annotations

from fts.factor_engine.regime_conditional_weight import (
    RegimeConditionalConfig,
    build_regime_conditioned_weights,
)


def _factor(fid: str, scope, profile: dict | None = None) -> dict:
    """构造含 regime 画像的因子 dict。"""
    return {
        "factor_id": fid,
        "name": fid,
        "regime_scope": scope,
        "regime_ic_profile": profile or {},
    }


def _profile(regime: str, ic: float, effective: bool = False) -> dict:
    """构造单制度画像片段 {regime: {ic, effective, ...}}。"""
    return {regime: {"ic": ic, "icir": 1.0 if effective else -1.0, "effective": effective, "n": 60}}


def test_negative_ic_zero_mode():
    """zero 模式：当前制度 IC 显著为负 → 权重归零。"""
    factors = [
        _factor("f_neg", ["bull", "oscillate"], _profile("bear", -0.20)),
        _factor("f_pos", ["bull", "bear"], _profile("bear", 0.15)),
    ]
    mod = build_regime_conditioned_weights(factors, "bear", RegimeConditionalConfig(decay_mode="zero"))
    assert mod["f_neg"] == 0.0
    assert mod["f_pos"] == 1.0  # bear 在 scope 内（effective）→ 全权重


def test_negative_ic_soft_mode():
    """soft 模式：当前制度 IC 显著为负 → 按 |ic|/max_ic 相对缩放。"""
    factors = [
        _factor("f1", ["bull"], _profile("bear", -0.10)),
    ]
    # 需同时给 bull 一个 ic 用于 max_ic 分母
    factors[0]["regime_ic_profile"]["bull"] = {"ic": 0.20}
    mod = build_regime_conditioned_weights(factors, "bear", RegimeConditionalConfig(decay_mode="soft"))
    assert abs(mod["f1"] - 0.10 / 0.20) < 1e-9


def test_scope_all_kept():
    """scope='all' → 当前制度任何值都不降权。"""
    factors = [_factor("f_all", "all", _profile("bear", -0.30))]
    mod = build_regime_conditioned_weights(factors, "bear", RegimeConditionalConfig())
    assert mod["f_all"] == 1.0


def test_scope_unknown_kept():
    """scope='unknown' → 不误杀。"""
    factors = [_factor("f_unk", "unknown", _profile("bear", -0.30))]
    mod = build_regime_conditioned_weights(factors, "bear", RegimeConditionalConfig())
    assert mod["f_unk"] == 1.0


def test_no_profile_kept():
    """无 regime 画像字段 → scope_default='all' 全保留。"""
    factors = [{"factor_id": "f_none", "name": "f_none"}]  # 无 scope/profile
    mod = build_regime_conditioned_weights(factors, "bear", RegimeConditionalConfig())
    assert mod["f_none"] == 1.0


def test_weak_negative_ic_kept():
    """|ic| < min_abs_ic（弱负向）→ 不降权（护栏偏向漏标，防过度裁剪）。"""
    factors = [_factor("f_weak", ["bull"], _profile("bear", -0.03))]
    mod = build_regime_conditioned_weights(
        factors, "bear", RegimeConditionalConfig(min_abs_ic=0.05)
    )
    assert mod["f_weak"] == 1.0


def test_positive_ic_kept():
    """当前制度 IC 为正 → 不降权。"""
    factors = [_factor("f_pos2", ["bull"], _profile("bear", 0.08))]
    mod = build_regime_conditioned_weights(factors, "bear", RegimeConditionalConfig())
    assert mod["f_pos2"] == 1.0


def test_effective_regime_kept():
    """当前制度在 scope 内（effective）→ 不降权（即使 profile ic 字段为负值也不触发，scope 优先）。"""
    factors = [_factor("f_eff", ["bear", "oscillate"], _profile("bear", -0.10, effective=True))]
    mod = build_regime_conditioned_weights(factors, "bear", RegimeConditionalConfig())
    assert mod["f_eff"] == 1.0


def test_current_regime_no_record_kept():
    """当前制度无画像记录 → 1.0 不误杀。"""
    factors = [_factor("f_norec", ["bull"], {"bull": {"ic": 0.15, "effective": True}})]
    mod = build_regime_conditioned_weights(factors, "bear", RegimeConditionalConfig())
    assert mod["f_norec"] == 1.0


def test_non_float_ic_kept():
    """ic 字段异常（None/非数值）→ 1.0 不降权。"""
    factors = [_factor("f_bad", ["bull"], {"bear": {"ic": None, "effective": False}})]
    mod = build_regime_conditioned_weights(factors, "bear", RegimeConditionalConfig())
    assert mod["f_bad"] == 1.0
