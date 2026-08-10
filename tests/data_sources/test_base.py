"""tests/data_sources/test_base.py — BaseFuturesSource 抽象基类 + 异常 + 校验器测试。

HARNESS §5.4 测试随重构: 适配器/校验器变更必须同步更新本测试。
"""

from __future__ import annotations

import pandas as pd
import pytest


# ─── 公共辅助：构造一个最小可用具体子类用于测试抽象行为 ──────────


class _StubSource:
    """用于测试抽象基类的最小具体子类，行为可由 fixture 控制。"""

    def __init__(self, *, ohlcv_result=None, quote_result=None, available=True, raise_on_fetch=None):
        self.source_name = "STUB"
        self._ohlcv_result = ohlcv_result
        self._quote_result = quote_result
        self._available = available
        self._raise_on_fetch = raise_on_fetch

    def is_available(self) -> bool:
        return self._available

    def fetch_ohlcv(self, symbol, days, trace_id=""):
        if self._raise_on_fetch is not None:
            raise self._raise_on_fetch
        return self._ohlcv_result

    def fetch_quote(self, symbol, trace_id=""):
        return self._quote_result


# ─── SourceUnavailable 异常 ─────────────────────────────────


def test_source_unavailable_is_exception():
    """SourceUnavailable 继承自 Exception，可被 except Exception 捕获。"""
    from fts.data_sources.base import SourceUnavailable

    err = SourceUnavailable("TQ_LOCAL", "7721 端口不可达")
    assert isinstance(err, Exception)
    assert err.source == "TQ_LOCAL"
    assert err.reason == "7721 端口不可达"


def test_source_unavailable_message_format():
    """异常消息含源名 + 原因，便于日志排查。"""
    from fts.data_sources.base import SourceUnavailable

    err = SourceUnavailable("AKSHARE", "rate limit")
    msg = str(err)
    assert "AKSHARE" in msg
    assert "rate limit" in msg


# ─── 抽象基类语义 ─────────────────────────────────────────


def test_base_futures_source_is_abstract():
    """BaseFuturesSource 不可直接实例化（含抽象方法）。"""
    from fts.data_sources.base import BaseFuturesSource

    with pytest.raises(TypeError):
        BaseFuturesSource()  # type: ignore[abstract]


def test_base_futures_source_requires_three_methods():
    """子类必须实现 fetch_ohlcv / fetch_quote / is_available 三个抽象方法。"""
    from fts.data_sources.base import BaseFuturesSource

    class Incomplete(BaseFuturesSource):
        def fetch_ohlcv(self, symbol, days, trace_id=""):
            return None

        # 缺少 fetch_quote 和 is_available

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_concrete_subclass_works():
    """完整实现三个抽象方法的子类可正常实例化与调用。"""
    from fts.data_sources.base import BaseFuturesSource

    class ConcreteSource(BaseFuturesSource):
        source_name = "TEST"

        def is_available(self):
            return True

        def fetch_ohlcv(self, symbol, days, trace_id=""):
            return pd.DataFrame({"close": [1.0]})

        def fetch_quote(self, symbol, trace_id=""):
            return {"last": 1.0}

    src = ConcreteSource()
    assert src.source_name == "TEST"
    assert src.is_available() is True
    assert src.fetch_ohlcv("RB0", 30) is not None
    assert src.fetch_quote("RB0") == {"last": 1.0}


# ─── fetch_ohlcv_or_none 包装方法 ──────────────────────────


def test_fetch_ohlcv_or_none_returns_none_on_generic_exception():
    """非 SourceUnavailable 异常被吞掉，返回 None（聚合器友好）。"""
    from fts.data_sources.base import BaseFuturesSource

    class _BoomSource(BaseFuturesSource):
        source_name = "BOOM"

        def is_available(self):
            return True

        def fetch_quote(self, symbol, trace_id=""):
            return None

        def fetch_ohlcv(self, symbol, days, trace_id=""):
            raise RuntimeError("network down")

    src = _BoomSource()
    assert src.fetch_ohlcv_or_none("RB0", 30) is None


