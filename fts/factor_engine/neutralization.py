"""
fts.factor_engine.neutralization — 横截面因子中性化（D.2 股票 L3 补齐）。

在因子信号层剥离行业 / 市值偏好，消除因子作为行业 / 市值 proxy 的"伪预测力"：
    - ``industry_neutralize``: 行业组内去均值（组内 mean 归零，跨行业量纲保留）
    - ``size_neutralize``:    log 市值 OLS 回归取残差（剥离市值线性偏好）
    - ``cross_section_neutralize``: 逐交易日施加 行业 + 市值 中性化

降级语义（FTS 通用兜底原则）:
    - 行业映射缺失 / 为空 / 组内仅 1 只 → 对应跳过或归零，不抛错
    - 市值映射缺失 / 样本不足 / 市值无区分度 → 原样返回
    - 全 NaN / 全常数截面 → 原样返回

FTS 角色边界: 只做信号层处理，不涉及真实组合暴露管理（组合级暴露中性化由
``PortfolioOptimizer`` GAP-L304 负责）。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

__all__ = ["industry_neutralize", "size_neutralize", "cross_section_neutralize"]


def industry_neutralize(signal: pd.Series, industry_map: dict[str, str]) -> pd.Series:
    """行业中性化：组内去均值。

    - 无行业映射的 symbol：保留原值（不误杀）
    - 行业组内仅 1 只：该股信号归零（无组内相对信息）
    - 全 NaN / 全常数：原样返回

    Args:
        signal: index=symbol 的信号序列
        industry_map: {symbol: industry_name}

    Returns:
        中性化后的信号序列（新对象，不修改入参）。
    """
    out = signal.copy().astype(float)
    groups = pd.Series([industry_map.get(s, "unknown") for s in out.index], index=out.index)
    for ind, idx in groups.groupby(groups).groups.items():
        if ind == "unknown":
            continue
        vals = out.loc[idx]
        if len(vals) <= 1:
            out.loc[idx] = 0.0
            continue
        mean = float(np.nanmean(vals)) if np.isfinite(vals).any() else np.nan
        if not np.isfinite(mean):
            continue
        out.loc[idx] = vals - mean
    return out


def size_neutralize(signal: pd.Series, cap_map: dict[str, float]) -> pd.Series:
    """市值中性化：对 log 市值做 OLS 回归，取残差。

    signal_resid = signal − (a + b * log_cap)
    - 市值缺失的 symbol：保留原值（不误杀）
    - 有效样本 < 5：不做回归，原样返回
    - 市值方差 < 1e-12：无区分度，原样返回

    Args:
        signal: index=symbol 的信号序列
        cap_map: {symbol: market_cap}（原始市值，内部取 log）

    Returns:
        中性化后的信号序列（新对象，不修改入参）。
    """
    out = signal.copy().astype(float)
    caps = pd.Series(
        [np.log(float(cap_map.get(s, np.nan))) if cap_map.get(s) is not None else np.nan for s in out.index],
        index=out.index,
        dtype=float,
    )
    mask = caps.notna() & out.notna()
    if int(mask.sum()) < 5:
        return out
    x = caps[mask].values
    y = out[mask].values
    if float(np.nanstd(x)) < 1e-12:
        return out
    a, b = np.polyfit(x, y, 1)  # a=斜率, b=截距（polyfit 返回 [最高次, 常数项]）
    resid = y - (a * x + b)
    out.loc[mask] = resid
    return out


def cross_section_neutralize(
    signal_matrix: pd.DataFrame,
    industry_map: Optional[dict[str, str]] = None,
    cap_map: Optional[dict[str, float]] = None,
) -> pd.DataFrame:
    """按交易日逐行施加行业 + 市值中性化（行业先去均值，再市值回归残差）。

    - 任一映射缺失 / 为空 → 对应步骤跳过
    - 两者皆无 → 原样返回（向后兼容）
    - 逐行隔离异常：单日全 NaN 跳过，不影响其他交易日

    Args:
        signal_matrix: index=date, columns=symbol 的截面信号矩阵
        industry_map: {symbol: industry_name}，None/空跳过行业中性化
        cap_map: {symbol: market_cap}，None/空跳过市值中性化

    Returns:
        中性化后的信号矩阵（新对象，不修改入参）。
    """
    if industry_map is None and cap_map is None:
        return signal_matrix.copy()

    out = signal_matrix.copy()
    for dt, row in out.iterrows():
        r = row.dropna()
        if r.empty:
            continue
        if industry_map:
            r = industry_neutralize(r, industry_map)
        if cap_map:
            r = size_neutralize(r, cap_map)
        out.loc[dt, r.index] = r
    return out
