# FTS 韧性设计

> 版本: v3.0.0+6
> 最后更新: 2026-08-05

---

## 1. 韧性策略总览

| 策略 | 机制 | 触发条件 | 恢复方式 |
|:-----|:-----|:---------|:---------|
| 熔断器 | 三阈值自动停止 L2 演化 | token 超限 / 连续低 IC / 高失败率 | 人类审查后更新 Program.md 恢复 |
| 原子持久化 | 临时文件 + os.replace | 进程崩溃写入中 | 降级读取 `.bak.*` 备份 |
| 备份轮转 | 保留最近 3 个 `.bak.*` 文件 | 每次 `atomic_write_state()` | 主文件损坏时自动回退 |
| 静默降级 | 可选依赖惰性导入 | 依赖未安装 | 自动回退 Mock/合成数据 |
| 进程看门狗 | 重启策略 | 30s 内 3 次重启 | 5min 熔断后不再重启 |
| 安全沙箱 | AST 预验证 + 受限 __builtins__ | 因子代码违反安全规则 | 拒绝执行 + 记录失败原因 |

## 2. 熔断器 (Circuit Breaker)

### L2 熔断条件

| 条件 | 阈值 | 说明 |
|:-----|:------|:-----|
| Token 预算耗尽 | `nightly_token_limit` (200K) | 单夜 LLM token 超限 |
| 连续低 IC | `circuit_breaker_consecutive_low_ic` (3 代) | 连续 3 代 IC < 0.01 |
| 失败率超限 | `circuit_breaker_failure_rate` (90%) | 代内失败因子比例超限 |

### L1 熔断条件

| 条件 | 阈值 |
|:-----|:------|
| 单日 token 超支 | >2x daily_token_limit (60K，plans/41 D1 上调) |
| 失败率超限 | > circuit_breaker_failure_rate (95%) |
| 连续低质量候选 | > circuit_breaker_consecutive_low_quality (5 次) |

### 种子因子豁免规则

种子因子评估不计入熔断计数器。种子因子是已知起点，仅通过简单 IC/Sharpe 筛选（IC≥0.03, Sharpe≥1.5）后直接晋升 elite，跳过 Verifier 三级验证。种子评估的通过/失败不计入 `evaluated`/`promoted` 计数器，不影响熔断判定。

### Regime 降级路径（28 计划，2026-08-11）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| regime 无 `regime_probs`（旧格式/异常） | 概率混合回退硬查表（`regime_adaptive_weight_adjustment` 非 blend 分支） | 检测器恢复输出 probs 后自动启用 blend |
| `probability_mix=False` 配置关闭 | 恒走硬查表 | 配置开启后生效 |
| `confidence_scale=False` 或 regime 为 None/异常 | `exposure_scale` 恒 1.0（`_compute_exposure_scale` try/except 兜底） | 配置开启/数据恢复后生效 |
| 熵标定输入无 probs | `RegimeConfidenceCalibrator.calibrate` 直通 `confidence`（视为确定性分布） | — |
| 指标上报失败 | `record_regime_metrics` 异常仅告警不阻断主流程（Step 2.5 try/except） | 下一轮自动重试 |
| HMM 检测失败（hmmlearn 不可用/训练异常） | 5 层降级链逐级回退（multi_hmm → msm → hmm → rule → oscillate/0.5） | 依赖/数据恢复后自动升级 |

### 子链方向 Gate 回退路径（plans/48，v2.104.0+111）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| Gate 判定/应用异常 | 信号管线 Step 3h1 try/except 捕获 → 跳过 Gate 保持现状得分（不阻断主流程），日志 `[子链 Gate] 应用失败（跳过，保持现状）` | 代码/数据修复后下一轮自动恢复 |
| Gate 灰度未开启 | `l3.regime_gating.enabled=false` 或未传 `--enable-regime-gating` → 全局软票 + 方向偏置原逻辑（与现状逐位一致） | 观察后开启灰度，回退一键关闭 |
| 子链 regime 检测失败（energy L3 Step 2.5） | `detect_all` 异常 → `subchain_regimes=None` 回退全局 regime 倍率（`regime_adaptive_weight_adjustment` 缺省分支） | 数据恢复后自动启用子链路由 |
| 子链数据不足/无检测子链 | `build_subchain_gates` 缺检测子链 → neutral（不误杀）；因子无 scope/all/unknown/部分链 → 回退全局倍率 | 子链数据积累后自动下钻 |
| 盲测池/无子链归属品种 | `blind_default="avoid"`（默认回避）不参与 Gate 决策 | 配置 `blind_default="neutral"` 保留 |
| Gate 分布构建失败（D3） | Step 2.5 异常仅告警，质量报告 `subchain_gate_distribution` 为空段 | 下一轮自动重试 |

