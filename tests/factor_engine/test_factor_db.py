"""
tests/factor_engine/test_factor_db.py — DuckDB 因子目录数据库测试

覆盖范围:
    - Schema 初始化与验证
    - FactorRepository CRUD 操作
    - 因子列表查询、搜索、统计
    - 版本管理与回滚
    - 评估记录管理
    - JSON → DuckDB 迁移
    - 边界条件与异常处理

版本: v1.0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# 确保能导入 fts.factor_engine
_FTS_ROOT = Path(__file__).resolve().parents[2]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))

from fts.factor_engine.factor_db.schema import (
    init_database,
    verify_database,
)
from fts.factor_engine.factor_db.repository import FactorRepository


# ─── Fixtures ──────────────────────────────────────────


@pytest.fixture
def temp_db(tmp_path):
    """创建临时 DuckDB 数据库。"""
    db_path = tmp_path / "test_factors.duckdb"
    init_database(db_path)
    return db_path


@pytest.fixture
def repo(temp_db):
    """创建 FactorRepository 实例。"""
    r = FactorRepository(temp_db)
    yield r
    r.close()


@pytest.fixture
def sample_factor():
    """创建示例因子数据。"""
    return {
        "name": "test_alpha_factor",
        "code": "def compute(): return np.random.randn(100)",
        "params": {"window": 20, "threshold": 0.5},
        "signature": {"input": ["close", "volume"], "output": "factor_value"},
        "economic_logic": {"description": "测试因子逻辑", "category": "technical"},
        "source": "evolution",
        "generation": 3,
        "trace_id": "trace_test_001",
        "sharpe": 2.5,
        "ic": 0.08,
        "icir": 1.2,
        "max_drawdown": 0.15,
        "turnover_monthly": 0.35,
        "decay_6m": 0.03,
        "market": "stock",
        "is_elite": False,
        "metadata": {"backtest_period": "2020-2025"},
    }


@pytest.fixture
def sample_factors_batch():
    """创建批量示例因子。"""
    return [
        {
            "name": f"factor_{i}",
            "code": f"def compute_{i}(): return {i} * np.ones(100)",
            "sharpe": round(1.0 + i * 0.5, 2),
            "ic": round(0.02 + i * 0.01, 4),
            "market": "stock" if i < 5 else "futures",
            "status": "active" if i < 8 else "failed",
            "is_elite": i < 6,
            "decay_6m": round(0.02 + i * 0.01, 3),
        }
        for i in range(10)
    ]


# ─── Schema 测试 ──────────────────────────────────────


class TestSchema:
    """Schema 初始化与验证测试。"""

    def test_init_database_creates_tables(self, temp_db):
        """测试初始化数据库创建所有表。"""
        stats = verify_database(temp_db)
        assert stats["exists"] is True
        assert "factor_catalog" in stats["tables"]
        assert "factor_evaluations" in stats["tables"]
        assert "factor_versions" in stats["tables"]
        assert "factor_correlations" in stats["tables"]

    def test_init_database_idempotent(self, temp_db):
        """测试多次初始化不会出错。"""
        init_database(temp_db)
        init_database(temp_db)
        stats = verify_database(temp_db)
        assert stats["exists"] is True

    def test_verify_database_nonexistent(self, tmp_path):
        """测试验证不存在的数据库。"""
        stats = verify_database(tmp_path / "nonexistent.duckdb")
        assert stats["exists"] is False

    def test_database_tables_initially_empty(self, temp_db):
        """测试初始化后表为空。"""
        stats = verify_database(temp_db)
        assert stats["factor_catalog_count"] == 0
        assert stats["factor_evaluations_count"] == 0
        assert stats["factor_versions_count"] == 0
        assert stats["factor_correlations_count"] == 0


# ─── CRUD 测试 ────────────────────────────────────────


class TestCRUD:
    """因子 CRUD 操作测试。"""

    def test_create_factor(self, repo, sample_factor):
        """测试创建因子。"""
        factor_id = repo.create_factor(sample_factor)
        assert factor_id.startswith("fct_")

        fetched = repo.get_factor(factor_id)
        assert fetched is not None
        assert fetched["name"] == sample_factor["name"]
        assert fetched["sharpe"] == sample_factor["sharpe"]

    def test_create_factor_with_custom_id(self, repo, sample_factor):
        """测试使用自定义 ID 创建因子。"""
        sample_factor["factor_id"] = "custom_id_001"
        factor_id = repo.create_factor(sample_factor)
        assert factor_id == "custom_id_001"

    def test_get_factor_nonexistent(self, repo):
        """测试获取不存在的因子。"""
        result = repo.get_factor("nonexistent_id")
        assert result is None

    def test_get_factor_by_name(self, repo, sample_factor):
        """测试按名称获取因子。"""
        repo.create_factor(sample_factor)
        result = repo.get_factor_by_name(sample_factor["name"])
        assert result is not None
        assert result["name"] == sample_factor["name"]

    def test_update_factor(self, repo, sample_factor):
        """测试更新因子。"""
        factor_id = repo.create_factor(sample_factor)
        success = repo.update_factor(factor_id, {"sharpe": 3.0, "ic": 0.1})
        assert success is True

        fetched = repo.get_factor(factor_id)
        assert fetched["sharpe"] == 3.0
        assert fetched["ic"] == 0.1

    def test_update_factor_no_changes(self, repo, sample_factor):
        """测试空更新。"""
        factor_id = repo.create_factor(sample_factor)
        success = repo.update_factor(factor_id, {})
        assert success is False

    def test_delete_factor_soft(self, repo, sample_factor):
        """测试软删除因子。"""
        factor_id = repo.create_factor(sample_factor)
        repo.delete_factor(factor_id)

        fetched = repo.get_factor(factor_id)
        assert fetched["status"] == "deleted"

    def test_create_factor_creates_version(self, repo, sample_factor):
        """测试创建因子时自动创建版本记录。"""
        factor_id = repo.create_factor(sample_factor)
        versions = repo.get_versions(factor_id)
        assert len(versions) == 1
        assert versions[0]["change_type"] == "create"

    def test_create_factor_creates_evaluation(self, repo, sample_factor):
        """测试创建因子时评估记录不存在（需手动添加）。"""
        factor_id = repo.create_factor(sample_factor)
        evaluations = repo.get_evaluations(factor_id)
        assert len(evaluations) == 0


# ─── 列表查询测试 ──────────────────────────────────────


class TestListQueries:
    """因子列表查询测试。"""

    def test_list_factors_empty(self, repo):
        """测试空数据库列表查询。"""
        factors = repo.list_factors()
        assert factors == []

    def test_list_factors_all(self, repo, sample_factors_batch):
        """测试查询所有因子。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        factors = repo.list_factors()
        assert len(factors) == 10

    def test_list_factors_by_market(self, repo, sample_factors_batch):
        """测试按市场筛选。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        stock_factors = repo.list_factors(market="stock")
        futures_factors = repo.list_factors(market="futures")
        assert len(stock_factors) == 5
        assert len(futures_factors) == 5

    def test_list_factors_by_status(self, repo, sample_factors_batch):
        """测试按状态筛选。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        active = repo.list_factors(status="active")
        failed = repo.list_factors(status="failed")
        assert len(active) == 8
        assert len(failed) == 2

    def test_list_factors_by_elite(self, repo, sample_factors_batch):
        """测试按精英状态筛选。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        elite = repo.list_factors(is_elite=True)
        non_elite = repo.list_factors(is_elite=False)
        assert len(elite) == 6
        assert len(non_elite) == 4

    def test_list_factors_with_min_sharpe(self, repo, sample_factors_batch):
        """测试最小 Sharpe 筛选。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        factors = repo.list_factors(min_sharpe=2.0)
        assert len(factors) >= 1
        for f in factors:
            assert f["sharpe"] >= 2.0

    def test_list_factors_pagination(self, repo, sample_factors_batch):
        """测试分页。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        page1 = repo.list_factors(limit=3, offset=0)
        page2 = repo.list_factors(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 3

    def test_list_factors_sort_by_sharpe(self, repo, sample_factors_batch):
        """测试按 Sharpe 排序。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        factors = repo.list_factors(sort_by="sharpe", sort_order="desc")
        for i in range(len(factors) - 1):
            assert factors[i]["sharpe"] >= factors[i + 1]["sharpe"]

    def test_list_factors_invalid_sort_fallback(self, repo, sample_factor):
        """测试无效排序字段回退。"""
        repo.create_factor(sample_factor)
        factors = repo.list_factors(sort_by="invalid_field")
        assert len(factors) == 1


