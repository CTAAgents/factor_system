"""
fts.factor_engine.capital_allocator — 资金分配器（B.2 资金管理模块）。

支持四种资金管理模式：
    - fixed: 固定比例
    - vol_target: 波动率目标（按实现波动率缩放仓位）
    - risk_parity: 风险平价（各资产风险贡献相等，迭代求解）
    - kelly: 凯利公式（按胜率与盈亏比计算最优仓位）

用法:
    from fts.factor_engine.capital_allocator import CapitalAllocator

    alloc = CapitalAllocator()
    result = alloc.allocate(portfolio_returns=returns, total_capital=1_000_000, mode="vol_target")

版本: v1.0.0
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from .retired_l3 import warn_if_retired

logger = logging.getLogger(__name__)

# 退役登记（plans/57 §4.1：资金分配平移 RD 后 FTS 侧标记弃用）
warn_if_retired("capital_allocator")


def compute_position_target(
    signal_strength: float,
    confidence: float,
    realized_vol: float,
    risk_budget: float,
    max_position: float = 1.0,
    min_position: float = 0.0,
) -> float:
    """头寸公式 f(信号强度, 置信度, 波动率, 风险预算)（plans/54 P2-3）。

    目标仓位 = clip(|信号强度| × 置信度 × (风险预算 / 年化波动率), [min_position, max_position])：
    - 信号越强仓位越大（信号强度线性）
    - 置信度越高仓位越大（识别可信度调制）
    - 波动率越高仓位越小（风险预算约束，vol targeting 语义）
    - 风险预算（目标波动率）越高仓位越大

    Args:
        signal_strength: 信号强度（如综合得分/IC，正负均可取绝对值）。
        confidence: 识别置信度 ∈ [0,1]。
        realized_vol: 年化波动率（>0；≤0 或 NaN → 保守按 risk_budget 满仓的风险倍数）。
        risk_budget: 风险预算（目标年化波动率，如 0.15）。
        max_position: 仓位上限（默认 1.0）。
        min_position: 仓位下限（默认 0.0）。

    Returns:
        目标仓位 ∈ [min_position, max_position]。
    """
    import math

    strength = abs(float(signal_strength))
    conf = max(0.0, min(1.0, float(confidence)))
    vol = float(realized_vol)
    budget = float(risk_budget) if float(risk_budget) > 0 else 0.15
    if not math.isfinite(vol) or vol <= 1e-9:
        vol_scale = 1.0  # 波动率不可用 → 不放大（保守）
    else:
        vol_scale = budget / vol
    target = strength * conf * vol_scale
    return float(np.clip(target, min_position, max_position))


def shrink_factor_diversity(
    weights: dict[str, float],
    confidence: float,
    low_conf_threshold: float = 0.4,
    keep_ratio: float = 0.5,
) -> dict[str, float]:
    """对称化仓位（plans/54 P2-1，文档 §7.1）：低置信度不仅减仓还缩小因子种类。

    低置信度（confidence < low_conf_threshold）时保留权重 top keep_ratio 的因子
    （其余归零后重归一化）——"缩小策略种类"；高置信度不干预（原样返回）。

    Args:
        weights: 因子 → 权重。
        confidence: 识别置信度 ∈ [0,1]。
        low_conf_threshold: 低置信度阈值（默认 0.4）。
        keep_ratio: 保留因子比例（默认 0.5 = 砍半）。

    Returns:
        调整后权重 dict（新 dict，不修改入参）。
    """
    if confidence >= low_conf_threshold or not weights:
        return dict(weights)
    n_keep = max(1, int(round(len(weights) * keep_ratio)))
    top = sorted(weights.items(), key=lambda kv: -abs(kv[1]))[:n_keep]
    kept = dict(top)
    total = sum(abs(v) for v in kept.values())
    if total <= 1e-12:
        return dict(weights)
    return {k: v / total * sum(abs(vv) for vv in weights.values()) for k, v in kept.items()}


@dataclass
class AllocationResult:
    """资金分配结果。"""

    mode: str
    leverage: float  # 总杠杆
    weights: dict[str, float]  # 资产 → 权重（单资产时 {"portfolio": w}）
    allocated_capital: dict[str, float]  # 资产 → 分配资金
    details: dict[str, Any] = field(default_factory=dict)


class CapitalAllocator:
    """资金分配器（B.2 资金管理模块）。"""

    def __init__(self, max_leverage: float = 2.0, min_weight: float = 0.1) -> None:
        """初始化资金分配器。

        Args:
            max_leverage: 最大杠杆倍数
            min_weight: 最小仓位比例
        """
        self._max_leverage = float(max_leverage)
        self._min_weight = float(min_weight)

    # ─── 主入口 ──────────────────────────────────────────

    def allocate(
        self,
        portfolio_returns: pd.Series | pd.DataFrame,
        total_capital: float = 1_000_000.0,
        mode: str = "vol_target",
        target_volatility: float = 0.15,
        max_drawdown: float = 0.20,
        win_rate: Optional[float] = None,
        payoff_ratio: Optional[float] = None,
        margin_rates: Optional[dict[str, float]] = None,
        max_margin_usage: float = 0.80,
    ) -> AllocationResult:
        """分配资金。

        Args:
            portfolio_returns: 组合收益率（Series 单资产；DataFrame 多资产）
            total_capital: 总资金
            mode: "fixed" | "vol_target" | "risk_parity" | "kelly"
            target_volatility: 目标年化波动率（vol_target 模式）
            max_drawdown: 最大回撤约束（vol_target 模式）
            win_rate: 胜率（kelly 模式）
            payoff_ratio: 盈亏比（kelly 模式）
            margin_rates: 品种保证金率表（GAP-F09，v2.60.0，缺省读取配置 margin_rate_map）
            max_margin_usage: 最大保证金占用率（超过触发强平风险告警并按上限缩放）

        Returns:
            AllocationResult。
        """
        if mode == "fixed":
            result = self._fixed_allocation(total_capital)
        elif mode == "vol_target":
            result = self._vol_target_allocation(portfolio_returns, total_capital, target_volatility, max_drawdown)
        elif mode == "risk_parity":
            result = self._risk_parity_allocation(portfolio_returns, total_capital)
        elif mode == "min_variance":
            result = self._min_variance_allocation(portfolio_returns, total_capital)
        elif mode == "kelly":
            result = self._kelly_criterion_allocation(total_capital, win_rate, payoff_ratio)
        else:
            logger.warning("[CapitalAllocator] 未知模式 %s，回退 vol_target", mode)
            result = self._vol_target_allocation(portfolio_returns, total_capital, target_volatility, max_drawdown)

        # v2.60.0 (GAP-F09): 保证金占用约束（强平风险告警 + 上限截断）
        result = self._apply_margin_constraint(result, total_capital, margin_rates, max_margin_usage)
        return result

    # ─── 保证金占用约束（GAP-F09，v2.60.0）────────────────

    @staticmethod
    def _apply_margin_constraint(
        result: AllocationResult,
        total_capital: float,
        margin_rates: Optional[dict[str, float]] = None,
        max_margin_usage: float = 0.80,
    ) -> AllocationResult:
        """保证金占用约束：保证金占用 / 总权益 ≤ max_margin_usage。

        保证金占用 = Σ(weight_i × total_capital × margin_i)，
        未配置保证金率的品种用默认 0.10。超限时等比缩放权重至上限，
        并触发强平风险告警（AGENTS.md 4.3：禁止无止损/超限额重仓）。

        Args:
            result: 分配结果（原地更新 weights/allocated_capital/details）
            total_capital: 总资金
            margin_rates: {symbol: 保证金率}
            max_margin_usage: 最大保证金占用率

        Returns:
            应用约束后的 AllocationResult
        """
        if margin_rates is None:
            try:
                from fts.config.settings import get_config

                margin_rates = get_config().margin_rate_map or {}
            except Exception:  # noqa: BLE001
                margin_rates = {}
        if not margin_rates:
            return result

        weights = dict(result.weights)
        margin_usage = 0.0
        for sym, w in weights.items():
            margin = float(margin_rates.get(sym, 0.10))
            margin_usage += w * margin

        details = dict(result.details)
        details["margin_usage"] = round(margin_usage, 6)
        details["margin_scaled"] = False

        if margin_usage > max_margin_usage > 0:
            scale = max_margin_usage / margin_usage
            weights = {sym: w * scale for sym, w in weights.items()}
            margin_usage *= scale
            details["margin_usage"] = round(margin_usage, 6)  # 缩放后占用回落至上限
            details["margin_scaled"] = True
            details["margin_scale_factor"] = round(scale, 6)
            logger.warning(
                "[CapitalAllocator] 保证金占用 %.2f%% 超上限 %.0f%%，权重等比缩放至 %.4f（强平风险告警）",
                margin_usage * 100,
                max_margin_usage * 100,
                scale,
            )

        result.weights = weights
        result.allocated_capital = {sym: total_capital * w for sym, w in weights.items()}
        result.details = details
        return result

    # ─── 各模式实现 ──────────────────────────────────────

    def _fixed_allocation(self, total_capital: float) -> AllocationResult:
        """固定比例：满仓。"""
        return AllocationResult(
            mode="fixed",
            leverage=1.0,
            weights={"portfolio": 1.0},
            allocated_capital={"portfolio": total_capital},
        )

    def _vol_target_allocation(
        self,
        portfolio_returns: pd.Series | pd.DataFrame,
        total_capital: float,
        target_volatility: float,
        max_drawdown: float,
    ) -> AllocationResult:
        """波动率目标：scale = target_vol / realized_vol，限制在 [min, max]。"""
        returns = _to_frame(portfolio_returns)
        if returns.empty:
            return AllocationResult(
                mode="vol_target",
                leverage=1.0,
                weights={"portfolio": 1.0},
                allocated_capital={"portfolio": total_capital},
                details={"reason": "no data"},
            )

        weights: dict[str, float] = {}
        capital: dict[str, float] = {}
        for col in returns.columns:
            r = returns[col].dropna()
            realized_vol = float(r.std() * np.sqrt(252)) if len(r) > 1 else 0.0
            if realized_vol < 1e-8:
                w = 1.0
            else:
                w = target_volatility / realized_vol
            w = float(np.clip(w, self._min_weight, self._max_leverage))
            weights[col] = w
            capital[col] = total_capital * w

        leverage = sum(weights.values())
        logger.info(
            "[CapitalAllocator] vol_target 完成 [target=%.2f, leverage=%.2f]",
            target_volatility,
            leverage,
        )
        return AllocationResult(
            mode="vol_target",
            leverage=leverage,
            weights=weights,
            allocated_capital=capital,
            details={"target_volatility": target_volatility},
        )

    def _risk_parity_allocation(
        self,
        portfolio_returns: pd.Series | pd.DataFrame,
        total_capital: float,
        max_iter: int = 100,
    ) -> AllocationResult:
        """风险平价：迭代使各资产风险贡献相等（协方差分解）。"""
        returns = _to_frame(portfolio_returns)
        if returns.empty or len(returns.columns) == 0:
            return self._fixed_allocation(total_capital)
        if len(returns.columns) == 1:
            return AllocationResult(
                mode="risk_parity",
                leverage=1.0,
                weights={"portfolio": 1.0},
                allocated_capital={"portfolio": total_capital},
            )

        cov = returns.dropna().cov().values
        n = len(returns.columns)
        if np.any(np.isnan(cov)) or np.linalg.det(cov) < 1e-12:
            # 退化协方差矩阵 → 等权
            w = np.ones(n) / n
        else:
            w = np.ones(n) / n
            for _ in range(max_iter):
                sigma_p = np.sqrt(w @ cov @ w)
                if sigma_p < 1e-12:
                    break
                marg = (cov @ w) / sigma_p  # 边际风险贡献
                rc = w * marg
                rc_sum: float = np.sum(rc)
                if rc_sum < 1e-12:
                    break
                target = rc_sum / n
                w = w * (target / (rc + 1e-12))
                w = w / np.sum(w)

        cols = list(returns.columns)
        weights = {c: float(wi) for c, wi in zip(cols, w)}
        capital = {c: total_capital * wi for c, wi in weights.items()}
        return AllocationResult(
            mode="risk_parity",
            leverage=1.0,
            weights=weights,
            allocated_capital=capital,
        )

    def _min_variance_allocation(
        self,
        portfolio_returns: pd.Series | pd.DataFrame,
        total_capital: float,
    ) -> AllocationResult:
        """方差最小化：w = Σ⁻¹1 / (1'Σ⁻¹1)（G15，35-gap-closure-plan §5.8）。

        Ledoit-Wolf 收缩（对角结构化目标，收缩强度 min(1, N/T)）防奇异；
        负权重截断到 min_weight 后重归一化（资金分配为非负语义）。
        """
        returns = _to_frame(portfolio_returns)
        if returns.empty or len(returns.columns) == 0:
            return self._fixed_allocation(total_capital)
        if len(returns.columns) == 1:
            return AllocationResult(
                mode="min_variance",
                leverage=1.0,
                weights={"portfolio": 1.0},
                allocated_capital={"portfolio": total_capital},
            )

        df = returns.dropna()
        cov = df.cov().values
        n = len(returns.columns)
        # Ledoit-Wolf 收缩（对角结构化目标）——与 weight_learning.ic_covariance_weights 同口径
        target = np.diag(np.diag(cov))
        s = min(1.0, n / max(int(len(df)), 1))
        cov_shrunk = (1.0 - s) * cov + s * target
        ones = np.ones(n)
        try:
            inv_cov_ones = np.linalg.solve(cov_shrunk + 1e-6 * np.eye(n), ones)
        except np.linalg.LinAlgError:
            inv_cov_ones = ones  # 奇异 → 等权兜底
        denom = float(np.sum(inv_cov_ones))
        if not np.isfinite(denom) or denom <= 0:
            w = np.ones(n) / n
        else:
            w = inv_cov_ones / denom
            # 负权重截断（资金分配非负语义），重归一化
            w = np.clip(w, float(self._min_weight), None)
            w_sum = float(np.sum(w))
            if w_sum <= 0 or not np.isfinite(w_sum):
                w = np.ones(n) / n
            else:
                w = w / w_sum

        cols = list(returns.columns)
        weights = {c: float(wi) for c, wi in zip(cols, w)}
        capital = {c: total_capital * wi for c, wi in weights.items()}
        return AllocationResult(
            mode="min_variance",
            leverage=1.0,
            weights=weights,
            allocated_capital=capital,
            details={"shrinkage": round(float(s), 4)},
        )

    def _kelly_criterion_allocation(
        self,
        total_capital: float,
        win_rate: Optional[float],
        payoff_ratio: Optional[float],
    ) -> AllocationResult:
        """凯利公式：f* = (bp - q) / b，b = 盈亏比，p = 胜率，q = 1 - p。"""
        if win_rate is None or payoff_ratio is None:
            logger.warning("[CapitalAllocator] kelly 模式缺少胜率/盈亏比，回退 fixed")
            return self._fixed_allocation(total_capital)

        p = float(np.clip(win_rate, 0.0, 1.0))
        b = max(float(payoff_ratio), 1e-6)
        q = 1.0 - p
        f = max(0.0, (b * p - q) / b)
        f = min(f, self._max_leverage)
        f = max(f, self._min_weight)
        return AllocationResult(
            mode="kelly",
            leverage=f,
            weights={"portfolio": f},
            allocated_capital={"portfolio": total_capital * f},
            details={"win_rate": p, "payoff_ratio": b, "fraction": f},
        )


def _to_frame(returns: pd.Series | pd.DataFrame) -> pd.DataFrame:
    """统一为 DataFrame（单序列 → 单列）。"""
    if isinstance(returns, pd.DataFrame):
        return returns
    return returns.to_frame(name="portfolio")


__all__ = ["CapitalAllocator", "AllocationResult"]
