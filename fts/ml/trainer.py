"""
fts.ml.trainer — ML 信号模型训练管线（Phase 24，v2.38.0）。

支持三种训练模式:
    - ``cross_sectional``: 横截面回归（面板数据，每日截面特征 → 未来收益）
    - ``time_series``: 时序预测（单标的时间序列特征 → 未来收益）
    - ``ensemble_fusion``: 集成融合（多模型等权平均）

统一入口 ``SignalModelTrainer.train(X, y)`` 返回 ``TrainResult``：
    - ``model``: 训练好的 MLSignalModel（依赖缺失时为 None）
    - ``score``: R²（回归）
    - ``feature_importance``: 特征重要性映射（可用时）

角色边界: 训练管线只产出预测模型，不涉及交易执行。

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

import numpy as np

from .models import MLSignalModel, ModelKind, create_signal_model

logger = logging.getLogger(__name__)


class TrainMode(str, Enum):
    """训练模式枚举。"""

    CROSS_SECTIONAL = "cross_sectional"
    TIME_SERIES = "time_series"
    ENSEMBLE_FUSION = "ensemble_fusion"


@dataclass
class TrainResult:
    """训练结果。"""

    mode: TrainMode
    kind: ModelKind
    model: Optional[MLSignalModel] = None
    score: float = 0.0
    n_samples: int = 0
    n_features: int = 0
    feature_importance: dict[str, float] = field(default_factory=dict)
    message: str = ""


class SignalModelTrainer:
    """ML 信号模型训练器。

    Args:
        kind: 模型类型（lightgbm / xgboost / ensemble）
        mode: 训练模式
        params: 模型超参（可选）
        seed: 随机种子
    """

    def __init__(
        self,
        kind: ModelKind | str = "lightgbm",
        mode: TrainMode | str = TrainMode.CROSS_SECTIONAL,
        params: Optional[dict[str, Any]] = None,
        seed: int = 42,
    ) -> None:
        self.kind = ModelKind(kind)
        self.mode = TrainMode(mode)
        self.params = dict(params or {})
        self.seed = seed

    # ─── 主入口 ──────────────────────────────────────────

    def train(
        self,
        X: Any,
        y: Any,
        feature_names: Optional[list[str]] = None,
    ) -> TrainResult:
        """训练模型。

        Args:
            X: 特征矩阵（numpy 数组或 pandas DataFrame）
            y: 目标向量
            feature_names: 特征名（用于输出重要性，缺省用位置名）

        Returns:
            TrainResult；模型依赖缺失时 model=None + message 说明。
        """
        import pandas as pd

        if isinstance(X, pd.DataFrame):
            X_np = X.to_numpy(dtype=float)
            names = list(X.columns) if feature_names is None else feature_names
        else:
            X_np = np.asarray(X, dtype=float)
            n_feat = X_np.shape[1]
            names = feature_names or [f"f{i}" for i in range(n_feat)]

        y_np = np.asarray(y, dtype=float)
        n_samples = X_np.shape[0]
        n_features = X_np.shape[1]
        if n_samples < 2 or n_features < 1:
            return TrainResult(
                mode=self.mode, kind=self.kind, message="样本数或特征数不足",
            )

        # 模式预处理
        if self.mode == TrainMode.CROSS_SECTIONAL:
            pass  # 直接回归
        elif self.mode == TrainMode.TIME_SERIES:
            pass  # 特征已含滞后项，直接回归
        elif self.mode == TrainMode.ENSEMBLE_FUSION:
            # 强制 ensemble 模型类型
            self.kind = ModelKind.ENSEMBLE

        # NaN 清理（简单去行，保持回归器输入合法）
        mask = np.isfinite(X_np).all(axis=1) & np.isfinite(y_np)
        X_clean = X_np[mask]
        y_clean = y_np[mask]
        if len(y_clean) < 2:
            return TrainResult(
                mode=self.mode, kind=self.kind, message="清理 NaN 后样本数不足",
            )

        model = create_signal_model(self.kind, self.params, self.seed)
        if model is None:
            return TrainResult(
                mode=self.mode, kind=self.kind, model=None,
                n_samples=len(y_clean), n_features=n_features,
                message="模型依赖未安装，跳过训练（降级）",
            )

        model.fit(X_clean, y_clean)
        pred = model.predict(X_clean)
        score = self._r2_score(y_clean, pred)
        importance = self._extract_importance(model, names, n_features)

        return TrainResult(
            mode=self.mode, kind=self.kind, model=model,
            score=float(score),
            n_samples=int(len(y_clean)), n_features=n_features,
            feature_importance=importance,
            message="ok",
        )

    # ─── 内部工具 ────────────────────────────────────────

    @staticmethod
    def _r2_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
        """R² 计算。"""
        denom = float(np.sum((y_true - np.mean(y_true)) ** 2))
        if denom <= 1e-12:
            return 0.0
        return float(1.0 - np.sum((y_true - y_pred) ** 2) / denom)

    def _extract_importance(
        self,
        model: MLSignalModel,
        names: list[str],
        n_features: int,
    ) -> dict[str, float]:
        """提取特征重要性（可用时）。"""
        if model.kind == ModelKind.ENSEMBLE:
            # Ensemble 无统一重要性，聚合子模型均值
            imp = {}
            for sub in getattr(model, "_sub_models", []) or []:
                try:
                    fi = sub.feature_importances_
                    for i, v in enumerate(fi):
                        if i < n_features:
                            key = names[i] if i < len(names) else f"f{i}"
                            imp[key] = imp.get(key, 0.0) + float(v)
                except Exception:  # noqa: BLE001 - 子模型无 importance 属性
                    return {}
            total = sum(imp.values()) or 1.0
            return {k: v / total for k, v in imp.items()}

        base = model.model
        if base is None:
            return {}
        try:
            fi = base.feature_importances_
        except Exception:  # noqa: BLE001
            return {}
        arr = np.asarray(fi, dtype=float)
        if arr.size != n_features:
            return {}
        total = float(arr.sum()) or 1.0
        return {
            (names[i] if i < len(names) else f"f{i}"): float(arr[i]) / total
            for i in range(n_features)
        }


__all__ = [
    "TrainMode",
    "SignalModelTrainer",
    "TrainResult",
]
