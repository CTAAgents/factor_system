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

logger = logging.getLogger(__name__)


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
