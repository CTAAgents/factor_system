"""
fts.risk.risk_manager — 实时风控管理器（C.2 §4）。

五项风控规则:
    1. 单品种仓位上限（默认 10%）
    2. 组合最大回撤（默认 20%）
    3. 单日最大亏损（默认 5%）
    4. 杠杆上限（默认 3x）
    5. 集中度上限（默认前 3 大品种 ≤ 50%）

任何规则不通过即拦截信号（approved=False），拦截后信号不达交易层。

用法:
    from fts.risk import RiskManager

    rm = RiskManager()
    result = rm.check(signal, account, positions)
    if result["approved"]:
        # 允许下单
    else:
        # 拦截并告警

版本: v1.0.0
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TypedDict, cast

logger = logging.getLogger(__name__)


class RiskConfig(TypedDict, total=False):
    """风控配置。"""

    single_position_limit_pct: float  # 单品种仓位上限（默认 0.10）
    max_portfolio_drawdown_pct: float  # 最大组合回撤（默认 0.20）
    daily_loss_limit_pct: float  # 单日最大亏损（默认 0.05）
    max_leverage: float  # 最大杠杆（默认 3.0）
    max_concentration_pct: float  # 最大集中度（默认 0.50）
    max_open_positions: int  # 最大持仓品种数（默认 20）


class RiskCheckItem(TypedDict, total=False):
    """单项风控检查结果。"""

    check_name: str
    passed: bool
    current_value: float
    limit_value: float
    message: str
    severity: str  # 'warning' | 'critical'


class RiskCheckResult(TypedDict, total=False):
    """完整风控检查结果。"""

    approved: bool
    checks: list[RiskCheckItem]
    blocking_violations: list[RiskCheckItem]
    timestamp: str
    signal_id: str


_DEFAULT_CONFIG: RiskConfig = {
    "single_position_limit_pct": 0.10,
    "max_portfolio_drawdown_pct": 0.20,
    "daily_loss_limit_pct": 0.05,
    "max_leverage": 3.0,
    "max_concentration_pct": 0.50,
    "max_open_positions": 20,
}


class RiskManager:
    """实时风控管理器（C.2 §4）。"""

    def __init__(self, config: RiskConfig | None = None, regime: str | None = None) -> None:
        """初始化风控管理器。

        Args:
            config: 风控配置（缺省使用默认值）
            regime: 当前市场制度（G14，可选）。提供且命中 `REGIME_RISK_PARAMS`
                时，将表内 leverage_cap / daily_loss_pct 注入对应配置项
                （max_leverage / daily_loss_limit_pct），按制度收紧/放大风控边界。
                不改变 `check()` 内部逻辑。
        """
        merged: dict[str, Any] = dict(_DEFAULT_CONFIG)
        if config:
            merged.update({k: v for k, v in config.items() if v is not None})
        if regime:
            try:
                from fts.factor_engine.regime_multipliers import REGIME_RISK_PARAMS

                params = REGIME_RISK_PARAMS.get(regime)
                if params:
                    if params.get("leverage_cap") is not None:
                        merged["max_leverage"] = float(params["leverage_cap"])
                    if params.get("daily_loss_pct") is not None:
                        merged["daily_loss_limit_pct"] = float(params["daily_loss_pct"])
                    logger.info(
                        "[RiskManager] Regime=%s 风控参数注入: max_leverage=%.2f, daily_loss_limit=%.2f",
                        regime,
                        merged["max_leverage"],
                        merged["daily_loss_limit_pct"],
                    )
            except Exception as e:  # noqa: BLE001 — 注入失败回退常量，不阻断初始化
                logger.warning("[RiskManager] Regime 风控参数注入失败，回退常量: %s", e)
        self._config: RiskConfig = cast(RiskConfig, merged)

    # ─── 主入口 ──────────────────────────────────────────

    def check(
        self,
        signal: dict[str, Any],
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
    ) -> RiskCheckResult:
        """执行风控检查。

        Args:
            signal: FactorSignal 契约字典
            account: AccountStatus（balance/available/margin_used/position_value/total_equity）
            positions: symbol → PositionInfo

        Returns:
            RiskCheckResult（approved=False 时 blocking_violations 含未通过项）。
        """
        checks: list[RiskCheckItem] = []
        checks.append(self._check_single_position_limit(signal, account, positions))
        checks.append(self._check_portfolio_drawdown(signal, account))
        checks.append(self._check_daily_loss_limit(signal, account))
        checks.append(self._check_leverage_limit(signal, account, positions))
        checks.append(self._check_concentration_limit(signal, positions))

        blocking = [c for c in checks if not c.get("passed", False)]
        approved = len(blocking) == 0

        logger.info(
            "[RiskManager] 检查完成 [signal_id=%s, approved=%s, violations=%d]",
            signal.get("signal_id", "?"),
            approved,
            len(blocking),
        )
        return RiskCheckResult(
            approved=approved,
            checks=checks,
            blocking_violations=blocking,
            timestamp=datetime.now(timezone.utc).isoformat(),
            signal_id=signal.get("signal_id", ""),
        )

    # ─── 单项规则 ────────────────────────────────────────

    def _check_single_position_limit(
        self,
        signal: dict[str, Any],
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
    ) -> RiskCheckItem:
        """单品种仓位上限：现有 + 目标 ≤ 总权益 × limit。"""
        limit = float(self._config.get("single_position_limit_pct", 0.10))
        equity = float(account.get("total_equity", account.get("balance", 0.0)) or 0.0)

        worst: tuple[str, float] = ("", 0.0)
        for sig in signal.get("signals", []):
            symbol = sig.get("symbol", "")
            cur = float(positions.get(symbol, {}).get("market_value", 0.0) or 0.0)
            target_value = float(sig.get("position", 0.0) or 0.0) * float(sig.get("price", 0.0) or 0.0)
            ratio = (cur + target_value) / max(equity, 1e-9)
            if ratio > worst[1]:
                worst = (symbol, ratio)

        passed = worst[1] <= limit
        return RiskCheckItem(
            check_name="single_position_limit",
            passed=passed,
            current_value=worst[1],
            limit_value=limit,
            message=(f"单品种仓位 {worst[0] or '?'}: {worst[1]:.2%} (limit {limit:.0%})"),
            severity="critical" if not passed else "warning",
        )

    def _check_portfolio_drawdown(
        self,
        signal: dict[str, Any],
        account: dict[str, Any],
    ) -> RiskCheckItem:
        """组合最大回撤：权益 / 峰值权益 - 1 ≤ limit（负值）。"""
        limit = float(self._config.get("max_portfolio_drawdown_pct", 0.20))
        equity = float(account.get("total_equity", account.get("balance", 0.0)) or 0.0)
        peak = float(account.get("peak_equity", 0.0) or 0.0)

        if peak <= 0:
            return RiskCheckItem(
                check_name="portfolio_drawdown",
                passed=True,
                current_value=0.0,
                limit_value=limit,
                message="无峰值权益数据，跳过",
                severity="warning",
            )
        drawdown = equity / peak - 1.0
        passed = drawdown >= -limit
        return RiskCheckItem(
            check_name="portfolio_drawdown",
            passed=passed,
            current_value=drawdown,
            limit_value=-limit,
            message=f"组合回撤 {drawdown:.2%} (limit -{limit:.0%})",
            severity="critical" if not passed else "warning",
        )

    def _check_daily_loss_limit(
        self,
        signal: dict[str, Any],
        account: dict[str, Any],
    ) -> RiskCheckItem:
        """单日最大亏损：今日已实现+未实现亏损 ≤ 总权益 × limit。"""
        limit = float(self._config.get("daily_loss_limit_pct", 0.05))
        equity = float(account.get("total_equity", account.get("balance", 0.0)) or 0.0)
        day_pnl = float(account.get("daily_pnl", 0.0) or 0.0)

        loss_ratio = -day_pnl / max(equity, 1e-9)
        passed = loss_ratio <= limit
        return RiskCheckItem(
            check_name="daily_loss_limit",
            passed=passed,
            current_value=loss_ratio,
            limit_value=limit,
            message=f"单日亏损 {loss_ratio:.2%} (limit {limit:.0%})",
            severity="critical" if not passed else "warning",
        )

    def _check_leverage_limit(
        self,
        signal: dict[str, Any],
        account: dict[str, Any],
        positions: dict[str, dict[str, Any]],
    ) -> RiskCheckItem:
        """杠杆上限：持仓市值 / 总权益 ≤ limit。"""
        limit = float(self._config.get("max_leverage", 3.0))
        equity = float(account.get("total_equity", account.get("balance", 0.0)) or 0.0)
        position_value = float(account.get("position_value", 0.0) or 0.0)
        for pos in positions.values():
            position_value += float(pos.get("market_value", 0.0) or 0.0)
        for sig in signal.get("signals", []):
            position_value += float(sig.get("position", 0.0) or 0.0) * float(sig.get("price", 0.0) or 0.0)

        leverage = position_value / max(equity, 1e-9)
        passed = leverage <= limit
        return RiskCheckItem(
            check_name="leverage_limit",
            passed=passed,
            current_value=leverage,
            limit_value=limit,
            message=f"杠杆 {leverage:.2f}x (limit {limit:.1f}x)",
            severity="critical" if not passed else "warning",
        )

    def _check_concentration_limit(
        self,
        signal: dict[str, Any],
        positions: dict[str, dict[str, Any]],
    ) -> RiskCheckItem:
        """集中度：前 3 大品种市值 / 持仓总市值 ≤ limit。

        持仓品种数 < 3 时不适用集中度限制（品种不足无法分散）。
        """
        limit = float(self._config.get("max_concentration_pct", 0.50))

        values: list[float] = []
        for pos in positions.values():
            values.append(float(pos.get("market_value", 0.0) or 0.0))
        for sig in signal.get("signals", []):
            values.append(float(sig.get("position", 0.0) or 0.0) * float(sig.get("price", 0.0) or 0.0))

        values = [v for v in values if v > 1e-9]
        if len(values) < 3:
            return RiskCheckItem(
                check_name="concentration_limit",
                passed=True,
                current_value=0.0,
                limit_value=limit,
                message=f"持仓品种数 {len(values)} < 3，跳过集中度检查",
                severity="warning",
            )
        total = sum(values)
        top3 = sum(sorted(values, reverse=True)[:3])
        ratio = top3 / total
        passed = ratio <= limit
        return RiskCheckItem(
            check_name="concentration_limit",
            passed=passed,
            current_value=ratio,
            limit_value=limit,
            message=f"前3大品种集中度 {ratio:.2%} (limit {limit:.0%})",
            severity="critical" if not passed else "warning",
        )


__all__ = ["RiskManager", "RiskConfig", "RiskCheckItem", "RiskCheckResult"]
