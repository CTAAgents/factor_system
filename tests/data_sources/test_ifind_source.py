"""tests/data_sources/test_ifind_source.py — iFinD 适配器测试。

HARNESS §5.4: mock MCP 调用，覆盖连接失败/解析失败/字段缺失/空数据。
iFinD 是字段增强层（EDB 宏观/产业链 + 期货 K 线），不参与 K 线主路径。
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest


# ─── Fixture: iFinD 原始响应 ─────────────────────────────


@pytest.fixture
def ifind_kline_raw() -> dict:
    """模拟 iFinD bond_market_data 返回的 K 线 JSON 响应。"""
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
                "openInterest": 80000,
                "settle": 3540,
                "preSettle": 3520,
                "openInterestChg": 2000,
            },
            {
                "date": "2026-08-04",
                "open": 3540,
                "high": 3600,
                "low": 3530,
                "close": 3580,
                "volume": 120000,
                "amount": 420000000,
                "openInterest": 82000,
                "settle": 3580,
                "preSettle": 3540,
                "openInterestChg": 2000,
            },
        ],
    }


@pytest.fixture
def ifind_quote_raw() -> dict:
    """模拟 iFinD 实时快照响应。"""
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
            "openInterest": 82000,
            "settle": 3580,
            "preSettle": 3540,
            "openInterestChg": 2000,
        },
    }


@pytest.fixture
def ifind_edb_raw() -> dict:
    """模拟 iFinD get_edb_data 返回的 EDB 宏观数据。"""
    return {
        "data": [
            {
                "indicator": "M0001396",  # 中国 GDP
                "indicator_name": "中国:GDP:不变价:当季值",
                "date": "2025-12-31",
                "value": 1349083.5,
                "unit": "亿元",
                "yoy": 4.5,  # 同比
            },
            {
                "indicator": "M0001396",
                "indicator_name": "中国:GDP:不变价:当季值",
                "date": "2026-03-31",
                "value": 1372073.3,
                "unit": "亿元",
                "yoy": 4.7,
            },
        ],
    }


# ─── 探活测试 ──────────────────────────────────────────


def test_is_available_returns_true_when_mcp_responds():
    """MCP 探活成功 → is_available() = True。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", return_value={"status": "ok"}):
        assert IFindSource().is_available() is True


def test_is_available_returns_false_on_connection_error():
    """MCP 连接失败 → is_available() = False。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=ConnectionError("MCP down")):
        assert IFindSource().is_available() is False


def test_is_available_returns_false_on_timeout():
    """MCP 超时 → is_available() = False。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=TimeoutError("timeout")):
        assert IFindSource().is_available() is False


