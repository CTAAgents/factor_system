"""测试 AKShare futures_zh_daily_sina 返回的字段"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import akshare as ak

df = ak.futures_zh_daily_sina(symbol="RB2610")
print("Columns:", list(df.columns))
print("Shape:", df.shape)
print("\nFirst 3 rows:")
print(df.head(3).to_string())
print("\nLast 3 rows:")
print(df.tail(3).to_string())
print("\nDtypes:")
print(df.dtypes)

# 检查是否有 hold, settle 字段
for col in ["hold", "settle", "open_interest", "settlement", "amount"]:
    found = [c for c in df.columns if col.lower() in c.lower()]
    if found:
        print(f"\n'{col}' found as: {found}")
        print(df[found[0]].head(5).to_string())