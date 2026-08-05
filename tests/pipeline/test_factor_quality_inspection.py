"""
tests/pipeline/test_factor_quality_inspection.py — 因子质检过滤层测试。

覆盖范围:
    1. FactorQualityInspection 初始化与配置
    2. inspect() 单因子质检流程
    3. batch_inspect() 批量质检
    4. filter_passed() 分离通过/不通过
    5. InspectionResult 数据结构
    6. 便捷函数 inspect_factor()
    7. 分级准入过滤逻辑

版本: v1.0.0
"""

from __future__ import annotations

import pytest

from fts.pipeline.factor_quality_inspection import (
    FactorQualityInspection,
    InspectionResult,
    inspect_factor,
)
from fts.factor_engine.contracts import (
    BacktestMetrics,
    EconomicScore,
    FactorEvaluation,
    FactorProgram,
)


# ══════════════════════════════════════════════════════════
# 测试辅助函数
# ══════════════════════════════════════════════════════════


def make_factor(
    factor_id: str = "fct_test",
    name: str = "test_factor",
    frequency: str = "daily",
) -> FactorProgram:
    """创建测试因子。"""
    return {
        "factor_id": factor_id,
        "name": name,
        "code": "return close - close.shift(1)",
        "params": {},
        "signature": {
            "input_fields": ["close"],
            "output_type": "signal",
            "frequency": frequency,
            "lookback": 1,
        },
        "source": "seed",
        "parent_id": None,
        "generation": 0,
        "created_at": "2026-08-01T00:00:00",
        "trace_id": "test_trace",
    }


def make_evaluation(
    ic: float = 0.05,
    sharpe: float = 2.0,
    turnover: float = 0.3,
    passed: bool = True,
    theory: int = 4,
    behavioral: int = 4,
    microstructure: int = 3,
    institutional: int = 3,
) -> FactorEvaluation:
    """创建测试评估结果。"""
    bt: BacktestMetrics = {
        "ic": ic,
        "icir": ic * 10,  # 简化
        "sharpe": sharpe,
        "max_drawdown": 0.1,
        "monotonicity": True,
        "oos_ratio": 0.4,
        "t_stat": 3.0,
        "turnover_monthly": turnover,
    }
    econ: EconomicScore = {
        "theory": theory,
        "behavioral": behavioral,
        "microstructure": microstructure,
        "institutional": institutional,
        "dimensions_passed": sum(
            1 for v in (theory, behavioral, microstructure, institutional) if v >= 3
        ),
        "narrative": "Test narrative",
    }
    return {
        "factor_id": "fct_test",
        "trace_id": "test_trace",
        "level_1_backtest": bt,
        "level_2_economic": econ,
        "level_3_multiple": {
            "bonferroni_p": 0.01,
            "fdr_q": 0.02,
            "effective_n_factors": 10,
            "adjusted_t": 2.5,
            "passed": True,
        },
        "passed": passed,
        "failure_reasons": [],
        "evaluated_at": "2026-08-01T00:00:00",
    }


# ══════════════════════════════════════════════════════════
# FactorQualityInspection 初始化测试
# ══════════════════════════════════════════════════════════


class TestInspectionInit:
    """初始化测试。"""

    def test_default_init(self) -> None:
        insp = FactorQualityInspection()
        assert insp.min_grade == "B"
        assert insp.card is not None

    def test_custom_min_grade(self) -> None:
        insp = FactorQualityInspection(min_grade="A")
        assert insp.min_grade == "A"

    def test_set_min_grade(self) -> None:
        insp = FactorQualityInspection()
        insp.min_grade = "C"
        assert insp.min_grade == "C"

    def test_custom_card_config(self) -> None:
        config = {"total_max": 30, "grade_A_threshold": 24.0, "grade_B_min": 18.0}
        insp = FactorQualityInspection(card_config=config)
        assert insp.card._config.get("total_max") == 30


# ══════════════════════════════════════════════════════════
# inspect() 单因子质检测试
# ══════════════════════════════════════════════════════════


