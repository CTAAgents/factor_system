# FTS 开发生命周期

> 版本: v2.39.0
> 最后更新: 2026-08-08

---

## 1. 阶段划分

FTS 从 FDT 剥离共经历 16 个 Phase，目前全部完成：

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
| **Phase 11** | 期货自治循环：L1/L2/L3 全自动调度 + 期货基本面数据接入（库存/仓单/基差）+ 信号管道定时任务 + 期货全量种子因子库（12 大因子家族 50+ 子因子）+ 顶级因子过滤（IC>0.3）+ 信号管道输出到 reports/{date}/ | ✅ 完成 | 482 种子因子（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面），期货 12 家族 50+ 子因子，8 个定时任务，信号报告输出到 reports/ |
| **Phase 12** | 策略进化：动态因子权重（DynamicWeightStrategy）、市场制度自适应（RegimeAdaptiveStrategy）、多周期信号融合（MultiPeriodSignalFusion） | ✅ 完成 | 3 种策略进化能力，55 个测试用例全绿，strategy_evolution.py 95% 覆盖率 |
| **Phase 13** | 信号管道 v5 多空双向 + 信号增量：信号管道升级为多空双向排名（按绝对值排序），新增信号增量追踪（较昨日变化判断趋势加速/衰竭），信号快照 JSON 持久化 + JSONL 历史追加，L3 Portfolio Loop 自动触发信号管道（全量 82 品种），README 拆分股票/期货种子因子 | ✅ 完成 | 1601 测试全绿，12 大期货因子家族 50+ 子因子 |
| **Phase 14** | Design 全量落地（v2.9.0）：9 个设计文档（A.1-C.3）全部完成——S1 数据层（质量评分/状态历史/审计报告 3 表 + 3 仓储类）、S2 监控调度（Prometheus 指标注册表 + 自适应权重封装 + 数据质量三维指标 + 月度衰减/数据质量 2 任务）、S3 回测流水线（7 阶段类 + run_batch + Builder + CLI）、S4 C.1 CLI（feature list/analyze + gp evolve）、S5 C.2 实盘对接（信号契约 + fts/risk 风控包 + LiveFactorMonitor + HTTP 端点）、S6 C.3 反馈闭环（FeedbackLoop 家族 + 4 反馈表 + CLI）；新增 79 测试用例 | ✅ 完成 | 2066+ 测试，9 设计全部实现（详见 docs/harness/design/） |
| **Phase 15** | 算子演化引擎（v2.10.0，Phase 3+ / C.4）：`OperatorEvolutionEngine` 在 DSL 算子空间（58 算子 L0-L5）做适应度导向进化搜索（种群初始化 validator 校验 → IC+Sharpe 评估（DSL executor + 缓存）→ 锦标赛选择 → 子树交叉/变异（参数受 param_bounds 约束）→ 精英保留），取代 `_generate_operator_factor` 纯随机组合；evolution_loop operator/hybrid 模式接入（无评估数据回退随机生成）；产物为 `kind=OPERATOR` 因子；关闭 GAP-026 | ✅ 完成 | 算子演化引擎 + 13 测试用例（引擎 11 + 集成 2），C.4 设计落地（详见 docs/harness/design/C.4） |
| **Phase 16** | 组合漂移治理（v2.11.0）：L3 组合漂移监控（DriftMonitor 成员重合率 + 权重 L1 变化率 → drift_history/YYYY-MM-DD.json）+ PortfolioManager combo_history 归档 + build_combo 粘性约束（±30% 变动 / 新因子首日封顶）+ L2 影子池（新晋升因子观察 5 个交易日，种子因子直接进正式组合）；新增 20 测试用例 | ✅ 完成 | 82 个 portfolio_loop 测试全绿，漂移数据持久化 memory/portfolio/drift_history/ |
| **Phase 17** | 孤立模块集成 Phase 2（v2.16.0）：`LogicMonitor`/`FactorInspector` 注册为定时任务（每日 22:00/03:00）；`ProcessWatchdog` 集成到 `SchedulerEngine`（`start_watchdog()` 方法）；任务注册表增至 8 个任务 | ✅ 完成 | 2043 回归测试通过，8 个定时任务（L1/L2/L3 + 健康检查 + 月度衰减 + 数据质量 + 逻辑监控 + 因子巡检） |
| **Phase 18** | 因子淘汰主流程集成（v2.17.0）：`FactorRepository.retire_factor()` 实现 DuckDB 状态更新 + JSON 文件迁移至 `_retired/` + 状态变迁记录；`monthly_decay_eval_job` 调用 `retire_factor()` 同步淘汰到主存储；修复 `update_factor`/`update_factor_status` DuckDB ART 索引 bug（DROP → UPDATE → 重建索引） | ✅ 完成 | 因子淘汰正式成为主流程环节，退化因子自动从活跃池移除 |
| **Phase 22** | Elastic Net 信号合成 + ACTIVE_FACTOR_CAP（v2.35.0）：L3 组合构建默认信号合成模式从 equal_weight 切换为 elastic_net（Elastic Net 截面回归，L1+L2 自动变量选择，防止冗余因子稀释组合夏普）；新增 ACTIVE_FACTOR_CAP=20 活跃因子数量上限，因子数超过上限时按 Sharpe 排名保留 Top N，自动过滤低质量因子；期货 CLI 默认 synthesis_mode 同步切换为 elastic_net；109 相关测试全绿，无回归 | ✅ 完成 | 2102+ 回归测试通过，portfolio_loop 90 测试全绿，Elastic Net 自动变量选择 + ACTIVE_FACTOR_CAP=20 因子筛选 |
| **Phase 23** | P1 因子聚类 + P2 PCA 降维（v2.36.0）：新增 `fts/factor_engine/factor_clustering.py` 模块，`FactorClusteringEngine` 实现信号相关性层次聚类 + 代表因子选择（Pearson 相关系数 → 层次聚类 → Sharpe 最高代表），`PCASignalCompressor` 实现 PCA 信号降维压缩（z-score 标准化 → PCA 保留 95% 方差 → 载荷矩阵映射因子权重）；集成到 L3 PortfolioLoop 的 Step 1.8（P1 聚类）和 Step 1.9（P2 PCA，可选）；关闭 GAP-034 和 GAP-035 | ✅ 完成 | 因子聚类模块全量测试通过，portfolio_loop 集成测试通过，P1/P2 可独立控制 |
| **Phase 24** | ML 模型集成层（v2.38.0）：新增 `fts/ml/` 包，封装 LightGBM/XGBoost/Ensemble 三种模型，支持横截面回归/时序预测/集成融合三种训练模式；L3 信号合成新增 `ml_ensemble` 模式，通过可选依赖 [`ml`] extra 控制；新增 [ml] 可选依赖声明 | ✅ 完成 | fts/ml/ 包全量测试通过，L3 ml_ensemble 模式集成测试通过 |
| **Phase 25** | VNPY 信号桥接层（v2.38.0）：新增 `fts/bridge/` 包，SignalBridge 实现 JSON/Redis/REST 三种协议的交易信号格式转换；`fts bridge` CLI 子命令支持 serve/status 操作；新增 [bridge] 可选依赖声明 | ✅ 完成 | fts/bridge/ 包全量测试通过，CLI bridge 子命令集成测试通过 |
| **Phase 19** | 因子家族多样性约束（v2.18.0）：`_promote_to_elite` 新增家族数量检查（`max_per_family=3`），限制单一家族因子过度繁殖；`BudgetConfig` 新增 `max_per_family` 字段；配置文档同步更新 | ✅ 完成 | L2 演化晋升受家族多样性约束，fut_bias 等家族从 8+ 个降至 ≤3 个 |
| **Phase 20** | 分钟级回测 Phase 1（v2.30.0）：三源分钟数据源适配（通达信 TDX HTTP + TQ-Local + 天勤 TQSDK），DuckDB minute_cache 缓存，聚合器扩展支持分钟级数据路径，回测引擎增加 frequency 参数（年化因子/窗口/成本自适应），CLI 增加 --frequency 参数 | ✅ 完成 | 分钟级回测可运行，支持 1m/5m/15m/30m/60m/daily 频率切换 |
| **Phase 21** | 宏观字段增强层（v2.32.0）：`IFindSource.get_macro_series()` 实现 edb_cache 缓存读写（查 → miss 拉取 → 幂等写回）；新增 `fts/data_sources/macro_aligner.py`（`MacroFieldAligner.align()` 月度→交易日 ffill + 发布滞后防未来函数 + `inject_macro_fields()` 批量注入）；`BacktestPipeline._compute_factor()` 因子执行前注入宏观列（export/import_data/cpi/rate/us_bond），宏观因子不再走 close 代理降级 | ⏳ 进行中 | 宏观因子可读取真实 EDB 数据，缓存 + 对齐 + 注入全链路可用 |

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

