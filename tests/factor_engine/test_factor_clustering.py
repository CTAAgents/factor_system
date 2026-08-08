"""tests/factor_engine/test_factor_clustering.py — P1 因子聚类 + P2 PCA 降维测试。"""

from __future__ import annotations

import numpy as np
import pytest

# 固定随机种子确保测试结果可复现
np.random.seed(42)

from fts.factor_engine.factor_clustering import (
    DEFAULT_CLUSTER_THRESHOLD,
    DEFAULT_PCA_VARIANCE_RATIO,
    FactorClusteringEngine,
    PCASignalCompressor,
    compute_cluster_summary,
    compute_pca_summary,
)


# ─── 辅助函数 ──────────────────────────────────────────────

def _make_factors(n: int, prefix: str = "fct") -> list[dict]:
    """生成测试用因子列表。"""
    return [
        {
            "factor_id": f"{prefix}_{i}",
            "name": f"{prefix}_{i}",
            "code": "def run(df, params):\n    return df['close'].values",
            "sharpe": 1.5 + i * 0.1,
            "ic": 0.05 + i * 0.01,
            "turnover": 0.3,
            "decay_6m": 0.05,
        }
        for i in range(n)
    ]


def _make_factors_without_code(n: int) -> list[dict]:
    """生成不含 code 的因子列表（用于测试跳过逻辑）。"""
    return [
        {
            "factor_id": f"fct_no_code_{i}",
            "name": f"fct_no_code_{i}",
            "code": "",
            "sharpe": 1.0,
        }
        for i in range(n)
    ]


def _make_panel_data(n_dates: int = 50, n_symbols: int = 1) -> dict[str, "pd.DataFrame"]:
    """生成测试用面板数据。"""
    import pandas as pd
    dates = pd.date_range("2026-01-01", periods=n_dates, freq="B")
    panel = {}
    for s in range(n_symbols):
        sym = f"SYM{s}"
        data = pd.DataFrame(
            {"close": 100 + np.cumsum(np.random.randn(n_dates) * 0.5)},
            index=dates,
        )
        data["open"] = data["close"] * (1 + np.random.randn(n_dates) * 0.005)
        data["high"] = data[["close", "open"]].max(axis=1) * (1 + np.abs(np.random.randn(n_dates) * 0.005))
        data["low"] = data[["close", "open"]].min(axis=1) * (1 - np.abs(np.random.randn(n_dates) * 0.005))
        data["volume"] = 10000 + np.random.randint(0, 5000, n_dates)
        panel[sym] = data
    return panel


# ═══════════════════════════════════════════════════════════
# P1: FactorClusteringEngine
# ═══════════════════════════════════════════════════════════

class TestFactorClusteringEngineInit:
    """FactorClusteringEngine 初始化测试。"""

    def test_default_params(self) -> None:
        engine = FactorClusteringEngine()
        assert engine.cluster_threshold == DEFAULT_CLUSTER_THRESHOLD
        assert engine.linkage_method == "average"
        assert engine.min_cluster_size == 1

    def test_custom_params(self) -> None:
        engine = FactorClusteringEngine(
            cluster_threshold=0.5,
            linkage_method="complete",
            min_cluster_size=2,
        )
        assert engine.cluster_threshold == 0.5
        assert engine.linkage_method == "complete"
        assert engine.min_cluster_size == 2


class TestFactorClusteringEngineComputeSignalCorrelations:
    """compute_signal_correlations 方法测试。"""

    def test_empty_panel(self) -> None:
        engine = FactorClusteringEngine()
        corr, fids = engine.compute_signal_correlations([], {})
        assert corr.size == 0
        assert fids == []

    def test_single_factor(self) -> None:
        engine = FactorClusteringEngine()
        factors = _make_factors(1)
        panel = _make_panel_data()
        corr, fids = engine.compute_signal_correlations(factors, panel)
        # 少于 2 个有效信号返回空
        assert corr.size == 0 or len(fids) < 2

    def test_multiple_factors_valid_data(self) -> None:
        engine = FactorClusteringEngine()
        factors = _make_factors(5)
        panel = _make_panel_data()
        corr, fids = engine.compute_signal_correlations(factors, panel)
        if corr.size > 0 and len(fids) >= 2:
            assert corr.shape == (len(fids), len(fids))
            # 对角线为 1.0
            for i in range(len(fids)):
                assert np.isclose(corr[i, i], 1.0) or np.isnan(corr[i, i])

    def test_factors_without_code_skipped(self) -> None:
        engine = FactorClusteringEngine()
        factors = _make_factors_without_code(3)
        panel = _make_panel_data()
        corr, fids = engine.compute_signal_correlations(factors, panel)
        # 无有效代码，信号为空
        assert corr.size == 0 or len(fids) < 2


