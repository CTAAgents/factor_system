"""GAP-129 测试因子库隔离验证。

根 conftest.py 的 autouse fixture `_isolated_factor_db` 将
`fts.factor_engine.factor_db.schema.get_db_path` 单挂载点重定向至每测试独立
tmp DuckDB。本文件验证:

1. 隔离生效：未标记测试中 get_db_path / 仓储 db_path 全部落在 tmp 目录，
   futures/energy 分库路由语义保留（仅文件位置变化）
2. 零污染：隔离库写入三表后，真实库三表 COUNT 与基线一致
3. 豁免生效：`@pytest.mark.uses_real_factor_db` 测试仍路由真实库路径
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fts.factor_engine.factor_db.schema as schema  # noqa: E402
from fts.factor_engine.factor_db.repository import (  # noqa: E402
    FactorAuditReportRepository,
    FactorQualityScoreRepository,
    FactorRepository,
)


def _table_counts(db_path: Path) -> dict[str, int]:
    """读取因子库三表行数（factor_catalog / factor_quality_scores / factor_audit_reports）。"""
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        return {
            "catalog": conn.execute("SELECT COUNT(*) FROM factor_catalog").fetchone()[0],
            "quality": conn.execute("SELECT COUNT(*) FROM factor_quality_scores").fetchone()[0],
            "audit": conn.execute("SELECT COUNT(*) FROM factor_audit_reports").fetchone()[0],
        }
    finally:
        conn.close()


class TestIsolationActive:
    """未标记测试：隔离应生效。"""

    def test_get_db_path_redirected_to_tmp(self, tmp_path) -> None:
        """get_db_path 重定向至 tmp，且 futures/energy 分库路由语义保留。"""
        futures_path = schema.get_db_path("futures")
        energy_path = schema.get_db_path("energy")

        assert futures_path.is_relative_to(tmp_path)
        assert energy_path.is_relative_to(tmp_path)
        # 分库映射保留：文件名与真实库一致，仅位置迁移
        assert futures_path.name == schema.DATABASE_PATH_FUTURES.name
        assert energy_path.name == schema.DATABASE_PATH_ENERGY.name
        assert futures_path != schema.DATABASE_PATH_FUTURES
        assert energy_path != schema.DATABASE_PATH_ENERGY

    def test_repository_uses_isolated_db(self, tmp_path) -> None:
        """4 仓储类经 get_db_path 局部导入，构造时应拿到 tmp 隔离路径。"""
        with FactorRepository(market="futures") as repo:
            assert repo._db_path.is_relative_to(tmp_path)
        with FactorQualityScoreRepository(market="futures") as qrepo:
            assert qrepo._db_path.is_relative_to(tmp_path)
        arepo = FactorAuditReportRepository(market="futures")  # 不支持 with（GAP-128）
        try:
            assert arepo._db_path.is_relative_to(tmp_path)
        finally:
            arepo.close()

    def test_repository_default_market_follows_global(self, tmp_path, monkeypatch) -> None:
        """FactorRepository 未显式指定 market 时跟随全局 FTS_DEFAULT_MARKET（v2.104.0+101）。"""
        from unittest.mock import patch as mpatch

        with mpatch("fts.config.get_config") as mock_cfg:
            mock_cfg.return_value = SimpleNamespace(default_market="energy")
            with FactorRepository() as repo:
                assert repo._db_path.name == schema.DATABASE_PATH_ENERGY.name
                assert repo._db_path.is_relative_to(tmp_path)
        with mpatch("fts.config.get_config") as mock_cfg:
            mock_cfg.return_value = SimpleNamespace(default_market="futures")
            with FactorRepository() as repo:
                assert repo._db_path.name == schema.DATABASE_PATH_FUTURES.name

    def test_promotion_writes_do_not_touch_real_db(self) -> None:
        """模拟晋升写入（factor_catalog + quality + audit 三表）仅落隔离库，真实库 COUNT 不变。"""
        real_db = schema.DATABASE_PATH_FUTURES
        if not real_db.exists():
            pytest.skip("真实因子库不存在（无 data/factor_catalog_futures.duckdb）")
        baseline = _table_counts(real_db)

        factor_id = f"fct_iso_{uuid.uuid4().hex[:6]}"
        with FactorRepository(market="futures") as repo:
            repo.create_factor(
                {
                    "factor_id": factor_id,
                    "name": "iso_test",
                    "code": "def factor_program(close, params):\n    return close",
                    "status": "active",
                    "is_elite": True,
                }
            )
        with FactorQualityScoreRepository(market="futures") as qrepo:
            qrepo.save_score(
                {"factor_id": factor_id, "total_score": 60.0, "grade": "C", "dimension_scores": []}
            )
        arepo = FactorAuditReportRepository(market="futures")
        try:
            arepo.save_report(
                {
                    "factor_id": factor_id,
                    "passed": True,
                    "overall_score": 0.8,
                    "total_checks": 3,
                    "passed_checks": 3,
                }
            )
        finally:
            arepo.close()

        # 隔离库内三表写入生效（防"写入失败致真实库未变"的假阳性）
        iso_counts = _table_counts(schema.get_db_path("futures"))
        assert iso_counts["catalog"] >= 1
        assert iso_counts["quality"] >= 1
        assert iso_counts["audit"] >= 1

        # 真实库三表 COUNT 与基线完全一致（零污染）
        assert _table_counts(real_db) == baseline


class TestExemption:
    """豁免标记测试：仍路由真实库。"""

    @pytest.mark.uses_real_factor_db  # GAP-129: 豁免验证
    def test_exempt_marker_uses_real_db_path(self) -> None:
        """命中 uses_real_factor_db 时 get_db_path 返回真实默认库路径。"""
        assert schema.get_db_path("futures") == schema.DATABASE_PATH_FUTURES
        assert schema.get_db_path("energy") == schema.DATABASE_PATH_ENERGY
