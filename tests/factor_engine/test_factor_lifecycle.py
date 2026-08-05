"""tests/factor_engine/test_factor_lifecycle.py — 因子完整生命周期 E2E 测试

覆盖完整闭环:
    1. 因子入库 (FactorRepository.create_factor)
    2. 血缘追踪 (FactorLineage.get_lineage / get_evaluation_trend)
    3. 审计 + 失败分类 (FactorAuditor.audit + FailureClassifier)
    4. 巡检与自动降级 (FactorInspector.inspect_and_downgrade)
    5. 重新激活 (FactorInspector.reactivate_factor)

版本: v1.0
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fts.factor_engine.audit import (
    AuditItemResult,
    FactorAuditConfig,
    FactorAuditReport,
    FactorAuditor,
)
from fts.factor_engine.factor_db import (
    FactorLineage,
    FactorRepository,
    init_database,
)
from fts.factor_engine.factor_inspector import FactorInspector


# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_lifecycle.db"
    init_database(str(db_path))
    return db_path


@pytest.fixture
def repo(db):
    return FactorRepository(str(db))


@pytest.fixture
def lineage(repo):
    return FactorLineage(repo)


@pytest.fixture
def auditor():
    return FactorAuditor()


@pytest.fixture
def inspector(repo, lineage):
    return FactorInspector(repo=repo, lineage=lineage)


def _add_evals(repo, factor_id, sharpe_values, ic=0.05):
    """添加评估记录 (sharpe_values[0] = 最旧, [-1] = 最新)。"""
    base_date = datetime(2025, 1, 1)
    n = len(sharpe_values)
    for i, sharpe in enumerate(sharpe_values):
        # 最旧值 (i=0) 使用最早日期，最新值使用最新日期
        eval_date = base_date - timedelta(days=(n - 1 - i) * 30)
        repo._get_conn().execute("""
            INSERT INTO factor_evaluations (
                eval_id, factor_id,
                level_1_sharpe, level_1_ic, level_1_icir,
                evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, [
            f"eval_{uuid.uuid4().hex[:8]}",
            factor_id,
            sharpe,
            ic,
            ic * 2 if ic > 0 else ic,
            eval_date.strftime("%Y-%m-%d %H:%M:%S"),
        ])


# ─── 1. 因子入库 + 血缘追踪 ──────────────────────────


class TestFactorStorageAndLineage:
    def test_factor_roundtrip(self, repo, lineage):
        """因子入库后可通过血缘查询获取完整信息。"""
        fid = repo.create_factor({
            "factor_id": "f_e2e_001",
            "name": "E2E Factor",
            "code": "close - open",
            "family": "e2e",
            "market": "stock",
            "is_elite": True,
            "status": "active",
            "sharpe": 1.5,
            "ic": 0.08,
        })

        assert fid == "f_e2e_001"

        factor = repo.get_factor(fid)
        assert factor is not None
        assert factor["name"] == "E2E Factor"
        assert factor["is_elite"] is True
        assert factor["sharpe"] == 1.5

        lineage_info = lineage.get_lineage(fid)
        assert lineage_info["factor_id"] == fid
        assert lineage_info["factor_info"]["name"] == "E2E Factor"
        assert lineage_info["evaluations_summary"]["total_evals"] == 0

    def test_lineage_with_evaluations(self, repo, lineage):
        """添加评估后血缘报告应包含评估摘要。"""
        fid = repo.create_factor({
            "factor_id": "f_e2e_002",
            "name": "E2E Eval Factor",
            "code": "close",
            "family": "e2e",
            "market": "futures",
            "is_elite": True,
            "sharpe": 2.0,
        })
        _add_evals(repo, fid, sharpe_values=[2.0, 2.1, 1.9, 2.0])

        lineage_info = lineage.get_lineage(fid)
        summary = lineage_info["evaluations_summary"]
        assert summary["total_evals"] == 4
        assert summary["avg_sharpe"] > 1.5


# ─── 2. 审计 + 失败分类 ──────────────────────────


