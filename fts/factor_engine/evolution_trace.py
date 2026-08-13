"""
loop_engine/evolution_trace.py — EvolutionLoop 领域 J Mixin：trace 记录 + 经验链 + 实验日志

34 计划（plans/34-evolution-loop-refactor-inventory.md）B 阶段第二步：从
evolution_loop.py 抽取领域 J（trace 记录与经验链）为独立 Mixin，行为等价、
公开 API 不变。原方法剪切迁移（不改逻辑），领域独享状态（_success_pattern_cache
/ _experiment_log_dir / _experiment_variants）随迁（mixin 类型声明 + 主类
__init__ 装配），跨领域共享状态（experience_chain / memory_dir / state_manager /
market）留在主类实例，经 self 访问。

契约（见 01-architecture.md §5 EvolutionLoop Mixin 拆分契约）：
- Mixin 方法名全局唯一，不 import evolution_loop（单向依赖，防循环导入）；
- `_QualityInspectionResult` 数据类随迁至此（被 trace 方法与
  evolution_loop._QualityInspectionCompat 共用），evolution_loop re-export。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .audit import FactorAuditReport
from .contracts import FactorEvaluation, FactorProgram
from .experience_chain import ExperienceChain, ParentFailureContext, create_trace_from_evaluation
from .experiment_log import ExperimentLogWriter, extract_scores
from .state import EvolutionStateManager
from .success_pattern import SuccessPatternConfig, SuccessPatternReport, analyze_success_patterns

logger = logging.getLogger(__name__)


# ─── 兼容包装: 替代已删除的 FactorQualityInspection（质检结果数据类） ────


class _QualityInspectionResult:
    """兼容 InspectionResult 属性接口，用于 evolution_loop 内部。"""

    def __init__(self, score: dict, filtered: bool, reason: str = "") -> None:
        self.total_score: float = score.get("total_score", 0.0)
        self.grade: str = score.get("grade", "C")
        self.reason: str = reason
        self.filtered: bool = filtered
        self.quality_score: dict = score


class EvolutionTraceMixin:
    """领域 J：trace 记录 + 经验链 + 实验日志。

    实例属性由主类 EvolutionLoop.__init__ 装配；此处类型声明供 mypy
    跨文件识别（34 计划 Mixin 拆分契约第 5/6 条）。领域独享状态
    （_success_pattern_cache/_experiment_log_dir/_experiment_variants）
    随本 Mixin 迁移，跨领域共享状态（experience_chain/memory_dir/
    state_manager/market）留在主类。
    """

    # ── 实例属性类型声明（装配在 evolution_loop.py EvolutionLoop.__init__） ──
    _success_pattern_cache: Optional[SuccessPatternReport]
    _experiment_log_dir: str
    _experiment_variants: list[dict]
    experience_chain: ExperienceChain
    memory_dir: Path
    state_manager: EvolutionStateManager
    market: str

    def _build_parent_failure_ctx(self, parent: FactorProgram) -> Optional[ParentFailureContext]:
        """构造父因子最近失败归因上下文（Phase 1.1 P0-2 定向修复）。

        从失败经验链按 parent_id 读取最近失败轨迹，聚合去重失败原因。
        无失败记录或父因子无 factor_id 时返回 None（不注入归因段落）。

        Args:
            parent: 父因子

        Returns:
            ParentFailureContext 或 None
        """
        parent_id = parent.get("factor_id")
        if not parent_id:
            return None
        traces = self.experience_chain.read_failures_by_parent(parent_id)
        if not traces:
            return None
        reasons: list[str] = []
        latest_failed_at: Optional[str] = None
        for t in traces:
            eval_ = t.get("evaluation", {})
            for reason in eval_.get("failure_reasons", []):
                if reason and reason not in reasons:
                    reasons.append(reason)
            if latest_failed_at is None:
                latest_failed_at = t.get("recorded_at")
        if not reasons:
            return None
        return ParentFailureContext(
            parent_id=parent_id,
            failure_reasons=reasons,
            patterns=list(reasons),
            latest_failed_at=latest_failed_at,
        )

    def _build_success_pattern_report(self) -> Optional[SuccessPatternReport]:
        """构造近期成功模式报告（Phase 1.2 P0-1），进程内缓存避免重复读取。

        从 FTSConfig 读取开关/窗口/样本下限，构造 SuccessPatternConfig 聚合经验链。
        异常/开关关闭 → None（prompt 层不注入）；空报告（样本不足）照常返回，
        由 MacroEvolver 判断 sample_count==0 不注入。

        Returns:
            SuccessPatternReport 或 None
        """
        if self._success_pattern_cache is not None:
            return self._success_pattern_cache
        try:
            from fts.config.settings import get_config as _get_sp_cfg

            _cfg = _get_sp_cfg()
            config = SuccessPatternConfig(
                enabled=_cfg.evolution_success_pattern_enabled,
                window_days=_cfg.success_pattern_window_days,
                min_sample=_cfg.success_pattern_min_sample,
            )
            if not config.enabled:
                return None
            report = analyze_success_patterns(self.experience_chain, config)
            self._success_pattern_cache = report
            return report
        except Exception as e:  # noqa: BLE001 — 降级不阻断演化
            logger.debug("成功模式报告构造失败（降级跳过）: %s", e)
            return None

    def _record_experiment_variant(
        self,
        factor: FactorProgram,
        parent: Optional[FactorProgram],
        generation: int,
        method: str,
        summary: str,
        evaluation: Optional[FactorEvaluation],
        outcome: str,
        quality_grade: Optional[str] = None,
    ) -> None:
        """记录实验候选（Phase 2 P1-2），run 结束时导出实验日志。

        Args:
            factor: 候选因子
            parent: 父因子（可能为 None）
            generation: 当前代数
            method: 演化方法（macro/gp/operator/deep）
            summary: 演化摘要
            evaluation: 评估结果（预筛/运行时拦截可能为 None）
            outcome: 候选结局（prefilter_rejected/verifier_failed/audit_failed/promoted）
            quality_grade: 质量评分卡等级（A/B/C），available 时传入
        """
        scores = extract_scores(evaluation)
        if quality_grade is not None:
            scores["quality_grade"] = quality_grade
        self._experiment_variants.append(
            {
                "generation": generation,
                "parent_id": parent.get("factor_id") if parent else None,
                "candidate_id": factor.get("factor_id", "?"),
                "method": method,
                "summary": summary,
                "scores": scores,
                "outcome": outcome,
            }
        )

    def _export_experiment_log(
        self,
        run_id: str,
        trace_id: str,
        generations_completed: int,
    ) -> Optional[Path]:
        """导出结构化实验日志（Phase 2 P1-2），非阻塞（失败仅 warning）。"""
        try:
            writer = ExperimentLogWriter(self._experiment_log_dir)
            return writer.export(
                run_id=run_id,
                trace_id=trace_id,
                market=self.market,
                started_at=datetime.now().isoformat(),
                generations_completed=generations_completed,
                variants=self._experiment_variants,
            )
        except Exception as e:  # noqa: BLE001 — 导出失败降级不阻断 run
            logger.warning("实验日志导出失败（降级不阻断）: %s", e)
            return None

    def _record_audit_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        audit_report: FactorAuditReport,
        evaluation: Optional[FactorEvaluation] = None,
    ) -> None:
        """记录审计失败轨迹。

        Args:
            factor: 因子程序
            generation: 当前代数
            trace_id: 全链路 trace_id
            audit_report: 审计报告
            evaluation: 评估结果（可选）
        """
        import json

        factor_id = factor.get("factor_id", "unknown")
        factor_name = factor.get("name", "?")
        sub_trace_id = f"{trace_id}_g{generation}_audit_fail_{factor_id[:8]}"

        trace_dir = self.memory_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        fp = trace_dir / f"{sub_trace_id}.json"

        record: dict[str, Any] = {
            "trace_id": sub_trace_id,
            "parent_trace_id": trace_id,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "generation": generation,
            "type": "audit_failed",
            "timestamp": datetime.now().isoformat(),
            "audit_report": audit_report.to_dict(),
            "failure_analysis": audit_report.failure_analysis,
        }
        if evaluation:
            record["evaluation"] = evaluation

        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[evo] 审计失败轨迹已记录: {factor_name} → 代 {generation}, 通过率={audit_report.pass_rate:.0%}")

    def _record_ablation_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        ablation_result: dict[str, Any],
    ) -> None:
        """记录消融实验失败轨迹。"""
        import json

        factor_id = factor.get("factor_id", "unknown")
        factor_name = factor.get("name", "?")
        sub_trace_id = f"{trace_id}_g{generation}_ablation_fail_{factor_id[:8]}"

        trace_dir = self.memory_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        fp = trace_dir / f"{sub_trace_id}.json"

        record: dict[str, Any] = {
            "trace_id": sub_trace_id,
            "parent_trace_id": trace_id,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "generation": generation,
            "type": "ablation_failed",
            "timestamp": datetime.now().isoformat(),
            "ablation_result": ablation_result,
        }

        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[evo] 消融失败轨迹已记录: {factor_name}")

    def _record_robustness_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        robustness_result: dict[str, Any],
    ) -> None:
        """记录鲁棒性审查失败轨迹。"""
        import json

        factor_id = factor.get("factor_id", "unknown")
        factor_name = factor.get("name", "?")
        sub_trace_id = f"{trace_id}_g{generation}_robustness_fail_{factor_id[:8]}"

        trace_dir = self.memory_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        fp = trace_dir / f"{sub_trace_id}.json"

        record: dict[str, Any] = {
            "trace_id": sub_trace_id,
            "parent_trace_id": trace_id,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "generation": generation,
            "type": "robustness_failed",
            "timestamp": datetime.now().isoformat(),
            "robustness_result": robustness_result,
        }

        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[evo] 鲁棒性失败轨迹已记录: {factor_name}")

    def _record_causal_failed_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        causal_result: dict[str, Any],
    ) -> None:
        """记录因果验证失败轨迹。"""
        import json

        factor_id = factor.get("factor_id", "unknown")
        factor_name = factor.get("name", "?")
        sub_trace_id = f"{trace_id}_g{generation}_causal_fail_{factor_id[:8]}"

        trace_dir = self.memory_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        fp = trace_dir / f"{sub_trace_id}.json"

        record: dict[str, Any] = {
            "trace_id": sub_trace_id,
            "parent_trace_id": trace_id,
            "factor_id": factor_id,
            "factor_name": factor_name,
            "generation": generation,
            "type": "causal_failed",
            "timestamp": datetime.now().isoformat(),
            "causal_result": causal_result,
        }

        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"[evo] 因果失败轨迹已记录: {factor_name}")

    def _record_success_trace(
        self,
        factor: FactorProgram,
        generation: int,
        mutation_type: str,
        mutation_summary: str,
        evaluation: FactorEvaluation,
        lessons: list[str],
        trace_id: str,
    ) -> None:
        """记录成功轨迹。"""
        # 生成唯一子 trace_id（避免文件名碰撞）
        sub_trace_id = f"{trace_id}_g{generation}_{mutation_type}_{factor['factor_id'][:8]}"
        trace = create_trace_from_evaluation(
            factor_id=factor["factor_id"],
            parent_id=factor.get("parent_id"),
            generation=generation,
            mutation_type=mutation_type,
            mutation_summary=mutation_summary,
            evaluation=evaluation,
            lessons=lessons,
            trace_id=sub_trace_id,
        )
        self.experience_chain.record_success(trace)
        self.state_manager.add_experience_ref(self.state_manager.load_or_init(), trace["trace_id"])

    def _record_failure_trace(
        self,
        factor: FactorProgram,
        generation: int,
        mutation_type: str,
        mutation_summary: str,
        failure_reasons: list[str],
        trace_id: str,
        evaluation: Optional[FactorEvaluation] = None,
    ) -> None:
        """记录失败轨迹。"""
        # 生成唯一子 trace_id（避免文件名碰撞）
        sub_trace_id = f"{trace_id}_g{generation}_{mutation_type}_{factor['factor_id'][:8]}"
        # 构造评估结果
        if evaluation is None:
            evaluation = FactorEvaluation(
                factor_id=factor["factor_id"],
                trace_id=sub_trace_id,
                passed=False,
                failure_reasons=failure_reasons or ["未知失败"],
                evaluated_at=datetime.now().isoformat(),
            )
        else:
            # 确保失败原因非空
            if not evaluation.get("failure_reasons"):
                evaluation["failure_reasons"] = failure_reasons or ["未知失败"]

        trace = create_trace_from_evaluation(
            factor_id=factor["factor_id"],
            parent_id=factor.get("parent_id"),
            generation=generation,
            mutation_type=mutation_type,
            mutation_summary=mutation_summary,
            evaluation=evaluation,
            lessons=[f"代 {generation} 失败: {r}" for r in failure_reasons[:3]],
            trace_id=sub_trace_id,
        )
        try:
            self.experience_chain.record_failure(trace)
        except Exception:
            pass  # 失败轨迹记录失败不应中断主循环

    def _log_inspection_detail(
        self,
        factor: FactorProgram,
        inspection: _QualityInspectionResult,
        status: str,
        generation: int,
    ) -> None:
        """打印详细的因子质检日志。

        Args:
            factor: 因子程序
            inspection: 质检结果
            status: 状态 ("通过" / "淘汰")
            generation: 当前代数
        """
        factor_name = factor.get("name", factor.get("factor_id", "?"))
        total = inspection.total_score
        grade = inspection.grade
        reason = inspection.reason

        # 主日志行
        icon = "✅" if status == "通过" else "❌"
        print(f"[evo] {icon} 代{generation} 因子质检{status}: {factor_name} (等级={grade}, 总分={total}/50)")

        # 淘汰时显示原因
        if status == "淘汰" and reason:
            print(f"       淘汰原因: {reason}")

        # 显示各维度得分
        dims = inspection.quality_score.get("dimension_scores", [])
        if dims:
            low_dims = [d for d in dims if d.get("score", 5) < 3]
            if low_dims:
                print("       ⚠️  低分项 (< 3.0):")
                for d in low_dims[:5]:  # 最多显示 5 个低分项
                    name = d.get("name", "?")
                    score = d.get("score", 0)
                    desc = d.get("description", "")
                    print(f"         - {name}: {score:.1f}/5.0 ({desc})")

    def _record_quality_filtered_trace(
        self,
        factor: FactorProgram,
        generation: int,
        trace_id: str,
        inspection: _QualityInspectionResult,
        evaluation: Optional[FactorEvaluation] = None,
    ) -> None:
        """记录质检过滤轨迹 (Phase A.1)。

        当因子通过 Verifier 但质量评分低于阈值时记录。
        """
        sub_trace_id = f"{trace_id}_g{generation}_quality_filtered_{factor['factor_id'][:8]}"
        reasons = [inspection.reason] if inspection.reason else ["质检淘汰"]
        if evaluation is None:
            evaluation = FactorEvaluation(
                factor_id=factor["factor_id"],
                trace_id=sub_trace_id,
                passed=False,
                failure_reasons=reasons,
                evaluated_at=datetime.now().isoformat(),
            )
        else:
            if not evaluation.get("failure_reasons"):
                evaluation["failure_reasons"] = reasons

        trace = create_trace_from_evaluation(
            factor_id=factor["factor_id"],
            parent_id=factor.get("parent_id"),
            generation=generation,
            mutation_type="quality_filtered",
            mutation_summary=(f"质量评分淘汰: 等级={inspection.grade}, 总分={inspection.total_score}/50"),
            evaluation=evaluation,
            lessons=[f"质检淘汰: {inspection.reason}"],
            trace_id=sub_trace_id,
        )
        try:
            self.experience_chain.record_failure(trace)
        except Exception:
            pass
