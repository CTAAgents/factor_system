"""factor_db/__init__.py — DuckDB 因子目录数据库

提供精英因子的持久化存储层，替代原 JSON 文件存储。
支持高效查询、版本管理、去重和批量操作。

版本: v1.1 (Phase 3 读写链路改造)
"""

from .schema import init_database, verify_database, DATABASE_PATH, DATABASE_PATH_FUTURES, get_db_path
from .repository import (
    FactorRepository,
    FactorQualityScoreRepository,
    FactorStatusRepository,
    FactorAuditReportRepository,
)
from .lineage import FactorLineage

__all__ = [
    "init_database",
    "verify_database",
    "DATABASE_PATH",
    "DATABASE_PATH_FUTURES",
    "get_db_path",
    "FactorRepository",
    "FactorQualityScoreRepository",
    "FactorStatusRepository",
    "FactorAuditReportRepository",
    "FactorLineage",
]
