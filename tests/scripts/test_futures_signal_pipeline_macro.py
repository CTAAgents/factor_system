"""tests/scripts/test_futures_signal_pipeline_macro.py — 期货信号管道宏观注入接线测试（GAP-088，v2.103.0）。

覆盖: 默认开启注入（helper 被调用且注入列保留）/ 关闭跳过 / helper 异常降级不阻断。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

import scripts.futures_signal_pipeline as fsp  # noqa: E402


def _make_df(days: int = 10, start: str = "2026-01-05") -> pd.DataFrame:
    """构造测试 OHLCV DataFrame（DatetimeIndex，交易日）。"""
    idx = pd.bdate_range(start=start, periods=days)
    return pd.DataFrame({"close": [float(i) for i in range(days)]}, index=idx)


class TestInjectMacroToPanel:
    def test_injects_when_enabled(self) -> None:
        """默认开启：调用面板级 helper，注入列保留。"""
        panel = {"RB0": _make_df()}
        injected = {"RB0": _make_df()}
        injected["RB0"]["export"] = 100.0
        with patch(
            "fts.data_sources.macro_aligner.inject_macro_fields_to_panel",
            return_value=injected,
        ) as m:
            out = fsp._inject_macro_to_panel(panel, enabled=True, trace_id="t1")
        m.assert_called_once()
        assert "export" in out["RB0"].columns

    def test_disabled_returns_panel_without_calling(self) -> None:
        """关闭（--no-macro-injection）：不调用 helper，面板原样返回。"""
        panel = {"RB0": _make_df()}
        with patch("fts.data_sources.macro_aligner.inject_macro_fields_to_panel") as m:
            out = fsp._inject_macro_to_panel(panel, enabled=False)
        m.assert_not_called()
        assert out is panel

    def test_helper_failure_degrades_without_raising(self, capsys: pytest.CaptureFixture) -> None:
        """helper 异常 → 返回原面板，不抛异常（因子走 close 代理）。"""
        panel = {"RB0": _make_df()}
        with patch(
            "fts.data_sources.macro_aligner.inject_macro_fields_to_panel",
            side_effect=RuntimeError("macro down"),
        ):
            out = fsp._inject_macro_to_panel(panel, enabled=True)
        assert out is panel
        assert "宏观注入失败" in capsys.readouterr().out
