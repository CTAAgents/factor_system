"""
tests/test_data_futures.py — FTS 期货数据模块（fts/data_futures.py）单元测试。

覆盖目标:
  1. retry_on_conflict: 成功 / 冲突重试 / 耗尽抛出 / 非冲突异常直抛
  2. AsyncWriteQueue: 写入队列串行化（成功 / 异常 / 超时继续 / 停止 / flush / queue_size）
  3. DuckDBConnection: 连接管理（复用 / 锁配置降级 / 异步队列 / close / stop_async）
  4. _get_db / get_futures_provider: 模块级单例与失败路径
  5. FuturesDataProvider:
     - _init_default_aggregator 各分支（成功 / 源失败 / 聚合器失败）
     - _from_aggregator_df 列转换
     - get_ohlcv 降级链（Aggregator → DuckDB → TQ-Local → AKShare → 合成）
     - get_minute_ohlcv / get_tick_data
     - _from_kline_cache / _from_tq_local / _from_akshare
  6. 实时价路径: _try_tq_realtime / _try_akshare_realtime / _extract_quote_price / get_realtime_prices
  7. get_dominant_contracts / _fetch_dominant_akshare 补充分支

隔离性: 全部使用 mock 数据源 / 合成 DataFrame，不访问真实 DuckDB 文件与网络。
"""

from __future__ import annotations

import asyncio
import sys

import numpy as np
import pandas as pd
import pytest

import fts.data_futures as fut_mod
from fts.data_futures import (
    FUTURES_SUBSET,
    AsyncWriteQueue,
    DuckDBConnection,
    FuturesDataError,
    FuturesDataProvider,
    _extract_quote_price,
    _fetch_dominant_akshare,
    _get_db,
    _try_akshare_realtime,
    _try_tq_realtime,
    get_dominant_contracts,
    get_futures_provider,
    get_realtime_prices,
    retry_on_conflict,
)


# ─── 工具函数 ──────────────────────────────────────────────


def _make_provider_df(dates: list[str], base: float = 100.0) -> pd.DataFrame:
    """构造 FuturesDataProvider 输出格式（含 vwap/hold/settle）的 DataFrame。"""
    idx = pd.DatetimeIndex(pd.to_datetime(dates))
    close = pd.Series(np.arange(len(idx)) * 0.1 + base, index=idx)
    return pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 1000.0,
            "vwap": close + 0.5,
            "hold": 5000.0,
            "settle": close,
        }
    )


