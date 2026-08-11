"""
fts.live_trade.contracts — 模拟仓契约（D.1，v2.102.0）。

定义模拟仓模块的数据契约与辅助函数:
    - ``SimPosition``: 单一持仓（含已实现盈亏）
    - ``SimAccount``: 账户状态（供风控核算）
    - ``SimDailyRecord``: 逐日盯市记录（权益曲线）
    - ``SimFill``: 撮合结果（含滑点/手续费）
    - ``SimApplyResult``: 信号应用结果（成交流/拦截原因）
    - ``ReplayResult``: 历史回放结果
    - ``CONTRACT_MULTIPLIERS`` / ``contract_multiplier`` / ``infer_market``: 合约规格与市场推断

FTS 角色边界: 本模块只做模拟核算，真实撮合由下游（FDT）负责。

版本: v1.0.0（D.1）
"""

from __future__ import annotations

import re
from typing import TypedDict

# ─── 持仓 / 账户 / 日度记录 ──────────────────────────────


class SimPosition(TypedDict, total=False):
    """单一持仓。"""

    symbol: str
    market: str  # futures | stock | etf
    direction: str  # long | short
    quantity: float  # 期货=手数，股票/ETF=股数
    avg_price: float  # 开仓均价（含滑点/手续费影响折算）
    multiplier: float  # 合约乘数（期货），股票=1.0
    margin_rate: float  # 保证金率（期货），股票=1.0
    opened_at: str
    realized_pnl: float  # 该持仓已实现盈亏（部分平仓累计）


class SimAccount(TypedDict, total=False):
    """账户状态（供风控核算）。"""

    cash: float
    total_equity: float
    available: float
    margin_used: float
    position_value: float
    peak_equity: float
    daily_pnl: float
    realized_pnl_total: float
    unrealized_pnl: float


class SimDailyRecord(TypedDict, total=False):
    """逐日盯市记录。"""

    date: str
    equity: float  # 总权益
    cash: float
    margin_used: float  # 保证金占用（期货）
    position_value: float  # 持仓市值
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    turnover: float  # 当日换手
    n_positions: int


# ─── 撮合 / 信号应用结果 ─────────────────────────────────


class SimFill(TypedDict, total=False):
    """撮合结果。"""

    order_id: str
    symbol: str
    side: str  # open_long / open_short / close_long / close_short
    quantity: float
    fill_price: float  # 含滑点
    fee: float
    slippage_cost: float
    timestamp: str


class SimApplyResult(TypedDict, total=False):
    """信号应用结果。"""

    signal_id: str
    date: str
    approved: bool
    fills: list[SimFill]
    blocked_reasons: list[str]
    trace_id: str


class ReplayResult(TypedDict, total=False):
    """历史回放结果。"""

    equity_curve: list[SimDailyRecord]
    feedback_records: list[dict]
    fills: list[SimFill]
    summary: dict


# ─── 合约规格辅助 ─────────────────────────────────────────


# 合约乘数（交易所公开固定规格；与 scripts/liquidity_snapshot.py 保持一致，
# 为静态规格数据，scripts 顶层带 sys.path 副作用不可导入，故于此对齐内嵌）。
CONTRACT_MULTIPLIERS: dict[str, float] = {
    # 上期所
    "CU": 5.0,
    "AL": 5.0,
    "ZN": 5.0,
    "PB": 5.0,
    "NI": 1.0,
    "SN": 1.0,
    "AU": 1000.0,
    "AG": 15.0,
    "RB": 10.0,
    "HC": 10.0,
    "SS": 5.0,
    "RU": 10.0,
    "FU": 10.0,
    "BU": 10.0,
    "SP": 10.0,
    "AO": 20.0,
    "BR": 5.0,
    # 能源中心
    "SC": 1000.0,
    "NR": 10.0,
    "LU": 10.0,
    "BC": 5.0,
    "EC": 50.0,
    # 大商所
    "M": 10.0,
    "Y": 10.0,
    "A": 10.0,
    "B": 10.0,
    "P": 10.0,
    "C": 10.0,
    "CS": 10.0,
    "JD": 5.0,
    "L": 5.0,
    "PP": 5.0,
    "V": 5.0,
    "EB": 5.0,
    "EG": 10.0,
    "PG": 20.0,
    "LH": 16.0,
    "RR": 10.0,
    "J": 100.0,
    "JM": 60.0,
    "I": 100.0,
    "FB": 500.0,
    "BB": 500.0,
    # 郑商所
    "TA": 5.0,
    "MA": 10.0,
    "FG": 20.0,
    "SA": 20.0,
    "SF": 5.0,
    "SM": 5.0,
    "CF": 5.0,
    "SR": 10.0,
    "OI": 10.0,
    "RM": 10.0,
    "RS": 10.0,
    "WH": 20.0,
    "JR": 20.0,
    "LR": 20.0,
    "RI": 20.0,
    "CY": 5.0,
    "AP": 10.0,
    "CJ": 5.0,
    "UR": 20.0,
    "PK": 5.0,
    "PF": 5.0,
    "PX": 5.0,
    "SH": 30.0,
    # 中金所（元/点）
    "IF": 300.0,
    "IH": 300.0,
    "IC": 200.0,
    "IM": 200.0,
    # 广期所
    "SI": 5.0,
    "LC": 1.0,
}


def contract_multiplier(symbol: str) -> float:
    """返回品种合约乘数；未知品种 1.0。支持主连后缀（AU0 -> AU）。"""
    return CONTRACT_MULTIPLIERS.get(_strip_contract_suffix(symbol), 1.0)


def _strip_contract_suffix(symbol: str) -> str:
    """剥离合约数字后缀得到品种代码（RB2610 -> RB，AU0 -> AU）。"""
    return re.sub(r"\d+$", "", symbol.upper())


def infer_market(symbol: str, default: str = "futures") -> str:
    """按代码形态推断市场：期货（字母+数字，如 RB0 / RB2610）/ 股票（6 位数字）。

    Args:
        symbol: 标的代码
        default: 无法推断时的默认市场（默认 "futures"）

    Returns:
        市场名（"futures" / "stock"）。ETF（6 位数字）归入 stock，
        如需区分显式传入 market 参数。
    """
    s = symbol.strip()
    if s.isdigit() and len(s) == 6:
        return "stock"
    return default


__all__ = [
    "SimPosition",
    "SimAccount",
    "SimDailyRecord",
    "SimFill",
    "SimApplyResult",
    "ReplayResult",
    "CONTRACT_MULTIPLIERS",
    "contract_multiplier",
    "infer_market",
]