### L0 宏观 Beta 层回退路径（plans/55，v2.105.0+22）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| Beta 层灰度未开启 | `l3.regime_beta_layer.enabled=false` 或未传 `--enable-regime-beta` → 不检测不偏置（与现状逐位一致） | 观察后开启灰度，回退一键关闭 |
| 金融期货面板数据不足（<2 品种 / 指数 <2 行 / 无 trend+vol） | `BetaDetector.detect` 返回 `unknown` → `compute_beta_scale=1.0`、`apply_beta_bias` 不干预（零行为变更） | 数据恢复后自动检测 |
| 股债比缺口/计算异常 | `risk_pref_z=NaN`（ffill + min_periods 兜底后仍不足）→ 降级为 trend+vol 2 信号投票，不崩溃 | 数据积累后自动恢复 3 信号 |
| Beta 检测/消费异常 | 信号管线 Step 3h1.5 与 portfolio_loop Step 2.5 try/except 捕获 → 回退 scale=1.0 不阻断主流程，日志 `[Beta 层] 应用失败（跳过，保持现状）` / `[L3] Step 2.5: Beta 层计算失败，回退 scale=1.0` | 代码/数据修复后下一轮自动恢复 |
| Beta 状态置信度不足 | 软投票置信度 < `min_confidence` → 视为 RANGE_BOUND（不偏置） | 信号一致性恢复后自动判定 |
| 实盘风控 beta_state 注入失败 | `RiskManager` 异常捕获 → 回退常量（不阻断初始化） | 修复后自动注入 |
| 二期未接线（四象限慢层/实盘 http_server 传 beta_state） | 当前不参与消费（保持现状） | 二期按 plans/55 排期接线 |

### 拥挤度体系化回退路径（plans/56，v2.105.0+25）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| 拥挤度灰度未开启 | `l3.regime_crowding.enabled=false` 或未传 `--enable-regime-crowding` → 不检测不干预（与现状逐位一致） | 决策门校准通过后开启，回退一键关闭 |
| 面板数据不足（<2 品种 / 无 close/volume） | `compute_crowding_signals` 返回 score=0.0/neutral/fallback → 联合门控 scale=1.0、方向偏置不干预（零行为变更） | 数据恢复后自动计算 |
| 单信号失败（如 OI 缺失） | 对应信号降级跳过（score 按可用信号归一化），不阻断合成 | 数据补齐后自动恢复 |
| 拥挤度检测/消费异常 | 组合层 Step 2.5 与信号管线 Step 3h1.6 try/except 捕获 → 回退 scale=1.0 不阻断主流程，日志 `[L3] Step 2.5: 拥挤度计算失败` / `[拥挤度] 应用失败` | 代码/数据修复后下一轮自动恢复 |
| 决策门未通过 | **灰度保持关闭**（当前状态）：高拥挤样本不足 + 事件研究命中率 0%，待阈值校准 | 校准 6 信号分位 + high_crowding 后重跑决策门 |

### QuantData 权威主链路降级路径（v2.105.0+32 规划，GAP-156）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| QuantData 库不可达（DuckDB 打开失败/文件缺失/权限） | `QuantDataProvider` 熔断（连续失败 3 次 + 冷却）→ 读取缓存 DUCKDB_CACHE 兜底 → 仍缺则 SYNTHETIC 保证可运行（v3.0.0+1 起天勤/通达信/AKShare 已从默认链移除，不静默降级外部网络源） | 数据/权限恢复后下一轮自动升级回 QuantData |
| QuantData 品种缺失（88 品种 vs FTS 82，映射外品种） | 映射校验失败 → 该品种无数据（不阻断面板构建），如实标注缺失 | QuantData 补品种后自动覆盖 |
| QuantData 无 settle/amount/vwap（kline_daily 仅 OHLCV+OI） | Provider 返回 NaN（v3.0.0+1 起天勤 TQSDKEnhanceSource 已移除；hold 由 QuantData open_interest 权威覆盖 L0、oi_change 由 QuantDataProvider 差分自算）→ 仍缺则典型价/均量代理，**标注非权威来源**（GAP-158，不硬拒） | QuantData 侧补 settle 采集后可升 L0 |
| 期限结构权威构建失败（continuous_map 缺映射日/近远月对齐不足） | 当日 term_spread/roll_yield 缺失置 NaN，D15 算子自动跳过（数据不足不误报），不阻断主流程 | 映射数据补齐后自动恢复 |
| 复权序列异常（QuantData 连续序列与 FTS RollCalendar 交叉验证偏离 >0.5%） | aggregator cross_check 记录分歧 `data/data_source_disagreements.jsonl`，按现有分歧处理策略降级，不静默采信 | 差异分析后统一口径 |

