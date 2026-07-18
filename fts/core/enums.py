"""
fts.core.enums — FTS 特有枚举

注: DataType / MarketType / SourceGrade 由 datacore.models.enums 提供，
    FTS 直接导入使用，不重复定义。

HARNESS §契约优先：枚举变更必须 bump 版本号。
"""

from __future__ import annotations

from enum import Enum


class EvolutionStage(str, Enum):
    """因子演化阶段标识。"""
    L0_HUMAN = "l0_human"              # 人类设定
    L1_META_LOOP = "l1_meta_loop"      # L1 元循环（市场感知）
    L2_EVOLUTION = "l2_evolution"      # L2 演化循环（因子进化）
    L3_PORTFOLIO = "l3_portfolio"      # L3 组合循环（组合构建）


class FactorPriority(str, Enum):
    """因子优先级（基于 L1 debate_gap + 经济逻辑）。"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FactorStatus(str, Enum):
    """因子在种子池中的状态。"""
    PENDING = "pending"        # 待注入
    INJECTED = "injected"      # 已注入
    DECAYED = "decayed"        # 已衰减
    REJECTED = "rejected"      # 已拒绝


__all__ = ["EvolutionStage", "FactorPriority", "FactorStatus"]
