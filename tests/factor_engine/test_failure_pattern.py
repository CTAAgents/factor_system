"""
tests/factor_engine/test_failure_pattern.py — 失败模式聚类测试

测试 FailurePatternAnalyzer 的分类和格式化逻辑。

版本: v1.9.0
"""
# pylint: disable=redefined-outer-name,unused-argument,protected-access

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from fts.factor_engine.experience_chain import (
    ExperienceChain,
    FailurePatternAnalyzer,
    FAILURE_PATTERN_KEYWORDS,
    DEFAULT_PATTERN,
)
from fts.factor_engine.contracts import ExperienceTrace, FactorEvaluation


# ─── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def temp_chain(tmp_path: Path) -> ExperienceChain:
    """创建临时经验链。"""
    mem = tmp_path / "evolution"
    return ExperienceChain(memory_dir=str(mem))


@pytest.fixture
def analyzer(temp_chain: ExperienceChain) -> FailurePatternAnalyzer:
    """创建分析器。"""
    return FailurePatternAnalyzer(temp_chain)


def _create_failure_trace(
    factor_id: str, reasons: list[str], recorded_at: str = "2024-01-01T00:00:00"
) -> ExperienceTrace:
    """创建失败轨迹。"""
    return ExperienceTrace(
        trace_id=f"trace_{factor_id}",
        factor_id=factor_id,
        parent_id=None,
        generation=1,
        mutation_type="macro_logic",
        mutation_summary="test mutation",
        evaluation=FactorEvaluation(
            factor_id=factor_id,
            trace_id=f"trace_{factor_id}",
            passed=False,
            failure_reasons=reasons,
            evaluated_at=recorded_at,
        ),
        success=False,
        lessons=["test lesson"],
        recorded_at=recorded_at,
    )


# ─── 分类测试 ──────────────────────────────────────────────

class TestClassifyReason:
    """失败原因分类测试。"""

    def test_ic_pattern(self, analyzer: FailurePatternAnalyzer):
        """含 IC 的原因应分类为「IC 过低」。"""
        assert analyzer._classify_reason("IC=0.01 过低") == "IC 过低"
        assert analyzer._classify_reason("截面 IC 不达标") == "IC 过低"

    def test_sharpe_pattern(self, analyzer: FailurePatternAnalyzer):
        """含 sharpe 的原因应分类为「夏普比率不达标」。"""
        assert analyzer._classify_reason("sharpe 比率 0.5 不达标") == "夏普比率不达标"
        assert analyzer._classify_reason("夏普比率过低") == "夏普比率不达标"

    def test_drawdown_pattern(self, analyzer: FailurePatternAnalyzer):
        """含回撤的原因应分类为「最大回撤过大」。"""
        assert analyzer._classify_reason("max_drawdown 超过 50%") == "最大回撤过大"
        assert analyzer._classify_reason("回撤过大") == "最大回撤过大"

    def test_monotonic_pattern(self, analyzer: FailurePatternAnalyzer):
        """含 monotonic 的原因应分类为「十分位单调性不通过」。"""
        assert analyzer._classify_reason("monotonicity 失败") == "十分位单调性不通过"
        assert analyzer._classify_reason("单调性不满足") == "十分位单调性不通过"

    def test_oos_pattern(self, analyzer: FailurePatternAnalyzer):
        """含 oos 的原因应分类为「样本外比例不足」。"""
        assert analyzer._classify_reason("oos 比例 0.15") == "样本外比例不足"
        assert analyzer._classify_reason("样本外比例不足") == "样本外比例不足"

    def test_turnover_pattern(self, analyzer: FailurePatternAnalyzer):
        """含 turnover 的原因应分类为「换手率过高」。"""
        assert analyzer._classify_reason("turnover 过高 0.9") == "换手率过高"
        assert analyzer._classify_reason("换手率超标") == "换手率过高"

    def test_zero_signal_pattern(self, analyzer: FailurePatternAnalyzer):
        """含 zero 的原因应分类为「信号零方差/退化」。"""
        assert analyzer._classify_reason("信号 zero variance") == "信号零方差/退化"
        assert analyzer._classify_reason("零方差信号") == "信号零方差/退化"

    def test_nan_pattern(self, analyzer: FailurePatternAnalyzer):
        """含 nan 的原因应分类为「信号含 NaN」。"""
        assert analyzer._classify_reason("信号包含 nan") == "信号含 NaN"

    def test_economic_logic_patterns(self, analyzer: FailurePatternAnalyzer):
        """经济逻辑相关分类。"""
        assert analyzer._classify_reason("theory score 不足") == "理论支撑不足"
        assert analyzer._classify_reason("behavioral 维度不达标") == "行为金融解释不足"
        assert analyzer._classify_reason("microstructure 评分低") == "微观结构支撑不足"
        assert analyzer._classify_reason("institutional 可行性不足") == "机构可行性不足"

    def test_multiple_test_patterns(self, analyzer: FailurePatternAnalyzer):
        """多重检验相关分类。"""
        assert analyzer._classify_reason("bonferroni 校正未通过") == "多重检验未通过"
        assert analyzer._classify_reason("FDR 过高") == "多重检验未通过"
        assert analyzer._classify_reason("adjusted_t 不显著") == "t 统计量不显著"

    def test_default_pattern(self, analyzer: FailurePatternAnalyzer):
        """无法匹配的原因应归类为默认。"""
        assert analyzer._classify_reason("未知错误") == DEFAULT_PATTERN
        assert analyzer._classify_reason("some random failure") == DEFAULT_PATTERN

    def test_case_insensitive(self, analyzer: FailurePatternAnalyzer):
        """分类应不区分大小写。"""
        assert analyzer._classify_reason("IC 过低") == "IC 过低"
        assert analyzer._classify_reason("ic 过低") == "IC 过低"
        assert analyzer._classify_reason("Ic 过低") == "IC 过低"