### FTS 信号接口降级熔断（plans/57，v3.0.0，RD 消费侧容错底线）

RD（Regime-Driven）经因子信号契约 v1 拉取 FTS 信号矩阵失败时的降级语义（design/F.3 §7）：

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| FTS 信号拉取失败（连续 N=3 次） | RD `signal_client` 熔断 → 冷却 5 分钟 → 降级到 RD 本地 11 因子规则法（纯本地全链路可运行） | 冷却后自动重试，恢复后回 FTS 信号消费 |
| 信号过期（end_date < 决策日-1） | 视为过期走降级（同上），报告注明 `degraded: fts_signal_unavailable` | 下一交易日 FTS 增量追加后就绪 |
| schema_version 不兼容 | RD 拉取时校验告警 → 降级本地规则法，不静默消费异构契约 | FTS 契约版本对齐后自动恢复 |
| FTS 完全不可用 | RD 无 FTS 也可完整运行 = 天然回滚通道，是阶段 1 安全双轨的底层保障 | — |

### 子链质量矩阵与生命周期回退路径（plans/49，v2.104.0+112）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| 质量矩阵灰度未开启 | `l3.subchain_quality.enabled=false` → 评审质检/生命周期回退全链原逻辑（Q10 跨板块方向一致、全链 IC 衰减，与现状逐位一致） | 观察后开启灰度，回退一键关闭 |
| 子链 IC 时序样本不足 | 退化检测期数 < `min_periods`（默认 5）→ 返回 None（审计 skipped，不误判），不触发 scope_shrink/degrade | 每期评审写一行，期数积累后自动判定 |
| 无子链画像因子（股票/无 scope） | `compute_subchain_degradation` 无 ever_effective → keep（因子状态不变）；`judge_q10_subchain` 无 chain 数据 → 回退原 Q10 语义 | 晋升/评审落库首行后自动下钻 |
| scope 收缩过激（单期噪声误判失效链） | 需连续期数确认 + 冷却期 `cooldown_days`（30 交易日）保护才收缩；scope 回滚需重审达标 | 冷却期后重审，达标自动回 active 并重评子链画像 |
| 质量矩阵落库失败 | `save_subchain_quality` 异常仅告警不阻断评审/晋升（E.4 短连接 + 幂等 UPSERT） | 下一轮评审自动重试写入 |
| 质量矩阵快照构建失败（D3） | 质量报告 `subchain_quality_matrix` 段为空，主流程不阻断 | 下一轮自动重试 |
| 单链特异放行误判 | 机审放行仍过完整 QA 门禁（audit/评分卡/多重检验/Q1Q10）；Sharpe < min_sharpe 不放行 | 质检门禁兜底，人审复核通道保留 |

### 子链 Gate 权重源头回退路径（plans/50，v2.104.0+113）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| Gate 灰度未开启 | `l3.regime_gating.enabled=false` 或未传 `--enable-regime-gating` → L3 调制矩阵不含 gate_scale（纯质量×幅度），`subchain_gate_scale` 段为空 | 显式打开灰度后自动并入 |
| 无调制矩阵（`enable_subchain_weight` 未开） | Step 2.5 检测到 Gate 但 `self._subchain_modulation` 为空 → 仅记录 `subchain_gate_distribution`/`subchain_gate_scale` 观测段，不改变任何权重输出（依赖语义：权重层 Gate 需要子链调制通道） | 开启子链权重灰度后 Gate 并入生效 |
| 非 energy 市场 | Step 2.5 子链 regime 检测跳过 → 无 Gate 并入（全链原逻辑） | 扩展到其它产业链时子链定义入 futures_universe.yaml 自动生效 |
| Gate 并入失败（异常） | `_merge_gate_scale_into_modulation` 异常仅告警，保持观测语义（`subchain_gate_scale={}`），不阻断主流程 | 代码修复后下一轮自动恢复 |
| 双重惩罚 | 权重层 gate_scale 已回避 avoid 链（系数 0/ratio）→ 信号层 Step 3h1 对 0 得分跳过 / soft 链不二次缩放（复用 plans/48 B3 语义） | 乘性串联天然防重复 |

