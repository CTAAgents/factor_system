"""
fts.pipeline.factor_quality_inspection — 因子质检过滤层

将 FactorQualityCard 集成到因子质检过滤流水线中。
从评估结果 (FactorEvaluation) 提取指标 → 计算质量评分卡 → 分级准入 (A/B/C)。

用法:
    inspection = FactorQualityInspection()
    result = inspection.inspect(
        factor=factor_program,
        evaluation=evaluation_result,
        walk_forward_result=wf_result,
        decay_rate=0.12,
        ...
    )
    if result["grade"] in ("A", "B"):
        # 入库
        ...

版本: v1.0.0
"""

from __future__ import annotations

from typing import Literal, Optional

from ..factor_engine.contracts import (
    EconomicScore,
    FactorEvaluation,
    FactorProgram,
)
from ..factor_engine.factor_quality_card import (
    FactorQualityCard,
    FactorQualityCardConfig,
    FactorQualityScore,
    DimensionScore,
    determine_grade,
)
from ..factor_engine.walk_forward import WalkForwardResult


Grade = Literal["A", "B", "C"]


class InspectionResult:
    """质检结果。

    Attributes:
        factor_id: 因子 ID
        passed: 是否通过质检 (grade != 'C')
        grade: 等级 (A/B/C)
        quality_score: 完整质量评分卡
        total_score: 总分 (0-50)
        filtered: 是否被过滤 (grade == 'C')
        reason: 过滤原因 (仅当 filtered=True)
    """

    def __init__(
        self,
        factor_id: str,
        passed: bool,
        grade: Grade,
        quality_score: FactorQualityScore,
        total_score: float,
        filtered: bool,
        reason: str = "",
    ) -> None:
        self.factor_id = factor_id
        self.passed = passed
        self.grade = grade
        self.quality_score = quality_score
        self.total_score = total_score
        self.filtered = filtered
        self.reason = reason

    def to_dict(self) -> dict:
        return {
            "factor_id": self.factor_id,
            "passed": self.passed,
            "grade": self.grade,
            "total_score": self.total_score,
            "filtered": self.filtered,
            "reason": self.reason,
            "quality_score": self.quality_score,
        }


