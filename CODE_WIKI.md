# FTS (Factor Trading System) — Code Wiki

> **版本**: v2.46.0 | **最后更新**: 2026-08-08
>
> 本文档基于当前源代码分析重新生成，是 FTS 项目的代码级参考文档，面向开发者阅读。
> 覆盖：项目整体架构、主要模块职责、关键类与函数说明、依赖关系、项目运行方式。

---

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构](#2-系统架构)
3. [目录结构](#3-目录结构)
4. [核心模块详解](#4-核心模块详解)
   - 4.1 [核心契约层 `fts.core`](#41-核心契约层-ftscore)
   - 4.2 [配置系统 `fts.config`](#42-配置系统-ftsconfig)
   - 4.3 [因子引擎 `fts.factor_engine`](#43-因子引擎-ftsfactor_engine)
   - 4.4 [数据提供者层 `fts.data*`](#44-数据提供者层-ftsdata)
   - 4.5 [多源数据适配器 `fts.data_sources`](#45-多源数据适配器-ftsdata_sources)
   - 4.6 [监控层 `fts.monitor`](#46-监控层-ftsmonitor)
   - 4.7 [调度层 `fts.scheduler`](#47-调度层-ftsscheduler)
   - 4.8 [风控层 `fts.risk`](#48-风控层-ftsrisk)
   - 4.9 [ML 模型层 `fts.ml`](#49-ml-模型层-ftsml)
   - 4.10 [信号桥接层 `fts.bridge`](#410-信号桥接层-ftsbridge)
   - 4.11 [跨市场验证 `fts.cross_market`](#411-跨市场验证-ftscross_market)
   - 4.12 [LLM 客户端 `fts.llm`](#412-llm-客户端-ftsllm)
   - 4.13 [CLI 入口 `fts.cli`](#413-cli-入口-ftscli)
5. [关键类/函数速查表](#5-关键类函数速查表)
6. [依赖关系图](#6-依赖关系图)
7. [项目运行方式](#7-项目运行方式)
8. [配置系统](#8-配置系统)
9. [数据流与执行流程](#9-数据流与执行流程)
10. [测试体系](#10-测试体系)
11. [附录 A: 版本历史](#11-附录-a-版本历史)
12. [附录 B: 相关文档](#12-附录-b-相关文档)

---

## 1. 项目概述

**FTS (Factor Trading System)** 是一个 AI 原生的量化因子智能系统，从 FDT 项目剥离的独立因子策略系统。专注于多因子挖掘、评估、组合与演化，输出标准化的交易信号（ScoredSignal / FactorSignal），交易执行由下游系统（FDT 等）负责。

### 核心定位

```
数据源（DuckDB / TQ-Local / TQSDK / AKShare / Wind / iFinD / 腾讯）
    ↓
FTS（因子智能系统 → 交易信号）
    ├── L0 Program（人类设定层：program.md）
    ├── L1 Meta-Loop（每日知识补给 + Bootstrapping）
    ├── L2 Evolution Loop（因子演化 + 三级评估 + 质检 + 审计）
    ├── L3 Portfolio Loop（组合构建 + 信号产出 + 风控检查）
    └── 反馈闭环（归因分析 + 演化方向调整）
    ↓
下游消费系统（FDT / VNPY / 手动执行，经 SignalBridge 桥接）
```

### 核心能力

| 能力 | 说明 |
|------|------|
| **四层循环体系** | L0 Program（人类设定）+ L1 Meta-Loop（08:30 知识补给）+ L2 Evolution Loop（23:00 夜间演化）+ L3 Portfolio Loop（20:00 组合构建） |
| **多市场支持** | A 股、ETF、期货（25 核心 / 82 全量连续合约），股票与期货因子演化严格分路径 |
| **因子种子库** | 股票 645（9 内置 + 101 世坤 wq101 + 158 Qlib + 191 国泰君安 gtja191 + 23 基本面 + 163 JQ）+ 期货 184（20 文件 17 家族）= **829 个** |
| **FTS-Expr DSL** | 算子表达式语言，58 个算子 7 大类别（L0-L5 分层），支持表达式因子 |
| **GP 遗传规划 + 算子演化** | 双引擎因子搜索：GP（表达式树交叉/变异）+ OperatorEvolution（ExprNode 层面演化） |
| **五层逻辑审查** | 消融实验 + 场景测试 + SHAP 分析 + 鲁棒性审查 + 因果验证 |
| **全自动调度** | APScheduler 9 个 cron 任务 + 进程看门狗 + 热重载 |
| **多源数据融合** | K 线主路径 5 级降级（DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC）+ WIND/IFIND 字段增强层 + 分钟/tick 路径，5 种融合策略 |
| **因子质检** | 10 维质量评分卡（A/B/C 三级准入）+ 6 项强制审计 |
| **回测流水线** | 多阶段回测（DataLoad → FactorCompute → Performance → Report），支持分钟频率 |
| **反馈闭环** | 6 种反馈事件类型 + 5 种根因归因 + 演化方向自适应调整 + 月度迭代报告 |
| **风控系统** | 5 项风控规则（仓位/回撤/亏损/杠杆/集中度），拦截不合格信号 |
| **Market Regime** | 四级检测链（多周期 HMM → MSM → 单周期 HMM → 规则），5 种市场状态 |
| **Prometheus 监控** | 数据源/因子/系统三维指标 + HTTP 端点 + Web UI 仪表盘（9100）+ K8s 部署 |
| **信号桥接** | JSON / Redis / REST 三种协议信号发布，VNPY 对接 |

### 技术栈

- **语言**: Python 3.10+（建议 3.12）
- **核心依赖**: numpy, pandas, scipy, pyyaml, shap
- **可选依赖**: optuna（演化）、openai/anthropic（LLM）、akshare（MCP 数据）、scikit-learn（组合）、lightgbm/xgboost（ML）、redis（桥接）
- **数据存储**: DuckDB（`data/fts_history.duckdb`：kline_cache / contract_kline / minute_cache / tick_cache / edb_cache / factor_catalog 等表）
- **调度**: APScheduler（未安装时静默降级）
- **监控**: 纯标准库 HTTP 仪表盘 + Prometheus 端点（`/metrics`）
- **包管理**: setuptools，`pyproject.toml` 定义项目元数据，`fts` 命令入口注册

---

## 2. 系统架构

### 2.1 四层循环架构

```
┌─────────────────────────────────────────────────────────────────────┐
│               L0 Program（人类唯一输入接口，每周维护）                  │
│  program.md：市场制度/因子偏好/避让/L0 熔断确认/预算约束                │
└─────────────────────────────────┬───────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    L1 Meta-Loop（每日 08:30）                         │
│  agentic 市场感知 → debate 分析 → Bootstrapping Agent 链 →           │
│  L1 Verifier 判定 → 注入种子池 + factor_pool.json                    │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ 种子候选
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L2 Evolution Loop（每日 23:00）                       │
│  DataQualityMonitor 数据校验 → State 加载 → 熔断预检查                 │
│  循环（每代）:                                                       │
│    UCT 父因子选择 → 宏观演化（LLM）→ 微观演化（optuna trials）        │
│    → 三级评估链（L1 回测 → L2 经济逻辑 → L3 多重检验）                │
│    → BacktestPipeline 回测 → Verifier 判定 → 质量评分卡（10 维）      │
│    → FactorAuditor 审计（6 项强制）→ 经验链记录 → 分级准入             │
│    → 影子池观察 → DuckDB 同步（idempotent write）                     │
│  可选：GP 遗传规划 / 算子演化 / 代码演化 / 因子聚类 + PCA              │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ 精英因子
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L3 Portfolio Loop（每日 20:00）                       │
│  加载精英因子（DuckDB / JSON 回退）→ 因子筛选 → 信号合成              │
│  （equal_weight / sharpe_weight / elastic_net / ml_ensemble）         │
│  → Regime 自适应权重 → 正交化/聚类/PCA → 衰减检验 → 组合构建          │
│  （含粘性约束 + ACTIVE_FACTOR_CAP=20）→ Verifier 判定 → 风控检查      │
│  → 输出信号 / SignalBridge 发布 / 注入 FDT                             │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Market Regime 四级检测链（`regime.py` + `regime_hmm.py`）

```
RegimeAwareSelector.select():
  1. MultiHorizonHMMDetector（主，默认） — 多周期 [63,126,252] HMM 加权投票
  2. MSMRegimeDetector（P3.1，默认关）    — statsmodels MarkovRegression
  3. HMMRegimeDetector（次）              — 单周期 GaussianHMM(4 状态)
  4. _detect_by_rule（规则回退）          — 多周期趋势投票 + ADX + EWMA 波动率
  兜底：oscillate / confidence 0.5

输出 5 种状态：bull / bear / oscillate / high_vol / low_vol
配套：StateMapStabilizer（防状态标签翻转）+ RegimeTransitionWarner（迁移预警）
     + AdaptiveRegimeConfig（阈值自适应）→ AdaptiveWeightManager（家族权重调整）
```

### 2.3 数据流架构

```
期货日线 K 线主路径（5 级降级）:
  DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
                    ↓
             字段增强层（并行，不阻断主路径）:
               WIND   → settle / oi_change / 期权 IV/PCR
               IFIND  → EDB 宏观 / 产业链数据 / settle 等
                    ↓
              熔断器（每源连续 5 次失败 → UNAVAILABLE → 6h 冷却 → 探活恢复）
                    ↓
              交叉验证（≥2 源同日 close 差异 > 0.5% 记录 disagreement.jsonl）
                    ↓
              FuturesDataAggregator（统一数据入口，DuckDB 缓存优先）
                    ↓
              因子引擎（四层循环）

期货分钟路径:  minute_cache → TDX_MINUTE(17709) → TQ_LOCAL(7721) → TQSDK
期货 tick 路径: tick_cache → TQSDK_TICK（5 档盘口 32 列）
A 股/ETF 路径:  腾讯 HTTP API (qt.gtimg.cn / web.ifzq.gtimg.cn) → 合成数据降级
基本面路径:     MCPBridge 本地缓存（东方财富 mx API）→ 合成数据降级
宏观字段:       IFindSource.fetch_edb + MacroFieldAligner（lag_days 防未来函数）
```

### 2.4 反馈闭环架构

```
Live 偏离 / 数据异常 / 市场事件 / 定期评估 / 审计失败 / 因子衰减 / 手动触发
                         ↓
                 FeedbackTrigger（6 种触发条件检查）
                         ↓
                 AttributionAnalyzer（5 种根因归因:
                   FACTOR_DECAY / REGIME_MISMATCH / DATA_QUALITY /
                   IMPLEMENTATION_BUG / NORMAL_FLUCTUATION）
                         ↓
              EvolutionDirectionAdjuster（演化方向调整）
                         ↓
                 进化循环（L2）→ 调整后因子
                         ↓
                 EvolutionEffectiveness（月度评估）
```

---

## 3. 目录结构

```
fts/                            # 核心源码包（pyproject.toml: include = ["fts*"]，约 140 个 py 文件）
├── __init__.py                 # 包初始化：从 pyproject.toml 动态读取版本号，自动加载 .env
├── cli.py                      # 统一命令行入口（约 1830 行，13 个顶级子命令）
│
├── core/                       # 核心契约层
│   ├── __init__.py
│   ├── enums.py                # FTS 特有枚举（EvolutionStage/FactorPriority/FactorStatus/DataSource/FusionStrategy）
│   ├── contracts.py            # 因子引擎契约重导出（from factor_engine.contracts）+ FusedOHLCV 契约
│   └── atomic.py               # 原子文件写入/读取（临时文件 + os.replace + 备份轮转）
│
├── config/                     # 配置系统
│   ├── __init__.py             # 导出 FTSConfig / get_config / load_config
│   ├── settings.py             # FTSConfig dataclass（YAML + 环境变量 + 默认值）
│   └── factor_quality_card_config.py  # 质量评分卡维度权重/阈值配置（预设/自定义/期货）
│
├── factor_engine/              # 因子引擎（核心模块，约 70+ 文件）
│   ├── __init__.py             # 导出所有核心组件（约 450 行）
│   ├── contracts.py            # L1/L2/L3 三层 TypedDict 契约（含多源交叉验证契约）
│   ├── factor_program.py       # 因子程序接口（安全沙箱编译执行，按 kind 分派 CODE/OPERATOR）
│   ├── program.py              # L0 人类设定层（program.md 解析器）
│   ├── seed_pool.py            # 种子池管理（按市场隔离，相关性预检）
│   ├── seed_loader.py          # YAML 种子因子加载器
│   ├── seed_data_futures_full.py # 期货种子因子完整加载（184 个）
│   ├── seed_data/              # 外部种子因子数据源（wq101 / qlib158 / gtja191 / fundamental / jq / loader）
│   ├── macro_evolution.py      # 宏观演化（LLM 改因子逻辑）
│   ├── micro_evolution.py      # 微观演化（optuna 贝叶斯调参）
│   ├── evaluation_chain.py     # 三级评估链（L1 回测/L2 经济逻辑/L3 多重检验）
│   ├── verifier.py             # Verifier 协议（锁定评估机制，初始化后不可修改）
│   ├── standardizer.py         # 6 种标准化方法
│   ├── experience_chain.py     # 经验链存储（成功/失败轨迹，满 100 淘汰最旧 20）
│   ├── state.py                # 演化状态管理 + trace_id 全链路生成
│   ├── evolution_loop.py       # L2 主循环（UCT 父因子选择 + 夜间演化）
│   ├── meta_loop.py            # L1 主循环（每日知识补给 + Bootstrapping + debate 分析）
│   ├── portfolio_loop.py       # L3 主循环（信号合成 + 正交化 + 组合构建）
│   ├── monitor.py              # 循环状态检查（底层 check_loop/check_all）
│   ├── factor_quality_card.py  # 因子质量评分卡（10 维评分，0-50 分）
│   ├── audit.py                # 6 项强制审计（渐进式，缺失项 skipped）
│   ├── factor_inspector.py     # 因子定时巡检与自动降级
│   ├── factor_clustering.py    # P1 因子聚类（层次聚类选代表）+ P2 PCA 降维
│   ├── factor_optimizer.py     # 因子优化框架（并行信号矩阵 + 两阶段正交化 + 缓存）
│   ├── factor_screener.py      # 因子筛选器（B.2 Stage 1）
│   ├── signal_generator.py     # 信号生成器（时序 z-score / 横截面排名）
│   ├── portfolio_constructor.py# 组合构建器（equal/sharpe/adaptive 三权重）
│   ├── capital_allocator.py    # 资金分配器（fixed/vol_target/risk_parity/kelly）
│   ├── cost_model.py           # 交易成本模型（三市场默认配置）
│   ├── cost_simulator.py       # 真实成本模拟器（品种差异化费率）
│   ├── risk_attributor.py      # 风险归因器（协方差分解 + VaR/ES）
│   ├── report_generator.py     # 回测报告生成器（Markdown）
│   ├── backtest_pipeline.py    # 回测管线（4 阶段 + 分钟频率支持 + 绩效/盈亏比/利润因子指标）
│   ├── ablation.py             # 消融实验（5 种模式）
│   ├── shap_analyzer.py        # SHAP 局部可解释性分析
│   ├── robustness.py           # 鲁棒性审查（对抗/缺失/分布外）
│   ├── causal_validator.py     # 因果结构验证（6 个预定义事件）
│   ├── walk_forward.py         # 走航验证（多窗口样本外）
│   ├── stress_test.py          # 压力测试（5 个内置极端场景）
│   ├── regime.py               # Market Regime 检测（5 种状态，四级检测链）
│   ├── regime_hmm.py           # HMM 增强模块（多周期/状态稳定/迁移预警）
│   ├── regime_features.py      # 扩展特征提取（偏度/峰度/量冲击等）
│   ├── adaptive_weight.py      # 自适应权重（按 Regime 家族倍率 + 指数平滑）
│   ├── feedback_loop.py        # 反馈闭环（6 种事件类型/5 种根因/月度评估）
│   ├── signal_contract.py      # 实盘信号契约（FactorSignal/SignalValidator）
│   ├── feature_ops.py          # 特征工程算子引擎（50+ 算子 6 大类）
│   ├── feature_importance.py   # 特征重要性分析
│   ├── gp_evolver.py           # GP 遗传规划搜索引擎
│   ├── operator_evolution.py   # 算子演化引擎（ExprNode 层面交叉/变异）
│   ├── extractors/             # 因子提取管线（base / futures_pipeline / stock_pipeline）
│   ├── expr_dsl/               # FTS-Expr DSL 算子表达式语言
│   │   ├── ast.py              # ExprNode AST 定义
│   │   ├── parser.py           # 表达式解析器
│   │   ├── registry.py         # 58 算子注册表（L0-L5 分层）
│   │   ├── validator.py        # 静态验证器（类型 + PIT max_lookback）
│   │   ├── executor.py         # 运行时执行器（解释 AST）
│   │   ├── compiler.py         # 表达式编译为 Python 代码
│   │   ├── factory.py          # 因子工厂（create_operator_factor）
│   │   └── runtime.py          # 沙箱运行时入口（eval_fts_expr）
│   └── factor_db/              # DuckDB 因子数据库
│       ├── schema.py           # 表结构（factor_catalog/evaluations/versions/correlations/quality_scores）
│       ├── repository.py       # CRUD + 排行榜 + 多样性选择 + retire_factor
│       ├── lineage.py          # 因子血缘追踪（评估趋势/质量退化/批量审计）
│       └── migrate_from_json.py # JSON 迁移脚本
│
├── data.py                     # FTSDataProvider（统一数据入口，组合 MCP/基本面/期货提供者）
├── data_futures.py             # FuturesDataProvider（DuckDB kline_cache + 多级降级 + 异步写队列）
├── data_fundamental.py         # FundamentalProvider（基本面字段注入 + 宏观 CPI）
├── data_mcp.py                 # MCPDataProvider（腾讯 HTTP API，A 股/ETF）
├── data_mcp_bridge.py          # MCPBridge（东方财富 mx API 缓存桥接）
│
├── data_sources/               # 期货多源数据适配器（12 个文件）
│   ├── base.py                 # BaseFuturesSource 抽象基类（3 个抽象方法）+ SourceUnavailable
│   ├── aggregator.py           # FuturesDataAggregator（多源调度 + 熔断器 + 交叉验证 + DuckDB 缓存）
│   ├── fusion.py               # OHLCVFusion（5 种融合策略）
│   ├── tq_source.py            # TQLocalSource（通达信本地 7721，JSON-RPC）
│   ├── tqsdk_source.py         # TQSDKSource（天勤 SDK 连续合约）
│   ├── tqsdk_tick_source.py    # TQSDKTickSource（tick 逐笔 5 档盘口）
│   ├── tdx_minute_source.py    # TDXMinuteSource（通达信分钟 17709）
│   ├── akshare_minute_source.py# AKShareMinuteSource（分钟 K 线）
│   ├── wind_source.py          # WindSource（字段增强层）
│   ├── ifind_source.py         # IFindSource（字段增强层 + EDB 宏观）
│   ├── macro_aligner.py        # MacroFieldAligner（宏观字段滞后对齐，防未来函数）
│   └── migrate.py              # DuckDB schema 迁移（幂等可重入）
│
├── monitor/                    # 监控层
│   ├── __init__.py             # 循环状态检查/Web UI/因子跟踪/逻辑监控
│   ├── http_server.py          # FTSDashboardServer（纯标准库，端口 9100，含风控/信号提交端点）
│   ├── prometheus_metrics.py   # MetricsRegistry（A.2/A.3/C.2/C.3 指标注册表）
│   ├── elite_tracker.py        # EliteFactorTracker + AutoRetireManager（状态机淘汰）
│   ├── data_quality_monitor.py # DataQualityMonitor（IC 漂移/容量突变告警 + 三维数据质量评估）
│   ├── logic_monitor.py        # LogicMonitor（行为漂移/极端预测/换月日异常）
│   └── live_factor_monitor.py  # LiveFactorMonitor（实盘偏离监控 30%/45%）
│
├── scheduler/                  # 调度层
│   ├── __init__.py
│   ├── tasks.py                # TaskSpec + TaskRegistry + 9 个默认任务
│   ├── engine.py               # SchedulerEngine（APScheduler 包装，缺失时降级）
│   ├── jobs.py                 # 10 个任务工作函数
│   ├── watchdog.py             # ProcessWatchdog（3 次/30s 熔断 5 分钟）
│   └── hotswap.py              # HotSwapWatcher（开发期模块热重载）
│
├── risk/                       # 风控层
│   ├── __init__.py             # 导出 RiskManager / RiskConfig 等
│   └── risk_manager.py         # 5 项风控规则检查（仓位/回撤/亏损/杠杆/集中度）
│
├── ml/                         # ML 模型层（v2.38.0）
│   ├── __init__.py
│   ├── models.py               # MLSignalModel（LightGBM/XGBoost/Ensemble，缺失依赖返回 None）
│   └── trainer.py              # SignalModelTrainer（三种训练模式）
│
├── bridge/                     # 信号桥接层（v2.38.0）
│   ├── __init__.py
│   └── signal_bridge.py        # SignalBridge（JSON/Redis/REST 三协议）
│
├── cross_market/               # 跨市场泛化验证（v2.27.0）
│   ├── __init__.py
│   ├── data_adapter.py         # CrossMarketDataAdapter（统一数据格式/路由）
│   └── engine.py               # CrossMarketEngine（三方向验证 + 因子分类 + 报告）
│
└── llm.py                      # LLM 客户端（OpenAI/Anthropic/Mock + 工厂函数）

config/                         # 项目配置
├── settings.yaml               # FTS 全局配置 YAML
├── prometheus.yml / prometheus_alerts.yml / alertmanager.yml
seeds/                          # YAML 种子因子定义（stock/ + futures/）
data/                           # 运行时数据（fts_history.duckdb 等，git 忽略）
scripts/                        # 工具脚本（70+ 个）
tests/                          # 测试目录（100 个文件，2632+ 用例）
docs/                           # 项目文档（harness 规范 + FTS_manual.md + plans/ + design/ + acceptance/）
deploy/k8s/                     # K8s 监控部署（8 个文件）
memory/                         # 运行时持久化（evolution/meta_loop/portfolio/tracking/knowledge/factors/{elite,futures_elite}）
reports/                        # 回测/信号/跨市场验证报告输出
logs/                           # 运行日志
```

---

## 4. 核心模块详解

### 4.1 核心契约层 `fts.core`

**文件**: `fts/core/`

**职责**: 定义 FTS 特有的核心枚举、原子操作，并将因子引擎契约 re-export（统一导入入口）。

#### `fts.core.enums`

| 枚举 | 成员 | 说明 |
|------|------|------|
| `EvolutionStage` | `L0_HUMAN` / `L1_META_LOOP` / `L2_EVOLUTION` / `L3_PORTFOLIO` | 因子演化阶段标识 |
| `FactorPriority` | `HIGH` / `MEDIUM` / `LOW` | 因子优先级 |
| `FactorStatus` | `PENDING` / `INJECTED` / `DECAYED` / `REJECTED` | 种子池因子状态 |
| `DataSource` | `DUCKDB_CACHE` / `TQ_LOCAL` / `TQ_PYTHON` / `AKSHARE` / `SYNTHETIC` / `WIND` / `IFIND` / `TQSDK` / `TQSDK_TICK` / `TDX_MINUTE` | 数据源标识（10 个） |
| `FusionStrategy` | `MEDIAN` / `MEAN` / `WEIGHTED` / `HIERARCHICAL` / `TRIMMED_MEAN` | OHLCVFusion 融合策略 |

#### `fts.core.atomic`

| 函数 | 签名 | 说明 |
|------|------|------|
| `atomic_write` | `(path, data, *, make_dir=True, encoding="utf-8")` | 原子写入 JSON：临时文件 `.tmp` + `os.replace` 原子替换 |
| `atomic_read` | `(path, *, default=None, encoding="utf-8")` | 安全读取 JSON（不存在/不合法返回 default） |
| `atomic_write_state` | `(path, state, *, backup_count=3)` | 原子写状态 + 备份轮转（`.bak.0`, `.bak.1`, ...） |

#### `fts.core.contracts`

Re-export `fts.factor_engine.contracts` 的全部契约（版本号、FactorProgram、评估/Verifier/预算、L1/L3 契约、`MultiSourceDisagreement`），本文件唯一新定义 `FusedOHLCV`（融合器输出契约）。

---

### 4.2 配置系统 `fts.config`

**文件**: `fts/config/`

**职责**: 全局配置管理，优先级：环境变量（`FTS_*`）> YAML 配置文件 > Python 默认值。

| 类/函数 | 说明 |
|---------|------|
| `FTSConfig` | `@dataclass` 全局配置。关键字段见 §8 |
| `get_config()` | 延迟初始化单例 |
| `load_config(config_path=None)` | 加载配置（YAML 解析 + `_apply_dict` + `_apply_env_overrides` 环境变量覆盖） |
| `get_elite_dir(market)` | 按市场返回 elite 目录（futures → `futures_elite_dir`，其他 → `elite_dir`） |

**`factor_quality_card_config.py`**：评分卡预设（`get_conservative_config` / `get_aggressive_config` / `get_permissive_config` / `get_futures_config`）、自定义（`create_config`）。

---

### 4.3 因子引擎 `fts.factor_engine`

**文件**: `fts/factor_engine/`（约 70+ 文件，核心模块）

**职责**: FTS 的核心，实现 L0/L1/L2/L3 四层循环 + 因子全生命周期管理。

#### 4.3.1 契约层 `contracts.py`

定义所有核心 TypedDict 契约（HARNESS §契约优先的核心）。关键内容：

- **版本**: `EVOLUTION_VERSION` 动态读取自 `fts.__version__`；`STATE_SCHEMA_VERSION = "1"`
- **市场与家族**: `FactorMarket`（futures/stock/etf/bond/multi）、`FactorFamily`（14 大类：trend/mean_reversion/carry/seasonality/cross_section/fundamental/technical/microstructure/macro/behavioral/liquidity/volatility/volume/multi_factor/other）
- **因子类型**: `FactorKind` = `OPERATOR` / `CODE` / `HYBRID`
- **核心契约**: `FactorProgram`、`FactorSignature`、`EconomicLogic`、`BacktestMetrics`、`EconomicScore`、`MultipleTestResult`、`FactorEvaluation`、`ExperienceTrace`、`EvolutionState`、`VerifierConfig`、`VerifierResult`、`BudgetConfig`
- **L1 契约**: `SeedCandidate`、`L1MetaLoopState`、`FactorPoolEntry`、`FactorPool`、`L1VerifierConfig`/`Result`、`L1BudgetConfig`、`L1BootstrappingSource`
- **L3 契约**: `PortfolioSignal`、`PortfolioCombo`、`AgentOptimizationProposal`、`L3VerifierConfig`、`L3MetaLoopState`、`FactorCorrelation`
- **多源交叉验证**: `MultiSourceDisagreement`
- **默认配置**: `DEFAULT_VERIFIER_CONFIG`、`DEFAULT_BUDGET_CONFIG`、`DEFAULT_L1_*`、`DEFAULT_L3_*`

#### 4.3.2 因子程序 `factor_program.py`

图灵完备因子代码 + 安全沙箱编译执行，按 `kind` 分派（CODE 走沙箱 exec，OPERATOR 走 DSL runtime）。

| 关键类/函数 | 说明 |
|------------|------|
| `FactorExecutor` | `execute(factor, data)` 按 kind 分派执行 |
| `create_factor_program` | 创建 FactorProgram 实例 |
| `generate_factor_id` | `fct_<8hex>`，基于 name + code + 随机熵 SHA1 哈希 |
| `validate_factor_code` | AST 静态分析验证（语法/黑名单 import/黑名单内置函数） |

**沙箱约束**: `ALLOWED_IMPORTS` = {numpy, pandas, scipy, statsmodels, talib, math, statistics}；`FORBIDDEN_MODULES` = {os, sys, subprocess, socket, requests, ctypes, pickle, ...}；唯一放行 FTS 模块：`fts.factor_engine.expr_dsl.runtime`。

#### 4.3.3 L0 Program `program.py`

| 关键类/函数 | 说明 |
|------------|------|
| `ProgramConfig` | market_regime / factor_priority / factor_avoid / agent_llm / token 预算 / 风险约束（max_drawdown 0.20, max_turnover 0.50, min_sharpe 1.5, min_economic_logic_score 3）/ 熔断确认清单 |
| `parse_program_md(content)` | 非严格正则解析 program.md（人类唯一输入接口） |
| `init_program` / `load_program` | 生成默认模板 / 加载 |
| `get_llm_env_overrides` | 生成 `FDT_LLM_<NAME>_MODEL` 环境变量 |

#### 4.3.4 种子池 `seed_pool.py` / `seed_loader.py` / `seed_data_futures_full.py`

| 关键类/函数 | 说明 |
|------------|------|
| `SeedPool` | `add/remove/list/get`，按市场隔离，相关性预检 |
| `get_default_seed_pool(market)` | 获取默认种子池（futures/stock） |
| `compute_seed_correlations` / `compute_cross_section_correlations` | 相关性计算 |
| `load_all_yaml_seeds` / `load_factors_from_dir` / `load_factors_from_yaml` | YAML 种子加载 |
| `load_futures_seeds_full` | 期货种子完整加载（184 个，17 家族） |
| `load_stock_seeds` | 股票种子加载（645 个） |

#### 4.3.5 演化核心 `macro_evolution.py` / `micro_evolution.py` / `evaluation_chain.py` / `verifier.py`

| 关键类/函数 | 说明 |
|------------|------|
| `MacroEvolver` | `evolve(factor, experience_chain, llm_client) → FactorProgram`，LLM 修改因子逻辑 |
| `evolve_micro(factor, data, forward_returns, n_trials)` | optuna 贝叶斯参数优化 |
| `EvaluationChain` | `evaluate(factor, data, forward_returns) → FactorEvaluation`：L1 回测（IC/ICIR/Sharpe/MDD/单调性/OOS/换手）→ L2 经济逻辑（4 维 LLM 评分）→ L3 多重检验（Bonferroni/FDR） |
| `cross_section_evaluate_backtest` | 横截面回测评估（期货多品种） |
| `FactorVerifier` | 锁定 Verifier：`__init__` 后 `_locked = True`，`check(evaluation) → VerifierResult` |
| `VerifierAlreadyLockedError` / `VerifierNotLockedError` | 锁定/未锁定异常 |
| `get_global_verifier()` | 全局 Verifier 单例 |

**L1 阈值**: IC > 0.03, Sharpe > 1.5, 单调性 >= 0.5, 样本外 >= 30%。

#### 4.3.6 L2 Evolution Loop `evolution_loop.py`

| 关键类 | 说明 |
|-------|------|
| `EvolutionLoop` | L2 主循环。`run() → EvolutionRunResult` |
| `EvolutionRunResult` | run_id, trace_id, generations_completed, elite_factor_ids, new_elite_count, avg_ic, avg_sharpe, circuit_broken, error |

**`run()` 流程**: 状态加载 + DataQualityMonitor 校验 → 熔断预检查（token/失败率/连续低 IC）→ 每代：UCT 父因子选择 → 宏观演化 → 微观演化 → 三级评估链 → BacktestPipeline → Verifier 判定 → 质量评分卡 → FactorAuditor 审计 → 经验链记录 → 分级准入（A/B 晋升、C 淘汰）→ 影子池 5 日观察 → 状态持久化 + DuckDB 同步。

**UCT 选择**: `UCT_EXPLORATION_C = 1.0`；`_select_parent_uct()` 平衡探索与利用；`_update_uct_stats()`。

#### 4.3.7 L1 Meta Loop `meta_loop.py`

| 关键类 | 说明 |
|-------|------|
| `MetaLoop` | `run() → MetaRunResult`：`_perceive_market`（agentic 市场感知）→ `_analyze_debate`（辩论分析）→ `_run_bootstrap`（Bootstrapping 候选）→ `_verify_and_inject`（L1 Verifier + 注入种子池） |
| `L1Verifier` | 宽松判定：economic_logic >= 2/4 + is_executable + not_duplicate + narrative >= 20 字 |
| `MetaStateManager` / `FactorPoolManager` | L1 状态 / 因子池管理 |
| `DebateQualityAnalyzer` / `BootstrappingChain` | 辩论质量分析 / Bootstrapping Agent 链 |

#### 4.3.8 L3 Portfolio Loop `portfolio_loop.py`

| 关键类/函数 | 说明 |
|------------|------|
| `PortfolioLoop` | `run() → PortfolioRunResult` |
| `L3Verifier` | combo_sharpe >= min / max_correlation / combo_turnover / decay_6m / n_factors 判定 |
| `synthesize_signals(factors, mode)` | 信号合成：equal_weight / sharpe_weight / elastic_net / ml_ensemble |
| `orthogonalize_factors` | 因子正交化（非 elastic_net 模式） |
| `decay_test(signals, lookback)` | 6 个月衰减率 > 0.3 剔除 |
| `build_combo` | 组合构建（含 StickyConfig 粘性约束） |
| `load_elite_factors(elite_dir, market)` | 加载精英因子（含去重） |
| `inject_to_fdt` | 注入 FDT / 触发信号管道 |

**关键约束**: `ACTIVE_FACTOR_CAP = 20`（活跃因子上限）、`SHADOW_OBSERVE_TRADING_DAYS = 5`（影子池观察期）、默认 `synthesis_mode = elastic_net`。

#### 4.3.9 质量评分卡 `factor_quality_card.py` + 审计 `audit.py`

**评分卡**: 10 维评分（IC/Sharpe/稳定性/鲁棒性/容量/交易性/多样性/逻辑性/实时性/兼容性），0-50 分；分级 A ≥ 35、B ≥ 25、C < 25（期货 B ≥ 24）。

| 关键类 | 说明 |
|-------|------|
| `FactorQualityCard` | `evaluate(...) → FactorQualityScore` |
| `FactorQualityCardConfig` / `FactorQualityScore` / `DimensionScore` | 配置/结果/单维度 |
| `compute_total_score` / `determine_grade` | 加权总分 / 等级判定 |

**6 项强制审计**（`FactorAuditor`）:

| # | 审计项 | 实现 |
|---|--------|------|
| 1 | 因果检验 | CausalValidator（Granger/反事实） |
| 2 | 样本外验证 | WalkForwardOptimizer |
| 3 | 跨品种验证 | ≥80% 品种 IC 为正 |
| 4 | 压力测试 | StressTester（5 个极端场景） |
| 5 | 多重检验 | Bonferroni / FDR 校正 |
| 6 | 数据窥探检验 | 无未来函数检查 |

渐进式审计：缺失项 `skipped` 不阻塞流程。

#### 4.3.10 因子巡检 `factor_inspector.py`

| 关键类/函数 | 说明 |
|------------|------|
| `FactorInspector.inspect_and_downgrade(threshold=-0.2, market=None, commit=True)` | 批量血缘审计 → 退化识别 → 降级（commit=False 为 dry-run） |
| `get_degraded_factors` / `reactivate_factor` | 查询降级因子 / 恢复激活 |

#### 4.3.11 因子聚类 + PCA `factor_clustering.py`（P1/P2，v2.36.0）

| 关键类 | 说明 |
|-------|------|
| `FactorClusteringEngine` | `compute_signal_correlations` → `cluster_by_correlation`（层次聚类，threshold=0.7）→ `select_representative_factors`（每簇 |sharpe| 最高）|
| `PCASignalCompressor` | z-score → PCA 保留 95% 方差（max_components=10）→ 主成分信号 + 载荷 |
| `compute_cluster_summary` / `compute_pca_summary` | 摘要统计 |

L3 中默认启用聚类（`enable_clustering=True`），PCA 默认关闭（`enable_pca=False`）。

#### 4.3.12 FTS-Expr DSL `expr_dsl/`

**职责**: 算子表达式语言，58 个算子 7 大类别（L0-L5 分层）。

| 关键类/函数 | 签名 | 说明 |
|------------|------|------|
| `ExprNode` | `@dataclass` | AST 节点（op/args/kind） |
| `parse_expression(expr_str)` | `str → ExprNode` | 表达式解析 |
| `validate_expr(node, registry)` | → bool | 静态验证 |
| `compute_max_lookback(node)` | → int | PIT 最大 lookback 分析 |
| `collect_fields(node)` | → set[str] | 收集所需数据字段 |
| `evaluate(node, data, registry)` | → Series/float | 运行时执行（向量化） |
| `compile_expr_to_code(expr_str)` | → str | 编译为 Python 代码 |
| `eval_fts_expr(expression, data, params)` | → np.ndarray | 沙箱运行时入口 |
| `OperatorMeta` | `@dataclass(frozen=True)` | 算子元数据（name/func/category/params/bounds/lookback/differentiable/economic_meaning） |
| `build_registry()` | → dict | 构建算子注册表 |

**算子分类**: time_series(6)、price(7)、rolling(9)、technical(7)、cross_section(5)、cross_symbol(3)、composite(13)。

#### 4.3.13 GP 遗传规划 `gp_evolver.py` + 算子演化 `operator_evolution.py`

| 关键类 | 说明 |
|-------|------|
| `TreeNode` / `ExpressionTree` | 表达式树 |
| `GPEvolver` | 锦标赛选择/交叉/变异/精英保留/多父代交叉；`GPEvolverConfig` 配置 |
| `GPEvolveResult` | 演化结果（best_fitness/best_expression/best_ic/best_sharpe） |
| `tree_to_factor_program` | 表达式树转因子程序 |
| `OperatorEvolver` | ExprNode 层面交叉/变异（独立于 GP），`OperatorEvolutionConfig` |

#### 4.3.14 特征工程 `feature_ops.py` + `feature_importance.py`

| 关键类 | 说明 |
|-------|------|
| `OperatorRegistry` | 50+ 算子注册表，6 大类 |
| `TimeSeriesOps` / `PriceOps` / `RollingOps` / `TechnicalOps` / `CrossSectionOps` / `CrossSymbolOps` / `CompositeOps` | 算子类 |
| `FeatureOpsEngine` | 特征工程引擎入口 |
| `FeatureImportanceAnalyzer` | 随机森林/GBDT/线性回归特征重要性 |

#### 4.3.15 回测流水线 `backtest_pipeline.py`

| 关键类 | 说明 |
|-------|------|
| `BacktestPipeline` | 4 阶段：DataLoad → FactorCompute → Performance → Report |
| `BacktestInput` | factor/data/benchmark/forward_period/cost_rate/slippage/capital/date_range/frequency |
| `BacktestReport` / `BacktestResult` | 回测报告（含 PerformanceMetrics 全量指标） |
| `BacktestPipelineBuilder` | Builder 模式 |
| `PipelineStage` | DATA_LOAD / FACTOR_COMPUTE / PERFORMANCE / REPORT |
| `PerformanceMetrics` | 总收益/年化/Sharpe/最大回撤/IC/ICIR/换手率/**盈亏比 payoff_ratio / 利润因子 profit_factor** |
| `FactorOutput` | 因子计算输出 |

支持分钟频率回测（daily/1m/5m/15m/30m/60m），分钟数据走 `FuturesDataProvider.get_minute_ohlcv`。

#### 4.3.16 成本与风控分析模块

| 模块 | 关键类/函数 | 说明 |
|------|-------------|------|
| `cost_model.py` | `TransactionCostModel.adjust(...)` | 三市场默认配置（futures 0.5/0.2/1.0 bps），成本惩罚夏普 |
| `cost_simulator.py` | `CostSimulator.simulate(...)` | 品种差异化费率覆盖，`CostResult` |
| `factor_optimizer.py` | `FactorOptimizer` | 并行信号矩阵 + `tiered_orthogonalize` 两阶段正交化（L2 种子相关性先验 + 代码哈希去重）+ 磁盘信号/相关性缓存 |
| `signal_generator.py` | `SignalGenerator` | 时序信号（滚动 20 日 z-score + tanh 压缩）/ 横截面信号（top/bottom 20%） |
| `factor_screener.py` | `FactorScreener.screen(...)` | 等级/总分/状态/风格过滤 |
| `portfolio_constructor.py` | `PortfolioConstructor.construct(...)` | equal / sharpe / adaptive 三权重 |
| `capital_allocator.py` | `CapitalAllocator` | fixed / vol_target / risk_parity / kelly 四模式 |
| `risk_attributor.py` | `RiskAttributor.attribute(...)` | 协方差分解 + 暴露分析 + VaR/ES |
| `report_generator.py` | `ReportGenerator.generate(...)` | 5 节 Markdown 报告（摘要/净值/回撤/IC/月度收益） |
| `adaptive_weight.py` | `AdaptiveWeightManager` / `RegimeSmoother` | Regime 家族倍率调整 + 切换平滑 |

#### 4.3.17 逻辑审查五层

| 模块 | 关键类 | 说明 |
|------|--------|------|
| `ablation.py` | `AblationExperiment` | 5 种消融模式（volume_zero/vwap_replace/time_shuffle/noise_inject/feature_permute） |
| `shap_analyzer.py` | `ShapAnalyzer` | SHAP 局部可解释性 |
| `robustness.py` | `RobustnessTester` | 对抗样本/缺失值/分布外 |
| `causal_validator.py` | `CausalValidator` | 6 个预定义自然事件因果验证 |
| `walk_forward.py` | `WalkForwardOptimizer` | 滚动多窗口样本外（综合评分 = 一致性 40% + 波动率 30% + 均值强度 30%） |
| `stress_test.py` | `StressTester` | 5 个内置场景，最大回撤 ≤ 40% 通过 |

#### 4.3.18 Market Regime `regime.py` / `regime_hmm.py` / `regime_features.py`

| 关键类 | 说明 |
|-------|------|
| `RegimeAwareSelector` | 四级检测链（见 §2.2），`select_factors` 按 IC/Sharpe 保留，`regime_report` 生成报告 |
| `HMMRegimeDetector` | 单周期 GaussianHMM（4 状态，seed=42），`maybe_refit` 每 20 次 |
| `MultiHorizonHMMDetector` | 多周期 [63,126,252] 加权投票（默认主检测器） |
| `MSMRegimeDetector` | statsmodels MarkovRegression（P3.1 默认关） |
| `StateMapStabilizer` | 状态标签翻转稳定（置信度 > 0.8 冻结） |
| `RegimeTransitionWarner` | 迁移预警（后验熵/转移矩阵/KL 散度三信号） |
| `AdaptiveRegimeConfig` | 5 个阈值自适应，每 20 日重优化 |
| `compute_extended_features` / `compute_hmm_feature_vector` | 扩展特征提取 |

#### 4.3.19 因子数据库 `factor_db/`

**表结构**（DuckDB `data/fts_history.duckdb`）:

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `factor_catalog` | 因子主表 | factor_id, name, code, sharpe, ic, market, family, status, is_elite, kind, expression |
| `factor_evaluations` | 评估历史 | eval_id, factor_id, level_1_*, level_2_*, walk_forward |
| `factor_versions` | 代码版本历史 | version_id, factor_id, code, code_hash, version |
| `factor_correlations` | 相关性矩阵 | factor_id_a, factor_id_b, pearson, spearman |
| `factor_quality_scores` | 质量评分 | factor_id, total_score, dimension_scores, grade |

**关键类**:

| 类 | 方法 | 说明 |
|----|------|------|
| `FactorRepository` | `create/get/update/list_factors` | 因子 CRUD |
| | `get_eligible(market, min_ic, min_sharpe)` | 合格因子查询 |
| | `get_diverse_factors` | 多样性选择（按家族分布） |
| | `get_leaderboard` | 排行榜 |
| | `retire_factor(fid, reason, elite_dir)` | 淘汰因子（同步 DuckDB + JSON） |
| `FactorLineage` | `get_lineage` / `get_evaluation_trend` / `detect_quality_degradation` / `batch_audit` | 血缘追踪 |

#### 4.3.20 其他引擎模块

| 模块 | 关键类/函数 | 说明 |
|------|-------------|------|
| `standardizer.py` | `Standardizer` / `standardize()` / `StandardizeMethod` | 6 种标准化（z-score/min-max/rank 等） |
| `experience_chain.py` | `ExperienceChain` / `create_trace_from_evaluation` | 经验链持久化（满 100 淘汰最旧 20） |
| `state.py` | `EvolutionStateManager` / `generate_trace_id` / `generate_run_id` / `generate_session_id` | 状态管理 + 备份恢复 + trace_id |
| `monitor.py` | `check_loop` / `check_all` / `LoopStatus` / `AllStatus` | 底层循环状态检查 |
| `signal_contract.py` | `FactorSignal` / `SignalMeta` / `SignalDetail` / `SignalValidator` | 信号契约（方向 long/short/flat，频率 tick~1d） |
| `feedback_loop.py` | `FeedbackTrigger` / `AttributionAnalyzer` / `EvolutionDirectionAdjuster` / `FeedbackLoop` | 反馈闭环 |
| `extractors/` | `FactorExtractorBase` / `FuturesPipeline` / `StockPipeline` | 因子提取管线 |

---

### 4.4 数据提供者层 `fts.data*`

#### `FTSDataProvider`（data.py）

统一数据提供者，组合 MCP（A股/ETF）、基本面、期货三个子提供者。

| 方法 | 签名 | 说明 |
|------|------|------|
| `get_ohlcv` | `(symbol, *, days=500, adjust="qfq", trace_id="", fundamental=False)` | 股票/ETF OHLCV（MCP 优先 → 合成降级） |
| `get_etf_ohlcv` | `(symbol, days=500, ...)` | ETF OHLCV |
| `get_csi300_panel` | `(days=500, max_stocks=50, ...)` | 沪深 300 面板（全交集日期） |
| `get_futures_ohlcv` | `(symbol, *, days=500, trace_id)` | 期货连续合约 OHLCV |
| `get_futures_panel` | `(symbols=None, days=500, trace_id)` | 期货面板（默认 `FUTURES_CORE_SUBSET` 25 品种） |
| `enrich_with_fundamental` / `enrich_futures_fundamental` | — | 基本面/期货基本面字段注入 |
| `get_stock_panel` / `get_etf_panel` | — | 股票/ETF 面板 |
| `search_symbol` | `(query, limit=10)` | 代码搜索 |
| `synthesize_ohlcv` | `(n_days, base_price, seed)` | 合成数据兜底 |

#### `FuturesDataProvider`（data_futures.py）

数据源优先级（直接路径）：DuckDB kline_cache → TQ-Local → AKShare → 合成。

| 组件 | 说明 |
|------|------|
| `FuturesDataProvider` | `get_ohlcv` / `get_minute_ohlcv` / `get_tick_data` / `get_futures_panel`（多数对齐） |
| `retry_on_conflict` | DuckDB 写冲突指数退避重试装饰器 |
| `AsyncWriteQueue` | 异步写入串行化队列（`start/stop/execute/flush`） |
| `DuckDBConnection` | 连接管理器（`SET lock_configuration = true`，同步重试 + 异步队列） |
| `get_dominant_contracts(symbols)` | 主力合约查询（contract_kline ROW_NUMBER 按 date/volume） |
| `get_realtime_prices(symbols)` | 实时价（TQ-Local 主路径 + AKShare 降级） |

品种清单：`FUTURES_SUBSET`（82）、`FUTURES_CORE_SUBSET`（25）、`FUTURES_HOLDOUT`（6 盲测）、`FUTURES_SECTOR_MAP`（12 产业链）、`FUTURES_STRATIFIED_SUBSET`（分层训练集）。

**VWAP 逻辑**: 有成交额 `amount/volume`；AKShare 有 settle `(H+L+C+settle)/4`；DuckDB 无 settle `(H+L+C)/3`。

#### `MCPDataProvider`（data_mcp.py）

腾讯 HTTP API（`web.ifzq.gtimg.cn/appstock/app/fqkline/get`），零外部依赖（httpx）。`CSI300_SUBSET`（77 只）、`ETF_SUBSET`（18 只）。

#### `FundamentalProvider`（data_fundamental.py）

字段分 6 组：VALUATION / SIZE / TRADING / QUALITY / GROWTH / MACRO（FUNDAMENTAL_FIELDS 全量）。`enrich_ohlcv` 优先 `MCPBridge` 缓存注入 → 合成降级（seed=42）；`_fetch_macro` 走东方财富 CPI API。

#### `MCPBridge`（data_mcp_bridge.py）

东方财富 mx API 缓存桥接（`data/fundamental_cache.json`），`get_fundamental` / `get_batch` / `save_cache` / `_parse_mx_response`。

---

### 4.5 多源数据适配器 `fts.data_sources`

**文件**: `fts/data_sources/`（12 个文件，v2.3.0 起多源集成）

**职责**: 期货多源数据适配，统一输出 17 列 kline_cache schema（日线）/ 11 列 minute_cache schema（分钟）/ 32 列 tick_cache schema（tick）。

#### 关键类

| 类 | 说明 |
|----|------|
| `BaseFuturesSource` | 抽象基类：`fetch_ohlcv()` / `fetch_quote()` / `is_available()` + `fetch_ohlcv_or_none` / `validate_ohlcv_row` |
| `SourceUnavailable` | 数据源不可用异常（`source`, `reason`），供熔断 |
| `FuturesDataAggregator` | 多源调度器。`get_ohlcv` / `get_minute_ohlcv` / `get_ticks` / `get_source_status` / `cross_check` |
| `OHLCVFusion` | `fuse_row` / `fuse_dataframe`；N=1 透传 |
| `TQLocalSource` | 通达信本地（JSON-RPC over HTTP 7721），`period` 可配（day/1m/5m/15m/30m/60m） |
| `TQSDKSource` | 天勤 SDK（`KQ.m@{EXCHANGE}.{symbol}` 连续合约，`_SYMBOL_MAP` 约 60 品种） |
| `TQSDKTickSource` | tick 逐笔（5 档盘口，免费账号约 42 分钟历史） |
| `TDXMinuteSource` | 通达信分钟（端口 17709，60m → 1h） |
| `AKShareMinuteSource` | AKShare 分钟（sina 周期） |
| `WindSource` | Wind MCP 字段增强层（覆写 `fetch_ohlcv_or_none` 吞掉 SourceUnavailable） |
| `IFindSource` | iFinD MCP 增强层 + `fetch_edb` / `get_macro_series`（EDB 宏观缓存） |
| `MacroFieldAligner` | 宏观字段滞后对齐（`lag_days` 防未来函数） |
| `migrate_schema` | DuckDB schema 幂等迁移（返回 counts） |

#### 数据路径

```
K 线主路径:   DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
分钟路径:     minute_cache → TDX_MINUTE(17709) → TQ_LOCAL(7721) → TQSDK
tick 路径:    tick_cache → TQSDK_TICK
字段增强层:   WIND（settle/oi_change/期权 IV/PCR）、IFIND（EDB 宏观/产业链）
```

#### 熔断器（`BreakerState`）

- 任一源连续 5 次失败 → `circuit_open = True`
- 6 小时冷却（`COOLDOWN_SECONDS = 21600`）→ 冷却后探活恢复
- `_record_success` / `_record_failure` / `_is_circuit_open`

#### 融合策略（`OHLCVFusion`）

- `MEDIAN`（默认）: 每字段取中位数
- `MEAN`: 算术平均
- `WEIGHTED`: 按源权重加权（TQ_LOCAL=2.0, TQ_PYTHON=2.0, WIND=1.5, IFIND=1.0, AKSHARE=0.5, DUCKDB_CACHE=1.0, SYNTHETIC=0.0）
- `HIERARCHICAL`: 优先级优先，与中位数分歧 > 阈值降级到中位数
- `TRIMMED_MEAN`: 去最高/最低后均值（N≥3 最稳健）

融合字段：`("open","high","low","close","volume","amount","settle")`；hold/oi_change/pre_settle 不融合。

#### 交叉验证

≥2 源同日 close 差异 > 0.5% 记录 disagreement 至 `data/_lineage/disagreements.jsonl`。

#### 缓存表（`migrate.py` DDL）

`kline_cache`（17 列）/ `minute_cache`（11 列）/ `tick_cache`（32 列 5 档）/ `edb_cache`（主键 indicator+date+source）/ `option_chain_cache`。

---

### 4.6 监控层 `fts.monitor`

**文件**: `fts/monitor/`

**职责**: 系统健康监控 + 精英因子跟踪 + Web UI + Prometheus 指标。

#### 循环状态检查（`__init__.py`）

| 函数 | 说明 |
|------|------|
| `check_loop_status(loop_name)` | 单循环状态（L1/L2/L3），读 `memory/{meta_loop,evolution,portfolio}/state.json` |
| `check_all_status()` | 系统级状态汇总 |
| `check_data_sources_status()` | 数据源健康度（14.5） |
| `format_status_report` / `status_report_to_json` | 报告格式化 |

#### `FTSDashboardServer`（http_server.py）

纯标准库 HTTP 服务器，端口 9100。端点：

| 端点 | 说明 |
|------|------|
| `/` | 内嵌单页仪表盘（暗色主题，10s 刷新） |
| `/api/status` | 系统状态 + DuckDB 因子统计 |
| `/api/factors` | 精英因子列表（DuckDB 动态 JOIN，JSON 回退） |
| `/api/candidates` | L1 候选因子池 |
| `/metrics` | Prometheus 完整指标 |
| `/metrics/data-sources` | 数据源指标（5s 缓存） |
| `/health` | 健康检查（含数据源熔断状态） |
| `/api/v1/risk/status` | 风控状态 |
| `/api/v1/live/factors` | Live 因子表现 |
| `POST /api/v1/signal/submit` | 信号提交 → SignalValidator → RiskManager → SimulatedTradeAdapter 模拟成交 |

#### `EliteFactorTracker`（elite_tracker.py）

| 方法 | 说明 |
|------|------|
| `determine_grade(quality_score)` | A ≥ 40 / B ≥ 30 / C < 30 |
| `init_tracker(...)` | 分级准入：C → rejected；B → observing（观察 3 个月）；A → active |
| `update(factor_id, new_ic, ...)` | 追加 weekly_ic / monthly_ic，计算 decay_6m，触发状态转换 |
| `get_decaying(max_consecutive)` / `get_by_status` | 查询 |
| `auto_retire(...)` | 淘汰：连续零值 IC≥4 / decay_6m≥0.30 / critical_decay / 连续零月≥12 / Sharpe 连续降≥12 |
| `run_monthly_evaluation()` | 月度增量评估 |

**状态转换**: `active` →（连续 IC 衰减）→ `decaying` → `critical_decay` → `retired`；B 级观察期满 → `active` / `decaying`。`AutoRetireManager` 封装淘汰 + 冷却复评（7 天）。

#### `DataQualityMonitor`（data_quality_monitor.py）

- `register_factor(baseline_ic, baseline_capacity, ...)` / `check(current_ic, current_capacity)`
- 告警：`ic_drift`（|z|≥2 warning / ≥3 critical）、`capacity_shock`（±50% warning / ±80% critical），冷却 3600s
- `validate_market_data`：数据完整性校验
- 三维指标：`compute_coverage_ratio` / `compute_cross_source_deviation` / `compute_update_delay` 等，`evaluate_source_data` 汇总

#### `LogicMonitor`（logic_monitor.py）

三项检查：行为漂移（与动量/均值回归基准相关 < 0.3）、极端预测（|z|>2σ 占比 > 5%）、换月日异常（前后 5 日均值变化 > 3σ）。

#### `LiveFactorMonitor`（live_factor_monitor.py）

回测基线 vs 实盘表现，偏离 > 30% warning、> 45% critical。

#### `MetricsRegistry`（prometheus_metrics.py）

线程安全指标注册表，分组：A.2 衰减、A.3 Regime/权重、C.2 Live/风控、C.3 反馈。`render()` 输出 Prometheus 文本格式。

---

### 4.7 调度层 `fts.scheduler`

**文件**: `fts/scheduler/`

**职责**: 定时任务注册 + APScheduler 调度 + 进程守护 + 热重载。

| 类/函数 | 说明 |
|---------|------|
| `TaskSpec` | `@dataclass`：name / cron_expression / callable_path / description / enabled / trace_id_prefix |
| `TaskRegistry` | `register / unregister / get / list_all / list_enabled` |
| `SchedulerEngine` | APScheduler 包装：`start / stop / running / start_watchdog`；APScheduler 缺失时静默降级 |
| `run_scheduler(daemon=True)` | 调度器入口 |
| `ProcessWatchdog` | 进程守护：3 次/30s 重启计数，熔断 300s |
| `HotSwapWatcher` | 开发期模块热重载（watchdog 库缺失时降级） |

**9 个默认任务**:

| 任务名 | cron | 说明 |
|--------|------|------|
| `l1_meta_loop` | `30 8 * * *` | L1 知识补给 + 种子注入 |
| `l2_evolution_loop` | `0 23 * * *` | L2 夜间演化（期货横截面，训练集剔除盲测池） |
| `l3_portfolio_loop` | `0 20 * * *` | L3 组合构建 + 触发信号管道 |
| `sync_futures_data` | `30 17 * * 1-5` | 多源数据同步（摘要 gzip 落盘 `data/_lineage/`） |
| `health_check` | `*/10 * * * *` | 健康检查 |
| `monthly_decay_eval` | `0 2 1 * *` | 月度衰减评估 + 自动淘汰 |
| `data_quality_eval` | `*/5 * * * *` | 数据质量快照 |
| `logic_monitor` | `0 22 * * *` | 逻辑监控（模拟数据冒烟） |
| `factor_inspector` | `0 3 * * *` | 因子巡检降级 |

---

### 4.8 风控层 `fts.risk`

**文件**: `fts/risk/risk_manager.py`

**职责**: 5 项风控规则检查，拦截不合格信号（FTS 只做检查，交易执行由下游负责）。

| 类 | 说明 |
|----|------|
| `RiskManager` | `check(signal, account, positions) → RiskCheckResult` |
| `RiskConfig` | single_position_limit_pct=0.10 / max_portfolio_drawdown_pct=0.20 / daily_loss_limit_pct=0.05 / max_leverage=3.0 / max_concentration_pct=0.50 / max_open_positions=20 |
| `RiskCheckItem` / `RiskCheckResult` | 检查项 / 结果（approved + blocking_violations） |

**5 项规则**: ① 单品种仓位 ≤ 10% ② 组合回撤 ≤ 20% ③ 单日亏损 ≤ 5% ④ 杠杆 ≤ 3x ⑤ 前 3 大品种集中度 ≤ 50%。

---

### 4.9 ML 模型层 `fts.ml`

**文件**: `fts/ml/`（v2.38.0，Phase 24）

**职责**: 封装 LightGBM / XGBoost / Ensemble 三种 ML 模型，供 L3 信号合成（ml_ensemble 模式）。可选依赖 `[ml]`，缺失时工厂返回 None（调用方降级，不抛异常）。

| 类/函数 | 说明 |
|---------|------|
| `ModelKind` | LIGHTGBM / XGBOOST / ENSEMBLE |
| `MLSignalModel` | `_build()` 构造底层模型，`fit` / `predict`（ENSEMBLE 子模型等权平均） |
| `create_signal_model(kind, params, seed)` | 工厂，依赖缺失返回 None |
| `TrainMode` | CROSS_SECTIONAL / TIME_SERIES / ENSEMBLE_FUSION |
| `SignalModelTrainer` | `train(X, y, feature_names) → TrainResult`：NaN 清理 → 训练 → 手工 R² → 特征重要性提取 |

---

### 4.10 信号桥接层 `fts.bridge`

**文件**: `fts/bridge/signal_bridge.py`（v2.38.0，Phase 25）

**职责**: 向 VNPY 等下游发布信号，三种传输协议。

| 类/函数 | 说明 |
|---------|------|
| `SignalBridge` | `publish(signal) → signal_id` / `latest()` / `status()` |
| `BridgeStatus` | protocol / available / detail / latest_signal_id / latest_timestamp |
| `BridgeError` | 操作失败异常 |

**协议**:
- **JSON**（默认，无依赖）: 写 `output_dir/latest_signal.json`（`.tmp` + `os.replace` 原子替换）
- **Redis**（可选 `[bridge]`）: `SET fts:signals:latest` + 时间戳；redis-py 缺失时抛 BridgeError（不静默）
- **REST**（标准库 http.client）: POST 到 `rest_url`，≥400 抛 BridgeError

`PROTOCOLS = ("json", "redis", "rest")`。

---

### 4.11 跨市场验证 `fts.cross_market`

**文件**: `fts/cross_market/`（v2.27.0，Phase 14）

**职责**: 跨市场因子泛化验证，支持三个方向：期货→A股、期货→ETF、股票→期货。

| 类 | 说明 |
|----|------|
| `CrossMarketDataAdapter` | `get_panel(target_market, days, max_stocks)` 统一格式 + 路由；`execute_factor_on_market` 逐品种执行因子 |
| `CrossMarketEngine` | `run_futures_to_stock` / `run_futures_to_etf` / `run_stock_to_futures` → `CrossMarketResult` / `CrossMarketReport` |

**分类阈值**: `GENERALIZATION_THRESHOLD=0.02`、`FUTURES_SPECIFIC_THRESHOLD=0.03`、`RETENTION_RATIO=0.50`。

**分类规则**: target_ic_abs ≥ 0.02 且 ic_retention ≥ 0.50 → universal；target_ic_abs < 0.01 且 source ≥ 0.03 → failed；source < 0.03 → unknown；其余 → market_specific。

**IC 计算**: 每日截面 Spearman 秩相关（信号 vs 未来 5 日收益），品种 ≥ 5 计入。

---

### 4.12 LLM 客户端 `fts.llm`

**文件**: `fts/llm.py`

**职责**: 统一 LLM 调用接口。

| 类/函数 | 说明 |
|---------|------|
| `LLMClient` | 抽象基类：`complete(prompt, max_tokens=4000) → (text, tokens)`；`generate_json`（8 层 JSON 容错解析，含 `_repair_json` 修复式解析）；`bootstrap_factors` |
| `OpenAIClient` | `chat.completions.create`，重试 2 次；`bootstrap_factors` 生产级实现（调试响应落盘 + 修复 prompt 重试） |
| `AnthropicClient` | `messages.create` |
| `MockLLMClient` | 开发/测试用预设响应 |
| `LLMError` / `LLMCallRecord` | 异常 / 调用记录 |
| `get_llm_client(backend="", temperature=None)` | 工厂：`FTS_LLM_BACKEND` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` → MockLLMClient |

**环境变量**: `OPENAI_API_KEY/BASE_URL/MODEL`（默认 gpt-4o）、`ANTHROPIC_API_KEY/MODEL`（默认 claude-sonnet-4-20250514）、`FTS_LLM_TEMPERATURE`（默认 1.2）。

---

### 4.13 CLI 入口 `fts.cli`

**文件**: `fts/cli.py`（约 1830 行）

**职责**: 统一命令行入口，所有子命令启动时生成 session_id / trace_id（HARNESS §trace_id 全链路）。通过 `pyproject.toml` 注册为 `fts` 命令。

| 子命令 | 子子命令 | 说明 |
|--------|---------|------|
| `fts version` | — | 打印版本 |
| `fts monitor` | — | 循环健康状态（`--json`） |
| `fts ui` | — | Web UI（`--host` / `--port 9100`） |
| `fts catalog` | `stats` / `verify` / `backup` | 因子存储统计 / JSON↔DuckDB 一致性 / 备份 |
| `fts evolution` | `run` | L2 演化（`--universe single/csi300/futures`, `--max-generations`, `--max-stocks`, `--symbol`） |
| `fts meta-loop` | `run` | L1 元循环（`--market stock/futures`, `--symbols`） |
| `fts portfolio` | `run` | L3 组合（`--universe`, `--synthesis-mode`），通过后触发信号管道 |
| `fts scheduler` | `run` / `list` | 调度器管理 |
| `fts factor` | `list` / `show` / `seeds` / `stats` / `lineage` / `cross-market` | 因子管理（筛选/多样性/跨市场验证） |
| `fts seed` | `validate` / `report` / `dedup` | 种子因子校验/统计/查重 |
| `fts backtest` | `run` / `batch` / `compare` | 回测（`--frequency daily/1m/5m/15m/30m/60m`） |
| `fts feature` | `list` / `analyze` | 特征工程中台 |
| `fts gp` | `evolve` | GP 遗传规划演化 |
| `fts feedback` | `trigger` / `process` / `report` / `stats` | 反馈闭环 |
| `fts bridge` | `serve` / `publish` / `status` | 信号桥接（REST 服务 8765 / 发布 / 状态） |

**注**: 数据同步等运营操作由 `scripts/` 脚本与 scheduler jobs 承担（`scripts/sync_futures_data.py` 等），非 CLI 子命令。

---

## 5. 关键类/函数速查表

### 5.1 核心引擎类

| 类 | 所在模块 | 职责 |
|----|---------|------|
| `EvolutionLoop` | `factor_engine.evolution_loop` | L2 因子演化主循环 |
| `MetaLoop` | `factor_engine.meta_loop` | L1 元循环 |
| `PortfolioLoop` | `factor_engine.portfolio_loop` | L3 组合循环 |
| `FactorVerifier` / `L1Verifier` / `L3Verifier` | verifier / meta_loop / portfolio_loop | 三级 Verifier |
| `EvaluationChain` | `factor_engine.evaluation_chain` | 三级评估链 |
| `SeedPool` | `factor_engine.seed_pool` | 种子池 |
| `MacroEvolver` | `factor_engine.macro_evolution` | 宏观演化 |
| `FactorExecutor` | `factor_engine.factor_program` | 因子沙箱执行器 |
| `FactorQualityCard` | `factor_engine.factor_quality_card` | 质量评分卡 |
| `FactorAuditor` | `factor_engine.audit` | 6 项强制审计 |
| `FactorInspector` | `factor_engine.factor_inspector` | 因子巡检降级 |
| `FactorRepository` | `factor_engine.factor_db.repository` | 因子数据库 CRUD |
| `FactorLineage` | `factor_engine.factor_db.lineage` | 因子血缘追踪 |
| `FTSDataProvider` | `data` | 统一数据入口 |
| `FuturesDataProvider` | `data_futures` | 期货数据提供者 |
| `FuturesDataAggregator` | `data_sources.aggregator` | 多源调度器 |
| `OHLCVFusion` | `data_sources.fusion` | 多源融合器 |
| `FTSDashboardServer` | `monitor.http_server` | Web UI 仪表盘 |
| `SchedulerEngine` | `scheduler.engine` | APScheduler 调度 |
| `ProcessWatchdog` | `scheduler.watchdog` | 进程看门狗 |
| `RiskManager` | `risk.risk_manager` | 5 项风控检查 |
| `FeedbackLoop` | `factor_engine.feedback_loop` | 反馈闭环 |
| `BacktestPipeline` | `factor_engine.backtest_pipeline` | 回测管线 |
| `GPEvolver` | `factor_engine.gp_evolver` | GP 遗传规划 |
| `OperatorEvolver` | `factor_engine.operator_evolution` | 算子演化 |
| `FactorClusteringEngine` / `PCASignalCompressor` | `factor_engine.factor_clustering` | 因子聚类 / PCA 降维 |
| `FactorOptimizer` | `factor_engine.factor_optimizer` | 因子优化（正交化/缓存） |
| `RegimeAwareSelector` | `factor_engine.regime` | Market Regime 选择器 |
| `MultiHorizonHMMDetector` | `factor_engine.regime_hmm` | 多周期 HMM 检测 |
| `SignalBridge` | `bridge.signal_bridge` | 信号桥接（json/redis/rest） |
| `CrossMarketEngine` | `cross_market.engine` | 跨市场验证引擎 |
| `MLSignalModel` / `SignalModelTrainer` | `ml.models` / `ml.trainer` | ML 信号模型 / 训练器 |
| `LLMClient` / `OpenAIClient` / `AnthropicClient` | `llm` | LLM 客户端 |
| `EliteFactorTracker` | `monitor.elite_tracker` | 精英因子跟踪 |
| `DataQualityMonitor` | `monitor.data_quality_monitor` | 数据质量监控 |
| `LogicMonitor` | `monitor.logic_monitor` | 逻辑监控 |
| `LiveFactorMonitor` | `monitor.live_factor_monitor` | Live 因子监控 |

### 5.2 关键函数

| 函数 | 所在模块 | 说明 |
|------|---------|------|
| `generate_trace_id(prefix="l2")` / `generate_run_id()` / `generate_session_id()` | `factor_engine.state` | 全链路 ID 生成 |
| `create_factor_program` / `validate_factor_code` / `generate_factor_id` | `factor_engine.factor_program` | 因子程序创建/校验/ID |
| `evolve_micro(factor, data, fwd_ret, n_trials)` | `factor_engine.micro_evolution` | 微观参数优化 |
| `synthesize_signals(factors, mode)` | `factor_engine.portfolio_loop` | 信号合成 |
| `orthogonalize_factors` / `decay_test` / `build_combo` / `load_elite_factors` | `factor_engine.portfolio_loop` | L3 组合工具 |
| `atomic_write` / `atomic_read` / `atomic_write_state` | `core.atomic` | 原子文件操作 |
| `parse_expression` / `validate_expr` / `evaluate` / `compute_max_lookback` / `collect_fields` / `eval_fts_expr` | `factor_engine.expr_dsl.*` | DSL 全流程 |
| `get_config()` / `load_config()` | `config.settings` | 配置 |
| `get_llm_client(backend)` | `llm` | LLM 客户端工厂 |
| `load_all_yaml_seeds` / `load_futures_seeds_full` / `load_stock_seeds` | `factor_engine.seed_loader` / `seed_data_futures_full` | 种子加载 |
| `get_dominant_contracts` / `get_realtime_prices` / `get_futures_provider` | `data_futures` | 期货数据工具 |
| `migrate_schema(db_path)` | `data_sources.migrate` | DuckDB schema 迁移 |
| `check_all_status` / `check_loop_status` / `check_data_sources_status` | `monitor` | 状态监控 |
| `list_tasks` / `get_task` / `run_scheduler` | `scheduler` | 调度工具 |
| `create_signal_model(kind)` | `ml.models` | ML 模型工厂（缺失返回 None） |

---

## 6. 依赖关系图

### 6.1 模块依赖关系

```
cli.py（统一入口，session_id + trace_id）
  ├── config.settings ──→ FTSConfig（dataclass + YAML + 环境变量）
  ├── core.atomic ──→ 原子文件操作
  ├── data ──→ FTSDataProvider
  │           ├── data_mcp ──→ 腾讯 HTTP API（A 股/ETF）
  │           ├── data_fundamental ──→ MCPBridge → 东方财富 mx 缓存
  │           ├── data_futures ──→ DuckDB kline_cache + 聚合器
  │           └── data_sources ──→ 多源适配器
  │               ├── aggregator ──→ 熔断器 + 交叉验证 + DuckDB 缓存
  │               ├── fusion ──→ 5 种融合策略
  │               ├── tq_source / tqsdk_source / tdx_minute_source / akshare_minute_source
  │               ├── tqsdk_tick_source（tick）
  │               └── wind_source / ifind_source / macro_aligner（字段增强层）
  ├── factor_engine ──→ 核心因子引擎
  │   ├── contracts ──→ 所有模块依赖的核心 TypedDict 契约
  │   ├── evolution_loop ──→ macro_evolution → micro_evolution
  │   │                        → evaluation_chain → verifier
  │   │                        → audit → factor_quality_card → factor_inspector
  │   │                        → backtest_pipeline → seed_pool → experience_chain → state
  │   ├── meta_loop ──→ BootstrappingChain → L1Verifier → llm
  │   ├── portfolio_loop ──→ signal_contract → factor_db.repository
  │   │                        → factor_clustering → factor_optimizer → ml
  │   ├── expr_dsl ──→ parser → validator → executor → runtime
  │   ├── gp_evolver / operator_evolution ──→ expr_dsl + feature_ops
  │   ├── regime / regime_hmm / regime_features ──→ adaptive_weight → portfolio_loop
  │   ├── factor_db ──→ DuckDB（factor_catalog 等）
  │   └── feedback_loop ──→ experience_chain → monitor
  ├── monitor ──→ factor_engine.monitor（底层 check_loop/check_all）
  │           ├── http_server ──→ 纯标准库 HTTPServer（9100）
  │           ├── elite_tracker ──→ core.atomic
  │           ├── prometheus_metrics ──→ 指标注册表
  │           └── data_quality_monitor / logic_monitor / live_factor_monitor
  ├── scheduler ──→ APScheduler（缺失降级）
  │           ├── tasks ──→ jobs（10 个工作函数）
  │           ├── watchdog / hotswap
  ├── risk ──→ 独立风控检查（无外部依赖）
  ├── ml ──→ lightgbm / xgboost（可选）
  ├── bridge ──→ redis-py（可选）
  ├── cross_market ──→ factor_engine（FactorExecutor）+ data
  └── llm ──→ OpenAI / Anthropic SDK（可选）

scripts/ ──→ fts.*（同步/信号/演化/验证脚本）
tests/   ──→ fts.*（100 个文件，2632+ 用例）
```

### 6.2 外部依赖

| 依赖 | 用途 | 必选/可选 | 安装组 |
|------|------|-----------|--------|
| numpy>=1.24 | 数值计算 | 必选 | 核心 |
| pandas>=2.0 | 数据处理 | 必选 | 核心 |
| scipy>=1.10 | 统计分析 | 必选 | 核心 |
| pyyaml>=6.0 | YAML 配置 | 必选 | 核心 |
| shap>=0.46 | SHAP 分析 | 必选 | 核心 |
| python-dotenv>=1.0 | .env 加载 | 必选 | 核心 |
| optuna>=3.0 | 贝叶斯调参 | 可选 | `[evolution]` |
| openai>=1.0 / anthropic>=0.20 | LLM 调用 | 可选 | `[llm]` |
| akshare>=1.18.64 | 金融数据 | 可选 | `[mcp]` |
| scikit-learn>=1.3 | 组合构建 | 可选 | `[portfolio]` |
| lightgbm>=4.0 / xgboost>=2.0 | ML 模型 | 可选 | `[ml]` |
| redis>=5.0 | 信号桥接 | 可选 | `[bridge]` |
| pytest>=7.4 / pytest-cov>=4.1 | 测试 | 开发 | `[dev]` |
| APScheduler | 定时调度 | 运行时 | 需手动安装 |
| duckdb | 数据存储 | 运行时 | 需手动安装 |
| httpx | HTTP 客户端 | 运行时 | 需手动安装 |
| statsmodels | HMM/MSM 回归 | 运行时 | 需手动安装 |

---

## 7. 项目运行方式

### 7.1 安装

```bash
# 基础安装
pip install -e .

# 全部可选依赖（推荐开发环境）
pip install -e ".[evolution,llm,mcp,portfolio,ml,bridge,dev]"

# 额外运行时依赖
pip install apscheduler duckdb httpx statsmodels
```

### 7.2 环境配置

创建 `.env` 文件（项目根目录，参见 `.env.example`，**禁止提交 GitHub**）：

```env
# LLM 配置（可选，缺省时自动使用 Mock）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o
FTS_LLM_BACKEND=openai
FTS_LLM_TEMPERATURE=1.2

# 期货数据源（可选）
TQSDK_USERNAME=xxx
TQSDK_PASSWORD=xxx

# FTS 配置（可选，有默认值）
FTS_MEMORY_DIR=memory
FTS_ELITE_DIR=memory/knowledge/factors/elite
FTS_FUTURES_ELITE_DIR=memory/knowledge/factors/futures_elite
FTS_DEFAULT_MARKET=futures
FTS_EVOLUTION_MODE=hybrid
FTS_MAX_WORKERS=4
FTS_LOG_LEVEL=INFO
```

### 7.3 运行命令

```bash
# 查看版本
fts version

# 查看监控状态
fts monitor
fts monitor --json

# L0 程序设定
python -m fts.cli seed validate          # 种子校验
fts seed report                          # 种子统计

# 启动 L1 Meta-Loop
fts meta-loop run
fts meta-loop run --market futures --symbols rb,i,au,sc

# 启动 L2 因子演化（期货横截面，默认）
fts evolution run --max-generations 10
# L2 演化（沪深300 横截面）
fts evolution run --universe csi300 --max-stocks 20
# L2 演化（单标模式）
fts evolution run --universe single --symbol 000001

# 启动 L3 组合构建
fts portfolio run --universe futures --synthesis-mode sharpe_weight
fts portfolio run --universe stock --synthesis-mode elastic_net

# 因子管理
fts factor list --market futures
fts factor list --market futures --diverse --total-count 10
fts factor stats --market futures --json
fts factor show <factor_id>
fts factor lineage <factor_id>
fts factor seeds --market futures
fts factor cross-market --direction futures-to-stock --days 120 --max-stocks 10

# 回测
fts backtest run --factor-id <factor_id> --market futures --days 500
fts backtest run --factor-id <factor_id> --frequency 5m --days 30
fts backtest batch --grade B --limit 20
fts backtest compare --factor-ids "fct_abc,fct_def"

# 特征工程 / GP
fts feature list --category time_series --json
fts feature analyze --factor-id <factor_id>
fts gp evolve --universe futures --population 200 --generations 50

# 反馈闭环
fts feedback trigger --factor-id <factor_id> --reason "manual review"
fts feedback process
fts feedback report --month 2026-08

# 信号桥接
fts bridge serve --host 127.0.0.1 --port 8765
fts bridge publish --protocol json --input signals/latest.json
fts bridge status --protocol redis

# 调度器 / Web UI
fts scheduler run
fts scheduler list
fts ui --port 9100
```

### 7.4 关键脚本（`scripts/`）

| 脚本 | 用途 |
|------|------|
| `sync_futures_data.py` | 手动触发多源期货数据同步（`--symbols --days --universe --json`） |
| `daily_signal_pipeline.py` | 股票每日信号管道 → `docs/daily_signals_{date}.md` |
| `futures_signal_pipeline.py` | 期货横截面信号管道（Ridge 加权 + 多空双向）→ `reports/{date}/futures_signals_*.md` |
| `run_futures_evolution.py` | 期货 L2 演化（DuckDB 截面数据） |
| `run_factor_audit.py` / `run_factor_audit_real.py` | 因子审计 |
| `cross_market_revalidation.py` | 跨市场泛化验证 |
| `full_chain_diagnosis.py` / `full_chain_diagnosis_v2.py` | 全链路诊断 |
| `alignment_backtest.py` | 回测与实盘信号对齐检查 |
| `verify_doc_consistency.py` | 文档一致性校验（HARNESS Layer 2） |
| `update_doc_versions.py` | 版本号同步 |
| `start_fts.ps1` | PowerShell 启动脚本（加载 .env、配置 PATH、打印命令清单） |

### 7.5 开发模式

```bash
# 运行测试（全量）
python -m pytest tests/ --no-cov --tb=short
# 指定模块
python -m pytest tests/factor_engine/ -q
# 覆盖率
python -m pytest tests/ --cov=fts --cov-report=term-missing
# 代码检查
ruff check fts/
```

---

## 8. 配置系统

### 8.1 配置层次

```
环境变量 (FTS_*) → YAML 配置文件（config/settings.yaml）→ Python 默认值（FTSConfig dataclass）
```

### 8.2 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FTS_MEMORY_DIR` | `memory` | 运行时持久化目录 |
| `FTS_ELITE_DIR` | `memory/knowledge/factors/elite` | 股票精英因子目录 |
| `FTS_FUTURES_ELITE_DIR` | `memory/knowledge/factors/futures_elite` | 期货精英因子目录 |
| `FTS_DEFAULT_MARKET` | `futures` | 默认市场 |
| `FTS_EVOLUTION_MODE` | `hybrid` | 演化模式（operator/code/hybrid） |
| `FTS_MAX_WORKERS` | `4` | 并行工作数 |
| `FTS_LLM_BACKEND` | `` | LLM 后端 |
| `FTS_LLM_TEMPERATURE` | `1.2` | LLM 采样温度 |
| `FTS_MACRO_FIELD_INJECTION` | `1` | 宏观字段注入开关 |
| `FTS_MACRO_LAG_DAYS` | `30` | 宏观发布滞后天数（防未来函数） |
| `FTS_LOG_LEVEL` | `INFO` | 日志级别 |
| `FTS_LOG_FILE` | `` | 日志文件路径 |
| `FTS_CONFIG_FILE` | `` | 配置文件路径 |

### 8.3 关键配置字段（FTSConfig）

`memory_dir` / `elite_dir` / `futures_elite_dir` / `default_market` / `evolution_mode` / `max_generations(10)` / `population_size(20)` / `micro_trials_per_generation(50)` / `max_workers(4)` / `meta_loop_interval_hours(24)` / `meta_loop_max_tokens(8000)` / `portfolio_max_factors(20)` / `portfolio_top_n(5)` / `portfolio_decay_days(90)` / `verifier{min_sharpe 1.5, max_correlation 0.5, max_turnover 0.50, max_decay_rate 0.30, min_n_factors 3}` / `log_level` / `log_file`。

### 8.4 质量评分卡配置

`fts/config/factor_quality_card_config.py`：`get_conservative_config` / `get_aggressive_config` / `get_permissive_config` / `get_futures_config`（期货放宽 grade_B_min=24）/ `create_config(weights, thresholds)`。

---

## 9. 数据流与执行流程

### 9.1 因子演化全流程

```
L1 Meta-Loop (08:30)
  ├── agentic 市场感知（FTSDataProvider 市场快照）
  ├── debate 分析（识别薄弱维度）
  ├── Bootstrapping（LLM bootstrap_factors → 候选因子）
  ├── L1 Verifier（economic_logic >= 2/4 + 可执行 + 非重复 + narrative）
  └── 注入 seed_pool + factor_pool.json
        ▼
L2 Evolution Loop (23:00)
  ├── DataQualityMonitor 数据完整性校验
  ├── State 加载 + 熔断预检查（token 预算/失败率/连续低 IC）
  ├── UCT 父因子选择（UCB，C=1.0）
  ├── 宏观演化（LLM 改逻辑）→ 微观演化（optuna）
  ├── 三级评估链（L1 回测 → L2 经济逻辑 → L3 多重检验）
  ├── BacktestPipeline 回测 → Verifier 判定（6 项）
  ├── 质量评分卡（10 维 A/B/C 分级）→ FactorAuditor 审计（6 项强制）
  ├── 经验链记录 → 分级准入（A/B 晋升精英，C 淘汰）
  ├── 影子池 5 日观察 → 状态持久化 + DuckDB 同步（idempotent write）
  ├── 可选：GP 演化 / 算子演化 / 因子聚类 + PCA
        ▼
L3 Portfolio Loop (20:00)
  ├── 加载精英因子（DuckDB / JSON 回退）→ FactorScreener 筛选
  ├── 信号合成（equal_weight / sharpe_weight / elastic_net / ml_ensemble）
  ├── Regime 自适应权重 → 正交化 / 聚类（ACTIVE_FACTOR_CAP=20）/ PCA
  ├── 衰减检验（6 个月衰减率 > 0.3 剔除）→ 组合构建（粘性约束）
  ├── L3 Verifier 判定 → 漂移监控记录
  ├── 风控检查（RiskManager 5 项规则）
  └── 输出信号 → SignalBridge 发布 / 注入 FDT / 触发信号管道
        ▼
反馈闭环（持续）
  ├── 触发条件检查（Live 偏离/数据异常/定期评估/审计失败/因子衰减）
  ├── 归因分析（5 种根因）→ 演化方向调整
  └── 月度迭代效果报告
```

### 9.2 数据获取流程

```
期货日线:
  DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
    → 字段增强（WIND / IFIND / MacroFieldAligner 并行）
    → 熔断器（连续 5 次失败 → UNAVAILABLE → 6h 冷却）
    → 交叉验证（≥2 源差异 > 0.5% 记录 disagreements.jsonl）
    → FuturesDataAggregator → 因子引擎

期货分钟: minute_cache → TDX_MINUTE(17709) → TQ_LOCAL(7721) → TQSDK
期货 tick: tick_cache → TQSDK_TICK
A 股/ETF: 腾讯 HTTP API → 合成数据降级
基本面:   MCPBridge 缓存 → 合成数据降级
```

### 9.3 因子质检流程

```
因子计算 → 三级评估 → Verifier 判定
                    → 质量评分卡（10 维 A≥35 / B≥25 / C<25，期货 B≥24）
                    → FactorAuditor 审计（6 项强制，渐进式 skipped）
                    → 全部通过 → 影子池观察 5 日 → 晋升精英 + DuckDB 同步
                    任一项失败 → 记录失败轨迹 + 阻断晋升
```

---

## 10. 测试体系

### 10.1 测试概况

- **测试文件**: 100 个
- **测试用例**: 2632+ 个（`def test_*` 计数）
- **测试框架**: pytest + pytest-cov
- **测试配置**: `pyproject.toml` `[tool.pytest.ini_options]`（testpaths=["tests"]）

### 10.2 测试目录结构

```
tests/
├── core/                       # 核心契约测试（test_atomic / test_contracts / test_enums）
├── data_sources/               # 数据源测试（aggregator / fusion / base / tq / wind / ifind / migrate / macro_aligner / tdx_minute / tick_microstructure）
├── factor_engine/              # 因子引擎测试（核心，~65 个文件）
│   ├── expr_dsl/               # DSL 测试（parser/registry/validator/executor/compiler/factory）
│   ├── factor_db/              # 数据库测试
│   └── operator_evolution/     # 算子演化测试
│   ├── test_evolution_loop.py  # L2 主循环（133 用例）
│   ├── test_portfolio_loop.py  # L3 组合（93 用例）
│   ├── test_meta_loop.py       # L1 元循环（79 用例）
│   ├── test_factor_quality_card.py  # 质量评分卡（105 用例）
│   └── ...
├── monitor/                    # 监控测试（elite_tracker / data_quality_monitor / logic_monitor 等）
├── scheduler/                  # 调度测试（engine / tasks / watchdog / hotswap / sync_futures_task）
├── scenarios/                  # 场景测试（test_scenarios / test_natural_experiments）
├── cli/                        # CLI 测试
├── test_e2e.py                 # 端到端
├── test_llm.py / test_ml_models.py / test_bridge.py / test_http_server.py
├── test_cross_market.py / test_data_futures_panel.py / test_backtest_frequency.py
└── test_stage5_risk_live.py    # Phase 5 风控实盘测试
```

### 10.3 运行测试

```bash
# 全量测试
python -m pytest tests/ --no-cov --tb=short
# 带覆盖率
python -m pytest tests/ --cov=fts --cov-report=term-missing
# 指定模块
python -m pytest tests/factor_engine/ -v
# 关键字过滤
python -m pytest -k "test_evolution" -v
```

---

## 11. 附录 A: 版本历史

| 版本 | 主要变更 |
|------|---------|
| v2.46.0 | 当前版本（2026-08-08） |
| v2.38.0+ | Phase 24 ML 模型层、Phase 25 信号桥接（SignalBridge）、L3 ml_ensemble 模式 |
| v2.36.0 | P1 因子聚类 + P2 PCA 降维（factor_clustering.py） |
| v2.35.0 | L3 Elastic Net 合成 + ACTIVE_FACTOR_CAP=20 |
| v2.30.0+ | 分钟级回测（TDX_MINUTE / AKShare 分钟源） |
| v2.29.0 | P2 修复（zscore_window、forward_returns 截断） |
| v2.27.0 | 跨市场泛化验证（fts/cross_market/） |
| v2.3.0 | 期货多源数据适配器、熔断器、5 种融合策略 |
| v1.1.0 | L1/L2/L3 三层循环架构初始实现 |

> 完整版本历史见 `docs/harness/07-operations.md`。

---

## 12. 附录 B: 相关文档

| 文档 | 位置 | 说明 |
|------|------|------|
| README | `README.md` | 项目快速开始 |
| FTS 手册 | `docs/FTS_manual.md` | 完整用户手册 |
| 架构文档 | `docs/harness/01-architecture.md` | Harness 架构规范 |
| 生命周期 | `docs/harness/02-lifecycle.md` | 阶段生命周期 |
| 配置文档 | `docs/harness/03-configuration.md` | 配置系统规范 |
| 弹性文档 | `docs/harness/04-resilience.md` | 熔断/降级/超时 |
| 可观测性 | `docs/harness/05-observability.md` | 指标/日志/监控 |
| 测试文档 | `docs/harness/06-testing.md` | 测试用例与覆盖率 |
| 操作文档 | `docs/harness/07-operations.md` | 版本历史与操作手册 |
| 差距管理 | `docs/harness/08-gap-analysis.md` | 技术债务登记 |
| 晋级计划 | `docs/harness/09-advancement-plan.md` | 晋级里程碑 |
| 设计文档 | `docs/harness/design/` | A.1-C.4 设计文档 |
| 验收测试 | `docs/harness/acceptance/` | 各阶段验收测试 |
| 技术评审 | `docs/harness/tech-review/` | P1/P2 技术评审 |
| 工程准则 | `AGENTS.md` / `CLAUDE.md` | 项目工程规范与行为准则 |
