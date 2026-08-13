"""
tests/live_trade/test_tqsdk_mhf_executor.py — TqSdk 模拟执行器单测。

覆盖：信号解析、目标手数、主连→主力合约（underlying_symbol）、全流程执行、异常兜底。
tqsdk 全程 mock，不发起真实连接。
"""

from __future__ import annotations

import sys
import types
from datetime import datetime
from typing import Any

import pytest

from fts.live_trade.tqsdk_mhf_executor import (
    ExecConfig,
    TqSdkMhfExecutor,
    is_trading_time,
    parse_signal_directions,
    select_underlying,
    target_lots,
)


# ── 纯函数 ────────────────────────────────────────────────


def test_is_trading_time() -> None:
    # 日盘 09:00-15:00
    assert is_trading_time(datetime(2026, 8, 13, 9, 0)) is True
    assert is_trading_time(datetime(2026, 8, 13, 14, 59)) is True
    assert is_trading_time(datetime(2026, 8, 13, 15, 0)) is False
    assert is_trading_time(datetime(2026, 8, 13, 8, 59)) is False
    # 夜盘 21:00-次日 02:30
    assert is_trading_time(datetime(2026, 8, 13, 20, 59)) is False
    assert is_trading_time(datetime(2026, 8, 13, 21, 0)) is True
    assert is_trading_time(datetime(2026, 8, 14, 2, 29)) is True
    assert is_trading_time(datetime(2026, 8, 14, 2, 30)) is False


def test_parse_signal_directions_filters_and_truncates() -> None:
    payload = {
        "signals": {
            "AG0": {"direction": 1},
            "CU0": {"direction": -1},
            "NI0": {"direction": 0},
            "SN0": {"direction": 1},
            "AU0": {"direction": 0},
        }
    }
    assert parse_signal_directions(payload, 2) == {"AG0": 1, "CU0": -1}
    assert parse_signal_directions(payload, 8) == {"AG0": 1, "CU0": -1, "SN0": 1}


def test_parse_signal_directions_empty() -> None:
    assert parse_signal_directions({}, 8) == {}
    assert parse_signal_directions({"signals": {"AG0": {"direction": 0}}}, 8) == {}


def test_target_lots() -> None:
    # 62500 / (4000 × 10) = 1.56 → 1 手
    assert target_lots(1, 4000.0, 10.0, 62500.0) == 1
    # 125000 / (2500 × 10) = 5 手
    assert target_lots(-1, 2500.0, 10.0, 125000.0) == 5
    # 最小 1 手
    assert target_lots(1, 961.42, 1000.0, 62500.0) == 1
    # 0 方向 / 无效价格 → 0
    assert target_lots(0, 4000.0, 10.0, 62500.0) == 0
    assert target_lots(1, 0.0, 10.0, 62500.0) == 0
    assert target_lots(1, 4000.0, 0.0, 62500.0) == 0


def test_select_underlying() -> None:
    quote_map = {
        "KQ.m@SHFE.ag": {"underlying_symbol": "SHFE.ag2610"},
        "KQ.m@SHFE.cu": {"underlying_symbol": "SHFE.cu2510"},
    }
    tq_map = {"AG0": "KQ.m@SHFE.ag", "CU0": "KQ.m@SHFE.cu"}
    assert select_underlying(quote_map, tq_map) == {
        "AG0": "SHFE.ag2610",
        "CU0": "SHFE.cu2510",
    }


def test_select_underlying_missing() -> None:
    # underlying_symbol 未就绪（空）→ 跳过该品种
    quote_map = {"KQ.m@SHFE.ag": {"underlying_symbol": ""}}
    assert select_underlying(quote_map, {"AG0": "KQ.m@SHFE.ag"}) == {}


# ── Fake TqSdk ─────────────────────────────────────────────


class FakeTargetPosTask:
    def __init__(self, api: Any, symbol: str) -> None:
        self.api = api
        self.symbol = symbol

    def set_target_volume(self, volume: int) -> None:
        pos = self.api.positions[self.symbol]
        if volume > 0:
            pos["volume_long"] = float(volume)
            pos["volume_short"] = 0.0
            pos["open_price_long"] = self.api.prices[self.symbol]
        elif volume < 0:
            pos["volume_short"] = float(-volume)
            pos["volume_long"] = 0.0
            pos["open_price_short"] = self.api.prices[self.symbol]
        else:
            pos["volume_long"] = 0.0
            pos["volume_short"] = 0.0


class FakeApi:
    """模拟 TqApi：主连→主力、行情、持仓、账户均为本地字典。"""

    def __init__(self, underlying: dict[str, str], prices: dict[str, float]) -> None:
        self.underlying = underlying
        self.prices = prices
        self.positions = {
            c: {
                "volume_long": 0.0,
                "volume_short": 0.0,
                "open_price_long": 0.0,
                "open_price_short": 0.0,
            }
            for c in prices
        }

    def get_quote(self, symbol: str) -> dict[str, Any]:
        if symbol in self.underlying:
            main = self.underlying[symbol]
            return {"underlying_symbol": main, "last_price": self.prices.get(main, 0.0)}
        return {"underlying_symbol": "", "last_price": self.prices.get(symbol, 0.0)}

    def wait_update(self, deadline: float | None = None) -> bool:
        return True

    def get_position(self, contract: str) -> dict[str, Any]:
        return self.positions[contract]

    def get_account(self) -> dict[str, Any]:
        return {
            "balance": 1_000_000.0,
            "available": 900_000.0,
            "position_profit": 0.0,
            "close_profit": 0.0,
        }

    def close(self) -> None:
        pass


