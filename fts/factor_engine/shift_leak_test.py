"""
fts.factor_engine.shift_leak_test — 因子 Shift 错位泄漏校验（CTA 手册阶段2）。

对照《期货CTA多因子策略标准化作业手册》阶段2 Checkpoint:
    「全因子做未来函数检测（Shift错位校验）」

核心思想（IC 延迟衰减曲线）:
    将因子信号依次滞后 0..max_shift 天，与同一 forward_returns 计算 IC 序列。
    正常因子：信号滞后后 IC 单调衰减（当期信号预测下一期收益，过期信息预测力弱）；
    未来函数泄漏：滞后 1 期 IC 不衰减甚至增强（信号实际携带未来信息，错位后仍"有效"）。

判定规则:
    passed = 任一滞后 k 期 IC 的绝对值未超过「原始 IC × threshold」且方向同向增强
    （默认 threshold=0.9：滞后 1 期 IC 达到原始 90% 即视为泄漏嫌疑）

设计约束:
    - 纯函数 / NaN 兜底 / 固定阈值可配
    - 零未来函数：仅用已观测样本的滞后重排，不引入新数据

版本: v1.0.0
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)


def _corr(x: np.ndarray, y: np.ndarray, method: str = "spearman") -> float:
    """带 NaN 兜底的相关系数（样本不足/常数输入返回 0.0）。"""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = ~(np.isnan(x) | np.isnan(y))
    xv, yv = x[valid], y[valid]
    if len(xv) < 5 or np.std(xv) < 1e-12 or np.std(yv) < 1e-12:
        return 0.0
    if method == "pearson":
        corr, _ = sp_stats.pearsonr(xv, yv)
    else:
        corr, _ = sp_stats.spearmanr(xv, yv)
    return float(corr) if not np.isnan(corr) else 0.0


def shift_leak_test(
    signal: np.ndarray | pd.Series,
    forward_returns: np.ndarray | pd.Series,
    max_shift: int = 3,
    threshold: float = 0.9,
    min_ic: float = 0.1,
    method: str = "spearman",
) -> dict:
    """Shift 错位泄漏校验。

    Args:
        signal: 因子信号（与 forward_returns 对齐）
        forward_returns: 未来收益标签
        max_shift: 最大滞后天数（默认 3）
        threshold: 泄漏判定阈值（滞后 IC 达到原始 IC 的该比例即嫌疑，默认 0.9）
        min_ic: 原始 IC 低于该绝对值时不判泄漏（无显著预测力，比率判定不可靠，默认 0.1）
        method: 相关性方法（"spearman" / "pearson"）

    Returns:
        dict: {
            ic0: 原始 IC,
            ic_by_shift: {滞后天数: IC},
            leak_shifts: 泄漏嫌疑滞后天数列表,
            passed: 是否通过,
            max_decay_ratio: 最大 IC 衰减率（滞后/原始绝对值比）,
        }
    """
    sig = np.asarray(signal, dtype=float)
    ret = np.asarray(forward_returns, dtype=float)
    result: dict = {
        "ic0": 0.0,
        "ic_by_shift": {},
        "leak_shifts": [],
        "passed": True,
        "max_decay_ratio": 0.0,
    }
    if len(sig) != len(ret) or len(sig) < 5:
        return result

    ic0 = _corr(sig, ret, method)
    result["ic0"] = ic0
    if abs(ic0) < min_ic:
        return result  # 无显著预测能力，泄漏判定无意义

    leak_shifts: list[int] = []
    max_ratio = 0.0
    for k in range(1, max_shift + 1):
        if len(sig) <= k:
            break
        ic_k = _corr(sig[:-k], ret[k:], method)
        result["ic_by_shift"][k] = ic_k
        ratio = abs(ic_k) / abs(ic0)
        max_ratio = max(max_ratio, ratio)
        # 滞后 IC 与原始同向且强度达到阈值 → 泄漏嫌疑
        if ic_k * ic0 > 0 and ratio >= threshold:
            leak_shifts.append(k)

    result["leak_shifts"] = leak_shifts
    result["max_decay_ratio"] = max_ratio
    result["passed"] = len(leak_shifts) == 0
    return result


__all__ = ["shift_leak_test"]
