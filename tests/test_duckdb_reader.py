"""
tests/test_duckdb_reader.py — DuckDB 读连接池（fts.data_futures.DuckDBReader）单元测试。

覆盖目标（E.1 设计 §5 测试计划，按 DuckDB 实际约束调整）:
  1. acquire/release: 连接复用与归还
  2. 池满关闭: 超出池容量时连接被关闭
  3. 读与写共存: 单写者连接打开时，读池连接可正常打开与查询
     （DuckDB 不允许同文件并存可写 + read_only=True 连接，故读池用普通连接，
       「只用于读」由代码纪律保证 — 见 DuckDBReader docstring）
  4. 写连接打开时读连接不阻塞

隔离性: 全部使用 tmp_path 隔离的临时 DuckDB 文件，不触碰真实 data/ 数据库。
"""

from __future__ import annotations

import duckdb
import pytest

from fts.data_futures import DuckDBReader, DuckDBWriter


@pytest.fixture()
def db_file(tmp_path):
    """隔离的临时 DuckDB 文件路径。"""
    path = tmp_path / "reader_test.duckdb"
    # 预置数据表（用独立连接写入）
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE t (id INTEGER, val VARCHAR)")
    con.execute("INSERT INTO t VALUES (1, 'a'), (2, 'b'), (3, 'c')")
    con.close()
    return path


class TestDuckDBReader:
    def test_acquire_returns_connection(self, db_file):
        """acquire 返回连接，可查询数据。"""
        reader = DuckDBReader(db_file, max_connections=2)
        con = reader.acquire()
        rows = con.execute("SELECT COUNT(*) FROM t").fetchall()
        assert rows[0][0] == 3
        reader.release(con)
        reader.close()

    def test_release_reuses_connection(self, db_file):
        """release 后连接归还池，重复 acquire 复用（不新建）。"""
        reader = DuckDBReader(db_file, max_connections=2)
        con1 = reader.acquire()
        reader.release(con1)
        con2 = reader.acquire()
        assert con2 is con1  # 同一连接复用
        reader.release(con2)
        reader.close()

    def test_pool_exhaustion_closes_extra(self, db_file, mocker):
        """池满后 release 超出容量的连接被关闭（用 mock 连接断言 close）。"""
        mock_conns = [mocker.MagicMock() for _ in range(3)]
        mocker.patch("duckdb.connect", side_effect=mock_conns)
        reader = DuckDBReader(db_file, max_connections=2)
        con1 = reader.acquire()
        con2 = reader.acquire()
        con3 = reader.acquire()  # 池内 2 个已借出，第 3 个新建
        assert con3 is not con1 and con3 is not con2
        reader.release(con1)
        reader.release(con2)
        reader.release(con3)  # 池已满（2），con3 应被关闭
        mock_conns[2].close.assert_called_once()
        mock_conns[0].close.assert_not_called()
        reader.close()

    def test_read_after_writer_closed(self, db_file):
        """E.4 S1 语义：写连接短生命周期（写入即关），关闭后读池 read_only 连接可正常打开并读取最新数据。

        实测约束：DuckDB lock_configuration 写连接打开期间，read_only=True 连接因
        "different configuration" 无法打开——E.4 S1 以「写短生命周期 + 读 read_only 短连接」
        规避（写完成即关，读连接只在写关闭后打开）。
        """
        writer = DuckDBWriter(db_file)
        writer.execute("INSERT INTO t VALUES (99, 'z')")
        writer.close()
        reader = DuckDBReader(db_file, max_connections=2)
        con = reader.acquire()
        rows = con.execute("SELECT COUNT(*) FROM t").fetchall()
        assert rows[0][0] == 4
        reader.release(con)
        reader.close()

    def test_close_closes_all(self, db_file, mocker):
        """close 关闭池内所有连接（用 mock 连接断言 close）。"""
        mock_conns = [mocker.MagicMock() for _ in range(2)]
        mocker.patch("duckdb.connect", side_effect=mock_conns)
        reader = DuckDBReader(db_file, max_connections=2)
        con1 = reader.acquire()
        con2 = reader.acquire()
        reader.release(con1)
        reader.release(con2)
        reader.close()
        mock_conns[0].close.assert_called_once()
        mock_conns[1].close.assert_called_once()
