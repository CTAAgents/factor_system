"""
fts.factor_engine.regime_multipliers — Regime 风控参数（G14）。

提供按市场制度（regime）解析的风控参数表与解析函数：
    - ``REGIME_RISK_PARAMS``: 各 Regime 的风控参数（杠杆上限/止损比例/单日亏损）
    - ``resolve_risk_params``: 按 Regime 覆盖基础参数，可选指数平滑防切换跳变

版本: v1.0.0
"""

from __future__ import annotations

from typing import Optional, TypedDict, cast


# ─── 契约 ───────────────────────────────────────────────────


class RiskParams(TypedDict, total=False):
    """Regime 风控参数包（G14）。

    字段与 `risk/risk_manager.py` RiskConfig / `paper_trader_mhf.py`
    MhfRiskConfig 的对应项对齐，供 `resolve_risk_params` 按 Regime 覆盖。
    """

    leverage_cap: float  # 杠杆上限（对应 RiskConfig.max_leverage）
    stop_loss_pct: float  # 单品种止损比例（对应 MhfRiskConfig.stop_loss_pct）
    daily_loss_pct: float  # 单日最大亏损（对应 RiskConfig.daily_loss_limit_pct）


# ─── G14: Regime 风控参数表（第二张表，v2.103.0+15） ─────────────
# 风险制度（bear/high_vol）降杠杆、收紧止损；低波/趋势制度放大杠杆。
# 数值为保守初始值，可按回测/实盘观察校准后落盘覆盖。
REGIME_RISK_PARAMS: dict[str, RiskParams] = {
    "bull": {"leverage_cap": 2.5, "stop_loss_pct": 0.015, "daily_loss_pct": 0.020},
    "bear": {"leverage_cap": 1.5, "stop_loss_pct": 0.010, "daily_loss_pct": 0.015},
    "oscillate": {"leverage_cap": 2.0, "stop_loss_pct": 0.012, "daily_loss_pct": 0.018},
    "high_vol": {"leverage_cap": 1.0, "stop_loss_pct": 0.008, "daily_loss_pct": 0.010},
    "low_vol": {"leverage_cap": 2.0, "stop_loss_pct": 0.015, "daily_loss_pct": 0.020},
}


# ─── plans/55 §D: L0 宏观 Beta 层风控档位（第三张表） ─────────
# 在量价制度参数（REGIME_RISK_PARAMS）基础上叠加收紧（乘性取更严）：
#   RISK_OFF（负 Beta / 风险规避）→ 杠杆 ×0.7、单日亏损 ×0.7；
#   RISK_ON / RANGE_BOUND → 无额外约束（沿用量价制度参数）。
# 值为保守倍率，按灰度观察校准后落盘覆盖。
BETA_RISK_PARAMS: dict[str, dict[str, float]] = {
    "RISK_ON": {},
    "RISK_OFF": {"leverage_cap": 0.7, "daily_loss_pct": 0.7},
    "RANGE_BOUND": {},
    "unknown": {},
}


def resolve_risk_params(
    regime: Optional[str],
    base: RiskParams,
    prev: Optional[RiskParams] = None,
    alpha: float = 0.3,
    beta_state: Optional[str] = None,
) -> RiskParams:
    """按 Regime（量价制度 + L0 宏观 Beta 档位）解析风控参数（G14 + plans/55 §D）。

    Args:
        regime: 市场制度（"bull"/"bear"/"oscillate"/"high_vol"/"low_vol"）。
            None / 未知 → 回退 base 原样返回。
        base: 基础风控参数（调用方当前常量配置）。
        prev: 上一期生效参数（可选）。提供时对字段做指数平滑
            `α×new + (1-α)×prev` 防 Regime 切换跳变（对齐 RegimeSmoother
            过渡期平滑思想：风险制度快降、其余慢调）。
        alpha: 平滑系数（0-1，默认 0.3）。
        beta_state: L0 宏观 Beta 档位（"RISK_ON"/"RISK_OFF"/"RANGE_BOUND"/"unknown"，
            可选）。命中 `BETA_RISK_PARAMS` 时按倍率在量价制度参数基础上叠加收紧
            （乘性取更严，如 RISK_OFF → 杠杆再 ×0.7）。

    Returns:
        解析后的风控参数包（不修改入参）。
    """
    new_params: RiskParams = dict(base)
    if regime and regime in REGIME_RISK_PARAMS:
        for k, v in REGIME_RISK_PARAMS[regime].items():
            new_params[k] = v
    if beta_state:
        muls = BETA_RISK_PARAMS.get(beta_state, {})
        for k, m in muls.items():
            if k in new_params and m > 0:
                new_params[k] = cast(float, new_params[k] * m)
    if prev and alpha > 0:
        for k, v in new_params.items():
            pv = prev.get(k)
            if pv is not None:
                new_params[k] = cast(float, alpha * v + (1.0 - alpha) * pv)
    return new_params


__all__ = [
    "RiskParams",
    "REGIME_RISK_PARAMS",
    "BETA_RISK_PARAMS",
    "resolve_risk_params",
]