def test_is_available_returns_false_on_exception():
    """任何异常 → is_available() = False（不抛）。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=Exception("unexpected")):
        assert IFindSource().is_available() is False


# ─── 字段映射 — parse_ohlcv 公开方法（核心）─────────────


def test_parse_ohlcv_returns_dataframe_with_17_columns(ifind_kline_raw):
    """parse_ohlcv 应返回 17 列 FTS schema DataFrame。"""
    from fts.data_sources.ifind_source import IFindSource

    df = IFindSource().parse_ohlcv(ifind_kline_raw, "RB2509", trace_id="t-001")

    assert isinstance(df, pd.DataFrame)
    assert len(df) == 2
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


def test_parse_ohlcv_maps_ifind_fields_to_fts(ifind_kline_raw):
    """字段映射: iFinD openInterest → FTS hold, preSettle → FTS pre_settle。"""
    from fts.data_sources.ifind_source import IFindSource

    df = IFindSource().parse_ohlcv(ifind_kline_raw, "RB2509", trace_id="t-002")

    row = df.iloc[0]
    assert row["hold"] == 80000  # iFinD openInterest → FTS hold
    assert row["settle"] == 3540  # iFinD settle → FTS settle
    assert row["pre_settle"] == 3520  # iFinD preSettle → FTS pre_settle
    assert row["oi_change"] == 2000  # iFinD openInterestChg → FTS oi_change


def test_parse_ohlcv_computes_vwap(ifind_kline_raw):
    """vwap 应由 amount / volume 计算。"""
    from fts.data_sources.ifind_source import IFindSource

    df = IFindSource().parse_ohlcv(ifind_kline_raw, "RB2509")

    # 第一行 amount=350M / volume=100K = 3500
    assert abs(df["vwap"].iloc[0] - 3500.0) < 0.01


def test_parse_ohlcv_fills_metadata(ifind_kline_raw):
    """元数据字段应被正确填充。"""
    from fts.data_sources.ifind_source import IFindSource

    df = IFindSource().parse_ohlcv(ifind_kline_raw, "RB2509", trace_id="t-007")

    assert (df["symbol"] == "RB2509").all()
    assert (df["period"] == "daily").all()
    assert (df["source"] == "IFIND").all()
    assert (df["trace_id"] == "t-007").all()
    assert df["fetched_at"].notna().all()


def test_parse_ohlcv_handles_snake_case_alias():
    """iFinD 字段也可能是 snake_case (open_interest / pre_settle) — 都应映射。"""
    from fts.data_sources.ifind_source import IFindSource

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
                "pre_settle": 3540,
                "oi_chg": 2000,
            },
        ]
    }
    df = IFindSource().parse_ohlcv(raw, "RB2509")
    assert df["hold"].iloc[0] == 82000
    assert df["pre_settle"].iloc[0] == 3540
    assert df["oi_change"].iloc[0] == 2000


def test_parse_ohlcv_returns_empty_df_when_no_data():
    """data 为空列表 → 返回带 schema 的空 DataFrame。"""
    from fts.data_sources.ifind_source import IFindSource

    df = IFindSource().parse_ohlcv({"data": []}, "RB2509")
    assert len(df) == 0
    assert "hold" in df.columns
    assert "settle" in df.columns


def test_parse_ohlcv_or_none_returns_none_on_malformed_data():
    """缺必填字段 → parse_ohlcv_or_none 返回 None。"""
    from fts.data_sources.ifind_source import IFindSource

    bad = {"data": [{"date": "2026-08-04"}]}  # 缺 OHLCV
    result = IFindSource().parse_ohlcv_or_none(bad, "RB2509")
    assert result is None


def test_parse_ohlcv_or_none_returns_none_on_non_dict():
    """非 dict 输入 → parse_ohlcv_or_none 返回 None。"""
    from fts.data_sources.ifind_source import IFindSource

    assert IFindSource().parse_ohlcv_or_none("not a dict", "RB2509") is None
    assert IFindSource().parse_ohlcv_or_none(None, "RB2509") is None


# ─── fetch_ohlcv 集成路径（mock _call_mcp）──────────────


def test_fetch_ohlcv_calls_mcp_and_parses(ifind_kline_raw):
    """fetch_ohlcv 应通过 _call_mcp 调用 MCP 并解析响应。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", return_value=ifind_kline_raw) as mock_call:
        df = IFindSource().fetch_ohlcv("RB2509", days=30, trace_id="t-100")

    # 验证调用了 MCP
    assert mock_call.called
    # 验证 query 包含关键信息
    call_args = mock_call.call_args
    query = call_args.kwargs.get("query") or call_args.args[0]
    assert "RB2509" in query
    assert "30" in query

    # 验证返回 DataFrame 正确
    assert len(df) == 2
    assert df["source"].iloc[0] == "IFIND"


def test_fetch_ohlcv_raises_source_unavailable_on_mcp_error():
    """MCP 连接错误 → fetch_ohlcv 抛 SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=ConnectionError("MCP down")):
        with pytest.raises(SourceUnavailable):
            IFindSource().fetch_ohlcv("RB2509", days=30)


def test_fetch_ohlcv_or_none_returns_none_on_mcp_error(ifind_kline_raw):
    """fetch_ohlcv_or_none 在 MCP 失败时返回 None（优雅降级）。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=ConnectionError("MCP down")):
        result = IFindSource().fetch_ohlcv_or_none("RB2509", days=30)
        assert result is None


# ─── fetch_quote ──────────────────────────────────────


def test_parse_quote_returns_dict(ifind_quote_raw):
    """parse_quote 应返回统一 dict 含 hold/settle/oi_change 等。"""
    from fts.data_sources.ifind_source import IFindSource

    q = IFindSource().parse_quote(ifind_quote_raw, "RB2509", trace_id="t-q01")

    assert isinstance(q, dict)
    assert q["last"] == 3580
    assert q["hold"] == 82000
    assert q["settle"] == 3580
    assert q["oi_change"] == 2000
    assert q["source"] == "IFIND"
    assert q["trace_id"] == "t-q01"


