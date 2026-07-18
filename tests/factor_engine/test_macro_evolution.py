"""tests/factor_engine/test_macro_evolution.py — 宏观演化测试。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.contracts import (
    EconomicLogic,
    FactorProgram,
    FactorSignature,
)
from fts.factor_engine.factor_program import create_factor_program


# ─── 共享 fixtures ────────────────────────────────────────

@pytest.fixture
def parent_factor() -> FactorProgram:
    """标准父因子（含 window 参数）。"""
    code = """
import numpy as np
def factor_program(data, params):
    w = params.get('window', 20)
    close = data['close'].values
    signal = np.zeros(len(close))
    for i in range(w, len(close)):
        signal[i] = (close[i] - close[i-w]) / max(close[i-w], 1e-10)
    return np.clip(signal * 10, -1.0, 1.0)
"""
    return create_factor_program(
        name="momentum",
        code=code,
        params={"window": 20},
        signature=FactorSignature(input_fields=["close"], output_type="signal", frequency="daily", lookback=30),
        economic_logic=EconomicLogic(theory=4, behavioral=3, microstructure=3, institutional=5, narrative="动量因子"),
        source="seed",
        parent_id=None,
        generation=0,
    )


# ─── MockLLMClient ───────────────────────────────────────

class TestMockLLMClient:
    """MockLLMClient 基本行为。"""

    def test_complete_returns_tuple(self):
        from fts.factor_engine.macro_evolution import MockLLMClient
        client = MockLLMClient()
        result = client.complete("some prompt", 100)
        assert isinstance(result, tuple)
        assert len(result) == 2
        text, tokens = result
        assert isinstance(text, str)
        assert tokens == 200

    def test_complete_response_is_json(self):
        from fts.factor_engine.macro_evolution import MockLLMClient
        client = MockLLMClient()
        text, _ = client.complete("test")
        parsed = json.loads(text)
        assert "mutation_type" in parsed
        assert "code_modification" in parsed
        assert parsed["code_modification"] == "window_plus_5"

    def test_complete_different_prompts(self):
        """不同 prompt 应返回相同的 mock 结构（mock 行为）。"""
        from fts.factor_engine.macro_evolution import MockLLMClient
        client = MockLLMClient()
        r1, _ = client.complete("prompt A")
        r2, _ = client.complete("prompt B")
        assert json.loads(r1)["code_modification"] == "window_plus_5"
        assert json.loads(r2)["code_modification"] == "window_plus_5"

    def test_complete_includes_economic_logic(self):
        from fts.factor_engine.macro_evolution import MockLLMClient
        client = MockLLMClient()
        text, _ = client.complete("test")
        parsed = json.loads(text)
        el = parsed["economic_logic_modification"]
        assert el["theory"] == 4
        assert el["behavioral"] == 3
        assert el["microstructure"] == 3
        assert el["institutional"] == 5
        assert "测试用" in el["narrative"]


# ─── get_default_llm_client ───────────────────────────────

class TestGetDefaultLLMClient:
    """get_default_llm_client 应返回 MockLLMClient。"""

    def test_returns_mock_client(self):
        from fts.factor_engine.macro_evolution import get_default_llm_client, MockLLMClient
        client = get_default_llm_client()
        assert isinstance(client, MockLLMClient)

    def test_returns_callable(self):
        from fts.factor_engine.macro_evolution import get_default_llm_client
        client = get_default_llm_client()
        assert hasattr(client, "complete")
        assert callable(client.complete)


# ─── MacroEvolver 基本 ───────────────────────────────────

class TestMacroEvolverBasic:
    """MacroEvolver 基础行为。"""

    def test_init_minimal(self):
        """无参数初始化应使用 MockLLMClient。"""
        from fts.factor_engine.macro_evolution import MacroEvolver, MockLLMClient
        evolver = MacroEvolver()
        assert isinstance(evolver.llm, MockLLMClient)
        assert evolver.experience_chain is None
        assert evolver.max_tokens_per_call == 4000

    def test_init_with_custom_llm(self):
        """应接受自定义 LLM 客户端。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        mock_llm = MagicMock()
        mock_llm.complete.return_value = (
            json.dumps({
                "mutation_type": "macro_logic",
                "mutation_summary": "自定义测试",
                "code_modification": "window_plus_5",
                "economic_logic_modification": {
                    "theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3,
                    "narrative": "自定义 LLM"
                },
                "lessons_referenced": [],
            }),
            150,
        )
        evolver = MacroEvolver(llm_client=mock_llm)
        assert evolver.llm is mock_llm

    def test_init_custom_max_tokens(self):
        from fts.factor_engine.macro_evolution import MacroEvolver
        evolver = MacroEvolver(max_tokens_per_call=8000)
        assert evolver.max_tokens_per_call == 8000

    def test_macro_evolution_error(self):
        from fts.factor_engine.macro_evolution import MacroEvolutionError
        assert issubclass(MacroEvolutionError, Exception)


# ─── MacroEvolver.evolve ─────────────────────────────────

