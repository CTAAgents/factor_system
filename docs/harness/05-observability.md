# FTS 可观测性

> 版本: v3.0.0+10
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

### L3 子链差异化权重观测（plans/47 §D2，v2.104.0+109）

| 指标 | 来源 | 告警条件 |
|:-----|:-----|:---------|
| 子链权重暴露占比 | `PortfolioLoop` Step 2b 日志 `[L3] Step 2b: ... 子链暴露={...}` + 质量报告 `elite_final_quality_*.json` 的 `subchain_exposure` 段 | 单子链占比 > `l3.subchain_weight.max_exposure_ratio`（默认 0.5）→ warning |
| 子链调制启用状态 | Step 2b 日志 + `factor_weights.json` 的 `subchain_weights`/`symbol_chain` 字段 | 开启时字段存在、关闭时缺失（灰度可观测） |
| 子链画像落库 | `factor_catalog.metadata.subchain_ic_profile/scope/specific` | 非空即画像已生成（A2） |

### 子链×制度 Gate 观测（plans/48 §D3，v2.104.0+111）

| 指标 | 来源 | 告警条件 |
|:-----|:-----|:---------|
| 子链 Gate 分布 | `PortfolioLoop` Step 2.5 `[L3] Step 2.5: 子链 regime 检测完成（§C 路由）` 日志 + 质量报告 `elite_final_quality_*.json` 的 `subchain_gate_distribution` 段（各子链 decision：long/short/avoid/neutral） | 全子链 avoid（方向层全回避）→ warning（制度不明朗或检测异常） |
| 收益来源族激活画像 | `PortfolioLoop._regime_meta.subchain_return_source`（{子链: {regime, confidence, active_styles}}） | 仅 energy + Gate 开启时非空（灰度可观测） |
| Gate 启用状态 | `futures_signal_pipeline` Step 3h1 `[子链 Gate] 应用方向 Gate + 暴露缩放` 日志 + settings.yaml `l3.regime_gating.enabled` / CLI `--enable-regime-gating` | 开启时日志含 avoid 计数与暴露缩放品种数；关闭时无该段（与现状逐位一致） |

### L0 宏观 Beta 层观测（plans/55 §E，v2.105.0+22）

| 指标 | 来源 | 告警条件 |
|:-----|:-----|:---------|
| Beta 状态 | Prometheus `fts_beta_state`（RISK_ON/RISK_OFF/RANGE_BOUND/unknown，经 `record_regime_metrics(beta_state=...)` 落 `_regime_metrics.beta_state`）+ `PortfolioLoop._regime_meta.beta_state`（current_combo.json 落盘可追溯） | RISK_OFF 连续 N 日 + 组合未收缩敞口 → warning（Beta 层未生效） |
| Beta 敞口倍率 | Prometheus `fts_beta_scale` + `_regime_meta.beta_scale`（build_combo 乘性并入后落盘）+ 信号管线 Step 3h1.5 `[Beta 层] ... 多头 ×x / 空头 ×x` 日志 | RISK_OFF 时 `beta_scale > 0.9` → warning（压缩未生效） |
| Beta 信号明细 | 信号管线 `[Beta 层]` 日志（trend/risk_pref_z/置信度）+ BetaDetector 输出 `trend_score/vol_score/risk_pref_z` | `risk_pref_z` 恒 NaN（股债比缺口无法修复）→ warning |
| Beta 启用状态 | settings.yaml `l3.regime_beta_layer.enabled` / CLI `--enable-regime-beta` + 日志段存在性 | 开启时日志含 `[Beta 层]`；关闭时无该段（与现状逐位一致） |

### 拥挤度体系化观测（plans/56 §D，v2.105.0+25）

| 指标 | 来源 | 告警条件 |
|:-----|:-----|:---------|
| 拥挤度敞口倍率 | Prometheus `crowding_scale`（经 `record_regime_metrics(crowding_scale=...)` 落 `_regime_metrics.crowding_scale`）+ `_regime_meta.crowding_scale`（current_combo.json 落盘） | 高拥挤（score≥high_crowding）时 `crowding_scale > 0.6` → warning（联合门控未收缩） |
| 拥挤度状态 | Prometheus `crowding_state`（long/short/neutral）+ `_regime_meta.crowding_direction` + 信号管线 Step 3h1.6 `[拥挤度]` 日志（score/触发信号列表/多空因子） | long/short_crowded 触发时日志含方向因子；neutral 不干预 |
| 决策门状态 | `reports/energy/{date}/regime_crowding_predictive_power.md`（K-W p / 事件研究命中率误报率） | 决策门未通过期间灰度保持关闭（当前状态） |

### 子链质量矩阵观测（plans/49 §D3，v2.104.0+112）

