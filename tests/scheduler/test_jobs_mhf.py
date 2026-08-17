"""
tests/scheduler/test_jobs_mhf.py — mhf_signal_job 信号后串行模拟执行。

覆盖：信号发布 → 串行执行被调用并落盘、无信号跳过执行、执行异常不影响信号任务。
全程 mock（SignalBridge / 信号管道 / TqSdkMhfExecutor），不发起真实连接。
"""

from __future__ import annotations

from typing import Any

import pytest

import fts.scheduler.jobs as jobs_mod


class FakeSignalBridge:
    published: list[Any] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def publish(self, payload: dict[str, Any]) -> None:
        FakeSignalBridge.published.append(payload)


class FakeExecutor:
    instances: list["FakeExecutor"] = []

    def __init__(self, config: Any, trace_id: str = "") -> None:
        self.config = config
        self.trace_id = trace_id
        FakeExecutor.instances.append(self)

    def run_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "targets": {"AG0": {"contract": "SHFE.ag2610", "lots": 1}},
            "equity": {"balance": 999870.02},
            "trace_id": self.trace_id,
        }


@pytest.fixture
def patch_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSignalBridge.published.clear()
    FakeExecutor.instances.clear()
    # GAP-140③ 全局默认市场 energy：本测试组只测信号发布/执行逻辑，不测 market gate
    monkeypatch.setattr(jobs_mod, "_market_gate", lambda market, *, task: True)

    def _fake_generate(trace_id: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "signal_id": f"sig_{trace_id}",
            "symbols": 1,
            "bar_time": "2026-08-13 23:30:00",
            "signals": {"AG0": {"direction": 1, "score": 1.0, "last_close": 16024.0}},
        }

    monkeypatch.setattr("scripts.mhf_signal_pipeline.generate_mhf_signals", _fake_generate)
    monkeypatch.setattr("fts.bridge.signal_bridge.SignalBridge", FakeSignalBridge)
    monkeypatch.setattr("fts.live_trade.tqsdk_mhf_executor.TqSdkMhfExecutor", FakeExecutor)
    # 固定为交易时段（真实时间无关），非交易时段用例单独 override
    monkeypatch.setattr("fts.live_trade.tqsdk_mhf_executor.is_trading_time", lambda now: True)


def test_signal_job_serial_exec_and_artifact(
    monkeypatch: pytest.MonkeyPatch, patch_deps: None, tmp_path: Any
) -> None:
    monkeypatch.setattr(jobs_mod, "PROJECT_ROOT", tmp_path)
    jobs_mod.mhf_signal_job()
    # 信号已发布
    assert len(FakeSignalBridge.published) == 1
    # 串行执行已调用
    assert len(FakeExecutor.instances) == 1
    assert FakeExecutor.instances[0].trace_id.endswith("_exec")
    # 留痕落盘到 reports/mhf/tqsdk_exec_job_*.json
    artifacts = list((tmp_path / "reports" / "mhf").glob("tqsdk_exec_job_*.json"))
    assert len(artifacts) == 1


def test_signal_job_no_signal_skips_exec(monkeypatch: pytest.MonkeyPatch,
                                         patch_deps: None, tmp_path: Any) -> None:
    monkeypatch.setattr(jobs_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        "scripts.mhf_signal_pipeline.generate_mhf_signals",
        lambda trace_id="": {"ok": False},
    )
    jobs_mod.mhf_signal_job()
    assert FakeSignalBridge.published == []
    assert FakeExecutor.instances == []


def test_signal_job_skips_exec_outside_trading_hours(monkeypatch: pytest.MonkeyPatch,
                                                      patch_deps: None, tmp_path: Any) -> None:
    monkeypatch.setattr(jobs_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr("fts.live_trade.tqsdk_mhf_executor.is_trading_time",
                        lambda now: False)
    jobs_mod.mhf_signal_job()
    # 信号仍发布；模拟执行被跳过
    assert len(FakeSignalBridge.published) == 1
    assert FakeExecutor.instances == []


def test_signal_job_exec_error_keeps_signal_ok(monkeypatch: pytest.MonkeyPatch,
                                               patch_deps: None, tmp_path: Any) -> None:
    monkeypatch.setattr(jobs_mod, "PROJECT_ROOT", tmp_path)

    def _boom(self: Any, payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("tq connection refused")

    monkeypatch.setattr("fts.live_trade.tqsdk_mhf_executor.TqSdkMhfExecutor.run_once", _boom)
    jobs_mod.mhf_signal_job()
    # 信号任务整体成功（无异常上抛），信号已发布
    assert len(FakeSignalBridge.published) == 1
