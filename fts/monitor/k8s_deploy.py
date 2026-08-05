"""
fts/monitor/k8s_deploy.py — Kubernetes 部署清单生成 + 配置冲突检查

用法:
    from fts.monitor.k8s_deploy import generate_k8s_manifests, check_k8s_conflicts

    # 生成 K8s 部署清单
    manifests = generate_k8s_manifests(
        namespace="monitoring",
        fts_service="fts:9100",
        webhook_url="http://webhook.example.com:5001/webhook",
    )

    # 检查配置冲突
    conflicts = check_k8s_conflicts()
    for c in conflicts:
        print(c)

HARNESS §trace_id: 本模块不生成 trace_id，仅处理 K8s 清单生成与冲突检查。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Kubernetes 资源模板 ──────────────────────────────────

NAMESPACE_TEMPLATE: dict[str, Any] = {
    "apiVersion": "v1",
    "kind": "Namespace",
    "metadata": {
        "name": "monitoring",
        "labels": {"app.kubernetes.io/managed-by": "fts"},
    },
}

SERVICE_MONITOR_PROMETHEUS: dict[str, Any] = {
    "apiVersion": "v1",
    "kind": "Service",
    "metadata": {
        "name": "fts-prometheus",
        "namespace": "monitoring",
        "labels": {
            "app": "fts-prometheus",
            "app.kubernetes.io/managed-by": "fts",
        },
    },
    "spec": {
        "type": "ClusterIP",
        "selector": {"app": "fts-prometheus"},
        "ports": [
            {"name": "http", "port": 9090, "targetPort": 9090, "protocol": "TCP"},
        ],
    },
}

SERVICE_ALERTMANAGER: dict[str, Any] = {
    "apiVersion": "v1",
    "kind": "Service",
    "metadata": {
        "name": "fts-alertmanager",
        "namespace": "monitoring",
        "labels": {
            "app": "fts-alertmanager",
            "app.kubernetes.io/managed-by": "fts",
        },
    },
    "spec": {
        "type": "ClusterIP",
        "selector": {"app": "fts-alertmanager"},
        "ports": [
            {"name": "http", "port": 9093, "targetPort": 9093, "protocol": "TCP"},
        ],
    },
}


def generate_k8s_manifests(
    namespace: str = "monitoring",
    fts_service: str = "localhost:9100",
    webhook_url: str = "http://localhost:5001/webhook",
    environment: str = "production",
    output_dir: str | None = None,
) -> dict[str, Any]:
    """生成 Kubernetes 部署清单。

    Args:
        namespace: K8s 命名空间
        fts_service: FTS 服务地址 (host:port)
        webhook_url: Webhook 通知地址
        environment: 环境标识
        output_dir: 输出目录 (可选, 用于写入 YAML 文件)

    Returns:
        K8s 资源清单字典，key 为资源类型，value 为 YAML 兼容字典列表
    """
    manifests: dict[str, Any] = {
        "namespace": _build_namespace(namespace),
        "configmaps": _build_configmaps(namespace, fts_service, webhook_url, environment),
        "deployments": _build_deployments(namespace),
        "services": _build_services(namespace),
        "ingresses": _build_ingresses(namespace),
    }

    logger.info("=" * 60)
    logger.info("Kubernetes 部署清单生成完成")
    logger.info("  命名空间: %s", namespace)
    logger.info("  FTS 服务: %s", fts_service)
    logger.info("  Webhook: %s", webhook_url)
    logger.info("  环境: %s", environment)
    logger.info("-" * 60)

    for resource_type, items in manifests.items():
        if isinstance(items, list):
            logger.info("  %s: %d 个资源", resource_type, len(items))
            for item in items:
                name = item.get("metadata", {}).get("name", "?")
                kind = item.get("kind", "?")
                logger.info("    • %s/%s", kind, name)

    if output_dir:
        _write_manifests(manifests, output_dir)

    logger.info("=" * 60)
    return manifests


def _build_namespace(namespace: str) -> dict[str, Any]:
    """构建 Namespace 资源。"""
    ns = json.loads(json.dumps(NAMESPACE_TEMPLATE))
    ns["metadata"]["name"] = namespace
    logger.info("构建 Namespace: %s", namespace)
    return ns


def _build_configmaps(
    namespace: str,
    fts_service: str,
    webhook_url: str,
    environment: str,
) -> list[dict[str, Any]]:
    """构建 ConfigMap 资源 (包含所有配置文件)。"""
    configmaps: list[dict[str, Any]] = []

    # ConfigMap 1: Prometheus 主配置
    prometheus_config = {
        "global": {
            "scrape_interval": "15s",
            "evaluation_interval": "15s",
            "external_labels": {
                "monitor": "fts-monitor",
                "environment": environment,
            },
        },
        "rule_files": ["/etc/prometheus/rules/*.yml"],
        "alerting": {
            "alertmanagers": [
                {
                    "static_configs": [
                        {"targets": ["fts-alertmanager:9093"]},
                    ],
                },
            ],
        },
        "scrape_configs": [
            {
                "job_name": "fts-metrics",
                "scheme": "http",
                "static_configs": [
                    {
                        "targets": [fts_service],
                        "labels": {"service": "fts", "component": "core"},
                    },
                ],
                "metrics_path": "/metrics",
                "scrape_interval": "10s",
            },
            {
                "job_name": "fts-data-sources",
                "scheme": "http",
                "static_configs": [
                    {
                        "targets": [fts_service],
                        "labels": {"service": "fts", "component": "data_source"},
                    },
                ],
                "metrics_path": "/metrics/data-sources",
                "scrape_interval": "30s",
            },
            {
                "job_name": "fts-health",
                "scheme": "http",
                "static_configs": [
                    {
                        "targets": [fts_service],
                        "labels": {"service": "fts", "component": "health"},
                    },
                ],
                "metrics_path": "/health",
                "scrape_interval": "10s",
            },
        ],
    }

    configmaps.append({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "fts-prometheus-config",
            "namespace": namespace,
            "labels": {
                "app": "fts-prometheus",
                "app.kubernetes.io/managed-by": "fts",
            },
        },
        "data": {
            "prometheus.yml": _to_yaml_str(prometheus_config),
        },
    })

    # ConfigMap 2: 告警规则
    alert_rules = {
        "groups": [
            {
                "name": "fts_data_source",
                "interval": "30s",
                "rules": [
                    {
                        "alert": "FTSDataSourceCircuitOpen",
                        "expr": "fts_circuit_open == 1",
                        "for": "1m",
                        "labels": {"severity": "critical", "team": "fts", "component": "data_source"},
                        "annotations": {
                            "summary": "FTS 数据源熔断器开启",
                            "description": "数据源熔断器已开启，数据采集可能中断。",
                            "runbook_url": "https://wiki.example.com/fts/alerts/circuit-open",
                        },
                    },
                    {
                        "alert": "FTSDataSourceSuccessRateLow",
                        "expr": "fts_data_source_success_rate < 0.8",
                        "for": "5m",
                        "labels": {"severity": "warning", "team": "fts", "component": "data_source"},
                        "annotations": {
                            "summary": "FTS 数据源成功率低于 80%",
                            "description": "数据源成功率为 {{ $value | humanizePercentage }}，低于阈值 80%。",
                        },
                    },
                    {
                        "alert": "FTSDataSourceDown",
                        "expr": 'up{job="fts-metrics"} == 0',
                        "for": "2m",
                        "labels": {"severity": "critical", "team": "fts", "component": "fts_service"},
                        "annotations": {
                            "summary": "FTS 指标端点不可达",
                            "description": "Prometheus 无法抓取 FTS /metrics 端点。FTS 进程可能已崩溃。",
                        },
                    },
                ],
            },
            {
                "name": "fts_scheduler",
                "interval": "60s",
                "rules": [
                    {
                        "alert": "FTSSchedulerNotRunning",
                        "expr": "fts_scheduler_running == 0",
                        "for": "5m",
                        "labels": {"severity": "critical", "team": "fts", "component": "scheduler"},
                        "annotations": {
                            "summary": "FTS 调度器未运行",
                            "description": "调度器已停止运行超过 5 分钟。定时任务将不会执行。",
                        },
                    },
                ],
            },
            {
                "name": "fts_factor_quality",
                "interval": "5m",
                "rules": [
                    {
                        "alert": "FTSEliteFactorCountLow",
                        "expr": "fts_elite_factor_count < 10",
                        "for": "15m",
                        "labels": {"severity": "warning", "team": "fts", "component": "factor_engine"},
                        "annotations": {
                            "summary": "Elite 因子数量偏低",
                            "description": "Elite 因子数量为 {{ $value }}，低于 10 个。",
                        },
                    },
                ],
            },
        ],
    }

    configmaps.append({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "fts-prometheus-alerts",
            "namespace": namespace,
            "labels": {
                "app": "fts-prometheus",
                "app.kubernetes.io/managed-by": "fts",
            },
        },
        "data": {
            "prometheus_alerts.yml": _to_yaml_str(alert_rules),
        },
    })

    # ConfigMap 3: AlertManager 配置
    alertmanager_config = {
        "global": {
            "external_labels": {
                "monitor": "fts-monitor",
                "environment": environment,
            },
        },
        "route": {
            "group_by": ["alertname", "component"],
            "group_wait": "30s",
            "group_interval": "5m",
            "repeat_interval": "4h",
            "receiver": "default-receiver",
            "routes": [
                {
                    "match": {"severity": "critical"},
                    "receiver": "critical-receiver",
                    "group_wait": "10s",
                    "repeat_interval": "10m",
                    "continue": True,
                },
            ],
        },
        "inhibit_rules": [
            {
                "source_match": {"severity": "critical"},
                "target_match": {"severity": "warning"},
                "equal": ["alertname", "component"],
            },
        ],
        "receivers": [
            {
                "name": "default-receiver",
                "webhook_configs": [
                    {"url": webhook_url, "send_resolved": True},
                ],
            },
            {
                "name": "critical-receiver",
                "webhook_configs": [
                    {"url": f"{webhook_url}/critical", "send_resolved": True},
                ],
            },
        ],
    }

    configmaps.append({
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {
            "name": "fts-alertmanager-config",
            "namespace": namespace,
            "labels": {
                "app": "fts-alertmanager",
                "app.kubernetes.io/managed-by": "fts",
            },
        },
        "data": {
            "alertmanager.yml": _to_yaml_str(alertmanager_config),
        },
    })

    logger.info("构建 %d 个 ConfigMap", len(configmaps))
    for cm in configmaps:
        logger.info("  • %s", cm["metadata"]["name"])

    return configmaps


def _build_deployments(namespace: str) -> list[dict[str, Any]]:
    """构建 Deployment 资源 (Prometheus + AlertManager)。"""
    deployments: list[dict[str, Any]] = []

    # Deployment 1: Prometheus
    deployments.append({
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "fts-prometheus",
            "namespace": namespace,
            "labels": {
                "app": "fts-prometheus",
                "app.kubernetes.io/managed-by": "fts",
            },
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "fts-prometheus"}},
            "template": {
                "metadata": {
                    "labels": {"app": "fts-prometheus"},
                },
                "spec": {
                    "containers": [
                        {
                            "name": "prometheus",
                            "image": "prom/prometheus:v2.50.0",
                            "args": [
                                "--config.file=/etc/prometheus/prometheus.yml",
                                "--storage.tsdb.path=/prometheus",
                                "--web.enable-lifecycle",
                            ],
                            "ports": [
                                {"containerPort": 9090, "name": "http"},
                            ],
                            "volumeMounts": [
                                {
                                    "name": "prometheus-config",
                                    "mountPath": "/etc/prometheus",
                                    "readOnly": True,
                                },
                                {
                                    "name": "prometheus-alerts",
                                    "mountPath": "/etc/prometheus/rules",
                                    "readOnly": True,
                                },
                                {
                                    "name": "prometheus-data",
                                    "mountPath": "/prometheus",
                                },
                            ],
                            "resources": {
                                "requests": {"cpu": "200m", "memory": "512Mi"},
                                "limits": {"cpu": "1", "memory": "2Gi"},
                            },
                            "readinessProbe": {
                                "httpGet": {"path": "-/healthy", "port": 9090},
                                "initialDelaySeconds": 10,
                                "periodSeconds": 5,
                            },
                            "livenessProbe": {
                                "httpGet": {"path": "-/healthy", "port": 9090},
                                "initialDelaySeconds": 30,
                                "periodSeconds": 10,
                            },
                        },
                    ],
                    "volumes": [
                        {
                            "name": "prometheus-config",
                            "configMap": {"name": "fts-prometheus-config"},
                        },
                        {
                            "name": "prometheus-alerts",
                            "configMap": {"name": "fts-prometheus-alerts"},
                        },
                        {
                            "name": "prometheus-data",
                            "emptyDir": {},
                        },
                    ],
                },
            },
        },
    })

    # Deployment 2: AlertManager
    deployments.append({
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "fts-alertmanager",
            "namespace": namespace,
            "labels": {
                "app": "fts-alertmanager",
                "app.kubernetes.io/managed-by": "fts",
            },
        },
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": "fts-alertmanager"}},
            "template": {
                "metadata": {
                    "labels": {"app": "fts-alertmanager"},
                },
                "spec": {
                    "containers": [
                        {
                            "name": "alertmanager",
                            "image": "prom/alertmanager:v0.27.0",
                            "args": [
                                "--config.file=/etc/alertmanager/alertmanager.yml",
                                "--storage.path=/alertmanager",
                            ],
                            "ports": [
                                {"containerPort": 9093, "name": "http"},
                            ],
                            "volumeMounts": [
                                {
                                    "name": "alertmanager-config",
                                    "mountPath": "/etc/alertmanager",
                                    "readOnly": True,
                                },
                                {
                                    "name": "alertmanager-data",
                                    "mountPath": "/alertmanager",
                                },
                            ],
                            "resources": {
                                "requests": {"cpu": "100m", "memory": "128Mi"},
                                "limits": {"cpu": "500m", "memory": "512Mi"},
                            },
                        },
                    ],
                    "volumes": [
                        {
                            "name": "alertmanager-config",
                            "configMap": {"name": "fts-alertmanager-config"},
                        },
                        {
                            "name": "alertmanager-data",
                            "emptyDir": {},
                        },
                    ],
                },
            },
        },
    })

    logger.info("构建 %d 个 Deployment", len(deployments))
    for d in deployments:
        logger.info("  • %s", d["metadata"]["name"])

    return deployments


def _build_services(namespace: str) -> list[dict[str, Any]]:
    """构建 Service 资源。"""
    services: list[dict[str, Any]] = [
        json.loads(json.dumps(SERVICE_MONITOR_PROMETHEUS)),
        json.loads(json.dumps(SERVICE_ALERTMANAGER)),
    ]
    for svc in services:
        svc["metadata"]["namespace"] = namespace

    logger.info("构建 %d 个 Service", len(services))
    for s in services:
        logger.info("  • %s", s["metadata"]["name"])

    return services


def _build_ingresses(namespace: str) -> list[dict[str, Any]]:
    """构建 Ingress 资源 (可选，暴露 UI)。"""
    ingresses: list[dict[str, Any]] = [
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "fts-monitoring",
                "namespace": namespace,
                "labels": {
                    "app.kubernetes.io/managed-by": "fts",
                },
                "annotations": {
                    "nginx.ingress.kubernetes.io/rewrite-target": "/$1",
                },
            },
            "spec": {
                "rules": [
                    {
                        "host": "fts-monitoring.example.com",
                        "http": {
                            "paths": [
                                {
                                    "path": "/prometheus/(.*)",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "fts-prometheus",
                                            "port": {"number": 9090},
                                        },
                                    },
                                },
                                {
                                    "path": "/alertmanager/(.*)",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "fts-alertmanager",
                                            "port": {"number": 9093},
                                        },
                                    },
                                },
                            ],
                        },
                    },
                ],
            },
        },
    ]

    logger.info("构建 %d 个 Ingress", len(ingresses))
    for ing in ingresses:
        logger.info("  • %s", ing["metadata"]["name"])

    return ingresses


def _write_manifests(manifests: dict[str, Any], output_dir: str) -> None:
    """将 K8s 清单写入 YAML 文件。"""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Namespace
    ns_path = out_dir / "00-namespace.yml"
    _write_yaml_file(ns_path, manifests["namespace"])

    # ConfigMaps
    for i, cm in enumerate(manifests["configmaps"], start=1):
        name = cm["metadata"]["name"]
        path = out_dir / f"0{i}-configmap-{name}.yml"
        _write_yaml_file(path, cm)

    # Deployments
    offset = 1 + len(manifests["configmaps"])
    for i, dep in enumerate(manifests["deployments"], start=offset):
        name = dep["metadata"]["name"]
        path = out_dir / f"0{i}-deployment-{name}.yml"
        _write_yaml_file(path, dep)

    # Services
    offset = 1 + len(manifests["configmaps"]) + len(manifests["deployments"])
    for i, svc in enumerate(manifests["services"], start=offset):
        name = svc["metadata"]["name"]
        path = out_dir / f"0{i}-service-{name}.yml"
        _write_yaml_file(path, svc)

    # Ingresses
    offset = 1 + len(manifests["configmaps"]) + len(manifests["deployments"]) + len(manifests["services"])
    for i, ing in enumerate(manifests["ingresses"], start=offset):
        name = ing["metadata"]["name"]
        path = out_dir / f"0{i}-ingress-{name}.yml"
        _write_yaml_file(path, ing)

    logger.info("K8s 清单已写入: %s", out_dir.resolve())


def _write_yaml_file(path: Path, data: dict[str, Any]) -> None:
    """安全写入 YAML 文件。"""
    try:
        import yaml  # type: ignore
        yaml_text = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        path.write_text(yaml_text, encoding="utf-8")
    except ImportError:
        json_text = json.dumps(data, ensure_ascii=False, indent=2)
        path.write_text(f"# YAML format (use kubectl apply -f)\n{json_text}", encoding="utf-8")
    logger.info("  写入: %s", path.name)


def _to_yaml_str(data: dict[str, Any]) -> str:
    """将字典转换为 YAML 字符串 (用于 ConfigMap data)。"""
    try:
        import yaml  # type: ignore
        return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except ImportError:
        return json.dumps(data, ensure_ascii=False, indent=2)


def check_k8s_conflicts(
    existing_namespaces: list[str] | None = None,
    existing_configmaps: list[dict[str, str]] | None = None,
    existing_deployments: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """检查 Kubernetes 配置冲突。

    Args:
        existing_namespaces: 现有命名空间列表 (模拟 kubectl get ns)
        existing_configmaps: 现有 ConfigMap 列表，格式 [{"name": ..., "namespace": ...}]
        existing_deployments: 现有 Deployment 列表，格式 [{"name": ..., "namespace": ...}]

    Returns:
        冲突字典列表，每项包含 type、resource、conflict 描述
    """
    conflicts: list[dict[str, Any]] = []

    # 检查命名空间冲突
    target_namespace = "monitoring"
    if existing_namespaces and target_namespace in existing_namespaces:
        conflicts.append({
            "type": "namespace_exists",
            "resource": f"Namespace/{target_namespace}",
            "conflict": f"命名空间 '{target_namespace}' 已存在",
            "severity": "info",
            "resolution": "使用 'kubectl apply' 更新现有资源，或选择不同的命名空间",
        })

    # 检查 ConfigMap 冲突
    target_configmaps = [
        "fts-prometheus-config",
        "fts-prometheus-alerts",
        "fts-alertmanager-config",
    ]
    if existing_configmaps:
        for target in target_configmaps:
            found = [c for c in existing_configmaps if c.get("name") == target]
            if found:
                conflicts.append({
                    "type": "configmap_exists",
                    "resource": f"ConfigMap/{target}",
                    "conflict": f"ConfigMap '{target}' 在命名空间 '{found[0].get('namespace', '?')}' 已存在",
                    "severity": "warning",
                    "resolution": "执行 'kubectl apply -f' 将更新现有 ConfigMap，注意可能覆盖自定义修改",
                })

    # 检查 Deployment 冲突
    target_deployments = ["fts-prometheus", "fts-alertmanager"]
    if existing_deployments:
        for target in target_deployments:
            found = [d for d in existing_deployments if d.get("name") == target]
            if found:
                conflicts.append({
                    "type": "deployment_exists",
                    "resource": f"Deployment/{target}",
                    "conflict": f"Deployment '{target}' 在命名空间 '{found[0].get('namespace', '?')}' 已存在",
                    "severity": "warning",
                    "resolution": "执行 'kubectl apply -f' 将滚动更新现有 Deployment",
                })

    # 检查端口冲突 (理论上)
    port_conflicts = [
        {"port": 9090, "service": "Prometheus"},
        {"port": 9093, "service": "AlertManager"},
    ]
    for pc in port_conflicts:
        conflicts.append({
            "type": "port_allocation",
            "resource": f"Port/{pc['port']}",
            "conflict": f"服务 {pc['service']} 使用端口 {pc['port']} (ClusterIP 模式通常无冲突风险)",
            "severity": "info",
            "resolution": "ClusterIP 模式下端口在集群内唯一；如需 NodePort 模式需检查冲突",
        })

    return conflicts


def print_conflict_report(conflicts: list[dict[str, Any]]) -> None:
    """打印冲突检查报告。"""
    if not conflicts:
        logger.info("✅ 未检测到配置冲突，部署可以安全执行")
        return

    logger.warning("⚠️  检测到 %d 个潜在冲突:", len(conflicts))
    logger.warning("=" * 60)

    for conflict in conflicts:
        severity_icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(
            conflict["severity"], "⚪",
        )
        logger.warning(
            "%s [%s] %s: %s",
            severity_icon,
            conflict["type"],
            conflict["resource"],
            conflict["conflict"],
        )
        logger.warning("   → 建议: %s", conflict["resolution"])

    logger.warning("=" * 60)
    logger.warning("部署前请评估以上冲突，确认无问题后再执行 apply")


__all__ = [
    "generate_k8s_manifests",
    "check_k8s_conflicts",
    "print_conflict_report",
]
