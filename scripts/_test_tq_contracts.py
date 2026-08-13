"""测试通达信 TQ 对过期合约和批量合约的支持"""
from __future__ import annotations

import json
import urllib.request

TDX_URL = "http://127.0.0.1:17709/"


def test_one(code: str, count: int = 3) -> None:
    payload = {
        "id": 1,
        "method": "get_market_data",
        "params": {
            "stock_list": [code],
            "count": count,
            "period": "1d",
        },
    }
    req = urllib.request.Request(
        TDX_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode())
    except Exception as e:
        print(f"{code}: Error - {e}")
        return

    result_block = result.get("result", {}) if isinstance(result, dict) else result
    vals = result_block.get("Value", {}) if isinstance(result_block, dict) else {}
    block = vals.get(code, {}) if isinstance(vals, dict) else {}

    if isinstance(block, dict) and "Date" in block:
        dates = block.get("Date", [])
        print(f"{code}: {len(dates)} bars, dates={dates[:3]}...{dates[-3:] if len(dates) > 3 else ''}")
        has_amount = "Amount" in block
        has_hold = "Hold" in block or "VolInStock" in block
        print(f"  has_amount={has_amount} hold={has_hold}")
    else:
        total = result_block.get("KlineTotal", {}).get(code, 0) if isinstance(result_block, dict) else 0
        print(f"{code}: Empty (total={total})")


# Test expired contracts across different years
print("=== 过期合约测试 ===")
test_one("RB2501.SHF")   # 2025年1月到期
test_one("RB2401.SHF")   # 2024年1月到期
test_one("RB2310.SHF")   # 2023年10月到期
test_one("RB2001.SHF")   # 2020年1月到期
test_one("RB1501.SHF")   # 2015年1月到期

print()
print("=== 当前活跃合约测试 ===")
test_one("RB2610.SHF", 500)  # 当前主力，500 bars
test_one("CU2608.SHF", 500)
test_one("M2609.DCE", 500)

print()
print("=== 批量合约测试 ===")
# Test multiple contracts at once
payload = {
    "id": 1,
    "method": "get_market_data",
    "params": {
        "stock_list": ["RB2610.SHF", "RB2609.SHF", "RB2608.SHF"],
        "count": 3,
        "period": "1d",
    },
}
req = urllib.request.Request(
    TDX_URL,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        result = json.loads(resp.read().decode())
    result_block = result.get("result", {}) if isinstance(result, dict) else {}
    vals = result_block.get("Value", {}) if isinstance(result_block, dict) else {}
    for k, v in vals.items():
        if isinstance(v, dict) and "Date" in v:
            print(f"  {k}: {len(v['Date'])} bars")
        else:
            print(f"  {k}: no data")
except Exception as e:
    print(f"  Error: {e}")