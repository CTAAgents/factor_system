"""
tests/monitor/test_live_factor_monitor.py — GAP-I402 在线因子性能监控测试（v2.77.0）。

覆盖:
    - LiveFactorMonitor 既有偏离检查（IC/Sharpe/最大回撤 偏离告警，阈值分级）
    - GAP-I402 ingest_live_ic: 消费 GAP-I401 实盘 IC 结果（compute_live_ic 输出 +
      backtest_ic_map + decay_status_map），自动构建基线/实盘并产出偏离+衰减告警
    - 衰减告警: decayed → critical（建议退役）/ weak → warning（持续观察）/
      ok 无告警/decay_alert_enabled=False 关闭
    - Prometheus 兼容指标日志（live_factor_ic / live_factor_decay）
    - 空数据降级（ingest 空结果不告警不报错）

版本: v1.0.0
"""

from __future__ import annotations

import logging

from fts.monitor.live_factor_monitor import LiveFactorMonitor


# ─── 既有偏离检查 ─────────────────────────────────────────


class TestDeviation:
    def test_no_alerts_without_deviation(self):
        mon = LiveFactorMonitor()
        mon.set_backtest_baseline("f1", {"ic": 0.05, "sharpe": 1.5})
        mon.update_live_performance("f1", {"ic": 0.055, "sharpe": 1.5})
        assert mon.check_deviation() == []

    def test_warning_on_moderate_deviation(self):
        mon = LiveFactorMonitor({"deviation_threshold_pct": 0.30})
        mon.set_backtest_baseline("f1", {"ic": 0.05})
        mon.update_live_performance("f1", {"ic": 0.005})  # 偏离 90% > 30%
        alerts = mon.check_deviation()
        assert len(alerts) == 1
        assert alerts[0]["metric"] == "ic"
        assert alerts[0]["severity"] in ("warning", "critical")

    def test_critical_on_large_deviation(self):
        mon = LiveFactorMonitor({"deviation_threshold_pct": 0.30})
        mon.set_backtest_baseline("f1", {"ic": 0.05})
        mon.update_live_performance("f1", {"ic": -0.05})  # 偏离 200% > 45%
        alerts = mon.check_deviation()
        assert alerts[0]["severity"] == "critical"

    def test_get_factor_deviation_report(self):
        mon = LiveFactorMonitor()
        mon.set_backtest_baseline("f1", {"ic": 0.05})
        mon.update_live_performance("f1", {"ic": 0.01})
        report = mon.get_factor_deviation("f1")
        assert report["factor_id"] == "f1"
        assert report["deviations"][0]["metric"] == "ic"

    def test_get_factor_ids(self):
        mon = LiveFactorMonitor()
        mon.update_live_performance("f1", {"ic": 0.01})
        mon.update_live_performance("f2", {"ic": 0.02})
        assert sorted(mon.get_factor_ids()) == ["f1", "f2"]


# ─── GAP-I402 ingest_live_ic ──────────────────────────────


def _live_ic_result() -> dict:
    return {
        "factors": {
            "f_decayed": {"ic": -0.01, "n_days": 20, "mean_return": -0.001},
            "f_ok": {"ic": 0.04, "n_days": 20, "mean_return": 0.001},
            "f_no_bt": {"ic": 0.03, "n_days": 10, "mean_return": 0.0},
        },
        "overall_ic": 0.02,
        "n_records": 200,
    }


