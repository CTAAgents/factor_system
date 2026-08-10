"""fts.data_sources — 期货多源数据适配器子包（v2.3.0）。

K 线主路径（5 级降级）:
    DUCKDB_CACHE → TDX_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC

字段增强层（独立并行）:
    WIND  — settle / oi_change / 期权 IV/PCR
    IFIND — EDB 宏观/产业链 / 期货全字段

HARNESS §契约优先: 本子包对外接口由 BaseFuturesSource 抽象类定义。
"""

from __future__ import annotations

from fts.data_sources.aggregator import FuturesDataAggregator
from fts.data_sources.tdx_local_source import TdxLocalSource
from fts.data_sources.base import BaseFuturesSource, SourceUnavailable
from fts.data_sources.ifind_source import IFindSource
from fts.data_sources.migrate import migrate_schema
from fts.data_sources.tqsdk_source import TQSDKSource
from fts.data_sources.tqsdk_tick_source import TQSDKTickSource
from fts.data_sources.wind_source import WindSource

__all__ = [
    "BaseFuturesSource",
    "SourceUnavailable",
    "migrate_schema",
    "TdxLocalSource",
    "TQSDKSource",
    "TQSDKTickSource",
    "WindSource",
    "IFindSource",
    "FuturesDataAggregator",
]
