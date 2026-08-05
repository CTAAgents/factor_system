"""
tests/monitor/test_k8s_deploy.py — Kubernetes 部署清单与冲突检查测试

覆盖:
- generate_k8s_manifests: 清单生成完整性
- check_k8s_conflicts: 冲突检测逻辑
- _build_configmaps: ConfigMap 构建
- _build_deployments: Deployment 构建
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fts.monitor.k8s_deploy import (
    check_k8s_conflicts,
    generate_k8s_manifests,
    _build_configmaps,
    _build_deployments,
    _to_yaml_str,
)


class TestGenerateK8sManifests:
    """测试 generate_k8s_manifests 函数。"""

    def test_generates_all_resource_types(self):
        manifests = generate_k8s_manifests()
        assert "namespace" in manifests
        assert "configmaps" in manifests
        assert "deployments" in manifests
        assert "services" in manifests
        assert "ingresses" in manifests

    def test_default_namespace_is_monitoring(self):
        manifests = generate_k8s_manifests()
        assert manifests["namespace"]["metadata"]["name"] == "monitoring"

    def test_custom_namespace(self):
        manifests = generate_k8s_manifests(namespace="custom-ns")
        assert manifests["namespace"]["metadata"]["name"] == "custom-ns"

    def test_returns_3_configmaps(self):
        manifests = generate_k8s_manifests()
        configmaps = manifests["configmaps"]
        assert len(configmaps) == 3
        names = [cm["metadata"]["name"] for cm in configmaps]
        assert "fts-prometheus-config" in names
        assert "fts-prometheus-alerts" in names
        assert "fts-alertmanager-config" in names

    def test_returns_2_deployments(self):
        manifests = generate_k8s_manifests()
        deployments = manifests["deployments"]
        assert len(deployments) == 2
        names = [d["metadata"]["name"] for d in deployments]
        assert "fts-prometheus" in names
        assert "fts-alertmanager" in names

    def test_returns_2_services(self):
        manifests = generate_k8s_manifests()
        services = manifests["services"]
        assert len(services) == 2
        names = [s["metadata"]["name"] for s in services]
        assert "fts-prometheus" in names
        assert "fts-alertmanager" in names

    def test_prometheus_scrape_targets_include_fts(self):
        manifests = generate_k8s_manifests()
        prom_cm = [c for c in manifests["configmaps"]
                    if c["metadata"]["name"] == "fts-prometheus-config"][0]
        prometheus_yaml = prom_cm["data"]["prometheus.yml"]
        assert "fts-metrics" in prometheus_yaml
        assert "fts-data-sources" in prometheus_yaml
        assert "fts-health" in prometheus_yaml

    def test_custom_fts_service_reflected(self):
        manifests = generate_k8s_manifests(fts_service="custom-host:8080")
        prom_cm = [c for c in manifests["configmaps"]
                    if c["metadata"]["name"] == "fts-prometheus-config"][0]
        prometheus_yaml = prom_cm["data"]["prometheus.yml"]
        assert "custom-host:8080" in prometheus_yaml

    def test_custom_webhook_reflected(self):
        manifests = generate_k8s_manifests(webhook_url="https://webhook.test:443/hook")
        am_cm = [c for c in manifests["configmaps"]
                 if c["metadata"]["name"] == "fts-alertmanager-config"][0]
        am_yaml = am_cm["data"]["alertmanager.yml"]
        assert "https://webhook.test:443/hook" in am_yaml

    def test_environment_label_in_configs(self):
        manifests = generate_k8s_manifests(environment="staging")
        prom_cm = [c for c in manifests["configmaps"]
                    if c["metadata"]["name"] == "fts-prometheus-config"][0]
        prometheus_yaml = prom_cm["data"]["prometheus.yml"]
        assert "staging" in prometheus_yaml

    def test_alert_rules_contain_fts_alertnames(self):
        manifests = generate_k8s_manifests()
        alerts_cm = [c for c in manifests["configmaps"]
                      if c["metadata"]["name"] == "fts-prometheus-alerts"][0]
        alerts_yaml = alerts_cm["data"]["prometheus_alerts.yml"]
        assert "FTSDataSourceCircuitOpen" in alerts_yaml
        assert "FTSDataSourceDown" in alerts_yaml
        assert "FTSSchedulerNotRunning" in alerts_yaml

    def test_writes_to_output_dir(self, tmp_path: Path):
        out_dir = tmp_path / "k8s"
        generate_k8s_manifests(output_dir=str(out_dir))
        assert out_dir.exists()
        files = list(out_dir.iterdir())
        assert len(files) >= 8  # ns + 3 cm + 2 deploy + 2 svc + 1 ingress

    def test_deployment_uses_prometheus_image(self):
        manifests = generate_k8s_manifests()
        prom_dep = [d for d in manifests["deployments"]
                     if d["metadata"]["name"] == "fts-prometheus"][0]
        containers = prom_dep["spec"]["template"]["spec"]["containers"]
        assert any("prometheus" in c["image"] for c in containers)

    def test_deployment_has_liveness_probe(self):
        manifests = generate_k8s_manifests()
        prom_dep = [d for d in manifests["deployments"]
                     if d["metadata"]["name"] == "fts-prometheus"][0]
        containers = prom_dep["spec"]["template"]["spec"]["containers"]
        assert containers[0].get("livenessProbe") is not None


class TestBuildConfigmaps:
    """测试 _build_configmaps 构建逻辑。"""

    def test_configmaps_have_correct_metadata(self):
        configmaps = _build_configmaps("test-ns", "localhost:9100", "http://hook", "prod")
        for cm in configmaps:
            assert cm["apiVersion"] == "v1"
            assert cm["kind"] == "ConfigMap"
            assert cm["metadata"]["namespace"] == "test-ns"
            assert "app.kubernetes.io/managed-by" in cm["metadata"]["labels"]

    def test_prometheus_configmap_has_scrape_jobs(self):
        configmaps = _build_configmaps("ns", "fts:9100", "http://h", "prod")
        prom_cm = [c for c in configmaps if c["metadata"]["name"] == "fts-prometheus-config"][0]
        data = prom_cm["data"]["prometheus.yml"]
        assert "scrape_configs" in data

    def test_alertmanager_configmap_has_receivers(self):
        configmaps = _build_configmaps("ns", "fts:9100", "http://h", "prod")
        am_cm = [c for c in configmaps if c["metadata"]["name"] == "fts-alertmanager-config"][0]
        data = am_cm["data"]["alertmanager.yml"]
        assert "receivers" in data


class TestBuildDeployments:
    """测试 _build_deployments 构建逻辑。"""

    def test_deployments_have_correct_structure(self):
        deployments = _build_deployments("test-ns")
        for dep in deployments:
            assert dep["apiVersion"] == "apps/v1"
            assert dep["kind"] == "Deployment"
            assert dep["metadata"]["namespace"] == "test-ns"
            assert dep["spec"]["replicas"] == 1

    def test_deployments_mount_configmaps(self):
        deployments = _build_deployments("ns")
        prom_dep = [d for d in deployments if d["metadata"]["name"] == "fts-prometheus"][0]
        volumes = prom_dep["spec"]["template"]["spec"]["volumes"]
        vol_names = [v["name"] for v in volumes]
        assert "prometheus-config" in vol_names


class TestCheckK8sConflicts:
    """测试 check_k8s_conflicts 冲突检测。"""

    def test_no_conflicts_when_cluster_empty(self):
        conflicts = check_k8s_conflicts(
            existing_namespaces=[],
            existing_configmaps=[],
            existing_deployments=[],
        )
        # 端口分配属于 info 级别，始终会有
        info_conflicts = [c for c in conflicts if c["severity"] == "info"]
        warning_conflicts = [c for c in conflicts if c["severity"] == "warning"]
        critical_conflicts = [c for c in conflicts if c["severity"] == "critical"]

        # 端口分配提示始终存在（2 条）
        assert len(info_conflicts) >= 2
        assert len(warning_conflicts) == 0
        assert len(critical_conflicts) == 0

    def test_detects_namespace_conflict(self):
        conflicts = check_k8s_conflicts(
            existing_namespaces=["default", "monitoring", "kube-system"],
            existing_configmaps=[],
            existing_deployments=[],
        )
        ns_conflicts = [c for c in conflicts if c["type"] == "namespace_exists"]
        assert len(ns_conflicts) == 1

    def test_detects_configmap_conflict(self):
        conflicts = check_k8s_conflicts(
            existing_namespaces=["monitoring"],
            existing_configmaps=[
                {"name": "fts-prometheus-config", "namespace": "monitoring"},
            ],
            existing_deployments=[],
        )
        cm_conflicts = [c for c in conflicts if c["type"] == "configmap_exists"]
        assert len(cm_conflicts) == 1
        assert cm_conflicts[0]["severity"] == "warning"

    def test_detects_multiple_configmap_conflicts(self):
        conflicts = check_k8s_conflicts(
            existing_namespaces=["monitoring"],
            existing_configmaps=[
                {"name": "fts-prometheus-config", "namespace": "monitoring"},
                {"name": "fts-alertmanager-config", "namespace": "monitoring"},
            ],
            existing_deployments=[],
        )
        cm_conflicts = [c for c in conflicts if c["type"] == "configmap_exists"]
        assert len(cm_conflicts) == 2

    def test_detects_deployment_conflict(self):
        conflicts = check_k8s_conflicts(
            existing_namespaces=["monitoring"],
            existing_configmaps=[],
            existing_deployments=[
                {"name": "fts-prometheus", "namespace": "monitoring"},
            ],
        )
        dep_conflicts = [c for c in conflicts if c["type"] == "deployment_exists"]
        assert len(dep_conflicts) == 1

    def test_each_conflict_has_resolution(self):
        conflicts = check_k8s_conflicts(
            existing_namespaces=["monitoring"],
            existing_configmaps=[
                {"name": "fts-prometheus-config", "namespace": "monitoring"},
            ],
            existing_deployments=[
                {"name": "fts-alertmanager", "namespace": "monitoring"},
            ],
        )
        for conflict in conflicts:
            assert "resolution" in conflict
            assert len(conflict["resolution"]) > 0

    def test_conflict_report_is_serializable(self):
        conflicts = check_k8s_conflicts()
        for c in conflicts:
            assert isinstance(c, dict)
            assert "type" in c
            assert "resource" in c
            assert "severity" in c


class TestToYamlStr:
    """测试 _to_yaml_str 转换。"""

    def test_converts_dict_to_string(self):
        result = _to_yaml_str({"key": "value"})
        assert isinstance(result, str)
        assert "key" in result
        assert "value" in result

    def test_handles_nested_dict(self):
        result = _to_yaml_str({
            "outer": {
                "inner": "value",
            },
        })
        assert "outer" in result
        assert "inner" in result
