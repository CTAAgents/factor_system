"""factor_db/schema.py — DuckDB 表结构定义与初始化

精英因子数据库 Schema:
1. factor_catalog      — 因子主表（当前活跃版本）
2. factor_evaluations  — 因子评估历史
3. factor_versions     — 因子代码版本历史
4. factor_correlations — 因子间相关性矩阵

设计原则:
- 因子主表存储当前最优版本的完整信息
- 评估表支持历史回溯和趋势分析
- 版本表追踪代码变更，支持回滚
- 相关性表支持组合构建时的去冗余

版本: v1.0
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ─── 数据库路径 ──────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "factor_catalog.duckdb"


# ─── DDL 语句 ─────────────────────────────────────────────

_CREATE_FACTOR_CATALOG = """
CREATE TABLE IF NOT EXISTS factor_catalog (
    factor_id       VARCHAR PRIMARY KEY,
    name            VARCHAR NOT NULL,
    code            TEXT NOT NULL,
    code_hash       VARCHAR NOT NULL,
    params          JSON,
    signature       JSON,
    economic_logic  JSON,
    source          VARCHAR DEFAULT 'seed',
    parent_id       VARCHAR,
    generation      INTEGER DEFAULT 0,
    trace_id        VARCHAR,
    sharpe          DOUBLE DEFAULT 0.0,
    ic              DOUBLE DEFAULT 0.0,
    icir            DOUBLE DEFAULT 0.0,
    max_drawdown    DOUBLE DEFAULT 0.0,
    turnover_monthly DOUBLE DEFAULT 0.0,
    decay_6m        DOUBLE DEFAULT 0.05,
    status          VARCHAR DEFAULT 'active',
    market          VARCHAR NOT NULL DEFAULT 'stock',
    family          VARCHAR NOT NULL DEFAULT 'other',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_elite        BOOLEAN DEFAULT FALSE,
    metadata        JSON
);

CREATE INDEX IF NOT EXISTS idx_factor_catalog_name 
    ON factor_catalog(name);
CREATE INDEX IF NOT EXISTS idx_factor_catalog_status 
    ON factor_catalog(status);
CREATE INDEX IF NOT EXISTS idx_factor_catalog_market 
    ON factor_catalog(market);
CREATE INDEX IF NOT EXISTS idx_factor_catalog_sharpe 
    ON factor_catalog(sharpe DESC);
