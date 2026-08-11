"""TQSDKEnhanceSource 单元测试（GAP-083 阶段 C：天勤真实持仓增强源）。

覆盖:
- 天勤 close_oi → hold / 差分 → oi_change 映射
- open_oi 回退 / 缺持仓字段降级 / close_oi 零值 → NaN
- 账号缺失 / 品种无映射 / 天勤异常 → 降级 None
- 符号映射（RB0 / RB 补 0 / 未知）
- is_available / fetch_quote
"""

import sys
from datetime import date

import pandas as pd
import pytest

from fts.data_sources.tqsdk_enhance_source import TQSDKEnhanceSource


def _kline_df(close_oi: list[float] | None = None, open_oi: list[float] | None = None) -> pd.DataFrame:
    """构造天勤 get_kline_serial 返回的 DataFrame（datetime 为 ns 时间戳）。"""
    ts = [pd.Timestamp(d).value for d in ("2026-08-07", "2026-08-10", "2026-08-11")]
    df = pd.DataFrame(
        {
            "datetime": ts,
            "open": [10.0, 11.0, 12.0],
            "high": [11.0, 12.0, 13.0],
            "low": [9.0, 10.0, 11.0],
            "close": [10.5, 11.5, 12.5],
            "volume": [1000, 1100, 1200],
            "symbol": "KQ.m@SHFE.rb",
            "duration": 86400,
        }
    )
    if close_oi is not None:
        df["close_oi"] = close_oi
    if open_oi is not None:
        df["open_oi"] = open_oi
    return df


@pytest.fixture
def mock_tqsdk(mocker):
    """mock tqsdk 模块 + 天勤账号环境变量，返回 fake_api。

    TqAuth 在 fetch_ohlcv 内 `from tqsdk import TqAuth` 导入 → 直接由 mock 模块的
    自动属性提供，无需单独 patch。
    """
    mocker.patch.dict(
        sys.modules,
        {"tqsdk": mocker.MagicMock()},
    )
    fake_api = mocker.MagicMock()
    fake_api.wait_update.return_value = None
    fake_api.close.return_value = None
    sys.modules["tqsdk"].TqApi.return_value = fake_api
    mocker.patch.dict(
        "os.environ",
        {"TQSDK_USERNAME": "test_user", "TQSDK_PASSWORD": "test_pass"},
        clear=False,
    )
    return fake_api


class TestFetchHoldSettle:
    def test_maps_hold_and_oi_change(self, mocker, mock_tqsdk):
        """close_oi → hold、差分 → oi_change；不输出 settle/amount 列。"""
        mock_tqsdk.get_kline_serial.return_value = _kline_df(close_oi=[100.0, 120.0, 110.0])
        out = TQSDKEnhanceSource().fetch_ohlcv("RB0", days=5, trace_id="t1")
        assert out is not None
        assert out["hold"].tolist() == [100.0, 120.0, 110.0]
        assert out["oi_change"].tolist() == [0.0, 20.0, -10.0]
        assert out["date"].tolist() == [date(2026, 8, 7), date(2026, 8, 10), date(2026, 8, 11)]
        assert "settle" not in out.columns
        assert "pre_settle" not in out.columns
        assert "amount" not in out.columns
        assert out["source"].iloc[0] == "TQSDK_ENHANCE"

    def test_open_oi_fallback(self, mocker, mock_tqsdk):
        """无 close_oi 时回退 open_oi 作为持仓量。"""
        mock_tqsdk.get_kline_serial.return_value = _kline_df(open_oi=[50.0, 60.0, 55.0])
        out = TQSDKEnhanceSource().fetch_ohlcv("RB0", days=5)
        assert out is not None
        assert out["hold"].tolist() == [50.0, 60.0, 55.0]

    def test_no_oi_field_returns_none(self, mocker, mock_tqsdk):
        """无 close_oi/open_oi → 返回 None（不产出无持仓的增强）。"""
        mock_tqsdk.get_kline_serial.return_value = _kline_df()
        assert TQSDKEnhanceSource().fetch_ohlcv("RB0", days=5) is None

    def test_zero_oi_to_nan(self, mocker, mock_tqsdk):
        """close_oi=0 → hold NaN（有效值覆盖时保留主路径回填值）。"""
        mock_tqsdk.get_kline_serial.return_value = _kline_df(close_oi=[0.0, 120.0, 110.0])
        out = TQSDKEnhanceSource().fetch_ohlcv("RB0", days=5)
        assert out is not None
        assert pd.isna(out["hold"].iloc[0])
        assert out["hold"].iloc[1] == 120.0

    def test_missing_credentials_returns_none(self, mocker):
        """未配置天勤账号 → 返回 None，不发起连接。"""
        mocker.patch.dict(sys.modules, {"tqsdk": mocker.MagicMock()})
        mocker.patch.dict(
            "os.environ",
            {"TQSDK_USERNAME": "", "TQSDK_PASSWORD": ""},
        )
        assert TQSDKEnhanceSource().fetch_ohlcv("RB0", days=5) is None

    def test_no_mapping_returns_none(self, mocker, mock_tqsdk):
        """品种无天勤主连映射（如 ZZ999）→ 返回 None。"""
        assert TQSDKEnhanceSource().fetch_ohlcv("ZZ999", days=5) is None

    def test_tqsdk_exception_returns_none(self, mocker, mock_tqsdk):
        """天勤调用异常 → 返回 None（增强层降级，不阻断主路径）。"""
        mock_tqsdk.get_kline_serial.side_effect = RuntimeError("network down")
        assert TQSDKEnhanceSource().fetch_ohlcv("RB0", days=5) is None


class TestResolveSymbol:
    def test_suffix0_mapping(self):
        assert TQSDKEnhanceSource()._resolve_symbol("RB0") == "KQ.m@SHFE.rb"

    def test_no_suffix_auto_append(self):
        assert TQSDKEnhanceSource()._resolve_symbol("RB") == "KQ.m@SHFE.rb"

    def test_unknown_passthrough(self):
        assert TQSDKEnhanceSource()._resolve_symbol("ZZ999") == "ZZ999"


class TestAvailability:
    def test_is_available_true(self, mocker, mock_tqsdk):
        assert TQSDKEnhanceSource().is_available() is True

    def test_is_available_missing_credentials(self, mocker):
        mocker.patch.dict(sys.modules, {"tqsdk": mocker.MagicMock()})
        mocker.patch.dict(
            "os.environ",
            {"TQSDK_USERNAME": "", "TQSDK_PASSWORD": ""},
        )
        assert TQSDKEnhanceSource().is_available() is False

    def test_is_available_missing_package(self, mocker):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "tqsdk":
                raise ImportError("no tqsdk")
            return real_import(name, *args, **kwargs)

        mocker.patch("builtins.__import__", side_effect=fake_import)
        assert TQSDKEnhanceSource().is_available() is False

    def test_fetch_quote_none(self):
        assert TQSDKEnhanceSource().fetch_quote("RB0") is None
