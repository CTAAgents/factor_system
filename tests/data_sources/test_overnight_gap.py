"""tests/data_sources/test_overnight_gap.py — 夜盘/隔夜跳空标记测试（GAP-066）。

HARNESS §测试随重构: 成功路径 + 边界 + 降级路径。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.data_sources.overnight_gap import compute_overnight_gap, inject_overnight_gap


def _ohlcv_with_jump(gap: float = 0.02) -> pd.DataFrame:
    """构造第 2 日显著高开的 OHLCV（open 相对前收跳空 gap）。"""
    n = 10
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    close = np.full(n, 100.0)
    open_vals = close.copy()
    open_vals[1] = close[0] * (1 + gap)  # 第 2 日高开 gap
    return pd.DataFrame({"open": open_vals, "high": open_vals + 1, "low": open_vals - 1, "close": close}, index=idx)


def test_compute_overnight_gap_basic():
    """跳空比例 = open[t]/close[t-1] - 1，首日 NaN。"""
    df = _ohlcv_with_jump(gap=0.02)
    gap = compute_overnight_gap(df)
    assert np.isnan(gap.iloc[0])
    assert gap.iloc[1] == pytest.approx(0.02)
    assert gap.iloc[2] == pytest.approx(0.0)


def test_inject_columns_present():
    """注入 overnight_gap / overnight_gap_flag 两列。"""
    df = inject_overnight_gap(_ohlcv_with_jump(gap=0.02))
    assert "overnight_gap" in df.columns
    assert "overnight_gap_flag" in df.columns
    assert len(df) == 10


def test_flag_threshold_marks_jump():
    """|gap| > 阈值（默认 1%）触发 flag。"""
    df = inject_overnight_gap(_ohlcv_with_jump(gap=0.02))
    assert df["overnight_gap_flag"].iloc[1] == 1  # 2% 跳空被标记
    assert df["overnight_gap_flag"].iloc[2] == 0  # 无跳空不标记


def test_small_gap_not_flagged():
    """小跳空（< 阈值）不触发 flag。"""
    df = inject_overnight_gap(_ohlcv_with_jump(gap=0.002), flag_threshold=0.01)
    assert df["overnight_gap_flag"].iloc[1] == 0


def test_negative_gap_flagged():
    """低开（负跳空）同样触发 flag。"""
    df = inject_overnight_gap(_ohlcv_with_jump(gap=-0.02))
    assert df["overnight_gap_flag"].iloc[1] == 1
    assert df["overnight_gap"].iloc[1] == pytest.approx(-0.02)


def test_missing_columns_returns_unchanged():
    """缺 open/close 列时原样返回（不崩溃）。"""
    df = pd.DataFrame({"close": [1.0, 2.0]})
    out = inject_overnight_gap(df)
    assert "overnight_gap" not in out.columns


def test_empty_df_no_crash():
    """空 DataFrame 不崩溃。"""
    out = inject_overnight_gap(pd.DataFrame())
    assert out is not None


def test_first_row_no_flag():
    """首日无前收，gap 为 NaN，flag 为 0。"""
    df = inject_overnight_gap(_ohlcv_with_jump())
    assert df["overnight_gap_flag"].iloc[0] == 0
