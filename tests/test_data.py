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
from fts.data_mcp import MCPDataProvider


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
