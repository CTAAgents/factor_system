"""
fts.risk.portfolio_metrics — 组合级风控指标（D.2 §3 补齐）。

在既有单笔/单标的/总仓位规则（``RiskManager``）之上，补充**组合级**指标，
覆盖"杠杆仓位、波动尾部、相关性、损益、流动性、执行质量"六维度，
按三级预警判定（WARN / BLOCK / FORCE_CLOSE）：

    - ``compute_portfolio_metrics``: 纯计算，输入账户/持仓/权益曲线，输出指标字典
    - ``evaluate_metrics``:         对指标做三级预警判定，输出检查项与动作建议

设计约束（FTS 风控红线 4.3）:
    - 任何异常（缺数据/除零/空持仓）降级为"通过 + warning"，不阻断主流程
    - 相关性维度用持仓集中度代理（模拟仓无 per-symbol 历史收益矩阵）
    - 执行质量维度在 tick 撮合（D.2 §4）启用后由调用方传入

FTS 角色边界: 只做模拟仓组合核算风控，真实实盘风控权限归属下游（FDT）。
"""

from __future__ import annotations

from typing import Any, TypedDict

import numpy as np

__all__ = [
    "PortfolioMetricsConfig",
    "compute_portfolio_metrics",
    "evaluate_metrics",
]

# 三级预警严重级别
WARN = "WARN"
BLOCK = "BLOCK"
FORCE_CLOSE = "FORCE_CLOSE"


class PortfolioMetricsConfig(TypedDict, total=False):
    """组合级风控阈值（默认值对应 D.2 §3.2 清单）。"""

    # 杠杆/仓位
    max_leverage: float                # 杠杆上限（默认 3.0，对齐 RiskManager）
    max_total_position_ratio: float    # 总仓位占比上限（默认 0.95）
    max_single_position_ratio: float   # 单标的仓位上限（默认 0.20）
    max_margin_usage: float            # 保证金占用率上限（默认 0.95）
    warn_total_position_ratio: float   # WARN 阈值（默认 0.80）
    warn_single_position_ratio: float  # 单标 WARN 阈值（默认 0.15）
    # 波动/尾部
    max_annual_vol: float              # 组合年化波动率上限（默认 0.40）
    warn_annual_vol: float             # WARN 阈值（默认 0.25）
    max_vol_spike_ratio: float         # 波动率突变倍数上限（默认 3.5）
    warn_vol_spike_ratio: float        # WARN 阈值（默认 2.5）
    var_confidence: float              # VaR 置信度（默认 0.95）
    # 相关性（集中度代理）
    min_effective_positions: int       # 有效持仓数下限（默认 3）
    warn_effective_positions: int      # WARN 阈值（默认 5）
    # 损益
    max_drawdown: float                # 最大回撤上限（默认 0.20）
    warn_drawdown: float               # WARN 阈值（默认 0.10）
    max_daily_loss: float              # 单日最大亏损上限（默认 0.05）
    max_consecutive_losses: int        # 连续亏损上限（默认 8）
    warn_consecutive_losses: int       # WARN 阈值（默认 5）
    # 流动性
    max_liquidity_share: float         # 单持仓占成交额上限（默认 0.10）
    warn_liquidity_share: float        # WARN 阈值（默认 0.05）
    # 执行质量（tick 撮合后由调用方传入；默认不启用）
    max_slippage_dev: float            # 滑点偏离倍数上限（默认 5.0）
    max_partial_fill_ratio: float      # 部分成交占比上限（默认 0.25）
    min_fill_rate: float               # 成交率下限（默认 0.70）


_DEFAULT_CONFIG: PortfolioMetricsConfig = {
    "max_leverage": 3.0,
    "max_total_position_ratio": 0.95,
    "max_single_position_ratio": 0.20,
    "max_margin_usage": 0.95,
    "warn_total_position_ratio": 0.80,
    "warn_single_position_ratio": 0.15,
    "max_annual_vol": 0.40,
    "warn_annual_vol": 0.25,
    "max_vol_spike_ratio": 3.5,
    "warn_vol_spike_ratio": 2.5,
    "var_confidence": 0.95,
    "min_effective_positions": 3,
    "warn_effective_positions": 5,
    "max_drawdown": 0.20,
    "warn_drawdown": 0.10,
    "max_daily_loss": 0.05,
    "max_consecutive_losses": 8,
    "warn_consecutive_losses": 5,
    "max_liquidity_share": 0.10,
    "warn_liquidity_share": 0.05,
    "max_slippage_dev": 5.0,
    "max_partial_fill_ratio": 0.25,
    "min_fill_rate": 0.70,
}


