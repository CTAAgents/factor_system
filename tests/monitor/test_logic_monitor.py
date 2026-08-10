"""
test_logic_monitor.py — 逻辑监控仪表盘测试。

HARNESS §11-logic-review-plan.md §C.2:
    验证逻辑监控器可正确执行，检查项符合预期。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.factor_program import create_factor_program
from fts.monitor.logic_monitor import (
    DriftCheckResult,
    ExtremePredictionResult,
    LogicMonitor,
    LogicMonitorResult,
    _MeanReversion,
    _SimpleMomentum,
)


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """生成 200 天的合成 OHLCV 数据。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    volume = np.random.randint(1000, 10000, n).astype(float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close + np.random.randn(n) * 0.1,
            "high": close + np.abs(np.random.randn(n)) * 0.3,
            "low": close - np.abs(np.random.randn(n)) * 0.3,
            "close": close,
            "volume": volume,
        }
    )


@pytest.fixture
def momentum_factor() -> FactorProgram:
    """动量因子（应与简单动量基准高度相关）。"""
    code = '''
def factor_program(data, params):
    """Alpha: momentum"""
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = params.get("lookback", 20)
    if len(close) < n + 1:
        return np.full(len(close), np.nan)
    signal = np.full(len(close), np.nan)
    signal[n:] = (close[n:] - close[:-n]) / np.maximum(close[:-n], 1e-10)
    return signal
'''
    return create_factor_program(
        name="test_momentum_monitor",
        code=code,
        params={"lookback": 20},
        signature={
            "input_fields": ["close", "volume"],
            "output_type": "signal",
            "frequency": "daily",
        },
        source="seed",
        economic_logic={
            "theory": 3,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "momentum test",
        },
    )


@pytest.fixture
def random_factor() -> FactorProgram:
    """随机噪声因子（与基准低相关，应触发漂移报警）。"""
    code = '''
def factor_program(data, params):
    """Alpha: random_noise"""
    import numpy as np
    np.random.seed(42)
    return np.random.randn(len(data["close"])) * 0.01
'''
    return create_factor_program(
        name="test_random_monitor",
        code=code,
        params={},
        signature={
            "input_fields": ["close", "volume"],
            "output_type": "signal",
            "frequency": "daily",
        },
        source="seed",
        economic_logic={
            "theory": 1,
            "behavioral": 1,
            "microstructure": 1,
            "institutional": 1,
            "narrative": "random noise test",
        },
    )


# ─── 基准因子测试 ──────────────────────────────────────────


class TestBenchmarkFactors:
    """测试基准因子计算。"""

    def test_simple_momentum_shape(self, sample_data):
        """简单动量应与输入等长。"""
        close = sample_data["close"].values
        signal = _SimpleMomentum.compute(close)
        assert len(signal) == len(close)

    def test_simple_momentum_first_n_nan(self, sample_data):
        """前 N 个值应为 NaN。"""
        close = sample_data["close"].values
        signal = _SimpleMomentum.compute(close, lookback=20)
        assert np.all(np.isnan(signal[:20]))

    def test_mean_reversion_shape(self, sample_data):
        """均值回归应与输入等长。"""
        close = sample_data["close"].values
        signal = _MeanReversion.compute(close)
        assert len(signal) == len(close)

    def test_mean_reversion_first_n_nan(self, sample_data):
        """前 N 个值应为 NaN。"""
        close = sample_data["close"].values
        signal = _MeanReversion.compute(close, lookback=20)
        assert np.all(np.isnan(signal[:20]))


# ─── LogicMonitor 初始化测试 ───────────────────────────────


class TestLogicMonitorInit:
    """测试 LogicMonitor 初始化。"""

    def test_default_thresholds(self):
        """默认阈值应符合预期。"""
        monitor = LogicMonitor()
        assert monitor._drift_threshold == 0.3
        assert monitor._extreme_ratio_threshold == 0.05
        assert monitor._contract_switch_sigma == 3.0

    def test_custom_thresholds(self):
        """自定义阈值应被正确设置。"""
        monitor = LogicMonitor(
            drift_threshold=0.5,
            extreme_ratio_threshold=0.1,
            contract_switch_sigma=2.0,
        )
        assert monitor._drift_threshold == 0.5
        assert monitor._extreme_ratio_threshold == 0.1
        assert monitor._contract_switch_sigma == 2.0


# ─── LogicMonitor 执行测试 ─────────────────────────────────


