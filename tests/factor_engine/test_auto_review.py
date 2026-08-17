"""tests/factor_engine/test_auto_review.py — C8-2 机审/人审可配置测试。

覆盖（实施设计 2026-08-11）:
    1. AutoReviewPolicy.classify 三态判定全分支（缺失/非数值/非有限/极端偏高→人审；
       低质→自动驳回；正常→自动批准；边界值）
    2. auto_review 批量机审（正常批准/低质驳回落库 reviewer=auto/异常保持 pending/统计返回）
    3. manual（纯人审）模式拒绝 + --force 覆盖（用户决定权）
    4. 幂等（重复 auto 不重复处理已审因子）+ 空队列 + env 阈值覆盖
    5. CLI `fts factor review auto`（成功/拒绝/失败）
"""

from __future__ import annotations

import math

import pytest

from fts.factor_engine.factor_inspector import (
    AutoReviewPolicy,
    FactorReviewWorkflow,
    ReviewDecision,
    load_review_mode,
)


@pytest.fixture(autouse=True)
def _isolate_factor_db(tmp_path, monkeypatch):
    """隔离 DuckDB factor_catalog，防污染真实库（同 test_review_workflow.py）。"""
    from fts.factor_engine.factor_db import schema

    isolated_db = tmp_path / "factor_catalog.duckdb"
    schema.init_database(isolated_db)
    monkeypatch.setattr(schema, "DATABASE_PATH", isolated_db)
    return str(isolated_db)


def _passing_qa_meta() -> dict:
    """完整质检结论（v2.104.0+89 机审门禁通过所需）。"""
    return {
        "audit_passed": True,
        "quality_grade": "B",
        "high_ic_grade": "A",
        "multiple_passed": True,
        "walk_forward_windows": 4,
        "q1_q10_passed": True,
    }


def _insert_factor(conn, factor_id: str, name: str, ic, sharpe, market: str = "futures",
                   metadata: dict | None = None) -> None:
    """向隔离 factor_catalog 插入一条因子（ic/sharpe 可控）。"""
    import json

    meta_json = json.dumps({"qa_review": metadata}) if metadata else "{}"
    conn.execute(
        "INSERT INTO factor_catalog (factor_id, name, code, code_hash, "
        "economic_logic, source, market, ic, sharpe, metadata) "
        "VALUES (?, ?, 'def f(): pass', 'h', '{}', 'seed', ?, ?, ?, ?)",
        [factor_id, name, market, ic, sharpe, meta_json],
    )


def _seed_factors(db_path: str) -> None:
    """插入四类机审样本：正常 / 低质 / 异常高 / 缺失（含完整质检结论）。"""
    import duckdb

    conn = duckdb.connect(db_path)
    try:
        _insert_factor(conn, "fct_ar_normal", "normal", 0.05, 2.0,
                       metadata=_passing_qa_meta())
        _insert_factor(conn, "fct_ar_low", "low_quality", 0.01, 1.5,
                       metadata=_passing_qa_meta())
        _insert_factor(conn, "fct_ar_high", "overfit_high", 0.9, 2.0,
                       metadata=_passing_qa_meta())
        _insert_factor(conn, "fct_ar_missing", "missing", None, None)
    finally:
        conn.close()


@pytest.fixture
def workflow(_isolate_factor_db) -> FactorReviewWorkflow:
    """隔离库上的审查工作流（含四类样本）。"""
    _seed_factors(_isolate_factor_db)
    return FactorReviewWorkflow(db_path=_isolate_factor_db)


def _policy() -> AutoReviewPolicy:
    """默认策略实例。"""
    return AutoReviewPolicy()


# ─── classify 三态判定全分支 ────────────────────────────────