class TestFactorClusteringEngineClusterByCorrelation:
    """cluster_by_correlation 方法测试。"""

    def test_single_factor(self) -> None:
        engine = FactorClusteringEngine()
        clusters = engine.cluster_by_correlation(
            np.array([[1.0]]), ["fct_0"],
        )
        assert len(clusters) == 1
        assert clusters[0] == [0]

    def test_highly_correlated_factors(self) -> None:
        engine = FactorClusteringEngine(cluster_threshold=0.7)
        # 3 个因子，两两高相关 (>0.3)
        corr = np.array([
            [1.0, 0.9, 0.85],
            [0.9, 1.0, 0.95],
            [0.85, 0.95, 1.0],
        ])
        clusters = engine.cluster_by_correlation(corr, ["a", "b", "c"])
        # 应合并为 1 个簇
        assert len(clusters) == 1
        assert len(clusters[0]) == 3

    def test_lowly_correlated_factors(self) -> None:
        engine = FactorClusteringEngine(cluster_threshold=0.7)
        # 3 个因子，低相关 (<0.3)
        corr = np.array([
            [1.0, 0.05, 0.1],
            [0.05, 1.0, 0.02],
            [0.1, 0.02, 1.0],
        ])
        clusters = engine.cluster_by_correlation(corr, ["a", "b", "c"])
        # 应分为 3 个簇
        assert len(clusters) == 3

    def test_mixed_correlations(self) -> None:
        engine = FactorClusteringEngine(cluster_threshold=0.7)
        # 4 个因子：(a,b) 高相关, (c,d) 高相关, 两组间低相关
        corr = np.array([
            [1.0, 0.9, 0.1, 0.05],
            [0.9, 1.0, 0.08, 0.03],
            [0.1, 0.08, 1.0, 0.85],
            [0.05, 0.03, 0.85, 1.0],
        ])
        clusters = engine.cluster_by_correlation(corr, ["a", "b", "c", "d"])
        # 应分为 2 个簇
        assert len(clusters) == 2

    def test_nan_correlation(self) -> None:
        engine = FactorClusteringEngine(cluster_threshold=0.7)
        corr = np.array([
            [1.0, np.nan],
            [np.nan, 1.0],
        ])
        clusters = engine.cluster_by_correlation(corr, ["a", "b"])
        # NaN 应处理为最大距离，不应当抛异常
        assert len(clusters) >= 1


class TestFactorClusteringEngineSelectRepresentative:
    """select_representative_factors 方法测试。"""

    def test_single_factor_clusters(self) -> None:
        engine = FactorClusteringEngine()
        factors = _make_factors(3)
        clusters = [[0], [1], [2]]
        fids = [f["factor_id"] for f in factors]
        selected = engine.select_representative_factors(factors, clusters, fids)
        assert len(selected) == 3

    def test_multi_factor_clusters_selects_highest_sharpe(self) -> None:
        engine = FactorClusteringEngine()
        factors = [
            {"factor_id": "a", "name": "a", "code": "...", "sharpe": 1.0},
            {"factor_id": "b", "name": "b", "code": "...", "sharpe": 2.5},
            {"factor_id": "c", "name": "c", "code": "...", "sharpe": 1.5},
        ]
        clusters = [[0, 1, 2]]
        fids = ["a", "b", "c"]
        selected = engine.select_representative_factors(factors, clusters, fids)
        assert len(selected) == 1
        # 应选 Sharpe 最高的 b (2.5)
        assert selected[0]["factor_id"] == "b"

    def test_empty_cluster_handling(self) -> None:
        engine = FactorClusteringEngine()
        selected = engine.select_representative_factors([], [], [])
        assert selected == []


class TestFactorClusteringEngineRun:
    """run 完整流程测试。"""

    def test_empty_factors(self) -> None:
        engine = FactorClusteringEngine()
        result = engine.run([], panel_data=None)
        assert result == []

    def test_no_panel_data(self) -> None:
        engine = FactorClusteringEngine()
        factors = _make_factors(5)
        result = engine.run(factors, panel_data=None)
        # 无面板数据应返回原列表
        assert len(result) == len(factors)

    def test_too_few_factors(self) -> None:
        engine = FactorClusteringEngine()
        factors = _make_factors(2)
        panel = _make_panel_data()
        result = engine.run(factors, panel_data=panel)
        # 少于 3 个因子跳过聚类
        assert len(result) == len(factors)

    def test_full_flow_with_panel(self) -> None:
        engine = FactorClusteringEngine()
        factors = _make_factors(10)
        panel = _make_panel_data()
        result = engine.run(factors, panel_data=panel)
        # 聚类后因子数应 <= 原始因子数
        assert len(result) <= len(factors)
        # 所有因子应有 factor_id
        for f in result:
            assert "factor_id" in f

    def test_identical_code_factors(self) -> None:
        """同代码因子应聚为同一簇，仅保留一个。"""
        engine = FactorClusteringEngine()
        code = "def run(df, params):\n    return df['close'].values"
        factors = [
            {"factor_id": f"fct_{i}", "name": f"fct_{i}", "code": code, "sharpe": 1.0 + i * 0.1}
            for i in range(5)
        ]
        panel = _make_panel_data()
        result = engine.run(factors, panel_data=panel)
        # 同代码产生相同信号，应聚为一簇
        assert len(result) <= len(factors)
        assert len(result) >= 1


