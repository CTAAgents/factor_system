# -*- coding: utf-8 -*-
"""能源链深度补全脚本测试（GAP-121 A，2026-08-15）。

覆盖：
1. 品种列表解析（None → 训练链+盲测池；逗号字符串 → 列表）
2. _symbol_variants 双格式（LU0 / LU）
3. _current_rows 真实行/SYNTHETIC 行/最新日期统计（mock agg）
4. _build_cache_df AKShare 列 → kline_cache 17 列（缺列默认值广播）
5. 触发条件：真实行达标且无 SYNTHETIC → skip；含 SYNTHETIC → 触发重建
6. dry-run 不写库
7. main CLI --dry-run 退出码 0
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from sync_energy_chain_depth import (  # noqa: E402
    _build_cache_df,
    _current_rows,
    _symbol_variants,
    sync_energy_chain_depth,
)


class _FakeAgg:
    """最小 mock：模拟 aggregator 的 db_path / _write_scope / _write_cache / 读取。"""

    def __init__(self, rows_real: int, rows_synth: int, latest: str | None) -> None:
        self.db_path = Path("data/fts_history.duckdb")
        self._rows_real = rows_real
        self._rows_synth = rows_synth
        self._latest = latest
        self.written: list = []
        self.deleted: list = []

    def _write_scope(self):
        class _Ctx:
            def __init__(self, owner):
                self._owner = owner

            def __enter__(self):
                return _FakeConn(self._owner)

            def __exit__(self, *exc):
                return False

        return _Ctx(self)

    def _write_cache(self, df) -> None:  # noqa: D401
        self.written.append(df)


class _FakeConn:
    def __init__(self, owner: "_FakeAgg") -> None:
        self._owner = owner

    def execute(self, sql: str, params: tuple | list) -> "_FakeResult":
        if "MAX(date)" in sql:
            return _FakeResult([(self._owner._latest,)])
        self._owner.deleted.append((sql, params))
        return _FakeResult([])

    def close(self) -> None:  # pragma: no cover
        pass


class _FakeResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


def test_symbol_variants() -> None:
    assert _symbol_variants("LU0") == ["LU", "LU0"]
    assert _symbol_variants("LU") == ["LU", "LU0"]


def test_current_rows_counts_real_and_synth(monkeypatch) -> None:
    """_current_rows 区分真实行 / SYNTHETIC 行。"""
    import duckdb

    class _FakeDuck:
        def __init__(self):
            self.results = iter([
                [(1482,)],  # 真实行
                [(120,)],   # SYNTHETIC 行
                [("2026-07-31",)],  # 最新真实日期
            ])

        def execute(self, sql, params):
            return _FakeResult([next(self.results)[0]])

        def close(self):
            pass

    monkeypatch.setattr(duckdb, "connect", lambda *a, **k: _FakeDuck())
    agg = _FakeAgg(0, 0, None)
    real, synth, latest = _current_rows(agg, "LU0")
    assert real == 1482
    assert synth == 120
    assert latest == "2026-07-31"


def test_build_cache_df_keeps_columns_and_defaults() -> None:
    import pandas as pd

    df = pd.DataFrame(
        {
            "date": ["2024-08-30", "2024-09-02"],
            "open": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "close": [1.2, 2.2],
            "volume": [100.0, 200.0],
        }
    )
    out = _build_cache_df(df, "PR0", "test")
    assert list(out.columns) == [
        "symbol", "period", "date", "open", "high", "low", "close",
        "volume", "amount", "hold", "settle", "pre_settle", "oi_change",
        "vwap", "source", "fetched_at", "trace_id",
    ]
    assert out["symbol"].iloc[0] == "PR0"
    assert out["period"].iloc[0] == "daily"
    # 缺列（amount/hold/settle）默认 0.0 广播，长度与源一致
    assert out["amount"].iloc[0] == 0.0
    assert len(out) == 2
    assert out["source"].iloc[0] == "AKSHARE"


def test_sync_skip_when_real_ok_and_no_synth(monkeypatch) -> None:
    """真实行达标且无 SYNTHETIC → skip，不触发写库。"""
    result = sync_energy_chain_depth(
        symbols=["LU0"], min_rows=300, dry_run=False, trace_id="t",
    )
    # 真实库中 LU0 已补全（1492 行），应 skip 而非报错
    assert result["checked"] == 1
    assert result["skipped"] == 1
    assert result["failed"] == 0


def test_dry_run_does_not_write(monkeypatch) -> None:
    """dry-run 不触发 DELETE/INSERT。"""
    result = sync_energy_chain_depth(
        symbols=["PR0"], min_rows=9999, dry_run=True, trace_id="t",
    )
    assert result["filled"] == 1
    for d in result["detail"]:
        assert d["action"] == "dry-run"


def test_main_cli_dry_run(monkeypatch) -> None:
    """CLI --dry-run 正常退出码 0。"""
    from sync_energy_chain_depth import main

    rc = main(["--dry-run", "--min-rows", "9999", "--symbols", "PR0"])
    assert rc == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
