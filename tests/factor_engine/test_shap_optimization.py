"""tests/factor_engine/test_shap_optimization.py — GAP-080 SHAP 批量计算优化测试。

覆盖（08-gap-analysis.md GAP-080，候选优化：SHAP 采样/降频）:
    1. ShapAnalyzer 新默认参数（n_extreme 50→25 / n_background 100→50 / nsamples 100→50）
    2. nsamples 透传至 KernelExplainer.shap_values（降频核心）
    3. FTSConfig 新增 shap_n_extreme/shap_n_background/shap_nsamples（env 可配）
    4. EvolutionLoop 用配置值构造 ShapAnalyzer
    5. 回归：既有 test_shap_analyzer 语义不变（analyze 结构/报告/保存）
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.factor_program import create_factor_program
from fts.factor_engine.shap_analyzer import ShapAnalyzer


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fake_shap(monkeypatch):
    """注入 fake shap 模块（shap 为可选依赖，未安装时测试仍可运行）。

    记录 shap_values 收到的 nsamples，验证降频参数透传。
    """

    class _FakeExplainer:
        instances: list["_FakeExplainer"] = []

        def __init__(self, predict_fn, X_background):
            self.calls: list[int] = []
            _FakeExplainer.instances.append(self)

        def shap_values(self, X_sample, nsamples=100):
            self.calls.append(nsamples)
            arr = np.arange(X_sample.shape[0] * X_sample.shape[1], dtype=np.float64).reshape(
                X_sample.shape
            ) % 3 - 1.0
            return arr

    fake = types.ModuleType("shap")
    fake.KernelExplainer = _FakeExplainer
    monkeypatch.setitem(sys.modules, "shap", fake)
    # 供测试内检查
    return fake


@pytest.fixture
def sample_data() -> pd.DataFrame:
    np.random.seed(42)
    n = 50
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    volume = np.random.randint(1000, 10000, n).astype(float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close + np.random.randn(n) * 0.1,
            "high": close + np.abs(np.random.randn(n)) * 0.3,
            "low": close - np.abs(np.random.randn(n)) * 0.3,
            "close": close,
            "volume": volume,
            "vwap": close + np.random.randn(n) * 0.05,
        }
    )


@pytest.fixture
def forward_returns() -> np.ndarray:
    np.random.seed(42)
    n = 50
    ret = np.random.randn(n) * 0.01
    ret[-1] = 0.0
    return ret


@pytest.fixture
def simple_momentum_factor():
    """一个简单的动量因子。"""
    code = '''
def factor_program(data, params):
    """Alpha: simple_momentum"""
    import numpy as np
    import pandas as pd
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = params.get("lookback", 5)
    mom = pd.Series(close).pct_change(n).values
    return np.clip(np.nan_to_num(mom, nan=0.0), -1.0, 1.0)
'''
    return create_factor_program(
        name="simple_momentum",
        code=code,
        params={},
        signature={
            "input_fields": ["close"],
            "output_type": "signal",
            "frequency": "daily",
            "lookback": 5,
        },
        economic_logic={
            "theory": 3,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "简单动量因子，用于测试",
        },
        source="test",
    )


# ─── 1. 新默认参数（降频） ────────────────────────────────


class TestShapOptimizedDefaults:
    def test_defaults_downscaled(self):
        """默认 n_extreme 50→25、n_background 100→50、nsamples 100→50（GAP-080 降频）。"""
        analyzer = ShapAnalyzer()
        assert analyzer._n_extreme == 25
        assert analyzer._n_background == 50
        assert analyzer._nsamples == 50

    def test_custom_params(self):
        """自定义参数全部生效。"""
        analyzer = ShapAnalyzer(n_extreme=10, n_background=20, nsamples=30, random_seed=123)
        assert analyzer._n_extreme == 10
        assert analyzer._n_background == 20
        assert analyzer._nsamples == 30
        assert analyzer._random_seed == 123

    def test_nsamples_passed_to_shap_values(
        self, fake_shap, sample_data, forward_returns, simple_momentum_factor
    ):
        """analyze 将 _nsamples 透传给 KernelExplainer.shap_values（降频核心）。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10, nsamples=50)
        analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)

        # 每个极端样本（top 2 + bottom 2 = 4 个）都应以 nsamples=50 调用 shap_values
        captured = fake_shap.KernelExplainer.instances[-1]
        assert len(captured.calls) == 4
        assert all(c == 50 for c in captured.calls)

    def test_summary_includes_nsamples(self, sample_data, forward_returns, simple_momentum_factor):
        """summary 增加 n_nsamples 字段（可观测性）。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10, nsamples=50)
        result = analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)
        assert result["summary"]["n_nsamples"] == 50


# ─── 2. FTSConfig 配置项 ──────────────────────────────────


class TestShapConfig:
    def test_defaults(self):
        """FTSConfig 新增 shap 三项，默认对齐降频参数。"""
        from fts.config.settings import FTSConfig

        cfg = FTSConfig()
        assert cfg.shap_n_extreme == 25
        assert cfg.shap_n_background == 50
        assert cfg.shap_nsamples == 50

    def test_env_override(self, monkeypatch):
        """env 可覆盖。"""
        monkeypatch.setenv("FTS_SHAP_N_EXTREME", "5")
        monkeypatch.setenv("FTS_SHAP_N_BACKGROUND", "8")
        monkeypatch.setenv("FTS_SHAP_NSAMPLES", "10")
        from fts.config.settings import FTSConfig

        cfg = FTSConfig()
        assert cfg.shap_n_extreme == 5
        assert cfg.shap_n_background == 8
        assert cfg.shap_nsamples == 10


# ─── 3. EvolutionLoop 接线 ────────────────────────────────


class TestShapEvolutionLoopWiring:
    def test_loop_constructs_analyzer_with_config(self, minimal_loop):
        """EvolutionLoop 用 FTSConfig 值构造 ShapAnalyzer（降频接线生效）。"""
        assert minimal_loop.shap_analyzer._n_extreme == 25
        assert minimal_loop.shap_analyzer._n_background == 50
        assert minimal_loop.shap_analyzer._nsamples == 50
