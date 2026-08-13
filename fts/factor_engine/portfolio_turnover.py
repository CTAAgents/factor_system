"""
fts.factor_engine.portfolio_turnover — 组合换手预算分配（G3，35-gap-closure-plan）。

对照《全链路 SOP》阶段 7「换手率全局上限约束」：单日组合总换手超过上限时，
优先剔除边际收益最低的弱信号，降低摩擦成本。

设计约束:
    - 纯函数、零未来函数（仅使用当期目标权重 / 当前持仓 / 边际收益评分）
    - 假定输入权重已归一化（target_weights 与 current_weights 尺度一致）
    - 剔除语义 = 目标回退到当前持仓（今日不对该因子调仓），非强制平仓
    - 剔除后对保留项重新归一化（剔除改变了权重总和）

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TurnoverBudgetConfig:
    """组合换手预算配置（G3）。

    Attributes:
        enabled: 是否启用（默认 True）
        daily_turnover_cap: 单日组合换手上限（默认 0.30，单边换手率）
        prioritize_by: 边际收益排序口径（默认 "sharpe"；由调用方传入的 scores 决定）
        drop_weakest: 超限时是否剔除最弱信号（True=剔除至达标；False=保留全部仅记录告警）
    """

    enabled: bool = True
    daily_turnover_cap: float = 0.30
    prioritize_by: str = "sharpe"
    drop_weakest: bool = True


def _single_side_turnover(
    target_weights: dict[str, float],
    current_weights: dict[str, float],
) -> float:
    """单边换手率 = Σ|Δw| / 2（0~1，1 表示完全换仓）。"""
    if not target_weights:
        return 0.0
    return (
        sum(
            abs(float(target_weights.get(s, 0.0) or 0.0) - float(current_weights.get(s, 0.0) or 0.0))
            for s in target_weights
        )
        / 2.0
    )


def allocate_turnover_budget(
    target_weights: dict[str, float],
    current_weights: dict[str, float],
    factor_scores: dict[str, float],
    config: Optional[TurnoverBudgetConfig] = None,
) -> dict[str, float]:
    """组合换手预算分配：单日换手超上限时剔除边际收益最低的弱信号。

    Args:
        target_weights: 目标权重 {factor_id: weight}（假定已归一化）
        current_weights: 当前持仓权重 {factor_id: weight}（上次组合，已归一化）
        factor_scores: 边际收益评分 {factor_id: score}（越大越优先保留；
            由调用方按 config.prioritize_by 口径传入）
        config: TurnoverBudgetConfig；None 或 enabled=False 原样返回

    Returns:
        裁剪后的目标权重 dict（剔除项回退当前持仓，保留项重新归一化）。
        未触发时返回 target_weights 副本。
    """
    cfg = config if config is not None else TurnoverBudgetConfig()
    if not cfg.enabled or not target_weights:
        return dict(target_weights)

    cap = float(cfg.daily_turnover_cap)
    # 浮点容差：防止边界（turnover == cap）因表示误差被误剔除
    eps = 1e-9
    turnover = _single_side_turnover(target_weights, current_weights)
    if turnover <= cap + eps:
        return dict(target_weights)

    result = dict(target_weights)
    if not cfg.drop_weakest:
        logger.warning(
            "[TURNOVER] 单日换手 %.3f > cap %.3f 且 drop_weakest=False，仅告警不裁剪",
            turnover,
            cap,
        )
        return result

    # 按边际收益升序（最弱在前），从最弱开始回退当前持仓直至换手达标
    ranked = sorted(result.keys(), key=lambda s: float(factor_scores.get(s, 0.0) or 0.0))
    for s in ranked:
        if _single_side_turnover(result, current_weights) <= cap + eps:
            break
        cur = float(current_weights.get(s, 0.0) or 0.0)
        result[s] = cur  # 剔除：目标回退当前持仓（今日不调仓该因子）

    # 剔除后重新归一化（剔除项回退 current，可能改变总和）
    total = sum(float(v) for v in result.values())
    if total > 1e-12:
        result = {s: float(w) / total for s, w in result.items()}

    final_turnover = _single_side_turnover(result, current_weights)
    logger.info(
        "[TURNOVER] 换手预算裁剪: 原始=%.3f → 裁剪后=%.3f (cap=%.3f, 剔除 %d 个弱信号)",
        turnover,
        final_turnover,
        cap,
        sum(1 for s in target_weights if abs(result.get(s, 0.0) - target_weights[s]) > 1e-12),
    )
    return result


__all__ = ["TurnoverBudgetConfig", "allocate_turnover_budget"]
