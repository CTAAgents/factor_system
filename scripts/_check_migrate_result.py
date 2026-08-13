"""检查 schema 迁移结果"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import duckdb
from fts.data_futures import _DUCKDB_PATH

con = duckdb.connect(str(_DUCKDB_PATH))
cols = con.execute("DESCRIBE contract_kline").fetchall()
print("=== contract_kline 列名 ===")
for c in cols:
    print(f"  {c[0]} ({c[1]})")

src = con.execute("SELECT source, COUNT(*) FROM contract_kline GROUP BY source").fetchall()
print("\n数据源分布:")
for s in src:
    print(f"  {s[0]}: {s[1]}")

total = con.execute("SELECT COUNT(*) FROM contract_kline").fetchone()[0]
for col in ["hold", "settle", "amount"]:
    nonzero = con.execute(f'SELECT COUNT(*) FROM contract_kline WHERE "{col}" != 0 AND "{col}" IS NOT NULL').fetchone()[0]
    print(f"  {col} 非零: {nonzero}/{total} ({nonzero/total*100:.1f}%)")

con.close()
print("\n完成 ✓")