def _make_agg_df(dates: list[str], base: float = 100.0, source: str = "DUCKDB") -> pd.DataFrame:
    """构造 FuturesDataAggregator 输出格式（含 date/source 列）的 DataFrame。"""
    df = _make_provider_df(dates, base=base).reset_index()
    df = df.rename(columns={"index": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df["source"] = source
    return df


class _FakeConn:
    """最小 DuckDB 连接 stub（AsyncWriteQueue 测试用）。"""

    def __init__(self, error: bool = False):
        self._error = error
        self.executed: list = []

    def execute(self, sql: str, params=None):
        if self._error:
            raise RuntimeError("boom")
        self.executed.append((sql, params))
        return "ok"


@pytest.fixture(autouse=True)
def _reset_module_globals():
    """每个测试后重置模块级全局连接/单例，避免测试间串扰。"""
    yield
    fut_mod._DB = None
    fut_mod._default_futures_provider = None


# ═══════════════════════════════════════════════════════════
# 1. retry_on_conflict
# ═══════════════════════════════════════════════════════════


class TestRetryOnConflict:
    @pytest.fixture(autouse=True)
    def _inject_conflict_exception(self, monkeypatch):
        """注入 duckdb.ConcurrentTransactionException 假类以验证重试逻辑本身。

        注：duckdb 1.1.3 实际不存在该异常类（产品 bug #1，见
        TestRetryOnConflictRealEnvBug），此处仅用于隔离测试装饰器逻辑。
        """

        class _FakeConcurrent(Exception):
            pass

        monkeypatch.setattr(
            "duckdb.ConcurrentTransactionException",
            _FakeConcurrent,
            raising=False,
        )

    def test_first_try_success(self):
        """无冲突时直接成功，不重试。"""
        calls: list[int] = []

        @retry_on_conflict(max_retries=3, delay=0.01)
        def f():
            calls.append(1)
            return "ok"

        assert f() == "ok"
        assert len(calls) == 1

    def test_recovers_after_conflicts(self):
        """写冲突后按退避重试，最终成功。"""
        import duckdb

        calls: list[int] = []

        @retry_on_conflict(max_retries=3, delay=0.01)
        def f():
            calls.append(1)
            if len(calls) < 3:
                raise duckdb.ConcurrentTransactionException("conflict")
            return "ok"

        assert f() == "ok"
        assert len(calls) == 3

    def test_exhausted_raises(self):
        """重试耗尽后抛出最后一次异常。"""
        import duckdb

        @retry_on_conflict(max_retries=2, delay=0.01)
        def f():
            raise duckdb.ConcurrentTransactionException("conflict")

        with pytest.raises(duckdb.ConcurrentTransactionException):
            f()

    def test_non_conflict_raises_immediately(self):
        """非并发冲突异常直接传播，不重试。"""

        @retry_on_conflict(max_retries=3, delay=0.01)
        def f():
            raise ValueError("other")

        with pytest.raises(ValueError):
            f()


class TestRetryOnConflictRealEnvBug:
    """真实环境（duckdb 1.1.3）下 retry_on_conflict 修复后的行为 — 产品 bug #1。

    修复前：except 子句引用不存在的 ConcurrentTransactionException，
    被装饰函数抛任何异常时求值即抛 AttributeError，掩盖原始异常、重试失效。
    修复后：兼容获取 TransactionException，写冲突正常重试，非冲突异常直接传播。
    """

    def test_non_conflict_exception_propagates(self):
        """非写冲突异常直接传播，不再被 AttributeError 掩盖。"""
        import duckdb

        if hasattr(duckdb, "ConcurrentTransactionException"):
            pytest.skip("当前 duckdb 版本存在该异常类，无此 bug")
        calls: list[int] = []

        @retry_on_conflict(max_retries=2, delay=0.01)
        def f():
            calls.append(1)
            raise ValueError("boom")

        with pytest.raises(ValueError):
            f()
        assert len(calls) == 1  # 不重试、不掩盖

    def test_transaction_conflict_retries(self):
        """真实 TransactionException 写冲突触发退避重试。"""
        import duckdb

        if hasattr(duckdb, "ConcurrentTransactionException"):
            pytest.skip("当前 duckdb 版本存在该异常类，无此 bug")
        calls: list[int] = []

        @retry_on_conflict(max_retries=3, delay=0.01)
        def f():
            calls.append(1)
            if len(calls) < 3:
                raise duckdb.TransactionException("conflict")
            return "ok"

        assert f() == "ok"
        assert len(calls) == 3


# ═══════════════════════════════════════════════════════════
# 2. AsyncWriteQueue
# ═══════════════════════════════════════════════════════════


class TestAsyncWriteQueue:
    def test_execute_success_with_and_without_params(self):
        """worker 串行执行带/不带参数的写入。"""

        async def scenario():
            conn = _FakeConn()
            q = AsyncWriteQueue(conn, max_queue_size=10)
            q.start()
            assert q.queue_size == 0
            r1 = await q.execute("INSERT INTO t VALUES (?, ?)", [1, 2])
            r2 = await q.execute("SELECT 1")
            await q.flush()
            assert r1 == "ok"
            assert r2 == "ok"
            assert conn.executed == [
                ("INSERT INTO t VALUES (?, ?)", [1, 2]),
                ("SELECT 1", None),
            ]
            await q.stop()
            assert q._worker_task is None

        asyncio.run(scenario())

    def test_execute_propagates_error(self):
        """worker 执行异常 → future 抛出异常，不吞错。"""

        async def scenario():
            conn = _FakeConn(error=True)
            q = AsyncWriteQueue(conn)
            q.start()
            with pytest.raises(RuntimeError, match="boom"):
                await q.execute("BAD SQL", [1])
            await q.stop()

        asyncio.run(scenario())

    def test_worker_timeout_keeps_running(self):
        """队列空时 worker 超时后继续循环（不退出）。"""

        async def scenario():
            q = AsyncWriteQueue(_FakeConn())
            q.start()
            await asyncio.sleep(1.2)  # 等待 worker 的 1.0s 超时窗口
            assert q._running
            await q.stop()

        asyncio.run(scenario())

    def test_start_idempotent(self):
        """重复 start 不重复创建 worker。"""

        async def scenario():
            q = AsyncWriteQueue(_FakeConn())
            q.start()
            task1 = q._worker_task
            q.start()
            assert q._worker_task is task1
            await q.stop()

        asyncio.run(scenario())

    def test_flush_waits_queue_drain(self):
        """flush 等待队列清空。"""

        async def scenario():
            conn = _FakeConn()
            q = AsyncWriteQueue(conn)
            q.start()
            await q.execute("INSERT INTO t VALUES (1)")
            await q.flush()
            assert q.queue_size == 0
            assert len(conn.executed) == 1
            await q.stop()

        asyncio.run(scenario())


# ═══════════════════════════════════════════════════════════
# 3. DuckDBConnection
# ═══════════════════════════════════════════════════════════


class TestDuckDBConnection:
    def test_connect_and_execute_real(self, tmp_path):
        """真实 DuckDB 连接：execute 带/不带参数、close 后连接置空。"""
        db = DuckDBConnection(tmp_path / "test.duckdb")
        db.execute("CREATE TABLE t (a INT, b VARCHAR)")
        db.execute("INSERT INTO t VALUES (?, ?)", [1, "x"])
        rows = db.execute("SELECT a, b FROM t").fetchall()
        assert rows == [(1, "x")]
        db.close()
        assert db._conn is None

    def test_connect_reuses_connection(self, tmp_path):
        """多次 connect 返回同一连接对象。"""
        db = DuckDBConnection(tmp_path / "test.duckdb")
        c1 = db.connect()
        c2 = db.connect()
        assert c1 is c2
        db.close()

    def test_lock_configuration_failure_fallback(self, tmp_path, mocker):
        """SET lock_configuration 不支持时静默降级，不影响连接。"""
        mock_conn = mocker.MagicMock()
        mock_conn.execute.side_effect = Exception("syntax error")
        mocker.patch("duckdb.connect", return_value=mock_conn)
        db = DuckDBConnection(tmp_path / "test.duckdb")
        assert db.connect() is mock_conn
        mock_conn.execute.assert_called_once_with("SET lock_configuration = true")

    def test_no_lock_config_when_retries_zero(self, tmp_path, mocker):
        """concurrency_retries=0 时不执行 SET lock_configuration。"""
        mock_conn = mocker.MagicMock()
        mocker.patch("duckdb.connect", return_value=mock_conn)
        db = DuckDBConnection(tmp_path / "test.duckdb", concurrency_retries=0)
        db.connect()
        mock_conn.execute.assert_not_called()

    def test_async_execute_disabled_raises(self, tmp_path):
        """未启用异步队列时 async_execute 抛 RuntimeError。"""
        db = DuckDBConnection(tmp_path / "test.duckdb")
        with pytest.raises(RuntimeError, match="异步写入队列未启用"):
            asyncio.run(db.async_execute("SELECT 1"))

    def test_async_execute_enabled(self, tmp_path):
        """启用异步队列后 async_execute 经队列串行执行。"""
        db = DuckDBConnection(tmp_path / "test.duckdb", enable_async_queue=True)

        async def scenario():
            await db.async_execute("CREATE TABLE t (a INT)")
            await db.async_execute("INSERT INTO t VALUES (1)")
            await db.async_execute("INSERT INTO t VALUES (2)")
            rows = db.execute("SELECT a FROM t ORDER BY a").fetchall()
            assert rows == [(1,), (2,)]
            await db.stop_async()
            assert db._async_queue is None

        asyncio.run(scenario())
        db.close()

    def test_stop_async_without_queue(self, tmp_path):
        """未启用异步队列时 stop_async 为 no-op。"""
        db = DuckDBConnection(tmp_path / "test.duckdb")
        asyncio.run(db.stop_async())
        db.close()


# ═══════════════════════════════════════════════════════════
# 4. _get_db 模块级连接
# ═══════════════════════════════════════════════════════════


class TestGetDb:
    def test_returns_connected_connection(self, mocker):
        """首次调用创建 DuckDBConnection 并返回原生连接。"""
        mock_db = mocker.MagicMock()
        mock_db.connect.return_value = "native-conn"
        mocker.patch("fts.data_futures.DuckDBConnection", return_value=mock_db)
        assert _get_db() == "native-conn"

    def test_init_failure_raises_futures_error(self, mocker):
        """连接初始化失败 → FuturesDataError。"""
        mocker.patch(
            "fts.data_futures.DuckDBConnection",
            side_effect=RuntimeError("boom"),
        )
        with pytest.raises(FuturesDataError, match="DuckDB 连接初始化失败"):
            _get_db()


# ═══════════════════════════════════════════════════════════
# 5. FuturesDataProvider._init_default_aggregator
# ═══════════════════════════════════════════════════════════


class TestInitDefaultAggregator:
    def _patch_sources(self, mocker):
        """默认所有数据源可正常实例化（聚合器由各测试单独控制）。"""
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource")
        mocker.patch("fts.data_sources.tqsdk_source.TQSDKSource")
        mocker.patch("fts.data_sources.tqsdk_tick_source.TQSDKTickSource")

    def test_aggregator_initialized(self, mocker):
        """默认路径：所有源实例化成功，聚合器非空。"""
        mock_agg = mocker.MagicMock()
        patched_agg = mocker.patch(
            "fts.data_sources.aggregator.FuturesDataAggregator",
            return_value=mock_agg,
        )
        self._patch_sources(mocker)
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=None)
        assert provider._aggregator is mock_agg
        assert patched_agg.call_args.kwargs["cache_max_age_days"] == 30

    def test_aggregator_initialized_with_sources_failing(self, mocker):
        """部分源实例化失败被跳过，聚合器仍初始化（空源列表）。"""
        mock_agg = mocker.MagicMock()
        mocker.patch("fts.data_sources.aggregator.FuturesDataAggregator", return_value=mock_agg)
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", side_effect=RuntimeError("no tdx"))
        mocker.patch("fts.data_sources.tqsdk_source.TQSDKSource", side_effect=RuntimeError("no tqsdk"))
        mocker.patch("fts.data_sources.tqsdk_tick_source.TQSDKTickSource", side_effect=RuntimeError("no tick"))
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=None)
        assert provider._aggregator is mock_agg

    def test_aggregator_constructor_fails(self, mocker):
        """聚合器构造失败 → _aggregator 置 None（降级到直接路径）。"""
        mocker.patch(
            "fts.data_sources.aggregator.FuturesDataAggregator",
            side_effect=RuntimeError("boom"),
        )
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", side_effect=RuntimeError("no tdx"))
        mocker.patch("fts.data_sources.tqsdk_source.TQSDKSource", side_effect=RuntimeError("no tqsdk"))
        mocker.patch("fts.data_sources.tqsdk_tick_source.TQSDKTickSource", side_effect=RuntimeError("no tick"))
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=None)
        assert provider._aggregator is None


