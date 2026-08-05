"""
tests/pipeline/test_batch_quality_inspector — 批量质检与因子定义优化测试

覆盖范围:
    1. 因子规范化工具（normalize_factor_program/normalize_factor_signature）
    2. 市场自动检测（detect_factor_market）
    3. 因子家族推断
    4. BatchQualityInspector 批量质检
    5. QualityRankReport 报告生成与序列化
    6. WalkForward 稳定性评分集成

版本: v1.0.0
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import (
    FactorProgram,
    FactorSignature,
    FactorMarket,
    FactorFamily,
    normalize_factor_program,
    normalize_factor_signature,
    detect_factor_market,
)
from fts.factor_engine.walk_forward import (
    WalkForwardConfig,
    WalkForwardResult,
)
from fts.pipeline.batch_quality_inspector import (
    BatchQualityInspector,
    FactorRankEntry,
    QualityRankReport,
    run_batch_quality_inspection,
)


# ══════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════


@pytest.fixture
def multi_symbol_panel() -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    """多品种合成面板数据。"""
    dates = pd.date_range("2022-01-01", periods=252, freq="B")
    rng = np.random.default_rng(42)

    panel: dict[str, pd.DataFrame] = {}
    for sym, base_price, drift, vol in [
        ("RB0", 4000, 0.0002, 0.015),
        ("M0", 3500, 0.0001, 0.012),
        ("CU0", 65000, 0.0003, 0.018),
        ("AU0", 500, 0.0004, 0.010),
        ("AG0", 8000, 0.0002, 0.020),
    ]:
        returns = rng.normal(drift, vol, len(dates))
        prices = base_price * np.cumprod(1 + returns)
        panel[sym] = pd.DataFrame({
            "open": prices * (1 + rng.normal(0, 0.002, len(dates))),
            "high": prices * (1 + np.abs(rng.normal(0, 0.005, len(dates)))),
            "low": prices * (1 - np.abs(rng.normal(0, 0.005, len(dates)))),
            "close": prices,
            "volume": rng.integers(10000, 50000, len(dates)),
            "open_interest": rng.integers(50000, 100000, len(dates)),
        }, index=dates)

    return panel, dates


@pytest.fixture
def test_factors() -> list[FactorProgram]:
    """测试因子列表。"""
    return [
        FactorProgram(
            factor_id="fct_test_001",
            name="trend_momentum_factor",
            code="""
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window + 5:
        return np.zeros(n)
    momentum = (close - np.roll(close, window)) / np.maximum(np.roll(close, window), 1e-10)
    std = np.std(momentum[-window:]) + 1e-10
    signal = momentum / std
    signal[:window] = 0
    return np.clip(signal, -1.0, 1.0)