当前版本：**v2.30.0**

### 版本号同步规则

FTS 包含两个版本号，修改时必须同步：

| 位置 | 用途 | 当前值 |
|:-----|:-----|:-------|
| `fts/__init__.py` | FTS 项目版本 | `"2.30.0"` |
| `pyproject.toml` | 包版本 | `"2.30.0"` |

`fts/factor_engine/contracts.py` 中的 `EVOLUTION_VERSION` 动态同步 `fts.__init__.__version__`（当前 v2.30.0），随 FTS 项目版本自动更新。

### 状态 schema 版本与冷启动规则

`fts/factor_engine/contracts.py` 中的 `STATE_SCHEMA_VERSION` 控制 L1/L2/L3 状态文件（`state.json`）的冷启动判定：

| 机制 | 说明 |
|:-----|:-----|
| `STATE_SCHEMA_VERSION` | 状态结构版本（`"1"`），仅在 `L1MetaLoopState` / `EvolutionState` / `L3MetaLoopState` TypedDict 字段变更时手动递增 |
| 冷启动触发条件 | `state.json` 中 `schema_version` 与 `STATE_SCHEMA_VERSION` 不一致（含缺失）→ 重新初始化状态 |
| 不触发冷启动 | FTS 功能版本号变更（`EVOLUTION_VERSION` / `__version__` 递增）不影响状态文件，避免小版本升级清空演化进度 |

