"""
tests/test_cross_market.py — 跨市场泛化验证模块测试

覆盖:
    1. CrossMarketDataAdapter 数据适配
    2. CrossMarketEngine 分类逻辑
    3. CrossMarketEngine 报告生成
    4. 边缘情况：空因子、空面板、空结果
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from fts.cross_market.data_adapter import (
    CrossMarketDataAdapter,
    CORE_FIELDS,
    TARGET_MARKET_STOCK,
    TARGET_MARKET_FUTURES,
)
from fts.cross_market.engine import (
    CrossMarketEngine,
    CrossMarketResult,
    CrossMarketReport,
    GENERALIZATION_THRESHOLD,
    RETENTION_RATIO,
    FAILURE_THRESHOLD,
    FUTURES_SPECIFIC_THRESHOLD,
)


# ══════════════════════════════════════════════════════════
# 1. CrossMarketDataAdapter 测试
# ══════════════════════════════════════════════════════════


class TestCrossMarketDataAdapter:
    """测试数据适配层的核心功能。"""

    def test_adapt_dataframe_stock(self):
        """股票 DataFrame 适配应保留核心字段。"""
        adapter = CrossMarketDataAdapter()
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame(
            {
                "open": np.random.randn(10) + 10,
                "high": np.random.randn(10) + 11,
                "low": np.random.randn(10) + 9,
                "close": np.random.randn(10) + 10,
                "volume": np.random.randint(1000, 10000, 10).astype(float),
            },
            index=dates,
        )

        result = adapter._adapt_dataframe(df, TARGET_MARKET_STOCK)

        # 应包含所有核心字段
        for field in CORE_FIELDS:
            assert field in result.columns, f"缺少核心字段: {field}"

        # 不应包含期货特有字段
        assert "open_interest" not in result.columns
        assert "hold" not in result.columns
        assert "settle" not in result.columns

        # 数据类型应为 float
        for field in CORE_FIELDS:
            assert result[field].dtype == float, f"{field} 类型应为 float"

    def test_adapt_dataframe_futures(self):
        """期货 DataFrame 适配应保留核心字段和期货特有字段。"""
        adapter = CrossMarketDataAdapter()
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame(
            {
                "open": np.random.randn(10) + 10,
                "high": np.random.randn(10) + 11,
                "low": np.random.randn(10) + 9,
                "close": np.random.randn(10) + 10,
                "volume": np.random.randint(1000, 10000, 10).astype(float),
                "open_interest": np.random.randint(10000, 50000, 10).astype(float),
                "hold": np.random.randint(100, 500, 10).astype(float),
                "settle": np.random.randn(10) + 10,
            },
            index=dates,
        )

        result = adapter._adapt_dataframe(df, TARGET_MARKET_FUTURES)

        # 应包含所有核心字段
        for field in CORE_FIELDS:
            assert field in result.columns, f"缺少核心字段: {field}"

        # 应包含期货特有字段
        assert "open_interest" in result.columns
        assert "hold" in result.columns
        assert "settle" in result.columns

    def test_adapt_dataframe_missing_fields(self):
        """缺失字段应被填充为 0.0。"""
        adapter = CrossMarketDataAdapter()
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        # 只提供 close 字段
        df = pd.DataFrame(
            {
                "close": np.random.randn(10) + 10,
            },
            index=dates,
        )

        result = adapter._adapt_dataframe(df, TARGET_MARKET_STOCK)

        # 所有核心字段应存在
        for field in CORE_FIELDS:
            assert field in result.columns, f"缺少核心字段: {field}"

        # 非 close 字段应为 0.0
        for field in ["open", "high", "low", "volume"]:
            assert (result[field] == 0.0).all(), f"{field} 应填充为 0.0"

    def test_adapt_panel(self):
        """面板适配应正确处理多个品种。"""
        adapter = CrossMarketDataAdapter()
        dates = pd.date_range("2024-01-01", periods=10, freq="D")

        panel = {
            "000001": pd.DataFrame(
                {
                    "close": np.random.randn(10) + 10,
                    "volume": np.random.randint(1000, 10000, 10).astype(float),
                },
                index=dates,
            ),
            "000002": pd.DataFrame(
                {
                    "close": np.random.randn(10) + 10,
                    "volume": np.random.randint(1000, 10000, 10).astype(float),
                },
                index=dates,
            ),
        }

        result = adapter._adapt_panel(panel, TARGET_MARKET_STOCK)

        assert len(result) == 2
        for sym in ["000001", "000002"]:
            assert sym in result
            for field in CORE_FIELDS:
                assert field in result[sym].columns

    @patch("fts.cross_market.data_adapter.CrossMarketDataAdapter._adapt_dataframe")
    def test_adapt_panel_failure_isolation(self, mock_adapt):
        """单个品种适配失败不应影响其他品种。"""
        adapter = CrossMarketDataAdapter()
        dates = pd.date_range("2024-01-01", periods=10, freq="D")

        panel = {
            "A": pd.DataFrame({"close": [1.0] * 10}, index=dates),
            "B": pd.DataFrame({"close": [2.0] * 10}, index=dates),
            "C": pd.DataFrame({"close": [3.0] * 10}, index=dates),
        }

        # B 适配失败（通过 DataFrame 的 close 值区分）
        def side_effect(df, market):
            if df is not None and "close" in df.columns:
                first_val = df["close"].iloc[0]
                if first_val == 2.0:
                    raise ValueError("B 适配失败")
            return df

        mock_adapt.side_effect = side_effect

        result = adapter._adapt_panel(panel, TARGET_MARKET_STOCK)

        assert "A" in result
        assert "B" not in result  # 失败品种被跳过
        assert "C" in result


# ══════════════════════════════════════════════════════════
# 2. CrossMarketEngine 分类逻辑测试
# ══════════════════════════════════════════════════════════


class TestCrossMarketEngineClassification:
    """测试引擎的因子分类逻辑。"""

    def setup_method(self):
        self.engine = CrossMarketEngine(adapter=MagicMock())

    def test_classify_universal(self):
        """高跨市场 IC + 高保持率 = 通用因子。"""
        result = self.engine._classify(
            source_ic=0.05,
            target_ic_abs=GENERALIZATION_THRESHOLD + 0.01,
            ic_retention=RETENTION_RATIO + 0.1,
        )
        assert result == "universal", f"预期 universal, 实际 {result}"

    def test_classify_failed(self):
        """低跨市场 IC + 高源 IC = 失效。"""
        result = self.engine._classify(
            source_ic=FUTURES_SPECIFIC_THRESHOLD + 0.01,
            target_ic_abs=FAILURE_THRESHOLD - 0.005,
            ic_retention=0.1,
        )
        assert result == "failed", f"预期 failed, 实际 {result}"

    def test_classify_futures_specific(self):
        """低跨市场 IC + 高源 IC + 保持率不高 = 期货特异。"""
        result = self.engine._classify(
            source_ic=FUTURES_SPECIFIC_THRESHOLD + 0.01,
            target_ic_abs=GENERALIZATION_THRESHOLD - 0.005,
            ic_retention=0.3,
        )
        assert result == "futures_specific", f"预期 futures_specific, 实际 {result}"

    def test_classify_unknown(self):
        """源 IC 本身很低 = 未知。"""
        result = self.engine._classify(
            source_ic=FUTURES_SPECIFIC_THRESHOLD - 0.02,
            target_ic_abs=0.01,
            ic_retention=0.5,
        )
        assert result == "unknown", f"预期 unknown, 实际 {result}"

    def test_classify_edge_case(self):
        """边界情况：IC 刚好达到阈值。"""
        result = self.engine._classify(
            source_ic=FUTURES_SPECIFIC_THRESHOLD,
            target_ic_abs=GENERALIZATION_THRESHOLD,
            ic_retention=RETENTION_RATIO,
        )
        # target_ic_abs >= GENERALIZATION_THRESHOLD 且 ic_retention >= RETENTION_RATIO
        assert result == "universal", f"边界情况预期 universal, 实际 {result}"


# ══════════════════════════════════════════════════════════
# 3. CrossMarketEngine 报告生成测试
# ══════════════════════════════════════════════════════════


class TestCrossMarketEngineReport:
    """测试报告生成功能。"""

    def setup_method(self):
        self.engine = CrossMarketEngine(adapter=MagicMock())

    def _make_sample_results(self) -> list[CrossMarketResult]:
        return [
            CrossMarketResult(
                name="fut_momentum",
                factor_id="fct_001",
                source_market="futures",
                target_market="stock",
                source_ic=0.05,
                target_ic=0.03,
                target_ic_abs=0.03,
                ic_retention=0.6,
                generalization="universal",
                n_target_symbols=50,
                n_dates=100,
                eval_time_sec=0.5,
                is_deprecated=False,
            ),
            CrossMarketResult(
                name="fut_basis",
                factor_id="fct_002",
                source_market="futures",
                target_market="stock",
                source_ic=0.06,
                target_ic=0.01,
                target_ic_abs=0.01,
                ic_retention=0.17,
                generalization="futures_specific",
                n_target_symbols=50,
                n_dates=100,
                eval_time_sec=0.5,
                is_deprecated=False,
            ),
            CrossMarketResult(
                name="fut_volume",
                factor_id="fct_003",
                source_market="futures",
                target_market="stock",
                source_ic=0.04,
                target_ic=-0.005,
                target_ic_abs=0.005,
                ic_retention=0.125,
                generalization="failed",
                n_target_symbols=50,
                n_dates=100,
                eval_time_sec=0.5,
                is_deprecated=False,
            ),
            CrossMarketResult(
                name="fut_carry_deprecated",
                factor_id="fct_004",
                source_market="futures",
                target_market="stock",
                source_ic=0.05,
                target_ic=0.0,
                target_ic_abs=0.0,
                ic_retention=0.0,
                generalization="failed",
                n_target_symbols=30,
                n_dates=100,
                eval_time_sec=0.5,
                is_deprecated=True,
            ),
        ]

    def test_generate_report_contents(self):
        """报告应包含所有必要章节。"""
        report = CrossMarketReport(
            generated_at="2026-08-07",
            source_market="futures",
            target_market="stock",
            total_factors=4,
            n_universal=1,
            n_market_specific=1,
            n_failed=2,
            n_deprecated=1,
            n_dates=100,
            n_target_symbols=50,
            elapsed_sec=3.5,
            results=self._make_sample_results(),
            target_ic_distribution=[0.03, 0.01, 0.005, 0.0],
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            report_path = self.engine.generate_report(report, output_path=output_path)

            content = Path(report_path).read_text(encoding="utf-8")

            # 应包含标题
            assert "# 跨市场泛化验证报告" in content
            # 应包含结果汇总
            assert "## 验证结果汇总" in content
            # 应包含 IC 分布
            assert "## 跨市场 IC 分布" in content
            # 应包含通用因子
            assert "## 🌍 通用因子" in content
            # 应包含市场特异因子
            assert "## 🔄 市场特异因子" in content
            # 应包含失效因子
            assert "## ❌ 跨市场失效因子" in content
            # 应包含全量对比
            assert "## 全量因子跨市场 IC 对比" in content

            # 应包含具体因子名称
            assert "fut_momentum" in content
            assert "fut_basis" in content
            assert "fut_volume" in content
            assert "fut_carry_deprecated" in content

            # 应包含统计数字
            assert "1" in content  # n_universal
            assert "2" in content  # n_failed

        finally:
            Path(output_path).unlink(missing_ok=True)

    def test_generate_report_empty(self):
        """空结果报告应正常生成。"""
        report = CrossMarketReport(
            generated_at="2026-08-07",
            source_market="futures",
            target_market="stock",
            total_factors=0,
            n_universal=0,
            n_market_specific=0,
            n_failed=0,
            n_deprecated=0,
            n_dates=0,
            n_target_symbols=0,
            elapsed_sec=0,
            results=[],
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            output_path = f.name

        try:
            report_path = self.engine.generate_report(report, output_path=output_path)
            content = Path(report_path).read_text(encoding="utf-8")
            assert "## 验证结果汇总" in content
            assert "通用因子 (跨市场有效): **0**" in content
        finally:
            Path(output_path).unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════
# 4. CrossMarketEngine 因子加载测试
# ══════════════════════════════════════════════════════════


class TestCrossMarketEngineLoading:
    """测试因子加载功能。"""

    def setup_method(self):
        self.engine = CrossMarketEngine(adapter=MagicMock())

    def test_load_factors_from_dir_empty(self):
        """空目录应返回空列表。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self.engine._load_factors_from_dir(Path(tmpdir))
            assert result == []

    def test_load_factors_from_dir_with_files(self):
        """包含 JSON 文件的目录应正确加载。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_path = Path(tmpdir)

            # 创建正常因子文件
            factor1 = {"factor_id": "fct_001", "name": "factor1", "evaluation": {"level_1_backtest": {"ic": 0.05}}}
            (dir_path / "factor1.json").write_text(json.dumps(factor1), encoding="utf-8")

            # 创建已降级因子
            deprecated_dir = dir_path / "_deprecated"
            deprecated_dir.mkdir()
            factor2 = {"factor_id": "fct_002", "name": "factor2", "evaluation": {"level_1_backtest": {"ic": 0.03}}}
            (deprecated_dir / "factor2.json").write_text(json.dumps(factor2), encoding="utf-8")

            # 创建无效 JSON 文件（应被跳过）
            (dir_path / "invalid.json").write_text("not json", encoding="utf-8")

            result = self.engine._load_factors_from_dir(dir_path)

            assert len(result) == 2
            assert not result[0]["_deprecated"]  # 第一个是活跃
            assert result[1]["_deprecated"]  # 第二个是降级

    def test_load_factors_from_dir_invalid_json(self):
        """无效 JSON 文件应被跳过。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_path = Path(tmpdir)
            (dir_path / "bad.json").write_text("{invalid json}", encoding="utf-8")

            result = self.engine._load_factors_from_dir(dir_path)
            assert result == []


