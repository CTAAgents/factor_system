"""
fts.factor_engine.symbolic_regression — 符号回归补充搜索器（GAP-I204 二期，v2.78.0）。

在 GP 随机演化之外，提供确定性的 beam-search 层级搜索：从单字段出发，
逐层用一元算子包装 / 二元算子组合生成候选表达式，按多目标适应度
（|IC|×Sharpe − 换手惩罚 − 衰减惩罚）每层保留 top-K，与 GP 结果互补
（GP 随机探索 vs 符号回归受控枚举），并可直接并入 Pareto 前沿输出。

用法:
    from fts.factor_engine.symbolic_regression import (
        SymbolicRegressionSearcher, SymbolicRegressionConfig,
    )

    searcher = SymbolicRegressionSearcher(
        operator_registry=registry,
        data_panel=panel_data,
        target_col="forward_return_20d",
        config=SymbolicRegressionConfig(max_depth=4, beam_width=10),
    )
    result = searcher.search()

版本: v1.0.0
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .feature_ops import OperatorRegistry
from .gp_evolver import (
    ExpressionTree,
    GPEvolver,
    GPEvolverConfig,
    TreeNode,
    _tree_depth,
    _tree_size,
    _tree_to_expression,
)

logger = logging.getLogger(__name__)

# 一元包装算子（无参，直接作用于子表达式）
_UNARY_OPS = [
    "rank",
    "zscore",
    "delta",
    "pct_change",
    "log_return",
    "ts_mean",
    "ts_std",
    "ts_max",
    "ts_min",
    "ts_sum",
    "ts_product",
    "ts_rank",
    "ts_zscore",
    "ts_momentum",
    "ts_volatility",
    "ts_skewness",
    "ts_kurtosis",
]

# 二元组合算子
_BINARY_OPS = ["add", "sub", "mul", "div"]


@dataclass
class SymbolicRegressionConfig:
    """符号回归补充搜索配置。"""

    max_depth: int = 4
    """最大层级深度（根算子数）。"""
    beam_width: int = 10
    """每层保留 top-K 候选。"""
    max_candidates: int = 200
    """候选表达式总评估上限（防组合爆炸）。"""
    fitness_metric: str = "multi_objective"
    """适应度口径（与 GPEvolver 一致）。"""
    turnover_penalty: float = 0.3
    """换手惩罚系数（multi_objective 生效）。"""
    decay_penalty: float = 0.3
    """衰减惩罚系数（multi_objective 生效）。"""
    min_fitness: float = 0.01
    """候选纳入结果的最小适应度阈值（过滤无效表达式）。"""
    seed: int = 42
    """固定种子（内部 GPEvolver 复用，保证可复现）。"""


@dataclass
class SymbolicCandidate:
    """符号回归候选解。"""

    expression: str = ""
    ic: float = 0.0
    sharpe: float = 0.0
    turnover: float = 0.0
    decay: float = 0.0
    fitness: float = 0.0
    depth: int = 0
    size: int = 0


@dataclass
class SymbolicRegressionResult:
    """符号回归搜索结果。"""

    candidates: list[SymbolicCandidate] = field(default_factory=list)
    """全部通过阈值过滤的候选（按 fitness 降序）。"""
    best: Optional[SymbolicCandidate] = None
    """最优候选。"""
    total_evaluated: int = 0
    """总评估表达式数。"""
    elapsed_ms: float = 0.0
    """耗时（毫秒）。"""


class SymbolicRegressionSearcher:
    """符号回归补充搜索器（确定性 beam-search）。

    从单字段出发逐层枚举：每层对 beam 内候选做一元包装 + 两两二元组合，
    评估后按多目标适应度保留 top-K。复用 GPEvolver 的多目标评估逻辑，
    保证与 GP 通道同口径（换手/衰减惩罚一致）。
    """

    def __init__(
        self,
        operator_registry: OperatorRegistry,
        data_panel: pd.DataFrame,
        target_col: str,
        config: Optional[SymbolicRegressionConfig] = None,
        train_mask: Optional[object] = None,
    ) -> None:
        self._registry = operator_registry
        self._data = data_panel
        self._target_col = target_col
        self._train_mask = train_mask
        self._config = config or SymbolicRegressionConfig()
        self._columns = [c for c in data_panel.columns if c != target_col]

        # 复用 GPEvolver 多目标评估（同口径惩罚系数）。
        self._gp = GPEvolver(
            operator_registry=operator_registry,
            data_panel=data_panel,
            target_col=target_col,
            config=GPEvolverConfig(
                fitness_metric=self._config.fitness_metric,
                turnover_penalty=self._config.turnover_penalty,
                decay_penalty=self._config.decay_penalty,
            ),
            train_mask=train_mask,
        )

    # ─── 候选生成与评估 ──────────────────────────────────

    @staticmethod
    def _wrap_unary(tree: TreeNode, op_name: str) -> TreeNode:
        """一元算子包装：op(child)。"""
        return TreeNode(op_name=op_name, children=[tree], is_terminal=False)

    @staticmethod
    def _combine_binary(left: TreeNode, right: TreeNode, op_name: str) -> TreeNode:
        """二元算子组合：op(left, right)。"""
        return TreeNode(op_name=op_name, children=[left, right], is_terminal=False)

    def _evaluate_candidate(self, tree: TreeNode) -> Optional[SymbolicCandidate]:
        """评估单个候选树，返回多目标指标；无效表达式返回 None。"""
        expression = _tree_to_expression(tree)
        result = self._gp._evaluate_fitness(ExpressionTree(root=tree, expression=expression))
        if result.fitness < self._config.min_fitness:
            return None
        return SymbolicCandidate(
            expression=expression,
            ic=result.ic,
            sharpe=result.sharpe,
            turnover=result.turnover,
            decay=result.decay,
            fitness=result.fitness,
            depth=_tree_depth(tree),
            size=_tree_size(tree),
        )

    # ─── 主搜索 ──────────────────────────────────────────

    def search(self) -> SymbolicRegressionResult:
        """执行符号回归 beam-search。"""
        random.seed(self._config.seed)
        start_ms = time.time() * 1000

        # 注册表内可用算子（避免枚举不存在的算子）。
        available = {info.name for info in self._registry.list_operators()}
        unary_ops = [op for op in _UNARY_OPS if op in available]
        binary_ops = [op for op in _BINARY_OPS if op in available]

        # 初始化 beam：单字段叶节点（排除目标列）。
        beam: list[TreeNode] = [TreeNode(operand=col, is_terminal=True) for col in self._columns]
        all_candidates: dict[str, SymbolicCandidate] = {}
        total_evaluated = 0

        for depth in range(1, self._config.max_depth + 1):
            generated: list[TreeNode] = []
            # 一元包装
            for tree in beam:
                for op in unary_ops:
                    generated.append(self._wrap_unary(tree, op))
            # 两两二元组合（有序对，覆盖 a op b 与 b op a）
            if depth >= 2:
                for left in beam:
                    for right in beam:
                        for op in binary_ops:
                            generated.append(self._combine_binary(left, right, op))

            # 去重（按表达式字符串）
            seen: set[str] = set()
            unique: list[TreeNode] = []
            for tree in generated:
                expr = _tree_to_expression(tree)
                if expr not in seen:
                    seen.add(expr)
                    unique.append(tree)

            # 评估候选
            scored: list[tuple[float, TreeNode]] = []
            for tree in unique:
                cand = self._evaluate_candidate(tree)
                total_evaluated += 1
                if cand is not None:
                    all_candidates[cand.expression] = cand
                    scored.append((cand.fitness, tree))
                if total_evaluated >= self._config.max_candidates:
                    break
            if total_evaluated >= self._config.max_candidates:
                break

            # 保留 top-K beam
            scored.sort(key=lambda x: x[0], reverse=True)
            beam = [tree for _, tree in scored[: self._config.beam_width]]
            # 无新候选 → 提前终止
            if not beam:
                break

        candidates = sorted(all_candidates.values(), key=lambda c: c.fitness, reverse=True)
        best = candidates[0] if candidates else None
        elapsed = (time.time() - start_ms) * 1000
        logger.info(
            "符号回归搜索完成: candidates=%d, best=%.4f, evaluated=%d, time=%.0fms",
            len(candidates),
            best.fitness if best else float("-inf"),
            total_evaluated,
            elapsed,
        )
        return SymbolicRegressionResult(
            candidates=candidates,
            best=best,
            total_evaluated=total_evaluated,
            elapsed_ms=elapsed,
        )