class TestAutoReviewPolicy:
    """机审判定策略三态分类。"""

    def test_missing_ic_none(self):
        """ic=None → 转人审。"""
        decision, reason = _policy().classify(None, 2.0)
        assert decision is None
        assert "缺失" in reason

    def test_missing_sharpe_none(self):
        """sharpe=None → 转人审。"""
        decision, reason = _policy().classify(0.05, None)
        assert decision is None

    def test_missing_nan(self):
        """NaN → 转人审。"""
        decision, _ = _policy().classify(float("nan"), 2.0)
        assert decision is None

    def test_non_numeric(self):
        """非数值 → 转人审。"""
        decision, _ = _policy().classify("abc", 2.0)
        assert decision is None

    def test_infinite(self):
        """非有限值 → 转人审。"""
        decision, _ = _policy().classify(0.05, math.inf)
        assert decision is None

    def test_high_ic_to_human(self):
        """ic 超上限（疑过拟合）→ 转人审。"""
        decision, reason = _policy().classify(0.9, 2.0)
        assert decision is None
        assert "过拟合" in reason

    def test_high_sharpe_to_human(self):
        """sharpe 超上限 → 转人审。"""
        decision, _ = _policy().classify(0.05, 50.0)
        assert decision is None

    def test_low_ic_rejected(self):
        """ic 低于下限 → 自动驳回。"""
        decision, reason = _policy().classify(0.01, 2.0)
        assert decision == ReviewDecision.REJECTED
        assert "低质" in reason

    def test_low_sharpe_rejected(self):
        """sharpe 低于下限 → 自动驳回。"""
        decision, _ = _policy().classify(0.05, 0.3)
        assert decision == ReviewDecision.REJECTED

    def test_normal_approved(self):
        """正常范围 + 完整质检结论 → 自动批准。"""
        decision, reason = _policy().classify(0.05, 2.0, qa_meta=_passing_qa_meta())
        assert decision == ReviewDecision.APPROVED
        assert "机审通过" in reason

    def test_boundary_equal_approved(self):
        """等于边界值（min/max）+ 完整质检结论 → 正常（严格大于/小于才触发异常）。"""
        assert _policy().classify(0.02, 0.5, qa_meta=_passing_qa_meta())[0] == ReviewDecision.APPROVED
        assert _policy().classify(0.8, 30.0, qa_meta=_passing_qa_meta())[0] == ReviewDecision.APPROVED

    def test_missing_qa_meta_to_human(self):
        """缺完整质检结论（v2.104.0+89 门禁）→ 宁缺毋滥转人审。"""
        decision, reason = _policy().classify(0.05, 2.0)
        assert decision is None
        assert "质检记录缺失" in reason

    def test_from_env_overrides(self, monkeypatch):
        """FTS_REVIEW_* env 覆盖阈值（非法值回退默认）。"""
        monkeypatch.setenv("FTS_REVIEW_MIN_IC", "0.1")
        monkeypatch.setenv("FTS_REVIEW_MAX_SHARPE", "10")
        monkeypatch.setenv("FTS_REVIEW_MIN_IC", "0.1")  # 覆盖上一行（合法）
        monkeypatch.setenv("FTS_REVIEW_MAX_IC", "not-a-number")
        p = AutoReviewPolicy.from_env()
        assert p.min_ic == 0.1
        assert p.max_ic == 0.8  # 非法回退默认
        assert p.max_sharpe == 10.0

    def test_from_env_defaults(self, monkeypatch):
        """无 env 时使用默认值。"""
        monkeypatch.delenv("FTS_REVIEW_MIN_IC", raising=False)
        p = AutoReviewPolicy.from_env()
        assert p.min_ic == 0.02
        assert p.max_sharpe == 30.0


# ─── auto_review 批量机审 ───────────────────────────────────


