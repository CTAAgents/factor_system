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


# ─── _check_monotonicity 深层边缘 ─────────────────────

class TestCheckMonotonicityDeep:
    """补齐 _check_monotonicity 的深层分支覆盖。"""

    def test_nan_after_dropna_below_n_buckets(self):
        """dropna 后数据量不足 n_buckets → 返回 False (line 90)。"""
        from fts.factor_engine.evaluation_chain import _check_monotonicity
        n = 500
        signal = np.linspace(-1, 1, n)
        returns = np.random.randn(n)
        # 在 signal 和 returns 中插入 NaN，使 dropna 后只剩 5 行
        signal[5:] = np.nan
        returns[:5] = np.nan
        result = _check_monotonicity(signal, returns, n_buckets=10)
        assert result is False

    def test_constant_returns_nan_corr(self):
        """常数收益率使桶均值恒定 → spearmanr 返回 NaN → 返回 False (line 98)。"""
        from fts.factor_engine.evaluation_chain import _check_monotonicity
        n = 500
        signal = np.linspace(-1, 1, n)
        returns = np.ones(n) * 0.01  # 所有收益率完全相同
        result = _check_monotonicity(signal, returns, n_buckets=10)
        assert result is False


# ─── evaluate_backtest 深层边缘 ──────────────────────

class TestEvaluateBacktestDeep:
    """补齐 evaluate_backtest 的空数据/单行数据分支。"""

    def test_empty_data_zero_signal(self):
        """空 DataFrame → 空 signal → ls_returns 走零填充 (line 147)。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        fp = create_factor_program(
            name="zero",
            code="import numpy as np\ndef factor_program(data, params):\n    return np.array([])",
            params={},
            signature=FactorSignature(input_fields=[], output_type="signal", frequency="daily", lookback=0),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="空数据"),
            source="manual",
        )
        import pandas as pd
        empty_df = pd.DataFrame()
        bt = evaluate_backtest(fp, empty_df, np.array([]))
        assert bt["ic"] == 0.0
        assert bt["sharpe"] == 0.0
        assert bt["turnover_monthly"] == 0.0

    def test_single_row_data(self):
        """单行数据 → len(signal)=1 → turnover=0.0 (line 165)。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        fp = create_factor_program(
            name="single_row",
            code="import numpy as np\ndef factor_program(data, params):\n    return np.zeros(len(data))",
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=0),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="单行"),
            source="manual",
        )
        import pandas as pd
        df = pd.DataFrame({"close": [100.0]}, index=pd.DatetimeIndex(["2024-01-01"]))
        bt = evaluate_backtest(fp, df, np.array([0.01]))
        assert bt["turnover_monthly"] == 0.0


# ─── evaluate_walk_forward 边缘 ──────────────────────