### 评审质检 apply 落库影子校验（v2.105.0+18，反沉降通道）

`l2_energy_qa_review_job` 灰度阀门 `FTS_ENERGY_QA_REVIEW_APPLY`（默认 "0" dry-run）落库前强制一致性断言（复用 l3_signal_service `_dates_digest` 面板指纹）：

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| 无基线（未跑过 dry-run/apply） | apply=True 拒绝落库（RuntimeError），不写库不改状态 | 先以 `FTS_ENERGY_QA_REVIEW_APPLY=0` 跑一次生成基线 |
| 同面板指纹下判定漂移 | 逐因子 (factor_id→decision) 与基线不一致 → 拒绝落库，日志列出漂移清单 | 人工确认漂移原因（代码/参数变更），复核后重跑更新基线 |
| 面板指纹变化（数据漂移） | 跳过一致性断言（warning），放行落库 | 数据恢复稳定后下一轮自动恢复断言 |
| 落库判定误伤（degraded 误降） | 冷却期 30 日回归自动恢复 active（JSON 自 `_deprecated` 移回） | 自动（`_scan_cooldown_regression`） |
| 基线写入失败（state.db 不可用） | 基线断言无法读取 → 保守拒绝落库 | state.db 恢复后自动恢复 |

> 反沉降通道缺口登记：retire 仍为终态（误 retire 无自动恢复路径）、无整批快照回滚命令（GAP-149~151 相邻排期）。

### 写路径与数据契约校验（GAP-150/151，v2.105.0+20，严格/分级模式）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| 写路径未登记 storage_landscape（GAP-150） | `warn_unregistered_write` 严格模式默认开启，**四写入口全覆盖（v2.105.0+21）**：`FactorRepository`（因子库）/ `StateKVStore.upsert`（状态库）/ `l3_signal_service.persist`（信号库）/ `data_futures._write_scope`（行情库）默认路径未登记抛 ValueError 阻断；env `FTS_STORAGE_WRITE_STRICT=0` 回退 warning；显式注入豁免 | 新增写路径先登记 `_data/storage_landscape.yaml` |
| 行情加载字段缺失/全空（GAP-151 分级） | 核心字段（date/open/high/low/close/volume）→ error+跳过（`_check_kline_field_integrity` 返回 False，宁缺毋滥）；增强字段（hold/settle/pre_settle）→ warning+代理降级显式暴露（`_read_kline_cache` 代理填充前） | 修复数据源/缓存契约；核心缺失跳过、增强代理为显式降级非静默 |
| 因子状态写入口非法值（GAP-149） | `_validate_catalog_status` 抛 ValueError（create/update/update_factor_status 三入口） | 写合法枚举值；存量非法状态扫描确认归零（energy/futures 实测 0，合法集含 archived/deprecated） |

### 熔断恢复流程

1. 审查 `memory/evolution/state.json` 中 `last_error`
2. 分析经验链 `memory/experience/failure/` 中的失败原因
3. 更新 `Program.md` 中的 `circuit_breakers_reviewed: true`
4. 重新执行演化命令

## 3. 原子持久化

所有状态文件通过 `atomic_write_state()` 写入：

```
state.json          ← 最新版本（写入时先生成 .tmp 再原子 rename）
state.json.bak.0    ← 上一次写入
state.json.bak.1    ← 上上一次写入
state.json.bak.2    ← 上上上一次写入
```

文件: `fts/core/atomic.py`

## 4. 静默降级 (Graceful Degradation)

