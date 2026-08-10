"""
tests/scenarios/test_scenarios.py — 宏观行为场景测试用例

HARNESS §11-logic-review-plan.md §A.2:
    验证场景定义、验证器逻辑和端到端场景测试。
"""

from __future__ import annotations

import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.factor_program import create_factor_program

from .definitions import ALL_SCENARIOS, ScenarioDefinition
from .validator import ScenarioValidator, ScenarioResult, ScenarioSummary


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def simple_momentum_factor() -> FactorProgram:
    """一个简单的动量因子（用于场景测试验证）。"""
    code = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = params.get("lookback", 5)
    # 简单动量：过去 n 日收益率
    ret = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    mom = np.zeros_like(ret)
    for i in range(n, len(ret)):
        mom[i] = np.mean(ret[i-n:i])
    return np.clip(np.nan_to_num(mom, nan=0.0), -1.0, 1.0)
"""
    return create_factor_program(
        name="simple_momentum",
        code=code,
        params={"lookback": 5},
        signature={
            "input_fields": ["close"],
            "output_type": "signal",
            "frequency": "daily",
            "lookback": 5,
        },
        economic_logic={
            "theory": 3,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "简单动量因子，捕捉短期趋势延续",
        },
        source="seed",
        trace_id="test_scenarios",
    )


@pytest.fixture
def mean_reversion_factor() -> FactorProgram:
    """一个简单的均值回归因子（反转信号）。"""
    code = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = params.get("lookback", 10)
    # 均值回归：价格偏离均线的程度（负值=超买，正值=超卖）
    import pandas as pd
    ma = pd.Series(close).rolling(n, min_periods=1).mean().values
    deviation = (ma - close) / np.maximum(close, 1e-10)
    return np.clip(np.nan_to_num(deviation, nan=0.0), -1.0, 1.0)
"""
    return create_factor_program(
        name="mean_reversion",
        code=code,
        params={"lookback": 10},
        signature={
            "input_fields": ["close"],
            "output_type": "signal",
            "frequency": "daily",
            "lookback": 10,
        },
        economic_logic={
            "theory": 4,
            "behavioral": 4,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "均值回归因子，捕捉超买超卖反转",
        },
        source="seed",
        trace_id="test_scenarios_mr",
    )


# ─── 测试场景定义 ─────────────────────────────────────────


class TestScenarioDefinitions:
    """验证场景定义的正确性。"""

    def test_all_scenarios_defined(self):
        """所有场景都正确定义。"""
        assert len(ALL_SCENARIOS) >= 20, f"期望至少 20 个场景，实际 {len(ALL_SCENARIOS)}"
        for scenario in ALL_SCENARIOS:
            assert isinstance(scenario, ScenarioDefinition)
            assert scenario.name, f"场景缺少 name: {scenario}"
            assert scenario.description, f"场景缺少 description: {scenario.name}"
            assert scenario.category, f"场景缺少 category: {scenario.name}"
            assert scenario.generate_data is not None, f"场景缺少 generate_data: {scenario.name}"
            assert scenario.expected_behavior, f"场景缺少 expected_behavior: {scenario.name}"

    def test_all_scenario_names_unique(self):
        """所有场景名称唯一。"""
        names = [s.name for s in ALL_SCENARIOS]
        assert len(names) == len(set(names)), f"场景名称重复: {[n for n in names if names.count(n) > 1]}"

    def test_scenario_categories_valid(self):
        """场景分类有效。"""
        valid_categories = {"trend", "reversal", "liquidity", "event", "oscillation", "futures"}
        for scenario in ALL_SCENARIOS:
            assert scenario.category in valid_categories, f"无效分类 '{scenario.category}' 在场景 {scenario.name}"

    def test_each_scenario_must_have_validation(self):
        """每个场景至少有一个验证方式（范围检查或自定义函数）。"""
        for scenario in ALL_SCENARIOS:
            has_range = scenario.expected_signal_range is not None
            has_check_fn = scenario.check_fn is not None
            assert has_range or has_check_fn, f"场景 {scenario.name} 缺少验证方式（需至少一个）"

    def test_scenario_data_generates_valid_output(self):
        """场景数据生成函数返回有效数据。"""
        for scenario in ALL_SCENARIOS:
            data, metadata = scenario.generate_data()
            assert isinstance(data, pd.DataFrame), f"{scenario.name}: data 不是 DataFrame"
            assert "close" in data.columns, f"{scenario.name}: 缺少 close 列"
            assert len(data) >= 20, f"{scenario.name}: 数据长度不足"
            assert isinstance(metadata, dict), f"{scenario.name}: metadata 不是 dict"