def test_parse_quote_returns_none_on_invalid_data():
    """无效数据 → parse_quote 返回 None。"""
    from fts.data_sources.ifind_source import IFindSource

    assert IFindSource().parse_quote({"data": None}, "RB2509") is None
    assert IFindSource().parse_quote({}, "RB2509") is None
    assert IFindSource().parse_quote("not a dict", "RB2509") is None


def test_fetch_quote_calls_mcp_and_parses(ifind_quote_raw):
    """fetch_quote 应通过 _call_mcp 并解析。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", return_value=ifind_quote_raw) as mock_call:
        q = IFindSource().fetch_quote("RB2509", trace_id="t-q02")

    assert mock_call.called
    assert q["last"] == 3580
    assert q["source"] == "IFIND"


def test_fetch_quote_raises_source_unavailable_on_mcp_error():
    """fetch_quote MCP 失败 → SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=ConnectionError("MCP down")):
        with pytest.raises(SourceUnavailable):
            IFindSource().fetch_quote("RB2509")


# ─── fetch_edb 独家能力（iFinD 核心价值）───────────────


def test_fetch_edb_returns_list_of_dicts(ifind_edb_raw):
    """fetch_edb 应返回 List[dict] 格式 EDB 数据。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", return_value=ifind_edb_raw) as mock_call:
        result = IFindSource().fetch_edb(
            indicator="M0001396",
            start_date="2025-12-01",
            end_date="2026-03-31",
            trace_id="t-edb01",
        )

    # 验证调用了 MCP
    assert mock_call.called
    call_args = mock_call.call_args
    query = call_args.kwargs.get("query") or call_args.args[0]
    assert "M0001396" in query
    assert "2025-12-01" in query or "20251201" in query
    assert "2026-03-31" in query or "20260331" in query

    # 验证返回结构
    assert isinstance(result, list)
    assert len(result) == 2
    assert all("indicator" in r for r in result)
    assert all("date" in r for r in result)
    assert all("value" in r for r in result)


def test_fetch_edb_returns_empty_list_on_no_data():
    """EDB 无数据 → 返回空列表。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", return_value={"data": []}):
        result = IFindSource().fetch_edb(
            indicator="M0001396",
            start_date="2025-12-01",
            end_date="2025-12-31",
        )
        assert result == []


def test_fetch_edb_returns_none_on_mcp_error():
    """EDB MCP 失败 → 返回 None（不抛）。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=ConnectionError("MCP down")):
        result = IFindSource().fetch_edb(
            indicator="M0001396",
            start_date="2025-12-01",
            end_date="2025-12-31",
        )
        assert result is None


def test_fetch_edb_uses_natural_language_query(ifind_edb_raw):
    """EDB 查询应使用自然语言而非结构化参数。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", return_value=ifind_edb_raw) as mock_call:
        IFindSource().fetch_edb(
            indicator="PTA产量",  # 中文指标名
            start_date="2025-12-01",
            end_date="2026-03-31",
        )

    query = mock_call.call_args.kwargs.get("query") or mock_call.call_args.args[0]
    # 自然语言查询应包含关键中文术语
    assert "PTA产量" in query
    assert "2025-12-01" in query or "20251201" in query


# ─── MCP 模块入口 ─────────────────────────────────────


def test_call_mcp_module_function_exists():
    """_call_mcp 模块级函数应存在（供适配器和测试调用）。"""
    from fts.data_sources import ifind_source

    assert hasattr(ifind_source, "_call_mcp")
    assert callable(ifind_source._call_mcp)


# ─── 期货代码转换（iFinD 特有）────────────────────────


def test_symbol_to_ifind_conversion():
    """FTS 品种 → iFinD 代码（RB2509 → RB2509.SHF，注意 SHFE → SHF）。"""
    from fts.data_sources.ifind_source import IFindSource

    assert IFindSource._symbol_to_ifind("RB2509") == "RB2509.SHF"
    assert IFindSource._symbol_to_ifind("CU2509") == "CU2509.SHF"
    assert IFindSource._symbol_to_ifind("M2509") == "M2509.DCE"
    assert IFindSource._symbol_to_ifind("TA509") == "TA509.CZC"
    assert IFindSource._symbol_to_ifind("IF2509") == "IF2509.CFX"

# ─── MCP 入口 / set_mcp_handler ─────────────────────────


