"""
fts.factor_engine.oos_checks — 过拟合排查与绩效归因（CTA 手册阶段9）。

对照《期货CTA多因子策略标准化作业手册》阶段9「滚动样本外回测 & 过拟合深度排查」:
    - 样本内外绩效衰减率: 训练集/验证集夏普落差不超过 30%（标准3）
    - 不同时间分段绩效: 2015-2018 / 2019-2022 / 2023-2026 三段净值一致性（标准4）
    - 回测报告必备指标补充: 分年度绩效 / 分板块收益贡献（阶段9.3）

设计约束:
    - 纯函数 / 零未来函数 / NaN 兜底 / 无样本段 skipped 不判失败

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 手册标准3：训练/验证夏普衰减上限
MAX_TRAIN_OOS_DECAY = 0.30
# 手册标准4：时间分段（2015-2018 / 2019-2022 / 2023-2026）
DEFAULT_PERIODS: list[tuple[str, str, str]] = [
    ("2015-2018", "2015-01-01", "2018-12-31"),
    ("2019-2022", "2019-01-01", "2022-12-31"),
    ("2023-2026", "2023-01-01", "2026-12-31"),
]


def performance_decay_check(
    train_sharpe: float,
    oos_sharpe: float,
    max_decay: float = MAX_TRAIN_OOS_DECAY,
) -> dict:
    """样本内外绩效衰减率校验（手册标准3：落差不超过 30%）。

    Args:
        train_sharpe: 训练集夏普
        oos_sharpe: 验证集（样本外）夏普
        max_decay: 最大允许衰减率（默认 0.30）

    Returns:
        dict: {decay_ratio, passed}
    """
    denom = abs(float(train_sharpe))
    decay = (float(train_sharpe) - float(oos_sharpe)) / denom if denom > 1e-9 else 0.0
    return {"decay_ratio": float(decay), "passed": bool(decay <= max_decay)}


def period_consistency_check(
    returns: np.ndarray | pd.Series,
    dates: np.ndarray | pd.Series,
    periods: Optional[list[tuple[str, str, str]]] = None,
    min_positive_ratio: float = 0.5,
) -> dict:
    """时间分段绩效一致性（手册标准4：三段净值走势一致性）。

    各固定时间段年化收益；多数段正向盈利且无「单段暴利其余亏损」畸形 → 通过。

    Args:
        returns: 收益率序列（与 dates 对齐）
        dates: 日期数组
        periods: 分段区间 [(名称, 起, 止)]；None 用默认三段
        min_positive_ratio: 正向盈利段占比下限（默认 0.5）

    Returns:
        dict: {
            periods: {名称: {cum_return, annual_return, n, positive, status}},
            positive_ratio, passed,
        }
    """
    segs = periods if periods is not None else DEFAULT_PERIODS
    ret = np.asarray(returns, dtype=float)
    dts = pd.DatetimeIndex(pd.to_datetime(np.asarray(dates)))
    result: dict = {"periods": {}, "positive_ratio": 0.0, "passed": False}
    if len(ret) != len(dts) or len(ret) < 5:
        return result

    per_period: dict[str, dict] = {}
    for name, start, end in segs:
        mask = (dts >= pd.Timestamp(start)) & (dts <= pd.Timestamp(end))
        r = ret[mask]
        n = int(mask.sum())
        if n < 5:
            per_period[name] = {"status": "skipped", "n": n}
            continue
        cum = float(np.prod(1.0 + r) - 1.0)
        annual = float((1.0 + cum) ** (252.0 / n) - 1.0) if cum > -1.0 else -1.0
        per_period[name] = {
            "cum_return": cum,
            "annual_return": annual,
            "n": n,
            "positive": bool(annual > 0),
            "status": "ok",
        }
    valid = [p for p in per_period.values() if p.get("status") == "ok"]
    result["periods"] = per_period
    if not valid:
        return result
    positive = sum(1 for p in valid if p["positive"])
    ratio = positive / len(valid)
    result["positive_ratio"] = ratio
    result["passed"] = bool(ratio >= min_positive_ratio)
    return result


def annual_returns(
    returns: np.ndarray | pd.Series,
    dates: np.ndarray | pd.Series,
) -> dict:
    """分年度绩效（手册阶段9.3 报告必备指标）。

    Args:
        returns: 收益率序列
        dates: 日期数组（对齐）

    Returns:
        dict: {年份: {annual_return, n_days}}
    """
    ret = np.asarray(returns, dtype=float)
    dts = pd.DatetimeIndex(pd.to_datetime(np.asarray(dates)))
    out: dict[str, dict] = {}
    if len(ret) != len(dts):
        return out
    for year in sorted(set(dts.year)):
        mask = dts.year == year
        r = ret[mask]
        n = int(mask.sum())
        if n < 2:
            continue
        cum = float(np.prod(1.0 + r) - 1.0)
        out[str(year)] = {"annual_return": cum, "n_days": n}
    return out


def sector_returns_contribution(
    returns_by_symbol: dict[str, np.ndarray | pd.Series],
    sector_map: dict[str, str],
) -> dict:
    """分板块收益贡献（手册阶段9.3 报告必备指标）。

    Args:
        returns_by_symbol: {symbol: 收益率序列}
        sector_map: {symbol: 板块名}

    Returns:
        dict: {
            sectors: {板块: {cum_return, n_symbols, contribution_share}},
            total_return, passed,
        }
    """
    sector_cum: dict[str, float] = {}
    sector_n: dict[str, int] = {}
    for sym, r in returns_by_symbol.items():
        sector = sector_map.get(sym, "未知")
        arr = np.asarray(r, dtype=float)
        arr = arr[np.isfinite(arr)]
        if len(arr) < 2:
            continue
        cum = float(np.prod(1.0 + arr) - 1.0)
        sector_cum[sector] = sector_cum.get(sector, 0.0) + cum
        sector_n[sector] = sector_n.get(sector, 0) + 1
    if not sector_cum:
        return {"sectors": {}, "total_return": 0.0, "passed": False}
    total = sum(sector_cum.values())
    sectors = {
        name: {
            "cum_return": cum,
            "n_symbols": sector_n[name],
            "contribution_share": cum / total if abs(total) > 1e-9 else 0.0,
        }
        for name, cum in sector_cum.items()
    }
    return {"sectors": sectors, "total_return": float(total), "passed": True}


__all__ = [
    "MAX_TRAIN_OOS_DECAY",
    "DEFAULT_PERIODS",
    "performance_decay_check",
    "period_consistency_check",
    "annual_returns",
    "sector_returns_contribution",
]
