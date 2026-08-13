"""
tests/test_transformer_factor.py — C5 轻量 Transformer 深度因子测试（v2.100.1）。

覆盖三层（对齐 test_gru_factor.py 风格）:
    - TransformerFactorModel 模型级（fts/ml/models.py）:
      训练/预测形状、学习能力、**因果掩码**（attn 上三角为 0，零未来函数）、
      标准化、降级路径（样本不足/未训练/非数值/维度不匹配）、get_params 导出
    - DeepFactorGenerator 集成级（fts/ml/deep_factor.py，model_kind="transformer"）:
      生成 FactorProgram 字段完整、code 可执行、零未来函数截断一致性、
      短序列降级、确定性
    - EvolutionLoop 接线级（fts/factor_engine/evolution_loop.py）:
      _run_deep_evolution model_kind="transformer" 透传、_evolve_one 分派、
      _batch_generate_one 轮换含 transformer
"""

from __future__ import annotations


import numpy as np
import pandas as pd
import pytest

from fts.ml import (
    ModelNotAvailableError,
    TransformerFactorModel,
    create_transformer_model,
)
from fts.ml.deep_factor import (
    DeepFactorConfig,
    create_deep_factor,
)


def _make_ohlcv(n: int = 400, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(rng.standard_normal(n) * 0.5)
    return pd.DataFrame(
        {
            "open": close + rng.standard_normal(n) * 0.1,
            "high": close + np.abs(rng.standard_normal(n)) * 0.3,
            "low": close - np.abs(rng.standard_normal(n)) * 0.3,
            "close": close,
            "volume": rng.integers(1000, 10000, n).astype(float),
        },
        index=dates,
    )


def _make_forward_returns(data: pd.DataFrame) -> np.ndarray:
    close = data["close"].values
    rets = np.zeros(len(close))
    rets[:-1] = (close[1:] - close[:-1]) / np.maximum(np.abs(close[:-1]), 1e-10)
    return rets


def _make_window_samples(n: int = 120, seq: int = 8, n_feat: int = 2, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, seq, n_feat))
    y = 2.0 * X[:, -1, 0] - 1.0 * X[:, -1, 1] + rng.standard_normal(n) * 0.05
    return X, y