def _safe(v: Any, default: float = 0.0) -> float:
    """数值兜底：None/NaN/Inf → default。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not np.isfinite(f):
        return default
    return f


def compute_portfolio_metrics(
    account: dict[str, Any],
    positions: dict[str, dict[str, Any]],
    equity_curve: list[Any] | None = None,
    prices: dict[str, float] | None = None,
    execution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """计算组合级风控指标（纯函数，不抛错）。

    Args:
        account: AccountStatus（total_equity/cash/peak_equity/daily_pnl/margin_used/position_value）
        positions: {symbol: {market/direction/quantity/avg_price/multiplier/margin_rate/realized_pnl}}
        equity_curve: 逐日盯市记录列表（含 equity/daily_pnl），用于波动/VaR/回撤/连续亏损
        prices: {symbol: 当前价}；缺失时用 pos 自带 avg_price 兜底
        execution: 执行质量指标 {slippage_dev/partial_fill_ratio/fill_rate}（tick 撮合后传入，可选）

    Returns:
        metrics dict（含 metric 名 → 值；数据不足时值为 None 或 0，不抛错）。
    """
    equity = _safe(account.get("total_equity", account.get("balance")))
    cash = _safe(account.get("cash"))
    peak = _safe(account.get("peak_equity"))
    daily_pnl = _safe(account.get("daily_pnl"))
    margin_used = _safe(account.get("margin_used"))
    position_value_acct = _safe(account.get("position_value"))

    # ── 1. 杠杆/仓位维度 ──
    notional_total = position_value_acct
    single_ratios: list[float] = []
    for sym, pos in positions.items():
        qty = _safe(pos.get("quantity"))
        price = _safe(prices.get(sym)) if prices and sym in prices else _safe(pos.get("avg_price"))
        mult = _safe(pos.get("multiplier"), 1.0)
        notional = qty * price * mult
        notional_total += notional
        single_ratios.append(notional / max(equity, 1e-9))
    leverage = notional_total / max(equity, 1e-9)
    total_position_ratio = min(notional_total / max(equity, 1e-9), leverage)
    max_single_position_ratio = max(single_ratios, default=0.0)
    margin_usage = margin_used / max(equity, 1e-9) if equity > 0 else 0.0

    # ── 2. 波动/尾部维度（基于组合权益曲线日收益）──
    vol_metrics = _compute_vol_tail_metrics(equity_curve)

    # ── 3. 相关性维度（集中度代理：有效持仓数）──
    weights = np.array([r for r in single_ratios if r > 1e-9], dtype=float)
    if weights.size > 0:
        w_norm = weights / weights.sum()
        effective_n = float(1.0 / np.sum(w_norm**2)) if np.any(w_norm) else 0.0
    else:
        effective_n = 0.0

    # ── 4. 损益维度 ──
    drawdown = (equity / peak - 1.0) if peak > 0 else 0.0
    daily_loss_ratio = -daily_pnl / max(equity, 1e-9) if equity > 0 else 0.0
    loss_metrics = _compute_loss_history(equity_curve)

    # ── 5. 流动性维度（默认 0，调用方可选传入）──
    liquidity_share = _safe(execution.get("liquidity_share")) if execution else 0.0

    # ── 6. 执行质量维度（tick 撮合后传入）──
    slippage_dev = _safe(execution.get("slippage_dev")) if execution else 0.0
    partial_fill_ratio = _safe(execution.get("partial_fill_ratio")) if execution else 0.0
    fill_rate = _safe(execution.get("fill_rate"), 1.0) if execution else 1.0

    return {
        "leverage": leverage,
        "total_position_ratio": total_position_ratio,
        "max_single_position_ratio": max_single_position_ratio,
        "margin_usage": margin_usage,
        "effective_positions": effective_n,
        "drawdown": drawdown,
        "daily_loss_ratio": daily_loss_ratio,
        "consecutive_losses": loss_metrics["consecutive_losses"],
        "win_loss_ratio": loss_metrics["win_loss_ratio"],
        "cash": cash,
        "liquidity_share": liquidity_share,
        "slippage_dev": slippage_dev,
        "partial_fill_ratio": partial_fill_ratio,
        "fill_rate": fill_rate,
        **vol_metrics,
    }


def _compute_vol_tail_metrics(equity_curve: list[Any] | None) -> dict[str, float]:
    """组合波动/VaR/CVaR/突变（权益曲线日收益，EWMA λ=0.94）。"""
    if not equity_curve:
        return {"annual_vol": 0.0, "var95": 0.0, "cvar95": 0.0, "vol_spike_ratio": 1.0}
    eqs: list[float] = []
    for rec in equity_curve:
        e = _safe(rec.get("equity") if isinstance(rec, dict) else getattr(rec, "equity", None))
        if e > 0:
            eqs.append(e)
    if len(eqs) < 5:
        return {"annual_vol": 0.0, "var95": 0.0, "cvar95": 0.0, "vol_spike_ratio": 1.0}
    rets = np.diff(np.log(np.array(eqs, dtype=float)))
    if len(rets) < 4:
        return {"annual_vol": 0.0, "var95": 0.0, "cvar95": 0.0, "vol_spike_ratio": 1.0}
    # EWMA 波动率（λ=0.94，年化 √252）
    lam = 0.94
    ewma_var = np.var(rets[-20:], ddof=0) if len(rets) >= 20 else np.var(rets, ddof=0)
    for r in reversed(rets[:-1][-30:]):
        ewma_var = lam * ewma_var + (1 - lam) * r**2
    daily_vol = float(np.sqrt(max(ewma_var, 1e-12)))
    annual_vol = daily_vol * np.sqrt(252)
    # VaR/CVaR（历史分位）
    conf = 0.95
    var95 = float(-np.percentile(rets, (1 - conf) * 100))
    tail = rets[rets <= -var95]
    cvar95 = float(-tail.mean()) if tail.size else var95
    # 波动率突变：近5日 EWMA / 前25日
    recent_vol = float(np.std(rets[-5:], ddof=0)) if len(rets) >= 5 else daily_vol
    base_vol = float(np.std(rets[:-5][-25:], ddof=0)) if len(rets) > 10 else daily_vol
    vol_spike_ratio = recent_vol / max(base_vol, 1e-12)
    return {
        "annual_vol": round(annual_vol, 6),
        "var95": round(var95, 6),
        "cvar95": round(cvar95, 6),
        "vol_spike_ratio": round(vol_spike_ratio, 4),
    }


def _compute_loss_history(equity_curve: list[Any] | None) -> dict[str, Any]:
    """连续亏损次数与近 20 日盈亏比。"""
    if not equity_curve:
        return {"consecutive_losses": 0, "win_loss_ratio": 0.0}
    pnls: list[float] = []
    for rec in equity_curve:
        p = _safe(rec.get("daily_pnl") if isinstance(rec, dict) else getattr(rec, "daily_pnl", None))
        pnls.append(p)
    pnls = [p for p in pnls if p != 0.0]
    if not pnls:
        return {"consecutive_losses": 0, "win_loss_ratio": 0.0}
    consec = 0
    for p in reversed(pnls):
        if p < 0:
            consec += 1
        else:
            break
    recent = pnls[-20:]
    wins = sum(1 for p in recent if p > 0)
    losses = sum(1 for p in recent if p < 0)
    win_loss = (wins / losses) if losses > 0 else (float(wins) if wins else 0.0)
    return {"consecutive_losses": consec, "win_loss_ratio": round(win_loss, 4)}


def evaluate_metrics(
    metrics: dict[str, Any],
    config: PortfolioMetricsConfig | None = None,
) -> dict[str, Any]:
    """对组合指标做三级预警判定（WARN / BLOCK / FORCE_CLOSE）。

    Args:
        metrics: compute_portfolio_metrics 输出
        config: 阈值配置（缺省使用默认）

    Returns:
        {
          "checks": [{name, severity, value, limit, message}],
          "max_severity": "WARN"|"BLOCK"|"FORCE_CLOSE"|"OK",
          "block_new_open": bool,   # 是否拒绝新开仓
          "force_close": bool,      # 是否触发强平
        }
    """
    cfg = dict(_DEFAULT_CONFIG)
    if config:
        cfg.update({k: v for k, v in config.items() if v is not None})

    checks: list[dict[str, Any]] = []

    def _add(name: str, severity: str, value: float, limit: float, msg: str) -> None:
        if severity != "OK":
            checks.append(
                {
                    "name": name,
                    "severity": severity,
                    "value": round(float(value), 6),
                    "limit": float(limit),
                    "message": msg,
                }
            )

    # 杠杆/仓位
    lev = _safe(metrics.get("leverage"))
    if lev > float(cfg["max_leverage"]):
        _add("leverage", FORCE_CLOSE, lev, cfg["max_leverage"], f"杠杆 {lev:.2f}x 超上限 {cfg['max_leverage']:.1f}x")
    tot = _safe(metrics.get("total_position_ratio"))
    if tot > float(cfg["max_total_position_ratio"]):
        _add("total_position", FORCE_CLOSE, tot, cfg["max_total_position_ratio"], "总仓位超上限")
    elif tot > float(cfg["warn_total_position_ratio"]):
        _add("total_position", WARN, tot, cfg["max_total_position_ratio"], "总仓位接近上限")
    single = _safe(metrics.get("max_single_position_ratio"))
    if single > float(cfg["max_single_position_ratio"]):
        _add("single_position", BLOCK, single, cfg["max_single_position_ratio"], "单标的仓位超上限")
    elif single > float(cfg["warn_single_position_ratio"]):
        _add("single_position", WARN, single, cfg["max_single_position_ratio"], "单标的仓位接近上限")
    margin = _safe(metrics.get("margin_usage"))
    if margin > float(cfg["max_margin_usage"]):
        _add("margin_usage", BLOCK, margin, cfg["max_margin_usage"], "保证金占用超上限")

    # 波动/尾部
    avol = _safe(metrics.get("annual_vol"))
    if avol > float(cfg["max_annual_vol"]):
        _add("annual_vol", BLOCK, avol, cfg["max_annual_vol"], "组合年化波动超上限")
    elif avol > float(cfg["warn_annual_vol"]):
        _add("annual_vol", WARN, avol, cfg["max_annual_vol"], "组合年化波动偏高")
    spike = _safe(metrics.get("vol_spike_ratio"))
    if spike > float(cfg["max_vol_spike_ratio"]):
        _add("vol_spike", BLOCK, spike, cfg["max_vol_spike_ratio"], "波动率突变超限")

    # 相关性（有效持仓数）
    eff = _safe(metrics.get("effective_positions"))
    if 0 < eff < float(cfg["min_effective_positions"]):
        _add("effective_positions", BLOCK, eff, cfg["min_effective_positions"], "有效持仓数过低，集中度风险")
    elif 0 < eff < float(cfg["warn_effective_positions"]):
        _add("effective_positions", WARN, eff, cfg["min_effective_positions"], "有效持仓数偏低")

    # 损益
    dd = _safe(metrics.get("drawdown"))
    if dd <= -float(cfg["max_drawdown"]):
        _add("drawdown", FORCE_CLOSE, dd, cfg["max_drawdown"], "回撤超上限，触发强平")
    elif dd <= -float(cfg["warn_drawdown"]):
        _add("drawdown", WARN, dd, cfg["max_drawdown"], "回撤接近上限")
    dloss = _safe(metrics.get("daily_loss_ratio"))
    if dloss > float(cfg["max_daily_loss"]):
        _add("daily_loss", FORCE_CLOSE, dloss, cfg["max_daily_loss"], "单日亏损超上限，触发强平")
    consec = int(_safe(metrics.get("consecutive_losses")))
    if consec >= int(cfg["max_consecutive_losses"]):
        _add("consecutive_losses", FORCE_CLOSE, float(consec), cfg["max_consecutive_losses"], "连续亏损超限，暂停交易")

    # 执行质量（tick 后启用）
    slip = _safe(metrics.get("slippage_dev"))
    if slip > float(cfg["max_slippage_dev"]):
        _add("slippage_dev", BLOCK, slip, cfg["max_slippage_dev"], "滑点偏离超限")
    part = _safe(metrics.get("partial_fill_ratio"))
    if part > float(cfg["max_partial_fill_ratio"]):
        _add("partial_fill", BLOCK, part, cfg["max_partial_fill_ratio"], "部分成交占比超限")
    fill = _safe(metrics.get("fill_rate"), 1.0)
    if fill < float(cfg["min_fill_rate"]):
        _add("fill_rate", BLOCK, fill, cfg["min_fill_rate"], "成交率过低")

    severity_rank = {FORCE_CLOSE: 3, BLOCK: 2, WARN: 1}
    max_rank = max((severity_rank[c["severity"]] for c in checks), default=0)
    max_sev = next((s for s, r in severity_rank.items() if r == max_rank), "OK") if max_rank else "OK"

    return {
        "checks": checks,
        "max_severity": max_sev,
        "block_new_open": max_rank >= severity_rank[BLOCK],
        "force_close": max_rank >= severity_rank[FORCE_CLOSE],
    }
