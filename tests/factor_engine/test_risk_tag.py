"""
test_risk_tag.py — 风险标签闭环验证

HARNESS §11-logic-review-plan.md §A.3:
    验证 loader 正确设置 risk_tag，evolution_loop 对 vwap_approx 因子施加更高 IC 阈值。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.factor_program import create_factor_program
from fts.factor_engine.seed_data.loader import make_factor_program


# ─── 测试 loader 风险标签设置 ─────────────────────────────

class TestLoaderRiskTag:
    """验证 loader 正确设置 risk_tag。"""

    def test_vwap_expression_sets_risk_tag(self):
        """含 vwap 表达式的因子应标记 risk_tag="vwap_approx"。"""
        fp = make_factor_program(
            name="alpha_vwap_test",
            expression="close - vwap",
            narrative="测试 vwap 因子",
        )
        assert fp.get("risk_tag") == "vwap_approx", f"预期 vwap_approx，实际 {fp.get('risk_tag')}"

    def test_vwap_upper_case_expression(self):
        """表达式中 vwap 大写也应被检测。"""
        fp = make_factor_program(
            name="alpha_VWAP_test",
            expression="VWAP - close",
            narrative="测试大写 VWAP",
        )
        assert fp.get("risk_tag") == "vwap_approx"

    def test_non_vwap_expression_no_risk_tag(self):
        """不含 vwap 的因子不应标记 risk_tag。"""
        fp = make_factor_program(
            name="alpha_no_vwap",
            expression="close - open",
            narrative="不含 vwap 的因子",
        )
        assert fp.get("risk_tag") is None, f"预期 None，实际 {fp.get('risk_tag')}"

    def test_volume_only_expression_no_risk_tag(self):
        """仅含 volume 的因子不应标记 risk_tag。"""
        fp = make_factor_program(
            name="alpha_volume_only",
            expression="volume / ts_mean(volume, 20)",
            narrative="纯量价因子（不含 vwap）",
        )
        assert fp.get("risk_tag") is None

    def test_mixed_expression_with_vwap(self):
        """含 vwap 和其他字段的混合表达式应标记。"""
        fp = make_factor_program(
            name="alpha_mixed",
            expression="(close - vwap) / volume * ts_mean(close, 20)",
            narrative="混合表达式含 vwap",
        )
        assert fp.get("risk_tag") == "vwap_approx"


# ─── 测试 evolution_loop 风险标签阈值 ─────────────────────

class TestEvolutionLoopRiskTag:
    """验证 evolution_loop 对 vwap_approx 因子施加更高 IC 阈值。"""

    @pytest.fixture
    def sample_data(self) -> pd.DataFrame:
        """生成合成数据。"""
        np.random.seed(42)
        n = 200
        dates = pd.date_range("2024-01-01", periods=n, freq="D")
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)
        return pd.DataFrame({
            "open": close + np.random.randn(n) * 0.1,
            "high": close + np.abs(np.random.randn(n)) * 0.3,
            "low": close - np.abs(np.random.randn(n)) * 0.3,
            "close": close,
            "volume": np.random.randint(1000, 10000, n).astype(float),
        }, index=dates)

    @pytest.fixture
    def forward_returns(self) -> np.ndarray:
        """生成未来收益率。"""
        np.random.seed(42)
        n = 200
        ret = np.random.randn(n) * 0.01
        ret[-1] = 0.0
        return ret

    def _make_mock_evaluation(self, ic: float, passed: bool = True) -> dict:
        """构造 mock FactorEvaluation。"""
        return {
            "factor_id": "fct_test",
            "trace_id": "test",
            "level_1_backtest": {"ic": ic, "sharpe": 2.0, "t_stat": 3.0},
            "level_2_economic": {"dimensions_passed": 3},
            "level_3_multiple": {"adjusted_t": 3.0, "passed": True},
            "passed": passed,
            "failure_reasons": [],
            "evaluated_at": "2026-01-01T00:00:00",
        }

    def test_vwap_approx_ic_006_skipped(self, sample_data, forward_returns):
        """vwap_approx 因子 IC=0.06 应被跳过（阈值 0.08）。"""
        from fts.factor_engine.evolution_loop import EvolutionLoop

        # 构造带风险标签的种子因子
        seed = create_factor_program(
            name="test_vwap_risk",
            code='''
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    score = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
''',
            params={},
            signature={"input_fields": ["close"], "output_type": "signal",
                       "frequency": "daily", "lookback": 2},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3,
                            "institutional": 3, "narrative": "测试"},
            source="seed",
            risk_tag="vwap_approx",
        )

        loop = EvolutionLoop(
            data=sample_data,
            forward_returns=forward_returns,
            elite_dir="memory/evolution/test_elite",
        )

        # Mock evaluation_chain.evaluate 返回 IC=0.06, passed=True
        # 注：非横截面模式下 _evaluate_and_promote_seeds 调用 evaluation_chain.evaluate
        with patch.object(loop.evaluation_chain, 'evaluate',
                          return_value=self._make_mock_evaluation(ic=0.06, passed=True)):
            elite_ids: list[str] = []
            promoted = loop._evaluate_and_promote_seeds(
                seeds=[seed], trace_id="test", state={
                    "run_id": "test", "started_at": "", "last_generation": 0,
                    "total_factors_evaluated": 0, "total_factors_promoted": 0,
                    "tokens_consumed": 0, "budget_limit": 200000,
                    "status": "running", "last_error": None,
                    "experience_chain_ref": [], "last_updated": "",
                    "version": "1.0.0",
                }, elite_ids=elite_ids,
            )
            assert promoted == 0, "vwap_approx 因子 IC=0.06 不应晋升"
            assert len(elite_ids) == 0

    def test_vwap_approx_ic_009_promoted(self, sample_data, forward_returns):
        """vwap_approx 因子 IC=0.09 应晋升（≥ 0.08）。"""
        from fts.factor_engine.evolution_loop import EvolutionLoop

        seed = create_factor_program(
            name="test_vwap_risk_high",
            code='''
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    score = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
''',
            params={},
            signature={"input_fields": ["close"], "output_type": "signal",
                       "frequency": "daily", "lookback": 2},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3,
                            "institutional": 3, "narrative": "测试"},
            source="seed",
            risk_tag="vwap_approx",
        )

        loop = EvolutionLoop(
            data=sample_data,
            forward_returns=forward_returns,
            elite_dir="memory/evolution/test_elite",
        )

        # Mock evaluation_chain.evaluate 返回 IC=0.09, passed=True
        with patch.object(loop.evaluation_chain, 'evaluate',
                          return_value=self._make_mock_evaluation(ic=0.09, passed=True)):
            elite_ids: list[str] = []
            promoted = loop._evaluate_and_promote_seeds(
                seeds=[seed], trace_id="test", state={
                    "run_id": "test", "started_at": "", "last_generation": 0,
                    "total_factors_evaluated": 0, "total_factors_promoted": 0,
                    "tokens_consumed": 0, "budget_limit": 200000,
                    "status": "running", "last_error": None,
                    "experience_chain_ref": [], "last_updated": "",
                    "version": "1.0.0",
                }, elite_ids=elite_ids,
            )
            assert promoted == 1, "vwap_approx 因子 IC=0.09 应晋升"
            assert len(elite_ids) == 1

    def test_no_risk_tag_ic_006_promoted(self, sample_data, forward_returns):
        """无 risk_tag 的因子 IC=0.06 应正常晋升（默认阈值 0.03）。"""
        from fts.factor_engine.evolution_loop import EvolutionLoop

        seed = create_factor_program(
            name="test_no_risk",
            code='''
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    score = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
''',
            params={},
            signature={"input_fields": ["close"], "output_type": "signal",
                       "frequency": "daily", "lookback": 2},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3,
                            "institutional": 3, "narrative": "测试"},
            source="seed",
            risk_tag=None,
        )

        loop = EvolutionLoop(
            data=sample_data,
            forward_returns=forward_returns,
            elite_dir="memory/evolution/test_elite",
        )

        # Mock evaluation_chain.evaluate 返回 IC=0.06, passed=True
        with patch.object(loop.evaluation_chain, 'evaluate',
                          return_value=self._make_mock_evaluation(ic=0.06, passed=True)):
            elite_ids: list[str] = []
            promoted = loop._evaluate_and_promote_seeds(
                seeds=[seed], trace_id="test", state={
                    "run_id": "test", "started_at": "", "last_generation": 0,
                    "total_factors_evaluated": 0, "total_factors_promoted": 0,
                    "tokens_consumed": 0, "budget_limit": 200000,
                    "status": "running", "last_error": None,
                    "experience_chain_ref": [], "last_updated": "",
                    "version": "1.0.0",
                }, elite_ids=elite_ids,
            )
            assert promoted == 1, "无 risk_tag 因子 IC=0.06 应晋升"
            assert len(elite_ids) == 1