"""
fts.factor_engine.stress_ic — 极端行情 IC 失效检验（CTA 手册阶段4）。

对照《期货CTA多因子策略标准化作业手册》阶段4 Checkpoint:
    「极端行情（如 2020 原油负价格、2022 俄乌扰动）下 IC 没有大幅失效」

在历史极端行情区间内分别计算因子 IC，与正常期 IC 对比：
    - 极端期 |IC| < 正常期 |IC| × decay_threshold（默认衰减超过 50%）→ 该因子在极端行情下失效
    - 符号反转（极端期 IC 与全样本方向相反且显著）→ 强失效标记

设计约束:
    - 纯函数 / NaN 兜底 / 区间无样本自动跳过（skipped 不判失败）
    - 极端区间数据不足 5 个有效样本 → skipped

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)

# 内置极端行情区间（对照手册阶段4 示例 + 现有压力场景）
STRESS_PERIODS: list[dict[str, str]] = [
    {"name": "原油负价格_2020", "start": "2020-04-01", "end": "2020-05-31"},
    {"name": "俄乌扰动_2022", "start": "2022-02-24", "end": "2022-05-31"},
    {"name": "疫情冲击_2020", "start": "2020-02-01", "end": "2020-03-31"},
]


def _corr(x: np.ndarray, y: np.ndarray) -> float:
    """带 NaN 兜底的 Spearman 相关系数。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = ~(np.isnan(x) | np.isnan(y))
    xv, yv = x[valid], y[valid]
    if len(xv) < 5 or np.std(xv) < 1e-12 or np.std(yv) < 1e-12:
        return 0.0
    corr, _ = sp_stats.spearmanr(xv, yv)
    return float(corr) if not np.isnan(corr) else 0.0


def stress_period_ic_test(
    signal: np.ndarray | pd.Series,
    forward_returns: np.ndarray | pd.Series,
    dates: np.ndarray | pd.Series,
    stress_periods: Optional[list[dict[str, str]]] = None,
    decay_threshold: float = 0.5,
) -> dict:
    """极端行情分段 IC 失效检验。

    Args:
        signal: 因子信号（与 forward_returns/dates 对齐）
        forward_returns: 未来收益标签
        dates: 日期数组（与 signal 对齐，字符串/时间戳均可）
        stress_periods: 极端行情区间 [{name, start, end}]；None 使用内置区间
        decay_threshold: 失效判定衰减阈值（默认 0.5 = 极端期 |IC| 低于正常期 50% 判失效）

    Returns:
        dict: {
            ic_full: 全样本 IC,
            ic_normal: 正常期 IC（剔除极端区间）,
            periods: [{name, ic, n, status: passed/failed/skipped}],
            failed_periods: [失效区间名],
            passed: 是否有失效区间,
        }
    """
    periods = stress_periods if stress_periods is not None else STRESS_PERIODS
    sig = np.asarray(signal, dtype=float)
    ret = np.asarray(forward_returns, dtype=float)
    dts = pd.DatetimeIndex(pd.to_datetime(np.asarray(dates)))
    result: dict = {
        "ic_full": 0.0,
        "ic_normal": 0.0,
        "periods": [],
        "failed_periods": [],
        "passed": True,
    }
    if len(sig) != len(ret) != len(dts) or len(sig) < 10:
        return result

    result["ic_full"] = _corr(sig, ret)

    # 极端期掩码合并（用于正常期 IC 计算）
    stress_mask: np.ndarray = np.zeros(len(dts), dtype=bool)
    for p in periods:
        start = pd.Timestamp(p["start"])
        end = pd.Timestamp(p["end"])
        stress_mask |= (dts >= start) & (dts <= end)
    normal_mask = ~stress_mask
    if normal_mask.sum() >= 5:
        result["ic_normal"] = _corr(sig[normal_mask], ret[normal_mask])

    failed: list[str] = []
    period_results: list[dict] = []
    for p in periods:
        start = pd.Timestamp(p["start"])
        end = pd.Timestamp(p["end"])
        mask = (dts >= start) & (dts <= end)
        n = int(mask.sum())
        if n < 5:
            period_results.append({"name": p["name"], "ic": 0.0, "n": n, "status": "skipped"})
            continue
        ic_stress = _corr(sig[mask], ret[mask])
        # 失效判定：极端期 |IC| 相对正常期衰减超过阈值 或 符号反转
        ic_norm_abs = abs(result["ic_normal"])
        status = "passed"
        if ic_norm_abs > 1e-6:
            if abs(ic_stress) < ic_norm_abs * decay_threshold:
                status = "failed"
            elif ic_stress * result["ic_full"] < 0 and abs(ic_stress) > 0.1:
                status = "failed"  # 符号反转且显著
        if status == "failed":
            failed.append(p["name"])
        period_results.append({"name": p["name"], "ic": ic_stress, "n": n, "status": status})

    result["periods"] = period_results
    result["failed_periods"] = failed
    result["passed"] = len(failed) == 0
    return result


__all__ = ["STRESS_PERIODS", "stress_period_ic_test"]
