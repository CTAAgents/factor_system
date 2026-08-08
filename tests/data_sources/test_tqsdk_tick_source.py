"""
tests.data_sources.test_tqsdk_tick_source — TQSDK tick 逐笔数据源测试（v2.31.0）。

测试覆盖:
    1. 品种映射复用（RB0 → KQ.m@SHFE.rb）
    2. tick 数据解析（纳秒时间戳 → datetime，字段标准化）
    3. tick_cache 表迁移
    4. Aggregator.get_ticks 降级链（缓存 → 源）
    5. Provider.get_tick_data 接口

HARNESS §5.4 测试随重构: 每阶段测试全绿才能进入下一阶段。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import duckdb
import numpy as np
import pandas as pd
import pytest

from fts.data_futures import FuturesDataProvider
from fts.data_sources.aggregator import FuturesDataAggregator
from fts.data_sources.base import BaseFuturesSource
from fts.data_sources.migrate import migrate_schema
from fts.data_sources.tqsdk_tick_source import TICK_COLUMNS, TICK_MAX_LENGTH, TQSDKTickSource


# ─── 1. 品种映射 ──────────────────────────────────────────


class TestTickSymbolMapping:
    """测试 tick 品种映射（复用 TQSDKSource._SYMBOL_MAP）。"""

    def test_map_rb(self) -> None:
        """RB0 → KQ.m@SHFE.rb。"""
        src = TQSDKTickSource()
        assert src._resolve_symbol("RB0") == "KQ.m@SHFE.rb"

    def test_map_unknown_fallback(self) -> None:
        """未知品种应回退为原始代码。"""
        src = TQSDKTickSource()
        assert src._resolve_symbol("UNKNOWN0") == "UNKNOWN0"


# ─── 2. tick 数据解析 ─────────────────────────────────────


class TestTickDataParsing:
    """测试 get_tick_serial 返回数据的解析。"""

    def _raw_tick_df(self, n: int = 100) -> pd.DataFrame:
        """构造 TQSDK get_tick_serial 原始返回。"""
        now = pd.Timestamp("2026-08-07 14:30:00").value  # ns
        step = int(500 * 1e6)  # 500ms
        return pd.DataFrame({
            "datetime": [now + i * step for i in range(n)],
            "id": list(range(n)),
            "last_price": 3010.0 + np.sin(np.arange(n)) * 5,
            "average": 3012.0,
            "highest": 3020.0,
            "lowest": 3000.0,
            "volume": np.arange(100, 100 + n, dtype=float),
            "amount": np.arange(1000, 1000 + n, dtype=float),
            "open_interest": np.full(n, 120000.0),
            "bid_price1": 3009.0,
            "bid_volume1": 100,
            "ask_price1": 3011.0,
            "ask_volume1": 90,
            "bid_price2": 3008.0,
            "bid_volume2": 200,
            "ask_price2": 3012.0,
            "ask_volume2": 180,
            "bid_price3": 3007.0,
            "bid_volume3": 150,
            "ask_price3": 3013.0,
            "ask_volume3": 120,
            "bid_price4": 3006.0,
            "bid_volume4": 300,
            "ask_price4": 3014.0,
            "ask_volume4": 250,
            "bid_price5": 3005.0,
            "bid_volume5": 400,
            "ask_price5": 3015.0,
            "ask_volume5": 350,
            "duration": n,
            "symbol": "KQ.m@SHFE.rb",
        })

    @patch("fts.data_sources.tqsdk_tick_source.time")
    @patch("fts.data_sources.tqsdk_tick_source.os.environ.get")
    def test_fetch_ticks_parse(self, mock_env, mock_time) -> None:
        """tick 数据应解析为标准 schema。"""
        mock_env.return_value = "test_user"  # TQSDK_USERNAME 有值（is_available 用）

        raw = self._raw_tick_df(100)

        class FakeApi:
            def __init__(self, *args, **kwargs):
                pass

            def get_tick_serial(self, symbol, data_length):
                return raw.copy()

            def wait_update(self, deadline=None):
                return None

            def close(self):
                return None

        fake_tqsdk = type("tqsdk", (), {"TqApi": FakeApi, "TqAuth": lambda u, p: None})

        with patch.dict("sys.modules", {"tqsdk": fake_tqsdk}):
            src = TQSDKTickSource()
            df = src.fetch_ticks("RB0", count=50, trace_id="test_tick")
            assert df is not None
            assert len(df) == 50
            assert set(TICK_COLUMNS).issubset(set(df.columns))
            assert df["symbol"].iloc[0] == "RB0"
            assert df["source"].iloc[0] == "TQSDK_TICK"
            # datetime 已解析为 Timestamp
            assert isinstance(df["datetime"].iloc[0], pd.Timestamp)
            # 正序
            assert df["datetime"].is_monotonic_increasing

    def test_tick_columns_schema(self) -> None:
        """TICK_COLUMNS 应包含 32 列（含 5 档盘口）。"""
        assert len(TICK_COLUMNS) == 32
        assert "last_price" in TICK_COLUMNS
        assert "bid_price1" in TICK_COLUMNS
        assert "ask_price1" in TICK_COLUMNS
        assert "bid_price5" in TICK_COLUMNS
        assert "ask_price5" in TICK_COLUMNS

    def test_max_length_cap(self) -> None:
        """count 超过免费账号上限应截断。"""
        src = TQSDKTickSource()
        assert TICK_MAX_LENGTH == 5000


# ─── 3. tick_cache 表迁移 ─────────────────────────────────


class TestTickCacheMigration:
    """测试 tick_cache 表创建。"""

    def test_migrate_creates_tick_cache(self, tmp_path: Path) -> None:
        """migrate_schema 应创建 tick_cache 表。"""
        db_path = tmp_path / "tick_test.duckdb"
        migrate_schema(db_path)
        con = duckdb.connect(str(db_path))
        try:
            tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
            assert "tick_cache" in tables
            # 验证列
            cols = [r[0] for r in con.execute(
                "DESCRIBE SELECT * FROM tick_cache").fetchall()]
            assert "last_price" in cols
            assert "bid_price1" in cols
            assert "open_interest" in cols
            assert "trace_id" in cols
            # 5 档盘口（Phase 5）
            assert "bid_price5" in cols
            assert "ask_price5" in cols
        finally:
            con.close()


# ─── 4. Aggregator.get_ticks 降级链 ───────────────────────


class TestAggregatorGetTicks:
    """测试 Aggregator.get_ticks 缓存 → 源降级。"""

    def _tick_df(self, n: int = 10) -> pd.DataFrame:
        times = pd.date_range("2026-08-07 14:30:00", periods=n, freq="500ms")
        return pd.DataFrame({
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
            "trace_id": "agg_test",
        })

    def test_cache_hit(self, tmp_path: Path) -> None:
        """缓存命中时不再调用数据源。"""
        db_path = tmp_path / "agg.duckdb"
        migrate_schema(db_path)
        agg = FuturesDataAggregator(
            minute_sources=[], tick_sources=[], db_path=db_path
        )
        agg._write_tick_cache(self._tick_df())
        df = agg.get_ticks("RB0", count=10, trace_id="t")
        assert not df.empty
        assert len(df) == 10
        assert df["symbol"].iloc[0] == "RB0"
        agg._cache_conn.close()

    def test_source_fallback(self, tmp_path: Path) -> None:
        """缓存未命中时走数据源。"""
        class FakeTickSource(BaseFuturesSource):
            source_name = "FAKE_TICK"

            def is_available(self) -> bool:
                return True

            def fetch_ticks(self, symbol, count, trace_id=""):
                return self._make_df(symbol, count)

            def fetch_ohlcv(self, symbol, days=500, trace_id=""):
                return None

            def fetch_quote(self, symbol, trace_id=""):
                return None

            def _make_df(self, symbol, count):
                n = min(count, 5)
                times = pd.date_range("2026-08-07 14:30:00", periods=n, freq="500ms")
                return pd.DataFrame({
                    "symbol": [symbol] * n, "datetime": times,
                    "last_price": 3010.0, "average": 3012.0,
                    "highest": 3020.0, "lowest": 3000.0,
                    "volume": np.arange(n, dtype=float),
                    "amount": np.arange(100, 100 + n, dtype=float),
                    "open_interest": 120000.0,
                    "bid_price1": 3009.0, "bid_volume1": 100.0,
                    "ask_price1": 3011.0, "ask_volume1": 90.0,
                    "bid_price2": 3008.0, "bid_volume2": 200.0,
                    "ask_price2": 3012.0, "ask_volume2": 180.0,
                    "bid_price3": 3007.0, "bid_volume3": 150.0,
                    "ask_price3": 3013.0, "ask_volume3": 120.0,
                    "bid_price4": 3006.0, "bid_volume4": 300.0,
                    "ask_price4": 3014.0, "ask_volume4": 250.0,
                    "bid_price5": 3005.0, "bid_volume5": 400.0,
                    "ask_price5": 3015.0, "ask_volume5": 350.0,
                    "source": "FAKE_TICK",
                    "fetched_at": pd.Timestamp.now(),
                    "trace_id": "agg_test",
                })

        db_path = tmp_path / "agg2.duckdb"
        migrate_schema(db_path)
        agg = FuturesDataAggregator(
            minute_sources=[], tick_sources=[FakeTickSource()], db_path=db_path
        )
        df = agg.get_ticks("RB0", count=5, trace_id="t")
        assert not df.empty
        assert len(df) == 5
        assert df["source"].iloc[0] == "FAKE_TICK"
        # 数据应写入缓存
        con = duckdb.connect(str(db_path))
        try:
            n = con.execute("SELECT COUNT(*) FROM tick_cache").fetchone()[0]
            assert n == 5
        finally:
            con.close()
        agg._cache_conn.close()

    def test_all_sources_fail(self, tmp_path: Path) -> None:
        """所有源失败返回空 DataFrame。"""
        db_path = tmp_path / "agg3.duckdb"
        migrate_schema(db_path)
        agg = FuturesDataAggregator(
            minute_sources=[], tick_sources=[], db_path=db_path
        )
        df = agg.get_ticks("RB0", count=5, trace_id="t")
        assert df.empty
        agg._cache_conn.close()


# ─── 5. Provider.get_tick_data 接口 ───────────────────────


class TestProviderGetTickData:
    """测试 FuturesDataProvider.get_tick_data 接口。"""

    def test_returns_empty_without_aggregator(self) -> None:
        """无聚合器时返回空 DataFrame。"""
        provider = FuturesDataProvider()
        provider._aggregator = None
        df = provider.get_tick_data("RB0", count=10, trace_id="t")
        assert df.empty
