"""
fts.monitor.prometheus_metrics — 因子生命周期与 Regime 权重指标注册表

为 A.2 衰减追踪 / A.3 自适应权重提供 Prometheus 指标：
    - fts_factor_decay_active_count       Gauge   活跃因子数
    - fts_factor_decay_decaying_count     Gauge   衰减中因子数
    - fts_factor_decay_critical_count     Gauge   严重衰减因子数
    - fts_factor_decay_deprecated_count   Gauge   已淘汰因子数
    - fts_factor_decay_evaluations_total  Counter 状态变更次数 (标签 status_before/status_after)
    - fts_regime_current                  Gauge   当前市场状态 (标签 regime, 1=当前)
    - fts_weight_rebalance_total          Counter 再平衡触发次数 (标签 regime)

用法:
    from fts.monitor.prometheus_metrics import metrics_registry

    metrics_registry.update_decay_counts(active=12, decaying=2, critical=1, deprecated=3)
    metrics_registry.record_decay_evaluation("active", "decaying")
    metrics_registry.set_regime("bull")
    metrics_registry.record_rebalance("bull")

    lines = metrics_registry.render()   # 供 HTTP /metrics 输出

HARNESS §可观测性: 指标全部挂载到 GET /metrics 端点。
HARNESS §trace_id: 本模块无 trace_id，仅聚合计数。

版本: v0.1.0
"""

from __future__ import annotations

import threading
from typing import Any


