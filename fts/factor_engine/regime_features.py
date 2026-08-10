"""
fts.factor_engine.regime_features — 扩展特征提取模块（STEP3 P2.1）

提供超越基础 [收益率, 波动率] 的扩展特征集，用于 HMM 和规则方法。

当前特征:
  - 成交量冲击因子 (Volume Shock)
  - 收益率偏度 (Skewness)
  - 收益率峰度 (Kurtosis)
  - 自相关系数 (Autocorrelation)
  - 日内波幅比 (Intraday Range Ratio)
  - 跨品种相关系数 (Cross-Symbol Correlation)

用法:
    from fts.factor_engine.regime_features import compute_extended_features
    features = compute_extended_features(ohlcv)

版本: v0.1.0
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 默认参数 ──────────────────────────────────────────────

_DEFAULT_ROLLING = 20  # 滚动窗口默认值


# ─── 单个特征提取 ──────────────────────────────────────────


def volume_shock(ohlcv: pd.DataFrame, window: int = _DEFAULT_ROLLING) -> float:
    """成交量冲击因子: (vol - vol_ma) / vol_ma

    衡量成交量相对于移动均线的偏离程度。
    正值表示放量，负值表示缩量。

    参数:
        ohlcv:  OHLCV DataFrame。
        window: 移动均线窗口。

    返回:
        成交量冲击值（无量纲）。
    """
    if ohlcv is None or ohlcv.empty or "volume" not in ohlcv.columns:
        return 0.0
    volume = ohlcv["volume"].fillna(0)
    vol_ma = volume.rolling(window, min_periods=1).mean()
    if vol_ma.iloc[-1] < 1e-12:
        return 0.0
    return float((volume.iloc[-1] - vol_ma.iloc[-1]) / vol_ma.iloc[-1])


def return_skewness(ohlcv: pd.DataFrame, window: int = _DEFAULT_ROLLING) -> float:
    """收益率偏度: 滚动窗口内收益率分布的不对称性。

    正值表示右偏（较多大涨），负值表示左偏（较多大跌）。

    参数:
        ohlcv:  OHLCV DataFrame。
        window: 滚动窗口。

    返回:
        偏度值。
    """
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return 0.0
    close = ohlcv["close"].dropna()
    if len(close) < window + 1:
        return 0.0
    rets = close.pct_change().dropna()
    if len(rets) < window:
        return 0.0
    rolling = rets.iloc[-window:]
    if rolling.std() < 1e-12:
        return 0.0
    return float(rolling.skew())


def return_kurtosis(ohlcv: pd.DataFrame, window: int = _DEFAULT_ROLLING) -> float:
    """收益率峰度: 滚动窗口内收益率分布的厚尾程度。

    正态分布峰度为 3，> 3 表示厚尾（极端事件多）。

    参数:
        ohlcv:  OHLCV DataFrame。
        window: 滚动窗口。

    返回:
        峰度值（未中心化，即 Fisher 峰度 + 3）。
    """
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return 0.0
    close = ohlcv["close"].dropna()
    if len(close) < window + 1:
        return 0.0
    rets = close.pct_change().dropna()
    if len(rets) < window:
        return 0.0
    rolling = rets.iloc[-window:]
    if rolling.std() < 1e-12:
        return 0.0
    return float(rolling.kurtosis() + 3)  # 转化为未中心化峰度


def return_autocorr(ohlcv: pd.DataFrame, lag: int = 1, window: int = _DEFAULT_ROLLING) -> float:
    """收益率自相关系数: 衡量收益率序列的动量/反转特性。

    正值表示正自相关（动量），负值表示负自相关（反转）。

    参数:
        ohlcv:  OHLCV DataFrame。
        lag:    滞后阶数。
        window: 滚动窗口。

    返回:
        自相关系数。
    """
    if ohlcv is None or ohlcv.empty or "close" not in ohlcv.columns:
        return 0.0
    close = ohlcv["close"].dropna()
    if len(close) < window + lag + 1:
        return 0.0
    rets = close.pct_change().dropna()
    if len(rets) < window + lag:
        return 0.0
    rolling = rets.iloc[-window:]
    if rolling.std() < 1e-12:
        return 0.0
    return float(rolling.autocorr(lag=lag))


def intraday_range_ratio(ohlcv: pd.DataFrame, window: int = _DEFAULT_ROLLING) -> float:
    """日内波幅比: (high - low) / close 的滚动均值。

    衡量日内波动幅度，与日间波动率互补。

    参数:
        ohlcv:  OHLCV DataFrame。
        window: 滚动窗口。

    返回:
        日内波幅比均值。
    """
    if ohlcv is None or ohlcv.empty:
        return 0.0
    if not all(c in ohlcv.columns for c in ["high", "low", "close"]):
        return 0.0
    high = ohlcv["high"].ffill()
    low = ohlcv["low"].ffill()
    close = ohlcv["close"].ffill()
    if close.iloc[-1] < 1e-12:
        return 0.0
    range_ratio = (high - low) / (close + 1e-12)
    return float(range_ratio.iloc[-window:].mean())


def cross_symbol_correlation(
    panel: dict[str, pd.DataFrame],
    symbols: list[str],
    window: int = _DEFAULT_ROLLING,
) -> float:
    """跨品种相关系数: 产业链内品种收益率的平均相关系数。

    高相关 = 系统性驱动，低相关 = 品种驱动。

    参数:
        panel:   品种行情面板 (symbol → OHLCV DataFrame)。
        symbols: 产业链内的品种代码列表。
        window:  滚动窗口。

    返回:
        平均相关系数（0~1）。数据不足时返回 0.0。
    """
    rets_dict: dict[str, pd.Series] = {}
    for sym in symbols:
        df = panel.get(sym)
        if df is None or df.empty or "close" not in df.columns:
            continue
        close = df["close"].dropna()
        if len(close) < window + 1:
            continue
        rets_dict[sym] = close.pct_change().dropna()

    if len(rets_dict) < 2:
        return 0.0

    rets_df = pd.DataFrame(rets_dict)
    # 取最近 window 行
    recent = rets_df.iloc[-window:]
    corr_matrix = recent.corr()
    # 提取上三角均值（排除对角线）
    triu = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
    values = triu.stack().values
    if len(values) == 0:
        return 0.0
    return float(np.nanmean(values))


# ─── 综合特征提取 ──────────────────────────────────────────


def compute_extended_features(
    ohlcv: pd.DataFrame,
    panel: dict[str, pd.DataFrame] | None = None,
    sector_symbols: list[str] | None = None,
) -> dict[str, Any]:
    """计算所有扩展特征并返回字典。

    参数:
        ohlcv:         当前品种的 OHLCV DataFrame。
        panel:         全品种面板（用于跨品种相关系数）。
        sector_symbols: 产业链品种列表（用于跨品种相关系数）。

    返回:
        特征字典，所有值均为 float。
    """
    features: dict[str, Any] = {}

    # 基础特征
    features["volume_shock"] = round(volume_shock(ohlcv), 6)
    features["skewness"] = round(return_skewness(ohlcv), 6)
    features["kurtosis"] = round(return_kurtosis(ohlcv), 6)
    features["autocorr_lag1"] = round(return_autocorr(ohlcv, lag=1), 6)
    features["autocorr_lag5"] = round(return_autocorr(ohlcv, lag=5), 6)
    features["intraday_range_ratio"] = round(intraday_range_ratio(ohlcv), 6)

    # 跨品种相关性
    if panel is not None and sector_symbols is not None:
        features["cross_corr_mean"] = round(cross_symbol_correlation(panel, sector_symbols), 6)
    else:
        features["cross_corr_mean"] = 0.0

    return features


def compute_hmm_feature_vector(
    ohlcv: pd.DataFrame,
    base_features: np.ndarray | None = None,
    panel: dict[str, pd.DataFrame] | None = None,
    sector_symbols: list[str] | None = None,
) -> np.ndarray:
    """构建增强的 HMM 特征向量。

    在基础 [收益率, 20d 波动率] 基础上，追加扩展特征。

    参数:
        ohlcv:         OHLCV DataFrame。
        base_features: 基础特征矩阵 [N, 2]（收益率, 波动率），None 时自动计算。
        panel:         全品种面板（用于跨品种相关系数）。
        sector_symbols: 产业链品种列表。

    返回:
        增强特征矩阵 [N, 2 + M]，M 为扩展特征数。
    """
    if ohlcv is None or ohlcv.empty:
        return np.array([])

    close = ohlcv["close"].dropna()
    if len(close) < 21:
        return np.array([])

    rets = close.pct_change().dropna()
    if base_features is None:
        rets_vals = rets.to_numpy().reshape(-1, 1)
        vol = rets.rolling(20).std().fillna(0).to_numpy().reshape(-1, 1)
        base_features = np.column_stack([rets_vals, vol])

    n = base_features.shape[0]

    # 计算扩展特征时间序列
    ext_features_list: list[np.ndarray] = []

    # 成交量冲击（滚动）
    if "volume" in ohlcv.columns:
        volume = ohlcv["volume"].reindex(rets.index).fillna(0)
        vol_ma = volume.rolling(20, min_periods=1).mean()
        shock = np.where(vol_ma > 1e-12, (volume - vol_ma) / vol_ma, 0.0).reshape(-1, 1)
        ext_features_list.append(shock[-n:])

    # 偏度（滚动 20d）
    skew = rets.rolling(20, min_periods=5).skew().fillna(0).to_numpy().reshape(-1, 1)
    ext_features_list.append(skew[-n:])

    # 峰度（滚动 20d）
    kurt = (rets.rolling(20, min_periods=5).kurt() + 3).fillna(3).to_numpy().reshape(-1, 1)
    ext_features_list.append(kurt[-n:])

    # 自相关（滚动 20d）
    def _rolling_autocorr(s: pd.Series, lag: int = 1) -> pd.Series:
        return s.rolling(20, min_periods=5).apply(
            lambda x: x.autocorr(lag=lag) if len(x) > lag else 0.0,
            raw=False,
        )

    acf1 = _rolling_autocorr(rets, lag=1).fillna(0).to_numpy().reshape(-1, 1)
    ext_features_list.append(acf1[-n:])

    # 日内波幅比
    if all(c in ohlcv.columns for c in ["high", "low"]):
        high = ohlcv["high"].reindex(rets.index).ffill()
        low = ohlcv["low"].reindex(rets.index).ffill()
        c = close.reindex(rets.index)
        range_ratio = ((high - low) / (c + 1e-12)).fillna(0).to_numpy().reshape(-1, 1)
        ext_features_list.append(range_ratio[-n:])

    if ext_features_list:
        ext_matrix = np.column_stack(ext_features_list)
        return np.column_stack([base_features, ext_matrix])
    return base_features
