"""tests/factor_engine/test_microstructure_factors.py — Level2 订单流因子（GAP-I503 首期）。

覆盖：
- classify_tick_direction：价差方向判定（升/降/持平延续/首条 0）
- order_flow_imbalance：主动买卖量差归一化（纯买方=+1、纯卖方=-1、混合）
- order_book_imbalance：盘口深度不平衡
- large_trade_ratio：大单占比（绝对/相对阈值）
- compute_microstructure_factors：契约列输出 + 降级（缺列/不足 min_rows）
- MicrostructureConfig 参数校验
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.microstructure_factors import (
    MicrostructureConfig,
    classify_tick_direction,
    compute_microstructure_factors,
    large_trade_ratio,
    order_book_imbalance,
    order_flow_imbalance,
)


def _make_tick(
    n: int = 40,
    up: bool = True,
    big_idx: tuple[int, ...] = (),
    big_vol: float = 50.0,
    bid_vol1: float = 100.0,
    ask_vol1: float = 90.0,
) -> pd.DataFrame:
    """合成 tick 快照：均匀 1 手/tick，指定大单 tick 放量（默认无大单）。

    5 档盘口与第 1 档同向成比例（便于整档深度偏置测试）。
    """
    times = pd.date_range("2026-08-07 14:30:00", periods=n, freq="500ms")
    step = 1.0 if up else -1.0
    last_price = 3000.0 + step * np.arange(n, dtype=float)
    volume = np.arange(n, dtype=float) + 1.0  # 累计量（1 手/tick）
    for i in big_idx:
        if i < n:
            volume[i] = volume[i - 1] + big_vol
    return pd.DataFrame(
        {
            "datetime": times,
            "last_price": last_price,
            "volume": volume,
            "amount": np.arange(100, 100 + n, dtype=float),
            "bid_volume1": bid_vol1,
            "ask_volume1": ask_vol1,
            "bid_volume2": 2.0 * bid_vol1,
            "ask_volume2": 2.0 * ask_vol1,
            "bid_volume3": 1.5 * bid_vol1,
            "ask_volume3": 1.5 * ask_vol1,
            "bid_volume4": 3.0 * bid_vol1,
            "ask_volume4": 3.0 * ask_vol1,
            "bid_volume5": 4.0 * bid_vol1,
            "ask_volume5": 4.0 * ask_vol1,
        }
    )


class TestClassifyDirection:
    def test_uptrend_all_buy(self) -> None:
        df = _make_tick(n=10, up=True)
        d = classify_tick_direction(df)
        assert (d.iloc[1:] == 1).all()
        assert d.iloc[0] == 0  # 首条无法判定

    def test_downtrend_all_sell(self) -> None:
        df = _make_tick(n=10, up=False)
        d = classify_tick_direction(df)
        assert (d.iloc[1:] == -1).all()

    def test_flat_prices_follow_previous(self) -> None:
        """持平 tick 沿用前一条方向。"""
        times = pd.date_range("2026-08-07 14:30:00", periods=5, freq="500ms")
        df = pd.DataFrame(
            {
                "datetime": times,
                "last_price": [3000.0, 3001.0, 3001.0, 3000.0, 3000.0],
                "volume": np.arange(5, dtype=float) + 1.0,
            }
        )
        d = classify_tick_direction(df)
        assert d.tolist() == [0, 1, 1, -1, -1]

    def test_missing_price_col_returns_empty(self) -> None:
        df = pd.DataFrame({"datetime": pd.date_range("2026-08-07", periods=3)})
        assert classify_tick_direction(df).empty


class TestOrderFlowImbalance:
    def test_all_buy_equals_positive_one(self) -> None:
        df = _make_tick(n=30, up=True)
        ofi = order_flow_imbalance(df, window=10)
        assert not ofi.empty
        assert (ofi.iloc[10:] >= 0.999).all()  # 纯买方 → +1（滚动窗口尾部）

    def test_all_sell_equals_negative_one(self) -> None:
        df = _make_tick(n=30, up=False)
        ofi = order_flow_imbalance(df, window=10)
        assert not ofi.empty
        assert (ofi.iloc[10:] <= -0.999).all()

    def test_mixed_direction_between_bounds(self) -> None:
        """混合方向：OFI ∈ (-1, 1)。"""
        times = pd.date_range("2026-08-07 14:30:00", periods=30, freq="500ms")
        prices = [3000.0] + [3000.0 + (1 if i % 2 == 0 else -1) * 0.5 for i in range(29)]
        df = pd.DataFrame({"datetime": times, "last_price": prices, "volume": np.arange(30, dtype=float) + 1.0})
        ofi = order_flow_imbalance(df, window=10)
        assert not ofi.empty
        assert (ofi >= -1.0).all() and (ofi <= 1.0).all()
        assert (ofi.abs() < 1.0).any()  # 存在混合

    def test_empty_input(self) -> None:
        assert order_flow_imbalance(pd.DataFrame()).empty


class TestOrderBookImbalance:
    def test_positive_when_bid_heavier(self) -> None:
        df = _make_tick(n=5, bid_vol1=150.0, ask_vol1=50.0)
        obi = order_book_imbalance(df)
        assert not obi.empty
        assert (obi > 0).all()

    def test_negative_when_ask_heavier(self) -> None:
        df = _make_tick(n=5, bid_vol1=50.0, ask_vol1=150.0)
        obi = order_book_imbalance(df)
        assert (obi < 0).all()

    def test_missing_depth_returns_zero(self) -> None:
        df = _make_tick(n=5)[["datetime", "last_price", "volume"]]
        obi = order_book_imbalance(df)
        assert (obi == 0.0).all()


class TestLargeTradeRatio:
    def test_big_trades_raise_ratio(self) -> None:
        df = _make_tick(n=40, up=True, big_idx=(10, 20), big_vol=50.0)
        cfg = MicrostructureConfig(window=20, large_threshold_abs=10.0, large_threshold_mult=1.0)
        ltr = large_trade_ratio(df, cfg)
        assert not ltr.empty
        # 大单（50 手）出现后窗口内占比显著 > 0
        assert ltr.iloc[30:].mean() > 0.3

    def test_no_big_trades_zero_ratio(self) -> None:
        df = _make_tick(n=40, up=True)  # 全部 1 手
        cfg = MicrostructureConfig(window=20, large_threshold_abs=10.0, large_threshold_mult=1.0)
        ltr = large_trade_ratio(df, cfg)
        assert (ltr == 0.0).all()

    def test_relative_threshold_only(self) -> None:
        """无绝对阈值时用均量倍数：均匀量不触发大单。"""
        df = _make_tick(n=40, up=True)
        cfg = MicrostructureConfig(window=20, large_threshold_abs=None, large_threshold_mult=3.0)
        ltr = large_trade_ratio(df, cfg)
        assert (ltr == 0.0).all()


class TestComputeMicrostructureFactors:
    def test_output_contract_columns(self) -> None:
        df = _make_tick(n=40, up=True, big_idx=(10, 20), big_vol=50.0)
        out = compute_microstructure_factors(df)
        assert list(out.columns) == [
            "datetime",
            "direction",
            "trade_volume",
            "ofi",
            "obi",
            "large_trade_ratio",
        ]
        assert len(out) == 40
        assert (out["direction"].isin([-1, 0, 1])).all()

    def test_trade_volume_derived_from_cumulative(self) -> None:
        df = _make_tick(n=40, up=True, big_idx=(10,), big_vol=50.0)
        out = compute_microstructure_factors(df)
        # 第 10 个 tick 单笔量为 50，其余 1
        assert out["trade_volume"].iloc[10] == 50.0
        assert out["trade_volume"].iloc[5] == 1.0
        assert out["trade_volume"].iloc[0] == 0.0

    def test_insufficient_rows_degrade_empty(self) -> None:
        df = _make_tick(n=5)
        cfg = MicrostructureConfig(min_rows=20)
        out = compute_microstructure_factors(df, cfg)
        assert out.empty
        assert list(out.columns) == [
            "datetime",
            "direction",
            "trade_volume",
            "ofi",
            "obi",
            "large_trade_ratio",
        ]

    def test_missing_columns_degrade_empty(self) -> None:
        df = pd.DataFrame({"datetime": pd.date_range("2026-08-07", periods=30), "x": 1.0})
        assert compute_microstructure_factors(df).empty

    def test_unsorted_input_sorted_by_time(self) -> None:
        df = _make_tick(n=40, up=True)
        shuffled = df.sample(frac=1.0, random_state=42).reset_index(drop=True)
        out = compute_microstructure_factors(shuffled)
        assert out["datetime"].is_monotonic_increasing


class TestMicrostructureConfigValidation:
    def test_window_positive(self) -> None:
        with pytest.raises(ValueError):
            MicrostructureConfig(window=0)

    def test_mult_positive(self) -> None:
        with pytest.raises(ValueError):
            MicrostructureConfig(large_threshold_mult=0.0)

    def test_min_rows_positive(self) -> None:
        with pytest.raises(ValueError):
            MicrostructureConfig(min_rows=0)
