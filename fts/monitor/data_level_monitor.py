"""
fts.monitor.data_level_monitor — 数据级质量监控器（GAP-F06，v2.60.0）。

与 data_quality_monitor.py（因子级 IC 漂移 / 容量突变）互补，本模块聚焦
**数据本身**的四维质量：

    1. 完整性 (missing): 全表/关键字段缺失率
    2. 准确性 (outliers): close/volume/hold 的 3σ 异常值比例
    3. 复权一致性 (adjust_consistency): 复权因子序列与参考序列的相对偏差
    4. 多源分歧 (source_disagreement): 主源/次源 close 相对偏差中位数

设计原则:
    - 阈值全部可配置（DataLevelConfig），无硬编码
    - 每个 check_* 独立可测，run_all 汇总一次执行
    - 告警带冷却时间，避免重复刷屏
    - 数据为空/非法输入时返回空告警（不抛异常，监控器永不中断数据管线）

用法:
    monitor = DataLevelMonitor()
    alerts = monitor.run_all(
        df=ohlcv_df,
        adj=adj_factor_series,
        ref=reference_adj_series,
        primary_close=primary_close,
        secondary_close=secondary_close,
    )

版本: v1.0.0
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ─── 契约 ───────────────────────────────────────────────────


@dataclass
class DataLevelConfig:
    """数据级监控阈值配置（GAP-F06 步骤③：阈值可配置）。"""

    # 完整性：缺失率
    missing_ratio_warning: float = 0.05
    missing_ratio_critical: float = 0.20
    # 关键字段集合（缺失率逐字段检查）
    key_fields: tuple[str, ...] = ("close", "volume", "hold")  # GAP-085: 原 open_interest 与期货日线字段 hold 错位 → 修正
    # 代理字段失真（GAP-151，v2.105.0+21）：增强字段缺失率组级度量（超阈值→critical，
    # 下游走代理值如 hold 20 日滚动均量——失真风险显式化）
    proxy_fields: tuple[str, ...] = ("hold", "settle", "pre_settle")
    proxy_ratio_critical: float = 0.50

    # 准确性：异常值
    outlier_zscore: float = 3.0
    outlier_ratio_warning: float = 0.01
    outlier_ratio_critical: float = 0.05

    # 复权一致性：相对偏差超 tol 的样本占比
    adj_consistency_tol: float = 0.005
    adj_consistency_warning: float = 0.01
    adj_consistency_critical: float = 0.05

    # 多源分歧：close 相对偏差中位数
    source_disagreement_warning: float = 0.005
    source_disagreement_critical: float = 0.02

    # 告警冷却时间（秒）
    alert_cooldown: float = 3600.0


@dataclass
class DataLevelAlert:
    """数据级质量告警。"""

    alert_type: str  # missing_ratio / outlier_ratio / adjust_consistency / source_disagreement
    scope: str  # "market_data" 或 symbol
    severity: str  # warning / critical
    message: str
    metric_name: str
    metric_value: float
    threshold: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_type": self.alert_type,
            "scope": self.scope,
            "severity": self.severity,
            "message": self.message,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "threshold": self.threshold,
            "timestamp": self.timestamp,
        }


# ─── DataLevelMonitor ──────────────────────────────────────


class DataLevelMonitor:
    """数据级质量监控器（GAP-F06）。

    Args:
        config: 阈值配置（None 用默认）
        alert_callback: 告警回调（可选）
    """

    def __init__(
        self,
        config: Optional[DataLevelConfig] = None,
        alert_callback: Optional[Callable[[DataLevelAlert], None]] = None,
    ) -> None:
        self._config = config or DataLevelConfig()
        self._alert_callback = alert_callback
        self._last_alert_time: dict[str, float] = {}
        # 指标累积
        self._total_checks = 0
        self._total_alerts = 0
        self._critical_alerts = 0
        self._warning_alerts = 0
        self._last_snapshot: dict[str, Any] = {}

    # ─── 单一检查 ─────────────────────────────────────────

    def check_missing(self, df: pd.DataFrame, scope: str = "market_data") -> list[DataLevelAlert]:
        """完整性：全表缺失率 + 关键字段缺失率。"""
        alerts: list[DataLevelAlert] = []
        if df is None or df.empty:
            return alerts
        n_rows = len(df)

        # 派生字段豁免（GAP-148）：adj_factor 由 RollCalendar 在读取时计算
        # （data_futures.get_ohlcv 复权路径），kline_cache 按设计不持久化该列，
        # 其 100% 缺失属正常，不计入全表缺失率避免误报。
        derived_cols = [c for c in ("adj_factor",) if c in df.columns]
        if derived_cols:
            df = df.drop(columns=derived_cols)

        total_missing = float(df.isna().sum().sum()) / (n_rows * df.shape[1])
        if total_missing > self._config.missing_ratio_critical:
            alerts.append(
                self._make_alert(
                    "missing_ratio",
                    scope,
                    "critical",
                    f"全表缺失率 {total_missing:.1%} 超过严重阈值 {self._config.missing_ratio_critical:.1%}",
                    "total_missing_ratio",
                    total_missing,
                    self._config.missing_ratio_critical,
                )
            )
        elif total_missing > self._config.missing_ratio_warning:
            alerts.append(
                self._make_alert(
                    "missing_ratio",
                    scope,
                    "warning",
                    f"全表缺失率 {total_missing:.1%} 超过警告阈值 {self._config.missing_ratio_warning:.1%}",
                    "total_missing_ratio",
                    total_missing,
                    self._config.missing_ratio_warning,
                )
            )

        for col in self._config.key_fields:
            if col not in df.columns:
                continue
            ratio = float(df[col].isna().sum()) / n_rows
            if ratio > self._config.missing_ratio_critical:
                alerts.append(
                    self._make_alert(
                        "missing_ratio",
                        scope,
                        "critical",
                        f"字段 {col} 缺失率 {ratio:.1%} 超过严重阈值",
                        f"missing_ratio_{col}",
                        ratio,
                        self._config.missing_ratio_critical,
                    )
                )
            elif ratio > self._config.missing_ratio_warning:
                alerts.append(
                    self._make_alert(
                        "missing_ratio",
                        scope,
                        "warning",
                        f"字段 {col} 缺失率 {ratio:.1%} 超过警告阈值",
                        f"missing_ratio_{col}",
                        ratio,
                        self._config.missing_ratio_warning,
                    )
                )

        # 代理字段失真检查（GAP-151，v2.105.0+21）：增强字段缺失率组级度量，
        # 超阈值 → critical（下游走代理值，失真风险显式化，避免静默代理）
        proxy_ratios = []
        for col in self._config.proxy_fields:
            if col not in df.columns:
                continue
            proxy_ratios.append(float(df[col].isna().sum()) / n_rows)
        if proxy_ratios:
            avg_proxy = sum(proxy_ratios) / len(proxy_ratios)
            if avg_proxy > self._config.proxy_ratio_critical:
                alerts.append(
                    self._make_alert(
                        "proxy_missing_ratio",
                        scope,
                        "critical",
                        f"代理字段（{'+'.join(self._config.proxy_fields)}）平均缺失率 "
                        f"{avg_proxy:.1%} 超过严重阈值 {self._config.proxy_ratio_critical:.1%}"
                        f"——下游将走代理值（失真风险）",
                        "proxy_missing_ratio",
                        avg_proxy,
                        self._config.proxy_ratio_critical,
                    )
                )
        return alerts

    def check_outliers(self, df: pd.DataFrame, scope: str = "market_data") -> list[DataLevelAlert]:
        """准确性：关键字段 3σ 异常值比例。"""
        alerts: list[DataLevelAlert] = []
        if df is None or df.empty:
            return alerts
        for col in self._config.key_fields:
            if col not in df.columns or df[col].dropna().empty:
                continue
            series = df[col].dropna().astype(float)
            if len(series) < 2:
                continue
            mean, std = float(series.mean()), float(series.std())
            if std == 0:
                continue
            ratio = float(((series - mean).abs() > self._config.outlier_zscore * std).sum()) / len(series)
            if ratio > self._config.outlier_ratio_critical:
                alerts.append(
                    self._make_alert(
                        "outlier_ratio",
                        scope,
                        "critical",
                        f"字段 {col} 异常值比例 {ratio:.1%}（{self._config.outlier_zscore}σ）超过严重阈值",
                        f"outlier_ratio_{col}",
                        ratio,
                        self._config.outlier_ratio_critical,
                    )
                )
            elif ratio > self._config.outlier_ratio_warning:
                alerts.append(
                    self._make_alert(
                        "outlier_ratio",
                        scope,
                        "warning",
                        f"字段 {col} 异常值比例 {ratio:.1%}（{self._config.outlier_zscore}σ）超过警告阈值",
                        f"outlier_ratio_{col}",
                        ratio,
                        self._config.outlier_ratio_warning,
                    )
                )
        return alerts

    def check_adjustment_consistency(
        self,
        adj: pd.Series,
        reference: pd.Series,
        scope: str = "market_data",
    ) -> list[DataLevelAlert]:
        """复权一致性：复权因子序列与参考序列相对偏差超容差的样本占比。"""
        if adj is None or reference is None:
            return []
        merged = pd.concat([adj.rename("a"), reference.rename("r")], axis=1).dropna()
        if merged.empty:
            return []
        rel_dev = (merged["a"] - merged["r"]).abs() / merged["r"].abs().clip(lower=1e-12)
        over_tol = float((rel_dev > self._config.adj_consistency_tol).mean())
        if over_tol > self._config.adj_consistency_critical:
            alerts = [
                self._make_alert(
                    "adjust_consistency",
                    scope,
                    "critical",
                    f"复权因子一致性异常：{over_tol:.1%} 样本偏差超过 {self._config.adj_consistency_tol:.1%}",
                    "adjust_consistency_violation",
                    over_tol,
                    self._config.adj_consistency_critical,
                )
            ]
        elif over_tol > self._config.adj_consistency_warning:
            alerts = [
                self._make_alert(
                    "adjust_consistency",
                    scope,
                    "warning",
                    f"复权因子一致性偏差：{over_tol:.1%} 样本偏差超过 {self._config.adj_consistency_tol:.1%}",
                    "adjust_consistency_violation",
                    over_tol,
                    self._config.adj_consistency_warning,
                )
            ]
        else:
            alerts = []
        return alerts

    def check_source_disagreement(
        self,
        primary: pd.Series,
        secondary: pd.Series,
        scope: str = "market_data",
    ) -> list[DataLevelAlert]:
        """多源分歧：主源/次源 close 相对偏差中位数。"""
        if primary is None or secondary is None:
            return []
        merged = pd.concat([primary.rename("p"), secondary.rename("s")], axis=1).dropna()
        if merged.empty:
            return []
        deviation = (merged["p"] - merged["s"]).abs() / merged["s"].abs().clip(lower=1e-9)
        median_dev = float(deviation.median())
        if median_dev > self._config.source_disagreement_critical:
            alerts = [
                self._make_alert(
                    "source_disagreement",
                    scope,
                    "critical",
                    f"多源分歧中位数 {median_dev:.2%} 超过严重阈值 {self._config.source_disagreement_critical:.2%}",
                    "source_disagreement_median",
                    median_dev,
                    self._config.source_disagreement_critical,
                )
            ]
        elif median_dev > self._config.source_disagreement_warning:
            alerts = [
                self._make_alert(
                    "source_disagreement",
                    scope,
                    "warning",
                    f"多源分歧中位数 {median_dev:.2%} 超过警告阈值 {self._config.source_disagreement_warning:.2%}",
                    "source_disagreement_median",
                    median_dev,
                    self._config.source_disagreement_warning,
                )
            ]
        else:
            alerts = []
        return alerts

    # ─── 汇总执行 ─────────────────────────────────────────

    def run_all(
        self,
        df: Optional[pd.DataFrame] = None,
        adj: Optional[pd.Series] = None,
        reference: Optional[pd.Series] = None,
        primary_close: Optional[pd.Series] = None,
        secondary_close: Optional[pd.Series] = None,
        scope: str = "market_data",
    ) -> list[DataLevelAlert]:
        """执行全部数据级检查，返回触发的告警列表。"""
        self._total_checks += 1
        alerts: list[DataLevelAlert] = []
        alerts.extend(self.check_missing(df, scope))
        alerts.extend(self.check_outliers(df, scope))
        alerts.extend(self.check_adjustment_consistency(adj, reference, scope))
        alerts.extend(self.check_source_disagreement(primary_close, secondary_close, scope))

        for alert in alerts:
            self._handle_alert(alert)

        self._last_snapshot = {
            "scope": scope,
            "alert_count": len(alerts),
            "critical": sum(1 for a in alerts if a.severity == "critical"),
            "warning": sum(1 for a in alerts if a.severity == "warning"),
            "types": sorted({a.alert_type for a in alerts}),
        }
        return alerts

    # ─── 内部工具 ─────────────────────────────────────────

    def _make_alert(
        self,
        alert_type: str,
        scope: str,
        severity: str,
        message: str,
        metric_name: str,
        metric_value: float,
        threshold: float,
    ) -> DataLevelAlert:
        return DataLevelAlert(
            alert_type=alert_type,
            scope=scope,
            severity=severity,
            message=message,
            metric_name=metric_name,
            metric_value=round(float(metric_value), 6),
            threshold=threshold,
        )

    def _handle_alert(self, alert: DataLevelAlert) -> None:
        """冷却检查 + 计数 + 日志 + 回调。"""
        # 按 metric_name 区分冷却（同一类型不同字段/来源的告警互不阻塞）
        cooldown_key = f"{alert.scope}_{alert.metric_name}"
        last = self._last_alert_time.get(cooldown_key, 0.0)
        now = time.time()
        if now - last < self._config.alert_cooldown:
            logger.debug("数据级告警冷却中，跳过 [%s]", cooldown_key)
            return
        self._last_alert_time[cooldown_key] = now

        self._total_alerts += 1
        if alert.severity == "critical":
            self._critical_alerts += 1
        else:
            self._warning_alerts += 1

        level = logging.CRITICAL if alert.severity == "critical" else logging.WARNING
        logger.log(
            level,
            "数据级质量告警 [type=%s, scope=%s]: %s",
            alert.alert_type,
            alert.scope,
            alert.message,
        )
        if self._alert_callback:
            try:
                self._alert_callback(alert)
            except Exception as e:  # noqa: BLE001
                logger.error("数据级告警回调失败: %s", e)

    def get_snapshot(self) -> dict[str, Any]:
        """最近一次 run_all 的快照。"""
        return dict(self._last_snapshot)

    def get_metrics_snapshot(self) -> dict[str, Any]:
        """指标汇总快照（Prometheus/JSON 用）。"""
        return {
            "total_checks": self._total_checks,
            "total_alerts": self._total_alerts,
            "critical_alerts": self._critical_alerts,
            "warning_alerts": self._warning_alerts,
            "last": self._last_snapshot,
        }


def create_data_level_monitor(
    alert_callback: Optional[Callable[[DataLevelAlert], None]] = None,
) -> DataLevelMonitor:
    """创建默认配置的数据级监控器（GAP-F06 便捷入口）。"""
    return DataLevelMonitor(config=DataLevelConfig(), alert_callback=alert_callback)


__all__ = [
    "DataLevelConfig",
    "DataLevelAlert",
    "DataLevelMonitor",
    "create_data_level_monitor",
]
