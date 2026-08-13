"""
loop_engine/evolution_uct.py — EvolutionLoop 领域 I Mixin：UCT 父因子选择 + 熔断/提前停止

34 计划（plans/34-evolution-loop-refactor-inventory.md）B 阶段第一步：从
evolution_loop.py 抽取领域 I（UCT 选择与熔断停止）为独立 Mixin，行为等价、
公开 API 不变。原方法剪切迁移（不改逻辑），领域独享状态（_uct_stats /
_evolution_stop_* / _consecutive_empty_generations / _early_stop_*）随迁，
跨领域共享状态（budget / _consecutive_low_ic）留在主类实例，经 self 访问。

契约（见 01-architecture.md §5 EvolutionLoop Mixin 拆分契约）：
- Mixin 方法名全局唯一，不 import evolution_loop（单向依赖，防循环导入）；
- 本模块为 UCT_EXPLORATION_C 单一事实源，evolution_loop.py re-export。
"""

from __future__ import annotations

import math
from typing import Optional

from .contracts import BudgetConfig, EvolutionState, FactorEvaluation, FactorProgram

# ─── UCT 常量（单一事实源，evolution_loop.py re-export） ─────────────
UCT_EXPLORATION_C: float = 1.0
"""UCT 探索常数。越大越倾向探索未访问的父因子。"""


class EvolutionUctMixin:
    """领域 I：UCT 树搜索父因子选择 + 熔断/提前停止。

    实例属性由主类 EvolutionLoop.__init__ 装配；此处类型声明供 mypy
    跨文件识别（34 计划 Mixin 拆分契约第 5/6 条）。领域独享状态
    （_uct_stats/_evolution_stop_*/_consecutive_empty_generations/_early_stop_*）
    随本 Mixin 迁移，跨领域共享状态（budget/_consecutive_low_ic）留在主类。
    """

    # ── 实例属性类型声明（装配在 evolution_loop.py EvolutionLoop.__init__） ──
    _uct_stats: dict[str, dict[str, float]]
    budget: BudgetConfig
    _consecutive_low_ic: int
    _evolution_stop_enabled: bool
    _evolution_stop_k: int
    _consecutive_empty_generations: int
    _early_stop_last_count: int
    _early_stop_reason: Optional[str]

    def _select_parent_uct(self, parents: list[FactorProgram]) -> FactorProgram:
        """UCT 树搜索选择父因子，平衡探索与利用。

        UCB = avg_reward + c * sqrt(ln(total_visits) / visits)

        未访问的父因子（visits=0）返回无限大 UCB，确保优先探索。
        """
        total_visits = sum(s.get("visits", 0) for s in self._uct_stats.values())
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
            exploration = UCT_EXPLORATION_C * math.sqrt(math.log(max(total_visits, 1)) / visits)
            ucb = avg_reward + exploration
            if ucb > best_score:
                best_score = ucb
                best_parent = p

        return best_parent

    def _update_uct_stats(self, parent: FactorProgram, evaluation: FactorEvaluation) -> None:
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

    def _update_uct_failure(self, parent: FactorProgram) -> None:
        """记录父因子演化失败的 UCT 反馈（GAP-074 P0-1）。

        演化失败/运行时校验失败/快速预筛失败路径均调用：visits+1、不授予
        正奖励。避免失败父因子 visits 恒 0，导致 `_select_parent_uct`
        永远返回 parents[0] 的选择坍缩（50 代全部演化同一父因子）。
        """
        fid = parent["factor_id"]
        if fid not in self._uct_stats:
            self._uct_stats[fid] = {"visits": 0, "total_reward": 0.0}
        self._uct_stats[fid]["visits"] += 1

    def _check_circuit_breaker(self, state: EvolutionState) -> Optional[str]:
        """熔断检查。返回原因字符串（None = 未触发）。"""
        # Token 超 2x
        tokens = state.get("tokens_consumed", 0)
        limit = state.get("budget_limit", self.budget["nightly_token_limit"])
        if tokens > limit * self.budget["circuit_breaker_token_ratio"]:
            return f"Token 熔断: {tokens} > {limit} * {self.budget['circuit_breaker_token_ratio']}"

        # 连续低 IC
        if self._consecutive_low_ic >= self.budget["circuit_breaker_consecutive_low_ic"]:
            return (
                f"连续低 IC 熔断: {self._consecutive_low_ic} 代 IC < {self.budget['circuit_breaker_low_ic_threshold']}"
            )

        # 失败率 > 90%
        evaluated = state.get("total_factors_evaluated", 0)
        promoted = state.get("total_factors_promoted", 0)
        if evaluated >= 10:
            failure_rate = (evaluated - promoted) / evaluated
            if failure_rate > self.budget["circuit_breaker_failure_rate"]:
                return f"失败率熔断: {failure_rate:.2%} > {self.budget['circuit_breaker_failure_rate']:.2%}"

        return None

    def _maybe_early_stop(self, state: EvolutionState) -> bool:
        """P1-3 (Phase 3, 26 计划 §8): 连续 K 代零晋升 → 提前停止（每代结束后调用）。

        基于 `state.total_factors_promoted` 与上次记录值的差异判断本代是否晋升，
        覆盖全部路径（演化失败/运行时拦截/预筛拦截 continue 均计入零晋升代）。
        保守默认关闭（enabled=False，验证见 plans/26 §8.7.1）。

        Args:
            state: L2 演化状态

        Returns:
            True 表示达到阈值应提前结束 run（调用方 break，正常收尾）
        """
        if not self._evolution_stop_enabled:
            self._consecutive_empty_generations = 0
            self._early_stop_last_count = state.get("total_factors_promoted", 0)
            return False
        cur = state.get("total_factors_promoted", 0)
        if cur == self._early_stop_last_count:
            self._consecutive_empty_generations += 1
        else:
            self._consecutive_empty_generations = 0
        self._early_stop_last_count = cur
        if self._consecutive_empty_generations >= self._evolution_stop_k:
            self._early_stop_reason = (
                f"连续 {self._consecutive_empty_generations} 代零晋升（阈值 K={self._evolution_stop_k}）"
            )
            return True
        return False
