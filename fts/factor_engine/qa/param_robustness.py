"""
fts.factor_engine.qa.param_robustness — 参数稳健区动态化检测（plans/59 OPT-07 / GAP-167）。

Q3 入库质检只要求 params 非空（≥3 组档位由 WalkForward 间接保证）、F3 季度复检
只查离散档位偏移——参数平面"连续可行域"未检测，窄峰参数因子（仅最优档位附近
有效、邻域剧烈衰减）可蒙混过关。

本模块提供纯函数：
  - ``param_perturbations``：数值参数邻域扰动组合生成（默认 ±20% 三档网格）；
  - ``compute_param_robustness``：按网格点绩效衰减占比计算鲁棒区（衰减 ≤ 阈值
    的网格点占比）；
  - ``robust_ratio_verdict``：鲁棒区占比 ≥ min_robust_ratio → robust，否则 fragile。

接入点：
  - Q3 入库质检（`qa/pre_entry.py` 调用方 `build_qa_review`）：评估链产出
    ``param_robustness`` 时按其 verdict 判定，缺失回退 bool(params)（向后兼容）；
  - F3 季度复检（`qa/quarterly_check.py`）：新增 param_robust_ratio 维度；
  - 月度复检（`qa/monthly_check.py`）：新增 param_robust_ratio 附加预警。

版本: v1.0.0
"""

from __future__ import annotations

import itertools
import logging
from typing import Any, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

VERDICT_ROBUST = "robust"
VERDICT_FRAGILE = "fragile"


