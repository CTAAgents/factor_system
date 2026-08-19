"""算子演化引擎 — DSL 算子空间适应度导向进化搜索 (Phase 3+ / C.4)。

在 FTS-Expr 算子空间（58 算子 L0-L5）内做进化式搜索：
种群初始化（validator 校验通过）→ IC/Sharpe 适应度评估（DSL executor）→
锦标赛选择 → 子树交叉/变异（ExprNode 层面，参数受 param_bounds 约束）→
精英保留，多代迭代后取最优表达式产出 kind=OPERATOR 因子。

取代 evolution_loop._generate_operator_factor 的纯随机组合生成，
并关闭 GAP-026（GP 算子命名与 DSL 对齐：本引擎直接以 DSL 注册表为算子空间）。

设计文档: docs/harness/design/C.4-operator-evolution-engine-design.md
"""

from __future__ import annotations

import copy
import logging
import random
from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np
import pandas as pd

from .contracts import FactorProgram
from .expr_dsl.ast import ExprNode
from .expr_dsl.executor import evaluate
from .expr_dsl.factory import create_operator_factor
from .expr_dsl.parser import parse_expression
from .expr_dsl.registry import L0_FIELDS, OperatorMeta, build_registry
from .expr_dsl.validator import validate_expr

logger = logging.getLogger(__name__)

_MIN_SAMPLES = 20
_PENALTY_NO_SIGNAL = -10.0
_PENALTY_WEAK = -5.0


@dataclass
class OperatorEvolutionConfig:
    """算子演化配置（契约见 C.4 设计 §3.1）。"""

    population_size: int = 100  # 种群大小
    max_generations: int = 20  # 最大代数
    tournament_size: int = 3  # 锦标赛大小
    crossover_rate: float = 0.7  # 交叉率
    mutation_rate: float = 0.15  # 变异率
    max_tree_depth: int = 5  # 表达式最大深度
    elitism_size: int = 5  # 精英保留数
    fitness_metric: Literal["ic", "sharpe", "ic_sharpe_combo"] = "ic_sharpe_combo"
    max_attempts: int = 30  # 无效个体（校验失败）重试上限
    random_seed: int = 42  # 随机种子（可复现）


@dataclass
class OperatorGenerationSnapshot:
    """每代快照。"""

    generation: int
    best_fitness: float
    best_expression: str
    avg_fitness: float
    population_diversity: float  # 唯一表达式占比


@dataclass
class OperatorEvolutionResult:
    """算子演化结果。"""

    best_expression: str
    best_fitness: float
    best_ic: float
    best_sharpe: float
    generations_completed: int
    history: list[OperatorGenerationSnapshot] = field(default_factory=list)
    total_evaluations: int = 0


@dataclass
class _FitnessScore:
    """内部适应度评分。"""

    ic: float = 0.0
    sharpe: float = 0.0
    fitness: float = _PENALTY_NO_SIGNAL


# ─── 表达式树工具 ───────────────────────────────────────────


def _node_to_str(node: ExprNode) -> str:
    """ExprNode → FTS-Expr 字符串。"""
    if node.kind in ("field", "const"):
        return node.op
    return f"{node.op}({', '.join(_node_to_str(a) for a in node.args)})"


def _node_depth(node: ExprNode) -> int:
    """计算表达式深度。"""
    if not node.args:
        return 1
    return 1 + max(_node_depth(a) for a in node.args)


def _collect_nodes(root: ExprNode) -> list[tuple[ExprNode, Optional[ExprNode], int]]:
    """收集 (节点, 父节点, 子索引)，根节点父为 (None, -1)。"""
    nodes: list[tuple[ExprNode, Optional[ExprNode], int]] = [(root, None, -1)]
    stack: list[ExprNode] = [root]
    while stack:
        node = stack.pop()
        for i, arg in enumerate(node.args):
            nodes.append((arg, node, i))
            stack.append(arg)
    return nodes


# ─── 算子演化引擎 ───────────────────────────────────────────


