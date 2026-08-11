"""
fts/factor_engine/horizon_analysis.py — 多持有期 IC 体系（GAP-060，v2.90.0）

对照《期货因子质检六层框架》Layer 4 衰减分析补齐：
    - 多持有期 IC（默认 1/5/10/20 日）：同一因子在不同持有期下的预测力
    - IC 衰减曲线：IC 随持有期延长的相对衰减（IC(h)/IC(1)）
    - 最佳持有期选择：最大化 ICIR 的持有期

设计约束:
    - 零未来函数：第 t 日使用 [t, t+h] 的未来收益，仅依赖 t 及之前的信息
    - NaN 兜底：信号/收益含 NaN 时剔除样本对；常数输入返回 0
    - 块状滚动 IC 序列（非重叠块）计算 ICIR / 胜率，避免单期 IC 无波动
    - 独立模块、无循环依赖；evaluation_chain 可选集成（配置开关）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy import stats as _sp

DEFAULT_HORIZONS: tuple[int, ...] = (1, 5, 10, 20)


@dataclass
class HorizonAnalysisResult:
    """多持有期 IC 分析结果。"""

    horizons: list[int] = field(default_factory=lambda: list(DEFAULT_HORIZONS))
    ic_by_horizon: dict[int, float] = field(default_factory=dict)  # 各持有期均值 IC
    icir_by_horizon: dict[int, float] = field(default_factory=dict)  # IC 均值 / IC 标准差
    win_rate_by_horizon: dict[int, float] = field(default_factory=dict)  # 正 IC 块占比
    ic_series_by_horizon: dict[int, list[float]] = field(default_factory=dict)  # 各持有期块状 IC 序列
    best_horizon: int = 1  # 最大化 ICIR 的持有期
    decay_curve: dict[int, float] = field(default_factory=dict)  # 持有期 -> IC(h)/IC(1)
    monotonic_decay: bool = False  # IC 绝对值是否随持有期单调衰减

    def to_dict(self) -> dict:
        """序列化为 dict（供 FactorEvaluation / JSON 输出）。"""
        return {
            "horizons": self.horizons,
            "ic_by_horizon": self.ic_by_horizon,
            "icir_by_horizon": self.icir_by_horizon,
            "win_rate_by_horizon": self.win_rate_by_horizon,
            "best_horizon": self.best_horizon,
            "decay_curve": self.decay_curve,
            "monotonic_decay": self.monotonic_decay,
        }


def _spearman_ic(signal: np.ndarray, forward_returns: np.ndarray) -> float:
    """单段 Spearman IC（NaN 掩码 + 常数兜底），不可用返回 NaN。"""
    sig = np.asarray(signal, dtype=float)
    ret = np.asarray(forward_returns, dtype=float)
    if len(sig) != len(ret) or len(sig) < 2:
        return float("nan")
    valid = ~(np.isnan(sig) | np.isnan(ret))
    sig_v = sig[valid]
    ret_v = ret[valid]
    if len(sig_v) < 2 or np.std(sig_v) < 1e-12 or np.std(ret_v) < 1e-12:
        return float("nan")
    ic, _ = _sp.spearmanr(sig_v, ret_v)
    if np.isnan(ic):
        return float("nan")
    return float(ic)


def _forward_returns(close: np.ndarray, horizon: int) -> np.ndarray:
    """h 日前向收益率：(close[t+h]-close[t])/close[t]，末尾 h 位 NaN。"""
    close = np.asarray(close, dtype=float)
    n = len(close)
    fwd = np.full(n, np.nan)
    if horizon >= n or horizon <= 0:
        return fwd
    denom = np.maximum(np.abs(close[: n - horizon]), 1e-10)
    fwd[: n - horizon] = (close[horizon:] - close[: n - horizon]) / denom
    return fwd


def compute_multi_horizon_ic(
    signal: np.ndarray,
    close: np.ndarray,
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    min_samples: int = 30,
    block_size: int = 20,
) -> Optional[HorizonAnalysisResult]:
    """对 1/5/10/20 日等持有期计算 IC / ICIR / 胜率，并输出衰减曲线与最佳持有期。

    Args:
        signal: 因子信号（与 close 等长）
        close: 收盘价序列
        horizons: 待分析持有期（天）
        min_samples: 最少样本数，不足返回 None
        block_size: 非重叠块大小（计算块状 IC 序列，块数过少时退化为单段）

    Returns:
        HorizonAnalysisResult；样本不足/无有效持有期返回 None。
    """
    signal = np.asarray(signal, dtype=float)
    close = np.asarray(close, dtype=float)
    n = min(len(signal), len(close))
    if n < min_samples or np.all(np.isnan(signal)):
        return None

    result = HorizonAnalysisResult(horizons=[h for h in horizons if h > 0])
    if not result.horizons:
        return None

    # 单段全样本 IC 作为参考锚点
    for h in result.horizons:
        fwd = _forward_returns(close, h)
        ics: list[float] = []
        n_blocks = max(n // block_size, 1)
        for b in range(n_blocks):
            s = b * block_size
            e = min(s + block_size, n)
            ic = _spearman_ic(signal[s:e], fwd[s:e])
            if np.isfinite(ic):
                ics.append(ic)
        if not ics:
            # 块状失败则退化单段
            ic_all = _spearman_ic(signal, fwd)
            if not np.isfinite(ic_all):
                continue
            ics = [ic_all]
        result.ic_series_by_horizon[h] = ics
        ic_arr = np.asarray(ics, dtype=float)
        result.ic_by_horizon[h] = float(np.mean(ic_arr))
        result.win_rate_by_horizon[h] = float(np.mean(ic_arr > 0))
        std = float(np.std(ic_arr))
        result.icir_by_horizon[h] = float(np.mean(ic_arr) / max(std, 1e-10))

    if not result.ic_by_horizon:
        return None

    # 最佳持有期：ICIR 最大（平局取较短持有期）
    valid = [(h, abs(result.icir_by_horizon[h])) for h in result.horizons if h in result.icir_by_horizon]
    if valid:
        best = min(valid, key=lambda x: (-x[1], x[0]))[0]
        result.best_horizon = best

    # 衰减曲线：IC(h)/IC(1)（绝对 IC 归一化）
    base_h = result.horizons[0]
    base_abs = abs(result.ic_by_horizon.get(base_h, 0.0))
    for h in result.horizons:
        ic_h = abs(result.ic_by_horizon.get(h, 0.0))
        result.decay_curve[h] = float(ic_h / base_abs) if base_abs > 1e-10 else 0.0

    # 单调衰减判定：IC 绝对值随持有期非增
    abs_ics = [abs(result.ic_by_horizon.get(h, 0.0)) for h in result.horizons if h in result.ic_by_horizon]
    result.monotonic_decay = bool(len(abs_ics) >= 2 and all(abs_ics[i] >= abs_ics[i + 1] - 1e-9 for i in range(len(abs_ics) - 1)))
    return result


def compute_ic_decay_curve(result: HorizonAnalysisResult) -> dict[int, float]:
    """返回 IC 衰减曲线（持有期 -> 相对第 1 持有期的绝对 IC 比例）。"""
    return dict(result.decay_curve)


def select_best_horizon(result: HorizonAnalysisResult) -> int:
    """返回最佳持有期（最大化 |ICIR|）。"""
    return result.best_horizon


__all__ = [
    "HorizonAnalysisResult",
    "compute_multi_horizon_ic",
    "compute_ic_decay_curve",
    "select_best_horizon",
    "DEFAULT_HORIZONS",
]
