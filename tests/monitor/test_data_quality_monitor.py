"""
tests/monitor/test_data_quality_monitor.py — DataQualityMonitor 市场数据校验测试

覆盖范围:
    - validate_market_data 正常数据
    - validate_market_data 空数据
    - validate_market_data 缺失字段
    - validate_market_data 高缺失率
    - validate_market_data forward_returns 缺失
    - 集成到 EvolutionLoop.run() 数据加载流程

版本: v0.1.0
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

from fts.monitor.data_quality_monitor import (
    DataQualityMonitor,
    FactorBaseline,
    MonitorConfig,
    QualityAlert,
)


def _make_good_data(n: int = 100) -> pd.DataFrame:
    """构造正常 OHLCV 数据。"""
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "open": 100 + np.cumsum(rng.standard_normal(n) * 0.5),
            "high": 101 + np.cumsum(rng.standard_normal(n) * 0.5),
            "low": 99 + np.cumsum(rng.standard_normal(n) * 0.5),
            "close": 100 + np.cumsum(rng.standard_normal(n) * 0.5),
            "volume": rng.integers(1000, 10000, n).astype(float),
        }
    )


def _make_data_with_missing_cols() -> pd.DataFrame:
    """构造缺少必要字段的数据。"""
    return pd.DataFrame(
        {
            "open": [100.0, 101.0],
            "close": [100.5, 101.5],
        }
    )


def _make_data_with_high_missing(missing_ratio: float = 0.3) -> pd.DataFrame:
    """构造高缺失率的数据。"""
    n = 100
    data = _make_good_data(n)
    n_missing = int(n * missing_ratio)
    data.loc[: n_missing - 1, "close"] = np.nan
    return data


class TestValidateMarketData:
    """DataQualityMonitor.validate_market_data 测试。"""

    def setup_method(self):
        self.monitor = DataQualityMonitor()

    def test_good_data_no_alerts(self):
        """正常数据不应产生告警。"""
        data = _make_good_data()
        alerts = self.monitor.validate_market_data(data)
        assert len(alerts) == 0

    def test_empty_data_critical_alert(self):
        """空数据应产生 critical 告警。"""
        alerts = self.monitor.validate_market_data(pd.DataFrame())
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"
        assert alerts[0].alert_type == "ic_drift"

    def test_none_data_critical_alert(self):
        """None 数据应产生 critical 告警。"""
        alerts = self.monitor.validate_market_data(None)
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_missing_columns_critical(self):
        """缺少必要字段应产生 critical 告警。"""
        data = _make_data_with_missing_cols()
        alerts = self.monitor.validate_market_data(data)
        assert len(alerts) >= 1
        critical = [a for a in alerts if a.severity == "critical"]
        assert len(critical) >= 1

    def test_high_missing_ratio_warning(self):
        """高缺失率 (>5%) 应产生告警。"""
        data = _make_data_with_high_missing(missing_ratio=0.1)
        alerts = self.monitor.validate_market_data(data)
        assert len(alerts) >= 1
        assert any(a.metric_name == "missing_ratio" for a in alerts)

    def test_critical_missing_ratio(self):
        """严重缺失率 (>20%) 应产生 critical 告警。"""
        data = _make_data_with_high_missing(missing_ratio=0.3)
        alerts = self.monitor.validate_market_data(data)
        critical = [a for a in alerts if a.severity == "critical"]
        assert len(critical) >= 1

    def test_forward_returns_missing(self):
        """forward_returns 高缺失率应产生告警。"""
        data = _make_good_data()
        fr = np.random.randn(100)
        fr[:30] = np.nan  # 30% 缺失
        alerts = self.monitor.validate_market_data(data, forward_returns=fr)
        assert len(alerts) >= 1
        fr_alerts = [a for a in alerts if a.metric_name == "forward_returns_missing"]
        assert len(fr_alerts) >= 1

    def test_forward_returns_clean(self):
        """干净的 forward_returns 不应产生额外告警。"""
        data = _make_good_data()
        fr = np.random.randn(100)
        alerts = self.monitor.validate_market_data(data, forward_returns=fr)
        assert len(alerts) == 0

    def test_data_quality_monitor_integration(self):
        """验证 DataQualityMonitor 可正常实例化和调用。"""
        monitor = DataQualityMonitor(config=MonitorConfig())
        data = _make_good_data()
        alerts = monitor.validate_market_data(data)
        assert isinstance(alerts, list)
        assert all(isinstance(a, QualityAlert) for a in alerts)


class TestPrometheusMetrics:
    """DataQualityMonitor Prometheus 指标生成测试。"""

    def setup_method(self):
        self.monitor = DataQualityMonitor()

    def test_initial_metrics_default_values(self):
        """初始状态指标应为零值。"""
        metrics = self.monitor.get_metrics_snapshot()
        assert metrics["total_checks"] == 0
        assert metrics["total_alerts"] == 0
        assert metrics["critical_alerts"] == 0
        assert metrics["warning_alerts"] == 0
        assert metrics["data_completeness_ratio"] == 1.0
        assert metrics["market_data_valid"] is True
        assert metrics["registered_factors"] == 0

    def test_metrics_after_good_validation(self):
        """正常数据校验后指标应更新。"""
        data = _make_good_data()
        self.monitor.validate_market_data(data)

        metrics = self.monitor.get_metrics_snapshot()
        assert metrics["total_checks"] == 1
        assert metrics["total_alerts"] == 0
        assert metrics["data_completeness_ratio"] == 1.0
        assert metrics["market_data_valid"] is True
        assert metrics["last_validation_time"] > 0

    def test_metrics_after_bad_validation(self):
        """异常数据校验后指标应反映告警。"""
        self.monitor.validate_market_data(None)

        metrics = self.monitor.get_metrics_snapshot()
        assert metrics["total_checks"] == 1
        assert metrics["total_alerts"] >= 1
        assert metrics["critical_alerts"] >= 1
        assert metrics["market_data_valid"] is False
        assert metrics["data_completeness_ratio"] == 0.0

    def test_metrics_accumulate_across_checks(self):
        """多次检查指标应累积。"""
        data = _make_good_data()
        self.monitor.validate_market_data(data)
        self.monitor.validate_market_data(data)
        self.monitor.validate_market_data(None)

        metrics = self.monitor.get_metrics_snapshot()
        assert metrics["total_checks"] == 3
        assert metrics["total_alerts"] >= 1
        assert metrics["critical_alerts"] >= 1

    def test_factor_check_metrics(self):
        """因子检查应累积指标。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        self.monitor.check("f1", current_ic=0.05)
        self.monitor.check("f1", current_ic=0.01)

        metrics = self.monitor.get_metrics_snapshot()
        assert metrics["factor_check_count"] == 2
        assert metrics["registered_factors"] == 1

    def test_ic_drift_alerts_counted(self):
        """IC 漂移告警应单独计数。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        self.monitor.check("f1", current_ic=0.01)

        metrics = self.monitor.get_metrics_snapshot()
        assert metrics["ic_drift_alerts"] >= 1

    def test_prometheus_format_contains_required_metrics(self):
        """Prometheus 格式应包含所有必需指标。"""
        output = self.monitor.get_prometheus_metrics()

        assert "fts_data_quality_data_completeness_ratio" in output
        assert "fts_data_quality_market_data_valid" in output
        assert "fts_data_quality_total_checks" in output
        assert "fts_data_quality_total_alerts" in output
        assert "fts_data_quality_critical_alerts" in output
        assert "fts_data_quality_warning_alerts" in output
        assert "fts_data_quality_ic_drift_alerts" in output
        assert "fts_data_quality_capacity_shock_alerts" in output
        assert "fts_data_quality_registered_factors" in output
        assert "fts_data_quality_last_validation_timestamp" in output

    def test_prometheus_format_has_help_and_type(self):
        """Prometheus 格式应包含 HELP 和 TYPE 注释。"""
        output = self.monitor.get_prometheus_metrics()
        assert "# HELP" in output
        assert "# TYPE" in output

    def test_prometheus_format_values_are_numeric(self):
        """Prometheus 指标值应为数值。"""
        output = self.monitor.get_prometheus_metrics()
        for line in output.split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        float(parts[1])
                    except (ValueError, IndexError):
                        pytest.fail(f"非数值指标行: {line}")

    def test_registered_factor_count_in_metrics(self):
        """注册因子数应反映在指标中。"""
        self.monitor.register_factor("f1", 0.05, 1000)
        self.monitor.register_factor("f2", 0.03, 500)

        output = self.monitor.get_prometheus_metrics()
        assert "fts_data_quality_registered_factors 2" in output


# ─── 告警/配置契约 ──────────────────────────────────────


class TestAlertAndBaselineContracts:
    """FactorBaseline / QualityAlert / MonitorConfig 契约。"""

    def test_factor_baseline_defaults(self):
        baseline = FactorBaseline(factor_id="f1", baseline_ic=0.05, baseline_capacity=1000)
        assert baseline.ic_std == 0.01
        assert baseline.capacity_std == 0.0

    def test_quality_alert_to_dict(self):
        alert = QualityAlert(
            factor_id="f1",
            alert_type="ic_drift",
            severity="warning",
            message="msg",
            metric_name="IC",
            metric_value=0.01,
            baseline_value=0.05,
            threshold=2.0,
        )
        d = alert.to_dict()
        assert d["factor_id"] == "f1"
        assert d["metric_value"] == 0.01
        assert "timestamp" in d
        assert d["threshold"] == 2.0

    def test_quality_alert_default_timestamp(self):
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

    def test_monitor_config_custom_thresholds(self):
        cfg = MonitorConfig(
            ic_zscore_warning=1.0,
            ic_zscore_critical=2.0,
            capacity_change_warning=0.3,
            capacity_change_critical=0.6,
            alert_cooldown=10,
        )
        monitor = DataQualityMonitor(config=cfg)
        monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        alerts = monitor.check("f1", current_ic=0.08)  # z=3 → custom critical=2.0
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"


# ─── check 补充分支 ─────────────────────────────────────


class TestCheckBranches:
    """DataQualityMonitor.check 的分支覆盖。"""

    def setup_method(self):
        self.monitor = DataQualityMonitor()

    def test_unregistered_factor_returns_empty(self):
        """未注册因子 → 空告警列表。"""
        assert self.monitor.check("unknown", current_ic=0.1) == []

    def test_ic_critical(self):
        """|z|>=3 → critical 告警。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        alerts = self.monitor.check("f1", current_ic=0.08)  # z=3
        assert len(alerts) == 1
        assert alerts[0].alert_type == "ic_drift"
        assert alerts[0].severity == "critical"

    def test_ic_warning(self):
        """2<=|z|<3 → warning 告警。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        alerts = self.monitor.check("f1", current_ic=0.07)  # z=2
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"

    def test_ic_no_alert_when_std_zero(self):
        """ic_std<=0 → 不检测 IC 漂移。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000, ic_std=0)
        assert self.monitor.check("f1", current_ic=0.5) == []

    def test_capacity_critical(self):
        """容量变化率>=80% → critical 告警。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        alerts = self.monitor.check("f1", current_capacity=200_000)  # -80%
        assert len(alerts) == 1
        assert alerts[0].alert_type == "capacity_shock"
        assert alerts[0].severity == "critical"

    def test_capacity_warning(self):
        """容量变化率 50%~80% → warning 告警。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        alerts = self.monitor.check("f1", current_capacity=400_000)  # -60%
        assert len(alerts) == 1
        assert alerts[0].severity == "warning"

    def test_capacity_no_alert_when_baseline_zero(self):
        """baseline_capacity<=0 → 不检测容量突变。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=0)
        assert self.monitor.check("f1", current_capacity=100) == []

    def test_ic_and_capacity_simultaneously(self):
        """IC 漂移 + 容量突变同时触发 → 2 条告警。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        alerts = self.monitor.check("f1", current_ic=0.2, current_capacity=100_000)
        assert len(alerts) == 2
        assert {a.alert_type for a in alerts} == {"ic_drift", "capacity_shock"}

    def test_capacity_only_when_ic_none(self):
        """仅提供 current_capacity → 只触发容量告警。"""
        self.monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        alerts = self.monitor.check("f1", current_capacity=100_000)
        assert len(alerts) == 1
        assert alerts[0].alert_type == "capacity_shock"


