"""
tests/monitor/test_data_level_monitor.py — GAP-F06 数据级质量监控器测试。

覆盖:
    1. 完整性: 缺失率告警（全表 + 关键字段）+ 阈值边界
    2. 准确性: 3σ 异常值比例告警
    3. 复权一致性: 相对偏差超容差比例告警
    4. 多源分歧: close 相对偏差中位数告警
    5. 汇总执行 run_all / 冷却 / 回调 / 空数据安全
    6. scheduler 任务接入（无缓存库跳过 + 读取失败降级）
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from fts.monitor.data_level_monitor import (
    DataLevelAlert,
    DataLevelConfig,
    DataLevelMonitor,
)


def _make_ohlcv(n: int = 200, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 3500 + np.cumsum(rng.normal(scale=5.0, size=n))
    return pd.DataFrame(
        {
            "close": close,
            "volume": np.abs(rng.normal(100_000, 10_000, n)),
            "open_interest": np.abs(rng.normal(200_000, 20_000, n)),
        }
    )


class TestMissing:
    def test_no_alert_on_clean_data(self) -> None:
        monitor = DataLevelMonitor()
        assert monitor.check_missing(_make_ohlcv()) == []

    def test_total_missing_warning(self) -> None:
        """全表缺失率超警告阈值 → warning 告警。"""
        monitor = DataLevelMonitor(config=DataLevelConfig(missing_ratio_warning=0.05))
        df = _make_ohlcv(n=100)
        df.loc[:20, "close"] = np.nan  # 21% 缺失
        alerts = monitor.check_missing(df)
        assert any(a.alert_type == "missing_ratio" and a.severity == "critical" for a in alerts)

    def test_field_missing_critical(self) -> None:
        """关键字段缺失率超严重阈值 → critical 告警。"""
        monitor = DataLevelMonitor()
        df = _make_ohlcv(n=100)
        df.loc[:49, "volume"] = np.nan  # 50% 缺失
        alerts = monitor.check_missing(df)
        field_alerts = [a for a in alerts if a.metric_name == "missing_ratio_volume"]
        assert field_alerts and field_alerts[0].severity == "critical"

    def test_hold_missing_detected(self) -> None:
        """GAP-085 回归：key_fields 修正为 hold 后，持仓量缺失率真实触发告警。

        原 key_fields 用 open_interest（期货日线字段为 hold），持仓量缺失静默跳过。
        """
        monitor = DataLevelMonitor()
        df = _make_ohlcv(n=100)
        df["hold"] = 200_000.0
        df.loc[:49, "hold"] = np.nan  # 50% 缺失
        alerts = monitor.check_missing(df)
        field_alerts = [a for a in alerts if a.metric_name == "missing_ratio_hold"]
        assert field_alerts and field_alerts[0].severity == "critical"

    def test_hold_not_in_df_no_alert(self) -> None:
        """数据无 hold 列（如纯 OHLCV）→ 跳过，不误报。"""
        monitor = DataLevelMonitor()
        df = _make_ohlcv(n=100).drop(columns=["open_interest"])
        assert monitor.check_missing(df) == []

    def test_threshold_boundary(self) -> None:
        """恰好等于阈值不触发（严格大于）。"""
        monitor = DataLevelMonitor(config=DataLevelConfig(missing_ratio_warning=0.10))
        df = _make_ohlcv(n=100)
        df.loc[:9, "close"] = np.nan  # 恰好 10%
        assert monitor.check_missing(df) == []

    def test_empty_df_no_alert(self) -> None:
        assert DataLevelMonitor().check_missing(pd.DataFrame()) == []


class TestOutliers:
    def test_clean_data_no_alert(self) -> None:
        monitor = DataLevelMonitor()
        assert monitor.check_outliers(_make_ohlcv()) == []

    def test_outlier_ratio_alert(self) -> None:
        """注入极端异常值 → 超警告阈值告警。"""
        monitor = DataLevelMonitor(config=DataLevelConfig(outlier_zscore=3.0, outlier_ratio_warning=0.01))
        df = _make_ohlcv(n=100)
        df.loc[:1, "close"] = 1e6  # 2 个极端异常值 = 2% > 1% 警告阈值
        alerts = monitor.check_outliers(df)
        assert any(a.alert_type == "outlier_ratio" for a in alerts)

    def test_constant_field_skipped(self) -> None:
        """常数列（std=0）不产生除零，也不告警。"""
        df = _make_ohlcv(n=50)
        df["close"] = 3500.0
        assert DataLevelMonitor().check_outliers(df) == []


class TestAdjustmentConsistency:
    def test_consistent_no_alert(self) -> None:
        adj = pd.Series(np.linspace(1.0, 1.5, 50))
        ref = adj * 1.001  # 0.1% 偏差，低于容差
        assert DataLevelMonitor().check_adjustment_consistency(adj, ref) == []

    def test_inconsistent_alert(self) -> None:
        """一半样本偏差 20% → critical 告警。"""
        monitor = DataLevelMonitor()
        n = 50
        adj = pd.Series(np.linspace(1.0, 1.5, n))
        ref = adj.copy()
        ref.iloc[:25] *= 1.2
        alerts = monitor.check_adjustment_consistency(adj, ref)
        assert alerts and alerts[0].alert_type == "adjust_consistency"
        assert alerts[0].severity == "critical"

    def test_none_inputs_no_alert(self) -> None:
        assert DataLevelMonitor().check_adjustment_consistency(None, None) == []


class TestSourceDisagreement:
    def test_agreeing_sources_no_alert(self) -> None:
        primary = pd.Series(np.linspace(3500, 3600, 60))
        secondary = primary * 1.002  # 0.2% 偏差
        assert DataLevelMonitor().check_source_disagreement(primary, secondary) == []

    def test_disagreeing_sources_critical(self) -> None:
        """一半样本偏离 5% → critical 告警。"""
        monitor = DataLevelMonitor()
        primary = pd.Series(np.linspace(3500, 3600, 60))
        secondary = primary.copy()
        secondary.iloc[:30] *= 0.95
        alerts = monitor.check_source_disagreement(primary, secondary)
        assert alerts and alerts[0].alert_type == "source_disagreement"
        assert alerts[0].severity == "critical"

    def test_none_inputs_no_alert(self) -> None:
        assert DataLevelMonitor().check_source_disagreement(None, None) == []


class TestRunAll:
    def test_run_all_aggregates(self) -> None:
        """run_all 汇总全部四维检查。"""
        monitor = DataLevelMonitor()
        df = _make_ohlcv(n=100)
        df.loc[:49, "close"] = np.nan  # 缺失
        df.loc[:1, "volume"] = 1e9  # 异常值（2 个 = 2%）
        adj = pd.Series(np.linspace(1.0, 1.2, 50))
        ref = adj.copy()
        ref.iloc[:25] *= 1.5
        primary = pd.Series(np.linspace(3500, 3600, 50))
        secondary = primary * 1.10
        alerts = monitor.run_all(
            df=df,
            adj=adj,
            reference=ref,
            primary_close=primary,
            secondary_close=secondary,
        )
        types = {a.alert_type for a in alerts}
        assert {
            "missing_ratio",
            "outlier_ratio",
            "adjust_consistency",
            "source_disagreement",
        } <= types

    def test_snapshot_and_metrics(self) -> None:
        monitor = DataLevelMonitor()
        monitor.run_all(df=_make_ohlcv())
        snap = monitor.get_snapshot()
        assert snap["alert_count"] == 0
        metrics = monitor.get_metrics_snapshot()
        assert metrics["total_checks"] == 1

    def test_alert_cooldown(self) -> None:
        """同类型告警在冷却期内不重复触发。"""
        monitor = DataLevelMonitor(config=DataLevelConfig(alert_cooldown=3600.0))
        df = _make_ohlcv(n=100)
        df.loc[:49, "close"] = np.nan
        first = monitor.run_all(df=df)
        monitor.run_all(df=df)  # 第二次（冷却期内）
        assert len(first) > 0
        # 冷却期内第二次不再计数
        assert monitor.get_metrics_snapshot()["total_alerts"] == len(first)

    def test_callback_invoked(self) -> None:
        seen: list[DataLevelAlert] = []
        monitor = DataLevelMonitor(alert_callback=seen.append)
        df = _make_ohlcv(n=100)
        df.loc[:99, "close"] = np.nan
        monitor.run_all(df=df)
        assert len(seen) > 0

    def test_empty_run_all_safe(self) -> None:
        monitor = DataLevelMonitor()
        assert monitor.run_all(df=pd.DataFrame()) == []
        assert monitor.get_metrics_snapshot()["total_checks"] == 1


class TestSchedulerIntegration:
    def test_job_skips_without_cache_db(self) -> None:
        """无缓存库 → job 正常返回不抛异常。"""
        from fts.scheduler import jobs

        with patch("pathlib.Path.exists", return_value=False):
            jobs.data_level_monitor_job()  # 不抛异常即通过

    def test_read_kline_cache_failure_returns_none(self) -> None:
        """缓存读取失败 → 返回 None（降级）。"""
        from fts.scheduler.jobs import _read_kline_cache

        with patch("duckdb.connect", side_effect=RuntimeError("db locked")):
            assert _read_kline_cache(MagicMock(), "RB") is None

    def test_task_registered(self) -> None:
        """data_level_monitor 任务已注册且启用。"""
        from fts.scheduler.tasks import register_default_tasks, get_task

        register_default_tasks()
        task = get_task("data_level_monitor")
        assert task is not None
        assert task.enabled
        assert task.callable_path == "fts.scheduler.jobs.data_level_monitor_job"
