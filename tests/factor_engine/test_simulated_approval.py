"""tests/factor_engine/test_simulated_approval.py — 5 个待人工确认异常因子模拟审批测试。

背景（C8-2 实施，2026-08-11）：机审 AutoReviewPolicy 将异常值（IC/Sharpe 缺失、超上限、
非数值等）转人审。本测试对 5 个构造的异常因子执行「机审分类 → 保持待审 → 模拟人工
审批（批准/驳回）→ 落库与队列收敛」全流程模拟，固化 C8-2 人审闭环的可复现验证。

5 个异常因子样例:
    1. fct_sim_miss_ic   ic=None（缺失 → 无法机审 → 人审）
    2. fct_sim_miss_sp   sharpe=None（缺失 → 人审）
    3. fct_sim_high_ic   ic=0.95（超上限，疑过拟合/未来函数 → 人审）
    4. fct_sim_high_sp   sharpe=99.0（超上限 → 人审）
    5. fct_sim_nan_ic    ic=NaN（非数值 → 人审）

覆盖:
    1. AutoReviewPolicy.classify 五类异常全部转人审（decision None）
    2. auto_review 后 5 个全部保持 pending 且进入 needs_human（未落库）
    3. 模拟人工批准全流程：approve（意见+审查人）→ 落库 approved + 队列清空
    4. 模拟人工驳回全流程：reject → 落库 rejected（经验链降级不阻断）
    5. 幂等覆盖：先批准后驳回覆盖旧决定（模拟复核）
"""

from __future__ import annotations

import math

import pytest

from fts.factor_engine.factor_inspector import (
    AutoReviewPolicy,
    FactorReviewWorkflow,
    ReviewDecision,
)

# 5 个待人工确认的异常因子样例（ic/sharpe 各类异常）
SIM_ABNORMAL_FACTORS = [
    ("fct_sim_miss_ic", "sim_missing_ic", None, 2.0),
    ("fct_sim_miss_sp", "sim_missing_sharpe", 0.05, None),
    ("fct_sim_high_ic", "sim_high_ic", 0.95, 2.0),
    ("fct_sim_high_sp", "sim_high_sharpe", 0.05, 99.0),
    ("fct_sim_nan_ic", "sim_nan_ic", float("nan"), 1.5),
]


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """隔离 DuckDB factor_catalog，防污染真实库（同 test_review_workflow.py）。"""
    from fts.factor_engine.factor_db import schema

    isolated_db = tmp_path / "factor_catalog.duckdb"
    schema.init_database(isolated_db)
    monkeypatch.setattr(schema, "DATABASE_PATH", isolated_db)
    return str(isolated_db)


def _seed_abnormal_factors(db_path: str) -> None:
    """插入 5 个异常因子。"""
    import duckdb

    conn = duckdb.connect(db_path)
    try:
        for factor_id, name, ic, sharpe in SIM_ABNORMAL_FACTORS:
            conn.execute(
                "INSERT INTO factor_catalog (factor_id, name, code, code_hash, "
                "economic_logic, source, market, ic, sharpe) "
                "VALUES (?, ?, 'def f(): pass', 'h', '{}', 'seed', 'futures', ?, ?)",
                [factor_id, name, ic, sharpe],
            )
    finally:
        conn.close()


@pytest.fixture
def workflow(_isolate_factor_db) -> FactorReviewWorkflow:
    """隔离库上的审查工作流（含 5 个异常因子）。"""
    _seed_abnormal_factors(_isolate_factor_db)
    return FactorReviewWorkflow(db_path=_isolate_factor_db)


@pytest.fixture(autouse=True)
def _silence_experience_chain(monkeypatch):
    """屏蔽驳回意见写经验链副作用（聚焦审批状态机；真实经验链另测）。"""
    monkeypatch.setattr(FactorReviewWorkflow, "_record_rejection", lambda *a, **k: None)


def _factor_ids() -> list[str]:
    return [f[0] for f in SIM_ABNORMAL_FACTORS]


