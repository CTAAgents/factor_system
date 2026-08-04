"""
tests/scenarios/validator.py — 宏观行为场景验证器

HARNESS §11-logic-review-plan.md §A.2:
    对每个场景运行因子程序并检查输出是否符合预期行为。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from fts.factor_engine.contracts import FactorProgram
from fts.factor_engine.factor_program import FactorExecutor

from .definitions import ALL_SCENARIOS, ScenarioDefinition


@dataclass
class ScenarioResult:
    """单个场景的验证结果。

    Attributes:
        scenario_name: 场景名称
        category: 场景分类
        description: 场景描述
        expected_behavior: 期望行为
        signal_range_passed: 信号范围检查是否通过（None=未检查）
        signal_last: 最后一个信号值
        check_fn_passed: 自定义验证是否通过（None=未检查）
        check_fn_message: 自定义验证信息
        passed: 是否通过（所有检查项均通过）
        error: 异常信息（如有）
        signal_min: 信号最小值
        signal_max: 信号最大值
        signal_mean: 信号均值
        signal_std: 信号标准差
    """
    scenario_name: str
    category: str
    description: str
    expected_behavior: str
    signal_range_passed: Optional[bool] = None
    signal_last: float = 0.0
    check_fn_passed: Optional[bool] = None
    check_fn_message: str = ""
    passed: bool = False
    error: Optional[str] = None
    signal_min: float = 0.0
    signal_max: float = 0.0
    signal_mean: float = 0.0
    signal_std: float = 0.0


@dataclass
class ScenarioSummary:
    """场景验证汇总报告。

    Attributes:
        total: 总场景数
        passed: 通过数
        failed: 失败数
        errored: 异常数
        results: 每个场景的详细结果
        pass_rate: 通过率
    """
    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total

    def print_report(self) -> str:
        """生成可读的汇总报告。"""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append("宏观行为场景测试报告")
        lines.append("=" * 70)
        lines.append(f"总计: {self.total}  |  通过: {self.passed}  "
                      f"|  失败: {self.failed}  |  异常: {self.errored}  "
                      f"|  通过率: {self.pass_rate:.1%}")
        lines.append("")

        # 按分类聚合
        categories: dict[str, list[ScenarioResult]] = {}
        for r in self.results:
            categories.setdefault(r.category, []).append(r)

        for cat, cat_results in sorted(categories.items()):
            cat_passed = sum(1 for r in cat_results if r.passed)
            lines.append(f"  [{cat}] {cat_passed}/{len(cat_results)} 通过")
            for r in cat_results:
                status = "✓" if r.passed else "✗"
                if r.error:
                    lines.append(f"    {status} {r.scenario_name}: ERROR - {r.error}")
                elif not r.passed:
                    detail = r.check_fn_message if r.check_fn_message else (
                        f"信号范围 [{r.signal_min:.2f}, {r.signal_max:.2f}] 超出期望"
                    )
                    lines.append(f"    {status} {r.scenario_name}: {detail}")
                else:
                    lines.append(f"    {status} {r.scenario_name}")

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)


class ScenarioValidator:
    """宏观行为场景验证器。

    对每个场景运行因子程序，检查输出信号是否符合预期行为。

    Usage:
        validator = ScenarioValidator()
        summary = validator.validate_all(factor, forward_returns)
        print(summary.print_report())
    """

    def __init__(self, scenarios: Optional[list[ScenarioDefinition]] = None):
        self._scenarios = scenarios or ALL_SCENARIOS

    def validate_scenario(
        self,
        factor: FactorProgram,
        scenario: ScenarioDefinition,
        forward_returns: Optional[np.ndarray] = None,
    ) -> ScenarioResult:
        """验证单个场景。

        Args:
            factor: 因子程序
            scenario: 场景定义
            forward_returns: 可选，未来收益率（用于 IC 计算，非必需）

        Returns:
            ScenarioResult
        """
        try:
            data, metadata = scenario.generate_data()

            # 运行因子程序
            executor = FactorExecutor(factor)
            params = factor.get("params", {})
            signal = executor.execute(data, params)

            if not isinstance(signal, np.ndarray) or len(signal) == 0:
                return ScenarioResult(
                    scenario_name=scenario.name,
                    category=scenario.category,
                    description=scenario.description,
                    expected_behavior=scenario.expected_behavior,
                    error="因子程序返回空信号",
                    passed=False,
                )

            signal_last = float(signal[-1]) if len(signal) > 0 else 0.0
            signal_min = float(np.min(signal))
            signal_max = float(np.max(signal))
            signal_mean = float(np.mean(signal))
            signal_std = float(np.std(signal))

            signal_range_passed: Optional[bool] = None
            check_fn_passed: Optional[bool] = None
            check_fn_message = ""

            # 检查信号范围
            if scenario.expected_signal_range is not None:
                lo, hi = scenario.expected_signal_range
                signal_range_passed = lo <= signal_last <= hi

            # 运行自定义验证函数
            if scenario.check_fn is not None:
                fn_passed, fn_msg = scenario.check_fn(signal, metadata)
                check_fn_passed = fn_passed
                check_fn_message = fn_msg

            # 综合判定
            checks = []
            if signal_range_passed is not None:
                checks.append(signal_range_passed)
            if check_fn_passed is not None:
                checks.append(check_fn_passed)
            passed = all(checks) if checks else True

            return ScenarioResult(
                scenario_name=scenario.name,
                category=scenario.category,
                description=scenario.description,
                expected_behavior=scenario.expected_behavior,
                signal_range_passed=signal_range_passed,
                signal_last=signal_last,
                check_fn_passed=check_fn_passed,
                check_fn_message=check_fn_message,
                passed=passed,
                signal_min=signal_min,
                signal_max=signal_max,
                signal_mean=signal_mean,
                signal_std=signal_std,
            )

        except Exception as e:
            return ScenarioResult(
                scenario_name=scenario.name,
                category=scenario.category,
                description=scenario.description,
                expected_behavior=scenario.expected_behavior,
                error=str(e),
                passed=False,
            )

    def validate_all(
        self,
        factor: FactorProgram,
        forward_returns: Optional[np.ndarray] = None,
    ) -> ScenarioSummary:
        """验证所有场景。

        Args:
            factor: 因子程序
            forward_returns: 可选，未来收益率

        Returns:
            ScenarioSummary
        """
        results: list[ScenarioResult] = []
        for scenario in self._scenarios:
            result = self.validate_scenario(factor, scenario, forward_returns)
            results.append(result)

        total = len(results)
        passed = sum(1 for r in results if r.passed and r.error is None)
        failed = sum(1 for r in results if not r.passed and r.error is None)
        errored = sum(1 for r in results if r.error is not None)

        return ScenarioSummary(
            total=total,
            passed=passed,
            failed=failed,
            errored=errored,
            results=results,
        )


__all__ = ["ScenarioValidator", "ScenarioResult", "ScenarioSummary"]