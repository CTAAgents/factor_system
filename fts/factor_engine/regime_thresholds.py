"""
fts.factor_engine.regime_thresholds — 因子评审质检门槛 Regime 条件化（plans/59 OPT-01 / GAP-161）。

把评审质检的静态门槛（IC / Sharpe / IR / 衰减率）按市场制度分层调整：
    - regime 归一：bull / bear → trend；oscillate / high_vol / low_vol 原样；
    - 乘数表配置化（Pydantic），默认乘数 1.0 = 恒等（不改变现有行为）；
    - enabled 总开关，默认 False（向后兼容：未启用 / 无 regime 上下文时返回原值）。

纯函数 / 零依赖 regime 检测（由调用方传入 regime 标签）/ 不判失败不崩溃。

接入点（plans/59 OPT-01 方案）：
    - `AutoReviewPolicy.classify`（factor_inspector.py）：min_ic / min_sharpe
    - `ir_thresholds.factor_ir_threshold`：min_ir
    - `qa/monthly_check.py` M2：min_ir（ir_gate）
    - `qa/quarterly_check.py` F5：门槛调整告警（regime_change）

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# regime 归一后支持的键（bull/bear 归并为 trend）
REGIME_KEYS: tuple[str, ...] = ("trend", "oscillate", "high_vol", "low_vol")

# 受支持的乘数键（对应评审质检门槛维度）
MULT_KEYS: tuple[str, ...] = ("min_ic", "min_sharpe", "min_ir", "decay_warn")

# regime 别名 → 归一键
_ALIASES: dict[str, str] = {"bull": "trend", "bear": "trend"}


def _default_multipliers() -> dict[str, dict[str, float]]:
    """默认乘数表：全部 1.0（恒等）——未显式配置时不改变现有行为。"""
    return {key: {r: 1.0 for r in REGIME_KEYS} for key in MULT_KEYS}


class RegimeThresholdConfig(BaseModel):
    """Regime 条件化门槛配置。

    enabled=False（默认）时 ``apply_regime_multiplier`` 恒返回原值（向后兼容）；
    enabled=True 且传入有效 regime 时按乘数调整门槛。

    建议值（供启用时参考，不硬编码生效）：
        - oscillate（震荡市）：min_ic ×1.5 / min_sharpe ×1.5（噪声多，更挑剔）
        - high_vol（高波动）：min_sharpe ×1.5（稳定性要求更高）
        - trend（趋势市）/ low_vol：1.0（基准）
    """

    enabled: bool = Field(default=False, description="总开关；False=回退静态门槛")
    multipliers: dict[str, dict[str, float]] = Field(
        default_factory=_default_multipliers,
        description="{min_ic|min_sharpe|min_ir|decay_warn: {regime: 乘数}}",
    )

    @classmethod
    def from_env(cls) -> "RegimeThresholdConfig":
        """从环境变量读取 enabled（FTS_REGIME_THRESHOLDS_ENABLED，默认 false）。"""
        import os

        return cls(enabled=os.getenv("FTS_REGIME_THRESHOLDS_ENABLED", "0").lower() in {"1", "true", "yes"})


def normalize_regime(regime: Any) -> Optional[str]:
    """regime 标签归一：bull/bear → trend；其余小写原样；未知返回 None。

    Args:
        regime: 任意 regime 标签（str / None / 其他）

    Returns:
        str: 归一后的 regime 键（REGIME_KEYS 之一）；无法归一返回 None。
    """
    if not regime:
        return None
    r = str(regime).strip().lower()
    r = _ALIASES.get(r, r)
    return r if r in REGIME_KEYS else None


def apply_regime_multiplier(
    base: float,
    regime: Any,
    key: str,
    config: Optional[RegimeThresholdConfig] = None,
) -> float:
    """按 regime 乘数调整门槛基值（纯函数）。

    Args:
        base: 静态门槛基值（如 0.02 的 min_ic）
        regime: 当前市场制度标签（None / 未知 → 返回原值）
        key: 乘数键（MULT_KEYS 之一）
        config: RegimeThresholdConfig（None → 默认：enabled=False）

    Returns:
        float: 调整后门槛（未启用 / 无有效 regime / 未知 key → 原值）。
    """
    cfg = config or RegimeThresholdConfig()
    if not cfg.enabled:
        return float(base)
    r = normalize_regime(regime)
    if r is None or key not in cfg.multipliers:
        return float(base)
    mult = cfg.multipliers[key].get(r, 1.0)
    return float(base) * mult


__all__ = [
    "REGIME_KEYS",
    "MULT_KEYS",
    "RegimeThresholdConfig",
    "normalize_regime",
    "apply_regime_multiplier",
]
