"""
fts.factor_engine.qa.monthly_check — 月度滚动复检 M1-M5（CTA 手册 6.5）。

对照《期货CTA多因子策略标准化作业手册》6.5 月度定期复检:
    M1 滚动60日IC均值: 最近60个交易日 IC 均值，健康: 同向且 |IC| > 0.02
    M2 滚动60日IR:    最近60日 IC均值/IC标准差，健康: 达到分类门槛×80%
    M3 IC衰减率:      (样本外IC - 训练期IC) / 训练期IC，健康: 衰减 < 30%
    M4 当月分层收益:  Top1-Bottom1 多空收益 > 0，预警: 连续 2 月 < 0
    M5 因子-持仓一致性: 因子排名 vs 实际持仓排名偏差 < 10%

处置路径（手册 6.5）:
    0 项预警 → 正常服役（权重不变）
    1 项预警 → 降权至 50%，进入观察期
    2 项预警 → 降权至 30%，进入观察期
    3 项及以上 → 权重归零，暂停服役，进入复审队列
    连续 3 月预警 → 触发退役判定（§6.7）

纯函数 / NaN 兜底（样本不足或数据缺失时对应项标记"无法判定"不误判预警）。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# M1: 健康 IC 下限（日频）
IC_MIN = 0.02
# M2: 健康 IR = 分类门槛 × 80%
IR_HEALTH_RATIO = 0.8
# M3: IC 衰减预警阈值
IC_DECAY_WARN = 0.30
# M5: 排名偏差预警阈值
RANK_DEV_WARN = 0.10
# plans/59 OPT-07：参数鲁棒区占比合格线（对齐 ParamRobustnessConfig 默认）
PARAM_ROBUST_MIN_RATIO = 0.60

MONTHLY_INDICATORS: list[str] = ["M1", "M2", "M3", "M4", "M5"]


def _ic60(ic_series: np.ndarray) -> tuple[Optional[float], Optional[float]]:
    """取最近 60 日 IC 均值/标准差（样本不足返回 None）。"""
    vals = np.asarray(ic_series, dtype=float)
    vals = vals[np.isfinite(vals)]
    if len(vals) < 20:
        return None, None
    tail = vals[-60:]
    mean = float(np.mean(tail))
    std = float(np.std(tail))
    return mean, std


def monthly_recheck(
    ic_series: np.ndarray,
    oos_baseline_ic: float,
    ir_gate: float,
    month_layered_return: Optional[float] = None,
    prev_month_layered_negative: bool = False,
    rank_deviation: Optional[float] = None,
    prev_warn_months: int = 0,
    regime: Optional[str] = None,
    param_robust_ratio: Optional[float] = None,
) -> dict:
    """月度滚动复检（手册 6.5 M1-M5 + 分级处置路径）。

    plans/59 OPT-01（GAP-161）：传入 regime 时 M2 的 IR 健康下限按乘数调整
    （未启用/无 regime → 原值，向后兼容）。

    Args:
        ic_series: IC 序列（含近 60 日数据）
        oos_baseline_ic: 样本外训练期 IC 均值（M3 对照基准）
        ir_gate: 该因子分类 IR 门槛（量价 0.3/基本面 0.4/期限结构 0.35）
        month_layered_return: 当月 Top1-Bottom1 多空收益（None 无法判定）
        prev_month_layered_negative: 上月分层收益是否 < 0（M4 连续两月判定）
        rank_deviation: 因子排名 vs 实际持仓排名偏差比例（0-1，None 无法判定）
        prev_warn_months: 上月累计连续预警月数
        regime: 当前市场制度（可选；调整 M2 IR 健康下限）
        param_robust_ratio: 参数鲁棒区占比（plans/59 OPT-07，可选；< 0.60 附加预警，
            不并入 M1-M5 warn_count，避免改变既有处置梯度）

    Returns:
        dict: {
            indicators: {M1..M5: {passed, warned, detail}},
            warn_count, prev_warn_months,
            action: "normal"|"observe_50"|"observe_30"|"suspend"|"retire_review",
            weight_scale: 1.0|0.5|0.3|0.0,
            reason: str,
        }
    """
    mean, std = _ic60(ic_series)
    ind: dict[str, dict] = {}

    # M1 滚动 60 日 IC 均值
    if mean is None:
        ind["M1"] = {"passed": False, "warned": False, "detail": "IC 样本不足，无法判定"}
    else:
        warn = (
            abs(mean) <= IC_MIN or mean * oos_baseline_ic < 0 if abs(oos_baseline_ic) > 1e-12 else abs(mean) <= IC_MIN
        )
        ind["M1"] = {
            "passed": not warn,
            "warned": warn,
            "detail": f"60日IC均值={mean:.4f}（健康: 同向且 |IC|>{IC_MIN}）",
        }

    # M2 滚动 60 日 IR（年化）≥ 分类门槛×80%（plans/59 OPT-01：regime 乘数调整健康下限）
    if mean is None or std is None or std <= 1e-12:
        ind["M2"] = {"passed": False, "warned": False, "detail": "IR 样本不足，无法判定"}
    else:
        ir = float(mean / std * np.sqrt(252))
        eff_ir_gate = ir_gate
        if regime:
            from fts.factor_engine.regime_thresholds import apply_regime_multiplier

            eff_ir_gate = apply_regime_multiplier(ir_gate, regime, "min_ir")
        health_floor = eff_ir_gate * IR_HEALTH_RATIO
        warn = bool(ir < health_floor)
        ind["M2"] = {"passed": not warn, "warned": warn, "detail": f"60日IR={ir:.2f}（健康下限={health_floor:.2f}）"}

    # M3 IC 衰减率 = (样本外IC - 训练期IC)/训练期IC，衰减 ≥ 30% 预警
    if mean is None or abs(oos_baseline_ic) <= 1e-12:
        ind["M3"] = {"passed": False, "warned": False, "detail": "衰减基准缺失，无法判定"}
    else:
        decay = (oos_baseline_ic - mean) / oos_baseline_ic
        warn = decay >= IC_DECAY_WARN
        ind["M3"] = {
            "passed": not warn,
            "warned": warn,
            "detail": f"IC衰减率={decay:.1%}（预警线≥{IC_DECAY_WARN:.0%}）",
        }

    # M4 当月分层收益 > 0；连续 2 月 < 0 预警
    if month_layered_return is None:
        ind["M4"] = {"passed": False, "warned": False, "detail": "当月分层收益缺失，无法判定"}
    else:
        warn = month_layered_return < 0 and prev_month_layered_negative
        ind["M4"] = {
            "passed": not warn,
            "warned": warn,
            "detail": f"当月分层收益={month_layered_return:.4f}（连续2月<0预警）",
        }

    # M5 因子-持仓一致性偏差 < 10%
    if rank_deviation is None:
        ind["M5"] = {"passed": False, "warned": False, "detail": "排名偏差缺失，无法判定"}
    else:
        warn = rank_deviation >= RANK_DEV_WARN
        ind["M5"] = {
            "passed": not warn,
            "warned": warn,
            "detail": f"排名偏差={rank_deviation:.1%}（预警线≥{RANK_DEV_WARN:.0%}）",
        }

    warn_count = sum(1 for v in ind.values() if v["warned"])
    consecutive = prev_warn_months + 1 if warn_count > 0 else 0

    if consecutive >= 3:
        action = "retire_review"
        weight_scale = 0.0
        reason = "连续 3 月预警，触发退役判定（§6.7）"
    elif warn_count >= 3:
        action = "suspend"
        weight_scale = 0.0
        reason = "3 项及以上预警，权重归零，暂停服役，进入复审队列"
    elif warn_count == 2:
        action = "observe_30"
        weight_scale = 0.30
        reason = "2 项预警，降权至 30%，进入观察期"
    elif warn_count == 1:
        action = "observe_50"
        weight_scale = 0.50
        reason = "1 项预警，降权至 50%，进入观察期"
    else:
        action = "normal"
        weight_scale = 1.0
        reason = "全部健康，继续正常服役"

    # plans/59 OPT-07：参数鲁棒区附加预警（< 0.60 窄峰参数风险；不并入 warn_count，
    # 避免改变既有 M1-M5 处置梯度）
    if param_robust_ratio is None:
        param_robust: dict = {"passed": True, "warned": False, "detail": "参数鲁棒区数据缺失，无法判定"}
    else:
        pr_warn = param_robust_ratio < PARAM_ROBUST_MIN_RATIO
        param_robust = {
            "passed": not pr_warn,
            "warned": pr_warn,
            "detail": f"参数鲁棒区占比={param_robust_ratio:.2f}（< {PARAM_ROBUST_MIN_RATIO:.0%} 窄峰参数预警）",
        }

    return {
        "indicators": ind,
        "warn_count": warn_count,
        "prev_warn_months": prev_warn_months,
        "consecutive_warn_months": consecutive,
        "param_robust": param_robust,
        "action": action,
        "weight_scale": float(weight_scale),
        "reason": reason,
    }


__all__ = [
    "MONTHLY_INDICATORS",
    "IC_MIN",
    "IR_HEALTH_RATIO",
    "IC_DECAY_WARN",
    "RANK_DEV_WARN",
    "monthly_recheck",
]