class MetricsRegistry:
    """进程内 Prometheus 指标注册表（线程安全）。

    以文本格式渲染指标行，供 ``http_server`` 的 ``/metrics`` 端点拼接输出。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 衰减计数
        self._decay_counts: dict[str, int] = {
            "active": 0, "decaying": 0, "critical_decay": 0, "deprecated": 0,
        }
        # 状态变更计数器: (status_before, status_after) -> 次数
        self._decay_evaluations: dict[tuple[str, str], int] = {}
        # 当前 Regime
        self._current_regime: str = ""
        # 再平衡计数: regime -> 次数
        self._rebalance_total: dict[str, int] = {}
        # C.2 Live 因子表现: factor_id -> {metric: value}
        self._live_factor_values: dict[str, dict[str, float]] = {}
        # C.2 Live 偏离告警计数: (factor_id, severity) -> 次数
        self._live_deviation_alerts: dict[tuple[str, str], int] = {}
        # C.2 风控检查计数: (check_name, result) -> 次数
        self._risk_check_total: dict[tuple[str, str], int] = {}
        # C.2 风控拦截计数: check_name -> 次数
        self._risk_check_blocked: dict[str, int] = {}

    # ─── 衰减追踪指标 (A.2) ────────────────────────────────

    def update_decay_counts(self, *, active: int | None = None,
                            decaying: int | None = None,
                            critical: int | None = None,
                            deprecated: int | None = None) -> None:
        """更新各状态因子计数（None 表示不修改）。"""
        with self._lock:
            if active is not None:
                self._decay_counts["active"] = max(0, int(active))
            if decaying is not None:
                self._decay_counts["decaying"] = max(0, int(decaying))
            if critical is not None:
                self._decay_counts["critical_decay"] = max(0, int(critical))
            if deprecated is not None:
                self._decay_counts["deprecated"] = max(0, int(deprecated))

    def record_decay_evaluation(self, status_before: str, status_after: str) -> None:
        """记录一次状态变更。"""
        with self._lock:
            key = (status_before, status_after)
            self._decay_evaluations[key] = self._decay_evaluations.get(key, 0) + 1

    def get_decay_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._decay_counts)

    # ─── Regime / 权重指标 (A.3) ───────────────────────────

    def set_regime(self, regime: str) -> None:
        """设置当前市场状态。"""
        with self._lock:
            self._current_regime = regime or ""

    def get_regime(self) -> str:
        with self._lock:
            return self._current_regime

    def record_rebalance(self, regime: str) -> None:
        """记录一次权重再平衡。"""
        with self._lock:
            self._rebalance_total[regime or "unknown"] = (
                self._rebalance_total.get(regime or "unknown", 0) + 1
            )

    # ─── Live 因子指标 (C.2) ─────────────────────────────

    def update_live_factor(self, factor_id: str, metrics: dict[str, float]) -> None:
        """更新因子 Live 表现指标（ic/sharpe/max_drawdown）。"""
        with self._lock:
            cleaned = {k: float(v) for k, v in metrics.items() if v is not None}
            if cleaned:
                self._live_factor_values[factor_id] = cleaned

    def record_live_deviation_alert(self, factor_id: str, severity: str) -> None:
        """记录一次 Live 偏离告警。"""
        with self._lock:
            key = (factor_id, severity)
            self._live_deviation_alerts[key] = (
                self._live_deviation_alerts.get(key, 0) + 1
            )

    # ─── 风控指标 (C.2) ─────────────────────────────────

    def record_risk_check(self, check_name: str, result: str) -> None:
        """记录一次风控检查（result: passed/blocked）。"""
        with self._lock:
            self._risk_check_total[(check_name, result)] = (
                self._risk_check_total.get((check_name, result), 0) + 1
            )
            if result == "blocked":
                self._risk_check_blocked[check_name] = (
                    self._risk_check_blocked.get(check_name, 0) + 1
                )

    # ─── 渲染 ─────────────────────────────────────────────

    def render(self) -> list[str]:
        """渲染 Prometheus 文本行（含 HELP/TYPE 注释）。"""
        with self._lock:
            counts = dict(self._decay_counts)
            evaluations = dict(self._decay_evaluations)
            regime = self._current_regime
            rebalances = dict(self._rebalance_total)

        lines: list[str] = []
        for metric, key in (
            ("fts_factor_decay_active_count", "active"),
            ("fts_factor_decay_decaying_count", "decaying"),
            ("fts_factor_decay_critical_count", "critical_decay"),
            ("fts_factor_decay_deprecated_count", "deprecated"),
        ):
            lines.append(f"# HELP {metric} 因子生命周期状态计数")
            lines.append(f"# TYPE {metric} gauge")
            lines.append(f"{metric} {counts.get(key, 0)}")
            lines.append("")

        lines.append("# HELP fts_factor_decay_evaluations_total 因子状态变更次数")
        lines.append("# TYPE fts_factor_decay_evaluations_total counter")
        for (before, after), n in sorted(evaluations.items()):
            lines.append(
                f'fts_factor_decay_evaluations_total{{status_before="{before}",'
                f'status_after="{after}"}} {n}'
            )
        if not evaluations:
            lines.append("fts_factor_decay_evaluations_total 0")
        lines.append("")

        lines.append("# HELP fts_regime_current 当前市场状态 (1=当前生效)")
        lines.append("# TYPE fts_regime_current gauge")
        if regime:
            lines.append(f'fts_regime_current{{regime="{regime}"}} 1')
        lines.append("")

        lines.append("# HELP fts_weight_rebalance_total 权重再平衡触发次数")
        lines.append("# TYPE fts_weight_rebalance_total counter")
        if rebalances:
            for r, n in sorted(rebalances.items()):
                lines.append(f'fts_weight_rebalance_total{{regime="{r}"}} {n}')
        else:
            lines.append("fts_weight_rebalance_total 0")
        lines.append("")

        # ── Live 因子指标 (C.2) ──
        live_values = dict(self._live_factor_values)
        lines.append("# HELP fts_live_factor_ic Live 因子 IC 值")
        lines.append("# TYPE fts_live_factor_ic gauge")
        for fid, m in sorted(live_values.items()):
            if "ic" in m:
                lines.append(f'fts_live_factor_ic{{factor_id="{fid}"}} {m["ic"]}')
        lines.append("")

        lines.append("# HELP fts_live_factor_sharpe Live 因子 Sharpe 值")
        lines.append("# TYPE fts_live_factor_sharpe gauge")
        for fid, m in sorted(live_values.items()):
            if "sharpe" in m:
                lines.append(f'fts_live_factor_sharpe{{factor_id="{fid}"}} {m["sharpe"]}')
        lines.append("")

        alerts = dict(self._live_deviation_alerts)
        lines.append("# HELP fts_live_factor_deviation_alerts_total Live 偏离告警次数")
        lines.append("# TYPE fts_live_factor_deviation_alerts_total counter")
        if alerts:
            for (fid, sev), n in sorted(alerts.items()):
                lines.append(
                    f'fts_live_factor_deviation_alerts_total{{factor_id="{fid}",'
                    f'severity="{sev}"}} {n}'
                )
        else:
            lines.append("fts_live_factor_deviation_alerts_total 0")
        lines.append("")

        # ── 风控指标 (C.2) ──
        risk_total = dict(self._risk_check_total)
        lines.append("# HELP fts_risk_check_total 风控检查次数")
        lines.append("# TYPE fts_risk_check_total counter")
        if risk_total:
            for (check, result), n in sorted(risk_total.items()):
                lines.append(
                    f'fts_risk_check_total{{check_name="{check}",result="{result}"}} {n}'
                )
        else:
            lines.append("fts_risk_check_total 0")
        lines.append("")

        risk_blocked = dict(self._risk_check_blocked)
        lines.append("# HELP fts_risk_check_blocked_total 风控拦截次数")
        lines.append("# TYPE fts_risk_check_blocked_total counter")
        if risk_blocked:
            for check, n in sorted(risk_blocked.items()):
                lines.append(f'fts_risk_check_blocked_total{{check_name="{check}"}} {n}')
        else:
            lines.append("fts_risk_check_blocked_total 0")
        lines.append("")
        return lines


# ─── 全局单例（供 http_server 引用） ──────────────────────

metrics_registry = MetricsRegistry()


__all__ = ["MetricsRegistry", "metrics_registry"]