def test_set_mcp_handler_injects_handler():
    """set_mcp_handler 注入后 _call_mcp 直接调用 handler。"""
    from fts.data_sources import ifind_source

    calls = []

    def handler(query: str):
        calls.append(query)
        return {"data": []}

    ifind_source.set_mcp_handler(handler)
    try:
        result = ifind_source._call_mcp("测试查询")
        assert result == {"data": []}
        assert calls == ["测试查询"]
    finally:
        ifind_source._mcp_handler = None


def test_call_mcp_raises_when_enabled_without_handler():
    """mcp_enabled=true 但未注入客户端 → RuntimeError（显式初始化提示）。"""
    from fts.data_sources import ifind_source

    ifind_source._mcp_handler = None
    with patch("fts.config.settings.get_config") as mock_get:
        cfg = mock_get.return_value
        cfg.mcp_enabled = True
        with pytest.raises(RuntimeError, match="MCP 已启用但客户端未注入"):
            ifind_source._call_mcp("q")


def test_call_mcp_returns_none_when_disabled():
    """mcp_enabled=false → _call_mcp 返回 None（降级跳过）。"""
    from fts.data_sources import ifind_source

    ifind_source._mcp_handler = None
    with patch("fts.config.settings.get_config") as mock_get:
        cfg = mock_get.return_value
        cfg.mcp_enabled = False
        assert ifind_source._call_mcp("q") is None


# ─── _infer_exchange（iFinD 特有后缀）───────────────────


def test_infer_exchange_t_bond_fallback():
    """T 开头品种（国债）兜底为 CFX。"""
    from fts.data_sources.ifind_source import _infer_exchange

    assert _infer_exchange("T2509") == "CFX"


def test_infer_exchange_unknown_raises():
    """未知品种前缀应抛 ValueError。"""
    from fts.data_sources.ifind_source import _infer_exchange

    with pytest.raises(ValueError):
        _infer_exchange("XX0")


# ─── parse_ohlcv 补充分支 ──────────────────────────────


def test_parse_ohlcv_returns_none_on_missing_fields():
    """缺必填字段 → parse_ohlcv 直接返回 None。"""
    from fts.data_sources.ifind_source import IFindSource

    bad = {"data": [{"date": "2026-08-04"}]}
    assert IFindSource().parse_ohlcv(bad, "RB2509") is None


def test_parse_ohlcv_vwap_typical_when_volume_zero(ifind_kline_raw):
    """volume 全 0 → vwap 回退到典型价。"""
    from fts.data_sources.ifind_source import IFindSource

    for r in ifind_kline_raw["data"]:
        r["volume"] = 0
    df = IFindSource().parse_ohlcv(ifind_kline_raw, "RB2509")
    first = df.iloc[0]
    expected = (first["high"] + first["low"] + first["close"]) / 3
    assert abs(df["vwap"].iloc[0] - expected) < 0.01


def test_parse_ohlcv_or_none_returns_none_on_exception():
    """parse_ohlcv 抛通用异常 → parse_ohlcv_or_none 返回 None。"""
    from fts.data_sources.ifind_source import IFindSource

    raw = {"data": [{"date": {"bad": "object"}, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]}
    assert IFindSource().parse_ohlcv_or_none(raw, "RB2509") is None


# ─── fetch_ohlcv 补充异常分支 ──────────────────────────


def test_fetch_ohlcv_raises_on_timeout():
    """MCP 超时 → fetch_ohlcv 抛 SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=TimeoutError("timeout")):
        with pytest.raises(SourceUnavailable):
            IFindSource().fetch_ohlcv("RB2509", days=30)


def test_fetch_ohlcv_raises_on_generic_exception():
    """MCP 通用异常 → fetch_ohlcv 抛 SourceUnavailable（MCP error）。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=ValueError("boom")):
        with pytest.raises(SourceUnavailable, match="MCP error"):
            IFindSource().fetch_ohlcv("RB2509", days=30)


def test_fetch_ohlcv_or_none_returns_none_on_generic_exception():
    """fetch_ohlcv_or_none 捕获通用异常 → None。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=ValueError("boom")):
        assert IFindSource().fetch_ohlcv_or_none("RB2509", days=30) is None


# ─── fetch_quote 补充异常分支 ──────────────────────────


def test_fetch_quote_raises_on_timeout():
    """fetch_quote MCP 超时 → SourceUnavailable。"""
    from fts.data_sources.base import SourceUnavailable
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=TimeoutError("timeout")):
        with pytest.raises(SourceUnavailable):
            IFindSource().fetch_quote("RB2509")


# ─── fetch_edb 补充分支 ────────────────────────────────


def test_fetch_edb_returns_none_on_timeout():
    """EDB MCP 超时 → 返回 None。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=TimeoutError("timeout")):
        assert IFindSource().fetch_edb("M0001396") is None


