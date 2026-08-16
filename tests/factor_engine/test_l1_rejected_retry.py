"""
tests/factor_engine/test_l1_rejected_retry.py — plans/44 C2: L1 拒绝候选复活

覆盖:
    - 编译失败候选经规则/LLM 修复后复活注入，rejected 文件移走
    - 非编译失败拒绝候选跳过
    - 修复仍失败候选保留
    - l1_rejected_retry 配置关闭 → 跳过
    - rejected 目录不存在 → 返回 0
    - 目录损坏文件解析失败跳过不阻断

版本: v1.0.0（与 FTS 同步）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.contracts import EconomicLogic, L1MetaLoopState  # noqa: E402
from fts.factor_engine.meta_loop import MetaLoop  # noqa: E402


@pytest.fixture
def tmp_meta_dir(tmp_path) -> Path:
    p = tmp_path / "meta_loop"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_state_store(tmp_path):
    from fts.store.state_db import StateKVStore

    store = StateKVStore(tmp_path / "state.duckdb")
    yield store
    store.close()


@pytest.fixture
def tmp_factor_pool_path(tmp_path) -> Path:
    p = tmp_path / "factors" / "factor_pool.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_inject_dir(tmp_path) -> Path:
    p = tmp_path / "l1_injected"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def tmp_debates_dir(tmp_path) -> Path:
    p = tmp_path / "debates"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _make_rejected_record(candidate_id: str = "cand_rej_compile") -> dict:
    """构造编译失败拒绝候选记录（对应 _persist_rejected 落盘格式）。"""
    return {
        "candidate_id": candidate_id,
        "name": "fut_compile_rejected",
        "code": "def factor_program(data, params):\n    return x @@@\n",
        "params": {},
        "signature": {
            "input_fields": ["close"],
            "output_type": "signal",
            "frequency": "daily",
            "lookback": 1,
        },
        "economic_logic": EconomicLogic(
            theory=4,
            behavioral=4,
            microstructure=4,
            institutional=4,
            narrative="该因子具备充分的经济逻辑论证，满足长度要求。",
        ),
        "source": "l1_bootstrapping",
        "parent_topic": "复活测试",
        "is_executable": False,
        "is_duplicate": False,
        "passed_l1_verifier": False,
        "failure_reasons": ["编译失败: 语法错误: invalid syntax (line 2)"],
        "trace_id": "t",
        "created_at": "2026-08-16",
        "market": "futures",
        "l1_rejection": {
            "reasons": ["编译失败: 语法错误: invalid syntax (line 2)"],
            "rejected_at": "2026-08-16T22:00:00",
            "trace_id": "t",
        },
    }


def _make_state() -> L1MetaLoopState:
    return L1MetaLoopState(
        run_id="test",
        started_at="",
        status="running",
        tokens_consumed=0,
        budget_limit=50000,
        total_candidates_generated=0,
        total_candidates_injected=0,
    )


class TestL1RejectedRetry:
    """拒绝候选复活（plans/44 C2）。"""

    def test_no_rejected_dir_returns_zero(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """rejected 目录不存在 → 0。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
        )
        assert not loop.rejected_dir.exists()
        assert loop._retry_rejected_candidates(_make_state(), "t", []) == 0

    def test_config_disabled_skips(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store, monkeypatch
    ):
        """l1_rejected_retry 关闭 → 跳过（即使目录存在）。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
        )
        loop.rejected_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            "fts.config.settings.get_config",
            lambda: SimpleNamespace(l1_rejected_retry=False),
        )
        assert loop._retry_rejected_candidates(_make_state(), "t", []) == 0

    def test_compile_fail_revived_and_injected(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """编译失败候选经 LLM 修复后复活注入，rejected 文件移走。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
            llm_client=MagicMock(),  # 修复代码走 _try_revive_code → 规则失败 → LLM(MagicMock)
        )
        # 规则修复必然失败（@@@），LLM 走 MagicMock.fix_factor_code 默认返回 MagicMock（非 str）→ None？
        # 为验证"复活注入"，改用真实 MockLLMClient（fix_factor_code 返回有效代码）
        from fts.llm import MockLLMClient

        loop.llm_client = MockLLMClient()
        rec = _make_rejected_record()
        loop.rejected_dir.mkdir(parents=True, exist_ok=True)
        (loop.rejected_dir / f"{rec['candidate_id']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, default=str), encoding="utf-8"
        )

        state = _make_state()
        injected_ids: list[str] = []
        retried = loop._retry_rejected_candidates(state, "t", injected_ids)
        assert retried == 1, "编译失败候选应复活注入"
        assert injected_ids == ["cand_rej_compile"]
        assert state["total_candidates_injected"] == 1
        # rejected 文件已移走
        assert not (loop.rejected_dir / "cand_rej_compile.json").exists()
        # 注入目录已有产物
        assert (loop.inject_dir / "cand_rej_compile.json").exists()

    def test_non_compile_rejection_skipped(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """非编译失败拒绝候选（如经济逻辑）跳过。"""
        from fts.llm import MockLLMClient

        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
            llm_client=MockLLMClient(),
        )
        rec = _make_rejected_record(candidate_id="cand_econ_rejected")
        rec["l1_rejection"]["reasons"] = ["经济逻辑达标维度 1/4 < 2"]
        rec["failure_reasons"] = ["经济逻辑达标维度 1/4 < 2"]
        loop.rejected_dir.mkdir(parents=True, exist_ok=True)
        (loop.rejected_dir / f"{rec['candidate_id']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, default=str), encoding="utf-8"
        )
        assert loop._retry_rejected_candidates(_make_state(), "t", []) == 0
        assert (loop.rejected_dir / "cand_econ_rejected.json").exists(), "非编译类应保留"

    def test_llm_fix_still_fails_kept(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """LLM 修复仍失败 → 候选保留不注入。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
            llm_client=MagicMock(),  # fix_factor_code 返回 MagicMock（非 str）→ _try_revive_code 判无效
        )
        rec = _make_rejected_record(candidate_id="cand_unfixable")
        loop.rejected_dir.mkdir(parents=True, exist_ok=True)
        (loop.rejected_dir / f"{rec['candidate_id']}.json").write_text(
            json.dumps(rec, ensure_ascii=False, default=str), encoding="utf-8"
        )
        assert loop._retry_rejected_candidates(_make_state(), "t", []) == 0
        assert (loop.rejected_dir / "cand_unfixable.json").exists(), "修复失败应保留"

    def test_corrupt_file_skipped(
        self, tmp_meta_dir, tmp_factor_pool_path, tmp_inject_dir, tmp_debates_dir, tmp_state_store
    ):
        """损坏 JSON 跳过不阻断。"""
        loop = MetaLoop(
            memory_dir=tmp_meta_dir,
            factor_pool_path=tmp_factor_pool_path,
            inject_dir=tmp_inject_dir,
            debates_dir=tmp_debates_dir,
            state_store=tmp_state_store,
        )
        loop.rejected_dir.mkdir(parents=True, exist_ok=True)
        (loop.rejected_dir / "cand_broken.json").write_text("{ not json", encoding="utf-8")
        assert loop._retry_rejected_candidates(_make_state(), "t", []) == 0