# ─── _handle_alert 冷却/回调 ────────────────────────────


class TestHandleAlert:
    """告警处理：冷却、回调、异常吞掉。"""

    def test_cooldown_skips_second_alert(self):
        """同一因子同一类型冷却期内重复告警 → 回调只执行一次。"""
        calls = []
        monitor = DataQualityMonitor(
            config=MonitorConfig(alert_cooldown=3600),
            alert_callback=lambda a: calls.append(a),
        )
        monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        monitor.check("f1", current_ic=-0.05)  # z=-10 critical
        monitor.check("f1", current_ic=-0.05)
        assert len(calls) == 1

    def test_alert_callback_exception_swallowed(self):
        """回调抛异常 → 不向外传播。"""

        def bad_callback(alert):
            raise RuntimeError("callback boom")

        monitor = DataQualityMonitor(alert_callback=bad_callback)
        monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        monitor.check("f1", current_ic=-0.05)  # 不应抛异常

    def test_alert_counters_update(self):
        """告警计数应随 severity 累积。"""
        monitor = DataQualityMonitor(config=MonitorConfig(alert_cooldown=0))
        monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        monitor.check("f1", current_ic=0.2, current_capacity=100_000)
        snapshot = monitor.get_metrics_snapshot()
        assert snapshot["total_alerts"] >= 2
        assert snapshot["critical_alerts"] >= 2
        assert snapshot["ic_drift_alerts"] >= 1
        assert snapshot["capacity_shock_alerts"] >= 1


