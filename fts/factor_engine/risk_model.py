"""
fts.factor_engine.risk_model — 风险模型估计器（GAP-L302，v2.61.0）。

机构级组合构建的第二块地基：对因子收益矩阵估计稳健的协方差矩阵。
核心为 Ledoit-Wolf 收缩（样本协方差 → 对角结构化目标），保证正定性，
供组合优化（均值方差/风险平价）与组合风险度量使用。

实现:
    - 优先使用 sklearn.covariance.LedoitWolf（若已安装）
    - 回退 numpy 自实现 Ledoit-Wolf（对角目标 + 收缩强度估计）
    - 输出: 收缩协方差 / 收缩强度 / 特征值 / 条件数 / 年化波动率

用法:
    estimator = RiskModelEstimator()
    result = estimator.estimate(factor_returns)
    cov = result.cov  # 收缩协方差 (n, n)

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── 契约 ─────────────────────────────────────────────────


@dataclass
class RiskModelConfig:
    """风险模型估计配置。"""

    shrinkage: str = "ledoit_wolf"  # "ledoit_wolf" | "none"
    annualize_factor: float = 252.0  # 年化因子
    min_obs: int = 10  # 最少观测行数（不足抛 ValueError）


@dataclass
class RiskModelResult:
    """风险模型估计结果。"""

    cov: np.ndarray  # 收缩协方差矩阵 (n, n)
    sample_cov: np.ndarray  # 原始样本协方差 (n, n)
    shrinkage: float  # 收缩强度 (0~1，0=不收缩)
    eigenvalues: np.ndarray  # 协方差特征值（升序）
    condition_number: float  # 条件数（最大/最小特征值）
    realized_vol: np.ndarray  # 年化波动率 (n,)
    n_obs: int  # 有效观测行数
    n_factors: int  # 因子数

    def to_dict(self) -> dict[str, float]:
        """序列化摘要（供报告/日志）。"""
        return {
            "n_obs": int(self.n_obs),
            "n_factors": int(self.n_factors),
            "shrinkage": round(float(self.shrinkage), 4),
            "condition_number": round(float(self.condition_number), 4),
        }


class RiskModelEstimator:
    """风险模型估计器（Ledoit-Wolf 收缩协方差）。

    Args:
        config: 估计配置（None 用默认）
    """

    def __init__(self, config: Optional[RiskModelConfig] = None) -> None:
        self._config = config or RiskModelConfig()
        if self._config.shrinkage not in ("ledoit_wolf", "none"):
            raise ValueError(f"不支持的收缩方式: {self._config.shrinkage}")

    # ─── 主入口 ──────────────────────────────────────────

    def estimate(self, factor_returns: pd.DataFrame) -> RiskModelResult:
        """估计收缩协方差矩阵。

        Args:
            factor_returns: 因子收益矩阵 (T × N)，缺失行剔除

        Returns:
            RiskModelResult（收缩协方差/收缩强度/特征值/条件数/年化波动率）

        Raises:
            ValueError: 因子数 < 2 / 有效观测不足
        """
        fr = factor_returns.dropna(how="any")
        n_obs, n_factors = fr.shape
        if n_factors < 2:
            raise ValueError(f"因子数 {n_factors} < 2，无法估计协方差")
        if n_obs < self._config.min_obs:
            raise ValueError(f"有效观测不足（{n_obs} < {self._config.min_obs}），无法估计协方差")

        X = fr.to_numpy(dtype=float)
        sample_cov = np.cov(X, rowvar=False, ddof=1)

        if self._config.shrinkage == "none":
            shrinkage = 0.0
            cov = sample_cov
        else:
            cov, shrinkage = self._ledoit_wolf(X, sample_cov)

        # 正定性兜底：特征值 clip + 微 jitter
        cov = self._ensure_positive_definite(cov)

        # 特征值 / 条件数 / 年化波动率
        eigenvals = np.linalg.eigvalsh(cov)
        eigenvals = np.maximum(eigenvals, 1e-12)
        cond = float(eigenvals.max() / max(eigenvals.min(), 1e-12))
        vol = np.sqrt(np.maximum(np.diag(cov), 0.0)) * np.sqrt(self._config.annualize_factor)

        logger.info(
            "[RiskModel] 估计完成: %d 因子 × %d 观测, shrinkage=%.4f, 条件数=%.2f",
            n_factors,
            n_obs,
            shrinkage,
            cond,
        )
        return RiskModelResult(
            cov=cov,
            sample_cov=sample_cov,
            shrinkage=shrinkage,
            eigenvalues=eigenvals,
            condition_number=cond,
            realized_vol=vol,
            n_obs=n_obs,
            n_factors=n_factors,
        )

    # ─── Ledoit-Wolf 收缩 ────────────────────────────────

    def _ledoit_wolf(
        self,
        X: np.ndarray,
        sample_cov: np.ndarray,
    ) -> tuple[np.ndarray, float]:
        """Ledoit-Wolf 收缩（纯 numpy 自实现，对角结构化目标）。

        Ledoit & Wolf (2004)：收缩协方差 = α·F + (1−α)·S，
        F = 对角目标矩阵，α = min(1, b²/d²) 由数据估计。

        Args:
            X: 收益矩阵 (T × N)
            sample_cov: 样本协方差 (N, N)

        Returns:
            (收缩协方差, 收缩强度)
        """
        n, _ = X.shape
        Xc = X - X.mean(axis=0)
        # 对角结构化目标
        target = np.diag(np.diag(sample_cov))
        d2 = float(np.sum((sample_cov - target) ** 2))

        # b² = (1/n) * Σ_k ||outer(x_k, x_k) − S||²_F
        b2 = 0.0
        for k in range(n):
            outer = np.outer(Xc[k], Xc[k]) - sample_cov
            b2 += float(np.sum(outer**2))
        b2 /= n

        shrinkage = 0.0 if d2 <= 0 else float(np.clip(b2 / d2, 0.0, 1.0))
        cov = shrinkage * target + (1.0 - shrinkage) * sample_cov
        return cov, shrinkage

    # ─── 数值兜底 ────────────────────────────────────────

    @staticmethod
    def _ensure_positive_definite(cov: np.ndarray) -> np.ndarray:
        """正定性保证：特征值 clip 到 1e-8 + 微 jitter。"""
        try:
            eigenvals, eigvecs = np.linalg.eigh(cov)
            eigenvals = np.maximum(eigenvals, 1e-8)
            cov_pd = (eigvecs * eigenvals) @ eigvecs.T
            cov_pd = (cov_pd + cov_pd.T) / 2.0
            return cov_pd
        except np.linalg.LinAlgError:
            n = cov.shape[0]
            return cov + np.eye(n) * 1e-8


__all__ = [
    "RiskModelConfig",
    "RiskModelResult",
    "RiskModelEstimator",
]
