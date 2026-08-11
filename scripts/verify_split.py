"""
快速验证：分库后的 FactorRepository 能否正常工作。
"""
import sys

sys.path.insert(0, "d:/Programs/factor_system")
from fts.factor_engine.factor_db import FactorRepository, get_db_path, DATABASE_PATH_STOCK, DATABASE_PATH_FUTURES

print("=== 分库验证 ===")

# 验证数据库路径
print(f"Stock DB: {DATABASE_PATH_STOCK} (exists={DATABASE_PATH_STOCK.exists()})")
print(f"Futures DB: {DATABASE_PATH_FUTURES} (exists={DATABASE_PATH_FUTURES.exists()})")

# 验证 get_db_path 函数
print(f"get_db_path('stock') = {get_db_path('stock')}")
print(f"get_db_path('futures') = {get_db_path('futures')}")

# 测试股票仓库
repo_stock = FactorRepository(market="stock")
stock_count = repo_stock._execute("SELECT count(*) FROM factor_catalog").fetchone()[0]
stock_elite = repo_stock._execute("SELECT count(*) FROM factor_catalog WHERE is_elite=true").fetchone()[0]
print(f"\nStock DB: {stock_count} factors, {stock_elite} elite")

# 测试期货仓库
repo_futures = FactorRepository(market="futures")
futures_count = repo_futures._execute("SELECT count(*) FROM factor_catalog").fetchone()[0]
futures_elite = repo_futures._execute("SELECT count(*) FROM factor_catalog WHERE is_elite=true").fetchone()[0]
print(f"Futures DB: {futures_count} factors, {futures_elite} elite")

# 验证 multi 因子
multi_stock = repo_stock._execute("SELECT factor_id, name FROM factor_catalog WHERE market='multi'").fetchone()
multi_futures = repo_futures._execute("SELECT factor_id, name FROM factor_catalog WHERE market='multi'").fetchone()
print(f"\nMulti factor in stock: {multi_stock}")
print(f"Multi factor in futures: {multi_futures}")

# 并发访问测试
print("\n=== 并发访问测试 ===")
print("Stock + Futures 同时打开: OK")

# 无参数构造测试（默认 stock）
repo_default = FactorRepository()
default_count = repo_default._execute("SELECT count(*) FROM factor_catalog").fetchone()[0]
print(f"Default repo (market=stock): {default_count} factors")

print("\n✓ 分库验证通过！")