def test_fetch_ohlcv_or_none_propagates_source_unavailable():
    """SourceUnavailable 异常必须向上传播，供聚合器判定熔断。"""
    from fts.data_sources.base import BaseFuturesSource, SourceUnavailable

    class _UnavailableSource(BaseFuturesSource):
        source_name = "DOWN"

        def is_available(self):
            return False

        def fetch_quote(self, symbol, trace_id=""):
            return None

        def fetch_ohlcv(self, symbol, days, trace_id=""):
            raise SourceUnavailable("DOWN", "auth expired")

    src = _UnavailableSource()
    with pytest.raises(SourceUnavailable):
        src.fetch_ohlcv_or_none("RB0", 30)


def test_fetch_ohlcv_or_none_returns_dataframe_on_success():
    """正常路径透传 fetch_ohlcv 的返回。"""
    from fts.data_sources.base import BaseFuturesSource

    class _OkSource(BaseFuturesSource):
        source_name = "OK"

        def is_available(self):
            return True

        def fetch_quote(self, symbol, trace_id=""):
            return None

        def fetch_ohlcv(self, symbol, days, trace_id=""):
            return pd.DataFrame({"close": [100.0, 101.0]})

    src = _OkSource()
    df = src.fetch_ohlcv_or_none("RB0", 30)
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2


# ─── validate_ohlcv_row 静态方法 ─────────────────────────


def _valid_row() -> dict:
    return {
        "symbol": "RB0",
        "date": "2026-08-04",
        "open": 3500.0,
        "high": 3550.0,
        "low": 3490.0,
        "close": 3540.0,
        "volume": 100000.0,
    }


def test_validate_ohlcv_row_valid():
    """合法 8 必填字段通过校验。"""
    from fts.data_sources.base import BaseFuturesSource

    ok, err = BaseFuturesSource.validate_ohlcv_row(_valid_row())
    assert ok is True
    assert err == ""


def test_validate_ohlcv_row_negative_price_rejected():
    """open/high/low/close 任一 <= 0 即校验失败。"""
    from fts.data_sources.base import BaseFuturesSource

    for bad_field in ("open", "high", "low", "close"):
        row = _valid_row()
        row[bad_field] = -1.0
        ok, err = BaseFuturesSource.validate_ohlcv_row(row)
        assert ok is False, f"{bad_field}=-1 应被拒绝"
        assert bad_field in err


def test_validate_ohlcv_row_negative_volume_rejected():
    """volume < 0 校验失败。"""
    from fts.data_sources.base import BaseFuturesSource

    row = _valid_row()
    row["volume"] = -100.0
    ok, err = BaseFuturesSource.validate_ohlcv_row(row)
    assert ok is False
    assert "volume" in err


def test_validate_ohlcv_row_invalid_date_rejected():
    """日期非 YYYY-MM-DD 格式即校验失败。"""
    from fts.data_sources.base import BaseFuturesSource

    row = _valid_row()
    row["date"] = "2026/08/04"  # 用了 /
    ok, err = BaseFuturesSource.validate_ohlcv_row(row)
    assert ok is False
    assert "date" in err


def test_validate_ohlcv_row_missing_required_rejected():
    """缺必填字段（symbol/date/ohlc/volume）即校验失败。"""
    from fts.data_sources.base import BaseFuturesSource

    for missing in ("symbol", "date", "open", "high", "low", "close", "volume"):
        row = _valid_row()
        del row[missing]
        ok, err = BaseFuturesSource.validate_ohlcv_row(row)
        assert ok is False, f"缺 {missing} 应被拒绝"
        assert missing in err


def test_validate_ohlcv_row_accepts_optional_missing():
    """可选字段（amount/hold/settle/oi_change/vwap/source/fetched_at）允许缺失。"""
    from fts.data_sources.base import BaseFuturesSource

    row = _valid_row()  # 只含必填
    ok, err = BaseFuturesSource.validate_ohlcv_row(row)
    assert ok is True
    assert err == ""
