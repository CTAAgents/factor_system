"""tests/data_sources/test_wind_source.py — Wind 适配器测试。

HARNESS §5.4: mock MCP 调用，覆盖连接失败/解析失败/字段缺失/空数据。
Wind 是字段增强层（settle/oi_change/期权 IV），不参与 K 线主路径。
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest


# ─── Fixture: Wind 原始响应 ─────────────────────────────


@pytest.fixture
def wind_kline_raw() -> dict:
    """模拟 Wind mx_ashare_finance_data 返回的 K 线 JSON 响应。"""
    return {
        "data": [
            {
                "date": "2026-08-01",
                "open": 3500,
                "high": 3550,
                "low": 3490,
                "close": 3540,
                "volume": 100000,
                "amount": 350000000,
                "oi": 80000,
                "settle": 3540,
                "pre_settle": 3520,
                "oi_chg": 2000,
            },
            {
                "date": "2026-08-04",
                "open": 3540,
                "high": 3600,
                "low": 3530,
                "close": 3580,
                "volume": 120000,
                "amount": 420000000,
                "oi": 82000,
                "settle": 3580,
                "pre_settle": 3540,
                "oi_chg": 2000,
            },
        ],
    }


@pytest.fixture
def wind_quote_raw() -> dict:
    """模拟 Wind 实时快照响应。"""
    return {
        "data": {
            "last": 3580,
            "bid": 3579,
            "ask": 3581,
            "open": 3540,
            "high": 3600,
            "low": 3530,
            "volume": 120000,
            "amount": 420000000,
            "oi": 82000,
            "settle": 3580,
            "pre_settle": 3540,
            "oi_chg": 2000,
        },
    }


# ─── 探活测试 ──────────────────────────────────────────


def test_is_available_returns_true_when_mcp_responds():
    """MCP 探活成功 → is_available() = True。"""
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", return_value={"status": "ok"}):
        assert WindSource().is_available() is True


def test_is_available_returns_false_on_connection_error():
    """MCP 连接失败 → is_available() = False。"""
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=ConnectionError("MCP down")):
        assert WindSource().is_available() is False


def test_is_available_returns_false_on_timeout():
    """MCP 超时 → is_available() = False。"""
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=TimeoutError("timeout")):
        assert WindSource().is_available() is False


def test_is_available_returns_false_on_exception():
    """任何异常 → is_available() = False（不抛）。"""
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=Exception("unexpected")):
        assert WindSource().is_available() is False


# ─── 字段映射 — parse_ohlcv 公开方法（核心）─────────────


def test_parse_ohlcv_returns_dataframe_with_17_columns(wind_kline_raw):
    """parse_ohlcv 应返回 17 列 FTS schema DataFrame。"""
    from fts.data_sources.wind_source import WindSource

    df = WindSource().parse_ohlcv(wind_kline_raw, "RB2509.SHFE", trace_id="t-001")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
    # 完整 17 列
    expected = [
        "symbol",
        "period",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "hold",
        "settle",
        "pre_settle",
        "oi_change",
        "vwap",
        "source",
        "fetched_at",
        "trace_id",
    ]
    assert list(df.columns) == expected


def test_parse_ohlcv_maps_wind_fields_to_fts(wind_kline_raw):
    """字段映射: Wind oi → FTS hold, Wind settle → FTS settle, Wind oi_chg → FTS oi_change。"""
    from fts.data_sources.wind_source import WindSource

    df = WindSource().parse_ohlcv(wind_kline_raw, "RB2509.SHFE", trace_id="t-002")

    row = df.iloc[0]
    assert row["hold"] == 80000  # Wind oi → FTS hold
    assert row["settle"] == 3540  # Wind settle → FTS settle
    assert row["pre_settle"] == 3520  # Wind pre_settle → FTS pre_settle
    assert row["oi_change"] == 2000  # Wind oi_chg → FTS oi_change


def test_parse_ohlcv_computes_vwap(wind_kline_raw):
    """vwap 应由 amount / volume 计算。"""
    from fts.data_sources.wind_source import WindSource

    df = WindSource().parse_ohlcv(wind_kline_raw, "RB2509.SHFE")

    # 第一行 amount=350M / volume=100K = 3500
    assert abs(df["vwap"].iloc[0] - 3500.0) < 0.01


def test_parse_ohlcv_fills_metadata(wind_kline_raw):
    """元数据字段应被正确填充。"""
    from fts.data_sources.wind_source import WindSource

    df = WindSource().parse_ohlcv(wind_kline_raw, "RB2509.SHFE", trace_id="t-007")

    assert (df["symbol"] == "RB2509.SHFE").all()
    assert (df["period"] == "daily").all()
    assert (df["source"] == "WIND").all()
    assert (df["trace_id"] == "t-007").all()
    assert df["fetched_at"].notna().all()


def test_parse_ohlcv_handles_oi_field_alias():
    """Wind 字段可能是 open_interest 而非 oi — 应正确映射。"""
    from fts.data_sources.wind_source import WindSource

    raw = {
        "data": [
            {
                "date": "2026-08-04",
                "open": 3540,
                "high": 3600,
                "low": 3530,
                "close": 3580,
                "volume": 120000,
                "amount": 420000000,
                "open_interest": 82000,
                "settle": 3580,
                "oi_chg": 2000,
            },
        ]
    }
    df = WindSource().parse_ohlcv(raw, "RB2509.SHFE")
    assert df["hold"].iloc[0] == 82000


def test_parse_ohlcv_handles_oi_change_aliases():
    """oi_change 字段名有多种写法 (oi_chg/open_interest_change) — 都应映射。"""
    from fts.data_sources.wind_source import WindSource

    raw = {
        "data": [
            {
                "date": "2026-08-04",
                "open": 3540,
                "high": 3600,
                "low": 3530,
                "close": 3580,
                "volume": 120000,
                "amount": 420000000,
                "oi": 82000,
                "settle": 3580,
                "open_interest_change": 2000,
            },
        ]
    }
    df = WindSource().parse_ohlcv(raw, "RB2509.SHFE")
    assert df["oi_change"].iloc[0] == 2000


def test_parse_ohlcv_returns_empty_df_when_no_data():
    """data 为空列表 → 返回带 schema 的空 DataFrame。"""
    from fts.data_sources.wind_source import WindSource

    df = WindSource().parse_ohlcv({"data": []}, "RB2509.SHFE")
    assert len(df) == 0
    assert "hold" in df.columns
    assert "settle" in df.columns


def test_parse_ohlcv_or_none_returns_none_on_malformed_data():
    """缺必填字段 → parse_ohlcv_or_none 返回 None。"""
    from fts.data_sources.wind_source import WindSource

    bad = {"data": [{"date": "2026-08-04"}]}  # 缺 OHLCV
    result = WindSource().parse_ohlcv_or_none(bad, "RB2509.SHFE")
    assert result is None


def test_parse_ohlcv_or_none_returns_none_on_non_dict():
    """非 dict 输入 → parse_ohlcv_or_none 返回 None。"""
    from fts.data_sources.wind_source import WindSource

    assert WindSource().parse_ohlcv_or_none("not a dict", "RB2509.SHFE") is None
    assert WindSource().parse_ohlcv_or_none(None, "RB2509.SHFE") is None


# ─── fetch_ohlcv 集成路径（mock _call_mcp）──────────────


def test_fetch_ohlcv_calls_mcp_and_parses(wind_kline_raw):
    """fetch_ohlcv 应通过 _call_mcp 调用 MCP 并解析响应。"""
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", return_value=wind_kline_raw) as mock_call:
        df = WindSource().fetch_ohlcv("RB2509.SHFE", days=30, trace_id="t-100")

    # 验证调用了 MCP
    assert mock_call.called
    # 验证 query 包含关键信息
    call_args = mock_call.call_args
    query = call_args.kwargs.get("query") or call_args.args[0]
    assert "RB2509.SHFE" in query or "RB2509" in query
    assert "30" in query

    # 验证返回 DataFrame 正确
    assert len(df) == 2
    assert df["source"].iloc[0] == "WIND"


def test_fetch_ohlcv_raises_source_unavailable_on_mcp_error():
    """MCP 连接错误 → fetch_ohlcv 抛 SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=ConnectionError("MCP down")):
        with pytest.raises(SourceUnavailable):
            WindSource().fetch_ohlcv("RB2509.SHFE", days=30)


