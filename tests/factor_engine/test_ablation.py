"""
test_ablation.py — 输入敏感性消融实验测试

HARNESS §11-logic-review-plan.md §A.1:
    验证 5 种消融模式可正确执行，输出符合预期。
"""

from __future__ import annotations


import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.ablation import (
    ABLATION_MODES,
    AblationExperiment,
    AblationResult,
    SingleAblation,
    _ablate_shuffle_dates,
    _ablate_volume_zero,
    _ablate_vwap_to_close,
    _ablate_vwap_to_settle,
    _ablate_zero_one_feature,
)
from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.factor_program import create_factor_program


# ─── Fixtures ─────────────────────────────────────────────


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """生成 200 天的合成 OHLCV 数据（含 vwap）。"""
    np.random.seed(42)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    volume = np.random.randint(1000, 10000, n).astype(float)
    data = pd.DataFrame(
        {
            "open": close + np.random.randn(n) * 0.1,
            "high": close + np.abs(np.random.randn(n)) * 0.3,
            "low": close - np.abs(np.random.randn(n)) * 0.3,
            "close": close,
            "volume": volume,
            "vwap": close + np.random.randn(n) * 0.05,
        },
        index=dates,
    )
    return data


@pytest.fixture
def forward_returns() -> np.ndarray:
    """生成未来收益率（与 sample_data 等长）。"""
    np.random.seed(42)
    n = 200
    ret = np.random.randn(n) * 0.01
    ret[-1] = 0.0  # 最后一天无未来收益
    return ret


@pytest.fixture
def vwap_factor() -> FactorProgram:
    """一个包含 vwap 的简单动量因子（vwap 从 volume 计算，以支持 volume_zero 消融验证）。"""
    code = '''
def factor_program(data, params):
    """Alpha: vwap_momentum — 使用 vwap 的简单动量因子"""
    import numpy as np
    import pandas as pd
    close = data['close'].values if hasattr(data, 'close') else data['close']
    volume = data['volume'].values if hasattr(data, 'volume') else np.ones_like(close)
    vwap_col = data['vwap'].values if hasattr(data, 'vwap') else data['close']
    # 从 volume 计算 vwap，使 volume_zero 消融能影响因子输出
    vwap = (close * volume) / np.maximum(volume, 1e-10)
    n = params.get("lookback", 10)
    # vwap 偏离度：价格相对平均成交成本的偏离
    vwap_ma = pd.Series(vwap).rolling(n, min_periods=1).mean().values
    score = (close - vwap_ma) / np.maximum(vwap_ma, 1e-10)
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
'''
    return create_factor_program(
        name="vwap_momentum",
        code=code,
        params={"lookback": 10},
        signature={
            "input_fields": ["close", "vwap"],
            "output_type": "signal",
            "frequency": "daily",
            "lookback": 10,
        },
        economic_logic={
            "theory": 4,
            "behavioral": 3,
            "microstructure": 3,
            "institutional": 3,
            "narrative": "vwap 偏离度动量因子",
        },
        source="seed",
        trace_id="test_ablation",
    )


# ─── 测试消融模式 ─────────────────────────────────────────


