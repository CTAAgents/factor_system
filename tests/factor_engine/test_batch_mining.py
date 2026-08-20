"""tests/factor_engine/test_batch_mining.py — 批量挖掘漏斗单元测试（GAP-I201）。

设计文档: docs/archive/design/D.1-batch-mining-design.md §8 验收标准。
"""

from __future__ import annotations

from unittest.mock import MagicMock


from fts.factor_engine.batch_mining import (
    BatchGenerationResult,
    BatchMiner,
    BatchMiningConfig,
)


def _make_proposal(factor_id: str = "fct_abcd1234") -> dict:
    """构造一个最小合法 BatchedProposal。"""
    return {
        "factor": {
            "factor_id": factor_id,
            "name": f"f_{factor_id}",
            "code": "close - close.shift(1)",
            "params": {},
        },
        "parent_id": "parent_1",
        "method": "gp_evolution",
        "summary": "GP Gen=1",
        "tokens": 0,
        "prefilter_ok": False,
        "prefilter_reason": "",
        "prefilter_ic": 0.0,
    }


def _always_pass_runtime(factor) -> tuple[bool, str]:
    return True, ""


# ── generate_batch ────────────────────────────────────────


def test_generate_batch_returns_batch_size():
    """验收 1: generate_batch 返回 ≤ batch_size 个合法后代。"""
    cfg = BatchMiningConfig(batch_size=10)
    gen = MagicMock(return_value=_make_proposal())
    miner = BatchMiner(config=cfg, generate_cb=gen)
    proposals = miner.generate_batch({"factor_id": "p"}, 1, "t")
    assert len(proposals) == 10
    assert gen.call_count == 10


def test_generate_batch_without_cb_returns_empty():
    """generate_cb 未注入时返回空批（容错）。"""
    miner = BatchMiner(config=BatchMiningConfig(batch_size=5))
    assert miner.generate_batch({"factor_id": "p"}, 1, "t") == []


def test_generate_batch_skips_none():
    """生成失败（None）的候选不计入批次。"""
    cfg = BatchMiningConfig(batch_size=5)
    gen = MagicMock(side_effect=[None, _make_proposal(), None, _make_proposal(), None])
    miner = BatchMiner(config=cfg, generate_cb=gen)
    proposals = miner.generate_batch({"factor_id": "p"}, 1, "t")
    assert len(proposals) == 2


# ── filter_batch ──────────────────────────────────────────


def test_filter_batch_all_pass():
    """并行过滤：全部通过时 passed 与输入一致。"""
    cfg = BatchMiningConfig(max_workers=4, max_candidates=8)
    props = [_make_proposal(f"f{i}") for i in range(8)]
    miner = BatchMiner(
        config=cfg,
        runtime_check_cb=_always_pass_runtime,
        prefilter_cb=lambda f, t: (True, "", 0.05),
    )
    result = miner.filter_batch(props, "t")
    assert result.total_passed == 8
    assert result.total_rejected == 0
    assert all(p["prefilter_ic"] == 0.05 for p in result.passed)


def test_filter_batch_truncates_by_ic_desc():
    """验收 3: passed 截断 ≤ max_candidates 且按 prefilter_ic 降序。"""
    cfg = BatchMiningConfig(max_candidates=3)
    props = [_make_proposal(f"f{i}") for i in range(5)]
    miner = BatchMiner(
        config=cfg,
        runtime_check_cb=_always_pass_runtime,
        # IC 从因子名尾字符解析: f4 → 0.04 ... f0 → 0.00
        prefilter_cb=lambda f, t: (True, "", int(f["factor_id"][-1]) / 100.0),
    )
    result = miner.filter_batch(props, "t")
    assert result.total_passed == 3
    assert result.total_rejected == 2
    ics = [p["prefilter_ic"] for p in result.passed]
    assert ics == sorted(ics, reverse=True)
    # 被截断项进 rejected 且带截断标记（计数一致: 5 = 3 + 2）
    truncated_marks = [r for r in result.rejected if "截断" in r["prefilter_reason"]]
    assert len(truncated_marks) == 2