def test_fetch_ohlcv_or_none_returns_none_on_mcp_error(wind_kline_raw):
    """fetch_ohlcv_or_none 在 MCP 失败时返回 None（优雅降级）。"""
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=ConnectionError("MCP down")):
        result = WindSource().fetch_ohlcv_or_none("RB2509.SHFE", days=30)
        assert result is None


# ─── fetch_quote ──────────────────────────────────────


def test_parse_quote_returns_dict(wind_quote_raw):
    """parse_quote 应返回统一 dict 含 hold/settle/oi_change 等。"""
    from fts.data_sources.wind_source import WindSource

    q = WindSource().parse_quote(wind_quote_raw, "RB2509.SHFE", trace_id="t-q01")

    assert isinstance(q, dict)
    assert q["last"] == 3580
    assert q["hold"] == 82000
    assert q["settle"] == 3580
    assert q["oi_change"] == 2000
    assert q["source"] == "WIND"
    assert q["trace_id"] == "t-q01"


def test_parse_quote_returns_none_on_invalid_data():
    """无效数据 → parse_quote 返回 None。"""
    from fts.data_sources.wind_source import WindSource

    assert WindSource().parse_quote({"data": None}, "RB2509.SHFE") is None
    assert WindSource().parse_quote({}, "RB2509.SHFE") is None
    assert WindSource().parse_quote("not a dict", "RB2509.SHFE") is None


