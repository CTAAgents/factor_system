"""
fts.strategies — FTS 策略层。

从 FDT skills/quant-daily/scripts/strategies/ 迁移而来。
保留 v2 策略可插拔框架（BaseStrategyV2）和 MultiFactorStrategy，
新增策略进化模块（RegimeAdaptiveStrategy / DynamicWeightStrategy / MultiPeriodSignalFusion）。

模块:
    - base_v2: v2 策略可插拔框架（compute → filter → score 三段式）
    - multi_factor_strategy: 四维因子加权打分策略
    - strategy_evolution: 策略进化（制度自适应 / 动态权重 / 多周期融合）
    - rules: 策略规则知识库（占位，待填充）

版本: v1.2.0
"""

from .base_v2 import (
    BaseStrategyV2,
    RawSignal,
    ScoredSignal,
    StrategyV1Adapter,
    format_reason,
)
from .multi_factor_strategy import (
    MultiFactorStrategy,
    FACTOR_WEIGHTS,
    PURE_MOMENTUM_WEIGHTS,
    SCORE_THRESHOLDS,
)
from .strategy_evolution import (
    RegimeAdaptiveStrategy,
    DynamicWeightStrategy,
    MultiPeriodSignalFusion,
    REGIME_WEIGHT_MAP,
    BULL_WEIGHTS,
    BEAR_WEIGHTS,
    HIGH_VOL_WEIGHTS,
    LOW_VOL_WEIGHTS,
    OSCILLATE_WEIGHTS,
)

__version__ = "0.2.0"
__all__ = [
    # v2 基类
    "BaseStrategyV2",
    "RawSignal",
    "ScoredSignal",
    "StrategyV1Adapter",
    "format_reason",
    # 多因子策略
    "MultiFactorStrategy",
    "FACTOR_WEIGHTS",
    "PURE_MOMENTUM_WEIGHTS",
    "SCORE_THRESHOLDS",
    # 策略进化
    "RegimeAdaptiveStrategy",
    "DynamicWeightStrategy",
    "MultiPeriodSignalFusion",
    "REGIME_WEIGHT_MAP",
    "BULL_WEIGHTS",
    "BEAR_WEIGHTS",
    "HIGH_VOL_WEIGHTS",
    "LOW_VOL_WEIGHTS",
    "OSCILLATE_WEIGHTS",
]