class TestAuditAndFailureClassification:
    def test_audit_failure_generates_suggestions(self, auditor):
        """审计失败时自动输出 FailureClassifier 建议。"""
        n = 120
        rng = np.random.RandomState(42)
        data = pd.DataFrame({
            "close": np.cumsum(rng.randn(n) * 0.5) + 100,
        })
        forward_returns = rng.randn(n) * 0.01

        # 故意提供失败的审计条件
        report = auditor.audit(
            factor={"factor_id": "f_fail", "name": "failing_factor"},
            data=data,
            forward_returns=forward_returns,
            symbol_ic_map={"RB": -0.05, "HC": -0.03, "CU": 0.01},
            p_values=[0.1, 0.2, 0.5],
            ic=-0.05,
            sharpe=0.3,
        )

        assert report.passed is False
        assert report.failure_analysis is not None
        assert "detected_patterns" in report.failure_analysis
        assert "suggestions" in report.failure_analysis
        assert len(report.failure_analysis["detected_patterns"]) > 0
        assert len(report.failure_analysis["suggestions"]) > 0

        # 建议字段完整
        for suggestion in report.failure_analysis["suggestions"]:
            assert "pattern" in suggestion
            assert "priority" in suggestion
            assert "action" in suggestion
            assert "rationale" in suggestion

    def test_audit_pass_without_failure_analysis(self, auditor):
        """审计通过时 failure_analysis 应为空。"""
        report = auditor.audit(
            factor={"factor_id": "f_pass", "name": "passing_factor"},
            oos_result={"ic_consistency": 0.8, "passed": True},
            symbol_ic_map={"RB": 0.05, "HC": 0.03, "CU": 0.04},
            p_values=[0.01, 0.02],
        )

        assert report.passed is True
        assert report.failure_analysis is None

    def test_audit_report_to_dict_includes_failure(self, auditor):
        """失败报告 to_dict 应包含 failure_analysis。"""
        report = auditor.audit(
            factor={"factor_id": "f_dict", "name": "dict_factor"},
            p_values=[0.5, 0.6],
            ic=-0.1,
        )

        d = report.to_dict()
        assert "failure_analysis" in d
        assert d["failure_analysis"]["severity"] in ("high", "medium", "low")


# ─── 3. 巡检 + 降级 + 改善建议 ──────────────────────────


class TestInspectionAndSuggestions:
    def test_degraded_factor_triggers_downgrade(self, inspector, repo):
        """Sharpe 持续退化的因子应被降级。"""
        fid = repo.create_factor({
            "factor_id": "f_degenerate",
            "name": "Degenerate Factor",
            "code": "close",
            "family": "test",
            "market": "stock",
            "is_elite": True,
            "sharpe": 1.0,
        })
        # 时间顺序 [最旧 → 最新]: 1.0, 1.0, 0.2, 0.2 → 下降
        _add_evals(repo, fid, sharpe_values=[1.0, 1.0, 0.2, 0.2])

        result = inspector.inspect_and_downgrade(threshold=-0.1, commit=True)

        assert result["summary"]["downgraded"] >= 1

        factor = repo.get_factor(fid)
        assert factor["status"] == "degraded"
        assert factor["is_elite"] is False

        # 降级记录包含改善建议
        records = result["records"]
        assert len(records) >= 1
        for rec in records:
            if rec["action"] == "downgraded":
                assert "reason" in rec
                assert "degradation_score" in rec

    def test_downgraded_factor_gets_suggestion(self, inspector, repo):
        """被降级因子的记录应包含退化原因。"""
        fid = repo.create_factor({
            "factor_id": "f_suggest",
            "name": "Suggest Factor",
            "code": "close",
            "family": "test",
            "market": "stock",
            "is_elite": True,
            "sharpe": 0.8,
        })
        # 时间顺序: 0.8, 0.8, 0.1, 0.1 → 下降
        _add_evals(repo, fid, sharpe_values=[0.8, 0.8, 0.1, 0.1])

        result = inspector.inspect_and_downgrade(threshold=-0.1, commit=True)

        records = [r for r in result["records"] if r["action"] == "downgraded"]
        assert len(records) >= 1
        rec = records[0]
        # reason 字段包含退化相关描述
        assert isinstance(rec["reason"], str)
        assert len(rec["reason"]) > 0

    def test_reactivate_restores_elite_status(self, inspector, repo):
        """重新激活因子应恢复精英状态。"""
        fid = repo.create_factor({
            "factor_id": "f_reactivate",
            "name": "Reactivate Factor",
            "code": "close",
            "family": "test",
            "market": "stock",
            "is_elite": True,
            "sharpe": 0.5,
        })
        # 时间顺序: 0.5, 0.5, 0.1, 0.1 → 下降
        _add_evals(repo, fid, sharpe_values=[0.5, 0.5, 0.1, 0.1])

        # 降级
        inspector.inspect_and_downgrade(threshold=-0.1, commit=True)
        factor = repo.get_factor(fid)
        assert factor["is_elite"] is False
        assert factor["status"] == "degraded"

        # 重新激活
        success = inspector.reactivate_factor(fid, promote_to_elite=True)
        assert success is True

        factor = repo.get_factor(fid)
        assert factor["status"] == "active"
        assert factor["is_elite"] is True


