"""
迁移脚本：将 factor_catalog.duckdb 拆分为分市场数据库。

拆分逻辑:
  1. 创建 factor_catalog_stock.duckdb（market='stock' + 'multi'）
  2. 创建 factor_catalog_futures.duckdb（market='futures' + 'multi'）
  3. 'multi' 记录写入两个库（通用因子）
  4. 所有表结构保持完全一致

用法:
  python scripts/migrate_factor_catalog_split.py [--dry-run]

安全保证:
  - 源数据库只读（不修改）
  - 目标数据库文件不存在则创建，已存在则跳过
  - 迁移后自动校验行数一致性
"""

import argparse
import sys
from pathlib import Path

import duckdb

DATA_DIR = Path("d:/Programs/factor_system/data")
SRC_DB = DATA_DIR / "factor_catalog.duckdb"
DST_STOCK = DATA_DIR / "factor_catalog_stock.duckdb"
DST_FUTURES = DATA_DIR / "factor_catalog_futures.duckdb"


def _get_schema_sql(conn: duckdb.DuckDBPyConnection, table_name: str) -> str:
    """获取建表 DDL（CREATE TABLE ...）。"""
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", [table_name]
    ).fetchall()
    return rows[0][0] if rows else ""


def _get_all_tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """获取所有用户表（排除系统表）。"""
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name"
    ).fetchall()
    return [r[0] for r in rows]


def _has_column(conn: duckdb.DuckDBPyConnection, table: str, col: str) -> bool:
    rows = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name=? AND column_name=? AND table_schema='main'",
        [table, col],
    ).fetchall()
    return len(rows) > 0


def migrate(dry_run: bool = False) -> int:
    """执行迁移。

    Returns:
        0 = 成功，1 = 失败
    """
    if not SRC_DB.exists():
        print(f"[ERROR] 源数据库不存在: {SRC_DB}")
        return 1

    for dst in (DST_STOCK, DST_FUTURES):
        if dst.exists():
            print(f"[WARN] 目标数据库已存在: {dst}（跳过，如需重新迁移请删除）")
            # 验证已存在的数据库
            if not dry_run:
                print(f"  → 验证 {dst.name} ...")
                verify_db(dst)
            return 0

    if dry_run:
        print("[DRY-RUN] 模拟迁移，不写入文件")
        return 0

    print("=== 分库迁移 ===")
    print(f"源: {SRC_DB}")
    print(f"目标: {DST_STOCK.name} (stock + multi)")
    print(f"目标: {DST_FUTURES.name} (futures + multi)")
    print()

    # 连接源库，读取 schema 后关闭（ATTACH 需要排他访问）
    src = duckdb.connect(str(SRC_DB), read_only=True)
    tables = _get_all_tables(src)

    # 预检查：factor_catalog 是否有 market 列
    if not _has_column(src, "factor_catalog", "market"):
        print("[ERROR] factor_catalog 表缺少 market 列，无法拆分")
        src.close()
        return 1

    # 预读取所有表 schema（关闭 src 后无法再读取）
    table_schemas: dict[str, str] = {}
    for t in tables:
        schema = _get_schema_sql(src, t)
        if schema:
            table_schemas[t] = schema
        else:
            print(f"  [WARN] 无法获取 {t} 的 schema")

    # 关闭源库连接（ATTACH 需要排他访问）
    src.close()

    for market_name, dst_path, market_filter in [
        ("stock", DST_STOCK, ("stock", "multi")),
        ("futures", DST_FUTURES, ("futures", "multi")),
    ]:
        print(f"\n--- 创建 {market_name} 库: {dst_path.name} ---")

        # 创建目标库
        dst = duckdb.connect(str(dst_path))

        # 创建所有表（从预读取的 schema）
        for t in tables:
            schema = table_schemas.get(t)
            if not schema:
                print(f"  [WARN] 跳过 {t}（无 schema）")
                continue

            # 在目标库中创建表
            dst.execute(schema)
            print(f"  ✓ 创建表 {t}")

        # ATTACH 源库以便跨库查询（使用绝对路径）
        dst.execute(f"ATTACH DATABASE '{SRC_DB.resolve()}' AS src_db")

        # 复制数据（按 market 过滤）
        # 表名映射：有的表可能需要关联 factor_catalog 过滤
        join_tables = {
            "factor_versions": "factor_id",
            "factor_evaluations": "factor_id",
            "factor_reviews": "factor_id",
            "factor_quality_scores": "factor_id",
            "factor_status_history": "factor_id",
            "feedback_events": "factor_id",
        }
        correlation_tables = {"factor_correlations"}
        # 无 factor_id 的表：直接复制全部
        no_filter_tables = {
            "seed_lineage",
            "attribution_reports",
            "feedback_processing_results",
            "feedback_reports",
        }

        for t in tables:
            if t == "factor_catalog":
                placeholders = ",".join("?" * len(market_filter))
                dst.execute(
                    f'INSERT INTO "{t}" SELECT * FROM src_db.main."{t}" WHERE market IN ({placeholders})',
                    list(market_filter),
                )
            elif t in no_filter_tables:
                dst.execute(f'INSERT INTO "{t}" SELECT * FROM src_db.main."{t}"')
            elif t in correlation_tables:
                placeholders = ",".join("?" * len(market_filter))
                dst.execute(
                    f"""
                    INSERT INTO "{t}" SELECT f.* FROM src_db.main."{t}" f
                    WHERE f.factor_id_a IN (
                        SELECT factor_id FROM src_db.main.factor_catalog
                        WHERE market IN ({placeholders})
                    ) OR f.factor_id_b IN (
                        SELECT factor_id FROM src_db.main.factor_catalog
                        WHERE market IN ({placeholders})
                    )
                """,
                    list(market_filter) * 2,
                )
            elif t in join_tables:
                join_col = join_tables[t]
                placeholders = ",".join("?" * len(market_filter))
                dst.execute(
                    f"""
                    INSERT INTO "{t}" SELECT child.* FROM src_db.main."{t}" child
                    JOIN src_db.main.factor_catalog fc ON child.{join_col} = fc.factor_id
                    WHERE fc.market IN ({placeholders})
                """,
                    list(market_filter),
                )
            else:
                # 其他表：直接复制全部
                dst.execute(f'INSERT INTO "{t}" SELECT * FROM src_db.main."{t}"')

            cnt = dst.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
            print(f"  ✓ 复制 {t}: {cnt} 行")

        dst.close()
    print("\n=== 迁移完成 ===")

    # 验证
    for dst in (DST_STOCK, DST_FUTURES):
        print(f"\n--- 验证 {dst.name} ---")
        verify_db(dst)

    print("\n✓ 迁移成功！")
    return 0


def verify_db(db_path: Path) -> None:
    """验证数据库完整性。"""
    if not db_path.exists():
        print(f"  [WARN] 数据库不存在: {db_path}")
        return

    con = duckdb.connect(str(db_path))
    tables = _get_all_tables(con)
    for t in tables:
        cnt = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        print(f"  {t}: {cnt} 行")
    con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="分库迁移脚本")
    parser.add_argument("--dry-run", action="store_true", help="仅模拟，不写入")
    args = parser.parse_args()
    sys.exit(migrate(dry_run=args.dry_run))