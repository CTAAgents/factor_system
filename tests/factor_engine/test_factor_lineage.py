"""tests/factor_engine/test_factor_lineage.py — 因子数据血缘审计测试

覆盖 GAP-023: 因子数据血缘审计能力
- 演化谱系查询
- 评估历史追溯
- 质量退化检测
- 血缘报告生成
- 批量血缘审计

版本: v1.0
"""

from __future__ import annotations


import pytest

from fts.factor_engine.factor_db import (
    FactorLineage,
    FactorRepository,
    init_database,
)
from fts.factor_engine.factor_db.schema import get_connection


# ─=== Fixtures ──────────────────────────────────────────


@pytest.fixture
def test_db_path(tmp_path):
    """临时测试数据库路径。"""
    return tmp_path / "test_lineage.duckdb"


@pytest.fixture
def repo(test_db_path):
    """初始化的 FactorRepository。"""
    init_database(test_db_path)
    return FactorRepository(test_db_path)


@pytest.fixture
def lineage(repo):
    """初始化的 FactorLineage。"""
    return FactorLineage(repo)


@pytest.fixture
def sample_factor(repo):
    """创建示例因子。"""
    factor_id = repo.create_factor(
        {
            "name": "test_momentum_factor",
            "code": "def factor_program(data, params):\\n    return data['close']",
            "family": "momentum",
            "market": "futures",
            "source": "seed",
            "sharpe": 1.5,
            "ic": 0.05,
        }
    )
    return factor_id


@pytest.fixture
def evolved_factor(repo, sample_factor):
    """创建演化因子（含 parent_id）。"""
    factor_id = repo.create_factor(
        {
            "name": "evolved_momentum_v1",
            "code": "def factor_program(data, params):\\n    return data['close'] * 1.1",
            "family": "momentum",
            "market": "futures",
            "source": "evolved",
            "parent_id": sample_factor,
            "generation": 1,
            "sharpe": 2.0,
            "ic": 0.06,
        }
    )
    return factor_id


@pytest.fixture
def with_evaluations(repo, sample_factor):
    """为因子添加评估记录。"""
    for i in range(5):
        repo.add_evaluation(
            sample_factor,
            {
                "sharpe": 1.0 + i * 0.1,
                "ic": 0.04 + i * 0.005,
                "icir": 1.2 + i * 0.1,
                "max_drawdown": 0.05 + i * 0.01,
                "turnover": 0.5,
                "overall_passed": i >= 2,
            },
        )
    return sample_factor


@pytest.fixture
def declining_factor(repo):
    """创建质量退化的因子。"""
    factor_id = repo.create_factor(
        {
            "name": "declining_factor",
            "code": "def factor_program(data, params):\\n    return data['open']",
            "family": "mean_reversion",
            "market": "futures",
            "source": "evolved",
            "sharpe": 1.0,
            "ic": 0.04,
            "is_elite": True,
        }
    )
    # 添加先好后差的评估
    for i in range(10):
        sharpe = 2.0 - i * 0.3
        repo.add_evaluation(
            factor_id,
            {
                "sharpe": sharpe,
                "ic": 0.05 - i * 0.003,
                "icir": 1.5 - i * 0.15,
                "max_drawdown": 0.05 + i * 0.02,
                "turnover": 0.5,
                "overall_passed": sharpe > 0.5,
            },
        )
    return factor_id


# ─=== 基础测试 ──────────────────────────────────────────


class TestFactorLineageInit:
    """FactorLineage 初始化测试。"""

    def test_init_with_repo(self, repo):
        lineage = FactorLineage(repo)
        assert lineage.repo is repo

    def test_init_lazy_repo(self, test_db_path):
        init_database(test_db_path)
        lineage = FactorLineage()
        assert lineage.repo is not None


# ─=== 演化谱系测试 ──────────────────────────────────


