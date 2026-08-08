"""
fts.ml.models — ML 信号模型封装（Phase 24，v2.38.0）。

统一接口抽象 LightGBM / XGBoost / Ensemble 三种模型，可选依赖：
    - ``lightgbm>=4.0``
    - ``xgboost>=2.0``

设计原则:
    - 缺依赖不抛异常：``create_signal_model`` 返回 ``None``，调用方降级回退
    - 模型接口统一为 ``fit(X, y)`` / ``predict(X)``，与 sklearn 风格一致
    - Ensemble 为「等权平均」集成，输入为各子模型预测矩阵

版本: v1.0.0
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional, Protocol, runtime_checkable

import numpy as np

logger = logging.getLogger(__name__)

# 可选依赖探测（一次完成，供整个模块使用）
_has_lightgbm = False
_has_xgboost = False
try:  # pragma: no cover - 依赖探测
    import lightgbm  # noqa: F401

    _has_lightgbm = True
except ImportError:  # pragma: no cover
    pass
try:  # pragma: no cover - 依赖探测
    import xgboost  # noqa: F401

    _has_xgboost = True
except ImportError:  # pragma: no cover
    pass


class ModelKind(str, Enum):
    """模型类型枚举。"""

    LIGHTGBM = "lightgbm"
    XGBOOST = "xgboost"
    ENSEMBLE = "ensemble"


class ModelNotAvailableError(RuntimeError):
    """模型依赖未安装时抛出。"""


@runtime_checkable
class FittableModel(Protocol):
    """sklearn 风格的可拟合模型协议（fit/predict）。"""

    def fit(self, X: Any, y: Any, **kwargs: Any) -> "FittableModel": ...

    def predict(self, X: Any) -> np.ndarray: ...


class MLSignalModel:
    """ML 信号模型统一封装。

    包装具体模型（LightGBM/XGBoost/等权 Ensemble），对外暴露统一接口。
    对 ``fit`` 的 ``eval_set`` 等进阶参数做 Optional 透传，保持简单。
    """

    def __init__(
        self,
        kind: ModelKind | str,
        params: Optional[dict[str, Any]] = None,
        seed: int = 42,
    ) -> None:
        self.kind = ModelKind(kind)
        self.params = dict(params or {})
        self.seed = seed
        self._model: Optional[Any] = None

    # ─── 工厂构造 ────────────────────────────────────────

    def _build(self) -> Any:
        """按 kind 构造底层模型；依赖缺失抛 ModelNotAvailableError。"""
        if self.kind == ModelKind.LIGHTGBM:
            if not _has_lightgbm:
                raise ModelNotAvailableError("lightgbm 未安装，请执行 pip install .[ml]")
            from lightgbm import LGBMRegressor

            return LGBMRegressor(
                n_estimators=self.params.get("n_estimators", 200),
                learning_rate=self.params.get("learning_rate", 0.05),
                num_leaves=self.params.get("num_leaves", 31),
                random_state=self.seed,
                verbose=-1,
            )
        if self.kind == ModelKind.XGBOOST:
            if not _has_xgboost:
                raise ModelNotAvailableError("xgboost 未安装，请执行 pip install .[ml]")
            from xgboost import XGBRegressor

            return XGBRegressor(
                n_estimators=self.params.get("n_estimators", 200),
                learning_rate=self.params.get("learning_rate", 0.05),
                max_depth=self.params.get("max_depth", 6),
                random_state=self.seed,
            )
        # Ensemble
        if not (_has_lightgbm or _has_xgboost):
            raise ModelNotAvailableError("ensemble 需要至少安装 lightgbm 或 xgboost")
        return None  # Ensemble 无单一底层模型，见 fit/predict

    @property
    def is_available(self) -> bool:
        """底层依赖是否可用（LIGHTGBM 需要 lightgbm，XGBOOST 需要 xgboost，ENSEMBLE 需要至少其一）。"""
        if self.kind == ModelKind.LIGHTGBM:
            return _has_lightgbm
        if self.kind == ModelKind.XGBOOST:
            return _has_xgboost
        return _has_lightgbm or _has_xgboost

    @property
    def model(self) -> Optional[Any]:
        """底层模型（未训练时为 None）。"""
        return self._model

    # ─── 训练与预测 ──────────────────────────────────────

    def fit(self, X: Any, y: Any, **kwargs: Any) -> "MLSignalModel":
        """训练模型。缺依赖抛 ModelNotAvailableError。"""
        if self.kind == ModelKind.ENSEMBLE:
            self._fit_ensemble(X, y, **kwargs)
            return self
        base = self._build()
        base.fit(X, y, **kwargs)
        self._model = base
        return self

    def predict(self, X: Any) -> np.ndarray:
        """预测。未训练或缺依赖时抛异常。"""
        if self._model is None:
            raise ModelNotAvailableError("模型未训练或依赖缺失，无法预测")
        if self.kind == ModelKind.ENSEMBLE:
            preds = []
            for sub in self._sub_models:
                preds.append(np.asarray(sub.predict(X), dtype=float))
            if not preds:
                raise ModelNotAvailableError("ensemble 无子模型")
            return np.mean(preds, axis=0)
        return np.asarray(self._model.predict(X), dtype=float)

    # ─── Ensemble 内部实现 ───────────────────────────────

    def _fit_ensemble(self, X: Any, y: Any, **kwargs: Any) -> None:
        """等权集成：训练所有可用的子模型，取预测均值。"""
        self._sub_models: list[Any] = []
        if _has_lightgbm:
            from lightgbm import LGBMRegressor

            self._sub_models.append(
                LGBMRegressor(
                    n_estimators=self.params.get("n_estimators", 200),
                    learning_rate=self.params.get("learning_rate", 0.05),
                    random_state=self.seed,
                    verbose=-1,
                ).fit(X, y, **kwargs)
            )
        if _has_xgboost:
            from xgboost import XGBRegressor

            self._sub_models.append(
                XGBRegressor(
                    n_estimators=self.params.get("n_estimators", 200),
                    learning_rate=self.params.get("learning_rate", 0.05),
                    max_depth=self.params.get("max_depth", 6),
                    random_state=self.seed,
                ).fit(X, y, **kwargs)
            )
        self._model = "ensemble"  # 哨兵值标记已训练


def create_signal_model(
    kind: ModelKind | str = "lightgbm",
    params: Optional[dict[str, Any]] = None,
    seed: int = 42,
) -> Optional[MLSignalModel]:
    """创建 ML 信号模型。

    Args:
        kind: 模型类型（lightgbm / xgboost / ensemble）
        params: 模型超参（可选）
        seed: 随机种子

    Returns:
        模型实例；底层依赖缺失时返回 ``None``（调用方降级回退）。
    """
    model = MLSignalModel(kind=kind, params=params, seed=seed)
    if not model.is_available:
        logger.warning("[ML] %s 依赖未安装，返回 None（降级回退）", kind)
        return None
    return model


__all__ = [
    "ModelKind",
    "ModelNotAvailableError",
    "MLSignalModel",
    "create_signal_model",
]