# ══════════════════════════════════════════════════════════
# 5. 边缘情况测试
# ══════════════════════════════════════════════════════════


class TestCrossMarketEdgeCases:
    """测试边缘情况。"""

    def test_empty_report_creation(self):
        """空报告应包含所有必要字段。"""
        report = CrossMarketReport(
            generated_at="2026-08-07",
            source_market="futures",
            target_market="stock",
            total_factors=0,
            n_universal=0,
            n_market_specific=0,
            n_failed=0,
            n_deprecated=0,
            n_dates=0,
            n_target_symbols=0,
            elapsed_sec=0,
            results=[],
        )
        assert report.total_factors == 0
        assert report.n_universal == 0
        assert report.n_failed == 0

    def test_engine_no_factors(self):
        """无因子时应返回空报告。"""
        adapter = MagicMock()
        engine = CrossMarketEngine(adapter=adapter)

        # 模拟空因子列表
        with patch.object(engine, "_load_futures_factors", return_value=[]):
            report = engine.run_futures_to_stock(days=120)
            assert report.total_factors == 0
            assert report.n_universal == 0
            assert report.n_failed == 0

    def test_engine_no_data(self):
        """无数据时应返回空报告。"""
        adapter = MagicMock()
        adapter.get_panel.return_value = ({}, pd.DatetimeIndex([]))
        engine = CrossMarketEngine(adapter=adapter)

        with patch.object(
            engine,
            "_load_futures_factors",
            return_value=[{"factor_id": "fct_001", "name": "test", "evaluation": {"level_1_backtest": {"ic": 0.05}}}],
        ):
            report = engine.run_futures_to_stock(days=120)
            assert report.total_factors == 1
            assert report.n_dates == 0  # 无数据

    def test_cross_market_result_dataclass(self):
        """CrossMarketResult dataclass 应正确初始化。"""
        result = CrossMarketResult(
            name="test",
            factor_id="fct_001",
            source_market="futures",
            target_market="stock",
            source_ic=0.05,
            target_ic=0.02,
            target_ic_abs=0.02,
            ic_retention=0.4,
            generalization="futures_specific",
            n_target_symbols=30,
            n_dates=100,
            eval_time_sec=0.5,
            is_deprecated=False,
        )
        assert result.name == "test"
        assert result.generalization == "futures_specific"
        assert result.n_target_symbols == 30


