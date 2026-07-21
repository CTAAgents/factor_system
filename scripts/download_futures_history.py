"""批量下载所有期货品种历史数据到 FTS 本地 DuckDB（支持断点续传）。

通过 Data-Core 的 UnifiedDataProvider 获取数据，不直接依赖 AKShare。
Data-Core 内部按多源降级链取值：TdxLc -> EastMoney -> QMT -> ExchangeApi -> ...
"""
import os
import sys
import time

import duckdb

sys.path.insert(0, "D:/Programs/data-core")

from datacore import UnifiedDataProvider
from datacore.models.enums import DataType

FUTURES_SYMBOLS = [
    ("RB", "螺纹钢"), ("HC", "热卷"), ("I", "铁矿石"), ("J", "焦炭"), ("JM", "焦煤"),
    ("SF", "硅铁"), ("SM", "锰硅"), ("SC", "原油"), ("LU", "低硫燃油"), ("FU", "燃油"),
    ("BU", "沥青"), ("PG", "液化气"), ("PX", "对二甲苯"), ("TA", "PTA"), ("PF", "短纤"),
    ("EG", "乙二醇"), ("EB", "苯乙烯"), ("V", "PVC"), ("PP", "聚丙烯"), ("L", "聚乙烯"),
    ("MA", "甲醇"), ("UR", "尿素"), ("SA", "纯碱"), ("C", "玉米"), ("CS", "玉米淀粉"),
    ("M", "豆粕"), ("RM", "菜粕"), ("Y", "豆油"), ("P", "棕榈油"), ("OI", "菜油"),
    ("A", "豆一"), ("B", "豆二"), ("FB", "纤维板"), ("BB", "胶合板"), ("JD", "鸡蛋"),
    ("LH", "生猪"), ("AP", "苹果"), ("CJ", "红枣"), ("SR", "白糖"), ("CF", "棉花"),
    ("CY", "棉纱"), ("PK", "花生"), ("SI", "工业硅"), ("LC", "碳酸锂"), ("CU", "铜"),
    ("AL", "铝"), ("ZN", "锌"), ("PB", "铅"), ("NI", "镍"), ("SN", "锡"),
    ("AU", "黄金"), ("AG", "白银"), ("AO", "氧化铝"), ("BR", "丁二烯橡胶"), ("RR", "粳米"),
    ("IF", "沪深300"), ("IH", "上证50"), ("IC", "中证500"), ("IM", "中证1000"),
]

DAYS_TO_DOWNLOAD = 3650
PERIOD = "daily"

db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "fts_history.duckdb")
os.makedirs(os.path.dirname(db_path), exist_ok=True)
print(f"DuckDB: {db_path}")


