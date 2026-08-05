"""scripts/dedup_factor_db.py — DuckDB 因子去重工具

按 (name, market) 去重，保留 IC 最高的版本。
同时清理 factor_evaluations 表中的孤儿记录。

用法:
    python scripts/dedup_factor_db.py [--market futures|stock|all] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import duckdb


def dedup_factors(db_path: str, market: str = "all", dry_run: bool = False) -> dict:
    """按 (name, market) 去重因子，保留 IC 最高版本。

    Args:
        db_path: DuckDB 数据库路径
        market: 市场过滤 ("all" 处理所有市场)
        dry_run: True 只报告不删除

    Returns:
        统计结果字典
    """
    conn = duckdb.connect(db_path)

    # 1. 找出重复组
    if market == "all":
        dup_groups = conn.execute("""
            SELECT market, name, COUNT(*) as cnt
            FROM factor_catalog
            GROUP BY market, name
            HAVING cnt > 1
            ORDER BY cnt DESC
        """).fetchall()
    else:
        dup_groups = conn.execute("""
            SELECT market, name, COUNT(*) as cnt
            FROM factor_catalog
            WHERE market = ?
            GROUP BY market, name
            HAVING cnt > 1
            ORDER BY cnt DESC
        """, [market]).fetchall()

    if not dup_groups:
        print("✅ 无重复因子，数据库已是干净状态")
        conn.close()
        return {"total_groups": 0, "total_removed": 0}

    print(f"📊 发现 {len(dup_groups)} 组重复因子")
    print(f"   涉及 {sum(g[2] for g in dup_groups)} 条记录 → 目标保留 {len(dup_groups)} 条")

    total_removed = 0
    total_kept = 0

    for mkt, name, cnt in dup_groups:
        # 找出该组中 IC 最高的因子
        best = conn.execute("""
            SELECT factor_id, name, ic, sharpe, market, is_elite, status
            FROM factor_catalog
            WHERE market = ? AND name = ?
            ORDER BY ic DESC, sharpe DESC
            LIMIT 1
        """, [mkt, name]).fetchone()

        best_id = best[0]
        best_ic = best[2]
        best_sharpe = best[3]

        # 找出该组中需要删除的因子
        to_delete = conn.execute("""
            SELECT factor_id FROM factor_catalog
            WHERE market = ? AND name = ? AND factor_id != ?
        """, [mkt, name, best_id]).fetchall()

        delete_ids = [r[0] for r in to_delete]

        if dry_run:
            print(f"\n  [DRY-RUN] {mkt}/{name}: 保留 {best_id} (IC={best_ic:.4f}), 删除 {len(delete_ids)} 个")
            if len(delete_ids) <= 5:
                for did in delete_ids:
                    print(f"    - {did}")
        else:
            # 删除重复因子的评估记录
            for did in delete_ids:
                conn.execute("DELETE FROM factor_evaluations WHERE factor_id = ?", [did])

            # 删除重复因子
            placeholders = ",".join(["?"] * len(delete_ids))
            conn.execute(
                f"DELETE FROM factor_catalog WHERE factor_id IN ({placeholders})",
                delete_ids,
            )
            print(f"\n  ✅ {mkt}/{name}: 保留 {best_id} (IC={best_ic:.4f}), 删除 {len(delete_ids)} 个")

        total_removed += len(delete_ids)
        total_kept += 1

    # 2. 清理 factor_evaluations 中的孤儿记录
    if not dry_run:
        orphan_count = conn.execute("""
            SELECT COUNT(*) FROM factor_evaluations fe
            WHERE NOT EXISTS (
                SELECT 1 FROM factor_catalog fc WHERE fc.factor_id = fe.factor_id
            )
        """).fetchone()[0]

        if orphan_count > 0:
            conn.execute("""
                DELETE FROM factor_evaluations fe
                WHERE NOT EXISTS (
                    SELECT 1 FROM factor_catalog fc WHERE fc.factor_id = fe.factor_id
                )
            """)
            print(f"\n🧹 清理孤儿评估记录: {orphan_count} 条")

    # 3. 统计结果
    final_count = conn.execute("SELECT COUNT(*) FROM factor_catalog").fetchone()[0]
    unique_names = conn.execute(
        "SELECT COUNT(DISTINCT name) FROM factor_catalog"
    ).fetchone()[0]
    active_elite = conn.execute("""
        SELECT COUNT(*) FROM factor_catalog WHERE status='active' AND is_elite=true
    """).fetchone()[0]

    print(f"\n📈 清理结果:")
    print(f"   总记录数: {final_count} (去除 {total_removed})")
    print(f"   唯一因子名: {unique_names}")
    print(f"   活跃精英因子: {active_elite}")

    conn.commit()
    conn.close()

    return {
        "total_groups": len(dup_groups),
        "total_removed": total_removed,
        "final_count": final_count,
        "unique_names": unique_names,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="DuckDB 因子去重工具")
    parser.add_argument(
        "--market",
        default="all",
        choices=["futures", "stock", "all"],
        help="市场过滤 (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只报告不删除",
    )
    parser.add_argument(
        "--db",
        default="data/factor_catalog.duckdb",
        help="DuckDB 数据库路径",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"❌ 数据库不存在: {db_path}")
        return 1

    mode = "DRY-RUN" if args.dry_run else "EXECUTE"
    print(f"🔧 DuckDB 因子去重 [{mode}]")
    print(f"   数据库: {db_path}")
    print(f"   市场: {args.market}")
    print()

    if not args.dry_run:
        confirm = input("⚠️  即将执行删除操作，确认? [y/N]: ")
        if confirm.lower() != "y":
            print("取消")
            return 0

    result = dedup_factors(str(db_path), args.market, args.dry_run)
    print(f"\n✅ 完成: 处理 {result['total_groups']} 组重复，去除 {result['total_removed']} 条记录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
