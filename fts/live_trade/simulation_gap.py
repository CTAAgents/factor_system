"""
fts.live_trade.simulation_gap — 仿真 vs 回测净值偏差对比（CTA 手册阶段10）。

对照《期货CTA多因子策略标准化作业手册》阶段10「仿真环境联调」Checkpoint:
    - 仿真净值与回测净值误差控制在 ±5% 以内

设计约束:
    - 纯函数 / 零未来函数 / 曲线长度不足或重叠过少 → 不判通过不崩溃
    - 两条曲线可来自不同初始资金，统一归一化到首日净值 1.0 后比较

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Iterable, Mapping

import numpy as np

logger = logging.getLogger(__name__)

# 手册阶段10 Checkpoint：仿真与回测净值误差上限 ±5%
DEFAULT_MAX_GAP = 0.05
# 有效重叠期最少样本数，少于该值不判通过
MIN_OVERLAP = 5

Curve = Mapping[str, float] | Iterable[tuple[str, float]]


def _to_series(curve: Curve) -> dict[str, float]:
    """统一解析曲线输入为 {date: equity}。"""
    if isinstance(curve, Mapping):
        return {str(k): float(v) for k, v in curve.items()}
    out: dict[str, float] = {}
    for date, value in curve:
        out[str(date)] = float(value)
    return out


def _normalized_ret(curve: dict[str, float]) -> tuple[list[str], np.ndarray]:
    """按日期排序并归一化到首日净值 1.0，返回 (dates, 累计收益率数组)。"""
    dates = sorted(curve.keys())
    if len(dates) == 0:
        return [], np.zeros(0, dtype=float)
    base = curve[dates[0]]
    ret = np.array(
        [curve[d] / base - 1.0 if abs(base) > 1e-12 else 0.0 for d in dates],
        dtype=float,
    )
    return dates, ret


def simulation_backtest_gap_check(
    sim_curve: Curve,
    bt_curve: Curve,
    max_gap: float = DEFAULT_MAX_GAP,
    min_overlap: int = MIN_OVERLAP,
) -> dict:
    """仿真净值与回测净值偏差校验（手册阶段10：±5% 以内）。

    Args:
        sim_curve: 仿真净值曲线（date → equity，或 [(date, equity)]）
        bt_curve: 回测净值曲线（date → equity，或 [(date, equity)]）
        max_gap: 最大允许偏差（默认 0.05，即 ±5%）
        min_overlap: 有效重叠期最少样本数（默认 5）

    Returns:
        dict: {
            overlap_n, max_gap, mean_gap, final_gap, passed,
            detail: [{date, sim_ret, bt_ret, gap}],
        }
        重叠样本不足 → passed=False 且不崩溃。
    """
    sim = _normalized_ret(_to_series(sim_curve))
    bt = _normalized_ret(_to_series(bt_curve))
    sim_map = dict(zip(sim[0], sim[1]))
    bt_map = dict(zip(bt[0], bt[1]))
    common = [d for d in sim[0] if d in bt_map]

    result: dict = {
        "overlap_n": len(common),
        "max_gap": None,
        "mean_gap": None,
        "final_gap": None,
        "passed": False,
        "detail": [],
    }
    if len(common) < min_overlap:
        return result

    gaps = [abs(sim_map[d] - bt_map[d]) for d in common]
    detail = [
        {"date": d, "sim_ret": float(sim_map[d]), "bt_ret": float(bt_map[d]), "gap": float(sim_map[d] - bt_map[d])}
        for d in common
    ]
    result["max_gap"] = float(max(gaps))
    result["mean_gap"] = float(np.mean(gaps))
    result["final_gap"] = float(gaps[-1])
    result["detail"] = detail
    result["passed"] = bool(result["max_gap"] <= max_gap)
    return result


__all__ = [
    "DEFAULT_MAX_GAP",
    "MIN_OVERLAP",
    "simulation_backtest_gap_check",
]
