"""
fts.factor_engine.qa.review_sla — 人审 SLA 自动降级（plans/59 OPT-06 / GAP-166）。

needs_human / pending 因子依赖人工审查且无超时路径，待审因子可长期滞留、
L3 池供给受人工节奏制约。本模块提供 SLA 自动处置（全程机审，无需人工介入）：

  - 超 sla_days（默认 5 交易日）未完成审查 → warned：降权 50%（标记
    ``metadata.review_sla = {status: warned, weight_scale: 0.5}``）；
  - 超 escalation_days（默认 10 交易日 = 2×sla_days）→ escalated：
    退回 L2 冷却池（status=degraded，走 evolution_seeds 30 日冷却通道）
    + factor_reviews 写 rejected（comment=SLA 超时退冷却池，全程留痕）。

接入点：`FactorReviewWorkflow.enforce_review_sla`（扫描 pending 因子按
``factor_catalog.created_at`` 判定处置）。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

PHASE_ACTIVE = "active"  # 未超时
PHASE_WARNED = "warned"  # 超 sla_days → 降权 50%
PHASE_ESCALATED = "escalated"  # 超 escalation_days → 退冷却池

# metadata.review_sla 标记键
META_KEY = "review_sla"


class ReviewSlaConfig(BaseModel):
    """人审 SLA 配置。"""

    enabled: bool = Field(default=True, description="总开关；False=不处置（向后兼容）")
    sla_days: int = Field(default=5, description="超 N 天未审 → 降权 50%")
    escalation_days: int = Field(default=10, description="超 2N 天未审 → 退 L2 冷却池")
    weight_scale: float = Field(default=0.5, description="warned 阶段权重缩放系数")

    @classmethod
    def from_env(cls) -> "ReviewSlaConfig":
        """从环境变量读取（FTS_REVIEW_SLA_ENABLED / FTS_REVIEW_SLA_DAYS 等）。"""
        import os

        def _i(key: str, default: int) -> int:
            try:
                return int(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        enabled = os.getenv("FTS_REVIEW_SLA_ENABLED", "1").lower() in {"1", "true", "yes"}
        return cls(
            enabled=enabled,
            sla_days=_i("FTS_REVIEW_SLA_DAYS", 5),
            escalation_days=_i("FTS_REVIEW_SLA_ESCALATION_DAYS", 10),
            weight_scale=_f("FTS_REVIEW_SLA_WEIGHT_SCALE", 0.5),
        )


def _to_date(v: Any) -> Optional[date]:
    """解析日期/ISO 字符串为 date；失败返回 None。"""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v).date()
        except ValueError:
            return None
    return None


def sla_phase(
    created_at: Any,
    today: Any = None,
    config: Optional[ReviewSlaConfig] = None,
) -> str:
    """SLA 阶段判定（纯函数）。

    Args:
        created_at: 因子创建/进入待审时间（datetime/date/ISO 字符串）
        today: 当前日期（None → date.today()）
        config: 配置（None → 默认）

    Returns:
        str: "active" | "warned" | "escalated"（时间缺失 → active，不误伤）。
    """
    cfg = config or ReviewSlaConfig()
    c_date = _to_date(created_at)
    if c_date is None:
        return PHASE_ACTIVE
    today = _to_date(today) or date.today()
    elapsed = (today - c_date).days
    if elapsed >= cfg.escalation_days:
        return PHASE_ESCALATED
    if elapsed >= cfg.sla_days:
        return PHASE_WARNED
    return PHASE_ACTIVE


def sla_marker(phase: str, config: Optional[ReviewSlaConfig] = None) -> dict[str, Any]:
    """构造 SLA 处置标记（写入 metadata.review_sla）。"""
    cfg = config or ReviewSlaConfig()
    return {
        "status": phase,
        "at": str(date.today()),
        "weight_scale": cfg.weight_scale if phase == PHASE_WARNED else 0.0,
    }


__all__ = [
    "PHASE_ACTIVE",
    "PHASE_WARNED",
    "PHASE_ESCALATED",
    "META_KEY",
    "ReviewSlaConfig",
    "sla_phase",
    "sla_marker",
]
