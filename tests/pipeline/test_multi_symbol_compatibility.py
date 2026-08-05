"""tests/pipeline/test_multi_symbol_compatibility.py — 多品种因子兼容性测试。

覆盖范围:
    1. 多品种因子定义 (RB0/螺纹钢, M0/豆粕)
    2. 跨品种覆盖率计算
    3. 横截面回测在多品种上的表现
    4. 质量评分卡在多品种下的评分
    5. WalkForward 在多品种下的稳定性验证

版本: v1.0.0
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.data_futures import FUTURES_CORE_SUBSET
from fts.factor_engine.contracts import (
    BacktestMetrics,
    EconomicScore,
    FactorEvaluation,
    FactorProgram,
    MultipleTestResult,
)
from fts.factor_engine.evaluation_chain import (
    cross_section_evaluate_backtest,
)
from fts.factor_engine.walk_forward import (
    WalkForwardConfig,
    WalkForwardOptimizer,
    WalkForwardResult,
    WalkForwardWindowResult,
)
from fts.factor_engine.factor_quality_card import (
    FactorQualityCard,
    FactorQualityScore,
)
from fts.pipeline.factor_quality_inspection import (
    FactorQualityInspection,
    InspectionResult,
)


# ══════════════════════════════════════════════════════════
# Fixtures: 多品种合成数据
# ══════════════════════════════════════════════════════════


@pytest.fixture
def multi_symbol_panel() -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    """RB0 + M0 + CU0 三个品种的合成面板数据。"""
    dates = pd.date_range("2022-01-01", periods=252, freq="B")
    rng = np.random.default_rng(42)

    panel: dict[str, pd.DataFrame] = {}
    for sym, base_price, drift, vol in [
        ("RB0", 4000, 0.0002, 0.015),
        ("M0", 3500, 0.0001, 0.012),
        ("CU0", 65000, 0.0003, 0.018),
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

    common_dates = dates
    return panel, common_dates


@pytest.fixture
def single_symbol_panel() -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
    """单品种面板数据 (RB0 only)。"""
    dates = pd.date_range("2022-01-01", periods=252, freq="B")
    rng = np.random.default_rng(123)

    prices = 4000 * np.cumprod(1 + rng.normal(0.0002, 0.015, len(dates)))
    panel = {"RB0": pd.DataFrame({
        "open": prices * (1 + rng.normal(0, 0.002, len(dates))),
        "high": prices * (1 + np.abs(rng.normal(0, 0.005, len(dates)))),
        "low": prices * (1 - np.abs(rng.normal(0, 0.005, len(dates)))),
        "close": prices,
        "volume": rng.integers(10000, 50000, len(dates)),
        "open_interest": rng.integers(50000, 100000, len(dates)),
    }, index=dates)}

    return panel, dates


@pytest.fixture
def multi_symbol_factor() -> FactorProgram:
    """多品种通用因子 — 动量反转因子。"""
    code = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 20))
    n = len(close)
    if n < window + 5:
        return np.zeros(n)
    momentum = (close - np.roll(close, window)) / np.maximum(np.roll(close, window), 1e-10)
    std = np.std(momentum[-window:]) + 1e-10
    signal = -momentum / std
    signal[:window] = 0
    return np.clip(signal, -1.0, 1.0)
"""
    return FactorProgram(
        factor_id="fct_multi_001",
        name="multi_symbol_momentum_reversal",
        code=code,
        params={"window": 20},
        signature={
            "inputs": ["close"],
            "outputs": ["signal"],
            "feature_dim": 1,
        },
        economic_logic={
            "theory": 4,
            "behavioral": 3,
            "microstructure": 4,
            "institutional": 3,
            "narrative": "多品种动量反转效应，跨品种套利逻辑",
        },
        source="seed",
        generation=0,
        created_at="2026-08-05T00:00:00",
        trace_id="test_multi_001",
    )


