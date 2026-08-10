"""
fts.monitor.live_factor_monitor — Live 因子表现监控器（C.2 §5）。

跟踪因子 Live 表现与回测基线的偏离，超阈值（默认 30%）触发告警：
    - IC 偏离
    - Sharpe 偏离
    - 最大回撤偏离

GAP-I402（v2.77.0）增强：
    - ingest_live_ic: 直接消费 GAP-I401 实盘反馈数据源
      （LiveFeedbackImporter.compute_live_ic 输出 + LiveVsBacktestICReport 的 status 字段）
    - 因子衰减告警：decayed → critical「衰减退役建议（GAP-I305 闭环）」/ weak → warning「持续观察」
    - Prometheus 兼容指标日志：METRIC live_factor_ic{factor_id=..}

用法:
    from fts.monitor.live_factor_monitor import LiveFactorMonitor

    monitor = LiveFactorMonitor()
    monitor.update_live_performance("fut_abc", {"ic": 0.05})
    alerts = monitor.check_deviation()

版本: v1.1.0
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
    GAP-I402（v2.77.0）：支持 ingest_live_ic 一键消费 GAP-I401 实盘反馈数据源，
    并内置因子衰减状态（ok/weak/decayed）告警。
    """

    def __init__(self, config: Optional[dict[str, Any]] = None) -> None:
        """初始化监控器。

        Args:
            config: LiveMonitorConfig 字典（deviation_threshold_pct / decay_alert_enabled 等）
        """
        self._config = dict(config or {})
        self._threshold = float(self._config.get("deviation_threshold_pct", 0.30))
        self._decay_alert_enabled = bool(self._config.get("decay_alert_enabled", True))
        self._backtest: dict[str, dict[str, float]] = {}  # factor_id -> 回测基线
        self._live: dict[str, dict[str, float]] = {}  # factor_id -> Live 表现
        self._decay: dict[str, str] = {}  # factor_id -> 衰减状态 (ok/weak/decayed)
        self._alerts: list[dict[str, Any]] = []

    # ─── 数据更新 ────────────────────────────────────────

    def set_backtest_baseline(self, factor_id: str, metrics: dict[str, float]) -> None:
        """设置因子回测基线指标（ic/sharpe/max_drawdown 等）。"""
        self._backtest[factor_id] = {k: float(v) for k, v in metrics.items() if v is not None}

    def update_live_performance(self, factor_id: str, live_metrics: dict[str, float]) -> None:
        """更新因子 Live 表现。"""
        cleaned = {k: float(v) for k, v in live_metrics.items() if v is not None}
        self._live[factor_id] = cleaned
        logger.info("[LiveMonitor] 更新 Live 表现 [factor=%s, metrics=%s]", factor_id, cleaned)

    # ─── GAP-I401 实盘反馈数据源接入（GAP-I402） ──────────

    def ingest_live_ic(
        self,
        live_ic_result: dict[str, Any],
        backtest_ic_map: Optional[dict[str, float]] = None,
        decay_status_map: Optional[dict[str, str]] = None,
    ) -> list[dict[str, Any]]:
        """消费 GAP-I401 实盘 IC 结果，构建基线/实盘并返回偏离+衰减告警。

        Args:
            live_ic_result: ``LiveFeedbackImporter.compute_live_ic`` 输出
                （{factors: {factor_id: {ic, n_days, mean_return}}, overall_ic, n_records}）
            backtest_ic_map: factor_id → 回测 IC（可选，来自因子目录）
            decay_status_map: factor_id → 衰减状态（ok/weak/decayed，可选，
                来自 ``LiveVsBacktestICReport.generate`` 的 status 字段）

        Returns:
            告警列表（偏离 + 衰减）
        """
        factors = (live_ic_result or {}).get("factors", {})
        for fid, stats in factors.items():
            bt_ic = (backtest_ic_map or {}).get(fid)
            if bt_ic is not None:
                self.set_backtest_baseline(fid, {"ic": float(bt_ic)})
            self.update_live_performance(fid, {"ic": float(stats.get("ic", 0.0))})
            if decay_status_map and fid in decay_status_map:
                self._decay[fid] = decay_status_map[fid]
        logger.info(
            "[LiveMonitor] ingest_live_ic: factors=%d records=%d overall_ic=%.4f",
            len(factors),
            int((live_ic_result or {}).get("n_records", 0)),
            float((live_ic_result or {}).get("overall_ic", 0.0)),
        )
        alerts = self.check_deviation()
        alerts.extend(self._check_decay_alerts())
        self._alerts = alerts
        return alerts

    # ─── 偏离检查 ────────────────────────────────────────

    def check_deviation(self) -> list[dict[str, Any]]:
        """检查所有因子 Live vs 回测偏离，返回告警列表。"""
        alerts: list[dict[str, Any]] = []
        for factor_id in self._live:
            report = self._get_deviation_report(factor_id)
            for dev in report.get("deviations", []):
                if dev.get("severity") in ("warning", "critical"):
                    alerts.append(dev)
                    logger.info(
                        "METRIC live_factor_ic{factor_id=%r} %s",
                        factor_id,
                        dev.get("live_value", 0.0),
                    )
        self._alerts = alerts
        return alerts

    # ─── 衰减告警（GAP-I402） ────────────────────────────

    def set_decay_status(self, factor_id: str, status: str) -> None:
        """手动设置因子衰减状态（ok/weak/decayed）。"""
        self._decay[factor_id] = status

    def get_decay_status(self, factor_id: str) -> Optional[str]:
        """查询因子衰减状态。"""
        return self._decay.get(factor_id)

    def _check_decay_alerts(self) -> list[dict[str, Any]]:
        """衰减状态驱动告警：decayed → critical（建议退役），weak → warning（持续观察）。"""
        if not self._decay_alert_enabled:
            return []
        alerts: list[dict[str, Any]] = []
        for factor_id, status in self._decay.items():
            if status not in ("decayed", "weak"):
                continue
            severity = "critical" if status == "decayed" else "warning"
            alerts.append(
                {
                    "alert_id": str(uuid.uuid4()),
                    "factor_id": factor_id,
                    "metric": "decay",
                    "decay_status": status,
                    "severity": severity,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "recommendation": (
                        "衰减退役建议（GAP-I305 闭环）：实盘 IC 显著低于回测或符号反转"
                        if status == "decayed"
                        else "建议持续观察实盘表现"
                    ),
                }
            )
            logger.info(
                "METRIC live_factor_decay{factor_id=%r,status=%r} 1",
                factor_id,
                status,
            )
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
            severity = (
                "critical"
                if dev_pct > self._threshold * 1.5
                else ("warning" if dev_pct > self._threshold else "normal")
            )
            if severity != "normal":
                worst = "critical" if severity == "critical" else "warning"
            deviations.append(
                {
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
                        "建议重新评估因子有效性"
                        if severity == "critical"
                        else "建议持续观察"
                        if severity == "warning"
                        else "无需操作"
                    ),
                }
            )

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
