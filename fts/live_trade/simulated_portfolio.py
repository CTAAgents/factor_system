"""
fts.live_trade.simulated_portfolio — 模拟仓核心（D.1，v2.102.0）。

用仿真交易替代真实账户，打通"信号 → 模拟撮合 → 逐日盯市 → 因子归因 → 反馈闭环"链路。

账户模型（保真核算）:
    - 期货: 开仓将保证金从 cash 划出，平仓回补并结转已实现盈亏；
      总权益 = cash + 当前保证金占用 + 期货浮动盈亏 + 股票市值。
    - 股票/ETF: 开仓全额划出，平仓回补；总权益 = cash + 持仓市值。

撮合纪律与回测仿真对齐（滑点/手续费/保证金），风控/干预复用既有
`RiskManager` / `InterventionController`，下单前强制校验。

FTS 角色边界: 只做模拟核算，真实撮合由下游（FDT）负责。

版本: v1.0.0（D.1）
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict

from fts.factor_engine.cost_model import TransactionCostModel
from fts.factor_engine.feedback_loop import LiveFeedbackImporter
from fts.live_trade.contracts import (
    SimAccount,
    SimApplyResult,
    SimDailyRecord,
    SimFill,
    SimPosition,
    contract_multiplier,
    infer_market,
)
from fts.live_trade.intervention import InterventionController
from fts.live_trade.sqlite_store import SimSQLiteStore
from fts.risk.risk_manager import RiskConfig, RiskManager

logger = logging.getLogger(__name__)

# 模拟仓缺省风控（宽松）：仿真以保真核算为主，严格风控由调用方显式注入 RiskManager 开启
_PERMISSIVE_RISK: RiskConfig = {
    "single_position_limit_pct": 1.0,
    "max_portfolio_drawdown_pct": 0.99,
    "daily_loss_limit_pct": 0.99,
    "max_leverage": 10.0,
    "max_concentration_pct": 1.0,
    "max_open_positions": 50,
}

# 撮合方向（内部）
_OPEN_LONG = "open_long"
_OPEN_SHORT = "open_short"
_CLOSE_LONG = "close_long"
_CLOSE_SHORT = "close_short"


class SimPortfolioConfig(TypedDict, total=False):
    """模拟仓配置。"""

    initial_cash: float
    margin_rate_map: dict[str, float]  # {品种代码: 保证金率}
    default_margin_rate: float  # 期货缺省保证金率（默认 0.12）
    default_market: str  # 缺省市场（默认 "futures"）


def _now_iso() -> str:
    """当前 UTC 时间 ISO 格式。"""
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str = "") -> str:
    """生成短 UUID。"""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


class SimulatedPortfolio:
    """模拟仓主类：持仓/盯市/撮合/风控/归因/闭环。"""

    def __init__(
        self,
        config: Optional[SimPortfolioConfig] = None,
        gateway: Any = None,
        cost_model: Optional[TransactionCostModel] = None,
        risk_manager: Optional[RiskManager] = None,
        intervention: Optional[InterventionController] = None,
        store: Optional[SimSQLiteStore] = None,
        matching: Any = None,
    ) -> None:
        """初始化模拟仓。

        Args:
            config: 模拟仓配置（缺省使用默认值）
            gateway: 保留网关参数（当前撮合走内部仿真，不依赖 gateway）
            cost_model: 交易成本模型（提供滑点/手续费/保证金率）
            risk_manager: 风控管理器（缺省使用默认风险规则）
            intervention: 人工干预控制器（缺省使用默认实现）
            store: SQLite 持久化存储（注入后启动恢复 + 每次变更落盘；None 不持久化）
            matching: tick 盘口撮合引擎（D.2 §4，可选；注入后成交价走盘口
                逐档撮合，无盘口/异常自动降级 bps；None 保持现状）
        """
        cfg: SimPortfolioConfig = SimPortfolioConfig(**(config or {}))
        self._initial_cash: float = float(cfg.get("initial_cash", 1_000_000.0))
        self._margin_rate_map: dict[str, float] = dict(cfg.get("margin_rate_map") or {})
        self._default_margin_rate: float = float(cfg.get("default_margin_rate", 0.12))
        self._default_market: str = cfg.get("default_market", "futures")

        self._cash: float = self._initial_cash
        self._positions: dict[str, SimPosition] = {}
        self._daily_records: list[SimDailyRecord] = []
        self._peak_equity: float = self._initial_cash
        self._last_equity: float = self._initial_cash
        self._daily_pnl: float = 0.0
        self._realized_pnl_total: float = 0.0
        self._last_turnover: float = 0.0

        self._cost_model = cost_model or TransactionCostModel()
        self._risk_manager = risk_manager or RiskManager(_PERMISSIVE_RISK)
        self._intervention = intervention or InterventionController()

        # tick 盘口撮合（D.2 §4）：注入后启用盘口逐档撮合；None 保持 bps 路径
        self._matching = matching
        self._book_provider: Any = None

        # SQLite 持久化：注入 store 则启动恢复既有状态
        self._store = store
        if store is not None:
            self._restore_from_store()

    def set_book_provider(self, provider: Any) -> None:
        """注入盘口提供方（D.2 §4）：``(symbol) -> OrderBookSnapshot | None``。

        提供方返回 None 时撮合自动降级 bps 路径。
        """
        self._book_provider = provider

    # ─── 对外接口 ────────────────────────────────────────

    def apply_signal(self, signal: dict[str, Any], prices: dict[str, float], date: str) -> SimApplyResult:
        """应用信号：干预/风控门 → 目标仓位映射 → 撮合。

        Args:
            signal: FactorSignal 契约字典
            prices: {symbol: 成交价}（t+1 开盘价，避免未来函数）
            date: 信号日期（YYYY-MM-DD）

        Returns:
            SimApplyResult（approved=False 时 blocked_reasons 非空）。
        """
        trace_id = (signal.get("meta") or {}).get("trace_id", "")
        result: SimApplyResult = {
            "signal_id": signal.get("signal_id", ""),
            "date": date,
            "approved": False,
            "fills": [],
            "blocked_reasons": [],
            "trace_id": trace_id,
        }

        # 1. 干预门（权限最高）
        if self._intervention.should_block():
            result["blocked_reasons"] = ["intervention"]
            logger.warning("[SimPortfolio] 干预拦截信号 [signal_id=%s]", signal.get("signal_id"))
            return result

        # 2. 目标仓位映射 + 风控门（注入 prices 供风控按名义金额核定）
        targets: dict[str, float] = {}

        def _enrich_price(leg: dict[str, Any]) -> dict[str, Any]:
            """将成交价写入 signal leg（RiskManager 依赖 leg['price'] 核定额度）。"""
            cp = dict(leg)
            if cp.get("price") is None:
                cp["price"] = prices.get(cp.get("symbol", ""), 0.0)
            return cp

        risk_signal: dict[str, Any] = dict(signal)
        risk_signal["signals"] = [_enrich_price(leg) for leg in signal.get("signals") or []]
        for leg in risk_signal["signals"]:
            symbol = leg.get("symbol", "")
            direction = leg.get("direction", "flat")
            position = float(leg.get("position", 0.0) or 0.0)
            if direction == "long":
                targets[symbol] = position
            elif direction == "short":
                targets[symbol] = -position
            else:  # flat
                targets[symbol] = 0.0

        risk_result = self._risk_manager.check(
            risk_signal,
            self._risk_account(),
            self._risk_positions(),
        )
        if not risk_result["approved"]:
            reasons = [c.get("check_name", "risk") for c in risk_result["blocking_violations"]]
            result["blocked_reasons"] = reasons
            logger.warning("[SimPortfolio] 风控拦截 [signal_id=%s, reasons=%s]", signal.get("signal_id"), reasons)
            return result

        # 3. 撮合
        fills: list[SimFill] = []
        for symbol, target in targets.items():
            market = self._market_for(symbol)
            fills.extend(self._reconcile(symbol, market, target, prices.get(symbol), date))

        result["approved"] = True
        result["fills"] = fills
        self._last_turnover = self._compute_turnover(fills)
        if self._store is not None:
            self._persist_account_positions()
            self._store.append_fills(fills)
        return result

    def mark_to_market(self, date: str, prices: dict[str, float]) -> SimDailyRecord:
        """逐日盯市：计算未实现盈亏/保证金/权益，追加日度记录。

        Args:
            date: 盯市日期（YYYY-MM-DD）
            prices: {symbol: 收盘价}

        Returns:
            SimDailyRecord（行情缺失标的跳过，不中断）。
        """
        futures_notional = 0.0
        futures_margin = 0.0
        futures_unrealized = 0.0
        stock_value = 0.0
        stock_unrealized = 0.0

        for sym, pos in self._positions.items():
            price = prices.get(sym)
            if price is None:
                # 行情缺失：回退持仓均价，保证金照常占用、浮动盈亏记 0，不中断
                price = pos["avg_price"]
            if pos["market"] == "futures":
                mult = pos["multiplier"]
                notional = price * mult * pos["quantity"]
                futures_notional += notional
                futures_margin += notional * pos["margin_rate"]
                if pos["direction"] == "long":
                    futures_unrealized += (price - pos["avg_price"]) * mult * pos["quantity"]
                else:
                    futures_unrealized += (pos["avg_price"] - price) * mult * pos["quantity"]
            else:
                stock_value += price * pos["quantity"]
                stock_unrealized += (price - pos["avg_price"]) * pos["quantity"]

        equity = self._cash + futures_margin + futures_unrealized + stock_value
        prev_equity = self._last_equity
        daily = equity - prev_equity
        self._daily_pnl = daily
        self._last_equity = equity
        self._peak_equity = max(self._peak_equity, equity)

        record = SimDailyRecord(
            date=date,
            equity=round(equity, 6),
            cash=round(self._cash, 6),
            margin_used=round(futures_margin, 6),
            position_value=round(futures_notional + stock_value, 6),
            realized_pnl=round(self._realized_pnl_total, 6),
            unrealized_pnl=round(futures_unrealized + stock_unrealized, 6),
            daily_pnl=round(daily, 6),
            turnover=round(self._last_turnover, 6),
            n_positions=len(self._positions),
        )
        self._daily_records.append(record)
        if self._store is not None:
            self._persist_account_positions()
            self._store.append_equity(record)
        return record

    def close_symbol(self, symbol: str, price: float) -> SimFill | None:
        """全平某标的持仓（供一键平仓/紧急离场）。"""
        pos = self._positions.get(symbol)
        if pos is None:
            return None
        market = pos["market"]
        side = _CLOSE_LONG if pos["direction"] == "long" else _CLOSE_SHORT
        fill = self._close(symbol, market, pos["quantity"], price, date=_now_iso()[:10], side=side)
        logger.info("[SimPortfolio] 全平持仓 [symbol=%s, price=%s]", symbol, price)
        return fill

    def all_close(self, prices: dict[str, float]) -> list[SimFill]:
        """一键平仓全部持仓。"""
        fills: list[SimFill] = []
        for symbol in list(self._positions.keys()):
            price = prices.get(symbol)
            if price is None:
                continue
            fill = self.close_symbol(symbol, price)
            if fill:
                fills.append(fill)
        return fills

    def account_status(self) -> SimAccount:
        """返回账户状态（供风控/展示）。"""
        return self._risk_account()

    def positions(self) -> dict[str, SimPosition]:
        """返回当前持仓快照。"""
        return dict(self._positions)

    def equity_curve(self) -> list[SimDailyRecord]:
        """返回逐日盯市记录（权益曲线）。"""
        return list(self._daily_records)

    def portfolio_risk_status(self, prices: Optional[dict[str, float]] = None) -> dict[str, Any]:
        """组合级风控指标（D.2 §3）：计算 6 维度指标并做三级预警判定。

        在既有单笔/单标的/总仓风控（RiskManager）之上补充组合级视角
        （杠杆/波动/VaR/集中度/回撤/连续亏损等）。数据不足时指标降级为
        0/通过，不抛错。调用方可按 ``max_severity`` / ``force_close``
        决定动作（WARN=告警 / BLOCK=拒绝新开仓 / FORCE_CLOSE=触发强平）。

        Args:
            prices: {symbol: 当前价}（可选；缺失用持仓均价兜底）

        Returns:
            {"checks": [...], "max_severity", "block_new_open", "force_close"}
        """
        from fts.risk.portfolio_metrics import compute_portfolio_metrics, evaluate_metrics

        metrics = compute_portfolio_metrics(
            account=dict(self._risk_account()),
            positions={sym: dict(pos) for sym, pos in self._positions.items()},
            equity_curve=list(self._daily_records),
            prices=prices,
        )
        return evaluate_metrics(metrics)

    def attribute_factor_returns(
        self,
        signal: dict[str, Any],
        next_return: dict[str, float],
    ) -> list[dict[str, Any]]:
        """因子收益归因（D.1 §5.4）。

        对每个出现在 contributing_factors 的因子，输出一条 LiveFeedbackRecord：
            signal_value    = Σ(position × factor_signal) / Σ position
            position_return = Σ(position × direction_symbol_return) / Σ position

        Args:
            signal: FactorSignal 契约字典
            next_return: {symbol: 区间收益}（t→t+1）

        Returns:
            LiveFeedbackRecord 列表（可直接经 LiveFeedbackImporter 落盘）。
        """
        buckets: dict[str, dict[str, float]] = {}
        for leg in signal.get("signals") or []:
            symbol = leg.get("symbol", "")
            direction = leg.get("direction", "flat")
            if direction not in ("long", "short"):
                continue
            position = float(leg.get("position", 0.0) or 0.0)
            if position <= 0:
                continue
            sign = 1.0 if direction == "long" else -1.0
            sym_ret = float(next_return.get(symbol, 0.0) or 0.0) * sign
            for fc in leg.get("contributing_factors") or []:
                fid = fc.get("factor_id")
                if not fid:
                    continue
                fsig = float(fc.get("signal", 0.0) or 0.0)
                b = buckets.setdefault(fid, {"pos_sum": 0.0, "wsig": 0.0, "wret": 0.0})
                b["pos_sum"] += position
                b["wsig"] += position * fsig
                b["wret"] += position * sym_ret

        date = str(signal.get("timestamp", ""))[:10]
        market = self._default_market
        records: list[dict[str, Any]] = []
        for fid, b in buckets.items():
            if b["pos_sum"] <= 1e-9:
                continue
            records.append(
                {
                    "factor_id": fid,
                    "signal_date": date,
                    "signal_value": round(b["wsig"] / b["pos_sum"], 6),
                    "position_return": round(b["wret"] / b["pos_sum"], 6),
                    "turnover": round(self._last_turnover, 6),
                    "market": market,
                }
            )
        return records

    def import_feedback(self, records: list[dict[str, Any]], db_path: Optional[str] = None) -> dict[str, Any]:
        """将归因记录落盘（GAP-L402 闭环入口）。"""
        importer = LiveFeedbackImporter(db_path)
        return importer.import_records(records)

    # ─── 内部：撮合 ──────────────────────────────────────

    def _reconcile(
        self,
        symbol: str,
        market: str,
        target_signed: float,
        base_price: Optional[float],
        date: str,
    ) -> list[SimFill]:
        """将某标的持仓收敛到目标有符号仓位（开/平/加/减/反手统一）。

        有符号仓位：正=多，负=空，0=空仓。按目标与当前相对关系分派：
            空仓→开仓；目标0→平仓；同向→加/减仓；反向→先平后开（反手）。
        """
        fills: list[SimFill] = []
        if base_price is None or base_price <= 0:
            logger.warning("[SimPortfolio] 无成交价，跳过撮合 [symbol=%s]", symbol)
            return fills

        pos = self._positions.get(symbol)
        current_signed = 0.0
        if pos is not None:
            current_signed = pos["quantity"] if pos["direction"] == "long" else -pos["quantity"]

        diff = target_signed - current_signed
        if abs(diff) < 1e-9:
            return fills

        cost = self._cost_model.get_cost_bps(market)
        fee_rate = float(cost.get("commission_bps", 0.3)) / 10000.0
        slippage_bps = float(cost.get("slippage_bps", 0.0))

        def _side_open() -> tuple[str, str]:
            direction = "long" if target_signed > 0 else "short"
            side = _OPEN_LONG if direction == "long" else _OPEN_SHORT
            return direction, side

        def _side_close() -> str:
            return _CLOSE_LONG if current_signed > 0 else _CLOSE_SHORT

        # 1. 空仓 → 开仓
        if abs(current_signed) < 1e-9:
            direction, side = _side_open()
            fills.append(
                self._open(
                    symbol, market, direction, abs(target_signed), base_price, fee_rate, date, side, slippage_bps
                )
            )
        # 2. 有仓 → 目标 0：平仓
        elif abs(target_signed) < 1e-9:
            fills.append(
                self._close(symbol, market, abs(current_signed), base_price, date, _side_close(), slippage_bps)
            )
        # 3. 同向：加仓或减仓
        elif target_signed * current_signed > 0:
            delta = abs(target_signed) - abs(current_signed)
            if abs(delta) < 1e-9:
                return fills
            if delta > 0:
                direction, side = _side_open()
                fills.append(
                    self._open(symbol, market, direction, delta, base_price, fee_rate, date, side, slippage_bps)
                )
            else:
                fills.append(self._close(symbol, market, -delta, base_price, date, _side_close(), slippage_bps))
        # 4. 反向：先平后开（反手）
        else:
            fills.append(
                self._close(symbol, market, abs(current_signed), base_price, date, _side_close(), slippage_bps)
            )
            direction, side = _side_open()
            fills.append(
                self._open(
                    symbol, market, direction, abs(target_signed), base_price, fee_rate, date, side, slippage_bps
                )
            )
        return fills

    def _open(
        self,
        symbol: str,
        market: str,
        direction: str,
        quantity: float,
        base_price: float,
        fee_rate: float,
        date: str,
        side: str,
        slippage_bps: float = 0.0,
    ) -> SimFill:
        """开仓/加仓。"""
        mult = contract_multiplier(symbol) if market == "futures" else 1.0
        margin_rate = self._margin_rate(symbol, market)
        fill_price = self._execution_price(symbol, side, base_price, quantity, slippage_bps)
        notional = fill_price * mult * quantity
        fee = notional * fee_rate

        if market == "futures":
            self._cash -= notional * margin_rate  # 保证金划出
        else:
            self._cash -= notional  # 全额划出
        self._cash -= fee

        pos = self._positions.get(symbol)
        if pos is not None and pos["direction"] == direction:
            # 同向加仓：加权平均
            total_qty = pos["quantity"] + quantity
            pos["avg_price"] = (pos["avg_price"] * pos["quantity"] + fill_price * quantity) / total_qty
            pos["quantity"] = total_qty
            pos["realized_pnl"] = pos.get("realized_pnl", 0.0)
        else:
            self._positions[symbol] = SimPosition(
                symbol=symbol,
                market=market,
                direction=direction,
                quantity=quantity,
                avg_price=fill_price,
                multiplier=mult,
                margin_rate=margin_rate,
                opened_at=date,
                realized_pnl=0.0,
            )
        return SimFill(
            order_id=_new_id("sim_"),
            symbol=symbol,
            side=side,
            quantity=quantity,
            fill_price=round(fill_price, 8),
            fee=round(fee, 6),
            slippage_cost=round(abs(fill_price - base_price) * mult * quantity, 6),
            timestamp=_now_iso(),
        )

    def _close(
        self,
        symbol: str,
        market: str,
        quantity: float,
        base_price: float,
        date: str,
        side: str,
        slippage_bps: float = 0.0,
    ) -> SimFill:
        """平仓/减仓：释放保证金/资金，结转已实现盈亏。"""
        pos = self._positions[symbol]
        mult = pos["multiplier"]
        margin_rate = pos["margin_rate"]
        direction = pos["direction"]
        fill_price = self._execution_price(symbol, side, base_price, quantity, slippage_bps)
        notional = fill_price * mult * quantity

        cost = self._cost_model.get_cost_bps(market)
        fee_rate = float(cost.get("commission_bps", 0.3)) / 10000.0
        fee = notional * fee_rate

        if direction == "long":
            realized = (fill_price - pos["avg_price"]) * mult * quantity
        else:
            realized = (pos["avg_price"] - fill_price) * mult * quantity

        if market == "futures":
            self._cash += notional * margin_rate  # 释放保证金
        else:
            self._cash += notional  # 回补全额资金
        self._cash += realized
        self._cash -= fee
        self._realized_pnl_total += realized

        remaining = pos["quantity"] - quantity
        if remaining <= 1e-9:
            del self._positions[symbol]
        else:
            pos["quantity"] = remaining
            pos["realized_pnl"] = pos.get("realized_pnl", 0.0) + realized
        return SimFill(
            order_id=_new_id("sim_"),
            symbol=symbol,
            side=side,
            quantity=quantity,
            fill_price=round(fill_price, 8),
            fee=round(fee, 6),
            slippage_cost=round(abs(fill_price - base_price) * mult * quantity, 6),
            timestamp=_now_iso(),
        )

    def _execution_price(
        self,
        symbol: str,
        side: str,
        base_price: float,
        quantity: float,
        slippage_bps: float = 0.0,
    ) -> float:
        """撮合成交价（D.2 §4）：盘口逐档撮合优先，无盘口/异常降级 bps。

        - 注入 matching + book_provider 时：市价单按对手盘逐档消耗，成交均价
          为加权均价（滑点自然产生）；深度不足部分成交（fill 量为盘中已取，
          剩余未成交量由上层处理）。
        - 未注入或盘口不可用：bps 路径（买入上浮、卖下沉），保持现状。
        """
        if self._matching is not None and self._book_provider is not None:
            try:
                book = self._book_provider(symbol)
                side_key = "buy" if side in (_OPEN_LONG, _CLOSE_SHORT) else "sell"
                res = self._matching.match_market(book, side_key, quantity, base_price)
                if res.get("book_used") and res.get("avg_price", 0.0) > 0:
                    return float(res["avg_price"])
            except Exception:  # noqa: BLE001 — 盘口异常降级 bps，不抛错
                pass
        slip = slippage_bps / 10000.0
        if side in (_OPEN_LONG, _CLOSE_SHORT):
            return base_price * (1 + slip)
        return base_price * (1 - slip)

    # ─── 内部：账户/风控 ─────────────────────────────────

    def _margin_rate(self, symbol: str, market: str) -> float:
        """解析保证金率：显式映射 > 成本模型 > 缺省。"""
        if market != "futures":
            return 1.0
        if symbol in self._margin_rate_map:
            return self._margin_rate_map[symbol]
        cost = self._cost_model.get_cost_bps(market)
        return float(cost.get("margin_rate", self._default_margin_rate))

    def _market_for(self, symbol: str) -> str:
        """推断标的归属市场。”"""
        return infer_market(symbol, self._default_market)

    def _risk_account(self) -> SimAccount:
        """构造风控账户快照。"""
        return SimAccount(
            cash=self._cash,
            total_equity=self._last_equity,
            available=self._cash,
            margin_used=0.0,
            position_value=0.0,
            peak_equity=self._peak_equity,
            daily_pnl=self._daily_pnl,
            realized_pnl_total=self._realized_pnl_total,
            unrealized_pnl=0.0,
        )

    def _risk_positions(self) -> dict[str, dict[str, Any]]:
        """构造风控持仓快照（symbol → market_value）。"""
        out: dict[str, dict[str, Any]] = {}
        for sym, pos in self._positions.items():
            out[sym] = {"market_value": self._position_market_value(pos)}
        return out

    @staticmethod
    def _position_market_value(pos: SimPosition, close: Optional[float] = None) -> float:
        """估算持仓市值（用持仓均价近似，供风控）。"""
        if close is not None:
            return close * pos["multiplier"] * pos["quantity"]
        return pos["avg_price"] * pos["multiplier"] * pos["quantity"]

    def _compute_turnover(self, fills: list[SimFill]) -> float:
        """计算本次撮合换手 = 名义成交额 / 总权益。"""
        if not fills or self._last_equity <= 0:
            return 0.0
        notional = sum((f.get("fill_price", 0.0) * f.get("quantity", 0.0)) for f in fills)
        return notional / self._last_equity

    # ─── 内部：SQLite 持久化 ─────────────────────────────

    def _restore_from_store(self) -> None:
        """启动时从 SQLite 恢复账户/持仓/权益曲线（缺失零风险忽略）。"""
        assert self._store is not None
        acct = self._store.load_account()
        if acct is not None:
            self._initial_cash = float(acct.get("initial_cash", self._initial_cash))
            self._cash = float(acct.get("cash", self._cash))
            self._peak_equity = float(acct.get("peak_equity", self._peak_equity))
            self._last_equity = float(acct.get("last_equity", self._last_equity))
            self._daily_pnl = float(acct.get("daily_pnl", self._daily_pnl))
            self._realized_pnl_total = float(acct.get("realized_pnl_total", self._realized_pnl_total))
            self._last_turnover = float(acct.get("last_turnover", self._last_turnover))
        self._positions = self._store.load_positions()
        self._daily_records = self._store.load_equity_curve()

    def _persist_account_positions(self) -> None:
        """将账户状态与持仓落盘（幂等、事务内）。"""
        assert self._store is not None
        self._store.save_account(
            {
                "initial_cash": self._initial_cash,
                "cash": self._cash,
                "peak_equity": self._peak_equity,
                "last_equity": self._last_equity,
                "daily_pnl": self._daily_pnl,
                "realized_pnl_total": self._realized_pnl_total,
                "last_turnover": self._last_turnover,
            }
        )
        self._store.save_positions(self._positions)


__all__ = ["SimPortfolioConfig", "SimulatedPortfolio"]
