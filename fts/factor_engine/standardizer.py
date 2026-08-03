"""
fts.factor_engine.standardizer — 因子标准化模块

实现注册表要求的 6 种标准化方式，提供统一的 fit/transform 接口。
支持单品种（1D）和截面（2D）标准化。

标准化方法:
    - zscore: (x - mean) / std，可选裁剪到 [-clip, +clip]
    - rank: 截面排名 / 总数 → [0, 1]
    - quantile: 滚动分位数 rank → [0, 1]
    - minmax: (x - min) / (max - min) → [0, 1]
    - winsorize_then_zscore: 先缩尾（clip 分位数），再 zscore
    - none: 不做标准化，原值返回

使用:
    from fts.factor_engine.standardizer import Standardizer, standardize

    # 面向对象
    std = Standardizer("zscore", clip=3.0)
    scores = std.fit_transform(raw_signals)

    # 便捷函数
    scores = standardize(raw_signals, "zscore", clip=3.0)

HARNESS §契约优先: StandardizerConfig 定义标准化参数契约。
版本: v1.0.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import numpy as np


# ─── 标准化方法类型 ────────────────────────────────────────

StandardizeMethod = Literal[
    "zscore",
    "rank",
    "quantile",
    "minmax",
    "winsorize_then_zscore",
    "none",
]

SUPPORTED_METHODS: tuple[StandardizeMethod, ...] = (
    "zscore",
    "rank",
    "quantile",
    "minmax",
    "winsorize_then_zscore",
    "none",
)

# ─── 配置 ─────────────────────────────────────────────────

@dataclass
class StandardizerConfig:
    """标准化器配置。

    Attributes:
        method: 标准化方法（zscore/rank/quantile/minmax/winsorize_then_zscore/none）
        clip: zscore 裁剪边界（仅 method=zscore/winsorize_then_zscore 时生效）
        winsorize_lower: 缩尾下分位数（仅 method=winsorize_then_zscore，默认 0.01）
        winsorize_upper: 缩尾上分位数（仅 method=winsorize_then_zscore，默认 0.99）
        axis: 标准化轴 — 0=截面(列方向), 1=时序(行方向), None=展平
        skipna: 是否跳过 NaN（默认 True）
    """
    method: StandardizeMethod = "zscore"
    clip: Optional[float] = 3.0
    winsorize_lower: float = 0.01
    winsorize_upper: float = 0.99
    axis: Optional[int] = 0
    skipna: bool = True


# ─── 标准化器 ─────────────────────────────────────────────

class Standardizer:
    """因子标准化器 — fit/transform 模式。

    用法:
        std = Standardizer("zscore", clip=3.0)
        std.fit(train_data)           # 计算均值/标准差
        scores = std.transform(test_data)  # 用训练参数标准化测试数据

        # 或一步完成
        scores = std.fit_transform(all_data)
    """

    def __init__(self, method: StandardizeMethod = "zscore", **kwargs):
        """初始化标准化器。

        Args:
            method: 标准化方法
            **kwargs: 传递给 StandardizerConfig 的参数
                - clip: zscore 裁剪边界
                - winsorize_lower/winsorize_upper: 缩尾分位数
                - axis: 标准化轴
                - skipna: 是否跳过 NaN
        """
        if method not in SUPPORTED_METHODS:
            raise ValueError(
                f"不支持的标准化方法: {method}，支持: {SUPPORTED_METHODS}"
            )
        cfg_kwargs = {"method": method}
        for k in ("clip", "winsorize_lower", "winsorize_upper", "axis", "skipna"):
            if k in kwargs:
                cfg_kwargs[k] = kwargs[k]
        self._config = StandardizerConfig(**cfg_kwargs)
        self._fitted = False
        self._params: dict = {}

    @property
    def config(self) -> StandardizerConfig:
        return self._config

    @property
    def method(self) -> StandardizeMethod:
        return self._config.method

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def fit(self, data: np.ndarray) -> "Standardizer":
        """计算标准化参数（均值/标准差/分位数等）。

        Args:
            data: 输入数据（1D 或 2D numpy array）

        Returns:
            self
        """
        data = np.asarray(data, dtype=np.float64)
        method = self._config.method
        axis = self._config.axis
        skipna = self._config.skipna

        if method == "none":
            self._params = {}
        elif method == "zscore":
            self._params["mean"] = _nanmean(data, axis=axis, skipna=skipna)
            self._params["std"] = _nanstd(data, axis=axis, skipna=skipna)
        elif method in ("rank", "quantile"):
            # rank/quantile 不需要 fit（transform 时动态计算）
            self._params = {}
        elif method == "minmax":
            self._params["min"] = _nanmin(data, axis=axis, skipna=skipna)
            self._params["max"] = _nanmax(data, axis=axis, skipna=skipna)
        elif method == "winsorize_then_zscore":
            lo = self._config.winsorize_lower
            hi = self._config.winsorize_upper
            self._params["lower"] = _nanpercentile(data, lo * 100, axis=axis, skipna=skipna)
            self._params["upper"] = _nanpercentile(data, hi * 100, axis=axis, skipna=skipna)
            # 缩尾后的均值/标准差在 transform 时计算
            self._params["mean"] = None
            self._params["std"] = None

        self._fitted = True
        return self

    def transform(self, data: np.ndarray) -> np.ndarray:
        """对数据应用标准化变换。

        Args:
            data: 输入数据（1D 或 2D numpy array）

        Returns:
            np.ndarray: 标准化后的数据
        """
        data = np.asarray(data, dtype=np.float64)
        method = self._config.method

        if method == "none":
            return data.copy()

        if method == "zscore":
            return _apply_zscore(
                data,
                self._params.get("mean"),
                self._params.get("std"),
                clip=self._config.clip,
                axis=self._config.axis,
                skipna=self._config.skipna,
            )

        if method == "rank":
            return _apply_rank(data, axis=self._config.axis, skipna=self._config.skipna)

        if method == "quantile":
            return _apply_quantile(data, axis=self._config.axis, skipna=self._config.skipna)

        if method == "minmax":
            return _apply_minmax(
                data,
                self._params.get("min"),
                self._params.get("max"),
                axis=self._config.axis,
                skipna=self._config.skipna,
            )

        if method == "winsorize_then_zscore":
            return _apply_winsorize_zscore(
                data,
                self._params.get("lower"),
                self._params.get("upper"),
                clip=self._config.clip,
                axis=self._config.axis,
                skipna=self._config.skipna,
            )

        raise ValueError(f"未知标准化方法: {method}")

    def fit_transform(self, data: np.ndarray) -> np.ndarray:
        """计算参数并标准化（一步完成）。

        Args:
            data: 输入数据（1D 或 2D numpy array）

        Returns:
            np.ndarray: 标准化后的数据
        """
        self.fit(data)
        return self.transform(data)


# ─── 便捷函数 ─────────────────────────────────────────────

def standardize(
    data: np.ndarray,
    method: StandardizeMethod = "zscore",
    **kwargs,
) -> np.ndarray:
    """便捷标准化函数 — 一步完成 fit + transform。

    Args:
        data: 输入数据（1D 或 2D numpy array）
        method: 标准化方法
        **kwargs: 传递给 Standardizer 的参数

    Returns:
        np.ndarray: 标准化后的数据
    """
    std = Standardizer(method, **kwargs)
    return std.fit_transform(data)


# ─── 内部辅助函数 ─────────────────────────────────────────

def _nanmean(data: np.ndarray, axis=None, skipna=True):
    """计算均值，支持 NaN 跳过。"""
    if skipna:
        return np.nanmean(data, axis=axis, keepdims=True)
    return np.mean(data, axis=axis, keepdims=True)


def _nanstd(data: np.ndarray, axis=None, skipna=True):
    """计算标准差，支持 NaN 跳过。"""
    if skipna:
        return np.nanstd(data, axis=axis, keepdims=True)
    return np.std(data, axis=axis, keepdims=True)


def _nanmin(data: np.ndarray, axis=None, skipna=True):
    """计算最小值，支持 NaN 跳过。"""
    if skipna:
        return np.nanmin(data, axis=axis, keepdims=True)
    return np.min(data, axis=axis, keepdims=True)


def _nanmax(data: np.ndarray, axis=None, skipna=True):
    """计算最大值，支持 NaN 跳过。"""
    if skipna:
        return np.nanmax(data, axis=axis, keepdims=True)
    return np.max(data, axis=axis, keepdims=True)


def _nanpercentile(data: np.ndarray, q, axis=None, skipna=True):
    """计算分位数，支持 NaN 跳过。"""
    if skipna:
        return np.nanpercentile(data, q, axis=axis, keepdims=True)
    return np.percentile(data, q, axis=axis, keepdims=True)


def _apply_zscore(
    data: np.ndarray,
    mean, std,
    clip: Optional[float] = None,
    axis=None,
    skipna=True,
) -> np.ndarray:
    """zscore 标准化: (x - mean) / std。NaN 位置保持不变。"""
    if mean is None:
        mean = _nanmean(data, axis=axis, skipna=skipna)
    if std is None:
        std = _nanstd(data, axis=axis, skipna=skipna)
    std = np.where(std < 1e-10, 1.0, std)
    result = (data - mean) / std
    if clip is not None:
        result = np.clip(result, -clip, clip)
    # NaN 位置置零（避免 NaN 传播）
    result = np.where(np.isnan(data), 0.0, result)
    return result


def _apply_rank(data: np.ndarray, axis=None, skipna=True) -> np.ndarray:
    """rank 标准化: 排名 / 总数 → [0, 1]。NaN 位置置零。"""
    if data.ndim == 1:
        valid = ~np.isnan(data) if skipna else np.ones_like(data, dtype=bool)
        n_valid = valid.sum()
        if n_valid == 0:
            return np.zeros_like(data)
        result = np.zeros_like(data, dtype=np.float64)
        order = np.argsort(np.argsort(data[valid]))
        result[valid] = (order + 1) / n_valid
        return result

    # 2D: 沿 axis 做截面 rank
    if axis is None:
        flat = data.ravel()
        valid = ~np.isnan(flat) if skipna else np.ones_like(flat, dtype=bool)
        n_valid = valid.sum()
        if n_valid == 0:
            return np.zeros_like(data)
        result = np.zeros_like(flat, dtype=np.float64)
        order = np.argsort(np.argsort(flat[valid]))
        result[valid] = (order + 1) / n_valid
        return result.reshape(data.shape)

    result = np.zeros_like(data, dtype=np.float64)
    if axis == 0:
        for col in range(data.shape[1]):
            col_data = data[:, col]
            valid = ~np.isnan(col_data) if skipna else np.ones_like(col_data, dtype=bool)
            n_valid = valid.sum()
            if n_valid > 0:
                order = np.argsort(np.argsort(col_data[valid]))
                result[valid, col] = (order + 1) / n_valid
    else:  # axis == 1
        for row in range(data.shape[0]):
            row_data = data[row, :]
            valid = ~np.isnan(row_data) if skipna else np.ones_like(row_data, dtype=bool)
            n_valid = valid.sum()
            if n_valid > 0:
                order = np.argsort(np.argsort(row_data[valid]))
                result[row, valid] = (order + 1) / n_valid
    return result


def _apply_quantile(data: np.ndarray, axis=None, skipna=True) -> np.ndarray:
    """quantile 标准化: 滚动分位数 → [0, 1]。"""
    if data.ndim == 1:
        valid = ~np.isnan(data) if skipna else np.ones_like(data, dtype=bool)
        n_valid = valid.sum()
        if n_valid == 0:
            return np.zeros_like(data)
        result = np.zeros_like(data, dtype=np.float64)
        # 使用 argsort 计算分位数: 每个值在排序后的位置 / 总数
        order = np.argsort(np.argsort(data[valid]))
        result[valid] = order / max(n_valid - 1, 1)
        return result

    if axis is None:
        return _apply_quantile(data.ravel(), axis=None, skipna=skipna).reshape(data.shape)

    result = np.zeros_like(data, dtype=np.float64)
    if axis == 0:
        for col in range(data.shape[1]):
            result[:, col] = _apply_quantile(data[:, col], axis=None, skipna=skipna)
    else:
        for row in range(data.shape[0]):
            result[row, :] = _apply_quantile(data[row, :], axis=None, skipna=skipna)
    return result


def _apply_minmax(data: np.ndarray, dmin, dmax, axis=None, skipna=True) -> np.ndarray:
    """minmax 标准化: (x - min) / (max - min) → [0, 1]。"""
    if dmin is None:
        dmin = _nanmin(data, axis=axis, skipna=skipna)
    if dmax is None:
        dmax = _nanmax(data, axis=axis, skipna=skipna)
    denom = dmax - dmin
    denom = np.where(denom < 1e-10, 1.0, denom)
    result = (data - dmin) / denom
    return np.clip(result, 0.0, 1.0)


def _apply_winsorize_zscore(
    data: np.ndarray,
    lower, upper,
    clip: Optional[float] = None,
    axis=None,
    skipna=True,
) -> np.ndarray:
    """winsorize + zscore: 先缩尾再标准化。"""
    if lower is None:
        lo = _nanpercentile(data, 1, axis=axis, skipna=skipna)
    else:
        lo = lower
    if upper is None:
        hi = _nanpercentile(data, 99, axis=axis, skipna=skipna)
    else:
        hi = upper
    # 缩尾
    winsorized = np.clip(data, lo, hi)
    # zscore
    mean = _nanmean(winsorized, axis=axis, skipna=skipna)
    std = _nanstd(winsorized, axis=axis, skipna=skipna)
    std = np.where(std < 1e-10, 1.0, std)
    result = (winsorized - mean) / std
    if clip is not None:
        result = np.clip(result, -clip, clip)
    return result


__all__ = [
    "StandardizeMethod",
    "SUPPORTED_METHODS",
    "StandardizerConfig",
    "Standardizer",
    "standardize",
]