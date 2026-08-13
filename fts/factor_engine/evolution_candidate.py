"""
loop_engine/evolution_candidate.py — 领域 B 协作类：候选准入链

34 计划（plans/34-evolution-loop-refactor-inventory.md）C 阶段 Phase 47i：
B 阶段产物 EvolutionCandidateMixin 组合式重构为 CandidateProcessor 协作类，
行为等价、公开 API 不变。领域独享状态（_prior_evaluations，34 §8.3 状态
所有权确认归本协作类）随迁本类构造（原主类 __init__ 对应段迁移）；跨领域
共享数据（data / forward_returns / cross_section_data / _is_cross_section /
evaluation_chain / verifier / quality_inspector / state_manager / budget /
n_trials_micro / _micro_staged_evolution / _signal_cache（归 AuditPipeline，
主类 property 转发共享同一引用）/ _consecutive_low_ic（归主循环，主类 property
转发到 low_ic_box，与 UctSelector 共享））经 owner（主类实例）动态读取。
跨域方法调用（_record_*_trace / _record_experiment_variant / _evaluate_cross_section
/ _build_wf_config / _update_uct_stats / _run_*_check / _promote_to_elite 等 21 个）
经 owner 转发使测试 `loop._X = MagicMock` 类实例打桩生效。主类 EvolutionLoop
组合持有本类实例，保留 1 方法转发桩 + 1 属性 property 转发（兼容测试零改动，
见 34 §8.5）。**本 Phase 完成后 C 阶段 9 协作类全部交付，主类继承链清零。**

契约（见 01-architecture.md §5 EvolutionLoop Mixin 拆分契约）：
- 协作类不 import evolution_loop（防循环导入），owner 仅经 Any 标注，
  运行时经主类组装注入。
"""

from __future__ import annotations

import logging
from typing import Any

from .contracts import FactorCorrelation, FactorEvaluation, FactorProgram  # noqa: E402 — 延迟导入规避循环依赖
from .evolution_trace import _QualityInspectionResult  # noqa: E402 — 类型注解运行时解析
from .micro_evolution import evolve_micro  # noqa: E402 — 延迟导入规避循环依赖

logger = logging.getLogger(__name__)


