# FTS 韧性设计

> 版本: v1.1.0
> 最后更新: 2026-07-24

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
| 单日 token 超支 | >2x daily_token_limit (50K) |
| 失败率超限 | > circuit_breaker_failure_rate (95%) |
| 连续低质量候选 | > circuit_breaker_consecutive_low_quality (5 次) |

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
