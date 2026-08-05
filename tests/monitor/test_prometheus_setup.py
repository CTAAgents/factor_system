"""Tests for fts/monitor/prometheus_setup.py"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from fts.monitor.prometheus_setup import (
    generate_configs,
    validate_configs,
    DEFAULT_PROMETHEUS_CONFIG,
    DEFAULT_ALERTMANAGER_CONFIG,
    DEFAULT_ALERT_RULES,
)


@pytest.fixture()
def temp_config_dir(tmp_path: Path):
    """临时配置目录。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return str(config_dir)


class TestGenerateConfigs:
    def test_generates_all_three_files(self, temp_config_dir: str):
        """验证 generate_configs 生成所有三个配置文件。"""
        paths = generate_configs(output_dir=temp_config_dir)

        assert "prometheus" in paths
        assert "alertmanager" in paths
        assert "alerts" in paths

        for path in paths.values():
            assert Path(path).exists(), f"文件不存在: {path}"

    def test_configs_contain_fts_scrape_jobs(self, temp_config_dir: str):
        """验证 Prometheus 配置包含 FTS 抓取任务。"""
        generate_configs(output_dir=temp_config_dir)
        prom_path = Path(temp_config_dir) / "prometheus.yml"
        text = prom_path.read_text(encoding="utf-8")

        assert "fts-metrics" in text
        assert "fts-data-sources" in text
        assert "fts-health" in text
        assert "/metrics" in text
        assert "/metrics/data-sources" in text
        assert "/health" in text

    def test_configs_reference_alert_rules(self, temp_config_dir: str):
        """验证 Prometheus 配置引用告警规则文件。"""
        generate_configs(output_dir=temp_config_dir)
        prom_path = Path(temp_config_dir) / "prometheus.yml"
        text = prom_path.read_text(encoding="utf-8")

        assert "prometheus_alerts.yml" in text

    def test_configs_contain_alertmanager_target(self, temp_config_dir: str):
        """验证 AlertManager 端点配置。"""
        generate_configs(output_dir=temp_config_dir)
        prom_path = Path(temp_config_dir) / "prometheus.yml"
        text = prom_path.read_text(encoding="utf-8")

        assert "localhost:9093" in text

    def test_alert_rules_contain_fts_rules(self, temp_config_dir: str):
        """验证告警规则包含 FTS 特定规则。"""
        generate_configs(output_dir=temp_config_dir)
        alert_path = Path(temp_config_dir) / "prometheus_alerts.yml"
        text = alert_path.read_text(encoding="utf-8")

        assert "FTSDataSourceCircuitOpen" in text
        assert "FTSDataSourceSuccessRateLow" in text
        assert "FTSDataSourceDown" in text

    def test_alertmanager_config_has_receivers(self, temp_config_dir: str):
        """验证 AlertManager 配置包含接收器。"""
        generate_configs(output_dir=temp_config_dir)
        am_path = Path(temp_config_dir) / "alertmanager.yml"
        text = am_path.read_text(encoding="utf-8")

        assert "default-receiver" in text
        assert "critical-receiver" in text
        assert "webhook" in text

    def test_custom_host_and_port(self, temp_config_dir: str):
        """验证自定义 FTS 地址和端口。"""
        generate_configs(
            output_dir=temp_config_dir,
            fts_host="fts.example.com",
            fts_port=8080,
        )
        prom_path = Path(temp_config_dir) / "prometheus.yml"
        text = prom_path.read_text(encoding="utf-8")

        assert "fts.example.com:8080" in text

    def test_custom_webhook_url(self, temp_config_dir: str):
        """验证自定义 webhook URL。"""
        generate_configs(
            output_dir=temp_config_dir,
            webhook_url="https://hooks.example.com/alert",
        )
        am_path = Path(temp_config_dir) / "alertmanager.yml"
        text = am_path.read_text(encoding="utf-8")

        assert "https://hooks.example.com/alert" in text

    def test_custom_environment(self, temp_config_dir: str):
        """验证自定义环境标识。"""
        generate_configs(
            output_dir=temp_config_dir,
            environment="production",
        )
        prom_path = Path(temp_config_dir) / "prometheus.yml"
        text = prom_path.read_text(encoding="utf-8")

        assert "production" in text

    def test_creates_output_directory_if_not_exists(self, tmp_path: Path):
        """验证自动创建输出目录。"""
        output_dir = str(tmp_path / "nonexistent" / "config")
        paths = generate_configs(output_dir=output_dir)

        assert Path(output_dir).exists()
        for path in paths.values():
            assert Path(path).exists()


