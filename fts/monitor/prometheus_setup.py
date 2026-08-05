"""
fts/monitor/prometheus_setup.py — Prometheus + AlertManager 监控配置管理

用法:
    from fts.monitor.prometheus_setup import generate_configs, validate_configs

    # 生成配置
    generate_configs(output_dir="config", fts_host="localhost", fts_port=9100)

    # 验证配置
    errors = validate_configs(config_dir="config")
    if errors:
        for error in errors:
            print(f"❌ {error}")
    else:
        print("✅ 配置文件验证通过")

HARNESS §trace_id: 本模块不生成 trace_id，仅处理配置文件。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── 默认配置模板 ──────────────────────────────────────────

DEFAULT_PROMETHEUS_CONFIG: dict[str, Any] = {
    "global": {
        "scrape_interval": "15s",
        "evaluation_interval": "15s",
        "external_labels": {
            "monitor": "fts-monitor",
            "environment": "development",
        },
    },
    "rule_files": ["prometheus_alerts.yml"],
    "alerting": {
        "alertmanagers": [
            {
                "static_configs": [
                    {"targets": ["localhost:9093"]},
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
                    "targets": ["localhost:9100"],
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
                    "targets": ["localhost:9100"],
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
                    "targets": ["localhost:9100"],
                    "labels": {"service": "fts", "component": "health"},
                },
            ],
            "metrics_path": "/health",
            "scrape_interval": "10s",
        },
    ],
}

DEFAULT_ALERTMANAGER_CONFIG: dict[str, Any] = {
    "global": {
        "external_labels": {
            "monitor": "fts-monitor",
            "environment": "development",
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
                {"url": "http://localhost:5001/webhook", "send_resolved": True},
            ],
        },
        {
            "name": "critical-receiver",
            "webhook_configs": [
                {"url": "http://localhost:5001/webhook/critical", "send_resolved": True},
            ],
        },
    ],
}

# FTS 告警规则 (JSON 格式, 方便模板生成)
DEFAULT_ALERT_RULES: dict[str, Any] = {
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


def generate_configs(
    output_dir: str = "config",
    fts_host: str = "localhost",
    fts_port: int = 9100,
    alertmanager_port: int = 9093,
    webhook_url: str = "http://localhost:5001/webhook",
    environment: str = "development",
) -> dict[str, str]:
    """生成 Prometheus + AlertManager 配置文件。

    Args:
        output_dir: 输出目录 (默认 config/)
        fts_host: FTS 服务地址
        fts_port: FTS 服务端口
        alertmanager_port: AlertManager 端口
        webhook_url: Webhook 通知地址
        environment: 环境标识 (development / production)

    Returns:
        已生成文件的路径字典
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    target = f"{fts_host}:{fts_port}"

    logger.info("=" * 60)
    logger.info("开始生成监控配置")
    logger.info("  输出目录: %s", out_dir.resolve())
    logger.info("  FTS 目标: %s", target)
    logger.info("  环境: %s", environment)
    logger.info("  Webhook: %s", webhook_url)
    logger.info("=" * 60)

    # 1. Prometheus 主配置
    prom_config: dict[str, Any] = dict(DEFAULT_PROMETHEUS_CONFIG)
    prom_config["global"]["external_labels"]["environment"] = environment
    for job in prom_config["scrape_configs"]:
        job["static_configs"][0]["targets"] = [target]

    prom_path = out_dir / "prometheus.yml"
    _write_yaml(prom_path, prom_config)
    logger.info("[1/3] Prometheus 主配置已生成: %s", prom_path)
    logger.info("      - 抓取任务数: %d", len(prom_config["scrape_configs"]))
    for job in prom_config["scrape_configs"]:
        logger.info("        • %s → %s%s (间隔 %s)",
                     job["job_name"],
                     job["static_configs"][0]["targets"][0],
                     job["metrics_path"],
                     job.get("scrape_interval", "15s"))
    logger.info("      - 告警规则文件: %s", prom_config.get("rule_files", []))
    logger.info("      - AlertManager 端点: %s",
                prom_config.get("alerting", {}).get("alertmanagers", []))

    # 2. AlertManager 配置
    am_config: dict[str, Any] = dict(DEFAULT_ALERTMANAGER_CONFIG)
    am_config["global"]["external_labels"]["environment"] = environment
    for receiver in am_config["receivers"]:
        if receiver["name"] == "default-receiver":
            receiver["webhook_configs"][0]["url"] = webhook_url
        elif receiver["name"] == "critical-receiver":
            receiver["webhook_configs"][0]["url"] = f"{webhook_url}/critical"

    am_path = out_dir / "alertmanager.yml"
    _write_yaml(am_path, am_config)
    logger.info("[2/3] AlertManager 配置已生成: %s", am_path)
    logger.info("      - 接收器数: %d", len(am_config["receivers"]))
    for r in am_config["receivers"]:
        urls = [c["url"] for c in r.get("webhook_configs", [])]
        logger.info("        • %s → %s", r["name"], urls)
    logger.info("      - 抑制规则: %d 条", len(am_config.get("inhibit_rules", [])))
    logger.info("      - 路由分组: %s",
                am_config.get("route", {}).get("group_by", []))

    # 3. 告警规则
    alert_rules: dict[str, Any] = json.loads(
        json.dumps(DEFAULT_ALERT_RULES),
    )
    alert_path = out_dir / "prometheus_alerts.yml"
    _write_yaml(alert_path, alert_rules)
    logger.info("[3/3] 告警规则已生成: %s", alert_path)
    total_alerts = 0
    for group in alert_rules["groups"]:
        rule_count = len(group["rules"])
        total_alerts += rule_count
        alert_names = [r["alert"] for r in group["rules"]]
        logger.info("      - 组 '%s': %d 条规则 (%s)",
                    group["name"], rule_count, ", ".join(alert_names))
    logger.info("      - 告警总数: %d", total_alerts)

    result = {
        "prometheus": str(prom_path),
        "alertmanager": str(am_path),
        "alerts": str(alert_path),
    }

    logger.info("-" * 60)
    logger.info("配置生成完成: %d 个文件", len(result))
    for key, path in result.items():
        logger.info("  %s: %s", key, path)
    logger.info("-" * 60)

    return result


