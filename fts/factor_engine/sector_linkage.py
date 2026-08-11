"""
fts/factor_engine/sector_linkage.py — 品种间板块联动检测（GAP-065，v2.95.0）

对照《期货因子质检六层框架》Layer 5 期货特有维度补齐：
    - 板块内联动强度：同一产业链（黑色系/化工系/有色系…）品种收益两两相关均值
    - 跨板块联动：该板块与其余板块成员的均值相关（评估板块独立性）
    - 因子跨联动板块分散度：因子在板块成员间的截面离散度（拥挤度先行指标）

设计约束:
    - 纯函数、无外部依赖注入（sector_map 由调用方传入，默认取 FUTURES_SECTOR_MAP）
    - NaN 兜底：收益/信号缺失对剔除；成员 <2 时联动强度记为 0
    - 独立模块、无循环依赖；可独立调用或集成至 L3 报告
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

DEFAULT_LINKAGE_THRESHOLD: float = 0.5  # 板块内均值相关 >= 0.5 判定高联动


@dataclass
class SectorLinkageReport:
    """单个板块的联动检测结果。"""

    sector: str
    members: list[str] = field(default_factory=list)
    intra_sector_avg_corr: float = 0.0  # 板块内品种收益两两相关均值（联动强度）
    intra_sector_max_corr: float = 0.0  # 板块内最大两两相关
    cross_sector_avg_corr: float = 0.0  # 与其余板块成员的平均相关
    factor_dispersion: Optional[float] = None  # 因子在板块成员间的截面离散度
    high_linkage: bool = False  # intra >= threshold

    def to_dict(self) -> dict:
        return {
            "sector": self.sector,
            "members": self.members,
            "intra_sector_avg_corr": self.intra_sector_avg_corr,
            "intra_sector_max_corr": self.intra_sector_max_corr,
            "cross_sector_avg_corr": self.cross_sector_avg_corr,
            "factor_dispersion": self.factor_dispersion,
            "high_linkage": self.high_linkage,
        }


def _pairwise_stats(sub: pd.DataFrame) -> tuple[float, float]:
    """子面板（列=品种）两两 Pearson 相关均值与最大值；成员 <2 返回 (0,0)。"""
    if sub.shape[1] < 2 or sub.shape[0] < 5:
        return 0.0, 0.0
    c = sub.corr()
    vals: list[float] = []
    n = c.shape[0]
    for i in range(n):
        for j in range(i + 1, n):
            v = c.iloc[i, j]
            if np.isfinite(v):
                vals.append(float(v))
    if not vals:
        return 0.0, 0.0
    return float(np.mean(vals)), float(np.max(vals))


def compute_sector_linkage(
    returns: pd.DataFrame,
    sector_map: dict[str, str] | None = None,
    threshold: float = DEFAULT_LINKAGE_THRESHOLD,
    signal: Optional[pd.DataFrame] = None,
) -> list[SectorLinkageReport]:
    """计算各板块联动强度 + 跨板块相关 + 因子截面分散度。

    Args:
        returns: 品种收益面板（index=日期，columns=品种代码）
        sector_map: {品种: 板块}；None 时使用 fts.data_futures.FUTURES_SECTOR_MAP
        threshold: 高联动判定阈值（板块内均值相关）
        signal: 可选因子信号面板（index=日期，columns=品种）；提供时计算板块内因子截面分散度

    Returns:
        各板块 SectorLinkageReport 列表（按板块内均值相关降序）。
    """
    if sector_map is None:
        try:
            from ..data_futures import FUTURES_SECTOR_MAP

            sector_map = {sym: sec for sec, members in FUTURES_SECTOR_MAP.items() for sym in members}
        except Exception:  # noqa: BLE001
            sector_map = {}
    if not sector_map or returns.empty:
        return []

    # 板块 -> 品种列表（仅保留收益面板中存在的品种）
    sectors: dict[str, list[str]] = {}
    for sym, sec in sector_map.items():
        if sym in returns.columns:
            sectors.setdefault(sec, []).append(sym)

    reports: list[SectorLinkageReport] = []
    for sec, members in sectors.items():
        sub = returns[members]
        avg_corr, max_corr = _pairwise_stats(sub)

        # 跨板块联动：该板块成员 vs 其余板块成员的均值相关
        other_cols = [s for s in returns.columns if s not in members]
        cross_avg = 0.0
        if other_cols:
            cross_vals: list[float] = []
            c_full = returns[members + other_cols].corr()
            for m in members:
                for o in other_cols:
                    v = c_full.loc[m, o]
                    if np.isfinite(v):
                        cross_vals.append(float(v))
            cross_avg = float(np.mean(cross_vals)) if cross_vals else 0.0

        disp: Optional[float] = None
        if signal is not None and not signal.empty:
            disp_vals: list[float] = []
            for d in range(len(signal)):
                row = signal[members].iloc[d].to_numpy(dtype=float)
                row_valid = row[np.isfinite(row)]
                if len(row_valid) >= 2:
                    disp_vals.append(float(np.std(row_valid)))
            if disp_vals:
                disp = float(np.mean(disp_vals))

        reports.append(
            SectorLinkageReport(
                sector=sec,
                members=members,
                intra_sector_avg_corr=avg_corr,
                intra_sector_max_corr=max_corr,
                cross_sector_avg_corr=cross_avg,
                factor_dispersion=disp,
                high_linkage=bool(avg_corr >= threshold),
            )
        )

    reports.sort(key=lambda r: -r.intra_sector_avg_corr)
    return reports


def factor_dispersion_by_sector(signal: pd.DataFrame, sector_map: dict[str, str]) -> dict[str, float]:
    """因子在板块成员间的截面分散度（拥挤度先行指标：分散度下降 = 拥挤度上升）。

    Returns:
        {sector: 逐日截面 std 的时间均值}；成员 <2 的板块跳过。
    """
    out: dict[str, float] = {}
    if signal is None or signal.empty:
        return out
    sectors: dict[str, list[str]] = {}
    for sym, sec in sector_map.items():
        if sym in signal.columns:
            sectors.setdefault(sec, []).append(sym)
    for sec, members in sectors.items():
        if len(members) < 2:
            continue
        disp_vals: list[float] = []
        for d in range(len(signal)):
            row = signal[members].iloc[d].to_numpy(dtype=float)
            row_valid = row[np.isfinite(row)]
            if len(row_valid) >= 2:
                disp_vals.append(float(np.std(row_valid)))
        if disp_vals:
            out[sec] = float(np.mean(disp_vals))
    return out


__all__ = [
    "SectorLinkageReport",
    "compute_sector_linkage",
    "factor_dispersion_by_sector",
    "DEFAULT_LINKAGE_THRESHOLD",
]
