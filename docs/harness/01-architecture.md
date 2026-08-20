# FTS 系统架构文档

> 版本: v3.1.0+4
> 最后更新: 2026-08-20

---

## 1. 项目概述

FTS（Factor Intelligence System，因子智能系统）是**因子生产系统**，专注期货因子推演、评估、生命周期管理与因子信号矩阵输出。数据层基于 QuantData 权威主链路（DuckDB 只读短连接直读），无外部数据项目依赖。股票管线已剥离至独立项目 fts-stock（2026-08）。

### 1.0 双系统切分（v3.0.0 架构级调整，plans/57）

> **角色重定位**：FTS 由"因子生产 + 策略合成"混体收敛为**因子生产系统**（专注因子管理与信号矩阵输出）；策略合成职责整体迁移至外部 **Regime-Driven**（三层 Regime + 信号合成 + 五要素路由 + 组合风控）。

```
数据层        D:\QuantData（已有）—— 行情/存储，RD 与 FTS 均只读
                ↑ 只读
因子生产层    FTS（保留：注入/演化/质检/生命周期/因子资产库/信号矩阵输出）
                → 输出：因子信号矩阵 + 因子画像（子链/regime/质量）+ 因子状态
                ↓ 接口（因子信号契约 v1，design/F.3）
策略合成层    Regime-Driven（扩展：三层 Regime + 信号合成 + 五要素路由 + 组合风控）
                → 输出：每日交易计划 / 交易管道
```

**切分落地状态（v3.0.0）**：
- **FTS 保留**：信号矩阵构建/增量（`l3_signal_service`）、信号序列计算、正交化/中性化、DSL 算子库、数据链路（`FTSDataProvider` → QuantData）；存量因子资产作为候选池保留，重审后决定去留。
- **FTS 退役登记**（`fts/factor_engine/retired_l3.py`，35 项）：L3 组合侧——`futures_signal_pipeline` 组合侧 + `portfolio_loop` 策略侧 + `weight_learning`/`capital_allocator`/`regime_crowding` 标记弃用；import 期 DeprecationWarning + `warn_if_retired` 告警；存量调用点兼容不删码（物理删除为后续独立里程碑）。
- **RD 承接**：`strategy_synthesis`（信号合成）、`combo_verifier`（组合校验）、`money_management`（权重学习/资金分配）、`crowding_gate`（拥挤度权威替换）、`signal_client`（契约拉取）、`backtest_engine` 消费信号矩阵、L2 子链化（5 子链）。
- **接口层**：因子信号契约 v1（design/F.3）——l3_signal_meta 追加 `schema_version`/`factor_status`/`factor_scope` 三列 + 历史回填；双模式读取（决策/训练）隔离防未来函数；增量幂等 + 新鲜度校验 + 降级熔断。
- **验收证据（2026-08-20）**：阶段 0 A/B（状态一致率 92.04% / 方向一致率 97.55%）+ 阶段 1 双轨对账（信号余弦 1.0000 / 组合方向 100% / 绩效差 0.000000）+ 因子映射 10/12 Spearman=1.0000 + 全量回归 8129 passed。

### 1.1 QuantData 权威数据集成（v2.105.0+32 主链路切换，v3.0.0+1 起 K 线唯一数据源）

> **原则**：FTS 可消费的**权威数据仅限 QuantData**（本机统一金融数据仓库）。其他来源（AKShare/通达信/天勤/WebSearch）权威性受质疑，一律降级为标注来源的兜底层。一切因子挖掘必须基于实际可消费数据。

**QuantData 实测数据现实（2026-08-19）**：

| 表 | 规模 | 关键列 | 权威字段 |
|:----|:-----|:-------|:---------|
| `kline_daily` | 127.6万行 / 5339 合约 | symbol, trade_date, OHLCV, open_interest | **OHLCV + open_interest**（无 amount/settle） |
| `continuous_daily` | 23.9万行 / 88 品种 | symbol, series_type(main/sub), OHLCV, open_interest, **adj_factor**, main_contract | **后复权连续序列**（重叠窗口平滑换月） |
| `continuous_map` | 12.1万行 / 88 品种 | symbol, trade_date, main_contract, sub_contract | **主力/次主力逐日映射**（期限结构构建源） |
| `kline_minute` / `kline_tick` | 3552万行 / 19.5万行 | 多周期 / 5 档盘口 | OHLCV + 持仓 / 5 档盘口 |

**QuantData 不含**：`amount`/`settle`/`pre_settle`/`vwap`/`oi_change` 与**库存/仓单/现货基差/宏观**（fundamental 类）——登记 GAP-157/158。

