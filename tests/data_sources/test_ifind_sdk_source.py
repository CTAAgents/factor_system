"""IFindSDKSource 单元测试（GAP-083 阶段 C 方案 A：iFinD 官方 SDK 增强源）。

覆盖:
- 符号映射（主连剥 0 / 具体合约 / 交易所推断 / 未知 → None）
- futures_get 原始返回解析（DataFrame/dict → 17 列契约子集 + 无效值清理）
- 认证双模式（token / 账号密码）与全降级路径（无 SDK/无凭据/登录失败/接口异常）
- is_available / fetch_quote
"""

import sys
from datetime import date

import pandas as pd
import pytest

from fts.data_sources.ifind_sdk_source import IFindSDKSource


@pytest.fixture
def mock_ifind(mocker):
    """mock iFinDPy 模块 + token 凭据环境变量，返回 fake_ths。"""
    fake = mocker.MagicMock()
    fake.token.return_value = 0
    fake.login.return_value = 0
    fake.logout.return_value = None
    fake.thsi.futures_get.return_value = pd.DataFrame()
    mocker.patch.dict(sys.modules, {"iFinDPy": fake})
    mocker.patch.dict(
        "os.environ",
        {"IFIND_TOKEN": "test-token"},
        clear=False,
    )
    return fake


def _futures_df() -> pd.DataFrame:
    """构造 iFinD futures_get 返回的 DataFrame。"""
    return pd.DataFrame(
        {
            "date": ["2026-08-07", "2026-08-10", "2026-08-11"],
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "volume": [1000, 1100, 1200],
            "settle": [10.4, 11.4, 12.4],
            "preSettle": [10.0, 10.5, 11.5],
            "openInterest": [100.0, 110.0, 120.0],
            "openInterestChg": [0.0, 10.0, 10.0],
        }
    )


class TestResolveSymbol:
    def test_main_contract_strip_zero(self):
        assert IFindSDKSource()._resolve_symbol("RB0") == "RB.SHF"
        assert IFindSDKSource()._resolve_symbol("RB") == "RB.SHF"

    def test_specific_contract(self):
        assert IFindSDKSource()._resolve_symbol("RB2609") == "RB2609.SHF"

    def test_index_futures_cfx(self):
        assert IFindSDKSource()._resolve_symbol("IF0") == "IF.CFX"
        assert IFindSDKSource()._resolve_symbol("TF0") == "TF.CFX"

    def test_unknown_returns_none(self):
        assert IFindSDKSource()._resolve_symbol("ZZ999") is None


class TestParseFuturesDf:
    def test_dataframe_maps_fields(self):
        out = IFindSDKSource()._parse_futures_df(_futures_df(), "RB0", trace_id="t1")
        assert out is not None
        assert out["date"].tolist() == [date(2026, 8, 7), date(2026, 8, 10), date(2026, 8, 11)]
        assert out["settle"].tolist() == [10.4, 11.4, 12.4]
        assert out["pre_settle"].tolist() == [10.0, 10.5, 11.5]
        assert out["hold"].tolist() == [100.0, 110.0, 120.0]
        assert out["oi_change"].tolist() == [0.0, 10.0, 10.0]
        assert out["source"].iloc[0] == "IFIND_SDK"

    def test_dict_input(self):
        raw = {"data": [{"date": "2026-08-11", "open": 12.0, "close": 12.5,
                         "settle": 12.4, "preSettle": 11.5, "openInterest": 120.0}]}
        out = IFindSDKSource()._parse_futures_df(raw, "RB0")
        assert out is not None and len(out) == 1
        assert out["pre_settle"].iloc[0] == 11.5

    def test_invalid_values_cleaned(self):
        df = _futures_df()
        df.loc[1, "settle"] = 0.0
        df.loc[2, "preSettle"] = -1.0
        out = IFindSDKSource()._parse_futures_df(df, "RB0")
        assert out is not None
        assert pd.isna(out["settle"].iloc[1])
        assert pd.isna(out["pre_settle"].iloc[2])

    def test_empty_or_none_returns_none(self):
        assert IFindSDKSource()._parse_futures_df(None, "RB0") is None
        assert IFindSDKSource()._parse_futures_df(pd.DataFrame(), "RB0") is None
        assert IFindSDKSource()._parse_futures_df({"data": []}, "RB0") is None