class TestInspect:
    """单因子质检测试。"""

    def test_good_factor_passes(self) -> None:
        insp = FactorQualityInspection()
        factor = make_factor()
        eval_result = make_evaluation(ic=0.08, sharpe=3.0)
        result = insp.inspect(factor=factor, evaluation=eval_result)

        assert isinstance(result, InspectionResult)
        assert result.factor_id == "fct_test"
        assert result.passed is True
        assert result.filtered is False
        assert result.grade in ("A", "B")
        assert result.total_score > 30

    def test_poor_factor_filtered(self) -> None:
        insp = FactorQualityInspection()
        factor = make_factor()
        eval_result = make_evaluation(
            ic=0.005, sharpe=0.3, passed=False,
            theory=1, behavioral=1, microstructure=1, institutional=1,
        )
        result = insp.inspect(
            factor=factor,
            evaluation=eval_result,
            decay_rate=0.6,
            turnover=0.01,
            correlation_max=0.85,
            cross_symbol_coverage=0.2,
            capacity_estimate=1_000,
        )

        assert result.grade == "C"
        assert result.filtered is True
        assert result.passed is False
        assert "低于准入阈值" in result.reason

    def test_grade_A_factor(self) -> None:
        insp = FactorQualityInspection(min_grade="A")
        factor = make_factor()
        eval_result = make_evaluation(
            ic=0.08, sharpe=3.5,
            theory=5, behavioral=5, microstructure=5, institutional=5,
        )
        result = insp.inspect(
            factor=factor,
            evaluation=eval_result,
            decay_rate=0.05,
            turnover=0.2,
            correlation_max=0.2,
            cross_symbol_coverage=0.95,
            capacity_estimate=150_000_000,
        )
        assert result.grade == "A"
        assert result.passed is True

    def test_grade_B_filtered_with_min_A(self) -> None:
        insp = FactorQualityInspection(min_grade="A")
        factor = make_factor()
        eval_result = make_evaluation(ic=0.03, sharpe=1.5)
        result = insp.inspect(
            factor=factor,
            evaluation=eval_result,
            decay_rate=0.2,
            turnover=0.3,
        )
        # B 级因子被 min_grade=A 过滤
        assert result.filtered is True
        assert result.passed is False

    def test_quality_score_attached(self) -> None:
        insp = FactorQualityInspection()
        factor = make_factor()
        eval_result = make_evaluation(ic=0.05, sharpe=2.0)
        result = insp.inspect(factor=factor, evaluation=eval_result)

        qs = result.quality_score
        assert "total_score" in qs
        assert "dimension_scores" in qs
        assert "grade" in qs
        assert len(qs["dimension_scores"]) == 10

    def test_to_dict(self) -> None:
        insp = FactorQualityInspection()
        factor = make_factor()
        eval_result = make_evaluation(ic=0.05, sharpe=2.0)
        result = insp.inspect(factor=factor, evaluation=eval_result)

        d = result.to_dict()
        assert "factor_id" in d
        assert "passed" in d
        assert "grade" in d
        assert "total_score" in d
        assert "quality_score" in d


# ══════════════════════════════════════════════════════════
# batch_inspect() 批量质检测试
# ══════════════════════════════════════════════════════════


class TestBatchInspect:
    """批量质检测试。"""

    def test_batch_processes_all(self) -> None:
        insp = FactorQualityInspection()
        items = [
            {"factor": make_factor("fct_1"), "evaluation": make_evaluation(ic=0.08, sharpe=3.0)},
            {"factor": make_factor("fct_2"), "evaluation": make_evaluation(ic=0.05, sharpe=2.0)},
            {"factor": make_factor("fct_3"), "evaluation": make_evaluation(ic=0.005, sharpe=0.3)},
        ]
        results = insp.batch_inspect(items)
        assert len(results) == 3
        assert all(isinstance(r, InspectionResult) for r in results)

    def test_empty_batch(self) -> None:
        insp = FactorQualityInspection()
        results = insp.batch_inspect([])
        assert results == []


# ══════════════════════════════════════════════════════════
# filter_passed() 分离测试
# ══════════════════════════════════════════════════════════


