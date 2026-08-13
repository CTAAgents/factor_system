"""tests/data_sources/test_macro_panel_injection.py — 面板级宏观注入 helper 测试（GAP-088，v2.103.0）。

覆盖: 多标的 5 列注入 / 发布滞后防未来函数 / 字段缺失降级 / 单标的失败不阻断 /
      拉取失败整体降级 / 跨标的共享序列只拉一次 / cli.py 期货演化路径接线。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fts.cli import _prepare_futures_data  # noqa: E402
from fts.data_sources.macro_aligner import (  # noqa: E402
    MACRO_FIELD_QUERIES,
    MacroFieldAligner,
    inject_macro_fields_to_panel,
)


def _make_ohlcv(days: int = 30, start: str = "2026-01-05") -> pd.DataFrame:
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


class _FakeSource:
    """模拟 source.get_macro_series（indicator 中文名 → field 名反查）。"""

    def __init__(self, data: dict | None = None, fail_on: list[str] | None = None) -> None:
        self._data = data or {}
        self._fail_on = set(fail_on or ())
        self.calls: list[str] = []

    def get_macro_series(self, indicator, db_path=None, trace_id=""):
        self.calls.append(indicator)
        field = next((k for k, v in MACRO_FIELD_QUERIES.items() if v == indicator), indicator)
        if field in self._fail_on:
            raise RuntimeError(f"fetch fail {field}")
        return self._data.get(field)


class TestPanelHelper:
    def test_inject_5_columns_multi_symbols_shared_fetch(self) -> None:
        """多标的注入 5 列；跨标的共享序列只拉取一次（每字段 1 次）。"""
        df1 = _make_ohlcv(days=30)
        df2 = _make_ohlcv(days=30)
        macro = _make_macro(["2025-12-31"], [100.0])
        src = _FakeSource(
            {
                "export": macro,
                "import_data": macro,
                "cpi": macro,
                "rate": macro,
                "us_bond": macro,
            }
        )
        aligner = MacroFieldAligner(source=src)
        panel = {"RB0": df1, "CU0": df2}
        out = inject_macro_fields_to_panel(panel, aligner=aligner, lag_days=0)
        for sym in ("RB0", "CU0"):
            for field in ("export", "import_data", "cpi", "rate", "us_bond"):
                assert field in out[sym].columns, f"{sym} 缺 {field}"
                assert (out[sym][field] == 100.0).all()
        # 跨标的共享：每字段恰好拉取 1 次（共 5 次，非 5×标的数）
        assert len(src.calls) == 5
        # 原 panel 未被修改
        assert "export" not in df1.columns

    def test_lag_days_prevents_future_data(self) -> None:
        """发布滞后: lag_days 内数据不可用（防未来函数）。"""
        df = _make_ohlcv(days=60)
        macro = _make_macro(["2026-01-31"], [110.0])
        src = _FakeSource({"export": macro})
        out = inject_macro_fields_to_panel({"RB0": df}, aligner=MacroFieldAligner(source=src), lag_days=30)
        before = out["RB0"][out["RB0"].index < "2026-03-02"]["export"]
        assert before.isna().all()
        after = out["RB0"][out["RB0"].index >= "2026-03-02"]["export"]
        assert (after == 110.0).all()

    def test_missing_field_not_injected(self) -> None:
        """某字段无数据 → 该列不注入，其余字段正常注入。"""
        df = _make_ohlcv(days=30)
        src = _FakeSource({"export": _make_macro(["2025-12-31"], [100.0])})
        out = inject_macro_fields_to_panel(
            {"RB0": df},
            fields=["export", "us_bond"],
            aligner=MacroFieldAligner(source=src),
            lag_days=0,
        )
        assert "export" in out["RB0"].columns
        assert "us_bond" not in out["RB0"].columns

    def test_symbol_failure_does_not_block_others(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """单标的注入失败 → 跳过该标的，其余标的不受影响。"""
        df_ok = _make_ohlcv(days=30, start="2026-01-05")
        df_bad = _make_ohlcv(days=30, start="2026-01-06")
        src = _FakeSource({"export": _make_macro(["2025-12-31"], [100.0])})
        real_align = MacroFieldAligner.align

        def _flaky_align(df, macro, field, lag_days=0):  # noqa: ANN001
            if df.index[0] == pd.Timestamp("2026-01-06"):
                raise ValueError("boom")
            return real_align(df, macro, field, lag_days=lag_days)

        monkeypatch.setattr(MacroFieldAligner, "align", staticmethod(_flaky_align))
        out = inject_macro_fields_to_panel(
            {"RB0": df_bad, "CU0": df_ok},
            fields=["export"],
            aligner=MacroFieldAligner(source=src),
            lag_days=0,
        )
        # 失败标的保持原样（无宏观列）
        assert "export" not in out["RB0"].columns
        # 正常标的成功注入
        assert "export" in out["CU0"].columns

    def test_fetch_failure_all_degrades(self) -> None:
        """宏观拉取失败 → 全部字段不注入，不抛异常（因子走 close 代理）。"""
        df = _make_ohlcv(days=30)
        src = _FakeSource({}, fail_on=["export", "import_data", "cpi", "rate", "us_bond"])
        panel = {"RB0": df}
        out = inject_macro_fields_to_panel(
            panel,
            aligner=MacroFieldAligner(source=src),
            lag_days=0,
        )
        assert set(out.keys()) == {"RB0"}
        assert "export" not in out["RB0"].columns

    def test_none_or_empty_fields_noop(self) -> None:
        """panel None / fields 空 → 原样返回，不拉取。"""
        assert inject_macro_fields_to_panel(None) is None
        df = _make_ohlcv()
        src = _FakeSource({})
        out = inject_macro_fields_to_panel(
            {"RB0": df},
            fields=[],
            aligner=MacroFieldAligner(source=src),
        )
        assert "export" not in out["RB0"].columns
        assert src.calls == []


class TestCliFuturesPrepareMacro:
    """cli._prepare_futures_data 宏观注入接线（GAP-088 v2.103.0）。"""

    def _setup_panel(self) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        df = _make_ohlcv(days=10)
        panel = {"RB0": df}
        dates = pd.DatetimeIndex(df.index)
        return panel, dates

    def test_prepare_futures_injects_macro(self, capsys: pytest.CaptureFixture) -> None:
        """面板构建后调用宏观注入 helper，注入列保留。"""
        panel, dates = self._setup_panel()
        injected = {sym: df.copy() for sym, df in panel.items()}
        injected["RB0"]["export"] = 100.0
        with (
            patch("fts.data_futures.get_dynamic_core_subset", return_value=["RB0"]),
            patch("fts.cli.FTSDataProvider") as m_provider,
            patch("fts.data_sources.macro_aligner.inject_macro_fields_to_panel", return_value=injected) as m_inject,
        ):
            m_provider.return_value.get_futures_panel.return_value = (panel, dates)
            result, _dates, _fwd = _prepare_futures_data(days=700, max_symbols=0)
        m_inject.assert_called_once()
        assert "export" in result["RB0"].columns
        assert "宏观字段注入完成" in capsys.readouterr().out

    def test_prepare_futures_macro_failure_degrades(self, capsys: pytest.CaptureFixture) -> None:
        """宏观注入异常 → 降级不阻断（面板原样返回，不抛异常）。"""
        panel, dates = self._setup_panel()
        with (
            patch("fts.data_futures.get_dynamic_core_subset", return_value=["RB0"]),
            patch("fts.cli.FTSDataProvider") as m_provider,
            patch(
                "fts.data_sources.macro_aligner.inject_macro_fields_to_panel",
                side_effect=RuntimeError("macro down"),
            ),
        ):
            m_provider.return_value.get_futures_panel.return_value = (panel, dates)
            result, _dates, _fwd = _prepare_futures_data(days=700, max_symbols=0)
        assert "RB0" in result
        assert "export" not in result["RB0"].columns
        assert "宏观注入失败" in capsys.readouterr().out
