"""检查 market='multi' 的记录。"""
import duckdb

db_path = "d:/Programs/factor_system/data/factor_catalog.duckdb"
src = duckdb.connect(db_path)
row = src.execute("SELECT factor_id, name, market, is_elite, status FROM factor_catalog WHERE market='multi'").fetchone()
print(f"factor_id={row[0]}, name={row[1]}, market={row[2]}, is_elite={row[3]}, status={row[4]}")
src.close()