"""tests/factor_engine/test_review_workflow.py — GAP-I102 Alpha 审查工作流测试。

覆盖:
    1. 审查状态机: approve/reject 后 get_status 返回正确 decision
    2. 审查意见回写: comment/reviewer 落盘 DuckDB factor_reviews 表
    3. 审查队列: list_pending 排除已审查因子、market 过滤
    4. 幂等 UPSERT: 同因子重复审查覆盖旧决定
"""

from __future__ import annotations


import pytest

from fts.factor_engine.factor_inspector import FactorReviewWorkflow


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """隔离 DuckDB factor_catalog，防污染真实库（同 test_evolution_loop.py）。"""
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
def workflow(_isolate_factor_db) -> FactorReviewWorkflow:
    """隔离库上的审查工作流实例。"""
    import duckdb

    conn = duckdb.connect(_isolate_factor_db)
    try:
        _insert_factor(conn, "fct_rev_a", "review_a", market="futures")
        _insert_factor(conn, "fct_rev_b", "review_b", market="stock")
    finally:
        conn.close()
    return FactorReviewWorkflow(db_path=_isolate_factor_db)


class TestFactorReviewWorkflow:
    """GAP-I102: Alpha 审查工作流（状态机 + 意见回写）。"""

    def test_approve_writes_decision(self, workflow):
        """approve → get_status 返回 approved。"""
        workflow.approve("fct_rev_a", comment="经济逻辑成立，放行")
        status = workflow.get_status("fct_rev_a")
        assert status is not None
        assert status["decision"] == "approved"
        assert status["comment"] == "经济逻辑成立，放行"
        assert status["reviewer"] == "cli"

    def test_reject_writes_decision(self, workflow):
        """reject → get_status 返回 rejected。"""
        workflow.reject("fct_rev_b", comment="经济逻辑存疑，驳回")
        status = workflow.get_status("fct_rev_b")
        assert status["decision"] == "rejected"
        assert status["comment"] == "经济逻辑存疑，驳回"

    def test_get_status_none_for_unreviewed(self, workflow):
        """未审查因子 get_status 返回 None。"""
        assert workflow.get_status("fct_rev_a") is None

    def test_list_pending_excludes_reviewed(self, workflow):
        """审查队列排除已审查因子。"""
        workflow.approve("fct_rev_a")
        queue = workflow.list_pending()
        ids = {f["factor_id"] for f in queue}
        assert "fct_rev_b" in ids
        assert "fct_rev_a" not in ids

    def test_list_pending_market_filter(self, workflow):
        """审查队列 market 过滤。"""
        queue = workflow.list_pending(market="stock")
        assert [f["factor_id"] for f in queue] == ["fct_rev_b"]
        assert all(f["market"] == "stock" for f in queue)

    def test_review_upsert_idempotent(self, workflow):
        """重复审查幂等：reject 覆盖 approve 旧决定。"""
        workflow.approve("fct_rev_a", comment="先批准")
        workflow.reject("fct_rev_a", comment="复核驳回")
        status = workflow.get_status("fct_rev_a")
        assert status["decision"] == "rejected"
        assert status["comment"] == "复核驳回"

    def test_review_comment_reviewer_persisted(self, workflow):
        """审查意见与审查人落盘（comment/reviewer 断言）。"""
        workflow.reject("fct_rev_b", comment="数据依据不足", reviewer="alpha-board")
        status = workflow.get_status("fct_rev_b")
        assert status["reviewer"] == "alpha-board"
        assert "数据依据不足" in status["comment"]


class TestReviewCliCommands:
    """GAP-I102: CLI `factor review list/approve/reject` 命令接线。"""

    @staticmethod
    def _args(**kw):
        from argparse import Namespace

        return Namespace(**kw)

    def test_cli_review_list(self, workflow, _isolate_factor_db, capsys):
        """list 输出待审查队列（含 factor_id 行）。"""
        from fts.cli import _cmd_factor_review_list

        rc = _cmd_factor_review_list(self._args(market=None, limit=50, db=_isolate_factor_db))
        assert rc == 0
        out = capsys.readouterr().out
        assert "待审查因子队列" in out
        assert "fct_rev_a" in out
        assert "fct_rev_b" in out

    def test_cli_review_list_market_filter(self, workflow, _isolate_factor_db, capsys):
        """list --market stock 只输出 stock 因子。"""
        from fts.cli import _cmd_factor_review_list

        rc = _cmd_factor_review_list(self._args(market="stock", limit=50, db=_isolate_factor_db))
        assert rc == 0
        out = capsys.readouterr().out
        assert "fct_rev_b" in out
        assert "fct_rev_a" not in out

    def test_cli_review_approve(self, _isolate_factor_db, capsys):
        """approve 命令回写 DuckDB（决策 approved）。"""
        from fts.cli import _cmd_factor_review_approve
        from fts.factor_engine.factor_inspector import FactorReviewWorkflow

        rc = _cmd_factor_review_approve(
            self._args(factor_id="fct_rev_a", comment="经济逻辑成立", db=_isolate_factor_db)
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "已批准" in out
        status = FactorReviewWorkflow(db_path=_isolate_factor_db).get_status("fct_rev_a")
        assert status["decision"] == "approved"
        assert status["comment"] == "经济逻辑成立"

    def test_cli_review_reject(self, _isolate_factor_db, capsys):
        """reject 命令回写 DuckDB（决策 rejected）。"""
        from fts.cli import _cmd_factor_review_reject
        from fts.factor_engine.factor_inspector import FactorReviewWorkflow

        rc = _cmd_factor_review_reject(self._args(factor_id="fct_rev_b", comment="经济逻辑存疑", db=_isolate_factor_db))
        assert rc == 0
        out = capsys.readouterr().out
        assert "已驳回" in out
        status = FactorReviewWorkflow(db_path=_isolate_factor_db).get_status("fct_rev_b")
        assert status["decision"] == "rejected"
        assert status["comment"] == "经济逻辑存疑"