@pytest.fixture
def single_symbol_factor() -> FactorProgram:
    """单品种因子 — 仅适用于 RB0。"""
    code = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    window = int(params.get('window', 10))
    n = len(close)
    if n < window + 5:
        return np.zeros(n)
    ma = np.convolve(close, np.ones(window)/window, mode='same')
    signal = (close - ma) / np.maximum(ma, 1e-10)
    signal[:window] = 0
    return np.clip(signal, -1.0, 1.0)
"""
    return FactorProgram(
        factor_id="fct_single_001",
        name="single_symbol_trend",
        code=code,
        params={"window": 10},
        signature={
            "inputs": ["close"],
            "outputs": ["signal"],
            "feature_dim": 1,
        },
        economic_logic={
            "theory": 3,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "单品种趋势跟踪",
        },
        source="seed",
        generation=0,
        created_at="2026-08-05T00:00:00",
        trace_id="test_single_001",
    )


# ══════════════════════════════════════════════════════════
# 1. 多品种因子定义测试
# ══════════════════════════════════════════════════════════


class TestMultiSymbolFactor:
    """多品种因子定义与面板数据兼容性。"""

    def test_factor_code_executes_on_multi_symbol(
        self, multi_symbol_factor, multi_symbol_panel
    ):
        """因子代码在所有品种上都能正常执行。"""
        from fts.factor_engine.factor_program import FactorExecutor
        panel, _ = multi_symbol_panel
        executor = FactorExecutor(multi_symbol_factor)

        for sym, df in panel.items():
            sig = executor.execute(df, multi_symbol_factor.get("params", {}))
            assert len(sig) == len(df), f"{sym}: 信号长度不匹配"
            assert not np.all(np.isnan(sig)), f"{sym}: 信号全为 NaN"

    def test_multi_symbol_has_expected_symbols(self, multi_symbol_panel):
        """多品种面板包含预期的品种: RB0(螺纹钢), M0(豆粕), CU0(铜)。"""
        panel, _ = multi_symbol_panel
        assert "RB0" in panel, "缺少螺纹钢 RB0"
        assert "M0" in panel, "缺少豆粕 M0"
        assert "CU0" in panel, "缺少铜 CU0"
        assert len(panel) == 3

    def test_actual_futures_core_subset_contains_targets(self):
        """FUTURES_CORE_SUBSET 包含螺纹钢和豆粕。"""
        assert "RB0" in FUTURES_CORE_SUBSET  # 螺纹钢
        assert "M0" in FUTURES_CORE_SUBSET   # 豆粕

    def test_panel_data_consistency(self, multi_symbol_panel):
        """面板数据各品种日期对齐。"""
        panel, common_dates = multi_symbol_panel
        for sym, df in panel.items():
            assert len(df) == len(common_dates), f"{sym}: 日期长度不匹配"


# ══════════════════════════════════════════════════════════
# 2. 横截面回测兼容性测试
# ══════════════════════════════════════════════════════════


class TestCrossSectionBacktest:
    """横截面回测在多品种/单品种下的表现。"""

    def test_multi_symbol_backtest_runs(
        self, multi_symbol_factor, multi_symbol_panel
    ):
        """多品种因子在多品种面板上的回测能正常运行。"""
        panel, dates = multi_symbol_panel
        bt = cross_section_evaluate_backtest(
            multi_symbol_factor, panel, dates
        )
        assert isinstance(bt, dict)
        assert "ic" in bt
        assert "sharpe" in bt
        assert "icir" in bt

    def test_multi_symbol_generates_valid_ic(
        self, multi_symbol_factor, multi_symbol_panel
    ):
        """多品种因子能生成有效的 IC 值。"""
        panel, dates = multi_symbol_panel
        bt = cross_section_evaluate_backtest(
            multi_symbol_factor, panel, dates
        )
        assert bt["ic"] is not None
        assert np.isfinite(bt["ic"]), "IC 应为有限数值"

    def test_single_symbol_minimum_threshold(
        self, single_symbol_factor, single_symbol_panel
    ):
        """单品种因子（仅 1 个品种）不足以计算截面 IC。"""
        panel, dates = single_symbol_panel
        bt = cross_section_evaluate_backtest(
            single_symbol_factor, panel, dates
        )
        # 单品种只有 1 个标的，达不到 5 个标的的最低要求
        assert bt.get("ic", 0) == 0.0

    def test_multi_symbol_outperforms_single(
        self, multi_symbol_factor, multi_symbol_panel, single_symbol_panel
    ):
        """多品种因子在多品种面板上的夏普应高于单品种。"""
        panel_multi, dates_multi = multi_symbol_panel
        panel_single, dates_single = single_symbol_panel

        bt_multi = cross_section_evaluate_backtest(
            multi_symbol_factor, panel_multi, dates_multi
        )
        bt_single = cross_section_evaluate_backtest(
            multi_symbol_factor, panel_single, dates_single
        )

        # 多品种 Sharpe 应更高 (更多分散化)
        assert bt_multi.get("sharpe", 0) >= bt_single.get("sharpe", 0)


# ══════════════════════════════════════════════════════════
# 3. 跨品种覆盖率测试
# ══════════════════════════════════════════════════════════


class TestCrossSymbolCoverage:
    """跨品种覆盖率计算与兼容性评分。"""

    def test_coverage_calculation_multi(self, multi_symbol_panel):
        """多品种面板的跨品种覆盖率计算。"""
        panel, _ = multi_symbol_panel
        n_symbols = len(panel)
        coverage = min(n_symbols / 3, 1.0)
        assert coverage == 1.0

    def test_coverage_calculation_single(self, single_symbol_panel):
        """单品种面板的跨品种覆盖率计算。"""
        panel, _ = single_symbol_panel
        n_symbols = len(panel)
        coverage = n_symbols / 3
        assert coverage == pytest.approx(0.333, rel=0.01)

    def test_coverage_score_mapping(self):
        """覆盖率到质量分的映射正确。"""
        from fts.factor_engine.factor_quality_card import _map_coverage_to_score
        assert _map_coverage_to_score(1.0) == 5.0
        assert _map_coverage_to_score(0.5) == 3.0
        assert _map_coverage_to_score(0.3) == 2.0

    def test_coverage_reflected_in_quality_score(
        self, multi_symbol_factor, multi_symbol_panel
    ):
        """多品种因子的兼容性评分更高。"""
        panel, dates = multi_symbol_panel
        card = FactorQualityCard()

        bt = cross_section_evaluate_backtest(
            multi_symbol_factor, panel, dates
        )
        n_symbols = len(panel)
        coverage = min(n_symbols / 3, 1.0)

        # Calmar = 年化收益 / 最大回撤 (简化)
        sharpe = bt.get("sharpe", 0)
        max_dd = bt.get("max_drawdown", 1.0)
        calmar = sharpe / max_dd if max_dd > 0 else 0.0

        score = card.evaluate(
            factor_id=multi_symbol_factor["factor_id"],
            ic=bt.get("ic", 0),
            sharpe=sharpe,
            calmar=calmar,
            decay_rate=0.2,
            cross_symbol_coverage=coverage,
            data_frequency="daily",
            logic_score=3,
        )
        compat_dim = next(
            d for d in score["dimension_scores"] if d["name"] == "compatibility_score"
        )
        assert compat_dim["score"] >= 2.0, f"兼容性分过低: {compat_dim['score']}"


# ══════════════════════════════════════════════════════════
# 4. WalkForward 多品种稳定性测试
# ══════════════════════════════════════════════════════════


class TestWalkForwardMultiSymbol:
    """WalkForward 在多品种下的稳定性验证。"""

    def test_walk_forward_runs_on_multi_symbol(self, multi_symbol_panel):
        """WalkForward 能在多品种面板数据上运行。"""
        panel, _ = multi_symbol_panel
        df = panel["RB0"]

        # 使用较短窗口以适应 252 天的数据
        config: WalkForwardConfig = {
            "window_years": 0.5,       # 约 126 天
            "step_months": 2,          # 约 60 天
            "min_oos_months": 1,       # 约 30 天
            "n_windows": 2,            # 减少窗口数
            "min_ic_consistency": 0.5,
            "max_ic_volatility": 0.5,
        }

        optimizer = WalkForwardOptimizer(config=config)

        def evaluate_fn(train: pd.DataFrame, oos: pd.DataFrame) -> dict:
            """简化的评估函数 — 计算动量因子 IC。"""
            close_train = train["close"]
            close_oos = oos["close"]
            window = min(20, len(close_train) // 3)
            if window < 5:
                return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}

            # 计算信号
            signal_oos = close_oos.pct_change(window).fillna(0)
            # 计算 forward return
            fwd_ret = close_oos.pct_change(5).shift(-5).fillna(0)
            # 对齐长度
            min_len = min(len(signal_oos), len(fwd_ret))
            if min_len <= 1:
                return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}

            sig = signal_oos.values[:min_len]
            ret = fwd_ret.values[:min_len]
            # 移除零值
            mask = (sig != 0) & (ret != 0)
            if mask.sum() < 3:
                return {"ic": 0.0, "sharpe": 0.0, "turnover": 0.0}

            ic_val = np.corrcoef(sig[mask], ret[mask])[0, 1]
            sharpe_val = np.mean(ret[mask]) / (np.std(ret[mask]) + 1e-10) * np.sqrt(252)
            return {
                "ic": float(ic_val) if np.isfinite(ic_val) else 0.0,
                "sharpe": float(sharpe_val),
                "turnover": 0.5,
            }

        result = optimizer.evaluate(df, evaluate_fn)
        assert isinstance(result, dict)
        assert "windows" in result
        assert "ic_consistency" in result
        assert result["n_windows_completed"] >= 1

    def test_walk_forward_result_contract(self):
        """WalkForwardResult 数据契约完整。"""
        result: WalkForwardResult = {
            "windows": [
                WalkForwardWindowResult(
                    train_start="2022-01-01",
                    train_end="2022-06-30",
                    oos_start="2022-07-01",
                    oos_end="2022-09-30",
                    ic=0.05,
                    sharpe=1.2,
                    turnover=0.3,
                ),
                WalkForwardWindowResult(
                    train_start="2022-03-01",
                    train_end="2022-09-30",
                    oos_start="2022-10-01",
                    oos_end="2022-12-31",
                    ic=0.03,
                    sharpe=0.8,
                    turnover=0.4,
                ),
            ],
            "ic_consistency": 1.0,
            "ic_volatility": 0.15,
            "sharpe_volatility": 0.28,
            "consistency_score": 72.0,
            "passed": True,
            "n_windows_completed": 2,
        }
        assert result["ic_consistency"] == 1.0
        assert result["passed"] is True
        assert result["consistency_score"] > 50.0

    def test_walk_forward_feeds_stability_score(self):
        """WalkForward 结果能正确映射到稳定性评分。"""
        from fts.factor_engine.factor_quality_card import _map_stability_to_score

        good_result: WalkForwardResult = {
            "ic_consistency": 0.8,
            "ic_volatility": 0.15,
            "n_windows_completed": 4,
        }
        good_score = _map_stability_to_score(good_result)
        assert good_score >= 3.0, f"良好稳定性应 >= 3 分，实际 {good_score}"

        poor_result: WalkForwardResult = {
            "ic_consistency": 0.3,
            "ic_volatility": 0.6,
            "n_windows_completed": 2,
        }
        poor_score = _map_stability_to_score(poor_result)
        assert poor_score < 3.0, f"较差稳定性应 < 3 分，实际 {poor_score}"

    def test_walk_forward_default_config(self):
        """默认 WalkForward 配置合理。"""
        from fts.factor_engine.walk_forward import DEFAULT_WALK_FORWARD_CONFIG
        assert DEFAULT_WALK_FORWARD_CONFIG["window_years"] == 3
        assert DEFAULT_WALK_FORWARD_CONFIG["step_months"] == 6
        assert DEFAULT_WALK_FORWARD_CONFIG["n_windows"] == 4
        assert DEFAULT_WALK_FORWARD_CONFIG["min_ic_consistency"] == 0.5


# ══════════════════════════════════════════════════════════
# 5. 质量评分卡多品种集成测试
# ══════════════════════════════════════════════════════════


class TestQualityInspectionMultiSymbol:
    """质量评分卡在多品种场景下的集成。"""

    def test_multi_symbol_factor_passes_inspection(
        self, multi_symbol_factor, multi_symbol_panel
    ):
        """多品种因子通过质量评分卡检查。"""
        panel, dates = multi_symbol_panel

        bt = cross_section_evaluate_backtest(
            multi_symbol_factor, panel, dates
        )
        n_symbols = len(panel)
        coverage = min(n_symbols / 3, 1.0)

        wf_result: WalkForwardResult = {
            "ic_consistency": 0.75,
            "ic_volatility": 0.2,
            "n_windows_completed": 4,
            "consistency_score": 65.0,
        }

        card = FactorQualityCard()
        sharpe = bt.get("sharpe", 0)
        max_dd = bt.get("max_drawdown", 1.0)
        calmar = sharpe / max_dd if max_dd > 0 else 0.0

        score = card.evaluate(
            factor_id=multi_symbol_factor["factor_id"],
            ic=bt.get("ic", 0),
            sharpe=sharpe,
            calmar=calmar,
            decay_rate=0.2,
            cross_symbol_coverage=coverage,
            data_frequency="daily",
            logic_score=3,
            walk_forward_result=wf_result,
        )
        assert isinstance(score, dict)
        assert "total_score" in score
        assert "grade" in score
        assert score["total_score"] > 0
        assert score["grade"] in ("A", "B", "C")

    def test_inspection_pipeline_with_multi_symbol(
        self, multi_symbol_factor, multi_symbol_panel
    ):
        """质检流水线正确处理多品种因子。"""
        panel, dates = multi_symbol_panel

        bt = cross_section_evaluate_backtest(
            multi_symbol_factor, panel, dates
        )
        n_symbols = len(panel)
        coverage = min(n_symbols / 3, 1.0)

        ec = EconomicScore(
            theory=4, behavioral=3, microstructure=4, institutional=3,
            dimensions_passed=4, narrative="OK",
        )
        mt = MultipleTestResult(
            bonferroni_p=0.01, fdr_q=0.05, effective_n_factors=1,
            adjusted_t=3.5, passed=True,
        )

        evaluation = FactorEvaluation(
            factor_id=multi_symbol_factor["factor_id"],
            trace_id="test_trace",
            level_1_backtest=bt,
            level_2_economic=ec,
            level_3_multiple=mt,
            passed=bt.get("ic", 0) >= 0.03,
            failure_reasons=[],
            evaluated_at="2026-08-05T00:00:00",
        )

        inspector = FactorQualityInspection(min_grade="B")
        result = inspector.inspect(
            factor=multi_symbol_factor,
            evaluation=evaluation,
            cross_symbol_coverage=coverage,
        )
        assert isinstance(result, InspectionResult)
        assert result.factor_id == multi_symbol_factor["factor_id"]


# ══════════════════════════════════════════════════════════
# 6. 期货品种池验证
# ══════════════════════════════════════════════════════════


class TestFuturesSubsets:
    """期货品种池完整性验证。"""

    def test_core_subset_contains_key_symbols(self):
        """核心品种集包含螺纹钢、豆粕等主力品种。"""
        assert "RB0" in FUTURES_CORE_SUBSET   # 螺纹钢
        assert "M0" in FUTURES_CORE_SUBSET    # 豆粕
        assert "CU0" in FUTURES_CORE_SUBSET   # 铜
        assert "AU0" in FUTURES_CORE_SUBSET   # 黄金
        assert len(FUTURES_CORE_SUBSET) >= 20

    def test_core_subset_symbols_unique(self):
        """核心品种集无重复。"""
        assert len(set(FUTURES_CORE_SUBSET)) == len(FUTURES_CORE_SUBSET)

    def test_subset_symbols_have_valid_format(self):
        """品种代码格式正确 (以0结尾的连续合约)。"""
        for sym in FUTURES_CORE_SUBSET:
            assert sym.endswith("0"), f"{sym}: 应为连续合约格式 (如 RB0)"
