"""tests/factor_engine/test_verifier.py — Verifier 协议测试。"""

from __future__ import annotations

import pytest

from fts.factor_engine.contracts import (
    DEFAULT_VERIFIER_CONFIG,
    BacktestMetrics,
    EconomicScore,
    FactorEvaluation,
    MultipleTestResult,
    VerifierConfig,
)
from fts.factor_engine.verifier import (
    FactorVerifier,
    VerifierAlreadyLockedError,
    VerifierNotLockedError,
    get_global_verifier,
    reset_global_verifier,
)


@pytest.fixture
def passing_evaluation() -> FactorEvaluation:
    """完全通过的评估结果。"""
    return FactorEvaluation(
        factor_id="fct_pass", trace_id="l2_t",
        level_1_backtest=BacktestMetrics(
            ic=0.05, icir=0.8, sharpe=2.0, max_drawdown=0.1,
            monotonicity=True, oos_ratio=0.4, t_stat=3.5,
            turnover_monthly=0.3,
        ),
        level_2_economic=EconomicScore(
            theory=4, behavioral=3, microstructure=4, institutional=5,
            dimensions_passed=4, narrative="四维全达标",
        ),
        level_3_multiple=MultipleTestResult(
            bonferroni_p=0.005, fdr_q=0.03, effective_n_factors=8,
            adjusted_t=3.2, passed=True,
        ),
        passed=True, failure_reasons=[], evaluated_at="2026-07-18T00:00:00",
    )


@pytest.fixture
def failing_evaluation_low_ic() -> FactorEvaluation:
    """IC 不达标的评估结果。"""
    return FactorEvaluation(
        factor_id="fct_fail_ic", trace_id="l2_t",
        level_1_backtest=BacktestMetrics(
            ic=0.01, icir=0.3, sharpe=2.0, max_drawdown=0.1,
            monotonicity=True, oos_ratio=0.4, t_stat=3.5,
            turnover_monthly=0.3,
        ),
        level_2_economic=EconomicScore(
            theory=4, behavioral=3, microstructure=4, institutional=5,
            dimensions_passed=4, narrative="达标",
        ),
        level_3_multiple=MultipleTestResult(
            bonferroni_p=0.005, fdr_q=0.03, effective_n_factors=8,
            adjusted_t=3.2, passed=True,
        ),
        passed=False, failure_reasons=["Level 1 失败: IC=0.0100 < 0.03"],
        evaluated_at="2026-07-18T00:00:00",
    )


# ─── Verifier 锁定机制 ────────────────────────────────────

def test_verifier_is_locked_after_init():
    v = FactorVerifier()
    assert v.locked is True


def test_verifier_update_config_raises_when_locked():
    v = FactorVerifier()
    with pytest.raises(VerifierAlreadyLockedError):
        v.update_config(VerifierConfig(min_ic=0.5))


def test_verifier_unlock_then_update():
    v = FactorVerifier()
    v.unlock()
    assert v.locked is False
    # 解锁后可以更新
    v.update_config(VerifierConfig(min_ic=0.5))
    assert v.config["min_ic"] == 0.5


def test_verifier_check_raises_when_not_locked():
    v = FactorVerifier()
    v.unlock()
    with pytest.raises(VerifierNotLockedError):
        v.check(FactorEvaluation())


def test_verifier_config_is_copy():
    """config 属性应返回副本，外部修改不影响内部。"""
    v = FactorVerifier()
    cfg = v.config
    cfg["min_ic"] = 999
    # 内部应不变
    assert v.config["min_ic"] == 0.03


# ─── Verifier 判定 ────────────────────────────────────────

def test_verifier_passes_good_evaluation(passing_evaluation):
    v = FactorVerifier()
    result = v.check(passing_evaluation)
    assert result["passed"] is True
    assert result["failure_reasons"] == []


def test_verifier_fails_low_ic(failing_evaluation_low_ic):
    v = FactorVerifier()
    result = v.check(failing_evaluation_low_ic)
    assert result["passed"] is False
    assert any("IC" in r for r in result["failure_reasons"])


def test_verifier_fails_low_sharpe():
    ev = FactorEvaluation(
        factor_id="fct", trace_id="t",
        level_1_backtest=BacktestMetrics(
            ic=0.05, icir=0.8, sharpe=1.0,  # 不达标
            max_drawdown=0.1, monotonicity=True, oos_ratio=0.4,
            t_stat=3.5, turnover_monthly=0.3,
        ),
        level_2_economic=EconomicScore(
            theory=4, behavioral=3, microstructure=4, institutional=5,
            dimensions_passed=4, narrative="达标",
        ),
        level_3_multiple=MultipleTestResult(
            bonferroni_p=0.005, fdr_q=0.03, effective_n_factors=8,
            adjusted_t=3.2, passed=True,
        ),
        passed=False, failure_reasons=[],
        evaluated_at="2026-07-18T00:00:00",
    )
    v = FactorVerifier()
    result = v.check(ev)
    assert result["passed"] is False
    assert any("夏普" in r for r in result["failure_reasons"])


def test_verifier_fails_high_drawdown():
    ev = FactorEvaluation(
        factor_id="fct", trace_id="t",
        level_1_backtest=BacktestMetrics(
            ic=0.05, icir=0.8, sharpe=2.0,
            max_drawdown=0.5,  # 不达标 > 0.20
            monotonicity=True, oos_ratio=0.4, t_stat=3.5, turnover_monthly=0.3,
        ),
        level_2_economic=EconomicScore(
            theory=4, behavioral=3, microstructure=4, institutional=5,
            dimensions_passed=4, narrative="达标",
        ),
        level_3_multiple=MultipleTestResult(
            bonferroni_p=0.005, fdr_q=0.03, effective_n_factors=8,
            adjusted_t=3.2, passed=True,
        ),
        passed=False, failure_reasons=[],
        evaluated_at="2026-07-18T00:00:00",
    )
    v = FactorVerifier()
    result = v.check(ev)
    assert result["passed"] is False
    assert any("回撤" in r for r in result["failure_reasons"])


def test_verifier_fails_low_economic_dimensions():
    ev = FactorEvaluation(
        factor_id="fct", trace_id="t",
        level_1_backtest=BacktestMetrics(
            ic=0.05, icir=0.8, sharpe=2.0, max_drawdown=0.1,
            monotonicity=True, oos_ratio=0.4, t_stat=3.5, turnover_monthly=0.3,
        ),
        level_2_economic=EconomicScore(
            theory=2, behavioral=2, microstructure=2, institutional=2,
            dimensions_passed=0,  # 全部不达标
            narrative="差",
        ),
        level_3_multiple=MultipleTestResult(
            bonferroni_p=0.005, fdr_q=0.03, effective_n_factors=8,
            adjusted_t=3.2, passed=True,
        ),
        passed=False, failure_reasons=[],
        evaluated_at="2026-07-18T00:00:00",
    )
    v = FactorVerifier()
    result = v.check(ev)
    assert result["passed"] is False
    assert any("经济逻辑" in r for r in result["failure_reasons"])


def test_verifier_checked_against_snapshot(passing_evaluation):
    """判定结果必须包含 checked_against 快照。"""
    v = FactorVerifier()
    result = v.check(passing_evaluation)
    assert "checked_against" in result
    assert result["checked_against"]["min_ic"] == 0.03


def test_global_verifier_singleton():
    """全局 Verifier 应为单例。"""
    reset_global_verifier()
    v1 = get_global_verifier()
    v2 = get_global_verifier()
    assert v1 is v2
