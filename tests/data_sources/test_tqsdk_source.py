"""tests/data_sources/test_tqsdk_source.py — 天勤 TQSDK 数据源适配器测试。

覆盖:
    1. 周期校验 / 探活 / 品种映射
    2. fetch_ohlcv 全路径（未安装 / 匿名 / 认证 / 空数据 / 缺字段 / 正常 / 异常）
    3. fetch_quote 占位返回 None
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.data_sources.base import SourceUnavailable  # noqa: E402
from fts.data_sources.tqsdk_source import SUPPORTED_PERIODS, TQSDKSource, _SYMBOL_MAP  # noqa: E402


# ─── fake tqsdk 模块 ───────────────────────────────────────


class FakeTqApi:
    """模拟 TqApi。"""

    def __init__(self, auth=None):
        self.auth = auth
        self.closed = False
        self.kline_result = None
        self.wait_exception = None

    def get_kline_serial(self, symbol, duration_seconds, data_length):
        return self.kline_result

    def wait_update(self, deadline=None):
        if self.wait_exception:
            raise self.wait_exception

    def close(self):
        self.closed = True


class FakeTqAuth:
    def __init__(self, user, pwd):
        self.user = user
        self.pwd = pwd


def _install_fake_tqsdk(monkeypatch, api: FakeTqApi | None = None):
    fake = types.ModuleType("tqsdk")
    fake.TqAuth = FakeTqAuth

    def _factory(auth=None):
        # 模拟真实 TqApi(auth=...)：显式传入 api 实例时同样注入 auth
        if api is not None:
            api.auth = auth
            return api
        return FakeTqApi(auth=auth)

    fake.TqApi = _factory
    monkeypatch.setitem(sys.modules, "tqsdk", fake)
    return fake


def _block_tqsdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "tqsdk", None)


def _make_kline_df(n: int = 30, include_datetime: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    data = {
        "open": rng.uniform(3000, 3100, n),
        "high": rng.uniform(3100, 3200, n),
        "low": rng.uniform(2900, 3000, n),
        "close": rng.uniform(2950, 3150, n),
        "volume": rng.integers(100, 1000, n).astype(float),
    }
    if include_datetime:
        # 纳秒时间戳（TQSDK 原生格式）
        start = pd.Timestamp("2026-08-01 09:00").value
        data["datetime"] = [start + i * 60_000_000_000 for i in range(n)]
    return pd.DataFrame(data)


# ─── 基础行为 ──────────────────────────────────────────────


class TestBasics:
    def test_supported_periods_include_day(self):
        assert SUPPORTED_PERIODS["day"] == 86400
        assert SUPPORTED_PERIODS["1m"] == 60

    def test_valid_periods(self):
        for p in SUPPORTED_PERIODS:
            src = TQSDKSource(period=p)
            assert src.period == p

    def test_invalid_period_raises(self):
        with pytest.raises(ValueError, match="不支持的周期"):
            TQSDKSource(period="weekly")

    def test_is_available_true(self, monkeypatch):
        _install_fake_tqsdk(monkeypatch)
        assert TQSDKSource().is_available() is True

    def test_is_available_false(self, monkeypatch):
        _block_tqsdk(monkeypatch)
        assert TQSDKSource().is_available() is False

    def test_resolve_known_symbol(self):
        assert TQSDKSource()._resolve_symbol("rb0") == "KQ.m@SHFE.rb"
        assert TQSDKSource()._resolve_symbol("RB0") == "KQ.m@SHFE.rb"

    def test_resolve_unknown_symbol_returns_raw(self):
        assert TQSDKSource()._resolve_symbol("XYZ0") == "XYZ0"

    def test_fetch_quote_returns_none(self):
        assert TQSDKSource().fetch_quote("RB0") is None


# ─── fetch_ohlcv ───────────────────────────────────────────


class TestFetchOhlcv:
    def test_tqsdk_not_installed_returns_none(self, monkeypatch):
        _block_tqsdk(monkeypatch)
        assert TQSDKSource().fetch_ohlcv("RB0") is None

    def test_anonymous_access_without_env(self, monkeypatch):
        monkeypatch.delenv("TQSDK_USERNAME", raising=False)
        monkeypatch.delenv("TQSDK_PASSWORD", raising=False)
        api = FakeTqApi()
        api.kline_result = _make_kline_df()
        _install_fake_tqsdk(monkeypatch, api)
        df = TQSDKSource("day").fetch_ohlcv("RB0", days=500)
        assert df is not None
        assert api.auth is None  # 匿名
        assert api.closed is True  # 连接已关闭

    def test_auth_access_with_env(self, monkeypatch):
        monkeypatch.setenv("TQSDK_USERNAME", "user1")
        monkeypatch.setenv("TQSDK_PASSWORD", "pass1")
        api = FakeTqApi()
        api.kline_result = _make_kline_df()
        _install_fake_tqsdk(monkeypatch, api)
        df = TQSDKSource().fetch_ohlcv("RB0")
        assert df is not None
        assert isinstance(api.auth, FakeTqAuth)
        assert api.auth.user == "user1"

    def test_empty_kline_returns_none(self, monkeypatch):
        monkeypatch.delenv("TQSDK_USERNAME", raising=False)
        api = FakeTqApi()
        api.kline_result = pd.DataFrame()
        _install_fake_tqsdk(monkeypatch, api)
        assert TQSDKSource().fetch_ohlcv("RB0") is None

    def test_missing_required_fields_returns_none(self, monkeypatch):
        api = FakeTqApi()
        api.kline_result = pd.DataFrame({"open": [1.0], "close": [1.0]})  # 缺 high/low/volume
        _install_fake_tqsdk(monkeypatch, api)
        assert TQSDKSource().fetch_ohlcv("RB0") is None

    def test_exception_raises_source_unavailable(self, monkeypatch):
        api = FakeTqApi()
        api.wait_exception = RuntimeError("network down")
        _install_fake_tqsdk(monkeypatch, api)
        with pytest.raises(SourceUnavailable, match="获取失败"):
            TQSDKSource().fetch_ohlcv("RB0")

    def test_normal_df_standardized(self, monkeypatch):
        monkeypatch.delenv("TQSDK_USERNAME", raising=False)
        api = FakeTqApi()
        api.kline_result = _make_kline_df(n=30)
        _install_fake_tqsdk(monkeypatch, api)
        df = TQSDKSource("day").fetch_ohlcv("RB0", days=500, trace_id="tq-1")
        assert df is not None
        assert df["symbol"].iloc[0] == "RB0"
        assert df["period"].iloc[0] == "day"
        assert df["source"].iloc[0] == "TQSDK"
        assert df["trace_id"].iloc[0] == "tq-1"
        assert pd.api.types.is_datetime64_any_dtype(df["datetime"])
        assert df["datetime"].is_monotonic_increasing
        assert df["close"].dtype == np.float64

    def test_datetime_from_index_when_missing(self, monkeypatch):
        monkeypatch.delenv("TQSDK_USERNAME", raising=False)
        api = FakeTqApi()
        df = _make_kline_df(n=20, include_datetime=False)
        df.index = pd.date_range("2026-08-01", periods=20, freq="D")
        df["symbol"] = "RB0"  # 产品 fallback 分支要求存在 symbol 列
        api.kline_result = df
        _install_fake_tqsdk(monkeypatch, api)
        result = TQSDKSource().fetch_ohlcv("RB0")
        assert result is not None
        assert pd.api.types.is_datetime64_any_dtype(result["datetime"])

    def test_tail_truncation(self, monkeypatch):
        monkeypatch.delenv("TQSDK_USERNAME", raising=False)
        api = FakeTqApi()
        api.kline_result = _make_kline_df(n=100)
        _install_fake_tqsdk(monkeypatch, api)
        df = TQSDKSource().fetch_ohlcv("RB0", days=20)
        assert len(df) == 20

    def test_symbol_map_coverage(self):
        # 关键品种映射抽查
        assert _SYMBOL_MAP["RB0"] == "KQ.m@SHFE.rb"
        assert _SYMBOL_MAP["SC0"] == "KQ.m@INE.sc"
        assert _SYMBOL_MAP["IF0"] == "KQ.m@CFFEX.IF"
        assert _SYMBOL_MAP["TA0"] == "KQ.m@CZCE.TA"
        assert _SYMBOL_MAP["M0"] == "KQ.m@DCE.m"
        # 映射表规模
        assert len(_SYMBOL_MAP) > 70


# ─── GAP-140⑤ 兼容层：TqApi 运行期 logger ─────────────────


class TestTqCompatLogger:
    """兼容层验证：tqsdk 运行期 logger 接受裸 kwargs（api.py:3612 / auth.py:114）。

    ``_import_tqsdk_safe`` 还原 loggerClass 后，TqApi 内部
    ``debug("process start", product=..., version=...)`` 等调用携带自定义 kwargs，
    标准 ``logging.Logger._log`` 不接受 → 必须由兼容子类兜住。
    """

    def test_import_restores_compat_logger_class(self, monkeypatch):
        import logging

        _install_fake_tqsdk(monkeypatch)
        snap_cls = logging.getLoggerClass()
        try:
            from fts.data_sources.tqsdk_source import _TqCompatLogger, _import_tqsdk_safe

            _import_tqsdk_safe()
            # loggerClass 还原为兼容子类（标准 Logger 子类，行为一致）
            assert logging.getLoggerClass() is _TqCompatLogger
            assert issubclass(_TqCompatLogger, logging.Logger)

            # 模拟 api.py:229 强制 DEBUG + api.py:3612 裸 kwargs 调用，不抛 TypeError
            tq_logger = logging.getLogger("TqApi")
            tq_logger.setLevel(logging.DEBUG)
            tq_logger.debug(
                "process start",
                product="tqsdk-python",
                version="3.10.2",
                os="Windows",
                py_version="3.12",
            )
            # 模拟 auth.py:114（ShinnyLoggerAdapter 透传 user_name 裸 kwargs）
            tq_auth_logger = tq_logger.getChild("TqAuth")
            tq_auth_logger.setLevel(logging.DEBUG)
            tq_auth_logger.debug("login", user_name="tq_user")
        finally:
            logging.setLoggerClass(snap_cls)
