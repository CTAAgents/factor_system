# FTS (Factor Trading System) — Code Wiki

> **版本**: v2.103.0 | **最后更新**: 2026-08-12
>
> 本文档基于当前源代码重新分析生成（旧版停留在 v2.46.0，本项目已历经数据层收敛 plans/29、Regime 机构级优化 plans/28、模拟交易体系 D.1/D.2、算子库扩容等重大演进），是 FTS 项目的代码级参考文档，面向开发者阅读。
> 覆盖：项目整体架构、主要模块职责、关键类与函数说明、依赖关系、项目运行方式。
>
> **⚠️ 工程现状标注（2026-08-12）**：**FTS 主系统定位为期货因子智能系统**。股票管线已通过 plans/32 不对称分离剥离为独立项目 `d:\Programs\fts-stock`（v0.0.1，独立 pyproject/venv/CLI/测试/种子库/数据/文档 + `fts_core` 共享内核 hash 同步），两者互不干扰。**主系统清理已执行**：股票专属模块（evolution_stock/stock_regime/neutralization/stock_pipeline/data_mcp/data_fundamental/data_mcp_bridge/ashare_special_source/stock_fundamental_source/cross_market）与股票脚本/测试已删除，共享文件的股票分支（cli/portfolio_loop/evolution_loop/meta_loop/scheduler/factor_db/cost_model 等）已剥离。文中保留的 A 股/ETF 相关描述仅作历史与架构参考（barra 风格中性化因期货横截面使用而保留），不再属于期货主系统的运行路径。

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
   - 4.6 [存储注册表与状态库 `fts.store`](#46-存储注册表与状态库-ftsstore)
   - 4.7 [模拟交易体系 `fts.live_trade`](#47-模拟交易体系-ftslive_trade)
   - 4.8 [监控层 `fts.monitor`](#48-监控层-ftsmonitor)
   - 4.9 [调度层 `fts.scheduler`](#49-调度层-ftsscheduler)
   - 4.10 [风控层 `fts.risk`](#410-风控层-ftsrisk)
   - 4.11 [ML 模型层 `fts.ml`](#411-ml-模型层-ftsml)
   - 4.12 [信号桥接层 `fts.bridge`](#412-信号桥接层-ftsbridge)
   - 4.13 [跨市场验证 `fts.cross_market`](#413-跨市场验证-ftscross_market)
   - 4.14 [LLM 客户端 `fts.llm`](#414-llm-客户端-ftsllm)
   - 4.15 [CLI 入口 `fts.cli`](#415-cli-入口-ftscli)
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
数据源（DuckDB / TDX-Local 17709 / TQSDK / AKShare / Wind / iFinD / 东财 / 腾讯）
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
| **四层循环体系** | L0 Program（人类设定）+ L1 Meta-Loop（工作日 07:59 知识补给）+ L2 Evolution Loop（工作日 00:00 夜间演化）+ L3 Portfolio Loop（工作日每日 19:00 期货 / 19:30 股票已剥离 fts-stock） |
| **多市场支持** | 🎯 **主系统=期货**（定位）：期货 82 全量连续合约 / 25 核心 / 15 盲测池 / 21 分层训练集。A 股/ETF 已剥离至独立项目 `d:\Programs\fts-stock`（v0.0.1），本仓库内残留的股票代码（evolution_stock/data_mcp/barra 等）为**工程残余**，不属于主系统设计意图 |
| **因子种子库** | 期货 YAML 种子 20 文件（seeds/futures/，主路径）+ Python 硬编码 81 种子 14 家族；股票 9 内置 + WQ101 + Qlib158 + GTJA191 + 基本面 + JQ 外部库 + YAML |
| **FTS-Expr DSL** | 算子表达式语言，**512 项算子**（20 字段 + 492 算子，L0-L5 分层），支持表达式因子，双注册表一致性校验 |
| **进化搜索双引擎** | GP 遗传规划（表达式树）+ OperatorEvolution（ExprNode 层面）+ 符号回归（确定性 beam-search）+ 批量挖掘漏斗（GAP-I201）+ 深度因子（GRU/Transformer，纯 numpy） |
| **演化执行后端** | 可插拔 Thread / Process / Dask / Ray 四后端（`executor_backend.py`），调用方无感知切换 |
| **六层存储架构** | plans/29 收敛：L1 配置(YAML) → L2 行情库(DuckDB+Parquet 归档) → L3 因子资产库(DuckDB SSOT) → L4 运行状态库(state.duckdb) → L5 信号缓存(Parquet) → L6 日志血缘(JSONL)；`fts/store` 注册表契约 + `StateKVStore` 双表（当前态+历史回放） |
| **多源数据融合** | K 线主路径 5 级降级（DUCKDB_CACHE → TDX_LOCAL(17709) → TQ_PYTHON → AKSHARE → SYNTHETIC）+ 字段增强层（TQSDK/IFindSDK/Wind/iFinD）+ 熔断器（5 次失败 6h 冷却）+ 交叉验证（0.5% 分歧记录）+ 5 种融合策略 |
| **因子质检** | 10 维 0-50 分质量评分卡（A/B/C 分级）+ 6 项强制审计（渐进式 skipped）+ HighICScreener 16 项检查×5 项一票否决 + 五层逻辑审查 |
| **Market Regime** | 四级检测链（多周期 HMM → MSM → 单周期 HMM → 规则），5 种市场状态 + 机构级优化（概率混合 / 熵标定 / BIC 选态 / 数据驱动倍率 / 样本外验证） |
| **回测流水线** | 4 阶段管线（DataLoad → FactorCompute → Performance → Report），日频+分钟频率（1m~60m），盈亏比/利润因子/成本敏感性 |
| **模拟交易体系** | 模拟仓（开/加/减/平/反手、盯市、归因）+ tick 盘口撮合（逐档消耗/部分成交/限价单/集合竞价）+ 组合级风控三级预警 + 人工干预 + SQLite 持久化 + 回放/纸面双引擎 |
| **反馈闭环** | 6 种反馈事件 + 5 种根因归因 + 演化方向自适应调整 + 月度迭代报告 + 实盘 IC 导入 |
| **风控系统** | 5 项风控规则（仓位/回撤/亏损/杠杆/集中度）+ 组合级三级预警（WARN/BLOCK/FORCE_CLOSE） |
| **Prometheus 监控** | 数据源/因子/系统三维指标 + HTTP 端点 + Web UI 仪表盘（9100）+ K8s 部署 |
| **信号桥接** | JSON / Redis / REST 三种协议信号发布，VNPY 对接 |
| **Alpha 人审工作流** | factor_reviews 表 + Web 人审工作台 + AutoReviewPolicy 机审分类 + 幂等 UPSERT |

### 技术栈

- **语言**: Python 3.10+（项目内 Python 路径 `C:\Program Files\Python312\python.exe`）
- **核心依赖**: numpy, pandas, scipy, pyyaml, shap, python-dotenv, duckdb
- **可选依赖**: optuna（演化）、openai/anthropic（LLM）、akshare（数据）、scikit-learn（组合）、lightgbm/xgboost（ML）、redis（桥接）、hmmlearn/statsmodels（Regime）、fastapi/uvicorn（监控）、distributed（分布式）
- **数据存储**: DuckDB（`data/fts_history.duckdb` 行情库 / `data/factor_catalog_{stock,futures}.duckdb` 因子资产库 / `data/state.duckdb` 运行状态库）+ Parquet（信号缓存/冷归档）+ SQLite（模拟仓持久化）
- **调度**: APScheduler（未安装时静默降级）+ ProcessWatchdog 进程守护
- **监控**: 纯标准库 HTTP 仪表盘 + Prometheus 端点（`/metrics`）
- **包管理**: setuptools，`pyproject.toml` 定义元数据，`fts` 命令入口注册

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
│                    L1 Meta-Loop（工作日 07:59）                       │
│  agentic 市场感知 → debate 分析 → Bootstrapping 三源候选              │
│  （提取器/LLM/模板）→ L1 Verifier 判定 → 注入 factor_pool.json         │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ 种子候选（l1_injected/）
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L2 Evolution Loop（工作日 00:00）                     │
│  DataQualityMonitor 数据校验 → State 加载 → 熔断预检查                 │
│  循环（每代）:                                                       │
│    UCT 父因子选择 → 宏观演化（LLM）→ 微观演化（optuna 两阶段漏斗）      │
│    → 三级评估链（L1 回测 → L2 经济逻辑 → L3 多重检验）                 │
│    → BacktestPipeline 回测 → Verifier 判定 → 质量评分卡（10 维）      │
│    → FactorAuditor 审计（6 项强制）→ 消融/因果/鲁棒性/SHAP            │
│    → 经验链记录 → 分级准入（结构簇配额 + L2 正交去冗余）                │
│    → 影子池 5 日观察 → DuckDB 先写（SSOT）→ JSON 快照备份              │
│  可选：GP / 算子演化 / 符号回归 / 批量挖掘 / 深度因子（GRU/Transformer）│
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ 精英因子（futures_elite|stocks_elite）
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L3 Portfolio Loop（工作日每日 19:00 期货 / 19:30 股票已剥离）                 │
│  加载精英因子（DuckDB SSOT 优先）→ 质量门槛/影子池过滤 → 基础名去重     │
│  → ACTIVE_FACTOR_CAP=20 → 聚类/PCA（可选）→ 信号合成（9 种模式）       │
│  → Regime 自适应权重（family×style + 概率混合 + 置信度缩放）            │
│  → 正交化/衰减检验 → 组合构建（粘性约束/换手惩罚）→ Verifier 判定       │
│  → 漂移监控/归因/走航/组合级风控 → 输出信号 / SignalBridge 发布         │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 六层存储架构（plans/29 SSOT 单一事实源）

```
L1 配置层      seeds/*.yaml + config/                        （YAML）
L2 行情库      data/fts_history.duckdb：kline_cache / contract_kline /
               minute_cache / tick_cache / edb_cache / option_chain_cache /
               stock_kline_cache / ashare_special_cache / stock_fundamental_cache
               （追加式 + 按年冷热归档 data/archive/*.parquet）
L3 因子资产库  data/factor_catalog_{stock,futures}.duckdb     （DuckDB 权威存储，JSON 只读快照）
L4 运行状态库  data/state.duckdb：state_kv（当前态）+ state_history（历史回放）
L5 信号缓存    memory/cache/factor_signals/                  （Parquet + checksum）
L6 日志血缘    JSONL 保留追加 + 摘要入库

核心约束：fts/store/registry.py StorageRegistry（storage_landscape.yaml 契约）为唯一
读写入口契约；新增数据域必须先登记后落库；禁止同类数据双写漂移。
```

### 2.3 Market Regime 检测体系（`regime.py` 系列）

```
RegimeAwareSelector.detect() 四级检测链（置信度 gate ≥ 0.3）:
  1. MultiHorizonHMMDetector（主，默认）— 多周期 [63,126,252] 加权投票，输出全制度概率分布
  2. MSMRegimeDetector（P3.1，默认关）    — statsmodels MarkovRegression
  3. HMMRegimeDetector（次）              — 单周期 GaussianHMM（BIC 选状态数）
  4. _detect_by_rule（规则回退）          — 多周期趋势投票 + ADX + EWMA 波动率
  兜底：oscillate / confidence 0.5

输出 5 种状态：bull / bear / oscillate / high_vol / low_vol
股票侧（stock_regime）：large_cap / small_cap / growth / value / sector_concentrated / sector_rotating

机构级优化（plans/28）：
  - regime_probs 概率混合（probability_mix，无 probs 回退硬查表）
  - RegimeConfidenceCalibrator 熵标定 → exposure_scale 置信度仓位缩放
  - RegimeSmoother 不对称切换（de_risk_alpha=0.8 快速降权 / re_risk_alpha=0.1 缓慢加仓）
  - BIC 状态数选择（select_n_states 防翻转）+ fit_standardizer 仅 fit 训练段防数据窥探
  - RegimeMultiplierEstimator 数据驱动倍率（GAP-L308 替代硬编码查表）
  - validate_regime_predictive_power Kruskal-Wallis 样本外有效性验证
  - fts_regime_* 观测指标（/metrics 审计）
```

### 2.4 数据流架构

```
期货日线 K 线主路径（5 级降级）:
  DUCKDB_CACHE → TDX_LOCAL(17709) → TQ_PYTHON(TQSDK) → AKSHARE → SYNTHETIC
                    ↓
             字段增强层（并行，不阻断主路径）:
               TQSDKEnhanceSource（hold/oi_change，默认注册）
               IFindSDKSource / Wind / iFinD（settle/oi_change，futures_enhance_enabled 可选）
                    ↓
              熔断器（每源连续 5 次失败 → UNAVAILABLE → 6h 冷却 → 探活恢复）
                    ↓
              交叉验证（≥2 源同日 close 差异 > 0.5% 记录 data/data_source_disagreements.jsonl）
                    ↓
              FuturesDataAggregator（统一数据入口，DuckDB 缓存优先，pre_settle 零依赖派生）
                    ↓
              换月复权（RollCalendar 比率法后复权）+ 夜盘跳空注入（OvernightGap，配置开关）
                    ↓
              因子引擎（四层循环）

期货分钟路径:  minute_cache → TDX_LOCAL(17709, 5m) → TQSDK
期货 tick 路径: tick_cache → TQSDK_TICK（5 档盘口 32 列）
A 股/ETF 路径:  TDX_LOCAL 17709 股票端点（qfq/hfq 复权）→ 腾讯 HTTP API → 合成数据降级
基本面路径:     MCPBridge 本地缓存（东方财富 mx API）→ 合成数据降级
A股特有字段:    AshareSpecialSource（两融/股东户数/北向/分析师，GAP-081）+ StockFundamentalSource（估值/财务，GAP-082）
宏观字段:       EastmoneyMacroSource（EDB 缓存）+ MacroFieldAligner（lag_days=30 防未来函数）
期货基本面:     AkshareFuturesFundamentalProvider（库存/基差/仓单）
```

### 2.5 反馈闭环架构

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
                 EvolutionEffectiveness（月度评估）+ LiveFeedbackImporter（实盘 IC 导入）
```

---

## 3. 目录结构

```
fts/                            # 核心源码包（pyproject.toml: include = ["fts*"]，约 150 个 py 文件）
├── __init__.py                 # 包初始化：从 pyproject.toml 动态读取版本号，自动加载 .env
├── cli.py                      # 统一命令行入口（2819 行，17 个顶级子命令）
│
├── core/                       # 核心契约层
│   ├── enums.py                # FTS 特有枚举（EvolutionStage/FactorPriority/FactorStatus/DataSource(10)/FusionStrategy）
│   ├── contracts.py            # 因子引擎契约重导出 + FusedOHLCV/OHLCVBase/FuturesOHLCV 数据契约
│   └── atomic.py               # 原子文件写入/读取（临时文件 + os.replace + 备份轮转）
│
├── config/                     # 配置系统
│   ├── settings.py             # FTSConfig dataclass（YAML + 环境变量 + 默认值，592 行）
│   └── factor_quality_card_config.py  # 质量评分卡维度权重/阈值配置（5 预设 + 自定义）
│
├── factor_engine/              # 因子引擎（核心模块，约 100 文件）
│   ├── __init__.py             # 导出所有核心组件（约 450 行）
│   ├── contracts.py            # L1/L2/L3 三层 TypedDict 契约（SSOT）+ 默认配置常量
│   ├── factor_program.py       # 因子程序接口（安全沙箱编译执行，白名单/黑名单，自动修复）
│   ├── program.py              # L0 人类设定层（program.md 解析器）
│   ├── seed_pool.py / seed_loader.py / seed_data_futures_full.py / seed_data/
│   ├── evolution_loop.py       # L2 主循环（UCT 选择 + 三重熔断 + 准入链 + 晋升门）
│   ├── evolution_futures.py / evolution_stock.py  # 市场专用引擎分叉（F.2）
│   ├── meta_loop.py            # L1 主循环（感知 → 辩论 → Bootstrapping → 注入）
│   ├── portfolio_loop.py       # L3 主循环（9 种信号合成 + Regime 自适应 + 组合构建）
│   ├── batch_mining.py         # 批量挖掘漏斗（batch_size=20 生成 → 并行粗筛 → 截断 5）
│   ├── executor_backend.py     # 可插拔执行后端（Thread/Process/Dask/Ray）
│   ├── macro_evolution.py / micro_evolution.py  # 宏观（LLM 改逻辑）/ 微观（optuna 两阶段）
│   ├── evaluation_chain.py     # 三级评估链 + 走航 + 极值扰动
│   ├── verifier.py             # Verifier 锁定协议（初始化后不可修改）
│   ├── standardizer.py         # 6 种标准化方法
│   ├── experience_chain.py / success_pattern.py / experiment_log.py
│   ├── factor_quality_card.py  # 10 维评分卡（0-50 分 A/B/C）
│   ├── audit.py                # 6 项强制审计 + FailureClassifier（10 种失败模式）
│   ├── high_ic_screener.py     # 16 项检查×5 项一票否决
│   ├── factor_inspector.py     # 因子巡检降级 + FactorReviewWorkflow 人审工作流
│   ├── factor_clustering.py / orthogonal_basis.py / neutralization.py
│   ├── barra/                  # Barra 10 风格暴露 + 风格×行业双重中性化
│   ├── factor_optimizer.py     # 4 层分层优化框架 + 信号缓存（Parquet+checksum）
│   ├── factor_screener.py / signal_generator.py / portfolio_constructor.py
│   ├── capital_allocator.py / cost_model.py / cost_simulator.py / cost_sensitivity.py
│   ├── risk_attributor.py / report_generator.py / backtest_pipeline.py
│   ├── regime.py / regime_hmm.py / regime_features.py / regime_calibration.py
│   ├── regime_model_selection.py / regime_multipliers.py / regime_validation.py / stock_regime.py
│   ├── adaptive_weight.py      # 自适应权重（Regime 家族/风格倍率 + 不对称平滑）
│   ├── weight_learning.py      # 权重学习（ElasticNet/滚动 OOS/跨市场 IC/IC 协方差）
│   ├── black_litterman.py / portfolio_optimizer.py / portfolio_risk_controls.py
│   ├── portfolio_walk_forward.py / symbol_holdout.py / walk_forward.py
│   ├── factor_returns.py / horizon_analysis.py / multi_frequency.py
│   ├── microstructure_factors.py / microstructure_generator.py
│   ├── sector_linkage.py / position_rank_crowding.py / style_classifier.py
│   ├── ablation.py / shap_analyzer.py / robustness.py / causal_validator.py / stress_test.py
│   ├── gp_evolver.py / operator_evolution.py / symbolic_regression.py
│   ├── feature_ops.py / ops_library.py / feature_importance.py
│   ├── feedback_loop.py / recalibration.py / signal_contract.py / signal_cache.py
│   ├── monitor.py              # 循环状态检查（底层 check_loop/check_all）
│   ├── factor_db/              # DuckDB 因子资产库（13 张表）
│   ├── extractors/             # 因子提取管线（base/futures_pipeline/stock_pipeline/alternative_sources）
│   └── expr_dsl/               # FTS-Expr DSL（512 算子，L0-L5 分层）
│
├── data.py                     # FTSDataProvider（统一数据入口，组合 MCP/基本面/期货/期货基本面）
├── data_futures.py             # FuturesDataProvider（5 级降级 + 读写分离并发治理 + 品种清单）
├── data_fundamental.py         # FundamentalProvider（6 组基本面字段注入 + A股特有字段）
├── data_futures_fundamental.py # AkshareFuturesFundamentalProvider（库存/基差/仓单）
├── data_mcp.py                 # MCPDataProvider（TDX 股票端点 + 腾讯 HTTP API，A 股/ETF）
├── data_mcp_bridge.py          # MCPBridge（东方财富 mx API 缓存桥接）
│
├── data_sources/               # 数据源适配器（20 个文件）
│   ├── base.py                 # BaseFuturesSource 抽象基类 + SourceUnavailable + validate_ohlcv_row
│   ├── aggregator.py           # FuturesDataAggregator（多源调度 + 熔断器 + 交叉验证 + 缓存 + pre_settle 派生）
│   ├── fusion.py               # OHLCVFusion（5 种融合策略）
│   ├── tdx_local_source.py     # TdxLocalSource（通达信本地 17709 统一源，日线/分钟/快照/股票）
│   ├── tqsdk_source.py / tqsdk_enhance_source.py / tqsdk_tick_source.py
│   ├── ifind_source.py / ifind_sdk_source.py / wind_source.py
│   ├── macro_aligner.py / macro_eastmoney_source.py
│   ├── ashare_special_source.py / stock_fundamental_source.py
│   ├── roll_calendar.py / overnight_gap.py / migrate.py
│   └── akshare_minute_source.py
│
├── store/                      # 存储层（plans/29）
│   ├── registry.py             # StorageRegistry（storage_landscape.yaml 契约 + validate_contract）
│   └── state_db.py             # StateKVStore（state_kv 当前态 + state_history 历史回放）
│
├── live_trade/                 # 模拟交易体系（D.1/D.2）
│   ├── contracts.py            # 模拟仓契约 + CONTRACT_MULTIPLIERS 合约乘数表
│   ├── simulated_portfolio.py  # SimulatedPortfolio（开/加/减/平/反手、盯市、归因、风控集成）
│   ├── simulated_engine.py     # SimulatedReplayEngine（回放）/ SimulatedPaperTrader（纸面）
│   ├── book.py                 # OrderBookSnapshot / build_book_from_ticks（5 档盘口）
│   ├── matching.py             # OrderBookMatchingEngine（逐档撮合）
│   ├── orders.py               # Order 生命周期状态机 + OrderLifecycle
│   ├── stop_orders.py          # StopOrderManager（止损止盈单）
│   ├── intervention.py         # InterventionController（人工干预，权限最高）
│   ├── gateway.py              # AbstractGateway / SimulatedGateway / submit_with_retry
│   └── sqlite_store.py         # SimSQLiteStore（4 表持久化，WAL）
│
├── monitor/                    # 监控层
│   ├── http_server.py          # FTSDashboardServer（纯标准库，端口 9100，20+ 端点含人审工作台）
│   ├── prometheus_metrics.py   # MetricsRegistry（线程安全指标注册表）
│   ├── elite_tracker.py        # EliteFactorTracker + AutoRetireManager（状态机淘汰）
│   ├── data_quality_monitor.py # DataQualityMonitor（IC 漂移/容量突变 + 三维数据质量）
│   ├── data_level_monitor.py   # DataLevelMonitor（数据级：缺失/异常/复权/分歧，GAP-F06）
│   ├── logic_monitor.py        # LogicMonitor（行为漂移/极端预测/换月日异常）
│   └── live_factor_monitor.py  # LiveFactorMonitor（实盘偏离 30%/45%）
│
├── scheduler/                  # 调度层
│   ├── tasks.py                # TaskSpec + TaskRegistry + 15 个默认任务
│   ├── engine.py               # SchedulerEngine（APScheduler 包装，缺失时降级）
│   ├── jobs.py                 # 12 个任务工作函数
│   ├── watchdog.py             # ProcessWatchdog（3 次/30s 熔断 5 分钟）
│   └── hotswap.py              # HotSwapWatcher（开发期模块热重载）
│
├── risk/                       # 风控层
│   ├── risk_manager.py         # 5 项风控规则检查
│   ├── portfolio_metrics.py    # 组合级 6 维指标 + 三级预警（WARN/BLOCK/FORCE_CLOSE）
│   └── simulated_adapter.py    # SimulatedTradeAdapter（模拟成交适配器）
│
├── ml/                         # ML 模型层
│   ├── models.py               # MLSignalModel（LGBM/XGB/Ensemble）+ MLP/GRU/Transformer 纯 numpy 模型
│   ├── trainer.py              # SignalModelTrainer（三种训练模式）
│   └── deep_factor.py          # DeepFactorGenerator（深度因子生成，权重内嵌确定性 code）
│
├── bridge/                     # 信号桥接层
│   └── signal_bridge.py        # SignalBridge（JSON/Redis/REST 三协议）
│
├── cross_market/               # 跨市场泛化验证
│   ├── data_adapter.py         # CrossMarketDataAdapter（统一数据格式/路由）
│   └── engine.py               # CrossMarketEngine（三方向验证 + 因子分类 + 报告）
│
└── llm.py                      # LLM 客户端（OpenAI/Anthropic/Mock + 工厂 + 8 层 JSON 容错解析）

config/                         # 项目配置
├── settings.yaml               # FTS 全局配置 YAML
├── prometheus.yml / prometheus_alerts.yml / alertmanager.yml
seeds/                          # YAML 种子因子定义（futures/ 20 个文件）
data/                           # 运行时数据（fts_history.duckdb 等，git 忽略）
scripts/                        # 工具脚本（90+ 个）
tests/                          # 测试目录（100 个文件，5200+ 用例）
docs/                           # 项目文档（harness 规范 + FTS_manual.md + plans/ + design/ + acceptance/）
deploy/k8s/                     # K8s 监控部署（8 个文件）
memory/                         # 运行时持久化（evolution/meta_loop/portfolio/tracking/knowledge/factors）
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
| `DataSource` | `DUCKDB_CACHE` / `TDX_LOCAL` / `TQ_PYTHON` / `AKSHARE` / `SYNTHETIC` / `WIND` / `IFIND` / `TQSDK` / `TQSDK_TICK` / `TDX_LOCAL` | 数据源标识（10 个，TQ_LOCAL 7721 已废弃由 TDX_LOCAL 17709 统一承载） |
| `FusionStrategy` | `MEDIAN` / `MEAN` / `WEIGHTED` / `HIERARCHICAL` / `TRIMMED_MEAN` | OHLCVFusion 融合策略 |

#### `fts.core.atomic`

| 函数 | 签名 | 说明 |
|------|------|------|
| `atomic_write` | `(path, data, *, make_dir=True, encoding="utf-8")` | 原子写入 JSON：临时文件 `.tmp` + `os.replace` 原子替换 |
| `atomic_read` | `(path, *, default=None, encoding="utf-8")` | 安全读取 JSON（不存在/不合法返回 default） |
| `atomic_write_state` | `(path, state, *, backup_count=3)` | 原子写状态 + 备份轮转（`.bak.0`, `.bak.1`, ...） |

#### `fts.core.contracts`

Re-export `fts.factor_engine.contracts` 的全部契约（版本号、FactorProgram、评估/Verifier/预算、L1/L3 契约、`MultiSourceDisagreement`），本地新定义数据层契约：`OHLCVBase`（公共字段）、`FusionMeta`、`StockOHLCV`（含复权因子 adjust_factor）、`FusedOHLCV`（融合器输出）、`FuturesOHLCV`（8 必填 + 8 可选 + 融合元数据）、`FuturesDataLineage`、`FusionReport`。

---

### 4.2 配置系统 `fts.config`

**文件**: `fts/config/`

**职责**: 全局配置管理，优先级：环境变量（`FTS_*`）> YAML 配置文件 > Python 默认值。

| 类/函数 | 说明 |
|---------|------|
| `FTSConfig` | `@dataclass` 全局配置。关键字段见 §8 |
| `get_config()` | 延迟初始化单例 |
| `load_config(config_path=None)` | 加载配置（YAML 解析 + `_apply_dict` + `_apply_env_overrides` 按类型强转） |
| `get_elite_dir(market)` | 按市场返回 elite 目录（futures → `futures_elite_dir`，其他 → `elite_dir`） |
| `is_weight_recompute_day(cfg, today)` | GAP-072 权重重算日判定（daily 恒重算 / weekly 仅周五） |
| `load_industry_map` / `load_cap_map` | 股票行业/市值中性化映射加载（GAP-086 cap_map 默认动态化） |

**`factor_quality_card_config.py`**：10 维权重（`DimensionWeights`，Σ=7.1）+ 分级阈值（`GradeThresholds` A≥40/B≥30）+ 5 预设（默认/期货/保守/激进/宽松）+ `create_config("weights.ic_score" 点号覆盖)`。

---

### 4.3 因子引擎 `fts.factor_engine`

**文件**: `fts/factor_engine/`（约 100 文件，核心模块）

**职责**: FTS 的核心，实现 L0/L1/L2/L3 四层循环 + 因子全生命周期管理。

#### 4.3.1 契约层 `contracts.py`

定义所有核心 TypedDict 契约（HARNESS §契约优先的核心）。关键内容：

- **版本**: `EVOLUTION_VERSION` 动态读取自 `fts.__version__`；`STATE_SCHEMA_VERSION = "1"`
- **市场/家族/风格**: `FactorMarket`（futures/stock/etf/bond/multi）、`FactorFamily`（**18 大类**：trend/mean_reversion/carry/seasonality/cross_section/qlib/gtja/wq101/fundamental/technical/microstructure/macro/behavioral/liquidity/volatility/volume/multi_factor/other）、`FactorStyle`（**15 类**：momentum/value/low_vol/defensive/open_interest/intraday 等）
- **因子类型**: `FactorKind` = `OPERATOR`（DSL 表达式）/ `CODE`（Python 沙箱）/ `HYBRID`
- **核心契约**: `FactorProgram`、`FactorSignature`、`EconomicLogic`（四维评分）、`BacktestMetrics`、`EconomicScore`、`MultipleTestResult`、`FactorEvaluation`、`ExperienceTrace`、`EvolutionState`、`VerifierConfig`/`Result`、`BudgetConfig`
- **L1 契约**: `SeedCandidate`、`L1MetaLoopState`、`FactorPoolEntry`、`FactorPool`、`L1VerifierConfig`/`Result`、`L1BudgetConfig`、`L1BootstrappingSource`
- **L3 契约**: `PortfolioSignal`、`PortfolioCombo`、`AgentOptimizationProposal`、`L3VerifierConfig`、`L3MetaLoopState`、`FactorCorrelation`、`StickyConfig`、`AdaptiveWeightConfig`、`DriftAlertConfig`
- **多源交叉验证**: `MultiSourceDisagreement`
- **默认配置**: `DEFAULT_VERIFIER_CONFIG`、`FUTURES_VERIFIER_CONFIG`（放宽 sharpe=1.0/icir=0.3/t=2.0）、`DEFAULT_BUDGET_CONFIG`、`DEFAULT_L1_*`、`DEFAULT_L3_*`、`DEFAULT_STICKY_CONFIG` 等
- **工具函数**: `normalize_factor_program` / `normalize_factor_signature` / `detect_factor_market`（按品种前缀推断市场）

#### 4.3.2 因子程序 `factor_program.py`

图灵完备因子代码 + 安全沙箱编译执行，按 `kind` 分派（CODE 走沙箱 exec，OPERATOR 走 DSL runtime）。

| 关键类/函数 | 说明 |
|------------|------|
| `FactorExecutor` | `compile()` 受限 globals → `execute(data, params)`：算子快速路径（直接解释 DSL AST）或沙箱执行；`_ArrayDataWrapper` 列访问转 ndarray；nan_to_num + clip(-10,10) 数值稳定；GAP-070 信号缓存 |
| `create_factor_program` | 创建 FactorProgram 实例（narrative 非空强校验） |
| `generate_factor_id` | `fct_<8hex>`，基于 name + code + 随机熵 SHA1 哈希 |
| `validate_factor_code` | AST 静态分析验证（语法/必须定义 `factor_program(data, params)`/黑名单 import/黑名单内置） |
| `fix_factor_code` | 5 类自动修复（未闭合字符串/括号不匹配/补冒号/双运算符/全局括号平衡） |

**沙箱约束**: `ALLOWED_IMPORTS` = {numpy, pandas, scipy, statsmodels, talib, math, statistics}；`FORBIDDEN_MODULES` = {os, sys, subprocess, shutil, pathlib, socket, http, urllib, requests, ctypes, multiprocessing, threading, asyncio, pickle, marshal, importlib}；唯一放行 FTS 模块：`fts.factor_engine.expr_dsl.runtime`；`FORBIDDEN_NAMES` = open/exec/eval/compile/globals/locals/getattr 等 19 个。

#### 4.3.3 L0 Program `program.py`

| 关键类/函数 | 说明 |
|------------|------|
| `ProgramConfig` | market_regime / factor_priority / factor_avoid / agent_llm / token 预算 / 风险约束 / 熔断确认清单 |
| `parse_program_md(content)` | 非严格正则解析 program.md（人类唯一输入接口） |
| `init_program` / `load_program` | 生成默认模板 / 加载 |
| `get_llm_env_overrides` | 生成 `FDT_LLM_<NAME>_MODEL` 环境变量 |

#### 4.3.4 种子池 `seed_pool.py` / `seed_loader.py` / `seed_data_futures_full.py` / `seed_data/`

| 关键类/函数 | 说明 |
|------------|------|
| `SeedPool` | `load_all_seeds`（YAML 优先，回退硬编码）、`get_seed_counts`（动态统计）、`inject_from_l1`、`compute_correlations` |
| `get_default_seed_pool(market)` | 获取默认种子池（futures/stock） |
| `compute_seed_correlations` / `compute_cross_section_correlations` | 相关性计算 |
| `load_all_yaml_seeds` / `load_factors_from_dir` / `load_factors_from_yaml` / `verify_yaml_integrity` | YAML 种子加载（内联 20+ 个 WQ alpha 算子实现） |
| `load_futures_seeds_full` | 期货种子完整加载（Python 硬编码 81 个，14 家族） |
| `seed_data/loader.py` | 股票外部种子统一加载：`load_wq101_seeds` / `load_qlib158_seeds` / `load_gtja191_seeds` / `load_fundamental_seeds` / `load_jq_seeds` |

#### 4.3.5 演化核心 `macro_evolution.py` / `micro_evolution.py` / `evaluation_chain.py` / `verifier.py`

| 关键类/函数 | 说明 |
|------------|------|
| `MacroEvolver` | `evolve(factor, generation, trace_id, parent_failure_ctx, success_pattern) → FactorProgram`：读取经验链 + 父失败归因定向修复段 + 成功模式段 → LLM 生成 → JSON 解析（支持 markdown 包裹） |
| `evolve_micro(factor, data, forward_returns, n_trials=100, use_staged)` | optuna TPE 贝叶斯参数优化；GAP-I205 两阶段漏斗（粗筛 20 trials <0.02 淘汰 → 精筛自适应） |
| `EvaluationChain` | `evaluate(...)`：L1 回测（IC/ICIR/Sharpe/单调性/OOS≥30%/换手/衰减）→ L2 经济逻辑（四维≥3/4）→ L3 多重检验（Bonferroni+FDR）+ 强制 WalkForward + GAP-F15 极值扰动（剔除上下 1% 重算 IC） |
| `cross_section_evaluate_backtest` | 横截面评估：行业/市值/Barra 中性化 → 逐期截面 Spearman IC → 方向自动翻转 → Q1-Q5/symbol_ic/symbol_holdout |
| `FactorVerifier` | 锁定 Verifier：`__init__` 后不可修改，`check(evaluation) → VerifierResult`（含 checked_against 快照）；`get_global_verifier()` 全局单例 |

**Verifier 三级阈值**（DEFAULT / FUTURES 放宽）：min_ic=0.03、min_icir=0.5/0.3、min_sharpe=1.5/1.0、max_drawdown=0.50、min_economic_score=3、min_t_stat=3.0/2.0、max_fdr=0.05、min_oos_ratio=0.30、max_turnover_monthly=5.0。

#### 4.3.6 L2 Evolution Loop `evolution_loop.py`

| 关键类 | 说明 |
|-------|------|
| `EvolutionLoop` | L2 主循环。`run(max_generation) → EvolutionRunResult`（run_id/trace_id/generations/promoted/tokens/status/circuit_breaker_reason/early_stopped） |
| `UCT_EXPLORATION_C = 1.0` | UCT 探索常数 |

**`run()` 流程**（完整）:
1. 初始化：generate_trace_id("l2")、清空质检信号缓存/实验变体
2. 状态：EvolutionStateManager.load_or_init → mark_running（SSOT 为 state.duckdb）
3. DataQualityMonitor.validate_market_data → critical 直接熔断
4. Step 0 种子加载与预检：seed_pool 加载 + `_merge_l1_candidates`（factor_pool.json pending 门控）+ `_run_seed_correlation_check`（阈值 0.95 仅标记不删除）
5. Step 1 种子评估晋升：三级评估 → 轻量走航 → vwap_approx 因子 IC≥0.08 门 → Verifier → 质量评分卡 → 端到端回测 → 数据质量 → 6 项审计 → 消融/因果/鲁棒性/SHAP → `_promote_to_elite`（种子直接进正式组合）
6. 演化主循环 for generation in 1..max_gen（默认 50）：
   - a. 熔断预检查：token > nightly×2.0 / 连续低 IC≥5（|IC|<0.005）/ 失败率>0.95
   - b. UCT 父因子选择：UCB = avg_reward + C·√(ln(total)/visits)
   - c. 模式分派：`batch`（BatchMiner 批量生成 → 并行粗筛 → IC 截断前 5）或单因子（operator_first/operator/code/hybrid 四模式逐级兜底）
   - d. 运行时校验（拦截广播/长度不匹配/常数信号）→ e. 快速预筛（nunique>10、std>1e-6、快速 IC：futures≥0.01/stock≥0.02）
   - f. `_process_candidate` 准入链：微观演化（optuna）→ 三级评估 → UCT 反馈 → Verifier 判定 → 质量评分卡（A/B 晋升 C 淘汰）→ 端到端回测 → 数据质量 → 6 项审计 → 消融/因果/鲁棒性（futures≥0.7/stock≥0.9）/SHAP → `_promote_to_elite`（晋升门）
   - g. 经验链清理（>100 淘汰最旧 20）→ h. 提前停止（连续 K 代零晋升，默认关）
7. 收尾：`_write_seed_correlation_index`（供 L3 读先验）→ `_run_periodic_factor_review`（elite_tracker 自动退役 + 反馈闭环 + LogicMonitor）→ `_export_experiment_log`

**晋升门（_promote_to_elite，全部通过才落库）**: ① DuckDB 名称去重 → ② 结构簇配额（与既有 elite |corr|≥0.85 成员 ≥15 拒绝；回退 max_per_family=15，other/unknown 豁免）→ ③ L2 准入去冗余（|corr|≥0.9 时 Gram-Schmidt 正交基底，残差<0.3 且保留比>0.3；回退单参照 OLS）→ ④ HighICScreener 强制门（grade C 拒绝）→ ⑤ 多重检验强制门（L3 passed）→ ⑥ 影子池标记（5 交易日观察）→ ⑦ **DuckDB 先写成功后再写 JSON**（DuckDB 失败则 JSON 也不写，GAP-032 防孤儿）→ ⑧ seed_lineage 溯源 + elite_tracker + 一致性日志。

#### 4.3.7 市场专用引擎 `evolution_futures.py` / `evolution_stock.py`（F.2 引擎分叉）

> 🗑️ **工程残余**：`evolution_stock.py` 属股票管线，已随 plans/32 剥离迁至 `d:\Programs\fts-stock`（本文件为主系统剥离后未清理副本）。`evolution_futures.py` 为期货主系统实际使用引擎。

与 `evolution_loop.py` 几乎同构，差异点：

| 差异点 | evolution_futures | evolution_stock |
|---|---|---|
| 市场 | `market="futures"` | `market="stock"` |
| Verifier | `FUTURES_VERIFIER_CONFIG`（放宽） | `get_global_verifier()`（默认） |
| 演化模式 | 保持配置 | hybrid 改写为 operator_first |
| 中性化 | 期货板块映射（FUTURES_SECTOR_MAP） | 股票行业/市值映射（load_industry_map/load_cap_map） |
| long_only | False（多空） | True（仅多） |
| 预筛 IC | 0.01 | 0.02 |

#### 4.3.8 L1 Meta Loop `meta_loop.py`

| 关键类 | 说明 |
|-------|------|
| `MetaLoop` | `run(max_bootstraps) → MetaRunResult`：`_perceive_market`（agentic 市场感知）→ `_analyze_debate`（辩论分析）→ `_run_bootstrap`（三源候选：提取器优先 → LLM 补足 → 内置模板兜底）→ `_verify_and_inject`（L1 Verifier + 注入 factor_pool.json + l1_injected/） |
| `L1Verifier` | 宽松判定：economic_logic >= 2/4 + is_executable + not_duplicate + narrative >= 20 字 |
| `MetaStateManager` / `FactorPoolManager` | L1 状态（state.duckdb SSOT）/ 因子池管理 |
| `DebateQualityAnalyzer` | 从 debate_journal.json 识别薄弱维度 |
| `BootstrappingChain` | 候选生成 + 编译验证 + `fix_factor_code` 自动修复 + 去重标记 |

**L1 熔断**: token > 日预算×2、失败率 > 95%、连续 5 次低质量（仅硬失败计）。

#### 4.3.9 L3 Portfolio Loop `portfolio_loop.py`

| 关键类/函数 | 说明 |
|------------|------|
| `PortfolioLoop` | `run(...) → PortfolioRunResult` |
| `L3Verifier` | 六维判定：sharpe∈[2.0, 3.5]、max_correlation≤0.5、turnover≤0.5、decay≤0.30、retained≥3 |
| `synthesize_signals(factors, mode)` | 信号合成（**9 种模式**，见下） |
| `regime_adaptive_weight_adjustment` | family/style/both 三维度调整 + 概率混合（28-T3）+ high_vol 衰减因子额外 -20% |
| `_compute_exposure_scale` | 28-T4 置信度仓位缩放 |
| `orthogonalize_factors` | 4 种模式：分层正交化（≥30 因子）/ 相关性矩阵剔除 / 代码哈希去重 / 无依据全标记 |
| `decay_test(signals, max_decay_rate=0.30)` | 衰减检验 |
| `build_combo` | 组合构建：粘性约束（±30%/新因子 10% 封顶）+ 换手惩罚 + 归一化 + 置信度缩放 + 实测化指标 + net 成本 + 夏普虚高验证（>3.5 警戒 + Dirichlet 随机化 1000 次）+ GAP-063 质检三标准 |
| `DriftMonitor` | 漂移监控（Jaccard 重合率 + 权重 L1 变化 + 告警 + 重平衡建议） |
| `load_elite_factors` | DuckDB 优先（market+active+is_elite）→ JSON 兜底；质量门槛（IC≥0.03/Sharpe≥1.5）+ 影子池过滤 + 基础名去重 |
| `inject_to_fdt` | 输出 current_combo.json + factor_weights.json + agent_proposals/ |

**9 种信号合成模式**：
1. `equal_weight`（1/N）
2. `sharpe_weight`（w ∝ 截断前原始 Sharpe）
3. `ic_weight`（GAP-064：IC 协方差加权 (Σ+λI)⁻¹μ，Ledoit-Wolf）
4. `elastic_net`（默认：逐日截面 ElasticNet → 系数归一化 → 6.5 风险调整 → 6.6 滚动 OOS → 6.7 跨市场 IC）
5. `ml_ensemble`（LightGBM 特征重要性归一化）
6. `adaptive`（sharpe_weight 为基 + Regime 调整）
7. `optimizer`（PortfolioOptimizer 约束优化：risk_parity/mvo）
8. `bl`（C3 Black-Litterman：先验=风险平价，观点=IC 自动构建）
9. 未实现模式 → 回退等权

前置截断：`IC_CAP=0.15`、`SHARPE_CAP=2.0`（原始值存 `_ic_raw`/`_sharpe_raw` 供审计）。`ACTIVE_FACTOR_CAP=20`（活跃因子上限）。

#### 4.3.10 批量挖掘 `batch_mining.py` + 执行后端 `executor_backend.py`

| 关键类 | 说明 |
|-------|------|
| `BatchMiningConfig` | batch_size=20、max_candidates=5、max_workers=4、executor_backend="thread" |
| `BatchMiner` | `generate_batch`（方法轮换 + seed 递增：macro/gp/deep/transformer/operator）→ `filter_batch`（并行粗筛）→ 预筛 IC 降序截断前 5 → 逐个走准入链 |
| `ExecutorBackend` (ABC) | `map()`（保序）+ `shutdown()` |
| `ThreadBackend` / `ProcessBackend` / `DaskBackend` / `RayBackend` | 四实现；Dask/Ray 缺依赖自动降级 Process |
| `create_executor_backend(backend, max_workers)` | 工厂，未知名称回退 thread |

#### 4.3.11 质量评分卡 `factor_quality_card.py` + 审计 `audit.py` + 高 IC 筛查 `high_ic_screener.py`

**评分卡**: 10 维评分（ic/sharpe/stability/robustness/capacity/tradability/diversity/logic/timeliness/compatibility），各 0-5 分加权求和归一化到 0-50；分级 A ≥ 40、B ≥ 30、C < 30（期货 get_futures_config：A≥38/B≥28，IC/Sharpe 阈值降 30%）。Sharpe>20 过拟合惩罚逐步减分。

| 关键类 | 说明 |
|-------|------|
| `FactorQualityCard` | `evaluate(...) → FactorQualityScore` |
| `compute_total_score` / `determine_grade` | 加权总分 / 等级判定 |

**6 项强制审计**（`FactorAuditor`，渐进式 skipped 不阻塞）:

| # | 审计项 | 实现 |
|---|--------|------|
| 1 | 因果检验 | CausalValidator（6 个预定义自然事件） |
| 2 | 样本外验证 | WalkForwardOptimizer（OOS 一致性复用走航结果） |
| 3 | 跨品种验证 | A+C 双机制 OR：符号比例≥80% / 平均 IC+符号比例 / 二项检验 |
| 4 | 标的外验证 | SymbolHoldout（GAP-075：留出集 IC 保持率） |
| 5 | 压力测试 | StressTester（5 个极端场景，回撤 ≤40%） |
| 6 | 多重检验 + 窥探 | Bonferroni/FDR + 无未来函数检查 |

**`FailureClassifier`**: 10 种失败模式 → 改善建议（IC/ICIR/sharpe/monotonic 等 22 类关键词聚类）。

**`HighICScreener`**: 16 项检查×6 大模块归一化 100 分 + 5 项一票否决（V1 外样本 IC 衰减>30% / V2 极值扰动 IC 降幅>25% / V3 存量相关>0.7 / V4 扣成本后超额≤0 / V5 经济逻辑最低分<2）+ A/B/C 分级，缺失数据 skipped 不误杀。

#### 4.3.12 因子巡检与 Alpha 人审 `factor_inspector.py`

| 关键类/函数 | 说明 |
|------------|------|
| `FactorInspector.inspect_and_downgrade(threshold=-0.2, market=None, commit=True)` | 批量血缘审计 → 退化识别（Sharpe 变化率）→ 降级（is_elite=False, status='degraded'） |
| `FactorReviewWorkflow` | GAP-I102 人审工作流：`list_pending` / `approve` / `reject` / `auto_review`（幂等 UPSERT factor_reviews 表 + 驳回写经验链） |
| `AutoReviewPolicy.classify(ic, sharpe)` | 三态机审（min_ic=0.02/max_ic=0.8/min_sharpe=0.5/max_sharpe=30），越界/NaN → 转人审 |

#### 4.3.13 因子数据库 `factor_db/`

**13 张表**（DuckDB `data/factor_catalog_{stock,futures}.duckdb`）:

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `factor_catalog` | 因子主表（SSOT） | factor_id(PK)/name/code/code_hash/params/signature/economic_logic/source/parent_id/generation/trace_id/sharpe/ic/icir/max_drawdown/turnover_monthly/decay_6m/status/market/family/is_elite/style_tags 等 |
| `factor_evaluations` | 三级评估历史 | level_1_* / level_2_* / level_3_* / overall_passed / failure_reasons |
| `factor_versions` | 代码版本历史 | version_id/factor_id/code/code_hash/version_number |
| `factor_correlations` | 因子相关性矩阵 | factor_id_a/b/pearson_corr/spearman_corr |
| `factor_quality_scores` | 质量评分 | total_score/grade/dimension_scores + 10 维快捷列 |
| `factor_status_history` | 生命周期状态变迁 | from_status/to_status/reason |
| `factor_audit_reports` | 审计报告 | passed/overall_score/results_json |
| `feedback_events` / `attribution_reports` / `feedback_processing_results` / `feedback_reports` | 反馈闭环（C.3） | — |
| `seed_lineage` | L0→L2 种子溯源 | seed_name/family/market/evolved_factor_id |
| `factor_reviews` | Alpha 人审（E.1） | factor_id(PK)/decision/comment/reviewer/reviewed_at |

**关键类**: `FactorRepository`（CRUD/排行榜/多样性/`retire_factor`/`rollback_to_version`/`resolve_seed_lineage`；update 时 DROP 索引→UPDATE→重建规避 DuckDB 1.1.x ART 索引 bug）、`FactorLineage`（血缘/评估趋势/退化检测/批量审计）、`FactorQualityScoreRepository`、`FactorStatusRepository`、`FactorAuditReportRepository`、`migrate_factors`（JSON→DuckDB 迁移）。

#### 4.3.14 FTS-Expr DSL `expr_dsl/`

**职责**: 算子表达式语言，**512 项**（20 字段 + 492 算子）L0-L5 分层，双注册表一致性校验。

| 关键类/函数 | 签名 | 说明 |
|------------|------|------|
| `ExprNode` | `@dataclass` | AST 节点（op/args/kind） |
| `parse_expression(expr_str)` | `str → ExprNode` | 递归下降解析 |
| `validate_expr(node, registry)` | → (errors, max_lookback) | 静态验证（未知算子/字段/参数边界/最大 lookback PIT） |
| `compute_max_lookback(node)` | → int | PIT 最大 lookback 分析 |
| `collect_fields(node)` | → set[str] | 收集所需数据字段 |
| `evaluate(node, data, registry)` | → Series/float | 解释执行（向量化，不经沙箱 exec） |
| `compile_expr_to_code(expr_str)` | → str | 编译为确定性沙箱安全 FactorProgram 代码 |
| `eval_fts_expr(expression, data, params)` | → np.ndarray | 沙箱运行时入口（沙箱唯一放行 FTS 模块） |
| `create_operator_factor(expression, ...)` | → FactorProgram | 因子工厂（kind=OPERATOR） |
| `build_registry()` | → dict | 构建算子注册表 |
| `verify_registry_consistency()` | → bool | expr_dsl 与 feature_ops 双注册表同输入比对（容差 1e-6） |
| `analyze_seed_expression` / `estimate_lookback_static` | → SeedExprAnalysis | 老模板种子 PIT 静态审计（GAP-S09） |

**算子分层**: L0 基础字段（10）+ L0b A股字段（10）+ L1 时序（21+C8 12+C9 14）/ L2 横截面（5+C8 4+C9 5）/ L3 逻辑（6+C8 3+C9 4）/ L4 组合（14+6 高阶）/ L5 领域（4+C8 3+C9 7）+ C8 扩容（22）+ C9 扩容（30）+ **D10-D17 八大族 380 算子**（波动/风险 55、技术指标 60、动量/趋势 55、截面/排名 45、条件/事件 40、组合/跨序列 50、量价/流动性 40、市场结构/分布 35）。

#### 4.3.15 GP 遗传规划 + 算子演化 + 符号回归

| 关键类 | 说明 |
|-------|------|
| `GPEvolver` | 锦标赛选择/双亲·三父代交叉/子树变异/精英保留；适应度 0.6|IC|+0.4·max(Sharpe,0) 或多目标（减换手/衰减惩罚）；train_mask 前 60% 防泄露；`tree_to_factor_program` 渲染为静态内联算子代码（`_GP_FACTOR_CODE_TEMPLATE` 内嵌 22 算子） |
| `OperatorEvolutionEngine` | DSL 算子空间进化：子树交叉/参数扰动/字段替换变异；纯字段表达式（lookback=0）罚分；产出 kind=OPERATOR 因子 |
| `SymbolicRegressionSearcher` | 确定性 beam-search（beam_width=10、max_depth=4），结果并入 GP 的 Pareto 前沿 |

#### 4.3.16 回测流水线 `backtest_pipeline.py`

| 关键类 | 说明 |
|-------|------|
| `BacktestPipeline` | 4 阶段：DataLoad（≥60 行/日期过滤）→ FactorCompute（宏观字段注入 + 沙箱执行 + 前向收益 + 滚动 Spearman IC）→ Performance（滚动 z-score 信号 + 滞后一期收益 - 成本，含展期成本/可交易掩码（涨跌停/停牌）/容量约束）→ Report |
| `PerformanceMetrics` | 18 项：total/annual_return、sharpe、max_drawdown、calmar、win_rate、volatility、ic_mean/std/ir、turnover、exposure、**payoff_ratio 盈亏比、profit_factor 盈亏因子、max_consecutive_losses** |
| `FREQUENCY_ANNUAL_FACTOR` | 分钟频率年化（daily 252 / 60m 1638 / 30m 3276 / 15m 6552 / 5m 19656 / 1m 98280） |
| `BacktestPipelineBuilder` | Builder 模式链式配置 |

#### 4.3.17 成本与风控分析模块

| 模块 | 关键类/函数 | 说明 |
|------|-------------|------|
| `cost_model.py` | `TransactionCostModel.adjust(...)` | 滑点/手续费/冲击/展期（GAP-F11 实际价差优先）/融资成本，净夏普 = 毛夏普 − 年化成本/0.15 |
| `cost_simulator.py` | `CostSimulator.simulate(...)` | 品种差异化费率 |
| `cost_sensitivity.py` | `run_cost_sensitivity` | 1/2/4/8 倍滑点压力 + 盈亏平衡倍数（GAP-061） |
| `factor_optimizer.py` | `FactorOptimizer` | 4 层分层（T1 并行计算 ≥30 因子 / T2 两阶段正交化 / T3 增量缓存 / T4 采样式相关估计）；`FactorSignalCache` Parquet+checksum |
| `signal_generator.py` | `SignalGenerator` | 时序信号（滚动 z-score→tanh）/ 横截面信号（top/bottom 20%） |
| `factor_screener.py` | `FactorScreener.screen(...)` | 等级/总分/状态/风格过滤 |
| `portfolio_constructor.py` | `PortfolioConstructor.construct(...)` | equal / sharpe / adaptive 三权重 |
| `capital_allocator.py` | `CapitalAllocator` | fixed / vol_target / risk_parity / kelly + 保证金占用约束（GAP-F09） |
| `risk_attributor.py` | `RiskAttributor.attribute(...)` | 协方差分解 + 暴露分析 + VaR/ES |
| `report_generator.py` | `ReportGenerator.generate(...)` | 多节 Markdown 报告 |
| `adaptive_weight.py` | `AdaptiveWeightManager` / `RegimeSmoother` | Regime 倍率调整 + 不对称平滑（de-risk 0.8 / re-risk 0.1） |
| `weight_learning.py` | `risk_adjust_weights` / `rolling_oos_validate` / `ic_covariance_weights` | Ledoit-Wolf 风险调整 / 滚动 OOS / IC 协方差加权 |
| `black_litterman.py` / `portfolio_optimizer.py` | BL 观点融合 / 约束优化（risk_parity/mvo） | L3 模式 7/8 |

#### 4.3.18 逻辑审查五层

| 模块 | 关键类 | 说明 |
|------|--------|------|
| `ablation.py` | `AblationExperiment` | 5 种消融模式（volume_zero/vwap_replace/vwap_to_settle/time_shuffle/feature_permute） |
| `shap_analyzer.py` | `ShapAnalyzer` | SHAP 局部可解释性（n_extreme=25/n_background=50/nsamples=50，GAP-080 降频 4x） |
| `robustness.py` | `RobustnessTester` | 对抗样本/缺失值/分布外 |
| `causal_validator.py` | `CausalValidator` | 6 个预定义自然事件因果验证 |
| `walk_forward.py` | `WalkForwardOptimizer` | 滚动多窗口样本外（window_years=3/step_months=6/n_windows=4） |
| `portfolio_walk_forward.py` | `PortfolioWalkForward` | 组合级走航（≥120 行） |
| `symbol_holdout.py` | `run_symbol_holdout` | 标的留出验证（GAP-075） |
| `stress_test.py` | `StressTester` | 5 个内置极端场景，最大回撤 ≤ 40% 通过 |

#### 4.3.19 Market Regime 体系

| 关键类 | 说明 |
|-------|------|
| `RegimeAwareSelector` | 四级检测链（见 §2.3），`detect` / `profile_factor` / `select_factors` / `regime_report` |
| `HMMRegimeDetector` | 单周期 GaussianHMM（BIC 状态数 + 扩展特征 + StateMapStabilizer），`maybe_refit` 每 20 次 |
| `MultiHorizonHMMDetector` | 多周期 [63,126,252] 加权投票（默认主检测器），输出全制度概率分布 |
| `MSMRegimeDetector` | statsmodels MarkovRegression（P3.1 默认关） |
| `SectorRegimeSelector` | 产业链级制度检测（FUTURES_SECTOR_MAP 17 链） |
| `StockRegimeSelector` | A 股行业轮动 + 风格轮动（GAP-S03） |
| `RegimeTransitionWarner` | 迁移预警（归一化熵/转移矩阵/KL 散度三信号 → yellow/orange/red） |
| `AdaptiveRegimeConfig` | 5 阈值自适应，每 20 日网格重优化 |
| `RegimeConfidenceCalibrator` | 置信度熵标定（`confidence × (1 − 0.5×H_norm)`，下限 0.3） |
| `RegimeMultiplierEstimator` | 数据驱动倍率（GAP-L308，min_samples=10 回退 1.0） |
| `validate_regime_predictive_power` | Kruskal-Wallis 制度样本外有效性 |

#### 4.3.20 因子分析模块

| 模块 | 关键类/函数 | 说明 |
|------|-------------|------|
| `factor_returns.py` | `FactorReturnsBuilder` | top/bottom 20% 多空 → T×N 因子收益矩阵 |
| `horizon_analysis.py` | `compute_multi_horizon_ic` | 1/5/10/20 日 IC/ICIR/胜率 + 衰减曲线 + 最佳持有期（GAP-060） |
| `multi_frequency.py` | `compute_multi_frequency_signal` | 日频+多频分钟信号融合（blend/resolve_conflict 三规则） |
| `microstructure_factors.py` | `compute_microstructure_factors` | OFI/OBI/大单占比（tick 级） |
| `microstructure_generator.py` | `MicrostructureFactorGenerator` | tick → 日频聚合 → 确定性 FactorProgram（每品种 4 因子） |
| `sector_linkage.py` | `compute_sector_linkage` | 板块内/跨板块相关 + 因子截面分散度 |
| `position_rank_crowding.py` | `compute_crowding` | 前 N 集中度 + 多空比 + 净占比 → 拥挤度信号 |
| `style_classifier.py` | `FactorStyleClassifier` | 名称→代码→签名三级关键词推断风格标签 |
| `neutralization.py` | `industry_neutralize` / `size_neutralize` | 行业组内去均值 + log 市值 OLS 残差（D.2） |
| `barra/barra_style.py` | `BarraStyleEngine` | Barra 10 大风格暴露计算 |
| `barra/barra_neutralizer.py` | `barra_neutralize_matrix` | 风格+行业双重中性化（逐日截面 OLS 残差） |
| `orthogonal_basis.py` | `OrthogonalBasisManager` | Gram-Schmidt 多因子正交基底（max_size=10） |
| `factor_clustering.py` | `FactorClusteringEngine` / `PCASignalCompressor` | P1 层次聚类（threshold=0.7）+ P2 PCA（95% 方差） |
| `standardizer.py` | `Standardizer` / `standardize()` | 6 种标准化（zscore/rank/quantile/minmax/winsorize_zscore/none） |

#### 4.3.21 其他引擎模块

| 模块 | 关键类/函数 | 说明 |
|------|-------------|------|
| `experience_chain.py` | `ExperienceChain` / `FailurePatternAnalyzer` / `ParentFailureContext` | 经验链持久化（满 100 淘汰最旧 20）+ 失败模式聚类 + 父失败归因 |
| `success_pattern.py` | `analyze_success_patterns` | 成功模式统计（窗口/算子分布，供 LLM 参考） |
| `experiment_log.py` | `ExperimentLogWriter.export()` | 结构化实验日志（data/experiments-{run_id}.json） |
| `state.py` | `EvolutionStateManager` / `generate_trace_id` / `generate_run_id` / `generate_session_id` | 状态管理（state.duckdb SSOT）+ 全链路 ID |
| `monitor.py` | `check_loop` / `check_all` / `LoopStatus` / `AllStatus` | 底层循环状态检查 |
| `signal_contract.py` | `FactorSignal` / `SignalValidator` | 信号契约（方向 long/short/flat） |
| `signal_cache.py` | `SignalCache` | 进程内信号缓存（LRU 16 条，因子+数据指纹命中） |
| `feedback_loop.py` | `FeedbackTrigger` / `AttributionAnalyzer` / `EvolutionDirectionAdjuster` / `FeedbackLoop` / `LiveFeedbackImporter` | 反馈闭环 + 实盘 IC 导入 |
| `recalibration.py` | `RecalibrationQueue` / `recalibrate_factor` | 因子再校准队列（LLM 补参重新评估） |
| `extractors/` | `BaseExtractor` / `BaseExtractorPipeline` / `FuturesExtractorPipeline` / `StockExtractorPipeline` / `AnnouncementNewsExtractor` / `MacroEventExtractor` | 因子提取管线（天软 YAML/研报/arXiv/公告/宏观，状态持久化 state.duckdb） |
| `pareto.py` | `compute_pareto_front` | 4 目标帕累托前沿（IC/Sharpe/回撤/换手） |
| `risk_model.py` | `RiskModelEstimator` | Ledoit-Wolf 收缩协方差 + 正定化 |

---

### 4.4 数据提供者层 `fts.data*`

#### `FTSDataProvider`（data.py）

统一数据提供者，组合 MCP（A股/ETF）、基本面、期货、期货基本面四个子提供者。

| 方法 | 签名 | 说明 |
|------|------|------|
| `get_ohlcv` | `(symbol, *, days=500, adjust="qfq", trace_id="", fundamental=False)` | 股票/ETF OHLCV（TDX 17709 → 腾讯 API → 合成降级） |
| `get_etf_ohlcv` | `(symbol, days=500, ...)` | ETF OHLCV |
| `get_csi300_panel` | `(days=500, max_stocks=50, min_coverage_ratio=0.8)` | 沪深 300 面板（覆盖率阈值对齐替代硬交集，GAP-079 修复） |
| `get_futures_ohlcv` | `(symbol, *, days=500, trace_id)` | 期货连续合约 OHLCV（含 hold/settle/换月复权） |
| `get_futures_panel` | `(symbols=None, days=500, trace_id)` | 期货面板（默认动态核心池，多数对齐） |
| `enrich_with_fundamental` / `enrich_futures_fundamental` | — | 基本面/期货基本面字段注入 |
| `search_symbol` | `(query, limit=10)` | 代码搜索 |
| `get_data_provider()` | — | 全局单例（惰性初始化） |

#### `FuturesDataProvider`（data_futures.py）

| 组件 | 说明 |
|------|------|
| `FuturesDataProvider` | `get_ohlcv`（5 级降级 + 换月复权 + 夜盘跳空）/ `get_minute_ohlcv` / `get_tick_data` / `get_futures_panel` |
| `retry_on_conflict` | DuckDB 写冲突指数退避重试装饰器 |
| `AsyncWriteQueue` | 异步写入串行化队列 |
| `DuckDBConnection` / `DuckDBWriter` / `DuckDBReader` | 读写分离并发模型（单写者 + 读池，GAP-056/design E.1） |
| `get_dominant_contracts(symbols)` | 主力合约查询（contract_kline ROW_NUMBER 按 date/volume） |
| `get_realtime_prices(symbols)` | 实时价（TDX_LOCAL 17709 快照 → AKShare 降级） |
| `get_dynamic_core_subset()` | 数据驱动动态池（state.duckdb SSOT → JSON 缓存 → 静态池三级降级） |

品种清单：`FUTURES_SUBSET`（82）、`FUTURES_CORE_SUBSET`（25）、`FUTURES_HOLDOUT`（15 盲测，GAP-055 按产业链分层）、`FUTURES_SECTOR_MAP`（**17 产业链**，GAP-S05 拆分）、`FUTURES_STRATIFIED_SUBSET`（21 分层训练集）。

**VWAP 逻辑**: 有成交额 `amount/volume`；AKShare 有 settle `(H+L+C+settle)/4`；DuckDB/TDX 无 settle `(H+L+C)/3`。

#### `MCPDataProvider`（data_mcp.py）

A 股/ETF 双源：TDX_LOCAL 17709 股票端点（`fetch_stock_ohlcv`，qfq/hfq 复权）+ 腾讯 HTTP API（qt.gtimg.cn / web.ifzq.gtimg.cn）。探活机制 `_probe_tq_once`（冷却 30s + 2 次瞬时重试，GAP-078）。`CSI300_SUBSET`（77 只）、`ETF_SUBSET`（18 只）。

#### `FundamentalProvider`（data_fundamental.py）

字段分 6 组：VALUATION / SIZE / TRADING / QUALITY / GROWTH / MACRO（`FUNDAMENTAL_FIELDS` 全量）。`enrich_ohlcv` 优先 MCPBridge 缓存 → 合成降级（seed=42）；`_fetch_macro` 走 EastmoneyMacroSource（cpi 真实）+ akshare macro_china_pmi（GAP-087 真实源接入）。GAP-081/082 开关（`FTS_ASHARE_SPECIAL_ENABLED` / `FTS_STOCK_FUNDAMENTAL_ENABLED`，默认关）。

#### `MCPBridge`（data_mcp_bridge.py）

东方财富 mx API 缓存桥接（`data/fundamental_cache.json`），`get_fundamental` / `get_batch` / `save_cache`（Agent 预填充，FTS 运行时只读）。

#### `AkshareFuturesFundamentalProvider`（data_futures_fundamental.py）

期货基本面三能力：`get_inventory`（东财 futures_inventory_em → 99 期货兜底）、`get_basis`（futures_spot_price 逐日并行 6 线程）、`get_warehouse_receipt`（CZCE/GFEX 官方并行逐日 + SHFE/DCE/INE 东财 RPT_FUTU_STOCKDATA，GAP-091）。

---

### 4.5 多源数据适配器 `fts.data_sources`

**文件**: `fts/data_sources/`（20 个文件）

**职责**: 期货多源数据适配，统一输出 17+1 列 kline_cache schema（日线）/ 11 列 minute_cache schema（分钟）/ 32 列 tick_cache schema（tick）。

#### 关键类

| 类 | 说明 |
|----|------|
| `BaseFuturesSource` | 抽象基类：`fetch_ohlcv()` / `fetch_quote()` / `is_available()` + `fetch_ohlcv_or_none` / `validate_ohlcv_row`（7 必填字段） |
| `SourceUnavailable` | 数据源不可用异常（`source`, `reason`），必须上抛供熔断 |
| `FuturesDataAggregator` | 多源调度器。`get_ohlcv` / `get_minute_ohlcv` / `get_ticks` / `get_source_status` / `cross_check` / `_derive_pre_settle`（GAP-083 零依赖派生） |
| `OHLCVFusion` | `fuse_row` / `fuse_dataframe`；N=1 透传 |
| `TdxLocalSource` | 通达信本地 17709 统一源（JSON-RPC get_market_data：day 17 列 / 分钟 11 列 + get_market_snapshot + `fetch_stock_ohlcv` A 股复用） |
| `TQSDKSource` | 天勤 TQSDK 分钟/日线（`KQ.m@{EXCHANGE}.{symbol}`，`_SYMBOL_MAP` 约 70 品种） |
| `TQSDKEnhanceSource` | 字段增强层：close_oi→hold、差分→oi_change（默认注册） |
| `TQSDKTickSource` | tick 逐笔（5 档盘口 32 列，免费账号 5000 行上限） |
| `IFindSource` / `IFindSDKSource` / `WindSource` | iFinD MCP 增强 + EDB 宏观 / iFinD 官方 SDK（iFinDPy）/ Wind MCP 增强 |
| `MacroFieldAligner` | 宏观字段滞后对齐（`lag_days=30` 防未来函数）+ `inject_macro_fields_to_panel` 面板级批量 |
| `EastmoneyMacroSource` | 东财 EDB 宏观源（CPI/进出口/中债 1 年/美债 10 年，分年拼接） |
| `AshareSpecialSource` | A 股特有字段（GAP-081）：两融/股东户数/北向/分析师，缓存优先 |
| `StockFundamentalSource` | 股票基本面（GAP-082）：估值日频 + 财务季度（百分比 ÷100），缓存优先 |
| `RollCalendar` + `RollEvent` | 换月日历 + 比率法后复权 adj_factor（GAP-083 前已在用） |
| `compute_overnight_gap` / `inject_overnight_gap` | 夜盘跳空（|gap|>1% 标记，配置开关） |
| `migrate_schema` | DuckDB schema 幂等迁移（返回 counts） |

#### 数据路径

```
K 线主路径:   DUCKDB_CACHE → TDX_LOCAL(17709) → TQ_PYTHON → AKSHARE → SYNTHETIC
分钟路径:     minute_cache → TDX_LOCAL(17709, 5m) → TQSDK
tick 路径:    tick_cache → TQSDK_TICK
实时价路径:   TDX_LOCAL(17709) → AKSHARE
字段增强层:   TQSDKEnhanceSource（hold/oi_change）→ IFindSDKSource/Wind/iFinD（可选）
A 股路径:     TDX_LOCAL 17709 股票端点 → 腾讯 HTTP API → 合成
```

#### 熔断器（`BreakerState`）

- 任一源连续 5 次失败 → `circuit_open = True`（按源独立）
- 6 小时冷却（`COOLDOWN_SECONDS = 21600`）→ 冷却后自动半开探活恢复
- 空数据不算成功也不算失败；增强层失败不熔断主路径

#### 融合策略（`OHLCVFusion`）

- `MEDIAN`（默认）/ `MEAN` / `WEIGHTED`（TDX_LOCAL=2.0, TQ_PYTHON=2.0, WIND=1.5, IFIND=1.0, AKSHARE=0.5, DUCKDB_CACHE=1.0, SYNTHETIC=0.0）/ `HIERARCHICAL` / `TRIMMED_MEAN`（N≥3 最稳健）
- 融合字段：`open/high/low/close/volume/amount/settle`；hold/oi_change/pre_settle/vwap 为事件型字段取首个非空源

#### 交叉验证

≥2 源同日 close 差异 > 0.5%（`cross_check_threshold=0.005`）记录 disagreement 至 `data/data_source_disagreements.jsonl`；主路径尾部自动验证最近 5 个交易日。

#### 缓存表（`migrate.py` DDL）

`kline_cache`（17+1 列含 adj_factor）/ `contract_kline`（15 列）/ `minute_cache`（11 列）/ `tick_cache`（32 列 5 档）/ `edb_cache`（PK indicator+date+source）/ `option_chain_cache`（14 列）/ `stock_kline_cache`（12 列）/ `ashare_special_cache` / `stock_fundamental_cache`（PK symbol+date+field）。

---

### 4.6 存储注册表与状态库 `fts.store`

**文件**: `fts/store/`（plans/29 数据层收敛）

**职责**: 六层存储架构的契约层与运行状态库（SSOT）。

| 类/函数 | 说明 |
|---------|------|
| `StorageBackend` | 枚举：DUCKDB/PARQUET/JSON/YAML/JSONL/NPY/SQLITE/MIXED |
| `StorageDomain` | frozen dataclass：domain/description/backend/path/tables/partition_key/retention/status/migrated_from/migrated_to |
| `StorageRegistry` | YAML 契约加载（env `FTS_STORAGE_LANDSCAPE_PATH` > 默认 `docs/harness/_data/storage_landscape.yaml`）；`get(domain)`（未知域抛 KeyError）/ `validate_contract()`（必填字段/后端枚举/相对路径/legacy 必须 migrated_to/planned 必须 migrated_from）/ `summary()` |
| `StateKVStore` | `upsert(namespace, key, value, run_id)`：写 `state_kv` 当前态 + 追加 `state_history`（seq 自增可回放），返回 history seq；`get` / `get_all` / `snapshot()`（冷启动对账）/ `history()` |
| `get_state_store()` | 进程级懒加载单例连接 |

**域登记（storage_landscape.yaml 13 域）**: config_seeds / market_history / factor_assets（migrated_from=elite_snapshots）/ elite_snapshots（legacy）/ run_state / evolution_state / portfolio_state（legacy）/ signal_parquet / signal_cache（legacy）/ lineage_logs / experiment_logs（legacy）/ sim_portfolio / reports。

---

### 4.7 模拟交易体系 `fts.live_trade`

**文件**: `fts/live_trade/`（D.1 模拟仓 + D.2 进阶优化）

**职责**: 信号→模拟撮合→盯市→归因→反馈闭环的完整模拟核算链路（真实撮合由下游 FDT 负责）。

| 模块 | 关键类/函数 | 说明 |
|------|-------------|------|
| `contracts.py` | `SimPosition` / `SimAccount` / `SimDailyRecord` / `SimFill` / `SimApplyResult` / `ReplayResult` | 模拟仓契约；`CONTRACT_MULTIPLIERS`（约 80 品种合约乘数）；`contract_multiplier(symbol)`；`infer_market` |
| `simulated_portfolio.py` | `SimulatedPortfolio` | 核心：`apply_signal`（干预门 → 风控门 → 逐标的 `_reconcile` 撮合 → 落盘）、`mark_to_market`（逐日盯市）、`close_symbol`/`all_close`（一键平仓）、`portfolio_risk_status`（组合级三级预警）、`attribute_factor_returns`（因子归因 → LiveFeedbackRecord） |
| `simulated_engine.py` | `SimulatedReplayEngine.replay()` | 严格时间单向：t 日信号 → t+1 开盘成交 → t+1 收盘盯市 → 归因（零未来函数）；`SimulatedPaperTrader` 实时纸面（on_signal/on_market_close/SQLite 持久化） |
| `book.py` | `build_book_from_ticks` / `OrderBookSnapshot` | tick 构造 5 档盘口（五档数组/单档字段双形态，last_price 单档兜底） |
| `matching.py` | `OrderBookMatchingEngine.match_market` | 市价逐档消耗（buy 吃 ask 升序），加权均价，深度不足部分成交，盘口异常降级 bps |
| `orders.py` | `Order` / `OrderLifecycle` / `OrderState` | 订单状态机（PENDING→SUBMITTED/PARTIAL/FILLED/CANCELED/REJECTED） |
| `stop_orders.py` | `StopOrderManager.check()` | 止损止盈单 → `CloseInstruction`（long 跌破/涨破） |
| `intervention.py` | `InterventionController` | `AUTHORITY="highest"`：pause/resume/request_all_close/mark_flattened/should_block |
| `gateway.py` | `AbstractGateway` / `SimulatedGateway` / `submit_with_retry` | 部分成交/限价单（`_limit_tradeable`）/集合竞价（`auction_open` 最大成交量均衡价）/补单（fill_partial）/重试+超时回滚 |
| `sqlite_store.py` | `SimSQLiteStore` | 4 表持久化（sim_account/sim_positions/sim_fills/sim_equity_curve），WAL，启动恢复 |

---

### 4.8 监控层 `fts.monitor`

**文件**: `fts/monitor/`

**职责**: 系统健康监控 + 精英因子跟踪 + Web UI + Prometheus 指标 + 人审工作台。

#### 循环状态检查（`__init__.py`）

| 函数 | 说明 |
|------|------|
| `check_loop_status(loop_name)` | 单循环状态（L1/L2/L3），读 state.duckdb / memory 状态 |
| `check_all_status()` | 系统级状态汇总 |
| `check_data_sources_status()` | 数据源健康度 |
| `format_status_report` / `status_report_to_json` | 报告格式化 |

#### `FTSDashboardServer`（http_server.py，1908 行）

纯标准库 HTTP 服务器，端口 9100。端点（20+）:

| 端点 | 说明 |
|------|------|
| `/` | 内嵌单页仪表盘（暗色主题，10s 刷新） |
| `/api/status` | 系统状态 + DuckDB 因子统计 |
| `/api/factors` | 精英因子列表（DuckDB JOIN，JSON 回退） |
| `/api/candidates` | L1 候选因子池 |
| `/review` + `/api/review/{pending,history}` + POST `/api/review/{approve,reject,auto}` | Alpha 人审工作台（C8） |
| `/metrics` | Prometheus 完整指标 |
| `/metrics/data-sources` | 数据源指标（5s 缓存） |
| `/health` | 健康检查（含数据源熔断状态） |
| `/api/v1/risk/status` | 风控状态 |
| `/api/v1/live/factors` + `/{id}/deviation` | Live 因子表现与偏离 |
| `POST /api/v1/signal/submit` | 信号提交 → SignalValidator → RiskManager → SimulatedTradeAdapter 模拟成交 |

#### `EliteFactorTracker`（elite_tracker.py）

- `determine_grade(quality_score)`：A ≥ 40 / B ≥ 30 / C < 30
- 状态机：`active` → `observing`（B 级观察 3 月）→ `decaying` → `critical_decay` → `retired`
- `decay_grade(ic_series)`：滚动 6M IC 线性回归斜率归一化分级（GAP-I305）
- `auto_retire`：连续零值 IC≥4 / decay_6m≥0.30 / critical_decay / 连续零月≥12 / Sharpe 连续降≥12
- `AutoRetireManager`：封装淘汰 + 冷却复评（7 天）

#### `DataQualityMonitor`（data_quality_monitor.py）

- `register_factor(baseline_ic, baseline_capacity)` / `check(current_ic, current_capacity)`
- 告警：`ic_drift`（|z|≥2 warning / ≥3 critical）、`capacity_shock`（±50% / ±80%），冷却 3600s
- 三维数据质量指标：完整性（coverage/timestamp/field/missing）/ 准确性（cross_source/outlier/jump/PSI drift）/ 及时性（update_delay/cache_hit/freshness）

#### `DataLevelMonitor`（data_level_monitor.py，GAP-F06）

缺失率 5%/20%、3σ 异常 1%/5%、复权容差 0.5%、多源分歧 0.5%/2%，按 metric_name 冷却。

#### `LogicMonitor`（logic_monitor.py）

行为漂移（与动量/均值回归基准相关 < 0.3）、极端预测（|z|>2σ 占比 > 5%）、换月日异常（前后 5 日均值变化 > 3σ）。

#### `LiveFactorMonitor`（live_factor_monitor.py）

回测基线 vs 实盘表现，偏离 > 30% warning、> 45% critical；`ingest_live_ic`（GAP-I401 实盘 IC 数据源）。

#### `MetricsRegistry`（prometheus_metrics.py）

线程安全指标注册表，分组：A.2 衰减（fts_factor_decay_*）、A.3 Regime/权重（fts_regime_*/fts_weight_rebalance_total）、C.2 Live/风控（fts_live_factor_*/fts_risk_check_*）、C.3 反馈（fts_feedback_*/fts_evolution_*）。`render()` 输出 Prometheus 文本格式。

---

### 4.9 调度层 `fts.scheduler`

**文件**: `fts/scheduler/`

**职责**: 定时任务注册 + APScheduler 调度 + 进程守护 + 热重载。

| 类/函数 | 说明 |
|---------|------|
| `TaskSpec` | `@dataclass`：name / cron_expression / callable_path / description / enabled / trace_id_prefix |
| `TaskRegistry` | `register / unregister / get / list_all / list_enabled`；全局 `REGISTRY` |
| `SchedulerEngine` | APScheduler 包装：`start / stop / running / start_watchdog`；APScheduler 缺失时静默降级 |
| `run_scheduler(daemon=True)` | 调度器入口 |
| `ProcessWatchdog` | 进程守护：3 次/30s 重启计数，熔断 300s |
| `HotSwapWatcher` | 开发期模块热重载（watchdog 库缺失时降级） |

**15 个默认任务**:

| 任务名 | cron | 说明 |
|--------|------|------|
| `l1_meta_loop` | `59 7 * * 1-5` | L1 知识补给 + 种子注入 |
| `l2_evolution_loop` | `0 0 * * 1-5` | L2 夜间演化（分层训练集排除盲测池） |
| `l3_portfolio_loop` | `0 19 * * 1-5` | L3 期货组合构建（equal_weight 每日重算，与信号管道解绑 GAP-072） |
| `l3_portfolio_loop_stock` | `30 19 * * 5` | L3 股票组合权重重算 |
| `futures_signal_pipeline` | `0 20 * * 1-5` | 期货横截面信号报告 |
| `daily_signal_pipeline` | `45 8 * * 1-5` | 股票/ETF 逐股打分信号 |
| `sync_futures_data` | `30 17 * * 1-5` | 期货多源数据同步 |
| `sync_stock_data` | `0 17 * * 1-5` | 股票数据同步（TDX 优先） |
| `health_check` | `*/10 * * * *` | 健康检查 |
| `monthly_decay_eval` | `0 4 1 * *` | 月度衰减评估 + 自动淘汰 |
| `data_quality_eval` | `*/5 * * * *` | 数据质量快照 |
| `data_level_monitor` | `0 4 * * *` | 数据级监控 |
| `logic_monitor` | `0 22 * * *` | 逻辑监控 |
| `factor_inspector` | `0 3 * * *` | 因子巡检降级 |
| `sync_liquidity_pool` | `0 8 * * 6` | 动态池刷新 |

---

### 4.10 风控层 `fts.risk`

**文件**: `fts/risk/`

**职责**: 单笔/组合级风控检查，拦截不合格信号（FTS 只做检查，交易执行由下游负责）。

| 类 | 说明 |
|----|------|
| `RiskManager` | `check(signal, account, positions) → RiskCheckResult`；5 项规则：① 单品种仓位 ≤10% ② 组合回撤 ≤20% ③ 单日亏损 ≤5% ④ 杠杆 ≤3x ⑤ 前 3 大品种集中度 ≤50%（品种<3 跳过） |
| `RiskConfig` | single_position_limit_pct=0.10 / max_portfolio_drawdown_pct=0.20 / daily_loss_limit_pct=0.05 / max_leverage=3.0 / max_concentration_pct=0.50 / max_open_positions=20 |
| `compute_portfolio_metrics` | 组合级 6 维指标（杠杆仓位/波动尾部 EWMA+VaR95/CVaR95/集中度 HHI/损益回撤/流动性/执行质量），异常降级不抛错 |
| `evaluate_metrics` | 三级预警判定（WARN/BLOCK/FORCE_CLOSE），杠杆/总仓位/回撤/日亏/连续亏损超限 → FORCE_CLOSE |
| `SimulatedTradeAdapter` | 模拟成交适配器（`submit_signal` 模拟成交 + 内存持仓） |

---

### 4.11 ML 模型层 `fts.ml`

**文件**: `fts/ml/`

**职责**: 封装 ML 模型，供 L3 信号合成（ml_ensemble 模式）与深度因子生成。可选依赖 `[ml]`，缺失时工厂返回 None（调用方降级，不抛异常）。

| 类/函数 | 说明 |
|---------|------|
| `ModelKind` | LIGHTGBM / XGBOOST / ENSEMBLE / MLP |
| `MLSignalModel` | `_build()` 构造底层模型，`fit` / `predict`（ENSEMBLE 等权平均） |
| `create_signal_model(kind, params, seed)` | 工厂，依赖缺失返回 None |
| `MLPFactorModel` / `GRUFactorModel` / `TransformerFactorModel` | 纯 numpy 深度模型（零未来函数，权重可导出，供 deep_factor 生成确定性 code） |
| `DeepFactorGenerator` | `generate(data, forward_returns, market, ...)`：特征构造 → 滚动窗口样本 → 训练 → 验证集 IC → 权重内嵌生成 FactorProgram code（可过审计链） |
| `TrainMode` | CROSS_SECTIONAL / TIME_SERIES / ENSEMBLE_FUSION |
| `SignalModelTrainer` | `train(X, y, feature_names) → TrainResult`：NaN 清理 → 训练 → 手工 R² → 特征重要性提取 |

---

### 4.12 信号桥接层 `fts.bridge`

**文件**: `fts/bridge/signal_bridge.py`

**职责**: 向 VNPY 等下游发布信号，三种传输协议。

| 类/函数 | 说明 |
|---------|------|
| `SignalBridge` | `publish(signal) → signal_id` / `latest()` / `status()` |
| `BridgeStatus` | protocol / available / detail / latest_signal_id / latest_timestamp |
| `BridgeError` | 操作失败异常 |

**协议**: JSON（默认，无依赖，`.tmp` + `os.replace` 原子替换）/ Redis（可选 `[bridge]`，redis-py 缺失抛 BridgeError 不静默）/ REST（标准库 http.client，≥400 抛 BridgeError）。`PROTOCOLS = ("json", "redis", "rest")`。

---

### 4.13 跨市场验证 `fts.cross_market`

**文件**: `fts/cross_market/`

**职责**: 跨市场因子泛化验证，支持三个方向：期货→A股、期货→ETF、股票→期货。

| 类 | 说明 |
|----|------|
| `CrossMarketDataAdapter` | `get_panel(target_market, days, max_stocks)` 统一格式 + 路由；`execute_factor_on_market` 逐品种执行因子 |
| `CrossMarketEngine` | `run_futures_to_stock` / `run_futures_to_etf` / `run_stock_to_futures` → `CrossMarketResult` / `CrossMarketReport` |

**分类阈值**: `GENERALIZATION_THRESHOLD=0.02`、`FUTURES_SPECIFIC_THRESHOLD=0.03`、`FAILURE_THRESHOLD=0.01`、`RETENTION_RATIO=0.50`。分类规则：universal（目标 IC≥0.02 且保持率≥50%）/ failed / futures_specific / stock_specific / unknown。

---

### 4.14 LLM 客户端 `fts.llm`

**文件**: `fts/llm.py`

**职责**: 统一 LLM 调用接口。

| 类/函数 | 说明 |
|---------|------|
| `LLMClient` | 抽象基类：`complete(prompt, max_tokens=4000) → (text, tokens)`；`generate_json`（8 层 JSON 容错解析，含 `_repair_json` 栈式括号修复）；`bootstrap_factors` |
| `OpenAIClient` | `chat.completions.create`，重试 2 次；`bootstrap_factors` 生产级实现（调试响应落盘 + 修复 prompt 重试） |
| `AnthropicClient` | `messages.create` |
| `MockLLMClient` | 开发/测试用预设响应 |
| `LLMError` / `LLMCallRecord` | 异常 / 调用记录（含 token 统计） |
| `get_llm_client(backend="", temperature=None)` | 工厂：`FTS_LLM_BACKEND` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` → MockLLMClient |

**环境变量**: `OPENAI_API_KEY/BASE_URL/MODEL`（默认 gpt-4o）、`ANTHROPIC_API_KEY/MODEL`（默认 claude-sonnet-4-20250514）、`FTS_LLM_TEMPERATURE`（默认 1.2）。

---

### 4.15 CLI 入口 `fts.cli`

**文件**: `fts/cli.py`（2819 行，17 个顶级子命令）

**职责**: 统一命令行入口，所有子命令启动时生成 session_id / trace_id（HARNESS §trace_id 全链路）。通过 `pyproject.toml` 注册为 `fts` 命令。

| 子命令 | 子子命令 | 说明 |
|--------|---------|------|
| `fts version` | — | 打印版本 |
| `fts monitor` | — | 循环健康状态（`--json`） |
| `fts ui` | — | Web UI（`--host` / `--port 9100`） |
| `fts evolution` | `run` | L2 演化（`--universe futures/csi300/single`, `--max-generations`, `--max-stocks`, `--symbol`, `--days`） |
| `fts meta-loop` | `run` | L1 元循环（`--market stock/futures`, `--symbols`） |
| `fts portfolio` | `run` | L3 组合（`--universe`, `--synthesis-mode` 9 种, `--optimizer-mode risk_parity/mvo/bl`, `--returns-matrix`, `--force-recompute`） |
| `fts scheduler` | `run` / `list` | 调度器管理 |
| `fts catalog` | `stats` / `verify` / `backup` | 因子存储统计 / JSON↔DuckDB 一致性 / 备份 |
| `fts factor` | `list` / `show` / `seeds` / `stats` / `lineage` / `cross-market` / `review` / `recalibrate` / `micro-generate` / `micro-evaluate` / `senti-generate` / `senti-consistency` | 因子管理 + Alpha 人审 + 重校准 + 微观结构/舆情候选 |
| `fts seed` | `validate` / `report` / `dedup` | 种子因子校验/统计/查重 |
| `fts backtest` | `run` / `batch` / `compare` | 回测（`--frequency daily/1m/5m/15m/30m/60m`） |
| `fts feature` | `list` / `analyze` | 特征工程中台 |
| `fts gp` | `evolve` | GP 遗传规划演化 |
| `fts feedback` | `trigger` / `process` / `report` / `stats` / `import` / `live-ic` | 反馈闭环 + 实盘反馈导入 |
| `fts bridge` | `serve` / `publish` / `status` | 信号桥接（REST 服务 8765 / 发布 / 状态） |
| `fts data` | `status` / `sync-futures` / `sync-stock` / `cross-check` / `fuse` | 数据源状态/同步/交叉验证/K 线融合 |

**注**: 数据同步等运营操作由 `scripts/` 脚本与 scheduler jobs 承担，非 CLI 子命令。

---

## 5. 关键类/函数速查表

### 5.1 核心引擎类

| 类 | 所在模块 | 职责 |
|----|---------|------|
| `EvolutionLoop` | `factor_engine.evolution_loop` | L2 因子演化主循环 |
| `EvolutionLoopFutures` / `EvolutionLoopStock` | `factor_engine.evolution_futures` / `evolution_stock` | 市场专用引擎 |
| `MetaLoop` | `factor_engine.meta_loop` | L1 元循环 |
| `PortfolioLoop` | `factor_engine.portfolio_loop` | L3 组合循环 |
| `BatchMiner` | `factor_engine.batch_mining` | 批量挖掘漏斗 |
| `FactorVerifier` / `L1Verifier` / `L3Verifier` | verifier / meta_loop / portfolio_loop | 三级 Verifier |
| `EvaluationChain` | `factor_engine.evaluation_chain` | 三级评估链 |
| `SeedPool` | `factor_engine.seed_pool` | 种子池 |
| `MacroEvolver` | `factor_engine.macro_evolution` | 宏观演化（LLM） |
| `FactorExecutor` | `factor_engine.factor_program` | 因子沙箱执行器 |
| `FactorQualityCard` | `factor_engine.factor_quality_card` | 质量评分卡 |
| `FactorAuditor` | `factor_engine.audit` | 6 项强制审计 |
| `HighICScreener` | `factor_engine.high_ic_screener` | 高 IC 质检关卡 |
| `FactorInspector` / `FactorReviewWorkflow` | `factor_engine.factor_inspector` | 因子巡检降级 / Alpha 人审 |
| `FactorRepository` / `FactorLineage` | `factor_engine.factor_db` | 因子数据库 CRUD / 血缘 |
| `FTSDataProvider` | `data` | 统一数据入口 |
| `FuturesDataProvider` | `data_futures` | 期货数据提供者 |
| `FuturesDataAggregator` | `data_sources.aggregator` | 多源调度器 |
| `OHLCVFusion` | `data_sources.fusion` | 多源融合器 |
| `StorageRegistry` / `StateKVStore` | `store.registry` / `store.state_db` | 存储契约 / 运行状态库 |
| `SimulatedPortfolio` | `live_trade.simulated_portfolio` | 模拟仓核心 |
| `SimulatedReplayEngine` / `SimulatedPaperTrader` | `live_trade.simulated_engine` | 回放 / 纸面引擎 |
| `OrderBookMatchingEngine` | `live_trade.matching` | tick 盘口撮合 |
| `InterventionController` | `live_trade.intervention` | 人工干预（权限最高） |
| `FTSDashboardServer` | `monitor.http_server` | Web UI 仪表盘 |
| `EliteFactorTracker` / `AutoRetireManager` | `monitor.elite_tracker` | 精英因子跟踪/自动淘汰 |
| `SchedulerEngine` | `scheduler.engine` | APScheduler 调度 |
| `ProcessWatchdog` | `scheduler.watchdog` | 进程看门狗 |
| `RiskManager` | `risk.risk_manager` | 5 项风控检查 |
| `FeedbackLoop` / `LiveFeedbackImporter` | `factor_engine.feedback_loop` | 反馈闭环 / 实盘反馈导入 |
| `BacktestPipeline` | `factor_engine.backtest_pipeline` | 回测管线 |
| `GPEvolver` / `OperatorEvolutionEngine` / `SymbolicRegressionSearcher` | `factor_engine.gp_evolver` / `operator_evolution` / `symbolic_regression` | 进化搜索三引擎 |
| `DeepFactorGenerator` | `ml.deep_factor` | 深度因子生成 |
| `FactorClusteringEngine` / `PCASignalCompressor` | `factor_engine.factor_clustering` | 因子聚类 / PCA 降维 |
| `FactorOptimizer` | `factor_engine.factor_optimizer` | 因子优化（正交化/缓存） |
| `RegimeAwareSelector` / `MultiHorizonHMMDetector` / `StockRegimeSelector` | `factor_engine.regime*` | Market Regime 检测体系 |
| `RegimeSmoother` / `AdaptiveWeightManager` | `factor_engine.adaptive_weight` | 权重平滑 / 自适应 |
| `SignalBridge` | `bridge.signal_bridge` | 信号桥接（json/redis/rest） |
| `CrossMarketEngine` | `cross_market.engine` | 跨市场验证引擎 |
| `MLSignalModel` / `SignalModelTrainer` | `ml.models` / `ml.trainer` | ML 信号模型 / 训练器 |
| `LLMClient` / `OpenAIClient` / `AnthropicClient` | `llm` | LLM 客户端 |
| `DataQualityMonitor` / `DataLevelMonitor` / `LogicMonitor` / `LiveFactorMonitor` | `monitor` | 各类监控器 |

### 5.2 关键函数

| 函数 | 所在模块 | 说明 |
|------|---------|------|
| `generate_trace_id(prefix)` / `generate_run_id()` / `generate_session_id()` | `factor_engine.state` | 全链路 ID 生成 |
| `create_factor_program` / `validate_factor_code` / `fix_factor_code` / `generate_factor_id` | `factor_engine.factor_program` | 因子程序创建/校验/修复/ID |
| `evolve_micro(factor, data, fwd_ret, n_trials)` | `factor_engine.micro_evolution` | 微观参数优化（两阶段漏斗） |
| `synthesize_signals(factors, mode)` | `factor_engine.portfolio_loop` | 信号合成（9 种模式） |
| `regime_adaptive_weight_adjustment` / `orthogonalize_factors` / `decay_test` / `build_combo` | `factor_engine.portfolio_loop` | L3 组合工具 |
| `parse_expression` / `validate_expr` / `evaluate` / `compute_max_lookback` / `eval_fts_expr` / `verify_registry_consistency` | `factor_engine.expr_dsl.*` | DSL 全流程 |
| `get_config()` / `load_config()` / `is_weight_recompute_day` | `config.settings` | 配置 |
| `get_llm_client(backend)` | `llm` | LLM 客户端工厂 |
| `load_all_yaml_seeds` / `load_futures_seeds_full` / `load_wq101_seeds` | `factor_engine.seed_loader` 等 | 种子加载 |
| `get_dominant_contracts` / `get_realtime_prices` / `get_dynamic_core_subset` | `data_futures` | 期货数据工具 |
| `migrate_schema(db_path)` | `data_sources.migrate` | DuckDB schema 迁移 |
| `compute_multi_horizon_ic` / `compute_multi_frequency_signal` / `compute_crowding` | `factor_engine.horizon_analysis` 等 | 因子分析工具 |
| `industry_neutralize` / `size_neutralize` / `barra_neutralize_matrix` | `factor_engine.neutralization` / `barra` | 中性化工具 |
| `check_all_status` / `check_loop_status` / `check_data_sources_status` | `monitor` | 状态监控 |
| `list_tasks` / `get_task` / `run_scheduler` | `scheduler` | 调度工具 |
| `create_signal_model(kind)` | `ml.models` | ML 模型工厂（缺失返回 None） |
| `run_symbol_holdout` / `validate_regime_predictive_power` / `compute_cost_sensitivity` | `factor_engine.*` | 验证工具 |

---

## 6. 依赖关系图

### 6.1 模块依赖关系

```
cli.py（统一入口，session_id + trace_id）
  ├── config.settings ──→ FTSConfig（dataclass + YAML + 环境变量）
  ├── core.atomic ──→ 原子文件操作
  ├── data ──→ FTSDataProvider
  │           ├── data_mcp ──→ TdxLocalSource.fetch_stock_ohlcv + 腾讯 HTTP API（A 股/ETF）
  │           ├── data_fundamental ──→ MCPBridge + EastmoneyMacroSource + AshareSpecialSource + StockFundamentalSource
  │           ├── data_futures ──→ FuturesDataAggregator + RollCalendar + OvernightGap + StateKVStore（动态池 SSOT）
  │           └── data_futures_fundamental ──→ AkshareFuturesFundamentalProvider（库存/基差/仓单）
  │                └── data_sources ──→ 多源适配器
  │                    ├── aggregator ──→ 熔断器 + 交叉验证 + DuckDB 缓存 + pre_settle 派生
  │                    ├── fusion ──→ 5 种融合策略
  │                    ├── tdx_local_source / tqsdk_* / ifind_* / wind_source
  │                    ├── macro_aligner / macro_eastmoney_source
  │                    └── migrate ──→ 全部缓存表 DDL
  ├── factor_engine ──→ 核心因子引擎
  │   ├── contracts ──→ 所有模块依赖的核心 TypedDict 契约（SSOT）
  │   ├── evolution_loop ──→ macro_evolution → micro_evolution
  │   │                        → evaluation_chain → verifier
  │   │                        → audit → high_ic_screener → factor_quality_card
  │   │                        → backtest_pipeline → seed_pool → experience_chain → state
  │   │                        → batch_mining → executor_backend
  │   │                        → gp_evolver / operator_evolution / symbolic_regression
  │   │                        → factor_db（DuckDB SSOT，写路径反转）
  │   ├── meta_loop ──→ BootstrappingChain → L1Verifier → extractors → llm
  │   ├── portfolio_loop ──→ signal_contract → factor_db.repository
  │   │                        → factor_clustering → factor_optimizer → weight_learning → black_litterman
  │   │                        → regime* → adaptive_weight → portfolio_risk_controls → portfolio_walk_forward
  │   ├── expr_dsl ──→ parser → validator → executor → compiler → runtime → factory
  │   │                └── registry → feature_ops + ops_library（单一算子事实源，双注册表校验）
  │   ├── factor_db ──→ DuckDB（factor_catalog_*.duckdb 13 张表）
  │   └── feedback_loop ──→ experience_chain → monitor
  ├── store ──→ StorageRegistry（storage_landscape.yaml）+ StateKVStore（data/state.duckdb）
  ├── live_trade ──→ risk（RiskManager/portfolio_metrics）+ cost_model + intervention + sqlite_store
  ├── monitor ──→ factor_engine.monitor（底层 check_loop/check_all）
  │           ├── http_server ──→ 纯标准库 HTTPServer（9100）+ 人审端点
  │           ├── elite_tracker ──→ factor_db（factor_quality_scores/status_history）
  │           ├── prometheus_metrics ──→ 指标注册表
  │           └── data_quality_monitor / data_level_monitor / logic_monitor / live_factor_monitor
  ├── scheduler ──→ APScheduler（缺失降级）
  │           ├── tasks ──→ jobs（12 个工作函数）
  │           ├── watchdog / hotswap
  ├── risk ──→ 独立风控检查（无外部依赖）
  ├── ml ──→ lightgbm / xgboost（可选）+ 纯 numpy 深度模型
  ├── bridge ──→ redis-py（可选）
  ├── cross_market ──→ factor_engine（FactorExecutor）+ data
  └── llm ──→ OpenAI / Anthropic SDK（可选）

scripts/ ──→ fts.*（同步/信号/演化/回填/验证脚本）
tests/   ──→ fts.*（100 个文件，5200+ 用例）
```

### 6.2 外部依赖

| 依赖 | 用途 | 必选/可选 | 安装组 |
|------|------|-----------|--------|
| numpy>=1.24 / pandas>=2.0 / scipy>=1.10 | 数值计算 / 数据处理 / 统计分析 | 必选 | 核心 |
| pyyaml>=6.0 / shap>=0.46 / python-dotenv>=1.0 / duckdb>=1.0 | 配置 / SHAP / .env / 存储 | 必选 | 核心 |
| optuna>=3.0 | 贝叶斯调参 | 可选 | `[evolution]` |
| openai>=1.0 / anthropic>=0.20 | LLM 调用 | 可选 | `[llm]` |
| akshare>=1.18.64 | 金融数据 | 可选 | `[mcp]` |
| scikit-learn>=1.3 | 组合构建 | 可选 | `[portfolio]` |
| lightgbm>=4.0 / xgboost>=2.0 | ML 模型 | 可选 | `[ml]` |
| redis>=5.0 | 信号桥接 | 可选 | `[bridge]` |
| hmmlearn>=0.3 / statsmodels>=0.14 | Regime 检测 | 可选 | `[regime]` |
| fastapi>=0.100 / uvicorn>=0.23 | 监控服务 | 可选 | `[monitor]` |
| distributed>=2024.3 | 分布式执行后端 | 可选 | `[distributed]` |
| requests>=2.28 / tqdm>=4.60 / pyarrow>=14.0 | 数据层工具 | 可选 | `[data]` |
| APScheduler | 定时调度 | 运行时 | 需手动安装 |

---

## 7. 项目运行方式

### 7.1 安装

```bash
# 基础安装
pip install -e .

# 全部可选依赖（推荐开发环境）
pip install -e ".[all,dev]"
# 或
pip install -r requirements.txt

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
FTS_ELITE_DIR=memory/knowledge/factors/stocks_elite
FTS_FUTURES_ELITE_DIR=memory/knowledge/factors/futures_elite
FTS_DEFAULT_MARKET=futures
FTS_EVOLUTION_MODE=hybrid
FTS_MAX_WORKERS=4
FTS_LOG_LEVEL=INFO
# A 股特有字段/股票基本面开关（回填缓存后置 1）
FTS_ASHARE_SPECIAL_ENABLED=0
FTS_STOCK_FUNDAMENTAL_ENABLED=0
```

### 7.3 运行命令

```bash
# 查看版本
fts version

# 查看监控状态
fts monitor
fts monitor --json

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
fts portfolio run --optimizer-mode risk_parity --returns-matrix factors_returns.csv

# 因子管理
fts factor list --market futures
fts factor stats --market futures --json
fts factor show <factor_id> --market futures
fts factor lineage <factor_id>
fts factor seeds --market futures
fts factor cross-market --direction futures-to-stock --days 120 --max-stocks 10
fts factor review pending            # Alpha 人审队列
fts factor recalibrate list          # 重校准队列

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
fts feedback import live_feedback.csv   # 实盘反馈导入

# 信号桥接
fts bridge serve --host 127.0.0.1 --port 8765
fts bridge publish --protocol json --input signals/latest.json
fts bridge status --protocol redis

# 数据管理
fts data status --json                 # 多源熔断器/成功率状态
fts data sync-futures --days 120       # 期货数据同步
fts data cross-check --symbol RB0 --date 2026-08-11
fts data fuse --symbol RB0 --strategy MEDIAN

# 调度器 / Web UI
fts scheduler run
fts scheduler list
fts ui --port 9100
```

### 7.4 关键脚本（`scripts/`）

| 脚本 | 用途 |
|------|------|
| `sync_futures_data.py` | 手动触发多源期货数据同步 |
| `sync_tq_contract_kline.py` | TQ 全合约日线同步（contract_kline 全品种） |
| `futures_signal_pipeline.py` | 期货横截面信号管道（Ridge 加权 + 宏观注入）→ `reports/{date}/` |
| `daily_signal_pipeline.py` | 股票每日信号管道（中性化/L3 模式/Regime 自适应） |
| `run_futures_evolution.py` | 期货 L2 演化（DuckDB 截面数据） |
| `backfill_futures_hold.py` | hold/settle 回填 + pre_settle 派生（幂等可重入） |
| `backfill_ashare_special.py` / `backfill_stock_fundamental.py` | A 股特有字段/股票基本面回填 |
| `build_cap_map.py` | 市值映射构建（GAP-086，size 中性化生效前提） |
| `migrate_elite_json_to_catalog.py` | JSON 精英因子 → DuckDB 迁移（差量补齐/幂等） |
| `migrate_state_to_duckdb.py` | 状态 JSON → StateKVStore 迁移 |
| `archive_history_cold.py` | 行情库按年冷热归档（--dry-run/--verify-only） |
| `simulated_replay.py` | 模拟回放（信号→撮合→盯市→归因→反馈闭环） |
| `verify_doc_consistency.py` | 文档一致性校验（HARNESS Layer 2） |
| `update_doc_versions.py` | 版本号同步 |
| `generate_operator_catalog.py` | 算子目录自动生成（operator_catalog.yaml） |
| `start_fts.ps1` | PowerShell 启动脚本（加载 .env、配置 PATH、打印命令清单） |

### 7.5 开发模式

```bash
# 运行测试（全量，仅发布前/月度巡检）
python -m pytest tests/ --no-cov --tb=short
# 日常：指定模块
python -m pytest tests/factor_engine/ -q
python -m pytest tests/data_sources/ -q
python -m pytest tests/live_trade/ -q
# 覆盖率
python -m pytest tests/ --cov=fts --cov-report=term-missing
# 代码检查（格式化 + 静态校验 + 类型检查）
ruff format src/ tests/
ruff check src/ tests/
mypy src/
```

---

## 8. 配置系统

### 8.1 配置层次

```
环境变量 (FTS_*) → YAML 配置文件（config/settings.yaml 或 FTS_CONFIG_FILE）→ Python 默认值（FTSConfig dataclass）
```

### 8.2 核心环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FTS_MEMORY_DIR` | `memory` | 运行时持久化目录 |
| `FTS_ELITE_DIR` / `FTS_FUTURES_ELITE_DIR` | `memory/knowledge/factors/stocks_elite` / `futures_elite` | 精英因子目录 |
| `FTS_DEFAULT_MARKET` | `futures` | 默认市场 |
| `FTS_EVOLUTION_MODE` | `hybrid` | 演化模式（operator/operator_first/code/hybrid/batch） |
| `FTS_MAX_WORKERS` | `4` | 并行工作数 |
| `FTS_MACRO_FIELD_INJECTION` / `FTS_MACRO_LAG_DAYS` | `1` / `30` | 宏观字段注入开关 / 发布滞后天数 |
| `FTS_LLM_BACKEND` / `FTS_LLM_TEMPERATURE` | `` / `1.2` | LLM 后端 / 温度 |
| `FTS_ASHARE_SPECIAL_ENABLED` / `FTS_STOCK_FUNDAMENTAL_ENABLED` | `0` / `0` | A 股特有字段 / 股票基本面开关 |
| `FTS_FUTURES_ENHANCE_ENABLED` | `0` | 字段增强层（iFinD/Wind）开关 |
| `FTS_INJECT_OVERNIGHT_GAP` | `0` | 夜盘跳空注入开关 |
| `FTS_EXECUTOR_BACKEND` | `thread` | 执行后端（thread/process/dask/ray） |
| `FTS_CS_MIN_COVERAGE` | `0.8` | 横截面面板覆盖率阈值 |
| `FTS_CLUSTER_QUOTA_ENABLED` / `FTS_CLUSTER_MAX` | `1` / `15` | 结构簇配额 |
| `FTS_L3_WEIGHT_RECOMPUTE_CADENCE` / `_WEEKDAY` | `weekly` / `4` | L3 权重重算节奏（周五） |
| `FTS_STOCK_SIGNAL_NEUTRALIZE` / `FTS_STOCK_SIGNAL_L3_MODE` / `FTS_STOCK_SIGNAL_REGIME` | `none` / `ridge` / `none` | 股票信号管道配置 |
| `FTS_STORAGE_LANDSCAPE_PATH` | `` | 存储注册表契约路径覆盖 |
| `FTS_LOG_LEVEL` / `FTS_LOG_FILE` | `INFO` / `` | 日志 |

### 8.3 关键配置字段（FTSConfig 分组）

- **路径**: `memory_dir` / `elite_dir` / `futures_elite_dir` / `default_market`
- **演化**: `evolution_mode` / `max_generations(10)` / `population_size(20)` / `micro_trials_per_generation(50)` / `micro_staged_evolution(true)` / `micro_coarse_trials(20)` / `micro_coarse_ic_floor(0.02)` / `max_per_family(15)` / `structure_cluster_*` / `shap_*(25/50/50)` / `evolution_success_pattern_*` / `evolution_stop_*(关/K=5)` / `extreme_perturb_pct(0.01)` / `eval_horizons((1,5,10,20))`
- **L2 准入去冗余**: `l2_elite_corr_threshold(0.9)` / `l2_elite_orthogonalize(true)` / `l2_orthogonal_residual_corr_max(0.3)` / `l2_orthogonal_basis_enabled(true)` / `l2_barra_style_neutral(true)`
- **批量挖掘**: `batch_size(20)` / `batch_max_candidates(5)` / `executor_backend("thread")` / `executor_max_workers(4)`
- **L1**: `meta_loop_interval_hours(24)` / `meta_loop_max_tokens(8000)` / `l1_announcement_extractor_enabled(true)` / `l1_macro_extractor_enabled(true)`
- **L3**: `portfolio_max_factors(20)` / `portfolio_top_n(5)` / `portfolio_decay_days(90)` / `portfolio_optimizer_mode("risk_parity")` / `l3_turnover_penalty(0.0)` / `l3_weight_recompute_cadence("weekly")` / `recalibration_enabled(false)`
- **股票**: `stock_neutralization(true)` / `industry_map_path` / `cap_map_path`（默认动态化）/ `stock_signal_*`
- **期货**: `futures_adjusted(true)` / `roll_cost_bps(2.0)` / `minute_cache_max_age_days(1)` / `futures_neutralization(true)` / `futures_enhance_enabled(false)` / `futures_limit_pct(0.08)`
- **回测**: `backtest_capacity_cap(true)` / `capacity_cap_ratio(0.01)` / `force_walkforward(true)` / `backtest_trade_filter(true)`
- **保证金**: `margin_rate_map({})` / `max_margin_usage(0.80)`
- **DuckDB 并发**: `duckdb_single_writer(true)` / `duckdb_read_pool_size(4)` / `duckdb_batch_size(1000)` / `duckdb_commit_every(100)`
- **Verifier**: `verifier{min_sharpe 1.5, max_correlation 0.5, max_turnover 0.50, max_decay_rate 0.30, min_n_factors 3}`
- **日志**: `log_level(INFO)` / `log_file("")`

---

## 9. 数据流与执行流程

### 9.1 因子演化全流程

```
L1 Meta-Loop (工作日 07:59)
  ├── agentic 市场感知（FTSDataProvider 市场快照）
  ├── debate 分析（识别薄弱维度）
  ├── Bootstrapping（提取器 → LLM → 模板三源候选）
  ├── L1 Verifier（economic_logic >= 2/4 + 可执行 + 非重复 + narrative）
  └── 注入 factor_pool.json + l1_injected/
        ▼
L2 Evolution Loop (工作日 00:00)
  ├── DataQualityMonitor 数据完整性校验 → 熔断预检查
  ├── 种子加载 + L1 候选合并（pending 门控）→ 种子评估晋升
  ├── UCT 父因子选择（UCB，C=1.0）
  ├── 模式分派（batch 漏斗 / 单因子 operator_first/operator/code/hybrid）
  ├── 宏观演化（LLM 改逻辑 + 父失败归因 + 成功模式）→ 微观演化（optuna 两阶段）
  ├── 运行时校验 + 快速预筛（nunique/std/快速 IC）
  ├── 三级评估链（L1 回测 → L2 经济逻辑 → L3 多重检验 + 走航 + 极值扰动）
  ├── Verifier 判定 → 质量评分卡（10 维 A/B/C）→ 端到端回测 → 数据质量
  ├── 6 项强制审计 → 消融/因果/鲁棒性/SHAP（五层逻辑审查）
  ├── 晋升门（去重 → 结构簇配额 → L2 正交去冗余 → 高 IC 门 → 多重检验门）
  ├── 影子池 5 日观察 → DuckDB 先写（SSOT）→ JSON 快照备份
  ├── 经验链记录 → 状态持久化（state.duckdb）→ 实验日志导出
  └── 收尾：相关性索引 + 周期因子审查（自动退役 + 反馈闭环 + 逻辑监控）
        ▼
L3 Portfolio Loop (工作日每日 19:00 期货 / 19:30 股票已剥离)
  ├── GAP-072 权重重算日判定（冻结日复用上次组合）
  ├── 加载精英因子（DuckDB SSOT 优先 → 质量门槛 → 影子池过滤 → 基础名去重）
  ├── 纯外推验证 → ACTIVE_FACTOR_CAP=20 → 聚类/PCA（可选）
  ├── 信号合成（9 种模式：equal/sharpe/ic/elastic_net/ml_ensemble/adaptive/optimizer/bl）
  ├── Regime 自适应权重（family×style + 概率混合 + 置信度缩放 + 不对称平滑）
  ├── 正交化 → 衰减检验（>0.30 剔除）→ 组合构建（粘性/换手惩罚）
  ├── 夏普虚高验证 → 漂移监控 → L3 Verifier 判定
  ├── 归因 → 组合层走航 → 组合级风控（三级预警）
  └── 注入 FDT（current_combo.json + factor_weights.json）+ 触发信号管道
        ▼
反馈闭环（持续）
  ├── 触发条件检查（Live 偏离/数据异常/定期评估/审计失败/因子衰减）
  ├── 归因分析（5 种根因）→ 演化方向调整
  └── 月度迭代效果报告 + 实盘反馈导入（LiveFeedbackImporter）
```

### 9.2 数据获取流程

```
期货日线:
  DUCKDB_CACHE → TDX_LOCAL(17709) → TQ_PYTHON → AKSHARE → SYNTHETIC
    → 字段增强（TQSDKEnhance / IFindSDK / Wind / iFinD 并行）
    → 熔断器（连续 5 次失败 → UNAVAILABLE → 6h 冷却 → 探活恢复）
    → 交叉验证（≥2 源差异 > 0.5% 记录 disagreements.jsonl）
    → pre_settle 零依赖派生 → 换月复权 → 夜盘跳空注入
    → FuturesDataAggregator → 因子引擎

期货分钟: minute_cache → TDX_LOCAL(17709) → TQSDK
期货 tick: tick_cache → TQSDK_TICK
A 股/ETF: TDX_LOCAL(17709) 股票端点 → 腾讯 HTTP API → 合成数据降级
基本面:   MCPBridge 缓存（东财 mx）→ 合成数据降级
A股特有:  AshareSpecialSource / StockFundamentalSource（缓存优先，回填后零网络）
宏观:     EastmoneyMacroSource → MacroFieldAligner（lag_days=30 防未来函数）
期货基本面: AkshareFuturesFundamentalProvider（库存/基差/仓单）
```

### 9.3 因子质检流程

```
因子计算 → 三级评估 → Verifier 判定
                    → 质量评分卡（10 维 A≥40 / B≥30 / C<30，期货 A≥38/B≥28）
                    → HighICScreener（16 项检查 + 5 项一票否决）
                    → FactorAuditor 审计（6 项强制，渐进式 skipped）
                    → 全部通过 → 影子池观察 5 日 → 晋升精英（DuckDB 先写 + JSON 快照）
                    任一项失败 → 记录失败轨迹 + 阻断晋升
```

### 9.4 模拟交易流程（D.1/D.2）

```
FactorSignal → SimulatedPortfolio.apply_signal
  → InterventionController.should_block（人工干预，权限最高）
  → RiskManager.check（5 项风控规则）
  → 目标仓位映射 → _reconcile 撮合（开/加/减/平/反手）
  → OrderBookMatchingEngine 盘口逐档成交（bps 降级）→ SimulatedGateway（限价/部分成交）
  → mark_to_market 逐日盯市 → portfolio_risk_status（组合级三级预警）
  → attribute_factor_returns 因子归因 → LiveFeedbackImporter 落盘
  → SimSQLiteStore 持久化（WAL，启动恢复）
```

---

## 10. 测试体系

### 10.1 测试概况

- **测试文件**: 100 个
- **测试用例**: 5200+ 个（2561 个 `def test_*` × 参数化展开，README 徽章 5193 passing）
- **测试框架**: pytest + pytest-cov
- **测试配置**: `pyproject.toml` `[tool.pytest.ini_options]`（testpaths=["tests"], --cov=fts）
- **分级测试政策**（2026-08-11 修订）: 日常任务只跑受影响的模块/集成测试；全量回归仅在发布前 + 每月底例行巡检

### 10.2 测试目录结构

```
tests/
├── core/                       # 核心契约测试（atomic/contracts/enums）
├── config/                     # 配置测试（weight_recompute）
├── data_sources/               # 数据源测试（aggregator 98/fusion/tdx_local/tqsdk_*/ifind_*/wind/migrate/macro_aligner/roll_calendar/overnight_gap/ashare_special/stock_fundamental）
├── factor_engine/              # 因子引擎测试（核心，~100 文件）
│   ├── expr_dsl/               # DSL 测试（parser/validator/executor/compiler/factory/runtime/seed_analyzer）
│   ├── factor_db/              # 数据库测试
│   ├── operator_evolution/     # 算子演化测试
│   ├── test_evolution_loop.py  # L2 主循环（133+ 用例）
│   ├── test_portfolio_loop.py  # L3 组合（217+ 用例，含 Regime 机构级）
│   ├── test_meta_loop.py       # L1 元循环（93 用例）
│   ├── test_operator_expansion*.py  # 算子扩容（838+ 用例）
│   └── ...
├── live_trade/                 # 模拟交易测试（simulated_portfolio/book_matching/live_trade）
├── monitor/                    # 监控测试（elite_tracker/data_quality_monitor/logic_monitor/prometheus_metrics）
├── risk/                       # 风控测试（risk_manager/portfolio_metrics）
├── scheduler/                  # 调度测试（engine/tasks/watchdog/hotswap/sync_futures_task）
├── scenarios/                  # 场景测试（scenarios/natural_experiments）
├── scripts/                    # 脚本测试（migrate/backfill/archive/verify_doc_consistency 等）
├── store/                      # 存储层测试（storage_registry/state_db）
├── cli/                        # CLI 测试（data_cli）
├── test_e2e.py / test_cli*.py / test_http_server.py / test_llm.py / test_bridge.py
├── test_data*.py / test_duckdb_*.py / test_config_settings.py
└── test_*_factor.py            # MLP/GRU/Transformer 深度模型测试
```

### 10.3 运行测试

```bash
# 全量测试（仅发布前/月度巡检）
python -m pytest tests/ --no-cov --tb=short
# 带覆盖率
python -m pytest tests/ --cov=fts --cov-report=term-missing
# 日常：指定模块（按受影响范围选择）
python -m pytest tests/factor_engine/ -v
python -m pytest tests/data_sources/ -v
python -m pytest tests/live_trade/ -v
python -m pytest tests/store/ -v
python -m pytest tests/factor_engine/ -k "evolution" -v
```

---

## 11. 附录 A: 版本历史

| 版本 | 主要变更 |
|------|---------|
| v2.103.0 | 当前版本（2026-08-12）：GAP-081/082 A 股真实字段回填 + 缓存优先契约；GAP-088 期货宏观注入端闭环；P4 读路径 DuckDB SSOT 优先 JSON 回退；GAP-084/086/087 股票数据层三 P1 关闭；GAP-096 跨品种 A+C 双机制 |
| v2.102.0 | GAP-079 oos_consistency 全量误杀修复 + 数据层收敛（plans/29 P0-P4）+ 期货数据源接入（GAP-083/085/091）累积发布里程碑 |
| v2.101.0 | plans/29 六层存储收敛（P0 注册表 → P1 因子资产入库 → P2 状态入库 → P3 信号 Parquet/冷热归档 → P4 读路径切换）；plans/28 Regime 机构级优化（T1-T10）；模拟交易 D.1/D.2 全阶段；算子库扩容（DSL 132→512，D10-D17 380 算子）；结构化实验日志；成功模式定向演化；GAP-077 结构簇配额；C4 Dask 分布式后端；C8 人审工作台 + 22 算子；C9 30 算子；期货持仓/结算数据源接入（GAP-083/085）；仓单接入（GAP-091） |
| v2.98.x | GAP-071 L2 质检性能（信号缓存）；GAP-072 L3 权重重算日冻结；股票 L3 早间调度 + 信号管道联动 |
| v2.100.x | GAP-074 算子演化多样性修复；L1 感知层样本按市场区分；GAP-068 多频叠加；GAP-069 持仓拥挤度；GAP-075 跨标的稳健性；GAP-076 信号管道截面标准化；GAP-078 TQ 探活进程级重试 |
| v2.56.0 | Regime 自适应双维度（FactorFamily × FactorStyle）+ RegimeSmoother |
| v2.50.0 | 种子评估晋升与演化因子 Verifier 对齐 |
| v2.46.0 | 旧版 Code Wiki 对应版本（2026-08-08） |
| v2.39.0+ | Phase 25 信号桥接（SignalBridge REST 服务 8765） |
| v2.38.0 | Phase 24 ML 模型层（MLSignalModel + SignalModelTrainer）、L3 ml_ensemble 模式 |
| v2.36.0 | P1 因子聚类 + P2 PCA 降维（factor_clustering.py） |
| v2.35.0 | L3 Elastic Net 合成 + ACTIVE_FACTOR_CAP=20 |
| v2.31.0 | TQSDK tick 逐笔数据源 |
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
| 差距管理 | `docs/harness/08-gap-analysis.md` | 技术债务登记（GAP 清单） |
| 晋级计划 | `docs/harness/09-advancement-plan.md` | 晋级里程碑 |
| 存储契约 | `docs/harness/_data/storage_landscape.yaml` | 六层存储域登记（Layer 3 数据） |
| 设计文档 | `docs/harness/design/` | A.1-C.4 + D.1/D.2 + E.1/F.1/F.2 设计文档 |
| 验收测试 | `docs/harness/acceptance/` | 各阶段验收测试 |
| 技术评审 | `docs/harness/tech-review/` | P1/P2 技术评审 |
| 计划文档 | `docs/harness/plans/` | plans/10-32 演进计划（含 plans/26 演化优化 / 28 Regime 机构级 / 29 存储收敛 / **32 股票剥离——股票已独立为 `d:\Programs\fts-stock`**） |
| 工程准则 | `AGENTS.md` / `CLAUDE.md` | 项目工程规范与行为准则 |
