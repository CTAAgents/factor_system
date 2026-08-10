"""tests/factor_engine/test_failure_classifier.py — 失败模式分类器测试

覆盖:
- FailurePattern 模式描述
- 审计报告驱动的失败模式识别
- 因子指标驱动的量化识别
- 改善建议生成（去重、优先级）
- 严重性评估
- 端到端分类流程

版本: v1.0
"""

from __future__ import annotations

from dataclasses import asdict


from fts.factor_engine.audit import (
    AuditItemResult,
    FailureClassifier,
    FailurePattern,
    FactorAuditReport,
    ImprovementSuggestion,
)


ALL_ITEM_NAMES: list[str] = [
    "causal_validity",
    "oos_consistency",
    "cross_symbol",
    "stress_resilience",
    "multiple_testing",
    "snooping_check",
]


# ─── Fixtures ──────────────────────────────────────────────


def _make_report(
    factor_id: str = "f_test",
    failed: list[str] | None = None,
) -> FactorAuditReport:
    """构造测试用 FactorAuditReport。"""
    items = [
        AuditItemResult(
            name=n,
            status="failed" if n in (failed or []) else "passed",
            evidence="test",
        )
        for n in ALL_ITEM_NAMES
    ]

    return FactorAuditReport(
        factor_id=factor_id,
        factor_name=f"Factor {factor_id}",
        audited_at="2025-01-01T00:00:00",
        items=items,
        passed=not (failed or []),
        pass_rate=0.0 if failed else 1.0,
        summary={"failed_items": failed or []},
    )


# ─── FailurePattern ────────────────────────────────────────


class TestFailurePattern:
    def test_describe_known(self):
        desc = FailurePattern.describe("negative_ic")
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_describe_unknown(self):
        desc = FailurePattern.describe("unknown_pattern_xyz")
        assert desc == "unknown_pattern_xyz"

    def test_all_patterns_have_description(self):
        pattern_attrs = {k: v for k, v in vars(FailurePattern).items() if k.isupper() and not k.startswith("_")}
        for key in pattern_attrs:
            value = pattern_attrs[key]
            if isinstance(value, str):
                desc = FailurePattern.describe(value)
                assert desc != value or value == "unknown_pattern_xyz"


# ─── 审计报告驱动分类 ──────────────────────────────────────


class TestReportDrivenClassification:
    def test_no_failures(self):
        classifier = FailureClassifier()
        report = _make_report(failed=[])
        result = classifier.classify(audit_report=report)

        assert result["factor_id"] == "f_test"
        assert result["num_patterns"] == 0
        assert result["severity"] == "healthy"
        assert len(result["suggestions"]) == 0

    def test_negative_ic_from_metrics(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"ic": -0.05, "sharpe": 1.2})

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "negative_ic" in patterns
        assert any(s.pattern == "negative_ic" and "反转" in s.action for s in result["suggestions"])

    def test_sharpe_low(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"sharpe": 0.3, "ic": 0.05})

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "sharpe_low" in patterns

    def test_high_turnover(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"turnover": 1.5, "ic": 0.05})

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "high_turnover" in patterns

    def test_oos_instability_from_metrics(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"oos_pass_ratio": 0.3, "ic": 0.05})

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "oos_instability" in patterns

    def test_cross_symbol_from_metrics(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"cross_symbol_ratio": 0.5, "ic": 0.05})

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "cross_symbol_failure" in patterns

    def test_ic_decay(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"ic_trend": "declining", "ic": 0.02})

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "ic_decay" in patterns

    def test_snooping_failure(self):
        classifier = FailureClassifier()
        report = _make_report(failed=["snooping_check"])
        result = classifier.classify(audit_report=report)

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "snooping_suspected" in patterns

    def test_stress_failure(self):
        classifier = FailureClassifier()
        report = _make_report(failed=["stress_resilience"])
        result = classifier.classify(audit_report=report)

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "stress_vulnerable" in patterns

    def test_multiple_testing_failure(self):
        classifier = FailureClassifier()
        report = _make_report(failed=["multiple_testing"])
        result = classifier.classify(audit_report=report)

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "multiple_testing" in patterns

    def test_causal_failure(self):
        classifier = FailureClassifier()
        report = _make_report(failed=["causal_validity"])
        result = classifier.classify(audit_report=report)

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "causal_weak" in patterns

    def test_oos_from_report(self):
        classifier = FailureClassifier()
        report = _make_report(failed=["oos_consistency"])
        result = classifier.classify(audit_report=report)

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "oos_instability" in patterns

    def test_cross_symbol_from_report(self):
        classifier = FailureClassifier()
        report = _make_report(failed=["cross_symbol"])
        result = classifier.classify(audit_report=report)

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "cross_symbol_failure" in patterns


# ─── 组合分类（审计报告 + 指标） ────────────────────────────


