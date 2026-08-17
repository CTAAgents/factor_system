"""
tests/factor_engine/test_subchain_weight.py — L3 子链差异化权重调制测试（plans/47 §B5 / §5.2；plans/50 §B1）。

覆盖:
    - build_subchain_weights：scope 语义（all/unknown/单链/部分链/缺省）+ zero/soft 双模式
    - apply_subchain_modulation：3D 信号矩阵逐品种左乘 + 未知链兜底 + 维度校验
    - compute_chain_exposure：子链暴露占比（§D2 监控输入）
    - _merge_gate_scale_into_modulation：Gate 缩放并入调制矩阵（plans/50，权重源头生效）

纯计算无 DB/IO，不触真实因子库。
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from fts.factor_engine.portfolio_loop import _merge_gate_scale_into_modulation
from fts.factor_engine.regime_gate import (
    GateConfig,
    apply_subchain_gate,
    build_subchain_gates,
)
from fts.factor_engine.subchain_weight import (
    SubchainWeightConfig,
    apply_subchain_modulation,
    build_subchain_weights,
    build_symbol_chain_map,
    compute_chain_exposure,
)

# 四大子链品种映射（与 portfolio_loop.ENERGY_CHAIN_SUB_SYMBOLS 对齐）
CHAINS: dict[str, list[str]] = {
    "能源": ["SC0", "FU0", "BU0"],
    "聚酯": ["PF0", "TA0", "EG0"],
    "油化工": ["L0", "PP0", "PG0"],
    "煤化工": ["MA0", "UR0", "SA0"],
}

SYMBOL_CHAIN = build_symbol_chain_map(CHAINS)


def _factor(fid: str, scope, profile: dict | None = None) -> dict:
    return {"factor_id": fid, "name": fid, "subchain_scope": scope, "subchain_ic_profile": profile or {}}


def _profile(effective_chains: set[str], mean_ics: dict[str, float]) -> dict:
    """构造 subchain_ic_profile：effective 链 true、其余 false（附 mean_ic）。"""
    out = {}
    for chain in CHAINS:
        out[chain] = {
            "n_symbols": 3,
            "mean_ic": mean_ics.get(chain, 0.0),
            "std_ic": 0.01,
            "t_stat": 10.0,
            "p_value": 0.001,
            "effective": chain in effective_chains,
        }
    return out


class TestBuildWeights:
    def test_scope_single_chain_zero(self):
        # 单链特异：仅油化工 effective，zero 模式 → 其余链 0.0
        f = _factor("f1", ["油化工"], _profile({"油化工"}, {"油化工": 0.20}))
        m = build_subchain_weights([f], CHAINS, SubchainWeightConfig())
        assert m["f1"]["油化工"] == 1.0
        assert all(m["f1"][c] == 0.0 for c in ("能源", "聚酯", "煤化工"))

    def test_scope_multi_chain(self):
        f = _factor("f1", ["能源", "聚酯"], _profile({"能源", "聚酯"}, {"能源": 0.20, "聚酯": 0.18}))
        m = build_subchain_weights([f], CHAINS, SubchainWeightConfig())
        assert m["f1"]["能源"] == 1.0
        assert m["f1"]["聚酯"] == 1.0
        assert m["f1"]["油化工"] == 0.0
        assert m["f1"]["煤化工"] == 0.0

    def test_scope_all(self):
        f = _factor("f1", "all", _profile(set(CHAINS), {}))
        m = build_subchain_weights([f], CHAINS, SubchainWeightConfig())
        assert all(m["f1"][c] == 1.0 for c in CHAINS)

    def test_scope_unknown(self):
        f = _factor("f1", "unknown", {})
        m = build_subchain_weights([f], CHAINS, SubchainWeightConfig())
        assert all(m["f1"][c] == 1.0 for c in CHAINS)  # 未知不误杀

    def test_missing_scope_defaults_all(self):
        # 无 subchain_scope 字段 → scope_default="all" 全链保留（兼容现状）
        f = {"factor_id": "f1", "name": "f1"}
        m = build_subchain_weights([f], CHAINS, SubchainWeightConfig())
        assert all(m["f1"][c] == 1.0 for c in CHAINS)

    def test_missing_scope_defaults_override(self):
        f = {"factor_id": "f1", "name": "f1"}
        m = build_subchain_weights([f], CHAINS, SubchainWeightConfig(scope_default="unknown"))
        assert all(m["f1"][c] == 1.0 for c in CHAINS)  # unknown 语义同 all（全链保留）

    def test_soft_decay_scaling(self):
        # soft 模式：非 effective 链按 |mean_ic|/max_ic 缩放
        prof = _profile({"煤化工"}, {"煤化工": 0.20, "能源": 0.04, "聚酯": 0.02, "油化工": 0.06})
        f = _factor("f1", ["煤化工"], prof)
        m = build_subchain_weights([f], CHAINS, SubchainWeightConfig(decay_mode="soft"))
        assert m["f1"]["煤化工"] == 1.0
        assert m["f1"]["能源"] == pytest.approx(0.04 / 0.20)  # 0.2
        assert m["f1"]["聚酯"] == pytest.approx(0.02 / 0.20)  # 0.1
        assert m["f1"]["油化工"] == pytest.approx(0.06 / 0.20)  # 0.3

    def test_soft_min_ratio_floor(self):
        prof = _profile({"煤化工"}, {"煤化工": 0.20, "能源": 0.04, "聚酯": 0.02, "油化工": 0.06})
        f = _factor("f1", ["煤化工"], prof)
        m = build_subchain_weights([f], CHAINS, SubchainWeightConfig(decay_mode="soft", soft_min_ratio=0.15))
        assert m["f1"]["能源"] == pytest.approx(0.20)  # 0.04/0.20=0.2 > floor 保持
        assert m["f1"]["聚酯"] == pytest.approx(0.15)  # 0.02/0.20=0.1 被抬到 floor
        assert m["f1"]["油化工"] == pytest.approx(0.30)  # 0.06/0.20=0.3 > floor 保持


class TestApplyModulation:
    def _setup(self):
        f_single = _factor("f_single", ["油化工"], _profile({"油化工"}, {"油化工": 0.20}))
        f_all = _factor("f_all", "all", {})
        factors = [f_single, f_all]
        mod = build_subchain_weights(factors, CHAINS, SubchainWeightConfig())
        # 12 品种 × 2 因子 × 10 日 全 1 信号
        signal = np.ones((10, 12, 2))
        return factors, mod, signal

    def test_zero_mode_zeroes_invalid_chain(self):
        factors, mod, signal = self._setup()
        out = apply_subchain_modulation(signal, mod, SYMBOL_CHAIN, factors)
        # f_single（j=0）在油化工品种（L0/PP0/PG0 → 索引 6/7/8）保持 1，其余品种归 0
        oil_idx = [list(SYMBOL_CHAIN.keys()).index(s) for s in ("L0", "PP0", "PG0")]
        for i in range(12):
            expected = 1.0 if i in oil_idx else 0.0
            assert np.allclose(out[:, i, 0], expected)
        # f_all（j=1）全品种保持 1
        assert np.allclose(out[:, :, 1], 1.0)
        # 原矩阵未被修改（返回副本）
        assert np.allclose(signal, 1.0)

    def test_unknown_symbol_defaults_one(self):
        factors, mod, signal = self._setup()
        # symbol_chain 额外含未知品种（无子链映射）→ 兜底 1.0
        ext_chain = dict(SYMBOL_CHAIN)
        ext_chain["NEW0"] = ""  # 未知链
        out = apply_subchain_modulation(np.ones((10, 13, 2)), mod, ext_chain, factors)
        # NEW0 在末尾（索引 12），f_single 应保持 1（兜底）
        assert np.allclose(out[:, 12, 0], 1.0)

    def test_ndim_validation(self):
        factors, mod, signal = self._setup()
        with pytest.raises(ValueError):
            apply_subchain_modulation(np.ones((10, 12)), mod, SYMBOL_CHAIN, factors)

    def test_dim_mismatch_returns_unchanged(self):
        factors, mod, _ = self._setup()
        signal = np.ones((10, 5, 2))  # 品种维度 5 ≠ symbol_chain 12
        out = apply_subchain_modulation(signal, mod, SYMBOL_CHAIN, factors)
        assert np.allclose(out, 1.0)  # 告警回退，不崩溃


class TestL3Integration:
    """L3 集成：PortfolioSignal 携带子链权重 + factor_weights.json 序列化（plans/47 §B）。"""

    def _combo(self) -> dict:
        return {
            "version": "v2",
            "updated_at": "2026-08-17T00:00:00",
            "synthesis_mode": "quality_weight",
            "combo_sharpe": 1.5,
            "n_factors": 2,
            "signals": [
                {"name": "f1", "weight": 0.6, "retained": True},
                {"name": "f2", "weight": 0.4, "retained": True},
            ],
        }

    def test_inject_to_fdt_with_subchain(self, tmp_path):
        from fts.factor_engine.portfolio_loop import inject_to_fdt

        sw = {"f1": {"能源": 1.0, "聚酯": 0.0, "油化工": 0.0, "煤化工": 0.0}}
        sc = {"SC0": "能源", "FU0": "能源", "BU0": "能源"}
        inject_to_fdt(self._combo(), [], tmp_path, subchain_weights=sw, symbol_chain=sc)
        data = json.loads((tmp_path / "factor_weights.json").read_text(encoding="utf-8"))
        assert data["subchain_weights"] == sw
        assert data["symbol_chain"] == sc
        assert set(data["weights"].keys()) == {"f1", "f2"}

    def test_inject_to_fdt_subchain_key_normalized_to_name(self, tmp_path):
        # 方案 A 键一致性修复：调制矩阵以 factor_id 构建（fct_xxx），信号管线按 name 消费
        # （fut_xxx）——注入时经 combo.signals 的 factor_id→name 映射归一，管线 get(name) 命中。
        from fts.factor_engine.portfolio_loop import inject_to_fdt

        combo = {
            "version": "v2",
            "updated_at": "2026-08-17T00:00:00",
            "synthesis_mode": "quality_weight",
            "combo_sharpe": 1.5,
            "n_factors": 2,
            "signals": [
                {"factor_id": "fct_aaa", "name": "fut_alpha", "weight": 0.6, "retained": True},
                {"factor_id": "fct_bbb", "name": "fut_beta", "weight": 0.4, "retained": True},
            ],
        }
        sw = {
            "fct_aaa": {"能源": 1.0, "聚酯": 0.0, "油化工": 0.0, "煤化工": 0.0},
            "fct_bbb": {"能源": 0.0, "聚酯": 0.0, "油化工": 0.0, "煤化工": 0.0},
        }
        sc = {"SC0": "能源", "FU0": "能源", "BU0": "能源"}
        inject_to_fdt(combo, [], tmp_path, subchain_weights=sw, symbol_chain=sc)
        data = json.loads((tmp_path / "factor_weights.json").read_text(encoding="utf-8"))
        # 键已归一为 name（fut_xxx），与 weights 字段键一致 → 管线按 name 消费可命中
        assert set(data["subchain_weights"].keys()) == {"fut_alpha", "fut_beta"}
        assert data["subchain_weights"]["fut_alpha"]["能源"] == 1.0
        assert data["subchain_weights"]["fut_beta"]["能源"] == 0.0

    def test_inject_to_fdt_subchain_key_missing_id_keeps_key(self, tmp_path):
        # 信号无 factor_id（旧产物/测试夹具）时保留原键，不误伤
        from fts.factor_engine.portfolio_loop import inject_to_fdt

        combo = {
            "version": "v2",
            "updated_at": "2026-08-17T00:00:00",
            "synthesis_mode": "equal_weight",
            "n_factors": 1,
            "signals": [{"name": "f1", "weight": 1.0, "retained": True}],
        }
        sw = {"f1": {"能源": 1.0, "聚酯": 0.0, "油化工": 0.0, "煤化工": 0.0}}
        inject_to_fdt(combo, [], tmp_path, subchain_weights=sw, symbol_chain={"SC0": "能源"})
        data = json.loads((tmp_path / "factor_weights.json").read_text(encoding="utf-8"))
        assert data["subchain_weights"] == sw  # 无 factor_id → 键不变

    def test_inject_to_fdt_without_subchain_compat(self, tmp_path):
        # 兼容现状：不传子链参数 → 输出不含子链字段（enable=false 回归路径）
        from fts.factor_engine.portfolio_loop import inject_to_fdt

        inject_to_fdt(self._combo(), [], tmp_path)
        data = json.loads((tmp_path / "factor_weights.json").read_text(encoding="utf-8"))
        assert "subchain_weights" not in data
        assert "symbol_chain" not in data

    def test_portfolio_signal_contract_has_subchain_weights(self):
        # PortfolioSignal 契约字段就绪（synthesize_signals 附加路径）
        from fts.factor_engine.contracts import PortfolioSignal

        s: PortfolioSignal = {
            "factor_id": "f1",
            "name": "f1",
            "weight": 0.5,
            "subchain_weights": {"能源": 1.0, "油化工": 0.0},
        }
        assert s["subchain_weights"]["油化工"] == 0.0

    def test_compute_composite_scores_subchain_modulation(self):
        # 信号管线按品种应用子链调制：无效子链品种权重归零 → 综合得分剔除
        from scripts.futures_signal_pipeline import _compute_composite_scores

        signal_matrix = {"symA": {"f1": np.array([1.0])}, "symB": {"f1": np.array([1.0])}}
        factor_weights = {"f1": 1.0}
        subchain_weights = {"f1": {"链A": 1.0, "链B": 0.0}}
        symbol_chain = {"symA": "链A", "symB": "链B"}
        scores, _ = _compute_composite_scores(
            signal_matrix,
            {},
            [{"name": "f1"}],
            factor_weights,
            subchain_weights=subchain_weights,
            symbol_chain=symbol_chain,
        )
        assert scores["symA"] == pytest.approx(1.0)  # 有效子链全权重
        assert "symB" not in scores  # 无效子链权重 0 → 权重和为 0，不进入组合

    def test_end_to_end_subchain_key_chain(self, tmp_path):
        # 方案 A/B 端到端：build_subchain_weights（factor_id 键）→ inject_to_fdt
        # （归一为 name 键）→ _load_l3_subchain_meta（按 name 读回）→
        # _compute_composite_scores（按 name 消费）——调制矩阵真实生效。
        from fts.factor_engine.portfolio_loop import inject_to_fdt
        from fts.factor_engine.subchain_weight import SubchainWeightConfig, build_subchain_weights
        from scripts.futures_signal_pipeline import _compute_composite_scores, _load_l3_subchain_meta

        # 真实形态：factor_id=fct_xxx，name=fut_xxx（generate_factor_id 生成）
        f1 = {
            "factor_id": "fct_aaa",
            "name": "fut_alpha",
            "subchain_scope": ["能源"],
            "subchain_ic_profile": _profile({"能源"}, {"能源": 0.20}),
        }
        f2 = {
            "factor_id": "fct_bbb",
            "name": "fut_beta",
            "subchain_scope": "all",
            "subchain_ic_profile": {},
        }
        mod = build_subchain_weights([f1, f2], CHAINS, SubchainWeightConfig())
        assert set(mod.keys()) == {"fct_aaa", "fct_bbb"}  # 矩阵键 = factor_id
        assert mod["fct_aaa"]["能源"] == 1.0
        assert mod["fct_aaa"]["聚酯"] == 0.0
        assert mod["fct_bbb"]["能源"] == 1.0  # all → 全链 1.0

        combo = {
            "version": "v2",
            "updated_at": "2026-08-17T00:00:00",
            "synthesis_mode": "quality_weight",
            "n_factors": 2,
            "signals": [
                {"factor_id": "fct_aaa", "name": "fut_alpha", "weight": 0.6, "retained": True},
                {"factor_id": "fct_bbb", "name": "fut_beta", "weight": 0.4, "retained": True},
            ],
        }
        inject_to_fdt(combo, [], tmp_path, subchain_weights=mod, symbol_chain=SYMBOL_CHAIN)
        sw, sc = _load_l3_subchain_meta(tmp_path / "factor_weights.json")
        assert set(sw.keys()) == {"fut_alpha", "fut_beta"}  # 注入端已归一 name 键
        assert sw["fut_alpha"]["能源"] == 1.0
        assert sw["fut_alpha"]["聚酯"] == 0.0

        # 管线消费：fut_alpha 在无效子链（聚酯 PF0）权重 0 → 仅全链 fut_beta 留在 PF0
        signal_matrix = {
            "SC0": {"fut_alpha": np.array([1.0]), "fut_beta": np.array([1.0])},
            "PF0": {"fut_alpha": np.array([1.0]), "fut_beta": np.array([1.0])},
            "TA0": {"fut_alpha": np.array([1.0])},  # 仅单链特异因子 → 权重和 0 → 剔除
        }
        factor_weights = {"fut_alpha": 0.6, "fut_beta": 0.4}
        factors = [{"name": "fut_alpha"}, {"name": "fut_beta"}]
        scores, _ = _compute_composite_scores(
            signal_matrix, {}, factors, factor_weights, subchain_weights=sw, symbol_chain=sc
        )
        assert scores["SC0"] == pytest.approx(1.0)  # 有效子链全权重（0.6+0.4 归一）
        assert "TA0" not in scores  # fut_alpha 权重 0 → 权重和 0，剔除（调制生效）
        # PF0 中 fut_alpha 被归零、fut_beta 保留 → 得分仍为全链因子主导的 1.0
        # （调制语义=降权而非剔除品种，此处断言不再包含被归零的 fut_alpha 单独主导）
        assert scores["PF0"] == pytest.approx(1.0)

    def test_compute_composite_scores_no_subchain_compat(self):
        # 不传子链参数 → 全链权重（兼容现状）
        from scripts.futures_signal_pipeline import _compute_composite_scores

        signal_matrix = {"symA": {"f1": np.array([1.0])}}
        scores, _ = _compute_composite_scores(signal_matrix, {}, [{"name": "f1"}], {"f1": 1.0})
        assert scores["symA"] == pytest.approx(1.0)

    def test_load_l3_subchain_meta(self, tmp_path):
        from scripts.futures_signal_pipeline import _load_l3_subchain_meta

        fp = tmp_path / "factor_weights.json"
        fp.write_text(
            json.dumps({"weights": {"f1": 1.0}, "subchain_weights": {"f1": {"能源": 1.0}}, "symbol_chain": {"SC0": "能源"}}),
            encoding="utf-8",
        )
        sw, sc = _load_l3_subchain_meta(fp)
        assert sw == {"f1": {"能源": 1.0}}
        assert sc == {"SC0": "能源"}

    def test_load_l3_subchain_meta_missing_compat(self, tmp_path):
        # 旧产物/未开启 → 空 dict（兼容现状）
        from scripts.futures_signal_pipeline import _load_l3_subchain_meta

        fp = tmp_path / "factor_weights.json"
        fp.write_text(json.dumps({"weights": {"f1": 1.0}}), encoding="utf-8")
        sw, sc = _load_l3_subchain_meta(fp)
        assert sw == {}
        assert sc == {}


class TestChainExposure:
    def test_exposure_ratio(self):
        f1 = _factor("f1", ["油化工"], _profile({"油化工"}, {"油化工": 0.20}))
        f2 = _factor("f2", "all", {})
        mod = build_subchain_weights([f1, f2], CHAINS, SubchainWeightConfig())
        exp = compute_chain_exposure(mod, CHAINS)
        # f1: 油化工 1 + 其他 0；f2: 全 1 → 总权重 = 1(油化工) + 4(全链) = 5
        assert exp["油化工"] == pytest.approx(2.0 / 5.0)  # 1+1 / 5
        assert exp["能源"] == pytest.approx(1.0 / 5.0)
        assert exp["聚酯"] == pytest.approx(1.0 / 5.0)
        assert exp["煤化工"] == pytest.approx(1.0 / 5.0)

    def test_exposure_empty(self):
        exp = compute_chain_exposure({}, CHAINS)
        assert all(exp[c] == 0.0 for c in CHAINS)


class TestPlan48ChainedD2:
    """plans/48 §D2：47 子链调制（幅度层）→ 48 方向 Gate（方向层）串联验证。

    链路：L3 组合（47 因子权重调制）→ 信号管线合成品种得分 → 48 Gate 过滤方向。
    Gate 仅改变品种得分方向（方向层），调制仅改变因子权重（幅度层），两者正交。
    """

    def test_plan47_modulation_then_plan48_gate_orthogonal(self):
        # 47：单链特异因子 f1（仅油化工 effective）→ 无效链归零
        f1 = _factor("f1", ["油化工"], _profile({"油化工"}, {"油化工": 0.20}))
        mod = build_subchain_weights([f1], CHAINS, SubchainWeightConfig())
        # 3D 信号矩阵：3 日期 × 12 品种 × 1 因子（品种序与 SYMBOL_CHAIN 对齐）
        mat = np.ones((3, len(SYMBOL_CHAIN), 1))
        mat_mod = apply_subchain_modulation(mat, mod, SYMBOL_CHAIN, [f1])
        # 油化工品种 idx 6..8 保持 1.0；其余链归零（幅度层生效）
        assert np.all(mat_mod[:, 6, 0] == 1.0)
        assert np.all(mat_mod[:, 0, 0] == 0.0)
        assert np.all(mat_mod[:, 3, 0] == 0.0)
        assert np.all(mat_mod[:, 9, 0] == 0.0)

        # 48：子链方向 Gate（能源 bear 低置信 avoid；煤化工 oscillate neutral；油化工/聚酯 bull long）
        gates = build_subchain_gates(
            {
                "能源": {"regime": "bear", "confidence": 0.2},
                "聚酯": {"regime": "bull", "confidence": 0.9},
                "油化工": {"regime": "bull", "confidence": 0.8},
                "煤化工": {"regime": "oscillate", "confidence": 0.8},
            },
            CHAINS,
            GateConfig(),
        )
        scores = {"SC0": 0.4, "PF0": -0.2, "L0": 0.5, "MA0": 0.3}
        gated = apply_subchain_gate(scores, gates, SYMBOL_CHAIN, GateConfig())
        assert gated["SC0"] == 0.0  # avoid 剔除（方向层）
        assert gated["PF0"] == 0.0  # long 过滤负分（方向层）
        assert gated["L0"] == pytest.approx(0.5)  # long 正分保留
        assert gated["MA0"] == pytest.approx(0.3)  # neutral 不变

        # 串联正交性：幅度层调制仅动因子权重、方向层 Gate 仅动品种得分，互不覆盖
        assert np.all(mat_mod[:, 6, 0] == 1.0)  # 调制后油化工因子权重保持
        assert gated["L0"] == pytest.approx(0.5)  # Gate 后油化工方向保留
        assert gated["SC0"] == 0.0  # Gate 对能源剔除不受调制影响（正交）


class TestMergeGateScaleIntoModulation:
    """plans/50 §B1：Gate 缩放系数并入子链调制矩阵（权重源头生效）。

    链路：build_subchain_weights（47 幅度）→ gate_scale_map（48 方向）→
    _merge_gate_scale_into_modulation → m'[factor][子链] = m × gate_scale。
    """

    def test_avoid_hard_zeroes_chain(self):
        # avoid-hard 链调制系数归零（该链权重源头剔除）；有效链不受影响
        # 部分链有效因子（能源/油化工 effective）→ 两链调制=1.0，gate 仅归零 avoid 链
        f1 = _factor("f1", ["能源", "油化工"], _profile({"能源", "油化工"}, {"能源": 0.20, "油化工": 0.18}))
        mod = build_subchain_weights([f1], CHAINS, SubchainWeightConfig())
        assert mod["f1"]["能源"] == pytest.approx(1.0)
        mod_merged = _merge_gate_scale_into_modulation(
            mod, {"能源": 0.0, "聚酯": 1.0, "油化工": 1.0, "煤化工": 1.0}, []
        )
        assert mod_merged["f1"]["能源"] == pytest.approx(0.0)  # avoid-hard 归零
        assert mod_merged["f1"]["油化工"] == pytest.approx(1.0)  # long/neutral 链不受影响

    def test_avoid_soft_ratio(self):
        # avoid-soft 链按 soft_avoid_ratio 降权（×0.3，保留小仓位）
        f1 = _factor("f1", ["能源", "油化工"], _profile({"能源", "油化工"}, {"能源": 0.20, "油化工": 0.18}))
        mod = build_subchain_weights([f1], CHAINS, SubchainWeightConfig())
        assert mod["f1"]["能源"] == pytest.approx(1.0)
        mod_merged = _merge_gate_scale_into_modulation(
            mod, {"能源": 0.3, "聚酯": 1.0, "油化工": 1.0, "煤化工": 1.0}, []
        )
        assert mod_merged["f1"]["能源"] == pytest.approx(0.3)
        assert mod_merged["f1"]["油化工"] == pytest.approx(1.0)

    def test_long_short_neutral_unchanged(self):
        # long/short/neutral → gate_scale=1.0 → 调制系数不变（方向过滤属信号层）
        f1 = _factor("f1", ["能源"], _profile({"能源"}, {"能源": 0.20}))
        mod = build_subchain_weights([f1], CHAINS, SubchainWeightConfig())
        snapshot = {c: dict(row) for c, row in mod.items()}
        _merge_gate_scale_into_modulation(
            mod, {"能源": 1.0, "聚酯": 1.0, "油化工": 1.0, "煤化工": 1.0}, []
        )
        assert mod == snapshot  # 全 1.0 → 逐位不变（无浮点抖动）

    def test_signals_subchain_weights_synced(self):
        # signals 中 Step 2b 标注的 subchain_weights 同步缩放（factor_weights.json 输出）
        signals = [{"factor_id": "f1", "name": "f1", "subchain_weights": {"能源": 1.0, "油化工": 1.0}}]
        _merge_gate_scale_into_modulation(
            {}, {"能源": 0.0, "油化工": 1.0}, signals
        )
        assert signals[0]["subchain_weights"]["能源"] == pytest.approx(0.0)
        assert signals[0]["subchain_weights"]["油化工"] == pytest.approx(1.0)

    def test_scale_not_covered_chain_kept(self):
        # gate_scale 未覆盖的链（新子链/未知链）保持原值不误伤
        f1 = _factor("f1", ["油化工"], _profile({"油化工"}, {"油化工": 0.20}))
        mod = build_subchain_weights([f1], CHAINS, SubchainWeightConfig())
        _merge_gate_scale_into_modulation(mod, {"能源": 0.0}, [])
        assert mod["f1"]["油化工"] == pytest.approx(1.0)  # 未覆盖链不变

    def test_empty_modulation_noop(self):
        # 空调制矩阵 → no-op（Gate 开启但 enable_subchain_weight 未开时零行为变更）
        assert _merge_gate_scale_into_modulation({}, {"能源": 0.0}, []) == {}
