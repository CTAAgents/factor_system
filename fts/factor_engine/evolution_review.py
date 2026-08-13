"""
loop_engine/evolution_review.py — FactorReviewer 协作类：定期评审与数据质量

34 计划（plans/34-evolution-loop-refactor-inventory.md）C 阶段 Phase 47f：
B 阶段产物 EvolutionReviewMixin 组合式重构为 FactorReviewer 协作类，行为等价、
公开 API 不变。本领域无领域独享状态：组件（elite_tracker / feedback_loop /
logic_monitor / verifier / data_quality_monitor）与跨领域共享数据（data /
elite_dir / _decay_auto_retire_enabled）均由主类 EvolutionLoop 持有，本协作类
经 owner（主类实例）动态读取（34 §8.3 可变上下文修订），兼容主类/测试运行时
重赋值。主类 EvolutionLoop 组合持有本类实例，保留 4 方法转发桩（兼容测试
零改动，见 34 §8.5）。

跨组件约束（34 §8.3）：协作类不 import evolution_loop（防循环导入），
owner 仅经 Any 标注，运行时经主类组装注入。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from .contracts import FactorEvaluation, FactorProgram

logger = logging.getLogger(__name__)


class FactorReviewer:
    """领域 F：定期评审与数据质量（34 计划 C 阶段协作类）。

    状态所有权（34 §8.3）：无领域独享状态——组件（elite_tracker/feedback_loop/
    logic_monitor/verifier/data_quality_monitor）与共享数据（data/elite_dir/
    _decay_auto_retire_enabled）均由主类持有，本协作类经 owner（主类实例）
    动态读取，兼容运行时重赋值。主类 EvolutionLoop 组合持有本类实例，保留
    4 方法转发桩（兼容测试零改动，见 34 §8.5）。
    """

    def __init__(self, owner: Any) -> None:
        self._owner: Any = owner
        # 无领域独享状态；全部属性读取延迟到方法体内经 _owner 动态访问。

    # ── Phase A.2: EliteFactorTracker 定期重评估 ──────────

    def _run_periodic_factor_review(
        self,
        elite_ids: list[str],
        trace_id: str,
    ) -> None:
        """运行精英因子定期重评估（Phase A.2 集成）。

        在演化循环结束时，对所有精英因子执行:
        1. 自动淘汰检查（衰减/严重衰减）
        2. 生成因子状态报告
        3. 更新因子跟踪快照

        Args:
            elite_ids: 精英因子 ID 列表
            trace_id: 全链路 trace_id
        """
        try:
            print("[elite-review] 开始精英因子定期重评估...")

            # 1. 自动淘汰检查（GAP-I305: 受 decay_auto_retire_enabled 开关控制）
            if self._owner._decay_auto_retire_enabled:
                retired = self._owner.elite_tracker.auto_retire()
                if retired:
                    print(f"[elite-review] 自动淘汰 {len(retired)} 个因子: {retired}")
            else:
                # 开关关闭：仅统计应退役而未退役的因子（日志告警）
                observe = self._owner.elite_tracker.get_by_status("decaying")
                if observe:
                    print(f"[elite-review] 自动退役已关闭，当前 {len(observe)} 个衰减因子待处理")

            # 2. 为每个精英因子更新跟踪快照 + 逻辑监控
            for fid in elite_ids:
                # 先检查跟踪记录是否存在，不存在则跳过（可能种子因子重复跳过）
                tracker_snapshot = self._owner.elite_tracker.get(fid)
                if tracker_snapshot is None:
                    logger.debug(
                        "跳过重评估: 跟踪记录不存在 [factor_id=%s]（可能种子因子重复跳过）",
                        fid,
                    )
                    continue
                factor_data = self._owner._get_factor_data_for_review(fid)
                if factor_data is None:
                    continue
                ic = factor_data.get("ic", 0.0)
                sharpe = factor_data.get("sharpe", 0.0)
                self._owner.elite_tracker.update(fid, ic, sharpe)

                # ── GAP-I305: 衰减分级 + 反馈闭环联动 ──
                try:
                    snapshot = self._owner.elite_tracker.get(fid)
                    decay_grade = (snapshot or {}).get("decay_grade", "normal")
                    if decay_grade in ("observe", "retired"):
                        # 构造 FACTOR_DECAY 反馈事件，走归因分析并记录动作
                        event = {
                            "event_id": f"fe_decay_{fid}",
                            "event_type": "factor_decay",
                            "factor_id": fid,
                            "trigger_reason": f"衰减分级={decay_grade}",
                            "severity": ("critical" if decay_grade == "retired" else "warning"),
                            "payload": {
                                "decay_grade": decay_grade,
                                "ic_slope_6m": (snapshot or {}).get("ic_slope_6m", 0.0),
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "handled": False,
                            "handled_at": None,
                        }
                        result = self._owner.feedback_loop._handle_event(  # noqa: SLF001
                            event,
                            {fid: factor_data},
                            {},
                        )
                        # 反馈结果写回跟踪快照（可追溯）
                        snap = self._owner.elite_tracker.get(fid) or {}
                        snap["last_feedback"] = {
                            "decay_grade": decay_grade,
                            "root_cause": result.get("root_cause", "unknown"),
                            "action": result.get("action_taken", "monitor_only"),
                            "at": datetime.now(timezone.utc).isoformat(),
                        }
                        self._owner.elite_tracker._write_snapshot(fid, snap)  # noqa: SLF001
                except Exception as e:
                    logger.debug("衰减反馈联动跳过 %s: %s", fid, e)

                # ── Phase C.2: LogicMonitor 集成 ──
                try:
                    import json

                    # 从 elite 快照读取因子程序（_promote_to_elite 写入）
                    fp_snapshot = self._owner.elite_dir / f"{fid}.json"
                    if not fp_snapshot.exists() or self._owner.data is None:
                        continue
                    factor_program = json.loads(fp_snapshot.read_text(encoding="utf-8"))
                    logic_report = self._owner.logic_monitor.run(
                        factor_program,
                        self._owner.data,
                        switch_dates=[],
                    )
                    if not logic_report.all_healthy:
                        print(f"[elite-review] 逻辑监控告警: {fid}")
                except Exception as e:
                    logger.debug("逻辑监控跳过 %s: %s", fid, e)

            # 3. 生成报告
            report = self._owner.elite_tracker.report()
            status_counts = report.get("status_counts", {})
            grade_counts = report.get("grade_counts", {})
            print(
                f"[elite-review] 因子状态报告: "
                f"活跃={status_counts.get('active', 0)}, "
                f"观察={status_counts.get('observing', 0)}, "
                f"衰减={status_counts.get('decaying', 0)}, "
                f"淘汰={status_counts.get('retired', 0)}, "
                f"总计={status_counts.get('total', 0)}"
            )
            print(
                f"[elite-review] 等级分布: "
                f"A级={grade_counts.get('A', 0)}, "
                f"B级={grade_counts.get('B', 0)}, "
                f"C级={grade_counts.get('C', 0)}"
            )
        except Exception as e:
            logger.debug("精英因子定期重评估异常: %s", e)

    def _get_factor_data_for_review(
        self,
        factor_id: str,
    ) -> Optional[dict[str, float]]:
        """获取因子的 IC 和 Sharpe 数据用于重评估。

        Args:
            factor_id: 因子 ID

        Returns:
            包含 ic 和 sharpe 的字典，失败返回 None
        """
        try:
            factor_data = (
                self._owner.verifier.get_factor_by_id(factor_id)
                if hasattr(self._owner.verifier, "get_factor_by_id")
                else None
            )
            if factor_data is None:
                return {"ic": 0.0, "sharpe": 0.0}
            return {"ic": 0.0, "sharpe": 0.0}
        except Exception:
            return None

    # ── Phase B.1: 数据质量监控集成 ──────────────────────────

    def _register_factor_baseline(
        self,
        factor: "FactorProgram",
        evaluation: "FactorEvaluation",
    ) -> None:
        """注册因子基准数据到数据质量监控器。

        当因子首次通过评估时，将其 IC 和容量注册为基准，
        用于后续监控数据漂移和容量突变。

        Args:
            factor: 因子程序
            evaluation: 评估结果
        """
        factor_id = factor.get("factor_id", "?")
        bt = evaluation.get("level_1_backtest", {}) if isinstance(evaluation, dict) else {}
        ic = bt.get("ic", 0.0) if isinstance(bt, dict) else 0.0
        self._owner.data_quality_monitor.register_factor(
            factor_id=factor_id,
            baseline_ic=ic,
            baseline_capacity=0.0,
            ic_std=max(abs(ic) * 0.1, 0.001),
        )

    def _check_factor_data_quality(
        self,
        factor: "FactorProgram",
        evaluation: "FactorEvaluation",
    ) -> list[Any]:
        """检查因子数据质量，返回触发的告警列表。

        Args:
            factor: 因子程序
            evaluation: 当前评估结果

        Returns:
            告警列表（可能为空）
        """
        factor_id = factor.get("factor_id", "?")
        bt = evaluation.get("level_1_backtest", {}) if isinstance(evaluation, dict) else {}
        current_ic = bt.get("ic", 0.0) if isinstance(bt, dict) else 0.0
        alerts = self._owner.data_quality_monitor.check(
            factor_id=factor_id,
            current_ic=current_ic,
        )
        if alerts:
            for alert in alerts:
                alert_type = getattr(alert, "alert_type", "unknown")
                severity = getattr(alert, "severity", "unknown")
                msg = getattr(alert, "message", "")
                print(f"[dq-monitor] 告警 [{factor_id}]: type={alert_type}, severity={severity}, msg={msg}")
        return alerts
