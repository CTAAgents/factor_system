"""
fts.factor_engine.seed_data — 外部种子因子数据源

提供 WQ 101 Alpha、Qlib 158 和国泰君安 191 Alpha 因子作为 FTS 种子因子。
通过 loader 模块转换为 FactorProgram 后注入 SeedPool。

版本: v1.1.0（与 FTS 同步）
"""

from .loader import (
    load_wq101_seeds,
    load_qlib158_seeds,
    load_gtja191_seeds,
    load_all_external_seeds,
    get_external_seed_count,
)

__all__ = [
    "load_wq101_seeds",
    "load_qlib158_seeds",
    "load_gtja191_seeds",
    "load_all_external_seeds",
    "get_external_seed_count",
]