# ══════════════════════════════════════════════════════════
# 6. 完整集成测试（模拟数据）
# ══════════════════════════════════════════════════════════


class TestCrossMarketIntegration:
    """集成测试 — 使用模拟数据验证完整流程。"""

    def test_full_pipeline_with_mock_data(self):
        """完整流程应正确分类因子。"""
        # 创建模拟因子
        factors = [
            {
                "factor_id": "fct_001",
                "name": "fut_momentum",
                "code": "def signal(df, params):\n    import numpy as np\n    close = df['close'].values\n    ret = np.zeros(len(close))\n    ret[20:] = (close[20:] - close[:-20]) / close[:-20]\n    return ret",
                "params": {},
                "signature": {"input_fields": ["close"], "output_type": "signal"},
                "evaluation": {"level_1_backtest": {"ic": 0.05}},
            },
        ]

        # 创建模拟面板数据
        n_dates = 200
        n_symbols = 10
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")

        panel = {}
        for i in range(n_symbols):
            sym = f"STOCK_{i:06d}"
            panel[sym] = pd.DataFrame(
                {
                    "open": np.random.randn(n_dates) + 10,
                    "high": np.random.randn(n_dates) + 11,
                    "low": np.random.randn(n_dates) + 9,
                    "close": np.cumsum(np.random.randn(n_dates) * 0.5) + 10,
                    "volume": np.random.randint(1000, 10000, n_dates).astype(float),
                },
                index=dates,
            )

        common_dates = dates

        # 模拟适配器
        adapter = MagicMock(spec=CrossMarketDataAdapter)
        adapter.get_panel.return_value = (panel, common_dates)

        # 模拟因子执行
        def mock_execute(factor_data, p, cd):
            signals = {}
            for sym, df in p.items():
                close = df["close"].values
                ret = np.zeros(len(close))
                ret[20:] = (close[20:] - close[:-20]) / np.maximum(close[:-20], 1e-10)
                signals[sym] = ret[: len(cd)]
            return signals

        adapter.execute_factor_on_market.side_effect = mock_execute

        engine = CrossMarketEngine(adapter=adapter)

        with patch.object(engine, "_load_futures_factors", return_value=factors):
            report = engine.run_futures_to_stock(days=n_dates)

            assert report.total_factors == 1
            assert report.n_dates == n_dates
            assert report.n_target_symbols == n_symbols

            # 因子应被正确分类（至少不抛异常）
            assert len(report.results) == 1
            assert report.results[0].name == "fut_momentum"

            # 报告应能正常生成
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
                output_path = f.name
            try:
                engine.generate_report(report, output_path=output_path)
                content = Path(output_path).read_text(encoding="utf-8")
                assert "fut_momentum" in content
            finally:
                Path(output_path).unlink(missing_ok=True)
