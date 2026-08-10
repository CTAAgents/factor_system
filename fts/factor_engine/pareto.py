"""
fts.factor_engine.pareto — Pareto 多目标优化工具（GAP-I204 二期，v2.78.0）。

提供 NSGA-II 风格的快速非支配排序与 Pareto 前沿提取，用于 GP 演化 /
符号回归搜索的多目标候选输出（多目标：|IC|、Sharpe 最大化；换手、衰减最小化）。

用法:
    from fts.factor_engine.pareto import ParetoItem, compute_pareto_front

    items = [
        ParetoItem(expression="ts_mean(close)", ic=0.05, sharpe=0.8,
                   turnover=0.3, decay=0.1, fitness=0.5),
        ...
    ]
    front = compute_pareto_front(items)

版本: v1.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass
class ParetoItem:
    """Pareto 前沿个体（多目标评估结果，供人审）。

    GAP-I204 二期：多目标为 |IC|、Sharpe（越大越好）与换手、衰减（越小越好）。
    """

    expression: str = ""
    """因子表达式。"""
    ic: float = 0.0
    """IC（带符号，评估时用绝对值）。"""
    sharpe: float = 0.0
    """Sharpe。"""
    turnover: float = 0.0
    """信号换手（越小越好）。"""
    decay: float = 0.0
    """信号衰减（越小越好）。"""
    fitness: float = 0.0
    """合成适应度（multi_objective 口径，仅供排序参考）。"""
    source: str = "gp"
    """来源：gp / symbolic。"""

    @property
    def objectives(self) -> tuple[float, float, float, float]:
        """多目标向量（全部统一为「越大越好」口径）。

        Returns:
            (|ic|, sharpe, -turnover, -decay)
        """
        return (abs(self.ic), self.sharpe, -self.turnover, -self.decay)


def _dominates(a: ParetoItem, b: ParetoItem) -> bool:
    """a 是否支配 b（a 所有目标 ≥ b 且至少一个严格 >）。"""
    oa, ob = a.objectives, b.objectives
    ge_all = all(x >= y for x, y in zip(oa, ob))
    gt_any = any(x > y for x, y in zip(oa, ob))
    return ge_all and gt_any


def fast_non_dominated_sort(items: Iterable[ParetoItem]) -> list[list[ParetoItem]]:
    """NSGA-II 快速非支配排序。

    Args:
        items: 待排序个体集合。

    Returns:
        按 rank 分层的列表（front[0] = rank 0 非支配前沿，逐层向外）。
    """
    pool = list(items)
    fronts: list[list[ParetoItem]] = []
    remaining = pool
    while remaining:
        current: list[ParetoItem] = []
        dominated_by_others: list[ParetoItem] = []
        for a in remaining:
            if any(_dominates(b, a) for b in remaining if b is not a):
                dominated_by_others.append(a)
            else:
                current.append(a)
        if not current:
            # 理论不可达（剩余池非空必有非支配个体），防御性截断。
            fronts.append(dominated_by_others)
            break
        fronts.append(current)
        remaining = [x for x in dominated_by_others]
    return fronts


def compute_pareto_front(items: Iterable[ParetoItem]) -> list[ParetoItem]:
    """提取多目标 Pareto 前沿（rank 0 非支配解集）。

    Args:
        items: 候选个体集合。

    Returns:
        Pareto 前沿个体列表（无支配关系）。
    """
    pool = [i for i in items if i.expression]
    if not pool:
        return []
    fronts = fast_non_dominated_sort(pool)
    front = list(fronts[0]) if fronts else []
    # 按合成适应度降序，供人审优先展示高适应度个体。
    front.sort(key=lambda i: i.fitness, reverse=True)
    return front