class OperatorEvolutionEngine:
    """DSL 算子空间适应度导向进化引擎（契约见 C.4 设计 §3.3）。

    Usage:
        engine = OperatorEvolutionEngine(data_panel, target_col="forward_return")
        result = engine.evolve()
        factor = engine.best_factor_program(result, name="op_x", market="futures",
                                            narrative="...")
    """

    def __init__(
        self,
        data_panel: pd.DataFrame,
        target_col: str,
        registry: Optional[dict[str, OperatorMeta]] = None,
        config: Optional[OperatorEvolutionConfig] = None,
        train_mask: Optional[pd.Series] = None,
        cross_section_data: Optional[dict[str, pd.DataFrame]] = None,
        cross_section_dates: Optional[pd.DatetimeIndex] = None,
    ) -> None:
        """横截面模式（GAP-146 v2.105.0+11）：注入 cross_section_data /
        cross_section_dates 后，适应度评估切换为截面 Spearman IC 口径
        （与快速预筛/正式评估同口径），消除单品种时序高分、截面无区分
        度的适应度-验收错配；未注入时保持单序列 IC/Sharpe 原逻辑。
        """
        self._data = data_panel
        self._train_mask = train_mask
        self._target_col = target_col
        self._registry = registry if registry is not None else build_registry()
        self._config = config or OperatorEvolutionConfig()
        self._rng = random.Random(self._config.random_seed)
        self._cross_section_data = cross_section_data
        self._cross_section_dates = cross_section_dates

        # 面板可用 L0 字段（面板中存在且属于 DSL 基础字段）
        self._available_fields = sorted(set(L0_FIELDS) & set(data_panel.columns)) or ["close"]

        # 内部算子池: L1 单序列时序算子 + L4 组合/双序列/条件算子（GAP-L401:
        # 放开双序列约束，条件算子 if_else/regression_residual 经参数边界护栏控制复杂度）
        self._internal_ops = [
            name for name, meta in self._registry.items() if meta.category in ("L1", "L4") and "cond" not in meta.params
        ]
        # 顶层封装池: L2 单参截面算子
        self._wrap_ops = [
            name for name, meta in self._registry.items() if meta.category == "L2" and len(meta.params) == 1
        ]

        self._fitness_cache: dict[str, _FitnessScore] = {}
        self._total_evaluations: int = 0

    # ── 表达式生成与校验 ─────────────────────────────────────

    def _valid(self, expr: str) -> bool:
        """表达式合法性校验（参数边界 + PIT lookback + 字段存在）。"""
        try:
            node = parse_expression(expr)
            errors, _ = validate_expr(node, self._registry, fields=self._available_fields)
            return not errors
        except Exception:
            return False

    def _gen_random_node(self, depth: int) -> ExprNode:
        """递归生成随机表达式节点。"""
        if depth <= 0 or (self._rng.random() < 0.35 and depth < self._config.max_tree_depth):
            return ExprNode(op=self._rng.choice(self._available_fields), kind="field")
        name = self._rng.choice(self._internal_ops)
        meta = self._registry[name]
        args: list[ExprNode] = []
        for pname in meta.params:
            if pname in meta.int_params:
                lo, hi = meta.param_bounds.get(pname, (1, 250))
                args.append(
                    ExprNode(
                        op=str(int(self._rng.randint(int(lo), int(hi)))),
                        kind="const",
                    )
                )
            elif pname in meta.float_params:
                lo, hi = meta.param_bounds.get(pname, (0.0, 1.0))
                args.append(
                    ExprNode(
                        op=str(round(self._rng.uniform(float(lo), float(hi)), 4)),
                        kind="const",
                    )
                )
            else:
                args.append(self._gen_random_node(depth - 1))
        return ExprNode(op=name, args=args)

    def _random_expression(self, max_depth: int) -> str:
        """生成一个通过校验的随机 FTS-Expr 表达式。"""
        for _ in range(self._config.max_attempts):
            node = self._gen_random_node(max_depth)
            if self._wrap_ops and self._rng.random() < 0.4:
                node = ExprNode(op=self._rng.choice(self._wrap_ops), args=[node])
            expr = _node_to_str(node)
            if self._valid(expr):
                return expr
        raise RuntimeError("算子演化: 无法生成合法随机表达式")

    def _initialize_population(self, size: int) -> list[str]:
        """初始化种群（全部为合法表达式）。"""
        return [self._random_expression(self._config.max_tree_depth) for _ in range(size)]

    # ── 适应度评估 ───────────────────────────────────────────

    def _evaluate_fitness(self, expr: str) -> _FitnessScore:
        """评估单个表达式适应度（IC + Sharpe，带缓存）。

        数据泄露防护: 当 train_mask 存在时，仅在训练集上计算适应度，
        确保 OOS 数据不被用于 GP/算子搜索过程中的选择。

        横截面模式（GAP-146）：注入 cross_section_data 后分派
        `_evaluate_cs_fitness`，以截面 Spearman IC 为适应度。
        """
        if self._cross_section_data is not None:
            return self._evaluate_cs_fitness(expr)

        cached = self._fitness_cache.get(expr)
        if cached is not None:
            return cached

        self._total_evaluations += 1
        try:
            node = parse_expression(expr)
            # GAP-I202 (v2.75.0): 纯字段/无算子表达式（lookback=0）罚分——
            # 算子演化产物应包含实际算子变换，避免裸字段包装（如 rank(close)）
            # 在单调合成数据上以虚假高 IC 占据最优；与常信号罚分同一档强度。
            from .expr_dsl.validator import compute_max_lookback

            if compute_max_lookback(node, self._registry) == 0:
                score = _FitnessScore(fitness=_PENALTY_WEAK)
                self._fitness_cache[expr] = score
                return score
            # 使用训练掩码防止数据泄露
            eval_data = self._data[self._train_mask] if self._train_mask is not None else self._data
            values = evaluate(node, eval_data, self._registry)
        except Exception:
            score = _FitnessScore(fitness=_PENALTY_WEAK)
            self._fitness_cache[expr] = score
            return score

        if isinstance(values, float):
            values = pd.Series(values, index=eval_data.index)
        else:
            values = pd.Series(values)
        values.index = eval_data.index

        if values.isna().all():
            score = _FitnessScore(fitness=_PENALTY_NO_SIGNAL)
            self._fitness_cache[expr] = score
            return score

        target = eval_data[self._target_col]
        aligned = pd.concat([values, target], axis=1).dropna()
        if len(aligned) < _MIN_SAMPLES:
            score = _FitnessScore(fitness=_PENALTY_NO_SIGNAL)
            self._fitness_cache[expr] = score
            return score

        ic = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if pd.isna(ic):
            score = _FitnessScore(fitness=_PENALTY_WEAK)
            self._fitness_cache[expr] = score
            return score

        sharpe = 0.0
        rets = values.pct_change().dropna()
        if len(rets) > 1 and rets.std() > 0:
            sharpe = float(rets.mean() / rets.std() * np.sqrt(252))

        metric = self._config.fitness_metric
        if metric == "ic":
            fitness = abs(ic)
        elif metric == "sharpe":
            fitness = sharpe
        else:  # ic_sharpe_combo
            fitness = abs(ic) * 0.6 + max(sharpe, 0) * 0.4

        score = _FitnessScore(ic=float(ic), sharpe=float(sharpe), fitness=float(fitness))
        self._fitness_cache[expr] = score
        return score

    def _evaluate_cs_fitness(self, expr: str) -> _FitnessScore:
        """横截面模式适应度（GAP-146 v2.105.0+11）。

        对每品种执行 DSL 表达式 + 5 日前向收益（与评估链
        `_cs_execute_factors` 同口径），按共同日期前 60% 训练段对齐，
        逐期截面 Spearman IC 均值作为适应度——与快速预筛/正式评估
        （`_cs_compute_ics`）完全同口径，引导算子搜索直接优化截面区分度。

        Returns:
            截面 IC 均值（可为负，fitness 取绝对值）；有效品种 <5、
            共同日期为空或截面 IC 全无效时按罚分处理。
        """
        from .evaluation_chain import _cs_build_matrices, _cs_compute_ics
        from .expr_dsl.validator import compute_max_lookback

        cached = self._fitness_cache.get(expr)
        if cached is not None:
            return cached

        self._total_evaluations += 1
        try:
            node = parse_expression(expr)
            # 纯字段/无算子表达式（lookback=0）罚分（与单序列路径 GAP-I202 同规则）
            if compute_max_lookback(node, self._registry) == 0:
                score = _FitnessScore(fitness=_PENALTY_WEAK)
                self._fitness_cache[expr] = score
                return score
        except Exception:
            score = _FitnessScore(fitness=_PENALTY_WEAK)
            self._fitness_cache[expr] = score
            return score

        signal_dict: dict[str, pd.Series] = {}
        ret_dict: dict[str, pd.Series] = {}
        for sym, df in self._cross_section_data.items():
            try:
                values = evaluate(node, df, self._registry)
                if isinstance(values, float):
                    values = pd.Series(values, index=df.index)
                else:
                    values = pd.Series(values)
                    values.index = df.index
                signal_dict[sym] = values
                closes = df["close"].values
                fwd_ret = np.zeros(len(closes))
                if len(closes) > 5:
                    fwd_ret[:-5] = (closes[5:] - closes[:-5]) / np.maximum(closes[:-5], 1e-10)
                ret_dict[sym] = pd.Series(fwd_ret, index=df.index)
            except Exception:  # noqa: BLE001 - 单品种失败跳过，与预筛一致
                continue

        if len(signal_dict) < 5:
            score = _FitnessScore(fitness=_PENALTY_NO_SIGNAL)
            self._fitness_cache[expr] = score
            return score

        common = self._cross_section_dates
        if common is None or len(common) == 0:
            score = _FitnessScore(fitness=_PENALTY_NO_SIGNAL)
            self._fitness_cache[expr] = score
            return score

        # 数据泄露防护: 仅训练段（与单序列路径 train_mask 同语义，前 60%）
        if self._train_mask is not None and len(self._train_mask) == len(common):
            train_dates = common[self._train_mask.values]
        else:
            n_train = max(int(len(common) * 0.6), 1)
            train_dates = common[:n_train]
        if len(train_dates) < _MIN_SAMPLES:
            score = _FitnessScore(fitness=_PENALTY_NO_SIGNAL)
            self._fitness_cache[expr] = score
            return score

        signal_matrix, ret_matrix = _cs_build_matrices(
            signal_dict,
            ret_dict,
            train_dates,
            len(train_dates),
        )
        ics = _cs_compute_ics(signal_matrix, ret_matrix)
        if not ics:
            score = _FitnessScore(fitness=_PENALTY_NO_SIGNAL)
            self._fitness_cache[expr] = score
            return score

        ic_mean = float(np.mean(ics))
        score = _FitnessScore(ic=ic_mean, sharpe=0.0, fitness=abs(ic_mean))
        self._fitness_cache[expr] = score
        return score

    # ── 进化算子 ─────────────────────────────────────────────

    def _crossover(self, p1: str, p2: str) -> str:
        """子树交换交叉：p1 随机内部节点 ← p2 随机节点子树。"""
        for _ in range(self._config.max_attempts):
            try:
                root1 = copy.deepcopy(parse_expression(p1))
                root2 = parse_expression(p2)
                nodes1 = _collect_nodes(root1)
                internal1 = [t for t in nodes1 if t[0].kind == "op"]
                if not internal1:
                    return p1
                _, parent, idx = self._rng.choice(internal1)
                donor = self._rng.choice(_collect_nodes(root2))[0]
                assert parent is not None
                parent.args[idx] = copy.deepcopy(donor)
                if _node_depth(root1) > self._config.max_tree_depth:
                    continue
                expr = _node_to_str(root1)
                if self._valid(expr):
                    return expr
            except Exception:
                continue
        return p1

    def _mutate(self, expr: str) -> str:
        """变异：子树替换 / 参数扰动 / 字段替换（随机选一，校验失败重试）。"""
        for _ in range(self._config.max_attempts):
            try:
                root = parse_expression(expr)
                nodes = _collect_nodes(root)
                consts = [t for t in nodes if t[0].kind == "const"]
                fields = [t for t in nodes if t[0].kind == "field"]
                ops = [t for t in nodes if t[0].kind == "op"]

                strategies: list[str] = []
                if ops:
                    strategies.append("subtree")
                if consts:
                    strategies.append("param")
                if fields:
                    strategies.append("field")
                if not strategies:
                    return expr
                strategy = self._rng.choice(strategies)

                if strategy == "subtree":
                    node, parent, idx = self._rng.choice(ops)
                    depth_budget = max(
                        1,
                        self._config.max_tree_depth - _node_depth(node) + 1,
                    )
                    assert parent is not None
                    parent.args[idx] = self._gen_random_node(depth_budget)
                elif strategy == "param":
                    const, _, _ = self._rng.choice(consts)
                    old = float(const.op)
                    new_val = old * self._rng.uniform(0.8, 1.2)
                    const.op = str(int(round(new_val))) if float(const.op).is_integer() else str(round(new_val, 4))
                    if abs(new_val) > 250 or abs(new_val) < 1e-6:
                        continue
                else:  # field
                    fnode, _, _ = self._rng.choice(fields)
                    fnode.op = self._rng.choice(self._available_fields)

                expr_out = _node_to_str(root)
                if self._valid(expr_out):
                    return expr_out
            except Exception:
                continue
        return expr

    def _tournament_select(
        self,
        evaluated: list[tuple[str, _FitnessScore]],
    ) -> str:
        """锦标赛选择。"""
        k = min(self._config.tournament_size, len(evaluated))
        candidates = self._rng.sample(evaluated, k)
        return max(candidates, key=lambda t: t[1].fitness)[0]

    def _evolve_population(
        self,
        evaluated: list[tuple[str, _FitnessScore]],
    ) -> list[str]:
        """生成下一代：精英保留 + 锦标赛选择 + 交叉 + 变异。"""
        config = self._config
        sorted_pop = sorted(evaluated, key=lambda t: t[1].fitness, reverse=True)
        new_population = [expr for expr, _ in sorted_pop[: config.elitism_size]]

        while len(new_population) < config.population_size:
            p1 = self._tournament_select(evaluated)
            p2 = self._tournament_select(evaluated)
            if self._rng.random() < config.crossover_rate:
                child = self._crossover(p1, p2)
            else:
                child = p1
            if self._rng.random() < config.mutation_rate:
                child = self._mutate(child)
            new_population.append(child)

        return new_population

    # ── 主入口 ───────────────────────────────────────────────

    def evolve(self) -> OperatorEvolutionResult:
        """执行算子空间进化搜索，返回最优表达式。"""
        population = self._initialize_population(self._config.population_size)

        best_expr: Optional[str] = None
        best_score = _FitnessScore(fitness=-float("inf"))
        history: list[OperatorGenerationSnapshot] = []

        for gen in range(self._config.max_generations):
            evaluated: list[tuple[str, _FitnessScore]] = [(expr, self._evaluate_fitness(expr)) for expr in population]
            evaluated.sort(key=lambda t: t[1].fitness, reverse=True)

            gen_best_expr, gen_best_score = evaluated[0]
            if gen_best_score.fitness > best_score.fitness:
                best_expr = gen_best_expr
                best_score = gen_best_score

            avg_fitness = sum(s.fitness for _, s in evaluated) / len(evaluated)
            diversity = len({expr for expr, _ in evaluated}) / len(evaluated)
            history.append(
                OperatorGenerationSnapshot(
                    generation=gen,
                    best_fitness=gen_best_score.fitness,
                    best_expression=gen_best_expr,
                    avg_fitness=avg_fitness,
                    population_diversity=diversity,
                )
            )

            logger.info(
                "算子演化 Gen %d: best=%.4f (IC=%.4f, Sharpe=%.4f), avg=%.4f, 多样性=%.0f%% | %s",
                gen,
                gen_best_score.fitness,
                gen_best_score.ic,
                gen_best_score.sharpe,
                avg_fitness,
                diversity * 100,
                gen_best_expr[:60],
            )

            population = self._evolve_population(evaluated)

        assert best_expr is not None
        return OperatorEvolutionResult(
            best_expression=best_expr,
            best_fitness=best_score.fitness,
            best_ic=best_score.ic,
            best_sharpe=best_score.sharpe,
            generations_completed=len(history),
            history=history,
            total_evaluations=self._total_evaluations,
        )

    def best_factor_program(
        self,
        result: OperatorEvolutionResult,
        *,
        name: str,
        market: str,
        narrative: str,
        trace_id: Optional[str] = None,
        parent_id: Optional[str] = None,
        generation: int = 0,
        source: str = "operator_evolution",
    ) -> FactorProgram:
        """最优表达式 → OPERATOR 类型因子（契约见 C.4 设计 §3.3）。"""
        factor = create_operator_factor(
            expression=result.best_expression,
            name=name,
            market=market,
            narrative=narrative,
            params={},
            trace_id=trace_id,
            source=source,
        )
        if parent_id:
            factor["parent_id"] = parent_id
        if generation:
            factor["generation"] = generation
        return factor
