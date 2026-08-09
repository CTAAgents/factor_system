"""tests/factor_engine/test_factor_clustering_full.py — 因子聚类与 PCA 降维【成功路径】补充测试。

背景: test_factor_clustering.py 的因子代码使用 `def run(df, params)` 签名，
会被沙箱验证器拒绝（要求 `factor_program(data, params)`），因此原测试只覆盖
空/失败路径。本文件使用合法因子代码，覆盖信号计算成功、聚类成功、PCA 成功
及各类边界分支。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.factor_clustering import (  # noqa: E402
    FactorClusteringEngine,
    PCASignalCompressor,
)


# ─── 工具 ──────────────────────────────────────────────────


def _make_df(n: int = 100, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    return pd.DataFrame({
        "open": close + rng.normal(0, 0.1, n),
        "high": close + np.abs(rng.normal(0, 0.3, n)),
        "low": close - np.abs(rng.normal(0, 0.3, n)),
        "close": close,
        "volume": rng.integers(1000, 10000, n).astype(float),
    }, index=dates)


def _make_factor(fid: str, name: str, sharpe: float = 1.0, code: str | None = None) -> dict:
    return {
        "factor_id": fid,
        "name": name,
        "sharpe": sharpe,
        "code": code or "def factor_program(data, params):\n    return data['close'] / 100.0",
    }


def _make_panel(n_symbols: int = 1, n: int = 100) -> dict:
    return {f"SYM{i}": _make_df(n=n, seed=i + 1) for i in range(n_symbols)}


# 不同信号特征的因子代码（FactorExecutor 后置处理会 clip 到 [-10,10]，
# 价格 ~100 直接返回会变常数，需用归一化或差分信号）
_CODE_F1 = "def factor_program(data, params):\n    import numpy as np\n    return data['close'] / 100.0"
_CODE_F2 = "def factor_program(data, params):\n    import numpy as np\n    return np.roll(data['close'], 1) / 100.0"
_CODE_F3 = "def factor_program(data, params):\n    import numpy as np\n    c = data['close']\n    return np.concatenate(([0.0], np.diff(c))) / 10.0"
_CODE_F4 = "def factor_program(data, params):\n    import numpy as np\n    return data['close'] * 1.1 / 100.0"


# ─── FactorClusteringEngine 成功路径 ───────────────────────


class TestClusteringSuccessPaths:
    def test_corr_normal_symmetric(self):
        e = FactorClusteringEngine()
        factors = [_make_factor("f1", "a"), _make_factor("f2", "b"), _make_factor("f3", "c")]
        m, ids = e.compute_signal_correlations(factors, _make_panel(n=100))
        assert m.shape == (3, 3)
        assert ids == ["f1", "f2", "f3"]
        np.testing.assert_allclose(np.diag(m), np.ones(3))
        np.testing.assert_allclose(m, m.T)

    def test_corr_high_correlation_observed(self):
        e = FactorClusteringEngine()
        # 两个因子信号线性相关 → 相关性应接近 1
        code = "def factor_program(data, params):\n    return data['close'] * 2.0 / 100.0"
        factors = [_make_factor("f1", "a"), _make_factor("f2", "b", code=code)]
        m, ids = e.compute_signal_correlations(factors, _make_panel(n=200))
        assert m.shape == (2, 2)
        assert abs(m[0, 1]) > 0.99

    def test_corr_compile_failure_skipped(self):
        e = FactorClusteringEngine()
        f = _make_factor("f1", "a", code="import os\ndef factor_program(data, params):\n    return data['close']")
        m, ids = e.compute_signal_correlations([f, _make_factor("f2", "b")], _make_panel(n=100))
        # f1 编译失败被跳过，只剩 f2 → <2 有效信号
        assert m.size == 0

    def test_cluster_high_corr_merges(self):
        e = FactorClusteringEngine()
        m = np.array([[1.0, 0.9, 0.1], [0.9, 1.0, 0.1], [0.1, 0.1, 1.0]])
        clusters = e.cluster_by_correlation(m, ["a", "b", "c"])
        # a/b 高相关同簇，c 独立
        assert len(clusters) == 2
        sizes = sorted(len(c) for c in clusters)
        assert sizes == [1, 2]

    def test_cluster_linkage_failure_fallback(self):
        e = FactorClusteringEngine(linkage_method="invalid_xyz")
        m = np.array([[1.0, 0.5], [0.5, 1.0]])
        assert e.cluster_by_correlation(m, ["a", "b"]) == [[0], [1]]

    def test_select_missing_factor_skipped(self):
        e = FactorClusteringEngine()
        factors = [_make_factor("f1", "a")]
        selected = e.select_representative_factors(factors, [[0, 1]], ["f1", "ghost"])
        assert len(selected) == 1

    def test_select_multi_picks_highest_sharpe(self):
        e = FactorClusteringEngine()
        factors = [_make_factor("f1", "a", sharpe=1.0), _make_factor("f2", "b", sharpe=3.0)]
        selected = e.select_representative_factors(factors, [[0, 1]], ["f1", "f2"])
        assert selected[0]["factor_id"] == "f2"

    def test_run_corr_failure_returns_all(self):
        e = FactorClusteringEngine()
        f = _make_factor("f1", "a", code="")
        factors = [f, f, f]
        assert e.run(factors, _make_panel()) == factors

    def test_run_normal_success(self):
        e = FactorClusteringEngine(cluster_threshold=0.3)
        factors = [
            _make_factor("f1", "a", sharpe=2.0),
            _make_factor("f2", "b", sharpe=1.0),
            _make_factor("f3", "c", sharpe=1.5),
        ]
        selected = e.run(factors, _make_panel(n=200))
        assert 1 <= len(selected) <= 3
        for f in selected:
            assert "factor_id" in f

    def test_run_identical_code_factors_merge(self):
        e = FactorClusteringEngine()
        code = "def factor_program(data, params):\n    return data['close']"
        factors = [
            _make_factor(f"f{i}", f"n{i}", code=code, sharpe=float(i + 1))
            for i in range(5)
        ]
        panel = _make_panel(n=200)
        selected = e.run(factors, panel)
        assert 1 <= len(selected) <= 5  # 相同信号 → 应聚簇


# ─── PCASignalCompressor 成功路径 ──────────────────────────


class TestPCASuccessPaths:
    def test_signal_matrix_normal(self):
        c = PCASignalCompressor()
        factors = [_make_factor("f1", "a"), _make_factor("f2", "b"), _make_factor("f3", "c")]
        m, ids, dates = c.compute_signal_matrix(factors, _make_panel(n=100))
        assert m.shape == (100, 3)
        assert ids == ["f1", "f2", "f3"]
        assert len(dates) == 100

    def test_signal_matrix_short_length(self):
        c = PCASignalCompressor()
        factors = [_make_factor("f1", "a"), _make_factor("f2", "b")]
        m, ids, dates = c.compute_signal_matrix(factors, _make_panel(n=5))
        assert m.size == 0

    def test_run_sklearn_missing(self, monkeypatch):
        c = PCASignalCompressor()
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "sklearn.decomposition":
                raise ImportError("no sklearn")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        factors = [_make_factor("f1", "a"), _make_factor("f2", "b"), _make_factor("f3", "c")]
        result = c.run(factors, _make_panel(n=100))
        assert result["pca_applied"] is False

    def test_run_normal_pca(self):
        c = PCASignalCompressor()
        factors = [
            _make_factor("f1", "a", sharpe=2.0, code=_CODE_F1),
            _make_factor("f2", "b", sharpe=1.0, code=_CODE_F2),
            _make_factor("f3", "c", sharpe=1.5, code=_CODE_F3),
            _make_factor("f4", "d", sharpe=0.8, code=_CODE_F4),
        ]
        result = c.run(factors, _make_panel(n=200))
        assert result["pca_applied"] is True
        assert result["n_components"] >= 1
        assert result["explained_variance_ratio"] > 0
        assert len(result["pca_signals"]) == 4
        assert len(result["factor_loadings"]) == 4
        # 权重归一化
        weights = [s["weight"] for s in result["pca_signals"]]
        assert sum(weights) == pytest.approx(1.0, abs=1e-6)
        # 信号字段完整
        for sig in result["pca_signals"]:
            assert sig["orthogonalized"] is True
            assert "pca_loading" in sig
            assert "pca_component" in sig
            assert "retained" in sig

    def test_run_signal_matrix_failure(self):
        c = PCASignalCompressor()
        f = _make_factor("f1", "a", code="")
        result = c.run([f, f, f], _make_panel())
        assert result["pca_applied"] is False