class TestEvolutionLineage:
    """演化谱系查询测试。"""

    def test_get_lineage_simple(self, lineage, sample_factor):
        result = lineage.get_lineage(sample_factor)
        assert "factor_info" in result
        assert result["factor_info"]["factor_id"] == sample_factor
        assert result["factor_info"]["name"] == "test_momentum_factor"

    def test_get_lineage_with_ancestors(self, lineage, evolved_factor, sample_factor):
        result = lineage.get_lineage(evolved_factor)
        assert len(result["ancestors"]) >= 1
        ancestor_ids = [a["factor_id"] for a in result["ancestors"]]
        assert sample_factor in ancestor_ids

    def test_get_lineage_descendants(self, lineage, sample_factor, evolved_factor):
        result = lineage.get_lineage(sample_factor)
        assert len(result["descendants"]) >= 1
        desc_ids = [d["factor_id"] for d in result["descendants"]]
        assert evolved_factor in desc_ids

    def test_get_lineage_not_found(self, lineage):
        result = lineage.get_lineage("nonexistent_factor")
        assert "error" in result

    def test_lineage_includes_versions(self, lineage, sample_factor):
        result = lineage.get_lineage(sample_factor)
        assert "versions" in result
        assert len(result["versions"]) >= 1

    def test_lineage_includes_evaluations(self, lineage, with_evaluations):
        result = lineage.get_lineage(with_evaluations)
        assert "evaluations_summary" in result
        summary = result["evaluations_summary"]
        assert summary["total_evals"] == 5
        assert summary["pass_rate"] > 0


# ─=== 评估趋势测试 ──────────────────────────────────


class TestEvaluationTrend:
    """评估历史追溯测试。"""

    def test_trend_with_data(self, lineage, with_evaluations):
        trend = lineage.get_evaluation_trend(with_evaluations, "sharpe")
        assert "trend" in trend
        assert trend["trend"] in ("improving", "stable", "declining")

    def test_trend_no_data(self, lineage, sample_factor):
        trend = lineage.get_evaluation_trend(sample_factor, "sharpe")
        assert trend["trend"] == "no_data"

    def test_trend_declining(self, lineage, declining_factor):
        trend = lineage.get_evaluation_trend(declining_factor, "sharpe")
        assert trend["trend"] == "declining"
        assert trend["pct_change"] < 0

    def test_trend_metrics(self, lineage, with_evaluations):
        for metric in ("sharpe", "ic", "icir"):
            trend = lineage.get_evaluation_trend(with_evaluations, metric)
            assert "trend" in trend
            assert "data_points" in trend

    def test_trend_pct_change(self, lineage, declining_factor):
        trend = lineage.get_evaluation_trend(declining_factor, "sharpe")
        assert "pct_change" in trend
        assert isinstance(trend["pct_change"], float)


# ─=== 质量退化检测测试 ──────────────────────────────────


class TestDegradationDetection:
    """质量退化检测测试。"""

    def test_detect_healthy(self, lineage, with_evaluations):
        result = lineage.detect_degradation(with_evaluations)
        assert "is_degraded" in result
        assert result["is_degraded"] is False

    def test_detect_degraded(self, lineage, declining_factor):
        result = lineage.detect_degradation(declining_factor)
        assert result["is_degraded"] is True
        assert result["recommendation"] == "考虑暂停使用该因子"

    def test_degradation_score(self, lineage, declining_factor):
        result = lineage.detect_degradation(declining_factor)
        assert "degradation_score" in result
        assert result["degradation_score"] < 0

    def test_degradation_threshold(self, lineage, declining_factor):
        result = lineage.detect_degradation(declining_factor, threshold=-0.3)
        assert isinstance(result["is_degraded"], bool)


# ─=== 血缘报告测试 ──────────────────────────────────


class TestLineageReport:
    """血缘报告生成测试。"""

    def test_generate_report(self, lineage, with_evaluations):
        report = lineage.generate_lineage_report(with_evaluations)
        assert report["report_type"] == "factor_lineage_audit"
        assert "lineage_summary" in report
        assert "quality_assessment" in report
        assert "recommendations" in report

    def test_report_includes_versions(self, lineage, with_evaluations):
        report = lineage.generate_lineage_report(with_evaluations, include_versions=True)
        assert "version_history" in report

    def test_report_excludes_versions(self, lineage, with_evaluations):
        report = lineage.generate_lineage_report(with_evaluations, include_versions=False)
        assert "version_history" not in report

    def test_report_recommendations(self, lineage, declining_factor):
        report = lineage.generate_lineage_report(declining_factor)
        assert len(report["recommendations"]) >= 1
        assert any("退化" in r for r in report["recommendations"])


