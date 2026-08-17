"""
tests/factor_engine/test_regime_gate.py — 子链方向 Gate + 暴露缩放测试（plans/48 §A/§B / §五；plans/50 §A1）。

覆盖:
    - build_subchain_gates：long/short/avoid/neutral 判定 + min_confidence 参数化
    - apply_subchain_gate：hard 剔除 / soft 降权 / long·short 方向过滤 / blind_default
    - map_confidence_to_exposure：分段映射（<min→0 / ≥sat→1.0 / 中间线性）
    - apply_exposure_scale：暴露 = 置信度映射 × 对齐度；防双重惩罚；盲测 default
    - gate_scale_map：Gate 决策 → 链级权重缩放系数（L3 权重层并入调制矩阵，plans/50）
    - build_symbol_chain_map 反查

纯计算无 DB/IO，不触真实因子库。
"""

from __future__ import annotations

import pytest

from fts.factor_engine.regime_gate import (
    GateConfig,
    apply_exposure_scale,
    apply_subchain_gate,
    build_subchain_gates,
    build_symbol_chain_map,
    gate_scale_map,
    map_confidence_to_exposure,
)

# 四大子链品种映射（与 portfolio_loop.ENERGY_CHAIN_SUB_SYMBOLS 对齐）
CHAINS: dict[str, list[str]] = {
    "能源": ["SC0", "FU0", "BU0"],
    "聚酯": ["PF0", "TA0", "EG0"],
    "油化工": ["L0", "PP0", "PG0"],
    "煤化工": ["MA0", "UR0", "SA0"],
}

SYMBOL_CHAIN = build_symbol_chain_map(CHAINS)


def _regime(regime: str, confidence: float) -> dict:
    return {"regime": regime, "confidence": confidence, "detected_at": "2026-08-17T00:00:00"}


class TestBuildGates:
    def test_bull_high_conf_long(self):
        gates = build_subchain_gates({"能源": _regime("bull", 0.9)}, CHAINS, GateConfig())
        assert gates["能源"]["decision"] == "long"
        assert gates["能源"]["confidence"] == pytest.approx(0.9)

    def test_bear_high_conf_short(self):
        gates = build_subchain_gates({"聚酯": _regime("bear", 0.8)}, CHAINS, GateConfig())
        assert gates["聚酯"]["decision"] == "short"

    def test_low_conf_avoid(self):
        gates = build_subchain_gates({"煤化工": _regime("bear", 0.2)}, CHAINS, GateConfig())
        assert gates["煤化工"]["decision"] == "avoid"  # 方向判定存在但置信度不足

    def test_oscillate_neutral(self):
        # 明确非方向 regime → neutral（不 gate，交因子层）
        gates = build_subchain_gates({"能源": _regime("oscillate", 0.8)}, CHAINS, GateConfig())
        assert gates["能源"]["decision"] == "neutral"

    def test_missing_sector_neutral(self):
        # 无检测数据子链 → neutral（缺数据不误杀）
        gates = build_subchain_gates({}, CHAINS, GateConfig())
        assert all(gates[c]["decision"] == "neutral" for c in CHAINS)

    def test_min_confidence_parametric(self):
        # 门槛参数化：0.58 置信度在 0.55 门槛 → long；0.60 门槛 → avoid
        g_lo = build_subchain_gates({"能源": _regime("bull", 0.58)}, CHAINS, GateConfig())
        assert g_lo["能源"]["decision"] == "long"
        g_hi = build_subchain_gates(
            {"能源": _regime("bull", 0.58)}, CHAINS, GateConfig(min_confidence=0.60)
        )
        assert g_hi["能源"]["decision"] == "avoid"


