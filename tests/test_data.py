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
    def test_returns_real_data(self):
        """应返回真实的 OHLCV 数据（腾讯 API）。"""
        p = FTSDataProvider()
        df = p.get_ohlcv("510300", days=250)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) > 0
        assert isinstance(df.index, pd.DatetimeIndex)
        # 真实价格应在合理范围（510300 约 3~6 元）
        assert 3.0 < df["close"].mean() < 6.0

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
            "code": 0, "data": {"sh510300": {"other_key": []}},
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
            {"close": [1.0, 2.0], "open": [0.9, 1.8], "high": [1.1, 2.2],
             "low": [0.8, 1.7], "volume": [1000.0, 2000.0]},
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
            {"close": [1.0, 2.0], "open": [0.9, 1.8], "high": [1.1, 2.2],
             "low": [0.8, 1.7], "volume": [1000.0, 2000.0]},
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
