# FTS 可观测性

> 版本: v3.1.0+4
> 最后更新: 2026-08-20

---

## 1. 可观测性三支柱

| 支柱 | 实现 | 文件 |
|:-----|:-----|:-----|
| **日志** | Python logging 模块 | 各模块 logger |
| **指标** | HTTP /metrics 端点（Prometheus 格式） | `fts/monitor/http_server.py` |
| **追踪** | trace_id 全链路传播 | `fts/factor_engine/state.py` |

## 2. trace_id 全链路追踪

```python
# fts.factor_engine.state.generate_trace_id(prefix="ftr")
trace_id = f"{prefix}_{8hex}_{timestamp}"   # 例: ftr_a1b2c3d4_20260724T120000
```

CLI 入口生成 trace_id → 传递到 EvolutionLoop.run / MetaLoop.run / PortfolioLoop.run → 经 `FactorProgram.trace_id` / `FactorEvaluation.trace_id` / `ExperienceTrace.trace_id` / `LLMCallRecord.trace_id` 贯穿全链路。

---

## 3. 监控系统

### 循环状态监控

`fts monitor` 输出 L1/L2/L3 三层循环状态：status（`circuit_broken`=紧急）、age_hours（>24h=stale）、tokens_consumed（按 budget 阈值）、healthy（综合判定）。

### HTTP 监控端点

监听地址: `127.0.0.1:9100`

| 端点 | 格式 | 内容 |
|:-----|:-----|:-----|
| `GET /health` | JSON | 健康状态 + L1/L2/L3 循环摘要 |
| `GET /metrics` | 文本 (Prometheus) | 循环 status gauge、token counter、衰减/Regime/质量/风控/数据源指标 |
| `GET /metrics/data-sources` | 文本 (Prometheus) | 数据源专用指标（熔断状态/成功率） |
| `GET /` | HTML | 仪表板（状态表格） |
| `POST /api/v1/signal/submit` | JSON | 信号提交（验证 → 风控 → 模拟成交） |
| `GET /api/v1/risk/status` | JSON | 风控规则状态 |
| `GET /api/v1/live/factors` | JSON | Live 因子偏离监控列表 |
| `GET /workflow` + `/api/workflow/*` | HTML/JSON | WorkFlow UI SPA + 阶段/批次/质检看板 |

### 关键告警观测点（当前生效）

| 观测点 | 来源/含义 | 级别 |
|:-----|:---------|:-----|
| L3 组合漂移告警（GAP-F13） | `DriftMonitor` 成员重合率（<0.50）或权重 L1 变化率（>0.40）超阈值 → `METRIC drift_alert{...} 1` + state 标记 | warning |
| 数据级质量监控（GAP-F06） | `data_level_monitor_job` 每日 04:00：缺失率/异常值/复权一致性/多源分歧四维检查（trace_id 前缀 `fts.dlm`） | warning/critical |
| 因果审查跳过告警（v2.105.0+4） | `CausalValidator.validate` 异常 → `skipped=True` + run 收尾汇总 `[evo] ⚠️ 因果审查跳过 N 个因子` | warning |
| 评审质检阀门日志（v2.104.0+89） | `[review] 因子 {id} 审查决定: {decision}` / L3 池巡检统计 / 机审门禁判定原因 | info |
| 评审质检落库影子校验（v2.105.0+18） | apply 前置断言：判定漂移 → 拒绝落库；无基线 → 拒绝；面板指纹变化 → 跳过断言 | warning |
| L3 因子准入过滤（v2.104.0+88） | `[L3] L2 评审过滤 [DuckDB]: 剔除 {n} 个评审驳回/未评审因子，保留 {n}/{m}` | info |
| L3 信号矩阵观测（plans/51 C3） | `[L3-SIGNAL]` 前缀：持久化/增量窗口追加/降级全量重算/形状防护/读失败兜底 | info/warning |
| L3 退役与信号契约观测（plans/57） | `retired_l3.py warn_if_retired` import 期告警 + `scan_l3_retirement.py` 只读扫描 + RD 侧 `degraded: fts_signal_unavailable` | warning |
| 子链/制度 Gate/Beta/拥挤度观测 | 质量报告四网合一段（subchain_exposure / subchain_gate_distribution / subchain_quality_matrix / subchain_gate_scale）+ Prometheus `fts_beta_*`/`crowding_*` 指标（灰度功能默认关，开启时按观测点记录） | warning |

### 指标字段（Prometheus）

```
fts_loop_status{loop="L1|L2|L3"} 0~4      # 0=unknown 1=running 2=paused 3=completed 4=circuit_broken
fts_tokens_consumed{loop="L1|L2|L3"}       # counter
fts_factor_decay_ic{factor_id=..}          # 因子衰减监控 IC（A.2）
fts_quality_score{factor_id=..}            # 因子质量评分（A.1）
fts_regime_confidence{market=..}           # 制度置信度（28-T10）
fts_regime_entropy_norm{market=..}         # 制度后验归一化熵
fts_regime_exposure_scale{market=..}       # 置信度仓位缩放因子
fts_regime_blend_hhi{market=..}            # 制度概率分布集中度 HHI
fts_regime_name{market=..,regime=..}       # 当前制度名（1=生效）
fts_dq_completeness                        # 数据完整性指标（B.1）
fts_beta_state / fts_beta_scale            # Beta 层状态/敞口倍率（plans/55）
crowding_scale / crowding_state           # 拥挤度倍率/状态（plans/56）
```

`fts_regime_*` 由 `MetricsRegistry.record_regime_metrics` 落盘（PortfolioLoop Step 2.5 上报，失败仅告警不阻断）。

---

## 4. Elite 因子追踪

`EliteFactorTracker`（`fts/monitor/elite_tracker.py`）：`update()` 更新追踪记录 / `get_decaying(max_consecutive=4)` 检测连续低 IC / `auto_retire()` 自动退役（含衰减分级 decay_grade：normal/observe/retired，滚动 6M IC 斜率）/ `report()` 追踪报告。

---

## 5. 日志规范

| 级别 | 使用场景 |
|:-----|:---------|
| ERROR | 数据不可用、熔断触发、编译失败 |
| WARNING | 降级回退、可选依赖缺失、部分失败 |
| INFO | 循环启动/完成、因子晋升、配置加载 |
| DEBUG | 详细执行流程、参数值 |

格式：`%(asctime)s [%(levelname)s] %(name)s: %(message)s`（含 trace_id）。

---

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|:-------------|:-----------|:---------|
| `fts/factor_engine/state.py:generate_trace_id` | trace_id 格式为 `{prefix}_{8hex}_{timestamp}` | 单元测试 |
| `fts/monitor/http_server.py:MetricsHTTPServer` | 端点 /health /metrics 返回预期格式 | `pytest tests/test_http_server.py` |
| `fts/monitor/elite_tracker.py:EliteFactorTracker` | auto_retire 正确移除衰减因子 | 单元测试 |
| `fts/factor_engine/monitor.py:check_loop` | 读取 state.json 返回 LoopStatus | 单元测试 |
