"""tests/data_sources/test_tq_source.py — TQ-Local 适配器测试。

HARNESS §5.4: mock HTTP 响应，覆盖连接失败/解析失败/字段缺失/空数据。
所有外部依赖（7721 端口 HTTP）必须通过 mock 注入。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from requests.exceptions import ConnectionError, Timeout


# ─── Fixture: mock 成功响应 ─────────────────────────────


@pytest.fixture
def tq_kline_response() -> dict:
    """模拟 TQ-Local tq_get_kline 成功响应（带完整字段）。"""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "symbol": "RB0.SHFE",
            "rows": [
                {
                    "date": "2026-08-01",
                    "open": 3500,
                    "high": 3550,
                    "low": 3490,
                    "close": 3540,
                    "volume": 100000,
                    "amount": 350000000,
                    "hold": 80000,
                    "settle": 3540,
                    "pre_settle": 3520,
                    "oi_change": 2000,
                },
                {
                    "date": "2026-08-04",
                    "open": 3540,
                    "high": 3600,
                    "low": 3530,
                    "close": 3580,
                    "volume": 120000,
                    "amount": 420000000,
                    "hold": 82000,
                    "settle": 3580,
                    "pre_settle": 3540,
                    "oi_change": 2000,
                },
            ],
        },
    }


@pytest.fixture
def tq_quote_response() -> dict:
    """模拟 TQ-Local tq_get_quote 成功响应。"""
    return {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "symbol": "RB0.SHFE",
            "last": 3580,
            "bid": 3579,
            "ask": 3581,
            "volume": 120000,
            "amount": 420000000,
            "hold": 82000,
            "settle": 3580,
            "pre_settle": 3540,
            "open_interest_change": 2000,
        },
    }


# ─── Fixture: mock requests ─────────────────────────────


@pytest.fixture
def mock_post():
    """Patch fts.data_sources.tq_source.requests.post。"""
    with patch("fts.data_sources.tq_source.requests.post") as mock:
        default = MagicMock()
        default.status_code = 200
        default.json.return_value = {"jsonrpc": "2.0", "result": "ok"}
        default.raise_for_status = MagicMock()
        mock.return_value = default
        yield mock


@pytest.fixture
def mock_get():
    """Patch fts.data_sources.tq_source.requests.get（is_available 探活用）。"""
    with patch("fts.data_sources.tq_source.requests.get") as mock:
        default = MagicMock()
        default.status_code = 200
        mock.return_value = default
        yield mock


def _ok_response(body: dict | list) -> MagicMock:
    """构造 HTTP 200 mock 响应。"""
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = body
    r.raise_for_status = MagicMock()
    return r


# ─── 探活测试 ──────────────────────────────────────────


def test_is_available_returns_true_when_200(mock_post):
    """健康检查返回 200 → is_available() = True。"""
    from fts.data_sources.tq_source import TQLocalSource

    assert TQLocalSource().is_available() is True


def test_is_available_returns_false_on_connection_error():
    """连接失败 → is_available() = False（不抛异常）。"""
    from fts.data_sources.tq_source import TQLocalSource

    with patch("fts.data_sources.tq_source.requests.post", side_effect=ConnectionError("7721 refused")):
        assert TQLocalSource().is_available() is False


def test_is_available_returns_false_on_timeout():
    """超时 → is_available() = False。"""
    from fts.data_sources.tq_source import TQLocalSource

    with patch("fts.data_sources.tq_source.requests.post", side_effect=Timeout("read timeout")):
        assert TQLocalSource().is_available() is False


# ─── 期货代码转换 ──────────────────────────────────────


def test_symbol_to_tq_shfe():
    """RB0 → RB0.SHFE（上期所）。"""
    from fts.data_sources.tq_source import TQLocalSource

    assert TQLocalSource._symbol_to_tq("RB0") == "RB0.SHFE"
    assert TQLocalSource._symbol_to_tq("CU0") == "CU0.SHFE"
    assert TQLocalSource._symbol_to_tq("AU2609") == "AU2609.SHFE"


def test_symbol_to_tq_dce():
    """M0 → M0.DCE（大商所）。"""
    from fts.data_sources.tq_source import TQLocalSource

    assert TQLocalSource._symbol_to_tq("M0") == "M0.DCE"
    assert TQLocalSource._symbol_to_tq("I2509") == "I2509.DCE"
    assert TQLocalSource._symbol_to_tq("A0") == "A0.DCE"


def test_symbol_to_tq_czce():
    """TA0 → TA0.CZCE（郑商所 — 3 字母前缀）。"""
    from fts.data_sources.tq_source import TQLocalSource

    assert TQLocalSource._symbol_to_tq("TA0") == "TA0.CZCE"
    assert TQLocalSource._symbol_to_tq("SR0") == "SR0.CZCE"
    assert TQLocalSource._symbol_to_tq("MA2509") == "MA2509.CZCE"


def test_symbol_to_tq_cffex():
    """IF0 → IF0.CFFEX（中金所）。"""
    from fts.data_sources.tq_source import TQLocalSource

    assert TQLocalSource._symbol_to_tq("IF0") == "IF0.CFFEX"
    assert TQLocalSource._symbol_to_tq("T2509") == "T2509.CFFEX"


def test_symbol_to_tq_unknown_exchange_raises():
    """未知品种前缀应抛 ValueError。"""
    from fts.data_sources.tq_source import TQLocalSource

    with pytest.raises(ValueError, match="未知交易所"):
        TQLocalSource._symbol_to_tq("XX0")


# ─── fetch_ohlcv 成功路径 ──────────────────────────────


def test_fetch_ohlcv_returns_dataframe_with_new_fields(mock_post, tq_kline_response):
    """成功响应应解析为 DataFrame，含 13 字段 + 元数据 4 字段。"""
    from fts.data_sources.tq_source import TQLocalSource

    mock_post.return_value = _ok_response(tq_kline_response)

    df = TQLocalSource().fetch_ohlcv("RB0", days=30, trace_id="trace-001")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    # 核心 OHLCV 字段
    for col in ("symbol", "date", "open", "high", "low", "close", "volume"):
        assert col in df.columns
    # 期货专属字段（用户验证重点）
    for col in ("hold", "settle", "pre_settle", "oi_change"):
        assert col in df.columns, f"新字段 {col} 缺失"
    # vwap 由适配器计算
    assert "vwap" in df.columns
    expected_vwap = 350000000 / 100000
    assert abs(df["vwap"].iloc[0] - expected_vwap) < 0.01
    # 元数据
    assert df["source"].iloc[0] == "TQ_LOCAL"
    assert df["trace_id"].iloc[0] == "trace-001"


def test_fetch_ohlcv_computes_vwap_from_amount_and_volume(mock_post, tq_kline_response):
    """vwap 应由 amount / volume 计算。"""
    from fts.data_sources.tq_source import TQLocalSource

    mock_post.return_value = _ok_response(tq_kline_response)

    df = TQLocalSource().fetch_ohlcv("RB0", days=30)

    expected_vwap = 350000000 / 100000
    assert abs(df["vwap"].iloc[0] - expected_vwap) < 0.01


def test_fetch_ohlcv_vwap_falls_back_to_typical_price(mock_post, tq_kline_response):
    """amount 缺失时 vwap 回退到 (h+l+c)/3。"""
    from fts.data_sources.tq_source import TQLocalSource

    rows = [{k: v for k, v in r.items() if k != "amount"} for r in tq_kline_response["result"]["rows"]]
    tq_kline_response["result"]["rows"] = rows
    mock_post.return_value = _ok_response(tq_kline_response)

    df = TQLocalSource().fetch_ohlcv("RB0", days=30)

    first = df.iloc[0]
    expected = (first["high"] + first["low"] + first["close"]) / 3
    assert abs(df["vwap"].iloc[0] - expected) < 0.01


def test_fetch_ohlcv_fills_metadata_correctly(mock_post, tq_kline_response):
    """所有元数据字段（symbol/period/source/trace_id/fetched_at）应被填充。"""
    from fts.data_sources.tq_source import TQLocalSource

    mock_post.return_value = _ok_response(tq_kline_response)

    df = TQLocalSource().fetch_ohlcv("RB0", days=30, trace_id="t-007")

    assert (df["symbol"] == "RB0.SHFE").all()
    assert (df["period"] == "daily").all()
    assert (df["source"] == "TQ_LOCAL").all()
    assert (df["trace_id"] == "t-007").all()
    assert df["fetched_at"].notna().all()


# ─── fetch_ohlcv 失败路径 ──────────────────────────────


def test_fetch_ohlcv_raises_source_unavailable_on_connection_error():
    """连接失败 → fetch_ohlcv 抛 SourceUnavailable（向上传播供熔断）。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.tq_source import TQLocalSource

    with patch("fts.data_sources.tq_source.requests.post", side_effect=ConnectionError("refused")):
        with pytest.raises(SourceUnavailable):
            TQLocalSource().fetch_ohlcv("RB0", days=30)