| 可选依赖 | 缺失时行为 |
|:---------|:-----------|
| `akshare` | 回退合成 OHLCV 数据 |
| `optuna` | Micro 演化跳过，使用默认参数 |
| `openai` / `anthropic` | 回退 `MockLLMClient` |
| `apscheduler` | `SchedulerEngine.start()` 返回 False |
| `watchdog` | `HotSwapWatcher` 静默 no-op |

系统在零可选依赖安装的情况下仍可端到端运行（使用 MockLLMClient + 合成数据）。

### 数据缺失降级（v2.58.0，GAP-046）

| 场景 | 降级行为 |
|:-----|:---------|
| `contract_kline` 表缺失 / 无具体合约数据（旧库未同步） | `RollCalendar` 返回空换月日历；`get_ohlcv(adjusted=True)` 回退返回原始拼接序列（不报错、不阻断） |
| 换月日历构建异常（单品种数据残缺） | 跳过该品种复权，返回原始序列并记录 warning；不影响其他品种 |
| 展期成本数据缺失（切换日新旧合约价格不全） | 该次展期按 `roll_cost_bps` 配置系数计成本（默认 2.0 bps），不中断回测 |

### 回测真实性仿真降级（v2.59.0，GAP-F02）

| 场景 | 降级行为 |
|:-----|:---------|
| 涨跌停日（close 单日涨跌幅 ≥ `futures_limit_pct`） | 当日持仓保持上一交易日（无法成交），统计为被拦截成交；`backtest_trade_filter=false` 时跳过拦截（回归兼容） |
| 停牌日（volume==0 或行情缺失） | 当日持仓保持上一交易日，统计为被拦截成交；不传播 NaN |
| 板块中性化数据缺失（品种无板块映射） | 归入 UNKNOWN 组参与去均值，不阻断评估；`futures_neutralization=false` 时跳过中性化（GAP-F03） |

### 实盘执行兜底（v2.60.0，GAP-F01）

| 兜底项 | 说明 |
|:-------|:-----|
| 下单重试 | 网关提交失败自动重试（可配置次数/间隔），连续失败转异常订单（REJECTED）并告警 |
| 下单超时 | 超过超时阈值未确认 → 标记异常并回查网关状态，防止错单/漏单 |
| 状态机非法转移 | `OrderState` 拒绝非法转移（如 FILLED→PENDING），触发审计日志 |
| 人工干预优先级 | `InterventionController`（紧急暂停/一键平仓）权限高于所有自动化逻辑，可随时拦截后续信号 |
| 止损单触发 | 持仓级止损止盈单触发后生成平仓指令，风控不通过仍可执行（紧急保护优先） |

### 样本外强制降级（v2.60.0，GAP-F08）

| 场景 | 降级行为 |
|:-----|:---------|
| 数据长度不足（无法构建 WalkForward 窗口） | 跳过冷启动验证并在审计报告记录原因（数据不足），不静默放行 |
| `force_walkforward=false` | 跳过强制验证，审计 oos_consistency 回退 L1 单段 ICIR 近似，日志记录跳过原因 |
| WalkForward 窗口评估异常 | 该窗口跳过，剩余窗口继续；全部失败时判定不通过 |

### 保证金建模兜底（v2.60.0，GAP-F09）

| 场景 | 降级行为 |
|:-----|:---------|
| 品种未配置保证金率 | 使用默认保证金率 0.10（可配置），不阻断分配 |
| 保证金占用超 `max_margin_usage` | 触发强平风险告警，权重按可用保证金上限截断 |
| 协方差矩阵退化（风险平价） | 回退等权分配（既有行为） |

### 数据源降级加固（v2.60.0，GAP-F04）

| 场景 | 降级行为 |
|:-----|:---------|
| `mcp_enabled=false`（默认） | Wind/iFinD MCP 未启用，`_call_mcp` 返回 None，明确跳过增强字段查询（is_available=False），主路径走 DUCKDB_CACHE/TQ/AKSHARE |
| `mcp_enabled=true` 且已注入 handler | 直接调用注入的 MCP 客户端（`set_mcp_handler`） |
| `mcp_enabled=true` 但未注入 handler | 抛 RuntimeError 显式初始化报错，避免静默失败掩盖配置错误 |

### WorkFlow 执行韧性（v2.104.0+25，CTA 手册端到端工作流）

