"""多频信号叠加与冲突消解（GAP-068，Phase J / plans 25 §10）。

分钟级 alpha 信号 → 日频聚合 → 与日频因子信号加权叠加 → 方向冲突消解 →
分钟信号日频持有回测。全链路复用 `get_minute_ohlcv` 4 级降级链
（minute_cache → TDX 17709 → TQ-Local → TQSDK），不新增数据源；
分钟数据不可用时返回 None / 空指标，不阻断日频路径。

数据规约（分钟 K 线）：
    列 open/high/low/close/volume；索引为 DatetimeIndex（或含 datetime 列自动识别）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 分钟数据加载器签名：loader(symbol, days, frequency, trace_id) -> DataFrame
MinuteLoader = Callable[[str, int, str, str], pd.DataFrame]


@dataclass
class MultiFrequencyConfig:
    """多频信号叠加配置（契约见 plans/25 §10.2）。"""

    minute_freqs: list[str] = field(default_factory=lambda: ["5m", "15m", "60m"])
    agg_method: str = "last"          # 分钟→日频聚合: last/mean/max/min
    daily_weight: float = 0.6         # 日频信号权重（分钟权重 = 1 - daily_weight，按频率均分）
    conflict_rule: str = "weighted"   # 冲突消解: weighted / penalty / discard
    min_minute_rows: int = 60         # 单日分钟样本下限，不足该日跳过
    conflict_penalty: float = 0.5     # penalty 模式下冲突时分钟贡献削弱系数
    lookback_days: int = 120          # 分钟数据回溯天数
    trace_id: str = ""


@dataclass
class MultiFrequencyResult:
    """单日多频叠加结果。"""

    date: pd.Timestamp
    daily_signal: float
    minute_agg: dict[str, float]      # freq -> 聚合分钟信号
    blended: float                    # 冲突消解后最终信号
    has_conflict: bool                # 日频与分钟共识方向是否冲突


def _default_minute_loader() -> MinuteLoader:
    """延迟构造默认分钟数据加载器（FuturesDataAggregator.get_minute_ohlcv）。"""

    def loader(symbol: str, days: int, frequency: str, trace_id: str = "") -> pd.DataFrame:
        from fts.data_futures import FuturesDataAggregator

        return FuturesDataAggregator().get_minute_ohlcv(
            symbol, days, frequency, trace_id
        )

    return loader


def build_minute_signal(ohlcv_minute: pd.DataFrame) -> pd.Series:
    """分钟 alpha：日内动量 close/prev_close - 1（NaN/Inf 兜底为 0）。

    Args:
        ohlcv_minute: 分钟 K 线 DataFrame。

    Returns:
        按分钟索引的信号 Series；数据不可用返回空 Series。
    """
    if ohlcv_minute is None or ohlcv_minute.empty or "close" not in ohlcv_minute.columns:
        return pd.Series(dtype=float, name="minute_signal")
    df = ohlcv_minute.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"]).set_index("datetime")
        else:
            return pd.Series(dtype=float, name="minute_signal")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev = close.shift(1)
    sig = close / prev - 1.0
    sig = sig.replace([np.inf, -np.inf], np.nan)
    return sig.fillna(0.0).rename("minute_signal")


def aggregate_minute(
    minute_signal: pd.Series,
    method: str = "last",
    min_rows: int = 1,
) -> pd.Series:
    """分钟信号 → 日频聚合（last/mean/max/min）。

    Args:
        minute_signal: 分钟索引信号 Series。
        method: 聚合方法，未知方法回退 last。
        min_rows: 单日分钟样本下限，不足该日丢弃。

    Returns:
        按日期索引的聚合信号 Series；输入为空返回空 Series。
    """
    if minute_signal is None or minute_signal.empty:
        return pd.Series(dtype=float, name="minute_agg")
    frame = pd.DataFrame(
        {
            "sig": pd.to_numeric(minute_signal, errors="coerce").to_numpy(),
            "date": pd.to_datetime(minute_signal.index, errors="coerce").normalize(),
        }
    ).dropna(subset=["date"])
    if frame.empty:
        return pd.Series(dtype=float, name="minute_agg")
    counts = frame.groupby("date")["sig"].count()
    method = (method or "last").lower()
    if method == "mean":
        out = frame.groupby("date")["sig"].mean()
    elif method == "max":
        out = frame.groupby("date")["sig"].max()
    elif method == "min":
        out = frame.groupby("date")["sig"].min()
    else:
        out = frame.groupby("date")["sig"].last()
    min_rows = max(int(min_rows), 1)
    out = out[counts >= min_rows]
    return out.rename("minute_agg")


def blend_signals(
    daily: pd.Series,
    minute_agg: dict[str, pd.Series],
    config: MultiFrequencyConfig,
) -> tuple[pd.Series, pd.Series]:
    """加权叠加日频与多频分钟信号 → (叠加信号, 冲突标记)。

    - 分钟权重 = (1 - daily_weight) / 可用频率数，各频率均分；
    - 冲突定义：日频信号与分钟共识（可用频率等权均值）方向相反且均非零。
    """
    if daily is None or daily.empty:
        return (
            pd.Series(dtype=float, name="blended"),
            pd.Series(dtype=bool, name="has_conflict"),
        )
    daily = pd.to_numeric(daily, errors="coerce").fillna(0.0)
    freqs = [
        f
        for f in config.minute_freqs
        if f in minute_agg and minute_agg[f] is not None and not minute_agg[f].empty
    ]
    blended = daily.astype(float) * config.daily_weight
    consensus = pd.Series(0.0, index=daily.index, dtype=float)
    if freqs:
        w_min = (1.0 - config.daily_weight) / len(freqs)
        for f in freqs:
            m = pd.to_numeric(minute_agg[f], errors="coerce").reindex(
                daily.index
            ).fillna(0.0)
            blended = blended.add(m * w_min, fill_value=0.0)
            consensus = consensus.add(m, fill_value=0.0)
        consensus = consensus / len(freqs)
    has_conflict = (
        (np.sign(daily) * np.sign(consensus) < 0)
        & (daily.abs() > 0)
        & (consensus.abs() > 0)
    )
    return blended.rename("blended"), has_conflict.rename("has_conflict")


def resolve_conflict(
    daily: pd.Series,
    minute_agg: dict[str, pd.Series],
    config: MultiFrequencyConfig,
) -> pd.Series:
    """方向冲突消解后返回最终信号。

    - weighted：按权重合成（默认，等价于 blend 结果）；
    - penalty：冲突时分钟贡献 × conflict_penalty；
    - discard：冲突时丢弃分钟贡献，仅保留日频部分。
    """
    blended, has_conflict = blend_signals(daily, minute_agg, config)
    rule = (config.conflict_rule or "weighted").lower()
    if rule not in ("penalty", "discard"):
        return blended
    freqs = [
        f
        for f in config.minute_freqs
        if f in minute_agg and minute_agg[f] is not None and not minute_agg[f].empty
    ]
    if not freqs:
        return blended
    w_min = (1.0 - config.daily_weight) / len(freqs)
    min_contrib = pd.Series(0.0, index=daily.index, dtype=float)
    for f in freqs:
        m = pd.to_numeric(minute_agg[f], errors="coerce").reindex(
            daily.index
        ).fillna(0.0)
        min_contrib = min_contrib.add(m * w_min, fill_value=0.0)
    mask = has_conflict.fillna(False)
    daily = pd.to_numeric(daily, errors="coerce").fillna(0.0)
    if rule == "penalty":
        blended[mask] = (
            daily[mask] * config.daily_weight
            + min_contrib[mask] * config.conflict_penalty
        )
    else:  # discard
        blended[mask] = daily[mask] * config.daily_weight
    return blended.rename("resolved")


def compute_multi_frequency_signal(
    symbol: str,
    config: MultiFrequencyConfig,
    daily_signal: Optional[Union[float, pd.Series]] = None,
    minute_loader: Optional[MinuteLoader] = None,
) -> MultiFrequencyResult | None:
    """统一入口：取分钟数据 → 构建分钟信号 → 日频聚合 → 叠加 → 冲突消解。

    Args:
        symbol: 品种代码（如 "RB0"）。
        config: 多频配置。
        daily_signal: 日频因子信号（标量取最新日；Series 按最新共同日期取值）。
        minute_loader: 分钟数据加载器（测试注入用），缺省用 4 级降级链默认实现。

    Returns:
        最新共同交易日的 MultiFrequencyResult；全品种无分钟数据或数据不足返回 None。
    """
    loader = minute_loader or _default_minute_loader()
    minute_agg: dict[str, pd.Series] = {}
    for freq in config.minute_freqs:
        try:
            ohlcv = loader(symbol, config.lookback_days, freq, config.trace_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("分钟数据获取失败 [%s/%s]: %s", symbol, freq, e)
            continue
        if ohlcv is None or ohlcv.empty:
            continue
        daily = aggregate_minute(
            build_minute_signal(ohlcv), config.agg_method, config.min_minute_rows
        )
        if not daily.empty:
            minute_agg[freq] = daily
    if not minute_agg:
        return None

    common = None
    for s in minute_agg.values():
        common = s.index if common is None else common.intersection(s.index)
    if common is None or len(common) == 0:
        return None
    date = common[-1]

    if isinstance(daily_signal, pd.Series):
        daily_val = (
            float(daily_signal.reindex([date]).iloc[0])
            if date in daily_signal.index
            else 0.0
        )
    else:
        daily_val = float(daily_signal) if daily_signal is not None else 0.0

    last_minute = {
        f: pd.Series({date: float(s.loc[date])})
        for f, s in minute_agg.items()
        if date in s.index
    }
    daily_series = pd.Series({date: daily_val}, name="daily_signal")
    final = resolve_conflict(daily_series, last_minute, config)
    _, has_conflict = blend_signals(daily_series, last_minute, config)
    return MultiFrequencyResult(
        date=date,
        daily_signal=daily_val,
        minute_agg={f: float(v.iloc[0]) for f, v in last_minute.items()},
        blended=float(final.iloc[0]),
        has_conflict=bool(has_conflict.iloc[0]),
    )


def backtest_minute_signal(
    symbol: str,
    config: MultiFrequencyConfig,
    cost_bps: float = 2.0,
    minute_loader: Optional[MinuteLoader] = None,
) -> dict[str, Any]:
    """分钟信号日频持有回测（T+1 隔日调仓，滑点+手续费双向）。

    - 分钟共识 = 可用频率日频聚合信号的等权均值 → 次日持仓方向 sign(共识)；
    - 日收益由行数最多的频率日收盘价计算；
    - 数据不足（< 2 个交易日）返回空 dict（降级，不抛错）。
    """
    loader = minute_loader or _default_minute_loader()
    daily_closes: pd.Series | None = None
    consensus_list: list[pd.Series] = []
    for freq in config.minute_freqs:
        try:
            ohlcv = loader(symbol, config.lookback_days, freq, config.trace_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("分钟数据获取失败 [%s/%s]: %s", symbol, freq, e)
            continue
        if ohlcv is None or ohlcv.empty:
            continue
        df = ohlcv.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            if "datetime" in df.columns:
                df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
                df = df.dropna(subset=["datetime"]).set_index("datetime")
            else:
                continue
        close = pd.to_numeric(df["close"], errors="coerce")
        daily_close = close.groupby(
            pd.to_datetime(close.index, errors="coerce").normalize()
        ).last().dropna()
        if daily_closes is None or len(daily_close) > len(daily_closes):
            daily_closes = daily_close
        daily_sig = aggregate_minute(
            build_minute_signal(df), config.agg_method, config.min_minute_rows
        )
        if not daily_sig.empty:
            consensus_list.append(daily_sig)
    if (
        daily_closes is None
        or not consensus_list
        or len(daily_closes) < 2
    ):
        return {}

    consensus = (
        pd.concat(consensus_list, axis=1).mean(axis=1)
        .reindex(daily_closes.index).ffill().fillna(0.0)
    )
    pos = np.sign(consensus.shift(1)).fillna(0.0)  # T+1 持仓
    ret = daily_closes.pct_change().fillna(0.0)
    cost = float(cost_bps) / 1e4
    turnover = pos.diff().abs().fillna(pos.abs())
    strat = pos * ret - turnover * cost
    cum = (1.0 + strat).cumprod()
    n = int(len(cum))
    total = float(cum.iloc[-1])
    years = n / 252.0
    annualized = total ** (1.0 / years) - 1.0 if years > 0 and total > 0 else 0.0
    sharpe = (
        float(strat.mean() / strat.std() * np.sqrt(252.0))
        if strat.std() > 0
        else 0.0
    )
    return {
        "symbol": symbol,
        "n_days": n,
        "cum_return": total - 1.0,
        "annualized_return": annualized,
        "sharpe": sharpe,
        "max_drawdown": float((cum / cum.cummax() - 1.0).min()),
        "win_rate": float((strat > 0).mean()),
    }
