"""
fts.factor_engine.regime_model_selection — HMM 状态数选择（BIC）与特征标准化（28 计划 T8）。

对标:
  - SSRN 5785443: 用 BIC 选择 HMM 状态数（三状态优于二状态），避免固定状态数过拟合/欠拟合；
  - VAE 市场画像一文: 特征只 fit 训练段（StandardScaler 只在训练集 fit），防止数据泄露/数据窥探。

当前包含:
  - fit_standardizer: 训练段均值/标准差拟合（防数据窥探）；
  - select_n_states: 对候选状态数逐 BIC 评分，取 BIC 最小者。

版本: v0.1.0
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

_HMM_AVAILABLE: bool = False
try:
    from hmmlearn import hmm

    _HMM_AVAILABLE = True
except ImportError:
    pass


def fit_standardizer(features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """fit 训练段均值和标准差（只统计训练段，防数据窥探）。

    参数:
        features: (n_samples, n_features) 训练特征矩阵。

    返回:
        (mean, std) — 每列均值与标准差（std 加 1e-8 防除零）。
    """
    mean = features.mean(axis=0)
    std = features.std(axis=0) + 1e-8
    return mean, std


def select_n_states(
    features: np.ndarray,
    candidates: tuple[int, ...] = (2, 3, 4, 5),
    random_seed: int = 42,
) -> int:
    """用 BIC 选择最优状态数（训练稳定 + BIC 最小者优先）。

    BIC = -2 * loglik + n_params * ln(len(features))
    n_params 近似 = n*(n-1) + 2*n*n_features（转移矩阵 + 每状态均值/方差对角）。

    参数:
        features:   (n_samples, n_features) 特征矩阵（应已标准化）。
        candidates: 候选状态数集合。
        random_seed: 随机种子（保证确定性）。

    返回:
        BIC 最小的状态数；hmmlearn 不可用或数据 < 60 时回退 4（现状默认）。
    """
    if not _HMM_AVAILABLE or len(features) < 60:
        return 4  # 默认回退现状
    best_n: int = candidates[0]
    best_bic: float = float("inf")
    for n in candidates:
        try:
            model = hmm.GaussianHMM(
                n_components=n,
                covariance_type="diag",
                n_iter=100,
                tol=1e-4,
                random_state=random_seed,
            )
            model.fit(features)
            loglik = model.score(features)
            n_params = n * (n - 1) + 2 * n * features.shape[1]  # 转移 + 均值/方差对角
            bic = -2.0 * loglik + n_params * np.log(len(features))
            logger.info("[RegimeModelSelection] n_states=%d BIC=%.2f", n, bic)
            if bic < best_bic:
                best_bic, best_n = bic, n
        except Exception as e:  # noqa: BLE001
            logger.warning("[RegimeModelSelection] n=%d 拟合失败: %s", n, e)
    return best_n
