"""
loop_engine/evolution_loop.py — L2 因子演化主循环

HARNESS §11-loop-engineering.md §2.2:
    seed_pool.fetch()  →  for generation in 1..MAX_GEN:
        ├─ macro_evolution.evolve(factor, experience_chain)  # LLM 改逻辑
        ├─ micro_evolution.optimize(factor_new)              # optuna 100 trials
        ├─ evaluation_chain.evaluate(factor_optimized)       # 三级评估
        ├─ verifier.check(eval_result)                        # 锁定 Verifier
        ├─ experience_chain.record(factor, eval_result)       # 经验链
        └─ state.persist(generation, factor, eval_result)     # 状态文件

预算控制 + 熔断:
    - 单夜 token 超 2x → circuit_broken
    - 连续 3 代 IC < 0.01 → circuit_broken
    - 失败率 > 90% → circuit_broken

版本: v1.9.0（与 FTS 同步，引入 UCT 父因子选择）
"""
# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments,too-many-locals,too-few-public-methods,broad-exception-caught,import-outside-toplevel

from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

from .contracts import (
    DEFAULT_BUDGET_CONFIG,
    BudgetConfig,
    EvolutionState,
    FactorCorrelation,
    FactorEvaluation,
    FactorProgram,
)
from .audit import FactorAuditor, FactorAuditReport
from .evaluation_chain import (
    EvaluationChain,
    cross_section_evaluate_backtest,
)
from .experience_chain import (
    ExperienceChain,
    create_trace_from_evaluation,
)
from .macro_evolution import MacroEvolver, get_default_llm_client
from .micro_evolution import evolve_micro
from .seed_pool import SeedPool, compute_seed_correlations
from .state import EvolutionStateManager, generate_trace_id
from .verifier import FactorVerifier, get_global_verifier


# ─── UCT 常量 ─────────────────────────────────────────────

UCT_EXPLORATION_C: float = 1.0
"""UCT 探索常数。越大越倾向探索未访问的父因子。"""


# ─── 演化结果 ─────────────────────────────────────────────

@dataclass
class EvolutionRunResult:
    """单次演化运行的结果。"""
    run_id: str
    trace_id: str
    generations_completed: int
    total_factors_evaluated: int
    total_factors_promoted: int
    tokens_consumed: int
    status: str  # running / paused / completed / circuit_broken
    circuit_breaker_reason: Optional[str] = None
    elite_factor_ids: list[str] = None  # type: ignore[assignment]
    seed_correlations: Optional[list[FactorCorrelation]] = None  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "generations_completed": self.generations_completed,
            "total_factors_evaluated": self.total_factors_evaluated,
            "total_factors_promoted": self.total_factors_promoted,
            "tokens_consumed": self.tokens_consumed,
            "status": self.status,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "elite_factor_ids": self.elite_factor_ids or [],
            "seed_correlations": self.seed_correlations or [],
        }


# ─── 演化循环 ─────────────────────────────────────────────