@pytest.fixture
def fake_api() -> FakeApi:
    underlying = {
        "KQ.m@SHFE.ag": "SHFE.ag2512",
        "KQ.m@SHFE.cu": "SHFE.cu2510",
        "KQ.m@SHFE.ni": "SHFE.ni2609",
        "KQ.m@SHFE.sn": "SHFE.sn2605",
        "KQ.m@SHFE.au": "SHFE.au2512",
    }
    prices = {
        "SHFE.ag2512": 16094.0,
        "SHFE.cu2510": 108080.0,
        "SHFE.ni2609": 129130.0,
        "SHFE.sn2605": 428000.0,
        "SHFE.au2512": 961.42,
    }
    return FakeApi(underlying, prices)


def _make_executor(fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch,
                   **cfg_kw: Any) -> TqSdkMhfExecutor:
    """构造执行器并注入 fake api / fake tqsdk 模块。"""
    exec_cfg = ExecConfig(**cfg_kw)
    executor = TqSdkMhfExecutor(exec_cfg, trace_id="fts.mhf.exec_test")
    monkeypatch.setattr(executor, "_build_api", lambda: fake_api)
    monkeypatch.setattr(
        executor,
        "_symbol_map",
        lambda: {
            "AG0": "KQ.m@SHFE.ag",
            "CU0": "KQ.m@SHFE.cu",
            "NI0": "KQ.m@SHFE.ni",
            "SN0": "KQ.m@SHFE.sn",
            "AU0": "KQ.m@SHFE.au",
        },
    )
    fake_tqsdk = types.ModuleType("tqsdk")
    fake_tqsdk.TargetPosTask = FakeTargetPosTask  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tqsdk", fake_tqsdk)
    return executor


SIGNAL_PAYLOAD = {
    "signal_id": "fts.mhf.test",
    "bar_time": "2026-08-13 23:30:00",
    "signals": {
        "AG0": {"direction": 1, "score": 1.0},
        "CU0": {"direction": 1, "score": 1.0},
        "NI0": {"direction": 1, "score": 1.0},
        "SN0": {"direction": 1, "score": 1.0},
        "AU0": {"direction": 0, "score": 0.0},
    },
}


# ── 全流程 ────────────────────────────────────────────────


def test_run_once_full_flow(fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _make_executor(fake_api, monkeypatch)
    result = executor.run_once(SIGNAL_PAYLOAD)
    assert result["ok"] is True
    assert result["trace_id"] == "fts.mhf.exec_test"
    assert result["signal_id"] == "fts.mhf.test"
    # 4 个活跃品种全部映射为 ≥1 手多头（62500 / 价格×乘数 < 1）
    assert set(result["targets"]) == {"AG0", "CU0", "NI0", "SN0"}
    for t in result["targets"].values():
        assert t["lots"] >= 1
        assert t["direction"] == 1
        assert t["contract"].startswith("SHFE.")
    # 成交后持仓到位
    for t in result["targets"].values():
        assert result["fills"][t["contract"]]["volume_long"] == t["lots"]
    assert result["equity"]["balance"] > 0
    assert result["skipped"] == []


def test_run_once_max_positions_truncate(fake_api: FakeApi,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _make_executor(fake_api, monkeypatch, max_positions=2)
    result = executor.run_once(SIGNAL_PAYLOAD)
    assert result["ok"] is True
    assert set(result["signals"]) == {"AG0", "CU0"}
    assert set(result["targets"]) == {"AG0", "CU0"}


def test_run_once_no_signals(fake_api: FakeApi, monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _make_executor(fake_api, monkeypatch)
    result = executor.run_once({"signal_id": "x", "signals": {"AU0": {"direction": 0}}})
    assert result["ok"] is False
    assert result["signals"] == {}


def test_run_once_unknown_contract_skipped(fake_api: FakeApi,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    executor = _make_executor(fake_api, monkeypatch)
    # XX0 不在主连映射表 → 无合约可映射 → 全部跳过，ok=False
    payload = {"signal_id": "x", "signals": {"XX0": {"direction": 1}}}
    result = executor.run_once(payload)
    assert result["ok"] is False
    assert result["targets"] == {}
    assert result["skipped"] == ["XX0"]


def test_run_once_api_error(monkeypatch: pytest.MonkeyPatch) -> None:
    executor = TqSdkMhfExecutor(ExecConfig(), trace_id="fts.mhf.exec_err")

    def boom() -> Any:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(executor, "_build_api", boom)
    result = executor.run_once(SIGNAL_PAYLOAD)
    assert result["ok"] is False
