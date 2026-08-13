# FTS 可观测性

> 版本: v2.103.0+33
> 最后更新: 2026-08-05

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
| `GET /metrics` | 文本 (Prometheus) | 各循环 status gauge, token counter, 衰减/Regime/权重/质量/Live/风控/反馈指标 |
| `GET /metrics/data-sources` | 文本 (Prometheus) | 数据源专用指标（熔断状态/成功率） |
| `GET /` | HTML | 仪表板 (状态表格) |
| `POST /api/v1/signal/submit` | JSON | 信号提交（验证 → 风控 → 模拟成交） |
| `GET /api/v1/risk/status` | JSON | 风控规则状态 |
| `GET /api/v1/live/factors` | JSON | Live 因子偏离监控列表 |
| `GET /api/v1/live/factors/{id}/deviation` | JSON | 单因子偏离详情 |
| `data_level_monitor_job`（每日 04:00） | 日志 | 数据级质量监控（GAP-F06）：缺失率/异常值/复权一致性/多源分歧四维检查结果与告警计数（scheduler 任务 `fts.dlm`） |

### 数据级质量监控任务（GAP-F06）

scheduler 任务 `data_level_monitor_job`（每日 04:00）执行数据级质量监控：缺失率/异常值/复权一致性/多源分歧四维检查，输出告警计数与结果日志（trace_id 前缀 `fts.dlm`）。

### L3 组合漂移告警（GAP-F13，v2.72.1）

`DriftMonitor` 每次 L3 组合构建后计算成员重合率（Jaccard）与权重 L1 变化率，超阈值时输出告警日志 + Prometheus 兼容指标（可被 `/metrics` 采集解析）：

| 配置项 | 默认值 | 说明 |
|:-------|:-------|:-----|
| `overlap_threshold` | 0.50 | 成员重合率下限（低于触发告警） |
| `weight_l1_threshold` | 0.40 | 权重 L1 变化率上限（高于触发告警） |
| `trigger_rebalance` | False | 超阈值时是否自动生成粘性重平衡建议 |

告警指标格式（`METRIC drift_alert`）：
```
METRIC drift_alert{overlap=0.00,weight=1.00,o_th=0.50,w_th=0.40} 1
```
告警同时写入 `PortfolioLoop` state（`drift_alerted` / `drift_alert_info`）；`trigger_rebalance=True` 时生成 `AgentOptimizationProposal`（source=`drift_monitor`）附加到 proposals 供下游 Agent 消费。

### 股票信号管道标准化与权重冻结日志（GAP-076 / GAP-072，v2.101.0；已随股票管线剥离至 fts-stock，2026-08）

> 以下为历史记录：`scripts/daily_signal_pipeline.py`（股票信号管道）已随股票管线剥离至 fts-stock（2026-08），主系统仅保留期货信号管道（`scripts/futures_signal_pipeline.py`）的同类结构化进度日志。

`scripts/daily_signal_pipeline.py` 每次运行输出结构化进度日志（stdout，含 trace_id 前置）：
- `[标准化] 截面 none/zscore/rank 已应用到 {n} 个因子（{m} 交易日）`——Step 4 前截面标准化方式与覆盖范围（GAP-076）
- `[权重冻结] 复用 {recomputed_at} 权重快照（{k} 因子），仅刷新因子值`——冻结日复用快照（GAP-072，快照含 `normalize` 字段记录重算日标准化口径）
- `[权重] 本周重算日: 权重已学习并保存快照 -> {path}`——重算日 Ridge 权重学习 + 快照落盘
- `[警告] 信号计算错误: {n} 次`——因子执行异常计数（不阻断主流程）

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

# HELP fts_factor_decay_ic 因子衰减监控 IC（A.2）
# TYPE fts_factor_decay_ic gauge
# HELP fts_quality_score 因子质量评分（A.1）
# TYPE fts_quality_score gauge
# HELP fts_regime_weight_adjustment 市场制度权重调整（A.3）
# TYPE fts_regime_weight_adjustment gauge
# HELP fts_regime_confidence 当前市场制度置信度（28-T10）
# TYPE fts_regime_confidence gauge
# HELP fts_regime_entropy_norm 制度后验归一化熵(0~1, 越高越不确定)（28-T10）
# TYPE fts_regime_entropy_norm gauge
# HELP fts_regime_exposure_scale 置信度仓位缩放因子（28-T10）
# TYPE fts_regime_exposure_scale gauge
# HELP fts_regime_blend_hhi 制度概率分布集中度(HHI)（28-T10）
# TYPE fts_regime_blend_hhi gauge
# HELP fts_regime_name 当前市场制度名称 (1=当前生效)（28-T10）
# TYPE fts_regime_name gauge
# HELP fts_dq_completeness 数据完整性指标（B.1）
# TYPE fts_dq_completeness gauge
# HELP fts_live_factor_ic 实盘因子 IC（C.2）
# TYPE fts_live_factor_ic gauge
# HELP fts_risk_check_total 风控检查次数（C.2）
# TYPE fts_risk_check_total counter
# HELP fts_feedback_triggers_total 反馈触发次数（C.3）
# TYPE fts_feedback_triggers_total counter
```

### Regime 观测指标（28-T10，机构观测纪律）

`fts_regime_*` 指标由 `fts/monitor/prometheus_metrics.py:MetricsRegistry.record_regime_metrics(market, regime, confidence, probs, exposure_scale)` 落盘，`PortfolioLoop` Step 2.5 Regime 调整完成后按市场上报（失败仅告警不阻断主流程），供 `GET /metrics` 审计 regime 决策：

| 指标 | 语义 | 说明 |
|:-----|:-----|:-----|
| `fts_regime_confidence{market=...}` | 当前制度置信度 ∈ [0,1] | 越界钳制 |
| `fts_regime_entropy_norm{market=...}` | 制度后验归一化熵 ∈ [0,1] | `H/ln(K)`；无 probs（硬查表回退）记 0.0 |
| `fts_regime_exposure_scale{market=...}` | 置信度仓位缩放因子 | ∈ [scale_min, 1.0]，`confidence_scale` 关闭时恒 1.0 |
| `fts_regime_blend_hhi{market=...}` | 制度概率分布集中度 HHI = Σ p_i² | 无 probs 记 1.0（确定性分布） |
| `fts_regime_name{market=...,regime=...}` | 当前制度名（1=生效） | 空 regime 不上报 |

示例（futures 市场）：
```
fts_regime_confidence{market="futures"} 0.7
fts_regime_entropy_norm{market="futures"} 0.63
fts_regime_exposure_scale{market="futures"} 0.6
fts_regime_blend_hhi{market="futures"} 0.52
fts_regime_name{market="futures",regime="bear"} 1
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
