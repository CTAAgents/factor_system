"""
fts/factor_engine/batch_mining.py — 批量挖掘漏斗（GAP-I201，Stage 1）

解决 L2 演化"每代 1 个后代"的吞吐瓶颈：对同一父因子批量生成多个后代
（方法轮换 + seed 递增），并行粗筛（运行时校验 + 快速预筛），通过者
按预筛 IC 排序截断进入细评估。准入链（micro/eval/审计/4 重审查/晋升）
由 evolution_loop._process_candidate 复用，本模块零业务耦合（依赖注入）。

设计文档: docs/archive/design/D.1-batch-mining-design.md

版本: v1.0.0（GAP-I201，v2.65.0）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from itertools import repeat
from typing import Callable, Optional, TypedDict

from .contracts import FactorProgram
from .executor_backend import create_executor_backend

logger = logging.getLogger(__name__)

# ─── 配置 ─────────────────────────────────────────────────


@dataclass
class BatchMiningConfig:
    """批量挖掘配置（D.1 §3.1）。

    Attributes:
        batch_size: 每代批量候选生成数。
        max_candidates: 通过粗筛后进入细评估的最大候选数（预算护栏）。
        max_workers: 粗筛并行工作数（numpy/scipy 纯计算，线程并行有效）。
        ic_threshold: 预筛 IC 阈值；None = 按市场自适应（复用 _quick_prefilter）。
        random_seed: 随机种子（可复现，batch 内按序号递增）。
        executor_backend: 执行器后端（"thread"/"process"/"dask"/"ray"，GAP-I502）。
        executor_max_workers: 后端并行数（None 时用 max_workers）。
    """

    batch_size: int = 20
    max_candidates: int = 5
    max_workers: int = 4
    ic_threshold: Optional[float] = None
    random_seed: int = 42
    executor_backend: str = "thread"
    executor_max_workers: Optional[int] = None


# ─── 契约 ─────────────────────────────────────────────────


class BatchedProposal(TypedDict, total=False):
    """批量候选（D.1 §3.2）。

    - factor: 后代因子程序
    - parent_id: 父因子 ID
    - method: 演化方法（macro_evolution / gp_evolution / operator_evolution）
    - summary: 演化摘要（经验链/失败轨迹用）
    - tokens: LLM token 消耗（state 计数用）
    - prefilter_ok: 粗筛通过标记
    - prefilter_reason: 未通过原因
    - prefilter_ic: 预筛 IC（排序截断依据，未通过时为 0.0）
    """

    factor: FactorProgram
    parent_id: str
    method: str
    summary: str
    tokens: int
    prefilter_ok: bool
    prefilter_reason: str
    prefilter_ic: float


@dataclass
class BatchGenerationResult:
    """一代批量漏斗结果（D.1 §3.3）。"""

    generation: int
    total_generated: int = 0
    total_passed: int = 0
    total_rejected: int = 0
    passed: list[BatchedProposal] = field(default_factory=list)
    rejected: list[BatchedProposal] = field(default_factory=list)
    tokens_consumed: int = 0
    duration_ms: float = 0.0


# ─── 挖掘器 ───────────────────────────────────────────────

_GenerateCb = Callable[[FactorProgram, int, str], Optional[BatchedProposal]]
_RuntimeCheckCb = Callable[[FactorProgram], tuple[bool, str]]
_PrefilterCb = Callable[[FactorProgram, str], tuple[bool, str, float]]


class BatchMiner:
    """批量挖掘器 — 批量生成 + 并行粗筛（D.1 §3.4）。

    Usage:
        miner = BatchMiner(
            config=BatchMiningConfig(batch_size=20),
            generate_cb=loop._evolve_one_wrapped,
            runtime_check_cb=loop._check_factor_runtime,
            prefilter_cb=loop._quick_prefilter_wrapped,
        )
        result = miner.run_iteration(parent, generation=1, trace_id="...")
        for proposal in result.passed:
            loop._process_candidate(proposal["factor"], ...)
    """

    def __init__(
        self,
        config: Optional[BatchMiningConfig] = None,
        *,
        generate_cb: Optional[_GenerateCb] = None,
        runtime_check_cb: Optional[_RuntimeCheckCb] = None,
        prefilter_cb: Optional[_PrefilterCb] = None,
    ) -> None:
        self.config = config or BatchMiningConfig()
        self._generate_cb = generate_cb
        self._runtime_check_cb = runtime_check_cb
        self._prefilter_cb = prefilter_cb

    # ── 批量生成 ──────────────────────────────────────────

    def generate_batch(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
    ) -> list[BatchedProposal]:
        """批量生成 batch_size 个后代（依赖注入 generate_cb）。

        Args:
            parent: 父因子
            generation: 当前代数
            trace_id: 全链路 trace_id

        Returns:
            生成的候选列表（生成失败者不计入，实际数量 ≤ batch_size）
        """
        if self._generate_cb is None:
            logger.warning("[batch] generate_cb 未注入，返回空批")
            return []
        proposals: list[BatchedProposal] = []
        for i in range(self.config.batch_size):
            try:
                proposal = self._generate_cb(parent, generation, trace_id)
            except Exception as e:
                logger.debug("[batch] 第 %d 个候选生成失败: %s", i, e)
                proposal = None
            if proposal is not None:
                proposals.append(proposal)
        return proposals

    # ── 批量粗筛（并行） ──────────────────────────────────

    def _filter_one(
        self,
        proposal: BatchedProposal,
        trace_id: str,
    ) -> BatchedProposal:
        """对单个候选执行运行时校验 + 快速预筛。"""
        factor = proposal.get("factor", {})
        # ① 运行时校验
        if self._runtime_check_cb is not None:
            ok, reason = self._runtime_check_cb(factor)
            if not ok:
                return {
                    **proposal,
                    "prefilter_ok": False,
                    "prefilter_reason": f"运行时校验失败: {reason}",
                    "prefilter_ic": 0.0,
                }
        # ② 快速预筛
        if self._prefilter_cb is not None:
            ok, reason, ic = self._prefilter_cb(factor, trace_id)
            if not ok:
                return {
                    **proposal,
                    "prefilter_ok": False,
                    "prefilter_reason": f"预筛失败: {reason}",
                    "prefilter_ic": 0.0,
                }
            return {**proposal, "prefilter_ok": True, "prefilter_reason": "", "prefilter_ic": ic}
        return {**proposal, "prefilter_ok": True, "prefilter_reason": "", "prefilter_ic": 0.0}

    def filter_batch(
        self,
        proposals: list[BatchedProposal],
        trace_id: str,
    ) -> BatchGenerationResult:
        """批量并行粗筛，按 prefilter_ic 降序截断（≤ max_candidates）。

        Args:
            proposals: 待粗筛候选
            trace_id: 全链路 trace_id

        Returns:
            批量漏斗结果（passed / rejected / 统计）
        """
        start = time.perf_counter()
        n_workers = min(self.config.max_workers, max(len(proposals), 1))
        # GAP-I502 (v2.83.0): 执行器后端可插拔（thread/process/dask/ray，调用方无感知）
        filtered: list[BatchedProposal] = []
        if len(proposals) == 1:
            filtered = [self._filter_one(proposals[0], trace_id)]
        elif len(proposals) > 1:
            backend = create_executor_backend(
                self.config.executor_backend,
                self.config.executor_max_workers or n_workers,
            )
            try:
                it = backend.map(self._filter_one, proposals, repeat(trace_id))
                for p in proposals:
                    try:
                        filtered.append(next(it))
                    except StopIteration:
                        break
                    except Exception as e:  # 单任务异常降级为 rejected（与其他任务隔离）
                        logger.debug("[batch] 粗筛任务异常: %s", e)
                        filtered.append(
                            {
                                **p,
                                "prefilter_ok": False,
                                "prefilter_reason": f"粗筛异常: {type(e).__name__}: {e}",
                                "prefilter_ic": 0.0,
                            }
                        )
            finally:
                backend.shutdown()

        passed = [p for p in filtered if p.get("prefilter_ok")]
        rejected = [p for p in filtered if not p.get("prefilter_ok")]
        # 按预筛 IC 降序，截断到 max_candidates；被截断项标记后进 rejected（保持计数一致）
        passed.sort(key=lambda p: p.get("prefilter_ic", 0.0), reverse=True)
        truncated = passed[: self.config.max_candidates]
        for overflow in passed[self.config.max_candidates :]:
            overflow["prefilter_ok"] = False
            overflow["prefilter_reason"] = f"超过 max_candidates={self.config.max_candidates} 截断"
            rejected.append(overflow)

        duration_ms = (time.perf_counter() - start) * 1000.0
        return BatchGenerationResult(
            generation=0,
            total_generated=len(proposals),
            total_passed=len(truncated),
            total_rejected=len(rejected),
            passed=truncated,
            rejected=rejected,
            tokens_consumed=sum(p.get("tokens", 0) for p in proposals),
            duration_ms=round(duration_ms, 2),
        )

    # ── 一代完整漏斗 ──────────────────────────────────────

    def run_iteration(
        self,
        parent: FactorProgram,
        generation: int,
        trace_id: str,
    ) -> BatchGenerationResult:
        """一代完整漏斗：generate_batch → filter_batch。

        Args:
            parent: 父因子
            generation: 当前代数
            trace_id: 全链路 trace_id

        Returns:
            批量漏斗结果
        """
        proposals = self.generate_batch(parent, generation, trace_id)
        result = self.filter_batch(proposals, trace_id)
        result.generation = generation
        return result


__all__ = [
    "BatchMiningConfig",
    "BatchedProposal",
    "BatchGenerationResult",
    "BatchMiner",
]