class ParamRobustnessConfig(BaseModel):
    """参数稳健区检测配置。"""

    enabled: bool = Field(default=True, description="总开关；False=回退旧行为")
    neighborhood_ratio: float = Field(default=0.20, description="数值参数邻域比例（±20%）")
    perf_decay_threshold: float = Field(default=0.30, description="网格点绩效衰减阈值（> 阈值视为失稳点）")
    min_robust_ratio: float = Field(default=0.60, description="鲁棒区占比合格线（≥ 该值 → robust）")
    max_samples: int = Field(default=27, description="网格采样上限（防参数数多时组合爆炸）")

    @classmethod
    def from_env(cls) -> "ParamRobustnessConfig":
        """从环境变量读取（FTS_PARAM_ROBUST_ENABLED / FTS_PARAM_ROBUST_MIN_RATIO 等）。"""
        import os

        def _f(key: str, default: float) -> float:
            try:
                return float(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        def _i(key: str, default: int) -> int:
            try:
                return int(os.getenv(key, default))
            except (TypeError, ValueError):
                return default

        enabled = os.getenv("FTS_PARAM_ROBUST_ENABLED", "1").lower() in {"1", "true", "yes"}
        return cls(
            enabled=enabled,
            neighborhood_ratio=_f("FTS_PARAM_ROBUST_NEIGHBORHOOD", 0.20),
            perf_decay_threshold=_f("FTS_PARAM_ROBUST_DECAY", 0.30),
            min_robust_ratio=_f("FTS_PARAM_ROBUST_MIN_RATIO", 0.60),
            max_samples=_i("FTS_PARAM_ROBUST_MAX_SAMPLES", 27),
        )


def _is_numeric(v: Any) -> bool:
    """数值（bool 除外，int/float 且有限）。"""
    if isinstance(v, bool):
        return False
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def param_perturbations(
    params: dict[str, Any],
    config: Optional[ParamRobustnessConfig] = None,
) -> list[dict[str, Any]]:
    """生成数值参数邻域扰动组合（笛卡尔积三档网格）。

    仅扰动数值参数（int/float）；布尔/字符串/列表参数原样保留。
    组合数超 max_samples 时先按参数序截断采样（保留首个扰动参数全网格）。

    Args:
        params: 原始参数 dict
        config: 配置（None → 默认）

    Returns:
        list[dict]: 扰动参数组合（含原始参数自身）。
    """
    cfg = config or ParamRobustnessConfig()
    if not isinstance(params, dict) or not params:
        return []
    base = {k: v for k, v in params.items()}
    numeric_keys = [k for k, v in params.items() if _is_numeric(v)]
    if not numeric_keys:
        return [base]
    grid: list[list[tuple[str, Any]]] = []
    for k in numeric_keys:
        v = float(params[k])
        low, mid, high = v * (1 - cfg.neighborhood_ratio), v, v * (1 + cfg.neighborhood_ratio)
        grid.append([(k, low), (k, mid), (k, high)])
    samples: list[dict[str, Any]] = []
    for combo in itertools.product(*grid):
        p = dict(base)
        for k, val in combo:
            p[k] = val
        samples.append(p)
        if len(samples) >= cfg.max_samples:
            break
    # 保证原始参数在集合中（截断采样可能未包含）
    if base not in samples:
        samples.append(base)
    return samples


def compute_param_robustness(
    base_metric: float,
    grid_metrics: list[dict[str, Any]],
    config: Optional[ParamRobustnessConfig] = None,
) -> dict[str, Any]:
    """按网格点绩效衰减占比计算鲁棒区（纯函数）。

    Args:
        base_metric: 原始参数下的基准绩效指标（如 IC / Sharpe，正值方向）
        grid_metrics: [{params: {...}, metric: float}]（含原始参数点）——调用方
            用扰动参数评估后产出；metric 为同口径绩效指标
        config: 配置（None → 默认）

    Returns:
        dict: {robust_ratio, robust_count, total, max_decay, verdict, detail}
            robust_ratio = 衰减 ≤ perf_decay_threshold 的网格点占比
    """
    cfg = config or ParamRobustnessConfig()
    if not grid_metrics or base_metric is None or base_metric == 0:
        return {
            "robust_ratio": 0.0,
            "robust_count": 0,
            "total": len(grid_metrics),
            "max_decay": None,
            "verdict": VERDICT_FRAGILE,
            "detail": "网格绩效数据缺失或基准绩效为零，无法判定（按 fragile 保守）",
        }
    base = abs(float(base_metric))
    robust = 0
    max_decay = 0.0
    for g in grid_metrics:
        m = g.get("metric")
        try:
            mf = float(m)
        except (TypeError, ValueError):
            continue
        if mf != mf:
            continue
        decay = max(0.0, (base - abs(mf)) / base)
        max_decay = max(max_decay, decay)
        if decay <= cfg.perf_decay_threshold + 1e-9:  # 浮点容差
            robust += 1
    total = len(grid_metrics)
    ratio = robust / total if total else 0.0
    verdict = robust_ratio_verdict(ratio, cfg)
    return {
        "robust_ratio": round(ratio, 4),
        "robust_count": robust,
        "total": total,
        "max_decay": round(max_decay, 4),
        "verdict": verdict,
        "detail": f"鲁棒区占比 {robust}/{total}={ratio:.1%}（阈值 {cfg.min_robust_ratio:.0%}，衰减线 {cfg.perf_decay_threshold:.0%}）",
    }


def robust_ratio_verdict(
    robust_ratio: float,
    config: Optional[ParamRobustnessConfig] = None,
) -> str:
    """鲁棒区占比判定（纯函数）。

    Args:
        robust_ratio: compute_param_robustness 输出的 robust_ratio
        config: 配置（None → 默认）

    Returns:
        str: "robust" | "fragile"
    """
    cfg = config or ParamRobustnessConfig()
    if not cfg.enabled:
        return VERDICT_ROBUST  # 关闭时默认放行（向后兼容）
    return VERDICT_ROBUST if robust_ratio >= cfg.min_robust_ratio else VERDICT_FRAGILE


__all__ = [
    "VERDICT_ROBUST",
    "VERDICT_FRAGILE",
    "ParamRobustnessConfig",
    "param_perturbations",
    "compute_param_robustness",
    "robust_ratio_verdict",
]