# ═══════════════════════════════════════════════════════════
# 6. _from_aggregator_df
# ═══════════════════════════════════════════════════════════


class TestFromAggregatorDf:
    def test_converts_columns_and_index(self):
        """17 列聚合器输出 → 8 列标准格式，date 索引升序。"""
        agg_df = _make_agg_df(["2026-01-02", "2026-01-01"], base=100.0)
        df = FuturesDataProvider._from_aggregator_df(agg_df, "RB0")
        assert list(df.columns) == ["open", "high", "low", "close", "volume", "vwap", "hold", "settle"]
        assert isinstance(df.index, pd.DatetimeIndex)
        assert df.index.is_monotonic_increasing


# ═══════════════════════════════════════════════════════════
# 7. get_ohlcv 降级链
# ═══════════════════════════════════════════════════════════


class TestGetOhlcvFallbackChain:
    def _provider(self, mocker, use_akshare: bool = False, aggregator=None):
        return FuturesDataProvider(
            use_akshare_fallback=use_akshare,
            aggregator=aggregator or mocker.MagicMock(),
        )

    def test_aggregator_hit(self, mocker):
        """Aggregator 返回非合成数据 → 直接采用。

        v2.58.0 (GAP-046): 复权路径额外返回 adj_factor 列（供落库使用）。
        """
        mock_agg = mocker.MagicMock()
        mock_agg.get_ohlcv.return_value = _make_agg_df(["2026-01-01", "2026-01-02"])
        provider = self._provider(mocker, aggregator=mock_agg)
        df = provider.get_ohlcv("RB0", days=30)
        assert list(df.columns) == [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "vwap",
            "hold",
            "settle",
            "adj_factor",
        ]
        mock_agg.get_ohlcv.assert_called_once_with("RB0", 30, "")

    def test_aggregator_synthetic_falls_through_to_duckdb(self, mocker):
        """Aggregator 返回 SYNTHETIC → 继续尝试 DuckDB。"""
        mock_agg = mocker.MagicMock()
        mock_agg.get_ohlcv.return_value = _make_agg_df(
            ["2026-01-01", "2026-01-02"],
            source="SYNTHETIC",
        )
        provider = self._provider(mocker, aggregator=mock_agg)
        kline_df = _make_provider_df(["2026-01-01", "2026-01-02"])
        mocker.patch.object(provider, "_from_kline_cache", return_value=kline_df)
        df = provider.get_ohlcv("RB0", days=30)
        assert not df.empty
        provider._from_kline_cache.assert_called_once_with("RB0", 30)

    def test_aggregator_error_falls_back_to_duckdb(self, mocker):
        """Aggregator 抛异常 → 降级 DuckDB。"""
        mock_agg = mocker.MagicMock()
        mock_agg.get_ohlcv.side_effect = RuntimeError("boom")
        provider = self._provider(mocker, aggregator=mock_agg)
        kline_df = _make_provider_df(["2026-01-01"])
        mocker.patch.object(provider, "_from_kline_cache", return_value=kline_df)
        df = provider.get_ohlcv("RB0", days=30)
        assert not df.empty

    def test_duckdb_empty_falls_to_tq(self, mocker):
        """DuckDB 无数据 → TQ-Local。"""
        provider = self._provider(mocker)
        provider._aggregator.get_ohlcv.return_value = None
        mocker.patch.object(provider, "_from_kline_cache", return_value=None)
        tq_df = _make_provider_df(["2026-01-01"])
        mocker.patch.object(provider, "_from_tq_local", return_value=tq_df)
        df = provider.get_ohlcv("RB0", days=30)
        assert not df.empty
        provider._from_tq_local.assert_called_once_with("RB0", 30)

    def test_duckdb_exception_falls_to_tq(self, mocker):
        """DuckDB 读取抛异常 → 继续降级 TQ-Local。"""
        provider = self._provider(mocker)
        provider._aggregator.get_ohlcv.return_value = None
        mocker.patch.object(provider, "_from_kline_cache", side_effect=RuntimeError("boom"))
        tq_df = _make_provider_df(["2026-01-01"])
        mocker.patch.object(provider, "_from_tq_local", return_value=tq_df)
        df = provider.get_ohlcv("RB0", days=30)
        assert not df.empty

    def test_tq_empty_falls_to_akshare(self, mocker):
        """TQ-Local 无数据 → AKShare 即时获取。"""
        provider = self._provider(mocker, use_akshare=True)
        provider._aggregator.get_ohlcv.return_value = None
        mocker.patch.object(provider, "_from_kline_cache", return_value=None)
        mocker.patch.object(provider, "_from_tq_local", return_value=None)
        ak_df = _make_provider_df(["2026-01-01"])
        mocker.patch.object(provider, "_from_akshare", return_value=ak_df)
        df = provider.get_ohlcv("RB0", days=30)
        assert not df.empty
        provider._from_akshare.assert_called_once_with("RB0", 30)

    def test_tq_exception_falls_to_akshare(self, mocker):
        """TQ-Local 抛异常 → 继续降级 AKShare。"""
        provider = self._provider(mocker, use_akshare=True)
        provider._aggregator.get_ohlcv.return_value = None
        mocker.patch.object(provider, "_from_kline_cache", return_value=None)
        mocker.patch.object(provider, "_from_tq_local", side_effect=RuntimeError("boom"))
        ak_df = _make_provider_df(["2026-01-01"])
        mocker.patch.object(provider, "_from_akshare", return_value=ak_df)
        df = provider.get_ohlcv("RB0", days=30)
        assert not df.empty

    def test_all_sources_fail_synthetic(self, mocker):
        """全部源失败 → 合成数据降级。"""
        provider = self._provider(mocker, use_akshare=True)
        provider._aggregator.get_ohlcv.return_value = None
        mocker.patch.object(provider, "_from_kline_cache", return_value=None)
        mocker.patch.object(provider, "_from_tq_local", return_value=None)
        mocker.patch.object(provider, "_from_akshare", return_value=None)
        df = provider.get_ohlcv("RB0", days=30)
        assert len(df) == 30
        assert "settle" in df.columns

    def test_akshare_exception_falls_to_synthetic(self, mocker):
        """AKShare 抛异常 → 合成数据降级。"""
        provider = self._provider(mocker, use_akshare=True)
        provider._aggregator.get_ohlcv.return_value = None
        mocker.patch.object(provider, "_from_kline_cache", return_value=None)
        mocker.patch.object(provider, "_from_tq_local", return_value=None)
        mocker.patch.object(provider, "_from_akshare", side_effect=RuntimeError("boom"))
        df = provider.get_ohlcv("RB0", days=30)
        assert len(df) == 30

    def test_akshare_disabled_skips_to_synthetic(self, mocker):
        """use_akshare_fallback=False 时跳过 AKShare 直接合成。"""
        provider = self._provider(mocker, use_akshare=False)
        provider._aggregator.get_ohlcv.return_value = None
        mocker.patch.object(provider, "_from_kline_cache", return_value=None)
        mocker.patch.object(provider, "_from_tq_local", return_value=None)
        ak_mock = mocker.patch.object(provider, "_from_akshare", return_value=None)
        df = provider.get_ohlcv("RB0", days=30)
        assert len(df) == 30
        ak_mock.assert_not_called()


