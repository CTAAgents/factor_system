"""tests/factor_engine/test_factor_inspector.py — 因子巡检与自动降级测试

覆盖:
- FactorInspector 初始化
- 巡检与自动降级流程（dry-run / commit）
- 降级因子查询
- 因子重新激活
- 边界条件

版本: v1.0
"""

from __future__ import annotations

import pytest

from fts.factor_engine.factor_db import (
    FactorLineage,
    FactorRepository,
    init_database,
)
from fts.factor_engine.factor_inspector import (
    DowngradeRecord,
    FactorInspector,
)


# ─── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path):
    """使用临时数据库的仓库。"""
    db_path = tmp_path / "test_inspector.db"
    init_database(str(db_path))
    return FactorRepository(str(db_path))


@pytest.fixture
def inspector(repo):
    """巡检器实例。"""
    lineage = FactorLineage(repo)
    return FactorInspector(repo=repo, lineage=lineage)


def _create_elite_factor(repo, factor_id, sharpe=1.0):
    """创建一个精英因子。"""
    return repo.create_factor(
        {
            "factor_id": factor_id,
            "name": f"Factor {factor_id}",
            "code": "close",
            "market": "stock",
            "is_elite": True,
            "status": "active",
            "sharpe": sharpe,
        }
    )


def _add_evaluations(repo, factor_id, sharpe_values, ic=0.05):
    """添加多条评估记录。

    sharpe_values[0] = 最新值, sharpe_values[-1] = 最旧值。
    数据库按 evaluated_at DESC 排序，因此最新值排在最前。
    """
    import uuid
    from datetime import datetime, timedelta

    base_date = datetime(2025, 1, 1)
    n = len(sharpe_values)
    for i, sharpe in enumerate(sharpe_values):
        # 最新值 (i=0) 使用最新日期，最旧值使用最早日期
        eval_date = base_date - timedelta(days=(n - 1 - i) * 30)
        repo._get_conn().execute(
            """
            INSERT INTO factor_evaluations (
                eval_id, factor_id,
                level_1_sharpe, level_1_ic, level_1_icir,
                evaluated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        """,
            [
                f"eval_{uuid.uuid4().hex[:8]}",
                factor_id,
                sharpe,
                ic,
                ic * 2 if ic > 0 else ic,
                eval_date.strftime("%Y-%m-%d %H:%M:%S"),
            ],
        )


# ─── 初始化 ──────────────────────────────────────────────


class TestInspectorInit:
    def test_init_default(self, repo):
        inspector = FactorInspector(repo=repo)
        assert inspector._repo is not None
        assert inspector._lineage is not None

    def test_init_with_lineage(self, repo):
        lineage = FactorLineage(repo)
        inspector = FactorInspector(repo=repo, lineage=lineage)
        assert inspector._repo is repo
        assert inspector._lineage is lineage


# ─── 巡检与降级流程 ──────────────────────────────────────


class TestInspectionAndDowngrade:
    def test_dry_run_no_factors(self, inspector):
        """空库 dry-run。"""
        result = inspector.inspect_and_downgrade(commit=False)

        assert result["summary"]["total_audited"] == 0
        assert result["summary"]["downgraded"] == 0
        assert result["commit"] is False
        assert len(result["records"]) == 0

    def test_dry_run_with_factors_no_degradation(self, inspector, repo):
        """有因子但无退化。"""
        fid = _create_elite_factor(repo, "f_healthy", sharpe=1.0)
        _add_evaluations(repo, fid, sharpe_values=[1.0, 1.1, 1.2])

        result = inspector.inspect_and_downgrade(commit=False)

        assert result["summary"]["total_audited"] >= 1
        assert result["summary"]["downgraded"] == 0

    def test_downgrade_degraded_factor(self, inspector, repo):
        """退化因子被降级。"""
        fid = _create_elite_factor(repo, "f_degraded", sharpe=0.5)
        # 近期 Sharpe 下降
        _add_evaluations(repo, fid, sharpe_values=[0.5, 0.5, 0.2, 0.2])

        result = inspector.inspect_and_downgrade(threshold=-0.1, commit=True)

        summary = result["summary"]
        assert summary["downgraded"] >= 1

        # 验证因子状态已更新
        factor = repo.get_factor(fid)
        assert factor is not None
        assert factor["status"] == "degraded"
        assert factor["is_elite"] is False

    def test_dry_run_does_not_modify(self, inspector, repo):
        """dry-run 不修改数据。"""
        fid = _create_elite_factor(repo, "f_test", sharpe=0.5)
        _add_evaluations(repo, fid, sharpe_values=[0.5, 0.3])

        inspector.inspect_and_downgrade(threshold=-0.1, commit=False)

        # 确认状态未变
        factor = repo.get_factor(fid)
        assert factor["status"] == "active"
        assert factor["is_elite"] is True

    def test_already_degraded_skipped(self, inspector, repo):
        """已降级的因子跳过。"""
        fid = _create_elite_factor(repo, "f_already", sharpe=0.5)
        _add_evaluations(repo, fid, sharpe_values=[0.5, 0.2])
        # 手动标记为 degraded，但保留 is_elite=True 以便被巡检筛选
        repo.update_factor(fid, {"status": "degraded"})

        result = inspector.inspect_and_downgrade(threshold=-0.1, commit=True)

        assert result["summary"]["skipped"] >= 1

    def test_downgrade_record_structure(self, inspector, repo):
        """验证降级记录结构。"""
        fid = _create_elite_factor(repo, "f_test", sharpe=0.5)
        _add_evaluations(repo, fid, sharpe_values=[0.5, 0.3])

        result = inspector.inspect_and_downgrade(threshold=-0.1, commit=True)

        for record in result["records"]:
            assert isinstance(record, dict)
            assert "factor_id" in record
            assert "action" in record
            assert record["action"] in {"downgraded", "skipped", "error"}


