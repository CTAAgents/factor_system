"""
fts.factor_engine.fdr_discount — 跨运行累积 FDR 折扣（plans/59 OPT-02 / GAP-162）。

Bonferroni 校正只控制"单次评估批次"的家族错误率；因子跨日多次重试后"最终通过"
与"首次通过"的假阳性风险不等价——重试越多，碰巧通过的概率越高。本模块提供
discovery discount（alpha 支出折扣）：

    p_eff = min(1.0, p × discount^retries)

- retries 越多 → p_eff 越大 → 显著性门槛越严（要求 p 值更显著才通过）；
- 默认关闭（enabled=False）/ 折扣系数与显著性水平配置化 / 纯函数可单测；
- 非有限值 / 非数值输入原样返回（不判失败不崩溃）。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class FdrDiscountConfig(BaseModel):
    """跨运行 FDR 折扣配置。

    enabled=False（默认）时 ``apply_fdr_discount`` 恒返回原 p 值（向后兼容）；
    enabled=True 时按 ``p_eff = min(1.0, p × discount^retries)`` 放大 p 值。
    """

    enabled: bool = Field(default=False, description="总开关；False=回退原多重检验判定")
    discount: float = Field(default=1.25, description="每次重试的 p 值放大系数（>1 收紧）")
    alpha: float = Field(default=0.05, description="显著性水平（p_eff <= alpha 判通过）")
    max_retries_cap: int = Field(default=10, description="重试次数计入上限（防指数爆炸）")

    @classmethod
    def from_env(cls) -> "FdrDiscountConfig":
        """从环境变量读取（FTS_FDR_DISCOUNT_ENABLED / FTS_FDR_DISCOUNT / FTS_FDR_ALPHA）。"""
        import os

        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        enabled = os.getenv("FTS_FDR_DISCOUNT_ENABLED", "0").lower() in {"1", "true", "yes"}
        return cls(
            enabled=enabled,
            discount=_f("FTS_FDR_DISCOUNT", 1.25),
            alpha=_f("FTS_FDR_ALPHA", 0.05),
        )


def apply_fdr_discount(
    p_value: Optional[float],
    retries: Optional[int],
    config: Optional[FdrDiscountConfig] = None,
) -> Optional[float]:
    """按重试次数放大 p 值（纯函数）。

    Args:
        p_value: 单次批次 Bonferroni 校正后的 p 值
        retries: 该 factor_id 累计历史评估次数（首次=0）
        config: FdrDiscountConfig（None → 默认：enabled=False）

    Returns:
        float: 折扣后 p_eff（未启用 / 非数值 / 非有限 → 原值）。
    """
    cfg = config or FdrDiscountConfig()
    if not cfg.enabled or p_value is None or retries is None:
        return p_value
    try:
        p = float(p_value)
        r = int(retries)
    except (TypeError, ValueError):
        return p_value
    if p != p or p in (float("inf"), float("-inf")):  # NaN/inf 原样返回
        return p_value
    r = max(0, min(r, cfg.max_retries_cap))
    return min(1.0, p * (cfg.discount ** r))


def fdr_passed(
    p_value: Optional[float],
    retries: Optional[int],
    config: Optional[FdrDiscountConfig] = None,
) -> bool:
    """跨运行折扣后的显著性判定（纯函数）。

    Args:
        p_value: Bonferroni 校正后 p 值
        retries: 累计历史评估次数
        config: FdrDiscountConfig（None → 默认）

    Returns:
        bool: p_eff <= alpha 判通过；无 p 值 → False（保守不通过）。
    """
    cfg = config or FdrDiscountConfig()
    p_eff = apply_fdr_discount(p_value, retries, cfg)
    if p_eff is None:
        return False
    return bool(p_eff <= cfg.alpha)


__all__ = ["FdrDiscountConfig", "apply_fdr_discount", "fdr_passed"]
