"""算子演化引擎测试（Phase 3+ / C.4）。

覆盖: 种群初始化合法性 / 进化收敛 / 交叉变异产物校验 /
      产物为 OPERATOR 因子 / 常信号罚分 / 评估缓存。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorKind
from fts.factor_engine.expr_dsl.parser import parse_expression
from fts.factor_engine.expr_dsl.registry import build_registry
from fts.factor_engine.expr_dsl.validator import validate_expr
from fts.factor_engine.factor_program import validate_factor_code
from fts.factor_engine.operator_evolution import (
    OperatorEvolutionConfig,
    OperatorEvolutionEngine,
    OperatorEvolutionResult,
)


def _make_panel(n: int = 300, seed: int = 42, monotonic: bool = False) -> pd.DataFrame:
    """构造合成 OHLCV 面板 + forward_return 目标列。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    if monotonic:
        close = 100.0 * np.exp(np.cumsum(np.full(n, 0.005)))
    else:
        trend = np.linspace(0, 1, n) * 0.8
        close = 100.0 * np.exp(np.cumsum(trend * 0.002 + rng.normal(0, 0.01, n)))
    vol = rng.uniform(1000, 5000, n)
    df = pd.DataFrame(
        {
            "open": close * (1 + rng.normal(0, 0.002, n)),
            "high": close * (1 + np.abs(rng.normal(0, 0.004, n))),
            "low": close * (1 - np.abs(rng.normal(0, 0.004, n))),
            "close": close,
            "volume": vol,
        },
        index=dates,
    )
    ret = pd.Series(close).pct_change()
    df["forward_return"] = ret.shift(-1).to_numpy()
    return df


def _valid(expr: str) -> bool:
    registry = build_registry()
    try:
        node = parse_expression(expr)
        errors, _ = validate_expr(node, registry)
    except Exception:
        return False
    return not errors


@pytest.fixture()
def panel() -> pd.DataFrame:
    return _make_panel(monotonic=True)


@pytest.fixture()
def engine(panel: pd.DataFrame) -> OperatorEvolutionEngine:
    return OperatorEvolutionEngine(
        data_panel=panel,
        target_col="forward_return",
        config=OperatorEvolutionConfig(
            population_size=20,
            max_generations=5,
            random_seed=7,
            max_attempts=30,
        ),
    )


# ── 1. 种群初始化合法性 ─────────────────────────────────────


def test_initialization_produces_valid_expressions(engine: OperatorEvolutionEngine):
    for _ in range(30):
        expr = engine._random_expression(max_depth=4)
        assert isinstance(expr, str) and expr
        assert _valid(expr), f"非法表达式: {expr}"


def test_initialized_population_all_valid(engine: OperatorEvolutionEngine):
    population = engine._initialize_population(engine._config.population_size)
    assert len(population) == engine._config.population_size
    for expr in population:
        assert _valid(expr), f"非法表达式: {expr}"


# ── 2. 进化搜索 ─────────────────────────────────────────────


def test_evolve_returns_result(engine: OperatorEvolutionEngine):
    result = engine.evolve()
    assert isinstance(result, OperatorEvolutionResult)
    assert result.best_expression and _valid(result.best_expression)
    assert result.generations_completed == engine._config.max_generations
    assert result.total_evaluations >= engine._config.population_size
    assert len(result.history) == result.generations_completed


def test_evolve_best_fitness_positive(engine: OperatorEvolutionEngine):
    result = engine.evolve()
    # 单调数据下至少一个时序因子 IC>0，不应停留在罚分
    assert result.best_fitness > 0, f"best_fitness={result.best_fitness}"


def test_evolve_best_not_worse_than_first_gen(engine: OperatorEvolutionEngine):
    result = engine.evolve()
    first_best = result.history[0].best_fitness
    assert result.best_fitness >= first_best - 1e-9


def test_evolve_reproducible_with_seed(panel: pd.DataFrame):
    e1 = OperatorEvolutionEngine(
        panel, "forward_return",
        config=OperatorEvolutionConfig(population_size=12, max_generations=3, random_seed=11),
    )
    e2 = OperatorEvolutionEngine(
        panel, "forward_return",
        config=OperatorEvolutionConfig(population_size=12, max_generations=3, random_seed=11),
    )
    assert e1.evolve().best_expression == e2.evolve().best_expression


# ── 3. 交叉 / 变异产物校验 ──────────────────────────────────


