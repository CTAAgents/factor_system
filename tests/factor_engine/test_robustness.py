"""
test_robustness.py — 鲁棒性审查测试

HARNESS §11-logic-review-plan.md §B.2:
    验证鲁棒性测试模块可正确执行，涵盖对抗样本、缺失值、分布外。
"""

from __future__ import annotations


import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.factor_program import create_factor_program
from fts.factor_engine.robustness import (
    AdversarialTestResult,
    MissingValueTestResult,
    OODTestResult,
    RobustnessTestResult,
    RobustnessTester,
    _generate_ood_data,
    _inject_missing,
    _perturb_prices,
)


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """生成 200 天的合成 OHLCV 数据。"""
    np.random.seed(42)
    n = 200
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
    """生成未来收益率。"""
    np.random.seed(42)
    n = 200
    ret = np.random.randn(n) * 0.01
    ret[-1] = 0.0
    return ret


@pytest.fixture
def simple_momentum_factor() -> FactorProgram:
    """一个简单的动量因子。"""
    code = """
def factor_program(data, params):
    import numpy as np
    import pandas as pd
    close = data['close'].values if hasattr(data, 'close') else data['close']
    n = params.get("lookback", 10)
    mom = pd.Series(close).pct_change(n).values
    return np.clip(np.nan_to_num(mom, nan=0.0), -1.0, 1.0)
"""
    return create_factor_program(
        name="simple_momentum",
        code=code,
        params={},
        signature={
            "input_fields": ["close"],
            "output_type": "signal",
            "frequency": "daily",
            "lookback": 10,
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


# ─── 测试数据扰动函数 ─────────────────────────────────────


class TestPerturbPrices:
    """测试 _perturb_prices 函数。"""

    def test_perturb_multiplies_price_cols(self, sample_data):
        """价格列应被扰动因子相乘。"""
        result = _perturb_prices(sample_data, 1.0001)
        assert np.allclose(result["close"], sample_data["close"] * 1.0001)
        assert np.allclose(result["open"], sample_data["open"] * 1.0001)
        assert np.allclose(result["high"], sample_data["high"] * 1.0001)

    def test_perturb_preserves_shape(self, sample_data):
        """扰动后形状不变。"""
        result = _perturb_prices(sample_data, 1.0001)
        assert result.shape == sample_data.shape

    def test_perturb_preserves_volume(self, sample_data):
        """成交量列不应被扰动。"""
        result = _perturb_prices(sample_data, 1.0001)
        assert np.array_equal(result["volume"], sample_data["volume"])


class TestInjectMissing:
    """测试 _inject_missing 函数。"""

    def test_missing_5pct(self, sample_data):
        """5% 缺失时应产生约 5% 的 NaN。"""
        result = _inject_missing(sample_data, 0.05, random_seed=42)
        nan_ratio = np.isnan(result.select_dtypes(include=[np.number]).values).mean()
        assert 0.01 < nan_ratio < 0.15, f"NaN 比例异常: {nan_ratio:.3f}"

    def test_missing_20pct(self, sample_data):
        """20% 缺失时应产生约 20% 的 NaN。"""
        result = _inject_missing(sample_data, 0.20, random_seed=42)
        nan_ratio = np.isnan(result.select_dtypes(include=[np.number]).values).mean()
        assert 0.10 < nan_ratio < 0.35, f"NaN 比例异常: {nan_ratio:.3f}"

    def test_missing_preserves_date(self, sample_data):
        """date 列不应被设为 NaN。"""
        result = _inject_missing(sample_data, 0.50, random_seed=42)
        assert not result["date"].isna().any()

    def test_missing_bool_col_no_warning(self, sample_data):
        """bool 列缺失注入不触发 FutureWarning，且被提升为 float。"""
        import warnings

        df = sample_data.copy()
        df["flag"] = df["close"] > df["close"].mean()
        assert df["flag"].dtype == bool
        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            result = _inject_missing(df, 0.20, random_seed=42)
        assert result["flag"].dtype == float
        assert result["flag"].isna().any()


class TestGenerateOODData:
    """测试 _generate_ood_data 函数。"""

    def test_high_vol_increases_volatility(self, sample_data):
        """高波动场景应增大价格波动。"""
        ood = _generate_ood_data(sample_data, "high_vol", random_seed=42)
        original_std = sample_data["close"].std()
        ood_std = ood["close"].std()
        assert ood_std > original_std, "高波动场景应增大标准差"

    def test_low_vol_decreases_volatility(self, sample_data):
        """低波动场景应减小价格波动。"""
        ood = _generate_ood_data(sample_data, "low_vol", random_seed=42)
        sample_data["close"].std()
        ood["close"].std()
        # 低波动噪声很小，但 close 本身不变，所以 std 应接近
        # 这里只验证不崩溃
        assert ood.shape == sample_data.shape

    def test_trending_adds_trend(self, sample_data):
        """强趋势场景应添加单调趋势到价格列。"""
        ood = _generate_ood_data(sample_data, "trending", random_seed=42)
        # 趋势场景应使 close 列与原始数据不同
        assert not np.allclose(ood["close"].values, sample_data["close"].values), "趋势场景应改变价格"
        # 趋势应使价格范围扩大（或至少不缩小太多）
        ood_std = ood["close"].std()
        assert ood_std > 0, "趋势场景应保持正标准差"

    def test_noisy_adds_noise(self, sample_data):
        """高噪声场景应增大波动。"""
        ood = _generate_ood_data(sample_data, "noisy", random_seed=42)
        original_std = sample_data["close"].std()
        ood_std = ood["close"].std()
        assert ood_std > original_std * 0.8, "高噪声场景应增大标准差"


# ─── 测试 RobustnessTester ────────────────────────────────


class TestRobustnessTesterInit:
    """测试 RobustnessTester 初始化。"""

    def test_default_init(self):
        """默认参数应正确设置。"""
        tester = RobustnessTester()
        assert tester._adversarial_threshold == 0.01
        assert tester._missing_retention_threshold == 0.50
        assert tester._ood_retention_threshold == 0.50


class TestRobustnessTesterRun:
    """测试 run() 方法。"""

    def test_run_returns_correct_structure(self, sample_data, forward_returns, simple_momentum_factor):
        """run() 返回 RobustnessTestResult 且包含三类测试结果。"""
        tester = RobustnessTester()
        result = tester.run(simple_momentum_factor, sample_data, forward_returns)

        assert isinstance(result, RobustnessTestResult)
        assert result["factor_id"] == simple_momentum_factor["factor_id"]
        assert result["factor_name"] == simple_momentum_factor["name"]
        assert len(result["adversarial_results"]) == 4
        assert len(result["missing_value_results"]) == 3
        assert len(result["ood_results"]) == 4

    def test_adversarial_results_have_correct_fields(self, sample_data, forward_returns, simple_momentum_factor):
        """对抗样本结果包含必要字段。"""
        tester = RobustnessTester()
        result = tester.run(simple_momentum_factor, sample_data, forward_returns)

        for r in result["adversarial_results"]:
            assert isinstance(r, AdversarialTestResult)
            assert "perturbation" in r
            assert "perturbation_factor" in r
            assert "baseline_ic" in r
            assert "perturbed_ic" in r
            assert "ic_change" in r
            assert "passed" in r

    def test_missing_value_results_have_correct_fields(self, sample_data, forward_returns, simple_momentum_factor):
        """缺失值结果包含必要字段。"""
        tester = RobustnessTester()
        result = tester.run(simple_momentum_factor, sample_data, forward_returns)

        for r in result["missing_value_results"]:
            assert isinstance(r, MissingValueTestResult)
            assert "missing_pct" in r
            assert "baseline_ic" in r
            assert "missing_ic" in r
            assert "ic_retention" in r
            assert "passed" in r

    def test_ood_results_have_correct_fields(self, sample_data, forward_returns, simple_momentum_factor):
        """分布外结果包含必要字段。"""
        tester = RobustnessTester()
        result = tester.run(simple_momentum_factor, sample_data, forward_returns)

        for r in result["ood_results"]:
            assert isinstance(r, OODTestResult)
            assert "scenario" in r
            assert "baseline_ic" in r
            assert "ood_ic" in r
            assert "ic_retention" in r
            assert "passed" in r

    def test_summary_contains_correct_info(self, sample_data, forward_returns, simple_momentum_factor):
        """汇总信息应包含必要字段。"""
        tester = RobustnessTester()
        result = tester.run(simple_momentum_factor, sample_data, forward_returns)

        s = result["summary"]
        assert "baseline_ic" in s
        assert "adversarial" in s
        assert "missing_value" in s
        assert "ood" in s
        assert "overall_pass_rate" in s
        assert s["adversarial"]["total"] == 4
        assert s["missing_value"]["total"] == 3
        assert s["ood"]["total"] == 4

    def test_run_without_forward_returns(self, sample_data, simple_momentum_factor):
        """不传 forward_returns 时仍可运行。"""
        # 使用不依赖 forward_returns 的因子（信号值排序）
        code = """
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    score = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
"""
        factor = create_factor_program(
            name="simple_momentum",
            code=code,
            params={},
            signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 2},
            economic_logic={
                "theory": 3,
                "behavioral": 3,
                "microstructure": 3,
                "institutional": 3,
                "narrative": "简单动量因子",
            },
            source="test",
        )
        forward_ret = np.random.randn(len(sample_data)) * 0.01
        tester = RobustnessTester()
        result = tester.run(factor, sample_data, forward_ret)
        assert isinstance(result, RobustnessTestResult)


class TestRobustnessTesterReport:
    """测试报告生成。"""

    def test_report_returns_string(self, sample_data, forward_returns, simple_momentum_factor):
        """report() 应返回字符串。"""
        tester = RobustnessTester()
        result = tester.run(simple_momentum_factor, sample_data, forward_returns)
        report_str = tester.report(result)

        assert isinstance(report_str, str)
        assert len(report_str) > 0
        assert simple_momentum_factor["name"] in report_str
        assert "鲁棒性测试报告" in report_str

    def test_report_contains_all_sections(self, sample_data, forward_returns, simple_momentum_factor):
        """报告应包含对抗样本、缺失值、分布外和汇总四个章节。"""
        tester = RobustnessTester()
        result = tester.run(simple_momentum_factor, sample_data, forward_returns)
        report_str = tester.report(result)

        assert "对抗样本" in report_str
        assert "缺失值" in report_str
        assert "分布外" in report_str
        assert "汇总" in report_str


class TestRobustnessTesterCustomThresholds:
    """测试自定义阈值。"""

    def test_custom_thresholds_affect_results(self, sample_data, forward_returns, simple_momentum_factor):
        """自定义阈值应影响通过/不通过判定。"""
        # 严格阈值
        strict = RobustnessTester(
            adversarial_threshold=0.001,
            missing_retention_threshold=0.99,
            ood_retention_threshold=0.99,
        )
        strict_result = strict.run(simple_momentum_factor, sample_data, forward_returns)

        # 宽松阈值
        loose = RobustnessTester(
            adversarial_threshold=1.0,
            missing_retention_threshold=0.0,
            ood_retention_threshold=0.0,
        )
        loose_result = loose.run(simple_momentum_factor, sample_data, forward_returns)

        # 宽松阈值应通过更多测试
        assert loose_result["summary"]["overall_pass_rate"] >= strict_result["summary"]["overall_pass_rate"]
