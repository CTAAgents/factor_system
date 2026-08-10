"""tests/test_stage5_risk_live.py — C.2 实盘对接测试。

覆盖:
1. SignalValidator 信号契约验证（必填/方向/置信度）
2. to_factor_signal ScoredSignal 转换
3. RiskManager 五项风控规则
4. LiveFactorMonitor 偏离检测
5. Prometheus live/risk 指标渲染
6. HTTP 端点（signal submit / risk status / live factors）
"""

import json

import pytest

from fts.factor_engine.signal_contract import SignalValidator
from fts.monitor.live_factor_monitor import LiveFactorMonitor
from fts.monitor.prometheus_metrics import MetricsRegistry
from fts.risk import RiskManager


def _valid_signal() -> dict:
    return {
        "signal_id": "sig-001",
        "portfolio_id": "p1",
        "timestamp": "2026-08-06T10:00:00",
        "frequency": "1d",
        "universe": ["RB0"],
        "signals": [
            {
                "symbol": "RB0",
                "direction": "long",
                "position": 2.0,
                "confidence": 0.8,
                "price": 3000.0,
                "contributing_factors": [{"factor_id": "f1", "weight": 0.5, "signal": 1.0}],
            }
        ],
        "meta": {"trace_id": "t1", "factor_count": 1},
    }


# ─── 1. SignalValidator ─────────────────────────────────


def test_validator_valid_signal():
    assert SignalValidator().validate(_valid_signal()) == []


def test_validator_missing_required():
    errors = SignalValidator().validate({"signals": []})
    assert any("signal_id" in e for e in errors)
    assert any("timestamp" in e for e in errors)


def test_validator_invalid_direction():
    sig = _valid_signal()
    sig["signals"][0]["direction"] = "buy"
    errors = SignalValidator().validate(sig)
    assert any("direction" in e for e in errors)


def test_validator_confidence_range():
    sig = _valid_signal()
    sig["signals"][0]["confidence"] = 1.5
    errors = SignalValidator().validate(sig)
    assert any("confidence" in e for e in errors)


def test_validator_invalid_frequency():
    sig = _valid_signal()
    sig["frequency"] = "weekly"
    errors = SignalValidator().validate(sig)
    assert any("frequency" in e for e in errors)


def test_validator_negative_position():
    sig = _valid_signal()
    sig["signals"][0]["position"] = -1.0
    errors = SignalValidator().validate(sig)
    assert any("position" in e for e in errors)


# ─── 2. to_factor_signal ────────────────────────────────


def _make_scored(symbol="RB0", direction="bull", total=2.0, grade="STRONG"):
    return type(
        "_S",
        (),
        {
            "symbol": symbol,
            "direction": direction,
            "total": total,
            "grade": grade,
            "price": 3000.0,
            "weight": 1.0,
        },
    )()


def test_to_factor_signal_mapping():
    fs = SignalValidator.to_factor_signal(
        [_make_scored()],
        portfolio_id="p1",
        trace_id="t1",
    )
    assert fs["portfolio_id"] == "p1"
    assert fs["meta"]["trace_id"] == "t1"
    assert fs["signals"][0]["direction"] == "long"
    assert fs["signals"][0]["confidence"] == 0.9
    assert SignalValidator().validate(fs) == []


def test_to_factor_signal_bear_neutral():
    fs = SignalValidator.to_factor_signal(
        [_make_scored(direction="bear", grade="WEAK"), _make_scored(symbol="CU0", direction="neutral", grade="NOISE")],
        trace_id="t2",
    )
    dirs = {s["symbol"]: s["direction"] for s in fs["signals"]}
    assert dirs["RB0"] == "short"
    assert dirs["CU0"] == "flat"


# ─── 3. RiskManager ─────────────────────────────────────


def _account(equity=1_000_000, peak=1_000_000, daily_pnl=0, position_value=0):
    return {
        "total_equity": equity,
        "balance": equity,
        "peak_equity": peak,
        "daily_pnl": daily_pnl,
        "position_value": position_value,
    }


def test_risk_approve_normal_signal():
    sig = _valid_signal()  # 2 手 × 3000 = 6000 < 10% × 1M
    result = RiskManager().check(sig, _account(), {})
    assert result["approved"] is True
    assert len(result["blocking_violations"]) == 0
    assert len(result["checks"]) == 5


def test_risk_single_position_limit():
    sig = _valid_signal()
    sig["signals"][0]["position"] = 100.0  # 100 × 3000 = 300k > 100k (10%)
    result = RiskManager().check(sig, _account(), {})
    assert result["approved"] is False
    names = [c["check_name"] for c in result["blocking_violations"]]
    assert "single_position_limit" in names


def test_risk_leverage_limit():
    sig = _valid_signal()
    sig["signals"][0]["position"] = 500.0  # 500 × 3000 = 1.5M > 3M? no, 1.5M/1M = 1.5x
    sig["signals"].append(
        {
            "symbol": "CU0",
            "direction": "long",
            "position": 400.0,
            "confidence": 0.5,
            "price": 6000.0,  # 2.4M
        }
    )
    # 总市值 = 1.5M + 2.4M = 3.9M > 3x × 1M → 拦截
    result = RiskManager().check(sig, _account(), {})
    assert result["approved"] is False
    names = [c["check_name"] for c in result["blocking_violations"]]
    assert "leverage_limit" in names


