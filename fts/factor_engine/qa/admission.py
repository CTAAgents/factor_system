"""
fts.factor_engine.qa.admission — 因子三级准入分类（CTA 手册 6.3）。

对照《期货CTA多因子策略标准化作业手册》6.3 三级准入分类标准:
    | 等级 | 综合得分 | IR 门槛 | 权重上限 | 状态 |
    | 核心库 | ≥4 | 达到分类门槛 | 单因子权重≤30% | 正式服役 |
    | 候选库 | 3~4 | 达到分类门槛 | 单因子权重≤15% | 观察期服役 |
    | 淘汰 | <3 | 未达门槛 | 0% | 归档淘汰库 |

纯函数 / 边界含入判定（score=4.0 归核心库） / 参数配置化。

版本: v1.0.0
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 三级准入配置: (等级, 中文名, 最低综合得分, 权重上限, 状态)
ADMISSION_LEVELS: list[dict] = [
    {"level": "CORE", "label": "核心库", "min_score": 4.0, "max_weight": 0.30, "status": "正式服役"},
    {"level": "CANDIDATE", "label": "候选库", "min_score": 3.0, "max_weight": 0.15, "status": "观察期服役"},
    {"level": "REJECTED", "label": "淘汰", "min_score": None, "max_weight": 0.0, "status": "归档淘汰库"},
]

# 手册默认评分阈值（可覆盖）
DEFAULT_CORE_MIN_SCORE = 4.0
DEFAULT_CANDIDATE_MIN_SCORE = 3.0


def classify_admission(
    score: float,
    ir_ok: bool,
    core_min_score: float = DEFAULT_CORE_MIN_SCORE,
    candidate_min_score: float = DEFAULT_CANDIDATE_MIN_SCORE,
) -> str:
    """三级准入分类（手册 6.3）。

    Args:
        score: 综合得分（0-5）
        ir_ok: 是否达到因子类别分类 IR 门槛
        core_min_score: 核心库最低综合得分（默认 4.0）
        candidate_min_score: 候选库最低综合得分（默认 3.0）

    Returns:
        str: "CORE" | "CANDIDATE" | "REJECTED"
    """
    if not ir_ok or score < candidate_min_score:
        return "REJECTED"
    if score >= core_min_score:
        return "CORE"
    return "CANDIDATE"


def max_weight_for(level: str) -> float:
    """返回指定准入等级的权重上限（手册 6.3：核心≤30%/候选≤15%/其他 0）。"""
    for spec in ADMISSION_LEVELS:
        if spec["level"] == level:
            return spec["max_weight"]
    return 0.0


def level_label(level: str) -> str:
    """返回准入等级中文名。"""
    for spec in ADMISSION_LEVELS:
        if spec["level"] == level:
            return spec["label"]
    return "未知"


def admission_summary(score: float, ir_ok: bool) -> dict:
    """准入评估汇总（供质检报告第九部分引用）。

    Returns:
        dict: {score, ir_ok, level, label, max_weight, status}
    """
    level = classify_admission(score, ir_ok)
    spec = next((s for s in ADMISSION_LEVELS if s["level"] == level), ADMISSION_LEVELS[-1])
    return {
        "score": float(score),
        "ir_ok": bool(ir_ok),
        "level": level,
        "label": spec["label"],
        "max_weight": spec["max_weight"],
        "status": spec["status"],
    }


__all__ = [
    "ADMISSION_LEVELS",
    "DEFAULT_CORE_MIN_SCORE",
    "DEFAULT_CANDIDATE_MIN_SCORE",
    "classify_admission",
    "max_weight_for",
    "level_label",
    "admission_summary",
]
