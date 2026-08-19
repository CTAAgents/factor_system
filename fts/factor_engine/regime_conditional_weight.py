"""
fts.factor_engine.regime_conditional_weight — L3 Regime 条件化因子权重（plans/53 §B）

背景
----
业务目标（plans/53 §1.1）：在识别到的市场制度（Regime）下，通过因子进行胜率和
回报率更高的交易。本模块为组合层提供"Regime 条件化因子选择"——当前市场制度下
IC 显著为负/弱的因子权重归零或降权，避免"逆制度因子"拖累组合。

与 subchain_weight.py（plans/47 §B）同构但维度不同：
  - subchain_weight 管"因子×子链"（有效链全权重、无效链降权）；
  - 本模块管"因子×当前制度"（当前制度 effective 全权重、显著负向降权/归零）。

语义（§B1）
    - 无 regime 画像字段的因子 → scope_default="all" 全保留（不误杀）
    - regime_scope in ("all", "unknown") → m=1.0（全制度/未知不降权）
    - 当前制度在该因子画像中 effective → m=1.0
    - 当前制度画像存在但 effective=False 且 ic < -min_abs_ic（显著负向）→
      decay_mode="zero" → m=0.0；decay_mode="soft" → max(soft_min_ratio, |ic|/max_ic)
    - 其余（当前制度无画像/弱正向）→ m=1.0（护栏偏向漏标，防过度裁剪）

接入点（§B2）
    PortfolioLoop Step 2.5（regime_adaptive_weight_adjustment 之后）乘性应用 m，
    与现有 regime 倍率叠加；仅 market="energy" 且开关开启时生效。

HARNESS §契约优先：RegimeConditionalConfig / build_regime_conditioned_weights 即对外契约。

版本: v0.1.0（plans/53 §B）
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 非 effective 制度降权模式
DecayMode = str  # "zero" | "soft"


class RegimeConditionalConfig(BaseModel):
    """L3 Regime 条件化配置（config/settings.yaml → l3.regime_conditional）。"""

    enabled: bool = Field(default=False, description="灰度开关（默认关，零行为变更）")
    decay_mode: DecayMode = Field(
        default="zero",
        description="当前制度 IC 显著为负的因子：zero=归零 / soft=按 |ic| 相对缩放",
    )
    soft_min_ratio: float = Field(
        default=0.0, ge=0.0, le=1.0, description="soft 模式最低保留比例（0.0=可归零，1.0=等效全保留）"
    )
    scope_default: str = Field(default="all", description="无 regime 画像字段因子的默认处理（all=全保留）")
    min_abs_ic: float = Field(
        default=0.05, ge=0.0,
        description="显著负向 IC 幅度门槛：ic < -min_abs_ic 才触发降权（对齐 regime_profile.min_abs_ic）",
    )


def build_regime_conditioned_weights(
    factors: list[dict[str, Any]],
    current_regime: str,
    config: RegimeConditionalConfig,
) -> dict[str, float]:
    """构建当前制度下的因子权重调制系数 {factor_id: m}（plans/53 §B1）。

    Args:
        factors: 因子列表，每项含 factor_id / regime_ic_profile / regime_scope
                 （DuckDB 加载路径从 metadata 透传，L3 调用方负责注入）
        current_regime: 当前市场制度名（"bull"/"bear"/"oscillate"/"high_vol"/"low_vol"）
        config: 调制配置

    Returns:
        {factor_id: 权重调制系数 m}——m=1.0 表示不调整。
    """
    modulation: dict[str, float] = {}
    for f in factors:
        fid = f.get("factor_id", f.get("name", "?"))
        scope = f.get("regime_scope")
        prof = f.get("regime_ic_profile") or {}

        # 无画像字段 → scope_default（默认 all=全保留，不误杀）
        if not scope or not prof:
            modulation[fid] = 1.0
            continue
        # 全制度/未知 → 不降权
        if scope in ("all", "unknown"):
            modulation[fid] = 1.0
            continue

        # 部分制度：当前制度在 scope 内（effective）→ 全权重
        cur = str(current_regime)
        if isinstance(scope, list) and cur in scope:
            modulation[fid] = 1.0
            continue

        # 当前制度画像存在但 effective=False 且显著负向 → 降权/归零
        stat = prof.get(cur) or {}
        ic = stat.get("ic")
        if ic is None or not isinstance(ic, (int, float)) or abs(float(ic)) < config.min_abs_ic:
            # 无画像/弱相关：不误杀，维持现状
            modulation[fid] = 1.0
            continue
        if float(ic) >= 0:
            # 非负向（弱正向/样本不足但非反向）→ 不降权
            modulation[fid] = 1.0
            continue

        # 显著负向（ic < -min_abs_ic）
        if config.decay_mode == "soft":
            max_ic = max(
                (
                    abs(float((prof.get(r) or {}).get("ic") or 0.0))
                    for r in prof
                    if isinstance((prof.get(r) or {}).get("ic"), (int, float))
                ),
                default=1e-9,
            )
            m = max(config.soft_min_ratio, abs(float(ic)) / max_ic) if max_ic > 1e-9 else config.soft_min_ratio
            modulation[fid] = float(m)
            logger.info(
                "[regime_conditional] %s 在当前制度 %s IC=%.4f（显著负向）→ soft 降权 m=%.4f",
                fid, cur, float(ic), m,
            )
        else:  # "zero"
            modulation[fid] = 0.0
            logger.info(
                "[regime_conditional] %s 在当前制度 %s IC=%.4f（显著负向）→ 归零",
                fid, cur, float(ic),
            )
    return modulation


__all__ = [
    "RegimeConditionalConfig",
    "build_regime_conditioned_weights",
]
