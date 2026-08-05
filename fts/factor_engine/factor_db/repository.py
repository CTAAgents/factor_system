"""factor_db/repository.py — 因子目录 Repository 层

提供因子的 CRUD 操作、批量查询、排行榜、版本管理等功能。
作为 DuckDB 的统一访问层，上层业务通过此模块与数据库交互。

核心功能:
- 因子 CRUD: 创建、读取、更新、删除
- 批量操作: 批量插入、批量查询
- 排行榜: 按 Sharpe/IC 等指标排序
- 版本管理: 代码版本追踪、回滚
- 相关性管理: 因子间相关性存储与查询

版本: v1.0
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FactorRepository:
    """因子目录 Repository。

    提供对 factor_catalog 等表的统一访问接口。
    支持连接复用和事务管理。
    """

    def __init__(self, db_path: str | Path | None = None):
        from .schema import DATABASE_PATH, get_connection

        self._db_path = Path(db_path) if db_path else DATABASE_PATH
        self._conn = None
        self._last_columns: list[str] = []

    def _get_conn(self):
        """获取或创建数据库连接。"""
        if self._conn is None:
            import duckdb
            self._conn = duckdb.connect(str(self._db_path))
        return self._conn

    def _execute(self, sql: str, params: list | None = None):
        """执行 SQL 并保存列名信息。"""
        conn = self._get_conn()
        if params:
            result = conn.execute(sql, params)
        else:
            result = conn.execute(sql)
        if result.description:
            self._last_columns = [desc[0] for desc in result.description]
        return result

    def close(self):
        """关闭数据库连接。"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ─=== 因子 CRUD ──────────────────────────────────────

    def create_factor(self, factor: dict[str, Any]) -> str:
        """创建新因子。

        Args:
            factor: 因子数据字典

        Returns:
            factor_id
        """
        import hashlib

        conn = self._get_conn()
        factor_id = factor.get("factor_id", f"fct_{uuid.uuid4().hex[:8]}")
        code = factor.get("code", "")
        code_hash = hashlib.sha256(code.encode()).hexdigest() if code else ""

        conn.execute("""
            INSERT INTO factor_catalog (
                factor_id, name, code, code_hash, params, signature,
                economic_logic, source, parent_id, generation, trace_id,
                sharpe, ic, icir, max_drawdown, turnover_monthly,
                decay_6m, status, market, family, is_elite, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            factor_id,
            factor.get("name", factor_id),
            code,
            code_hash,
            json.dumps(factor.get("params", {})),
            json.dumps(factor.get("signature", {})),
            json.dumps(factor.get("economic_logic", {})),
            factor.get("source", "seed"),
            factor.get("parent_id"),
            factor.get("generation", 0),
            factor.get("trace_id", factor_id),
            factor.get("sharpe", 0.0),
            factor.get("ic", 0.0),
            factor.get("icir", 0.0),
            factor.get("max_drawdown", 0.0),
            factor.get("turnover_monthly", 0.0),
            factor.get("decay_6m", 0.05),
            factor.get("status", "active"),
            factor.get("market", "stock"),
            factor.get("family") or "other",
            factor.get("is_elite", False),
            json.dumps(factor.get("metadata", {})),
        ])

        # 记录版本
        version_id = f"ver_{uuid.uuid4().hex[:12]}"
        conn.execute("""
            INSERT INTO factor_versions (
                version_id, factor_id, code, code_hash,
                version_number, change_type, change_summary
            ) VALUES (?, ?, ?, ?, 1, 'create', ?)
        """, [version_id, factor_id, code, code_hash, factor.get("summary", "创建新因子")])

        conn.execute("CHECKPOINT")
        logger.info("[FactorRepo] 创建因子: %s (%s)", factor_id, factor.get("name"))
        return factor_id

    def get_factor(self, factor_id: str) -> dict[str, Any] | None:
        """根据 ID 获取因子。

        Args:
            factor_id: 因子 ID

        Returns:
            因子数据字典，不存在返回 None
        """
        result = self._execute("SELECT * FROM factor_catalog WHERE factor_id = ?", [factor_id])
        row = result.fetchone()
        if not row:
            return None

        return self._row_to_dict(row)

    def get_factor_by_name(self, name: str, market: str | None = None) -> dict[str, Any] | None:
        """根据名称获取因子。

        Args:
            name: 因子名称
            market: 可选的市场过滤

        Returns:
            因子记录或 None
        """
        if market:
            result = self._execute(
                "SELECT * FROM factor_catalog WHERE name = ? AND market = ? LIMIT 1",
                [name, market],
            )
        else:
            result = self._execute("SELECT * FROM factor_catalog WHERE name = ? LIMIT 1", [name])
        row = result.fetchone()
        if not row:
            return None
        return self._row_to_dict(row)

    def update_factor(self, factor_id: str, updates: dict[str, Any]) -> bool:
        """更新因子。

        Args:
            factor_id: 因子 ID
            updates: 要更新的字段

        Returns:
            是否成功
        """
        conn = self._get_conn()
        set_clauses = []
        params = []

        for key, value in updates.items():
            if key in ("factor_id", "created_at"):
                continue
            set_clauses.append(f"{key} = ?")
            if isinstance(value, (dict, list)):
                params.append(json.dumps(value))
            else:
                params.append(value)

        if not set_clauses:
            return False

        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        params.append(factor_id)

        sql = f"UPDATE factor_catalog SET {', '.join(set_clauses)} WHERE factor_id = ?"
        conn.execute(sql, params)
        conn.execute("CHECKPOINT")

        logger.info("[FactorRepo] 更新因子: %s", factor_id)
        return True

    def delete_factor(self, factor_id: str) -> bool:
        """删除因子（软删除，将状态设为 'deleted'）。"""
        return self.update_factor(factor_id, {"status": "deleted"})

    # ─=== 批量查询 ──────────────────────────────────────

    def list_factors(
        self,
        market: str | None = None,
        status: str | None = None,
        is_elite: bool | None = None,
        min_sharpe: float | None = None,
        min_ic: float | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "sharpe",
        sort_order: str = "desc",
    ) -> list[dict[str, Any]]:
        """查询因子列表。

        Args:
            market: 市场过滤 ('stock'/'futures'/None)
            status: 状态过滤 ('active'/'failed'/'deleted'/None)
            is_elite: 是否精英因子
            min_sharpe: 最小 Sharpe
            min_ic: 最小 IC
            limit: 返回数量
            offset: 偏移量
            sort_by: 排序字段
            sort_order: 排序方向 ('asc'/'desc')

        Returns:
            因子列表
        """
        conn = self._get_conn()
        conditions = []
        params = []

        if market:
            conditions.append("market = ?")
            params.append(market)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if is_elite is not None:
            conditions.append("is_elite = ?")
            params.append(is_elite)
        if min_sharpe is not None:
            conditions.append("sharpe >= ?")
            params.append(min_sharpe)
        if min_ic is not None:
            conditions.append("ic >= ?")
            params.append(min_ic)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        # 安全检查排序字段
        allowed_sorts = {"sharpe", "ic", "icir", "max_drawdown", "turnover_monthly", "created_at", "updated_at"}
        if sort_by not in allowed_sorts:
            sort_by = "sharpe"
        sort_dir = "DESC" if sort_order == "desc" else "ASC"

        sql = f"""
            SELECT * FROM factor_catalog
            {where_clause}
            ORDER BY {sort_by} {sort_dir}
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        result = self._execute(sql, params)
        rows = result.fetchall()
        return self._rows_to_dicts(rows)

    def count_factors(
        self,
        market: str | None = None,
        status: str | None = None,
        is_elite: bool | None = None,
    ) -> int:
        """统计因子数量。"""
        conn = self._get_conn()
        conditions = []
        params = []

        if market:
            conditions.append("market = ?")
            params.append(market)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if is_elite is not None:
            conditions.append("is_elite = ?")
            params.append(is_elite)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT COUNT(*) FROM factor_catalog {where_clause}"

        return conn.execute(sql, params).fetchone()[0]

    def get_top_factors(
        self,
        n: int = 10,
        by: str = "sharpe",
        market: str | None = None,
        min_ic: float = 0.02,
    ) -> list[dict[str, Any]]:
        """获取 Top N 因子。

        Args:
            n: 返回数量
            by: 排序指标
            market: 市场过滤
            min_ic: 最小 IC 阈值

        Returns:
            Top N 因子列表
        """
        return self.list_factors(
            market=market,
            status="active",
            is_elite=True,
            min_ic=min_ic,
            limit=n,
            sort_by=by,
            sort_order="desc",
        )

    def search_factors(
        self,
        keyword: str,
        market: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按名称或描述搜索因子。

        Args:
            keyword: 搜索关键词
            market: 市场过滤
            limit: 返回数量

        Returns:
            匹配的因子列表
        """
        conn = self._get_conn()
        conditions = ["(name ILIKE ? OR factor_id ILIKE ?)"]
        search_pattern = f"%{keyword}%"
        params = [search_pattern, search_pattern]

        if market:
            conditions.append("market = ?")
            params.append(market)

        sql = f"""
            SELECT * FROM factor_catalog
            WHERE {' AND '.join(conditions)}
            LIMIT ?
        """
        params.append(limit)

        result = self._execute(sql, params)
        rows = result.fetchall()
        return self._rows_to_dicts(rows)

    # ─=== 评估管理 ──────────────────────────────────────

    def add_evaluation(
        self,
        factor_id: str,
        evaluation: dict[str, Any],
    ) -> str:
        """添加因子评估记录。

        Args:
            factor_id: 因子 ID
            evaluation: 评估数据

        Returns:
            eval_id
        """
        conn = self._get_conn()
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
            evaluation.get("trace_id"),
            evaluation.get("ic", 0.0),
            evaluation.get("icir", 0.0),
            evaluation.get("sharpe", 0.0),
            evaluation.get("max_drawdown", 0.0),
            evaluation.get("turnover", 0.0),
            evaluation.get("t_stat", 0.0),
            evaluation.get("monotonicity", False),
            evaluation.get("oos_ratio", 0.0),
            evaluation.get("theory_score", 0),
            evaluation.get("behavioral_score", 0),
            evaluation.get("microstructure_score", 0),
            evaluation.get("institutional_score", 0),
            evaluation.get("dims_passed", 0),
            evaluation.get("bonferroni_p", 1.0),
            evaluation.get("fdr_q", 0.05),
            evaluation.get("effective_n", 1),
            evaluation.get("adjusted_t", 0.0),
            evaluation.get("l3_passed", False),
            evaluation.get("overall_passed", False),
            json.dumps(evaluation.get("failure_reasons", [])),
            evaluation.get("evaluated_at", datetime.now().isoformat()),
        ])

        # 更新主表最新评估
        self.update_factor(factor_id, {
            "sharpe": evaluation.get("sharpe", 0.0),
            "ic": evaluation.get("ic", 0.0),
            "icir": evaluation.get("icir", 0.0),
            "max_drawdown": evaluation.get("max_drawdown", 0.0),
            "turnover_monthly": evaluation.get("turnover", 0.0),
            "status": "active" if evaluation.get("overall_passed") else "failed",
        })

        conn.execute("CHECKPOINT")
        logger.info("[FactorRepo] 添加评估: %s -> %s", factor_id, eval_id)
        return eval_id

    def get_evaluations(
        self,
        factor_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """获取因子的评估历史。"""
        result = self._execute("""
            SELECT * FROM factor_evaluations
            WHERE factor_id = ?
            ORDER BY evaluated_at DESC
            LIMIT ?
        """, [factor_id, limit])
        rows = result.fetchall()
        return self._rows_to_dicts(rows)

    # ─=== 版本管理 ──────────────────────────────────────

    def get_versions(self, factor_id: str) -> list[dict[str, Any]]:
        """获取因子的版本历史。"""
        result = self._execute("""
            SELECT * FROM factor_versions
            WHERE factor_id = ?
            ORDER BY created_at DESC
        """, [factor_id])
        rows = result.fetchall()
        return self._rows_to_dicts(rows)

    def rollback_to_version(self, factor_id: str, version_id: str) -> bool:
        """回滚到指定版本。"""
        result = self._execute(
            "SELECT * FROM factor_versions WHERE version_id = ?",
            [version_id]
        )
        version_row = result.fetchone()
        if not version_row:
            return False

        version = self._row_to_dict(version_row)

        self.update_factor(factor_id, {
            "code": version["code"],
            "code_hash": version["code_hash"],
        })

        # 记录回滚版本
        new_version_id = f"ver_{uuid.uuid4().hex[:12]}"
        conn = self._get_conn()
        conn.execute("""
            INSERT INTO factor_versions (
                version_id, factor_id, code, code_hash,
                version_number, change_type, change_summary
            ) VALUES (?, ?, ?, ?, ?, 'rollback', ?)
        """, [
            new_version_id,
            factor_id,
            version["code"],
            version["code_hash"],
            version["version_number"] + 1,
            f"回滚到版本 {version['version_number']}",
        ])

        conn.execute("CHECKPOINT")
        logger.info("[FactorRepo] 回滚: %s -> version %s", factor_id, version_id)
        return True

    # ─=== 统计信息 ──────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """获取因子库统计信息。"""
        conn = self._get_conn()

        total = conn.execute("SELECT COUNT(*) FROM factor_catalog").fetchone()[0]
        active = conn.execute(
            "SELECT COUNT(*) FROM factor_catalog WHERE status = 'active'"
        ).fetchone()[0]
        elite = conn.execute(
            "SELECT COUNT(*) FROM factor_catalog WHERE is_elite = TRUE"
        ).fetchone()[0]
        avg_sharpe = conn.execute(
            "SELECT AVG(sharpe) FROM factor_catalog WHERE sharpe > 0"
        ).fetchone()[0]
        avg_ic = conn.execute(
            "SELECT AVG(ic) FROM factor_catalog WHERE ic > 0"
        ).fetchone()[0]

        # 按市场分组
        market_stats = conn.execute("""
            SELECT market, COUNT(*) as cnt
            FROM factor_catalog
            WHERE status != 'deleted'
            GROUP BY market
        """).fetchall()

        return {
            "total_factors": total,
            "active_factors": active,
            "elite_factors": elite,
            "avg_sharpe": round(avg_sharpe, 3) if avg_sharpe else 0.0,
            "avg_ic": round(avg_ic, 4) if avg_ic else 0.0,
            "by_market": {row[0]: row[1] for row in market_stats},
        }

    # ─=== 高级查询 API (因子挖掘) ──────────────────────────

    def get_by_family(
        self,
        family: str,
        market: str | None = None,
        min_sharpe: float | None = None,
        min_ic: float | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """按因子家族查询。

        Args:
            family: 因子家族名称 (momentum/mean_reversion/liquidity 等)
            market: 市场过滤
            min_sharpe: 最小 Sharpe 阈值
            min_ic: 最小 IC 阈值
            limit: 返回数量

        Returns:
            该家族的因子列表
        """
        conditions = ["family = ?"]
        params: list[Any] = [family]

        if market:
            conditions.append("market = ?")
            params.append(market)
        if min_sharpe is not None:
            conditions.append("sharpe >= ?")
            params.append(min_sharpe)
        if min_ic is not None:
            conditions.append("ic >= ?")
            params.append(min_ic)

        sql = f"""
            SELECT * FROM factor_catalog
            WHERE {' AND '.join(conditions)}
            ORDER BY sharpe DESC
            LIMIT ?
        """
        params.append(limit)

        result = self._execute(sql, params)
        return self._rows_to_dicts(result.fetchall())

    def get_eligible(
        self,
        market: str = "stock",
        min_ic: float = 0.02,
        min_sharpe: float = 0.5,
        require_elite: bool = True,
    ) -> list[dict[str, Any]]:
        """筛选合格因子 (用于组合构建)。

        Args:
            market: 目标市场
            min_ic: 最小 IC 阈值
            min_sharpe: 最小 Sharpe 阈值
            require_elite: 是否只返回精英因子

        Returns:
            符合条件的因子列表
        """
        conditions = [
            "market = ?",
            "status = 'active'",
            "ic >= ?",
            "sharpe >= ?",
        ]
        params: list[Any] = [market, min_ic, min_sharpe]

        if require_elite:
            conditions.append("is_elite = TRUE")

        sql = f"""
            SELECT * FROM factor_catalog
            WHERE {' AND '.join(conditions)}
            ORDER BY (ic + sharpe) DESC
        """

        result = self._execute(sql, params)
        return self._rows_to_dicts(result.fetchall())

    def get_diverse_factors(
        self,
        market: str = "stock",
        total_count: int = 10,
        max_per_family: int = 3,
        min_ic: float = 0.02,
        min_sharpe: float = 0.5,
    ) -> list[dict[str, Any]]:
        """因子多样性选择 (避免因子集中在单一家族)。

        策略: 先按家族分组，在每个家族内按 Sharpe 排序，
        然后轮流从每个家族选取最优因子，确保家族多样性。

        Args:
            market: 目标市场
            total_count: 需要的因子总数
            max_per_family: 单一家族最大因子数
            min_ic: 最小 IC 阈值
            min_sharpe: 最小 Sharpe 阈值

        Returns:
            多样化因子列表
        """
        # 先获取所有合格因子
        eligible = self.get_eligible(
            market=market,
            min_ic=min_ic,
            min_sharpe=min_sharpe,
        )

        if len(eligible) <= total_count:
            return eligible

        # 按家族分组
        family_groups: dict[str, list[dict[str, Any]]] = {}
        for factor in eligible:
            fam = factor.get("family", "unknown")
            if fam not in family_groups:
                family_groups[fam] = []
            family_groups[fam].append(factor)

        # 每个家族内按 Sharpe 排序
        for fam in family_groups:
            family_groups[fam].sort(
                key=lambda x: x.get("sharpe", 0),
                reverse=True
            )

        # 多样性选择: 轮流从每个家族取一个
        selected: list[dict[str, Any]] = []
        family_counters: dict[str, int] = {fam: 0 for fam in family_groups}

        while len(selected) < total_count:
            added = False
            for fam, factors in family_groups.items():
                if len(selected) >= total_count:
                    break
                idx = family_counters[fam]
                if idx < min(len(factors), max_per_family):
                    selected.append(factors[idx])
                    family_counters[fam] = idx + 1
                    added = True
            if not added:
                break

        return selected

    def get_factor_lineage(self, factor_id: str) -> dict[str, Any] | None:
        """获取因子演化谱系 (追溯父因子链)。

        Args:
            factor_id: 因子 ID

        Returns:
            谱系信息字典，包含 factor_id, parent_id, generations,
            versions 等完整演化信息。
        """
        factor = self.get_factor(factor_id)
        if not factor:
            return None

        lineage = {
            "factor_id": factor_id,
            "name": factor.get("name"),
            "generation": factor.get("generation", 0),
            "parent_id": factor.get("parent_id"),
            "source": factor.get("source"),
            "created_at": factor.get("created_at"),
            "metrics": {
                "sharpe": factor.get("sharpe"),
                "ic": factor.get("ic"),
            },
        }

        # 获取版本历史
        versions = self.get_versions(factor_id)
        lineage["version_count"] = len(versions)
        lineage["versions"] = [
            {
                "version_id": v.get("version_id"),
                "version_number": v.get("version_number"),
                "change_type": v.get("change_type"),
                "change_summary": v.get("change_summary"),
                "created_at": v.get("created_at"),
            }
            for v in versions[:10]  # 最近 10 个版本
        ]

        # 递归获取父因子谱系
        if factor.get("parent_id"):
            parent_lineage = self.get_factor_lineage(factor["parent_id"])
            if parent_lineage:
                lineage["parent"] = parent_lineage

        return lineage

    def get_family_distribution(
        self,
        market: str | None = None,
        min_sharpe: float = 0.0,
    ) -> list[dict[str, Any]]:
        """获取因子家族分布统计。

        Args:
            market: 市场过滤
            min_sharpe: 最小 Sharpe 阈值

        Returns:
            家族分布列表，包含 family, count, avg_sharpe, max_sharpe
        """
        conditions = ["status != 'deleted'", "sharpe >= ?"]
        params: list[Any] = [min_sharpe]

        if market:
            conditions.append("market = ?")
            params.append(market)

        sql = f"""
            SELECT
                family,
                COUNT(*) as factor_count,
                AVG(sharpe) as avg_sharpe,
                MAX(sharpe) as max_sharpe,
                AVG(ic) as avg_ic
            FROM factor_catalog
            WHERE {' AND '.join(conditions)}
            GROUP BY family
            ORDER BY factor_count DESC
        """

        result = self._execute(sql, params)
        rows = result.fetchall()

        return [
            {
                "family": row[0] or "unknown",
                "count": row[1],
                "avg_sharpe": round(row[2], 3) if row[2] else 0.0,
                "max_sharpe": round(row[3], 3) if row[3] else 0.0,
                "avg_ic": round(row[4], 4) if row[4] else 0.0,
            }
            for row in rows
        ]

    # ─=== 内部工具 ──────────────────────────────────────

    def _row_to_dict(self, row) -> dict[str, Any]:
        """将数据库行转为字典。使用 _last_columns 获取列名。"""
        cols = self._last_columns
        result = {}
        for i, val in enumerate(row):
            if i >= len(cols):
                break
            col = cols[i]
            if col in ("params", "signature", "economic_logic", "metadata"):
                if val:
                    try:
                        result[col] = json.loads(val)
                    except (json.JSONDecodeError, TypeError):
                        result[col] = val
                else:
                    result[col] = {}
            elif col.endswith("_at") or col in ("created_at", "updated_at", "evaluated_at"):
                result[col] = str(val) if val else None
            elif col == "failure_reasons" and val:
                try:
                    result[col] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    result[col] = val
            else:
                result[col] = val
        return result

    def _rows_to_dicts(self, rows) -> list[dict[str, Any]]:
        """将多行数据转为字典列表。"""
        return [self._row_to_dict(row) for row in rows]
