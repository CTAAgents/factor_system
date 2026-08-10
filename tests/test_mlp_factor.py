"""
tests/test_mlp_factor.py — GAP-F05 深度时序模型（轻量纯 numpy MLP 因子）测试。

覆盖:
    1. 训练 + 预测形状与学习能力（线性目标拟合）
    2. 输入标准化与常数列保护
    3. 降级路径：样本数不足 / 未训练 / 非数值输入 / 维度不匹配
    4. create_mlp_model 工厂 + 超参透传 + 可复现性
"""

from __future__ import annotations

import numpy as np
import pytest

from fts.ml import MLPFactorModel, ModelKind, ModelNotAvailableError, create_mlp_model


def _make_data(n: int = 300, n_features: int = 4, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.normal(size=(n, n_features))
    # 线性目标 y = 2x1 - 3x2 + 0.5x3 + 噪声
    y = 2.0 * X[:, 0] - 3.0 * X[:, 1] + 0.5 * X[:, 2] + rng.normal(scale=0.1, size=n)
    return X, y


class TestMLPFactorModel:
    def test_fit_predict_shape_and_learning(self) -> None:
        """训练后预测形状正确，且能学到线性目标的大致方向。"""
        X, y = _make_data()
        model = MLPFactorModel(epochs=300, hidden=24, seed=42)
        model.fit(X, y)
        pred = model.predict(X)
        assert pred.shape == (len(X),)
        assert np.all(np.isfinite(pred))
        # 预测与目标的相关性应显著为正（学习到了信号）
        corr = np.corrcoef(pred, y)[0, 1]
        assert corr > 0.6, f"MLP 学习效果差: corr={corr:.3f}"

    def test_predict_after_fit_matches_forward(self) -> None:
        """预测与手动 forward（标准化 + 两层）一致，验证内部状态。"""
        X, y = _make_data(n=200, n_features=3)
        model = MLPFactorModel(epochs=5, seed=1)
        model.fit(X, y)
        pred = model.predict(X)
        X_norm = (X - model._x_mean) / model._x_std
        h = np.maximum(0.0, X_norm @ model._W1 + model._b1)
        manual = (h @ model._W2 + model._b2).ravel()
        np.testing.assert_allclose(pred, manual, atol=1e-10)

    def test_constant_feature_column_not_scaled(self) -> None:
        """常数列 std=0 时应置为 1.0，不产生除零。"""
        X, y = _make_data(n=150, n_features=3)
        X[:, 0] = 5.0  # 常数特征
        model = MLPFactorModel(epochs=3, seed=3)
        model.fit(X, y)
        assert model._x_std[0] == 1.0
        assert np.all(np.isfinite(model.predict(X)))

    def test_insufficient_samples_raises(self) -> None:
        """样本数不足 min_samples → ModelNotAvailableError（降级路径）。"""
        X, y = _make_data(n=10)
        model = MLPFactorModel(min_samples=32)
        with pytest.raises(ModelNotAvailableError, match="低于最小要求"):
            model.fit(X, y)

    def test_predict_before_fit_raises(self) -> None:
        """未训练直接预测 → ModelNotAvailableError。"""
        model = MLPFactorModel()
        with pytest.raises(ModelNotAvailableError, match="未训练"):
            model.predict(np.zeros((5, 3)))

    def test_non_numeric_input_raises(self) -> None:
        """非数值输入 → ModelNotAvailableError。"""
        X, y = _make_data(n=100)
        model = MLPFactorModel(epochs=2, seed=4)
        model.fit(X, y)
        bad = [["a", "b", "c", "d"]]
        with pytest.raises(ModelNotAvailableError, match="非数值"):
            model.predict(bad)

    def test_dim_mismatch_raises(self) -> None:
        """特征与目标样本数不一致 → ModelNotAvailableError。"""
        X, y = _make_data(n=100, n_features=3)
        model = MLPFactorModel(epochs=2)
        with pytest.raises(ModelNotAvailableError, match="不一致"):
            model.fit(X, y[:50])

    def test_predict_input_must_be_2d(self) -> None:
        """1D 输入预测 → ModelNotAvailableError。"""
        X, y = _make_data(n=100, n_features=3)
        model = MLPFactorModel(epochs=2, seed=5)
        model.fit(X, y)
        with pytest.raises(ModelNotAvailableError, match="2D"):
            model.predict(X[:, 0])

    def test_reproducible_with_seed(self) -> None:
        """同 seed 两次训练预测完全一致（可复现）。"""
        X, y = _make_data()
        p1 = MLPFactorModel(epochs=10, seed=9).fit(X, y).predict(X)
        p2 = MLPFactorModel(epochs=10, seed=9).fit(X, y).predict(X)
        np.testing.assert_allclose(p1, p2, atol=1e-12)


class TestCreateMLPModel:
    def test_factory_and_params(self) -> None:
        """工厂创建 + 超参透传 + is_available 恒 True。"""
        model = create_mlp_model({"hidden": 8, "learning_rate": 0.05, "seed": 2})
        assert isinstance(model, MLPFactorModel)
        assert model.hidden == 8
        assert model.learning_rate == 0.05
        assert model.is_available

    def test_factory_default(self) -> None:
        """默认参数创建。"""
        model = create_mlp_model()
        assert model.hidden == 16
        assert model.min_samples == 32

    def test_model_kind_has_mlp(self) -> None:
        """ModelKind 枚举含 MLP 成员。"""
        assert ModelKind.MLP == "mlp"

class TestMLPBoundary:
    """GAP-F16 补充：1D 特征 / 零特征输入边界。"""

    def test_fit_1d_features_raises(self) -> None:
        """fit 时特征为 1D → ModelNotAvailableError。"""
        X = np.linspace(0.0, 1.0, 50)
        y = np.ones(50)
        model = MLPFactorModel(epochs=1)
        with pytest.raises(ModelNotAvailableError, match="2D"):
            model.fit(X, y)

    def test_fit_zero_features_succeeds(self) -> None:
        """0 特征（n, 0）→ scale 兜底 1.0，训练与预测不崩溃。"""
        rng = np.random.default_rng(0)
        X = rng.standard_normal((64, 0))
        y = rng.standard_normal(64)
        model = MLPFactorModel(epochs=2, hidden=4, min_samples=16)
        model.fit(X, y)
        pred = model.predict(X)
        assert pred.shape == (64,)
        assert np.all(np.isfinite(pred))