| 指标 | 来源 | 告警条件 |
|:-----|:-----|:---------|
| 质量矩阵快照 | 质量报告 `elite_final_quality_*.json` 的 `subchain_quality_matrix` 段（各因子×子链最近期 effective 快照 + scope 变化历史；DuckDB `subchain_factor_quality` 时序表 SSOT） | 与 47 `subchain_exposure`（幅度层）/48 `subchain_gate_distribution`（方向层）三网合一；因子 effective 子链集合变化 → 复核 |
| 单元退化判定 | `compute_subchain_degradation` 输出 factor_status（keep/scope_shrink/degrade）+ `_shrink_scope` 剔除链写入 metadata.subchain_scope | 任一曾经 effective 子链判定失效 → 退化/收缩记录留痕（`factor_status_history`） |
| scope 动态收缩 | `metadata.subchain_scope` 对比晋升期基准（A2 落库）变化 | scope 变化 → Step 2b 调制矩阵重算日志 + 质量矩阵段标注 |
| 评审两级判定 | Q10/F6 输出 `q10_verdict`（consistent/subchain_specific/conflicted）+ avoid_chain 标记 | conflicted → flag；有效链集合漂移 > 阈值 → scope 复核标记 |
| 质量矩阵启用状态 | settings.yaml `l3.subchain_quality.enabled` + 评审日志（`_stage_degradation` 子链旁路） | 开启时退化判定按单元粒度、关闭时全链原逻辑（灰度可观测） |

### 子链 Gate 权重源头观测（plans/50 §B3，v2.104.0+113）

| 指标 | 来源 | 告警条件 |
|:-----|:-----|:---------|
| Gate 缩放系数 | 质量报告 `elite_final_quality_*.json` 的 `subchain_gate_scale` 段（各子链 gate_scale 快照：avoid-hard=0.0 / avoid-soft=ratio / 其余=1.0；并入调制矩阵后 `factor_weights.json` 的 `subchain_weights` 同步缩放） | 与 47 `subchain_exposure`（幅度）/48 `subchain_gate_distribution`（决策）/49 `subchain_quality_matrix`（质量）四网合一；avoid 链 gate_scale=0 且调制矩阵存在 → 权重源头已回避 |
| 调制矩阵合并 | `_merge_gate_scale_into_modulation` 应用日志 `[L3] Step 2.5: Gate 并入调制矩阵...` | 仅 energy + Gate 开启 + `enable_subchain_weight` 时生效；任一缺失 → 无该日志（观测语义，零行为变更） |
| 权重源头回避 | `factor_weights.json` 的 `subchain_weights`（avoid 链系数 = 0 或 ×soft_avoid_ratio） | 与信号管道 Step 3h1 硬 Gate 乘性串联（权重层已回避 → 信号层对 0 得分跳过，无双重惩罚） |

### 因果审查跳过告警（v2.105.0+4，修复旁路静默）

| 指标 | 来源 | 告警条件 |
|:-----|:-----|:---------|
| 因果审查跳过计数 | `_run_causal_validation` 异常分支日志 `因果验证异常已跳过 (累计 N, factor_id=...)`（evolution_futures / evolution_audit 两路径共用 `CausalValidator`，异常时返回 `skipped=True` + `error` 写入 `evaluation.causal_validation`） | 出现任意一次即 warning；run 收尾打印 `[evo] ⚠️ 因果审查跳过 N 个因子` 汇总（种子阶段 `_evaluate_and_promote_seeds` / 演化 `run()` 各一处），N>0 时人工核查因子参数契约 |
| 因果审查执行参数 | `CausalValidator.validate` 使用 `factor.get("params", {})` 执行因子（与评估链 evaluation_chain 口径一致） | 因子代码 `params['window']` 直取型若无法解析 → 上述跳过告警（根因已在 v2.105.0+4 修复，回归锚点 test_causal_validator.py 直取型用例） |

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
| `GET /workflow` | HTML | WorkFlow UI SPA（React 构建产物，web/workflow_ui/dist） |
| `GET /api/workflow/stages` | JSON | WorkFlow 阶段定义（11 阶段 + 质检闭环） |
| `GET /api/workflow/runs` / `runs/{id}` | JSON | 运行批次列表 / 批次详情（阶段动作记录含日志与 JSON 产物） |
| `GET /api/workflow/qa/board` | JSON | 质检状态看板（QA 7 状态分布 + 预警清单） |
| `POST /api/workflow/runs` / `runs/{id}/run_all` / `runs/{id}/stage/{s}/action/{a}/run` | JSON | 创建批次 / 端到端执行 / 单动作执行 |
| `data_level_monitor_job`（每日 04:00） | 日志 | 数据级质量监控（GAP-F06）：缺失率/异常值/复权一致性/多源分歧四维检查结果与告警计数（scheduler 任务 `fts.dlm`） |

