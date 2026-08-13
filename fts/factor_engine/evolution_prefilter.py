"""
loop_engine/evolution_prefilter.py — CandidatePrefilter 协作类：候选预筛

34 计划（plans/34-evolution-loop-refactor-inventory.md）C 阶段 Phase 47b：
B 阶段产物 EvolutionPrefilterMixin 组合式重构为 CandidatePrefilter 协作类，
行为等价、公开 API 不变。本领域纯读跨领域共享数据（data / market /
forward_returns / cross_section_data / cross_section_dates /
_is_cross_section），无领域独享状态。由于主类与测试可能在构造后动态
重赋值这些全局上下文（见 34 §8.3：可变全局上下文经主类实例访问），
本协作类注入 owner（EvolutionLoop 实例）而非上下文值快照，方法内动态
经 `self._owner.<attr>` 读取。主类 EvolutionLoop 组合持有本类实例，保留
原 3 方法转发桩（兼容测试零改动，见 34 §8.5）。

跨组件约束（34 §8.3）：协作类不 import evolution_loop（防循环导入），
owner 仅经 TYPE_CHECKING 类型标注，运行时经主类组装注入。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查
    from .contracts import FactorProgram

logger = logging.getLogger(__name__)


class CandidatePrefilter:
    """领域 H：候选预筛（34 计划 C 阶段协作类）。

    状态所有权（34 §8.3）：无领域独享状态；可变全局上下文
    （data/market/forward_returns/cross_section_data/cross_section_dates/
    _is_cross_section）经 owner（主类实例）动态读取，避免构造快照与
    主类/测试运行时重赋值脱节。
    """

    def __init__(self, owner: Any) -> None:
        self._owner: Any = owner
        # 兼容：owner 构造早期注入调用点（如单元测试直构）可能尚无属性，
        # 属性读取一律延迟到方法体内经 _owner 动态访问。
        # owner 经主类组装注入（EvolutionLoop），Any 标注避免循环 ForwardRef
        # 解析（协作类不 import evolution_loop，见 34 §8.3）。

    # ── Phase B.2.1: 快速预筛选（新增） ──────────────────

    def _quick_prefilter(
        self,
        factor: "FactorProgram",
        trace_id: str,
    ) -> tuple[bool, str, float]:
        """快速预筛选：在源头拦截低质量信号，避免浪费评估资源。

        检查项:
            1. 信号非全常数: nunique > 10
            2. 快速 IC 检查: abs(IC) > 0.02（Spearman 秩相关）
            3. 信号标准差 > 1e-6

        横截面模式使用真实截面收益（信号矩阵 vs 截面 forward 收益，
        与 cross_section_evaluate_backtest 同口径），而非单标的时序 IC。

        Args:
            factor: 因子程序
            trace_id: 全链路 trace_id

        Returns:
            (是否通过, 失败原因, 预筛 IC；通过时原因为空，失败时 IC 为 0.0)
        """
        from scipy import stats as sp_stats
        from .backtest_pipeline import BacktestPipeline

        # 横截面模式: 用全面板构建真实截面收益计算 IC（GAP-X01）
        if self._owner._is_cross_section:
            return self._cross_section_prefilter(factor, trace_id)

        probe_data = self._owner.data
        try:
            signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""),
                probe_data,
                factor.get("params", {}),
            )
        except Exception as e:
            return False, f"预筛选执行失败: {type(e).__name__}: {e}", 0.0

        if not isinstance(signal, np.ndarray) or len(signal) != len(probe_data):
            return (
                False,
                f"预筛选输出长度不匹配: {len(signal) if hasattr(signal, '__len__') else '?'} != {len(probe_data)}",
                0.0,
            )

        # 检查1: 信号非全常数
        nunique = len(np.unique(signal))
        if nunique <= 10:
            return False, f"信号无足够变化: nunique={nunique} <= 10", 0.0

        # 检查2: 信号标准差
        sig_std = np.nanstd(signal)
        if sig_std < 1e-6:
            return False, f"信号标准差过小: {sig_std:.2e} < 1e-6", 0.0

        # 检查3: 快速 IC 检查（导致 NaN 也视为无效）
        # 期货日频单品种时序 IC 信噪比低（常见 0.01-0.02 区间），
        # 阈值按市场自适应放宽，避免拦截本可进入截面评估的后代
        ic_threshold = 0.01 if self._owner.market == "futures" else 0.02
        fr = self._owner.forward_returns
        if fr is not None and len(fr) == len(signal):
            valid = ~(np.isnan(signal) | np.isnan(fr))
            if valid.sum() >= 10:
                ic, pval = sp_stats.spearmanr(signal[valid], fr[valid])
                if np.isnan(ic) or abs(ic) < ic_threshold:
                    return (
                        False,
                        (
                            f"快速 IC 过低: abs(IC)={abs(ic):.4f} < {ic_threshold}"
                            f"{'' if np.isnan(ic) else f', p={pval:.4f}'}"
                        ),
                        0.0,
                    )
                return True, "", abs(ic)

        return True, "", 0.0

    def _cross_section_prefilter(
        self,
        factor: "FactorProgram",
        trace_id: str,
    ) -> tuple[bool, str, float]:
        """横截面快速预筛：用真实截面收益计算截面 Spearman IC。

        与 cross_section_evaluate_backtest 同口径：对所有标的同时运行因子，
        对齐共同日期构建信号矩阵与截面 forward 收益矩阵，每期计算截面 IC。
        替代原先单标的时序 IC 口径（与 forward_returns 长度不齐时常被跳过，
        且单标的时序 IC 无法反映因子截面区分能力）。

        Args:
            factor: 因子程序
            trace_id: 全链路 trace_id

        Returns:
            (是否通过, 失败原因, 预筛 IC 绝对值；失败时 IC 为 0.0)
        """
        from .evaluation_chain import (
            _cs_build_matrices,
            _cs_compute_ics,
            _cs_execute_factors,
        )
        from .factor_program import FactorExecutor

        panel = self._owner.cross_section_data
        if not panel:
            return True, "", 0.0

        try:
            executor = FactorExecutor(factor)
            signal_dict, ret_dict = _cs_execute_factors(
                executor,
                factor.get("params", {}),
                panel,
            )
        except Exception as e:
            return False, f"预筛选执行失败: {type(e).__name__}: {e}", 0.0

        if len(signal_dict) < 5:
            return False, f"横截面有效标的不足: {len(signal_dict)} < 5", 0.0

        common_dates = self._owner.cross_section_dates
        if common_dates is None or len(common_dates) == 0:
            return True, "", 0.0

        # 全样本截面（预筛不切片，正式评估再走 OOS）
        signal_matrix, ret_matrix = _cs_build_matrices(
            signal_dict,
            ret_dict,
            common_dates,
            len(common_dates),
        )
        ics = _cs_compute_ics(signal_matrix, ret_matrix)
        if not ics:
            # 无有效截面期（如窗口期样本不足），放行交由正式评估兜底
            return True, "", 0.0

        ic_abs = abs(float(np.mean(ics)))
        ic_threshold = 0.01 if self._owner.market == "futures" else 0.02
        if ic_abs < ic_threshold:
            return False, (f"横截面快速 IC 过低: abs(IC)={ic_abs:.4f} < {ic_threshold}"), 0.0
        return True, "", ic_abs

    # ── Phase B.2.1: 后代因子运行时校验 ──────────────────

    def _check_factor_runtime(
        self,
        factor: "FactorProgram",
    ) -> tuple[bool, str]:
        """试运行因子程序，在源头拦截 LLM 生成代码的运行时错误。

        拦截场景:
            - 广播错误（如 shapes (n,) 与 (2,) 混合运算）
            - 输出长度与输入不匹配（np.diff/np.convolve 未保持长度 n）
            - 常数信号（无信息量）

        复用 BacktestPipeline._execute_factor_code（与回测流水线同一执行路径），
        保证「校验通过 = 流水线可执行」，避免无效后代进入下游评估。

        Args:
            factor: 因子程序

        Returns:
            (是否通过, 失败原因；通过时原因为空)
        """
        from .backtest_pipeline import BacktestPipeline

        probe_data = (
            list(self._owner.cross_section_data.values())[0]
            if (self._owner._is_cross_section and self._owner.cross_section_data is not None)
            else self._owner.data
        )
        try:
            signal = BacktestPipeline._execute_factor_code(
                factor.get("code", ""),
                probe_data,
                factor.get("params", {}),
            )
        except Exception as e:
            return False, f"执行失败: {type(e).__name__}: {e}"

        if not isinstance(signal, np.ndarray) or len(signal) != len(probe_data):
            return False, (f"输出长度不匹配: {len(signal) if hasattr(signal, '__len__') else '?'} != {len(probe_data)}")
        if np.std(signal) < 1e-12:
            return False, "输出为常数信号（无信息量）"
        return True, ""
