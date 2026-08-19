"""fts.factor_engine.imports — 外部因子常态化导入管道（v2.105.0+32）。

将 scripts/extract_* 的一次性 YAML 产物升级为带字段权威校验/去重/注入/血缘的
常态化导入管道，准入评估（Q1-Q10 + admission）由既有 L2 种子评估链保证。
"""

from .import_pipeline import (
    EXTERNAL_SEED_SOURCES,
    DEFAULT_INJECT_DIR,
    DEFAULT_POOL_PATH,
    ExternalFactorImportRunner,
    _make_candidate_id,
)

__all__ = [
    "EXTERNAL_SEED_SOURCES",
    "DEFAULT_INJECT_DIR",
    "DEFAULT_POOL_PATH",
    "ExternalFactorImportRunner",
    "_make_candidate_id",
]
