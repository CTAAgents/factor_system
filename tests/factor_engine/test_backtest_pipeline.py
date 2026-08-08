"""tests/factor_engine/test_backtest_pipeline.py — 端到端回测流水线测试。

覆盖回归场景:
1. 标准约定 `def factor_program(data, params):` + DatetimeIndex 数据（无 date 列）
   —— 此前导致「因子代码未设置 output 变量」+「因子计算失败: 'date'」。
2. 传统 output 变量约定保持兼容。
3. 显式 date 列数据保持兼容。
"""

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.backtest_pipeline import BacktestInput, BacktestPipeline


def _make_ohlcv(n: int = 200, seed: int = 42) -> pd.DataFrame:
    """构造 OHLCV DataFrame，日期在 DatetimeIndex 上（期货面板风格）。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    close = 3000 + np.cumsum(rng.normal(0, 10, n))
    open_ = close + rng.normal(0, 2, n)
    high = np.maximum(open_, close) + rng.uniform(0, 3, n)
    low = np.minimum(open_, close) - rng.uniform(0, 3, n)
    volume = rng.uniform(1e4, 1e6, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


def _make_factor(code: str, factor_id: str = "test_fct") -> dict:
    return {
        "factor_id": factor_id,
        "name": factor_id,
        "code": code,
        "params": {},
    }


def test_pipeline_with_canonical_factor_program_and_datetime_index():
    """标准 factor_program 约定 + DatetimeIndex 数据（无 date 列）应回测成功。"""
    data = _make_ohlcv()
    factor = _make_factor(
        "def factor_program(data, params):\n"
        "    import numpy as np\n"
        "    close = data['close']\n"
        "    n = len(close)\n"
        "    ret = np.zeros(n)\n"
        "    if n > 5:\n"
        "        ret[5:] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)\n"
        "    return np.tanh(ret * 10)\n",
        "test_canonical",
    )
    result = BacktestPipeline().run(BacktestInput(factor=factor, data=data))
    assert result.success, f"回测失败: {result.error}"
    assert result.output is not None
    assert result.output.metrics.ic_mean != 0.0


def test_pipeline_with_output_var_convention():
    """传统 output 变量约定应保持兼容。"""
    data = _make_ohlcv()
    factor = _make_factor(
        "output = close / np.maximum(open, 1e-10) - 1.0",
        "test_legacy",
    )
    result = BacktestPipeline().run(BacktestInput(factor=factor, data=data))
    assert result.success, f"回测失败: {result.error}"


def test_pipeline_with_explicit_date_column():
    """显式 date 列数据应保持兼容。"""
    data = _make_ohlcv().reset_index().rename(columns={"index": "date"})
    factor = _make_factor(
        "def factor_program(data, params):\n"
        "    import numpy as np\n"
        "    close = data['close']\n"
        "    return close / np.maximum(np.roll(close, 1), 1e-10) - 1.0\n",
        "test_date_col",
    )
    result = BacktestPipeline().run(BacktestInput(factor=factor, data=data))
    assert result.success, f"回测失败: {result.error}"


def test_pipeline_invalid_code_returns_failure():
    """无效因子代码应返回失败而非抛出。"""
    data = _make_ohlcv()
    factor = _make_factor("raise RuntimeError('boom')", "test_bad")
    result = BacktestPipeline().run(BacktestInput(factor=factor, data=data))
    assert result.failed
    assert result.error is not None


def test_pipeline_missing_ohlcv_columns_fails():
    """缺少必要列应报 DataLoad 错误。"""
    data = pd.DataFrame({"close": np.arange(100.0)})
    factor = _make_factor("output = close", "test_missing_cols")
    result = BacktestPipeline().run(BacktestInput(factor=factor, data=data))
    assert result.failed
    assert "缺少必要列" in (result.error or "")


def test_performance_metrics_include_payoff_and_profit_factor():
    """回测结果的绩效指标应包含盈亏比和盈亏因子。"""
    data = _make_ohlcv()
    factor = _make_factor(
        "def factor_program(data, params):\n"
        "    import numpy as np\n"
        "    close = data['close']\n"
        "    n = len(close)\n"
        "    ret = np.zeros(n)\n"
        "    if n > 5:\n"
        "        ret[5:] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)\n"
        "    return np.tanh(ret * 10)\n",
        "test_payoff",
    )
    result = BacktestPipeline().run(BacktestInput(factor=factor, data=data))
    assert result.success, f"回测失败: {result.error}"
    assert result.output is not None
    m = result.output.metrics
    assert hasattr(m, "payoff_ratio"), "缺少 payoff_ratio 字段"
    assert hasattr(m, "profit_factor"), "缺少 profit_factor 字段"
    assert m.payoff_ratio >= 0.0, f"盈亏比不应为负: {m.payoff_ratio}"
    assert m.profit_factor >= 0.0, f"盈亏因子不应为负: {m.profit_factor}"
