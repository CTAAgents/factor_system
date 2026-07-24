# FTS 可观测性

> 版本: v1.1.0
> 最后更新: 2026-07-24

---

## 1. 可观测性三支柱

| 支柱 | 实现 | 文件 |
|:-----|:-----|:-----|
| **日志** | Python logging 模块 | 各模块 logger |
| **指标** | HTTP /metrics 端点（Prometheus 格式） | `fts/monitor/http_server.py` |
| **追踪** | trace_id 全链路传播 | `fts/factor_engine/state.py` |

## 2. trace_id 全链路追踪

### 生成规则

```python
# fts.factor_engine.state.generate_trace_id(prefix="ftr")
trace_id = f"{prefix}_{8hex}_{timestamp}"
# 示例: ftr_a1b2c3d4_20260724T120000
```

### 传播路径

```
CLI 入口 (cli.py)
  └── generate_trace_id()
      ├── EvolutionLoop.run()      → 所有因子演化操作
      ├── MetaLoop.run()           → 所有 L1 操作
      └── PortfolioLoop.run()      → 所有 L3 操作
```

### 追踪内容

| 组件 | trace_id 载体 |
|:-----|:--------------|
| 因子程序 | `FactorProgram.trace_id` |
| 评估结果 | `FactorEvaluation.trace_id` |
| 经验链 | `ExperienceTrace.trace_id` |
| LLM 调用 | `LLMCallRecord.trace_id` |
| 状态文件 | `EvolutionState` (内存中) |

## 3. 监控系统

### 循环状态监控

`fts monitor` 命令输出 L1/L2/L3 三层循环状态：

| 指标 | 来源 | 告警条件 |
|:-----|:-----|:---------|
| status | `state.json` 中的 `status` 字段 | `circuit_broken` = 紧急 |
| age_hours | 距上次更新的小时数 | >24h = stale |
| tokens_consumed | token 消耗量 | 按 budget 阈值 |
| healthy | 综合判定 | False 时告警 |

### HTTP 监控端点

监听地址: `127.0.0.1:9100`

| 端点 | 格式 | 内容 |
|:-----|:-----|:-----|
| `GET /health` | JSON | 健康状态 + L1/L2/L3 循环摘要 |
| `GET /metrics` | 文本 (Prometheus) | 各循环 status gauge, token counter |
| `GET /` | HTML | 仪表板 (状态表格) |

### 指标字段

```
# HELP fts_loop_status L1/L2/L3 loop status (0=unknown, 1=running, 2=paused, 3=completed, 4=circuit_broken)
# TYPE fts_loop_status gauge
fts_loop_status{loop="L1"} 3.0
fts_loop_status{loop="L2"} 3.0
fts_loop_status{loop="L3"} 3.0

# HELP fts_tokens_consumed Total tokens consumed
# TYPE fts_tokens_consumed counter
fts_tokens_consumed{loop="L1"} 15000.0
fts_tokens_consumed{loop="L2"} 85000.0
fts_tokens_consumed{loop="L3"} 5000.0
```

## 4. Elite 因子追踪

`EliteFactorTracker` 追踪 elite 因子性能：

| 功能 | 说明 |
|:-----|:------|
| `update()` | 更新因子追踪记录 |
| `get_decaying(max_consecutive=4)` | 检测连续低 IC 的因子 |
| `auto_retire()` | 自动退役过期因子 |
| `report()` | 生成追踪报告 |
| `_calc_decay_6m()` | 6 个月衰减率计算 |

文件: `fts/monitor/elite_tracker.py`

## 5. 日志规范

### 日志级别使用

| 级别 | 使用场景 |
|:-----|:---------|
| ERROR | 数据不可用、熔断触发、编译失败 |
| WARNING | 降级回退、可选依赖缺失、部分失败 |
| INFO | 循环启动/完成、因子晋升、配置加载 |
| DEBUG | 详细执行流程、参数值 |

### 日志格式

```
%(asctime)s [%(levelname)s] %(name)s: %(message)s
2026-07-24 12:00:00,123 [INFO] fts.evolution_loop: trace_id=ftr_a1b2c3d4 run completed
```

---

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|:-------------|:-----------|:---------|
| `fts/factor_engine/state.py:generate_trace_id` | trace_id 格式为 `{prefix}_{8hex}_{timestamp}` | 单元测试 |
| `fts/monitor/http_server.py:MetricsHTTPServer` | 端点 /health /metrics / 返回预期格式 | `pytest tests/test_http_server.py` |
| `fts/monitor/elite_tracker.py:EliteFactorTracker` | auto_retire 正确移除衰减因子 | 单元测试 |
| `fts/factor_engine/monitor.py:check_loop` | 读取 state.json 返回 LoopStatus | 单元测试 |