# ─=== 批量审计测试 ──────────────────────────────────


class TestBatchAudit:
    """批量血缘审计测试。"""

    def test_batch_audit(self, lineage, repo):
        for i in range(5):
            fid = repo.create_factor(
                {
                    "name": f"batch_factor_{i}",
                    "code": f"def factor_program(data, params):\\n    return data['close'] * {i}",
                    "family": "momentum",
                    "market": "futures",
                    "source": "seed",
                    "sharpe": 1.0 + i * 0.2,
                    "ic": 0.04 + i * 0.005,
                    "is_elite": True,
                }
            )
            for j in range(6):
                repo.add_evaluation(
                    fid,
                    {
                        "sharpe": 0.5 + j * 0.2,
                        "ic": 0.03 + j * 0.005,
                        "icir": 1.0,
                        "max_drawdown": 0.05,
                        "turnover": 0.5,
                        "overall_passed": True,
                    },
                )

        result = lineage.batch_audit(market="futures", min_evals=5)
        assert result["audit_type"] == "batch_lineage_audit"
        assert result["total_audited"] >= 5
        assert result["healthy_count"] >= 0
        assert result["degradation_rate"] >= 0

    def test_batch_audit_with_threshold(self, lineage, repo, declining_factor):
        # Re-set status to active since add_evaluation sets status based on last eval
        repo.update_factor(declining_factor, {"status": "active"})
        result = lineage.batch_audit(market="futures", min_evals=3, limit=3)
        assert "results" in result
        assert len(result["results"]) >= 1

    def test_batch_audit_summary(self, lineage, repo):
        fid = repo.create_factor(
            {
                "name": "summary_test",
                "code": "def factor_program(data, params):\\n    return data['close']",
                "family": "test",
                "market": "futures",
                "sharpe": 1.0,
                "ic": 0.04,
                "is_elite": True,
            }
        )
        for _ in range(6):
            repo.add_evaluation(
                fid,
                {
                    "sharpe": 1.0,
                    "ic": 0.04,
                    "overall_passed": True,
                },
            )

        result = lineage.batch_audit(market="futures", min_evals=5)
        assert isinstance(result["degradation_rate"], float)


# ─=== 边界条件测试 ──────────────────────────────────


class TestEdgeCases:
    """边界条件测试。"""

    def test_factor_without_evaluations(self, lineage, sample_factor):
        result = lineage.get_lineage(sample_factor)
        assert result["evaluations_summary"]["total_evals"] == 0
        assert result["evaluations_summary"]["pass_rate"] == 0.0

    def test_circular_lineage_protection(self, repo, test_db_path):
        init_database(test_db_path)
        r2 = FactorRepository(test_db_path)
        fid1 = r2.create_factor(
            {
                "name": "circle_1",
                "code": "def f(d, p): return d['x']",
                "family": "test",
                "market": "stock",
                "source": "evolved",
            }
        )
        fid2 = r2.create_factor(
            {
                "name": "circle_2",
                "code": "def f(d, p): return d['x'] * 2",
                "family": "test",
                "market": "stock",
                "source": "evolved",
                "parent_id": fid1,
            }
        )
        # Create circular reference manually
        conn = get_connection(test_db_path)
        conn.execute(
            "UPDATE factor_catalog SET parent_id = ? WHERE factor_id = ?",
            [fid2, fid1],
        )
        conn.close()

        lineage = FactorLineage(r2)
        result = lineage.get_lineage(fid1)
        # Should not hang (circular reference handled)
        assert "factor_info" in result

    def test_batch_audit_empty(self, lineage, repo):
        result = lineage.batch_audit(market="nonexistent", min_evals=100)
        assert result["total_audited"] == 0
        assert result["degradation_rate"] == 0.0
