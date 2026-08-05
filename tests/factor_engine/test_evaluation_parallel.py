"""
tests/factor_engine/test_evaluation_parallel.py — Phase 1 并行化测试

覆盖:
    - _resolve_workers: 线程数解析逻辑
    - _cs_execute_single: 单标的执行线程安全
    - _cs_execute_factors: 串行/并行两种路径
    - cross_section_evaluate_backtest: max_workers 参数传递
    - 正确性: 并行结果与串行一致
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
    _cs_execute_single,
    _resolve_workers,
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


# ─── _resolve_workers 测试 ───────────────────────────────


class TestResolveWorkers:
    """测试 _resolve_workers 线程数解析。"""

    def test_explicit_workers(self):
        assert _resolve_workers(4, 100) == 4
        assert _resolve_workers(1, 100) == 1
        assert _resolve_workers(0, 100) == 0

    def test_small_dataset_serial(self):
        assert _resolve_workers(None, 7) == 1
        assert _resolve_workers(None, 1) == 1

    def test_large_dataset_parallel(self):
        workers = _resolve_workers(None, 50)
        assert workers >= 2

    def test_auto_workers_respects_cpu(self):
        import os
        cpu = os.cpu_count() or 4
        workers = _resolve_workers(None, 100)
        assert workers <= cpu
        assert workers >= 2


# ─── _cs_execute_single 测试 ─────────────────────────────


class TestCsExecuteSingle:
    """测试 _cs_execute_single 单标的执行。"""

    def test_returns_correct_shapes(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(1, 50)
        sym, sig, ret = _cs_execute_single(executor, {"window": 5}, "SYM000", panel["SYM000"])
        assert sym == "SYM000"
        assert sig is not None
        assert ret is not None
        assert len(sig) == len(panel["SYM000"])
        assert len(ret) == len(panel["SYM000"])

    def test_failed_factor_returns_none(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        bad_df = pd.DataFrame({"wrong_column": [1, 2, 3]})
        sym, sig, ret = _cs_execute_single(executor, {"window": 5}, "BAD", bad_df)
        assert sym == "BAD"
        assert sig is None
        assert ret is None


# ─── _cs_execute_factors 并行/串行一致性测试 ────────────


class TestCsExecuteFactorsParallel:
    """测试并行执行路径。"""

    def test_parallel_produces_same_results_as_serial(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(15, 200)

        sig_serial, ret_serial = _cs_execute_factors(
            executor, {"window": 5}, panel, max_workers=1,
        )

        sig_parallel, ret_parallel = _cs_execute_factors(
            executor, {"window": 5}, panel, max_workers=2,
        )

        assert set(sig_serial.keys()) == set(sig_parallel.keys())
        assert set(ret_serial.keys()) == set(ret_parallel.keys())

        for sym in sig_serial:
            pd.testing.assert_series_equal(
                sig_serial[sym], sig_parallel[sym], check_names=False,
            )
            pd.testing.assert_series_equal(
                ret_serial[sym], ret_parallel[sym], check_names=False,
            )

    def test_auto_parallel_for_large_dataset(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(50, 200)

        sig, ret = _cs_execute_factors(executor, {"window": 5}, panel, max_workers=None)
        assert len(sig) == 50

    def test_serial_for_small_dataset(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(3, 50)

        sig, ret = _cs_execute_factors(executor, {"window": 5}, panel)
        assert len(sig) == 3

    def test_parallel_handles_failures_gracefully(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(5, 50)
        panel["BAD"] = pd.DataFrame({"wrong": [1, 2, 3]})

        sig, ret = _cs_execute_factors(executor, {"window": 5}, panel, max_workers=2)
        assert len(sig) == 5
        assert "BAD" not in sig


# ─── cross_section_evaluate_backtest 集成测试 ────────────


class TestCrossSectionParallelIntegration:
    """测试 cross_section_evaluate_backtest 的并行集成。"""

    def test_max_workers_parameter_passed(self):
        factor = _make_simple_factor()
        panel = _make_panel_data(12, 200)
        common_dates = panel["SYM000"].index

        result_parallel = cross_section_evaluate_backtest(
            factor, panel, common_dates, max_workers=2,
        )

        result_serial = cross_section_evaluate_backtest(
            factor, panel, common_dates, max_workers=1,
        )

        assert abs(result_parallel["ic"] - result_serial["ic"]) < 1e-10
        assert abs(result_parallel["icir"] - result_serial["icir"]) < 1e-10
        assert abs(result_parallel["sharpe"] - result_serial["sharpe"]) < 1e-10

    def test_auto_mode_default(self):
        factor = _make_simple_factor()
        panel = _make_panel_data(15, 200)
        common_dates = panel["SYM000"].index

        result = cross_section_evaluate_backtest(
            factor, panel, common_dates,
        )
        assert result["ic"] != 0.0 or result["icir"] != 0.0

    def test_forward_compatibility_no_max_workers(self):
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
    """基准测试：对比串行 vs 并行性能。"""

    def test_parallel_is_faster_than_serial(self):
        from fts.factor_engine.factor_program import FactorExecutor
        factor = _make_simple_factor()
        executor = FactorExecutor(factor)
        panel = _make_panel_data(50, 500)

        _cs_execute_factors(executor, {"window": 5}, panel, max_workers=1)

        start = time.perf_counter()
        _cs_execute_factors(executor, {"window": 5}, panel, max_workers=1)
        serial_time = time.perf_counter() - start

        start = time.perf_counter()
        _cs_execute_factors(executor, {"window": 5}, panel, max_workers=4)
        parallel_time = time.perf_counter() - start

        speedup = serial_time / max(parallel_time, 1e-6)
        logger = logging.getLogger(__name__)
        logger.info("基准测试: 串行=%.3fs, 并行=%.3fs, 加速比=%.2fx",
                     serial_time, parallel_time, speedup)

        assert speedup > 1.0, (
            f"并行 ({parallel_time:.3f}s) 未比串行 ({serial_time:.3f}s) 快"
        )
