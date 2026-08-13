"""检查 contract_kline 表结构"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import duckdb
from fts.data_futures import _DUCKDB_PATH

con = duckdb.connect(str(_DUCKDB_PATH))

# 检查实际列名
cols = con.execute("DESCRIBE contract_kline").fetchall()
print("=== contract_kline 实际列名 ===")
for c in cols:
    print(f"  {c[0]} ({c[1]})")

# 检查是否有 source 列
has_source = any(c[0].lower() == "source" for c in cols)
print(f"\nhas source column: {has_source}")
has_settle = any(c[0].lower() == "settle" for c in cols)
print(f"has settle column: {has_settle}")
has_hold = any(c[0].lower() == "hold" for c in cols)
print(f"has hold column: {has_hold}")

# 字段缺失情况
total = con.execute("SELECT COUNT(*) FROM contract_kline").fetchone()[0]
print(f"\n总行数: {total}")
for col in ["amount", "hold", "settle"]:
    col_actual = None
    for c in cols:
        if c[0].lower() == col:
            col_actual = c[0]
            break
    if col_actual:
        zero = con.execute(f'SELECT COUNT(*) FROM contract_kline WHERE "{col_actual}" = 0 OR "{col_actual}" IS NULL').fetchone()[0]
        print(f"  {col}: 零值/空={zero}/{total} ({zero/total*100:.1f}%)")

# 日期范围
dr = con.execute("SELECT MIN(date), MAX(date) FROM contract_kline").fetchone()
print(f"\n日期范围: {dr[0]} ~ {dr[1]}")

# 品种列表
syms = con.execute("SELECT DISTINCT symbol FROM contract_kline ORDER BY symbol").fetchall()
print(f"\n品种列表 ({len(syms)}):")
print("  " + ", ".join(s[0] for s in syms))

# 缺少的品种
print("\n=== 缺少的品种（现有 82 个品种 - 已同步 59 个）===")
from fts.data_futures import FUTURES_SUBSET
existing = set(s[0] for s in syms)
all_syms = set(FUTURES_SUBSET)
missing = all_syms - existing
print(f"缺少 {len(missing)} 个品种:")
for m in sorted(missing):
    print(f"  {m}")

con.close()
print("\n完成 ✓")