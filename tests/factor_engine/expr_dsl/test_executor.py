"""FTS-Expr 解释执行器测试。"""
import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.expr_dsl.executor import DSLExecutionError, evaluate
from fts.factor_engine.expr_dsl.parser import parse_expression
from fts.factor_engine.expr_dsl.registry import build_registry

REG = build_registry()


@pytest.fixture
def data() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=80, freq="D")
    close = pd.Series(np.arange(80, dtype=float) + 10.0, index=idx)
    volume = pd.Series(np.full(80, 1000.0), index=idx)
    return pd.DataFrame({"close": close, "volume": volume})


def test_evaluate_field(data):
    out = evaluate(parse_expression("close"), data, REG)
    assert out.iloc[0] == 10.0


def test_evaluate_ts_mean(data):
    out = evaluate(parse_expression("ts_mean(close, 20)"), data, REG)
    assert out.iloc[19] == 19.5  # mean(10..29) = 19.5


def test_evaluate_nested_rank_ts_zscore(data):
    out = evaluate(parse_expression("rank(ts_zscore(close, 60))"), data, REG)
    assert isinstance(out, pd.Series) and len(out) == 80
    assert out.iloc[:59].isna().all()  # 前 59 位 rolling 不足窗口


def test_evaluate_mul_const(data):
    out = evaluate(parse_expression("mul(ts_mean(volume, 20), 2.0)"), data, REG)
    assert out.iloc[19] == 2000.0


def test_evaluate_where_conditional(data):
    expr = "where(gt(momentum(close, 20), 0), ts_rank(volume, 10), neg(ts_rank(volume, 10)))"
    out = evaluate(parse_expression(expr), data, REG)
    assert isinstance(out, pd.Series) and len(out) == 80


def test_evaluate_div_zero_safe(data):
    flat = pd.DataFrame({"close": pd.Series([1.0, 2.0, 3.0, 4.0])})
    out = evaluate(parse_expression("div(close, ts_delta(close, 1))"), flat, REG)
    assert np.isnan(out.iloc[0]) or not np.isinf(out.iloc[0])


def test_evaluate_unknown_operator(data):
    with pytest.raises(DSLExecutionError):
        evaluate(parse_expression("foo(close)"), data, REG)


def test_evaluate_unknown_field(data):
    with pytest.raises(DSLExecutionError):
        evaluate(parse_expression("close_extra"), data, REG)