class TestTransformerFactorModel:
    def test_fit_predict_shape(self):
        X, y = _make_window_samples(n=80, seq=8, n_feat=2)
        model = TransformerFactorModel(hidden=4, epochs=10, min_samples=16)
        model.fit(X, y)
        pred = model.predict(X[:10])
        assert pred.shape == (10,)
        assert pred.dtype == np.float64
        assert np.all(np.isfinite(pred))

    def test_seq_len_n_features_properties(self):
        X, y = _make_window_samples(n=80, seq=8, n_feat=2)
        model = TransformerFactorModel(hidden=4, epochs=5, min_samples=16)
        with pytest.raises(ModelNotAvailableError):
            _ = model.seq_len  # 未训练不可取
        model.fit(X, y)
        assert model.seq_len == 8
        assert model.n_features == 2

    def test_learning_correlation(self):
        X, y = _make_window_samples(n=120, seq=8, n_feat=2)
        model = TransformerFactorModel(hidden=8, epochs=60, min_samples=16)
        model.fit(X, y)
        pred = model.predict(X)
        corr = float(np.corrcoef(pred, y)[0, 1])
        assert np.isfinite(corr)
        assert corr > 0.5  # 线性目标可学习

    def test_causal_mask(self):
        """因果掩码：attention 上三角为 0（t 时刻只用 ≤t 输入，零未来函数）。"""
        X, y = _make_window_samples(n=40, seq=6, n_feat=2)
        model = TransformerFactorModel(hidden=4, epochs=5, min_samples=16)
        model.fit(X, y)
        X_norm = (X - model._x_mean) / model._x_std
        _, cache = model._forward(X_norm)
        attn = cache["attn"]  # (b, seq, seq)
        assert attn.shape[2] == 6
        # 上三角（j > i）注意力权重 ≈ 0
        upper = np.triu(np.ones((6, 6), dtype=bool), k=1)
        assert np.all(attn[:, upper] < 1e-6)

    def test_reproducible_same_seed(self):
        X, y = _make_window_samples(n=60, seq=6, n_feat=2)
        m1 = TransformerFactorModel(hidden=4, epochs=10, seed=9, min_samples=16)
        m2 = TransformerFactorModel(hidden=4, epochs=10, seed=9, min_samples=16)
        m1.fit(X, y)
        m2.fit(X, y)
        np.testing.assert_allclose(m1.predict(X), m2.predict(X), atol=1e-9)

    def test_insufficient_samples_raises(self):
        X, y = _make_window_samples(n=8, seq=6, n_feat=2)
        model = TransformerFactorModel(min_samples=16)
        with pytest.raises(ModelNotAvailableError):
            model.fit(X, y)

    def test_predict_before_fit_raises(self):
        X, _ = _make_window_samples(n=30, seq=6, n_feat=2)
        model = TransformerFactorModel()
        with pytest.raises(ModelNotAvailableError):
            model.predict(X)

    def test_non_numeric_input_raises(self):
        X, y = _make_window_samples(n=40, seq=6, n_feat=2)
        model = TransformerFactorModel(epochs=5, min_samples=16)
        X_bad = X.astype(object)
        X_bad[0, 0, 0] = "bad"
        with pytest.raises(ModelNotAvailableError):
            model.fit(X_bad, y)

    def test_wrong_ndim_raises(self):
        X, y = _make_window_samples(n=40, seq=6, n_feat=2)
        model = TransformerFactorModel(epochs=5, min_samples=16)
        model.fit(X, y)
        with pytest.raises(ModelNotAvailableError):
            model.predict(X[:, :, 0])  # 2D

    def test_get_params_exports_weights(self):
        X, y = _make_window_samples(n=40, seq=6, n_feat=2)
        model = TransformerFactorModel(hidden=4, epochs=5, min_samples=16)
        model.fit(X, y)
        params = model.get_params()
        assert set(params) == {"Wq", "Wk", "Wv", "Wo", "bo", "pos_emb"}
        assert params["Wq"].shape == (2, 4)
        assert params["pos_emb"].shape == (6, 4)

    def test_get_params_before_fit_raises(self):
        model = TransformerFactorModel()
        with pytest.raises(ModelNotAvailableError):
            model.get_params()

    def test_create_transformer_model_factory(self):
        m = create_transformer_model({"hidden": 4, "epochs": 3})
        assert isinstance(m, TransformerFactorModel)
        assert m.hidden == 4


class TestDeepFactorTransformer:
    def _generate(self, n: int = 300, seed: int = 42, **cfg_kwargs):
        data = _make_ohlcv(n=n, seed=seed)
        y = _make_forward_returns(data)
        cfg = DeepFactorConfig(model_kind="transformer", **cfg_kwargs)
        factor = create_deep_factor(
            data,
            y,
            market="futures",
            parent_name="rb",
            trace_id="t_transformer_001",
            config=cfg,
        )
        return factor, data

    def test_generate_returns_factor_program(self):
        factor, _ = self._generate()
        assert factor is not None
        assert factor["deep_model"]["model"] == "transformer"
        assert factor["name"].startswith("deep_transformer_")
        assert factor["params"]["model_kind"] == "transformer"
        assert factor["kind"] == "code"
        assert factor["factor_id"]

    def test_generate_code_executes(self):
        from fts.factor_engine.backtest_pipeline import BacktestPipeline

        factor, data = self._generate()
        assert factor is not None
        values = BacktestPipeline._execute_factor_code(
            factor["code"],
            data,
            factor.get("params"),
        )
        assert values.shape == (len(data),)
        assert np.all(np.isfinite(values))
        lb = factor["params"]["lookback"]
        assert np.all(np.abs(values[lb:]) <= 1.0 + 1e-9)  # tanh 压缩信号

    def test_zero_future_function(self):
        """零未来函数：截断到 [0, t] 推理，位置 t 与全序列一致。"""
        from fts.factor_engine.backtest_pipeline import BacktestPipeline

        factor, data = self._generate()
        assert factor is not None
        t = 200
        full = BacktestPipeline._execute_factor_code(factor["code"], data, factor.get("params"))
        truncated = BacktestPipeline._execute_factor_code(
            factor["code"], data.iloc[: t + 1], factor.get("params")
        )
        assert truncated.shape[0] == t + 1
        np.testing.assert_allclose(truncated[t], full[t], atol=1e-9)

    def test_short_series_returns_none(self):
        data = _make_ohlcv(n=20)
        y = _make_forward_returns(data)
        cfg = DeepFactorConfig(model_kind="transformer")
        assert create_deep_factor(data, y, config=cfg) is None

    def test_deterministic_same_seed(self):
        factor1, data = self._generate(seed=11, epochs=20, min_samples=32)
        factor2, _ = self._generate(seed=11, epochs=20, min_samples=32)
        assert factor1 is not None and factor2 is not None
        assert factor1["code"] == factor2["code"]

    def test_default_kind_stays_gru(self):
        """默认 model_kind="gru"，transformer 需显式指定（不改默认行为）。"""
        data = _make_ohlcv(n=200)
        y = _make_forward_returns(data)
        factor = create_deep_factor(data, y, market="futures", parent_name="rb", trace_id="t_gru_keep")
        assert factor is not None
        assert factor["deep_model"]["model"] == "gru"