class TestIngestLiveIc:
    def test_ingest_builds_baseline_and_live(self):
        mon = LiveFactorMonitor()
        alerts = mon.ingest_live_ic(
            _live_ic_result(),
            backtest_ic_map={"f_decayed": 0.06, "f_ok": 0.05},
        )
        # 回测基线已写入
        assert mon._backtest["f_decayed"]["ic"] == 0.06
        assert mon._backtest["f_ok"]["ic"] == 0.05
        # 实盘指标已写入
        assert mon._live["f_decayed"]["ic"] == -0.01
        assert mon._live["f_no_bt"]["ic"] == 0.03
        # 无回测基线因子不产生偏离告警（跳过检查）
        assert all(a["factor_id"] != "f_no_bt" for a in alerts)

    def test_ingest_with_decay_status_alerts(self):
        mon = LiveFactorMonitor()
        alerts = mon.ingest_live_ic(
            _live_ic_result(),
            backtest_ic_map={"f_decayed": 0.06, "f_ok": 0.05},
            decay_status_map={
                "f_decayed": "decayed",
                "f_ok": "ok",
                "f_no_bt": "weak",
            },
        )
        decay_alerts = [a for a in alerts if a["metric"] == "decay"]
        assert len(decay_alerts) == 2  # decayed + weak
        decayed = [a for a in decay_alerts if a["factor_id"] == "f_decayed"][0]
        assert decayed["severity"] == "critical"
        assert "退役" in decayed["recommendation"]
        weak = [a for a in decay_alerts if a["factor_id"] == "f_no_bt"][0]
        assert weak["severity"] == "warning"

    def test_decay_alert_disabled(self):
        mon = LiveFactorMonitor({"decay_alert_enabled": False})
        mon.set_decay_status("f1", "decayed")
        assert mon._check_decay_alerts() == []

    def test_set_get_decay_status(self):
        mon = LiveFactorMonitor()
        assert mon.get_decay_status("f1") is None
        mon.set_decay_status("f1", "weak")
        assert mon.get_decay_status("f1") == "weak"

    def test_ingest_empty_result_degrade(self):
        mon = LiveFactorMonitor()
        assert mon.ingest_live_ic({}) == []
        assert mon.ingest_live_ic({"factors": {}, "n_records": 0}) == []

    def test_prometheus_metric_logs(self, caplog):
        mon = LiveFactorMonitor()
        with caplog.at_level(logging.INFO, logger="fts.monitor.live_factor_monitor"):
            mon.ingest_live_ic(
                _live_ic_result(),
                backtest_ic_map={"f_decayed": 0.06, "f_ok": 0.05},
                decay_status_map={
                    "f_decayed": "decayed",
                    "f_ok": "ok",
                    "f_no_bt": "weak",
                },
            )
        log_text = "\n".join(caplog.messages)
        assert "METRIC live_factor_ic{" in log_text
        assert "METRIC live_factor_decay{" in log_text


# ─── GAP-I401 端到端对接 ─────────────────────────────────


class TestGapI401Integration:
    def test_ingest_from_compute_live_ic_and_report(self, tmp_path):
        """LiveFeedbackImporter.compute_live_ic + LiveVsBacktestICReport.generate
        输出可直接被 LiveFactorMonitor.ingest_live_ic 消费。"""
        from fts.factor_engine.feedback_loop import (
            LiveFeedbackImporter,
            LiveVsBacktestICReport,
        )

        importer = LiveFeedbackImporter()
        importer.import_records(
            [
                {
                    "factor_id": "f1",
                    "signal_date": "2026-07-01",
                    "signal_value": 1.0,
                    "position_return": 0.02,
                    "turnover": 0.1,
                    "slippage": 0.0001,
                    "market": "futures",
                },
                {
                    "factor_id": "f1",
                    "signal_date": "2026-07-02",
                    "signal_value": -0.5,
                    "position_return": -0.005,
                    "turnover": 0.1,
                    "slippage": 0.0001,
                    "market": "futures",
                },
            ]
        )
        live_ic = importer.compute_live_ic()
        report = LiveVsBacktestICReport().generate(live_ic, {"f1": 0.05})
        status_map = {r["factor_id"]: r["status"] for r in report["factors"]}

        mon = LiveFactorMonitor()
        alerts = mon.ingest_live_ic(live_ic, {"f1": 0.05}, status_map)
        # f1 实盘 IC 已写入（时序回退计算，数值有限）
        assert "f1" in mon._live
        # 状态映射已消费
        assert mon.get_decay_status("f1") == status_map["f1"]
        # 告警为偏离 + 衰减告警列表
        assert isinstance(alerts, list)
