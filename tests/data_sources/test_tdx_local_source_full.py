"""tests/data_sources/test_tdx_local_source_full.py — TDX 统一数据源补充测试。

补充 test_tdx_local_source.py 未覆盖的路径:
    - fetch_ohlcv 异常分支（HTTP/OSError/JSON）
    - 三种时间格式（date+time / time-only / date-only / 缺失）
    - 缺字段 / 非 dict 结构 / 多品种回退 / 截断
    - fetch_quote 全路径 / is_available
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.data_sources.base import SourceUnavailable  # noqa: E402
from fts.data_sources.tdx_local_source import TdxLocalSource  # noqa: E402


# ─── fake urlopen ──────────────────────────────────────────


class FakeResp:
    def __init__(self, status: int = 200, data=None, raise_error: Exception | None = None):
        self.status = status
        self._data = data if data is not None else b"{}"
        self.raise_error = raise_error

    def read(self):
        if self.raise_error:
            raise self.raise_error
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _patch_urlopen(monkeypatch, resp: FakeResp):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: resp)


def _make_kline_block(n: int = 30, has_date: bool = True, has_time: bool = True) -> dict:
    rng = np.random.default_rng(4)
    close = 3000 + np.cumsum(rng.normal(0, 5, n))
    block = {
        "Date": ["20260801"] * n if has_date else [None] * n,
        "Open": (close + rng.normal(0, 1, n)).tolist(),
        "High": (close + np.abs(rng.normal(0, 2, n))).tolist(),
        "Low": (close - np.abs(rng.normal(0, 2, n))).tolist(),
        "Close": close.tolist(),
        "Volume": rng.integers(100, 1000, n).astype(float).tolist(),
    }
    if has_time:
        block["Time"] = [f"{90000 + i}" for i in range(n)]
    return block


def _make_raw(block: dict | None = None, symbol: str = "RBL8.SHF") -> bytes:
    value = {symbol: block} if block is not None else {}
    return json.dumps({"result": {"Value": value}}).encode("utf-8")


class TestFetchOhlcvErrors:
    def test_http_error_raises(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(raise_error=urllib.error.URLError("boom")))
        with pytest.raises(SourceUnavailable, match="HTTP 请求失败"):
            TdxLocalSource(period="5m").fetch_ohlcv("RB0")

    def test_oserror_raises(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(raise_error=OSError("conn")))
        with pytest.raises(SourceUnavailable, match="连接失败"):
            TdxLocalSource(period="5m").fetch_ohlcv("RB0")

    def test_json_error_raises(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(data=b"not json"))
        with pytest.raises(SourceUnavailable, match="JSON 解析失败"):
            TdxLocalSource(period="5m").fetch_ohlcv("RB0")

    def test_result_none_returns_none(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(data=b'{"result": null}'))
        assert TdxLocalSource(period="5m").fetch_ohlcv("RB0") is None

    def test_value_not_dict_returns_none(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(data=b'{"result": {"Value": 123}}'))
        assert TdxLocalSource(period="5m").fetch_ohlcv("RB0") is None

    def test_block_not_dict_returns_none(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(data=b'{"result": {"Value": {"RBL8.SHF": 123}}}'))
        assert TdxLocalSource(period="5m").fetch_ohlcv("RB0") is None

    def test_missing_required_fields_returns_none(self, monkeypatch):
        block = {"Date": ["20260801"], "Close": [3000.0]}
        _patch_urlopen(monkeypatch, FakeResp(data=_make_raw(block)))
        assert TdxLocalSource(period="5m").fetch_ohlcv("RB0") is None

    def test_no_date_time_returns_none(self, monkeypatch):
        # 无 Date/Time 列（仅 OHLCV）
        rng = np.random.default_rng(4)
        block = {
            "Open": rng.normal(3000, 5, 10).tolist(),
            "High": rng.normal(3010, 5, 10).tolist(),
            "Low": rng.normal(2990, 5, 10).tolist(),
            "Close": rng.normal(3000, 5, 10).tolist(),
            "Volume": rng.integers(100, 1000, 10).astype(float).tolist(),
        }
        _patch_urlopen(monkeypatch, FakeResp(data=_make_raw(block)))
        assert TdxLocalSource(period="5m").fetch_ohlcv("RB0") is None


class TestFetchOhlcvFormats:
    def test_normal_date_time(self, monkeypatch):
        block = _make_kline_block(n=30)
        _patch_urlopen(monkeypatch, FakeResp(data=_make_raw(block)))
        df = TdxLocalSource("1m").fetch_ohlcv("RB0", days=500, trace_id="tid")
        assert df is not None
        assert len(df) == 30
        assert df["symbol"].iloc[0] == "RB0"
        assert df["period"].iloc[0] == "1m"
        assert df["source"].iloc[0] == "TDX_LOCAL"
        assert df["trace_id"].iloc[0] == "tid"
        assert pd.api.types.is_datetime64_any_dtype(df["datetime"])
        assert df["datetime"].is_monotonic_increasing
        assert df["close"].dtype == np.float64

    def test_time_only(self, monkeypatch):
        # 仅 Time 列（日内分钟数据），无 Date 列
        block = _make_kline_block(n=20, has_date=True, has_time=True)
        block.pop("Date", None)
        _patch_urlopen(monkeypatch, FakeResp(data=_make_raw(block)))
        df = TdxLocalSource(period="5m").fetch_ohlcv("RB0")
        assert df is not None and len(df) == 20

    def test_date_only(self, monkeypatch):
        # 仅 Date 列，无 Time 列
        block = _make_kline_block(n=20, has_date=True, has_time=True)
        block.pop("Time", None)
        _patch_urlopen(monkeypatch, FakeResp(data=_make_raw(block)))
        df = TdxLocalSource(period="5m").fetch_ohlcv("RB0")
        assert df is not None
        assert pd.api.types.is_datetime64_any_dtype(df["datetime"])

    def test_tail_truncation(self, monkeypatch):
        block = _make_kline_block(n=100)
        _patch_urlopen(monkeypatch, FakeResp(data=_make_raw(block)))
        assert len(TdxLocalSource(period="5m").fetch_ohlcv("RB0", days=20)) == 20

    def test_fallback_first_block_for_unknown_symbol(self, monkeypatch):
        block = _make_kline_block(n=10)
        raw = json.dumps({"result": {"Value": {"RBX1.SHF": block}}}).encode("utf-8")
        _patch_urlopen(monkeypatch, FakeResp(data=raw))
        df = TdxLocalSource(period="5m").fetch_ohlcv("RB0")
        assert df is not None and len(df) == 10


class TestFetchQuoteAndProbe:
    def test_quote_normal(self, monkeypatch):
        raw = json.dumps({"result": {"Now": 3005.5, "Open": 3000, "Max": 3010, "Min": 2990, "Volume": 999}}).encode()
        _patch_urlopen(monkeypatch, FakeResp(data=raw))
        quote = TdxLocalSource().fetch_quote("RB0", trace_id="tq")
        assert quote is not None
        assert quote["symbol"] == "RB0"
        assert quote["last_price"] == 3005.5
        assert quote["volume"] == 999
        assert quote["trace_id"] == "tq"

    def test_quote_exception_returns_none(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(raise_error=OSError("down")))
        assert TdxLocalSource().fetch_quote("RB0") is None

    def test_quote_empty_result_returns_none(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(data=b'{"result": null}'))
        assert TdxLocalSource().fetch_quote("RB0") is None

    def test_quote_result_not_dict_defaults_zero(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(data=b'{"result": 42}'))
        quote = TdxLocalSource().fetch_quote("RB0")
        assert quote is not None
        # result 非 dict → 仅基础字段，无 last_price
        assert "last_price" not in quote

    def test_is_available_true(self, monkeypatch):
        _patch_urlopen(monkeypatch, FakeResp(status=200))
        assert TdxLocalSource().is_available() is True

    def test_is_available_false(self, monkeypatch):
        def _raise(req, timeout):
            raise urllib.error.URLError("down")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        assert TdxLocalSource().is_available() is False
