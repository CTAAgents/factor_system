"""测试 AKShare futures_display_main_sina 返回的列"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import akshare as ak

df = ak.futures_display_main_sina()
print("Columns:", list(df.columns))
print("Shape:", df.shape)
print("\nFirst 5 rows:")
print(df.head().to_string())
print("\n\nSample row:")
print(df.iloc[0].to_dict())

# 测试是否有 contract 列的各种可能名称
for col in df.columns:
    print(f"  col: '{col}'")

# 试试 futures_zh_spot 看能不能获取合约列表
print("\n\n=== futures_zh_spot ===")
try:
    spot = ak.futures_zh_spot(symbol="RB0", market="SHFE")
    print("Columns:", list(spot.columns))
    print(spot.head().to_string())
except Exception as e:
    print(f"Error: {e}")