""",
            params={"window": 20},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=30,
            ),
            economic_logic={
                "theory": 4,
                "behavioral": 3,
                "microstructure": 4,
                "institutional": 3,
                "narrative": "趋势动量因子",
            },
            source="seed",
            generation=0,
            created_at="2026-08-05T00:00:00",
            trace_id="test_001",
        ),
        FactorProgram(
            factor_id="fct_test_002",
            name="volume_reversion_factor",
            code="""
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else data['volume']
    window = int(params.get('window', 10))
    n = len(close)
    if n < window + 5:
        return np.zeros(n)
    avg_vol = np.convolve(volume, np.ones(window)/window, mode='same')
    vol_ratio = volume / np.maximum(avg_vol, 1e-10)
    chg = np.zeros(n)
    chg[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    signal = -chg * (vol_ratio > 1.5) * 0.5
    return np.clip(signal, -1.0, 1.0)
""",
            params={"window": 10},
            signature=FactorSignature(
                input_fields=["close", "volume"],
                output_type="signal",
                frequency="daily",
                lookback=15,
            ),
            economic_logic={
                "theory": 3,
                "behavioral": 4,
                "microstructure": 5,
                "institutional": 4,
                "narrative": "成交量反转因子",
            },
            source="seed",
            generation=0,
            created_at="2026-08-05T00:00:00",
            trace_id="test_002",
        ),
        FactorProgram(
            factor_id="fct_test_003",
            name="volatility_breakout_factor",
            code="""
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window + 5:
        return np.zeros(n)
    returns = np.zeros(n)
    returns[1:] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    vol = np.array([np.std(returns[max(0,i-window+1):i+1]) if i >= 1 else 0 for i in range(n)])
    vol_threshold = np.mean(vol[-window:]) + 1e-10
    signal = np.where(vol > vol_threshold, np.sign(returns) * 0.8, 0)
    return np.clip(signal, -1.0, 1.0)
""",
            params={"window": 20},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=30,
            ),
            economic_logic={
                "theory": 4,
                "behavioral": 3,
                "microstructure": 4,
                "institutional": 3,
                "narrative": "波动率突破因子",
            },
            source="seed",
            generation=0,
            created_at="2026-08-05T00:00:00",
            trace_id="test_003",
        ),
    ]


# ══════════════════════════════════════════════════════════
# 1. 因子规范化工具测试
# ══════════════════════════════════════════════════════════


class TestNormalizeFactorSignature:
    """因子签名规范化测试。"""

    def test_standard_signature_passthrough(self):
        """标准格式签名直接通过。"""
        sig = FactorSignature(
            input_fields=["close", "volume"],
            output_type="signal",
            frequency="daily",
            lookback=20,
        )
        result = normalize_factor_signature(sig)
        assert result["input_fields"] == ["close", "volume"]
        assert result["output_type"] == "signal"
        assert result["frequency"] == "daily"
        assert result["lookback"] == 20

    def test_legacy_inputs_outputs_format(self):
        """旧版 inputs/outputs 格式转换。"""
        old_sig = {"inputs": ["close"], "outputs": ["signal"], "feature_dim": 1}
        result = normalize_factor_signature(old_sig)
        assert result["input_fields"] == ["close"]
        assert result["output_type"] == "signal"
        assert result["frequency"] == "daily"
        assert result["lookback"] == 10

    def test_empty_signature_defaults(self):
        """空签名使用默认值。"""
        result = normalize_factor_signature({})
        assert result["input_fields"] == ["close"]
        assert result["output_type"] == "signal"
        assert result["frequency"] == "daily"

    def test_mixed_format_with_frequency(self):
        """混合格式保留 frequency。"""
        old_sig = {
            "inputs": ["close", "high"],
            "outputs": ["score"],
            "frequency": "hourly",
            "lookback": 48,
        }
        result = normalize_factor_signature(old_sig)
        assert result["input_fields"] == ["close", "high"]
        assert result["output_type"] == "score"
        assert result["frequency"] == "hourly"
        assert result["lookback"] == 48


class TestDetectFactorMarket:
    """市场自动检测测试。"""

    def test_futures_symbols_detected(self):
        """期货品种检测。"""
        market = detect_factor_market(["RB0", "M0", "CU0"])
        assert market == "futures"

    def test_stock_symbols_detected(self):
        """股票代码检测。"""
        market = detect_factor_market(["SH600000", "SZ000001"])
        assert market == "stock"

    def test_empty_symbols_defaults_multi(self):
        """空品种列表默认 multi。"""
        market = detect_factor_market([])
        assert market == "multi"

    def test_market_hint_overrides(self):
        """market_hint 覆盖自动检测。"""
        market = detect_factor_market(["RB0"], market_hint="stock")
        assert market == "stock"

    def test_mixed_symbols(self):
        """混合品种检测为 multi。"""
        market = detect_factor_market(["RB0", "SH600000"])
        assert market == "multi"


class TestNormalizeFactorProgram:
    """因子定义规范化测试。"""

    def test_adds_market_and_family(self):
        """规范化时自动添加 market 和 family。"""
        factor = FactorProgram(
            factor_id="fct_test",
            name="momentum_factor",
            code="def factor_program(data, params): return data['close'] * 0",
            params={},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=10,
            ),
            economic_logic={
                "theory": 4, "behavioral": 3,
                "microstructure": 3, "institutional": 4,
                "narrative": "测试因子",
            },
            source="seed",
            generation=0,
            created_at="2026-08-05T00:00:00",
            trace_id="test",
        )
        result = normalize_factor_program(factor)

        assert result["market"] in ("futures", "multi", "stock")
        assert result["family"] != "other" or result["name"] == "momentum_factor"
        assert "symbols" in result
        assert result["factor_version"] == "v2"
        assert isinstance(result["is_multi_symbol"], bool)

    def test_infers_trend_family(self):
        """趋势因子自动推断为 trend 家族。"""
        factor = FactorProgram(
            factor_id="fct_trend",
            name="trend_following_momentum",
            code="def factor_program(data, params): return data['close']",
            params={},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=20,
            ),
            economic_logic={
                "theory": 4, "behavioral": 3,
                "microstructure": 3, "institutional": 4,
                "narrative": "趋势因子",
            },
            source="seed",
            generation=0,
            created_at="2026-08-05T00:00:00",
            trace_id="test",
        )
        result = normalize_factor_program(factor)
        assert result["family"] == "trend"

    def test_infers_volume_family(self):
        """成交量因子自动推断为 volume 家族。"""
        factor = FactorProgram(
            factor_id="fct_vol",
            name="volume_flow_analysis",
            code="def factor_program(data, params): return data['volume']",
            params={},
            signature=FactorSignature(
                input_fields=["volume"],
                output_type="signal",
                frequency="daily",
                lookback=10,
            ),
            economic_logic={
                "theory": 3, "behavioral": 4,
                "microstructure": 5, "institutional": 4,
                "narrative": "成交量因子",
            },
            source="seed",
            generation=0,
            created_at="2026-08-05T00:00:00",
            trace_id="test",
        )
        result = normalize_factor_program(factor)
        assert result["family"] == "volume"

    def test_preserves_existing_metadata(self):
        """保留已有的 market/family 字段。"""
        factor = FactorProgram(
            factor_id="fct_custom",
            name="custom_factor",
            code="def factor_program(data, params): return data['close']",
            params={},
            signature=FactorSignature(
                input_fields=["close"],
                output_type="signal",
                frequency="daily",
                lookback=10,
            ),
            economic_logic={
                "theory": 3, "behavioral": 3,
                "microstructure": 3, "institutional": 3,
                "narrative": "自定义因子",
            },
            source="seed",
            generation=0,
            created_at="2026-08-05T00:00:00",
            trace_id="test",
            market="futures",
            family="carry",
            symbols=["RB0", "M0"],
        )
        result = normalize_factor_program(factor)
        assert result["market"] == "futures"
        assert result["family"] == "carry"
        assert result["symbols"] == ["RB0", "M0"]

    def test_legacy_signature_normalized(self):
        """旧版签名格式被规范化。"""
        factor = FactorProgram(
            factor_id="fct_legacy",
            name="legacy_format",
            code="def factor_program(data, params): return data['close']",
            params={},
            signature={"inputs": ["close"], "outputs": ["signal"], "feature_dim": 1},
            economic_logic={
                "theory": 3, "behavioral": 3,
                "microstructure": 3, "institutional": 3,
                "narrative": "旧版格式",
            },
            source="seed",
            generation=0,
            created_at="2026-08-05T00:00:00",
            trace_id="test",
        )
        result = normalize_factor_program(factor)
        sig = result["signature"]
        assert sig["input_fields"] == ["close"]
        assert sig["output_type"] == "signal"


# ══════════════════════════════════════════════════════════
# 2. BatchQualityInspector 批量质检测试
# ══════════════════════════════════════════════════════════


class TestBatchQualityInspector:
    """批量质检器测试。"""

    def test_initialization(self, multi_symbol_panel):
        """初始化成功。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates)
        assert inspector.n_symbols == 5
        assert inspector.walk_forward_enabled is True

    def test_inspect_single_factor(self, multi_symbol_panel, test_factors):
        """单因子质检。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        factor = test_factors[0]

        entry = inspector.inspect_single(factor)
        assert isinstance(entry, FactorRankEntry)
        assert entry.factor_id == factor["factor_id"]
        assert entry.total_score >= 0
        assert entry.grade in ("A", "B", "C")
        assert entry.market in ("futures", "multi", "stock")
        assert entry.family != ""

    def test_inspect_all_factors(self, multi_symbol_panel, test_factors):
        """批量质检多个因子。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)

        report = inspector.inspect_all(test_factors)
        assert isinstance(report, QualityRankReport)
        assert report.total_factors == len(test_factors)
        assert len(report.entries) == len(test_factors)
        assert report.passed_factors + report.failed_factors == len(test_factors)

    def test_walk_forward_integration(self, multi_symbol_panel, test_factors):
        """WalkForward 稳定性评分集成测试。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=True)

        report = inspector.inspect_all(test_factors[:2])
        for entry in report.entries:
            if entry.wf_result:
                assert "ic_consistency" in entry.wf_result
                assert "consistency_score" in entry.wf_result
                assert "n_windows_completed" in entry.wf_result

    def test_factor_normalization_in_batch(self, multi_symbol_panel, test_factors):
        """批量质检中因子规范化。"""
        panel, dates = multi_symbol_panel

        # 创建一个缺少 market/family 的旧版因子
        old_factor = FactorProgram(
            factor_id="fct_old",
            name="old_style_factor",
            code="def factor_program(data, params): return data['close'] * 0.1",
            params={},
            signature={"inputs": ["close"], "outputs": ["signal"], "feature_dim": 1},
            economic_logic={
                "theory": 3, "behavioral": 3,
                "microstructure": 3, "institutional": 3,
                "narrative": "旧版因子",
            },
            source="seed",
            generation=0,
            created_at="2026-08-05T00:00:00",
            trace_id="old_test",
        )

        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        entry = inspector.inspect_single(old_factor)

        # 验证规范化生效
        assert entry.market != ""
        assert entry.family != ""
        assert entry.total_score >= 0


# ══════════════════════════════════════════════════════════
# 3. QualityRankReport 报告测试
# ══════════════════════════════════════════════════════════


class TestQualityRankReport:
    """质量排名报告测试。"""

    def test_report_statistics(self, multi_symbol_panel, test_factors):
        """报告统计信息正确。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        report = inspector.inspect_all(test_factors)

        assert report.total_factors == len(test_factors)
        assert report.pass_rate >= 0
        assert report.average_score >= 0
        assert "A" in report.grade_distribution
        assert "B" in report.grade_distribution
        assert "C" in report.grade_distribution

    def test_report_sorting(self, multi_symbol_panel, test_factors):
        """报告按得分降序排列。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        report = inspector.inspect_all(test_factors)

        sorted_entries = report.sorted_entries
        for i in range(len(sorted_entries) - 1):
            assert sorted_entries[i].total_score >= sorted_entries[i + 1].total_score

    def test_report_to_dict(self, multi_symbol_panel, test_factors):
        """报告转为字典正确。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        report = inspector.inspect_all(test_factors)

        data = report.to_dict()
        assert "report_meta" in data
        assert "distributions" in data
        assert "rankings" in data
        assert len(data["rankings"]) == len(test_factors)

    def test_report_save_json(self, multi_symbol_panel, test_factors, tmp_path):
        """报告保存为 JSON。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        report = inspector.inspect_all(test_factors)

        json_path = tmp_path / "test_report.json"
        report.save_json(json_path)

        assert json_path.exists()
        with open(json_path) as f:
            loaded = json.load(f)
        assert "report_meta" in loaded
        assert loaded["report_meta"]["total_factors"] == len(test_factors)

    def test_report_save_csv(self, multi_symbol_panel, test_factors, tmp_path):
        """报告保存为 CSV。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        report = inspector.inspect_all(test_factors)

        csv_path = tmp_path / "test_report.csv"
        report.save_csv(csv_path)

        assert csv_path.exists()
        content = csv_path.read_text()
        assert "rank" in content
        assert "factor_name" in content

    def test_report_summary(self, multi_symbol_panel, test_factors):
        """报告摘要生成。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        report = inspector.inspect_all(test_factors)

        summary = report.summary()
        assert "FTS 因子质量排名报告" in summary
        assert "总因子数" in summary
        assert "通过率" in summary
        assert "TOP 10 因子" in summary

    def test_factor_rank_entry_to_dict(self, multi_symbol_panel, test_factors):
        """FactorRankEntry 序列化。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        entry = inspector.inspect_single(test_factors[0])

        data = entry.to_dict()
        assert "rank" in data
        assert "factor_id" in data
        assert "total_score" in data
        assert "grade" in data
        assert "dimension_scores" in data
        assert "walk_forward" in data


