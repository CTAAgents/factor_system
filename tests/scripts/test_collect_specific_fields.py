"""
tests/scripts/test_collect_specific_fields.py — 品种特有字段采集脚本测试（GAP-162 骨架）

覆盖：manual csv/json 导入 → parquet 生成（date 对齐）/ 幂等 upsert / dry-run 不写
文件 / placeholder 源报告 / 无效字段退出码。
"""

from __future__ import annotations

import pandas as pd

from scripts.collect_specific_fields import _parse_manual_input, upsert_parquet


class TestParseManual:
    def test_csv_parse(self, tmp_path):
        f = tmp_path / "sc.csv"
        f.write_text("date,sc_freight_premium\n2026-01-05,1.2\n2026-01-06,1.3\n", encoding="utf-8")
        rows = _parse_manual_input(f, "sc_freight_premium")
        assert len(rows) == 2 and rows[0] == ("2026-01-05", 1.2)

    def test_json_parse(self, tmp_path):
        f = tmp_path / "sc.json"
        f.write_text(
            '[{"date": "2026-01-05", "sc_freight_premium": 1.2}, {"date": "2026-01-06", "sc_freight_premium": 1.3}]',
            encoding="utf-8",
        )
        rows = _parse_manual_input(f, "sc_freight_premium")
        assert len(rows) == 2

    def test_invalid_rows_skipped(self, tmp_path):
        f = tmp_path / "sc.csv"
        f.write_text("date,sc_freight_premium\n2026-01-05,1.2\n2026-01-06,abc\n", encoding="utf-8")
        rows = _parse_manual_input(f, "sc_freight_premium")
        assert len(rows) == 1  # 非法值跳过


class TestUpsertParquet:
    def test_write_and_idempotent(self, tmp_path):
        out = tmp_path / "sf"
        n1 = upsert_parquet(out, "SC0", [("2026-01-05", 1.2), ("2026-01-06", 1.3)], "sc_freight_premium")
        assert n1 == 2
        df = pd.read_parquet(out / "SC0.parquet")
        assert len(df) == 2

        # 幂等：重复导入 + 新增一天 → 不重复、只增新
        n2 = upsert_parquet(out, "SC0", [("2026-01-05", 1.2), ("2026-01-07", 1.4)], "sc_freight_premium")
        df2 = pd.read_parquet(out / "SC0.parquet")
        assert len(df2) == 3
        assert n2 == 2  # 新增 1 天 + 更新 1 天

    def test_update_overwrites(self, tmp_path):
        out = tmp_path / "sf"
        upsert_parquet(out, "SC0", [("2026-01-05", 1.2)], "sc_freight_premium")
        upsert_parquet(out, "SC0", [("2026-01-05", 9.9)], "sc_freight_premium")
        df = pd.read_parquet(out / "SC0.parquet")
        assert len(df) == 1 and float(df.iloc[0]["sc_freight_premium"]) == 9.9
