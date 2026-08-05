# FTS 验收报告归档索引（v2.3.0）

> 版本: v2.4.0
> 最后更新: 2026-08-05
> 用途: 归档 v2.3.0 ~ v2.4.0 下所有 Phase 14.x 验收报告

---

## 归档目录

`docs/harness/acceptance/v2.3.0/` 收录 v2.3.0 ~ v2.4.0 下 10 个 Phase 14.x 验收报告。

## 报告索引

| Phase | 标题 | 版本 | 完成日期 | 文件 |
|:------|:-----|:-----|:---------|:-----|
| **14.0.6** | 期货数据迁移验收 | v2.3.0 | 2026-08-04 | [14.0.6-migrate-acceptance.md](14.0.6-migrate-acceptance.md) |
| **14.0.7** | TQ_LOCAL 接入验收 | v2.3.0-alpha.1 | 2026-08-04 | [14.0.7-tq-local-acceptance.md](14.0.7-tq-local-acceptance.md) |
| **14.0.8** | Wind MCP 接入验收 | v2.3.0-alpha.2 | 2026-08-04 | [14.0.8-wind-mcp-acceptance.md](14.0.8-wind-mcp-acceptance.md) |
| **14.0.9** | iFinD MCP 接入验收 | v2.3.0-alpha.3 | 2026-08-04 | [14.0.9-ifind-mcp-acceptance.md](14.0.9-ifind-mcp-acceptance.md) |
| **14.1** | 数据源优先级调度器验收 | v2.3.0-beta.0 | 2026-08-04 | [14.1-aggregator-acceptance.md](14.1-aggregator-acceptance.md) |
| **14.2** | 多源交叉验证告警机制验收 | v2.3.0-beta.1 | 2026-08-04 | [14.2-cross-validation-acceptance.md](14.2-cross-validation-acceptance.md) |
| **14.3** | 多源数据融合策略验收 | v2.3.0-beta.2 | 2026-08-04 | [14.3-data-fusion-acceptance.md](14.3-data-fusion-acceptance.md) |
| **14.4** | CLI 集成 + 真实多源联调验收 | v2.3.0-rc.0 | 2026-08-04 | [14.4-cli-integration-acceptance.md](14.4-cli-integration-acceptance.md) |
| **14.5** | 调度注册 + 可观测性指标端点验收 | v2.3.0-rc.1 | 2026-08-04 | [14.5-observability-acceptance.md](14.5-observability-acceptance.md) |
| **14.6** | APScheduler 接入 + Prometheus 指标端点验收 | v2.4.0 | 2026-08-05 | [14.6-prometheus-watchdog-acceptance.md](14.6-prometheus-watchdog-acceptance.md) |

---

## 引用规范

从 `docs/harness/*.md` 引用归档报告，请使用相对路径：

```markdown
详见 [14.5-observability-acceptance.md](acceptance/v2.3.0/14.5-observability-acceptance.md)
```

从归档目录内文件互相引用，请使用同级相对路径：

```markdown
详见 [14.1-aggregator-acceptance.md](14.1-aggregator-acceptance.md)
```

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 归档目录 | `docs/harness/acceptance/v2.3.0/` |
| 报告数量 | 10 个（Phase 14.0.6 ~ 14.6） |
| 引用源文档 | `02-lifecycle.md` / `07-operations.md` / `09-advancement-plan.md` / `13-futures-data-source-integration.md` |
| 检验方式 | `ls docs/harness/acceptance/v2.3.0/*.md \| wc -l` 应输出 10 |
