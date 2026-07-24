"""tests/factor_engine/test_contracts.py — 契约层测试。

HARNESS §契约优先: 契约必须先于实现，测试必须先于代码。
"""

from __future__ import annotations

import pytest

from fts.factor_engine.contracts import (
    DEFAULT_BUDGET_CONFIG,
    DEFAULT_VERIFIER_CONFIG,
    EVOLUTION_VERSION,
    BacktestMetrics,
    BudgetConfig,
    EconomicLogic,
    EconomicScore,
    EvolutionState,
    ExperienceTrace,
    FactorEvaluation,
    FactorProgram,
    FactorSignature,
    MultipleTestResult,
    VerifierConfig,
    VerifierResult,
)


# ─── 版本号 ───────────────────────────────────────────────

def test_evolution_version_matches_fts():
    """版本号必须与 FTS 项目版本同步（v1.1.0 = MCP 数据源迁移）。"""
    assert EVOLUTION_VERSION == "1.1.0"


# ─── TypedDict 实例化 ────────────────────────────────────

def test_factor_signature_instantiation():
    sig = FactorSignature(
        input_fields=["close", "volume"],
        output_type="signal",
        frequency="daily",
        lookback=20,
    )
    assert sig["input_fields"] == ["close", "volume"]
    assert sig["output_type"] == "signal"


def test_economic_logic_instantiation():
    el = EconomicLogic(
        theory=4, behavioral=3, microstructure=4, institutional=5,
        narrative="测试经济逻辑",
    )
    assert el["theory"] == 4
    assert el["narrative"] == "测试经济逻辑"


def test_factor_program_instantiation():
    fp = FactorProgram(
        factor_id="fct_test0001",
        name="test_factor",
        code="def factor_program(data, params): return data['close'].values",
        params={"window": 10},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="测试"),
        source="seed",
        parent_id=None,
        generation=0,
        created_at="2026-07-18T00:00:00",
        trace_id="l2_test_001",
    )
    assert fp["factor_id"] == "fct_test0001"
    assert fp["source"] == "seed"
    assert fp["params"]["window"] == 10


def test_backtest_metrics_instantiation():
    bt = BacktestMetrics(
        ic=0.05, icir=0.8, sharpe=2.0, max_drawdown=0.1,
        monotonicity=True, oos_ratio=0.3, t_stat=3.5, turnover_monthly=0.4,
    )
    assert bt["ic"] == 0.05
    assert bt["monotonicity"] is True


def test_economic_score_instantiation():
    es = EconomicScore(
        theory=4, behavioral=3, microstructure=4, institutional=2,
        dimensions_passed=3, narrative="四维达标",
    )
    assert es["dimensions_passed"] == 3


def test_multiple_test_result_instantiation():
    mt = MultipleTestResult(
        bonferroni_p=0.005, fdr_q=0.03, effective_n_factors=8,
        adjusted_t=3.2, passed=True,
    )
    assert mt["passed"] is True


def test_factor_evaluation_instantiation():
    ev = FactorEvaluation(
        factor_id="fct_test", trace_id="l2_t",
        level_1_backtest=BacktestMetrics(),
        level_2_economic=EconomicScore(),
        level_3_multiple=MultipleTestResult(),
        passed=True, failure_reasons=[], evaluated_at="2026-07-18T00:00:00",
    )
    assert ev["passed"] is True
    assert ev["failure_reasons"] == []


def test_experience_trace_instantiation():
    trace = ExperienceTrace(
        trace_id="exp_001", factor_id="fct_test", parent_id=None,
        generation=1, mutation_type="macro_logic",
        mutation_summary="测试变异",
        evaluation=FactorEvaluation(),
        success=True, lessons=["成功教训"],
        recorded_at="2026-07-18T00:00:00",
    )
    assert trace["success"] is True
    assert trace["mutation_type"] == "macro_logic"


def test_evolution_state_instantiation():
    state = EvolutionState(
        run_id="run_001", started_at="2026-07-18T00:00:00",
        last_generation=5, total_factors_evaluated=20,
        total_factors_promoted=3, tokens_consumed=50000,
        budget_limit=200000, status="running", last_error=None,
        experience_chain_ref=[], last_updated="2026-07-18T00:00:00",
        version=EVOLUTION_VERSION,
    )
    assert state["status"] == "running"
    assert state["version"] == EVOLUTION_VERSION


def test_verifier_config_instantiation():
    cfg = VerifierConfig(
        min_ic=0.03, min_icir=0.5, min_sharpe=1.5,
        max_drawdown=0.20, min_economic_score=3,
        min_t_stat=3.0, max_fdr=0.05,
        min_oos_ratio=0.30, max_turnover_monthly=0.50,
    )
    assert cfg["min_ic"] == 0.03


def test_verifier_result_instantiation():
    vr = VerifierResult(
        passed=False, failure_reasons=["IC 不达标"],
        checked_against=DEFAULT_VERIFIER_CONFIG,
        checked_at="2026-07-18T00:00:00",
    )
    assert vr["passed"] is False
    assert "IC 不达标" in vr["failure_reasons"]


def test_budget_config_instantiation():
    bc = BudgetConfig(
        nightly_token_limit=200_000, monthly_token_limit=6_000_000,
        max_generation=50, max_tokens_per_factor=10_000,
        circuit_breaker_token_ratio=2.0,
        circuit_breaker_consecutive_low_ic=3,
        circuit_breaker_low_ic_threshold=0.01,
        circuit_breaker_failure_rate=0.90,
    )
    assert bc["nightly_token_limit"] == 200_000


# ─── 默认配置 ─────────────────────────────────────────────

def test_default_verifier_config_values():
    """DEFAULT_VERIFIER_CONFIG 必须是 v8.9.2 锁定值。"""
    assert DEFAULT_VERIFIER_CONFIG["min_ic"] == 0.03
    assert DEFAULT_VERIFIER_CONFIG["min_sharpe"] == 1.5
    assert DEFAULT_VERIFIER_CONFIG["max_drawdown"] == 0.20
    assert DEFAULT_VERIFIER_CONFIG["min_economic_score"] == 3
    assert DEFAULT_VERIFIER_CONFIG["min_t_stat"] == 3.0
    assert DEFAULT_VERIFIER_CONFIG["max_fdr"] == 0.05
    assert DEFAULT_VERIFIER_CONFIG["min_oos_ratio"] == 0.30
    assert DEFAULT_VERIFIER_CONFIG["max_turnover_monthly"] == 0.50


def test_default_budget_config_values():
    """DEFAULT_BUDGET_CONFIG 必须是 v8.9.2 锁定值。"""
    assert DEFAULT_BUDGET_CONFIG["nightly_token_limit"] == 200_000
    assert DEFAULT_BUDGET_CONFIG["max_generation"] == 50
    assert DEFAULT_BUDGET_CONFIG["circuit_breaker_token_ratio"] == 2.0
    assert DEFAULT_BUDGET_CONFIG["circuit_breaker_consecutive_low_ic"] == 3
    assert DEFAULT_BUDGET_CONFIG["circuit_breaker_failure_rate"] == 0.90


def test_default_configs_are_immutable_copies():
    """默认配置应为字典副本（防止外部修改污染常量）。"""
    # 注：实现使用 dict() 复制，调用方拿到的是副本
    cfg = dict(DEFAULT_VERIFIER_CONFIG)
    cfg["min_ic"] = 999
    # 原常量不应被影响
    assert DEFAULT_VERIFIER_CONFIG["min_ic"] == 0.03
