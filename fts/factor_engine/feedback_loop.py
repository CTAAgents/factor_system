"""
fts.factor_engine.feedback_loop — 系统化反馈闭环（C.3）。

实现 "因子表现→归因→演化方向调整" 的完整闭环：
    - ``FeedbackTrigger``: 检查 Live 偏离/数据异常/定期评估触发条件
    - ``AttributionAnalyzer``: 归因分析（5 种根因判定）
    - ``EvolutionDirectionAdjuster``: 根据归因调整演化配置
    - ``EvolutionEffectiveness``: 月度迭代效果评估
    - ``FeedbackLoop``: 闭环主类（处理事件/手动触发/月度报告）

经验沉淀复用 ``experience_chain.py`` 作为归因输入之一。

用法:
    from fts.factor_engine.feedback_loop import FeedbackLoop

    loop = FeedbackLoop()
    results = loop.process_feedback()
    loop.trigger_manual_feedback(factor_id="fut_abc", reason="manual review")

版本: v1.0.0
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, TypedDict, cast

import numpy as np

logger = logging.getLogger(__name__)


class FeedbackEventType(str, Enum):
    """反馈事件类型。"""

    LIVE_DEVIATION = "live_deviation"
    DATA_ANOMALY = "data_anomaly"
    MARKET_EVENT = "market_event"
    PERIODIC_EVAL = "periodic_eval"
    AUDIT_FAILURE = "audit_failure"
    FACTOR_DECAY = "factor_decay"
    USER_TRIGGERED = "user_triggered"


class RootCause(str, Enum):
    """根本原因枚举。"""

    FACTOR_DECAY = "factor_decay"
    REGIME_MISMATCH = "regime_mismatch"
    DATA_QUALITY = "data_quality"
    IMPLEMENTATION_BUG = "implementation_bug"
    NORMAL_FLUCTUATION = "normal_fluctuation"
    UNKNOWN = "unknown"


def _now_iso() -> str:
    """当前 UTC 时间 ISO 格式。"""
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    """生成短 UUID。"""
    return f"{prefix}{uuid.uuid4().hex[:16]}"


# ─── 反馈触发器 ──────────────────────────────────────────


class FeedbackTrigger:
    """反馈触发器（C.3 §2）。

    检查 Live 偏离 / 数据异常 / 定期评估三类触发条件。
    """

    def __init__(
        self,
        live_monitor: Any | None = None,
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        """初始化触发器。

        Args:
            live_monitor: LiveFactorMonitor 实例（Live 偏离触发用）
            config: FeedbackTriggerConfig 字典
        """
        self._live_monitor = live_monitor
        self._config = dict(config or {})
        self._cooldown: dict[str, float] = {}  # event_type -> 上次触发时间戳
        self._triggers_today: int = 0
        self._today: str = ""
        self._last_periodic_day: str = ""  # 定期评估上次触发日期（每日一次）

    def check_triggers(self) -> list[dict[str, Any]]:
        """检查所有触发条件，返回需要处理的事件列表。"""
        events: list[dict[str, Any]] = []
        events.extend(self._check_live_deviation())
        events.extend(self._check_periodic_eval())
        return events

    # ─── 触发子项 ────────────────────────────────────────

    def _check_live_deviation(self) -> list[dict[str, Any]]:
        """Live 偏离触发（critical 级）。"""
        if self._live_monitor is None:
            return []
        try:
            alerts = self._live_monitor.check_deviation()
        except Exception as e:  # noqa: BLE001
            logger.warning("[FeedbackTrigger] Live 检查失败: %s", e)
            return []

        events: list[dict[str, Any]] = []
        for alert in alerts:
            if alert.get("severity") != "critical":
                continue
            if not self._pass_cooldown("live_deviation"):
                continue
            events.append(
                {
                    "event_id": _new_id("fe_"),
                    "event_type": FeedbackEventType.LIVE_DEVIATION.value,
                    "factor_id": alert.get("factor_id"),
                    "trigger_reason": (f"{alert.get('metric')} 偏离 {alert.get('deviation_pct', 0):.1%}"),
                    "severity": "critical",
                    "payload": alert,
                    "timestamp": _now_iso(),
                    "handled": False,
                    "handled_at": None,
                }
            )
        return events

    def _check_periodic_eval(self) -> list[dict[str, Any]]:
        """定期评估触发（每 24 小时一次）。"""
        now = datetime.now(timezone.utc)
        day_key = now.strftime("%Y-%m-%d")
        # 每日仅触发一次（以日期为 key）
        if self._last_periodic_day == day_key:
            return []
        self._last_periodic_day = day_key
        return [
            {
                "event_id": _new_id("fe_"),
                "event_type": FeedbackEventType.PERIODIC_EVAL.value,
                "factor_id": None,
                "trigger_reason": "定期因子评估",
                "severity": "info",
                "payload": {"day": day_key},
                "timestamp": _now_iso(),
                "handled": False,
                "handled_at": None,
            }
        ]

    # ─── 冷却保护 ────────────────────────────────────────

    def _pass_cooldown(self, event_type: str, cooldown_hours: float = 24.0) -> bool:
        """同类型事件冷却期检查（默认 24h）。"""
        last = self._cooldown.get(event_type, 0.0)
        now = datetime.now(timezone.utc).timestamp()
        if now - last < cooldown_hours * 3600:
            return False
        self._cooldown[event_type] = now
        return True


# ─── 归因分析器 ──────────────────────────────────────────


class AttributionAnalyzer:
    """归因分析器（C.3 §3）。

    根据事件与因子信息判定根本原因（5 种根因）。
    """

    def analyze(
        self,
        event: dict[str, Any],
        factor: Optional[dict[str, Any]] = None,
        market_data: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """执行归因分析。

        Args:
            event: FeedbackEvent 字典
            factor: 因子信息（含 decay_6m/grade/style 等）
            market_data: 市场快照（含 regime 等）

        Returns:
            AttributionReport 字典。
        """
        root_cause, confidence = self._determine_root_cause(event, factor or {}, market_data or {})
        recommendation = self._recommend_action(root_cause, factor or {}, event)
        return {
            "report_id": _new_id("ar_"),
            "event_id": event.get("event_id", ""),
            "root_cause": root_cause,
            "confidence": round(confidence, 3),
            "analyses": {
                "factor_decay": self._analyze_factor_decay(factor or {}),
                "regime_change": self._analyze_regime_change(event, market_data or {}),
                "data_quality": self._analyze_data_quality(event),
                "implementation": {"likely": False, "confidence": 0.1, "evidence": []},
            },
            "recommendation": recommendation,
            "timestamp": _now_iso(),
        }

    # ─── 内部分析 ────────────────────────────────────────

    @staticmethod
    def _analyze_factor_decay(factor: dict[str, Any]) -> dict[str, Any]:
        """因子衰减分析。"""
        decay_6m = float(factor.get("decay_6m", 0.0) or 0.0)
        likely = decay_6m > 0.30
        return {
            "likely": likely,
            "confidence": min(0.9, decay_6m * 2),
            "evidence": [f"decay_6m={decay_6m:.2f}"] if likely else [],
        }

    @staticmethod
    def _analyze_regime_change(event: dict[str, Any], market_data: dict[str, Any]) -> dict[str, Any]:
        """市场状态切换分析。"""
        regime = market_data.get("regime", "")
        event_type = event.get("event_type", "")
        likely = bool(regime) and event_type != FeedbackEventType.DATA_ANOMALY.value
        return {
            "likely": likely,
            "confidence": 0.6 if likely else 0.2,
            "evidence": [f"regime={regime}"] if regime else [],
        }

    @staticmethod
    def _analyze_data_quality(event: dict[str, Any]) -> dict[str, Any]:
        """数据质量分析。"""
        likely = event.get("event_type") == FeedbackEventType.DATA_ANOMALY.value
        return {
            "likely": likely,
            "confidence": 0.8 if likely else 0.1,
            "evidence": [event.get("trigger_reason", "")] if likely else [],
        }

    def _determine_root_cause(
        self,
        event: dict[str, Any],
        factor: dict[str, Any],
        market_data: dict[str, Any],
    ) -> tuple[str, float]:
        """综合判定根本原因。"""
        event_type = event.get("event_type", "")

        if event_type == FeedbackEventType.DATA_ANOMALY.value:
            return RootCause.DATA_QUALITY.value, 0.85
        if event_type == FeedbackEventType.PERIODIC_EVAL.value:
            return RootCause.NORMAL_FLUCTUATION.value, 0.7
        if event_type in (
            FeedbackEventType.AUDIT_FAILURE.value,
            FeedbackEventType.USER_TRIGGERED.value,
        ):
            return RootCause.IMPLEMENTATION_BUG.value, 0.8

        # Live 偏离：按因子衰减 vs Regime 不匹配 vs 正常波动
        decay_6m = float(factor.get("decay_6m", 0.0) or 0.0)
        if decay_6m > 0.30:
            return RootCause.FACTOR_DECAY.value, 0.8
        if market_data.get("regime"):
            return RootCause.REGIME_MISMATCH.value, 0.6
        return RootCause.NORMAL_FLUCTUATION.value, 0.5

    @staticmethod
    def _recommend_action(root_cause: str, factor: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
        """根据根因生成操作建议。"""
        if root_cause == RootCause.FACTOR_DECAY.value:
            return {
                "action": "retire_factor",
                "description": f"因子 {factor.get('factor_id', '?')} 衰减严重，建议淘汰",
                "priority": "high",
                "suggested_params": {"factor_id": factor.get("factor_id")},
            }
        if root_cause == RootCause.REGIME_MISMATCH.value:
            return {
                "action": "reweight_factor",
                "description": "市场状态与因子风格不匹配，建议调整权重",
                "priority": "medium",
                "suggested_params": {"factor_id": factor.get("factor_id")},
            }
        if root_cause == RootCause.DATA_QUALITY.value:
            return {
                "action": "fix_data_source",
                "description": "数据质量问题，建议修复数据源",
                "priority": "high",
                "suggested_params": {},
            }
        if root_cause == RootCause.IMPLEMENTATION_BUG.value:
            return {
                "action": "fix_implementation",
                "description": "实现 Bug，建议修复代码",
                "priority": "high",
                "suggested_params": {"event_id": event.get("event_id")},
            }
        return {
            "action": "monitor_only",
            "description": "正常波动，仅监控",
            "priority": "low",
            "suggested_params": {},
        }


# ─── 演化方向调整器 ──────────────────────────────────────


class EvolutionDirectionAdjuster:
    """演化方向调整器（C.3 §4）。

    根据归因报告调整下一轮演化的搜索方向（配置 dict）。
    """

    def __init__(self, max_generation_limit: int = 200) -> None:
        """初始化方向调整器。"""
        self._max_generation_limit = int(max_generation_limit)

    def adjust_direction(
        self,
        attribution: dict[str, Any],
        current_config: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """根据归因结果调整演化配置。

        Args:
            attribution: AttributionReport 字典
            current_config: 当前演化配置（dict，缺省空配置）

        Returns:
            调整后的配置副本。
        """
        config = copy.deepcopy(dict(current_config or {}))
        recommendation = attribution.get("recommendation", {})
        action = recommendation.get("action", "monitor_only")

        if action == "trigger_evolution":
            cur_gen = int(config.get("max_generation", config.get("max_generations", 20)))
            config["max_generations"] = min(int(cur_gen * 1.5), self._max_generation_limit)
            config["inject_experience"] = {
                "type": attribution.get("root_cause", "unknown"),
                "event_id": attribution.get("event_id"),
            }
        elif action == "reweight_factor":
            factor_ids = recommendation.get("suggested_params", {}).get("factor_id")
            if factor_ids:
                config["regime_overrides"] = {"decrease": [factor_ids]}
        elif action == "retire_factor":
            config["retire_candidates"] = attribution.get("event_id", "")

        return config


# ─── 迭代效果评估器 ──────────────────────────────────────


class EvolutionEffectiveness:
    """迭代效果评估器（C.3 §5）。

    汇总月度新因子数/有效率/反馈处理数等指标，生成月度报告。
    """

    def __init__(self, metrics_store: Optional[dict[str, Any]] = None) -> None:
        """初始化评估器。

        Args:
            metrics_store: 指标仓库（缺省使用进程内 dict）
        """
        self._store: dict[str, Any] = dict(metrics_store or {})

    def generate_monthly_report(self, period: Optional[str] = None) -> dict[str, Any]:
        """生成月度迭代效果报告。"""
        if period is None:
            period = datetime.now(timezone.utc).strftime("%Y-%m")

        new_factors = int(self._store.get("new_factors", 0))
        total_generated = int(self._store.get("total_generated", 0))
        effective_rate = new_factors / total_generated if total_generated > 0 else 0.0
        handled = int(self._store.get("feedback_handled", 0))
        recommendations_total = int(self._store.get("recommendations_total", 0))
        accepted = int(self._store.get("recommendations_accepted", 0))

        return {
            "report_id": _new_id("fr_"),
            "period": period,
            "new_factors": new_factors,
            "effective_rate": round(effective_rate, 4),
            "avg_sharpe_improvement": float(self._store.get("avg_sharpe_improvement", 0.0)),
            "decay_rate_reduction": float(self._store.get("decay_rate_reduction", 0.0)),
            "evolution_rounds": int(self._store.get("evolution_rounds", 0)),
            "feedback_events_handled": handled,
            "attribution_accuracy": float(self._store.get("attribution_accuracy", 0.0)),
            "recommendations_accepted": accepted,
            "recommendations_total": recommendations_total,
            "summary_text": (f"{period}: 新因子 {new_factors} 个，有效率 {effective_rate:.1%}，反馈处理 {handled} 件"),
            "timestamp": _now_iso(),
        }


# ─── 反馈闭环主类 ────────────────────────────────────────


class FeedbackLoop:
    """反馈闭环主类（C.3 §6）。

    组合触发器/归因分析器/方向调整器/效果评估器，提供：
        process_feedback() / trigger_manual_feedback() / generate_monthly_report()
    """

    def __init__(
        self,
        live_monitor: Any | None = None,
        config: Optional[dict[str, Any]] = None,
    ) -> None:
        """初始化反馈闭环。

        Args:
            live_monitor: LiveFactorMonitor 实例
            config: FeedbackLoopConfig 字典
        """
        self._config = dict(config or {})
        self._trigger = FeedbackTrigger(
            live_monitor=live_monitor,
            config=self._config.get("trigger"),
        )
        self._analyzer = AttributionAnalyzer()
        self._adjuster = EvolutionDirectionAdjuster()
        self._evaluator = EvolutionEffectiveness(self._config.get("metrics_store"))
        self._processed: set[str] = set()  # 幂等处理集合
        self._store = self._evaluator._store  # noqa: SLF001

    # ─── 主流程 ──────────────────────────────────────────

    def process_feedback(
        self,
        factors: Optional[dict[str, dict[str, Any]]] = None,
        market_data: Optional[dict[str, Any]] = None,
    ) -> list[dict[str, Any]]:
        """检查并处理所有待处理的反馈事件。

        Args:
            factors: factor_id → 因子信息（归因用）
            market_data: 市场快照（归因用）

        Returns:
            FeedbackProcessResult 列表。
        """
        events = self._trigger.check_triggers()
        results: list[dict[str, Any]] = []
        for event in events:
            result = self._handle_event(event, factors or {}, market_data or {})
            results.append(result)
        return results

    def trigger_manual_feedback(self, factor_id: str = "", reason: str = "") -> dict[str, Any]:
        """手动触发反馈事件。"""
        event: dict[str, Any] = {
            "event_id": _new_id("fe_"),
            "event_type": FeedbackEventType.USER_TRIGGERED.value,
            "factor_id": factor_id or None,
            "trigger_reason": reason or "manual trigger",
            "severity": "warning",
            "payload": {},
            "timestamp": _now_iso(),
            "handled": False,
            "handled_at": None,
        }
        self._store["feedback_handled"] = self._store.get("feedback_handled", 0) + 1
        return event

    def generate_monthly_report(self, period: Optional[str] = None) -> dict[str, Any]:
        """生成月度迭代效果报告。"""
        report = self._evaluator.generate_monthly_report(period)
        self._store["last_report"] = report
        return report

    def get_statistics(self) -> dict[str, Any]:
        """返回闭环统计（供 CLI/指标使用）。"""
        return {
            "events_handled": int(self._store.get("feedback_handled", 0)),
            "processed_ids": len(self._processed),
            "last_report": self._store.get("last_report"),
        }

    # ─── 内部处理 ────────────────────────────────────────

    def _handle_event(
        self,
        event: dict[str, Any],
        factors: dict[str, dict[str, Any]],
        market_data: dict[str, Any],
    ) -> dict[str, Any]:
        """处理单个反馈事件（幂等）。"""
        event_id = event.get("event_id", "")
        if event_id in self._processed:
            return {
                "event_id": event_id,
                "root_cause": "skipped",
                "action_taken": "none",
                "success": False,
                "reason": "already processed",
            }
        self._processed.add(event_id)

        factor = factors.get(event.get("factor_id", ""), {})
        attribution = self._analyzer.analyze(event, factor, market_data)
        new_config = self._adjuster.adjust_direction(attribution, self._config.get("evolution_config"))

        # 记录处理结果
        self._store["feedback_handled"] = self._store.get("feedback_handled", 0) + 1
        self._store["recommendations_total"] = self._store.get("recommendations_total", 0) + 1
        action = attribution.get("recommendation", {}).get("action", "monitor_only")
        if action != "monitor_only":
            self._store["recommendations_accepted"] = self._store.get("recommendations_accepted", 0) + 1

        logger.info(
            "[FeedbackLoop] 处理事件 %s [root_cause=%s, action=%s]",
            event_id,
            attribution.get("root_cause"),
            action,
        )
        return {
            "event_id": event_id,
            "root_cause": attribution.get("root_cause"),
            "action_taken": action,
            "success": True,
            "adjusted_config": new_config,
        }


# ─── GAP-L402 实盘反馈闭环 ────────────────────────────────
#
# 承接总纲 GAP-I401（P0）：实盘表现回流 → 实盘 IC vs 回测 IC 对比 → 衰减修正。
# 角色边界：FTS 只产信号、接收下游回传的成交/净值数据并分析，不做交易决策。


class LiveFeedbackRecord(TypedDict, total=False):
    """实盘反馈记录契约（GAP-L402）。

    由下游 FDT 或人工导入，一条记录 = 单因子单信号日的实盘表现。

    Attributes:
        factor_id: 因子 ID
        signal_date: 信号日期（YYYY-MM-DD）
        signal_value: 信号值（-1 ~ +1，方向）
        position_return: 持仓收益（该信号日的实际收益率，小数）
        turnover: 换手率（0~1）
        slippage: 实际滑点（基点，可选）
        market: 市场（"futures"/"stock"/"etf"）
        backtest_ic: 回测 IC（用于对比，可选）
        weight: 组合权重（可选）
    """

    factor_id: str
    signal_date: str
    signal_value: float
    position_return: float
    turnover: float
    slippage: Optional[float]
    market: Optional[str]
    backtest_ic: Optional[float]
    weight: Optional[float]


def validate_live_feedback_record(
    record: dict[str, Any],
) -> tuple[bool, str]:
    """校验单条实盘反馈记录契约（GAP-L402）。

    Args:
        record: 待校验记录

    Returns:
        (是否有效, 错误信息)；有效时错误信息为空串。
    """
    if not isinstance(record, dict):
        return False, "记录必须为 dict"
    factor_id = record.get("factor_id")
    if not factor_id or not isinstance(factor_id, str):
        return False, "factor_id 缺失或非字符串"
    signal_date = record.get("signal_date")
    if not signal_date or not isinstance(signal_date, str):
        return False, "signal_date 缺失或非字符串"
    for key, label in (("signal_value", "信号值"), ("position_return", "持仓收益"), ("turnover", "换手率")):
        val = record.get(key)
        if val is None:
            continue
        try:
            float(val)
        except (TypeError, ValueError):
            return False, f"{label} ({key}) 非数值"
    return True, ""


class LiveFeedbackImporter:
    """实盘反馈数据导入器（GAP-L402）。

    负责：
        - 批量导入实盘反馈记录（CSV / dict 列表）
        - 落盘到 DuckDB（feedback_live 表，追加式）或 JSONL（无 DuckDB 时）
        - 计算实盘 IC（信号值 × 实际收益的截面秩相关）
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        """初始化导入器。

        Args:
            db_path: DuckDB 文件路径；None 时仅用 JSONL 落盘。
        """
        self._db_path = db_path
        self._records: list[LiveFeedbackRecord] = []

    # ─── 导入 ──────────────────────────────────────────

    def import_records(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        """导入多条记录（逐条校验 + 落盘）。

        Args:
            records: 记录列表

        Returns:
            导入统计 {total, valid, invalid, invalid_messages}
        """
        valid: list[LiveFeedbackRecord] = []
        invalid_msgs: list[str] = []
        for r in records:
            ok, msg = validate_live_feedback_record(r)
            if ok:
                valid.append(cast(LiveFeedbackRecord, {k: v for k, v in r.items() if v is not None}))
            else:
                invalid_msgs.append(msg)
        self._records.extend(valid)
        self._persist(valid)
        return {
            "total": len(records),
            "valid": len(valid),
            "invalid": len(records) - len(valid),
            "invalid_messages": invalid_msgs[:20],
        }

    def import_csv(self, path: str) -> dict[str, Any]:
        """从 CSV 导入（列名需与 LiveFeedbackRecord 字段一致）。"""
        import csv

        records: list[dict[str, Any]] = []
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                rec = {k: v for k, v in row.items() if v != ""}
                if "factor_id" not in rec or "signal_date" not in rec:
                    continue
                records.append(rec)
        return self.import_records(records)

    # ─── 实盘 IC ───────────────────────────────────────

    def compute_live_ic(
        self,
        records: Optional[list[LiveFeedbackRecord]] = None,
    ) -> dict[str, Any]:
        """计算实盘 IC。

        对每个因子：信号值 × 实际收益的截面 Spearman 相关（按信号日聚合，
        多标的时取当日截面相关均值）。

        Args:
            records: 记录列表；None 用已导入记录

        Returns:
            {factor_id: {ic, n_days, mean_return}} 汇总 + 全局统计
        """
        recs = records if records is not None else self._records
        if not recs:
            return {"factors": {}, "overall_ic": 0.0, "n_records": 0}

        from collections import defaultdict

        by_factor: dict[str, list[LiveFeedbackRecord]] = defaultdict(list)
        for r in recs:
            by_factor[r["factor_id"]].append(r)

        factor_stats: dict[str, dict[str, float]] = {}
        ics: list[float] = []
        for fid, items in by_factor.items():
            # 当日聚合：同一信号日取平均（多标的情况）
            by_date: dict[str, list[tuple[float, float]]] = defaultdict(list)
            for it in items:
                by_date[it["signal_date"]].append(
                    (float(it.get("signal_value", 0.0)), float(it.get("position_return", 0.0)))
                )
            day_ics = []
            cross_section_days = 0
            for pairs in by_date.values():
                if len(pairs) < 2:
                    continue
                cross_section_days += 1
                signals = np.array([p[0] for p in pairs])
                returns = np.array([p[1] for p in pairs])
                if np.std(signals) < 1e-12 or np.std(returns) < 1e-12:
                    continue
                r = np.corrcoef(signals, returns)[0, 1]
                if np.isfinite(r):
                    day_ics.append(r)
            # 时间序列回退：无任何截面日（单标的时序数据）时，
            # 用全期信号 vs 实际收益的秩相关作为时间序列 IC。
            if not day_ics:
                sig_all = np.array([float(it.get("signal_value", 0.0)) for it in items])
                ret_all = np.array([float(it.get("position_return", 0.0)) for it in items])
                if len(sig_all) >= 5 and np.std(sig_all) > 1e-12 and np.std(ret_all) > 1e-12:
                    r = np.corrcoef(sig_all, ret_all)[0, 1]
                    if np.isfinite(r):
                        day_ics.append(r)
            mean_ic = float(np.mean(day_ics)) if day_ics else 0.0
            factor_stats[fid] = {
                "ic": mean_ic,
                "n_days": max(cross_section_days, len(day_ics)),
                "mean_return": float(np.mean([it.get("position_return", 0.0) for it in items])),
            }
            if day_ics:
                ics.append(mean_ic)
        overall = float(np.mean(ics)) if ics else 0.0
        return {"factors": factor_stats, "overall_ic": overall, "n_records": len(recs)}

    # ─── 落盘 ──────────────────────────────────────────

    def _persist(self, records: list[LiveFeedbackRecord]) -> None:
        """落盘：优先 DuckDB，回退 JSONL。"""
        if not records:
            return
        if self._db_path:
            try:
                self._persist_duckdb(records)
                return
            except Exception as e:  # noqa: BLE001
                logger.warning("[GAP-L402] DuckDB 落盘失败 (%s)，回退 JSONL", e)
        self._persist_jsonl(records)

    def _persist_jsonl(self, records: list[LiveFeedbackRecord]) -> None:
        """JSONL 追加落盘（memory/portfolio/live_feedback.jsonl）。"""
        import json as _json
        from pathlib import Path

        out = Path("memory/portfolio/live_feedback.jsonl")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "a", encoding="utf-8") as fh:
            for r in records:
                fh.write(_json.dumps(r, ensure_ascii=False) + "\n")

    def _persist_duckdb(self, records: list[LiveFeedbackRecord]) -> None:
        """DuckDB 表 feedback_live 追加（不存在则建表）。"""
        import duckdb  # type: ignore

        con = duckdb.connect(self._db_path)
        try:
            con.execute("""
                CREATE TABLE IF NOT EXISTS feedback_live (
                    factor_id VARCHAR,
                    signal_date VARCHAR,
                    signal_value DOUBLE,
                    position_return DOUBLE,
                    turnover DOUBLE,
                    slippage DOUBLE,
                    market VARCHAR,
                    backtest_ic DOUBLE,
                    weight DOUBLE
                )
            """)
            for r in records:
                con.execute(
                    "INSERT INTO feedback_live VALUES (?,?,?,?,?,?,?,?,?)",
                    [
                        r.get("factor_id"),
                        r.get("signal_date"),
                        r.get("signal_value"),
                        r.get("position_return"),
                        r.get("turnover"),
                        r.get("slippage"),
                        r.get("market"),
                        r.get("backtest_ic"),
                        r.get("weight"),
                    ],
                )
        finally:
            con.close()


