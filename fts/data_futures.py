"""
fts.data_futures — 期货数据提供者

基于 DuckDB（data/fts_history.duckdb）的 kline_cache 表提供期货连续合约 OHLCV 数据。

数据源优先级:
    K 线主路径: DuckDB → TQ_LOCAL → TQ_PYTHON → AKShare → SYNTHETIC
    实时价路径: TQ_LOCAL → AKShare（降级）

数据流:
    因子引擎 → FTSDataProvider → FuturesDataProvider → DuckDB (kline_cache)
                                                     ↘ akshare 即时获取（降级）

期货特有字段:
    - hold: 持仓量（open interest），日线和分钟线均有
    - settle: 结算价（仅日线）

⚠️ VWAP 字段说明:
    vwap 字段的计算逻辑:
    - 精确 VWAP（有成交额时）: amount / volume
    - AKShare 路径（有 settle 时）: (H + L + C + settle) / 4
    - DuckDB 路径（无 settle 时）: (H + L + C) / 3（典型价格）
    settle 参与计算的思路是：结算价是期货交易所官方定价基准，比
    简单平均更贴近期货价格特征。最终信号质量由演化引擎的 IC/Sharpe 指标评判。

期货截面含义:
    横截面是"不同品种 × 同一日期"，可做跨品种因子（如跨商品动量、品种间强弱）。

HARNESS §契约优先: 数据接口通过本模块定义。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── DuckDB 路径 ───────────────────────────────────────────

_DUCKDB_PATH = Path(__file__).resolve().parent.parent / "data" / "fts_history.duckdb"


# ─── 异常 ──────────────────────────────────────────────────

class FuturesDataError(RuntimeError):
    """期货数据获取失败。"""


# ─── 重试装饰器 ───────────────────────────────────────────

def retry_on_conflict(
    max_retries: int = 3,
    delay: float = 0.1,
    backoff: float = 2.0,
) -> Callable:
    """DuckDB 写冲突自动重试装饰器。

    Args:
        max_retries: 最大重试次数
        delay: 初始重试间隔（秒）
        backoff: 退避倍数
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            import duckdb  # type: ignore[import-untyped]
            # duckdb 不同版本写冲突异常类名不一致（1.1.x 为 TransactionException），
            # 统一兼容获取，避免 AttributeError 掩盖原始异常导致重试失效。
            conflict_exc = (
                getattr(duckdb, "ConcurrentTransactionException", None)
                or getattr(duckdb, "TransactionException", None)
                or Exception
            )
            last_exc: Exception | None = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except conflict_exc as e:
                    last_exc = e
                    if attempt == max_retries - 1:
                        raise
                    wait = delay * (backoff ** attempt)
                    logger.debug(
                        "DuckDB 写冲突 (第 %d/%d 次)，等待 %.1fs 重试",
                        attempt + 1, max_retries, wait,
                    )
                    time.sleep(wait)
            raise last_exc  # type: ignore[misc]
        return wrapper
    return decorator


# ─── 异步写入队列 ─────────────────────────────────────────

