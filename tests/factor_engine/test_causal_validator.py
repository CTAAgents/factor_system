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


# ─── 边缘路径补充（GAP-F16）──────────────────────────────


class TestCausalValidatorEdgePaths:
    """补充 validate/report 未覆盖的降级与异常分支。"""

    @staticmethod
    def _make_edge_factor():
        """构造一个简单动量因子（供自建数据场景使用）。"""
        code = '''
def factor_program(data, params):
    """Alpha: edge_momentum"""
    import numpy as np
    close = data['close'].values
    n = params.get("lookback", 2)
    if len(close) < n + 1:
        return np.full(len(close), np.nan)
    signal = np.full(len(close), np.nan)
    signal[n:] = (close[n:] - close[:-n]) / np.maximum(close[:-n], 1e-10)
    return signal
'''
        return create_factor_program(
            name="edge_momentum",
            code=code,
            params={"lookback": 2},
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
                "narrative": "edge momentum test",
            },
        )

    def test_import_default_events_failure(self, monkeypatch):
        """_import_default_events 导入失败应返回空列表（line 105）。"""
        import importlib

        from fts.factor_engine.causal_validator import CausalValidator, _import_default_events

        monkeypatch.setattr(importlib, "import_module", lambda name: (_ for _ in ()).throw(ImportError("no module")))
        assert _import_default_events() == []
        # 默认事件导入失败 → 初始化后 _events 为空，validate 无事件
        validator = CausalValidator()
        assert validator._events == []

    def test_datetime_index_without_date_column(self, simple_momentum_factor):
        """数据为 DatetimeIndex 且无 date 列时自动补 date 列（lines 163-164）。"""
        from datetime import datetime

        np.random.seed(7)
        n = 60
        idx = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)
        data = pd.DataFrame(
            {
                "open": close + np.random.randn(n) * 0.1,
                "high": close + np.abs(np.random.randn(n)) * 0.3,
                "low": close - np.abs(np.random.randn(n)) * 0.3,
                "close": close,
                "volume": np.random.randint(1000, 10000, n).astype(float),
            },
            index=idx,
        )
        fwd = np.random.randn(n) * 0.01
        event = NaturalExperiment(
            event_id="idx_event",
            event_type="circuit_breaker",
            event_date=datetime(2024, 2, 15),  # datetime 对象 → line 196 分支
            symbol="",
            name="datetime 事件",
            expected_direction="unknown",
            description="测试 datetime 事件日期",
            pre_window=2,
            post_window=2,
        )
        validator = CausalValidator(events=[event], sigma_threshold=99.0)
        result = validator.validate(simple_momentum_factor, data, fwd)
        # 事件日期在数据范围内 → 事件被分析（datetime 分支不跳过）
        assert result["n_events"] == 1
        assert result["all_events"][0]["event_id"] == "idx_event"

    def test_signal_length_mismatch_fills_nan(self, simple_momentum_factor, sample_data, forward_returns):
        """FactorExecutor 返回长度不匹配时信号降级为全 NaN（line 171）。"""
        from unittest.mock import patch

        from fts.factor_engine.causal_validator import CausalValidator

        validator = CausalValidator(events=[], sigma_threshold=3.0)
        with patch(
            "fts.factor_engine.factor_program.FactorExecutor.execute",
            return_value=np.array([0.1, 0.2, 0.3]),
        ):
            result = validator.validate(simple_momentum_factor, sample_data, forward_returns)
        assert result["n_events"] == 0

    def test_anomalous_positive_direction_matches(self):
        """异常事件且方向匹配 → anomaly_direction 保持 positive（lines 224-225）。"""
        np.random.seed(3)
        n = 10
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        data = pd.DataFrame({"close": 100 + np.arange(n, dtype=float)}, index=dates)
        data["date"] = dates
        data["open"] = data["close"]
        data["high"] = data["close"] + 0.1
        data["low"] = data["close"] - 0.1
        data["volume"] = np.ones(n) * 1000
        # 事件日 index=5；构造 fwd 使事件后误差绝对值大于事件前 → change > 0
        fwd = np.array([0.1, 0.1, 0.1, 0.1, 0.01, 0.1, 0.5, 0.1, 0.1, 0.1])
        event = NaturalExperiment(
            event_id="pos_match",
            event_type="policy_shock",
            event_date=date(2024, 1, 6),
            symbol="",
            name="正向匹配事件",
            expected_direction="positive",
            description="测试正向匹配",
            pre_window=1,
            post_window=1,
        )
        validator = CausalValidator(events=[event], sigma_threshold=0.0)
        result = validator.validate(self._make_edge_factor(), data, fwd)
        assert result["n_events"] == 1
        assert result["n_anomalous"] == 1
        ev = result["anomalous_events"][0]
        assert bool(ev["is_anomalous"]) is True
        assert ev["anomaly_direction"] == "positive"

    def test_anomalous_unexpected_direction(self):
        """异常事件但方向不匹配 → unexpected_positive（lines 226-229）。"""
        np.random.seed(3)
        n = 10
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        data = pd.DataFrame({"close": 100 + np.arange(n, dtype=float)}, index=dates)
        data["date"] = dates
        data["open"] = data["close"]
        data["high"] = data["close"] + 0.1
        data["low"] = data["close"] - 0.1
        data["volume"] = np.ones(n) * 1000
        fwd = np.array([0.1, 0.1, 0.1, 0.1, 0.01, 0.1, 0.5, 0.1, 0.1, 0.1])
        event = NaturalExperiment(
            event_id="neg_mismatch",
            event_type="policy_shock",
            event_date=date(2024, 1, 6),
            symbol="",
            name="方向不匹配事件",
            expected_direction="negative",  # 期望下跌，实际 change > 0
            description="测试方向不匹配",
            pre_window=1,
            post_window=1,
        )
        validator = CausalValidator(events=[event], sigma_threshold=0.0)
        result = validator.validate(self._make_edge_factor(), data, fwd)
        assert result["n_anomalous"] == 1
        ev = result["anomalous_events"][0]
        assert ev["anomaly_direction"] == "unexpected_positive"

    def test_report_with_anomalous_events(self):
        """report() 含异常事件详情 + ⚠️ 标记分支（lines 302-315, 326）。"""
        np.random.seed(3)
        n = 10
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        data = pd.DataFrame({"close": 100 + np.arange(n, dtype=float)}, index=dates)
        data["date"] = dates
        data["open"] = data["close"]
        data["high"] = data["close"] + 0.1
        data["low"] = data["close"] - 0.1
        data["volume"] = np.ones(n) * 1000
        fwd = np.array([0.1, 0.1, 0.1, 0.1, 0.01, 0.1, 0.5, 0.1, 0.1, 0.1])
        event = NaturalExperiment(
            event_id="report_event",
            event_type="circuit_breaker",
            event_date=date(2024, 1, 6),
            symbol="",
            name="报告事件",
            expected_direction="unknown",
            description="测试报告",
            pre_window=1,
            post_window=1,
        )
        validator = CausalValidator(events=[event], sigma_threshold=0.0)
        result = validator.validate(self._make_edge_factor(), data, fwd)
        report = CausalValidator.report(result)
        assert "异常事件详情" in report
        assert "⚠️" in report
        assert "所有事件" in report

    def test_report_all_events_consistent_direction(self):
        """consistent 异常（方向一致）在报告中标记 ✅。"""
        np.random.seed(3)
        n = 10
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        data = pd.DataFrame({"close": 100 + np.arange(n, dtype=float)}, index=dates)
        data["date"] = dates
        data["open"] = data["close"]
        data["high"] = data["close"] + 0.1
        data["low"] = data["close"] - 0.1
        data["volume"] = np.ones(n) * 1000
        fwd = np.array([0.1, 0.1, 0.1, 0.1, 0.01, 0.1, 0.5, 0.1, 0.1, 0.1])
        event = NaturalExperiment(
            event_id="consist_event",
            event_type="policy_shock",
            event_date=date(2024, 1, 6),
            symbol="",
            name="一致事件",
            expected_direction="positive",
            description="测试一致",
            pre_window=1,
            post_window=1,
        )
        validator = CausalValidator(events=[event], sigma_threshold=0.0)
        result = validator.validate(self._make_edge_factor(), data, fwd)
        assert result["summary"]["n_anomalous_consistent"] == 1
        report = CausalValidator.report(result)
        assert "✅" in report
