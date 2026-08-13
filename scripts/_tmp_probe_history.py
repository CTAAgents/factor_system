"""临时勘察：fts_history.duckdb 表结构与分区现状（验证后删除）"""
import duckdb
import os

p = "data/fts_history.duckdb"
print("size_MB:", round(os.path.getsize(p) / 1e6, 1))
c = duckdb.connect(p, read_only=True)
tables = [r[0] for r in c.execute("show tables").fetchall()]
print("tables:", tables)
for t in tables:
    try:
        n = c.execute(f'select count(*) from "{t}"').fetchone()[0]
        print(f"  {t}: {n} 行")
    except Exception as e:
        print(f"  {t}: err {e}")
try:
    print(
        "kline 日期范围:",
        c.execute("select min(date), max(date), count(distinct date) from kline_cache").fetchone(),
    )
    print(
        "kline 品种数:", c.execute("select count(distinct symbol) from kline_cache").fetchone()[0]
    )
    print(
        "kline 按年分布:",
        c.execute(
            "select year(date)::varchar y, count(*) from kline_cache group by 1 order by 1"
        ).fetchall(),
    )
except Exception as e:
    print("kline 查询 err:", e)
c.close()