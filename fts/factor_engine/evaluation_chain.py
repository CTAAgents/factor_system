"""
loop_engine/evaluation_chain.py — agentic 三级评估链

HARNESS §11-loop-engineering.md §4:
    Level 1 — 回测验证（IC>0.03 / 夏普>1.5 / 单调性 / 样本外≥30%）
    Level 2 — 经济逻辑（四维评分 ≥ 3/4）
    Level 3 — 多重检验（FDR + Bonferroni）

版本: v8.10.0
"""
# pylint: disable=too-many-locals,too-few-public-methods

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

from .contracts import (
    BacktestMetrics,
    EconomicScore,
    FactorEvaluation,
    FactorProgram,
    MultipleTestResult,
)
from .factor_program import FactorExecutor


# ─── Level 1: 回测验证 ────────────────────────────────────

def _compute_ic(
    signal: np.ndarray, forward_returns: np.ndarray, method: str = "spearman"
) -> tuple[float, float]:
    """计算 IC（信息系数）和 ICIR。

    Args:
        signal: 因子信号数组
        forward_returns: 未来收益率数组
        method: "spearman" / "pearson"

    Returns:
        (ic_mean, icir)
    """
    if len(signal) != len(forward_returns) or len(signal) < 2:
        return 0.0, 0.0
    if method == "spearman":
        ic, _ = sp_stats.spearmanr(signal, forward_returns)
    else:
        ic, _ = sp_stats.pearsonr(signal, forward_returns)
    if np.isnan(ic):
        return 0.0, 0.0
    # ICIR = IC 均值 / IC 标准差（这里简化为单期）
    return float(ic), float(ic)  # 多期时 icir = mean/std


def _compute_sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float:
    """计算年化夏普比率（假设无风险利率=0）。"""
    if len(returns) < 2:
        return 0.0
    std = np.std(returns, ddof=1)
    if std < 1e-10:
        return 0.0
    return float(np.mean(returns) / std * np.sqrt(periods_per_year))


def _compute_max_drawdown(cumulative: np.ndarray) -> float:
    """计算最大回撤（0~1）。"""
    if len(cumulative) < 2:
        return 0.0
    peak = np.maximum.accumulate(cumulative)
    drawdown = (peak - cumulative) / np.maximum(peak, 1e-10)
    return float(np.max(drawdown))


def _check_monotonicity(signal: np.ndarray, returns: np.ndarray, n_buckets: int = 10) -> bool:
    """检查十分位组合收益率是否严格单调。

    Args:
        signal: 因子信号
        returns: 未来收益率
        n_buckets: 分组数（默认 10）

    Returns:
        True 如果严格单调（升序或降序）
    """
    if len(signal) < n_buckets * 10:
        return False
    df = pd.DataFrame({"signal": signal, "return": returns})
    df = df.dropna()
    if len(df) < n_buckets:
        return False
    df["bucket"] = pd.qcut(df["signal"], n_buckets, labels=False, duplicates="drop")
    bucket_returns = df.groupby("bucket")["return"].mean()
    if len(bucket_returns) < n_buckets:
        return False
    # 严格单调递增或递减
    diffs = np.diff(bucket_returns.values)
    return bool(np.all(diffs > 0) or np.all(diffs < 0))


