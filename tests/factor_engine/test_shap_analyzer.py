"""
test_shap_analyzer.py — SHAP 局部可解释性分析测试

HARNESS §11-logic-review-plan.md §B.1:
    验证 SHAP 分析器可正确执行，输出符合预期。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from typing import Any

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.factor_program import create_factor_program
from fts.factor_engine.shap_analyzer import (
    ShapAnalysisResult,
    ShapAnalyzer,
    ShapFeatureImportance,
    ShapSampleAnalysis,
)


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fake_shap(monkeypatch):
    """注入 fake shap 模块（shap 为可选依赖，未安装时测试仍可运行）。

    ShapAnalyzer.analyze 延迟导入 `import shap`，此处用确定性 fake
    替代 KernelExplainer.shap_values，保证离线/无依赖环境可测试。
    """
    import numpy as np

    class _FakeExplainer:
        def __init__(self, predict_fn, X_background):
            self._bg = X_background

        def shap_values(self, X_sample, nsamples=100):
            # 返回与输入同形的确定性 SHAP 值（正负交替），供排序/统计使用
            arr = np.arange(X_sample.shape[0] * X_sample.shape[1], dtype=np.float64).reshape(
                X_sample.shape
            ) % 3 - 1.0
            return arr

    fake = types.ModuleType("shap")
    fake.KernelExplainer = _FakeExplainer
    monkeypatch.setitem(sys.modules, "shap", fake)
    return fake


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """生成 50 天的合成 OHLCV 数据（含 vwap）。"""
    np.random.seed(42)
    n = 50
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    volume = np.random.randint(1000, 10000, n).astype(float)
    return pd.DataFrame({
        "date": dates,
        "open": close + np.random.randn(n) * 0.1,
        "high": close + np.abs(np.random.randn(n)) * 0.3,
        "low": close - np.abs(np.random.randn(n)) * 0.3,
        "close": close,
        "volume": volume,
        "vwap": close + np.random.randn(n) * 0.05,
    })


@pytest.fixture
def forward_returns() -> np.ndarray:
    """生成未来收益率。"""
    np.random.seed(42)
    n = 50
    ret = np.random.randn(n) * 0.01
    ret[-1] = 0.0
    return ret


@pytest.fixture
def simple_momentum_factor() -> FactorProgram:
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


# ─── 测试 ShapAnalyzer ────────────────────────────────────


class TestShapAnalyzerInit:
    """测试 ShapAnalyzer 初始化。"""

    def test_default_init(self):
        """默认参数应正确设置。"""
        analyzer = ShapAnalyzer()
        assert analyzer._n_extreme == 50
        assert analyzer._n_background == 100
        assert analyzer._random_seed == 42

    def test_custom_init(self):
        """自定义参数应正确设置。"""
        analyzer = ShapAnalyzer(n_extreme=10, n_background=20, random_seed=123)
        assert analyzer._n_extreme == 10
        assert analyzer._n_background == 20
        assert analyzer._random_seed == 123


class TestShapAnalyzerFeatureCols:
    """测试特征列提取。"""

    def test_get_feature_cols_excludes_date(self, sample_data):
        """date 列不应出现在特征列中。"""
        analyzer = ShapAnalyzer()
        cols = analyzer._get_feature_cols(sample_data)
        assert "date" not in cols
        assert "open" in cols
        assert "close" in cols
        assert "volume" in cols
        assert "vwap" in cols


class TestShapAnalyzerAnalyze:
    """测试 SHAP 分析执行。"""

    def test_analyze_returns_correct_structure(self, sample_data, forward_returns, simple_momentum_factor):
        """analyze() 返回 ShapAnalysisResult 且包含必要字段。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10)
        result = analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)

        assert isinstance(result, ShapAnalysisResult)
        assert result["factor_id"] == simple_momentum_factor["factor_id"]
        assert result["factor_name"] == simple_momentum_factor["name"]
        assert result["num_extreme_samples"] == 2
        assert result["num_features"] > 0
        assert isinstance(result["summary"], dict)
        assert isinstance(result["global_top_features"], list)

    def test_analyze_includes_top_and_bottom_samples(self, sample_data, forward_returns, simple_momentum_factor):
        """分析结果应包含 top 和 bottom 样本。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10)
        result = analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)

        assert len(result["top_samples"]) == 2
        assert len(result["bottom_samples"]) == 2

        for sample in result["top_samples"]:
            assert isinstance(sample, ShapSampleAnalysis)
            assert "sample_index" in sample
            assert "signal_value" in sample
            assert "top_features" in sample
            assert len(sample["top_features"]) <= 5

        for sample in result["bottom_samples"]:
            assert isinstance(sample, ShapSampleAnalysis)
            assert "sample_index" in sample
            assert "signal_value" in sample
            assert "top_features" in sample
            assert len(sample["top_features"]) <= 5

    def test_analyze_global_top_features(self, sample_data, forward_returns, simple_momentum_factor):
        """全局 top-5 特征应正确返回。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10)
        result = analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)

        assert len(result["global_top_features"]) <= 5
        for feat in result["global_top_features"]:
            assert isinstance(feat, ShapFeatureImportance)
            assert "feature_name" in feat
            assert "shap_value" in feat
            assert "impact_direction" in feat

    def test_analyze_summary(self, sample_data, forward_returns, simple_momentum_factor):
        """汇总信息应包含必要字段。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10)
        result = analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)

        summary = result["summary"]
        assert "total_samples" in summary
        assert "valid_samples" in summary
        assert "n_extreme_analyzed" in summary
        assert "n_features" in summary
        assert "feature_names" in summary
        assert "signal_range" in summary
        assert summary["total_samples"] == 50

    def test_analyze_without_forward_returns(self, sample_data, simple_momentum_factor):
        """不传 forward_returns 时仍可使用信号值排序。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10)
        result = analyzer.analyze(simple_momentum_factor, sample_data)

        assert isinstance(result, ShapAnalysisResult)
        assert len(result["top_samples"]) == 2
        assert len(result["bottom_samples"]) == 2


