"""tests/data_sources/test_macro_aligner.py — 宏观字段增强层测试（v2.32.0）。

覆盖: 时序对齐 / 发布滞后 / 缺数据降级 / 批量注入 / edb_cache 读写。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fts.data_sources.macro_aligner import (  # noqa: E402
    MACRO_FIELD_QUERIES,
    MacroFieldAligner,
    inject_macro_fields,
)


def _make_ohlcv(days: int = 60, start: str = "2026-01-05") -> pd.DataFrame:
    """构造测试 OHLCV DataFrame（DatetimeIndex，交易日）。"""
    idx = pd.bdate_range(start=start, periods=days)
    n = len(idx)
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "open": 3000 + np.cumsum(rng.normal(0, 5, n)),
            "high": 0.0,
            "low": 0.0,
            "close": 0.0,
            "volume": rng.integers(1000, 5000, n).astype(float),
        },
        index=idx,
    )


def _make_macro(dates: list[str], values: list[float]) -> pd.Series:
    """构造月度宏观序列（月末日期）。"""
    return pd.Series(values, index=pd.to_datetime(dates))


# ─── align：时序对齐 ─────────────────────────────────────


class TestAlign:
    def test_align_ffill_to_trading_days(self) -> None:
        """月度宏观序列前向填充到交易日。"""
        df = _make_ohlcv(days=30, start="2026-01-05")
        macro = _make_macro(
            ["2025-12-31", "2026-01-31"],
            [100.0, 110.0],
        )
        out = MacroFieldAligner.align(df, macro, "export")
        assert "export" in out.columns
        # 1 月交易日取 2025-12 的值，2 月起取 1 月值
        jan = out[out.index < "2026-02-01"]["export"]
        assert (jan == 100.0).all()
        # 对齐不修改原 df
        assert "export" not in df.columns

    def test_align_lag_days_no_future_data(self) -> None:
        """发布滞后: lag_days 内数据不可用（防未来函数）。"""
        df = _make_ohlcv(days=60, start="2026-01-05")
        macro = _make_macro(["2026-01-31"], [110.0])
        out = MacroFieldAligner.align(df, macro, "export", lag_days=30)
        # 1-31 + 30 天 = 3-2 才可用，之前应为 NaN
        before = out[out.index < "2026-03-02"]["export"]
        assert before.isna().all()
        after = out[out.index >= "2026-03-02"]["export"]
        assert (after == 110.0).all()

    def test_align_missing_macro_returns_original(self) -> None:
        """宏观序列缺失 → 不注入列（返回原 df 副本）。"""
        df = _make_ohlcv()
        out = MacroFieldAligner.align(df, None, "export")
        assert "export" not in out.columns
        assert len(out) == len(df)


# ─── inject：批量注入 ────────────────────────────────────


class _FakeSource:
    """模拟 IFindSource.get_macro_series。"""

    def __init__(self, data: dict[str, pd.Series] | None = None) -> None:
        self._data = data or {}

    def get_macro_series(self, indicator, db_path=None, trace_id=""):
        # 反查映射表: indicator 中文名 → field 名
        field = next((k for k, v in MACRO_FIELD_QUERIES.items() if v == indicator), indicator)
        return self._data.get(field)


class TestInject:
    def test_inject_batch(self) -> None:
        """批量注入 export / cpi 两列。"""
        df = _make_ohlcv(days=30, start="2026-01-05")
        src = _FakeSource({
            "export": _make_macro(["2025-12-31", "2026-01-31"], [100.0, 110.0]),
            "cpi": _make_macro(["2025-12-31"], [2.5]),
        })
        aligner = MacroFieldAligner(source=src)
        out = aligner.inject(df, fields=["export", "cpi"])
        assert "export" in out.columns
        assert "cpi" in out.columns
        assert len(out) == len(df)

    def test_inject_partial_failure(self) -> None:
        """某字段数据缺失 → 该列不注入，其余字段正常，不阻断。"""
        df = _make_ohlcv(days=30, start="2026-01-05")
        src = _FakeSource({
            "export": _make_macro(["2025-12-31"], [100.0]),
        })
        aligner = MacroFieldAligner(source=src)
        out = aligner.inject(df, fields=["export", "us_bond"])
        assert "export" in out.columns
        assert "us_bond" not in out.columns

    def test_inject_macro_fields_entrypoint(self) -> None:
        """模块级便捷入口等价于 aligner.inject。"""
        df = _make_ohlcv(days=30, start="2026-01-05")
        src = _FakeSource({
            "export": _make_macro(["2025-12-31"], [100.0]),
        })
        aligner = MacroFieldAligner(source=src)
        out = inject_macro_fields(df, aligner, fields=["export"])
        assert "export" in out.columns


# ─── IFindSource.get_macro_series：edb_cache 读写 ────────


class TestGetMacroSeries:
    def test_cache_hit_returns_series(self, tmp_path: Path) -> None:
        """edb_cache 已有数据 → 直接返回（不调 MCP）。"""
        import duckdb

        from fts.data_sources.ifind_source import IFindSource

        db = tmp_path / "t.duckdb"
        con = duckdb.connect(str(db))
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS edb_cache (
                indicator VARCHAR, date DATE, value DOUBLE, unit VARCHAR,
                source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR,
                PRIMARY KEY (indicator, date, source)
            )
            """
        )
        con.execute(
            "INSERT INTO edb_cache (indicator, date, value, unit, source) VALUES "
            "('中国出口金额当月值', '2026-01-31', 100.0, '亿美元', 'IFIND')"
        )
        con.close()

        src = IFindSource()
        src.fetch_edb = lambda *a, **k: (_ for _ in ()).throw(  # noqa: BLE001
            AssertionError("不应调用 MCP")
        )
        series = src.get_macro_series("中国出口金额当月值", db_path=db)
        assert series is not None
        assert series.iloc[0] == 100.0

    def test_miss_fetch_and_write(self, tmp_path: Path, monkeypatch) -> None:
        """缓存 miss → 拉取 → 幂等写回。"""
        from fts.data_sources import ifind_source

        db = tmp_path / "t.duckdb"
        src = ifind_source.IFindSource()

        def fake_fetch(indicator, start_date="", end_date="", trace_id=""):
            return [{
                "indicator": indicator,
                "date": "2026-01-31",
                "value": 100.0,
                "unit": "亿美元",
            }]

        monkeypatch.setattr(src, "fetch_edb", fake_fetch)
        series = src.get_macro_series("中国出口金额当月值", db_path=db)
        assert series is not None
        assert series.iloc[0] == 100.0
        # 二次调用应命中缓存（fetch 不再执行）
        calls = {"n": 0}

        def fake_fetch2(indicator, start_date="", end_date="", trace_id=""):
            calls["n"] += 1
            return fake_fetch(indicator, start_date, end_date, trace_id)

        monkeypatch.setattr(src, "fetch_edb", fake_fetch2)
        src.get_macro_series("中国出口金额当月值", db_path=db)
        assert calls["n"] == 0

    def test_all_fail_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        """缓存无数据且拉取失败 → None。"""
        from fts.data_sources import ifind_source

        db = tmp_path / "t.duckdb"
        src = ifind_source.IFindSource()
        monkeypatch.setattr(src, "fetch_edb", lambda *a, **k: None)
        assert src.get_macro_series("中国出口金额当月值", db_path=db) is None


# ─── MACRO_FIELD_QUERIES ─────────────────────────────────


class TestMapping:
    def test_queries_cover_macro_fields(self) -> None:
        """映射表覆盖宏观因子常用字段。"""
        for f in ("export", "import_data", "cpi", "rate", "us_bond"):
            assert f in MACRO_FIELD_QUERIES
