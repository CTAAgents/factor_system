# FTS Code Wiki

> **Factor Trading System — 因子智能系统完整技术文档**

**版本**: v2.2.0  
**最后更新**: 2026-08-04  
**测试状态**: 1850+ 通过 / 99% 覆盖率  
**代码规模**: ~5,000 语句 / 85+ 文件

---

## 目录

1. [项目概述](#项目概述)
2. [整体架构](#整体架构)
3. [核心模块详解](#核心模块详解)
4. [关键类与函数](#关键类与函数)
5. [依赖关系](#依赖关系)
6. [运行方式](#运行方式)
7. [核心设计模式](#核心设计模式)
8. [配置体系](#配置体系)
9. [运行时状态](#运行时状态)

---

## 项目概述

FTS（Factor Trading System）是一个 **AI 原生的量化因子智能系统**，实现从因子发现、评估、组合到信号产出的全自动化三层进化循环：

- **L1 Meta-Loop** — 每日市场感知与知识补给（Web 感知 + Bootstrapping + debate 分析）
- **L2 Evolution Loop** — 夜间因子自动演化（LLM 宏观改逻辑 + optuna 微观调参）
- **L3 Portfolio Loop** — 组合构建与信号产出（正交化 + 衰减检验 + 加权融合）

**项目定位**：MCP/akshare（腾讯/东方财富数据源）← FTS（因子智能 → 交易信号）→ 下游消费系统

**支持市场**：
- A 股（CSI300 横截面因子演化）
- ETF（18 只常见 ETF）
- 期货（82 个品种：25 核心 + 57 全量，支持多空双向信号）

**种子因子体系**：
- **股票种子因子**：482 个（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面/另类/宏观）
- **期货种子因子**：28 个（8 核心 + 4 备选 + 9 机构 + 7 CTA 注册表补充）

---

## 整体架构

### 四层循环架构

```
┌─────────────────────────────────────────────────────────┐
│ L0: 数据层 (Data Layer)                                  │
│ - MCP/akshare 数据源（腾讯自选股/东方财富）                │
│ - DuckDB 期货数据存储（kline_cache 表）                   │
│ - 基本面数据注入（估值/财务/宏观/库存/仓单/基差）          │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ L1: Meta-Loop (元循环)                                   │
│ - 每日 09:00 触发                                        │
│ - Web 感知 + Bootstrapping + debate 分析                 │
│ - 知识补给（L1 注入因子到 l1_injected/）                  │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ L2: Evolution Loop (演化循环)                            │
│ - 每日 23:00 触发                                        │
│ - LLM 宏观改逻辑 + optuna 微观调参                       │
│ - 三级评估链（快速→标准→严格）                           │
│ - 熔断器保护（连续 5 代 IC<0.005 触发）                  │
│ - 精英因子输出到 elite/ 或 futures_elite/                 │
└─────────────────────────────────────────────────────────┘
                          ↓
┌─────────────────────────────────────────────────────────┐
│ L3: Portfolio Loop (组合循环)                            │
│ - 每周一 06:00 触发                                      │
│ - 因子正交化（剔除相关性>0.7 的因子）                     │
│ - Ridge 回归学习因子权重                                 │
│ - Market Regime 检测（bull/bear/oscillate/high_vol/low_vol）│
│ - 盲测品种验证（泛化能力检查）                           │
│ - 信号输出到 reports/{date}/                              │
└─────────────────────────────────────────────────────────┘
```

### 五层分层架构

| 层级 | 职责 | 关键模块 |
|------|------|----------|
| **入口层** | CLI 命令解析、调度器启动 | `fts.cli`, `fts.scheduler` |
| **L0 数据层** | 数据获取、清洗、缓存 | `fts.data`, `fts.data_mcp`, `fts.data_futures`, `fts.data_fundamental` |
| **L1 元循环层** | 市场感知、知识补给 | `fts.factor_engine.meta_loop` |
| **L2 演化层** | 因子演化、评估、验证 | `fts.factor_engine.evolution_loop`, `fts.factor_engine.macro_evolution`, `fts.factor_engine.micro_evolution` |
| **L3 组合层** | 组合构建、信号产出 | `fts.factor_engine.portfolio_loop`, `scripts/futures_signal_pipeline.py` |

### 数据流

```
AKShare (futures_zh_daily_sina)
    ↓
DuckDB (data/fts_history.duckdb, kline_cache 表)
    ↓
FuturesDataProvider._from_kline_cache()
    ↓
EvolutionLoop (L2 演化)
    ↓
futures_elite/ (精英因子库)
    ↓
futures_signal_pipeline.py (信号管道)
    ↓
reports/{date}/futures_signals_{date}.md (信号报告)
```

---

## 核心模块详解

### 1. fts/core — 核心契约层

**职责**: 定义 FTS 系统的核心类型契约和枚举，是所有模块的基础依赖。

**关键文件**:
- `contracts.py` — TypedDict 契约定义（FactorProgram, BacktestResult, ScoredSignal 等）
- `enums.py` — 枚举定义（Market, SignalType, Grade 等）
- `atomic.py` — 原子操作工具函数

**核心契约**:

```python
# FactorProgram — 因子程序契约
class FactorProgram(TypedDict):
    factor_id: str          # 唯一标识（SHA256 哈希）
    name: str               # 因子名称
    code: str               # Python 代码字符串
    signature: str          # 函数签名（如 "ohlcv"）
    economic_logic: str     # 经济逻辑描述
    source: str             # 来源（"seed" | "evolved" | "l1_injected"）
    backtest: BacktestResult # 回测结果

# BacktestResult — 回测结果契约
class BacktestResult(TypedDict):
    ic: float               # 信息系数（IC）
    sharpe: float           # 夏普比率
    t_stat: float           # t 统计量
    turnover: float         # 换手率
    drawdown: float         # 最大回撤
    returns: list[float]    # 收益序列

# ScoredSignal — 打分信号契约（L3 输出）
class ScoredSignal(TypedDict):
    symbol: str             # 标的代码
    direction: str          # 方向（"long" | "short"）
    signal_type: str        # 信号类型
    strategy_name: str      # 策略名称
    total: float            # 综合得分
    abs_score: float        # 绝对得分
    grade: str              # 等级（STRONG/WATCH/WEAK）
    weight: float           # 权重
```

---

### 2. fts/factor_engine — 因子引擎层

**职责**: FTS 的核心智能层，实现 L1/L2/L3 三层循环的因子发现、演化、组合逻辑。

#### 2.1 演化循环模块

**evolution_loop.py** — L2 演化主循环
- `EvolutionLoop` 类：协调宏观演化和微观演化
- 关键方法：`run()`, `_run_generation()`, `_check_circuit_breaker()`
- 熔断器逻辑：连续 5 代 IC<0.005 触发终止

**macro_evolution.py** — 宏观演化（LLM 驱动）
- `MacroEvolution` 类：使用 LLM 修改因子逻辑
- 关键方法：`evolve()`, `_prompt_llm()`, `_parse_new_factor()`
- 依赖 `fts.llm.LLMClient` 调用 OpenAI/Anthropic API

**micro_evolution.py** — 微观演化（optuna 调参）
- `MicroEvolution` 类：使用 optuna 贝叶斯优化因子参数
- 关键方法：`optimize()`, `_objective()`, `_suggest_params()`
- 依赖 `optuna` 库（可选依赖）

**meta_loop.py** — L1 元循环
- `MetaLoop` 类：每日市场感知与知识补给
- 关键方法：`run()`, `_web_perception()`, `_bootstrapping()`, `_debate_analysis()`
- 输出 L1 注入因子到 `memory/knowledge/factors/l1_injected/`

**portfolio_loop.py** — L3 组合循环
- `PortfolioLoop` 类：组合构建与信号产出
- 关键方法：`run()`, `_orthogonalize()`, `_compute_weights()`, `_generate_signals()`
- 依赖 `fts.factor_engine.regime.RegimeAwareSelector` 进行 Market Regime 检测

#### 2.2 评估与验证模块

**evaluation_chain.py** — 三级评估链
- `EvaluationChain` 类：串联快速→标准→严格三级评估
- 关键方法：`evaluate()`, `_fast_eval()`, `_standard_eval()`, `_strict_eval()`
- 快速评估：IC>0.02 即可通过（筛选明显无效因子）
- 标准评估：IC>0.03, Sharpe>1.5, t_stat>2.0
- 严格评估：IC>0.05, Sharpe>2.0, t_stat>3.0（精英因子门槛）

**verifier.py** — 因子验证器
- `Verifier` 类：独立验证因子有效性（锁定机制）
- 关键方法：`verify()`, `_check_correlation()`, `_check_overfitting()`
- **锁定机制**: 一旦 Verifier 批准因子，后续不可撤销（防止过拟合）

**causal_validator.py** — 因果验证器
- `CausalValidator` 类：检验因子因果关系（非伪相关）
- 关键方法：`validate_causality()`, `_granger_test()`, `_shap_importance()`
- 依赖 `fts.factor_engine.shap_analyzer.SHAPAnalyzer`

**walk_forward.py** — 滚动前进验证
- `WalkForwardValidator` 类：时间序列交叉验证（防止前视偏差）
- 关键方法：`validate()`, `_split_folds()`, `_compute_oos_performance()`

**ablation.py** — 消融测试
- `AblationTester` 类：测试因子在不同子样本上的稳健性
- 关键方法：`test()`, `_subsample_test()`, `_regime_test()`

**robustness.py** — 稳健性检验
- `RobustnessChecker` 类：多市场、多周期稳健性检验
- 关键方法：`check()`, `_cross_market_test()`, `_cross_period_test()`

**stress_test.py** — 压力测试
- `StressTester` 类：极端市场环境下的因子表现
- 关键方法：`test()`, `_crisis_test()`, `_high_vol_test()`

#### 2.3 因子管理与执行模块

**factor_program.py** — 因子执行器
- `FactorExecutor` 类：编译、执行因子代码，计算因子值
- 关键方法：`compile()`, `execute()`, `_safe_eval()`
- **安全沙箱**: 使用受限的 Python 执行环境（禁止 import、文件操作等）

**seed_pool.py** — 种子池管理
- `SeedPool` 类：管理所有种子因子（内置 + 外部）
- 关键方法：`load_all_seeds()`, `_filter_by_ic()`, `_promote_to_elite()`
- 支持 `include_external` 参数控制是否加载外部种子（wq101, qlib158, gtja191）

**standardizer.py** — 因子标准化
- `Standardizer` 类：因子值标准化（z-score, rank, min-max）
- 关键方法：`standardize()`, `_zscore()`, `_rank()`, `_minmax()`

**experience_chain.py** — 经验链
- `ExperienceChain` 类：记录历史演化经验（成功/失败模式）
- 关键方法：`record()`, `query()`, `_extract_pattern()`
- 用于指导 LLM 生成新因子时避免重复错误

**monitor.py** — 演化监控
- `EvolutionMonitor` 类：实时监控演化过程指标
- 关键方法：`record_generation()`, `get_stats()`, `_check_stagnation()`

**regime.py** — Market Regime 检测
- `RegimeAwareSelector` 类：检测市场制度（bull/bear/oscillate/high_vol/low_vol）
- 关键方法：`detect_regime()`, `_compute_features()`, `_classify()`
- 依赖 HMM（隐马尔可夫模型）或规则-based 分类

**cost_model.py** — 交易成本模型
- `CostModel` 类：估算交易成本（手续费、滑点、冲击成本）
- 关键方法：`estimate()`, `_commission()`, `_slippage()`, `_impact()`

**shap_analyzer.py** — SHAP 归因分析
- `SHAPAnalyzer` 类：使用 SHAP 值解释因子贡献
- 关键方法：`analyze()`, `_compute_shap_values()`, `_plot_importance()`
- 依赖 `shap` 库

**state.py** — 状态管理
- `EvolutionState` 类：持久化演化状态（代际、最佳因子、熔断器计数）
- 关键方法：`save()`, `load()`, `reset()`
- 状态文件：`memory/evolution/state.json`

**program.py** — 因子程序工具
- `FactorProgram` 相关工具函数
- 关键函数：`validate_program()`, `compute_factor_id()`, `serialize()`

#### 2.4 种子因子定义

**seed_data/** — 种子因子代码库
- `__init__.py` — 种子池初始化
- `loader.py` — 种子加载器（`_list_base_seeds()` 机制）
- `alpha_ops.py` — 基础算子（ts_rank, ts_corr, ts_delta 等）
- `wq101.py` — 世坤 101 因子（101 个量价因子）
- `qlib158.py` — Qlib 158 因子（158 个量价因子）
- `gtja191.py` — 国泰君安 191 因子（191 个量价因子）
- `fundamental_seeds.py` — 23 个基本面/另类/宏观因子

**seed_data_futures_full.py** — 期货种子因子（57 个，13大因子家族）

13 大因子家族：
1. 动量因子（momentum）— 5 个
2. 期限结构（term_structure）— 3 个
3. 持仓因子（open_interest）— 3 个
4. 波动率（volatility）— 2 个
5. 量价因子（volume_price）— 3 个
6. 基差因子（basis）— 3 个
7. 资金流（money_flow）— 3 个
8. 高频因子（high_frequency）— 6 个
9. 情绪因子（sentiment）— 3 个
10. 拥挤度（crowding）— 6 个
11. 期权 PCR（option_pcr）— 3 个
12. 市场环境（market_regime）— 9 个
13. CTA 注册表补充（CTA_registry）— 7 个

---

### 3. fts/data* — 数据层

#### 3.1 fts/data.py — 数据集成入口

**职责**: FTS 数据层统一入口，基于腾讯自选股 MCP 提供统一数据访问接口。

**关键类**:
- `FTSDataProvider` — 统一数据提供者
  - `get_ohlcv(symbol, market, start, end)` — 获取 OHLCV 数据
  - `get_csi300_panel(days)` — 获取 CSI300 面板数据
  - `get_futures_panel(days)` — 获取期货面板数据
  - `inject_fundamental(panel)` — 注入基本面数据

**数据源优先级**:
1. MCP (腾讯自选股 HTTP API)
2. AKShare (futures_zh_daily_sina)
3. 合成数据（降级回退）

#### 3.2 fts/data_mcp.py — 腾讯 MCP 适配层

**职责**: 腾讯自选股 HTTP API 适配层，提供 A 股和 ETF 的 OHLCV 数据。

**关键类**:
- `MCPDataProvider` — MCP 数据提供者
  - `get_ohlcv(symbol, start, end)` — 获取单标的 OHLCV
  - `get_etf_ohlcv(symbol, start, end)` — ETF 专用接口
  - `get_panel(symbols, days)` — 批量面板数据

**API 端点**:
- `qt.gtimg.cn` — 腾讯行情 API
- `web.ifzq.gtimg.cn` — 腾讯 K 线 API

**代码转换**:
- `_to_tencent_code("000001")` → `"sz000001"`
- `_to_tencent_code("600000")` → `"sh600000"`

#### 3.3 fts/data_futures.py — 期货数据层

**职责**: 期货数据提供者，基于 DuckDB 的 kline_cache 表提供期货连续合约 OHLCV 数据。

**关键类**:
- `FuturesDataProvider` — 期货数据提供者
  - `_from_kline_cache(symbol, start, end)` — 从 DuckDB 读取（优先级 1）
  - `_from_akshare(symbol, start, end)` — 从 AKShare 即时获取（优先级 2）
  - `synthesize_ohlcv(symbol, start, end)` — 合成数据（降级回退）
  - `get_panel(symbols, days)` — 批量面板数据

**DuckDB 表结构**:
```sql
CREATE TABLE kline_cache (
    symbol TEXT,          -- 连续合约代码（如 RB0, TA0）
    date TEXT,            -- 日期（YYYY-MM-DD）
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    amount REAL,          -- 成交额
    settle REAL,          -- 结算价（部分品种有）
    vwap REAL             -- VWAP（计算字段：amount/volume 或 (H+L+C)/3）
);
```

**品种池**:
- `FUTURES_SUBSET` — 82 个期货连续合约（全量）
- `FUTURES_CORE_SUBSET` — 25 个常用品种（流动性好，用于演化训练）
- `FUTURES_HOLDOUT` — 6 个盲测品种（不参与训练，用于泛化验证）
- `FUTURES_SECTOR_MAP` — 产业链分类映射（黑色系/有色金属/能源化工/农产品/贵金属/新能源新材料/金融期货）

**VWAP 计算逻辑**:
- `_from_kline_cache`: `vwap = amount / NULLIF(volume, 0)`，回退到 `(H+L+C)/3`（典型价格）
- `_from_akshare` 和 `synthesize_ohlcv`: `vwap = (H+L+C+settle)/4`
- **风险标签**: VWAP 相关因子自动标记 `risk_tag="vwap_approx"`，要求 IC≥0.08（而非 0.03）才能晋升精英

#### 3.4 fts/data_fundamental.py — 基本面数据层

**职责**: 为 FTS 因子引擎提供基本面数据（估值、财务、宏观）的获取与注入。

**关键类**:
- `FundamentalProvider` — 基本面数据提供者
  - `inject_ohlcv(panel)` — 注入基本面字段到 OHLCV 面板
  - `_from_mcp(symbol)` — 从 MCP westock 工具获取
  - `_synthesize_fundamental(symbol)` — 合成数据降级

**基本面字段**:
- `VALUATION_FIELDS` — 估值类（pe_ttm, pb, ps_ttm, pcf_ttm）
- `SIZE_FIELDS` — 市值类（total_market_cap, free_market_cap, circulating_market_cap）
- `TRADING_FIELDS` — 交易类（turnover_rate, volume_ratio, amplitude）
- `QUALITY_FIELDS` — 财务质量类（roe, roa, gross_margin, net_margin, debt_to_equity, current_ratio, eps, bps）
- `GROWTH_FIELDS` — 成长类（revenue_growth, profit_growth, asset_growth）
- `MACRO_FIELDS` — 宏观类（pmi, cpi, gdp_growth, m2_growth, shibor_1y, lpr_1y）

#### 3.5 fts/data_futures_fundamental.py — 期货基本面数据层

**职责**: 期货基本面数据提供者，基于 AKShare 提供库存、仓单、现货基差等数据。

**关键类**:
- `FuturesFundamentalProvider` — 期货基本面数据提供者
  - `get_inventory(symbol)` — 库存数据
  - `get_basis(symbol)` — 现货基差
  - `get_warehouse_receipt(symbol)` — 仓单数据

**映射表**:
- `INVENTORY_SYMBOL_MAP` — FTS 代码 → AKShare 库存 API 中文名
- `BASIS_SYMBOL_MAP` — FTS 代码 → AKShare 基差 API 品种代码

#### 3.6 fts/data_mcp_bridge.py — MCP 数据桥接层

**职责**: 桥接 FTS 因子引擎与 TRAE MCP 数据源（东方财富妙想 mx API），通过本地缓存机制支持 Agent 预填充数据后由 FTS 代码读取。

**关键类**:
- `MCPBridge` — MCP 数据桥接器
  - `get_fundamental(symbol)` — 获取单只股票基本面数据
  - `get_batch(symbols)` — 批量获取
  - `get_cache_age()` — 查询缓存年龄

**缓存文件**: `data/fundamental_cache.json`

---

### 4. fts/llm.py — LLM 客户端集成

**职责**: 提供统一的 LLM 调用接口，支持 OpenAI / Anthropic 两种后端。

**关键类**:
- `LLMClient` — LLM 客户端抽象基类
  - `complete(prompt, max_tokens)` — 文本补全
  - `generate_json(prompt, schema)` — JSON 格式输出

- `OpenAIClient` — OpenAI API 客户端
  - 环境变量配置：`OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`

- `AnthropicClient` — Anthropic Claude API 客户端
  - 环境变量配置：`ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`

- `MockLLMClient` — 模拟客户端（用于测试）

**工厂函数**:
- `get_llm_client(backend="openai")` — 获取 LLM 客户端实例

---

### 5. fts/pipeline — 因子推演管线

#### 5.1 fts/pipeline/base.py — 管线抽象基类

**职责**: 定义 FTS 因子计算层的核心契约，输入为 Data-Core 已加工的结构化数据，输出为因子输入数据。

**关键类**:
- `DataPayload` — 数据载荷（Data-Core 与 FTS 管线之间的标准传输对象）
  - `data_type: str` — 数据类型（"ohlcv" | "fundamental" | "panel"）
  - `symbol: str` — 标的代码
  - `payload: dict` — 实际数据
  - `metadata: dict` — 元数据
  - `trace_id: str` — 全链路追踪 ID

- `FactorPipeline` — 因子推演管线
  - `run(payload)` — 按顺序运行所有 stage

#### 5.2 fts/pipeline/factor_combiner.py — 多因子融合器

**职责**: 多因子加权/融合，输入为多个因子得分，输出为组合因子得分。

**关键类**:
- `CombinerConfig` — 组合器配置
  - `weights: dict[str, float]` — 因子权重
  - `normalize_inputs: bool` — 是否标准化输入
  - `clip_sigma: float` — 截断标准差（防止极端值）
  - `orthogonalize: bool` — 是否正交化
  - `min_active_factors: int` — 最小活跃因子数

- `FactorCombiner` — 多因子融合器
  - `combine(factors, config)` — 加权融合

- `CombineResult` — 组合结果
  - `combined_scores: dict[str, float]` — 综合得分
  - `active_counts: int` — 活跃因子数

---

### 6. fts/strategies — 策略层

#### 6.1 fts/strategies/base_v2.py — v2 策略框架

**职责**: v2 策略可插拔框架，每个策略是自包含的 compute → filter → score 三段式模块。

**关键类**:
- `BaseStrategyV2` — v2 策略基类
  - `compute(panel)` — 计算原始信号
  - `filter(signals)` — 过滤信号
  - `score(signals)` — 打分信号

- `RawSignal` — 原始信号（未过滤未打分）
- `ScoredSignal` — 打分信号（已过滤+打分，准备交付融合）

- `StrategyV1Adapter` — v1 策略适配器（向后兼容）

#### 6.2 fts/strategies/multi_factor_strategy.py — 多因子量化策略

**职责**: 多因子量化策略，四维因子加权打分预测品种未来收益。

**关键类**:
- `MultiFactorStrategy` — 多因子量化策略
  - 继承 `BaseStrategyV2`
  - 因子体系：量价 40%、产业 30%、宏观 20%、另类 10%
  - 支持模式：`pure_momentum`, `long_short`, `neutral`

**因子计算函数**:
- `_calc_momentum()` — 动量因子（价格变化率 + MA 斜率 + MACD 交叉）
- `_calc_volatility_reversion()` — 波动率因子（高波动后回归预期）
- `_calc_volume_flow()` — 资金流因子（成交量变化 + 持仓倾向）
- `_calc_oi_change()` — 持仓量变化因子
- `_calc_basis()` — 基差因子（期现价差方向）
- `_calc_macro()` — 宏观因子（宏观制度方向）
- `_calc_position_rank()` — 龙虎持仓因子
- `_calc_warrant_change()` — 仓单变化因子
- `_calc_inventory()` — 库存分位因子
- `_calc_capacity()` — 开工率因子
- `_calc_pmi_proxy()` — 制造业 PMI 景气度因子
- `_calc_rate_proxy()` — 利率因子（LPR1Y 代理）

#### 6.3 fts/strategies/strategy_evolution.py — 策略进化模块

**职责**: 动态因子权重、市场制度自适应、多周期信号融合。

**关键类**:
- `RegimeAdaptiveStrategy` — 市场制度自适应策略
  - 继承 `MultiFactorStrategy`
  - 使用 `RegimeAwareSelector` 检测市场制度
  - 根据制度选择最优权重（BULL_WEIGHTS, BEAR_WEIGHTS 等）

- `DynamicWeightStrategy` — 动态因子权重策略
  - 继承 `MultiFactorStrategy`
  - 跟踪各因子历史表现（IC 代理）
  - 指数衰减加权平均更新权重

- `MultiPeriodSignalFusion` — 多周期信号融合策略
  - 继承 `BaseStrategyV2`
  - 同时使用短周期（20 日）、中周期（60 日）、长周期（120 日）
  - 加权融合生成综合信号

**制度权重映射**:
- `BULL_WEIGHTS` — 牛市（加强动量，降低防御）
- `BEAR_WEIGHTS` — 熊市（加强防御，降低动量）
- `HIGH_VOL_WEIGHTS` — 高波动（加强波动率因子）
- `LOW_VOL_WEIGHTS` — 低波动（趋势跟踪为主）
- `OSCILLATE_WEIGHTS` — 震荡（侧重均值回复）

---

### 7. fts/scheduler — 调度层

**职责**: 基于 APScheduler 的全自动 L1/L2/L3 定时调度引擎。

**关键文件**:
- `engine.py` — 调度引擎（APScheduler 封装）
- `tasks.py` — 任务注册表（TaskRegistry）
- `jobs.py` — 任务定义（L1/L2/L3 任务）
- `hotswap.py` — 热更新机制（运行时更新因子库）
- `watchdog.py` — 看门狗（监控任务健康状态）

**定时任务**:
| 任务 | 调度时间 | 职责 |
|------|----------|------|
| L1 Meta-Loop | 每日 09:00 | 市场感知、知识补给 |
| L2 Evolution | 每日 23:00 | 因子演化 |
| L3 Portfolio | 每周一 06:00 | 组合构建、信号产出 |
| Health Check | 每 10 分钟 | 系统健康检查 |
| Elite Tracker | 每日 15:30 | 精英因子追踪 |

---

### 8. fts/monitor — 健康监控层

**职责**: 系统健康监控 + HTTP 端点 + Elite 因子追踪。

**关键文件**:
- `elite_tracker.py` — 精英因子追踪器（监控因子 IC 衰减）
- `http_server.py` — HTTP 监控端点（Koa 服务器）
- `logic_monitor.py` — 逻辑监控器（检测因子逻辑异常）

**HTTP 端点**:
- `GET /health` — 系统健康状态
- `GET /metrics` — 系统指标（因子数、演化代际、信号数）
- `GET /elite` — 精英因子列表
- `GET /signals` — 最新信号

---

### 9. fts/cli.py — 统一命令行入口

**职责**: FTS 统一命令行入口，解析子命令并路由到对应模块。

**CLI 命令树**:
```
fts
├── version              # 查看版本与配置
├── monitor              # 查看监控状态
├── meta-loop run        # L1 元循环（市场感知）
├── evolution run        # L2 因子演化
│   ├── --universe       # 横截面（csi300 | futures）
│   ├── --max-stocks     # 最大股票数（默认 0=全部）
│   ├── --max-generations# 最大代际（默认 10）
│   └── --synthesis-mode # 合成模式（ic_weight | sharpe_weight）
├── portfolio run        # L3 组合构建
│   ├── --universe       # 市场（futures | stock）
│   └── --synthesis-mode # 合成模式
├── factor list          # 查看 elite 因子
│   └── --market         # 市场（futures_elite | elite）
├── scheduler list       # 查看调度器任务
└── scheduler start      # 启动调度器
```

---

## 关键类与函数

### 核心契约类

| 类名 | 文件 | 职责 |
|------|------|------|
| `FactorProgram` | `fts/core/contracts.py` | 因子程序契约（factor_id/name/code/signature/economic_logic） |
| `BacktestResult` | `fts/core/contracts.py` | 回测结果契约（ic/sharpe/t_stat/turnover/drawdown） |
| `ScoredSignal` | `fts/core/contracts.py` | 打分信号契约（symbol/direction/total/grade） |
| `DataPayload` | `fts/pipeline/base.py` | 数据载荷（data_type/symbol/payload/metadata/trace_id） |

### 数据提供者类

| 类名 | 文件 | 职责 |
|------|------|------|
| `FTSDataProvider` | `fts/data.py` | 统一数据入口（A 股/ETF/期货） |
| `MCPDataProvider` | `fts/data_mcp.py` | 腾讯 MCP 适配（A 股/ETF OHLCV） |
| `FuturesDataProvider` | `fts/data_futures.py` | 期货数据（DuckDB + AKShare） |
| `FundamentalProvider` | `fts/data_fundamental.py` | 基本面数据（估值/财务/宏观） |
| `FuturesFundamentalProvider` | `fts/data_futures_fundamental.py` | 期货基本面（库存/仓单/基差） |
| `MCPBridge` | `fts/data_mcp_bridge.py` | MCP 数据桥接（东方财富 mx API） |

### 因子引擎核心类

| 类名 | 文件 | 职责 |
|------|------|------|
| `EvolutionLoop` | `fts/factor_engine/evolution_loop.py` | L2 演化主循环 |
| `MacroEvolution` | `fts/factor_engine/macro_evolution.py` | 宏观演化（LLM 驱动） |
| `MicroEvolution` | `fts/factor_engine/micro_evolution.py` | 微观演化（optuna 调参） |
| `MetaLoop` | `fts/factor_engine/meta_loop.py` | L1 元循环（市场感知） |
| `PortfolioLoop` | `fts/factor_engine/portfolio_loop.py` | L3 组合循环 |
| `EvaluationChain` | `fts/factor_engine/evaluation_chain.py` | 三级评估链 |
| `Verifier` | `fts/factor_engine/verifier.py` | 因子验证器（锁定机制） |
| `FactorExecutor` | `fts/factor_engine/factor_program.py` | 因子执行器（安全沙箱） |
| `SeedPool` | `fts/factor_engine/seed_pool.py` | 种子池管理 |
| `RegimeAwareSelector` | `fts/factor_engine/regime.py` | Market Regime 检测 |

### 策略类

| 类名 | 文件 | 职责 |
|------|------|------|
| `BaseStrategyV2` | `fts/strategies/base_v2.py` | v2 策略基类（compute/filter/score） |
| `MultiFactorStrategy` | `fts/strategies/multi_factor_strategy.py` | 多因子量化策略 |
| `RegimeAdaptiveStrategy` | `fts/strategies/strategy_evolution.py` | 市场制度自适应策略 |
| `DynamicWeightStrategy` | `fts/strategies/strategy_evolution.py` | 动态因子权重策略 |
| `MultiPeriodSignalFusion` | `fts/strategies/strategy_evolution.py` | 多周期信号融合策略 |

### LLM 客户端类

| 类名 | 文件 | 职责 |
|------|------|------|
| `LLMClient` | `fts/llm.py` | LLM 抽象基类 |
| `OpenAIClient` | `fts/llm.py` | OpenAI API 客户端 |
| `AnthropicClient` | `fts/llm.py` | Anthropic Claude API 客户端 |
| `MockLLMClient` | `fts/llm.py` | 模拟客户端（测试用） |

---

## 依赖关系

### 内部依赖图

```
fts.cli
    ├── fts.config.settings
    ├── fts.scheduler.engine
    ├── fts.factor_engine.meta_loop
    ├── fts.factor_engine.evolution_loop
    ├── fts.factor_engine.portfolio_loop
    └── fts.monitor

fts.factor_engine.evolution_loop
    ├── fts.factor_engine.macro_evolution
    │   └── fts.llm
    ├── fts.factor_engine.micro_evolution
    │   └── optuna (可选)
    ├── fts.factor_engine.evaluation_chain
    │   ├── fts.factor_engine.verifier
    │   └── fts.factor_engine.causal_validator
    ├── fts.factor_engine.seed_pool
    │   └── fts.factor_engine.seed_data.*
    ├── fts.factor_engine.factor_program
    └── fts.data

fts.data
    ├── fts.data_mcp
    ├── fts.data_futures
    ├── fts.data_fundamental
    │   └── fts.data_mcp_bridge
    └── fts.data_futures_fundamental

fts.strategies.multi_factor_strategy
    └── fts.strategies.base_v2

fts.strategies.strategy_evolution
    ├── fts.strategies.multi_factor_strategy
    └── fts.factor_engine.regime
```

### 外部依赖

**核心依赖**（必装）:
- `numpy>=1.24` — 数值计算
- `pandas>=2.0` — 数据处理
- `scipy>=1.10` — 科学计算
- `pyyaml>=6.0` — YAML 配置解析
- `shap>=0.46` — SHAP 归因分析

**可选依赖**:
| Extra | 功能 | 安装命令 |
|-------|------|----------|
| `evolution` | optuna 贝叶斯调参 | `pip install -e ".[evolution]"` |
| `llm` | LLM 客户端（openai/anthropic） | `pip install -e ".[llm]"` |
| `mcp` | MCP 数据源（akshare） | `pip install -e ".[mcp]"` |
| `dev` | 开发工具（pytest/pytest-cov） | `pip install -e ".[dev]"` |
| `portfolio` | 组合优化（scikit-learn） | `pip install -e ".[portfolio]"` |
| 全部 | 安装所有可选依赖 | `pip install -e ".[evolution,llm,mcp,dev,portfolio]"` |

**数据源依赖**:
- **腾讯自选股 MCP** — A 股/ETF OHLCV 数据（`qt.gtimg.cn`, `web.ifzq.gtimg.cn`）
- **AKShare** — 期货日线数据（`futures_zh_daily_sina`）、期货基本面数据
- **东方财富妙想 mx API** — 基本面数据（通过 TRAE MCP 工具）
- **DuckDB** — 期货历史数据存储（`data/fts_history.duckdb`）

---

## 运行方式

### 安装

```bash
# 克隆项目
git clone <repo-url>
cd factor_system

# 安装（开发模式）
pip install -e ".[evolution,llm,mcp,dev,portfolio]"

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
```

### 数据准备

```bash
# 下载期货数据到 DuckDB
python scripts/download_futures.py --subset  # 核心 25 品种
python scripts/download_futures.py            # 全量 82 品种

# 构建基本面缓存（需在 TRAE Agent 会话中配合 run_mcp）
python scripts/build_fundamental_cache.py
```

### 手动运行三层循环

```bash
# L1 Meta-Loop（市场感知）
fts meta-loop run

# L2 因子演化（单标的）
fts evolution run --max-generations 10

# L2 因子演化（横截面，CSI300）
fts evolution run --universe csi300 --max-stocks 20

# L2 期货因子演化
python scripts/run_futures_evolution.py --generations 10

# L3 组合构建（A 股）
fts portfolio run

# L3 期货组合构建
python scripts/futures_l3_portfolio.py --mode sharpe_weight

# 查看 elite 因子
fts factor list
fts factor list --market futures  # 期货精英因子
```

### 全自动调度模式

```bash
# 启动调度器（APScheduler 驱动 L1/L2/L3 自动执行）
fts scheduler start

# 查看调度器任务
fts scheduler list

# 查看监控状态
fts monitor
```

### 信号产出

```bash
# 期货每日信号（v5 Ridge 回归 + Regime 检测，最核心）
python scripts/futures_signal_pipeline.py --universe core

# A 股每日信号
python scripts/daily_signal_pipeline.py --max-stocks 100

# 因子重验证（检测退化因子）
python scripts/futures_factor_revalidation.py --ic-threshold 0.02
```

### 测试

```bash
# 运行全部测试
python -m pytest tests/ --no-cov --tb=short

# 运行测试并生成覆盖率报告
python -m pytest tests/ --cov=fts --cov-report=html

# 运行特定测试文件
python -m pytest tests/factor_engine/test_evolution_loop.py -v
```

### 文档一致性检查

```bash
# 检查 docs/harness/ 文档一致性
python scripts/verify_doc_consistency.py

# 单文件检查
python scripts/verify_doc_consistency.py --file docs/harness/01-architecture.md
```

---

## 核心设计模式

### 1. Verifier 锁定机制

**问题**: 因子在演化过程中可能过拟合，但后续无法检测。  
**解决**: 一旦 Verifier 批准因子进入 elite，后续不可撤销（锁定）。  
**实现**: `fts/factor_engine/verifier.py` 的 `Verifier.verify()` 方法。

### 2. 安全沙箱

**问题**: 因子代码可能包含恶意操作（文件读写、网络请求）。  
**解决**: 使用受限的 Python 执行环境（禁止 import、文件操作等）。  
**实现**: `fts/factor_engine/factor_program.py` 的 `FactorExecutor._safe_eval()` 方法。

### 3. 熔断器保护

**问题**: LLM 生成质量不足导致连续多代 IC 极低，浪费计算资源。  
**解决**: 连续 5 代 IC<0.005 触发熔断，提前终止演化。  
**实现**: `fts/factor_engine/evolution_loop.py` 的 `EvolutionLoop._check_circuit_breaker()` 方法。  
**配置**: `circuit_breaker_consecutive_low_ic=5`, `circuit_breaker_low_ic_threshold=0.005`

### 4. 经验链

**问题**: LLM 可能重复生成失败的因子逻辑。  
**解决**: 记录历史演化经验（成功/失败模式），指导 LLM 避免重复错误。  
**实现**: `fts/factor_engine/experience_chain.py` 的 `ExperienceChain` 类。

### 5. 三级评估链

**问题**: 快速筛选大量因子时，严格评估耗时过长。  
**解决**: 快速→标准→严格三级评估，逐级过滤。  
**实现**: `fts/factor_engine/evaluation_chain.py` 的 `EvaluationChain` 类。

### 6. Market Regime 检测

**问题**: 不同市场环境下因子表现差异大。  
**解决**: 检测市场制度（bull/bear/oscillate/high_vol/low_vol），选择制度最优权重。  
**实现**: `fts/factor_engine/regime.py` 的 `RegimeAwareSelector` 类。

### 7. 盲测品种验证

**问题**: 因子可能在训练品种上过拟合。  
**解决**: 保留 6 个盲测品种（`FUTURES_HOLDOUT`），不参与训练，仅用于泛化验证。  
**实现**: `scripts/futures_signal_pipeline.py` 的 `_compute_holdout_validation()` 函数。

### 8. 两层去重机制

**问题**: 重复的 elite 因子导致 Ridge 回归双重加权，降低稳健性。  
**解决**: 两层去重：SHA256 哈希（代码逻辑）+ 精确匹配（IC/Sharpe/t_stat）。  
**实现**: `scripts/futures_signal_pipeline.py` 的 `load_futures_elite_factors()` 函数。

### 9. VWAP 风险标签

**问题**: 期货连续合约的 VWAP 计算存在结构性缺陷（展期跳跃、日频信息不足）。  
**解决**: VWAP 相关因子自动标记 `risk_tag="vwap_approx"`，要求更高 IC 门槛（0.08 vs 0.03）。  
**实现**: `fts/data_futures.py` 的 VWAP 计算逻辑 + `scripts/futures_signal_pipeline.py` 的风险过滤。

### 10. trace_id 全链路追踪

**问题**: 跨模块调试困难。  
**解决**: 所有 CLI 子命令和工作流启动时生成 trace_id，贯穿所有模块、文档和日志。  
**实现**: `fts/factor_engine/state.py` 的 `generate_trace_id()` 函数。

---

## 配置体系

### 配置优先级

1. **环境变量**（最高优先级）— `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`
2. **YAML 配置文件** — `config/settings.yaml`
3. **默认值**（最低优先级）— 代码中的默认参数

### YAML 配置项

```yaml
# config/settings.yaml

default_market: "stock"           # 默认市场（stock | futures）

llm_backend: "openai"             # LLM 后端（openai | anthropic）

max_generations: 10               # L2 最大演化代际
population_size: 20               # 种群大小
micro_trials_per_generation: 50   # 每代微观演化试验数

meta_loop_interval_hours: 24      # L1 元循环间隔（小时）
meta_loop_max_tokens: 8000        # L1 最大 token 数

portfolio_max_factors: 20         # L3 最大因子数
portfolio_top_n: 5                # L3 Top N 因子
portfolio_decay_days: 90          # L3 因子衰减天数

log_level: "INFO"                 # 日志级别
log_file: "logs/fts.log"          # 日志文件
```

### 环境变量

```bash
# .env 文件

OPENAI_API_KEY="sk-..."
OPENAI_BASE_URL="https://api.openai.com/v1"
OPENAI_MODEL="gpt-4"

# 可选
ANTHROPIC_API_KEY="sk-ant-..."
ANTHROPIC_MODEL="claude-3-opus"
```

---

## 运行时状态

### 状态文件

| 文件路径 | 职责 |
|----------|------|
| `memory/evolution/state.json` | L2 演化状态（代际、最佳因子、熔断器计数） |
| `memory/meta_loop/state.json` | L1 元循环状态 |
| `memory/portfolio/current_combo.json` | L3 当前组合配置 |
| `memory/portfolio/factor_weights.json` | L3 因子权重 |
| `memory/knowledge/factors/elite/*.json` | A 股精英因子库 |
| `memory/knowledge/factors/futures_elite/*.json` | 期货精英因子库 |
| `memory/knowledge/factors/l1_injected/*.json` | L1 注入因子库 |
| `data/fts_history.duckdb` | 期货历史数据（kline_cache 表） |
| `data/fundamental_cache.json` | 基本面数据缓存 |
| `reports/{date}/futures_signals_{date}.md` | 期货信号报告 |
| `reports/{date}/signal_scores.json` | 信号得分（JSON） |
| `reports/signal_scores_history.jsonl` | 历史信号得分（JSONL） |

### 日志文件

- `logs/fts.log` — 主日志文件
- 日志格式：`%(asctime)s [%(levelname)s] %(name)s: %(message)s`

---

## 附录

### A. 期货品种列表

**核心 25 品种**（`FUTURES_CORE_SUBSET`）:
```
RB0, HC0, I0, J0, JM0, ZC0, SF0, SM0, FG0, SA0,
TA0, MA0, PP0, PE0, PVC0, BU0, RU0, SP0, AU0, AG0,
CU0, AL0, ZN0, PB0, NI0
```

**盲测 6 品种**（`FUTURES_HOLDOUT`）:
```
SC0, LU0, NR0, BC0, PK0, PG0
```

### B. CLI 命令速查

```bash
# 版本与配置
fts version

# 监控
fts monitor

# L1 元循环
fts meta-loop run

# L2 演化
fts evolution run
fts evolution run --universe csi300 --max-stocks 20
fts evolution run --universe futures --max-generations 10

# L3 组合
fts portfolio run
fts portfolio run --universe futures --synthesis-mode sharpe_weight

# 因子查看
fts factor list
fts factor list --market futures

# 调度器
fts scheduler list
fts scheduler start
```

### C. 脚本速查

```bash
# 数据准备
python scripts/download_futures.py --subset
python scripts/build_fundamental_cache.py

# 期货演化
python scripts/run_futures_evolution.py --generations 10

# 期货信号
python scripts/futures_signal_pipeline.py --universe core
python scripts/futures_l3_portfolio.py --mode sharpe_weight
python scripts/futures_strategy.py --mode ic_weight

# A 股信号
python scripts/daily_signal_pipeline.py --max-stocks 100

# 因子运维
python scripts/futures_factor_revalidation.py --ic-threshold 0.02
python scripts/analyze_elite.py

# 组合分析
python scripts/portfolio_backtest.py --save
python scripts/portfolio_analysis.py --regime

# 工程保障
python scripts/verify_doc_consistency.py
```

### D. 工程指标

| 指标 | 值 |
|------|:---:|
| **版本** | v2.2.0 |
| **测试通过数** | 1850+ / 1850+（100%） |
| **测试覆盖率** | 99%（46/47 模块 100%，1 模块 73% 需 MCP 网络环境） |
| **代码行数** | ~5,000 语句 |
| **文件数** | 85+ 个源码 + 测试文件 |
| **股票种子因子** | 482（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面/另类/宏观） |
| **期货种子因子** | 28 个（8 核心 + 4 备选 + 9 机构 + 7 CTA 注册表补充） |
| **期货精英因子** | 36 个（截至 2026-08-04） |

---

**文档维护**: 本文档随代码演进，每次架构变更后需同步更新。  
**贡献者**: FDT Team  
**许可证**: MIT License
