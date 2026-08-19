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

import math
import threading


def _entropy_norm_hhi(probs: dict[str, float] | None) -> tuple[float, float]:
    """计算制度后验概率的归一化熵与 HHI 集中度（28-T10）。

    Args:
        probs: 制度后验概率分布；None / 空 / 全零视为确定性分布。

    Returns:
        (entropy_norm, blend_hhi)：归一化熵 ∈ [0, 1]（0 完全确定），
        HHI = Σ p_i² ∈ [0, 1]（1 完全集中）。
    """
    if not probs:
        return 0.0, 1.0
    ps = [max(0.0, float(p)) for p in probs.values()]
    total = sum(ps)
    if total <= 0.0:
        return 0.0, 1.0
    ps = [p / total for p in ps]
    hhi = sum(p * p for p in ps)
    nonzero = [p for p in ps if p > 0.0]
    if len(nonzero) <= 1:
        return 0.0, hhi
    h = -sum(p * math.log(p) for p in nonzero)
    entropy_norm = h / math.log(len(nonzero))
    return max(0.0, min(1.0, entropy_norm)), hhi


class MetricsRegistry:
    """进程内 Prometheus 指标注册表（线程安全）。

    以文本格式渲染指标行，供 ``http_server`` 的 ``/metrics`` 端点拼接输出。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 衰减计数
        self._decay_counts: dict[str, int] = {
            "active": 0,
            "decaying": 0,
            "critical_decay": 0,
            "deprecated": 0,
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
        # C.3 反馈指标: event_type -> 触发次数
        self._feedback_triggers: dict[str, int] = {}
        # C.3 反馈处理: (action, success) -> 次数
        self._feedback_processing: dict[tuple[str, str], int] = {}
        # C.3 待处理事件数: event_type -> 数量
        self._feedback_pending: dict[str, int] = {}
        # C.3 效果指标
        self._attribution_accuracy: float = 0.0
        self._recommendations_accepted: float = 0.0
        self._new_factors: int = 0
        self._effective_rate: float = 0.0
        # 28-T10 Regime 观测指标: market -> {confidence, entropy_norm, exposure_scale, blend_hhi}
        self._regime_metrics: dict[str, dict[str, float]] = {}
        # 28-T10 当前制度名: market -> regime
        self._regime_by_market: dict[str, str] = {}

    # ─── 衰减追踪指标 (A.2) ────────────────────────────────

    def update_decay_counts(
        self,
        *,
        active: int | None = None,
        decaying: int | None = None,
        critical: int | None = None,
        deprecated: int | None = None,
    ) -> None:
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
            self._rebalance_total[regime or "unknown"] = self._rebalance_total.get(regime or "unknown", 0) + 1

    def record_regime_metrics(
        self,
        market: str,
        regime: str,
        confidence: float,
        probs: dict[str, float] | None = None,
        exposure_scale: float = 1.0,
        beta_scale: float = 1.0,
        beta_state: str = "",
        crowding_scale: float = 1.0,
        crowding_state: str = "",
    ) -> None:
        """记录某市场当前 regime 观测指标（28-T10 + plans/55 §E + plans/56 §D）。

        置信度、归一化熵、exposure_scale、beta_scale/beta_state、crowding_scale/
        crowding_state 与 blend HHI 全部落盘供 /metrics 审计；无 probs（硬查表回退
        场景）时熵为 0.0、HHI 为 1.0。

        Args:
            market: 市场标识（futures/stock/...）。
            regime: 当前制度名称（bear/bull/...）。
            confidence: 制度置信度 ∈ [0, 1]（越界钳制）。
            probs: 制度后验概率分布；可无。
            exposure_scale: 置信度仓位缩放因子。
            beta_scale: L0 宏观 Beta 层总敞口倍率（plans/55 §C，默认 1.0=未启用）。
            beta_state: L0 宏观 Beta 状态（RISK_ON/RISK_OFF/RANGE_BOUND/unknown）。
            crowding_scale: 拥挤×置信度联合门控倍率（plans/56 §B，默认 1.0=未启用）。
            crowding_state: 拥挤度状态（long/short/neutral 或空）。
        """
        entropy_norm, blend_hhi = _entropy_norm_hhi(probs)
        with self._lock:
            self._regime_by_market[market or "unknown"] = regime or ""
            self._regime_metrics[market or "unknown"] = {
                "confidence": max(0.0, min(1.0, float(confidence))),
                "entropy_norm": entropy_norm,
                "exposure_scale": float(exposure_scale),
                "blend_hhi": blend_hhi,
                "beta_scale": float(beta_scale),
                "beta_state": beta_state or "",
                "crowding_scale": float(crowding_scale),
                "crowding_state": crowding_state or "",
            }

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
            self._live_deviation_alerts[key] = self._live_deviation_alerts.get(key, 0) + 1

    # ─── 风控指标 (C.2) ─────────────────────────────────

    def record_risk_check(self, check_name: str, result: str) -> None:
        """记录一次风控检查（result: passed/blocked）。"""
        with self._lock:
            self._risk_check_total[(check_name, result)] = self._risk_check_total.get((check_name, result), 0) + 1
            if result == "blocked":
                self._risk_check_blocked[check_name] = self._risk_check_blocked.get(check_name, 0) + 1

    # ─── 反馈闭环指标 (C.3) ─────────────────────────────

    def record_feedback_trigger(self, event_type: str) -> None:
        """记录一次反馈触发。"""
        with self._lock:
            self._feedback_triggers[event_type or "unknown"] = (
                self._feedback_triggers.get(event_type or "unknown", 0) + 1
            )

    def update_feedback_pending(self, pending: dict[str, int]) -> None:
        """更新待处理事件数。"""
        with self._lock:
            self._feedback_pending = {k: max(0, int(v)) for k, v in (pending or {}).items()}

    def record_feedback_processing(self, action: str, success: bool) -> None:
        """记录一次反馈处理。"""
        with self._lock:
            key = (action or "unknown", "ok" if success else "fail")
            self._feedback_processing[key] = self._feedback_processing.get(key, 0) + 1

    def update_effectiveness(
        self,
        *,
        attribution_accuracy: float | None = None,
        recommendations_accepted: float | None = None,
        new_factors: int | None = None,
        effective_rate: float | None = None,
    ) -> None:
        """更新迭代效果指标。"""
        with self._lock:
            if attribution_accuracy is not None:
                self._attribution_accuracy = float(attribution_accuracy)
            if recommendations_accepted is not None:
                self._recommendations_accepted = float(recommendations_accepted)
            if new_factors is not None:
                self._new_factors = int(new_factors)
            if effective_rate is not None:
                self._effective_rate = float(effective_rate)

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
            lines.append(f'fts_factor_decay_evaluations_total{{status_before="{before}",status_after="{after}"}} {n}')
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

        # ── Regime 观测指标 (28-T10) ──
        regime_metrics = dict(self._regime_metrics)
        regime_by_market = dict(self._regime_by_market)

        lines.append("# HELP fts_regime_confidence 当前市场制度置信度")
        lines.append("# TYPE fts_regime_confidence gauge")
        for m, vals in sorted(regime_metrics.items()):
            lines.append(f'fts_regime_confidence{{market="{m}"}} {vals["confidence"]}')
        lines.append("")

        lines.append("# HELP fts_regime_entropy_norm 制度后验归一化熵(0~1, 越高越不确定)")
        lines.append("# TYPE fts_regime_entropy_norm gauge")
        for m, vals in sorted(regime_metrics.items()):
            lines.append(f'fts_regime_entropy_norm{{market="{m}"}} {vals["entropy_norm"]}')
        lines.append("")

        lines.append("# HELP fts_regime_exposure_scale 置信度仓位缩放因子")
        lines.append("# TYPE fts_regime_exposure_scale gauge")
        for m, vals in sorted(regime_metrics.items()):
            lines.append(f'fts_regime_exposure_scale{{market="{m}"}} {vals["exposure_scale"]}')
        lines.append("")

        lines.append("# HELP fts_regime_blend_hhi 制度概率分布集中度(HHI)")
        lines.append("# TYPE fts_regime_blend_hhi gauge")
        for m, vals in sorted(regime_metrics.items()):
            lines.append(f'fts_regime_blend_hhi{{market="{m}"}} {vals["blend_hhi"]}')
        lines.append("")

        lines.append("# HELP fts_regime_name 当前市场制度名称 (1=当前生效)")
        lines.append("# TYPE fts_regime_name gauge")
        for m, r in sorted(regime_by_market.items()):
            if r:
                lines.append(f'fts_regime_name{{market="{m}",regime="{r}"}} 1')
        lines.append("")

        # ── Live 因子指标 (C.2) ──
        live_values = dict(self._live_factor_values)
        lines.append("# HELP fts_live_factor_ic Live 因子 IC 值")
        lines.append("# TYPE fts_live_factor_ic gauge")
        for fid, lv in sorted(live_values.items()):
            if "ic" in lv:
                lines.append(f'fts_live_factor_ic{{factor_id="{fid}"}} {lv["ic"]}')
        lines.append("")

        lines.append("# HELP fts_live_factor_sharpe Live 因子 Sharpe 值")
        lines.append("# TYPE fts_live_factor_sharpe gauge")
        for fid, lv in sorted(live_values.items()):
            if "sharpe" in lv:
                lines.append(f'fts_live_factor_sharpe{{factor_id="{fid}"}} {lv["sharpe"]}')
        lines.append("")

        alerts = dict(self._live_deviation_alerts)
        lines.append("# HELP fts_live_factor_deviation_alerts_total Live 偏离告警次数")
        lines.append("# TYPE fts_live_factor_deviation_alerts_total counter")
        if alerts:
            for (fid, sev), n in sorted(alerts.items()):
                lines.append(f'fts_live_factor_deviation_alerts_total{{factor_id="{fid}",severity="{sev}"}} {n}')
        else:
            lines.append("fts_live_factor_deviation_alerts_total 0")
        lines.append("")

        # ── 风控指标 (C.2) ──
        risk_total = dict(self._risk_check_total)
        lines.append("# HELP fts_risk_check_total 风控检查次数")
        lines.append("# TYPE fts_risk_check_total counter")
        if risk_total:
            for (check, result), n in sorted(risk_total.items()):
                lines.append(f'fts_risk_check_total{{check_name="{check}",result="{result}"}} {n}')
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

        # ── 反馈闭环指标 (C.3) ──
        triggers = dict(self._feedback_triggers)
        lines.append("# HELP fts_feedback_triggers_total 反馈触发次数")
        lines.append("# TYPE fts_feedback_triggers_total counter")
        if triggers:
            for etype, n in sorted(triggers.items()):
                lines.append(f'fts_feedback_triggers_total{{event_type="{etype}"}} {n}')
        else:
            lines.append("fts_feedback_triggers_total 0")
        lines.append("")

        pending = dict(self._feedback_pending)
        lines.append("# HELP fts_feedback_events_pending 待处理反馈事件数")
        lines.append("# TYPE fts_feedback_events_pending gauge")
        if pending:
            for etype, n in sorted(pending.items()):
                lines.append(f'fts_feedback_events_pending{{event_type="{etype}"}} {n}')
        else:
            lines.append("fts_feedback_events_pending 0")
        lines.append("")

        processing = dict(self._feedback_processing)
        lines.append("# HELP fts_feedback_processing_total 反馈处理次数")
        lines.append("# TYPE fts_feedback_processing_total counter")
        if processing:
            for (action, success), n in sorted(processing.items()):
                lines.append(f'fts_feedback_processing_total{{action_taken="{action}",success="{success}"}} {n}')
        else:
            lines.append("fts_feedback_processing_total 0")
        lines.append("")

        lines.append("# HELP fts_feedback_attribution_accuracy 归因准确率")
        lines.append("# TYPE fts_feedback_attribution_accuracy gauge")
        lines.append(f"fts_feedback_attribution_accuracy {self._attribution_accuracy}")
        lines.append("")

        lines.append("# HELP fts_feedback_recommendations_accepted 建议采纳率")
        lines.append("# TYPE fts_feedback_recommendations_accepted gauge")
        lines.append(f"fts_feedback_recommendations_accepted {self._recommendations_accepted}")
        lines.append("")

        lines.append("# HELP fts_evolution_new_factors 新因子数")
        lines.append("# TYPE fts_evolution_new_factors counter")
        lines.append(f"fts_evolution_new_factors {self._new_factors}")
        lines.append("")

        lines.append("# HELP fts_evolution_effective_rate 因子有效率")
        lines.append("# TYPE fts_evolution_effective_rate gauge")
        lines.append(f"fts_evolution_effective_rate {self._effective_rate}")
        lines.append("")
        return lines


# ─── 全局单例（供 http_server 引用） ──────────────────────

metrics_registry = MetricsRegistry()


__all__ = ["MetricsRegistry", "metrics_registry"]
