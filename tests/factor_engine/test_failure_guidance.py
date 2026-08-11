"""tests/factor_engine/test_failure_guidance.py — Phase 1.1 P0-2 失败归因定向修复测试。

覆盖（26 号计划 §5）:
    1. ExperienceChain.read_failures_by_parent: 按 parent_id 过滤 / 时间倒序 /
       limit 生效 / 无记录返回 []
    2. MacroEvolver prompt 注入: 有 parent_failure_ctx → prompt 含"父因子最近失败归因"
       + 定向修复要求；无 ctx → 不含该段落（现有行为不变）
    3. EvolutionLoop._evolve_one 传递: macro 演化分支读取失败归因并传给 evolver
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from fts.factor_engine.experience_chain import ExperienceChain
from fts.factor_engine.macro_evolution import MacroEvolver
from fts.factor_engine.contracts import FactorEvaluation


# ─── 工具 ─────────────────────────────────────────────────


def _failure_trace(
    trace_id: str,
    parent_id: str,
    factor_id: str,
    reasons: list[str],
) -> dict:
    """构造失败轨迹 dict（满足 record_failure 验证）。"""
    return {
        "trace_id": trace_id,
        "parent_trace_id": f"l2_{trace_id}",
        "factor_id": factor_id,
        "parent_id": parent_id,
        "generation": 1,
        "mutation_type": "macro_logic",
        "mutation_summary": f"failure {trace_id}",
        "success": False,
        "evaluation": FactorEvaluation(
            factor_id=factor_id,
            trace_id=trace_id,
            passed=False,
            failure_reasons=reasons,
            evaluated_at="2026-08-11T00:00:00",
        ),
        "lessons": [f"代 1 失败: {reasons[0] if reasons else '?'}"],
        "recorded_at": "2026-08-11T00:00:00",
    }


def _parent_factor(fid: str = "fct_parent") -> dict:
    """构造最小父因子。"""
    return {
        "factor_id": fid,
        "name": "parent_momentum",
        "code": (
            "def factor_program(data, params):\n"
            "    import numpy as np\n"
            "    close = data['close']\n"
            "    n = len(close)\n"
            "    return np.tanh(np.diff(close, prepend=close[0]) / np.maximum(close, 1e-10) * 10)\n"
        ),
        "params": {"window": 10},
        "signature": {"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 1},
        "economic_logic": {"theory": 3, "behavioral": 3, "microstructure": 3, "institutional": 3, "narrative": "t"},
    }


def _mock_llm_ok() -> MagicMock:
    """返回返回合法 JSON 的 mock LLM。"""
    llm = MagicMock()
    llm.complete.return_value = (
        json.dumps(
            {
                "mutation_type": "macro_logic",
                "mutation_summary": "mutation",
                "full_code": (
                    "def factor_program(data, params):\n"
                    "    import numpy as np\n"
                    "    close = data['close']\n"
                    "    return np.zeros(len(close))\n"
                ),
                "economic_logic_modification": {
                    "theory": 3,
                    "behavioral": 3,
                    "microstructure": 3,
                    "institutional": 3,
                    "narrative": "n",
                },
                "lessons_referenced": [],
            }
        ),
        100,
    )
    return llm


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """隔离 DuckDB factor_catalog。"""
    from fts.factor_engine.factor_db import schema

    isolated_db = tmp_path / "factor_catalog.duckdb"
    schema.init_database(isolated_db)
    monkeypatch.setattr(schema, "DATABASE_PATH", isolated_db)


# ─── 1. read_failures_by_parent ───────────────────────────


class TestReadFailuresByParent:
    def test_filters_by_parent_id(self, tmp_path):
        """按 parent_id 过滤：仅返回匹配父因子的失败轨迹。"""
        chain = ExperienceChain(tmp_path / "evo")
        chain.record_failure(_failure_trace("t1", "parent_a", "fct_1", ["IC 过低"]))
        chain.record_failure(_failure_trace("t2", "parent_a", "fct_2", ["换手率过高"]))
        chain.record_failure(_failure_trace("t3", "parent_b", "fct_3", ["多重检验未通过"]))

        result = chain.read_failures_by_parent("parent_a")

        assert len(result) == 2
        assert all(t.get("parent_id") == "parent_a" for t in result)
        assert {t.get("factor_id") for t in result} == {"fct_1", "fct_2"}

    def test_limit_applied(self, tmp_path):
        """limit 生效：返回最近 limit 条。"""
        chain = ExperienceChain(tmp_path / "evo")
        for i in range(5):
            chain.record_failure(_failure_trace(f"t{i}", "parent_a", f"fct_{i}", ["IC 过低"]))

        result = chain.read_failures_by_parent("parent_a", limit=2)

        assert len(result) == 2

    def test_empty_when_no_match(self, tmp_path):
        """无匹配父因子的失败记录 → []。"""
        chain = ExperienceChain(tmp_path / "evo")
        chain.record_failure(_failure_trace("t1", "parent_a", "fct_1", ["IC 过低"]))

        assert chain.read_failures_by_parent("parent_zzz") == []
        assert chain.read_failures_by_parent("") == []

    def test_empty_chain_returns_empty(self, tmp_path):
        """空经验链 → []。"""
        chain = ExperienceChain(tmp_path / "evo")
        assert chain.read_failures_by_parent("parent_a") == []


# ─── 2. MacroEvolver prompt 注入 ──────────────────────────


class TestMacroEvolverFailureGuidance:
    def test_prompt_injects_failure_guidance(self):
        """有 parent_failure_ctx → prompt 含失败归因段落与定向修复要求。"""
        evolver = MacroEvolver(llm_client=_mock_llm_ok(), experience_chain=None)
        parent = _parent_factor()
        from fts.factor_engine.experience_chain import ParentFailureContext

        ctx = ParentFailureContext(
            parent_id="fct_parent",
            failure_reasons=["换手率过高", "IC 过低"],
            patterns=["换手率过高", "IC 过低"],
            latest_failed_at="2026-08-11T00:00:00",
        )

        evolver.evolve(parent, generation=1, parent_failure_ctx=ctx)

        prompt = evolver.llm.complete.call_args.args[0]
        assert "父因子最近失败归因" in prompt
        assert "换手率过高" in prompt
        assert "IC 过低" in prompt
        assert "定向修复要求" in prompt

    def test_prompt_without_ctx_unchanged(self):
        """无 parent_failure_ctx → prompt 不含失败归因段落（现有行为回归）。"""
        evolver = MacroEvolver(llm_client=_mock_llm_ok(), experience_chain=None)
        parent = _parent_factor()

        evolver.evolve(parent, generation=1)

        prompt = evolver.llm.complete.call_args.args[0]
        assert "父因子最近失败归因" not in prompt
        assert "定向修复要求" not in prompt
        # 现有关键段落保留
        assert "失败模式聚类分析" in prompt
        assert "增量创新" in prompt

    def test_evolve_with_ctx_returns_factor(self):
        """带 ctx 的 evolve 仍正常产出新因子。"""
        evolver = MacroEvolver(llm_client=_mock_llm_ok(), experience_chain=None)
        parent = _parent_factor()
        from fts.factor_engine.experience_chain import ParentFailureContext

        ctx = ParentFailureContext(parent_id="fct_parent", failure_reasons=["IC 过低"], patterns=["IC 过低"])

        new_factor, summary, tokens = evolver.evolve(parent, generation=1, parent_failure_ctx=ctx)

        assert new_factor.get("factor_id")
        assert new_factor.get("parent_id") == "fct_parent"
        assert tokens == 100


# ─── 3. EvolutionLoop._evolve_one 传递 ────────────────────


class TestEvolveOnePassesFailureContext:
    def test_macro_branch_passes_ctx(self, minimal_loop):
        """macro 演化分支读取父因子失败归因并传给 evolver。"""
        from fts.factor_engine.experience_chain import ParentFailureContext

        # mock 经验链读取
        minimal_loop.experience_chain.read_failures_by_parent = MagicMock(
            return_value=[_failure_trace("t1", "fct_parent", "fct_1", ["换手率过高"])]
        )
        # mock macro evolver 捕获参数
        mock_evolve = MagicMock(
            return_value=(dict(_parent_factor()), "summary", 0)
        )
        minimal_loop.macro_evolver.evolve = mock_evolve

        parent = _parent_factor()
        minimal_loop._evolve_one(parent, generation=1, trace_id="trace_x", method_hint="macro")

        # evolver 收到非空 parent_failure_ctx
        kwargs = mock_evolve.call_args.kwargs
        assert kwargs.get("parent_failure_ctx") is not None
        ctx = kwargs["parent_failure_ctx"]
        assert isinstance(ctx, ParentFailureContext)
        assert ctx.parent_id == "fct_parent"
        assert "换手率过高" in ctx.failure_reasons
        # 读取接口被调用
        minimal_loop.experience_chain.read_failures_by_parent.assert_called_once_with("fct_parent")

    def test_macro_branch_no_failure_records(self, minimal_loop):
        """无失败记录 → 传递 None，evolve 照常调用。"""
        minimal_loop.experience_chain.read_failures_by_parent = MagicMock(return_value=[])
        mock_evolve = MagicMock(return_value=(dict(_parent_factor()), "summary", 0))
        minimal_loop.macro_evolver.evolve = mock_evolve

        parent = _parent_factor()
        minimal_loop._evolve_one(parent, generation=1, trace_id="trace_x", method_hint="macro")

        kwargs = mock_evolve.call_args.kwargs
        assert kwargs.get("parent_failure_ctx") is None
