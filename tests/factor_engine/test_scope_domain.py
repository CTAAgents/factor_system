"""
tests/factor_engine/test_scope_domain.py — scope 域评估模块测试（P0 方案）

覆盖：resolver 链映射（futures 17 链 / energy 4 子链 YAML）/ evaluator 域内
统计量（IC 聚合方向对齐、Sharpe、子期一致）/ guard 真伪鉴别三护栏 / hooks
接入桩（attach 开关、域内门禁）/ portfolio_loop ENERGY_CHAIN_SUB_SYMBOLS 加载。
"""

from __future__ import annotations

import math
import os

import pytest

from fts.factor_engine.scope_domain.evaluator import (
    aggregate_domain,
    compute_domain_stats,
    domain_sharpe,
    evaluate_symbol_scope,
    subperiod_consistency,
)
from fts.factor_engine.scope_domain.guard import permutation_p, run_scope_guard
from fts.factor_engine.scope_domain.hooks import (
    attach_evaluation_domain_stats,
    chain_focus_batches,
    domain_gate_decision,
    scope_domain_enabled,
    symbol_scope_guard,
)
from fts.factor_engine.scope_domain.resolver import resolve_chain_map, resolve_scope
from fts.factor_engine.scope_domain.types import DomainStats, FactorScope


# ─── resolver ───────────────────────────────────────────────


class TestResolver:
    def test_futures_chain_map(self):
        """futures 链映射 = sector_map 17 产业链（含黑色系等关键链）。"""
        m = resolve_chain_map("futures")
        assert "黑色系" in m and "有色金属" in m and "金融期货" in m
        assert "RB0" in m["黑色系"]
        assert len(m) >= 17

    def test_energy_chain_map_yaml(self):
        """energy 子链自 YAML workflows.energy.sub_symbols 加载（4 子链）。"""
        m = resolve_chain_map("energy")
        assert set(m) == {"能源", "聚酯", "油化工", "煤化工"}
        assert m["能源"] == ["SC0", "FU0", "BU0"]

    def test_resolve_scope(self):
        assert resolve_scope(None).kind == "all"
        s = resolve_scope({"kind": "chain", "chains": ["黑色系", "橡胶"]})
        assert s.kind == "chain" and "黑色系" in s.chains
        s2 = resolve_scope({"kind": "symbol", "symbols": ["RB0", "HC0"]})
        assert s2.kind == "symbol" and s2.symbols == ["RB0"]


# ─── evaluator ──────────────────────────────────────────────


class TestEvaluator:
    def test_aggregate_domain_direction_aligned(self):
        """域内 IC 按多数方向对齐：3 正 1 负 → 正向均值（abs）。"""
        scope = FactorScope(kind="all")
        ic, ratio, n = aggregate_domain(
            {"RB0": 0.04, "HC0": 0.05, "I0": 0.03, "J0": -0.01}, scope
        )
        assert n == 4
        assert ratio == pytest.approx(0.75)
        assert ic == pytest.approx((0.04 + 0.05 + 0.03 + 0.01) / 4)

    def test_aggregate_domain_chain_scope(self):
        """链级 scope 只聚合该链品种。"""
        scope = resolve_scope({"kind": "chain", "chains": ["橡胶"]})
        ic, ratio, n = aggregate_domain(
            {"RU0": 0.05, "NR0": 0.06, "BR0": 0.04, "RB0": 0.99}, scope
        )
        assert n == 3  # RB0 不在橡胶链
        assert ic == pytest.approx(0.05)

    def test_aggregate_domain_empty(self):
        ic, ratio, n = aggregate_domain({}, FactorScope(kind="all"))
        assert ic is None and n == 0

    def test_domain_sharpe(self):
        daily = [0.01] * 30 + [-0.005] * 10
        s = domain_sharpe(daily)
        assert s is not None and s > 0
        assert domain_sharpe([0.01]) is None  # 样本不足

    def test_subperiod_consistency(self):
        """三段同号 → 1.0；方向混杂 → <1.0。"""
        assert subperiod_consistency([0.01] * 60, 3) == 1.0
        c = subperiod_consistency([0.01] * 20 + [-0.01] * 20 + [0.01] * 20, 3)
        assert c == pytest.approx(2 / 3)

    def test_compute_domain_stats_valid(self):
        stats = compute_domain_stats(
            symbol_ic={"RB0": 0.04, "HC0": 0.05},
            scope=FactorScope(kind="all"),
            daily_ic=[0.02, 0.03, 0.01, 0.02, 0.04, 0.0, 0.03, 0.05, 0.02, 0.01],
        )
        assert stats.valid and stats.ic is not None
        assert stats.n_symbols == 2