class TestCombinedClassification:
    def test_report_and_metrics_combined(self):
        classifier = FailureClassifier()
        report = _make_report(failed=["snooping_check"])
        result = classifier.classify(
            audit_report=report,
            factor_metrics={"ic": -0.03, "sharpe": 0.2, "turnover": 1.2},
        )

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "snooping_suspected" in patterns
        assert "negative_ic" in patterns
        assert "sharpe_low" in patterns
        assert "high_turnover" in patterns
        assert result["num_patterns"] >= 4

    def test_deduplicate_within_report_and_metrics(self):
        classifier = FailureClassifier()
        report = _make_report(failed=["oos_consistency"])
        result = classifier.classify(
            audit_report=report,
            factor_metrics={"oos_pass_ratio": 0.2},
        )

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert patterns.count("oos_instability") == 1


# ─── 严重性评估 ────────────────────────────────────────────


class TestSeverity:
    def test_high_severity(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"ic": -0.05})
        assert result["severity"] == "high"

    def test_medium_severity(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"sharpe": 0.3, "turnover": 1.5})
        assert result["severity"] == "medium"

    def test_low_severity(self):
        classifier = FailureClassifier()
        # 触发单一 medium confidence 模式
        result = classifier.classify(audit_report=_make_report(failed=["causal_validity"]))
        assert result["severity"] == "low"

    def test_healthy_severity(self):
        classifier = FailureClassifier()
        result = classifier.classify()
        assert result["severity"] == "healthy"


# ─── 建议生成 ────────────────────────────────────────────


class TestSuggestions:
    def test_suggestions_have_required_fields(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"ic": -0.05})
        for s in result["suggestions"]:
            assert isinstance(s, ImprovementSuggestion)
            assert s.pattern
            assert s.priority in {"high", "medium", "low"}
            assert s.action
            assert s.rationale
            assert s.expected_improvement

    def test_suggestions_deduplicated(self):
        classifier = FailureClassifier()
        result = classifier.classify(
            audit_report=_make_report(failed=["oos_consistency"]),
            factor_metrics={"oos_pass_ratio": 0.2},
        )
        actions = [s.action for s in result["suggestions"]]
        assert len(actions) == len(set(actions))

    def test_high_priority_first(self):
        classifier = FailureClassifier()
        result = classifier.classify(factor_metrics={"ic": -0.05, "sharpe": 0.3})
        if len(result["suggestions"]) >= 2:
            priorities = [s.priority for s in result["suggestions"]]
            high_indexes = [i for i, p in enumerate(priorities) if p == "high"]
            medium_indexes = [i for i, p in enumerate(priorities) if p == "medium"]
            if high_indexes and medium_indexes:
                assert min(high_indexes) <= max(medium_indexes)

    def test_each_pattern_has_suggestion(self):
        classifier = FailureClassifier()
        for pattern_name in classifier.PATTERN_TO_SUGGESTIONS:
            assert len(classifier.PATTERN_TO_SUGGESTIONS[pattern_name]) > 0


# ─── 端到端 ────────────────────────────────────────────────


class TestEndToEnd:
    def test_full_workflow(self):
        classifier = FailureClassifier()
        report = _make_report(
            factor_id="f_momentum",
            failed=["oos_consistency", "cross_symbol", "causal_validity"],
        )
        result = classifier.classify(
            audit_report=report,
            factor_metrics={
                "factor_id": "f_momentum",
                "ic": 0.02,
                "sharpe": 0.4,
                "turnover": 0.8,
                "oos_pass_ratio": 0.33,
                "cross_symbol_ratio": 0.6,
                "ic_trend": "declining",
            },
        )

        assert result["factor_id"] == "f_momentum"
        assert result["num_patterns"] >= 4
        assert len(result["suggestions"]) >= 4
        assert result["severity"] in {"high", "medium", "low"}

        for s in result["suggestions"]:
            assert isinstance(asdict(s), dict)

    def test_empty_input(self):
        classifier = FailureClassifier()
        result = classifier.classify()
        assert result["num_patterns"] == 0
        assert result["severity"] == "healthy"
        assert result["suggestions"] == []

    def test_audit_report_only(self):
        classifier = FailureClassifier()
        report = _make_report(failed=["snooping_check", "stress_resilience"])
        result = classifier.classify(audit_report=report)

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert "snooping_suspected" in patterns
        assert "stress_vulnerable" in patterns

    def test_metrics_only(self):
        classifier = FailureClassifier()
        result = classifier.classify(
            factor_metrics={
                "ic": -0.04,
                "sharpe": 0.2,
                "turnover": 1.3,
                "ic_trend": "declining",
            }
        )

        patterns = [p["pattern"] for p in result["detected_patterns"]]
        assert len(patterns) == 4
