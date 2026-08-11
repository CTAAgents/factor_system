"""
fts/factor_engine/portfolio_risk_controls.py — 组合级风控：回撤止损 + 相关性熔断（GAP-067，v2.97.0）

对照《期货因子策略组合·阶段三 风控与执行》补齐组合层约束：
    - 组合级回撤止损：组合滚动回撤超过阈值 → 减仓/止损建议（CTA 高杠杆下回撤控制优先于收益）
    - 相关性熔断：组合成员收益相关性异常飙升（危机模式）→ 平仓建议
      （相关性常态下分散风险，危机时趋同变为同向暴露）

设计约束:
    - 纯函数、零未来函数（滚动窗口仅用截至当前日的数据）
    - NaN 兜底：缺失收益对剔除；窗口样本不足返回不触发
    - 输出 dict（可直接落盘/进报告），默认不修改交易逻辑（建议信号，执行由 FDT 负责）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

DEFAULT_DRAWDOWN_THRESHOLD: float = 0.10  # 组合回撤 >10% 触发止损建议
DEFAULT_CORR_THRESHOLD: float = 0.80  # 成员均值相关 >0.8 触发熔断建议
DEFAULT_CORR_WINDOW: int = 60  # 相关性观察窗口（交易日）


@dataclass
class PortfolioRiskAlert:
    """组合级风控告警输出。"""

    drawdown_stop: bool = False  # 是否触发回撤止损建议
    drawdown_current: float = 0.0  # 当前滚动回撤（0~1）
    drawdown_threshold: float = DEFAULT_DRAWDOWN_THRESHOLD
    correlation_breaker: bool = False  # 是否触发相关性熔断建议
    correlation_current: float = 0.0  # 当前成员均值相关
    correlation_threshold: float = DEFAULT_CORR_THRESHOLD
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "drawdown_stop": self.drawdown_stop,
            "drawdown_current": self.drawdown_current,
            "drawdown_threshold": self.drawdown_threshold,
            "correlation_breaker": self.correlation_breaker,
            "correlation_current": self.correlation_current,
            "correlation_threshold": self.correlation_threshold,
            "notes": self.notes,
        }


def _max_drawdown_since_peak(returns: np.ndarray) -> float:
    """累计净值从历史峰值的最大回撤（0~1，净值从 1 起）。"""
    if len(returns) == 0:
        return 0.0
    nav = np.concatenate([[1.0], np.cumprod(1.0 + np.asarray(returns, dtype=float))])  # 净值从 1 起，恒正
    peak = np.maximum.accumulate(nav)
    dd = (peak - nav) / peak  # 回撤 ≥0；取最大
    return float(np.max(dd)) if len(dd) else 0.0


def check_drawdown_stop(
    returns: np.ndarray,
    threshold: float = DEFAULT_DRAWDOWN_THRESHOLD,
) -> dict[str, float | bool]:
    """组合级回撤止损：全样本最大回撤超过阈值触发。

    Args:
        returns: 组合收益序列
        threshold: 回撤阈值（默认 10%）

    Returns:
        {triggered, max_drawdown, threshold}
    """
    dd = _max_drawdown_since_peak(np.asarray(returns, dtype=float))
    return {"triggered": bool(dd >= threshold), "max_drawdown": dd, "threshold": threshold}


def check_correlation_circuit_breaker(
    returns: pd.DataFrame,
    threshold: float = DEFAULT_CORR_THRESHOLD,
    window: int = DEFAULT_CORR_WINDOW,
) -> dict[str, float | bool]:
    """相关性熔断：最近窗口成员收益两两相关均值超过阈值触发（危机模式）。"""
    if returns is None or returns.empty:
        return {"triggered": False, "mean_corr": 0.0, "threshold": threshold}
    sub = returns.tail(max(2, window))
    if sub.shape[1] < 2 or sub.shape[0] < 5:
        return {"triggered": False, "mean_corr": 0.0, "threshold": threshold}
    c = sub.corr()
    vals: list[float] = []
    n = c.shape[0]
    for i in range(n):
        for j in range(i + 1, n):
            v = c.iloc[i, j]
            if np.isfinite(v):
                vals.append(float(v))
    mean_corr = float(np.mean(vals)) if vals else 0.0
    return {"triggered": bool(mean_corr >= threshold), "mean_corr": mean_corr, "threshold": threshold}


def run_portfolio_risk_controls(
    combo_returns: Optional[np.ndarray],
    member_returns: Optional[pd.DataFrame],
    drawdown_threshold: float = DEFAULT_DRAWDOWN_THRESHOLD,
    corr_threshold: float = DEFAULT_CORR_THRESHOLD,
    corr_window: int = DEFAULT_CORR_WINDOW,
) -> PortfolioRiskAlert:
    """组合级风控综合检查：回撤止损 + 相关性熔断。

    Args:
        combo_returns: 组合收益序列（回撤止损用）；None 时跳过回撤检查
        member_returns: 组合成员收益面板（index=日期，columns=成员；相关性熔断用）
        drawdown_threshold: 回撤止损阈值
        corr_threshold: 相关性熔断阈值
        corr_window: 相关性观察窗口

    Returns:
        PortfolioRiskAlert
    """
    alert = PortfolioRiskAlert(
        drawdown_threshold=drawdown_threshold,
        correlation_threshold=corr_threshold,
    )
    if combo_returns is not None and len(combo_returns) >= 5:
        dd = check_drawdown_stop(combo_returns, drawdown_threshold)
        alert.drawdown_current = float(dd["max_drawdown"])
        alert.drawdown_stop = bool(dd["triggered"])
        if alert.drawdown_stop:
            alert.notes.append(f"组合最大回撤 {alert.drawdown_current:.2%} ≥ 阈值 {drawdown_threshold:.2%}，建议减仓/止损")
    if member_returns is not None:
        cc = check_correlation_circuit_breaker(member_returns, corr_threshold, corr_window)
        alert.correlation_current = float(cc["mean_corr"])
        alert.correlation_breaker = bool(cc["triggered"])
        if alert.correlation_breaker:
            alert.notes.append(f"成员均值相关 {alert.correlation_current:.2f} ≥ 阈值 {corr_threshold:.2f}，建议平仓（危机模式）")
    return alert


__all__ = [
    "PortfolioRiskAlert",
    "check_drawdown_stop",
    "check_correlation_circuit_breaker",
    "run_portfolio_risk_controls",
    "DEFAULT_DRAWDOWN_THRESHOLD",
    "DEFAULT_CORR_THRESHOLD",
    "DEFAULT_CORR_WINDOW",
]
