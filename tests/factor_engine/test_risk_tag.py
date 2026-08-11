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
        return pd.DataFrame(
            {
                "open": close + np.random.randn(n) * 0.1,
                "high": close + np.abs(np.random.randn(n)) * 0.3,
                "low": close - np.abs(np.random.randn(n)) * 0.3,
                "close": close,
                "volume": np.random.randint(1000, 10000, n).astype(float),
            },
            index=dates,
        )

    @pytest.fixture
    def forward_returns(self) -> np.ndarray:
        """生成未来收益率。"""
        np.random.seed(42)
        n = 200
        ret = np.random.randn(n) * 0.01
        ret[-1] = 0.0
        return ret

    def _make_mock_evaluation(self, ic: float, passed: bool = True) -> dict:
        """构造 mock FactorEvaluation（含质检评分卡所需字段）。

        注意: _evaluate_and_promote_seeds 会经 FactorQualityCard 质检（min_grade=B），
        mock 需提供 icir/sharpe/walk_forward/经济四维等字段，否则总分不足被 C 级淘汰。
        v2.50.0 起种子路径新增 Verifier 判定，mock 需补齐
        max_drawdown/monotonicity/oos_ratio/adjusted_t/fdr_q 字段使真实 Verifier 通过。
        """
        return {
            "factor_id": "fct_test",
            "trace_id": "test",
            "level_1_backtest": {
                "ic": ic,
                "sharpe": 3.0,
                "t_stat": 3.0,
                "icir": 3.0,
                "decay_6m": 0.1,
                "turnover_monthly": 0.3,
                "max_drawdown": 0.2,
                "monotonicity": True,
                "oos_ratio": 0.35,
            },
            "level_2_economic": {
                "theory": 4,
                "behavioral": 4,
                "microstructure": 4,
                "institutional": 4,
                "dimensions_passed": 3,
            },
            "level_3_multiple": {"adjusted_t": 3.5, "fdr_q": 0.01, "passed": True},
            "walk_forward": {
                "ic_consistency": 0.9,
                "ic_volatility": 0.1,
                "consistency_score": 0.9,
                "window_score": 1.0,
            },
            "passed": passed,
            "failure_reasons": [],
            "evaluated_at": "2026-01-01T00:00:00",
        }

    @staticmethod
    def _mock_quality_gates_pass(loop) -> None:
        """mock v2.50.0 种子路径新增的 Verifier/消融/因果/鲁棒/SHAP 审查通过。

        用于聚焦验证既有晋升链路（vwap 门槛、质量卡、审计等）的测试，
        避免真实审查模块（依赖数据/随机性）导致结果不稳定。
        """
        loop.verifier.check = MagicMock(return_value={"passed": True, "failure_reasons": []})
        loop._run_ablation_check = MagicMock(return_value={"passed": True})
        loop._run_causal_validation = MagicMock(return_value={"passed": True})
        loop._run_robustness_check = MagicMock(return_value={"passed": True})
        loop._run_shap_analysis = MagicMock(return_value={})

    def test_vwap_approx_ic_006_skipped(self, sample_data, forward_returns, tmp_path):
        """vwap_approx 因子 IC=0.06 应被跳过（阈值 0.08）。"""
        from fts.factor_engine.evolution_loop import EvolutionLoop

        # 构造带风险标签的种子因子
        seed = create_factor_program(
            name="test_vwap_risk",
            code="""
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    score = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
""",
            params={},
            signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 2},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "测试"},
            source="seed",
            risk_tag="vwap_approx",
        )

        loop = EvolutionLoop(
            data=sample_data,
            forward_returns=forward_returns,
            elite_dir="memory/evolution/test_elite",
            factor_db_path=tmp_path / "test_catalog.duckdb",
        )

        # Mock evaluation_chain.evaluate 返回 IC=0.06, passed=True
        # 注：非横截面模式下 _evaluate_and_promote_seeds 调用 evaluation_chain.evaluate
        with patch.object(
            loop.evaluation_chain, "evaluate", return_value=self._make_mock_evaluation(ic=0.06, passed=True)
        ):
            elite_ids: list[str] = []
            promoted = loop._evaluate_and_promote_seeds(
                seeds=[seed],
                trace_id="test",
                state={
                    "run_id": "test",
                    "started_at": "",
                    "last_generation": 0,
                    "total_factors_evaluated": 0,
                    "total_factors_promoted": 0,
                    "tokens_consumed": 0,
                    "budget_limit": 200000,
                    "status": "running",
                    "last_error": None,
                    "experience_chain_ref": [],
                    "last_updated": "",
                    "version": "1.0.0",
                },
                elite_ids=elite_ids,
            )
            assert promoted == 0, "vwap_approx 因子 IC=0.06 不应晋升"
            assert len(elite_ids) == 0

    def test_vwap_approx_ic_009_promoted(self, sample_data, forward_returns, tmp_path):
        """vwap_approx 因子 IC=0.09 应晋升（≥ 0.08）。"""
        from fts.factor_engine.evolution_loop import EvolutionLoop

        seed = create_factor_program(
            name="test_vwap_risk_high",
            code="""
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    score = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
""",
            params={},
            signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 2},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "测试"},
            source="seed",
            risk_tag="vwap_approx",
        )

        loop = EvolutionLoop(
            data=sample_data,
            forward_returns=forward_returns,
            elite_dir="memory/evolution/test_elite",
            factor_db_path=tmp_path / "test_catalog.duckdb",
        )
        loop._cluster_quota_enabled = False  # GAP-077: 结构簇配额滞后同步，与 test_evolution_loop.py 一致

        # Mock evaluation_chain.evaluate 返回 IC=0.09, passed=True
        with patch.object(
            loop.evaluation_chain, "evaluate", return_value=self._make_mock_evaluation(ic=0.09, passed=True)
        ):
            # v2.50.0 新增全链审查 mock 通过（聚焦 vwap 门槛验证）
            self._mock_quality_gates_pass(loop)
            elite_ids: list[str] = []
            promoted = loop._evaluate_and_promote_seeds(
                seeds=[seed],
                trace_id="test",
                state={
                    "run_id": "test",
                    "started_at": "",
                    "last_generation": 0,
                    "total_factors_evaluated": 0,
                    "total_factors_promoted": 0,
                    "tokens_consumed": 0,
                    "budget_limit": 200000,
                    "status": "running",
                    "last_error": None,
                    "experience_chain_ref": [],
                    "last_updated": "",
                    "version": "1.0.0",
                },
                elite_ids=elite_ids,
            )
            assert promoted == 1, "vwap_approx 因子 IC=0.09 应晋升"
            assert len(elite_ids) == 1

    def test_no_risk_tag_ic_006_promoted(self, sample_data, forward_returns, tmp_path):
        """无 risk_tag 的因子 IC=0.06 应正常晋升（默认阈值 0.03）。"""
        from fts.factor_engine.evolution_loop import EvolutionLoop

        seed = create_factor_program(
            name="test_no_risk",
            code="""
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    score = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
""",
            params={},
            signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 2},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "测试"},
            source="seed",
            risk_tag=None,
        )

        loop = EvolutionLoop(
            data=sample_data,
            forward_returns=forward_returns,
            elite_dir="memory/evolution/test_elite",
            factor_db_path=tmp_path / "test_catalog.duckdb",
        )
        loop._cluster_quota_enabled = False  # GAP-077: 结构簇配额滞后同步，与 test_evolution_loop.py 一致

        # Mock evaluation_chain.evaluate 返回 IC=0.06, passed=True
        with patch.object(
            loop.evaluation_chain, "evaluate", return_value=self._make_mock_evaluation(ic=0.06, passed=True)
        ):
            # v2.50.0 新增全链审查 mock 通过（聚焦默认 IC 门槛验证）
            self._mock_quality_gates_pass(loop)
            elite_ids: list[str] = []
            promoted = loop._evaluate_and_promote_seeds(
                seeds=[seed],
                trace_id="test",
                state={
                    "run_id": "test",
                    "started_at": "",
                    "last_generation": 0,
                    "total_factors_evaluated": 0,
                    "total_factors_promoted": 0,
                    "tokens_consumed": 0,
                    "budget_limit": 200000,
                    "status": "running",
                    "last_error": None,
                    "experience_chain_ref": [],
                    "last_updated": "",
                    "version": "1.0.0",
                },
                elite_ids=elite_ids,
            )
            assert promoted == 1, "无 risk_tag 因子 IC=0.06 应晋升"
            assert len(elite_ids) == 1

    # ─── v2.50.0: 种子因子质检全链对齐（Verifier/消融/因果/鲁棒/SHAP） ──

    def _make_seed(self, name: str) -> FactorProgram:
        """构造无 risk_tag 的普通种子因子。"""
        return create_factor_program(
            name=name,
            code="""
def factor_program(data, params):
    import numpy as np
    close = data['close'].values if hasattr(data, 'close') else data['close']
    score = np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10)
    return np.clip(np.nan_to_num(score, nan=0.0), -1.0, 1.0)
""",
            params={},
            signature={"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 2},
            economic_logic={"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "测试"},
            source="seed",
            risk_tag=None,
        )

    @staticmethod
    def _seed_state() -> dict[str, Any]:
        return {
            "run_id": "test",
            "started_at": "",
            "last_generation": 0,
            "total_factors_evaluated": 0,
            "total_factors_promoted": 0,
            "tokens_consumed": 0,
            "budget_limit": 200000,
            "status": "running",
            "last_error": None,
            "experience_chain_ref": [],
            "last_updated": "",
            "version": "1.0.0",
        }

    def _run_seed_promotion(self, loop, seed) -> int:
        elite_ids: list[str] = []
        promoted = loop._evaluate_and_promote_seeds(
            seeds=[seed],
            trace_id="test",
            state=self._seed_state(),
            elite_ids=elite_ids,
        )
        return promoted

    def _make_loop(self, sample_data, forward_returns, tmp_path):
        from fts.factor_engine.evolution_loop import EvolutionLoop

        return EvolutionLoop(
            data=sample_data,
            forward_returns=forward_returns,
            elite_dir="memory/evolution/test_elite",
            factor_db_path=tmp_path / "test_catalog.duckdb",
        )

    def test_seed_verifier_fail_not_promoted(self, sample_data, forward_returns, tmp_path):
        """v2.50.0: Verifier 判定失败 → 种子不晋升。"""
        seed = self._make_seed("seed_verifier_fail")
        loop = self._make_loop(sample_data, forward_returns, tmp_path)
        with patch.object(
            loop.evaluation_chain, "evaluate", return_value=self._make_mock_evaluation(ic=0.09, passed=True)
        ):
            loop.verifier.check = MagicMock(return_value={"passed": False, "failure_reasons": ["mock verifier fail"]})
            loop._run_ablation_check = MagicMock(return_value={"passed": True})
            loop._run_causal_validation = MagicMock(return_value={"passed": True})
            loop._run_robustness_check = MagicMock(return_value={"passed": True})
            loop._run_shap_analysis = MagicMock(return_value={})
            promoted = self._run_seed_promotion(loop, seed)
        assert promoted == 0, "Verifier 失败种子不应晋升"

    def test_seed_ablation_fail_not_promoted(self, sample_data, forward_returns, tmp_path):
        """v2.50.0: 消融实验失败（疑似伪相关）→ 种子不晋升。"""
        seed = self._make_seed("seed_ablation_fail")
        loop = self._make_loop(sample_data, forward_returns, tmp_path)
        with patch.object(
            loop.evaluation_chain, "evaluate", return_value=self._make_mock_evaluation(ic=0.09, passed=True)
        ):
            loop.verifier.check = MagicMock(return_value={"passed": True, "failure_reasons": []})
            loop._run_ablation_check = MagicMock(return_value={"passed": False, "ablations": []})
            loop._run_causal_validation = MagicMock(return_value={"passed": True})
            loop._run_robustness_check = MagicMock(return_value={"passed": True})
            loop._run_shap_analysis = MagicMock(return_value={})
            promoted = self._run_seed_promotion(loop, seed)
        assert promoted == 0, "消融失败种子不应晋升"

    def test_seed_causal_fail_not_promoted(self, sample_data, forward_returns, tmp_path):
        """v2.50.0: 因果结构审查失败（事件敏感）→ 种子不晋升。"""
        seed = self._make_seed("seed_causal_fail")
        loop = self._make_loop(sample_data, forward_returns, tmp_path)
        with patch.object(
            loop.evaluation_chain, "evaluate", return_value=self._make_mock_evaluation(ic=0.09, passed=True)
        ):
            loop.verifier.check = MagicMock(return_value={"passed": True, "failure_reasons": []})
            loop._run_ablation_check = MagicMock(return_value={"passed": True})
            loop._run_causal_validation = MagicMock(return_value={"passed": False})
            loop._run_robustness_check = MagicMock(return_value={"passed": True})
            loop._run_shap_analysis = MagicMock(return_value={})
            promoted = self._run_seed_promotion(loop, seed)
        assert promoted == 0, "因果审查失败种子不应晋升"

    def test_seed_robustness_fail_not_promoted(self, sample_data, forward_returns, tmp_path):
        """v2.50.0: 鲁棒性审查失败 → 种子不晋升。"""
        seed = self._make_seed("seed_robustness_fail")
        loop = self._make_loop(sample_data, forward_returns, tmp_path)
        with patch.object(
            loop.evaluation_chain, "evaluate", return_value=self._make_mock_evaluation(ic=0.09, passed=True)
        ):
            loop.verifier.check = MagicMock(return_value={"passed": True, "failure_reasons": []})
            loop._run_ablation_check = MagicMock(return_value={"passed": True})
            loop._run_causal_validation = MagicMock(return_value={"passed": True})
            loop._run_robustness_check = MagicMock(return_value={"passed": False})
            loop._run_shap_analysis = MagicMock(return_value={})
            promoted = self._run_seed_promotion(loop, seed)
        assert promoted == 0, "鲁棒性失败种子不应晋升"
