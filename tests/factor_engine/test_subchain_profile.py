"""
tests/factor_engine/test_subchain_profile.py — 子链适用性画像与显著性护栏测试（plans/47 §A3 / §5.1）。

覆盖:
    - 三门槛 AND 判定链（min_symbols / min_t_stat / min_chain_ic）
    - df=2 单样本 t 检验 + ddof=1 样本标准差（n=3 关键语义）
    - std=0 兜底（t=inf）/ NaN 剔除重算 / 品种数不足拦截
    - scope 派生（单链 specific / 多链 / all / unknown）与保守性设计
    - 随机噪声误报率 < 5%（统计护栏核心目标）

用例与 plans/47 §5.1 清单一一对应（20 项，纯计算无 DB/IO，不触真实因子库）。
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats as sp_stats

from fts.factor_engine.subchain_profile import (
    SubchainProfileConfig,
    build_subchain_metadata,
    compute_subchain_profile,
)

# 四大子链品种映射（与 portfolio_loop.ENERGY_CHAIN_SUB_SYMBOLS 对齐）
CHAINS: dict[str, list[str]] = {
    "能源": ["SC0", "FU0", "BU0"],
    "聚酯": ["PF0", "TA0", "EG0"],
    "油化工": ["L0", "PP0", "PG0"],
    "煤化工": ["MA0", "UR0", "SA0"],
}

HIGH = [0.20, 0.21, 0.22]  # 同向一致 → effective
# 弱均值（③拦截）：min_chain_ic=0.02（v2.105.0+16 由 0.10 校准），0.006 < 0.02
WEAK = [0.005, 0.006, 0.007]


def _symbol_ic(chains_ics: dict[str, list[float]]) -> dict[str, float]:
    """按 {子链: 品种IC列表} 构造 symbol_ic 全量字典。"""
    out: dict[str, float] = {}
    for chain, ics in chains_ics.items():
        for sym, ic in zip(CHAINS[chain], ics):
            out[sym] = float(ic)
    return out


def _single_chain_stat(ics: list[float], cfg: SubchainProfileConfig | None = None) -> "object":
    """单子链画像快捷方式：仅用"能源"子链计算，返回 ChainStat。"""
    sic = dict(zip(CHAINS["能源"], ics))
    prof = compute_subchain_profile("f", sic, {"能源": CHAINS["能源"]}, cfg)
    return prof.chain_stats["能源"]


# ── P0：判定链主干 ──────────────────────────────────────────────


class TestJudgeChain:
    def test_3syms_same_direction(self):
        st = _single_chain_stat(HIGH)
        assert st.effective is True
        assert st.n_symbols == 3
        assert st.mean_ic == pytest.approx(0.210, abs=1e-3)
        assert st.t_stat > 10
        assert st.p_value < 0.01

    def test_mixed_sign_rejected(self):
        # [0.20, 0.05, -0.10]：均值 0.05 接近门槛但符号混杂 → t 小，不标
        st = _single_chain_stat([0.20, 0.05, -0.10])
        assert st.effective is False
        assert st.t_stat < 2.0

    def test_weak_mean_rejected(self):
        # t 很大但 |mean|<0.02 → ③拦截（min_chain_ic=0.02 校准）
        st = _single_chain_stat([0.015, 0.016, 0.017])
        assert st.effective is False
        assert st.t_stat > 10

    def test_2symbols_insufficient(self):
        st = _single_chain_stat([0.20, 0.21])
        assert st.effective is False
        assert st.n_symbols == 2
        assert st.t_stat is None  # 门槛①拦截，不做 t 检验

    def test_1symbol_insufficient(self):
        st = _single_chain_stat([0.20])
        assert st.effective is False
        assert st.n_symbols == 1
        assert st.t_stat is None
        assert st.std_ic is None  # 不抛异常

    def test_nan_dropped_insufficient(self):
        sic = {"S0": 0.20, "S1": 0.21, "S2": float("nan")}
        prof = compute_subchain_profile("f", sic, {"能源": ["S0", "S1", "S2"]})
        st = prof.chain_stats["能源"]
        assert st.n_symbols == 2  # NaN 剔除后重算
        assert st.effective is False


# ── P1：边界与工程细节 ──────────────────────────────────────────


class TestEdgeCases:
    def test_identical_std_zero(self):
        st = _single_chain_stat([0.20, 0.20, 0.20])
        assert st.t_stat == float("inf")
        assert st.p_value == 0.0
        assert st.effective is True  # std 兜底，仅由③决定

    def test_identical_weak_std_zero(self):
        st = _single_chain_stat([0.01, 0.01, 0.01])
        assert st.t_stat == float("inf")
        assert st.effective is False  # std 兜底下仍被③拦截（0.01 < 0.02）

    def test_threshold_parametric(self):
        # t 落在 [2.0, 2.92) 之间的数组：mean=0.10, std(ddof=1)=0.07, t≈2.47
        ics = [0.03, 0.17, 0.10]
        st_default = _single_chain_stat(ics)
        assert st_default.effective is True  # 默认 min_t_stat=2.0
        st_strict = _single_chain_stat(ics, SubchainProfileConfig(min_t_stat=2.92))
        assert st_strict.effective is False  # |t|=2.47 < 2.92

    def test_ddof1_sample_std(self):
        ics = [0.20, 0.05, -0.10]
        st = _single_chain_stat(ics)
        arr = np.array(ics, dtype=float)
        t_ddof0 = arr.mean() / (arr.std(ddof=0) / np.sqrt(3))
        assert st.t_stat == pytest.approx(arr.mean() / (arr.std(ddof=1) / np.sqrt(3)))
        assert st.t_stat != pytest.approx(t_ddof0)  # n=3 必须 ddof=1

    def test_pvalue_recorded(self):
        ics = [0.20, 0.21, 0.22]
        st = _single_chain_stat(ics)
        expected = float(sp_stats.t.sf(abs(st.t_stat), df=st.n_symbols - 1) * 2)
        assert st.p_value == pytest.approx(expected)

    def test_partial_chain_symbols(self):
        # 煤化工仅 2 品种 → 该链 n=2 拦截，其余链正常
        sic = _symbol_ic({"能源": HIGH, "聚酯": WEAK, "油化工": WEAK})
        sic["MA0"] = 0.20
        sic["UR0"] = 0.21  # 缺 SA0 → 煤化工 n=2
        prof = compute_subchain_profile("f", sic, CHAINS)
        assert prof.chain_stats["煤化工"].n_symbols == 2
        assert prof.chain_stats["煤化工"].effective is False
        assert prof.chain_stats["能源"].effective is True

    def test_negative_ic_symmetric(self):
        # 全负 IC：取 |mean| 判定，方向由因子符号承载
        st = _single_chain_stat([-0.20, -0.21, -0.22])
        assert st.effective is True
        assert st.mean_ic < 0


# ── P0/P1：scope 派生 ───────────────────────────────────────────


class TestScopeDerivation:
    def test_scope_single_chain(self):
        sic = _symbol_ic({"能源": WEAK, "聚酯": WEAK, "油化工": HIGH, "煤化工": WEAK})
        prof = compute_subchain_profile("f", sic, CHAINS)
        assert prof.subchain_scope == ["油化工"]
        assert prof.subchain_specific is True

    def test_scope_multi_chain(self):
        sic = _symbol_ic({"能源": HIGH, "聚酯": HIGH, "油化工": WEAK, "煤化工": WEAK})
        prof = compute_subchain_profile("f", sic, CHAINS)
        assert set(prof.subchain_scope) == {"能源", "聚酯"}
        assert prof.subchain_specific is False

    def test_scope_all(self):
        sic = _symbol_ic({"能源": HIGH, "聚酯": HIGH, "油化工": HIGH, "煤化工": WEAK})
        prof = compute_subchain_profile("f", sic, CHAINS)
        assert prof.subchain_scope == "all"
        assert prof.subchain_specific is False

    def test_scope_unknown(self):
        prof = compute_subchain_profile("f", {}, CHAINS)
        assert prof.subchain_scope == "unknown"
        assert prof.subchain_specific is False
        assert prof.chain_stats == {}

    def test_unknown_chain_key(self):
        # 子链符号与 symbol_ic 无交集 → ics=[] → n=0 拦截，不抛异常
        prof = compute_subchain_profile("f", {"NO_SYM": 0.2}, {"未知链": ["GHOST"]})
        st = prof.chain_stats["未知链"]
        assert st.effective is False
        assert st.n_symbols == 0


# ── A2 落库（build_subchain_metadata）─────────────────────────


class TestA2Metadata:
    def test_metadata_keys_complete(self):
        sic = _symbol_ic({"能源": HIGH, "聚酯": WEAK, "油化工": WEAK, "煤化工": WEAK})
        md = build_subchain_metadata("f", sic, CHAINS)
        assert set(md.keys()) == {"subchain_ic_profile", "subchain_scope", "subchain_specific"}
        assert md["subchain_scope"] == ["能源"]
        assert md["subchain_specific"] is True
        # 画像含全部统计字段（A2 落库契约）
        for chain, stat in md["subchain_ic_profile"].items():
            for field in ("n_symbols", "mean_ic", "std_ic", "t_stat", "p_value", "effective"):
                assert field in stat

    def test_metadata_inf_json_safe(self):
        # std=0 兜底场景 t=inf → 落库序列化为 None（DuckDB JSON 安全）
        import json

        md = build_subchain_metadata("f", {"SC0": 0.20, "FU0": 0.20, "BU0": 0.20}, {"能源": ["SC0", "FU0", "BU0"]})
        s = json.dumps(md, ensure_ascii=False)  # 不抛异常即 JSON 安全
        assert "Infinity" not in s
        assert md["subchain_ic_profile"]["能源"]["t_stat"] is None
        assert md["subchain_ic_profile"]["能源"]["effective"] is True

    def test_metadata_empty_symbol_ic(self):
        md = build_subchain_metadata("f", {}, CHAINS)
        assert md["subchain_scope"] == "unknown"
        assert md["subchain_specific"] is False
        assert md["subchain_ic_profile"] == {}

    def test_metadata_chain_symbols_default_lazy(self):
        # chain_symbols=None 懒加载 ENERGY_CHAIN_SUB_SYMBOLS（四子链 12 品种）
        sic = _symbol_ic({"能源": HIGH, "聚酯": WEAK, "油化工": WEAK, "煤化工": WEAK})
        md = build_subchain_metadata("f", sic)
        assert set(md["subchain_ic_profile"].keys()) == {"能源", "聚酯", "油化工", "煤化工"}


# ── P1/P2：契约与统计护栏 ──────────────────────────────────────


class TestContractAndGuardrail:
    def test_profile_contract(self):
        # 输出契约完整（A2 落库字段就绪断言；DB 落库接线后补充持久化用例）
        sic = _symbol_ic({"能源": HIGH, "聚酯": WEAK, "油化工": WEAK, "煤化工": WEAK})
        prof = compute_subchain_profile("f", sic, CHAINS)
        assert prof.factor_id == "f"
        for st in prof.chain_stats.values():
            for field in ("n_symbols", "mean_ic", "std_ic", "t_stat", "p_value", "effective"):
                assert hasattr(st, field)

    def test_random_noise_false_positive(self):
        # 统计护栏：随机噪声（mean≈0，σ=0.01=半门槛）下 subchain_specific 比例 < 5%
        # σ 与门槛同比校准（旧 0.10 → σ0.05；新 0.02 → σ0.01），保持护栏语义
        rng = np.random.default_rng(42)
        n_specific = 0
        n_trials = 1000
        for _ in range(n_trials):
            sic = {
                sym: float(rng.normal(0.0, 0.01))
                for syms in CHAINS.values()
                for sym in syms
            }
            prof = compute_subchain_profile("f", sic, CHAINS)
            if prof.subchain_specific:
                n_specific += 1
        assert n_specific / n_trials < 0.05
