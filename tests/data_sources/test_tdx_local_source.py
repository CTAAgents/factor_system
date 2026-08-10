"""
tests.data_sources.test_tdx_local_source — 通达信本地 HTTP 统一数据源测试（v2.85.0）。

测试覆盖:
    1. 主力连续代码映射（RB0 → RBL8.SHF / IF0 → IFL0.CFF / TA0 → TAL8.CZC）
    2. 交易所后缀推断（不误判 TA 为 CFFEX 国债 T）
    3. 周期映射（day → 1d / 60m → 1h）
    4. 日线响应解析（17 列 kline_cache schema，date 列）
    5. 分钟响应解析（11 列 minute_cache schema，datetime 列）
    6. 实时快照 fetch_quote（get_market_snapshot）
    7. 探活 is_available / 异常降级

HARNESS §5.4 测试随重构: v2.87.0 合并 TQLocalSource(7721) 与 TDXMinuteSource(17709)。
"""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import patch

import pandas as pd
import pytest

from fts.data_sources.tdx_local_source import (
    SUPPORTED_PERIODS,
    TdxLocalSource,
    _infer_exchange_suffix,
    _symbol_to_tdx,
)


# ─── 1. 主力连续代码映射 ──────────────────────────────────


class TestSymbolToTdx:
    """测试 FTS 连续合约代码 → 通达信主力连续代码。"""

    @pytest.mark.parametrize(
        "fts_code, expected",
        [
            # 上期所商品期货 → L8.SHF
            ("RB0", "RBL8.SHF"),
            ("CU0", "CUL8.SHF"),
            ("AU0", "AUL8.SHF"),
            ("AG0", "AGL8.SHF"),
            # 大商所 → L8.DCE
            ("M0", "ML8.DCE"),
            ("I0", "IL8.DCE"),
            ("J0", "JL8.DCE"),
            ("Y0", "YL8.DCE"),
            # 郑商所 → L8.CZC
            ("TA0", "TAL8.CZC"),
            ("MA0", "MAL8.CZC"),
            ("FG0", "FGL8.CZC"),
            # 中金所 → L0.CFF
            ("IF0", "IFL0.CFF"),
            ("IH0", "IHL0.CFF"),
            ("IC0", "ICL0.CFF"),
            ("T0", "TL0.CFF"),
        ],
    )
    def test_mapping(self, fts_code: str, expected: str) -> None:
        """各市场主力连续代码映射正确。"""
        assert _symbol_to_tdx(fts_code) == expected

    def test_ta_not_mistaken_for_t_bond(self) -> None:
        """TA（郑商所 PTA）不得误判为中金所国债 T。"""
        assert _symbol_to_tdx("TA0") == "TAL8.CZC"


# ─── 2. 交易所后缀推断 ────────────────────────────────────


class TestInferExchangeSuffix:
    """测试交易所后缀推断。"""

    @pytest.mark.parametrize(
        "symbol, expected",
        [
            ("RB0", "SHF"),
            ("CU0", "SHF"),
            ("M0", "DCE"),
            ("I0", "DCE"),
            ("TA0", "CZC"),
            ("MA0", "CZC"),
            ("IF0", "CFF"),
            ("T0", "CFF"),
            ("SC0", "SHF"),  # 能源中心品种未单独映射，回退 SHF
        ],
    )
    def test_suffix(self, symbol: str, expected: str) -> None:
        assert _infer_exchange_suffix(symbol) == expected


# ─── 3. 周期映射 ──────────────────────────────────────────


class TestSupportedPeriods:
    """测试周期映射（day → 1d，60m → 1h）。"""

    def test_all_periods_supported(self) -> None:
        assert SUPPORTED_PERIODS == {
            "day": "1d",
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "30m": "30m",
            "60m": "1h",
        }

    def test_invalid_period_rejected(self) -> None:
        with pytest.raises(ValueError):
            TdxLocalSource(period="2m")


# ─── 4. 日线响应解析 ──────────────────────────────────────