# ─── get_factor_status / list_registered ────────────────


class TestFactorStatus:
    """因子状态查询。"""

    def test_get_factor_status_unknown_returns_none(self):
        monitor = DataQualityMonitor()
        assert monitor.get_factor_status("unknown") is None

    def test_get_factor_status_registered(self):
        monitor = DataQualityMonitor()
        monitor.register_factor("f1", baseline_ic=0.05, baseline_capacity=1_000_000)
        status = monitor.get_factor_status("f1")
        assert status["registered"] is True
        assert status["baseline_ic"] == 0.05
        assert status["baseline_capacity"] == 1_000_000

    def test_list_registered(self):
        monitor = DataQualityMonitor()
        monitor.register_factor("f1", 0.05, 1000)
        monitor.register_factor("f2", 0.03, 500)
        assert set(monitor.list_registered()) == {"f1", "f2"}


# ─── validate_market_data 补充 ──────────────────────────


class TestValidateMarketDataExtra:
    """validate_market_data 的 n_checked==0 等分支。"""

    def test_no_required_columns_completeness_zero(self):
        """数据只有无关列 → 完整性比率归零。"""
        monitor = DataQualityMonitor()
        data = pd.DataFrame({"foo": [1.0, 2.0]})
        alerts = monitor.validate_market_data(data)
        assert len(alerts) >= 1
        assert monitor._last_completeness_ratio == 0.0
        assert monitor._market_data_valid is False


