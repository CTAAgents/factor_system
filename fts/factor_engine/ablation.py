"""
factor_engine/ablation.py — 输入敏感性消融实验

HARNESS §11-logic-review-plan.md §A.1:
    五种消融模式，检测因子是否依赖不该依赖的变量。

设计:
    AblationExperiment 类对输入特征做系统化扰动，
    复用 evaluate_backtest 计算消融前后的 IC/Sharpe 变化。

版本: v1.0.0
"""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import pandas as pd

from .contracts import BacktestMetrics, FactorProgram
from .evaluation_chain import evaluate_backtest

# ─── 消融模式 ─────────────────────────────────────────────

ABLATION_MODES: dict[str, str] = {
    "volume_zero": "成交量置零 — 检测量价因子是否真实依赖成交量",
    "vwap_to_close": "VWAP 替换为 close — 检测 VWAP 近似是否冗余",
    "vwap_to_settle": "VWAP 替换为 settle — 检测结算价等价性",
    "shuffle_dates": "时间戳打乱 — 检测是否依赖时序因果",
    "zero_one_feature": "单特征归零 — 逐个特征置零检测 IC 变化",
}


# ─── 消融结果契约 ─────────────────────────────────────────

class SingleAblation(dict):
    """单次消融实验结果。"""
    def __init__(
        self,
        mode: str,
        description: str,
        ic: float,
        sharpe: float,
        ic_change: float,
        sharpe_change: float,
    ) -> None:
        super().__init__()
        self["mode"] = mode
        self["description"] = description
        self["ic"] = ic
        self["sharpe"] = sharpe
        self["ic_change"] = ic_change
        self["sharpe_change"] = sharpe_change


class AblationResult(dict):
    """完整消融实验结果。"""
    def __init__(
        self,
        factor_id: str,
        factor_name: str,
        baseline_ic: float,
        baseline_sharpe: float,
        ablations: list[SingleAblation],
    ) -> None:
        super().__init__()
        self["factor_id"] = factor_id
        self["factor_name"] = factor_name
        self["baseline_ic"] = baseline_ic
        self["baseline_sharpe"] = baseline_sharpe
        self["ablations"] = ablations


# ─── 数据扰动函数 ─────────────────────────────────────────

def _ablate_volume_zero(data: pd.DataFrame) -> pd.DataFrame:
    """将成交量字段置零。"""
    modified = data.copy()
    if "volume" in modified.columns:
        modified["volume"] = 0.0
    return modified


def _ablate_vwap_to_close(data: pd.DataFrame) -> pd.DataFrame:
    """将 VWAP 替换为 close。"""
    modified = data.copy()
    if "vwap" in modified.columns:
        modified["vwap"] = modified["close"].values
    return modified


def _ablate_vwap_to_settle(data: pd.DataFrame) -> pd.DataFrame:
    """将 VWAP 替换为 settle（若无 settle 则用 close 替代）。"""
    modified = data.copy()
    if "vwap" in modified.columns:
        if "settle" in modified.columns:
            modified["vwap"] = modified["settle"].values
        else:
            modified["vwap"] = modified["close"].values
    return modified


def _ablate_shuffle_dates(data: pd.DataFrame) -> pd.DataFrame:
    """打乱时间戳（保持特征间同期关系，破坏时序因果）。"""
    modified = data.copy()
    # 对每个特征列独立打乱（保持特征间的同期关系，但破坏时间序列结构）
    indices = np.random.permutation(len(modified))
    feature_cols = [c for c in modified.columns if c != "date"]
    for col in feature_cols:
        modified[col] = modified[col].iloc[indices].values
    return modified


