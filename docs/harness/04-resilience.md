# FTS 韧性设计

> 版本: v3.1.0+4
> 最后更新: 2026-08-20

---

## 1. 韧性策略总览

| 策略 | 机制 | 触发条件 | 恢复方式 |
|:-----|:-----|:---------|:---------|
| 熔断器 | 三阈值自动停止 L2/L1 演化 | token 超限 / 连续低 IC / 高失败率 | 人类审查后更新 Program.md 恢复 |
| 原子持久化 | 临时文件 + os.replace | 进程崩溃写入中 | 降级读取 `.bak.*` 备份 |
| 备份轮转 | 保留最近 3 个 `.bak.*` 文件 | 每次 `atomic_write_state()` | 主文件损坏时自动回退 |
| 静默降级 | 可选依赖惰性导入 | 依赖未安装 | 自动回退 Mock/合成数据 |
| 进程看门狗 | 重启策略 | 30s 内 3 次重启 | 5min 熔断后不再重启 |
| 安全沙箱 | AST 预验证 + 受限 __builtins__ | 因子代码违反安全规则 | 拒绝执行 + 记录失败原因 |

---

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
| 单日 token 超支 | >2x daily_token_limit (60K) |
| 失败率超限 | > circuit_breaker_failure_rate (95%) |
| 连续低质量候选 | > circuit_breaker_consecutive_low_quality (5 次) |

**种子因子豁免**：种子因子评估不计入熔断计数器（种子是已知起点，通过简单 IC/Sharpe 筛选直接晋升）。

### 熔断恢复流程

1. 审查 `memory/evolution/state.json` 中 `last_error`
2. 分析经验链 `memory/experience/failure/` 中的失败原因
3. 更新 `Program.md` 中的 `circuit_breakers_reviewed: true`
4. 重新执行演化命令

---

## 3. 主链路降级路径（当前生效）

### QuantData 权威主链路降级（v2.105.0+32，GAP-156）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| QuantData 库不可达 | `QuantDataProvider` 熔断（连续失败 3 次 + 冷却）→ 读取缓存 DUCKDB_CACHE 兜底 → 仍缺则 SYNTHETIC 保证可运行（天勤/通达信/AKShare 已从默认链移除，不静默降级外部网络源） | 数据/权限恢复后自动升级回 QuantData |
| 品种缺失 | 映射校验失败 → 该品种无数据（不阻断面板构建），如实标注缺失 | QuantData 补品种后自动覆盖 |
| 无 settle/amount/vwap | Provider 返回 NaN → 典型价/均量代理，**标注非权威来源**（GAP-158，不硬拒）；hold 由 QuantData open_interest 权威覆盖、oi_change 差分自算 | QuantData 侧补 settle 采集后可升 L0 |
| 期限结构构建失败 | 当日 term_spread/roll_yield 缺失置 NaN，D15 算子自动跳过，不阻断主流程 | 映射数据补齐后自动恢复 |
| 复权序列异常 | aggregator cross_check 记录分歧 `data/data_source_disagreements.jsonl`（偏离 >0.5%），按分歧处理策略降级，不静默采信 | 差异分析后统一口径 |

### FTS 信号接口降级熔断（plans/57，v3.0.0，RD 消费侧容错底线）

RD（Regime-Driven）经因子信号契约 v1 拉取 FTS 信号矩阵失败时的降级语义（design/F.3 §7）：

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| 信号拉取失败（连续 N=3 次） | RD `signal_client` 熔断 → 冷却 5 分钟 → 降级到 RD 本地 11 因子规则法（纯本地全链路可运行） | 冷却后自动重试，恢复后回 FTS 信号消费 |
| 信号过期 / schema 不兼容 | 视为过期走降级，报告注明 `degraded: fts_signal_unavailable`，不静默消费异构契约 | 下一交易日 FTS 增量追加后就绪 |
| FTS 完全不可用 | RD 无 FTS 也可完整运行 = 天然回滚通道（阶段 1 安全双轨底层保障） | — |

### 写路径与数据契约校验（GAP-150/151，严格/分级模式）

| 场景 | 降级行为 | 恢复方式 |
|:-----|:---------|:---------|
| 写路径未登记 storage_landscape | `warn_unregistered_write` 严格模式默认开启，四写入口全覆盖（FactorRepository / StateKVStore.upsert / l3_signal_service.persist / data_futures._write_scope）未登记抛 ValueError 阻断；`FTS_STORAGE_WRITE_STRICT=0` 回退 warning；显式注入豁免 | 新增写路径先登记 `_data/storage_landscape.yaml` |
| 行情加载字段缺失/全空 | 核心字段（date/open/high/low/close/volume）→ error+跳过（宁缺毋滥）；增强字段（hold/settle/pre_settle）→ warning+代理降级显式暴露 | 修复数据源/缓存契约 |
| 因子状态写入口非法值 | `_validate_catalog_status` 抛 ValueError（create/update/update_factor_status 三入口） | 写合法枚举值（9 状态合法集） |

### 评审质检 apply 落库影子校验（v2.105.0+18，反沉降通道）

`FTS_ENERGY_QA_REVIEW_APPLY`（默认 "0" dry-run）落库前强制一致性断言（复用 `_dates_digest` 面板指纹）：无基线 → 拒绝落库；同面板指纹下判定漂移 → 拒绝落库并列出漂移清单；面板指纹变化（数据漂移）→ 跳过断言放行；误降级经冷却期 30 日回归自动恢复。**缺口登记**：retire 仍为终态（误 retire 无自动恢复路径）、无整批快照回滚命令。

---

## 4. 降级路径（历史 GAP 落地）

### 数据缺失降级（GAP-046）

