"""
tests/scenarios/natural_experiments.py — 自然实验事件定义。

HARNESS §11-logic-review-plan.md §C.1:
    定义历史上的"自然实验"事件，用于因果结构审查。

事件类型:
    - circuit_breaker: 熔断日（市场机制突变）
    - limit_move: 涨跌停板打开日（价格发现恢复）
    - contract_switch: 主力合约切换日（换月效应）
    - policy_shock: 重大政策冲击日（外部干预）

版本: v1.0.0
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Optional


# ─── 自然实验事件类型 ──────────────────────────────────────

EventType = Literal[
    "circuit_breaker",    # 熔断
    "limit_move",         # 涨跌停板打开
    "contract_switch",    # 主力合约切换
    "policy_shock",       # 政策冲击
    "gap_open",           # 跳空开盘
    "volume_spike",       # 成交量异常放大
]


@dataclass
class NaturalExperiment:
    """自然实验事件。

    Attributes:
        event_id: 事件唯一标识
        event_type: 事件类型
        event_date: 事件发生日期
        symbol: 品种/合约代码（空 = 全市场事件）
        name: 事件名称（人类可读）
        expected_direction: 预期影响方向（"positive" / "negative" / "unknown"）
        description: 事件描述
        pre_window: 事件前窗口（交易日数，默认 5）
        post_window: 事件后窗口（交易日数，默认 5）
    """
    event_id: str
    event_type: EventType
    event_date: date
    symbol: str
    name: str
    expected_direction: Literal["positive", "negative", "unknown"]
    description: str
    pre_window: int = 5
    post_window: int = 5


# ─── 预定义事件库 ──────────────────────────────────────────

# A 股历史自然实验事件
A_SHARE_EVENTS: list[NaturalExperiment] = [
    NaturalExperiment(
        event_id="cb_2015_07_08",
        event_type="circuit_breaker",
        event_date=date(2015, 7, 8),
        symbol="",
        name="2015 年股灾熔断",
        expected_direction="negative",
        description="2015 年股灾期间，大量个股熔断停牌，市场流动性枯竭",
        pre_window=10,
        post_window=10,
    ),
    NaturalExperiment(
        event_id="cb_2016_01_04",
        event_type="circuit_breaker",
        event_date=date(2016, 1, 4),
        symbol="",
        name="2016 年首次熔断",
        expected_direction="negative",
        description="2016 年 1 月 4 日，A 股首次触发熔断机制，当日提前收盘",
        pre_window=5,
        post_window=5,
    ),
    NaturalExperiment(
        event_id="cb_2016_01_07",
        event_type="circuit_breaker",
        event_date=date(2016, 1, 7),
        symbol="",
        name="2016 年第二次熔断",
        expected_direction="negative",
        description="2016 年 1 月 7 日，A 股再次触发熔断，熔断机制随后被暂停",
        pre_window=5,
        post_window=5,
    ),
    NaturalExperiment(
        event_id="policy_2024_09_24",
        event_type="policy_shock",
        event_date=date(2024, 9, 24),
        symbol="",
        name="2024 年 924 政策组合拳",
        expected_direction="positive",
        description="2024 年 9 月 24 日，央行+证监会+金融监管总局联合发布多项重磅政策",
        pre_window=10,
        post_window=10,
    ),
]

# 期货自然实验事件
FUTURES_EVENTS: list[NaturalExperiment] = [
    NaturalExperiment(
        event_id="iron_ore_2023_06",
        event_type="policy_shock",
        event_date=date(2023, 6, 1),
        symbol="I0",
        name="铁矿石政策调控",
        expected_direction="negative",
        description="2023 年 6 月，发改委对铁矿石价格进行政策干预",
        pre_window=5,
        post_window=5,
    ),
    NaturalExperiment(
        event_id="oil_crash_2020_04",
        event_type="limit_move",
        event_date=date(2020, 4, 20),
        symbol="SC0",
        name="原油期货暴跌",
        expected_direction="negative",
        description="2020 年 4 月 20 日，WTI 原油期货跌至负值，SC 跟随暴跌",
        pre_window=10,
        post_window=10,
    ),
]

# 合并事件库
DEFAULT_EVENTS: list[NaturalExperiment] = A_SHARE_EVENTS + FUTURES_EVENTS


# ─── 事件查找工具 ──────────────────────────────────────────

def get_events_by_type(
    events: list[NaturalExperiment],
    event_type: EventType,
) -> list[NaturalExperiment]:
    """按事件类型过滤事件。"""
    return [e for e in events if e.event_type == event_type]


def get_events_for_symbol(
    events: list[NaturalExperiment],
    symbol: str,
) -> list[NaturalExperiment]:
    """按品种过滤事件（空 symbol = 全市场事件）。"""
    return [e for e in events if e.symbol == symbol or e.symbol == ""]


def get_event_by_id(
    events: list[NaturalExperiment],
    event_id: str,
) -> Optional[NaturalExperiment]:
    """按 event_id 查找事件。"""
    for e in events:
        if e.event_id == event_id:
            return e
    return None


__all__ = [
    "EventType",
    "NaturalExperiment",
    "A_SHARE_EVENTS",
    "FUTURES_EVENTS",
    "DEFAULT_EVENTS",
    "get_events_by_type",
    "get_events_for_symbol",
    "get_event_by_id",
]