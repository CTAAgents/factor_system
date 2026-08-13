"""E.4（S1）：fts/store/duckdb_lock.py 跨进程写锁测试。

覆盖:
- 锁互斥：A 持锁期间 B 获取超时；A 释放后 B 可获取
- timeout 超时抛 TimeoutError
- 锁文件自动创建于 <db 父目录>/.locks/
- contextmanager 正常释放（可再次获取）
"""

from __future__ import annotations

import threading
import time

import pytest

from fts.store.duckdb_lock import duckdb_write_lock


class TestDuckDBWriteLock:
    def test_lock_file_created(self, tmp_path):
        db = tmp_path / "fts_history.duckdb"
        with duckdb_write_lock(db, timeout=0.5):
            lock_fp = tmp_path / ".locks" / "fts_history.duckdb.lock"
            assert lock_fp.exists()

    def test_mutex_blocks_second_acquire(self, tmp_path):
        """A 持锁期间 B 获取 → 超时；A 释放后 B 可获取。"""
        db = tmp_path / "test.duckdb"

        acquired_by_b = threading.Event()
        release_a = threading.Event()
        b_timeout: list[bool] = [False]

        def worker_a() -> None:
            with duckdb_write_lock(db, timeout=5.0):
                release_a.wait(timeout=5.0)

        def worker_b() -> None:
            try:
                with duckdb_write_lock(db, timeout=0.3):
                    acquired_by_b.set()
            except TimeoutError:
                b_timeout[0] = True

        ta = threading.Thread(target=worker_a)
        tb = threading.Thread(target=worker_b)
        ta.start()
        time.sleep(0.2)  # 确保 A 已持锁
        tb.start()
        tb.join(timeout=3.0)
        # B 在 A 持锁期间获取失败（超时）
        assert b_timeout[0] is True
        assert not acquired_by_b.is_set()

        # A 释放后 B 可获取
        release_a.set()
        ta.join(timeout=3.0)
        acquired_by_b.clear()
        with duckdb_write_lock(db, timeout=2.0):
            pass  # 主线程可正常获取（锁已释放）

    def test_timeout_raises(self, tmp_path):
        db = tmp_path / "t.duckdb"
        with duckdb_write_lock(db, timeout=5.0):
            with pytest.raises(TimeoutError, match="等待超时"):
                # 同线程嵌套获取同一库 → 死锁由 timeout 兜底
                with duckdb_write_lock(db, timeout=0.3):
                    pass

    def test_context_release_then_reacquire(self, tmp_path):
        db = tmp_path / "r.duckdb"
        for _ in range(3):
            with duckdb_write_lock(db, timeout=2.0):
                pass  # 每次释放后下次可再获取