class TestFetchOhlcv:
    def test_token_mode(self, mocker, mock_ifind):
        mock_ifind.thsi.futures_get.return_value = _futures_df()
        out = IFindSDKSource().fetch_ohlcv("RB0", days=5, trace_id="t1")
        assert out is not None
        assert out["pre_settle"].tolist() == [10.0, 10.5, 11.5]
        mock_ifind.token.assert_called_once_with("test-token")
        # 登录后必须登出
        mock_ifind.logout.assert_called_once()

    def test_login_mode(self, mocker, mock_ifind):
        mocker.patch.dict(
            "os.environ",
            {"IFIND_TOKEN": "", "IFIND_USERNAME": "user", "IFIND_PASSWORD": "pwd"},
            clear=True,
        )
        mock_ifind.thsi.futures_get.return_value = _futures_df()
        out = IFindSDKSource().fetch_ohlcv("RB0", days=5)
        assert out is not None
        mock_ifind.login.assert_called_once_with("user", "pwd")
        assert not mock_ifind.token.called

    def test_no_sdk_returns_none(self, mocker):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "iFinDPy":
                raise ImportError("no ifindpydep")
            return real_import(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=fake_import)
        mocker.patch.dict("os.environ", {"IFIND_TOKEN": "x"}, clear=False)
        assert IFindSDKSource().fetch_ohlcv("RB0", days=5) is None

    def test_no_credentials_returns_none(self, mocker):
        mocker.patch.dict(
            sys.modules,
            {"iFinDPy": mocker.MagicMock()},
        )
        mocker.patch.dict(
            "os.environ",
            {"IFIND_TOKEN": "", "IFIND_USERNAME": "", "IFIND_PASSWORD": ""},
            clear=True,
        )
        assert IFindSDKSource().fetch_ohlcv("RB0", days=5) is None

    def test_no_mapping_returns_none(self, mocker, mock_ifind):
        assert IFindSDKSource().fetch_ohlcv("ZZ999", days=5) is None
        mock_ifind.thsi.futures_get.assert_not_called()

    def test_login_failure_returns_none(self, mocker, mock_ifind):
        mock_ifind.token.return_value = -1  # 登录失败错误码
        assert IFindSDKSource().fetch_ohlcv("RB0", days=5) is None
        mock_ifind.thsi.futures_get.assert_not_called()

    def test_interface_exception_returns_none(self, mocker, mock_ifind):
        mock_ifind.thsi.futures_get.side_effect = RuntimeError("api down")
        assert IFindSDKSource().fetch_ohlcv("RB0", days=5) is None

    def test_empty_result_returns_none(self, mocker, mock_ifind):
        mock_ifind.thsi.futures_get.return_value = pd.DataFrame()
        assert IFindSDKSource().fetch_ohlcv("RB0", days=5) is None


class TestAvailability:
    def test_is_available_token(self, mocker, mock_ifind):
        assert IFindSDKSource().is_available() is True

    def test_is_available_no_credentials(self, mocker):
        mocker.patch.dict(sys.modules, {"iFinDPy": mocker.MagicMock()})
        mocker.patch.dict(
            "os.environ",
            {"IFIND_TOKEN": "", "IFIND_USERNAME": "", "IFIND_PASSWORD": ""},
            clear=True,
        )
        assert IFindSDKSource().is_available() is False

    def test_is_available_no_sdk(self, mocker):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "iFinDPy":
                raise ImportError("no sdk")
            return real_import(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=fake_import)
        mocker.patch.dict("os.environ", {"IFIND_TOKEN": "x"}, clear=False)
        assert IFindSDKSource().is_available() is False

    def test_fetch_quote_none(self):
        assert IFindSDKSource().fetch_quote("RB0") is None
