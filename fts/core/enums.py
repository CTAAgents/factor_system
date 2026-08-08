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


class DataSource(str, Enum):
    """FTS 数据源枚举 — 7 个成员。

    对应多源数据融合策略中的源标识符。
    """
    DUCKDB_CACHE = "DUCKDB_CACHE"    # DuckDB 本地缓存（主路径 Top1）
    TQ_LOCAL = "TQ_LOCAL"            # 通达信本地 HTTP 7721
    TQ_PYTHON = "TQ_PYTHON"          # 通达信 TQ-Python SDK
    AKSHARE = "AKSHARE"              # AKShare 即时获取（降级）
    SYNTHETIC = "SYNTHETIC"          # 合成数据降级（保证系统可运行）
    WIND = "WIND"                    # 万得金融终端（字段增强层）
    IFIND = "IFIND"                  # 同花顺 iFinD（字段增强层）
    TQSDK = "TQSDK"                  # 天勤 TQSDK（分钟/日线数据源）
    TQSDK_TICK = "TQSDK_TICK"        # 天勤 TQSDK tick 逐笔数据源（v2.31.0）
    TDX_MINUTE = "TDX_MINUTE"        # 通达信 TQ-Local 分钟数据（端口 17709）


class FusionStrategy(str, Enum):
    """多源数据融合策略 — 5 种算法。

    对应 OHLCVFusion 融合器中的策略选择。
    """
    MEDIAN = "median"                # 中位数（默认，抗异常值）
    MEAN = "mean"                    # 算术平均
    WEIGHTED = "weighted"            # 按源权重加权平均
    HIERARCHICAL = "hierarchical"    # 优先级优先，与中位数分歧时降级
    TRIMMED_MEAN = "trimmed_mean"    # 去掉最高/最低后取均值


__all__ = ["EvolutionStage", "FactorPriority", "FactorStatus", "DataSource", "FusionStrategy"]
