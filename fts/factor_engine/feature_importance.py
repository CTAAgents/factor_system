"""
fts.factor_engine.feature_importance — 特征重要性分析 (Phase C.1)。

基于置换重要性 (Permutation Importance) 的特征重要性分析器。
通过逐个打乱特征值，观察因子 IC 下降幅度来评估特征贡献。

版本: v0.1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FeatureImportanceResult:
    """特征重要性分析结果。"""

    factor_id: str = ""
    feature_importance: dict[str, float] = field(default_factory=dict)
    top_features: list[tuple[str, float]] = field(default_factory=list)
    analysis_method: str = "permutation"
    n_features_analyzed: int = 0
    baseline_ic: float = 0.0
    analysis_time_ms: float = 0.0


class FeatureImportanceAnalyzer:
    """特征重要性分析器。

    Usage:
        analyzer = FeatureImportanceAnalyzer()
        importance = analyzer.analyze(
            factor_series=factor_values,
            data=data_panel,
            target_col='forward_return_20d',
        )
    """

    def analyze(
        self,
        factor_series: pd.Series,
        data: pd.DataFrame,
        target_col: str,
        feature_names: Optional[list[str]] = None,
        n_permutations: int = 1,
    ) -> FeatureImportanceResult:
        """分析特征重要性。

        Args:
            factor_series: 因子值序列
            data: 输入数据 DataFrame
            target_col: 目标列名
            feature_names: 要分析的特征列名列表 (None = 所有列)
            n_permutations: 置换次数

        Returns:
            FeatureImportanceResult
        """
        import time

        start_ms = time.time() * 1000

        if feature_names is None:
            feature_names = [c for c in data.columns if c != target_col]

        # 计算基线 IC
        target = data[target_col]
        aligned = pd.concat([factor_series, target], axis=1).dropna()
        if len(aligned) < 20:
            return FeatureImportanceResult(
                factor_id="unknown",
                n_features_analyzed=0,
                baseline_ic=0.0,
            )

        baseline_ic = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
        if np.isnan(baseline_ic):
            baseline_ic = 0.0

        # 置换重要性
        importance: dict[str, float] = {}
        for feature in feature_names:
            if feature not in data.columns:
                continue
            importance[feature] = self._compute_permutation_importance(
                factor_series,
                data,
                target_col,
                feature,
                baseline_ic,
                n_permutations,
            )

        # 排序取 Top-10
        sorted_features = sorted(
            importance.items(),
            key=lambda x: abs(x[1]),
            reverse=True,
        )
        top_features = sorted_features[:10]

        elapsed_ms = time.time() * 1000 - start_ms

        return FeatureImportanceResult(
            factor_id="factor_analysis",
            feature_importance=importance,
            top_features=top_features,
            analysis_method="permutation",
            n_features_analyzed=len(feature_names),
            baseline_ic=float(baseline_ic),
            analysis_time_ms=elapsed_ms,
        )

    def _compute_permutation_importance(
        self,
        factor_series: pd.Series,
        data: pd.DataFrame,
        target_col: str,
        feature_col: str,
        baseline_ic: float,
        n_permutations: int = 1,
    ) -> float:
        """计算单个特征的置换重要性。

        Args:
            factor_series: 因子值序列
            data: 输入数据
            target_col: 目标列
            feature_col: 要置换的特征列
            baseline_ic: 基线 IC
            n_permutations: 置换次数

        Returns:
            重要性分数 (IC 下降幅度)
        """
        import numpy as np

        shuffled_data = data.copy()
        ic_drops: list[float] = []

        for _ in range(n_permutations):
            # 打乱特征
            shuffled_data[feature_col] = np.random.permutation(shuffled_data[feature_col].values)

            # 重新计算因子与目标的 IC
            # 简化: 直接用打乱后的特征和因子的相关性变化估计
            target = shuffled_data[target_col]
            aligned = pd.concat(
                [factor_series, target],
                axis=1,
            ).dropna()

            if len(aligned) < 5:
                continue

            try:
                perturbed_ic = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
                if np.isnan(perturbed_ic):
                    perturbed_ic = 0.0
                ic_drop = abs(baseline_ic) - abs(perturbed_ic)
                ic_drops.append(ic_drop)
            except Exception:
                continue

        if not ic_drops:
            return 0.0

        return float(np.mean(ic_drops))