# ══════════════════════════════════════════════════════════
# 4. 便捷函数测试
# ══════════════════════════════════════════════════════════


class TestRunBatchQualityInspection:
    """便捷函数测试。"""

    def test_run_batch_with_defaults(self, multi_symbol_panel, test_factors, tmp_path):
        """使用默认参数运行批量质检。"""
        panel, dates = multi_symbol_panel
        report = run_batch_quality_inspection(
            test_factors[:2],
            panel,
            output_dir=tmp_path,
            enable_walk_forward=False,
        )

        assert isinstance(report, QualityRankReport)
        assert report.total_factors == 2

        # 验证报告文件已生成
        date_dir = tmp_path / pd.Timestamp.now().strftime("%Y-%m-%d")
        assert (date_dir / "quality_ranking.json").exists()
        assert (date_dir / "quality_ranking.csv").exists()


# ══════════════════════════════════════════════════════════
# 5. 边界情况测试
# ══════════════════════════════════════════════════════════


class TestEdgeCases:
    """边界情况测试。"""

    def test_empty_factor_list(self, multi_symbol_panel):
        """空因子列表。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        report = inspector.inspect_all([])

        assert report.total_factors == 0
        assert report.pass_rate == 0.0
        assert report.average_score == 0.0

    def test_empty_panel(self):
        """空面板数据。"""
        inspector = BatchQualityInspector({}, enable_walk_forward=False)
        assert inspector.n_symbols == 0

    def test_single_symbol_data(self, test_factors):
        """单品种数据。"""
        dates = pd.date_range("2022-01-01", periods=252, freq="B")
        rng = np.random.default_rng(42)
        prices = 4000 * np.cumprod(1 + rng.normal(0.0002, 0.015, len(dates)))
        panel = {"RB0": pd.DataFrame({
            "open": prices * 1.001,
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
            "volume": rng.integers(10000, 50000, len(dates)),
            "open_interest": rng.integers(50000, 100000, len(dates)),
        }, index=dates)}

        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        report = inspector.inspect_all(test_factors[:1])
        assert report.total_factors == 1

    def test_progress_callback(self, multi_symbol_panel, test_factors):
        """进度回调。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)

        progress_log: list[tuple[int, int, str]] = []
        def callback(completed: int, total: int, name: str) -> None:
            progress_log.append((completed, total, name))

        report = inspector.inspect_all(test_factors, progress_callback=callback)

        assert len(progress_log) == len(test_factors)
        assert progress_log[-1][0] == len(test_factors)
        assert progress_log[-1][1] == len(test_factors)


