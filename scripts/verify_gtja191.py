"""验证 gtja191.py 的完整性和正确性。"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GTJA_FILE = ROOT / "fts" / "factor_engine" / "seed_data" / "gtja191.py"

# 1. Syntax check
print("=" * 60)
print("1. Python 语法检查")
print("=" * 60)
source = GTJA_FILE.read_text(encoding="utf-8")
try:
    ast.parse(source)
    print("  ✓ Python 语法正确")
except SyntaxError as e:
    print(f"  ✗ 语法错误: {e}")
    sys.exit(1)

# 2. Import and count
print("\n" + "=" * 60)
print("2. 因子数量与字段检查")
print("=" * 60)
sys.path.insert(0, str(ROOT))
from fts.factor_engine.seed_data.gtja191 import GTJA191_DEFINITIONS

count = len(GTJA191_DEFINITIONS)
print(f"  ✓ 总因子数: {count}")

# Check fields
missing_fields = []
for i, f in enumerate(GTJA191_DEFINITIONS):
    for key in ["name", "expression", "narrative"]:
        if key not in f:
            missing_fields.append(f"  ✗ 因子 {i+1}: 缺少字段 {key}")
    if not f.get("name", "").startswith("gtja_"):
        missing_fields.append(f'  ✗ 因子 {i+1}: name 格式错误: {f.get("name")}')

if missing_fields:
    for m in missing_fields:
        print(m)
else:
    print("  ✓ 所有因子均包含正确的 name/expression/narrative 字段")

# 3. Consecutive numbering
print("\n" + "=" * 60)
print("3. 编号连续性检查")
print("=" * 60)
names = [f["name"] for f in GTJA191_DEFINITIONS]
expected = [f"gtja_{i:03d}" for i in range(1, 192)]
if names == expected:
    print("  ✓ 191 个因子连续编号 (gtja_001 ~ gtja_191)")
else:
    missing_nums = set(expected) - set(names)
    extra_nums = set(names) - set(expected)
    if missing_nums:
        print(f"  ✗ 缺少因子: {sorted(missing_nums)}")
    if extra_nums:
        print(f"  ✗ 多余因子: {sorted(extra_nums)}")

# 4. Check for untranslated DolphinDB functions
print("\n" + "=" * 60)
print("4. 未翻译函数检查")
print("=" * 60)
dolphindb_fns = ["mfirst", "mavg", "msum", "mstd", "mcorr", "mmax", "mmin",
                 "mrank", "mcovar", "mbeta", "mimax", "mimin", "mcount",
                 "rowRank", "ewmMean", "linearTimeTrend", "mfirst"]
issues = []
for f in GTJA191_DEFINITIONS:
    expr = f["expression"]
    for fn in dolphindb_fns:
        if fn in expr:
            issues.append(f'  ✗ {f["name"]}: 包含未翻译的 {fn}')

if not issues:
    print("  ✓ 所有表达式已从 DolphinDB 翻译为 Python")
else:
    for issue in issues[:10]:
        print(issue)
    if len(issues) > 10:
        print(f"  ... 还有 {len(issues)-10} 个问题")

# 5. Summary
print("\n" + "=" * 60)
print("验证完成")
print("=" * 60)
print(f"  文件: {GTJA_FILE}")
print(f"  因子数: {count}")
print(f"  状态: {'通过' if not missing_fields and not issues else '有警告'}")