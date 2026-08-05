"""tests/factor_engine/test_gp_evolver.py — GP 演化搜索引擎测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.feature_ops import OperatorRegistry
from fts.factor_engine.gp_evolver import (
    ExpressionTree,
    FitnessResult,
    GPEvolver,
    GPEvolverConfig,
    GPEvolveResult,
    TreeNode,
    _evaluate_tree,
    _get_operator_arity,
    _random_terminal,
    _random_tree,
    _tree_depth,
    _tree_size,
    _tree_to_expression,
    tree_to_factor_program,
)


# ─── Helpers ──────────────────────────────────────────────


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """合成测试数据。"""
    np.random.seed(42)
    n = 200
    return pd.DataFrame({
        "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
        "high": 105 + np.cumsum(np.random.randn(n) * 0.5),
        "low": 95 + np.cumsum(np.random.randn(n) * 0.5),
        "volume": np.random.randint(1000, 10000, n).astype(float),
        "forward_return_20d": np.random.randn(n) * 0.02,
    })


@pytest.fixture
def registry() -> OperatorRegistry:
    return OperatorRegistry()


# ─── TreeNode / ExpressionTree ────────────────────────────


class TestTreeNode:
    def test_terminal_node(self):
        node = TreeNode(operand=42.0, is_terminal=True)
        assert node.is_terminal
        assert node.operand == 42.0
        assert node.children == []

    def test_operator_node(self):
        node = TreeNode(
            op_name="add",
            children=[TreeNode(operand=1.0, is_terminal=True)],
            is_terminal=False,
        )
        assert not node.is_terminal
        assert node.op_name == "add"
        assert len(node.children) == 1


# ─── Tree generation ──────────────────────────────────────


class TestTreeGeneration:
    def test_random_terminal_column(self, sample_data):
        cols = list(sample_data.columns)
        node = _random_terminal(cols)
        assert node.is_terminal
        assert node.operand in cols or isinstance(node.operand, float)

    def test_random_terminal_constant(self, sample_data, monkeypatch):
        monkeypatch.setattr("random.random", lambda: 0.9)
        cols = list(sample_data.columns)
        node = _random_terminal(cols)
        assert node.is_terminal
        assert isinstance(node.operand, float)

    def test_random_tree_depth(self, registry, sample_data):
        cols = list(sample_data.columns)
        tree = _random_tree(registry, cols, max_depth=3)
        depth = _tree_depth(tree)
        assert 0 <= depth <= 4  # allow some flexibility

    def test_random_tree_expression(self, registry, sample_data):
        cols = list(sample_data.columns)
        tree = _random_tree(registry, cols, max_depth=3)
        expr = _tree_to_expression(tree)
        assert len(expr) > 0

    def test_tree_depth(self, registry, sample_data):
        cols = list(sample_data.columns)
        tree = _random_tree(registry, cols, max_depth=3)
        depth = _tree_depth(tree)
        assert depth >= 0

    def test_tree_size(self, registry, sample_data):
        cols = list(sample_data.columns)
        tree = _random_tree(registry, cols, max_depth=3)
        size = _tree_size(tree)
        assert size >= 1


# ─── Operator arity ──────────────────────────────────────


class TestOperatorArity:
    def test_binary_ops(self):
        assert _get_operator_arity("add") == 2
        assert _get_operator_arity("sub") == 2
        assert _get_operator_arity("mul") == 2
        assert _get_operator_arity("div") == 2

    def test_unary_ops(self):
        assert _get_operator_arity("rank") == 1
        assert _get_operator_arity("zscore") == 1
        assert _get_operator_arity("ts_mean") == 1

    def test_ternary_ops(self):
        assert _get_operator_arity("if_then_else") == 3
        assert _get_operator_arity("conditional_weight") == 3

    def test_unknown_op(self):
        assert _get_operator_arity("nonexistent") == 2


# ─── Tree evaluation ──────────────────────────────────────


class TestTreeEvaluation:
    def test_evaluate_terminal_column(self, registry, sample_data):
        tree = TreeNode(operand="close", is_terminal=True)
        result = _evaluate_tree(tree, registry, sample_data)
        assert result is not None
        assert len(result) == len(sample_data)

    def test_evaluate_constant(self, registry, sample_data):
        tree = TreeNode(operand=5.0, is_terminal=True)
        result = _evaluate_tree(tree, registry, sample_data)
        assert result is not None
        assert (result == 5.0).all()

    def test_evaluate_add(self, registry, sample_data):
        tree = TreeNode(
            op_name="add",
            children=[
                TreeNode(operand="close", is_terminal=True),
                TreeNode(operand="high", is_terminal=True),
            ],
            is_terminal=False,
        )
        result = _evaluate_tree(tree, registry, sample_data)
        assert result is not None
        expected = sample_data["close"] + sample_data["high"]
        assert np.allclose(result.values, expected.values)

    def test_evaluate_mul(self, registry, sample_data):
        tree = TreeNode(
            op_name="mul",
            children=[
                TreeNode(operand="close", is_terminal=True),
                TreeNode(operand="volume", is_terminal=True),
            ],
            is_terminal=False,
        )
        result = _evaluate_tree(tree, registry, sample_data)
        assert result is not None
        expected = sample_data["close"] * sample_data["volume"]
        assert np.allclose(result.values, expected.values)

    def test_evaluate_rank(self, registry, sample_data):
        tree = TreeNode(
            op_name="rank",
            children=[TreeNode(operand="close", is_terminal=True)],
            is_terminal=False,
        )
        result = _evaluate_tree(tree, registry, sample_data)
        assert result is not None
        assert result.max() <= 1.0
        assert result.min() >= 0.0

    def test_evaluate_ts_mean(self, registry, sample_data):
        tree = TreeNode(
            op_name="ts_mean",
            children=[
                TreeNode(operand="close", is_terminal=True),
                TreeNode(operand=10, is_terminal=True),
            ],
            is_terminal=False,
        )
        result = _evaluate_tree(tree, registry, sample_data)
        assert result is not None

    def test_evaluate_unknown_op(self, registry, sample_data):
        tree = TreeNode(
            op_name="unknown_op",
            children=[TreeNode(operand="close", is_terminal=True)],
            is_terminal=False,
        )
        result = _evaluate_tree(tree, registry, sample_data)
        assert result is None


# ─── GPEvolver ────────────────────────────────────────────


class TestGPEvolver:
    def test_init(self, registry, sample_data):
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
        )
        assert gp._config.population_size == 200

    def test_init_custom_config(self, registry, sample_data):
        config = GPEvolverConfig(
            population_size=50,
            max_generations=5,
            max_tree_depth=3,
        )
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
            config=config,
        )
        assert gp._config.population_size == 50

    def test_evolve(self, registry, sample_data):
        config = GPEvolverConfig(
            population_size=20,
            max_generations=3,
            max_tree_depth=3,
            tournament_size=2,
            elitism_size=2,
        )
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
            config=config,
        )
        result = gp.evolve()

        assert isinstance(result, GPEvolveResult)
        assert result.best_fitness > -float("inf")
        assert len(result.history) == 3
        assert result.total_evaluations > 0
        assert len(result.best_expression) > 0

    def test_evolve_with_generated_expressions(self, registry, sample_data):
        config = GPEvolverConfig(
            population_size=15,
            max_generations=2,
            max_tree_depth=3,
        )
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
            config=config,
        )
        result = gp.evolve()

        # 验证表达式可被解析
        assert "(" in result.best_expression or result.best_expression in sample_data.columns

    def test_initialize_population(self, registry, sample_data):
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
        )
        population = gp._initialize_population(10)
        assert len(population) == 10
        for tree in population:
            assert isinstance(tree, ExpressionTree)
            assert tree.size > 0
            assert len(tree.expression) > 0

    def test_evaluate_fitness(self, registry, sample_data):
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
        )
        tree = ExpressionTree(
            root=TreeNode(operand="close", is_terminal=True),
            expression="close",
        )
        result = gp._evaluate_fitness(tree)
        assert isinstance(result, FitnessResult)
        assert result.fitness >= -10.0

    def test_crossover(self, registry, sample_data):
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
        )
        parent1 = ExpressionTree(
            root=TreeNode(operand="close", is_terminal=True),
            expression="close",
        )
        parent2 = ExpressionTree(
            root=TreeNode(operand="high", is_terminal=True),
            expression="high",
        )
        child = gp._crossover(parent1, parent2)
        assert isinstance(child, ExpressionTree)
        assert len(child.expression) > 0

    def test_mutate(self, registry, sample_data):
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
        )
        tree = ExpressionTree(
            root=TreeNode(operand="close", is_terminal=True),
            expression="close",
        )
        mutated = gp._mutate(tree)
        assert isinstance(mutated, ExpressionTree)
        assert len(mutated.expression) > 0

    def test_tournament_select(self, registry, sample_data):
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
        )
        population = gp._initialize_population(5)
        for tree in population:
            tree.fitness = np.random.randn()
        selected = gp._tournament_select(population)
        assert selected in population

    def test_copy_tree(self):
        root = TreeNode(
            op_name="add",
            children=[
                TreeNode(operand="close", is_terminal=True),
                TreeNode(operand=1.0, is_terminal=True),
            ],
            is_terminal=False,
        )
        copied = GPEvolver._copy_tree(root)
        assert copied.op_name == "add"
        assert len(copied.children) == 2
        # 修改副本不影响原树
        copied.children[0].operand = "modified"
        assert root.children[0].operand == "close"

    def test_collect_internal_nodes(self):
        root = TreeNode(
            op_name="add",
            children=[
                TreeNode(operand="close", is_terminal=True),
                TreeNode(
                    op_name="rank",
                    children=[TreeNode(operand="high", is_terminal=True)],
                    is_terminal=False,
                ),
            ],
            is_terminal=False,
        )
        nodes = GPEvolver._collect_internal_nodes(root)
        assert len(nodes) >= 1
        assert root in nodes

    def test_evolve_population(self, registry, sample_data):
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
            config=GPEvolverConfig(population_size=10, elitism_size=2),
        )
        population = gp._initialize_population(10)
        for tree in population:
            tree.fitness = np.random.randn()
        new_population = gp._evolve_population(population)
        assert len(new_population) == 10

    def test_fitness_metric_ic(self, registry, sample_data):
        config = GPEvolverConfig(
            population_size=10,
            max_generations=2,
            fitness_metric="ic",
        )
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
            config=config,
        )
        result = gp.evolve()
        # IC metric uses abs(ic) so should be non-negative for valid factors
        assert result.best_fitness >= -10.0

    def test_fitness_metric_sharpe(self, registry, sample_data):
        config = GPEvolverConfig(
            population_size=10,
            max_generations=2,
            fitness_metric="sharpe",
        )
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
            config=config,
        )
        result = gp.evolve()
        assert isinstance(result.best_fitness, float)


# ─── Edge cases ──────────────────────────────────────────


class TestGPEvolverEdgeCases:
    def test_evolve_with_small_data(self, registry):
        """测试小数据集。"""
        small_data = pd.DataFrame({
            "close": [100.0, 101.0],
            "forward_return_20d": [0.01, -0.01],
        })
        config = GPEvolverConfig(
            population_size=5,
            max_generations=2,
        )
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=small_data,
            target_col="forward_return_20d",
            config=config,
        )
        result = gp.evolve()
        assert result is not None
        assert result.generations_completed > 0

    def test_evaluate_fitness_nan_handling(self, registry, sample_data):
        """测试 NaN 处理。"""
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
        )
        # 创建一个全 NaN 的树
        tree = ExpressionTree(
            root=TreeNode(operand=float("nan"), is_terminal=True),
            expression="nan",
        )
        result = gp._evaluate_fitness(tree)
        assert result.fitness <= 0  # 应该返回惩罚分数

    def test_crossover_single_child(self, registry, sample_data):
        """测试只有一个子节点的交叉。"""
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
        )
        parent1 = ExpressionTree(
            root=TreeNode(operand="close", is_terminal=True),
            expression="close",
        )
        parent2 = ExpressionTree(
            root=TreeNode(
                op_name="rank",
                children=[TreeNode(operand="high", is_terminal=True)],
                is_terminal=False,
            ),
            expression="rank(high)",
        )
        child = gp._crossover(parent1, parent2)
        assert isinstance(child, ExpressionTree)


# ─── tree_to_factor_program ──────────────────────────────


class TestTreeToFactorProgram:
    """表达式树 → FactorProgram 转换测试。"""

    def test_simple_tree(self):
        tree = ExpressionTree(
            root=TreeNode(operand="close", is_terminal=True),
            depth=0,
            size=1,
            expression="close",
            fitness=0.5,
        )
        result = tree_to_factor_program(tree)
        assert "factor_id" in result
        assert result["factor_id"].startswith("fct_")
        assert "code" in result
        assert "expression" in result
        assert result["expression"] == "close"
        assert result["fitness"] == 0.5
        assert result["source"] == "gp_evolution"

    def test_complex_tree(self):
        tree = ExpressionTree(
            root=TreeNode(
                op_name="add",
                children=[
                    TreeNode(operand="close", is_terminal=True),
                    TreeNode(operand="high", is_terminal=True),
                ],
                is_terminal=False,
            ),
            depth=1,
            size=3,
            expression="add(close, high)",
            fitness=0.8,
        )
        result = tree_to_factor_program(tree)
        assert result["tree_depth"] == 1
        assert result["tree_size"] == 3
        assert result["expression"] == "add(close, high)"
        assert "compute" in result["code"]

    def test_unique_factor_ids(self):
        tree1 = ExpressionTree(
            root=TreeNode(operand="close", is_terminal=True),
            depth=0,
            size=1,
            expression="close",
            fitness=0.5,
        )
        tree2 = ExpressionTree(
            root=TreeNode(operand="high", is_terminal=True),
            depth=0,
            size=1,
            expression="high",
            fitness=0.5,
        )
        result1 = tree_to_factor_program(tree1)
        result2 = tree_to_factor_program(tree2)
        assert result1["factor_id"] != result2["factor_id"]

    def test_code_contains_expression(self):
        tree = ExpressionTree(
            root=TreeNode(
                op_name="mul",
                children=[
                    TreeNode(operand="close", is_terminal=True),
                    TreeNode(operand="volume", is_terminal=True),
                ],
                is_terminal=False,
            ),
            depth=1,
            size=3,
            expression="mul(close, volume)",
            fitness=0.9,
        )
        result = tree_to_factor_program(tree)
        assert "mul(close, volume)" in result["code"]