class TestEvolutionLoopTransformer:
    def _make_loop(self, tmp_path):
        from fts.factor_engine.evolution_loop import EvolutionLoop

        data = _make_ohlcv(n=400)
        elite_dir = tmp_path / "elite"
        memory_dir = tmp_path / "memory"
        elite_dir.mkdir(parents=True, exist_ok=True)
        memory_dir.mkdir(parents=True, exist_ok=True)
        return EvolutionLoop(
            data=data,
            forward_returns=_make_forward_returns(data),
            elite_dir=str(elite_dir),
            memory_dir=str(memory_dir),
            n_trials_micro=5,
            market="futures",
        )

    def _fake_factor(self):
        return {
            "factor_id": "fct_tf_test",
            "name": "deep_transformer_test",
            "code": "def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))\n",
            "params": {"lookback": 10, "hidden": 8, "horizon": 5, "model_kind": "transformer"},
            "deep_model": {"model": "transformer", "lookback": 10, "val_ic": 0.3},
        }

    def test_run_deep_evolution_transformer_kind(self, tmp_path, monkeypatch):
        loop = self._make_loop(tmp_path)
        captured = {}

        def _fake_run(parent, generation, trace_id, model_kind="gru"):
            captured["model_kind"] = model_kind
            return self._fake_factor(), "ok"

        monkeypatch.setattr(loop, "_run_deep_evolution", _fake_run)

        parent = {"factor_id": "p1", "name": "parent1"}
        result = loop._evolve_one(parent, generation=1, trace_id="t_tf_loop_1", method_hint="transformer")
        assert result is not None
        factor, method, summary, tokens = result
        assert factor["deep_model"]["model"] == "transformer"
        assert captured["model_kind"] == "transformer"
        assert method == "deep_evolution"

    def test_evolve_one_transformer_failure_returns_none(self, tmp_path, monkeypatch):
        loop = self._make_loop(tmp_path)

        def _boom(parent, generation, trace_id, model_kind="gru"):
            raise RuntimeError("transformer down")

        monkeypatch.setattr(loop, "_run_deep_evolution", _boom)
        parent = {"factor_id": "p1", "name": "parent1"}
        assert loop._evolve_one(parent, generation=1, trace_id="t_tf_loop_2", method_hint="transformer") is None

    def test_batch_rotation_includes_transformer(self, tmp_path, monkeypatch):
        loop = self._make_loop(tmp_path)
        hints: list[str] = []
        loop._batch_idx = 0
        loop.batch_random_seed = 0

        def _fake_evolve(parent, generation, trace_id, method_hint=None, seed=None):
            hints.append(method_hint)
            return self._fake_factor(), method_hint, "ok", 0

        monkeypatch.setattr(loop, "_evolve_one", _fake_evolve)
        parent = {"factor_id": "p1", "name": "parent1"}
        for _ in range(6):  # 覆盖 idx=0..5（含 transformer 槽位 idx=3）
            loop._batch_generate_one(parent, generation=1, trace_id="t_tf_loop_3")
        assert "transformer" in hints
        assert hints[:4] == ["macro", "gp", "deep", "transformer"]
