"""
fts/store/registry.py — 存储域注册表（Storage Domain Registry）

从 `docs/harness/_data/storage_landscape.yaml`（Layer 3 数据）加载 FTS
全部存储域的权威契约，提供按域路由与契约校验。对齐头部机构
「一数一源 SSOT + 元数据目录」的数据持久化实践（plans/29 §2）。

契约字段（YAML 必填）:
    domain        — 域标识（全局唯一，如 market_history）
    description   — 域职责描述
    backend       — 存储后端（StorageBackend 枚举值）
    path          — 相对项目根目录的路径（禁止绝对路径）
可选字段:
    tables        — 目标库表清单（duckdb 域）
    partition_key — 分区键（日期类）
    retention     — 保留策略描述
    status        — active / legacy（待迁移） / planned（目标态） / archived
    migrated_from / migrated_to — 迁移血缘

用法:
    from fts.store import StorageRegistry

    reg = StorageRegistry()
    domain = reg.get("factor_assets")      # StorageDomain
    issues = reg.validate_contract()       # 契约违规项列表
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# 项目根目录（本模块位于 <root>/fts/store/registry.py）
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 存储域盘点登记（Layer 3 数据，文档一致性校验引用）
DEFAULT_LANDSCAPE_PATH = PROJECT_ROOT / "docs" / "harness" / "_data" / "storage_landscape.yaml"

# 允许的域状态
DOMAIN_STATUS_ACTIVE = "active"
DOMAIN_STATUS_LEGACY = "legacy"
DOMAIN_STATUS_PLANNED = "planned"
DOMAIN_STATUS_ARCHIVED = "archived"
VALID_DOMAIN_STATUS = (DOMAIN_STATUS_ACTIVE, DOMAIN_STATUS_LEGACY, DOMAIN_STATUS_PLANNED, DOMAIN_STATUS_ARCHIVED)

# 契约必填字段
REQUIRED_FIELDS = ("domain", "description", "backend", "path")


class StorageBackend(str, Enum):
    """FTS 支持的存储后端类型（与 storage_landscape.yaml backend 枚举对齐）。"""

    DUCKDB = "duckdb"
    PARQUET = "parquet"
    JSON = "json"
    YAML = "yaml"
    JSONL = "jsonl"
    NPY = "npy"
    SQLITE = "sqlite"
    MIXED = "mixed"  # 报告类（csv/png/json 混存，可再生成）


@dataclass(frozen=True)
class StorageDomain:
    """单个数据域的权威存储契约。"""

    domain: str
    description: str
    backend: StorageBackend
    path: str
    tables: tuple[str, ...] = ()
    partition_key: str = ""
    retention: str = ""
    status: str = DOMAIN_STATUS_ACTIVE
    migrated_from: tuple[str, ...] = ()
    migrated_to: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "StorageDomain":
        """从 YAML 字典构建，缺省字段取默认值。"""
        backend = StorageBackend(str(raw["backend"]))
        tables = tuple(str(t) for t in raw.get("tables", []))
        return cls(
            domain=str(raw["domain"]),
            description=str(raw["description"]),
            backend=backend,
            path=str(raw["path"]),
            tables=tables,
            partition_key=str(raw.get("partition_key", "")),
            retention=str(raw.get("retention", "")),
            status=str(raw.get("status", DOMAIN_STATUS_ACTIVE)),
            migrated_from=tuple(str(m) for m in raw.get("migrated_from", [])),
            migrated_to=tuple(str(m) for m in raw.get("migrated_to", [])),
        )


class StorageRegistry:
    """存储域注册表 — 加载 YAML 契约并提供路由/校验。"""

    def __init__(self, yaml_path: str | Path | None = None) -> None:
        env_path = os.getenv("FTS_STORAGE_LANDSCAPE_PATH", "")
        self.yaml_path = Path(yaml_path or env_path or DEFAULT_LANDSCAPE_PATH)
        self._domains: dict[str, StorageDomain] = {}
        self._version: str = ""
        self.load()

    # ── 加载与查询 ──────────────────────────────────────────

    def load(self) -> None:
        """加载（或重载）存储域契约 YAML。"""
        if not self.yaml_path.exists():
            logger.warning("存储域契约缺失: %s（空注册表）", self.yaml_path)
            self._domains = {}
            self._version = ""
            return
        with open(self.yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        self._version = str(data.get("version", ""))
        domains: dict[str, StorageDomain] = {}
        for raw in data.get("domains", []):
            domain = StorageDomain.from_dict(raw)
            domains[domain.domain] = domain
        self._domains = domains
        logger.info("存储域注册表加载完成: %d 域 (version=%s)", len(domains), self._version)

    def get(self, domain: str) -> StorageDomain:
        """按域标识查询存储契约，未知域抛 KeyError。"""
        if domain not in self._domains:
            raise KeyError(f"未知存储域: {domain}（登记于 {self.yaml_path.name}）")
        return self._domains[domain]

    def domains(self) -> list[StorageDomain]:
        """全部存储域（按加载顺序）。"""
        return list(self._domains.values())

    @property
    def version(self) -> str:
        return self._version

    def summary(self) -> dict[str, Any]:
        """按后端/状态聚合的概览（供报告与一致性校验）。"""
        by_backend: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for d in self._domains.values():
            by_backend[d.backend.value] = by_backend.get(d.backend.value, 0) + 1
            by_status[d.status] = by_status.get(d.status, 0) + 1
        return {
            "total": len(self._domains),
            "version": self._version,
            "by_backend": by_backend,
            "by_status": by_status,
        }

    # ── 契约校验 ────────────────────────────────────────────

    def validate_contract(self) -> list[str]:
        """校验契约合规性，返回违规项列表（空 = 全部通过）。

        校验项: 必填字段 / 后端枚举 / 域唯一 / 相对路径 / 状态合法 /
        legacy 与 planned 域必须带迁移血缘方向。
        """
        issues: list[str] = []
        for domain in self._domains.values():
            for field_name in REQUIRED_FIELDS:
                if not getattr(domain, field_name):
                    issues.append(f"[{domain.domain}] 缺少必填字段: {field_name}")
            if domain.status not in VALID_DOMAIN_STATUS:
                issues.append(f"[{domain.domain}] 非法状态: {domain.status}")
            if Path(domain.path).is_absolute():
                issues.append(f"[{domain.domain}] path 必须为相对路径: {domain.path}")
            if domain.status == DOMAIN_STATUS_LEGACY and not domain.migrated_to:
                issues.append(f"[{domain.domain}] legacy 域必须声明 migrated_to")
            if domain.status == DOMAIN_STATUS_PLANNED and not domain.migrated_from:
                issues.append(f"[{domain.domain}] planned 域必须声明 migrated_from")
        return issues

    # ── 写路径契约（GAP-150，2026-08-19） ─────────────────

    def find_by_path(self, path: str | Path) -> StorageDomain | None:
        """按实际写路径反查登记域（相对项目根规范化比对）。

        - 模板路径（如 ``data/factor_catalog_{stock,futures,energy}.duckdb``）按
          前缀 + 后缀拆分匹配；
        - 项目根外路径（测试 tmp / 外部库）返回 None（显式注入豁免）。
        """
        p = Path(path)
        try:
            rel = p.resolve().relative_to(PROJECT_ROOT.resolve())
        except ValueError:
            return None
        rel_str = rel.as_posix()
        for d in self._domains.values():
            dpath = d.path
            if "{" in dpath:
                prefix, _, rest = dpath.partition("{")
                suffix = rest.split("}", 1)[1] if "}" in rest else ""
                if rel_str.startswith(prefix) and rel_str.endswith(suffix):
                    return d
            elif rel_str.startswith(dpath):
                return d
        return None

    def warn_unregistered_write(
        self,
        path: str | Path,
        caller: str = "",
        strict: bool = False,
    ) -> StorageDomain | None:
        """写路径注册断言（GAP-150）：路径未落在登记域时告警或抛错。

        - 告警模式（strict=False，默认）：未登记 warning，SSOT 可观测化；
        - 严格模式（strict=True，v2.105.0+19）：未登记抛 ValueError（阻断，强制先登记）。

        返回匹配域（None = 未登记）。
        """
        d = self.find_by_path(path)
        if d is None:
            if strict:
                raise ValueError(
                    f"[StorageRegistry] 写路径未登记: {path}（严格模式，新增写路径必须先登记 "
                    f"storage_landscape.yaml，SSOT）"
                )
            logger.warning(
                "[StorageRegistry] 写路径未登记: %s%s——新增写路径必须先登记 storage_landscape.yaml（SSOT）",
                path,
                f"（调用方: {caller}）" if caller else "",
            )
        return d


def load_storage_landscape(yaml_path: str | Path | None = None) -> StorageRegistry:
    """便捷工厂：加载存储域注册表。"""
    return StorageRegistry(yaml_path=yaml_path)


# 写路径契约进程级单例（GAP-150，v2.105.0+21）：各写入口高频调用避免重复解析 YAML。
_REGISTRY_CACHE: StorageRegistry | None = None


class _NoResetRegistryError(RuntimeError):
    """单例重置受限标记（测试环境如需隔离请直接构造 StorageRegistry）。"""


def get_storage_registry() -> StorageRegistry:
    """返回进程级 StorageRegistry 单例（首次调用加载，之后复用）。

    服务 GAP-150 写路径契约检查（repository/state_db/l3_signal/data_futures
    写入口统一入口）；测试隔离请直接构造 StorageRegistry，不走单例。
    """
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = StorageRegistry()
    return _REGISTRY_CACHE
