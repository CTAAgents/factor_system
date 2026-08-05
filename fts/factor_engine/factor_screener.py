"""
fts.factor_engine.factor_screener — 因子筛选器（B.2 Stage 1）。

按质量等级、总分、状态、风格标签筛选待回测因子。
支持内存列表筛选与 DuckDB 仓库查询两种入口，无匹配时优雅返回空列表。

用法:
    from fts.factor_engine.factor_screener import FactorScreener

    screener = FactorScreener()
    factors = screener.screen(
        factors=all_factors,
        min_grade="B",
        min_total_score=40.0,
        status=["active"],
    )

版本: v1.0.0
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 等级顺序（A > B > C > D）
_GRADE_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1}


class FactorScreener:
    """因子筛选器（B.2 Stage 1）。

    按评分卡等级、质量总分、因子状态、风格标签筛选因子。
    """

    def __init__(
        self,
        repo: Any | None = None,
        market: str = "futures",
    ) -> None:
        """初始化筛选器。

        Args:
            repo: FactorRepository 实例（可选，screen 时 factors=None 时使用）
            market: 市场类型（"futures" / "stock"，DuckDB 查询时使用）
        """
        self._repo = repo
        self._market = market

    # ─── 主入口 ──────────────────────────────────────────

    def screen(
        self,
        factors: Optional[list[dict[str, Any]]] = None,
        min_grade: str = "B",
        min_total_score: Optional[float] = None,
        status: Optional[list[str]] = None,
        style_filter: Optional[list[str]] = None,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """筛选符合条件的因子。

        Args:
            factors: 因子列表。为 None 时从 DuckDB 仓库查询。
            min_grade: 最低等级（A > B > C，默认 B）
            min_total_score: 最低质量总分（可选）
            status: 允许的因子状态列表（如 ["active", "observing"]）
            style_filter: 风格标签过滤（因子含 style_tags 字段时生效）
            limit: 返回数量上限（可选）

        Returns:
            筛选后的因子列表（无匹配时返回空列表）。
        """
        candidates = factors if factors is not None else self._load_from_repo()

        if not candidates:
            logger.info("[FactorScreener] 无候选因子，返回空列表")
            return []

        grade_min = _GRADE_ORDER.get(min_grade, 3)
        filtered: list[dict[str, Any]] = []
        for f in candidates:
            if not self._pass_grade(f, grade_min):
                continue
            if not self._pass_total_score(f, min_total_score):
                continue
            if not self._pass_status(f, status):
                continue
            if not self._pass_style(f, style_filter):
                continue
            filtered.append(f)

        if limit is not None and limit > 0:
            filtered = filtered[:limit]

        logger.info(
            "[FactorScreener] 筛选完成 [candidates=%d, passed=%d, grade>%s]",
            len(candidates), len(filtered), min_grade,
        )
        return filtered

    # ─── 筛选条件 ────────────────────────────────────────

    @staticmethod
    def _pass_grade(factor: dict[str, Any], grade_min: int) -> bool:
        """等级门槛检查。"""
        grade = str(factor.get("grade", "C") or "C").upper()
        return _GRADE_ORDER.get(grade, 2) >= grade_min

    @staticmethod
    def _pass_total_score(
        factor: dict[str, Any], min_total_score: Optional[float]
    ) -> bool:
        """总分门槛检查。"""
        if min_total_score is None:
            return True
        score = factor.get("total_score")
        if score is None:
            score = factor.get("quality_score")  # 兼容旧字段名
        return score is not None and float(score) >= min_total_score

    @staticmethod
    def _pass_status(
        factor: dict[str, Any], status: Optional[list[str]]
    ) -> bool:
        """状态过滤。"""
        if not status:
            return True
        f_status = str(factor.get("status", "") or "")
        return f_status in status

    @staticmethod
    def _pass_style(
        factor: dict[str, Any], style_filter: Optional[list[str]]
    ) -> bool:
        """风格标签过滤。"""
        if not style_filter:
            return True
        tags = factor.get("style_tags") or factor.get("style") or []
        if isinstance(tags, str):
            tags = [tags]
        return any(tag in style_filter for tag in tags)

    # ─── 仓库入口 ────────────────────────────────────────

    def _load_from_repo(self) -> list[dict[str, Any]]:
        """从 DuckDB 仓库加载因子（失败时降级为空列表）。"""
        if self._repo is None:
            try:
                from .factor_db.repository import FactorRepository

                self._repo = FactorRepository()
            except Exception as e:  # noqa: BLE001
                logger.warning("[FactorScreener] 仓库初始化失败: %s", e)
                return []
        try:
            factors = self._repo.get_eligible(
                market=self._market,
                require_elite=True,
            )
            return list(factors or [])
        except Exception as e:  # noqa: BLE001
            logger.warning("[FactorScreener] 仓库查询失败: %s", e)
            return []


__all__ = ["FactorScreener"]
