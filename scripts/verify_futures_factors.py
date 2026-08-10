"""验证期货因子 YAML 种子完整性、加载和编译。

测试内容:
  1. YAML 文件完整性
  2. 期货种子加载（全部 17 家族）
  3. 因子代码编译验证
  4. 按家族分布统计
  5. mc_cta 家族专项验证
"""

import sys

sys.path.insert(0, "d:\\Programs\\factor_system")

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

from fts.factor_engine.seed_loader import (
    load_all_yaml_seeds,
    get_seeds_dir,
)

# ════════════════════════════════════════════════════════════
# 1. YAML 文件完整性
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("1. Checking futures YAML file integrity...")
seeds_dir = get_seeds_dir() / "futures"
total = 0
errors = []

import yaml

for fpath in sorted(seeds_dir.glob("*.yaml")):
    fname = fpath.name
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        factors = data.get("factors", [])
        count = len(factors)
        total += count
        family = data.get("family", "?")
        print(f"   OK: {fname:40s} ({count:3d} factors, family={family})")
    except Exception as e:
        errors.append(f"  ERROR: {fname}: {e}")

if errors:
    for e in errors:
        print(e)
print(f"   Total: {total} factors across all YAML files")
print()

# ════════════════════════════════════════════════════════════
# 2. 加载期货种子
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("2. Loading futures YAML seeds (17 families)...")
futures = load_all_yaml_seeds(trace_id="verify_futures_p3", market="futures")
print(f"   Total futures factors loaded: {len(futures)}")
print()

# ════════════════════════════════════════════════════════════
# 3. 因子代码编译验证
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("3. Compiling futures factor code...")
passed = 0
failed = 0
for f in futures:
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

print(f"   Compiled: {passed}/{len(futures)}")
if failed:
    print(f"   FAILED: {failed}")
print()

# ════════════════════════════════════════════════════════════
# 4. 按家族分布
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("4. Factor family distribution...")
families: dict[str, int] = {}
for f in futures:
    fam = f.get("family", "unknown")
    families[fam] = families.get(fam, 0) + 1

for fam, cnt in sorted(families.items(), key=lambda x: -x[1]):
    print(f"   {fam:30s}: {cnt}")
print(f"   Total families: {len(families)}")
print(f"   Total factors:  {len(futures)}")
print()

# ════════════════════════════════════════════════════════════
# 5. mc_cta 家族专项验证
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("5. mc_cta family specific validation...")
mc_factors = [f for f in futures if f.get("family") == "mc_cta" or "mc_cta" in str(f.get("name", ""))]
print(f"   mc_cta factors found: {len(mc_factors)}")
mc_names = [f.get("name", "?") for f in mc_factors]
for name in sorted(mc_names):
    print(f"   - {name}")

# 验证所有 mc_cta 因子可编译
mc_passed = 0
mc_failed = 0
for f in mc_factors:
    name = f.get("name", "?")
    code = f.get("code", "")
    try:
        compile(code, f"{name}.py", "exec")
        mc_passed += 1
    except SyntaxError as e:
        print(f"   FAIL: {name} - {e}")
        mc_failed += 1

print(f"   mc_cta compiled: {mc_passed}/{len(mc_factors)}")
print()

# ════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════
print("=" * 60)
print("SUMMARY")
print(f"   YAML files: {len(list(seeds_dir.glob('*.yaml')))}")
print(f"   YAML factors: {total}")
print(f"   Loaded factors: {len(futures)}")
print(f"   Compiled: {passed}/{len(futures)}")
print(f"   Families: {len(families)}")
print(f"   mc_cta factors: {len(mc_factors)}")
print(f"   mc_cta compiled: {mc_passed}/{len(mc_factors)}")
print(f"   All OK: {passed == len(futures) and len(futures) > 0 and mc_failed == 0}")
print("=" * 60)