class TestApplyGate:
    def test_hard_avoid_zeroed(self):
        gates = {
            "能源": _regime("bear", 0.2) | {"decision": "avoid"},
            "煤化工": _regime("oscillate", 0.8) | {"decision": "neutral"},  # 未 gate 链
        }
        scores = {"SC0": 0.5, "FU0": -0.3, "MA0": 0.2}
        out = apply_subchain_gate(scores, gates, SYMBOL_CHAIN, GateConfig())
        assert out["SC0"] == 0.0
        assert out["FU0"] == 0.0
        assert out["MA0"] == pytest.approx(0.2)  # neutral 链不变

    def test_soft_avoid_ratio(self):
        gates = {"煤化工": _regime("bear", 0.2) | {"decision": "avoid"}}
        scores = {"MA0": 0.8, "UR0": -0.4}
        out = apply_subchain_gate(
            scores, gates, SYMBOL_CHAIN, GateConfig(avoid_mode="soft", soft_avoid_ratio=0.3)
        )
        assert out["MA0"] == pytest.approx(0.8 * 0.3)
        assert out["UR0"] == pytest.approx(-0.4 * 0.3)

    def test_long_gate_filters_negative(self):
        # long 子链仅放开多头信号：负分置 0
        gates = {"能源": _regime("bull", 0.9) | {"decision": "long"}}
        scores = {"SC0": 0.5, "FU0": -0.3, "BU0": 0.1}
        out = apply_subchain_gate(scores, gates, SYMBOL_CHAIN, GateConfig())
        assert out["SC0"] == pytest.approx(0.5)
        assert out["FU0"] == 0.0
        assert out["BU0"] == pytest.approx(0.1)

    def test_short_gate_filters_positive(self):
        gates = {"聚酯": _regime("bear", 0.8) | {"decision": "short"}}
        scores = {"PF0": 0.4, "TA0": -0.2, "EG0": -0.1}
        out = apply_subchain_gate(scores, gates, SYMBOL_CHAIN, GateConfig())
        assert out["PF0"] == 0.0
        assert out["TA0"] == pytest.approx(-0.2)
        assert out["EG0"] == pytest.approx(-0.1)

    def test_blind_default_avoid(self):
        # 无子链归属品种（盲测池 BZ0 等）默认回避
        gates = {"能源": _regime("bull", 0.9) | {"decision": "long"}}
        scores = {"BZ0": 0.7, "SC0": 0.5}
        out = apply_subchain_gate(scores, gates, SYMBOL_CHAIN, GateConfig())
        assert out["BZ0"] == 0.0
        assert out["SC0"] == pytest.approx(0.5)

    def test_blind_default_neutral(self):
        gates = {"能源": _regime("bull", 0.9) | {"decision": "long"}}
        scores = {"BZ0": 0.7}
        out = apply_subchain_gate(scores, gates, SYMBOL_CHAIN, GateConfig(blind_default="neutral"))
        assert out["BZ0"] == pytest.approx(0.7)

    def test_neutral_chain_unchanged(self):
        gates = {"能源": _regime("oscillate", 0.8) | {"decision": "neutral"}}
        scores = {"SC0": 0.5, "FU0": -0.3}
        out = apply_subchain_gate(scores, gates, SYMBOL_CHAIN, GateConfig())
        assert out["SC0"] == pytest.approx(0.5)
        assert out["FU0"] == pytest.approx(-0.3)


class TestMapConfidenceToExposure:
    """§B1 置信度→暴露系数分段映射（GateConfig 默认 min=0.4 / sat=0.7）。"""

    def test_below_min_zero(self):
        assert map_confidence_to_exposure(0.3, GateConfig()) == pytest.approx(0.0)

    def test_boundary_min_zero(self):
        # confidence == exposure_min → 0.0（下边界归入线性起点）
        assert map_confidence_to_exposure(0.4, GateConfig()) == pytest.approx(0.0)

    def test_linear_mid(self):
        # 0.55 ∈ [0.4, 0.7) → (0.55-0.4)/0.3 = 0.5
        assert map_confidence_to_exposure(0.55, GateConfig()) == pytest.approx(0.5)

    def test_linear_upper_below_sat(self):
        # 0.69 → (0.69-0.4)/0.3 ≈ 0.9667
        assert map_confidence_to_exposure(0.69, GateConfig()) == pytest.approx((0.69 - 0.4) / 0.3)

    def test_saturated_one(self):
        assert map_confidence_to_exposure(0.8, GateConfig()) == pytest.approx(1.0)

    def test_boundary_sat_one(self):
        assert map_confidence_to_exposure(0.7, GateConfig()) == pytest.approx(1.0)

    def test_parametric_thresholds(self):
        # 门槛参数化：min=0.5/sat=0.9 → 0.7 → (0.7-0.5)/0.4 = 0.5
        cfg = GateConfig(exposure_min=0.5, exposure_sat=0.9)
        assert map_confidence_to_exposure(0.7, cfg) == pytest.approx(0.5)
        assert map_confidence_to_exposure(0.4, cfg) == pytest.approx(0.0)


