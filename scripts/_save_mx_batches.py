"""保存所有批次并合并生成基本面缓存。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fts.data_mcp_bridge import _parse_mx_response, save_cache

files = [
    ("a53683e4-8174-4388-97c7-dbf140a2320e", 1),
    ("4e96c881-5281-48c0-93f0-06b3e718e121", 2),
    ("cd7e8824-ebc9-4670-9285-2d08d633e3e0", 3),
    ("a823c1d3-45ac-48ff-9575-0dfad7f4fa46", 4),
    ("850cc5cb-5d3d-477e-bc47-0ddc6508c417", 5),
    ("6bdc5b03-b22a-49b8-82db-c16d2f85b2c7", 6),
]

all_data = []

for file_id, batch_num in files:
    src = Path(rf"C:\Users\yangd\AppData\Local\Temp\trae\toolcall-output\{file_id}.txt")
    if not src.exists():
        print(f"Batch {batch_num}: file not found -> {src}")
        continue
    text = src.read_text(encoding="utf-8")

    idx = text.index("[")
    outer = json.loads(text[idx:])
    inner = json.loads(outer[0]["text"])

    # 保存原始 JSON
    out = Path(rf"d:\Programs\factor_system\data\mx_batch_{batch_num}.json")
    out.write_text(json.dumps(inner, ensure_ascii=False, indent=2), encoding="utf-8")

    sheets = len(inner["data"])
    all_data.extend(inner["data"])
    print(f"Batch {batch_num} saved: {sheets} sheets -> {out}")

print(f"\nTotal: {len(all_data)} sheets across {len(files)} batches")

# 合并所有批次 → 缓存
print("\nParsing into fundamental cache...")
cache = _parse_mx_response(all_data)
print(f"Parsed: {len(cache)} stocks")
save_cache(cache)
print("Done! Cache saved to data/fundamental_cache.json")