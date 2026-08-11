"""
fts.factor_engine.black_litterman — Black-Litterman 观点融合组合层（C3，v2.100.1）。

机构级 ML 组合层的最小落地：在既有 Ledoit-Wolf 收缩协方差 + 风险平价/均值方差
基础上，引入 Black-Litterman 观点融合——先验权重（风险平价）隐含收益 π 与
因子观点（IC 均值 × 置信度）Q 融合，得到后验 μ/Σ，再解最大夏普权重。

数学（标准 Black-Litterman，增量形式，数值稳定）:
    π      = λ·Σ·w_prior                          # 逆优化隐含收益
    μ_post = π + τΣP'(PτΣP' + Ω)⁻¹(Q − Pπ)        # 后验均值（增量修正）
    M_inv  = τΣ − τΣP'(PτΣP' + Ω)⁻¹PτΣ            # 参数不确定性协方差
    Σ_post = Σ + M_inv                             # 后验协方差
    w      = max_sharpe(Σ_post, μ_post) → 约束投影  # 后验权重

性质:
    - 空观点（k=0）退化: w = w_prior（= 风险平价先验），不融合
    - Q = Pπ 时: μ_post = π（观点与先验一致不改变后验均值）
    - 观点置信度 ↑（Ω↓）: 后验偏离先验幅度 ↑

零依赖（纯 numpy），对齐 GAP-F07/GAP-L302 既有约束语义
（集中度 max_weight / 杠杆 max_leverage）。

用法:
    result = black_litterman_weights(cov, prior_w, views_q, views_p)
    result.weights  # 后验权重
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class BlackLittermanConfig:
    """Black-Litterman 融合配置（C3）。"""

    tau: float = 0.05  # 先验协方差缩放标量 τ
    omega_scale: float = 0.1  # 观点不确定性标量：Ω = diag(diag(P(τΣ)P')) × scale
    risk_aversion: float = 1.0  # 隐含收益的风险厌恶系数 λ
    max_weight: float = 0.3  # 单因子权重上限（与 OptimizerConfig.max_weight 对齐）
    max_leverage: float = 1.0  # 总杠杆上限


@dataclass
class BlackLittermanResult:
    """BL 融合结果（C3）。"""

    mu_posterior: np.ndarray  # 后验均值 (n,)
    sigma_posterior: np.ndarray  # 后验协方差 (n, n)
    weights: np.ndarray  # 后验权重 (n,)
    prior_mu: np.ndarray  # 先验隐含收益 π (n,)
    view_q: np.ndarray  # 观点收益 (k,)


def implied_returns(
    cov: np.ndarray,
    prior_weights: np.ndarray,
    risk_aversion: float = 1.0,
) -> np.ndarray:
    """逆优化隐含收益：π = λ·Σ·w_prior。

    Args:
        cov: 协方差矩阵 (n, n)
        prior_weights: 先验权重 (n,)
        risk_aversion: 风险厌恶系数

    Returns:
        隐含收益向量 (n,)
    """
    cov = np.asarray(cov, dtype=float)
    prior_weights = np.asarray(prior_weights, dtype=float).ravel()
    if cov.ndim != 2 or cov.shape[0] != cov.shape[1]:
        raise ValueError(f"协方差必须为方阵，收到 {cov.shape}")
    if prior_weights.shape != (cov.shape[0],):
        raise ValueError(f"先验权重维度 {prior_weights.shape} 与资产数 {cov.shape[0]} 不一致")
    return risk_aversion * cov @ prior_weights


def build_auto_views(
    factors: list[dict[str, Any]],
    pi: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """从因子 IC 自动构建绝对观点。

    观点方向 = 原始 IC 符号（`_ic_raw` 优先，IC 截断前值），
    强度 = 原始 IC × (mean|π| / max|IC|) 尺度（对齐先验隐含收益量级）。

    Args:
        factors: 因子列表（每个含 ic/_ic_raw）
        pi: 先验隐含收益 (n,)

    Returns:
        (views_p, views_q)：P = I（绝对观点），Q = 观点收益 (n,)；
        全部 IC 为 0 时返回空视图 (k=0, 退化先验)
    """
    n = len(factors)
    if n == 0:
        return np.zeros((0, n)), np.array([], dtype=float)
    raw_ics = np.array(
        [float(f.get("_ic_raw", f.get("ic", 0.0))) for f in factors],
        dtype=float,
    )
    max_abs = float(np.max(np.abs(raw_ics))) if n else 0.0
    if max_abs <= 0:
        return np.zeros((0, n)), np.array([], dtype=float)
    scale = float(np.mean(np.abs(pi))) / max_abs if n else 0.0
    views_q = raw_ics * scale
    return np.eye(n), views_q


def black_litterman_weights(
    cov: np.ndarray,
    prior_weights: np.ndarray,
    views_q: np.ndarray,
    views_p: Optional[np.ndarray] = None,
    config: Optional[BlackLittermanConfig] = None,
) -> BlackLittermanResult:
    """Black-Litterman 观点融合，输出后验权重。

    Args:
        cov: 协方差矩阵 (n, n)（建议 Ledoit-Wolf 收缩）
        prior_weights: 先验权重 (n,)（建议风险平价解）
        views_q: 观点预期收益 (k,)
        views_p: 观点矩阵 (k, n)；None 时视为绝对观点 P=I
        config: 融合配置（None 用默认）

    Returns:
        BlackLittermanResult（含后验权重）

    Raises:
        ValueError: 维度不匹配
    """
    cfg = config or BlackLittermanConfig()
    cov = np.asarray(cov, dtype=float)
    n = cov.shape[0]
    if cov.ndim != 2 or cov.shape != (n, n):
        raise ValueError(f"协方差必须为方阵，收到 {cov.shape}")
    if n == 0:
        return BlackLittermanResult(
            mu_posterior=np.array([], dtype=float),
            sigma_posterior=np.array([], dtype=float).reshape(0, 0),
            weights=np.array([], dtype=float),
            prior_mu=np.array([], dtype=float),
            view_q=np.array([], dtype=float),
        )
    prior_weights = np.asarray(prior_weights, dtype=float).ravel()
    if prior_weights.shape != (n,):
        raise ValueError(f"先验权重维度 {prior_weights.shape} 与资产数 {n} 不一致")

    pi = implied_returns(cov, prior_weights, cfg.risk_aversion)

    views_q = np.asarray(views_q, dtype=float).ravel()
    if views_p is None:
        views_p = np.eye(n)
    views_p = np.asarray(views_p, dtype=float)

    # 空观点退化：不融合，直接返回先验权重（与 views_p 形状无关）
    if views_q.size == 0:
        w_prior = _project_weights(prior_weights, cfg)
        return BlackLittermanResult(
            mu_posterior=pi,
            sigma_posterior=cov,
            weights=w_prior,
            prior_mu=pi,
            view_q=views_q,
        )

    if views_p.ndim != 2 or views_p.shape[1] != n:
        raise ValueError(f"观点矩阵形状 {views_p.shape} 需 (k, {n})")
    k = views_p.shape[0]
    if views_q.shape != (k,):
        raise ValueError(f"观点收益维度 {views_q.shape} 与观点数 {k} 不一致")

    # Ω = diag(diag(P(τΣ)P')) × omega_scale（正定下限）
    tau_sigma = cfg.tau * cov
    p_t = views_p.T
    omega_diag = np.maximum(
        np.diag(views_p @ tau_sigma @ p_t) * cfg.omega_scale,
        1e-12,
    )
    omega = np.diag(omega_diag)
    kernel = views_p @ tau_sigma @ p_t + omega

    # 后验均值（增量形式）：μ_post = π + τΣP'(PτΣP' + Ω)⁻¹(Q − Pπ)
    diff = views_q - views_p @ pi
    try:
        corr = np.linalg.solve(kernel, diff)
        mu_post = pi + tau_sigma @ p_t @ corr
        # 参数不确定性协方差：M_inv = τΣ − τΣP'(PτΣP' + Ω)⁻¹PτΣ
        corr_sigma = np.linalg.solve(kernel, views_p @ tau_sigma)
        m_inv = tau_sigma - tau_sigma @ p_t @ corr_sigma
    except np.linalg.LinAlgError:
        logger.warning("[BL] 观点矩阵奇异，退化到先验权重")
        w_prior = _project_weights(prior_weights, cfg)
        return BlackLittermanResult(
            mu_posterior=pi,
            sigma_posterior=cov,
            weights=w_prior,
            prior_mu=pi,
            view_q=views_q,
        )
    sigma_post = cov + m_inv

    # 后验最大夏普权重（无约束解 + 约束投影）
    try:
        w = np.linalg.solve(
            sigma_post + 1e-9 * np.eye(n) * float(np.mean(np.diag(sigma_post)) + 1e-12),
            mu_post,
        )
    except np.linalg.LinAlgError:
        w = mu_post
    w = np.nan_to_num(w, nan=0.0, posinf=0.0, neginf=0.0)
    w = _project_weights(w, cfg)

    return BlackLittermanResult(
        mu_posterior=mu_post,
        sigma_posterior=sigma_post,
        weights=w,
        prior_mu=pi,
        view_q=views_q,
    )


def _project_weights(w: np.ndarray, cfg: BlackLittermanConfig) -> np.ndarray:
    """投影到集中度 + 杠杆约束（截断 → 归一化），语义对齐 PortfolioOptimizer._project。"""
    w = np.clip(w, 0.0, cfg.max_weight)
    total = float(np.sum(w))
    if total > cfg.max_leverage and total > 0:
        w = w * (cfg.max_leverage / total)
    if total <= 0:
        w = np.full_like(w, cfg.max_leverage / max(len(w), 1))
    return w


__all__ = [
    "BlackLittermanConfig",
    "BlackLittermanResult",
    "build_auto_views",
    "implied_returns",
    "black_litterman_weights",
]