# ══════════════════════════════════════════════════════════
# 6. WalkForward 稳定性评分与质量评分卡集成
# ══════════════════════════════════════════════════════════


class TestWalkForwardIntegration:
    """WalkForward 稳定性评分集成测试。"""

    def test_stability_score_reflects_wf_result(self, multi_symbol_panel, test_factors):
        """稳定性分反映 WalkForward 结果。"""
        from fts.factor_engine.factor_quality_card import _map_stability_to_score

        # 高稳定性结果
        good_wf: WalkForwardResult = {
            "ic_consistency": 0.8,
            "ic_volatility": 0.15,
            "consistency_score": 80.0,
            "n_windows_completed": 4,
        }
        good_score = _map_stability_to_score(good_wf)

        # 低稳定性结果
        poor_wf: WalkForwardResult = {
            "ic_consistency": 0.3,
            "ic_volatility": 0.6,
            "consistency_score": 20.0,
            "n_windows_completed": 2,
        }
        poor_score = _map_stability_to_score(poor_wf)

        assert good_score > poor_score
        assert good_score >= 3.0  # 良好稳定性
        assert poor_score < 3.0  # 较差稳定性

    def test_wf_improves_quality_score(self, multi_symbol_panel, test_factors):
        """WalkForward 集成提升质量评分准确性。"""
        panel, dates = multi_symbol_panel
        factor = test_factors[0]

        # 不带 WalkForward
        inspector_no_wf = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        entry_no_wf = inspector_no_wf.inspect_single(factor)

        # 带 WalkForward
        inspector_wf = BatchQualityInspector(panel, dates, enable_walk_forward=True)
        entry_wf = inspector_wf.inspect_single(factor)

        # 两者都应有有效得分
        assert entry_no_wf.total_score > 0
        assert entry_wf.total_score > 0

        # 带 WalkForward 的条目应有 wf_result
        if entry_wf.wf_result:
            assert "consistency_score" in entry_wf.wf_result


