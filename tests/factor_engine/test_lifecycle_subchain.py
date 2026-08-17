"""
tests/factor_engine/test_lifecycle_subchain.py — 因子×子链单元粒度退化检测测试（plans/49 §C1/C2 / §五）。

覆盖:
    - compute_subchain_degradation：全部有效链衰减 degrade / 部分链衰减 scope_shrink /
      单链特异唯一链衰减 degrade / 无衰减 keep / 样本不足不误判 / 从未 effective 链不参与
    - scope_without_chains：scope 剔除失效链（闭环辅助）
    - load_subchain_lifecycle_config：settings → 参数化 SSOT

纯计算无 DB/IO，不触真实因子库。
"""

from __future__ import annotations

import pytest

from fts.factor_engine.subchain_lifecycle import (
    compute_subchain_degradation,
    load_subchain_lifecycle_config,
    scope_without_chains,
)

# 默认配置：decay_threshold=0.30 / drop_severe=0.50 / window_days=60→window=2 / min_periods=5
CHAINS: dict[str, list[str]] = {
    "能源": ["SC0", "FU0", "BU0"],
    "聚酯": ["PF0", "TA0", "EG0"],
    "油化工": ["L0", "PP0", "PG0"],
    "煤化工": ["MA0", "UR0", "SA0"],
}


def _seq(chain: str, ics: list[float], eff: bool | None = None, n: int = 0) -> list[dict]:
    """构造单链质量时序（mean_ic 序列 → 各行；evaluated_at 递增）。

    Args:
        chain: 子链名
        ics: mean_ic 序列（按时间升序）
        eff: 各期 effective（None=按 |mean_ic|≥0.10 自动推断）
        n: 期数偏移（evaluated_at 起始日期）
    """
    rows = []
    for i, ic in enumerate(ics):
        e = eff[i] if isinstance(eff, list) and i < len(eff) else (abs(ic) >= 0.10)
        rows.append(
            {
                "factor_id": "f1",
                "market": "energy",
                "chain": chain,
                "evaluated_at": f"2026-08-{10 + n + i:02d}T00:00:00",
                "mean_ic": ic,
                "effective": e,
            }
        )
    return rows


def _rows(*chains: list[dict]) -> list[dict]:
    out: list[dict] = []
    for c in chains:
        out.extend(c)
    return out