def test_fetch_edb_returns_none_on_generic_exception():
    """EDB MCP 通用异常 → 返回 None。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", side_effect=ValueError("boom")):
        assert IFindSource().fetch_edb("M0001396") is None


def test_fetch_edb_returns_none_on_non_dict():
    """EDB 响应非 dict → 返回 None。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", return_value=["not", "dict"]):
        assert IFindSource().fetch_edb("M0001396") is None


def test_fetch_edb_returns_empty_list_when_data_not_list():
    """EDB data 非 list → 返回空列表。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", return_value={"data": {"not": "list"}}):
        assert IFindSource().fetch_edb("M0001396") == []


def test_fetch_edb_skips_non_dict_rows():
    """EDB 行非 dict → 跳过。"""
    from fts.data_sources.ifind_source import IFindSource

    rows = [
        {"indicator": "M0001396", "date": "2025-12-31", "value": 1.0},
        "not-a-dict",
        42,
    ]
    with patch("fts.data_sources.ifind_source._call_mcp", return_value={"data": rows}):
        result = IFindSource().fetch_edb("M0001396")
        assert len(result) == 1


def test_fetch_edb_with_only_start_date(ifind_edb_raw):
    """仅指定 start_date 时查询应含“起”字。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.ifind_source._call_mcp", return_value=ifind_edb_raw) as mock_call:
        IFindSource().fetch_edb("M0001396", start_date="2025-12-01")
    query = mock_call.call_args.args[0]
    assert "2025-12-01起" in query


# ─── get_macro_series（缓存 → miss 拉取）───────────────


def test_get_macro_series_cache_hit():
    """edb_cache 命中 → 直接返回缓存，不调用 fetch_edb。"""
    from fts.data_sources.ifind_source import IFindSource

    cached = pd.Series([1.0, 2.0], index=pd.to_datetime(["2025-01-01", "2025-01-02"]))
    src = IFindSource()
    with patch.object(IFindSource, "_read_edb_cache", return_value=cached):
        with patch.object(IFindSource, "fetch_edb", return_value=[{"date": "2025-01-03", "value": 3.0}]) as mock_fetch:
            result = src.get_macro_series("M0001396", db_path="dummy.duckdb")
    mock_fetch.assert_not_called()
    assert result is cached


def test_get_macro_series_miss_no_data():
    """缓存 miss 且拉取无数据 → 返回 None。"""
    from fts.data_sources.ifind_source import IFindSource

    src = IFindSource()
    with patch.object(IFindSource, "_read_edb_cache", return_value=None):
        with patch.object(IFindSource, "fetch_edb", return_value=[]):
            assert src.get_macro_series("M0001396", db_path="dummy.duckdb") is None


def test_get_macro_series_miss_fetch_success():
    """缓存 miss 拉取成功 → 写缓存并返回 Series。"""
    from fts.data_sources.ifind_source import IFindSource

    rows = [{"date": "2025-01-03", "value": 3.0}]
    series = pd.Series([3.0], index=pd.to_datetime(["2025-01-03"]))
    src = IFindSource()
    with patch.object(IFindSource, "_read_edb_cache", return_value=None):
        with patch.object(IFindSource, "fetch_edb", return_value=rows):
            with patch.object(IFindSource, "_write_edb_cache") as mock_write:
                with patch.object(IFindSource, "_rows_to_series", return_value=series):
                    result = src.get_macro_series("M0001396", db_path="dummy.duckdb", trace_id="t")
    mock_write.assert_called_once()
    assert result is series


# ─── _read_edb_cache ───────────────────────────────────


def test_read_edb_cache_missing_db_returns_none(tmp_path):
    """DB 文件不存在 → None。"""
    from fts.data_sources.ifind_source import IFindSource

    assert IFindSource._read_edb_cache(tmp_path / "missing.duckdb", "M0001396") is None


