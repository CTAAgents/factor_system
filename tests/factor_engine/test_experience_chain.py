"""tests/factor_engine/test_experience_chain.py — 经验链存储测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fts.factor_engine.contracts import (
    BacktestMetrics,
    EconomicScore,
    FactorEvaluation,
    ExperienceTrace,
    MultipleTestResult,
)
from fts.factor_engine.experience_chain import (
    MAX_CHAIN_SIZE,
    ExperienceChain,
    ExperienceChainError,
    create_trace_from_evaluation,
)


# ─── 工厂函数 ─────────────────────────────────────────────

def make_evaluation(passed: bool = True, ic: float = 0.05) -> FactorEvaluation:
    return FactorEvaluation(
        factor_id="fct_test",
        trace_id="l2_t",
        level_1_backtest=BacktestMetrics(
            ic=ic, icir=0.8, sharpe=2.0, max_drawdown=0.1,
            monotonicity=True, oos_ratio=0.4, t_stat=3.5, turnover_monthly=0.3,
        ),
        level_2_economic=EconomicScore(
            theory=4, behavioral=3, microstructure=4, institutional=5,
            dimensions_passed=4, narrative="测试",
        ),
        level_3_multiple=MultipleTestResult(
            bonferroni_p=0.005, fdr_q=0.03, effective_n_factors=8,
            adjusted_t=3.2, passed=True,
        ),
        passed=passed,
        failure_reasons=[] if passed else ["测试失败原因"],
        evaluated_at="2026-07-18T00:00:00",
    )


def make_trace(success: bool = True, trace_id: str = "exp_001") -> ExperienceTrace:
    return ExperienceTrace(
        trace_id=trace_id,
        factor_id="fct_test",
        parent_id=None,
        generation=1,
        mutation_type="macro_logic",
        mutation_summary="测试变异",
        evaluation=make_evaluation(passed=success),
        success=success,
        lessons=["测试教训"],
        recorded_at="2026-07-18T00:00:00",
    )


# ─── 存储与读取 ───────────────────────────────────────────

def test_record_success(tmp_memory_dir):
    chain = ExperienceChain(tmp_memory_dir)
    trace = make_trace(success=True)
    fp = chain.record_success(trace)
    assert fp.exists()
    assert fp.suffix == ".json"


def test_record_failure(tmp_memory_dir):
    chain = ExperienceChain(tmp_memory_dir)
    trace = make_trace(success=False)
    fp = chain.record_failure(trace)
    assert fp.exists()


def test_record_success_rejects_failure_trace(tmp_memory_dir):
    chain = ExperienceChain(tmp_memory_dir)
    trace = make_trace(success=False)
    with pytest.raises(ExperienceChainError):
        chain.record_success(trace)


def test_record_failure_rejects_success_trace(tmp_memory_dir):
    chain = ExperienceChain(tmp_memory_dir)
    trace = make_trace(success=True)
    with pytest.raises(ExperienceChainError):
        chain.record_failure(trace)


def test_record_rejects_empty_summary(tmp_memory_dir):
    chain = ExperienceChain(tmp_memory_dir)
    trace = make_trace(success=True)
    trace["mutation_summary"] = ""
    with pytest.raises(ExperienceChainError):
        chain.record_success(trace)


def test_record_rejects_empty_trace_id(tmp_memory_dir):
    chain = ExperienceChain(tmp_memory_dir)
    trace = make_trace(success=True)
    trace["trace_id"] = ""
    with pytest.raises(ExperienceChainError):
        chain.record_success(trace)


def test_record_failure_requires_failure_reasons(tmp_memory_dir):
    """失败轨迹的 evaluation.failure_reasons 不能为空。"""
    chain = ExperienceChain(tmp_memory_dir)
    trace = make_trace(success=False)
    trace["evaluation"]["failure_reasons"] = []
    with pytest.raises(ExperienceChainError):
        chain.record_failure(trace)


def test_read_recent_for_llm(tmp_memory_dir):
    chain = ExperienceChain(tmp_memory_dir)
    # 写入 5 条成功 + 3 条失败
    for i in range(5):
        chain.record_success(make_trace(success=True, trace_id=f"s_{i}"))
    for i in range(3):
        chain.record_failure(make_trace(success=False, trace_id=f"f_{i}"))

    recent = chain.read_recent_for_llm()
    assert len(recent["success"]) == 5
    assert len(recent["failure"]) == 3


def test_read_recent_limits_to_10(tmp_memory_dir):
    chain = ExperienceChain(tmp_memory_dir)
    # 写入 15 条成功
    for i in range(15):
        chain.record_success(make_trace(success=True, trace_id=f"s_{i}"))

    recent = chain.read_recent_for_llm()
    assert len(recent["success"]) == 10  # 上限


def test_count(tmp_memory_dir):
    chain = ExperienceChain(tmp_memory_dir)
    chain.record_success(make_trace(success=True, trace_id="s_1"))
    chain.record_success(make_trace(success=True, trace_id="s_2"))
    chain.record_failure(make_trace(success=False, trace_id="f_1"))

    c = chain.count()
    assert c["success"] == 2
    assert c["failure"] == 1
    assert c["total"] == 3


def test_create_trace_from_evaluation():
    ev = make_evaluation(passed=True)
    trace = create_trace_from_evaluation(
        factor_id="fct_1",
        parent_id="fct_0",
        generation=2,
        mutation_type="macro_logic",
        mutation_summary="测试",
        evaluation=ev,
        lessons=["教训1"],
    )
    assert trace["success"] is True
    assert trace["factor_id"] == "fct_1"
    assert trace["parent_id"] == "fct_0"


def test_cleanup_when_over_limit(tmp_memory_dir):
    """超过 MAX_CHAIN_SIZE 时应淘汰最旧的 20 条。"""
    chain = ExperienceChain(tmp_memory_dir)
    # 写入 105 条
    import time
    for i in range(105):
        chain.record_success(make_trace(success=True, trace_id=f"s_{i:03d}"))
        time.sleep(0.001)  # 确保 mtime 不同

    deleted = chain.cleanup_if_needed()
    assert deleted == 20  # 淘汰 20 条
    c = chain.count()
    assert c["total"] == 85  # 105 - 20


def test_update_summary(tmp_memory_dir):
    """应能生成 markdown 摘要文件。"""
    chain = ExperienceChain(tmp_memory_dir)
    chain.record_success(make_trace(success=True, trace_id="s_1"))
    chain.record_failure(make_trace(success=False, trace_id="f_1"))

    summary_path = chain.update_summary()
    assert summary_path.exists()
    content = summary_path.read_text(encoding="utf-8")
    assert "经验链摘要" in content
    assert "fct_test" in content


# ─── 覆盖遗漏行 ───────────────────────────────────────────

class TestCoverageGaps:
    """覆盖遗漏行 (98, 102, 130-131, 138-139, 182, 223-224)。"""

    def test_read_all_success(self, tmp_memory_dir):
        """line 98: read_all_success 应返回所有成功轨迹。"""
        chain = ExperienceChain(tmp_memory_dir)
        chain.record_success(make_trace(success=True, trace_id="s_a"))
        chain.record_success(make_trace(success=True, trace_id="s_b"))
        traces = chain.read_all_success()
        assert len(traces) == 2

    def test_read_all_failure(self, tmp_memory_dir):
        """line 102: read_all_failure 应返回所有失败轨迹。"""
        chain = ExperienceChain(tmp_memory_dir)
        chain.record_failure(make_trace(success=False, trace_id="f_a"))
        chain.record_failure(make_trace(success=False, trace_id="f_b"))
        traces = chain.read_all_failure()
        assert len(traces) == 2

    def test_cleanup_oserror_on_stat(self, tmp_memory_dir, monkeypatch):
        """lines 130-131: cleanup 时 stat 失败应静默跳过。"""
        import os
        chain = ExperienceChain(tmp_memory_dir)
        chain.record_success(make_trace(success=True, trace_id="s_oserr"))
        chain.record_success(make_trace(success=True, trace_id="s_oserr2"))

        # 让 stat 抛出 OSError
        original_stat = os.stat

        def broken_stat(path, *args, **kwargs):
            if "s_oserr" in str(path):
                raise OSError("stat 失败")
            return original_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", broken_stat)
        deleted = chain.cleanup_if_needed()
        assert deleted >= 0  # 不应抛异常

    def test_cleanup_oserror_on_unlink(self, tmp_memory_dir, monkeypatch):
        """lines 138-139: cleanup 时 unlink 失败应静默跳过。"""
        chain = ExperienceChain(tmp_memory_dir)
        for i in range(105):
            chain.record_success(make_trace(success=True, trace_id=f"s_unlink_{i:03d}"))

        original_unlink = Path.unlink

        def broken_unlink(self, *args, **kwargs):
            if "s_unlink" in str(self):
                raise OSError("unlink 失败")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", broken_unlink)
        deleted = chain.cleanup_if_needed()
        # cleanup 应继续执行不抛异常
        assert deleted >= 0

    def test_validate_fails_empty_factor_id(self, tmp_memory_dir):
        """line 182: factor_id 为空应抛出异常。"""
        chain = ExperienceChain(tmp_memory_dir)
        trace = make_trace(success=True, trace_id="no_fid")
        trace["factor_id"] = ""
        with pytest.raises(ExperienceChainError, match="factor_id 不能为空"):
            chain.record_success(trace)

    def test_read_dir_skip_corrupt_json(self, tmp_memory_dir):
        """lines 223-224: _read_dir 遇到损坏 JSON 应跳过。"""
        chain = ExperienceChain(tmp_memory_dir)
        # 写入一个有效轨迹
        chain.record_success(make_trace(success=True, trace_id="valid_trace"))
        # 写入一个无效文件
        bad_file = chain.success_dir / "corrupt.json"
        bad_file.write_text("not valid json", encoding="utf-8")

        traces = chain.read_all_success()
        assert len(traces) == 1
        assert traces[0]["trace_id"] == "valid_trace"
