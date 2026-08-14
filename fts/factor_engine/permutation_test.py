"""
fts.factor_engine.permutation_test — 因子有效性置换检验（手册阶段4/6/9）。

对照《期货CTA多因子策略标准化作业手册》:
    阶段4 Checkpoint: 因子通过置换检验（随机打乱标签后 IC 显著降低）
    阶段6 Checkpoint: 合成模型的样本外夏普应显著低于真实标签下的夏普
    阶段9 Checkpoint: 置换检验 p 值 < 0.05

核心思想（蒙特卡洛置换检验）:
    将「标签」随机打乱 N 次，每次重算目标统计量（IC / 夏普），构造零分布；
    若真实统计量落在零分布极尾（双侧 p 值 < α），说明因子有效性并非偶然。

设计约束:
    - 固定随机种子（默认 42），结果可复现
    - 双侧 p 值：同时检验正/负方向有效性（因子方向为负时同样显著）
    - NaN 兜底：剔除含 NaN 的样本对
    - 零未来函数：只重排已观测样本的标签，不引入任何未来信息

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
import pandas as pd
from scipy import stats as sp_stats

logger = logging.getLogger(__name__)


# ─── 配置契约 ─────────────────────────────────────────────


@dataclass
class PermutationConfig:
    """置换检验配置。

    Attributes:
        n_permutations: 置换次数（越大零分布越稳，默认 200）
        random_seed: 随机种子（固定保证可复现）
        alpha: 显著性水平（默认 0.05）
        method: 相关性方法（"spearman" / "pearson"）
        sharpe_annualization: 夏普年化因子（日频 252）
    """

    n_permutations: int = 200
    random_seed: int = 42
    alpha: float = 0.05
    method: str = "spearman"
    sharpe_annualization: int = 252


# ─── 结果契约 ─────────────────────────────────────────────


@dataclass
class PermutationResult:
    """置换检验结果。"""

    observed: float  # 真实统计量
    null_mean: float  # 零分布均值
    null_std: float  # 零分布标准差
    null_p5: float  # 零分布 5% 分位
    null_p95: float  # 零分布 95% 分位
    p_value: float  # 双侧 p 值
    passed: bool  # p_value < alpha 即通过
    n_permutations: int  # 实际置换次数
    statistic: str  # 统计量名称（"ic" / "sharpe"）

    def to_dict(self) -> dict:
        return {
            "observed": self.observed,
            "null_mean": self.null_mean,
            "null_std": self.null_std,
            "null_p5": self.null_p5,
            "null_p95": self.null_p95,
            "p_value": self.p_value,
            "passed": self.passed,
            "n_permutations": self.n_permutations,
            "statistic": self.statistic,
        }


# ─── 底层：通用蒙特卡洛置换引擎 ───────────────────────────


def _run_permutations(
    observed: float,
    null_sampler: Callable[[np.random.RandomState], float],
    n_permutations: int,
    random_seed: int,
) -> tuple[float, float, float, float, float, np.ndarray]:
    """执行置换采样并汇总零分布统计。

    Args:
        observed: 真实统计量
        null_sampler: 接收 RandomState 的函数，单次打乱标签后返回置换统计量
        n_permutations: 置换次数
        random_seed: 随机种子（注入 sampler 保证可复现、不污染全局随机流）

    Returns:
        (零分布均值, 零分布标准差, p5, p95, p_value, null 数组)。
    """
    rng = np.random.RandomState(random_seed)
    null: np.ndarray = np.empty(n_permutations, dtype=float)
    for i in range(n_permutations):
        null[i] = null_sampler(rng)
    # 双侧 p 值 = 2 × min(上尾, 下尾)，+1 平滑避免 0
    p_upper = (int(np.sum(null >= observed)) + 1) / (n_permutations + 1)
    p_lower = (int(np.sum(null <= observed)) + 1) / (n_permutations + 1)
    p_value = min(1.0, 2.0 * min(p_upper, p_lower))
    p5 = float(np.percentile(null, 5))
    p95 = float(np.percentile(null, 95))
    return float(np.mean(null)), float(np.std(null, ddof=1) if n_permutations > 1 else 0.0), p5, p95, p_value, null


def _corr(x: np.ndarray, y: np.ndarray, method: str) -> float:
    """带 NaN 兜底的相关系数计算。

    Args:
        x: 信号数组
        y: 收益数组
        method: "spearman" / "pearson"

    Returns:
        相关系数；样本不足或常数输入返回 0.0。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = ~(np.isnan(x) | np.isnan(y))
    xv, yv = x[valid], y[valid]
    if len(xv) < 2 or np.std(xv) < 1e-12 or np.std(yv) < 1e-12:
        return 0.0
    if method == "pearson":
        corr, _ = sp_stats.pearsonr(xv, yv)
    else:
        corr, _ = sp_stats.spearmanr(xv, yv)
    return float(corr) if not np.isnan(corr) else 0.0


