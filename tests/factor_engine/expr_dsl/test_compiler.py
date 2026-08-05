"""FTS-Expr 编译器与表达式分析测试。"""
import pytest

from fts.factor_engine.expr_dsl.compiler import (
    ExprAnalysis, analyze_expression, compile_expr_to_code,
)
from fts.factor_engine.expr_dsl.validator import DSLValidationError
from fts.factor_engine.factor_program import validate_factor_code


def test_compile_expr_to_code_valid():
    code = compile_expr_to_code("rank(ts_zscore(close, 60))", "op_factor")
    assert "eval_fts_expr" in code
    assert "rank(ts_zscore(close, 60))" in code
    ok, reasons = validate_factor_code(code)
    assert ok, reasons


def test_compile_expr_to_code_invalid_raises():
    with pytest.raises(DSLValidationError):
        compile_expr_to_code("foo(close)", "bad")


def test_analyze_expression_metadata():
    analysis = analyze_expression("add(ts_mean(close, 20), ts_std(close, 60))")
    assert isinstance(analysis, ExprAnalysis)
    assert analysis.max_lookback == 60
    assert analysis.operator_count == 3  # add + ts_mean + ts_std
    assert analysis.depth == 2


def test_analyze_expression_invalid():
    with pytest.raises(DSLValidationError):
        analyze_expression("ts_mean(close, 999)")
