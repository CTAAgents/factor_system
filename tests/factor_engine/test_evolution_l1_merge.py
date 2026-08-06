"""tests/factor_engine/test_evolution_l1_merge.py — GAP-031 L1→L2 候选合并测试。

覆盖 EvolutionLoop._merge_l1_candidates 的核心逻辑：
- 无注入目录时原样返回
- pending 门控（factor_pool.json status=pending）
- market 过滤（含老文件缺失 market 字段的兼容放行）
- 名称去重
- 幂等（消费后 factor_pool.json pending → injected）
- 已消费标记（injected_to_l2）跳过
"""

from __future__ import annotations

import json

import pytest


# ─── helpers ──────────────────────────────────────────────

def _make_candidate(
    cand_id: str = "cand_abc12345",
    name: str = "l1_test_factor",
    market: str = "futures",
    injected_to_l2: bool = False,
) -> dict:
    """构造 SeedCandidate 字典（契约字段齐全）。"""
    return {
        "candidate_id": cand_id,
        "name": name,
        "code": "close - close.shift(5)",
        "params": {},
        "signature": {"inputs": ["close"], "output": "signal"},
        "economic_logic": {
            "narrative": "测试候选：短期动量",
            "theory": 4, "behavioral": 3, "microstructure": 2, "institutional": 1,
        },
        "source": "l1_bootstrapping",
        "market": market,
        "parent_topic": "test",
        "debate_round_ref": None,
        "debate_gap": None,
        "web_snapshot_ref": None,
        "is_executable": True,
        "is_duplicate": False,
        "passed_l1_verifier": True,
        "failure_reasons": [],
        "trace_id": "trace-test",
        "created_at": "2026-08-06T00:00:00",
        "injected_to_l2": injected_to_l2,
        "injected_at": None,
    }


def _write_pool(tmp_path, factors: list[dict]) -> None:
    """写入 factor_pool.json。"""
    pool_path = tmp_path / "memory" / "knowledge" / "factors" / "factor_pool.json"
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool_path.write_text(
        json.dumps({"version": "v2.12.0", "updated_at": "2026-08-06", "factors": factors}),
        encoding="utf-8",
    )


def _write_candidate(tmp_path, cand: dict) -> None:
    """写入 l1_injected/<candidate_id>.json。"""
    inject_dir = tmp_path / "memory" / "knowledge" / "factors" / "l1_injected"
    inject_dir.mkdir(parents=True, exist_ok=True)
    (inject_dir / f"{cand['candidate_id']}.json").write_text(
        json.dumps(cand, ensure_ascii=False), encoding="utf-8",
    )


def _make_loop(tmp_path, market: str = "futures"):
    """构造 EvolutionLoop 实例（market 可控，副作用隔离到 tmp）。"""
    import numpy as np
    import pandas as pd
    from fts.factor_engine.evolution_loop import EvolutionLoop

    np.random.seed(42)
    n = 120
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    data = pd.DataFrame({
        "open": close, "high": close, "low": close, "close": close,
        "volume": np.random.randint(1000, 10000, n).astype(float),
    }, index=pd.date_range("2024-01-01", periods=n, freq="D"))
    rets = np.zeros(n)
    rets[:-1] = (close[1:] - close[:-1]) / np.maximum(close[:-1], 1e-10)
    return EvolutionLoop(
        data=data,
        forward_returns=rets,
        elite_dir=str(tmp_path / "elite"),
        memory_dir=str(tmp_path / "memory" / "evolution"),
        n_trials_micro=1,
        market=market,
    )


