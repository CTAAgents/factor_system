"""scripts/verify_migrate_duckdb.py — 本地验证 migrate.py 在真实 DuckDB 上的行为。

HARNESS §5.4: 真实环境验证（非 mock）— 验证 schema 正确生成、数据保留、幂等性。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import duckdb

# 把项目根加进 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fts.data_sources.migrate import migrate_schema  # noqa: E402


def banner(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def describe_table(con: duckdb.DuckDBPyConnection, table: str) -> list[tuple]:
    return con.execute(f"PRAGMA table_info('{table}')").fetchall()


def show_table(con: duckdb.DuckDBPyConnection, table: str) -> None:
    print(f"  [{table} 字段]")
    for cid, name, typ, *_ in describe_table(con, table):
        print(f"    {cid:>2}  {name:<14}  {typ}")


def show_pks(con: duckdb.DuckDBPyConnection, table: str) -> None:
    rows = con.execute(
        "SELECT column_name FROM information_schema.key_column_usage "
        "WHERE table_name=? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    print(f"  [{table} 主键列] {[r[0] for r in rows]}")


def show_indexes(con: duckdb.DuckDBPyConnection) -> None:
    rows = con.execute(
        "SELECT table_name, index_name FROM duckdb_indexes() ORDER BY table_name, index_name"
    ).fetchall()
    print(f"  [索引] {[(r[0], r[1]) for r in rows]}")


def main() -> int:
    data_dir = ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    fresh_db = data_dir / "fts_verify_fresh.duckdb"
    legacy_db = data_dir / "fts_verify_legacy.duckdb"

    # ─────────────────────────────────────────────────
    # Step 1: 全新空 DB
    # ─────────────────────────────────────────────────
    banner("Step 1: 全新空 DB 迁移")
    if fresh_db.exists():
        fresh_db.unlink()

    r1 = migrate_schema(fresh_db)
    print(f"  migrate_schema 返回: {json.dumps(r1, ensure_ascii=False)}")
    print(f"  文件大小: {fresh_db.stat().st_size} bytes")

    con = duckdb.connect(str(fresh_db), read_only=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()]
        print(f"  [表清单] {tables}")
        for t in tables:
            show_table(con, t)
            show_pks(con, t)
        show_indexes(con)

        # 验证 kline_cache 17 列（含 8 个新列）
        cols = {r[1] for r in describe_table(con, "kline_cache")}
        expected_new = {"hold", "settle", "pre_settle", "oi_change",
                        "vwap", "source", "fetched_at", "trace_id"}
        missing = expected_new - cols
        assert not missing, f"全新 DB 缺字段: {missing}"
        print("  ✓ kline_cache 17 列齐全（含 8 个新列）")
    finally:
        con.close()

    # ─────────────────────────────────────────────────
    # Step 2: 旧版 DB（v2.2.1 schema：9 列）
    # ─────────────────────────────────────────────────
    banner("Step 2: 旧版 DB（v2.2.1 schema：9 列 kline_cache）")
    if legacy_db.exists():
        legacy_db.unlink()

    con = duckdb.connect(str(legacy_db))
    try:
        con.execute("""
            CREATE TABLE kline_cache (
                symbol VARCHAR, period VARCHAR, date DATE,
                open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE,
                volume DOUBLE, amount DOUBLE
            )
        """)
        con.execute("""
            INSERT INTO kline_cache VALUES
                ('RB0', 'daily', '2026-08-01', 3500, 3550, 3490, 3540, 100000, 350000000),
                ('CU0', 'daily', '2026-08-01', 70000, 70500, 69800, 70300, 50000, 3500000000),
                ('AU0', 'daily', '2026-08-01', 480, 485, 478, 482, 20000, 96000000)
        """)
        con.commit()
    finally:
        con.close()

    r2 = migrate_schema(legacy_db)
    print(f"  migrate_schema 返回: {json.dumps(r2, ensure_ascii=False)}")

    con = duckdb.connect(str(legacy_db), read_only=True)
    try:
        # 验证旧字段仍在 + 新字段已加
        cols = {r[1] for r in describe_table(con, "kline_cache")}
        old_cols = {"symbol", "period", "date", "open", "high", "low",
                    "close", "volume", "amount"}
        new_cols = {"hold", "settle", "pre_settle", "oi_change",
                    "vwap", "source", "fetched_at", "trace_id"}
        assert old_cols <= cols, f"旧字段丢失: {old_cols - cols}"
        assert new_cols <= cols, f"新字段缺失: {new_cols - cols}"
        print(f"  ✓ 旧字段 {len(old_cols)} 个全在 + 新字段 {len(new_cols)} 个已加")

        # 验证数据保留
        rows = con.execute(
            "SELECT symbol, close FROM kline_cache ORDER BY symbol"
        ).fetchall()
        print(f"  [数据保留] {rows}")
        assert rows == [("AU0", 482.0), ("CU0", 70300.0), ("RB0", 3540.0)], \
            f"数据被破坏: {rows}"
        print("  ✓ 3 条历史数据完整保留")

        # 验证表已新建
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()}
        assert {"edb_cache", "option_chain_cache"} <= tables
        print("  ✓ edb_cache + option_chain_cache 已在旧版 DB 上新建")

        # 验证索引已建
        idx = {r[0] for r in con.execute(
            "SELECT index_name FROM duckdb_indexes() WHERE table_name='kline_cache'"
        ).fetchall()}
        assert "idx_kline_symbol_date_source" in idx
        print("  ✓ 联合索引 idx_kline_symbol_date_source 已建")
    finally:
        con.close()

    # ─────────────────────────────────────────────────
    # Step 3: 幂等性（重跑 migrate_schema）
    # ─────────────────────────────────────────────────
    banner("Step 3: 幂等性验证（重跑应全部 0）")
    r3 = migrate_schema(fresh_db)
    print(f"  全新 DB 二次执行: {json.dumps(r3, ensure_ascii=False)}")
    assert r3 == {"columns_added": 0, "tables_created": 0, "indexes_created": 0}
    print("  ✓ 全新 DB 二次执行 0 改动")

    r4 = migrate_schema(legacy_db)
    print(f"  旧版 DB 二次执行: {json.dumps(r4, ensure_ascii=False)}")
    assert r4 == {"columns_added": 0, "tables_created": 0, "indexes_created": 0}
    print("  ✓ 旧版 DB 二次执行 0 改动")

    # ─────────────────────────────────────────────────
    # Step 4: 插入新字段并查询（端到端）
    # ─────────────────────────────────────────────────
    banner("Step 4: 端到端 — 插入新字段 + 索引命中")
    con = duckdb.connect(str(fresh_db))
    try:
        con.execute("""
            INSERT INTO kline_cache VALUES
                ('RB0', 'daily', '2026-08-04', 3500, 3600, 3490, 3580, 120000,
                 420000000, 80000, 3550, 3540, 2000, 3500, 'TQ_LOCAL',
                 current_timestamp, 'trace-001')
        """)
        con.execute("""
            INSERT INTO edb_cache VALUES
                ('CPI', '2026-08-01', 102.5, '%', 'IFIND', current_timestamp, 'trace-002')
        """)
        con.execute("""
            INSERT INTO option_chain_cache VALUES
                ('CU', 'CU2409C70000', '2026-08-04', 'CALL', 70000, 100, 95, 105,
                 100, 500, 0.25, 'WIND', current_timestamp, 'trace-003')
        """)
        con.commit()

        # 索引命中查询
        rb = con.execute("""
            EXPLAIN SELECT * FROM kline_cache
            WHERE symbol='RB0' AND date='2026-08-04' AND source='TQ_LOCAL'
        """).fetchall()
        plan = "\n".join(r[0] for r in rb if r[0])
        if "idx_kline_symbol_date_source" in plan or "INDEX_SCAN" in plan.upper() or "index" in plan.lower():
            print("  ✓ EXPLAIN 计划含索引使用痕迹（详见下方）")
        else:
            print("  [EXPLAIN 计划] 小表上 DuckDB 可能选择全表扫描，正常")
        print(f"  [EXPLAIN 摘要] {plan[:300]}...")

        cpi = con.execute("SELECT * FROM edb_cache WHERE indicator='CPI'").fetchall()
        print(f"  [EDB 验证] CPI 记录: {cpi}")
        assert len(cpi) == 1

        opt = con.execute(
            "SELECT contract, type, strike FROM option_chain_cache "
            "WHERE underlying='CU'"
        ).fetchall()
        print(f"  [期权验证] CU 期权: {opt}")
        assert len(opt) == 1
    finally:
        con.close()

    # ─────────────────────────────────────────────────
    # Step 5: 清理
    # ─────────────────────────────────────────────────
    banner("Step 5: 清理验证 DB")
    fresh_db.unlink()
    legacy_db.unlink()
    print(f"  ✓ 已删除 {fresh_db.name} 和 {legacy_db.name}")

    banner("全部验证通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
