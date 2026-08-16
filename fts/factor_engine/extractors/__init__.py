"""
fts/factor_engine/extractors/ — 因子提取器管道

提供三源提取器管道，用于从多个数据源自动提取因子候选，
集成到 L1 Meta-Loop 的知识注入流程。

提取器类型:
    - FuturesExtractorPipeline: 期货三源提取器（天软/券商研报/学术论文）

用法:
    pipeline = FuturesExtractorPipeline()
    candidates = pipeline.extract(trace_id="l1_xxx")
"""

from __future__ import annotations

from .base import BaseExtractor, BaseExtractorPipeline
from .futures_pipeline import FuturesExtractorPipeline
from .source_discovery import DiscoveryRecord, DiscoveryStore, SourceDiscoverer
from .source_registry import SourceInfo, SourceProber, SourceRegistry, is_probe_acceptable

__all__ = [
    "BaseExtractor",
    "BaseExtractorPipeline",
    "FuturesExtractorPipeline",
    "SourceRegistry",
    "SourceInfo",
    "SourceProber",
    "is_probe_acceptable",
    "SourceDiscoverer",
    "DiscoveryStore",
    "DiscoveryRecord",
]
