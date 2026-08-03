"""临时诊断 8：验证从 contract_kline 判定主力合约的可行性。"""
import duckdb

conn = duckdb.connect(r"d:\Programs\factor_system\data\fts_history.duckdb", read_only=True)

# 最新日期
latest = conn.execute(
    "SELECT MAX(date) FROM contract_kline"
).fetchone()[0]
print("contract_kline 最新日期:", latest)

# 各品种最新交易日的主力合约（成交量最大）
rows = conn.execute(f"""
    SELECT symbol, contract, volume
    FROM contract_kline
    WHERE date = '{latest}'
    QUALIFY ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY volume DESC) = 1
    ORDER BY symbol
""").fetchall()
print(f"\n最新交易日主力合约 ({len(rows)} 个品种):")
for r in rows:
    print(f"  {r[0]:6s} -> {r[1]:8s} vol={r[2]:.0f}")
