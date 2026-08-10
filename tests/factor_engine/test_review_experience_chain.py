"""tests/factor_engine/test_review_experience_chain.py — 审查意见接入经验链测试（GAP-I102 二期，v2.80.0）。

覆盖:
    1. reject + comment → 失败轨迹写入经验链（success=False + failure_reasons + lessons）
    2. approve → 不写入经验链
    3. reject 空 comment → 不写入经验链
    4. 开关关闭（FTS_REVIEW_EXPERIENCE_CHAIN=0）→ 不写入
    5. 经验链写入异常 → 降级不阻断审查流程
"""

from __future__ import annotations

import pytest

from fts.factor_engine.experience_chain import ExperienceChain
from fts.factor_engine.factor_inspector import FactorReviewWorkflow


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """隔离 DuckDB factor_catalog，防污染真实库（同 test_review_workflow.py）。"""
    from fts.factor_engine.factor_db import schema

    isolated_db = tmp_path / "factor_catalog.duckdb"
    schema.init_database(isolated_db)
    monkeypatch.setattr(schema, "DATABASE_PATH", isolated_db)
    return str(isolated_db)


def _insert_factor(conn, factor_id: str, name: str, market: str = "futures") -> None:
    """向隔离 factor_catalog 插入一条因子。"""
    conn.execute(
        "INSERT INTO factor_catalog (factor_id, name, code, code_hash, "
        "economic_logic, source, market, ic, sharpe) "
        "VALUES (?, ?, 'def f(): pass', 'h', '{}', 'seed', ?, 0.05, 1.5)",
        [factor_id, name, market],
    )


@pytest.fixture
def isolated_db(_isolate_factor_db):
    import duckdb

    conn = duckdb.connect(_isolate_factor_db)
    try:
        _insert_factor(conn, "fct_rev_x", "review_x", market="futures")
        _insert_factor(conn, "fct_rev_y", "review_y", market="stock")
    finally:
        conn.close()
    return _isolate_factor_db


@pytest.fixture
def exp_chain(tmp_path) -> ExperienceChain:
    """隔离经验链实例（tmp 目录，防污染真实 memory/evolution）。"""
    return ExperienceChain(memory_dir=str(tmp_path / "evolution"))


def _make_workflow(isolated_db, exp_chain) -> FactorReviewWorkflow:
    return FactorReviewWorkflow(db_path=isolated_db, experience_chain=exp_chain)


class TestReviewExperienceChain:
    """GAP-I102 二期: 审查意见接入经验链闭环。"""

    def test_reject_writes_failure_trace(self, isolated_db, exp_chain):
        """reject + comment → 经验链失败轨迹计数 +1。"""
        wf = _make_workflow(isolated_db, exp_chain)
        wf.reject("fct_rev_x", comment="经济逻辑存疑，数据依据不足")
        stats = exp_chain.count()
        assert stats["failure"] == 1
        assert stats["success"] == 0
        failures = exp_chain.read_all_failure()
        assert len(failures) == 1
        tr = failures[0]
        assert tr["factor_id"] == "fct_rev_x"
        assert tr["success"] is False
        assert "经济逻辑存疑" in tr["mutation_summary"]
        assert any("经济逻辑存疑" in r for r in tr["evaluation"]["failure_reasons"])
        assert any("经济逻辑存疑" in lesson for lesson in tr["lessons"])

    def test_approve_does_not_write(self, isolated_db, exp_chain):
        """approve → 不写经验链。"""
        wf = _make_workflow(isolated_db, exp_chain)
        wf.approve("fct_rev_y", comment="经济逻辑成立，放行")
        assert exp_chain.count()["total"] == 0

    def test_reject_empty_comment_does_not_write(self, isolated_db, exp_chain):
        """reject 但 comment 为空 → 不写经验链。"""
        wf = _make_workflow(isolated_db, exp_chain)
        wf.reject("fct_rev_x", comment="   ")
        assert exp_chain.count()["total"] == 0

    def test_reject_switch_off_does_not_write(self, isolated_db, exp_chain, monkeypatch):
        """开关关闭（FTS_REVIEW_EXPERIENCE_CHAIN=0）→ 即使注入 chain 也不写。"""
        monkeypatch.setenv("FTS_REVIEW_EXPERIENCE_CHAIN", "0")
        from fts.factor_engine import factor_inspector

        monkeypatch.setattr(factor_inspector, "_load_review_experience_enabled", lambda: False)
        wf = FactorReviewWorkflow(db_path=isolated_db)  # 不注入 chain，走懒加载（开关关闭→None）
        wf.reject("fct_rev_x", comment="经济逻辑存疑")
        assert exp_chain.count()["total"] == 0

    def test_experience_write_failure_degrades(self, isolated_db, monkeypatch):
        """经验链写入异常 → 降级不阻断审查（决策仍回写成功）。"""
        wf = FactorReviewWorkflow(db_path=isolated_db, experience_chain=None)
        monkeypatch.setattr(wf, "_get_experience_chain", lambda: _BoomChain())
        result = wf.reject("fct_rev_x", comment="存疑驳回")
        assert result["status"] == "ok"
        assert result["decision"] == "rejected"
        # 审查决定本身已落盘
        assert wf.get_status("fct_rev_x")["decision"] == "rejected"

    def test_reject_reviewer_embedded_in_lessons(self, isolated_db, exp_chain):
        """审查人信息写入 lessons（供 LLM 归因上下文）。"""
        wf = _make_workflow(isolated_db, exp_chain)
        wf.reject("fct_rev_y", comment="数据口径存疑", reviewer="alpha-board")
        failures = exp_chain.read_all_failure()
        assert any("alpha-board" in lesson for lesson in failures[0]["lessons"])

    def test_approve_after_reject_no_extra_trace(self, isolated_db, exp_chain):
        """先 reject 再 approve（幂等覆盖）→ 经验链不重复写（仅驳回写 1 条）。"""
        wf = _make_workflow(isolated_db, exp_chain)
        wf.reject("fct_rev_x", comment="存疑驳回")
        wf.approve("fct_rev_x", comment="复核通过")
        assert exp_chain.count()["failure"] == 1
        assert exp_chain.count()["total"] == 1


class _BoomChain:
    """模拟经验链写入异常。"""

    def record_failure(self, trace):
        raise RuntimeError("disk full")