**字段权威矩阵（SSOT）**：

| 层级 | 字段 | 来源 | 状态 |
|:-----|:-----|:-----|:-----|
| **L0 权威** | open/high/low/close/volume/hold(=open_interest) | QuantData continuous_daily | 生效 |
| **L0 权威（接线后）** | term_spread/roll_yield | QuantData continuous_map + kline_daily | 生效（D15 算子转可用） |
| **L1 降级·非权威** | vwap/amount/settle/pre_settle | FTS 缓存 + 增强源 + 代理兜底 | 标注来源，不硬拒 |
| **L2 缺失·禁依赖** | fundamental 9 字段（inventory/warehouse_receipt/spot/basis） | 无权威源 | GAP-157 禁依赖 |

**主链路设计**：
- **降级链**：`DUCKDB_CACHE`（QuantData 读取缓存）→ `QUANTDATA`（唯一权威源）→ `SYNTHETIC`（测试/离线兜底）；天勤/通达信实时/AKShare 已从默认聚合器移除（显式扩展场景 opt-in）。
- **Provider**：`fts/data_sources/quantdata_provider.py`——DuckDB **只读短连接**直读 kline_history.duckdb，不依赖 `client_v2`；路径经 `FTS_QUANTDATA_HOME` 配置解析。
- **品种映射**：FTS `RB0` ↔ QuantData `RB`（88 vs 82 品种，六交易所）。
- **复权策略**：主链路直接消费 continuous_daily 后复权序列；`RollCalendar.apply_adjustment` 仅作 QuantData 缺失时降级，**避免双重复权**。
- **缓存一致性（v3.1.0+3 根治落地）**：日线 `cache_max_age_days` 30→**1**（QuantData 每日刷新真实消费）；缓存命中保留真实 source（QUANTDATA 跳过二次复权，旧 TDX 缓存仍走 RollCalendar）；`_write_cache` 幂等覆盖（先删后插，一数一源）。
- **settle 处理**：QuantData 无 settle → `(H+L+C)/3` 典型价代理，标注非权威（GAP-158 L1 降级）。
- **历史深度边界**：QuantData 主力连续历史起点 ~2019（评估/演化窗口 days=500~700 完全覆盖）；长窗口历史由降级链 kline_cache 承接（非权威但深度兜底，不做数据口径混用回填）。

### 项目边界

| 职责 | 归属 |
|:-----|:-----|
| 行情数据获取（期货 OHLCV） | **FTS**（QuantData 权威主链路 + 降级链） |
| 因子推演（挖掘/演化/评估） | **FTS 核心能力** |
| 因子信号矩阵输出 | **FTS 核心能力**（因子信号契约 v1，design/F.3） |
| 多因子策略组建（信号→组合权重） | **Regime-Driven**（v3.0.0 起；FTS L3 组合侧已登记退役） |
| 交易信号产出 / 每日交易计划 | **Regime-Driven**（v3.0.0 起） |
| 循环调度与状态管理 / 健康监控 | **FTS 核心能力** |

---

## 2. 分层架构

FTS 采用 5 层分层架构：

| 层 | 模块 | 职责 |
|:---|:-----|:-----|
| 入口层 | `cli.py` / `scheduler/` / `monitor/` | 统一命令行入口、定时任务调度、系统健康监控 + HTTP 端点 |
| L0 人类设定层 | `program.py`（Program.md） | 人类设定演化目标、约束、市场偏好、风险偏好，L1/L2/L3 均受其约束 |
| L1 Meta-Loop | `meta_loop.py` + `experience_chain.py` + `extractors/` + `bulk_*` | 每日知识补给 → 种子因子注入 → 市场语境感知 → 演化方向指引（批量三层管线「采集→粗筛→LLM 深读」、知识源自动发现、按子链分批 bootstrap、L1→L2 闭环漏斗） |
| L2 Evolution Loop | `evolution_loop.py` + 9 协作类（UctSelector/CandidatePrefilter/EliteStore/AuditPipeline/TraceRecorder/FactorReviewer/EvolutionChannels/SeedManager/CandidateProcessor）+ `batch_mining.py` + `verifier.py` | 因子核心演化层：种子池 → 演化（code/hybrid/operator/deep/transformer/batch）→ 横截面评估 → elite 晋升（QA 门禁） |
| L3 信号矩阵 | `l3_signal_service.py`（build_signal_matrix/增量窗口追加）+ `futures_signal_pipeline._compute_signal_matrix` | 因子信号矩阵构建与输出（决策/训练双模式，因子信号契约 v1）；**组合构建职责已迁移 Regime-Driven** |

