"""
fts.factor_engine.feature_ops — 特征算子库 (Phase C.1)。

提供 50+ 特征算子，分为:
- 基础算子 (BasicOps): 时序/价格/滚动/截面
- 组合算子 (CompositeOps): 嵌套/条件/运算
- 算子注册表: 管理和调用所有算子

版本: v0.1.0
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

OperatorCategory = str
"""算子类别: time_series / price / rolling / cross_section / composite / cross_symbol"""


def _rolling_series_out(func: Callable[[np.ndarray, int], np.ndarray], series: pd.Series | pd.DataFrame, window: int):
    """对 Series(1D)/DataFrame(2D) 执行 1D 向量化滚动 func 并还原类型。

    func(arr_1d, window) -> 等长 ndarray。DataFrame（面板化执行路径，pdata
    按列求值）逐列循环调用 —— 列数通常 ≤150，远快于原 rolling.apply 逐窗口回调。
    """
    arr = series.to_numpy(dtype=float)
    if arr.ndim == 2:
        cols = list(series.columns)
        res = np.stack([func(arr[:, j], window) for j in range(len(cols))], axis=1)
        return pd.DataFrame(res, index=series.index, columns=cols)
    return pd.Series(func(arr, window), index=series.index)


# ─── 缺口感知执行上下文（plans/39 5.1） ─────────────────────
# 面板化执行（execute_factor_panel）把每个品种 reindex 到 union_dates，缺口日
# 期在该品种列上成为 NaN 行。逐品种路径在品种自身日历上滚动（无 NaN 行），
# 两者窗口内有效观测集合不同 → 验证回退。缺口感知模式把缺口列压缩为密集序列
# （= 品种自身日历上的观测序列）计算后散射回原位置，与逐品种语义逐位一致。
# 默认关闭（逐品种路径与既有对照测试保持既有 pandas 等价语义，零漂移）。

_GAP_AWARE: bool = False


@contextmanager
def gap_aware_mode() -> Iterator[None]:
    """在 execute_factor_panel 评估/验证作用域内开启缺口感知滚动语义。

    逐品种路径 / 单元测试默认关闭；仅面板化执行开启，避免改变既有语义。
    """
    global _GAP_AWARE
    prev = _GAP_AWARE
    _GAP_AWARE = True
    try:
        yield
    finally:
        _GAP_AWARE = prev


def _rolling_apply_native(
    arr: np.ndarray,
    window: int,
    min_periods: int,
    row_fn: Callable[[np.ndarray], float],
    batch_fn: Callable[[np.ndarray], np.ndarray],
) -> np.ndarray:
    """pandas ``rolling(window, min_periods).apply(raw=True)`` 的 numpy 等价实现。

    语义对齐（与 pandas 实测一致：窗口原始值保留 NaN，inf 一律视为 NaN，
    仅按窗口内非 NaN 观测计数判定是否输出）：
    - 前缀（窗口长度不足 window）逐窗口精确计算，量级 < window；
    - 主区间 sliding_window_view：行内全有效 → ``batch_fn`` 批量向量化（一次调用）；
      行内含 NaN → ``row_fn`` 逐行精确计算（真实无缺口数据不触发该路径）。

    ``row_fn(raw_1d) -> float`` 接收**含 NaN 的原始窗口数组**，须与原始 lambda
    语义一致（NaN 处理由内部函数自行兜底，如 nanquantile / 显式 dropna）；
    ``batch_fn(rows_2d) -> (m,)`` 接收 ``(m, window)`` 全有效行块，批量向量化结果。

    缺口感知（plans/39 5.1）：``gap_aware_mode`` 作用域内，含缺口（NaN）列先压缩
    为密集序列（非 NaN 观测按序提取 = 品种自身日历），在原内核上计算后散射回
    原位置；无缺口列走既有快路径，逐位不变（零漂移）。
    """
    arr = np.where(np.isinf(arr), np.nan, arr)
    n = len(arr)
    if _GAP_AWARE and n and np.isnan(arr).any():
        idx = np.flatnonzero(~np.isnan(arr))
        dense = arr[idx]
        r = _rolling_apply_native(dense, window, min_periods, row_fn, batch_fn)
        out: np.ndarray = np.full(n, np.nan, dtype=float)
        out[idx] = r
        return out
    out = np.full(n, np.nan, dtype=float)
    if n < min_periods:
        return out
    for i in range(min_periods - 1, min(window - 1, n)):
        win = arr[max(0, i - window + 1) : i + 1]
        if np.sum(~np.isnan(win)) >= min_periods:
            out[i] = row_fn(win)
    if n >= window:
        view = np.lib.stride_tricks.sliding_window_view(arr, window)
        valid = np.sum(~np.isnan(view), axis=-1)
        full = valid == window
        res = np.full(view.shape[0], np.nan, dtype=float)
        if full.any():
            res[full] = batch_fn(view[full])
        partial = (valid >= min_periods) & ~full
        for idx in np.flatnonzero(partial):
            res[idx] = row_fn(view[idx])
        out[window - 1 :] = res
    return out


def _ts_product_vec(arr: np.ndarray, window: int) -> np.ndarray:
    """滚动乘积（等价 rolling(window).apply(np.prod, raw=True)）。"""
    n = len(arr)
    out: np.ndarray = np.full(n, np.nan, dtype=float)
    if n >= window:
        view = np.lib.stride_tricks.sliding_window_view(arr, window)
        out[window - 1 :] = np.prod(view, axis=-1)
    return out


def _ts_zscore_vec(arr: np.ndarray, window: int) -> np.ndarray:
    """滚动 Z-Score（等价 rolling(window).apply，含 std 守卫与 NaN skipna 语义）。"""
    n = len(arr)
    out: np.ndarray = np.full(n, np.nan, dtype=float)
    if n >= window:
        view = np.lib.stride_tricks.sliding_window_view(arr, window)
        cnt = np.sum(~np.isnan(view), axis=-1)
        valid = cnt >= window  # pandas min_periods 按非 NaN 观测计数
        res = np.zeros(view.shape[0], dtype=float)
        rows = cnt >= 2
        if rows.any():
            sub = view[rows]
            with np.errstate(invalid="ignore"):
                mean = np.nanmean(sub, axis=-1)
                std = np.nanstd(sub, axis=-1, ddof=1)
            z = np.zeros_like(std)
            np.divide(sub[:, -1] - mean, std, out=z, where=std > 0)
            res[rows] = np.where(std > 0, z, 0.0)
        out[window - 1 :] = np.where(valid, res, np.nan)
    return out


def _ts_min_max_diff_vec(arr: np.ndarray, window: int) -> np.ndarray:
    """滚动极差（等价 rolling(window).apply(max-min)，skipna）。"""
    n = len(arr)
    out: np.ndarray = np.full(n, np.nan, dtype=float)
    if n >= window:
        view = np.lib.stride_tricks.sliding_window_view(arr, window)
        cnt = np.sum(~np.isnan(view), axis=-1)
        valid = cnt >= window
        res = np.full(view.shape[0], np.nan)
        rows = cnt >= 1
        if rows.any():
            sub = view[rows]
            res[rows] = np.nanmax(sub, axis=-1) - np.nanmin(sub, axis=-1)
        out[window - 1 :] = np.where(valid, res, np.nan)
    return out


def _ts_cum_max_vec(arr: np.ndarray, window: int) -> np.ndarray:
    """滚动累计最大值（等价 rolling(window).apply(x.cummax().iloc[-1])，skipna）。"""
    n = len(arr)
    out: np.ndarray = np.full(n, np.nan, dtype=float)
    if n >= window:
        view = np.lib.stride_tricks.sliding_window_view(arr, window)
        cnt = np.sum(~np.isnan(view), axis=-1)
        valid = cnt >= window
        res = np.full(view.shape[0], np.nan)
        rows = cnt >= 1
        if rows.any():
            sub = view[rows]
            res[rows] = np.nanmax(sub, axis=-1)
        out[window - 1 :] = np.where(valid, res, np.nan)
    return out


def _max_drawdown_vec(arr: np.ndarray, window: int) -> np.ndarray:
    """滚动最大回撤（等价 rolling(window).apply((x/cummax-1).min())，skipna）。"""
    n = len(arr)
    out: np.ndarray = np.full(n, np.nan, dtype=float)
    if n >= window:
        view = np.lib.stride_tricks.sliding_window_view(arr, window)
        cnt = np.sum(~np.isnan(view), axis=-1)
        valid = cnt >= window
        res = np.full(view.shape[0], np.nan)
        rows = cnt >= 1
        if rows.any():
            sub = view[rows]
            cm = np.maximum.accumulate(np.where(np.isnan(sub), -np.inf, sub), axis=-1)
            cm = np.where(np.isnan(sub), np.nan, cm)
            with np.errstate(invalid="ignore"):
                res[rows] = np.nanmin(sub / cm - 1.0, axis=-1)
        out[window - 1 :] = np.where(valid, res, np.nan)
    return out


def _ts_argmin_vec(arr: np.ndarray, window: int) -> np.ndarray:
    """窗口内最小值位置（等价 rolling(window, min_periods=2).apply(np.argmin)）。"""
    n = len(arr)
    out: np.ndarray = np.full(n, np.nan, dtype=float)
    if n >= 2:
        for k in range(2, min(window, n + 1)):
            head = arr[:k]
            if np.count_nonzero(~np.isnan(head)) >= 2:
                out[k - 1] = np.argmin(head)
        if n >= window:
            view = np.lib.stride_tricks.sliding_window_view(arr, window)
            valid = np.sum(~np.isnan(view), axis=-1) >= 2
            res = np.argmin(view, axis=-1)
            out[window - 1 :] = np.where(valid, res, np.nan)
    return out


def _ts_argmax_vec(arr: np.ndarray, window: int) -> np.ndarray:
    """窗口内最大值位置（等价 rolling(window).apply(np.argmax, raw=True)，min_periods=window）。"""
    n = len(arr)
    out: np.ndarray = np.full(n, np.nan, dtype=float)
    if n >= window:
        view = np.lib.stride_tricks.sliding_window_view(arr, window)
        valid = np.sum(~np.isnan(view), axis=-1) >= window
        res = np.argmax(view, axis=-1)
        out[window - 1 :] = np.where(valid, res, np.nan)
    return out


def _ts_decay_linear_vec(arr: np.ndarray, window: int) -> np.ndarray:
    """线性衰减加权（等价 rolling(n).apply(dot(w, arange)/sum)，min_periods=n）。"""
    n = len(arr)
    out: np.ndarray = np.full(n, np.nan, dtype=float)
    if n >= window:
        view = np.lib.stride_tricks.sliding_window_view(arr, window)
        valid = np.sum(~np.isnan(view), axis=-1) >= window
        w = np.arange(1, window + 1, dtype=float) / (window * (window + 1) / 2.0)
        res = np.sum(view * w[None, :], axis=-1)
        out[window - 1 :] = np.where(valid, res, np.nan)
    return out


def _self_corr_vec(arr: np.ndarray, window: int) -> np.ndarray:
    """lag-1 自相关（等价 rolling(window, min_periods=3).apply(_lag1).fillna(0)）。"""
    n = len(arr)
    out: np.ndarray = np.zeros(n, dtype=float)
    if n < 3 or window < 3:
        return out
    for k in range(3, min(window, n + 1)):
        x0, x1 = arr[: k - 1], arr[1:k]
        s0, s1 = float(x0.std()), float(x1.std())
        if s0 == 0 or s1 == 0:
            out[k - 1] = 0.0
        else:
            v = float(np.corrcoef(x0, x1)[0, 1])
            out[k - 1] = 0.0 if np.isnan(v) else v
    if n >= window:
        view = np.lib.stride_tricks.sliding_window_view(arr, window)
        x0, x1 = view[:, :-1], view[:, 1:]
        npair = x0.shape[1]
        with np.errstate(invalid="ignore"):
            m0 = x0.mean(axis=-1)
            m1 = x1.mean(axis=-1)
            s0 = x0.std(axis=-1, ddof=0)
            s1 = x1.std(axis=-1, ddof=0)
            cov = np.sum((x0 - m0[:, None]) * (x1 - m1[:, None]), axis=-1) / (npair - 1)
            corr = cov / (x0.std(axis=-1, ddof=1) * x1.std(axis=-1, ddof=1))
        corr = np.where((s0 != 0) & (s1 != 0), corr, 0.0)
        out[window - 1 :] = np.where(np.isnan(corr), 0.0, corr)
    return out


@dataclass
class OperatorInfo:
    """算子元数据。"""

    name: str
    category: str
    params: list[str] = field(default_factory=list)
    description: str = ""
    signature: str = ""
    version: str = "0.1.0"
    added_at: str = ""


# ─── 时序算子 ───────────────────────────────────────────────


class TimeSeriesOps:
    """时序算子集合。"""

    @staticmethod
    def ts_mean(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动均值。"""
        return series.rolling(window=window).mean()

    @staticmethod
    def ts_std(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动标准差。"""
        return series.rolling(window=window).std()

    @staticmethod
    def ts_max(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动最大值。"""
        return series.rolling(window=window).max()

    @staticmethod
    def ts_min(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动最小值。"""
        return series.rolling(window=window).min()

    @staticmethod
    def ts_sum(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动求和。"""
        return series.rolling(window=window).sum()

    @staticmethod
    def ts_product(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动乘积。"""
        return _rolling_series_out(_ts_product_vec, series, window)


# ─── 价格算子 ───────────────────────────────────────────────


class PriceOps:
    """价格算子集合。"""

    @staticmethod
    def rank(series: pd.Series) -> pd.Series:
        """截面排名 (0-1 归一化)。"""
        return series.rank(pct=True)

    @staticmethod
    def zscore(series: pd.Series) -> pd.Series:
        """Z-Score 标准化。"""
        mean = series.mean()
        std = series.std()
        return (series - mean) / std if std > 0 else series

    @staticmethod
    def delta(series: pd.Series, periods: int = 1) -> pd.Series:
        """变化量。"""
        return series.diff(periods)

    @staticmethod
    def pct_change(series: pd.Series, periods: int = 1) -> pd.Series:
        """百分比变化。"""
        return series.pct_change(periods)

    @staticmethod
    def log_return(series: pd.Series) -> pd.Series:
        """对数收益。"""
        return np.log(series / series.shift(1))


# ─── 滚动算子 ───────────────────────────────────────────────


class RollingOps:
    """滚动算子集合。"""

    @staticmethod
    def ts_rank(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动窗口内排名（pct）。

        numba 快速路径（plans/38 4.2/4.3）：1D 全有限数组 → ``rank_1d`` 内核；
        面板（DataFrame）全有限 → ``rank_2d`` 单次 njit（消除逐列 pandas 循环）；
        含 NaN/inf（面板缺口列）或开关关闭 → 回退 pandas ``rolling.rank``，零漂移。
        """
        if isinstance(series, pd.Series):
            arr = series.to_numpy(dtype=float)
            if np.isfinite(arr).all():
                from .numba_kernels import rank_1d

                nb = rank_1d(arr, window, window, pct=True)
                if nb is not None:
                    return pd.Series(nb, index=series.index)
        elif isinstance(series, pd.DataFrame):
            arr = series.to_numpy(dtype=float)
            if np.isfinite(arr).all():
                from .numba_kernels import rank_2d

                nb = rank_2d(arr, window, window, pct=True)
                if nb is not None:
                    return pd.DataFrame(nb, index=series.index, columns=series.columns)
        return series.rolling(window=window).rank(pct=True)

    @staticmethod
    def ts_zscore(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动 Z-Score。

        plans/40 C 层：numba 快速路径（1D/2D 全有限数组 → ``zscore_1d/zscore_2d``）；
        含 NaN/inf（缺口列）或开关关闭 → 回退向量化现值 ``_ts_zscore_vec``，零漂移。
        （plans/38 §4.5 曾回退现值；plans/40 复核后按 2D 面板语义重新启用）
        """
        from .numba_kernels import zscore_1d, zscore_2d

        if isinstance(series, pd.Series):
            arr = series.to_numpy(dtype=float)
            if np.isfinite(arr).all():
                nb = zscore_1d(arr, window)
                if nb is not None:
                    return pd.Series(nb, index=series.index)
        elif isinstance(series, pd.DataFrame):
            arr = series.to_numpy(dtype=float)
            if np.isfinite(arr).all():
                nb = zscore_2d(arr, window)
                if nb is not None:
                    return pd.DataFrame(nb, index=series.index, columns=series.columns)
        return _rolling_series_out(_ts_zscore_vec, series, window)

    @staticmethod
    def ts_momentum(series: pd.Series, window: int = 20) -> pd.Series:
        """动量指标 (当前值 / window 前的值 - 1)。"""
        return series / series.shift(window) - 1

    @staticmethod
    def ts_volatility(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动波动率 (年化)。"""
        return series.pct_change().rolling(window=window).std() * np.sqrt(252)

    @staticmethod
    def ts_skewness(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动偏度。"""
        return series.rolling(window=window).skew()

    @staticmethod
    def ts_kurtosis(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动峰度。"""
        return series.rolling(window=window).kurt()

    @staticmethod
    def ts_median(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动中位数。"""
        return series.rolling(window=window).median()

    @staticmethod
    def ts_min_max_diff(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动极差。"""
        return _rolling_series_out(_ts_min_max_diff_vec, series, window)

    @staticmethod
    def ts_cum_max(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动累计最大值。"""
        return _rolling_series_out(_ts_cum_max_vec, series, window)

    @staticmethod
    def ts_regression_residual(
        series: pd.Series,
        other: pd.Series,
        window: int = 20,
    ) -> pd.Series:
        """滚动线性回归残差（GAP-L401）。

        在滚动窗口内对 `series`（y）关于 `other`（x）做一元线性回归，
        返回当前点的残差 y − (a + b·x)，用于去 beta 的 alpha 提取。
        窗口内存在 NaN / 样本不足 / 方差过小时返回 NaN（安全降级）。

        Args:
            series: 因变量（y）
            other: 自变量（x）
            window: 滚动窗口长度

        Returns:
            残差序列（NaN 安全）。
        """
        y_vals = series.to_numpy(dtype=float)
        x_vals = other.reindex(series.index).to_numpy(dtype=float)
        n = len(y_vals)
        result = pd.Series(np.nan, index=series.index, dtype=float)
        if n < window:
            return result
        for i in range(window - 1, n):
            lo = i - window + 1
            yy = y_vals[lo : i + 1]
            xx = x_vals[lo : i + 1]
            if not (np.isfinite(yy).all() and np.isfinite(xx).all()):
                continue
            if len(yy) < 3 or np.std(xx) < 1e-12:
                continue
            b = float(np.cov(xx, yy)[0, 1] / np.var(xx))
            a = float(np.mean(yy) - b * np.mean(xx))
            result.iloc[i] = float(yy[-1] - (a + b * xx[-1]))
        return result

    @staticmethod
    def ts_quantile_bucket(series: pd.Series, n_buckets: int = 5) -> pd.Series:
        """分位桶（GAP-L401）。

        按序列值分位划分为 0~n_buckets-1 桶（qcut），
        用于将连续信号离散为排序分组。

        Args:
            series: 输入序列
            n_buckets: 桶数（≥2）

        Returns:
            桶编号序列（0~n_buckets-1）；样本不足时 NaN。
        """
        if n_buckets < 2:
            raise ValueError("n_buckets 必须 ≥ 2")
        valid = series.dropna()
        if len(valid) < n_buckets:
            return pd.Series(np.nan, index=series.index, dtype=float)
        try:
            buckets = pd.qcut(valid, q=n_buckets, labels=False, duplicates="drop")
        except ValueError:
            return pd.Series(np.nan, index=series.index, dtype=float)
        return buckets.reindex(series.index)

    @staticmethod
    def ts_slope(series: pd.Series | pd.DataFrame, window: int = 20) -> pd.Series | pd.DataFrame:
        """滚动线性回归斜率（GAP-I202，v2.75.0）。

        在滚动窗口内对序列关于时间索引（0,1,...,window-1）做一元线性回归，
        返回回归斜率，用于刻画局部趋势强度与方向。
        窗口内存在 NaN / 样本不足 / 方差过小时返回 NaN（安全降级）。

        实现走 ``_rolling_apply_native``（plans/39 5.3）：无缺口列零漂移（等价
        既有手动循环语义：仅全有限窗输出、min_periods=window）；缺口列在
        ``gap_aware_mode`` 作用域内压缩-散射，与逐品种语义逐位一致（面板化）。

        Args:
            series: 输入序列（Series），或面板路径 DataFrame/_GapAwareFrame
            window: 滚动窗口长度

        Returns:
            斜率序列（NaN 安全）；面板路径返回等宽 DataFrame。
        """
        # 面板路径（DataFrame / _GapAwareFrame）：逐列调用（_rolling_apply_native 缺口感知）
        if hasattr(series, "columns") and len(series.columns) > 0:
            return pd.DataFrame({c: RollingOps.ts_slope(series[c], window) for c in series.columns})

        vals = series.to_numpy(dtype=float)
        t: np.ndarray = np.arange(window, dtype=float)
        t_centered = t - t.mean()
        denom = float(t_centered @ t_centered)

        def _row(w: np.ndarray) -> float:
            if not np.isfinite(w).all():
                return np.nan
            y_centered = w - w.mean()
            return float(t_centered @ y_centered) / denom

        def _batch(rows: np.ndarray) -> np.ndarray:
            y_centered = rows - rows.mean(axis=-1, keepdims=True)
            return (t_centered @ y_centered.T) / denom

        if denom < 1e-12:
            out = np.full(len(vals), np.nan, dtype=float)
        else:
            out = _rolling_apply_native(vals, window, window, _row, _batch)
        return pd.Series(out, index=series.index, dtype=float)

    @staticmethod
    def ts_quantile(series: pd.Series, window: int = 20, q: float = 0.5) -> pd.Series:
        """滚动分位数（GAP-I202，v2.75.0）。

        Args:
            series: 输入序列
            window: 滚动窗口长度
            q: 分位数（0~1）

        Returns:
            分位数序列（NaN 安全）。
        """
        if not (0.0 <= q <= 1.0):
            raise ValueError("q 必须在 [0, 1] 区间")
        return series.rolling(window=window).quantile(q)


# ─── 技术指标算子 ──────────────────────────────────────────


class TechnicalOps:
    """技术指标算子集合。"""

    @staticmethod
    def rsi(series: pd.Series, window: int = 14) -> pd.Series:
        """RSI 相对强弱指数。"""
        delta = series.diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        avg_gain = gain.rolling(window=window).mean()
        avg_loss = loss.rolling(window=window).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def bollinger_upper(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
        """布林带上轨。"""
        ma = series.rolling(window=window).mean()
        std = series.rolling(window=window).std()
        return ma + num_std * std

    @staticmethod
    def bollinger_lower(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
        """布林带下轨。"""
        ma = series.rolling(window=window).mean()
        std = series.rolling(window=window).std()
        return ma - num_std * std

    @staticmethod
    def bollinger_width(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
        """布林带宽度。"""
        upper = TechnicalOps.bollinger_upper(series, window, num_std)
        lower = TechnicalOps.bollinger_lower(series, window, num_std)
        return (upper - lower) / series.rolling(window=window).mean()

    @staticmethod
    def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
        """平均真实波幅。"""
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return tr.rolling(window=window).mean()

    @staticmethod
    def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
        """MACD 指标。"""
        ema_fast = series.ewm(span=fast, adjust=False).mean()
        ema_slow = series.ewm(span=slow, adjust=False).mean()
        dif = ema_fast - ema_slow
        return dif.ewm(span=signal, adjust=False).mean()

    @staticmethod
    def max_drawdown(series: pd.Series, window: int = 252) -> pd.Series:
        """滚动最大回撤。"""
        return _rolling_series_out(_max_drawdown_vec, series, window)


# ─── 截面算子 ───────────────────────────────────────────────


class CrossSectionOps:
    """截面算子集合。"""

    @staticmethod
    def cross_rank(
        panel: pd.DataFrame,
        group_col: str = "date",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """截面排名 (按日期分组)。"""
        result = panel.copy()
        result["cross_rank"] = result.groupby(group_col)[value_col].rank(pct=True)
        return result

    @staticmethod
    def cross_zscore(
        panel: pd.DataFrame,
        group_col: str = "date",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """截面 Z-Score。"""
        result = panel.copy()
        result["cross_zscore"] = result.groupby(group_col)[value_col].transform(
            lambda x: (x - x.mean()) / x.std() if x.std() > 0 else 0.0
        )
        return result

    @staticmethod
    def industry_neutral(
        panel: pd.DataFrame,
        group_col: str = "date",
        industry_col: str = "industry",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """行业中性化。"""
        result = panel.copy()
        result["industry_mean"] = result.groupby([group_col, industry_col])[value_col].transform("mean")
        result["neutralized"] = result[value_col] - result["industry_mean"]
        return result


# ─── 跨品种算子 ─────────────────────────────────────────────


class CrossSymbolOps:
    """跨品种算子集合。"""

    @staticmethod
    def industry_demean(
        panel: pd.DataFrame,
        group_col: str = "date",
        industry_col: str = "industry",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """行业去均值 (中性化)。"""
        result = panel.copy()
        result["industry_mean"] = result.groupby([group_col, industry_col])[value_col].transform("mean")
        result[value_col] = result[value_col] - result["industry_mean"]
        return result

    @staticmethod
    def region_demean(
        panel: pd.DataFrame,
        group_col: str = "date",
        region_col: str = "region",
        value_col: str = "value",
    ) -> pd.DataFrame:
        """区域去均值 (中性化)。"""
        result = panel.copy()
        result["region_mean"] = result.groupby([group_col, region_col])[value_col].transform("mean")
        result[value_col] = result[value_col] - result["region_mean"]
        return result


# ─── 组合算子 ───────────────────────────────────────────────


class CompositeOps:
    """组合算子集合。"""

    @staticmethod
    def add(a: pd.Series, b: pd.Series) -> pd.Series:
        """加法。"""
        return a + b

    @staticmethod
    def sub(a: pd.Series, b: pd.Series) -> pd.Series:
        """减法。"""
        return a - b

    @staticmethod
    def mul(a: pd.Series, b: pd.Series) -> pd.Series:
        """乘法。"""
        return a * b

    @staticmethod
    def div(a: pd.Series, b: pd.Series) -> pd.Series:
        """除法 (安全除零保护)。"""
        return a / b.replace(0, np.nan)

    @staticmethod
    def scale(series: pd.Series, factor: float = 1.0) -> pd.Series:
        """缩放。"""
        return series * factor

    @staticmethod
    def if_then_else(
        condition: pd.Series,
        then_value: pd.Series | float,
        else_value: pd.Series | float,
    ) -> pd.Series:
        """条件算子。"""
        return pd.Series(
            np.where(condition, then_value, else_value),
            index=condition.index,
        )

    @staticmethod
    def conditional_weight(
        series: pd.Series,
        weight: pd.Series,
        threshold: float = 0.0,
    ) -> pd.Series:
        """条件加权。"""
        return pd.Series(
            np.where(series > threshold, series * weight, 0.0),
            index=series.index,
        )


# ─── 算子注册表 ─────────────────────────────────────────────


class OperatorRegistry:
    """特征算子注册表。

    管理所有可用算子，支持运行时查询和调用。

    Usage:
        registry = OperatorRegistry()
        result = registry.call("ts_mean", series, window=20)
    """

    def __init__(self) -> None:
        self._operators: dict[str, tuple[OperatorInfo, Callable]] = {}
        self._initialize_builtin()

    def register(
        self,
        name: str,
        func: Callable,
        category: str,
        params: list[str],
        description: str = "",
    ) -> None:
        """注册新算子。"""
        import datetime

        info = OperatorInfo(
            name=name,
            category=category,
            params=params,
            description=description,
            signature=f"{name}({', '.join(params)})",
            added_at=datetime.datetime.now().isoformat(),
        )
        self._operators[name] = (info, func)
        logger.debug("注册算子: %s [%s]", name, category)

    def call(self, name: str, *args: Any, **kwargs: Any) -> pd.Series:
        """调用算子。"""
        if name not in self._operators:
            raise KeyError(f"算子未注册: {name}")
        _, func = self._operators[name]
        return func(*args, **kwargs)

    def list_operators(self, category: Optional[str] = None) -> list[OperatorInfo]:
        """列出所有算子。"""
        operators = [info for info, _ in self._operators.values()]
        if category:
            operators = [op for op in operators if op.category == category]
        return sorted(operators, key=lambda x: x.name)

    def get_operator(self, name: str) -> Optional[OperatorInfo]:
        """获取算子信息。"""
        info, _ = self._operators.get(name, (None, None))
        return info

    def list_categories(self) -> list[str]:
        """列出所有算子类别。"""
        categories = {info.category for info, _ in self._operators.values()}
        return sorted(categories)

    @property
    def operator_count(self) -> int:
        """已注册算子数量。"""
        return len(self._operators)

    def _initialize_builtin(self) -> None:
        """初始化内置算子。"""
        # 时序算子
        ts_ops = [
            ("ts_mean", TimeSeriesOps.ts_mean, ["series", "window"]),
            ("ts_std", TimeSeriesOps.ts_std, ["series", "window"]),
            ("ts_max", TimeSeriesOps.ts_max, ["series", "window"]),
            ("ts_min", TimeSeriesOps.ts_min, ["series", "window"]),
            ("ts_sum", TimeSeriesOps.ts_sum, ["series", "window"]),
            ("ts_product", TimeSeriesOps.ts_product, ["series", "window"]),
        ]
        for name, func, params in ts_ops:
            self.register(name, func, "time_series", params)

        # 价格算子
        price_ops = [
            ("rank", PriceOps.rank, ["series"]),
            ("zscore", PriceOps.zscore, ["series"]),
            ("delta", PriceOps.delta, ["series", "periods"]),
            ("pct_change", PriceOps.pct_change, ["series", "periods"]),
            ("log_return", PriceOps.log_return, ["series"]),
            ("abs", lambda s: s.abs(), ["series"]),
            ("sign", lambda s: np.sign(s), ["series"]),
        ]
        for name, func_p, params in price_ops:
            self.register(name, func_p, "price", params)

        # 滚动算子
        rolling_ops = [
            ("ts_rank", RollingOps.ts_rank, ["series", "window"]),
            ("ts_zscore", RollingOps.ts_zscore, ["series", "window"]),
            ("ts_momentum", RollingOps.ts_momentum, ["series", "window"]),
            ("ts_volatility", RollingOps.ts_volatility, ["series", "window"]),
            ("ts_skewness", RollingOps.ts_skewness, ["series", "window"]),
            ("ts_kurtosis", RollingOps.ts_kurtosis, ["series", "window"]),
            ("ts_median", RollingOps.ts_median, ["series", "window"]),
            ("ts_min_max_diff", RollingOps.ts_min_max_diff, ["series", "window"]),
            ("ts_cum_max", RollingOps.ts_cum_max, ["series", "window"]),
        ]
        for name, func, params in rolling_ops:
            self.register(name, func, "rolling", params)

        # GAP-I202 (v2.75.0): 组合/跨标的算子单一事实源——
        # 与 expr_dsl 注册表（L1/L4）共用 RollingOps/PriceOps 底层原语，
        # GP 演化与算子演化可发现同一组算子（verify_registry_consistency 强制共享）。
        combo_ops = [
            # 时序组合（L1 对应）
            ("ts_slope", RollingOps.ts_slope, ["series", "window"]),
            ("ts_quantile", RollingOps.ts_quantile, ["series", "window", "q"]),
            # 组合数学/跨标的（L4 对应，GAP-L401）
            ("regression_residual", RollingOps.ts_regression_residual, ["series", "other", "window"]),
            ("quantile_bucket", RollingOps.ts_quantile_bucket, ["series", "n_buckets"]),
            ("cross_section_demean", lambda s: s - s.mean(), ["series"]),
            ("if_else", lambda cond, x, y: x.where(cond.fillna(False).astype(bool), y), ["cond", "x", "y"]),
            ("corr", lambda x, y, n: x.rolling(n).corr(y), ["x", "y", "window"]),
            ("cross_section_rank", PriceOps.rank, ["series"]),
        ]
        for name, func_c, params in combo_ops:
            self.register(name, func_c, "combo", params)

        # 技术指标算子
        tech_ops = [
            ("rsi", TechnicalOps.rsi, ["series", "window"]),
            ("bollinger_upper", TechnicalOps.bollinger_upper, ["series", "window", "num_std"]),
            ("bollinger_lower", TechnicalOps.bollinger_lower, ["series", "window", "num_std"]),
            ("bollinger_width", TechnicalOps.bollinger_width, ["series", "window", "num_std"]),
            ("atr", TechnicalOps.atr, ["high", "low", "close", "window"]),
            ("macd", TechnicalOps.macd, ["series", "fast", "slow", "signal"]),
            ("max_drawdown", TechnicalOps.max_drawdown, ["series", "window"]),
        ]
        for name, func_t, params in tech_ops:
            self.register(name, func_t, "technical", params)

        # 截面算子
        cs_ops = [
            ("cross_rank", CrossSectionOps.cross_rank, ["panel", "group_col", "value_col"]),
            ("cross_zscore", CrossSectionOps.cross_zscore, ["panel", "group_col", "value_col"]),
            (
                "cross_demean",
                lambda p, g, v: p.assign(**{v: p[v] - p.groupby(g)[v].transform("mean")}),
                ["panel", "group_col", "value_col"],
            ),
            ("cross_median", lambda p, g, v: p.groupby(g)[v].transform("median"), ["panel", "group_col", "value_col"]),
            ("cross_std", lambda p, g, v: p.groupby(g)[v].transform("std"), ["panel", "group_col", "value_col"]),
        ]
        for name, func_cs, params in cs_ops:
            self.register(name, func_cs, "cross_section", params)

        # 跨品种算子
        csymbol_ops = [
            ("industry_demean", CrossSymbolOps.industry_demean, ["panel", "group_col", "industry_col", "value_col"]),
            ("region_demean", CrossSymbolOps.region_demean, ["panel", "group_col", "region_col", "value_col"]),
        ]
        for name, func_s, params in csymbol_ops:
            self.register(name, func_s, "cross_symbol", params)

        # 组合算子
        comp_ops = [
            ("add", CompositeOps.add, ["a", "b"]),
            ("sub", CompositeOps.sub, ["a", "b"]),
            ("mul", CompositeOps.mul, ["a", "b"]),
            ("div", CompositeOps.div, ["a", "b"]),
            ("scale", CompositeOps.scale, ["series", "factor"]),
            ("if_then_else", CompositeOps.if_then_else, ["condition", "then_value", "else_value"]),
            ("conditional_weight", CompositeOps.conditional_weight, ["series", "weight", "threshold"]),
            ("max", lambda a, b: np.maximum(a, b), ["a", "b"]),
            ("min", lambda a, b: np.minimum(a, b), ["a", "b"]),
            ("pow", lambda a, b: np.power(a, b), ["a", "b"]),
            ("sqrt", lambda s: np.sqrt(s.abs()), ["series"]),
            ("exp", lambda s: np.exp(s), ["series"]),
            ("log", lambda s: np.log(s.abs() + 1e-10), ["series"]),
        ]
        for name, func_comp, params in comp_ops:
            self.register(name, func_comp, "composite", params)

        # C8 算子扩容（2026-08-11）：22 个高价值算子（与 expr_dsl.registry 双注册表共享）
        c8_ops = [
            # L1 时序统计
            ("ts_argmin", C8Ops.ts_argmin, ["series", "window"]),
            ("ts_ema", C8Ops.ts_ema, ["series", "span"]),
            ("ts_mad", C8Ops.ts_mad, ["series", "window"]),
            ("ts_range", C8Ops.ts_range, ["series", "window"]),
            ("ts_iqr", C8Ops.ts_iqr, ["series", "window"]),
            ("ts_quantile_range", C8Ops.ts_quantile_range, ["series", "window", "q_hi", "q_lo"]),
            ("ts_return_over_max", C8Ops.ts_return_over_max, ["series", "window"]),
            ("ts_min_max_ratio", C8Ops.ts_min_max_ratio, ["series", "window"]),
            ("ts_std_ratio", C8Ops.ts_std_ratio, ["series", "short", "long"]),
            ("ts_roc_sum", C8Ops.ts_roc_sum, ["series", "window"]),
            ("ts_breakout", C8Ops.ts_breakout, ["series", "window"]),
            ("ts_cumulative_return", C8Ops.ts_cumulative_return, ["series", "window"]),
            # L2 截面（单序列滚动语义）
            ("cs_rank_diff", C8Ops.cs_rank_diff, ["series", "window"]),
            ("cs_zscore_diff", C8Ops.cs_zscore_diff, ["series", "window"]),
            ("cs_extreme_ratio", C8Ops.cs_extreme_ratio, ["series", "window", "n_std"]),
            ("cs_median_dev", C8Ops.cs_median_dev, ["series", "window"]),
            # L3 条件
            ("where_gt", C8Ops.where_gt, ["series", "threshold", "a", "b"]),
            ("consecutive_true", C8Ops.consecutive_true, ["series", "window"]),
            ("sign_flip", C8Ops.sign_flip, ["series", "window"]),
            # L5 领域
            ("mean_reversion_z", C8Ops.mean_reversion_z, ["series", "window"]),
            ("trend_strength", C8Ops.trend_strength, ["series", "window"]),
            ("volume_pressure", C8Ops.volume_pressure, ["close", "volume", "window"]),
        ]
        for name, func_c8, params in c8_ops:
            self.register(name, func_c8, "c8", params)

        # C9 算子扩容（2026-08-11）：30 个高价值算子（与 expr_dsl.registry 双注册表共享）
        c9_ops = [
            # L1 时序统计
            ("ts_pct_rank_window", C9Ops.ts_pct_rank_window, ["series", "window"]),
            ("ts_zscore_rolling", C9Ops.ts_zscore_rolling, ["series", "window"]),
            ("ts_skew", C9Ops.ts_skew, ["series", "window"]),
            ("ts_kurt", C9Ops.ts_kurt, ["series", "window"]),
            ("ts_slope_pct", C9Ops.ts_slope_pct, ["series", "window"]),
            ("ts_position_in_range", C9Ops.ts_position_in_range, ["series", "window"]),
            ("ts_down_ratio", C9Ops.ts_down_ratio, ["series", "window"]),
            ("ts_up_ratio", C9Ops.ts_up_ratio, ["series", "window"]),
            ("ts_gain_loss_ratio", C9Ops.ts_gain_loss_ratio, ["series", "window"]),
            ("ts_bias_ma", C9Ops.ts_bias_ma, ["series", "window"]),
            ("ts_boll_position", C9Ops.ts_boll_position, ["series", "window", "k"]),
            ("ts_ma_diff", C9Ops.ts_ma_diff, ["series", "short", "long"]),
            ("ts_vol_shrink", C9Ops.ts_vol_shrink, ["series", "short", "long"]),
            ("ts_tail_risk", C9Ops.ts_tail_risk, ["series", "window", "q"]),
            # L2 截面（单序列滚动语义）
            ("cs_winsor_flag", C9Ops.cs_winsor_flag, ["series", "window", "k"]),
            ("cs_demean_ratio", C9Ops.cs_demean_ratio, ["series", "window"]),
            ("cs_rank_norm", C9Ops.cs_rank_norm, ["series"]),
            ("cs_med_ratio", C9Ops.cs_med_ratio, ["series", "window"]),
            ("cs_extreme_gap", C9Ops.cs_extreme_gap, ["series", "window"]),
            # L3 条件
            ("where_between", C9Ops.where_between, ["series", "lo", "hi", "a", "b"]),
            ("cross_above", C9Ops.cross_above, ["series", "threshold"]),
            ("cross_below", C9Ops.cross_below, ["series", "threshold"]),
            ("momentum_break", C9Ops.momentum_break, ["series", "window", "k"]),
            # L5 领域
            ("vol_regime", C9Ops.vol_regime, ["series", "window"]),
            ("mean_reversion_signal", C9Ops.mean_reversion_signal, ["series", "window"]),
            ("price_volume_div", C9Ops.price_volume_div, ["close", "volume", "window"]),
            ("liquidity_dryup", C9Ops.liquidity_dryup, ["volume", "window"]),
            ("self_corr", C9Ops.self_corr, ["series", "window"]),
            ("sign_entropy", C9Ops.sign_entropy, ["series", "window"]),
            ("reversal_strength", C9Ops.reversal_strength, ["series", "window"]),
        ]
        for name, func_c9, params in c9_ops:
            self.register(name, func_c9, "c9", params)

        # D10 算子族（2026-08-11 扩容二期）：波动/风险族 55 算子（与 expr_dsl.registry 双注册表共享）
        from .ops_library import D10Ops as _D10

        d10_ops = [
            ("ts_realized_vol", _D10.ts_realized_vol, ["series", "window"]),
            ("ts_ewma_vol", _D10.ts_ewma_vol, ["series", "span"]),
            ("ts_parkinson", _D10.ts_parkinson, ["high", "low", "window"]),
            ("ts_garman_klass", _D10.ts_garman_klass, ["open_p", "high", "low", "close", "window"]),
            ("ts_rogers_satchell", _D10.ts_rogers_satchell, ["open_p", "high", "low", "close", "window"]),
            ("ts_yang_zhang", _D10.ts_yang_zhang, ["open_p", "high", "low", "close", "window"]),
            ("ts_downside_vol", _D10.ts_downside_vol, ["series", "window"]),
            ("ts_upside_vol", _D10.ts_upside_vol, ["series", "window"]),
            ("ts_vol_of_vol", _D10.ts_vol_of_vol, ["series", "window"]),
            ("ts_bipower_var", _D10.ts_bipower_var, ["series", "window"]),
            ("ts_range_vol", _D10.ts_range_vol, ["high", "low", "close", "window"]),
            ("ts_harmonic_vol", _D10.ts_harmonic_vol, ["series", "window"]),
            ("ts_drawdown", _D10.ts_drawdown, ["series", "window"]),
            ("ts_max_drawdown", _D10.ts_max_drawdown, ["series", "window"]),
            ("ts_avg_drawdown", _D10.ts_avg_drawdown, ["series", "window"]),
            ("ts_drawdown_duration", _D10.ts_drawdown_duration, ["series", "window"]),
            ("ts_ulcer_index", _D10.ts_ulcer_index, ["series", "window"]),
            ("ts_var_95", _D10.ts_var_95, ["series", "window"]),
            ("ts_var_99", _D10.ts_var_99, ["series", "window"]),
            ("ts_cvar_95", _D10.ts_cvar_95, ["series", "window"]),
            ("ts_cvar_99", _D10.ts_cvar_99, ["series", "window"]),
            ("ts_semi_std", _D10.ts_semi_std, ["series", "window"]),
            ("ts_lpm_2", _D10.ts_lpm_2, ["series", "window"]),
            ("ts_hpm_2", _D10.ts_hpm_2, ["series", "window"]),
            ("ts_gain_std", _D10.ts_gain_std, ["series", "window"]),
            ("ts_loss_std", _D10.ts_loss_std, ["series", "window"]),
            ("ts_sharpe_ratio", _D10.ts_sharpe_ratio, ["series", "window"]),
            ("ts_sortino_ratio", _D10.ts_sortino_ratio, ["series", "window"]),
            ("ts_calmar_ratio", _D10.ts_calmar_ratio, ["series", "window"]),
            ("ts_profit_factor", _D10.ts_profit_factor, ["series", "window"]),
            ("ts_omega_ratio", _D10.ts_omega_ratio, ["series", "window"]),
            ("ts_kelly_fraction", _D10.ts_kelly_fraction, ["series", "window"]),
            ("ts_worst_day", _D10.ts_worst_day, ["series", "window"]),
            ("ts_best_day", _D10.ts_best_day, ["series", "window"]),
            ("ts_win_rate", _D10.ts_win_rate, ["series", "window"]),
            ("ts_loss_rate", _D10.ts_loss_rate, ["series", "window"]),
            ("ts_avg_gain", _D10.ts_avg_gain, ["series", "window"]),
            ("ts_avg_loss", _D10.ts_avg_loss, ["series", "window"]),
            ("ts_expectancy", _D10.ts_expectancy, ["series", "window"]),
            ("ts_recovery_factor", _D10.ts_recovery_factor, ["series", "window"]),
            ("ts_risk_return_ratio", _D10.ts_risk_return_ratio, ["series", "window"]),
            ("ts_downside_deviation", _D10.ts_downside_deviation, ["series", "window"]),
            ("ts_vol_ratio_ewma", _D10.ts_vol_ratio_ewma, ["series", "short", "long"]),
            ("ts_realized_vol_pct", _D10.ts_realized_vol_pct, ["series", "window"]),
            ("ts_vol_zscore", _D10.ts_vol_zscore, ["series", "window"]),
            ("ts_vol_percentile", _D10.ts_vol_percentile, ["series", "window"]),
            ("ts_garch_proxy", _D10.ts_garch_proxy, ["series", "window"]),
            ("ts_vol_asymmetry", _D10.ts_vol_asymmetry, ["series", "window"]),
            ("ts_leverage_effect", _D10.ts_leverage_effect, ["series", "window"]),
            ("ts_baseline_vol", _D10.ts_baseline_vol, ["series", "window"]),
            ("ts_long_term_vol", _D10.ts_long_term_vol, ["series", "window"]),
            ("ts_short_term_vol", _D10.ts_short_term_vol, ["series", "window"]),
            ("ts_vol_term_structure", _D10.ts_vol_term_structure, ["series", "short", "long"]),
            ("ts_max_loss_ratio", _D10.ts_max_loss_ratio, ["series", "window"]),
            ("ts_beta_vol", _D10.ts_beta_vol, ["series", "short", "long"]),
        ]
        for name, func_d10, params in d10_ops:
            self.register(name, func_d10, "d10", params)

        # D11 算子族（2026-08-11 扩容二期）：技术指标族 60 算子（与 expr_dsl.registry 双注册表共享）
        from .ops_library import D11Ops as _D11

        d11_ops = [
            ("ts_ema_fast_slow", _D11.ts_ema_fast_slow, ["series", "short", "long"]),
            ("ts_macd", _D11.ts_macd, ["series", "short", "long"]),
            ("ts_macd_signal", _D11.ts_macd_signal, ["series", "short", "long", "signal"]),
            ("ts_macd_hist", _D11.ts_macd_hist, ["series", "short", "long", "signal"]),
            ("ts_dema", _D11.ts_dema, ["series", "span"]),
            ("ts_tema", _D11.ts_tema, ["series", "span"]),
            ("ts_kama", _D11.ts_kama, ["series", "window", "fast", "slow"]),
            ("ts_vwap", _D11.ts_vwap, ["close", "volume", "window"]),
            ("ts_rsi", _D11.ts_rsi, ["series", "window"]),
            ("ts_rsi_smoothed", _D11.ts_rsi_smoothed, ["series", "window"]),
            ("ts_stoch_k", _D11.ts_stoch_k, ["high", "low", "close", "window"]),
            ("ts_stoch_d", _D11.ts_stoch_d, ["high", "low", "close", "window", "smooth"]),
            ("ts_williams_r", _D11.ts_williams_r, ["high", "low", "close", "window"]),
            ("ts_cci", _D11.ts_cci, ["high", "low", "close", "window"]),
            ("ts_trix", _D11.ts_trix, ["series", "window"]),
            ("ts_ppo", _D11.ts_ppo, ["series", "short", "long"]),
            ("ts_tsi", _D11.ts_tsi, ["series", "short", "long"]),
            ("ts_awesome", _D11.ts_awesome, ["high", "low", "short", "long"]),
            ("ts_ultimate_osc", _D11.ts_ultimate_osc, ["high", "low", "close", "short", "mid", "long"]),
            ("ts_roc", _D11.ts_roc, ["series", "window"]),
            ("ts_momentum_index", _D11.ts_momentum_index, ["series", "window"]),
            ("ts_rate_of_change_ma", _D11.ts_rate_of_change_ma, ["series", "window"]),
            ("ts_fisher_transform", _D11.ts_fisher_transform, ["series", "window"]),
            ("ts_stoch_rsi", _D11.ts_stoch_rsi, ["series", "window"]),
            ("ts_rvi", _D11.ts_rvi, ["high", "low", "close", "window"]),
            ("ts_obv", _D11.ts_obv, ["close", "volume"]),
            ("ts_obv_ma", _D11.ts_obv_ma, ["close", "volume", "window"]),
            ("ts_mfi", _D11.ts_mfi, ["high", "low", "close", "volume", "window"]),
            ("ts_adi", _D11.ts_adi, ["high", "low", "close", "volume"]),
            ("ts_cmf", _D11.ts_cmf, ["high", "low", "close", "volume", "window"]),
            ("ts_chaikin_vol", _D11.ts_chaikin_vol, ["high", "low", "window"]),
            ("ts_chaikin_osc", _D11.ts_chaikin_osc, ["high", "low", "close", "volume", "short", "long"]),
            ("ts_volume_oscillator", _D11.ts_volume_oscillator, ["volume", "short", "long"]),
            ("ts_market_facilitation", _D11.ts_market_facilitation, ["high", "low", "volume"]),
            ("ts_atr", _D11.ts_atr, ["high", "low", "close", "window"]),
            ("ts_natr", _D11.ts_natr, ["high", "low", "close", "window"]),
            ("ts_bb_width", _D11.ts_bb_width, ["series", "window", "k"]),
            ("ts_bb_percent_b", _D11.ts_bb_percent_b, ["series", "window", "k"]),
            ("ts_bb_bandwidth", _D11.ts_bb_bandwidth, ["series", "window", "k"]),
            ("ts_price_channel", _D11.ts_price_channel, ["series", "window"]),
            ("ts_aroon_up", _D11.ts_aroon_up, ["series", "window"]),
            ("ts_aroon_down", _D11.ts_aroon_down, ["series", "window"]),
            ("ts_aroon_osc", _D11.ts_aroon_osc, ["series", "window"]),
            ("ts_dpo", _D11.ts_dpo, ["series", "window"]),
            ("ts_kst", _D11.ts_kst, ["series", "window"]),
            ("ts_kst_signal", _D11.ts_kst_signal, ["series", "window"]),
            ("ts_mass_index", _D11.ts_mass_index, ["high", "low", "window"]),
            ("ts_vortex_pos", _D11.ts_vortex_pos, ["high", "low", "close", "window"]),
            ("ts_vortex_neg", _D11.ts_vortex_neg, ["high", "low", "close", "window"]),
            ("ts_vortex_ratio", _D11.ts_vortex_ratio, ["high", "low", "close", "window"]),
            ("ts_ichimoku_conv", _D11.ts_ichimoku_conv, ["high", "low", "window"]),
            ("ts_ichimoku_base", _D11.ts_ichimoku_base, ["high", "low", "window"]),
            ("ts_ichimoku_span_a", _D11.ts_ichimoku_span_a, ["high", "low", "window"]),
            ("ts_ichimoku_span_b", _D11.ts_ichimoku_span_b, ["high", "low", "window"]),
            ("ts_sma_cross_signal", _D11.ts_sma_cross_signal, ["series", "short", "long"]),
            ("ts_ema_cross_signal", _D11.ts_ema_cross_signal, ["series", "short", "long"]),
            ("ts_parabolic_sar", _D11.ts_parabolic_sar, ["high", "low", "step", "max_step"]),
            ("ts_price_oscillator", _D11.ts_price_oscillator, ["series", "short", "long"]),
            ("ts_trend_score", _D11.ts_trend_score, ["series", "window"]),
            ("ts_cycle_score", _D11.ts_cycle_score, ["series", "window"]),
        ]
        for name, func_d11, params in d11_ops:
            self.register(name, func_d11, "d11", params)

        # D12 算子族（2026-08-11 扩容二期）：动量/趋势族 55 算子（与 expr_dsl.registry 双注册表共享）
        from .ops_library import D12Ops as _D12

        d12_ops = [
            ("ts_velocity", _D12.ts_velocity, ["series"]),
            ("ts_acceleration", _D12.ts_acceleration, ["series", "window"]),
            ("ts_jerk", _D12.ts_jerk, ["series", "window"]),
            ("ts_momentum_ratio", _D12.ts_momentum_ratio, ["series", "window"]),
            ("ts_momentum_breakout_ratio", _D12.ts_momentum_breakout_ratio, ["series", "window"]),
            ("ts_ewm_momentum", _D12.ts_ewm_momentum, ["series", "span"]),
            ("ts_momentum_vol_adj", _D12.ts_momentum_vol_adj, ["series", "window"]),
            ("ts_roc_zscore", _D12.ts_roc_zscore, ["series", "window"]),
            ("ts_velocity_zscore", _D12.ts_velocity_zscore, ["series", "window"]),
            ("ts_trend_angle", _D12.ts_trend_angle, ["series", "window"]),
            ("ts_linear_trend_score", _D12.ts_linear_trend_score, ["series", "window"]),
            ("ts_trend_strength_pct", _D12.ts_trend_strength_pct, ["series", "window"]),
            ("ts_above_ma_ratio", _D12.ts_above_ma_ratio, ["series", "window"]),
            ("ts_below_ma_ratio", _D12.ts_below_ma_ratio, ["series", "window"]),
            ("ts_slope_change", _D12.ts_slope_change, ["series", "window"]),
            ("ts_curvature", _D12.ts_curvature, ["series", "window"]),
            ("ts_momentum_consistency", _D12.ts_momentum_consistency, ["series", "window"]),
            ("ts_trend_persistence", _D12.ts_trend_persistence, ["series", "window"]),
            ("ts_reversal_signal_z", _D12.ts_reversal_signal_z, ["series", "window"]),
            ("ts_trend_strength_ma", _D12.ts_trend_strength_ma, ["series", "short", "long"]),
            ("ts_relative_strength", _D12.ts_relative_strength, ["series", "window"]),
            ("ts_cross_momentum", _D12.ts_cross_momentum, ["series", "short", "long"]),
            ("ts_momentum_regime", _D12.ts_momentum_regime, ["series", "window"]),
            ("ts_trend_filter", _D12.ts_trend_filter, ["series", "window"]),
            ("ts_higher_high_count", _D12.ts_higher_high_count, ["series", "window"]),
            ("ts_lower_low_count", _D12.ts_lower_low_count, ["series", "window"]),
            ("ts_new_high_ratio", _D12.ts_new_high_ratio, ["series", "window"]),
            ("ts_new_low_ratio", _D12.ts_new_low_ratio, ["series", "window"]),
            ("ts_range_expansion", _D12.ts_range_expansion, ["series", "window"]),
            ("ts_breakout_distance", _D12.ts_breakout_distance, ["series", "window"]),
            ("ts_pullback_depth", _D12.ts_pullback_depth, ["series", "window"]),
            ("ts_continuation_signal", _D12.ts_continuation_signal, ["series", "window"]),
            ("ts_exhaustion_signal", _D12.ts_exhaustion_signal, ["series", "window"]),
            ("ts_donchian_break", _D12.ts_donchian_break, ["high", "low", "window"]),
            ("ts_donchian_mid", _D12.ts_donchian_mid, ["high", "low", "window"]),
            ("ts_supertrend_signal", _D12.ts_supertrend_signal, ["series", "high", "low", "window", "mult"]),
            ("ts_psar_position", _D12.ts_psar_position, ["high", "low", "step", "max_step"]),
            ("ts_uptrend_flag", _D12.ts_uptrend_flag, ["series", "window"]),
            ("ts_downtrend_flag", _D12.ts_downtrend_flag, ["series", "window"]),
            ("ts_sideways_flag", _D12.ts_sideways_flag, ["series", "window"]),
            ("ts_trend_direction_strength", _D12.ts_trend_direction_strength, ["series", "window"]),
            ("ts_multi_tf_trend", _D12.ts_multi_tf_trend, ["series", "short", "mid", "long"]),
            ("ts_fractal_up", _D12.ts_fractal_up, ["high", "window"]),
            ("ts_fractal_down", _D12.ts_fractal_down, ["low", "window"]),
            ("ts_support_proximity", _D12.ts_support_proximity, ["series", "window"]),
            ("ts_resistance_proximity", _D12.ts_resistance_proximity, ["series", "window"]),
            ("ts_breakout_pullback_signal", _D12.ts_breakout_pullback_signal, ["series", "window"]),
            ("ts_directional_up", _D12.ts_directional_up, ["high", "low", "window"]),
            ("ts_directional_down", _D12.ts_directional_down, ["high", "low", "window"]),
            ("ts_adx_pos", _D12.ts_adx_pos, ["high", "low", "close", "window"]),
            ("ts_adx_neg", _D12.ts_adx_neg, ["high", "low", "close", "window"]),
            ("ts_adx", _D12.ts_adx, ["high", "low", "close", "window"]),
            ("ts_adx_wilder", _D12.ts_adx_wilder, ["high", "low", "close", "window"]),
            ("ts_atr_ratio", _D12.ts_atr_ratio, ["high", "low", "close", "window"]),
            ("ts_trend_vol_ratio", _D12.ts_trend_vol_ratio, ["series", "window"]),
            ("ts_trend_entropy", _D12.ts_trend_entropy, ["series", "window"]),
            ("ts_up_down_strength", _D12.ts_up_down_strength, ["series", "window"]),
        ]
        for name, func_d12, params in d12_ops:
            self.register(name, func_d12, "d12", params)

        # D13 算子族（2026-08-11 扩容二期）：截面/排名族 45 算子（与 expr_dsl.registry 双注册表共享）
        from .ops_library import D13Ops as _D13

        d13_ops = [
            ("cs_rank_pct", _D13.cs_rank_pct, ["series", "window"]),
            ("cs_percent_rank", _D13.cs_percent_rank, ["series", "window"]),
            ("cs_rank_demean", _D13.cs_rank_demean, ["series", "window"]),
            ("cs_inverse_rank", _D13.cs_inverse_rank, ["series", "window"]),
            ("cs_signed_rank", _D13.cs_signed_rank, ["series", "window"]),
            ("cs_rank_ratio", _D13.cs_rank_ratio, ["series", "window"]),
            ("cs_cross_rank_diff", _D13.cs_cross_rank_diff, ["series", "window"]),
            ("cs_rank_momentum", _D13.cs_rank_momentum, ["series", "window"]),
            ("cs_rank_volatility", _D13.cs_rank_volatility, ["series", "window"]),
            ("cs_rank_stability", _D13.cs_rank_stability, ["series", "window"]),
            ("cs_ewm_rank", _D13.cs_ewm_rank, ["series", "span"]),
            ("cs_smooth_rank", _D13.cs_smooth_rank, ["series", "window"]),
            ("cs_robust_rank", _D13.cs_robust_rank, ["series", "window"]),
            ("cs_quantile_rank", _D13.cs_quantile_rank, ["series", "window"]),
            ("cs_cross_section_bucket", _D13.cs_cross_section_bucket, ["series", "window", "n_buckets"]),
            ("cs_zscore_med", _D13.cs_zscore_med, ["series", "window"]),
            ("cs_mad_zscore", _D13.cs_mad_zscore, ["series", "window"]),
            ("cs_winsor_z", _D13.cs_winsor_z, ["series", "window", "k"]),
            ("cs_normalize_01", _D13.cs_normalize_01, ["series", "window"]),
            ("cs_minmax_norm", _D13.cs_minmax_norm, ["series", "window"]),
            ("cs_softmax_weight", _D13.cs_softmax_weight, ["series", "window"]),
            ("cs_distance_median", _D13.cs_distance_median, ["series", "window"]),
            ("cs_distance_mean", _D13.cs_distance_mean, ["series", "window"]),
            ("cs_relative_to_max", _D13.cs_relative_to_max, ["series", "window"]),
            ("cs_relative_to_min", _D13.cs_relative_to_min, ["series", "window"]),
            ("cs_max_share", _D13.cs_max_share, ["series", "window"]),
            ("cs_trim_mean_diff", _D13.cs_trim_mean_diff, ["series", "window"]),
            ("cs_market_relative", _D13.cs_market_relative, ["series", "window"]),
            ("cs_dispersion", _D13.cs_dispersion, ["series", "window"]),
            ("cs_coefficient_variation", _D13.cs_coefficient_variation, ["series", "window"]),
            ("cs_gini_score", _D13.cs_gini_score, ["series", "window"]),
            ("cs_herfindahl", _D13.cs_herfindahl, ["series", "window"]),
            ("cs_concentration", _D13.cs_concentration, ["series", "window"]),
            ("cs_top_bottom_spread", _D13.cs_top_bottom_spread, ["series", "window"]),
            ("cs_winner_loser_gap", _D13.cs_winner_loser_gap, ["series", "window"]),
            ("cs_median_gap", _D13.cs_median_gap, ["series", "window"]),
            ("cs_extreme_strength", _D13.cs_extreme_strength, ["series", "window"]),
            ("cs_outlier_flag", _D13.cs_outlier_flag, ["series", "window", "k"]),
            ("cs_tail_weight", _D13.cs_tail_weight, ["series", "window"]),
            ("cs_skewness_score", _D13.cs_skewness_score, ["series", "window"]),
            ("cs_kurtosis_score", _D13.cs_kurtosis_score, ["series", "window"]),
            ("cs_extreme_skew", _D13.cs_extreme_skew, ["series", "window"]),
            ("cs_breadth_position", _D13.cs_breadth_position, ["series", "window"]),
            ("cs_entropy_rank", _D13.cs_entropy_rank, ["series", "window"]),
            ("cs_outlier_ratio", _D13.cs_outlier_ratio, ["series", "window", "k"]),
        ]
        for name, func_d13, params in d13_ops:
            self.register(name, func_d13, "d13", params)

        # D14 算子族（2026-08-11 扩容二期）：条件/事件族 40 算子（与 expr_dsl.registry 双注册表共享）
        from .ops_library import D14Ops as _D14

        d14_ops = [
            ("ts_cross_threshold_up", _D14.ts_cross_threshold_up, ["series", "threshold"]),
            ("ts_cross_threshold_down", _D14.ts_cross_threshold_down, ["series", "threshold"]),
            ("ts_threshold_band", _D14.ts_threshold_band, ["series", "lo", "hi"]),
            ("ts_range_condition", _D14.ts_range_condition, ["series", "lo", "hi"]),
            ("ts_condition_count", _D14.ts_condition_count, ["series", "threshold", "window"]),
            ("ts_condition_ratio", _D14.ts_condition_ratio, ["series", "threshold", "window"]),
            ("ts_consecutive_above", _D14.ts_consecutive_above, ["series", "threshold"]),
            ("ts_consecutive_below", _D14.ts_consecutive_below, ["series", "threshold"]),
            ("ts_consecutive_increase", _D14.ts_consecutive_increase, ["series"]),
            ("ts_consecutive_decrease", _D14.ts_consecutive_decrease, ["series"]),
            ("ts_consecutive_same_sign", _D14.ts_consecutive_same_sign, ["series"]),
            ("ts_condition_change", _D14.ts_condition_change, ["series", "threshold"]),
            ("ts_condition_switch_rate", _D14.ts_condition_switch_rate, ["series", "threshold", "window"]),
            ("ts_state_duration", _D14.ts_state_duration, ["series", "threshold"]),
            ("ts_state_age", _D14.ts_state_age, ["series", "threshold"]),
            ("ts_breakout_event", _D14.ts_breakout_event, ["series", "window"]),
            ("ts_breakdown_event", _D14.ts_breakdown_event, ["series", "window"]),
            ("ts_cross_ma_event", _D14.ts_cross_ma_event, ["series", "window"]),
            ("ts_golden_cross_event", _D14.ts_golden_cross_event, ["series", "short", "long"]),
            ("ts_death_cross_event", _D14.ts_death_cross_event, ["series", "short", "long"]),
            ("ts_turning_point", _D14.ts_turning_point, ["series", "window"]),
            ("ts_zigzag_direction", _D14.ts_zigzag_direction, ["series", "window"]),
            ("ts_event_density", _D14.ts_event_density, ["series", "window"]),
            ("ts_event_count_n", _D14.ts_event_count_n, ["series", "window"]),
            ("ts_signal_persistence", _D14.ts_signal_persistence, ["series", "threshold", "window"]),
            ("ts_signal_decay", _D14.ts_signal_decay, ["series", "threshold", "window"]),
            ("ts_condition_entropy", _D14.ts_condition_entropy, ["series", "threshold", "window"]),
            ("ts_pattern_continuation", _D14.ts_pattern_continuation, ["series", "window"]),
            ("ts_pattern_reversal", _D14.ts_pattern_reversal, ["series", "window"]),
            ("ts_momentum_filter", _D14.ts_momentum_filter, ["series", "window"]),
            ("ts_volatility_filter", _D14.ts_volatility_filter, ["series", "window"]),
            ("ts_liquidity_filter", _D14.ts_liquidity_filter, ["series", "window"]),
            ("ts_trend_condition", _D14.ts_trend_condition, ["series", "window"]),
            ("ts_breakout_condition", _D14.ts_breakout_condition, ["series", "window"]),
            ("ts_reversal_condition", _D14.ts_reversal_condition, ["series", "window"]),
            ("ts_level_test", _D14.ts_level_test, ["series", "window"]),
            ("ts_support_break", _D14.ts_support_break, ["series", "window"]),
            ("ts_resistance_break", _D14.ts_resistance_break, ["series", "window"]),
            ("ts_condition_combo", _D14.ts_condition_combo, ["series", "window"]),
            ("ts_breakout_strength", _D14.ts_breakout_strength, ["series", "window"]),
        ]
        for name, func_d14, params in d14_ops:
            self.register(name, func_d14, "d14", params)

        # D15 算子族（2026-08-11 扩容二期）：组合/跨序列族 50 算子（与 expr_dsl.registry 双注册表共享）
        from .ops_library import D15Ops as _D15

        d15_ops = [
            ("cs_ratio", _D15.cs_ratio, ["x", "y"]),
            ("cs_diff", _D15.cs_diff, ["x", "y"]),
            ("cs_sum", _D15.cs_sum, ["x", "y"]),
            ("cs_product", _D15.cs_product, ["x", "y"]),
            ("cs_min", _D15.cs_min, ["x", "y"]),
            ("cs_max", _D15.cs_max, ["x", "y"]),
            ("cs_spread", _D15.cs_spread, ["x", "y"]),
            ("cs_return_spread", _D15.cs_return_spread, ["x", "y"]),
            ("cs_relative_ratio", _D15.cs_relative_ratio, ["x", "y", "window"]),
            ("cs_log_ratio", _D15.cs_log_ratio, ["x", "y"]),
            ("cs_pct_diff", _D15.cs_pct_diff, ["x", "y"]),
            ("cs_weighted_average", _D15.cs_weighted_average, ["x", "y", "w"]),
            ("cs_composite_score", _D15.cs_composite_score, ["x", "y", "window"]),
            ("cs_normalized_ratio", _D15.cs_normalized_ratio, ["x", "y", "window"]),
            ("cs_smoothed_ratio", _D15.cs_smoothed_ratio, ["x", "y", "window"]),
            ("cs_exponential_ratio", _D15.cs_exponential_ratio, ["x", "y", "span"]),
            ("cs_ratio_ma", _D15.cs_ratio_ma, ["x", "y", "window"]),
            ("cs_ratio_zscore", _D15.cs_ratio_zscore, ["x", "y", "window"]),
            ("cs_relative_strength_ratio", _D15.cs_relative_strength_ratio, ["x", "y", "window"]),
            ("ts_pair_corr", _D15.ts_pair_corr, ["x", "y", "window"]),
            ("ts_cov", _D15.ts_cov, ["x", "y", "window"]),
            ("ts_beta", _D15.ts_beta, ["x", "y", "window"]),
            ("ts_alpha", _D15.ts_alpha, ["x", "y", "window"]),
            ("ts_lead_lag_corr", _D15.ts_lead_lag_corr, ["x", "y", "window"]),
            ("ts_cross_corr_lag1", _D15.ts_cross_corr_lag1, ["x", "y", "window"]),
            ("ts_granger_proxy", _D15.ts_granger_proxy, ["x", "y", "window"]),
            ("ts_hedge_ratio", _D15.ts_hedge_ratio, ["x", "y", "window"]),
            ("ts_cointegration_proxy", _D15.ts_cointegration_proxy, ["x", "y", "window"]),
            ("ts_spread_zscore", _D15.ts_spread_zscore, ["x", "y", "window"]),
            ("ts_spread_band", _D15.ts_spread_band, ["x", "y", "window"]),
            ("ts_pair_divergence", _D15.ts_pair_divergence, ["x", "y", "window"]),
            ("ts_pair_convergence", _D15.ts_pair_convergence, ["x", "y", "window"]),
            ("ts_convergence_rate", _D15.ts_convergence_rate, ["x", "y", "window"]),
            ("ts_pair_trade_signal", _D15.ts_pair_trade_signal, ["x", "y", "window", "k"]),
            ("ts_price_gap", _D15.ts_price_gap, ["open_p", "close"]),
            ("ts_overnight_return", _D15.ts_overnight_return, ["open_p", "close"]),
            ("ts_intraday_return", _D15.ts_intraday_return, ["open_p", "close"]),
            ("ts_open_close_diff", _D15.ts_open_close_diff, ["open_p", "close"]),
            ("ts_high_low_ratio", _D15.ts_high_low_ratio, ["high", "low"]),
            ("ts_range_ratio", _D15.ts_range_ratio, ["high", "low", "close", "window"]),
            ("ts_basis", _D15.ts_basis, ["spot", "future"]),
            ("ts_basis_ratio", _D15.ts_basis_ratio, ["spot", "future"]),
            ("ts_term_spread", _D15.ts_term_spread, ["near", "far"]),
            ("ts_roll_yield", _D15.ts_roll_yield, ["near", "far", "window"]),
            ("ts_volume_price_corr", _D15.ts_volume_price_corr, ["close", "volume", "window"]),
            ("ts_volume_ratio_vs_avg", _D15.ts_volume_ratio_vs_avg, ["volume", "window"]),
            ("ts_volume_breakout", _D15.ts_volume_breakout, ["volume", "window"]),
            ("ts_volume_zscore", _D15.ts_volume_zscore, ["volume", "window"]),
            ("ts_price_volume_sync", _D15.ts_price_volume_sync, ["close", "volume", "window"]),
            ("ts_amount_velocity", _D15.ts_amount_velocity, ["amount", "window"]),
        ]
        for name, func_d15, params in d15_ops:
            self.register(name, func_d15, "d15", params)

        # D16 算子族（2026-08-11 扩容二期）：量价/流动性族 40 算子（与 expr_dsl.registry 双注册表共享）
        from .ops_library import D16Ops as _D16

        d16_ops = [
            ("ts_amihud_illiquidity", _D16.ts_amihud_illiquidity, ["close", "amount", "window"]),
            ("ts_turnover", _D16.ts_turnover, ["volume", "float_shares", "window"]),
            ("ts_liquidity_ratio", _D16.ts_liquidity_ratio, ["volume", "close", "window"]),
            ("ts_liquidity_zscore", _D16.ts_liquidity_zscore, ["volume", "window"]),
            ("ts_liquidity_risk", _D16.ts_liquidity_risk, ["volume", "window"]),
            ("ts_float_turnover", _D16.ts_float_turnover, ["volume", "float_shares"]),
            ("ts_dollar_volume", _D16.ts_dollar_volume, ["close", "volume"]),
            ("ts_bid_ask_spread_proxy", _D16.ts_bid_ask_spread_proxy, ["high", "low", "close", "window"]),
            ("ts_trading_intensity", _D16.ts_trading_intensity, ["volume", "window"]),
            ("ts_tick_size_proxy", _D16.ts_tick_size_proxy, ["close", "window"]),
            ("ts_price_impact", _D16.ts_price_impact, ["close", "volume", "window"]),
            ("ts_liquidity_premium", _D16.ts_liquidity_premium, ["close", "volume", "window"]),
            ("ts_volume_price_trend", _D16.ts_volume_price_trend, ["close", "volume"]),
            ("ts_money_flow_ratio", _D16.ts_money_flow_ratio, ["close", "volume", "window"]),
            ("ts_force_index", _D16.ts_force_index, ["close", "volume", "window"]),
            ("ts_ease_of_movement", _D16.ts_ease_of_movement, ["high", "low", "close", "volume", "window"]),
            ("ts_volume_price_regime", _D16.ts_volume_price_regime, ["close", "volume", "window"]),
            ("ts_volume_pressure_ratio", _D16.ts_volume_pressure_ratio, ["close", "volume", "window"]),
            ("ts_volume_price_corr_lag", _D16.ts_volume_price_corr_lag, ["close", "volume", "window"]),
            ("ts_order_flow_proxy", _D16.ts_order_flow_proxy, ["close", "volume", "window"]),
            ("ts_volume_change_rate", _D16.ts_volume_change_rate, ["volume", "window"]),
            ("ts_volume_momentum", _D16.ts_volume_momentum, ["volume", "window"]),
            ("ts_volume_acceleration", _D16.ts_volume_acceleration, ["volume", "window"]),
            ("ts_volume_ma_ratio", _D16.ts_volume_ma_ratio, ["volume", "short", "long"]),
            ("ts_volume_std_ratio", _D16.ts_volume_std_ratio, ["volume", "short", "long"]),
            ("ts_volume_skewness", _D16.ts_volume_skewness, ["volume", "window"]),
            ("ts_volume_kurtosis", _D16.ts_volume_kurtosis, ["volume", "window"]),
            ("ts_volume_autocorr", _D16.ts_volume_autocorr, ["volume", "window"]),
            ("ts_volume_entropy", _D16.ts_volume_entropy, ["volume", "window"]),
            ("ts_volume_concentration", _D16.ts_volume_concentration, ["volume", "window"]),
            ("ts_volume_cycle", _D16.ts_volume_cycle, ["volume", "window"]),
            ("ts_volume_breakout_ratio", _D16.ts_volume_breakout_ratio, ["volume", "window"]),
            ("ts_volume_surge", _D16.ts_volume_surge, ["volume", "window", "k"]),
            ("ts_volume_shrinkage", _D16.ts_volume_shrinkage, ["volume", "window"]),
            ("ts_volume_spike", _D16.ts_volume_spike, ["volume", "window"]),
            ("ts_volume_cluster", _D16.ts_volume_cluster, ["volume", "window"]),
            ("ts_trade_value_ratio", _D16.ts_trade_value_ratio, ["amount", "window"]),
            ("ts_turnover_zscore", _D16.ts_turnover_zscore, ["volume", "float_shares", "window"]),
            ("ts_volume_weighted_return", _D16.ts_volume_weighted_return, ["close", "volume", "window"]),
            ("ts_price_volume_divergence_score", _D16.ts_price_volume_divergence_score, ["close", "volume", "window"]),
        ]
        for name, func_d16, params in d16_ops:
            self.register(name, func_d16, "d16", params)

        # D17 算子族（2026-08-11 扩容二期）：市场结构/分布族 35 算子（与 expr_dsl.registry 双注册表共享）
        from .ops_library import D17Ops as _D17

        d17_ops = [
            ("ts_market_breadth", _D17.ts_market_breadth, ["series", "window"]),
            ("ts_advance_decline_ratio", _D17.ts_advance_decline_ratio, ["series", "window"]),
            ("ts_new_high_low_ratio", _D17.ts_new_high_low_ratio, ["series", "window"]),
            ("ts_breadth_momentum", _D17.ts_breadth_momentum, ["series", "window"]),
            ("ts_breadth_divergence", _D17.ts_breadth_divergence, ["series", "window"]),
            ("ts_sector_rotation_score", _D17.ts_sector_rotation_score, ["series", "window"]),
            ("ts_concentration_index", _D17.ts_concentration_index, ["series", "window"]),
            ("ts_diversification_index", _D17.ts_diversification_index, ["series", "window"]),
            ("ts_correlation_regime", _D17.ts_correlation_regime, ["series", "window"]),
            ("ts_market_dispersion", _D17.ts_market_dispersion, ["series", "window"]),
            ("ts_cross_section_momentum", _D17.ts_cross_section_momentum, ["series", "window"]),
            ("ts_cross_section_reversal", _D17.ts_cross_section_reversal, ["series", "window"]),
            ("ts_size_premium_proxy", _D17.ts_size_premium_proxy, ["series", "window"]),
            ("ts_value_premium_proxy", _D17.ts_value_premium_proxy, ["series", "window"]),
            ("ts_momentum_factor_proxy", _D17.ts_momentum_factor_proxy, ["series", "window"]),
            ("ts_low_vol_factor_proxy", _D17.ts_low_vol_factor_proxy, ["series", "window"]),
            ("ts_quality_factor_proxy", _D17.ts_quality_factor_proxy, ["series", "window"]),
            ("ts_sentiment_score", _D17.ts_sentiment_score, ["series", "window"]),
            ("ts_risk_appetite", _D17.ts_risk_appetite, ["series", "window"]),
            ("ts_fear_greed_index", _D17.ts_fear_greed_index, ["series", "window"]),
            ("ts_momentum_crowding", _D17.ts_momentum_crowding, ["series", "window"]),
            ("ts_position_extreme", _D17.ts_position_extreme, ["series", "window"]),
            ("ts_herding_proxy", _D17.ts_herding_proxy, ["series", "window"]),
            ("ts_implied_vol_proxy", _D17.ts_implied_vol_proxy, ["series", "window"]),
            ("ts_risk_reversal_proxy", _D17.ts_risk_reversal_proxy, ["series", "window"]),
            ("ts_smile_proxy", _D17.ts_smile_proxy, ["series", "window"]),
            ("ts_market_regime_score", _D17.ts_market_regime_score, ["series", "window"]),
            ("ts_trend_regime_proxy", _D17.ts_trend_regime_proxy, ["series", "window"]),
            ("ts_volatility_regime_proxy", _D17.ts_volatility_regime_proxy, ["series", "window"]),
            ("ts_liquidity_regime_proxy", _D17.ts_liquidity_regime_proxy, ["volume", "window"]),
            ("ts_market_timing_score", _D17.ts_market_timing_score, ["series", "window"]),
            ("ts_regime_confidence", _D17.ts_regime_confidence, ["series", "window"]),
            ("ts_regime_persistence", _D17.ts_regime_persistence, ["series", "window"]),
            ("ts_regime_transition_prob", _D17.ts_regime_transition_prob, ["series", "window"]),
            ("ts_market_phase", _D17.ts_market_phase, ["series", "window"]),
        ]
        for name, func_d17, params in d17_ops:
            self.register(name, func_d17, "d17", params)

        logger.info("初始化内置算子: %d 个", self.operator_count)


# ─── 特征工程中台主引擎 ─────────────────────────────────────


class C8Ops:
    """C8 算子扩容原语（2026-08-11）— 22 个高价值算子单一实现。

    设计约束:
        - 全部单序列输入（对齐 FTS-Expr DSL 执行语义，逐日截面由执行器分派）
        - 滚动窗口 NaN 兜底（min_periods=2 或 fillna），不抛异常
        - 与 expr_dsl.registry 双注册表共享（verify_registry_consistency 强制一致）
    """

    # ── L1 时序统计 ─────────────────────────────────────

    @staticmethod
    def ts_argmin(series: pd.Series, window: int = 20) -> pd.Series:
        """窗口内最小值位置（滞后形态，0=最新）。"""
        return _rolling_series_out(_ts_argmin_vec, series, window)

    @staticmethod
    def ts_ema(series: pd.Series, span: int = 20) -> pd.Series:
        """指数移动平均（span 半衰期，趋势平滑）。"""
        return series.ewm(span=span, adjust=False).mean()

    @staticmethod
    def ts_mad(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动中位数绝对偏差（稳健离散度）。"""
        med = series.rolling(window, min_periods=2).median()
        return (series - med).abs().rolling(window, min_periods=2).mean()

    @staticmethod
    def ts_range(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动振幅 (max−min)/mean（相对波动，无量纲）。"""
        mean = series.rolling(window, min_periods=2).mean().replace(0.0, np.nan)
        rng = series.rolling(window, min_periods=2).max() - series.rolling(window, min_periods=2).min()
        return (rng / mean).fillna(0.0)

    @staticmethod
    def ts_iqr(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动四分位距（q75−q25，稳健离散度）。"""
        return series.rolling(window, min_periods=2).quantile(0.75) - series.rolling(window, min_periods=2).quantile(
            0.25
        )

    @staticmethod
    def ts_quantile_range(series: pd.Series, window: int = 20, q_hi: float = 0.9, q_lo: float = 0.1) -> pd.Series:
        """滚动分位差（q_hi − q_lo，尾部宽度）。"""
        return series.rolling(window, min_periods=2).quantile(q_hi) - series.rolling(window, min_periods=2).quantile(
            q_lo
        )

    @staticmethod
    def ts_return_over_max(series: pd.Series, window: int = 20) -> pd.Series:
        """距滚动高点回撤（x/max − 1 ≤ 0，回调深度）。"""
        mx = series.rolling(window, min_periods=2).max()
        return series / mx.replace(0.0, np.nan) - 1.0

    @staticmethod
    def ts_min_max_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动 max/min − 1（区间幅度）。"""
        mx = series.rolling(window, min_periods=2).max()
        mn = series.rolling(window, min_periods=2).min()
        return (mx / mn.replace(0.0, np.nan) - 1.0).fillna(0.0)

    @staticmethod
    def ts_std_ratio(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """短/长波动比（短期相对长期波动，均值回归强度代理）。"""
        short_std = series.rolling(short, min_periods=2).std()
        long_std = series.rolling(long, min_periods=2).std()
        return short_std / long_std.replace(0.0, np.nan)

    @staticmethod
    def ts_roc_sum(series: pd.Series, window: int = 20) -> pd.Series:
        """窗口收益率累加（累积动量）。"""
        return series.pct_change().rolling(window, min_periods=2).sum()

    @staticmethod
    def ts_breakout(series: pd.Series, window: int = 20) -> pd.Series:
        """突破滚动新高（t 日价 > 前 n 日最高 → 1，事件信号）。"""
        prior_max = series.shift(1).rolling(window, min_periods=2).max()
        return (series > prior_max).astype(float)

    @staticmethod
    def ts_cumulative_return(series: pd.Series, window: int = 20) -> pd.Series:
        """n 期累计收益（x / x[t−n] − 1）。"""
        return (series / series.shift(window).replace(0.0, np.nan) - 1.0).fillna(0.0)

    # ── L2 截面（单序列滚动语义，逐日截面由执行器分派） ──

    @staticmethod
    def cs_rank_diff(series: pd.Series, window: int = 1) -> pd.Series:
        """截面排名变化（rank 的时序差分，排名动量）。"""
        r = PriceOps.rank(series)
        return r - r.shift(window)

    @staticmethod
    def cs_zscore_diff(series: pd.Series, window: int = 1) -> pd.Series:
        """截面 zscore 变化（标准化值的时序差分）。"""
        z = PriceOps.zscore(series)
        return z - z.shift(window)

    @staticmethod
    def cs_extreme_ratio(series: pd.Series, window: int = 20, n_std: float = 2.0) -> pd.Series:
        """窗口内 |x| 超 n_std·std 的占比（极端值密度）。"""
        mean = series.rolling(window, min_periods=2).mean()
        std = series.rolling(window, min_periods=2).std()
        mask = (series - mean).abs() > n_std * std.replace(0.0, np.nan)
        return mask.astype(float).rolling(window, min_periods=2).mean().fillna(0.0)

    @staticmethod
    def cs_median_dev(series: pd.Series, window: int = 20) -> pd.Series:
        """与滚动中位数的偏离（中位数去均值后的水平）。"""
        med = series.rolling(window, min_periods=2).median()
        return series - med

    # ── L3 条件 ──────────────────────────────────────────

    @staticmethod
    def where_gt(series: pd.Series, threshold: float, a: float = 1.0, b: float = 0.0) -> pd.Series:
        """条件选值：x > threshold 取 a，否则 b（受控条件因子）。"""
        return series.where(series > threshold, b).where(series <= threshold, a)

    @staticmethod
    def consecutive_true(cond: pd.Series, window: int = 20) -> pd.Series:
        """连续满足条件计数（连续 True 天数，持续性信号）。"""
        if not isinstance(cond, pd.Series):
            return pd.Series(0, index=range(len(cond)))
        c = cond.fillna(False).astype(bool).astype(int)
        return c.groupby((c == 0).cumsum()).cumsum().clip(upper=window)

    @staticmethod
    def sign_flip(series: pd.Series, window: int = 20) -> pd.Series:
        """窗口内符号翻转计数（方向稳定性/反转信号）。"""
        s = np.sign(series).fillna(0.0)
        flip = (s.diff() != 0).astype(float)
        # 首行 diff NaN → 0
        flip.iloc[0] = 0.0
        return flip.rolling(window, min_periods=2).sum()

    # ── L5 领域 ──────────────────────────────────────────

    @staticmethod
    def mean_reversion_z(series: pd.Series, window: int = 20) -> pd.Series:
        """均值回归强度（−滚动 zscore：高→超买，预期回落）。"""
        return -RollingOps.ts_zscore(series, window=window)

    @staticmethod
    def trend_strength(series: pd.Series, window: int = 20) -> pd.Series:
        """趋势强度（|回归斜率| 归一化到 [0,1]）。"""
        slope = RollingOps.ts_slope(series, window=window)
        return (slope.abs() / (series.abs().rolling(window, min_periods=2).mean().replace(0.0, np.nan) + 1e-10)).clip(
            0.0, 1.0
        )

    @staticmethod
    def volume_pressure(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """量价压力（量比 × 涨跌幅：放量上涨/缩量下跌的方向压力）。"""
        ret = PriceOps.pct_change(close, 1)
        vol_ratio = volume / volume.rolling(window, min_periods=2).mean().replace(0.0, np.nan)
        return (vol_ratio * ret).fillna(0.0)


class C9Ops:
    """C9 算子扩容原语（2026-08-11）— 30 个高价值算子单一实现（102→132）。

    设计约束（对齐 C8Ops）:
        - 全部单序列输入（多序列算子 volume/close 成对传参），对齐 FTS-Expr DSL 执行语义
        - 滚动窗口 NaN 兜底（min_periods 或 fillna），不抛异常
        - 与 expr_dsl.registry 双注册表共享（verify_registry_consistency 强制一致）
    """

    # ── L1 时序统计 ─────────────────────────────────────

    @staticmethod
    def ts_pct_rank_window(series: pd.Series, window: int = 20) -> pd.Series:
        """窗口内当前值百分位（(x−min)/(max−min)，区间极值取 0.5 兜底）。"""
        mx = series.rolling(window, min_periods=2).max()
        mn = series.rolling(window, min_periods=2).min()
        span = (mx - mn).replace(0.0, np.nan)
        return ((series - mn) / span).fillna(0.5)

    @staticmethod
    def ts_zscore_rolling(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动 zscore（标准化位置，均值回归强度）。"""
        return RollingOps.ts_zscore(series, window=window)

    @staticmethod
    def ts_skew(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动偏度（分布不对称性，样本不足/常数降级 0）。"""
        return series.rolling(window, min_periods=3).skew().fillna(0.0)

    @staticmethod
    def ts_kurt(series: pd.Series, window: int = 20) -> pd.Series:
        """滚动峰度（分布厚尾，样本不足/常数序列降级 0）。"""
        std = series.rolling(window, min_periods=4).std().replace(0.0, np.nan)
        kurt = series.rolling(window, min_periods=4).kurt().fillna(0.0)
        return kurt.where(std.notna(), 0.0)

    @staticmethod
    def ts_slope_pct(series: pd.Series, window: int = 20) -> pd.Series:
        """线性斜率占价格比（斜率/|均值|，无量纲趋势强度）。"""
        slope = RollingOps.ts_slope(series, window=window)
        mean = series.rolling(window, min_periods=2).mean().abs().replace(0.0, np.nan)
        return (slope / mean).fillna(0.0)

    @staticmethod
    def ts_position_in_range(series: pd.Series, window: int = 20) -> pd.Series:
        """区间位置 (x−min)/(max−min) ∈[0,1]（现价在窗口内的相对高低）。"""
        mx = series.rolling(window, min_periods=2).max()
        mn = series.rolling(window, min_periods=2).min()
        span = (mx - mn).replace(0.0, np.nan)
        return ((series - mn) / span).clip(0.0, 1.0).fillna(0.5)

    @staticmethod
    def ts_down_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """窗口内下跌天数占比（负收益占比，弱势强度）。"""
        return (series.diff() < 0).astype(float).rolling(window, min_periods=2).mean()

    @staticmethod
    def ts_up_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """窗口内上涨天数占比（正收益占比，强势强度）。"""
        return (series.diff() > 0).astype(float).rolling(window, min_periods=2).mean()

    @staticmethod
    def ts_gain_loss_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """涨跌幅比（窗口平均涨/平均跌，无跌时 0 兜底）。"""
        ret = series.diff()
        gain = ret.clip(lower=0.0).rolling(window, min_periods=2).mean()
        loss = (-ret.clip(upper=0.0)).rolling(window, min_periods=2).mean().replace(0.0, np.nan)
        return (gain / loss).fillna(0.0)

    @staticmethod
    def ts_bias_ma(series: pd.Series, window: int = 20) -> pd.Series:
        """乖离率 (x/MA − 1)（偏离均值的程度与方向）。"""
        ma = series.rolling(window, min_periods=2).mean().replace(0.0, np.nan)
        return series / ma - 1.0

    @staticmethod
    def ts_boll_position(series: pd.Series, window: int = 20, k: float = 2.0) -> pd.Series:
        """布林带位置 (x−μ)/(2kσ) 截断 ∈[−0.5,0.5]（标准化波动位置）。"""
        mean = series.rolling(window, min_periods=2).mean()
        std = series.rolling(window, min_periods=2).std().replace(0.0, np.nan)
        return ((series - mean) / (2 * k * std)).clip(-0.5, 0.5).fillna(0.0)

    @staticmethod
    def ts_ma_diff(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """双均线差 (短MA/长MA − 1)（趋势强弱与方向）。"""
        sma = series.rolling(short, min_periods=2).mean()
        lma = series.rolling(long, min_periods=2).mean().replace(0.0, np.nan)
        return sma / lma - 1.0

    @staticmethod
    def ts_vol_shrink(series: pd.Series, short: int = 5, long: int = 20) -> pd.Series:
        """波动收缩度（长/短波动比 − 1，>0 收敛、<0 扩张）。"""
        ss = series.rolling(short, min_periods=2).std()
        ls = series.rolling(long, min_periods=2).std().replace(0.0, np.nan)
        return (ls / ss - 1.0).fillna(0.0)

    @staticmethod
    def ts_tail_risk(series: pd.Series, window: int = 20, q: float = 0.05) -> pd.Series:
        """尾部风险（x − 下分位，跌破分位为负、远离为正）。"""
        qv = series.rolling(window, min_periods=2).quantile(q)
        return series - qv

    # ── L2 截面（单序列滚动语义，逐日截面由执行器分派） ──

    @staticmethod
    def cs_winsor_flag(series: pd.Series, window: int = 20, k: float = 3.0) -> pd.Series:
        """极端值标记（|z| > k → 1，异常值密度代理）。"""
        mean = series.rolling(window, min_periods=2).mean()
        std = series.rolling(window, min_periods=2).std().replace(0.0, np.nan)
        return ((series - mean).abs() > k * std).astype(float)

    @staticmethod
    def cs_demean_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """去均值比率 (x/|mean| − 1)（相对均值的水平）。"""
        mean = series.rolling(window, min_periods=2).mean()
        return series / mean.abs().replace(0.0, np.nan) - 1.0

    @staticmethod
    def cs_rank_norm(series: pd.Series) -> pd.Series:
        """截面 rank 归一化到 [−1,1]（横截面相对位置）。"""
        r = PriceOps.rank(series)
        return 2.0 * r - 1.0

    @staticmethod
    def cs_med_ratio(series: pd.Series, window: int = 20) -> pd.Series:
        """与滚动中位数比 (x/med − 1)（稳健偏离）。"""
        med = series.rolling(window, min_periods=2).median().replace(0.0, np.nan)
        return series / med - 1.0

    @staticmethod
    def cs_extreme_gap(series: pd.Series, window: int = 20) -> pd.Series:
        """距极值缺口（(max−x)/(max−min) − 0.5，远离上轨为正）。"""
        mx = series.rolling(window, min_periods=2).max()
        mn = series.rolling(window, min_periods=2).min()
        to_up = (mx - series) / (mx - mn).replace(0.0, np.nan)
        return (to_up - 0.5).clip(-0.5, 0.5).fillna(0.0)

    # ── L3 条件 ──────────────────────────────────────────

    @staticmethod
    def where_between(series: pd.Series, lo: float, hi: float, a: float = 1.0, b: float = 0.0) -> pd.Series:
        """区间条件：lo ≤ x ≤ hi 取 a，否则 b（区间状态信号）。"""
        mask = (series >= lo) & (series <= hi)
        return pd.Series(np.where(mask, a, b), index=series.index, dtype=float)

    @staticmethod
    def cross_above(series: pd.Series, threshold: float = 0.0) -> pd.Series:
        """上穿阈值（今 > 阈 且 昨 ≤ 阈 → 1，突破事件）。"""
        prev = series.shift(1)
        return ((series > threshold) & (prev <= threshold)).astype(float)

    @staticmethod
    def cross_below(series: pd.Series, threshold: float = 0.0) -> pd.Series:
        """下穿阈值（今 < 阈 且 昨 ≥ 阈 → 1，跌破事件）。"""
        prev = series.shift(1)
        return ((series < threshold) & (prev >= threshold)).astype(float)

    @staticmethod
    def momentum_break(series: pd.Series, window: int = 20, k: float = 1.0) -> pd.Series:
        """动量突破（n 期动量超自身 std·k 倍 → 1，加速上行事件）。"""
        mom = series - series.shift(window)
        std = series.rolling(window, min_periods=2).std().replace(0.0, np.nan)
        return (mom > k * std).astype(float)

    # ── L5 领域 ──────────────────────────────────────────

    @staticmethod
    def vol_regime(series: pd.Series, window: int = 20) -> pd.Series:
        """波动率制度（std 处长期 >75 分位 → 1 高波动 / <25 分位 → −1 低波动 / 0 中）。"""
        std = series.rolling(window, min_periods=3).std()
        hi = std.rolling(window, min_periods=2).quantile(0.75)
        lo = std.rolling(window, min_periods=2).quantile(0.25)
        s = pd.Series(np.where(std > hi, 1.0, np.where(std < lo, -1.0, 0.0)), index=series.index)
        return s.fillna(0.0)

    @staticmethod
    def mean_reversion_signal(series: pd.Series, window: int = 20) -> pd.Series:
        """均值回归触发（|z| > 1 取反符号，否则 0；强偏离预示回归）。"""
        z = RollingOps.ts_zscore(series, window=window)
        return pd.Series(np.sign(-z) * (z.abs() > 1.0).astype(float), index=series.index, dtype=float)

    @staticmethod
    def price_volume_div(close: pd.Series, volume: pd.Series, window: int = 20) -> pd.Series:
        """价量背离（价格动量与量能变化方向相反占比）。"""
        pr = close.diff()
        vr = volume.diff()
        div = ((pr > 0) & (vr < 0)) | ((pr < 0) & (vr > 0))
        return div.astype(float).rolling(window, min_periods=2).mean().fillna(0.0)

    @staticmethod
    def liquidity_dryup(volume: pd.Series, window: int = 20) -> pd.Series:
        """流动性枯竭（量能 < 均值 0.5 倍 → 1）。"""
        ma = volume.rolling(window, min_periods=2).mean().replace(0.0, np.nan)
        return (volume < 0.5 * ma).astype(float)

    @staticmethod
    def self_corr(series: pd.Series, window: int = 20) -> pd.Series:
        """lag-1 自相关（正→趋势持续，负→反转倾向；样本不足降级 0）。"""
        return _rolling_series_out(_self_corr_vec, series, window)

    @staticmethod
    def sign_entropy(series: pd.Series, window: int = 20) -> pd.Series:
        """方向熵（正负收益占比熵：0=单向、1=完全无序）。"""
        p = (series.diff() > 0).astype(float).rolling(window, min_periods=2).mean().clip(1e-10, 1 - 1e-10)
        return -(p * np.log(p) + (1 - p) * np.log(1 - p)) / np.log(2)

    @staticmethod
    def reversal_strength(series: pd.Series, window: int = 20) -> pd.Series:
        """反转强度（−n 期动量/波动，负动量越强反转信号越大）。"""
        mom = series - series.shift(window)
        std = series.rolling(window, min_periods=2).std().replace(0.0, np.nan)
        return (-mom / std).fillna(0.0)


# ─── 特征工程中台主引擎 ─────────────────────────────────────


class FeatureOpsEngine:
    """特征工程中台主引擎。

    提供 GP 搜索、混合演化、特征重要性分析等统一入口。

    Usage:
        engine = FeatureOpsEngine()
        # 列出所有算子
        ops = engine.list_operators()
        # GP 搜索
        result = engine.run_gp_search(data, target_col='forward_return_20d')
        # 特征重要性分析
        importance = engine.analyze_importance(factor_series, data, 'forward_return_20d')
    """

    def __init__(self) -> None:
        self.registry = OperatorRegistry()

    def register_operator(
        self,
        name: str,
        func: Callable,
        category: str,
        params: list[str],
        description: str = "",
    ) -> None:
        """注册自定义算子。"""
        self.registry.register(name, func, category, params, description)

    def list_operators(self, category: Optional[str] = None) -> list[OperatorInfo]:
        """列出所有算子。"""
        return self.registry.list_operators(category)

    def get_operator(self, name: str) -> Optional[OperatorInfo]:
        """获取算子信息。"""
        return self.registry.get_operator(name)

    def list_categories(self) -> list[str]:
        """列出所有算子类别。"""
        return self.registry.list_categories()

    def run_gp_search(
        self,
        data: pd.DataFrame,
        target: str,
        config: Optional[dict[str, Any]] = None,
        train_mask: Optional[pd.Series] = None,
    ) -> Any:
        """运行 GP 演化搜索。

        Args:
            data: 特征数据面板
            target: 目标列名
            config: GP 配置覆盖
            train_mask: 训练集掩码（数据泄露防护），
                        仅当 train_mask 存在时，GPEvolver 在训练集上计算适应度

        Returns:
            GPEvolveResult
        """
        from .gp_evolver import GPEvolver, GPEvolverConfig

        gp_config = GPEvolverConfig()
        if config:
            for key, value in config.items():
                if hasattr(gp_config, key):
                    setattr(gp_config, key, value)

        gp = GPEvolver(
            operator_registry=self.registry,
            data_panel=data,
            target_col=target,
            config=gp_config,
            train_mask=train_mask,
        )
        return gp.evolve()

    def analyze_importance(
        self,
        factor_series: pd.Series,
        data: pd.DataFrame,
        target_col: str,
        feature_names: Optional[list[str]] = None,
    ) -> Any:
        """分析特征重要性。

        Args:
            factor_series: 因子值序列
            data: 原始特征数据
            target_col: 目标列名
            feature_names: 待分析特征列表 (默认所有数值列)

        Returns:
            FeatureImportanceResult
        """
        from .feature_importance import FeatureImportanceAnalyzer

        analyzer = FeatureImportanceAnalyzer()
        return analyzer.analyze(
            factor_series=factor_series,
            data=data,
            target_col=target_col,
            feature_names=feature_names,
        )