# ─── 统计与搜索测试 ──────────────────────────────────


class TestStatsAndSearch:
    """统计与搜索测试。"""

    def test_count_factors(self, repo, sample_factors_batch):
        """测试因子计数。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        count = repo.count_factors()
        assert count == 10

    def test_count_factors_by_market(self, repo, sample_factors_batch):
        """测试按市场计数。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        stock_count = repo.count_factors(market="stock")
        futures_count = repo.count_factors(market="futures")
        assert stock_count == 5
        assert futures_count == 5

    def test_get_top_factors(self, repo, sample_factors_batch):
        """测试获取 Top N 因子。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        top5 = repo.get_top_factors(n=5, by="sharpe")
        assert len(top5) == 5
        for i in range(len(top5) - 1):
            assert top5[i]["sharpe"] >= top5[i + 1]["sharpe"]

    def test_get_top_factors_with_market_filter(self, repo, sample_factors_batch):
        """测试 Top N 带市场筛选。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        top = repo.get_top_factors(n=3, market="stock")
        assert len(top) == 3
        for f in top:
            assert f["market"] == "stock"

    def test_search_factors(self, repo, sample_factors_batch):
        """测试因子搜索。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        results = repo.search_factors("factor_1")
        assert len(results) >= 1

    def test_search_factors_no_results(self, repo, sample_factor):
        """测试搜索无结果。"""
        repo.create_factor(sample_factor)
        results = repo.search_factors("nonexistent_keyword_xyz")
        assert len(results) == 0

    def test_get_stats(self, repo, sample_factors_batch):
        """测试获取统计信息。"""
        for f in sample_factors_batch:
            repo.create_factor(f)
        stats = repo.get_stats()
        assert stats["total_factors"] == 10
        assert stats["active_factors"] == 8
        assert stats["elite_factors"] == 6
        assert stats["avg_sharpe"] > 0
        assert stats["avg_ic"] > 0


# ─── 版本管理测试 ──────────────────────────────────


class TestVersionManagement:
    """版本管理测试。"""

    def test_get_versions(self, repo, sample_factor):
        """测试获取版本历史。"""
        factor_id = repo.create_factor(sample_factor)
        versions = repo.get_versions(factor_id)
        assert len(versions) >= 1

    def test_rollback_to_version(self, repo, sample_factor):
        """测试回滚到指定版本。"""
        factor_id = repo.create_factor(sample_factor)
        sample_factor["code"]

        # 更新因子
        repo.update_factor(factor_id, {"code": "def compute(): return modified", "code_hash": "hash2"})

        # 获取版本并回滚
        versions = repo.get_versions(factor_id)
        assert len(versions) >= 1
        success = repo.rollback_to_version(factor_id, versions[0]["version_id"])
        assert success is True

    def test_rollback_nonexistent_version(self, repo, sample_factor):
        """测试回滚到不存在的版本。"""
        factor_id = repo.create_factor(sample_factor)
        success = repo.rollback_to_version(factor_id, "nonexistent_version_id")
        assert success is False


# ─── 评估管理测试 ──────────────────────────────────


class TestEvaluationManagement:
    """评估记录管理测试。"""

    def test_add_evaluation(self, repo, sample_factor):
        """测试添加评估记录。"""
        factor_id = repo.create_factor(sample_factor)
        eval_id = repo.add_evaluation(
            factor_id,
            {
                "ic": 0.06,
                "icir": 1.5,
                "sharpe": 2.8,
                "max_drawdown": 0.12,
                "turnover": 0.3,
                "overall_passed": True,
                "trace_id": "eval_trace_001",
            },
        )
        assert eval_id.startswith("eval_")

        evaluations = repo.get_evaluations(factor_id)
        assert len(evaluations) == 1
        assert evaluations[0]["level_1_sharpe"] == 2.8

    def test_add_multiple_evaluations(self, repo, sample_factor):
        """测试添加多条评估记录。"""
        factor_id = repo.create_factor(sample_factor)
        for i in range(3):
            repo.add_evaluation(
                factor_id,
                {
                    "sharpe": 1.0 + i,
                    "overall_passed": True,
                },
            )
        evaluations = repo.get_evaluations(factor_id)
        assert len(evaluations) == 3

    def test_get_evaluations_limit(self, repo, sample_factor):
        """测试评估记录数量限制。"""
        factor_id = repo.create_factor(sample_factor)
        for i in range(5):
            repo.add_evaluation(factor_id, {"sharpe": float(i)})
        evaluations = repo.get_evaluations(factor_id, limit=2)
        assert len(evaluations) == 2


# ─── 上下文管理器测试 ──────────────────────────────


class TestContextManager:
    """上下文管理器测试。"""

    def test_context_manager(self, temp_db):
        """测试 with 语句支持。"""
        with FactorRepository(temp_db) as r:
            factor_id = r.create_factor(
                {
                    "name": "ctx_test",
                    "code": "return 1",
                }
            )
            assert factor_id is not None

    def test_context_manager_closes_connection(self, temp_db):
        """测试上下文管理器关闭连接。"""
        r = FactorRepository(temp_db)
        r.create_factor({"name": "test", "code": "1"})
        r.close()
        assert r._conn is None


# ─── 数据完整性测试 ──────────────────────────────


class TestDataIntegrity:
    """数据完整性测试。"""

    def test_factor_metadata_preserved(self, repo, sample_factor):
        """测试元数据保存完整。"""
        factor_id = repo.create_factor(sample_factor)
        fetched = repo.get_factor(factor_id)
        assert fetched["params"]["window"] == 20
        assert fetched["signature"]["input"] == ["close", "volume"]
        assert fetched["economic_logic"]["category"] == "technical"
        assert fetched["metadata"]["backtest_period"] == "2020-2025"

    def test_json_fields_handled_correctly(self, repo, sample_factor):
        """测试 JSON 字段正确处理。"""
        factor_id = repo.create_factor(sample_factor)
        fetched = repo.get_factor(factor_id)
        assert isinstance(fetched["params"], dict)
        assert isinstance(fetched["signature"], dict)

    def test_none_values_handled(self, repo, sample_factor):
        """测试 None 值处理。"""
        sample_factor["parent_id"] = None
        factor_id = repo.create_factor(sample_factor)
        fetched = repo.get_factor(factor_id)
        assert fetched["parent_id"] is None

    def test_string_timestamps(self, repo, sample_factor):
        """测试时间戳格式。"""
        factor_id = repo.create_factor(sample_factor)
        fetched = repo.get_factor(factor_id)
        assert fetched["created_at"] is not None
        assert fetched["updated_at"] is not None


# ─── 迁移脚本测试 ──────────────────────────────────


class TestMigration:
    """迁移脚本测试。"""

    def test_migrate_dry_run(self, tmp_path):
        """测试 dry-run 模式。"""
        from fts.factor_engine.factor_db.migrate_from_json import migrate_factors

        # 创建临时 elite 目录
        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()

        # 创建一个示例因子 JSON
        sample_json = {
            "factor_id": "migrate_test_001",
            "name": "migrate_factor",
            "code": "def compute(): return 1",
            "evaluation": {
                "level_1_backtest": {
                    "sharpe": 2.0,
                    "ic": 0.05,
                    "turnover_monthly": 0.3,
                },
                "passed": True,
            },
        }
        (elite_dir / "test_001.json").write_text(json.dumps(sample_json, indent=2), encoding="utf-8")

        db_path = tmp_path / "test_migrate.duckdb"
        stats = migrate_factors(elite_dir, db_path, dry_run=True)

        assert stats["total_files"] == 1
        assert stats["success"] == 1
        assert stats["failed"] == 0
        assert stats["skipped"] == 0

    def test_migrate_with_real_data(self, tmp_path):
        """测试使用真实数据迁移。"""
        from fts.factor_engine.factor_db.migrate_from_json import migrate_factors

        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()

        # 创建因子文件
        for i in range(3):
            data = {
                "factor_id": f"test_{i:03d}",
                "name": f"test_factor_{i}",
                "code": f"def compute_{i}(): return {i} * 2",
                "evaluation": {
                    "level_1_backtest": {
                        "sharpe": 1.0 + i,
                        "ic": 0.03 + i * 0.01,
                        "turnover_monthly": 0.2 + i * 0.05,
                    },
                    "level_2_economic": {
                        "theory": 7,
                        "behavioral": 6,
                        "microstructure": 5,
                        "institutional": 8,
                        "dimensions_passed": 3,
                    },
                    "level_3_multiple": {
                        "bonferroni_p": 0.01,
                        "fdr_q": 0.02,
                        "effective_n_factors": 3,
                        "adjusted_t": 2.5,
                        "passed": True,
                    },
                    "passed": True,
                },
                "source": "seed",
                "generation": 1,
                "decay_6m": 0.04,
            }
            (elite_dir / f"test_{i:03d}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

        db_path = tmp_path / "test_migrate.duckdb"
        stats = migrate_factors(elite_dir, db_path, force=True)

        assert stats["total_files"] == 3
        assert stats["success"] == 3
        assert stats["failed"] == 0

        # 验证数据
        repo = FactorRepository(db_path)
        all_factors = repo.list_factors()
        assert len(all_factors) == 3
        assert all(f["is_elite"] for f in all_factors)

        # 验证评估记录
        evaluations = repo.get_evaluations("test_000")
        assert len(evaluations) == 1
        assert evaluations[0]["level_1_sharpe"] == 1.0

        # 验证版本记录
        versions = repo.get_versions("test_000")
        assert len(versions) == 1

        repo.close()

    def test_migrate_skips_metadata_files(self, tmp_path):
        """测试跳过元数据文件（以 _ 开头）。"""
        from fts.factor_engine.factor_db.migrate_from_json import migrate_factors

        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()

        # 创建元数据文件（应被跳过）
        meta_data = {"index": True, "count": 100}
        (elite_dir / "_elite_index.json").write_text(json.dumps(meta_data), encoding="utf-8")

        # 创建正常因子文件
        factor_data = {
            "factor_id": "real_factor",
            "name": "real",
            "code": "return 1",
            "evaluation": {
                "level_1_backtest": {"sharpe": 1.0, "ic": 0.05},
                "passed": True,
            },
        }
        (elite_dir / "real.json").write_text(json.dumps(factor_data), encoding="utf-8")

        db_path = tmp_path / "test_migrate.duckdb"
        stats = migrate_factors(elite_dir, db_path, force=True)

        # 只有 real.json 被处理
        assert stats["total_files"] == 1
        assert stats["success"] == 1

    def test_migrate_invalid_json_handled(self, tmp_path):
        """测试无效 JSON 文件处理。"""
        from fts.factor_engine.factor_db.migrate_from_json import migrate_factors

        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()

        # 创建无效 JSON
        (elite_dir / "invalid.json").write_text("not valid json{{{", encoding="utf-8")

        # 创建有效 JSON
        valid_data = {
            "factor_id": "valid_factor",
            "name": "valid",
            "code": "return 1",
            "evaluation": {
                "level_1_backtest": {"sharpe": 1.0},
                "passed": True,
            },
        }
        (elite_dir / "valid.json").write_text(json.dumps(valid_data), encoding="utf-8")

        db_path = tmp_path / "test_migrate.duckdb"
        stats = migrate_factors(elite_dir, db_path, force=True)

        assert stats["total_files"] == 2
        assert stats["success"] == 1
        assert stats["failed"] == 1

    def test_migrate_idempotent(self, tmp_path):
        """测试重复迁移幂等性。"""
        from fts.factor_engine.factor_db.migrate_from_json import migrate_factors

        elite_dir = tmp_path / "elite"
        elite_dir.mkdir()

        data = {
            "factor_id": "idem_factor",
            "name": "idem",
            "code": "return 1",
            "evaluation": {
                "level_1_backtest": {"sharpe": 1.0},
                "passed": True,
            },
        }
        (elite_dir / "idem.json").write_text(json.dumps(data), encoding="utf-8")

        db_path = tmp_path / "test_migrate.duckdb"

        # 第一次迁移
        stats1 = migrate_factors(elite_dir, db_path)
        assert stats1["success"] == 1
        assert stats1["skipped"] == 0

        # 第二次迁移（跳过已存在）
        stats2 = migrate_factors(elite_dir, db_path)
        assert stats2["success"] == 0
        assert stats2["skipped"] == 1

        # 强制迁移
        stats3 = migrate_factors(elite_dir, db_path, force=True)
        assert stats3["success"] == 1
        assert stats3["skipped"] == 0


# ─── 边界条件测试 ──────────────────────────────────


class TestEdgeCases:
    """边界条件测试。"""

    def test_empty_string_code_hash(self, repo, sample_factor):
        """测试空字符串代码的哈希处理。"""
        sample_factor["code"] = ""
        factor_id = repo.create_factor(sample_factor)
        fetched = repo.get_factor(factor_id)
        assert fetched["code_hash"] == ""

    def test_very_long_factor_name(self, repo, sample_factor):
        """测试超长因子名。"""
        sample_factor["name"] = "A" * 500
        factor_id = repo.create_factor(sample_factor)
        fetched = repo.get_factor(factor_id)
        assert len(fetched["name"]) == 500

    def test_special_characters_in_name(self, repo, sample_factor):
        """测试特殊字符名称。"""
        sample_factor["name"] = "factor_αβγ_<>!@#$%"
        factor_id = repo.create_factor(sample_factor)
        fetched = repo.get_factor(factor_id)
        assert fetched["name"] == sample_factor["name"]

    def test_zero_values(self, repo, sample_factor):
        """测试零值处理。"""
        sample_factor["sharpe"] = 0.0
        sample_factor["ic"] = 0.0
        factor_id = repo.create_factor(sample_factor)
        fetched = repo.get_factor(factor_id)
        assert fetched["sharpe"] == 0.0
        assert fetched["ic"] == 0.0

    def test_negative_values(self, repo, sample_factor):
        """测试负值处理。"""
        sample_factor["sharpe"] = -1.5
        sample_factor["max_drawdown"] = -0.2
        factor_id = repo.create_factor(sample_factor)
        fetched = repo.get_factor(factor_id)
        assert fetched["sharpe"] == -1.5
        assert fetched["max_drawdown"] == -0.2


# ─── Repository 初始化测试 ──────────────────────────


class TestRepositoryInit:
    """Repository 初始化测试。"""

    @pytest.mark.uses_real_factor_db  # GAP-129: 真实默认库路由断言
    def test_default_database_path(self):
        """测试默认数据库路径（v2.104.0+103 全局默认市场 futures→energy）。"""
        from fts.factor_engine.factor_db.schema import DATABASE_PATH_ENERGY, DATABASE_PATH_FUTURES

        repo = FactorRepository()
        assert repo._db_path == DATABASE_PATH_ENERGY
        repo.close()

        repo_futures = FactorRepository(market="futures")
        assert repo_futures._db_path == DATABASE_PATH_FUTURES
        repo_futures.close()

    def test_custom_database_path(self, temp_db):
        """测试自定义数据库路径。"""
        repo = FactorRepository(temp_db)
        assert repo._db_path == temp_db
        repo.close()

    def test_lazy_connection(self, temp_db):
        """测试懒加载连接。"""
        repo = FactorRepository(temp_db)
        assert repo._conn is None
        repo.create_factor({"name": "test", "code": "1"})
        assert repo._conn is not None
        repo.close()


# ─── 高级查询 API 测试 (因子挖掘) ──────────────────────────


class TestAdvancedQueries:
    """高级查询 API 测试。"""

    def test_get_eligible(self, repo, sample_factors_batch):
        """测试筛选合格因子。"""
        for f in sample_factors_batch:
            repo.create_factor(f)

        # 获取合格因子
        eligible = repo.get_eligible(market="stock", min_ic=0.02)
        assert len(eligible) > 0
        for f in eligible:
            assert f["market"] == "stock"
            assert f["ic"] >= 0.02
            assert f["status"] == "active"

    def test_get_eligible_with_filters(self, repo, sample_factors_batch):
        """测试筛选合格因子带多条件。"""
        for f in sample_factors_batch:
            f["is_elite"] = True
            repo.create_factor(f)

        # 只返回精英因子
        eligible = repo.get_eligible(market="stock", min_sharpe=1.5, require_elite=True)
        for f in eligible:
            assert f["market"] == "stock"
            assert f["sharpe"] >= 1.5
            assert f["is_elite"] is True

    @patch("fts.factor_engine.factor_clustering.cluster_factors_by_signal")
    def test_get_diverse_factors(self, mock_cluster, repo, sample_factors_batch):
        """测试因子多样性选择（按信号聚类簇配额）。"""
        for f in sample_factors_batch:
            f["is_elite"] = True
            f["status"] = "active"
            repo.create_factor(f)

        captured: dict = {}

        def _fake_cluster(code_factors, **kwargs):
            fids = [f["factor_id"] for f in code_factors]
            result = {
                "assign": {fid: i % 3 for i, fid in enumerate(fids)},
                "cluster_order": [0, 1, 2],
                "cluster_members": {c: [fid for i, fid in enumerate(fids) if i % 3 == c] for c in range(3)},
            }
            captured["result"] = result
            return result

        mock_cluster.side_effect = _fake_cluster

        # 获取多样化因子（eligible=5 > total_count=4 → 触发簇配额选择）
        diverse = repo.get_diverse_factors(
            market="stock",
            total_count=4,
            max_per_cluster=2,
        )
        assert len(diverse) <= 4

        # 检查簇多样性：每个信号簇最多 2 个
        assign = captured["result"]["assign"]
        cluster_counts: dict[int, int] = {}
        for f in diverse:
            cid = assign.get(f["factor_id"])
            cluster_counts[cid] = cluster_counts.get(cid, 0) + 1
        for count in cluster_counts.values():
            assert count <= 2

    def test_get_diverse_factors_small_pool(self, repo, sample_factors_batch):
        """测试小因子池下的多样性选择。"""
        for f in sample_factors_batch[:3]:  # 只有 3 个因子
            repo.create_factor(f)

        diverse = repo.get_diverse_factors(total_count=10)
        # 因子不足时返回全部
        assert len(diverse) <= 3

    def test_get_factor_lineage(self, repo, sample_factor):
        """测试获取因子演化谱系。"""
        # 创建父因子
        parent_id = repo.create_factor(
            {
                **sample_factor,
                "name": "parent_factor",
                "generation": 1,
            }
        )

        # 创建子因子
        child_id = repo.create_factor(
            {
                **sample_factor,
                "name": "child_factor",
                "parent_id": parent_id,
                "generation": 2,
            }
        )

        # 获取子因子谱系
        lineage = repo.get_factor_lineage(child_id)
        assert lineage is not None
        assert lineage["factor_id"] == child_id
        assert lineage["parent_id"] == parent_id
        assert "parent" in lineage
        assert lineage["parent"]["factor_id"] == parent_id

    def test_get_factor_lineage_no_parent(self, repo, sample_factor):
        """测试无父因子的谱系查询。"""
        factor_id = repo.create_factor(sample_factor)
        lineage = repo.get_factor_lineage(factor_id)
        assert lineage is not None
        assert "parent" not in lineage

    def test_get_factor_lineage_nonexistent(self, repo):
        """测试不存在因子的谱系查询。"""
        lineage = repo.get_factor_lineage("nonexistent_id")
        assert lineage is None
