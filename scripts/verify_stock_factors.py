"""验证股票因子 YAML 种子完整性、加载和编译。

测试内容:
  1. YAML 文件完整性
  2. 股票种子加载（内置 + 外部）
  3. 因子代码编译验证
  4. 股票三源提取器管道
"""

import sys
sys.path.insert(0, "d:\\Programs\\factor_system")

import logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from fts.factor_engine.seed_loader import (
    load_all_yaml_seeds,
    load_factors_from_yaml,
    verify_yaml_integrity,
    get_seeds_dir,
)
from pathlib import Path

STOCK_SEEDS = [
    "builtin.yaml", "wq101.yaml", "qlib158.yaml",
    "gtja191.yaml", "fundamental.yaml", "jq_factors.yaml",
]

# ════════════════════════════════════════════════════════════
# 1. YAML 文件完整性
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("1. Checking stock YAML file integrity...")
seeds_dir = get_seeds_dir() / "stock"
total = 0
errors = []

for fname in STOCK_SEEDS:
    fpath = seeds_dir / fname
    if not fpath.exists():
        errors.append(f"  MISSING: {fname}")
        continue
    try:
        import yaml
        with open(fpath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        factors = data.get("factors", [])
        count = len(factors)
        total += count
        print(f"   OK: {fname} ({count} factors)")
    except Exception as e:
        errors.append(f"  ERROR: {fname}: {e}")

if errors:
    for e in errors:
        print(e)
print(f"   Total: {total} factors across {len(STOCK_SEEDS)} files")
print()

# ════════════════════════════════════════════════════════════
# 2. 加载股票种子
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("2. Loading stock YAML seeds...")
stock = load_all_yaml_seeds(trace_id="verify_stock", market="stock")
print(f"   Total stock factors loaded: {len(stock)}")
print()

# ════════════════════════════════════════════════════════════
# 3. 因子代码编译验证
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("3. Compiling stock factor code...")
passed = 0
failed = 0
for f in stock:
    name = f.get("name", "?")
    code = f.get("code", "")
    if not code:
        print(f"   SKIP: {name} - no code")
        failed += 1
        continue
    try:
        compile(code, f"{name}.py", "exec")
        passed += 1
    except SyntaxError as e:
        print(f"   FAIL: {name} - SYNTAX ERROR: {e}")
        failed += 1

print(f"   Compiled: {passed}/{len(stock)}")
if failed:
    print(f"   FAILED: {failed}")
print()

# ════════════════════════════════════════════════════════════
# 4. 按家族分布
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("4. Factor family distribution...")
families: dict[str, int] = {}
for f in stock:
    fam = f.get("family", "unknown")
    families[fam] = families.get(fam, 0) + 1

for fam, cnt in sorted(families.items(), key=lambda x: -x[1]):
    print(f"   {fam}: {cnt}")
print(f"   Total families: {len(families)}")
print(f"   Total factors: {len(stock)}")
print()

# ════════════════════════════════════════════════════════════
# 5. 测试股票三源提取器管道
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("5. Testing stock extractor pipeline...")
try:
    from fts.factor_engine.extractors.stock_pipeline import (
        StockExtractorPipeline,
        create_stock_extractor_pipeline,
    )
    pipeline = create_stock_extractor_pipeline(
        state_path="memory/extractors/test_state_stock.json",
        pause_jq_after_first=True,
    )
    candidates = pipeline.extract(trace_id="verify_stock_p2")
    print(f"   Extracted candidates: {len(candidates)}")
    print(f"   Active sources: {list(pipeline.extractors.keys())}")
    for src_name, src in pipeline.extractors.items():
        print(f"   - {src_name}: paused={src.paused}")
    print()
except Exception as e:
    import traceback
    print(f"   ERROR: {e}")
    traceback.print_exc()
    print()

# ════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("SUMMARY")
print(f"   YAML files: {len(STOCK_SEEDS)}")
print(f"   YAML factors: {total}")
print(f"   Loaded factors: {len(stock)}")
print(f"   Compiled: {passed}/{len(stock)}")
print(f"   Families: {len(families)}")
print(f"   All OK: {passed == len(stock) and len(stock) > 0}")
print("=" * 60)