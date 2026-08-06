"""
tests/factor_engine/test_evaluation_parallel.py — 横截面评估测试

覆盖:
    - _cs_execute_factors: 多标的执行
    - cross_section_evaluate_backtest: 横截面回测评估
    - 正确性: 结果结构完整性
"""

from __future__ import annotations

import logging
import time

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.evaluation_chain import (
    _cs_execute_factors,
    cross_section_evaluate_backtest,
)
from fts.factor_engine.factor_program import create_factor_program


def _make_simple_factor() -> FactorProgram:
    """创建一个简单的测试因子 (SMA 均线)。"""
    code = '''
def factor_program(data, params):
    import numpy as np
    close = data['close'].values
    window = params.get('window', 5)
    result = np.zeros(len(close))
    for i in range(window, len(close)):
        result[i] = np.mean(close[i-window:i])
    return result
'''
    return create_factor_program(
        name="test_sma",
        code=code,
        params={"window": 5},
        signature={
            "input_fields": ["close"],
            "output_type": "signal",
            "frequency": "daily",
            "lookback": 5,
        },
        economic_logic={
            "theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3,
            "narrative": "简单均线策略，用于并行化测试",
        },
        source="test",
    )


def _make_panel_data(n_symbols: int = 10, n_days: int = 200) -> dict[str, pd.DataFrame]:
    """创建模拟面板数据。"""
    panel: dict[str, pd.DataFrame] = {}
    base_date = pd.Timestamp("2023-01-01")
    rng = np.random.RandomState(42)
    for i in range(n_symbols):
        dates = pd.date_range(base_date, periods=n_days, freq="B")
        close = 100 + np.cumsum(rng.randn(n_days) * 0.5)
        df = pd.DataFrame({
            "open": close + rng.randn(n_days) * 0.2,
            "high": close + abs(rng.randn(n_days) * 1.0),
            "low": close - abs(rng.randn(n_days) * 1.0),
            "close": close,
            "volume": rng.randint(100000, 1000000, n_days),
            "settle": close,
            "open_interest": rng.randint(50000, 200000, n_days),
        }, index=dates)
        panel[f"SYM{i:03d}"] = df
    return panel


# ─── _cs_execute_factors 测试 ────────────────────────────


class TestCsExecuteFactors:
    """测试 _cs_execute_factors 多标的执行。"""

    def test_returns_correct_shapes(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(1, 50)

        sig, ret = _cs_execute_factors(executor, {"window": 5}, panel)
        assert "SYM000" in sig
        assert "SYM000" in ret
        assert len(sig["SYM000"]) == len(panel["SYM000"])
        assert len(ret["SYM000"]) == len(panel["SYM000"])

    def test_failed_factor_returns_empty(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(1, 50)
        panel["BAD"] = pd.DataFrame({"wrong_column": [1, 2, 3]})

        sig, ret = _cs_execute_factors(executor, {"window": 5}, panel)
        assert "BAD" not in sig
        assert "BAD" not in ret

    def test_handles_multiple_symbols(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(15, 200)

        sig, ret = _cs_execute_factors(executor, {"window": 5}, panel)
        assert len(sig) == 15
        assert len(ret) == 15

    def test_consistent_results(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(10, 200)

        sig1, ret1 = _cs_execute_factors(executor, {"window": 5}, panel)
        sig2, ret2 = _cs_execute_factors(executor, {"window": 5}, panel)

        assert set(sig1.keys()) == set(sig2.keys())
        for sym in sig1:
            pd.testing.assert_series_equal(
                sig1[sym], sig2[sym], check_names=False,
            )
            pd.testing.assert_series_equal(
                ret1[sym], ret2[sym], check_names=False,
            )


# ─── cross_section_evaluate_backtest 集成测试 ────────────


class TestCrossSectionIntegration:
    """测试 cross_section_evaluate_backtest 集成。"""

    def test_returns_valid_metrics(self):
        factor = _make_simple_factor()
        panel = _make_panel_data(12, 200)
        common_dates = panel["SYM000"].index

        result = cross_section_evaluate_backtest(
            factor, panel, common_dates,
        )
        assert isinstance(result, dict)
        assert "ic" in result
        assert "sharpe" in result
        assert "icir" in result

    def test_auto_mode_default(self):
        factor = _make_simple_factor()
        panel = _make_panel_data(15, 200)
        common_dates = panel["SYM000"].index

        result = cross_section_evaluate_backtest(
            factor, panel, common_dates,
        )
        assert isinstance(result, dict)
        assert "ic" in result
        assert "sharpe" in result
        assert "icir" in result

    def test_forward_compatibility(self):
        factor = _make_simple_factor()
        panel = _make_panel_data(10, 200)
        common_dates = panel["SYM000"].index

        result = cross_section_evaluate_backtest(
            factor, panel, common_dates,
        )
        assert isinstance(result, dict)
        assert "ic" in result
        assert "sharpe" in result


# ─── 基准测试 ─────────────────────────────────────────────


class TestBenchmark:
    """基准测试。"""

    def test_basic_parallel_performance(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(50, 500)

        start = time.perf_counter()
        _cs_execute_factors(executor, {"window": 5}, panel)
        elapsed = time.perf_counter() - start

        logger = logging.getLogger(__name__)
        logger.info("基准测试: 执行时间=%.3fs, 标的数=%d", elapsed, len(panel))
        assert elapsed >= 0  # 确保执行完成