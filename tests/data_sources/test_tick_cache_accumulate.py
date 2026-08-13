"""tests/data_sources/test_tick_cache_accumulate.py — tick_cache 增量累积（GAP-I503 首期）。

覆盖：
- 去重写入：重复写入同 (symbol, datetime) 不产生重复行
- 时间窗口查询：start_time/end_time 过滤
- 保留清理：超过 tick_cache_retention_days 的过期 tick 被清理
- 向后兼容：无时间窗口参数行为不变
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from fts.data_sources.aggregator import FuturesDataAggregator
from fts.data_sources.migrate import migrate_schema


def _tick_df(n: int = 10, start: str = "2026-08-07 14:30:00", freq: str = "500ms") -> pd.DataFrame:
    times = pd.date_range(start, periods=n, freq=freq)
    return pd.DataFrame(
        {
            "symbol": ["RB0"] * n,
            "datetime": times,
            "last_price": 3010.0,
            "average": 3012.0,
            "highest": 3020.0,
            "lowest": 3000.0,
            "volume": np.arange(n, dtype=float),
            "amount": np.arange(100, 100 + n, dtype=float),
            "open_interest": 120000.0,
            "bid_price1": 3009.0,
            "bid_volume1": 100.0,
            "ask_price1": 3011.0,
            "ask_volume1": 90.0,
            "bid_price2": 3008.0,
            "bid_volume2": 200.0,
            "ask_price2": 3012.0,
            "ask_volume2": 180.0,
            "bid_price3": 3007.0,
            "bid_volume3": 150.0,
            "ask_price3": 3013.0,
            "ask_volume3": 120.0,
            "bid_price4": 3006.0,
            "bid_volume4": 300.0,
            "ask_price4": 3014.0,
            "ask_volume4": 250.0,
            "bid_price5": 3005.0,
            "bid_volume5": 400.0,
            "ask_price5": 3015.0,
            "ask_volume5": 350.0,
            "source": "TQSDK_TICK",
            "fetched_at": pd.Timestamp.now(),
            "trace_id": "tick_accumulate_test",
        }
    )


def _make_agg(tmp_path: Path, retention_days: int = 7) -> FuturesDataAggregator:
    db_path = tmp_path / "tick_accumulate.duckdb"
    migrate_schema(db_path)
    agg = FuturesDataAggregator(
        minute_sources=[],
        tick_sources=[],
        db_path=db_path,
        tick_cache_retention_days=retention_days,
    )
    return agg


def _count_rows(agg: FuturesDataAggregator) -> int:
    con = agg._open_read_conn()
    try:
        return con.execute("SELECT COUNT(*) FROM tick_cache").fetchone()[0]
    finally:
        con.close()


class TestTickCacheDedup:
    """去重写入：重复写入同一时间段不产生重复行。"""

    def test_write_twice_no_duplicates(self, tmp_path: Path) -> None:
        agg = _make_agg(tmp_path)
        try:
            df = _tick_df(n=10)
            agg._write_tick_cache(df)
            agg._write_tick_cache(df)  # 重复写入
            assert _count_rows(agg) == 10
        finally:
            agg.close()  # E.4 S1: no persistent conn

    def test_partial_overlap_dedup(self, tmp_path: Path) -> None:
        """部分重叠：重叠 tick 去重，新增 tick 追加（跨会话累积）。"""
        agg = _make_agg(tmp_path)
        try:
            batch1 = _tick_df(n=5, start="2026-08-07 14:30:00")
            batch2 = _tick_df(n=8, start="2026-08-07 14:30:00")  # 与 batch1 重叠 5 条 + 新增 3 条
            agg._write_tick_cache(batch1)
            agg._write_tick_cache(batch2)
            assert _count_rows(agg) == 8
        finally:
            agg.close()  # E.4 S1: no persistent conn

    def test_get_ticks_no_duplicates_after_rewrite(self, tmp_path: Path) -> None:
        """get_ticks 返回无重复行。"""
        agg = _make_agg(tmp_path)
        try:
            df = _tick_df(n=6)
            agg._write_tick_cache(df)
            agg._write_tick_cache(df)
            out = agg.get_ticks("RB0", count=6, trace_id="t")
            assert len(out) == 6
            assert out["datetime"].duplicated().sum() == 0
        finally:
            agg.close()  # E.4 S1: no persistent conn


class TestTickCacheTimeWindow:
    """时间窗口查询。"""

    def test_start_time_filter(self, tmp_path: Path) -> None:
        agg = _make_agg(tmp_path)
        try:
            df = _tick_df(n=10, start="2026-08-07 14:30:00")  # 覆盖 14:30:00 ~ 14:30:04.5
            agg._write_tick_cache(df)
            out = agg.get_ticks("RB0", count=100, trace_id="t", start_time="2026-08-07 14:30:01")
            assert not out.empty
            assert (out["datetime"] >= pd.Timestamp("2026-08-07 14:30:01")).all()
            assert len(out) < 10
        finally:
            agg.close()  # E.4 S1: no persistent conn

    def test_end_time_filter(self, tmp_path: Path) -> None:
        agg = _make_agg(tmp_path)
        try:
            df = _tick_df(n=10, start="2026-08-07 14:30:00")
            agg._write_tick_cache(df)
            out = agg.get_ticks("RB0", count=100, trace_id="t", end_time="2026-08-07 14:30:03")
            assert not out.empty
            assert (out["datetime"] <= pd.Timestamp("2026-08-07 14:30:03")).all()
            assert len(out) < 10
        finally:
            agg.close()  # E.4 S1: no persistent conn

    def test_window_both_bounds(self, tmp_path: Path) -> None:
        agg = _make_agg(tmp_path)
        try:
            df = _tick_df(n=10, start="2026-08-07 14:30:00")
            agg._write_tick_cache(df)
            out = agg.get_ticks(
                "RB0",
                count=100,
                trace_id="t",
                start_time="2026-08-07 14:30:01",
                end_time="2026-08-07 14:30:03",
            )
            assert not out.empty
            assert (out["datetime"] >= pd.Timestamp("2026-08-07 14:30:01")).all()
            assert (out["datetime"] <= pd.Timestamp("2026-08-07 14:30:03")).all()
        finally:
            agg.close()  # E.4 S1: no persistent conn

    def test_no_window_backward_compat(self, tmp_path: Path) -> None:
        """不带时间窗口参数行为不变（返回全部）。"""
        agg = _make_agg(tmp_path)
        try:
            df = _tick_df(n=10)
            agg._write_tick_cache(df)
            out = agg.get_ticks("RB0", count=10, trace_id="t")
            assert len(out) == 10
        finally:
            agg.close()  # E.4 S1: no persistent conn


class TestTickCacheRetention:
    """保留清理：过期 tick 被清除。"""

    def test_prune_expired(self, tmp_path: Path) -> None:
        agg = _make_agg(tmp_path, retention_days=7)
        try:
            old = _tick_df(n=3, start="2026-07-01 14:30:00")
            fresh = _tick_df(n=3, start="2026-08-07 14:30:00")
            agg._write_tick_cache(old)
            agg._write_tick_cache(fresh)  # 触发清理
            assert _count_rows(agg) == 3
            out = agg.get_ticks("RB0", count=100, trace_id="t")
            assert (out["datetime"] >= pd.Timestamp("2026-08-07")).all()
        finally:
            agg.close()  # E.4 S1: no persistent conn

    def test_fresh_data_kept(self, tmp_path: Path) -> None:
        agg = _make_agg(tmp_path, retention_days=7)
        try:
            df = _tick_df(n=5, start="2026-08-07 14:30:00")
            agg._write_tick_cache(df)
            assert _count_rows(agg) == 5
        finally:
            agg.close()  # E.4 S1: no persistent conn
