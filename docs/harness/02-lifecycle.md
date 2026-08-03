# FTS 开发生命周期

> 版本: v1.8.1
> 最后更新: 2026-08-03

---

## 1. 阶段划分

FTS 从 FDT 剥离共经历 8 个 Phase，目前全部完成：

| 阶段 | 内容 | 状态 | 产出物 |
|:-----|:-----|:-----|:-------|
| **Phase 1** | FTS 核心契约 + 因子引擎骨架 + Data-Core 集成验证 | ✅ 完成 | 因子引擎框架 |
| **Phase 2** | 因子引擎完整实现（三层循环 + 种子池数据类型感知） | ✅ 完成 | 可用的因子进化引擎 |
| **Phase 3** | 数据处理管线（pipeline 抽象基类 + factor_combiner） | ✅ 完成 | 衍生数据管线骨架 |
| **Phase 4** | 多因子策略 + CLI + 调度 | ✅ 完成 | 完整可运行系统 |
| **Phase 5** | 测试覆盖 + pylint 清理 + FDT 侧清理 | ✅ 完成 | 交付就绪 |
| **Phase 6** | Memory 重定向：FTS 独立 memory 路径，FDT_PATH 环境变量解耦 | ✅ 完成 | 独立持久化 |
| **Phase 7** | FTS 层级提升为独立项目，claude.md / fts-coding.mdc 割离 | ✅ 完成 | 完整项目独立 |
| **Phase 8** | 种子因子集成：世坤 101 因子 + Qlib 158 因子加入种子池，seed_data 目录统一管理外部因子，支持 include_external 参数控制加载 | ✅ 完成 | 268 种子因子（9 内置 + 259 外部），1325 测试全绿 |
| **Phase 9** | 国泰君安 191 因子集成 + 全量工程测试：全部模块边缘路径覆盖，46/47 模块 100% 覆盖率 | ✅ 完成 | 459 种子因子（9 内置 + 450 外部），1431 测试全绿，仅余 1 空白行 |
| **Phase 10** | 基本面/另类/宏观因子集成：23 个基本面种子因子加入种子池，新增 FundamentalProvider 数据层 | ✅ 完成 | 482 种子因子（9 内置 + 473 外部），1502 测试全绿 |
| **Phase 11** | 期货自治循环：L1/L2/L3 全自动调度 + 期货基本面数据接入（库存/仓单/基差）+ 信号管道定时任务 + 期货全量种子因子库（12 大因子家族 50+ 子因子）+ 顶级因子过滤（IC>0.3）+ 信号管道输出到 reports/{date}/ | ✅ 完成 | 482 种子因子（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面），期货 12 家族 50+ 子因子，5 个定时任务，信号报告输出到 reports/ |
| **Phase 12** | 策略进化：动态因子权重（DynamicWeightStrategy）、市场制度自适应（RegimeAdaptiveStrategy）、多周期信号融合（MultiPeriodSignalFusion） | ✅ 完成 | 3 种策略进化能力，55 个测试用例全绿，strategy_evolution.py 95% 覆盖率 |
| **Phase 13** | 信号管道 v5 多空双向 + 信号增量：信号管道升级为多空双向排名（按绝对值排序），新增信号增量追踪（较昨日变化判断趋势加速/衰竭），信号快照 JSON 持久化 + JSONL 历史追加，L3 Portfolio Loop 自动触发信号管道（全量 82 品种），README 拆分股票/期货种子因子 | ✅ 完成 | 1601 测试全绿，12 大期货因子家族 50+ 子因子 |

---

## 2. 文件命名规范

- **Python 文件**: `snake_case.py`
- **测试文件**: `test_<module_name>.py`
- **配置文件**: `settings.yaml`
- **Markdown 文档**: `NN-topic.md`（NN 为两位数字序号）
- **程序配置文件**: `Program.md`（首字母大写，位于项目根目录）

