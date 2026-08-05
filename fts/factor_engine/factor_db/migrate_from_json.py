"""factor_db/migrate_from_json.py — 从 JSON 迁移精英因子到 DuckDB

迁移逻辑:
1. 扫描 elite_dir 下所有 JSON 文件（排除 _ 开头的元数据文件）
2. 解析每个因子的完整数据结构
3. 写入 factor_catalog 主表
4. 写入 factor_evaluations 评估表
5. 写入 factor_versions 版本表
6. 记录迁移日志

用法:
    python -m fts.factor_engine.factor_db.migrate_from_json \
        --elite-dir memory/knowledge/factors/elite \
        --db-path data/factor_catalog.duckdb \
        [--dry-run] \
        [--force]

版本: v1.0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

DEFAULT_ELITE_DIR = "memory/knowledge/factors/elite"


def compute_code_hash(code: str) -> str:
    """计算代码 SHA256 哈希。"""
    return hashlib.sha256(code.encode()).hexdigest()


def parse_factor_json(json_path: Path) -> dict[str, Any] | None:
    """解析单个因子 JSON 文件。

    Returns:
        因子数据字典，解析失败返回 None
    """
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return data
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("解析失败: %s — %s", json_path.name, e)
        return None


def extract_evaluation_metrics(data: dict[str, Any]) -> dict[str, Any]:
    """从因子数据提取评估指标。"""
    eval_data = data.get("evaluation", {})
    l1 = eval_data.get("level_1_backtest", {})
    l2 = eval_data.get("level_2_economic", {})
    l3 = eval_data.get("level_3_multiple", {})

    return {
        "ic": l1.get("ic", 0.0),
        "icir": l1.get("icir", 0.0),
        "sharpe": l1.get("sharpe", 0.0),
        "max_drawdown": l1.get("max_drawdown", 0.0),
        "turnover_monthly": l1.get("turnover_monthly", 0.0),
        "t_stat": l1.get("t_stat", 0.0),
        "monotonicity": l1.get("monotonicity", False),
        "oos_ratio": l1.get("oos_ratio", 0.0),
        "l2_theory": l2.get("theory", 0),
        "l2_behavioral": l2.get("behavioral", 0),
        "l2_microstructure": l2.get("microstructure", 0),
        "l2_institutional": l2.get("institutional", 0),
        "l2_dims_passed": l2.get("dimensions_passed", 0),
        "l3_bonferroni_p": l3.get("bonferroni_p", 1.0),
        "l3_fdr_q": l3.get("fdr_q", 0.05),
        "l3_effective_n": l3.get("effective_n_factors", 1),
        "l3_adjusted_t": l3.get("adjusted_t", 0.0),
        "l3_passed": l3.get("passed", False),
        "overall_passed": eval_data.get("passed", False),
        "failure_reasons": eval_data.get("failure_reasons", []),
    }


def migrate_factors(
    elite_dir: str | Path,
    db_path: str | Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """执行迁移。

    Args:
        elite_dir: 精英因子 JSON 目录
        db_path: DuckDB 数据库路径
        dry_run: 仅扫描不写入
        force: 强制覆盖已有数据

    Returns:
        迁移统计信息
    """
    import duckdb
    from .schema import init_database

    elite_path = Path(elite_dir)
    if not elite_path.exists():
        raise FileNotFoundError(f"精英因子目录不存在: {elite_path}")

    # 初始化数据库
    if not dry_run:
        init_database(Path(db_path))

    # 扫描 JSON 文件
    json_files = sorted(elite_path.glob("*.json"))
    factor_files = [f for f in json_files if not f.name.startswith("_")]

    logger.info("[Migrate] 发现 %d 个因子 JSON 文件", len(factor_files))

    stats = {
        "total_files": len(factor_files),
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "factors": [],
        "errors": [],
    }

    if dry_run:
        logger.info("[Migrate] 🧪  Dry-run 模式，仅扫描不写入")

    # 连接数据库
    conn = None
    if not dry_run:
        conn = duckdb.connect(str(db_path))

    try:
        for json_file in factor_files:
            data = parse_factor_json(json_file)
            if data is None:
                stats["failed"] += 1
                continue

            factor_id = data.get("factor_id", json_file.stem)
            name = data.get("name", json_file.stem)
            code = data.get("code", "")
            code_hash = compute_code_hash(code) if code else ""

            # 检查是否已存在
            if conn and not force:
                existing = conn.execute(
                    "SELECT factor_id FROM factor_catalog WHERE factor_id = ?",
                    [factor_id]
                ).fetchone()
                if existing:
                    stats["skipped"] += 1
                    logger.debug("跳过已存在: %s", factor_id)
                    continue

            # 提取评估数据
            metrics = extract_evaluation_metrics(data)
            signature = data.get("signature", {})
            economic_logic = data.get("economic_logic", {})

            factor_info = {
                "factor_id": factor_id,
                "name": name,
                "code_hash": code_hash,
                "sharpe": metrics["sharpe"],
                "ic": metrics["ic"],
                "status": "active" if metrics["overall_passed"] else "failed",
                "source_file": json_file.name,
            }

            if conn and not dry_run:
                # 写入 factor_catalog
                conn.execute("""
                    INSERT OR REPLACE INTO factor_catalog (
                        factor_id, name, code, code_hash, params, signature,
                        economic_logic, source, parent_id, generation, trace_id,
                        sharpe, ic, icir, max_drawdown, turnover_monthly,
                        decay_6m, status, market, created_at, is_elite, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    factor_id,
                    name,
                    code,
                    code_hash,
                    json.dumps(data.get("params", {})),
                    json.dumps(signature),
                    json.dumps(economic_logic),
                    data.get("source", "seed"),
                    data.get("parent_id"),
                    data.get("generation", 0),
                    data.get("trace_id", factor_id),
                    metrics["sharpe"],
                    metrics["ic"],
                    metrics["icir"],
                    metrics["max_drawdown"],
                    metrics["turnover_monthly"],
                    data.get("decay_6m", 0.05),
                    factor_info["status"],
                    data.get("market", "stock"),
                    data.get("created_at", datetime.now().isoformat()),
                    True,
                    json.dumps({
                        "evaluation": data.get("evaluation"),
                        "correlation_metadata": data.get("correlation_metadata", {}),
                    }),
                ])

                # 写入 factor_evaluations
                eval_id = f"eval_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                    INSERT INTO factor_evaluations (
                        eval_id, factor_id, trace_id,
                        level_1_ic, level_1_icir, level_1_sharpe, level_1_max_dd,
                        level_1_turnover, level_1_t_stat, level_1_monotonicity,
                        level_1_oos_ratio,
                        level_2_theory_score, level_2_behavioral_score,
                        level_2_microstructure_score, level_2_institutional_score,
                        level_2_dims_passed,
                        level_3_bonferroni_p, level_3_fdr_q,
                        level_3_effective_n, level_3_adjusted_t, level_3_passed,
                        overall_passed, failure_reasons, evaluated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [
                    eval_id,
                    factor_id,
                    data.get("evaluation", {}).get("trace_id"),
                    metrics["ic"],
                    metrics["icir"],
                    metrics["sharpe"],
                    metrics["max_drawdown"],
                    metrics["turnover_monthly"],
                    metrics["t_stat"],
                    metrics["monotonicity"],
                    metrics["oos_ratio"],
                    metrics["l2_theory"],
                    metrics["l2_behavioral"],
                    metrics["l2_microstructure"],
                    metrics["l2_institutional"],
                    metrics["l2_dims_passed"],
                    metrics["l3_bonferroni_p"],
                    metrics["l3_fdr_q"],
                    metrics["l3_effective_n"],
                    metrics["l3_adjusted_t"],
                    metrics["l3_passed"],
                    metrics["overall_passed"],
                    json.dumps(metrics["failure_reasons"]),
                    data.get("evaluation", {}).get("evaluated_at", datetime.now().isoformat()),
                ])

                # 写入 factor_versions
                version_id = f"ver_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                    INSERT INTO factor_versions (
                        version_id, factor_id, code, code_hash,
                        version_number, change_type, change_summary
                    ) VALUES (?, ?, ?, ?, 1, 'migrate', 'JSON 迁移初始化')
                """, [
                    version_id,
                    factor_id,
                    code,
                    code_hash,
                ])

            stats["success"] += 1
            stats["factors"].append(factor_info)

            if stats["success"] % 100 == 0:
                logger.info("[Migrate] 进度: %d/%d", stats["success"], len(factor_files))

        # 提交事务
        if conn and not dry_run:
            conn.execute("CHECKPOINT")
            logger.info("[Migrate] ✅ 迁移完成，数据已持久化")

    except Exception as e:
        logger.error("[Migrate] ❌ 迁移失败: %s", e)
        stats["errors"].append(str(e))
        raise

    finally:
        if conn:
            conn.close()

    # 输出统计
    logger.info("[Migrate] 迁移统计:")
    logger.info("  总计: %d", stats["total_files"])
    logger.info("  成功: %d", stats["success"])
    logger.info("  跳过: %d", stats["skipped"])
    logger.info("  失败: %d", stats["failed"])

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="从 JSON 迁移精英因子到 DuckDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # Dry-run 模式，仅扫描不写入
  python -m fts.factor_engine.factor_db.migrate_from_json --dry-run

  # 正式迁移
  python -m fts.factor_engine.factor_db.migrate_from_json

  # 强制重新迁移
  python -m fts.factor_engine.factor_db.migrate_from_json --force
        """,
    )

    parser.add_argument(
        "--elite-dir",
        type=str,
        default=DEFAULT_ELITE_DIR,
        help=f"精英因子 JSON 目录 (默认: {DEFAULT_ELITE_DIR})",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/factor_catalog.duckdb",
        help="DuckDB 数据库路径 (默认: data/factor_catalog.duckdb)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅扫描不写入，验证数据完整性",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制覆盖已有数据",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="详细日志输出",
    )

    args = parser.parse_args()

    # 设置日志
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 检查目录
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    elite_dir = project_root / args.elite_dir
    db_path = project_root / args.db_path

    if not elite_dir.exists():
        logger.error("精英因子目录不存在: %s", elite_dir)
        sys.exit(1)

    logger.info("精英目录: %s", elite_dir)
    logger.info("数据库路径: %s", db_path)

    try:
        stats = migrate_factors(
            elite_dir=elite_dir,
            db_path=db_path,
            dry_run=args.dry_run,
            force=args.force,
        )

        if args.dry_run:
            logger.info("\n🧪 Dry-run 完成，可以安全执行正式迁移")
        else:
            logger.info("\n✅ 迁移完成")

        return 0 if stats["failed"] == 0 else 1

    except Exception as e:
        logger.error("迁移失败: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
