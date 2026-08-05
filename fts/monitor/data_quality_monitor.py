"""
fts.monitor.data_quality_monitor — 因子数据质量实时监控 (Phase B.1)。

对生产环境中运行的因子进行实时质量监控，重点检测：
    1. IC 漂移 (IC Drift): 实时 IC 偏离基准 IC 的程度。
    2. 容量突变 (Capacity Shock): 因子资金承载能力的剧烈变化。

提供基于阈值的告警机制，支持日志记录和回调通知。

用法:
    monitor = DataQualityMonitor()
    monitor.register_factor("factor_001", baseline_ic=0.05, baseline_capacity=1_000_000)
    alert = monitor.check("factor_001", current_ic=0.01, current_capacity=200_000)
    if alert:
        print(f"Alert: {alert}")

版本: v0.1.0
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 契约 ───────────────────────────────────────────────────


@dataclass
class FactorBaseline:
    """因子基准数据。"""

    factor_id: str
    baseline_ic: float
    baseline_capacity: float
    ic_std: float = 0.01  # IC 标准差 (用于 Z-Score 计算)
    capacity_std: float = 0.0  # 容量标准差


@dataclass
class QualityAlert:
    """质量告警信息。"""

    factor_id: str
    alert_type: Literal["ic_drift", "capacity_shock"]
    severity: Literal["warning", "critical"]
    message: str
    metric_name: str
    metric_value: float
    baseline_value: float
    threshold: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_id": self.factor_id,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "message": self.message,
            "metric_name": self.metric_name,
            "metric_value": self.metric_value,
            "baseline_value": self.baseline_value,
            "threshold": self.threshold,
            "timestamp": self.timestamp,
        }


# ─── 监控配置 ──────────────────────────────────────────────


@dataclass
class MonitorConfig:
    """监控配置。"""

    # IC 漂移告警
    ic_zscore_warning: float = 2.0  # IC Z-Score 警告阈值
    ic_zscore_critical: float = 3.0  # IC Z-Score 严重阈值

    # 容量突变告警
    capacity_change_warning: float = 0.5  # 容量变化率警告阈值 (50%)
    capacity_change_critical: float = 0.8  # 容量变化率严重阈值 (80%)

    # 告警冷却时间 (秒) - 同一因子同一类型告警的最小间隔
    alert_cooldown: float = 3600.0


# ─── DataQualityMonitor ───────────────────────────────────


class DataQualityMonitor:
    """因子数据质量实时监控器。

    Args:
        config: 监控配置 (默认值若为 None)
        alert_callback: 告警回调函数 (可选)
    """

    def __init__(
        self,
        config: Optional[MonitorConfig] = None,
        alert_callback: Optional[Callable[[QualityAlert], None]] = None,
    ) -> None:
        self._config = config or MonitorConfig()
        self._alert_callback = alert_callback
        self._baselines: dict[str, FactorBaseline] = {}
        # 记录最近告警时间用于冷却
        self._last_alert_time: dict[str, float] = {}
        # Prometheus 指标追踪
        self._total_checks = 0
        self._total_alerts = 0
        self._critical_alerts = 0
        self._warning_alerts = 0
        self._last_completeness_ratio = 1.0
        self._last_validation_time = 0.0
        self._market_data_valid = True
        self._factor_check_count = 0
        self._ic_drift_alerts = 0
        self._capacity_shock_alerts = 0

    def register_factor(
        self,
        factor_id: str,
        baseline_ic: float,
        baseline_capacity: float,
        ic_std: float = 0.01,
        capacity_std: float = 0.0,
    ) -> None:
        """注册因子基准数据。

        Args:
            factor_id: 因子唯一标识
            baseline_ic: 基准 IC (历史均值)
            baseline_capacity: 基准容量 (历史均值)
            ic_std: IC 标准差
            capacity_std: 容量标准差
        """
        self._baselines[factor_id] = FactorBaseline(
            factor_id=factor_id,
            baseline_ic=baseline_ic,
            baseline_capacity=baseline_capacity,
            ic_std=ic_std,
            capacity_std=capacity_std,
        )
        logger.info(
            "注册因子基准 [factor_id=%s, ic=%.4f, capacity=%.0f]",
            factor_id, baseline_ic, baseline_capacity,
        )

    def check(
        self,
        factor_id: str,
        current_ic: Optional[float] = None,
        current_capacity: Optional[float] = None,
    ) -> list[QualityAlert]:
        """检查因子数据质量，返回触发的告警列表。

        Args:
            factor_id: 因子唯一标识
            current_ic: 当前实时 IC
            current_capacity: 当前估算容量

        Returns:
            触发的告警列表 (可能为空)
        """
        self._total_checks += 1
        self._factor_check_count += 1

        baseline = self._baselines.get(factor_id)
        if baseline is None:
            logger.warning("因子未注册，跳过检查 [factor_id=%s]", factor_id)
            return []

        alerts: list[QualityAlert] = []

        # 1. IC 漂移检测
        if current_ic is not None:
            ic_alert = self._check_ic_drift(baseline, current_ic)
            if ic_alert:
                alerts.append(ic_alert)
                self._ic_drift_alerts += 1

        # 2. 容量突变检测
        if current_capacity is not None:
            cap_alert = self._check_capacity_shock(baseline, current_capacity)
            if cap_alert:
                alerts.append(cap_alert)
                self._capacity_shock_alerts += 1

        # 处理告警
        for alert in alerts:
            self._handle_alert(alert)

        return alerts

    def _check_ic_drift(
        self, baseline: FactorBaseline, current_ic: float
    ) -> Optional[QualityAlert]:
        """检测 IC 漂移。"""
        if baseline.ic_std <= 0:
            return None

        # 计算 Z-Score
        z_score = (current_ic - baseline.baseline_ic) / baseline.ic_std
        abs_z = abs(z_score)

        if abs_z >= self._config.ic_zscore_critical:
            severity = "critical"
        elif abs_z >= self._config.ic_zscore_warning:
            severity = "warning"
        else:
            return None

        direction = "上升" if z_score > 0 else "下降"
        return QualityAlert(
            factor_id=baseline.factor_id,
            alert_type="ic_drift",
            severity=severity,
            message=(
                f"因子 IC {direction} 漂移: Z-Score={z_score:.2f}, "
                f"当前IC={current_ic:.4f}, 基准IC={baseline.baseline_ic:.4f}"
            ),
            metric_name="IC",
            metric_value=current_ic,
            baseline_value=baseline.baseline_ic,
            threshold=self._config.ic_zscore_critical if severity == "critical" else self._config.ic_zscore_warning,
        )

    def _check_capacity_shock(
        self, baseline: FactorBaseline, current_capacity: float
    ) -> Optional[QualityAlert]:
        """检测容量突变。"""
        if baseline.baseline_capacity <= 0:
            return None

        # 计算变化率
        change_rate = (current_capacity - baseline.baseline_capacity) / baseline.baseline_capacity
        abs_change = abs(change_rate)

        if abs_change >= self._config.capacity_change_critical:
            severity = "critical"
        elif abs_change >= self._config.capacity_change_warning:
            severity = "warning"
        else:
            return None

        direction = "增加" if change_rate > 0 else "减少"
        return QualityAlert(
            factor_id=baseline.factor_id,
            alert_type="capacity_shock",
            severity=severity,
            message=(
                f"因子容量{direction}突变: 变化率={change_rate:.1%}, "
                f"当前容量={current_capacity:.0f}, 基准容量={baseline.baseline_capacity:.0f}"
            ),
            metric_name="Capacity",
            metric_value=current_capacity,
            baseline_value=baseline.baseline_capacity,
            threshold=self._config.capacity_change_critical if severity == "critical" else self._config.capacity_change_warning,
        )

    def _handle_alert(self, alert: QualityAlert) -> None:
        """处理告警（冷却检查 + 日志 + 回调 + 指标累积）。"""
        self._total_alerts += 1
        if alert.severity == "critical":
            self._critical_alerts += 1
        else:
            self._warning_alerts += 1

        # 检查冷却
        cooldown_key = f"{alert.factor_id}_{alert.alert_type}"
        last_time = self._last_alert_time.get(cooldown_key, 0)
        now = time.time()

        if now - last_time < self._config.alert_cooldown:
            logger.debug(
                "告警冷却中，跳过 [factor_id=%s, type=%s]",
                alert.factor_id, alert.alert_type,
            )
            return

        # 更新冷却时间
        self._last_alert_time[cooldown_key] = now

        # 日志记录
        log_level = logging.WARNING if alert.severity == "warning" else logging.CRITICAL
        logger.log(
            log_level,
            "数据质量告警 [factor_id=%s, type=%s, severity=%s]: %s",
            alert.factor_id, alert.alert_type, alert.severity, alert.message,
        )

        # 回调通知
        if self._alert_callback:
            try:
                self._alert_callback(alert)
            except Exception as e:
                logger.error("告警回调执行失败: %s", e)

    def get_factor_status(self, factor_id: str) -> Optional[dict[str, Any]]:
        """查询因子状态。"""
        baseline = self._baselines.get(factor_id)
        if baseline is None:
            return None
        return {
            "factor_id": factor_id,
            "baseline_ic": baseline.baseline_ic,
            "baseline_capacity": baseline.baseline_capacity,
            "registered": True,
        }

    def list_registered(self) -> list[str]:
        """列出所有已注册因子。"""
        return list(self._baselines.keys())

    def validate_market_data(
        self,
        data: pd.DataFrame,
        forward_returns: Optional[np.ndarray] = None,
    ) -> list[QualityAlert]:
        """校验市场数据质量（完整性检查）。

        在数据加载流程中调用，检查关键字段的完整性和时效性。

        Args:
            data: OHLCV DataFrame (index=timestamp, columns=[open,high,low,close,volume])
            forward_returns: 未来收益率数组 (可选)

        Returns:
            数据质量告警列表
        """
        alerts: list[QualityAlert] = []
        self._last_validation_time = time.time()

        if data is None or data.empty:
            self._market_data_valid = False
            self._last_completeness_ratio = 0.0
            alerts.append(QualityAlert(
                factor_id="market_data",
                alert_type="ic_drift",
                severity="critical",
                message="市场数据为空，无法执行演化",
                metric_name="data_completeness",
                metric_value=0.0,
                baseline_value=1.0,
                threshold=0.5,
            ))
            self._total_alerts += 1
            self._critical_alerts += 1
            self._total_checks += 1
            return alerts

        n_rows = len(data)
        required_cols = {"open", "high", "low", "close", "volume"}
        existing_cols = set(data.columns)
        missing_cols = required_cols - existing_cols

        if missing_cols:
            self._market_data_valid = False
            alerts.append(QualityAlert(
                factor_id="market_data",
                alert_type="ic_drift",
                severity="critical",
                message=f"缺少必要字段: {missing_cols}",
                metric_name="field_completeness",
                metric_value=0.0,
                baseline_value=1.0,
                threshold=0.8,
            ))
            self._total_alerts += 1
            self._critical_alerts += 1

        total_missing_ratio = 0.0
        n_checked = 0
        for col in required_cols & existing_cols:
            missing_ratio = float(data[col].isna().sum()) / n_rows if n_rows > 0 else 1.0
            total_missing_ratio += missing_ratio
            n_checked += 1
            if missing_ratio > 0.05:
                sev = "critical" if missing_ratio > 0.2 else "warning"
                alerts.append(QualityAlert(
                    factor_id="market_data",
                    alert_type="ic_drift",
                    severity=sev,
                    message=f"字段 {col} 缺失率 {missing_ratio:.1%}",
                    metric_name="missing_ratio",
                    metric_value=missing_ratio,
                    baseline_value=0.0,
                    threshold=0.05,
                ))
                self._total_alerts += 1
                if sev == "critical":
                    self._critical_alerts += 1
                else:
                    self._warning_alerts += 1

        if n_checked > 0:
            avg_missing = total_missing_ratio / n_checked
            self._last_completeness_ratio = max(0.0, 1.0 - avg_missing)
        else:
            self._last_completeness_ratio = 0.0

        if forward_returns is not None and len(forward_returns) > 0:
            fr_missing = float(np.isnan(forward_returns).sum()) / len(forward_returns)
            if fr_missing > 0.05:
                sev = "critical" if fr_missing > 0.2 else "warning"
                alerts.append(QualityAlert(
                    factor_id="market_data",
                    alert_type="ic_drift",
                    severity=sev,
                    message=f"forward_returns 缺失率 {fr_missing:.1%}",
                    metric_name="forward_returns_missing",
                    metric_value=fr_missing,
                    baseline_value=0.0,
                    threshold=0.05,
                ))
                self._total_alerts += 1
                if sev == "critical":
                    self._critical_alerts += 1
                else:
                    self._warning_alerts += 1

        self._market_data_valid = not any(a.severity == "critical" for a in alerts)

        for alert in alerts:
            self._handle_alert(alert)

        if alerts:
            logger.warning(
                "市场数据质量检查发现 %d 个告警 (critical=%d)",
                len(alerts),
                sum(1 for a in alerts if a.severity == "critical"),
            )

        self._total_checks += 1
        return alerts

    def get_prometheus_metrics(self) -> str:
        """生成 Prometheus 文本格式指标。

        Returns:
            Prometheus exposition format 字符串
        """
        registered_count = len(self._baselines)
        lines = [
            "# HELP fts_data_quality_data_completeness_ratio 数据完整性比率 (1.0=完美)",
            "# TYPE fts_data_quality_data_completeness_ratio gauge",
            f"fts_data_quality_data_completeness_ratio {self._last_completeness_ratio:.4f}",
            "",
            "# HELP fts_data_quality_market_data_valid 市场数据是否有效 (1=有效)",
            "# TYPE fts_data_quality_market_data_valid gauge",
            f"fts_data_quality_market_data_valid {1.0 if self._market_data_valid else 0.0}",
            "",
            "# HELP fts_data_quality_total_checks 数据质量检查总次数",
            "# TYPE fts_data_quality_total_checks counter",
            f"fts_data_quality_total_checks {self._total_checks}",
            "",
            "# HELP fts_data_quality_factor_check_count 因子质量检查次数",
            "# TYPE fts_data_quality_factor_check_count counter",
            f"fts_data_quality_factor_check_count {self._factor_check_count}",
            "",
            "# HELP fts_data_quality_total_alerts 告警总次数",
            "# TYPE fts_data_quality_total_alerts counter",
            f"fts_data_quality_total_alerts {self._total_alerts}",
            "",
            "# HELP fts_data_quality_critical_alerts 严重告警次数",
            "# TYPE fts_data_quality_critical_alerts counter",
            f"fts_data_quality_critical_alerts {self._critical_alerts}",
            "",
            "# HELP fts_data_quality_warning_alerts 警告告警次数",
            "# TYPE fts_data_quality_warning_alerts counter",
            f"fts_data_quality_warning_alerts {self._warning_alerts}",
            "",
            "# HELP fts_data_quality_ic_drift_alerts IC 漂移告警次数",
            "# TYPE fts_data_quality_ic_drift_alerts counter",
            f"fts_data_quality_ic_drift_alerts {self._ic_drift_alerts}",
            "",
            "# HELP fts_data_quality_capacity_shock_alerts 容量突变告警次数",
            "# TYPE fts_data_quality_capacity_shock_alerts counter",
            f"fts_data_quality_capacity_shock_alerts {self._capacity_shock_alerts}",
            "",
            "# HELP fts_data_quality_registered_factors 已注册基准的因子数",
            "# TYPE fts_data_quality_registered_factors gauge",
            f"fts_data_quality_registered_factors {registered_count}",
            "",
            "# HELP fts_data_quality_last_validation_timestamp 最近校验时间戳",
            "# TYPE fts_data_quality_last_validation_timestamp gauge",
            f"fts_data_quality_last_validation_timestamp {self._last_validation_time}",
            "",
        ]
        return "\n".join(lines)

    def get_metrics_snapshot(self) -> dict[str, Any]:
        """获取指标快照（JSON 格式）。"""
        return {
            "data_completeness_ratio": self._last_completeness_ratio,
            "market_data_valid": self._market_data_valid,
            "total_checks": self._total_checks,
            "factor_check_count": self._factor_check_count,
            "total_alerts": self._total_alerts,
            "critical_alerts": self._critical_alerts,
            "warning_alerts": self._warning_alerts,
            "ic_drift_alerts": self._ic_drift_alerts,
            "capacity_shock_alerts": self._capacity_shock_alerts,
            "registered_factors": len(self._baselines),
            "last_validation_time": self._last_validation_time,
        }


# ─── B.1 三维指标计算函数（完整性/准确性/及时性） ──────────


def compute_coverage_ratio(df: pd.DataFrame, expected_symbols: set[str]) -> float:
    """完整性: 品种覆盖率 = 实际品种 ∩ 预期品种 / 预期品种数。"""
    if not expected_symbols:
        return 0.0
    if "symbol" not in df.columns or df.empty:
        return 0.0
    actual = set(df["symbol"].unique())
    return len(actual & expected_symbols) / len(expected_symbols)


def compute_timestamp_continuity(df: pd.DataFrame, freq: str = "D") -> float:
    """完整性: 时间戳连续率（按指定频率的连续时间戳对齐）。"""
    if df.empty or "timestamp" not in df.columns:
        return 0.0
    timestamps = pd.to_datetime(df["timestamp"]).sort_values().unique()
    if len(timestamps) < 2:
        return 1.0
    expected = pd.date_range(start=timestamps[0], end=timestamps[-1], freq=freq)
    if len(expected) == 0:
        return 0.0
    actual_set = set(timestamps)
    return len(actual_set & set(expected)) / len(expected)


def compute_field_completeness(df: pd.DataFrame, field: str) -> float:
    """完整性: 单个字段非空率。"""
    if field not in df.columns:
        return 0.0
    if df.empty:
        return 0.0
    return float(df[field].notna().mean())


def compute_missing_ratio(df: pd.DataFrame) -> float:
    """完整性: 缺失值率（全表 NaN 占比）。"""
    if df.empty:
        return 0.0
    return float(df.isna().sum().sum() / (df.shape[0] * df.shape[1]))


def compute_cross_source_deviation(primary: pd.Series, secondary: pd.Series) -> float:
    """准确性: 多源交叉偏差率（相对偏差的中位数）。"""
    merged = pd.concat([primary.rename("p"), secondary.rename("s")], axis=1).dropna()
    if merged.empty:
        return 0.0
    deviations = (merged["p"] - merged["s"]).abs() / merged["s"].abs().clip(lower=0.001)
    return float(deviations.median())


def compute_outlier_ratio(series: pd.Series, threshold: float = 3.0) -> float:
    """准确性: 异常值比率（3σ 准则）。"""
    if len(series) < 2:
        return 0.0
    mean, std = float(series.mean()), float(series.std())
    if std == 0:
        return 0.0
    outliers = ((series - mean).abs() > threshold * std).sum()
    return float(outliers / len(series))


def compute_jump_detection(df: pd.DataFrame, threshold: float = 0.15) -> int:
    """准确性: 价格跳变次数（单日涨跌 > threshold）。"""
    if "close" not in df.columns or len(df) < 2:
        return 0
    returns = df["close"].pct_change().abs()
    return int((returns > threshold).sum())


def compute_data_drift_rate(reference: pd.Series, current: pd.Series,
                            bins: int = 10) -> float:
    """准确性: PSI 数据漂移率（> 0.25 表示严重漂移）。"""
    if reference.dropna().empty or current.dropna().empty:
        return 0.0
    ref_counts, bin_edges = np.histogram(reference.dropna(), bins=bins)
    curr_counts, _ = np.histogram(current.dropna(), bins=bin_edges)
    ref_ratios = np.clip(ref_counts / ref_counts.sum(), 1e-6, None)
    curr_ratios = np.clip(curr_counts / curr_counts.sum(), 1e-6, None)
    return float(np.sum((curr_ratios - ref_ratios) * np.log(curr_ratios / ref_ratios)))


def compute_update_delay(latest_timestamp, now=None) -> float:
    """及时性: 数据更新延迟（秒）。"""
    from datetime import datetime

    now = now or datetime.now()
    ts = pd.to_datetime(latest_timestamp)
    return float((now - ts.to_pydatetime()).total_seconds())


def compute_cache_hit_ratio(hits: int, total: int) -> float:
    """及时性: 缓存命中率。"""
    return hits / total if total > 0 else 0.0


def compute_freshness(df: pd.DataFrame, now=None) -> float:
    """及时性: 数据新鲜度（最大数据年龄，秒）。"""
    from datetime import datetime

    if df.empty or "timestamp" not in df.columns:
        return float("inf")
    now = now or datetime.now()
    latest = pd.to_datetime(df["timestamp"]).max().to_pydatetime()
    return float((now - latest).total_seconds())


def evaluate_source_data(df: pd.DataFrame,
                         expected_symbols: set[str] | None = None,
                         freq: str = "D",
                         reference_close: pd.Series | None = None) -> dict[str, Any]:
    """汇总评估单数据源的三维质量指标（B.1）。

    Args:
        df: OHLCV DataFrame（含 symbol/timestamp/close 等列）
        expected_symbols: 预期品种集合（缺省取实际品种）
        freq: 时间戳连续率频率
        reference_close: 参考 close 序列（用于 PSI 漂移，可选）

    Returns:
        {
            "completeness": {...},
            "accuracy": {...},
            "timeliness": {...},
        }
    """
    expected = expected_symbols or (
        set(df["symbol"].unique()) if "symbol" in df.columns else set()
    )
    completeness = {
        "coverage_ratio": compute_coverage_ratio(df, expected),
        "timestamp_continuity": compute_timestamp_continuity(df, freq),
        "missing_ratio": compute_missing_ratio(df),
    }
    if "close" in df.columns:
        completeness["field_completeness_close"] = compute_field_completeness(df, "close")

    accuracy: dict[str, Any] = {"outlier_ratio": 0.0, "jump_detection_count": 0}
    if "close" in df.columns:
        accuracy["outlier_ratio"] = compute_outlier_ratio(df["close"])
        accuracy["jump_detection_count"] = compute_jump_detection(df)
        if reference_close is not None and len(reference_close) > 0:
            accuracy["data_drift_rate"] = compute_data_drift_rate(
                reference_close, df["close"]
            )

    timeliness = {
        "freshness_seconds": compute_freshness(df),
        "cache_hit_ratio": 0.0,
    }
    if "timestamp" in df.columns and not df.empty:
        timeliness["update_delay_seconds"] = compute_update_delay(
            pd.to_datetime(df["timestamp"]).max()
        )

    return {
        "completeness": completeness,
        "accuracy": accuracy,
        "timeliness": timeliness,
    }


# ─── 便捷函数 ───────────────────────────────────────────────


def create_default_monitor(
    alert_callback: Optional[Callable[[QualityAlert], None]] = None,
) -> DataQualityMonitor:
    """创建使用默认配置的监控器。"""
    return DataQualityMonitor(
        config=MonitorConfig(),
        alert_callback=alert_callback,
    )


__all__ = [
    "DataQualityMonitor",
    "MonitorConfig",
    "FactorBaseline",
    "QualityAlert",
    "create_default_monitor",
]