def _ablate_zero_one_feature(data: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """逐个特征置零，返回 {feature_name: modified_data}。

    跳过 'date' 和 'vwap' 列（vwap 有专门的消融模式）。
    """
    results: dict[str, pd.DataFrame] = {}
    skip_cols = {"date", "vwap"}
    for col in data.columns:
        if col in skip_cols:
            continue
        modified = data.copy()
        modified[col] = 0.0
        results[col] = modified
    return results


# ─── 消融实验执行器 ───────────────────────────────────────

def _run_single_ablation(
    factor: FactorProgram,
    modified_data: pd.DataFrame,
    forward_returns: np.ndarray,
    baseline_ic: float,
    baseline_sharpe: float,
    mode: str,
    description: str,
    **eval_kwargs: Any,
) -> SingleAblation:
    """对扰动后的数据运行回测，返回消融结果。"""
    try:
        bt = evaluate_backtest(factor, modified_data, forward_returns, **eval_kwargs)
        ic = bt.get("ic", 0.0)
        sharpe = bt.get("sharpe", 0.0)
    except Exception:
        ic = 0.0
        sharpe = 0.0

    return SingleAblation(
        mode=mode,
        description=description,
        ic=ic,
        sharpe=sharpe,
        ic_change=ic - baseline_ic,
        sharpe_change=sharpe - baseline_sharpe,
    )


# ─── 主类 ─────────────────────────────────────────────────

class AblationExperiment:
    """输入敏感性消融实验。

    Usage:
        experiment = AblationExperiment()
        result = experiment.run(factor, data, forward_returns)
        print(experiment.report([result]))
    """

    def __init__(self, random_seed: int = 42):
        self._random_seed = random_seed

    def run(
        self,
        factor: FactorProgram,
        data: pd.DataFrame,
        forward_returns: np.ndarray,
        **eval_kwargs: Any,
    ) -> AblationResult:
        """对单个因子执行全部 5 种消融实验。

        Args:
            factor: 因子程序
            data: OHLCV 数据
            forward_returns: 未来收益率
            **eval_kwargs: 传递给 evaluate_backtest 的额外参数

        Returns:
            AblationResult
        """
        np.random.seed(self._random_seed)

        # Baseline（空数据不报错）
        try:
            bt = evaluate_backtest(factor, data, forward_returns, **eval_kwargs)
            baseline_ic = bt.get("ic", 0.0)
            baseline_sharpe = bt.get("sharpe", 0.0)
        except Exception:
            baseline_ic = 0.0
            baseline_sharpe = 0.0

        ablations: list[SingleAblation] = []

        # 1. 成交量置零
        vol_zero_data = _ablate_volume_zero(data)
        ablations.append(_run_single_ablation(
            factor, vol_zero_data, forward_returns,
            baseline_ic, baseline_sharpe,
            "volume_zero", "成交量置零",
            **eval_kwargs,
        ))

        # 2. VWAP → close
        vwap_close_data = _ablate_vwap_to_close(data)
        ablations.append(_run_single_ablation(
            factor, vwap_close_data, forward_returns,
            baseline_ic, baseline_sharpe,
            "vwap_to_close", "VWAP 替换为 close",
            **eval_kwargs,
        ))

        # 3. VWAP → settle
        vwap_settle_data = _ablate_vwap_to_settle(data)
        ablations.append(_run_single_ablation(
            factor, vwap_settle_data, forward_returns,
            baseline_ic, baseline_sharpe,
            "vwap_to_settle", "VWAP 替换为 settle",
            **eval_kwargs,
        ))

        # 4. 时间戳打乱
        shuffled_data = _ablate_shuffle_dates(data)
        ablations.append(_run_single_ablation(
            factor, shuffled_data, forward_returns,
            baseline_ic, baseline_sharpe,
            "shuffle_dates", "时间戳打乱",
            **eval_kwargs,
        ))

        # 5. 单特征归零（逐个特征独立测试，取最大 IC 变化）
        zero_one_data = _ablate_zero_one_feature(data)
        worst_ic = baseline_ic
        worst_feature = ""
        for col, mod_data in zero_one_data.items():
            try:
                bt_col = evaluate_backtest(factor, mod_data, forward_returns, **eval_kwargs)
                ic_col = bt_col.get("ic", 0.0)
                if abs(ic_col - baseline_ic) > abs(worst_ic - baseline_ic):
                    worst_ic = ic_col
                    worst_feature = col
            except Exception:
                continue

        if worst_feature:
            ablations.append(SingleAblation(
                mode="zero_one_feature",
                description=f"单特征归零（影响最大: {worst_feature}）",
                ic=worst_ic,
                sharpe=0.0,
                ic_change=worst_ic - baseline_ic,
                sharpe_change=0.0,
            ))
        else:
            ablations.append(SingleAblation(
                mode="zero_one_feature",
                description="单特征归零（无显著影响）",
                ic=baseline_ic,
                sharpe=baseline_sharpe,
                ic_change=0.0,
                sharpe_change=0.0,
            ))

        return AblationResult(
            factor_id=factor.get("factor_id", "unknown"),
            factor_name=factor.get("name", "unknown"),
            baseline_ic=baseline_ic,
            baseline_sharpe=baseline_sharpe,
            ablations=ablations,
        )

    def run_batch(
        self,
        factors: list[FactorProgram],
        data: pd.DataFrame,
        forward_returns: np.ndarray,
        **eval_kwargs: Any,
    ) -> list[AblationResult]:
        """批量执行消融实验。"""
        return [
            self.run(f, data, forward_returns, **eval_kwargs)
            for f in factors
        ]

    @staticmethod
    def report(results: list[AblationResult]) -> str:
        """生成可读的消融实验报告。"""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append("消融实验报告")
        lines.append("=" * 70)

        for result in results:
            lines.append(f"\n因子: {result['factor_name']} ({result['factor_id']})")
            lines.append(f"  Baseline IC={result['baseline_ic']:.4f}, Sharpe={result['baseline_sharpe']:.4f}")
            lines.append(f"  {'消融模式':<25} {'IC':>8} {'IC变化':>10} {'Sharpe':>8} {'Sharpe变化':>10}")
            lines.append(f"  {'-'*25} {'-'*8} {'-'*10} {'-'*8} {'-'*10}")
            for ab in result["ablations"]:
                lines.append(
                    f"  {ab['description']:<25} {ab['ic']:>8.4f} "
                    f"{ab['ic_change']:>+10.4f} {ab['sharpe']:>8.4f} "
                    f"{ab['sharpe_change']:>+10.4f}"
                )

        lines.append("\n" + "=" * 70)
        return "\n".join(lines)


__all__ = [
    "ABLATION_MODES",
    "SingleAblation",
    "AblationResult",
    "AblationExperiment",
]