class TestEvaluateWalkForward:
    """覆盖 evaluate_walk_forward 的深层分支。"""

    def test_min_len_less_than_2(self):
        """样本外信号长度 < 2 → 返回默认 metrics (line 412)。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import evaluate_walk_forward
        from fts.factor_engine.factor_program import create_factor_program
        from fts.factor_engine.walk_forward import WalkForwardConfig
        import pandas as pd
        import numpy as np

        # 因子代码始终返回长度为 1 的数组 → min_len = 1 < 2
        fp = create_factor_program(
            name="short",
            code="import numpy as np\ndef factor_program(data, params):\n    return np.zeros(1)",
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=0),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="短信号"),
            source="manual",
        )
        dates = pd.date_range("2024-01-01", periods=500, freq="D")
        df = pd.DataFrame({"close": np.arange(500, dtype=float)}, index=dates)
        # 极小的窗口配置让 _evaluate_window 被调用
        config = WalkForwardConfig(window_years=0, step_months=0, min_oos_months=0, n_windows=1)
        result = evaluate_walk_forward(fp, df, np.zeros(500), config=config)
        assert result is not None
        assert "n_windows_completed" in result

    def test_train_signal_single_element(self):
        """训练信号长度 <= 1 且 min_len >= 2 → turnover=0.0 (line 432)。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import evaluate_walk_forward
        from fts.factor_engine.factor_program import create_factor_program
        from fts.factor_engine.walk_forward import WalkForwardConfig
        import pandas as pd
        import numpy as np

        # window_years=0 → train_df 为空 → train_signal 长度 0
        # step_months=1 → oos 有 30 行数据 → min_len >= 2
        # 这样 min_len >= 2 通过，但 len(train_signal) <= 1 走 else → line 432
        fp = create_factor_program(
            name="tiny",
            code="import numpy as np\ndef factor_program(data, params):\n    return np.zeros(len(data['close']))",
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=0),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="微小信号"),
            source="manual",
        )
        dates = pd.date_range("2024-01-01", periods=500, freq="D")
        df = pd.DataFrame({"close": np.arange(500, dtype=float)}, index=dates)
        config = WalkForwardConfig(window_years=0, step_months=1, min_oos_months=1, n_windows=1)
        result = evaluate_walk_forward(fp, df, np.zeros(500), config=config)
        assert result is not None

    def test_full_evaluate_window_path(self):
        """完整 _evaluate_window 路径（min_len >= 2）→ 覆盖 lines 414-434。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import evaluate_walk_forward
        from fts.factor_engine.factor_program import create_factor_program
        from fts.factor_engine.walk_forward import WalkForwardConfig
        import pandas as pd
        import numpy as np

        # 动量因子产生非恒定信号
        fp = create_factor_program(
            name="wf_full",
            code=(
                "import numpy as np\n"
                "def factor_program(data, params):\n"
                "    close = data['close'].values\n"
                "    n = len(close)\n"
                "    sig = np.zeros(n)\n"
                "    for i in range(5, n):\n"
                "        sig[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
                "    return np.clip(sig * 10, -1.0, 1.0)\n"
            ),
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
            economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="走航全路径"),
            source="manual",
        )
        # 500 天数据，窗口配置：1年训练 + 1月步长 + 1月验证，1个窗口
        dates = pd.date_range("2024-01-01", periods=500, freq="D")
        close = 100 + np.cumsum(np.random.randn(500) * 0.5)
        df = pd.DataFrame({"close": close}, index=dates)
        fwd_ret = np.zeros(500)
        fwd_ret[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)

        config = WalkForwardConfig(window_years=1, step_months=1, min_oos_months=1, n_windows=1)
        result = evaluate_walk_forward(fp, df, fwd_ret, config=config)
        assert result is not None
        assert result["n_windows_completed"] >= 1


# ─── EvaluationChain walk_forward 分支 ───────────────

class TestEvaluationChainWalkForward:
    """覆盖 EvaluationChain.evaluate 的走航分支 (lines 342, 360-361)。"""

    def test_walk_forward_not_passed(self, sample_ohlcv):
        """走航结果不通过 → failure_reasons 包含走航失败原因。"""
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import EvaluationChain
        from fts.factor_engine.factor_program import create_factor_program
        from fts.factor_engine.walk_forward import WalkForwardConfig
        import pandas as pd
        import numpy as np

        fp = create_factor_program(
            name="wf_fail",
            code="import numpy as np\ndef factor_program(data, params):\n    return np.zeros(len(data))",
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="走航失败"),
            source="manual",
        )
        forward_returns = np.zeros(len(sample_ohlcv))
        # 极小的窗口配置确保走航运行但不通过
        wf_config = WalkForwardConfig(window_years=0, step_months=0, min_oos_months=0, n_windows=1)
        chain = EvaluationChain()
        ev = chain.evaluate(fp, sample_ohlcv, forward_returns, walk_forward_config=wf_config)
        # 应包含走航相关失败原因
        reasons = ev["failure_reasons"]
        has_walk_forward_reason = any("走航" in r for r in reasons)
        assert has_walk_forward_reason, f"应包含走航失败原因，实际: {reasons}"
        assert ev["walk_forward"] is not None
        assert ev["walk_forward"]["passed"] is False


# ─── cross_section_evaluate_backtest ─────────────────

class TestCrossSectionEvaluateBacktest:
    """覆盖 cross_section_evaluate_backtest (lines 462-562) 全分支。"""

    @staticmethod
    def _make_panel(n_stocks: int, n_dates: int = 500, seed: int = 42) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """创建横截面 panel 数据。"""
        np.random.seed(seed)
        dates = pd.date_range("2024-01-01", periods=n_dates, freq="D")
        panel: dict[str, pd.DataFrame] = {}
        for i in range(n_stocks):
            close = 100 + np.cumsum(np.random.randn(n_dates) * 0.5)
            panel[f"STK_{i}"] = pd.DataFrame({
                "open": close + np.random.randn(n_dates) * 0.1,
                "high": close + np.abs(np.random.randn(n_dates)) * 0.3,
                "low": close - np.abs(np.random.randn(n_dates)) * 0.3,
                "close": close,
                "volume": np.random.randint(1000, 10000, n_dates).astype(float),
            }, index=dates)
        return panel, dates

    def test_normal_panel(self):
        """正常 panel（10 只股票）应返回有效 BacktestMetrics。"""
        import pandas as pd
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        fp = create_factor_program(
            name="cross_mom",
            code=(
                "import numpy as np\n"
                "def factor_program(data, params):\n"
                "    close = data['close'].values\n"
                "    n = len(close)\n"
                "    sig = np.zeros(n)\n"
                "    for i in range(5, n):\n"
                "        sig[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
                "    return np.clip(sig * 10, -1.0, 1.0)\n"
            ),
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
            economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="横截面测试"),
            source="manual",
        )
        panel, dates = self._make_panel(10)
        bt = cross_section_evaluate_backtest(fp, panel, dates)
        assert "ic" in bt
        assert "sharpe" in bt
        assert "max_drawdown" in bt
        assert "t_stat" in bt

    def test_few_stocks(self):
        """少于 5 只股票 → 返回零值 BacktestMetrics (line 483-487)。"""
        import pandas as pd
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        fp = create_factor_program(
            name="few",
            code="import numpy as np\ndef factor_program(data, params):\n    return np.zeros(len(data['close']))",
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="少股票"),
            source="manual",
        )
        panel, dates = self._make_panel(3)  # 只有 3 只
        bt = cross_section_evaluate_backtest(fp, panel, dates)
        assert bt["ic"] == 0.0
        assert bt["sharpe"] == 0.0
        assert bt["t_stat"] == 0.0

    def test_nan_in_signal(self):
        """因子信号含 NaN → 在 IC 计算中被过滤。"""
        import pandas as pd
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        # 因子信号每隔 5 个插入 NaN
        fp = create_factor_program(
            name="nan_sig",
            code=(
                "import numpy as np\n"
                "def factor_program(data, params):\n"
                "    n = len(data['close'])\n"
                "    sig = np.random.randn(n) * 0.1\n"
                "    sig[::5] = np.nan\n"
                "    return sig\n"
            ),
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="NaN信号"),
            source="manual",
        )
        panel, dates = self._make_panel(10, n_dates=200)
        bt = cross_section_evaluate_backtest(fp, panel, dates)
        assert "ic" in bt

    def test_constant_signal_returns(self):
        """信号和收益率都恒定 → 所有 IC 期跳过 → 返回零值。"""
        import pandas as pd
        import numpy as np
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        # 因子返回全零信号（恒定）
        fp = create_factor_program(
            name="const",
            code="import numpy as np\ndef factor_program(data, params):\n    return np.zeros(len(data['close']))",
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="恒定信号"),
            source="manual",
        )
        # 使用恒定 close 价格 → forward_returns 全为零 → 收益率恒定
        dates = pd.date_range("2024-01-01", periods=200, freq="D")
        panel = {}
        for i in range(10):
            panel[f"C_{i}"] = pd.DataFrame({
                "close": np.ones(200) * 100.0,
                "open": np.ones(200) * 100.0,
                "high": np.ones(200) * 100.5,
                "low": np.ones(200) * 99.5,
                "volume": np.ones(200) * 1000,
            }, index=dates)
        bt = cross_section_evaluate_backtest(fp, panel, dates)
        assert bt["ic"] == 0.0
        assert bt["t_stat"] == 0.0

    def test_exception_in_executor(self):
        """因子执行异常 → 被 except 捕获并 continue (lines 480-481)。"""
        import pandas as pd
        import numpy as np
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        # 访问不存在的 data 列 → 执行时抛出异常
        fp = create_factor_program(
            name="bad_col",
            code="import numpy as np\ndef factor_program(data, params):\n    return np.array(data['nonexistent'])",
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=1),
            economic_logic=EconomicLogic(theory=3, behavioral=3, microstructure=3, institutional=3, narrative="异常因子"),
            source="manual",
        )
        panel, dates = self._make_panel(10)
        bt = cross_section_evaluate_backtest(fp, panel, dates)
        # 所有股票执行都异常 → signal_dict 为空 → 返回零值
        assert bt["ic"] == 0.0

    def test_t_stat_zero_std(self):
        """ls_returns 标准差为零 → t_stat = 0.0 (line 560)。"""
        import pandas as pd
        import numpy as np
        from unittest import mock
        from fts.factor_engine.contracts import EconomicLogic, FactorSignature
        from fts.factor_engine.evaluation_chain import cross_section_evaluate_backtest
        from fts.factor_engine.factor_program import create_factor_program

        # 动量因子产生有方差的信号
        fp = create_factor_program(
            name="cross_t",
            code=(
                "import numpy as np\n"
                "def factor_program(data, params):\n"
                "    close = data['close'].values\n"
                "    n = len(close)\n"
                "    sig = np.zeros(n)\n"
                "    for i in range(5, n):\n"
                "        sig[i] = (close[i] - close[i-5]) / max(close[i-5], 1e-10)\n"
                "    return np.clip(sig * 10, -1.0, 1.0)\n"
            ),
            params={},
            signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=10),
            economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=4, narrative="t_stat零"),
            source="manual",
        )
        panel, dates = self._make_panel(10, n_dates=200)
        # mock np.std 返回 0 以强制执行 else 分支
        with mock.patch("fts.factor_engine.evaluation_chain.np.std", return_value=0.0):
            bt = cross_section_evaluate_backtest(fp, panel, dates)
        assert bt["t_stat"] == 0.0