class TestValidateConfigs:
    def test_valid_configs_pass_validation(self, temp_config_dir: str):
        """验证生成的配置文件能通过验证。"""
        generate_configs(output_dir=temp_config_dir)
        errors = validate_configs(config_dir=temp_config_dir)

        assert len(errors) == 0, f"验证失败: {errors}"

    def test_missing_directory_fails(self, tmp_path: Path):
        """验证不存在的目录返回错误。"""
        errors = validate_configs(config_dir=str(tmp_path / "nonexistent"))

        assert len(errors) > 0
        assert "不存在" in errors[0]

    def test_missing_prometheus_file_fails(self, temp_config_dir: str):
        """验证缺少 prometheus.yml 返回错误。"""
        generate_configs(output_dir=temp_config_dir)
        os.remove(Path(temp_config_dir) / "prometheus.yml")
        errors = validate_configs(config_dir=temp_config_dir)

        assert any("prometheus.yml" in e for e in errors)

    def test_missing_alertmanager_file_fails(self, temp_config_dir: str):
        """验证缺少 alertmanager.yml 返回错误。"""
        generate_configs(output_dir=temp_config_dir)
        os.remove(Path(temp_config_dir) / "alertmanager.yml")
        errors = validate_configs(config_dir=temp_config_dir)

        assert any("alertmanager.yml" in e for e in errors)

    def test_missing_alert_rules_file_fails(self, temp_config_dir: str):
        """验证缺少 prometheus_alerts.yml 返回错误。"""
        generate_configs(output_dir=temp_config_dir)
        os.remove(Path(temp_config_dir) / "prometheus_alerts.yml")
        errors = validate_configs(config_dir=temp_config_dir)

        assert any("prometheus_alerts.yml" in e for e in errors)

    def test_invalid_yaml_fails(self, temp_config_dir: str):
        """验证格式错误的 YAML 返回错误。"""
        generate_configs(output_dir=temp_config_dir)
        bad_file = Path(temp_config_dir) / "prometheus.yml"
        bad_file.write_text(":::invalid:yaml:::", encoding="utf-8")
        errors = validate_configs(config_dir=temp_config_dir)

        assert len(errors) > 0


class TestDefaultConfigs:
    def test_default_prometheus_config_has_required_keys(self):
        """验证默认 Prometheus 配置包含必要字段。"""
        assert "scrape_configs" in DEFAULT_PROMETHEUS_CONFIG
        assert "rule_files" in DEFAULT_PROMETHEUS_CONFIG
        assert "alerting" in DEFAULT_PROMETHEUS_CONFIG
        assert "global" in DEFAULT_PROMETHEUS_CONFIG

    def test_default_alertmanager_config_has_required_keys(self):
        """验证默认 AlertManager 配置包含必要字段。"""
        assert "receivers" in DEFAULT_ALERTMANAGER_CONFIG
        assert "route" in DEFAULT_ALERTMANAGER_CONFIG
        assert "inhibit_rules" in DEFAULT_ALERTMANAGER_CONFIG

    def test_default_alert_rules_has_fts_groups(self):
        """验证默认告警规则包含 FTS 告警组。"""
        group_names = [g["name"] for g in DEFAULT_ALERT_RULES["groups"]]

        assert "fts_data_source" in group_names
        assert "fts_scheduler" in group_names
        assert "fts_factor_quality" in group_names

    def test_default_alert_rules_have_required_fields(self):
        """验证每条告警规则包含必要字段。"""
        for group in DEFAULT_ALERT_RULES["groups"]:
            for rule in group["rules"]:
                assert "alert" in rule
                assert "expr" in rule
                assert "for" in rule
                assert "labels" in rule
