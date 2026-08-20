"""
fts.factor_engine.qa.liquidity_env — 容量/交易性评分流动性环境动态化（plans/59 OPT-08 / GAP-168）。

`factor_quality_card` 的 capacity_score/tradability_score 基于静态容量估算与换手，
期货合约月份/换月窗口流动性变化大——移仓期实际可交易容量显著收缩，静态评分失真。

本模块提供纯函数：
  - ``liquidity_env_scale``：按流动性快照（移仓窗口标记 / 价差比）计算容量缩放系数
    （正常期 1.0，移仓期/价差扩大 → 下调至 scale_min）；
  - ``apply_capacity_scale``：容量/交易性分 × 缩放系数（clamp [0,5]）。

接入点：`FactorQualityCard.evaluate` 新增 ``liquidity_scale`` 参数（默认 1.0 向后
兼容），capacity_score/tradability_score 计算后按缩放系数下调。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class LiquidityEnvConfig(BaseModel):
    """流动性环境缩放配置。"""

    enabled: bool = Field(default=True, description="总开关；False=恒 1.0（向后兼容）")
    capacity_scale_min: float = Field(default=0.5, description="移仓期容量/交易性分缩放下限系数")
    spread_ratio_warning: float = Field(default=1.5, description="价差比警告线（当前价差/基准价差 > 该值开始下调）")

    @classmethod
    def from_env(cls) -> "LiquidityEnvConfig":
        """从环境变量读取（FTS_LIQUIDITY_ENV_ENABLED / FTS_LIQUIDITY_SCALE_MIN 等）。"""
        import os

        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        enabled = os.getenv("FTS_LIQUIDITY_ENV_ENABLED", "1").lower() in {"1", "true", "yes"}
        return cls(
            enabled=enabled,
            capacity_scale_min=_f("FTS_LIQUIDITY_SCALE_MIN", 0.5),
            spread_ratio_warning=_f("FTS_LIQUIDITY_SPREAD_WARNING", 1.5),
        )


def liquidity_env_scale(
    snapshot: Optional[dict[str, Any]],
    config: Optional[LiquidityEnvConfig] = None,
) -> float:
    """按流动性快照计算容量缩放系数（纯函数）。

    Args:
        snapshot: 流动性快照（调用方从 liquidity_snapshot 聚合）：
            {"roll_active": bool,        # 是否处于移仓窗口（主力合约切换期）
             "spread_ratio": float}      # 当前价差 / 基准价差（>1 表示价差扩大）
            None / 关键字段缺失 → 无法判定，返回 1.0（不误伤）。
        config: 配置（None → 默认）

    Returns:
        float: 缩放系数（[scale_min, 1.0]）。移仓窗口 → scale_min；
            价差扩大 → 按超警告线比例线性下调；正常 → 1.0。
    """
    cfg = config or LiquidityEnvConfig()
    if not cfg.enabled or not isinstance(snapshot, dict):
        return 1.0
    scale = 1.0
    if snapshot.get("roll_active"):
        scale = cfg.capacity_scale_min
    sr = snapshot.get("spread_ratio")
    try:
        sr_f = float(sr)
    except (TypeError, ValueError):
        sr_f = None
    if sr_f is not None and sr_f > cfg.spread_ratio_warning:
        # 价差扩大：超过警告线后线性下调至 scale_min（按 2 倍警告线封顶）
        excess = min(1.0, (sr_f - cfg.spread_ratio_warning) / cfg.spread_ratio_warning)
        scale = min(scale, 1.0 - excess * (1.0 - cfg.capacity_scale_min))
    return round(max(cfg.capacity_scale_min, min(1.0, scale)), 4)


def apply_capacity_scale(
    score: float,
    scale: Optional[float],
    config: Optional[LiquidityEnvConfig] = None,
) -> float:
    """容量/交易性分 × 缩放系数（clamp [0,5]）。

    Args:
        score: 原始容量/交易性分（0-5）
        scale: 缩放系数（None/异常 → 不缩放）
        config: 配置（None → 默认）

    Returns:
        float: 缩放后分数（[0,5]）。
    """
    try:
        s = float(score)
    except (TypeError, ValueError):
        return float(score) if score is not None else 0.0
    try:
        sc = float(scale) if scale is not None else 1.0
    except (TypeError, ValueError):
        sc = 1.0
    cfg = config or LiquidityEnvConfig()
    sc = max(cfg.capacity_scale_min, min(1.0, sc))
    return round(max(0.0, min(5.0, s * sc)), 4)


__all__ = [
    "LiquidityEnvConfig",
    "liquidity_env_scale",
    "apply_capacity_scale",
]
