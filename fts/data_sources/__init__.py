"""fts.data_sources — 期货数据适配器子包（v2.3.0）。

K 线主路径（v3.0.0+1 起唯一数据源 QuantData）:
    DUCKDB_CACHE(读取缓存) → QUANTDATA → SYNTHETIC(测试/离线兜底)
    天勤（TQSDK/TQ_PYTHON）、通达信实时（TDX_LOCAL）、AKShare 即时抓取
    已从默认链移除——FTS 因子生命周期管理仅依赖 QuantData 不同周期数据。

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
