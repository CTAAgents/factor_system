"""
tests/test_gru_factor.py — GAP-I203 深度因子学习测试（v2.73.0）。

覆盖三层:
    - GRUFactorModel 模型级（fts/ml/models.py）:
      训练/预测形状、学习能力、标准化、降级路径（样本不足/未训练/
      非数值/维度不匹配）、get_params 权重导出、同 seed 可复现性
    - DeepFactorGenerator 集成级（fts/ml/deep_factor.py）:
      生成 FactorProgram 字段完整、code 可执行、零未来函数验证、
      短序列/非数值/长度不匹配降级、无 volume 兜底、确定性
    - EvolutionLoop 接线级（fts/factor_engine/evolution_loop.py）:
      _run_deep_evolution 成功/失败路径、_evolve_one deep 分派、
      _batch_generate_one 轮换含 deep

版本: v1.0.0
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pytest

if TYPE_CHECKING:  # pragma: no cover
    from fts.factor_engine.evolution_loop import EvolutionLoop

from fts.ml import (
    GRUFactorModel,
    ModelNotAvailableError,
    create_gru_model,
)
from fts.ml.deep_factor import (
    DeepFactorConfig,
    DeepFactorGenerator,
    create_deep_factor,
)


# ─── 通用数据构造 ─────────────────────────────────────────


def _make_ohlcv(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """合成 OHLCV 面板（与 conftest.sample_ohlcv 同构）。"""
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
    """未来 1 日收益率（与 data 等长）。"""
    close = data["close"].values
    rets = np.zeros(len(close))
    rets[:-1] = (close[1:] - close[:-1]) / np.maximum(np.abs(close[:-1]), 1e-10)
    return rets


def _make_window_samples(n: int = 200, seq: int = 8, n_feat: int = 2, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """构造可学习的窗口样本：目标由末步特征线性映射决定。"""
    rng = np.random.default_rng(seed)
    X = rng.standard_normal((n, seq, n_feat))
    y = 2.0 * X[:, -1, 0] - 1.0 * X[:, -1, 1] + rng.standard_normal(n) * 0.05
    return X, y


# ─── GRUFactorModel 模型级 ────────────────────────────────


class TestGRUFactorModel:
    def test_fit_predict_shape(self):
        X, y = _make_window_samples(n=80, seq=8, n_feat=2)
        model = GRUFactorModel(hidden=4, epochs=10, min_samples=16)
        model.fit(X, y)
        pred = model.predict(X[:10])
        assert pred.shape == (10,)
        assert pred.dtype == np.float64
        assert np.all(np.isfinite(pred))

    def test_seq_len_n_features_properties(self):
        X, y = _make_window_samples(n=80, seq=8, n_feat=2)
        model = GRUFactorModel(hidden=4, epochs=5, min_samples=16)
        with pytest.raises(ModelNotAvailableError):
            _ = model.seq_len  # 未训练不可取
        model.fit(X, y)
        assert model.seq_len == 8
        assert model.n_features == 2

    def test_learning_correlation(self):
        """GRU 应能从线性记忆目标中学习（预测与目标正相关）。"""
        X, y = _make_window_samples(n=200, seq=8, n_feat=2, seed=7)
        model = GRUFactorModel(hidden=8, epochs=120, min_samples=32, seed=42)
        model.fit(X, y)
        pred = model.predict(X)
        corr = float(np.corrcoef(pred, y)[0, 1])
        assert np.isfinite(corr)
        assert corr > 0.3, f"GRU 学习能力不足: corr={corr:.3f}"

    def test_reproducible_same_seed(self):
        X, y = _make_window_samples(n=100, seq=8, n_feat=2, seed=7)
        m1 = GRUFactorModel(hidden=4, epochs=20, min_samples=16, seed=11)
        m2 = GRUFactorModel(hidden=4, epochs=20, min_samples=16, seed=11)
        m1.fit(X, y)
        m2.fit(X, y)
        np.testing.assert_allclose(m1.predict(X), m2.predict(X), atol=1e-12)

    def test_insufficient_samples_raises(self):
        X, y = _make_window_samples(n=8, seq=8, n_feat=2)
        model = GRUFactorModel(min_samples=32)
        with pytest.raises(ModelNotAvailableError, match="样本数"):
            model.fit(X, y)

    def test_predict_before_fit_raises(self):
        model = GRUFactorModel()
        with pytest.raises(ModelNotAvailableError, match="未训练"):
            model.predict(np.zeros((4, 8, 2)))

    def test_non_numeric_input_raises(self):
        X = np.array([["a", "b"], ["c", "d"]], dtype=object).reshape(2, 1, 2)
        model = GRUFactorModel(min_samples=1)
        with pytest.raises(ModelNotAvailableError, match="非数值"):
            model.fit(X, np.array([1.0, 2.0]))

    def test_wrong_ndim_raises(self):
        X, y = _make_window_samples(n=60, seq=8, n_feat=2)
        model = GRUFactorModel(min_samples=16)
        model.fit(X, y)
        with pytest.raises(ModelNotAvailableError, match="3D"):
            model.predict(np.zeros((10, 8)))  # 2D 输入
        with pytest.raises(ModelNotAvailableError, match="形状不匹配"):
            model.predict(np.zeros((10, 6, 2)))  # seq 不匹配

    def test_constant_feature_column(self):
        """常数列特征不导致 NaN（std 兜底为 1）。"""
        rng = np.random.default_rng(1)
        X = rng.standard_normal((80, 8, 2))
        X[:, :, 1] = 5.0  # 常数列
        y = rng.standard_normal(80)
        model = GRUFactorModel(hidden=4, epochs=5, min_samples=16)
        model.fit(X, y)
        pred = model.predict(X[:5])
        assert np.all(np.isfinite(pred))

    def test_get_params_exports_weights(self):
        X, y = _make_window_samples(n=80, seq=8, n_feat=2)
        model = GRUFactorModel(hidden=4, epochs=10, min_samples=16)
        model.fit(X, y)
        params = model.get_params()
        assert set(params) == {
            "Wz",
            "Uz",
            "bz",
            "Wr",
            "Ur",
            "br",
            "Wh",
            "Uh",
            "bh",
            "Wo",
            "bo",
        }
        assert params["Wz"].shape == (2, 4)
        assert params["Uz"].shape == (4, 4)
        assert params["Wo"].shape == (4, 1)
        assert params["bo"].shape == (1,)
        assert all(np.all(np.isfinite(v)) for v in params.values())

    def test_get_params_before_fit_raises(self):
        model = GRUFactorModel()
        with pytest.raises(ModelNotAvailableError, match="未训练"):
            model.get_params()

    def test_n_features_before_fit_raises(self):
        model = GRUFactorModel()
        with pytest.raises(ModelNotAvailableError, match="未训练"):
            _ = model.n_features

    def test_fit_wrong_ndim_raises(self):
        """fit 输入非 3D → ModelNotAvailableError。"""
        model = GRUFactorModel(min_samples=4)
        with pytest.raises(ModelNotAvailableError, match="3D"):
            model.fit(np.zeros((60, 8)), np.zeros(60))

    def test_fit_samples_mismatch_raises(self):
        """fit 时 X 与 y 样本数不一致 → ModelNotAvailableError。"""
        X, y = _make_window_samples(n=60, seq=8, n_feat=2)
        model = GRUFactorModel(min_samples=4)
        with pytest.raises(ModelNotAvailableError, match="不一致"):
            model.fit(X, y[:30])

    def test_create_gru_model_factory(self):
        model = create_gru_model({"hidden": 6, "min_samples": 4})
        assert isinstance(model, GRUFactorModel)
        assert model.hidden == 6
        assert model.is_available is True


# ─── DeepFactorGenerator 集成级 ───────────────────────────


class TestDeepFactorGenerator:
    def test_generate_returns_factor_program(self):
        data = _make_ohlcv()
        y = _make_forward_returns(data)
        factor = create_deep_factor(
            data,
            y,
            market="futures",
            parent_name="rb_momentum",
            trace_id="t_gru_001",
        )
        assert factor is not None
        assert factor["factor_id"].startswith("fct_")
        assert factor["name"].startswith("deep_gru_")
        assert "def factor_program" in factor["code"]
        assert factor["source"] == "deep_evolution"
        assert factor["market"] == "futures"
        assert factor["parent_id"] == "rb_momentum"
        assert factor["deep_model"]["model"] == "gru"
        assert factor["signature"]["input_fields"] == ["close", "volume"]
        assert factor["economic_logic"]["narrative"]

    def test_generate_code_executes(self):
        """生成 code 应能通过 BacktestPipeline._execute_factor_code 执行。"""
        from fts.factor_engine.backtest_pipeline import (
            BacktestPipeline,
        )

        data = _make_ohlcv(n=400)
        y = _make_forward_returns(data)
        factor = create_deep_factor(
            data,
            y,
            market="futures",
            parent_name="rb",
            trace_id="t_gru_002",
        )
        assert factor is not None
        values = BacktestPipeline._execute_factor_code(
            factor["code"],
            data,
            factor.get("params"),
        )
        assert values.shape == (len(data),)
        assert np.all(np.isfinite(values))
        # 前 lookback-1 行为零（窗口不足），之后输出为 tanh 压缩信号 ∈ [-1, 1]
        lb = factor["params"]["lookback"]
        assert np.all(np.abs(values[lb:]) <= 1.0 + 1e-9)

    def test_zero_future_function(self):
        """零未来函数验证：t 位置输出不受 t 之后数据影响。

        截断到 [0, t] 再推理，位置 t 的值应与全序列推理一致。
        """
        from fts.factor_engine.backtest_pipeline import (
            BacktestPipeline,
        )

        data = _make_ohlcv(n=300)
        y = _make_forward_returns(data)
        factor = create_deep_factor(
            data,
            y,
            market="futures",
            parent_name="rb",
            trace_id="t_gru_003",
        )
        assert factor is not None

        t = 200
        full = BacktestPipeline._execute_factor_code(
            factor["code"],
            data,
            factor.get("params"),
        )
        truncated = BacktestPipeline._execute_factor_code(
            factor["code"],
            data.iloc[: t + 1],
            factor.get("params"),
        )
        assert truncated.shape[0] == t + 1
        np.testing.assert_allclose(truncated[t], full[t], atol=1e-9)

    def test_short_series_returns_none(self):
        data = _make_ohlcv(n=20)  # 低于 lookback+horizon+min_samples
        y = _make_forward_returns(data)
        assert create_deep_factor(data, y) is None

    def test_non_numeric_returns_none(self):
        data = _make_ohlcv(n=200)
        y = _make_forward_returns(data)  # 先算 y（clean）
        data["close"] = data["close"].astype(object)
        data.loc[data.index[50], "close"] = "bad"
        assert create_deep_factor(data, y) is None

    def test_length_mismatch_returns_none(self):
        data = _make_ohlcv(n=200)
        y = np.zeros(100)  # 长度不齐
        assert create_deep_factor(data, y) is None

    def test_missing_volume_still_generates(self):
        data = _make_ohlcv(n=300).drop(columns=["volume"])
        y = _make_forward_returns(data)
        factor = create_deep_factor(
            data,
            y,
            market="futures",
            parent_name="rb",
            trace_id="t_gru_004",
        )
        assert factor is not None  # volume 缺失兜底为零

    def test_deterministic_same_seed(self):
        data = _make_ohlcv(n=300)
        y = _make_forward_returns(data)
        cfg = DeepFactorConfig(epochs=30, min_samples=32, seed=99)
        f1 = create_deep_factor(
            data,
            y,
            market="futures",
            parent_name="rb",
            trace_id="t_gru_005",
            config=cfg,
        )
        f2 = create_deep_factor(
            data,
            y,
            market="futures",
            parent_name="rb",
            trace_id="t_gru_005",
            config=cfg,
        )
        assert f1 is not None and f2 is not None
        assert f1["code"] == f2["code"]  # 权重字面量确定

    def test_config_custom_lookback(self):
        data = _make_ohlcv(n=300)
        y = _make_forward_returns(data)
        cfg = DeepFactorConfig(lookback=6, horizon=3, epochs=30)
        factor = create_deep_factor(
            data,
            y,
            market="stock",
            parent_name="sz",
            trace_id="t_gru_006",
            config=cfg,
        )
        assert factor is not None
        assert factor["params"]["lookback"] == 6
        assert factor["params"]["horizon"] == 3
        assert factor["market"] == "stock"

    def test_generator_degradation_on_train_failure(self, monkeypatch):
        """GRU 训练抛异常（ModelNotAvailableError）时 generate 返回 None。"""
        data = _make_ohlcv(n=300)
        y = _make_forward_returns(data)
        gen = DeepFactorGenerator(DeepFactorConfig(epochs=30))

        def _raise(*args, **kwargs):
            raise ModelNotAvailableError("模拟训练失败")

        monkeypatch.setattr(
            "fts.ml.models.GRUFactorModel.fit",
            _raise,
        )
        assert gen.generate(data, y) is None


# ─── EvolutionLoop 接线级 ─────────────────────────────────


def _make_loop(tmp_path: Path) -> "EvolutionLoop":
    """最小化 EvolutionLoop 实例（数据 500 行，满足深度因子样本需求）。"""
    from fts.factor_engine.evolution_loop import EvolutionLoop

    data = _make_ohlcv(n=500)
    y = _make_forward_returns(data)
    return EvolutionLoop(
        data=data,
        forward_returns=y,
        elite_dir=str(tmp_path / "elite"),
        memory_dir=str(tmp_path / "memory"),
        n_trials_micro=5,
        market="futures",
    )


def _parent_factor() -> dict:
    """最小父因子（含 code/name/factor_id）。"""
    return {
        "factor_id": "fct_parent_001",
        "name": "rb_momentum",
        "code": "def factor_program(data, params):\n    return data['close']",
    }


class TestEvolutionLoopDeep:
    def test_run_deep_evolution_success(self, tmp_path):
        loop = _make_loop(tmp_path)
        factor, summary = loop._run_deep_evolution(
            _parent_factor(),
            generation=3,
            trace_id="t_gru_evol_001",
        )
        assert factor["factor_id"].startswith("fct_")
        assert factor["parent_id"] == "fct_parent_001"
        assert factor["generation"] == 3
        assert factor["trace_id"] == "t_gru_evol_001"
        assert "Deep GRU" in summary
        assert factor["source"] == "deep_evolution"

    def test_run_deep_evolution_no_data_raises(self, tmp_path):
        from fts.factor_engine.evolution_loop import EvolutionLoop

        loop = EvolutionLoop(
            data=None,
            forward_returns=None,
            elite_dir=str(tmp_path / "elite"),
            memory_dir=str(tmp_path / "memory"),
            n_trials_micro=5,
            market="futures",
        )
        with pytest.raises(RuntimeError, match="无可用行情数据"):
            loop._run_deep_evolution(_parent_factor(), 1, "t_gru_evol_002")

    def test_run_deep_evolution_insufficient_data_raises(self, tmp_path):
        from fts.factor_engine.evolution_loop import EvolutionLoop

        data = _make_ohlcv(n=15)
        y = _make_forward_returns(data)
        loop = EvolutionLoop(
            data=data,
            forward_returns=y,
            elite_dir=str(tmp_path / "elite"),
            memory_dir=str(tmp_path / "memory"),
            n_trials_micro=5,
            market="futures",
        )
        with pytest.raises(RuntimeError, match="样本不足"):
            loop._run_deep_evolution(_parent_factor(), 1, "t_gru_evol_003")

    def test_evolve_one_deep_hint(self, tmp_path):
        loop = _make_loop(tmp_path)
        result = loop._evolve_one(
            _parent_factor(),
            generation=1,
            trace_id="t_gru_evol_004",
            method_hint="deep",
            seed=42,
        )
        assert result is not None
        factor, method, summary, tokens = result
        assert method == "deep_evolution"
        assert tokens == 0
        assert factor["source"] == "deep_evolution"

    def test_evolve_one_deep_failure_returns_none(self, tmp_path, monkeypatch):
        loop = _make_loop(tmp_path)

        def _boom(*args, **kwargs):
            raise RuntimeError("深度演化失败")

        monkeypatch.setattr(loop, "_run_deep_evolution", _boom)
        result = loop._evolve_one(
            _parent_factor(),
            generation=1,
            trace_id="t_gru_evol_005",
            method_hint="deep",
            seed=42,
        )
        assert result is None

    def test_batch_generate_one_rotation_includes_deep(self, tmp_path):
        """batch 轮换: idx 2 → deep, idx 3 → transformer（macro 打头，gp/deep/transformer/operator 四方法轮换）。"""
        loop = _make_loop(tmp_path)
        loop._batch_idx = 0
        hints = []

        def _capture(parent, generation, trace_id, **kwargs):
            hints.append(kwargs.get("method_hint"))
            return None

        loop._evolve_one = _capture  # type: ignore[method-assign]
        for _ in range(8):
            loop._batch_generate_one(_parent_factor(), 1, "t_gru_evol_006")
        assert hints == ["macro", "gp", "deep", "transformer", "operator", "gp", "deep", "transformer"]
