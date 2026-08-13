"""
fts/factor_engine/barra/barra_neutralizer.py — Barra 风格中性化器（GAP-S02）。

对因子信号矩阵做多因子横截面回归，取残差作为"纯 alpha"信号：

    signal = Σ β_style × style_exposure + industry_dummies + ε

- style 暴露矩阵（BarraStyleEngine 输出）作为回归自变量
- 行业虚拟变量可选（与 GAP-S01 行业中性化衔接，二者可叠加）
- 残差 ε 即剥离风格/行业暴露后的纯净信号

实现要点:
    - 逐日截面 OLS（numpy lstsq），无 scipy/statsmodels 重依赖
    - 风格暴露全 NaN（字段缺失）时自动跳过该风格
    - 样本过少（< n_exposures + 2）时降级为仅去均值，不抛异常
    - 返回残差矩阵保持原形状，NaN 位置保留

用法:
    from fts.factor_engine.barra.barra_neutralizer import barra_neutralize_matrix

    residual = barra_neutralize_matrix(
        signal_matrix, symbols_list, style_exposures,
        industry_map=None, cap_map=None,
    )

版本: v1.0.0（GAP-S02）
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from .barra_style import STYLE_FACTOR_NAMES

logger = logging.getLogger(__name__)


def _build_industry_dummies(
    symbols_list: list[str],
    industry_map: Optional[dict[str, str]],
) -> tuple[np.ndarray, list[str]]:
    """构建行业虚拟变量矩阵（n_stocks, n_industries）。

    Args:
        symbols_list: 标的列表
        industry_map: {symbol: industry}（None/空 → 无行业列）

    Returns:
        (dummies, industry_names)
    """
    if not industry_map:
        return np.zeros((len(symbols_list), 0)), []
    industries: list[str] = []
    for sym in symbols_list:
        ind = industry_map.get(sym, "UNKNOWN")
        if ind not in industries:
            industries.append(ind)
    n_industries = len(industries)
    if n_industries == 0:
        return np.zeros((len(symbols_list), 0)), []
    dummies = np.zeros((len(symbols_list), n_industries))
    for i, sym in enumerate(symbols_list):
        ind = industry_map.get(sym, "UNKNOWN")
        j = industries.index(ind)
        dummies[i, j] = 1.0
    return dummies, industries


def _deseasonalize_time_series(
    signal_matrix: np.ndarray,
    dates: Optional[pd.DatetimeIndex],
    min_samples: int = 15,
) -> np.ndarray:
    """逐品种时序月度去季节化（G10，v2.103.0+15）。

    对每列信号做 OLS: signal ~ 截距 + 11 个月哑变量(2..12，1 月为基准)，
    取残差剥离日历季节性（如"一月效应"）。主力连续合约下同交易日各品种月份
    相同，月份哑变量无法进截面回归（常数列被剔除），故采用时序路径。

    Args:
        signal_matrix: (n_dates, n_stocks) 信号矩阵
        dates: 与行一一对应的日期索引；None 或长度不匹配 → 原样返回
        min_samples: 有效样本下限（不足时该列跳过，避免过拟合）

    Returns:
        去季节化残差矩阵（形状保持，NaN 位置保留）
    """
    if dates is None or len(dates) != signal_matrix.shape[0]:
        return signal_matrix
    months = dates.month.to_numpy()  # 1-12
    result = signal_matrix.copy()
    n_dates, n_stocks = signal_matrix.shape
    for j in range(n_stocks):
        col = signal_matrix[:, j]
        valid = ~np.isnan(col)
        n_valid = int(np.sum(valid))
        if n_valid < min_samples:
            continue
        y = col[valid].astype(float)
        if np.std(y) < 1e-12:
            continue
        m = months[valid]
        # 11 个哑变量（2..12）+ 截距；1 月为基准避免共线
        X = np.zeros((n_valid, 12))
        X[:, 0] = 1.0
        for k in range(2, 13):
            X[:, k - 1] = (m == k).astype(float)
        try:
            coef, *_ = np.linalg.lstsq(X, y, rcond=None)
            fitted = X @ coef
        except np.linalg.LinAlgError:
            continue
        result[valid, j] = y - fitted
    return result


def barra_neutralize_matrix(
    signal_matrix: np.ndarray,
    symbols_list: list[str],
    style_exposures: dict[str, Any],
    industry_map: Optional[dict[str, str]] = None,
    cap_map: Optional[dict[str, float]] = None,
    min_samples_factor: float = 1.5,
    vol_map: Optional[dict[str, float]] = None,
    dates: Optional[pd.DatetimeIndex] = None,
    include_vol_neutral: bool = True,
    include_season_neutral: bool = True,
) -> np.ndarray:
    """Barra 风格 + 行业 + 波动率/季节性中性化：逐日截面回归取残差。

    Args:
        signal_matrix: (n_dates, n_stocks) 信号矩阵（行业中性化后）
        symbols_list: 标的列表，与列顺序一致
        style_exposures: {style_name: DataFrame(index=dates, columns=symbols)}
            标准化风格暴露（BarraStyleEngine 输出）；缺失风格自动跳过
        industry_map: {symbol: industry}（可选，与行业去均值叠加）
        cap_map: {symbol: market_cap}（预留，市值加权残差暂不支持，保留签名兼容）
        min_samples_factor: 最少样本数 = 自变量数 × 该系数；不足时降级去均值
        vol_map: {symbol: 年化波动率}（G10，可选；开启波动率中性化时加入截面列）
        dates: 与行一一对应的日期索引（G10，可选；开启季节中性化时做时序去季节化）
        include_vol_neutral: 是否加入波动率截面列（默认 True）
        include_season_neutral: 是否做时序月度去季节化（默认 True）

    Returns:
        残差矩阵（n_dates, n_stocks），NaN 位置保留，其余位置为回归残差
    """
    n_dates, n_stocks = signal_matrix.shape
    if n_stocks == 0 or n_dates == 0:
        return signal_matrix.copy()

    result = signal_matrix.copy()

    # G10 时序去季节化（先剥离日历季节性，再做截面回归）
    if include_season_neutral:
        result = _deseasonalize_time_series(result, dates)

    # 行业虚拟变量（所有日期共享结构）
    industry_dummies, _ = _build_industry_dummies(symbols_list, industry_map)

    # G10 波动率截面列（静态暴露，对标股票市值；全 NaN 品种自动跳过）
    vol_flat: Optional[np.ndarray] = None
    if include_vol_neutral and vol_map:
        vol_flat = np.array([vol_map.get(sym, np.nan) for sym in symbols_list], dtype=float)
        if np.isnan(vol_flat).all():
            vol_flat = None

    # 汇总每期可用的风格暴露矩阵（style → (dates_index, matrix)）
    # 假设所有暴露 DataFrame 与 signal_matrix 日期行一一对应
    style_names: list[str] = []
    style_mats: list[np.ndarray] = []
    for style in STYLE_FACTOR_NAMES:
        df = style_exposures.get(style)
        if df is None or df.empty:
            continue
        mat = df.values  # (n_dates, n_stocks)
        if mat.shape != (n_dates, n_stocks) or np.isnan(mat).all():
            continue
        style_names.append(style)
        style_mats.append(mat)

    n_styles = len(style_names)

    for t in range(n_dates):
        sig_t = result[t, :]
        valid = ~np.isnan(sig_t)

        # 构建回归矩阵 X = [行业虚拟 | 波动率 | 风格暴露]
        x_cols: list[np.ndarray] = []
        if industry_dummies.shape[1] > 0:
            x_cols.append(industry_dummies)
        if vol_flat is not None:
            x_cols.append(vol_flat.reshape(-1, 1))
        for m in style_mats:
            x_cols.append(m[t, :].reshape(-1, 1))

        if not x_cols:
            continue  # 无自变量 → 保持原信号

        X = np.hstack(x_cols)  # (n_stocks, n_cols)
        n_cols = X.shape[1]

        # 仅用有效样本
        x_valid = X[valid, :]
        y_valid = sig_t[valid]
        n_valid = len(y_valid)

        if n_valid < max(int(n_cols * min_samples_factor), 3):
            # 样本不足 → 降级为整体去均值（保持有界）
            y_mean = np.nanmean(sig_t)
            if not np.isnan(y_mean):
                result[t, valid] = sig_t[valid] - y_mean
            continue

        # 常数列处理：行业虚拟可能因单行业样本退化为常数列 → 剔除
        keep: list[int] = []
        for c in range(n_cols):
            col = x_valid[:, c]
            if np.std(col) > 1e-12:
                keep.append(c)
        if not keep:
            y_mean = np.nanmean(sig_t)
            if not np.isnan(y_mean):
                result[t, valid] = sig_t[valid] - y_mean
            continue
        X_final = x_valid[:, keep]

        # G10: 波动率列在场时补截距列 → 残差截面均值归零，
        # 消除无截距回归下"时间均值残差与波动率"的相关性偏差（仅新功能路径生效）
        if vol_flat is not None:
            X_final = np.hstack([np.ones((X_final.shape[0], 1)), X_final])

        # OLS: β = (X'X)^-1 X'y
        try:
            coef, *_ = np.linalg.lstsq(X_final, y_valid, rcond=None)
        except np.linalg.LinAlgError:
            y_mean = np.nanmean(sig_t)
            if not np.isnan(y_mean):
                result[t, valid] = sig_t[valid] - y_mean
            continue

        fitted = X_final @ coef
        result[t, valid] = y_valid - fitted

    if n_styles > 0:
        logger.debug(
            "[BarraNeutralizer] 完成 %d 期截面回归，使用 %d 个风格暴露 + %d 行业列",
            n_dates,
            n_styles,
            industry_dummies.shape[1] if industry_dummies.size else 0,
        )
    return result


__all__ = [
    "barra_neutralize_matrix",
    "STYLE_FACTOR_NAMES",
]