def test_crossover_products_valid(engine: OperatorEvolutionEngine):
    for _ in range(20):
        p1 = engine._random_expression(4)
        p2 = engine._random_expression(4)
        child = engine._crossover(p1, p2)
        assert isinstance(child, str)
        assert _valid(child), f"交叉产物非法: {child}"


def test_mutation_products_valid(engine: OperatorEvolutionEngine):
    for _ in range(20):
        expr = engine._random_expression(4)
        mutated = engine._mutate(expr)
        assert isinstance(mutated, str)
        assert _valid(mutated), f"变异产物非法: {mutated}"


# ── 4. 产物为 OPERATOR 因子 ─────────────────────────────────


def test_best_factor_program_is_operator(engine: OperatorEvolutionEngine):
    result = engine.evolve()
    factor = engine.best_factor_program(
        result,
        name="op_evolved_001",
        market="futures",
        family="operator",
        narrative="算子演化测试因子",
        trace_id="test-trace-001",
        parent_id="fct_parent",
        generation=1,
    )
    assert factor["kind"] == FactorKind.OPERATOR
    assert factor["expression"] == result.best_expression
    assert factor["max_lookback"] >= 1
    assert factor["parent_id"] == "fct_parent"
    ok, reasons = validate_factor_code(factor["code"])
    assert ok, reasons


# ── 5. 常信号 / 无效数据罚分 ────────────────────────────────


def test_constant_signal_penalized():
    n = 200
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    df = pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 1000.0,
            "forward_return": rng.normal(0, 0.01, n),
        },
        index=dates,
    )
    eng = OperatorEvolutionEngine(
        df, "forward_return",
        config=OperatorEvolutionConfig(population_size=8, max_generations=2, random_seed=3),
    )
    # 常数表达式（close 恒为 100 → 全 0 信号 → 罚分）
    fitness = eng._evaluate_fitness("close").fitness
    assert fitness < 0, f"常数信号应罚分, fitness={fitness}"


def test_evaluate_fitness_cache(engine: OperatorEvolutionEngine):
    f1 = engine._evaluate_fitness("ts_mean(close, 20)")
    f2 = engine._evaluate_fitness("ts_mean(close, 20)")
    assert f1.fitness == f2.fitness
    assert engine._total_evaluations == 1  # 相同表达式只评估一次


# ── 6. evolution_loop 集成（operator 模式走引擎） ───────────


def _fake_result():
    return OperatorEvolutionResult(
        best_expression="rank(ts_zscore(close, 60))",
        best_fitness=0.5,
        best_ic=0.1,
        best_sharpe=0.5,
        generations_completed=1,
        history=[],
        total_evaluations=1,
    )


def test_evolution_loop_operator_mode_calls_engine(monkeypatch, panel):
    from fts.factor_engine.evolution_loop import EvolutionLoop

    loop = EvolutionLoop(
        data=panel.drop(columns=["forward_return"]),
        forward_returns=panel["forward_return"].to_numpy(),
        market="futures",
    )
    calls: list[int] = []

    def fake_evolve(self):
        calls.append(1)
        return _fake_result()

    monkeypatch.setattr(OperatorEvolutionEngine, "evolve", fake_evolve)

    parent = {"factor_id": "fct_parent_1", "name": "parent", "family": "trend"}
    factor, summary = loop._generate_operator_factor(
        parent, generation=0, trace_id="it-001",
    )
    assert calls == [1], "operator 模式应调用算子演化引擎"
    assert factor["kind"] == FactorKind.OPERATOR
    assert factor["expression"] == "rank(ts_zscore(close, 60))"
    assert "OpEvolve" in summary


def test_operator_engine_skipped_without_forward_returns(monkeypatch, panel):
    from fts.factor_engine.evolution_loop import EvolutionLoop

    loop = EvolutionLoop(
        data=panel.drop(columns=["forward_return"]),
        forward_returns=None,
        market="futures",
    )
    calls: list[int] = []

    def fake_evolve(self):
        calls.append(1)
        return _fake_result()

    monkeypatch.setattr(OperatorEvolutionEngine, "evolve", fake_evolve)

    parent = {"factor_id": "fct_parent_1", "name": "parent", "family": "trend"}
    factor, summary = loop._generate_operator_factor(
        parent, generation=0, trace_id="it-002",
    )
    assert calls == [], "无 forward_returns 时不应调用引擎，回退随机生成"
    assert factor["kind"] == FactorKind.OPERATOR
    assert factor.get("expression")
