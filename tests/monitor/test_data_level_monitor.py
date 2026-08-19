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

    def test_adj_factor_derived_exempted(self) -> None:
        """GAP-148：adj_factor 为读取时派生字段（kline_cache 按设计不落库），
        全表缺失率豁免其 100% 缺失，不触发误报。"""
        monitor = DataLevelMonitor(config=DataLevelConfig(missing_ratio_warning=0.05))
        df = _make_ohlcv(n=100)
        df["adj_factor"] = np.nan  # 100% 缺失的派生列
        assert monitor.check_missing(df) == []

    def test_proxy_fields_all_empty_critical(self) -> None:
        """GAP-151：代理字段（hold/settle/pre_settle）全部缺失 → 组级 critical 告警
        （下游走代理值，失真风险显式化，不再静默）。"""
        monitor = DataLevelMonitor()
        df = _make_ohlcv(n=100)
        df["hold"] = np.nan
        df["settle"] = np.nan
        df["pre_settle"] = np.nan
        alerts = monitor.check_missing(df)
        proxy_alerts = [a for a in alerts if a.alert_type == "proxy_missing_ratio"]
        assert proxy_alerts and proxy_alerts[0].severity == "critical"

    def test_proxy_fields_partial_under_threshold_no_alert(self) -> None:
        """GAP-151：代理字段缺失率低于阈值（默认 0.5）→ 不触发失真告警。"""
        monitor = DataLevelMonitor()
        df = _make_ohlcv(n=100)
        df["hold"] = 200_000.0
        df.loc[:19, "hold"] = np.nan  # 20% 缺失（<50%）
        df["settle"] = 100.0
        df["pre_settle"] = 100.0
        assert not any(a.alert_type == "proxy_missing_ratio" for a in monitor.check_missing(df))


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

    def test_job_checks_energy_chain_symbols(self) -> None:
        """数据级监控检查品种为能化链 12 品种（非动态核心子集）。"""
        from fts.scheduler import jobs

        seen: list[str] = []

        def fake_read(db_path, sym, limit=120):
            seen.append(sym)
            return None  # 无缓存 → 跳过（仅记录）

        with (
            patch("pathlib.Path.exists", return_value=True),
            patch.object(jobs, "_read_kline_cache", side_effect=fake_read),
            patch("fts.data_futures.ENERGY_CHAIN_SYMBOLS", ["SC0", "TA0"]),
        ):
            jobs.data_level_monitor_job()

        assert seen == ["SC0", "TA0"]

    def test_read_kline_cache_completeness_dedup(self) -> None:
        """GAP-148：双符号变体（SC/SC0）同日期重叠时，完整度去重保留字段完整行。"""
        from fts.scheduler.jobs import _read_kline_cache

        dates = pd.to_datetime(["2026-08-10", "2026-08-11", "2026-08-12"])
        base_cols = {
            "symbol": ["SC0", "SC0", "SC0"],
            "period": ["daily"] * 3,
            "date": dates,
            "open": [100.0] * 3,
            "high": [101.0] * 3,
            "low": [99.0] * 3,
            "close": [100.5] * 3,
            "volume": [1e5] * 3,
            "amount": [1e7] * 3,
            "hold": [2e5] * 3,
            "settle": [100.5] * 3,
            "pre_settle": [100.0] * 3,
            "oi_change": [100.0] * 3,
            "vwap": [100.2] * 3,
            "source": ["TDX_LOCAL"] * 3,
            "fetched_at": [pd.Timestamp.now()] * 3,
            "trace_id": ["t1"] * 3,
            "adj_factor": [1.0] * 3,
        }
        sc0 = pd.DataFrame(base_cols)
        sc = sc0.copy()
        sc["symbol"] = "SC"  # 历史裸数据变体：扩展字段全空 + 无 hold
        sc["vwap"] = np.nan
        sc["oi_change"] = np.nan
        sc["source"] = np.nan
        sc["fetched_at"] = np.nan
        sc["trace_id"] = np.nan
        sc["hold"] = np.nan
        combined = pd.concat([sc0, sc], ignore_index=True)

        class _FakeCon:
            def __init__(self, df: pd.DataFrame) -> None:
                self._df = df

            def execute(self, sql: str, params=None):  # type: ignore[no-untyped-def]
                return self

            def df(self) -> pd.DataFrame:
                return self._df

            def close(self) -> None:  # read_kline_cache finally 关闭连接
                return

        with patch("duckdb.connect", return_value=_FakeCon(combined)):
            out = _read_kline_cache(MagicMock(), "SC0", limit=120)

        assert out is not None and len(out) == 3  # 3 个独立日期不缩水
        assert set(out["symbol"]) == {"SC0"}  # 完整行保留
        assert int(out["vwap"].isna().sum()) == 0

    def test_sync_aggregator_has_tqsdk_enhancer(self) -> None:
        """GAP-148：sync 聚合器注册 TQSDK 增强源（此前 enhancers=[] 致 oi_change 全缺）。"""
        from fts.cli import _build_default_aggregator

        agg = _build_default_aggregator()
        names = [e.source_name for e in agg.enhancers]
        assert "TQSDK_ENHANCE" in names

    def test_task_registered(self) -> None:
        """data_level_monitor 任务已注册（v2.104.0+98 内部调度停用后 enabled=False）。"""
        from fts.scheduler.tasks import register_default_tasks, get_task

        register_default_tasks()
        task = get_task("data_level_monitor")
        assert task is not None
        assert task.enabled is False
        assert task.callable_path == "fts.scheduler.jobs.data_level_monitor_job"
