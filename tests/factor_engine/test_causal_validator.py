"""
test_causal_validator.py — 因果结构审查测试。

HARNESS §11-logic-review-plan.md §C.1:
    验证因果验证器可正确执行，输出符合预期。
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.causal_validator import (
    CausalValidationResult,
    CausalValidator,
    EventPredictionError,
)
from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.factor_program import create_factor_program
from tests.scenarios.natural_experiments import (
    NaturalExperiment,
)


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """生成 200 天的合成 OHLCV 数据（含 date 列）。"""
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
def forward_returns() -> np.ndarray:
    """生成未来收益率。"""
    np.random.seed(42)
    n = 200
    ret = np.random.randn(n) * 0.01
    ret[-1] = 0.0
    return ret


@pytest.fixture
def simple_momentum_factor() -> FactorProgram:
    """一个简单的动量因子。"""
    code = '''
def factor_program(data, params):
    """Alpha: simple_momentum"""
    import numpy as np
    import pandas as pd
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = params.get("lookback", 5)
    if len(close) < n + 1:
        return np.full(len(close), np.nan)
    signal = np.full(len(close), np.nan)
    signal[n:] = (close[n:] - close[:-n]) / np.maximum(close[:-n], 1e-10)
    return signal
'''
    return create_factor_program(
        name="test_momentum_causal",
        code=code,
        params={"lookback": 5},
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
def mock_events() -> list[NaturalExperiment]:
    """3 个测试用自然实验事件（在数据范围内）。"""
    return [
        NaturalExperiment(
            event_id="test_event_1",
            event_type="circuit_breaker",
            event_date=date(2024, 3, 1),
            symbol="",
            name="测试熔断事件 1",
            expected_direction="negative",
            description="测试用熔断事件",
            pre_window=3,
            post_window=3,
        ),
        NaturalExperiment(
            event_id="test_event_2",
            event_type="policy_shock",
            event_date=date(2024, 6, 1),
            symbol="",
            name="测试政策事件 2",
            expected_direction="positive",
            description="测试用政策事件",
            pre_window=3,
            post_window=3,
        ),
        NaturalExperiment(
            event_id="test_event_3",
            event_type="limit_move",
            event_date=date(2024, 9, 1),
            symbol="",
            name="测试涨跌停事件 3",
            expected_direction="negative",
            description="测试用涨跌停事件",
            pre_window=3,
            post_window=3,
        ),
    ]


# ─── CausalValidator 初始化测试 ────────────────────────────


class TestCausalValidatorInit:
    """测试 CausalValidator 初始化。"""

    def test_default_events(self):
        """默认事件应非空。"""
        validator = CausalValidator()
        assert len(validator._events) > 0

    def test_custom_events(self, mock_events):
        """自定义事件应被正确设置。"""
        validator = CausalValidator(events=mock_events)
        assert len(validator._events) == 3
        assert validator._events[0].event_id == "test_event_1"

    def test_custom_sigma_threshold(self):
        """自定义 sigma 阈值应被正确设置。"""
        validator = CausalValidator(sigma_threshold=2.0)
        assert validator._sigma_threshold == 2.0


# ─── CausalValidator 执行测试 ──────────────────────────────


class TestCausalValidatorValidate:
    """测试 CausalValidator.validate()。"""

    def test_validate_returns_correct_structure(
        self,
        sample_data,
        forward_returns,
        simple_momentum_factor,
        mock_events,
    ):
        """validate() 返回 CausalValidationResult 且包含必要字段。"""
        validator = CausalValidator(events=mock_events)
        result = validator.validate(simple_momentum_factor, sample_data, forward_returns)

        assert isinstance(result, CausalValidationResult)
        assert result["factor_id"] is not None
        assert result["factor_name"] == simple_momentum_factor["name"]
        assert result["n_events"] > 0
        assert isinstance(result["summary"], dict)
        assert "n_events_in_data" in result["summary"]

    def test_validate_events_in_data_range(
        self,
        sample_data,
        forward_returns,
        simple_momentum_factor,
        mock_events,
    ):
        """数据范围内的事件应被正确分析。"""
        validator = CausalValidator(events=mock_events)
        result = validator.validate(simple_momentum_factor, sample_data, forward_returns)

        # 3 个事件中 2 个在 2024 年数据范围内（200 天从 2024-01-01 到 ~2024-07-18）
        assert result["n_events"] == 2
        assert len(result["all_events"]) == 2

    def test_validate_all_events_have_required_fields(
        self,
        sample_data,
        forward_returns,
        simple_momentum_factor,
        mock_events,
    ):
        """每个事件分析结果应包含必要字段。"""
        validator = CausalValidator(events=mock_events)
        result = validator.validate(simple_momentum_factor, sample_data, forward_returns)

        for event in result["all_events"]:
            assert isinstance(event, EventPredictionError)
            assert "event_id" in event
            assert "event_name" in event
            assert "event_type" in event
            assert "is_anomalous" in event
            assert "pre_mean_error" in event
            assert "post_mean_error" in event
            assert "error_change" in event

    def test_validate_summary_contains_expected_fields(
        self,
        sample_data,
        forward_returns,
        simple_momentum_factor,
        mock_events,
    ):
        """汇总应包含所有期望字段。"""
        validator = CausalValidator(events=mock_events)
        result = validator.validate(simple_momentum_factor, sample_data, forward_returns)

        s = result["summary"]
        assert "n_events_in_data" in s
        assert "n_anomalous" in s
        assert "anomaly_rate" in s
        assert "sigma_threshold" in s
        assert "global_error_std" in s
        assert "event_types_covered" in s

    def test_validate_without_date_column_raises(self):
        """缺少 date 列应引发 ValueError。"""
        data = pd.DataFrame({"close": [100, 101, 102]})
        fwd = np.array([0.01, 0.02, 0.03])
        factor = FactorProgram(
            factor_id="test",
            name="test",
            code="",
            params={},
            source="seed",
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "test"},
        )
        validator = CausalValidator(events=[])
        with pytest.raises(ValueError, match="date"):
            validator.validate(factor, data, fwd)

    def test_report_generates_string(
        self,
        sample_data,
        forward_returns,
        simple_momentum_factor,
        mock_events,
    ):
        """report() 应生成字符串。"""
        validator = CausalValidator(events=mock_events)
        result = validator.validate(simple_momentum_factor, sample_data, forward_returns)
        report = CausalValidator.report(result)
        assert isinstance(report, str)
        assert len(report) > 0
        assert "因果结构审查报告" in report


# ─── 事件过滤测试 ──────────────────────────────────────────


class TestCausalValidatorSkipEvents:
    """测试事件过滤（data 范围外的应被跳过）。"""

    def test_skip_out_of_range_events(
        self,
        sample_data,
        forward_returns,
        simple_momentum_factor,
    ):
        """数据范围外的事件应被跳过。"""
        # 创建一个在 2025 年的事件，超出数据范围
        out_of_range_events = [
            NaturalExperiment(
                event_id="future_event",
                event_type="circuit_breaker",
                event_date=date(2025, 6, 1),
                symbol="",
                name="未来事件",
                expected_direction="negative",
                description="超出数据范围的事件",
                pre_window=3,
                post_window=3,
            ),
        ]
        validator = CausalValidator(events=out_of_range_events)
        result = validator.validate(simple_momentum_factor, sample_data, forward_returns)
        assert result["n_events"] == 0
        assert len(result["all_events"]) == 0
