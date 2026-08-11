"""检查所有表的列信息，特别是关联列。"""
import duckdb

db_path = "d:/Programs/factor_system/data/factor_catalog.duckdb"
con = duckdb.connect(db_path)

tables = con.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'main' ORDER BY table_name").fetchall()
for t in tables:
    name = t[0]
    cols = con.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{name}' AND table_schema = 'main' ORDER BY ordinal_position").fetchall()
    col_names = [c[0] for c in cols]
    print(f"{name}: {', '.join(col_names[:8])}{'...' if len(col_names) > 8 else ''}")

con.close()