class LiveVsBacktestICReport:
    """实盘 IC vs 回测 IC 对比报告（GAP-L402）。

    输入实盘 IC（LiveFeedbackImporter 输出）与回测 IC（因子目录），
    输出对比报告：衰减判定（|实盘 − 回测| 阈值）、退役建议。
    """

    def __init__(self, live_decay_threshold: float = 0.02) -> None:
        """初始化。

        Args:
            live_decay_threshold: 实盘 IC 显著低于回测 IC 的绝对阈值（默认 0.02）
        """
        self._threshold = live_decay_threshold

    def generate(
        self,
        live_ic_result: dict[str, Any],
        backtest_ic_map: Optional[dict[str, float]] = None,
    ) -> dict[str, Any]:
        """生成对比报告。

        Args:
            live_ic_result: compute_live_ic 的输出
            backtest_ic_map: factor_id → 回测 IC（可选）

        Returns:
            对比报告 {factors: [...], summary: {...}}
        """
        factors = live_ic_result.get("factors", {})
        rows = []
        decayed = 0
        for fid, stats in factors.items():
            live_ic = stats["ic"]
            bt_ic = (backtest_ic_map or {}).get(fid)
            status = "ok"
            if bt_ic is not None:
                # 衰减判定（GAP-L402）：
                #   1) 实盘符号反转（bt>0 而 live<=0）→ decayed
                #   2) 同向但 |live| 显著低于 |bt|（阈值差）→ decayed
                if bt_ic > 0 and live_ic <= 0:
                    status = "decayed"
                    decayed += 1
                elif abs(live_ic) < abs(bt_ic) - self._threshold:
                    status = "decayed"
                    decayed += 1
                elif live_ic > 0:
                    status = "ok"
                else:
                    status = "weak"
            rows.append(
                {
                    "factor_id": fid,
                    "live_ic": round(live_ic, 4),
                    "backtest_ic": round(bt_ic, 4) if bt_ic is not None else None,
                    "status": status,
                    # GAP-I401 (v2.71.0): 退役建议（供 GAP-I305 衰减自动退役闭环消费）
                    "recommend_retire": status == "decayed",
                    "decay_gap": (round(abs(bt_ic) - abs(live_ic), 4) if bt_ic is not None else None),
                    "n_days": stats.get("n_days", 0),
                    "mean_return": round(stats.get("mean_return", 0.0), 6),
                }
            )
        return {
            "factors": sorted(rows, key=lambda r: r["live_ic"]),
            "summary": {
                "n_factors": len(rows),
                "n_decayed": decayed,
                "n_recommend_retire": sum(1 for r in rows if r["recommend_retire"]),
                "overall_live_ic": round(live_ic_result.get("overall_ic", 0.0), 4),
                "n_records": live_ic_result.get("n_records", 0),
            },
            "generated_at": _now_iso(),
        }


__all__ = [
    "FeedbackEventType",
    "RootCause",
    "FeedbackTrigger",
    "AttributionAnalyzer",
    "EvolutionDirectionAdjuster",
    "EvolutionEffectiveness",
    "FeedbackLoop",
    "LiveFeedbackRecord",
    "validate_live_feedback_record",
    "LiveFeedbackImporter",
    "LiveVsBacktestICReport",
]
