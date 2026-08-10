"""scripts/migrate_add_seed_lineage.py — 新增 seed_lineage 表迁移脚本

将 seed_lineage 表加入到现有 DuckDB 数据库中（幂等，可重复执行）。

背景:
    L0 种子因子（YAML）→ L1 候选因子（JSON）→ L2 精英因子（DuckDB）
    三层存储之间缺乏溯源链路。seed_lineage 表记录"哪个种子因子
    演化出了哪个精英因子"，打通 L0→L2 全链路。

用法:
    python scripts/migrate_add_seed_lineage.py                    # 默认路径
    python scripts/migrate_add_seed_lineage.py --db-path /path/to/custom.duckdb
    python scripts/migrate_add_seed_lineage.py --dry-run           # 仅检查不执行
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb

from fts.factor_engine.factor_db.schema import DATABASE_PATH, _CREATE_SEED_LINEAGE


def check_table_exists(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """检查表是否存在。"""
    rows = conn.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema='main' AND table_name=?",
        [table_name],
    ).fetchall()
    return len(rows) > 0


def run_migration(db_path: Path, dry_run: bool = False) -> dict:
    """执行 seed_lineage 表迁移。

    Args:
        db_path: DuckDB 数据库路径
        dry_run: True 仅检查不执行 DDL

    Returns:
        迁移结果统计
    """
    result: dict = {
        "db_path": str(db_path),
        "db_exists": db_path.exists(),
        "table_exists": False,
        "migration_applied": False,
        "error": None,
    }

    if not db_path.exists():
        result["error"] = f"数据库文件不存在: {db_path}"
        return result

    conn = duckdb.connect(str(db_path))

    try:
        # 检查 seed_lineage 是否已存在
        result["table_exists"] = check_table_exists(conn, "seed_lineage")

        if result["table_exists"]:
            # 验证表结构完整性
            cols = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='main' AND table_name='seed_lineage'"
                ).fetchall()
            }
            expected_cols = {
                "lineage_id",
                "seed_name",
                "seed_family",
                "seed_market",
                "evolved_factor_id",
                "evolved_factor_name",
                "generation",
                "parent_id",
                "trace_id",
                "promoted_at",
            }
            missing = expected_cols - cols
            if missing:
                result["error"] = f"seed_lineage 表已存在但缺少字段: {missing}"
            else:
                row_count = conn.execute("SELECT COUNT(*) FROM seed_lineage").fetchone()[0]
                result["row_count"] = int(row_count)
                result["migration_applied"] = False
                result["message"] = f"seed_lineage 表已存在，无需迁移 (行数: {row_count})"
        else:
            if dry_run:
                result["message"] = "[dry-run] seed_lineage 表不存在，将执行建表 DDL"
                result["migration_applied"] = False
            else:
                conn.execute(_CREATE_SEED_LINEAGE)
                conn.execute("CHECKPOINT")
                # 验证
                if check_table_exists(conn, "seed_lineage"):
                    result["table_exists"] = True
                    result["migration_applied"] = True
                    result["row_count"] = 0
                    result["message"] = "✅ seed_lineage 表创建成功"
                else:
                    result["error"] = "建表后验证失败"
    except Exception as e:
        result["error"] = str(e)
    finally:
        conn.close()

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="新增 seed_lineage 表到 DuckDB 数据库",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="DuckDB 数据库路径（默认: data/factor_catalog.duckdb）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅检查不执行 DDL",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DATABASE_PATH

    result = run_migration(db_path, dry_run=args.dry_run)

    # 输出结果
    print(f"数据库路径: {result['db_path']}")
    print(f"数据库存在: {result['db_exists']}")
    print(f"seed_lineage 存在: {result['table_exists']}")
    print(f"迁移已执行: {result['migration_applied']}")
    if "row_count" in result:
        print(f"当前行数: {result['row_count']}")
    print(f"消息: {result.get('message', '')}")
    if result["error"]:
        print(f"❌ 错误: {result['error']}")
        sys.exit(1)
    elif result["migration_applied"]:
        print("✅ 迁移完成")
    else:
        print("ℹ️  无需迁移")


if __name__ == "__main__":
    main()
