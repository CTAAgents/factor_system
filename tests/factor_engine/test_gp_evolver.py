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
    return pd.DataFrame(
        {
            "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
            "high": 105 + np.cumsum(np.random.randn(n) * 0.5),
            "low": 95 + np.cumsum(np.random.randn(n) * 0.5),
            "volume": np.random.randint(1000, 10000, n).astype(float),
            "forward_return_20d": np.random.randn(n) * 0.02,
        }
    )


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
        small_data = pd.DataFrame(
            {
                "close": [100.0, 101.0],
                "forward_return_20d": [0.01, -0.01],
            }
        )
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
        assert "def factor_program" in result["code"]

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
        assert "_ns['mul'](_ns['close'], _ns['volume'])" in result["code"]


# ─── GP 因子代码可执行性 ──────────────────────────────────


class TestGpFactorExecutable:
    """GP 因子代码在标准执行路径下的可执行性。

    回归: 此前 tree_to_factor_program 生成 `compute` 函数约定，
    不匹配 factor_program/output 约定，_execute_factor_code 静默返回
    全零信号，导致 GP 后代因子全部被「常数信号」拦截、失败率 100% 熔断。
    """

    @staticmethod
    def _make_data() -> pd.DataFrame:
        """合成 OHLCV 数据（含 open 列，_execute_factor_code 前置访问 data['open']）。"""
        rng = np.random.default_rng(7)
        n = 200
        close = 100 + np.cumsum(rng.normal(0, 0.5, n))
        return pd.DataFrame(
            {
                "open": close + rng.normal(0, 0.1, n),
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": rng.uniform(1e4, 1e6, n),
            }
        )

    @staticmethod
    def _make_tree(expression: str) -> ExpressionTree:
        if expression == "ts_mean(close) - close":
            ts_node = TreeNode(
                op_name="ts_mean",
                children=[TreeNode(operand="close", is_terminal=True)],
                is_terminal=False,
            )
            root = TreeNode(
                op_name="sub",
                children=[ts_node, TreeNode(operand="close", is_terminal=True)],
                is_terminal=False,
            )
            return ExpressionTree(
                root=root,
                depth=2,
                size=5,
                expression=expression,
                fitness=0.5,
            )
        root = TreeNode(
            op_name="add",
            children=[
                TreeNode(operand="close", is_terminal=True),
                TreeNode(operand="high", is_terminal=True),
            ],
            is_terminal=False,
        )
        return ExpressionTree(
            root=root,
            depth=1,
            size=3,
            expression=expression,
            fitness=0.8,
        )

    def test_code_uses_standard_convention(self):
        """生成的代码应遵循 factor_program 约定，而非 compute 约定。"""
        tree = self._make_tree("ts_mean(close) - close")
        result = tree_to_factor_program(tree)
        assert "def factor_program(data, params)" in result["code"]
        assert "def compute" not in result["code"]

    def test_executable_in_backtest_pipeline(self):
        """GP 因子代码经 BacktestPipeline._execute_factor_code 应返回非零信号。"""
        from fts.factor_engine.backtest_pipeline import BacktestPipeline

        data = self._make_data()
        tree = self._make_tree("ts_mean(close) - close")
        result = tree_to_factor_program(tree)
        signal = BacktestPipeline._execute_factor_code(result["code"], data, {})
        assert isinstance(signal, np.ndarray)
        assert len(signal) == len(data)
        assert np.std(signal) > 1e-9

    def test_executable_via_factor_executor(self):
        """GP 因子代码应通过 FactorExecutor 沙箱编译并执行。"""
        from fts.factor_engine.factor_program import FactorExecutor

        data = self._make_data()
        tree = self._make_tree("ts_mean(close) - close")
        result = tree_to_factor_program(tree)
        executor = FactorExecutor(result)
        out = executor.execute(data, {})
        assert isinstance(out, np.ndarray)
        assert len(out) == len(data)
        assert np.std(out) > 1e-9

    def test_runtime_check_passes(self):
        """演化循环 _check_factor_runtime 的判定逻辑应放行 GP 因子。"""
        from fts.factor_engine.backtest_pipeline import BacktestPipeline

        data = self._make_data()
        tree = self._make_tree("ts_mean(close) - close")
        result = tree_to_factor_program(tree)
        signal = BacktestPipeline._execute_factor_code(result["code"], data, {})
        assert isinstance(signal, np.ndarray) and len(signal) == len(data)
        assert np.std(signal) >= 1e-12  # 非「常数信号」

    def test_execute_factor_code_accepts_series_return(self):
        """_execute_factor_code 应接受 factor_program 返回 pd.Series（此前静默返回全零）。"""
        from fts.factor_engine.backtest_pipeline import BacktestPipeline

        data = self._make_data()
        code = (
            "def factor_program(data, params):\n"
            "    import pandas as pd\n"
            "    return pd.Series(data['close'] - data['close'].mean())\n"
        )
        signal = BacktestPipeline._execute_factor_code(code, data, {})
        assert isinstance(signal, np.ndarray)
        assert len(signal) == len(data)
        assert np.std(signal) > 1e-9

    def test_ts_product_template_works_on_pandas_2(self):
        """GP 模板 ts_product 不再依赖 Rolling.prod（pandas≥2.1 已移除）。

        回归: 此前模板用 x.rolling(window).prod() 抛 AttributeError，被
        _execute_factor_code 降级为全零 → GP 含 ts_product 的因子全部被
        「常数信号」拦截。现改用 apply(np.prod)（与 feature_ops 对齐）。
        """
        from fts.factor_engine.backtest_pipeline import BacktestPipeline

        rng = np.random.default_rng(11)
        n = 120
        close = 1.0 + 0.05 * np.cumsum(rng.normal(0, 1, n))  # 量级 ~1，乘积不触发 clip
        data = pd.DataFrame(
            {
                "open": close,
                "high": close + 0.01,
                "low": close - 0.01,
                "close": close,
                "volume": rng.uniform(1e4, 1e6, n),
            }
        )
        root = TreeNode(
            op_name="ts_product",
            children=[TreeNode(operand="close", is_terminal=True)],
            is_terminal=False,
        )
        tree = ExpressionTree(
            root=root,
            depth=1,
            size=2,
            expression="ts_product(close, 5)",
            fitness=0.5,
        )
        result = tree_to_factor_program(tree)
        signal = BacktestPipeline._execute_factor_code(result["code"], data, {})
        assert isinstance(signal, np.ndarray)
        assert len(signal) == len(data)
        assert np.std(signal) > 1e-9  # 非全零降级

    def test_evaluate_fitness_clips_to_pipeline_range(self):
        """GP 适应度与流水线后处理对齐（clip[-10,10] + nan_to_num）。

        回归: mul(volume, volume) 量级被 _execute_factor_code 裁剪为常数，
        但 _evaluate_fitness 未对齐 → GP 误选该表达式为最优，下游运行时校验
        再以「常数信号」拦截，通道空转。对齐后该表达式应被罚分。
        """
        from fts.factor_engine.feature_ops import OperatorRegistry

        data = self._make_data()
        target = np.roll(np.diff(data["close"].values, prepend=data["close"].iloc[0]), -1)
        gp_data = data.copy()
        gp_data["forward_return"] = target
        gp = GPEvolver(
            operator_registry=OperatorRegistry(),
            data_panel=gp_data,
            target_col="forward_return",
            config=GPEvolverConfig(),
        )
        tree = ExpressionTree(
            root=TreeNode(
                op_name="mul",
                children=[
                    TreeNode(operand="volume", is_terminal=True),
                    TreeNode(operand="volume", is_terminal=True),
                ],
                is_terminal=False,
            ),
            depth=1,
            size=3,
            expression="mul(volume, volume)",
            fitness=0.0,
        )
        ft = gp._evaluate_fitness(tree)
        assert ft.fitness < 0, f"裁剪后为常数的表达式应被罚分, fitness={ft.fitness}"