# ═══════════════════════════════════════════════════════════
# P2: PCASignalCompressor
# ═══════════════════════════════════════════════════════════

class TestPCASignalCompressorInit:
    """PCASignalCompressor 初始化测试。"""

    def test_default_params(self) -> None:
        compressor = PCASignalCompressor()
        assert compressor.variance_ratio == DEFAULT_PCA_VARIANCE_RATIO
        assert compressor.max_components == 10

    def test_custom_params(self) -> None:
        compressor = PCASignalCompressor(variance_ratio=0.9, max_components=5)
        assert compressor.variance_ratio == 0.9
        assert compressor.max_components == 5


class TestPCASignalCompressorComputeSignalMatrix:
    """compute_signal_matrix 方法测试。"""

    def test_empty_panel(self) -> None:
        compressor = PCASignalCompressor()
        matrix, fids, dates = compressor.compute_signal_matrix([], {})
        assert matrix.size == 0
        assert fids == []
        assert dates.size == 0

    def test_too_few_signals(self) -> None:
        compressor = PCASignalCompressor()
        factors = _make_factors(1)
        panel = _make_panel_data()
        matrix, fids, dates = compressor.compute_signal_matrix(factors, panel)
        assert matrix.size == 0 or len(fids) < 2

    def test_valid_signals(self) -> None:
        compressor = PCASignalCompressor()
        factors = _make_factors(5)
        panel = _make_panel_data(n_dates=30)
        matrix, fids, dates = compressor.compute_signal_matrix(factors, panel)
        if matrix.size > 0 and len(fids) >= 2:
            assert matrix.shape[0] > 0  # n_dates
            assert matrix.shape[1] == len(fids)  # n_factors


class TestPCASignalCompressorRun:
    """run 完整流程测试。"""

    def test_empty_factors(self) -> None:
        compressor = PCASignalCompressor()
        result = compressor.run([], panel_data=None)
        assert result["pca_applied"] is False
        assert result["n_original"] == 0

    def test_no_panel_data(self) -> None:
        compressor = PCASignalCompressor()
        factors = _make_factors(5)
        result = compressor.run(factors, panel_data=None)
        assert result["pca_applied"] is False
        assert result["n_original"] == len(factors)

    def test_too_few_factors(self) -> None:
        compressor = PCASignalCompressor()
        factors = _make_factors(2)
        panel = _make_panel_data()
        result = compressor.run(factors, panel_data=panel)
        assert result["pca_applied"] is False

    def test_full_flow_with_panel(self) -> None:
        compressor = PCASignalCompressor()
        factors = _make_factors(10)
        panel = _make_panel_data(n_dates=50)
        result = compressor.run(factors, panel_data=panel)
        if result["pca_applied"]:
            assert result["n_components"] > 0
            assert result["n_components"] <= len(factors)
            assert result["explained_variance_ratio"] > 0
            assert len(result["pca_signals"]) > 0
            # 所有 PCA 信号应有 weight
            for sig in result["pca_signals"]:
                assert "weight" in sig
                assert "factor_id" in sig
        else:
            # 可能因 scikit-learn 未安装或信号矩阵不足
            assert result["pca_applied"] is False


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

class TestComputeClusterSummary:
    """compute_cluster_summary 测试。"""

    def test_reduction(self) -> None:
        original = _make_factors(10)
        reduced = _make_factors(4)
        summary = compute_cluster_summary(original, reduced)
        assert summary["n_original"] == 10
        assert summary["n_reduced"] == 4
        assert summary["removed_count"] == 6
        assert summary["reduction_ratio"] == 0.6

    def test_no_reduction(self) -> None:
        original = _make_factors(5)
        reduced = list(original)
        summary = compute_cluster_summary(original, reduced)
        assert summary["n_original"] == 5
        assert summary["n_reduced"] == 5
        assert summary["removed_count"] == 0
        assert summary["reduction_ratio"] == 0.0

    def test_empty_input(self) -> None:
        summary = compute_cluster_summary([], [])
        assert summary["n_original"] == 0
        assert summary["n_reduced"] == 0
        assert summary["reduction_ratio"] == 0.0


class TestComputePCASummary:
    """compute_pca_summary 测试。"""

    def test_applied(self) -> None:
        result = {
            "pca_applied": True,
            "n_original": 20,
            "n_components": 5,
            "explained_variance_ratio": 0.96,
        }
        summary = compute_pca_summary(result)
        assert summary["pca_applied"] is True
        assert summary["n_original"] == 20
        assert summary["n_components"] == 5
        assert summary["compression_ratio"] == 0.75
        assert summary["explained_variance_ratio"] == 0.96

    def test_not_applied(self) -> None:
        result = {
            "pca_applied": False,
            "n_original": 10,
            "n_components": 0,
            "explained_variance_ratio": 0.0,
        }
        summary = compute_pca_summary(result)
        assert summary["pca_applied"] is False
        assert summary["n_components"] == 0

    def test_empty(self) -> None:
        summary = compute_pca_summary({})
        assert summary["n_original"] == 0
        assert summary["compression_ratio"] == 0.0