# ─── P2: 品种级评估与护栏 ──────────────────────────────────


class TestSymbolScope:
    def _mk(self, n: int = 600, ic_strength: float = 0.05) -> tuple[list[float], list[float]]:
        """构造信号-收益对：信号 ≈ 前一日收益，收益 = 信号×强度 + 噪声（稳定正 IC）。"""
        import random

        rng = random.Random(7)
        sig: list[float] = []
        ret: list[float] = []
        prev = 0.0
        for i in range(n):
            s = 0.9 * prev + rng.uniform(-0.1, 0.1)
            r = s * ic_strength + rng.uniform(-0.02, 0.02)
            sig.append(s)
            ret.append(r)
            prev = s
        return sig, ret

    def test_evaluate_symbol_scope_valid(self):
        sig, ret = self._mk(600)
        stats = evaluate_symbol_scope(sig, ret, "RB0")
        assert stats.valid and stats.scope.kind == "symbol"
        assert stats.n_dates >= 590 and stats.ic is not None and stats.ic > 0

    def test_evaluate_symbol_scope_insufficient(self):
        stats = evaluate_symbol_scope([1.0, 2.0, 3.0], [0.1, 0.2, 0.3], "RB0")
        assert not stats.valid

    def test_symbol_scope_guard_pass(self):
        sig, ret = self._mk(600)
        out = symbol_scope_guard(signal=sig, forward_returns=ret, symbol="RB0")
        assert out["stats"]["valid"] and out["stats"]["guard_passed"] is True
        assert out["guard"]["passed"] is True

    def test_symbol_scope_guard_fail_noise(self):
        import random

        rng = random.Random(3)
        sig = [rng.uniform(-1, 1) for _ in range(600)]
        ret = [rng.uniform(-0.05, 0.05) for _ in range(600)]
        out = symbol_scope_guard(signal=sig, forward_returns=ret, symbol="RB0")
        # 噪声：样本窗足但子期一致/IC 不稳 → 护栏不过（宁漏标）
        assert out["guard"]["passed"] is False or out["stats"]["valid"] is False


# ─── P3: 品种特有字段通道 ──────────────────────────────────


