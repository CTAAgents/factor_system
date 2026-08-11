"""P3-B 行情库冷热归档脚本测试（plans/29，用临时库隔离真实行情库）"""

import sys
from pathlib import Path

import duckdb
import pytest

_FTS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from scripts.archive_history_cold import (  # noqa: E402
    DEFAULT_TABLE,
    DEFAULT_UNTIL_YEAR,
    _archive_file,
    _connect,
    _min_year,
    _year_counts,
    archive,
    dry_run,
    verify,
)


def _make_db(path: Path) -> None:
    """造 kline_cache 数据（date 存 VARCHAR，模拟真实库列类型）。"""
    con = duckdb.connect(str(path))
    con.execute(
        """
        CREATE TABLE kline_cache (
            symbol VARCHAR, period VARCHAR, date VARCHAR,
            open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
            volume DOUBLE, amount DOUBLE, hold DOUBLE, settle DOUBLE,
            pre_settle DOUBLE, oi_change DOUBLE, vwap DOUBLE,
            source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR, adj_factor DOUBLE
        )
        """
    )
    rows = [
        ("RB0", "1d", "2012-01-03", 1.0, 2.0, 1.0, 1.5, 100, 0, 0, 0, 0, 0, 1.5, "T", "2026-01-01", "t1", 1.0),
        ("RB0", "1d", "2012-01-04", 1.5, 2.5, 1.5, 2.0, 120, 0, 0, 0, 0, 0, 2.0, "T", "2026-01-01", "t2", 1.0),
        ("RB0", "1d", "2013-06-01", 2.0, 3.0, 2.0, 2.5, 130, 0, 0, 0, 0, 0, 2.5, "T", "2026-01-01", "t3", 1.0),
        ("RB0", "1d", "2015-03-01", 2.5, 3.5, 2.5, 3.0, 140, 0, 0, 0, 0, 0, 3.0, "T", "2026-01-01", "t4", 1.0),
        ("RB0", "1d", "2020-07-01", 3.0, 4.0, 3.0, 3.5, 150, 0, 0, 0, 0, 0, 3.5, "T", "2026-01-01", "t5", 1.0),
    ]
    con.executemany(
        "INSERT INTO kline_cache VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
    )
    con.close()


class TestYearCounts:
    def test_year_counts(self, tmp_path):
        db = tmp_path / "hist.duckdb"
        _make_db(db)
        con = _connect(db)
        try:
            counts = _year_counts(con, DEFAULT_TABLE)
            assert counts == {2012: 2, 2013: 1, 2015: 1, 2020: 1}
        finally:
            con.close()

    def test_min_year(self, tmp_path):
        db = tmp_path / "hist.duckdb"
        _make_db(db)
        con = _connect(db)
        try:
            assert _min_year(con, DEFAULT_TABLE) == 2012
        finally:
            con.close()


class TestDryRun:
    def test_dry_run_counts(self, tmp_path):
        db = tmp_path / "hist.duckdb"
        _make_db(db)
        res = dry_run(db, DEFAULT_TABLE, DEFAULT_UNTIL_YEAR)
        assert res["mode"] == "dry-run"
        assert res["total"] == 5
        assert res["archivable_rows"] == 3  # 2012×2 + 2013×1
        assert res["archivable_years"] == {"2012": 2, "2013": 1}

    def test_dry_run_missing_table_raises(self, tmp_path):
        # 库存在但无 kline_cache 表 → 抛异常（non-RuntimeError 视为失败透明）
        db = tmp_path / "empty.duckdb"
        duckdb.connect(str(db)).close()
        with pytest.raises(Exception):
            dry_run(db, DEFAULT_TABLE, DEFAULT_UNTIL_YEAR)


class TestArchiveAndVerify:
    def test_archive_and_verify_cycle(self, tmp_path):
        db = tmp_path / "hist.duckdb"
        archive_root = tmp_path / "archive"
        _make_db(db)
        until = 2013
        res = archive(db, DEFAULT_TABLE, until, archive_root)
        assert res["mode"] == "archive"
        assert res["deleted_rows"] == 3  # 2012×2 + 2013×1 导出并删除
        assert res["exported_files"] == {"2012": 2, "2013": 1}
        # 冷层文件存在
        assert _archive_file(archive_root, DEFAULT_TABLE, 2012).exists()
        assert _archive_file(archive_root, DEFAULT_TABLE, 2013).exists()
        # 热库剩余 2 行（2015/2020）
        con = _connect(db)
        try:
            assert con.execute("SELECT count(*) FROM kline_cache").fetchone()[0] == 2
        finally:
            con.close()
        # verify 一致
        v = verify(db, DEFAULT_TABLE, until, archive_root)
        assert v["consistent"] is True
        assert v["cold_rows"] == 3

    def test_archive_idempotent(self, tmp_path):
        db = tmp_path / "hist.duckdb"
        archive_root = tmp_path / "archive"
        _make_db(db)
        until = 2013
        archive(db, DEFAULT_TABLE, until, archive_root)
        # 二次归档：文件已存在跳过、无剩余可删行
        res2 = archive(db, DEFAULT_TABLE, until, archive_root)
        assert res2["exported_files"] == {}
        assert res2["deleted_rows"] == 0

    def test_verify_mismatch_detected(self, tmp_path):
        db = tmp_path / "hist.duckdb"
        archive_root = tmp_path / "archive"
        _make_db(db)
        until = 2013
        # 只归档 2012，缺少 2013 → verify 不一致
        archive(db, DEFAULT_TABLE, 2012, archive_root)
        v = verify(db, DEFAULT_TABLE, until, archive_root)
        assert v["consistent"] is False