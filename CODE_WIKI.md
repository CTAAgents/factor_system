# FTS (Factor Trading System) — Code Wiki

> **版本**: v2.7.0 | **最后更新**: 2026-08-05
>
> 本文档是 FTS 项目的代码级参考文档，面向开发者阅读。

---

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构](#2-系统架构)
3. [目录结构](#3-目录结构)
4. [核心模块详解](#4-核心模块详解)
   - 4.1 [核心契约层 `fts.core`](#41-核心契约层-ftscore)
   - 4.2 [因子引擎 `fts.factor_engine`](#42-因子引擎-ftsfactor_engine)
   - 4.3 [配置系统 `fts.config`](#43-配置系统-ftsconfig)
   - 4.4 [管线层 `fts.pipeline`](#44-管线层-ftspipeline)
   - 4.5 [策略层 `fts.strategies`](#45-策略层-ftsstrategies)
   - 4.6 [数据层 `fts.data`](#46-数据层-ftsdata)
   - 4.7 [数据源适配器 `fts.data_sources`](#47-数据源适配器-ftsdata_sources)
   - 4.8 [调度层 `fts.scheduler`](#48-调度层-ftsscheduler)
   - 4.9 [监控层 `fts.monitor`](#49-监控层-ftsmonitor)
   - 4.10 [LLM 客户端 `fts.llm`](#410-llm-客户端-ftsllm)
   - 4.11 [CLI 入口 `fts.cli`](#411-cli-入口-ftscli)
5. [关键类/函数速查表](#5-关键类函数速查表)
6. [依赖关系图](#6-依赖关系图)
7. [项目运行方式](#7-项目运行方式)
8. [配置系统](#8-配置系统)
9. [数据流与执行流程](#9-数据流与执行流程)
10. [测试体系](#10-测试体系)

---

## 1. 项目概述

**FTS (Factor Trading System)** 是一个 AI 原生的量化因子智能系统，从 FDT 项目剥离的独立因子策略系统。专注于多因子挖掘、评估、组合与演化。

### 核心定位

```
MCP/akshare（腾讯自选股/东方财富 API）← FTS（因子智能 → 交易信号）→ 下游消费系统（FDT / 手动执行）
```

### 核心能力

| 能力 | 说明 |
|------|------|
| **三层循环** | L1 Meta-Loop（每日知识补给）+ L2 Evolution Loop（因子演化）+ L3 Portfolio Loop（组合构建） |
| **多市场支持** | A 股、ETF、82 个期货品种（25 核心 + 57 全量） |
| **因子种子库** | 563 个种子因子（股票 482 + 期货 81），覆盖 14 大因子家族 |
| **五层逻辑审查** | 消融实验 + 场景测试 + SHAP 分析 + 鲁棒性审查 + 因果验证 |
| **全自动调度** | 基于 APScheduler 的 L1/L2/L3 定时任务 |
| **多源数据融合** | DuckDB → TQ_LOCAL → TQ_PYTHON → AKShare → SYNTHETIC 五级降级 |
| **因子质检** | 10 维质量评分卡（A/B/C 三级准入），6 项强制审计 |

### 技术栈

- **语言**: Python 3.10+（C++ 核心引擎已剥离为独立组件）
- **核心依赖**: numpy, pandas, scipy, pyyaml, shap
- **可选依赖**: optuna（演化）, openai/anthropic（LLM）, akshare（MCP 数据）, scikit-learn（组合构建）
- **数据存储**: DuckDB（因子目录 + 期货 K 线）
- **调度**: APScheduler
- **监控**: HTTP 内置仪表盘

---

## 2. 系统架构

### 2.1 三层循环架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        L1 Meta-Loop（每日 08:30）                  │
│  市场感知（Web 收集）→ debate 分析 → Bootstrapping → L1 Verifier  │
│  输出：种子候选注入 L2 种子池                                      │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ 种子候选
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                     L2 Evolution Loop（每日 23:00）                 │
│  UCT 父因子选择 → 宏观演化（LLM）→ 微观演化（optuna）→ 三级评估链   │
│  → Verifier 判定 → 质量评分卡 → 精英因子晋升                       │
└─────────────────────────────────┬───────────────────────────────┘
                                  │ 精英因子
                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│                     L3 Portfolio Loop（每日 20:00）                 │
│  加载精英因子 → 信号合成 → Regime 自适应 → 正交化 → 衰减检验 →    │
│  组合构建 → Verifier → 注入 FDT / 生成信号                        │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 五层逻辑审查框架

```
L1 输入敏感性     ablation.py         5 种消融模式
L2 宏观行为       scenarios/          23 个典型市场场景
L3 局部可解释     shap_analyzer.py    SHAP 极端样本归因
L4 鲁棒性         robustness.py       对抗样本/缺失值/分布外
L5 因果结构       causal_validator.py 自然实验事件验证
```

### 2.3 数据流架构

```
期货数据源（5 级降级）:
  DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
                    ↓
             字段增强层（并行）:
               WIND   → settle / oi_change / 期权 IV
               IFIND  → EDB 宏观 / 产业链数据
                    ↓
              OHLCVFusion（5 种融合策略）
                    ↓
              FTSDataProvider（统一数据入口）
                    ↓
              因子引擎（三层循环）
                    ↓
              交易信号（ScoredSignal）
```

---

## 3. 目录结构

```
fts/                          # 核心源码包
├── __init__.py               # 包初始化，版本定义 v2.7.0，自动加载 .env
├── cli.py                    # 统一命令行入口（argparse）
│
├── config/                   # 配置系统
│   ├── __init__.py           # 导出 FTSConfig / 评分卡配置
│   ├── settings.py           # FTSConfig 数据类，YAML+环境变量+默认值
│   └── factor_quality_card_config.py  # 质量评分卡维度权重/阈值配置
│
├── core/                     # 核心契约层
│   ├── __init__.py
│   ├── enums.py              # FTS 特有枚举
│   ├── contracts.py          # 因子引擎契约重导出
│   └── atomic.py             # 原子文件写入/读取（临时文件+rename）
│
├── factor_engine/            # 因子引擎（核心模块）
│   ├── __init__.py           # 导出所有核心组件
│   ├── contracts.py          # L1/L2/L3 三层 TypedDict 契约
│   ├── factor_program.py     # 因子程序接口（安全沙箱编译执行）
│   ├── seed_pool.py          # 种子池管理
│   ├── seed_loader.py        # YAML 种子因子加载器
│   ├── macro_evolution.py    # 宏观演化（LLM 改逻辑）
│   ├── micro_evolution.py    # 微观演化（optuna 贝叶斯调参）
│   ├── evaluation_chain.py   # 三级评估链（L1 回测/L2 经济逻辑/L3 多重检验）
│   ├── verifier.py           # Verifier 协议（锁定评估机制）
│   ├── standardizer.py       # 6 种标准化方法
│   ├── experience_chain.py   # 经验链存储
│   ├── state.py              # 演化状态管理 + trace_id 全链路
│   ├── evolution_loop.py     # L2 主循环（夜间因子演化）
│   ├── meta_loop.py          # L1 主循环（每日知识补给 + Bootstrapping）
│   ├── portfolio_loop.py     # L3 主循环（组合构建 + 正交化 + 信号产出）
│   ├── monitor.py            # 循环状态检查
│   ├── program.py            # 程序配置与加载
│   ├── factor_quality_card.py # 因子质量评分卡（10 维评分）
│   ├── audit.py              # 6 项强制审计
│   ├── factor_inspector.py   # 因子定时巡检与自动降级
│   ├── ablation.py           # 消融实验（5 种模式）
│   ├── shap_analyzer.py      # SHAP 分析
│   ├── robustness.py         # 鲁棒性审查
│   ├── causal_validator.py   # 因果结构验证
│   ├── walk_forward.py       # 走航验证
│   ├── stress_test.py        # 压力测试
│   ├── regime.py             # Market Regime 检测
│   ├── cost_model.py         # 成本模型
│   ├── backtest_pipeline.py  # 回测管线
│   ├── factor_optimizer.py   # 因子优化器
│   │
│   ├── factor_db/            # DuckDB 因子数据库
│   │   ├── __init__.py
│   │   ├── schema.py         # 表结构定义（factor_catalog / evaluations / versions / correlations）
│   │   ├── repository.py     # CRUD 操作 + 批量查询 + 排行榜
│   │   ├── lineage.py        # 因子血缘追踪
│   │   └── migrate_from_json.py  # JSON 迁移脚本
│   │
│   └── seed_data/            # 种子因子定义库
│       ├── __init__.py
│       ├── wq101.py          # 101 个 WorldQuant Alpha 因子
│       ├── qlib158.py        # 158 个 Qlib 因子
│       ├── gtja191.py        # 191 个国泰君安 Alpha 因子
│       ├── fundamental_seeds.py  # 23 个基本面/另类/宏观因子
│       ├── alpha_ops.py      # Alpha 算子库
│       ├── alpha_ops_numba.py # Numba 加速算子
│       └── loader.py         # 动态加载器
│
├── pipeline/                 # 因子推演管线
│   ├── __init__.py
│   ├── base.py               # FactorPipeline 抽象基类 + ProcessingStage 协议
│   ├── factor_combiner.py    # 多因子加权/融合器
│   ├── factor_quality_inspection.py  # 因子质检过滤层
│   └── batch_quality_inspector.py    # 批量质检
│
├── strategies/               # 策略层
│   ├── __init__.py
│   ├── base_v2.py            # v2 策略可插拔框架（compute → filter → score）
│   ├── multi_factor_strategy.py  # 四维因子加权打分策略
│   ├── strategy_evolution.py     # 策略进化（制度自适应/动态权重/多周期融合）
│   └── rules/                # 策略规则知识库
│
├── scheduler/                # 调度层
│   ├── __init__.py
│   ├── tasks.py              # TaskSpec 定义 + TaskRegistry 注册表
│   ├── engine.py             # SchedulerEngine（APScheduler 包装）
│   ├── jobs.py               # 定时任务工作函数
│   ├── watchdog.py           # 进程级看门狗
│   └── hotswap.py            # 热重载支持
│
├── monitor/                  # 监控层
│   ├── __init__.py           # 循环状态检查/Web UI/因子跟踪/逻辑监控
│   ├── data_quality_monitor.py  # 数据质量监控
│   ├── elite_tracker.py      # 精英因子样本外跟踪
│   ├── http_server.py        # 内置 Web UI 仪表盘
│   ├── logic_monitor.py      # 因子行为逻辑监控
│   ├── k8s_deploy.py         # K8s 部署配置
│   └── prometheus_setup.py   # Prometheus 监控集成
│
├── data.py                   # FTSDataProvider（统一数据入口）
├── data_futures.py           # FuturesDataProvider（DuckDB + AKShare）
├── data_futures_fundamental.py  # 期货基本面数据
├── data_fundamental.py       # 基本面数据层
├── data_mcp.py               # MCP 数据适配层（腾讯 HTTP API）
├── data_mcp_bridge.py        # MCP 桥接层
├── data_cache.py             # 数据缓存
├── llm.py                    # LLM 客户端统一接口
└── talib_bridge.py           # TA-Lib 桥接

data_sources/                 # 期货多源数据适配器
├── __init__.py
├── base.py                   # BaseFuturesSource 抽象基类
├── aggregator.py             # FuturesDataAggregator（多源调度 + 熔断器）
├── fusion.py                 # OHLCVFusion（5 种融合策略）
├── tq_source.py              # 通达信本地数据源
├── wind_source.py            # Wind 数据源
├── ifind_source.py           # iFinD 数据源
└── migrate.py                # 数据库迁移

seeds/                        # YAML 种子因子定义
├── stock/                    # 股票种子因子 YAML
│   ├── builtin.yaml
│   ├── wq101.yaml
│   ├── qlib158.yaml
│   ├── gtja191.yaml
│   └── fundamental.yaml
└── futures/                  # 期货种子因子 YAML（13 个家族）
    ├── momentum.yaml
    ├── volatility.yaml
    ├── term_structure.yaml
    ├── high_frequency.yaml
    ...（共 15 个 YAML 文件）

tests/                        # 63 个测试文件，1700+ 测试用例
scripts/                      # 工具脚本（17 个）
config/                       # 配置文件
memory/                       # 运行时持久化（自动创建）
docs/                         # 项目文档
```

---

## 4. 核心模块详解

### 4.1 核心契约层 `fts.core`

**文件**: `fts/core/`

**职责**: 定义 FTS 特有的核心枚举和契约，所有模块必须基于本层实现。

#### `fts.core.enums`

| 枚举 | 值 | 说明 |
|------|-----|------|
| `EvolutionStage` | `l0_human` / `l1_meta_loop` / `l2_evolution` / `l3_portfolio` | 因子演化阶段标识 |
| `FactorPriority` | `high` / `medium` / `low` | 因子优先级 |
| `FactorStatus` | `pending` / `injected` / `decayed` / `rejected` | 种子池因子状态 |

#### `fts.core.atomic`

| 函数 | 说明 |
|------|------|
| `atomic_write(path, data)` | 原子写入 JSON（临时文件 + os.replace） |
| `atomic_read(path, default)` | 安全读取 JSON（不存在/不合法返回 default） |
| `atomic_write_state(path, state, backup_count)` | 原子写入状态文件 + 备份轮转 |

#### `fts.core.contracts`

重导出 `fts.factor_engine.contracts` 中的所有契约，提供统一导入入口。

---

### 4.2 因子引擎 `fts.factor_engine`

**文件**: `fts/factor_engine/`

**职责**: FTS 的核心，实现 L1/L2/L3 三层循环 + 因子全生命周期管理。

#### 4.2.1 契约层 `contracts.py`

定义所有核心 TypedDict 契约。文件较大（约 750 行），是 HARNESS §契约优先的核心。

| 契约 | 用途 | 关键字段 |
|------|------|---------|
| `FactorProgram` | 因子程序定义 | factor_id, name, code, params, signature, economic_logic, market, family |
| `FactorSignature` | 因子输入/输出签名 | input_fields, output_type, frequency, lookback |
| `EconomicLogic` | 四维经济逻辑评分 | theory, behavioral, microstructure, institutional, narrative |
| `BacktestMetrics` | L1 回测指标 | ic, icir, sharpe, max_drawdown, monotonicity, turnover |
| `EconomicScore` | L2 经济逻辑评分 | theory, behavioral, microstructure, institutional, dimensions_passed |
| `MultipleTestResult` | L3 多重检验 | bonferroni_p, fdr_q, adjusted_t, passed |
| `FactorEvaluation` | 三级评估链输出 | factor_id, level_1_backtest, level_2_economic, level_3_multiple, passed |
| `ExperienceTrace` | 经验链轨迹 | trace_id, factor_id, mutation_type, evaluation, success, lessons |
| `EvolutionState` | 演化状态 | run_id, status, last_generation, tokens_consumed |
| `VerifierConfig` | Verifier 配置 | min_ic, min_sharpe, max_drawdown, max_turnover |
| `BudgetConfig` | 预算配置 | nightly_token_limit, max_generation, circuit_breaker_* |
| `SeedCandidate` | L1 种子候选 | candidate_id, name, code, source, economic_logic |
| `PortfolioSignal` | L3 信号合成输出 | factor_id, weight, sharpe, ic, decay_6m, retained |
| `PortfolioCombo` | L3 组合构建输出 | combo_id, signals, combo_sharpe, synthesis_mode |
| `AgentOptimizationProposal` | 优化建议 | proposal_id, agent_name, suggested_changes |

**FactorMarket 类型**: `Literal["futures", "stock", "etf", "bond", "multi"]`

**FactorFamily 类型**: `Literal["trend", "mean_reversion", "carry", "seasonality", "cross_section", "fundamental", "technical", "microstructure", "macro", "behavioral", "liquidity", "volatility", "volume", "multi_factor", "other"]`（14 大类）

**规范化工具**:
- `normalize_factor_program(factor, market_hint)` — 规范化因子定义
- `normalize_factor_signature(signature)` — 兼容新旧版签名
- `detect_factor_market(symbols, market_hint)` — 自动检测市场类型

#### 4.2.2 因子程序 `factor_program.py`

**职责**: 因子代码的图灵完备表示 + 安全沙箱编译执行。

| 关键类/函数 | 说明 |
|------------|------|
| `FactorCompileError` | 因子程序编译/验证失败异常 |
| `FactorExecutor` | 因子执行器（编译 → 缓存 → 执行） |
| `create_factor_program(...)` | 创建 FactorProgram 实例 |
| `generate_factor_id()` | 生成唯一因子 ID（`fct_<8hex>`） |
| `validate_factor_code(code)` | 安全沙箱验证（AST 静态分析） |

**安全沙箱约束**:
- 允许: numpy, pandas, scipy, statsmodels, talib, math, statistics
- 禁止: os, sys, subprocess, socket, requests, ctypes, 等危险模块
- 禁止: exec, eval, compile, open, getattr, 等危险内置函数

#### 4.2.3 种子池 `seed_pool.py` & `seed_loader.py`

**职责**: 管理种子因子池，提供 L2 演化的初始种群。

| 关键类/函数 | 说明 |
|------------|------|
| `SeedPool` | 种子池管理（按市场隔离） |
| `get_default_seed_pool(market)` | 获取默认种子池 |
| `compute_seed_correlations(seed_pool)` | 种子因子相关性计算 |
| `load_all_yaml_seeds(trace_id)` | 从 YAML 加载所有种子 |
| `load_factors_from_dir(directory)` | 从目录加载 YAML 种子 |
| `verify_yaml_integrity()` | 校验 YAML 文件完整性 |

**种子因子来源**:
- 股票: 9 内置 + 101 WQ101 + 158 Qlib + 191 GTJA + 23 基本面 = 482 个
- 期货: 81 个（14 大因子家族，覆盖动量/波动率/期限结构/高频等）

#### 4.2.4 宏观演化 `macro_evolution.py`

**职责**: LLM 驱动的因子逻辑变更（宏观演化）。

| 关键类/函数 | 说明 |
|------------|------|
| `MacroEvolver` | 宏观演化器（LLM 改逻辑） |
| `MockLLMClient` | 模拟 LLM 客户端（测试用） |
| `get_default_llm_client()` | 获取默认 LLM 客户端 |

#### 4.2.5 微观演化 `micro_evolution.py`

**职责**: optuna 贝叶斯调参（微观演化）。

| 关键类/函数 | 说明 |
|------------|------|
| `evolve_micro(factor, data, forward_returns, n_trials)` | 微观演化主函数 |
| `optimize_params(factor, data, forward_returns, n_trials)` | 参数优化 |

#### 4.2.6 三级评估链 `evaluation_chain.py`

**职责**: agentic 三级评估链（L1 回测 → L2 经济逻辑 → L3 多重检验）。

| 关键函数 | 说明 |
|---------|------|
| `_compute_ic(signal, fwd_ret, method)` | 计算 IC 和 ICIR |
| `_compute_sharpe(signal)` | 计算夏普比率 |
| `_compute_max_drawdown(signal)` | 计算最大回撤 |
| `_evaluate_level_1(...)` | L1 回测验证 |
| `_evaluate_level_2(...)` | L2 经济逻辑评分 |
| `_evaluate_level_3(...)` | L3 多重检验校正 |
| `EvaluationChain` | 三级评估链编排器 |

#### 4.2.7 Verifier `verifier.py`

**职责**: 锁定的评估机制 — 初始化后不可修改。

| 关键类 | 说明 |
|-------|------|
| `FactorVerifier` | 锁定 Verifier |
| `VerifierAlreadyLockedError` | 锁定后修改配置抛出 |
| `VerifierNotLockedError` | 未锁定时调用 check() 抛出 |
| `get_global_verifier()` | 获取全局 Verifier 单例 |

**核心原则**: Verifier 一旦锁定，任何 LLM 调用、参数演化、人类干预都不可修改判定逻辑。

#### 4.2.8 L2 Evolution Loop `evolution_loop.py`

**职责**: 夜间因子演化主循环。

| 关键类 | 说明 |
|-------|------|
| `EvolutionLoop` | L2 演化循环主类 |
| `EvolutionRunResult` | 演化运行结果 |

**`EvolutionLoop.run()` 执行流程**:
1. 加载/初始化演化状态
2. 熔断预检查
3. **循环**（每代）:
   a. `_select_parent_uct()` — UCT 树搜索选择父因子
   b. 宏观演化 — LLM 修改因子逻辑
   c. 微观演化 — optuna 参数调优
   d. 三级评估链 — L1 回测 / L2 经济逻辑 / L3 多重检验
   e. Verifier 判定
   f. 质量评分卡 (A/B/C 分级)
   g. 经验链记录
   h. 分级准入（A/B 级晋升精英，C 级淘汰）
   i. 状态持久化
4. 生成精英因子质量报告

#### 4.2.9 L1 Meta Loop `meta_loop.py`

**职责**: 每日知识补给 + Bootstrapping + debate 分析。

| 关键类 | 说明 |
|-------|------|
| `MetaLoop` | L1 元循环主类 |
| `MetaRunResult` | 元循环运行结果 |
| `L1Verifier` | L1 宽松 Verifier |
| `MetaStateManager` | L1 状态管理 |
| `FactorPoolManager` | 因子池管理 |
| `DebateQualityAnalyzer` | 辩论质量分析器 |
| `BootstrappingChain` | Bootstrapping 链 |

**`MetaLoop.run()` 执行流程**:
1. `_perceive_market()` — agentic 市场感知
2. `_analyze_debate()` — debate 分析（识别薄弱维度）
3. `_run_bootstrap()` — Bootstrapping 生成候选因子
4. `_verify_and_inject()` — L1 Verifier + 注入种子池

#### 4.2.10 L3 Portfolio Loop `portfolio_loop.py`

**职责**: 组合构建 + 信号产出。

| 关键类/函数 | 说明 |
|------------|------|
| `PortfolioLoop` | L3 组合循环主类 |
| `PortfolioRunResult` | 组合运行结果 |
| `synthesize_signals(factors, mode)` | 信号合成（equal_weight / sharpe_weight / elastic_net） |
| `orthogonalize_factors(signals, data)` | 因子正交化 |
| `decay_test(signals, lookback)` | 衰减检验 |
| `build_combo(signals)` | 组合构建 |
| `generate_agent_proposals(combo, trace_id)` | 生成 Agent 优化建议 |
| `load_elite_factors(elite_dir, market)` | 加载精英因子（含去重） |
| `inject_to_fdt(combo, proposals, memory_dir)` | 注入 FDT |

**`PortfolioLoop.run()` 执行流程**:
1. 加载精英因子（DuckDB 或 JSON 回退）
2. 信号合成
3. Regime 自适应权重调整（可选）
4. 因子正交化（非 elastic_net 模式）
5. 衰减检验
6. 组合构建
7. Verifier 判定
8. 注入 FDT / 生成信号

#### 4.2.11 因子质量评分卡 `factor_quality_card.py`

**职责**: 10 维因子质量评分。

| 关键类/函数 | 说明 |
|------------|------|
| `FactorQualityCard` | 评分卡计算器 |
| `FactorQualityCardConfig` | 评分卡配置 |
| `FactorQualityScore` | 评分结果（10 维 + 总分 + 等级） |
| `DimensionScore` | 单维度评分 |
| `compute_total_score(scores, weights)` | 计算加权总分 |
| `determine_grade(total_score, thresholds)` | 确定等级 |

**10 维度评分**:
| 维度 | 权重 | 说明 |
|------|------|------|
| IC 得分 | 1.0 | IC/ICIR 指标 |
| Sharpe 得分 | 1.0 | Sharpe/Calmar 比率 |
| 稳定性 | 0.8 | WalkForward 结果 |
| 鲁棒性 | 0.8 | 衰减率 |
| 容量 | 0.6 | 容量估算 |
| 交易性 | 0.6 | 换手率 |
| 多样性 | 0.5 | 最大相关性 |
| 逻辑性 | 0.5 | 经济逻辑分 |
| 实时性 | 0.4 | 数据频率 |
| 兼容性 | 0.4 | 跨品种覆盖率 |

**分级阈值**: A ≥ 35, B ≥ 25, C < 25

#### 4.2.12 因子审计 `audit.py`

**职责**: 6 项强制审计。

| 关键类 | 说明 |
|-------|------|
| `FactorAuditor` | 审计执行器 |
| `FactorAuditConfig` | 审计配置 |
| `FactorAuditReport` | 审计报告 |
| `AuditItemResult` | 单项审计结果 |

**6 项审计**:
1. 因果检验（Granger / 反事实分析）
2. 样本外验证（WalkForward OOS）
3. 跨品种验证（≥80% 品种 IC 为正）
4. 压力测试（极端行情下表现）
5. 多重检验（Bonferroni / FDR 校正）
6. 数据窥探检验（无未来函数）

#### 4.2.13 因子巡检 `factor_inspector.py`

**职责**: 定时巡检精英因子，自动降级退化因子。

| 关键类 | 说明 |
|-------|------|
| `FactorInspector` | 巡检执行器 |
| `DowngradeRecord` | 降级记录 |

**方法**:
- `inspect_and_downgrade(threshold, commit)` — 巡检 + 降级
- `reactivate_factor(factor_id)` — 重新激活已降级因子

#### 4.2.14 因子数据库 `factor_db/`

**职责**: DuckDB 持久化层。

**表结构**:

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `factor_catalog` | 因子主表 | factor_id, name, code, sharpe, ic, market, family, status, is_elite |
| `factor_evaluations` | 评估历史 | eval_id, factor_id, level_1_*, level_2_*, walk_forward |
| `factor_versions` | 代码版本历史 | version_id, factor_id, code, code_hash, version |
| `factor_correlations` | 相关性矩阵 | factor_id_a, factor_id_b, pearson, spearman |

**关键类**:

| 类 | 方法 | 说明 |
|----|------|------|
| `FactorRepository` | `create_factor()` | 创建因子 |
| | `get_factor(factor_id)` | 查询因子 |
| | `update_factor(factor_id, updates)` | 更新因子 |
| | `list_factors(market, min_ic, min_sharpe)` | 列表查询 |
| | `get_eligible(...)` | 合格因子查询 |
| | `get_diverse_factors(...)` | 多样性选择 |
| | `get_family_distribution(...)` | 家族分布统计 |
| `FactorLineage` | `get_lineage(factor_id)` | 血缘查询 |
| | `get_evaluation_trend(factor_id)` | 评估趋势分析 |
| | `detect_quality_degradation(factor_id)` | 质量退化检测 |
| | `batch_audit(...)` | 批量审计 |

#### 4.2.15 其他分析模块

| 模块 | 类/函数 | 说明 |
|------|---------|------|
| `ablation.py` | `AblationExperiment` | 5 种消融模式（volume_zero / vwap_replace / time_shuffle / noise_inject / feature_permute） |
| `shap_analyzer.py` | `ShapAnalyzer` | SHAP 局部可解释性分析 |
| `robustness.py` | `RobustnessTester` | 对抗样本/缺失值/分布外测试 |
| `causal_validator.py` | `CausalValidator` | 因果结构验证（6 个预定义事件） |
| `walk_forward.py` | `WalkForwardOptimizer` | 走航验证 |
| `stress_test.py` | `StressTester` | 压力测试 |
| `regime.py` | `MarketRegimeDetector` | 5 种市场状态检测 |
| `standardizer.py` | `Standardizer` | 6 种标准化方法（z-score / min-max / rank / etc.） |
| `experience_chain.py` | `ExperienceChain` | 经验链持久化 |

---

### 4.3 配置系统 `fts.config`

**文件**: `fts/config/`

**职责**: 全局配置管理（YAML → 环境变量 → 默认值）。

#### 关键类

| 类 | 说明 |
|----|------|
| `FTSConfig` | 全局配置数据类（内存/进化/数据/LLM 路径等） |
| `DimensionWeights` | 10 维评分卡权重配置 |
| `GradeThresholds` | 分级阈值配置 |
| `FactorQualityCardFullConfig` | 完整评分卡配置 |

**配置优先级**: 环境变量 (`FTS_*`) > YAML 配置文件 > Python 默认值

**`FTSConfig` 关键字段**:
- `memory_dir`: 内存目录（默认 `memory`）
- `elite_dir`: 股票精英因子目录
- `futures_elite_dir`: 期货精英因子目录
- `default_market`: 默认市场（默认 `futures`）
- `llm_backend`: LLM 后端
- `max_generations`: 最大演化代数
- `portfolio_max_factors`: 组合最大因子数

---

### 4.4 管线层 `fts.pipeline`

**文件**: `fts/pipeline/`

**职责**: 因子推演管线（数据处理管线）。

| 类/函数 | 说明 |
|---------|------|
| `DataPayload` | 数据载荷（标准传输对象） |
| `ProcessingStage` | 管线阶段协议 |
| `FactorPipeline` | 管线抽象基类 |
| `PipelineResult` | 管线运行结果 |
| `FactorCombiner` | 多因子加权/融合器 |
| `CombinerConfig` | 组合器配置 |
| `WeightedFactor` | 加权因子条目 |
| `FactorQualityInspection` | 因子质检过滤层 |
| `InspectionResult` | 质检结果 |

---

### 4.5 策略层 `fts.strategies`

**文件**: `fts/strategies/`

**职责**: 策略层，包含 v2 可插拔策略框架和多因子策略。

| 类/函数 | 说明 |
|---------|------|
| `BaseStrategyV2` | v2 策略抽象基类（compute → filter → score 三段式） |
| `RawSignal` | 原始信号 |
| `ScoredSignal` | 打分信号（含 direction, grade, weight 等） |
| `StrategyV1Adapter` | v1 兼容适配器 |
| `MultiFactorStrategy` | 四维因子加权打分策略 |
| `RegimeAdaptiveStrategy` | 制度自适应策略 |
| `DynamicWeightStrategy` | 动态权重策略 |
| `MultiPeriodSignalFusion` | 多周期信号融合 |

---

### 4.6 数据层 `fts.data`

**文件**: `fts/data.py`, `fts/data_futures.py`, `fts/data_fundamental.py`, `fts/data_mcp.py`

**职责**: 统一数据访问层。

#### `FTSDataProvider` (data.py)

统一数据提供者，组合多个子提供者。

| 方法 | 说明 |
|------|------|
| `get_ohlcv(symbol, days)` | 获取 A 股 OHLCV |
| `get_etf_ohlcv(symbol, days)` | 获取 ETF OHLCV |
| `get_csi300_panel(days, max_stocks)` | 获取沪深 300 面板数据 |
| `get_futures_panel(symbols, days)` | 获取期货面板数据 |
| `enrich_with_fundamental(df, symbol)` | 基本面字段注入 |

#### `FuturesDataProvider` (data_futures.py)

| 方法 | 说明 |
|------|------|
| `get_ohlcv(symbol, days)` | 获取期货连续合约 OHLCV |
| `get_panel(symbols, days)` | 获取期货面板数据 |

**数据源优先级**: DuckDB kline_cache → AKShare → 合成数据

#### `MCPDataProvider` (data_mcp.py)

| 方法 | 说明 |
|------|------|
| `get_ohlcv(code, days)` | 腾讯 HTTP API 获取 OHLCV |

---

### 4.7 数据源适配器 `fts.data_sources`

**文件**: `fts/data_sources/`

**职责**: 期货多源数据适配器（v2.3.0 多源集成）。

#### 关键类

| 类 | 说明 |
|----|------|
| `BaseFuturesSource` | 抽象基类（3 个抽象方法） |
| `FuturesDataAggregator` | 多源调度器（5 级降级 + 熔断器 + 交叉验证） |
| `OHLCVFusion` | 多源融合器（5 种策略） |
| `SourceUnavailable` | 数据源不可用异常 |
| `TQLocalSource` | 通达信本地数据源 |
| `WindSource` | Wind 数据源 |
| `IFindSource` | iFinD 数据源 |

**K 线主路径（5 级降级）**:
```
DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
```

**字段增强层（并行）**:
```
WIND  → settle / oi_change / 期权 IV/PCR
IFIND → EDB 宏观/产业链 / 期货全字段
```

**熔断器**: 任一源连续 5 次失败 → UNAVAILABLE → 6 小时冷却 → 探活恢复

**融合策略**: `MEDIAN` / `MEAN` / `WEIGHTED` / `HIERARCHICAL` / `TRIMMED_MEAN`

---

### 4.8 调度层 `fts.scheduler`

**文件**: `fts/scheduler/`

**职责**: 定时任务注册 + APScheduler 调度。

| 类/函数 | 说明 |
|---------|------|
| `TaskSpec` | 定时任务规格（name, cron_expression, callable_path） |
| `TaskRegistry` | 任务注册表 |
| `SchedulerEngine` | 调度器引擎（APScheduler 包装） |
| `ProcessWatchdog` | 进程级看门狗 |
| `HotSwapWatcher` | 热重载支持 |

**默认任务**:

| 任务名 | cron 表达式 | 说明 |
|--------|-------------|------|
| `l1_meta_loop` | `30 8 * * *` (08:30) | L1 知识补给 + 种子注入 |
| `l2_evolution_loop` | `0 23 * * *` (23:00) | L2 夜间因子演化 |
| `l3_portfolio_loop` | `0 20 * * *` (20:00) | L3 组合构建 + 信号 |
| `health_check` | `*/10 * * * *` (每 10 分钟) | 健康检查 |

---

### 4.9 监控层 `fts.monitor`

**文件**: `fts/monitor/`

**职责**: 系统健康监控 + 精英因子跟踪。

| 类/函数 | 说明 |
|---------|------|
| `check_loop_status(loop_name)` | 检查单个循环状态 |
| `check_all_status()` | 检查所有循环状态 |
| `format_status_report(report)` | 格式化状态报告 |
| `status_report_to_json(report)` | JSON 格式状态报告 |
| `LoopStatusReport` | 循环状态报告 |
| `SystemStatusReport` | 系统级状态报告 |
| `FTSDashboardServer` | Web UI 仪表盘（默认端口 9100） |
| `EliteFactorTracker` | 精英因子样本外跟踪 |
| `AutoRetireManager` | 自动淘汰管理 |
| `LogicMonitor` | 因子行为逻辑监控 |

---

### 4.10 LLM 客户端 `fts.llm`

**文件**: `fts/llm.py`

**职责**: 统一的 LLM 调用接口。

| 类/函数 | 说明 |
|---------|------|
| `LLMClient` | 抽象基类 |
| `LLMError` | LLM 调用失败异常 |
| `LLMCallRecord` | 单次调用记录 |

**支持后端**: OpenAI / Anthropic，通过环境变量 `.env` 配置。

---

### 4.11 CLI 入口 `fts.cli`

**文件**: `fts/cli.py`

**职责**: 统一命令行入口。

**子命令列表**:

| 子命令 | 子子命令 | 说明 |
|--------|---------|------|
| `fts version` | — | 打印版本号 |
| `fts monitor` | — | 检查所有循环健康状态 |
| `fts evolution` | `run` | 启动 L2 因子演化 |
| `fts meta-loop` | `run` | 启动 L1 Meta-Loop |
| `fts portfolio` | `run` | 启动 L3 组合构建 |
| `fts ui` | — | 启动 Web UI 仪表盘 |
| `fts scheduler` | `run` / `list` | 调度器管理 |
| `fts factor` | `list` / `show` / `seeds` / `stats` / `lineage` | 因子管理 |

**演化模式参数**:
- `--universe`: `futures`（默认）/ `csi300` / `single`
- `--max-generations`: 最大演化代数
- `--max-stocks`: 横截面最大标的数

---

## 5. 关键类/函数速查表

### 5.1 核心引擎类

| 类 | 所在模块 | 职责 |
|----|---------|------|
| `EvolutionLoop` | `factor_engine.evolution_loop` | L2 因子演化主循环 |
| `MetaLoop` | `factor_engine.meta_loop` | L1 元循环 |
| `PortfolioLoop` | `factor_engine.portfolio_loop` | L3 组合循环 |
| `FactorVerifier` | `factor_engine.verifier` | 锁定 Verifier |
| `EvaluationChain` | `factor_engine.evaluation_chain` | 三级评估链 |
| `SeedPool` | `factor_engine.seed_pool` | 种子池管理 |
| `MacroEvolver` | `factor_engine.macro_evolution` | 宏观演化 |
| `FactorExecutor` | `factor_engine.factor_program` | 因子执行器 |
| `FactorQualityCard` | `factor_engine.factor_quality_card` | 质量评分卡 |
| `FactorAuditor` | `factor_engine.audit` | 因子审计 |
| `FactorInspector` | `factor_engine.factor_inspector` | 因子巡检 |
| `FactorRepository` | `factor_engine.factor_db.repository` | 因子数据库 |
| `FactorLineage` | `factor_engine.factor_db.lineage` | 因子血缘 |
| `FTSDataProvider` | `data` | 统一数据提供者 |
| `FuturesDataProvider` | `data_futures` | 期货数据提供者 |
| `FuturesDataAggregator` | `data_sources.aggregator` | 多源数据调度器 |
| `OHLCVFusion` | `data_sources.fusion` | 多源融合器 |
| `FTSDashboardServer` | `monitor.http_server` | Web UI 仪表盘 |
| `SchedulerEngine` | `scheduler.engine` | 调度器引擎 |
| `ProcessWatchdog` | `scheduler.watchdog` | 进程看门狗 |

### 5.2 关键函数

| 函数 | 所在模块 | 说明 |
|------|---------|------|
| `generate_trace_id()` | `factor_engine.state` | 生成全链路 trace_id |
| `generate_run_id()` | `factor_engine.state` | 生成运行 ID |
| `create_factor_program(...)` | `factor_engine.factor_program` | 创建因子程序 |
| `validate_factor_code(code)` | `factor_engine.factor_program` | 安全沙箱验证 |
| `evolve_micro(...)` | `factor_engine.micro_evolution` | 微观参数优化 |
| `synthesize_signals(factors, mode)` | `factor_engine.portfolio_loop` | 信号合成 |
| `orthogonalize_factors(signals, data)` | `factor_engine.portfolio_loop` | 因子正交化 |
| `decay_test(signals, lookback)` | `factor_engine.portfolio_loop` | 衰减检验 |
| `build_combo(signals)` | `factor_engine.portfolio_loop` | 组合构建 |
| `load_elite_factors(...)` | `factor_engine.portfolio_loop` | 加载精英因子 |
| `atomic_write(path, data)` | `core.atomic` | 原子写入 |
| `atomic_read(path, default)` | `core.atomic` | 安全读取 |

---

## 6. 依赖关系图

### 6.1 模块依赖关系

```
cli.py ──────────────────────────────────────────────────┐
  ├── config  ──→ settings.py                            │
  ├── data ──→ data_futures.py ──→ data_mcp.py           │
  ├── factor_engine ─────────────────────────────────────┤
  │   ├── evolution_loop.py ──→ macro_evolution.py       │
  │   │                        ├── micro_evolution.py    │
  │   │                        ├── evaluation_chain.py   │
  │   │                        ├── verifier.py           │
  │   │                        └── seed_pool.py          │
  │   ├── meta_loop.py ──→ factor_engine.contracts       │
  │   ├── portfolio_loop.py ──→ factor_db.repository     │
  │   ├── factor_db/ ──→ DuckDB (/data/factor_catalog)   │
  │   ├── audit.py ──→ causal_validator / stress_test    │
  │   └── contracts.py ──→ (所有模块依赖的核心契约)       │
  ├── monitor ──→ factor_engine.monitor                  │
  ├── scheduler ──→ APScheduler                          │
  └── llm ──→ OpenAI / Anthropic SDK                     │

data_sources/ ────────────────────────────────────────────
  ├── base.py ──→ (抽象基类)
  ├── aggregator.py ──→ tq_source/wind_source/ifind_source
  ├── fusion.py ──→ core.contracts / core.enums
  └── tq_source.py ──→ 通达信 HTTP API

pipeline/ ────────────────────────────────────────────────
  ├── base.py ──→ (抽象基类 + 协议)
  ├── factor_combiner.py ──→ numpy/pandas
  └── factor_quality_inspection.py ──→ factor_engine

strategies/ ──────────────────────────────────────────────
  ├── base_v2.py ──→ (抽象基类)
  ├── multi_factor_strategy.py ──→ base_v2
  └── strategy_evolution.py ──→ base_v2
```

### 6.2 外部依赖

| 依赖 | 用途 | 必选/可选 |
|------|------|-----------|
| numpy | 数值计算 | 必选 |
| pandas | 数据处理 | 必选 |
| scipy | 统计分析 | 必选 |
| pyyaml | YAML 配置 | 必选 |
| shap | SHAP 分析 | 必选 |
| optuna | 贝叶斯调参 | 可选 (evolution) |
| openai | LLM 调用 | 可选 (llm) |
| anthropic | LLM 调用 | 可选 (llm) |
| akshare | 金融数据 | 可选 (mcp) |
| scikit-learn | 组合构建 | 可选 (portfolio) |
| APScheduler | 定时调度 | 运行时 |
| duckdb | 数据存储 | 运行时 |
| httpx | HTTP 客户端 | 运行时 |

---

## 7. 项目运行方式

### 7.1 安装

```bash
# 基础安装
pip install -e .

# 全部可选依赖（推荐）
pip install -e ".[evolution,llm,mcp,portfolio,dev]"
```

### 7.2 环境配置

创建 `.env` 文件（位于项目根目录）：

```env
# LLM 配置（必需）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

# FTS 配置（可选，有默认值）
FTS_MEMORY_DIR=memory
FTS_ELITE_DIR=memory/knowledge/factors/elite
FTS_FUTURES_ELITE_DIR=memory/knowledge/factors/futures_elite
FTS_DEFAULT_MARKET=futures
FTS_MAX_WORKERS=4
```

### 7.3 运行命令

```bash
# 查看版本
fts version

# 查看监控状态
fts monitor

# 启动 L1 Meta-Loop（市场感知 + 种子注入）
fts meta-loop run

# 启动 L2 因子演化（期货横截面，默认）
fts evolution run --max-generations 10

# L2 演化（沪深300 横截面）
fts evolution run --universe csi300 --max-stocks 20

# L2 演化（单标模式）
fts evolution run --universe single --symbol 000001

# 启动 L3 组合构建（期货）
fts portfolio run --universe futures --synthesis-mode sharpe_weight

# L3 组合构建（股票）
fts portfolio run --universe stock --synthesis-mode elastic_net

# 查看精英因子
fts factor list --market futures
fts factor list --market stock

# 查看种子因子
fts factor seeds --market futures

# 因子统计
fts factor stats --market futures

# 查看单个因子详情
fts factor show <factor_id>

# 查询因子演化血缘
fts factor lineage <factor_id>

# 启动调度器（后台运行所有定时任务）
fts scheduler run

# 查看调度器任务列表
fts scheduler list

# 启动 Web UI 仪表盘
fts ui --port 9100

# 数据管理
fts data status                          # 查看数据源状态
fts data sync-futures --symbol RB --days 500  # 同步期货数据
fts data cross-check --symbol RB --date 2026-08-05  # 交叉验证
fts data fuse --strategy MEDIAN          # 多源数据融合
```

### 7.4 运行测试

```bash
# 运行所有测试
python -m pytest tests/ --no-cov --tb=short

# 运行特定模块测试
python -m pytest tests/factor_engine/ -v

# 运行带覆盖率
python -m pytest tests/ --cov=fts --cov-report=term-missing -v
```

---

## 8. 配置系统

### 8.1 配置加载优先级

```
环境变量 (FTS_*)  >  YAML 配置文件 (config/settings.yaml)  >  Python 默认值
```

### 8.2 核心配置项

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|---------|--------|------|
| `memory_dir` | `FTS_MEMORY_DIR` | `memory` | 运行时持久化目录 |
| `elite_dir` | `FTS_ELITE_DIR` | `memory/knowledge/factors/elite` | 股票精英因子目录 |
| `futures_elite_dir` | `FTS_FUTURES_ELITE_DIR` | `memory/knowledge/factors/futures_elite` | 期货精英因子目录 |
| `default_market` | `FTS_DEFAULT_MARKET` | `futures` | 默认市场 |
| `llm_backend` | `FTS_LLM_BACKEND` | `""` | LLM 后端类型 |
| `max_generations` | — | `10` | 最大演化代数 |
| `max_workers` | `FTS_MAX_WORKERS` | `4` | 并行工作线程数 |

### 8.3 Verifier 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `min_ic` | 0.03 | 最小 IC |
| `min_sharpe` | 1.5 | 最小夏普 |
| `max_drawdown` | 0.50 | 最大回撤 |
| `min_economic_score` | 3 | 最小经济逻辑维度达标数 |
| `min_t_stat` | 3.0 | 最小 t 统计量 |
| `max_fdr` | 0.05 | 最大 FDR |
| `max_turnover_monthly` | 5.0 | 最大月度换手率 |

### 8.4 预算配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `nightly_token_limit` | 200,000 | 单夜 token 上限 |
| `monthly_token_limit` | 6,000,000 | 月度 token 上限 |
| `max_generation` | 50 | 最大演化代数 |
| `circuit_breaker_consecutive_low_ic` | 5 | 连续低 IC 熔断次数 |
| `circuit_breaker_low_ic_threshold` | 0.005 | 低 IC 阈值 |
| `circuit_breaker_failure_rate` | 0.95 | 失败率熔断 |

---

## 9. 数据流与执行流程

### 9.1 因子演化完整流程

```
数据准备 → 种子池加载 → UCT 父因子选择 → 宏观演化（LLM 改逻辑）
  → 微观演化（optuna 调参） → 三级评估链 → Verifier 判定
  → 质量评分卡（A/B/C 分级） → 精英因子晋升 → 经验链记录
  → 下一轮演化（循环至熔断/完成）
```

### 9.2 数据流路径

```
期货数据:
  DuckDB (kline_cache) → TQ_LOCAL (通达信) → AKShare → 合成数据
  ↓
  FTSDataProvider.get_futures_panel()
  ↓
  EvolutionLoop 或 PortfolioLoop
  ↓
  因子计算 → 评估 → 信号合成 → 交易信号输出
```

### 9.3 精英因子去重逻辑

1. 按基础名分组（移除 `_gXX` 后缀）
2. 计算组内信号相关性矩阵
3. 贪婪选择：按 IC 绝对值降序，保留与已选因子相关性 < 0.8 的因子
4. 回退策略：若相关性计算失败，使用 IC-only 模式（保留最高 IC 版本）

### 9.4 熔断机制

**触发条件**:
- Token 消耗超过预算上限（`circuit_breaker_token_ratio`）
- 连续 N 代低 IC（`circuit_breaker_consecutive_low_ic`）
- 失败率超过阈值（`circuit_breaker_failure_rate`）

**恢复**: 熔断后必须人类介入恢复（修改配置或充值 API Key）

---

## 10. 测试体系

### 10.1 测试概况

- 测试文件: 63 个
- 测试用例: 1700+（全部通过）
- 代码覆盖率: 99%

### 10.2 测试目录结构

```
tests/
├── factor_engine/        # 24 个文件，含消融/因果/鲁棒性/SHAP
├── scenarios/             # 23 个宏观行为场景
├── monitor/               # 监控测试
├── pipeline/              # 管线测试（2 文件）
├── scheduler/             # 调度测试（4 文件）
├── strategies/            # 策略测试（3 文件）
├── core/                  # 核心契约测试（3 文件）
├── test_cli.py            # CLI 集成测试
├── test_data_futures_panel.py  # 期货面板测试
└── test_futures_signal_pipeline.py  # 信号管道测试
```

---

> 本文档遵循 HARNESS §契约优先原则，所有模块接口定义以 `factor_engine/contracts.py` 为准。
> 版本变更必须同步更新 `pyproject.toml`、`fts/__init__.py` 和 `docs/harness/07-operations.md`。