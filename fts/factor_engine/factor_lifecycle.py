"""
fts.factor_engine.factor_lifecycle — 因子生命周期管理（CTA 手册阶段11.3）。

对照《期货CTA多因子策略标准化作业手册》阶段11.3「因子生命周期管理」:
    - 每个因子有明确的服役期和退役标准
    - 滚动 60 日 IC 均值较样本外训练期下降超过 30%，或 IR 跌破 0.3
      → 自动归零权重并进入复审队列
    - 因子库只增不减会导致模型漂移累积，需定期清理

设计约束:
    - 纯函数 / 零未来函数（仅用截至当前的有效 IC） / NaN 兜底
    - 样本不足或对照基准不可用时 → 不判退役（保守保活）

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# 手册 11.3: 滚动 60 日 IC 均值对照窗口
DEFAULT_WINDOW = 60
# 手册 11.3: 滚动 IC 均值较样本外训练期衰减超过 30% 触发退役复审
DEFAULT_IC_DECAY_RATIO = 0.30
# 手册 11.3: 滚动 IR 跌破 0.3 触发退役复审
DEFAULT_IR_FLOOR = 0.30
# 年化因子（滚动窗口内 IC 序列标准差换算为年化 IR）
_ANNUALIZE = 252


def _rolling_stats(
    ic_series: np.ndarray,
    window: int,
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """取最近 window 个有效 IC 的均值、标准差与年化 IR。

    Returns:
        (mean, std, ir)；有效样本不足 → (None, None, None)
    """
    vals = ic_series[np.isfinite(ic_series)]
    if len(vals) < max(5, window // 10):
        return None, None, None
    tail = vals[-window:]
    mean = float(np.mean(tail))
    std = float(np.std(tail))
    ir = mean / std * np.sqrt(_ANNUALIZE) if std > 1e-12 else 0.0
    return mean, std, ir


def factor_lifecycle_review(
    ic_series: np.ndarray,
    oos_baseline_ic: float,
    window: int = DEFAULT_WINDOW,
    ic_decay_ratio: float = DEFAULT_IC_DECAY_RATIO,
    ir_floor: float = DEFAULT_IR_FLOOR,
) -> dict:
    """滚动 60 日 IC/IR 衰减审查（手册 11.3 退役标准）。

    Args:
        ic_series: 每日 IC 序列（截至当前，不含未来）
        oos_baseline_ic: 样本外训练期 IC 均值（对照基准）
        window: 滚动窗口天数（默认 60）
        ic_decay_ratio: IC 衰减触发阈值（默认 0.30，即下降超过 30%）
        ir_floor: IR 触发下限（默认 0.30）

    Returns:
        dict: {
            window_ic, rolling_ir, decay_ratio,
            ic_triggered, ir_triggered,
            action: "hold" | "zero_weight_review",
            reasons: [str, ...],
        }
    """
    arr = np.asarray(ic_series, dtype=float)
    mean, std, ir = _rolling_stats(arr, window)
    result: dict = {
        "window_ic": mean,
        "rolling_ir": ir,
        "decay_ratio": None,
        "ic_triggered": False,
        "ir_triggered": False,
        "action": "hold",
        "reasons": [],
    }
    if mean is None or ir is None:
        result["reasons"].append("滚动窗口有效 IC 样本不足，暂不判退役")
        return result

    baseline = float(oos_baseline_ic)
    decay = 1.0 - mean / baseline if abs(baseline) > 1e-12 and baseline > 0 else 0.0
    result["decay_ratio"] = float(decay)
    if decay > ic_decay_ratio:
        result["ic_triggered"] = True
        result["reasons"].append(
            f"滚动{window}日 IC 均值 {mean:.4f} 较样本外基准 {baseline:.4f} 衰减 {decay:.1%} > {ic_decay_ratio:.0%}"
        )
    if ir < ir_floor:
        result["ir_triggered"] = True
        result["reasons"].append(f"滚动 IR {ir:.2f} 跌破下限 {ir_floor:.2f}")
    if result["ic_triggered"] or result["ir_triggered"]:
        result["action"] = "zero_weight_review"
        result["reasons"].append("归零权重并进入复审队列")
    return result


def factor_lifecycle_plan() -> dict:
    """因子生命周期管理机制说明（手册 11.3 机器可读配置）。"""
    return {
        "window": DEFAULT_WINDOW,
        "ic_decay_ratio": DEFAULT_IC_DECAY_RATIO,
        "ir_floor": DEFAULT_IR_FLOOR,
        "retire_action": "zero_weight_review",
        "rules": [
            "滚动60日IC均值较样本外训练期下降超过30% → 归零权重并进入复审队列",
            "IR跌破0.3 → 归零权重并进入复审队列",
            "因子库定期清理，避免模型漂移累积",
        ],
    }


def factor_lifecycle_review_subchain(
    rows: list[dict],
    decay_threshold: float = 0.30,
    drop_severe: float = 0.50,
    min_periods: int = 5,
) -> dict:
    """因子×子链生命周期复核（plans/49 §C3 子链重载，保留全链原函数向后兼容）。

    基于 ``subchain_factor_quality`` 质量矩阵时序做**单元粒度**退化检测
    （复用 ``subchain_lifecycle.compute_subchain_degradation``）——解决全链 IC 掩蔽：
    部分有效链失效 → scope_shrink（收缩而非整因子降级）；全部有效链失效 / 单链特异
    唯一链失效 → degrade。

    Args:
        rows: ``SubchainQualityRepository.query_subchain_quality`` 输出
            （factor×chain 多期，含 evaluated_at/mean_ic/effective）
        decay_threshold: 有效链 IC 衰减触发阈值（> 阈值 → 标记 scope_shrink）
        drop_severe: 全部有效链衰减严重阈值（> 阈值且当前不 effective → degrade）
        min_periods: 子链质量时序最少期数（不足 → keep，不误判）

    Returns:
        {factor_status, per_chain, ever_effective_chains, scope_shrink_chains,
         degrade_chains, detail}——见 ``compute_subchain_degradation`` 契约。
    """
    from fts.factor_engine.subchain_lifecycle import (
        SubchainLifecycleConfig,
        compute_subchain_degradation,
    )

    cfg = SubchainLifecycleConfig(
        enabled=True,
        decay_threshold=decay_threshold,
        drop_severe=drop_severe,
        min_periods=min_periods,
    )
    return compute_subchain_degradation(rows, cfg)


__all__ = [
    "DEFAULT_WINDOW",
    "DEFAULT_IC_DECAY_RATIO",
    "DEFAULT_IR_FLOOR",
    "factor_lifecycle_review",
    "factor_lifecycle_review_subchain",
    "factor_lifecycle_plan",
]
