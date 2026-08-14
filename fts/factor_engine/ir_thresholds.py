"""
fts.factor_engine.ir_thresholds — 因子 IR 分类门槛（CTA 手册阶段4）。

对照《期货CTA多因子策略标准化作业手册》阶段4 Checkpoint:
    「IR 达到分类门槛（量价≥0.3 / 基本面≥0.4 / 期限结构≥0.35）」

国内商品期货日频 IC 统计特性差异大，单一 IR 门槛会导致量价因子被误杀，
按因子类别分级设门槛（v1.2 修订核心内容）:
    - 量价技术因子：   IR ≥ 0.30（信号噪声比高，0.3 已属优质）
    - 基本面产业链因子：IR ≥ 0.40（低频信号允许更高波动）
    - 期限结构因子：   IR ≥ 0.35（中频信号，介于量价与基本面之间）

类别判定映射（基于 FTS 因子元数据 style_tags，缺省按最宽松的量价档，
避免未知类别因子被误杀）:
    - 期限结构: style=carry
    - 基本面:   style∈{value, quality, sentiment}
    - 量价:     其余（trend/mean_reversion/technical/liquidity/volatility/volume/microstructure 等）

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 手册阶段4 分级 IR 门槛（按因子类别）
IR_THRESHOLDS: dict[str, float] = {
    "量价": 0.30,
    "基本面": 0.40,
    "期限结构": 0.35,
}

# 未分类/未知类别回退（取最宽松档，不误杀）
DEFAULT_IR_THRESHOLD: float = 0.30

# style_tags → 类别（仅列出有明确归属的，其余默认量价）
_STYLE_TO_CATEGORY: dict[str, str] = {
    "carry": "期限结构",
    "value": "基本面",
    "quality": "基本面",
    "sentiment": "基本面",
}


def _get_field(factor: Any, name: str) -> Any:
    """兼容 dict / 对象两种因子元数据形态取字段。"""
    if isinstance(factor, dict):
        return factor.get(name)
    return getattr(factor, name, None)


def classify_factor_category(factor: Any) -> str:
    """按因子元数据判定手册三分类（量价 / 基本面 / 期限结构）。

    Args:
        factor: 因子元数据（dict 或含 style_tags 属性的对象）

    Returns:
        类别名（"量价" / "基本面" / "期限结构"），未知回退"量价"。
    """
    styles = _get_field(factor, "style_tags")
    if not styles:
        styles = _get_field(factor, "style")
    if styles:
        style_list = styles if isinstance(styles, (list, tuple, set, frozenset)) else [styles]
        for s in style_list:
            s_name = str(s).lower()
            if s_name in _STYLE_TO_CATEGORY:
                return _STYLE_TO_CATEGORY[s_name]
    return "量价"


def factor_ir_threshold(factor: Any) -> float:
    """返回因子对应的分类 IR 门槛（手册阶段4）。

    Args:
        factor: 因子元数据（dict 或对象）

    Returns:
        IR 门槛（0.30 / 0.35 / 0.40）。
    """
    category = classify_factor_category(factor)
    return IR_THRESHOLDS.get(category, DEFAULT_IR_THRESHOLD)


__all__ = ["IR_THRESHOLDS", "DEFAULT_IR_THRESHOLD", "classify_factor_category", "factor_ir_threshold"]
