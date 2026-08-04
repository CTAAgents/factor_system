"""
test_natural_experiments.py — 自然实验事件定义测试。

HARNESS §11-logic-review-plan.md §C.1:
    验证自然实验事件定义的正确性和查找工具。
"""

from __future__ import annotations

from datetime import date

from fts.factor_engine.causal_validator import (
    CausalValidationResult,
    CausalValidator,
    EventPredictionError,
    _import_default_events,
)
from tests.scenarios.natural_experiments import (
    DEFAULT_EVENTS,
    get_event_by_id,
    get_events_by_type,
    get_events_for_symbol,
)


# ─── 事件定义测试 ──────────────────────────────────────────


class TestNaturalExperimentDefinitions:
    """验证自然实验事件定义的正确性。"""

    def test_default_events_not_empty(self):
        """DEFAULT_EVENTS 不应为空。"""
        assert len(DEFAULT_EVENTS) > 0, "应有至少 1 个自然实验事件"

    def test_default_events_include_circuit_breaker(self):
        """应包含熔断事件。"""
        cb_events = get_events_by_type(DEFAULT_EVENTS, "circuit_breaker")
        assert len(cb_events) >= 1, "应至少包含 1 个熔断事件"

    def test_default_events_include_policy_shock(self):
        """应包含政策冲击事件。"""
        ps_events = get_events_by_type(DEFAULT_EVENTS, "policy_shock")
        assert len(ps_events) >= 1, "应至少包含 1 个政策冲击事件"

    def test_events_have_required_fields(self):
        """每个事件应包含所有必要字段。"""
        for event in DEFAULT_EVENTS:
            assert event.event_id, "event_id 不能为空"
            assert event.event_type, "event_type 不能为空"
            assert isinstance(event.event_date, date), "event_date 应为 date 类型"
            assert event.name, "name 不能为空"
            assert event.expected_direction in ("positive", "negative", "unknown")
            assert event.pre_window > 0, "pre_window 应大于 0"
            assert event.post_window > 0, "post_window 应大于 0"

    def test_event_ids_unique(self):
        """event_id 应全局唯一。"""
        ids = [e.event_id for e in DEFAULT_EVENTS]
        assert len(ids) == len(set(ids)), "event_id 应唯一"


# ─── 查找工具测试 ──────────────────────────────────────────


class TestNaturalExperimentLookup:
    """验证事件查找工具。"""

    def test_get_event_by_id_found(self):
        """按 event_id 查找应返回正确事件。"""
        event = get_event_by_id(DEFAULT_EVENTS, "cb_2016_01_04")
        assert event is not None
        assert event.name == "2016 年首次熔断"

    def test_get_event_by_id_not_found(self):
        """不存在的 event_id 应返回 None。"""
        event = get_event_by_id(DEFAULT_EVENTS, "nonexistent")
        assert event is None

    def test_get_events_by_type(self):
        """按类型过滤应返回正确的事件列表。"""
        cb_events = get_events_by_type(DEFAULT_EVENTS, "circuit_breaker")
        assert all(e.event_type == "circuit_breaker" for e in cb_events)

    def test_get_events_for_symbol(self):
        """按品种过滤应返回匹配事件。"""
        events = get_events_for_symbol(DEFAULT_EVENTS, "I0")
        assert len(events) >= 1
        assert any(e.event_id == "iron_ore_2023_06" for e in events)


# ─── 导入工具测试 ──────────────────────────────────────────


class TestImportDefaultEvents:
    """验证 _import_default_events 函数。"""

    def test_import_returns_list(self):
        """导入应返回非空列表。"""
        events = _import_default_events()
        assert isinstance(events, list)
        assert len(events) > 0

    def test_imported_events_have_expected_fields(self):
        """导入的事件应包含必要字段。"""
        events = _import_default_events()
        for event in events:
            assert hasattr(event, "event_id")
            assert hasattr(event, "event_type")
            assert hasattr(event, "event_date")