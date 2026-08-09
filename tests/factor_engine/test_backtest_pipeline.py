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


# ─── v2.59.0 (GAP-F02) 回测真实性仿真：涨跌停拦截 + 停牌过滤 ────────


def _make_block_data(n: int = 80) -> pd.DataFrame:
    """构造含涨停/跌停/停牌日的线性行情（无自然波动，避免涨跌停连锁触发）。

    - index=10: 涨停日（close 相对前日 +8.5%，≥ limit_pct=0.08）
    - index=11: 恢复日（回到线性趋势值，-7.8% 不触发跌停）
    - index=n-1: 跌停日（close 相对前日 -8.5%，置于末位避免恢复连锁）
    - index=30: 停牌日（volume=0）

    n ≥ 60 以满足流水线最小行数校验。
    """
    base = np.linspace(3000.0, 3100.0, n)
    close = base.copy()
    close[10] = close[9] * 1.085
    close[11] = base[11]
    close[n - 1] = base[n - 1] * 0.915
    volume = np.full(n, 1e5)
    volume[30] = 0.0
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.005,
            "low": close * 0.995,
            "close": close,
            "volume": volume,
        },
        index=dates,
    )


class TestGapF02TradeFilter:
    """GAP-F02: 可交易掩码（涨跌停拦截 + 停牌过滤）。"""

    def test_build_tradeable_mask_detects_blocks(self):
        """涨停/跌停/停牌日应被拦截，普通日可交易。"""
        data = _make_block_data()
        input_data = BacktestInput(
            factor=_make_factor("output = close", "test_mask"),
            data=data,
            trade_filter=True,
            limit_pct=0.08,
        )
        mask, stats = BacktestPipeline._build_tradeable_mask(input_data)
        assert mask is not None
        assert stats["limit_up"] == 1, f"limit_up 应为 1: {stats}"
        assert stats["limit_down"] == 1, f"limit_down 应为 1: {stats}"
        assert stats["halt"] == 1, f"halt 应为 1: {stats}"
        assert not mask[10], "涨停日应被拦截"
        assert not mask[data.index.size - 1], "跌停日应被拦截"
        assert not mask[30], "停牌日应被拦截"
        assert mask[5], "普通日应可交易"

    def test_build_tradeable_mask_disabled(self):
        """trade_filter=False 时应返回 (None, 全 0)，保持回归兼容。"""
        input_data = BacktestInput(
            factor=_make_factor("output = close", "test_no_mask"),
            data=_make_block_data(),
            trade_filter=False,
        )
        mask, stats = BacktestPipeline._build_tradeable_mask(input_data)
        assert mask is None
        assert stats == {"limit_up": 0, "limit_down": 0, "halt": 0}

    def test_strategy_returns_keep_position_on_blocked_day(self):
        """被拦截日持仓应保持上一交易日，且不产生换手成本。"""
        n = 30
        rng = np.random.default_rng(3)
        factor_values = rng.normal(size=n)
        forward_returns = rng.normal(0, 0.01, size=n)
        dates = pd.date_range("2024-01-01", periods=n, freq="B")
        mask = np.ones(n, dtype=bool)
        mask[15] = False  # 第 15 日被拦截

        returns, positions, blocked = BacktestPipeline._compute_strategy_returns(
            factor_values, forward_returns,
            cost_rate=0.001, slippage=0.0005,
            zscore_window=10, dates=dates, tradeable_mask=mask,
        )
        assert positions[15] == pytest.approx(positions[14]), "拦截日持仓应保持"
        assert blocked["halt"] == 1, "拦截日应计入 blocked 统计"
        turnover = np.abs(np.diff(positions, prepend=0))
        assert turnover[15] == pytest.approx(0.0), "拦截日不应产生换手成本"

    def test_report_contains_blocked_trades(self):
        """端到端回测报告 config 应包含 blocked_trades 统计。"""
        data = _make_block_data()
        factor = _make_factor(
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = data['close']\n"
            "    n = len(close)\n"
            "    ret = np.zeros(n)\n"
            "    if n > 5:\n"
            "        ret[5:] = (close[5:] - close[:-5]) / np.maximum(close[:-5], 1e-10)\n"
            "    return np.tanh(ret * 10)\n",
            "test_blocked_report",
        )
        result = BacktestPipeline().run(
            BacktestInput(factor=factor, data=data, trade_filter=True, limit_pct=0.08)
        )
        assert result.success, f"回测失败: {result.error}"
        assert result.output is not None
        blocked = result.output.summary["config"].get("blocked_trades", {})
        assert blocked.get("limit_up", 0) >= 1, f"报告应含 limit_up 拦截: {blocked}"
        assert blocked.get("halt", 0) >= 1, f"报告应含 halt 拦截: {blocked}"