def test_fetch_ohlcv_raises_source_unavailable_on_timeout():
    """超时 → fetch_ohlcv 抛 SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.tq_source import TQLocalSource

    with patch("fts.data_sources.tq_source.requests.post", side_effect=Timeout("read timeout")):
        with pytest.raises(SourceUnavailable):
            TQLocalSource().fetch_ohlcv("RB0", days=30)


def test_fetch_ohlcv_or_none_returns_none_on_malformed_json(mock_post):
    """JSON 解析失败 → fetch_ohlcv_or_none 返回 None（优雅降级）。"""
    from fts.data_sources.tq_source import TQLocalSource

    mock_post.return_value.json.side_effect = ValueError("malformed")

    result = TQLocalSource().fetch_ohlcv_or_none("RB0", days=30)
    assert result is None


def test_fetch_ohlcv_or_none_returns_none_on_missing_required_fields(mock_post):
    """响应缺必填字段 → fetch_ohlcv_or_none 返回 None。"""
    from fts.data_sources.tq_source import TQLocalSource

    bad = {
        "jsonrpc": "2.0",
        "result": {
            "symbol": "RB0.SHFE",
            "rows": [{"date": "2026-08-01"}],  # 缺 OHLCV
        },
    }
    mock_post.return_value = _ok_response(bad)

    result = TQLocalSource().fetch_ohlcv_or_none("RB0", days=30)
    assert result is None


def test_fetch_ohlcv_returns_empty_df_when_no_rows(mock_post, tq_kline_response):
    """响应含空 rows → 返回空 DataFrame（带正确 schema）。"""
    from fts.data_sources.tq_source import TQLocalSource

    tq_kline_response["result"]["rows"] = []
    mock_post.return_value = _ok_response(tq_kline_response)

    df = TQLocalSource().fetch_ohlcv("RB0", days=30)
    assert len(df) == 0
    # 即使空，schema 应包含所有字段
    for col in ("hold", "settle", "vwap", "source", "trace_id"):
        assert col in df.columns, f"空 DataFrame 缺 schema 字段: {col}"


def test_fetch_ohlcv_raises_on_http_error(mock_post):
    """HTTP 500 → fetch_ohlcv 抛 SourceUnavailable（不静默吞）。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.tq_source import TQLocalSource

    r = MagicMock()
    r.status_code = 500
    r.raise_for_status.side_effect = Exception("500 Server Error")
    mock_post.return_value = r

    with pytest.raises(SourceUnavailable):
        TQLocalSource().fetch_ohlcv("RB0", days=30)