class TestShapAnalyzerReport:
    """测试报告生成。"""

    def test_report_returns_string(self, sample_data, forward_returns, simple_momentum_factor):
        """report() 应返回字符串。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10)
        result = analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)
        report_str = analyzer.report(result)

        assert isinstance(report_str, str)
        assert len(report_str) > 0
        assert simple_momentum_factor["factor_id"] in report_str
        assert simple_momentum_factor["name"] in report_str
        assert "SHAP 分析报告" in report_str

    def test_report_contains_extreme_sections(self, sample_data, forward_returns, simple_momentum_factor):
        """报告应包含 top 和 bottom 样本章节。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10)
        result = analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)
        report_str = analyzer.report(result)

        assert "Top 2" in report_str
        assert "Bottom 2" in report_str
        assert "汇总" in report_str


class TestShapAnalyzerSaveReport:
    """测试报告保存。"""

    def test_save_report_creates_json(self, sample_data, forward_returns, simple_momentum_factor):
        """save_report() 应创建 JSON 文件。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10)
        result = analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = analyzer.save_report(result, tmpdir)
            assert os.path.exists(filepath)
            assert filepath.endswith(".json")
            assert result["factor_id"] in filepath

            with open(filepath, "r", encoding="utf-8") as f:
                saved = json.load(f)
            assert saved["factor_id"] == result["factor_id"]
            assert saved["factor_name"] == result["factor_name"]
            assert len(saved["top_samples"]) == 2

    def test_save_report_creates_date_subdir(self, sample_data, forward_returns, simple_momentum_factor):
        """save_report() 应在日期子目录下创建文件。"""
        analyzer = ShapAnalyzer(n_extreme=2, n_background=10)
        result = analyzer.analyze(simple_momentum_factor, sample_data, forward_returns)

        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = analyzer.save_report(result, tmpdir)
            # 文件路径应包含日期子目录
            relative = os.path.relpath(filepath, tmpdir)
            assert len(relative.split(os.sep)) >= 2  # 至少有 date/filename.json


class TestShapAnalyzerEdgeCases:
    """测试边界情况。"""

    def test_small_dataset(self, simple_momentum_factor):
        """小数据集不应崩溃。"""
        np.random.seed(42)
        n = 10
        data = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": np.random.randn(n) * 0.1 + 100,
            "high": np.random.randn(n) * 0.3 + 100,
            "low": np.random.randn(n) * 0.3 + 100,
            "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
            "volume": np.random.randint(1000, 10000, n).astype(float),
        })
        forward_returns = np.random.randn(n) * 0.01

        analyzer = ShapAnalyzer(n_extreme=2, n_background=5)
        result = analyzer.analyze(simple_momentum_factor, data, forward_returns)

        assert isinstance(result, ShapAnalysisResult)
        assert result["summary"]["total_samples"] == 10

    def test_single_feature_data(self, simple_momentum_factor):
        """只有 close 列的数据。"""
        np.random.seed(42)
        n = 20
        data = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
        })
        forward_returns = np.random.randn(n) * 0.01

        analyzer = ShapAnalyzer(n_extreme=2, n_background=5)
        result = analyzer.analyze(simple_momentum_factor, data, forward_returns)

        assert isinstance(result, ShapAnalysisResult)
        assert result["num_features"] >= 1