@pytest.fixture
def chdir_tmp(tmp_path, monkeypatch):
    """将 cwd 切到 tmp，使相对路径 memory/knowledge/... 落在 tmp 内。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path


# ─── tests ────────────────────────────────────────────────

def test_no_inject_dir_returns_seeds(chdir_tmp):
    """无 l1_injected 目录时原样返回种子列表。"""
    loop = _make_loop(chdir_tmp)
    seeds = [{"name": "seed_a", "factor_id": "fct_a"}]
    merged = loop._merge_l1_candidates(seeds, "trace-test")  # noqa: SLF001
    assert merged == seeds


def test_merge_pending_candidate(chdir_tmp):
    """pending 候选被合并，source=bootstrapping。"""
    cand = _make_candidate()
    _write_candidate(chdir_tmp, cand)
    _write_pool(chdir_tmp, [{
        "factor_id": cand["candidate_id"], "name": cand["name"],
        "source": "l1_bootstrapping", "status": "pending",
    }])
    loop = _make_loop(chdir_tmp, market="futures")
    merged = loop._merge_l1_candidates([], "trace-test")  # noqa: SLF001
    assert len(merged) == 1
    fp = merged[0]
    assert fp["name"] == cand["name"]
    assert fp["source"] == "bootstrapping"
    assert fp["parent_id"] == cand["candidate_id"]


def test_pending_gate_skips_non_pending(chdir_tmp):
    """pool 中 status != pending 的候选不合并。"""
    cand = _make_candidate()
    _write_candidate(chdir_tmp, cand)
    _write_pool(chdir_tmp, [{
        "factor_id": cand["candidate_id"], "name": cand["name"],
        "source": "l1_bootstrapping", "status": "injected",
    }])
    loop = _make_loop(chdir_tmp)
    merged = loop._merge_l1_candidates([], "trace-test")  # noqa: SLF001
    assert merged == []


def test_market_filter_excludes_other_market(chdir_tmp):
    """候选 market=futures 时，stock 演化循环排除它。"""
    cand = _make_candidate(market="futures")
    _write_candidate(chdir_tmp, cand)
    _write_pool(chdir_tmp, [{
        "factor_id": cand["candidate_id"], "name": cand["name"],
        "source": "l1_bootstrapping", "status": "pending",
    }])
    loop = _make_loop(chdir_tmp, market="stock")
    merged = loop._merge_l1_candidates([], "trace-test")  # noqa: SLF001
    assert merged == []


def test_market_missing_legacy_allowed(chdir_tmp):
    """老文件缺失 market 字段时放行（向后兼容）。"""
    cand = _make_candidate()
    del cand["market"]
    _write_candidate(chdir_tmp, cand)
    _write_pool(chdir_tmp, [{
        "factor_id": cand["candidate_id"], "name": cand["name"],
        "source": "l1_bootstrapping", "status": "pending",
    }])
    loop = _make_loop(chdir_tmp, market="futures")
    merged = loop._merge_l1_candidates([], "trace-test")  # noqa: SLF001
    assert len(merged) == 1


def test_name_dedup_skips_existing_seed(chdir_tmp):
    """与现有种子重名的候选跳过。"""
    cand = _make_candidate(name="dup_factor")
    _write_candidate(chdir_tmp, cand)
    _write_pool(chdir_tmp, [{
        "factor_id": cand["candidate_id"], "name": cand["name"],
        "source": "l1_bootstrapping", "status": "pending",
    }])
    loop = _make_loop(chdir_tmp)
    seeds = [{"name": "dup_factor", "factor_id": "fct_seed"}]
    merged = loop._merge_l1_candidates(seeds, "trace-test")  # noqa: SLF001
    assert len(merged) == 1  # 只有原种子，候选被去重


def test_idempotent_updates_pool_status(chdir_tmp):
    """消费后 factor_pool.json 中候选状态 pending → injected。"""
    cand = _make_candidate()
    _write_candidate(chdir_tmp, cand)
    pool_entry = {
        "factor_id": cand["candidate_id"], "name": cand["name"],
        "source": "l1_bootstrapping", "status": "pending",
    }
    _write_pool(chdir_tmp, [pool_entry])
    loop = _make_loop(chdir_tmp)
    loop._merge_l1_candidates([], "trace-test")  # noqa: SLF001

    pool = json.loads(
        (chdir_tmp / "memory" / "knowledge" / "factors" / "factor_pool.json").read_text(encoding="utf-8")
    )
    entry = pool["factors"][0]
    assert entry["status"] == "injected"


def test_consumed_marker_skipped(chdir_tmp):
    """候选文件带 injected_to_l2=True 时跳过（防重复消费）。"""
    cand = _make_candidate(injected_to_l2=True)
    _write_candidate(chdir_tmp, cand)
    loop = _make_loop(chdir_tmp)
    merged = loop._merge_l1_candidates([], "trace-test")  # noqa: SLF001
    assert merged == []