# ─── 三维指标计算函数（B.1）────────────────────────────


class TestThreeDimensionMetrics:
    """覆盖 B.1 三维指标计算函数。"""

    def test_compute_coverage_ratio(self):
        from fts.monitor.data_quality_monitor import compute_coverage_ratio

        df = pd.DataFrame({"symbol": ["RB0", "RB0", "CU0"]})
        assert compute_coverage_ratio(df, {"RB0", "CU0"}) == 1.0
        assert compute_coverage_ratio(df, {"RB0", "CU0", "AU0"}) == pytest.approx(2 / 3)
        assert compute_coverage_ratio(df, set()) == 0.0
        assert compute_coverage_ratio(pd.DataFrame(), {"RB0"}) == 0.0
        assert compute_coverage_ratio(pd.DataFrame({"close": [1.0]}), {"RB0"}) == 0.0

    def test_compute_timestamp_continuity(self):
        from fts.monitor.data_quality_monitor import compute_timestamp_continuity

        df = pd.DataFrame({"timestamp": ["2026-08-01", "2026-08-02", "2026-08-03"]})
        assert compute_timestamp_continuity(df, freq="D") == 1.0
        df2 = pd.DataFrame({"timestamp": ["2026-08-01", "2026-08-03"]})
        assert compute_timestamp_continuity(df2, freq="D") == pytest.approx(2 / 3)
        assert compute_timestamp_continuity(pd.DataFrame(), freq="D") == 0.0
        assert compute_timestamp_continuity(pd.DataFrame({"close": [1.0]}), freq="D") == 0.0
        single = pd.DataFrame({"timestamp": ["2026-08-01"]})
        assert compute_timestamp_continuity(single) == 1.0

    def test_compute_field_completeness(self):
        from fts.monitor.data_quality_monitor import compute_field_completeness

        df = pd.DataFrame({"close": [1.0, None, 3.0]})
        assert compute_field_completeness(df, "close") == pytest.approx(2 / 3)
        assert compute_field_completeness(df, "missing") == 0.0
        assert compute_field_completeness(pd.DataFrame(), "close") == 0.0

    def test_compute_missing_ratio(self):
        from fts.monitor.data_quality_monitor import compute_missing_ratio

        df = pd.DataFrame({"a": [1.0, None], "b": [None, None]})
        assert compute_missing_ratio(df) == pytest.approx(3 / 4)
        assert compute_missing_ratio(pd.DataFrame()) == 0.0

    def test_compute_cross_source_deviation(self):
        from fts.monitor.data_quality_monitor import compute_cross_source_deviation

        p = pd.Series([100.0, 102.0, 101.0])
        s = pd.Series([99.0, 100.0, 101.0])
        assert compute_cross_source_deviation(p, s) > 0
        assert compute_cross_source_deviation(pd.Series(dtype=float), pd.Series(dtype=float)) == 0.0

    def test_compute_outlier_ratio(self):
        from fts.monitor.data_quality_monitor import compute_outlier_ratio

        assert compute_outlier_ratio(pd.Series([1.0] * 100)) == 0.0  # std == 0
        assert compute_outlier_ratio(pd.Series([1.0])) == 0.0  # len < 2
        s2 = pd.Series([1.0] * 99 + [1000.0])
        assert compute_outlier_ratio(s2) == pytest.approx(0.01)

    def test_compute_jump_detection(self):
        from fts.monitor.data_quality_monitor import compute_jump_detection

        df = pd.DataFrame({"close": [100.0, 120.0, 121.0]})
        assert compute_jump_detection(df) == 1  # 20% 跳变 > 15%
        assert compute_jump_detection(pd.DataFrame({"open": [1.0, 2.0]})) == 0
        assert compute_jump_detection(pd.DataFrame({"close": [1.0]})) == 0

    def test_compute_data_drift_rate(self):
        from fts.monitor.data_quality_monitor import compute_data_drift_rate

        ref = pd.Series(np.random.default_rng(1).normal(0, 1, 1000))
        curr = pd.Series(np.random.default_rng(2).normal(0, 1, 1000))
        assert compute_data_drift_rate(ref, curr) >= 0
        assert compute_data_drift_rate(pd.Series(dtype=float), pd.Series(dtype=float)) == 0.0

    def test_compute_update_delay(self):
        from datetime import datetime

        from fts.monitor.data_quality_monitor import compute_update_delay

        now = datetime(2026, 8, 10, 10, 0, 0)
        delay = compute_update_delay("2026-08-10 09:00:00", now=now)
        assert delay == pytest.approx(3600.0)

    def test_compute_cache_hit_ratio(self):
        from fts.monitor.data_quality_monitor import compute_cache_hit_ratio

        assert compute_cache_hit_ratio(3, 10) == pytest.approx(0.3)
        assert compute_cache_hit_ratio(0, 0) == 0.0

    def test_compute_freshness(self):
        from datetime import datetime

        from fts.monitor.data_quality_monitor import compute_freshness

        now = datetime(2026, 8, 10, 10, 0, 0)
        df = pd.DataFrame({"timestamp": ["2026-08-10 09:00:00"]})
        assert compute_freshness(df, now=now) == pytest.approx(3600.0)
        assert compute_freshness(pd.DataFrame()) == float("inf")
        assert compute_freshness(pd.DataFrame({"close": [1.0]})) == float("inf")


