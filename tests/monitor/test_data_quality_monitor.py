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
    MonitorConfig,
    QualityAlert,
)


def _make_good_data(n: int = 100) -> pd.DataFrame:
    """构造正常 OHLCV 数据。"""
    rng = np.random.default_rng(42)
    return pd.DataFrame({
        "open": 100 + np.cumsum(rng.standard_normal(n) * 0.5),
        "high": 101 + np.cumsum(rng.standard_normal(n) * 0.5),
        "low": 99 + np.cumsum(rng.standard_normal(n) * 0.5),
        "close": 100 + np.cumsum(rng.standard_normal(n) * 0.5),
        "volume": rng.integers(1000, 10000, n).astype(float),
    })


def _make_data_with_missing_cols() -> pd.DataFrame:
    """构造缺少必要字段的数据。"""
    return pd.DataFrame({
        "open": [100.0, 101.0],
        "close": [100.5, 101.5],
    })


def _make_data_with_high_missing(missing_ratio: float = 0.3) -> pd.DataFrame:
    """构造高缺失率的数据。"""
    n = 100
    data = _make_good_data(n)
    n_missing = int(n * missing_ratio)
    data.loc[:n_missing - 1, "close"] = np.nan
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