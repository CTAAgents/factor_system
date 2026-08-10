"""tests/data_sources/test_akshare_minute_source.py — AKShare 分钟 K 线适配器测试。

覆盖:
    1. 年化因子 / z-score 窗口工具函数
    2. AKShareMinuteSource 周期校验 / 探活 / 数据获取全路径 / 快照
    3. SourceUnavailable 传播（ImportError / API 异常 / 空数据）
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.data_sources.akshare_minute_source import (  # noqa: E402
    FREQUENCY_ANNUALIZATION,
    SUPPORTED_PERIODS,
    AKShareMinuteSource,
    get_annualization_factor,
    get_default_zscore_window,
)
from fts.data_sources.base import SourceUnavailable  # noqa: E402


# ─── 工具函数 ──────────────────────────────────────────────


class TestAnnualization:
    def test_supported_frequencies(self):
        assert get_annualization_factor("daily") == 252.0
        assert get_annualization_factor("1m") == 252.0 * 390.0
        assert get_annualization_factor("5m") == 252.0 * 78.0
        assert get_annualization_factor("60m") == 252.0 * 6.5

    def test_unknown_frequency_defaults_252(self):
        assert get_annualization_factor("hourly") == 252.0

    def test_annualization_map_complete(self):
        assert FREQUENCY_ANNUALIZATION["daily"] == 252.0
        assert len(FREQUENCY_ANNUALIZATION) == 6

    def test_default_zscore_window(self):
        assert get_default_zscore_window("daily") == 20
        assert get_default_zscore_window("1m") == 20 * 390
        assert get_default_zscore_window("unknown") == 20


# ─── 模块注入工具 ──────────────────────────────────────────


def _install_fake_akshare(monkeypatch, df: pd.DataFrame | None, raise_error: Exception | None = None):
    """注入 fake akshare 模块到 sys.modules。"""
    fake = types.ModuleType("akshare")

    def _futures_zh_minute_sina(symbol, period):
        if raise_error is not None:
            raise raise_error
        return df.copy() if df is not None else None

    fake.futures_zh_minute_sina = _futures_zh_minute_sina
    monkeypatch.setitem(sys.modules, "akshare", fake)
    return fake


def _make_minute_df(n: int = 30) -> pd.DataFrame:
    """构造 AKShare 分钟数据。"""
    rng = np.random.default_rng(3)
    times = pd.date_range("2026-08-01 09:00", periods=n, freq="min")
    return pd.DataFrame(
        {
            "datetime": times,
            "open": rng.uniform(3000, 3100, n),
            "high": rng.uniform(3100, 3200, n),
            "low": rng.uniform(2900, 3000, n),
            "close": rng.uniform(2950, 3150, n),
            "volume": rng.integers(100, 1000, n).astype(float),
            "hold": rng.integers(1000, 5000, n).astype(float),
        }
    )


def _block_akshare(monkeypatch):
    """sys.modules 置 None → import 抛 ImportError。"""
    monkeypatch.setitem(sys.modules, "akshare", None)


# ─── AKShareMinuteSource ───────────────────────────────────


class TestInitAndProbe:
    def test_valid_periods(self):
        for p in SUPPORTED_PERIODS:
            src = AKShareMinuteSource(period=p)
            assert src.period == p

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError, match="不支持的分钟周期"):
            AKShareMinuteSource(period="7m")

    def test_is_available_true_with_module(self, monkeypatch):
        _install_fake_akshare(monkeypatch, _make_minute_df())
        assert AKShareMinuteSource().is_available() is True

    def test_is_available_false_without_module(self, monkeypatch):
        _block_akshare(monkeypatch)
        assert AKShareMinuteSource().is_available() is False


class TestFetchOhlcv:
    def test_import_error_raises_source_unavailable(self, monkeypatch):
        _block_akshare(monkeypatch)
        with pytest.raises(SourceUnavailable, match="akshare 模块不可用"):
            AKShareMinuteSource().fetch_ohlcv("RB0")

    def test_api_error_raises_source_unavailable(self, monkeypatch):
        _install_fake_akshare(monkeypatch, None, raise_error=RuntimeError("api down"))
        with pytest.raises(SourceUnavailable, match="获取失败"):
            AKShareMinuteSource().fetch_ohlcv("RB0")

    def test_empty_df_returns_none(self, monkeypatch):
        _install_fake_akshare(monkeypatch, pd.DataFrame())
        assert AKShareMinuteSource().fetch_ohlcv("RB0") is None

    def test_normal_df_standardized(self, monkeypatch):
        _install_fake_akshare(monkeypatch, _make_minute_df(n=30))
        df = AKShareMinuteSource("1m").fetch_ohlcv("RB0", days=500)
        assert df is not None
        assert df["symbol"].iloc[0] == "RB0"
        assert df["period"].iloc[0] == "1m"
        assert df["source"].iloc[0] == "AKSHARE_MINUTE"
        assert df["trace_id"].iloc[0] == ""
        # 数值类型
        assert df["close"].dtype == np.float64
        # 时间排序
        assert df["datetime"].is_monotonic_increasing
        # 元数据列齐全
        for col in (
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "hold",
            "symbol",
            "period",
            "source",
            "fetched_at",
            "trace_id",
        ):
            assert col in df.columns

    def test_tail_truncation(self, monkeypatch):
        _install_fake_akshare(monkeypatch, _make_minute_df(n=100))
        df = AKShareMinuteSource().fetch_ohlcv("RB0", days=20)
        assert df is not None
        assert len(df) == 20

    def test_trace_id_propagated(self, monkeypatch):
        _install_fake_akshare(monkeypatch, _make_minute_df(n=10))
        df = AKShareMinuteSource().fetch_ohlcv("RB0", trace_id="tid-1")
        assert df["trace_id"].iloc[0] == "tid-1"

    def test_fetch_ohlcv_or_none_preserves_source_unavailable(self, monkeypatch):
        # SourceUnavailable 向上传播（熔断判定需要）
        _block_akshare(monkeypatch)
        with pytest.raises(SourceUnavailable):
            AKShareMinuteSource().fetch_ohlcv_or_none("RB0", days=500)


class TestFetchQuote:
    def test_quote_normal(self, monkeypatch):
        _install_fake_akshare(monkeypatch, _make_minute_df(n=10))
        quote = AKShareMinuteSource().fetch_quote("RB0", trace_id="tq")
        assert quote is not None
        assert quote["symbol"] == "RB0"
        assert quote["source"] == "AKSHARE_MINUTE"
        assert quote["trace_id"] == "tq"
        assert "last_price" in quote and "datetime" in quote

    def test_quote_empty_df_returns_none(self, monkeypatch):
        _install_fake_akshare(monkeypatch, pd.DataFrame())
        assert AKShareMinuteSource().fetch_quote("RB0") is None

    def test_quote_exception_returns_none(self, monkeypatch):
        _install_fake_akshare(monkeypatch, None, raise_error=RuntimeError("boom"))
        assert AKShareMinuteSource().fetch_quote("RB0") is None
