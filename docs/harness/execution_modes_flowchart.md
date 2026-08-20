# FTS 执行模式流程图

> 版本: v3.1.0+4
> 最后更新: 2026-08-20

> **v3.0.0（2026-08-20，plans/57 双系统切分）**：FTS 角色收敛为因子生产系统，策略合成职责迁移 Regime-Driven；L3 组合侧已登记退役（`retired_l3.py`，存量调用点兼容不删码，物理删除为后续独立里程碑）。FTS 侧执行模式（CLI/定时任务/监控）不变。

## 三种执行模式

```
CLI 命令行模式
  python -m fts.cli <command> [options]
  ├── version / monitor / evolution run / meta-loop run / portfolio run
  ├── factor list / factor show <id> / seeds
  ├── scheduler run / scheduler list
  └── trace_id 自动生成贯穿全链路

Scheduler 定时调度模式
  fts/scheduler/engine.py（APScheduler 驱动）
  └── 调度计划由 TRAE Schedule 定时自动化执行（v2.104.0+99 起内部调度默认停用）：
      每日 04:00 L1 知识补给 / 每日 L2 种子评估+演化 / 每日 04:00 监控+评审质检阀门（周日重量级）
      / 月度外部因子导入 / 每 10 分钟 Health Check
  └── 一键启用：$env:FTS_INTERNAL_SCHEDULER_ENABLED="1"; fts scheduler run
  └── 每个任务执行时生成独立 trace_id（前缀+时间戳）
  └── 配套：watchdog 进程监控 / hotswap 热重载 / 任务执行 3 次重试+熔断

Monitor 监控模式
  fts/monitor/（HTTP 端点 + 定期巡检）
  ├── 健康检查端点 GET /health（127.0.0.1:9100）+ CLI `fts monitor [--json]`
  ├── Elite 因子追踪（elite_tracker.py：自动淘汰/衰减检验/行为漂移）
  └── 熔断保护（circuit_breaker.py）
```

## 模式选择

| 模式 | 适用场景 | 启动方式 |
|:-----|:---------|:---------|
| CLI 命令行 | 开发调试、手动执行、单次运行 | `fts evolution run --max-generations 3` |
| Scheduler 定时 | 生产环境自动运行（周期任务由 TRAE Schedule 执行） | `$env:FTS_INTERNAL_SCHEDULER_ENABLED="1"; fts scheduler run` |
| Monitor 监控 | 持续监控、告警 | 内嵌于其他模式，自动启动 HTTP 端点 |

## 状态流转

```
[stopped] → [running] → [completed] → [circuit_broken] → [recovered/stopped]
     │                                              │
     └────────────────────── [paused] ──────────────┘
```

各循环状态持久化到 `memory/` 目录，通过 `fts monitor` 查看实时状态。

## 熔断策略

| 熔断类型 | 触发条件 | 恢复方式 |
|:---------|:---------|:---------|
| Token 熔断 | 消耗 > budget × 2.0 | 等待下一个调度周期 |
| 低 IC 熔断 | 连续 5 代 IC < 0.005 | 手动重启或等待次日 |
| 失败率熔断 | 失败率 > 95% | 检查配置后重启 |
| 连续低质量 | 连续 3 代质量分 < 30 | 手动重启 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件描述 FTS 三种执行模式（CLI 命令行 / Scheduler 定时调度 / Monitor 监控），对应 `fts/cli.py`、`fts/scheduler/engine.py`、`fts/monitor/` 模块 |
| 可验证断言 | CLI 支持 `fts evolution run` / `fts meta-loop run` 等子命令；Scheduler 周期任务由 TRAE Schedule 执行（内部默认停用 `FTS_INTERNAL_SCHEDULER_ENABLED` 默认 "0"）；Monitor 提供 HTTP 健康检查端点 GET /health |
| 检验方式 | 运行 `python -m fts.cli --help` 验证 CLI 命令；`fts scheduler status` 查看实际调度数（内部停用默认 0） |
