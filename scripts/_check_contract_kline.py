"""检查 contract_kline 表结构及字段缺失情况"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import duckdb
from fts.data_futures import _DUCKDB_PATH

con = duckdb.connect(str(_DUCKDB_PATH))

# 列名
cols = con.execute("DESCRIBE contract_kline").fetchall()
print("=== contract_kline 列名 ===")
for c in cols:
    print(f"  {c[0]} ({c[1]})")

# 字段缺失情况
total = con.execute("SELECT COUNT(*) FROM contract_kline").fetchone()[0]
print(f"\n总行数: {total}")

for col_name in ["amount", "hold", "settle", "pre_settle", "oi_change"]:
    for c in cols:
        if c[0].lower() == col_name:
            zero = con.execute(f'SELECT COUNT(*) FROM contract_kline WHERE "{c[0]}" = 0 OR "{c[0]}" IS NULL').fetchone()[0]
            non_zero = total - zero
            print(f"  {c[0]}: 非零={non_zero}/{total} ({non_zero/total*100:.1f}%)")
            break

# 数据源
print("\n数据源分布:")
src = con.execute("SELECT source, COUNT(*) as cnt FROM contract_kline GROUP BY source ORDER BY cnt DESC").fetchall()
for s in src:
    print(f"  {s[0]}: {s[1]}")

# 日期范围
dr = con.execute("SELECT MIN(date), MAX(date) FROM contract_kline").fetchone()
print(f"\n日期范围: {dr[0]} ~ {dr[1]}")

con.close()
print("\n完成 ✓")