class TestAblationModes:
    """测试每种消融模式的数据扰动函数。"""

    def test_volume_zero(self, sample_data):
        """成交量置零后 volume 列全为 0。"""
        result = _ablate_volume_zero(sample_data)
        assert result["volume"].sum() == 0.0
        assert result.shape == sample_data.shape

    def test_vwap_to_close(self, sample_data):
        """VWAP 替换为 close 后，vwap 列与 close 相同。"""
        result = _ablate_vwap_to_close(sample_data)
        assert np.allclose(result["vwap"].values, result["close"].values)
        assert "vwap" in result.columns

    def test_vwap_to_settle(self, sample_data):
        """无 settle 时 VWAP 替换为 close。"""
        result = _ablate_vwap_to_settle(sample_data)
        assert "settle" not in sample_data.columns
        assert np.allclose(result["vwap"].values, result["close"].values)

    def test_vwap_to_settle_with_settle(self, sample_data):
        """有 settle 时 VWAP 替换为 settle。"""
        data = sample_data.copy()
        data["settle"] = data["close"] + 0.5
        result = _ablate_vwap_to_settle(data)
        assert np.allclose(result["vwap"].values, result["settle"].values)

    def test_shuffle_dates(self, sample_data):
        """时间戳打乱后数据形状不变，但顺序改变。"""
        result = _ablate_shuffle_dates(sample_data)
        assert result.shape == sample_data.shape
        # 至少有一个特征列顺序不同
        changed = False
        for col in sample_data.columns:
            if not np.array_equal(result[col].values, sample_data[col].values):
                changed = True
                break
        assert changed, "打乱后至少一个特征列顺序应改变"

    def test_zero_one_feature(self, sample_data):
        """单特征归零返回正确的特征映射。"""
        results = _ablate_zero_one_feature(sample_data)
        assert "volume" in results
        assert "close" in results
        assert "vwap" not in results  # vwap 被跳过
        assert results["volume"]["volume"].sum() == 0.0


# ─── 测试 AblationExperiment ─────────────────────────────


class TestAblationExperiment:
    """测试 AblationExperiment 主类。"""

    def test_run_returns_correct_structure(self, sample_data, forward_returns, vwap_factor):
        """run() 返回 AblationResult 且包含所有 5 种消融模式。"""
        experiment = AblationExperiment(random_seed=42)
        result = experiment.run(vwap_factor, sample_data, forward_returns)

        assert isinstance(result, AblationResult)
        assert result["factor_id"] == vwap_factor["factor_id"]
        assert result["factor_name"] == vwap_factor["name"]
        assert isinstance(result["baseline_ic"], float)
        assert isinstance(result["baseline_sharpe"], float)
        assert len(result["ablations"]) == 5

    def test_all_ablation_modes_present(self, sample_data, forward_returns, vwap_factor):
        """所有 5 种消融模式都存在于结果中。"""
        experiment = AblationExperiment(random_seed=42)
        result = experiment.run(vwap_factor, sample_data, forward_returns)

        modes = {ab["mode"] for ab in result["ablations"]}
        for expected_mode in ABLATION_MODES:
            assert expected_mode in modes, f"缺少消融模式: {expected_mode}"

    def test_baseline_is_stable(self, sample_data, forward_returns, vwap_factor):
        """相同 seed 下 baseline 稳定。"""
        experiment = AblationExperiment(random_seed=42)
        r1 = experiment.run(vwap_factor, sample_data, forward_returns)
        r2 = experiment.run(vwap_factor, sample_data, forward_returns)
        assert abs(r1["baseline_ic"] - r2["baseline_ic"]) < 1e-10

    def test_zero_one_feature_records_column(self, sample_data, forward_returns, vwap_factor):
        """zero_one_feature 消融记录被置零的特征列（v2.50.0 feature 契约）。

        SingleAblation.feature 字段应指向影响最大的非 date/vwap 列；
        其他信息型消融模式的 feature 为 None。
        """
        experiment = AblationExperiment(random_seed=42)
        result = experiment.run(vwap_factor, sample_data, forward_returns)

        zero_one = [ab for ab in result["ablations"] if ab["mode"] == "zero_one_feature"]
        assert len(zero_one) == 1
        # feature 应指向影响最大的非 date/vwap 列（vwap_factor 中 vwap 由 close×volume
        # 计算，置零 close 或 volume 均致 IC 崩塌；具体列以实算为准）
        assert zero_one[0]["feature"] in {"open", "high", "low", "close", "volume"}
        assert zero_one[0]["feature"] not in {"date", "vwap"}

        for mode in ("volume_zero", "vwap_to_close", "vwap_to_settle", "shuffle_dates"):
            entries = [ab for ab in result["ablations"] if ab["mode"] == mode]
            for ab in entries:
                assert ab["feature"] is None, f"{mode} 不应有 feature 字段"

    def test_single_ablation_feature_roundtrip(self):
        """SingleAblation 构造可显式传入 feature 字段。"""
        ab = SingleAblation(
            mode="zero_one_feature",
            description="单特征归零（影响最大: volume）",
            ic=0.01,
            sharpe=0.3,
            ic_change=-0.04,
            sharpe_change=-1.2,
            feature="volume",
        )
        assert ab["feature"] == "volume"
        assert "feature" in ab  # 序列化兼容（dict 子类）

    def test_run_batch_returns_list(self, sample_data, forward_returns, vwap_factor):
        """run_batch 返回正确长度的列表。"""
        experiment = AblationExperiment(random_seed=42)
        results = experiment.run_batch([vwap_factor, vwap_factor], sample_data, forward_returns)
        assert len(results) == 2
        assert all(isinstance(r, AblationResult) for r in results)

    def test_report_format(self, sample_data, forward_returns, vwap_factor):
        """report() 返回非空字符串。"""
        experiment = AblationExperiment(random_seed=42)
        result = experiment.run(vwap_factor, sample_data, forward_returns)
        report = experiment.report([result])
        assert isinstance(report, str)
        assert len(report) > 0
        assert "消融实验报告" in report
        assert vwap_factor["name"] in report

    def test_volume_zero_affects_vwap_factor(self, sample_data, forward_returns, vwap_factor):
        """vwap 因子在成交量置零后 IC 应变化（成交量影响 vwap 计算）。"""
        experiment = AblationExperiment(random_seed=42)
        result = experiment.run(vwap_factor, sample_data, forward_returns)

        vol_zero = [ab for ab in result["ablations"] if ab["mode"] == "volume_zero"]
        assert len(vol_zero) == 1
        # IC 变化可能为正或负，但不应该为 0
        assert abs(vol_zero[0]["ic_change"]) > 1e-6, "成交量置零后 IC 应变化"

    def test_ablation_result_serializable(self, sample_data, forward_returns, vwap_factor):
        """AblationResult 可序列化为 JSON 兼容格式。"""
        experiment = AblationExperiment(random_seed=42)
        result = experiment.run(vwap_factor, sample_data, forward_returns)
        import json

        json_str = json.dumps(result, ensure_ascii=False)
        assert isinstance(json_str, str)
        parsed = json.loads(json_str)
        assert parsed["factor_id"] == vwap_factor["factor_id"]


