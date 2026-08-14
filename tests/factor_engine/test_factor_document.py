"""test_factor_document — 因子逻辑文档化测试（CTA 手册阶段2）。"""

from __future__ import annotations

from types import SimpleNamespace

from fts.factor_engine.factor_document import (
    build_factor_document,
    infer_apply_regime,
    render_factor_document,
)


def test_infer_apply_regime_momentum() -> None:
    """动量风格 → 趋势市。"""
    assert infer_apply_regime({"style_tags": ["momentum"]}) == "趋势市"


def test_infer_apply_regime_mean_reversion() -> None:
    """均值回归风格 → 震荡市。"""
    assert infer_apply_regime({"style_tags": ["mean_reversion"]}) == "震荡市"


def test_infer_apply_regime_carry() -> None:
    """Carry → 全天候底仓。"""
    assert infer_apply_regime({"style_tags": ["carry"]}) == "全天候（Regime 敏感性低，可作底仓）"


def test_infer_apply_regime_by_style_tags() -> None:
    """风格标签命中全天候语义（value/carry）。"""
    assert "全天候" in infer_apply_regime({"style_tags": ["value"]})
    assert "全天候" in infer_apply_regime({"style_tags": ["carry"]})


def test_infer_apply_regime_default() -> None:
    """未知 → 综合。"""
    assert infer_apply_regime({}) == "综合"


def test_build_factor_document_dict() -> None:
    """dict 形态因子 → 结构化文档。"""
    factor = {
        "factor_id": "mom_20",
        "name": "动量20日",
        "code": "rank(ts_zscore(close, 20))",
        "style_tags": ["momentum"],
        "params": {"N": 20},
    }
    doc = build_factor_document(factor)
    assert doc["factor_id"] == "mom_20"
    assert doc["category"] == "量价"
    assert doc["formula"] == "rank(ts_zscore(close, 20))"
    assert doc["style_tags"] == ["momentum"]
    assert doc["apply_regime"] == "趋势市"


def test_build_factor_document_object() -> None:
    """对象形态因子 → 结构化文档。"""
    factor = SimpleNamespace(
        factor_id="carry_1",
        name="展期收益",
        code="futures_carry()",
        style_tags=["carry"],
        params={"n": 5},
    )
    doc = build_factor_document(factor)
    assert doc["category"] == "期限结构"
    assert doc["apply_regime"].startswith("全天候")


def test_render_factor_document_markdown() -> None:
    """Markdown 渲染包含关键字段。"""
    text = render_factor_document({"factor_id": "f1", "code": "close.pct_change(5)", "style_tags": ["momentum"]})
    assert "### 因子: f1" in text
    assert "`close.pct_change(5)`" in text
    assert "适用行情环境" in text
    assert "趋势市" in text
