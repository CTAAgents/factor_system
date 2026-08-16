"""
tests/factor_engine/test_meta_loop_phase3.py — plans/44 Phase 3 C4/B1/C3 bootstrap 优化测试

覆盖:
    - C4 语义去重：dedup_semantic 拦截候选 → is_duplicate 置位（monkeypatch module-level）
    - B1 负面样本：bootstrap 注入 negative_factor_names（≤20）→ prompt 含负面样本段
    - C3 narrative 补全：bootstrap LLM 候选 narrative <20 字 → 模板补全

版本: v1.0.0（与 FTS 同步）
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.contracts import (  # noqa: E402
    EconomicLogic,
    FactorSignature,
    SeedCandidate,
)
from fts.factor_engine.meta_loop import BootstrappingChain  # noqa: E402
from fts.factor_engine.seed_pool import SeedPool  # noqa: E402
from fts.llm import OpenAIClient  # noqa: E402


def _candidate(name: str, narrative: str = "这是一个有效测试因子，捕捉量价与经济逻辑机制传导路径。") -> SeedCandidate:
    return SeedCandidate(
        candidate_id=f"cand_{name}",
        name=name,
        code="def factor_program(data, params):\n    import numpy as np\n    return np.zeros(len(data['close']))\n",
        params={},
        signature=FactorSignature(
            input_fields=["close"],
            output_type="signal",
            frequency="daily",
            lookback=1,
        ),
        economic_logic=EconomicLogic(
            theory=4,
            behavioral=4,
            microstructure=4,
            institutional=4,
            narrative=narrative,
        ),
        source="l1_bootstrapping",
        parent_topic="测试",
        trace_id="t",
        created_at="2026-07-18T00:00:00",
    )


class TestC4SemanticDedup:
    """C4 语义去重接入 bootstrap。"""

    def test_semantic_duplicate_blocks_candidate(self, monkeypatch) -> None:
        """dedup_semantic 判重的候选被置 is_duplicate=True。"""
        from fts.factor_engine import meta_loop as ml

        captured: dict = {}
        monkeypatch.setattr(
            ml,
            "dedup_semantic",
            lambda cand_texts, existing_texts, threshold=0.90, embedder=None: (
                captured.update({"cand_texts": cand_texts, "existing_texts": existing_texts}) or [True, False]
            ),
        )
        llm = MagicMock()
        llm.bootstrap_factors.return_value = [
            _candidate("fut_semantic_ok"),
            _candidate("fut_semantic_dup"),
        ]
        chain = BootstrappingChain(llm_client=llm)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=2,
            seed_pool=SeedPool(),
            trace_id="t",
        )
        assert captured["cand_texts"] == ["fut_semantic_ok", "fut_semantic_dup"]
        assert captured["existing_texts"], "负面样本应非空"
        by_name = {c["name"]: c for c in candidates}
        assert by_name["fut_semantic_ok"]["is_duplicate"] is False
        assert by_name["fut_semantic_dup"]["is_duplicate"] is True

    def test_semantic_dedup_disabled_skips(self, monkeypatch) -> None:
        """l1_semantic_dedup=False 时不调用 dedup_semantic（monkeypatch 配置函数）。"""
        from fts.factor_engine import meta_loop as ml

        class _Cfg:
            l1_semantic_dedup = False
            l1_dedup_threshold = 0.9
            default_market = "futures"

        monkeypatch.setattr("fts.config.settings.get_config", lambda: _Cfg())
        monkeypatch.setattr(ml, "dedup_semantic", lambda *a, **k: pytest.fail("不应调用"))
        llm = MagicMock()
        llm.bootstrap_factors.return_value = [_candidate("fut_sem_disabled")]
        chain = BootstrappingChain(llm_client=llm)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=1,
            seed_pool=SeedPool(),
            trace_id="t",
        )
        assert candidates[0]["is_duplicate"] is False


class TestB1NegativeSamples:
    """B1 负面样本注入 prompt。"""

    def test_bootstrap_injects_negative_names(self, monkeypatch) -> None:
        """bootstrap 将已注入因子名（≤20）写入 market_snapshot 传给 LLM。"""
        llm = MagicMock()
        llm.bootstrap_factors.return_value = [_candidate("fut_b1_ok")]
        chain = BootstrappingChain(llm_client=llm)
        snapshot: dict = {}
        chain.bootstrap(
            market_snapshot=snapshot,
            debate_gaps=[],
            max_candidates=1,
            seed_pool=SeedPool(),
            trace_id="t",
        )
        assert "negative_factor_names" in snapshot
        assert len(snapshot["negative_factor_names"]) <= 20
        # 透传到 bootstrap_factors 的 market_snapshot
        passed_snapshot = llm.bootstrap_factors.call_args[0][0]
        assert passed_snapshot["negative_factor_names"] == snapshot["negative_factor_names"]

    def test_prompt_contains_negative_block(self) -> None:
        """_build_bootstrap_prompt 含负面样本段（防机制重复）。"""
        client = OpenAIClient(api_key="test", model="gpt-test")
        prompt = client._build_bootstrap_prompt(  # noqa: SLF001
            market_snapshot={"negative_factor_names": ["fut_momentum_20d", "fut_carry_3m"]},
            debate_gaps=[],
            max_candidates=3,
            trace_id="t",
        )
        assert "【已注入因子（负面样本" in prompt
        assert "fut_momentum_20d" in prompt

    def test_prompt_no_negative_when_absent(self) -> None:
        """无负面样本时 prompt 不含该段。"""
        client = OpenAIClient(api_key="test", model="gpt-test")
        prompt = client._build_bootstrap_prompt(  # noqa: SLF001
            market_snapshot={},
            debate_gaps=[],
            max_candidates=3,
            trace_id="t",
        )
        assert "负面样本" not in prompt


class TestC3NarrativeFill:
    """C3 bootstrap LLM 候选 narrative 补全。"""

    def test_short_narrative_filled(self) -> None:
        """narrative <20 字的 LLM 候选经模板补全至达标。"""
        llm = MagicMock()
        llm.bootstrap_factors.return_value = [_candidate("fut_narr_short", narrative="短")]
        chain = BootstrappingChain(llm_client=llm)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=1,
            seed_pool=SeedPool(),
            trace_id="t",
        )
        assert len(candidates) == 1
        narrative = candidates[0]["economic_logic"]["narrative"]
        assert len(narrative) >= 20
        assert "fut_narr_short因子" in narrative, "模板以因子名开头"

    def test_long_narrative_kept(self) -> None:
        """narrative 已达标时不改动。"""
        llm = MagicMock()
        llm.bootstrap_factors.return_value = [
            _candidate("fut_narr_long", narrative="这是一个足够长的经济学解释，覆盖理论机制与行为偏差传导路径。")
        ]
        chain = BootstrappingChain(llm_client=llm)
        candidates = chain.bootstrap(
            market_snapshot={},
            debate_gaps=[],
            max_candidates=1,
            seed_pool=SeedPool(),
            trace_id="t",
        )
        assert candidates[0]["economic_logic"]["narrative"].startswith("这是一个足够长的经济学解释")