# ═══════════════════════════════════════════════════════════
# 8. get_minute_ohlcv / get_tick_data
# ═══════════════════════════════════════════════════════════


class TestMinuteAndTick:
    def _minute_df(self):
        return pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-01-01 09:05:00", "2026-01-01 09:10:00"]),
                "open": [1.0, 2.0],
                "high": [2.0, 3.0],
                "low": [0.5, 1.5],
                "close": [1.5, 2.5],
                "volume": [100.0, 200.0],
                "source": ["TDX", "TDX"],
            }
        )

    def test_minute_hit(self, mocker):
        """分钟数据命中 → datetime 索引 + OHLCV 五列。"""
        mock_agg = mocker.MagicMock()
        mock_agg.get_minute_ohlcv.return_value = self._minute_df()
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=mock_agg)
        df = provider.get_minute_ohlcv("RB0", days=100, frequency="5m", trace_id="t1")
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert isinstance(df.index, pd.DatetimeIndex)
        mock_agg.get_minute_ohlcv.assert_called_once_with("RB0", 100, "5m", "t1")

    def test_minute_empty(self, mocker):
        """分钟数据为空 → 空 DataFrame。"""
        mock_agg = mocker.MagicMock()
        mock_agg.get_minute_ohlcv.return_value = None
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=mock_agg)
        assert provider.get_minute_ohlcv("RB0", days=100).empty

    def test_minute_error(self, mocker):
        """分钟数据聚合器异常 → 空 DataFrame（不抛）。"""
        mock_agg = mocker.MagicMock()
        mock_agg.get_minute_ohlcv.side_effect = RuntimeError("boom")
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=mock_agg)
        assert provider.get_minute_ohlcv("RB0", days=100).empty

    def _tick_df(self):
        return pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2026-01-01 09:00:00.5", "2026-01-01 09:00:01.0"]),
                "last_price": [10.0, 10.5],
                "source": ["TQSDK", "TQSDK"],
            }
        )

    def test_tick_hit(self, mocker):
        """tick 数据命中 → datetime 索引。"""
        mock_agg = mocker.MagicMock()
        mock_agg.get_ticks.return_value = self._tick_df()
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=mock_agg)
        df = provider.get_tick_data("RB0", count=100, trace_id="t1")
        assert isinstance(df.index, pd.DatetimeIndex)
        assert not df.empty
        mock_agg.get_ticks.assert_called_once_with("RB0", 100, "t1")

    def test_tick_empty(self, mocker):
        """tick 数据为空 → 空 DataFrame。"""
        mock_agg = mocker.MagicMock()
        mock_agg.get_ticks.return_value = None
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=mock_agg)
        assert provider.get_tick_data("RB0", count=100).empty

    def test_tick_error(self, mocker):
        """tick 聚合器异常 → 空 DataFrame（不抛）。"""
        mock_agg = mocker.MagicMock()
        mock_agg.get_ticks.side_effect = RuntimeError("boom")
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=mock_agg)
        assert provider.get_tick_data("RB0", count=100).empty


