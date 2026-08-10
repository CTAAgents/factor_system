"""
tests/test_duckdb_writer.py — DuckDB 单写者（fts.data_futures.DuckDBWriter）单元测试。

覆盖目标（E.1 设计 §5 测试计划）:
  1. 写锁互斥: 多线程并发写不产生 ConcurrentTransactionException（结构消除）
  2. 批量 COPY: 与逐条 INSERT 数据一致
  3. 写事务异常回滚: 不留半写入状态
  4. executemany: 批量写入正确
  5. close: 关闭后释放资源

隔离性: 全部使用 tmp_path 隔离的临时 DuckDB 文件，不触碰真实 data/ 数据库。
"""

from __future__ import annotations

import threading

import duckdb
import pytest

from fts.data_futures import DuckDBWriter


@pytest.fixture()
def db_file(tmp_path):
    """隔离的临时 DuckDB 文件路径。"""
    return tmp_path / "writer_test.duckdb"


@pytest.fixture()
def writer(db_file):
    w = DuckDBWriter(db_file)
    w.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER, name VARCHAR)")
    yield w
    w.close()


class TestDuckDBWriter:
    def test_conn_writable(self, writer):
        """writer 连接可正常写入（DuckDBWriter 构造即建连）。"""
        writer.execute("INSERT INTO t VALUES (?, ?)", [1, "a"])
        rows = writer.query("SELECT COUNT(*) FROM t")
        assert rows[0][0] == 1

    def test_write_single_and_query(self, writer):
        """单条写入 + 查询回读一致。"""
        writer.execute("INSERT INTO t VALUES (?, ?)", [7, "seven"])
        writer.execute("INSERT INTO t VALUES (?, ?)", [8, "eight"])
        rows = writer.query("SELECT id, name FROM t ORDER BY id")
        assert [(r[0], r[1]) for r in rows] == [(7, "seven"), (8, "eight")]

    def test_executemany_batch(self, writer):
        """executemany 批量写入全部落库。"""
        writer.executemany(
            "INSERT INTO t VALUES (?, ?)",
            [(i, f"n{i}") for i in range(10)],
        )
        count = writer.query("SELECT COUNT(*) FROM t")[0][0]
        assert count == 10

    def test_execute_is_atomic_on_error(self, db_file):
        """单条 execute 原子性：失败语句不产生半写入，已有提交数据不受损。

        DuckDB execute 单条自动提交（原子），失败语句对已有数据无影响，
        且连接在失败后可继续使用。
        """
        w = DuckDBWriter(db_file)
        w.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER, name VARCHAR)")
        w.execute("INSERT INTO t VALUES (?, ?)", [1, "ok"])
        with pytest.raises(Exception):
            w.execute("INSERT INTO non_existent_table VALUES (1)")  # 失败语句
        # 已有提交数据完整，连接可用
        rows = w.query("SELECT COUNT(*) FROM t")
        assert rows[0][0] == 1
        w.execute("INSERT INTO t VALUES (?, ?)", [3, "after"])
        count = w.query("SELECT COUNT(*) FROM t")[0][0]
        assert count == 2
        w.close()

    def test_executemany_is_atomic_on_error(self, db_file):
        """executemany 批量写入：唯一约束冲突时整体失败，不留半写入。"""
        w = DuckDBWriter(db_file)
        w.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER UNIQUE, name VARCHAR)")
        # 前 3 条合法 + 第 4 条 id 重复（唯一约束冲突）
        with pytest.raises(Exception):
            w.executemany(
                "INSERT INTO t VALUES (?, ?)",
                [(1, "a"), (2, "b"), (3, "c"), (1, "dup")],
            )
        count = w.query("SELECT COUNT(*) FROM t")[0][0]
        # DuckDB executemany 为单语句批处理：冲突失败后不产生部分落库
        assert count == 0
        w.close()

    def test_write_after_error_recovers(self, writer):
        """异常回滚后 writer 仍可用（连接未损坏）。"""
        writer.execute("INSERT INTO t VALUES (?, ?)", [1, "a"])
        with pytest.raises(Exception):
            writer.execute("SELECT * FROM missing_table")
        writer.execute("INSERT INTO t VALUES (?, ?)", [2, "b"])
        count = writer.query("SELECT COUNT(*) FROM t")[0][0]
        assert count == 2

    def test_thread_safety_no_conflict(self, db_file):
        """多线程并发写：单写者锁保证零冲突异常（E.1 验收标准 #1）。

        8 线程 × 50 条并发写入，断言无异常且全部落库。
        """
        w = DuckDBWriter(db_file)
        w.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER, name VARCHAR)")
        errors: list[Exception] = []

        def worker(worker_id: int):
            try:
                for i in range(50):
                    w.execute("INSERT INTO t VALUES (?, ?)", [worker_id * 100 + i, f"w{worker_id}"])
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert errors == [], f"并发写产生异常: {errors}"
        count = w.query("SELECT COUNT(*) FROM t")[0][0]
        assert count == 8 * 50
        w.close()

    def test_batch_copy_consistency(self, writer):
        """批量 COPY 与逐条 INSERT 数据一致（E.1 验收标准 #3）。"""
        writer.execute("CREATE TABLE IF NOT EXISTS t_copy (id INTEGER, val DOUBLE)")
        writer.copy_from_records(
            "t_copy",
            ["id", "val"],
            [(i, float(i) * 1.5) for i in range(100)],
        )
        # 对照: 另一表逐条写入同数据
        writer.execute("CREATE TABLE IF NOT EXISTS t_ins (id INTEGER, val DOUBLE)")
        writer.executemany(
            "INSERT INTO t_ins VALUES (?, ?)",
            [(i, float(i) * 1.5) for i in range(100)],
        )
        copy_rows = writer.query("SELECT id, val FROM t_copy ORDER BY id")
        ins_rows = writer.query("SELECT id, val FROM t_ins ORDER BY id")
        assert [(r[0], r[1]) for r in copy_rows] == [(r[0], r[1]) for r in ins_rows]

    def test_batch_copy_empty(self, writer):
        """空记录批量 COPY 为 no-op 不报错。"""
        writer.execute("CREATE TABLE IF NOT EXISTS t_copy (id INTEGER)")
        writer.copy_from_records("t_copy", ["id"], [])
        count = writer.query("SELECT COUNT(*) FROM t_copy")[0][0]
        assert count == 0

    def test_close_releases_connection(self, db_file):
        """close 后连接关闭，文件可被重新打开。"""
        w = DuckDBWriter(db_file)
        w.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")
        w.close()
        # close 后可重新连接（文件未被占用）
        con = duckdb.connect(str(db_file), read_only=False)
        con.close()
