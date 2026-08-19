"""fts/factor_engine/numba_kernels.py — numba 定点内核库（plans/38 批 4，ts_rank 保留）。

38 号计划（numba 批 4）以 ``@njit`` 定点清除"含 NaN 多趟聚合 + 面板 2D" 窄子集的
Python 循环。真实规模对照（149×3000 面板，2026-08-15 实测）逐算子结果：

| 算子 | 1D 加速比 | 2D 面板加速比 | 结论 |
|:-----|:-----:|:-----:|:-----|
| ts_rank | 7.8x | 9.2x | 保留（用户确认放宽门槛至 ≥5x） |
| ts_zscore | 5.1x | 4.4x | 回退现值（未达门槛） |
| ts_cvar_95/99 | 1.2-1.3x | 1.4x | 回退现值（未达门槛） |

本库因此仅保留 ts_rank 的 1D/2D 内核；ts_cvar/ts_zscore 已回退
``_native_apply`` / ``_rolling_series_out`` 现值实现（见 plans/38 §4.5 豁免记录）。

语义零漂移铁律（对齐 plans/38 §6 与 feature_ops._rolling_apply_native 同规）：
- 入口统一 ``inf → NaN``；
- 窗口按非 NaN 观测计数判定 min_periods；
- ``fastmath=False`` 保数值逐位一致；
- numba/llvmlite 缺失或版本冲突 → ``_NUMBA_AVAILABLE=False``，调用方回退现值，零漂移。

依赖纪律（plans/38 §4.5）：numba==0.66.0 / llvmlite==0.48.0（pyproject 锁定），
不引入 numba-scipy 等扩展；``prange`` 列并行默认关闭（保守单线程起步）。

版本: v1.1.0（38-4.5 回退后，仅 ts_rank）
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# ─── 依赖可用性探测（import 失败 / 版本异常 → 回退现值，零漂移） ───
try:  # numba 是可选依赖：未安装/版本不兼容时全库降级为不可用，调用方回退现值实现
    import numba as _nb
    from numba import njit

    _NUMBA_AVAILABLE = True
    _NUMBA_VERSION: str | None = getattr(_nb, "__version__", None)
except Exception:  # noqa: BLE001 — 任何导入失败都视为不可用，不阻断主链路
    _nb = None  # type: ignore[assignment]

    def _njit_passthrough(*args: Any, **kwargs: Any) -> Any:
        """@njit 装饰器透传：numba 不可用时使 @njit(...) 成为 no-op。

        关键修复（2026-08-20）：numba 安装但版本不兼容（如 numpy≥2.5 时 numba 需要
        numpy≤2.4）时，`import numba` 抛 ImportError 被捕获，但模块级 `@njit(...)`
        仍会执行——njit=None 导致整模块 import 抛 TypeError，调用方无法降级。
        改为返回原函数，模块可正常加载，enabled()=False → 内核入口返回 None → 回退现值。
        """
        if args and callable(args[0]):
            return args[0]  # @njit 无括号用法
        return lambda f: f  # @njit(...) 带括号用法

    njit = _njit_passthrough  # type: ignore[assignment]
    _NUMBA_AVAILABLE = False
    _NUMBA_VERSION = None
    logger.warning("numba 不可用，算子 numba 内核回退现值实现（零漂移）")

# ─── 开关判定 ──────────────────────────────────────────────


def enabled() -> bool:
    """ops_numba 开关 + 依赖可用性双重判定（配置缺失时默认开启，仅依赖安装后生效）。"""
    if not _NUMBA_AVAILABLE:
        return False
    try:
        from fts.config.settings import get_config

        return get_config().ops_numba
    except Exception:  # noqa: BLE001 — 配置读取失败不阻断，默认开启
        return True


# ─── 通用归一 ──────────────────────────────────────────────


def _inf_to_nan(arr: np.ndarray) -> np.ndarray:
    return np.where(np.isinf(arr), np.nan, arr)


# ─── ts_rank 内核 ─────────────────────────────────────────


@njit(cache=True, fastmath=False)
def _rank_1d_njit(arr: np.ndarray, window: int, min_periods: int, pct: bool) -> np.ndarray:
    """1D 滚动排名内核：对齐 pandas ``rolling(window, min_periods).rank(pct=True)``。

    语义（2026-08-15 逐例实证）：平均秩法（ties 取平均）、分母=窗口内非 NaN 观测
    计数、最后元素 NaN → NaN、未达标窗口 → NaN。
    """
    n = arr.shape[0]
    out = np.full(n, np.nan)
    for j in range(n):
        last = arr[j]
        if np.isnan(last):
            continue
        lo = j - window + 1
        if lo < 0:
            lo = 0
        cnt = 0
        for k in range(lo, j + 1):
            if not np.isnan(arr[k]):
                cnt += 1
        if cnt < min_periods:
            continue
        n_less = 0
        n_equal = 0
        for k in range(lo, j + 1):
            v = arr[k]
            if np.isnan(v):
                continue
            if v < last:
                n_less += 1
            elif v == last:
                n_equal += 1
        rank_avg = n_less + (n_equal + 1) / 2.0
        out[j] = rank_avg / cnt if pct else rank_avg
    return out


@njit(cache=True, fastmath=False)
def _rank_2d_njit(panel: np.ndarray, window: int, min_periods: int, pct: bool) -> np.ndarray:
    """(rows, cols) 面板 → 同形输出，逐列复用 _rank_1d_njit（列串行保守起步）。"""
    n, m = panel.shape
    out = np.full((n, m), np.nan)
    for i in range(m):
        out[:, i] = _rank_1d_njit(panel[:, i], window, min_periods, pct)
    return out


def rank_1d(arr: np.ndarray, window: int, min_periods: int, pct: bool = True) -> np.ndarray | None:
    """1D 滚动排名内核入口。开关关闭/不可用/运行时异常 → None。"""
    if not enabled():
        return None
    try:
        return _rank_1d_njit(_inf_to_nan(np.ascontiguousarray(arr, dtype=np.float64)), window, min_periods, pct)
    except Exception:  # noqa: BLE001
        logger.warning("rank_1d numba 内核异常，回退现值实现", exc_info=True)
        return None


def rank_2d(panel: np.ndarray, window: int, min_periods: int, pct: bool = True) -> np.ndarray | None:
    """2D 面板滚动排名内核入口。开关关闭/不可用/运行时异常 → None。"""
    if not enabled():
        return None
    try:
        return _rank_2d_njit(_inf_to_nan(np.ascontiguousarray(panel, dtype=np.float64)), window, min_periods, pct)
    except Exception:  # noqa: BLE001
        logger.warning("rank_2d numba 内核异常，回退现值实现", exc_info=True)
        return None


# ─── ts_zscore 内核（plans/40 C 层，1D/2D）─────────────────
# 语义复刻 feature_ops._ts_zscore_vec：窗口内非 NaN 计数 >= window 才输出，
# z=(x-last - mean)/std(ddof=1)，std<=0 → 0；前缀（<window）全 NaN。


@njit(cache=True, fastmath=False)
def _ts_zscore_1d_njit(arr: np.ndarray, window: int) -> np.ndarray:
    """1D 滚动 Z-Score 内核（对齐 _ts_zscore_vec，含 NaN skipna）。"""
    n = arr.shape[0]
    out = np.full(n, np.nan)
    if n < window:
        return out
    for i in range(window - 1, n):
        lo = i - window + 1
        cnt = 0
        s = 0.0
        for k in range(lo, i + 1):
            v = arr[k]
            if not np.isnan(v):
                cnt += 1
                s += v
        if cnt < window:
            continue
        if cnt < 2:
            out[i] = 0.0
            continue
        mean = s / cnt
        var = 0.0
        for k in range(lo, i + 1):
            v = arr[k]
            if not np.isnan(v):
                d = v - mean
                var += d * d
        std = np.sqrt(var / (cnt - 1))  # ddof=1
        if std > 0.0:
            out[i] = (arr[i] - mean) / std
        else:
            out[i] = 0.0
    return out


@njit(cache=True, fastmath=False)
def _ts_zscore_2d_njit(panel: np.ndarray, window: int) -> np.ndarray:
    """(rows, cols) 面板 → 同形输出，逐列复用 1D 内核（列串行保守起步）。"""
    n, m = panel.shape
    out = np.full((n, m), np.nan)
    for i in range(m):
        out[:, i] = _ts_zscore_1d_njit(panel[:, i], window)
    return out


def zscore_1d(arr: np.ndarray, window: int) -> np.ndarray | None:
    """1D 滚动 Z-Score 内核入口。开关关闭/不可用/运行时异常 → None。"""
    if not enabled():
        return None
    try:
        return _ts_zscore_1d_njit(_inf_to_nan(np.ascontiguousarray(arr, dtype=np.float64)), int(window))
    except Exception:  # noqa: BLE001
        logger.warning("zscore_1d numba 内核异常，回退现值实现", exc_info=True)
        return None


def zscore_2d(panel: np.ndarray, window: int) -> np.ndarray | None:
    """2D 面板滚动 Z-Score 内核入口。开关关闭/不可用/运行时异常 → None。"""
    if not enabled():
        return None
    try:
        return _ts_zscore_2d_njit(_inf_to_nan(np.ascontiguousarray(panel, dtype=np.float64)), int(window))
    except Exception:  # noqa: BLE001
        logger.warning("zscore_2d numba 内核异常，回退现值实现", exc_info=True)
        return None


# ─── ts_cvar 内核（plans/40 C 层，1D/2D）─────────────────
# 语义复刻 ops_library.ts_cvar_* 的 _native_apply 路径：前缀（<window）用
# nanquantile(alpha) 分位（基于非 NaN 计数），主区间用 pos=alpha*(window-1) 排序
# 插值分位；tail=v[v<=q] 均值，空 → q。min_periods=2。


@njit(cache=True, fastmath=False)
def _quantile_linear(vals: np.ndarray, alpha: float) -> float:
    """numpy linear 分位（对齐 np.nanquantile(alpha) 默认插值，输入已去 NaN）。"""
    n = vals.shape[0]
    if n == 0:
        return np.nan
    s = np.sort(vals)
    if n == 1:
        return s[0]
    pos = alpha * (n - 1)
    lo = int(np.floor(pos))
    hi = int(np.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


@njit(cache=True, fastmath=False)
def _cvar_scalar(win: np.ndarray, alpha: float) -> float:
    """前缀窗口 CVaR（对齐 row_fn=_cvar：nanquantile + tail 均值）。"""
    # 提取非 NaN
    cnt = 0
    for v in win:
        if not np.isnan(v):
            cnt += 1
    if cnt == 0:
        return 0.0
    vals = np.empty(cnt)
    k = 0
    for v in win:
        if not np.isnan(v):
            vals[k] = v
            k += 1
    q = _quantile_linear(vals, alpha)
    s = 0.0
    t = 0
    for v in vals:
        if v <= q:
            s += v
            t += 1
    return s / t if t > 0 else q


@njit(cache=True, fastmath=False)
def _cvar_batch_scalar(win: np.ndarray, alpha: float, window: int) -> float:
    """主区间窗口 CVaR（对齐 batch_fn：pos=alpha*(window-1) 排序插值）。"""
    # 提取非 NaN 并排序
    cnt = 0
    for v in win:
        if not np.isnan(v):
            cnt += 1
    if cnt == 0:
        return 0.0
    vals = np.empty(cnt)
    k = 0
    for v in win:
        if not np.isnan(v):
            vals[k] = v
            k += 1
    s = np.sort(vals)
    pos = alpha * (window - 1)
    lo = int(np.floor(pos))
    hi = int(np.ceil(pos))
    if lo == hi:
        q = s[lo]
    else:
        q = s[lo] + (s[hi] - s[lo]) * (pos - lo)
    s2 = 0.0
    t = 0
    for v in s:
        if v <= q:
            s2 += v
            t += 1
    return s2 / t if t > 0 else q


@njit(cache=True, fastmath=False)
def _ts_cvar_1d_njit(arr: np.ndarray, window: int, alpha: float) -> np.ndarray:
    """1D 滚动 CVaR 内核（对齐 _native_apply 前缀/主区间双语义）。"""
    n = arr.shape[0]
    out = np.full(n, np.nan)
    if n < 2:
        return out
    # 前缀: i in [1, min(window-1, n))
    for i in range(1, min(window - 1, n)):
        lo = max(0, i - window + 1)
        cnt = 0
        for k in range(lo, i + 1):
            if not np.isnan(arr[k]):
                cnt += 1
        if cnt >= 2:
            out[i] = _cvar_scalar(arr[lo : i + 1], alpha)
    # 主区间
    if n >= window:
        for i in range(window - 1, n):
            lo = i - window + 1
            cnt = 0
            for k in range(lo, i + 1):
                if not np.isnan(arr[k]):
                    cnt += 1
            if cnt >= 2:
                out[i] = _cvar_batch_scalar(arr[lo : i + 1], alpha, window)
    return out


@njit(cache=True, fastmath=False)
def _ts_cvar_2d_njit(panel: np.ndarray, window: int, alpha: float) -> np.ndarray:
    """(rows, cols) 面板 → 同形输出，逐列复用 1D 内核（列串行保守起步）。"""
    n, m = panel.shape
    out = np.full((n, m), np.nan)
    for i in range(m):
        out[:, i] = _ts_cvar_1d_njit(panel[:, i], window, alpha)
    return out


def cvar_1d(arr: np.ndarray, window: int, alpha: float = 0.05) -> np.ndarray | None:
    """1D 滚动 CVaR 内核入口。开关关闭/不可用/运行时异常 → None。"""
    if not enabled():
        return None
    try:
        return _ts_cvar_1d_njit(_inf_to_nan(np.ascontiguousarray(arr, dtype=np.float64)), int(window), float(alpha))
    except Exception:  # noqa: BLE001
        logger.warning("cvar_1d numba 内核异常，回退现值实现", exc_info=True)
        return None


def cvar_2d(panel: np.ndarray, window: int, alpha: float = 0.05) -> np.ndarray | None:
    """2D 面板滚动 CVaR 内核入口。开关关闭/不可用/运行时异常 → None。"""
    if not enabled():
        return None
    try:
        return _ts_cvar_2d_njit(_inf_to_nan(np.ascontiguousarray(panel, dtype=np.float64)), int(window), float(alpha))
    except Exception:  # noqa: BLE001
        logger.warning("cvar_2d numba 内核异常，回退现值实现", exc_info=True)
        return None


# ─── 预热（冷启动 ~1.1s/函数；cache=True 落盘 __pycache__ 后进程间零编译） ──


def warmup() -> None:
    """触发全部内核一次小规模 JIT 编译（cache=True 落盘；CLI 入口可显式调用）。"""
    if not enabled():
        return
    tiny = np.array([1.0, 2.0, 3.0, 4.0, 5.0], dtype=np.float64)
    _rank_1d_njit(tiny, 3, 3, True)
    tiny2 = tiny.reshape(5, 1)
    _rank_2d_njit(tiny2, 3, 3, True)
    _ts_zscore_1d_njit(tiny, 3)
    _ts_zscore_2d_njit(tiny2, 3)
    _ts_cvar_1d_njit(tiny, 3, 0.05)
    _ts_cvar_2d_njit(tiny2, 3, 0.05)


__all__ = [
    "enabled",
    "warmup",
    "rank_1d",
    "rank_2d",
    "zscore_1d",
    "zscore_2d",
    "cvar_1d",
    "cvar_2d",
    "_NUMBA_AVAILABLE",
    "_NUMBA_VERSION",
]