# ═══════════════════════════════════════════════════════════
# 9. _from_kline_cache
# ═══════════════════════════════════════════════════════════


class TestFromKlineCache:
    def test_reads_and_enriches(self, mocker):
        """DuckDB 读取：vwap 精确计算、settle/hold 代理列、升序索引。"""
        mock_db = mocker.MagicMock()
        mock_result = mocker.MagicMock()
        # 8 列: date, open, high, low, close, volume, amount, vwap
        mock_result.fetchall.return_value = [
            ("2026-01-02", 10.5, 11.5, 9.5, 11.0, 2000.0, 22000.0, 11.0),
            ("2026-01-01", 10.0, 11.0, 9.0, 10.5, 1000.0, 10500.0, 10.5),
        ]
        mock_db.execute.return_value = mock_result
        mocker.patch("fts.data_futures._get_reader", return_value=mock_db)
        mocker.patch("fts.data_futures._release_reader")

        mocker.patch.object(FuturesDataProvider, "_init_default_aggregator")
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=None)
        df = provider._from_kline_cache("RB0", days=10)
        assert list(df.columns) == ["open", "high", "low", "close", "volume", "vwap", "hold", "settle"]
        assert df.index.is_monotonic_increasing
        # 排序后第一行 = 2026-01-01: vwap = amount/volume = 10500/1000
        assert df["vwap"].iloc[0] == pytest.approx(10500.0 / 1000.0)
        # settle 代理 = (H+L+C)/3
        assert df["settle"].iloc[0] == pytest.approx((11.0 + 9.0 + 10.5) / 3)
        # hold 代理 = 20 日滚动均量（min_periods=1）
        assert df["hold"].iloc[0] == 1000.0

    def test_empty_rows_returns_none(self, mocker):
        """无数据行 → None。"""
        mock_db = mocker.MagicMock()
        mock_result = mocker.MagicMock()
        mock_result.fetchall.return_value = []
        mock_db.execute.return_value = mock_result
        mocker.patch("fts.data_futures._get_reader", return_value=mock_db)
        mocker.patch("fts.data_futures._release_reader")
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=None)
        mocker.patch.object(provider, "_init_default_aggregator")
        assert provider._from_kline_cache("RB0", days=10) is None


