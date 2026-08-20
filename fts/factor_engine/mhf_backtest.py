"""
fts/factor_engine/mhf_backtest.py — 分钟级事件驱动回测引擎（Phase 2，核心缺口）。

对多品种分钟 OHLCV 面板 + 方向信号做事件驱动回测：

- 信号 bar t 收盘产生 → 持仓 bar t+1 开盘成交（零未来函数）
- 换仓 bar 收益锚定 open（开仓后 bar 内 open→close），未换仓锚定前收盘
- 换仓扣单边成本（滑点+手续费，按 bps 配置）
- 允许隔夜持仓（分钟时间轴连续，跨日收益自然衔接）
- 涨跌停/极端 bar 过滤（可选：单 bar 相对前收盘跳变超阈值则不成交保持仓位）

输出：分钟净值 + 日聚合净值 + 指标（年化/夏普/回撤/换手/成本占比）+ 交易明细。

设计文档: docs/archive/plans/33-mhf-trading-plan.md §Phase 2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MhfBacktestConfig:
    """分钟回测配置。

    Attributes:
        cost_bps: 单边总成本（滑点+手续费，基点）。
        target_pct: 单品种目标仓位占总资金比例（多空对称）。
        max_positions: 最多同时持仓品种数（用于信号合成，回测引擎本身不限制）。
        filter_extreme: 是否过滤极端 bar（单 bar 跳变超 limit_pct 不成交）。
        limit_pct: 极端 bar 判定阈值（相对前收盘单 bar 涨跌幅）。
        annual_bars: 年化用 bar 数（5m≈20000，15m≈6800）。
    """

    cost_bps: float = 2.0
    cost_bps_map: dict[str, float] = field(default_factory=dict)  # 品种级成本覆盖（优先）
    target_pct: float = 0.08
    max_positions: int = 8
    filter_extreme: bool = False
    limit_pct: float = 0.09
    annual_bars: float = 20000.0


@dataclass
class MhfTrade:
    """单笔交易记录（开平仓事件）。"""

    symbol: str
    bar_time: pd.Timestamp
    side: int          # +1 开多 / -1 开空 / 0 平仓
    price: float
    turnover_frac: float   # 换手仓位占比（权重 × 方向变化）


@dataclass
class MhfBacktestResult:
    """回测结果。"""

    equity: pd.Series          # 分钟净值
    daily_equity: pd.Series    # 日聚合净值（按最后 bar 收盘）
    metrics: dict[str, float]  # 绩效指标
    trades: list[MhfTrade]


def _prepare_ohlcv(
    df: pd.DataFrame,
) -> Optional[pd.DataFrame]:
    """归一化分钟 OHLCV：DatetimeIndex + open/high/low/close/volume 数值列。"""
    if df is None or df.empty:
        return None
    out = df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        if "datetime" in out.columns:
            out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
            out = out.dropna(subset=["datetime"]).set_index("datetime")
        else:
            return None
    for col in ("open", "high", "low", "close"):
        if col not in out.columns:
            return None
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.sort_index()


def _extreme_mask(
    df: pd.DataFrame, limit_pct: float
) -> pd.Series:
    """极端 bar 掩码：单 bar (high-low)/prev_close 或 |open/prev_close-1| 超阈值。"""
    prev = df["close"].shift(1)
    rng = (df["high"] - df["low"]) / prev.replace(0.0, np.nan)
    gap = (df["open"] / prev - 1.0).abs()
    return (rng.fillna(0.0) > limit_pct) | (gap.fillna(0.0) > limit_pct)


def _per_symbol(
    df: pd.DataFrame,
    sig: pd.Series,
    config: MhfBacktestConfig,
) -> tuple[pd.Series, pd.Series, pd.Series, list[MhfTrade]]:
    """单品种事件驱动回测 → (收益, 换手, 持仓, 交易明细)。

    零未来：pos_t = sign(sig_{t-1})，bar t 开盘成交。
    """
    prep = _prepare_ohlcv(df)
    if prep is None or len(prep) < 2:
        return pd.Series(dtype=float), pd.Series(dtype=float), pd.Series(dtype=float), []
    df = prep
    sig = pd.to_numeric(sig, errors="coerce").reindex(df.index).ffill().fillna(0.0)
    pos = pd.Series(np.sign(sig.shift(1)).fillna(0.0), index=df.index)
    prev_pos = pos.shift(1).fillna(0.0)
    turnover = (pos - prev_pos).abs()
    if config.filter_extreme:
        extreme = _extreme_mask(df, config.limit_pct)
        # 极端 bar 不允许新成交：保留原持仓、无换手
        blocked = extreme & (turnover > 0)
        pos[blocked] = prev_pos[blocked]
        turnover = (pos - prev_pos).abs()

    close = df["close"]
    open_ = df["open"]
    prev_close = close.shift(1)
    anchor = open_.where(turnover > 0, prev_close)          # 换仓 bar 以 open 锚
    bar_ret = (close / anchor - 1.0).fillna(0.0)
    cost_rate = config.cost_bps / 1e4
    ret = pos * bar_ret - turnover * cost_rate

    trades: list[MhfTrade] = []
    changed = turnover.index[turnover > 0]
    for t in changed:
        trades.append(
            MhfTrade(
                symbol=str(sig.name or ""),
                bar_time=t,
                side=int(pos.loc[t]) if pos.loc[t] != 0 else 0,
                price=float(open_.loc[t]) if t in open_.index else 0.0,
                turnover_frac=float(turnover.loc[t]),
            )
        )
    return ret, turnover, pos, trades


def run_mhf_backtest(
    ohlcv_panel: dict[str, pd.DataFrame],
    signal_panel: dict[str, pd.Series],
    config: Optional[MhfBacktestConfig] = None,
) -> MhfBacktestResult:
    """多品种分钟事件驱动回测。

    Args:
        ohlcv_panel: {symbol: 分钟 OHLCV DataFrame}。
        signal_panel: {symbol: 方向信号 Series}（可连续值，符号决定多空）。
        config: 回测配置。

    Returns:
        MhfBacktestResult（净值/指标/交易明细）。
    """
    cfg = config or MhfBacktestConfig()
    rets: dict[str, pd.Series] = {}
    cost_series: dict[str, pd.Series] = {}
    all_trades: list[MhfTrade] = []
    for sym, df in ohlcv_panel.items():
        sig = signal_panel.get(sym)
        if sig is None:
            continue
        per_cfg = cfg
        if cfg.cost_bps_map and sym in cfg.cost_bps_map:
            per_cfg = replace(cfg, cost_bps=cfg.cost_bps_map[sym])
        ret, turnover, _, trades = _per_symbol(df, sig, per_cfg)
        if ret.empty:
            continue
        ret.name = sym
        rets[sym] = ret
        cost_series[sym] = turnover * (per_cfg.cost_bps / 1e4)
        for tr in trades:
            tr.symbol = sym
        all_trades.extend(trades)

    if not rets:
        return MhfBacktestResult(
            equity=pd.Series(dtype=float),
            daily_equity=pd.Series(dtype=float),
            metrics={},
            trades=[],
        )

    ret_df = pd.DataFrame(rets).fillna(0.0).sort_index()
    cost_df = pd.DataFrame(cost_series).fillna(0.0).sort_index()
    weight = cfg.target_pct
    port_ret = (ret_df * weight).sum(axis=1)
    cost_ret = (cost_df * weight).sum(axis=1)
    net_ret = port_ret - cost_ret

    equity = (1.0 + net_ret).cumprod()
    daily = equity.groupby(equity.index.normalize()).last()
    metrics = _metrics(net_ret, cost_ret, equity, daily, cfg, len(all_trades))
    return MhfBacktestResult(
        equity=equity,
        daily_equity=daily,
        metrics=metrics,
        trades=all_trades,
    )


def _metrics(
    net_ret: pd.Series,
    cost_ret: pd.Series,
    equity: pd.Series,
    daily: pd.Series,
    cfg: MhfBacktestConfig,
    n_trades: int,
) -> dict[str, float]:
    """绩效指标：年化/夏普/回撤/换手/成本占比。空输入返回空 dict。"""
    if equity.empty or len(equity) < 2:
        return {}
    total_ret = float(equity.iloc[-1] - 1.0)
    n = int(len(net_ret))
    years = n / cfg.annual_bars
    annualized = float((1.0 + total_ret) ** (1.0 / years) - 1.0) if years > 0 else 0.0
    std = float(net_ret.std())
    sharpe = float(net_ret.mean() / std * np.sqrt(cfg.annual_bars)) if std > 0 else 0.0
    dd = float((equity / equity.cummax() - 1.0).min())
    turnover_daily = float((daily.diff().abs().sum()) / max(len(daily), 1)) if len(daily) > 1 else 0.0
    gross_ret = (net_ret + cost_ret).abs().sum()
    cost_ratio = float(cost_ret.abs().sum() / gross_ret) if gross_ret > 0 else 0.0
    return {
        "total_return": round(total_ret, 4),
        "annualized_return": round(annualized, 4),
        "sharpe": round(sharpe, 3),
        "max_drawdown": round(dd, 4),
        "turnover_daily": round(turnover_daily, 4),
        "cost_ratio": round(cost_ratio, 4),
        "n_bars": n,
        "n_trades": n_trades,
    }
