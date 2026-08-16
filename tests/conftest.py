"""tests/conftest.py — 根级 pytest 配置：测试因子库隔离（GAP-129）。

背景: EvolutionLoop/各因子库仓储未显式传 `db_path` 时经
`fts.factor_engine.factor_db.schema.get_db_path(market)` 路由到真实
`data/factor_catalog_{futures,energy}.duckdb`，测试组（test_evolution_loop 等）
运行即写入真实库，污染 L3 因子资产库 SSOT（GAP-129）。

本文件提供 autouse fixture `_isolated_factor_db`：在单挂载点
`schema.get_db_path` 处将全部市场重定向至每测试独立 tmp DuckDB——
4 个仓储类（FactorRepository/FactorQualityScoreRepository/
FactorStatusRepository/FactorAuditReportRepository）构造时均局部
`from .schema import get_db_path`（调用时解析模块符号），替换该符号即全量生效；
仓储连接时自动 init_database 幂等建表，无需手动初始化。
显式传 `db_path=` 的测试不调用 get_db_path，不受影响。

真实路由断言/真实数据依赖测试用 `@pytest.mark.uses_real_factor_db` 豁免。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_FTS_ROOT = Path(__file__).resolve().parents[1]
if str(_FTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_FTS_ROOT))


def pytest_configure(config) -> None:
    """注册 uses_real_factor_db 标记（GAP-129 豁免机制）。"""
    config.addinivalue_line(
        "markers",
        "uses_real_factor_db: 允许该测试访问真实因子库 data/factor_catalog_*.duckdb "
        "（豁免 GAP-129 测试因子库隔离；仅限真实路由断言/真实数据依赖测试）",
    )


@pytest.fixture(autouse=True)
def _isolated_factor_db(request, monkeypatch, tmp_path) -> None:
    """GAP-129 测试因子库隔离：get_db_path 全局重定向至每测试独立 tmp DuckDB。

    命中 uses_real_factor_db 标记的测试豁免（仍路由真实库）。
    """
    if request.node.get_closest_marker("uses_real_factor_db"):
        yield
        return

    import fts.factor_engine.factor_db.schema as schema

    db_dir = tmp_path / "factor_db"
    db_dir.mkdir(exist_ok=True)

    real_names = {
        "futures": schema.DATABASE_PATH_FUTURES.name,
        "energy": schema.DATABASE_PATH_ENERGY.name,
    }

    def _isolated_get_db_path(market: str = "futures") -> Path:
        return db_dir / real_names.get(market, f"factor_catalog_{market}.duckdb")

    monkeypatch.setattr(schema, "get_db_path", _isolated_get_db_path)
    yield