# ─── 分析测试 ──────────────────────────────────────────────

class TestAnalyze:
    """失败模式分析测试。"""

    def test_empty_no_failures(self, analyzer: FailurePatternAnalyzer):
        """无失败轨迹时应返回空字典。"""
        assert analyzer.analyze() == {}

    def test_analyze_single_pattern(self, temp_chain: ExperienceChain, analyzer: FailurePatternAnalyzer):
        """单条失败轨迹应正确分类。"""
        trace = _create_failure_trace("fct_aaa", ["IC=0.01 过低"])
        temp_chain.record_failure(trace)
        result = analyzer.analyze()
        assert result == {"IC 过低": 1}

    def test_analyze_multiple_reasons_same_trace(self, temp_chain: ExperienceChain, analyzer: FailurePatternAnalyzer):
        """同一轨迹的多条原因应去重。"""
        trace = _create_failure_trace(
            "fct_aaa",
            ["IC=0.01 过低", "sharpe 0.3 不达标", "ICIR 不足"],
        )
        temp_chain.record_failure(trace)
        result = analyzer.analyze()
        # "IC 过低" 匹配 "IC" 和 "ICIR" 两个原因 → 但去重后只计 1
        assert result["IC 过低"] == 1
        assert result["夏普比率不达标"] == 1

    def test_analyze_distribution(self, temp_chain: ExperienceChain, analyzer: FailurePatternAnalyzer):
        """多条失败轨迹应正确统计分布。"""
        traces = [
            _create_failure_trace("fct_a", ["IC=0.01 过低"]),
            _create_failure_trace("fct_b", ["IC=0.005 过低"]),
            _create_failure_trace("fct_c", ["IC=0.008 过低"]),
            _create_failure_trace("fct_d", ["sharpe 0.5 不达标"]),
            _create_failure_trace("fct_e", ["sharpe 0.3 不达标"]),
            _create_failure_trace("fct_f", ["max_drawdown 过大"]),
        ]
        for t in traces:
            temp_chain.record_failure(t)

        result = analyzer.analyze()
        assert result["IC 过低"] == 3
        assert result["夏普比率不达标"] == 2
        assert result["最大回撤过大"] == 1

    def test_analyze_respects_max_traces(self, temp_chain: ExperienceChain, analyzer: FailurePatternAnalyzer):
        """应限制分析最近 N 条轨迹。"""
        for i in range(30):
            trace = _create_failure_trace(
                f"fct_{i:03d}", ["IC=0.01 过低"],
                recorded_at=f"2024-01-{min(i+1, 31):02d}T00:00:00",
            )
            temp_chain.record_failure(trace)

        result = analyzer.analyze(max_traces=10)
        total = sum(result.values())
        assert total <= 10  # 最多 10 条轨迹


# ─── 格式化测试 ────────────────────────────────────────────

class TestFormatForLLM:
    """LLM prompt 格式化测试。"""

    def test_format_empty(self, analyzer: FailurePatternAnalyzer):
        """无失败时返回提示。"""
        result = analyzer.format_for_llm()
        assert "暂无失败轨迹" in result

    def test_format_with_patterns(self, temp_chain: ExperienceChain, analyzer: FailurePatternAnalyzer):
        """有失败时返回结构化统计。"""
        traces = [
            _create_failure_trace("fct_a", ["IC=0.01 过低"]),
            _create_failure_trace("fct_b", ["IC=0.005 过低"]),
            _create_failure_trace("fct_c", ["sharpe 0.5 不达标"]),
        ]
        for t in traces:
            temp_chain.record_failure(t)

        result = analyzer.format_for_llm()
        assert "最近 3 次失败的模式分布" in result
        assert "IC 过低" in result
        assert "夏普比率不达标" in result
        assert "重点:" in result  # 包含针对性建议

    def test_format_includes_percentages(self, temp_chain: ExperienceChain, analyzer: FailurePatternAnalyzer):
        """应包含百分比。"""
        trace = _create_failure_trace("fct_a", ["IC=0.01 过低"])
        temp_chain.record_failure(trace)
        result = analyzer.format_for_llm()
        assert "100%" in result


# ─── 关键词映射完整性测试 ──────────────────────────────────

class TestKeywordMapping:
    """关键词映射完整性测试。"""

    def test_all_keywords_have_patterns(self):
        """所有映射值应非空。"""
        for keyword, pattern in FAILURE_PATTERN_KEYWORDS.items():
            assert pattern, f"关键词 '{keyword}' 的模式名称为空"

    def test_default_pattern_defined(self):
        """默认模式应已定义。"""
        assert DEFAULT_PATTERN == "其他原因"