def validate_configs(config_dir: str = "config") -> list[str]:
    """验证监控配置文件的正确性。

    Args:
        config_dir: 配置文件目录

    Returns:
        错误消息列表 (空列表表示验证通过)
    """
    errors: list[str] = []
    config_path = Path(config_dir)

    logger.info("开始验证配置: %s", config_path.resolve())

    if not config_path.exists():
        errors.append(f"配置目录不存在: {config_dir}")
        logger.error("  ❌ %s", errors[-1])
        return errors

    # 1. 检查必要文件
    required_files = ["prometheus.yml", "alertmanager.yml", "prometheus_alerts.yml"]
    logger.info("  检查必要文件...")
    for filename in required_files:
        filepath = config_path / filename
        if not filepath.exists():
            errors.append(f"缺少必要配置文件: {filepath}")
            logger.error("    ❌ 缺少: %s", filename)
        else:
            logger.info("    ✅ 存在: %s", filename)

    if errors:
        logger.warning("  文件检查未通过，共 %d 个错误", len(errors))
        return errors

    # 2. 验证 Prometheus 配置
    logger.info("  验证 prometheus.yml...")
    try:
        prom_config = _read_yaml(config_path / "prometheus.yml")
        if "scrape_configs" not in prom_config:
            errors.append("prometheus.yml 缺少 scrape_configs 配置")
            logger.error("    ❌ 缺少 scrape_configs")
        else:
            job_names = [j.get("job_name", "") for j in prom_config["scrape_configs"]]
            logger.info("    ✅ %d 个抓取任务: %s", len(job_names), job_names)
            if "fts-metrics" not in job_names:
                errors.append("prometheus.yml 缺少 fts-metrics 抓取任务")
                logger.error("    ❌ 缺少 fts-metrics")

        if "rule_files" not in prom_config:
            errors.append("prometheus.yml 缺少 rule_files 引用")
            logger.error("    ❌ 缺少 rule_files")
        else:
            logger.info("    ✅ 引用告警规则: %s", prom_config["rule_files"])
    except Exception as e:  # noqa: BLE001
        errors.append(f"prometheus.yml 解析失败: {e}")
        logger.error("    ❌ 解析失败: %s", e)

    # 3. 验证告警规则
    logger.info("  验证 prometheus_alerts.yml...")
    try:
        alert_rules = _read_yaml(config_path / "prometheus_alerts.yml")
        if "groups" not in alert_rules:
            errors.append("prometheus_alerts.yml 缺少 groups 定义")
            logger.error("    ❌ 缺少 groups")
        else:
            total_rules = 0
            for group in alert_rules["groups"]:
                group_name = group.get("name", "?")
                if "rules" not in group:
                    errors.append(f"告警组 '{group_name}' 缺少 rules")
                    logger.error("    ❌ 组 '%s' 缺少 rules", group_name)
                else:
                    rule_count = len(group["rules"])
                    total_rules += rule_count
                    logger.info("    ✅ 组 '%s': %d 条规则", group_name, rule_count)
                    for rule in group["rules"]:
                        if "alert" not in rule or "expr" not in rule:
                            errors.append(f"告警规则格式错误: {rule}")
                            logger.error("      ❌ 格式错误: %s", rule)
            logger.info("    告警总数: %d", total_rules)
    except Exception as e:  # noqa: BLE001
        errors.append(f"prometheus_alerts.yml 解析失败: {e}")
        logger.error("    ❌ 解析失败: %s", e)

    # 4. 验证 AlertManager 配置
    logger.info("  验证 alertmanager.yml...")
    try:
        am_config = _read_yaml(config_path / "alertmanager.yml")
        if "receivers" not in am_config:
            errors.append("alertmanager.yml 缺少 receivers 定义")
            logger.error("    ❌ 缺少 receivers")
        else:
            receiver_names = [r["name"] for r in am_config["receivers"]]
            logger.info("    ✅ %d 个接收器: %s", len(receiver_names), receiver_names)

        if "route" not in am_config:
            errors.append("alertmanager.yml 缺少 route 定义")
            logger.error("    ❌ 缺少 route")
        else:
            logger.info("    ✅ 路由配置存在")
    except Exception as e:  # noqa: BLE001
        errors.append(f"alertmanager.yml 解析失败: {e}")
        logger.error("    ❌ 解析失败: %s", e)

    if errors:
        logger.warning("  验证完成，共 %d 个错误", len(errors))
    else:
        logger.info("  ✅ 所有配置文件验证通过")

    return errors


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    """安全写入 YAML 文件 (使用 JSON 兼容格式，避免 pyyaml 依赖)。"""
    try:
        import yaml  # type: ignore
        yaml_text = yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)
        path.write_text(yaml_text, encoding="utf-8")
    except ImportError:
        # 回退: 写入 JSON 格式 (Prometheus 支持 JSON)
        json_text = json.dumps(data, ensure_ascii=False, indent=2)
        path.write_text(f"# YAML 格式配置 (pyyaml 未安装，已生成 JSON 兼容格式)\n{json_text}", encoding="utf-8")


def _read_yaml(path: Path) -> dict[str, Any]:
    """安全读取 YAML 文件。"""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        # 回退: 尝试解析 JSON
        import re
        json_match = re.search(r'\{[\s\S]*\}', text)
        if json_match:
            return json.loads(json_match.group(0))
        raise ImportError("pyyaml 未安装，且文件不是 JSON 格式") from None


__all__ = [
    "generate_configs",
    "validate_configs",
    "DEFAULT_PROMETHEUS_CONFIG",
    "DEFAULT_ALERTMANAGER_CONFIG",
    "DEFAULT_ALERT_RULES",
]
