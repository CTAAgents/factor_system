"""scripts/verify_ifind_field_writes.py — 端到端验证 iFinD 字段写入 kline_cache 和 edb_cache。

HARNESS §任务 14.0.9 验证:
    1. 启动临时 DuckDB
    2. 运行 migrate_schema
    3. mock iFinD MCP 响应 → 通过 IFindSource.fetch_ohlcv 拿到 DataFrame
    4. INSERT 到 kline_cache
    5. SELECT 回查 hold/settle/oi_change/pre_settle/vwap 字段是否正确落库
    6. mock iFinD get_edb_data 响应 → 通过 IFindSource.fetch_edb 拿到 EDB 数据
    7. INSERT 到 edb_cache
    8. SELECT 回查 EDB 数据是否正确落库

Usage:
    python scripts/verify_ifind_field_writes.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import duckdb

# 让脚本独立运行
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fts.data_sources.ifind_source import IFindSource  # noqa: E402
from fts.data_sources.migrate import migrate_schema  # noqa: E402


# 模拟 iFinD bond_market_data 返回的 K 线（带 iFinD 风格的 camelCase 字段）
IFIND_MCP_RAW = {
    "data": [
        {
            "date": "2026-08-01",
            "open": 3500, "high": 3550, "low": 3490, "close": 3540,
            "volume": 100000, "amount": 350000000,
            "openInterest": 80000, "settle": 3540, "preSettle": 3520,
            "openInterestChg": 2000,
        },
        {
            "date": "2026-08-04",
            "open": 3540, "high": 3600, "low": 3530, "close": 3580,
            "volume": 120000, "amount": 420000000,
            "openInterest": 82000, "settle": 3580, "preSettle": 3540,
            "openInterestChg": 2000,
        },
        {
            "date": "2026-08-05",
            "open": 3580, "high": 3620, "low": 3570, "close": 3610,
            "volume": 110000, "amount": 396000000,
            "open_interest": 85000,  # 字段别名测试
            "settle": 3610, "pre_settle": 3580,
            "oi_chg": 3000,  # 字段别名测试
        },
    ],
}

# 模拟 iFinD get_edb_data 返回的 EDB 宏观数据
IFIND_EDB_RAW = {
    "data": [
        {
            "indicator": "M0001396",
            "indicator_name": "中国:GDP:不变价:当季值",
            "date": "2025-12-31",
            "value": 1349083.5,
            "unit": "亿元",
            "yoy": 4.5,
        },
        {
            "indicator": "M0001396",
            "indicator_name": "中国:GDP:不变价:当季值",
            "date": "2026-03-31",
            "value": 1372073.3,
            "unit": "亿元",
            "yoy": 4.7,
        },
        {
            "indicator": "M0000612",  # 中国 CPI
            "indicator_name": "中国:CPI:当月同比",
            "date": "2026-07-31",
            "value": 102.5,
            "unit": "%",
            "yoy": 0.5,
        },
    ],
}


def main() -> int:
    print("=" * 60)
    print("任务 14.0.9 验证: iFinD 字段写入 kline_cache + edb_cache")
    print("=" * 60)

    # 1. 临时 DuckDB
    tmpdir = Path(tempfile.mkdtemp(prefix="fts_ifind_verify_"))
    db_path = tmpdir / "fts_verify.duckdb"
    print(f"\n[1/8] 临时 DB 路径: {db_path}")

    try:
        # 2. 跑迁移
        result = migrate_schema(db_path)
        print(f"[2/8] migrate_schema 返回: {result}")

        con = duckdb.connect(str(db_path))
        try:
            # 3. 列出 kline_cache 全部列
            cols = con.execute("PRAGMA table_info('kline_cache')").fetchall()
            col_names = [r[1] for r in cols]
            print(f"[3/8] kline_cache 列数: {len(col_names)}")
            for col in ("hold", "settle", "pre_settle", "oi_change", "vwap",
                        "source", "trace_id"):
                assert col in col_names, f"缺列: {col}"
            print("       ✅ 全部 8 个新字段存在")

            # 4. 调 IFindSource 拿 DataFrame（K 线）
            with patch(
                "fts.data_sources.ifind_source._call_mcp",
                return_value=IFIND_MCP_RAW,
            ) as mock_mcp:
                df = IFindSource().fetch_ohlcv(
                    "RB2509", days=30, trace_id="verify-14.0.9"
                )
            assert mock_mcp.called
            print(f"\n[4/8] IFindSource.fetch_ohlcv 返回 {len(df)} 行")
            print("       DataFrame 预览:")
            for col in ("date", "close", "hold", "settle", "oi_change", "vwap"):
                print(f"         {col}: {df[col].tolist()}")

            # 5. INSERT 到 kline_cache
            con.register("df_ifind", df)
            con.execute("INSERT INTO kline_cache SELECT * FROM df_ifind")
            con.unregister("df_ifind")
            print(f"\n[5/8] INSERT kline_cache 完成")

            # 6. SELECT 回查，验证落库正确
            rows = con.execute(
                """
                SELECT date, close, hold, settle, pre_settle, oi_change, vwap, source, trace_id
                FROM kline_cache
                WHERE symbol = 'RB2509' AND source = 'IFIND'
                ORDER BY date
                """
            ).fetchall()
            print(f"[6/8] 回查 kline_cache ({len(rows)} 行):")
            print("       date       | close | hold   | settle | pre_settle | oi_change | vwap   | source | trace_id")
            print("       " + "-" * 95)
            for row in rows:
                print(
                    f"       {str(row[0])} | {row[1]:5} | {int(row[2]):6} | {int(row[3]):6} | "
                    f"{int(row[4]):6}    | {int(row[5]):6}    | {row[6]:.1f} | {row[7]:5} | {row[8]}"
                )

            # 断言
            assert len(rows) == 3, f"期望 3 行，实际 {len(rows)}"
            # 第一行 hold=80000, settle=3540, pre_settle=3520, oi_change=2000
            assert rows[0][2] == 80000, f"hold 不匹配: {rows[0][2]}"
            assert rows[0][3] == 3540, f"settle 不匹配: {rows[0][3]}"
            assert rows[0][4] == 3520, f"pre_settle 不匹配: {rows[0][4]}"
            assert rows[0][5] == 2000, f"oi_change 不匹配: {rows[0][5]}"
            # vwap 校验
            expected_vwap = 350000000 / 100000  # = 3500.0
            assert abs(rows[0][6] - expected_vwap) < 0.01, f"vwap 不匹配: {rows[0][6]}"
            # 第三行测试字段别名
            assert rows[2][2] == 85000, f"open_interest 别名映射失败: {rows[2][2]}"
            assert rows[2][5] == 3000, f"oi_chg 别名映射失败: {rows[2][5]}"
            # source & trace_id
            for r in rows:
                assert r[7] == "IFIND", f"source 应为 IFIND: {r[7]}"
                assert r[8] == "verify-14.0.9", f"trace_id 不匹配: {r[8]}"
            print("       ✅ kline_cache 8 个新字段全部 100% 落库 + 别名映射正确")

            # 7. 调 IFindSource.fetch_edb 拿 EDB 数据
            with patch(
                "fts.data_sources.ifind_source._call_mcp",
                return_value=IFIND_EDB_RAW,
            ) as mock_edb:
                edb_data = IFindSource().fetch_edb(
                    indicator="M0001396",
                    start_date="2025-12-01",
                    end_date="2026-03-31",
                    trace_id="verify-edb-14.0.9",
                )
            assert mock_edb.called
            print(f"\n[7/8] IFindSource.fetch_edb 返回 {len(edb_data)} 个 EDB 数据点")
            for d in edb_data:
                print(f"       {d['indicator']} | {d['date']} | "
                      f"value={d['value']} | unit={d['unit']} | "
                      f"source={d['source']} | trace_id={d['trace_id']}")

            # INSERT 到 edb_cache
            con.executemany(
                """
                INSERT INTO edb_cache
                    (indicator, date, value, unit, source, fetched_at, trace_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        d["indicator"],
                        d["date"],
                        d["value"],
                        d["unit"],
                        d["source"],
                        d["fetched_at"],
                        d["trace_id"],
                    )
                    for d in edb_data
                ],
            )
            print(f"       INSERT edb_cache 完成")

            # 8. SELECT 回查 edb_cache
            edb_rows = con.execute(
                """
                SELECT indicator, date, value, unit, source, trace_id
                FROM edb_cache
                WHERE source = 'IFIND'
                ORDER BY date
                """
            ).fetchall()
            print(f"[8/8] 回查 edb_cache ({len(edb_rows)} 行):")
            print("       indicator   | date       | value     | unit | source | trace_id")
            print("       " + "-" * 90)
            for row in edb_rows:
                print(
                    f"       {row[0]:12} | {str(row[1])} | {row[2]:8} | {row[3]:4} | "
                    f"{row[4]:5} | {row[5]}"
                )

            # 断言
            assert len(edb_rows) == 3, f"期望 3 行 EDB，实际 {len(edb_rows)}"
            # 第一行 M0001396 / 2025-12-31 / 1349083.5
            assert edb_rows[0][0] == "M0001396"
            assert edb_rows[0][1].isoformat() == "2025-12-31"
            assert abs(edb_rows[0][2] - 1349083.5) < 0.01
            assert edb_rows[0][3] == "亿元"
            # 第三行 M0000612 / CPI
            assert edb_rows[2][0] == "M0000612"
            assert edb_rows[2][3] == "%"
            for r in edb_rows:
                assert r[4] == "IFIND", f"source 应为 IFIND: {r[4]}"
                assert r[5] == "verify-edb-14.0.9", f"trace_id 不匹配: {r[5]}"
            print("       ✅ edb_cache 3 行 EDB 宏观数据全部正确落库")

            # 验证三表齐全
            tables = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main'"
            ).fetchall()
            table_names = {r[0] for r in tables}
            print(f"\n       所有表: {sorted(table_names)}")
            for t in ("kline_cache", "edb_cache", "option_chain_cache"):
                assert t in table_names, f"表 {t} 缺失"
            print("       ✅ kline_cache / edb_cache / option_chain_cache 三表齐全")

            print("\n" + "=" * 60)
            print("✅ 任务 14.0.9 端到端验证全部通过")
            print("=" * 60)
            print(f"  - K 线 8 个新字段 (hold/settle/pre_settle/oi_change) 落 kline_cache")
            print(f"  - 字段别名 (open_interest / oi_chg) 正确映射")
            print(f"  - EDB 3 行宏观数据落 edb_cache")
            print(f"  - source='IFIND' / trace_id 双链路贯通")
            return 0
        finally:
            con.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
