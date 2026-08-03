"""
tests/test_data_fundamental.py — FundamentalProvider 测试。

覆盖目标:
  1. FundamentalProvider.__init__
  2. enrich_ohlcv: MCP 路径 / 合成降级 / 空 DataFrame
  3. enrich_panel: 批量注入
  4. _synthetic_enrich: 输出字段验证
  5. _to_westock_code: 各种代码格式
  6. _get_market: 市场编号
  7. get_fundamental_provider: 全局单例
  8. FundamentalDataError / FUNDAMENTAL_FIELDS 常量
  9. FTSDataProvider.enrich_with_fundamental / set_fundamental_provider
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.data_fundamental import (
    FUNDAMENTAL_FIELDS,
    FundamentalDataError,
    FundamentalProvider,
    VALUATION_FIELDS,
    QUALITY_FIELDS,
    GROWTH_FIELDS,
    MACRO_FIELDS,
    get_fundamental_provider,
    _to_westock_code,
    _get_market,
)


# ═══════════════════════════════════════════════════════════
# 1. 常量验证
# ═══════════════════════════════════════════════════════════

class TestConstants:
    def test_valuation_fields_not_empty(self):
        assert len(VALUATION_FIELDS) > 0

    def test_quality_fields_not_empty(self):
        assert len(QUALITY_FIELDS) > 0

    def test_growth_fields_not_empty(self):
        assert len(GROWTH_FIELDS) > 0

    def test_macro_fields_not_empty(self):
        assert len(MACRO_FIELDS) > 0

    def test_fundamental_fields_contains_all(self):
        """FUNDAMENTAL_FIELDS 应包含所有子类字段。"""
        for f in VALUATION_FIELDS + QUALITY_FIELDS + GROWTH_FIELDS + MACRO_FIELDS:
            assert f in FUNDAMENTAL_FIELDS

    def test_fundamental_fields_sorted(self):
        """FUNDAMENTAL_FIELDS 应排序。"""
        assert FUNDAMENTAL_FIELDS == sorted(set(FUNDAMENTAL_FIELDS))

    def test_fundamental_fields_no_duplicates(self):
        assert len(FUNDAMENTAL_FIELDS) == len(set(FUNDAMENTAL_FIELDS))


# ═══════════════════════════════════════════════════════════
# 2. FundamentalDataError
# ═══════════════════════════════════════════════════════════

class TestFundamentalDataError:
    def test_is_runtime_error(self):
        assert issubclass(FundamentalDataError, RuntimeError)

    def test_can_raise_with_message(self):
        with pytest.raises(FundamentalDataError, match="test error"):
            raise FundamentalDataError("test error")


# ═══════════════════════════════════════════════════════════
# 3. FundamentalProvider.__init__
# ═══════════════════════════════════════════════════════════

class TestInit:
    def test_mcp_available_default(self):
        p = FundamentalProvider()
        assert p._mcp_available is True

    def test_mcp_available_false(self):
        p = FundamentalProvider(mcp_available=False)
        assert p._mcp_available is False

    def test_mcp_available_true(self):
        p = FundamentalProvider(mcp_available=True)
        assert p._mcp_available is True

    def test_cache_empty_on_init(self):
        p = FundamentalProvider()
        assert p._bridge is None
        assert p._macro_cache == {}


# ═══════════════════════════════════════════════════════════
# 4. enrich_ohlcv — 合成数据降级路径
# ═══════════════════════════════════════════════════════════

class TestEnrichOhlcvSynthetic:
    """mcp_available=False 时使用合成数据。"""

    def _make_ohlcv(self, n: int = 100) -> pd.DataFrame:
        return pd.DataFrame({
            "close": np.random.randn(n) + 15.0,
            "open": np.random.randn(n) + 15.0,
            "high": np.random.randn(n) + 15.5,
            "low": np.random.randn(n) + 14.5,
            "volume": np.random.randint(1000, 10000, n).astype(float),
        })

    def test_returns_dataframe(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(50)
        result = p.enrich_ohlcv(df, "000001")
        assert isinstance(result, pd.DataFrame)

    def test_contains_fundamental_columns(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(50)
        result = p.enrich_ohlcv(df, "000001")
        assert "pe_ttm" in result.columns
        assert "pb" in result.columns
        assert "roe" in result.columns
        assert "revenue_growth" in result.columns

    def test_empty_df_returns_empty(self):
        p = FundamentalProvider(mcp_available=False)
        empty = pd.DataFrame()
        result = p.enrich_ohlcv(empty, "000001")
        assert result.empty

    def test_synthetic_pe_ttm_range(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(100)
        result = p.enrich_ohlcv(df, "000001")
        assert result["pe_ttm"].between(0, 100).all()

    def test_synthetic_roe_range(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(100)
        result = p.enrich_ohlcv(df, "000001")
        assert result["roe"].between(0, 0.5).all()

    def test_reproducible_with_seed(self):
        p1 = FundamentalProvider(mcp_available=False)
        p2 = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(50)
        r1 = p1.enrich_ohlcv(df.copy(), "000001")
        r2 = p2.enrich_ohlcv(df.copy(), "000001")
        pd.testing.assert_frame_equal(r1, r2)

    def test_preserves_original_columns(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(50)
        result = p.enrich_ohlcv(df, "000001")
        assert "close" in result.columns
        assert "volume" in result.columns
        assert len(result) == 50


# ═══════════════════════════════════════════════════════════
# 5. enrich_panel — 批量注入
# ═══════════════════════════════════════════════════════════

class TestEnrichPanel:
    def _make_ohlcv(self, n: int = 50) -> pd.DataFrame:
        return pd.DataFrame({
            "close": np.random.randn(n) + 15.0,
            "open": np.random.randn(n) + 15.0,
            "high": np.random.randn(n) + 15.5,
            "low": np.random.randn(n) + 14.5,
            "volume": np.random.randint(1000, 10000, n).astype(float),
        })

    def test_returns_dict(self):
        p = FundamentalProvider(mcp_available=False)
        panel = {"000001": self._make_ohlcv(50), "000002": self._make_ohlcv(50)}
        result = p.enrich_panel(panel)
        assert isinstance(result, dict)
        assert len(result) == 2

    def test_each_has_fundamental_columns(self):
        p = FundamentalProvider(mcp_available=False)
        panel = {"000001": self._make_ohlcv(50), "000002": self._make_ohlcv(50)}
        result = p.enrich_panel(panel)
        for sym, df in result.items():
            assert "pe_ttm" in df.columns
            assert "roe" in df.columns

    def test_synthetic_key_handling(self):
        p = FundamentalProvider(mcp_available=False)
        panel = {"SYNTHETIC": self._make_ohlcv(50)}
        result = p.enrich_panel(panel)
        assert "SYNTHETIC" in result
        assert "pe_ttm" in result["SYNTHETIC"].columns

    def test_empty_panel(self):
        p = FundamentalProvider(mcp_available=False)
        result = p.enrich_panel({})
        assert result == {}


# ═══════════════════════════════════════════════════════════
# 6. _synthetic_enrich — 输出字段验证
# ═══════════════════════════════════════════════════════════

class TestSyntheticEnrich:
    def _make_ohlcv(self, n: int = 100) -> pd.DataFrame:
        return pd.DataFrame({
            "close": np.random.randn(n) + 15.0,
            "open": np.random.randn(n) + 15.0,
            "high": np.random.randn(n) + 15.5,
            "low": np.random.randn(n) + 14.5,
            "volume": np.random.randint(1000, 10000, n).astype(float),
        })

    def test_all_expected_fields_present(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(50)
        result = p._synthetic_enrich(df)
        expected = {"pe_ttm", "pb", "ps_ttm", "total_market_cap",
                     "free_market_cap", "turnover_rate", "roe", "roa",
                     "gross_margin", "net_margin", "eps",
                     "revenue_growth", "profit_growth", "pmi", "cpi"}
        for field in expected:
            assert field in result.columns, f"缺少字段: {field}"

    def test_same_length_as_input(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(100)
        result = p._synthetic_enrich(df)
        assert len(result) == 100

    def test_positive_total_market_cap(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(50)
        result = p._synthetic_enrich(df)
        assert (result["total_market_cap"] > 0).all()

    def test_pmi_is_constant(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(50)
        result = p._synthetic_enrich(df)
        assert (result["pmi"] == 50.5).all()

    def test_cpi_is_constant(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_ohlcv(50)
        result = p._synthetic_enrich(df)
        assert (result["cpi"] == 0.5).all()


# ═══════════════════════════════════════════════════════════
# 7. enrich_ohlcv — MCP 路径（mock 模拟）
# ═══════════════════════════════════════════════════════════

class TestEnrichOhlcvMCP:
    def _make_ohlcv(self, n: int = 50) -> pd.DataFrame:
        return pd.DataFrame({
            "close": np.random.randn(n) + 15.0,
            "open": np.random.randn(n) + 15.0,
            "high": np.random.randn(n) + 15.5,
            "low": np.random.randn(n) + 14.5,
            "volume": np.random.randint(1000, 10000, n).astype(float),
        })

    def test_mcp_error_falls_back_to_synthetic(self, mocker):
        """MCP 异常时降级到合成数据。"""
        p = FundamentalProvider(mcp_available=True)
        mocker.patch.object(p, "_mcp_enrich", side_effect=FundamentalDataError("fail"))
        df = self._make_ohlcv(50)
        result = p.enrich_ohlcv(df, "000001")
        assert "pe_ttm" in result.columns
        assert "roe" in result.columns

    def test_mcp_generic_exception_falls_back(self, mocker):
        """MCP 通用异常时降级到合成数据。"""
        p = FundamentalProvider(mcp_available=True)
        mocker.patch.object(p, "_mcp_enrich", side_effect=ValueError("unexpected"))
        df = self._make_ohlcv(50)
        result = p.enrich_ohlcv(df, "000001")
        assert "pe_ttm" in result.columns

    def test_mcp_success_returns_enriched(self, mocker):
        """MCP 成功时返回注入后的 DataFrame。"""
        p = FundamentalProvider(mcp_available=True)
        enriched = self._make_ohlcv(50)
        enriched["pe_ttm"] = 15.0
        mocker.patch.object(p, "_mcp_enrich", return_value=enriched)
        df = self._make_ohlcv(50)
        result = p.enrich_ohlcv(df, "000001")
        assert "pe_ttm" in result.columns
        assert result["pe_ttm"].iloc[0] == 15.0

    def test_mcp_enrich_profile_applied(self, mocker):
        """_mcp_enrich 应通过 _get_bridge() 获取数据并注入字段。"""
        p = FundamentalProvider(mcp_available=True)
        df = self._make_ohlcv(50)
        mock_bridge = mocker.MagicMock()
        mock_bridge.get_fundamental.return_value = {"pe_ttm": 12.5, "pb": 1.5, "roe": 0.15}
        mocker.patch.object(p, "_get_bridge", return_value=mock_bridge)
        mocker.patch.object(p, "_fetch_macro", return_value={"pmi": 51.0})
        result = p._mcp_enrich(df, "000001", "")
        assert result["pe_ttm"].iloc[0] == 12.5
        assert result["pb"].iloc[0] == 1.5
        assert result["roe"].iloc[0] == 0.15
        assert result["pmi"].iloc[0] == 51.0

    def test_mcp_enrich_empty_profile(self, mocker):
        """_get_bridge() 返回空时不应报错。"""
        p = FundamentalProvider(mcp_available=True)
        df = self._make_ohlcv(50)
        mock_bridge = mocker.MagicMock()
        mock_bridge.get_fundamental.return_value = {}
        mocker.patch.object(p, "_get_bridge", return_value=mock_bridge)
        mocker.patch.object(p, "_fetch_macro", return_value={})
        result = p._mcp_enrich(df, "000001", "")
        assert "close" in result.columns
        assert len(result) == 50


# ═══════════════════════════════════════════════════════════
# 8. _to_westock_code
# ═══════════════════════════════════════════════════════════

class TestToWestockCode:
    def test_sz_prefix(self):
        assert _to_westock_code("000001") == "SZ000001"

    def test_sh_prefix(self):
        assert _to_westock_code("600519") == "SH600519"

    def test_already_sh(self):
        assert _to_westock_code("sh600519") == "SH600519"

    def test_already_sz(self):
        assert _to_westock_code("sz000001") == "SZ000001"

    def test_already_sh_upper(self):
        assert _to_westock_code("SH600519") == "SH600519"

    def test_9_prefix_is_sh(self):
        assert _to_westock_code("900001") == "SH900001"

    def test_hk_prefix_preserved(self):
        assert _to_westock_code("HK00700") == "HK00700"

    def test_bj_prefix_preserved(self):
        assert _to_westock_code("BJ430047") == "BJ430047"

    def test_empty_string(self):
        assert _to_westock_code("") == "SZ"


# ═══════════════════════════════════════════════════════════
# 9. _get_market
# ═══════════════════════════════════════════════════════════

class TestGetMarket:
    def test_sh_from_six_prefix(self):
        assert _get_market("600519") == "1"

    def test_sh_from_sh_prefix(self):
        assert _get_market("SH600519") == "1"

    def test_sz_from_zero_prefix(self):
        assert _get_market("000001") == "0"

    def test_sz_from_three_prefix(self):
        assert _get_market("300001") == "0"

    def test_sz_from_sz_prefix(self):
        assert _get_market("SZ000001") == "0"

    def test_default_for_unknown(self):
        assert _get_market("") == "1"


# ═══════════════════════════════════════════════════════════
# 10. get_fundamental_provider
# ═══════════════════════════════════════════════════════════

class TestGetFundamentalProvider:
    def test_returns_fundamental_provider(self):
        p = get_fundamental_provider()
        assert isinstance(p, FundamentalProvider)

    def test_singleton_same_instance(self):
        p1 = get_fundamental_provider()
        p2 = get_fundamental_provider()
        assert p1 is p2

    def test_mcp_available_param(self):
        import fts.data_fundamental as _df
        _df._default_fundamental_provider = None
        p = get_fundamental_provider(mcp_available=False)
        assert p._mcp_available is False

    def test_reset_between_tests(self):
        import fts.data_fundamental as _df
        _df._default_fundamental_provider = None
        p = get_fundamental_provider()
        assert _df._default_fundamental_provider is p


# ═══════════════════════════════════════════════════════════
# 11. _parse_profile
# ═══════════════════════════════════════════════════════════

class TestParseProfile:
    def test_parse_valid_data(self):
        """_parse_profile 已弃用，返回空 dict。"""
        p = FundamentalProvider(mcp_available=False)
        data = {"pe_ttm": 15.0, "pb": 2.5, "total_market_cap": "1000000000"}
        result = p._parse_profile(data)
        assert result == {}

    def test_parse_non_dict_data(self):
        p = FundamentalProvider(mcp_available=False)
        result = p._parse_profile([1, 2, 3])
        assert result == {}

    def test_parse_skip_non_numeric(self):
        p = FundamentalProvider(mcp_available=False)
        data = {"pe_ttm": "N/A", "pb": None}
        result = p._parse_profile(data)
        assert "pe_ttm" not in result
        assert "pb" not in result

    def test_parse_skip_missing_key(self):
        p = FundamentalProvider(mcp_available=False)
        data = {"pe_ttm": 15.0}
        result = p._parse_profile(data)
        assert "pb" not in result


# ═══════════════════════════════════════════════════════════
# 12. _apply_profile / _apply_finance / _apply_macro
# ═══════════════════════════════════════════════════════════

class TestApplyMethods:
    def _make_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "close": np.random.randn(10) + 15.0,
            "volume": np.random.randint(1000, 10000, 10).astype(float),
        })

    def test_apply_profile_sets_columns(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_df()
        p._apply_profile(df, {"pe_ttm": 12.0, "pb": 1.5})
        assert df["pe_ttm"].iloc[0] == 12.0
        assert df["pb"].iloc[0] == 1.5
        assert (df["pe_ttm"] == 12.0).all()

    def test_apply_finance_sets_columns(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_df()
        p._apply_finance(df, {"roe": 0.15, "eps": 2.0})
        assert df["roe"].iloc[0] == 0.15
        assert df["eps"].iloc[0] == 2.0

    def test_apply_macro_sets_columns(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_df()
        p._apply_macro(df, {"pmi": 51.5, "cpi": 0.8})
        assert df["pmi"].iloc[0] == 51.5
        assert df["cpi"].iloc[0] == 0.8

    def test_apply_profile_skips_missing(self):
        p = FundamentalProvider(mcp_available=False)
        df = self._make_df()
        p._apply_profile(df, {"pe_ttm": 12.0})
        assert "pe_ttm" in df.columns
        assert "pb" not in df.columns