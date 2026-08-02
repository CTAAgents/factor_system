"""
seed_data/alpha_ops.py — 因子原始操作函数库

提供 WQ 101 Alpha 和 Qlib 158 因子所需的公共操作函数。
所有函数使用 numpy 向量化实现，支持滚动窗口计算。

版本: v1.1.0
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# ─── 兼容性处理 ───────────────────────────────────────────

_EPS = 1e-10


def _to_series(x: np.ndarray) -> pd.Series:
    """确保输入为 pd.Series（用于 rolling 操作）。"""
    return x if isinstance(x, pd.Series) else pd.Series(x)


def _to_array(x: pd.Series | np.ndarray) -> np.ndarray:
    """确保输出为 np.ndarray。"""
    return x.values if isinstance(x, pd.Series) else np.asarray(x)


# ─── 截面操作 ─────────────────────────────────────────────

def rank(x: np.ndarray) -> np.ndarray:
    """截面排序分位数（0~1）。"""
    n = len(x)
    if n <= 1:
        return np.zeros_like(x)
    return np.argsort(np.argsort(x)).astype(float) / (n - 1)


def scale(x: np.ndarray, a: float = 1.0) -> np.ndarray:
    """缩放至 abs(sum) = a。"""
    s = np.sum(np.abs(x))
    if s < _EPS:
        return x
    return x * a / s


def ifelse(cond: np.ndarray, a: np.ndarray | float, b: np.ndarray | float) -> np.ndarray:
    """条件选择，等价于 np.where。"""
    return np.where(cond, a, b)


# ─── 时间序列操作 ─────────────────────────────────────────

def ts_sum(x: np.ndarray, d: int) -> np.ndarray:
    """滚动求和 d 天。"""
    return _to_array(_to_series(x).rolling(d, min_periods=1).sum())


def ts_mean(x: np.ndarray, d: int) -> np.ndarray:
    """滚动均值 d 天。"""
    return _to_array(_to_series(x).rolling(d, min_periods=1).mean())


def ts_stddev(x: np.ndarray, d: int) -> np.ndarray:
    """滚动标准差 d 天。"""
    return _to_array(_to_series(x).rolling(d, min_periods=1).std(ddof=0))


def ts_corr(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:
    """滚动相关系数 d 天。"""
    return _to_array(_to_series(x).rolling(d, min_periods=1).corr(_to_series(y)))


def ts_covariance(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:
    """滚动协方差 d 天。"""
    return _to_array(_to_series(x).rolling(d, min_periods=1).cov(_to_series(y)))


def ts_argmax(x: np.ndarray, d: int) -> np.ndarray:
    """滚动 argmax 位置 d 天。"""
    def _argmax(v):
        return np.argmax(v) if len(v) > 0 else 0
    return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_argmax, raw=True))


def ts_argmin(x: np.ndarray, d: int) -> np.ndarray:
    """滚动 argmin 位置 d 天。"""
    def _argmin(v):
        return np.argmin(v) if len(v) > 0 else 0
    return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_argmin, raw=True))


def ts_rank(x: np.ndarray, d: int) -> np.ndarray:
    """滚动分位数排序 d 天（0~1）。"""
    def _rank(v):
        if len(v) <= 1:
            return 0.5
        return np.argsort(np.argsort(v))[-1] / (len(v) - 1)
    return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_rank, raw=True))


def ts_min(x: np.ndarray, d: int) -> np.ndarray:
    """滚动最小值 d 天。"""
    return _to_array(_to_series(x).rolling(d, min_periods=1).min())


def ts_max(x: np.ndarray, d: int) -> np.ndarray:
    """滚动最大值 d 天。"""
    return _to_array(_to_series(x).rolling(d, min_periods=1).max())


def ts_product(x: np.ndarray, d: int) -> np.ndarray:
    """滚动乘积 d 天。"""
    return _to_array(_to_series(x).rolling(d, min_periods=1).apply(np.prod, raw=True))


def signed_power(x: np.ndarray, a: float) -> np.ndarray:
    """sign(x) * |x|^a。"""
    return np.sign(x) * np.abs(x) ** a


def decay_linear(x: np.ndarray, d: int) -> np.ndarray:
    """线性衰减加权平均 d 天。"""
    w = np.arange(1, d + 1, dtype=float)
    w = w / w.sum()

    def _decay(v):
        if len(v) < d:  # pragma: no cover — min_periods=d 保证不会触发
            return np.nan
        return np.sum(v[-d:] * w)
    return _to_array(_to_series(x).rolling(d, min_periods=d).apply(_decay, raw=True))


def delta(x: np.ndarray, d: int) -> np.ndarray:
    """差分: x - delay(x, d)。"""
    return x - delay(x, d)


def delay(x: np.ndarray, d: int) -> np.ndarray:
    """滞后 d 期。"""
    return _to_array(_to_series(x).shift(d))


def log(x: np.ndarray) -> np.ndarray:
    """自然对数。"""
    return np.log(np.maximum(x, _EPS))


def sign(x: np.ndarray) -> np.ndarray:
    """符号函数。"""
    return np.sign(x)


def abs_(x: np.ndarray) -> np.ndarray:
    """绝对值。"""
    return np.abs(x)


def neg(x: np.ndarray) -> np.ndarray:
    """取负。"""
    return -x


# ─── 高级操作 ─────────────────────────────────────────────

def highday(x: np.ndarray, d: int) -> np.ndarray:
    """距离 d 天最高价的天数。"""
    def _highest_day(v):
        if len(v) <= 1:
            return 0
        return float(len(v) - 1 - np.argmax(v))
    return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_highest_day, raw=True))


def lowday(x: np.ndarray, d: int) -> np.ndarray:
    """距离 d 天最低价的天数。"""
    def _lowest_day(v):
        if len(v) <= 1:
            return 0
        return float(len(v) - 1 - np.argmin(v))
    return _to_array(_to_series(x).rolling(d, min_periods=1).apply(_lowest_day, raw=True))