| 场景 | 机制 |
|:-----|:-----|
| 单阶段动作执行 | 后台线程 subprocess，`StageAction.timeout` 超时熔断（超时标记 failed 不挂死），stdout/stderr 全量捕获留痕 |
| 端到端执行 | 按阶段顺序推进、任一阶段失败即停（run 置 failed 可回放重试），`{factor_id}`/`{report_dir}` 动态占位符运行时解析（无 active 因子时显式报错不静默） |
| JSON 产物解析 | `--json` 输出提取失败回退整体/末行扫描，解析失败仅产物为 None 不阻断执行 |
| 批次状态持久化 | `WorkflowStore` SQLite WAL 双表（workflow_runs + stage_runs），崩溃/重启后可回放历史与继续审计 |
| 前端静态托管 | `web/workflow_ui/dist` 未构建时返回引导提示（`npm run build`），不 500 |

### 数据源降级加固（v2.60.0，GAP-F04）

| 场景 | 降级行为 |
|:-----|:---------|
| `mcp_enabled=false`（默认） | Wind/iFinD MCP 未启用，`_call_mcp` 返回 None，明确跳过增强字段查询（is_available=False），主路径走 DUCKDB_CACHE/TQ/AKSHARE |
| `mcp_enabled=true` 且已注入 handler | 直接调用注入的 MCP 客户端（`set_mcp_handler`） |
| `mcp_enabled=true` 但未注入 handler | 抛 RuntimeError 显式初始化报错，避免静默失败掩盖配置错误 |

### DuckDB 并发模型（v2.86.0，GAP-056，design/E.1；v2.101.0 补充分库方案）

| 场景 | 机制 |
|:-----|:-----|
| 进程内并发写 | 单写者 `DuckDBWriter` + 进程内写锁，所有写操作串行，结构上消除 `ConcurrentTransactionException` |
| 批量写原子性 | `executemany`/`copy_from_records` 显式 `BEGIN/COMMIT` 包裹（DuckDB executemany 为逐条执行非单事务），任一条失败整批 `ROLLBACK`，不留半写入 |
| 读写互不阻塞 | 读走 `read_only=True` 短连接（E.4 S1 起），配合 `lock_configuration=true` 与写窗口（秒级短连接）共存；MVCC 快照使写提交期间读侧不受阻塞 |
| 跨进程写 | 写入口统一经 `fts/store/duckdb_lock.py` `duckdb_write_lock`（`data/.locks/*.duckdb.lock` filelock，msvcrt/fcntl 标准库）串行化——写窗口任意时刻至多一个（跨进程），结构上消除文件锁互抢（E.4 S1，2026-08-13） |
| 跨市场文件锁竞争 | 按市场（股票/期货）拆分独立 DuckDB 文件（`factor_catalog_stock.duckdb`/`factor_catalog_futures.duckdb`），物理隔离消除跨市场锁冲突 |
| 兼容降级 | `duckdb_single_writer=false` 回退旧多路径；`retry_on_conflict`/`AsyncWriteQueue`/`lock_configuration` 保留为防御兜底（不依赖其解决并发） |
| 锁配置降级 | 旧版 DuckDB 不支持 `lock_configuration` 时静默降级，由应用层写锁兜底 |
| L4 运行状态库（E.3 S2，2026-08-13） | `data/state.db` 后端切换 **SQLite WAL**（`StateKVStore`）：写连接存活期间外部只读**不受阻塞**（多读单写不互斥，解决演化进程持锁连只读亦被锁）；upsert 单事务包裹双表原子；跨进程写冲突由 `busy_timeout=5000` 等待而非失败 |
| L2/L3 连接生命周期（E.4 S1，2026-08-13） | 写连接一律**短生命周期**（`_write_scope` = filelock 互斥 + 写完即关，秒级，演化/同步进程其余时间零写连接）；读连接一律 `read_only=True` 短连接（`_open_read_conn`）；模块级常驻写连接（`_WRITER`/`_DB`/`_cache_conn`）已移除——读侧不再被长驻写连接阻塞 |

### 容错兜底

| 兜底项 | 说明 |
|:-------|:-----|
| 复权因子计算 | 切换日价格任一缺失 → 该换月事件跳过（不复权），异常因子不传播 NaN |
| 换月日历构建（v2.104.0+39 修复） | ① `contract_kline.date` 为 VARCHAR 时统一 `to_datetime` 规范化，修复 `_close_on` 日期比较失配导致全部换月事件误判"价格缺失"；② volume 无效行（缺失/0）不参与主力判定，消除假换月来回切换；③ 主判定与收盘价查找分离（volume 过滤仅作用于主力判定，价格查找用全量数据） |
| 展期事件序列 | 按日期排序，重复/逆序事件去重；单品种换月次数异常（>阈值）告警 |

