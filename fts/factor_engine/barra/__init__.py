"""
fts.factor_engine.barra — Barra 风格因子体系（GAP-S02）。

子模块:
    barra_style:        10 大风格因子截面暴露计算（Barra CNE6 简化版）
    barra_neutralizer:  多因子横截面回归残差（风格 + 行业双重中性化）

版本: v1.0.0（GAP-S02）
"""

from .barra_neutralizer import barra_neutralize_matrix
from .barra_style import (
    STYLE_FACTOR_NAMES,
    STYLE_SPECS,
    BarraStyleEngine,
    StyleFactorSpec,
)

__all__ = [
    "STYLE_FACTOR_NAMES",
    "STYLE_SPECS",
    "StyleFactorSpec",
    "BarraStyleEngine",
    "barra_neutralize_matrix",
]