# ═══════════════════════════════════════════════════════════
# 10. _from_tq_local
# ═══════════════════════════════════════════════════════════


class TestFromTqLocal:
    def _provider(self, mocker):
        provider = FuturesDataProvider(use_akshare_fallback=False, aggregator=None)
        mocker.patch.object(provider, "_init_default_aggregator")
        return provider

    def test_service_unavailable_returns_none(self, mocker):
        """TQ 服务不可达 → None。"""
        mock_source = mocker.MagicMock()
        mock_source.is_available.return_value = False
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", return_value=mock_source)
        assert self._provider(mocker)._from_tq_local("RB0", 10) is None

    def test_hit_normalizes_columns(self, mocker):
        """TQ 命中 → 标准 8 列输出。"""
        mock_source = mocker.MagicMock()
        mock_source.is_available.return_value = True
        mock_source.fetch_ohlcv.return_value = (
            _make_provider_df(
                ["2026-01-02", "2026-01-01"],
            )
            .reset_index()
            .rename(columns={"index": "date"})
        )
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", return_value=mock_source)
        df = self._provider(mocker)._from_tq_local("RB0", 10)
        assert list(df.columns) == ["open", "high", "low", "close", "volume", "vwap", "hold", "settle"]
        assert df.index.is_monotonic_increasing

    def test_empty_or_missing_close_returns_none(self, mocker):
        """空数据或缺 close 列 → None。"""
        mock_source = mocker.MagicMock()
        mock_source.is_available.return_value = True
        mock_source.fetch_ohlcv.return_value = pd.DataFrame()
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", return_value=mock_source)
        assert self._provider(mocker)._from_tq_local("RB0", 10) is None

        mock_source.fetch_ohlcv.return_value = pd.DataFrame(
            {
                "date": ["2026-01-01"],
                "open": [1.0],  # 无 close
            }
        )
        assert self._provider(mocker)._from_tq_local("RB0", 10) is None

    def test_import_error_returns_none(self, mocker):
        """TdxLocalSource 实例化抛 ImportError → None（降级不抛）。"""
        mocker.patch(
            "fts.data_sources.tdx_local_source.TdxLocalSource",
            side_effect=ImportError("no module"),
        )
        assert self._provider(mocker)._from_tq_local("RB0", 10) is None

    def test_generic_exception_returns_none(self, mocker):
        """TQ 通用异常 → None（降级不抛）。"""
        mock_source = mocker.MagicMock()
        mock_source.is_available.return_value = True
        mock_source.fetch_ohlcv.side_effect = RuntimeError("boom")
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", return_value=mock_source)
        assert self._provider(mocker)._from_tq_local("RB0", 10) is None


# ═══════════════════════════════════════════════════════════
# 11. _from_akshare
# ═══════════════════════════════════════════════════════════


