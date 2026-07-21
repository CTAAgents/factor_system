# Factor Intelligence System (FTS) — Code Wiki

> **项目路径**: `d:\Programs\factor_system`
> **版本**: 1.0.0 (factor_engine EVOLUTION_VERSION 8.10.0)
> **Python**: 3.10+
> **代码规模**: ~3,400 LOC, 1,231 测试通过, 96% 覆盖率
> **入口点**: `fts = "fts.cli:main"`

---

## 目录

1. [项目整体架构](#1-项目整体架构)
2. [模块/包结构](#2-模块包结构)
3. [关键类与函数说明](#3-关键类与函数说明)
4. [模块间依赖关系](#4-模块间依赖关系)
5. [外部依赖](#5-外部依赖)
6. [运行/构建/测试方式](#6-运行构建测试方式)
7. [核心设计模式](#7-核心设计模式)
8. [配置文件说明](#8-配置文件说明)

---

## 1. 项目整体架构

FTS(Factor Intelligence System)是一个 **AI 原生量化因子发现、演化与组合构建系统**,采用 **Loop Engineering 范式**,由人在回路(L0)的顶层监督三个自治层(L1/L2/L3):

```
+--------------------------------------------------------------+
|  L0 HUMAN LAYER  (Program.md — 周度人工设置)                  |
+--------------------------------------------------------------+
              |                                  |
              v                                  v
+--------------------------+    +--------------------------------+
| L1 META-LOOP (每日 09:00) |    | L3 PORTFOLIO LOOP (周一 06:00)  |
|  - 知识注入               |    |  - 正交化                       |
|  - 辩论分析               |    |  - 衰减测试(6个月)              |
|  - 因子池更新             |    |  - 信号合成                     |
|  - Verifier-locked        |    |  - L3 Verifier-locked           |
+--------------------------+    +--------------------------------+
              |
              v
+--------------------------------------------------------------+
|  L2 EVOLUTION LOOP (每夜 23:00)                              |
|  - Macro 演化(LLM 驱动的代码编辑)                            |
|  - Micro 演化(optuna TPE 参数调优)                          |
|  - 3 级评估链(回测/经济学/多重检验)                          |
|  - Verifier-locked 评估                                      |
|  - 经验链(避免 LLM 重复犯错)                                 |
|  - 熔断器(token/低IC/失败率)                                 |
+--------------------------------------------------------------+
              |                                  |
              v                                  v
+--------------------------+    +--------------------------------+
| Data-Core                |    | FDT (下游)                     |
| UnifiedDataProvider      |    | inject_to_fdt()                |
| PRIMARY→DAILY→CACHED→    |    | elite/*.json                   |
| STALE→UNAVAILABLE        |    |                                |
+--------------------------+    +--------------------------------+
```

### 关键架构属性

- **trace_id 全链路追踪** — 跨所有模块的端到端追踪(`{prefix}_{8hex}_{timestamp}` 格式,见 `fts/factor_engine/state.py`)
- **Verifier 锁定协议** — 评估机制锁定;初始化后 Verifier 配置不可修改(抛出 `VerifierAlreadyLockedError`)
- **TypedDict 契约** — HARNESS §契约优先 原则;所有数据形状在 `fts/factor_engine/contracts.py` 中声明
- **原子文件操作** — 临时文件 + `os.replace` 实现崩溃安全的状态持久化(`fts/core/atomic.py`)
- **安全沙箱** — 因子代码执行使用白名单导入,阻止 `os`/`sys`/`subprocess`/`open`/`exec`/`eval`(`fts/factor_engine/factor_program.py`)
- **静默降级** — 可选依赖(optuna, openai, anthropic, datacore, apscheduler, watchdog)自动回退到 Mock 实现

参考文档:
- `docs/harness/01-architecture.md` — 架构图 + Verifier 锁定协议
- `docs/harness/02-lifecycle.md` — 7 个 FDT 分离阶段、文件命名、版本控制
- `README.md` — 项目概览和 CLI 命令摘要

---

## 2. 模块/包结构

### 顶层目录布局

```
d:\Programs\factor_system\
├── fts/                          # 主包(入口: fts.cli:main)
│   ├── __init__.py               # __version__="1.0.0", 模块列表
│   ├── cli.py                    # 404 LOC, 统一 CLI
│   ├── config/
│   │   └── settings.py           # 156 LOC, FTSConfig + load_config()
│   ├── core/                     # 原子 IO + 枚举 + 契约重导出
│   │   ├── atomic.py
│   │   ├── contracts.py          # 从 factor_engine.contracts 重导出
│   │   └── enums.py              # EvolutionStage, FactorPriority, FactorStatus
│   ├── data.py                   # 300+ LOC, FTSDataProvider(Data-Core 适配器)
│   ├── llm.py                    # 235 LOC, LLMClient 层次结构
│   ├── factor_engine/            # 最大模块 — L1/L2/L3 循环
│   │   ├── contracts.py          # 500+ LOC, 所有 TypedDict 契约
│   │   ├── evolution_loop.py     # L2 主循环
│   │   ├── meta_loop.py          # L1 Meta-Loop
│   │   ├── portfolio_loop.py     # L3 Portfolio Loop
│   │   ├── seed_pool.py          # 15 个内置种子因子
│   │   ├── factor_program.py     # 安全沙箱执行器
│   │   ├── macro_evolution.py    # LLM 驱动的代码编辑
│   │   ├── micro_evolution.py    # optuna TPE 调优
│   │   ├── evaluation_chain.py   # 3 级评估
│   │   ├── verifier.py           # 锁定的 FactorVerifier
│   │   ├── state.py              # 状态管理器 + trace_id/run_id
│   │   ├── experience_chain.py   # 成功/失败追踪
│   │   ├── program.py            # L0 Program.md 解析器
│   │   ├── walk_forward.py       # Walk-forward OOS 验证
│   │   ├── cost_model.py         # 交易成本模型
│   │   ├── regime.py             # 市场状态检测
│   │   ├── stress_test.py        # 5 个历史压力场景
│   │   └── monitor.py            # LoopStatus 检查
│   ├── pipeline/                 # Pipeline + Stage Protocol
│   │   ├── base.py               # DataPayload, ProcessingStage, FactorPipeline
│   │   └── factor_combiner.py    # FactorCombiner(z-score + QR 正交)
│   ├── strategies/               # v2 可插拔策略框架
│   │   ├── base_v2.py            # BaseStrategyV2 ABC + StrategyV1Adapter
│   │   └── multi_factor_strategy.py  # 12因子多策略
│   ├── scheduler/                # APScheduler + watchdog + hotswap
│   │   ├── engine.py
│   │   ├── tasks.py
│   │   ├── watchdog.py
│   │   └── hotswap.py
│   └── monitor/                  # 状态报告 + HTTP metrics 服务器
│       ├── __init__.py
│       ├── http_server.py        # 纯 stdlib /health, /metrics, /
│       └── elite_tracker.py      # EliteFactorTracker + AutoRetireManager
├── config/
│   └── settings.yaml             # 默认 YAML 配置
├── docs/
│   ├── harness/                  # 活工程规范(NN-*.md)
│   ├── deploy/                   # INSTALL.md, WINDOWS.md
│   ├── production_plan.md
│   └── ...
├── tests/
│   ├── factor_engine/conftest.py # fixtures: sample_ohlcv, tmp_memory_dir 等
│   └── test_e2e.py               # 10 个 E2E 场景
├── .github/workflows/ci.yml      # Python 3.10/3.11/3.12 矩阵
├── pyproject.toml                # 构建配置 + 依赖 + 入口点
├── README.md
├── CLAUDE.md                     # AI 编码标准
├── start_fts.ps1                 # PowerShell 启动脚本
└── .gitignore
```

### 模块职责一览

| 包 | 职责 |
|---|---|
| `fts.cli` | 统一 CLI 入口 — `fts version/monitor/evolution/meta-loop/portfolio/factor/scheduler` |
| `fts.config` | 配置加载(YAML → 环境变量 → 默认值 优先级) |
| `fts.core` | 原子文件 IO + 枚举 + 契约重导出(基础工具) |
| `fts.data` | `FTSDataProvider` 适配 Data-Core 的 `UnifiedDataProvider`,5 级降级 |
| `fts.llm` | LLM 客户端抽象(`OpenAIClient` / `AnthropicClient` / `MockLLMClient`),自动检测 |
| `fts.factor_engine` | 核心引擎 — L1/L2/L3 循环、评估、verifier、沙箱、种子、经验链 |
| `fts.pipeline` | Pipeline 框架,包含 `ProcessingStage` Protocol + `FactorPipeline` ABC + `FactorCombiner` |
| `fts.strategies` | v2 可插拔策略框架(BaseStrategyV2 ABC) + `StrategyV1Adapter` 向后兼容 |
| `fts.scheduler` | 基于 APScheduler 的 cron 引擎 + `ProcessWatchdog` + `HotSwapWatcher` |
| `fts.monitor` | 循环状态报告 + HTTP metrics 服务器 + `EliteFactorTracker` 自动退役 |

---

## 3. 关键类与函数说明

### 3.1 `fts.cli` — `fts/cli.py`

- **`main()`** — 统一 CLI 分发器,带子命令
- **`version`** — 打印 `__version__`
- **`monitor`** — 调用 `fts.monitor` 的 `check_all_status()`
- **`evolution run`** — 运行单因子或 csi300 模式演化循环;生成 `trace_id`+`run_id`
- **`meta-loop run`** — 运行 L1 Meta-Loop
- **`portfolio run`** — 运行 L3 Portfolio Loop
- **`scheduler run/list`** — 管理 APScheduler 任务
- **`factor list/show`** — 列出/显示 elite 目录中的因子
- **`_prepare_data()`** — 通过 `FTSDataProvider` 加载 OHLCV 的辅助函数
- **`_prepare_cross_section_data()`** — 横截面数据准备辅助函数

### 3.2 `fts.config.settings` — `fts/config/settings.py`

- **`FTSConfig`** (dataclass, ~L20) — 字段: `memory_dir`, `elite_dir`, `max_generations`, `micro_trials_per_generation`, `portfolio_max_factors` 等
- **`load_config()`** (~L70) — YAML → 环境变量 → 默认值 合并
- **`get_config()`** (~L140) — 惰性单例访问器

### 3.3 `fts.core` — `fts/core/`

- **`atomic.py:atomic_write(path, data)`** (~L15) — 临时文件 + `os.replace`
- **`atomic.py:atomic_read(path, default=None)`** (~L40) — try/except 返回 default
- **`atomic.py:atomic_write_state(path, state)`** (~L60) — 备份轮换(`.bak.0`, `.bak.1`, `.bak.2`)
- **`enums.py:EvolutionStage`** (~L5) — 枚举: `L0_HUMAN`, `L1_META_LOOP`, `L2_EVOLUTION`, `L3_PORTFOLIO`
- **`enums.py:FactorPriority`** (~L12) — 枚举: `HIGH`, `MEDIUM`, `LOW`
- **`enums.py:FactorStatus`** (~L18) — 枚举: `PENDING`, `INJECTED`, `DECAYED`, `REJECTED`
- **`contracts.py`** (91 LOC) — 从 `fts.factor_engine.contracts` 重导出所有 TypedDict

### 3.4 `fts.data` — `fts/data.py`

- **`FTSDataProvider`** (~L30) — 包装 Data-Core 的 `UnifiedDataProvider`;惰性导入 `datacore`,`ImportError` 时回退
- **`get_ohlcv(symbol, start, end)`** (~L60) — 日 OHLCV bars
- **`get_fundamental(symbol)`** (~L100) — 基本面
- **`get_macro()`** (~L130) — 宏观指标
- **`get_news(symbol)`** / **`get_sentiment(symbol)`** (~L160) — 新闻 + 情绪
- **`get_market_state()`** (~L200) — 当前市场状态
- **`synthesize_ohlcv(days=500)`** (~L240) — 用于测试的合成 OHLCV

### 3.5 `fts.llm` — `fts/llm.py`

- **`LLMClient`** (ABC, ~L20) — 抽象 `complete(prompt)` 和 `generate_json(prompt)`
- **`OpenAIClient`** (~L60) — 使用 `openai` 库;读取 `OPENAI_API_KEY`/`OPENAI_BASE_URL`/`OPENAI_MODEL`
- **`AnthropicClient`** (~L120) — 使用 `anthropic` 库;读取 `ANTHROPIC_API_KEY`
- **`MockLLMClient`** (~L180) — 用于测试的确定性 mock
- **`get_llm_client()`** (~L220) — 通过环境变量自动检测后端(OpenAI → Anthropic → Mock 回退)

### 3.6 `fts.factor_engine`(最大模块)

#### `contracts.py` — TypedDict 契约(500+ LOC)

- **`FactorProgram`** — `factor_id`, `name`, `code` (str), `params` (dict), `economic_logic` (EconomicLogic), `signature` (FactorSignature)
- **`FactorSignature`** — `inputs` (list[str]), `outputs` (list[str]), `param_space` (dict)
- **`EconomicLogic`** — 4 维: `theory`, `behavioral`, `microstructure`, `institutional`(每个: 推理文本 + 强度 0-1)
- **`BacktestMetrics`** — `IC`, `ICIR`, `sharpe`, `max_drawdown`, `monotonicity`, `t_stat`, `turnover`
- **`EconomicScore`** — 4 维评分(每维 0-4)
- **`MultipleTestResult`** — `bonferroni_pass`, `fdr_pass`, `effective_n`, `p_value`
- **`FactorEvaluation`** — `BacktestMetrics` + `EconomicScore` + `MultipleTestResult` + `walk_forward` 复合
- **`ExperienceTrace`** — `success`/`failure` 因子追踪,带 `summary` markdown
- **`EvolutionState`** — generation, total_trials, success_count, failure_count, circuit_broken, last_run_at
- **`VerifierConfig`** — 锁定阈值: `min_ic`, `min_sharpe`, `min_monotonicity`, `max_turnover`
- **`BudgetConfig`** — `nightly_token_limit=200_000`, `max_consecutive_low_ic=5`, `max_failure_rate=0.3`
- **`L1MetaLoopState`** / **`L1VerifierConfig`** / **`L1BudgetConfig`** — L1 专用
- **`SeedCandidate`** — 种子因子候选描述符
- **`FactorPool`** — `factors` (list), `last_updated`, `weak_dimensions`

常量:
- **`EVOLUTION_VERSION = "8.10.0"`** — 继承自 FDT
- **`DEFAULT_VERIFIER_CONFIG`** — `min_ic=0.03`, `min_sharpe=1.0`, `min_monotonicity=0.6`, `max_turnover=0.6`
- **`DEFAULT_BUDGET_CONFIG`** — `nightly_token_limit=200_000`

#### `evolution_loop.py` — L2 主编排器

- **`EvolutionLoop`** 类 (490 LOC) — L2 主编排器
- **`run(generation)`** — 执行 6 步流水线:
  1. `macro_evolution` — 通过 `MacroEvolver` 进行 LLM 驱动的代码编辑
  2. `micro_evolution` — 通过 `MicroEvolver` 进行 optuna TPE 调优
  3. `evaluation_chain` — `EvaluationChain.evaluate()`
  4. `verifier` — `FactorVerifier.check()`(锁定)
  5. `experience_chain` — `ExperienceChain.update()`(成功/失败追踪)
  6. `state` — `EvolutionStateManager.save()`
- **`_check_circuit_breaker()`** — 检查 token 预算 / 连续低 IC / 失败率阈值
- **`_promote_to_elite(factor)`** — 将 elite JSON 写入 elite_dir

#### `meta_loop.py` — L1 Meta-Loop

- **`L1Verifier`** (~L30) — 锁定 verifier;检查 `economic_logic≥2/4` 维度 + `executable=True` + `not_duplicate`
- **`MetaStateManager`** (~L150) — 管理 `state.json` + 备份镜像
- **`FactorPoolManager`** (~L220) — 管理 `factor_pool.json`
- **`DebateQualityAnalyzer`** (~L280) — 读取 `debate_journal.json`;标志 `bullish_weak`/`bearish_weak`/`insufficient_rounds`/`no_debate`
- **`BootstrappingChain`** (~L350) — 已知因子族的 mock 模板(如 `bbands_width_reversion`)
- **`MetaLoop`** (~L420) — 编排器,组合以上所有;每日 09:00 运行

#### `portfolio_loop.py` — L3 Portfolio Loop

- **`L3Verifier`** (~L30) — 锁定;阈值: `combo_sharpe≥2.0`, `max_correlation≤0.3`, `combo_turnover≤0.5`, `decay_6m≤0.3`, `min_n_factors≥3`
- **`PortfolioStateManager`** (~L80) — 管理 `current_combo.json` + `agent_proposals/`
- **`PortfolioManager`** (~L140) — 主编排器:
  - **`load_elite_factors()`** — 从 elite_dir 读取
  - **`orthogonalize_factors(factors)`** — 基于 QR 的正交化
  - **`decay_test(factor, window=6m)`** — 比较前半段 vs 后半段均值
  - **`synthesize_signals(factors, method="equal_weight")`** — 支持 `equal_weight`/`sharpe_weight`/`lightgbm`
  - **`build_combo(factors)`** — 构建最终组合
  - **`generate_agent_proposals()`** — LLM 驱动的组合提案
  - **`inject_to_fdt(combo)`** — 写入 FDT 预期路径
- 每周一 06:00 运行

#### `seed_pool.py` — 种子因子池

- **`_SEED_DEFINITIONS`** (列表, 15 项, ~L20) — 每项含 `name`, `code` (Python 源码字符串), `EconomicLogic`
- 种子: `momentum`, `volatility_reversion`, `volume_flow`, `oi_change`, `basis`, `inventory_pct`, `capacity`, `macro_regime`, `rate_proxy`, `pmi_proxy`, `position_rank`, `warrant_change`, `value_factor`, `quality_factor`, `size_factor`
- **`get_seed_candidates()`** (~L380) — 返回 `SeedCandidate` 列表

#### `factor_program.py` — 安全沙箱

- **`ALLOWED_IMPORTS`** (~L10) — `numpy`, `pandas`, `scipy`, `statsmodels`, `talib`, `math`, `statistics`
- **`FORBIDDEN_NAMES`** (~L15) — `open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals` 等
- **`FORBIDDEN_MODULES`** (~L20) — `os`, `sys`, `subprocess`, `socket`, `ctypes`, `pickle` 等
- **`validate_factor_code(code)`** (~L40) — 使用 `ast.parse` 进行 AST 遍历,拒绝禁止的模式
- **`FactorExecutor`** (~L100) — 受限的 `__builtins__` + `_safe_import` 用于沙箱执行
- **`generate_factor_id(code)`** (~L260) — 基于代码 hash 返回 `fct_<sha1[:8]>`

#### `macro_evolution.py` — Macro 演化

- **`MacroEvolver`** (~L30) — LLM 驱动的代码编辑器
- **`evolve(parent_factor, experience_chain)`** (~L60) — 构建包含父因子 + 经验链上下文的 prompt;调用 LLM
- **`_apply_code_modification(code, modification)`** (~L150) — 支持通过正则的 `window_plus_5` mock 修改

#### `micro_evolution.py` — Micro 演化

- **`optimize_params(factor, objective, n_trials=100)`** (~L30) — optuna TPE 采样器,带早停(20 次连续无改进)
- **`_suggest_param(trial, name, default_value)`** (~L100) — 从默认值推断参数空间(int → int 范围, float → float 范围, bool → 分类)
- **`evolve_micro(factor, data)`** (~L150) — 返回带最佳参数的新 `FactorProgram`

#### `evaluation_chain.py` — 3 级评估链

- **`evaluate_backtest(factor, data, forward_returns)`** (~L40) — IC/ICIR/Sharpe/max_drawdown/monotonicity/t_stat/turnover
- **`evaluate_economic_logic(factor)`** (~L150) — 4 维评分(theory/behavioral/microstructure/institutional),每维 0-4
- **`evaluate_multiple_tests(factors, evaluations)`** (~L220) — Bonferroni + FDR + 基于 PCA 的 `effective_n`
- **`EvaluationChain.evaluate(factor, data, forward_returns, walk_forward=False)`** (~L300) — 编排以上三级 + 可选 walk-forward

#### `verifier.py` — 锁定 Verifier

- **`FactorVerifier`** (~L20) — `__init__` 后 `_locked=True`;修改配置抛出 `VerifierAlreadyLockedError`
- **`check(evaluation)`** (~L80) — 与 `VerifierConfig` 严格比较;返回 `VerifierResult`
- **`get_global_verifier()`** (~L180) — 带 `DEFAULT_VERIFIER_CONFIG` 的单例访问器

#### `state.py` — 状态管理

- **`generate_trace_id(prefix="ftr")`** (~L15) — 返回 `{prefix}_{8hex}_{timestamp}`
- **`generate_run_id()`** (~L30) — 返回 `run_{8hex}_{timestamp}`
- **`EvolutionStateManager`** (~L50) — `state.json` + 备份镜像
  - **`save_state(state)`** — 带备份的原子写入
  - **`load_state()`** — 读取,回退到备份

#### `experience_chain.py` — 经验链(LLM 记忆)

- **`ExperienceChain`** (~L30) — 在 `success/` 和 `failure/` 子目录中存储追踪
- **MAX_CHAIN_SIZE=100**,满时淘汰最旧的 20 条
- **`read_recent_for_llm()`** (~L100) — 返回 10 条成功 + 10 条失败追踪作为 markdown
- **`update_summary()`** (~L200) — 为 LLM 上下文生成 markdown 摘要

#### `program.py` — L0 Program.md 解析

- **`DEFAULT_PROGRAM_MD`** (~L10) — 带 YAML frontmatter 的 L0 模板
- **`parse_program_md(content)`** (~L100) — 正则提取 `market_regime`, `factor_preference`, `agent_llm`, `budget`, `risk_constraints`, `circuit_breakers_reviewed`

#### `walk_forward.py` — Walk-Forward 验证

- **`WalkForwardOptimizer`** (~L20) — 滚动窗口(`window_years=3`, `step_months=6`)
- **`optimize(factor, data)`** (~L80) — 计算 `ic_consistency`(IC>0 的窗口比例), `ic_volatility`
- **`consistency_score`** = 40% consistency + 30% volatility + 30% strength

#### `cost_model.py` — 交易成本模型

- **`TransactionCostModel`** (~L20) — 按市场配置(`futures`/`stock`/`etf`)
- **`adjust(metrics, market)`** (~L100) — 计算 `net_sharpe = gross_sharpe - cost_penalty`,其中 `cost_penalty = total_cost_bps * 12 / 0.15`

#### `regime.py` — 市场状态检测

- **`RegimeAwareSelector`** (~L20) — 从 OHLCV 检测 `bull`/`bear`/`oscillate`/`high_vol`/`low_vol`
- 使用 MA20 斜率(>±2%)和 ATR/价格比(>3%/<1%)进行状态分类
- **`select_factors(factors, regime)`** (~L150) — 按状态特定历史表现过滤

#### `stress_test.py` — 压力测试

- **`StressTester`** (~L20) — 5 个内置场景:
  - **原油暴跌** (2020-03 ~ 2020-05)
  - **双十一闪崩** (2016-11-11)
  - **股灾** (2015-06 ~ 2015-09)
  - **疫情冲击** (2020-02 ~ 2020-03)
  - **供给侧改革** (2016)
- 通过阈值: `max_drawdown ≤ 40%`

#### `monitor.py` — 循环状态检查

- **`LoopStatus`** (dataclass, ~L10) — `last_run_at`, `age_hours`, `circuit_broken`, `healthy`
- **`AllStatus`** (dataclass, ~L40) — L1/L2/L3 状态聚合
- **`check_loop(loop_name)`** (~L60) — 读取 `state.json`,计算 `age_hours`, `healthy = not circuit_broken AND age < 24h`
- **`check_all()`** (~L120) — 聚合 L1+L2+L3

### 3.7 `fts.pipeline` — `fts/pipeline/`

- **`base.py:DataPayload`** (dataclass, ~L10) — `data_type`, `symbol`, `payload`, `metadata`, `trace_id`
- **`base.py:ProcessingStage`** (Protocol, ~L30) — `input_type`/`output_type` + `process(payload)` 方法
- **`base.py:FactorPipeline`** (ABC, ~L60) — 抽象 `build_stages()` + 具体 `run()` 返回 `PipelineResult`
- **`factor_combiner.py:FactorCombiner`** (~L20) — z-score 归一化,可选 QR 正交化,基于权重的融合
- **`factor_combiner.py:CombinerConfig`** (~L10) — `weights`, `normalize_inputs=True`, `clip_sigma=3.0`, `orthogonalize=True`, `min_active_factors=3`

### 3.8 `fts.strategies` — `fts/strategies/`

- **`base_v2.py:RawSignal`** / **`ScoredSignal`** (dataclasses)
- **`base_v2.py:BaseStrategyV2`** (ABC) — 抽象 `name`, `score()`;默认 `compute()`, `filter()`, `validators`, `weight`, `depends_on`
- **`base_v2.py:StrategyV1Adapter`** — v1→v2 桥接(适配器模式)
- **`multi_factor_strategy.py:FACTOR_WEIGHTS`** (12 因子, 总=1.0): momentum 0.15, basis 0.15 等
- **`multi_factor_strategy.py:PURE_MOMENTUM_WEIGHTS`** — 60% 量价
- **`multi_factor_strategy.py:MultiFactorStrategy`** — 3 模式: `pure_momentum`, `long_short`, `neutral`
- 各因子计算函数: `_calc_momentum`, `_calc_volatility_reversion`, `_calc_volume_flow`, `_calc_oi_change`, `_calc_basis`, `_calc_macro`, `_calc_position_rank`, `_calc_warrant_change`, `_calc_inventory`, `_calc_capacity`, `_calc_pmi_proxy`, `_calc_rate_proxy`

### 3.9 `fts.scheduler` — `fts/scheduler/`

- **`engine.py:SchedulerEngine`** (~L20) — 包装 APScheduler `BackgroundScheduler`;`start(daemon=True)` 在 APScheduler 未安装时返回 False
- **`tasks.py:TaskSpec`** (dataclass) + **`TaskRegistry`** 含 register/unregister/list_enabled
- 默认任务: `l1_meta_loop` (cron `0 9 * * *`), `l2_evolution_loop` (`0 23 * * *`), `l3_portfolio_loop` (`0 6 * * 1`), `health_check` (`*/10 * * * *`)
- **`watchdog.py:ProcessWatchdog`** — 重启策略: 30s 内 3 次重启 → 5min 熔断器
- **`hotswap.py:HotSwapWatcher`** — 使用 `watchdog` 库;通过 `importlib.reload` 进行 `_reload_module()`;库缺失时静默回退

### 3.10 `fts.monitor` — `fts/monitor/`

- **`__init__.py:LoopStatusReport`** / **`SystemStatusReport`** (dataclasses)
- **`__init__.py:check_loop_status()`** / **`check_all_status()`** / **`format_status_report()`** / **`status_report_to_json()`**
- **`http_server.py:MetricsHTTPServer`** (~L20) — 纯 stdlib HTTP 服务器,`127.0.0.1:9100`
  - 端点: `/health` (JSON), `/metrics` (Prometheus 文本), `/` (HTML 仪表板)
  - `start()` 在守护线程中运行
- **`elite_tracker.py:TrackingSnapshot`** (TypedDict)
- **`elite_tracker.py:EliteFactorTracker`** — `init_tracker()`, `update()`, `get_decaying(max_consecutive=4)`, `auto_retire()`, `report()`
  - **`_calc_decay_6m()`** — 比较前半段 vs 后半段均值 IC
- **`elite_tracker.py:AutoRetireManager`** — 基于 cooldown_days 的自动退役

---

## 4. 模块间依赖关系

### 高层依赖图

```
fts.cli
  ├── fts.config.settings        (get_config)
  ├── fts.data                   (_prepare_data)
  ├── fts.llm                    (get_llm_client)
  ├── fts.factor_engine.*        (所有循环 + 因子管理)
  ├── fts.pipeline               (FactorPipeline)
  ├── fts.strategies             (MultiFactorStrategy)
  ├── fts.scheduler              (SchedulerEngine, TaskRegistry)
  └── fts.monitor                (check_all_status)

fts.factor_engine.evolution_loop
  ├── fts.factor_engine.contracts          (TypedDicts)
  ├── fts.factor_engine.macro_evolution    (MacroEvolver)
  ├── fts.factor_engine.micro_evolution    (MicroEvolver)
  ├── fts.factor_engine.evaluation_chain   (EvaluationChain)
  ├── fts.factor_engine.verifier           (FactorVerifier)
  ├── fts.factor_engine.state              (EvolutionStateManager)
  ├── fts.factor_engine.experience_chain   (ExperienceChain)
  ├── fts.factor_engine.cost_model         (TransactionCostModel)
  ├── fts.factor_engine.regime             (RegimeAwareSelector)
  ├── fts.factor_engine.stress_test        (StressTester)
  ├── fts.llm                              (get_llm_client)
  └── fts.core.atomic                      (atomic_write_state)

fts.factor_engine.meta_loop
  ├── fts.factor_engine.contracts
  ├── fts.factor_engine.state
  ├── fts.factor_engine.seed_pool
  └── fts.factor_engine.verifier           (L1Verifier — 锁定)

fts.factor_engine.portfolio_loop
  ├── fts.factor_engine.contracts
  ├── fts.factor_engine.state
  ├── fts.factor_engine.verifier           (L3Verifier — 锁定)
  ├── fts.factor_engine.walk_forward
  └── fts.pipeline.factor_combiner         (orthogonalize_factors)

fts.factor_engine.evaluation_chain
  ├── fts.factor_engine.contracts
  ├── fts.factor_engine.walk_forward
  └── numpy/pandas/scipy/statsmodels       (统计计算)

fts.core.contracts  →  fts.factor_engine.contracts  (重导出)
fts.strategies.base_v2  →  fts.pipeline.base  (StrategyV1Adapter 包装 pipeline)
```

### 关键不变性

- **`fts.core`** 是基础层 — 不依赖上层模块
- **`fts.factor_engine.contracts`** 是 TypedDict 的单一真源;`fts.core.contracts` 仅重导出
- **`fts.cli`** 是顶层编排器,依赖所有子系统
- **`fts.factor_engine`** 有内部子依赖,但不依赖 `fts.pipeline`, `fts.strategies`, `fts.scheduler`, 或 `fts.monitor`
- **`fts.strategies`** 依赖 `fts.pipeline`(通过 `StrategyV1Adapter`)
- **`fts.scheduler`** 完全解耦 — 仅依赖 stdlib + 可选 `apscheduler`/`watchdog`

---

## 5. 外部依赖

### 必需依赖(`pyproject.toml`)

| 库 | 版本 | 用途 |
|---|---|---|
| `numpy` | `>=1.24` | 数值计算(IC, Sharpe, 矩阵运算) |
| `pandas` | `>=2.0` | DataFrame,时间序列 |
| `pyyaml` | `>=6.0` | YAML 配置解析 |

### 可选依赖(extras)

| Extra | 库 | 用途 |
|---|---|---|
| `evolution` | `optuna` | 用于 micro 演化的 TPE 贝叶斯优化 |
| `llm` | `openai` | OpenAI API 客户端(默认后端) |
| `llm` | `anthropic` | Anthropic Claude API 客户端(替代) |
| `data` | `datacore` | Data-Core UnifiedDataProvider 集成 |
| `dev` | `pytest` | 测试运行器 |
| `dev` | `pytest-cov` | 覆盖率报告 |

### 隐式/软依赖(静默回退)

| 库 | 用途 |
|---|---|
| `scipy` | 统计检验,QR 分解 |
| `statsmodels` | 多重检验校正 |
| `talib` | 技术分析指标(沙箱中允许) |
| `apscheduler` | 基于 cron 的任务调度(降级为 no-op) |
| `watchdog` | 文件系统监视用于热交换(降级为 no-op) |
| `lightgbm` | 可选信号合成方法 |

---

## 6. 运行/构建/测试方式

### 安装

来自 `docs/deploy/INSTALL.md`:

```bash
# 需要 Python 3.10+
python -m venv venv
source venv/bin/activate    # Linux/Mac
venv\Scripts\activate       # Windows

pip install -e .[dev,evolution]
# 可选 extras:
pip install -e .[llm,data]
```

### 环境设置

来自 `start_fts.ps1`:

```powershell
$env:OPENAI_API_KEY = "your-key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"
$env:OPENAI_MODEL = "deepseek-v4-flash"
$env:FTS_CONFIG_FILE = "config/settings.yaml"
$env:FTS_MEMORY_DIR = "memory"
```

### CLI 命令(入口点: `fts = "fts.cli:main"`)

```bash
fts version                                   # 打印版本
fts monitor                                   # 显示 L1/L2/L3 循环状态
fts evolution run --mode single               # 单因子演化
fts evolution run --mode csi300               # CSI300 多标的演化
fts meta-loop run                             # L1 Meta-Loop(每日 09:00)
fts portfolio run                             # L3 Portfolio Loop(每周一 06:00)
fts scheduler run                             # 启动带默认任务的 APScheduler
fts scheduler list                            # 列出已启用任务
fts factor list                               # 列出 elite 因子
fts factor show <factor_id>                   # 显示因子详情
```

### 测试

来自 `pyproject.toml` pytest 配置:

```bash
pytest                                         # 所有测试带覆盖率
pytest --cov=fts --cov-report=term-missing -v  # 默认调用
pytest tests/test_e2e.py                       # 仅 E2E 测试
pytest tests/factor_engine/                    # 仅因子引擎测试
```

当前状态: **1,231 测试通过, 96% 覆盖率**。

### CI/CD — `.github/workflows/ci.yml`

- 矩阵: Python 3.10 / 3.11 / 3.12
- 步骤: `pip install .[dev,evolution]` → `pytest` 带覆盖率 → codecov 上传

### 生产部署 — `docs/deploy/WINDOWS.md`

三种模式:
1. **任务计划程序**(开发) — Windows 任务计划程序触发 `start_fts.ps1`
2. **NSSM Windows 服务**(生产) — `nssm install FTS python fts/cli.py scheduler run`
3. **后台进程** — `pythonw fts/cli.py scheduler run` 带 stdio 重定向

### HTTP metrics 服务器

运行时 FTS 暴露:
- `http://127.0.0.1:9100/health` — JSON 健康状态
- `http://127.0.0.1:9100/metrics` — Prometheus 文本格式
- `http://127.0.0.1:9100/` — HTML 仪表板

---

## 7. 核心设计模式

### 7.1 Verifier 锁定协议
**文件:** `fts/factor_engine/verifier.py`

`FactorVerifier`, `L1Verifier`, `L3Verifier` 都在 `__init__` 末尾设置 `_locked=True`。任何后续修改 `VerifierConfig` 的尝试都会抛出 `VerifierAlreadyLockedError`。这确保评估标准无法被 LLM 在运行中博弈。`get_global_verifier()` 是进程级单例。

### 7.2 Loop Engineering 范式
三个自治循环(L1/L2/L3)具有不同的节奏(每日/每夜/每周)和人在回路顶层(L0 Program.md)。每个循环有自己的 `StateManager`, `Verifier`, 和 `Budget`。见 `docs/harness/01-architecture.md`。

### 7.3 安全沙箱执行
**文件:** `fts/factor_engine/factor_program.py`

`FactorExecutor` 运行 LLM 生成的因子代码,具有:
- 白名单导入(`numpy`, `pandas`, `scipy`, `statsmodels`, `talib`, `math`, `statistics`)
- 黑名单名称(`open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals`)
- 黑名单模块(`os`, `sys`, `subprocess`, `socket`, `ctypes`, `pickle`)
- 通过 `validate_factor_code()` 在任何执行前进行 AST 预验证
- 受限的 `__builtins__` 字典

### 7.4 Strategy v2 可插拔框架
**文件:** `fts/strategies/base_v2.py`

`BaseStrategyV2` ABC 定义 `name`(抽象), `score()`(抽象),并提供默认 `compute()`, `filter()`, `validators`, `weight`, `depends_on`。新策略扩展 ABC 并只覆盖需要的部分。`StrategyV1Adapter` 将 v1 策略适配到 v2 接口(适配器模式)。

### 7.5 Pipeline + Stage Protocol
**文件:** `fts/pipeline/base.py`

`ProcessingStage` 是一个 `Protocol`,含 `input_type`/`output_type` + `process(payload)`。`FactorPipeline` 是一个 ABC,含抽象 `build_stages()` 和具体 `run()` 编排器,返回 `PipelineResult`。Stage 可组合,通过 Protocol 进行类型检查。

### 7.6 适配器模式
**文件:** `fts/strategies/base_v2.py`

`StrategyV1Adapter` 桥接 v1 策略接口到 v2 ABC,允许渐进迁移而不破坏现有策略。

### 7.7 原子文件操作
**文件:** `fts/core/atomic.py`

`atomic_write()` 使用临时文件 + `os.replace`(在 POSIX 和 Windows NTFS 上原子)。`atomic_write_state()` 添加备份轮换(`.bak.0`, `.bak.1`, `.bak.2`)实现崩溃安全的状态持久化。所有状态管理器(`EvolutionStateManager`, `MetaStateManager`, `PortfolioStateManager`)使用此原语。

### 7.8 单例全局 Verifier
**文件:** `fts/factor_engine/verifier.py`

`get_global_verifier()` 返回以 `DEFAULT_VERIFIER_CONFIG` 初始化的进程级单例。确保所有 L2 运行间评估标准一致。

### 7.9 经验链(LLM 记忆)
**文件:** `fts/factor_engine/experience_chain.py`

`ExperienceChain` 在独立子目录中存储成功和失败追踪。`read_recent_for_llm()` 返回 10 条成功 + 10 条失败追踪作为下次 LLM 调用的 markdown 上下文,防止 LLM 重复过去的错误。最多 100 条,FIFO 淘汰最旧的 20 条。

### 7.10 熔断器
**文件:** `fts/factor_engine/evolution_loop.py` (`_check_circuit_breaker`)

三个阈值停止 L2 演化:
- Token 预算耗尽(`nightly_token_limit=200_000`)
- 连续低 IC 试验(`max_consecutive_low_ic=5`)
- 失败率超限(`max_failure_rate=0.3`)

### 7.11 静默降级
所有可选依赖(optuna, openai, anthropic, datacore, apscheduler, watchdog, lightgbm)都惰性导入,优雅回退到 Mock 实现。系统在零可选依赖安装的情况下端到端运行(使用 `MockLLMClient` 和合成数据)。

---

## 8. 配置文件说明

### 8.1 `pyproject.toml`

**用途:** Python 项目构建配置。

关键部分:
- `[project]` — name="fts", version="1.0.0", requires-python=">=3.10"
- `[project.dependencies]` — numpy, pandas, pyyaml(必需)
- `[project.optional-dependencies]` — `evolution`, `llm`, `data`, `dev` extras
- `[project.scripts]` — `fts = "fts.cli:main"`(CLI 入口点)
- `[tool.pytest.ini_options]` — `--cov=fts --cov-report=term-missing -v`

### 8.2 `config/settings.yaml`

**用途:** 被 `FTSConfig.load_config()` 消费的默认 YAML 配置。

关键字段:
- `default_market: "futures"` — 数据获取的默认市场
- `llm_backend: "openai"` — LLM 提供商选择
- `max_generations: 10` — 每次 L2 演化运行的代数
- `micro_trials_per_generation: 50` — 每次 micro 演化的 optuna 试验数
- `portfolio_max_factors: 20` — L3 组合 combo 最大因子数

### 8.3 `fts/config/settings.py`

**用途:** 配置加载器,优先级: YAML 文件 → 环境变量 → 默认值。`FTSConfig` dataclass 持有所有运行时配置;`get_config()` 是惰性单例。

### 8.4 运行时状态文件(在 `memory/` 目录下,运行时创建)

| 文件 | 用途 | 拥有者 |
|---|---|---|
| `memory/state.json` | L2 演化状态(代数, 试验, 计数) | `EvolutionStateManager` |
| `memory/l1_state.json` | L1 Meta-Loop 状态 | `MetaStateManager` |
| `memory/l3_state.json` | L3 Portfolio 状态 | `PortfolioStateManager` |
| `memory/factor_pool.json` | L1 发现的因子池 | `FactorPoolManager` |
| `memory/debate_journal.json` | 辩论质量记录 | `DebateQualityAnalyzer` |
| `memory/current_combo.json` | 当前 L3 组合 combo | `PortfolioManager` |
| `memory/agent_proposals/` | LLM 生成的组合提案 | `PortfolioManager` |
| `memory/experience/success/` | LLM 上下文的成功追踪 | `ExperienceChain` |
| `memory/experience/failure/` | LLM 上下文的失败追踪 | `ExperienceChain` |
| `elite/*.json` | 晋升的 elite 因子(每因子一文件) | `EvolutionLoop._promote_to_elite()` |
| `Program.md` | L0 周度人工设置 | 人工(由 `program.py` 解析) |

### 8.5 `.github/workflows/ci.yml`

**用途:** GitHub Actions CI 矩阵(Python 3.10/3.11/3.12),运行 `pytest` 带覆盖率并上传到 codecov。

### 8.6 `start_fts.ps1`

**用途:** PowerShell 启动脚本,设置环境变量(`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL=deepseek-v4-flash`, `FTS_CONFIG_FILE`, `FTS_MEMORY_DIR`)后启动 FTS。

### 8.7 `CLAUDE.md`

**用途:** 贡献者和 AI 助手的 AI 编码标准。要求:
- think-before-code, simplicity-first, surgical-modifications, goal-driven, HARNESS-first
- 13 点 commit 清单要求代码更改前更新文档

### 8.8 `.gitignore`

**用途:** 标准 Python 忽略 + 研究/实验文件(如 `brain_field_test.py`)。

### 8.9 文档目录 `docs/`

| 文件 | 用途 |
|---|---|
| `harness/01-architecture.md` | 架构图, L0-L3 层, Verifier 锁定协议, 运行时调度 |
| `harness/02-lifecycle.md` | 7 个 FDT 分离阶段, 文件命名约定, 语义版本控制, trace_id/run_id 规则 |
| `harness/08-gap-analysis.md` | 7 个 GAP 全部关闭(P0×2, P1×2, P2×3) |
| `harness/09-advancement-plan.md` | 里程碑: v0.1.0 → v0.2.0 → v0.3.0(当前) |
| `production_plan.md` | Phase A/B/C/D 生产路线图 |
| `deploy/INSTALL.md` | 安装步骤 |
| `deploy/WINDOWS.md` | 3 种 Windows 部署模式(任务计划程序 / NSSM / 后台) |

---

## 总结

FTS 是一个架构良好的 AI 原生量化因子系统,实现了 **Loop Engineering 范式**,由人在回路 L0 层监督三个自治层(L1 每日 / L2 每夜 / L3 每周)。代码库强调:

1. **契约优先设计** — 所有数据形状在 `fts/factor_engine/contracts.py` 中声明为 TypedDict
2. **安全** — 锁定 Verifier 协议防止评估博弈;安全沙箱阻止 LLM 生成代码中的危险操作
3. **韧性** — 原子文件写入,备份轮换,可选依赖静默降级,熔断器,进程看门狗
4. **可观测性** — trace_id 全链路追踪,HTTP metrics 服务器,带自动退役的 elite 因子追踪器
5. **可扩展性** — Strategy v2 可插拔 ABC,Pipeline+Stage Protocol,适配器向后兼容
6. **测试覆盖** — 1,231 测试,96% 覆盖率,含 10 个 E2E 场景

系统已生产就绪,具有文档化的 Windows 部署(NSSM 服务 / 任务计划程序),通过 GitHub Actions 的 CI/CD,以及作为活文档的全面 HARNESS 工程规范在 `docs/harness/` 中。
