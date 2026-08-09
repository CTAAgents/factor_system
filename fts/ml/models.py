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
    MLP = "mlp"


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


# ─── MLP 因子模型（GAP-F05，v2.60.0）────────────────────────


class MLPFactorModel:
    """轻量纯 numpy MLP 因子模型（GAP-F05 深度时序模型）。

    不引入 torch/tensorflow 等重依赖，用 numpy 实现单隐层 MLP，
    用于横截面收益预测（AlphaNet 式神经网络因子的轻量起步）。

    设计原则:
        - 纯 numpy 实现，无可选依赖，``is_available`` 恒为 True
        - 训练前对输入做 z-score 标准化，预测时用同一标准化逆变换
        - 样本数不足 ``min_samples`` 时抛 ``ModelNotAvailableError``，
          调用方据此降级回退（对齐现有 create_signal_model 契约）
        - 接口统一为 ``fit(X, y)`` / ``predict(X)``，与 sklearn 风格一致
    """

    def __init__(
        self,
        hidden: int = 16,
        learning_rate: float = 0.01,
        epochs: int = 200,
        batch_size: int = 64,
        l2: float = 1e-4,
        seed: int = 42,
        min_samples: int = 32,
    ) -> None:
        self.hidden = int(hidden)
        self.learning_rate = float(learning_rate)
        self.epochs = int(epochs)
        self.batch_size = int(batch_size)
        self.l2 = float(l2)
        self.seed = int(seed)
        self.min_samples = int(min_samples)

        self._W1: Optional[np.ndarray] = None
        self._b1: Optional[np.ndarray] = None
        self._W2: Optional[np.ndarray] = None
        self._b2: Optional[np.ndarray] = None
        self._x_mean: Optional[np.ndarray] = None
        self._x_std: Optional[np.ndarray] = None
        self._fitted = False

    @property
    def is_available(self) -> bool:
        """纯 numpy 实现，无重依赖，恒可用。"""
        return True

    # ─── 训练与预测 ──────────────────────────────────────

    def fit(self, X: Any, y: Any, **kwargs: Any) -> "MLPFactorModel":
        """训练 MLP 因子模型。

        Args:
            X: 特征矩阵 (n_samples, n_features)，可为 pd.DataFrame/np.ndarray
            y: 目标向量 (n_samples,)

        Returns:
            self

        Raises:
            ModelNotAvailableError: 样本数不足 min_samples 或输入非数值
        """
        X_arr, y_arr = self._validate(X, y)
        n_samples, n_features = X_arr.shape

        rng = np.random.default_rng(self.seed)
        scale = 1.0 / np.sqrt(n_features) if n_features > 0 else 1.0
        self._W1 = rng.normal(0.0, scale, size=(n_features, self.hidden))
        self._b1 = np.zeros(self.hidden)
        self._W2 = rng.normal(0.0, scale, size=(self.hidden, 1))
        self._b2 = np.zeros(1)

        X_norm = (X_arr - self._x_mean) / self._x_std
        v_w1 = np.zeros_like(self._W1)
        v_b1 = np.zeros_like(self._b1)
        v_w2 = np.zeros_like(self._W2)
        v_b2 = np.zeros_like(self._b2)
        momentum = 0.9

        for _ in range(self.epochs):
            perm = rng.permutation(n_samples)
            for start in range(0, n_samples, self.batch_size):
                idx = perm[start:start + self.batch_size]
                Xb, yb = X_norm[idx], y_arr[idx]

                # forward
                h = np.maximum(0.0, Xb @ self._W1 + self._b1)  # ReLU
                pred = h @ self._W2 + self._b2

                # backward（MSE + L2 正则）
                d_pred = (pred - yb.reshape(-1, 1)) * 2.0 / max(len(idx), 1)
                d_w2 = h.T @ d_pred + self.l2 * self._W2
                d_b2 = d_pred.sum(axis=0)
                d_h = d_pred @ self._W2.T
                d_h[h <= 0.0] = 0.0  # ReLU 梯度
                d_w1 = Xb.T @ d_h + self.l2 * self._W1
                d_b1 = d_h.sum(axis=0)

                # 带动量更新
                v_w2 = momentum * v_w2 - self.learning_rate * d_w2
                self._W2 += v_w2
                v_b2 = momentum * v_b2 - self.learning_rate * d_b2
                self._b2 += v_b2
                v_w1 = momentum * v_w1 - self.learning_rate * d_w1
                self._W1 += v_w1
                v_b1 = momentum * v_b1 - self.learning_rate * d_b1
                self._b1 += v_b1

        self._fitted = True
        logger.info(
            "[ML] MLP 因子模型训练完成: samples=%d features=%d hidden=%d",
            n_samples, n_features, self.hidden,
        )
        return self

    def predict(self, X: Any) -> np.ndarray:
        """预测。未训练时抛 ModelNotAvailableError。"""
        if not self._fitted:
            raise ModelNotAvailableError("MLP 因子模型未训练，无法预测")
        X_arr = self._to_float_array(X)
        if X_arr.ndim != 2:
            raise ModelNotAvailableError(f"预测输入需为 2D 矩阵，收到 {X_arr.ndim}D")
        X_norm = (X_arr - self._x_mean) / self._x_std
        h = np.maximum(0.0, X_norm @ self._W1 + self._b1)
        return np.asarray(h @ self._W2 + self._b2, dtype=float).ravel()

    # ─── 内部工具 ────────────────────────────────────────

    @staticmethod
    def _to_float_array(X: Any) -> np.ndarray:
        """将输入转为 float64 numpy 数组，非数值抛 ModelNotAvailableError。"""
        try:
            arr = np.asarray(X, dtype=float)
        except (TypeError, ValueError) as e:
            raise ModelNotAvailableError(f"输入含非数值数据: {e}") from e
        return arr

    def _validate(self, X: Any, y: Any) -> tuple[np.ndarray, np.ndarray]:
        """校验并标准化训练输入。"""
        X_arr = self._to_float_array(X)
        y_arr = self._to_float_array(y)
        if X_arr.ndim != 2:
            raise ModelNotAvailableError(f"特征需为 2D 矩阵，收到 {X_arr.ndim}D")
        if X_arr.shape[0] != y_arr.shape[0]:
            raise ModelNotAvailableError(
                f"特征与目标样本数不一致: {X_arr.shape[0]} vs {y_arr.shape[0]}"
            )
        if X_arr.shape[0] < self.min_samples:
            raise ModelNotAvailableError(
                f"样本数 {X_arr.shape[0]} 低于最小要求 {self.min_samples}，降级回退"
            )
        self._x_mean = X_arr.mean(axis=0)
        self._x_std = X_arr.std(axis=0)
        self._x_std[self._x_std < 1e-12] = 1.0  # 常数列不缩放
        return X_arr, y_arr


def create_mlp_model(params: Optional[dict[str, Any]] = None) -> MLPFactorModel:
    """创建 MLP 因子模型。

    Args:
        params: 超参（hidden/learning_rate/epochs/batch_size/l2/seed/min_samples）

    Returns:
        MLPFactorModel 实例（纯 numpy 实现，恒可用）。
    """
    return MLPFactorModel(**(params or {}))


__all__ = [
    "ModelKind",
    "ModelNotAvailableError",
    "MLSignalModel",
    "create_signal_model",
    "MLPFactorModel",
    "create_mlp_model",
]
