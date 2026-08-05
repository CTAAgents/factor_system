"""
fts.factor_engine.gp_evolver — GP 遗传规划搜索引擎 (Phase C.1)。

基于遗传规划 (Genetic Programming) 在算子空间搜索最优因子表达式。
支持锦标赛选择、交叉、变异和精英保留策略。

用法:
    gp = GPEvolver(
        operator_registry=registry,
        data_panel=panel_data,
        target_col='forward_return_20d',
    )
    result = gp.evolve()
    print(result.best_expression)

版本: v0.1.0
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

import numpy as np
import pandas as pd

from .feature_ops import OperatorRegistry

logger = logging.getLogger(__name__)


# ─── 类型定义 ────────────────────────────────────────────────


@dataclass
class TreeNode:
    """树节点。"""

    op_name: Optional[str] = None
    operand: Optional[float | str] = None
    children: list["TreeNode"] = field(default_factory=list)
    is_terminal: bool = False


@dataclass
class ExpressionTree:
    """GP 表达式树。"""

    root: TreeNode
    depth: int = 0
    size: int = 0
    expression: str = ""
    fitness: float = 0.0


@dataclass
class FitnessResult:
    """适应度评估结果。"""

    ic: float = 0.0
    sharpe: float = 0.0
    fitness: float = 0.0
    factor_code: str = ""
    evaluation_time_ms: float = 0.0


@dataclass
class GPEvolverConfig:
    """GP 演化配置。"""

    population_size: int = 200
    max_generations: int = 50
    tournament_size: int = 3
    crossover_rate: float = 0.7
    mutation_rate: float = 0.1
    max_tree_depth: int = 5
    min_tree_depth: int = 2
    elitism_size: int = 5
    fitness_metric: Literal["ic", "sharpe", "ic_sharpe_combo"] = "ic_sharpe_combo"


@dataclass
class GenerationSnapshot:
    """每代快照。"""

    generation: int
    best_fitness: float
    best_expression: str
    avg_fitness: float
    population_diversity: float


@dataclass
class GPEvolveResult:
    """GP 演化结果。"""

    best_tree: ExpressionTree
    best_fitness: float
    best_expression: str
    best_ic: float
    best_sharpe: float
    generations_completed: int
    history: list[GenerationSnapshot] = field(default_factory=list)
    total_evaluations: int = 0


# ─── 表达式树生成 ────────────────────────────────────────────


def _random_terminal(
    columns: list[str],
    const_range: tuple[float, float] = (-1.0, 1.0),
) -> TreeNode:
    """生成随机叶节点 (列名或常量)。"""
    if random.random() < 0.7:
        return TreeNode(
            operand=random.choice(columns),
            is_terminal=True,
        )
    else:
        return TreeNode(
            operand=round(random.uniform(*const_range), 6),
            is_terminal=True,
        )


def _random_tree(
    registry: OperatorRegistry,
    columns: list[str],
    max_depth: int,
    current_depth: int = 0,
    force_func: bool = False,
) -> TreeNode:
    """递归生成随机表达式树。"""
    categories = registry.list_categories()
    composite_ops = [
        info for info in registry.list_operators()
        if info.category in ("composite", "time_series", "price", "rolling")
    ]
    if not composite_ops:
        return _random_terminal(columns)

    # 达到最大深度或强制叶节点
    if current_depth >= max_depth or (not force_func and current_depth > 0 and random.random() < 0.3):
        return _random_terminal(columns)

    op_info = random.choice(composite_ops)
    op_name = op_info.name
    n_children = _get_operator_arity(op_name)

    children = [
        _random_tree(
            registry, columns, max_depth, current_depth + 1,
        )
        for _ in range(n_children)
    ]
    return TreeNode(op_name=op_name, children=children, is_terminal=False)


def _get_operator_arity(op_name: str) -> int:
    """获取算子的参数数量。"""
    arity_map = {
        "add": 2, "sub": 2, "mul": 2, "div": 2, "scale": 1,
        "if_then_else": 3, "conditional_weight": 3,
        "rank": 1, "zscore": 1, "delta": 1, "pct_change": 1, "log_return": 1,
        "ts_mean": 1, "ts_std": 1, "ts_max": 1, "ts_min": 1,
        "ts_sum": 1, "ts_product": 1,
        "ts_rank": 1, "ts_zscore": 1, "ts_momentum": 1, "ts_volatility": 1,
        "ts_skewness": 1, "ts_kurtosis": 1,
    }
    return arity_map.get(op_name, 2)


def _tree_to_expression(node: TreeNode) -> str:
    """将表达式树转换为可读字符串。"""
    if node.is_terminal:
        return str(node.operand)

    op_name = node.op_name or "unknown"
    if not node.children:
        return op_name

    args = [_tree_to_expression(child) for child in node.children]
    return f"{op_name}({', '.join(args)})"


def _tree_depth(node: TreeNode) -> int:
    """计算树深度。"""
    if node.is_terminal:
        return 0
    if not node.children:
        return 1
    return 1 + max(_tree_depth(child) for child in node.children)


def _tree_size(node: TreeNode) -> int:
    """计算树大小 (节点数)。"""
    if node.is_terminal:
        return 1
    return 1 + sum(_tree_size(child) for child in node.children)


def _evaluate_tree(
    tree: TreeNode,
    registry: OperatorRegistry,
    data: pd.DataFrame,
) -> Optional[pd.Series]:
    """在数据上评估表达式树，返回因子值序列。"""
    if tree.is_terminal:
        if isinstance(tree.operand, str) and tree.operand in data.columns:
            return data[tree.operand].astype(float)
        return pd.Series([float(tree.operand)] * len(data), index=data.index)

    if not tree.children or tree.op_name is None:
        return None

    op_name = tree.op_name
    children_results = []
    for child in tree.children:
        result = _evaluate_tree(child, registry, data)
        if result is None:
            return None
        children_results.append(result)

    try:
        if op_name in ("add", "sub", "mul", "div"):
            ops = {
                "add": lambda a, b: a + b,
                "sub": lambda a, b: a - b,
                "mul": lambda a, b: a * b,
                "div": lambda a, b: a / b.replace(0, np.nan),
            }
            return ops[op_name](children_results[0], children_results[1])

        elif op_name == "scale":
            factor_val = tree.children[1].operand if len(tree.children) > 1 else 1.0
            return children_results[0] * float(factor_val)

        elif registry.get_operator(op_name) is not None:
            func = registry.call
            kwargs = {}
            if op_name in ("ts_mean", "ts_std", "ts_max", "ts_min", "ts_sum",
                           "ts_product", "ts_rank", "ts_zscore", "ts_momentum",
                           "ts_volatility", "ts_skewness", "ts_kurtosis"):
                window = 20
                if len(tree.children) > 1 and tree.children[1].is_terminal:
                    window = max(2, int(tree.children[1].operand))
                return func(op_name, children_results[0], window=window)
            elif op_name in ("delta", "pct_change"):
                periods = 1
                if len(tree.children) > 1 and tree.children[1].is_terminal:
                    periods = max(1, int(tree.children[1].operand))
                return func(op_name, children_results[0], periods=periods)
            elif op_name == "rank":
                return func(op_name, children_results[0])
            elif op_name == "zscore":
                return func(op_name, children_results[0])
            elif op_name == "log_return":
                return func(op_name, children_results[0])
            elif op_name == "if_then_else":
                cond = children_results[0] > 0
                return pd.Series(
                    np.where(cond, children_results[1], children_results[2]),
                    index=data.index,
                )
            elif op_name == "conditional_weight":
                threshold = 0.0
                if len(tree.children) > 2 and tree.children[2].is_terminal:
                    threshold = float(tree.children[2].operand)
                return pd.Series(
                    np.where(children_results[0] > threshold,
                             children_results[0] * children_results[1], 0.0),
                    index=data.index,
                )
            else:
                return None
    except Exception:
        return None

    return None


# ─── GP 演化器 ─────────────────────────────────────────────


class GPEvolver:
    """遗传规划演化器。

    Usage:
        gp = GPEvolver(
            operator_registry=registry,
            data_panel=panel_data,
            target_col='forward_return_20d',
        )
        result = gp.evolve()
    """

    def __init__(
        self,
        operator_registry: OperatorRegistry,
        data_panel: pd.DataFrame,
        target_col: str,
        config: Optional[GPEvolverConfig] = None,
    ) -> None:
        self._registry = operator_registry
        self._data = data_panel
        self._target_col = target_col
        self._config = config or GPEvolverConfig()
        self._columns = list(data_panel.columns)
        if target_col in self._columns:
            self._columns.remove(target_col)
        self._history: list[GenerationSnapshot] = []
        self._total_evaluations: int = 0

    def evolve(self) -> GPEvolveResult:
        """执行 GP 演化，返回最优因子。"""
        start_time = time.time()

        # 初始化种群
        population = self._initialize_population(self._config.population_size)
        fitness_cache: dict[str, float] = {}

        best_tree: Optional[ExpressionTree] = None
        best_fitness = -float("inf")

        for gen in range(self._config.max_generations):
            # 评估适应度
            evaluated = []
            for tree in population:
                key = tree.expression
                if key in fitness_cache:
                    tree.fitness = fitness_cache[key]
                else:
                    result = self._evaluate_fitness(tree)
                    tree.fitness = result.fitness
                    fitness_cache[key] = result.fitness
                    self._total_evaluations += 1
                evaluated.append(tree)

            # 记录最优
            gen_best = max(evaluated, key=lambda t: t.fitness)
            if gen_best.fitness > best_fitness:
                best_fitness = gen_best.fitness
                best_tree = gen_best

            avg_fitness = sum(t.fitness for t in evaluated) / len(evaluated)

            # 计算多样性
            expressions = {t.expression for t in evaluated}
            diversity = len(expressions) / len(evaluated)

            snapshot = GenerationSnapshot(
                generation=gen,
                best_fitness=gen_best.fitness,
                best_expression=gen_best.expression,
                avg_fitness=avg_fitness,
                population_diversity=diversity,
            )
            self._history.append(snapshot)

            logger.info(
                "GP Gen %d: best_fitness=%.4f, avg=%.4f, diversity=%.2f%%",
                gen, gen_best.fitness, avg_fitness, diversity * 100,
            )

            # 选择 + 交叉 + 变异
            population = self._evolve_population(evaluated)

        if best_tree is None:
            best_tree = ExpressionTree(
                root=TreeNode(operand="close", is_terminal=True),
                expression="close",
            )

        best_ic, best_sharpe = self._evaluate_best_metrics(best_tree)

        elapsed = (time.time() - start_time) * 1000
        logger.info(
            "GP 演化完成: best_fitness=%.4f, ic=%.4f, sharpe=%.4f, time=%.0fms",
            best_fitness, best_ic, best_sharpe, elapsed,
        )

        return GPEvolveResult(
            best_tree=best_tree,
            best_fitness=best_fitness,
            best_expression=best_tree.expression,
            best_ic=best_ic,
            best_sharpe=best_sharpe,
            generations_completed=len(self._history),
            history=self._history,
            total_evaluations=self._total_evaluations,
        )

    def _initialize_population(self, size: int) -> list[ExpressionTree]:
        """初始化种群。"""
        population: list[ExpressionTree] = []
        for _ in range(size):
            root = _random_tree(
                self._registry, self._columns,
                self._config.max_tree_depth,
                force_func=True,
            )
            expr = _tree_to_expression(root)
            population.append(ExpressionTree(
                root=root,
                depth=_tree_depth(root),
                size=_tree_size(root),
                expression=expr,
            ))
        return population

    def _evaluate_fitness(self, tree: ExpressionTree) -> FitnessResult:
        """评估单个表达式树的适应度。"""
        start_ms = time.time() * 1000

        # 计算因子值
        factor_values = _evaluate_tree(tree.root, self._registry, self._data)
        if factor_values is None or factor_values.isna().all():
            return FitnessResult(fitness=-10.0, evaluation_time_ms=0)

        # 计算 IC
        target = self._data[self._target_col]
        aligned = pd.concat([factor_values, target], axis=1).dropna()
        if len(aligned) < 20:
            return FitnessResult(fitness=-5.0, evaluation_time_ms=0)

        ic = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if np.isnan(ic):
            return FitnessResult(fitness=-5.0, evaluation_time_ms=0)

        # 计算 Sharpe
        try:
            rets = factor_values.pct_change().dropna()
            if len(rets) > 1 and rets.std() > 0:
                sharpe = rets.mean() / rets.std() * np.sqrt(252)
            else:
                sharpe = 0.0
        except Exception:
            sharpe = 0.0

        # 适应度计算
        metric = self._config.fitness_metric
        if metric == "ic":
            fitness = abs(ic)
        elif metric == "sharpe":
            fitness = sharpe
        else:  # ic_sharpe_combo
            fitness = abs(ic) * 0.6 + max(sharpe, 0) * 0.4

        elapsed_ms = time.time() * 1000 - start_ms
        return FitnessResult(
            ic=float(ic),
            sharpe=float(sharpe),
            fitness=float(fitness),
            factor_code=tree.expression,
            evaluation_time_ms=elapsed_ms,
        )

    def _evolve_population(
        self,
        population: list[ExpressionTree],
    ) -> list[ExpressionTree]:
        """进化种群: 选择 + 交叉 + 变异 + 精英保留。"""
        config = self._config
        new_population: list[ExpressionTree] = []

        # 精英保留
        sorted_pop = sorted(population, key=lambda t: t.fitness, reverse=True)
        elites = sorted_pop[: config.elitism_size]
        new_population.extend(elites)

        # 锦标赛选择 + 交叉 + 变异
        while len(new_population) < config.population_size:
            parent1 = self._tournament_select(population)
            parent2 = self._tournament_select(population)

            if random.random() < config.crossover_rate:
                child = self._crossover(parent1, parent2)
            else:
                child = ExpressionTree(
                    root=self._copy_tree(parent1.root),
                    depth=parent1.depth,
                    size=parent1.size,
                    expression=parent1.expression,
                )

            if random.random() < config.mutation_rate:
                child = self._mutate(child)

            new_population.append(child)

        # 截断到种群大小
        return new_population[: config.population_size]

    def _tournament_select(
        self,
        population: list[ExpressionTree],
    ) -> ExpressionTree:
        """锦标赛选择。"""
        candidates = random.sample(
            population,
            min(self._config.tournament_size, len(population)),
        )
        return max(candidates, key=lambda t: t.fitness)

    def _crossover(
        self,
        parent1: ExpressionTree,
        parent2: ExpressionTree,
    ) -> ExpressionTree:
        """交叉: 随机选择两个父代的子树交换。"""
        child_root = self._copy_tree(parent1.root)
        donor_root = self._copy_tree(parent2.root)

        # 找到 child 的所有子树节点
        child_nodes = self._collect_internal_nodes(child_root)
        if not child_nodes:
            return ExpressionTree(
                root=child_root,
                depth=_tree_depth(child_root),
                size=_tree_size(child_root),
                expression=_tree_to_expression(child_root),
            )

        # 找到 donor 的所有子树节点
        donor_nodes = self._collect_internal_nodes(donor_root)
        if not donor_nodes:
            return ExpressionTree(
                root=child_root,
                depth=_tree_depth(child_root),
                size=_tree_size(child_root),
                expression=_tree_to_expression(child_root),
            )

        # 随机交换一个子树
        child_target = random.choice(child_nodes)
        donor_source = random.choice(donor_nodes)

        # 复制 donor 子树替换
        child_target.op_name = donor_source.op_name
        child_target.children = self._copy_children(donor_source.children)

        expr = _tree_to_expression(child_root)
        return ExpressionTree(
            root=child_root,
            depth=_tree_depth(child_root),
            size=_tree_size(child_root),
            expression=expr,
        )

    def _mutate(self, tree: ExpressionTree) -> ExpressionTree:
        """变异: 随机替换子树。"""
        root = self._copy_tree(tree.root)
        nodes = self._collect_internal_nodes(root)

        if nodes:
            target = random.choice(nodes)
            new_subtree = _random_tree(
                self._registry,
                self._columns,
                self._config.max_tree_depth,
                force_func=True,
            )
            target.op_name = new_subtree.op_name
            target.children = new_subtree.children
        else:
            # 叶节点变异
            if root.is_terminal:
                root.operand = random.choice(self._columns)

        expr = _tree_to_expression(root)
        return ExpressionTree(
            root=root,
            depth=_tree_depth(root),
            size=_tree_size(root),
            expression=expr,
        )

    def _evaluate_best_metrics(
        self,
        tree: ExpressionTree,
    ) -> tuple[float, float]:
        """评估最优树的 IC 和 Sharpe。"""
        result = self._evaluate_fitness(tree)
        return result.ic, result.sharpe

    @staticmethod
    def _copy_tree(node: TreeNode) -> TreeNode:
        """深拷贝树节点。"""
        return TreeNode(
            op_name=node.op_name,
            operand=node.operand,
            children=[GPEvolver._copy_tree(c) for c in node.children],
            is_terminal=node.is_terminal,
        )

    @staticmethod
    def _copy_children(children: list[TreeNode]) -> list[TreeNode]:
        """深拷贝子节点列表。"""
        return [GPEvolver._copy_tree(c) for c in children]

    @staticmethod
    def _collect_internal_nodes(node: TreeNode) -> list[TreeNode]:
        """收集所有内部节点 (非叶节点)。"""
        if node.is_terminal:
            return []
        nodes = [node]
        for child in node.children:
            nodes.extend(GPEvolver._collect_internal_nodes(child))
        return nodes


# ─── 表达式树 → FactorProgram 转换 ───────────────────────────


def tree_to_factor_program(tree: ExpressionTree) -> dict[str, Any]:
    """将 GP 表达式树转换为因子程序字典。

    生成的因子代码示例::

        def compute(close, high, low, volume):
            from .ops import ts_mean, rank
            x1 = ts_mean(close, window=20)
            return rank(x1)

    Args:
        tree: GP 表达式树

    Returns:
        因子程序字典，包含 factor_id, code, expression 等字段
    """
    import hashlib
    import time

    expression = tree.expression
    unique_key = f"{expression}_{time.time_ns()}_{id(tree)}"
    factor_id = "fct_" + hashlib.md5(
        unique_key.encode()
    ).hexdigest()[:8]

    code_lines = [
        f'"""GP 演化因子 {factor_id}."""',
        "",
        "import numpy as np",
        "import pandas as pd",
        "",
        "def compute(close, high, low, volume):",
        f'    """计算因子值."""',
        f"    return ({expression})",
        "",
    ]
    code = "\n".join(code_lines)

    return {
        "factor_id": factor_id,
        "name": f"gp_factor_{factor_id}",
        "code": code,
        "expression": expression,
        "tree_depth": tree.depth,
        "tree_size": tree.size,
        "fitness": tree.fitness,
        "source": "gp_evolution",
    }