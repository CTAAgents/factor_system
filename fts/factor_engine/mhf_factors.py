"""
fts/factor_engine/mhf_factors.py — 中高频（MHF）分钟级因子族（Phase 1）。

面向期货 5m/15m 分钟 K 线，计算时序量价因子（动量/反转/波动/量能/位置），
供混合策略（截面选品种 + 时序进出场）使用。

设计约束（对齐 HARNESS 因子研发红线）:
    - 零未来函数：t 时刻因子值仅由 ≤t 的 bar 计算（全部 shift/rolling 后向窗口）
    - 向量化：纯 pandas，无逐行循环
    - 数值兜底：NaN/Inf → 0.0，不抛错不阻塞
    - 周期无关：5m/15m/30m/60m 输入均可，窗口参数按 bar 数给定

数据规约：输入 DataFrame 含 open/high/low/close/volume，索引为 DatetimeIndex
（或含 datetime 列自动识别），与 multi_frequency.py 约定一致。

设计文档: docs/archive/plans/33-mhf-trading-plan.md §Phase 1
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MhfFactorConfig:
    """分钟因子配置（窗口单位 = bar 数）。"""

    mom_short: int = 5          # 短动量回看
    mom_mid: int = 20           # 中动量回看
    rev_short: int = 5          # 短期偏离均线回看
    rev_mid: int = 20           # 中期偏离均线回看
    vol_std: int = 20           # 波动率滚动窗
    vol_long: int = 60          # 波动率长窗（状态比较）
    volume_ma: int = 20         # 量能均线窗
    range_win: int = 20         # 高低位窗口
    min_rows: int = 60          # 最小 bar 数，不足返回空 dict

    def __post_init__(self) -> None:
        for name in ("mom_short", "mom_mid", "rev_short", "rev_mid",
                     "vol_std", "vol_long", "volume_ma", "range_win"):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} 必须 >= 1")


def _prepare(ohlcv: pd.DataFrame) -> Optional[pd.DataFrame]:
    """归一化输入：datetime 索引 + 数值列；非法输入返回 None。"""
    if ohlcv is None or ohlcv.empty:
        return None
    df = ohlcv.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"]).set_index("datetime")
        else:
            return None
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df.columns:
            return None
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_index()
    if len(df) == 0:
        return None
    return df


def _safe(s: pd.Series) -> pd.Series:
    """数值兜底：Inf/NaN → 0.0（因子值 0 表示中性，不参与方向判断）。"""
    return s.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def compute_mhf_factors(
    ohlcv: pd.DataFrame, config: Optional[MhfFactorConfig] = None
) -> dict[str, pd.Series]:
    """计算单品种分钟因子族。

    Args:
        ohlcv: 分钟 K 线 DataFrame（open/high/low/close/volume）。
        config: 因子配置；None 用默认。

    Returns:
        dict[str, pd.Series]：因子名 → 因子值 Series（与输入索引对齐）；
        bar 数不足 min_rows 或输入非法返回空 dict（降级不抛错）。
    """
    cfg = config or MhfFactorConfig()
    df = _prepare(ohlcv)
    if df is None or len(df) < cfg.min_rows:
        return {}

    close = df["close"]
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # ── 时序动量 ──
    mom_short = close / close.shift(cfg.mom_short) - 1.0
    mom_mid = close / close.shift(cfg.mom_mid) - 1.0
    # ── 短期偏离（反转源）──
    rev_short = close / close.rolling(cfg.rev_short).mean() - 1.0
    rev_mid = close / close.rolling(cfg.rev_mid).mean() - 1.0
    # ── 波动率 ──
    logret = np.log(close).diff()
    vol_std = logret.rolling(cfg.vol_std).std()
    vol_long = logret.rolling(cfg.vol_long).std()
    vol_regime = vol_std / vol_long - 1.0
    # ── 量能 ──
    vol_ratio = volume / volume.rolling(cfg.volume_ma).mean() - 1.0
    # ── 量价同步：收益方向 × 量能强弱（放量上涨/缩量下跌为正向）──
    vp_sync = logret * np.sign(vol_ratio)
    # ── 收益偏度（短期分布形状，反转/极端态信号）──
    ret_skew = logret.rolling(cfg.vol_std).skew()
    # ── 日内动量（当日开盘至当前 bar）──
    day_open = open_.groupby(pd.DatetimeIndex(open_.index).normalize()).transform("first")
    intraday_mom = close / day_open - 1.0
    # ── 高低位位置 ──
    hhv = high.rolling(cfg.range_win).max()
    llv = low.rolling(cfg.range_win).min()
    rng = (hhv - llv).replace(0.0, np.nan)
    pos_range = (close - llv) / rng

    out: dict[str, pd.Series] = {
        "mom_short": _safe(mom_short),
        "mom_mid": _safe(mom_mid),
        "rev_short": _safe(rev_short),
        "rev_mid": _safe(rev_mid),
        "vol_std": _safe(vol_std),
        "vol_regime": _safe(vol_regime),
        "vol_ratio": _safe(vol_ratio),
        "vp_sync": _safe(vp_sync),
        "ret_skew": _safe(ret_skew),
        "intraday_mom": _safe(intraday_mom),
        "pos_range": _safe(pos_range),
    }
    return out


def compute_mhf_factor_panel(
    ohlcv_panel: dict[str, pd.DataFrame],
    config: Optional[MhfFactorConfig] = None,
) -> dict[str, dict[str, pd.Series]]:
    """批量计算多品种因子面板。

    Args:
        ohlcv_panel: {symbol: 分钟 OHLCV DataFrame}。
        config: 因子配置。

    Returns:
        {factor_name: {symbol: Series}}；缺数据品种自动跳过（降级）。
    """
    cfg = config or MhfFactorConfig()
    panel: dict[str, dict[str, pd.Series]] = {}
    for sym, df in ohlcv_panel.items():
        factors = compute_mhf_factors(df, cfg)
        for name, s in factors.items():
            panel.setdefault(name, {})[sym] = s
    return panel