**L2 关键机制**：种子池 185 因子（含 fut_macro_import，17 类 style_tags）；数据 82 品种 OHLCV 面板（common_dates 多数对齐）；评估 `cross_section_evaluate_backtest`；生成端去重前置（Step 1.35）+ 晋升端去重兜底；批量挖掘漏斗（BatchMiner + ExecutorBackend 可插拔）。

---

## 3. 模块结构

```text
src/  (实现为 fts/)
├── factor_engine/     # 因子挖掘/演化/评估/生命周期（核心）
│   ├── evolution_*.py # 演化主循环（Mixin 已拆 9 协作类）
│   ├── l3_signal_service.py  # L3 信号矩阵构建/增量
│   ├── qa/            # 评审质检（Q1-Q10 门禁/月度/季度复检）
│   ├── scope_domain/  # 品种域/特异因子通道
│   ├── expr_dsl/      # FTS-Expr DSL 算子库（132 算子）
│   ├── retired_l3.py  # L3 组合侧退役登记（35 项）
│   └── factor_db/     # 因子资产库仓储层
├── data_sources/      # 数据源适配（quantdata_provider/aggregator/roll_calendar）
├── scheduler/         # 定时任务（jobs.py：l1_meta_loop_job/l2_*_job 等）
├── monitor/           # 监控（logic/data_level/factor_level/http_server）
├── store/             # 存储域注册表（StorageRegistry + storage_landscape.yaml）
├── config/            # 配置（settings.py + config/*.yaml）
├── ml/                # 深度因子模型（MLP/GRU/Transformer）
├── llm.py             # LLM 客户端
└── cli.py             # 统一命令行入口
```

---

## 4. 数据流

### 全局数据流

```
QuantData（权威 K 线）→ FTSDataProvider/QuantDataProvider → 因子面板（82 品种）
   ↓                                                          ↓
因子计算（FTS-Expr DSL / 沙箱 / numba 快速路径）         L1 知识补给 → L2 演化 → 种子/精英因子
   ↓                                                          ↓
L3 信号矩阵（l3_signal_service，DuckDB l3_signal_store） ← 因子资产库（factor_catalog_futures.duckdb）
   ↓
因子信号契约 v1 → Regime-Driven（策略合成，外部）
```

### 期货数据流

- 日线：QuantData `continuous_daily`（后复权，adj_factor 自带）→ 缓存 `data/fts_history.duckdb`（kline_cache，cache_max_age_days=1）→ 因子面板。
- 期限结构：`continuous_map`（main/sub 逐日映射）+ `kline_daily` 近远月 close → `term_spread`/`roll_yield` → D15 算子。
- 换月日历：QuantData 复权序列主链路消费；`RollCalendar`（换月事件序列 + adj_ratio）仅 QuantData 缺失时降级。
- 分钟级：显式扩展场景（`minute_cache → TDX_LOCAL`，默认聚合器仅注册 TDX_LOCAL 分钟源）；tick 默认不注册（需显式 TQSDKTickSource）。

---

## 5. 关键契约

### TraceID 全链路

`trace_id` 必须贯穿所有模块、文档和日志。生成规则：`trace_id = f"{prefix}_{8hex}_{timestamp}"`（`fts.factor_engine.state.generate_trace_id()`）。所有 CLI 子命令启动时生成，经参数传递到各层循环。

### Verifier 锁定协议

Verifier 是核心安全机制，锁定后不可逆：L1 控制种子注入/知识补给；L2 控制因子演化流程；L3 控制组合构建与信号产出。锁定后只能读取，无法修改配置。

**L2 Verifier 换手校验（方案 A，v2.104.0+13 / GAP-114）**：`max_turnover_monthly` 为「成本敏感净收益校验」——换手超 5.0（次/月）时按 `净夏普 = 毛夏普 − 月换手×12×2×单边成本率 / 年化波动`（`one_side_cost_rate=0.0005`、`assumed_annual_vol=0.15`）判定，净夏普 ≥ min_sharpe 即准入并输出 `cost_adjusted` 明细。

**EvolutionLoop 熔断预算传播契约（v2.104.0+14 / GAP-115）**：`EvolutionLoop.budget` 为 property，重绑时同步传播 `_uct_selector.budget`，任何入口重绑（含 `FTS_EVOLUTION_CB_FAILURE_RATE` env 覆盖）自动生效到熔断判定。

### EvolutionLoop Mixin 拆分契约（34 计划，2026-08-13 起）

