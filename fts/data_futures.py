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
from contextlib import contextmanager
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

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
                    wait = delay * (backoff**attempt)
                    logger.debug(
                        "DuckDB 写冲突 (第 %d/%d 次)，等待 %.1fs 重试",
                        attempt + 1,
                        max_retries,
                        wait,
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
        self._queue: asyncio.Queue[tuple[str, Optional[list], asyncio.Future]] = asyncio.Queue(maxsize=max_queue_size)
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
                    self._queue.get(),
                    timeout=1.0,
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
                "DuckDB 连接已建立: %s (lock_configuration=true, app_retry=%d)",
                self._path,
                self._concurrency_retries,
            )
            if self._enable_async:
                self._async_queue = AsyncWriteQueue(
                    self._conn,
                    max_queue_size=self._max_async_queue,
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
            raise RuntimeError("异步写入队列未启用（构造时设置 enable_async_queue=True）")
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


# ─── 单写者 / 只读连接池（E.1 并发模型根治）──────────────


class DuckDBWriter:
    """DuckDB 单写者 — 唯一可写连接，进程内写锁串行化。

    并发模型根治（design/E.1）核心组件:
      - 对同一 .duckdb 文件，任意时刻至多一个可写连接（单写者）
      - 进程内 `threading.Lock` 保证所有写操作串行，结构上消除写冲突
      - 批量写入（executemany/copy_from_records）降低 commit 频率，
        减少 checkpoint 对只读查询的阻塞

    用法:
        writer = DuckDBWriter(path)
        writer.execute("INSERT INTO t VALUES (?, ?)", [1, "a"])
        writer.executemany("INSERT INTO t VALUES (?, ?)", [(2, "b"), (3, "c")])
        writer.copy_from_records("t", ["id", "name"], [(4, "d"), (5, "e")])
        writer.close()
    """

    def __init__(self, path: Path, batch_size: int = 1000, commit_every: int = 100):
        """
        Args:
            path: DuckDB 数据库文件路径
            batch_size: copy_from_records 单批缓冲行数
            commit_every: 批量写入 commit 周期（秒）—— 保留参数，批量提交由调用方批次控制
        """
        self._path = Path(path)
        self._batch_size = batch_size
        self._commit_every = commit_every
        self._lock = threading.Lock()
        import duckdb  # type: ignore[import-untyped]

        self._conn: Any = duckdb.connect(str(self._path))
        # 启用 lock_configuration（DuckDB 1.1+ 单写多读），失败静默降级（旧版）
        try:
            self._conn.execute("SET lock_configuration = true")
        except Exception:
            logger.debug("DuckDB lock_configuration 不可用（旧版），使用应用层串行写锁")
        logger.info("DuckDBWriter 已建立: %s (batch_size=%d)", self._path, batch_size)

    def execute(self, sql: str, params: Optional[list] = None) -> Any:
        """带写锁执行单条 SQL（原子提交）。

        Args:
            sql: SQL 语句
            params: 参数列表（可选）

        Returns:
            执行结果
        """
        with self._lock:
            if params:
                return self._conn.execute(sql, params)
            return self._conn.execute(sql)

    def executemany(self, sql: str, seq_params: list[list] | list[tuple]) -> Any:
        """带写锁批量执行同一条 SQL（显式事务包裹，整批原子）。

        DuckDB 的 executemany 为逐条执行（非单事务），必须用
        BEGIN/COMMIT 包裹保证「整批成功或整批回滚」（E.1 §2.2）。

        Args:
            sql: SQL 语句
            seq_params: 参数序列

        Returns:
            执行结果

        Raises:
            任一条失败时整批回滚并抛出原始异常
        """
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                result = self._conn.executemany(sql, seq_params)
                self._conn.execute("COMMIT")
                return result
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    logger.debug("executemany 回滚失败: %s", self._path)
                raise

    def copy_from_records(self, table: str, columns: list[str], records: list[tuple]) -> None:
        """批量写入（带写锁 + 显式事务，整批原子）。

        等效于逐条 INSERT（数据一致），但单事务提交保证原子性，
        并降低 commit 频率，减少 checkpoint 对读连接的阻塞（E.1 §2.4）。

        Args:
            table: 目标表名
            columns: 目标列名列表
            records: 行元组列表（空列表为 no-op）
        """
        if not records:
            return
        placeholders = ",".join("?" * len(columns))
        col_sql = ",".join(columns)
        sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})"
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.executemany(sql, records)
                self._conn.execute("COMMIT")
            except Exception:
                try:
                    self._conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    logger.debug("copy_from_records 回滚失败: %s", self._path)
                raise

    def query(self, sql: str, params: Optional[list] = None) -> list[tuple]:
        """带锁查询（writer 连接内读，避免额外连接）。

        Args:
            sql: SQL 语句
            params: 参数列表（可选）

        Returns:
            查询结果行列表
        """
        with self._lock:
            cur = self._conn.execute(sql, params) if params else self._conn.execute(sql)
            return cur.fetchall()

    def close(self) -> None:
        """关闭连接（幂等）。"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
                logger.info("DuckDBWriter 已关闭: %s", self._path)


class DuckDBReader:
    """DuckDB 读连接池 — read_only 连接与写连接解耦。

    并发模型根治（design/E.1 + E.4 S1）核心组件:
      - 所有读操作走池内连接，连接为 `read_only=True`（零写语义，不持有写锁）
      - DuckDB 1.1+ `lock_configuration=true`（写连接已启用）下单进程内
        可写连接 + 只读连接可共存；跨进程写锁存在时只读连接打开可能短暂
        失败（写窗口已由 E.4 filelock 收敛至秒级，读侧由既有降级链兜底）
      - 读侧不参与写锁竞争，写提交期间读侧基于 MVCC 快照

    用法:
        reader = DuckDBReader(path, max_connections=4)
        con = reader.acquire()
        try:
            rows = con.execute("SELECT * FROM t").fetchall()
        finally:
            reader.release(con)
        reader.close()
    """

    def __init__(self, path: Path, max_connections: int = 4):
        """
        Args:
            path: DuckDB 数据库文件路径
            max_connections: 连接池最大容量
        """
        self._path = Path(path)
        self._max_connections = max_connections
        self._lock = threading.Lock()
        self._pool: list[Any] = []

    def acquire(self) -> Any:
        """获取一个连接（池内复用或新建，read_only=True）。

        Returns:
            DuckDB read_only 连接对象（写操作将被 DuckDB 拒绝）
        """
        with self._lock:
            if self._pool:
                return self._pool.pop()
            import duckdb  # type: ignore[import-untyped]

            return duckdb.connect(str(self._path), read_only=True)

    def release(self, conn: Any) -> None:
        """归还只读连接（池满时关闭）。

        Args:
            conn: 待归还的连接
        """
        with self._lock:
            if len(self._pool) < self._max_connections:
                self._pool.append(conn)
            else:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001 — 关闭失败不阻断
                    logger.debug("DuckDBReader 归还时关闭连接失败: %s", self._path)

    def close(self) -> None:
        """关闭池内所有连接（幂等）。"""
        with self._lock:
            for conn in self._pool:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    logger.debug("DuckDBReader 关闭连接失败: %s", self._path)
            self._pool.clear()
            logger.info("DuckDBReader 已关闭: %s (pool=%d)", self._path, self._max_connections)


# ─── 模块级 DuckDB 连接（E.4 S1：写短生命周期 + 读 read_only）──

_READER: Optional[DuckDBReader] = None


@contextmanager
def _write_scope(timeout: float = 30.0) -> Iterator[DuckDBWriter]:
    """写窗口 contextmanager（E.4 S1）：跨进程写锁 + 短生命周期写连接。

    写操作在 `duckdb_write_lock` 保护下以短连接执行；窗口结束（with 退出）
    即关闭连接并释放跨进程写锁——写锁存活从「进程级（小时）」降到「秒级」。

    Args:
        timeout: 跨进程写锁等待上限（秒），超时抛 TimeoutError

    Usage:
        with _write_scope() as writer:
            writer.execute("DELETE FROM t WHERE symbol = ?", [s])
            writer.executemany("INSERT INTO t VALUES (?, ?)", rows)
    """
    from fts.store import get_storage_registry
    from fts.store.duckdb_lock import duckdb_write_lock

    # GAP-150 写路径契约（严格模式）：生产默认行情库必须登记；
    # 测试 monkeypatch 替换路径时豁免（非默认即显式注入语义）
    _prod_default = Path(__file__).resolve().parent.parent / "data" / "fts_history.duckdb"
    if Path(_DUCKDB_PATH).resolve() == _prod_default.resolve():
        get_storage_registry().warn_unregistered_write(
            _DUCKDB_PATH, caller="data_futures", strict=True
        )

    with duckdb_write_lock(_DUCKDB_PATH, timeout=timeout):
        writer = DuckDBWriter(_DUCKDB_PATH)
        try:
            yield writer
        finally:
            writer.close()


def _get_writer() -> DuckDBWriter:
    """创建一次性短生命周期写连接（E.4 S1：不再全局常驻）。

    ⚠️ deprecated：新代码应使用 `_write_scope()`（filelock + 写完即关）。
    本函数仅为兼容旧调用方（脚本），返回的连接**必须在使用后 close()**，
    否则仍会持有写锁到进程结束。
    """
    return DuckDBWriter(_DUCKDB_PATH)


def _get_reader() -> Any:
    """获取读连接（E.4 S1：read_only=True，不持有写锁）。

    所有读操作走池内 read_only 连接，与写连接解耦。
    返回 DuckDB 原生连接对象（.execute()/.fetchall() 可用，写操作被拒），
    调用方完成后需调用 `_release_reader(conn)` 归还。

    Returns:
        DuckDB read_only 连接对象
    """
    global _READER  # pylint: disable=global-statement
    if _READER is None:
        try:
            pool_size = 4
            try:
                from fts.config.settings import get_config

                pool_size = get_config().duckdb_read_pool_size
            except Exception:  # noqa: BLE001
                pass
            _READER = DuckDBReader(_DUCKDB_PATH, max_connections=pool_size)
        except Exception as e:
            raise FuturesDataError(f"DuckDB 读连接池初始化失败: {e}") from e
    return _READER.acquire()


def _release_reader(conn: Any) -> None:
    """归还只读连接至池（配合 _get_reader 使用）。"""
    global _READER  # pylint: disable=global-statement
    if _READER is not None:
        _READER.release(conn)


def _get_db() -> Any:
    """延迟获取 DuckDB 连接（兼容读语义调用方，E.4 S1 改 read_only 短连接）。

    ⚠️ 兼容入口：返回 `read_only=True` 连接，**不持有写锁**；
    调用方使用后必须 `close()`。新代码用 `_get_reader()`/`_release_reader()`。
    """
    import duckdb  # type: ignore[import-untyped]

    return duckdb.connect(str(_DUCKDB_PATH), read_only=True)


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

    def __init__(self, use_akshare_fallback: bool = True, aggregator: Optional[Any] = None):
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

        v2.87.0: TQLocalSource(7721) 与 TDXMinuteSource(17709) 合并为 TdxLocalSource(17709)。
        """
        try:
            from fts.config.settings import get_config as _agg_cfg
            from fts.data_sources.aggregator import FuturesDataAggregator
            from fts.data_sources.tdx_local_source import TdxLocalSource

            sources: list = []
            # QuantData 权威源置首（v2.105.0+32 主链路切换，GAP-156）：
            # QUANTDATA → TDX_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
            try:
                from fts.data_sources.quantdata_provider import QuantDataProvider

                qd = QuantDataProvider()
                # 不在这探活 — 让 aggregator 的熔断器管理失败状态
                sources.append(qd)
            except Exception as _e:
                logger.debug("QuantDataProvider 实例化失败，跳过权威源 [%s]", _e)
            try:
                tq = TdxLocalSource()
                # 不在这探活 — 让 aggregator 的熔断器管理失败状态
                sources.append(tq)
            except Exception:
                logger.debug("TdxLocalSource 实例化失败，跳过")

            # ── 分钟数据源（v2.85.0：TDX 统一源 + 天勤 TQSDK）──
            minute_sources: list = []
            try:
                minute_sources.append(TdxLocalSource(period="5m"))
            except Exception:
                logger.debug("TdxLocalSource(5m) 初始化失败，跳过分钟源")

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

            # ── 字段增强层（GAP-083 阶段 C）：TQSDK 真实持仓增强 + iFinD SDK 可选 ──
            # TQSDKEnhanceSource 默认注册：天勤账号已在 .env，零额外依赖，补充权威 hold/oi_change，
            # 失败自动降级（_enhance_fields 内部 try/except + 熔断器）不阻断主路径。
            # futures_enhance_enabled=true 时追加 IFindSDKSource（方案 A：iFinD 官方 SDK 直连，
            # 补 settle/pre_settle 权威值；需本地安装 iFinDPy + .env 凭据，失败自动降级）。
            enhancers: list = []
            try:
                from fts.data_sources.tqsdk_enhance_source import TQSDKEnhanceSource

                enhancers.append(TQSDKEnhanceSource())
            except Exception as _e:
                logger.debug("TQSDKEnhanceSource 实例化失败，跳过字段增强 [%s]", _e)
            if _agg_cfg().futures_enhance_enabled:
                try:
                    from fts.data_sources.ifind_sdk_source import IFindSDKSource

                    enhancers.append(IFindSDKSource())
                except Exception as _e:
                    logger.debug("IFindSDKSource 实例化失败，跳过 [%s]", _e)

            self._aggregator = FuturesDataAggregator(
                sources=sources,
                enhancers=enhancers,
                minute_sources=minute_sources,
                tick_sources=tick_sources,
                db_path=db_path,
                cache_max_age_days=30,
                minute_cache_max_age_days=_agg_cfg().minute_cache_max_age_days,
            )
            logger.info(
                "FuturesDataAggregator 初始化完成（源数=%d, 分钟源数=%d, tick 源数=%d）",
                len(sources),
                len(minute_sources),
                len(tick_sources),
            )
        except Exception as e:
            logger.warning("FuturesDataAggregator 初始化失败: %s（降级到直接路径）", e)
            self._aggregator = None

    @staticmethod
    def _from_aggregator_df(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """将 FuturesDataAggregator（17 列）输出转换为 FuturesDataProvider 格式。

        GAP-083 补充 amount 输出（aggregator 17 列含 amount，TDX_LOCAL 真实值）；
        缺失时补 0.0（vwap 回退典型价逻辑兼容）。
        与 _from_kline_cache 对齐：settle/hold 无效（NA/≤0 占位）代理兜底，
        防止缓存中未回填的 0 占位（如股指 settle sina 源缺失）流入因子计算。
        """
        df = df.copy()
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        if "amount" not in df.columns:
            df["amount"] = 0.0
        # settle 代理：(H+L+C)/3 —— 与 vwap 回退公式一致
        mask_settle = df["settle"].isna() | (df["settle"] <= 0)
        df.loc[mask_settle, "settle"] = (df["high"] + df["low"] + df["close"]) / 3.0
        # hold 代理：20 日滚动均量
        mask_hold = df["hold"].isna() | (df["hold"] <= 0)
        df.loc[mask_hold, "hold"] = df["volume"].rolling(window=20, min_periods=1).mean()
        return df[["open", "high", "low", "close", "volume", "amount", "vwap", "hold", "settle"]]

    # ── 单标的 OHLCV ──

    def get_ohlcv(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
        adjusted: Optional[bool] = None,
    ) -> pd.DataFrame:
        """获取期货连续合约 OHLCV 日 K 线数据（v2.58.0 支持换月复权）。

        Args:
            symbol: 期货连续合约代码（如 "RB0" / "CU0" / "IF0"）。
            days: 回溯天数。
            trace_id: HARNESS trace_id。
            adjusted: 是否返回换月后复权序列（None=读取配置 futures_adjusted，
                      默认 true）。复权消除换月跳空对因子值的污染；
                      contract_kline 缺失时降级返回原始拼接序列。

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume, amount, hold, settle
            复权路径（adjusted=True 且 symbol 以 0 结尾）额外含 adj_factor 列
            Index: DatetimeIndex

        Raises:
            FuturesDataError: 所有数据源不可用
        """
        df = self._get_ohlcv_raw(symbol, days, trace_id)

        # v2.58.0 (GAP-046): 换月后复权（仅日线连续合约）
        if df is None or df.empty:
            return df
        if adjusted is None:
            try:
                from fts.config.settings import get_config

                adjusted = get_config().futures_adjusted
            except Exception:  # noqa: BLE001
                adjusted = True
        if adjusted and symbol.endswith("0"):
            try:
                from fts.data_sources.roll_calendar import RollCalendar

                df, rolls = RollCalendar().apply_adjustment(df, symbol)
                if rolls:
                    logger.info(
                        "[复权] [%s] 应用 %d 次换月复权（因子计算用）",
                        symbol,
                        len(rolls),
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning("[复权] [%s] 复权失败，返回原始序列: %s", symbol, e)

        # GAP-066 (v2.96.0): 夜盘/隔夜跳空列注入（配置开关，默认关闭，不改变既有列结构）
        try:
            from fts.config.settings import get_config as _og_cfg

            _og_enabled = bool(getattr(_og_cfg(), "inject_overnight_gap_enabled", False))
            _og_th = float(getattr(_og_cfg(), "overnight_gap_flag_threshold", 0.01))
        except Exception:  # noqa: BLE001
            _og_enabled, _og_th = False, 0.01
        if _og_enabled:
            try:
                from fts.data_sources.overnight_gap import inject_overnight_gap

                df = inject_overnight_gap(df, flag_threshold=_og_th)
            except Exception as e:  # noqa: BLE001
                logger.warning("[跳空标记] [%s] 注入失败: %s", symbol, e)
        return df

    def _get_ohlcv_raw(
        self,
        symbol: str,
        days: int = 500,
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取期货连续合约原始（未复权）OHLCV 日 K 线数据。

        Args:
            symbol: 期货连续合约代码（如 "RB0" / "CU0" / "IF0"）。
            days: 回溯天数。
            trace_id: HARNESS trace_id。

        Returns:
            pd.DataFrame with columns: open, high, low, close, volume, amount, hold, settle
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
                    symbol,
                    days,
                    frequency,
                    trace_id,
                )
                if agg_df is not None and not agg_df.empty:
                    df = agg_df.copy()
                    df["datetime"] = pd.to_datetime(df["datetime"])
                    df.set_index("datetime", inplace=True)
                    df.sort_index(inplace=True)
                    logger.info(
                        "分钟数据命中 [%s][%s] 频率=%s %d 行",
                        symbol,
                        agg_df.get("source", "").iloc[0] if "source" in agg_df.columns else "?",
                        frequency,
                        len(df),
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
            return panel, pd.DatetimeIndex(df.index)

        # G8 (v2.103.0+15): 断K/跳空清洗标记（data_gap/gap_anomaly 列，配置默认开启；
        # 清洗失败仅告警不阻断面板构建）
        try:
            from fts.config.settings import get_config as _g8_cfg

            if getattr(_g8_cfg(), "inject_data_gap_enabled", True):
                from fts.data_sources.trading_calendar import (
                    TradingCalendar,
                    mark_gap_anomalies,
                    mark_hold_anomalies,
                    mark_panel_data_gaps,
                )

                calendar = TradingCalendar.from_symbol_dates(
                    {s: df.index for s, df in panel.items()}
                )
                gap_marks = mark_panel_data_gaps(panel, calendar)
                for sym, df in panel.items():
                    m = gap_marks.get(sym, {})
                    if m.get("data_gap"):
                        logger.warning("[G8] 断K标记 [%s]: %s", sym, m.get("reason", "?"))
                    out = df.copy()
                    out["data_gap"] = bool(m.get("data_gap", False))
                    out["gap_anomaly"] = mark_gap_anomalies(out).to_numpy()
                    # CTA 手册阶段1（v2.104.0+20）：持仓量突变标记（hold_anomaly 列）
                    out["hold_anomaly"] = mark_hold_anomalies(out).to_numpy()
                    panel[sym] = out
        except Exception as e:  # noqa: BLE001 — 清洗失败降级，不阻断面板
            logger.warning("[G8] 断K/跳空清洗标记失败，跳过: %s", e)

        # 多数对齐：至少 max(2, 品种数//2) 个品种共有的日期
        min_syms = max(2, len(panel) // 2)
        common_dates = pd.DatetimeIndex(sorted(d for d, c in date_counts.items() if c >= min_syms))
        return panel, common_dates

    # ── DuckDB 读取 ──

    def _from_kline_cache(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """从 DuckDB kline_cache 表读取连续合约数据。

        kline_cache 表结构:
            symbol: 品种代码（如 "RB" / "RB0"，双格式并存）
            period: 周期（如 "daily"）
            date: 日期字符串
            open/high/low/close: 价格
            volume: 成交量
            amount: 成交额
            hold/settle: 持仓量/结算价（GAP-083：真实优先、代理兜底）

        Args:
            symbol: 期货代码（支持 "RB0" / "RB" 两种格式）
            days: 回溯天数

        Returns:
            OHLCV DataFrame（含 hold/settle 列，真实值优先、缺失/0 占位用代理）
        """
        db = _get_reader()
        try:
            # 标准化: 去掉末尾的 "0" 连续合约标记
            raw = symbol.strip().upper()
            sym = raw[:-1] if raw.endswith("0") else raw

            # 双格式对齐（GAP-083）：同时匹配 "RB" 与 "RB0"（TQ 15 年同步写入带 0 后缀），
            # 同日期优先保留 "RB0"（ORDER BY date DESC, 0 后缀优先 → drop_duplicates keep first）。
            # vwap: amount/volume（精确 VWAP，amount 有效时），否则用典型价格 (H+L+C)/3。
            # hold/settle 真实列读取；无效（NULL/0 占位）在下方代理兜底。
            result = db.execute(
                "SELECT date, open, high, low, close, volume, amount, hold, settle, "
                "  CASE WHEN amount > 0 AND volume > 0 THEN amount / volume "
                "       ELSE (high + low + close) / 3.0 END AS vwap, "
                "  symbol "
                "FROM kline_cache WHERE symbol IN (?, ?) AND period = 'daily' "
                "ORDER BY date DESC, CASE WHEN symbol LIKE '%0' THEN 0 ELSE 1 END "
                "LIMIT ?",
                [sym, f"{sym}0", days * 2],
            )
            rows = result.fetchall()
        finally:
            _release_reader(db)
        if not rows:
            return None

        df = pd.DataFrame(
            rows,
            columns=["date", "open", "high", "low", "close", "volume", "amount", "hold", "settle", "vwap", "symbol"],
        )
        df["date"] = pd.to_datetime(df["date"])
        # 双格式去重：ORDER BY 已保证 "RB0" 在前，按日期保留首行
        df = df.drop_duplicates(subset="date", keep="first")
        df.set_index("date", inplace=True)
        df.sort_index(inplace=True)
        if len(df) > days:
            df = df.iloc[-days:]

        # GAP-083 真实优先/代理兜底：settle/hold 无效（NULL 或 0 占位）才用代理
        # settle 代理：(H+L+C)/3 —— 与 vwap 回退公式保持一致，业内典型做法
        # hold 代理：20 日滚动均量（反映资金关注度持续性，因子代码中需注意此为代理）
        mask_settle = df["settle"].isna() | (df["settle"] <= 0)
        df.loc[mask_settle, "settle"] = (df["high"] + df["low"] + df["close"]) / 3.0
        mask_hold = df["hold"].isna() | (df["hold"] <= 0)
        df.loc[mask_hold, "hold"] = df["volume"].rolling(window=20, min_periods=1).mean()

        # 标准列顺序（GAP-083 补充 amount：kline_cache 已有 amount 列，TDX 真实值/TQ 0.0 占位）
        return df[["open", "high", "low", "close", "volume", "amount", "vwap", "hold", "settle"]]

    # ── 通达信本地客户端（TQ 服务 17709）──

    def _from_tq_local(self, symbol: str, days: int) -> Optional[pd.DataFrame]:
        """从通达信本地客户端 HTTP 服务获取 K 线数据。

        通达信 TQ 服务端口 17709，协议 JSON-RPC 2.0（get_market_data）。
        v2.87.0: 原 7721 TQLocalSource 已合并为 17709 TdxLocalSource。
        失败时返回 None（不抛异常，供上层降级）。

        Args:
            symbol: 期货连续合约代码（如 "RB0"）
            days: 回溯天数

        Returns:
            OHLCV DataFrame（含 hold/settle 列），或 None
        """
        try:
            from fts.data_sources.tdx_local_source import TdxLocalSource

            source = TdxLocalSource()
            if not source.is_available():
                logger.warning("通达信 TQ 服务不可达 (127.0.0.1:17709)，降级到 AKShare")
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
            # GAP-083 补充 amount：TdxLocalSource 17 列含 amount（缺失补 0.0 兜底）
            if "amount" not in df.columns:
                df["amount"] = 0.0
            return df[["open", "high", "low", "close", "volume", "amount", "vwap", "hold", "settle"]]
        except ImportError:
            logger.warning("TdxLocalSource 不可用（依赖缺失），降级到 AKShare")
            return None
        except Exception:
            logger.warning("通达信 TQ 获取异常，降级到 AKShare", exc_info=True)
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

        # GAP-083 补充 amount：AKShare sina 源无成交额 → 补 0.0（vwap 回退典型价逻辑兼容）
        df["amount"] = 0.0
        cols.append("amount")

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
            periods=n_days,
            freq="D",
        )
        close = base_price + np.cumsum(np.random.randn(n_days) * 30)
        close = np.maximum(close, base_price * 0.5)  # 防止负价格
        hold = np.random.randint(100000, 1000000, n_days).astype(float)
        high = close + np.abs(np.random.randn(n_days)) * 30
        low = close - np.abs(np.random.randn(n_days)) * 30
        return pd.DataFrame(
            {
                "open": close + np.random.randn(n_days) * 10,
                "high": high,
                "low": low,
                "close": close,
                "volume": np.random.randint(10000, 500000, n_days).astype(float),
                "amount": np.zeros(n_days),  # 合成无成交额 → 0.0（vwap 回退典型价）
                "hold": hold,
                "settle": close + np.random.randn(n_days) * 5,
                "vwap": (high + low + close + (close + np.random.randn(n_days) * 5)) / 4.0,
            },
            index=dates,
        )


# ─── 期货品种子集（84 个连续合约）───────────────────────────

# 来自 AKShare futures_display_main_sina() 的完整列表（+ T0/TL0 国债，plans/57 全期货覆盖）
FUTURES_SUBSET: list[str] = [
    # 大商所 (dce) — 22 个
    "V0",
    "P0",
    "B0",
    "M0",
    "I0",
    "JD0",
    "L0",
    "PP0",
    "FB0",
    "Y0",
    "C0",
    "A0",
    "J0",
    "JM0",
    "CS0",
    "EG0",
    "RR0",
    "EB0",
    "PG0",
    "LH0",
    "LG0",
    "BZ0",
    # 郑商所 (czce) — 25 个
    "TA0",
    "OI0",
    "RS0",
    "RM0",
    "WH0",
    "JR0",
    "SR0",
    "CF0",
    "RI0",
    "MA0",
    "FG0",
    "LR0",
    "SF0",
    "SM0",
    "CY0",
    "AP0",
    "CJ0",
    "UR0",
    "SA0",
    "PF0",
    "PK0",
    "SH0",
    "PX0",
    "PR0",
    "PL0",
    # 上期所 (shfe) — 19 个
    "FU0",
    "AL0",
    "RU0",
    "ZN0",
    "CU0",
    "AU0",
    "RB0",
    "PB0",
    "AG0",
    "BU0",
    "HC0",
    "SN0",
    "NI0",
    "SP0",
    "SS0",
    "AO0",
    "BR0",
    "AD0",
    "OP0",
    # 能源中心 (ine) — 5 个
    "SC0",
    "NR0",
    "LU0",
    "BC0",
    "EC0",
    # 中金所 (cffex) — 8 个
    "IF0",
    "TF0",
    "IH0",
    "IC0",
    "TS0",
    "IM0",
    "T0",
    "TL0",
    # 广期所 (gfex) — 5 个
    "SI0",
    "LC0",
    "PS0",
    "PT0",
    "PD0",
]

# 常用期货品种子集（流动性好的品种，用于快速测试）
FUTURES_CORE_SUBSET: list[str] = [
    "RB0",  # 螺纹钢
    "CU0",  # 铜
    "AU0",  # 黄金
    "AG0",  # 白银
    "I0",  # 铁矿石
    "M0",  # 豆粕
    "TA0",  # PTA
    "MA0",  # 甲醇
    "SC0",  # 原油
    "HC0",  # 热卷
    "NI0",  # 镍
    "SN0",  # 锡
    "P0",  # 棕榈油
    "Y0",  # 豆油
    "C0",  # 玉米
    "A0",  # 豆一
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
# 机构标准（GAP-055，v2.81.0）：固定锁死（保代际可比 + 防数据窥探）、按产业链分层抽样、
# 与核心交易池（动态池）和分层训练集（FUTURES_STRATIFIED_SUBSET）互不重叠、
# 覆盖大中小流动性品种。v2.81.0 由 6 个扩大至 15 个，覆盖 10 条产业链。
FUTURES_HOLDOUT: list[str] = [
    # 黑色系
    "JM0",  # 焦煤 — 大商所，黑色系（与训练集焦炭 J0 互补）
    # 有色金属
    "AL0",  # 铝 — 上期所，有色金属（大流动性）
    "PB0",  # 铅 — 上期所，有色金属（中流动性）
    # 能源
    "BU0",  # 沥青 — 上期所，能源（炼化下游）
    # 聚酯链
    "EG0",  # 乙二醇 — 大商所，聚酯链
    # 油化工
    "L0",  # 聚乙烯 — 大商所，油化工（塑料，大流动性）
    # 煤化工
    "FG0",  # 玻璃 — 郑商所，煤化工/建材
    "UR0",  # 尿素 — 郑商所，煤化工
    # 橡胶
    "NR0",  # 20号胶 — 能源中心，橡胶（能源化工）
    "RU0",  # 天然橡胶 — 上期所，橡胶（大流动性）
    # 造纸/林浆纸
    "SP0",  # 纸浆 — 上期所，造纸
    # 航运
    "EC0",  # 集运欧线 — 能源中心，航运（独立产业链，高波动尾部）
    # 农产品
    "JD0",  # 鸡蛋 — 大商所，农产品（禽蛋）
    "AP0",  # 苹果 — 郑商所，农产品（果蔬）
    "LH0",  # 生猪 — 大商所，农产品（畜产品，波动大）
]


# ─── 产业链分类映射（用于分层训练集选择）────────────────────

FUTURES_SECTOR_MAP: dict[str, list[str]] = {
    # 炼化聚酯链（能源产业链专属工作流身份，GAP-Ixxx）
    # 置于首位：通用工作流中性化反向映射 {sym: sector} 按"后序覆盖前序"，
    # 该 12 品种后续仍会被 能源/油化工/聚酯链/煤化工 分组覆盖，通用中性化语义不变；
    # 本分组供链工作流身份识别与板块联动监控使用（与 ENERGY_CHAIN_SYMBOLS 对齐，
    # 2026-08-15 由 9 扩至 12，覆盖四大化工子链）。
    "炼化聚酯链": [
        "SC0",
        "FU0",
        "BU0",
        "PX0",
        "TA0",
        "PF0",
        "L0",
        "PP0",
        "PG0",
        "MA0",
        "UR0",
        "SA0",  # 能源(原油→燃料油/沥青) + 芳烃→聚酯 + 油化工(塑料/液化气) + 煤化工(甲醇/尿素/纯碱)
    ],
    "黑色系": [
        "I0",
        "RB0",
        "HC0",
        "SS0",  # 钢铁产业链
        "J0",
        "JM0",  # 焦煤焦炭
        "SF0",
        "SM0",  # 铁合金
    ],
    "有色金属": [
        "CU0",
        "AL0",
        "ZN0",
        "PB0",
        "SN0",
        "NI0",  # 基本金属（含铝 AL0）
        "BC0",
        "AO0",
        "AD0",  # 铜/铝衍生
    ],
    "能源": [
        "SC0",
        "FU0",
        "LU0",
        "BU0",  # 原油/燃料油/沥青
    ],
    "聚酯链": [
        "PX0",
        "TA0",
        "PF0",
        "PR0",
        "EG0",  # 对二甲苯→PTA→聚酯(短纤/瓶片), 乙二醇
    ],
    "油化工": [
        "L0",
        "PP0",
        "V0",
        "PG0",  # 聚乙烯/聚丙烯/聚氯乙烯/液化气
        "EB0",
        "BZ0",
        "PL0",  # 苯乙烯/苯/丙烯
    ],
    "煤化工": [
        "MA0",
        "SA0",
        "UR0",
        "FG0",
        "SH0",  # 甲醇/纯碱/尿素/玻璃/烧碱
    ],
    "橡胶": [
        "RU0",
        "NR0",
        "BR0",  # 天然橡胶/20号胶/丁二烯橡胶
    ],
    "造纸/林浆纸": [
        "SP0",
        "LG0",
        "FB0",
        "OP0",  # 纸浆/原木/纤维板/双胶纸（林浆纸一体化：木材→纸浆→纸品）
    ],
    "航运": [
        "EC0",  # 集运欧线（航运运价，独立于商品产业链）
    ],
    "油脂油料": [
        "A0",
        "B0",
        "M0",
        "Y0",
        "P0",  # 豆系/棕榈油
        "OI0",
        "RS0",
        "RM0",  # 菜籽系
        "PK0",  # 花生（油脂压榨为主）
    ],
    "谷物": [
        "C0",
        "CS0",
        "RR0",
        "WH0",
        "JR0",
        "RI0",
        "LR0",  # 玉米/淀粉/稻米/麦
    ],
    "畜牧": [
        "LH0",
        "JD0",  # 生猪/鸡蛋（养殖链）
    ],
    "软商品": [
        "SR0",
        "CF0",
        "CY0",  # 白糖/棉花/棉纱（进口/天气驱动）
    ],
    "果蔬": [
        "AP0",
        "CJ0",  # 苹果/红枣（鲜果现货驱动）
    ],
    "贵金属": [
        "AU0",
        "AG0",
        "PT0",
        "PD0",  # 黄金/白银/铂/钯（铂族金属同属贵金属板块）
    ],
    "新能源/新材料": [
        "LC0",
        "SI0",
        "PS0",
    ],
    "金融期货": [
        "IF0",
        "IC0",
        "IH0",
        "IM0",
        "TF0",
        "TS0",
    ],
}


# 分层训练品种集（按产业链从每组中选取 2-3 个代表品种，
# 确保训练集覆盖所有产业链类别，排除盲测品种池）
FUTURES_STRATIFIED_SUBSET: list[str] = [
    # 黑色系
    "RB0",
    "I0",
    "J0",
    # 有色金属
    "CU0",
    "ZN0",
    "NI0",
    # 能源 → 原油/燃料油/沥青
    # 聚酯链 → PX→PTA→聚酯
    # 油化工 → 石脑油裂解下游
    # 煤化工 → 煤基化工品
    # 橡胶 → 天然/合成橡胶
    # 造纸/林浆纸 → 林浆纸一体化（纸浆/原木/纤维板）
    # 航运 → 集运欧线
    "TA0",
    "MA0",
    "SC0",
    # 农产品
    "M0",
    "C0",
    "SR0",
    # 贵金属
    "AU0",
    "AG0",
    # 新能源/新材料
    "LC0",
    "SI0",
    # 金融期货
    "IF0",
    "IC0",
    "IH0",
]


# ─── 能源产业链专属工作流（独立于 FTS 通用工作流）────────────────
# 设计（GAP-Ixxx）：全训 + 链外盲测，以能源链为核心泛化到全部化工产业链。
# 训练链 = 12 个化工品种（覆盖四大化工子链，2026-08-15 由 9→12 扩池降相关性）：
#   能源 3（SC/FU/BU）+ 聚酯链 3（PF/TA/EG）+ 油化工 3（L/PP/PG）+ 煤化工 3（MA/UR/SA）；
#   原 9 品种 SC/FU/LU/BU/PG/PX/TA/PF/PR 中 LU（与 FU 高相关）/PR（与 PF 高相关）
#   换出至盲测池，换入 L/PP/MA/UR/SA 覆盖油化工/煤化工子链，降低训练池内品种相关性。
# v2.104.0+106（GAP-133）：聚酯链代表 PX0→EG0——PX0（2023-09 上市，仅 704 行）会封顶
#   训练公共窗口致走航窗口数受限，EG0（2018-12 上市，1861 行）历史更长；PX0 自动回盲测池。
# 盲测池 = 其余化工产业链（聚酯链/油化工/煤化工）全部品种 − 训练 12 品种，
#   验证链因子向整个化工产业链的外延泛化能力。
# 存储路由：因子库 market="energy" → data/factor_catalog_energy.duckdb（独立文件），
# 精英目录 → memory/knowledge/factors/energy_chain_elite（独立目录）。
# 链内品种历史窗口 v2.104.0+106 起不再受 PX0 限制（最长品种约 6 年，共同窗口由
#   jobs.py `_get_l2_panel_days()`（config l2_panel_days=750，env FTS_L2_PANEL_DAYS 可覆盖）决定，走航可切 4 窗口）。
ENERGY_CHAIN_SYMBOLS: list[str] = [
    "SC0",  # 原油 — INE，链上游源头（能源）
    "FU0",  # 燃料油 — SHFE，原油下游（能源）
    "BU0",  # 沥青 — SHFE，炼化下游（能源）
    "PF0",  # 短纤 — CZCE，聚酯成品（聚酯链）
    "TA0",  # PTA — CZCE，聚酯链中游（聚酯链）
    "EG0",  # 乙二醇 — DCE，聚酯原料/防冻剂（聚酯链）
    "L0",   # 聚乙烯 — DCE，塑料（油化工）
    "PP0",  # 聚丙烯 — DCE，塑料（油化工）
    "PG0",  # 液化石油气 — DCE，炼厂伴生气（油化工）
    "MA0",  # 甲醇 — CZCE，煤基化工（煤化工）
    "UR0",  # 尿素 — CZCE，氮肥（煤化工）
    "SA0",  # 纯碱 — CZCE，煤化工/建材（煤化工）
]

# 能源链专属训练链（全训：链内品种全部参与演化训练）
ENERGY_CHAIN_TRAIN: list[str] = list(ENERGY_CHAIN_SYMBOLS)

# 化工产业链分组（盲测池来源；橡胶为独立板块不计入，可按需扩展）
ENERGY_CHAIN_CHEMICAL_SECTORS: tuple[str, ...] = ("聚酯链", "油化工", "煤化工")

# 能源链专属盲测池（链外盲测：其余化工产业链品种，泛化验证链因子的外延能力）
ENERGY_CHAIN_HOLDOUT: list[str] = sorted(
    {
        sym
        for sec in ENERGY_CHAIN_CHEMICAL_SECTORS
        for sym in FUTURES_SECTOR_MAP.get(sec, [])
    }
    - set(ENERGY_CHAIN_SYMBOLS)
)

# 能源链因子库/精英目录路由标记（与通用 "futures" 隔离）
ENERGY_CHAIN_MARKET: str = "energy"

# 能源链训练品种最小历史深度阈值（GAP-Ixxx，A 数据补全后核验标准）：
# 真实（非 SYNTHETIC）日线行数 ≥ 该值方可保留在训练链参与演化训练；
# 低于该值的品种经 scripts/sync_energy_chain_depth.py 补全，补全后仍不足则降级。
# 2026-08-14 实测补全后：LU0=1492（2020-06 起）、PR0=473（2024-08 起）、PL0=260（盲测池）；
# 2026-08-15 扩池至 12 品种后，最短历史为 PX0（2023-09 起，约 700 行），全部达标，共同窗口由 PX0 决定。
# v2.104.0+106（GAP-133）PX0 换出训练链（入盲测池）、EG0 换入（2018-12 起约 1861 行），
# 训练链最短历史为 PF0（2020-10 起约 1418 行），共同窗口不再受 PX0 限制（由 jobs.py 面板天数决定）。
ENERGY_CHAIN_MIN_TRAIN_ROWS: int = 300
# 盲测池最小真实历史门槛（GAP-130 v2.104.0+80，config/futures_universe.yaml SSOT）
ENERGY_CHAIN_MIN_HOLDOUT_ROWS: int = 250

# 能源链 L1 独立输出目录（2026-08-15：与通用 L1 严格隔离）：
# 通用 L1 产出：memory/meta_loop、memory/knowledge/factors/factor_pool.json、
#   memory/knowledge/factors/l1_injected/、memory/debates/；
# 能源链 L1 全部独立落在 energy 专属子目录，互不污染。
ENERGY_CHAIN_L1_MEMORY_DIR: str = "memory/meta_loop/energy"
ENERGY_CHAIN_L1_POOL_PATH: str = "memory/knowledge/factors/factor_pool_energy.json"
ENERGY_CHAIN_L1_INJECT_DIR: str = "memory/knowledge/factors/l1_injected_energy"
ENERGY_CHAIN_L1_DEBATES_DIR: str = "memory/debates/energy"


# ─── 品种池/产业链配置加载（SSOT: config/futures_universe.yaml）────────────
# 上方硬编码常量即为"内置默认"（兜底）。模块加载时若 YAML 存在且校验通过，
# 则以 YAML 为准覆盖；YAML 缺失/损坏/校验失败则保留内置默认并告警。
# 消费方经 `from fts.data_futures import XXX` 导入，常量名/类型不变，零改动。

_FUTURES_UNIVERSE_YAML = Path(__file__).resolve().parent.parent / "config" / "futures_universe.yaml"

# 全期货覆盖优先级规划（plans/57 §9 步骤0，P0→P3 扩展路线；YAML coverage_priority 加载后覆盖）
FUTURES_COVERAGE_PLAN: dict[str, dict[str, object]] = {}


def _load_futures_universe_config() -> bool:
    """加载 config/futures_universe.yaml 并覆盖品种池/产业链常量。

    校验规则（任一失败即回退内置默认）:
      1) universe 展平无重复品种；
      2) 各池（core/holdout/stratified/训练池）均 ⊆ universe；
      3) 盲测池 ∩ 分层训练集 = ∅（机构标准 GAP-055）；
      4) 泛化范围子链名必须存在于 sector_map；
      5) 炼化聚酯链分组由 energy 训练池自动生成并置首位（与 ENERGY_CHAIN_SYMBOLS 对齐）。

    Returns:
        True=已应用 YAML 配置；False=YAML 缺失/损坏/校验失败，保留内置默认。
    """
    if not _FUTURES_UNIVERSE_YAML.exists():
        return False
    try:
        import yaml  # type: ignore[import-untyped]

        cfg = yaml.safe_load(_FUTURES_UNIVERSE_YAML.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("品种池/产业链配置 YAML 解析失败，使用内置默认: %s", e)
        return False
    try:
        universe = [s for grp in cfg["universe"].values() for s in grp]
        core_subset = list(cfg["core_subset"])
        holdout = list(cfg["holdout"])
        stratified = list(cfg["stratified_subset"])
        sector_map: dict[str, list[str]] = {k: list(v) for k, v in cfg["sector_map"].items()}
        ew = cfg["workflows"]["energy"]
        chain_symbols = list(ew["chain_symbols"])
        chemical_sectors = tuple(ew["chemical_sectors"])

        # 校验
        assert len(set(universe)) == len(universe), "universe 含重复品种"
        uni_set = set(universe)
        for name, pool in (
            ("core_subset", core_subset),
            ("holdout", holdout),
            ("stratified_subset", stratified),
            ("energy.chain_symbols", chain_symbols),
        ):
            assert set(pool) <= uni_set, f"{name} 存在 universe 外品种"
        assert not (set(holdout) & set(stratified)), "盲测池与分层训练集重叠"
        for sec in chemical_sectors:
            assert sec in sector_map, f"泛化范围子链 [{sec}] 不存在于 sector_map"
    except (KeyError, TypeError, ValueError, AssertionError) as e:
        logger.warning("品种池/产业链配置校验失败，使用内置默认: %s", e)
        return False

    # 应用（覆盖内置默认；炼化聚酯链分组置首位）
    global FUTURES_SUBSET, FUTURES_CORE_SUBSET, FUTURES_HOLDOUT
    global FUTURES_SECTOR_MAP, FUTURES_STRATIFIED_SUBSET
    global ENERGY_CHAIN_SYMBOLS, ENERGY_CHAIN_TRAIN, ENERGY_CHAIN_CHEMICAL_SECTORS
    global ENERGY_CHAIN_HOLDOUT, ENERGY_CHAIN_MARKET, ENERGY_CHAIN_MIN_TRAIN_ROWS
    global ENERGY_CHAIN_MIN_HOLDOUT_ROWS
    global ENERGY_CHAIN_L1_MEMORY_DIR, ENERGY_CHAIN_L1_POOL_PATH
    global ENERGY_CHAIN_L1_INJECT_DIR, ENERGY_CHAIN_L1_DEBATES_DIR
    global FUTURES_COVERAGE_PLAN

    FUTURES_SUBSET = list(universe)
    FUTURES_CORE_SUBSET = list(core_subset)
    FUTURES_HOLDOUT = list(holdout)
    FUTURES_STRATIFIED_SUBSET = list(stratified)
    FUTURES_SECTOR_MAP = {"炼化聚酯链": list(chain_symbols), **sector_map}

    ENERGY_CHAIN_SYMBOLS = list(chain_symbols)
    ENERGY_CHAIN_TRAIN = list(chain_symbols)
    ENERGY_CHAIN_CHEMICAL_SECTORS = tuple(chemical_sectors)
    ENERGY_CHAIN_HOLDOUT = sorted(
        {
            sym
            for sec in ENERGY_CHAIN_CHEMICAL_SECTORS
            for sym in FUTURES_SECTOR_MAP.get(sec, [])
        }
        - set(ENERGY_CHAIN_SYMBOLS)
    )
    ENERGY_CHAIN_MARKET = str(ew["market"])
    ENERGY_CHAIN_MIN_TRAIN_ROWS = int(ew["min_train_rows"])
    ENERGY_CHAIN_MIN_HOLDOUT_ROWS = int(ew.get("min_holdout_rows", 250))
    ENERGY_CHAIN_L1_MEMORY_DIR = str(ew["l1_memory_dir"])
    ENERGY_CHAIN_L1_POOL_PATH = str(ew["l1_pool_path"])
    ENERGY_CHAIN_L1_INJECT_DIR = str(ew["l1_inject_dir"])
    ENERGY_CHAIN_L1_DEBATES_DIR = str(ew["l1_debates_dir"])

    # 全期货覆盖优先级规划（plans/57 §9 步骤0，best-effort：缺失/损坏不阻断主配置）。
    # 校验：每级 symbols ⊆ universe、级别间交集为空、四级并集 = universe。
    _coverage: dict[str, dict[str, object]] = {}
    raw_cov = cfg.get("coverage_priority")
    if isinstance(raw_cov, dict) and raw_cov:
        try:
            cov_sets: list[set[str]] = []
            for key, item in raw_cov.items():
                syms = list(item["symbols"])  # type: ignore[index]
                assert set(syms) <= uni_set, f"coverage_priority[{key}] 存在 universe 外品种"
                cov_sets.append(set(syms))
                _coverage[key] = {"chains": list(item["chains"]), "symbols": syms}  # type: ignore[index]
            for i in range(len(cov_sets)):
                for j in range(i + 1, len(cov_sets)):
                    assert not (cov_sets[i] & cov_sets[j]), "coverage_priority 级别间品种重叠"
            assert set().union(*cov_sets) == uni_set, "coverage_priority 并集 != universe"
        except (KeyError, TypeError, ValueError, AssertionError) as e:
            logger.warning("coverage_priority 校验失败，覆盖规划不加载: %s", e)
            _coverage = {}
    FUTURES_COVERAGE_PLAN = _coverage
    logger.info("品种池/产业链配置已从 %s 加载（SSOT）", _FUTURES_UNIVERSE_YAML.name)
    return True


_load_futures_universe_config()


def check_energy_chain_depth(
    min_rows: int = ENERGY_CHAIN_MIN_TRAIN_ROWS,
) -> dict[str, int]:
    """审计能源链训练链品种历史深度（kline_cache 真实行数，SYNTHETIC 排除）。

    Args:
        min_rows: 深度阈值（默认 ENERGY_CHAIN_MIN_TRAIN_ROWS）。

    Returns:
        {"ok": 达标品种数, "below": 不达标品种数, "below_symbols": [..]}。
    """
    try:
        db = _get_reader()
        try:
            below: list[str] = []
            ok = 0
            for sym in ENERGY_CHAIN_TRAIN:
                base = sym[:-1] if sym.endswith("0") else sym
                n = db.execute(
                    "SELECT COUNT(*) FROM kline_cache "
                    "WHERE symbol IN (?, ?) AND period='daily' "
                    "AND (source IS NULL OR source != 'SYNTHETIC')",
                    [base, sym],
                ).fetchone()[0]
                if int(n) >= min_rows:
                    ok += 1
                else:
                    below.append(sym)
            return {"ok": ok, "below": len(below), "below_symbols": below}
        finally:
            _release_reader(db)
    except Exception:  # noqa: BLE001
        return {"ok": 0, "below": len(ENERGY_CHAIN_TRAIN), "below_symbols": list(ENERGY_CHAIN_TRAIN)}


# ─── 品种中文名称映射（FUTURES_SUBSET 全量）─────────────────

FUTURES_SYMBOL_NAMES: dict[str, str] = {
    # 大商所 (dce)
    "V0": "聚氯乙烯",
    "P0": "棕榈油",
    "B0": "豆二",
    "M0": "豆粕",
    "I0": "铁矿石",
    "JD0": "鸡蛋",
    "L0": "聚乙烯",
    "PP0": "聚丙烯",
    "FB0": "纤维板",
    "Y0": "豆油",
    "C0": "玉米",
    "A0": "豆一",
    "J0": "焦炭",
    "JM0": "焦煤",
    "CS0": "玉米淀粉",
    "EG0": "乙二醇",
    "RR0": "粳米",
    "EB0": "苯乙烯",
    "PG0": "液化石油气",
    "LH0": "生猪",
    "LG0": "原木",
    "BZ0": "苯",
    # 郑商所 (czce)
    "TA0": "PTA",
    "OI0": "菜籽油",
    "RS0": "菜籽",
    "RM0": "菜粕",
    "WH0": "强麦",
    "JR0": "粳稻",
    "SR0": "白糖",
    "CF0": "棉花",
    "RI0": "早籼稻",
    "MA0": "甲醇",
    "FG0": "玻璃",
    "LR0": "晚籼稻",
    "SF0": "硅铁",
    "SM0": "锰硅",
    "CY0": "棉纱",
    "AP0": "苹果",
    "CJ0": "红枣",
    "UR0": "尿素",
    "SA0": "纯碱",
    "PF0": "短纤",
    "PK0": "花生",
    "SH0": "烧碱",
    "PX0": "对二甲苯",
    "PR0": "瓶片",
    "PL0": "丙烯",
    # 上期所 (shfe)
    "FU0": "燃料油",
    "AL0": "铝",
    "RU0": "橡胶",
    "ZN0": "锌",
    "CU0": "铜",
    "AU0": "黄金",
    "RB0": "螺纹钢",
    "PB0": "铅",
    "AG0": "白银",
    "BU0": "沥青",
    "HC0": "热轧卷板",
    "SN0": "锡",
    "NI0": "镍",
    "SP0": "纸浆",
    "SS0": "不锈钢",
    "AO0": "氧化铝",
    "BR0": "丁二烯橡胶",
    "AD0": "铸造铝合金",
    "OP0": "胶版印刷纸",
    # 能源中心 (ine)
    "SC0": "原油",
    "NR0": "20号胶",
    "LU0": "低硫燃料油",
    "BC0": "国际铜",
    "EC0": "集运欧线",
    # 中金所 (cffex)
    "IF0": "沪深300",
    "TF0": "5年期国债",
    "IH0": "上证50",
    "IC0": "中证500",
    "TS0": "2年期国债",
    "IM0": "中证1000",
    "T0": "10年期国债",
    "TL0": "30年期国债",
    # 广期所 (gfex)
    "SI0": "工业硅",
    "LC0": "碳酸锂",
    "PS0": "多晶硅",
    "PT0": "铂",
    "PD0": "钯",
}


# FTS 连续合约代码 → AKShare futures_symbol_mark 中文名（用于实时行情查询）
_SYMBOL_MARK_NAMES: dict[str, str] = {
    # 大商所
    "V0": "PVC",
    "P0": "棕榈",
    "B0": "豆二",
    "M0": "豆粕",
    "I0": "铁矿石",
    "JD0": "鸡蛋",
    "L0": "塑料",
    "PP0": "PP",
    "FB0": "纤维板",
    "Y0": "豆油",
    "C0": "玉米",
    "A0": "豆一",
    "J0": "焦炭",
    "JM0": "焦煤",
    "CS0": "玉米淀粉",
    "EG0": "乙二醇",
    "RR0": "粳米",
    "EB0": "苯乙烯",
    "PG0": "液化石油气",
    "LH0": "生猪",
    "LG0": "原木",
    "BZ0": "纯苯",
    # 郑商所
    "TA0": "PTA",
    "OI0": "菜油",
    "RS0": "菜籽",
    "RM0": "菜粕",
    "WH0": "强麦",
    "JR0": "粳稻",
    "SR0": "白糖",
    "CF0": "棉花",
    "RI0": "早籼稻",
    "MA0": "郑醇",
    "FG0": "玻璃",
    "LR0": "晚籼稻",
    "SF0": "硅铁",
    "SM0": "锰硅",
    "CY0": "棉纱",
    "AP0": "鲜苹果",
    "CJ0": "红枣",
    "UR0": "尿素",
    "SA0": "纯碱",
    "PF0": "短纤",
    "PK0": "花生",
    "SH0": "烧碱",
    "PX0": "二甲苯",
    "PR0": "瓶级聚酯切片",
    "PL0": "丙烯",
    # 上期所
    "FU0": "燃油",
    "AL0": "沪铝",
    "RU0": "橡胶",
    "ZN0": "沪锌",
    "CU0": "沪铜",
    "AU0": "黄金",
    "RB0": "螺纹钢",
    "PB0": "沪铅",
    "AG0": "白银",
    "BU0": "沥青",
    "HC0": "热轧卷板",
    "SN0": "沪锡",
    "NI0": "沪镍",
    "SP0": "纸浆",
    "SS0": "不锈钢",
    "AO0": "氧化铝",
    "BR0": "丁二烯橡胶",
    "AD0": "铸造铝合金期货",
    "OP0": "胶版印刷纸期货",
    # 能源中心
    "SC0": "原油",
    "NR0": "20号胶",
    "LU0": "低硫燃料油",
    "BC0": "国际铜",
    "EC0": "集运指数(欧线)期货",
    # 中金所
    "IF0": "沪深300指数期货",
    "TF0": "5年期国债期货",
    "IH0": "上证50指数期货",
    "IC0": "中证500指数期货",
    "TS0": "2年期国债期货",
    "IM0": "中证1000股指期货",
    "T0": "10年期国债期货",
    "TL0": "30年期国债期货",
    # 广期所
    "SI0": "工业硅",
    "LC0": "碳酸锂",
    "PS0": "多晶硅",
    "PT0": "铂",
    "PD0": "钯",
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
                df["symbol"].str.startswith(prefix) & (df["symbol"] != sym) & (df["symbol"].str.len() > len(prefix))
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
        db = _get_reader()
    except FuturesDataError:
        return result

    # contract_kline 中 symbol 无末尾 "0"（如 "RB"）
    base_syms = [s[:-1] if s.endswith("0") else s for s in symbols]
    placeholders = ",".join("?" * len(base_syms))
    try:
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
    finally:
        _release_reader(db)
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


def sync_contract_kline(
    symbols: list[str] | None = None,
    days: int = 500,
    trace_id: str = "",
) -> dict[str, int]:
    """补拉具体合约日线写入 contract_kline（v2.58.0，GAP-046 换月日历基础）。

    通过 AKShare `futures_display_main_sina` 获取各品种活跃具体合约列表，
    逐个拉取具体合约日线（`futures_zh_daily_sina(symbol="RB2610")`）写入
    `contract_kline` 表（按品种全量重写，幂等）。失败不抛异常——contract_kline
    缺失时 RollCalendar 自动降级返回原始拼接序列（见 04-resilience.md）。

    Args:
        symbols: 连续合约代码列表（如 ["RB0", "CU0"]），默认 FUTURES_CORE_SUBSET
        days: 每个合约回溯天数
        trace_id: HARNESS trace_id

    Returns:
        {"written": 写入行数, "failed": 失败品种数}
    """
    if symbols is None:
        symbols = get_dynamic_core_subset()

    try:
        import akshare as ak  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("[contract_kline] akshare 未安装，跳过具体合约同步")
        return {"written": 0, "failed": len(symbols)}

    # 品种 → 活跃具体合约列表（akshare 主连品种代码为 "RB0" 格式）
    contract_map: dict[str, list[str]] = {}
    try:
        display_df = ak.futures_display_main_sina()
        if display_df is not None and not display_df.empty:
            for _, row in display_df.iterrows():
                sym_code = str(row.get("symbol", "")).upper()
                if not sym_code:
                    continue
                base = sym_code[:-1] if sym_code.endswith("0") else sym_code
                contract = str(row.get("contract", "")).upper()
                if base and contract:
                    contract_map.setdefault(base, []).append(contract)
    except Exception as e:  # noqa: BLE001
        logger.warning("[contract_kline] 活跃合约列表获取失败: %s", e)

    if not contract_map:
        logger.warning("[contract_kline] 未获取到活跃合约列表，跳过")
        return {"written": 0, "failed": len(symbols)}

    written = 0
    failed = 0
    for sym in symbols:
        base = sym[:-1] if sym.endswith("0") else sym
        contracts = contract_map.get(base, [])
        if not contracts:
            failed += 1
            continue
        rows: list[tuple] = []
        for contract in contracts:
            try:
                df = ak.futures_zh_daily_sina(symbol=contract)
                if df is None or df.empty:
                    continue
                df = df.tail(days)
                for _, r in df.iterrows():
                    rows.append(
                        (
                            base,
                            contract,
                            "daily",
                            pd.Timestamp(r["date"]).date(),
                            float(r.get("open", 0.0)),
                            float(r.get("high", 0.0)),
                            float(r.get("low", 0.0)),
                            float(r.get("close", 0.0)),
                            float(r.get("volume", 0.0)),
                            float(r.get("amount", 0.0)),
                            float(r.get("hold", 0.0)),
                            float(r.get("settle", 0.0)),
                            "AKSHARE",
                            datetime.now().isoformat(),
                            trace_id,
                        )
                    )
            except Exception as e:  # noqa: BLE001
                logger.debug("[contract_kline] 合约 %s 拉取失败: %s", contract, e)
        if not rows:
            failed += 1
            continue
        try:
            _write_contract_kline(base, rows)
            written += len(rows)
        except Exception as e:  # noqa: BLE001
            logger.warning("[contract_kline] 写入失败 [%s]: %s", base, e)
            failed += 1

    logger.info("[contract_kline] 同步完成: %d 行写入, %d 品种失败 (trace_id=%s)", written, failed, trace_id)
    return {"written": written, "failed": failed}


def _write_contract_kline(base: str, rows: list[tuple]) -> None:
    """将具体合约日线写入 contract_kline（按品种全量重写，幂等）。

    E.4 S1：写操作在 `_write_scope()`（filelock + 短连接）内执行，
    写锁存活从进程级降到秒级。

    Args:
        base: 品种基础代码（如 "RB"，无 "0" 后缀）
        rows: (symbol, contract, period, date, open, high, low, close,
              volume, amount, hold, settle, source, fetched_at, trace_id) 元组列表
    """
    from fts.data_sources.migrate import migrate_schema

    with _write_scope() as writer:
        try:
            migrate_schema(str(_get_db_path()))
        except Exception as e:  # noqa: BLE001
            logger.warning("[contract_kline] migrate_schema 失败: %s", e)
        # 按品种全量重写（保证与最新活跃合约集一致）
        writer.execute("DELETE FROM contract_kline WHERE symbol = ?", [base])
        writer.executemany(
            """
            INSERT INTO contract_kline (
                symbol, contract, period, date, open, high, low, close,
                volume, amount, hold, settle, source, fetched_at, trace_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _get_db_path() -> str:
    """返回 DuckDB 文件路径（data/fts_history.duckdb）。"""
    return "data/fts_history.duckdb"


def _try_tq_realtime(symbols: list[str]) -> tuple[dict[str, float], set[str]]:
    """通过通达信本地 TQ 服务获取实时快照（主路径）。

    v2.87.0: TQLocalSource(7721) 合并为 TdxLocalSource(17709, get_market_snapshot)。

    Returns:
        (成功价格字典, 失败品种集合)
    """
    prices: dict[str, float] = {}
    failed: set[str] = set()

    try:
        from fts.data_sources.tdx_local_source import TdxLocalSource
    except ImportError:
        return prices, set(symbols)

    tq = TdxLocalSource()
    if not tq.is_available():
        logger.warning("通达信 TQ 服务探活失败，跳过实时路径")
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
        1. TDX_LOCAL — 通达信本地 HTTP 统一源 17709 实时快照（get_market_snapshot）
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
            logger.info(f"[realtime] AKShare 降级补全 {len(ak_prices)} 个品种")

    logger.info(
        f"[realtime] 最终覆盖 {len(prices)}/{len(symbols)} 品种 "
        f"(TQ:{len(tq_prices)} + AKShare:{len(prices) - len(tq_prices)})"
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


# ─── 数据驱动动态池（GAP-054）────────────────────────────

DYNAMIC_POOL_CACHE: str = str(
    Path(__file__).resolve().parent.parent / "memory" / "portfolio" / "futures" / "futures_dynamic_pool.json"
)


def _extract_pool(value: Any) -> list[str] | None:
    """从动态池值（dict 含 pool 列表）提取有效品种列表；非法返回 None。"""
    pool = value.get("pool") if isinstance(value, dict) else None
    if isinstance(pool, list) and pool:
        valid = [s for s in pool if isinstance(s, str) and s.strip()]
        if valid:
            return valid
    return None


def get_dynamic_core_subset() -> list[str]:
    """读取数据驱动动态核心池；缓存缺失/损坏时回退静态 FUTURES_CORE_SUBSET。

    动态池由 scripts/sync_liquidity_pool.py 定期刷新落盘（渐进式替换 +
    产业覆盖约束，见 GAP-054）。本函数纯读取、不抛异常，运行期零风险降级：
    - SSOT `state.duckdb`（plans/29 P4 读路径切换）→ JSON 缓存（兼容期）→ 静态清单
    - 任意异常 → 回退静态清单（降级优先）
    """
    import json

    # 1) SSOT: state.duckdb（读路径切换，plans/29 P4）
    try:
        from fts.store.state_db import StateKVStore

        store = StateKVStore()
        try:
            pool = _extract_pool(store.get("portfolio", "futures_dynamic_pool"))
            if pool:
                return pool
        finally:
            store.close()
    except Exception:  # noqa: BLE001 — 降级优先，绝不阻断运行期
        pass

    # 2) 兼容: JSON 缓存（冻结期退役前保留）
    try:
        path = Path(DYNAMIC_POOL_CACHE)
        if path.exists():
            pool = _extract_pool(json.loads(path.read_text(encoding="utf-8")))
            if pool:
                return pool
    except Exception:  # noqa: BLE001
        pass

    # 3) 降级: 静态池
    return list(FUTURES_CORE_SUBSET)


__all__ = [
    "DuckDBConnection",
    "DuckDBWriter",
    "DuckDBReader",
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
    "ENERGY_CHAIN_SYMBOLS",
    "ENERGY_CHAIN_TRAIN",
    "ENERGY_CHAIN_HOLDOUT",
    "ENERGY_CHAIN_CHEMICAL_SECTORS",
    "ENERGY_CHAIN_MARKET",
    "ENERGY_CHAIN_MIN_TRAIN_ROWS",
    "ENERGY_CHAIN_MIN_HOLDOUT_ROWS",
    "ENERGY_CHAIN_L1_MEMORY_DIR",
    "ENERGY_CHAIN_L1_POOL_PATH",
    "ENERGY_CHAIN_L1_INJECT_DIR",
    "ENERGY_CHAIN_L1_DEBATES_DIR",
    "check_energy_chain_depth",
    "FUTURES_SYMBOL_NAMES",
    "DYNAMIC_POOL_CACHE",
    "get_dynamic_core_subset",
    "get_dominant_contracts",
    "get_realtime_prices",
    "sync_contract_kline",
    "_try_tq_realtime",
    "_try_akshare_realtime",
    "_extract_quote_price",
]