# ─── 测试异常处理 ─────────────────────────────────────────


class TestAblationEdgeCases:
    """测试消融实验的边界情况和异常处理。"""

    def test_no_volume_column(self, forward_returns, vwap_factor):
        """无 volume 列时成交量置零不报错。"""
        data = pd.DataFrame(
            {
                "close": 100 + np.cumsum(np.random.randn(100) * 0.5),
                "vwap": 100 + np.random.randn(100) * 0.05,
            }
        )
        # 此时 volume 列不存在，改 ablate 函数应直接返回原数据
        result = _ablate_volume_zero(data)
        assert "volume" not in result.columns

    def test_empty_data(self, forward_returns, vwap_factor):
        """空数据不报错。"""
        data = pd.DataFrame()
        experiment = AblationExperiment(random_seed=42)
        # 不应抛出异常
        result = experiment.run(vwap_factor, data, forward_returns)
        assert isinstance(result, AblationResult)

    def test_no_vwap_column(self, forward_returns):
        """无 vwap 列时 VWAP 消融模式不报错。"""
        n = 100
        data = pd.DataFrame(
            {
                "close": 100 + np.cumsum(np.random.randn(n) * 0.5),
                "volume": np.random.randint(1000, 10000, n).astype(float),
            }
        )
        # 使用与数据等长的 forward_returns
        fwd = np.random.randn(n) * 0.01
        fwd[-1] = 0.0
        # 使用不含 vwap 的因子
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
            source="seed",
            trace_id="test_no_vwap",
        )
        experiment = AblationExperiment(random_seed=42)
        result = experiment.run(factor, data, fwd)
        assert isinstance(result, AblationResult)
        assert len(result["ablations"]) == 5
