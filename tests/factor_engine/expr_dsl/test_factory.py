"""算子因子工厂测试。"""
import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorKind
from fts.factor_engine.expr_dsl.factory import create_operator_factor
from fts.factor_engine.factor_program import FactorExecutor, validate_factor_code


def test_create_operator_factor_fields():
    factor = create_operator_factor(
        "rank(ts_zscore(close, 60))",
        name="op_rank_zscore",
        market="futures", family="mean_reversion", narrative="滚动Z-score截面排名",
    )
    assert factor["kind"] == FactorKind.OPERATOR
    assert factor["expression"] == "rank(ts_zscore(close, 60))"
    assert factor["max_lookback"] == 60
    assert factor["operator_count"] == 2
    assert factor["operator_depth"] == 2
    ok, reasons = validate_factor_code(factor["code"])
    assert ok, reasons


def test_create_operator_factor_invalid_expression():
    with pytest.raises(Exception):
        create_operator_factor("foo(close)", name="bad",
                               market="futures", family="trend", narrative="bad")
