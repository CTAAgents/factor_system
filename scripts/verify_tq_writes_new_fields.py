"""scripts/verify_tq_writes_new_fields.py — 验证 TQ-Local 适配器写入新字段到 kline_cache。

HARNESS §5.4: 端到端验证 — 用 mock 模拟 TQ-Local 响应，跑适配器→写库→查库，
确认 hold/settle/pre_settle/oi_change/vwap 等 8 个新字段全部正确填充。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fts.data_sources.migrate import migrate_schema  # noqa: E402
from fts.data_sources.tq_source import TQLocalSource  # noqa: E402

TEST_DB = ROOT / "data" / "fts_verify_tq.duckdb"


def _ok(body: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = body
    r.raise_for_status = MagicMock()
    return r


def main() -> int:
    # ─── Step 1: 准备测试 DB（空 DB，走完整迁移）──
    if TEST_DB.exists():
        TEST_DB.unlink()
    print("=" * 70)
    print("Step 1: 准备空 DB + 迁移到 v2.3.0")
    print("=" * 70)
    result = migrate_schema(TEST_DB)
    print(f"  迁移结果: {result}")
    assert result["indexes_created"] == 1

    # ─── Step 2: mock TQ-Local 响应（带完整 13 字段）──
    print()
    print("=" * 70)
    print("Step 2: Mock TQ-Local 响应")
    print("=" * 70)
    tq_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "symbol": "RB0.SHFE",
            "rows": [
                {
                    "date": "2026-08-01",
                    "open": 3500,
                    "high": 3550,
                    "low": 3490,
                    "close": 3540,
                    "volume": 100000,
                    "amount": 350000000,
                    "hold": 80000,
                    "settle": 3540,
                    "pre_settle": 3520,
                    "oi_change": 2000,
                },
                {
                    "date": "2026-08-04",
                    "open": 3540,
                    "high": 3600,
                    "low": 3530,
                    "close": 3580,
                    "volume": 120000,
                    "amount": 420000000,
                    "hold": 82000,
                    "settle": 3580,
                    "pre_settle": 3540,
                    "oi_change": 2000,
                },
            ],
        },
    }

    # ─── Step 3: 通过适配器拉数据 ──
    print()
    print("=" * 70)
    print("Step 3: TQLocalSource.fetch_ohlcv('RB0')")
    print("=" * 70)
    with patch("fts.data_sources.tq_source.requests.post", return_value=_ok(tq_response)):
        df = TQLocalSource().fetch_ohlcv("RB0", days=30, trace_id="verify-001")
    print(f"  返回 DataFrame: {len(df)} 行 × {len(df.columns)} 列")
    print(f"  列: {list(df.columns)}")
    print("  样本:")
    for col in df.columns:
        print(f"    {col:<14} {df[col].iloc[0]}")

    # ─── Step 4: 写入 kline_cache ──
    print()
    print("=" * 70)
    print("Step 4: 写入 DuckDB kline_cache")
    print("=" * 70)
    con = duckdb.connect(str(TEST_DB))
    try:
        con.register("df_view", df)
        con.execute("INSERT INTO kline_cache SELECT * FROM df_view")
        con.commit()
        cnt = con.execute("SELECT count(*) FROM kline_cache").fetchone()[0]
        print(f"  写入后 kline_cache 总条数: {cnt}")
        assert cnt == 2
    finally:
        con.close()

    # ─── Step 5: 验证 8 个新字段全部非 NULL ──
    print()
    print("=" * 70)
    print("Step 5: 验证 8 个新字段全部填充")
    print("=" * 70)
    con = duckdb.connect(str(TEST_DB), read_only=True)
    try:
        new_field_stats = con.execute("""
            SELECT
                sum(CASE WHEN hold IS NOT NULL THEN 1 ELSE 0 END) AS hold_filled,
                sum(CASE WHEN settle IS NOT NULL THEN 1 ELSE 0 END) AS settle_filled,
                sum(CASE WHEN pre_settle IS NOT NULL THEN 1 ELSE 0 END) AS pre_settle_filled,
                sum(CASE WHEN oi_change IS NOT NULL THEN 1 ELSE 0 END) AS oi_change_filled,
                sum(CASE WHEN vwap IS NOT NULL THEN 1 ELSE 0 END) AS vwap_filled,
                sum(CASE WHEN source IS NOT NULL THEN 1 ELSE 0 END) AS source_filled,
                sum(CASE WHEN trace_id IS NOT NULL THEN 1 ELSE 0 END) AS trace_id_filled,
                sum(CASE WHEN fetched_at IS NOT NULL THEN 1 ELSE 0 END) AS fetched_at_filled
            FROM kline_cache
        """).fetchone()
        expected = 2
        field_names = ["hold", "settle", "pre_settle", "oi_change", "vwap", "source", "trace_id", "fetched_at"]
        all_pass = True
        for name, val in zip(field_names, new_field_stats):
            ok = "✓" if val == expected else "✗"
            print(f"  {ok} {name:<14} 填充数={val}/{expected}")
            if val != expected:
                all_pass = False
        assert all_pass, "部分新字段未填充"

        # ─── Step 6: 验证 vwap 计算正确 ──
        print()
        print("=" * 70)
        print("Step 6: 验证 vwap = amount / volume")
        print("=" * 70)
        rows = con.execute("""
            SELECT date, amount, volume, vwap
            FROM kline_cache ORDER BY date
        """).fetchall()
        for d, amt, vol, vwap in rows:
            expected_vwap = amt / vol
            ok = "✓" if abs(vwap - expected_vwap) < 0.01 else "✗"
            print(
                f"  {ok} {str(d):<12} amount={amt:>12,} volume={vol:>10,} vwap={vwap:>10.2f} (期望 {expected_vwap:.2f})"
            )
            assert abs(vwap - expected_vwap) < 0.01, f"vwap 不对: {vwap} != {expected_vwap}"

        # ─── Step 7: 验证 source 标识 ──
        print()
        print("=" * 70)
        print("Step 7: 验证 source 字段标识 TQ_LOCAL")
        print("=" * 70)
        sources = con.execute("SELECT DISTINCT source FROM kline_cache").fetchall()
        print(f"  数据源: {[s[0] for s in sources]}")
        assert all(s[0] == "TQ_LOCAL" for s in sources)

        # ─── Step 8: 验证 trace_id 贯通 ──
        print()
        print("=" * 70)
        print("Step 8: 验证 trace_id 全链路")
        print("=" * 70)
        traces = con.execute("SELECT DISTINCT trace_id, count(*) FROM kline_cache GROUP BY trace_id").fetchall()
        for t, n in traces:
            print(f"  trace_id={t!r}  行数={n}")
            assert t == "verify-001"

        # ─── Step 9: 验证索引命中查询 ──
        print()
        print("=" * 70)
        print("Step 9: 验证 idx_kline_symbol_date_source 索引可查")
        print("=" * 70)
        idx = con.execute("SELECT index_name FROM duckdb_indexes() WHERE table_name='kline_cache'").fetchall()
        print(f"  索引: {[r[0] for r in idx]}")
        assert any(r[0] == "idx_kline_symbol_date_source" for r in idx)

        # 实际按 (symbol, date, source) 查
        sample = con.execute("""
            SELECT symbol, date, source, hold, settle, vwap
            FROM kline_cache
            WHERE symbol='RB0.SHFE' AND source='TQ_LOCAL'
            ORDER BY date DESC LIMIT 1
        """).fetchone()
        print(f"  索引查询样本: {sample}")
    finally:
        con.close()

    # ─── 清理 ──
    print()
    print("=" * 70)
    print("清理 + 收尾")
    print("=" * 70)
    TEST_DB.unlink()
    print(f"  ✓ 已删除 {TEST_DB.name}")
    print()
    print("=" * 70)
    print("✅ 全部 8 个新字段验证通过")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
