"""
fts.config — FTS 全局配置模块。

遵循 HARNESS §配置管理: 配置分层加载（YAML → 环境变量 → 默认值）。
"""

from __future__ import annotations

from .settings import FTSConfig, get_config, load_config, DEFAULT_MEMORY_DIR, DEFAULT_ELITE_DIR
from .factor_quality_card_config import (
    DimensionWeights,
    GradeThresholds,
    FactorQualityCardFullConfig,
    get_quality_card_config,
    create_config,
    get_conservative_config,
    get_aggressive_config,
    get_permissive_config,
)

__all__ = [
    "FTSConfig",
    "get_config",
    "load_config",
    "DEFAULT_MEMORY_DIR",
    "DEFAULT_ELITE_DIR",
    "DimensionWeights",
    "GradeThresholds",
    "FactorQualityCardFullConfig",
    "get_quality_card_config",
    "create_config",
    "get_conservative_config",
    "get_aggressive_config",
    "get_permissive_config",
]
