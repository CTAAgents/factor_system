"""fts.data_sources.migrate — DuckDB 表结构迁移脚本（v2.3.0）。

HARNESS §5.3 契约优先: 本脚本只动 schema，不动数据。
幂等可重入: 启动时自动执行，重跑不应报错且 counts=0。

迁移内容:
    - kline_cache     扩到 16 列（旧 8 + 新 8：hold/settle/pre_settle/oi_change/
                                            vwap/source/fetched_at/trace_id）
    - edb_cache       全新表（宏观/产业链 EDB 数据）
    - option_chain_cache 全新表（期权 IV/PCR 数据）
    - idx_kline_symbol_date_source  (symbol, date, source) 联合索引
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger(__name__)


# ─── 字段定义 ──────────────────────────────────────────────


# kline_cache 完整 16 列定义（v2.3.0 schema）。
# 旧版（v2.2.1）只有前 9 列；新版增量补充后 8 列。
KLINE_CACHE_FULL_COLUMNS: list[tuple[str, str]] = [
    ("symbol", "VARCHAR"),
    ("period", "VARCHAR"),
    ("date", "DATE"),
    ("open", "DOUBLE"),
    ("high", "DOUBLE"),
    ("low", "DOUBLE"),
    ("close", "DOUBLE"),
    ("volume", "DOUBLE"),
    ("amount", "DOUBLE"),
    ("hold", "DOUBLE"),
    ("settle", "DOUBLE"),
    ("pre_settle", "DOUBLE"),
    ("oi_change", "DOUBLE"),
    ("vwap", "DOUBLE"),
    ("source", "VARCHAR"),
    ("fetched_at", "TIMESTAMP"),
    ("trace_id", "VARCHAR"),
]

# 旧版（v2.2.1）的 9 列（兼容检测用）。
KLINE_CACHE_LEGACY_COLUMNS: set[str] = {
    "symbol", "period", "date", "open", "high", "low", "close", "volume", "amount",
}

# v2.3.0 增量新增的 8 列。
KLINE_CACHE_NEW_COLUMNS: list[tuple[str, str]] = [
    ("hold", "DOUBLE"),
    ("settle", "DOUBLE"),
    ("pre_settle", "DOUBLE"),
    ("oi_change", "DOUBLE"),
    ("vwap", "DOUBLE"),
    ("source", "VARCHAR"),
    ("fetched_at", "TIMESTAMP"),
    ("trace_id", "VARCHAR"),
]


# ─── DDL 模板 ──────────────────────────────────────────────


# 全新 DB 上 kline_cache 完整建表 DDL。
KLINE_CACHE_CREATE_DDL: str = """
CREATE TABLE IF NOT EXISTS kline_cache (
    symbol      VARCHAR,
    period      VARCHAR,
    date        DATE,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      DOUBLE,
    amount      DOUBLE,
    hold        DOUBLE,
    settle      DOUBLE,
    pre_settle  DOUBLE,
    oi_change   DOUBLE,
    vwap        DOUBLE,
    source      VARCHAR,
    fetched_at  TIMESTAMP,
    trace_id    VARCHAR
)
"""

EDB_CACHE_DDL: str = """
CREATE TABLE IF NOT EXISTS edb_cache (
    indicator   VARCHAR NOT NULL,
    date        DATE    NOT NULL,
    value       DOUBLE,
    unit        VARCHAR,
    source      VARCHAR,
    fetched_at  TIMESTAMP,
    trace_id    VARCHAR,
    PRIMARY KEY (indicator, date, source)
)
"""

OPTION_CHAIN_CACHE_DDL: str = """
CREATE TABLE IF NOT EXISTS option_chain_cache (
    underlying  VARCHAR,
    contract    VARCHAR NOT NULL,
    date        DATE    NOT NULL,
    type        VARCHAR,
    strike      DOUBLE,
    last        DOUBLE,
    bid         DOUBLE,
    ask         DOUBLE,
    volume      DOUBLE,
    oi          DOUBLE,
    iv          DOUBLE,
    source      VARCHAR,
    fetched_at  TIMESTAMP,
    trace_id    VARCHAR,
    PRIMARY KEY (contract, date, source)
)
"""


# ─── 内部辅助函数 ──────────────────────────────────────────


def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """检查表是否存在。"""
    row = con.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema='main' AND table_name=?",
        [table_name],
    ).fetchone()
    return bool(row and row[0] > 0)


def _index_exists(con: duckdb.DuckDBPyConnection, table_name: str, index_name: str) -> bool:
    """检查索引是否存在。"""
    rows = con.execute(
        "SELECT index_name FROM duckdb_indexes() WHERE table_name=?",
        [table_name],
    ).fetchall()
    return any(r[0] == index_name for r in rows)


def _get_existing_columns(
    con: duckdb.DuckDBPyConnection, table_name: str
) -> set[str]:
    """获取表的现有列名集合。"""
    rows = con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    # PRAGMA table_info 返回: (cid, name, type, notnull, dflt_value, pk)
    return {r[1] for r in rows}


def _add_missing_columns(
    con: duckdb.DuckDBPyConnection, table_name: str, new_columns: list[tuple[str, str]]
) -> int:
    """向已存在表添加缺失列，返回实际新增的列数。"""
    existing = _get_existing_columns(con, table_name)
    added = 0
    for col_name, col_type in new_columns:
        if col_name in existing:
            continue
        con.execute(f'ALTER TABLE "{table_name}" ADD COLUMN "{col_name}" {col_type}')
        added += 1
    return added


def _create_table_if_absent(
    con: duckdb.DuckDBPyConnection, table_name: str, ddl: str
) -> bool:
    """如果表不存在则按给定 DDL 创建，返回是否新建。"""
    if _table_exists(con, table_name):
        return False
    con.execute(ddl)
    return True


def _create_index_if_absent(
    con: duckdb.DuckDBPyConnection,
    table_name: str,
    index_name: str,
    index_ddl: str,
) -> bool:
    """如果索引不存在则创建，返回是否新建。"""
    if _index_exists(con, table_name, index_name):
        return False
    con.execute(index_ddl)
    return True


# ─── 主函数 ────────────────────────────────────────────────


def migrate_schema(db_path: str | Path) -> dict[str, int]:
    """执行 DuckDB schema 迁移（幂等可重入）。

    处理场景:
        - 全新 DB  : 依次创建 kline_cache (17 列) + edb_cache + option_chain_cache
                     + idx_kline_symbol_date_source 索引
        - 旧版 DB  : 保留 kline_cache 既有数据，ALTER 追加 8 个新列
                     + IF NOT EXISTS 建 edb_cache / option_chain_cache / 索引
        - 二次执行  : 全部已存在，counts 全部为 0

    Args:
        db_path: DuckDB 文件路径。父目录不存在时自动创建。

    Returns:
        {
            "columns_added":   int,   # 本次实际 ALTER ADD COLUMN 的数量
            "tables_created":  int,   # 本次实际 CREATE TABLE 的数量
            "indexes_created": int,   # 本次实际 CREATE INDEX 的数量
        }
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path))
    try:
        # 1) kline_cache 处理：旧版扩列 / 不存在则创建
        columns_added = 0
        if _table_exists(con, "kline_cache"):
            # 旧版表：ALTER 追加缺失的 8 列
            columns_added = _add_missing_columns(
                con, "kline_cache", KLINE_CACHE_NEW_COLUMNS
            )
        else:
            # 全新 DB：按 17 列 schema 一次性创建（不算 columns_added）
            con.execute(KLINE_CACHE_CREATE_DDL)

        # 2) edb_cache / option_chain_cache：IF NOT EXISTS
        tables_created = 0
        if _create_table_if_absent(con, "edb_cache", EDB_CACHE_DDL):
            tables_created += 1
        if _create_table_if_absent(con, "option_chain_cache", OPTION_CHAIN_CACHE_DDL):
            tables_created += 1

        # 3) 索引：IF NOT EXISTS
        indexes_created = 0
        index_ddl = (
            "CREATE INDEX IF NOT EXISTS idx_kline_symbol_date_source "
            "ON kline_cache(symbol, date, source)"
        )
        if _create_index_if_absent(
            con, "kline_cache", "idx_kline_symbol_date_source", index_ddl
        ):
            indexes_created += 1
    finally:
        con.close()

    logger.info(
        "[migrate_schema] db=%s columns_added=%d tables_created=%d indexes_created=%d",
        db_path,
        columns_added,
        tables_created,
        indexes_created,
    )
    return {
        "columns_added": columns_added,
        "tables_created": tables_created,
        "indexes_created": indexes_created,
    }


__all__ = [
    "migrate_schema",
    "KLINE_CACHE_FULL_COLUMNS",
    "KLINE_CACHE_LEGACY_COLUMNS",
    "KLINE_CACHE_NEW_COLUMNS",
    "KLINE_CACHE_CREATE_DDL",
    "EDB_CACHE_DDL",
    "OPTION_CHAIN_CACHE_DDL",
]
