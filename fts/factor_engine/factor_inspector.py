"""
fts.factor_engine.factor_inspector — 因子定时巡检与自动降级 (Phase 3)。

定义 ``FactorInspector`` 类，定期扫描精英因子库，
调用 ``FactorLineage.batch_audit`` 检测退化因子，
并自动执行降级操作（is_elite=False, status='degraded'）。

用法:
    inspector = FactorInspector()
    result = inspector.inspect_and_downgrade(
        threshold=-0.2,  # Sharpe 下降 20% 触发降级
        commit=True,     # 实际执行降级
    )
    print(result["downgraded"])

版本: v1.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from .factor_db import FactorLineage, FactorRepository

logger = logging.getLogger(__name__)


def _load_review_experience_enabled() -> bool:
    """读取人审意见写经验链开关（FTS_REVIEW_EXPERIENCE_CHAIN，默认开启）。"""
    import os

    return os.getenv("FTS_REVIEW_EXPERIENCE_CHAIN", "1") == "1"


@dataclass
class DowngradeRecord:
    """单条降级记录。"""

    factor_id: str
    factor_name: str
    reason: str
    degradation_score: float
    previous_status: str
    new_status: str
    action: str  # "downgraded" / "skipped" / "error"
    details: dict[str, Any] = field(default_factory=dict)


class FactorInspector:
    """因子巡检与自动降级执行器。

    定时巡检精英因子，基于血缘审计结果识别退化因子，
    并自动执行降级操作。

    Args:
        repo: 因子仓库（可选，默认新建）
        lineage: 血缘审计器（可选，默认新建）
    """

    def __init__(
        self,
        repo: Optional[FactorRepository] = None,
        lineage: Optional[FactorLineage] = None,
    ) -> None:
        self._repo = repo or FactorRepository()
        self._lineage = lineage or FactorLineage(self._repo)

    def inspect_and_downgrade(
        self,
        threshold: float = -0.2,
        market: Optional[str] = None,
        commit: bool = True,
    ) -> dict[str, Any]:
        """执行巡检并自动降级退化因子。

        Args:
            threshold: 退化阈值（Sharpe 相对变化，负数为退化）
            market: 限定市场（None 为全部）
            commit: 是否实际执行降级（False 为 dry-run）

        Returns:
            巡检结果字典
        """
        started_at = datetime.now()

        # 1. 批量血缘审计 (筛选活跃精英因子)
        audit_results = self._lineage.batch_audit(
            market=market,
            min_evals=1,
        )

        records: list[DowngradeRecord] = []
        downgraded_count = 0
        skipped_count = 0
        error_count = 0
        audited_count = audit_results.get("total_audited", 0)
        degraded_factors = []

        # 2. 遍历审计结果，按阈值筛选退化因子
        for info in audit_results.get("results", []):
            factor_id = info.get("factor_id", "")
            factor_name = info.get("name", "unknown")

            # 使用 detect_degradation 按指定阈值检查
            degradation = self._lineage.detect_degradation(
                factor_id=factor_id,
                threshold=threshold,
            )

            if degradation.get("is_degraded"):
                degraded_factors.append(
                    {
                        "factor_id": factor_id,
                        "factor_name": factor_name,
                        "degradation_score": degradation.get("degradation_score", 0.0),
                        "recommendation": degradation.get("recommendation", "Sharpe持续退化"),
                        "sharpe_trend": degradation.get("sharpe_trend", {}),
                        "ic_trend": degradation.get("ic_trend", {}),
                    }
                )

        # 3. 遍历退化因子，执行降级
        for info in degraded_factors:
            factor_id = info.get("factor_id", "")
            factor_name = info.get("factor_name", "unknown")
            degradation_score = info.get("degradation_score", 0.0)

            # 获取当前因子状态
            factor = self._repo.get_factor(factor_id)
            if not factor:
                records.append(
                    DowngradeRecord(
                        factor_id=factor_id,
                        factor_name=factor_name,
                        reason=info.get("recommendation", "未知"),
                        degradation_score=degradation_score,
                        previous_status="unknown",
                        new_status="unknown",
                        action="error",
                        details={"error": "因子不存在"},
                    )
                )
                error_count += 1
                continue

            previous_status = factor.get("status", "active")
            is_elite = factor.get("is_elite", False)

            # 已经降级的跳过
            if previous_status == "degraded" or not is_elite:
                records.append(
                    DowngradeRecord(
                        factor_id=factor_id,
                        factor_name=factor_name,
                        reason=info.get("recommendation", "已降级"),
                        degradation_score=degradation_score,
                        previous_status=previous_status,
                        new_status=previous_status,
                        action="skipped",
                        details={"reason": "已处于降级状态或非精英"},
                    )
                )
                skipped_count += 1
                continue

            # 4. 执行降级
            if commit:
                success = self._repo.update_factor(
                    factor_id,
                    {
                        "is_elite": False,
                        "status": "degraded",
                    },
                )
                if success:
                    action = "downgraded"
                    new_status = "degraded"
                    downgraded_count += 1
                    logger.info(
                        "[FactorInspector] 降级因子: %s (%s), Sharpe变化=%.2f",
                        factor_id,
                        factor_name,
                        degradation_score,
                    )
                else:
                    action = "error"
                    new_status = previous_status
                    error_count += 1
                    logger.warning("[FactorInspector] 降级失败: %s", factor_id)
            else:
                action = "downgraded"
                new_status = "degraded"
                downgraded_count += 1
                logger.info(
                    "[FactorInspector] [DRY-RUN] 拟降级因子: %s (%s)",
                    factor_id,
                    factor_name,
                )

            records.append(
                DowngradeRecord(
                    factor_id=factor_id,
                    factor_name=factor_name,
                    reason=info.get("recommendation", "Sharpe持续退化"),
                    degradation_score=degradation_score,
                    previous_status=previous_status,
                    new_status=new_status,
                    action=action,
                    details={
                        "sharpe_trend": info.get("sharpe_trend", {}),
                        "ic_trend": info.get("ic_trend", {}),
                    },
                )
            )

        completed_at = datetime.now()

        return {
            "inspection_id": f"insp_{started_at.strftime('%Y%m%d_%H%M%S')}",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": (completed_at - started_at).total_seconds(),
            "threshold": threshold,
            "market": market,
            "commit": commit,
            "summary": {
                "total_audited": audited_count,
                "degraded_detected": len(degraded_factors),
                "downgraded": downgraded_count,
                "skipped": skipped_count,
                "errors": error_count,
            },
            "records": [self._record_to_dict(r) for r in records],
        }

    def get_degraded_factors(
        self,
        market: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """获取当前所有已降级的因子。

        Args:
            market: 限定市场

        Returns:
            降级因子列表
        """
        conn = self._repo._get_conn()
        query = "SELECT * FROM factor_catalog WHERE status = 'degraded'"
        params: list[Any] = []
        if market:
            query += " AND market = ?"
            params.append(market)
        result = conn.execute(query, params)
        rows = result.fetchall()
        return [self._repo._row_to_dict(row) for row in rows]

    def reactivate_factor(
        self,
        factor_id: str,
        promote_to_elite: bool = False,
    ) -> bool:
        """重新激活因子（从降级状态恢复）。

        Args:
            factor_id: 因子 ID
            promote_to_elite: 是否同时恢复为精英因子

        Returns:
            是否成功
        """
        # 先检查因子是否存在
        factor = self._repo.get_factor(factor_id)
        if not factor:
            logger.warning("[FactorInspector] 重新激活失败，因子不存在: %s", factor_id)
            return False

        updates: dict[str, Any] = {"status": "active"}
        if promote_to_elite:
            updates["is_elite"] = True
        success = self._repo.update_factor(factor_id, updates)
        if success:
            logger.info(
                "[FactorInspector] 重新激活因子: %s (promote=%s)",
                factor_id,
                promote_to_elite,
            )
        return success

    @staticmethod
    def _record_to_dict(record: DowngradeRecord) -> dict[str, Any]:
        return {
            "factor_id": record.factor_id,
            "factor_name": record.factor_name,
            "reason": record.reason,
            "degradation_score": record.degradation_score,
            "previous_status": record.previous_status,
            "new_status": record.new_status,
            "action": record.action,
            "details": record.details,
        }


# ─── GAP-I102 (v2.71.0): Alpha 审查工作流 ─────────────────


class ReviewDecision(str, Enum):
    """人工审查决定（GAP-I102）。"""

    APPROVED = "approved"
    REJECTED = "rejected"


class FactorReviewWorkflow:
    """Alpha 审查工作流（GAP-I102，v2.71.0 骨架 + v2.80.0 经验链闭环）。

    WorldQuant 模式人审前置：在 Verifier + 质量卡 + 审计等自动审查之外，
    提供人工审查环节。审查状态机 pending → approved / rejected；
    审查意见回写 DuckDB ``factor_reviews`` 表（幂等 UPSERT）。
    二期（v2.80.0）：驳回意见接入经验链（GAP-I102 ③），LLM 下一轮
    知识补给/演化参考，避免重复踩坑。

    Args:
        repo: 因子仓库（可选，默认新建）
        db_path: DuckDB 文件路径（可选，覆盖默认库）
        experience_chain: 经验链实例（可选注入，便于测试；None 时懒加载默认实例）
    """

    def __init__(
        self,
        repo: Optional[FactorRepository] = None,
        db_path: Optional[str] = None,
        experience_chain: Optional[Any] = None,
    ) -> None:
        self._repo = repo or FactorRepository()
        self._db_path = db_path
        self._experience_chain = experience_chain

    def _get_experience_chain(self):
        """懒加载经验链实例（开关关闭返回 None）。"""
        if self._experience_chain is not None:
            return self._experience_chain
        if not _load_review_experience_enabled():
            return None
        from .experience_chain import ExperienceChain

        return ExperienceChain()

    def _conn(self):
        """获取审查表连接（连接由调用方关闭）。"""
        from .factor_db import schema

        return schema.get_connection(self._db_path)

    def list_pending(
        self,
        market: Optional[str] = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出待审查因子队列（factor_catalog 中尚无 review 记录的因子）。

        Args:
            market: 限定市场（None 为全部）
            limit: 队列上限

        Returns:
            待审查因子列表（factor_id/name/market/source/ic/sharpe）
        """
        conn = self._conn()
        try:
            params: list[Any] = []
            where = "NOT EXISTS (SELECT 1 FROM factor_reviews r WHERE r.factor_id = c.factor_id)"
            if market:
                where += " AND c.market = ?"
                params.append(market)
            rows = conn.execute(
                f"""
                SELECT c.factor_id, c.name, c.market, c.source, c.ic, c.sharpe
                FROM factor_catalog c
                WHERE {where}
                ORDER BY c.created_at DESC
                LIMIT ?
                """,
                [*params, int(limit)],
            ).fetchall()
            cols = ["factor_id", "name", "market", "source", "ic", "sharpe"]
            return [dict(zip(cols, r)) for r in rows]
        finally:
            conn.close()

    def approve(
        self,
        factor_id: str,
        comment: str = "",
        reviewer: str = "cli",
    ) -> dict[str, Any]:
        """批准因子（pending → approved，意见回写 DuckDB）。"""
        return self._decide(factor_id, ReviewDecision.APPROVED, comment, reviewer)

    def reject(
        self,
        factor_id: str,
        comment: str = "",
        reviewer: str = "cli",
    ) -> dict[str, Any]:
        """驳回因子（pending → rejected，意见回写 DuckDB）。"""
        return self._decide(factor_id, ReviewDecision.REJECTED, comment, reviewer)

    def get_status(self, factor_id: str) -> Optional[dict[str, Any]]:
        """查询因子审查状态（无审查记录返回 None）。"""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT factor_id, decision, comment, reviewer, reviewed_at FROM factor_reviews WHERE factor_id = ?",
                [factor_id],
            ).fetchall()
            if not rows:
                return None
            return {
                "factor_id": rows[0][0],
                "decision": rows[0][1],
                "comment": rows[0][2],
                "reviewer": rows[0][3],
                "reviewed_at": str(rows[0][4]),
            }
        finally:
            conn.close()

    def _decide(
        self,
        factor_id: str,
        decision: ReviewDecision,
        comment: str,
        reviewer: str,
    ) -> dict[str, Any]:
        """执行审查决定（幂等 UPSERT，同因子重复审查覆盖旧决定）。

        驳回（REJECTED）且 comment 非空时，将人审意见写入经验链（GAP-I102 二期）。
        """
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO factor_reviews "
                "(factor_id, decision, comment, reviewer, reviewed_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (factor_id) DO UPDATE SET "
                "decision = excluded.decision, comment = excluded.comment, "
                "reviewer = excluded.reviewer, reviewed_at = excluded.reviewed_at",
                [factor_id, decision.value, comment, reviewer, datetime.now().isoformat()],
            )
            conn.execute("CHECKPOINT")
        finally:
            conn.close()
        logger.info(
            "[review] 因子 %s 审查决定: %s (comment=%s, reviewer=%s)",
            factor_id,
            decision.value,
            comment or "-",
            reviewer,
        )
        # GAP-I102 二期: 驳回意见接入经验链（LLM 下一轮参考）
        if decision == ReviewDecision.REJECTED and comment.strip():
            self._record_rejection(factor_id, comment, reviewer)
        return {
            "factor_id": factor_id,
            "decision": decision.value,
            "reviewed_at": datetime.now().isoformat(),
            "status": "ok",
        }

    def _record_rejection(self, factor_id: str, comment: str, reviewer: str) -> None:
        """将人审驳回意见写入经验链失败轨迹（异常降级不阻断审查流程）。"""
        chain = self._get_experience_chain()
        if chain is None:
            return
        try:
            # 读取因子元信息（code/name，供 LLM 诊断上下文）
            name, code = "", ""
            try:
                meta = self._repo.get_factor(factor_id) if hasattr(self._repo, "get_factor") else None
                if meta:
                    name = meta.get("name", "") if isinstance(meta, dict) else getattr(meta, "name", "")
                    code = meta.get("code", "") if isinstance(meta, dict) else getattr(meta, "code", "")
            except Exception:  # noqa: BLE001 - 元信息读取失败不影响经验链记录
                pass
            from .contracts import ExperienceTrace

            trace = ExperienceTrace(
                trace_id=f"review_{factor_id[:8]}_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                factor_id=factor_id,
                parent_id=None,
                generation=0,
                mutation_type="combined",
                mutation_summary=f"人审驳回因子 {name or factor_id}: {comment[:200]}",
                evaluation={"failure_reasons": [f"人审驳回({reviewer}): {comment[:500]}"]},
                success=False,
                lessons=[f"人审驳回({reviewer}): {comment[:300]}"],
                recorded_at=datetime.now().isoformat(),
                factor_code=code or "",
            )
            chain.record_failure(trace)
            logger.info("[review] 因子 %s 驳回意见已写入经验链 (trace_id=%s)", factor_id, trace["trace_id"])
        except Exception as e:  # noqa: BLE001 - 经验链失败降级
            logger.warning("[review] 驳回意见写经验链失败: %s", e)


__all__ = [
    "FactorInspector",
    "DowngradeRecord",
    "ReviewDecision",
    "FactorReviewWorkflow",
]