class AsyncWriteQueue:
    """DuckDB 异步写入队列 — 将写入请求串行化。

    所有写入操作排队由单个 worker 协程顺序执行，
    避免多协程同时写入导致的 ConcurrentTransactionException。

    用法:
        queue = AsyncWriteQueue(conn)
        queue.start()
        await queue.execute("INSERT INTO kline_cache VALUES (?, ?)", [val1, val2])
        await queue.flush()
        await queue.stop()
    """

    def __init__(self, conn: Any, max_queue_size: int = 1000):
        """
        Args:
            conn: DuckDB 数据库连接
            max_queue_size: 队列最大长度（超过时阻塞）
        """
        self._conn = conn
        self._queue: asyncio.Queue[tuple[str, Optional[list], asyncio.Future]] = (
            asyncio.Queue(maxsize=max_queue_size)
        )
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._running = False

    def start(self) -> None:
        """启动后台 worker。"""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        """停止后台 worker（等待队列清空后退出）。"""
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def execute(self, sql: str, params: Optional[list] = None) -> Any:
        """异步执行 SQL 写入（排队等待）。

        Args:
            sql: SQL 语句
            params: 参数列表（可选）

        Returns:
            执行结果
        """
        future: asyncio.Future[Any] = asyncio.Future()
        await self._queue.put((sql, params, future))
        return await future

    async def _worker(self) -> None:
        """后台 worker — 串行执行队列中的写入请求。"""
        while self._running:
            try:
                sql, params, future = await asyncio.wait_for(
                    self._queue.get(), timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                if params:
                    result = self._conn.execute(sql, params)
                else:
                    result = self._conn.execute(sql)
                future.set_result(result)
            except Exception as e:
                future.set_exception(e)
            finally:
                self._queue.task_done()

    @property
    def queue_size(self) -> int:
        """当前队列中的待处理请求数。"""
        return self._queue.qsize()

    async def flush(self) -> None:
        """等待队列清空。"""
        await self._queue.join()


# ─── DuckDB 连接管理器 ──────────────────────────────────

class DuckDBConnection:
    """DuckDB 连接管理器 — 单连接 + 重试保护 + 可选异步写入队列。

    封装 DuckDB 连接，自动配置并发重试，并提供可选的异步写入队列
    用于高并发场景下的写入串行化。

    用法:
        db = DuckDBConnection(path)
        db.execute("SELECT * FROM kline_cache WHERE symbol = ?", ["RB"])

        # 异步写入（需启用 enable_async_queue=True）
        await db.async_execute("INSERT INTO kline_cache VALUES (?, ?)", [v1, v2])
        await db.stop_async()
    """

    def __init__(
        self,
        path: Path,
        concurrency_retries: int = 3,
        enable_async_queue: bool = False,
        max_async_queue_size: int = 1000,
    ):
        """
        Args:
            path: DuckDB 数据库文件路径
            concurrency_retries: 写冲突自动重试次数（0 表示禁用）
            enable_async_queue: 是否启用异步写入队列
            max_async_queue_size: 异步队列最大长度
        """
        self._path = path
        self._concurrency_retries = concurrency_retries
        self._conn: Any = None
        self._lock = threading.Lock()
        self._async_queue: Optional[AsyncWriteQueue] = None
        self._enable_async = enable_async_queue
        self._max_async_queue = max_async_queue_size

    def connect(self) -> Any:
        """获取或创建 DuckDB 连接。

        线程安全：首次调用时创建连接，后续复用。
        创建时自动尝试设置 lock_configuration，失败静默降级。

        Returns:
            DuckDB 连接对象
        """
        if self._conn is not None:
            return self._conn

        with self._lock:
            if self._conn is not None:
                return self._conn
            import duckdb  # type: ignore[import-untyped]
            self._conn = duckdb.connect(str(self._path))
            # 尝试设置锁配置避免死锁（旧版 DuckDB 不支持时静默降级）
            try:
                if self._concurrency_retries > 0:
                    self._conn.execute("SET lock_configuration = true")
            except Exception:
                logger.debug("DuckDB lock_configuration 不可用，使用应用层重试")
            logger.info(
                "DuckDB 连接已建立: %s (lock_configuration=true, "
                "app_retry=%d)",
                self._path, self._concurrency_retries,
            )
            if self._enable_async:
                self._async_queue = AsyncWriteQueue(
                    self._conn, max_queue_size=self._max_async_queue,
                )
                self._async_queue.start()
                logger.info("DuckDB 异步写入队列已启动")
        return self._conn

    @retry_on_conflict()
    def execute(self, sql: str, params: Optional[list] = None) -> Any:
        """同步执行 SQL（带写冲突自动重试）。

        Args:
            sql: SQL 语句
            params: 参数列表（可选）

        Returns:
            执行结果
        """
        conn = self.connect()
        if params:
            return conn.execute(sql, params)
        return conn.execute(sql)

    async def async_execute(self, sql: str, params: Optional[list] = None) -> Any:
        """异步执行 SQL（通过写入队列串行化，避免并发冲突）。

        需要启用 enable_async_queue=True。

        Args:
            sql: SQL 语句
            params: 参数列表（可选）

        Returns:
            执行结果
        """
        if not self._enable_async:
            raise RuntimeError(
                "异步写入队列未启用（构造时设置 enable_async_queue=True）"
            )
        self.connect()  # 确保连接已建立
        assert self._async_queue is not None
        return await self._async_queue.execute(sql, params)

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
                logger.info("DuckDB 连接已关闭")

    async def stop_async(self) -> None:
        """停止异步写入队列（等待队列清空后关闭 worker）。"""
        if self._async_queue:
            await self._async_queue.flush()
            await self._async_queue.stop()
            self._async_queue = None
            logger.info("DuckDB 异步写入队列已停止")


# ─── 模块级 DuckDB 连接（兼容现有 _get_db() 调用方）───────

_DB: Optional[DuckDBConnection] = None


def _get_db() -> Any:
    """延迟获取 DuckDB 连接（兼容现有调用方）。

    返回 DuckDB 原生连接对象（.execute() 可用），
    但底层使用 DuckDBConnection 管理重试和生命周期。
    """
    global _DB  # pylint: disable=global-statement
    if _DB is None:
        try:
            _DB = DuckDBConnection(_DUCKDB_PATH, concurrency_retries=3)
        except Exception as e:
            raise FuturesDataError(f"DuckDB 连接初始化失败: {e}") from e
    return _DB.connect()


# ─── 期货数据提供者 ───────────────────────────────────────

class FuturesDataProvider:
    """期货数据提供者 — 基于 DuckDB kline_cache 表。

    数据源优先级:
        1. DuckDB kline_cache（连续合约，已持久化）
        2. TQ-Local 通达信本地客户端（HTTP 7721）
        3. AKShare 即时获取（futures_zh_daily_sina API）
        4. 合成数据降级（保证系统可运行）

    用法:
        provider = FuturesDataProvider()
        df = provider.get_ohlcv("RB0", days=500)
        panel, dates = provider.get_futures_panel(["RB0", "CU0", "AU0"], days=500)
    """

    def __init__(self, use_akshare_fallback: bool = True,
                 aggregator: Optional[Any] = None):
        """
        Args:
            use_akshare_fallback: 是否在 DuckDB 无数据时尝试 AKShare 即时获取。
            aggregator: FuturesDataAggregator 实例；None 时惰性初始化默认聚合器。
        """
        self._use_akshare = use_akshare_fallback
        self._aggregator = aggregator
        if self._aggregator is None:
            self._init_default_aggregator()

    def _init_default_aggregator(self) -> None:
        """惰性初始化默认 FuturesDataAggregator（按需导入 + 探活）。

        v2.30.0: 分钟数据源也在此初始化（TDXMinuteSource → TQLocalSource → TQSDKSource）。
        """
        try:
            from fts.data_sources.aggregator import FuturesDataAggregator
            from fts.data_sources.tq_source import TQLocalSource

            sources: list = []
            try:
                tq = TQLocalSource()
                # 不在这探活 — 让 aggregator 的熔断器管理失败状态
                sources.append(tq)
            except Exception:
                logger.debug("TQLocalSource 实例化失败，跳过")

            # ── 分钟数据源（v2.30.0）──
            minute_sources: list = []
            try:
                from fts.data_sources.tdx_minute_source import TDXMinuteSource
                minute_sources.append(TDXMinuteSource())
            except Exception:
                logger.debug("TDXMinuteSource 初始化失败，跳过分钟源")

            try:
                minute_sources.append(TQLocalSource(period="5m"))
            except Exception:
                logger.debug("TQLocalSource(5m) 初始化失败，跳过")

            try:
                from fts.data_sources.tqsdk_source import TQSDKSource
                minute_sources.append(TQSDKSource(period="5m"))
            except Exception:
                logger.debug("TQSDKSource 初始化失败，跳过")

            # ── tick 逐笔数据源（v2.31.0）──
            tick_sources: list = []
            try:
                from fts.data_sources.tqsdk_tick_source import TQSDKTickSource
                tick_sources.append(TQSDKTickSource())
            except Exception:
                logger.debug("TQSDKTickSource 初始化失败，跳过 tick 源")

            db_path = _DUCKDB_PATH if _DUCKDB_PATH.exists() else None
            self._aggregator = FuturesDataAggregator(
                sources=sources, enhancers=[],
                minute_sources=minute_sources,
                tick_sources=tick_sources,
                db_path=db_path,
                cache_max_age_days=30,
            )
            logger.info("FuturesDataAggregator 初始化完成（源数=%d, 分钟源数=%d, tick 源数=%d）",
                        len(sources), len(minute_sources), len(tick_sources))
        except Exception as e:
            logger.warning("FuturesDataAggregator 初始化失败: %s（降级到直接路径）", e)
            self._aggregator = None

    @staticmethod
    def _from_aggregator_df(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """将 FuturesDataAggregator（17 列）输出转换为 FuturesDataProvider 格式。"""
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        return df[["open", "high", "low", "close", "volume", "vwap", "hold", "settle"]]

    # ── 单标的 OHLCV ──

    def get_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取期货连续合约 OHLCV 日 K 线数据。

        Args:
            symbol: 期货连续合约代码（如 "RB0" / "CU0" / "IF0"）。
            days: 回溯天数。
            trace_id: HARNESS trace_id。

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume, hold, settle
            Index: DatetimeIndex

        Raises:
            FuturesDataError: 所有数据源不可用
        """
        # 0. 尝试 FuturesDataAggregator（统一路由引擎，含熔断器 + 缓存 + 降级）
        if self._aggregator is not None:
            try:
                agg_df = self._aggregator.get_ohlcv(symbol, days, trace_id)
                if agg_df is not None and not agg_df.empty:
                    source = agg_df.get("source", "").iloc[0] if "source" in agg_df.columns else "?"
                    # 合成数据降级 → 继续尝试 AKShare
                    if source == "SYNTHETIC":
                        logger.debug("Aggregator 返回合成数据 [%s]，尝试 AKShare 直连", symbol)
                    else:
                        logger.info("Aggregator 命中 [%s][%s] %d 行", symbol, source, len(agg_df))
                        return self._from_aggregator_df(agg_df, symbol)
            except Exception as e:
                logger.warning("Aggregator 获取失败 [%s]: %s，降级到直接路径", symbol, e)

        # 1. DuckDB kline_cache
        try:
            df = self._from_kline_cache(symbol, days)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug(f"DuckDB kline_cache 获取失败 [{symbol}]: {e}")

        # 2. TQ-Local 通达信本地客户端（HTTP 7721）
        try:
            df = self._from_tq_local(symbol, days)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug(f"TQ-Local 获取失败 [{symbol}]: {e}")

        # 3. AKShare 即时获取
        if self._use_akshare:
            try:
                df = self._from_akshare(symbol, days)
                if df is not None and not df.empty:
                    return df
            except Exception as e:
                logger.debug(f"AKShare 获取失败 [{symbol}]: {e}")

        # 4. 合成数据降级
        logger.warning(f"使用合成数据回退 [期货 {symbol}]")
        return self.synthesize_ohlcv(n_days=days, base_price=3000.0, seed=42)

    # ── 分钟级 OHLCV（v2.30.0）──

    def get_minute_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        frequency: str = "5m",
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取期货分钟级 K 线数据。

        通过 FuturesDataAggregator.get_minute_ohlcv() 获取，
        经 4 级降级链（minute_cache → TDX 17709 → TQ-Local 7721 → TQSDK）。

        Args:
            symbol: 期货连续合约代码（如 "RB0"）。
            days: 回溯 K 线根数。
            frequency: 分钟频率，支持 "1m" / "5m" / "15m" / "30m" / "60m"。
            trace_id: HARNESS trace_id。

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume
            Index: DatetimeIndex（从 datetime 列设置）
            所有源失败时返回空 DataFrame。
        """
        if self._aggregator is not None:
            try:
                agg_df = self._aggregator.get_minute_ohlcv(
                    symbol, days, frequency, trace_id,
                )
                if agg_df is not None and not agg_df.empty:
                    df = agg_df.copy()
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df.set_index("datetime", inplace=True)
                    df.sort_index(inplace=True)
                    logger.info(
                        "分钟数据命中 [%s][%s] 频率=%s %d 行",
                        symbol, agg_df.get("source", "").iloc[0]
                        if "source" in agg_df.columns else "?", frequency, len(df),
                    )
                    return df[["open", "high", "low", "close", "volume"]]
            except Exception as e:
                logger.warning("分钟数据聚合器获取失败 [%s]: %s", symbol, e)

        logger.warning("分钟数据所有源失败 [%s] frequency=%s", symbol, frequency)
        return pd.DataFrame()

    # ── tick 逐笔数据（v2.31.0）──

    def get_tick_data(
        self,
        symbol: str,
        count: int = 5000,
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取期货 tick 逐笔数据（含 5 档盘口）。

        通过 FuturesDataAggregator.get_ticks() 获取，
        降级链: tick_cache → TQSDKTickSource。

        Args:
            symbol: 期货连续合约代码（如 "RB0"）。
            count: tick 行数（TQSDK 免费账号上限 5000）。
            trace_id: HARNESS trace_id。

        Returns:
            含 tick schema 的 DataFrame（datetime 为索引，last_price/盘口等列），
            所有源失败时返回空 DataFrame。
        """
        if self._aggregator is not None:
            try:
                agg_df = self._aggregator.get_ticks(symbol, count, trace_id)
                if agg_df is not None and not agg_df.empty:
                    df = agg_df.copy()
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df.set_index("datetime", inplace=True)
                    df.sort_index(inplace=True)
                    src = agg_df.get("source", "").iloc[0] if "source" in agg_df.columns else "?"
                    logger.info("tick 数据命中 [%s][%s] %d 行", symbol, src, len(df))
                    return df
            except Exception as e:
                logger.warning("tick 数据聚合器获取失败 [%s]: %s", symbol, e)

        logger.warning("tick 数据所有源失败 [%s]", symbol)
        return pd.DataFrame()

    # ── 批量面板数据 ──

    def get_futures_panel(
        self,
        symbols: list[str],
        days: int = 500,
        trace_id: str = "",
    ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取多个期货品种的 OHLCV 面板数据。

        Returns:
            (panel, common_dates)
            panel: dict[symbol, OHLCV DataFrame]（含 hold/settle 列）
            common_dates: 多数品种共有日期（至少 max(2, 品种数//2) 个品种）

        说明:
            早期版本使用「全品种日期交集」，当品种扩展至全量（76 个商品期货）
            时，个别停更品种（WH0/JR0/RI0/LR0 数据止于 2022-2023）会令交集
            为空，导致下游横截面方向校正（截面 IC 法）静默失效。
            现改为「多数对齐」：保留至少一半品种共有的日期。
        """
        from collections import Counter

        panel: dict[str, pd.DataFrame] = {}
        date_counts: Counter[str] = Counter()

        for sym in symbols:
            try:
                df = self.get_ohlcv(sym, days=days, trace_id=trace_id)
                if df is not None and not df.empty and "close" in df.columns:
                    panel[sym] = df
                    date_counts.update(set(df.index))
            except Exception:  # noqa: BLE001
                continue

        if not panel:
            logger.warning("所有期货品种数据获取失败，使用合成数据")
            df = self.synthesize_ohlcv(n_days=days, base_price=3000.0, seed=42)
            panel["SYNTHETIC"] = df
            return panel, df.index

        # 多数对齐：至少 max(2, 品种数//2) 个品种共有的日期
        min_syms = max(2, len(panel) // 2)
        common_dates = pd.DatetimeIndex(sorted(
            d for d, c in date_counts.items() if c >= min_syms
        ))
        return panel, common_dates

    # ── DuckDB 读取 ──

    def _from_kline_cache(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """从 DuckDB kline_cache 表读取连续合约数据。

        kline_cache 表结构:
            symbol: 品种代码（如 "RB"）
            period: 周期（如 "daily"）
            date: 日期字符串
            open/high/low/close: 价格
            volume: 成交量
            amount: 成交额

        Args:
            symbol: 期货代码（支持 "RB0" / "RB" 两种格式）
            days: 回溯天数

        Returns:
            OHLCV DataFrame（含 hold/settle 列，DuckDB 无持仓量时设为 NaN）
        """
        db = _get_db()

        # 标准化: 去掉末尾的 "0" 连续合约标记
        raw = symbol.strip().upper()
        sym = raw[:-1] if raw.endswith("0") else raw

        # 查询 kline_cache
        # vwap: amount/volume（精确 VWAP，amount 有效时），否则用典型价格 (H+L+C)/3。
        # kline_cache 无 settle 列，故用 (H+L+C)/3 而非 (H+L+C+settle)/4。
        result = db.execute(
            "SELECT date, open, high, low, close, volume, amount, "
            "  CASE WHEN amount > 0 AND volume > 0 THEN amount / volume "
            "       ELSE (high + low + close) / 3.0 END AS vwap "
            "FROM kline_cache WHERE symbol = ? AND period = 'daily' "
            "ORDER BY date DESC LIMIT ?",
            [sym, days],
        )
        rows = result.fetchall()
        if not rows:
            return None

        df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount", "vwap"])
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)

        # 添加期货特有字段（kline_cache 无 hold/settle 字段，使用代理值）
        # settle 代理：(H+L+C)/3 —— 与 vwap 回退公式保持一致，业内典型做法
        # hold 代理：20 日滚动均量（反映资金关注度持续性，因子代码中需注意此为代理）
        df["settle"] = (df["high"] + df["low"] + df["close"]) / 3.0
        df["hold"] = df["volume"].rolling(window=20, min_periods=1).mean()

        # 标准列顺序
        return df[["open", "high", "low", "close", "volume", "vwap", "hold", "settle"]]

    # ── TQ-Local 通达信本地客户端 ──

    def _from_tq_local(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """从通达信 TQ-Local HTTP 服务获取 K 线数据。

        TQ-Local 默认端口 7721，协议 JSON-RPC 2.0。
        失败时返回 None（不抛异常，供上层降级）。

        Args:
            symbol: 期货连续合约代码（如 "RB0"）
            days: 回溯天数

        Returns:
            OHLCV DataFrame（含 hold/settle 列），或 None
        """
        try:
            from fts.data_sources.tq_source import TQLocalSource
            source = TQLocalSource()
            if not source.is_available():
                logger.warning("TQ-Local 服务不可达 (127.0.0.1:7721)，降级到 AKShare")
                return None
            df = source.fetch_ohlcv(symbol, days)
            if df is None or df.empty:
                return None
            # 标准化为 FuturesDataProvider 输出格式
            df["date"] = pd.to_datetime(df["date"])
            df.set_index("date", inplace=True)
            df.sort_index(inplace=True)
            if "close" not in df.columns:
                return None
            return df[["open", "high", "low", "close", "volume", "vwap", "hold", "settle"]]
        except ImportError:
            logger.warning("TQLocalSource 不可用（依赖缺失），降级到 AKShare")
            return None
        except Exception:
            logger.warning("TQ-Local 获取异常，降级到 AKShare", exc_info=True)
            return None

    # ── AKShare 即时获取 ──

    def _from_akshare(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """从 AKShare futures_zh_daily_sina 即时获取数据。

        AKShare 返回字段:
            date, open, high, low, close, volume, hold, settle

        Args:
            symbol: 期货连续合约代码（如 "RB0"）
            days: 回溯天数

        Returns:
            OHLCV DataFrame（含 hold/settle 列）
        """
        try:
            import akshare as ak  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("akshare 未安装，无法即时获取期货数据")
            return None

        # 确保 symbol 是 "RB0" 格式（连续合约）
        sym = symbol.strip().upper()
        if not sym.endswith("0"):
            sym = f"{sym}0"

        try:
            df = ak.futures_zh_daily_sina(symbol=sym)
        except Exception as e:
            raise FuturesDataError(f"AKShare 获取失败 [{sym}]: {e}") from e

        if df is None or df.empty:
            return None

        # 重命名列: AKShare 返回的列名是中文或英文
        # 实际返回: date, open, high, low, close, volume, hold, settle
        col_map = {
            "hold": "hold",
            "settle": "settle",
        }
        # 确保必要列存在
        required = ["date", "open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                return None

        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)

        # 只保留需要的列
        cols = ["open", "high", "low", "close", "volume"]
        extra_cols = ["hold", "settle"]
        for c in extra_cols:
            if c in df.columns:
                cols.append(c)
            else:
                df[c] = np.nan
                cols.append(c)

        # vwap: AKShare 有 settle，用 (H+L+C+settle)/4 作为期货定价基准的近似。
        df["vwap"] = (df["high"] + df["low"] + df["close"] + df["settle"]) / 4.0
        cols.append("vwap")

        # 限制天数
        if len(df) > days:
            df = df.iloc[-days:]

        return df[cols]

    # ── 合成数据降级 ──

    @staticmethod
    def synthesize_ohlcv(
        n_days: int = 500,
        base_price: float = 3000.0,
        seed: int = 42,
    ) -> pd.DataFrame:
        """合成期货 OHLCV 数据（网络不可用时的降级回退）。

        Args:
            n_days: 天数
            base_price: 起始价格
            seed: 随机种子

        Returns:
            OHLCV DataFrame（含 hold/settle 列）
        """
        np.random.seed(seed)
        dates = pd.date_range(
            datetime.now() - timedelta(days=n_days),
            periods=n_days, freq="D",
        )
        close = base_price + np.cumsum(np.random.randn(n_days) * 30)
        close = np.maximum(close, base_price * 0.5)  # 防止负价格
        hold = np.random.randint(100000, 1000000, n_days).astype(float)
        high = close + np.abs(np.random.randn(n_days)) * 30
        low = close - np.abs(np.random.randn(n_days)) * 30
        return pd.DataFrame({
            "open": close + np.random.randn(n_days) * 10,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.random.randint(10000, 500000, n_days).astype(float),
            "hold": hold,
            "settle": close + np.random.randn(n_days) * 5,
            "vwap": (high + low + close + (close + np.random.randn(n_days) * 5)) / 4.0,
        }, index=dates)


# ─── 期货品种子集（82 个连续合约）───────────────────────────

# 来自 AKShare futures_display_main_sina() 的完整列表
FUTURES_SUBSET: list[str] = [
    # 大商所 (dce) — 22 个
    "V0", "P0", "B0", "M0", "I0", "JD0", "L0", "PP0", "FB0",
    "Y0", "C0", "A0", "J0", "JM0", "CS0", "EG0", "RR0", "EB0",
    "PG0", "LH0", "LG0", "BZ0",
    # 郑商所 (czce) — 25 个
    "TA0", "OI0", "RS0", "RM0", "WH0", "JR0", "SR0", "CF0",
    "RI0", "MA0", "FG0", "LR0", "SF0", "SM0", "CY0", "AP0",
    "CJ0", "UR0", "SA0", "PF0", "PK0", "SH0", "PX0", "PR0", "PL0",
    # 上期所 (shfe) — 19 个
    "FU0", "AL0", "RU0", "ZN0", "CU0", "AU0", "RB0", "PB0",
    "AG0", "BU0", "HC0", "SN0", "NI0", "SP0", "SS0", "AO0",
    "BR0", "AD0", "OP0",
    # 能源中心 (ine) — 5 个
    "SC0", "NR0", "LU0", "BC0", "EC0",
    # 中金所 (cffex) — 6 个
    "IF0", "TF0", "IH0", "IC0", "TS0", "IM0",
    # 广期所 (gfex) — 5 个
    "SI0", "LC0", "PS0", "PT0", "PD0",
]

# 常用期货品种子集（流动性好的品种，用于快速测试）
FUTURES_CORE_SUBSET: list[str] = [
    "RB0",  # 螺纹钢
    "CU0",  # 铜
    "AU0",  # 黄金
    "AG0",  # 白银
    "I0",   # 铁矿石
    "M0",   # 豆粕
    "TA0",  # PTA
    "MA0",  # 甲醇
    "SC0",  # 原油
    "HC0",  # 热卷
    "NI0",  # 镍
    "SN0",  # 锡
    "P0",   # 棕榈油
    "Y0",   # 豆油
    "C0",   # 玉米
    "A0",   # 豆一
    "CF0",  # 棉花
    "SR0",  # 白糖
    "SA0",  # 纯碱
    "IF0",  # 沪深300股指
    "IC0",  # 中证500股指
    "IH0",  # 上证50股指
    "IM0",  # 中证1000股指
    "LC0",  # 碳酸锂
    "SI0",  # 工业硅
]


# 盲测品种池（不参与演化训练，用于验证因子泛化能力）
# 从 FUTURES_SUBSET 中选取，覆盖不同产业链类别
FUTURES_HOLDOUT: list[str] = [
    "JD0",  # 鸡蛋 — 大商所，农产品
    "AP0",  # 苹果 — 郑商所，农产品
    "FG0",  # 玻璃 — 郑商所，建材
    "AL0",  # 铝 — 上期所，有色金属
    "UR0",  # 尿素 — 郑商所，化工
    "NR0",  # 20号胶 — 能源中心，能源化工
]


# ─── 产业链分类映射（用于分层训练集选择）────────────────────

FUTURES_SECTOR_MAP: dict[str, list[str]] = {
    "黑色系": [
        "I0", "RB0", "HC0", "SS0",   # 钢铁产业链
        "J0", "JM0",                   # 焦煤焦炭
        "SF0", "SM0",                  # 铁合金
    ],
    "有色金属": [
        "CU0", "AL0", "ZN0", "PB0", "SN0", "NI0",  # 基本金属（含铝 AL0）
        "BC0", "AO0", "AD0",                       # 铜/铝衍生
    ],
    "能源": [
        "SC0", "FU0", "LU0", "BU0",           # 原油/燃料油/沥青
    ],
    "聚酯链": [
        "PX0", "TA0", "PF0", "PR0", "EG0",   # 对二甲苯→PTA→聚酯(短纤/瓶片), 乙二醇
    ],
    "油化工": [
        "L0", "PP0", "V0", "PG0",            # 聚乙烯/聚丙烯/聚氯乙烯/液化气
        "EB0", "BZ0", "PL0",                 # 苯乙烯/苯/丙烯
    ],
    "煤化工": [
        "MA0", "SA0", "UR0", "FG0", "SH0",   # 甲醇/纯碱/尿素/玻璃/烧碱
    ],
    "橡胶": [
        "RU0", "NR0", "BR0",                  # 天然橡胶/20号胶/丁二烯橡胶
    ],
    "造纸/林浆纸": [
        "SP0", "LG0", "FB0", "OP0",          # 纸浆/原木/纤维板/双胶纸（林浆纸一体化：木材→纸浆→纸品）
    ],
    "航运": [
        "EC0",                                # 集运欧线（航运运价，独立于商品产业链）
    ],
    "农产品": [
        "C0", "A0", "B0", "M0", "Y0", "P0",   # 大豆/玉米/油脂
        "CS0", "RR0", "LH0",                  # 淀粉/生猪
        "OI0", "RS0", "RM0",                    # 菜籽/菜粕
        "SR0", "CF0", "CY0",                    # 白糖/棉花/棉纱
        "WH0", "JR0", "RI0", "LR0",             # 谷物
        "JD0", "AP0", "CJ0", "PK0",             # 软商品/果蔬
    ],
    "贵金属": [
        "AU0", "AG0", "PT0", "PD0",        # 黄金/白银/铂/钯（铂族金属同属贵金属板块）
    ],
    "新能源/新材料": [
        "LC0", "SI0", "PS0",
    ],
    "金融期货": [
        "IF0", "IC0", "IH0", "IM0", "TF0", "TS0",
    ],
}


# 分层训练品种集（按产业链从每组中选取 2-3 个代表品种，
# 确保训练集覆盖所有产业链类别，排除盲测品种池）
FUTURES_STRATIFIED_SUBSET: list[str] = [
    # 黑色系
    "RB0", "I0", "J0",
    # 有色金属
    "CU0", "ZN0", "NI0",
    # 能源 → 原油/燃料油/沥青
    # 聚酯链 → PX→PTA→聚酯
    # 油化工 → 石脑油裂解下游
    # 煤化工 → 煤基化工品
    # 橡胶 → 天然/合成橡胶
    # 造纸/林浆纸 → 林浆纸一体化（纸浆/原木/纤维板）
    # 航运 → 集运欧线
    "TA0", "MA0", "SC0",
    # 农产品
    "M0", "C0", "SR0",
    # 贵金属
    "AU0", "AG0",
    # 新能源/新材料
    "LC0", "SI0",
    # 金融期货
    "IF0", "IC0", "IH0",
]


# ─── 品种中文名称映射（FUTURES_SUBSET 全量）─────────────────

FUTURES_SYMBOL_NAMES: dict[str, str] = {
    # 大商所 (dce)
    "V0": "聚氯乙烯", "P0": "棕榈油", "B0": "豆二", "M0": "豆粕",
    "I0": "铁矿石", "JD0": "鸡蛋", "L0": "聚乙烯", "PP0": "聚丙烯",
    "FB0": "纤维板", "Y0": "豆油", "C0": "玉米", "A0": "豆一",
    "J0": "焦炭", "JM0": "焦煤", "CS0": "玉米淀粉", "EG0": "乙二醇",
    "RR0": "粳米", "EB0": "苯乙烯", "PG0": "液化石油气", "LH0": "生猪",
    "LG0": "原木", "BZ0": "苯",
    # 郑商所 (czce)
    "TA0": "PTA", "OI0": "菜籽油", "RS0": "菜籽", "RM0": "菜粕",
    "WH0": "强麦", "JR0": "粳稻", "SR0": "白糖", "CF0": "棉花",
    "RI0": "早籼稻", "MA0": "甲醇", "FG0": "玻璃", "LR0": "晚籼稻",
    "SF0": "硅铁", "SM0": "锰硅", "CY0": "棉纱", "AP0": "苹果",
    "CJ0": "红枣", "UR0": "尿素", "SA0": "纯碱", "PF0": "短纤",
    "PK0": "花生", "SH0": "烧碱", "PX0": "对二甲苯", "PR0": "瓶片",
    "PL0": "丙烯",
    # 上期所 (shfe)
    "FU0": "燃料油", "AL0": "铝", "RU0": "橡胶", "ZN0": "锌",
    "CU0": "铜", "AU0": "黄金", "RB0": "螺纹钢", "PB0": "铅",
    "AG0": "白银", "BU0": "沥青", "HC0": "热轧卷板", "SN0": "锡",
    "NI0": "镍", "SP0": "纸浆", "SS0": "不锈钢", "AO0": "氧化铝",
    "BR0": "丁二烯橡胶", "AD0": "铸造铝合金", "OP0": "胶版印刷纸",
    # 能源中心 (ine)
    "SC0": "原油", "NR0": "20号胶", "LU0": "低硫燃料油",
    "BC0": "国际铜", "EC0": "集运欧线",
    # 中金所 (cffex)
    "IF0": "沪深300", "TF0": "5年期国债", "IH0": "上证50",
    "IC0": "中证500", "TS0": "2年期国债", "IM0": "中证1000",
    # 广期所 (gfex)
    "SI0": "工业硅", "LC0": "碳酸锂", "PS0": "多晶硅",
    "PT0": "铂", "PD0": "钯",
}


# FTS 连续合约代码 → AKShare futures_symbol_mark 中文名（用于实时行情查询）
_SYMBOL_MARK_NAMES: dict[str, str] = {
    # 大商所
    "V0": "PVC", "P0": "棕榈", "B0": "豆二", "M0": "豆粕", "I0": "铁矿石",
    "JD0": "鸡蛋", "L0": "塑料", "PP0": "PP", "FB0": "纤维板", "Y0": "豆油",
    "C0": "玉米", "A0": "豆一", "J0": "焦炭", "JM0": "焦煤", "CS0": "玉米淀粉",
    "EG0": "乙二醇", "RR0": "粳米", "EB0": "苯乙烯", "PG0": "液化石油气",
    "LH0": "生猪", "LG0": "原木", "BZ0": "纯苯",
    # 郑商所
    "TA0": "PTA", "OI0": "菜油", "RS0": "菜籽", "RM0": "菜粕", "WH0": "强麦",
    "JR0": "粳稻", "SR0": "白糖", "CF0": "棉花", "RI0": "早籼稻", "MA0": "郑醇",
    "FG0": "玻璃", "LR0": "晚籼稻", "SF0": "硅铁", "SM0": "锰硅", "CY0": "棉纱",
    "AP0": "鲜苹果", "CJ0": "红枣", "UR0": "尿素", "SA0": "纯碱", "PF0": "短纤",
    "PK0": "花生", "SH0": "烧碱", "PX0": "二甲苯", "PR0": "瓶级聚酯切片",
    "PL0": "丙烯",
    # 上期所
    "FU0": "燃油", "AL0": "沪铝", "RU0": "橡胶", "ZN0": "沪锌", "CU0": "沪铜",
    "AU0": "黄金", "RB0": "螺纹钢", "PB0": "沪铅", "AG0": "白银", "BU0": "沥青",
    "HC0": "热轧卷板", "SN0": "沪锡", "NI0": "沪镍", "SP0": "纸浆",
    "SS0": "不锈钢", "AO0": "氧化铝", "BR0": "丁二烯橡胶",
    "AD0": "铸造铝合金期货", "OP0": "胶版印刷纸期货",
    # 能源中心
    "SC0": "原油", "NR0": "20号胶", "LU0": "低硫燃料油", "BC0": "国际铜",
    "EC0": "集运指数(欧线)期货",
    # 中金所
    "IF0": "沪深300指数期货", "TF0": "5年期国债期货", "IH0": "上证50指数期货",
    "IC0": "中证500指数期货", "TS0": "2年期国债期货",
    "IM0": "中证1000股指期货",
    # 广期所
    "SI0": "工业硅", "LC0": "碳酸锂", "PS0": "多晶硅", "PT0": "铂", "PD0": "钯",
}


def _fetch_dominant_akshare(symbols: list[str]) -> dict[str, str]:
    """通过 AKShare futures_zh_realtime 查询主力合约（持仓量最大具体合约）。

    Args:
        symbols: FTS 连续合约代码列表（如 ["RU0", "EC0"]）

    Returns:
        dict[symbol, contract]，查询失败的品种不包含在内
    """
    result: dict[str, str] = {}
    try:
        import akshare as ak  # type: ignore[import-untyped]
    except ImportError:
        return result

    for sym in symbols:
        name = _SYMBOL_MARK_NAMES.get(sym)
        if not name:
            continue
        try:
            df = ak.futures_zh_realtime(symbol=name)
            if df is None or df.empty or "symbol" not in df.columns:
                continue
            prefix = sym[:-1] if sym.endswith("0") else sym
            # 排除连续合约（如 RU0），取持仓量最大的具体合约（如 RU2609）
            concrete = df[
                df["symbol"].str.startswith(prefix)
                & (df["symbol"] != sym)
                & (df["symbol"].str.len() > len(prefix))
            ]
            if concrete.empty:
                continue
            top = concrete.sort_values("position", ascending=False).iloc[0]
            result[sym] = str(top["symbol"])
        except Exception:  # noqa: BLE001
            continue
    return result


def get_dominant_contracts(symbols: list[str] | None = None) -> dict[str, str]:
    """查询各品种主力合约代码（contract_kline 最新交易日最大成交量）。

    Args:
        symbols: 品种列表（如 ["RB0", "CU0"]），默认 FUTURES_SUBSET

    Returns:
        dict[symbol, contract]，如 {"RB0": "RB2610"}；无数据品种返回空串
    """
    if symbols is None:
        symbols = list(FUTURES_SUBSET)
    result: dict[str, str] = {s: "" for s in symbols}
    if not symbols:
        return result
    try:
        db = _get_db()
    except FuturesDataError:
        return result

    # contract_kline 中 symbol 无末尾 "0"（如 "RB"）
    base_syms = [s[:-1] if s.endswith("0") else s for s in symbols]
    placeholders = ",".join("?" * len(base_syms))
    rows = db.execute(
        f"""
        WITH ranked AS (
            SELECT symbol, contract,
                   ROW_NUMBER() OVER (
                       PARTITION BY symbol
                       ORDER BY date DESC, volume DESC
                   ) AS rn
            FROM contract_kline
            WHERE symbol IN ({placeholders}) AND period = 'daily'
        )
        SELECT symbol, contract FROM ranked WHERE rn = 1
        """,
        base_syms,
    ).fetchall()
    code2sym = {s[:-1] if s.endswith("0") else s: s for s in symbols}
    for base, contract in rows:
        sym = code2sym.get(base)
        if sym:
            result[sym] = contract

    # AKShare fallback：补全 DB 缺失品种的主力合约
    missing = [s for s in symbols if not result.get(s)]
    if missing:
        ak_map = _fetch_dominant_akshare(missing)
        for sym, contract in ak_map.items():
            result[sym] = contract
    return result


def _try_tq_realtime(symbols: list[str]) -> tuple[dict[str, float], set[str]]:
    """通过 TQ-Local HTTP 获取实时快照（主路径）。

    Returns:
        (成功价格字典, 失败品种集合)
    """
    prices: dict[str, float] = {}
    failed: set[str] = set()

    try:
        from fts.data_sources.tq_source import TQLocalSource
    except ImportError:
        return prices, set(symbols)

    tq = TQLocalSource()
    if not tq.is_available():
        logger.warning("TQ-Local 探活失败，跳过 TQ 实时路径")
        return prices, set(symbols)

    logger.info(f"[realtime] TQ-Local 探活成功，尝试获取 {len(symbols)} 个品种实时价")
    for sym in symbols:
        try:
            quote = tq.fetch_quote(sym, trace_id="realtime_price")
            if quote is None:
                failed.add(sym)
                continue
            price = _extract_quote_price(quote)
            if price is not None and price > 0:
                prices[sym] = price
            else:
                failed.add(sym)
        except Exception:  # noqa: BLE001
            failed.add(sym)

    logger.info(f"[realtime] TQ 成功 {len(prices)}/{len(symbols)} 品种")
    return prices, failed


def _extract_quote_price(quote: dict[str, Any]) -> Optional[float]:
    """从 TQ 实时快照中提取价格字段。

    TQ 协议返回字段名可能为 last_price / price / close / bid_price 等，
    按优先级逐一尝试。
    """
    for field in ("last_price", "price", "close", "bid_price", "current", "now"):
        val = quote.get(field)
        if val is not None:
            try:
                p = float(val)
                if p > 0:
                    return p
            except (TypeError, ValueError):
                continue
    return None


def _try_akshare_realtime(symbols: list[str]) -> dict[str, float]:
    """通过 AKShare 获取盘中实时价（降级路径）。

    使用 futures_zh_minute_sina 最新分时 close。
    """
    prices: dict[str, float] = {}
    try:
        import akshare as ak  # type: ignore[import-untyped]
    except ImportError:
        return prices

    for sym in symbols:
        try:
            df = ak.futures_zh_minute_sina(symbol=sym)
            if df is None or df.empty:
                continue
            price = float(df["close"].iloc[-1])
            if price > 0:
                prices[sym] = price
        except Exception:  # noqa: BLE001
            continue
    return prices


def get_realtime_prices(symbols: list[str] | None = None) -> dict[str, float]:
    """获取期货品种盘中实时价。

    数据源优先级（与 K 线主路径一致）:
        1. TQ_LOCAL — 通达信本地 HTTP 实时快照（tq_get_quote）
        2. AKSHARE — AKShare futures_zh_minute_sina 分时行情（降级）

    盘中实时价用于信号报告"最新价"展示；非交易时段返回当日最新分时价。

    Args:
        symbols: 品种列表（如 ["RB0", "CU0"]），默认 FUTURES_SUBSET

    Returns:
        dict[symbol, 实时价]；获取失败品种不包含在内
    """
    if symbols is None:
        symbols = list(FUTURES_SUBSET)

    # Path 1: TQ-Local 实时快照
    tq_prices, tq_failed = _try_tq_realtime(symbols)
    prices = dict(tq_prices)

    # Path 2: AKShare 降级（TQ 失败的品种）
    if tq_failed:
        ak_prices = _try_akshare_realtime(list(tq_failed))
        prices.update(ak_prices)
        if ak_prices:
            logger.info(
                f"[realtime] AKShare 降级补全 {len(ak_prices)} 个品种"
            )

    logger.info(
        f"[realtime] 最终覆盖 {len(prices)}/{len(symbols)} 品种 "
        f"(TQ:{len(tq_prices)} + AKShare:{len(prices)-len(tq_prices)})"
    )
    return prices


# ─── 缺省实例 ─────────────────────────────────────────────

_default_futures_provider: Optional[FuturesDataProvider] = None


def get_futures_provider() -> FuturesDataProvider:
    """获取全局 FuturesDataProvider 实例（惰性初始化）。"""
    global _default_futures_provider  # noqa: PLW0603
    if _default_futures_provider is None:
        _default_futures_provider = FuturesDataProvider()
    return _default_futures_provider


__all__ = [
    "DuckDBConnection",
    "AsyncWriteQueue",
    "retry_on_conflict",
    "FuturesDataProvider",
    "FuturesDataError",
    "get_futures_provider",
    "FUTURES_SUBSET",
    "FUTURES_CORE_SUBSET",
    "FUTURES_HOLDOUT",
    "FUTURES_SECTOR_MAP",
    "FUTURES_STRATIFIED_SUBSET",
    "FUTURES_SYMBOL_NAMES",
    "get_dominant_contracts",
    "get_realtime_prices",
    "_try_tq_realtime",
    "_try_akshare_realtime",
    "_extract_quote_price",
]