class TestMacroEvolverEvolve:
    """MacroEvolver.evolve 完整路径。"""

    def test_evolve_returns_tuple(self, parent_factor):
        from fts.factor_engine.macro_evolution import MacroEvolver
        evolver = MacroEvolver()
        new_factor, summary, tokens = evolver.evolve(parent_factor, generation=1, trace_id="trace_test")
        assert isinstance(new_factor, dict)
        assert "factor_id" in new_factor
        assert new_factor["generation"] == 1
        assert new_factor["source"] == "macro_evolution"
        assert isinstance(summary, str)
        assert isinstance(tokens, int)

    def test_evolve_increments_generation(self, parent_factor):
        from fts.factor_engine.macro_evolution import MacroEvolver
        evolver = MacroEvolver()
        new_factor, _, _ = evolver.evolve(parent_factor, generation=5)
        assert new_factor["generation"] == 5
        assert new_factor["name"] == "momentum_g5"

    def test_evolve_sets_parent_id(self, parent_factor):
        from fts.factor_engine.macro_evolution import MacroEvolver
        evolver = MacroEvolver()
        parent_id = parent_factor["factor_id"]
        new_factor, _, _ = evolver.evolve(parent_factor, generation=1)
        assert new_factor["parent_id"] == parent_id

    def test_evolve_with_trace_id(self, parent_factor):
        from fts.factor_engine.macro_evolution import MacroEvolver
        evolver = MacroEvolver()
        new_factor, _, tokens = evolver.evolve(parent_factor, generation=1, trace_id="l2_abc123_20260718")
        assert new_factor["trace_id"] == "l2_abc123_20260718"

    def test_evolve_mutation_summary_fallback(self, parent_factor):
        """缺少 mutation_summary 时应 fallback。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        mock_llm = MagicMock()
        mock_llm.complete.return_value = (
            json.dumps({
                "mutation_type": "macro_logic",
                # 故意缺少 mutation_summary
                "code_modification": "window_plus_5",
                "economic_logic_modification": {
                    "theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3,
                    "narrative": "fallback 测试"
                },
                "lessons_referenced": [],
            }),
            100,
        )
        evolver = MacroEvolver(llm_client=mock_llm)
        _, summary, tokens = evolver.evolve(parent_factor, generation=1)
        assert "代 1" in summary  # fallback 格式

    def test_evolve_llm_raises(self, parent_factor):
        """LLM 异常应转 MacroEvolutionError。"""
        from fts.factor_engine.macro_evolution import MacroEvolver, MacroEvolutionError
        mock_llm = MagicMock()
        mock_llm.complete.side_effect = RuntimeError("LLM 挂了")
        evolver = MacroEvolver(llm_client=mock_llm)
        with pytest.raises(MacroEvolutionError, match="LLM 调用失败"):
            evolver.evolve(parent_factor, generation=1)

    def test_evolve_invalid_json_response(self, parent_factor):
        """非 JSON 响应应抛 MacroEvolutionError。"""
        from fts.factor_engine.macro_evolution import MacroEvolver, MacroEvolutionError
        mock_llm = MagicMock()
        mock_llm.complete.return_value = ("not json at all", 50)
        evolver = MacroEvolver(llm_client=mock_llm)
        with pytest.raises(MacroEvolutionError, match="LLM 响应非 JSON"):
            evolver.evolve(parent_factor, generation=1)


# ─── MacroEvolver 内部方法 ──────────────────────────────

class TestMacroEvolverInternal:
    """MacroEvolver 内部方法覆盖。"""

    def test_read_experience_no_chain(self, parent_factor):
        """无 experience_chain 时应返回空字典。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        evolver = MacroEvolver()
        result = evolver._read_experience_for_llm()
        assert result == {"success": [], "failure": []}

    def test_read_experience_with_chain(self, parent_factor, tmp_memory_dir):
        """有 experience_chain 时应返回数据。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        from fts.factor_engine.experience_chain import ExperienceChain

        chain = ExperienceChain(tmp_memory_dir)
        evolver = MacroEvolver(experience_chain=chain)
        result = evolver._read_experience_for_llm()
        assert isinstance(result, dict)
        assert "success" in result
        assert "failure" in result

    def test_build_prompt_contains_info(self, parent_factor):
        from fts.factor_engine.macro_evolution import MacroEvolver
        evolver = MacroEvolver()
        prompt = evolver._build_prompt(parent_factor, 3, {"success": [], "failure": []})
        assert "父因子" in prompt
        assert parent_factor["name"] in prompt
        assert "代 3" in prompt
        assert "最近成功轨迹" in prompt
        assert "最近失败轨迹" in prompt

    def test_format_experience_empty(self):
        from fts.factor_engine.macro_evolution import MacroEvolver
        result = MacroEvolver._format_experience_for_prompt([])
        assert result == "(无)"

    def test_format_experience_with_traces(self):
        from fts.factor_engine.macro_evolution import MacroEvolver
        from fts.factor_engine.contracts import (
            BacktestMetrics, EconomicScore, ExperienceTrace, FactorEvaluation, MultipleTestResult,
        )
        traces = [
            ExperienceTrace(
                trace_id="t1", factor_id="fct_a", parent_id=None, generation=1,
                mutation_type="macro_logic", mutation_summary="测试 A",
                evaluation=FactorEvaluation(
                    factor_id="fct_a", trace_id="t1",
                    level_1_backtest=BacktestMetrics(ic=0.05, sharpe=1.8),
                    level_2_economic=EconomicScore(),
                    level_3_multiple=MultipleTestResult(),
                    passed=True, failure_reasons=[], evaluated_at="2026-07-18",
                ),
                success=True, lessons=["lesson 1"], recorded_at="2026-07-18",
            ),
        ]
        result = MacroEvolver._format_experience_for_prompt(traces)
        assert "fct_a" in result
        assert "0.05" in result
        assert "1.8" in result

    def test_format_experience_with_failures(self):
        from fts.factor_engine.macro_evolution import MacroEvolver
        from fts.factor_engine.contracts import (
            BacktestMetrics, EconomicScore, ExperienceTrace, FactorEvaluation, MultipleTestResult,
        )
        traces = [
            ExperienceTrace(
                trace_id="t2", factor_id="fct_b", parent_id=None, generation=1,
                mutation_type="macro_logic", mutation_summary="测试 B",
                evaluation=FactorEvaluation(
                    factor_id="fct_b", trace_id="t2",
                    level_1_backtest=BacktestMetrics(ic=0.01, sharpe=0.5),
                    level_2_economic=EconomicScore(),
                    level_3_multiple=MultipleTestResult(),
                    passed=False, failure_reasons=["IC 过低", "夏普偏低"],
                    evaluated_at="2026-07-18",
                ),
                success=False, lessons=["避免低 IC"], recorded_at="2026-07-18",
            ),
        ]
        result = MacroEvolver._format_experience_for_prompt(traces)
        assert "IC 过低" in result
        assert "夏普偏低" in result

    def test_apply_code_modification_empty(self):
        from fts.factor_engine.macro_evolution import MacroEvolver
        code = "original code"
        result = MacroEvolver._apply_code_modification(code, "")
        assert result == code

    def test_apply_code_modification_window_plus_5(self):
        from fts.factor_engine.macro_evolution import MacroEvolver
        code = """
