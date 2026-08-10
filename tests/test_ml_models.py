"""
tests/test_ml_models.py — ML 模型层测试（Phase 24，v2.38.0）。

覆盖:
    - ModelKind / TrainMode 枚举
    - create_signal_model 依赖探测与降级回退
    - MLSignalModel 训练/预测（mock 底层模型）
    - SignalModelTrainer 三种训练模式
    - 边界情况（样本不足 / NaN 清理 / 特征重要性提取）

版本: v1.0.0
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from fts.ml import (
    ModelKind,
    ModelNotAvailableError,
    SignalModelTrainer,
    TrainMode,
    TrainResult,
    create_signal_model,
    MLSignalModel,
)


# ─── Fixtures ─────────────────────────────────────────────


class _FakeRegressor:
    """模拟 sklearn 风格回归器（fit 时用最小二乘自动解算系数）。"""

    def __init__(self, coefs=None):
        self._coefs = coefs

    def fit(self, X, y, **kwargs):
        self.X_ = np.asarray(X, dtype=float)
        self.y_ = np.asarray(y, dtype=float)
        if self._coefs is None:
            self._coefs = np.linalg.lstsq(self.X_, self.y_, rcond=None)[0]
        return self

    def predict(self, X):
        X = np.asarray(X, dtype=float)
        return X @ self._coefs

    @property
    def feature_importances_(self):
        return np.ones(self.X_.shape[1]) if hasattr(self, "X_") else np.array([1.0])


@pytest.fixture
def fake_lightgbm(monkeypatch):
    """注入假 lightgbm 模块（覆盖真实安装与未安装两种情况）。"""
    mod = types.ModuleType("lightgbm")

    def _lgbm_regressor(**kwargs):
        return _FakeRegressor()

    mod.LGBMRegressor = _lgbm_regressor
    monkeypatch.setitem(sys.modules, "lightgbm", mod)
    monkeypatch.setattr("fts.ml.models._has_lightgbm", True)
    return mod


@pytest.fixture
def make_panel_data():
    def _make(n=120, n_feat=3, seed=42):
        rng = np.random.default_rng(seed)
        X = rng.standard_normal((n, n_feat))
        coefs = np.linspace(1.0, -0.5, n_feat)
        y = X @ coefs + rng.standard_normal(n) * 0.1
        return X, y, coefs

    return _make


# ─── 枚举 ─────────────────────────────────────────────────


class TestEnums:
    def test_model_kind_values(self):
        assert ModelKind.LIGHTGBM.value == "lightgbm"
        assert ModelKind.XGBOOST.value == "xgboost"
        assert ModelKind.ENSEMBLE.value == "ensemble"

    def test_train_mode_values(self):
        assert TrainMode.CROSS_SECTIONAL.value == "cross_sectional"
        assert TrainMode.TIME_SERIES.value == "time_series"
        assert TrainMode.ENSEMBLE_FUSION.value == "ensemble_fusion"


# ─── create_signal_model ─────────────────────────────────


class TestCreateSignalModel:
    def test_returns_none_when_lightgbm_missing(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_lightgbm", False)
        model = create_signal_model("lightgbm")
        assert model is None

    def test_returns_none_when_xgboost_missing(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        model = create_signal_model("xgboost")
        assert model is None

    def test_returns_none_when_ensemble_all_missing(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_lightgbm", False)
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        model = create_signal_model("ensemble")
        assert model is None

    def test_returns_model_when_lightgbm_present(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_lightgbm", True)
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        model = create_signal_model("lightgbm")
        assert isinstance(model, MLSignalModel)
        assert model.is_available is True

    def test_kind_string_coercion(self):
        model = MLSignalModel(kind="lightgbm")
        assert model.kind == ModelKind.LIGHTGBM


# ─── MLSignalModel ────────────────────────────────────────


class TestMLSignalModel:
    def test_predict_before_fit_raises(self):
        model = MLSignalModel(kind="lightgbm")
        with pytest.raises(ModelNotAvailableError):
            model.predict(np.zeros((3, 2)))

    def test_fit_missing_dependency_raises(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_lightgbm", False)
        model = MLSignalModel(kind="lightgbm")
        with pytest.raises(ModelNotAvailableError):
            model.fit(np.zeros((5, 2)), np.zeros(5))

    def test_fit_and_predict_with_fake_model(self, fake_lightgbm, make_panel_data):
        X, y, _ = make_panel_data(n_feat=2)
        model = MLSignalModel(kind="lightgbm")
        model.fit(X, y)
        pred = model.predict(X[:5])
        assert pred.shape == (5,)
        assert np.all(np.isfinite(pred))

    def test_predict_returns_float_array(self, fake_lightgbm):
        model = MLSignalModel(kind="lightgbm")
        model.fit(np.eye(2), np.arange(2, dtype=float))
        pred = model.predict(np.eye(2))
        assert pred.dtype == np.float64

    def test_is_available_reflects_dependency(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_lightgbm", False)
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        assert MLSignalModel("ensemble").is_available is False
        monkeypatch.setattr("fts.ml.models._has_lightgbm", True)
        assert MLSignalModel("ensemble").is_available is True


# ─── SignalModelTrainer ──────────────────────────────────


class TestSignalModelTrainer:
    def test_cross_sectional_training(self, fake_lightgbm, make_panel_data):
        X, y, _ = make_panel_data()
        trainer = SignalModelTrainer(kind="lightgbm", mode="cross_sectional")
        result = trainer.train(X, y, feature_names=["a", "b", "c"])
        assert isinstance(result, TrainResult)
        assert result.model is not None
        assert result.n_samples == X.shape[0]
        assert result.n_features == 3
        assert result.score > 0.5  # 线性可拟合
        assert set(result.feature_importance.keys()) == {"a", "b", "c"}

    def test_time_series_mode(self, fake_lightgbm, make_panel_data):
        X, y, _ = make_panel_data(n_feat=2)
        trainer = SignalModelTrainer(kind="lightgbm", mode=TrainMode.TIME_SERIES)
        result = trainer.train(X, y)
        assert result.mode == TrainMode.TIME_SERIES
        assert result.model is not None

    def test_ensemble_fusion_forces_ensemble_kind(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_lightgbm", True)
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        X = np.random.default_rng(0).standard_normal((40, 2))
        y = X[:, 0] * 2.0 + 1.0
        trainer = SignalModelTrainer(kind="lightgbm", mode="ensemble_fusion")
        result = trainer.train(X, y)
        assert result.kind == ModelKind.ENSEMBLE

    def test_dependency_missing_returns_none_model(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_lightgbm", False)
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        X = np.random.default_rng(0).standard_normal((20, 2))
        y = X[:, 0]
        trainer = SignalModelTrainer(kind="lightgbm")
        result = trainer.train(X, y)
        assert result.model is None
        assert "降级" in result.message

    def test_insufficient_samples(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_lightgbm", True)
        X = np.random.default_rng(0).standard_normal((1, 2))
        y = X[:, 0]
        trainer = SignalModelTrainer(kind="lightgbm")
        result = trainer.train(X, y)
        assert result.model is None
        assert "样本" in result.message

    def test_nan_rows_cleaned(self, fake_lightgbm):
        X = np.array([[1.0], [np.nan], [3.0], [4.0], [5.0]])
        y = np.array([2.0, 3.0, np.nan, 5.0, 6.0])
        trainer = SignalModelTrainer(kind="lightgbm")
        result = trainer.train(X, y)
        assert result.model is not None
        assert result.n_samples == 3  # 仅保留有限行

    def test_trainer_with_dataframe_input(self, fake_lightgbm, make_panel_data):
        import pandas as pd

        X, y, _ = make_panel_data()
        df = pd.DataFrame(X, columns=["f_a", "f_b", "f_c"])
        trainer = SignalModelTrainer(kind="lightgbm")
        result = trainer.train(df, y)
        assert result.n_features == 3
        assert set(result.feature_importance.keys()) == {"f_a", "f_b", "f_c"}

    def test_r2_score_edge(self):
        y = np.array([1.0, 1.0, 1.0])
        pred = np.array([1.0, 1.0, 1.0])
        assert SignalModelTrainer._r2_score(y, pred) == 0.0  # 零方差目标

# ─── GAP-F16: XGBoost / Ensemble 双子模型 / 边界分支 ────────


@pytest.fixture
def fake_xgboost(monkeypatch):
    """注入假 xgboost 模块。"""
    mod = types.ModuleType("xgboost")

    def _xgb_regressor(**kwargs):
        return _FakeRegressor()

    mod.XGBRegressor = _xgb_regressor
    monkeypatch.setitem(sys.modules, "xgboost", mod)
    monkeypatch.setattr("fts.ml.models._has_xgboost", True)
    return mod


class TestXGBoostModel:
    def test_fit_and_predict_with_fake_model(self, fake_xgboost, make_panel_data):
        X, y, _ = make_panel_data(n_feat=2)
        model = MLSignalModel(kind="xgboost")
        assert model.is_available is True
        model.fit(X, y)
        pred = model.predict(X[:5])
        assert pred.shape == (5,)
        assert np.all(np.isfinite(pred))
        assert model.model is not None

    def test_fit_missing_dependency_raises(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        model = MLSignalModel(kind="xgboost")
        with pytest.raises(ModelNotAvailableError, match="xgboost 未安装"):
            model.fit(np.zeros((5, 2)), np.zeros(5))

    def test_is_available_reflects_dependency(self, monkeypatch):
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        assert MLSignalModel("xgboost").is_available is False
        monkeypatch.setattr("fts.ml.models._has_xgboost", True)
        assert MLSignalModel("xgboost").is_available is True


class TestEnsembleModel:
    def test_fit_predict_with_both_sub_models(self, fake_lightgbm, fake_xgboost, make_panel_data):
        """双子模型等权集成：predict 为两个子模型预测均值。"""
        X, y, _ = make_panel_data(n_feat=2)
        model = MLSignalModel(kind="ensemble")
        assert model.is_available is True
        model.fit(X, y)
        assert len(model._sub_models) == 2
        pred = model.predict(X[:5])
        assert pred.shape == (5,)
        assert np.all(np.isfinite(pred))
        # 等权均值：等于两个子模型预测的平均
        p1 = model._sub_models[0].predict(X[:5])
        p2 = model._sub_models[1].predict(X[:5])
        np.testing.assert_allclose(pred, (p1 + p2) / 2.0)

    def test_build_ensemble_missing_all_deps_raises(self, monkeypatch):
        """直接调用 _build（ensemble 分支）且依赖全缺 → ModelNotAvailableError。"""
        monkeypatch.setattr("fts.ml.models._has_lightgbm", False)
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        model = MLSignalModel(kind="ensemble")
        with pytest.raises(ModelNotAvailableError, match="ensemble 需要至少安装"):
            model._build()

    def test_build_ensemble_returns_none_when_deps_ok(self, fake_lightgbm):
        """依赖可用时 ensemble 无单一底层模型 → _build 返回 None。"""
        model = MLSignalModel(kind="ensemble")
        assert model._build() is None

    def test_fit_ensemble_missing_all_deps_then_predict_raises(self, monkeypatch):
        """依赖全缺时 fit 静默构建空集成（真实行为），predict 才抛"无子模型"。"""
        monkeypatch.setattr("fts.ml.models._has_lightgbm", False)
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        model = MLSignalModel(kind="ensemble")
        model.fit(np.zeros((5, 2)), np.zeros(5))
        assert model._sub_models == []
        with pytest.raises(ModelNotAvailableError, match="无子模型"):
            model.predict(np.zeros((3, 2)))

    def test_predict_ensemble_empty_sub_models_raises(self):
        """哨兵 _model 已设但无子模型 → 抛 ModelNotAvailableError。"""
        model = MLSignalModel(kind="ensemble")
        model._model = "ensemble"  # 模拟已训练哨兵
        model._sub_models = []
        with pytest.raises(ModelNotAvailableError, match="无子模型"):
            model.predict(np.zeros((3, 2)))

    def test_ensemble_fit_with_single_sub_model(self, fake_lightgbm, monkeypatch, make_panel_data):
        """仅 lightgbm 可用 → 集成只有一个子模型。"""
        monkeypatch.setattr("fts.ml.models._has_xgboost", False)
        X, y, _ = make_panel_data(n_feat=2)
        model = MLSignalModel(kind="ensemble")
        model.fit(X, y)
        assert len(model._sub_models) == 1
        assert model.predict(X[:5]).shape == (5,)


class TestCreateSignalModelExtra:
    def test_invalid_kind_raises_value_error(self):
        with pytest.raises(ValueError):
            create_signal_model("bogus_kind")

    def test_params_are_copied(self):
        params = {"n_estimators": 50}
        model = MLSignalModel(kind="lightgbm", params=params)
        params["n_estimators"] = 999  # 外部修改不影响模型
        assert model.params == {"n_estimators": 50}