### WorkFlow 全链路日志（v2.104.0+25）

WorkFlow 执行全程留痕（`fts.workflow` logger）：
- 批次创建/阶段推进/失败中止/完成均写日志（含 run_id 与阶段 id）
- 每阶段动作 stdout+stderr 全量捕获入库（`stage_runs.log`，超 20KB 截断），JSON 产物单独入库（`stage_runs.output`）供 UI 展示
- 批次状态由 `_sync_run_status` 按 stage_runs 汇总（running/failed/success），SQLite WAL 持久化（`data/workflow.db`）崩溃可回放

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

### L3 因子准入过滤日志（v2.104.0+88）

`load_elite_factors`（portfolio_loop.py）DuckDB 路径按 L2 质检评审结果硬过滤（仅 `factor_reviews.decision='approved'` 因子参与权重重算）时输出：
- `[L3] L2 评审过滤 [DuckDB]: 剔除 {n} 个评审驳回因子 (decision=rejected): ...`——评审驳回因子被拦截（如评审驳回仍 is_elite 的存量因子）
- `[L3] L2 评审过滤 [DuckDB]: 剔除 {n} 个未评审因子 (factor_reviews 无 approved 记录): ...`——无评审记录因子被拦截
- `[L3] L2 评审过滤 [DuckDB]: 保留 {n}/{m} 因子 (decision=approved)`——准入统计
- `[L3] JSON 兜底路径 [{market}]: 无法校验 L2 阶段质检评审（JSON 无 factor_reviews 状态），仅按质量门槛+影子池过滤放行 {n} 个因子`——JSON 兜底降级路径告警（历史退役路径，生产 L3 走 DuckDB）

### 评审质检阀门日志（v2.104.0+89）

评审质检作为独立 L2→L3 阀门模块，就地审核 / L3 池巡检 / 机审门禁输出：
- `[review] 因子 {id} 审查决定: {decision} (comment=..., reviewer=...)`——就地审核/人审/机审决定落库（approved/rejected）
- `[review] L3 池巡检 [{market}]: 扫描 {n} 个 approved 因子，退回 {m} 个`——周度 L3 池巡检统计（功能 2，`l2_review_job` Step C，v3.0.0 起由每日 04:00 统一任务周日重量级分支执行）
- `[evo] 就地审核失败（不阻断晋升）: {name}: {err}`——晋升链就地审核非阻塞降级
- 机审门禁判定原因（`AutoReviewPolicy.classify`）：`质检记录缺失（...）宁缺毋滥转人审` / `6 项审计未通过` / `多重检验（Bonferroni）未通过` / `WalkForward 窗口 N < 2` / `质量评分卡 C 级` / `高IC筛查 C 级` / `Q1-Q10 入库质检未通过` / `疑似过拟合/未来函数（ic/sharpe 超上限）`

**评审质检落库影子校验日志（v2.105.0+18，反沉降通道）**：
- `[L2评审质检][energy] 影子校验通过: %d 因子判定与基线一致`——apply 前置断言通过（同面板指纹）
- `[L2评审质检][energy] 影子校验失败：同面板下判定漂移 %d 个因子，拒绝落库（...）`——判定漂移 → 拒绝落库（RuntimeError）
- `[L2评审质检][energy] 无 dry-run 基线，拒绝落库：请先以 FTS_ENERGY_QA_REVIEW_APPLY=0 运行一次生成基线`——无基线拒绝
- `[L2评审质检][energy] 面板指纹变化（数据漂移），跳过一致性断言: ...`——数据更新跳过断言（warning）
- 基线快照：state_kv `energy_qa_review/degradation_baseline`（每次运行 upsert + state_history 追加可回放，含 panel_digest/trace_id/apply/dispositions）

### 股票信号管道标准化与权重冻结日志（GAP-076 / GAP-072，v2.101.0；已随股票管线剥离至 fts-stock，2026-08）

> 以下为历史记录：`scripts/daily_signal_pipeline.py`（股票信号管道）已随股票管线剥离至 fts-stock（2026-08），主系统仅保留期货信号管道（`scripts/futures_signal_pipeline.py`）的同类结构化进度日志。

