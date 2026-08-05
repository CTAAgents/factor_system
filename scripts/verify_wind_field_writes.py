"""scripts/verify_wind_field_writes.py — 端到端验证 Wind 字段写入 kline_cache。

HARNESS §任务 14.0.8 验证:
    1. 启动临时 DuckDB
    2. 运行 migrate_schema
    3. mock Wind MCP 响应 → 通过 WindSource.fetch_ohlcv 拿到 DataFrame
    4. INSERT 到 kline_cache
    5. SELECT 回查 hold/settle/oi_change/pre_settle/vwap 字段是否正确落库

Usage:
    python scripts/verify_wind_field_writes.py
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

from fts.data_sources.migrate import migrate_schema  # noqa: E402
from fts.data_sources.wind_source import WindSource  # noqa: E402


# 模拟 Wind MCP 返回的 K 线（带所有 Wind 专属字段）
WIND_MCP_RAW = {
    "data": [
        {
            "date": "2026-08-01",
            "open": 3500, "high": 3550, "low": 3490, "close": 3540,
            "volume": 100000, "amount": 350000000,
            "oi": 80000, "settle": 3540, "pre_settle": 3520,
            "oi_chg": 2000,
        },
        {
            "date": "2026-08-04",
            "open": 3540, "high": 3600, "low": 3530, "close": 3580,
            "volume": 120000, "amount": 420000000,
            "oi": 82000, "settle": 3580, "pre_settle": 3540,
            "oi_chg": 2000,
        },
        {
            "date": "2026-08-05",
            "open": 3580, "high": 3620, "low": 3570, "close": 3610,
            "volume": 110000, "amount": 396000000,
            "open_interest": 85000,  # 字段别名测试
            "settle": 3610, "pre_settle": 3580,
            "open_interest_change": 3000,  # 字段别名测试
        },
    ],
}


def main() -> int:
    print("=" * 60)
    print("任务 14.0.8 验证: Wind 专属字段写入 kline_cache")
    print("=" * 60)

    # 1. 临时 DuckDB
    tmpdir = Path(tempfile.mkdtemp(prefix="fts_wind_verify_"))
    db_path = tmpdir / "fts_verify.duckdb"
    print(f"\n[1/6] 临时 DB 路径: {db_path}")

    try:
        # 2. 跑迁移
        result = migrate_schema(db_path)
        print(f"[2/6] migrate_schema 返回: {result}")

        # 3. 列出 kline_cache 全部列
        con = duckdb.connect(str(db_path))
        try:
            cols = con.execute("PRAGMA table_info('kline_cache')").fetchall()
            col_names = [r[1] for r in cols]
            print(f"[3/6] kline_cache 列数: {len(col_names)}")
            print(f"       列名: {col_names}")
            assert "hold" in col_names
            assert "settle" in col_names
            assert "pre_settle" in col_names
            assert "oi_change" in col_names
            assert "vwap" in col_names
            assert "source" in col_names
            assert "trace_id" in col_names
            print("       ✅ 全部 8 个新字段存在")

            # 4. 调 WindSource 拿 DataFrame
            with patch(
                "fts.data_sources.wind_source._call_mcp",
                return_value=WIND_MCP_RAW,
            ) as mock_mcp:
                df = WindSource().fetch_ohlcv(
                    "RB2509.SHFE", days=30, trace_id="verify-14.0.8"
                )
            assert mock_mcp.called
            print(f"\n[4/6] WindSource.fetch_ohlcv 返回 {len(df)} 行")
            print("       DataFrame 前 3 列预览:")
            for col in ("date", "open", "close", "volume", "hold", "settle", "oi_change", "vwap"):
                print(f"         {col}: {df[col].tolist()}")

            # 5. INSERT 到 kline_cache
            con.register("df_wind", df)
            con.execute(
                "INSERT INTO kline_cache SELECT * FROM df_wind"
            )
            con.unregister("df_wind")
            print(f"\n[5/6] INSERT 完成")

            # 6. SELECT 回查，验证落库正确
            rows = con.execute(
                """
                SELECT date, close, hold, settle, pre_settle, oi_change, vwap, source, trace_id
                FROM kline_cache
                WHERE symbol = 'RB2509.SHFE' AND source = 'WIND'
                ORDER BY date
                """
            ).fetchall()
            print(f"\n[6/6] 回查结果 ({len(rows)} 行):")
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
            # vwap 校验: amount/volume
            expected_vwap = 350000000 / 100000  # = 3500.0
            assert abs(rows[0][6] - expected_vwap) < 0.01, f"vwap 不匹配: {rows[0][6]}"
            # 第二行同样
            assert rows[1][2] == 82000
            assert rows[1][3] == 3580
            # 第三行测试字段别名 open_interest/open_interest_change
            assert rows[2][2] == 85000, f"open_interest 别名映射失败: {rows[2][2]}"
            assert rows[2][5] == 3000, f"open_interest_change 别名映射失败: {rows[2][5]}"
            # source & trace_id
            for r in rows:
                assert r[7] == "WIND", f"source 应为 WIND: {r[7]}"
                assert r[8] == "verify-14.0.8", f"trace_id 不匹配: {r[8]}"

            # 验证索引
            idx_list = con.execute(
                "SELECT index_name FROM duckdb_indexes() "
                "WHERE table_name='kline_cache'"
            ).fetchall()
            print(f"\n       索引列表: {[r[0] for r in idx_list]}")
            assert any(r[0] == "idx_kline_symbol_date_source" for r in idx_list), \
                "索引 idx_kline_symbol_date_source 缺失"
            print("       ✅ 索引 idx_kline_symbol_date_source 存在")

            # 验证 edb_cache / option_chain_cache 表存在
            tables = con.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main'"
            ).fetchall()
            table_names = {r[0] for r in tables}
            print(f"\n       所有表: {sorted(table_names)}")
            assert "kline_cache" in table_names
            assert "edb_cache" in table_names
            assert "option_chain_cache" in table_names
            print("       ✅ kline_cache / edb_cache / option_chain_cache 三表都存在")

            print("\n" + "=" * 60)
            print("✅ 任务 14.0.8 端到端验证全部通过")
            print("=" * 60)
            print(f"  - Wind 专属字段 (hold/settle/pre_settle/oi_change) 正确落库")
            print(f"  - 字段别名 (open_interest / open_interest_change) 正确映射")
            print(f"  - vwap 由 amount/volume 正确计算")
            print(f"  - source='WIND' / trace_id 正确写入")
            print(f"  - idx_kline_symbol_date_source 索引已建立")
            return 0
        finally:
            con.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