class TestComputeDegradation:
    def test_all_effective_chains_degraded(self):
        # 能源+油化工两有效链，recent 均衰减 75%（>0.50）且当前不 effective → degrade
        rows = _rows(
            _seq("能源", [0.2] * 5 + [0.05, 0.04]),  # baseline 0.2, recent 0.045 → 衰减 77%
            _seq("油化工", [0.18] * 5 + [0.04, 0.03], n=2),
        )
        r = compute_subchain_degradation(rows)
        assert r["factor_status"] == "degrade"
        assert r["degrade_chains"] == ["能源", "油化工"]

    def test_partial_chain_degraded_scope_shrink(self):
        # 能源衰减（degrade）、油化工保持 → 部分失效 → scope_shrink，剔除能源
        rows = _rows(
            _seq("能源", [0.2] * 5 + [0.05, 0.04]),
            _seq("油化工", [0.18] * 7, n=2),  # 持续 0.18 → 无衰减
        )
        r = compute_subchain_degradation(rows)
        assert r["factor_status"] == "scope_shrink"
        assert r["scope_shrink_chains"] == ["能源"]

    def test_single_chain_specific_degraded(self):
        # 单链特异因子（仅能源 effective）其唯一链衰减 → degrade
        rows = _rows(
            _seq("能源", [0.2] * 5 + [0.05, 0.04]),
            _seq("聚酯", [0.01, 0.0, 0.01, 0.0, 0.01, 0.0, 0.01], n=3),  # 从未 effective
        )
        r = compute_subchain_degradation(rows)
        assert r["factor_status"] == "degrade"
        assert r["ever_effective_chains"] == ["能源"]

    def test_single_chain_shrink_degraded(self):
        # 单链特异但仅轻微衰减（>0.30 未到 0.50）→ 唯一链 shrink → 仍 degrade（特异无备胎）
        rows = _rows(_seq("能源", [0.2] * 5 + [0.13, 0.12]))  # recent≈0.125 衰减 37%
        r = compute_subchain_degradation(rows)
        assert r["factor_status"] == "degrade"

    def test_no_decay_keep(self):
        rows = _rows(
            _seq("能源", [0.2] * 7),
            _seq("油化工", [0.18] * 7, n=2),
        )
        r = compute_subchain_degradation(rows)
        assert r["factor_status"] == "keep"
        assert r["scope_shrink_chains"] == []

    def test_insufficient_periods_keep(self):
        # 3 期 < min_periods=5 → 单元 keep（不误判）
        rows = _rows(_seq("能源", [0.2, 0.05, 0.04]))
        r = compute_subchain_degradation(rows)
        assert r["factor_status"] == "keep"
        assert r["per_chain"]["能源"]["status"] == "keep"

    def test_never_effective_chain_excluded(self):
        # 全链从未 effective → keep（不参与退化）
        rows = _rows(_seq("聚酯", [0.01, 0.0, 0.01, 0.0, 0.01, 0.0, 0.01]))
        r = compute_subchain_degradation(rows)
        assert r["factor_status"] == "keep"
        assert r["ever_effective_chains"] == []

    def test_empty_rows(self):
        r = compute_subchain_degradation([])
        assert r["factor_status"] == "keep"
        assert r["detail"] == "无质量时序"

    def test_cur_effective_keeps_even_with_slight_decay(self):
        # 轻微衰减但当前仍 effective（>0.30 阈值内）→ keep
        rows = _rows(_seq("能源", [0.2] * 5 + [0.17, 0.18]))  # recent 0.175 衰减 12.5%
        r = compute_subchain_degradation(rows)
        assert r["per_chain"]["能源"]["status"] == "keep"


class TestScopeShrink:
    def test_remove_chains(self):
        scope, changed = scope_without_chains(["能源", "聚酯", "油化工"], ["聚酯"])
        assert scope == ["能源", "油化工"]
        assert changed is True

    def test_all_scope_untouched(self):
        # "all" 语义为全链有效标记，集合差由重算画像决定（本函数不降级 "all"）
        scope, changed = scope_without_chains("all", ["聚酯"])
        assert scope == "all"
        assert changed is False

    def test_no_remove(self):
        scope, changed = scope_without_chains(["能源", "聚酯"], [])
        assert scope == ["能源", "聚酯"]
        assert changed is False


class TestConfig:
    def test_loads_from_settings(self):
        cfg = load_subchain_lifecycle_config()
        assert cfg.decay_threshold == pytest.approx(0.30)
        assert cfg.drop_severe == pytest.approx(0.50)
        assert cfg.window == 2  # window_days 60/30
        assert cfg.min_periods == 5


class TestModulationClosedLoop:
    def test_scope_shrink_flows_to_modulation_matrix(self):
        """C2 闭环：scope 剔除失效链后，47 号调制矩阵重算使失效链权重归零。"""
        from fts.factor_engine.subchain_weight import (
            SubchainWeightConfig,
            build_subchain_weights,
        )

        f = {
            "factor_id": "f1",
            "name": "f1",
            "subchain_scope": ["能源", "油化工"],  # 收缩后 scope（聚酯/煤化工已剔除）
            "subchain_ic_profile": {
                "能源": {"mean_ic": 0.2, "effective": True},
                "油化工": {"mean_ic": 0.18, "effective": True},
                "聚酯": {"mean_ic": 0.0, "effective": False},
                "煤化工": {"mean_ic": 0.01, "effective": False},
            },
        }
        mod = build_subchain_weights([f], CHAINS, SubchainWeightConfig())
        assert mod["f1"]["能源"] == 1.0
        assert mod["f1"]["油化工"] == 1.0
        assert mod["f1"]["聚酯"] == 0.0
        assert mod["f1"]["煤化工"] == 0.0
