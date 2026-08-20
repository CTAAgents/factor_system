"""
fts.factor_engine.qa.data_gate — 数据质量-评审联动门禁（plans/59 OPT-05 / GAP-165）。

`data_level_monitor`（缺失率/异常值/多源分歧）独立运行，因子评审不感知当期数据
质量——数据异常期做出的 approved 会污染评审历史（评审结论建立在失真数据上）。

本模块提供纯函数门禁：
  - ``assess_data_quality``：按缺失率/异常值/多源分歧分评估数据质量状态
    （ok / warning / critical）；
  - ``data_gate_decision``：critical（严重数据异常）→ 评审标记 data_degraded，
    不写 approved（延迟到下期数据正常时判定）；warning → 仅标记不阻断。

接入点：`FactorReviewWorkflow.review_inplace` / `auto_review`（评审机审时注入
``data_quality_provider`` 取当期数据质量快照；provider 缺失 → 无法判定，不阻断）。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# 数据质量状态
STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_CRITICAL = "critical"

# 门禁结论
GATE_PROCEED = "proceed"  # 数据正常，正常评审
GATE_DEFER = "defer"  # 数据严重异常，不写 approved（延迟下期）


class DataQualityGateConfig(BaseModel):
    """数据质量评审门禁配置。"""

    enabled: bool = Field(default=True, description="总开关；False=不接入（向后兼容）")
    critical_missing_ratio: float = Field(default=0.20, description="全表缺失率严重阈值（对齐 data_level_monitor）")
    warning_missing_ratio: float = Field(default=0.05, description="全表缺失率警告阈值")
    critical_outlier_ratio: float = Field(default=0.10, description="异常值占比严重阈值")
    critical_disagreement: float = Field(default=0.005, description="多源分歧严重阈值（同字段跨源偏差比例）")

    @classmethod
    def from_env(cls) -> "DataQualityGateConfig":
        """从环境变量读取（FTS_DATA_GATE_ENABLED / FTS_DATA_GATE_CRITICAL_MISSING 等）。"""
        import os

        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        enabled = os.getenv("FTS_DATA_GATE_ENABLED", "1").lower() in {"1", "true", "yes"}
        return cls(
            enabled=enabled,
            critical_missing_ratio=_f("FTS_DATA_GATE_CRITICAL_MISSING", 0.20),
            warning_missing_ratio=_f("FTS_DATA_GATE_WARNING_MISSING", 0.05),
            critical_outlier_ratio=_f("FTS_DATA_GATE_CRITICAL_OUTLIER", 0.10),
            critical_disagreement=_f("FTS_DATA_GATE_CRITICAL_DISAGREEMENT", 0.005),
        )


def assess_data_quality(
    missing_ratio: Optional[float],
    outlier_ratio: Optional[float] = None,
    source_disagreement: Optional[float] = None,
    config: Optional[DataQualityGateConfig] = None,
) -> dict[str, Any]:
    """按缺失率/异常值/多源分歧评估数据质量状态（纯函数）。

    Args:
        missing_ratio: 全表缺失率（None=不可得）
        outlier_ratio: 异常值占比（None=不可得）
        source_disagreement: 多源分歧（None=不可得）
        config: 配置（None → 默认）

    Returns:
        dict: {status, missing_ratio, outlier_ratio, source_disagreement,
               critical_hits: [str], detail}
    """
    cfg = config or DataQualityGateConfig()
    critical_hits: list[str] = []
    if missing_ratio is not None and missing_ratio > cfg.critical_missing_ratio:
        critical_hits.append(f"缺失率 {missing_ratio:.1%} > {cfg.critical_missing_ratio:.0%}")
    if outlier_ratio is not None and outlier_ratio > cfg.critical_outlier_ratio:
        critical_hits.append(f"异常值 {outlier_ratio:.1%} > {cfg.critical_outlier_ratio:.0%}")
    if source_disagreement is not None and source_disagreement > cfg.critical_disagreement:
        critical_hits.append(f"多源分歧 {source_disagreement:.3f} > {cfg.critical_disagreement}")

    if critical_hits:
        status = STATUS_CRITICAL
    elif missing_ratio is not None and missing_ratio > cfg.warning_missing_ratio:
        status = STATUS_WARNING
    else:
        status = STATUS_OK
    return {
        "status": status,
        "missing_ratio": missing_ratio,
        "outlier_ratio": outlier_ratio,
        "source_disagreement": source_disagreement,
        "critical_hits": critical_hits,
        "detail": "；".join(critical_hits) if critical_hits else f"数据质量正常（{status}）",
    }


def data_gate_decision(
    quality: dict[str, Any],
    config: Optional[DataQualityGateConfig] = None,
) -> str:
    """数据质量门禁结论（纯函数）。

    Args:
        quality: assess_data_quality 输出（或其等效 dict）
        config: 配置（None → 默认）

    Returns:
        str: "proceed"（正常评审）| "defer"（数据严重异常，不写 approved）。
    """
    cfg = config or DataQualityGateConfig()
    if not cfg.enabled:
        return GATE_PROCEED
    if (quality or {}).get("status") == STATUS_CRITICAL:
        return GATE_DEFER
    return GATE_PROCEED


__all__ = [
    "STATUS_OK",
    "STATUS_WARNING",
    "STATUS_CRITICAL",
    "GATE_PROCEED",
    "GATE_DEFER",
    "DataQualityGateConfig",
    "assess_data_quality",
    "data_gate_decision",
]
