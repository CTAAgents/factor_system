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


def test_compiled_code_executes_via_backtest_pipeline():
    """回归: 编译产物经 _execute_factor_code 执行不得抛 eval_fts_expr 未定义。

    此前 exec 的模块级 import（from ...runtime import eval_fts_expr）绑定落在
    local_vars，而 factor_program.__globals__ 指向 globals dict，函数调用时
    NameError，_execute_factor_code 降级返回全零 → operator 因子在运行时校验/
    预筛阶段被全数拦截（GP/operator 通道瘫痪）。
    """
    import numpy as np
    import pandas as pd

    from fts.factor_engine.backtest_pipeline import BacktestPipeline

    code = compile_expr_to_code("sub(ts_mean(close, 20), close)", "op_factor")
    n = 120
    rng = np.random.default_rng(7)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.4, n))
    data = pd.DataFrame(
        {
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": np.full(n, 1e6),
        }
    )
    signal = BacktestPipeline._execute_factor_code(code, data, {})
    assert isinstance(signal, np.ndarray)
    assert len(signal) == n
    assert len(np.unique(signal)) > 10  # 非常数信号，非 NameError 降级全零


def test_analyze_expression_metadata():
    analysis = analyze_expression("add(ts_mean(close, 20), ts_std(close, 60))")
    assert isinstance(analysis, ExprAnalysis)
    assert analysis.max_lookback == 60
    assert analysis.operator_count == 3  # add + ts_mean + ts_std
    assert analysis.depth == 2


def test_analyze_expression_invalid():
    with pytest.raises(DSLValidationError):
        analyze_expression("ts_mean(close, 999)")