class TestClassifyAllHuman:
    """五类异常 → 机审全部转人审。"""

    def test_all_classify_human(self):
        """5 个异常因子 classify 全部返回 None（转人审）。"""
        policy = AutoReviewPolicy()
        for _, _, ic, sharpe in SIM_ABNORMAL_FACTORS:
            decision, reason = policy.classify(ic, sharpe)
            assert decision is None, f"异常因子 {ic}/{sharpe} 应转人审，实际 decision={decision}"
            assert reason  # 转人审必有原因说明

    def test_high_ic_reason_mentions_overfit(self):
        """超上限 IC 的转人审原因含「过拟合」。"""
        policy = AutoReviewPolicy()
        decision, reason = policy.classify(0.95, 2.0)
        assert decision is None
        assert "过拟合" in reason

    def test_missing_reason_mentions_missing(self):
        """缺失 IC 的转人审原因含「缺失」。"""
        policy = AutoReviewPolicy()
        decision, reason = policy.classify(None, 2.0)
        assert decision is None
        assert "缺失" in reason


class TestAutoReviewAllToHuman:
    """批量机审 → 5 个异常因子全部保持 pending 且进入 needs_human。"""

    def test_all_needs_human_stay_pending(self, workflow):
        """auto_review 后 5 个全部在 needs_human、未写入审查记录。"""
        result = workflow.auto_review(limit=200)
        human_ids = {f["factor_id"] for f in result["needs_human"]}
        assert human_ids == set(_factor_ids())
        assert result["auto_approved"] == 0
        assert result["auto_rejected"] == 0
        # 未落库 → get_status None（仍待人工确认）
        for fid in _factor_ids():
            assert workflow.get_status(fid) is None

    def test_queue_keeps_5_pending(self, workflow):
        """审批前队列仍含全部 5 个异常因子。"""
        queue = workflow.list_pending()
        assert {f["factor_id"] for f in queue} == set(_factor_ids())


class TestSimulatedApproveAll:
    """模拟人工批准全流程（机审转人审 → 人审批准落库）。"""

    def test_approve_all_writes_decisions(self, workflow):
        """5 个异常因子逐个模拟人工批准 → 全部落库 approved + 队列清空。"""
        for fid in _factor_ids():
            status = workflow.approve(fid, comment="模拟人审：异常值人工复核后批准", reviewer="alpha-board")
            assert status["decision"] == "approved"
        # 落库断言
        for fid in _factor_ids():
            status = workflow.get_status(fid)
            assert status is not None
            assert status["decision"] == "approved"
            assert status["reviewer"] == "alpha-board"
        # 队列收敛
        assert workflow.list_pending() == []


class TestSimulatedRejectAll:
    """模拟人工驳回全流程（机审转人审 → 人审驳回落库）。"""

    def test_reject_all_writes_decisions(self, workflow):
        """5 个异常因子逐个模拟人工驳回 → 全部落库 rejected。"""
        for fid in _factor_ids():
            status = workflow.reject(fid, comment="模拟人审：经济逻辑存疑，驳回")
            assert status["decision"] == "rejected"
        for fid in _factor_ids():
            assert workflow.get_status(fid)["decision"] == "rejected"
        assert workflow.list_pending() == []


class TestMixedOverrideAndPersistence:
    """幂等覆盖 + 意见/审查人落盘。"""

    def test_approve_then_reject_overrides(self, workflow):
        """先批准后驳回 → 覆盖旧决定（模拟复核驳回）。"""
        fid = _factor_ids()[0]
        workflow.approve(fid, comment="先批准")
        workflow.reject(fid, comment="复核发现数据依据不足，驳回")
        status = workflow.get_status(fid)
        assert status["decision"] == "rejected"
        assert status["comment"] == "复核发现数据依据不足，驳回"

    def test_reviewer_and_comment_persisted(self, workflow):
        """意见与审查人完整落盘。"""
        fid = _factor_ids()[0]
        workflow.approve(fid, comment="人工复核通过", reviewer="human-reviewer-01")
        status = workflow.get_status(fid)
        assert status["reviewer"] == "human-reviewer-01"
        assert status["comment"] == "人工复核通过"
