"""tests/data_sources/test_migrate.py — DuckDB schema 迁移测试。

HARNESS §5.4 测试随重构: 任何 schema 变更必须同步更新本测试。
幂等性是硬性要求（启动时自动执行，重跑不应报错）。
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ─── Fixture：每个测试用临时 DuckDB 文件 ───────────────────


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """全新空 DuckDB 文件（无任何表）。"""
    return tmp_path / "test_migrate.duckdb"


@pytest.fixture
def legacy_db(tmp_path: Path) -> Path:
    """旧版 kline_cache（v2.2.1 字段：symbol/period/date/ohlc/volume/amount）。"""
    import duckdb

    db = tmp_path / "legacy.duckdb"
    con = duckdb.connect(str(db))
    try:
        con.execute("""
            CREATE TABLE kline_cache (
                symbol VARCHAR,
                period VARCHAR,
                date DATE,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                volume DOUBLE, amount DOUBLE
            )
        """)
        con.execute("""
            INSERT INTO kline_cache VALUES
                ('RB0', 'daily', '2026-08-01', 3500, 3550, 3490, 3540, 100000, 350000000),
                ('CU0', 'daily', '2026-08-01', 70000, 70500, 69800, 70300, 50000, 3500000000)
        """)
        con.commit()
    finally:
        con.close()
    return db


# ─── 全新 DB 上的迁移 ───────────────────────────────────


def test_migrate_creates_kline_cache_on_empty_db(fresh_db):
    """空 DB 上执行迁移，应创建 kline_cache 表。"""
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(fresh_db)
    assert fresh_db.exists()

    import duckdb

    con = duckdb.connect(str(fresh_db))
    try:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        ]
        assert "kline_cache" in tables
    finally:
        con.close()


def test_migrate_creates_edb_cache(fresh_db):
    """新建 edb_cache 表，含 7 字段 + 主键 (indicator, date, source)。"""
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(fresh_db)

    import duckdb

    con = duckdb.connect(str(fresh_db))
    try:
        cols = con.execute("DESCRIBE edb_cache").fetchall()
        col_names = {c[0] for c in cols}
        assert {"indicator", "date", "value", "unit", "source", "fetched_at", "trace_id"} <= col_names
    finally:
        con.close()


def test_migrate_creates_option_chain_cache(fresh_db):
    """新建 option_chain_cache 表，含 13 字段 + 主键 (contract, date, source)。"""
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(fresh_db)

    import duckdb

    con = duckdb.connect(str(fresh_db))
    try:
        cols = con.execute("DESCRIBE option_chain_cache").fetchall()
        col_names = {c[0] for c in cols}
        assert {
            "underlying",
            "contract",
            "date",
            "type",
            "strike",
            "last",
            "bid",
            "ask",
            "volume",
            "oi",
            "iv",
            "source",
            "fetched_at",
            "trace_id",
        } <= col_names
    finally:
        con.close()


# ─── 旧版 DB 上的迁移（不破坏既有数据） ──────────────────


def test_migrate_adds_9_columns_to_legacy_kline_cache(legacy_db):
    """旧版 kline_cache（8 列）应扩到 17 列（新增 hold/settle/pre_settle/oi_change/vwap/source/fetched_at/trace_id + adj_factor）。"""
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(legacy_db)

    import duckdb

    con = duckdb.connect(str(legacy_db))
    try:
        cols = con.execute("DESCRIBE kline_cache").fetchall()
        col_names = {c[0] for c in cols}
        # 旧字段仍在
        for old in ("symbol", "period", "date", "open", "high", "low", "close", "volume", "amount"):
            assert old in col_names, f"旧字段 {old} 丢失"
        # 新字段已加（v2.3.0 的 8 列 + v2.58.0 的 adj_factor）
        for new in (
            "hold",
            "settle",
            "pre_settle",
            "oi_change",
            "vwap",
            "source",
            "fetched_at",
            "trace_id",
            "adj_factor",
        ):
            assert new in col_names, f"新字段 {new} 缺失"
    finally:
        con.close()


def test_migrate_preserves_existing_data(legacy_db):
    """迁移不得破坏既有 kline_cache 数据。"""
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(legacy_db)

    import duckdb

    con = duckdb.connect(str(legacy_db))
    try:
        rows = con.execute("SELECT symbol, close FROM kline_cache ORDER BY symbol").fetchall()
        assert rows == [("CU0", 70300.0), ("RB0", 3540.0)]
    finally:
        con.close()


# ─── 索引创建 ───────────────────────────────────────────


def test_migrate_creates_symbol_date_source_index(fresh_db):
    """创建索引 idx_kline_symbol_date_source(symbol, date, source)。"""
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(fresh_db)

    import duckdb

    con = duckdb.connect(str(fresh_db))
    try:
        indexes = [
            r[0]
            for r in con.execute("SELECT index_name FROM duckdb_indexes() WHERE table_name='kline_cache'").fetchall()
        ]
        assert "idx_kline_symbol_date_source" in indexes
    finally:
        con.close()


# ─── 幂等性 ─────────────────────────────────────────────


def test_migrate_is_idempotent_on_fresh_db(fresh_db):
    """重跑迁移不应报错（幂等可重入）。"""
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(fresh_db)
    r2 = migrate_schema(fresh_db)  # 第二次

    # 第二次 columns_added 应为 0（所有列已存在）
    assert r2["columns_added"] == 0
    # 第二次 tables_created 应为 0（IF NOT EXISTS）
    assert r2["tables_created"] == 0


def test_migrate_is_idempotent_on_legacy_db(legacy_db):
    """旧版 DB 重跑也不报错。"""
    from fts.data_sources.migrate import migrate_schema

    migrate_schema(legacy_db)
    r2 = migrate_schema(legacy_db)

    # 第二次应不重复加列
    assert r2["columns_added"] == 0


# ─── 返回值结构 ─────────────────────────────────────────


def test_migrate_returns_dict_with_change_counts(fresh_db):
    """返回 dict 含 columns_added / tables_created / indexes_created 三个计数。"""
    from fts.data_sources.migrate import migrate_schema

    result = migrate_schema(fresh_db)

    assert isinstance(result, dict)
    assert "columns_added" in result
    assert "tables_created" in result
    assert "indexes_created" in result
    # 全新 DB：kline_cache 全新创建（不算 columns_added）
    # tables_created: kline_cache + contract_kline + minute_cache + edb_cache + option_chain_cache + tick_cache = 6（v2.58.0）
    # indexes_created: 1
    assert result["tables_created"] == 6
    assert result["indexes_created"] == 1


def test_migrate_legacy_db_returns_9_columns_added(legacy_db):
    """旧版 DB 迁移应返回 columns_added=9（v2.3.0 新增 8 列 + v2.58.0 adj_factor 列）。"""
    from fts.data_sources.migrate import migrate_schema

    result = migrate_schema(legacy_db)

    assert result["columns_added"] == 9


# ─── 路径处理 ───────────────────────────────────────────


def test_migrate_creates_parent_directory(tmp_path: Path):
    """DB 父目录不存在时应自动创建。"""
    from fts.data_sources.migrate import migrate_schema

    nested = tmp_path / "subdir" / "deep" / "fts.duckdb"
    assert not nested.parent.exists()

    migrate_schema(nested)

    assert nested.exists()
    assert nested.parent.is_dir()
