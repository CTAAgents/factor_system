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
                idx = perm[start : start + self.batch_size]
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
            n_samples,
            n_features,
            self.hidden,
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
            raise ModelNotAvailableError(f"特征与目标样本数不一致: {X_arr.shape[0]} vs {y_arr.shape[0]}")
        if X_arr.shape[0] < self.min_samples:
            raise ModelNotAvailableError(f"样本数 {X_arr.shape[0]} 低于最小要求 {self.min_samples}，降级回退")
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


# ─── GRU 因子模型（GAP-I203，v2.73.0）──────────────────────


class GRUFactorModel:
    """轻量纯 numpy 单层 GRU 因子模型（GAP-I203 深度因子学习首期）。

    不引入 torch/tensorflow 等重依赖，用 numpy 实现单层 GRU
    （update gate / reset gate / candidate hidden），对滚动窗口序列
    提取时序特征，输出层做横截面收益预测，预测映射为因子信号。

    设计原则（与 MLPFactorModel 对齐）:
        - 纯 numpy 实现，无可选依赖，``is_available`` 恒为 True
        - 输入 X 形状 (n_samples, seq_len, n_features)——滚动窗口序列；
          训练前按窗口内 z-score 标准化，预测用同一标准化
        - 样本数不足 ``min_samples`` 时抛 ``ModelNotAvailableError``，
          调用方据此降级回退
        - 接口统一为 ``fit(X, y)`` / ``predict(X)``，与 sklearn 风格一致
        - 参数可导出（``get_params``），供深度因子 code 序列化内嵌
          （可解释性约束：输出映射为因子信号，权重固化可审计）

    GRU 前向（t=1..T，h0=0）:
        z_t = σ(Wz·x_t + Uz·h_{t-1} + bz)      # update gate
        r_t = σ(Wr·x_t + Ur·h_{t-1} + br)      # reset gate
        h̃_t = tanh(Wh·x_t + Uh·(r_t⊙h_{t-1}) + bh)
        h_t = (1 - z_t)⊙h_{t-1} + z_t⊙h̃_t
        pred = Wo·h_T + bo
    """

    def __init__(
        self,
        hidden: int = 8,
        learning_rate: float = 0.01,
        epochs: int = 120,
        batch_size: int = 32,
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

        self._Wz: Optional[np.ndarray] = None
        self._Uz: Optional[np.ndarray] = None
        self._bz: Optional[np.ndarray] = None
        self._Wr: Optional[np.ndarray] = None
        self._Ur: Optional[np.ndarray] = None
        self._br: Optional[np.ndarray] = None
        self._Wh: Optional[np.ndarray] = None
        self._Uh: Optional[np.ndarray] = None
        self._bh: Optional[np.ndarray] = None
        self._Wo: Optional[np.ndarray] = None
        self._bo: Optional[np.ndarray] = None
        self._x_mean: Optional[np.ndarray] = None
        self._x_std: Optional[np.ndarray] = None
        self._fitted = False

    @property
    def is_available(self) -> bool:
        """纯 numpy 实现，无重依赖，恒可用。"""
        return True

    @property
    def seq_len(self) -> int:
        """输入序列长度（由 fit 时 X 推断）。"""
        if not self._fitted:
            raise ModelNotAvailableError("GRU 因子模型未训练，无法获取 seq_len")
        assert self._x_mean is not None
        return int(self._x_mean.shape[0])

    @property
    def n_features(self) -> int:
        """单步特征数（由 fit 时 X 推断）。"""
        if not self._fitted:
            raise ModelNotAvailableError("GRU 因子模型未训练，无法获取 n_features")
        assert self._x_mean is not None
        return int(self._x_mean.shape[1])

    # ─── 训练与预测 ──────────────────────────────────────

    def fit(self, X: Any, y: Any, **kwargs: Any) -> "GRUFactorModel":
        """训练 GRU 因子模型（BPTT + 动量 SGD）。

        Args:
            X: 特征序列 (n_samples, seq_len, n_features)
            y: 目标向量 (n_samples,)

        Returns:
            self

        Raises:
            ModelNotAvailableError: 样本数不足 min_samples 或输入非数值
        """
        X_arr, y_arr = self._validate(X, y)
        n_samples, seq_len, n_features = X_arr.shape

        rng = np.random.default_rng(self.seed)
        scale = 1.0 / np.sqrt(max(n_features, 1))
        self._Wz = rng.normal(0.0, scale, (n_features, self.hidden))
        self._Uz = rng.normal(0.0, scale, (self.hidden, self.hidden))
        self._bz = np.zeros(self.hidden)
        self._Wr = rng.normal(0.0, scale, (n_features, self.hidden))
        self._Ur = rng.normal(0.0, scale, (self.hidden, self.hidden))
        self._br = np.zeros(self.hidden)
        self._Wh = rng.normal(0.0, scale, (n_features, self.hidden))
        self._Uh = rng.normal(0.0, scale, (self.hidden, self.hidden))
        self._bh = np.zeros(self.hidden)
        self._Wo = rng.normal(0.0, scale, (self.hidden, 1))
        self._bo = np.zeros(1)

        X_norm = (X_arr - self._x_mean) / self._x_std
        # 动量缓冲区
        momentum = 0.9
        vel: dict[str, np.ndarray] = {}
        for name in ("Wz", "Uz", "bz", "Wr", "Ur", "br", "Wh", "Uh", "bh", "Wo", "bo"):
            vel[name] = np.zeros_like(getattr(self, f"_{name}"))

        for _ in range(self.epochs):
            perm = rng.permutation(n_samples)
            for start in range(0, n_samples, self.batch_size):
                idx = perm[start : start + self.batch_size]
                Xb, yb = X_norm[idx], y_arr[idx]
                grads = self._backward(Xb, yb)
                for name, g in grads.items():
                    vel[name] = momentum * vel[name] - self.learning_rate * g
                    param = getattr(self, f"_{name}")
                    param += vel[name]

        self._fitted = True
        logger.info(
            "[ML] GRU 因子模型训练完成: samples=%d seq=%d features=%d hidden=%d",
            n_samples,
            seq_len,
            n_features,
            self.hidden,
        )
        return self

    def predict(self, X: Any) -> np.ndarray:
        """预测。未训练时抛 ModelNotAvailableError。"""
        if not self._fitted:
            raise ModelNotAvailableError("GRU 因子模型未训练，无法预测")
        X_arr = self._to_float_array(X)
        if X_arr.ndim != 3:
            raise ModelNotAvailableError(f"预测输入需为 3D 序列 (n, seq, f)，收到 {X_arr.ndim}D")
        if X_arr.shape[1:] != (self.seq_len, self.n_features):
            raise ModelNotAvailableError(
                f"输入序列形状不匹配: 期望 (n,{self.seq_len},{self.n_features})，收到 {X_arr.shape}"
            )
        X_norm = (X_arr - self._x_mean) / self._x_std
        preds = []
        for x in X_norm:
            preds.append(self._forward_single(x)[0])
        return np.asarray(preds, dtype=float).ravel()

    # ─── 内部实现 ────────────────────────────────────────

    def _forward_single(self, x: np.ndarray) -> tuple[float, np.ndarray]:
        """单样本前向，返回 (预测值, 末隐状态)。x 形状 (seq_len, n_features)。"""
        h = np.zeros(self.hidden)
        for t in range(x.shape[0]):
            xt = x[t]
            z = self._sigmoid(xt @ self._Wz + h @ self._Uz + self._bz)
            r = self._sigmoid(xt @ self._Wr + h @ self._Ur + self._br)
            h_tilde = np.tanh(xt @ self._Wh + (r * h) @ self._Uh + self._bh)
            h = (1.0 - z) * h + z * h_tilde
        pred = float((h @ self._Wo + self._bo).item())
        return pred, h

    def _backward(
        self,
        Xb: np.ndarray,
        yb: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """小批量 BPTT 反向传播，返回各参数梯度（含 L2 正则）。

        Args:
            Xb: 标准化序列 (b, seq, f)
            yb: 目标 (b,)
        """
        b, seq, f = Xb.shape
        hdim = self.hidden

        # 梯度累积
        dWz = np.zeros_like(self._Wz)
        dUz = np.zeros_like(self._Uz)
        dbz = np.zeros_like(self._bz)
        dWr = np.zeros_like(self._Wr)
        dUr = np.zeros_like(self._Ur)
        dbr = np.zeros_like(self._br)
        dWh = np.zeros_like(self._Wh)
        dUh = np.zeros_like(self._Uh)
        dbh = np.zeros_like(self._bh)
        dWo = np.zeros_like(self._Wo)
        dbo = np.zeros_like(self._bo)

        for s in range(b):
            x = Xb[s]
            target = yb[s]
            # 前向缓存
            hs = np.zeros((seq + 1, hdim))
            zs = np.zeros((seq, hdim))
            rs = np.zeros((seq, hdim))
            hts = np.zeros((seq, hdim))
            for t in range(seq):
                xt = x[t]
                z = self._sigmoid(xt @ self._Wz + hs[t] @ self._Uz + self._bz)
                r = self._sigmoid(xt @ self._Wr + hs[t] @ self._Ur + self._br)
                ht = np.tanh(xt @ self._Wh + (r * hs[t]) @ self._Uh + self._bh)
                zs[t], rs[t], hts[t] = z, r, ht
                hs[t + 1] = (1.0 - z) * hs[t] + z * ht

            # 输出层梯度
            pred = hs[seq] @ self._Wo + self._bo
            d_pred = float((pred - target).item())
            dWo += np.outer(hs[seq], np.asarray([d_pred]))
            dbo += d_pred

            # 末隐状态梯度反传
            assert self._Wo is not None
            dh = d_pred * self._Wo.ravel()
            for t in range(seq - 1, -1, -1):
                z = zs[t]
                r = rs[t]
                ht = hts[t]
                h_prev = hs[t]
                # dh 关于 z/ht/h_prev
                dz = dh * (ht - h_prev)
                dht = dh * z
                dh_prev_direct = dh * (1.0 - z)
                # z 门（sigmoid 导数）
                dz_in = dz * z * (1.0 - z)
                dWz += np.outer(x[t], dz_in)
                dUz += np.outer(h_prev, dz_in)
                dbz += dz_in
                # candidate hidden（tanh 导数）
                d_ht_in = dht * (1.0 - ht**2)
                dWh += np.outer(x[t], d_ht_in)
                dUh += np.outer(r * h_prev, d_ht_in)
                dbh += d_ht_in
                # r 门
                d_r_inner = d_ht_in @ self._Uh * h_prev
                dr_in = d_r_inner * r * (1.0 - r)
                dWr += np.outer(x[t], dr_in)
                dUr += np.outer(h_prev, dr_in)
                dbr += dr_in
                # h_{t-1} 全梯度
                dh = dh_prev_direct + dz_in @ self._Uz + dr_in @ self._Ur + (d_ht_in @ self._Uh) * r

        # 小批量平均 + L2 正则
        n = max(b, 1)
        reg = self.l2 / n
        return {
            "Wz": dWz / n + reg * self._Wz,
            "Uz": dUz / n + reg * self._Uz,
            "bz": dbz / n,
            "Wr": dWr / n + reg * self._Wr,
            "Ur": dUr / n + reg * self._Ur,
            "br": dbr / n,
            "Wh": dWh / n + reg * self._Wh,
            "Uh": dUh / n + reg * self._Uh,
            "bh": dbh / n,
            "Wo": dWo / n + reg * self._Wo,
            "bo": dbo / n,
        }

    @staticmethod
    def _sigmoid(x: np.ndarray) -> np.ndarray:
        """数值稳定 sigmoid。"""
        return 1.0 / (1.0 + np.exp(-np.clip(x, -50.0, 50.0)))

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
        if X_arr.ndim != 3:
            raise ModelNotAvailableError(f"特征需为 3D 序列 (n, seq, f)，收到 {X_arr.ndim}D")
        if X_arr.shape[0] != y_arr.shape[0]:
            raise ModelNotAvailableError(f"特征与目标样本数不一致: {X_arr.shape[0]} vs {y_arr.shape[0]}")
        if X_arr.shape[0] < self.min_samples:
            raise ModelNotAvailableError(f"样本数 {X_arr.shape[0]} 低于最小要求 {self.min_samples}，降级回退")
        self._x_mean = X_arr.mean(axis=0)  # (seq, f)
        self._x_std = X_arr.std(axis=0)
        self._x_std[self._x_std < 1e-12] = 1.0
        return X_arr, y_arr

    def get_params(self) -> dict[str, np.ndarray]:
        """导出训练参数（权重），供深度因子 code 序列化内嵌（GAP-I203）。

        Returns:
            dict: 权重名称 → numpy 数组（未训练时抛 ModelNotAvailableError）
        """
        if not self._fitted:
            raise ModelNotAvailableError("GRU 因子模型未训练，无法导出参数")
        return {
            name: np.array(getattr(self, f"_{name}"), copy=True)
            for name in ("Wz", "Uz", "bz", "Wr", "Ur", "br", "Wh", "Uh", "bh", "Wo", "bo")
        }


def create_gru_model(params: Optional[dict[str, Any]] = None) -> GRUFactorModel:
    """创建 GRU 因子模型（GAP-I203 深度因子学习）。

    Args:
        params: 超参（hidden/learning_rate/epochs/batch_size/l2/seed/min_samples）

    Returns:
        GRUFactorModel 实例（纯 numpy 实现，恒可用）。
    """
    return GRUFactorModel(**(params or {}))


# ─── Transformer 因子模型（C5，v2.100.1）────────────────────


class TransformerFactorModel:
    """轻量纯 numpy 单头自注意力因子模型（C5）。

    不引入 torch/tensorflow 等重依赖，用 numpy 实现单层单头自注意力
    （Q/K/V 投影 + 因果掩码 + 简化 LayerNorm），对滚动窗口序列提取
    时序特征，取最后时间步输出预测，映射为因子信号。

    设计原则（与 GRUFactorModel 对齐）:
        - 纯 numpy 实现，无可选依赖，``is_available`` 恒为 True
        - 输入 X 形状 (n_samples, seq_len, n_features)；训练前按窗口 z-score
        - **因果掩码**（上三角 −inf）：t 时刻输出只依赖 ≤t 输入，天然零未来函数
        - 样本不足 ``min_samples`` 抛 ``ModelNotAvailableError`` 降级
        - 参数可导出（``get_params``）供因子 code 序列化内嵌

    Transformer 前向（单头、单层）:
        Q = X·Wq, K = X·Wk, V = X·Wv
        attn = softmax(QK'/√d + causal_mask)          # 因果掩码
        ctx  = attn·V + pos_emb                        # 位置编码
        ctx_ln = LayerNorm(ctx)                        # 简化 LN（减均值除 std）
        pred = tanh(ctx_ln[:, -1, :]·Wo + bo)          # 取最后时间步
    """

    def __init__(
        self,
        hidden: int = 8,
        learning_rate: float = 0.01,
        epochs: int = 120,
        l2: float = 1e-4,
        seed: int = 42,
        min_samples: int = 32,
    ) -> None:
        self.hidden = int(hidden)
        self.learning_rate = float(learning_rate)
        self.epochs = int(epochs)
        self.l2 = float(l2)
        self.seed = int(seed)
        self.min_samples = int(min_samples)

        self._Wq: Optional[np.ndarray] = None
        self._Wk: Optional[np.ndarray] = None
        self._Wv: Optional[np.ndarray] = None
        self._Wo: Optional[np.ndarray] = None
        self._bo: Optional[np.ndarray] = None
        self._pos_emb: Optional[np.ndarray] = None
        self._x_mean: Optional[np.ndarray] = None
        self._x_std: Optional[np.ndarray] = None
        self._fitted = False

    @property
    def is_available(self) -> bool:
        """纯 numpy 实现，无重依赖，恒可用。"""
        return True

    @property
    def seq_len(self) -> int:
        if not self._fitted:
            raise ModelNotAvailableError("Transformer 因子模型未训练，无法获取 seq_len")
        assert self._x_mean is not None
        return int(self._x_mean.shape[0])

    @property
    def n_features(self) -> int:
        if not self._fitted:
            raise ModelNotAvailableError("Transformer 因子模型未训练，无法获取 n_features")
        assert self._x_mean is not None
        return int(self._x_mean.shape[1])

    # ─── 训练与预测 ──────────────────────────────────────

    def fit(self, X: Any, y: Any, **kwargs: Any) -> "TransformerFactorModel":
        """训练单头自注意力模型（动量 SGD + L2）。

        Args:
            X: 特征序列 (n_samples, seq_len, n_features)
            y: 目标向量 (n_samples,)

        Returns:
            self

        Raises:
            ModelNotAvailableError: 样本数不足 min_samples 或输入非数值
        """
        X_arr, y_arr = self._validate(X, y)
        n_samples, seq_len, n_features = X_arr.shape

        rng = np.random.default_rng(self.seed)
        scale = 1.0 / np.sqrt(max(n_features, 1))
        self._Wq = rng.normal(0.0, scale, (n_features, self.hidden))
        self._Wk = rng.normal(0.0, scale, (n_features, self.hidden))
        self._Wv = rng.normal(0.0, scale, (n_features, self.hidden))
        self._Wo = rng.normal(0.0, scale, (self.hidden, 1))
        self._bo = np.zeros(1)
        self._pos_emb = rng.normal(0.0, scale, (seq_len, self.hidden))

        X_norm = (X_arr - self._x_mean) / self._x_std
        momentum = 0.9
        vel: dict[str, np.ndarray] = {}
        for name in ("Wq", "Wk", "Wv", "Wo", "bo", "pos_emb"):
            vel[name] = np.zeros_like(getattr(self, f"_{name}"))

        for _ in range(self.epochs):
            grads = self._backward(X_norm, y_arr)
            for name, g in grads.items():
                vel[name] = momentum * vel[name] - self.learning_rate * g
                param = getattr(self, f"_{name}")
                param += vel[name]

        self._fitted = True
        logger.info(
            "[ML] Transformer 因子模型训练完成: samples=%d seq=%d features=%d hidden=%d",
            n_samples,
            seq_len,
            n_features,
            self.hidden,
        )
        return self

    def predict(self, X: Any) -> np.ndarray:
        """预测。未训练时抛 ModelNotAvailableError。"""
        if not self._fitted:
            raise ModelNotAvailableError("Transformer 因子模型未训练，无法预测")
        X_arr = self._to_float_array(X)
        if X_arr.ndim != 3:
            raise ModelNotAvailableError(f"预测输入需为 3D 序列 (n, seq, f)，收到 {X_arr.ndim}D")
        if X_arr.shape[1:] != (self.seq_len, self.n_features):
            raise ModelNotAvailableError(
                f"输入序列形状不匹配: 期望 (n,{self.seq_len},{self.n_features})，收到 {X_arr.shape}"
            )
        X_norm = (X_arr - self._x_mean) / self._x_std
        out, _ = self._forward(X_norm)
        return np.asarray(out, dtype=float).ravel()

    # ─── 内部实现 ────────────────────────────────────────

    def _forward(self, X: np.ndarray) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """批量前向。返回 (pred (b,), cache)。"""
        assert self._Wq is not None and self._Wo is not None and self._pos_emb is not None
        b, seq, _ = X.shape
        d = self.hidden
        Q = X @ self._Wq  # (b, seq, d)
        K = X @ self._Wk
        V = X @ self._Wv
        logits = Q @ K.transpose(0, 2, 1) / np.sqrt(d)  # (b, seq, seq)
        # 因果掩码：上三角 −inf（t 时刻只用 ≤t）
        mask = np.triu(np.ones((seq, seq)), k=1)
        logits = logits - 1e9 * mask
        e = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
        attn = e / np.sum(e, axis=-1, keepdims=True)
        ctx = attn @ V + self._pos_emb[:seq]  # (b, seq, d)
        mean = ctx.mean(axis=-1, keepdims=True)
        std = ctx.std(axis=-1, keepdims=True) + 1e-6
        ctx_ln = (ctx - mean) / std
        out = np.tanh(ctx_ln @ self._Wo + self._bo)  # (b, seq, 1)
        cache = {
            "X": X,
            "Q": Q,
            "K": K,
            "V": V,
            "logits": logits,
            "attn": attn,
            "ctx": ctx,
            "mean": mean,
            "std": std,
            "ctx_ln": ctx_ln,
        }
        return out[:, -1, 0], cache

    def _backward(
        self,
        X_norm: np.ndarray,
        y: np.ndarray,
    ) -> dict[str, np.ndarray]:
        """解析梯度（仅最后时间步贡献 loss）。"""
        b, seq, _ = X_norm.shape
        d = self.hidden
        pred, cache = self._forward(X_norm)
        X, Q, K, V, attn, std, ctx_ln = (
            cache["X"], cache["Q"], cache["K"], cache["V"],
            cache["attn"], cache["std"], cache["ctx_ln"],
        )

        d_out = 2.0 * (pred - y) / b  # MSE 梯度（除批次）
        d_tanh = d_out * (1.0 - pred ** 2)  # (b,)
        assert self._Wo is not None
        d_Wo = ctx_ln[:, -1, :].T @ d_tanh[:, None]  # (d, 1)
        d_bo = np.array([float(np.sum(d_tanh))])
        d_ctx_ln_last = d_tanh[:, None] * self._Wo.T  # (b, d)

        # LayerNorm 反向（最后位置）
        ctx_ln_last = ctx_ln[:, -1, :]
        d_ctx_last = (d_ctx_ln_last - d_ctx_ln_last.mean(axis=-1, keepdims=True)
                      - ctx_ln_last * (d_ctx_ln_last * ctx_ln_last).mean(axis=-1, keepdims=True)) / std[:, -1, :]

        # 位置编码梯度（最后位置）
        d_pos_emb = np.zeros_like(self._pos_emb)
        d_pos_emb[seq - 1] = d_ctx_last.sum(axis=0)

        # 注意力反向（仅最后位置有梯度）
        a_last = attn[:, -1, :]  # (b, seq)
        dV = a_last.T @ d_ctx_last  # (seq, d)（聚合 batch）
        # 逐样本 score：score_b = d_ctx_last_b @ V_b^T（避免 matmul batch 广播歧义）
        score = np.einsum("bd,bsd->bs", d_ctx_last, V)  # (b, seq)
        d_logits_last = a_last * (score - (a_last * score).sum(axis=-1, keepdims=True))
        # 逐样本聚合：避免 2D @ 3D matmul 广播歧义（会得到 (b,b,d)）
        dQ_last = np.einsum("bs,bsd->bd", d_logits_last, K) / np.sqrt(d)  # (b, d)
        dK = (d_logits_last.T @ Q[:, -1, :]) / np.sqrt(d)  # (seq, d)

        X_last = X[:, -1, :]  # (b, f)
        d_Wq = X_last.T @ dQ_last  # (f, d)
        d_Wk = np.einsum("bsf,sd->fd", X, dK)  # sum_b X_b^T @ dK
        d_Wv = np.einsum("bsf,sd->fd", X, dV)

        # L2 正则（不计入 pos_emb）
        reg = self.l2 / b
        n = max(b, 1)
        return {
            "Wq": d_Wq / n + reg * self._Wq,
            "Wk": d_Wk / n + reg * self._Wk,
            "Wv": d_Wv / n + reg * self._Wv,
            "Wo": d_Wo / n + reg * self._Wo,
            "bo": d_bo / n,
            "pos_emb": d_pos_emb / n,
        }

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
        if X_arr.ndim != 3:
            raise ModelNotAvailableError(f"特征需为 3D 序列 (n, seq, f)，收到 {X_arr.ndim}D")
        if X_arr.shape[0] != y_arr.shape[0]:
            raise ModelNotAvailableError(f"特征与目标样本数不一致: {X_arr.shape[0]} vs {y_arr.shape[0]}")
        if X_arr.shape[0] < self.min_samples:
            raise ModelNotAvailableError(f"样本数 {X_arr.shape[0]} 低于最小要求 {self.min_samples}，降级回退")
        self._x_mean = X_arr.mean(axis=0)  # (seq, f)
        self._x_std = X_arr.std(axis=0)
        self._x_std[self._x_std < 1e-12] = 1.0
        return X_arr, y_arr

    def get_params(self) -> dict[str, np.ndarray]:
        """导出训练参数（权重），供因子 code 序列化内嵌（C5）。"""
        if not self._fitted:
            raise ModelNotAvailableError("Transformer 因子模型未训练，无法导出参数")
        return {
            name: np.array(getattr(self, f"_{name}"), copy=True)
            for name in ("Wq", "Wk", "Wv", "Wo", "bo", "pos_emb")
        }


def create_transformer_model(params: Optional[dict[str, Any]] = None) -> TransformerFactorModel:
    """创建 Transformer 因子模型（C5 深度因子学习二期）。

    Args:
        params: 超参（hidden/learning_rate/epochs/l2/seed/min_samples）

    Returns:
        TransformerFactorModel 实例（纯 numpy 实现，恒可用）。
    """
    return TransformerFactorModel(**(params or {}))


__all__ = [
    "ModelKind",
    "ModelNotAvailableError",
    "MLSignalModel",
    "create_signal_model",
    "MLPFactorModel",
    "create_mlp_model",
    "GRUFactorModel",
    "create_gru_model",
    "TransformerFactorModel",
    "create_transformer_model",
]
