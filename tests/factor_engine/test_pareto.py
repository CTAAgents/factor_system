"""tests/factor_engine/test_pareto.py — Pareto 多目标前沿工具测试（GAP-I204 二期）。"""

from __future__ import annotations

from fts.factor_engine.pareto import (
    ParetoItem,
    compute_pareto_front,
    fast_non_dominated_sort,
)


def _mk(
    expr: str,
    ic: float = 0.0,
    sharpe: float = 0.0,
    turnover: float = 0.0,
    decay: float = 0.0,
    fitness: float = 0.0,
    source: str = "gp",
) -> ParetoItem:
    return ParetoItem(
        expression=expr,
        ic=ic,
        sharpe=sharpe,
        turnover=turnover,
        decay=decay,
        fitness=fitness,
        source=source,
    )


class TestParetoItem:
    def test_objectives_maximization_orientation(self):
        """objectives 统一为「越大越好」口径（|ic|, sharpe, -turnover, -decay）。"""
        item = _mk("a", ic=-0.05, sharpe=1.0, turnover=0.3, decay=0.2)
        assert item.objectives == (0.05, 1.0, -0.3, -0.2)

    def test_defaults(self):
        item = ParetoItem()
        assert item.expression == ""
        assert item.source == "gp"
        assert item.objectives == (0.0, 0.0, 0.0, 0.0)


class TestFastNonDominatedSort:
    def test_single_item(self):
        items = [_mk("a", ic=0.05)]
        fronts = fast_non_dominated_sort(items)
        assert len(fronts) == 1
        assert len(fronts[0]) == 1

    def test_two_layers(self):
        """a 支配 b → a 在 rank0，b 在 rank1。"""
        a = _mk("a", ic=0.1, sharpe=1.0, turnover=0.1, decay=0.1)
        b = _mk("b", ic=0.05, sharpe=0.5, turnover=0.5, decay=0.5)
        fronts = fast_non_dominated_sort([b, a])
        assert [i.expression for i in fronts[0]] == ["a"]
        assert [i.expression for i in fronts[1]] == ["b"]

    def test_mutually_nondominated_same_rank(self):
        """a 与 b 互不支配（各占一个目标优势）→ 同 rank0。"""
        a = _mk("a", ic=0.10, sharpe=0.5)
        b = _mk("b", ic=0.05, sharpe=1.0)
        fronts = fast_non_dominated_sort([a, b])
        assert len(fronts[0]) == 2

    def test_identical_items_nondominated(self):
        """目标完全相同的个体互不支配 → 同 rank0。"""
        a = _mk("a", ic=0.05)
        b = _mk("b", ic=0.05)
        fronts = fast_non_dominated_sort([a, b])
        assert len(fronts[0]) == 2

    def test_chain_dominance(self):
        """链式支配：c 被 b 支配，b 被 a 支配 → 三层。"""
        a = _mk("a", ic=0.30, sharpe=1.0, turnover=0.1)
        b = _mk("b", ic=0.20, sharpe=0.8, turnover=0.2)
        c = _mk("c", ic=0.10, sharpe=0.5, turnover=0.3)
        fronts = fast_non_dominated_sort([c, a, b])
        assert [i.expression for i in fronts[0]] == ["a"]
        assert [i.expression for i in fronts[1]] == ["b"]
        assert [i.expression for i in fronts[2]] == ["c"]


class TestComputeParetoFront:
    def test_empty(self):
        assert compute_pareto_front([]) == []

    def test_ignores_blank_expression(self):
        front = compute_pareto_front([ParetoItem(), _mk("a", ic=0.05)])
        assert len(front) == 1
        assert front[0].expression == "a"

    def test_returns_rank0_sorted_by_fitness(self):
        """前沿按合成适应度降序输出。"""
        items = [
            _mk("low_ic_high_sharpe", ic=0.02, sharpe=2.0, fitness=0.5),
            _mk("high_ic_low_sharpe", ic=0.08, sharpe=0.5, fitness=0.9),
        ]
        front = compute_pareto_front(items)
        assert len(front) == 2  # 互不支配，均在前沿
        assert front[0].expression == "high_ic_low_sharpe"  # fitness 高者优先

    def test_dominated_excluded(self):
        dominated = _mk("dominated", ic=0.01, sharpe=0.1, turnover=0.9, decay=0.9, fitness=0.1)
        dom1 = _mk("d1", ic=0.10, sharpe=0.5, turnover=0.1, fitness=0.9)
        dom2 = _mk("d2", ic=0.09, sharpe=0.4, turnover=0.2, fitness=0.8)
        front = compute_pareto_front([dominated, dom1, dom2])
        assert all(i.expression != "dominated" for i in front)
        # d1 支配 d2（所有目标 ≥ 且至少一个严格 >）→ 前沿仅 d1
        assert len(front) == 1
        assert front[0].expression == "d1"

    def test_turnover_decay_penalize_dominance(self):
        """低换手/低衰减个体支配同 IC/Sharpe 高换手个体。"""
        clean = _mk("clean", ic=0.06, sharpe=1.0, turnover=0.1, decay=0.05)
        choppy = _mk("choppy", ic=0.06, sharpe=1.0, turnover=0.9, decay=0.5)
        front = compute_pareto_front([clean, choppy])
        assert len(front) == 1
        assert front[0].expression == "clean"
