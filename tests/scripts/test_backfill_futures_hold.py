"""GAP-083 阶段 B/C：scripts/backfill_futures_hold.py 测试。

覆盖:
- AKShare 真实 hold/settle 双格式（RB / RB0）UPDATE 回填
- 无效值（hold<=0 且 settle<=0）跳过，不覆盖真实数据
- 幂等（重复执行同值 UPDATE）
- dry_run 不实际写库
- 异常品种跳过不阻断
- CLI main 统计输出
- 方案 C：pre_settle = 前一交易日 settle 派生（零外部依赖，幂等回写）
"""

from __future__ import annotations

from scripts.backfill_futures_hold import (
    _write_pre_settle_derivation,
    backfill_hold_settle,
    derive_pre_settle,
    fetch_hold_settle_from_akshare,
    main,
    resolve_symbols,
    write_backfill,
)


class TestFetchHoldSettle:
    def test_column_alignment(self, mocker):
        """回归（预检发现）：fetch 返回 df 值必须与原始行对齐（非 NaN）。

        原始 akshare df 为 RangeIndex（0..n-1），目标 out 为 datetime index——
        pandas 索引对齐错位会导致全 NaN，必须 .to_numpy() 赋值。
        """
        import pandas as pd

        raw = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-08-11", "2026-08-10"]),
                "open": [3016.0, 2997.0],
                "high": [3019.0, 3016.0],
                "low": [2985.0, 2993.0],
                "close": [3016.0, 2997.0],
                "volume": [837090.0, 742347.0],
                "hold": [2158773.0, 2213769.0],
                "settle": [3002.0, 3002.0],
            },
            index=[0, 1],  # RangeIndex，模拟 akshare 返回
        )
        mocker.patch("akshare.futures_zh_daily_sina", return_value=raw)
        df = fetch_hold_settle_from_akshare("RB0")
        assert df is not None and len(df) == 2
        assert list(df.columns) == ["hold", "settle"]
        # 关键断言：值与行正确对齐，无 NaN
        assert df["hold"].iloc[0] == 2158773.0
        assert df["settle"].iloc[0] == 3002.0
        assert df["hold"].iloc[1] == 2213769.0
        assert not df["hold"].isna().any()
        assert not df["settle"].isna().any()

    def test_missing_columns_filled_zero(self, mocker):
        """缺 hold/settle 列 → 补 0.0（不抛）。"""
        import pandas as pd

        raw = pd.DataFrame(
            {"date": pd.to_datetime(["2026-08-11"]), "close": [3016.0]},
            index=[0],
        )
        mocker.patch("akshare.futures_zh_daily_sina", return_value=raw)
        df = fetch_hold_settle_from_akshare("RB0")
        assert df["hold"].iloc[0] == 0.0
        assert df["settle"].iloc[0] == 0.0

    def test_empty_or_no_date_returns_none(self, mocker):
        """空 df 或缺 date 列 → None（调用方跳过）。"""
        import pandas as pd

        mocker.patch("akshare.futures_zh_daily_sina", return_value=pd.DataFrame())
        assert fetch_hold_settle_from_akshare("RB0") is None

        mocker.patch(
            "akshare.futures_zh_daily_sina",
            return_value=pd.DataFrame({"open": [1.0]}, index=[0]),
        )
        assert fetch_hold_settle_from_akshare("RB0") is None


class TestResolveSymbols:
    def test_cli_symbols_priority(self):
        syms = resolve_symbols(["RB0", "CU0"], universe="core")
        assert syms == ["RB0", "CU0"]

    def test_universe_core(self, mocker):
        mocker.patch("fts.data_futures.FUTURES_CORE_SUBSET", ["RB0", "AU0"])
        assert resolve_symbols(None, universe="core") == ["RB0", "AU0"]


