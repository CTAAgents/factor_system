"""tests/factor_engine/test_symbolic_regression.py — 符号回归补充搜索器测试（GAP-I204 二期）。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.feature_ops import OperatorRegistry
from fts.factor_engine.symbolic_regression import (
    SymbolicCandidate,
    SymbolicRegressionConfig,
    SymbolicRegressionResult,
    SymbolicRegressionSearcher,
)


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """合成测试数据（含可预测 target）。"""
    rng = np.random.default_rng(42)
    n = 200
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    target = np.roll(np.diff(close, prepend=close[0]), -1)
    target = target / (close + 1e-9)
    return pd.DataFrame(
        {
            "open": close + rng.normal(0, 0.1, n),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": rng.uniform(1e4, 1e6, n),
            "forward_return": target,
        }
    )


@pytest.fixture
def registry() -> OperatorRegistry:
    return OperatorRegistry()


def _make_searcher(sample_data, **cfg_kw) -> SymbolicRegressionSearcher:
    config = SymbolicRegressionConfig(**cfg_kw)
    return SymbolicRegressionSearcher(
        operator_registry=OperatorRegistry(),
        data_panel=sample_data,
        target_col="forward_return",
        config=config,
    )


class TestSymbolicRegressionConfig:
    def test_defaults(self):
        cfg = SymbolicRegressionConfig()
        assert cfg.max_depth == 4
        assert cfg.beam_width == 10
        assert cfg.max_candidates == 200
        assert cfg.fitness_metric == "multi_objective"

    def test_custom(self):
        cfg = SymbolicRegressionConfig(max_depth=2, beam_width=3, max_candidates=50)
        assert cfg.max_depth == 2
        assert cfg.beam_width == 3


class TestSearcherInit:
    def test_init_excludes_target_col(self, sample_data):
        searcher = _make_searcher(sample_data)
        assert "forward_return" not in searcher._columns
        assert "close" in searcher._columns

    def test_internal_gp_reuses_multi_objective(self, sample_data):
        searcher = _make_searcher(sample_data)
        assert searcher._gp._config.fitness_metric == "multi_objective"
        assert searcher._gp._config.turnover_penalty == 0.3


class TestCandidateGeneration:
    def test_wrap_unary(self):
        from fts.factor_engine.gp_evolver import TreeNode

        leaf = TreeNode(operand="close", is_terminal=True)
        tree = SymbolicRegressionSearcher._wrap_unary(leaf, "rank")
        assert tree.op_name == "rank"
        assert tree.children[0].operand == "close"

    def test_combine_binary(self):
        from fts.factor_engine.gp_evolver import TreeNode

        left = TreeNode(operand="close", is_terminal=True)
        right = TreeNode(operand="high", is_terminal=True)
        tree = SymbolicRegressionSearcher._combine_binary(left, right, "add")
        assert tree.op_name == "add"
        assert len(tree.children) == 2


class TestSearch:
    def test_search_returns_result(self, registry, sample_data):
        searcher = _make_searcher(sample_data, max_depth=3, beam_width=5, max_candidates=60)
        result = searcher.search()
        assert isinstance(result, SymbolicRegressionResult)
        assert result.total_evaluated > 0
        assert isinstance(result.elapsed_ms, float)

    def test_candidates_sorted_by_fitness(self, sample_data):
        searcher = _make_searcher(sample_data, max_depth=3, beam_width=5, max_candidates=60)
        result = searcher.search()
        fitnesses = [c.fitness for c in result.candidates]
        assert fitnesses == sorted(fitnesses, reverse=True)

    def test_candidate_fields_populated(self, sample_data):
        searcher = _make_searcher(sample_data, max_depth=2, beam_width=4, max_candidates=40)
        result = searcher.search()
        if result.candidates:
            cand = result.candidates[0]
            assert isinstance(cand, SymbolicCandidate)
            assert len(cand.expression) > 0
            assert cand.depth >= 1
            assert cand.size >= 2

    def test_best_is_top_candidate(self, sample_data):
        searcher = _make_searcher(sample_data, max_depth=2, beam_width=4, max_candidates=40)
        result = searcher.search()
        if result.candidates:
            assert result.best is not None
            assert result.best.expression == result.candidates[0].expression

    def test_deterministic_reproducible(self, sample_data):
        """固定种子 → 两次搜索完全一致（可复现）。"""
        r1 = _make_searcher(sample_data, max_depth=3, beam_width=5, max_candidates=60).search()
        r2 = _make_searcher(sample_data, max_depth=3, beam_width=5, max_candidates=60).search()
        assert [c.expression for c in r1.candidates] == [c.expression for c in r2.candidates]
        assert [c.fitness for c in r1.candidates] == [c.fitness for c in r2.candidates]

    def test_max_depth_respected(self, sample_data):
        searcher = _make_searcher(sample_data, max_depth=2, beam_width=3, max_candidates=80)
        result = searcher.search()
        # 树深度 ≤ 2（一元包装 + 二元组合结构）
        assert all(c.depth <= 2 for c in result.candidates)

    def test_beam_width_limits_candidates(self, sample_data):
        """beam_width=1 时每层只保留 1 个候选，候选数受限。"""
        searcher = _make_searcher(sample_data, max_depth=4, beam_width=1, max_candidates=200)
        result = searcher.search()
        # 每层最多 unary 个候选；总候选有限
        assert len(result.candidates) <= 200
        assert result.total_evaluated <= 200


class TestGpIntegration:
    def test_evolve_with_symbolic_regression_enabled(self, registry, sample_data):
        """GPEvolver 启用符号回归补充搜索 → Pareto 前沿含 symbolic 来源个体。"""
        from fts.factor_engine.gp_evolver import GPEvolver, GPEvolverConfig

        config = GPEvolverConfig(
            population_size=10,
            max_generations=2,
            fitness_metric="multi_objective",
            symbolic_regression_enabled=True,
            symbolic_max_depth=2,
            symbolic_beam_width=4,
            symbolic_max_candidates=60,
        )
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return",
            config=config,
        )
        result = gp.evolve()
        assert result.pareto_front, "应输出 Pareto 前沿"
        assert any(i.source == "symbolic" for i in result.pareto_front)

    def test_evolve_pareto_front_without_symbolic(self, registry, sample_data):
        """multi_objective 模式（未启用符号回归）仍输出 GP 侧 Pareto 前沿。"""
        from fts.factor_engine.gp_evolver import GPEvolver, GPEvolverConfig

        config = GPEvolverConfig(
            population_size=10,
            max_generations=2,
            fitness_metric="multi_objective",
        )
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return",
            config=config,
        )
        result = gp.evolve()
        assert isinstance(result.pareto_front, list)
        assert all(i.source == "gp" for i in result.pareto_front)