def test_risk_portfolio_drawdown():
    sig = _valid_signal()
    acct = _account(equity=700_000, peak=1_000_000)  # 回撤 -30% > 20%
    result = RiskManager().check(sig, acct, {})
    assert result["approved"] is False
    names = [c["check_name"] for c in result["blocking_violations"]]
    assert "portfolio_drawdown" in names


def test_risk_daily_loss():
    sig = _valid_signal()
    acct = _account(equity=1_000_000, daily_pnl=-100_000)  # 亏损 10% > 5%
    result = RiskManager().check(sig, acct, {})
    assert result["approved"] is False
    names = [c["check_name"] for c in result["blocking_violations"]]
    assert "daily_loss_limit" in names


def test_risk_concentration():
    sig = _valid_signal()
    sig["signals"] = [
        {"symbol": f"s{i}", "direction": "long", "position": 1.0, "confidence": 0.5, "price": 400_000.0}  # 每只 400k
        for i in range(3)
    ]
    # 总 1.2M，前3大 = 全部 → 集中度 100% > 50%
    result = RiskManager().check(sig, _account(), {})
    assert result["approved"] is False
    names = [c["check_name"] for c in result["blocking_violations"]]
    assert "concentration_limit" in names


# ─── 6. LiveFactorMonitor ───────────────────────────────


def test_live_monitor_no_deviation():
    monitor = LiveFactorMonitor()
    monitor.set_backtest_baseline("f1", {"ic": 0.05, "sharpe": 2.0})
    monitor.update_live_performance("f1", {"ic": 0.05, "sharpe": 2.0})
    assert monitor.check_deviation() == []


def test_live_monitor_deviation_alert():
    monitor = LiveFactorMonitor()
    monitor.set_backtest_baseline("f1", {"ic": 0.05})
    monitor.update_live_performance("f1", {"ic": 0.01})  # 偏离 80% > 30%
    alerts = monitor.check_deviation()
    assert len(alerts) == 1
    assert alerts[0]["metric"] == "ic"
    assert alerts[0]["severity"] == "critical"
    assert alerts[0]["deviation_pct"] > 0.3


def test_live_monitor_get_deviation_report():
    monitor = LiveFactorMonitor()
    monitor.set_backtest_baseline("f1", {"ic": 0.05})
    monitor.update_live_performance("f1", {"ic": 0.04})  # 20% 偏离
    report = monitor.get_factor_deviation("f1")
    assert report["overall_status"] == "normal"
    assert len(report["deviations"]) == 1
    assert report["deviations"][0]["severity"] == "normal"


def test_live_monitor_threshold():
    monitor = LiveFactorMonitor({"deviation_threshold_pct": 0.10})
    monitor.set_backtest_baseline("f1", {"ic": 0.05})
    monitor.update_live_performance("f1", {"ic": 0.04})  # 20% > 10% 阈值
    alerts = monitor.check_deviation()
    assert len(alerts) == 1


# ─── 7. Prometheus live/risk 指标 ───────────────────────


def test_metrics_registry_live():
    reg = MetricsRegistry()
    reg.update_live_factor("f1", {"ic": 0.05, "sharpe": 2.0})
    reg.record_live_deviation_alert("f1", "critical")
    reg.record_risk_check("leverage_limit", "blocked")
    reg.record_risk_check("leverage_limit", "passed")

    lines = "\n".join(reg.render())
    assert 'fts_live_factor_ic{factor_id="f1"} 0.05' in lines
    assert 'fts_live_factor_sharpe{factor_id="f1"} 2.0' in lines
    assert 'fts_live_factor_deviation_alerts_total{factor_id="f1",severity="critical"} 1' in lines
    assert 'fts_risk_check_total{check_name="leverage_limit",result="blocked"} 1' in lines
    assert 'fts_risk_check_blocked_total{check_name="leverage_limit"} 1' in lines


# ─── 8. HTTP 端点 ───────────────────────────────────────


@pytest.fixture()
def server():
    from fts.monitor.http_server import FTSDashboardServer
    import socket

    FTSDashboardServer(port=0)
    # 手动绑定随机端口
    import threading
    from http.server import HTTPServer
    from fts.monitor.http_server import _DashboardHandler

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    httpd = HTTPServer(("127.0.0.1", port), _DashboardHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _http_get(url: str) -> dict:
    import urllib.request

    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_post(url: str, data: dict) -> tuple[int, dict]:
    import urllib.request

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def test_http_risk_status(server):
    data = _http_get(f"{server}/api/v1/risk/status")
    assert "risk_level" in data
    assert "positions" in data


def test_http_signal_submit_valid(server):
    status, data = _http_post(f"{server}/api/v1/signal/submit", _valid_signal())
    assert status == 200
    assert data["approved"] is True
    assert data["order"]["status"] == "filled"


def test_http_signal_submit_invalid(server):
    status, data = _http_post(f"{server}/api/v1/signal/submit", {"signals": []})
    assert status == 422
    assert data["approved"] is False


def test_http_live_factors(server):
    data = _http_get(f"{server}/api/v1/live/factors")
    assert "factors" in data
    assert "alerts" in data
