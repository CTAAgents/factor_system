"""
seed_data/alpha_ops_numba.py — Numba JIT 加速算子

为 alpha_ops.py 中的算子提供 Numba @njit 加速版本。
优先使用 Numba 版本，不可用时自动降级到原 pandas 实现。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# ─── Numba 可用性检测 ──────────────────────────────────────

try:
    from numba import njit

    _NUMBA_AVAILABLE = True
except ImportError:  # pragma: no cover
    _NUMBA_AVAILABLE = False
    njit = None  # type: ignore


def is_numba_available() -> bool:
    """检测 Numba 是否可用。"""
    return _NUMBA_AVAILABLE


# ─── 截面操作（Numba 兼容） ────────────────────────────────

if _NUMBA_AVAILABLE:

    @njit(cache=True)
    def _njit_rank(x: np.ndarray) -> np.ndarray:
        """截面排序分位数（0~1）— Numba 版。"""
        n = len(x)
        if n <= 1:
            return np.zeros_like(x)
        order = np.argsort(x)
        ranks = np.empty(n, dtype=np.float64)
        ranks[order] = np.arange(n, dtype=np.float64)
        return ranks / (n - 1)

    @njit(cache=True)
    def _njit_signed_power(x: np.ndarray, a: float) -> np.ndarray:
        """sign(x) * |x|^a — Numba 版。"""
        return np.sign(x) * np.abs(x) ** a

    @njit(cache=True)
    def _njit_sign(x: np.ndarray) -> np.ndarray:
        """符号函数 — Numba 版。"""
        return np.sign(x)

    @njit(cache=True)
    def _njit_abs(x: np.ndarray) -> np.ndarray:
        """绝对值 — Numba 版。"""
        return np.abs(x)

    @njit(cache=True)
    def _njit_neg(x: np.ndarray) -> np.ndarray:
        """取负 — Numba 版。"""
        return -x

    @njit(cache=True)
    def _njit_log(x: np.ndarray) -> np.ndarray:
        """自然对数（带保护）— Numba 版。"""
        return np.log(np.maximum(x, 1e-10))

    @njit(cache=True)
    def _njit_ifelse(cond: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
        """条件选择 — Numba 版。"""
        return np.where(cond, a, b)

    @njit(cache=True)
    def _njit_scale(x: np.ndarray, a: float) -> np.ndarray:
        """缩放至 abs(sum) = a — Numba 版。"""
        s = np.sum(np.abs(x))
        if s < 1e-10:
            return x.copy()
        return x * a / s

    # ─── 时间序列滚动操作 ──────────────────────────────────

    @njit(cache=True)
    def _njit_ts_mean(arr: np.ndarray, d: int) -> np.ndarray:
        """滚动均值 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        cumsum = 0.0
        for i in range(n):
            cumsum += arr[i]
            if i >= d:
                cumsum -= arr[i - d]
            if i >= d - 1:
                result[i] = cumsum / d
        return result

    @njit(cache=True)
    def _njit_ts_sum(arr: np.ndarray, d: int) -> np.ndarray:
        """滚动求和 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        cumsum = 0.0
        for i in range(n):
            cumsum += arr[i]
            if i >= d:
                cumsum -= arr[i - d]
            if i >= d - 1:
                result[i] = cumsum
        return result

    @njit(cache=True)
    def _njit_ts_std(arr: np.ndarray, d: int) -> np.ndarray:
        """滚动标准差 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            result[i] = np.std(window)
        return result

    @njit(cache=True)
    def _njit_ts_min(arr: np.ndarray, d: int) -> np.ndarray:
        """滚动最小值 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            result[i] = np.min(arr[i - d + 1 : i + 1])
        return result

    @njit(cache=True)
    def _njit_ts_max(arr: np.ndarray, d: int) -> np.ndarray:
        """滚动最大值 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            result[i] = np.max(arr[i - d + 1 : i + 1])
        return result

    @njit(cache=True)
    def _njit_ts_corr(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:
        """滚动相关系数 — Numba 版。"""
        n = len(x)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            wx = x[i - d + 1 : i + 1]
            wy = y[i - d + 1 : i + 1]
            mx = np.mean(wx)
            my = np.mean(wy)
            dx = wx - mx
            dy = wy - my
            denom = np.sqrt(np.sum(dx * dx) * np.sum(dy * dy))
            if denom < 1e-10:
                result[i] = 0.0
            else:
                result[i] = np.sum(dx * dy) / denom
        return result

    @njit(cache=True)
    def _njit_ts_cov(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:
        """滚动协方差 — Numba 版。

        使用 ddof=1（与 pandas rolling.cov 默认行为一致）。
        """
        n = len(x)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            wx = x[i - d + 1 : i + 1]
            wy = y[i - d + 1 : i + 1]
            w = len(wx)
            if w <= 1:
                result[i] = np.nan
            else:
                dx = wx - np.mean(wx)
                dy = wy - np.mean(wy)
                result[i] = np.sum(dx * dy) / (w - 1)
        return result

    @njit(cache=True)
    def _njit_ts_rank(arr: np.ndarray, d: int) -> np.ndarray:
        """滚动分位数排序（0~1）— Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            if len(window) <= 1:
                result[i] = 0.5
            else:
                order = np.argsort(window)
                rank_arr = np.empty(len(window), dtype=np.float64)
                rank_arr[order] = np.arange(len(window), dtype=np.float64)
                result[i] = rank_arr[-1] / (len(window) - 1)
        return result

    @njit(cache=True)
    def _njit_delay(arr: np.ndarray, d: int) -> np.ndarray:
        """滞后 d 期 — Numba 版。"""
        result = np.empty(len(arr), dtype=np.float64)
        result[:d] = np.nan
        result[d:] = arr[: len(arr) - d]
        return result

    @njit(cache=True)
    def _njit_delta(arr: np.ndarray, d: int) -> np.ndarray:
        """差分: x - delay(x, d) — Numba 版。"""
        delayed = _njit_delay(arr, d)
        return arr - delayed

    @njit(cache=True)
    def _njit_decay_linear(arr: np.ndarray, d: int) -> np.ndarray:
        """线性衰减加权平均 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        w = np.arange(1, d + 1, dtype=np.float64)
        w = w / w.sum()
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            result[i] = np.sum(window * w)
        return result

    @njit(cache=True)
    def _njit_highday(arr: np.ndarray, d: int) -> np.ndarray:
        """距离 d 天最高价的天数 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            if len(window) <= 1:
                result[i] = 0.0
            else:
                result[i] = float(len(window) - 1 - np.argmax(window))
        return result

    @njit(cache=True)
    def _njit_lowday(arr: np.ndarray, d: int) -> np.ndarray:
        """距离 d 天最低价的天数 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            if len(window) <= 1:
                result[i] = 0.0
            else:
                result[i] = float(len(window) - 1 - np.argmin(window))
        return result

    @njit(cache=True)
    def _njit_ts_argmax(arr: np.ndarray, d: int) -> np.ndarray:
        """滚动 argmax 位置 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            result[i] = float(np.argmax(window))
        return result

    @njit(cache=True)
    def _njit_ts_argmin(arr: np.ndarray, d: int) -> np.ndarray:
        """滚动 argmin 位置 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            result[i] = float(np.argmin(window))
        return result

    @njit(cache=True)
    def _njit_ts_product(arr: np.ndarray, d: int) -> np.ndarray:
        """滚动乘积 — Numba 版。"""
        n = len(arr)
        result = np.empty(n, dtype=np.float64)
        result[:d - 1] = np.nan
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            p = 1.0
            for j in range(len(window)):
                p *= window[j]
            result[i] = p
        return result

    # ─── Numba Spearman IC ────────────────────────────────

    @njit(cache=True)
    def _njit_spearman_ic(signal: np.ndarray, returns: np.ndarray) -> float:
        """Numba 自实现 Spearman 秩相关系数。

        等价于 scipy.stats.spearmanr, 但避免 scipy 开销。
        """
        n = len(signal)
        if n < 2:
            return 0.0
        # 转为秩
        sig_rank = _rankdata_numba(signal)
        ret_rank = _rankdata_numba(returns)
        # Pearson 相关系数
        mx = np.mean(sig_rank)
        my = np.mean(ret_rank)
        dx = sig_rank - mx
        dy = ret_rank - my
        denom = np.sqrt(np.sum(dx * dx) * np.sum(dy * dy))
        if denom < 1e-10:
            return 0.0
        return float(np.sum(dx * dy) / denom)

    @njit
    def _rankdata_numba(arr: np.ndarray) -> np.ndarray:
        """Numba 版 rankdata（平均秩，处理并列）。"""
        n = len(arr)
        order = np.argsort(arr, kind='mergesort')
        sorted_arr = arr[order]
        ranks = np.empty(n, dtype=np.float64)
        i = 0
        while i < n:
            j = i
            while j < n - 1 and sorted_arr[j] == sorted_arr[j + 1]:
                j += 1
            avg_rank = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1
        return ranks

else:  # pragma: no cover — Numba 不可用时的 fallback 占位

    def _njit_rank(x: np.ndarray) -> np.ndarray:  # type: ignore
        return np.zeros_like(x)

    def _njit_signed_power(x: np.ndarray, a: float) -> np.ndarray:  # type: ignore
        return np.sign(x) * np.abs(x) ** a

    def _njit_sign(x: np.ndarray) -> np.ndarray:  # type: ignore
        return np.sign(x)

    def _njit_abs(x: np.ndarray) -> np.ndarray:  # type: ignore
        return np.abs(x)

    def _njit_neg(x: np.ndarray) -> np.ndarray:  # type: ignore
        return -x

    def _njit_log(x: np.ndarray) -> np.ndarray:  # type: ignore
        return np.log(np.maximum(x, 1e-10))

    def _njit_ifelse(cond: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:  # type: ignore
        return np.where(cond, a, b)

    def _njit_scale(x: np.ndarray, a: float) -> np.ndarray:  # type: ignore
        s = np.sum(np.abs(x))
        if s < 1e-10:
            return x.copy()
        return x * a / s

    def _njit_ts_mean(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            result[i] = np.mean(arr[i - d + 1 : i + 1])
        return result

    def _njit_ts_sum(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            result[i] = np.sum(arr[i - d + 1 : i + 1])
        return result

    def _njit_ts_std(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            result[i] = np.std(arr[i - d + 1 : i + 1])
        return result

    def _njit_ts_min(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            result[i] = np.min(arr[i - d + 1 : i + 1])
        return result

    def _njit_ts_max(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            result[i] = np.max(arr[i - d + 1 : i + 1])
        return result

    def _njit_ts_corr(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(x)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            wx = x[i - d + 1 : i + 1]
            wy = y[i - d + 1 : i + 1]
            mx, my = np.mean(wx), np.mean(wy)
            dx, dy = wx - mx, wy - my
            denom = np.sqrt(np.sum(dx * dx) * np.sum(dy * dy))
            result[i] = np.sum(dx * dy) / denom if denom > 1e-10 else 0.0
        return result

    def _njit_ts_cov(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(x)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            wx = x[i - d + 1 : i + 1]
            wy = y[i - d + 1 : i + 1]
            w = len(wx)
            if w <= 1:
                result[i] = np.nan
            else:
                dx = wx - np.mean(wx)
                dy = wy - np.mean(wy)
                result[i] = np.sum(dx * dy) / (w - 1)
        return result

    def _njit_ts_rank(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            order = np.argsort(window)
            ranks = np.empty(len(window))
            ranks[order] = np.arange(len(window))
            result[i] = ranks[-1] / max(len(window) - 1, 1)
        return result

    def _njit_delay(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        result = np.full(len(arr), np.nan)
        result[d:] = arr[: len(arr) - d]
        return result

    def _njit_delta(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        delayed = _njit_delay(arr, d)
        return arr - delayed

    def _njit_decay_linear(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        w = np.arange(1, d + 1, dtype=np.float64)
        w = w / w.sum()
        for i in range(d - 1, n):
            result[i] = np.sum(arr[i - d + 1 : i + 1] * w)
        return result

    def _njit_highday(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            result[i] = float(len(window) - 1 - np.argmax(window))
        return result

    def _njit_lowday(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            window = arr[i - d + 1 : i + 1]
            result[i] = float(len(window) - 1 - np.argmin(window))
        return result

    def _njit_ts_argmax(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            result[i] = float(np.argmax(arr[i - d + 1 : i + 1]))
        return result

    def _njit_ts_argmin(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            result[i] = float(np.argmin(arr[i - d + 1 : i + 1]))
        return result

    def _njit_ts_product(arr: np.ndarray, d: int) -> np.ndarray:  # type: ignore
        n = len(arr)
        result = np.full(n, np.nan)
        for i in range(d - 1, n):
            p = 1.0
            for v in arr[i - d + 1 : i + 1]:
                p *= v
            result[i] = p
        return result

    def _njit_spearman_ic(signal: np.ndarray, returns: np.ndarray) -> float:  # type: ignore
        if len(signal) < 2:
            return 0.0
        sig_rank = np.argsort(np.argsort(signal)).astype(float)
        ret_rank = np.argsort(np.argsort(returns)).astype(float)
        mx, my = np.mean(sig_rank), np.mean(ret_rank)
        dx, dy = sig_rank - mx, ret_rank - my
        denom = np.sqrt(np.sum(dx * dx) * np.sum(dy * dy))
        return float(np.sum(dx * dy) / denom) if denom > 1e-10 else 0.0


# ─── 公共接口（带自动降级） ──────────────────────────────

def ts_mean(arr: np.ndarray, d: int) -> np.ndarray:
    """滚动均值 — Numba 加速版。"""
    return _njit_ts_mean(np.ascontiguousarray(arr, dtype=np.float64), d)


def ts_std(arr: np.ndarray, d: int) -> np.ndarray:
    """滚动标准差 — Numba 加速版。"""
    return _njit_ts_std(np.ascontiguousarray(arr, dtype=np.float64), d)


def ts_sum(arr: np.ndarray, d: int) -> np.ndarray:
    """滚动求和 — Numba 加速版。"""
    return _njit_ts_sum(np.ascontiguousarray(arr, dtype=np.float64), d)


def ts_min(arr: np.ndarray, d: int) -> np.ndarray:
    """滚动最小值 — Numba 加速版。"""
    return _njit_ts_min(np.ascontiguousarray(arr, dtype=np.float64), d)


def ts_max(arr: np.ndarray, d: int) -> np.ndarray:
    """滚动最大值 — Numba 加速版。"""
    return _njit_ts_max(np.ascontiguousarray(arr, dtype=np.float64), d)


def ts_corr(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:
    """滚动相关系数 — Numba 加速版。"""
    return _njit_ts_corr(
        np.ascontiguousarray(x, dtype=np.float64),
        np.ascontiguousarray(y, dtype=np.float64), d,
    )


def ts_cov(x: np.ndarray, y: np.ndarray, d: int) -> np.ndarray:
    """滚动协方差 — Numba 加速版。"""
    return _njit_ts_cov(
        np.ascontiguousarray(x, dtype=np.float64),
        np.ascontiguousarray(y, dtype=np.float64), d,
    )


def ts_rank(arr: np.ndarray, d: int) -> np.ndarray:
    """滚动分位数排序 — Numba 加速版。"""
    return _njit_ts_rank(np.ascontiguousarray(arr, dtype=np.float64), d)


def delay(arr: np.ndarray, d: int) -> np.ndarray:
    """滞后 d 期 — Numba 加速版。"""
    return _njit_delay(np.ascontiguousarray(arr, dtype=np.float64), d)


def delta(arr: np.ndarray, d: int) -> np.ndarray:
    """差分 — Numba 加速版。"""
    return _njit_delta(np.ascontiguousarray(arr, dtype=np.float64), d)


def spearman_ic(signal: np.ndarray, returns: np.ndarray) -> float:
    """Spearman IC — Numba 加速版。"""
    return _njit_spearman_ic(
        np.ascontiguousarray(signal, dtype=np.float64),
        np.ascontiguousarray(returns, dtype=np.float64),
    )


__all__ = [
    "is_numba_available",
    # 公共接口
    "ts_mean", "ts_std", "ts_sum", "ts_min", "ts_max",
    "ts_corr", "ts_cov", "ts_rank", "delay", "delta",
    "spearman_ic",
    # 底层实现
    "_njit_rank", "_njit_signed_power", "_njit_sign", "_njit_abs",
    "_njit_neg", "_njit_log", "_njit_ifelse", "_njit_scale",
    "_njit_ts_mean", "_njit_ts_std", "_njit_ts_sum",
    "_njit_ts_min", "_njit_ts_max",
    "_njit_ts_corr", "_njit_ts_cov", "_njit_ts_rank",
    "_njit_delay", "_njit_delta",
    "_njit_decay_linear", "_njit_highday", "_njit_lowday",
    "_njit_ts_argmax", "_njit_ts_argmin", "_njit_ts_product",
    "_njit_spearman_ic",
]