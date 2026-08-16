# FTS 韧性设计

> 版本: v2.104.0+90
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
