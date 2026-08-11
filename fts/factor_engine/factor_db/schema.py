"""factor_db/schema.py — DuckDB 表结构定义与初始化

精英因子数据库 Schema:
1. factor_catalog        — 因子主表（当前活跃版本）
2. factor_evaluations    — 因子评估历史
3. factor_versions       — 因子代码版本历史
4. factor_correlations   — 因子间相关性矩阵
5. factor_quality_scores — 因子质量评分卡（A.1）
6. factor_status_history — 因子生命周期状态变迁历史（A.2）
7. factor_audit_reports  — 因子审计报告（B.3）
8. feedback_events       — 反馈事件（C.3）
9. attribution_reports   — 归因分析报告（C.3）
10. feedback_processing_results — 反馈处理结果（C.3）
11. feedback_reports     — 迭代效果月度报告（C.3）
12. seed_lineage          — L0→L2 种子溯源链路（L0/L1/L2 统一管理辅助）

设计原则:
- 因子主表存储当前最优版本的完整信息
- 评估表支持历史回溯和趋势分析
- 版本表追踪代码变更，支持回滚
- 相关性表支持组合构建时的去冗余
- 质量评分/状态历史/审计报告支持分级准入与生命周期管理
- 反馈系列表支撑"因子表现→归因→演化方向调整"闭环
- seed_lineage 表打通 L0→L2 全链路，记录种子因子到精英因子的演化路径

版本: v1.2
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
DATABASE_PATH_STOCK = DATA_DIR / "factor_catalog_stock.duckdb"
DATABASE_PATH_FUTURES = DATA_DIR / "factor_catalog_futures.duckdb"


def get_db_path(market: str = "stock") -> Path:
    """按市场返回因子目录数据库路径。

    Args:
        market: "stock" 或 "futures"

    Returns:
        对应的 DuckDB 文件路径
    """
    if market == "futures":
        return DATABASE_PATH_FUTURES
    return DATABASE_PATH_STOCK


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
    status_updated_at TIMESTAMP,
    consecutive_ic_negative_months INTEGER DEFAULT 0,
    consecutive_sharpe_drop_months INTEGER DEFAULT 0,
    last_incremental_eval_at TIMESTAMP,
    decay_rate_3m    DOUBLE DEFAULT 0.0,
    decay_rate_6m    DOUBLE DEFAULT 0.0,
    market          VARCHAR NOT NULL DEFAULT 'stock',
    family          VARCHAR NOT NULL DEFAULT 'other',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_elite        BOOLEAN DEFAULT FALSE,
    metadata        JSON,
    style_tags      JSON
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


# ─── A.1: 因子质量评分卡 ────────────────────────────────────

_CREATE_FACTOR_QUALITY_SCORES = """
CREATE TABLE IF NOT EXISTS factor_quality_scores (
    score_id        VARCHAR PRIMARY KEY,
    factor_id       VARCHAR NOT NULL,
    total_score     DOUBLE NOT NULL DEFAULT 0,
    dimension_scores JSON NOT NULL,        -- 各维度明细 JSON
    grade           VARCHAR NOT NULL DEFAULT 'C',
    evaluated_at    TIMESTAMP NOT NULL,
    score_version   VARCHAR(20) NOT NULL DEFAULT 'v1',
    -- 关键维度快捷索引列
    ic_score        DOUBLE NOT NULL DEFAULT 0,
    sharpe_score    DOUBLE NOT NULL DEFAULT 0,
    stability_score DOUBLE NOT NULL DEFAULT 0,
    robustness_score DOUBLE NOT NULL DEFAULT 0,
    capacity_score  DOUBLE NOT NULL DEFAULT 0,
    tradability_score DOUBLE NOT NULL DEFAULT 0,
    diversity_score DOUBLE NOT NULL DEFAULT 0,
    logic_score     DOUBLE NOT NULL DEFAULT 0,
    timeliness_score DOUBLE NOT NULL DEFAULT 0,
    compatibility_score DOUBLE NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_fqs_factor_id
    ON factor_quality_scores(factor_id);
CREATE INDEX IF NOT EXISTS idx_fqs_total_score
    ON factor_quality_scores(total_score DESC);
CREATE INDEX IF NOT EXISTS idx_fqs_evaluated_at
    ON factor_quality_scores(evaluated_at);
"""


# ─── A.2: 因子状态变迁历史 ──────────────────────────────────

_CREATE_FACTOR_STATUS_HISTORY = """
CREATE TABLE IF NOT EXISTS factor_status_history (
    history_id      VARCHAR PRIMARY KEY,
    factor_id       VARCHAR NOT NULL,
    from_status     VARCHAR NOT NULL,
    to_status       VARCHAR NOT NULL,
    reason          VARCHAR NOT NULL,
    changed_at      TIMESTAMP NOT NULL,
    snapshot        JSON NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fsh_factor_id ON factor_status_history(factor_id);
CREATE INDEX IF NOT EXISTS idx_fsh_changed_at ON factor_status_history(changed_at);
"""


# ─── B.3: 因子审计报告 ─────────────────────────────────────

_CREATE_FACTOR_AUDIT_REPORTS = """
CREATE TABLE IF NOT EXISTS factor_audit_reports (
    report_id       VARCHAR PRIMARY KEY,
    factor_id       VARCHAR NOT NULL,
    factor_version_id VARCHAR,
    passed          BOOLEAN NOT NULL,
    overall_score   DOUBLE NOT NULL,
    total_checks    INT NOT NULL,
    passed_checks   INT NOT NULL,
    results_json    JSON NOT NULL,          -- AuditCheckResult 详情
    summary_json    JSON NOT NULL,          -- AuditSummary
    recommendations JSON,
    audited_at      TIMESTAMP NOT NULL,
    audit_version   VARCHAR(20) NOT NULL DEFAULT 'v1'
);

CREATE INDEX IF NOT EXISTS idx_far_factor_id ON factor_audit_reports(factor_id);
CREATE INDEX IF NOT EXISTS idx_far_passed ON factor_audit_reports(passed);
CREATE INDEX IF NOT EXISTS idx_far_audited_at ON factor_audit_reports(audited_at);
"""


# ─── factor_catalog 生命周期扩展字段（A.2，幂等，兼容旧库） ──

# 注: DuckDB 的 ALTER TABLE ADD COLUMN 不支持带 NOT NULL DEFAULT 约束的列，
#     新库字段已在 CREATE TABLE 中定义；此处仅对旧库做无约束补列。
_FACTOR_CATALOG_STATUS_EXTENSIONS = """
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS status_updated_at TIMESTAMP;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS consecutive_ic_negative_months INTEGER DEFAULT 0;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS consecutive_sharpe_drop_months INTEGER DEFAULT 0;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS last_incremental_eval_at TIMESTAMP;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS decay_rate_3m DOUBLE DEFAULT 0.0;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS decay_rate_6m DOUBLE DEFAULT 0.0;
ALTER TABLE factor_catalog ADD COLUMN IF NOT EXISTS style_tags JSON;
"""


# ─── C.3: 反馈闭环 4 张表 ──────────────────────────────

_CREATE_FEEDBACK_EVENTS = """
CREATE TABLE IF NOT EXISTS feedback_events (
    event_id        VARCHAR PRIMARY KEY,
    event_type      VARCHAR(50) NOT NULL,
    factor_id       VARCHAR(36),
    trigger_reason  VARCHAR(500) NOT NULL,
    severity        VARCHAR(20) NOT NULL,
    payload         JSON,
    timestamp       TIMESTAMP NOT NULL,
    handled         BOOLEAN DEFAULT FALSE,
    handled_at      TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fe_event_type ON feedback_events(event_type);
CREATE INDEX IF NOT EXISTS idx_fe_handled ON feedback_events(handled);
CREATE INDEX IF NOT EXISTS idx_fe_timestamp ON feedback_events(timestamp);
"""

_CREATE_ATTRIBUTION_REPORTS = """
CREATE TABLE IF NOT EXISTS attribution_reports (
    report_id       VARCHAR PRIMARY KEY,
    event_id        VARCHAR(36) NOT NULL,
    root_cause      VARCHAR(50) NOT NULL,
    confidence      DOUBLE NOT NULL,
    analyses_json   JSON NOT NULL,
    recommendation  JSON NOT NULL,
    created_at      TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ar_event_id ON attribution_reports(event_id);
CREATE INDEX IF NOT EXISTS idx_ar_root_cause ON attribution_reports(root_cause);
"""

_CREATE_FEEDBACK_PROCESSING_RESULTS = """
CREATE TABLE IF NOT EXISTS feedback_processing_results (
    result_id       VARCHAR PRIMARY KEY,
    event_id        VARCHAR(36) NOT NULL,
    report_id       VARCHAR(36),
    action_taken    VARCHAR(50) NOT NULL,
    success         BOOLEAN NOT NULL,
    execution_details JSON,
    processed_at    TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fpr_event_id ON feedback_processing_results(event_id);
CREATE INDEX IF NOT EXISTS idx_fpr_processed_at ON feedback_processing_results(processed_at);
"""

_CREATE_FEEDBACK_REPORTS = """
CREATE TABLE IF NOT EXISTS feedback_reports (
    report_id       VARCHAR PRIMARY KEY,
    period          VARCHAR(7) NOT NULL,
    new_factors     INT NOT NULL,
    effective_rate  DOUBLE NOT NULL,
    avg_sharpe_improvement DOUBLE,
    decay_rate_reduction DOUBLE,
    evolution_rounds INT NOT NULL,
    feedback_events_handled INT NOT NULL,
    attribution_accuracy DOUBLE,
    recommendations_accepted INT NOT NULL,
    recommendations_total INT NOT NULL,
    summary_text    TEXT,
    created_at      TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fr_period ON feedback_reports(period);
"""


# ─── D.1: 种子溯源链路（L0→L2） ─────────────────────────────

_CREATE_SEED_LINEAGE = """
CREATE TABLE IF NOT EXISTS seed_lineage (
    lineage_id          VARCHAR PRIMARY KEY,
    seed_name           VARCHAR NOT NULL,        -- L0 种子因子名称
    seed_family         VARCHAR NOT NULL,        -- L0 种子家族
    seed_market         VARCHAR NOT NULL,        -- 市场 (futures/stock)
    evolved_factor_id   VARCHAR NOT NULL,        -- L2 精英因子 ID
    evolved_factor_name VARCHAR NOT NULL,        -- L2 精英因子名称
    generation          INTEGER DEFAULT 0,       -- 从种子到精英的演化代数
    parent_id           VARCHAR,                 -- 直接父因子 ID
    trace_id            VARCHAR,                 -- 全链路追踪 ID
    promoted_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sl_seed_name ON seed_lineage(seed_name);
CREATE INDEX IF NOT EXISTS idx_sl_seed_family ON seed_lineage(seed_family);
CREATE INDEX IF NOT EXISTS idx_sl_evolved_factor_id ON seed_lineage(evolved_factor_id);
CREATE INDEX IF NOT EXISTS idx_sl_promoted_at ON seed_lineage(promoted_at DESC);
"""


# ─── E.1: Alpha 审查工作流（GAP-I102 首期） ─────────────────

_CREATE_FACTOR_REVIEWS = """
CREATE TABLE IF NOT EXISTS factor_reviews (
    factor_id   VARCHAR PRIMARY KEY,
    decision    VARCHAR NOT NULL,       -- approved / rejected
    comment     VARCHAR DEFAULT '',
    reviewer    VARCHAR DEFAULT 'cli',
    reviewed_at TIMESTAMP NOT NULL
);
-- 注意：不在 decision 列建索引——DuckDB 不允许 ON CONFLICT DO UPDATE 更新被索引
-- 引用的列（BinderException: Can not assign to column ... referenced by an INDEX），
-- 幂等 UPSERT（重复审查覆盖旧决定）是写路径核心，索引价值低（审查表小、低频）。
"""


# ─── 初始化函数 ──────────────────────────────────────────


def init_database(db_path: Optional[Path] = None, market: Optional[str] = None) -> Path:
    """初始化因子目录数据库，创建所有表和索引。

    Args:
        db_path: 数据库文件路径（优先级高于 market）
        market: "stock" 或 "futures"，用于确定默认路径（db_path 为 None 时生效）

    Returns:
        实际使用的数据库路径
    """
    import duckdb

    if db_path:
        path = Path(db_path)
    elif market:
        path = get_db_path(market)
    else:
        path = DATABASE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("[FactorDB] 初始化数据库: %s", path)
    conn = duckdb.connect(str(path))

    try:
        conn.execute(_CREATE_FACTOR_CATALOG)
        conn.execute(_CREATE_FACTOR_EVALUATIONS)
        conn.execute(_CREATE_FACTOR_VERSIONS)
        conn.execute(_CREATE_FACTOR_CORRELATIONS)
        conn.execute(_CREATE_FACTOR_QUALITY_SCORES)
        conn.execute(_CREATE_FACTOR_STATUS_HISTORY)
        conn.execute(_CREATE_FACTOR_AUDIT_REPORTS)
        # 幂等扩展 factor_catalog 生命周期字段（A.2）
        conn.execute(_FACTOR_CATALOG_STATUS_EXTENSIONS)
        # C.3 反馈闭环 4 张表
        conn.execute(_CREATE_FEEDBACK_EVENTS)
        conn.execute(_CREATE_ATTRIBUTION_REPORTS)
        conn.execute(_CREATE_FEEDBACK_PROCESSING_RESULTS)
        conn.execute(_CREATE_FEEDBACK_REPORTS)
        # D.1 种子溯源链路
        conn.execute(_CREATE_SEED_LINEAGE)
        # E.1 Alpha 审查工作流
        conn.execute(_CREATE_FACTOR_REVIEWS)
        # 迁移：清除旧版 decision 索引（阻塞 UPSERT，见 _CREATE_FACTOR_REVIEWS 注释）
        conn.execute("DROP INDEX IF EXISTS idx_frv_decision")

        conn.execute("CHECKPOINT")
        logger.info("[FactorDB] ✅ 数据库初始化完成")

        # 验证表结构
        tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
        logger.info("[FactorDB] 已创建表: %s", [t[0] for t in tables])

    finally:
        conn.close()

    return path


def get_connection(db_path: Optional[Path] = None, market: Optional[str] = None):
    """获取数据库连接。

    Args:
        db_path: 数据库文件路径（优先级高于 market）
        market: "stock" 或 "futures"，用于确定默认路径

    Returns:
        duckdb 连接对象（调用方负责关闭）
    """
    import duckdb

    if db_path:
        path = Path(db_path)
    elif market:
        path = get_db_path(market)
    else:
        path = DATABASE_PATH
    return duckdb.connect(str(path))


def verify_database(db_path: Optional[Path] = None, market: Optional[str] = None) -> dict:
    """验证数据库完整性，返回统计信息。

    Args:
        db_path: 数据库文件路径（优先级高于 market）
        market: "stock" 或 "futures"，用于确定默认路径

    Returns:
        统计信息字典
    """
    import duckdb

    if db_path:
        path = Path(db_path)
    elif market:
        path = get_db_path(market)
    else:
        path = DATABASE_PATH
    if not path.exists():
        return {"exists": False}

    conn = duckdb.connect(str(path), read_only=True)
    try:
        stats = {"exists": True, "path": str(path)}

        tables = conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='main'").fetchall()
        table_names = [t[0] for t in tables]
        stats["tables"] = table_names

        for table in table_names:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[f"{table}_count"] = count

        # 因子质量统计
        if "factor_catalog" in stats["tables"]:
            elite_count = conn.execute("SELECT COUNT(*) FROM factor_catalog WHERE is_elite = TRUE").fetchone()[0]
            active_count = conn.execute("SELECT COUNT(*) FROM factor_catalog WHERE status = 'active'").fetchone()[0]
            avg_sharpe = conn.execute("SELECT AVG(sharpe) FROM factor_catalog WHERE sharpe > 0").fetchone()[0]
            stats["elite_count"] = elite_count
            stats["active_count"] = active_count
            stats["avg_sharpe"] = round(avg_sharpe, 3) if avg_sharpe else 0.0

        # 种子溯源统计
        if "seed_lineage" in stats["tables"]:
            lineage_count = conn.execute("SELECT COUNT(*) FROM seed_lineage").fetchone()[0]
            family_dist = conn.execute("""
                SELECT seed_family, COUNT(*) as cnt
                FROM seed_lineage
                GROUP BY seed_family
                ORDER BY cnt DESC
            """).fetchall()
            stats["seed_lineage_count"] = int(lineage_count)
            stats["seed_lineage_families"] = {str(r[0]): int(r[1]) for r in family_dist}

        return stats

    finally:
        conn.close()