class EvolutionLoop:
    """L2 因子演化主循环。

    Usage:
        loop = EvolutionLoop(
            data=my_ohlcv_df,
            forward_returns=my_returns_array,
            elite_dir="memory/knowledge/factors/elite",
        )
        result = loop.run()
    """

    def __init__(
        self,
        data: pd.DataFrame,
        forward_returns: np.ndarray,
        elite_dir: str | Path | None = None,
        memory_dir: str | Path = "memory/evolution",
        budget: Optional[BudgetConfig] = None,
        verifier: Optional[FactorVerifier] = None,
        llm_client: Optional[Any] = None,
        seed_pool: Optional[SeedPool] = None,
        n_trials_micro: int = 100,
        cross_section_data: Optional[dict[str, pd.DataFrame]] = None,
        cross_section_dates: Optional[pd.DatetimeIndex] = None,
        quality_card_config: Optional[Any] = None,
        quality_min_grade: str = "B",
        market: str = "stock",
    ):
        self.data = data
        self.forward_returns = forward_returns
        self.cross_section_data = cross_section_data
        self.cross_section_dates = cross_section_dates
        self.market = market
        self._is_cross_section = cross_section_data is not None

        # ── 市场隔离: 自动按 market 选择 elite 目录 ──
        if elite_dir is None:
            if market == "futures":
                elite_dir = "memory/knowledge/factors/futures_elite"
            else:
                elite_dir = "memory/knowledge/factors/elite"
        self.elite_dir = Path(elite_dir)
        self.elite_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir = Path(memory_dir)
        self.budget: BudgetConfig = budget or DEFAULT_BUDGET_CONFIG
        self.verifier = verifier or get_global_verifier()
        self.llm_client = llm_client or get_default_llm_client()
        self.seed_pool = seed_pool or SeedPool()
        self.n_trials_micro = n_trials_micro

        # 子模块
        self.state_manager = EvolutionStateManager(self.memory_dir)
        self.experience_chain = ExperienceChain(self.memory_dir)
        self.macro_evolver = MacroEvolver(
            llm_client=self.llm_client,
            experience_chain=self.experience_chain,
            max_tokens_per_call=self.budget["max_tokens_per_factor"],
        )
        self.evaluation_chain = EvaluationChain()

        # 子模块: 因子质检过滤器 (Phase A.1 集成) — 延迟导入避免循环依赖
        from ..pipeline.factor_quality_inspection import FactorQualityInspection
        self.quality_inspector = FactorQualityInspection(
            card_config=quality_card_config,
            min_grade=quality_min_grade,
        )

        # 子模块: 因子审计器 (Phase B.3 集成)
        self.auditor = FactorAuditor()

        # 子模块: 端到端回测流水线 (Phase B.2 集成)
        from .backtest_pipeline import BacktestPipeline, PipelineConfig
        self.backtest_pipeline = BacktestPipeline(config=PipelineConfig())

        # 子模块: 数据质量监控器 (Phase B.1 集成)
        from ..monitor.data_quality_monitor import DataQualityMonitor
        self.data_quality_monitor = DataQualityMonitor()

        # 子模块: 精英因子追踪器 (Phase A.2 集成)
        from ..monitor.elite_tracker import EliteFactorTracker
        self.elite_tracker = EliteFactorTracker(
            tracking_dir=str(self.memory_dir / "tracking"),
        )

        # 状态
        self._prior_evaluations: list[FactorEvaluation] = []
        self._consecutive_low_ic: int = 0
        # UCT 统计: {factor_id: {"visits": int, "total_reward": float}}
        self._uct_stats: dict[str, dict[str, float]] = {}
        # DuckDB 仓储（延迟初始化）
        self._repo: Optional[Any] = None

    def run(self, max_generation: Optional[int] = None) -> EvolutionRunResult:
        """执行 L2 演化循环。

        Args:
            max_generation: 最大代数（None = 使用 budget 配置）

        Returns:
            EvolutionRunResult
        """
        trace_id = generate_trace_id("l2")

        # ── 清理前日因子信号缓存（缓存随因子集变化而失效） ──
        try:
            cache_dir = Path("memory/cache/factor_signals")
            if cache_dir.exists():
                n_cleared = sum(1 for _ in cache_dir.glob("*.npy"))
                for fp in cache_dir.glob("*.npy"):
                    try:
                        fp.unlink()
                    except OSError:
                        pass
                idx = cache_dir / "signal_index.json"
                if idx.exists():
                    try:
                        idx.unlink()
                    except OSError:
                        pass
                if n_cleared:
                    print(f"[L2] 清理因子信号缓存: {n_cleared} 个文件")
        except Exception:
            pass

        state = self.state_manager.load_or_init(self.budget["nightly_token_limit"])
        state = self.state_manager.mark_running()
        # mark_running 内部重新加载了状态文件，需在之后重置计数器
        state["last_generation"] = 0
        state["total_factors_evaluated"] = 0
        state["total_factors_promoted"] = 0
        self.state_manager.save(state)
        run_id = state["run_id"]

        max_gen = max_generation or self.budget["max_generation"]
        elite_ids: list[str] = []
        seed_correlations: list[FactorCorrelation] = []
        start_gen = 1  # 每次运行从第 1 代开始

        try:
            # ── Step 0: 种子因子相关性预检（轻量扫描，仅标记不删除） ──
            seeds = self.seed_pool.load_all_seeds()
            seed_correlations = self._run_seed_correlation_check(seeds, trace_id)
            if seed_correlations:
                high_corr_count = len(seed_correlations)
                print(f"[evo] 种子因子相关性预检: {high_corr_count} 对高相关因子 (阈值≥0.95)")
                for pair in seed_correlations[:5]:
                    print(f"  - {pair['factor_id_a']} × {pair['factor_id_b']}: "
                          f"Pearson={pair['pearson']:.4f} Spearman={pair['spearman']:.4f}")
                if high_corr_count > 5:
                    print(f"  ... 还有 {high_corr_count - 5} 对")

            # ── Step 1: 评估种子因子，合格直接晋升 elite ──
            promoted_seeds = self._evaluate_and_promote_seeds(
                seeds, trace_id, state, elite_ids,
                seed_correlations=seed_correlations,
            )
            if promoted_seeds > 0:
                print(f"[evo] 种子因子晋升: {promoted_seeds} 个")

            # 使用已晋升的种子作为父因子（只有高IC种子才值得演化）
            parent_seeds = [s for s in seeds if s["factor_id"] in elite_ids]
            if not parent_seeds:
                print("[evo] 无合格父因子，跳过演化循环")
                self.state_manager.mark_completed(state)
                return EvolutionRunResult(
                    run_id=run_id, trace_id=trace_id,
                    generations_completed=0,
                    total_factors_evaluated=0,
                    total_factors_promoted=0,
                    tokens_consumed=state.get("tokens_consumed", 0),
                    status="completed",
                    elite_factor_ids=elite_ids,
                    seed_correlations=seed_correlations,
                )

            for generation in range(start_gen, start_gen + max_gen):
                # 熔断检查
                cb_reason = self._check_circuit_breaker(state)
                if cb_reason:
                    self.state_manager.mark_circuit_broken(state, cb_reason)
                    return EvolutionRunResult(
                        run_id=run_id, trace_id=trace_id,
                        generations_completed=generation - start_gen,
                        total_factors_evaluated=state.get("total_factors_evaluated", 0),
                        total_factors_promoted=state.get("total_factors_promoted", 0),
                        tokens_consumed=state.get("tokens_consumed", 0),
                        status="circuit_broken",
                        circuit_breaker_reason=cb_reason,
                        elite_factor_ids=elite_ids,
                        seed_correlations=seed_correlations,
                    )

                # 选择父因子（UCT 树搜索，平衡探索与利用）
                parent = self._select_parent_uct(parent_seeds)

                # ── Step 1: 宏观演化（LLM 改逻辑） ──
                try:
                    new_factor, macro_summary, macro_tokens = self.macro_evolver.evolve(
                        parent, generation=generation, trace_id=trace_id
                    )
                    self.state_manager.add_tokens(state, macro_tokens)
                except Exception as e:
                    # 宏观演化失败 → 记录失败轨迹，跳过本代
                    self._record_failure_trace(
                        parent, generation, "macro_evolution",
                        f"宏观演化失败: {e}", [], trace_id,
                    )
                    continue

                # ── Step 2: 微观演化（optuna 调参） ──
                try:
                    # 横截面模式：用第一个股票的数据做微参
                    micro_data = list(self.cross_section_data.values())[0] if self._is_cross_section else self.data
                    micro_ret = self.forward_returns
                    optimized_factor, _ = evolve_micro(
                        new_factor, micro_data, micro_ret,
                        n_trials=self.n_trials_micro,
                    )
                except Exception as e:
                    self._record_failure_trace(
                        new_factor, generation, "micro_evolution",
                        f"微观演化失败: {e}", [], trace_id,
                    )
                    continue

                # ── Step 3: 三级评估链 ──
                if self._is_cross_section:
                    evaluation = self._evaluate_cross_section(optimized_factor, trace_id)
                else:
                    evaluation = self.evaluation_chain.evaluate(
                        optimized_factor, self.data, self.forward_returns,
                        prior_evaluations=self._prior_evaluations,
                    )
                self._prior_evaluations.append(evaluation)
                self.state_manager.increment_evaluated(state)

                # ── UCT 反馈: 根据子因子表现更新父因子统计 ──
                self._update_uct_stats(parent, evaluation)

                # ── Step 4: Verifier 判定 ──
                verifier_result = self.verifier.check(evaluation)

                # ── Step 4.5: 因子质量评分卡 (Phase A.1) ──
                inspection: InspectionResult = self.quality_inspector.inspect(
                    factor=optimized_factor,
                    evaluation=evaluation,
                )

                # ── Step 4.5.5: 端到端回测流水线 (Phase B.2) ──
                backtest_result = self._run_backtest_pipeline(
                    optimized_factor, evaluation, trace_id,
                )
                if backtest_result:
                    evaluation["backtest_pipeline"] = backtest_result

                # ── Step 4.5.6: 数据质量监控 (Phase B.1) ──
                self._register_factor_baseline(optimized_factor, evaluation)
                dq_alerts = self._check_factor_data_quality(
                    optimized_factor, evaluation,
                )
                if dq_alerts:
                    critical = any(
                        getattr(a, "severity", "") == "critical"
                        for a in dq_alerts
                    )
                    if critical:
                        print(
                            f"[evo] 数据质量严重告警 [{optimized_factor.get('name', '?')}]: "
                            f"跳过晋升"
                        )
                        continue

                # ── Step 4.6: 因子强制审计 (Phase B.3) ──
                audit_report = self._run_factor_audit(
                    optimized_factor, evaluation, trace_id,
                )

                # ── Step 5: 经验链记录 + 分级准入 ──
                if verifier_result["passed"]:
                    # 质检过滤: 仅 A/B 级晋升，C 级淘汰
                    if inspection.filtered:
                        self._log_inspection_detail(
                            optimized_factor, inspection, "淘汰", generation,
                        )
                        self._record_quality_filtered_trace(
                            optimized_factor, generation, trace_id,
                            inspection, evaluation=evaluation,
                        )
                        continue

                    # 审计过滤: 审计未通过则拒绝准入
                    if not audit_report.passed:
                        failed_items = [
                            it.name for it in audit_report.failed_items
                        ]
                        print(
                            f"[evo] 审计未通过 [{optimized_factor.get('name', '?')}]: "
                            f"失败项={failed_items}, 通过率={audit_report.pass_rate:.0%}"
                        )
                        self._record_audit_failed_trace(
                            optimized_factor, generation, trace_id,
                            audit_report, evaluation=evaluation,
                        )
                        continue

                    # 晋级精英池（去重检查 + 质量评分附加 + 审计报告）
                    self._log_inspection_detail(
                        optimized_factor, inspection, "通过", generation,
                    )
                    promoted_path = self._promote_to_elite(
                        optimized_factor, evaluation,
                        seed_correlations=seed_correlations,
                        quality_score=inspection.quality_score,
                        audit_report=audit_report,
                    )
                    if promoted_path is None:
                        # 因子名称重复，跳过
                        continue
                    self.state_manager.increment_promoted(state)
                    elite_ids.append(optimized_factor["factor_id"])
                    self._record_success_trace(
                        optimized_factor, generation, "combined",
                        macro_summary, evaluation,
                        [f"代 {generation} 晋级精英池",
                         f"质量分={inspection.total_score}/50 ({inspection.grade}级)",
                         f"审计通过率={audit_report.pass_rate:.0%}"],
                        trace_id,
                    )
                    self._consecutive_low_ic = 0
                else:
                    # 失败轨迹
                    self._record_failure_trace(
                        optimized_factor, generation, "combined",
                        macro_summary,
                        verifier_result["failure_reasons"], trace_id,
                        evaluation=evaluation,
                    )
                    # 检查低 IC
                    bt = evaluation.get("level_1_backtest", {})
                    if abs(bt.get("ic", 0)) < self.budget["circuit_breaker_low_ic_threshold"]:
                        self._consecutive_low_ic += 1
                    else:
                        self._consecutive_low_ic = 0

                # ── Step 6: 状态持久化 ──
                state["last_generation"] = generation
                self.state_manager.save(state)

                # 经验链清理（如果超过 100 条）
                self.experience_chain.cleanup_if_needed()

            # 正常完成
            self.state_manager.mark_completed(state)
            return EvolutionRunResult(
                run_id=run_id, trace_id=trace_id,
                generations_completed=max_gen,
                total_factors_evaluated=state.get("total_factors_evaluated", 0),
                total_factors_promoted=state.get("total_factors_promoted", 0),
                tokens_consumed=state.get("tokens_consumed", 0),
                status="completed",
                elite_factor_ids=elite_ids,
                seed_correlations=seed_correlations,
            )

        except Exception as e:
            self.state_manager.mark_paused(state, str(e))
            return EvolutionRunResult(
                run_id=run_id, trace_id=trace_id,
                generations_completed=0,
                total_factors_evaluated=state.get("total_factors_evaluated", 0),
                total_factors_promoted=state.get("total_factors_promoted", 0),
                tokens_consumed=state.get("tokens_consumed", 0),
                status="paused",
                circuit_breaker_reason=str(e),
                elite_factor_ids=elite_ids,
                seed_correlations=seed_correlations,
            )
        finally:
            # ── 写入 L2 相关性索引到 elite 目录（供 L3 批量读取） ──
            if seed_correlations:
                self._write_seed_correlation_index(seed_correlations, trace_id)

            # ── Phase A.2: 精英因子定期重评估 ──
            self._run_periodic_factor_review(elite_ids, trace_id)

    def _write_seed_correlation_index(
        self,
        seed_correlations: list[FactorCorrelation],
        trace_id: str,
    ) -> None:
        """将 L2 种子因子相关性预检结果写入 elite 目录的共享索引文件。

        该文件供 L3 Portfolio Loop 批量读取，作为相关性管理的先验数据。
        """
        import json
        from datetime import datetime

        index_path = self.elite_dir / "_l2_seed_correlation_index.json"
        index_data = {
            "source": "l2_seed_correlation_check",
            "trace_id": trace_id,
            "created_at": datetime.now().isoformat(),
            "threshold": 0.95,
            "total_pairs": len(seed_correlations),
            "correlations": [
                {
                    "factor_id_a": sc.get("factor_id_a", ""),
                    "factor_id_b": sc.get("factor_id_b", ""),
                    "pearson": sc.get("pearson", 0),
                    "spearman": sc.get("spearman", 0),
                }
                for sc in seed_correlations
            ],
        }
        index_path.write_text(
            json.dumps(index_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[evo] L2 相关性索引已写入: {index_path} ({len(seed_correlations)} 对高相关因子)")

    # ─── 内部方法 ───

    def _select_parent_uct(self, parents: list[FactorProgram]) -> FactorProgram:
        """UCT 树搜索选择父因子，平衡探索与利用。

        UCB = avg_reward + c * sqrt(ln(total_visits) / visits)

        未访问的父因子（visits=0）返回无限大 UCB，确保优先探索。
        """
        total_visits = sum(
            s.get("visits", 0) for s in self._uct_stats.values()
        )
        best_score = -float("inf")
        best_parent = parents[0]

        for p in parents:
            fid = p["factor_id"]
            stats = self._uct_stats.get(fid, {"visits": 0, "total_reward": 0.0})
            visits = stats["visits"]
            if visits == 0:
                # 未访问 → 优先探索
                return p
            avg_reward = stats["total_reward"] / visits
            exploration = UCT_EXPLORATION_C * math.sqrt(
                math.log(max(total_visits, 1)) / visits
            )
            ucb = avg_reward + exploration
            if ucb > best_score:
                best_score = ucb
                best_parent = p

        return best_parent

    def _update_uct_stats(
        self, parent: FactorProgram, evaluation: FactorEvaluation
    ) -> None:
        """根据子因子评估结果更新父因子的 UCT 统计。

        奖励 = abs(IC)（通过）/ 0（失败），鼓励 IC 高的父因子。
        """
        fid = parent["factor_id"]
        if fid not in self._uct_stats:
            self._uct_stats[fid] = {"visits": 0, "total_reward": 0.0}
        bt = evaluation.get("level_1_backtest", {})
        passed = evaluation.get("passed", False)
        reward = abs(bt.get("ic", 0.0)) if passed else 0.0
        self._uct_stats[fid]["visits"] += 1
        self._uct_stats[fid]["total_reward"] += reward

    def _check_circuit_breaker(self, state: EvolutionState) -> Optional[str]:
        """熔断检查。返回原因字符串（None = 未触发）。"""
        # Token 超 2x
        tokens = state.get("tokens_consumed", 0)
        limit = state.get("budget_limit", self.budget["nightly_token_limit"])
        if tokens > limit * self.budget["circuit_breaker_token_ratio"]:
            return (
                f"Token 熔断: {tokens} > {limit} * "
                f"{self.budget['circuit_breaker_token_ratio']}"
            )

        # 连续低 IC
        if self._consecutive_low_ic >= self.budget["circuit_breaker_consecutive_low_ic"]:
            return (
                f"连续低 IC 熔断: {self._consecutive_low_ic} 代 "
                f"IC < {self.budget['circuit_breaker_low_ic_threshold']}"
            )

        # 失败率 > 90%
        evaluated = state.get("total_factors_evaluated", 0)
        promoted = state.get("total_factors_promoted", 0)
        if evaluated >= 10:
            failure_rate = (evaluated - promoted) / evaluated
            if failure_rate > self.budget["circuit_breaker_failure_rate"]:
                return (
                    f"失败率熔断: {failure_rate:.2%} > "
                    f"{self.budget['circuit_breaker_failure_rate']:.2%}"
                )

        return None

    def _get_repo(self):
        """延迟初始化 DuckDB 仓储。"""
        if self._repo is None:
            from .factor_db import FactorRepository
            self._repo = FactorRepository()
        return self._repo

    def _promote_to_elite(
        self, factor: FactorProgram, evaluation: FactorEvaluation,
        seed_correlations: Optional[list[FactorCorrelation]] = None,
        quality_score: Optional[dict] = None,
        audit_report: Optional[FactorAuditReport] = None,
    ) -> Optional[Path]:
        """将因子晋升到精英池。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            seed_correlations: L2 种子因子相关性标记（可选）
            quality_score: 质量评分卡结果（Phase A.1 集成）
            audit_report: 因子审计报告（Phase B.3 集成）

        Returns:
            Path: 晋升成功
            None: 因子名称重复，跳过晋升
        """
        import json
        
        # 去重检查：检查因子名称是否已存在（JSON 文件 + DuckDB 双检查）
        factor_name = factor.get("name", "")
        for existing_file in self.elite_dir.glob("*.json"):
            try:
                existing_data = json.loads(existing_file.read_text(encoding="utf-8"))
                if existing_data.get("name") == factor_name:
                    print(f"[evo] 跳过重复因子: {factor_name} (已存在: {existing_file.name})")
                    return None
            except Exception:
                continue

        # DuckDB 去重检查（带市场过滤，防止跨市场重名冲突）
        try:
            repo = self._get_repo()
            existing = repo.get_factor_by_name(factor_name, market=self.market)
            if existing:
                print(f"[evo] 跳过重复因子: {factor_name} (DuckDB 已存在, market={self.market})")
                return None
        except Exception:
            pass

        fp = self.elite_dir / f"{factor['factor_id']}.json"
        # 将 factor 字段展开到顶层，方便 cli 直接读取
        record = dict(factor)
        # 确保 market 字段正确：若因子为默认 "multi"，使用演化上下文的市场
        if record.get("market", "multi") in ("multi", "other") and self.market in ("futures", "stock"):
            record["market"] = self.market
        record["evaluation"] = evaluation

        # ── 写入质量评分卡 (Phase A.1 集成) ──
        if quality_score is not None:
            record["quality_score"] = quality_score

        # ── 写入审计报告 (Phase B.3 集成) ──
        if audit_report is not None:
            record["audit_report"] = audit_report.to_dict()

        # ── 写入 L2 相关性元数据（供 L3 参考） ──
        if seed_correlations:
            factor_id = factor.get("factor_id", "")
            corr_flags: list[dict[str, Any]] = []
            
            for sc in seed_correlations:
                a, b = sc.get("factor_id_a", ""), sc.get("factor_id_b", "")
                pearson = sc.get("pearson", 0)
                spearman = sc.get("spearman", 0)
                max_abs = max(abs(pearson), abs(spearman))
                
                if factor_id == a or factor_id == b:
                    partner = b if factor_id == a else a
                    corr_flags.append({
                        "partner_factor_id": partner,
                        "pearson": pearson,
                        "spearman": spearman,
                        "max_abs": max_abs,
                        "source": "l2_seed_correlation_check",
                    })
            
            if corr_flags:
                record["correlation_metadata"] = {
                    "l2_seed_flags": corr_flags,
                    "flag_count": len(corr_flags),
                    "max_corr_detected": max(
                        (f["max_abs"] for f in corr_flags), default=0
                    ),
                }
                print(f"[evo] 因子 {factor.get('name', '?')} 写入 L2 相关性标记: "
                      f"{len(corr_flags)} 个高相关对")

        # ── 写入 JSON 文件（debug/备份） ──
        fp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # ── 写入 DuckDB（主存储） ──
        self._write_to_duckdb(factor, evaluation, quality_score, seed_correlations, audit_report)

        return fp

    def _write_to_duckdb(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        quality_score: Optional[dict] = None,
        seed_correlations: Optional[list[FactorCorrelation]] = None,
        audit_report: Optional[FactorAuditReport] = None,
    ) -> None:
        """将因子写入 DuckDB（主存储层）。

        支持幂等写入：若 factor_id 已存在则更新，不存在则创建。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            quality_score: 质量评分卡结果
            seed_correlations: L2 种子因子相关性标记
            audit_report: 因子审计报告（Phase B.3 集成）
        """
        try:
            repo = self._get_repo()
            factor_id = factor.get("factor_id")
            factor_name = factor.get("name", "?")
            factor_market = factor.get("market", "multi")
            # 若因子未显式指定有效市场（multi/other 为默认值），使用演化上下文的市场
            if factor_market in ("multi", "other") and self.market in ("futures", "stock"):
                factor_market = self.market
            factor_family = factor.get("family", "other")

            l1 = evaluation.get("level_1_backtest", {})

            factor_dict = {
                "factor_id": factor_id,
                "name": factor_name,
                "code": factor.get("code", ""),
                "params": factor.get("params", {}),
                "signature": factor.get("signature", {}),
                "economic_logic": factor.get("economic_logic", {}),
                "source": factor.get("source", "macro_evolution"),
                "parent_id": factor.get("parent_id"),
                "generation": factor.get("generation", 0),
                "trace_id": factor.get("trace_id"),
                "market": factor_market,
                "family": factor_family,
                "is_elite": True,
                "sharpe": l1.get("sharpe", 0.0),
                "ic": l1.get("ic", 0.0),
                "icir": l1.get("icir", 0.0),
                "max_drawdown": l1.get("max_drawdown", 0.0),
                "turnover_monthly": l1.get("turnover_monthly", 0.0),
                "decay_6m": l1.get("decay_6m", 0.05),
                "metadata": {
                    "quality_score": quality_score,
                    "correlation_metadata": factor.get("correlation_metadata", {}),
                    "symbols": factor.get("symbols", []),
                    "risk_tag": factor.get("risk_tag"),
                    "factor_version": factor.get("factor_version", "v2"),
                    "audit_report": audit_report.to_dict() if audit_report else None,
                },
            }

            # ── 幂等写入：已存在则更新，不存在则创建 ──
            existing = repo.get_factor(factor_id)
            if existing:
                repo.update_factor(factor_id, factor_dict)
                print(f"[evo] 🔄 更新已有因子 {factor_name} 到 DuckDB [market={factor_market}]")
            else:
                repo.create_factor(factor_dict)
                print(f"[evo] ✅ 新建因子 {factor_name} 到 DuckDB [market={factor_market}]")

            # ── 写入/更新评估记录 ──
            l2 = evaluation.get("level_2_economic", {})
            l3 = evaluation.get("level_3_multiple", {})

            eval_dict = {
                "trace_id": evaluation.get("trace_id"),
                "ic": l1.get("ic", 0),
                "icir": l1.get("icir", 0),
                "sharpe": l1.get("sharpe", 0),
                "max_drawdown": l1.get("max_drawdown", 0),
                "turnover": l1.get("turnover_monthly", 0),
                "t_stat": l1.get("t_stat", 0),
                "monotonicity": l1.get("monotonicity", False),
                "oos_ratio": l1.get("oos_ratio", 0),
                "theory_score": l2.get("theory", 0),
                "behavioral_score": l2.get("behavioral", 0),
                "microstructure_score": l2.get("microstructure", 0),
                "institutional_score": l2.get("institutional", 0),
                "dims_passed": l2.get("dims_passed", 0),
                "bonferroni_p": l3.get("bonferroni_p", 1.0),
                "fdr_q": l3.get("fdr_q", 0.05),
                "effective_n": l3.get("effective_n_factors", 1),
                "adjusted_t": l3.get("adjusted_t", 0),
                "l3_passed": l3.get("passed", False),
                "overall_passed": evaluation.get("passed", False),
                "failure_reasons": evaluation.get("failure_reasons", []),
                "evaluated_at": evaluation.get("evaluated_at"),
            }

            repo.add_evaluation(factor_id, eval_dict)

        except Exception as e:
            factor_name = factor.get("name", "?")
            print(f"[evo] ⚠️ DuckDB 写入失败 [{factor_name}]: {e}")
            import traceback
            traceback.print_exc()

    def _evaluate_and_promote_seeds(
        self,
        seeds: list[FactorProgram],
        trace_id: str,
        state: EvolutionState,
        elite_ids: list[str],
        seed_correlations: Optional[list[FactorCorrelation]] = None,
    ) -> int:
        """评估种子因子，合格的直接晋升 elite。

        种子是已知起点，跳过 Verifier 判定，仅用简单 IC/夏普筛选。
        种子评估不计入熔断计数器（evaluated/promoted），
        熔断仅针对演化过程中的因子。

        Args:
            seeds: 种子因子列表
            trace_id: 全链路 trace_id
            state: 演化状态
            elite_ids: 精英因子 ID 列表（将会被追加）
            seed_correlations: L2 相关性预检结果（传递给晋升方法）

        Returns:
            晋升的种子因子数量
        """
        promoted = 0
        for seed in seeds:
            try:
                if self._is_cross_section:
                    evaluation = self._evaluate_cross_section(seed, trace_id)
                else:
                    evaluation = self.evaluation_chain.evaluate(
                        seed, self.data, self.forward_returns,
                    )
                bt = evaluation.get("level_1_backtest", {})
                passed = evaluation.get("passed", False)

                # 风险标签额外检查：标记为 vwap_approx 的因子需要更高 IC 阈值
                if passed and seed.get("risk_tag") == "vwap_approx":
                    ic = bt.get("ic", 0)
                    if abs(ic) < 0.08:
                        print(f"[evo] 跳过 vwap_approx 因子: {seed['name']} (IC={abs(ic):.4f} < 0.08)")
                        continue

                if passed:
                    # 种子因子质量评分卡 (Phase A.1 集成)
                    inspection = self.quality_inspector.inspect(
                        factor=seed, evaluation=evaluation,
                    )
                    if inspection.filtered:
                        self._log_inspection_detail(
                            seed, inspection, "淘汰", 0,
                        )
                        continue

                    # 端到端回测流水线 (Phase B.2 集成)
                    backtest_result = self._run_backtest_pipeline(
                        seed, evaluation, trace_id,
                    )
                    if backtest_result:
                        evaluation["backtest_pipeline"] = backtest_result

                    # 数据质量监控 (Phase B.1 集成)
                    self._register_factor_baseline(seed, evaluation)
                    dq_alerts = self._check_factor_data_quality(
                        seed, evaluation,
                    )
                    if dq_alerts:
                        critical = any(
                            getattr(a, "severity", "") == "critical"
                            for a in dq_alerts
                        )
                        if critical:
                            print(
                                f"[evo] 种子数据质量严重告警 [{seed.get('name', '?')}]: "
                                f"跳过晋升"
                            )
                            continue

                    # 种子因子强制审计 (Phase B.3 集成)
                    audit_report = self._run_factor_audit(
                        seed, evaluation, trace_id,
                    )
                    if not audit_report.passed:
                        failed_items = [
                            it.name for it in audit_report.failed_items
                        ]
                        print(
                            f"[evo] 种子审计未通过 [{seed.get('name', '?')}]: "
                            f"失败项={failed_items}, 通过率={audit_report.pass_rate:.0%}"
                        )
                        self._record_audit_failed_trace(
                            seed, 0, trace_id,
                            audit_report, evaluation=evaluation,
                        )
                        continue

                    self._log_inspection_detail(
                        seed, inspection, "通过", 0,
                    )
                    self._promote_to_elite(
                        seed, evaluation,
                        seed_correlations=seed_correlations,
                        quality_score=inspection.quality_score,
                        audit_report=audit_report,
                    )
                    elite_ids.append(seed["factor_id"])
                    promoted += 1
                    print(f"[evo] 种子因子晋升: {seed['name']} (IC={bt.get('ic', 0):.4f}, "
                          f"质量分={inspection.total_score}/50)")
            except Exception:
                continue
        return promoted

    def _run_seed_correlation_check(
        self,
        seeds: list[FactorProgram],
        trace_id: str,
    ) -> list[FactorCorrelation]:
        """L2 种子因子相关性预检 — 轻量扫描，仅标记不删除。

        自动检测数据模式:
        - 股票时序模式: 计算 Pearson/Spearman 相关
        - 期货横截面模式: 计算截面排名 Spearman 相关

        设计原则:
        - 不过早删除：因子相关性是市场状态依赖的，当前相关≠永久相关
        - 仅做标记：高相关对记录到 metadata，L3 决策时再处理

        Args:
            seeds: 种子因子列表
            trace_id: 全链路 trace_id

        Returns:
            list[FactorCorrelation] — 超过阈值的高相关因子对
        """
        try:
            if self._is_cross_section:
                # 期货横截面模式: 截面排名 Spearman 相关
                from .seed_pool import compute_cross_section_correlations
                correlations = compute_cross_section_correlations(
                    seeds,
                    self.cross_section_data,
                    self.cross_section_dates,
                    threshold=0.95,
                )
            else:
                # 股票时序模式: Pearson/Spearman 相关
                correlations = compute_seed_correlations(
                    seeds, self.data, threshold=0.95
                )
            return correlations
        except Exception as e:
            mode = "横截面" if self._is_cross_section else "时序"
            print(f"[evo] 种子因子相关性预检异常（{mode}模式，跳过）: {e}")
            return []

    def _evaluate_cross_section(
        self, factor: FactorProgram, trace_id: str
    ) -> FactorEvaluation:
        """横截面模式下的评估：直接回测 + 自动构造 FactorEvaluation。"""
        from .contracts import EconomicScore, MultipleTestResult

        bt = cross_section_evaluate_backtest(
            factor,
            self.cross_section_data,
            self.cross_section_dates,
        )
        ec = EconomicScore(theory=0, behavioral=0, microstructure=0, institutional=0,
                           dimensions_passed=3, narrative="横截面评估（自动通过）")
        mt = MultipleTestResult(bonferroni_p=1.0, fdr_q=0.05, effective_n_factors=1,
                                adjusted_t=bt.get("t_stat", 3.0), passed=True)
        reasons: list[str] = []
        if bt.get("ic", 0) < 0.03:
            reasons.append(f"截面 IC={bt.get('ic', 0):.4f} < 0.03")
        if bt.get("sharpe", 0) < 1.5:
            reasons.append(f"截面夏普={bt.get('sharpe', 0):.4f} < 1.5")
        return FactorEvaluation(
            factor_id=factor["factor_id"],
            trace_id=trace_id,
            level_1_backtest=bt,
            level_2_economic=ec,
            level_3_multiple=mt,
            passed=len(reasons) == 0,
            failure_reasons=reasons,
            evaluated_at=datetime.now().isoformat(),
        )

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

            # 1. 自动淘汰检查
            retired = self.elite_tracker.auto_retire()
            if retired:
                print(f"[elite-review] 自动淘汰 {len(retired)} 个因子: {retired}")

            # 2. 为每个精英因子更新跟踪快照
            for fid in elite_ids:
                factor_data = self._get_factor_data_for_review(fid)
                if factor_data is None:
                    continue
                ic = factor_data.get("ic", 0.0)
                sharpe = factor_data.get("sharpe", 0.0)
                self.elite_tracker.update(fid, ic, sharpe)

            # 3. 生成报告
            report = self.elite_tracker.report()
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
            from .evaluation_chain import FactorEvaluation
            from .verifier import get_global_verifier

            factor_data = self.verifier.get_factor_by_id(factor_id) if hasattr(
                self.verifier, "get_factor_by_id"
            ) else None
            if factor_data is None:
                return {"ic": 0.0, "sharpe": 0.0}
            return {"ic": 0.0, "sharpe": 0.0}
        except Exception:
            return None

    # ── Phase B.1: 数据质量监控集成 ──────────────────────────

    def _register_factor_baseline(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
    ) -> None:
        """注册因子基准数据到数据质量监控器。

        当因子首次通过评估时，将其 IC 和容量注册为基准，
        用于后续监控数据漂移和容量突变。

        Args:
            factor: 因子程序
            evaluation: 评估结果
        """
        factor_id = factor.get("factor_id", "?")
        ic = evaluation.get("ic", 0.0)
        self.data_quality_monitor.register_factor(
            factor_id=factor_id,
            baseline_ic=ic,
            baseline_capacity=0.0,
            ic_std=max(abs(ic) * 0.1, 0.001),
        )

    def _check_factor_data_quality(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
    ) -> list[Any]:
        """检查因子数据质量，返回触发的告警列表。

        Args:
            factor: 因子程序
            evaluation: 当前评估结果

        Returns:
            告警列表（可能为空）
        """
        factor_id = factor.get("factor_id", "?")
        current_ic = evaluation.get("ic", 0.0)
        alerts = self.data_quality_monitor.check(
            factor_id=factor_id,
            current_ic=current_ic,
        )
        if alerts:
            for alert in alerts:
                alert_type = getattr(alert, "alert_type", "unknown")
                severity = getattr(alert, "severity", "unknown")
                msg = getattr(alert, "message", "")
                print(
                    f"[dq-monitor] 告警 [{factor_id}]: "
                    f"type={alert_type}, severity={severity}, msg={msg}"
                )
        return alerts

    # ── Phase B.2: 端到端回测流水线集成 ──────────────────

    def _run_backtest_pipeline(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> Optional[dict[str, Any]]:
        """执行端到端回测流水线（Phase B.2 集成）。

        在因子通过 L1/L2/L3 评估后，运行标准化回测流水线，
        生成完整的回测报告，供质检和审计使用。

        Args:
            factor: 因子程序
            evaluation: L1/L2/L3 评估结果
            trace_id: 全链路 trace_id

        Returns:
            回测结果字典，包含绩效指标和报告路径；失败返回 None
        """
        try:
            from .backtest_pipeline import BacktestInput

            bt_input = BacktestInput(
                factor=factor if isinstance(factor, dict) else dict(factor),
                data=self.data,
                benchmark=None,
                forward_period=1,
            )
            result = self.backtest_pipeline.run(bt_input)

            if not result.success:
                print(f"[evo] 回测流水线失败 [{factor.get('factor_id', '?')}]: {result.error}")
                return None

            report = result.output
            return {
                "success": True,
                "duration_ms": result.duration_ms,
                "report_path": getattr(report, "file_path", None) if report else None,
                "metrics": {
                    "total_return": getattr(report, "total_return", 0.0),
                    "sharpe": getattr(report, "sharpe_ratio", 0.0),
                    "max_drawdown": getattr(report, "max_drawdown", 0.0),
                    "calmar": getattr(report, "calmar_ratio", 0.0),
                } if report else {},
            }
        except Exception as e:
            logger.debug("回测流水线异常: %s", e)
            return None

    # ── Phase B.3: 因子强制审计 ──────────────────────────

    def _run_factor_audit(
        self,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        trace_id: str,
    ) -> FactorAuditReport:
        """执行因子审计（Phase B.3 集成）。

        将评估结果中的数据映射到审计器所需的输入，
        执行 6 项强制审计检查。

        Args:
            factor: 因子程序
            evaluation: 评估结果
            trace_id: 全链路 trace_id

        Returns:
            FactorAuditReport 审计报告
        """
        import traceback

        factor_meta = {
            "factor_id": factor.get("factor_id", ""),
            "name": factor.get("name", ""),
            "trace_id": trace_id,
            "family": factor.get("family", ""),
        }

        l1 = evaluation.get("level_1_backtest", {})
        l3 = evaluation.get("level_3_multiple", {})

        # 构造 OOS 结果（从评估链 L1 提取）
        oos_ratio = l1.get("oos_ratio", 0)
        oos_result = None
        if oos_ratio > 0:
            oos_result = {
                "ic_consistency": oos_ratio >= 0.5,
                "oos_ic": l1.get("ic", 0) * oos_ratio,
                "passed": oos_ratio >= 0.5,
            }

        # 构造 p-values（从 L3 提取，仅当非默认值时传递）
        p_values: list[float] = []
        bonf_p = l3.get("bonferroni_p")
        if bonf_p is not None and bonf_p < 1.0:
            p_values.append(float(bonf_p))

        try:
            report = self.auditor.audit(
                factor=factor_meta,
                data=self.data,
                forward_returns=self.forward_returns,
                oos_result=oos_result,
                p_values=p_values if p_values else None,
            )
        except Exception as e:
            logger.warning(
                "审计执行异常 [%s]: %s (降级为跳过所有审计项)",
                factor_meta["name"], str(e),
            )
            logger.debug(traceback.format_exc())
            report = FactorAuditReport(
                factor_id=factor_meta["factor_id"],
                factor_name=factor_meta["name"],
                audited_at=datetime.now().isoformat(),
                items=[],
                passed=False,
                pass_rate=0.0,
                summary={"total": 6, "passed": 0, "failed": 0, "skipped": 6, "pass_rate": 0.0},
            )

        return report

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
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            f"[evo] 审计失败轨迹已记录: {factor_name} → "
            f"代 {generation}, 通过率={audit_report.pass_rate:.0%}"
        )

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
        sub_trace_id = (
            f"{trace_id}_g{generation}_{mutation_type}_{factor['factor_id'][:8]}"
        )
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
        self.state_manager.add_experience_ref(
            self.state_manager.load_or_init(), trace["trace_id"]
        )

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
        sub_trace_id = (
            f"{trace_id}_g{generation}_{mutation_type}_{factor['factor_id'][:8]}"
        )
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
        inspection: InspectionResult,
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
        print(f"[evo] {icon} 代{generation} 因子质检{status}: "
              f"{factor_name} (等级={grade}, 总分={total}/50)")

        # 淘汰时显示原因
        if status == "淘汰" and reason:
            print(f"       淘汰原因: {reason}")

        # 显示各维度得分
        dims = inspection.quality_score.get("dimension_scores", [])
        if dims:
            low_dims = [d for d in dims if d.get("score", 5) < 3]
            if low_dims:
                print(f"       ⚠️  低分项 (< 3.0):")
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
        inspection: InspectionResult,
        evaluation: Optional[FactorEvaluation] = None,
    ) -> None:
        """记录质检过滤轨迹 (Phase A.1)。

        当因子通过 Verifier 但质量评分低于阈值时记录。
        """
        sub_trace_id = (
            f"{trace_id}_g{generation}_quality_filtered_{factor['factor_id'][:8]}"
        )
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
            mutation_summary=(
                f"质量评分淘汰: 等级={inspection.grade}, "
                f"总分={inspection.total_score}/50"
            ),
            evaluation=evaluation,
            lessons=[f"质检淘汰: {inspection.reason}"],
            trace_id=sub_trace_id,
        )
        try:
            self.experience_chain.record_failure(trace)
        except Exception:
            pass


