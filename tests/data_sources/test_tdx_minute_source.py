"""
tests.data_sources.test_tdx_minute_source — 通达信分钟数据源适配器测试（v2.30.0）。

测试覆盖:
    1. 主力连续代码映射（RB0 → RBL8.SHF / IF0 → IFL0.CFF / TA0 → TAL8.CZC）
    2. 交易所后缀推断（不误判 TA 为 CFFEX 国债 T）
    3. 周期映射（60m → 1h）
    4. 列字典响应解析（TQ-Local 返回格式）

HARNESS §5.4 测试随重构: 修复代码映射/解析逻辑后补充测试。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pandas as pd
import pytest

from fts.data_sources.tdx_minute_source import (
    SUPPORTED_PERIODS,
    TDXMinuteSource,
    _infer_exchange_suffix,
    _symbol_to_tdx,
)


# ─── 1. 主力连续代码映射 ──────────────────────────────────


class TestSymbolToTdx:
    """测试 FTS 连续合约代码 → 通达信主力连续代码。"""

    @pytest.mark.parametrize("fts_code, expected", [
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
    ])
    def test_mapping(self, fts_code: str, expected: str) -> None:
        """各市场主力连续代码映射正确。"""
        assert _symbol_to_tdx(fts_code) == expected

    def test_ta_not_mistaken_for_t_bond(self) -> None:
        """TA（郑商所 PTA）不得误判为中金所国债 T。"""
        assert _symbol_to_tdx("TA0") == "TAL8.CZC"


# ─── 2. 交易所后缀推断 ────────────────────────────────────


class TestInferExchangeSuffix:
    """测试交易所后缀推断。"""

    @pytest.mark.parametrize("symbol, expected", [
        ("RB0", "SHF"),
        ("CU0", "SHF"),
        ("M0", "DCE"),
        ("I0", "DCE"),
        ("TA0", "CZC"),
        ("MA0", "CZC"),
        ("IF0", "CFF"),
        ("T0", "CFF"),
        ("SC0", "SHF"),  # 能源中心品种未单独映射，回退 SHF
    ])
    def test_suffix(self, symbol: str, expected: str) -> None:
        assert _infer_exchange_suffix(symbol) == expected


# ─── 3. 周期映射 ──────────────────────────────────────────


class TestSupportedPeriods:
    """测试周期映射（60m 在通达信使用 1h 参数）。"""

    def test_all_periods_supported(self) -> None:
        assert SUPPORTED_PERIODS == {
            "1m": "1m", "5m": "5m", "15m": "15m",
            "30m": "30m", "60m": "1h",
        }

    def test_invalid_period_rejected(self) -> None:
        with pytest.raises(ValueError):
            TDXMinuteSource(period="2m")


# ─── 4. 列字典响应解析 ────────────────────────────────────


class TestFetchOhlcvParsing:
    """测试 TQ-Local 列字典返回格式的解析。"""

    def _tq_response(self, symbol: str = "RBL8.SHF") -> dict:
        """构造 TQ-Local 列字典格式响应。"""
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
    def test_parse_column_dict(self, mock_urlopen) -> None:
        """列字典响应应解析为标准分钟级 schema。"""
        resp = self._tq_response()

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(resp).encode("utf-8")

            @property
            def status(self):
                return 200

        mock_urlopen.return_value = FakeResp()

        src = TDXMinuteSource(period="5m")
        df = src.fetch_ohlcv("RB0", days=500, trace_id="test_tdx")
        assert df is not None
        assert len(df) == 2
        assert list(df.columns) == [
            "symbol", "period", "datetime", "open", "high", "low",
            "close", "volume", "source", "fetched_at", "trace_id",
        ]
        assert df["symbol"].iloc[0] == "RB0"
        assert df["period"].iloc[0] == "5m"
        assert df["source"].iloc[0] == "TDX_MINUTE"
        assert df["close"].iloc[0] == 3006.0
        assert pd.Timestamp(df["datetime"].iloc[0]) == pd.Timestamp("2026-08-07 21:40:00")

    @patch("urllib.request.urlopen")
    def test_empty_value_returns_none(self, mock_urlopen) -> None:
        """Value 为空时应返回 None。"""
        resp = {"id": 1, "result": {"Value": {}}}

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(resp).encode("utf-8")

            @property
            def status(self):
                return 200

        mock_urlopen.return_value = FakeResp()

        src = TDXMinuteSource(period="5m")
        assert src.fetch_ohlcv("RB0", days=500, trace_id="t") is None