# ─── GAP-I204: GP 多目标适应度（IC×换手×衰减）首期 ───────


class TestGapI204MultiObjective:
    """GAP-I204: multi_objective 适应度模式——IC×Sharpe 正向贡献 − 换手惩罚 − 衰减惩罚。"""

    def _make_tree(self) -> ExpressionTree:
        return ExpressionTree(
            root=TreeNode(operand=1.0, is_terminal=True),
            depth=1,
            size=1,
            expression="1.0",
            fitness=0.0,
        )

    def _make_gp(self, sample_data, **cfg_kw) -> GPEvolver:
        config = GPEvolverConfig(
            population_size=10,
            max_generations=2,
            fitness_metric="multi_objective",
            **cfg_kw,
        )
        return GPEvolver(
            operator_registry=OperatorRegistry(),
            data_panel=sample_data,
            target_col="forward_return_20d",
            config=config,
        )

    def _sig(self, sample_data, values: np.ndarray) -> pd.Series:
        return pd.Series(values, index=sample_data.index)

    def test_fitness_result_carries_turnover_decay(
        self,
        sample_data,
        monkeypatch,
    ):
        """FitnessResult 新增 turnover/decay 字段，默认 ic_sharpe_combo 模式也填充。"""
        gp = GPEvolver(
            operator_registry=OperatorRegistry(),
            data_panel=sample_data,
            target_col="forward_return_20d",
        )  # 默认 ic_sharpe_combo
        target = sample_data["forward_return_20d"].to_numpy()
        monkeypatch.setattr(
            "fts.factor_engine.gp_evolver._evaluate_tree",
            lambda *a, **k: self._sig(sample_data, target),
        )
        res = gp._evaluate_fitness(self._make_tree())
        assert res.turnover >= 0.0
        assert 0.0 <= res.decay <= 1.0

    def test_turnover_measures_signal_choppiness(
        self,
        sample_data,
        monkeypatch,
    ):
        """换手度量：平滑信号 turnover 小，高频振荡信号 turnover 大。"""
        gp = self._make_gp(sample_data)
        n = len(sample_data)
        t = np.arange(n)
        smooth = np.sin(t / 50.0)
        choppy = np.sin(t * 2.0)
        monkeypatch.setattr(
            "fts.factor_engine.gp_evolver._evaluate_tree",
            lambda *a, **k: self._sig(sample_data, smooth),
        )
        r_smooth = gp._evaluate_fitness(self._make_tree())
        monkeypatch.setattr(
            "fts.factor_engine.gp_evolver._evaluate_tree",
            lambda *a, **k: self._sig(sample_data, choppy),
        )
        r_choppy = gp._evaluate_fitness(self._make_tree())
        assert r_choppy.turnover > r_smooth.turnover
        assert r_choppy.fitness < r_smooth.fitness  # 换手惩罚拉低 multi_objective 适应度

    def test_multi_objective_penalizes_turnover(
        self,
        sample_data,
        monkeypatch,
    ):
        """同 IC 量级下，高换手信号在 multi_objective 模式被惩罚，fitness 更低。"""
        gp = self._make_gp(sample_data)
        target = sample_data["forward_return_20d"].to_numpy()
        n = len(target)
        t = np.arange(n)
        # 两者都与 target 强相关（IC≈0.9+），仅噪声波动频率不同
        low_turn = target + 0.01 * np.sin(t / 50.0)
        high_turn = target + 0.05 * np.sin(t * 2.0)
        monkeypatch.setattr(
            "fts.factor_engine.gp_evolver._evaluate_tree",
            lambda *a, **k: self._sig(sample_data, low_turn),
        )
        r_low = gp._evaluate_fitness(self._make_tree())
        monkeypatch.setattr(
            "fts.factor_engine.gp_evolver._evaluate_tree",
            lambda *a, **k: self._sig(sample_data, high_turn),
        )
        r_high = gp._evaluate_fitness(self._make_tree())
        assert r_high.turnover > r_low.turnover
        assert r_high.fitness < r_low.fitness

    def test_turnover_penalty_coefficient_scales(
        self,
        sample_data,
        monkeypatch,
    ):
        """turnover_penalty 系数放大换手惩罚（大系数 → fitness 更低）。"""
        target = sample_data["forward_return_20d"].to_numpy()
        n = len(target)
        t = np.arange(n)
        choppy = target + 0.05 * np.sin(t * 3.0)

        def _fitness(penalty: float) -> float:
            gp = self._make_gp(sample_data, turnover_penalty=penalty)
            monkeypatch.setattr(
                "fts.factor_engine.gp_evolver._evaluate_tree",
                lambda *a, **k: self._sig(sample_data, choppy),
            )
            return gp._evaluate_fitness(self._make_tree()).fitness

        assert _fitness(1.0) < _fitness(0.0)

    def test_multi_objective_penalizes_decay(
        self,
        sample_data,
        monkeypatch,
    ):
        """衰减度量：后段 IC 塌陷的信号 decay≈1，前后段稳定信号 decay≈0。"""
        gp = self._make_gp(sample_data)
        target = sample_data["forward_return_20d"].to_numpy()
        n = len(target)
        half = n // 2
        rng = np.random.default_rng(7)
        decayed = np.copy(target)
        decayed[half:] = rng.normal(size=n - half) * 0.001  # 后段≈噪声 → 后段 IC≈0
        stable = np.copy(target)  # 前后段都与 target 相关 → decay≈0
        monkeypatch.setattr(
            "fts.factor_engine.gp_evolver._evaluate_tree",
            lambda *a, **k: self._sig(sample_data, decayed),
        )
        r_decay = gp._evaluate_fitness(self._make_tree())
        monkeypatch.setattr(
            "fts.factor_engine.gp_evolver._evaluate_tree",
            lambda *a, **k: self._sig(sample_data, stable),
        )
        r_stable = gp._evaluate_fitness(self._make_tree())
        assert r_decay.decay > 0.5
        assert r_stable.decay < 0.2
        assert r_decay.fitness < r_stable.fitness  # 衰减惩罚拉低 fitness

    def test_decay_penalty_coefficient_scales(
        self,
        sample_data,
        monkeypatch,
    ):
        """decay_penalty 系数放大衰减惩罚（大系数 → fitness 更低）。"""
        target = sample_data["forward_return_20d"].to_numpy()
        n = len(target)
        half = n // 2
        rng = np.random.default_rng(3)
        decayed = np.copy(target)
        decayed[half:] = rng.normal(size=n - half) * 0.001

        def _fitness(penalty: float) -> float:
            gp = self._make_gp(sample_data, decay_penalty=penalty)
            monkeypatch.setattr(
                "fts.factor_engine.gp_evolver._evaluate_tree",
                lambda *a, **k: self._sig(sample_data, decayed),
            )
            return gp._evaluate_fitness(self._make_tree()).fitness

        assert _fitness(1.0) < _fitness(0.0)

    def test_evolve_multi_objective_mode(self, registry, sample_data):
        """端到端 evolve() 以 multi_objective 模式运行，输出最优因子换手/衰减指标。"""
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=sample_data,
            target_col="forward_return_20d",
            config=GPEvolverConfig(
                population_size=10,
                max_generations=2,
                fitness_metric="multi_objective",
                turnover_penalty=0.3,
                decay_penalty=0.3,
            ),
        )
        result = gp.evolve()
        assert result.best_fitness >= -10.0
        assert isinstance(result.best_turnover, float)
        assert 0.0 <= result.best_decay <= 1.0
