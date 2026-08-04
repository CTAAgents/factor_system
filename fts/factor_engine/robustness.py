"""
fts/factor_engine/robustness.py — 鲁棒性审查

HARNESS §11-logic-review-plan.md §B.2:
    测试因子在边缘情况下是否崩溃，涵盖对抗样本、缺失值、分布外。

设计:
    - 对抗样本测试: 对输入施加微小扰动（价格 × 1.0001），观察 IC 变化
    - 缺失值测试: 随机删除 5%/10%/20% 数据，观察预测稳定性
    - 分布外测试: 将因子应用到不同品种/市场，观察 IC 保持性

版本: v1.0.0
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from .contracts import FactorProgram
from .evaluation_chain import evaluate_backtest
from .factor_program import FactorExecutor


# ─── 鲁棒性测试结果契约 ──────────────────────────────────


class AdversarialTestResult(dict):
    """对抗样本测试结果。"""
    def __init__(
        self,
        perturbation: str,
        perturbation_factor: float,
        baseline_ic: float,
        perturbed_ic: float,
        ic_change: float,
        passed: bool,
    ) -> None:
        super().__init__()
        self["perturbation"] = perturbation
        self["perturbation_factor"] = perturbation_factor
        self["baseline_ic"] = baseline_ic
        self["perturbed_ic"] = perturbed_ic
        self["ic_change"] = ic_change
        self["passed"] = passed


class MissingValueTestResult(dict):
    """缺失值测试结果。"""
    def __init__(
        self,
        missing_pct: float,
        baseline_ic: float,
        missing_ic: float,
        ic_retention: float,
        passed: bool,
    ) -> None:
        super().__init__()
        self["missing_pct"] = missing_pct
        self["baseline_ic"] = baseline_ic
        self["missing_ic"] = missing_ic
        self["ic_retention"] = ic_retention
        self["passed"] = passed


class OODTestResult(dict):
    """分布外测试结果。"""
    def __init__(
        self,
        scenario: str,
        baseline_ic: float,
        ood_ic: float,
        ic_retention: float,
        passed: bool,
    ) -> None:
        super().__init__()
        self["scenario"] = scenario
        self["baseline_ic"] = baseline_ic
        self["ood_ic"] = ood_ic
        self["ic_retention"] = ic_retention
        self["passed"] = passed


class RobustnessTestResult(dict):
    """完整鲁棒性测试结果。"""
    def __init__(
        self,
        factor_id: str,
        factor_name: str,
        adversarial_results: list[AdversarialTestResult],
        missing_value_results: list[MissingValueTestResult],
        ood_results: list[OODTestResult],
        summary: dict[str, Any],
    ) -> None:
        super().__init__()
        self["factor_id"] = factor_id
        self["factor_name"] = factor_name
        self["adversarial_results"] = adversarial_results
        self["missing_value_results"] = missing_value_results
        self["ood_results"] = ood_results
        self["summary"] = summary


# ─── 数据扰动函数 ─────────────────────────────────────────


def _perturb_prices(
    data: pd.DataFrame,
    factor: float = 1.0001,
) -> pd.DataFrame:
    """对价格列施加微小扰动。"""
    modified = data.copy()
    price_cols = {"open", "high", "low", "close", "vwap", "settle"}
    for col in modified.columns:
        if col.lower() in price_cols:
            modified[col] = modified[col] * factor
    return modified


def _inject_missing(
    data: pd.DataFrame,
    missing_pct: float,
    random_seed: int = 42,
) -> pd.DataFrame:
    """随机删除指定比例的数据（设为 NaN）。

    Args:
        data: 原始数据
        missing_pct: 缺失比例，如 0.05 表示 5%
        random_seed: 随机种子
    """
    modified = data.copy()
    np.random.seed(random_seed)
    mask = np.random.random(modified.shape) < missing_pct
    # 保留 date 列不受影响
    date_cols = {"date", "index", "datetime", "timestamp"}
    feature_cols = [c for c in modified.columns if c.lower() not in date_cols]
    for col in feature_cols:
        col_idx = modified.columns.get_loc(col)
        modified.iloc[mask[:, col_idx], col_idx] = np.nan
    return modified


def _generate_ood_data(
    data: pd.DataFrame,
    scenario: str,
    random_seed: int = 42,
) -> pd.DataFrame:
    """生成分布外数据。

    Args:
        data: 原始数据
        scenario: 场景类型
            - "high_vol": 高波动率（价格波动放大 3 倍）
            - "low_vol": 低波动率（价格波动缩小 0.3 倍）
            - "trending": 强趋势（添加单调趋势）
            - "noisy": 高噪声（添加随机噪声）
    """
    np.random.seed(random_seed)
    modified = data.copy()

    price_cols = {"open", "high", "low", "close", "vwap", "settle"}
    existing_price_cols = [
        c for c in modified.columns if c.lower() in price_cols
    ]

    if scenario == "high_vol":
        for col in existing_price_cols:
            mean_val = modified[col].mean()
            noise = np.random.randn(len(modified)) * mean_val * 0.05
            modified[col] = modified[col] + noise
    elif scenario == "low_vol":
        for col in existing_price_cols:
            mean_val = modified[col].mean()
            noise = np.random.randn(len(modified)) * mean_val * 0.005
            modified[col] = modified[col] + noise
    elif scenario == "trending":
        trend = np.linspace(0, 1, len(modified)) * modified["close"].std() * 2
        for col in existing_price_cols:
            modified[col] = modified[col] + trend
    elif scenario == "noisy":
        for col in existing_price_cols:
            noise = np.random.randn(len(modified)) * modified[col].std() * 0.5
            modified[col] = modified[col] + noise

    return modified


# ─── 鲁棒性测试执行器 ─────────────────────────────────────


class RobustnessTester:
    """鲁棒性审查执行器。

    对因子执行三类鲁棒性测试：
    1. 对抗样本测试
    2. 缺失值测试
    3. 分布外测试

    Usage:
        tester = RobustnessTester()
        result = tester.run(factor, data, forward_returns)
        print(tester.report(result))
    """

    ADVERSARIAL_PERTURBATIONS: list[tuple[str, float]] = [
        ("价格 × 1.0001", 1.0001),
        ("价格 × 0.9999", 0.9999),
        ("价格 × 1.001", 1.001),
        ("价格 × 0.999", 0.999),
    ]

    MISSING_PCTS: list[float] = [0.05, 0.10, 0.20]

    OOD_SCENARIOS: list[str] = ["high_vol", "low_vol", "trending", "noisy"]

    def __init__(
        self,
        adversarial_threshold: float = 0.01,
        missing_retention_threshold: float = 0.80,
        ood_retention_threshold: float = 0.50,
        random_seed: int = 42,
    ):
        """
        Args:
            adversarial_threshold: 对抗样本 IC 变化阈值（默认 0.01）
            missing_retention_threshold: 缺失值 IC 保持率阈值（默认 80%）
            ood_retention_threshold: 分布外 IC 保持率阈值（默认 50%）
            random_seed: 随机种子
        """
        self._adversarial_threshold = adversarial_threshold
        self._missing_retention_threshold = missing_retention_threshold
        self._ood_retention_threshold = ood_retention_threshold
        self._random_seed = random_seed

    def run(
        self,
        factor: FactorProgram,
        data: pd.DataFrame,
        forward_returns: np.ndarray,
        **eval_kwargs: Any,
    ) -> RobustnessTestResult:
        """对因子执行全部鲁棒性测试。

        Args:
            factor: 因子程序
            data: OHLCV 数据
            forward_returns: 未来收益率
            **eval_kwargs: 传递给 evaluate_backtest 的额外参数

        Returns:
            RobustnessTestResult
        """
        np.random.seed(self._random_seed)

        # Baseline
        bt = evaluate_backtest(factor, data, forward_returns, **eval_kwargs)
        baseline_ic = bt.get("ic", 0.0)

        # 1. 对抗样本测试
        adversarial_results: list[AdversarialTestResult] = []
        for name, factor_val in self.ADVERSARIAL_PERTURBATIONS:
            perturbed_data = _perturb_prices(data, factor_val)
            bt_p = evaluate_backtest(factor, perturbed_data, forward_returns, **eval_kwargs)
            perturbed_ic = bt_p.get("ic", 0.0)
            ic_change = abs(perturbed_ic - baseline_ic)
            adversarial_results.append(AdversarialTestResult(
                perturbation=name,
                perturbation_factor=factor_val,
                baseline_ic=baseline_ic,
                perturbed_ic=perturbed_ic,
                ic_change=ic_change,
                passed=ic_change <= self._adversarial_threshold,
            ))

        # 2. 缺失值测试
        missing_value_results: list[MissingValueTestResult] = []
        for pct in self.MISSING_PCTS:
            missing_data = _inject_missing(data, pct, self._random_seed)
            bt_m = evaluate_backtest(factor, missing_data, forward_returns, **eval_kwargs)
            missing_ic = bt_m.get("ic", 0.0)
            ic_retention = abs(missing_ic / baseline_ic) if baseline_ic != 0 else 0.0
            missing_value_results.append(MissingValueTestResult(
                missing_pct=pct,
                baseline_ic=baseline_ic,
                missing_ic=missing_ic,
                ic_retention=ic_retention,
                passed=ic_retention >= self._missing_retention_threshold,
            ))

        # 3. 分布外测试
        ood_results: list[OODTestResult] = []
        for scenario in self.OOD_SCENARIOS:
            ood_data = _generate_ood_data(data, scenario, self._random_seed)
            bt_o = evaluate_backtest(factor, ood_data, forward_returns, **eval_kwargs)
            ood_ic = bt_o.get("ic", 0.0)
            ic_retention = abs(ood_ic / baseline_ic) if baseline_ic != 0 else 0.0
            ood_results.append(OODTestResult(
                scenario=scenario,
                baseline_ic=baseline_ic,
                ood_ic=ood_ic,
                ic_retention=ic_retention,
                passed=ic_retention >= self._ood_retention_threshold,
            ))

        # 汇总
        n_adversarial_pass = sum(1 for r in adversarial_results if r["passed"])
        n_missing_pass = sum(1 for r in missing_value_results if r["passed"])
        n_ood_pass = sum(1 for r in ood_results if r["passed"])
        total_tests = (
            len(adversarial_results)
            + len(missing_value_results)
            + len(ood_results)
        )
        total_passed = n_adversarial_pass + n_missing_pass + n_ood_pass

        summary = {
            "baseline_ic": baseline_ic,
            "adversarial": {
                "total": len(adversarial_results),
                "passed": n_adversarial_pass,
                "threshold": self._adversarial_threshold,
            },
            "missing_value": {
                "total": len(missing_value_results),
                "passed": n_missing_pass,
                "retention_threshold": self._missing_retention_threshold,
            },
            "ood": {
                "total": len(ood_results),
                "passed": n_ood_pass,
                "retention_threshold": self._ood_retention_threshold,
            },
            "overall_pass_rate": total_passed / total_tests if total_tests > 0 else 0.0,
        }

        return RobustnessTestResult(
            factor_id=factor.get("factor_id", "unknown"),
            factor_name=factor.get("name", "unknown"),
            adversarial_results=adversarial_results,
            missing_value_results=missing_value_results,
            ood_results=ood_results,
            summary=summary,
        )

    @staticmethod
    def report(result: RobustnessTestResult) -> str:
        """生成可读的鲁棒性测试报告。"""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append(f"鲁棒性测试报告 — {result['factor_name']} ({result['factor_id']})")
        lines.append("=" * 70)
        lines.append(f"\nBaseline IC: {result['summary']['baseline_ic']:.4f}\n")

        # 1. 对抗样本
        lines.append("--- 1. 对抗样本测试 ---")
        lines.append(f"  {'扰动方式':<20} {'扰动因子':>10} {'Baseline IC':>12} {'扰动后 IC':>12} {'IC 变化':>10} {'通过':>6}")
        for r in result["adversarial_results"]:
            lines.append(
                f"  {r['perturbation']:<20} {r['perturbation_factor']:>10.4f} "
                f"{r['baseline_ic']:>12.4f} {r['perturbed_ic']:>12.4f} "
                f"{r['ic_change']:>10.4f} {'✅' if r['passed'] else '❌':>6}"
            )

        # 2. 缺失值
        lines.append("\n--- 2. 缺失值测试 ---")
        lines.append(f"  {'缺失比例':<10} {'Baseline IC':>12} {'缺失后 IC':>12} {'IC 保持率':>10} {'通过':>6}")
        for r in result["missing_value_results"]:
            lines.append(
                f"  {r['missing_pct']*100:>6.0f}%    "
                f"{r['baseline_ic']:>12.4f} {r['missing_ic']:>12.4f} "
                f"{r['ic_retention']:>10.2%} {'✅' if r['passed'] else '❌':>6}"
            )

        # 3. 分布外
        lines.append("\n--- 3. 分布外测试 ---")
        lines.append(f"  {'场景':<12} {'Baseline IC':>12} {'OOD IC':>12} {'IC 保持率':>10} {'通过':>6}")
        scenario_names = {
            "high_vol": "高波动",
            "low_vol": "低波动",
            "trending": "强趋势",
            "noisy": "高噪声",
        }
        for r in result["ood_results"]:
            name = scenario_names.get(r["scenario"], r["scenario"])
            lines.append(
                f"  {name:<12} "
                f"{r['baseline_ic']:>12.4f} {r['ood_ic']:>12.4f} "
                f"{r['ic_retention']:>10.2%} {'✅' if r['passed'] else '❌':>6}"
            )

        # 汇总
        s = result["summary"]
        lines.append(f"\n--- 汇总 ---")
        lines.append(f"  对抗样本: {s['adversarial']['passed']}/{s['adversarial']['total']} 通过")
        lines.append(f"  缺失值:    {s['missing_value']['passed']}/{s['missing_value']['total']} 通过")
        lines.append(f"  分布外:    {s['ood']['passed']}/{s['ood']['total']} 通过")
        lines.append(f"  总体通过率: {s['overall_pass_rate']:.1%}")

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)


__all__ = [
    "AdversarialTestResult",
    "MissingValueTestResult",
    "OODTestResult",
    "RobustnessTestResult",
    "RobustnessTester",
]