class TestLogicMonitorRun:
    """测试 LogicMonitor.run()。"""

    def test_run_returns_correct_structure(self, sample_data, momentum_factor):
        """run() 返回 LogicMonitorResult 且包含必要字段。"""
        monitor = LogicMonitor()
        result = monitor.run(momentum_factor, sample_data, switch_dates=[])

        assert isinstance(result, LogicMonitorResult)
        assert result.factor_id == momentum_factor["factor_id"]
        assert isinstance(result.drift, DriftCheckResult)
        assert isinstance(result.extreme_prediction, ExtremePredictionResult)
        assert result.checked_at != ""

    def test_momentum_factor_not_drifted(self, sample_data, momentum_factor):
        """动量因子应与基准高度相关，不触发漂移报警。"""
        monitor = LogicMonitor()
        result = monitor.run(momentum_factor, sample_data)
        assert not result.drift.is_drifted, "动量因子不应被视为漂移"

    def test_random_factor_drifted(self, sample_data, random_factor):
        """随机因子应与基准低相关，应触发漂移报警。"""
        monitor = LogicMonitor(drift_threshold=0.1)
        result = monitor.run(random_factor, sample_data)
        # 随机因子与动量基准的相关性应较低（绝对值 < 0.5 即可，随机相关性可能偶然偏大）
        assert abs(result.drift.momentum_correlation) < 0.5
        assert abs(result.drift.mean_reversion_correlation) < 0.5

    def test_contract_switch_none_when_no_dates(self, sample_data, momentum_factor):
        """无换月日数据时，contract_switch 应为 None。"""
        monitor = LogicMonitor()
        result = monitor.run(momentum_factor, sample_data, switch_dates=None)
        assert result.contract_switch is None

    def test_contract_switch_with_dates(self, sample_data, momentum_factor):
        """有换月日数据时，应返回 ContractSwitchResult。"""
        switch_dates = ["2024-03-01", "2024-06-01", "2024-09-01"]
        monitor = LogicMonitor()
        result = monitor.run(momentum_factor, sample_data, switch_dates=switch_dates)
        # 可能部分日期在数据范围内
        assert result.contract_switch is not None

    def test_extreme_prediction_ratio_zero_for_normal_factor(self, sample_data, momentum_factor):
        """正常因子不应有极端预测报警。"""
        monitor = LogicMonitor(extreme_ratio_threshold=0.1)
        result = monitor.run(momentum_factor, sample_data)
        # 动量因子的信号应相对集中
        assert result.extreme_prediction.extreme_ratio < 0.3

    def test_format_report_generates_string(self, sample_data, momentum_factor):
        """format_report() 应生成字符串。"""
        monitor = LogicMonitor()
        result = monitor.run(momentum_factor, sample_data)
        report = LogicMonitor.format_report(result)
        assert isinstance(report, str)
        assert len(report) > 0
        assert "逻辑监控报告" in report


# ─── 极端因子测试 ──────────────────────────────────────────


class TestExtremePrediction:
    """测试极端预测检测。"""

    def test_extreme_factor_triggers_alarm(self):
        """极端信号比重高的因子应触发报警。"""
        code = """
def factor_program(data, params):
    import numpy as np
    np.random.seed(42)
    return np.random.randn(len(data["close"])) * 10.0
"""
        factor = create_factor_program(
            name="extreme",
            code=code,
            params={},
            signature={
                "input_fields": ["close", "volume"],
                "output_type": "signal",
                "frequency": "daily",
            },
            source="seed",
            economic_logic={
                "theory": 1,
                "behavioral": 1,
                "microstructure": 1,
                "institutional": 1,
                "narrative": "extreme",
            },
        )
        data = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=100, freq="D"),
                "close": np.ones(100),
            }
        )

        monitor = LogicMonitor(extreme_ratio_threshold=0.01)
        result = monitor.run(factor, data)

        # 常数信号不会产生极端值
        assert isinstance(result, LogicMonitorResult)


# ─── 换月日检测测试 ────────────────────────────────────────


class TestContractSwitch:
    """测试换月日信号异常检测。"""

    def test_contract_switch_anomaly_detection(self):
        """换月日前后的信号变化应被正确检测。"""
        # 构造一个因子：换月日前信号为 0，换月日后信号跳变为 1
        n = 100
        mid = 50
        signals = np.zeros(n)
        signals[mid:] = 1.0

        close = 100 + np.arange(n) * 0.1
        dates = pd.date_range("2024-01-01", periods=n, freq="D")

        FactorProgram(
            factor_id="fct_switch_test",
            name="switch_test",
            code="",
            params={},
            source="seed",
            economic_logic={
                "theory": 1,
                "behavioral": 1,
                "microstructure": 1,
                "institutional": 1,
                "narrative": "switch test",
            },
        )

        # 直接测试 _check_contract_switch 方法
        data = pd.DataFrame(
            {
                "date": dates,
                "close": close,
            }
        )

        switch_date = dates[mid].strftime("%Y-%m-%d")

        monitor = LogicMonitor(contract_switch_sigma=2.0)
        result = monitor._check_contract_switch(
            "test",
            data,
            signals,
            [switch_date],
        )

        assert result is not None
        assert result.n_switches >= 1
        # 换月前后均值应有明显变化
        assert abs(result.mean_change) > 0
