"""
fts.factor_engine.seed_data — 外部种子因子数据源

提供 WQ 101 Alpha、Qlib 158、国泰君安 191 Alpha、基本面/另类/宏观因子
和聚宽(JoinQuant)因子作为 FTS 种子因子。通过 loader 模块转换为 FactorProgram 后注入 SeedPool。

版本: v1.3.0（新增聚宽 JQ 因子种子加载）
"""

from .loader import (
    load_wq101_seeds,
    load_qlib158_seeds,
    load_gtja191_seeds,
    load_fundamental_seeds,
    load_jq_seeds,
    load_all_external_seeds,
    get_external_seed_count,
)

__all__ = [
    "load_wq101_seeds",
    "load_qlib158_seeds",
    "load_gtja191_seeds",
    "load_fundamental_seeds",
    "load_jq_seeds",
    "load_all_external_seeds",
    "get_external_seed_count",
]
