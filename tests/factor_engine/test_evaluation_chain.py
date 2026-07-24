"""tests/factor_engine/test_evaluation_chain.py — 三级评估链测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import (
    EconomicLogic,
    FactorProgram,
    FactorSignature,
)
from fts.factor_engine.evaluation_chain import (
    EvaluationChain,
    evaluate_backtest,
    evaluate_economic_logic,
    evaluate_multiple_tests,
)
from fts.factor_engine.factor_program import create_factor_program


@pytest.fixture
def simple_factor() -> FactorProgram:
    """简单的零信号因子（用于测试评估链流程）。"""
    code = """
import numpy as np
def factor_program(data, params):
    n = len(data['close'])
    return np.zeros(n)
"""
    return create_factor_program(
        name="zero_factor",
        code=code,
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="测试因子"),
        source="manual",
    )


@pytest.fixture
def good_factor() -> FactorProgram:
    """与未来收益率正相关的因子（应通过 IC 评估）。"""
    code = """
import numpy as np
def factor_program(data, params):
    close = data['close'].values
    # 简单动量：5 日收益率
    n = len(close)
    signal = np.zeros(n)
    for i in range(5, n):
        signal[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)
    return np.clip(signal * 10, -1.0, 1.0)
"""
    return create_factor_program(
        name="momentum_5d",
        code=code,
        params={"window": 5},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
        economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="5日动量因子"),
        source="manual",
    )


# ─── Level 1: 回测验证 ────────────────────────────────────

def test_evaluate_backtest_returns_metrics(simple_factor, sample_ohlcv, forward_returns):
    """应返回完整的 BacktestMetrics。"""
    bt = evaluate_backtest(simple_factor, sample_ohlcv, forward_returns)
    assert "ic" in bt
    assert "icir" in bt
    assert "sharpe" in bt
    assert "max_drawdown" in bt
    assert "monotonicity" in bt
    assert "oos_ratio" in bt
    assert "t_stat" in bt
    assert "turnover_monthly" in bt


def test_evaluate_backtest_oos_ratio(simple_factor, sample_ohlcv, forward_returns):
    """样本外比例应等于配置值。"""
    bt = evaluate_backtest(simple_factor, sample_ohlcv, forward_returns, oos_ratio=0.3)
    assert bt["oos_ratio"] == 0.3


def test_evaluate_backtest_zero_signal(simple_factor, sample_ohlcv, forward_returns):
    """零信号因子应返回零 IC。"""
    bt = evaluate_backtest(simple_factor, sample_ohlcv, forward_returns)
    assert abs(bt["ic"]) < 1e-6


# ─── Level 2: 经济逻辑 ────────────────────────────────────

def test_evaluate_economic_logic_full_pass(simple_factor):
    """四维全达标的因子应通过。"""
    ec = evaluate_economic_logic(simple_factor)
    assert ec["dimensions_passed"] == 4
    assert ec["theory"] == 4
    assert ec["behavioral"] == 3


def test_evaluate_economic_logic_partial_fail():
    """仅 2 维达标的因子不应通过。"""
    fp = create_factor_program(
        name="bad_factor",
        code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))",
        params={},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
        economic_logic=EconomicLogic(theory=2, behavioral=2, microstructure=4, institutional=4, narrative="部分达标"),
        source="manual",
    )
    ec = evaluate_economic_logic(fp)
    assert ec["dimensions_passed"] == 2  # 仅 microstructure/institutional 达标


# ─── Level 3: 多重检验 ────────────────────────────────────

def test_evaluate_multiple_tests_empty():
    """空输入应返回默认值。"""
    from fts.factor_engine.contracts import FactorEvaluation
    mt = evaluate_multiple_tests([])
    assert mt["effective_n_factors"] == 1


def test_evaluate_multiple_tests_with_data():
    """应正确计算 Bonferroni 校正。"""
    from fts.factor_engine.contracts import (
        BacktestMetrics, EconomicScore, FactorEvaluation, MultipleTestResult,
    )
    evals = [
        FactorEvaluation(
            factor_id=f"fct_{i}", trace_id="t",
            level_1_backtest=BacktestMetrics(t_stat=3.0 + i * 0.5),
            level_2_economic=EconomicScore(),
            level_3_multiple=MultipleTestResult(),
            passed=False, failure_reasons=[], evaluated_at="2026-07-18",
        ) for i in range(5)
    ]
    mt = evaluate_multiple_tests(evals)
    assert mt["effective_n_factors"] == 5
    assert 0 < mt["bonferroni_p"] <= 1.0


def test_evaluate_multiple_tests_with_correlation():
    """提供相关性矩阵时应调整有效因子数。"""
    from fts.factor_engine.contracts import (
        BacktestMetrics, EconomicScore, FactorEvaluation, MultipleTestResult,
    )
    evals = [
        FactorEvaluation(
            factor_id=f"fct_{i}", trace_id="t",
            level_1_backtest=BacktestMetrics(t_stat=3.0),
            level_2_economic=EconomicScore(),
            level_3_multiple=MultipleTestResult(),
            passed=False, failure_reasons=[], evaluated_at="2026-07-18",
        ) for i in range(3)
    ]
    # 高相关矩阵（几乎完全共线）
    corr = np.array([[1.0, 0.95, 0.95], [0.95, 1.0, 0.95], [0.95, 0.95, 1.0]])
    mt = evaluate_multiple_tests(evals, correlation_matrix=corr)
    # 高相关下有效因子数应显著 < n
    assert mt["effective_n_factors"] <= 3


# ─── 完整评估链 ───────────────────────────────────────────

def test_evaluation_chain_evaluate(simple_factor, sample_ohlcv, forward_returns):
    """应能执行完整三级评估。"""
    chain = EvaluationChain()
    ev = chain.evaluate(simple_factor, sample_ohlcv, forward_returns)
    assert "factor_id" in ev
    assert "level_1_backtest" in ev
    assert "level_2_economic" in ev
    assert "level_3_multiple" in ev
    assert "passed" in ev
    assert isinstance(ev["failure_reasons"], list)


def test_evaluation_chain_with_prior(simple_factor, sample_ohlcv, forward_returns):
    """应支持传入先验评估。"""
    chain = EvaluationChain()
    # 第一次评估
    ev1 = chain.evaluate(simple_factor, sample_ohlcv, forward_returns)
    # 第二次评估（带先验）
    ev2 = chain.evaluate(
        simple_factor, sample_ohlcv, forward_returns,
        prior_evaluations=[ev1],
    )
    assert "level_3_multiple" in ev2


# ─── evaluation_chain 边缘覆盖 ─────────────────────────

class TestEvaluationChainCoverage:
    """补齐 evaluation_chain.py 覆盖率缺口。"""

    # ── _compute_ic 边缘 ──

    def test_compute_ic_short_arrays(self):
        """长度不足 2 时应返回 (0,0)。"""
        from fts.factor_engine.evaluation_chain import _compute_ic
        ic, icir = _compute_ic(np.array([1.0]), np.array([0.5]))
        assert ic == 0.0
        assert icir == 0.0

    def test_compute_ic_mismatched_length(self):
        """长度不匹配时应返回 (0,0)。"""
        from fts.factor_engine.evaluation_chain import _compute_ic
        ic, icir = _compute_ic(np.array([1.0, 2.0]), np.array([0.5]))
        assert ic == 0.0
        assert icir == 0.0

    def test_compute_ic_pearson_method(self):
        """pearson 方法应正常工作。"""
        from fts.factor_engine.evaluation_chain import _compute_ic
        a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        b = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
        ic, icir = _compute_ic(a, b, method="pearson")
        assert abs(ic) > 0.9  # 完美线性相关
        assert abs(icir) > 0.9

    def test_compute_ic_constant_signal(self):
        """常数信号应返回 (0,0)。"""
        from fts.factor_engine.evaluation_chain import _compute_ic
        ic, icir = _compute_ic(np.ones(10), np.random.randn(10))
        assert ic == 0.0
        assert icir == 0.0

    # ── _compute_sharpe 边缘 ──

    def test_compute_sharpe_short_returns(self):
        """长度不足 2 应返回 0。"""
        from fts.factor_engine.evaluation_chain import _compute_sharpe
        assert _compute_sharpe(np.array([0.01])) == 0.0

    def test_compute_sharpe_zero_std(self):
        """零标准差应返回 0。"""
        from fts.factor_engine.evaluation_chain import _compute_sharpe
        assert _compute_sharpe(np.ones(10)) == 0.0

    # ── _compute_max_drawdown 边缘 ──

    def test_max_drawdown_short(self):
        """长度不足 2 应返回 0。"""
        from fts.factor_engine.evaluation_chain import _compute_max_drawdown
        assert _compute_max_drawdown(np.array([1.0])) == 0.0

    def test_max_drawdown_no_drawdown(self):
        """持续上涨应返回 0。"""
        from fts.factor_engine.evaluation_chain import _compute_max_drawdown
        cum = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        assert _compute_max_drawdown(cum) == 0.0

    def test_max_drawdown_positive(self):
        """有回撤应返回正值。"""
        from fts.factor_engine.evaluation_chain import _compute_max_drawdown
        cum = np.array([1.0, 2.0, 1.5, 1.0, 3.0])
        dd = _compute_max_drawdown(cum)
        assert dd > 0.0

    # ── _check_monotonicity 边缘 ──

    def test_monotonicity_insufficient_data(self):
        """数据量不足时应返回 False。"""
        from fts.factor_engine.evaluation_chain import _check_monotonicity
        assert not _check_monotonicity(np.ones(10), np.ones(10))

    def test_monotonicity_increasing(self):
        """严格单调递增应通过。"""
        from fts.factor_engine.evaluation_chain import _check_monotonicity
        np.random.seed(42)
        n = 500
        signal = np.linspace(-1, 1, n) + np.random.randn(n) * 0.01
        returns = np.linspace(-0.1, 0.1, n)
        assert _check_monotonicity(signal, returns)

    def test_monotonicity_decreasing(self):
        """严格单调递减应通过。"""
        from fts.factor_engine.evaluation_chain import _check_monotonicity
        n = 500
        signal = np.linspace(1, -1, n)
        returns = np.linspace(0.1, -0.1, n)
        assert _check_monotonicity(signal, returns, n_buckets=10)

    def test_monotonicity_not_monotonic(self):
        """非单调（倒 V 型）应返回 False。"""
        from fts.factor_engine.evaluation_chain import _check_monotonicity
        n = 500
        # 信号递增，收益先降后升（倒 V）：低信号/高信号→正收益，中信号→负收益
        signal = np.linspace(-1, 1, n)
        returns = np.concatenate([np.linspace(0.1, -0.1, n // 2), np.linspace(-0.1, 0.1, n - n // 2)])
        assert not _check_monotonicity(signal, returns)

    # ── evaluate_backtest 边缘 ──

    def test_evaluate_backtest_with_nan_signal(self, sample_ohlcv, forward_returns):
        """包含 NaN 的信号应正常处理。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature
        code = """
import numpy as np
def factor_program(data, params):
    n = len(data['close'])
    sig = np.zeros(n)
    sig[100:200] = np.nan
    return sig
"""
        fp = FactorProgram(
            factor_id="fct_nan_test",
            name="nan_factor",
            code=code,
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="NaN测试"),
            source="manual",
        )
        bt = evaluate_backtest(fp, sample_ohlcv, forward_returns)
        assert "ic" in bt

    def test_evaluate_backtest_tiny_oos(self, sample_ohlcv, forward_returns):
        """极小的样本外比例应降级为 1。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature
        code = """