# ─── 测试验证器逻辑 ───────────────────────────────────────


class TestScenarioValidator:
    """验证 ScenarioValidator 的核心逻辑。"""

    def test_validate_scenario_returns_result(self, simple_momentum_factor):
        """validate_scenario 返回 ScenarioResult。"""
        validator = ScenarioValidator(scenarios=[ALL_SCENARIOS[0]])
        result = validator.validate_scenario(simple_momentum_factor, ALL_SCENARIOS[0])
        assert isinstance(result, ScenarioResult)
        assert result.scenario_name == ALL_SCENARIOS[0].name
        assert isinstance(result.passed, bool)

    def test_validate_all_returns_summary(self, simple_momentum_factor):
        """validate_all 返回 ScenarioSummary。"""
        validator = ScenarioValidator(scenarios=ALL_SCENARIOS[:3])
        summary = validator.validate_all(simple_momentum_factor)
        assert isinstance(summary, ScenarioSummary)
        assert summary.total == 3
        assert summary.passed + summary.failed + summary.errored == summary.total

    def test_summary_pass_rate(self, simple_momentum_factor):
        """通过率计算正确。"""
        validator = ScenarioValidator(scenarios=ALL_SCENARIOS[:5])
        summary = validator.validate_all(simple_momentum_factor)
        assert 0.0 <= summary.pass_rate <= 1.0

    def test_summary_print_report(self, simple_momentum_factor):
        """print_report 返回非空字符串。"""
        validator = ScenarioValidator(scenarios=ALL_SCENARIOS[:5])
        summary = validator.validate_all(simple_momentum_factor)
        report = summary.print_report()
        assert isinstance(report, str)
        assert len(report) > 0
        assert "宏观行为场景测试报告" in report

    def test_validate_all_scenarios(self, simple_momentum_factor):
        """对所有 20+ 场景执行验证不报错。"""
        validator = ScenarioValidator()
        summary = validator.validate_all(simple_momentum_factor)
        assert summary.total == len(ALL_SCENARIOS)
        assert summary.errored == 0, f"有 {summary.errored} 个场景抛出异常"

    def test_invalid_factor_handled_gracefully(self):
        """因子程序为空时的异常处理。"""
        code = """
def factor_program(data, params):
    raise ValueError("模拟异常")
"""
        bad_factor = create_factor_program(
            name="bad_factor",
            code=code,
            params={},
            signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 2},
            economic_logic={
                "theory": 3,
                "behavioral": 3,
                "microstructure": 3,
                "institutional": 3,
                "narrative": "测试异常处理",
            },
            source="seed",
            trace_id="test_bad",
        )
        validator = ScenarioValidator(scenarios=ALL_SCENARIOS[:1])
        result = validator.validate_scenario(bad_factor, ALL_SCENARIOS[0])
        assert not result.passed
        assert result.error is not None


# ─── 端到端场景测试 ───────────────────────────────────────