class TestFilterPassed:
    """分离通过/不通过测试。"""

    def test_separates_correctly(self) -> None:
        insp = FactorQualityInspection()
        items = [
            {"factor": make_factor("fct_good"), "evaluation": make_evaluation(ic=0.08, sharpe=3.0)},
            {"factor": make_factor("fct_bad"), "evaluation": make_evaluation(ic=0.005, sharpe=0.3, passed=False)},
        ]
        passed, failed = insp.filter_passed(items)
        assert len(passed) >= 1
        assert len(failed) >= 1
        assert all(r.passed for r in passed)
        assert all(not r.passed for r in failed)

    def test_all_pass(self) -> None:
        insp = FactorQualityInspection()
        items = [
            {
                "factor": make_factor("fct_1"),
                "evaluation": make_evaluation(
                    ic=0.08, sharpe=3.0,
                    theory=5, behavioral=5, microstructure=5, institutional=5,
                ),
                "decay_rate": 0.05,
                "turnover": 0.3,
                "correlation_max": 0.2,
                "cross_symbol_coverage": 0.95,
                "capacity_estimate": 150_000_000,
            },
            {
                "factor": make_factor("fct_2"),
                "evaluation": make_evaluation(
                    ic=0.06, sharpe=2.5,
                    theory=5, behavioral=5, microstructure=5, institutional=5,
                ),
                "decay_rate": 0.05,
                "turnover": 0.3,
                "correlation_max": 0.2,
                "cross_symbol_coverage": 0.95,
                "capacity_estimate": 150_000_000,
            },
        ]
        passed, failed = insp.filter_passed(items)
        assert len(passed) == 2
        assert len(failed) == 0

    def test_all_fail(self) -> None:
        insp = FactorQualityInspection()
        items = [
            {"factor": make_factor("fct_1"), "evaluation": make_evaluation(ic=0.005, sharpe=0.3, passed=False)},
            {"factor": make_factor("fct_2"), "evaluation": make_evaluation(ic=0.003, sharpe=0.2, passed=False)},
        ]
        passed, failed = insp.filter_passed(items)
        assert len(passed) == 0
        assert len(failed) == 2


# ══════════════════════════════════════════════════════════
# 便捷函数测试
# ══════════════════════════════════════════════════════════


class TestInspectFactor:
    """便捷函数测试。"""

    def test_inspect_factor_passes(self) -> None:
        factor = make_factor()
        eval_result = make_evaluation(ic=0.05, sharpe=2.0)
        result = inspect_factor(factor=factor, evaluation=eval_result)
        assert isinstance(result, InspectionResult)
        assert result.factor_id == "fct_test"

    def test_inspect_factor_with_custom_min_grade(self) -> None:
        factor = make_factor()
        eval_result = make_evaluation(ic=0.03, sharpe=1.5)
        result = inspect_factor(
            factor=factor, evaluation=eval_result, min_grade="A",
        )
        assert result.passed is False


# ══════════════════════════════════════════════════════════
# 分级准入过滤逻辑测试
# ══════════════════════════════════════════════════════════


class TestGradeFiltering:
    """分级准入过滤逻辑测试。"""

    def test_min_grade_A_allows_A(self) -> None:
        insp = FactorQualityInspection(min_grade="A")
        factor = make_factor()
        eval_result = make_evaluation(
            ic=0.08, sharpe=3.5,
            theory=5, behavioral=5, microstructure=5, institutional=5,
        )
        result = insp.inspect(
            factor=factor, evaluation=eval_result,
            decay_rate=0.05, turnover=0.2,
            correlation_max=0.2,
            cross_symbol_coverage=0.95, capacity_estimate=150_000_000,
        )
        assert result.grade == "A"
        assert result.passed is True

    def test_min_grade_A_blocks_B(self) -> None:
        insp = FactorQualityInspection(min_grade="A")
        factor = make_factor()
        eval_result = make_evaluation(ic=0.04, sharpe=1.8)
        result = insp.inspect(factor=factor, evaluation=eval_result)
        assert result.filtered is True

    def test_min_grade_B_allows_A_and_B(self) -> None:
        insp = FactorQualityInspection(min_grade="B")
        factor = make_factor()
        # A 级
        eval_a = make_evaluation(
            ic=0.08, sharpe=3.5,
            theory=5, behavioral=5, microstructure=5, institutional=5,
        )
        result_a = insp.inspect(
            factor=factor, evaluation=eval_a,
            decay_rate=0.05, turnover=0.2,
            correlation_max=0.2,
            cross_symbol_coverage=0.95, capacity_estimate=150_000_000,
        )
        assert result_a.passed is True

        # 良好因子 (应至少 B 级)
        eval_b = make_evaluation(
            ic=0.05, sharpe=2.0,
            theory=4, behavioral=4, microstructure=4, institutional=4,
        )
        result_b = insp.inspect(
            factor=factor, evaluation=eval_b,
            decay_rate=0.1, turnover=0.3,
            correlation_max=0.3,
            cross_symbol_coverage=0.8, capacity_estimate=50_000_000,
        )
        assert result_b.passed is True

    def test_min_grade_B_blocks_C(self) -> None:
        insp = FactorQualityInspection(min_grade="B")
        factor = make_factor()
        eval_result = make_evaluation(ic=0.005, sharpe=0.3, passed=False)
        result = insp.inspect(
            factor=factor, evaluation=eval_result,
            decay_rate=0.6, turnover=0.01,
            correlation_max=0.85, cross_symbol_coverage=0.2,
            capacity_estimate=1_000,
        )
        assert result.filtered is True
        assert result.passed is False