def init_db(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kline_cache ("
        "symbol VARCHAR NOT NULL, period VARCHAR NOT NULL, "
        "date VARCHAR NOT NULL, open DOUBLE, high DOUBLE, "
        "low DOUBLE, close DOUBLE, volume DOUBLE, amount DOUBLE, "
        "PRIMARY KEY (symbol, period, date))"
    )


def load_existing_symbols(conn):
    try:
        rows = conn.execute("SELECT DISTINCT symbol FROM kline_cache").fetchall()
        return {r[0] for r in rows}
    except Exception:
        return set()


def payload_to_bars(payload):
    """从 Data-Core DataPayload 提取 (date, open, high, low, close, volume, amount) 列表。"""
    data = payload.data if hasattr(payload, "data") else payload

    if data is None:
        return []

    if hasattr(data, "bars") and hasattr(data, "symbol"):
        rows = []
        for bar in data.bars:
            rows.append((
                str(bar.date),
                float(bar.open), float(bar.high), float(bar.low), float(bar.close),
                float(bar.volume), float(bar.amount or 0.0),
            ))
        return rows

    import pandas as pd
    if isinstance(data, pd.DataFrame):
        rows = []
        for idx, row in data.iterrows():
            date_val = idx if isinstance(idx, (pd.Timestamp, str)) else str(idx)
            rows.append((
                str(date_val),
                float(row.get("open", 0.0)),
                float(row.get("high", 0.0)),
                float(row.get("low", 0.0)),
                float(row.get("close", 0.0)),
                float(row.get("volume", 0.0)),
                float(row.get("amount", 0.0)),
            ))
        return rows

    if isinstance(data, list):
        rows = []
        for item in data:
            if isinstance(item, dict):
                rows.append((
                    str(item.get("date", item.get("datetime", ""))),
                    float(item.get("open", 0.0)),
                    float(item.get("high", 0.0)),
                    float(item.get("low", 0.0)),
                    float(item.get("close", 0.0)),
                    float(item.get("volume", 0.0)),
                    float(item.get("amount", 0.0)),
                ))
        return rows

    return []


def main():
    provider = UnifiedDataProvider()

    conn = duckdb.connect(db_path)
    init_db(conn)
    existing = load_existing_symbols(conn)
    conn.close()
    print(f"已下载: {len(existing)} 品种, 剩余: {len(FUTURES_SYMBOLS) - len(existing)} 品种")

    success = 0
    failed = 0
    total_bars = 0
    errors = []

    pending = [(s, n) for s, n in FUTURES_SYMBOLS if s not in existing]
    print(f"开始下载 {len(pending)} 个品种 (period={PERIOD}, days={DAYS_TO_DOWNLOAD})...")

    for i, (symbol, name) in enumerate(pending, 1):
        print(f"[{i}/{len(pending)}] {symbol} ({name})...", end=" ", flush=True)

        try:
            payload = provider.get(
                symbol, DataType.OHLCV,
                params={"period": PERIOD, "days": DAYS_TO_DOWNLOAD},
            )

            if payload is None or not getattr(payload, "available", False):
                err_msg = "无数据"
                if payload and getattr(payload, "errors", None):
                    err_msg = "; ".join(payload.errors)
                print(err_msg)
                failed += 1
                errors.append(f"{symbol}: {err_msg}")
                continue

            bars_raw = payload_to_bars(payload)
            if not bars_raw:
                print("无有效K线")
                failed += 1
                errors.append(f"{symbol}: 无有效K线")
                continue

            source_name = getattr(payload, "source", "unknown")
            bars = []
            for date_str, o, h, l, c, v, a in bars_raw:
                if len(date_str) == 8 and "-" not in date_str:
                    date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                if a <= 0:
                    a = c * v
                bars.append((symbol, PERIOD, date_str, o, h, l, c, v, a))

            conn = duckdb.connect(db_path)
            conn.executemany(
                "INSERT OR REPLACE INTO kline_cache "
                "(symbol, period, date, open, high, low, close, volume, amount) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                bars,
            )
            conn.commit()
            conn.close()

            print(f"{len(bars)} bars [{source_name}] ({bars[0][2]} ~ {bars[-1][2]})")
            success += 1
            total_bars += len(bars)
            time.sleep(0.2)

        except Exception as e:
            print(f"ERROR: {e}")
            failed += 1
            errors.append(f"{symbol}: {e}")

    print()
    print("=" * 60)
    print(f"本轮: {success} 成功, {failed} 失败, {total_bars} 条 K 线")
    print(f"数据库大小: {os.path.getsize(db_path) / 1024 / 1024:.2f} MB")

    if errors:
        print(f"\n失败 ({len(errors)}):")
        for e in errors:
            print(f"  {e}")

    print("\n=== 全量验证 ===")
    conn = duckdb.connect(db_path, read_only=True)
    result = conn.execute(
        "SELECT COUNT(*), COUNT(DISTINCT symbol) FROM kline_cache"
    ).fetchone()
    print(f"总计: {result[1]} 品种, {result[0]} 条 K 线")

    result = conn.execute(
        "SELECT MIN(date), MAX(date) FROM kline_cache"
    ).fetchone()
    print(f"时间范围: {result[0]} ~ {result[1]}")
    conn.close()


if __name__ == "__main__":
    main()