### 张量化路径降级（plans/51 C3，v2.104.0+）

| 降级项 | 触发条件 | 回退行为 | 可观测性 |
|:-------|:---------|:---------|:---------|
| numba 内核（`numba_kernels.py`） | numba/llvmlite 缺失或版本冲突 / `FTS_OPS_NUMBA=false` | `enabled()` 双判定（依赖可用 + 开关），调用点回退 pandas/numpy 现值实现，零语义漂移 | import 失败 `logger.warning`（l3_signal_service/numba_kernels 模块级）；内核异常 `logger.warning`（rank/zscore/cvar 入口） |
| DuckDB 相关（`l3_signal_service` corr/持久化） | duckdb 缺失 / SQL 异常 / 库文件损坏 | `duckdb_corr_matrix` 回退 `_numpy_corr_matrix`；`persist/load/incremental` 外层 except 降级（不阻断主流程）；`load_or_build` 读失败 → 该批因子并入重算集（plans/51 A2） | persist 失败 `logger.warning`；load/增量失败 `logger.debug`；行数/读取降级重算 `logger.warning` |
| L3 信号矩阵增量库（plans/51 B1） | `FTS_L3_SIGNAL_STORE=false` / 配置读取失败 | `PortfolioLoop` 不激活 `signal_store`，回退纯 `build_signal_matrix` 全量构建，产出一致零漂移 | 构造未激活无日志（保持现状）；增量命中/重算见 `[L3-SIGNAL]` 日志（05-observability） |
| 增量窗口追加（plans/52，GAP-139） | 前缀不一致（历史修订/窗口收缩）/ meta `dates_digest` 缺失（旧库）/ 抽样对照验证不过 / `FTS_L3_SIGNAL_APPEND_WINDOW=false` | 该因子降级全量重算（A1 双哈希优先级更高：code/params 变化即全量），结果与全量逐位一致 | 前缀不符/验证失败 `logger.warning`；追加完成 `logger.info` |
| SignalCache 容量 | 条目超过 `l3_signal_cache_entries` 上限 | LRU 淘汰最久未使用项（plans/51 C3 起可观测） | 淘汰 `logger.debug`（累计数）；`stats()` 暴露 `evictions` |

## 5. 安全沙箱

`FactorExecutor` 执行 LLM 生成的因子代码时的安全机制：

| 机制 | 说明 |
|:-----|:------|
| 白名单导入 | 仅允许 numpy, pandas, scipy, statsmodels, talib, math, statistics |
| 黑名单名称 | 禁止 open, exec, eval, compile, __import__ |
| 黑名单模块 | 禁止 os, sys, subprocess, socket, ctypes |
| AST 预验证 | `validate_factor_code()` 在任何执行前检测违规 |
| 受限 __builtins__ | 仅暴露安全的数值/类型/迭代函数 |

文件: `fts/factor_engine/factor_program.py`

## 6. 进程看门狗

| 属性 | 值 |
|:-----|:---|
| 重启窗口 | 30 秒 |
| 最大重启次数 | 3 次 / 窗口 |
| 熔断时间 | 5 分钟 |
| 文件 | `fts/scheduler/watchdog.py` |

## 7. 热重载

| 属性 | 值 |
|:-----|:---|
| 监听库 | `watchdog`（可选）|
| 重载机制 | `importlib.reload` |
| 降级行为 | 库缺失时静默 no-op |
| 文件 | `fts/scheduler/hotswap.py` |

---

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|:-------------|:-----------|:---------|
| `fts/core/atomic.py` | 原子写入使用临时文件+os.replace | 代码审查 |
| `fts/factor_engine/evolution_loop.py:_check_circuit_breaker` | 三熔断条件检查 | 单元测试 |
| `fts/factor_engine/factor_program.py:validate_factor_code` | AST 预验证拒绝黑名单模块 | `pytest tests/factor_engine/test_factor_program.py` |
| `fts/scheduler/watchdog.py:ProcessWatchdog` | 3 次重启后熔断 5min | 单元测试 |