class TestFromAkshare:
    def _provider(self, mocker):
        mocker.patch.object(FuturesDataProvider, "_init_default_aggregator")
        return FuturesDataProvider(use_akshare_fallback=True, aggregator=None)

    def test_akshare_not_installed_returns_none(self, mocker):
        """akshare 未安装 → None。"""
        mocker.patch.dict(sys.modules, {"akshare": None})
        assert self._provider(mocker)._from_akshare("RB0", 10) is None

    def test_hit_with_full_columns(self, mocker):
        """AKShare 命中：列标准化 + vwap=(H+L+C+settle)/4。"""
        df = pd.DataFrame(
            {
                "date": ["2026-01-01", "2026-01-02"],
                "open": [10.0, 10.5],
                "high": [11.0, 11.5],
                "low": [9.0, 9.5],
                "close": [10.5, 11.0],
                "volume": [1000.0, 2000.0],
                "hold": [5000.0, 6000.0],
                "settle": [10.6, 11.1],
            }
        )
        mocker.patch("akshare.futures_zh_daily_sina", return_value=df)
        result = self._provider(mocker)._from_akshare("RB", 10)
        assert list(result.columns) == ["open", "high", "low", "close", "volume", "hold", "settle", "vwap"]
        assert result["vwap"].iloc[0] == pytest.approx((11.0 + 9.0 + 10.5 + 10.6) / 4)

    def test_limit_days(self, mocker):
        """行数超过 days 时截断。"""
        df = pd.DataFrame(
            {
                "date": [f"2026-01-{d:02d}" for d in range(1, 6)],
                "open": [10.0] * 5,
                "high": [11.0] * 5,
                "low": [9.0] * 5,
                "close": [10.5] * 5,
                "volume": [1000.0] * 5,
            }
        )
        mocker.patch("akshare.futures_zh_daily_sina", return_value=df)
        result = self._provider(mocker)._from_akshare("RB0", 2)
        assert len(result) == 2

    def test_missing_required_column_returns_none(self, mocker):
        """缺必要列 → None。"""
        df = pd.DataFrame(
            {
                "date": ["2026-01-01"],
                "open": [10.0],
                "high": [11.0],  # 无 close/volume
            }
        )
        mocker.patch("akshare.futures_zh_daily_sina", return_value=df)
        assert self._provider(mocker)._from_akshare("RB0", 10) is None

    def test_api_error_raises_futures_error(self, mocker):
        """AKShare 接口异常 → FuturesDataError。"""
        mocker.patch("akshare.futures_zh_daily_sina", side_effect=RuntimeError("boom"))
        with pytest.raises(FuturesDataError, match="AKShare 获取失败"):
            self._provider(mocker)._from_akshare("RB0", 10)

    def test_empty_response_returns_none(self, mocker):
        """AKShare 返回空 → None。"""
        mocker.patch("akshare.futures_zh_daily_sina", return_value=None)
        assert self._provider(mocker)._from_akshare("RB0", 10) is None


# ═══════════════════════════════════════════════════════════
# 12. 实时价路径
# ═══════════════════════════════════════════════════════════


class TestExtractQuotePrice:
    def test_all_field_precedence(self):
        """按字段优先级提取价格。"""
        assert _extract_quote_price({"last_price": 12.5}) == 12.5
        assert _extract_quote_price({"price": "13.0"}) == 13.0
        assert _extract_quote_price({"close": 14}) == 14.0
        assert _extract_quote_price({"bid_price": 1.5}) == 1.5
        assert _extract_quote_price({"current": 2.5}) == 2.5
        assert _extract_quote_price({"now": 3.5}) == 3.5

    def test_invalid_values(self):
        """零值 / 非数字 / 缺失 → None。"""
        assert _extract_quote_price({"last_price": 0}) is None
        assert _extract_quote_price({"last_price": "abc"}) is None
        assert _extract_quote_price({}) is None
        assert _extract_quote_price({"price": None}) is None


class TestTryTqRealtime:
    def test_import_error(self, mocker):
        """TdxLocalSource 模块不可用 → 全部失败。"""
        mocker.patch.dict(sys.modules, {"fts.data_sources.tdx_local_source": None})
        prices, failed = _try_tq_realtime(["RB0"])
        assert prices == {}
        assert failed == {"RB0"}

    def test_service_not_available(self, mocker):
        """TQ 探活失败 → 全部失败。"""
        mock_source = mocker.MagicMock()
        mock_source.is_available.return_value = False
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", return_value=mock_source)
        prices, failed = _try_tq_realtime(["RB0"])
        assert prices == {}
        assert failed == {"RB0"}

    def test_mixed_success_failure(self, mocker):
        """成功 / None / 非正价 / 异常 各路径。"""
        mock_source = mocker.MagicMock()
        mock_source.is_available.return_value = True
        mock_source.fetch_quote.side_effect = [
            {"last_price": 3500.0},  # 成功
            None,  # None → failed
            {"price": 0},  # 非正 → failed
            {"now": 100.5},  # 成功
        ]
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", return_value=mock_source)
        prices, failed = _try_tq_realtime(["RB0", "CU0", "AU0", "AG0"])
        assert prices == {"RB0": 3500.0, "AG0": 100.5}
        assert failed == {"CU0", "AU0"}

    def test_quote_exception(self, mocker):
        """fetch_quote 抛异常 → 计入失败集合。"""
        mock_source = mocker.MagicMock()
        mock_source.is_available.return_value = True
        mock_source.fetch_quote.side_effect = RuntimeError("boom")
        mocker.patch("fts.data_sources.tdx_local_source.TdxLocalSource", return_value=mock_source)
        prices, failed = _try_tq_realtime(["RB0"])
        assert prices == {}
        assert failed == {"RB0"}