# ─── 4. 完整闭环 E2E ──────────────────────────


class TestFullLifecycleE2E:
    def test_full_closed_loop(self, repo, lineage, auditor, inspector):
        """完整闭环: 入库 → 评估 → 血缘 → 审计 → 降级 → 激活。"""
        # 1. 因子入库
        fid = repo.create_factor({
            "factor_id": "f_lifecycle",
            "name": "Lifecycle Factor",
            "code": "alpha - beta",
            "family": "e2e",
            "market": "futures",
            "is_elite": True,
            "sharpe": 1.8,
            "ic": 0.07,
        })

        # 2. 添加评估历史 (最旧→最新: 1.8 → 0.3, 模拟退化)
        _add_evals(repo, fid, sharpe_values=[1.8, 1.5, 0.5, 0.3], ic=0.05)

        # 3. 血缘追踪
        lineage_info = lineage.get_lineage(fid)
        assert lineage_info["factor_id"] == fid
        assert lineage_info["factor_info"]["is_elite"] is True

        # 4. 审计 (故意失败以触发 FailureClassifier)
        n = 120
        rng = np.random.RandomState(42)
        data = pd.DataFrame({"close": np.cumsum(rng.randn(n)) * 0.5 + 100})
        forward_returns = rng.randn(n) * 0.01

        audit_report = auditor.audit(
            factor={"factor_id": fid, "name": "Lifecycle Factor"},
            data=data,
            forward_returns=forward_returns,
            symbol_ic_map={"RB": -0.05, "HC": -0.02, "CU": 0.01},
            p_values=[0.3, 0.4],
            ic=-0.05,
            sharpe=0.3,
        )

        # 审计失败 → 产生失败分析和建议
        assert audit_report.passed is False
        assert audit_report.failure_analysis is not None
        patterns = audit_report.failure_analysis["detected_patterns"]
        suggestions = audit_report.failure_analysis["suggestions"]
        assert len(patterns) > 0
        assert len(suggestions) > 0

        # 5. 巡检 + 自动降级
        inspection = inspector.inspect_and_downgrade(
            threshold=-0.1, commit=True
        )
        assert inspection["summary"]["downgraded"] >= 1

        # 验证降级结果
        degraded_factor = repo.get_factor(fid)
        assert degraded_factor["status"] == "degraded"
        assert degraded_factor["is_elite"] is False

        # 6. 重新激活
        success = inspector.reactivate_factor(fid, promote_to_elite=True)
        assert success is True

        restored = repo.get_factor(fid)
        assert restored["status"] == "active"
        assert restored["is_elite"] is True

    def test_healthy_factor_survives_inspection(self, repo, lineage, auditor, inspector):
        """健康因子应通过巡检不被降级。"""
        fid = repo.create_factor({
            "factor_id": "f_healthy",
            "name": "Healthy Factor",
            "code": "close",
            "family": "e2e",
            "market": "stock",
            "is_elite": True,
            "sharpe": 2.0,
        })
        _add_evals(repo, fid, sharpe_values=[1.9, 2.0, 2.0, 2.1])

        result = inspector.inspect_and_downgrade(threshold=-0.1, commit=True)

        factor = repo.get_factor(fid)
        assert factor["status"] == "active"
        assert factor["is_elite"] is True
        assert result["summary"]["downgraded"] == 0


# ─── 5. 边界条件 ──────────────────────────


class TestEdgeCases:
    def test_nonexistent_factor_reactivation(self, inspector):
        success = inspector.reactivate_factor("nonexistent_factor")
        assert success is False

    def test_empty_repo_inspection(self, inspector):
        result = inspector.inspect_and_downgrade(commit=False)
        assert result["summary"]["total_audited"] == 0
        assert result["summary"]["downgraded"] == 0

    def test_dry_run_does_not_modify(self, inspector, repo):
        fid = repo.create_factor({
            "factor_id": "f_dry",
            "name": "Dry Run Factor",
            "code": "close",
            "family": "test",
            "market": "stock",
            "is_elite": True,
            "sharpe": 0.5,
        })
        _add_evals(repo, fid, sharpe_values=[0.5, 0.5, 0.1, 0.1])

        result = inspector.inspect_and_downgrade(
            threshold=-0.1, commit=False
        )

        # dry-run 不修改数据
        factor = repo.get_factor(fid)
        assert factor["status"] == "active"
        assert factor["is_elite"] is True

    def test_audit_minimal_input(self, auditor):
        """最小输入审计不崩溃。"""
        report = auditor.audit(factor={"factor_id": "f_min"})
        assert report.factor_id == "f_min"
        assert len(report.items) == 6