class TestSpecificFields:
    def test_load_registry(self):
        from fts.factor_engine.scope_domain.specific_fields import load_specific_fields

        reg = load_specific_fields()
        assert "SC0" in reg and "AU0" in reg
        # SC0 为首个可落地字段（GAP-162：外部导入 parquet）
        assert reg["SC0"]["sc_freight_premium"]["enabled"] is True
        assert reg["AU0"]["au_ag_ratio"]["enabled"] is False  # 占位未采集

    def test_enabled_has_sc0(self):
        """启用清单含 SC0（首个真实字段），AU0/EC0 仍占位。"""
        from fts.factor_engine.scope_domain.specific_fields import enabled_specific_fields

        active = enabled_specific_fields()
        assert "SC0" in active and "au_ag_ratio" not in active

    def test_enrich_degraded_not_block(self):
        """enrich_specific_fields 未启用 → 原样返回面板（不阻断）。"""
        from fts.factor_engine.scope_domain.specific_fields import enrich_specific_fields

        panel = {"RB0": object()}
        assert enrich_specific_fields(panel, enabled=False) is panel

    def test_enrich_missing_cache_degraded(self, tmp_path):
        """启用但缓存缺失 → 面板原样返回（降级不阻断、不抛异常）。"""
        import pandas as pd

        from fts.factor_engine.scope_domain.specific_fields import enrich_specific_fields

        idx = pd.date_range("2026-01-01", periods=10, freq="B")
        panel = {"SC0": pd.DataFrame({"close": range(10)}, index=idx)}
        out = enrich_specific_fields(panel, enabled=True, cache_dir=str(tmp_path))
        assert "SC0" in out and "sc_freight_premium" not in out["SC0"].columns

    def test_enrich_real_injection(self, tmp_path):
        """真实数据源通道：外部导入 parquet → 按 date 对齐注入字段列。"""
        import numpy as np
        import pandas as pd

        from fts.factor_engine.scope_domain.specific_fields import enrich_specific_fields

        idx = pd.date_range("2026-01-01", periods=10, freq="B")
        panel = {"SC0": pd.DataFrame({"close": range(10)}, index=idx)}
        # 外部采集脚本产物：date + sc_freight_premium
        cache = tmp_path / "SC0.parquet"
        pd.DataFrame(
            {"date": idx, "sc_freight_premium": np.linspace(1.0, 2.0, 10)}
        ).to_parquet(cache, index=False)
        out = enrich_specific_fields(panel, enabled=True, cache_dir=str(tmp_path))
        assert "sc_freight_premium" in out["SC0"].columns
        assert out["SC0"]["sc_freight_premium"].notna().sum() == 10

    def test_symbol_focus_prompt_block(self, monkeypatch):
        """llm bootstrap prompt 支持 symbol_focus 品种级聚焦块（P3）。"""
        from fts.llm import OpenAIClient

        monkeypatch.setattr("fts.llm.os.getenv", lambda k, d="": d)
        prompt = OpenAIClient._build_bootstrap_prompt(
            market_snapshot={"symbol_focus": "RB0 螺纹钢——钢厂利润/库存特有结构"},
            debate_gaps=[],
            max_candidates=3,
            trace_id="t",
        )
        assert "【本批聚焦品种】" in prompt and "RB0" in prompt


# ─── GAP-161: 品种级候选探测 ─────────────────────────────────


class TestDetectSymbolCandidates:
    def test_detect_strong_symbol(self):
        """一个品种时序 IC 稳定（跨子期一致）→ 被探测为候选；噪声品种不入选。"""
        import numpy as np
        import pandas as pd

        from fts.factor_engine.evaluation_chain import _detect_symbol_candidates

        rng = np.random.default_rng(7)
        n = 120
        # RB0：信号→收益强正相关（稳定）；CU0：噪声
        rb_sig = np.linspace(-1, 1, n) + rng.normal(0, 0.02, n)
        rb_ret = rb_sig * 0.05 + rng.normal(0, 0.005, n)
        cu_sig = rng.normal(0, 1, n)
        cu_ret = rng.normal(0, 0.01, n)
        sig_mat = np.stack([rb_sig, cu_sig], axis=1)
        ret_mat = np.stack([rb_ret, cu_ret], axis=1)
        symbol_ic = {"RB0": 0.35, "CU0": 0.01}
        cands = _detect_symbol_candidates(sig_mat, ret_mat, ["RB0", "CU0"], symbol_ic)
        assert "RB0" in cands and "CU0" not in cands

    def test_detect_noise_excluded(self):
        """全噪声品种（IC 低）→ 无候选（宁漏标）。"""
        import numpy as np

        from fts.factor_engine.evaluation_chain import _detect_symbol_candidates

        rng = np.random.default_rng(3)
        sig_mat = rng.normal(0, 1, (120, 2))
        ret_mat = rng.normal(0, 0.01, (120, 2))
        assert _detect_symbol_candidates(sig_mat, ret_mat, ["A", "B"], {"A": 0.005, "B": 0.003}) == []


