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
from typing import Any, Optional

from .factor_db import FactorLineage, FactorRepository

logger = logging.getLogger(__name__)


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
                degraded_factors.append({
                    "factor_id": factor_id,
                    "factor_name": factor_name,
                    "degradation_score": degradation.get("degradation_score", 0.0),
                    "recommendation": degradation.get("recommendation", "Sharpe持续退化"),
                    "sharpe_trend": degradation.get("sharpe_trend", {}),
                    "ic_trend": degradation.get("ic_trend", {}),
                })

        # 3. 遍历退化因子，执行降级
        for info in degraded_factors:
            factor_id = info.get("factor_id", "")
            factor_name = info.get("factor_name", "unknown")
            degradation_score = info.get("degradation_score", 0.0)

            # 获取当前因子状态
            factor = self._repo.get_factor(factor_id)
            if not factor:
                records.append(DowngradeRecord(
                    factor_id=factor_id,
                    factor_name=factor_name,
                    reason=info.get("recommendation", "未知"),
                    degradation_score=degradation_score,
                    previous_status="unknown",
                    new_status="unknown",
                    action="error",
                    details={"error": "因子不存在"},
                ))
                error_count += 1
                continue

            previous_status = factor.get("status", "active")
            is_elite = factor.get("is_elite", False)

            # 已经降级的跳过
            if previous_status == "degraded" or not is_elite:
                records.append(DowngradeRecord(
                    factor_id=factor_id,
                    factor_name=factor_name,
                    reason=info.get("recommendation", "已降级"),
                    degradation_score=degradation_score,
                    previous_status=previous_status,
                    new_status=previous_status,
                    action="skipped",
                    details={"reason": "已处于降级状态或非精英"},
                ))
                skipped_count += 1
                continue

            # 4. 执行降级
            if commit:
                success = self._repo.update_factor(factor_id, {
                    "is_elite": False,
                    "status": "degraded",
                })
                if success:
                    action = "downgraded"
                    new_status = "degraded"
                    downgraded_count += 1
                    logger.info(
                        "[FactorInspector] 降级因子: %s (%s), Sharpe变化=%.2f",
                        factor_id, factor_name, degradation_score,
                    )
                else:
                    action = "error"
                    new_status = previous_status
                    error_count += 1
                    logger.warning(
                        "[FactorInspector] 降级失败: %s", factor_id
                    )
            else:
                action = "downgraded"
                new_status = "degraded"
                downgraded_count += 1
                logger.info(
                    "[FactorInspector] [DRY-RUN] 拟降级因子: %s (%s)",
                    factor_id, factor_name,
                )

            records.append(DowngradeRecord(
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
            ))

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
            logger.warning(
                "[FactorInspector] 重新激活失败，因子不存在: %s", factor_id
            )
            return False

        updates: dict[str, Any] = {"status": "active"}
        if promote_to_elite:
            updates["is_elite"] = True
        success = self._repo.update_factor(factor_id, updates)
        if success:
            logger.info(
                "[FactorInspector] 重新激活因子: %s (promote=%s)",
                factor_id, promote_to_elite,
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


__all__ = [
    "FactorInspector",
    "DowngradeRecord",
]
