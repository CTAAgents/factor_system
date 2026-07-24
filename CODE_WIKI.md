# FTS — Factor Intelligence System Code Wiki

> **项目路径**: `d:\Programs\factor_system`
> **版本**: v1.0.0 (factor_engine EVOLUTION_VERSION 8.10.0)
> **Python**: >=3.10
> **代码规模**: ~3,438 语句, 77 个源码+测试文件, 1,231 测试通过, 96% 覆盖率
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
8. [配置体系](#8-配置体系)
9. [运行时状态文件](#9-运行时状态文件)

---

## 1. 项目整体架构

FTS (Factor Intelligence System) 是一个 **AI 原生的量化因子发现、评估、组合与演化引擎**,位于数据流的中间位置:

```
MCP/akshare（腾讯自选股/东方财富 API）← 上游数据源
    ↓
FTS（因子智能系统）← 当前项目（因子发现 → 评估 → 组合 → 信号输出）
    ↓
下游系统（信号消费方）
```

### 1.1 三层循环 (Loop Engineering)

系统采用 **人在回路 (L0) 顶层监督 + 三个自治循环层** 的 Loop Engineering 范式:

| 循环 | 调度时间 | 职责 |
|:-----|:---------|:-----|
| **L0 Human Layer** | 周度人工 | 通过 `Program.md` 设定市场制度、因子偏好、风险约束、预算 |
| **L1 Meta-Loop** | 每日 09:00 | 市场感知、Web 知识补给、Bootstrapping、debate 分析、因子池更新 |
| **L2 Evolution Loop** | 每日 23:00 | 因子演化（LLM 宏观改逻辑 + optuna 微观调参）+ 三级评估链 + Verifier 锁定判定 |
| **L3 Portfolio Loop** | 每周一 06:00 | 组合构建、正交化、衰减检验、信号合成、注入 FDT |

### 1.2 架构数据流

```
Program.md (L0 周度设定)
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  L1 META-LOOP (每日 09:00)                               │
│  ┌─────────────────────────────────────────────────────┐ │
│  │ Step 1: Web 感知 → Data-Core 新闻/市场快照          │ │
│  │ Step 2: Debate 分析 → 读取辩论数据,识别薄弱维度     │ │
│  │ Step 3: Bootstrapping → Agent 链提取/验证/代码生成  │ │
│  │ Step 4: L1 Verifier → 宽松筛选(2/4 维度 + 可执行)   │ │
│  │ Step 5: 注入 factor_pool.json + l1_injected/        │ │
│  └─────────────────────────────────────────────────────┘ │
│                          │                               │
│                          ▼                               │
│  factor_pool.json (种子候选 → L2 种子池)                 │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  L2 EVOLUTION LOOP (每夜 23:00)                          │
│  for generation in 1..MAX_GEN:                           │
│  1. Macro Evolution → LLM 修改因子代码逻辑               │
│  2. Micro Evolution → optuna TPE 调优参数               │
│  3. Evaluation Chain → 3 级评估(回测/经济学/多重检验)    │
│  4. Verifier → 锁定标准判定(pass → elite / fail → 淘汰) │
│  5. Experience Chain → 记录经验(LLM 下一轮参考)         │
│  6. State → 持久化状态,检查熔断器                       │
│                                                          │
│  熔断: token 超限 / 连续低 IC / 失败率超限 → 自动停止   │
└──────────────────────────────────────────────────────────┘
    │
    ▼
  elite/*.json (精英因子库)
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  L3 PORTFOLIO LOOP (每周一 06:00)                        │
│  Step 1: 加载 elite 因子                                │
│  Step 2: QR 正交化 → 剔除高相关性                        │
│  Step 3: 6 个月衰减检验 → 衰减 >30% 剔除                 │
│  Step 4: 信号合成(等权/夏普加权/LightGBM)                │
│  Step 5: 注入下游 → combo.json + Agent 优化建议         │
└──────────────────────────────────────────────────────────┘
    │
    ▼
  FDT（期货交易决策系统）
```

### 1.3 关键架构属性

| 属性 | 说明 |
|:-----|:------|
| **trace_id 全链路** | 所有 CLI 子命令和工作流启动时生成 `{prefix}_{8hex}_{timestamp}`,贯穿所有模块和日志 |
| **Verifier 锁定协议** | 评估配置初始化后锁定,任何运行时修改抛 `VerifierAlreadyLockedError`,防止 LLM 博弈评估标准 |
| **TypedDict 契约优先** | 所有数据形状在 `contracts.py` 中声明为 TypedDict,模块间通过契约解耦 |
| **原子持久化** | `atomic_write()` 临时文件 + `os.replace()` 实现崩溃安全;备份轮转最多保留 3 个 `.bak.*` 文件 |
| **安全沙箱** | 因子代码执行使用白名单导入、黑名单函数名/模块名、AST 预验证,受限 `__builtins__` |
| **静默降级** | 所有可选依赖惰性导入,缺失时自动回退 Mock 实现,零可选依赖仍可端到端运行 |
| **熔断器** | 三阈值自动停止: token 预算耗尽 / 连续低 IC / 失败率超限,触发后须人类介入恢复 |

参考文档:
- [architecture](file:///d:/Programs/factor_system/docs/harness/01-architecture.md) — 详细架构图与层间交互
- [lifecycle](file:///d:/Programs/factor_system/docs/harness/02-lifecycle.md) — 开发生命周期与文件命名规范
- [README](file:///d:/Programs/factor_system/README.md) — 项目概览与 CLI 命令摘要

---

## 2. 模块/包结构

### 2.1 顶层目录布局

```
d:\Programs\factor_system\
├── fts/                           # 主源码包 (~30 个源文件)
│   ├── __init__.py                # __version__ = "1.0.0", 模块 re-export
│   ├── cli.py                     # 409 LOC — 统一 CLI 入口
│   ├── data.py                    # 300+ LOC — FTSDataProvider (Data-Core 适配器)
│   ├── llm.py                     # 235+ LOC — LLM 客户端层次 (OpenAI/Anthropic/Mock)
│   ├── config/                    # 配置系统
│   │   ├── __init__.py
│   │   └── settings.py            # 156 LOC — FTSConfig + load_config() + get_config()
│   ├── core/                      # 基础工具层
│   │   ├── __init__.py
│   │   ├── atomic.py              # 92 LOC — 原子文件写入/读取 + 备份轮转
│   │   ├── contracts.py           # 91 LOC — 从 factor_engine.contracts 重导出
│   │   └── enums.py               # enums — EvolutionStage / FactorPriority / FactorStatus
│   ├── factor_engine/             # 核心引擎模块 (19 个文件, 最大模块)
│   │   ├── __init__.py            # v8.10.0, 所有子模块重导出
│   │   ├── contracts.py           # 560 LOC — 所有 TypedDict 契约 (L1+L2+L3)
│   │   ├── evolution_loop.py      # L2 主循环编排器 (490+ LOC)
│   │   ├── meta_loop.py           # L1 Meta-Loop (500+ LOC)
│   │   ├── portfolio_loop.py      # L3 Portfolio Loop (400+ LOC)
│   │   ├── seed_pool.py           # 15 个内置种子因子
│   │   ├── factor_program.py      # 安全沙箱执行器 + AST 验证
│   │   ├── macro_evolution.py     # LLM 驱动的因子代码演化
│   │   ├── micro_evolution.py     # optuna TPE 贝叶斯调参
│   │   ├── evaluation_chain.py    # 三级评估链 (回测+经济学+多重检验)
│   │   ├── verifier.py            # 锁定 Verifier 协议 (FactoryVerifier)
│   │   ├── state.py               # 状态管理器 + trace_id/run_id 生成
│   │   ├── experience_chain.py    # 经验链存储 (LLM 记忆)
│   │   ├── program.py             # L0 Program.md 解析器
│   │   ├── walk_forward.py        # 走航验证 (Walk-forward OOS)
│   │   ├── cost_model.py          # 交易成本模型
│   │   ├── regime.py              # 市场制度检测 (bull/bear/oscillate/high_vol/low_vol)
│   │   ├── stress_test.py         # 5 个历史压力场景测试
│   │   └── monitor.py             # 三层循环状态监控 (LoopStatus/AllStatus)
│   ├── pipeline/                  # 因子推演管线
│   │   ├── base.py                # DataPayload / ProcessingStage / FactorPipeline ABC
│   │   └── factor_combiner.py     # FactorCombiner (z-score + QR 正交 + 权重融合)
│   ├── strategies/                # v2 可插拔策略框架
│   │   ├── base_v2.py             # BaseStrategyV2 ABC + StrategyV1Adapter
│   │   └── multi_factor_strategy.py  # 12 因子多策略 (3 种模式)
│   ├── scheduler/                 # 任务调度
│   │   ├── engine.py              # APScheduler 引擎包装
│   │   ├── tasks.py               # TaskRegistry 任务注册表
│   │   ├── watchdog.py            # ProcessWatchdog 进程看门狗
│   │   └── hotswap.py             # HotSwapWatcher 热重载
│   └── monitor/                   # 监控系统
│       ├── __init__.py            # LoopStatusReport / SystemStatusReport + 状态检查函数
│       ├── http_server.py         # 纯 stdlib HTTP 服务器 (127.0.0.1:9100)
│       └── elite_tracker.py       # EliteFactorTracker + AutoRetireManager
├── tests/                         # 28 个测试文件
│   ├── core/                      # 3 个文件
│   ├── factor_engine/             # 16 个文件 (含 conftest.py)
│   ├── pipeline/                  # 2 个文件
│   ├── scheduler/                 # 4 个文件
│   ├── strategies/                # 2 个文件
│   └── 顶层测试文件               # 8 个 (test_cli.py, test_llm.py, test_monitor.py, test_e2e.py 等)
├── config/
│   └── settings.yaml              # 默认 YAML 配置
├── docs/
│   ├── harness/                   # HARNESS 工程规范 (6 个文件)
│   ├── deploy/                    # 部署文档 (INSTALL.md, WINDOWS.md)
│   ├── factor_data_dict/          # 因子数据字典
│   ├── archive/                   # 已完成计划归档
│   ├── pending_for_datacore/      # 待 Data-Core 需求
│   └── production_plan.md         # 生产就绪路线图
├── memory/                        # 运行时持久化 (自动创建)
│   ├── evolution/                 # L2 演化状态
│   ├── meta_loop/                 # L1 元循环状态
│   ├── portfolio/                 # L3 组合状态
│   └── knowledge/factors/         # 因子知识库
│       ├── elite/                 # 精英因子 (晋升成功)
│       └── l1_injected/           # L1 注入因子候选
├── scripts/                       # 数据下载辅助脚本
├── data/                          # DuckDB 数据文件
├── .github/workflows/ci.yml       # GitHub Actions CI (Python 3.10/3.11/3.12)
├── pyproject.toml                 # 项目元数据 + 依赖 + 入口点
├── CLAUDE.md                      # AI 编码行为准则 (HARNESS 规范)
├── CODE_WIKI.md                   # 本文件 — 代码 Wiki
├── README.md                      # 项目概览文件
├── start_fts.ps1                  # PowerShell 启动脚本
└── .gitignore
```

### 2.2 模块职责一览

| 包 | 文件 | 职责 |
|---|---|---|
| `fts.cli` | `cli.py` | 统一 CLI 入口: version / monitor / evolution / meta-loop / portfolio / factor / scheduler |
| `fts.config` | `settings.py` | 配置加载 (YAML → 环境变量 → 默认值 三级优先级); `FTSConfig` dataclass; `get_config()` 惰性单例 |
| `fts.core` | `atomic.py` | 原子文件写入 (`atomic_write`)、读取 (`atomic_read`)、带备份轮转的状态写入 (`atomic_write_state`) |
| `fts.core` | `enums.py` | `EvolutionStage` (L0/L1/L2/L3), `FactorPriority` (HIGH/MEDIUM/LOW), `FactorStatus` (PENDING/INJECTED/DECAYED/REJECTED) |
| `fts.core` | `contracts.py` | 从 `factor_engine.contracts` 重导出所有 TypedDict |
| `fts.data` | `data.py` | `FTSDataProvider`: 包装 Data-Core 的 `UnifiedDataProvider`, 5 级数据质量降级 (PRIMARY→DAILY→CACHED→STALE→UNAVAILABLE), 合成数据回退 |
| `fts.llm` | `llm.py` | `LLMClient` (ABC) → `OpenAIClient` / `AnthropicClient` / `MockLLMClient`; `LLMCallRecord` 审计跟踪; 环境变量自动检测后端 |
| `fts.factor_engine` | 19 个文件 | **核心引擎**: L1/L2/L3 三层循环、契约、评估、Verifier、沙箱、种子、经验链 |
| `fts.pipeline` | `base.py` | Pipeline 框架: `DataPayload` dataclass, `ProcessingStage` Protocol, `FactorPipeline` ABC |
| `fts.pipeline` | `factor_combiner.py` | `FactorCombiner`: z-score 归一化、可选 QR 正交化、权重融合 |
| `fts.strategies` | `base_v2.py` | v2 可插拔策略框架: `BaseStrategyV2` ABC, `RawSignal`/`ScoredSignal`, `StrategyV1Adapter` 适配器 |
| `fts.strategies` | `multi_factor_strategy.py` | `MultiFactorStrategy`: 12 因子实现 (momentum/volatility/volume_flow/oi_change/basis 等), 3 种模式 (pure_momentum/long_short/neutral) |
| `fts.scheduler` | `engine.py` | `SchedulerEngine`: 包装 APScheduler `BackgroundScheduler`, 可选依赖回退 |
| `fts.scheduler` | `tasks.py` | `TaskRegistry`: 任务注册 + 4 个默认任务 (L1/L2/L3/health_check) |
| `fts.scheduler` | `watchdog.py` | `ProcessWatchdog`: 重启策略 (30s 内 3 次重启 → 5min 熔断) |
| `fts.scheduler` | `hotswap.py` | `HotSwapWatcher`: watchdog 库监听文件变更 → `importlib.reload`, 可选依赖回退 |
| `fts.monitor` | `__init__.py` | `LoopStatusReport`/`SystemStatusReport`, `check_all_status()`, `format_status_report()`, `status_report_to_json()` |
| `fts.monitor` | `http_server.py` | `MetricsHTTPServer`: 纯 stdlib HTTP (127.0.0.1:9100), 端点 /health /metrics / |
| `fts.monitor` | `elite_tracker.py` | `EliteFactorTracker`: 追踪 + 自动退役 (cooldown_days); `AutoRetireManager` |

---

## 3. 关键类与函数说明

### 3.1 `fts.cli` — `fts/cli.py`

统一命令行入口,使用 `argparse` 实现子命令分发。

**函数:**

| 函数 | 用途 |
|---|---|
| `main(argv=None)` | CLI 入口点,解析参数并分发到子命令处理器 |
| `build_parser()` | 构建 `ArgumentParser`,注册所有子命令 |
| `_cmd_version(_args)` | 打印版本号 + 引擎版本 + 配置路径 |
| `_cmd_monitor(args)` | 调用 `check_all_status()` 检查 L1/L2/L3 健康状态,支持 `--json` |
| `_cmd_evolution_run(args)` | 启动 L2 因子演化: 支持 `--universe single/csi300/futures`, `--max-generations`, `--symbol` |
| `_cmd_meta_loop_run(_args)` | 启动 L1 Meta-Loop (市场感知 + Bootstrapping) |
| `_cmd_portfolio_run(_args)` | 启动 L3 组合构建 (正交化 + 衰减检验 + 信号合成) |
| `_cmd_scheduler_run(_args)` | 启动 APScheduler 后台运行 |
| `_cmd_scheduler_list(_args)` | 列出所有已注册调度任务 |
| `_cmd_factor_list(args)` | 列出 elite 目录中的因子 (JSON 元数据) |
| `_cmd_factor_show(args)` | 查看单个因子 JSON 详情 (支持部分匹配) |
| `_prepare_data(symbol, days)` | 准备单标 OHLCV 数据 (Data-Core → 合成回退) + 前向收益 |
| `_prepare_cross_section_data(universe, days, max_stocks)` | 准备横截面面板数据 (csi300/futures) |

**CLI 命令树:**

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
├── scheduler            # 任务调度
│   ├── run              # 启动后台调度
│   └── list             # 列出任务
└── factor               # 因子管理
    ├── list [--elite-dir]
    └── show <factor_id> [--elite-dir]
```

### 3.2 `fts.config.settings` — `fts/config/settings.py`

**配置加载优先级:** 环境变量 (`FTS_*`) > YAML 配置文件 > 代码默认值。

**关键类/函数:**

| 名称 | 类型 | 说明 |
|---|---|---|
| `FTSConfig` | `@dataclass` | 全局配置容器,字段: `memory_dir`, `elite_dir`, `default_market`, `llm_backend`, `max_generations`, `population_size`, `micro_trials_per_generation`, `max_workers`, `meta_loop_interval_hours`, `meta_loop_max_tokens`, `portfolio_max_factors`, `portfolio_top_n`, `portfolio_decay_days`, `log_level`, `log_file` |
| `load_config(config_path)` | 函数 | 加载 YAML → 应用环境变量覆盖 → 返回 FTSConfig |
| `get_config()` | 函数 | 惰性单例访问器 |

**`FTSConfig` 关键字段默认值:**

| 字段 | 默认值 | 环境变量 |
|---|---|---|
| `memory_dir` | `"memory"` | `FTS_MEMORY_DIR` |
| `elite_dir` | `"memory/knowledge/factors/elite"` | `FTS_ELITE_DIR` |
| `default_market` | `"futures"` | `FTS_DEFAULT_MARKET` |
| `max_generations` | `10` | — |
| `micro_trials_per_generation` | `50` | — |
| `max_workers` | `4` | `FTS_MAX_WORKERS` |
| `portfolio_max_factors` | `20` | — |

### 3.3 `fts.core` — `fts/core/`

**`fts/core/atomic.py` — 原子文件操作:**

| 函数 | 说明 |
|---|---|
| `atomic_write(path, data, *, make_dir=True, encoding="utf-8")` | 临时文件 + `os.replace()` 原子写入 JSON |
| `atomic_read(path, *, default=None, encoding="utf-8")` | 安全读取 JSON,失败返回 default,不抛异常 |
| `atomic_write_state(path, state, *, backup_count=3)` | 原子写入 + 备份轮转: 旧文件 → `.bak.0` → `.bak.1` → `.bak.2` |

**`fts/core/enums.py` — 枚举:**

| 枚举 | 值 |
|---|---|
| `EvolutionStage` | `L0_HUMAN`, `L1_META_LOOP`, `L2_EVOLUTION`, `L3_PORTFOLIO` |
| `FactorPriority` | `HIGH`, `MEDIUM`, `LOW` |
| `FactorStatus` | `PENDING`, `INJECTED`, `DECAYED`, `REJECTED` |

**`fts/core/contracts.py` — 91 LOC, 从 `fts.factor_engine.contracts` 重导出所有 TypedDict.**

### 3.4 `fts.data` + `fts.data_mcp` — MCP 数据层

**`fts/data.py`** — 统一数据入口，包装 `MCPDataProvider` 提供 OHLCV 数据。
**`fts/data_mcp.py`** — 腾讯自选股 MCP 数据适配层，基于 akshare（东方财富/腾讯数据源）。

`FTSDataProvider` — MCP 数据集成器，自动处理代码格式转换（sh600519↔600519）。

**关键类/函数（`fts/data.py`）:**

| 名称 | 类型 | 说明 |
|---|---|---|
| `DataUnavailableError` | Exception | 所有数据源失效时抛出 |
| `FTSDataProvider` | class | 统一数据提供类，包装 MCPDataProvider；无网络时回退合成数据 |
| `__init__(mcp_provider)` | ctor | 注入外部 MCP 提供者（用于测试） |
| `get_ohlcv(symbol, days, adjust, trace_id)` | method | 获取 A 股/ETF 日 OHLCV 数据（前复权默认） |
| `get_etf_ohlcv(symbol, days, adjust, trace_id)` | method | 获取 ETF OHLCV 数据 |
| `get_csi300_panel(days, max_stocks)` | method | 获取沪深 300 成分股面板数据 |
| `get_etf_panel(days)` | method | 获取常见 ETF 面板数据 |
| `get_stock_panel(symbols, days)` | method | 获取任意股票列表面板数据 |
| `search_symbol(query, limit)` | method | 搜索股票/ETF 代码 |
| `synthesize_ohlcv(n_days, base_price, seed)` | static | 合成 OHLCV 数据（降级回退） |
| `get_data_provider()` | 函数 | 全局单例访问器 |

**关键类/函数（`fts/data_mcp.py`）:**

| 名称 | 类型 | 说明 |
|---|---|---|
| `MCPDataError` | Exception | MCP 数据获取失败 |
| `MCPDataProvider` | class | 基于 akshare 的 MCP 数据提供者 |
| `get_ohlcv(symbol, days, adjust)` | method | 自动识别股票/ETF 并获取 K 线 |
| `get_etf_ohlcv(symbol, days)` | method | ETF 专用 OHLCV |
| `get_stock_panel(symbols, days)` | method | 批量面板数据 |
| `synthesize_ohlcv(n_days, base_price, seed)` | static | 合成数据回退 |
| `search_symbol(query, limit)` | method | 搜索功能 |
| `list_csi300()` | method | 沪深 300 成分股列表 |
| `list_etf()` | method | 常见 ETF 列表 |
| `CSI300_SUBSET` | 常量 | 76 只沪深 300 代表股 |
| `ETF_SUBSET` | 常量 | 18 只常见 ETF |

### 3.5 `fts.llm` — `fts/llm.py`

**LLM 客户端层次结构:**

```
LLMClient (ABC)
├── OpenAIClient      ← 默认后端, 环境变量: OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
├── AnthropicClient   ← 替代后端, 环境变量: ANTHROPIC_API_KEY
└── MockLLMClient     ← 回退 (无 LLM 依赖时使用, 确定性输出)
```

**关键类/函数:**

| 名称 | 类型 | 说明 |
|---|---|---|
| `LLMError` | Exception | LLM 调用失败异常 |
| `LLMCallRecord` | `@dataclass` | 单次 LLM 调用记录: `prompt`, `response`, `model`, `tokens_in/out`, `duration_ms`, `error`, `trace_id`; `total_tokens` 属性 |
| `LLMClient` | ABC | 抽象基类: `complete(prompt, max_tokens)` → `(response, tokens_out)`, `generate_json(prompt)` → `dict` |
| `OpenAIClient` | class | OpenAI 兼容 API, 支持 `OPENAI_BASE_URL` (可用于 DeepSeek 等代理) |
| `AnthropicClient` | class | Anthropic Claude API |
| `MockLLMClient` | class | 确定性 mock, 返回预设模板内容 |
| `get_llm_client()` | 函数 | 自动检测: `LLM_BACKEND` env → OpenAI → Anthropic → Mock 回退 |
| `get_default_llm_client()` | 函数 | 便捷函数, 返回检测到的 LLM 客户端 |

### 3.6 `fts.factor_engine` — 核心引擎 (最大模块)

#### 3.6.1 `contracts.py` — TypedDict 契约 (560 LOC)

**L2 Evolution Loop 契约:**

| TypedDict / 常量 | 关键字段 |
|---|---|
| `FactorSignature` | `input_fields` (list[str]), `output_type` ("signal"/"score"), `frequency` ("daily"/"hourly"/"minute"), `lookback` (int) |
| `EconomicLogic` | `theory` (0-5), `behavioral` (0-5), `microstructure` (0-5), `institutional` (0-5), `narrative` (str) |
| `FactorProgram` | `factor_id` (fct\_\<8hex\>), `name`, `code` (str), `params` (dict), `signature`, `economic_logic`, `source` (seed/macro_evolution/bootstrapping/manual), `parent_id`, `generation`, `created_at`, `trace_id` |
| `BacktestMetrics` | `ic`, `icir`, `sharpe`, `max_drawdown`, `monotonicity` (bool), `oos_ratio`, `t_stat`, `turnover_monthly` |
| `EconomicScore` | `theory` (0-5), `behavioral` (0-5), `microstructure` (0-5), `institutional` (0-5), `dimensions_passed`, `narrative` |
| `MultipleTestResult` | `bonferroni_p`, `fdr_q`, `effective_n_factors`, `adjusted_t`, `passed` (bool) |
| `FactorEvaluation` | `factor_id`, `trace_id`, `level_1_backtest`, `level_2_economic`, `level_3_multiple`, `walk_forward`, `passed`, `failure_reasons` |
| `ExperienceTrace` | `trace_id`, `factor_id`, `parent_id`, `generation`, `mutation_type`, `mutation_summary`, `evaluation`, `success`, `lessons` |
| `EvolutionState` | `run_id`, `started_at`, `last_generation`, `total_factors_evaluated/promoted`, `tokens_consumed`, `budget_limit`, `status` (running/paused/completed/circuit_broken) |
| `VerifierConfig` | `min_ic` (0.03), `min_icir` (0.5), `min_sharpe` (1.5), `max_drawdown` (0.20), `min_economic_score` (3), `min_t_stat` (3.0), `max_fdr` (0.05), `min_oos_ratio` (0.30), `max_turnover_monthly` (0.50) |
| `VerifierResult` | `passed` (bool), `failure_reasons` (list[str]), `checked_against` (VerifierConfig), `checked_at` |
| `BudgetConfig` | `nightly_token_limit` (200K), `monthly_token_limit` (6M), `max_generation` (50), `max_tokens_per_factor` (10K), `circuit_breaker_token_ratio` (2.0), `circuit_breaker_consecutive_low_ic` (3), `circuit_breaker_low_ic_threshold` (0.01), `circuit_breaker_failure_rate` (0.90) |
| `FactorSource` | Literal["seed", "macro_evolution", "bootstrapping", "manual"] |
| `MutationType` | Literal["macro_logic", "micro_param", "combined"] |
| `EvolutionStatus` | Literal["running", "paused", "completed", "circuit_broken"] |

**L1 Meta-Loop 契约:**

| TypedDict / 常量 | 关键字段 |
|---|---|
| `L1BootstrappingSource` | Literal["l1_bootstrapping", "l1_web_discovery", "l1_debate_gap", "l1_manual"] |
| `SeedCandidate` | `candidate_id` (cand\_\<8hex\>), `name`, `code`, `params`, `signature`, `economic_logic`, `source`, `parent_topic`, `debate_round_ref`, `is_executable`, `is_duplicate`, `passed_l1_verifier`, `trace_id` |
| `L1MetaLoopState` | `run_id`, `last_bootstrap_topic`, `total_candidates_generated/injected`, `tokens_consumed`, `status`, `candidates_ref` |
| `FactorPoolEntry` | `factor_id`, `name`, `source`, `parent_topic`, `priority`, `status` (pending/injected/decayed/rejected) |
| `FactorPool` | `version`, `updated_at`, `factors` (list[FactorPoolEntry]), `total_count`, `pending_count` |
| `L1VerifierConfig` | `min_economic_score` (2), `require_executable` (True), `require_not_duplicate` (True), `min_narrative_length` (20) |
| `L1BudgetConfig` | `daily_token_limit` (50K), `monthly_token_limit` (1.5M), `max_bootstraps_per_run` (5), `max_tokens_per_candidate` (5K), `circuit_breaker_token_ratio` (2.0), `circuit_breaker_failure_rate` (0.95), `circuit_breaker_consecutive_low_quality` (5) |

**L3 Portfolio Loop 契约:**

| TypedDict / 常量 | 关键字段 |
|---|---|
| `FactorCorrelation` | `factor_id_a`, `factor_id_b`, `pearson`, `spearman` |
| `PortfolioSignal` | `factor_id`, `name`, `weight`, `sharpe`, `ic`, `turnover`, `decay_6m`, `orthogonalized` (bool), `retained` (bool) |
| `PortfolioCombo` | `combo_id` (cmb\_\<8hex\>), `trace_id`, `synthesis_mode` (equal_weight/sharpe_weight/lightgbm), `signals` (list), `combo_sharpe`, `combo_turnover`, `max_correlation`, `n_factors`, `status` |
| `AgentOptimizationProposal` | `proposal_id`, `agent_name`, `current_prompt_summary`, `suggested_changes`, `rationale`, `priority`, `status` |
| `L3VerifierConfig` | `min_sharpe` (2.0), `max_correlation` (0.3), `max_turnover` (0.50), `max_decay_rate` (0.30), `min_n_factors` (3) |
| `L3MetaLoopState` | `run_id`, `last_synthesis_mode`, `total_signals_processed/retained`, `status` |

#### 3.6.2 `evolution_loop.py` — L2 主循环

| 名称 | 类型 | 说明 |
|---|---|---|
| `EvolutionRunResult` | `@dataclass` | 演化运行结果: `run_id`, `trace_id`, `generations_completed`, `total_factors_evaluated`, `total_factors_promoted`, `tokens_consumed`, `status`, `circuit_breaker_reason`, `elite_factor_ids` |
| `EvolutionLoop` | class (490 LOC) | L2 主编排器, 每代执行 6 步流水线 |
| `__init__(data, forward_returns, elite_dir, memory_dir, llm_client, seed_pool, verifier, n_trials_micro, cross_section_data, cross_section_dates)` | ctor | 初始化演化循环, 可接受横截面面板数据 |
| `run(max_generation)` | method | 执行 6 步流水线: 1.macro → 2.micro → 3.eval → 4.verify → 5.experience → 6.save |
| `_check_circuit_breaker()` | method | 三阈值检测: token 超限 / 连续 3 代 IC<0.01 / 失败率>90% |
| `_promote_to_elite(factor)` | method | 写入 elite JSON 到 `elite_dir` |

#### 3.6.3 `meta_loop.py` — L1 Meta-Loop

| 名称 | 类型 | 说明 |
|---|---|---|
| `MetaRunResult` | `@dataclass` | L1 运行结果: `run_id`, `status`, `injected` (injected candidate IDs) |
| `MetaLoopError` | Exception | L1 操作失败 |
| `L1VerifierLocked` | Exception | L1 Verifier 锁定异常 |
| `FactorPoolError` | Exception | 因子池操作失败 |
| `MetaStateManagerError` | Exception | 状态管理失败 |
| `L1Verifier` | class | 锁定 Verifier: `min_economic_score>=2/4` + `executable=True` + `not_duplicate=True` |
| `MetaStateManager` | class | L1 状态管理: `state.json` + 备份镜像 (`state.json.backup`) |
| `FactorPoolManager` | class | 因子池管理: `factor_pool.json` 读写、注入、优先级排序 |
| `DebateQualityAnalyzer` | class | 辩论质量分析: 读取 `debate_journal.json`, 标志 `bullish_weak`/`bearish_weak`/`insufficient_rounds`/`no_debate` |
| `BootstrappingChain` | class | 种子候选生成链: 提取 Agent → 验证 Agent → 代码生成 Agent |
| `MetaLoop` | class | L1 编排器: 每日 09:00 执行 5 步流程 |

**L1 流程 (5 步):**
1. **感知**: Data-Core 获取新闻与市场快照
2. **Debate 分析**: 读取 FDT 辩论数据, 识别论证薄弱维度
3. **Bootstrapping**: Agent 链 → 提取市场主题 → 验证合理性 → 生成因子代码
4. **L1 Verifier**: 宽松筛选 (economic_logic >= 2/4 维度 + 可执行 + 不重复)
5. **注入**: 写入 `factor_pool.json` + `memory/knowledge/factors/l1_injected/`

#### 3.6.4 `portfolio_loop.py` — L3 Portfolio Loop

| 名称 | 类型 | 说明 |
|---|---|---|
| `PortfolioRunResult` | `@dataclass` | 组合运行结果: `run_id`, `status`, `factor_ids`, `combined_sharpe`, `combined_turnover`, `n_factors`, `tokens_consumed` |
| `L3Error` | Exception | L3 操作失败 |
| `L3Verifier` | class | 锁定 Verifier: `combo_sharpe>=2.0`, `max_correlation<=0.3`, `combo_turnover<=0.50`, `decay_6m<=0.30`, `min_n_factors>=3` |
| `PortfolioStateManager` | class | L3 状态管理: `current_combo.json` + `agent_proposals/` 目录 |
| `PortfolioManager` | class | L3 主编排器 (140+ LOC) |
| `load_elite_factors()` | method | 从 `elite_dir` 读取所有 elite 因子 JSON |
| `orthogonalize_factors(factors)` | method | QR 正交化, 剔除相关性 > 0.7 的因子 |
| `decay_test(factor, window)` | method | 6 个月滚动窗口衰减检验, 衰减 >30% 剔除 |
| `synthesize_signals(factors, method)` | method | 信号合成, 支持 `equal_weight` / `sharpe_weight` / `lightgbm` |
| `build_combo(factors)` | method | 构建最终组合, 权重归一化 + 十分位 + 多空 + 成本估算 |
| `generate_agent_proposals()` | method | LLM 驱动的 Agent 优化建议 |
| `inject_to_fdt(combo)` | method | 写入 FDT 消费路径 |
| `PortfolioLoop` | class | 每周一 06:00 运行的组合构建编排器 |

#### 3.6.5 `seed_pool.py` — 种子因子池

15 个内置种子因子,涵盖 A 股 + 期货市场:

| # | 因子名 | 代码名 | 市场 |
|---|---|---|---|
| 1 | 动量因子 | `momentum` | 全市场 |
| 2 | 波动率回归 | `volatility_reversion` | 全市场 |
| 3 | 资金流 | `volume_flow` | 全市场 |
| 4 | 持仓量变化 | `oi_change` | 期货 |
| 5 | 基差 | `basis` | 期货 |
| 6 | 库存分位 | `inventory_pct` | 期货 |
| 7 | 开工率 | `capacity` | 期货 |
| 8 | 宏观制度 | `macro_regime` | 全市场 |
| 9 | 利率代理 | `rate_proxy` | 全市场 |
| 10 | PMI 代理 | `pmi_proxy` | 全市场 |
| 11 | 龙虎持仓 | `position_rank` | 期货 |
| 12 | 仓单变化 | `warrant_change` | 期货 |
| 13 | 价值因子 | `value_factor` | A 股 |
| 14 | 质量因子 | `quality_factor` | A 股 |
| 15 | 市值因子 | `size_factor` | A 股 |

**关键函数:**
- `SeedPool` — 种子池管理器, 提供 `fetch()`, `add_seed()`, `list_seeds()`
- `get_default_seed_pool()` — 返回包含 15 个种子因子的默认 SeedPool 实例

#### 3.6.6 `factor_program.py` — 安全沙箱执行器

| 名称 | 类型 | 说明 |
|---|---|---|
| `ALLOWED_IMPORTS` | 常量 | 白名单: `numpy`, `pandas`, `scipy`, `statsmodels`, `talib`, `math`, `statistics` |
| `FORBIDDEN_NAMES` | 常量 | 黑名单: `open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals` 等 |
| `FORBIDDEN_MODULES` | 常量 | 黑名单: `os`, `sys`, `subprocess`, `socket`, `ctypes`, `pickle` 等 |
| `FactorCompileError` | Exception | 因子编译失败 |
| `validate_factor_code(code)` | 函数 | AST 预验证, 拒绝禁止的模式 |
| `create_factor_program(name, code, params, signature, economic_logic, source, parent_id, generation, trace_id)` | 函数 | 创建 `FactorProgram` TypedDict 实例, 自动生成 `factor_id` |
| `generate_factor_id(code)` | 函数 | 基于代码 SHA1 返回 `fct_<sha1[:8]>` |
| `FactorExecutor` | class | 受限 `__builtins__` + `_safe_import` 的安全沙箱执行器 |

#### 3.6.7 `macro_evolution.py` — 宏观演化

| 名称 | 类型 | 说明 |
|---|---|---|
| `MacroEvolver` | class | LLM 驱动的因子代码编辑器 |
| `evolve(parent_factor, experience_chain)` | method | 构建 prompt (父因子 + 经验链上下文) → 调用 LLM → 解析返回新因子代码 |
| `_apply_code_modification(code, modification)` | method | 通过正则替换实现 mock 级的代码修改 (如 `window_plus_5`) |

#### 3.6.8 `micro_evolution.py` — 微观演化

| 名称 | 类型 | 说明 |
|---|---|---|
| `optimize_params(factor, objective, n_trials)` | 函数 | optuna TPE 贝叶斯优化, 早停 (20 次连续无改进) |
| `_suggest_param(trial, name, default_value)` | 函数 | 从默认值推断参数空间 (int→int 范围, float→float 范围, bool→分类) |
| `evolve_micro(factor, data)` | 函数 | 执行微观调参, 返回带最佳参数的新 `FactorProgram` |

#### 3.6.9 `evaluation_chain.py` — 三级评估链

| 名称 | 类型 | 说明 |
|---|---|---|
| `evaluate_backtest(factor, data, forward_returns)` | 函数 | Level 1: IC / ICIR / Sharpe / max_drawdown / monotonicity / t_stat / turnover_monthly |
| `evaluate_economic_logic(factor)` | 函数 | Level 2: 四维经济学评分 (theory/behavioral/microstructure/institutional, 0-5/维) |
| `evaluate_multiple_tests(factors, evaluations)` | 函数 | Level 3: Bonferroni + FDR + PCA-based effective_n |
| `EvaluationChain` | class | 三级评估编排器 |
| `evaluate(factor, data, forward_returns, walk_forward)` | method | 执行三级评估 + 可选 walk-forward, 返回 `FactorEvaluation` |

#### 3.6.10 `verifier.py` — 锁定 Verifier 协议

| 名称 | 类型 | 说明 |
|---|---|---|
| `VerifierNotLockedError` | Exception | Verifier 未锁定则抛异常 |
| `VerifierAlreadyLockedError` | Exception | 锁定后试图修改配置则抛异常 |
| `FactorVerifier` | class | 核心 Verifier: `__init__` 后立即 `_locked=True` |
| `check(evaluation)` | method | 严格按 `VerifierConfig` 逐项比较, 返回 `VerifierResult` |
| `config` (property) | property | 返回配置只读副本 |
| `locked` (property) | property | 返回是否锁定 |
| `update_config(new_config)` | method | 锁定后调用抛 `VerifierAlreadyLockedError` |
| `unlock()` | method | 仅测试用, 生产禁止 |
| `get_global_verifier()` | 函数 | 进程级单例 (DEFAULT_VERIFIER_CONFIG) |
| `reset_global_verifier()` | 函数 | 重置单例 (仅测试用) |

#### 3.6.11 `state.py` — 状态管理

| 名称 | 类型 | 说明 |
|---|---|---|
| `generate_trace_id(prefix="ftr")` | 函数 | 返回 `{prefix}_{8hex}_{timestamp}` |
| `generate_run_id()` | 函数 | 返回 `run_{8hex}_{timestamp}` |
| `EvolutionStateManager` | class | 状态管理器: `state.json` + `state.json.backup` |
| `save_state(state)` | method | 原子写入 + 备份轮转 |
| `load_state()` | method | 安全读取, 主文件失败回退 backup |

#### 3.6.12 `experience_chain.py` — 经验链 (LLM 记忆)

| 名称 | 类型 | 说明 |
|---|---|---|
| `ExperienceChainError` | Exception | 经验链操作失败 |
| `ExperienceChain` | class | 在 `success/` 和 `failure/` 子目录存储经验追踪 |
| `MAX_CHAIN_SIZE` | 常量 | 100 (满时淘汰最旧 20 条) |
| `read_recent_for_llm()` | method | 返回 10 条成功 + 10 条失败经验作为 Markdown |
| `update(factor, evaluation)` | method | 更新经验链 + `update_summary()` |
| `update_summary()` | method | 为 LLM 生成 markdown 摘要 |
| `create_trace_from_evaluation(factor, evaluation, success)` | 函数 | 从评估结果创建 `ExperienceTrace` |

#### 3.6.13 `program.py` — L0 Program.md 解析

| 名称 | 类型 | 说明 |
|---|---|---|
| `DEFAULT_PROGRAM_MD` | 常量 | 带 YAML frontmatter 的 L0 模板 |
| `ProgramConfig` | class | 程序配置容器 |
| `parse_program_md(content)` | 函数 | 正则提取: `market_regime`, `factor_preference`, `agent_llm`, `budget`, `risk_constraints`, `circuit_breakers_reviewed` |
| `load_program()` | 函数 | 加载 Program.md |
| `init_program()` | 函数 | 初始化 Program.md |
| `get_llm_env_overrides(program_config)` | 函数 | 从 Program.md 获取 LLM 环境覆盖 |

#### 3.6.14 `walk_forward.py` — 走航验证

| 名称 | 类型 | 说明 |
|---|---|---|
| `WalkForwardOptimizer` | class | 滚动窗口验证 (`window_years=3`, `step_months=6`) |
| `optimize(factor, data)` | method | 计算 `ic_consistency` (IC>0 的窗口比例), `ic_volatility` |
| `consistency_score` | property | 40% consistency + 30% volatility + 30% strength |
| `WalkForwardResult` | TypedDict | `ic_consistency`, `ic_volatility`, `consistency_score`, `oos_sharpe` |

#### 3.6.15 `cost_model.py` — 交易成本模型

| 名称 | 类型 | 说明 |
|---|---|---|
| `TransactionCostModel` | class | 按市场配置交易成本 (`futures`/`stock`/`etf`) |
| `adjust(metrics, market)` | method | 计算 `net_sharpe = gross_sharpe - cost_penalty`, 其中 `cost_penalty = total_cost_bps * 12 / 0.15` |

#### 3.6.16 `regime.py` — 市场制度检测

| 名称 | 类型 | 说明 |
|---|---|---|
| `RegimeAwareSelector` | class | 从 OHLCV 数据检测市场制度 |
| `detect_regime(data)` | method | 返回 `bull` / `bear` / `oscillate` / `high_vol` / `low_vol` |
| `select_factors(factors, regime)` | method | 根据制度按历史表现筛选因子 |

**制度判定逻辑:**
- MA20 斜率 > +2% → `bull`; MA20 斜率 < -2% → `bear`
- ATR/价格比 > 3% → `high_vol`; < 1% → `low_vol`
- 其余 → `oscillate`

#### 3.6.17 `stress_test.py` — 压力测试

| 名称 | 类型 | 说明 |
|---|---|---|
| `StressTester` | class | 5 个内置历史压力场景测试 |
| `run_test(scenario_name)` | method | 运行指定压力场景 |
| `run_all()` | method | 运行全部 5 个场景 |
| `passed` | property | 全部场景 max_drawdown <= 40% 为通过 |

**压力场景:**
1. **原油暴跌** (2020-03 ~ 2020-05)
2. **双十一闪崩** (2016-11-11)
3. **股灾** (2015-06 ~ 2015-09)
4. **疫情冲击** (2020-02 ~ 2020-03)
5. **供给侧改革** (2016)

#### 3.6.18 `monitor.py` — 循环状态监控

| 名称 | 类型 | 说明 |
|---|---|---|
| `LoopStatus` | `@dataclass` | 单层循环状态: `name`, `state_file`, `exists`, `run_id`, `status`, `tokens_consumed`, `budget_limit`, `age_hours`, `healthy` |
| `AllStatus` | `@dataclass` | 三层汇总: `loops`, `any_circuit_broken`, `any_stale`, `total_tokens_today`, `checked_at` |
| `check_loop(name, state_dir, max_stale_hours)` | 函数 | 检查单层循环状态, 读取 `state.json` |
| `check_all(fdt_root, max_stale_hours)` | 函数 | 聚合 L1+L2+L3 状态 |
| `print_status_table(status)` | 函数 | 打印格式化状态表 |
| `main()` | 函数 | CLI 入口 |

### 3.7 `fts.pipeline` — `fts/pipeline/`

| 名称 | 类型 | 说明 |
|---|---|---|
| `DataPayload` | `@dataclass` | 管线数据载体: `data_type`, `symbol`, `payload`, `metadata`, `trace_id` |
| `ProcessingStage` | Protocol | Stage 协议: `input_type`/`output_type` + `process(payload)` |
| `FactorPipeline` | ABC | 管线抽象: `build_stages()` (抽象) + `run()` (具体编排器, 返回 `PipelineResult`) |
| `CombinerConfig` | `@dataclass` | 组合配置: `weights`, `normalize_inputs=True`, `clip_sigma=3.0`, `orthogonalize=True`, `min_active_factors=3` |
| `FactorCombiner` | class | z-score 归一化 → 可选 QR 正交化 → 加权融合 |

### 3.8 `fts.strategies` — `fts/strategies/`

| 名称 | 类型 | 说明 |
|---|---|---|
| `RawSignal` / `ScoredSignal` | `@dataclass` | 信号数据结构 |
| `BaseStrategyV2` | ABC | 抽象策略基类: `name` (抽象), `score()` (抽象), `compute()`, `filter()`, `validators`, `weight`, `depends_on` |
| `StrategyV1Adapter` | class | v1→v2 桥接 (适配器模式) |
| `FACTOR_WEIGHTS` | 常量 | 12 因子权重表 (总和 1.0): `momentum` 0.15, `basis` 0.15, ... |
| `PURE_MOMENTUM_WEIGHTS` | 常量 | 纯动量模式: 60% 量价 |
| `MultiFactorStrategy` | class | 多因子策略, 3 种模式: `pure_momentum`, `long_short`, `neutral` |
| `_calc_momentum` / `_calc_volatility_reversion` / ... | 函数 | 12 个因子计算函数 |

12 个因子计算函数: `_calc_momentum`, `_calc_volatility_reversion`, `_calc_volume_flow`, `_calc_oi_change`, `_calc_basis`, `_calc_macro`, `_calc_position_rank`, `_calc_warrant_change`, `_calc_inventory`, `_calc_capacity`, `_calc_pmi_proxy`, `_calc_rate_proxy`

### 3.9 `fts.scheduler` — `fts/scheduler/`

| 名称 | 类型 | 说明 |
|---|---|---|
| `SchedulerEngine` | class | 包装 APScheduler `BackgroundScheduler`; `start(daemon=True)` 在 APScheduler 未安装时返回 False |
| `TaskSpec` | `@dataclass` | 任务规范: `name`, `cron_expression`, `description`, `enabled` |
| `TaskRegistry` | class | 任务注册表: `register`, `unregister`, `list_enabled` |
| `list_tasks()` | 函数 | 列出所有已注册任务 |
| `ProcessWatchdog` | class | 进程看门狗: 30s 内 3 次重启 → 5min 熔断 |
| `HotSwapWatcher` | class | 文件变更监听 → `importlib.reload`; watchdog 库缺失时静默回退 |

**默认调度任务:**

| 任务 | cron | 描述 |
|---|---|---|
| `l1_meta_loop` | `0 9 * * *` | 每日 09:00 L1 Meta-Loop |
| `l2_evolution_loop` | `0 23 * * *` | 每日 23:00 L2 因子演化 |
| `l3_portfolio_loop` | `0 6 * * 1` | 每周一 06:00 L3 组合构建 |
| `health_check` | `*/10 * * * *` | 每 10 分钟健康检查 |

### 3.10 `fts.monitor` — `fts/monitor/`

**`__init__.py` — 监控系统主入口:**

| 名称 | 类型 | 说明 |
|---|---|---|
| `LoopStatusReport` | `@dataclass` | 单循环状态报告 |
| `SystemStatusReport` | `@dataclass` | 系统整体状态 |
| `check_loop_status(name, state_dir)` | 函数 | 检查单循环状态 |
| `check_all_status()` | 函数 | 检查所有循环, 返回 `SystemStatusReport` |
| `format_status_report(report)` | 函数 | 格式化人类可读报告 |
| `status_report_to_json(report)` | 函数 | 序列化 JSON 报告 |

**`http_server.py` — HTTP 监控服务器:**

| 名称 | 类型 | 说明 |
|---|---|---|
| `MetricsHTTPServer` | class | 纯 stdlib HTTP 服务器, 监听 `127.0.0.1:9100` |
| `start()` | method | 守护线程启动 |

**端点:**

| 端点 | 输出 |
|---|---|
| `GET /health` | JSON 健康状态 (状态 + 循环摘要) |
| `GET /metrics` | Prometheus 文本格式指标 (L1/L2/L3 状态 gauge, token 消耗 counter) |
| `GET /` | HTML 仪表板 (含状态表格) |

**`elite_tracker.py` — Elite 因子追踪:**

| 名称 | 类型 | 说明 |
|---|---|---|
| `TrackingSnapshot` | TypedDict | 追踪快照: `factor_id`, `sharpe`, `ic`, `decay_6m`, `consecutive_low_ic` |
| `EliteFactorTracker` | class | Elite 因子追踪器: `init_tracker()`, `update()`, `get_decaying(max_consecutive=4)`, `auto_retire()`, `report()` |
| `_calc_decay_6m()` | method | 前半段 vs 后半段均值 IC 比较 |
| `AutoRetireManager` | class | 基于 `cooldown_days` 的自动退役管理器 |

---

## 4. 模块间依赖关系

### 4.1 全局依赖图

```
fts.cli (顶层编排器)
  ├── fts.config.settings          (get_config)
  ├── fts.data                     (FTSDataProvider, _prepare_data)
  ├── fts.llm                      (MockLLMClient)
  ├── fts.factor_engine.*          (所有循环 + 因子管理 + 契约)
  ├── fts.monitor                  (check_all_status, format_status_report)
  └── fts.scheduler                (SchedulerEngine, list_tasks)

fts.factor_engine.evolution_loop (L2 主循环)
  ├── fts.factor_engine.contracts          (TypedDict 契约)
  ├── fts.factor_engine.evaluation_chain   (EvaluationChain)
  ├── fts.factor_engine.macro_evolution    (MacroEvolver)
  ├── fts.factor_engine.micro_evolution    (evolve_micro)
  ├── fts.factor_engine.verifier           (FactorVerifier)
  ├── fts.factor_engine.state              (EvolutionStateManager)
  ├── fts.factor_engine.experience_chain   (ExperienceChain)
  └── fts.factor_engine.seed_pool          (SeedPool)

fts.factor_engine.meta_loop (L1 主循环)
  ├── fts.factor_engine.contracts
  ├── fts.factor_engine.seed_pool
  ├── fts.factor_engine.state
  └── fts.factor_engine.verifier           (L1Verifier)

fts.factor_engine.portfolio_loop (L3 主循环)
  ├── fts.factor_engine.contracts
  ├── fts.factor_engine.state
  ├── fts.factor_engine.verifier           (L3Verifier)
  ├── fts.factor_engine.walk_forward       (WalkForwardOptimizer)
  └── fts.pipeline.factor_combiner         (orthogonalize_factors)

fts.factor_engine.evaluation_chain
  ├── fts.factor_engine.contracts
  ├── fts.factor_engine.walk_forward
  └── numpy / pandas / scipy / statsmodels (统计计算)

fts.core.contracts → fts.factor_engine.contracts (重导出)
fts.strategies.base_v2 → fts.pipeline.base (StrategyV1Adapter 包装)
```

### 4.2 依赖规则

- **`fts.core`** 是基础层 — 不依赖上层模块 (零依赖)
- **`fts.factor_engine.contracts`** 是 TypedDict 单一真源; `fts.core.contracts` 仅重导出
- **`fts.cli`** 是顶层编排器, 依赖所有子系统, 但不参与业务逻辑
- **`fts.factor_engine`** 有内部子依赖, 但不依赖 `fts.pipeline`, `fts.strategies`, `fts.scheduler`, 或 `fts.monitor`
- **`fts.strategies`** 依赖 `fts.pipeline` (通过 `StrategyV1Adapter`)
- **`fts.scheduler`** 完全解耦 — 仅依赖 stdlib + 可选 `apscheduler`/`watchdog`

### 4.3 数据流方向

```
Data-Core ──→ fts.data ──→ fts.factor_engine ──→ fts.monitor
                                ↕                       ↕
                          fts.pipeline            fts.monitor.http_server
                                ↕
                          fts.strategies
                                ↕
                          fts.scheduler
```

---

## 5. 外部依赖

### 5.1 必需依赖 (`pyproject.toml`)

| 库 | 最低版本 | 用途 |
|---|---|---|
| `numpy` | >=1.24 | 数值计算 (IC/Sharpe/矩阵运算/正交化) |
| `pandas` | >=2.0 | DataFrame / 时间序列 / OHLCV 处理 |
| `PyYAML` | >=6.0 | YAML 配置解析 |

### 5.2 可选依赖 (extras)

| Extra | 库 | 用途 |
|---|---|---|
| `evolution` | `optuna` | Micro 演化 TPE 贝叶斯调参 |
| `llm` | `openai` | OpenAI / DeepSeek 兼容 API 客户端 |
| `llm` | `anthropic` | Anthropic Claude API 客户端 |
| `mcp` | `akshare` | MCP 数据源（A 股/ETF 行情，腾讯/东方财富） |
| `dev` | `pytest` | 测试框架 |
| `dev` | `pytest-cov` | 覆盖率报告 |

### 5.3 隐式/软依赖 (静默回退)

| 库 | 用途 | 回退行为 |
|---|---|---|
| `scipy` | 统计检验, QR 分解 | 评估链降级 |
| `statsmodels` | 多重检验校正 (Bonferroni/FDR) | 多重检验降级 |
| `TA-Lib` | 技术分析指标 (沙箱中允许) | 因子代码中可用 |
| `APScheduler` | 基于 cron 的任务调度 | `SchedulerEngine.start()` 返回 False |
| `watchdog` | 文件系统变更监听 (热重载) | `HotSwapWatcher` 静默 no-op |
| `lightgbm` | L3 可选信号合成方法 | 回退到等权/夏普加权 |

---

## 6. 运行/构建/测试方式

### 6.1 安装

```bash
# 需要 Python 3.10+
python -m venv venv
venv\Scripts\activate       # Windows

# 基础安装 (仅必需依赖)
pip install -e .

# 开发安装 (含测试)
pip install -e ".[dev]"

# 完整安装 (含演化 + LLM + 数据)
pip install -e ".[dev,evolution,llm,data]"
```

### 6.2 环境变量

```powershell
# PowerShell 设置 (参考 start_fts.ps1)
$env:OPENAI_API_KEY = "your-key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com/v1"   # 可选: 使用 DeepSeek 代理
$env:OPENAI_MODEL = "deepseek-chat"
$env:FTS_CONFIG_FILE = "config/settings.yaml"
$env:FTS_MEMORY_DIR = "memory"
$env:FTS_LLM_BACKEND = ""           # 留空 = 自动检测
$env:FTS_LOG_LEVEL = "INFO"
```

### 6.3 CLI 命令

```bash
fts version                                    # 打印版本 + 引擎版本 + 配置路径
fts monitor [--json]                           # 显示 L1/L2/L3 循环状态 (默认文本, --json 输出 JSON)
fts evolution run [--max-generations 10]       # L2 单标因子演化
fts evolution run --universe csi300            # L2 CSI300 横截面演化
fts evolution run --universe futures           # L2 期货横截面演化
fts meta-loop run                              # L1 市场感知 (每日 09:00)
fts portfolio run                              # L3 组合构建 (每周一 06:00)
fts scheduler run                              # 启动 APScheduler 后台运行
fts scheduler list                             # 列出所有已注册任务
fts factor list [--elite-dir]                  # 列出 elite 因子
fts factor show <factor_id> [--elite-dir]      # 显示因子详情 (支持部分匹配)
```

### 6.4 测试

```bash
# 运行所有测试 (带覆盖率)
pytest

# 等同于:
pytest --cov=fts --cov-report=term-missing -v

# 仅运行特定测试模块
pytest tests/test_e2e.py                       # 10 个 E2E 场景
pytest tests/factor_engine/                    # 因子引擎测试 (16 文件)
pytest tests/scheduler/                        # 调度器测试 (4 文件)
pytest tests/strategies/                       # 策略测试 (2 文件)
pytest -k "test_verifier"                      # 按关键字过滤

# 当前测试状态: 1,231 测试通过, 96% 覆盖率 (35 个模块 >=90%)
```

### 6.5 CI/CD

`.github/workflows/ci.yml` — GitHub Actions:

- 矩阵: Python 3.10 / 3.11 / 3.12
- 步骤: `pip install -e ".[dev,evolution]"` → `pytest --cov=fts --cov-report=xml` → codecov 上传
- 触发: push / pull_request 到 main 分支

### 6.6 生产部署 (Windows)

3 种部署模式 (详见 `docs/deploy/WINDOWS.md`):

| 模式 | 工具 | 适用场景 |
|---|---|---|
| 任务计划程序 | Windows Task Scheduler + `start_fts.ps1` | 开发测试 |
| Windows 服务 | NSSM: `nssm install FTS python fts/cli.py scheduler run` | 生产稳定 |
| 后台进程 | `pythonw fts/cli.py scheduler run` + stdio 重定向 | 轻量部署 |

### 6.7 HTTP 监控端点

运行时 FTS 暴露 HTTP 服务于 `127.0.0.1:9100`:

| 端点 | 格式 | 内容 |
|---|---|---|
| `GET /health` | JSON | 健康状态 + L1/L2/L3 循环摘要 |
| `GET /metrics` | Prometheus 文本 | L1/L2/L3 status gauge, token 消耗 counter |
| `GET /` | HTML | 仪表板 (状态表格) |

---

## 7. 核心设计模式

### 7.1 Verifier 锁定协议

**核心防博弈机制。** `FactorVerifier`, `L1Verifier`, `L3Verifier` 都在 `__init__` 末尾设置 `_locked=True`。任何后续修改配置的尝试抛出 `VerifierAlreadyLockedError`。确保评估标准无法被 LLM (或人类) 在运行中博弈。

- `get_global_verifier()` — 进程级单例, `DEFAULT_VERIFIER_CONFIG` 初始化
- `check()` 返回 `checked_against` 快照, 可审计

### 7.2 Loop Engineering 范式

三个自治循环 (L1/L2/L3) 具有不同的节奏 (每日/每夜/每周), 由人在回路顶层 (L0 Program.md) 监督。每个循环有独立的:
- `StateManager` — 状态持久化 + 备份镜像
- `Verifier` — 锁定的判定标准
- `Budget` — 熔断预算控制

文件: `evolution_loop.py`, `meta_loop.py`, `portfolio_loop.py`

### 7.3 安全沙箱执行

**禁止 LLM 生成的因子代码执行危险操作。**

- 白名单导入: `numpy`, `pandas`, `scipy`, `statsmodels`, `talib`, `math`, `statistics`
- 黑名单名称: `open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals`
- 黑名单模块: `os`, `sys`, `subprocess`, `socket`, `ctypes`, `pickle`
- AST 预验证: `validate_factor_code()` 在任何执行前检测违规
- 受限 `__builtins__` 字典

文件: `factor_program.py`

### 7.4 Strategy v2 可插拔框架

`BaseStrategyV2` ABC 定义 `name` (抽象), `score()` (抽象), 并提供默认 `compute()`, `filter()`, `validators`, `weight`, `depends_on`。新策略扩展 ABC 并只覆盖需要的部分。

- `StrategyV1Adapter` — 适配器模式, 桥接 v1 策略接口到 v2 ABC
- `MultiFactorStrategy` — 12 因子实现, 3 种模式 (pure_momentum / long_short / neutral)

文件: `strategies/base_v2.py`, `strategies/multi_factor_strategy.py`

### 7.5 Pipeline + Stage Protocol

`ProcessingStage` 是一个 `Protocol`, 含 `input_type`/`output_type` 和 `process(payload)` 方法。`FactorPipeline` 是 ABC, 含抽象 `build_stages()` 和具体 `run()` 编排器, 返回 `PipelineResult`。Stage 可组合, 通过 Protocol 进行类型安全校验。

文件: `pipeline/base.py`

### 7.6 适配器模式

`StrategyV1Adapter` (在 `strategies/base_v2.py` 中) 桥接 v1 策略接口到 v2 ABC, 允许渐进迁移而不破坏现有策略。

### 7.7 原子文件操作

`atomic_write()` 临时文件 + `os.replace()` (跨平台原子 rename)。`atomic_write_state()` 添加备份轮转 (`.bak.0` → `.bak.1` → `.bak.2`), 实现崩溃安全的状态持久化。

所有状态管理器 (`EvolutionStateManager`, `MetaStateManager`, `PortfolioStateManager`) 使用此原语。

文件: `core/atomic.py`

### 7.8 单例全局 Verifier

`get_global_verifier()` 返回以 `DEFAULT_VERIFIER_CONFIG` 初始化的进程级单例。确保所有 L2 运行间评估标准一致。`reset_global_verifier()` 仅用于测试。

文件: `factor_engine/verifier.py`

### 7.9 经验链 (LLM 记忆)

`ExperienceChain` 在独立子目录中存储成功和失败追踪。`read_recent_for_llm()` 返回 10 条成功 + 10 条失败追踪作为下次 LLM 调用的 Markdown 上下文, 防止 LLM 重复过去的错误。

| 属性 | 值 |
|---|---|
| MAX_CHAIN_SIZE | 100 条 |
| 淘汰策略 | FIFO, 满时淘汰最旧的 20 条 |
| 存储目录 | `success/` + `failure/` 子目录 |
| 调用上下文 | 每次 LLM 调用前注入经验链摘要 |

文件: `factor_engine/experience_chain.py`

### 7.10 熔断器 (Circuit Breaker)

三阈值自动停止 L2 演化, 触发后须人类介入恢复:

| 熔断条件 | 阈值 |
|---|---|
| Token 预算耗尽 | `nightly_token_limit` (默认 200K) |
| 连续低 IC | `circuit_breaker_consecutive_low_ic` (默认 3 代) + `circuit_breaker_low_ic_threshold` (默认 0.01) |
| 失败率超限 | `circuit_breaker_failure_rate` (默认 90%) |

L1 和 L3 也有各自的熔断配置 (见 `L1BudgetConfig` / `L3` 常驻 budget)。

文件: `factor_engine/evolution_loop.py` (`_check_circuit_breaker`)

### 7.11 静默降级 (Graceful Degradation)

所有可选依赖都惰性导入, 缺失时优雅回退到 Mock/No-op 实现:

| 可选依赖 | 缺失时行为 |
|---|---|
| `optuna` | Micro 演化跳过, 使用默认参数 |
| `openai` / `anthropic` | 回退 `MockLLMClient` (确定性输出) |
| `datacore` | 回退合成 OHLCV 数据 |
| `apscheduler` | `SchedulerEngine.start()` 返回 False |
| `watchdog` | `HotSwapWatcher` 静默不工作 |
| `lightgbm` | L3 回退到等权/夏普加权 |

系统在零可选依赖安装的情况下仍可端到端运行。

### 7.12 备份轮转 (Backup Rotation)

所有状态文件通过 `atomic_write_state()` 写入时自动执行备份轮转:

```
state.json          ← 最新版本
state.json.bak.0    ← 上一次写入
state.json.bak.1    ← 上上一次写入
state.json.bak.2    ← 上上上一次写入
```

文件: `core/atomic.py`

---

## 8. 配置体系

### 8.1 配置层次与优先级

```
高优先级         环境变量 (FTS_* 前缀)
    ↑           YAML 配置文件 (config/settings.yaml)
    ↑           代码默认值 (FTSConfig dataclass)
低优先级
```

### 8.2 配置文件清单

| 文件 | 用途 | 说明 |
|---|---|---|
| `pyproject.toml` | Python 项目构建 + 依赖 + CLI 入口点 | `name="fts"`, `version="1.0.0"`, `scripts: fts = "fts.cli:main"` |
| `config/settings.yaml` | 默认 YAML 配置 | 被 `load_config()` 消费 |
| `fts/config/settings.py` | 配置加载器 | `FTSConfig` dataclass, `load_config()`, `get_config()` |
| `CLAUDE.md` | AI 编码行为准则 | HARNESS 规范 + 13 项 commit 前检查清单 |
| `.github/workflows/ci.yml` | GitHub Actions CI | Python 3.10/3.11/3.12 矩阵 |
| `start_fts.ps1` | PowerShell 启动脚本 | 设置环境变量后启动 FTS |

### 8.3 `pyproject.toml` 关键配置

```toml
[project]
name = "fts"
version = "1.0.0"
requires-python = ">=3.10"
dependencies = ["numpy>=1.24", "pandas>=2.0", "PyYAML>=6.0"]

[project.optional-dependencies]
evolution = ["optuna"]
llm = ["openai", "anthropic"]
data = ["datacore"]
dev = ["pytest", "pytest-cov"]

[project.scripts]
fts = "fts.cli:main"
```

### 8.4 `config/settings.yaml` 示例

```yaml
default_market: "futures"
llm_backend: "openai"
max_generations: 10
micro_trials_per_generation: 50
portfolio_max_factors: 20
```

### 8.5 运行时配置 (memory/ 目录)

所有运行时状态文件存储在 `memory/` 目录下, 通过 `FTS_MEMORY_DIR` 环境变量可配置。

---

## 9. 运行时状态文件

### 9.1 状态文件清单

| 文件路径 | 用途 | 拥有者 |
|---|---|---|
| `memory/evolution/state.json` | L2 演化状态 (代数/试验/计数/熔断) | `EvolutionStateManager` |
| `memory/evolution/state.json.backup` | L2 状态备份 | `atomic_write_state` |
| `memory/meta_loop/state.json` | L1 Meta-Loop 状态 | `MetaStateManager` |
| `memory/meta_loop/state.json.backup` | L1 状态备份 | `atomic_write_state` |
| `memory/portfolio/state.json` | L3 组合状态 | `PortfolioStateManager` |
| `memory/portfolio/state.json.backup` | L3 状态备份 | `atomic_write_state` |
| `memory/portfolio/current_combo.json` | 当前 L3 组合 combo | `PortfolioManager` |
| `memory/portfolio/agent_proposals/*.json` | LLM 生成的组合优化提案 | `PortfolioManager` |
| `memory/knowledge/factors/factor_pool.json` | L1 发现的因子池 | `FactorPoolManager` |
| `memory/knowledge/factors/elite/*.json` | 晋升的 elite 因子 (每因子一文件) | `EvolutionLoop._promote_to_elite()` |
| `memory/knowledge/factors/l1_injected/*.json` | L1 注入的因子候选 | `MetaLoop` |
| `memory/meta_loop/debate_journal.json` | 辩论质量记录 | `DebateQualityAnalyzer` |
| `memory/experience/success/*.json` | 成功经验追踪 (LLM 上下文) | `ExperienceChain` |
| `memory/experience/failure/*.json` | 失败经验追踪 (LLM 上下文) | `ExperienceChain` |
| `Program.md` (项目根目录) | L0 周度人工设定 | 人类 (由 `program.py` 解析) |

### 9.2 状态文件格式示例

**L2 Evolution State (memory/evolution/state.json):**

```json
{
  "run_id": "run_abc12345_20260724T120000",
  "started_at": "2026-07-24T12:00:00",
  "last_generation": 5,
  "total_factors_evaluated": 25,
  "total_factors_promoted": 3,
  "tokens_consumed": 45000,
  "budget_limit": 200000,
  "status": "completed",
  "last_error": null,
  "experience_chain_ref": ["ftr_...", "ftr_..."],
  "last_updated": "2026-07-24T12:30:00",
  "version": "8.10.0"
}
```

**Elite Factor (memory/knowledge/factors/elite/fct_a1b2c3d4.json):**

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
  "created_at": "2026-07-24T12:00:00"
}
```

---

## 总结

FTS 是一个架构清晰的 AI 原生量化因子系统, 核心特点:

1. **Contract-First 设计** — 所有数据形状在 `contracts.py` 中声明为 TypedDict (L1+L2+L3 三层, ~560 LOC), 模块间通过契约解耦
2. **安全防博弈** — Verifier 锁定协议 + 安全沙箱 + 熔断器 + 经验链, 防止 LLM 作弊和恶意代码执行
3. **工程韧性** — 原子文件持久化 + 备份轮转 + 静默降级 + 进程看门狗 + 热重载
4. **可观测性** — `trace_id` 全链路追踪 + HTTP metrics 服务器 (端点: /health /metrics /) + Elite 因子自动退役追踪
5. **可扩展性** — Strategy v2 可插拔 ABC + Pipeline + Stage Protocol + 适配器模式
6. **测试覆盖** — 1,231 测试 / 96% 覆盖率 / 28 个测试文件 / 10 个 E2E 场景 / CI 矩阵 (3.10/3.11/3.12)

项目处于生产就绪状态, 已在 Windows 上部署 3 种模式 (Task Scheduler / NSSM / 后台进程), 通过 GitHub Actions 持续集成。