def _sharpe(daily_returns: np.ndarray, annualization: int) -> float:
    """年化夏普比率（零未来函数，仅用给定收益序列）。"""
    r = np.asarray(daily_returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 2 or np.std(r) < 1e-12:
        return 0.0
    return float(np.mean(r) / np.std(r, ddof=1) * np.sqrt(annualization))


# ─── 阶段4：因子 IC 置换检验 ──────────────────────────────


def factor_ic_permutation_test(
    signal: np.ndarray | pd.Series,
    forward_returns: np.ndarray | pd.Series,
    config: Optional[PermutationConfig] = None,
) -> PermutationResult:
    """因子 IC 置换检验（阶段4 Checkpoint）。

    随机打乱标签（未来收益）N 次重算 IC 构造零分布，
    检验真实 IC 是否显著非零（双侧 p 值）。

    Args:
        signal: 因子信号（1D，与 forward_returns 对齐）
        forward_returns: 未来收益标签（1D）
        config: 置换配置（None=默认）

    Returns:
        PermutationResult。
    """
    cfg = config or PermutationConfig()
    sig = np.asarray(signal, dtype=float)
    ret = np.asarray(forward_returns, dtype=float)
    if len(sig) != len(ret) or len(sig) < 2:
        return PermutationResult(
            observed=0.0, null_mean=0.0, null_std=0.0,
            null_p5=0.0, null_p95=0.0, p_value=1.0, passed=False,
            n_permutations=cfg.n_permutations, statistic="ic",
        )
    observed = _corr(sig, ret, cfg.method)

    def sampler(rng: np.random.RandomState) -> float:
        perm = rng.permutation(len(ret))
        return _corr(sig, ret[perm], cfg.method)

    null_mean, null_std, p5, p95, p_value, _ = _run_permutations(
        observed, sampler, cfg.n_permutations, cfg.random_seed
    )
    passed = bool(p_value < cfg.alpha)
    return PermutationResult(
        observed=observed, null_mean=null_mean, null_std=null_std,
        null_p5=p5, null_p95=p95, p_value=p_value, passed=passed,
        n_permutations=cfg.n_permutations, statistic="ic",
    )


# ─── 阶段6/9：合成模型组合夏普置换检验 ────────────────────


def portfolio_sharpe_permutation_test(
    scores: np.ndarray | pd.Series,
    forward_returns: np.ndarray | pd.Series,
    config: Optional[PermutationConfig] = None,
) -> PermutationResult:
    """合成模型组合夏普置换检验（阶段6/9 Checkpoint）。

    真实标签下的组合夏普（分数标准化 × 未来收益）应显著高于
    打乱标签后的零分布夏普，验证合成得分非随机拟合。

    Args:
        scores: 综合预测得分（1D，与 forward_returns 对齐）
        forward_returns: 未来收益标签（1D）
        config: 置换配置（None=默认）

    Returns:
        PermutationResult（statistic="sharpe"）。
    """
    cfg = config or PermutationConfig()
    sc = np.asarray(scores, dtype=float)
    ret = np.asarray(forward_returns, dtype=float)
    if len(sc) != len(ret) or len(sc) < 2:
        return PermutationResult(
            observed=0.0, null_mean=0.0, null_std=0.0,
            null_p5=0.0, null_p95=0.0, p_value=1.0, passed=False,
            n_permutations=cfg.n_permutations, statistic="sharpe",
        )
    # 组合日收益：分数 z 标准化 × 未来收益（多空方向），NaN 对剔除
    valid = ~(np.isnan(sc) | np.isnan(ret))
    sc_v, ret_v = sc[valid], ret[valid]
    if len(sc_v) < 2:
        return PermutationResult(
            observed=0.0, null_mean=0.0, null_std=0.0,
            null_p5=0.0, null_p95=0.0, p_value=1.0, passed=False,
            n_permutations=cfg.n_permutations, statistic="sharpe",
        )
    sc_std = (sc_v - np.mean(sc_v)) / (np.std(sc_v) if np.std(sc_v) > 1e-12 else 1.0)
    observed = _sharpe(sc_std * ret_v, cfg.sharpe_annualization)

    def sampler(rng: np.random.RandomState) -> float:
        perm = rng.permutation(len(ret_v))
        return _sharpe(sc_std * ret_v[perm], cfg.sharpe_annualization)

    null_mean, null_std, p5, p95, p_value, _ = _run_permutations(
        observed, sampler, cfg.n_permutations, cfg.random_seed
    )
    passed = bool(p_value < cfg.alpha)
    return PermutationResult(
        observed=observed, null_mean=null_mean, null_std=null_std,
        null_p5=p5, null_p95=p95, p_value=p_value, passed=passed,
        n_permutations=cfg.n_permutations, statistic="sharpe",
    )


__all__ = [
    "PermutationConfig",
    "PermutationResult",
    "factor_ic_permutation_test",
    "portfolio_sharpe_permutation_test",
]