状态文件字段：`schema_version`（替代旧 `version` 字段），涉及 `MetaStateManager`（L1）、`EvolutionStateManager`（L2）、`PortfolioStateManager`（L3）。

---

## 4. session_id 与 trace_id 生成规则

### trace_id

```
trace_id = "{prefix}_{8hex}_{timestamp}"
```

- `{prefix}`: 模块/任务前缀（如 `l2`、`session`、`fts.l1.sched`）
- `{8hex}`: `secrets.token_hex(4)`，8 位十六进制随机串
- `{timestamp}`: `YYYYMMDDTHHMMSS`，本地时间戳

示例：`l2_3f9a2b1c_20260718T001230`

### run_id

```
run_id = "run_{8hex}_{timestamp}"
```

示例：`run_a1b2c3d4_20260718T001230`

### session_id

session_id 用于区分 CLI 每次执行：

- CLI 启动时由 `main()` 调用 `generate_session_id()` 自动生成（格式 `session_<8hex>_<timestamp>`）
- 挂载到 `args.session_id`，作用域为整个 CLI 会话
- 传递到各子命令，作为日志聚合标识（evolution / meta-loop / portfolio 启动日志均输出）

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

| 角色 | 职责 | 定义文件 |
|:-----|:-----|:-----|
| **AI Agent** | FTS 全链路开发：编码、测试、文档、部署（详见能力边界） | `agents/fts-agent.md`（v1.0.0） |

### 未来角色（预留）

| 角色 | 职责 | 引入状态 |
|:-----|:-----|:-------------|
| **人类审核员** | Verifier 锁定审核、Program.md 批准、P0 差距审查 | 未引入（截至 v2.12.0） |
| **运维工程师** | 生产环境部署、调度配置、故障恢复 | 未引入（截至 v2.12.0，部署由 start_fts.ps1 脚本承担） |

### 角色边界

- AI Agent 不得执行人类审核员的职责（如解锁 Verifier）
- AI Agent 不得修改已锁定的 Program.md
- AI Agent 不得越级执行交易决策（由下游系统 FDT 负责）
- AI Agent 不得删除未过期的 elite 因子
- AI Agent 不得修改生产环境配置（需通过文档审查）
- AI Agent 不得将 trace_id 省略或绕过

---

## 6. 状态机

FTS 项目整体状态：

```
[初始] → Phase 1 → Phase 2 → ... → Phase 16 → ... → Phase 19 → Phase 20 → [v2.30.0（当前版本）]
```

各循环的状态：

```
[stopped] → [running] → [paused/completed] → [circuit_broken] → [recovered/stopped]
```

### 未实现功能记录（v2.38.0）

本次升级（Phase 24 + Phase 25）未实现的深度学习模型与强化学习模块：

| 模型 | 说明 | 计划 |
|:-----|:------|:-----|
| **深度学习时序模型** | LSTM/GRU/Transformer 等端到端深度学习时序预测模型 | 未纳入本次计划，需引入 PyTorch/TensorFlow 依赖，存在训练成本高、可解释性低的权衡 |
| **强化学习（RL）** | 基于 RL 的交易策略优化（如 DQN/PPO/SAC），通过环境交互学习最优持仓决策 | 未纳入本次计划，需引入 gym 式环境 + RL 算法库，与 FTS 因子驱动的信号产出范式差异较大 |

以上功能登记为 **GAP-037**，优先级 P2，在 `docs/harness/08-gap-analysis.md` 中跟踪。

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | Phase 11 → `data_futures.py` + `data_futures_fundamental.py` + `seed_data_futures_full.py` + `scheduler/jobs.py` + `scripts/` |
| 可验证断言 | Phase 11 产出物：482 种子因子（9+101+158+191+23），期货 12 家族 50+ 子因子，8 个定时任务 |
| 检验方式 | `python -m pytest tests/factor_engine/test_seed_pool.py --no-cov -q 2>&1 | findstr "passed"` |
