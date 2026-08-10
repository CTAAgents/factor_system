"""FactorExecutor 按 kind 分派 + 快速路径/沙箱路径 parity 测试。"""

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorKind
from fts.factor_engine.expr_dsl.factory import create_operator_factor
from fts.factor_engine.factor_program import FactorExecutor, create_factor_program


@pytest.fixture
def data() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=120, freq="D")
    close = pd.Series(100.0 + np.cumsum(np.random.default_rng(42).normal(0, 1, 120)), index=idx)
    volume = pd.Series(np.random.default_rng(7).normal(1000, 100, 120), index=idx)
    return pd.DataFrame({"close": close, "volume": volume})


def test_operator_kind_routes_to_fast_path(data):
    factor = create_operator_factor(
        "rank(ts_zscore(close, 60))",
        name="op_dispatch",
        market="futures",
        family="mean_reversion",
        narrative="测试",
    )
    executor = FactorExecutor(factor)
    out = executor.execute(data, {})
    assert isinstance(out, np.ndarray)
    assert len(out) == len(data)
    assert np.isfinite(out).all()


def test_code_kind_still_uses_sandbox(data):
    factor = create_factor_program(
        name="code_twin",
        code="import numpy as np\ndef factor_program(data, params):\n    return np.array(data['close'], dtype=float)",
        params={},
        signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 1},
        economic_logic={
            "theory": 3,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "代码因子沙箱路径",
        },
        source="manual",
        market="futures",
        family="trend",
    )
    executor = FactorExecutor(factor)
    out = executor.execute(data, {})
    # 沙箱路径输出经 nan_to_num + clip(-10,10) 后处理；close ~100 会被截为 10.0
    assert np.allclose(out, np.clip(data["close"].values, -10.0, 10.0), atol=1e-8)


def test_operator_and_code_paths_agree(data):
    """parity: 算子快速路径 == 生成的沙箱代码路径。"""
    expr = "rank(ts_zscore(close, 60))"
    op_factor = create_operator_factor(
        expr,
        name="op_parity",
        market="futures",
        family="mean_reversion",
        narrative="测试",
    )
    fast = FactorExecutor(op_factor).execute(data, {})
    # 强制走沙箱路径: 复制为 CODE 类型, 代码由编译器生成
    code_factor = dict(op_factor)
    code_factor["kind"] = FactorKind.CODE
    slow = FactorExecutor(code_factor).execute(data, {})
    assert np.allclose(fast, slow, equal_nan=True)