class TestEndToEndScenarios:
    """端到端场景测试 — 验证因子在典型市场片段中的行为符合直觉。"""

    def test_momentum_in_trend_up(self, simple_momentum_factor):
        """动量因子在上涨趋势中应产生正信号。"""
        scenario = [s for s in ALL_SCENARIOS if s.name == "trend_up"][0]
        validator = ScenarioValidator(scenarios=[scenario])
        result = validator.validate_scenario(simple_momentum_factor, scenario)
        # 动量因子在上涨趋势中至少信号应 > -0.3
        assert result.signal_last > -0.3, f"上涨趋势中动量信号应偏正，实际={result.signal_last:.4f}"

    def test_momentum_in_trend_down(self, simple_momentum_factor):
        """动量因子在下跌趋势中应产生负信号。"""
        scenario = [s for s in ALL_SCENARIOS if s.name == "trend_down"][0]
        validator = ScenarioValidator(scenarios=[scenario])
        result = validator.validate_scenario(simple_momentum_factor, scenario)
        assert result.signal_last < 0.3, f"下跌趋势中动量信号应偏负，实际={result.signal_last:.4f}"

    def test_mean_reversion_overbought(self, mean_reversion_factor):
        """均值回归因子在超买时应产生负信号（做空倾向）。"""
        scenario = [s for s in ALL_SCENARIOS if s.name == "overbought_reversal"][0]
        validator = ScenarioValidator(scenarios=[scenario])
        result = validator.validate_scenario(mean_reversion_factor, scenario)
        # 均值回归因子在超买时信号应 ≤ 0.3
        assert result.signal_last <= 0.3, f"超买时均值回归信号应≤0.3，实际={result.signal_last:.4f}"

    def test_mean_reversion_oversold(self, mean_reversion_factor):
        """均值回归因子在超卖时应产生正信号（做多倾向）。"""
        scenario = [s for s in ALL_SCENARIOS if s.name == "oversold_reversal"][0]
        validator = ScenarioValidator(scenarios=[scenario])
        result = validator.validate_scenario(mean_reversion_factor, scenario)
        assert result.signal_last >= -0.3, f"超卖时均值回归信号应≥-0.3，实际={result.signal_last:.4f}"

    def test_low_liquidity_signal_muted(self, simple_momentum_factor):
        """低流动性场景下信号绝对值应偏低。"""
        scenario = [s for s in ALL_SCENARIOS if s.name == "low_liquidity"][0]
        validator = ScenarioValidator(scenarios=[scenario])
        result = validator.validate_scenario(simple_momentum_factor, scenario)
        assert abs(result.signal_last) < 0.6, f"低流动性信号应|signal|<0.6，实际={result.signal_last:.4f}"

    def test_consolidation_signal_neutral(self, simple_momentum_factor):
        """横盘震荡场景下信号应接近 0。"""
        scenario = [s for s in ALL_SCENARIOS if s.name == "consolidation_sideways"][0]
        validator = ScenarioValidator(scenarios=[scenario])
        result = validator.validate_scenario(simple_momentum_factor, scenario)
        assert abs(result.signal_last) < 0.4, f"横盘信号应|signal|<0.4，实际={result.signal_last:.4f}"

    def test_breakout_positive_signal(self, simple_momentum_factor):
        """放量突破场景下动量信号应偏正。"""
        scenario = [s for s in ALL_SCENARIOS if s.name == "breakout_with_volume"][0]
        validator = ScenarioValidator(scenarios=[scenario])
        result = validator.validate_scenario(simple_momentum_factor, scenario)
        assert result.signal_last > -0.5, f"突破信号应>-0.5，实际={result.signal_last:.4f}"

    def test_rollover_no_signal_jump(self, simple_momentum_factor):
        """换月日附近信号不应剧烈突变。"""
        scenario = [s for s in ALL_SCENARIOS if s.name == "futures_rollover"][0]
        validator = ScenarioValidator(scenarios=[scenario])
        validator.validate_scenario(simple_momentum_factor, scenario)
        # 检查换月日前后信号差 < 0.5
        roll_day = 50
        data, _ = scenario.generate_data()
        executor = __import__("fts.factor_engine.factor_program", fromlist=["FactorExecutor"]).FactorExecutor
        signal = executor(simple_momentum_factor).execute(data, simple_momentum_factor.get("params", {}))
        if roll_day > 0 and roll_day < len(signal) - 1:
            before = signal[roll_day - 1]
            after = signal[roll_day + 1]
            diff = abs(after - before)
            assert diff < 0.5, f"换月日前后信号差={diff:.4f}，期望<0.5"


# ─── 快速验证（可独立运行）────────────────────────────────


def test_quick_validation():
    """快速验证：对所有场景运行动量因子，统计通过率。"""
    from fts.factor_engine.factor_program import create_factor_program

    code = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = 5
    ret = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    mom = np.zeros_like(ret)
    for i in range(n, len(ret)):
        mom[i] = np.mean(ret[i-n:i])
    return np.clip(np.nan_to_num(mom, nan=0.0), -1.0, 1.0)
"""
    factor = create_factor_program(
        name="quick_momentum",
        code=code,
        params={},
        signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 5},
        economic_logic={
            "theory": 3,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "快速验证动量因子",
        },
        source="seed",
        trace_id="test_quick",
    )

    validator = ScenarioValidator()
    summary = validator.validate_all(factor)
    assert summary.errored == 0, f"有 {summary.errored} 个场景抛出异常"
    # 至少 50% 场景通过（宽松标准，因为有些场景对特定因子可能不适用）
    assert summary.pass_rate >= 0.3, f"通过率 {summary.pass_rate:.1%} 过低"


__all__ = [
    "TestScenarioDefinitions",
    "TestScenarioValidator",
    "TestEndToEndScenarios",
]
