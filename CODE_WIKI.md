# FTS — Factor Intelligence System Code Wiki

> **项目路径**: `d:\Programs\factor_system`
> **版本**: v1.8.0
> **Python**: >=3.10
> **入口点**: `fts = "fts.cli:main"`
> **代码规模**: ~5,800 语句, 80+ 源码+测试文件, 1601 测试通过, 91% 覆盖率

---

## 目录

1. [项目概述与定位](#1-项目概述与定位)
2. [整体架构](#2-整体架构)
3. [模块结构](#3-模块结构)
4. [关键类与函数](#4-关键类与函数)
5. [模块间依赖关系](#5-模块间依赖关系)
6. [外部依赖](#6-外部依赖)
7. [运行/构建/测试方式](#7-运行构建测试方式)
8. [核心设计模式](#8-核心设计模式)
9. [配置体系](#9-配置体系)
10. [运行时状态文件](#10-运行时状态文件)

---

## 1. 项目概述与定位

FTS（Factor Intelligence System，因子智能系统）是一个 **AI 原生的量化因子发现、评估、组合与演化引擎**。位于数据流中间位置：

```
上游数据源                          FTS 核心                         下游消费
┌──────────────────────┐    ┌──────────────────────┐    ┌──────────────────┐
│ MCP/akshare          │    │ 因子发现 → 评估       │    │ 交易信号消费方    │
│ (腾讯自选股/东方财富) │───→│ → 组合 → 信号输出    │───→│ (FDT 等下游系统)  │
│ DuckDB kline_cache   │    │                      │    │                  │
│ (期货连续合约)        │    │ L1/L2/L3 三层循环    │    │                  │
└──────────────────────┘    └──────────────────────┘    └──────────────────┘
```

### 1.1 核心能力

| 能力 | 说明 |
|:-----|:-----|
| **因子推演** | L0 人类设定 → L1 知识感知 → L2 因子演化 → L3 组合构建 |
| **多资产支持** | A 股、ETF、82 个期货品种（25 核心 + 57 全量），支持期货基本面数据注入 |
| **种子因子库** | 482 个种子因子（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面） |
| **定时调度** | APScheduler 自动化 L1/L2/L3 全链路定时执行，含看门狗和热重载 |
| **健康监控** | HTTP 端点 + Web UI 仪表盘 + Elite 因子追踪 + 自动淘汰 |
| **信号产出** | 交易信号输出到 `reports/` 目录，支持期货横截面信号管道（全量商品池） |

### 1.2 项目边界

| 职责 | 归属 |
|:-----|:-----|
| 行情数据获取（A 股/ETF） | FTS（通过 MCP/akshare 接入腾讯/东方财富 API） |
| 行情数据获取（期货） | FTS（通过 DuckDB kline_cache + AKShare futures_zh_daily_sina） |
| 基本面数据获取 | FTS（通过 MCPBridge 读取 Agent 预填充的缓存，或合成降级） |
| 因子推演（挖掘/演化/评估） | **FTS 核心能力** |
| 多因子策略组建 | **FTS 核心能力** |
| 交易信号产出 | **FTS 核心能力** |
| 循环调度与状态管理 | **FTS 核心能力** |
| 健康监控与 HTTP 指标 | **FTS 核心能力** |

---

## 2. 整体架构

### 2.1 四层循环架构（L0 → L1 → L2 → L3）

```
┌──────────────────────────────────────────────────────────────────┐
│  L0 人类设定层 (Human Configuration)                              │
│  Program.md — 人类设定因子演化的目标、约束、市场偏好、风险偏好    │
└───────────────────────────┬──────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  L1 Meta-Loop (元循环) — 每日 08:30                               │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Step 1: Web 感知 → 新闻/市场快照获取                       │  │
│  │ Step 2: Debate 分析 → 读取辩论数据，识别薄弱维度           │  │
│  │ Step 3: Bootstrapping → Agent 链提取/验证/代码生成         │  │
│  │ Step 4: L1 Verifier → 宽松筛选（2/4 维度 + 可执行）        │  │
│  │ Step 5: 注入 factor_pool.json + l1_injected/               │  │
│  └────────────────────────────────────────────────────────────┘  │
│  职责: 知识补给 → 种子因子注入 → 市场语境感知 → 演化方向指引    │
└───────────────────────────┬──────────────────────────────────────┘
                            │ 注入种子因子 + 演化方向
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  L2 Evolution Loop (演化循环) — 每日 23:00                        │
│  for generation in 1..MAX_GEN:                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Step 1: Macro Evolution → LLM 修改因子代码逻辑             │  │
│  │ Step 2: Micro Evolution → optuna TPE 贝叶斯调参            │  │
│  │ Step 3: Evaluation Chain → 三级评估（回测+经济学+多重检验） │  │
│  │ Step 4: Verifier → 锁定标准判定（pass→elite / fail→淘汰）  │  │
│  │ Step 5: Experience Chain → 记录经验（LLM 下一轮参考）      │  │
│  │ Step 6: State → 持久化状态，检查熔断器                     │  │
│  └────────────────────────────────────────────────────────────┘  │
│  熔断: token 超限 / 连续低 IC / 失败率超限 → 自动停止           │
│  职责: 夜间批量演化 → LLM 逻辑改造 → optuna 参数优化 → elite    │
└───────────────────────────┬──────────────────────────────────────┘
                            │ elite 因子
                            ▼
┌──────────────────────────────────────────────────────────────────┐
│  L3 Portfolio Loop (组合循环) — 每日 20:00                        │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Step 1: 加载 elite 因子                                    │  │
│  │ Step 2: QR 正交化 → 剔除高相关性                           │  │
│  │ Step 3: 6 个月衰减检验 → 衰减 >30% 剔除                    │  │
│  │ Step 4: 信号合成（等权/夏普加权/Ridge 回归）               │  │
│  │ Step 5: 注入下游 → combo.json + 信号报告                   │  │
│  └────────────────────────────────────────────────────────────┘  │
│  职责: 组合构建 → 正交化 → 衰减检验 → 信号合成                  │
└──────────────────────────────────────────────────────────────────┘
```

### 2.2 调度时间表

| 循环 | 触发时间 | 频率 | 职责 |
|:-----|:---------|:-----|:-----|
| L1 Meta-Loop | 08:30 | 每日 | 知识补给 + 种子注入 |
| L2 Evolution Loop | 23:00 | 每日 | 夜间因子演化（期货横截面） |
| L3 Portfolio Loop | 20:00 | 每日 | 组合构建 + 正交化 + 信号合成 |
| 期货信号管道 | 20:30 | 每日 | 横截面信号报告（全量商品池，Ridge 回归加权） |
| Health Check | 每 10 分钟 | 高频 | 状态监控 |

### 2.3 关键架构属性

| 属性 | 说明 |
|:-----|:-----|
| **trace_id 全链路** | 所有 CLI 子命令和工作流启动时生成，贯穿所有模块和日志 |
| **Verifier 锁定协议** | 评估配置初始化后锁定，任何运行时修改抛异常 |
| **TypedDict 契约优先** | 所有数据形状在 `contracts.py` 中声明为 TypedDict |
| **原子持久化** | `atomic_write()` 临时文件 + `os.replace()` 实现崩溃安全 |
| **安全沙箱** | 白名单导入 + 黑名单函数名 + AST 预验证 + 受限 `__builtins__` |
| **静默降级** | 所有可选依赖惰性导入，缺失时回退 Mock 实现 |
| **熔断器** | 三阈值：token 预算 / 连续低 IC / 失败率超限 |

---

## 3. 模块结构

### 3.1 顶层目录布局

```
d:\Programs\factor_system\
├── fts/                              # 核心源码包
│   ├── __init__.py                   # 包入口 + 版本号 v1.8.0 + .env 自动加载
│   ├── cli.py                        # 统一 CLI 入口
│   ├── data.py                       # 数据层统一入口（FTSDataProvider）
│   ├── data_mcp.py                   # MCP 数据适配层（akshare 腾讯/东方财富）
│   ├── data_mcp_bridge.py            # MCP 数据桥接层（本地缓存读取 + MX API 解析）
│   ├── data_fundamental.py           # 基本面数据层（估值/财务/宏观字段注入）
│   ├── data_futures.py               # 期货数据适配层（DuckDB + AKShare）
│   ├── data_futures_fundamental.py   # 期货基本面数据（库存/仓单/基差）
│   ├── llm.py                        # LLM 客户端（OpenAI/Anthropic/Mock）
│   ├── config/                       # 配置系统
│   │   ├── __init__.py
│   │   └── settings.py               # FTSConfig + get_config()
│   ├── core/                         # 核心契约层
│   │   ├── __init__.py
│   │   ├── atomic.py                 # 原子文件操作
│   │   ├── contracts.py              # TypedDict 契约重导出
│   │   └── enums.py                  # 枚举定义
│   ├── factor_engine/                # 因子引擎（核心模块，19 文件）
│   │   ├── __init__.py               # 模块入口 + 所有子模块重导出
│   │   ├── contracts.py              # 完整 TypedDict 契约（L1+L2+L3）
│   │   ├── evolution_loop.py         # L2 主循环编排器
│   │   ├── meta_loop.py              # L1 Meta-Loop
│   │   ├── portfolio_loop.py         # L3 Portfolio Loop
│   │   ├── seed_pool.py              # 种子池管理器（482 个因子）
│   │   ├── seed_data/                # 种子因子定义库
│   │   │   ├── __init__.py           # 统一导出入口
│   │   │   ├── wq101.py              # 101 个 WorldQuant Alpha 因子
│   │   │   ├── qlib158.py            # 158 个 Qlib 因子
│   │   │   ├── gtja191.py            # 191 个国泰君安 Alpha 因子
│   │   │   ├── fundamental_seeds.py  # 23 个基本面/另类/宏观因子
│   │   │   ├── alpha_ops.py          # 公共操作库
│   │   │   └── loader.py             # 动态加载器：因子定义 → FactorProgram
│   │   ├── seed_data_futures.py      # 期货种子因子（25 核心品种）
│   │   ├── seed_data_futures_full.py # 期货种子因子（全量 50+ 因子）
│   │   ├── factor_program.py         # 因子程序（安全沙箱）
│   │   ├── macro_evolution.py        # LLM 宏观演化
│   │   ├── micro_evolution.py        # optuna 微观调参
│   │   ├── evaluation_chain.py       # 三级评估链
│   │   ├── experience_chain.py       # 经验链存储
│   │   ├── verifier.py               # Verifier 锁定协议
│   │   ├── state.py                  # 演化状态管理 + trace_id 生成
│   │   ├── program.py                # L0 Program.md 解析
│   │   ├── walk_forward.py           # 走航验证
│   │   ├── cost_model.py             # 交易成本模型
│   │   ├── regime.py                 # 市场制度检测
│   │   ├── stress_test.py            # 压力测试
│   │   └── monitor.py                # 循环监控
│   ├── pipeline/                     # 因子推演管线
│   │   ├── __init__.py
│   │   ├── base.py                   # FactorPipeline 抽象基类
│   │   └── factor_combiner.py        # 因子组合器
│   ├── strategies/                   # 策略层
│   │   ├── rules/                    # 策略规则知识库（占位）
│   │   │   └── __init__.py
│   │   ├── __init__.py
│   │   ├── base_v2.py                # BaseStrategyV2 + ScoredSignal
│   │   ├── multi_factor_strategy.py  # 12 因子多策略（3 种模式）
│   │   └── strategy_evolution.py     # 策略进化（动态权重/制度自适应/多周期融合）
│   ├── scheduler/                    # 调度层
│   │   ├── __init__.py               # 模块入口
│   │   ├── engine.py                 # SchedulerEngine（APScheduler 包装器）
│   │   ├── tasks.py                  # TaskRegistry + 5 个默认任务
│   │   ├── jobs.py                   # 任务工作函数
│   │   ├── watchdog.py               # ProcessWatchdog 进程看门狗
│   │   └── hotswap.py                # HotSwapWatcher 热重载
│   └── monitor/                      # 健康监控
│       ├── __init__.py               # 状态报告函数
│       ├── http_server.py            # HTTP 监控端点 + Web UI 仪表盘
│       └── elite_tracker.py          # Elite 因子追踪
├── agents/                           # Agent 角色定义
│   └── fts-agent.md                  # FTS 开发代理职责与能力边界
├── tests/                            # 40+ 个测试文件，1601 全部通过
│   ├── core/                         # 3 个文件
│   ├── factor_engine/                # 16 个文件
│   ├── pipeline/                     # 2 个文件
│   ├── scheduler/                    # 4 个文件
│   ├── strategies/                   # 3 个文件
│   ├── monitor/                      # 1 个文件
│   └── 顶层测试                      # 10 个文件
├── config/
│   └── settings.yaml                 # YAML 配置示例
├── docs/
│   ├── harness/                      # HARNESS 工程文档（9 个文件）
│   ├── deploy/                       # 部署文档
│   ├── factor_data_dict/             # 因子数据字典
│   ├── templates/                    # 模板文件
│   └── *.md                          # 业务文档
├── scripts/                          # 辅助脚本
│   ├── download_futures.py           # 期货数据下载（断点续传）
│   ├── daily_signal_pipeline.py      # 每日信号管道
│   ├── futures_signal_pipeline.py    # 期货横截面信号管道
│   ├── futures_strategy.py           # 期货策略
│   ├── futures_l3_portfolio.py       # 期货 L3 组合
│   ├── run_futures_evolution.py      # 期货演化运行器
│   ├── portfolio_analysis.py         # 组合分析
│   ├── portfolio_backtest.py         # 组合回测
│   ├── analyze_elite.py              # Elite 因子分析
│   ├── build_fundamental_cache.py    # 基本面缓存构建
│   ├── verify_doc_consistency.py     # 文档一致性校验
│   ├── verify_gtja191.py             # 国泰君安 191 验证
│   ├── verify_loader.py              # 加载器验证
│   └── verify_seed_data.py           # 种子数据验证
├── reports/                          # 信号报告输出目录（按日期）
├── data/                             # DuckDB 数据文件 + MCP 缓存
├── memory/                           # 运行时持久化（自动创建）
│   ├── evolution/                    # L2 演化状态
│   ├── meta_loop/                    # L1 元循环状态
│   ├── portfolio/                    # L3 组合状态
│   └── knowledge/factors/            # 因子知识库
│       ├── elite/                    # 精英因子
│       └── l1_injected/              # L1 注入因子
├── .github/workflows/ci.yml          # GitHub Actions CI
├── pyproject.toml                    # 项目元数据 + 依赖
├── CLAUDE.md                         # AI 编码行为准则
├── CODE_WIKI.md                      # 本文件
├── README.md                         # 项目概览
└── start_fts.ps1                     # PowerShell 启动脚本
```

### 3.2 模块职责一览

| 模块 | 主要文件 | 职责 |
|:-----|:---------|:-----|
| `fts.cli` | `cli.py` | 统一 CLI 入口：version / monitor / evolution / meta-loop / portfolio / factor / scheduler / ui |
| `fts.config` | `settings.py` | 配置加载（YAML → 环境变量 → 默认值 三级优先级）；`FTSConfig` dataclass |
| `fts.core` | `atomic.py`, `contracts.py`, `enums.py` | 原子文件操作、契约重导出、枚举定义 |
| `fts.data` | `data.py` | `FTSDataProvider`：统一数据入口，包装 MCP + 期货 + 基本面数据提供者，多级降级 |
| `fts.data_mcp` | `data_mcp.py` | MCP 数据适配层，基于 akshare 获取 A 股/ETF OHLCV |
| `fts.data_mcp_bridge` | `data_mcp_bridge.py` | MCP 数据桥接层：从本地缓存读取 Agent 预填充的基本面数据，含 MX API 响应解析 |
| `fts.data_fundamental` | `data_fundamental.py` | 基本面数据注入（pe_ttm, pb, market_cap 等），MCP 缓存 → 合成降级 |
| `fts.data_futures` | `data_futures.py` | 期货数据提供者（DuckDB kline_cache + AKShare 降级 + 合成数据），82 个品种 |
| `fts.data_futures_fundamental` | `data_futures_fundamental.py` | 期货基本面数据（库存/仓单/基差） |
| `fts.llm` | `llm.py` | LLM 客户端层次：`LLMClient`(ABC) → `OpenAIClient` / `AnthropicClient` / `MockLLMClient` |
| `fts.factor_engine` | 19 个文件 | **核心引擎**：L1/L2/L3 三层循环、契约、评估、Verifier、沙箱、种子、经验链 |
| `fts.pipeline` | `base.py`, `factor_combiner.py` | 因子推演管线框架：`ProcessingStage` Protocol + `FactorPipeline` ABC + `FactorCombiner` |
| `fts.strategies` | `base_v2.py`, `multi_factor_strategy.py`, `strategy_evolution.py` | v2 可插拔策略框架 + 12 因子多策略 + 策略进化 |
| `fts.scheduler` | `engine.py`, `tasks.py`, `jobs.py`, `watchdog.py`, `hotswap.py` | APScheduler 调度引擎 + 5 个默认任务 + 看门狗 + 热重载 |
| `fts.monitor` | `__init__.py`, `http_server.py`, `elite_tracker.py` | 健康监控 + HTTP 端点 + Web UI 仪表盘 + Elite 因子追踪 |

---

## 4. 关键类与函数

### 4.1 `fts.cli` — 统一命令行入口

| 函数 | 用途 |
|:-----|:-----|
| `main(argv=None)` | CLI 入口点，解析参数并分发到子命令处理器 |
| `build_parser()` | 构建 `ArgumentParser`，注册所有子命令 |
| `_cmd_version(_args)` | 打印版本号 + 引擎版本 + 配置路径 |
| `_cmd_monitor(args)` | 检查 L1/L2/L3 健康状态，支持 `--json` |
| `_cmd_evolution_run(args)` | 启动 L2 因子演化：支持 `--universe single/csi300/futures` |
| `_cmd_meta_loop_run(args)` | 启动 L1 Meta-Loop（市场感知 + Bootstrapping） |
| `_cmd_portfolio_run(args)` | 启动 L3 组合构建，完成后自动触发期货信号管道 |
| `_cmd_ui(args)` | 启动 Web UI 仪表盘（127.0.0.1:9100） |
| `_cmd_scheduler_run(_args)` | 启动 APScheduler 后台运行 |
| `_cmd_scheduler_list(_args)` | 列出所有已注册调度任务 |
| `_cmd_factor_list(args)` | 列出 elite 目录中的因子 |
| `_cmd_factor_show(args)` | 查看单个因子 JSON 详情 |
| `_prepare_data(symbol, days)` | 准备单标 OHLCV 数据 + 前向收益 |
| `_prepare_cross_section_data(universe, days, max_stocks)` | 准备横截面面板数据（csi300 模式，含基本面注入） |
| `_prepare_futures_data(days, max_symbols)` | 准备期货横截面面板数据 |

**CLI 命令树：**

```
fts
├── version              # 打印版本
├── monitor [--json]     # 健康监控
├── evolution run        # L2 因子演化
│   ├── --max-generations (默认 10)
│   ├── --symbol (默认 000001)
│   ├── --universe single/csi300/futures
│   └── --max-stocks (默认 50)
├── meta-loop run        # L1 市场感知
├── portfolio run        # L3 组合构建
│   └── --universe stock/futures
├── ui                   # Web UI 仪表盘
│   ├── --host (默认 127.0.0.1)
│   └── --port (默认 9100)
├── scheduler            # 任务调度
│   ├── run              # 启动后台调度
│   └── list             # 列出任务
└── factor               # 因子管理
    ├── list [--elite-dir]
    └── show <factor_id> [--elite-dir]
```

### 4.2 `fts.config.settings` — 配置系统

**配置加载优先级：** 环境变量 (`FTS_*`) > YAML 配置文件 > 代码默认值

| 名称 | 类型 | 说明 |
|:-----|:-----|:-----|
| `FTSConfig` | `@dataclass` | 全局配置容器 |
| `load_config(config_path)` | 函数 | 加载 YAML → 应用环境变量覆盖 |
| `get_config()` | 函数 | 惰性单例访问器 |

**`FTSConfig` 关键字段：**

| 字段 | 默认值 | 环境变量 |
|:-----|:-------|:---------|
| `memory_dir` | `"memory"` | `FTS_MEMORY_DIR` |
| `elite_dir` | `"memory/knowledge/factors/elite"` | `FTS_ELITE_DIR` |
| `default_market` | `"stock"` | `FTS_DEFAULT_MARKET` |
| `llm_backend` | `""` (自动检测) | `FTS_LLM_BACKEND` |
| `max_generations` | `10` | — |
| `micro_trials_per_generation` | `50` | — |
| `max_workers` | `4` | `FTS_MAX_WORKERS` |
| `portfolio_max_factors` | `20` | — |
| `log_level` | `"INFO"` | `FTS_LOG_LEVEL` |

### 4.3 `fts.core` — 核心契约层

#### `atomic.py` — 原子文件操作

| 函数 | 说明 |
|:-----|:-----|
| `atomic_write(path, data, make_dir=True, encoding="utf-8")` | 临时文件 + `os.replace()` 原子写入 JSON |
| `atomic_read(path, default=None, encoding="utf-8")` | 安全读取 JSON，失败返回 default |
| `atomic_write_state(path, state, backup_count=3)` | 原子写入 + 备份轮转（`.bak.0` → `.bak.1` → `.bak.2`） |

#### `enums.py` — 枚举定义

| 枚举 | 值 |
|:-----|:---|
| `EvolutionStage` | `L0_HUMAN`, `L1_META_LOOP`, `L2_EVOLUTION`, `L3_PORTFOLIO` |
| `FactorPriority` | `HIGH`, `MEDIUM`, `LOW` |
| `FactorStatus` | `PENDING`, `INJECTED`, `DECAYED`, `REJECTED` |

#### `contracts.py` — 从 `factor_engine.contracts` 重导出所有 TypedDict

### 4.4 `fts.data` — 数据层

#### FTSDataProvider（统一数据入口）

| 方法 | 说明 |
|:-----|:-----|
| `get_ohlcv(symbol, days, adjust, trace_id, fundamental)` | 获取 A 股/ETF OHLCV K 线数据（MCP → 合成降级） |
| `get_etf_ohlcv(symbol, days, adjust, trace_id)` | 获取 ETF OHLCV 数据 |
| `get_csi300_panel(days, max_stocks, trace_id, fundamental)` | 沪深 300 成分股面板数据 |
| `get_etf_panel(days, trace_id)` | 常见 ETF 面板数据 |
| `get_futures_ohlcv(symbol, days, trace_id)` | 期货连续合约 OHLCV（DuckDB → AKShare → 合成降级） |
| `get_futures_panel(symbols, days, trace_id)` | 期货多品种面板数据（common_dates 多数对齐） |
| `enrich_with_fundamental(df, symbol, trace_id)` | 基本面字段注入（pe_ttm, pb, market_cap 等） |
| `enrich_futures_fundamental(df, symbol, trace_id)` | 期货基本面注入（库存/仓单/基差） |
| `search_symbol(query, limit)` | 搜索股票/ETF 代码 |
| `synthesize_ohlcv(n_days, base_price, seed)` | 合成 OHLCV 数据（降级回退） |

#### MCPBridge（数据桥接层）

| 方法 | 说明 |
|:-----|:-----|
| `get_fundamental(symbol)` | 从本地缓存获取单只股票基本面数据 |
| `get_batch(symbols)` | 批量获取多只股票基本面数据 |
| `cache_size` (property) | 缓存中的股票数量 |
| `cache_stocks` (property) | 缓存中的所有股票代码 |
| `get_cache_age_hours()` | 缓存年龄（小时） |

**辅助函数：** `save_cache()` 保存缓存数据，`_parse_mx_response()` 解析 MX API 响应为结构化缓存。

#### FuturesDataProvider（期货数据提供者）

| 方法 | 说明 |
|:-----|:-----|
| `get_ohlcv(symbol, days, trace_id)` | 获取期货连续合约 OHLCV（DuckDB kline_cache 优先） |
| `get_futures_panel(symbols, days, trace_id)` | 多品种面板数据，common_dates 多数对齐 |
| `get_dominant_contracts(date)` | 获取主力合约代码（按最大成交量判定） |

#### FuturesFundamentalProvider（期货基本面）

| 方法 | 说明 |
|:-----|:-----|
| `get_inventory(symbol)` | 获取库存数据（AKShare → 合成降级） |
| `get_basis(symbol, days)` | 获取基差数据（现货价格 + 近月/主力基差） |
| `get_warrant(symbol)` | 获取仓单数据 |

### 4.5 `fts.llm` — LLM 客户端

**LLM 客户端层次结构：**

```
LLMClient (ABC)
├── OpenAIClient      ← 默认后端（OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL）
├── AnthropicClient   ← 替代后端（ANTHROPIC_API_KEY）
└── MockLLMClient     ← 回退（无 LLM 依赖时使用，确定性输出）
```

| 类/函数 | 说明 |
|:-----|:-----|
| `LLMError` | LLM 调用失败异常 |
| `LLMCallRecord` | 单次调用记录：`prompt`, `response`, `model`, `tokens_in/out`, `duration_ms`, `trace_id` |
| `LLMClient` (ABC) | 抽象基类：`complete(prompt, max_tokens)` → `(response, tokens_out)` |
| `OpenAIClient` | OpenAI 兼容 API（支持 DeepSeek 等代理） |
| `AnthropicClient` | Anthropic Claude API |
| `MockLLMClient` | 确定性 mock，返回预设模板内容 |
| `get_llm_client()` | 自动检测：`LLM_BACKEND` env → OpenAI → Anthropic → Mock |
| `get_default_llm_client()` | 便捷函数，返回检测到的 LLM 客户端 |

### 4.6 `fts.factor_engine` — 核心引擎

#### 4.6.1 `contracts.py` — TypedDict 契约（L1+L2+L3）

**L2 核心契约：**

| TypedDict | 关键字段 |
|:-----|:-----|
| `FactorProgram` | `factor_id` (fct_\<8hex\>), `name`, `code`, `params`, `signature`, `economic_logic`, `source`, `parent_id`, `generation`, `trace_id` |
| `FactorSignature` | `input_fields`, `output_type` (signal/score), `frequency`, `lookback` |
| `EconomicLogic` | `theory` (0-5), `behavioral` (0-5), `microstructure` (0-5), `institutional` (0-5), `narrative` |
| `BacktestMetrics` | `ic`, `icir`, `sharpe`, `max_drawdown`, `monotonicity`, `oos_ratio`, `t_stat`, `turnover_monthly` |
| `FactorEvaluation` | `factor_id`, `level_1_backtest`, `level_2_economic`, `level_3_multiple`, `walk_forward`, `passed`, `failure_reasons` |
| `ExperienceTrace` | `trace_id`, `factor_id`, `parent_id`, `generation`, `mutation_type`, `mutation_summary`, `evaluation`, `success`, `lessons` |
| `EvolutionState` | `run_id`, `last_generation`, `total_factors_evaluated/promoted`, `tokens_consumed`, `status` |
| `VerifierConfig` | `min_ic` (0.03), `min_sharpe` (1.5), `min_economic_score` (3), `min_t_stat` (3.0), `max_fdr` (0.05) |
| `BudgetConfig` | `nightly_token_limit` (200K), `max_generation` (50), `circuit_breaker_token_ratio` (2.0), `circuit_breaker_consecutive_low_ic` (3) |

**L1 Meta-Loop 契约：**

| TypedDict | 关键字段 |
|:-----|:-----|
| `SeedCandidate` | `candidate_id` (cand_\<8hex\>), `name`, `code`, `economic_logic`, `source`, `is_executable`, `passed_l1_verifier` |
| `L1MetaLoopState` | `run_id`, `last_bootstrap_topic`, `total_candidates_generated/injected`, `status` |
| `FactorPool` | `version`, `factors` (list[FactorPoolEntry]), `total_count`, `pending_count` |
| `L1VerifierConfig` | `min_economic_score` (2/4), `require_executable`, `require_not_duplicate` |
| `L1BudgetConfig` | `daily_token_limit` (50K), `max_bootstraps_per_run` (5) |

**L3 Portfolio Loop 契约：**

| TypedDict | 关键字段 |
|:-----|:-----|
| `PortfolioSignal` | `factor_id`, `weight`, `sharpe`, `ic`, `decay_6m`, `orthogonalized`, `retained` |
| `PortfolioCombo` | `combo_id`, `synthesis_mode`, `signals`, `combo_sharpe`, `max_correlation`, `n_factors` |
| `L3VerifierConfig` | `min_sharpe` (2.0), `max_correlation` (0.3), `max_turnover` (0.50), `max_decay_rate` (0.30) |

#### 4.6.2 `evolution_loop.py` — L2 主循环

| 类/函数 | 说明 |
|:-----|:-----|
| `EvolutionRunResult` | 演化运行结果：`run_id`, `trace_id`, `generations_completed`, `total_factors_evaluated/promoted`, `tokens_consumed`, `status`, `elite_factor_ids` |
| `EvolutionLoop` | L2 主编排器，每代执行 6 步流水线 |
| `__init__(data, forward_returns, elite_dir, llm_client, seed_pool, verifier, n_trials_micro, cross_section_data, cross_section_dates)` | 初始化，支持单标/横截面模式 |
| `run(max_generation)` | 执行演化：种子评估 → macro → micro → eval → verify → experience → save |
| `_check_circuit_breaker()` | 三阈值熔断检测 |
| `_evaluate_cross_section(factor, trace_id)` | 横截面模式评估（截面 IC + 夏普） |
| `_promote_to_elite(factor, evaluation)` | 写入 elite JSON |

**演化模式：**

| 模式 | 命令 | 说明 |
|:-----|:-----|:-----|
| 单标演化 | `fts evolution run` | 单只股票/单个品种的因子演化 |
| 横截面（股票） | `fts evolution run --universe csi300` | 沪深 300 成分股横截面因子演化 |
| 横截面（期货） | `fts evolution run --universe futures` | 期货跨品种横截面因子演化 |

#### 4.6.3 `meta_loop.py` — L1 Meta-Loop

| 类/函数 | 说明 |
|:-----|:-----|
| `MetaRunResult` | L1 运行结果：`run_id`, `status`, `injected_candidate_ids` |
| `MetaLoop` | L1 编排器：每日 08:30 执行 5 步流程 |
| `L1Verifier` | 锁定 Verifier：`min_economic_score>=2/4` + `executable` + `not_duplicate` |
| `MetaStateManager` | L1 状态管理：`state.json` + 备份镜像 |
| `FactorPoolManager` | 因子池管理：`factor_pool.json` 读写、注入、优先级排序 |
| `DebateQualityAnalyzer` | 辩论质量分析：识别 `bullish_weak`/`bearish_weak`/`insufficient_rounds` |
| `BootstrappingChain` | 种子候选生成链：提取 Agent → 验证 Agent → 代码生成 Agent |

#### 4.6.4 `portfolio_loop.py` — L3 Portfolio Loop

| 类/函数 | 说明 |
|:-----|:-----|
| `PortfolioRunResult` | 组合运行结果：`run_id`, `status`, `n_factors_retained`, `combo_sharpe` |
| `PortfolioLoop` | L3 编排器：每日 20:00 运行 |
| `L3Verifier` | 锁定 Verifier：`combo_sharpe>=2.0`, `max_correlation<=0.3`, `decay_6m<=0.30` |
| `load_elite_factors()` | 从 `elite_dir` 读取所有 elite 因子 |
| `orthogonalize_factors(factors)` | QR 正交化，剔除相关性 > 0.7 的因子 |
| `decay_test(factor)` | 6 个月滚动窗口衰减检验 |
| `synthesize_signals(factors)` | 信号合成（等权/夏普加权/Ridge 回归） |
| `build_combo(factors)` | 构建最终组合 |
| `generate_agent_proposals(combo)` | LLM 生成组合优化建议 |
| `inject_to_fdt(combo)` | 输出到 FDT 下游 |

#### 4.6.5 `seed_pool.py` + `seed_data/` — 种子因子池

**482 个种子因子：**

| 类别 | 数量 | 来源文件 |
|:-----|:-----|:-----|
| 内置股票/期货因子 | 9 | `seed_pool.py` 内联代码 |
| WorldQuant 101 Alpha | 101 | `seed_data/wq101.py` |
| Qlib 158 | 158 | `seed_data/qlib158.py` |
| 国泰君安 191 Alpha | 191 | `seed_data/gtja191.py` |
| 基本面/另类/宏观 | 23 | `seed_data/fundamental_seeds.py` |

**期货专用模式（50+ 因子）：**

| 因子家族 | 因子数 | 说明 |
|:-----|:-----|:-----|
| 动量 | 6 | 时间序列动量、截面动量、多周期动量等 |
| 期限结构 | 5 | 基差、基差率、展期收益率等 |
| 持仓 | 5 | 持仓量变化、持仓量分位、持仓结构等 |
| 流动性 | 4 | 换手率、波动率调整成交量等 |
| 高阶矩 | 4 | 偏度、峰度、尾部风险等 |
| 波动率 | 5 | 实现波动率、波动率回归、波动率突破等 |
| 基本面 | 5 | 库存、仓单、基差、开工率等 |
| 拥挤度 | 4 | 持仓集中度、换手拥挤等 |
| Alpha | 4 | 异常收益率、Alpha 动量等 |
| 高频 | 3 | 已实现波动率、日内偏度等 |
| 期权隐含 | 2 | 隐含波动率、偏度指数 |
| 市场环境 | 3 | 市场制度、跨资产相关性等 |

**SeedPool 关键方法：**

| 方法 | 说明 |
|:-----|:-----|
| `load_all_seeds(include_external=True)` | 加载全部种子因子（stock 模式 482 个 / futures 模式 50+ 个） |
| `get_seed(name)` | 按名称获取种子因子 |
| `count()` | 返回种子因子总数 |
| `inject_from_l1(candidate, trace_id)` | L1 注入接口：将候选因子注入种子池 |
| `list_injected_l1()` | 列出所有从 L1 注入的因子 |

#### 4.6.6 `factor_program.py` — 安全沙箱

| 类/函数 | 说明 |
|:-----|:-----|
| `FactorCompileError` | 因子编译失败异常 |
| `validate_factor_code(code)` | AST 预验证，拒绝禁止的模式 |
| `create_factor_program(name, code, params, signature, economic_logic, source, parent_id, generation, trace_id)` | 创建 `FactorProgram` 实例 |
| `generate_factor_id(code)` | 基于代码 SHA1 返回 `fct_<sha1[:8]>` |
| `FactorExecutor` | 受限 `__builtins__` + `_safe_import` 的安全沙箱执行器 |

**安全检查：**
- 白名单导入：`numpy`, `pandas`, `scipy`, `statsmodels`, `talib`, `math`, `statistics`
- 黑名单名称：`open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals`
- 黑名单模块：`os`, `sys`, `subprocess`, `socket`, `ctypes`, `pickle`

#### 4.6.7 `macro_evolution.py` — 宏观演化

| 类/函数 | 说明 |
|:-----|:-----|
| `MacroEvolver` | LLM 驱动的因子代码编辑器 |
| `evolve(parent, generation, trace_id)` | 构建 prompt（父因子 + 经验链上下文）→ 调用 LLM → 返回新因子 |
| `_validate_mutation(original, mutated)` | 验证变异后的代码可编译 |

#### 4.6.8 `micro_evolution.py` — 微观演化

| 类/函数 | 说明 |
|:-----|:-----|
| `evolve_micro(factor, data, forward_returns, n_trials)` | optuna TPE 贝叶斯调参，早停（20 次连续无改进） |
| `optimize_params(factor, data, forward_returns, n_trials)` | 参数空间搜索 + 目标函数最大化 |
| `_suggest_param(trial, key, value)` | 从默认值推断参数空间（int→range, float→range, bool→categorical） |

#### 4.6.9 `evaluation_chain.py` — 三级评估链

| 类/函数 | 说明 |
|:-----|:-----|
| `EvaluationChain` | 三级评估编排器 |
| `evaluate(factor, data, forward_returns)` | Level 1: IC / ICIR / Sharpe / max_drawdown / monotonicity / t_stat |
| `evaluate_economic_logic(factor)` | Level 2: 四维经济学评分（theory/behavioral/microstructure/institutional, 0-5/维） |
| `evaluate_multiple_tests(factors, evaluations)` | Level 3: Bonferroni + FDR + PCA-based effective_n |
| `cross_section_evaluate_backtest(factor, panel, dates)` | 横截面模式专用评估（截面 IC + 夏普） |

#### 4.6.10 `verifier.py` — Verifier 锁定协议

| 类/函数 | 说明 |
|:-----|:-----|
| `FactorVerifier` | 核心 Verifier：`__init__` 后立即 `_locked=True` |
| `check(evaluation)` | 严格按 `VerifierConfig` 逐项比较，返回 `VerifierResult` |
| `get_global_verifier()` | 进程级单例（DEFAULT_VERIFIER_CONFIG） |
| `reset_global_verifier()` | 重置单例（仅测试用） |

#### 4.6.11 其他辅助模块

| 模块 | 说明 |
|:-----|:-----|
| `state.py` | `EvolutionStateManager`：状态持久化 + `generate_trace_id()` / `generate_run_id()` |
| `experience_chain.py` | `ExperienceChain`：成功/失败经验追踪，MAX_CHAIN_SIZE=100 |
| `program.py` | L0 Program.md 解析器：`parse_program_md()`, `load_program()`, `init_program()` |
| `walk_forward.py` | `WalkForwardOptimizer`：滚动窗口验证（window_years=3, step_months=6） |
| `cost_model.py` | `TransactionCostModel`：按市场配置交易成本 |
| `regime.py` | `RegimeAwareSelector`：牛/熊/震荡/高波/低波制度检测 |
| `stress_test.py` | `StressTester`：5 个历史压力场景测试 |
| `monitor.py` | `check_loop()`, `check_all()`：三层循环状态监控 |

### 4.7 `fts.pipeline` — 因子推演管线

| 类/函数 | 说明 |
|:-----|:-----|
| `DataPayload` | 管线数据载体：`data_type`, `symbol`, `payload`, `metadata`, `trace_id` |
| `ProcessingStage` (Protocol) | Stage 协议：`input_type`/`output_type` + `process(payload)` |
| `FactorPipeline` (ABC) | 管线抽象：`build_stages()` (抽象) + `run()` (编排器) |
| `PipelineResult` | 管线运行结果：`success`, `final_payload`, `stage_meta`, `trace_id` |
| `FactorCombiner` | 因子组合器：z-score 归一化 → 可选 QR 正交化 → 加权融合 |
| `CombinerConfig` | 组合配置：`weights`, `normalize_inputs`, `clip_sigma`, `orthogonalize` |

### 4.8 `fts.strategies` — 策略层

| 类/函数 | 说明 |
|:-----|:-----|
| `RawSignal` | 原始信号：`symbol`, `direction`, `signal_type`, `raw_score` |
| `ScoredSignal` | 打分信号：`total`, `abs_score`, `grade` (STRONG/WATCH/WEAK/NOISE)，含技术指标字段 |
| `BaseStrategyV2` (ABC) | v2 策略基类：`name`(抽象), `score()`(抽象), `compute()`, `filter()`, `validators`, `weight` |
| `StrategyV1Adapter` | v1→v2 桥接适配器 |
| `MultiFactorStrategy` | 12 因子多策略：`FACTOR_WEIGHTS` + `PURE_MOMENTUM_WEIGHTS`，3 种模式 |
| `strategy_evolution.py` | 策略进化：`RegimeAdaptiveStrategy` / `DynamicWeightStrategy` / `MultiPeriodSignalFusion` |

### 4.9 `fts.scheduler` — 调度层

| 类/函数 | 说明 |
|:-----|:-----|
| `TaskSpec` | 任务规格：`name`, `cron_expression`, `callable_path`, `description`, `enabled`, `trace_id_prefix` |
| `TaskRegistry` | 任务注册表：`register()`, `unregister()`, `list_enabled()` |
| `SchedulerEngine` | APScheduler 包装器：`start(daemon)`, `stop()`，APScheduler 不可用时静默降级 |
| `register_default_tasks()` | 注册 5 个默认任务（幂等） |
| `ProcessWatchdog` | 进程看门狗：30s 内 3 次重启 → 5min 熔断 |
| `HotSwapWatcher` | 文件变更监听 → `importlib.reload` |

**5 个默认调度任务：**

| 任务名 | cron | 描述 |
|:-----|:-----|:-----|
| `l1_meta_loop` | `30 8 * * *` | 每日 08:30 L1 Meta-Loop |
| `l2_evolution_loop` | `0 23 * * *` | 每日 23:00 L2 因子演化（期货横截面） |
| `l3_portfolio_loop` | `0 20 * * *` | 每日 20:00 L3 组合构建 + 自动触发期货信号管道 |
| `futures_signal_pipeline` | `30 20 * * *` | 每日 20:30 期货信号管道（独立调度） |
| `health_check` | `*/10 * * * *` | 每 10 分钟健康检查 |

**任务工作函数（`jobs.py`）：**

| 函数 | 说明 |
|:-----|:-----|
| `l1_meta_loop_job()` | L1 Meta-Loop 入口：创建 MetaLoop 实例并执行 |
| `l2_evolution_loop_job()` | L2 演化入口：准备期货横截面数据 → 创建 EvolutionLoop 并执行 |
| `l3_portfolio_loop_job()` | L3 组合入口：执行 PortfolioLoop → 完成后自动触发期货信号管道 |
| `futures_signal_pipeline_job()` | 独立期货信号管道入口 |
| `health_check_job()` | 健康检查入口：调用 `check_all_status()` |

### 4.10 `fts.monitor` — 健康监控

| 类/函数 | 说明 |
|:-----|:-----|
| `LoopStatusReport` | 单循环状态报告：`loop_name`, `healthy`, `status`, `last_error`, `run_id`, `age_hours` |
| `SystemStatusReport` | 系统整体状态：`healthy`, `loops`, `any_circuit_broken`, `any_stale`, `total_tokens_today` |
| `check_loop_status(name)` | 检查单循环状态 |
| `check_all_status()` | 检查所有循环，返回 `SystemStatusReport` |
| `format_status_report(report)` | 格式化人类可读报告 |
| `status_report_to_json(report)` | 序列化 JSON 报告 |
| `FTSDashboardServer` | HTTP 监控服务器（127.0.0.1:9100），纯标准库实现 |
| `EliteFactorTracker` | Elite 因子追踪：`update()`, `get_decaying()`, `auto_retire()` |
| `AutoRetireManager` | 基于 `cooldown_days` 的自动退役管理器 |

**HTTP 端点：**

| 端点 | 方法 | 说明 |
|:-----|:-----|:-----|
| `/` | GET | 现代仪表盘 HTML（零依赖单页应用） |
| `/api/status` | GET | 系统状态 JSON |
| `/api/factors` | GET | Elite 因子列表 JSON |
| `/health` | GET | 健康检查 JSON |

---

## 5. 模块间依赖关系

### 5.1 全局依赖图

```
fts.cli (顶层编排器)
  ├── fts.config.settings          (get_config)
  ├── fts.data                     (FTSDataProvider)
  ├── fts.llm                      (get_default_llm_client)
  ├── fts.factor_engine.*          (所有循环 + 契约)
  ├── fts.monitor                  (check_all_status, FTSDashboardServer)
  └── fts.scheduler                (SchedulerEngine, list_tasks)

fts.factor_engine.evolution_loop (L2)
  ├── fts.factor_engine.contracts
  ├── fts.factor_engine.evaluation_chain
  ├── fts.factor_engine.macro_evolution
  ├── fts.factor_engine.micro_evolution
  ├── fts.factor_engine.verifier
  ├── fts.factor_engine.state
  ├── fts.factor_engine.experience_chain
  └── fts.factor_engine.seed_pool

fts.factor_engine.meta_loop (L1)
  ├── fts.factor_engine.contracts
  ├── fts.factor_engine.seed_pool
  └── fts.factor_engine.state

fts.factor_engine.portfolio_loop (L3)
  ├── fts.factor_engine.contracts
  ├── fts.factor_engine.state
  └── fts.factor_engine.verifier

fts.data (数据层)
  ├── fts.data_mcp                 (MCPDataProvider)
  ├── fts.data_mcp_bridge          (MCPBridge)
  ├── fts.data_fundamental         (FundamentalProvider)
  ├── fts.data_futures             (FuturesDataProvider)
  └── fts.data_futures_fundamental (FuturesFundamentalProvider)

fts.core.contracts → fts.factor_engine.contracts (重导出)
```

### 5.2 依赖规则

- **`fts.core`** 是基础层 — 不依赖上层模块
- **`fts.factor_engine.contracts`** 是 TypedDict 单一真源；`fts.core.contracts` 仅重导出
- **`fts.cli`** 是顶层编排器，依赖所有子系统，但不参与业务逻辑
- **`fts.factor_engine`** 有内部子依赖，但不依赖 `fts.pipeline`, `fts.strategies`, `fts.scheduler`
- **`fts.scheduler`** 完全解耦 — 仅依赖 stdlib + 可选 `apscheduler`/`watchdog`

### 5.3 数据流方向

```
MCP/akshare + DuckDB → fts.data → fts.factor_engine → 信号输出
                             ↕
                       fts.pipeline
                             ↕
                       fts.strategies
                             ↕
                       fts.scheduler
```

---

## 6. 外部依赖

### 6.1 必需依赖

| 库 | 最低版本 | 用途 |
|:---|:---------|:-----|
| `numpy` | >=1.24 | 数值计算（IC/Sharpe/矩阵运算/正交化） |
| `pandas` | >=2.0 | DataFrame / 时间序列 / OHLCV 处理 |
| `scipy` | >=1.10 | 统计检验 |
| `pyyaml` | >=6.0 | YAML 配置解析 |

### 6.2 可选依赖（extras）

| Extra | 库 | 用途 |
|:------|:---|:-----|
| `evolution` | `optuna >= 3.0` | 微观演化 TPE 贝叶斯调参 |
| `llm` | `openai >= 1.0` | OpenAI / DeepSeek 兼容 API |
| `llm` | `anthropic >= 0.20` | Anthropic Claude API |
| `mcp` | `akshare >= 1.18.64` | MCP 数据源（A 股/ETF 行情） |
| `dev` | `pytest >= 7.4` | 测试框架 |
| `dev` | `pytest-cov >= 4.1` | 覆盖率报告 |

### 6.3 隐式/软依赖（静默回退）

| 库 | 用途 | 回退行为 |
|:---|:-----|:---------|
| `duckdb` | 期货数据缓存 | 回退到 AKShare 即时获取 |
| `apscheduler` | 定时任务调度 | `SchedulerEngine.start()` 返回 False |
| `watchdog` | 文件热重载 | `HotSwapWatcher` 静默 no-op |
| `lightgbm` | L3 信号合成 | 回退到等权/夏普加权 |
| `dotenv` | .env 文件加载 | 静默跳过 |

---

## 7. 运行/构建/测试方式

### 7.1 安装

```bash
# 基础安装（仅必需依赖）
pip install -e .

# 开发安装（含测试）
pip install -e ".[dev]"

# 完整安装（含演化 + LLM + 数据）
pip install -e ".[evolution,llm,mcp,dev]"
```

### 7.2 环境变量

```powershell
# .env 文件（自动加载）
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=https://api.deepseek.com/v1   # 可选：使用 DeepSeek 代理
OPENAI_MODEL=deepseek-chat
FTS_MEMORY_DIR=memory
FTS_ELITE_DIR=memory/knowledge/factors/elite
FTS_LLM_BACKEND=          # 留空 = 自动检测
FTS_LOG_LEVEL=INFO
```

### 7.3 CLI 命令

```bash
fts version                                    # 打印版本 + 引擎版本 + 配置路径
fts monitor [--json]                           # 显示 L1/L2/L3 循环状态
fts evolution run [--max-generations 10]       # L2 单标因子演化
fts evolution run --universe csi300            # L2 CSI300 横截面演化
fts evolution run --universe futures           # L2 期货横截面演化
fts meta-loop run                              # L1 市场感知
fts portfolio run                              # L3 组合构建（stock）
fts portfolio run --universe futures           # L3 期货组合构建
fts ui [--host 127.0.0.1] [--port 9100]       # 启动 Web UI 仪表盘
fts scheduler run                              # 启动后台调度
fts scheduler list                             # 列出所有已注册任务
fts factor list [--elite-dir]                  # 列出 elite 因子
fts factor show <factor_id> [--elite-dir]      # 显示因子详情
```

### 7.4 测试

```bash
# 运行所有测试（带覆盖率）
pytest

# 仅运行特定模块
pytest tests/factor_engine/                    # 因子引擎测试（16 文件）
pytest tests/scheduler/                        # 调度器测试（4 文件）
pytest tests/strategies/                       # 策略测试（3 文件）
pytest -k "test_verifier"                      # 按关键字过滤

# 当前测试状态：1601 测试通过，1 跳过，91% 覆盖率
```

### 7.5 CI/CD

`.github/workflows/ci.yml` — GitHub Actions：
- 矩阵：Python 3.10 / 3.11 / 3.12
- 步骤：`pip install -e ".[dev,evolution]"` → `pytest --cov=fts --cov-report=xml`
- 触发：push / pull_request 到 main 分支

---

## 8. 核心设计模式

### 8.1 Verifier 锁定协议

**核心防博弈机制。** `FactorVerifier`, `L1Verifier`, `L3Verifier` 都在 `__init__` 末尾设置 `_locked=True`。任何后续修改配置的尝试抛出 `VerifierAlreadyLockedError`。确保评估标准无法被 LLM（或人类）在运行中博弈。

### 8.2 Loop Engineering 范式

三个自治循环（L1/L2/L3）具有不同的节奏（每日/每夜/每日），由人在回路顶层（L0 Program.md）监督。每个循环有独立的 `StateManager`、`Verifier`、`Budget`。

### 8.3 安全沙箱执行

**禁止 LLM 生成的因子代码执行危险操作。** 白名单导入 + 黑名单名称 + 黑名单模块 + AST 预验证 + 受限 `__builtins__`。

### 8.4 Strategy v2 可插拔框架

`BaseStrategyV2` ABC 定义 `name`（抽象），`score()`（抽象），并提供默认 `compute()`, `filter()`, `validators`, `weight`。新策略扩展 ABC 并只覆盖需要的部分。

### 8.5 Pipeline + Stage Protocol

`ProcessingStage` 是 `Protocol`，含 `input_type`/`output_type` 和 `process(payload)` 方法。`FactorPipeline` 是 ABC，含抽象 `build_stages()` 和具体 `run()` 编排器。

### 8.6 适配器模式

`StrategyV1Adapter` 桥接 v1 策略接口到 v2 ABC，允许渐进迁移。

### 8.7 原子文件操作

`atomic_write()` 临时文件 + `os.replace()`（跨平台原子 rename）。`atomic_write_state()` 添加备份轮转（`.bak.0` → `.bak.1` → `.bak.2`），实现崩溃安全。

### 8.8 经验链（LLM 记忆）

`ExperienceChain` 在独立子目录中存储成功和失败追踪。`read_recent_for_llm()` 返回 10 条成功 + 10 条失败追踪作为下次 LLM 调用的 Markdown 上下文。MAX_CHAIN_SIZE=100，满时 FIFO 淘汰。

### 8.9 熔断器（Circuit Breaker）

三阈值自动停止 L2 演化，触发后须人类介入恢复：

| 熔断条件 | 阈值 |
|:-----|:-----|
| Token 预算耗尽 | `nightly_token_limit` × 2.0 |
| 连续低 IC | 3 代 IC < 0.01 |
| 失败率超限 | > 90% |

### 8.10 静默降级

所有可选依赖都惰性导入，缺失时优雅回退到 Mock/No-op 实现。系统在零可选依赖安装的情况下仍可端到端运行。

### 8.11 MCP 数据桥接

`MCPBridge` 实现了 Agent 运行时与 FTS 代码运行时的数据桥接。Agent 通过 `run_mcp` 预填充基本面缓存（`data/fundamental_cache.json`），FTS 代码运行时只读缓存。缓存未命中时返回空字典，由调用方自行降级。

---

## 9. 配置体系

### 9.1 配置层次与优先级

```
高优先级         环境变量 (FTS_* 前缀)
    ↑           YAML 配置文件 (config/settings.yaml)
    ↑           代码默认值 (FTSConfig dataclass)
低优先级
```

### 9.2 配置文件清单

| 文件 | 用途 |
|:-----|:-----|
| `pyproject.toml` | Python 项目构建 + 依赖 + CLI 入口点 |
| `config/settings.yaml` | 默认 YAML 配置 |
| `fts/config/settings.py` | 配置加载器（`FTSConfig` dataclass） |
| `.env` | 环境变量（API Key 等，不提交到 Git） |
| `CLAUDE.md` | AI 编码行为准则 |
| `agents/fts-agent.md` | FTS Agent 角色定义与能力边界 |
| `.github/workflows/ci.yml` | GitHub Actions CI |
| `start_fts.ps1` | PowerShell 启动脚本 |

### 9.3 `pyproject.toml` 关键配置

```toml
[project]
name = "fts"
version = "1.8.0"
requires-python = ">=3.10"
dependencies = ["numpy>=1.24", "pandas>=2.0", "scipy>=1.10", "pyyaml>=6.0"]

[project.optional-dependencies]
evolution = ["optuna>=3.0"]
llm = ["openai>=1.0", "anthropic>=0.20"]
mcp = ["akshare>=1.18.64"]
dev = ["pytest>=7.4", "pytest-cov>=4.1"]

[project.scripts]
fts = "fts.cli:main"
```

---

## 10. 运行时状态文件

### 10.1 状态文件清单

| 文件路径 | 用途 | 拥有者 |
|:-----|:-----|:-----|
| `memory/evolution/state.json` | L2 演化状态 | `EvolutionStateManager` |
| `memory/evolution/state.json.backup` | L2 状态备份 | `atomic_write_state` |
| `memory/meta_loop/state.json` | L1 Meta-Loop 状态 | `MetaStateManager` |
| `memory/meta_loop/state.json.backup` | L1 状态备份 | `atomic_write_state` |
| `memory/portfolio/state.json` | L3 组合状态 | `PortfolioStateManager` |
| `memory/portfolio/current_combo.json` | 当前 L3 组合 | `PortfolioManager` |
| `memory/portfolio/agent_proposals/*.json` | LLM 生成的组合优化提案 | `PortfolioManager` |
| `memory/knowledge/factors/factor_pool.json` | L1 发现的因子池 | `FactorPoolManager` |
| `memory/knowledge/factors/elite/*.json` | 晋升的 elite 因子 | `EvolutionLoop._promote_to_elite()` |
| `memory/knowledge/factors/l1_injected/*.json` | L1 注入的因子候选 | `MetaLoop` |
| `memory/experience/success/*.json` | 成功经验追踪 | `ExperienceChain` |
| `memory/experience/failure/*.json` | 失败经验追踪 | `ExperienceChain` |
| `data/fts_history.duckdb` | 期货数据缓存 | `FuturesDataProvider` |
| `data/fundamental_cache.json` | MCP 基本面数据缓存 | `MCPBridge` |
| `reports/{date}/` | 信号报告输出 | `scripts/futures_signal_pipeline.py` |
| `Program.md`（项目根目录） | L0 周度人工设定 | 人类（由 `program.py` 解析） |

### 10.2 状态文件格式示例

**L2 Evolution State (`memory/evolution/state.json`):**

```json
{
  "run_id": "run_abc12345_20260803T120000",
  "started_at": "2026-08-03T12:00:00",
  "last_generation": 5,
  "total_factors_evaluated": 25,
  "total_factors_promoted": 3,
  "tokens_consumed": 45000,
  "budget_limit": 200000,
  "status": "completed",
  "last_error": null,
  "experience_chain_ref": ["ftr_...", "ftr_..."],
  "last_updated": "2026-08-03T12:30:00",
  "version": "1.1.0"
}
```

**Elite Factor (`memory/knowledge/factors/elite/fct_a1b2c3d4.json`):**

```json
{
  "factor_id": "fct_a1b2c3d4",
  "name": "momentum_v2",
  "code": "def factor_program(data, params):...",
  "params": {"window": 20},
  "signature": {"input_fields": ["close"], "output_type": "signal", "frequency": "daily", "lookback": 20},
  "economic_logic": {"theory": 4, "behavioral": 3, "microstructure": 2, "institutional": 4, "narrative": "..."},
  "source": "macro_evolution",
  "parent_id": "fct_seed_...",
  "generation": 3,
  "trace_id": "ftr_...",
  "created_at": "2026-08-03T12:00:00"
}
```

---

## 总结

FTS 是一个架构清晰的 AI 原生量化因子系统，核心特点：

1. **Contract-First 设计** — 所有数据形状在 `contracts.py` 中声明为 TypedDict（L1+L2+L3 三层），模块间通过契约解耦
2. **安全防博弈** — Verifier 锁定协议 + 安全沙箱 + 熔断器 + 经验链，防止 LLM 作弊和恶意代码执行
3. **工程韧性** — 原子文件持久化 + 备份轮转 + 静默降级 + 进程看门狗 + 热重载
4. **可观测性** — `trace_id` 全链路追踪 + HTTP metrics 服务器 + Web UI 仪表盘 + Elite 因子自动退役追踪
5. **可扩展性** — Strategy v2 可插拔 ABC + Pipeline + Stage Protocol + 适配器模式
6. **多资产覆盖** — A 股/ETF/82 个期货品种，支持期货基本面数据（库存/仓单/基差）
7. **自动化调度** — 5 个 APScheduler 定时任务，覆盖 L1/L2/L3 全链路 + 独立期货信号管道
8. **数据桥接** — MCPBridge 实现 Agent 与 FTS 运行时的数据预填充和只读缓存机制
9. **测试覆盖** — 1601 测试 / 91% 覆盖率 / 40+ 个测试文件 / CI 矩阵（3.10/3.11/3.12）