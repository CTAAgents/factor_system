"""
fts/factor_engine/barra/barra_style.py — Barra 风格因子体系（GAP-S02）。

实现 Barra CNE6 风格因子（10 大风格）的截面暴露计算，作为因子中性化
与风格归因的基础。每个风格因子对 panel 中每只股票计算原始暴露序列，
再按日期截面做 rank → z-score 标准化，得到可比风格暴露。

10 大风格因子（名称 / 依赖字段 / 经济语义）:
    1.  size              total_market_cap   市值对数（大盘/小盘）
    2.  beta              close, market     市场贝塔（回归系数）
    3.  momentum          close             12-1 月动量（剔除最近 1 月）
    4.  residual_vol      close, market     残差波动率（回归残差 std）
    5.  nonlinear_size    total_market_cap  非线性市值（size 三次项正交化残差）
    6.  book_to_price     pb                 账面市值比（1/PB）
    7.  liquidity         turnover_rate      流动性（换手率对数）
    8.  earnings_yield    pe_ttm, roe       盈利收益率（1/PE 与 ROE 合成）
    9.  growth            revenue_growth    成长（营收/利润增速）
    10. leverage          debt_to_equity    杠杆（资产负债率）

依赖字段缺失时对应风格因子返回全 NaN 列（中性化器自动跳过），
保证在合成数据/字段缺失场景下降级可用。

用法:
    from fts.factor_engine.barra.barra_style import BarraStyleEngine

    engine = BarraStyleEngine()
    exposures = engine.compute_exposures(panel, common_dates)
    # {style_name: pd.DataFrame(index=common_dates, columns=symbols)}

版本: v1.0.0（GAP-S02）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 风格因子定义 ─────────────────────────────────────────

# 风格因子名称顺序（中性化回归时的暴露列顺序）
STYLE_FACTOR_NAMES: tuple[str, ...] = (
    "size",
    "beta",
    "momentum",
    "residual_vol",
    "nonlinear_size",
    "book_to_price",
    "liquidity",
    "earnings_yield",
    "growth",
    "leverage",
)


@dataclass(frozen=True)
class StyleFactorSpec:
    """风格因子元数据。

    Attributes:
        name: 风格因子名称
        required_fields: 依赖数据字段（OHLCV 或基本面列）
        description: 经济语义说明
    """

    name: str
    required_fields: tuple[str, ...]
    description: str


# 各风格因子的依赖字段与语义（实际计算逻辑在 _compute_* 函数中）
STYLE_SPECS: dict[str, StyleFactorSpec] = {
    "size": StyleFactorSpec("size", ("total_market_cap",), "市值对数（大盘/小盘）"),
    "beta": StyleFactorSpec("beta", ("close",), "市场贝塔（与市场等权收益的回归系数）"),
    "momentum": StyleFactorSpec("momentum", ("close",), "12-1 月动量（剔除最近 1 月）"),
    "residual_vol": StyleFactorSpec("residual_vol", ("close",), "残差波动率（日收益对市场回归的残差波动）"),
    "nonlinear_size": StyleFactorSpec("nonlinear_size", ("total_market_cap",), "非线性市值（size 三次项对 size 回归残差）"),
    "book_to_price": StyleFactorSpec("book_to_price", ("pb",), "账面市值比（1/PB）"),
    "liquidity": StyleFactorSpec("liquidity", ("turnover_rate",), "流动性（换手率对数）"),
    "earnings_yield": StyleFactorSpec("earnings_yield", ("pe_ttm", "roe"), "盈利收益率（1/PE 与 ROE 合成）"),
    "growth": StyleFactorSpec("growth", ("revenue_growth", "profit_growth"), "成长（营收/利润同比增速）"),
    "leverage": StyleFactorSpec("leverage", ("debt_to_equity",), "杠杆（资产负债率）"),
}


# ─── 单股票原始暴露计算 ───────────────────────────────────

# 动量窗口（交易日）：过去 252 日，剔除最近 21 日（Barra 惯例）
_MOMENTUM_LOOKBACK: int = 252
_MOMENTUM_SKIP: int = 21
_BETA_WINDOW: int = 252
_VOL_WINDOW: int = 252


def _compute_size(df: pd.DataFrame) -> pd.Series:
    """市值对数：ln(total_market_cap)。"""
    if "total_market_cap" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    cap = pd.to_numeric(df["total_market_cap"], errors="coerce")
    return np.log(cap.replace(0, np.nan))


def _compute_momentum(df: pd.DataFrame) -> pd.Series:
    """12-1 月动量：close[-252] 到 close[-21] 的累计收益。"""
    close = pd.to_numeric(df["close"], errors="coerce")
    result = pd.Series(np.nan, index=df.index)
    if len(close) <= _MOMENTUM_LOOKBACK:
        return result
    past = close.shift(_MOMENTUM_SKIP)
    base = close.shift(_MOMENTUM_LOOKBACK)
    ratio = past / base.replace(0, np.nan)
    result = ratio - 1.0
    result = result.replace([np.inf, -np.inf], np.nan)
    return result


def _market_returns(panel: dict[str, pd.DataFrame]) -> pd.Series:
    """市场等权日收益（用于 beta/残差波动率计算）。"""
    rets: list[pd.Series] = []
    for sym, df in panel.items():
        close = pd.to_numeric(df["close"], errors="coerce")
        r = close.pct_change()
        rets.append(r.rename(sym))
    if not rets:
        return pd.Series(dtype=float)
    market = pd.concat(rets, axis=1).mean(axis=1)
    return market


def _compute_beta(df: pd.DataFrame, market: pd.Series) -> pd.Series:
    """市场贝塔：日收益对市场收益的滚动回归系数。"""
    close = pd.to_numeric(df["close"], errors="coerce")
    stock_ret = close.pct_change()
    aligned = pd.concat([stock_ret.rename("stock"), market.rename("mkt")], axis=1).dropna()
    if len(aligned) < 30:
        return pd.Series(np.nan, index=df.index)
    # 全样本单一 beta（简化；滚动 beta 在数据充足时更贴近 Barra）
    cov = np.cov(aligned["stock"], aligned["mkt"])
    if cov[1, 1] < 1e-12:
        return pd.Series(np.nan, index=df.index)
    beta = cov[0, 1] / cov[1, 1]
    return pd.Series(beta, index=df.index)


def _compute_residual_vol(df: pd.DataFrame, market: pd.Series) -> pd.Series:
    """残差波动率：日收益对市场回归的残差 std（年化）。"""
    close = pd.to_numeric(df["close"], errors="coerce")
    stock_ret = close.pct_change()
    aligned = pd.concat([stock_ret.rename("stock"), market.rename("mkt")], axis=1).dropna()
    if len(aligned) < 30:
        return pd.Series(np.nan, index=df.index)
    x = aligned["mkt"].values
    y = aligned["stock"].values
    if np.std(x) < 1e-12:
        return pd.Series(np.nan, index=df.index)
    # OLS 残差
    a = np.polyfit(x, y, 1)
    resid = y - np.polyval(a, x)
    vol = np.std(resid) * np.sqrt(252)
    return pd.Series(vol, index=df.index)


def _compute_nonlinear_size(df: pd.DataFrame) -> pd.Series:
    """非线性市值占位（截面计算在引擎层基于 size 暴露矩阵完成）。

    非线性市值本质是横截面依赖因子（size 三次项对 size 回归残差），
    逐股票时序计算无意义（市值常量为常量序列）。引擎层对 size 暴露
    矩阵逐日截面计算 z³ 对 z 回归残差，本函数仅返回 NaN 占位。
    """
    return pd.Series(np.nan, index=df.index)


def _compute_book_to_price(df: pd.DataFrame) -> pd.Series:
    """账面市值比：1/PB。"""
    if "pb" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    pb = pd.to_numeric(df["pb"], errors="coerce")
    return (1.0 / pb.replace(0, np.nan))


def _compute_liquidity(df: pd.DataFrame) -> pd.Series:
    """流动性：换手率对数。"""
    if "turnover_rate" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    tr = pd.to_numeric(df["turnover_rate"], errors="coerce")
    return np.log(tr.replace(0, np.nan).clip(lower=1e-6))


def _compute_earnings_yield(df: pd.DataFrame) -> pd.Series:
    """盈利收益率：1/PE 与 ROE 的等权合成。"""
    parts: list[pd.Series] = []
    if "pe_ttm" in df.columns:
        pe = pd.to_numeric(df["pe_ttm"], errors="coerce")
        parts.append((1.0 / pe.replace(0, np.nan)).rename("ep"))
    if "roe" in df.columns:
        roe = pd.to_numeric(df["roe"], errors="coerce")
        parts.append(roe.rename("roe"))
    if not parts:
        return pd.Series(np.nan, index=df.index)
    joined = pd.concat(parts, axis=1)
    return joined.mean(axis=1)


def _compute_growth(df: pd.DataFrame) -> pd.Series:
    """成长：营收/利润同比增速取均值。"""
    parts: list[pd.Series] = []
    for col in ("revenue_growth", "profit_growth"):
        if col in df.columns:
            v = pd.to_numeric(df[col], errors="coerce")
            parts.append(v.rename(col))
    if not parts:
        return pd.Series(np.nan, index=df.index)
    return pd.concat(parts, axis=1).mean(axis=1)


def _compute_leverage(df: pd.DataFrame) -> pd.Series:
    """杠杆：资产负债率。"""
    if "debt_to_equity" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    dte = pd.to_numeric(df["debt_to_equity"], errors="coerce")
    return dte


# 风格因子 → 计算函数映射
_STYLE_COMPUTERS: dict[str, Callable[..., pd.Series]] = {
    "size": _compute_size,
    "beta": _compute_beta,
    "momentum": _compute_momentum,
    "residual_vol": _compute_residual_vol,
    "nonlinear_size": _compute_nonlinear_size,
    "book_to_price": _compute_book_to_price,
    "liquidity": _compute_liquidity,
    "earnings_yield": _compute_earnings_yield,
    "growth": _compute_growth,
    "leverage": _compute_leverage,
}

# 需要市场收益的风格因子（多参数签名）
_MARKET_DEPENDENT_STYLES: frozenset[str] = frozenset({"beta", "residual_vol"})


# ─── 截面标准化 ───────────────────────────────────────────

def _cross_section_zscore(
    exposure: pd.DataFrame,
) -> pd.DataFrame:
    """按日期截面做 rank → z-score 标准化。

    Args:
        exposure: DataFrame(index=dates, columns=symbols)，原始暴露

    Returns:
        标准化后的暴露（每行均值为 0、方差近似 1；NaN 保留）
    """
    result = exposure.copy()
    ranked = exposure.rank(axis=1, pct=True)  # [0, 1]
    mean = ranked.mean(axis=1)
    std = ranked.std(axis=1).replace(0, np.nan)
    z = (ranked.sub(mean, axis=0)).div(std, axis=0)
    result.loc[:] = z.values
    return result


def _nonlinear_size_from_size(size_df: pd.DataFrame) -> pd.DataFrame:
    """由标准化 size 暴露计算非线性市值暴露。

    逐日截面：y = z³ 对 x = z 做线性回归，残差即 nonlinear_size 暴露。
    回归残差自动剥离 size 的线性分量，仅保留非线性（立方）部分。

    Args:
        size_df: 标准化 size 暴露（index=dates, columns=symbols）

    Returns:
        非线性市值暴露（同形状；样本不足行返回 NaN）
    """
    result = pd.DataFrame(np.nan, index=size_df.index, columns=size_df.columns)
    for t in range(size_df.shape[0]):
        row = size_df.iloc[t].values
        valid = ~np.isnan(row)
        n_valid = int(valid.sum())
        if n_valid < 10:
            continue
        x = row[valid]
        y = x ** 3.0
        try:
            coef = np.polyfit(x, y, 1)
            resid = y - np.polyval(coef, x)
        except (np.linalg.LinAlgError, ValueError):
            continue
        result.iloc[t, valid] = resid
    return result


# ─── 引擎 ─────────────────────────────────────────────────

class BarraStyleEngine:
    """Barra 风格暴露计算引擎。

    计算 10 大风格因子的截面标准化暴露，供 BarraNeutralizer 使用。

    用法:
        engine = BarraStyleEngine()
        exposures = engine.compute_exposures(panel, common_dates)
        # {style_name: pd.DataFrame(index=dates, columns=symbols)}
    """

    def __init__(self, style_names: Optional[list[str]] = None) -> None:
        """初始化引擎。

        Args:
            style_names: 启用的风格因子列表（None=全部 10 个）
        """
        self.style_names: list[str] = list(style_names) if style_names else list(STYLE_FACTOR_NAMES)
        for name in self.style_names:
            if name not in _STYLE_COMPUTERS:
                raise ValueError(f"未知风格因子: {name}，可用: {STYLE_FACTOR_NAMES}")

    def compute_exposures(
        self,
        panel: dict[str, pd.DataFrame],
        common_dates: pd.DatetimeIndex,
    ) -> dict[str, pd.DataFrame]:
        """计算各风格因子的截面标准化暴露。

        Args:
            panel: {symbol: OHLCV(+基本面) DataFrame}
            common_dates: 所有标的共有日期索引

        Returns:
            {style_name: DataFrame(index=common_dates, columns=symbols)}
            各风格因子独立；字段缺失时对应风格全 NaN（中性化器自动跳过）
        """
        symbols = sorted(panel.keys())
        exposures: dict[str, pd.DataFrame] = {}

        # 市场收益（beta/residual_vol 依赖）
        market = _market_returns(panel)

        for style in self.style_names:
            computer = _STYLE_COMPUTERS[style]
            series_dict: dict[str, pd.Series] = {}
            for sym in symbols:
                df = panel[sym]
                try:
                    if style in _MARKET_DEPENDENT_STYLES:
                        s = computer(df, market)
                    else:
                        s = computer(df)
                    series_dict[sym] = s.reindex(common_dates)
                except Exception:  # noqa: BLE001 — 单标的失败不阻断整体
                    series_dict[sym] = pd.Series(np.nan, index=common_dates)
            raw = pd.DataFrame(series_dict, index=common_dates)
            if raw.empty or raw.shape[1] == 0:
                exposures[style] = raw
                continue
            # 字段完全缺失（全 NaN）时原样返回，由中性化器跳过
            if raw.isna().all().all():
                exposures[style] = raw
                continue
            exposures[style] = _cross_section_zscore(raw)

        # nonlinear_size（横截面依赖）：基于标准化 size 暴露矩阵，
        # 逐日计算 z³ 对 z 的回归残差（Barra 惯例：剥离 size 的立方项）
        if "size" in self.style_names and "nonlinear_size" in self.style_names:
            size_df = exposures.get("size")
            if size_df is not None and not size_df.isna().all().all():
                exposures["nonlinear_size"] = _nonlinear_size_from_size(size_df)

        return exposures


__all__ = [
    "STYLE_FACTOR_NAMES",
    "STYLE_SPECS",
    "StyleFactorSpec",
    "BarraStyleEngine",
]