def test_read_edb_cache_empty_returns_none(tmp_path):
    """无匹配数据 → None。"""
    import duckdb

    from fts.data_sources.ifind_source import IFindSource

    db_path = tmp_path / "edb_empty.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE TABLE edb_cache (indicator VARCHAR, date DATE, value DOUBLE)")
    finally:
        con.close()
    assert IFindSource._read_edb_cache(db_path, "M0001396") is None


def test_read_edb_cache_hit(tmp_path):
    """命中缓存 → DatetimeIndex Series。"""
    import duckdb

    from fts.data_sources.ifind_source import IFindSource

    db_path = tmp_path / "edb_hit.duckdb"
    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE TABLE edb_cache (indicator VARCHAR, date DATE, value DOUBLE)")
        con.execute("INSERT INTO edb_cache VALUES ('M0001396', '2025-12-31', 100.5)")
        con.execute("INSERT INTO edb_cache VALUES ('OTHER', '2025-12-31', 1.0)")
    finally:
        con.close()
    series = IFindSource._read_edb_cache(db_path, "M0001396")
    assert series is not None
    assert len(series) == 1
    assert series.iloc[0] == 100.5
    assert series.index[0] == pd.Timestamp("2025-12-31")
    # 带日期区间过滤分支
    series2 = IFindSource._read_edb_cache(db_path, "M0001396", "2025-01-01", "2026-12-31")
    assert len(series2) == 1


def test_read_edb_cache_duckdb_error_returns_none(tmp_path):
    """duckdb 读取异常 → None（不抛）。"""
    from fts.data_sources.ifind_source import IFindSource

    db_path = tmp_path / "edb_bad.duckdb"
    db_path.touch()
    with patch("duckdb.connect", side_effect=Exception("boom")):
        assert IFindSource._read_edb_cache(db_path, "M0001396") is None


# ─── _write_edb_cache ──────────────────────────────────


def test_write_edb_cache_empty_rows_noop(tmp_path):
    """空 rows → 直接返回。"""
    from fts.data_sources.ifind_source import IFindSource

    IFindSource._write_edb_cache(tmp_path / "edb_noop.duckdb", [])


def test_write_edb_cache_inserts(tmp_path):
    """正常写入 edb_cache 表。"""
    import duckdb

    from fts.data_sources.ifind_source import IFindSource

    db_path = tmp_path / "edb_write.duckdb"
    rows = [
        {
            "indicator": "M0001396",
            "date": "2025-12-31",
            "value": 100.5,
            "unit": "亿元",
            "source": "IFIND",
            "trace_id": "t",
        }
    ]
    IFindSource._write_edb_cache(db_path, rows)
    con = duckdb.connect(str(db_path))
    try:
        n = con.execute("SELECT COUNT(*) FROM edb_cache").fetchone()[0]
    finally:
        con.close()
    assert n == 1


def test_write_edb_cache_error_swallowed(tmp_path):
    """写入异常 → 吞掉（仅 warning）。"""
    from fts.data_sources.ifind_source import IFindSource

    with patch("fts.data_sources.migrate.migrate_schema", side_effect=Exception("boom")):
        IFindSource._write_edb_cache(
            tmp_path / "edb_err.duckdb",
            [{"indicator": "X", "date": "2025-01-01", "value": 1.0}],
        )


# ─── _rows_to_series ───────────────────────────────────


def test_rows_to_series_sorts_by_date():
    """有效行按日期升序转换为 Series。"""
    from fts.data_sources.ifind_source import IFindSource

    rows = [
        {"date": "2025-03-31", "value": "1372073.3"},
        {"date": "2025-12-31", "value": 1349083.5},
    ]
    series = IFindSource._rows_to_series(rows)
    assert isinstance(series, pd.Series)
    assert len(series) == 2
    assert series.index[0] == pd.Timestamp("2025-03-31")
    assert series.iloc[0] == 1372073.3


def test_rows_to_series_empty_when_no_valid():
    """无有效行（date/value 缺失）→ 空 Series。"""
    from fts.data_sources.ifind_source import IFindSource

    rows = [
        {"date": "", "value": None},
        {"date": "2025-01-01", "value": None},
    ]
    series = IFindSource._rows_to_series(rows)
    assert series.empty


def test_expected_columns_full_schema():
    """_expected_columns 应返回 17 列。"""
    from fts.data_sources.ifind_source import IFindSource

    assert len(IFindSource._expected_columns()) == 17
    assert IFindSource._expected_columns()[-1] == "trace_id"