def test_filter_batch_rejects_runtime_failure():
    """运行时校验失败进入 rejected 并记录原因。"""
    cfg = BatchMiningConfig()
    props = [_make_proposal("f0"), _make_proposal("f1")]
    miner = BatchMiner(
        config=cfg,
        runtime_check_cb=lambda f: (False, "broadcast error"),
        prefilter_cb=lambda f, t: (True, "", 0.05),
    )
    result = miner.filter_batch(props, "t")
    assert result.total_passed == 0
    assert result.total_rejected == 2
    assert "运行时校验失败" in result.rejected[0]["prefilter_reason"]


def test_filter_batch_rejects_prefilter_failure():
    """预筛失败进入 rejected 并记录原因。"""
    cfg = BatchMiningConfig()
    props = [_make_proposal("f0")]
    miner = BatchMiner(
        config=cfg,
        runtime_check_cb=_always_pass_runtime,
        prefilter_cb=lambda f, t: (False, "IC 过低", 0.0),
    )
    result = miner.filter_batch(props, "t")
    assert result.total_passed == 0
    assert "IC 过低" in result.rejected[0]["prefilter_reason"]


def test_filter_batch_single_proposal():
    """单候选路径（并行线程数为 1）与多候选结果一致。"""
    cfg = BatchMiningConfig(max_workers=1)
    props = [_make_proposal("f0")]
    miner = BatchMiner(
        config=cfg,
        runtime_check_cb=_always_pass_runtime,
        prefilter_cb=lambda f, t: (True, "", 0.03),
    )
    result = miner.filter_batch(props, "t")
    assert result.total_passed == 1
    assert result.passed[0]["prefilter_ic"] == 0.03


def test_filter_batch_tokens_accumulated():
    """tokens 消耗按全部候选求和（含被拦截者）。"""
    cfg = BatchMiningConfig(max_candidates=10)
    props = [{**_make_proposal(f"f{i}"), "tokens": 10} for i in range(4)]
    miner = BatchMiner(
        config=cfg,
        runtime_check_cb=_always_pass_runtime,
        prefilter_cb=lambda f, t: (True, "", 0.02),
    )
    result = miner.filter_batch(props, "t")
    assert result.tokens_consumed == 40


# ── run_iteration ─────────────────────────────────────────


def test_run_iteration_end_to_end():
    """验收: 一代完整漏斗（生成→过滤）并标记 generation。"""
    cfg = BatchMiningConfig(batch_size=4, max_candidates=2)
    gen = MagicMock(
        side_effect=[
            _make_proposal("f0"),
            None,
            _make_proposal("f2"),
            _make_proposal("f3"),
        ]
    )
    miner = BatchMiner(
        config=cfg,
        generate_cb=gen,
        runtime_check_cb=_always_pass_runtime,
        prefilter_cb=lambda f, t: (True, "", 0.08),
    )
    result = miner.run_iteration({"factor_id": "p"}, generation=3, trace_id="t")
    assert isinstance(result, BatchGenerationResult)
    assert result.generation == 3
    assert result.total_generated == 3
    assert result.total_passed == 2
    assert result.duration_ms >= 0


def test_run_iteration_all_rejected():
    """全部被拦截时 passed 为空。"""
    cfg = BatchMiningConfig(batch_size=3, max_candidates=2)
    gen = MagicMock(return_value=_make_proposal())
    miner = BatchMiner(
        config=cfg,
        generate_cb=gen,
        runtime_check_cb=_always_pass_runtime,
        prefilter_cb=lambda f, t: (False, "常数信号", 0.0),
    )
    result = miner.run_iteration({"factor_id": "p"}, 1, "t")
    assert result.total_passed == 0
    assert result.total_rejected == 3