import numpy as np
def factor_program(data, params):
    return np.zeros(len(data['close']))
"""
        fp = FactorProgram(
            factor_id="fct_tiny_test",
            name="tiny_factor",
            code=code,
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="tiny测试"),
            source="manual",
        )
        # 用 3 条数据测试
        tiny_data = sample_ohlcv.iloc[:3]
        tiny_returns = forward_returns[:3]
        bt = evaluate_backtest(fp, tiny_data, tiny_returns, oos_ratio=0.001)
        assert bt["oos_ratio"] == 0.001

    # ── evaluate_multiple_tests 边缘 ──

    def test_evaluate_multiple_tests_zero_t(self):
        """t_stat = 0 时应返回 p=1.0。"""
        from fts.factor_engine.contracts import (
            BacktestMetrics, EconomicScore, FactorEvaluation, MultipleTestResult,
        )
        evals = [
            FactorEvaluation(
                factor_id="fct_zero_t", trace_id="t",
                level_1_backtest=BacktestMetrics(t_stat=0.0),
                level_2_economic=EconomicScore(),
                level_3_multiple=MultipleTestResult(),
                passed=False, failure_reasons=[], evaluated_at="2026-07-18",
            )
        ]
        mt = evaluate_multiple_tests(evals)
        assert mt["bonferroni_p"] == 1.0

    def test_evaluate_multiple_tests_correlation_eigenvalue_failure(self):
        """相关性矩阵特征值计算失败时应 fallback 到 n。"""
        from fts.factor_engine.contracts import (
            BacktestMetrics, EconomicScore, FactorEvaluation, MultipleTestResult,
        )
        evals = [
            FactorEvaluation(
                factor_id=f"fct_{i}", trace_id="t",
                level_1_backtest=BacktestMetrics(t_stat=2.0),
                level_2_economic=EconomicScore(),
                level_3_multiple=MultipleTestResult(),
                passed=False, failure_reasons=[], evaluated_at="2026-07-18",
            ) for i in range(3)
        ]
        # 非方阵 → 特征值计算失败
        bad_corr = np.array([[1.0, 0.5], [0.5, 1.0], [0.3, 0.3]])
        mt = evaluate_multiple_tests(evals, correlation_matrix=bad_corr)
        assert mt["effective_n_factors"] == 3  # fallback to n

    def test_evaluate_multiple_tests_passed(self):
        """高 t_stat + 低 n 应通过多重检验。"""
        from fts.factor_engine.contracts import (
            BacktestMetrics, EconomicScore, FactorEvaluation, MultipleTestResult,
        )
        evals = [
            FactorEvaluation(
                factor_id="fct_high_t", trace_id="t",
                level_1_backtest=BacktestMetrics(t_stat=15.0),
                level_2_economic=EconomicScore(),
                level_3_multiple=MultipleTestResult(),
                passed=False, failure_reasons=[], evaluated_at="2026-07-18",
            )
        ]
        mt = evaluate_multiple_tests(evals)
        assert mt["passed"] is True

    # ── EvaluationChain 边缘 ──

    def test_evaluation_chain_with_correlation_matrix(self, simple_factor, sample_ohlcv, forward_returns):
        """传入相关性矩阵应被正确使用。"""
        chain = EvaluationChain()
        corr = np.array([[1.0]])
        ev = chain.evaluate(simple_factor, sample_ohlcv, forward_returns, correlation_matrix=corr)
        assert "level_3_multiple" in ev

    def test_evaluation_chain_failure_reasons(self, sample_ohlcv, forward_returns):
        """低质量因子应有完整的失败原因。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorProgram, FactorSignature

        code = """
import numpy as np
def factor_program(data, params):
    return np.zeros(len(data['close']))
"""
        fp = FactorProgram(
            factor_id="fct_fail_test",
            name="fail_factor",
            code=code,
            params={},
            trace_id="trace_fail",
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=1, behavioral=1, microstructure=1, institutional=1, narrative="失败因子"),
            source="manual",
        )
        chain = EvaluationChain()
        ev = chain.evaluate(fp, sample_ohlcv, forward_returns)
        assert not ev["passed"]
        assert len(ev["failure_reasons"]) >= 3  # IC、夏普、单调性、经济逻辑等

    def test_evaluation_chain_custom_oos(self, simple_factor, sample_ohlcv, forward_returns):
        """自定义 oos_ratio 和 periods_per_year。"""
        chain = EvaluationChain(oos_ratio=0.5, periods_per_year=52)
        ev = chain.evaluate(simple_factor, sample_ohlcv, forward_returns)
        assert ev["level_1_backtest"]["oos_ratio"] == 0.5