class TestAutoReviewWorkflow:
    """批量机审主流程。"""

    def test_normal_approved_with_auto_reviewer(self, workflow):
        """正常因子 → 自动批准落库（reviewer=auto）。"""
        workflow.auto_review(limit=200)
        status = workflow.get_status("fct_ar_normal")
        assert status["decision"] == "approved"
        assert status["reviewer"] == "auto"
        assert "[机审]" in status["comment"]

    def test_low_quality_rejected(self, workflow):
        """低质因子 → 自动驳回落库（reviewer=auto）。"""
        workflow.auto_review(limit=200)
        status = workflow.get_status("fct_ar_low")
        assert status["decision"] == "rejected"
        assert status["reviewer"] == "auto"

    def test_human_cases_stay_pending(self, workflow):
        """异常/缺失因子 → 保持 pending 且进入 needs_human。"""
        result = workflow.auto_review(limit=200)
        ids = {f["factor_id"] for f in result["needs_human"]}
        assert "fct_ar_high" in ids
        assert "fct_ar_missing" in ids
        # 未写入审查记录 → get_status None
        assert workflow.get_status("fct_ar_high") is None
        assert workflow.get_status("fct_ar_missing") is None

    def test_stats_returned(self, workflow):
        """返回统计 {mode, auto_approved, auto_rejected, needs_human}。"""
        result = workflow.auto_review(limit=200)
        assert result["mode"] == "auto"
        assert result["auto_approved"] == 1
        assert result["auto_rejected"] == 1
        assert len(result["needs_human"]) == 2
        assert result["total_pending"] == 4

    def test_manual_mode_raises(self, workflow, monkeypatch):
        """manual 模式（FTS_REVIEW_MODE=manual）拒绝执行。"""
        monkeypatch.setenv("FTS_REVIEW_MODE", "manual")
        with pytest.raises(ValueError, match="manual"):
            workflow.auto_review(limit=200)

    def test_manual_mode_force_overrides(self, workflow, monkeypatch):
        """manual 模式 + force=True → 显式覆盖执行（用户决定权）。"""
        monkeypatch.setenv("FTS_REVIEW_MODE", "manual")
        result = workflow.auto_review(limit=200, force=True)
        assert result["auto_approved"] == 1
        assert result["mode"] == "manual"

    def test_idempotent_second_run(self, workflow):
        """重复 auto_review：已审因子不在队列，第二次不再处理。"""
        workflow.auto_review(limit=200)
        result2 = workflow.auto_review(limit=200)
        assert result2["total_pending"] == 2  # 仅剩两个 needs_human
        assert result2["auto_approved"] == 0
        assert result2["auto_rejected"] == 0

    def test_empty_queue(self, _isolate_factor_db):
        """空队列 → 全零统计。"""
        wf = FactorReviewWorkflow(db_path=_isolate_factor_db)
        result = wf.auto_review(limit=200)
        assert result["total_pending"] == 0
        assert result["auto_approved"] == 0
        assert result["needs_human"] == []

    def test_limit_applied(self, workflow):
        """limit 限制处理条数。"""
        result = workflow.auto_review(limit=2)
        assert result["total_pending"] == 2

    def test_load_review_mode_default_auto(self, monkeypatch):
        """默认审查模式 auto。"""
        monkeypatch.delenv("FTS_REVIEW_MODE", raising=False)
        assert load_review_mode() == "auto"

    def test_load_review_mode_env(self, monkeypatch):
        """FTS_REVIEW_MODE 可配置。"""
        monkeypatch.setenv("FTS_REVIEW_MODE", "manual")
        assert load_review_mode() == "manual"


# ─── CLI ────────────────────────────────────────────────────


class TestCliReviewAuto:
    """CLI fts factor review auto。"""

    def _run(self, monkeypatch, workflow_result=None, raise_error=None, force=False):
        from fts import cli

        args = type("Args", (), {"limit": 200, "force": force, "db": None})()
        if raise_error is not None:

            def _boom(*a, **k):
                raise raise_error

            monkeypatch.setattr(FactorReviewWorkflow, "auto_review", _boom)
        else:
            monkeypatch.setattr(
                FactorReviewWorkflow,
                "auto_review",
                lambda *a, **k: workflow_result or {
                    "mode": "auto",
                    "total_pending": 0,
                    "auto_approved": 0,
                    "auto_rejected": 0,
                    "needs_human": [],
                    "skipped": 0,
                },
            )
        return cli._cmd_factor_review_auto(args)

    def test_cli_auto_success(self, monkeypatch, capsys):
        """机审成功 → rc=0 且输出统计。"""
        rc = self._run(
            monkeypatch,
            workflow_result={
                "mode": "auto",
                "total_pending": 4,
                "auto_approved": 1,
                "auto_rejected": 1,
                "needs_human": [{"factor_id": "f", "reason": "超上限"}],
                "skipped": 0,
            },
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "机审完成" in out
        assert "自动批准 1" in out
        assert "转人审" in out

    def test_cli_auto_manual_rejected(self, monkeypatch, capsys):
        """manual 模式拒绝 → rc=2 + stderr 提示。"""
        rc = self._run(monkeypatch, raise_error=ValueError("manual"))
        assert rc == 2
        assert "manual" in capsys.readouterr().err

    def test_cli_auto_failure(self, monkeypatch, capsys):
        """执行异常 → rc=1。"""
        rc = self._run(monkeypatch, raise_error=RuntimeError("db down"))
        assert rc == 1
        assert "机审执行失败" in capsys.readouterr().err

    def test_cli_parser_has_auto(self):
        """argparse 已注册 review auto 子命令。"""
        from fts.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["factor", "review", "auto", "--limit", "10", "--force"])
        assert args.subcommand == "auto"
        assert args.limit == 10
        assert args.force is True
