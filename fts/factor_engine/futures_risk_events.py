"""
fts.factor_engine.futures_risk_events — 期货特有风险场景事件处理（CTA 手册阶段8）。

对照《期货CTA多因子策略标准化作业手册》阶段8.2/8.4:
    - 盘中风控: 组合保证金占用/权益 > 70% → 自动触发降仓（非日终）
    - 交易所临时提保/限仓: 新保证金下超限 → 按比例减仓至合规
    - 极端行情熔断: 熔断时段该品种不可成交（暂停交易，不追单）
    - 主力合约切换异常: 换月价差异常（>3% 日波动）→ 延迟移仓 1-2 日；
      5 日内未收敛 → 强制移仓并标记异常成本

设计约束:
    - 纯函数，零未来函数（仅用当前输入）
    - 输出降仓/暂停/移仓建议，执行交由下游（FDT）

版本: v1.0.0
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 手册阈值
MARGIN_USAGE_ALERT = 0.70  # 盘中保证金占用/权益 > 70% 触发降仓
ROLL_SPREAD_ANOMALY = 0.03  # 换月价差 > 3% 视为异常
ROLL_FORCE_DAYS = 5  # 异常价差 5 日内未收敛 → 强制移仓


def margin_usage_reduce_position(
    margin_used: float,
    equity: float,
    threshold: float = MARGIN_USAGE_ALERT,
) -> dict:
    """盘中保证金占用降仓建议（手册阶段8.2：>70% 自动触发降仓，非日终）。

    Args:
        margin_used: 当前组合保证金占用
        equity: 总权益
        threshold: 降仓触发阈值（默认 0.7）

    Returns:
        dict: {
            margin_usage: 当前占用比例,
            triggered: 是否触发降仓,
            reduce_to_ratio: 目标占用比例（不触发时=当前）,
            reduce_factor: 减仓因子（目标/当前，<1 需减仓）,
        }
    """
    if equity <= 0:
        return {"margin_usage": 0.0, "triggered": False, "reduce_to_ratio": 0.0, "reduce_factor": 1.0}
    usage = margin_used / equity
    triggered = usage > threshold
    reduce_factor = threshold / usage if triggered else 1.0
    return {
        "margin_usage": usage,
        "triggered": triggered,
        "reduce_to_ratio": threshold if triggered else usage,
        "reduce_factor": reduce_factor,
    }


def margin_increase_reduce(
    margin_used: float,
    equity: float,
    old_margin_rate: float,
    new_margin_rate: float,
    max_margin_usage: float = MARGIN_USAGE_ALERT,
) -> dict:
    """交易所临时提保：新保证金下超限 → 按比例减仓至合规（手册阶段8.4）。

    Args:
        margin_used: 提保前组合保证金占用
        equity: 总权益
        old_margin_rate: 原保证金比例
        new_margin_rate: 提保后保证金比例（> old 为加保）
        max_margin_usage: 最大保证金占用比例（默认 0.7）

    Returns:
        dict: {
            old_usage, new_usage: 提保前后占用比例,
            exceeded: 是否超限,
            reduce_factor: 减仓因子（<1 需减仓）,
            required: 是否要求减仓,
        }
    """
    if equity <= 0 or old_margin_rate <= 0:
        return {"old_usage": 0.0, "new_usage": 0.0, "exceeded": False, "reduce_factor": 1.0, "required": False}
    old_usage = margin_used / equity
    ratio = new_margin_rate / old_margin_rate
    new_usage = old_usage * ratio
    exceeded = new_usage > max_margin_usage
    reduce_factor = max_margin_usage / new_usage if exceeded else 1.0
    return {
        "old_usage": old_usage,
        "new_usage": new_usage,
        "exceeded": exceeded,
        "reduce_factor": reduce_factor,
        "required": exceeded,
    }


def circuit_breaker_block(
    is_circuit_breaker: bool,
    current_position: int = 0,
) -> dict:
    """极端行情熔断：暂停该品种交易，熔断解除后重新评估信号（手册阶段8.4）。

    Args:
        is_circuit_breaker: 该品种是否处于熔断状态
        current_position: 当前持仓方向（+1/-1/0）

    Returns:
        dict: {
            tradable: 是否可交易（熔断中 False）,
            pause_new: 是否暂停新开仓（熔断中 True）,
            hold_existing: 是否允许持有现有持仓,
        }
    """
    return {
        "tradable": not is_circuit_breaker,
        "pause_new": is_circuit_breaker,
        "hold_existing": True,  # 熔断期间允许持有，不强制平仓（恢复后评估）
    }


def roll_anomaly_delay(
    spread_ratio: float,
    wait_days: int = 0,
    anomaly_threshold: float = ROLL_SPREAD_ANOMALY,
    force_days: int = ROLL_FORCE_DAYS,
) -> dict:
    """主力切换异常：换月价差异常（>3%）→ 延迟移仓；5 日未收敛强制移仓（手册阶段8.4）。

    Args:
        spread_ratio: 换月价差比例（|new_close/old_close - 1|）
        wait_days: 已等待延迟天数
        anomaly_threshold: 异常价差阈值（默认 3%）
        force_days: 强制移仓等待上限（默认 5 日）

    Returns:
        dict: {
            anomalous: 价差是否异常,
            delay_recommended: 是否建议延迟移仓,
            force_roll: 是否强制移仓（等待超限）,
            abnormal_cost: 是否标记异常成本,
            next_wait_days: 下次等待天数,
        }
    """
    anomalous = spread_ratio > anomaly_threshold
    if not anomalous:
        return {
            "anomalous": False,
            "delay_recommended": False,
            "force_roll": False,
            "abnormal_cost": False,
            "next_wait_days": 0,
        }
    force_roll = wait_days >= force_days - 1  # 已达等待上限 → 强制移仓
    return {
        "anomalous": True,
        "delay_recommended": not force_roll,
        "force_roll": force_roll,
        "abnormal_cost": force_roll,  # 强制移仓标记异常成本
        "next_wait_days": wait_days + 1 if not force_roll else wait_days,
    }


__all__ = [
    "MARGIN_USAGE_ALERT",
    "ROLL_SPREAD_ANOMALY",
    "ROLL_FORCE_DAYS",
    "margin_usage_reduce_position",
    "margin_increase_reduce",
    "circuit_breaker_block",
    "roll_anomaly_delay",
]
