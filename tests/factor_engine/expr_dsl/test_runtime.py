"""tests/factor_engine/expr_dsl/test_runtime.py — FTS-Expr 沙箱 runtime 测试。

覆盖:
    1. eval_fts_expr 基础字段表达式
    2. 算子表达式 / 返回类型（float64 ndarray）
    3. 异常传播
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[3]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.expr_dsl.executor import DSLExecutionError  # noqa: E402
from fts.factor_engine.expr_dsl.runtime import eval_fts_expr  # noqa: E402


@pytest.fixture
def data() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=80, freq="D")
    close = pd.Series(np.arange(80, dtype=float) + 10.0, index=idx)
    volume = pd.Series(np.full(80, 1000.0), index=idx)
    return pd.DataFrame({"close": close, "volume": volume})


class TestEvalFtsExpr:
    def test_field_expression(self, data):
        out = eval_fts_expr("close", data, {})
        assert isinstance(out, np.ndarray)
        assert out.dtype == np.float64
        assert out[0] == 10.0
        assert out[-1] == 89.0

    def test_operator_expression(self, data):
        out = eval_fts_expr("ts_mean(close, 20)", data, {})
        assert out[19] == pytest.approx(19.5)
        assert np.isnan(out[0])  # 窗口不足

    def test_nested_expression(self, data):
        out = eval_fts_expr("mul(ts_mean(volume, 20), 2.0)", data, {})
        assert out[19] == pytest.approx(2000.0)

    def test_params_ignored(self, data):
        out = eval_fts_expr("close", data, {"window": 10})
        assert len(out) == 80

    def test_unknown_operator_raises(self, data):
        with pytest.raises(DSLExecutionError):
            eval_fts_expr("foo(close)", data, {})

    def test_unknown_field_raises(self, data):
        with pytest.raises(DSLExecutionError):
            eval_fts_expr("nonexistent_field", data, {})
