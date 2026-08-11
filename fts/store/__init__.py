"""
fts/store — 数据持久化统一访问层（Storage Layer）

HARNESS §文档先行: 本层为 plans/29 存储收敛计划的 Phase 0 基建，提供
存储域注册表（storage_landscape.yaml 契约加载）与统一路由入口，
供后续 Phase 1~4 的迁移与落库消费。

职责边界:
    - 登记每个数据域的权威存储后端 / 路径 / 保留策略（SSOT 单一事实源）
    - 不直接读写业务数据（行情/因子/状态读写仍走既有模块）
    - 存量数据零变更（Phase 0 纯加固）
"""

from .registry import (
    StorageBackend,
    StorageDomain,
    StorageRegistry,
    load_storage_landscape,
)
from .state_db import DEFAULT_STATE_DB, StateKVStore

__all__ = [
    "StorageBackend",
    "StorageDomain",
    "StorageRegistry",
    "load_storage_landscape",
    "StateKVStore",
    "DEFAULT_STATE_DB",
]
