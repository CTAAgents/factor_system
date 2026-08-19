"""存储域注册表测试（plans/29 Phase 0 基建验收）。"""

from __future__ import annotations

import pytest

from fts.store import StorageBackend, StorageDomain, StorageRegistry, load_storage_landscape


@pytest.fixture()
def registry() -> StorageRegistry:
    return StorageRegistry()


class TestStorageRegistry:
    def test_load_default_landscape(self, registry: StorageRegistry) -> None:
        """默认 YAML 契约加载成功且覆盖全部存储域。"""
        domains = registry.domains()
        assert len(domains) >= 8
        ids = {d.domain for d in domains}
        # 32-stock-extraction-plan.md P3: market_history 域拆为 futures/stock 两域
        for required in (
            "market_history_futures",
            "market_history_stock",
            "factor_assets",
            "elite_snapshots",
            "run_state",
            "signal_cache",
        ):
            assert required in ids

    def test_required_fields_present(self, registry: StorageRegistry) -> None:
        """每个域均具备契约必填字段。"""
        for d in registry.domains():
            assert d.domain
            assert d.description
            assert isinstance(d.backend, StorageBackend)
            assert d.path

    def test_get_known_domain(self, registry: StorageRegistry) -> None:
        domain = registry.get("factor_assets")
        assert domain.backend == StorageBackend.DUCKDB
        assert "factor_catalog" in domain.tables

    def test_get_unknown_domain_raises(self, registry: StorageRegistry) -> None:
        with pytest.raises(KeyError):
            registry.get("not_a_domain")

    def test_contract_validation_passes(self, registry: StorageRegistry) -> None:
        """随库 YAML 契约校验无违规项。"""
        assert registry.validate_contract() == []

    def test_legacy_requires_migrated_to(self, registry: StorageRegistry) -> None:
        """legacy 域必须声明迁移去向（SSOT 收敛契约）。"""
        for d in registry.domains():
            if d.status == "legacy":
                assert d.migrated_to, f"{d.domain} 缺少 migrated_to"

    def test_planned_requires_migrated_from(self, registry: StorageRegistry) -> None:
        for d in registry.domains():
            if d.status == "planned":
                assert d.migrated_from, f"{d.domain} 缺少 migrated_from"

    def test_no_absolute_paths(self, registry: StorageRegistry) -> None:
        """path 一律相对路径（禁止硬编码绝对路径）。"""
        for d in registry.domains():
            assert not d.path.startswith("/"), f"{d.domain} path 为绝对路径: {d.path}"

    def test_summary_shape(self, registry: StorageRegistry) -> None:
        summary = registry.summary()
        assert summary["total"] == len(registry.domains())
        assert summary["version"]
        assert "duckdb" in summary["by_backend"]
        assert summary["by_status"].get("active", 0) >= 3


class TestStorageDomain:
    def test_from_dict_with_defaults(self) -> None:
        d = StorageDomain.from_dict({"domain": "x", "description": "d", "backend": "duckdb", "path": "data/x.duckdb"})
        assert d.status == "active"
        assert d.tables == ()
        assert d.partition_key == ""

    def test_from_dict_invalid_backend(self) -> None:
        with pytest.raises(ValueError):
            StorageDomain.from_dict({"domain": "x", "description": "d", "backend": "oracle", "path": "data/x"})

    def test_missing_landscape_returns_empty(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("FTS_STORAGE_LANDSCAPE_PATH", str(tmp_path / "none.yaml"))
        reg = load_storage_landscape()
        assert reg.domains() == []
        assert reg.validate_contract() == []

    def test_env_path_override(self, tmp_path, monkeypatch) -> None:
        import yaml

        p = tmp_path / "custom.yaml"
        p.write_text(
            yaml.safe_dump(
                {
                    "version": "t",
                    "domains": [{"domain": "custom", "description": "c", "backend": "parquet", "path": "data/c"}],
                },
                allow_unicode=True,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("FTS_STORAGE_LANDSCAPE_PATH", str(p))
        reg = load_storage_landscape()
        assert reg.get("custom").backend == StorageBackend.PARQUET


class TestWritePathContract:
    """写路径契约（GAP-150，v2.105.0+18）：find_by_path 反查 + 未登记告警。"""

    def test_find_by_path_matches_factor_assets_template(self, registry: StorageRegistry) -> None:
        """模板路径匹配：factor_catalog_energy.duckdb 落在 factor_assets 域。"""
        d = registry.find_by_path("data/factor_catalog_energy.duckdb")
        assert d is not None
        assert d.domain == "factor_assets"

    def test_find_by_path_template_stock_futures(self, registry: StorageRegistry) -> None:
        d = registry.find_by_path("data/factor_catalog_futures.duckdb")
        assert d is not None
        assert d.domain == "factor_assets"

    def test_find_by_path_registered_concrete(self, registry: StorageRegistry) -> None:
        """非模板域精确前缀匹配（run_state / state.db）。"""
        d = registry.find_by_path("data/state.db")
        assert d is not None
        assert d.domain == "run_state"

    def test_find_by_path_unregistered_returns_none(self, registry: StorageRegistry, tmp_path) -> None:
        """项目根外路径（测试 tmp）→ 未登记（显式注入豁免）。"""
        assert registry.find_by_path(tmp_path / "x.duckdb") is None

    def test_warn_unregistered_write_logs(self, registry: StorageRegistry, tmp_path, caplog) -> None:
        """未登记写路径 → warning（告警模式，不抛错）。"""
        with caplog.at_level("WARNING", logger="fts.store.registry"):
            d = registry.warn_unregistered_write(tmp_path / "y.db", caller="Test")
        assert d is None
        assert any("写路径未登记" in r.message for r in caplog.records)

    def test_warn_registered_no_log(self, registry: StorageRegistry, caplog) -> None:
        """已登记写路径 → 无告警，返回匹配域。"""
        with caplog.at_level("WARNING", logger="fts.store.registry"):
            d = registry.warn_unregistered_write("data/factor_catalog_energy.duckdb")
        assert d is not None
        assert not any("写路径未登记" in r.message for r in caplog.records)

    def test_strict_unregistered_raises(self, registry: StorageRegistry, tmp_path) -> None:
        """严格模式（strict=True）：未登记写路径 → ValueError 阻断。"""
        with pytest.raises(ValueError, match="写路径未登记"):
            registry.warn_unregistered_write(tmp_path / "z.db", caller="Test", strict=True)

    def test_strict_registered_ok(self, registry: StorageRegistry) -> None:
        """严格模式：已登记路径 → 不抛，返回匹配域。"""
        d = registry.warn_unregistered_write("data/factor_catalog_futures.duckdb", strict=True)
        assert d is not None
        assert d.domain == "factor_assets"