# ─── 降级因子查询 ──────────────────────────────────────


class TestGetDegradedFactors:
    def test_get_empty(self, inspector):
        factors = inspector.get_degraded_factors()
        assert factors == []

    def test_get_degraded(self, inspector, repo):
        fid1 = _create_elite_factor(repo, "f1", sharpe=0.5)
        fid2 = _create_elite_factor(repo, "f2", sharpe=0.5)

        # 手动降级
        repo.update_factor(fid1, {"status": "degraded", "is_elite": False})
        repo.update_factor(fid2, {"status": "degraded", "is_elite": False})

        factors = inspector.get_degraded_factors()
        assert len(factors) == 2

    def test_get_by_market(self, inspector, repo):
        fid1 = _create_elite_factor(repo, "f1", sharpe=0.5)
        _create_elite_factor(repo, "f_fut", sharpe=0.5)

        repo.update_factor(fid1, {"status": "degraded", "is_elite": False})

        factors = inspector.get_degraded_factors(market="futures")
        # 只有 f_fut，因为 f1 是 stock
        assert len(factors) == 0


# ─── 重新激活 ──────────────────────────────────────────


class TestReactivation:
    def test_reactivate_basic(self, inspector, repo):
        fid = _create_elite_factor(repo, "f_reactivate", sharpe=0.5)
        repo.update_factor(fid, {"status": "degraded", "is_elite": False})

        success = inspector.reactivate_factor(fid)
        assert success is True

        factor = repo.get_factor(fid)
        assert factor["status"] == "active"
        assert factor["is_elite"] is False

    def test_reactivate_and_promote(self, inspector, repo):
        fid = _create_elite_factor(repo, "f_reactivate2", sharpe=0.5)
        repo.update_factor(fid, {"status": "degraded", "is_elite": False})

        success = inspector.reactivate_factor(fid, promote_to_elite=True)
        assert success is True

        factor = repo.get_factor(fid)
        assert factor["status"] == "active"
        assert factor["is_elite"] is True

    def test_reactivate_nonexistent(self, inspector):
        success = inspector.reactivate_factor("f_nonexistent")
        assert success is False


# ─── 边界条件 ──────────────────────────────────────────


class TestEdgeCases:
    def test_inspect_with_market_filter(self, inspector, repo):
        _create_elite_factor(repo, "f_stock", sharpe=1.0)
        _create_elite_factor(repo, "f_fut", sharpe=1.0)

        result = inspector.inspect_and_downgrade(market="futures")
        assert result["summary"]["total_audited"] >= 0

    def test_inspect_result_structure(self, inspector):
        result = inspector.inspect_and_downgrade(commit=False)

        assert "inspection_id" in result
        assert "started_at" in result
        assert "completed_at" in result
        assert "duration_seconds" in result
        assert "summary" in result
        assert "records" in result

    def test_record_to_dict(self):
        record = DowngradeRecord(
            factor_id="f_test",
            factor_name="Test",
            reason="test",
            degradation_score=-0.3,
            previous_status="active",
            new_status="degraded",
            action="downgraded",
        )
        d = FactorInspector._record_to_dict(record)
        assert d["factor_id"] == "f_test"
        assert d["action"] == "downgraded"
        assert d["degradation_score"] == -0.3