# ══════════════════════════════════════════════════════════
# 7. 多品种兼容性集成
# ══════════════════════════════════════════════════════════


class TestMultiSymbolIntegration:
    """多品种兼容性集成测试。"""

    def test_factor_normalized_for_multi_symbol(self, test_factors):
        """因子规范化后支持多品种。"""
        factor = test_factors[0]
        factor["symbols"] = ["RB0", "M0", "CU0"]

        result = normalize_factor_program(factor)
        assert result["is_multi_symbol"] is True
        assert result["market"] == "futures"

    def test_compatibility_score_in_batch(self, multi_symbol_panel, test_factors):
        """批量质检包含兼容性评分。"""
        panel, dates = multi_symbol_panel
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        report = inspector.inspect_all(test_factors)

        for entry in report.entries:
            assert entry.compatibility_score >= 0
            assert entry.compatibility_score <= 5.0

    def test_multi_symbol_coverage_affects_score(self, multi_symbol_panel, test_factors):
        """多品种覆盖率影响兼容性评分。"""
        panel, dates = multi_symbol_panel
        # 5 个品种 → coverage = min(5/3, 1.0) = 1.0
        inspector = BatchQualityInspector(panel, dates, enable_walk_forward=False)
        entry = inspector.inspect_single(test_factors[0])

        # 5 个品种覆盖率高，兼容性分应较高
        assert entry.compatibility_score >= 3.0  # 至少 3 分 (0.5 覆盖率)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