class TestTryAkshareRealtime:
    def test_import_missing(self, mocker):
        """akshare 未安装 → 空 dict。"""
        mocker.patch.dict(sys.modules, {"akshare": None})
        assert _try_akshare_realtime(["RB0"]) == {}

    def test_hit_and_skip_empty(self, mocker):
        """正常解析 close，空 DataFrame 跳过。"""
        df = pd.DataFrame({"close": [3000.0, 3010.0]})
        mocker.patch("akshare.futures_zh_minute_sina", side_effect=[df, pd.DataFrame()])
        result = _try_akshare_realtime(["RB0", "CU0"])
        assert result == {"RB0": 3010.0}

    def test_exception_skipped(self, mocker):
        """接口异常跳过，不抛。"""
        mocker.patch("akshare.futures_zh_minute_sina", side_effect=RuntimeError("boom"))
        assert _try_akshare_realtime(["RB0"]) == {}


class TestGetRealtimePrices:
    def test_tq_only(self, mocker):
        """TQ 全部成功，无 AKShare 降级。"""
        mocker.patch(
            "fts.data_futures._try_tq_realtime",
            return_value=({"RB0": 3500.0}, set()),
        )
        result = get_realtime_prices(["RB0"])
        assert result == {"RB0": 3500.0}

    def test_akshare_fallback(self, mocker):
        """TQ 失败品种由 AKShare 补全。"""
        mocker.patch(
            "fts.data_futures._try_tq_realtime",
            return_value=({"RB0": 3500.0}, {"CU0"}),
        )
        mocker.patch(
            "fts.data_futures._try_akshare_realtime",
            return_value={"CU0": 4000.0},
        )
        result = get_realtime_prices(["RB0", "CU0"])
        assert result == {"RB0": 3500.0, "CU0": 4000.0}

    def test_default_symbols_all_fail(self, mocker):
        """symbols=None 使用 FUTURES_SUBSET，全部失败返回空。"""
        mocker.patch(
            "fts.data_futures._try_tq_realtime",
            return_value=({}, set(FUTURES_SUBSET)),
        )
        mocker.patch("fts.data_futures._try_akshare_realtime", return_value={})
        assert get_realtime_prices() == {}


# ═══════════════════════════════════════════════════════════
# 13. get_dominant_contracts / _fetch_dominant_akshare 补充
# ═══════════════════════════════════════════════════════════


class TestDominantContractsExtra:
    def test_empty_symbols(self):
        """空 symbols → 空 dict。"""
        assert get_dominant_contracts([]) == {}

    def test_default_symbols_uses_subset(self, mocker):
        """symbols=None → 全量 FUTURES_SUBSET，全部无数据返回空串。"""
        mock_db = mocker.MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mocker.patch("fts.data_futures._get_reader", return_value=mock_db)
        mocker.patch("fts.data_futures._release_reader")
        mocker.patch("fts.data_futures._fetch_dominant_akshare", return_value={})
        result = get_dominant_contracts()
        assert len(result) == len(FUTURES_SUBSET)
        assert all(v == "" for v in result.values())


class TestFetchDominantAkshareExtra:
    def test_import_missing(self, mocker):
        """akshare 未安装 → 空 dict。"""
        mocker.patch.dict(sys.modules, {"akshare": None})
        assert _fetch_dominant_akshare(["RB0"]) == {}

    def test_unknown_symbol_skipped(self, mocker):
        """未知品种（不在名称映射）跳过，不调用接口。"""
        mock_ak = mocker.patch("akshare.futures_zh_realtime")
        assert _fetch_dominant_akshare(["XYZ0"]) == {}
        mock_ak.assert_not_called()

    def test_empty_realtime_skipped(self, mocker):
        """realtime 返回 None / 空 / 缺 symbol 列 → 跳过。"""
        mocker.patch("akshare.futures_zh_realtime", return_value=None)
        assert _fetch_dominant_akshare(["RB0"]) == {}

        mocker.patch(
            "akshare.futures_zh_realtime",
            return_value=pd.DataFrame({"symbol": []}),
        )
        assert _fetch_dominant_akshare(["RB0"]) == {}

    def test_no_concrete_contract_skipped(self, mocker):
        """只返回连续合约（无具体合约）→ 跳过。"""
        df = pd.DataFrame({"symbol": ["RB0"], "position": [100]})
        mocker.patch("akshare.futures_zh_realtime", return_value=df)
        assert _fetch_dominant_akshare(["RB0"]) == {}

    def test_interface_error_skipped(self, mocker):
        """接口异常 → 跳过不抛。"""
        mocker.patch("akshare.futures_zh_realtime", side_effect=RuntimeError("boom"))
        assert _fetch_dominant_akshare(["RB0"]) == {}


# ═══════════════════════════════════════════════════════════
# 14. get_futures_provider 单例
# ═══════════════════════════════════════════════════════════


class TestGetFuturesProvider:
    def test_singleton_same_instance(self, mocker):
        """重复调用返回同一实例。"""
        mocker.patch.object(FuturesDataProvider, "_init_default_aggregator")
        fut_mod._default_futures_provider = None
        p1 = get_futures_provider()
        p2 = get_futures_provider()
        assert p1 is p2
        assert fut_mod._default_futures_provider is p1