class TestDailyParsing:
    """测试日线列字典响应 → 17 列 kline_cache schema。"""

    def _daily_response(self, symbol: str = "RBL8.SHF") -> dict:
        return {
            "id": 1,
            "result": {
                "ErrorId": "0",
                "KlineTotal": {symbol: 2},
                "Value": {
                    symbol: {
                        "Date": ["20260806", "20260807"],
                        "Time": ["0", "0"],
                        "Open": ["3012", "3010"],
                        "High": ["3023", "3025"],
                        "Low": ["3000", "3000"],
                        "Close": ["3014", "3008"],
                        "Volume": ["824109.00", "742347.00"],
                        "Amount": ["0.00", "0.00"],
                    }
                },
            },
        }

    @patch("urllib.request.urlopen")
    def test_parse_daily(self, mock_urlopen) -> None:
        """日线响应应解析为 17 列 kline_cache schema（date 列）。"""
        mock_urlopen.return_value = _FakeResp(self._daily_response())
        src = TdxLocalSource(period="day")
        df = src.fetch_ohlcv("RB0", days=500, trace_id="test_tdx")

        assert df is not None
        assert len(df) == 2
        expected_cols = [
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
        assert list(df.columns) == expected_cols
        assert df["symbol"].iloc[0] == "RB0"
        assert df["period"].iloc[0] == "daily"
        assert df["source"].iloc[0] == "TDX_LOCAL"
        assert df["close"].iloc[0] == 3014.0
        assert pd.Timestamp(df["date"].iloc[0]) == pd.Timestamp("2026-08-06")

    @patch("urllib.request.urlopen")
    def test_daily_missing_futures_fields_na(self, mock_urlopen) -> None:
        """日线响应缺 hold/settle/pre_settle/oi_change → 补 NA 保持 schema。"""
        mock_urlopen.return_value = _FakeResp(self._daily_response())
        df = TdxLocalSource(period="day").fetch_ohlcv("RB0", days=30)
        assert df is not None
        assert df["hold"].isna().all()
        assert df["settle"].isna().all()
        assert df["pre_settle"].isna().all()
        assert df["oi_change"].isna().all()

    @patch("urllib.request.urlopen")
    def test_daily_vwap_fallback_typical(self, mock_urlopen) -> None:
        """amount 无效时 vwap 回退到 (h+l+c)/3。"""
        mock_urlopen.return_value = _FakeResp(self._daily_response())
        df = TdxLocalSource(period="day").fetch_ohlcv("RB0", days=30)
        assert df is not None
        first = df.iloc[0]
        expected = (first["high"] + first["low"] + first["close"]) / 3
        assert abs(df["vwap"].iloc[0] - expected) < 0.01


# ─── 5. 分钟响应解析 ──────────────────────────────────────


class TestMinuteParsing:
    """测试分钟列字典响应 → 11 列 minute_cache schema。"""

    def _minute_response(self, symbol: str = "RBL8.SHF") -> dict:
        return {
            "id": 1,
            "result": {
                "ErrorId": "0",
                "KlineTotal": {symbol: 2},
                "Value": {
                    symbol: {
                        "Date": ["20260807", "20260807"],
                        "Time": ["214000", "214500"],
                        "Open": ["3003", "3005"],
                        "High": ["3007", "3008"],
                        "Low": ["3003", "3004"],
                        "Close": ["3006", "3006"],
                        "Volume": ["7952.00", "7413.00"],
                        "Amount": ["0.00", "0.00"],
                    }
                },
            },
        }

    @patch("urllib.request.urlopen")
    def test_parse_minute(self, mock_urlopen) -> None:
        """分钟响应应解析为 11 列 minute_cache schema。"""
        mock_urlopen.return_value = _FakeResp(self._minute_response())
        src = TdxLocalSource(period="5m")
        df = src.fetch_ohlcv("RB0", days=500, trace_id="test_tdx")

        assert df is not None
        assert len(df) == 2
        assert list(df.columns) == [
            "symbol",
            "period",
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "source",
            "fetched_at",
            "trace_id",
        ]
        assert df["symbol"].iloc[0] == "RB0"
        assert df["period"].iloc[0] == "5m"
        assert df["source"].iloc[0] == "TDX_LOCAL"
        assert df["close"].iloc[0] == 3006.0
        assert pd.Timestamp(df["datetime"].iloc[0]) == pd.Timestamp("2026-08-07 21:40:00")

    @patch("urllib.request.urlopen")
    def test_time_only_builds_datetime_with_today(self, mock_urlopen) -> None:
        """仅有 Time 字段 → 使用当日日期构建 datetime。"""
        block = {
            "Time": ["214000", "214500"],
            "Open": ["3003", "3005"],
            "High": ["3007", "3008"],
            "Low": ["3003", "3004"],
            "Close": ["3006", "3006"],
            "Volume": ["7952.00", "7413.00"],
        }
        mock_urlopen.return_value = _FakeResp({"result": {"Value": {"RBL8.SHF": block}}})
        df = TdxLocalSource(period="5m").fetch_ohlcv("RB0")
        assert df is not None
        assert len(df) == 2
        assert df["datetime"].iloc[0].date() == pd.Timestamp.now().normalize().date()

    @patch("urllib.request.urlopen")
    def test_date_only_builds_datetime(self, mock_urlopen) -> None:
        """仅有 Date 字段 → 构建日期 datetime。"""
        block = {
            "Date": ["20260807", "20260808"],
            "Open": ["3003", "3005"],
            "High": ["3007", "3008"],
            "Low": ["3003", "3004"],
            "Close": ["3006", "3006"],
            "Volume": ["7952.00", "7413.00"],
        }
        mock_urlopen.return_value = _FakeResp({"result": {"Value": {"RBL8.SHF": block}}})
        df = TdxLocalSource(period="5m").fetch_ohlcv("RB0")
        assert df is not None
        assert df["datetime"].iloc[0] == pd.Timestamp("2026-08-07")

    @patch("urllib.request.urlopen")
    def test_no_date_time_fields_returns_none(self, mock_urlopen) -> None:
        """缺 date/time 字段 → 返回 None。"""
        block = {"Open": ["3003"], "High": ["3007"], "Low": ["3003"], "Close": ["3006"], "Volume": ["1"]}
        mock_urlopen.return_value = _FakeResp({"result": {"Value": {"RBL8.SHF": block}}})
        assert TdxLocalSource(period="5m").fetch_ohlcv("RB0") is None

    @patch("urllib.request.urlopen")
    def test_truncates_to_last_days_rows(self, mock_urlopen) -> None:
        """截取最近 days 行。"""
        n = 5
        block = {
            "Date": ["20260807"] * n,
            "Time": [f"{210000 + i * 100:06d}" for i in range(n)],
            "Open": ["3003"] * n,
            "High": ["3007"] * n,
            "Low": ["3003"] * n,
            "Close": ["3006"] * n,
            "Volume": ["7952.00"] * n,
        }
        mock_urlopen.return_value = _FakeResp({"result": {"Value": {"RBL8.SHF": block}}})
        df = TdxLocalSource(period="5m").fetch_ohlcv("RB0", days=2)
        assert df is not None
        assert len(df) == 2


# ─── 6. 探活 is_available ─────────────────────────────────


class TestIsAvailable:
    """测试 is_available 探活分支。"""

    @patch("urllib.request.urlopen")
    def test_available_when_200(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResp(status=200)
        assert TdxLocalSource().is_available() is True

    @patch("urllib.request.urlopen")
    def test_unavailable_on_url_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("refused")
        assert TdxLocalSource().is_available() is False

    @patch("urllib.request.urlopen")
    def test_unavailable_on_http_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError("url", 500, "err", {}, None)
        assert TdxLocalSource().is_available() is False

    @patch("urllib.request.urlopen")
    def test_unavailable_on_os_error(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = OSError("socket closed")
        assert TdxLocalSource().is_available() is False


# ─── 7. fetch_ohlcv 异常分支 ──────────────────────────────


class TestFetchOhlcvErrors:
    """测试 fetch_ohlcv 的 HTTP/解析异常 → SourceUnavailable。"""

    @patch("urllib.request.urlopen")
    def test_url_error_raises(self, mock_urlopen) -> None:
        from fts.data_sources.base import SourceUnavailable

        mock_urlopen.side_effect = urllib.error.URLError("refused")
        with pytest.raises(SourceUnavailable):
            TdxLocalSource().fetch_ohlcv("RB0", days=10)

    @patch("urllib.request.urlopen")
    def test_http_error_raises(self, mock_urlopen) -> None:
        from fts.data_sources.base import SourceUnavailable

        mock_urlopen.side_effect = urllib.error.HTTPError("url", 500, "err", {}, None)
        with pytest.raises(SourceUnavailable):
            TdxLocalSource().fetch_ohlcv("RB0", days=10)

    @patch("urllib.request.urlopen")
    def test_os_error_raises(self, mock_urlopen) -> None:
        from fts.data_sources.base import SourceUnavailable

        mock_urlopen.side_effect = OSError("connection refused")
        with pytest.raises(SourceUnavailable):
            TdxLocalSource().fetch_ohlcv("RB0", days=10)

    @patch("urllib.request.urlopen")
    def test_json_decode_error_raises(self, mock_urlopen) -> None:
        from fts.data_sources.base import SourceUnavailable

        mock_urlopen.return_value = _FakeResp("{not json")
        with pytest.raises(SourceUnavailable):
            TdxLocalSource().fetch_ohlcv("RB0", days=10)


# ─── 8. fetch_ohlcv 解析降级分支 ──────────────────────────


class TestFetchOhlcvDegradation:
    """测试 fetch_ohlcv 对异常响应的降级返回 None。"""

    @patch("urllib.request.urlopen")
    def test_raw_not_dict_returns_none(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResp(["not", "dict"])
        assert TdxLocalSource().fetch_ohlcv("RB0") is None

    @patch("urllib.request.urlopen")
    def test_result_without_value_returns_none(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResp({"result": {"ErrorId": "0"}})
        assert TdxLocalSource().fetch_ohlcv("RB0") is None

    @patch("urllib.request.urlopen")
    def test_block_not_dict_returns_none(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResp({"result": {"Value": {"RBL8.SHF": ["not", "dict"]}}})
        assert TdxLocalSource().fetch_ohlcv("RB0") is None

    @patch("urllib.request.urlopen")
    def test_empty_value_returns_none(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResp({"id": 1, "result": {"Value": {}}})
        assert TdxLocalSource().fetch_ohlcv("RB0") is None


# ─── 9. fetch_quote ──────────────────────────────────────


class TestFetchQuote:
    """测试 fetch_quote 快照解析。"""

    @patch("urllib.request.urlopen")
    def test_parse_result_dict(self, mock_urlopen) -> None:
        result = {"Now": "3006", "Open": "3000", "Max": "3010", "Min": "2990", "Volume": "1000"}
        mock_urlopen.return_value = _FakeResp({"result": result})
        q = TdxLocalSource().fetch_quote("RB0", trace_id="t")
        assert q is not None
        assert q["last_price"] == 3006.0
        assert q["open"] == 3000.0
        assert q["high"] == 3010.0
        assert q["low"] == 2990.0
        assert q["volume"] == 1000.0
        assert q["symbol"] == "RB0"
        assert q["source"] == "TDX_LOCAL"
        assert q["trace_id"] == "t"

    @patch("urllib.request.urlopen")
    def test_result_not_dict_returns_basic_quote(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResp({"result": "raw"})
        q = TdxLocalSource().fetch_quote("RB0")
        assert q is not None
        assert "last_price" not in q
        assert q["symbol"] == "RB0"

    @patch("urllib.request.urlopen")
    def test_empty_result_returns_none(self, mock_urlopen) -> None:
        mock_urlopen.return_value = _FakeResp({"result": None})
        assert TdxLocalSource().fetch_quote("RB0") is None

    @patch("urllib.request.urlopen")
    def test_exception_returns_none(self, mock_urlopen) -> None:
        mock_urlopen.side_effect = OSError("boom")
        assert TdxLocalSource().fetch_quote("RB0") is None


# ─── 通用 mock HTTP 响应 ──────────────────────────────────


class _FakeResp:
    """通用 mock HTTP 响应（支持 with 上下文与 status/read）。"""

    def __init__(self, body=None, status=200) -> None:
        self._body = body
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        if isinstance(self._body, str):
            return self._body.encode("utf-8")
        return json.dumps(self._body).encode("utf-8")

    @property
    def status(self):
        return self._status
