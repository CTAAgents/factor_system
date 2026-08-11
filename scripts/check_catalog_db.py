"""检查 factor_catalog.duckdb 的结构和数据分布。"""
import duckdb
from pathlib import Path

db_path = Path("d:/Programs/factor_system/data/factor_catalog.duckdb")
src = duckdb.connect(str(db_path))

# 检查表结构
tables = src.execute("SELECT table_name, table_type FROM information_schema.tables WHERE table_schema = 'main'").fetchall()
print("=== 表列表 ===")
for t in tables:
    print(f"  {t[0]} ({t[1]})")

# 检查 factor_catalog 表结构
print("\n=== factor_catalog 列 ===")
cols = src.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'factor_catalog' AND table_schema = 'main' ORDER BY ordinal_position").fetchall()
for c in cols:
    print(f"  {c[0]}: {c[1]}")

# market 分布
print("\n=== market 分布 ===")
rows = src.execute("SELECT market, count(*) FROM factor_catalog GROUP BY market ORDER BY market").fetchall()
for r in rows:
    print(f"  {r[0]}: {r[1]}")

# is_elite + status 分布
print("\n=== is_elite + status 分布 ===")
rows = src.execute("SELECT market, is_elite, status, count(*) FROM factor_catalog GROUP BY market, is_elite, status ORDER BY market, is_elite, status").fetchall()
for r in rows:
    print(f"  {r[0]}, is_elite={r[1]}, status={r[2]}: {r[3]}")

# 检查其他表
print("\n=== 其他表行数 ===")
for t in tables:
    if t[0] != "factor_catalog":
        cnt = src.execute(f"SELECT count(*) FROM \"{t[0]}\"").fetchone()[0]
        print(f"  {t[0]}: {cnt} 行")

# 文件大小
src.close()
print(f"\n数据库大小: {db_path.stat().st_size / 1024 / 1024:.2f} MB")