def factor_program(data, params):
    w = params.get('window', 20)
    return data['close'].values * w
"""
        result = MacroEvolver._apply_code_modification(code, "window_plus_5")
        assert "params.get('window', 25)" in result

    def test_apply_code_modification_unknown(self):
        from fts.factor_engine.macro_evolution import MacroEvolver
        code = "original code"
        result = MacroEvolver._apply_code_modification(code, "unknown_modification")
        assert result == code

    def test_apply_code_modification_no_window(self):
        """代码中无 window 参数时应原样返回。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        code = "def factor_program(data, params):\n    return data['close'].values"
        result = MacroEvolver._apply_code_modification(code, "window_plus_5")
        assert result == code


# ─── 完整演化集成 ──────────────────────────────────────

class TestMacroEvolverIntegration:
    """使用真实 MockLLMClient 的集成测试。"""

    def test_full_evolve_with_experience_chain(self, parent_factor, tmp_memory_dir):
        """带经验链的完整演化。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        from fts.factor_engine.experience_chain import ExperienceChain

        chain = ExperienceChain(tmp_memory_dir)
        # 先记录一条成功轨迹
        from fts.factor_engine.contracts import (
            BacktestMetrics, EconomicScore, FactorEvaluation, MultipleTestResult,
        )
        from fts.factor_engine.experience_chain import create_trace_from_evaluation
        trace = create_trace_from_evaluation(
            factor_id=parent_factor["factor_id"],
            parent_id=None, generation=0,
            mutation_type="macro_logic",
            mutation_summary="初始种子",
            evaluation=FactorEvaluation(
                factor_id=parent_factor["factor_id"], trace_id="t_init",
                level_1_backtest=BacktestMetrics(ic=0.03, sharpe=1.5),
                level_2_economic=EconomicScore(theory=3, behavioral=3, microstructure=3, institutional=3),
                level_3_multiple=MultipleTestResult(passed=True),
                passed=True, failure_reasons=[], evaluated_at="2026-07-18",
            ),
            lessons=["初始成功"],
            trace_id="t_init",
        )
        chain.record_success(trace)

        evolver = MacroEvolver(experience_chain=chain)
        new_factor, summary, tokens = evolver.evolve(parent_factor, generation=1)
        assert new_factor["generation"] == 1
        assert tokens == 200

    def test_evolve_inherits_params(self, parent_factor):
        """新因子应继承父因子的 params。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        evolver = MacroEvolver()
        new_factor, _, _ = evolver.evolve(parent_factor, generation=1)
        assert new_factor["params"] == {"window": 20}

    def test_evolve_emits_tokens(self, parent_factor):
        """应返回 mock 消耗的 token 数。"""
        from fts.factor_engine.macro_evolution import MacroEvolver
        evolver = MacroEvolver()
        _, _, tokens = evolver.evolve(parent_factor, generation=1)
        assert tokens == 200