class CandidateProcessor:
    """领域 B：候选准入链（34 计划 C 阶段协作类）。

    状态所有权（34 §8.3）：领域独享状态（_prior_evaluations）随迁本类构造；
    跨领域共享数据（含 _signal_cache 归 AuditPipeline / _consecutive_low_ic 归
    主循环）经 owner（主类实例）动态读取；跨域方法调用经 owner 转发使测试
    实例打桩生效。主类 EvolutionLoop 组合持有本类实例，保留 1 方法转发桩 +
    1 属性 property 转发（兼容测试零改动，见 34 §8.5）。
    """

    def __init__(self, owner: Any) -> None:
        self._owner: Any = owner
        # ── 领域独享状态随迁（原主类 __init__ 对应段迁移） ──
        self._prior_evaluations: list[FactorEvaluation] = []

    def _process_candidate(
        self,
        factor: FactorProgram,
        parent: FactorProgram,
        generation: int,
        evolution_method: str,
        evolution_summary: str,
        state: dict[str, Any],
        elite_ids: list[str],
        trace_id: str,
        seed_correlations: list[FactorCorrelation],
    ) -> bool:
        """Step 2-6 准入链（GAP-I201 抽取，batch 与单因子路径共用）。

        流程: 微观演化 → 三级评估 → UCT 反馈 → Verifier → 质量评分卡
              → 端到端回测 → 数据质量 → 6 项强制审计 → 消融 → 因果
              → 鲁棒性 → SHAP → 晋升/淘汰 → 状态持久化。

        Args:
            factor: 后代因子（已通过运行时校验与快速预筛）
            parent: 父因子
            generation: 当前代数
            evolution_method: 演化方式
            evolution_summary: 演化摘要
            state: L2 演化状态
            elite_ids: 已晋升 elite 因子 ID 列表
            trace_id: 全链路 trace_id
            seed_correlations: 种子相关性预检结果

        Returns:
            是否成功晋升 elite。
        """
        promoted = False

        # ── Step 2: 微观演化（optuna 调参） ──
        try:
            # 横截面模式：用第一个股票的数据做微参
            micro_data = (
                list(self._owner.cross_section_data.values())[0]
                if (self._owner._is_cross_section and self._owner.cross_section_data is not None)
                else self._owner.data
            )
            micro_ret = self._owner.forward_returns
            # GAP-I205 (v2.68.0): 两阶段漏斗——粗筛低 trials 快速打分淘汰低潜力，
            # 精筛 trials 按粗筛得分自适应 + TPE 早停；配置 micro_staged_evolution 可关闭。
            optimized_factor, _ = evolve_micro(
                factor,
                micro_data,
                micro_ret,
                n_trials=self._owner.n_trials_micro,
                use_staged=self._owner._micro_staged_evolution,
            )
        except Exception as e:
            self._owner._record_failure_trace(
                factor,
                generation,
                "micro_evolution",
                f"微观演化失败: {e}",
                [],
                trace_id,
            )
            self._owner._record_experiment_variant(
                factor,
                parent,
                generation,
                evolution_method,
                f"微观演化失败: {e}",
                None,
                "verifier_failed",
            )
            return False

        # ── Step 3: 三级评估链 ──
        if self._owner._is_cross_section:
            evaluation = self._owner._evaluate_cross_section(optimized_factor, trace_id)
        else:
            # GAP-070: 注入共享信号缓存 + 统一走航配置（与审计 _build_wf_config 同源，
            # 支撑审计复用本走航结果，消除双重 WalkForward）
            evaluation = self._owner.evaluation_chain.evaluate(
                optimized_factor,
                self._owner.data,
                self._owner.forward_returns,
                prior_evaluations=self._prior_evaluations,
                signal_cache=self._owner._signal_cache,
                walk_forward_config=self._owner._build_wf_config(self._owner.data),
            )
        self._prior_evaluations.append(evaluation)
        self._owner.state_manager.increment_evaluated(state)

        # ── UCT 反馈: 根据子因子表现更新父因子统计 ──
        self._owner._update_uct_stats(parent, evaluation)

        # ── Step 4: Verifier 判定 ──
        verifier_result = self._owner.verifier.check(evaluation)
        print(f"[DEBUG-evo] verifier_result={verifier_result}")
        print(f"[DEBUG-evo] evaluation.get('level_1_backtest')={evaluation.get('level_1_backtest')}")

        # ── Step 4.5: 因子质量评分卡 (Phase A.1) ──
        inspection: _QualityInspectionResult = self._owner.quality_inspector.inspect(
            factor=optimized_factor,
            evaluation=evaluation,
        )

        # ── Step 4.5.5: 端到端回测流水线 (Phase B.2) ──
        backtest_result = self._owner._run_backtest_pipeline(
            optimized_factor,
            evaluation,
            trace_id,
        )
        if backtest_result:
            evaluation["backtest_pipeline"] = backtest_result

        # ── Step 4.5.6: 数据质量监控 (Phase B.1) ──
        self._owner._register_factor_baseline(optimized_factor, evaluation)
        dq_alerts = self._owner._check_factor_data_quality(
            optimized_factor,
            evaluation,
        )
        if dq_alerts:
            critical = any(getattr(a, "severity", "") == "critical" for a in dq_alerts)
            if critical:
                print(f"[evo] 数据质量严重告警 [{optimized_factor.get('name', '?')}]: 跳过晋升")
                self._owner._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    f"数据质量严重告警: {[a.message for a in dq_alerts if getattr(a, 'severity', '') == 'critical'][:2]}",
                    evaluation,
                    "audit_failed",
                )
                return False

        # ── Step 4.6: 因子强制审计 (Phase B.3) ──
        audit_report = self._owner._run_factor_audit(
            optimized_factor,
            evaluation,
            trace_id,
        )

        # ── Step 5: 经验链记录 + 分级准入 ──
        print(f"[DEBUG-evo] verifier_result['passed']={verifier_result.get('passed')}")
        if verifier_result["passed"]:
            print("[DEBUG-evo] PROMOTION PATH")
            # 质检过滤: 仅 A/B 级晋升，C 级淘汰
            if inspection.filtered:
                self._owner._log_inspection_detail(
                    optimized_factor,
                    inspection,
                    "淘汰",
                    generation,
                )
                self._owner._record_quality_filtered_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    inspection,
                    evaluation=evaluation,
                )
                self._owner._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    f"质检过滤淘汰: 质量分={inspection.total_score}/50 ({inspection.grade}级)",
                    evaluation,
                    "audit_failed",
                    quality_grade=inspection.grade,
                )
                return False

            # 审计过滤: 审计未通过则拒绝准入
            if not audit_report.passed:
                failed_items = [it.name for it in audit_report.failed_items]
                print(
                    f"[evo] 审计未通过 [{optimized_factor.get('name', '?')}]: "
                    f"失败项={failed_items}, 通过率={audit_report.pass_rate:.0%}"
                )
                self._owner._record_audit_failed_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    audit_report,
                    evaluation=evaluation,
                )
                self._owner._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    f"审计未通过: 失败项={failed_items}, 通过率={audit_report.pass_rate:.0%}",
                    evaluation,
                    "audit_failed",
                )
                return False

            # ── Step 4.6.5: 消融实验检查 (Phase A 集成) ──
            ablation_result = self._owner._run_ablation_check(
                optimized_factor,
                evaluation,
                trace_id,
            )
            evaluation["ablation_check"] = ablation_result
            if not ablation_result.get("passed", True):
                print(f"[evo] 消融实验未通过 [{optimized_factor.get('name', '?')}]: 疑似伪相关")
                self._owner._record_ablation_failed_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    ablation_result,
                )
                self._owner._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    "消融实验未通过: 疑似伪相关",
                    evaluation,
                    "audit_failed",
                )
                return False

            # ── Step 4.6.6: 因果结构审查 (Phase C 集成) ──
            causal_result = self._owner._run_causal_validation(
                optimized_factor,
                evaluation,
                trace_id,
            )
            evaluation["causal_validation"] = causal_result
            if not causal_result.get("passed", True):
                print(f"[evo] 因果审查未通过 [{optimized_factor.get('name', '?')}]: 事件敏感")
                self._owner._record_causal_failed_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    causal_result,
                )
                self._owner._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    "因果审查未通过: 事件敏感",
                    evaluation,
                    "audit_failed",
                )
                return False

            # ── Step 4.6.7: 鲁棒性审查 (Phase B 集成) ──
            robustness_result = self._owner._run_robustness_check(
                optimized_factor,
                evaluation,
                trace_id,
            )
            evaluation["robustness_check"] = robustness_result
            if not robustness_result.get("passed", True):
                print(f"[evo] 鲁棒性审查未通过 [{optimized_factor.get('name', '?')}]")
                self._owner._record_robustness_failed_trace(
                    optimized_factor,
                    generation,
                    trace_id,
                    robustness_result,
                )
                self._owner._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    "鲁棒性审查未通过",
                    evaluation,
                    "audit_failed",
                )
                return False

            # ── Step 4.6.8: SHAP 可解释性分析 (Phase B 集成) ──
            shap_result = self._owner._run_shap_analysis(
                optimized_factor,
                evaluation,
                trace_id,
            )
            evaluation["shap_analysis"] = shap_result

            # 晋级精英池（去重检查 + 质量评分附加 + 审计报告）
            self._owner._log_inspection_detail(
                optimized_factor,
                inspection,
                "通过",
                generation,
            )
            promoted_path = self._owner._promote_to_elite(
                optimized_factor,
                evaluation,
                seed_correlations=seed_correlations,
                quality_score=inspection.quality_score,
                audit_report=audit_report,
            )
            if promoted_path is None:
                # 因子名称重复，跳过
                self._owner._record_experiment_variant(
                    optimized_factor,
                    parent,
                    generation,
                    evolution_method,
                    "因子名称重复，跳过晋升",
                    evaluation,
                    "audit_failed",
                    quality_grade=inspection.grade,
                )
                return False
            self._owner.state_manager.increment_promoted(state)
            elite_ids.append(optimized_factor["factor_id"])
            self._owner._record_success_trace(
                optimized_factor,
                generation,
                evolution_method,
                evolution_summary,
                evaluation,
                [
                    f"代 {generation} 晋级精英池",
                    f"质量分={inspection.total_score}/50 ({inspection.grade}级)",
                    f"审计通过率={audit_report.pass_rate:.0%}",
                ],
                trace_id,
            )
            self._owner._record_experiment_variant(
                optimized_factor,
                parent,
                generation,
                evolution_method,
                evolution_summary,
                evaluation,
                "promoted",
                quality_grade=inspection.grade,
            )
            self._owner._consecutive_low_ic = 0
            print("[DEBUG-evo] promotion path: _consecutive_low_ic reset to 0")
            promoted = True
        else:
            # 失败轨迹
            self._owner._record_failure_trace(
                optimized_factor,
                generation,
                evolution_method,
                evolution_summary,
                verifier_result["failure_reasons"],
                trace_id,
                evaluation=evaluation,
            )
            self._owner._record_experiment_variant(
                optimized_factor,
                parent,
                generation,
                evolution_method,
                f"Verifier 未通过: {verifier_result.get('failure_reasons', [])[:3]}",
                evaluation,
                "verifier_failed",
            )
            # 检查低 IC
            bt = evaluation.get("level_1_backtest", {})
            if abs(bt.get("ic", 0)) < self._owner.budget["circuit_breaker_low_ic_threshold"]:
                self._owner._consecutive_low_ic += 1
                print(
                    f"[DEBUG-evo] failure path, low IC: _consecutive_low_ic incremented to {self._owner._consecutive_low_ic}"
                )
            else:
                self._owner._consecutive_low_ic = 0
                print("[DEBUG-evo] failure path, not low IC: _consecutive_low_ic reset to 0")

        # ── Step 6: 状态持久化 ──
        state["last_generation"] = generation
        self._owner.state_manager.save(state)

        return promoted
