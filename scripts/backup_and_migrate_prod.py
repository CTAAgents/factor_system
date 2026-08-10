"""scripts/backup_and_migrate_prod.py — 备份生产 DB 并执行迁移。

HARNESS §5.7 风险控制: 生产 DB 迁移前必须先备份。
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fts.data_sources.migrate import migrate_schema  # noqa: E402

PROD_DB = ROOT / "data" / "fts_history.duckdb"
BACKUP_DIR = ROOT / "data" / "backup"


def main() -> int:
    if not PROD_DB.exists():
        print(f"生产 DB 不存在: {PROD_DB}")
        return 1

    # ─── Step 1: 备份 ───
    print("=" * 70)
    print("Step 1: 备份生产 DB")
    print("=" * 70)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"fts_history.duckdb.bak.{ts}"

    src_size = PROD_DB.stat().st_size
    print(f"  源:   {PROD_DB}  ({src_size:,} bytes)")
    print(f"  目标: {backup_path}")
    t0 = time.perf_counter()
    shutil.copy2(str(PROD_DB), str(backup_path))
    elapsed = time.perf_counter() - t0
    bak_size = backup_path.stat().st_size
    assert bak_size == src_size, f"备份大小不一致: {bak_size} != {src_size}"
    print(f"  ✓ 备份完成 ({elapsed:.2f}s, {bak_size:,} bytes)")

    # ─── Step 2: 备份前数据快照 ───
    print()
    print("=" * 70)
    print("Step 2: 备份前数据快照")
    print("=" * 70)
    con_bak = duckdb.connect(str(backup_path), read_only=True)
    try:
        bak_count = con_bak.execute("SELECT count(*) FROM kline_cache").fetchone()[0]
        bak_cols = {
            r[0]
            for r in con_bak.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='kline_cache'"
            ).fetchall()
        }
        print(f"  kline_cache 条数: {bak_count:,}")
        print(f"  kline_cache 列数: {len(bak_cols)}  → {sorted(bak_cols)}")
    finally:
        con_bak.close()

    # ─── Step 3: 执行迁移 ───
    print()
    print("=" * 70)
    print("Step 3: 执行 migrate_schema")
    print("=" * 70)
    t0 = time.perf_counter()
    result = migrate_schema(PROD_DB)
    elapsed = time.perf_counter() - t0
    print(f"  返回: {json.dumps(result, ensure_ascii=False)}")
    print(f"  耗时: {elapsed * 1000:.1f} ms")

    # ─── Step 4: 迁移后验证 ───
    print()
    print("=" * 70)
    print("Step 4: 迁移后验证")
    print("=" * 70)
    con = duckdb.connect(str(PROD_DB), read_only=True)
    try:
        # 表清单
        tables = {
            r[0]
            for r in con.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
            ).fetchall()
        }
        print(f"  表清单: {sorted(tables)}")
        for t in ("edb_cache", "option_chain_cache"):
            assert t in tables, f"缺表: {t}"
        print("  ✓ edb_cache + option_chain_cache 已建")

        # kline_cache 字段
        cols = {
            r[0]
            for r in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='kline_cache'"
            ).fetchall()
        }
        new_cols = {"hold", "settle", "pre_settle", "oi_change", "vwap", "source", "fetched_at", "trace_id"}
        assert new_cols <= cols, f"缺新字段: {new_cols - cols}"
        print(f"  ✓ kline_cache 字段: {len(cols)} 列  ({sorted(cols)})")

        # 数据保留
        new_count = con.execute("SELECT count(*) FROM kline_cache").fetchone()[0]
        assert new_count == bak_count, f"数据丢失: {bak_count} → {new_count}"
        print(f"  ✓ kline_cache 条数: {new_count:,}  (与备份一致，未丢失)")

        # 新字段默认为 NULL（DuckDB ALTER ADD COLUMN 默认行为）
        nulls = con.execute(
            "SELECT "
            "sum(CASE WHEN hold IS NULL THEN 1 ELSE 0 END), "
            "sum(CASE WHEN settle IS NULL THEN 1 ELSE 0 END), "
            "sum(CASE WHEN vwap IS NULL THEN 1 ELSE 0 END), "
            "sum(CASE WHEN source IS NULL THEN 1 ELSE 0 END), "
            "sum(CASE WHEN trace_id IS NULL THEN 1 ELSE 0 END) "
            "FROM kline_cache"
        ).fetchone()
        print(
            f"  新字段 NULL 计数: hold={nulls[0]:,}, settle={nulls[1]:,}, "
            f"vwap={nulls[2]:,}, source={nulls[3]:,}, trace_id={nulls[4]:,}"
        )
        print("  (旧数据的新字段默认 NULL — 等适配器写入新数据时填充)")

        # 索引
        idx = {
            r[0]
            for r in con.execute("SELECT index_name FROM duckdb_indexes() WHERE table_name='kline_cache'").fetchall()
        }
        assert "idx_kline_symbol_date_source" in idx
        print(f"  ✓ 索引: {sorted(idx)}")

        # 旧字段仍可查
        sample = con.execute("SELECT symbol, date, close FROM kline_cache ORDER BY date DESC LIMIT 3").fetchall()
        print("  最新 3 条样本（验证旧字段仍可读）:")
        for s, d, c in sample:
            print(f"    {s:<10} {str(d):<12} close={c}")
    finally:
        con.close()

    # ─── Step 5: 备份设为只读保护 ───
    print()
    print("=" * 70)
    print("Step 5: 保护备份（设为只读）")
    print("=" * 70)
    try:
        import os

        os.chmod(str(backup_path), 0o444)
        print(f"  ✓ 备份已设为只读: {backup_path}")
    except Exception as e:
        print(f"  ⚠ 设置只读失败（Windows 权限模型不同）: {e}")

    print()
    print("=" * 70)
    print("✅ 迁移完成")
    print("=" * 70)
    print(f"  生产 DB: {PROD_DB}  ({PROD_DB.stat().st_size:,} bytes)")
    print(f"  备份:    {backup_path}  ({backup_path.stat().st_size:,} bytes)")
    print(f"  回滚命令: copy {backup_path} {PROD_DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
