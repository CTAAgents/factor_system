"""
fts/data_sources/overnight_gap.py — 夜盘/隔夜跳空标记（GAP-066，v2.96.0）

对照《期货因子质检六层框架》Layer 1 期货特有挑战补齐：隔夜跳空（夜盘/跳空）需单独标记，
避免将跳空计入日波动率导致波动因子失真。

设计约束:
    - overnight_gap[t] = open[t] / close[t-1] - 1（前收至今开的跳空，负值=低开）
    - overnight_gap_flag[t] = |gap| > flag_threshold（默认 1% = 100 bps，显著跳空）
    - 首个交易日无前收，gap/flag 为 NaN/False
    - 零未来函数：仅依赖 t-1 及之前的收盘与 t 的开盘
    - 换月复权序列上，换月日会产生人为跳空并触发 flag（复权消除连续合约切换噪声的固有代价）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_FLAG_THRESHOLD: float = 0.01  # 1% = 100 bps 视为显著隔夜跳空


def compute_overnight_gap(df: pd.DataFrame) -> pd.Series:
    """计算隔夜跳空序列（open[t]/close[t-1] - 1）。"""
    if df is None or df.empty or "open" not in df.columns or "close" not in df.columns:
        return pd.Series(dtype=float)
    open_vals = pd.to_numeric(df["open"], errors="coerce").to_numpy(dtype=float)
    close_vals = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
    n = len(df)
    gap = np.full(n, np.nan)
    if n >= 2:
        denom = np.maximum(np.abs(close_vals[:-1]), 1e-10)
        gap[1:] = (open_vals[1:] - close_vals[:-1]) / denom
    return pd.Series(gap, index=df.index)


def inject_overnight_gap(df: pd.DataFrame, flag_threshold: float = DEFAULT_FLAG_THRESHOLD) -> pd.DataFrame:
    """向 DataFrame 注入 overnight_gap（跳空比例）与 overnight_gap_flag（显著跳空标记）列。

    Args:
        df: OHLCV 数据（须含 open/close 列）
        flag_threshold: 显著跳空阈值（绝对值，默认 0.01）

    Returns:
        注入后的 DataFrame 副本；缺 open/close 列时原样返回。
    """
    if df is None or df.empty or "open" not in df.columns or "close" not in df.columns:
        return df
    out = df.copy()
    gap = compute_overnight_gap(out)
    out["overnight_gap"] = gap
    out["overnight_gap_flag"] = (gap.abs() > float(flag_threshold)).astype(int) if not gap.empty else 0
    return out


__all__ = ["compute_overnight_gap", "inject_overnight_gap", "DEFAULT_FLAG_THRESHOLD"]
