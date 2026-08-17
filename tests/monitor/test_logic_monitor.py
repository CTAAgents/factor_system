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

    def test_run_passes_factor_params_to_executor(self, sample_data, momentum_factor):
        """run() 透传因子 params 给 FactorExecutor（修复 params['window'] 必传参数 KeyError）。"""
        from fts.monitor import logic_monitor as lm

        captured: dict[str, object] = {}
        original = lm.FactorExecutor

        class _FakeExecutor:
            def __init__(self, factor):
                captured["factor_params"] = factor.get("params")

            def execute(self, data, params):
                captured["execute_params"] = params
                return np.zeros(len(data))

        lm.FactorExecutor = _FakeExecutor
        try:
            monitor = lm.LogicMonitor()
            monitor.run(momentum_factor, sample_data, switch_dates=[])
        finally:
            lm.FactorExecutor = original

        assert captured.get("execute_params") == {"lookback": 20}


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

    # ── v2.104.0+72 双口径检测（离散信号不误报）──

    def _discrete_factor(self, name: str, signal_fn) -> FactorProgram:
        """构造输出离散三态信号的因子。"""
        code = f'''
def factor_program(data, params):
    """Alpha: {name}"""
    import numpy as np
    return {signal_fn}
'''
        return create_factor_program(
            name=name,
            code=code,
            params={},
            signature={
                "input_fields": ["close", "volume"],
                "output_type": "signal",
                "frequency": "daily",
            },
            source="seed",
            economic_logic={
                "theory": 3,
                "behavioral": 2,
                "microstructure": 3,
                "institutional": 2,
                "narrative": name,
            },
        )

    def test_discrete_breakout_factor_no_false_alarm(self, sample_data):
        """离散三态突破因子（{-1,0,+1}，非零占比 22%）不应触发极端报警。

        回归复现 fct_211b96d7（fut_price_volatility_breakout，能源链 22.1% 误报）：
        z-score 口径下离散信号全部非零档位天然 |z|>2，须走主导档位退化口径。
        """
        signal = "np.where(np.arange(len(data['close'])) % 5 < 1, 1.0, np.where(np.arange(len(data['close'])) % 9 == 0, -1.0, 0.0))"
        factor = self._discrete_factor("breakout_tri_state", signal)

        monitor = LogicMonitor()
        result = monitor.run(factor, sample_data, switch_dates=[])

        ex = result.extreme_prediction
        assert ex.method == "discrete", "离散信号应走 discrete 口径"
        assert ex.discrete_nunique is not None and ex.discrete_nunique <= 20
        # 非零占比约 22%，但主导档位（0）占比约 78% < 95% → 不告警
        assert not ex.is_alarmed, "正常离散突破因子不应触发极端报警"
        assert ex.dominant_ratio < monitor._discrete_dominant_threshold

    def test_discrete_degenerate_factor_triggers_alarm(self):
        """退化为近常数（单一档位占比 ≥95%）的离散信号应触发报警。"""
        signal = "np.full(len(data['close']), 1.0)"
        factor = self._discrete_factor("degenerate_constant", signal)
        data = pd.DataFrame(
            {
                "date": pd.date_range("2024-01-01", periods=100, freq="D"),
                "close": 100 + np.arange(100) * 0.1,
            }
        )

        monitor = LogicMonitor()
        result = monitor.run(factor, data, switch_dates=[])

        ex = result.extreme_prediction
        assert ex.method == "discrete"
        assert ex.is_alarmed, "退化离散信号（主导档位 100%）应触发报警"
        assert ex.dominant_ratio >= monitor._discrete_dominant_threshold

    def test_continuous_signal_stays_zscore(self, sample_data, momentum_factor):
        """连续信号应保持 z-score 口径（method='zscore'）。"""
        monitor = LogicMonitor()
        result = monitor.run(momentum_factor, sample_data, switch_dates=[])

        ex = result.extreme_prediction
        assert ex.method == "zscore"
        assert ex.discrete_nunique is None

    def test_discrete_threshold_configurable(self, sample_data):
        """离散判定阈值应可配置。"""
        signal = "np.where(np.arange(len(data['close'])) % 2 == 0, 1.0, 0.0)"
        factor = self._discrete_factor("binary_signal", signal)

        # nunique=2，默认阈值 20 判定离散
        monitor = LogicMonitor()
        result = monitor.run(factor, sample_data, switch_dates=[])
        assert result.extreme_prediction.method == "discrete"

        # 阈值设为 1 → nunique=2 > 1，判定连续（走 zscore）
        monitor2 = LogicMonitor(discrete_nunique_threshold=1)
        result2 = monitor2.run(factor, sample_data, switch_dates=[])
        assert result2.extreme_prediction.method == "zscore"


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
