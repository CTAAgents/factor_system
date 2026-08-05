"""Check FTS optimization progress"""
from pathlib import Path

print("=== portfolio_loop.py DuckDB 集成状态 ===")
portfolio = Path("fts/factor_engine/portfolio_loop.py")
if portfolio.exists():
    content = portfolio.read_text(encoding="utf-8")
    lines = len(content.splitlines())
    print(f"portfolio_loop.py: {lines} 行")
    print("  已集成 DuckDB:", "factor_catalog" in content or "FactorRepository" in content)
    print("  import factor_db:", "factor_db" in content)
    print("  FactorRepository:", "FactorRepository" in content)

print()
print("=== evolution_loop.py DuckDB 集成 ===")
evo = Path("fts/factor_engine/evolution_loop.py")
if evo.exists():
    content = evo.read_text(encoding="utf-8")
    print("使用 FactorRepository:", "FactorRepository" in content)
    print("使用 factor_db:", "factor_db" in content)
    print("INSERT INTO factor_catalog:", "factor_catalog" in content)

print()
print("=== 种子加载器检查 ===")
seed_loader = Path("fts/factor_engine/seed_loader.py")
if seed_loader.exists():
    content = seed_loader.read_text(encoding="utf-8")
    print(f"seed_loader.py: 存在 ({len(content.splitlines())} 行)")
    print("  YAML 加载:", "yaml" in content)
else:
    print("seed_loader.py: 不存在")

print()
print("=== 现有测试统计 ===")
tests_dir = Path("tests")
if tests_dir.exists():
    test_files = list(tests_dir.rglob("test_*.py"))
    print(f"测试文件数: {len(test_files)}")
    
    db_tests = list(tests_dir.rglob("*factor_db*"))
    print(f"Factor DB 测试文件: {len(db_tests)}")
    for t in db_tests:
        print(f"  {t}")
    
    lineage_tests = list(tests_dir.rglob("*lineage*"))
    print(f"Lineage 测试文件: {len(lineage_tests)}")
    for t in lineage_tests:
        print(f"  {t}")

# Check lineage module
lineage_files = list(Path("fts").rglob("*lineage*"))
print(f"\nLineage 相关模块: {len(lineage_files)}")
for f in lineage_files:
    print(f"  {f}")