- `contract_kline` 缺失 → `RollCalendar` 返回空日历，`get_ohlcv(adjusted=True)` 回退原始拼接序列。
- 换月日历构建异常（单品种数据残缺）→ 跳过该品种复权，返回原始序列并告警。
- 展期成本数据缺失 → 按 `roll_cost_bps`（默认 2.0 bps）计成本，不中断回测。
- **复权因子计算（v3.1.0+3 根治落地）**：主链路 QuantData 已复权不二次复权；降级链切换日价格缺失 → 向前回溯最近共同交易日（默认窗口 20 交易日，可配置 `backfill_days`）计算 adj_ratio；窗口内仍无共同价格才跳过换月事件（不复权），异常不传播 NaN。

### 回测真实性仿真降级（GAP-F02）

涨跌停日（单日涨跌幅 ≥ `futures_limit_pct`）→ 持仓保持上一交易日，统计为被拦截成交；停牌日（volume==0）→ 同；板块中性化数据缺失 → 归 UNKNOWN 组，不阻断评估。

### 实盘执行兜底（GAP-F01）

下单重试（可配置次数/间隔，连续失败转 REJECTED 告警）/ 下单超时回查网关状态 / `OrderState` 非法转移拦截 / `InterventionController` 紧急暂停·一键平仓（权限高于自动化）/ 止损单触发后风控不通过仍可执行（紧急保护优先）。

### 样本外强制降级（GAP-F08）

数据不足无法构建 WalkForward → 跳过冷启动验证并在审计报告记录原因（不静默放行）；`force_walkforward=false` → 跳过强制验证回退 L1 单段 ICIR 近似；窗口评估异常 → 该窗口跳过，全部失败判定不通过。

### 保证金建模兜底（GAP-F09）

品种未配置保证金率 → 默认 0.10；占用超 `max_margin_usage` → 强平风险告警 + 权重截断；协方差矩阵退化 → 回退等权分配。

### MCP 数据源降级（GAP-F04）

`mcp_enabled=false`（默认）→ `_call_mcp` 返回 None 明确跳过增强字段；`true` 已注入 handler → 直接调用；`true` 未注入 → 抛 RuntimeError 显式报错（避免静默失败掩盖配置错误）。

---

## 5. DuckDB/SQLite 并发模型（E.1/E.3/E.4）

| 场景 | 机制 |
|:-----|:-----|
| 进程内并发写 | 写连接一律**短生命周期**（`_write_scope` = filelock 互斥 + 写完即关，秒级）；模块级常驻写连接（`_WRITER`/`_DB`/`_cache_conn`）已移除 |
| 跨进程写 | 统一经 `fts/store/duckdb_lock.py` `duckdb_write_lock`（`data/.locks/*.duckdb.lock` filelock）串行化 |
| 读写共存 | 读连接一律 `read_only=True` 短连接 + `lock_configuration=true`；MVCC 快照使写提交期间读侧不受阻塞 |
| 批量写原子性 | `executemany`/`copy_from_records` 显式 `BEGIN/COMMIT` 包裹，任一条失败整批 `ROLLBACK` |
| 跨市场文件锁竞争 | 按市场拆分独立 DuckDB 文件，物理隔离消除跨市场锁冲突 |
| L4 状态库 | `data/state.db` SQLite WAL（多读单写不互斥）；跨进程写冲突 `busy_timeout=5000` 等待而非失败 |

---

## 6. 张量化路径降级（plans/51 C3）

| 降级项 | 触发条件 | 回退行为 |
|:-------|:---------|:---------|
| numba 内核 | numba/llvmlite 缺失或版本冲突 / `FTS_OPS_NUMBA=false` | `enabled()` 双判定，调用点回退 pandas/numpy 现值实现，零语义漂移 |
| DuckDB corr/持久化 | duckdb 缺失 / SQL 异常 / 库损坏 | `duckdb_corr_matrix` 回退 `_numpy_corr_matrix`；persist/load 外层 except 降级；读失败 → 并入重算集 |
| 增量窗口追加（plans/52） | 前缀不一致 / dates_digest 缺失 / 验证不过 | 该因子降级全量重算（A1 双哈希优先级更高），结果与全量逐位一致 |
| SignalCache 容量 | 超过 `l3_signal_cache_entries` 上限 | LRU 淘汰最久未使用项 |

---

## 7. 安全沙箱

`FactorExecutor` 执行 LLM 生成的因子代码时的安全机制（`fts/factor_engine/factor_program.py`）：白名单导入（numpy/pandas/scipy/statsmodels/talib/math/statistics）；黑名单名称（open/exec/eval/compile/`__import__`）；黑名单模块（os/sys/subprocess/socket/ctypes）；AST 预验证 `validate_factor_code()`；受限 `__builtins__`。

## 8. 进程看门狗 / 热重载

- **看门狗**（`fts/scheduler/watchdog.py`）：重启窗口 30s / 最大 3 次 / 熔断 5min。
- **热重载**（`fts/scheduler/hotswap.py`）：`watchdog`（可选）+ `importlib.reload`，库缺失静默 no-op。

---

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|:-------------|:-----------|:---------|
| `fts/core/atomic.py` | 原子写入使用临时文件+os.replace | 代码审查 |
| `fts/factor_engine/evolution_loop.py:_check_circuit_breaker` | 三熔断条件检查 | 单元测试 |
| `fts/factor_engine/factor_program.py:validate_factor_code` | AST 预验证拒绝黑名单模块 | `pytest tests/factor_engine/test_factor_program.py` |
| `fts/store/duckdb_lock.py` | 跨进程写 filelock 串行 | `pytest tests/store/` |
| `fts/scheduler/watchdog.py:ProcessWatchdog` | 3 次重启后熔断 5min | 单元测试 |