"""

_CREATE_FACTOR_EVALUATIONS = """
CREATE TABLE IF NOT EXISTS factor_evaluations (
    eval_id         VARCHAR PRIMARY KEY,
    factor_id       VARCHAR NOT NULL,
    trace_id        VARCHAR,
    level_1_ic      DOUBLE,
    level_1_icir    DOUBLE,
    level_1_sharpe  DOUBLE,
    level_1_max_dd   DOUBLE,
    level_1_turnover DOUBLE,
    level_1_t_stat   DOUBLE,
    level_1_monotonicity BOOLEAN,
    level_1_oos_ratio DOUBLE,
    level_2_theory_score INTEGER,
    level_2_behavioral_score INTEGER,
    level_2_microstructure_score INTEGER,
    level_2_institutional_score INTEGER,
    level_2_dims_passed INTEGER,
    level_3_bonferroni_p DOUBLE,
    level_3_fdr_q    DOUBLE,
    level_3_effective_n INTEGER,
    level_3_adjusted_t DOUBLE,
    level_3_passed   BOOLEAN DEFAULT FALSE,
    overall_passed   BOOLEAN DEFAULT FALSE,
    failure_reasons  JSON,
    evaluated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_evals_factor_id 
    ON factor_evaluations(factor_id);
CREATE INDEX IF NOT EXISTS idx_evals_sharpe 
    ON factor_evaluations(level_1_sharpe DESC);
CREATE INDEX IF NOT EXISTS idx_evals_evaluated_at 
    ON factor_evaluations(evaluated_at DESC);
CREATE INDEX IF NOT EXISTS idx_evals_passed 
    ON factor_evaluations(overall_passed);
"""

_CREATE_FACTOR_VERSIONS = """
CREATE TABLE IF NOT EXISTS factor_versions (
    version_id      VARCHAR PRIMARY KEY,
    factor_id       VARCHAR NOT NULL,
    code            TEXT NOT NULL,
    code_hash       VARCHAR NOT NULL,
    version_number   INTEGER DEFAULT 1,
    change_type     VARCHAR DEFAULT 'create',
    change_summary   VARCHAR,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by      VARCHAR DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_versions_factor_id 
    ON factor_versions(factor_id);
CREATE INDEX IF NOT EXISTS idx_versions_created_at 
    ON factor_versions(created_at DESC);
"""

_CREATE_FACTOR_CORRELATIONS = """
CREATE TABLE IF NOT EXISTS factor_correlations (
    correlation_id  VARCHAR PRIMARY KEY,
    factor_id_a     VARCHAR NOT NULL,
    factor_id_b     VARCHAR NOT NULL,
    pearson_corr    DOUBLE,
    spearman_corr   DOUBLE,
    sample_size     INTEGER,
    computed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_corr_factor_a 
    ON factor_correlations(factor_id_a);
CREATE INDEX IF NOT EXISTS idx_corr_factor_b 
    ON factor_correlations(factor_id_b);
CREATE INDEX IF NOT EXISTS idx_corr_pearson 
    ON factor_correlations(pearson_corr DESC);
"""


# ─── 初始化函数 ──────────────────────────────────────────

def init_database(db_path: Optional[Path] = None) -> Path:
    """初始化因子目录数据库，创建所有表和索引。

    Args:
        db_path: 数据库文件路径，默认使用 DATABASE_PATH

    Returns:
        实际使用的数据库路径
    """
    import duckdb

    path = Path(db_path) if db_path else DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("[FactorDB] 初始化数据库: %s", path)
    conn = duckdb.connect(str(path))

    try:
        conn.execute(_CREATE_FACTOR_CATALOG)
        conn.execute(_CREATE_FACTOR_EVALUATIONS)
        conn.execute(_CREATE_FACTOR_VERSIONS)
        conn.execute(_CREATE_FACTOR_CORRELATIONS)

        conn.execute("CHECKPOINT")
        logger.info("[FactorDB] ✅ 数据库初始化完成")

        # 验证表结构
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        logger.info("[FactorDB] 已创建表: %s", [t[0] for t in tables])

    finally:
        conn.close()

    return path


def get_connection(db_path: Optional[Path] = None):
    """获取数据库连接。

    Args:
        db_path: 数据库文件路径

    Returns:
        duckdb 连接对象（调用方负责关闭）
    """
    import duckdb

    path = Path(db_path) if db_path else DATABASE_PATH
    return duckdb.connect(str(path))


def verify_database(db_path: Optional[Path] = None) -> dict:
    """验证数据库完整性，返回统计信息。

    Args:
        db_path: 数据库文件路径

    Returns:
        统计信息字典
    """
    import duckdb

    path = Path(db_path) if db_path else DATABASE_PATH
    if not path.exists():
        return {"exists": False}

    conn = duckdb.connect(str(path), read_only=True)
    try:
        stats = {"exists": True, "path": str(path)}

        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        stats["tables"] = [t[0] for t in tables]

        for table in stats["tables"]:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[f"{table}_count"] = count

        # 因子质量统计
        if "factor_catalog" in stats["tables"]:
            elite_count = conn.execute(
                "SELECT COUNT(*) FROM factor_catalog WHERE is_elite = TRUE"
            ).fetchone()[0]
            active_count = conn.execute(
                "SELECT COUNT(*) FROM factor_catalog WHERE status = 'active'"
            ).fetchone()[0]
            avg_sharpe = conn.execute(
                "SELECT AVG(sharpe) FROM factor_catalog WHERE sharpe > 0"
            ).fetchone()[0]
            stats["elite_count"] = elite_count
            stats["active_count"] = active_count
            stats["avg_sharpe"] = round(avg_sharpe, 3) if avg_sharpe else 0.0

        return stats

    finally:
        conn.close()