# ─── guard ──────────────────────────────────────────────────


class TestGuard:
    def test_permutation_p_significant(self):
        """稳定正向 IC 序列 → 置换显著（p 小）。"""
        daily = [0.02 + 0.001 * (i % 7) for i in range(200)]
        assert permutation_p(daily, n=100) < 0.05

    def test_permutation_p_noise(self):
        """随机噪声 IC → 置换不显著（p 大）。"""
        import random

        rng = random.Random(42)
        daily = [rng.uniform(-0.01, 0.01) for _ in range(200)]
        assert permutation_p(daily, n=100) >= 0.05

    def test_guard_pass(self):
        stats = DomainStats(n_symbols=1, n_dates=600, subperiod_consistency=1.0, valid=True)
        r = run_scope_guard(stats=stats, daily_ic=[0.02] * 300, cfg=None)
        assert r.passed

    def test_guard_sample_window(self):
        """样本窗不足 → 护栏不过（宁漏标）。"""
        stats = DomainStats(n_symbols=1, n_dates=100, subperiod_consistency=1.0, valid=True)
        r = run_scope_guard(stats=stats, daily_ic=[0.02] * 50, cfg=None)
        assert not r.passed and any("样本窗" in x for x in r.reasons)


# ─── hooks ──────────────────────────────────────────────────


class TestHooks:
    def test_scope_domain_enabled_default(self):
        """开关默认开启（用户决策：不设灰度关闭期）。"""
        assert scope_domain_enabled()

    def test_attach_evaluation_domain_stats(self, monkeypatch):
        monkeypatch.setenv("FTS_SCOPE_DOMAIN_ENABLED", "1")
        ev = {}
        out = attach_evaluation_domain_stats(ev, {"RB0": 0.04, "HC0": 0.05})
        assert "domain_stats" in out and out["domain_stats"]["n_symbols"] == 2

    def test_attach_disabled_noop(self, monkeypatch):
        monkeypatch.setenv("FTS_SCOPE_DOMAIN_ENABLED", "0")
        ev = {"ic": 0.03}
        out = attach_evaluation_domain_stats(ev, {"RB0": 0.04})
        assert "domain_stats" not in out and out["ic"] == 0.03

    def test_domain_gate_decision(self, monkeypatch):
        monkeypatch.setenv("FTS_SCOPE_DOMAIN_ENABLED", "1")
        stats = {"scope": {"kind": "all"}, "n_symbols": 2, "ic": 0.04,
                 "sharpe": 1.2, "valid": True}
        # 域内 IC/Sharpe 达标 → approved
        assert domain_gate_decision(
            ic=0.01, sharpe=0.1, domain_stats=stats, min_ic=0.02, min_sharpe=0.5
        ) == "approved"
        # 域内不达标 → None（走全链）
        stats_low = dict(stats, ic=0.01, sharpe=0.1)
        assert (
            domain_gate_decision(
                ic=0.01, sharpe=0.1, domain_stats=stats_low, min_ic=0.02, min_sharpe=0.5
            )
            is None
        )

    def test_chain_focus_batches(self):
        batches = chain_focus_batches("energy", 12)
        assert len(batches) == 4 and all(b[1] >= 1 for b in batches)
        assert batches[0][0] == "能源"


# ─── portfolio_loop 兼容（ENERGY_CHAIN_SUB_SYMBOLS 自 resolver 加载）───


class TestPortfolioLoopCompat:
    def test_energy_sub_symbols_from_resolver(self):
        from fts.factor_engine.portfolio_loop import ENERGY_CHAIN_SUB_SYMBOLS

        assert set(ENERGY_CHAIN_SUB_SYMBOLS) == {"能源", "聚酯", "油化工", "煤化工"}