def test_fetch_quote_calls_mcp_and_parses(wind_quote_raw):
    """fetch_quote 应通过 _call_mcp 并解析。"""
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", return_value=wind_quote_raw) as mock_call:
        q = WindSource().fetch_quote("RB2509.SHFE", trace_id="t-q02")

    assert mock_call.called
    assert q["last"] == 3580
    assert q["source"] == "WIND"


def test_fetch_quote_raises_source_unavailable_on_mcp_error():
    """fetch_quote MCP 失败 → SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=ConnectionError("MCP down")):
        with pytest.raises(SourceUnavailable):
            WindSource().fetch_quote("RB2509.SHFE")


# ─── MCP 模块入口 ─────────────────────────────────────


def test_call_mcp_module_function_exists():
    """_call_mcp 模块级函数应存在（供适配器和测试调用）。"""
    from fts.data_sources import wind_source

    assert hasattr(wind_source, "_call_mcp")
    assert callable(wind_source._call_mcp)

# ─── MCP 入口 / set_mcp_handler ─────────────────────────


def test_set_mcp_handler_injects_handler():
    """set_mcp_handler 注入后 _call_mcp 直接调用 handler。"""
    from fts.data_sources import wind_source

    calls = []

    def handler(query: str):
        calls.append(query)
        return {"data": []}

    wind_source.set_mcp_handler(handler)
    try:
        result = wind_source._call_mcp("测试查询")
        assert result == {"data": []}
        assert calls == ["测试查询"]
    finally:
        wind_source._mcp_handler = None


def test_call_mcp_raises_when_enabled_without_handler():
    """mcp_enabled=true 但未注入客户端 → RuntimeError（显式初始化提示）。"""
    from fts.data_sources import wind_source

    wind_source._mcp_handler = None
    with patch("fts.config.settings.get_config") as mock_get:
        cfg = mock_get.return_value
        cfg.mcp_enabled = True
        with pytest.raises(RuntimeError, match="MCP 已启用但客户端未注入"):
            wind_source._call_mcp("q")


def test_call_mcp_returns_none_when_disabled():
    """mcp_enabled=false → _call_mcp 返回 None（降级跳过）。"""
    from fts.data_sources import wind_source

    wind_source._mcp_handler = None
    with patch("fts.config.settings.get_config") as mock_get:
        cfg = mock_get.return_value
        cfg.mcp_enabled = False
        assert wind_source._call_mcp("q") is None


# ─── _pick 字段别名 ───────────────────────────────────


def test_pick_field_alias_fallback():
    """_pick 按别名顺序取第一个存在且非 None 的字段。"""
    from fts.data_sources.wind_source import _pick

    row = {"oi": 100, "open_interest": 200, "hold": 300}
    assert _pick(row, "hold") == 100  # oi 优先
    assert _pick({"open_interest": 200}, "hold") == 200
    assert _pick({"settle": None, "settlement": 5.0}, "settle") == 5.0
    assert _pick({}, "hold") is None
    assert _pick({"custom": 1}, "unknown_field") is None


# ─── parse_ohlcv 补充分支 ──────────────────────────────


def test_parse_ohlcv_returns_none_on_missing_fields():
    """缺必填字段 → parse_ohlcv 直接返回 None。"""
    from fts.data_sources.wind_source import WindSource

    bad = {"data": [{"date": "2026-08-04"}]}
    assert WindSource().parse_ohlcv(bad, "RB2509.SHFE") is None


def test_parse_ohlcv_vwap_typical_when_volume_zero(wind_kline_raw):
    """volume 全 0 → vwap 回退到典型价。"""
    from fts.data_sources.wind_source import WindSource

    for r in wind_kline_raw["data"]:
        r["volume"] = 0
    df = WindSource().parse_ohlcv(wind_kline_raw, "RB2509.SHFE")
    first = df.iloc[0]
    expected = (first["high"] + first["low"] + first["close"]) / 3
    assert abs(df["vwap"].iloc[0] - expected) < 0.01


def test_parse_ohlcv_or_none_returns_none_on_exception():
    """parse_ohlcv 抛通用异常 → parse_ohlcv_or_none 返回 None。"""
    from fts.data_sources.wind_source import WindSource

    raw = {"data": [{"date": {"bad": "object"}, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]}
    assert WindSource().parse_ohlcv_or_none(raw, "RB2509.SHFE") is None


# ─── fetch_ohlcv 补充异常分支 ──────────────────────────


def test_fetch_ohlcv_raises_on_timeout():
    """MCP 超时 → fetch_ohlcv 抛 SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=TimeoutError("timeout")):
        with pytest.raises(SourceUnavailable):
            WindSource().fetch_ohlcv("RB2509.SHFE", days=30)


