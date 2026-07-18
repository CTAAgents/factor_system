# FTS — Factor Trading System Code Wiki

> **版本**: v2.2 (规划阶段)
> **最后更新**: 2026-07-18
> **项目状态**: 设计规划中，代码待实现
> **文档位置**: [CODE_WIKI.md](file:///d:/Programs/factor_system/CODE_WIKI.md)

---

## 目录

1. [项目概述](#1-项目概述)
2. [三项目生态关系](#2-三项目生态关系)
3. [整体架构](#3-整体架构)
4. [核心模块职责](#4-核心模块职责)
5. [关键类与接口](#5-关键类与接口)
6. [数据类型体系（Data-Core 提供）](#6-数据类型体系data-core-提供)
7. [市场特征画像与差异分析](#7-市场特征画像与差异分析)
8. [因子体系](#8-因子体系)
9. [依赖关系](#9-依赖关系)
10. [项目运行方式](#10-项目运行方式)
11. [实施路线图](#11-实施路线图)
12. [设计原则](#12-设计原则)

---

## 1. 项目概述

### 1.1 项目定位

**FTS (Factor Trading System)** 是从 FDT (Futures Day Trading) 系统中剥离出来的独立因子策略系统，专注于多因子挖掘、演化与交易，支持国内期货、A股股票、ETF、可转债、REITs 等全市场品种。

核心目标是构建一个**因子自演化**的量化投研系统。FTS **不包含独立的数据层**，数据基础设施由外部 **[Data-Core](file:///d:/Programs/data-core/README.md)** 项目提供（通过 `pip install datacore` 接入）。

**剥离范围**：FTS 仅从 FDT 剥离了**因子演化引擎**（`loop_engine/`）和**多因子策略**（`multi_factor_strategy`）。FDT 保留了完整的数据采集、指标计算和基本面分析能力，三项目互相独立。

### 1.2 核心特性

| 特性 | 说明 |
|:-----|:-----|
| **数据层外置** | 数据基础设施由独立的 Data-Core 项目提供，FTS 聚焦因子引擎与策略 |
| **三项目独立** | FDT、FTS、Data-Core 互相独立，FTS 通过 Data-Core 获取数据，FDT 自有数据层 |
| **三层进化循环** | L1元循环（市场感知）→ L2因子演化（LLM+贝叶斯）→ L3组合构建 |
| **程序级因子表示** | 因子为图灵完备的Python代码，突破符号表达式限制 |
| **知识注入因子挖掘** | 从金融研报中提取因子知识，冷启动因子池 |
| **情绪数据管线** | 新闻→分类→LLM情绪打分→聚合，支持规则基线零成本运行 |
| **多市场支持** | 期货/A股/ETF/可转债/REITs，通过 Data-Core 统一数据接口接入 |

### 1.3 技术栈

| 层级 | 技术选型 |
|:-----|:---------|
| **编程语言** | Python 3.10+ |
| **数据层依赖** | [Data-Core](file:///d:/Programs/data-core/) (独立项目，pip 包 `datacore`) |
| **数据处理** | pandas, numpy |
| **因子进化** | LLM (宏观变异) + Optuna/贝叶斯优化 (微观调参) |
| **包管理** | pyproject.toml (Poetry/pip) |
| **配置管理** | YAML + Python settings |

### 1.4 剥离范围与三项目关系

**从 FDT 剥离到 FTS 的内容**：

| 剥离模块 | 原FDT路径 | 代码量 | 说明 |
|:---------|:----------|:-------|:-----|
| 因子演化引擎 | `loop_engine/` | 5,089 行 / 15 文件 | L1/L2/L3 三层循环 + 种子池 + 验证器 |
| 因子引擎测试 | `tests/loop_engine/` | 2,840 行 / 11 文件 | 完整的因子引擎测试套件 |
| 多因子策略测试 | `tests/strategies/multi_factor*` | 330 行 / 2 文件 | 多因子策略测试 |
| **合计** | — | **8,259 行 / 28 文件** | **占 FDT 总量 8.2%** |

**FDT 保留的内容**（不迁移到 FTS 或 Data-Core）：

| 保留模块 | 原FDT路径 | 说明 |
|:---------|:----------|:-----|
| 数据采集 | `futures_data_core/collectors/` | TDX/TqSDK/QMT/Web 多源采集 |
| 指标计算 | `futures_data_core/indicators/` | 45+ numpy 向量化指标 |
| 数据核心 | `futures_data_core/core/` | 降级链、缓存、符号注册 |
| 基本面分析 | `futures_data_core/f10/` | 仓单/基差/宏观/持仓/情绪 |
| 9-Agent 辩论 | `fdt_langgraph/` | LangGraph 辩论核心 |
| 8 策略管线 | `pipeline/` + `skills/` | 除 multi_factor 外的7条策略 |

---

## 2. 三项目生态关系

### 2.1 三项目独立性

```
┌─────────────────────────────────────────────────────────────────┐
│                         FDT（独立）                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ 9-Agent 辩论  │  │ 8策略管线     │  │ futures_data_core/   │  │
│  │ (LangGraph)  │  │ (除multi_    │  │ ├─ collectors/ 数据采集│  │
│  │              │  │   factor)    │  │ ├─ indicators/ 指标计算│  │
│  └──────────────┘  └──────────────┘  │ ├─ core/ 数据核心     │  │
│                                      │ └─ f10/ 基本面分析    │  │
│                                      └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                         FTS（独立）                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ 因子引擎      │  │ 多因子策略    │  │ 数据处理管线          │  │
│  │ (factor_     │  │ (multi_      │  │ (pipeline/)          │  │
│  │  engine/)    │  │  factor)     │  │                      │  │
│  └──────┬───────┘  └──────────────┘  └──────────────────────┘  │
│         │ from datacore import ...                               │
└─────────┼───────────────────────────────────────────────────────┘
          │ pip install datacore
          ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Data-Core（独立）                           │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │       UnifiedDataProvider (统一数据入口)                   │   │
│  ├──────────┬───────────┬───────────┬──────────────────────┤   │
│  │ Futures  │  Equity   │   Store   │  SymbolRegistry      │   │
│  │ Provider │ Provider  │  存储层    │  符号注册表           │   │
│  ├──────────┴───────────┴───────────┴──────────────────────┤   │
│  │  TDX-LC / 东方财富 / 腾讯 / 国信证券                      │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 三项目职责边界

| 职责 | FDT | FTS | Data-Core |
|:-----|:----|:----|:----------|
| 数据采集（期货OHLCV/行情） | ✅ 自有 `futures_data_core/` | ❌ | ✅ 自有采集器 |
| 数据采集（A股/全市场OHLCV） | ❌ | ❌ | ✅ 自有采集器 |
| 数据采集（新闻/公告/研报文本） | ❌ | ❌ | ✅ 自有采集器 |
| 数据加工（新闻分类/结构化抽取） | ❌ | ❌ | ✅ 数据加工层（含LLM） |
| 数据加工（LLM情绪打分/规则基线） | ❌ | ❌ | ✅ 数据加工层（含LLM） |
| 数据加工（情绪聚合器/market_regime） | ❌ | ❌ | ✅ 数据加工层（含LLM） |
| 数据存储（DuckDB/Redis/内存） | ✅ 自有 | ❌ | ✅ 自有 |
| 指标计算（45+ numpy指标） | ✅ 自有 | ❌ | ❌ |
| 基本面分析（仓单/基差/宏观） | ✅ 自有 `f10/` | ❌ | ❌ |
| 符号注册表 | ✅ 自有 | ❌ 使用 Data-Core | ✅ |
| 数据类型枚举（DataType/MarketType） | ✅ 自有定义 | ❌ 使用 Data-Core | ✅ 定义并导出 |
| 因子推演（挖掘/演化/评估） | ❌ 已剥离 | ✅ 核心能力 | ❌ |
| 多因子策略组建 | ❌ 已剥离 | ✅ 核心能力 | ❌ |
| 交易信号产出 | ❌ | ✅ 核心能力 | ❌ |
| 9-Agent 辩论 | ✅ 核心能力 | ❌ | ❌ |
| CTP 信号输出 | ✅ | ❌ | ❌ |

> **边界原则**: Data-Core 负责"数据采集 + 数据加工"（产出 NEWS/ANNOUNCEMENT/SENTIMENT/MARKET_STATE 等）；
> FTS 负责"数据消费 + 因子推演 + 策略组建 + 交易信号产出"；
> **LLM 是三个项目的基本能力，不作为边界划分标准**。

### 2.3 三项目数据来源对比

| 项目 | 数据来源 | 依赖关系 |
|:-----|:---------|:---------|
| **FDT** | 自有 `futures_data_core/`，TDX-LC / 东方财富 / TqSDK / WebFallback | 无外部依赖 |
| **FTS** | `pip install datacore`，通过 Data-Core 的 `UnifiedDataProvider` 获取 | 依赖 Data-Core |
| **Data-Core** | 自有采集器，TDX-LC / 东方财富 / 腾讯 / 国信证券 | 无外部依赖 |

---

## 3. 整体架构

### 3.1 系统分层架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                        应用层 (Application)                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                          │
│  │   CLI    │  │  Monitor │  │ Scheduler│                          │
│  └──────────┘  └──────────┘  └──────────┘                          │
├─────────────────────────────────────────────────────────────────────┤
│                        策略层 (Strategies)                           │
│  ┌──────────────────────┐  ┌──────────────────────────────┐        │
│  │  BaseStrategyV2      │  │  MultiFactorStrategy         │        │
│  │  (策略基类)          │  │  (多因子策略)                 │        │
│  └──────────────────────┘  └──────────────────────────────┘        │
├─────────────────────────────────────────────────────────────────────┤
│                      因子引擎层 (Factor Engine)                      │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐ │
│  │ L1 元  │  │ L2 演化 │  │ L3 组合 │  │  种子池  │  │  经验链  │ │
│  │  循环   │  │  循环   │  │  循环   │  │         │  │         │ │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘  └─────────┘ │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐              │
│  │ 安全沙箱 │  │ 验证器  │  │ 评估链  │  │ 指标计算 │              │
│  └─────────┘  └─────────┘  └─────────┘  └─────────┘              │
├─────────────────────────────────────────────────────────────────────┤
│                    数据消费层 (Data Consumption)                      │
│  FTS 直接消费 Data-Core 的所有 DataType（含 SENTIMENT/MARKET_STATE）   │
├─────────────────────────────────────────────────────────────────────┤
│           外部依赖: Data-Core (pip install datacore)                │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              UnifiedDataProvider (统一数据入口)               │  │
│  │  get(symbol, data_type, params) → DataPayload                │  │
│  ├──────────┬───────────┬───────────┬───────────────────────┤  │
│  │ Futures  │  Equity   │   Store   │  SymbolRegistry       │  │
│  │ Provider │ Provider  │  存储层    │  符号注册表            │  │
│  ├──────────┴───────────┴───────────┴───────────────────────┤  │
│  │  数据采集层: TDX-LC/东方财富/腾讯/财联社/交易所公告           │  │
│  │  数据加工层(含LLM): 新闻分类/情绪打分/情绪聚合/market_regime │  │
│  │  存储: DuckDB + Memory + PostgreSQL + Redis                 │  │
│  │  输出: OHLCV, NEWS, SENTIMENT, MARKET_STATE, FUTURES_*...   │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 设计哲学：关注点分离

FTS 严格遵循**关注点分离**原则：

- **FDT 负责"期货交易决策"**：9-Agent 辩论、8 策略管线、数据采集、指标计算、基本面分析
- **FTS 负责"因子智能"**：因子挖掘、因子进化、策略组合、信号生成
- **Data-Core 负责"数据基础设施"**：统一数据接口、多源降级、存储、符号解析

FTS 通过 `from datacore import UnifiedDataProvider` 接入数据层，自身不包含任何数据源实现代码。FDT 和 Data-Core 各自拥有独立的数据采集能力，互不依赖。

### 3.3 三层进化循环架构

```
L1 元循环 (Meta Loop)          ← 市场制度感知，大尺度决策
     │
     ▼
L2 演化循环 (Evolution Loop)   ← 因子代码进化，中尺度搜索
     │
     ▼
L3 组合循环 (Portfolio Loop)   ← 多因子组合构建，小尺度调优
```

| 层级 | 名称 | 时间尺度 | 核心职责 | 技术手段 |
|:----|:-----|:---------|:---------|:---------|
| **L0** | 人类设定 | — | 初始因子/约束/目标 | 人工编写 |
| **L1** | 元循环 | 日/周 | 市场制度识别、搜索空间调整 | LLM 市场分析 |
| **L2** | 演化循环 | 小时/日 | 因子代码变异、参数优化 | LLM宏观变异 + 贝叶斯微观调参 |
| **L3** | 组合循环 | 分钟/日 | 因子权重分配、组合构建 | 优化器/风险模型 |

---

## 4. 核心模块职责

### 4.1 目录结构总览

```
factor_system/
├── fts/                           # 主包
│   ├── __init__.py                # 包入口 + 版本号
│   │
│   ├── core/                      # 核心契约（FTS 自身契约，非数据契约）
│   │   ├── contracts.py           # 因子引擎 TypedDict 契约
│   │   └── enums.py               # FTS 特有枚举（如 EvolutionStage）
│   │   # 注: DataType/MarketType/SourceGrade 由 datacore.models.enums 提供
│   │
│   ├── pipeline/                  # 因子推演管线（FTS 自有，因子计算层）
│   │   ├── base.py                # FactorPipeline 抽象基类
│   │   └── factor_combiner.py     # 因子组合器（多因子加权/融合）
│   │   # 注: 数据加工管线（sentiment_llm/sentiment_rule/market_regime/fundamental_llm）
│   │   #       已迁移到 Data-Core 数据加工层（含 LLM 调用）
│   │
│   ├── indicators/                # 指标计算（纯 numpy，因子计算的基础）
│   │   ├── core.py                # numpy 向量化 45+ 指标
│   │   ├── trend_maturity.py      # 趋势阶段评估
│   │   └── legacy_numpy.py        # 兼容原 _compute_indicators_numpy
│   │
│   ├── strategies/                # 策略层
│   │   ├── base_v2.py             # BaseStrategyV2 基类
│   │   ├── multi_factor_strategy.py  # 多因子策略
│   │   └── rules/                 # 策略规则知识库
│   │
│   ├── factor_engine/             # 因子引擎（核心）
│   │   ├── evolution_loop.py      # L2 主循环
│   │   ├── meta_loop.py           # L1 元循环（通过 Data-Core 感知市场）
│   │   ├── portfolio_loop.py      # L3 组合构建
│   │   ├── macro_evolution.py     # LLM 演化（宏观变异）
│   │   ├── micro_evolution.py     # optuna 调参（微观优化）
│   │   ├── evaluation_chain.py    # 三级评估链
│   │   ├── experience_chain.py    # 经验链
│   │   ├── seed_pool.py           # 种子池（数据类型感知）
│   │   ├── factor_program.py      # 安全沙箱
│   │   ├── verifier.py            # Verifier 验证器
│   │   ├── state.py               # 状态管理
│   │   └── program.py             # L0 人类设定
│   │
│   ├── scheduler/                 # 调度层
│   │   └── tasks.py               # 定时任务注册
│   │
│   ├── cli.py                     # 统一命令行入口
│   └── monitor.py                 # 健康监控
│
├── memory/                        # 运行时持久化（FTS 自身状态）
│   ├── evolution/                 # L2 状态
│   ├── meta_loop/                 # L1 状态
│   ├── portfolio/                 # L3 状态
│   └── knowledge/factors/         # 因子知识库
│       ├── elite/                 # 精英因子
│       ├── factor_pool.json       # 因子池元数据
│       └── l1_injected/           # L1 注入因子
│
├── config/                        # 配置
│   ├── settings.py                # 全局配置
│   └── settings.yaml              # YAML 配置文件
│
├── pyproject.toml                 # 包管理 (依赖 datacore)
├── PLAN.md                        # 实施计划
└── CODE_WIKI.md                   # 本文档
```

### 4.2 核心模块详细职责

#### 4.2.1 core/ — 核心契约层

FTS 自身的核心契约层，仅定义因子引擎相关的契约。数据相关的枚举和类型从 `datacore` 导入。

| 模块 | 职责 | 关键输出 |
|:-----|:-----|:---------|
| [contracts.py](file:///d:/Programs/factor_system/fts/core/contracts.py) | 因子引擎的接口契约 | FactorDef, EvolutionState, EvaluationResult |
| [enums.py](file:///d:/Programs/factor_system/fts/core/enums.py) | FTS 特有枚举 | EvolutionStage, FactorPriority 等 |

> **注意**: `DataType`, `MarketType`, `SourceGrade` 由 [datacore.models.enums](file:///d:/Programs/data-core/datacore/models/enums.py) 提供，FTS 直接导入使用。

#### 4.2.2 pipeline/ — 因子推演管线（FTS 自有，因子计算层）

FTS 的管线层专注于**因子组合与推演**，不涉及数据加工。数据加工（含 LLM 调用）由 Data-Core 完成。

| 模块 | 职责 | 输入→输出 |
|:-----|:-----|:---------|
| [base.py](file:///d:/Programs/factor_system/fts/pipeline/base.py) | FactorPipeline 抽象基类 | 定义因子推演管线接口 |
| [factor_combiner.py](file:///d:/Programs/factor_system/fts/pipeline/factor_combiner.py) | 因子组合器（多因子加权/融合） | 多个因子 → 组合因子 |

> **已迁移到 Data-Core**: `sentiment_llm.py`（LLM 情绪打分）、`sentiment_rule.py`（规则情绪基线）、`sentiment_aggregator.py`（情绪聚合器）、`market_regime.py`（市场制度识别）、`fundamental_llm.py`（LLM 基本面增强）均属于数据加工层，由 Data-Core 实现。
> **LLM 是三个项目的基本能力，不作为边界划分标准**。

#### 4.2.3 indicators/ — 指标计算

纯 numpy 向量化实现的技术指标库，作为因子计算的基础设施。

| 模块 | 职责 | 关键输出 |
|:-----|:-----|:---------|
| [core.py](file:///d:/Programs/factor_system/fts/indicators/core.py) | numpy 向量化 45+ 指标 | MA/EMA/RSI/MACD/Boll/ATR/ADX... |
| [trend_maturity.py](file:///d:/Programs/factor_system/fts/indicators/trend_maturity.py) | 趋势阶段评估 | 趋势成熟度评分 |
| [legacy_numpy.py](file:///d:/Programs/factor_system/fts/indicators/legacy_numpy.py) | 兼容原 _compute_indicators_numpy | 向后兼容 |

#### 4.2.4 factor_engine/ — 因子引擎层（核心）

| 模块 | 职责 | 关键输出 |
|:-----|:-----|:---------|
| [evolution_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_loop.py) | L2 主循环，因子代码进化 | 进化后的因子程序 |
| [meta_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/meta_loop.py) | L1 元循环，市场感知与搜索空间调整 | 市场制度判断 + 搜索策略调整 |
| [portfolio_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py) | L3 组合循环，多因子权重优化 | 因子组合权重 |
| [macro_evolution.py](file:///d:/Programs/factor_system/fts/factor_engine/macro_evolution.py) | LLM 宏观变异（结构/逻辑变更） | 新因子代码 |
| [micro_evolution.py](file:///d:/Programs/factor_system/fts/factor_engine/micro_evolution.py) | 贝叶斯微观调参（数值优化） | 最优参数组合 |
| [evaluation_chain.py](file:///d:/Programs/factor_system/fts/factor_engine/evaluation_chain.py) | 三级评估链（IC→稳健性→经济意义） | 评估分数 |
| [experience_chain.py](file:///d:/Programs/factor_system/fts/factor_engine/experience_chain.py) | 经验链，历史进化轨迹学习 | 经验轨迹 + 启发式提示 |
| [seed_pool.py](file:///d:/Programs/factor_system/fts/factor_engine/seed_pool.py) | 种子池管理 | 初始因子集合 |
| [factor_program.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_program.py) | 因子安全沙箱执行 | 隔离的因子运行环境 |
| [verifier.py](file:///d:/Programs/factor_system/fts/factor_engine/verifier.py) | 因子验证器（语法/逻辑/过拟合） | 验证报告 |
| [state.py](file:///d:/Programs/factor_system/fts/factor_engine/state.py) | 进化状态管理 | 持久化的进化状态 |
| [program.py](file:///d:/Programs/factor_system/fts/factor_engine/program.py) | L0 人类设定因子 | 初始因子程序 |

#### 4.2.5 strategies/ — 策略层

| 模块 | 职责 | 关键输出 |
|:-----|:-----|:---------|
| [base_v2.py](file:///d:/Programs/factor_system/fts/strategies/base_v2.py) | 策略基类，通用接口 | BaseStrategyV2 |
| [multi_factor_strategy.py](file:///d:/Programs/factor_system/fts/strategies/multi_factor_strategy.py) | 多因子策略实现 | 多因子信号 + 持仓建议 |

---

## 5. 关键类与接口

### 5.1 数据接入：UnifiedDataProvider（由 Data-Core 提供）

**来源**: [datacore/api.py](file:///d:/Programs/data-core/datacore/api.py)

**职责**: FTS 所有数据获取的唯一入口，由 Data-Core 项目提供，FTS 通过 pip 依赖引入。

```python
# 从 datacore 导入（非 fts.data）
from datacore import UnifiedDataProvider
from datacore.models.enums import DataType, MarketType, SourceGrade
from datacore.models.payload import DataPayload

class UnifiedDataProvider:
    """Data-Core 统一数据入口（由 Data-Core 项目提供）。
    
    FTS 通过此接口获取所有数据，不关心数据源具体实现。
    自动处理：符号解析 → 市场路由 → 多源降级 → 缓存命中 → 数据质量评级。
    """
    
    def get(self, symbol: str, data_type: DataType,
            params: dict | None = None) -> DataPayload:
        """获取指定类型的数据。
        
        内部路由:
        - 期货 (RB/RU/M/...) → FuturesDataProvider (TDX-LC → 东方财富)
        - A股 (600519/000001/...) → EquityDataProvider (腾讯 → 东方财富)
        """
    
    def get_batch(self, symbols: list[str], data_type: DataType,
                  params: dict | None = None) -> dict[str, DataPayload]:
        """批量获取。"""
    
    def list_symbols(self, market: MarketType | None = None) -> list[dict]:
        """列出所有可用符号。"""
```

**FTS 中的使用示例**:
```python
from datacore import UnifiedDataProvider
from datacore.models.enums import DataType

# FTS 因子引擎获取数据
provider = UnifiedDataProvider()

# 获取螺纹钢日线（Data-Core 自动路由到期货数据源）
dp = provider.get("RB", DataType.OHLCV, {"period": "daily", "days": 400})

# 获取贵州茅台行情（Data-Core 自动路由到A股数据源）
dp = provider.get("600519", DataType.QUOTE)

# 获取宏观数据
macro = provider.get("*", DataType.MACRO)
```

### 5.2 DataPayload — 统一数据载荷（由 Data-Core 提供）

**来源**: [datacore/models/payload.py](file:///d:/Programs/data-core/datacore/models/payload.py)

```python
@dataclass
class DataPayload:
    """统一数据载荷信封（由 Data-Core 提供）。"""
    symbol: str
    data_type: DataType
    market: MarketType
    data: Any = None                    # 核心数据（各类型不同）
    source: str = ""                    # 数据源名称
    grade: SourceGrade = SourceGrade.UNAVAILABLE  # 数据质量等级
    collected_at: float = 0.0           # 采集时间戳
    meta: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    
    @property
    def available(self) -> bool:
        """数据是否可用。"""
        return self.grade != SourceGrade.UNAVAILABLE
```

### 5.3 ProcessingStage — 数据处理管线阶段

**位置**: [fts/pipeline/base.py](file:///d:/Programs/factor_system/fts/pipeline/base.py)

**职责**: FTS 自有的因子计算管线，消费 Data-Core 已加工的结构化数据，产出因子输入（如情绪分数、市场制度）。
**边界**: 数据采集和基础加工（新闻分类、实体抽取）由 Data-Core 完成，FTS 管线从已结构化的数据开始。

```python
from datacore.models.enums import DataType
from datacore.models.payload import DataPayload

class ProcessingStage(ABC):
    """数据处理管线阶段（FTS 自有，因子计算层）。
    
    输入 Data-Core 已加工的结构化数据 → 输出因子输入（如带标签的新闻 → 情绪分数）。
    管线可组合串联。LLM 是管线中的一个 stage。
    """
    
    input_type: DataType   # 从 Data-Core 获取的数据类型（已加工）
    output_type: DataType  # 管线产出的因子输入数据类型
    
    @abstractmethod
    def process(self, 
                input_data: DataPayload,
                symbol: str | None = None,
                ) -> DataPayload:
        """处理数据。"""
```

**管线串联示例**:
```python
# 新闻情绪管线：消费 Data-Core 已分类的 NEWS 数据，产出 SENTIMENT 因子输入
# 注: NewsCollector 和 NewsClassifier 已由 Data-Core 完成，NEWS 返回时携带 tags
pipeline = (
    SentimentLLMStage()              # LLM 情绪打分（消费带 tags 的 NEWS）
    >> SentimentAggregatorStage()    # 按品种/时间聚合
)
# 输出: symbol→{date→{score, volume, topics, ...}}

# 零成本模式：跳过 LLM，使用词典法
pipeline_zero_cost = (
    SentimentRuleStage()             # 规则情绪基线（词典法）
    >> SentimentAggregatorStage()    # 按品种/时间聚合
)
```

### 5.4 FactorProgram — 因子程序沙箱

**位置**: [fts/factor_engine/factor_program.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_program.py)

**职责**: 安全沙箱执行因子代码，隔离执行环境。因子通过 Data-Core 获取数据。

```python
from datacore.models.payload import DataPayload
from datacore.models.enums import DataType

class FactorProgram:
    """因子程序 — 安全沙箱执行环境。
    
    将因子表示为可执行的Python代码，在受限环境中运行。
    因子数据通过 Data-Core 的 UnifiedDataProvider 获取。
    """
    
    code: str                       # 因子源代码
    entry_point: str                # 入口函数名
    required_data_types: list[DataType]  # 依赖的数据类型（从 Data-Core 获取）
    
    def execute(self, data: dict[str, DataPayload]) -> np.ndarray:
        """在沙箱中执行因子，返回因子值数组。
        
        Args:
            data: 由 Data-Core 预先获取的 DataPayload 字典
                  key = DataType, value = DataPayload
        """
    
    def validate(self) -> ValidationResult:
        """验证因子代码的语法和安全性。"""
```

### 5.5 EvolutionLoop — L2 演化主循环

**位置**: [fts/factor_engine/evolution_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_loop.py)

**职责**: 因子代码进化的主循环，协调宏观变异和微观调参。

```python
from datacore import UnifiedDataProvider

class EvolutionLoop:
    """L2 因子演化主循环。
    
    四阶段流水线：
    1. Program Selection — 基于UCT树选择进化路径
    2. Idea Generation — LLM结合经验链生成宏观变异
    3. Implementation — 贝叶斯搜索优化参数
    4. Feedback Propagation — 结构化反馈反向传播
    
    数据通过 Data-Core 的 UnifiedDataProvider 获取。
    """
    
    def __init__(self, data_provider: UnifiedDataProvider, max_iterations: int = 200):
        self.data_provider = data_provider  # Data-Core 数据入口
    
    def run(self, seed_pool: SeedPool) -> list[FactorProgram]:
        """运行演化循环，返回精英因子列表。"""
```

### 5.6 SeedPool — 种子池

**位置**: [fts/factor_engine/seed_pool.py](file:///d:/Programs/factor_system/fts/factor_engine/seed_pool.py)

**职责**: 管理初始因子集合，每个因子显式声明依赖的 DataType。

```python
from datacore.models.enums import DataType, MarketType

FACTOR_REGISTRY: dict[str, FactorDef] = {
    # ── 量价因子（全市场，从 OHLCV 计算） ──
    "momentum": {
        "fn": _calc_momentum,
        "data_types": [DataType.OHLCV],  # 需要从 Data-Core 获取 OHLCV
        "markets": "*",
    },
    
    # ── 产业因子（期货专用，从 FUNDAMENTAL 计算） ──
    "basis": {
        "fn": _calc_basis,
        "data_types": [DataType.FUNDAMENTAL],  # 需要从 Data-Core 获取 FUNDAMENTAL
        "markets": [MarketType.FUTURES],
        "required_fields": ["basis_pct"],
    },
    
    # ── 情绪因子（全市场，从 SENTIMENT 计算） ──
    "news_sentiment": {
        "fn": _calc_news_sentiment,
        "data_types": [DataType.SENTIMENT],  # 需要 FTS pipeline 产出的 SENTIMENT
        "markets": "*",
        "priority": "low",
        "pending": True,
    },
}
```

### 5.7 SymbolRegistry（由 Data-Core 提供）

**来源**: [datacore/registry/symbol_registry.py](file:///d:/Programs/data-core/datacore/registry/symbol_registry.py)

**职责**: 全局符号管理，由 Data-Core 提供并维护。FTS 直接使用，不维护自己的符号注册表。

```python
from datacore.registry.symbol_registry import SymbolRegistry

registry = SymbolRegistry()

# 解析符号（Data-Core 内置 56+ 期货品种）
entry = registry.lookup("RB")  # → {symbol: "RB", name: "螺纹钢", market: FUTURES, ...}

# 按市场列出
futures = registry.list_by_market(MarketType.FUTURES)
```

---

## 6. 数据类型体系（Data-Core 提供）

### 6.1 DataType 枚举

**来源**: [datacore/models/enums.py](file:///d:/Programs/data-core/datacore/models/enums.py)

FTS 直接使用 Data-Core 定义的枚举，不重复定义。

```python
# 从 datacore 导入
from datacore.models.enums import DataType, MarketType, SourceGrade

class DataType(str, Enum):
    """数据类型 — 按数据结构特征划分（由 Data-Core 定义）。"""
    OHLCV           = "ohlcv"            # K线：open/high/low/close/volume
    QUOTE           = "quote"            # 实时行情快照
    TECHNICAL       = "technical"        # 技术指标衍生数据
    FINANCIAL       = "financial"        # 财务报表指标 (PE/PB/ROE/营收)
    FUNDAMENTAL     = "fundamental"      # 产业基本面（仓单/库存/基差/开工率）
    MACRO           = "macro"            # 宏观数据（PMI/LPR/CPI/GDP）
    NEWS            = "news"             # 新闻资讯
    ANNOUNCEMENT    = "announcement"     # 公司公告/交易所公告
    SENTIMENT       = "sentiment"        # 情绪分数（由 FTS pipeline 产出）
    MARKET_STATE    = "market_state"     # 市场制度识别（由 FTS pipeline 产出）
```

### 6.2 MarketType 枚举

```python
class MarketType(str, Enum):
    FUTURES      = "futures"       # 国内商品期货
    STOCK        = "stock"         # A 股股票
    ETF          = "etf"           # 交易所交易基金
    CB           = "cb"            # 可转换债券
    REIT         = "reit"          # 不动产投资信托基金
```

### 6.3 SourceGrade 枚举

```python
class SourceGrade(str, Enum):
    """数据质量等级（由 Data-Core 定义，从高到低）。"""
    PRIMARY      = "primary"       # 主源，实时/高质量
    DAILY        = "daily"         # 日频更新
    CACHED       = "cached"        # 缓存数据
    STALE        = "stale"         # 陈旧数据（降级用）
    UNAVAILABLE  = "unavailable"   # 不可用
```

### 6.4 Data-Core 数据源矩阵

Data-Core 已实现的数据源降级链：

| 市场 | 数据类型 | 主源 | 备用源 | 状态 |
|:-----|:---------|:-----|:-------|:-----|
| **期货** | OHLCV / QUOTE | 通达信 TQ-Local | 东方财富 | ✅ 已实现 |
| **A股** | OHLCV / QUOTE | 腾讯自选股 | 东方财富 | ✅ 已实现 |
| **A股** | FINANCIAL | 东方财富 | — | ✅ 已实现 |
| **全市场** | MACRO | 东方财富 | — | ✅ 已实现 |
| **A股** | 全量数据 | 国信证券 | — | 🔧 开发中 |

> 更多详情参考 [Data-Core README](file:///d:/Programs/data-core/README.md)

---

## 7. 市场特征画像与差异分析

### 7.1 设计理念：市场感知的因子演化

FTS 虽然以"数据类型优先"统一架构，但不同市场标的的**数据特征、交易规则、因子有效性**差异巨大。简单地"一套因子跑所有市场"会导致：
- 期货的期限结构 Alpha 被浪费（只用主力合约 OHLCV）
- 股票的财务因子在 ETF 上失效（ETF 没有直接的 PE/PB）
- 可转债的凸性特征无法被通用因子捕捉

因此 FTS 引入 **MarketProfile（市场特征画像）** 机制，在统一架构下实现**市场感知的差异化因子演化**。

```
统一因子引擎 (FactorEngine)
    ├── 通用因子层（OHLCV 量价类，全市场通用）
    └── 市场感知层（MarketProfile 驱动的差异化搜索/评估）
         ├── 期货：合约链 + 期限结构 + 多空对称
         ├── 股票：财务深度 + 做多约束
         ├── ETF：宏观敏感 + 折溢价
         ├── 可转债：转债特异因子 + 凸性
         └── REITs：分红 + 利率敏感
```

### 7.2 MarketProfile — 市场特征画像

```python
@dataclass
class MarketProfile:
    """市场特征画像 — 指导因子演化的搜索空间和评估方式。
    
    由 Data-Core 的 SymbolRegistry 提供，FTS 因子引擎读取后
    动态调整搜索空间、变异算子、评估指标。
    """
    
    market: MarketType
    
    # ── 交易规则 ──
    trading_directions: list[str]       # ["long", "short"] 或 ["long"]
    has_intraday: bool                  # 是否支持日内交易（T+0）
    has_price_limit: bool               # 是否有涨跌停限制
    margin_ratio: float | None          # 保证金比例（期货）
    
    # ── 数据特征 ──
    has_multiple_contracts: bool        # 是否有多合约体系（期货）
    financial_depth: str                # "deep"（股票）/ "shallow"（ETF）/ "none"（期货）
    macro_sensitivity: float            # 0~1，宏观敏感度（ETF/REITs 高，个股低）
    
    # ── 因子有效性先验 ──
    factor_category_weights: dict[str, float]  # 各类因子的权重先验
    # 例：期货 {term_structure: 0.3, momentum: 0.2, carry: 0.2, ...}
    # 例：股票 {value: 0.25, quality: 0.2, momentum: 0.15, ...}
    # 例：ETF {macro: 0.3, style_rotation: 0.25, flow: 0.2, ...}
```

### 7.3 各市场数据特征对比

| 特征维度 | 期货 | A股股票 | ETF | 可转债 | REITs |
|:---------|:-----|:-------|:----|:-------|:------|
| **合约体系** | 多合约链 | 单一标的 | 单一标的 | 单一标的 | 单一标的 |
| **多空方向** | 多空双向 | 仅做多 | 仅做多 | 多空（T+0） | 仅做多 |
| **日内交易** | T+0 | T+1 | T+1（场内） | T+0（沪市） | T+1 |
| **财务深度** | 无 | 深（三大报表） | 浅（成分股加权） | 中（转债+正股） | 中（派息/NAV） |
| **宏观敏感度** | 中高 | 低（个股） | 高（宽基） | 中 | 高（利率敏感） |
| **杠杆/保证金** | 有（5-15%） | 无 | 无 | 无 | 无 |
| **涨跌停** | 有（品种差异） | 有（±10%/20%） | 有 | 无（沪市） | 有 |
| **特异数据** | 基差/仓单/库存 | PE/PB/ROE/营收 | 净值/折溢价/份额 | 转股溢价/纯债价值 | 派息率/NAV |

### 7.4 期货：多合约与期限结构（高优先级）

期货是所有市场中数据结构最特殊的——**一个品种对应多条合约曲线**，而非一条时间序列。

```
螺纹钢合约链示意：
RB2501 ──┐
RB2505 ──┤── 期限结构曲线（升水/贴水）
RB2510 ──┤
RB2601 ──┘
   \      \      \
  跨期价差 展期收益 基差率
```

#### 7.4.1 期货特异数据类型（Data-Core 提供）

| 数据类型 | 说明 | 可构造因子 |
|:---------|:-----|:-----------|
| `FUTURES_CONTRACT_CHAIN` | 整条合约链的 OHLCV | 期限结构斜率、曲线曲率 |
| `FUTURES_SPREAD` | 指定合约对的价差 | 价差回归、跨期套利因子 |
| `FUTURES_TERM_STRUCTURE` | 期限结构快照 | 展期收益率、carry 因子 |
| `FUTURES_BASIS` | 基差数据（现货-期货） | 基差率、基差动量 |

#### 7.4.2 期货对因子演化的特殊影响

| 影响维度 | 说明 | 处理方式 |
|:---------|:-----|:---------|
| **搜索空间** | 因子可以操作"整条合约链"而非单个序列 | 增加合约维度变异算子（选哪些合约、如何组合） |
| **评估方式** | 多空对称，IC 双向都有效 | 评估链不做单边约束 |
| **策略形态** | 支持跨期套利、蝶式套利等价差策略 | 策略层增加 spread strategy 模板 |
| **展期处理** | 主力合约切换时价格跳空，因子计算需处理 | Data-Core 提供复权连续合约，因子可选择原始/复权 |

### 7.5 股票：财务深度与做多约束（中高优先级）

| 特征 | 对因子演化的影响 |
|:-----|:----------------|
| **财务指标丰富** | 价值/质量/成长类因子搜索空间大，权重高 |
| **只能做多** | 评估时只看多头端 IC，空头信号需过滤 |
| **涨跌停** | 涨跌停日的价格信号失真，因子计算需特殊处理 |
| **T+1** | 日内因子有效性低，偏向日频及以上 |

### 7.6 ETF/REITs：宏观敏感与折溢价（中优先级）

| 特征 | 对因子演化的影响 |
|:-----|:----------------|
| **财务指标浅** | 财务类因子权重低，宏观/风格/资金流权重高 |
| **折溢价率** | 新增 premium_discount 因子（价格-净值偏差） |
| **份额变化** | 资金流因子（份额增减反映机构行为） |
| **宏观敏感** | 宽基 ETF 对利率/PMI/流动性等宏观因子反应直接 |
| **REITs 分红** | 派息率因子、利率敏感度因子 |

### 7.7 可转债：凸性与混合属性（低优先级，Phase 5+）

| 特征 | 对因子演化的影响 |
|:-----|:----------------|
| **债底+股性** | 转股溢价率、纯债价值、Delta 等转债特异因子 |
| **条款博弈** | 赎回/回售/下修条款影响价格行为 |
| **非对称收益** | 凸性因子（下跌有底、上涨无限） |
| **T+0** | 日内因子有效性高 |

### 7.8 市场感知的因子演化机制

L1 元循环不仅识别"市场制度"（趋势/震荡），还识别**市场类型特征**，动态调整 L2 演化的搜索空间：

```python
class MetaLoop:
    def adjust_search_space(self, market: MarketType):
        """根据市场类型调整因子演化搜索空间。"""
        
        profile = MarketProfileRegistry.get(market)
        
        if market == MarketType.FUTURES:
            # 期货：启用合约链变异 + 期限结构因子 + 多空策略
            self.enable_contract_chain_mutation()
            self.add_factor_category("term_structure", weight=0.3)
            self.add_factor_category("carry", weight=0.2)
            self.enable_spread_strategy_template()
            
        elif market == MarketType.STOCK:
            # 股票：强化财务因子 + 做多约束
            self.add_factor_category("value", weight=0.25)
            self.add_factor_category("quality", weight=0.2)
            self.constrain_long_only()
            
        elif market == MarketType.ETF:
            # ETF：宏观敏感 + 折溢价 + 资金流
            self.add_factor_category("macro", weight=0.3)
            self.add_factor_category("flow", weight=0.2)
            self.add_factor_category("premium", weight=0.15)
            
        elif market == MarketType.CB:
            # 可转债：转债特异因子
            self.add_factor_category("conversion", weight=0.3)
            self.add_factor_category(" convexity", weight=0.2)
            
        # 通用因子始终启用（momentum/volatility/volume 等）
        self.add_factor_category("price", weight=0.25)
```

---

## 8. 因子体系

### 8.1 因子与数据类型的映射

每个因子显式声明依赖哪些 DataType，引擎通过 Data-Core 自动编排数据获取。

```python
# 因子声明示例
FACTOR_REGISTRY = {
    "momentum": {
        "fn": _calc_momentum,
        "data_types": [DataType.OHLCV],       # → Data-Core 获取
        "markets": "*",
    },
    "basis": {
        "fn": _calc_basis,
        "data_types": [DataType.FUNDAMENTAL],  # → Data-Core 获取
        "markets": [MarketType.FUTURES],
    },
    "news_sentiment": {
        "fn": _calc_news_sentiment,
        "data_types": [DataType.SENTIMENT],    # → FTS pipeline 产出
        "markets": "*",
        "pending": True,                        # 等待新闻源就绪后激活
    },
}
```

### 8.2 因子分类（按市场感知分层）

FTS 因子库采用**通用 + 市场特异**的分层设计：

```
FTS 因子库
├── 通用因子层（全市场，基于 OHLCV）
│   ├── 动量类（momentum, reversal）
│   ├── 波动率类（volatility, atr_ratio）
│   ├── 成交量类（volume_flow, obv_slope）
│   └── 形态类（channel_breakout, fractal_dimension）
│
├── 期货专用因子（FUTURES only）
│   ├── 期限结构类（term_structure_slope, roll_yield）
│   ├── 基差类（basis_rate, basis_momentum）
│   ├── 跨期价差类（spread_mean_reversion）
│   └── 持仓类（position_change, inventory_pct）
│
├── 股票专用因子（STOCK only）
│   ├── 价值类（pe_ep, pb_ratio, dividend_yield）
│   ├── 质量类（roe, roic, profit_margin）
│   ├── 成长类（revenue_growth, eps_growth）
│   └── 分析师预期类（consensus_revision）
│
├── ETF/REITs 专用因子
│   ├── 折溢价类（premium_discount）
│   ├── 资金流类（share_change, fund_flow）
│   └── 宏观敏感类（rate_sensitivity, inflation_beta）
│
└── 可转债专用因子（CB only）
    ├── 转股类（conversion_premium, delta）
    ├── 债底类（pure_bond_value, ytm）
    └── 凸性类（convexity_score）
```

#### 8.2.1 通用因子 — OHLCV 派生（全市场通用）

| 因子名 | 原始数据类型 | 计算方式 | 数据来源 |
|:-------|:------------|:---------|:---------|
| momentum | OHLCV | 价格变化率 + MA 斜率 | Data-Core |
| volatility_reversion | OHLCV | 布林带位置 + ATR | Data-Core |
| volume_flow | OHLCV | 成交量 + OBV | Data-Core |
| macro_regime | OHLCV | 价格相对 MA120 | Data-Core |
| trend_strength_adx | OHLCV | Wilder DMI 计算 | Data-Core |
| ma_alignment | OHLCV | MA5/10/20/60 排序 | Data-Core |

#### 8.2.2 基本面派生因子（按市场分层）

| 因子名 | 原始数据类型 | 适用市场 | 数据来源 |
|:-------|:------------|:---------|:---------|
| basis | FUNDAMENTAL | 期货 | Data-Core |
| inventory_pct | FUNDAMENTAL | 期货 | Data-Core |
| pe_ep | FINANCIAL | A股股票 | Data-Core |
| pb_ratio | FINANCIAL | A股股票 | Data-Core |
| dividend_yield | FINANCIAL | 股票/REITs | Data-Core |

#### 8.2.3 新闻/情绪派生因子（FTS pipeline 产出）

| 因子名 | 原始数据类型 | 计算方式 | 数据来源 |
|:-------|:------------|:---------|:---------|
| news_sentiment | NEWS → SENTIMENT | 新闻情绪聚合得分 | Data-Core(NEWS) + FTS pipeline(SENTIMENT) |
| sentiment_divergence | SENTIMENT | 情绪与价格走势背离 | FTS pipeline |
| news_volume | NEWS | 新闻量异常暴增 | Data-Core(NEWS) |
| topic_intensity | NEWS | 特定主题热度变化 | Data-Core(NEWS) + FTS pipeline |

#### 8.2.4 宏观派生因子

| 因子名 | 原始数据类型 | 计算方式 | 数据来源 |
|:-------|:------------|:---------|:---------|
| pmi_proxy | MACRO | PMI > 50 → 偏多 | Data-Core |
| rate_proxy | MACRO | LPR 升降方向 | Data-Core |
| macro_momentum | MACRO | PMI 环比变化方向 | Data-Core |

### 8.3 因子进化机制

#### 三大分离设计

1. **逻辑分离**：程序逻辑/思想进化（LLM负责） vs 参数优化（贝叶斯搜索负责）
2. **搜索策略分离**：LLM驱动的定向启发式搜索 vs 自动贝叶斯超参数搜索
3. **资源分离**：LLM API调用（昂贵、稀疏） vs 本地计算资源（廉价、密集）

#### 四阶段进化流水线

| 阶段 | 名称 | 职责 | 技术 |
|:-----|:-----|:-----|:-----|
| 1 | Program Selection | 基于UCT树选择进化路径，平衡探索与利用 | UCT树搜索 |
| 2 | Idea Generation | LLM结合经验链生成高层启发和结构修改 | LLM + Chain of Experience |
| 3 | Implementation | 贝叶斯搜索优化参数，两阶段验证确保可执行性 | Optuna/贝叶斯优化 |
| 4 | Feedback Propagation | 结构化反馈沿进化路径反向传播 | Q值更新 |

### 8.4 三级评估链

| 级别 | 评估维度 | 指标 | 通过标准 |
|:-----|:---------|:-----|:---------|
| 一级 | 统计显著性 | IC, ICIR, Rank IC | IC > 阈值 & ICIR > 阈值 |
| 二级 | 稳健性检验 | 分时段IC, 参数敏感性 | 多时段稳定，参数不敏感 |
| 三级 | 经济解释性 | 因子逻辑, 边际贡献 | 有合理经济逻辑，新增信息 |

---

## 9. 依赖关系

### 9.1 模块间依赖图

```
strategies/
    └── depends on → factor_engine/
                        ├── depends on → datacore (Data-Core 外部包)
                        ├── depends on → pipeline/ (FTS 数据处理管线)
                        ├── depends on → indicators/ (FTS 指标计算)
                        └── depends on → core/ (FTS 自身契约)

pipeline/
    └── depends on → datacore (消费 Data-Core 的原始数据)

indicators/
    └── 纯 numpy，无内部依赖

core/
    └── depends on → datacore.models (复用 DataType/MarketType 枚举)
```

### 9.2 外部依赖

| 依赖包 | 用途 | 必需 | 来源 |
|:-------|:-----|:-----|:-----|
| **datacore** | 数据基础设施（UnifiedDataProvider/SymbolRegistry/DataPayload） | ✅ | [Data-Core 项目](file:///d:/Programs/data-core/) |
| **numpy** | 数值计算，指标向量化 | ✅ | PyPI |
| **pandas** | 数据处理，DataFrame 操作 | ✅ | PyPI |
| **optuna** | 贝叶斯超参数优化 | ⚙️ 因子进化必需 | PyPI |
| **openai / anthropic** | LLM API 客户端 | ⚙️ LLM 情绪/进化必需 | PyPI |
| **pyyaml** | YAML 配置解析 | ✅ | PyPI |

### 9.3 Data-Core 提供的关键依赖

FTS 从 `datacore` 包导入以下核心组件：

```python
# 数据入口
from datacore import UnifiedDataProvider

# 枚举（数据类型/市场类型/质量等级）
from datacore.models.enums import DataType, MarketType, SourceGrade

# 数据载荷
from datacore.models.payload import DataPayload

# 符号注册表
from datacore.registry.symbol_registry import SymbolRegistry

# OHLCV 数据结构
from datacore.models.ohlcv import KlineData, KlineBar, QuoteData
```

### 9.4 Data-Core 自身架构（供参考）

Data-Core 作为独立项目，其内部架构如下：

```
datacore/
├── api.py                    # UnifiedDataProvider 统一入口
├── cli.py                    # 命令行工具
├── config.py                 # 配置管理
├── models/                   # 数据模型
│   ├── enums.py              # DataType/MarketType/SourceGrade
│   ├── payload.py            # DataPayload 统一信封
│   └── ohlcv.py              # K线/行情数据结构
├── registry/                 # 符号注册表
│   └── symbol_registry.py    # 56+ 期货品种
├── futures/                  # 期货数据模块
│   ├── futures_provider.py   # 期货多源降级: TDX-LC → 东方财富
│   └── providers/
│       ├── tdx_lc.py         # 通达信 TQ-Local
│       └── eastmoney.py      # 东方财富
├── equity/                   # A股数据模块
│   ├── equity_provider.py    # A股多源降级: 腾讯 → 东方财富
│   ├── financial.py          # 财务数据
│   └── providers/
│       ├── tencent.py        # 腾讯自选股
│       └── eastmoney.py      # 东方财富
└── store/                    # 存储层
    ├── cache.py              # 内存缓存
    ├── duckdb.py             # DuckDB 持久化
    ├── postgres.py           # PostgreSQL
    └── redis.py              # Redis 热缓存
```

---

## 10. 项目运行方式

### 10.1 环境准备

```bash
# 1. 先安装 Data-Core 数据基础设施
cd d:\Programs\data-core
pip install -e "datacore[full]"  # 完整安装（含 DuckDB/Redis/PostgreSQL）

# 2. 安装 FTS
cd d:\Programs\factor_system
pip install -e .

# 3. 配置数据源（见 Data-Core 配置）
# 编辑 ~/.datacore/settings.yaml 或设置环境变量
```

### 10.2 Data-Core 配置

配置文件由 Data-Core 管理，位置: `~/.datacore/settings.yaml` 或 `config/settings.yaml`

```yaml
# Data-Core 数据源配置
sources:
  tdx_lc:
    enabled: true
    url: http://127.0.0.1:17709/
    timeout: 3
  eastmoney:
    enabled: true
  tencent:
    enabled: true
  guosen:
    enabled: false
    api_key: YOUR_API_KEY

store:
  backend: duckdb
  cache_ttl: 3600
  duckdb_path: ~/.datacore/datacore.db
```

> 详细配置参考 [Data-Core README](file:///d:/Programs/data-core/README.md)

### 10.3 FTS 配置

FTS 自身配置: [config/settings.yaml](file:///d:/Programs/factor_system/config/settings.yaml)

```yaml
# FTS 因子引擎配置
factor_engine:
  max_iterations: 200
  population_size: 50
  elite_count: 10
  evaluation_metric: "ic"

# LLM 配置（因子进化用）
llm:
  provider: "anthropic"
  api_key: "your_api_key"
  model: "claude-3-sonnet-20240229"

# 情绪管线配置
sentiment:
  mode: "rule"  # "rule" (零成本词典法) | "llm" (LLM增强)
```

### 10.4 CLI 命令

**入口**: [fts/cli.py](file:///d:/Programs/factor_system/fts/cli.py)

```bash
# 因子挖掘
fts factor evolve --market futures --symbols RB,RU,M --iter 200
fts factor list --elite-only
fts factor test <factor_id> --symbol RB

# 策略回测
fts strategy backtest multi_factor --symbols RB,RU,M --start 2024-01-01

# 情绪管线
fts pipeline sentiment --mode rule --days 30

# 系统监控
fts monitor status
```

> **数据相关命令** 使用 Data-Core 的 CLI: `datacore --help`

### 10.5 Python API 使用

```python
# === 从 Data-Core 导入数据层 ===
from datacore import UnifiedDataProvider
from datacore.models.enums import DataType

# === 从 FTS 导入因子引擎 ===
from fts.factor_engine.evolution_loop import EvolutionLoop
from fts.factor_engine.seed_pool import SeedPool
from fts.pipeline.sentiment_rule import RuleSentimentStage

# 1. 初始化 Data-Core 数据提供者
provider = UnifiedDataProvider()

# 2. 获取数据（由 Data-Core 自动路由）
ohlcv = provider.get("RB", DataType.OHLCV, {"period": "daily", "days": 400})
print(f"数据来源: {ohlcv.source}, 质量: {ohlcv.grade}")

# 3. 运行 FTS 情绪管线（消费 Data-Core 的 NEWS 数据）
news = provider.get("*", DataType.NEWS, {"date": "2026-07-18"})
sentiment_stage = RuleSentimentStage()
sentiment = sentiment_stage.process(news)

# 4. 运行因子演化
seed_pool = SeedPool.from_default("futures")
loop = EvolutionLoop(data_provider=provider, max_iterations=200)
elite_factors = loop.run(seed_pool)

for factor in elite_factors:
    print(f"因子: {factor.name}, IC: {factor.metrics.ic:.4f}")
```

---

## 11. 实施路线图

### 11.1 实施阶段总览

| 阶段 | 内容 | 前置条件 | 产出物 |
|:-----|:-----|:---------|:-------|
| **Phase 1** | FTS 核心契约 + 因子引擎骨架 + Data-Core 集成 | Data-Core v0.1.0+ | 因子引擎框架 |
| **Phase 2** | 因子引擎完整实现（三层循环 + 市场感知） | Phase 1 | 可用的因子进化引擎 |
| **Phase 3** | 数据处理管线（新闻/情绪/市场制度） | Phase 2 | 衍生数据管线 |
| **Phase 4** | 多因子策略 + CLI + 调度 | Phase 2, 3 | 完整可运行系统 |
| **Phase 5** | 市场感知增强 + 测试 + 文档 + FDT 侧清理 | Phase 4 | 交付就绪 |

### 11.2 Phase 1 — 核心契约 + Data-Core 集成

**目标**: 搭建 FTS 骨架，验证与 Data-Core 的集成。

**任务清单**:
- [ ] 定义 FTS 核心契约 (contracts.py: FactorDef, EvolutionState)
- [ ] 验证 `from datacore import UnifiedDataProvider` 可用
- [ ] 实现 FactorProgram 因子沙箱骨架
- [ ] 实现 SeedPool 种子池骨架（声明依赖的 DataType）
- [ ] 实现 EvolutionLoop 主循环骨架
- [ ] 编写 Data-Core 集成测试

### 11.3 Phase 2 — 因子引擎完整实现

**目标**: 因子引擎完整可用，支持自动因子挖掘，包含市场感知能力。

**任务清单**:
- [ ] 实现 L2 EvolutionLoop 完整逻辑
- [ ] 实现 LLM 宏观变异 (macro_evolution)
- [ ] 实现贝叶斯微观调参 (micro_evolution)
- [ ] 实现 EvaluationChain 三级评估链
- [ ] 实现 ExperienceChain 经验链
- [ ] 实现 L1 MetaLoop 元循环（含市场类型感知）
- [ ] 实现 L3 PortfolioLoop 组合循环
- [ ] 实现 MarketProfile 市场特征画像
- [ ] 实现市场感知的搜索空间调整

### 11.4 Phase 3 — 数据处理管线

**目标**: 新闻情绪管线可用，市场制度识别可用。

**任务清单**:
- [ ] 实现 ProcessingStage 抽象基类
- [ ] 实现新闻分类器 (NewsClassifier)
- [ ] 实现规则情绪分析（词典法，零成本）
- [ ] 实现 LLM 情绪分析（可选增强）
- [ ] 实现情绪聚合器
- [ ] 实现市场制度识别 (market_regime)

### 11.5 Phase 4 — 多因子策略 + CLI

**目标**: 多因子策略可用，系统可操作。

**任务清单**:
- [ ] 迁移 BaseStrategyV2 策略基类
- [ ] 实现 MultiFactorStrategy 多因子策略
- [ ] 实现 CLI 命令行入口
- [ ] 实现调度器 (Scheduler)
- [ ] 实现健康监控

### 11.6 Phase 5 — 市场感知增强 + 测试 + 文档

**目标**: 系统测试通过，市场感知因子演化，文档完善，准备交付。

**任务清单**:
- [ ] 期货：合约链与期限结构因子支持
- [ ] 股票：财务因子 + 做多约束支持
- [ ] ETF/REITs：折溢价 + 资金流因子支持
- [ ] 编写单元测试
- [ ] 编写集成测试（含 Data-Core 集成）
- [ ] FDT 侧清理（移除 loop_engine/ 和 multi_factor 相关代码）
- [ ] 文档完善

---

## 12. 设计原则

### 12.1 核心设计原则

| 原则 | 说明 |
|:-----|:-----|
| **三项目独立** | FDT、FTS、Data-Core 互相独立；FDT 自有数据层，FTS 通过 Data-Core 获取数据 |
| **关注点分离** | FDT 负责"期货交易决策"，FTS 负责"因子智能"，Data-Core 负责"数据基础设施" |
| **数据层外置** | FTS 数据获取/存储/符号管理全部委托给 Data-Core，不重复造轮子 |
| **市场感知演化** | 因子引擎根据 MarketProfile 动态调整搜索空间，不同市场差异化因子进化 |
| **数据采集加工归 Data-Core** | 数据采集和基础加工（新闻分类/结构化抽取）由 Data-Core 负责，FTS 只消费已加工数据 |
| **FTS 专注因子智能** | FTS 职责为数据消费 + 因子推演 + 策略组建 + 交易信号产出，不涉及数据采集 |
| **因子声明所需数据类型** | 每个因子显式声明依赖哪些 DataType，引擎通过 Data-Core 自动编排 |
| **LLM 是管线 stage** | LLM 是数据处理管线中的一个处理阶段，不是独立的数据消费者 |
| **程序级因子表示** | 因子为图灵完备的Python代码，突破符号表达式限制 |
| **情绪默认零成本** | 规则基线模式可运行，LLM 增强可选 |

### 12.2 可扩展性设计

#### 新增因子（3步）
1. 编写因子函数，声明依赖的 DataType（从 Data-Core 获取）
2. 注册到 FACTOR_REGISTRY
3. 策略自动发现并集成

#### 新增数据处理管线（2步）
1. 实现 ProcessingStage 接口
2. 串联到现有管线

#### 新增数据源
- **由 Data-Core 项目负责**，FTS 无需改动
- Data-Core 新增数据源后，FTS 自动获得新数据能力

#### 新增市场
- **主要由 Data-Core 负责**（注册符号到 SymbolRegistry）
- FTS 中选择已有数据类型（OHLCV 自动适配），现有因子自动运行

### 12.3 过拟合防御机制

| 机制 | 说明 |
|:-----|:-----|
| **多层交叉验证** | 训练集/验证集/测试集严格分离，滚动验证 |
| **参数随机化** | 随机参数基线对比，排除运气成分 |
| **伪因子框架** | 生成随机因子作为无效基准 |
| **降级机制** | 因子性能衰减超过阈值自动降级/停用 |
| **稳健性检验** | 分时段、分品种、参数扰动多重检验 |

### 12.4 多样性保护机制

| 机制 | 说明 |
|:-----|:-----|
| **相似性惩罚** | 因子相似度过高时降低适应度 |
| **尼采机制** | 定期引入随机外来因子，增加多样性 |
| **覆盖度监控** | 监控因子在不同市场/时段的覆盖度 |
| **多岛进化** | 多个独立进化岛并发，定期迁移精英 |

---

## 附录

### A. 相关文档

| 文档 | 位置 | 说明 |
|:-----|:-----|:-----|
| FTS 实施计划 | [PLAN.md](file:///d:/Programs/factor_system/PLAN.md) | 项目详细设计 |
| Data-Core README | [README.md](file:///d:/Programs/data-core/README.md) | 数据基础设施说明 |
| Data-Core Code Wiki | [CODE_WIKI.md](file:///d:/Programs/data-core/CODE_WIKI.md) | Data-Core 代码 Wiki |
| Data-Core 架构文档 | [ARCHITECTURE.md](file:///d:/Programs/data-core/ARCHITECTURE.md) | Data-Core 架构设计 |
| FactorEngine 论文 | [论文精读](file:///D:/Knowledge/quant/paper/2026-07-17_FactorEngine__Program-level_Knowledge-Infused_Factor_Mining_Framework.md) | 程序级因子挖掘框架理论基础 |
| 因子挖掘Agent设计 | [设计框架](file:///D:/Knowledge/method/2026-07-09_因子挖掘Agent设计框架.md) | Agent 化因子挖掘设计思路 |

### B. 知识库参考

知识库位置: `D:\Knowledge\`

| 类别 | 相关资源 |
|:-----|:---------|
| **因子投资** | 因子投资：方法与实践—石川 |
| **动量策略** | 动量策略_利用Python建立关键交易模型—安德烈亚斯·克列诺 |
| **趋势跟踪** | 趋势交易—安德烈亚斯·克列诺, 趋势跟踪(原书第5版)—迈克尔·卡沃尔 |
| **主动投资组合管理** | 主动投资组合管理（原书第2版·典藏版）—格林诺德_卡恩 |
| **量化价值投资** | 量化价值投资：人工智能算法驱动的理性投资—格雷_卡莱尔 |
| **Agent Harness** | Agent_Harness_十二大模块, Harness Engineering 工程手册 |
| **Loop Engineering** | Loop Engineering: The Karpathy Method, LangChain Loop Engineering |

### C. 版本历史

| 版本 | 日期 | 变更说明 |
|:-----|:-----|:---------|
| v2.2 | 2026-07-18 | 项目边界梳理：Data-Core 负责数据采集+数据加工（含新闻分类），FTS 只负责数据消费+因子推演+策略组建+交易信号产出；pipeline/ 移除 news_classifier.py，新增 sentiment_aggregator.py |
| v2.1 | 2026-07-18 | 新增市场特征画像（MarketProfile）章节：期货多合约/期限结构、股票财务深度、ETF宏观敏感、可转债凸性等差异分析；因子库改为通用+市场特异分层设计；L1元循环增加市场类型感知；实施路线图增加市场感知增强阶段 |
| v2.0 | 2026-07-18 | 架构重大修正：明确三项目独立关系（FDT/FTS/Data-Core），仅剥离因子引擎+多因子策略，FDT保留完整数据层；新增剥离范围统计（8,259行/28文件/8.2%） |
| v1.3 | 2026-07-18 | 修正 Data-Core 数据源：移除 Tushare（Data-Core 未使用） |
| v1.2 | 2026-07-18 | 修正架构：FTS 不含数据层，数据层使用独立 Data-Core 项目 |
| v1.1 | 2026-07-18 | 首次 Code Wiki 发布，基于 PLAN.md v1.1 规划 |

---

> **文档状态**: 规划阶段文档，代码实现中持续更新
> **维护者**: factor_system 团队
