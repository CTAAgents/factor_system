"""
fts/factor_engine/portfolio_risk_controls.py — 组合级风控：回撤止损 + 相关性熔断 + 同向敞口惩罚 + 踩踏规避

对照《期货因子策略组合·阶段三 风控与执行》补齐组合层约束（35-gap-closure-plan G1/G2）：
    - 组合级回撤止损：组合滚动回撤超过阈值 → 减仓/止损建议（CTA 高杠杆下回撤控制优先于收益）
    - 相关性熔断：组合成员收益相关性异常飙升（危机模式）→ 平仓建议
      （相关性常态下分散风险，危机时趋同变为同向暴露）
    - 同向敞口惩罚（G1）：多个同向因子共振时压缩组合总敞口，切断「局部最优共振重仓」
    - 集中踩踏止损规避（G2）：同一交易日批量止损超限时按风险敞口分批执行

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


# ─── G1: 同向敞口惩罚（35-gap-closure-plan G1）────────────────


@dataclass
class AlignedExposureConfig:
    """组合级同向敞口惩罚配置（G1）。

    Attributes:
        enabled: 是否启用（默认 True）
        align_threshold: 同向权重占比阈值（默认 0.6；≥60% 触发压缩）
        max_compress: 最大压缩系数（默认 0.5，即最多压缩至原敞口 50%）
        compress_curve: 压缩曲线 "linear" | "sqrt"（更温和） | "exp"（更激进）
    """

    enabled: bool = True
    align_threshold: float = 0.60
    max_compress: float = 0.50
    compress_curve: str = "linear"


def check_aligned_exposure(
    signals: list[dict],
    config: Optional[AlignedExposureConfig] = None,
) -> dict[str, float | bool]:
    """组合级同向敞口检测：以因子 IC 符号代理方向，按 |weight| 加权计算同向占比。

    因子 ic>0 视为看多因子、ic<0 视为看空因子；同向权重占比 = max(看多占比, 看空占比)。
    同向占比 ≥ align_threshold 时输出 compress_scale ∈ [max_compress, 1]，
    用于压缩组合总敞口——多个同向因子共振时整体降仓，切断「局部最优共振重仓」。

    零未来函数：仅使用当期信号与权重；纯函数，不修改输入。

    Args:
        signals: PortfolioSignal 列表（含 ic 与 weight 字段；权重归一化与否均可，
            占比对常数缩放不变）
        config: AlignedExposureConfig；None 或 enabled=False 返回不触发（scale=1.0）

    Returns:
        {triggered, long_ratio, short_ratio, compress_scale}
    """
    cfg = config if config is not None else AlignedExposureConfig()
    if not cfg.enabled:
        return {"triggered": False, "long_ratio": 0.0, "short_ratio": 0.0, "compress_scale": 1.0}

    long_w = 0.0
    short_w = 0.0
    for s in signals:
        ic = float(s.get("ic", 0.0) or 0.0)
        w = abs(float(s.get("weight", 0.0) or 0.0))
        if ic > 0:
            long_w += w
        elif ic < 0:
            short_w += w
    total = long_w + short_w
    if total <= 0:
        return {"triggered": False, "long_ratio": 0.0, "short_ratio": 0.0, "compress_scale": 1.0}

    long_ratio = long_w / total
    short_ratio = short_w / total
    ratio = max(long_ratio, short_ratio)
    if ratio < cfg.align_threshold:
        return {
            "triggered": False,
            "long_ratio": long_ratio,
            "short_ratio": short_ratio,
            "compress_scale": 1.0,
        }

    # 线性：阈值处 scale=1.0，占比=1 时 scale=max_compress
    scale = 1.0 - (ratio - cfg.align_threshold) / (1.0 - cfg.align_threshold) * (1.0 - cfg.max_compress)
    if cfg.compress_curve == "sqrt":
        # sqrt 在 [0,1] 内大于原值 → 压缩更温和
        scale = float(np.sqrt(max(scale, 0.0)))
    elif cfg.compress_curve == "exp":
        # 指数衰减：占比=1 时指数压至 max_compress，k 由 max_compress 反解
        k = -np.log(max(float(cfg.max_compress), 1e-6))
        scale = float(np.exp(-k * (ratio - cfg.align_threshold) / (1.0 - cfg.align_threshold)))
    scale = float(np.clip(scale, cfg.max_compress, 1.0))
    return {"triggered": True, "long_ratio": long_ratio, "short_ratio": short_ratio, "compress_scale": scale}


# ─── G2: 集中踩踏止损规避（35-gap-closure-plan G2）────────────────


@dataclass
class ExitStampedeConfig:
    """集中踩踏止损规避配置（G2）。

    Attributes:
        enabled: 是否启用（默认 True）
        max_same_day_exits: 单日最大同时平仓数（默认 3）
        batch_gap_days: 顺延批次间隔（计划日数，默认 1）
        order_by: 排序口径 "exposure_desc"（优先平最大敞口）
    """

    enabled: bool = True
    max_same_day_exits: int = 3
    batch_gap_days: int = 1
    order_by: str = "exposure_desc"


def throttle_exit_stampede(
    exit_signals: pd.DataFrame,
    exposures: pd.Series,
    config: Optional[ExitStampedeConfig] = None,
) -> pd.DataFrame:
    """集中踩踏止损规避：单日触发止损合约数超限时，按风险敞口分批执行。

    仅重排执行顺序（将超限合约顺延到后续计划日），不取消止损触发——保住止损纪律，
    同时降低行情拐点集体平仓的冲击成本。

    Args:
        exit_signals: 触发平仓矩阵（index=日期，columns=合约，值 1/True=触发）
        exposures: 各合约当前敞口（index=合约；用于 exposure_desc 排序）
        config: ExitStampedeConfig；None 或 enabled=False 原样返回

    Returns:
        分批平仓计划 DataFrame（同 index/columns，值为 1 表示该计划日执行）；
        每计划日执行数 ≤ max_same_day_exits（计划日耗尽时保留原日不丢弃）。
    """
    cfg = config if config is not None else ExitStampedeConfig()
    if not cfg.enabled or exit_signals is None or exit_signals.empty:
        return exit_signals

    dates = list(exit_signals.index)
    n_days = len(dates)
    result = pd.DataFrame(0, index=exit_signals.index, columns=exit_signals.columns)

    # 收集 (触发日索引, 合约)，按触发日 + 敞口降序稳定排序
    pending: list[tuple[int, str]] = []
    for i, d in enumerate(dates):
        for c in exit_signals.columns:
            if bool(exit_signals.loc[d, c]):
                pending.append((i, str(c)))
    if not pending:
        return result

    def _exposure(c: str) -> float:
        try:
            return float(exposures.get(c, 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    pending.sort(key=lambda x: (x[0], -_exposure(x[1])))

    day_cnt: dict[str, int] = {d: 0 for d in dates}
    for trig_i, c in pending:
        if day_cnt[dates[trig_i]] < cfg.max_same_day_exits:
            exec_i = trig_i
        else:
            exec_i = trig_i + max(1, int(cfg.batch_gap_days))
            while exec_i < n_days and day_cnt[dates[exec_i]] >= cfg.max_same_day_exits:
                exec_i += 1
            if exec_i >= n_days:
                exec_i = trig_i  # 计划日耗尽：保留原日，不丢弃止损
        result.loc[dates[exec_i], c] = 1
        day_cnt[dates[exec_i]] += 1
    return result


# ─── 综合入口 ────────────────────────────────────────────


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
    "AlignedExposureConfig",
    "check_aligned_exposure",
    "ExitStampedeConfig",
    "throttle_exit_stampede",
    "DEFAULT_DRAWDOWN_THRESHOLD",
    "DEFAULT_CORR_THRESHOLD",
    "DEFAULT_CORR_WINDOW",
]
