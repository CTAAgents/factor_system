"""验证 WQ 101 和 Qlib 158 定义文件。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fts.factor_engine.seed_data.wq101 import WQ101_DEFINITIONS
from fts.factor_engine.seed_data.qlib158 import QLIB158_DEFINITIONS

print(f"WQ101: {len(WQ101_DEFINITIONS)} entries")
print(f"  First: {WQ101_DEFINITIONS[0]['name']}")
print(f"  Last: {WQ101_DEFINITIONS[-1]['name']}")

print(f"Qlib158: {len(QLIB158_DEFINITIONS)} entries")
print(f"  First: {QLIB158_DEFINITIONS[0]['name']}")
print(f"  Last: {QLIB158_DEFINITIONS[-1]['name']}")

# 验证名称连续性
for i, d in enumerate(WQ101_DEFINITIONS):
    expected = f"alpha_{i + 1:03d}"
    if d["name"] != expected:
        print(f"  WARNING: WQ101[{i}] name mismatch: {d['name']} != {expected}")

for i, d in enumerate(QLIB158_DEFINITIONS):
    expected = f"qlib_{i + 1:03d}"
    if d["name"] != expected:
        print(f"  WARNING: Qlib158[{i}] name mismatch: {d['name']} != {expected}")

print("All entries verified.")