`class EvolutionLoop:` 零 Mixin 继承，组合持有 9 个协作类实例（`_uct_selector`/`_candidate_prefilter`/`_audit_pipeline`/`_trace_recorder`/`_factor_reviewer`/`_evolution_channels`/`_seed_manager`/`_elite_store`/`_candidate_processor`）+ 全部 `_*` 方法转发桩 + property 转发。契约约束：公开导入路径不变、公开方法签名/返回值不变、私有符号兼容、Mixin 方法名全局唯一、跨领域共享状态留主类由构造注入、领域独享状态随协作类搬迁。

### FactorCorrelation 契约

L2 种子因子相关性预检产物（仅标记不删除，供组合阶段参考）：`factor_id_a`/`factor_id_b`/`pearson`/`spearman`，阈值默认 0.95，仅标记 `max(|pearson|, |spearman|) >= 0.95` 的因子对。

### Program.md 约定

人类通过 `Program.md` 设定最高层级指令：`ProgramConfig`（目标/约束/市场偏好/风险偏好）、`parse_program_md()`（解析）、`load_program()`（加载验证）。

### FactorKind 枚举与 FactorProgram 可选字段（Phase C.2）

`FactorKind`（OPERATOR/CODE/HYBRID）+ `FactorProgram` 可选字段（`kind`/`expression`/`operator_depth`/`operator_count`/`max_lookback`），向后兼容扩展，存量因子经 `normalize_factor_program` 默认 `kind=CODE`。

### 存储域契约（plans/29，GAP-150）

`fts/store/registry.py` `StorageRegistry` 从 `docs/harness/_data/storage_landscape.yaml` 加载存储域注册表（SSOT 一数一源）；写路径严格模式默认开启（未登记路径抛 ValueError，`FTS_STORAGE_WRITE_STRICT=0` 回退告警）；L2/L3 DuckDB 写连接一律短生命周期（filelock 跨进程串行），读连接 `read_only=True`。

---

## 6. 各层循环运行时间

> 定时任务由 **TRAE Schedule 自动化**承载（v3.0.0 起），全部面向**全期货**（默认市场 futures，84 品种/17 产业链），统一走 `fts.scheduler.jobs` 通用 job。执行纪律：无论当天是否已执行过，必须完整重新执行，命令用 `python -u` 无缓冲模式。

| 任务 | 执行 | 职责 |
|:-----|:-----|:-----|
| L1 知识补给（`l1_meta_loop_job`） | 每日 04:00 | 知识补给 + Bootstrapping + 种子注入（全期货 17 链分批） |
| L2 种子评估+演化（`l2_seed_promotion_job`/`l2_evolution_weekday\|weekend_job`） | 每日 | 种子评估晋升 + 演化主循环（工作日 ≈10 代 / 周末 ≈50 代） |
| L2 监控+评审质检阀门 | 每日 04:00 | pending 机审 + approved 复核 + factor/logic/data/factor-level 四项监控；**周日重量级** `l2_review_job`（reaudit 全量重审 + 衰减评估 + 阀门收口） |
| 外部因子导入（`import_external_factors_job`） | 月度 | extract_* YAML 常态化导入 |
| Health Check | 每 10 分钟 | 状态监控 |

> 市场路由：全局市场开关 `FTS_DEFAULT_MARKET`（settings.default_market，默认 "futures" 全部期货）。共享数据/监控任务不门控。

---

## 7. 技术栈

- **语言**: Python 3.11+
- **核心依赖**: numpy, pandas, pyyaml, duckdb
- **演化依赖（可选）**: optuna（evolution extra）
- **LLM 依赖（可选）**: openai, anthropic（llm extra）
- **数据依赖（可选）**: akshare（mcp extra）
- **ML 依赖（可选）**: lightgbm, xgboost（ml extra）
- **测试**: pytest 7.4+, pytest-cov 4.1+
- **打包**: setuptools, pyproject.toml

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/data_sources/quantdata_provider.py` → §1.1 QuantData 主链路；`fts/factor_engine/retired_l3.py` → §1.0 双系统切分；`fts/store/registry.py` → §5 存储域契约（storage_landscape.yaml）；`fts/scheduler/jobs.py` → §6 调度任务；`fts/factor_engine/seed_pool.py` → §2 L2 种子池（185 因子） |
| 可验证断言 | 期货种子池总数 = **185**（含 fut_macro_import）；数据源优先级 `QUANTDATA → DUCKDB_CACHE → SYNTHETIC`；`fts/__init__.py` 版本与 pyproject.toml 一致 |
| 检验方式 | `python scripts/verify_doc_consistency.py`（版本号一致性 PASS + 种子池 185 断言）；`python -c "from fts.data_sources.quantdata_provider import QuantDataProvider; print('ok')"`（Provider 可导入） |