# ─── fetch_quote ──────────────────────────────────────


def test_fetch_quote_returns_dict(mock_post, tq_quote_response):
    """成功响应 → 返回 dict 含 last/bid/ask/volume/hold/settle。"""
    from fts.data_sources.tq_source import TQLocalSource

    mock_post.return_value = _ok_response(tq_quote_response)

    q = TQLocalSource().fetch_quote("RB0", trace_id="trace-002")

    assert isinstance(q, dict)
    assert q["last"] == 3580
    assert q["hold"] == 82000
    assert q["settle"] == 3580
    assert q["source"] == "TQ_LOCAL"
    assert q["trace_id"] == "trace-002"


def test_fetch_quote_raises_source_unavailable_on_connection_error():
    """fetch_quote 连接失败 → SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.tq_source import TQLocalSource

    with patch("fts.data_sources.tq_source.requests.post", side_effect=ConnectionError("refused")):
        with pytest.raises(SourceUnavailable):
            TQLocalSource().fetch_quote("RB0")


def test_fetch_quote_returns_none_when_no_result(mock_post):
    """响应无 result → 返回 None。"""
    from fts.data_sources.tq_source import TQLocalSource

    mock_post.return_value = _ok_response({"jsonrpc": "2.0", "result": None})

    q = TQLocalSource().fetch_quote("RB0")
    assert q is None

# ─── 分钟线响应 fixture ──────────────────────────────


@pytest.fixture
def tq_minute_response() -> dict:
    """模拟 TQ-Local tq_get_kline 分钟线响应（倒序返回）。"""
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "symbol": "RB0.SHFE",
            "rows": [
                {
                    "datetime": "2026-08-07 21:45:00",
                    "open": 3005,
                    "high": 3008,
                    "low": 3004,
                    "close": 3006,
                    "volume": 7413,
                },
                {
                    "datetime": "2026-08-07 21:40:00",
                    "open": 3003,
                    "high": 3007,
                    "low": 3003,
                    "close": 3006,
                    "volume": 7952,
                },
            ],
        },
    }


# ─── _infer_exchange 直接测试 ──────────────────────────


def test_infer_exchange_t_bond_fallback():
    """T 开头品种（国债）兜底为 CFFEX。"""
    from fts.data_sources.tq_source import _infer_exchange

    assert _infer_exchange("T2509") == "CFFEX"


def test_is_available_false_on_generic_exception():
    """通用异常 → is_available() = False。"""
    from fts.data_sources.tq_source import TQLocalSource

    with patch("fts.data_sources.tq_source.requests.post", side_effect=ValueError("unexpected")):
        assert TQLocalSource().is_available() is False


# ─── fetch_ohlcv 补充失败路径 ──────────────────────────


def test_fetch_ohlcv_raises_on_request_exception():
    """非连接类 RequestException → SourceUnavailable（HTTP error 分支）。"""
    from requests.exceptions import HTTPError

    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.tq_source import TQLocalSource

    with patch("fts.data_sources.tq_source.requests.post", side_effect=HTTPError("500")):
        with pytest.raises(SourceUnavailable):
            TQLocalSource().fetch_ohlcv("RB0", days=30)


def test_fetch_ohlcv_returns_empty_df_when_data_not_dict(mock_post):
    """resp.json() 返回非 dict → 按空数据处理（空 DataFrame）。"""
    from fts.data_sources.tq_source import TQLocalSource

    mock_post.return_value = _ok_response(["not", "dict"])
    df = TQLocalSource().fetch_ohlcv("RB0", days=30)
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_fetch_ohlcv_fills_missing_futures_fields_with_na(mock_post, tq_kline_response):
    """响应缺 hold/settle/pre_settle/oi_change → 补 pd.NA 保持 schema。"""
    from fts.data_sources.tq_source import TQLocalSource

    rows = [
        {k: v for k, v in r.items() if k not in ("hold", "settle", "pre_settle", "oi_change")}
        for r in tq_kline_response["result"]["rows"]
    ]
    tq_kline_response["result"]["rows"] = rows
    mock_post.return_value = _ok_response(tq_kline_response)

    df = TQLocalSource().fetch_ohlcv("RB0", days=30)
    assert df["hold"].isna().all()
    assert df["settle"].isna().all()
    assert df["pre_settle"].isna().all()
    assert df["oi_change"].isna().all()


def test_fetch_ohlcv_vwap_typical_when_volume_zero(mock_post, tq_kline_response):
    """volume 全 0 → vwap 回退到典型价。"""
    from fts.data_sources.tq_source import TQLocalSource

    rows = [{**r, "volume": 0} for r in tq_kline_response["result"]["rows"]]
    tq_kline_response["result"]["rows"] = rows
    mock_post.return_value = _ok_response(tq_kline_response)

    df = TQLocalSource().fetch_ohlcv("RB0", days=30)
    first = df.iloc[0]
    expected = (first["high"] + first["low"] + first["close"]) / 3
    assert abs(df["vwap"].iloc[0] - expected) < 0.01


def test_fetch_ohlcv_daily_missing_required_raises(mock_post):
    """日线缺必填字段 → ValueError（fetch_ohlcv 直接传播）。"""
    from fts.data_sources.tq_source import TQLocalSource

    bad = {"jsonrpc": "2.0", "result": {"rows": [{"date": "2026-08-01"}]}}
    mock_post.return_value = _ok_response(bad)
    with pytest.raises(ValueError):
        TQLocalSource().fetch_ohlcv("RB0", days=30)


# ─── 分钟线处理 ──────────────────────────────────────


def test_fetch_ohlcv_minute_parses_and_sorts(mock_post, tq_minute_response):
    """分钟线应解析为 11 列 schema 并按时间正序。"""
    from fts.data_sources.tq_source import TQLocalSource

    mock_post.return_value = _ok_response(tq_minute_response)
    df = TQLocalSource(period="5m").fetch_ohlcv("RB0", days=30, trace_id="t-min")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    assert "datetime" in df.columns
    assert df["datetime"].is_monotonic_increasing
    assert df["symbol"].iloc[0] == "RB0"
    assert df["period"].iloc[0] == "5m"
    assert df["source"].iloc[0] == "TQ_LOCAL"
    assert df["trace_id"].iloc[0] == "t-min"


def test_fetch_ohlcv_minute_uses_date_column(mock_post):
    """分钟线使用 date 列代替 datetime 时应正常解析。"""
    from fts.data_sources.tq_source import TQLocalSource

    resp = {
        "jsonrpc": "2.0",
        "result": {
            "rows": [
                {
                    "date": "2026-08-07 21:45:00",
                    "open": 3005,
                    "high": 3008,
                    "low": 3004,
                    "close": 3006,
                    "volume": 7413,
                }
            ]
        },
    }
    mock_post.return_value = _ok_response(resp)
    df = TQLocalSource(period="5m").fetch_ohlcv("RB0", days=30)
    assert df is not None
    assert df["datetime"].iloc[0] == pd.Timestamp("2026-08-07 21:45:00")


def test_fetch_ohlcv_minute_missing_required_returns_none(mock_post):
    """分钟线缺必填字段 → fetch_ohlcv_or_none 返回 None。"""
    from fts.data_sources.tq_source import TQLocalSource

    bad = {"jsonrpc": "2.0", "result": {"rows": [{"datetime": "2026-08-07 21:45:00"}]}}
    mock_post.return_value = _ok_response(bad)
    assert TQLocalSource(period="5m").fetch_ohlcv_or_none("RB0", days=30) is None


def test_fetch_ohlcv_minute_empty_rows_returns_empty_schema(mock_post):
    """分钟线空 rows → 返回 11 列空 DataFrame。"""
    from fts.data_sources.tq_source import TQLocalSource

    resp = {"jsonrpc": "2.0", "result": {"rows": []}}
    mock_post.return_value = _ok_response(resp)
    df = TQLocalSource(period="5m").fetch_ohlcv("RB0", days=30)
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert "datetime" in df.columns
    assert "trace_id" in df.columns


# ─── fetch_quote 补充 ─────────────────────────────────


def test_fetch_quote_raises_on_timeout():
    """fetch_quote 超时 → SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.tq_source import TQLocalSource

    with patch("fts.data_sources.tq_source.requests.post", side_effect=Timeout("read timeout")):
        with pytest.raises(SourceUnavailable):
            TQLocalSource().fetch_quote("RB0")


def test_fetch_quote_returns_none_when_data_not_dict(mock_post):
    """fetch_quote 响应非 dict → 返回 None。"""
    from fts.data_sources.tq_source import TQLocalSource

    mock_post.return_value = _ok_response(["not", "dict"])
    assert TQLocalSource().fetch_quote("RB0") is None