class TestWriteBackfill:
    def _conn(self, mocker):
        conn = mocker.MagicMock()
        return conn

    def test_updates_both_formats(self, mocker):
        """真实 hold/settle 双格式（RB / RB0）均 UPDATE。"""
        conn = self._conn(mocker)
        import pandas as pd

        df = pd.DataFrame(
            {"hold": [5000.0, 4800.0], "settle": [3100.0, 3150.0]},
            index=pd.to_datetime(["2026-01-02", "2026-01-01"]),
        )
        n = write_backfill("RB0", df, dry_run=False, conn=conn)
        assert n == 4  # 2 日期 × 2 格式
        calls = [c for c in conn.execute.call_args_list]
        assert len(calls) == 4
        # 参数化 SQL：symbol 值在第 3 个参数（索引 2），双格式 RB / RB0 各 2 次
        symbols_used = [c.args[1][2] for c in calls]
        assert symbols_used.count("RB") == 2
        assert symbols_used.count("RB0") == 2
        # 日期参数按行对齐
        assert calls[0].args[1][3] == "2026-01-02" or calls[0].args[1][3] == "2026-01-01"

    def test_invalid_values_skipped(self, mocker):
        """hold<=0 且 settle<=0 的行跳过，不覆盖真实数据。"""
        conn = self._conn(mocker)
        import pandas as pd

        df = pd.DataFrame(
            {"hold": [0.0, 4800.0], "settle": [0.0, 3150.0]},
            index=pd.to_datetime(["2026-01-02", "2026-01-01"]),
        )
        n = write_backfill("RB0", df, dry_run=False, conn=conn)
        assert n == 2  # 仅 2026-01-01 有效 × 2 格式
        assert conn.execute.call_count == 2

    def test_dry_run_no_write(self, mocker):
        """dry_run 不执行 UPDATE（仅计数）。"""
        conn = self._conn(mocker)
        import pandas as pd

        df = pd.DataFrame(
            {"hold": [5000.0], "settle": [3100.0]},
            index=pd.to_datetime(["2026-01-02"]),
        )
        n = write_backfill("RB0", df, dry_run=True, conn=conn)
        assert n == 2
        conn.execute.assert_not_called()

    def test_empty_df_no_op(self, mocker):
        conn = self._conn(mocker)
        import pandas as pd

        df = pd.DataFrame(columns=["hold", "settle"])
        assert write_backfill("RB0", df, dry_run=False, conn=conn) == 0
        conn.execute.assert_not_called()


class TestBackfillHoldSettle:
    def _fetch(self, mocker, df=None, exc=None):
        """mock fetch_hold_settle_from_akshare。"""
        def _fake(sym: str):
            if exc:
                raise exc
            return df
        return mocker.patch("scripts.backfill_futures_hold.fetch_hold_settle_from_akshare", side_effect=_fake)

    def test_normal_run(self, mocker):
        """正常回填：双格式 UPDATE + 统计。"""
        import pandas as pd

        df = pd.DataFrame(
            {"hold": [5000.0], "settle": [3100.0]},
            index=pd.to_datetime(["2026-01-02"]),
        )
        self._fetch(mocker, df=df)
        conn = mocker.MagicMock()
        mocker.patch("scripts.backfill_futures_hold._open_db", return_value=conn)
        mocker.patch("scripts.backfill_futures_hold.time.sleep")

        res = backfill_hold_settle(["RB0", "CU0"], dry_run=False)
        assert res["updated"] == 4  # 2 品种 × 1 日期 × 2 格式
        assert res["skipped"] == 0
        assert res["failed"] == []

    def test_failed_symbol_does_not_block(self, mocker):
        """异常品种跳过，不阻断其他品种。"""
        import pandas as pd

        df = pd.DataFrame(
            {"hold": [5000.0], "settle": [3100.0]},
            index=pd.to_datetime(["2026-01-02"]),
        )

        def _fake(sym: str):
            if sym == "RB0":
                raise RuntimeError("akshare boom")
            return df
        mocker.patch("scripts.backfill_futures_hold.fetch_hold_settle_from_akshare", side_effect=_fake)
        conn = mocker.MagicMock()
        mocker.patch("scripts.backfill_futures_hold._open_db", return_value=conn)
        mocker.patch("scripts.backfill_futures_hold.time.sleep")

        res = backfill_hold_settle(["RB0", "CU0"], dry_run=False)
        assert res["updated"] == 2  # 仅 CU0
        assert len(res["failed"]) == 1
        assert "RB0" in res["failed"][0]

    def test_empty_fetch_skipped(self, mocker):
        """AKShare 返回空 → 品种计入 skipped。"""
        import pandas as pd

        empty = pd.DataFrame(columns=["hold", "settle"])
        self._fetch(mocker, df=empty)
        conn = mocker.MagicMock()
        mocker.patch("scripts.backfill_futures_hold._open_db", return_value=conn)
        mocker.patch("scripts.backfill_futures_hold.time.sleep")

        res = backfill_hold_settle(["RB0"], dry_run=False)
        assert res["updated"] == 0
        assert res["skipped"] == 1