示例：
```
fts/
├── factor_engine/
│   ├── evolution_loop.py
│   ├── meta_loop.py
│   ├── portfolio_loop.py
│   └── evaluation_chain.py
tests/
├── factor_engine/
│   ├── test_evolution_loop.py
│   ├── test_meta_loop.py
│   └── test_portfolio_loop.py
```

---

## 3. 版本号命名规则

遵循语义化版本号 `MAJOR.MINOR.PATCH`：

| 级别 | 变更类型 | 示例 |
|:-----|:---------|:-----|
| **MAJOR** | 重大架构变更（如 LangGraph 迁移） | v1.0.0 → v2.0.0 |
| **MINOR** | 功能新增或阶段完成 | v0.1.0 → v0.2.0 |
| **PATCH** | bug 修复或文档更新 | v0.1.0 → v0.1.1 |

当前版本：**v1.8.1**

### 版本号同步规则

FTS 包含两个版本号，修改时必须同步：

| 位置 | 用途 | 当前值 |
|:-----|:-----|:-------|
| `fts/__init__.py` | FTS 项目版本 | `"1.7.0"` |
| `pyproject.toml` | 包版本 | `"1.7.0"` |

`fts.factor_engine.__init__.py` 中的 `EVOLUTION_VERSION` 为因子引擎内部版本号（v1.1.0），与 FTS 项目版本同步。

---

## 4. session_id 与 trace_id 生成规则

### trace_id

```
trace_id = "{timestamp}-{uuid4_hex8}"
```

- `{timestamp}`: `int(time.time())`，Unix 时间戳
- `{uuid4_hex8}`: `uuid4().hex[:8]`，UUID4 的前 8 位十六进制字符

示例：`1658136000-a1b2c3d4`

### run_id

```
run_id = "run_{timestamp}_{uuid4_hex6}"
```

示例：`run_1658136000_a1b2c3`

### session_id

session_id 用于区分 CLI 每次执行：

- CLI 启动时自动生成
- 格式与 trace_id 相同，但作用域为整个 CLI 会话
- 传递到各子命令作为日志聚合标识

### 全链路传播

所有模块必须遵循以下规则：

1. CLI 入口生成 `trace_id` 和 `session_id`
2. 传递给 `factor_engine` 各模块（通过函数参数）
3. 管线各 stage 必须传播 `trace_id`（通过 `DataPayload.trace_id`）
4. 监控和日志记录必须包含 `trace_id`
5. scheduler 任务执行时生成独立的带前缀的 trace_id

---

## 5. 角色定义

### 当前角色

| 角色 | 职责 | 备注 |
|:-----|:-----|:-----|
| **AI Agent** | FTS 全链路开发：编码、测试、文档、部署 | 当前版本全部由 Agent 完成 |

### 未来角色（预留）

| 角色 | 职责 | 预计引入时间 |
|:-----|:-----|:-------------|
| **人类审核员** | Verifier 锁定审核、Program.md 批准、P0 差距审查 | v0.2.0+ |
| **运维工程师** | 生产环境部署、调度配置、故障恢复 | v0.3.0+ |

### 角色边界

- AI Agent 不得执行人类审核员的职责（如解锁 Verifier）
- AI Agent 不得修改已锁定的 Program.md
- AI Agent 不得将 trace_id 省略或绕过

---

## 6. 状态机

FTS 项目整体状态：

```
[初始] → Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7 → [v0.1.0]
                                                                                      │
                                                                                      ▼
                                                                               [v0.2.0 目标]
```

各循环的状态：

```
[stopped] → [running] → [paused/completed] → [circuit_broken] → [recovered/stopped]
```

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | Phase 11 → `data_futures.py` + `data_futures_fundamental.py` + `seed_data_futures_full.py` + `scheduler/jobs.py` + `scripts/` |
| 可验证断言 | Phase 11 产出物：482 种子因子（9+101+158+191+23），期货 12 家族 50+ 子因子，5 个定时任务 |
| 检验方式 | `python -m pytest tests/factor_engine/test_seed_pool.py --no-cov -q 2>&1 | findstr "passed"` |
