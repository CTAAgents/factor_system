"""
fts/factor_engine/microstructure_factors.py — Level2 订单流微观结构因子（GAP-I503 首期）

基于 tick 逐笔快照（含 5 档盘口）计算微观结构 alpha 因子：

- **订单流不平衡（Order Flow Imbalance, OFI）**：按 tick 方向（价格上涨=买方主动、
  下跌=卖方主动）聚合主动买卖量差 / 总量，滚动窗口归一化 ∈ [-1, 1]。
- **大单占比（Large Trade Ratio, LTR）**：单笔成交量（累计量差分）超过阈值
  （绝对手数 或 滚动均量倍数）的大单成交量占总成交量比例。
- **盘口不平衡（Order Book Imbalance, OBI）**：(买深 - 卖深) / (买深 + 卖深)，
  基于 5 档盘口深度。

数据口径：
- 输入 tick 快照的 `volume` 为当日累计成交量，单笔量 = 差分（换日重置为负 → clip 0）。
- `last_price` 快照价差判定买卖方向；持平 tick 沿用前向方向（flat 延续）。
- 全部 pandas 向量化，输入不足 min_rows 时返回空 DataFrame（降级不抛错）。

HARNESS §5.3 契约优先: 输出列契约冻结 —— datetime/direction/trade_volume/ofi/obi/large_trade_ratio。

设计文档: docs/harness/plans/23-institutional-transformation-plan.md（GAP-I503 首期）
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 输出列契约（冻结）
FACTOR_COLUMNS: list[str] = [
    "datetime",
    "direction",  # +1 买方主动 / -1 卖方主动 / 0 持平（延续或无法判定）
    "trade_volume",  # 单笔成交量（累计量差分，clip >= 0）
    "ofi",  # 订单流不平衡（滚动窗口，[-1, 1]）
    "obi",  # 盘口不平衡（5 档深度，[-1, 1]）
    "large_trade_ratio",  # 大单成交量占比（滚动窗口，[0, 1]）
]


@dataclass
class MicrostructureConfig:
    """订单流微观结构因子配置。

    Attributes:
        window: OFI / 大单占比滚动窗口（tick 数）。
        large_threshold_abs: 大单绝对成交量阈值（手）；None = 不启用绝对阈值。
        large_threshold_mult: 大单相对阈值 = 滚动均量 × 该倍数。
        min_rows: 最少 tick 数，不足返回空 DataFrame（降级）。
    """

    window: int = 20
    large_threshold_abs: Optional[float] = None
    large_threshold_mult: float = 3.0
    min_rows: int = 20

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError(f"window 必须 >= 1，收到 {self.window}")
        if self.large_threshold_mult <= 0:
            raise ValueError(f"large_threshold_mult 必须 > 0，收到 {self.large_threshold_mult}")
        if self.min_rows < 1:
            raise ValueError(f"min_rows 必须 >= 1，收到 {self.min_rows}")


def _prepare_tick_frame(tick_df: pd.DataFrame) -> pd.DataFrame:
    """校验并补齐 tick 输入：排序 + 单笔量差分。

    Args:
        tick_df: tick 快照（含 datetime/last_price/volume，5 档盘口可选）。

    Returns:
        按 datetime 升序的副本，含 datetime/last_price/trade_volume；
        缺必需列时返回空 DataFrame。
    """
    if tick_df is None or tick_df.empty:
        return pd.DataFrame()
    needed = {"datetime", "last_price", "volume"}
    if not needed.issubset(tick_df.columns):
        logger.warning("[microstructure] 缺必需列 %s，输入列=%s", needed - set(tick_df.columns), list(tick_df.columns))
        return pd.DataFrame()

    df = tick_df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.dropna(subset=["datetime", "last_price"]).sort_values("datetime").reset_index(drop=True)
    df["last_price"] = pd.to_numeric(df["last_price"], errors="coerce")
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

    # 单笔成交量 = 累计量差分（换日重置为负 → clip 0，首行 NaN → 0）
    trade_volume = df["volume"].diff()
    trade_volume.iloc[0] = 0.0
    df["trade_volume"] = trade_volume.fillna(0.0).clip(lower=0.0)
    return df


def classify_tick_direction(tick_df: pd.DataFrame) -> pd.Series:
    """按快照价差判定 tick 买卖方向。

    - last_price 上升 → +1（买方主动）
    - last_price 下降 → -1（卖方主动）
    - 持平 → 沿用前一条方向（flat 延续）；首条为 0

    Returns:
        int64 Series，长度与输入一致；输入非法返回空 Series。
    """
    df = _prepare_tick_frame(tick_df)
    if df.empty:
        return pd.Series(dtype="int64")

    price_diff = df["last_price"].diff()
    direction = np.where(price_diff > 0, 1, np.where(price_diff < 0, -1, 0))
    direction = pd.Series(direction, dtype="int64")
    # 持平 tick 沿用前向方向（首条 NaN → 0）
    direction.iloc[0] = 0
    direction = direction.mask(direction == 0).ffill().fillna(0).astype("int64")
    return direction


def order_flow_imbalance(tick_df: pd.DataFrame, window: int = 20) -> pd.Series:
    """订单流不平衡（OFI）：滚动窗口内 (Σ主动买量 - Σ主动卖量) / Σ总主动量。

    Returns:
        float64 Series ∈ [-1, 1]；分母为 0 时该点记 0；输入非法返回空 Series。
    """
    df = _prepare_tick_frame(tick_df)
    if df.empty:
        return pd.Series(dtype="float64")

    direction = classify_tick_direction(df)
    signed = direction * df["trade_volume"]
    buy_vol = signed.clip(lower=0.0).rolling(window, min_periods=2).sum()
    sell_vol = (-signed).clip(lower=0.0).rolling(window, min_periods=2).sum()
    total = buy_vol + sell_vol
    ofi = (buy_vol - sell_vol) / total.replace(0.0, np.nan)
    return ofi.fillna(0.0)


def order_book_imbalance(tick_df: pd.DataFrame) -> pd.Series:
    """盘口不平衡（OBI）：(5 档买深 - 5 档卖深) / (买深 + 卖深)，逐 tick。

    Returns:
        float64 Series ∈ [-1, 1]；缺盘口列或分母为 0 记 0。
    """
    df = _prepare_tick_frame(tick_df)
    if df.empty:
        return pd.Series(dtype="float64")

    bid_cols = [f"bid_volume{i}" for i in range(1, 6)]
    ask_cols = [f"ask_volume{i}" for i in range(1, 6)]
    if not set(bid_cols + ask_cols).issubset(df.columns):
        return pd.Series(0.0, index=df.index)

    bid_depth = df[bid_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1).fillna(0.0)
    ask_depth = df[ask_cols].apply(pd.to_numeric, errors="coerce").sum(axis=1).fillna(0.0)
    total = bid_depth + ask_depth
    obi = (bid_depth - ask_depth) / total.replace(0.0, np.nan)
    return obi.fillna(0.0)


def large_trade_ratio(tick_df: pd.DataFrame, config: MicrostructureConfig) -> pd.Series:
    """大单占比（LTR）：滚动窗口内大单成交量 / 总成交量。

    大单判定：单笔量 >= max(large_threshold_abs, 滚动均量 × large_threshold_mult)。

    Returns:
        float64 Series ∈ [0, 1]；分母为 0 记 0。
    """
    df = _prepare_tick_frame(tick_df)
    if df.empty:
        return pd.Series(dtype="float64")

    tv = df["trade_volume"]
    # 滚动均量（min_periods=2 避免首点分母 0）
    mean_vol = tv.rolling(config.window, min_periods=2).mean()
    threshold = mean_vol * config.large_threshold_mult
    if config.large_threshold_abs is not None:
        threshold = threshold.where(threshold >= config.large_threshold_abs, config.large_threshold_abs)

    large_mask = tv >= threshold
    large_sum = tv.where(large_mask, 0.0).rolling(config.window, min_periods=2).sum()
    total = tv.rolling(config.window, min_periods=2).sum()
    ratio = large_sum / total.replace(0.0, np.nan)
    return ratio.fillna(0.0)


def compute_microstructure_factors(
    tick_df: pd.DataFrame,
    config: Optional[MicrostructureConfig] = None,
) -> pd.DataFrame:
    """统一入口：计算订单流微观结构因子集（契约列见 FACTOR_COLUMNS）。

    Args:
        tick_df: tick 快照 DataFrame（datetime/last_price/volume + 5 档盘口）。
        config: 因子配置；None 使用默认。

    Returns:
        FACTOR_COLUMNS 列契约的 DataFrame；输入非法或不足 min_rows 返回空 DataFrame（降级）。
    """
    cfg = config or MicrostructureConfig()
    df = _prepare_tick_frame(tick_df)
    if df.empty or len(df) < cfg.min_rows:
        return pd.DataFrame(columns=FACTOR_COLUMNS)

    direction = classify_tick_direction(df)
    result = pd.DataFrame(
        {
            "datetime": df["datetime"],
            "direction": direction.values,
            "trade_volume": df["trade_volume"].values,
            "ofi": order_flow_imbalance(df, cfg.window).values,
            "obi": order_book_imbalance(df).values,
            "large_trade_ratio": large_trade_ratio(df, cfg).values,
        },
        index=df.index,
    )
    return result[FACTOR_COLUMNS].reset_index(drop=True)


__all__ = [
    "MicrostructureConfig",
    "FACTOR_COLUMNS",
    "classify_tick_direction",
    "order_flow_imbalance",
    "order_book_imbalance",
    "large_trade_ratio",
    "compute_microstructure_factors",
]