class TestMain:
    def test_cli_dry_run(self, mocker):
        """CLI --dry-run 全流程可执行并输出统计。"""
        import pandas as pd

        df = pd.DataFrame(
            {"hold": [5000.0], "settle": [3100.0]},
            index=pd.to_datetime(["2026-01-02"]),
        )
        mocker.patch("scripts.backfill_futures_hold.fetch_hold_settle_from_akshare", return_value=df)
        conn = mocker.MagicMock()
        mocker.patch("scripts.backfill_futures_hold._open_db", return_value=conn)
        mocker.patch("scripts.backfill_futures_hold.time.sleep")
        mocker.patch(
            "sys.argv",
            ["backfill_futures_hold.py", "--symbols", "RB0", "--dry-run"],
        )
        rc = main()
        assert rc == 0


class TestDerivePreSettle:
    """方案 C：pre_settle = 前一交易日 settle 派生（零外部依赖，幂等回写）。"""

    @staticmethod
    def _conn(mocker, rows_map):
        """mock conn：SELECT 返回 rows_map[symbol_key]，UPDATE 返回空。"""
        conn = mocker.MagicMock()

        def _exec(sql, params):
            ret = mocker.MagicMock()
            if sql.startswith("SELECT"):
                ret.fetchall.return_value = rows_map.get(params[0], [])
            return ret

        conn.execute.side_effect = _exec
        return conn

    @staticmethod
    def _updates(conn):
        return [c for c in conn.execute.call_args_list if c.args[0].startswith("UPDATE")]

    def test_derives_prev_settle_across_rows(self, mocker):
        """pre_settle 无效行 = 前一日 settle（升序）；首行无前值跳过。"""
        rows = [
            ("2026-01-02", 3100.0, None),
            ("2026-01-03", 3120.0, None),
            ("2026-01-05", 3110.0, None),
        ]
        conn = self._conn(mocker, {"RB": rows, "RB0": []})
        n = _write_pre_settle_derivation("RB", dry_run=False, conn=conn, trace_id="t")
        assert n == 2
        updates = self._updates(conn)
        assert len(updates) == 2
        assert updates[0].args[1][0] == 3100.0
        assert updates[0].args[1][1] == "RB"
        assert updates[0].args[1][2] == "2026-01-03"
        assert updates[1].args[1][0] == 3120.0
        assert updates[1].args[1][1] == "RB"
        assert updates[1].args[1][2] == "2026-01-05"

    def test_existing_valid_presettle_not_overwritten(self, mocker):
        """已有有效 pre_settle 不覆盖（增强层权威值优先）。"""
        rows = [
            ("2026-01-02", 3100.0, 3300.0),  # 有效 → 不更新
            ("2026-01-03", 3120.0, None),  # 无效 → 派生 3100
        ]
        conn = self._conn(mocker, {"RB": rows, "RB0": []})
        n = _write_pre_settle_derivation("RB", dry_run=False, conn=conn, trace_id="t")
        assert n == 1
        updates = self._updates(conn)
        assert len(updates) == 1
        assert updates[0].args[1][0] == 3100.0
        assert updates[0].args[1][2] == "2026-01-03"

    def test_dry_run_no_update(self, mocker):
        """dry_run 只计数不执行 UPDATE。"""
        rows = [
            ("2026-01-02", 3100.0, None),
            ("2026-01-03", 3120.0, None),
        ]
        conn = self._conn(mocker, {"RB": rows, "RB0": []})
        n = _write_pre_settle_derivation("RB", dry_run=True, conn=conn, trace_id="t")
        assert n == 1  # 首行跳过，第二行可派生
        assert self._updates(conn) == []

    def test_invalid_settle_keeps_prev(self, mocker):
        """settle 无效（0/None）时不推进前值，后续仍用最近有效 settle。"""
        rows = [
            ("2026-01-02", 3100.0, None),
            ("2026-01-03", 0.0, None),  # settle 无效，不推进
            ("2026-01-05", 3120.0, None),
        ]
        conn = self._conn(mocker, {"RB": rows, "RB0": []})
        n = _write_pre_settle_derivation("RB", dry_run=False, conn=conn, trace_id="t")
        assert n == 2
        updates = self._updates(conn)
        # 01-03 用 3100；01-05 前值仍为 3100（01-03 的 settle=0 未推进）
        assert [u.args[1][0] for u in updates] == [3100.0, 3100.0]
        assert [u.args[1][2] for u in updates] == ["2026-01-03", "2026-01-05"]

    def test_both_formats_rb_rb0(self, mocker):
        """双格式 RB / RB0 均处理。"""
        rows = [("2026-01-02", 3100.0, None), ("2026-01-03", 3120.0, None)]
        conn = self._conn(mocker, {"RB": rows, "RB0": rows})
        n = _write_pre_settle_derivation("RB0", dry_run=False, conn=conn, trace_id="t")
        assert n == 2  # RB 1 行 + RB0 1 行
        updates = self._updates(conn)
        assert len(updates) == 2
        assert {u.args[1][1] for u in updates} == {"RB", "RB0"}

    def test_no_valid_settle_skips_all(self, mocker):
        """全库无有效 settle 时跳过（不派生）。"""
        rows = [("2026-01-02", 0.0, None), ("2026-01-03", None, None)]
        conn = self._conn(mocker, {"RB": rows, "RB0": []})
        n = _write_pre_settle_derivation("RB", dry_run=False, conn=conn, trace_id="t")
        assert n == 0
        assert self._updates(conn) == []

    def test_symbol_exception_does_not_block(self, mocker):
        """单品种派生失败不阻断其他品种。"""
        mocker.patch(
            "scripts.backfill_futures_hold._write_pre_settle_derivation",
            side_effect=RuntimeError("derive boom"),
        )
        res = derive_pre_settle(["RB0", "CU0"], dry_run=False, conn=mocker.MagicMock(), trace_id="t")
        assert res["updated"] == 0
        assert len(res["failed"]) == 2
        assert "RB0" in res["failed"][0]

    def test_aggregate_counts(self, mocker):
        """derive_pre_settle 汇总各品种更新行数。"""
        rows = [("2026-01-02", 3100.0, None), ("2026-01-03", 3120.0, None)]
        conn = self._conn(mocker, {"RB": rows, "RB0": [], "CU": rows, "CU0": []})
        res = derive_pre_settle(["RB0", "CU0"], dry_run=False, conn=conn, trace_id="t")
        assert res["symbols"] == 2
        assert res["updated"] == 2  # RB 1 + CU 1
        assert res["failed"] == []

    def test_cli_derive_presettle_dry_run(self, mocker):
        """CLI --derive-presettle --dry-run 可执行，只读不写。"""
        conn = self._conn(mocker, {"RB": [], "RB0": []})
        mocker.patch("scripts.backfill_futures_hold._open_db", return_value=conn)
        mocker.patch(
            "sys.argv",
            ["backfill_futures_hold.py", "--symbols", "RB0", "--derive-presettle", "--dry-run"],
        )
        rc = main()
        assert rc == 0
        assert self._updates(conn) == []
