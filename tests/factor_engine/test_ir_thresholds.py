"""test_ir_thresholds — 因子 IR 分类门槛单元测试（CTA 手册阶段4）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fts.factor_engine.ir_thresholds import (
    DEFAULT_IR_THRESHOLD,
    IR_THRESHOLDS,
    classify_factor_category,
    factor_ir_threshold,
)


# ─── 门槛值定义 ───────────────────────────────────────────


def test_threshold_values() -> None:
    """量价 0.30 / 基本面 0.40 / 期限结构 0.35。"""
    assert IR_THRESHOLDS["量价"] == pytest.approx(0.30)
    assert IR_THRESHOLDS["基本面"] == pytest.approx(0.40)
    assert IR_THRESHOLDS["期限结构"] == pytest.approx(0.35)


def test_style_carry() -> None:
    """style_tags 含 carry → 期限结构。"""
    assert factor_ir_threshold({"style_tags": ["momentum", "carry"]}) == pytest.approx(0.35)


def test_style_value_fundamental() -> None:
    """style_tags 含 value → 基本面。"""
    assert factor_ir_threshold({"style_tags": ["value"]}) == pytest.approx(0.40)


def test_style_quality_sentiment_fundamental() -> None:
    """style_tags 含 quality/sentiment → 基本面。"""
    assert factor_ir_threshold({"style_tags": ["quality"]}) == pytest.approx(0.40)
    assert factor_ir_threshold({"style_tags": ["sentiment"]}) == pytest.approx(0.40)


def test_style_unknown_default() -> None:
    """style_tags 无明确归属 → 量价默认档。"""
    assert factor_ir_threshold({"style_tags": ["momentum"]}) == pytest.approx(0.30)
    assert factor_ir_threshold({"style_tags": []}) == pytest.approx(0.30)


# ─── 无元数据 / 兼容形态 ──────────────────────────────────


def test_no_metadata_default() -> None:
    """无 style_tags 字段 → 量价默认档。"""
    assert factor_ir_threshold({}) == pytest.approx(0.30)
    assert classify_factor_category({}) == "量价"


def test_dict_and_object_forms_consistent() -> None:
    """dict 与对象两种形态结果一致。"""
    d = {"style_tags": ["carry", "momentum"]}
    obj = SimpleNamespace(style_tags=["carry"])
    assert factor_ir_threshold(d) == factor_ir_threshold(obj) == pytest.approx(0.35)


def test_object_without_style_tags() -> None:
    """对象缺 style_tags 属性不崩溃（getattr 兜底，回退 style 字段）。"""
    obj = SimpleNamespace(style=["value"])
    assert factor_ir_threshold(obj) == pytest.approx(0.40)