`scripts/daily_signal_pipeline.py` 每次运行输出结构化进度日志（stdout，含 trace_id 前置）：
- `[标准化] 截面 none/zscore/rank 已应用到 {n} 个因子（{m} 交易日）`——Step 4 前截面标准化方式与覆盖范围（GAP-076）
- `[权重冻结] 复用 {recomputed_at} 权重快照（{k} 因子），仅刷新因子值`——冻结日复用快照（GAP-072，快照含 `normalize` 字段记录重算日标准化口径）
- `[权重] 本周重算日: 权重已学习并保存快照 -> {path}`——重算日 Ridge 权重学习 + 快照落盘
- `[警告] 信号计算错误: {n} 次`——因子执行异常计数（不阻断主流程）

**期货信号管道日志（v2.105.0 架构对齐后，`scripts/futures_signal_pipeline.py`）**：
- `[L3 组合] 加载基础权重: {n} 因子 (n_factors={k})`——L3 组合权重加载（因子选择与基础权重权威源）
- `[Regime 权重] {regime}: 类别缩放 {cat} → Top: {top3}`——Regime 档位缩放权重调整结果（bull/bear/oscillate/high_vol/low_vol）
- `[警告] L3 组合因子在因子资产库中缺失，跳过: ...`——单因子缺失降级（不阻断）
- `[ERROR] L3 组合权重文件缺失/损坏/为空 ...（严格模式，退出）`——L3 组合不可用严格模式报错退出，不自行回退
- `[增量] 昨日快照因子组合与今日不一致（...），前后得分不可比，跳过增量计算`——跨因子组合可比性校验（v2.104.0+69）：因子集合变更（如 L3 重算 8→7 因子）时增量标记无效，防虚假信号增量；快照含 `factor_signature` + `semantics` 字段
- `[权重冻结]/[权重] 本周重算日` 等 Ridge 权重快照日志已随 v2.105.0 移除

### L3 信号矩阵观测（plans/51 C3，v2.104.0+）

L3 信号矩阵服务（`l3_signal_service.py`）日志前缀统一 `[L3-SIGNAL]`，关键日志：

| 日志 | 级别 | 含义 |
|:-----|:-----|:-----|
| `[L3-SIGNAL] 信号矩阵持久化: %d 因子 × %d 品种 × %d 日 [%s@%s]` | info | D 层写入成功（市场@end_date） |
| `[L3-SIGNAL] 增量窗口追加完成: %d 个因子仅重算新增交易日（plans/52）` | info | 增量窗口追加命中（跨日窗口推进仅算新增段） |
| `[L3-SIGNAL] %d 个可复用因子前缀不一致/元数据缺失，降级全量重算` | info | 前缀判定不通过（历史修订/旧库无 digest） |
| `[L3-SIGNAL] 增量窗口追加对照验证失败（factor=%s n_old=%d），降级全量重算` | warning | 抽样对照验证不过（窗口回退不足等）零漂移兜底 |
| `[L3-SIGNAL] 信号库行数 %d != 当前面板 %d 行 ... 降级重算 %d 个因子` | warning | A2 形状防护触发（窗口变化安全重算） |
| `[L3-SIGNAL] 信号库读取失败 ... 降级重算 %d 个可复用因子` | warning | 读取失败不丢信号 |
| `[L3-SIGNAL] 信号矩阵持久化失败（非致命）: ...` | warning | 写失败不阻断主流程 |
| `[L3-SIGNAL] DuckDB 相关性失败，回退 numpy: ...` | debug | corr SQL 降级（结果一致） |

**SignalCache 命中观测**（plans/51 C3）：`SignalCache.stats()` 返回 `{hits, misses, entries, evictions}`；
LRU 淘汰记录 `logger.debug`（高频场景不噪）。L3 增量复用效果由"二次运行日志对比重算因子数"验证。

### L3 退役与信号契约观测（plans/57，v3.0.0）

双系统切分后 FTS 侧新增观测点（design/F.3 §6 trace_id 贯穿）：

| 观测点 | 位置/含义 | 级别 |
|:-----|:---------|:-----|
| L3 组合侧退役告警 | `fts/factor_engine/retired_l3.py` `warn_if_retired`——import 期 DeprecationWarning + 调用点告警，存量调用不再新增 | warning |
| 退役调用图扫描 | `scripts/scan_l3_retirement.py`（只读）报告存量调用点分布，物理删除前核对依据 | info |
| 信号契约版本指纹 | RD 决策日志记录 `{end_date + dates_digest + schema_version}`，阶段 1 双轨对账按指纹精确定位消费版本 | info |
| 信号拉取 trace_id | RD `signal_client` 每次拉取记录 `{trace_id, market, factor_ids, end_date, rows}`（承接 FTS 父 trace_id） | info |
| 信号降级熔断 | RD 报告注明 `degraded: fts_signal_unavailable`（design/F.3 §7） | warning |
| 历史回填校验 | `verify_backfill_consistency` 拼接校验（重叠区容差 1e-8）通过/失败留痕 | info/warning |

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
