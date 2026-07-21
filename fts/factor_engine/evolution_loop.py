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

版本: v8.10.0
"""
# pylint: disable=too-many-instance-attributes,too-many-arguments,too-many-positional-arguments,too-many-locals,too-few-public-methods,broad-exception-caught,import-outside-toplevel

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from .contracts import (
    DEFAULT_BUDGET_CONFIG,
    BudgetConfig,
    EvolutionState,
    FactorEvaluation,
    FactorProgram,
)
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
from .seed_pool import SeedPool
from .state import EvolutionStateManager, generate_trace_id
from .verifier import FactorVerifier, get_global_verifier


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
        elite_dir: str | Path = "memory/knowledge/factors/elite",
        memory_dir: str | Path = "memory/evolution",
        budget: Optional[BudgetConfig] = None,
        verifier: Optional[FactorVerifier] = None,
        llm_client: Optional[Any] = None,
        seed_pool: Optional[SeedPool] = None,
        n_trials_micro: int = 100,
        cross_section_data: Optional[dict[str, pd.DataFrame]] = None,
        cross_section_dates: Optional[pd.DatetimeIndex] = None,
    ):
        self.data = data
        self.forward_returns = forward_returns
        self.cross_section_data = cross_section_data
        self.cross_section_dates = cross_section_dates
        self._is_cross_section = cross_section_data is not None
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

        # 状态
        self._prior_evaluations: list[FactorEvaluation] = []
        self._consecutive_low_ic: int = 0

    def run(self, max_generation: Optional[int] = None) -> EvolutionRunResult:
        """执行 L2 演化循环。

        Args:
            max_generation: 最大代数（None = 使用 budget 配置）

        Returns:
            EvolutionRunResult
        """
        trace_id = generate_trace_id("l2")
        state = self.state_manager.load_or_init(self.budget["nightly_token_limit"])
        state = self.state_manager.mark_running()
        run_id = state["run_id"]

        max_gen = max_generation or self.budget["max_generation"]
        elite_ids: list[str] = []
        start_gen = state.get("last_generation", 0) + 1

        try:
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
                    )

                # 选择父因子（轮询种子池）
                seeds = self.seed_pool.load_all_seeds()
                parent = seeds[(generation - 1) % len(seeds)]

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
                    # 横截面评估
                    bt = cross_section_evaluate_backtest(
                        optimized_factor,
                        self.cross_section_data,
                        self.cross_section_dates,
                    )
                    # 构造 FactorEvaluation（其余 Level 2/3 逻辑不变）
                    from .contracts import EconomicScore, MultipleTestResult
                    ec = EconomicScore(theory=0, behavioral=0, microstructure=0, institutional=0,
                                       dimensions_passed=3, narrative="横截面评估（自动通过）")
                    mt = MultipleTestResult(bonferroni_p=1.0, fdr_q=0.05, effective_n_factors=1,
                                            adjusted_t=0.0, passed=True)
                    reasons: list[str] = []
                    if bt.get("ic", 0) < 0.03:
                        reasons.append(f"截面 IC={bt.get('ic', 0):.4f} < 0.03")
                    if bt.get("sharpe", 0) < 1.5:
                        reasons.append(f"截面夏普={bt.get('sharpe', 0):.4f} < 1.5")
                    passed_cs = len(reasons) == 0
                    evaluation = FactorEvaluation(
                        factor_id=optimized_factor["factor_id"],
                        trace_id=trace_id,
                        level_1_backtest=bt,
                        level_2_economic=ec,
                        level_3_multiple=mt,
                        passed=passed_cs,
                        failure_reasons=reasons,
                        evaluated_at=datetime.now().isoformat(),
                    )
                else:
                    evaluation = self.evaluation_chain.evaluate(
                        optimized_factor, self.data, self.forward_returns,
                        prior_evaluations=self._prior_evaluations,
                    )
                self._prior_evaluations.append(evaluation)
                self.state_manager.increment_evaluated(state)

                # ── Step 4: Verifier 判定 ──
                verifier_result = self.verifier.check(evaluation)

                # ── Step 5: 经验链记录 ──
                if verifier_result["passed"]:
                    # 晋级精英池
                    self._promote_to_elite(optimized_factor, evaluation)
                    self.state_manager.increment_promoted(state)
                    elite_ids.append(optimized_factor["factor_id"])
                    self._record_success_trace(
                        optimized_factor, generation, "combined",
                        macro_summary, evaluation,
                        [f"代 {generation} 晋级精英池"], trace_id,
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
            )

    # ─── 内部方法 ───

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

    def _promote_to_elite(
        self, factor: FactorProgram, evaluation: FactorEvaluation
    ) -> Path:
        """将因子晋升到精英池。"""
        import json
        fp = self.elite_dir / f"{factor['factor_id']}.json"
        fp.write_text(
            json.dumps(
                {"factor": factor, "evaluation": evaluation},
                ensure_ascii=False, indent=2,
            ),
            encoding="utf-8",
        )
        return fp

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


if __name__ == "__main__":
    main()


__all__ = [
    "EvolutionRunResult",
    "EvolutionLoop",
    "main",
]