class TestApplyExposureScale:
    """§B2/B3 品种暴露缩放：暴露 = 子链置信度映射 × 品种-链对齐度。"""

    def test_saturated_conf_times_alignment(self):
        # conf=0.9 ≥ sat → 暴露 1.0；对齐度 0.8 → 得分 ×0.8
        # 两阶段串联：先 A 模块方向 Gate（FU0 负分置 0），再 B 模块暴露缩放（跳过已剔除）
        gates = {"能源": _regime("bull", 0.9) | {"decision": "long"}}
        scores = {"SC0": 0.5, "FU0": -0.3}
        gated = apply_subchain_gate(scores, gates, SYMBOL_CHAIN, GateConfig())
        out = apply_exposure_scale(gated, gates, SYMBOL_CHAIN, {"SC0": 0.8}, GateConfig())
        assert out["SC0"] == pytest.approx(0.5 * 0.8)
        assert out["FU0"] == 0.0  # long gate 剔除 → 防双重惩罚跳过

    def test_linear_conf_scaling(self):
        # conf=0.55 → 暴露 0.5；对齐度 1.0 → 得分 ×0.5；EG0 负分先被 long gate 剔除
        gates = {"聚酯": _regime("bull", 0.55) | {"decision": "long"}}
        scores = {"PF0": 0.8, "TA0": 0.4, "EG0": -0.2}
        gated = apply_subchain_gate(scores, gates, SYMBOL_CHAIN, GateConfig())
        out = apply_exposure_scale(
            gated, gates, SYMBOL_CHAIN, {"PF0": 1.0, "TA0": 1.0, "EG0": 1.0}, GateConfig()
        )
        assert out["PF0"] == pytest.approx(0.8 * 0.5)
        assert out["TA0"] == pytest.approx(0.4 * 0.5)
        assert out["EG0"] == 0.0

    def test_avoid_no_double_penalty_soft(self):
        # 两阶段串联：soft avoid 链已在 apply_subchain_gate 降权（×0.3）；
        # 暴露缩放保留 A 模块结果，不再二次缩放（不双重惩罚）
        gates = {"煤化工": _regime("bear", 0.2) | {"decision": "avoid"}}
        scores = {"MA0": 0.8, "UR0": -0.4}
        gated = apply_subchain_gate(
            scores, gates, SYMBOL_CHAIN, GateConfig(avoid_mode="soft", soft_avoid_ratio=0.3)
        )
        out = apply_exposure_scale(gated, gates, SYMBOL_CHAIN, {"MA0": 1.0, "UR0": 1.0}, GateConfig())
        assert out["MA0"] == pytest.approx(0.8 * 0.3)
        assert out["UR0"] == pytest.approx(-0.4 * 0.3)

    def test_blind_default_avoid(self):
        gates = {"能源": _regime("bull", 0.9) | {"decision": "long"}}
        scores = {"BZ0": 0.7, "SC0": 0.5}
        out = apply_exposure_scale(scores, gates, SYMBOL_CHAIN, {"SC0": 1.0}, GateConfig())
        assert out["BZ0"] == 0.0  # 无子链归属 → blind_default=avoid
        assert out["SC0"] == pytest.approx(0.5)

    def test_missing_alignment_default_half(self):
        # 对齐度缺失 → 保守默认 0.5
        gates = {"油化工": _regime("bull", 0.9) | {"decision": "long"}}
        scores = {"L0": 0.6}
        out = apply_exposure_scale(scores, gates, SYMBOL_CHAIN, {}, GateConfig())
        assert out["L0"] == pytest.approx(0.6 * 0.5)

    def test_neutral_chain_scaled(self):
        # neutral 链（oscillate）仍按置信度映射缩放（方向由因子层决定，暴露随置信度）
        gates = {"煤化工": _regime("oscillate", 0.8) | {"decision": "neutral"}}
        scores = {"MA0": 0.5}
        out = apply_exposure_scale(scores, gates, SYMBOL_CHAIN, {"MA0": 1.0}, GateConfig())
        assert out["MA0"] == pytest.approx(0.5)  # conf 0.8 ≥ sat → 暴露 1.0


class TestGateScaleMap:
    """plans/50 §A1：Gate 决策 → 链级权重缩放系数（L3 权重层并入调制矩阵）。"""

    def test_avoid_hard_zero(self):
        # avoid + hard → 0.0（该链权重归零，不参与组合）
        gates = {"能源": {"regime": "bear", "confidence": 0.2, "decision": "avoid"}}
        out = gate_scale_map(gates, GateConfig())
        assert out["能源"] == pytest.approx(0.0)

    def test_avoid_soft_ratio(self):
        # avoid + soft → soft_avoid_ratio（小仓位保留）
        gates = {"煤化工": {"regime": "bear", "confidence": 0.2, "decision": "avoid"}}
        out = gate_scale_map(gates, GateConfig(avoid_mode="soft", soft_avoid_ratio=0.3))
        assert out["煤化工"] == pytest.approx(0.3)

    def test_long_short_neutral_unchanged(self):
        # long/short/neutral 权重层不干预（方向过滤属信号层 Step 3h1 职责）
        gates = {
            "能源": {"regime": "bull", "confidence": 0.9, "decision": "long"},
            "聚酯": {"regime": "bear", "confidence": 0.8, "decision": "short"},
            "油化工": {"regime": "oscillate", "confidence": 0.8, "decision": "neutral"},
        }
        out = gate_scale_map(gates, GateConfig())
        assert all(v == pytest.approx(1.0) for v in out.values())

    def test_mixed_decisions(self):
        # 混合判定：long 链 1.0、avoid 链 0.0
        gates = {
            "能源": {"regime": "bull", "confidence": 0.9, "decision": "long"},
            "煤化工": {"regime": "bear", "confidence": 0.2, "decision": "avoid"},
        }
        out = gate_scale_map(gates, GateConfig())
        assert out["能源"] == pytest.approx(1.0)
        assert out["煤化工"] == pytest.approx(0.0)

    def test_empty_input(self):
        assert gate_scale_map({}, GateConfig()) == {}
