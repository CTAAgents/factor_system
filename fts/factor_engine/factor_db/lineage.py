"""factor_db/lineage.py — 因子数据血缘审计

通过 DuckDB 事务日志 + 版本历史实现因子数据血缘追踪。
支持因子演化谱系查询、评估历史追溯、信号贡献分析。

核心功能:
1. 演化谱系: 追踪因子从种子到精英的完整演化路径
2. 评估追溯: 查询因子的所有评估历史（L1/L2/L3）
3. 信号贡献: 统计因子在组合/信号中的使用记录
4. 质量退化检测: 自动识别因子质量下降趋势
5. 血缘报告: 生成因子数据血缘审计报告

版本: v1.0 (GAP-023 核心实现)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


class FactorLineage:
    """因子数据血缘审计器。

    基于 factor_versions + factor_evaluations 表实现血缘追踪。
    支持因子演化谱系、评估历史、使用记录的完整查询。
    """

    def __init__(self, repo=None, market: str = "futures"):
        """初始化血缘审计器。

        Args:
            repo: FactorRepository 实例（可选，延迟初始化）
            market: 市场类型（"futures"），延迟初始化时使用
        """
        self._repo = repo
        self._market = market
        self._conn = None

    @property
    def repo(self):
        if self._repo is None:
            from .repository import FactorRepository

            self._repo = FactorRepository(market=self._market)
        return self._repo

    def _get_conn(self):
        if self._conn is None:
            self._conn = self.repo._get_conn()
        return self._conn

    # ─=== 1. 演化谱系查询 ──────────────────────────────────────

    def get_lineage(self, factor_id: str) -> dict[str, Any]:
        """获取因子的完整演化谱系。

        从当前因子向上追溯父因子链，向下追溯子因子（通过 parent_id 关联）。

        Args:
            factor_id: 因子 ID

        Returns:
            谱系字典，包含:
            - factor: 当前因子信息
            - ancestors: 父因子链（从种子到父因子）
            - descendants: 子因子列表
            - versions: 版本变更历史
            - evaluations: 评估历史摘要
        """
        factor = self.repo.get_factor(factor_id)
        if not factor:
            return {"error": f"因子 {factor_id} 不存在"}

        ancestors = self._trace_ancestors(factor)
        descendants = self._find_descendants(factor_id)
        versions = self.repo.get_versions(factor_id)
        evaluations = self.repo.get_evaluations(factor_id, limit=50)

        return {
            "factor_id": factor_id,
            "factor_info": {
                "factor_id": factor_id,
                "name": factor.get("name"),
                "family": factor.get("family"),
                "source": factor.get("source"),
                "generation": factor.get("generation", 0),
                "status": factor.get("status"),
                "is_elite": factor.get("is_elite", False),
                "sharpe": factor.get("sharpe", 0.0),
                "ic": factor.get("ic", 0.0),
                "created_at": factor.get("created_at"),
            },
            "ancestors": ancestors,
            "descendants": descendants,
            "versions": versions,
            "evaluations_summary": {
                "total_evals": len(evaluations),
                "last_eval": evaluations[0] if evaluations else None,
                "avg_sharpe": self._avg_metric(evaluations, "level_1_sharpe"),
                "pass_rate": self._calc_pass_rate(evaluations),
            },
        }

    def _trace_ancestors(self, factor: dict) -> list[dict[str, Any]]:
        """向上追溯父因子链。"""
        chain = []
        current = factor
        visited = set()

        while current and current.get("parent_id"):
            pid = current["parent_id"]
            if pid in visited:
                break
            visited.add(pid)

            parent = self.repo.get_factor(pid)
            if not parent:
                chain.append({"factor_id": pid, "status": "missing"})
                break

            chain.append(
                {
                    "factor_id": parent["factor_id"],
                    "name": parent.get("name"),
                    "generation": parent.get("generation", 0),
                    "source": parent.get("source"),
                    "status": parent.get("status"),
                }
            )
            current = parent

        return chain

    def _find_descendants(self, factor_id: str) -> list[dict[str, Any]]:
        """查找子因子列表。"""
        conn = self._get_conn()
        result = conn.execute(
            """
            SELECT factor_id, name, generation, source, status
            FROM factor_catalog
            WHERE parent_id = ?
        """,
            [factor_id],
        )
        rows = result.fetchall()
        return [
            {
                "factor_id": row[0],
                "name": row[1],
                "generation": row[2],
                "source": row[3],
                "status": row[4],
            }
            for row in rows
        ]

    # ─=== 2. 评估历史追溯 ──────────────────────────────────────

    def get_evaluation_trend(
        self,
        factor_id: str,
        metric: str = "sharpe",
        window: int = 30,
    ) -> dict[str, Any]:
        """获取因子评估指标的时间趋势。

        Args:
            factor_id: 因子 ID
            metric: 指标名称 (sharpe/ic/icir/max_dd/turnover)
            window: 时间窗口（天数）

        Returns:
            趋势分析结果
        """
        evaluations = self.repo.get_evaluations(factor_id, limit=500)
        if not evaluations:
            return {"factor_id": factor_id, "trend": "no_data"}

        metric_key = {
            "sharpe": "level_1_sharpe",
            "ic": "level_1_ic",
            "icir": "level_1_icir",
            "max_dd": "level_1_max_dd",
            "turnover": "level_1_turnover",
        }.get(metric, metric)

        values = []
        for eval in evaluations:
            val = eval.get(metric_key)
            if val is not None:
                values.append(
                    {
                        "value": float(val),
                        "timestamp": eval.get("evaluated_at"),
                        "passed": eval.get("overall_passed", False),
                    }
                )

        if len(values) < 2:
            return {"factor_id": factor_id, "trend": "insufficient_data"}

        values = list(reversed(values))

        mid = len(values) // 2
        older_vals = [v["value"] for v in values[:mid]]
        recent_vals = [v["value"] for v in values[mid:]]

        if not recent_vals or not older_vals:
            return {"factor_id": factor_id, "trend": "insufficient_data"}

        recent_avg = float(sum(recent_vals)) / len(recent_vals)
        older_avg = float(sum(older_vals)) / len(older_vals)

        delta = recent_avg - older_avg
        pct_change = float(delta / abs(older_avg) * 100) if older_avg != 0 else 0.0

        if pct_change > 10:
            trend = "improving"
        elif pct_change < -10:
            trend = "declining"
        else:
            trend = "stable"

        return {
            "factor_id": factor_id,
            "metric": metric,
            "trend": trend,
            "recent_avg": round(recent_avg, 4),
            "older_avg": round(older_avg, 4),
            "delta": round(delta, 4),
            "pct_change": round(pct_change, 2),
            "data_points": len(values),
            "recent_values": values[:10],
        }

    # ─=== 3. 质量退化检测 ──────────────────────────────────────

    def detect_degradation(
        self,
        factor_id: str,
        threshold: float = -0.2,
        window: int = 10,
    ) -> dict[str, Any]:
        """检测因子质量是否退化。

        基于 Sharpe/IC 的近期变化趋势判断是否退化。

        Args:
            factor_id: 因子 ID
            threshold: 退化阈值（Sharpe 变化率）
            window: 滑动窗口大小

        Returns:
            退化检测结果
        """
        sharpe_trend = self.get_evaluation_trend(factor_id, "sharpe")
        ic_trend = self.get_evaluation_trend(factor_id, "ic")

        is_degraded = sharpe_trend.get("trend") == "declining" and sharpe_trend.get("pct_change", 0) < threshold * 100

        return {
            "factor_id": factor_id,
            "is_degraded": is_degraded,
            "degradation_score": round(sharpe_trend.get("pct_change", 0) / 100, 4),
            "sharpe_trend": sharpe_trend,
            "ic_trend": ic_trend,
            "recommendation": ("考虑暂停使用该因子" if is_degraded else "继续监控"),
            "detected_at": datetime.now().isoformat(),
        }

    # ─=== 4. 血缘报告生成 ──────────────────────────────────────

    def generate_lineage_report(
        self,
        factor_id: str,
        include_versions: bool = True,
        include_evaluations: bool = True,
    ) -> dict[str, Any]:
        """生成因子数据血缘审计报告。

        Args:
            factor_id: 因子 ID
            include_versions: 是否包含版本历史
            include_evaluations: 是否包含评估历史

        Returns:
            完整的血缘报告字典
        """
        lineage = self.get_lineage(factor_id)
        degradation = self.detect_degradation(factor_id)

        report = {
            "report_type": "factor_lineage_audit",
            "generated_at": datetime.now().isoformat(),
            "factor_id": factor_id,
            "factor_name": lineage.get("factor_info", {}).get("name", "unknown"),
            "lineage_summary": {
                "generation_depth": len(lineage.get("ancestors", [])),
                "num_descendants": len(lineage.get("descendants", [])),
                "total_versions": len(lineage.get("versions", [])),
                "total_evaluations": lineage.get("evaluations_summary", {}).get("total_evals", 0),
            },
            "evolution_path": [
                {
                    "factor_id": a.get("factor_id"),
                    "name": a.get("name"),
                    "generation": a.get("generation"),
                }
                for a in lineage.get("ancestors", [])
            ],
            "quality_assessment": {
                "current_sharpe": lineage.get("factor_info", {}).get("sharpe", 0),
                "current_ic": lineage.get("factor_info", {}).get("ic", 0),
                "pass_rate": lineage.get("evaluations_summary", {}).get("pass_rate", 0),
                "is_degraded": degradation.get("is_degraded", False),
                "degradation_score": degradation.get("degradation_score", 0),
            },
            "recommendations": self._build_recommendations(lineage, degradation),
        }

        if include_versions:
            report["version_history"] = lineage.get("versions", [])[:20]

        if include_evaluations:
            report["evaluation_history"] = lineage.get("evaluations_summary", {})

        return report

    def _build_recommendations(
        self,
        lineage: dict,
        degradation: dict,
    ) -> list[str]:
        """基于血缘分析构建改善建议。"""
        recommendations = []
        info = lineage.get("factor_info", {})

        if degradation.get("is_degraded"):
            recommendations.append("因子质量退化：建议暂停使用，排查退化原因（市场环境/参数失效/过拟合）")

        sharpe = info.get("sharpe", 0)
        if sharpe < 0.5:
            recommendations.append(f"Sharpe 较低 ({sharpe:.3f})：建议优化参数或调整因子逻辑")

        ic = info.get("ic", 0)
        if ic < 0.03:
            recommendations.append(f"IC 偏低 ({ic:.4f})：因子预测能力有限，建议增加特征维度")

        eval_summary = lineage.get("evaluations_summary", {})
        pass_rate = eval_summary.get("pass_rate", 0)
        if pass_rate < 0.5 and eval_summary.get("total_evals", 0) > 3:
            recommendations.append(f"评估通过率低 ({pass_rate:.1%})：L3 检验不稳定，建议增强因子鲁棒性")

        if not recommendations:
            recommendations.append("因子状态良好，建议定期监控趋势变化")

        return recommendations

    # ─=== 5. 批量血缘审计 ──────────────────────────────────────

    def batch_audit(
        self,
        market: Optional[str] = None,
        min_evals: int = 5,
        limit: int = 50,
    ) -> dict[str, Any]:
        """批量审计市场中所有因子的血缘状态。

        Args:
            market: 市场过滤（None 为全部）
            min_evals: 最少评估次数（低于此值跳过）
            limit: 审计因子数量上限

        Returns:
            批量审计结果
        """
        self._get_conn()
        conditions: list[str] = []
        params: list[Any] = []
        if market:
            conditions.append("market = ?")
            params.append(market)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT * FROM factor_catalog
            {where_clause}
            ORDER BY is_elite DESC, sharpe DESC
            LIMIT ?
        """
        result = self.repo._execute(sql, params + [limit])
        rows = result.fetchall()
        factors = [self.repo._row_to_dict(row) for row in rows]

        results = []
        degraded_count = 0

        for factor in factors:
            fid = factor["factor_id"]
            eval_count = len(self.repo.get_evaluations(fid, limit=100))
            status = factor.get("status", "active")
            is_elite = factor.get("is_elite", False)

            # 已降级的因子直接记录，不再重复检测
            if status == "degraded" or not is_elite:
                results.append(
                    {
                        "factor_id": fid,
                        "name": factor.get("name"),
                        "family": factor.get("family"),
                        "sharpe": factor.get("sharpe", 0),
                        "ic": factor.get("ic", 0),
                        "is_degraded": True,
                        "degradation_score": 0.0,
                        "recommendation": "因子已降级或非精英",
                        "factor_status": status,
                        "is_elite": is_elite,
                        "evaluations": eval_count,
                    }
                )
                degraded_count += 1
                continue

            if eval_count < min_evals:
                results.append(
                    {
                        "factor_id": fid,
                        "name": factor.get("name"),
                        "status": "insufficient_data",
                        "evaluations": eval_count,
                        "factor_status": status,
                        "is_elite": is_elite,
                    }
                )
                continue

            degradation = self.detect_degradation(fid)
            result = {
                "factor_id": fid,
                "name": factor.get("name"),
                "family": factor.get("family"),
                "sharpe": factor.get("sharpe", 0),
                "ic": factor.get("ic", 0),
                "is_degraded": degradation.get("is_degraded", False),
                "degradation_score": degradation.get("degradation_score", 0),
                "recommendation": degradation.get("recommendation", ""),
                "factor_status": status,
                "is_elite": is_elite,
            }
            results.append(result)

            if degradation.get("is_degraded"):
                degraded_count += 1

        return {
            "audit_type": "batch_lineage_audit",
            "market": market,
            "total_audited": len(results),
            "degraded_count": degraded_count,
            "healthy_count": len(results) - degraded_count,
            "degradation_rate": round(degraded_count / len(results), 4) if results else 0,
            "results": results,
            "generated_at": datetime.now().isoformat(),
        }

    # ─=== 内部工具 ──────────────────────────────────────────────

    @staticmethod
    def _avg_metric(evaluations: list[dict], key: str) -> float:
        vals = [e.get(key, 0) for e in evaluations if e.get(key) is not None]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    @staticmethod
    def _calc_pass_rate(evaluations: list[dict]) -> float:
        total = len(evaluations)
        if total == 0:
            return 0.0
        passed = sum(1 for e in evaluations if e.get("overall_passed", False))
        return round(passed / total, 4)
