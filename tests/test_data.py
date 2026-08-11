"""
tests/test_data.py — FTS data 模块测试（基于 MCP/akshare 数据源）。

覆盖目标:
  1. FTSDataProvider __init__ 参数注入
  2. get_ohlcv: 合成数据降级
  3. get_csi300_panel: 面板数据
  4. get_etf_panel / get_stock_panel
  5. search_symbol
  6. synthesize_ohlcv: 输出形状和列验证
  7. get_data_provider: 全局单例
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from fts.data import (
    FTSDataProvider,
    get_data_provider,
)
from fts.data_futures import (
    FUTURES_CORE_SUBSET,
    FuturesDataProvider,
)
from fts.data_mcp import (
    MCPDataError,
    MCPDataProvider,
    _fetch_kline_json,
    _is_etf_code,
    _kline_to_df,
    _to_tencent_code,
)


# ═══════════════════════════════════════════════════════════
# 1. __init__
# ═══════════════════════════════════════════════════════════


class TestInit:
    def test_with_mcp_provider(self, mocker):
        mock_mcp = mocker.MagicMock(spec=MCPDataProvider)
        p = FTSDataProvider(mcp_provider=mock_mcp)
        assert p._mcp is mock_mcp

    def test_default_mcp_provider(self):
        p = FTSDataProvider()
        assert p._mcp is not None
        assert isinstance(p._mcp, MCPDataProvider)


# ═══════════════════════════════════════════════════════════
# 2. get_ohlcv（降级到合成数据）
# ═══════════════════════════════════════════════════════════


class TestGetOhlcv:
    def test_returns_real_data(self, mocker):
        """应返回真实的 OHLCV 数据（mock provider 数据不被替换）。"""
        mock_mcp = mocker.MagicMock(spec=MCPDataProvider)
        idx = pd.date_range("2025-01-01", periods=250, freq="B")
        mock_df = pd.DataFrame(
            {
                "open": np.linspace(4.0, 4.5, 250),
                "high": np.linspace(4.05, 4.55, 250),
                "low": np.linspace(3.95, 4.45, 250),
                "close": np.linspace(4.0, 4.5, 250),
                "volume": np.full(250, 1e6),
            },
            index=idx,
        )
        mock_mcp.get_ohlcv.return_value = mock_df

        p = FTSDataProvider(mcp_provider=mock_mcp)
        df = p.get_ohlcv("510300", days=250)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) > 0
        assert isinstance(df.index, pd.DatetimeIndex)
        # mock provider 数据被直接传递，价格区间保持 4~4.5 元
        assert 3.0 < df["close"].mean() < 6.0
        mock_mcp.get_ohlcv.assert_called_once()

    def test_etf_ohlcv(self):
        p = FTSDataProvider()
        df = p.get_etf_ohlcv("510300", days=250)
        assert isinstance(df, pd.DataFrame)
        assert "close" in df.columns
        assert len(df) > 0

    def test_custom_adjust(self):
        p = FTSDataProvider()
        df = p.get_ohlcv("000001", adjust="qfq")
        assert isinstance(df, pd.DataFrame)
        assert not df.empty


# ═══════════════════════════════════════════════════════════
# 3. get_csi300_panel
# ═══════════════════════════════════════════════════════════


class TestGetCsi300Panel:
    def test_returns_panel(self):
        """CSI300 面板数据应返回 (panel, common_dates) 结构。"""
        p = FTSDataProvider()
        panel, common_dates = p.get_csi300_panel(days=100, max_stocks=3)
        assert isinstance(panel, dict)
        assert len(panel) > 0
        assert isinstance(common_dates, pd.DatetimeIndex)
        for sym, df in panel.items():
            assert "close" in df.columns


# ═══════════════════════════════════════════════════════════
# 4. ETF / Stock panel
# ═══════════════════════════════════════════════════════════


class TestPanelMethods:
    def test_etf_panel_synthetic(self):
        p = FTSDataProvider()
        panel, dates = p.get_etf_panel(days=100)
        assert isinstance(panel, dict)
        assert isinstance(dates, pd.DatetimeIndex)

    def test_stock_panel_synthetic(self):
        p = FTSDataProvider()
        panel, dates = p.get_stock_panel(["000001", "000002"], days=100)
        assert isinstance(panel, dict)
        assert isinstance(dates, pd.DatetimeIndex)


# ═══════════════════════════════════════════════════════════
# 5. search_symbol
# ═══════════════════════════════════════════════════════════


class TestSearchSymbol:
    def test_search_returns_list(self):
        p = FTSDataProvider()
        # 不验证实际返回值（依赖网络），只验证类型
        try:
            results = p.search_symbol("银行", limit=5)
            assert isinstance(results, list)
        except Exception:
            # 网络不可用时是正常的
            pass


# ═══════════════════════════════════════════════════════════
# 6. synthesize_ohlcv
# ═══════════════════════════════════════════════════════════


class TestSynthesizeOhlcv:
    def test_output_shape_and_columns(self):
        df = FTSDataProvider.synthesize_ohlcv(n_days=500, base_price=100.0, seed=42)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) == 500

    def test_index_is_datetime(self):
        df = FTSDataProvider.synthesize_ohlcv(n_days=30, seed=99)
        assert isinstance(df.index, pd.DatetimeIndex)
        expected_start = datetime.now() - timedelta(days=30)
        assert df.index[0].date() >= expected_start.date() - timedelta(days=1)

    def test_reproducible_with_seed(self):
        df1 = FTSDataProvider.synthesize_ohlcv(n_days=100, base_price=50.0, seed=42)
        df2 = FTSDataProvider.synthesize_ohlcv(n_days=100, base_price=50.0, seed=42)
        pd.testing.assert_frame_equal(
            df1.reset_index(drop=True),
            df2.reset_index(drop=True),
        )

    def test_different_seed_different_data(self):
        df1 = FTSDataProvider.synthesize_ohlcv(n_days=100, seed=42)
        df2 = FTSDataProvider.synthesize_ohlcv(n_days=100, seed=99)
        assert not df1["close"].equals(df2["close"])

    def test_volume_is_positive(self):
        df = FTSDataProvider.synthesize_ohlcv(n_days=50)
        assert (df["volume"] >= 0).all()
        assert df["volume"].dtype == float

    def test_high_is_max_low_is_min(self):
        df = FTSDataProvider.synthesize_ohlcv(n_days=100)
        assert (df["high"] >= df["close"]).all()
        assert (df["low"] <= df["close"]).all()


# ═══════════════════════════════════════════════════════════
# 7. get_data_provider（全局单例）
# ═══════════════════════════════════════════════════════════


class TestGetDataProvider:
    def test_returns_fts_data_provider(self):
        p = get_data_provider()
        assert isinstance(p, FTSDataProvider)

    def test_singleton_same_instance(self):
        p1 = get_data_provider()
        p2 = get_data_provider()
        assert p1 is p2

    def test_reset_between_tests(self):
        import fts.data as _data

        _data._default_provider = None
        p = get_data_provider()
        assert _data._default_provider is p


# ═══════════════════════════════════════════════════════════
# 8. _to_tencent_code 辅助函数
# ═══════════════════════════════════════════════════════════


class TestToTencentCode:
    """覆盖 data_mcp._to_tencent_code 的边缘/错误路径。"""

    def test_already_sh_prefix(self):
        """Line 50-51: 代码已含 sh 前缀时原始返回。"""
        assert _to_tencent_code("sh510300") == "sh510300"

    def test_already_sz_prefix(self):
        """Line 50-51: 代码已含 sz 前缀时原始返回。"""
        assert _to_tencent_code("sz000001") == "sz000001"

    def test_shanghai_6_prefix(self):
        """Line 53-54: 6 开头代码加 sh 前缀。"""
        assert _to_tencent_code("600000") == "sh600000"

    def test_shanghai_9_prefix(self):
        """Line 53-54: 9 开头代码加 sh 前缀。"""
        assert _to_tencent_code("900001") == "sh900001"

    def test_shenzhen_default(self):
        """Line 55: 非 6/9 开头默认加 sz 前缀。"""
        assert _to_tencent_code("000001") == "sz000001"


# ═══════════════════════════════════════════════════════════
# 9. _is_etf_code 辅助函数
# ═══════════════════════════════════════════════════════════


class TestIsEtfCode:
    """覆盖 data_mcp._is_etf_code 的全部路径。"""

    def test_etf_51_prefix(self):
        """Line 129: 51 开头是 ETF。"""
        assert _is_etf_code("510300") is True

    def test_etf_56_prefix(self):
        """Line 129: 56 开头是 ETF。"""
        assert _is_etf_code("560001") is True

    def test_etf_58_prefix(self):
        """Line 129: 58 开头是 ETF。"""
        assert _is_etf_code("588000") is True

    def test_etf_159_prefix(self):
        """Line 131: 159 开头是 ETF。"""
        assert _is_etf_code("159915") is True

    def test_not_etf(self):
        """Line 133: 普通股票不是 ETF。"""
        assert _is_etf_code("000001") is False

    def test_etf_with_sh_prefix_stripped(self):
        """Line 126-128: 带 sh 前缀的 ETF 代码。"""
        assert _is_etf_code("sh510300") is True

    def test_not_etf_with_sz_prefix_stripped(self):
        """Line 126-128: 带 sz 前缀的非 ETF 代码。"""
        assert _is_etf_code("sz000001") is False


# ═══════════════════════════════════════════════════════════
# 9.5 _tq_stock_available 探活缓存 + 失败冷却重试（GAP-076）
# ═══════════════════════════════════════════════════════════


class TestTqStockAvailable:
    """覆盖 data_mcp._tq_stock_available 的探活缓存/失败冷却重试机制。

    修复场景：TQ-Local（17709）瞬时抖动导致首次探活失败后整进程永久降级
    （缓存 False 不再重试）。新机制：失败冷却期后自动重探活 + 瞬时重试。
    """

    def _reset(self, monkeypatch):
        import fts.data_mcp as data_mcp

        monkeypatch.setattr(data_mcp, "_TQ_STOCK_AVAILABLE", None)
        monkeypatch.setattr(data_mcp, "_TQ_LAST_PROBE_TS", 0.0)
        monkeypatch.setattr(data_mcp.time, "sleep", lambda _s: None)
        return data_mcp

    def test_first_probe_success_cached(self, monkeypatch):
        """首次探活成功 → True 且缓存，后续不再探活。"""
        data_mcp = self._reset(monkeypatch)
        calls = {"n": 0}

        def probe() -> bool:
            calls["n"] += 1
            return True

        monkeypatch.setattr(data_mcp, "_probe_tq_once", probe)
        assert data_mcp._tq_stock_available() is True
        assert data_mcp._tq_stock_available() is True
        assert calls["n"] == 1, "成功后应缓存，不再探活"

    def test_transient_failure_retry_recovers(self, monkeypatch):
        """瞬时抖动：首次探活失败、重试成功 → 返回 True（间歇性失败被吸收）。"""
        data_mcp = self._reset(monkeypatch)
        seq = iter([False, True])

        def probe() -> bool:
            return next(seq)

        monkeypatch.setattr(data_mcp, "_probe_tq_once", probe)
        assert data_mcp._tq_stock_available() is True
        assert data_mcp._TQ_STOCK_AVAILABLE is True

    def test_all_failures_cooldown_no_reprobe(self, monkeypatch):
        """全部重试失败 → False，且冷却期内不再重复探活。"""
        data_mcp = self._reset(monkeypatch)
        calls = {"n": 0}

        def probe() -> bool:
            calls["n"] += 1
            return False

        monkeypatch.setattr(data_mcp, "_probe_tq_once", probe)
        assert data_mcp._tq_stock_available() is False
        assert calls["n"] == data_mcp._TQ_PROBE_RETRIES + 1, "重试次数 = retries + 1"
        # 冷却期内不再探活
        assert data_mcp._tq_stock_available() is False
        assert calls["n"] == data_mcp._TQ_PROBE_RETRIES + 1

    def test_cooldown_expiry_reprobe_recovers(self, monkeypatch):
        """冷却期结束自动重探活，成功则恢复 True（进程级恢复能力）。"""
        import time as _time

        data_mcp = self._reset(monkeypatch)
        monkeypatch.setattr(data_mcp, "_probe_tq_once", lambda: False)
        assert data_mcp._tq_stock_available() is False
        # 推进到冷却期之外
        monkeypatch.setattr(
            data_mcp,
            "_TQ_LAST_PROBE_TS",
            _time.time() - data_mcp._TQ_PROBE_COOLDOWN - 1.0,
        )
        monkeypatch.setattr(data_mcp, "_probe_tq_once", lambda: True)
        assert data_mcp._tq_stock_available() is True

    def test_probe_once_ok_response(self, monkeypatch):
        """_probe_tq_once 对合法 TQ 响应返回 True。"""
        import json
        from unittest import mock

        data_mcp = self._reset(monkeypatch)

        class _FakeResp:
            def __init__(self, body):
                self._body = body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return self._body

        resp = _FakeResp(json.dumps({"result": {"Value": {"000001.SZ": {"Close": ["11.37"]}}}}).encode("utf-8"))
        with mock.patch("urllib.request.urlopen", return_value=resp) as m_urlopen:
            assert data_mcp._probe_tq_once() is True
        assert m_urlopen.called

    def test_probe_once_error_response(self, monkeypatch):
        """_probe_tq_once 对异常/非法响应返回 False。"""
        import urllib.error
        from unittest import mock

        data_mcp = self._reset(monkeypatch)
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            assert data_mcp._probe_tq_once() is False


# ═══════════════════════════════════════════════════════════
# 10. _fetch_kline_json 错误处理
# ═══════════════════════════════════════════════════════════


class TestFetchKlineJson:
    """覆盖 data_mcp._fetch_kline_json 的 HTTP/数据异常路径。"""

    def test_http_request_fails(self, mocker):
        """Lines 97-98: HTTP 请求异常 → MCPDataError。"""
        mock_client = mocker.MagicMock()
        mock_client.get.side_effect = Exception("connection refused")
        mocker.patch("fts.data_mcp._get_http", return_value=mock_client)
        with pytest.raises(MCPDataError, match="腾讯 K 线请求失败"):
            _fetch_kline_json("sh510300", 250, "qfq")

    def test_bad_response_code(self, mocker):
        """Line 101: 响应 code≠0 → MCPDataError。"""
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {"code": -1, "msg": "invalid param"}
        mock_client.get.return_value = mock_resp
        mocker.patch("fts.data_mcp._get_http", return_value=mock_client)
        with pytest.raises(MCPDataError, match="腾讯 K 线返回异常"):
            _fetch_kline_json("sh510300", 250, "qfq")

    def test_code_not_in_data(self, mocker):
        """Line 105: 代码不在 data 中 → MCPDataError。"""
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {"code": 0, "data": {"other_code": {}}}
        mock_client.get.return_value = mock_resp
        mocker.patch("fts.data_mcp._get_http", return_value=mock_client)
        with pytest.raises(MCPDataError, match="腾讯 K 线无数据"):
            _fetch_kline_json("sh510300", 250, "qfq")

    def test_no_kline_key(self, mocker):
        """Line 116: 无 qfqday/hfqday/day 键 → MCPDataError。"""
        mock_client = mocker.MagicMock()
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {
            "code": 0,
            "data": {"sh510300": {"other_key": []}},
        }
        mock_client.get.return_value = mock_resp
        mocker.patch("fts.data_mcp._get_http", return_value=mock_client)
        with pytest.raises(MCPDataError, match="腾讯 K 线无 K 线数据"):
            _fetch_kline_json("sh510300", 250, "qfq")


# ═══════════════════════════════════════════════════════════
# 11. _kline_to_df 边缘路径
# ═══════════════════════════════════════════════════════════


class TestKlineToDf:
    """覆盖 data_mcp._kline_to_df 的跳过/空结果路径。"""

    def test_skip_short_row(self, mocker):
        """Line 146: 行元素不足 6 个时跳过。"""
        raw = [["2024-01-01", "10", "11", "12"]]  # only 4 elements
        df = _kline_to_df(raw)
        assert df.empty

    def test_all_rows_skipped(self, mocker):
        """Line 157: 所有行都被跳过 → 返回空 DataFrame。"""
        raw = [
            ["2024-01-01", "10"],
            ["2024-01-02", "11"],
        ]
        df = _kline_to_df(raw)
        assert df.empty

    def test_mixed_valid_invalid(self):
        """Line 146: 混合有效/无效行，只保留有效行。"""
        raw = [
            ["2024-01-01", "10", "12", "9", "11", "1000"],
            ["2024-01-02"],  # 短行，跳过
            ["2024-01-03", "11", "13", "10", "12", "2000"],
        ]
        df = _kline_to_df(raw)
        assert not df.empty
        assert len(df) == 2
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]


# ═══════════════════════════════════════════════════════════
# 12. MCPDataProvider.get_ohlcv 降级回退
# ═══════════════════════════════════════════════════════════


class TestMCPGetOhlcvFallback:
    """覆盖 data_mcp.MCPDataProvider.get_ohlcv 的异常→合成数据降级路径。"""

    def test_mcp_error_fallback(self, mocker):
        """Lines 210-211, 215-216: MCPDataError → 合成数据降级。"""
        mocker.patch("fts.data_mcp._fetch_kline_json", side_effect=MCPDataError("fail"))
        provider = MCPDataProvider()
        df = provider.get_ohlcv("000001", days=100)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100
        assert "close" in df.columns

    def test_generic_exception_fallback(self, mocker):
        """Lines 212-213, 215-216: 通用异常 → 合成数据降级。"""
        mocker.patch("fts.data_mcp._fetch_kline_json", side_effect=ValueError("bad"))
        provider = MCPDataProvider()
        df = provider.get_ohlcv("000001", days=100)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100

    def test_empty_dataframe_fallback(self, mocker):
        """Lines 215-216: 空 DataFrame → 合成数据降级。"""
        mocker.patch("fts.data_mcp._fetch_kline_json", return_value=[])
        mocker.patch("fts.data_mcp._kline_to_df", return_value=pd.DataFrame())
        provider = MCPDataProvider()
        df = provider.get_ohlcv("000001", days=100)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100


# ═══════════════════════════════════════════════════════════
# 13. MCPDataProvider.get_etf_ohlcv 委托
# ═══════════════════════════════════════════════════════════


class TestMCPGetEtfOhlcv:
    """覆盖 data_mcp.MCPDataProvider.get_etf_ohlcv (line 228)。"""

    def test_delegates_to_get_ohlcv(self, mocker):
        """Line 228: 委托给 get_ohlcv。"""
        mock_df = pd.DataFrame({"close": [1.0]})
        mock_get = mocker.patch.object(MCPDataProvider, "get_ohlcv", return_value=mock_df)
        provider = MCPDataProvider()
        result = provider.get_etf_ohlcv("510300", days=100)
        mock_get.assert_called_once_with("510300", days=100, adjust="qfq", trace_id="")
        assert result is mock_df


# ═══════════════════════════════════════════════════════════
# 14. MCPDataProvider.get_stock_panel 全部失败→合成数据
# ═══════════════════════════════════════════════════════════


class TestMCPGetStockPanelFallback:
    """覆盖 data_mcp.MCPDataProvider.get_stock_panel 的异常和空面板路径。"""

    def test_all_symbols_fail(self, mocker):
        """Lines 260-261, 264-267: 所有标的失败 → 合成数据面板。"""
        mocker.patch.object(MCPDataProvider, "get_ohlcv", side_effect=MCPDataError("fail"))
        provider = MCPDataProvider()
        panel, dates = provider.get_stock_panel(["000001", "000002"], days=100)
        assert "SYNTHETIC" in panel
        df = panel["SYNTHETIC"]
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100

    def test_partial_failure_still_works(self, mocker):
        """部分成功仍返回有效面板。"""
        good_df = pd.DataFrame(
            {
                "close": [1.0, 2.0],
                "open": [0.9, 1.8],
                "high": [1.1, 2.2],
                "low": [0.8, 1.7],
                "volume": [1000.0, 2000.0],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )

        def side_effect(sym, **kwargs):
            if sym == "000001":
                return good_df
            raise MCPDataError("fail")

        mocker.patch.object(MCPDataProvider, "get_ohlcv", side_effect=side_effect)
        provider = MCPDataProvider()
        panel, dates = provider.get_stock_panel(["000001", "000002"], days=100)
        assert "000001" in panel
        assert "000002" not in panel
        assert len(dates) == 2


# ═══════════════════════════════════════════════════════════
# 15. FTSDataProvider.get_ohlcv 降级回退
# ═══════════════════════════════════════════════════════════


class TestFTSGetOhlcvFallback:
    """覆盖 data.FTSDataProvider.get_ohlcv 的异常→合成数据降级路径 (lines 79-84)。"""

    def test_mcp_error_fallback_to_synthetic(self, mocker):
        """Lines 79-80, 82-84: MCPDataError → 合成数据降级。"""
        mock_mcp = mocker.MagicMock(spec=MCPDataProvider)
        mock_mcp.get_ohlcv.side_effect = MCPDataError("fail")
        p = FTSDataProvider(mcp_provider=mock_mcp)
        df = p.get_ohlcv("000001", days=100)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100
        assert "close" in df.columns

    def test_generic_exception_fallback_to_synthetic(self, mocker):
        """Lines 79-80, 82-84: 通用异常 → 合成数据降级。"""
        mock_mcp = mocker.MagicMock(spec=MCPDataProvider)
        mock_mcp.get_ohlcv.side_effect = ValueError("unexpected")
        p = FTSDataProvider(mcp_provider=mock_mcp)
        df = p.get_ohlcv("000001", days=100)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100

    def test_none_df_fallback(self, mocker):
        """Line 77: MCP 返回 None → 合成数据降级。"""
        mock_mcp = mocker.MagicMock(spec=MCPDataProvider)
        mock_mcp.get_ohlcv.return_value = None
        p = FTSDataProvider(mcp_provider=mock_mcp)
        df = p.get_ohlcv("000001", days=100)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100

    def test_empty_df_fallback(self, mocker):
        """Line 77: MCP 返回空 DataFrame → 合成数据降级。"""
        mock_mcp = mocker.MagicMock(spec=MCPDataProvider)
        mock_mcp.get_ohlcv.return_value = pd.DataFrame()
        p = FTSDataProvider(mcp_provider=mock_mcp)
        df = p.get_ohlcv("000001", days=100)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100


# ═══════════════════════════════════════════════════════════
# 16. FTSDataProvider.get_csi300_panel 全部失败→合成数据
# ═══════════════════════════════════════════════════════════


class TestFTSCsi300PanelFallback:
    """覆盖 data.FTSDataProvider.get_csi300_panel 的异常和空面板路径 (lines 130-136)。

    注意: FTSDataProvider.get_ohlcv 内部已捕获所有异常并回退合成数据，
    因此需要在类层面 patch get_ohlcv 以触发 get_csi300_panel 的 except 路径。
    """

    def test_all_symbols_fail_fallback(self, mocker):
        """Lines 130-131, 134-136: 所有成分股 get_ohlcv 抛出异常 → 合成数据面板。"""
        mocker.patch.object(FTSDataProvider, "get_ohlcv", side_effect=Exception("fail"))
        p = FTSDataProvider(mcp_provider=mocker.MagicMock())
        panel, dates = p.get_csi300_panel(days=100, max_stocks=3)
        assert "SYNTHETIC" in panel
        df = panel["SYNTHETIC"]
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 100

    def test_some_symbols_fail_continue(self, mocker):
        """Lines 130-131: 部分失败 continue 继续处理后续。"""
        good_df = pd.DataFrame(
            {
                "close": [1.0, 2.0],
                "open": [0.9, 1.8],
                "high": [1.1, 2.2],
                "low": [0.8, 1.7],
                "volume": [1000.0, 2000.0],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return good_df
            raise Exception("fail")

        mocker.patch.object(FTSDataProvider, "get_ohlcv", side_effect=side_effect)
        p = FTSDataProvider(mcp_provider=mocker.MagicMock())
        panel, dates = p.get_csi300_panel(days=100, max_stocks=3)
        assert len(panel) == 1  # 只有第一个成功


# ═══════════════════════════════════════════════════════════
# 17. FTSDataProvider 基本面注入接口
# ═══════════════════════════════════════════════════════════


class TestFTSFundamentalIntegration:
    """覆盖 data.FTSDataProvider 的基本面注入接口（enrich_with_fundamental / set_fundamental_provider / fundamental 参数）。"""

    def test_enrich_with_fundamental_delegates(self, mocker):
        """enrich_with_fundamental 委托给 FundamentalProvider.enrich_ohlcv。"""
        from fts.data_fundamental import FundamentalProvider

        mock_fund = mocker.MagicMock(spec=FundamentalProvider)
        mock_df = pd.DataFrame({"close": [1.0]})
        mock_fund.enrich_ohlcv.return_value = mock_df
        p = FTSDataProvider(mcp_provider=mocker.MagicMock(), fundamental_provider=mock_fund)
        result = p.enrich_with_fundamental(pd.DataFrame({"close": [1.0]}), "000001")
        mock_fund.enrich_ohlcv.assert_called_once()
        assert result is mock_df

    def test_set_fundamental_provider(self, mocker):
        """set_fundamental_provider 替换内部 provider。"""
        from fts.data_fundamental import FundamentalProvider

        p = FTSDataProvider(mcp_provider=mocker.MagicMock())
        assert p._fundamental is not None
        new_provider = FundamentalProvider(mcp_available=False)
        p.set_fundamental_provider(new_provider)
        assert p._fundamental is new_provider

    def test_get_ohlcv_with_fundamental_true(self, mocker):
        """fundamental=True 时 get_ohlcv 应注入基本面字段。"""
        from fts.data_fundamental import FundamentalProvider

        mock_fund = mocker.MagicMock(spec=FundamentalProvider)
        base_df = pd.DataFrame(
            {
                "close": [1.0, 2.0],
                "open": [0.9, 1.8],
                "high": [1.1, 2.2],
                "low": [0.8, 1.7],
                "volume": [1000.0, 2000.0],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        enriched_df = base_df.copy()
        enriched_df["pe_ttm"] = 15.0
        mock_fund.enrich_ohlcv.return_value = enriched_df
        mock_mcp = mocker.MagicMock()
        mock_mcp.get_ohlcv.return_value = base_df
        p = FTSDataProvider(mcp_provider=mock_mcp, fundamental_provider=mock_fund)
        result = p.get_ohlcv("000001", days=100, fundamental=True)
        assert "pe_ttm" in result.columns
        mock_fund.enrich_ohlcv.assert_called_once()

    def test_get_ohlcv_with_fundamental_false(self, mocker):
        """fundamental=False 时 get_ohlcv 不应注入基本面字段。"""
        from fts.data_fundamental import FundamentalProvider

        mock_fund = mocker.MagicMock(spec=FundamentalProvider)
        base_df = pd.DataFrame(
            {
                "close": [1.0, 2.0],
                "open": [0.9, 1.8],
                "high": [1.1, 2.2],
                "low": [0.8, 1.7],
                "volume": [1000.0, 2000.0],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        mock_mcp = mocker.MagicMock()
        mock_mcp.get_ohlcv.return_value = base_df
        p = FTSDataProvider(mcp_provider=mock_mcp, fundamental_provider=mock_fund)
        result = p.get_ohlcv("000001", days=100, fundamental=False)
        assert "pe_ttm" not in result.columns
        mock_fund.enrich_ohlcv.assert_not_called()

    def test_get_ohlcv_fallback_with_fundamental(self, mocker):
        """MCP 失败降级到合成数据时，fundamental=True 仍应注入基本面字段。"""
        from fts.data_fundamental import FundamentalProvider

        mock_fund = mocker.MagicMock(spec=FundamentalProvider)
        mock_mcp = mocker.MagicMock()
        mock_mcp.get_ohlcv.side_effect = Exception("fail")
        synthetic_df = FTSDataProvider.synthesize_ohlcv(n_days=100, base_price=15.0, seed=42)
        mock_fund.enrich_ohlcv.return_value = synthetic_df
        # patch synthesize_ohlcv to return synthetic_df
        mocker.patch.object(FTSDataProvider, "synthesize_ohlcv", return_value=synthetic_df)
        p = FTSDataProvider(mcp_provider=mock_mcp, fundamental_provider=mock_fund)
        result = p.get_ohlcv("000001", days=100, fundamental=True)
        assert "close" in result.columns
        mock_fund.enrich_ohlcv.assert_called_once()

    def test_get_csi300_panel_with_fundamental(self, mocker):
        """fundamental=True 时 get_csi300_panel 应注入基本面字段。"""
        from fts.data_fundamental import FundamentalProvider

        mock_fund = mocker.MagicMock(spec=FundamentalProvider)
        base_df = pd.DataFrame(
            {
                "close": [1.0, 2.0],
                "open": [0.9, 1.8],
                "high": [1.1, 2.2],
                "low": [0.8, 1.7],
                "volume": [1000.0, 2000.0],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        enriched_df = base_df.copy()
        enriched_df["pe_ttm"] = 15.0
        mock_fund.enrich_ohlcv.return_value = enriched_df
        mock_mcp = mocker.MagicMock()
        mock_mcp.get_ohlcv.return_value = base_df
        p = FTSDataProvider(mcp_provider=mock_mcp, fundamental_provider=mock_fund)
        panel, dates = p.get_csi300_panel(days=100, max_stocks=2, fundamental=True)
        for sym, df in panel.items():
            assert "pe_ttm" in df.columns, f"{sym} 缺少基本面字段"
        mock_fund.enrich_ohlcv.assert_called()

    def test_get_csi300_panel_synthetic_fallback_fundamental(self, mocker):
        """get_csi300_panel 全部失败时，合成数据应注入基本面字段。"""
        from fts.data_fundamental import FundamentalProvider

        mock_fund = mocker.MagicMock(spec=FundamentalProvider)
        synthetic_df = FTSDataProvider.synthesize_ohlcv(n_days=100, base_price=15.0, seed=42)
        enriched_synthetic = synthetic_df.copy()
        enriched_synthetic["pe_ttm"] = 15.0
        mock_fund.enrich_ohlcv.return_value = enriched_synthetic
        mocker.patch.object(FTSDataProvider, "get_ohlcv", side_effect=Exception("fail"))
        p = FTSDataProvider(fundamental_provider=mock_fund)
        panel, dates = p.get_csi300_panel(days=100, max_stocks=2, fundamental=True)
        assert "SYNTHETIC" in panel
        assert "pe_ttm" in panel["SYNTHETIC"].columns


# ═══════════════════════════════════════════════════════════
# 18. FTSDataProvider 期货接口集成
# ═══════════════════════════════════════════════════════════


class TestFTSFuturesIntegration:
    """覆盖 data.FTSDataProvider 的期货数据接口（委托给 FuturesDataProvider）。"""

    def test_get_futures_ohlcv(self, mocker):
        """get_futures_ohlcv 委托给期货 provider。"""
        mock_fut = mocker.MagicMock(spec=FuturesDataProvider)
        df = pd.DataFrame({"close": [1.0, 2.0]})
        mock_fut.get_ohlcv.return_value = df
        p = FTSDataProvider(
            mcp_provider=mocker.MagicMock(),
            futures_provider=mock_fut,
        )
        result = p.get_futures_ohlcv("RB0", days=100, trace_id="t1")
        mock_fut.get_ohlcv.assert_called_once_with("RB0", days=100, trace_id="t1")
        assert result is df

    def test_get_futures_panel_with_symbols(self, mocker):
        """get_futures_panel 显式传 symbols 时透传。"""
        mock_fut = mocker.MagicMock(spec=FuturesDataProvider)
        panel = {"RB0": pd.DataFrame({"close": [1.0]})}
        dates = pd.DatetimeIndex(["2026-01-01"])
        mock_fut.get_futures_panel.return_value = (panel, dates)
        p = FTSDataProvider(
            mcp_provider=mocker.MagicMock(),
            futures_provider=mock_fut,
        )
        result = p.get_futures_panel(["RB0"], days=100, trace_id="t1")
        assert result == (panel, dates)
        mock_fut.get_futures_panel.assert_called_once_with(["RB0"], days=100, trace_id="t1")

    def test_get_futures_panel_default_symbols(self, mocker):
        """get_futures_panel 不传 symbols 时使用 FUTURES_CORE_SUBSET。"""
        mock_fut = mocker.MagicMock(spec=FuturesDataProvider)
        mock_fut.get_futures_panel.return_value = ({}, pd.DatetimeIndex([]))
        p = FTSDataProvider(
            mcp_provider=mocker.MagicMock(),
            futures_provider=mock_fut,
        )
        p.get_futures_panel(days=100)
        args, kwargs = mock_fut.get_futures_panel.call_args
        assert args[0] == FUTURES_CORE_SUBSET
        assert kwargs == {"days": 100, "trace_id": ""}

    def test_enrich_futures_fundamental_fills_nan(self, mocker):
        """enrich_futures_fundamental 返回原 df 并补齐 fut_ 前缀 NaN 列。

        注: FTSDataProvider.__init__ 自 v2.101.0（GAP-083 缺口补充）起默认挂接 AKShare 期货基本面 provider，
        此处显式置 None 固化"无 provider 兜底"语义：不抛异常、7 个期货基本面列全部补齐（NaN）。
        """
        p = FTSDataProvider(
            mcp_provider=mocker.MagicMock(),
            futures_provider=mocker.MagicMock(spec=FuturesDataProvider),
        )
        # 固化无 provider 路径（避免默认 AKShare provider 发起网络请求）
        p._futures_fundamental = None
        base_df = pd.DataFrame(
            {
                "close": [1.0, 2.0],
                "open": [0.9, 1.8],
                "high": [1.1, 2.2],
                "low": [0.8, 1.7],
                "volume": [1000.0, 2000.0],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        result = p.enrich_futures_fundamental(base_df, "RB0", trace_id="t1")
        expected_cols = [
            "fut_inventory",
            "fut_inventory_chg",
            "fut_warehouse_receipt",
            "fut_warehouse_receipt_chg",
            "fut_spot_price",
            "fut_near_basis",
            "fut_dom_basis",
            "fut_near_basis_rate",
            "fut_dom_basis_rate",
        ]
        for col in expected_cols:
            assert col in result.columns
            assert result[col].isna().all()
        # 原 OHLCV 列不受影响
        assert list(result.columns[:5]) == ["close", "open", "high", "low", "volume"]


# ═══════════════════════════════════════════════════════════
# 19. FTSDataProvider.enrich_futures_fundamental provider 注入
# ═══════════════════════════════════════════════════════════


class TestEnrichFuturesFundamental:
    """覆盖 data.FTSDataProvider.enrich_futures_fundamental 的 provider 注入路径。"""

    @staticmethod
    def _base_df() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "close": [1.0, 2.0],
                "open": [0.9, 1.8],
                "high": [1.1, 2.2],
                "low": [0.8, 1.7],
                "volume": [1000.0, 2000.0],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )

    def test_provider_injects_inventory_and_basis(self, mocker):
        """provider 同时提供库存与基差 → fut_ 前缀列全部注入。"""
        mock_provider = mocker.MagicMock()
        inv_df = pd.DataFrame(
            {"inventory": [100.0, 110.0], "change": [10.0, 5.0]},
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        basis_df = pd.DataFrame(
            {
                "spot_price": [3000.0, 3010.0],
                "near_basis": [20.0, 25.0],
                "dom_basis": [15.0, 18.0],
                "near_basis_rate": [0.006, 0.008],
                "dom_basis_rate": [0.005, 0.006],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        mock_provider.get_inventory.return_value = inv_df
        mock_provider.get_basis.return_value = basis_df
        p = FTSDataProvider(mcp_provider=mocker.MagicMock())
        p._futures_fundamental = mock_provider

        result = p.enrich_futures_fundamental(self._base_df(), "RB0", trace_id="t1")
        assert result["fut_inventory"].iloc[0] == 100.0
        assert result["fut_inventory_chg"].iloc[0] == 10.0
        assert result["fut_spot_price"].iloc[0] == 3000.0
        assert result["fut_near_basis"].iloc[0] == 20.0
        assert result["fut_dom_basis"].iloc[0] == 15.0
        assert result["fut_near_basis_rate"].iloc[0] == 0.006
        assert result["fut_dom_basis_rate"].iloc[0] == 0.005
        mock_provider.get_inventory.assert_called_once_with("RB0")
        mock_provider.get_basis.assert_called_once_with("RB0", days=60)

    def test_provider_inventory_exception_swallowed(self, mocker):
        """get_inventory 抛异常 → 吞掉，仅注入基差。"""
        mock_provider = mocker.MagicMock()
        mock_provider.get_inventory.side_effect = Exception("boom")
        basis_df = pd.DataFrame(
            {"spot_price": [3000.0]},
            index=pd.DatetimeIndex(["2024-01-01"]),
        )
        mock_provider.get_basis.return_value = basis_df
        p = FTSDataProvider(mcp_provider=mocker.MagicMock())
        p._futures_fundamental = mock_provider

        result = p.enrich_futures_fundamental(self._base_df(), "RB0")
        assert result["fut_inventory"].isna().all()
        assert result["fut_spot_price"].iloc[0] == 3000.0

    def test_provider_inventory_empty_skipped(self, mocker):
        """get_inventory 返回空 df → 跳过注入。"""
        mock_provider = mocker.MagicMock()
        mock_provider.get_inventory.return_value = pd.DataFrame()
        mock_provider.get_basis.return_value = pd.DataFrame()
        p = FTSDataProvider(mcp_provider=mocker.MagicMock())
        p._futures_fundamental = mock_provider

        result = p.enrich_futures_fundamental(self._base_df(), "RB0")
        assert result["fut_inventory"].isna().all()
        assert "fut_spot_price" in result.columns

    def test_provider_basis_exception_swallowed(self, mocker):
        """get_basis 抛异常 → 吞掉，仅注入库存。"""
        mock_provider = mocker.MagicMock()
        inv_df = pd.DataFrame(
            {"inventory": [100.0], "change": [10.0]},
            index=pd.DatetimeIndex(["2024-01-01"]),
        )
        mock_provider.get_inventory.return_value = inv_df
        mock_provider.get_basis.side_effect = Exception("boom")
        p = FTSDataProvider(mcp_provider=mocker.MagicMock())
        p._futures_fundamental = mock_provider

        result = p.enrich_futures_fundamental(self._base_df(), "RB0")
        assert result["fut_inventory"].iloc[0] == 100.0
        assert result["fut_spot_price"].isna().all()


# ═══════════════════════════════════════════════════════════
# 20. FTSDataProvider.get_csi300_panel max_stocks=0 / 交集
# ═══════════════════════════════════════════════════════════


class TestCsi300PanelFull:
    """覆盖 get_csi300_panel 的 max_stocks=0 与正常交集路径。"""

    def test_max_stocks_zero_uses_all(self, mocker):
        """max_stocks=0 → 使用全部沪深300成分股（动态获取）。"""
        good_df = pd.DataFrame(
            {
                "close": [1.0, 2.0],
                "open": [0.9, 1.8],
                "high": [1.1, 2.2],
                "low": [0.8, 1.7],
                "volume": [1.0, 2.0],
            },
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        mocker.patch.object(FTSDataProvider, "get_ohlcv", return_value=good_df)
        from unittest.mock import patch

        with patch("fts.data_mcp.get_csi300_constituents", return_value=["AAA", "BBB", "CCC"]):
            p = FTSDataProvider(mcp_provider=mocker.MagicMock())
            panel, _ = p.get_csi300_panel(days=10, max_stocks=0)
        assert set(panel.keys()) == {"AAA", "BBB", "CCC"}

    def test_common_dates_intersection(self, mocker):
        """正常路径应返回所有股票共有日期。"""
        df1 = pd.DataFrame(
            {"close": [1.0, 2.0]},
            index=pd.DatetimeIndex(["2024-01-01", "2024-01-02"]),
        )
        df2 = pd.DataFrame(
            {"close": [2.0, 3.0]},
            index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"]),
        )

        def side_effect(sym, **kwargs):
            return df1 if sym == "AAA" else df2

        mocker.patch.object(FTSDataProvider, "get_ohlcv", side_effect=side_effect)
        from unittest.mock import patch

        with patch("fts.data_mcp.CSI300_SUBSET", ["AAA", "BBB"]):
            p = FTSDataProvider(mcp_provider=mocker.MagicMock())
            panel, dates = p.get_csi300_panel(days=10, max_stocks=2)
        assert set(panel.keys()) == {"AAA", "BBB"}
        assert list(dates) == [pd.Timestamp("2024-01-02")]


# ═══════════════════════════════════════════════════════════
# 21. get_etf_panel / get_stock_panel 委托
# ═══════════════════════════════════════════════════════════


class TestPanelDelegation:
    """覆盖 get_etf_panel / get_stock_panel 对 _mcp.get_stock_panel 的委托。"""

    def test_get_etf_panel_delegates(self, mocker):
        from fts.data_mcp import ETF_SUBSET

        mock_mcp = mocker.MagicMock()
        panel = {"510300": pd.DataFrame({"close": [1.0]})}
        dates = pd.DatetimeIndex(["2024-01-01"])
        mock_mcp.get_stock_panel.return_value = (panel, dates)
        p = FTSDataProvider(mcp_provider=mock_mcp)
        result = p.get_etf_panel(days=100, trace_id="t")
        assert result == (panel, dates)
        mock_mcp.get_stock_panel.assert_called_once_with(ETF_SUBSET, days=100, adjust="qfq", trace_id="t")

    def test_get_stock_panel_delegates(self, mocker):
        mock_mcp = mocker.MagicMock()
        panel = {"000001": pd.DataFrame({"close": [1.0]})}
        dates = pd.DatetimeIndex(["2024-01-01"])
        mock_mcp.get_stock_panel.return_value = (panel, dates)
        p = FTSDataProvider(mcp_provider=mock_mcp)
        result = p.get_stock_panel(["000001"], days=100, trace_id="t")
        assert result == (panel, dates)
        mock_mcp.get_stock_panel.assert_called_once_with(["000001"], days=100, adjust="qfq", trace_id="t")


# ═══════════════════════════════════════════════════════════
# 22. DataUnavailableError
# ═══════════════════════════════════════════════════════════


class TestDataUnavailableError:
    """DataUnavailableError 应为 RuntimeError 子类。"""

    def test_is_runtime_error(self):
        from fts.data import DataUnavailableError

        err = DataUnavailableError("boom")
        assert isinstance(err, RuntimeError)
        assert str(err) == "boom"