# ─── evaluate_source_data ──────────────────────────────


class TestEvaluateSourceData:
    """evaluate_source_data 汇总评估。"""

    def _df(self):
        return pd.DataFrame(
            {
                "symbol": ["RB0", "RB0"],
                "timestamp": ["2026-08-01", "2026-08-02"],
                "close": [100.0, 101.0],
            }
        )

    def test_default_evaluation(self):
        from fts.monitor.data_quality_monitor import evaluate_source_data

        result = evaluate_source_data(self._df())
        assert result["completeness"]["coverage_ratio"] == 1.0
        assert result["completeness"]["timestamp_continuity"] == 1.0
        assert result["accuracy"]["outlier_ratio"] == 0.0
        assert "update_delay_seconds" in result["timeliness"]
        assert "field_completeness_close" in result["completeness"]

    def test_with_reference_close(self):
        from fts.monitor.data_quality_monitor import evaluate_source_data

        ref = pd.Series([99.0, 100.5])
        result = evaluate_source_data(self._df(), reference_close=ref)
        assert "data_drift_rate" in result["accuracy"]

    def test_without_close_column(self):
        from fts.monitor.data_quality_monitor import evaluate_source_data

        df = pd.DataFrame({"symbol": ["RB0"], "timestamp": ["2026-08-01"]})
        result = evaluate_source_data(df)
        assert "field_completeness_close" not in result["completeness"]
        assert result["accuracy"]["outlier_ratio"] == 0.0


# ─── create_default_monitor ─────────────────────────────


def test_create_default_monitor():
    """create_default_monitor 应返回默认配置的监控器。"""
    from fts.monitor.data_quality_monitor import create_default_monitor

    monitor = create_default_monitor()
    assert isinstance(monitor, DataQualityMonitor)
    assert monitor._config.ic_zscore_warning == 2.0
    assert monitor._config.alert_cooldown == 3600.0
