"""
fts.pipeline — 因子推演管线（FTS 因子计算层）。

边界（HARNESS §契约优先）:
    - 输入: Data-Core 已加工的结构化数据（通过 UnifiedDataProvider 获取）
    - 输出: 因子输入数据（如品种→时间→{score, volume, ...}）
    - LLM 是管线中的一个 stage，不是独立的消费方

模块:
    - base: FactorPipeline 抽象基类 + ProcessingStage 协议
    - factor_combiner: 多因子加权/融合器

版本: v0.1.0
"""

from .base import ProcessingStage, FactorPipeline, PipelineResult
from .factor_combiner import FactorCombiner, CombinerConfig, WeightedFactor

__version__ = "0.1.0"
__all__ = [
    "ProcessingStage",
    "FactorPipeline",
    "PipelineResult",
    "FactorCombiner",
    "CombinerConfig",
    "WeightedFactor",
]
