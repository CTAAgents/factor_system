"""tests/factor_engine/test_multi_frequency.py — 多频信号叠加与冲突消解测试（GAP-068）。

覆盖：分钟信号计算 / 四种聚合方法 / 叠加权重 / 三种冲突消解规则 /
      分钟回测（含成本、方向）/ 数据不足降级。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.multi_frequency import (
    MultiFrequencyConfig,
    MultiFrequencyResult,
    aggregate_minute,
    backtest_minute_signal,
    blend_signals,
    build_minute_signal,
    compute_multi_frequency_signal,
    resolve_conflict,
)


def _make_minute(
    daily_closes: list[float],
    bars: int = 10,
    days: int | None = None,
    freq: str = "5m",
) -> pd.DataFrame:
    """构造分钟 K 线：每日 bars 根，close 日内递增（正向动量），收盘按日序列。"""
    if days is None:
        days = len(daily_closes)
    dates = pd.date_range("2026-06-01", periods=days, freq="B")
    rows: list[dict] = []
    for i, d in enumerate(dates):
        c = daily_closes[i % len(daily_closes)]
        for k in range(bars):
            rows.append(
                {
                    "datetime": d + pd.Timedelta(minutes=5 * k),
                    "close": c * (1 + k * 0.001),
                }
            )
    df = pd.DataFrame(rows).set_index("datetime")
    df["open"] = df["close"] * 0.999
    df["high"] = df["close"] * 1.002
    df["low"] = df["close"] * 0.998
    df["volume"] = 1000
    df = df[["open", "high", "low", "close", "volume"]]
    return df


class _FakeMinuteLoader:
    """按频率返回预置分钟数据的假加载器。"""

    def __init__(self, data: dict[str, pd.DataFrame]) -> None:
        self._data = data

    def __call__(self, symbol: str, days: int, frequency: str, trace_id: str = "") -> pd.DataFrame:
        return self._data.get(frequency, pd.DataFrame()).copy()


def _empty_loader() -> _FakeMinuteLoader:
    return _FakeMinuteLoader({})


# ── 1. 分钟信号计算 ────────────────────────────────────────


def test_build_minute_signal_momentum():
    df = _make_minute([100.0], days=1, bars=5)
    sig = build_minute_signal(df)
    assert not sig.empty
    # 首根 bar 无前值 → 0
    assert sig.iloc[0] == 0.0
    # 日内递增 → 其余动量恒正
    assert (sig.iloc[1:] > 0).all()


def test_build_minute_signal_datetime_column():
    df = _make_minute([100.0], days=1, bars=3).reset_index()  # datetime 变普通列
    sig = build_minute_signal(df)
    assert not sig.empty
    assert (sig >= 0).all()


def test_build_minute_signal_empty():
    assert build_minute_signal(pd.DataFrame()).empty


def test_build_minute_signal_missing_close():
    df = pd.DataFrame({"open": [1.0], "high": [2.0]})
    assert build_minute_signal(df).empty


# ── 2. 四种聚合方法 ────────────────────────────────────────


def _three_bar_signal() -> pd.Series:
    idx = pd.to_datetime(
        [
            "2026-06-01 09:00", "2026-06-01 09:05", "2026-06-01 09:10",
            "2026-06-02 09:00", "2026-06-02 09:05", "2026-06-02 09:10",
        ]
    )
    return pd.Series([0.1, 0.2, 0.3, 0.5, -0.1, 0.2], index=idx)


def test_aggregate_minute_methods():
    sig = _three_bar_signal()
    assert aggregate_minute(sig, "last").tolist() == pytest.approx([0.3, 0.2])
    assert aggregate_minute(sig, "mean").tolist() == pytest.approx([0.2, 0.2])
    assert aggregate_minute(sig, "max").tolist() == pytest.approx([0.3, 0.5])
    assert aggregate_minute(sig, "min").tolist() == pytest.approx([0.1, -0.1])


def test_aggregate_minute_unknown_method_falls_back():
    sig = _three_bar_signal()
    assert aggregate_minute(sig, "nonsense").tolist() == [0.3, 0.2]


def test_aggregate_minute_min_rows():
    sig = _three_bar_signal()
    assert aggregate_minute(sig, "last", min_rows=4).empty  # 每日仅 3 根
    assert not aggregate_minute(sig, "last", min_rows=3).empty


def test_aggregate_minute_empty():
    assert aggregate_minute(pd.Series(dtype=float)).empty


# ── 3. 叠加权重 ────────────────────────────────────────────


def _daily() -> pd.Series:
    return pd.Series([1.0, -1.0], index=pd.to_datetime(["2026-06-01", "2026-06-02"]))


def _minute_agg() -> dict[str, pd.Series]:
    """day1 与日频 +1 同向、day2 与日频 -1 反向（冲突在第 2 日）。"""
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    return {"5m": pd.Series([0.5, 0.5], index=idx)}


def _minute_agg_opp() -> dict[str, pd.Series]:
    """day1 与日频 +1 反向、day2 与日频 -1 同向（冲突在第 1 日）。"""
    idx = pd.to_datetime(["2026-06-01", "2026-06-02"])
    return {"5m": pd.Series([-0.5, -0.5], index=idx)}


def test_blend_single_freq_weights():
    daily = _daily()
    blended, _ = blend_signals(
        daily, _minute_agg(), MultiFrequencyConfig(daily_weight=0.6)
    )
    # day1 = 0.6*1 + 0.4*0.5 = 0.8; day2 = 0.6*(-1) + 0.4*0.5 = -0.4
    assert blended.iloc[0] == pytest.approx(0.8)
    assert blended.iloc[1] == pytest.approx(-0.4)


def test_blend_two_freqs_even_split():
    daily = _daily()
    ma = {
        "5m": pd.Series([0.5, 0.5], index=daily.index),
        "15m": pd.Series([0.5, 0.5], index=daily.index),
    }
    blended, _ = blend_signals(daily, ma, MultiFrequencyConfig(daily_weight=0.6))
    # 分钟权重 = 0.4/2 = 0.2 → 0.6*1 + 0.2*0.5 + 0.2*0.5 = 0.8
    assert blended.iloc[0] == pytest.approx(0.8)


def test_blend_conflict_detection():
    daily = _daily()  # [+1, -1]
    _, conflict = blend_signals(daily, _minute_agg(), MultiFrequencyConfig())
    assert not conflict.iloc[0]  # day1 +1 与 +0.5 同向
    assert conflict.iloc[1]      # day2 -1 与 +0.5 反向
    _, conflict_opp = blend_signals(daily, _minute_agg_opp(), MultiFrequencyConfig())
    assert conflict_opp.iloc[0]  # day1 +1 与 -0.5 反向
    assert not conflict_opp.iloc[1]


def test_blend_empty_inputs():
    blended, conflict = blend_signals(pd.Series(dtype=float), {}, MultiFrequencyConfig())
    assert blended.empty and conflict.empty


# ── 4. 三种冲突消解规则 ────────────────────────────────────


def test_resolve_conflict_weighted():
    daily = _daily()
    resolved = resolve_conflict(
        daily, _minute_agg(), MultiFrequencyConfig(conflict_rule="weighted")
    )
    # 与 blend 相同：day2 = 0.6*(-1) + 0.4*0.5 = -0.4
    assert resolved.iloc[0] == pytest.approx(0.8)
    assert resolved.iloc[1] == pytest.approx(-0.4)


def test_resolve_conflict_penalty():
    daily = _daily()
    resolved = resolve_conflict(
        daily,
        _minute_agg(),
        MultiFrequencyConfig(conflict_rule="penalty", conflict_penalty=0.5),
    )
    # day2 冲突 → 分钟贡献 ×0.5：0.6*(-1) + 0.4*0.5*0.5 = -0.5；day1 同向不惩罚
    assert resolved.iloc[0] == pytest.approx(0.8)
    assert resolved.iloc[1] == pytest.approx(-0.5)


def test_resolve_conflict_discard():
    daily = _daily()
    resolved = resolve_conflict(
        daily, _minute_agg(), MultiFrequencyConfig(conflict_rule="discard")
    )
    # day2 冲突 → 丢弃分钟贡献：仅日频 0.6*(-1) = -0.6；day1 同向保留叠加
    assert resolved.iloc[0] == pytest.approx(0.8)
    assert resolved.iloc[1] == pytest.approx(-0.6)


# ── 5. 统一入口 ────────────────────────────────────────────


def test_compute_multi_frequency_signal_ok():
    data = {f: _make_minute([100.0, 101.0, 102.0], days=3, bars=10) for f in ("5m", "15m", "60m")}
    cfg = MultiFrequencyConfig(min_minute_rows=2, trace_id="t1")
    res = compute_multi_frequency_signal("RB0", cfg, minute_loader=_FakeMinuteLoader(data))
    assert isinstance(res, MultiFrequencyResult)
    assert res.date == pd.Timestamp("2026-06-03")
    assert set(res.minute_agg) == {"5m", "15m", "60m"}
    assert isinstance(res.blended, float)
    assert isinstance(res.has_conflict, bool)
    assert res.daily_signal == 0.0  # 未提供日频信号


def test_compute_multi_frequency_signal_daily_series():
    data = {"5m": _make_minute([100.0, 101.0], days=2, bars=10)}
    daily_series = pd.Series([0.7], index=pd.to_datetime(["2026-06-02"]))
    cfg = MultiFrequencyConfig(min_minute_rows=2, daily_weight=0.6)
    res = compute_multi_frequency_signal(
        "RB0", cfg, daily_signal=daily_series, minute_loader=_FakeMinuteLoader(data)
    )
    assert res is not None
    assert res.daily_signal == pytest.approx(0.7)
    assert res.has_conflict is False


def test_compute_multi_frequency_signal_none_on_empty():
    res = compute_multi_frequency_signal(
        "RB0", MultiFrequencyConfig(), minute_loader=_empty_loader()
    )
    assert res is None


def test_compute_multi_frequency_signal_none_on_insufficient_rows():
    # 每日样本 1 根 < min_minute_rows=2 → 全部丢弃 → None
    data = {"5m": _make_minute([100.0, 101.0], days=2, bars=1)}
    res = compute_multi_frequency_signal(
        "RB0", MultiFrequencyConfig(min_minute_rows=2), minute_loader=_FakeMinuteLoader(data)
    )
    assert res is None


# ── 6. 分钟回测 ────────────────────────────────────────────


def test_backtest_minute_signal_metrics():
    data = {"5m": _make_minute([100.0, 110.0, 121.0, 133.1, 146.4], days=5, bars=10)}
    cfg = MultiFrequencyConfig(min_minute_rows=2, lookback_days=200)
    metrics = backtest_minute_signal("RB0", cfg, cost_bps=2.0, minute_loader=_FakeMinuteLoader(data))
    assert set(metrics) == {
        "symbol", "n_days", "cum_return", "annualized_return", "sharpe", "max_drawdown", "win_rate",
    }
    assert metrics["n_days"] == 5
    # 持续上涨 → 正向信号 → 累计收益为正
    assert metrics["cum_return"] > 0
    assert np.isfinite(metrics["sharpe"])


def test_backtest_minute_signal_direction_down():
    data = {"5m": _make_minute([146.4, 133.1, 121.0, 110.0, 100.0], days=5, bars=10)}
    cfg = MultiFrequencyConfig(min_minute_rows=2, lookback_days=200)
    metrics = backtest_minute_signal("RB0", cfg, minute_loader=_FakeMinuteLoader(data))
    assert metrics["cum_return"] < 0  # 下跌 → 信号转空 → 反向收益


def test_backtest_minute_signal_insufficient_data():
    # 无数据
    assert backtest_minute_signal("RB0", MultiFrequencyConfig(), minute_loader=_empty_loader()) == {}
    # 单日数据不足 2 个交易日
    data = {"5m": _make_minute([100.0], days=1, bars=10)}
    assert backtest_minute_signal(
        "RB0", MultiFrequencyConfig(min_minute_rows=2), minute_loader=_FakeMinuteLoader(data)
    ) == {}