def test_fetch_ohlcv_raises_on_generic_exception():
    """MCP 通用异常 → fetch_ohlcv 抛 SourceUnavailable（MCP error）。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=ValueError("boom")):
        with pytest.raises(SourceUnavailable, match="MCP error"):
            WindSource().fetch_ohlcv("RB2509.SHFE", days=30)


def test_fetch_ohlcv_or_none_returns_none_on_generic_exception():
    """fetch_ohlcv_or_none 捕获通用异常 → None。"""
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=ValueError("boom")):
        assert WindSource().fetch_ohlcv_or_none("RB2509.SHFE", days=30) is None


# ─── fetch_quote 补充异常分支 ──────────────────────────


def test_fetch_quote_raises_on_timeout():
    """fetch_quote MCP 超时 → SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=TimeoutError("timeout")):
        with pytest.raises(SourceUnavailable):
            WindSource().fetch_quote("RB2509.SHFE")


def test_fetch_quote_raises_on_generic_exception():
    """fetch_quote MCP 通用异常 → SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.wind_source import WindSource

    with patch("fts.data_sources.wind_source._call_mcp", side_effect=ValueError("boom")):
        with pytest.raises(SourceUnavailable):
            WindSource().fetch_quote("RB2509.SHFE")


def test_expected_columns_full_schema():
    """_expected_columns 应返回 17 列。"""
    from fts.data_sources.wind_source import WindSource

    assert len(WindSource._expected_columns()) == 17
    assert WindSource._expected_columns()[-1] == "trace_id"