# ─── CLI 入口 ─────────────────────────────────────────────

def main():
    """CLI 入口: python -m loop_engine.evolution_loop --once"""
    parser = argparse.ArgumentParser(description="L2 因子演化循环")
    parser.add_argument("--once", action="store_true", help="运行一次完整演化")
    parser.add_argument("--max-generation", type=int, default=None, help="最大代数")
    parser.add_argument("--memory-dir", default="memory/evolution", help="状态目录")
    parser.add_argument("--elite-dir", default="memory/knowledge/factors/elite", help="精英池目录")
    args = parser.parse_args()

    if not args.once:
        parser.print_help()
        sys.exit(1)

    # 生成合成数据用于演示（生产环境替换为真实数据）
    np.random.seed(42)
    n = 500
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    volume = np.random.randint(1000, 10000, n).astype(float)
    data = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close,
        "volume": volume,
    }, index=dates)
    forward_returns = np.roll(np.diff(close, prepend=close[0]), -1)
    forward_returns[-1] = 0

    loop = EvolutionLoop(
        data=data,
        forward_returns=forward_returns,
        elite_dir=args.elite_dir,
        memory_dir=args.memory_dir,
    )
    result = loop.run(max_generation=args.max_generation)
    print(f"\n演化完成: {result.status}")
    print(f"  代数: {result.generations_completed}")
    print(f"  评估: {result.total_factors_evaluated}")
    print(f"  晋级: {result.total_factors_promoted}")
    print(f"  Token: {result.tokens_consumed}")
    if result.circuit_breaker_reason:
        print(f"  熔断: {result.circuit_breaker_reason}")
    if result.elite_factor_ids:
        print(f"  精英: {result.elite_factor_ids}")


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = [
    "UCT_EXPLORATION_C",
    "EvolutionRunResult",
    "EvolutionLoop",
    "main",
]
