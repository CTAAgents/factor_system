"""
fts/factor_engine/mhf_evaluation.py — 分钟因子评估（Phase 1）。

对分钟因子序列做 IC/IR/胜率/衰减评估，支持训练/验证时间切割与截面因子评估。

评估纪律（对齐 HARNESS 红线）:
    - forward_returns 仅用于评估阶段对齐（允许未来收益），因子计算本身零未来
    - 时间切割：训练/验证按时间严格切分，禁止样本复用
    - 多重检验：批量因子评估时用 FDR 校正（BH 方法）控制假阳性

设计文档: docs/harness/plans/33-mhf-trading-plan.md §Phase 1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)


@dataclass
class IcSummary:
    """单因子 IC 评估摘要。"""

    factor: str
    ic_mean: float
    ic_std: float
    ir: float              # ic_mean / ic_std
    win_rate: float        # IC>0 占比
    t_stat: float
    n_periods: int
    ic_decay: list[float] = field(default_factory=list)  # 各前视周期的 IC


def forward_returns(close: pd.Series, horizon: int) -> pd.Series:
    """前视收益 ret_{t+h} = close_{t+h}/close_t - 1（评估专用，非因子）。"""
    if close is None or close.empty:
        return pd.Series(dtype=float)
    return close.shift(-horizon) / close - 1.0


def factor_ic(factor: pd.Series, fwd_ret: pd.Series) -> pd.Series:
    """因子与前视收益的滚动 Spearman IC 时间序列（按日聚合）。

    每日 IC：当日因子值与前视收益的截面（或时序）秩相关；
    无有效对或常数输入返回 NaN（scipy 自动处理，外部兜底）。
    """
    frame = pd.DataFrame({"f": factor, "r": fwd_ret}).dropna()
    if len(frame) < 5:
        return pd.Series(dtype=float)
    date = pd.to_datetime(frame.index, errors="coerce").normalize()
    frame["date"] = date
    ic: list[tuple[pd.Timestamp, float]] = []
    for d, g in frame.groupby("date"):
        if len(g) < 5 or g["f"].nunique() < 2 or g["r"].nunique() < 2:
            continue
        try:
            rho, _ = sp_stats.spearmanr(g["f"], g["r"])
            if np.isfinite(rho):
                ic.append((d, float(rho)))
        except Exception:  # noqa: BLE001 — 常数输入等异常忽略该日
            continue
    if not ic:
        return pd.Series(dtype=float)
    return pd.Series({d: v for d, v in ic}).sort_index()


def summarize_ic(ic: pd.Series) -> IcSummary:
    """IC 序列汇总（均值/IR/胜率/t值）。空输入返回全 0 摘要。"""
    if ic is None or ic.empty:
        return IcSummary(factor="", ic_mean=0.0, ic_std=0.0, ir=0.0,
                         win_rate=0.0, t_stat=0.0, n_periods=0)
    vals = ic.dropna().to_numpy(dtype=float)
    n = int(len(vals))
    if n == 0:
        return IcSummary(factor="", ic_mean=0.0, ic_std=0.0, ir=0.0,
                         win_rate=0.0, t_stat=0.0, n_periods=0)
    mean = float(vals.mean())
    std = float(vals.std(ddof=1)) if n > 1 else 0.0
    ir = mean / std if std > 0 else 0.0
    t = mean / (std / np.sqrt(n)) if std > 0 else 0.0
    return IcSummary(
        factor=str(ic.name or ""),
        ic_mean=round(mean, 5),
        ic_std=round(std, 5),
        ir=round(ir, 4),
        win_rate=round(float((vals > 0).mean()), 4),
        t_stat=round(float(t), 3),
        n_periods=n,
    )


def ic_decay_curve(factor: pd.Series, close: pd.Series,
                   horizons: Optional[list[int]] = None) -> list[float]:
    """IC 衰减曲线：各前视周期（bar）的日均 IC 均值。"""
    hz = horizons or [1, 3, 5, 10, 20]
    out: list[float] = []
    for h in hz:
        ic = factor_ic(factor, forward_returns(close, h))
        s = summarize_ic(ic)
        out.append(s.ic_mean)
    return out


def evaluate_factor(
    factor: pd.Series,
    close: pd.Series,
    horizon: int = 5,
    decay_horizons: Optional[list[int]] = None,
) -> IcSummary:
    """单因子一键评估：IC 摘要 + 衰减曲线。"""
    ic = factor_ic(factor, forward_returns(close, horizon))
    s = summarize_ic(ic)
    s.factor = str(getattr(factor, "name", "") or "")
    s.ic_decay = ic_decay_curve(factor, close, decay_horizons)
    return s


def split_time_series(
    factor: pd.Series,
    fwd_ret: pd.Series,
    train_ratio: float = 0.7,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """按时间严格切割训练/验证（前 train_ratio 训练，其余验证）。

    Returns:
        (train_factor, train_ret, val_factor, val_ret)。
    """
    frame = pd.DataFrame({"f": factor, "r": fwd_ret}).dropna().sort_index()
    n = len(frame)
    cut = int(n * train_ratio)
    train = frame.iloc[:cut]
    val = frame.iloc[cut:]
    return (train["f"], train["r"], val["f"], val["r"])


def bh_fdr(p_values: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg FDR 校正（批量因子多重检验）。返回校正后 q 值。"""
    p = np.asarray(p_values, dtype=float)
    p = p[np.isfinite(p)]
    m = len(p)
    if m == 0:
        return np.array([])
    order = np.argsort(p)
    ranked = p[order]
    q = np.full(m, np.nan)
    running = 1.0
    for i in range(m - 1, -1, -1):
        running = min(running, ranked[i] * m / (i + 1))
        q[order[i]] = running
    return q
