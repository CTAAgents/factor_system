"""tests/factor_engine/test_gap074_operator_diversity.py — 算子演化多样性退化修复测试。

GAP-074 (v2.100.0) 验收：
    P0-1 UCT 失败反馈：演化失败/运行时校验失败/预筛失败父因子 visits 递增，
          `_select_parent_uct` 切换至下一未访问父因子（消除选择坍缩）。
    P0-2 种子注入代际：同父因子不同 generation → 引擎 random_seed 不同；
          同父因子同 generation → 种子一致（可复现性保留）。
"""

from __future__ import annotations

import pytest

from fts.factor_engine.contracts import FactorKind
from fts.factor_engine.evolution_loop import EvolutionLoop
from fts.factor_engine.operator_evolution import (
    OperatorEvolutionEngine,
    OperatorEvolutionResult,
)


def _make_parents() -> list[dict]:
    """三个父因子（factor_id 有序，供 UCT 选择测试）。"""
    return [
        {"factor_id": "fct_p1", "name": "p1"},
        {"factor_id": "fct_p2", "name": "p2"},
        {"factor_id": "fct_p3", "name": "p3"},
    ]


def _fake_engine_evolve(self) -> OperatorEvolutionResult:
    """Mock evolve：返回固定合法表达式（供 best_factor_program 消费）。"""
    return OperatorEvolutionResult(
        best_expression="rank(ts_zscore(close, 60))",
        best_fitness=0.5,
        best_ic=0.1,
        best_sharpe=0.5,
        generations_completed=1,
        history=[],
        total_evaluations=1,
    )


# ── P0-1: UCT 失败反馈 ─────────────────────────────────────


def test_uct_failure_marks_visit_and_switches_parent(sample_ohlcv, forward_returns):
    """失败父因子 visits 递增后，`_select_parent_uct` 切换到下一未访问父因子。"""
    loop = EvolutionLoop(data=sample_ohlcv, forward_returns=forward_returns, market="stock")
    parents = _make_parents()

    # 初始：全部未访问 → 优先探索 parents[0]
    assert loop._select_parent_uct(parents)["factor_id"] == "fct_p1"

    # p1 演化失败 → UCT 失败反馈（visits+1，无正奖励）
    loop._update_uct_failure(parents[0])

    # 下一轮：p1 visits>0，p2 未访问优先 → 切换父因子（消除选择坍缩）
    assert loop._select_parent_uct(parents)["factor_id"] == "fct_p2"

    # 连续失败 → 逐轮轮换，最终覆盖全部父因子
    loop._update_uct_failure(parents[1])
    assert loop._select_parent_uct(parents)["factor_id"] == "fct_p3"
    loop._update_uct_failure(parents[2])
    # 全部已访问 → 回落到 UCB 计算（不抛错、返回某父因子）
    assert loop._select_parent_uct(parents)["factor_id"] in {"fct_p1", "fct_p2", "fct_p3"}


def test_uct_failure_does_not_grant_reward(sample_ohlcv, forward_returns):
    """失败反馈只累计 visits，不授予正奖励（与评估通过路径区分）。"""
    loop = EvolutionLoop(data=sample_ohlcv, forward_returns=forward_returns, market="stock")
    parent = _make_parents()[0]

    loop._update_uct_failure(parent)

    stats = loop._uct_stats["fct_p1"]
    assert stats["visits"] == 1
    assert stats["total_reward"] == 0.0


# ── P0-2: 种子注入代际 ─────────────────────────────────────


@pytest.fixture
def seed_capturing_engine(monkeypatch, sample_ohlcv, forward_returns):
    """EvolutionLoop + 捕获 random_seed 的引擎（evolve 已 mock）。"""
    captured: list[int] = []

    real_init = OperatorEvolutionEngine.__init__

    def fake_init(self, *args, **kwargs):
        captured.append(kwargs["config"].random_seed)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(OperatorEvolutionEngine, "__init__", fake_init)
    monkeypatch.setattr(OperatorEvolutionEngine, "evolve", _fake_engine_evolve)

    loop = EvolutionLoop(data=sample_ohlcv, forward_returns=forward_returns, market="stock")
    loop.captured_seeds = captured  # type: ignore[attr-defined]
    return loop


def test_operator_engine_seed_differs_by_generation(seed_capturing_engine):
    """同父因子不同 generation → 引擎 random_seed 不同（消除重复空转）。"""
    loop = seed_capturing_engine
    parent = {"factor_id": "fct_gtja_094", "name": "gtja_094"}

    factor1 = loop._try_operator_engine_evolution(parent, generation=1, trace_id="gap074-001")
    factor2 = loop._try_operator_engine_evolution(parent, generation=2, trace_id="gap074-002")

    assert factor1 is not None and factor2 is not None
    assert factor1["kind"] == FactorKind.OPERATOR
    assert len(loop.captured_seeds) == 2  # type: ignore[attr-defined]
    assert loop.captured_seeds[0] != loop.captured_seeds[1]  # type: ignore[attr-defined]


def test_operator_engine_seed_reproducible_same_generation(seed_capturing_engine):
    """同父因子同 generation → 种子一致（确定性/可复现性保留）。"""
    loop = seed_capturing_engine
    parent = {"factor_id": "fct_gtja_094", "name": "gtja_094"}

    loop._try_operator_engine_evolution(parent, generation=5, trace_id="gap074-003")
    loop._try_operator_engine_evolution(parent, generation=5, trace_id="gap074-004")

    assert len(loop.captured_seeds) == 2  # type: ignore[attr-defined]
    assert loop.captured_seeds[0] == loop.captured_seeds[1]  # type: ignore[attr-defined]
