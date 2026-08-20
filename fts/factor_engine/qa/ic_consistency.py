"""
fts.factor_engine.qa.ic_consistency — IC 口径一致性校验（plans/59 OPT-04 / GAP-164）。

IC 类信号在多处被消费（Verifier / B.4 高IC / 质量评分卡 / Q1-Q10 入库质检），
它们均源自评估链 `level_1_backtest.ic`，但评审环节还会接触新评估 IC 与主表
`factor_catalog.ic`（晋升落库值）。一旦任一处口径漂移（评估重算未同步主表、
不同窗口/复权口径混用等），评审结论会静默不一致。

本模块提供纯函数校验：
  - 以权威口径（默认 ``catalog``，因子主表 IC）为基准；
  - 其余来源与基准偏差 > tolerance（默认 0.005）→ 标记该来源不一致；
  - 有效来源 < 2 或权威缺失 → 无法判定，不误报（consistent=True）。

接入点：`FactorReviewWorkflow.review_inplace`（评审就地质检时校验，
口径漂移 → 转人审，宁缺毋滥）。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# 默认容差（绝对值，IC 量级 0.01~0.1 内 0.005 属显著漂移）
DEFAULT_TOLERANCE = 0.005


def check_ic_consistency(
    ic_values: dict[str, Optional[float]],
    tolerance: float = DEFAULT_TOLERANCE,
    authoritative: str = "catalog",
) -> dict:
    """IC 多来源口径一致性校验（纯函数）。

    Args:
        ic_values: {来源名: IC}（None/非数值自动剔除；如
            {"catalog": 0.032, "evaluation": 0.031, "audit": None}）
        tolerance: 偏差容差（绝对值）
        authoritative: 权威口径来源名（默认 catalog，因子主表）

    Returns:
        dict: {
            consistent: bool,
            n_valid: int,             # 参与校验的有效来源数
            max_deviation: float,     # 最大 |IC - 权威IC|
            min_ic: float|None,
            max_ic: float|None,
            authoritative_ic: float|None,
            inconsistent_sources: list[str],   # 偏离权威 > tolerance 的来源
            detail: str,
        }
    """
    valid: dict[str, float] = {}
    for k, v in (ic_values or {}).items():
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != f or f in (float("inf"), float("-inf")):  # NaN/inf 剔除
            continue
        valid[k] = f

    n = len(valid)
    base = valid.get(authoritative)
    if base is None or n < 2:
        return {
            "consistent": True,
            "n_valid": n,
            "max_deviation": 0.0,
            "min_ic": min(valid.values()) if valid else None,
            "max_ic": max(valid.values()) if valid else None,
            "authoritative_ic": base,
            "inconsistent_sources": [],
            "detail": "有效来源不足或权威口径缺失，无法判定（不误报）",
        }

    inconsistent: list[str] = []
    max_dev = 0.0
    for k, v in valid.items():
        if k == authoritative:
            continue
        dev = abs(v - base)
        max_dev = max(max_dev, dev)
        if dev > tolerance:
            inconsistent.append(k)

    consistent = not inconsistent
    detail = (
        "IC 口径一致"
        if consistent
        else f"IC 口径漂移: {authoritative}={base:.4f} vs "
        + ", ".join(f"{k}={valid[k]:.4f}" for k in inconsistent)
        + f"（容差 {tolerance}）"
    )
    return {
        "consistent": consistent,
        "n_valid": n,
        "max_deviation": float(max_dev),
        "min_ic": min(valid.values()),
        "max_ic": max(valid.values()),
        "authoritative_ic": base,
        "inconsistent_sources": inconsistent,
        "detail": detail,
    }


__all__ = ["DEFAULT_TOLERANCE", "check_ic_consistency"]
