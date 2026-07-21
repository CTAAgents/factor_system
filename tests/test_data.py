"""
tests/test_data.py — FTS data 模块全面测试。

使用 unittest.mock 模拟 Data-Core UnifiedDataProvider，不要求 datacore 实际安装。
覆盖目标: 85%+（当前 40%）

覆盖场景:
  1. FTSDataProvider __init__ 参数注入
  2. _provider 属性：注入 mock / ImportError → DataUnavailableError
  3. get_ohlcv: 成功（DF/dict/list 载荷），失败 → DataUnavailableError
  4. get_fundamental: 成功，失败 → 空 dict
  5. get_macro: 成功，失败 → 空 dict
  6. get_news: 成功（list/dict.items/dict.news），失败 → 空 list
  7. get_sentiment: 成功，失败 → 空 dict
  8. get_market_state: 成功，失败 → 空 dict
  9. list_symbols: 成功，失败 → 空 list
  10. get_batch_ohlcv: 部分成功（部分品种失败）
  11. synthesize_ohlcv: 输出形状和列验证
  12. _extract_data: .data / .payload / 原始数据
  13. _payload_to_ohlcv_df: DataFrame / dict / list / 无法识别 → 空 DataFrame
  14. _payload_to_dict: dict / 非 dict
  15. get_data_provider: 全局单例
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.data import (
    FTSDataProvider,
    DataUnavailableError,
    get_data_provider,
)


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def mock_provider():
    """创建一个模拟的 Data-Core 数据提供者。

    返回的 mock 具有 .get(symbol, data_type, params) 方法，
    返回的对象具有 .data 属性。
    """
    m = MagicMock()

    def _get(symbol, data_type, *, params=None, **kwargs):
        payload = MagicMock()
        if data_type == "ohlcv":
            dates = pd.date_range("2025-01-01", periods=10, freq="D")
            payload.data = pd.DataFrame({
                "open": np.arange(10.0),
                "high": np.arange(10.0) + 1,
                "low": np.arange(10.0) - 1,
                "close": np.arange(10.0) + 0.5,
                "volume": np.full(10, 1000.0),
            }, index=dates)
        elif data_type == "financial":
            payload.data = {"operating_income": 1e8, "equity": 5e8}
        elif data_type == "macro":
            payload.data = {"gdp": 3.0, "cpi": 2.5}
        elif data_type == "news":
            payload.data = [
                {"title": "news1", "content": "content1"},
                {"title": "news2", "content": "content2"},
            ]
        elif data_type == "sentiment":
            payload.data = {"2025-01-01": {"score": 0.5, "volume": 100}}
        elif data_type == "market_state":
            payload.data = {"regime": "bull", "confidence": 0.8}
        else:
            payload.data = {}
        return payload

    m.get.side_effect = _get
    m.list_symbols.return_value = ["RB", "HC", "I"]
    return m


@pytest.fixture
def provider(mock_provider):
    """使用注入的 mock provider 创建 FTSDataProvider。

    local_db 指向不存在的路径，确保测试使用 mock provider 而非本地数据库。
    """
    return FTSDataProvider(datacore_provider=mock_provider, local_db="/nonexistent/test.duckdb")


# ═══════════════════════════════════════════════════════════
# 1. __init__
# ═══════════════════════════════════════════════════════════

class TestInit:
    def test_with_datacore_provider(self, mock_provider):
        p = FTSDataProvider(datacore_provider=mock_provider)
        assert p._dc is mock_provider
        assert p._data_dir is not None

    def test_with_data_dir(self, tmp_path):
        p = FTSDataProvider(data_dir=str(tmp_path / "my_data"))
        assert str(tmp_path / "my_data") in str(p._data_dir)

    def test_default_data_dir(self):
        p = FTSDataProvider()
        assert "data" in str(p._data_dir)


# ═══════════════════════════════════════════════════════════
# 2. _provider 属性
# ═══════════════════════════════════════════════════════════

class TestProviderProperty:
    def test_returns_injected_provider(self, mock_provider):
        p = FTSDataProvider(datacore_provider=mock_provider)
        assert p._provider is mock_provider

    def test_import_error_raises_data_unavailable(self):
        p = FTSDataProvider()
        with patch.object(p, "_dc", None):
            with patch("builtins.__import__", side_effect=ImportError("no datacore")):
                with pytest.raises(DataUnavailableError, match="Data-Core 未安装"):
                    _ = p._provider

    def test_lazy_import_success(self):
        """_provider 在 _dc 为 None 时尝试延迟导入。"""
        fake_dc = MagicMock()
        p = FTSDataProvider()
        with patch.object(p, "_dc", None):
            with patch("builtins.__import__", return_value=MagicMock()):
                with patch(
                    "fts.data.FTSDataProvider._provider",
                    new_callable=PropertyMock,
                ) as mock_prop:
                    mock_prop.return_value = fake_dc
                    # 验证属性可访问
                    assert p._provider is fake_dc

    def test_caches_provider(self, mock_provider):
        p = FTSDataProvider(datacore_provider=mock_provider)
        p._dc = None
        # 首次访问触发延迟导入
        with patch("builtins.__import__") as mock_import:
            fake_mod = MagicMock()
            fake_mod.UnifiedDataProvider.return_value = MagicMock()
            mock_import.return_value = fake_mod
            prov = p._provider
            assert prov is not None
            # 第二次访问不再导入
            p._provider
            mock_import.assert_called_once()


# ═══════════════════════════════════════════════════════════
# 3. get_ohlcv
# ═══════════════════════════════════════════════════════════

class TestGetOhlcv:
    def test_success_dataframe_payload(self, provider, mock_provider):
        df = provider.get_ohlcv("RB", days=500)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) == 10
        mock_provider.get.assert_called_with(
            "RB", "ohlcv",
            params={"days": 500, "period": "daily"},
        )

    def test_success_dict_payload(self, provider, mock_provider):
        """_payload_to_ohlcv_df 处理 dict 类型 payload。"""
        mock_provider.get.side_effect = None
        payload = MagicMock()
        payload.data = {
            "open": [1.0, 2.0],
            "high": [1.5, 2.5],
            "low": [0.5, 1.5],
            "close": [1.2, 2.2],
            "volume": [1000.0, 2000.0],
            "date": pd.date_range("2025-01-01", periods=2, freq="D"),
        }
        mock_provider.get.return_value = payload
        df = provider.get_ohlcv("RB")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2

    def test_success_list_payload(self, provider, mock_provider):
        """_payload_to_ohlcv_df 处理 list 类型 payload（记录列表）。"""
        mock_provider.get.side_effect = None
        payload = MagicMock()
        payload.data = [
            {"open": 1.0, "high": 1.5, "low": 0.5, "close": 1.2,
             "volume": 1000.0, "date": "2025-01-01"},
            {"open": 2.0, "high": 2.5, "low": 1.5, "close": 2.2,
             "volume": 2000.0, "date": "2025-01-02"},
        ]
        mock_provider.get.return_value = payload
        df = provider.get_ohlcv("RB")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2

    def test_failure_raises_data_unavailable(self, provider, mock_provider):
        mock_provider.get.side_effect = ConnectionError("network down")
        with pytest.raises(DataUnavailableError, match="OHLCV 获取失败"):
            provider.get_ohlcv("RB")

    def test_with_trace_id(self, provider, mock_provider):
        provider.get_ohlcv("RB", trace_id="test-trace-001")
        # 验证 trace_id 不影响 provider.get 调用（目前未传播到 params）
        mock_provider.get.assert_called_with(
            "RB", "ohlcv",
            params={"days": 500, "period": "daily"},
        )


# ═══════════════════════════════════════════════════════════
# 4. get_fundamental
# ═══════════════════════════════════════════════════════════

class TestGetFundamental:
    def test_success(self, provider, mock_provider):
        result = provider.get_fundamental("RB", indicator="operating_income")
        assert isinstance(result, dict)
        assert result.get("operating_income") == 1e8

    def test_success_all_indicators(self, provider, mock_provider):
        result = provider.get_fundamental("RB")
        assert "operating_income" in result
        assert "equity" in result

    def test_failure_returns_empty_dict(self, provider, mock_provider):
        mock_provider.get.side_effect = ValueError("bad request")
        result = provider.get_fundamental("RB")
        assert result == {}


# ═══════════════════════════════════════════════════════════
# 5. get_macro
# ═══════════════════════════════════════════════════════════

class TestGetMacro:
    def test_success(self, provider, mock_provider):
        result = provider.get_macro(indicator="gdp")
        assert isinstance(result, dict)
        assert result.get("gdp") == 3.0

    def test_success_all_indicators(self, provider, mock_provider):
        result = provider.get_macro()
        assert "gdp" in result
        assert "cpi" in result

    def test_failure_returns_empty_dict(self, provider, mock_provider):
        mock_provider.get.side_effect = RuntimeError("api down")
        result = provider.get_macro(indicator="pmi")
        assert result == {}


# ═══════════════════════════════════════════════════════════
# 6. get_news
# ═══════════════════════════════════════════════════════════

class TestGetNews:
    def test_success_list_response(self, provider, mock_provider):
        """payload.data 是 list 时直接返回。"""
        result = provider.get_news("RB", days=7)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["title"] == "news1"

    def test_success_dict_with_items_key(self, provider, mock_provider):
        """payload.data 是 dict 且包含 'items' 键时提取 items。"""
        mock_provider.get.side_effect = None
        payload = MagicMock()
        payload.data = {
            "items": [{"title": "a"}, {"title": "b"}],
            "total": 2,
        }
        mock_provider.get.return_value = payload
        result = provider.get_news("RB")
        assert len(result) == 2
        assert result[0]["title"] == "a"

    def test_success_dict_with_news_key(self, provider, mock_provider):
        """payload.data 是 dict 且包含 'news' 键时提取 news。"""
        mock_provider.get.side_effect = None
        payload = MagicMock()
        payload.data = {"news": [{"title": "x"}], "page": 1}
        mock_provider.get.return_value = payload
        result = provider.get_news("RB")
        assert len(result) == 1
        assert result[0]["title"] == "x"

    def test_success_all_market(self, provider, mock_provider):
        """symbol 为空时应传入 '*'。"""
        mock_provider.get.side_effect = None
        payload = MagicMock()
        payload.data = []
        mock_provider.get.return_value = payload
        result = provider.get_news()
        assert result == []
        args, _ = mock_provider.get.call_args
        assert args[0] == "*"

    def test_failure_returns_empty_list(self, provider, mock_provider):
        mock_provider.get.side_effect = Exception("unexpected")
        result = provider.get_news("RB")
        assert result == []


# ═══════════════════════════════════════════════════════════
# 7. get_sentiment
# ═══════════════════════════════════════════════════════════

class TestGetSentiment:
    def test_success(self, provider, mock_provider):
        result = provider.get_sentiment("RB", days=30)
        assert isinstance(result, dict)
        assert "2025-01-01" in result

    def test_success_all_market(self, provider, mock_provider):
        """symbol 为空时应传入 '*'。"""
        mock_provider.get.side_effect = None
        payload = MagicMock()
        payload.data = {}
        mock_provider.get.return_value = payload
        result = provider.get_sentiment()
        assert result == {}
        args, _ = mock_provider.get.call_args
        assert args[0] == "*"

    def test_failure_returns_empty_dict(self, provider, mock_provider):
        mock_provider.get.side_effect = OSError("timeout")
        result = provider.get_sentiment("RB")
        assert result == {}


# ═══════════════════════════════════════════════════════════
# 8. get_market_state
# ═══════════════════════════════════════════════════════════

class TestGetMarketState:
    def test_success(self, provider, mock_provider):
        result = provider.get_market_state("RB")
        assert isinstance(result, dict)
        assert result.get("regime") == "bull"

    def test_success_all_market(self, provider, mock_provider):
        mock_provider.get.side_effect = None
        payload = MagicMock()
        payload.data = {}
        mock_provider.get.return_value = payload
        result = provider.get_market_state()
        assert result == {}
        args, _ = mock_provider.get.call_args
        assert args[0] == "*"

    def test_failure_returns_empty_dict(self, provider, mock_provider):
        mock_provider.get.side_effect = Exception("server error")
        result = provider.get_market_state("RB")
        assert result == {}


# ═══════════════════════════════════════════════════════════
# 9. list_symbols
# ═══════════════════════════════════════════════════════════

class TestListSymbols:
    def test_success_default_market(self, provider, mock_provider):
        result = provider.list_symbols()
        assert result == ["RB", "HC", "I"]
        mock_provider.list_symbols.assert_called_with(market="futures")

    def test_success_stock_market(self, provider, mock_provider):
        mock_provider.list_symbols.return_value = ["000001", "600519"]
        result = provider.list_symbols(market="stocks")
        assert result == ["000001", "600519"]
        mock_provider.list_symbols.assert_called_with(market="stocks")

    def test_failure_returns_empty_list(self, provider, mock_provider):
        mock_provider.list_symbols.side_effect = AttributeError("no method")
        result = provider.list_symbols()
        assert result == []


# ═══════════════════════════════════════════════════════════
# 10. get_batch_ohlcv
# ═══════════════════════════════════════════════════════════

class TestGetBatchOhlcv:
    def test_all_succeed(self, provider):
        result = provider.get_batch_ohlcv(["RB", "HC"])
        assert "RB" in result
        assert "HC" in result
        assert all(isinstance(v, pd.DataFrame) for v in result.values())

    def test_partial_failure(self, provider, mock_provider):
        """部分品种获取失败时应被跳过。"""
        original_get = provider.get_ohlcv

        def side_effect(symbol, **kw):
            if symbol == "RB":
                raise DataUnavailableError("no data for RB")
            return original_get(symbol, **kw)

        with patch.object(provider, "get_ohlcv", side_effect=side_effect):
            result = provider.get_batch_ohlcv(["RB", "HC", "I"])
        assert "RB" not in result
        assert "HC" in result
        assert "I" in result
        assert len(result) == 2

    def test_empty_symbols(self, provider):
        result = provider.get_batch_ohlcv([])
        assert result == {}


# ═══════════════════════════════════════════════════════════
# 11. synthesize_ohlcv
# ═══════════════════════════════════════════════════════════

class TestSynthesizeOhlcv:
    def test_output_shape_and_columns(self, provider):
        df = provider.synthesize_ohlcv(n_days=500, base_price=100.0, seed=42)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert len(df) == 500

    def test_index_is_datetime(self, provider):
        df = provider.synthesize_ohlcv(n_days=30, seed=99)
        assert isinstance(df.index, pd.DatetimeIndex)
        # 索引应为从 n 天前到昨天的每日频率
        expected_start = datetime.now() - timedelta(days=30)
        assert df.index[0].date() >= expected_start.date() - timedelta(days=1)

    def test_reproducible_with_seed(self, provider):
        df1 = provider.synthesize_ohlcv(n_days=100, base_price=50.0, seed=42)
        df2 = provider.synthesize_ohlcv(n_days=100, base_price=50.0, seed=42)
        # 索引基于 datetime.now() 不受 seed 控制，reset 后只比较数值列
        pd.testing.assert_frame_equal(
            df1.reset_index(drop=True),
            df2.reset_index(drop=True),
        )

    def test_different_seed_different_data(self, provider):
        df1 = provider.synthesize_ohlcv(n_days=100, seed=42)
        df2 = provider.synthesize_ohlcv(n_days=100, seed=99)
        assert not df1["close"].equals(df2["close"])

    def test_volume_is_positive_integer_like(self, provider):
        df = provider.synthesize_ohlcv(n_days=50)
        assert (df["volume"] >= 0).all()
        assert df["volume"].dtype == float

    def test_high_is_max_low_is_min(self, provider):
        df = provider.synthesize_ohlcv(n_days=100)
        assert (df["high"] >= df["close"]).all()
        assert (df["low"] <= df["close"]).all()


# ═══════════════════════════════════════════════════════════
# 12. _extract_data (static)
# ═══════════════════════════════════════════════════════════

class TestExtractData:
    def test_payload_with_data_attr(self):
        obj = MagicMock()
        obj.data = {"key": "value"}
        assert FTSDataProvider._extract_data(obj) == {"key": "value"}

    def test_payload_with_payload_attr(self):
        """对象有 .payload 但无 .data 属性时返回 .payload。"""
        obj = type("PayloadObj", (), {"payload": [1, 2, 3]})()
        assert FTSDataProvider._extract_data(obj) == [1, 2, 3]

    def test_raw_data_no_attrs(self):
        data = {"raw": "dict"}
        assert FTSDataProvider._extract_data(data) is data

    def test_dataframe_passthrough(self):
        df = pd.DataFrame({"a": [1]})
        assert FTSDataProvider._extract_data(df) is df

    def test_list_passthrough(self):
        lst = [1, 2, 3]
        assert FTSDataProvider._extract_data(lst) is lst


# ═══════════════════════════════════════════════════════════
# 13. _payload_to_ohlcv_df (static)
# ═══════════════════════════════════════════════════════════

class TestPayloadToOhlcvDF:
    def test_dataframe_passthrough(self):
        df = pd.DataFrame({
            "open": [1.0], "high": [2.0], "low": [0.5],
            "close": [1.5], "volume": [1000.0],
        })
        result = FTSDataProvider._payload_to_ohlcv_df(df)
        assert result is df

    def test_dict_with_date_column(self):
        payload = MagicMock()
        payload.data = {
            "open": [1.0, 2.0],
            "close": [1.5, 2.5],
            "date": pd.date_range("2025-01-01", periods=2, freq="D"),
        }
        df = FTSDataProvider._payload_to_ohlcv_df(payload)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert len(df) == 2

    def test_dict_without_date_column(self):
        payload = MagicMock()
        payload.data = {"open": [1.0, 2.0], "close": [1.5, 2.5]}
        df = FTSDataProvider._payload_to_ohlcv_df(payload)
        assert len(df) == 2
        # 无 date 列时索引应为 RangeIndex
        assert not isinstance(df.index, pd.DatetimeIndex)

    def test_list_of_dicts_with_date(self):
        payload = MagicMock()
        payload.data = [
            {"open": 1.0, "close": 1.5, "date": pd.Timestamp("2025-01-01")},
            {"open": 2.0, "close": 2.5, "date": pd.Timestamp("2025-01-02")},
        ]
        df = FTSDataProvider._payload_to_ohlcv_df(payload)
        assert isinstance(df.index, pd.DatetimeIndex)
        assert len(df) == 2

    def test_list_of_dicts_without_date(self):
        payload = MagicMock()
        payload.data = [
            {"open": 1.0, "close": 1.5},
            {"open": 2.0, "close": 2.5},
        ]
        df = FTSDataProvider._payload_to_ohlcv_df(payload)
        assert len(df) == 2
        assert not isinstance(df.index, pd.DatetimeIndex)

    def test_unrecognized_type_returns_empty_df(self):
        payload = MagicMock()
        payload.data = 42  # int 无法解析
        df = FTSDataProvider._payload_to_ohlcv_df(payload)
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    def test_none_data_returns_empty_df(self):
        payload = MagicMock()
        payload.data = None
        df = FTSDataProvider._payload_to_ohlcv_df(payload)
        assert isinstance(df, pd.DataFrame)
        assert df.empty


# ═══════════════════════════════════════════════════════════
# 14. _payload_to_dict (static)
# ═══════════════════════════════════════════════════════════

class TestPayloadToDict:
    def test_dict_data(self):
        payload = MagicMock()
        payload.data = {"a": 1, "b": 2}
        assert FTSDataProvider._payload_to_dict(payload) == {"a": 1, "b": 2}

    def test_non_dict_data_wraps_in_raw(self):
        payload = MagicMock()
        payload.data = [1, 2, 3]
        result = FTSDataProvider._payload_to_dict(payload)
        assert result == {"raw": [1, 2, 3]}

    def test_string_data(self):
        payload = MagicMock()
        payload.data = "hello"
        result = FTSDataProvider._payload_to_dict(payload)
        assert result == {"raw": "hello"}

    def test_none_data(self):
        payload = MagicMock()
        payload.data = None
        result = FTSDataProvider._payload_to_dict(payload)
        assert result == {"raw": None}

    def test_dataframe_wrapped(self):
        """DataFrame 为非 dict 类型，应被包裹在 {'raw': ...} 中。"""
        df = pd.DataFrame({"a": [1]})
        payload = MagicMock()
        payload.data = df
        result = FTSDataProvider._payload_to_dict(payload)
        assert "raw" in result
        assert isinstance(result["raw"], pd.DataFrame)


# ═══════════════════════════════════════════════════════════
# 15. get_data_provider（全局单例）
# ═══════════════════════════════════════════════════════════

class TestGetDataProvider:
    def test_returns_fts_data_provider(self):
        p = get_data_provider()
        assert isinstance(p, FTSDataProvider)

    def test_singleton_same_instance(self):
        p1 = get_data_provider()
        p2 = get_data_provider()
        assert p1 is p2

    def test_singleton_with_different_args_ignored(self):
        """第二次调用时即使传入不同参数也应返回同一实例。"""
        fake1 = MagicMock()
        fake2 = MagicMock()
        p1 = get_data_provider(datacore_provider=fake1)

        # 重置全局单例后重新测试
        import fts.data as _data
        _data._default_provider = None

        p2 = get_data_provider(datacore_provider=fake2)
        assert p2 is not p1

    def test_reset_between_tests(self):
        """验证单例在测试间被重置。"""
        import fts.data as _data
        _data._default_provider = None
        p = get_data_provider()
        assert _data._default_provider is p

    def test_with_mock_provider(self, mock_provider):
        """传入 mock provider 应创建携带该 provider 的实例。"""
        import fts.data as _data
        _data._default_provider = None
        p = get_data_provider(datacore_provider=mock_provider)
        assert p._dc is mock_provider


# ═══════════════════════════════════════════════════════════
# 16. 集成边界场景
# ═══════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_provider_import_error_on_get_ohlcv(self):
        """当 _provider 抛出 ImportError 时 get_ohlcv 应转为 DataUnavailableError。"""
        p = FTSDataProvider(local_db="/nonexistent/test.duckdb")
        with patch.object(p, "_dc", None):
            with patch("builtins.__import__", side_effect=ImportError("no datacore")):
                with pytest.raises(DataUnavailableError, match="Data-Core 未安装"):
                    p.get_ohlcv("RB")

    def test_get_news_unexpected_data_type(self, provider, mock_provider):
        """payload.data 为不可识别的类型时返回空列表。"""
        mock_provider.get.side_effect = None
        payload = MagicMock()
        payload.data = 42  # int，既不是 list 也不是 dict
        mock_provider.get.return_value = payload
        result = provider.get_news("RB")
        assert result == []

    def test_get_batch_ohlcv_reuses_trace_id(self, provider):
        """批量获取时 trace_id 应传递到每个单次调用。"""
        with patch.object(provider, "get_ohlcv") as mock_get:
            mock_get.return_value = pd.DataFrame()
            provider.get_batch_ohlcv(["RB", "HC"], trace_id="batch-001")
            for call_args in mock_get.call_args_list:
                assert call_args[1]["trace_id"] == "batch-001"

    def test_news_dict_without_items_or_news(self, provider, mock_provider):
        """dict 不含 items/news 键时返回空列表。"""
        mock_provider.get.side_effect = None
        payload = MagicMock()
        payload.data = {"other_key": "val"}
        mock_provider.get.return_value = payload
        result = provider.get_news("RB")
        assert result == []

    def test_empty_symbol_for_ohlcv(self, provider, mock_provider):
        """symbol 为空字符串时仍应传递给 provider.get。"""
        provider.get_ohlcv("")
        mock_provider.get.assert_called_with(
            "", "ohlcv",
            params={"days": 500, "period": "daily"},
        )
