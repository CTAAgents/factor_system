"""scripts/check_kline_cache_count.py — 查 kline_cache 数据条数"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    data_dir = ROOT / "data"
    if not data_dir.exists():
        print(f"data/ 目录不存在: {data_dir}")
        return 1

    # 1) 列 data/ 下所有 DuckDB 文件
    dbs = sorted(data_dir.glob("*.duckdb"))
    print("=" * 70)
    print("data/ 目录下的 DuckDB 文件")
    print("=" * 70)
    if not dbs:
        print("  (无)")
    for p in dbs:
        print(f"  {p.name:<40}  {p.stat().st_size:>10,} bytes")

    # 2) 重点查生产 DB
    prod = data_dir / "fts_history.duckdb"
    print()
    print("=" * 70)
    print(f"生产 DB: {prod}")
    print("=" * 70)
    if not prod.exists():
        print("  不存在")
        return 0

    con = duckdb.connect(str(prod), read_only=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name"
        ).fetchall()]
        print(f"  表清单: {tables}")

        if "kline_cache" not in tables:
            print("  kline_cache 表不存在（未迁移？）")
            return 0

        cnt = con.execute("SELECT count(*) FROM kline_cache").fetchone()[0]
        print(f"  kline_cache 总条数: {cnt:,}")

        if cnt == 0:
            print("  (空表)")
            return 0

        print()
        print("  Top 10 品种 (按记录数):")
        symbols = con.execute(
            "SELECT symbol, count(*) AS n FROM kline_cache "
            "GROUP BY symbol ORDER BY n DESC LIMIT 10"
        ).fetchall()
        for s, n in symbols:
            print(f"    {s:<10} {n:>10,}")

        print()
        print("  按 period 分组:")
        for p, n in con.execute(
            "SELECT period, count(*) FROM kline_cache GROUP BY period"
        ).fetchall():
            print(f"    {p:<10} {n:>10,}")

        print()
        dr = con.execute("SELECT min(date), max(date) FROM kline_cache").fetchone()
        print(f"  日期范围: {dr[0]} ~ {dr[1]}")

        print()
        cols = {r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='kline_cache' ORDER BY ordinal_position"
        ).fetchall()}
        new_cols = {"hold", "settle", "pre_settle", "oi_change",
                    "vwap", "source", "fetched_at", "trace_id"}
        if new_cols <= cols:
            print("  按 source 分布 (v2.3.0 新字段):")
            for s, n in con.execute(
                "SELECT COALESCE(source, '<NULL>') AS src, count(*) "
                "FROM kline_cache GROUP BY src ORDER BY count(*) DESC"
            ).fetchall():
                print(f"    {s:<15} {n:>10,}")
        else:
            missing = new_cols - cols
            print(f"  ⚠ kline_cache 仍为 v2.2.1 旧版 schema（缺字段: {sorted(missing)}）")
            print(f"  当前共 {len(cols)} 列: {sorted(cols)}")
            print(f"  需先执行 migrate_schema() 升级到 v2.3.0 (17 列)")
    finally:
        con.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