def evaluate_backtest(
    factor: FactorProgram,
    data: pd.DataFrame,
    forward_returns: np.ndarray,
    oos_ratio: float = 0.3,
    periods_per_year: int = 252,
) -> BacktestMetrics:
    """Level 1 — 回测验证。

    Args:
        factor: 因子程序
        data: OHLCV 数据
        forward_returns: 未来收益率（与 data 等长）
        oos_ratio: 样本外比例
        periods_per_year: 年化系数

    Returns:
        BacktestMetrics
    """
    executor = FactorExecutor(factor)
    signal = executor.execute(data, factor.get("params", {}))

    n = len(signal)
    oos_n = max(int(n * oos_ratio), 1)

    # 样本外切片
    oos_signal = signal[-oos_n:]
    oos_returns = forward_returns[-oos_n:]
    in_sample_signal = signal[:-oos_n]
    in_sample_returns = forward_returns[:-oos_n]

    # IC（样本外）
    ic, icir = _compute_ic(oos_signal, oos_returns)
    # 全样本 ICIR（多期近似）
    if len(in_sample_signal) > 0:
        ic_in, _ = _compute_ic(in_sample_signal, in_sample_returns)
        icir = float(np.mean([ic_in, ic]) / max(np.std([ic_in, ic]), 1e-10))

    # 信号分组构建多空组合收益
    if len(oos_signal) > 0:
        # 用信号 top 20% 做多，bottom 20% 做空
        sorted_idx = np.argsort(oos_signal)
        top_n = max(1, len(oos_signal) // 5)
        long_ret = np.mean(oos_returns[sorted_idx[-top_n:]])
        short_ret = np.mean(oos_returns[sorted_idx[:top_n]])
        ls_returns = np.full(oos_n, long_ret - short_ret)
    else:
        ls_returns = np.zeros(oos_n)

    sharpe = _compute_sharpe(ls_returns, periods_per_year)
    cumulative = np.cumsum(ls_returns)
    max_dd = _compute_max_drawdown(cumulative)
    monotonic = _check_monotonicity(signal, forward_returns)

    # t 统计量
    if len(ls_returns) > 1 and np.std(ls_returns) > 1e-10:
        t_stat = float(np.mean(ls_returns) / np.std(ls_returns, ddof=1) * np.sqrt(len(ls_returns)))
    else:
        t_stat = 0.0

    # 月度换手率（简化估计）
    if len(signal) > 1:
        signal_changes = np.abs(np.diff(np.sign(signal)))
        turnover = float(np.mean(signal_changes) * 21)  # 日均换手 * 21 = 月度
    else:
        turnover = 0.0

    return BacktestMetrics(
        ic=ic,
        icir=icir,
        sharpe=sharpe,
        max_drawdown=max_dd,
        monotonicity=monotonic,
        oos_ratio=oos_ratio,
        t_stat=t_stat,
        turnover_monthly=turnover,
    )


# ─── Level 2: 经济逻辑评分 ────────────────────────────────

def evaluate_economic_logic(factor: FactorProgram) -> EconomicScore:
    """Level 2 — 经济逻辑评分（四维）。

    直接从 factor.economic_logic 读取，并计算达标维度数。
    """
    el = factor.get("economic_logic", {})
    theory = int(el.get("theory", 0))
    behavioral = int(el.get("behavioral", 0))
    microstructure = int(el.get("microstructure", 0))
    institutional = int(el.get("institutional", 0))

    threshold = 3  # 每维达标阈值 3/5
    dims_passed = sum([
        1 if theory >= threshold else 0,
        1 if behavioral >= threshold else 0,
        1 if microstructure >= threshold else 0,
        1 if institutional >= threshold else 0,
    ])

    return EconomicScore(
        theory=theory,
        behavioral=behavioral,
        microstructure=microstructure,
        institutional=institutional,
        dimensions_passed=dims_passed,
        narrative=el.get("narrative", ""),
    )


# ─── Level 3: 多重检验校正 ────────────────────────────────

def evaluate_multiple_tests(
    factors_evaluations: list[FactorEvaluation],
    correlation_matrix: Optional[np.ndarray] = None,
    alpha: float = 0.01,
    fdr_q: float = 0.05,
) -> MultipleTestResult:
    """Level 3 — 多重检验校正。

    Bonferroni: p_adj = p * n
    FDR (Benjamini-Hochberg): 控制假阳性比例

    Args:
        factors_evaluations: 全部因子的评估结果
        correlation_matrix: 因子相关性矩阵（用于有效因子数调整）
        alpha: 显著性水平
        fdr_q: FDR 阈值

    Returns:
        MultipleTestResult（针对当前批次的统计）
    """
    n = max(1, len(factors_evaluations))

    # 收集所有 t 统计量
    t_stats = []
    for ev in factors_evaluations:
        bt = ev.get("level_1_backtest", {})
        t = bt.get("t_stat", 0.0)
        t_stats.append(t)

    # 当前因子的 t（取最后一个）
    current_t = t_stats[-1] if t_stats else 0.0

    # 双侧 p 值（正态近似）
    if current_t != 0:
        p_value = 2 * (1 - sp_stats.norm.cdf(abs(current_t)))
    else:
        p_value = 1.0

    # Bonferroni 校正
    bonferroni_p = min(1.0, p_value * n)

    # 有效因子数（考虑相关性）
    if correlation_matrix is not None and len(correlation_matrix) > 0:
        # 主成分分析近似：特征值 > 1 的数量
        try:
            eigenvalues = np.linalg.eigvalsh(correlation_matrix)
            effective_n = int(np.sum(eigenvalues > 1.0))
        except np.linalg.LinAlgError:
            effective_n = n
    else:
        effective_n = n

    # 调整后 t 统计量
    adjusted_t = current_t / np.sqrt(max(1, effective_n))

    # FDR 通过条件
    fdr_passed = bonferroni_p < alpha or p_value < (fdr_q / n)

    passed = bool(bonferroni_p < alpha and adjusted_t > 2.0 and fdr_passed)

    return MultipleTestResult(
        bonferroni_p=float(bonferroni_p),
        fdr_q=float(fdr_q),
        effective_n_factors=int(effective_n),
        adjusted_t=float(adjusted_t),
        passed=passed,
    )


# ─── 三级评估链 ───────────────────────────────────────────

class EvaluationChain:
    """agentic 三级评估链。

    Usage:
        chain = EvaluationChain()
        evaluation = chain.evaluate(factor, data, forward_returns, all_evaluations)
    """

    def __init__(self, oos_ratio: float = 0.3, periods_per_year: int = 252):
        self.oos_ratio = oos_ratio
        self.periods_per_year = periods_per_year

    def evaluate(
        self,
        factor: FactorProgram,
        data: pd.DataFrame,
        forward_returns: np.ndarray,
        prior_evaluations: Optional[list[FactorEvaluation]] = None,
        correlation_matrix: Optional[np.ndarray] = None,
    ) -> FactorEvaluation:
        """执行三级评估链。

        Args:
            factor: 待评估因子
            data: OHLCV 数据
            forward_returns: 未来收益率
            prior_evaluations: 之前所有因子的评估结果（用于多重检验）
            correlation_matrix: 因子相关性矩阵

        Returns:
            FactorEvaluation
        """
        # Level 1
        bt = evaluate_backtest(
            factor, data, forward_returns, self.oos_ratio, self.periods_per_year
        )
        # Level 2
        ec = evaluate_economic_logic(factor)
        # Level 3
        all_evals = list(prior_evaluations or [])
        # 当前因子的临时评估（无 Level 3）用于多重检验
        temp_eval = FactorEvaluation(
            factor_id=factor["factor_id"],
            trace_id=factor["trace_id"],
            level_1_backtest=bt,
            level_2_economic=ec,
            level_3_multiple=MultipleTestResult(),  # 占位
            passed=False,
            failure_reasons=[],
            evaluated_at=datetime.now().isoformat(),
        )
        all_evals.append(temp_eval)
        mt = evaluate_multiple_tests(all_evals, correlation_matrix)

        # 失败原因汇总
        reasons: list[str] = []
        if bt.get("ic", 0) < 0.03:
            reasons.append(f"Level 1: IC={bt.get('ic', 0):.4f} < 0.03")
        if bt.get("sharpe", 0) < 1.5:
            reasons.append(f"Level 1: 夏普={bt.get('sharpe', 0):.4f} < 1.5")
        if not bt.get("monotonicity", False):
            reasons.append("Level 1: 非单调")
        if ec.get("dimensions_passed", 0) < 3:
            reasons.append(f"Level 2: 维度达标={ec.get('dimensions_passed', 0)} < 3")
        if not mt.get("passed", False):
            reasons.append("Level 3: 多重检验未通过")

        passed = len(reasons) == 0
        return FactorEvaluation(
            factor_id=factor["factor_id"],
            trace_id=factor["trace_id"],
            level_1_backtest=bt,
            level_2_economic=ec,
            level_3_multiple=mt,
            passed=passed,
            failure_reasons=reasons,
            evaluated_at=datetime.now().isoformat(),
        )


__all__ = [
    "evaluate_backtest",
    "evaluate_economic_logic",
    "evaluate_multiple_tests",
    "EvaluationChain",
]
