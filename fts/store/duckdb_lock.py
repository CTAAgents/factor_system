"""
fts/store/duckdb_lock.py — 跨进程写锁（标准库实现）

E.4（S1）用于串行化对同一 .duckdb 文件的写窗口：任意时刻至多一个进程
持有写锁，写窗口结束（写连接关闭）后释放。解决「scripts/ 与调度器 job
并发写同一文件抢文件锁」的根因 2。

平台实现:
    - Windows: `msvcrt.locking`（LK_NBLCK 轮询实现 timeout）
    - POSIX:   `fcntl.flock`（LOCK_EX | LOCK_NB 轮询实现 timeout）
    零新增依赖（标准库）。

用法:
    from fts.store.duckdb_lock import duckdb_write_lock

    with duckdb_write_lock("data/fts_history.duckdb"):
        conn = duckdb.connect("data/fts_history.duckdb")
        ...  # 写操作（短生命周期，写完即关）
        conn.close()

注意:
    - 不可重入：同一线程内嵌套获取同一库锁会死锁（超时抛 TimeoutError）。
      写窗口必须串行，调用方保证不嵌套。
    - 锁文件位于 <db_path 父目录>/.locks/<db_path.name>.lock，自动创建。
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_IS_WINDOWS = os.name == "nt"

# 获锁轮询间隔（秒）
_POLL_INTERVAL = 0.1


def _lock_path(db_path: Path) -> Path:
    """锁文件路径：<db_path 父目录>/.locks/<db_path.name>.lock。"""
    return db_path.parent / ".locks" / f"{db_path.name}.lock"


def _acquire(fh, timeout: float) -> None:
    """非阻塞轮询获锁，超时抛 TimeoutError（失败透明）。"""
    if _IS_WINDOWS:
        import msvcrt

        deadline = time.monotonic() + timeout
        while True:
            try:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"跨进程写锁等待超时（{timeout:g}s）: {fh.name}"
                    ) from None
                time.sleep(_POLL_INTERVAL)
    else:
        import fcntl  # type: ignore[import-not-found]  # POSIX only

        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"跨进程写锁等待超时（{timeout:g}s）: {fh.name}"
                    ) from None
                time.sleep(_POLL_INTERVAL)


def _release(fh) -> None:
    """释放文件锁（幂等，失败静默）。"""
    if _IS_WINDOWS:
        import msvcrt

        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl  # type: ignore[import-not-found]  # POSIX only

        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextmanager
def duckdb_write_lock(db_path: str | Path, timeout: float = 30.0) -> Iterator[None]:
    """跨进程写锁 contextmanager：持有期间独占 db_path 写窗口，退出即释放。

    Args:
        db_path: 目标 .duckdb 文件路径
        timeout: 获锁等待上限（秒），超时抛 TimeoutError

    Raises:
        TimeoutError: timeout 内未获锁（另一进程正在写）
    """
    db = Path(db_path)
    lock_fp = _lock_path(db)
    lock_fp.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_fp, "a+b")  # noqa: SIM115 — 跨进程锁需保持文件句柄打开
    try:
        _acquire(fh, timeout)
        yield
    finally:
        _release(fh)
        fh.close()
