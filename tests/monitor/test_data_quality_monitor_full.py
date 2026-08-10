"""tests/monitor/test_data_quality_monitor_full.py — 数据质量监控器补充测试。

覆盖 test_data_quality_monitor.py 之外的路径:
    - check 的 IC 漂移/容量突变全分支（critical/warning/无告警/冷却）
    - validate_market_data 全分支（空/缺列/缺失率/forward_returns）
    - 指标函数边界（空数据/零方差/缺失字段）
    - evaluate_source_data 汇总评估
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.monitor.data_quality_monitor import (  # noqa: E402
    DataQualityMonitor,
    MonitorConfig,
    QualityAlert,
    compute_cache_hit_ratio,
    compute_coverage_ratio,
    compute_cross_source_deviation,
    compute_data_drift_rate,
    compute_field_completeness,
    compute_freshness,
    compute_jump_detection,
    compute_missing_ratio,
    compute_outlier_ratio,
    compute_timestamp_continuity,
    compute_update_delay,
    create_default_monitor,
    evaluate_source_data,
)


# ─── QualityAlert / 基础 ───────────────────────────────────


class TestQualityAlert:
    def test_to_dict_fields(self):
        alert = QualityAlert(
            factor_id="f1",
            alert_type="ic_drift",
            severity="warning",
            message="msg",
            metric_name="IC",
            metric_value=0.1,
            baseline_value=0.05,
            threshold=2.0,
            timestamp=123.0,
        )
        d = alert.to_dict()
        assert d["factor_id"] == "f1"
        assert d["severity"] == "warning"
        assert d["timestamp"] == 123.0

    def test_default_timestamp(self):
        alert = QualityAlert(
            factor_id="f1",
            alert_type="ic_drift",
            severity="warning",
            message="m",
            metric_name="IC",
            metric_value=0.1,
            baseline_value=0.05,
            threshold=2.0,
        )
        assert alert.timestamp > 0


class TestCreateDefaultMonitor:
    def test_create(self):
        m = create_default_monitor()
        assert isinstance(m, DataQualityMonitor)
        assert isinstance(m._config, MonitorConfig)


# ─── DataQualityMonitor ────────────────────────────────────


class TestMonitorCore:
    def _make_monitor(self, **kw) -> DataQualityMonitor:
        return DataQualityMonitor(**kw)

    def test_init_defaults(self):
        m = self._make_monitor()
        assert isinstance(m._config, MonitorConfig)
        assert m._baselines == {}

    def test_register_and_list(self):
        m = self._make_monitor()
        m.register_factor("f1", baseline_ic=0.05, baseline_capacity=1e7)
        assert m.list_registered() == ["f1"]
        m.register_factor("f1", baseline_ic=0.06, baseline_capacity=1e7)  # 覆盖
        assert len(m.list_registered()) == 1

    def test_get_factor_status(self):
        m = self._make_monitor()
        assert m.get_factor_status("ghost") is None
        m.register_factor("f1", baseline_ic=0.05, baseline_capacity=1e7)
        status = m.get_factor_status("f1")
        assert status["registered"] is True
        assert status["baseline_ic"] == 0.05

    def test_check_unregistered_factor(self):
        m = self._make_monitor()
        assert m.check("ghost", current_ic=0.1) == []


class TestICDrift:
    def _monitor(self) -> DataQualityMonitor:
        m = DataQualityMonitor()
        m.register_factor("f1", baseline_ic=0.05, baseline_capacity=1e7, ic_std=0.01)
        return m

    def test_no_drift(self):
        m = self._monitor()
        assert m.check("f1", current_ic=0.05) == []

    def test_warning_drift(self):
        m = self._monitor()
        alerts = m.check("f1", current_ic=0.075)  # z=2.5
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"
        assert alerts[0].alert_type == "ic_drift"

    def test_critical_drift(self):
        m = self._monitor()
        alerts = m.check("f1", current_ic=0.09)  # z=4
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_ic_std_zero_no_alert(self):
        m = DataQualityMonitor()
        m.register_factor("f1", baseline_ic=0.05, baseline_capacity=1e7, ic_std=0.0)
        assert m.check("f1", current_ic=0.9) == []

    def test_cooldown_suppresses_callback(self):
        calls = []
        m = DataQualityMonitor(alert_callback=lambda a: calls.append(a))
        m.register_factor("f1", baseline_ic=0.05, baseline_capacity=1e7, ic_std=0.01)
        first = m.check("f1", current_ic=0.09)
        assert len(first) == 1
        assert len(calls) == 1
        # 冷却期内：check 仍返回告警列表，但回调被抑制
        second = m.check("f1", current_ic=0.09)
        assert len(second) == 1
        assert len(calls) == 1  # 回调未再次触发
        assert m._total_alerts == 2


class TestCapacityShock:
    def _monitor(self) -> DataQualityMonitor:
        m = DataQualityMonitor()
        m.register_factor("f1", baseline_ic=0.05, baseline_capacity=1e7)
        return m

    def test_no_shock(self):
        m = self._monitor()
        assert m.check("f1", current_capacity=1e7) == []

    def test_warning_shock(self):
        m = self._monitor()
        alerts = m.check("f1", current_capacity=1.6e7)  # +60%
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"
        assert alerts[0].alert_type == "capacity_shock"

    def test_critical_shock(self):
        m = self._monitor()
        alerts = m.check("f1", current_capacity=2.5e7)  # +150%
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_zero_baseline_no_alert(self):
        m = DataQualityMonitor()
        m.register_factor("f1", baseline_ic=0.05, baseline_capacity=0.0)
        assert m.check("f1", current_capacity=1e7) == []


class TestAlertCallback:
    def test_callback_called(self):
        calls = []
        m = DataQualityMonitor(alert_callback=lambda a: calls.append(a))
        m.register_factor("f1", baseline_ic=0.05, baseline_capacity=1e7, ic_std=0.01)
        m.check("f1", current_ic=0.09)
        assert len(calls) == 1

    def test_callback_exception_swallowed(self):
        def bad_callback(alert):
            raise RuntimeError("cb fail")

        m = DataQualityMonitor(alert_callback=bad_callback)
        m.register_factor("f1", baseline_ic=0.05, baseline_capacity=1e7, ic_std=0.01)
        alerts = m.check("f1", current_ic=0.09)  # 不抛异常
        assert len(alerts) == 1


class TestValidateMarketData:
    def _make_ohlcv(self, n: int = 100) -> pd.DataFrame:
        rng = np.random.default_rng(1)
        dates = pd.date_range("2026-01-01", periods=n, freq="D")
        return pd.DataFrame(
            {
                "open": rng.normal(100, 1, n),
                "high": rng.normal(101, 1, n),
                "low": rng.normal(99, 1, n),
                "close": rng.normal(100, 1, n),
                "volume": rng.integers(1000, 9000, n).astype(float),
            },
            index=dates,
        )

    def test_empty_data_critical(self):
        m = DataQualityMonitor()
        alerts = m.validate_market_data(pd.DataFrame())
        assert any(a.severity == "critical" for a in alerts)
        assert m._market_data_valid is False

    def test_none_data(self):
        m = DataQualityMonitor()
        alerts = m.validate_market_data(None)  # type: ignore[arg-type]
        assert len(alerts) >= 1

    def test_missing_columns_critical(self):
        m = DataQualityMonitor()
        df = pd.DataFrame({"close": [1.0, 2.0]})  # 缺 open/high/low/volume
        alerts = m.validate_market_data(df)
        assert any("缺少必要字段" in a.message for a in alerts)

    def test_high_missing_ratio_critical(self):
        m = DataQualityMonitor()
        df = self._make_ohlcv(20)
        df.loc[df.index[:10], "close"] = np.nan  # 50% 缺失
        alerts = m.validate_market_data(df)
        assert any("缺失率" in a.message and a.severity == "critical" for a in alerts)

    def test_moderate_missing_ratio_warning(self):
        m = DataQualityMonitor()
        df = self._make_ohlcv(100)
        df.loc[df.index[:8], "close"] = np.nan  # 8% 缺失 → warning
        alerts = m.validate_market_data(df)
        assert any("缺失率" in a.message and a.severity == "warning" for a in alerts)

    def test_forward_returns_missing(self):
        m = DataQualityMonitor()
        df = self._make_ohlcv(100)
        fr = np.full(100, 0.01)
        fr[:30] = np.nan  # 30% 缺失 → critical
        alerts = m.validate_market_data(df, forward_returns=fr)
        assert any("forward_returns" in a.message for a in alerts)

    def test_clean_data_no_critical(self):
        m = DataQualityMonitor()
        df = self._make_ohlcv(100)
        alerts = m.validate_market_data(df)
        assert not any(a.severity == "critical" for a in alerts)
        assert m._market_data_valid is True
        assert m._last_completeness_ratio == pytest.approx(1.0)


class TestMetrics:
    def test_prometheus_metrics_format(self):
        m = DataQualityMonitor()
        out = m.get_prometheus_metrics()
        assert "fts_data_quality_total_checks" in out
        assert "fts_data_quality_registered_factors" in out

    def test_metrics_snapshot_fields(self):
        m = DataQualityMonitor()
        snap = m.get_metrics_snapshot()
        assert snap["registered_factors"] == 0
        assert "total_checks" in snap and "critical_alerts" in snap


# ─── 指标计算函数 ──────────────────────────────────────────


class TestMetricFunctions:
    def test_coverage_ratio(self):
        df = pd.DataFrame({"symbol": ["A", "B", "C"]})
        assert compute_coverage_ratio(df, {"A", "B"}) == 1.0
        assert compute_coverage_ratio(df, {"A", "X"}) == 0.5
        assert compute_coverage_ratio(df, set()) == 0.0
        assert compute_coverage_ratio(pd.DataFrame({"x": [1]}), {"A"}) == 0.0

    def test_timestamp_continuity(self):
        df = pd.DataFrame({"timestamp": ["2026-01-01", "2026-01-02", "2026-01-03"]})
        assert compute_timestamp_continuity(df) == 1.0
        assert compute_timestamp_continuity(pd.DataFrame()) == 0.0
        df2 = pd.DataFrame({"timestamp": ["2026-01-01"]})
        assert compute_timestamp_continuity(df2) == 1.0  # <2 时间戳
        df3 = pd.DataFrame({"x": [1, 2]})
        assert compute_timestamp_continuity(df3) == 0.0  # 无 timestamp 列

    def test_field_completeness(self):
        df = pd.DataFrame({"close": [1.0, np.nan, 3.0]})
        assert compute_field_completeness(df, "close") == pytest.approx(2 / 3)
        assert compute_field_completeness(df, "missing") == 0.0
        assert compute_field_completeness(pd.DataFrame(), "close") == 0.0

    def test_missing_ratio(self):
        df = pd.DataFrame({"a": [1.0, np.nan], "b": [1.0, 2.0]})
        assert compute_missing_ratio(df) == pytest.approx(0.25)
        assert compute_missing_ratio(pd.DataFrame()) == 0.0

    def test_cross_source_deviation(self):
        p = pd.Series([1.0, 2.0, 3.0])
        s = pd.Series([1.0, 2.2, 2.8])
        assert compute_cross_source_deviation(p, s) > 0
        # 完全对齐 → 0
        assert compute_cross_source_deviation(p, p.copy()) == 0.0
        # 无重叠 → 0
        assert compute_cross_source_deviation(pd.Series([1.0]), pd.Series([99.0], index=[10])) == 0.0

    def test_outlier_ratio(self):
        rng = np.random.default_rng(7)
        base = rng.normal(10, 0.5, 200)
        s = pd.Series(np.concatenate([base, [100.0]]))  # 单极端值 (~180σ)
        assert compute_outlier_ratio(s) > 0
        assert compute_outlier_ratio(pd.Series([1.0])) == 0.0  # len<2
        assert compute_outlier_ratio(pd.Series([5.0, 5.0, 5.0])) == 0.0  # std=0

    def test_jump_detection(self):
        df = pd.DataFrame({"close": [1.0, 1.5, 1.2, 1.4]})
        assert compute_jump_detection(df, threshold=0.4) == 1  # 仅 1.0→1.5 (+50%)
        assert compute_jump_detection(pd.DataFrame({"x": [1, 2]})) == 0  # 无 close
        assert compute_jump_detection(pd.DataFrame({"close": [1.0]})) == 0  # len<2

    def test_data_drift_rate(self):
        ref = pd.Series(np.random.default_rng(1).normal(0, 1, 500))
        cur = pd.Series(np.random.default_rng(2).normal(0, 1, 500))
        assert compute_data_drift_rate(ref, cur) >= 0
        # 空序列 → 0
        assert compute_data_drift_rate(pd.Series(dtype=float), pd.Series(dtype=float)) == 0.0

    def test_update_delay(self):
        from datetime import datetime, timedelta

        now = datetime.now()
        delay = compute_update_delay(now - timedelta(seconds=120), now=now)
        assert delay == pytest.approx(120.0, abs=1.0)

    def test_cache_hit_ratio(self):
        assert compute_cache_hit_ratio(3, 10) == 0.3
        assert compute_cache_hit_ratio(0, 0) == 0.0

    def test_freshness(self):
        from datetime import datetime, timedelta

        # 空/无 timestamp → inf
        assert compute_freshness(pd.DataFrame()) == float("inf")
        assert compute_freshness(pd.DataFrame({"x": [1]})) == float("inf")
        # 相对当前时间 → 秒数
        df = pd.DataFrame({"timestamp": [(datetime.now() - timedelta(minutes=5)).isoformat()]})
        assert compute_freshness(df) == pytest.approx(300.0, abs=10.0)


class TestEvaluateSourceData:
    def test_full_evaluation(self):
        rng = np.random.default_rng(1)
        n = 50
        dates = pd.date_range("2026-01-01", periods=n, freq="D")
        df = pd.DataFrame(
            {
                "symbol": ["RB0"] * n,
                "timestamp": dates,
                "close": 100 + np.cumsum(rng.normal(0, 0.5, n)),
                "open": 100 + rng.normal(0, 1, n),
                "high": 102 + rng.normal(0, 1, n),
                "low": 98 + rng.normal(0, 1, n),
                "volume": rng.integers(1000, 9000, n).astype(float),
            }
        )
        result = evaluate_source_data(df, expected_symbols={"RB0"}, reference_close=df["close"])
        assert "completeness" in result and "accuracy" in result and "timeliness" in result
        assert result["completeness"]["coverage_ratio"] == 1.0
        assert "data_drift_rate" in result["accuracy"]
        assert "update_delay_seconds" in result["timeliness"]

    def test_minimal_df(self):
        df = pd.DataFrame({"x": [1.0, 2.0]})
        result = evaluate_source_data(df)
        assert result["completeness"]["missing_ratio"] == 0.0
