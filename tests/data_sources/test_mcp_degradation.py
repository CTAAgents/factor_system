"""tests/data_sources/test_mcp_degradation.py — 数据源降级加固测试（GAP-F04，v2.60.0）。

覆盖:
1. 未启用 MCP（默认）→ _call_mcp 返回 None、is_available False、fetch 降级不抛异常
2. 启用但未注入 → _call_mcp 抛 RuntimeError（显式初始化报错）
3. 注入 handler → 正常调用、is_available True
4. set_mcp_handler(None) 恢复默认（防全局状态泄漏）
"""

import pytest
from unittest.mock import MagicMock

from fts.config.settings import FTSConfig
from fts.data_sources import ifind_source, wind_source
from fts.data_sources.ifind_source import IFindSource
from fts.data_sources.wind_source import WindSource


@pytest.fixture(autouse=True)
def _reset_mcp_handlers():
    """每个测试前后重置两个数据源的 MCP handler，防全局状态泄漏。"""
    ifind_source._mcp_handler = None
    wind_source._mcp_handler = None
    yield
    ifind_source._mcp_handler = None
    wind_source._mcp_handler = None


@pytest.fixture
def _mcp_disabled(monkeypatch):
    """默认配置：mcp_enabled=false。"""
    monkeypatch.setattr(
        "fts.config.settings.get_config",
        lambda: FTSConfig(mcp_enabled=False),
    )


@pytest.fixture
def _mcp_enabled(monkeypatch):
    """mcp_enabled=true 但未注入。"""
    monkeypatch.setattr(
        "fts.config.settings.get_config",
        lambda: FTSConfig(mcp_enabled=True),
    )


# ─── 未启用（默认）→ 明确降级 ────────────────────────────


def test_call_mcp_disabled_returns_none(_mcp_disabled):
    """未启用 MCP 时 _call_mcp 应返回 None（明确降级，不抛异常）。"""
    assert ifind_source._call_mcp("查询") is None
    assert wind_source._call_mcp("查询") is None


def test_is_available_disabled_false(_mcp_disabled):
    """未启用 MCP 时 is_available 应为 False。"""
    assert IFindSource().is_available() is False
    assert WindSource().is_available() is False


def test_fetch_ohlcv_disabled_degrades(_mcp_disabled):
    """未启用 MCP 时 fetch_ohlcv 应降级返回 None 而非抛 SourceUnavailable。"""
    assert IFindSource().fetch_ohlcv("RB2509", 10) is None
    assert WindSource().fetch_ohlcv("RB2509", 10) is None


# ─── 启用但未注入 → 显式报错 ─────────────────────────────


def test_call_mcp_enabled_without_handler_raises(_mcp_enabled):
    """mcp_enabled=true 但未注入时应抛 RuntimeError（提示初始化）。"""
    with pytest.raises(RuntimeError, match="未注入"):
        ifind_source._call_mcp("查询")
    with pytest.raises(RuntimeError, match="未注入"):
        wind_source._call_mcp("查询")


def test_is_available_enabled_without_handler_false(_mcp_enabled):
    """启用但未注入时 is_available 应为 False。"""
    assert IFindSource().is_available() is False
    assert WindSource().is_available() is False


# ─── 注入 handler → 正常调用 ─────────────────────────────


def test_call_mcp_after_handler_injected(_mcp_disabled):
    """注入 handler 后 _call_mcp 应调用 handler（即使 mcp_enabled=false）。"""
    handler = MagicMock(return_value={"ok": True})
    ifind_source.set_mcp_handler(handler)
    result = ifind_source._call_mcp("查询")
    assert result == {"ok": True}
    handler.assert_called_once_with("查询")


def test_is_available_after_handler_injected(_mcp_disabled):
    """注入 handler 后 is_available 应为 True。"""
    handler = MagicMock(return_value={"ok": True})
    wind_source.set_mcp_handler(handler)
    assert WindSource().is_available() is True


def test_fetch_ohlcv_with_handler_parses(_mcp_disabled):
    """注入 handler 后 fetch_ohlcv 应正常解析 MCP 响应。"""
    raw = {
        "data": [
            {
                "date": "2026-01-02",
                "open": 3000.0,
                "high": 3010.0,
                "low": 2990.0,
                "close": 3005.0,
                "volume": 1000,
                "amount": 3_000_000.0,
                "openInterest": 100,
                "settle": 3004.0,
            }
        ]
    }
    ifind_source.set_mcp_handler(MagicMock(return_value=raw))
    df = IFindSource().fetch_ohlcv("RB2509", 10)
    assert df is not None
    assert df["close"].iloc[0] == pytest.approx(3005.0)
    assert df["hold"].iloc[0] == pytest.approx(100.0)


def test_reset_handler_restores_disabled(_mcp_disabled):
    """set_mcp_handler(None) 应恢复默认（未注入 → 降级）。"""
    ifind_source.set_mcp_handler(MagicMock(return_value={"ok": True}))
    assert ifind_source._call_mcp("查询") == {"ok": True}
    ifind_source.set_mcp_handler(None)
    assert ifind_source._call_mcp("查询") is None
    assert IFindSource().is_available() is False