class FactorQualityInspection:
    """因子质检过滤器 — 将质量评分卡集成到质检流水线。

    Usage:
        inspection = FactorQualityInspection()
        result = inspection.inspect(
            factor=factor_program,
            evaluation=evaluation_result,
            walk_forward_result=wf_result,
        )
        if result.passed:
            # 入库 elite 池
        else:
            # 过滤 / 降级
    """

    def __init__(
        self,
        card_config: Optional[FactorQualityCardConfig] = None,
        min_grade: Grade = "B",
    ) -> None:
        """初始化质检过滤器。

        Args:
            card_config: 评分卡配置 (None = 默认配置)
            min_grade: 最低准入等级 (默认 B，即 A 和 B 均通过)
        """
        self._card = FactorQualityCard(card_config)
        self._min_grade = min_grade

    @property
    def card(self) -> FactorQualityCard:
        """底层评分卡计算器。"""
        return self._card

    @property
    def min_grade(self) -> Grade:
        """最低准入等级。"""
        return self._min_grade

    @min_grade.setter
    def min_grade(self, value: Grade) -> None:
        self._min_grade = value

    # ─── 公有方法 ──────────────────────────────────

    def inspect(
        self,
        *,
        factor: FactorProgram,
        evaluation: FactorEvaluation,
        walk_forward_result: Optional[WalkForwardResult] = None,
        decay_rate: float = 0.2,
        turnover: Optional[float] = None,
        correlation_max: float = 0.5,
        cross_symbol_coverage: float = 0.6,
        capacity_estimate: float = 10_000_000,
    ) -> InspectionResult:
        """执行因子质检。

        从评估结果提取 IC/Sharpe 等指标，计算质量评分卡，判定等级。

        Args:
            factor: 因子程序
            evaluation: L1/L2/L3 评估结果
            walk_forward_result: WalkForward 结果 (可选)
            decay_rate: 衰减率 (月环比 IC 下降幅度)
            turnover: 换手率 (可选，默认从评估结果提取)
            correlation_max: 与已有因子的最大相关性
            cross_symbol_coverage: 跨品种覆盖率
            capacity_estimate: 容量估算 (元)

        Returns:
            InspectionResult: 质检结果
        """
        # ── 从评估结果提取指标 ──
        bt = evaluation.get("level_1_backtest", {})
        ic = bt.get("ic", 0.0)
        icir = bt.get("icir", 0.0)
        sharpe = bt.get("sharpe", 0.0)
        turnover_val = turnover if turnover is not None else bt.get("turnover_monthly", 0.3)

        # Calmar = 年化收益 / 最大回撤 (简化计算)
        max_dd = bt.get("max_drawdown", 0.0)
        calmar = 0.0
        if max_dd > 0 and sharpe > 0:
            calmar = sharpe  # 简化: 假设无风险利率=0，年化收益≈Sharpe

        # 经济逻辑评分
        econ = evaluation.get("level_2_economic", {})
        logic_score = self._extract_logic_score(econ)

        # 数据频率
        freq = factor.get("signature", {}).get("frequency", "daily")

        # ── 计算质量评分卡 ──
        quality_score = self._card.evaluate(
            factor_id=factor.get("factor_id", "unknown"),
            ic=ic,
            sharpe=sharpe,
            walk_forward_result=walk_forward_result,
            decay_rate=decay_rate,
            turnover=turnover_val,
            correlation_max=correlation_max,
            logic_score=logic_score,
            data_frequency=freq,
            cross_symbol_coverage=cross_symbol_coverage,
            capacity_estimate=capacity_estimate,
            icir=icir,
            calmar=calmar,
        )

        total_score = quality_score["total_score"]
        grade = quality_score["grade"]

        # ── 判定是否通过 ──
        passed = self._is_grade_acceptable(grade)
        filtered = not passed
        reason = ""
        if filtered:
            reason = (
                f"等级 {grade} 低于准入阈值 {self._min_grade} "
                f"(总分 {total_score}/50)"
            )

        return InspectionResult(
            factor_id=factor.get("factor_id", "unknown"),
            passed=passed,
            grade=grade,
            quality_score=quality_score,
            total_score=total_score,
            filtered=filtered,
            reason=reason,
        )

    def batch_inspect(
        self,
        items: list[dict],
    ) -> list[InspectionResult]:
        """批量质检多个因子。

        Args:
            items: 列表，每个元素包含:
                - factor: FactorProgram
                - evaluation: FactorEvaluation
                - walk_forward_result: Optional[WalkForwardResult]
                - decay_rate: float
                - turnover: Optional[float]
                - correlation_max: float
                - cross_symbol_coverage: float
                - capacity_estimate: float

        Returns:
            list[InspectionResult]: 质检结果列表
        """
        return [self.inspect(**item) for item in items]

    def filter_passed(
        self,
        items: list[dict],
    ) -> tuple[list[InspectionResult], list[InspectionResult]]:
        """批量质检并分离通过/不通过的因子。

        Returns:
            (passed_results, failed_results)
        """
        results = self.batch_inspect(items)
        passed = [r for r in results if r.passed]
        failed = [r for r in results if not r.passed]
        return passed, failed

    # ─── 私有方法 ──────────────────────────────────

    def _extract_logic_score(self, econ: EconomicScore) -> int:
        """从经济逻辑评分提取综合分。

        计算方式: 达标维度数 / 4 * 5 (0-5 分)
        如果有 dimensions_passed 字段，直接使用。
        """
        if not econ:
            return 3  # 默认中等分

        # 如果有 dimensions_passed
        dims_passed = econ.get("dimensions_passed", 0)
        if dims_passed > 0:
            return int(min(dims_passed / 4.0 * 5.0, 5.0))

        # 否则计算各维度平均分
        total = 0
        count = 0
        for dim in ("theory", "behavioral", "microstructure", "institutional"):
            val = econ.get(dim)
            if isinstance(val, (int, float)):
                total += val
                count += 1
        if count > 0:
            return int(min(total / count, 5.0))

        return 3  # 默认中等分

    def _is_grade_acceptable(self, grade: Grade) -> bool:
        """判断等级是否满足准入要求。"""
        grade_order: dict[Grade, int] = {"A": 3, "B": 2, "C": 1}
        required = grade_order.get(self._min_grade, 2)
        actual = grade_order.get(grade, 1)
        return actual >= required


# ─── 便捷函数 ──────────────────────────────────────────


def inspect_factor(
    *,
    factor: FactorProgram,
    evaluation: FactorEvaluation,
    walk_forward_result: Optional[WalkForwardResult] = None,
    decay_rate: float = 0.2,
    turnover: Optional[float] = None,
    correlation_max: float = 0.5,
    cross_symbol_coverage: float = 0.6,
    capacity_estimate: float = 10_000_000,
    min_grade: Grade = "B",
) -> InspectionResult:
    """便捷函数：单因子质检。

    Args:
        factor: 因子程序
        evaluation: 评估结果
        walk_forward_result: WalkForward 结果
        decay_rate: 衰减率
        turnover: 换手率
        correlation_max: 最大相关性
        cross_symbol_coverage: 跨品种覆盖率
        capacity_estimate: 容量估算
        min_grade: 最低准入等级

    Returns:
        InspectionResult
    """
    inspector = FactorQualityInspection(min_grade=min_grade)
    return inspector.inspect(
        factor=factor,
        evaluation=evaluation,
        walk_forward_result=walk_forward_result,
        decay_rate=decay_rate,
        turnover=turnover,
        correlation_max=correlation_max,
        cross_symbol_coverage=cross_symbol_coverage,
        capacity_estimate=capacity_estimate,
    )