"""
fts.factor_engine.factor_document — 因子逻辑文档化导出（CTA 手册阶段2）。

对照《期货CTA多因子策略标准化作业手册》阶段2 Checkpoint:
    「因子逻辑文档化：公式、经济学逻辑、适用行情环境（趋势市/震荡市）」

从因子元数据生成结构化文档骨架：
    - 公式: 因子 code（表达式）
    - 分类: style_tags / 手册三分类（量价/基本面/期限结构）
    - 参数: params
    - 适用行情环境: 按风格推断（动量→趋势市、均值回归→震荡市、
      carry→全天候底仓、其余→综合）

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any

from .ir_thresholds import classify_factor_category

logger = logging.getLogger(__name__)

# 风格 → 适用行情环境（手册阶段2「适用行情环境（趋势市/震荡市）」）
_STYLE_TO_REGIME: dict[str, str] = {
    "momentum": "趋势市",
    "mean_reversion": "震荡市",
    "volatility": "趋势市/震荡市（择时）",
    "carry": "全天候（Regime 敏感性低，可作底仓）",
    "low_vol": "震荡市",
    "high_beta": "趋势市",
    "defensive": "震荡市",
    "value": "全天候（低频）",
    "quality": "全天候（低频）",
    "sentiment": "趋势市/震荡市（情绪择时）",
    "open_interest": "趋势市",
    "cross_section": "综合",
    "intraday": "综合（日内）",
}

# 类别 → 适用行情环境兜底
_CATEGORY_TO_REGIME: dict[str, str] = {
    "量价": "趋势市/震荡市（按风格细分）",
    "基本面": "全天候（低频）",
    "期限结构": "全天候（Regime 敏感性低，可作底仓）",
}


def _get_field(factor: Any, name: str) -> Any:
    """兼容 dict / 对象两种因子元数据形态取字段。"""
    if isinstance(factor, dict):
        return factor.get(name)
    return getattr(factor, name, None)


def infer_apply_regime(factor: Any) -> str:
    """推断因子适用行情环境（手册阶段2）。

    优先级: style_tags 命中 > 三分类兜底 > 默认综合。

    Args:
        factor: 因子元数据（dict 或对象）

    Returns:
        适用行情环境描述。
    """
    styles = _get_field(factor, "style_tags")
    if styles:
        style_list = styles if isinstance(styles, (list, tuple, set, frozenset)) else [styles]
        for s in style_list:
            env = _STYLE_TO_REGIME.get(str(s).lower())
            if env:
                return env
    # 无风格元数据 → 默认综合（三分类兜底仅对确有风格标签的因子生效）
    if not styles and not _get_field(factor, "style"):
        return "综合"
    category = classify_factor_category(factor)
    return _CATEGORY_TO_REGIME.get(category, "综合")


def build_factor_document(factor: Any) -> dict:
    """生成因子结构化文档（公式/分类/参数/适用环境）。

    Args:
        factor: 因子元数据（dict 或对象，含 code/style_tags/params 等）

    Returns:
        dict: {
            factor_id, name, formula(code), category(手册三分类),
            style_tags, params, apply_regime(适用行情环境),
        }
    """
    styles = _get_field(factor, "style_tags") or _get_field(factor, "style")
    return {
        "factor_id": _get_field(factor, "factor_id") or "",
        "name": _get_field(factor, "name") or _get_field(factor, "factor_id") or "",
        "formula": _get_field(factor, "code") or "",
        "category": classify_factor_category(factor),
        "style_tags": list(styles) if styles else [],
        "params": _get_field(factor, "params") or {},
        "apply_regime": infer_apply_regime(factor),
    }


def render_factor_document(factor: Any) -> str:
    """将因子文档渲染为 Markdown 文本（可落盘 / 报告引用）。

    Args:
        factor: 因子元数据

    Returns:
        Markdown 文档字符串。
    """
    doc = build_factor_document(factor)
    lines = [
        f"### 因子: {doc['name']}",
        "",
        f"- **公式**: `{doc['formula'] or '—'}`",
        f"- **类别**: {doc['category']}",
        f"- **风格标签**: {', '.join(doc['style_tags']) if doc['style_tags'] else '—'}",
        f"- **参数**: {doc['params'] if doc['params'] else '—'}",
        f"- **适用行情环境**: {doc['apply_regime']}",
        "",
    ]
    return "\n".join(lines)


__all__ = ["build_factor_document", "render_factor_document", "infer_apply_regime"]
