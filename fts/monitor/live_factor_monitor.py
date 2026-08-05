"""
fts.monitor.live_factor_monitor — Live 因子表现监控器（C.2 §5）。

跟踪因子 Live 表现与回测基线的偏离，超阈值（默认 30%）触发告警：
    - IC 偏离
    - Sharpe 偏离
    - 最大回撤偏离

用法:
    from fts.monitor.live_factor_monitor import LiveFactorMonitor

    monitor = LiveFactorMonitor()
    monitor.update_live_performance("fut_abc", {"ic": 0.05})
    alerts = monitor.check_deviation()

版本: v1.0.0
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 偏离率计算方向：deviation = |live - backtest| / max(|backtest|, eps)


class LiveFactorMonitor:
    """Live 因子表现监控器（C.2 §5）。

    存储因子回测基线（backtest）与 Live 表现，计算偏离并产出告警。
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        """初始化监控器。

        Args:
            config: LiveMonitorConfig 字典（deviation_threshold_pct 等）
        """
        self._config = dict(config or {})
        self._threshold = float(self._config.get("deviation_threshold_pct", 0.30))
        self._backtest: dict[str, dict[str, float]] = {}  # factor_id -> 回测基线
        self._live: dict[str, dict[str, float]] = {}  # factor_id -> Live 表现
        self._alerts: list[dict[str, Any]] = []

    # ─── 数据更新 ────────────────────────────────────────

    def set_backtest_baseline(
        self, factor_id: str, metrics: dict[str, float]
    ) -> None:
        """设置因子回测基线指标（ic/sharpe/max_drawdown 等）。"""
        self._backtest[factor_id] = {k: float(v) for k, v in metrics.items() if v is not None}

    def update_live_performance(
        self, factor_id: str, live_metrics: dict[str, float]
    ) -> None:
        """更新因子 Live 表现。"""
        cleaned = {k: float(v) for k, v in live_metrics.items() if v is not None}
        self._live[factor_id] = cleaned
        logger.info("[LiveMonitor] 更新 Live 表现 [factor=%s, metrics=%s]",
                    factor_id, cleaned)

    # ─── 偏离检查 ────────────────────────────────────────

    def check_deviation(self) -> list[dict[str, Any]]:
        """检查所有因子 Live vs 回测偏离，返回告警列表。"""
        alerts: list[dict[str, Any]] = []
        for factor_id in self._live:
            report = self._get_deviation_report(factor_id)
            for dev in report.get("deviations", []):
                if dev.get("severity") in ("warning", "critical"):
                    alerts.append(dev)
        self._alerts = alerts
        return alerts

    def get_factor_deviation(self, factor_id: str) -> dict[str, Any]:
        """获取单因子偏离报告。"""
        return self._get_deviation_report(factor_id)

    def get_alerts(self) -> list[dict[str, Any]]:
        """返回最近一次检查的告警列表。"""
        return list(self._alerts)

    def get_factor_ids(self) -> list[str]:
        """返回有 Live 数据的因子列表。"""
        return list(self._live.keys())

    # ─── 内部方法 ────────────────────────────────────────

    def _get_deviation_report(self, factor_id: str) -> dict[str, Any]:
        """构建单因子偏离报告。"""
        backtest = self._backtest.get(factor_id, {})
        live = self._live.get(factor_id, {})
        deviations: list[dict[str, Any]] = []
        worst: str = "normal"

        for metric in ("ic", "sharpe", "max_drawdown"):
            bt = backtest.get(metric)
            lv = live.get(metric)
            if bt is None or lv is None:
                continue
            dev_pct = abs(lv - bt) / max(abs(bt), 1e-9)
            severity = "critical" if dev_pct > self._threshold * 1.5 else (
                "warning" if dev_pct > self._threshold else "normal"
            )
            if severity != "normal":
                worst = "critical" if severity == "critical" else "warning"
            deviations.append({
                "alert_id": str(uuid.uuid4()),
                "factor_id": factor_id,
                "metric": metric,
                "backtest_value": bt,
                "live_value": lv,
                "deviation_pct": round(dev_pct, 4),
                "threshold_pct": self._threshold,
                "severity": severity,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "recommendation": (
                    "建议重新评估因子有效性" if severity == "critical"
                    else "建议持续观察" if severity == "warning" else "无需操作"
                ),
            })

        return {
            "factor_id": factor_id,
            "deviations": deviations,
            "overall_status": worst,
            "backtest_vs_live": {
                "backtest": backtest,
                "live": live,
            },
        }


__all__ = ["LiveFactorMonitor"]
