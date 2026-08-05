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

        # 2. 容量突变检测
        if current_capacity is not None:
            cap_alert = self._check_capacity_shock(baseline, current_capacity)
            if cap_alert:
                alerts.append(cap_alert)

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
        """处理告警（冷却检查 + 日志 + 回调）。"""
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
