# FTS (Factor Trading System) — Code Wiki

> **版本**: v3.0.0+2 | **最后更新**: 2026-08-20
>
> 本文档基于当前源代码重新分析生成（上一版停留在 v2.105.0+33 / 2026-08-20），是 FTS 项目的代码级参考文档，面向开发者阅读。
> 覆盖：项目整体架构、主要模块职责、关键类与函数说明、依赖关系、项目运行方式。
>
> **⚠️ 工程现状标注（v3.0.0 双系统切分正式发布，plans/57 阶段 0-13 全部完成）**：
> **FTS 系统定位为「期货市场因子生产系统」**——面向全部期货品种（84 品种/17 产业链，coverage_priority P0-P3），
> 专注**因子发现、评估、管理、生命周期与因子信号矩阵输出**；**不再具备交易信号产生与组合权重重算的功能和能力**
> （策略合成职责已正式迁移 Regime-Driven）。股票管线已通过 plans/32 剥离为独立项目 `d:\Programs\fts-stock`（v0.0.1）。
> 文中保留的 A 股/ETF 相关描述仅作历史与架构参考，不属于期货主系统的运行路径。
>
> **⚠️ L3 组合侧已正式退役（plans/57 §4.1/§5.3，v3.0.0 登记完成，v2.105.0+33 步骤 12 起）**：
> - **组合权重重算（L3 Portfolio Loop）**：`fts portfolio run` / `l3_portfolio_loop` 任务 / `PortfolioLoop` 已退役，**不再构成 FTS 能力**
> - **交易信号产生**：`futures_signal_pipeline` 信号管道（组合信号/交易建议/信号报告）已退役
> - **退役登记**：`retired_l3.py` 全 **35 项**登记（futures_signal_pipeline 组合侧 11 + portfolio_loop 策略侧 21 + 整体迁移模块 3），
>   存量调用点经 `warn_if_retired` 发出 DeprecationWarning；物理删除为后续独立里程碑（待授权 + 全量回归）
> - **FTS 保留能力**：因子生产（L1 知识注入/L2 演化/评审质检/生命周期）+ **因子信号矩阵输出**
>   （`l3_signal_service` → `l3_signal_store.duckdb`，信号契约 v1 经 F.3 冻结，供 Regime-Driven 消费）+ 正交化（`orthogonalize_factors`）
>
> **⚠️ 全局默认市场（v3.0.0+1 反转，v3.0.0+2 完成 YAML 同步）**：`settings.py` 代码默认与 `config/settings.yaml` 均已
> 反转回 `futures`（FTS 因子生产默认面向全部期货 84 品种/17 产业链），`FTS_DEFAULT_MARKET` 环境变量可覆盖。
> 上一版 v2.105.0+33 的文档曾如实标注 settings.yaml 残留 `default_market: "energy"` 的配置漂移，已于 v3.0.0+2 修复对齐。
>
> **v2.104.0 → v3.0.0 期间重大演进（本版 Wiki 相比 v2.103.0 版的新增重点）**：
> - **双系统切分（plans/57，v3.0.0）**：FTS 角色=因子生产系统；L3 组合侧退役登记 35 项；信号契约 v1（F.3）；
>   全期货覆盖规划（coverage_priority P0-P3）；存量因子集中重审管道（review_legacy_factors.py 分族 FDR-BH）
> - **全局默认市场反转 + 定时任务全期货重建（v3.0.0+1）**：TRAE Schedule 删除 4 个 energy 专属任务、新建 5 个全期货任务（8 Active）
> - **能化产业链独立工作流（GAP-121）**：12 化工品种训练链 + 8 化工链外盲测池，独立因子库 `factor_catalog_energy.duckdb`
> - **子链张量化（plans/47-49）**：子链适用性画像、子链差异化权重调制、因子×子链单元粒度退化检测、质量矩阵落库
> - **L2 评审+质检统一管道（方案 A）**：energy_qa_review.py 合并定期评审与定期质检
> - **L3 信号矩阵服务（plans/40/52）**：l3_signal_service.py 信号矩阵 B/D 层收敛 + 增量窗口追加（GAP-139）
> - **CTA 手册 WorkFlow 端到端工作流（v2.104.0+25）**：fts/workflow/ 11 阶段 + 质检闭环，Web UI 一键驱动
> - **权威数据源 QuantData（v2.105.0+32，GAP-156）**：字段权威矩阵校验，K 线主路径首级
> - **L1 知识注入增强（plans/41）+ 知识源自动发现（plans/46）**：WebSearch 动态因子源 + 批量采集/深读/注册表闭环

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
   - 4.7 [模拟/实盘交易体系 `fts.live_trade`](#47-模拟实盘交易体系-ftslive_trade)
   - 4.8 [风控层 `fts.risk`](#48-风控层-ftsrisk)
   - 4.9 [ML 模型层 `fts.ml`](#49-ml-模型层-ftsml)
   - 4.10 [信号桥接层 `fts.bridge`](#410-信号桥接层-ftsbridge)
   - 4.11 [监控层 `fts.monitor`](#411-监控层-ftsmonitor)
   - 4.12 [调度层 `fts.scheduler`](#412-调度层-ftsscheduler)
   - 4.13 [工作流层 `fts.workflow`](#413-工作流层-ftsworkflow)
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

**FTS (Factor Trading System)** 是一个 AI 原生的量化因子智能系统，通过三层进化循环实现因子的自动化发现、评估、组合与演化，输出标准化的因子信号矩阵（信号契约 v1，schema_version/factor_status/factor_scope），下游策略合成与交易执行由 Regime-Driven 系统（及 FDT 等）负责。

### 核心定位

```
数据源（QuantData 唯一权威 K 线源 / DuckDB 缓存 / Wind / iFinD 字段增强）
    ↓
FTS（因子生产系统 → 因子信号矩阵）
    ├── L0 Program（人类设定层：program.md）
    ├── L1 Meta-Loop（每日市场感知 + 知识补给 + Bootstrapping）
    ├── L2 Evolution Loop（因子演化 + 三级评估 + 评审质检 + 审计）
    └── L3（组合侧已退役 plans/57 v3.0.0）→ 仅保留信号矩阵/正交化基础设施
    ↓ 信号契约 v1（schema_version/factor_status/factor_scope，design/F.3 冻结）
下游策略合成系统（Regime-Driven：strategy_synthesis/combo_verifier/money_management/crowding_gate）
```

### 核心能力

| 能力 | 说明 |
|------|------|
| **因子生产循环** | L1 Meta-Loop（每日知识补给）+ L2 Evolution Loop（工作日/周末演化）+ 评审质检 + 信号矩阵产出（工作日 20:00）。**L3 组合侧已正式退役（plans/57 v3.0.0）：策略合成迁移 Regime-Driven，FTS 仅保留信号矩阵/正交化基础设施**（`retired_l3.py` 35 项登记 + `warn_if_retired` 告警） |
| **信号契约 v1（F.3）** | `l3_signal_meta` 追加 schema_version/factor_status/factor_scope 三列（幂等迁移），决策/训练双模式隔离，增量幂等 + 新鲜度校验 + 降级熔断，RD 校验不兼容即降级 |
| **全期货覆盖规划（plans/57）** | `config/futures_universe.yaml` coverage_priority P0-P3 逐链推进（P0 能源化工 24 / P1 黑色系+有色 17 / P2 农产品群+贵金属 27 / P3 其余），84 品种/17 产业链 |
| **存量因子集中重审（v3.0.0）** | `scripts/review_legacy_factors.py` 清点分层（active/shadow/degraded/deleted）→ 分族 FDR-BH 校正（α=0.05）→ audit 分层抽样（promote 100%/observe 30%/retire 10%），`FTS_REVIEW_LEGACY_APPLY=1` 才落库 |
| **能化产业链独立闭环（GAP-121）** | 12 化工品种训练链（能源3 SC/FU/BU + 聚酯3 PX/TA/PF + 油化工3 L/PP/PG + 煤化工3 MA/UR/SA）+ 8 化工链外盲测池，独立因子库/精英目录/报告；v3.0.0+1 起 energy 专属定时任务删除，energy 因子库作为候选池保留（`fts evolution run --chain energy` 仍可手动调用） |
| **因子种子库** | 期货 YAML 种子（seeds/futures/ + seeds/energy/）+ 内置种子（wq101/qlib158/jq/gtja191/fundamental，合规不包含第三方专有实现）+ L1 注入（l1_injected/）+ 外部因子常态化导入（import_external_factors_job 月度） |
| **FTS-Expr DSL** | 算子表达式语言（L0 字段 + L1 时序 + L2 横截面 + L3 逻辑 + L4 组合 + L5 领域算子，约 500+ 算子），双注册表一致性校验，PIT 静态审计防未来函数 |
| **进化搜索多引擎** | GP 遗传规划 + OperatorEvolution + 符号回归 + 批量挖掘漏斗（GAP-I201）+ 深度因子（GRU/Transformer 纯 numpy）+ LLM 宏观演化（四模式：operator/code/hybrid/batch） |
| **六层存储架构** | L1 配置(YAML) → L2 行情库(DuckDB+Parquet 归档) → L3 因子资产库(DuckDB SSOT，期货/能源双库) → L4 运行状态库(SQLite WAL) → L5 信号缓存(Parquet+checksum) → L6 日志血缘(JSONL) |
| **多源数据融合** | K 线唯一数据源 QuantData（v3.0.0+1；DUCKDB_CACHE 读取缓存 → QUANTDATA → SYNTHETIC 兜底；天勤 TQ_PYTHON/TQSDK、TDX_LOCAL 实时、AKShare 已从默认聚合器移除）+ 字段增强层（iFinDSDK/Wind/iFinD）+ 熔断器（每源连续 5 次失败 → 6h 冷却）+ 交叉验证（0.5% 分歧记录）+ 5 种融合策略 |
| **权威数据源（GAP-156）** | QuantData（D:\QuantData 本机统一金融数据仓库），字段权威矩阵校验（L0 权威 open/high/low/close/volume/hold、L1 降级、L2 缺失禁依赖） |
| **因子评审质检体系** | 10 维 0-50 分质量评分卡 + 6 项强制审计 + 7 状态机（DRAFT/PENDING_QA/CORE/CANDIDATE/OBSERVATION/SUSPENDED/RETIRED）+ Q1-Q10 入库前质检 + M1-M5 月度复检 + F1-F6 季度复检 + D1-D4 半年度复检 + 5 红线退役 |
| **子链张量化（plans/47-49）** | 子链画像（t 检验护栏）、子链差异化权重调制矩阵、因子×子链单元粒度退化检测（scope_shrink 传导调制矩阵）、质量矩阵落库 |
| **Market Regime** | 四级检测链（多周期 HMM → MSM → 单周期 HMM → 规则），5 种市场状态 + 机构级优化（概率混合/熵标定/BIC 选态/数据驱动倍率/样本外验证）+ 分层方向 Gate + Beta 层 + 拥挤度门控 |
| **回测流水线** | 4 阶段管线（DataLoad → FactorCompute → Performance → Report），日频+分钟频率，成本敏感性 + 走航验证 |
| **模拟交易体系** | 模拟仓（开/加/减/平/反手、盯市、归因）+ tick 盘口撮合（逐档消耗/部分成交/限价单/集合竞价）+ 组合级风控三级预警 + 人工干预 + SQLite 持久化 + 回放/纸面双引擎 |
| **CTA 手册 WorkFlow** | 11 阶段 + 质检闭环节点流（数据基建→因子库挖掘→IC/IR→Regime→合成→调仓→风控→样本外→仿真→爬坡→质检），Web UI 一键驱动真实执行 |
| **反馈闭环** | 6 种反馈事件 + 5 种根因归因 + 演化方向自适应调整 + 月度迭代报告 + 实盘 IC 导入 |
| **Prometheus 监控** | 数据源/因子/系统三维指标 + HTTP 端点 + Web UI 仪表盘（9100）+ K8s 部署 |
| **信号桥接** | JSON / Redis / REST 三种协议信号发布，VNPY 对接 |

### 技术栈

- **语言**: Python 3.10+（项目内 Python 路径 `C:\Program Files\Python312\python.exe`）
- **核心依赖**: numpy, pandas, scipy, pyyaml, shap, python-dotenv, duckdb, **numba==0.66.0 + llvmlite==0.48.0（锁定）**
- **可选依赖**: optuna（演化）、openai/anthropic（LLM）、akshare（数据）、scikit-learn（组合）、lightgbm/xgboost（ML）、redis（桥接）、hmmlearn/statsmodels（Regime）、fastapi/uvicorn（监控）、distributed（分布式）
- **数据存储**: DuckDB（`data/fts_history.duckdb` 行情库 / `data/factor_catalog_{futures,energy}.duckdb` 因子资产库 / `data/l3_signal_store.duckdb` 信号矩阵库）+ Parquet（信号缓存/冷归档/期限结构）+ SQLite WAL（`data/state.db` 状态库 / `data/workflow.db` 工作流 / `data/sim_portfolio.db` 模拟仓）
- **调度**: APScheduler（可选，`FTS_INTERNAL_SCHEDULER_ENABLED=1` 启用；默认以 TRAE Schedule 为唯一调度源，v3.0.0+1 起 8 Active 全期货任务）+ ProcessWatchdog 进程守护
- **监控**: 纯标准库 HTTP 仪表盘 + Prometheus 端点（`/metrics`）
- **包管理**: setuptools，`pyproject.toml` 定义元数据，`fts` 命令入口注册

---

## 2. 系统架构

### 2.1 三层循环架构

```
┌─────────────────────────────────────────────────────────────────────┐
│               L0 Program（人类唯一输入接口）                           │
│  program.md：市场制度/因子偏好/避让/L0 熔断确认/预算约束                │
└─────────────────────────────────┬───────────────────────────────────┘
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    L1 Meta-Loop（每日 00:00）                         │
│  agentic 市场感知 → debate 分析 → Bootstrapping 三源候选              │
│  （提取器/LLM/模板）→ L1 Verifier 判定 → 注入 factor_pool.json         │
│  能化链：按四子链分批 + 实时链知识面板注入（GAP-121 + plans/41）        │
│  L1→L2 漏斗统计（l1_l2_funnel：injected/consumed/promoted）           │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ 种子候选（l1_injected/）
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│          L2 Evolution Loop（工作日 04:00 ≈10 代 / 周六 ≈50 代）        │
│  DataQualityMonitor 数据校验 → State 加载 → 熔断预检查                 │
│  循环（每代）:                                                       │
│    UCT 父因子选择 → 演化分派（macro/GP/operator/deep/transformer）    │
│    → 生成端去重（GAP-135）→ 快速预筛 → 候选准入链                     │
│    （评估→审计→质检→晋升）→ 经验链记录 → 状态持久化                    │
│  可选：批量挖掘漏斗（batch_mining）/ 种子评估晋升（run_seed_stage）     │
│  评审质检：energy_qa_review 统一管道（准入重审→退化检测→生命周期收口）  │
│  子链评估：subchain_eval 批量子链质量矩阵落库（周日 09:00）             │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ 精英因子（futures_elite|energy_elite）
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│   L3（组合侧已正式退役 plans/57 §4.1/§5.3，v3.0.0 登记完成）           │
│  退役：信号合成/组合校验/权重学习/资金分配/拥挤度调制 → 迁 Regime-Driven│
│  （retired_l3.py 35 项 + warn_if_retired 告警，物理删除待授权）        │
│  保留：信号矩阵（l3_signal_service，信号契约 v1）/ 正交化              │
│  （orthogonalize_factors）                                           │
│  输出：因子信号矩阵（l3_signal_store.duckdb，schema_version/          │
│        factor_status/factor_scope 三列，供 RD 消费）                  │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 六层存储架构（plans/29 SSOT 单一事实源）

```
L1 配置层      seeds/*.yaml + config/ + docs/harness/_data/*.yaml   （YAML）
L2 行情库      data/fts_history.duckdb：kline_cache / contract_kline /
               minute_cache / tick_cache / edb_cache / option_chain_cache
               （追加式 + 按年冷热归档 data/archive/*.parquet）
L3 因子资产库  data/factor_catalog_futures.duckdb + factor_catalog_energy.duckdb
               （DuckDB 权威存储，JSON 只读快照；能源链零耦合专属库）
L4 运行状态库  data/state.db（SQLite WAL）：state_kv 当前态 + state_history 历史回放
               （E.3 S2 自 DuckDB 迁移，多读单写不互斥）
L5 信号缓存    memory/cache/factor_signals/（Parquet + checksum）+ l3_signal_store.duckdb
L6 日志血缘    JSONL 保留追加 + 摘要入库

核心约束：fts/store/registry.py StorageRegistry（storage_landscape.yaml 契约）为唯一
读写入口契约；新增数据域必须先登记后落库；DuckDB 写连接一律短生命周期
（fts/store/duckdb_lock.py 跨进程 filelock 互斥，E.4 S1）；禁止同类数据双写漂移。
```

### 2.3 Market Regime 检测体系（`regime.py` 系列）

```
RegimeAwareSelector.detect() 四级检测链（置信度 gate ≥ 0.3）:
  1. MultiHorizonHMMDetector（主，默认）— 多周期 [63,126,252] 加权投票，输出全制度概率分布
  2. MSMRegimeDetector（默认关）        — statsmodels MarkovRegression
  3. HMMRegimeDetector（次）            — 单周期 GaussianHMM（BIC 选状态数）
  4. _detect_by_rule（规则回退）        — 多周期趋势投票 + ADX + EWMA 波动率
  兜底：oscillate / confidence 0.5

输出 5 种状态：bull / bear / oscillate / high_vol / low_vol

机构级优化（plans/28）：
  - regime_probs 概率混合（probability_mix，无 probs 回退硬查表）
  - RegimeConfidenceCalibrator 熵标定 → exposure_scale 置信度仓位缩放
  - RegimeSmoother 不对称切换（de_risk_alpha 快速降权 / re_risk_alpha 缓慢加仓）
  - BIC 状态数选择（select_n_states 防翻转）+ fit_standardizer 仅 fit 训练段防数据窥探
  - RegimeMultiplierEstimator 数据驱动倍率
  - 子链分层方向 Gate（regime_gate，plans/48）：因子 subchain_scope 按子链制度路由
  - Beta 层（regime_beta_layer，plans/55）+ 拥挤度门控（regime_crowding，plans/56）
  - validate_regime_predictive_power Kruskal-Wallis 样本外有效性验证
```

### 2.4 数据流架构

```
期货日线 K 线主路径（v3.0.0+1 起唯一数据源 QuantData，FuturesDataAggregator 统一入口）:
  DUCKDB_CACHE(读取缓存) → QUANTDATA(唯一权威源) → SYNTHETIC(测试/离线兜底)
                    ↓
             字段增强层（并行，不阻断主路径）:
               IFindSDKSource / Wind / iFinD（settle/oi_change/pre_settle/hold）
               QuantData 权威字段矩阵（L0/L1/L2 分级约束；hold=L0，oi_change 差分自算）
                    ↓
              熔断器（每源连续 5 次失败 → UNAVAILABLE → 6h 冷却 → 探活恢复）
                    ↓
              交叉验证（≥2 源同日 close 差异 > 0.5% 记录 data_source_disagreements.jsonl）
                    ↓
              换月复权（RollCalendar 比率法后复权）+ 夜盘跳空注入（OvernightGap）
              + 断K/跳空/持仓量突变标记（TradingCalendar G8）
                    ↓
              因子引擎（三层循环）

期货分钟路径:  minute_cache → TDX_LOCAL(17709, 5m)（显式扩展场景；天勤 TQSDK 已移除）
期货 tick 路径: 默认不注册（因子生命周期管理不需要；需显式传 TQSDKTickSource）
宏观字段注入:  MacroFieldAligner（东财+中债登默认源，发布滞后防未来函数）
基本面字段:    AkshareFuturesFundamentalProvider（库存/基差/仓单）
期限结构:      sync_term_structure_fields（contract_kline 截面 → Parquet）
```

---

## 3. 目录结构

```
fts/                          # 核心源码（约 200 个 Python 文件）
├── __init__.py               # 版本号动态读取（pyproject.toml 单一真源）+ 自动加载 .env
├── cli.py                    # CLI 统一入口（15+ 命令组，全部带 trace_id）
├── llm.py                    # LLM 客户端（OpenAI/Anthropic/Mock 三后端）
├── data.py                   # 数据层统一入口（FTSDataProvider）
├── data_futures.py           # 期货数据核心（DuckDB 持久化/多源降级/换月复权/品种池）
├── data_futures_fundamental.py     # 期货基本面提供者（库存/基差/仓单）
├── data_futures_fundamental_sync.py# 基本面每日同步
├── data_futures_term_structure.py  # 期限结构每日同步
├── core/                     # 核心契约（contracts 转发 + enums + atomic 原子文件）
├── config/                   # 配置系统（settings.py + 质量卡配置 + 字段消费字典）
├── factor_engine/            # 因子引擎（L1/L2/L3 + 审计 + 评分卡 + 子链 + DSL + 提取器）
│   ├── contracts.py          # 因子引擎全部 TypedDict 契约（30+ 项）
│   ├── evolution_loop.py     # L2 演化主循环（9 协作类组合式重构）
│   ├── meta_loop.py          # L1 Meta-Loop
│   ├── portfolio_loop.py     # L3 组合构建（276KB，组合侧已退役登记 plans/57，仅正交化保留）
│   ├── retired_l3.py         # L3 组合侧退役登记（35 项 + warn_if_retired 告警，v3.0.0）
│   ├── l3_signal_service.py  # L3 信号矩阵 B/D 层（plans/40/52 + 信号契约 v1）
│   ├── energy_qa_review.py   # 能化链 L2 评审质检统一管道（方案 A）
│   ├── l1_l2_funnel.py       # L1→L2 闭环漏斗统计（plans/44）
│   ├── subchain_profile.py   # 子链适用性画像（plans/47）
│   ├── subchain_weight.py    # 子链差异化权重调制（plans/47）
│   ├── subchain_lifecycle.py # 因子×子链单元粒度退化检测（plans/49）
│   ├── subchain_eval.py      # 批量子链质量评估
│   ├── audit.py              # 6 类因子强制审计
│   ├── verifier.py           # Verifier 协议（锁定的评估机制）
│   ├── factor_quality_card.py# 50 分制质量评分卡（10 维）
│   ├── causal_validator.py   # 因果结构审查
│   ├── backtest_pipeline.py  # 端到端回测流水线
│   ├── factor_db/            # 因子资产库 DuckDB（repository/schema/lineage）
│   ├── qa/                   # 质检体系（Q1-Q10/M1-M5/F1-F6/D1-D4/退役/状态机）
│   ├── expr_dsl/             # 因子表达式 DSL（parser/validator/compiler/runtime/registry）
│   ├── extractors/           # L1 知识提取器（YAML/研报/论文/WebSearch/批量/动态源）
│   ├── seed_data/            # 种子因子库（wq101/qlib158/jq/gtja191/fundamental）
│   ├── evolution_*.py        # 演化子模块（uct/prefilter/promote/audit/trace/review/...）
│   └── ...                   # 其余引擎模块（regime 系列/mhf/panel_vector/numba_kernels 等）
├── data_sources/             # 多源数据适配器（aggregator + 12 数据源 + 复权/日历/融合）
├── store/                    # 存储注册表 + 跨进程写锁 + 状态库（SQLite WAL）
├── scheduler/                # APScheduler 定时调度（tasks/jobs/engine/watchdog）
├── monitor/                  # 健康监控（数据/逻辑/实盘/精英跟踪/指标/HTTP/重审）
├── live_trade/               # 模拟/实盘链路（订单/止损/干预/网关/撮合/模拟仓/SQLite）
├── risk/                     # 风控（5 项规则 + 组合级三级预警 + 模拟适配器）
├── ml/                       # ML 模型层（LightGBM/XGBoost/Ensemble/MLP/GRU/Transformer）
├── bridge/                   # 信号桥接（JSON/Redis/REST 三协议）
└── workflow/                 # CTA 手册 WorkFlow（stages/executor/store）

config/                       # 项目配置（settings.yaml/futures_universe.yaml/extractors.yaml）
seeds/                        # YAML 种子因子（futures/ + energy/）
scripts/                      # 工具脚本（180+ 个：同步/迁移/验证/回测/诊断/重审）
tests/                        # 分场景测试（core/data_sources/factor_engine/config/cli/...）
web/workflow_ui/              # WorkFlow React SPA（构建产物 dist）
docs/                         # 项目文档（harness 工程规范 + FTS_manual 手册）
memory/                       # 运行时持久化（自动创建：logs/cache/reports）
data/                         # 运行时数据库（DuckDB/SQLite/Parquet，自动创建）
```

---

## 4. 核心模块详解

### 4.1 核心契约层 `fts.core`

**职责**：FTS 数据契约的统一导入入口（re-export 层）+ FTS 特有枚举 + 原子文件读写。HARNESS §契约优先：字段集合锁定，禁止任意加减。

**关键契约（TypedDict）**：

| 契约名 | 说明 |
|---|---|
| `OHLCVBase` | 股票/期货两市场 K 线公共字段基契约 |
| `FuturesOHLCV` | 期货单条 K 线契约（必填 8 字段 + hold/settle/pre_settle/oi_change/vwap 可选） |
| `FusedOHLCV` | 多源融合 OHLCV 通用兼容契约 |
| `FusionMeta` / `FusionReport` | 多源融合元数据 / 融合报告（`fts data fuse` 输出） |
| `FuturesDataLineage` | 期货数据同步血缘追踪契约 |

**关键枚举（`enums.py`）**：`EvolutionStage`（L0/L1/L2/L3）、`FactorPriority`、`CandidateStatus`、`DataSource`（11 源，含 QUANTDATA）、`FusionStrategy`（MEDIAN/MEAN/WEIGHTED/HIERARCHICAL/TRIMMED_MEAN）。

**关键函数（`atomic.py`）**：
- `atomic_write(path, data)` / `atomic_read(path, default)`：临时文件 + `os.replace` 原子读写
- `atomic_write_state(path, state, backup_count=3)`：写状态文件并保留轮转备份

**依赖**：`fts/factor_engine/contracts.py`（因子引擎全部契约）、标准库。

### 4.2 配置系统 `fts.config`

**职责**：FTS 全局配置加载与管理。加载优先级：环境变量（`FTS_*`）> YAML 配置文件 > 模块默认值。覆盖路径、数据、LLM、演化、L1/L2/L3、DuckDB 并发、Verifier、回测仿真、风控等 80+ 配置项。

**关键类**：
- `FTSConfig`（dataclass）：全局配置模型。核心方法 `get_elite_dir(market="futures")`（market="energy" 返回能源产业链专属目录）；`default_market` 字段代码默认 `futures`（v3.0.0+1 反转，**注意 settings.yaml 残留 `energy` 覆盖，见 §8.1**）；字段均从 `os.getenv("FTS_*")` 读取
- `DimensionWeights` / `GradeThresholds` / `FactorQualityCardFullConfig`：质量评分卡配置（[factor_quality_card_config.py](file:///d:/Programs/factor_system/fts/config/factor_quality_card_config.py)，10 维权重/分级阈值/各维度映射）
- `FuturesFieldConsumptionConfig`（Pydantic）：期货字段消费字典 SSOT（[futures_field_consumption.py](file:///d:/Programs/factor_system/fts/config/futures_field_consumption.py)，kline 17 字段 / fundamental 9 字段 / term_structure 4 字段）

**关键函数**：
- `get_config()`：全局配置单例（延迟初始化）
- `load_config(config_path)`：加载 YAML → 应用环境变量覆盖
- `is_weight_recompute_day(cfg, today)`：~~L3 权重重算日判定（GAP-072，已随 L3 退役 v3.0.0）~~
- `validate_evolution_mode(mode)`：演化模式校验 ∈ {operator, operator_first, code, hybrid, batch}
- `get_quality_card_config()` / `create_config(...)` / `get_futures_config()` 等预设

**依赖**：yaml、os、dataclasses；`config/settings.yaml` 为实际生效值，`config/futures_universe.yaml` 为品种池/产业链 SSOT。

### 4.3 因子引擎 `fts.factor_engine`

因子引擎是 FTS 核心，约 120 个 Python 文件。以下按子系统详解。

#### 4.3.1 L2 演化主循环 `evolution_loop.py`

**职责**（v1.9.0）：`seed_pool.fetch() → for generation in 1..MAX_GEN`，每代经历 父因子 UCT 选择 → 演化分派（macro/GP/operator/deep/transformer）→ 运行时校验 → 生成端去重 → 快速预筛 → 候选准入链（评估→审计→质检→晋升）→ 经验链记录 → 状态持久化。34 计划 C 阶段已把 9 个领域 Mixin 重构为协作类（继承链清零），主类组合持有 9 协作类实例。

**关键类**：
- `EvolutionLoop`：主循环。核心方法：`run(max_generation)`（逐代演化，`_evolve_one`→校验→`_process_candidate`）、`run_seed_stage(trace_id, state, elite_ids)`（种子评估晋升独立入口）、`run_batch_stage`（batch 批量挖掘一代漏斗，熔断隔离）、`_is_generated_duplicate` / `_build_seen_expression_norms`（生成端同表达式去重 GAP-135）
- `EvolutionRunResult`（dataclass）：运行结果（run_id/trace_id/generations_completed/status/early_stopped）

**熔断机制**：token 超 2x、连续 3 代 IC<0.01、失败率>90%（默认关闭，`FTS_EVOLUTION_CB_FAILURE_RATE`）。

**9 个协作类**（详见 4.3.11 演化子模块表）。

#### 4.3.2 L1 Meta-Loop `meta_loop.py`

**职责**（v1.1.0）：5 步流程：Step1 agentic 感知（FTSDataProvider 新闻与市场快照）→ Step2 debate_round 分析（识别薄弱维度）→ Step2.5 扫描已注入因子（冷启动去重）→ Step2.75 拒绝候选复活（plans/44 C2）→ Step3 Bootstrapping（提取器→LLM→内置模板三优先）→ Step4 L1 Verifier 校验+注入（软失败 LLM 重写 / 硬失败落盘 rejected）→ Step5 注入 factor_pool.json + l1_injected/。energy 市场按四子链分批 + 实时链知识面板注入（GAP-121 + plans/41）。

**关键类**：
- `L1Verifier`：`check(candidate, seed_pool)` 判定 4 维度（economic_logic ≥2/4、is_executable、not_duplicate、narrative 长度）
- `MetaStateManager`：L1 状态管理器（state.duckdb 的 meta_loop/state 键）
- `FactorPoolManager`：factor_pool.json 管理器（`add_entry` 按 factor_id 去重）
- `DebateQualityAnalyzer`：`analyze_latest_debate()` 识别 bullish_weak/bearish_weak/insufficient_rounds/no_debate
- `BootstrappingChain`：`bootstrap(market_snapshot, debate_gaps, ...)` 编译验证+自动修复+语义去重；energy 市场按四子链分批
- `MetaLoop`：`run(max_bootstraps)` 五步主流程 + `_inject_chain_knowledge`（能源链专属知识注入）

#### 4.3.3 L3 Portfolio Loop `portfolio_loop.py`（276KB 大文件）

> **⚠️ 退役状态（plans/57 §4.1/§5.3，v3.0.0 登记完成）**：本文件**策略侧函数已正式退役登记**（21 项，列于 `retired_l3.py`）——
> `synthesize_signals` / `_compute_elastic_net_weights` / `_compute_ml_ensemble_weights` / `_synthesize_bl_weights` /
> `regime_adaptive_weight_adjustment` / `build_combo` / `_cap_safety_valve` / `_validate_combo_sharpe` /
> `_run_sharpe_randomization_test` / `decay_test` / `apply_turnover_penalty` / `_apply_sticky_constraints` /
> `_compute_subchain_exposure` / `_merge_gate_scale_into_modulation` / `_greedy_select_by_correlation` /
> `_dedup_factors_by_chain` / `_dedup_factors_by_chain_cluster` / `_dedup_within_chain` / `_filter_by_quality_gate` /
> `_filter_shadow_pending` / `_filter_review_approved`，策略合成职责迁移 Regime-Driven。
> **保留**：`orthogonalize_factors`（正交化，FTS 保留基础设施）。代码处于登记兼容期，未物理删除。

**职责**（历史定位，v1.2.0，仅供追溯）：Step 0.5 加载面板（energy 收缩至能化 20 品种）→ Step 1 加载 elite 因子（DuckDB 优先，仅 approved 放行）→ Step 1.5 纯外推验证（OOS IC 衰减标记降级）→ Step 1.8 因子聚类（energy 关闭全局 P1，去冗余下沉 Step 1.8b 链内聚类）→ Step 1.9 P2 PCA 降维 → Step 2 信号合成（八种模式）→ Step 2.5 Regime 自适应权重（子链 Gate/Beta 层/拥挤度门控/条件化权重）→ Step 3 正交化 → Step 4 衰减检验 → Step 5 组合构建（粘性约束+换手惩罚+置信度缩放）→ Step 5.5 漂移监控 → Step 6 L3 Verifier → Step 7 注入 FDT + 报告。**上述策略侧流程整体退役**，仅保留 Step 3 正交化。

**关键类**：
- `L3Verifier`：`check(combo)` 判定 6 维度（组合夏普 2.0-3.5、最大相关 <0.3、换手 <50%/月、衰减率、最少因子数 ≥3）——随组合校验职责退役（RD `combo_verifier` 承接）
- `PortfolioStateManager` / `PortfolioManager`：状态持久化 / 组合文件管理
- `DriftMonitor`：组合漂移监控（GAP-F13）
- `PortfolioLoop`：主循环（`run(...)`，登记兼容期仍可运行，调用将告警）

**关键函数（保留项）**：
- `orthogonalize_factors(...)`：因子正交化（分层/相关矩阵/代码哈希三模式）——**FTS 保留**

**关键函数（退役项，仅追溯）**：见上方 21 项清单。

#### 4.3.4 L3 信号矩阵服务 `l3_signal_service.py`（plans/40 B/D 层 + plans/52 增量窗口 + 信号契约 v1）

> **✅ 保留资产（plans/57 §2.2）**：信号矩阵构建/增量是 FTS 在 L3 退役后**保留的基础设施**，
> 经**信号契约 v1**（`design/F.3` 冻结：schema_version/factor_status/factor_scope）输出给 Regime-Driven 消费。

**职责**：把 L3 组合重算中最重的"信号重复重算"收敛为单一 2D/3D 信号矩阵服务：B 层构建统一 (n_dates, n_symbols, n_factors) 信号矩阵；D 层持久化到 `data/l3_signal_store.duckdb`（按 (code_hash, params_hash) 双哈希增量判定，GAP-139 增量窗口追加）。

**信号契约 v1（F.3，v3.0.0）**：`l3_signal_meta` 表幂等追加三列——`schema_version`（INTEGER，契约版本，RD 校验不兼容即降级熔断）、`factor_status`（VARCHAR，factor_id → active/degraded/shadow/retired 状态传播）、`factor_scope`（JSON，factor_id → {subchain_scope, subchain_specific} 作用域画像）；配套决策/训练双模式隔离 + 增量幂等（dates_digest）+ 新鲜度校验。

**关键类/函数**：
- `SignalMatrixBundle`（dataclass）：信号矩阵构建结果（signal_matrix/forward_returns/dates/symbols/factor_ids/forward_days）
- `align_signal_to_dates(sig, df, common_dates)`：向量化日期对齐（hash 查找 O(n)）
- `build_signal_matrix(panel, valid_factors, factor_codes, common_dates, forward_days, signal_cache)`：B 层核心
- `persist_signal_matrix(...)`：D 层写入（短写连接 + duckdb_write_lock + GAP-150 写路径契约校验；接受 factor_status_map/factor_scope_map/schema_version 契约参数）
- `load_signal_matrix(...)` / `load_signal_meta(...)`：D 层只读读取（含 code_hash/params_hash/schema_version/factor_status/factor_scope/dates_digest）
- `backfill_signal_matrix(...)`：增量窗口追加回填（`_W_RECALL=500` 回退段 + `dates_digest` 前缀判定 + `_verify_append` 验证兜底）
- `load_or_build_signal_matrix(...)`：增量判定入口（前缀一致 + 有增量 → 增量；否则全量）

#### 4.3.5 子链体系（plans/47-49）

| 文件 | 关键类/函数 | 职责 |
|---|---|---|
| [subchain_profile.py](file:///d:/Programs/factor_system/fts/factor_engine/subchain_profile.py) | `SubchainProfileConfig`（min_symbols=3/min_t_stat=2.0/min_chain_ic=0.02）、`_chain_effective(ics, cfg)`、`compute_subchain_profile(...)` | 子链适用性画像：单样本 t 检验三门槛 AND（n≥3、\|t\|≥2.0、\|mean_ic\|≥0.02），保守性设计防误伤 |
| [subchain_weight.py](file:///d:/Programs/factor_system/fts/factor_engine/subchain_weight.py) | `build_subchain_weights(factors, chain_symbols, config)`、`apply_subchain_modulation(signal_matrix, modulation, symbol_chain, factors)`、`compute_chain_exposure(...)` | L3 子链差异化权重调制矩阵 m[factor][子链]：effective 链 1.0，非 effective 按 zero/soft 降权 |
| [subchain_lifecycle.py](file:///d:/Programs/factor_system/fts/factor_engine/subchain_lifecycle.py) | `SubchainLifecycleConfig`（enabled 默认关）、`compute_subchain_degradation(rows, cfg)`、`scope_without_chains(...)` | 因子×子链单元粒度退化检测：全部有效链衰减→degrade、部分链→scope_shrink（剔除失效链传导 47 调制矩阵） |
| [subchain_eval.py](file:///d:/Programs/factor_system/fts/factor_engine/subchain_eval.py) | `SubchainEvalRunner.run()` | 批量子链质量评估：全部 active 因子逐品种 IC → 画像 → 落库 subchain_factor_quality 质量矩阵（UPSERT 幂等） |

**质量矩阵存储**：`SubchainQualityRepository`（factor_db/repository.py）管理 `subchain_factor_quality` 表。

#### 4.3.6 能化链 L2 评审质检统一管道 `energy_qa_review.py`（方案 A）

**职责**：合并定期评审与 energy 链定期质检为单一管道：`[0] 公共面板 → [1] 准入重审 → [2] 退化检测落库 → [3] 生命周期收口（含冷却期回归）→ [4] Inspector 血缘 → [4.5] invalid_when 命中检查 → [5] 统一报告`。原则"宁严勿松"：任一退化信号命中即降级。

> **v3.0.0+1 定位调整**：能化链专属定时任务已删除，energy 因子库作为**候选池**保留；该管道经 `l2_energy_qa_review_job` 手动调用（`FTS_ENERGY_QA_REVIEW_APPLY=1` 正式落库），全期货评审质检走 `l2_review_job`/`factor_level_monitor_job`。

**关键类**：
- `EnergyQaReviewConfig`（Pydantic）：days=300/apply=False（默认 dry-run）/ic_threshold=0.02/drop_threshold=0.30/drop_severe=0.50/cooldown_days=30
- `EnergyQaReviewPipeline`：`run(trace_id)` 六阶段串行；`_stage_degradation`（退化检测+统一落库）、`_stage_lifecycle`（AutoRetire+冷却期回归）、`_subchain_degradation`（单元粒度退化）、`_shrink_scope`（scope 收缩）、`_assert_apply_consistency`（影子校验，反沉降通道 v2.105.0+18）

**关键函数**：
- `decide_factor(...)`：宁严勿松判定——severe 或 slope=retired → degraded；任一命中 → shadow；全达标 → active
- `check_invalid_when(f, current_regime)`：因子 invalid_when 声明 vs 当前制度命中检查

**灰度**：`FTS_ENERGY_QA_REVIEW_APPLY=0` 全管道 dry-run（不落库不改 tracking），判定一致后置 1 正式落库。

#### 4.3.7 因子资产库 `factor_db/`

- **[schema.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_db/schema.py)**：DuckDB 表结构定义（v1.2）。`get_db_path(market)` 按市场返回库路径：`factor_catalog_futures.duckdb` / `factor_catalog_energy.duckdb` / `factor_catalog.duckdb`。核心表：`factor_catalog`（因子主表：factor_id PK/name/code/code_hash/params/economic_logic/status/market/is_elite/metadata）、`factor_evaluations`（L1-L3 三级评估）、`factor_versions`（代码版本历史）、`factor_correlations`、`factor_quality_scores`、`factor_status_history`、`factor_audit_reports`、`factor_reviews`（L2 评审决策 approved/rejected）、`feedback_*`（C.3 反馈闭环）、`seed_lineage`、`subchain_factor_quality`。
- **[repository.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_db/repository.py)**：仓储层（GAP-149 状态枚举校验 9 值；GAP-150 写路径契约）。`FactorRepository`（create_factor 带 sha256 code_hash + 版本记录 + CHECKPOINT / get / update / list / retire / get_versions / get_evaluations）、`FactorQualityScoreRepository`、`FactorStatusRepository`（update_factor_status/log_transition）、`FactorAuditReportRepository`、`SubchainQualityRepository`。
- **[lineage.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_db/lineage.py)**：`FactorLineage.get_lineage(factor_id)` 血缘追踪（ancestors 父链/descendants 子因子/versions/evaluations）。

#### 4.3.8 质检体系 `qa/`

对照《期货CTA多因子策略标准化作业手册》6.2-6.8：

| 文件 | 职责 |
|---|---|
| [pre_entry.py](file:///d:/Programs/factor_system/fts/factor_engine/qa/pre_entry.py) | 入库前质检 Q1-Q10：Q1-Q3 一票否决（未来函数/逻辑文档化/参数网格），Q4-Q10 评分项（IC/IR/单调性/置换检验/极端行情/参数敏感度/板块拆解）；`judge_q10_subchain` 子链特异判定 |
| [admission.py](file:///d:/Programs/factor_system/fts/factor_engine/qa/admission.py) | 三级准入分类：CORE（≥4 分，权重 ≤30%）/ CANDIDATE（3-4 分，≤15%）/ REJECTED（<3 分）；`SUBCHAIN_SPECIFIC_MAX_WEIGHT=0.10` |
| [monthly_check.py](file:///d:/Programs/factor_system/fts/factor_engine/qa/monthly_check.py) | 月度滚动复检 M1-M5（60 日 IC/IR/衰减率/分层收益/排名偏差），处置路径：0 预警/1 项降权 50%/2 项降权 30%/≥3 项归零暂停/连续 3 月退役 |
| [quarterly_check.py](file:///d:/Programs/factor_system/fts/factor_engine/qa/quarterly_check.py) | 季度全量复检 F1-F6（全样本 IC/IR/分层收益/参数最优性/相关性矩阵/Regime 条件 IC/板块拆解 F6 两级重构） |
| [semi_annual.py](file:///d:/Programs/factor_system/fts/factor_engine/qa/semi_annual.py) | 半年度深度复检 D1-D4（经济逻辑/全样本回测/品种池重构/淘汰库复审） |
| [retirement.py](file:///d:/Programs/factor_system/fts/factor_engine/qa/retirement.py) | 退役判定 5 条红线（R1 连续 3 月预警/R2 60 日 IC 降 >50%/R3 IR<0.15/R4 逻辑失效/R5 数据源中断），NaN 兜底不误退役 |
| [status_board.py](file:///d:/Programs/factor_system/fts/factor_engine/qa/status_board.py) | 7 状态机（DRAFT/PENDING_QA/CORE/CANDIDATE/OBSERVATION/SUSPENDED/RETIRED）+ 合法流转 + 状态别名归一（v2.104.0+95） |

#### 4.3.9 因子表达式 DSL `expr_dsl/`

| 文件 | 职责 |
|---|---|
| [ast.py](file:///d:/Programs/factor_system/fts/factor_engine/expr_dsl/ast.py) | `ExprNode`（dataclass）：op/args/kind 三种节点 |
| [parser.py](file:///d:/Programs/factor_system/fts/factor_engine/expr_dsl/parser.py) | `ExprParser.parse()` 递归下降解析受控函数调用语法 |
| [validator.py](file:///d:/Programs/factor_system/fts/factor_engine/expr_dsl/validator.py) | `validate_expr` 静态校验（算子/字段/参数边界）+ `compute_max_lookback` PIT 静态计算防未来函数 |
| [compiler.py](file:///d:/Programs/factor_system/fts/factor_engine/expr_dsl/compiler.py) | `compile_expr_to_code` 编译为沙箱安全 FactorProgram.code + `analyze_expression` 静态分析 |
| [executor.py](file:///d:/Programs/factor_system/fts/factor_engine/expr_dsl/executor.py) | `evaluate(node, data, registry)` 解释执行器（向量化，不经沙箱编译） |
| [runtime.py](file:///d:/Programs/factor_system/fts/factor_engine/expr_dsl/runtime.py) | 沙箱 runtime：`eval_fts_expr(expression, data, params)` 白名单唯一放行入口 |
| [registry.py](file:///d:/Programs/factor_system/fts/factor_engine/expr_dsl/registry.py) | 算子注册表（核心，约 500+ 算子，L0-L5 分层 + C8~D17 多批扩容）；`verify_registry_consistency()` 双注册表一致性校验 |
| [factory.py](file:///d:/Programs/factor_system/fts/factor_engine/expr_dsl/factory.py) | `create_operator_factor(expression, ...)` FTS-Expr → FactorProgram |
| [seed_analyzer.py](file:///d:/Programs/factor_system/fts/factor_engine/expr_dsl/seed_analyzer.py) | WQ 风格种子表达式 PIT 静态审计（max_lookback/fields/operators/depth） |

#### 4.3.10 L1 知识提取器 `extractors/`

| 文件 | 职责 |
|---|---|
| [base.py](file:///d:/Programs/factor_system/fts/factor_engine/extractors/base.py) | `BaseExtractor` / `BaseExtractorPipeline` 抽象基类（extract/pause/resume + 状态 SSOT） |
| [futures_pipeline.py](file:///d:/Programs/factor_system/fts/factor_engine/extractors/futures_pipeline.py) | 期货三源管道组装：`YamlSeedExtractor`（YAML 源）+ `ResearchReportExtractor`（东财研报）+ `AcademicPaperExtractor`（arXiv）；`create_futures_extractor_pipeline()` 工厂 |
| [web_search.py](file:///d:/Programs/factor_system/fts/factor_engine/extractors/web_search.py) | `WebSearchExtractor` 必应检索 → LLM 提取；`KnowledgeGapQueryGenerator` 知识缺口驱动检索（plans/44 A1） |
| [bulk_collector.py](file:///d:/Programs/factor_system/fts/factor_engine/extractors/bulk_collector.py) | 批量采集层（plans/44 P0）：10 固定源（arXiv/OpenAlex/东财研报/CFTC/EIA/IEA/OPEC/Crossref/NBER/巨潮/新浪/SemanticScholar）+ 动态源，零 LLM token，落 DuckDB l1_knowledge_cache |
| [bulk_knowledge.py](file:///d:/Programs/factor_system/fts/factor_engine/extractors/bulk_knowledge.py) | 深读提取层：采集 → embedding 粗筛（零 token）→ 分块 LLM 深读提取因子 |
| [knowledge_filter.py](file:///d:/Programs/factor_system/fts/factor_engine/extractors/knowledge_filter.py) | `TextEmbedder`（本地 sentence-transformers）+ `KnowledgeRelevanceFilter`（余弦相似度粗筛）+ `dedup_semantic` 语义去重；模型缺失降级关键词规则 |
| [source_registry.py](file:///d:/Programs/factor_system/fts/factor_engine/extractors/source_registry.py) | 动态源 SSOT 注册表（DuckDB l1_knowledge_sources）+ 纯规则探活（pending→active→cooldown→retired） |
| [source_discovery.py](file:///d:/Programs/factor_system/fts/factor_engine/extractors/source_discovery.py) | 知识源自动发现（plans/46）：WebSearch → LLM 提取候选源 → 探活 → 注册 |
| [alternative_sources.py](file:///d:/Programs/factor_system/fts/factor_engine/extractors/alternative_sources.py) | 另类知识源：`AnnouncementNewsExtractor`（公告/舆情）+ `MacroEventExtractor`（宏观日历） |

#### 4.3.11 演化子模块（34 计划 C 阶段协作类）

| 文件 | 关键类 | 职责 |
|---|---|---|
| [evolution_seeds.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_seeds.py) | `SeedManager` | 种子评估晋升（`_evaluate_and_promote_seeds`）、L1 候选合并（`_merge_l1_candidates`）、种子相关性预检、Barra 暴露 |
| [evolution_candidate.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_candidate.py) | `CandidateProcessor` | 候选准入链：`_process_candidate`（评估→质检→审计→晋升全链） |
| [evolution_uct.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_uct.py) | `UctSelector` | UCT 父因子选择（`_select_parent_uct`）+ 熔断判定（`_check_circuit_breaker`）+ 提前达标停止 |
| [evolution_review.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_review.py) | `FactorReviewer` | 定期评审/数据质量检查 |
| [evolution_prefilter.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_prefilter.py) | `CandidatePrefilter` | 候选预筛（`_quick_prefilter`/`_cross_section_prefilter`/`_check_factor_runtime`） |
| [evolution_promote.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_promote.py) | `EliteStore` | 精英晋升/持久化（含影子池）、DuckDB 写入、正交化、`normalize_expression` 去重 |
| [evolution_audit.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_audit.py) | `AuditPipeline` | 审计/验证管线（`_run_factor_audit`/`_run_backtest_pipeline`/`_run_walkforward_oos`/`_run_causal_validation`） |
| [evolution_trace.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_trace.py) | `TraceRecorder` | trace/经验链/实验日志记录 |
| [evolution_channels.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_channels.py) | `EvolutionChannels` | 演化通道（`_run_gp_evolution`/`_run_deep_evolution`/`_generate_operator_factor`） |

#### 4.3.12 审计/验证/评分卡/回测

- **[audit.py](file:///d:/Programs/factor_system/fts/factor_engine/audit.py)**：`FactorAuditor.audit(factor, data, forward_returns, symbol_ic_map)` 6 项强制审计（causal_validity 因果 / oos_consistency 样本外 / cross_symbol 跨品种 ≥80% / stress_resilience 压力 / multiple_testing 多重检验 Bonferroni/FDR / snooping 数据窥探）。
- **[verifier.py](file:///d:/Programs/factor_system/fts/factor_engine/verifier.py)**：`FactorVerifier.check(evaluation)` 三级判定（L1 回测 / L2 经济逻辑 / L3 多重检验）；**锁定机制**：评估机制一旦锁定，任何 LLM 调用/参数演化不可修改判定逻辑；GAP-114 升级为成本敏感净收益校验。`get_global_verifier()` 全局单例。
- **[factor_quality_card.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_quality_card.py)**：`FactorQualityCard.evaluate(...)` 10 维度 0-50 分评分 → A/B/C 分级准入；模块级 `_map_*_to_score` 各维度映射函数。
- **[causal_validator.py](file:///d:/Programs/factor_system/fts/factor_engine/causal_validator.py)**：`CausalValidator.validate(factor, data, forward_returns)` 自然实验事件因果验证（ANOMALY_SIGMA_THRESHOLD=3.0）。
- **[backtest_pipeline.py](file:///d:/Programs/factor_system/fts/factor_engine/backtest_pipeline.py)**：`BacktestPipeline.run(input_data)` 4 阶段（DATA_LOAD → FACTOR_COMPUTE → PERFORMANCE → REPORT）+ `run_batch` 批量回测排名。

#### 4.3.13 退役登记 `retired_l3.py`（v3.0.0）

**职责**：L3 组合侧退役对象登记（plans/57 §4.1/§5.3）。`RetiredEntry`（name/module/migrated_to/status）登记 35 项；`warn_if_retired(name)` 对存量调用点发 DeprecationWarning；`is_retired(name)` 判定；`retired_registry()` 全量导出（审计/报告用）。

**35 项清单**：
- **futures_signal_pipeline.py（组合侧）11 项**：`_compute_composite_scores` / `_compute_per_variety_weights` / `_apply_regime_weight_adjustment` / `_apply_regime_direction_bias` / `_generate_trading_advice` / `_generate_trading_advice_report` / `_compute_holdout_validation` / `_load_l3_combo_weights` / `_load_l3_subchain_meta` / `_load_l3_combo_meta` / `_load_l3_combo_factors`
- **portfolio_loop.py（策略侧）21 项**：`synthesize_signals` / `_compute_elastic_net_weights` / `_compute_ml_ensemble_weights` / `_synthesize_bl_weights` / `regime_adaptive_weight_adjustment` / `build_combo` / `_cap_safety_valve` / `_validate_combo_sharpe` / `_run_sharpe_randomization_test` / `decay_test` / `apply_turnover_penalty` / `_apply_sticky_constraints` / `_compute_subchain_exposure` / `_merge_gate_scale_into_modulation` / `_greedy_select_by_correlation` / `_dedup_factors_by_chain` / `_dedup_factors_by_chain_cluster` / `_dedup_within_chain` / `_filter_by_quality_gate` / `_filter_shadow_pending` / `_filter_review_approved`
- **整体迁移模块 3 项**：`weight_learning`（→ RD strategy_synthesis/money_management）/ `capital_allocator`（→ RD money_management.capital_allocate）/ `regime_crowding`（→ RD crowding_gate 权威口径平移）

### 4.4 数据提供者层 `fts.data*`

- **[data.py](file:///d:/Programs/factor_system/fts/data.py)**：统一数据入口。`FTSDataProvider`：`get_futures_ohlcv(symbol, days)` / `get_futures_panel(symbols, days)`（默认动态池）/ `enrich_futures_fundamental`（注入 9 基本面字段）/ `synthesize_ohlcv`（合成兜底）；`get_data_provider()` 全局单例。
- **[data_futures.py](file:///d:/Programs/factor_system/fts/data_futures.py)**（约 2500 行）：期货数据核心。`FuturesDataProvider`：`get_ohlcv`（换月复权 + 跳空注入）/ `get_minute_ohlcv` / `get_tick_data` / `get_futures_panel`（G8 断K/跳空/持仓量异常标记）；连接管理 `AsyncWriteQueue` / `DuckDBConnection` / `DuckDBWriter` / `DuckDBReader`；品种池常量（SSOT 由 futures_universe.yaml 驱动）：`FUTURES_SUBSET`（84）/ `FUTURES_CORE_SUBSET`（25）/ `FUTURES_HOLDOUT`（15 盲测池）/ `FUTURES_STRATIFIED_SUBSET`（19）/ `ENERGY_CHAIN_SYMBOLS/TRAIN/HOLDOUT`（12 训练 + 8 盲测）/ `FUTURES_SECTOR_MAP`（17 产业链）/ `FUTURES_COVERAGE_PLAN`（coverage_priority P0-P3）。
- **[data_futures_fundamental.py](file:///d:/Programs/factor_system/fts/data_futures_fundamental.py)**：`AkshareFuturesFundamentalProvider`（库存=东财主源+99 期货兜底；基差=futures_spot_price_daily；仓单=CZCE/GFEX 官方 + SHFE/DCE/INE 东财）。
- **[data_futures_term_structure.py](file:///d:/Programs/factor_system/fts/data_futures_term_structure.py)**：`sync_term_structure_fields` 期限结构每日同步（term_spread/roll_yield → Parquet）。

### 4.5 多源数据适配器 `fts.data_sources`

**[base.py](file:///d:/Programs/factor_system/fts/data_sources/base.py)**：`BaseFuturesSource`（ABC）定义 3 抽象方法（fetch_ohlcv/fetch_quote/is_available）+ `SourceUnavailable` 异常 + `validate_ohlcv_row` 字段校验。

**[aggregator.py](file:///d:/Programs/factor_system/fts/data_sources/aggregator.py)**（核心）：`FuturesDataAggregator` 多级降级 + 字段增强层 + 熔断器 + 交叉验证。主路径 `QUANTDATA → DUCKDB_CACHE → TDX_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC`。关键方法：`get_ohlcv`（17 列 schema）/ `get_minute_ohlcv` / `get_ticks` / `_enhance_fields`（settle/pre_settle/oi_change/hold 覆盖 GAP-083）/ `_derive_pre_settle` / `_synthesize` / `_is_circuit_open`（5 次失败 6h 冷却）/ `cross_check` / `get_source_status`。

| 数据源 | 职责 |
|---|---|
| [tdx_local_source.py](file:///d:/Programs/factor_system/fts/data_sources/tdx_local_source.py) | TDX_LOCAL（v2.85.0 统一承载 17709 JSON-RPC）：日线+分钟 K 线 + 实时快照；`_symbol_to_tdx` 代码映射 |
| [quantdata_provider.py](file:///d:/Programs/factor_system/fts/data_sources/quantdata_provider.py) | QUANTDATA 权威源（v2.105.0+32，GAP-156）：只读 D:\QuantData kline_history.duckdb，字段权威矩阵校验，自带熔断 |
| [tqsdk_source.py](file:///d:/Programs/factor_system/fts/data_sources/tqsdk_source.py) | TQSDK 兜底源：get_kline_serial 日/分钟线；`_SYMBOL_MAP` 70+ 品种映射 |
| [tqsdk_enhance_source.py](file:///d:/Programs/factor_system/fts/data_sources/tqsdk_enhance_source.py) | TQSDK_ENHANCE 字段增强：只补 hold（←close_oi）与 oi_change |
| [akshare_minute_source.py](file:///d:/Programs/factor_system/fts/data_sources/akshare_minute_source.py) | AKSHARE_MINUTE 分钟源（1023 行/周期） |
| [wind_source.py](file:///d:/Programs/factor_system/fts/data_sources/wind_source.py) | WIND 字段增强层（MCP 委托 mx_comprehensive_finance_data） |
| [ifind_source.py](file:///d:/Programs/factor_system/fts/data_sources/ifind_source.py) | IFIND 字段增强 + EDB 宏观数据（edb_cache 表） |
| [ifind_sdk_source.py](file:///d:/Programs/factor_system/fts/data_sources/ifind_sdk_source.py) | IFIND_SDK 官方 SDK 直连（iFinDPy，GAP-083 方案 A） |
| [fusion.py](file:///d:/Programs/factor_system/fts/data_sources/fusion.py) | `OHLCVFusion` 多源融合（MEDIAN 默认/MEAN/WEIGHTED/HIERARCHICAL/TRIMMED_MEAN） |
| [roll_calendar.py](file:///d:/Programs/factor_system/fts/data_sources/roll_calendar.py) | `RollCalendar` 换月日历：主力判定（每日最大成交量）→ 比率法后复权 |
| [trading_calendar.py](file:///d:/Programs/factor_system/fts/data_sources/trading_calendar.py) | `TradingCalendar` 交易日历 + 断K/跳空/持仓量突变标记（G8） |
| [overnight_gap.py](file:///d:/Programs/factor_system/fts/data_sources/overnight_gap.py) | `inject_overnight_gap` 夜盘跳空标记（GAP-066） |
| [macro_aligner.py](file:///d:/Programs/factor_system/fts/data_sources/macro_aligner.py) | `MacroFieldAligner` 宏观字段对齐注入（lag_days 发布滞后防未来函数） |
| [macro_eastmoney_source.py](file:///d:/Programs/factor_system/fts/data_sources/macro_eastmoney_source.py) | `EastmoneyMacroSource` 东财+中债登宏观源（替代 iFinD EDB 作为默认源） |
| [migrate.py](file:///d:/Programs/factor_system/fts/data_sources/migrate.py) | `migrate_schema` DuckDB 表结构幂等迁移 |

### 4.6 存储注册表与状态库 `fts.store`

- **[registry.py](file:///d:/Programs/factor_system/fts/store/registry.py)**：`StorageRegistry` 从 `docs/harness/_data/storage_landscape.yaml` 加载 FTS 全部存储域权威契约（SSOT 一数一源）。`get(domain)` / `find_by_path` / `warn_unregistered_write`（GAP-150 写路径契约断言，严格模式默认开启，`FTS_STORAGE_WRITE_STRICT=0` 回退告警）/ `get_storage_registry()` 进程级单例。
- **[duckdb_lock.py](file:///d:/Programs/factor_system/fts/store/duckdb_lock.py)**：`duckdb_write_lock(db_path, timeout)` 跨进程写锁（E.4 S1：Windows msvcrt / POSIX fcntl，非阻塞轮询，不可重入）。
- **[state_db.py](file:///d:/Programs/factor_system/fts/store/state_db.py)**：`StateKVStore` L4 运行状态库（E.3 S2 自 DuckDB 迁移至 SQLite WAL）：`state_kv` 当前态 UPSERT + `state_history` 历史回放。`upsert(namespace, key, value, run_id, ts)` 单事务原子写；`get_state_store()` 进程级单例。

### 4.7 模拟/实盘交易体系 `fts.live_trade`

| 文件 | 关键类/函数 | 职责 |
|---|---|---|
| [orders.py](file:///d:/Programs/factor_system/fts/live_trade/orders.py) | `Order` / `OrderLifecycle` | 订单生命周期状态机（PENDING→SUBMITTED→PARTIAL→FILLED/CANCELED/REJECTED） |
| [stop_orders.py](file:///d:/Programs/factor_system/fts/live_trade/stop_orders.py) | `StopOrderManager` | 持仓级止损/止盈单管理，触发生成平仓指令 |
| [intervention.py](file:///d:/Programs/factor_system/fts/live_trade/intervention.py) | `InterventionController` | 人工干预通道（权限最高）：紧急暂停/恢复/一键平仓/信号拦截 |
| [gateway.py](file:///d:/Programs/factor_system/fts/live_trade/gateway.py) | `AbstractGateway` / `SimulatedGateway` / `submit_with_retry` | 网关抽象 + 模拟实现（失败/超时注入）+ 下单重试 |
| [book.py](file:///d:/Programs/factor_system/fts/live_trade/book.py) | `build_book_from_ticks` | tick 盘口契约（5 档/单档两种形态） |
| [matching.py](file:///d:/Programs/factor_system/fts/live_trade/matching.py) | `OrderBookMatchingEngine.match_market` | tick 逐档撮合（逐档消耗/加权均价/滑点自然产生） |
| [simulated_engine.py](file:///d:/Programs/factor_system/fts/live_trade/simulated_engine.py) | `SimulatedReplayEngine` / `SimulatedPaperTrader` | 历史回放（t 日信号→t+1 开盘成交→收盘盯市，杜绝未来函数）/ 实时纸面 |
| [simulated_portfolio.py](file:///d:/Programs/factor_system/fts/live_trade/simulated_portfolio.py) | `SimulatedPortfolio` | 模拟仓核心：apply_signal（干预门→风控门→撮合）/ mark_to_market / 归因 / 反馈 |
| [sqlite_store.py](file:///d:/Programs/factor_system/fts/live_trade/sqlite_store.py) | `SimSQLiteStore` | 模拟仓 SQLite 持久化（账户/持仓/成交/权益四表，WAL） |
| [contracts.py](file:///d:/Programs/factor_system/fts/live_trade/contracts.py) | `SimPosition` 等 TypedDict + `CONTRACT_MULTIPLIERS`（约 90 品种） | 模拟仓契约 + 合约乘数表 |
| [paper_trader_mhf.py](file:///d:/Programs/factor_system/fts/live_trade/paper_trader_mhf.py) | `MhfPaperTrader` / `MhfRiskConfig` | 分钟级模拟盘回放 + 盘中风控 |
| [capital_ramp.py](file:///d:/Programs/factor_system/fts/live_trade/capital_ramp.py) | `ramp_status` / `can_advance` | 实盘资金三级爬坡（10%/30 天→50%→100%） |
| [simulation_gap.py](file:///d:/Programs/factor_system/fts/live_trade/simulation_gap.py) | `simulation_backtest_gap_check` | 仿真 vs 回测净值偏差对比（±5% Checkpoint） |
| [tqsdk_mhf_executor.py](file:///d:/Programs/factor_system/fts/live_trade/tqsdk_mhf_executor.py) | `TqSdkMhfExecutor` / `is_trading_time` | MHF 信号 TqSdk 模拟执行（TargetPosTask 调仓） |

### 4.8 风控层 `fts.risk`

- **[risk_manager.py](file:///d:/Programs/factor_system/fts/risk/risk_manager.py)**：`RiskManager.check(signal, account, positions)` 五项风控规则（单品种仓位 ≤10% / 组合最大回撤 ≤20% / 单日最大亏损 ≤5% / 杠杆 ≤3x / 集中度 ≤50%），任一不通过拦截；支持 regime/beta_state 制度化风控参数注入。
- **[portfolio_metrics.py](file:///d:/Programs/factor_system/fts/risk/portfolio_metrics.py)**：`compute_portfolio_metrics`（EWMA 波动/VaR/CVaR/集中度/连续亏损/盈亏比）+ `evaluate_metrics` 三级预警（WARN/BLOCK/FORCE_CLOSE）。
- **[simulated_adapter.py](file:///d:/Programs/factor_system/fts/risk/simulated_adapter.py)**：`SimulatedTradeAdapter` 模拟成交适配器。

### 4.9 ML 模型层 `fts.ml`

- **[models.py](file:///d:/Programs/factor_system/fts/ml/models.py)**：`MLSignalModel`（LightGBM/XGBoost/等权 Ensemble）+ `MLPFactorModel` / `GRUFactorModel` / `TransformerFactorModel`（纯 numpy 实现，缺依赖降级）；工厂 `create_signal_model()` 等。
- **[trainer.py](file:///d:/Programs/factor_system/fts/ml/trainer.py)**：`SignalModelTrainer.train(X, y, feature_names)` 三种模式（横截面/时序/集成融合），返回 R² 与特征重要性。
- **[deep_factor.py](file:///d:/Programs/factor_system/fts/ml/deep_factor.py)**：`DeepFactorGenerator.generate(...)` 深度因子生成（GRU/Transformer 权重序列化内嵌到因子 code，零未来函数，可过审计链）。

### 4.10 信号桥接层 `fts.bridge`

- **[signal_bridge.py](file:///d:/Programs/factor_system/fts/bridge/signal_bridge.py)**：`SignalBridge.publish(signal)` 统一发布到 JSON（默认）/ Redis / REST 三协议；`latest()` / `status()`。VNPY 对接。

### 4.11 监控层 `fts.monitor`

| 文件 | 关键类/函数 | 职责 |
|---|---|---|
| [data_level_monitor.py](file:///d:/Programs/factor_system/fts/monitor/data_level_monitor.py) | `DataLevelMonitor` | 数据级质量监控（GAP-F06）：完整性/异常值 3σ/复权一致性/多源分歧四维 + 代理失真量化（proxy_fields/proxy_ratio_critical=0.5，GAP-151） |
| [data_quality_monitor.py](file:///d:/Programs/factor_system/fts/monitor/data_quality_monitor.py) | `DataQualityMonitor` | 因子级实时质量：IC 漂移（Z-Score）+ 容量突变 + B.1 三维指标函数 |
| [logic_monitor.py](file:///d:/Programs/factor_system/fts/monitor/logic_monitor.py) | `LogicMonitor` | 逻辑监控：因子行为漂移/极端预测（连续+离散双口径，discrete_nunique_threshold=20）/换月日异常/市场前提（plans/54 P0-3） |
| [live_factor_monitor.py](file:///d:/Programs/factor_system/fts/monitor/live_factor_monitor.py) | `LiveFactorMonitor` | Live 表现 vs 回测基线偏离（默认 30%）+ GAP-I402 衰减告警 |
| [elite_tracker.py](file:///d:/Programs/factor_system/fts/monitor/elite_tracker.py) | `EliteFactorTracker` / `AutoRetireManager` | 精英因子 OOS 跟踪：周度 IC 记录、衰减检测（滚动 6M 回归斜率）、A/B/C 分级、状态机与自动淘汰 |
| [prometheus_metrics.py](file:///d:/Programs/factor_system/fts/monitor/prometheus_metrics.py) | `MetricsRegistry` / `metrics_registry` | Prometheus 指标注册表（A.2/A.3/C.2/C.3/28-T10 Regime 观测），`render()` 供 /metrics |
| [http_server.py](file:///d:/Programs/factor_system/fts/monitor/http_server.py) | `FTSDashboardServer` / `_DashboardHandler` | Web UI 仪表盘（9100）：状态/因子列表/候选池/人审工作台/指标/WorkFlow API |
| [reaudit.py](file:///d:/Programs/factor_system/fts/monitor/reaudit.py) | `run_reaudit` / `apply_reaudit_results` | 新标准准入复审（retain/shadow/retire/error 四处置） |

### 4.12 调度层 `fts.scheduler`

- **[tasks.py](file:///d:/Programs/factor_system/fts/scheduler/tasks.py)**：`TaskSpec` 契约 + `TaskRegistry` + `register_default_tasks()` 注册 18 个默认任务；统一 `enabled = FTS_INTERNAL_SCHEDULER_ENABLED`（默认 "0" 停用，以 TRAE Schedule 为唯一调度源）。
- **[jobs.py](file:///d:/Programs/factor_system/fts/scheduler/jobs.py)**：任务工作函数（全部签名 `() -> None`，内部生成 trace_id，捕获全部异常）。关键辅助：`_market_gate`（全局市场门控，v2.104.0+101）、`_global_market()`（跟随 `FTS_DEFAULT_MARKET` → cfg.default_market，默认 futures）、`_read_kline_cache`（字段完整度优先去重 GAP-148 + settle/hold/pre_settle 代理填充）、`_check_kline_field_integrity`（GAP-151 核心字段缺失→error+跳过，增强字段→warning+代理降级）。
- **[engine.py](file:///d:/Programs/factor_system/fts/scheduler/engine.py)**：`SchedulerEngine`（APScheduler BackgroundScheduler 包装，未安装静默降级）；`start_watchdog` 进程守护。
- **[watchdog.py](file:///d:/Programs/factor_system/fts/scheduler/watchdog.py)**：`ProcessWatchdog`（崩溃自动拉起，连续 3 次 <30s → 熔断 5 分钟）。

**内部注册表全部 18 个定时任务**（`enabled=False` 统一停用，仅作 TRAE Schedule 对齐清单与文档）：

| 任务名 | cron 表达式 | 职责 |
|---|---|---|
| `l1_meta_loop` | `0 0 * * *` | L1 Meta-Loop 每日知识补给 |
| `l2_seed_promotion` | `0 2 * * *` | L2 种子评估晋升（45 计划候选①） |
| `l2_evolution_weekday` | `0 3 * * 1-5` | L2 因子演化（小预算 ≈10 代） |
| `l2_evolution_weekend` | `0 3 * * 6` | L2 因子演化（大预算 ≈50 代） |
| `l3_portfolio_loop` | `0 6 * * 1-5` | L3 Portfolio Loop（**已退役登记 plans/57**，登记兼容期，调用将告警） |
| `futures_signal_pipeline` | `0 20 * * 1-5` | 期货信号管道（**组合侧已退役登记**，信号计算部分保留） |
| `sync_futures_data` | `30 17 * * 1-5` | 期货多源数据同步（Stage1 kline/Stage2 fundamental/Stage3 term_structure） |
| `health_check` | `*/10 * * * *` | 健康检查（check_all_status） |
| `l2_review` | `0 10 * * 0` | L2 周度评审（准入重审+衰减评估+阀门巡检；**v3.0.0 起并入 TRAE Schedule 每日 04:00 统一任务周日重量级分支**，内部注册表保留 cron） |
| `data_quality_eval` | `*/5 * * * *` | 数据质量周期评估（B.1） |
| `data_level_monitor` | `0 5 * * *` | 数据级质量监控（GAP-F06） |
| `logic_monitor` | `30 4 * * *` | 逻辑监控（行为漂移/极端预测/换月异常/市场前提） |
| `factor_inspector` | `0 4 * * *` | 因子巡检与自动降级 |
| `sync_liquidity_pool` | `0 8 * * 6` | 数据驱动动态池刷新（GAP-054） |
| `mhf_signal` | `*/30 * * * *` | MHF 中高频信号（30m 反转）→ Bridge 发布 + TqSdk 模拟执行 |
| `l2_subchain_quality` | `0 9 * * 0` | 批量子链质量评估 |
| `import_external_factors` | `0 9 1 * *` | 外部因子常态化导入（v2.105.0+32） |
| `l2_batch_mining` | `0 6 * * 0` | L2 批量挖掘（45 计划候选②） |

**TRAE Schedule 定时任务（v3.0.0+1 重建后 8 Active，全期货）**：

| 任务 | 说明 |
|---|---|
| L1 知识补给 `l1_meta_loop_job()` | 每日，全期货市场感知 + 知识注入 |
| L2 种子评估+演化 `l2_seed_promotion` + `evolution_weekday\|weekend` | 每日 01:00 合并任务（先种子晋升 → 演化，工作日 ≈10 代/周末 ≈50 代，生成端去重 Step 1.35 内嵌） |
| L2 评审质检统一任务 | 每日 04:00（v3.0.0 合并原「周日周度评审」+「每日阀门+监控」：周日重量级 `l2_review_job` 全量重审+衰减淘汰+阀门收口，其余日轻量五步 ①机审→②巡检降级 approved 豁免→③逻辑监控→④数据级监控→⑤因子级监控，全部在 05:00 前） |
| 外部因子导入 `import_external_factors_job()` | 每月 1 日，6 源 YAML → 字段权威校验 → 去重 → 注入 |
| 数据基础设施（多源同步 `sync_futures_data_job` / QuantData 每日日线刷新） | 工作日数据同步 |
| RD 交易建议（14:30 / 21:30） | Regime-Driven 交易建议产出 |

**jobs.py 中保留的 energy 链 job 函数**（GAP-121 独立工作流，**v3.0.0+1 起不再定时调度**，仅供手动调用）：`l2_seed_promotion_energy_job` / `l2_batch_mining_energy_job` / `l2_review_energy_job` / `l2_energy_qa_review_job`（`FTS_ENERGY_QA_REVIEW_APPLY=0` 默认 dry-run）/ `factor_level_monitor_job`。

### 4.13 工作流层 `fts.workflow`

- **[stages.py](file:///d:/Programs/factor_system/fts/workflow/stages.py)**：`STAGES` 12 阶段常量表（s1 数据基建 → s11 实盘爬坡 + qa 质检闭环），`StageAction`（id/label/cmd/kind: cli|script|info/timeout/json_output），命令支持 `{factor_id}`/`{report_dir}` 动态占位符。
- **[executor.py](file:///d:/Programs/factor_system/fts/workflow/executor.py)**：`WorkflowExecutor.run_stage` / `run_all`（端到端顺序执行，失败即停）真实调用 fts.cli/脚本（后台线程 subprocess），超时/退出码/日志全量留痕，JSON 产物解析入库。
- **[store.py](file:///d:/Programs/factor_system/fts/workflow/store.py)**：`WorkflowStore`（SQLite WAL `data/workflow.db`：workflow_runs + stage_runs 双表，崩溃可回放）。

### 4.14 LLM 客户端 `fts.llm`

- **`LLMClient`**（ABC）：`complete(prompt)` / `generate_json(prompt)`（多级 JSON 修复解析）/ `bootstrap_factors(...)`（L1 Bootstrapping）/ `fix_economic_logic(...)`（GAP-123）/ `fix_factor_code(...)`（plans/44 C1）。
- **`OpenAIClient`**：`OPENAI_API_KEY`（必填）/ `OPENAI_BASE_URL`（可指向 DeepSeek 等兼容端点）/ `OPENAI_MODEL`（默认 gpt-4o）；`_build_bootstrap_prompt` 含能源链知识段/子链聚焦/负面样本/论证-评分一致性规则。
- **`AnthropicClient`**：`ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`（默认 claude-sonnet-4-20250514）。
- **`MockLLMClient`**：开发/测试用模拟客户端。
- **`get_llm_client(backend, temperature)`**：工厂函数，自动检测顺序 `FTS_LLM_BACKEND` → `OPENAI_API_KEY` → `ANTHROPIC_API_KEY` → Mock。

### 4.15 CLI 入口 `fts.cli`

**职责**：FTS 全部 CLI 子命令的 argparse 定义与实现（约 2950 行，30+ 命令函数）。`main()` 为每个 CLI 会话挂载 session_id + 后台线程预热 numba 内核。所有命令生成 trace_id 贯穿全流程。

**子命令组**：`evolution run`（L2，`--chain energy` / `--symbols` 显式品种）/ `meta-loop run`（L1）/ `portfolio run`（L3，⚠️ 已退役登记，不建议调用）/ `monitor` / `data`（status/sync-futures/cross-check/fuse）/ `catalog`（stats/verify/backup）/ `factor`（list/show/seeds/stats/lineage/review/recalibrate/micro-generate/senti-generate）/ `seed`（validate/report/dedup）/ `backtest`（run/batch/compare）/ `feature`（list/analyze）/ `gp evolve` / `feedback`（trigger/process/report/stats/import/live-ic）/ `bridge`（serve/publish/status）/ `scheduler`（run/status/list）/ `ui`（Web UI 9100）/ `version`。

**关键辅助函数**：`_prepare_futures_data`（横截面面板准备 + 宏观注入）、`_build_default_aggregator`（QuantData→TDX_LOCAL 源 + TQSDKEnhanceSource 增强）、`_relaxed_futures_quality_config` / `_relaxed_futures_audit_config`。

---

## 5. 关键类/函数速查表

| 类/函数 | 位置 | 一句话说明 |
|---|---|---|
| `EvolutionLoop.run()` | [evolution_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_loop.py) | L2 因子演化主入口（逐代 UCT→演化→准入） |
| `MetaLoop.run()` | [meta_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/meta_loop.py) | L1 市场感知 + 知识补给 + Bootstrapping |
| `PortfolioLoop.run()` | [portfolio_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py) | L3 组合构建主入口（**已退役登记 plans/57**，登记兼容期运行将告警） |
| `retired_l3.warn_if_retired()` | [retired_l3.py](file:///d:/Programs/factor_system/fts/factor_engine/retired_l3.py) | L3 退役对象调用告警（35 项登记） |
| `FactorVerifier.check()` | [verifier.py](file:///d:/Programs/factor_system/fts/factor_engine/verifier.py) | 三级评估判定（锁定机制） |
| `FactorAuditor.audit()` | [audit.py](file:///d:/Programs/factor_system/fts/factor_engine/audit.py) | 6 项强制审计 |
| `FactorQualityCard.evaluate()` | [factor_quality_card.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_quality_card.py) | 10 维 50 分制质量评分 |
| `FactorRepository` | [repository.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_db/repository.py) | 因子资产库 DuckDB 仓储 |
| `build_signal_matrix()` | [l3_signal_service.py](file:///d:/Programs/factor_system/fts/factor_engine/l3_signal_service.py) | L3 信号矩阵 B 层构建（信号契约 v1） |
| `persist_signal_matrix()` | [l3_signal_service.py](file:///d:/Programs/factor_system/fts/factor_engine/l3_signal_service.py) | L3 信号矩阵 D 层写入（schema_version/factor_status/factor_scope） |
| `EnergyQaReviewPipeline.run()` | [energy_qa_review.py](file:///d:/Programs/factor_system/fts/factor_engine/energy_qa_review.py) | 能化链评审质检统一管道 |
| `compute_subchain_degradation()` | [subchain_lifecycle.py](file:///d:/Programs/factor_system/fts/factor_engine/subchain_lifecycle.py) | 因子×子链单元退化判定 |
| `FuturesDataAggregator.get_ohlcv()` | [aggregator.py](file:///d:/Programs/factor_system/fts/data_sources/aggregator.py) | 多源降级 K 线主入口 |
| `FuturesDataProvider.get_ohlcv()` | [data_futures.py](file:///d:/Programs/factor_system/fts/data_futures.py) | 期货日 K 主入口（复权+跳空） |
| `FTSConfig` / `get_config()` | [settings.py](file:///d:/Programs/factor_system/fts/config/settings.py) | 全局配置（env > YAML > 默认） |
| `StorageRegistry.warn_unregistered_write()` | [registry.py](file:///d:/Programs/factor_system/fts/store/registry.py) | 写路径契约断言（GAP-150 严格模式） |
| `StateKVStore.upsert()` | [state_db.py](file:///d:/Programs/factor_system/fts/store/state_db.py) | L4 状态库原子写（SQLite WAL） |
| `RiskManager.check()` | [risk_manager.py](file:///d:/Programs/factor_system/fts/risk/risk_manager.py) | 五项风控规则校验 |
| `SimulatedPortfolio.apply_signal()` | [simulated_portfolio.py](file:///d:/Programs/factor_system/fts/live_trade/simulated_portfolio.py) | 模拟仓信号应用（干预→风控→撮合） |
| `SignalBridge.publish()` | [signal_bridge.py](file:///d:/Programs/factor_system/fts/bridge/signal_bridge.py) | 信号三协议发布 |
| `get_llm_client()` | [llm.py](file:///d:/Programs/factor_system/fts/llm.py) | LLM 客户端工厂（自动检测后端） |
| `WorkflowExecutor.run_all()` | [executor.py](file:///d:/Programs/factor_system/fts/workflow/executor.py) | WorkFlow 端到端执行 |
| `FTSDashboardServer` | [http_server.py](file:///d:/Programs/factor_system/fts/monitor/http_server.py) | Web UI 仪表盘（9100） |
| `SchedulerEngine.start()` | [engine.py](file:///d:/Programs/factor_system/fts/scheduler/engine.py) | APScheduler 调度引擎 |

---

## 6. 依赖关系图

```
fts/core (契约/枚举/原子文件)
   ↑
fts/config ──► config/settings.yaml / futures_universe.yaml
   ↑
fts/data ──► fts/data_futures ──► fts/data_sources（aggregator + 12 源）
   │              │                   └─► fts/store（registry/lock）──► DuckDB
   │              └─► fts/data_futures_fundamental / term_structure
   ↑
fts/llm ──► OpenAI / Anthropic SDK
   ↑
fts/factor_engine（L1/L2/L3 主循环）
   ├── expr_dsl（DSL）──► feature_ops / ops_library（双注册表）
   ├── extractors（L1 知识源）──► requests / sentence-transformers
   ├── qa / factor_db（质检 + DuckDB 因子资产库）
   ├── subchain_*（子链体系）
   ├── energy_qa_review（评审质检管道）
   ├── retired_l3（L3 退役登记，v3.0.0）
   └── regime_* / mhf_* / panel_vector / numba_kernels
   ↑
fts/scheduler（tasks/jobs/engine/watchdog）──► 调用全部 Loop 与监控
   ↑
fts/monitor（数据/逻辑/实盘/精英跟踪/HTTP/Prometheus）
   ↑
fts/live_trade（模拟仓/撮合/干预）──► fts/risk（风控）
   ↑
fts/ml（模型训练/深度因子）──► fts/factor_engine（产出 FactorProgram）
fts/bridge（信号出口）──► FDT / VNPY
fts/workflow（CTA 手册工作流）──► fts.cli / scripts
fts/cli ──► 以上全部（统一入口）
```

**关键链路**：
- **L1 链**：`meta_loop`（MetaLoop/BootstrappingChain/L1Verifier）→ `extractors`（多源提取）→ `l1_l2_funnel.funnel_record` → factor_pool.json → L2 消费
- **L2 链**：`evolution_loop`（EvolutionLoop + 9 协作类）→ `factor_db`（写 factor_catalog_{futures,energy}.duckdb）→ `energy_qa_review`（退化检测/冷却期回归）→ `subchain_eval`（质量矩阵）
- **信号矩阵链（FTS 保留能力，v3.0.0）**：`l3_signal_service`（build_signal_matrix → persist_signal_matrix，信号契约 v1 三列）→ `l3_signal_store.duckdb` → **Regime-Driven** 消费（strategy_synthesis/combo_verifier/money_management/crowding_gate）
- **L3 组合侧（已退役 plans/57 §4.1）**：`retired_l3.py` 35 项登记；`portfolio_loop` 策略侧 / `futures_signal_pipeline` 组合侧函数禁止新增调用点
- **数据链**：`data_sources.base`（契约）← 各适配器 → `aggregator`（降级+熔断）→ `data_futures`（FuturesDataProvider）→ `data.FTSDataProvider`（引擎统一入口）
- **存储链**：`store.registry`（域登记 SSOT）→ `store.duckdb_lock`（写锁）→ 各 DuckDB/SQLite/Parquet

---

## 7. 项目运行方式

### 7.1 安装

```bash
# Python 3.10+（项目内 Python 路径 C:\Program Files\Python312\python.exe）
pip install -r requirements.txt          # 全量开发环境（all extra）
pip install -e .                         # 或仅核心
pip install -e ".[llm,evolution,regime]" # 按需 extra
```

### 7.2 环境准备

1. 复制 `.env.example` 为 `.env`，配置 `OPENAI_API_KEY`（或 `ANTHROPIC_API_KEY`）、`TQSDK_USER/TQSDK_PASSWORD`（如需 TQSDK 源）、`IFIND_TOKEN`（可选）等
2. 数据源：启动通达信客户端确保端口 17709 可访问（TDX_LOCAL）；或配置 `FTS_QUANTDATA_HOME` 指向权威数据仓库（QuantData，默认 D:\QuantData）
3. 使用 [start_fts.ps1](file:///d:/Programs/factor_system/start_fts.ps1) 启动（PowerShell 加载 .env 并设置 FTS_CONFIG_FILE/FTS_MEMORY_DIR）
4. **全局默认市场**：如需全期货工作流，设置 `$env:FTS_DEFAULT_MARKET='futures'`（覆盖 settings.yaml 残留的 `energy`，见 §8.1）

### 7.3 常用 CLI

```bash
fts version                    # 查看版本
fts monitor                    # 系统健康状态
fts meta-loop run              # L1 市场感知（--market futures/energy）
fts evolution run --max-generations 10          # L2 因子演化（全期货）
fts evolution run --chain energy --max-generations 10   # 能化链专属演化（候选池工作流）
fts portfolio run --universe futures            # L3 组合构建（⚠️ 已退役登记 plans/57，不建议调用）
fts factor list --market futures                # 列出 elite 因子
fts factor lineage fct_xxxxxxxx                 # 因子血缘
fts backtest run <factor_id>                    # 单因子回测
fts backtest batch --limit 20                   # 批量回测
fts ui --port 9100                              # Web UI 仪表盘（WorkFlow 看板 /workflow）
fts scheduler list                              # 查看调度任务
fts bridge serve                                # 启动信号桥接服务
fts data status                                 # 多源熔断器状态
fts data sync-futures --days 120                # 期货多源数据同步
fts data fuse --strategy MEDIAN                 # 多源融合
fts data cross-check --symbol RB0 --date 2026-08-19   # 多源交叉验证
```

### 7.4 能化产业链工作流（GAP-121 候选池）

```bash
# 0) 数据深度补全（LU0/PR0/PL0 等新上市品种）
python scripts/sync_energy_chain_depth.py            # 全品种；--symbols LU0,PR0 指定

# 1) 因子挖掘（12 化工品种演化，落 factor_catalog_energy.duckdb）
fts evolution run --chain energy --max-generations 10

# 2) 因子质检（12 品种全链质检）
python scripts/verify_qa_workflow.py --chain energy --days 300

# 3) L2 评审质检统一管道（FTS_ENERGY_QA_REVIEW_APPLY=1 正式落库）
$env:FTS_ENERGY_QA_REVIEW_APPLY='1'
python -u -c "from fts.scheduler.jobs import l2_energy_qa_review_job; l2_energy_qa_review_job()"
```

### 7.5 存量因子集中重审（v3.0.0 新增）

```bash
# 分族 FDR-BH 校正 + audit 分层抽样；默认 dry-run（FTS_REVIEW_LEGACY_APPLY=1 才落库）
python scripts/review_legacy_factors.py
$env:FTS_REVIEW_LEGACY_APPLY='1'
python scripts/review_legacy_factors.py
```

### 7.6 运行测试

```bash
# 日常回归（跳过 slow 重量级测试，单进程）
pytest tests/ -m "not slow" -q -o addopts="" -p no:cacheprovider

# 模块测试
pytest tests/factor_engine/ -v
pytest tests/data_sources/ -v

# 全量测试（仅发布前/月度巡检）
pytest tests/ -v
```

### 7.7 定时调度

默认以 TRAE Schedule 为唯一调度源（v3.0.0+1 起 8 Active 全期货任务；内部 APScheduler 停用）。如需内部调度：

```bash
$env:FTS_INTERNAL_SCHEDULER_ENABLED='1'
fts scheduler run
```

---

## 8. 配置系统

配置优先级：**环境变量（FTS_\*）> YAML 配置文件 > 代码默认值**。

### 8.1 关键环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `FTS_DEFAULT_MARKET` | `futures`（代码默认，v3.0.0+1） | 全局默认市场。**⚠️ 注意：`config/settings.yaml` 第 7 行仍残留 `default_market: "energy"`（优先级高于代码默认）→ 未设 env 时实际生效值为 `energy`**；设 `FTS_DEFAULT_MARKET=futures` 即回到全期货工作流。该 YAML 漂移建议修复对齐 |
| `FTS_MEMORY_DIR` | `./memory` | 运行时持久化目录 |
| `FTS_LOG_LEVEL` | `INFO` | 日志级别 |
| `FTS_LLM_BACKEND` | - | LLM 后端（openai/anthropic/mock） |
| `FTS_EVOLUTION_MODE` | `operator_first` | 演化模式（operator/operator_first/code/hybrid/batch） |
| `FTS_INTERNAL_SCHEDULER_ENABLED` | `0` | 内部 APScheduler 开关（TRAE Schedule 为唯一调度源） |
| `FTS_ENERGY_QA_REVIEW_APPLY` | `0` | 能化链评审质检灰度开关（1=正式落库） |
| `FTS_REVIEW_LEGACY_APPLY` | `0` | 存量因子集中重审落库开关（v3.0.0，1=正式落库） |
| `FTS_QUANTDATA_HOME` | `D:\QuantData` | 权威数据仓库路径 |
| `FTS_L3_SIGNAL_STORE_DB` | - | L3 信号矩阵库路径 |
| `FTS_EXTRACTORS_CONFIG` | - | L1 提取器源注册表配置路径 |
| `FTS_STORAGE_WRITE_STRICT` | `1` | 存储域写路径契约严格模式（0=回退告警） |

### 8.2 配置文件

| 文件 | 职责 |
|---|---|
| [config/settings.yaml](file:///d:/Programs/factor_system/config/settings.yaml) | 全局配置（llm/演化/L3 verifier/子链/regime 等；default_market 已统一 futures） |
| [config/futures_universe.yaml](file:///d:/Programs/factor_system/config/futures_universe.yaml) | 品种池/产业链 SSOT（84 品种 + 17 产业链 + coverage_priority P0-P3 + energy 工作流） |
| [config/extractors.yaml](file:///d:/Programs/factor_system/config/extractors.yaml) | L1 知识源注册表 |
| docs/harness/_data/storage_landscape.yaml | 存储域注册表契约（SSOT 一数一源） |
| docs/harness/_data/operator_catalog.yaml | 算子目录 |
| docs/harness/_data/l3_regime_multipliers.yaml | Regime 倍率（family 概念移除后标记 deprecated 仅归档） |

---

## 9. 数据流与执行流程

### 9.1 L2 单代演化流程

```
UCT 父因子选择 → 演化分派（macro/GP/operator/deep/transformer）
  → 运行时校验（编译/执行）
  → 生成端去重（GAP-135 同表达式判定）
  → 快速预筛（CandidatePrefilter）
  → 候选准入链（CandidateProcessor）：
      横截面评估 → Verifier 三级判定 → 6 项审计 → 10 维质量评分
      → 消融/因果/鲁棒性/SHAP → 经验链记录 → 分级准入
  → 影子池 5 日观察 → DuckDB 先写（SSOT）→ JSON 快照备份
```

### 9.2 因子信号矩阵输出流程（FTS 保留能力，v3.0.0）

```
elite 因子（DuckDB，仅 approved + L2 评审放行）
  → 信号计算（SignalCache 复用 + align_signal_to_dates 向量化对齐）
  → build_signal_matrix（B 层：(n_dates, n_symbols, n_factors) 3D 矩阵）
  → persist_signal_matrix（D 层：l3_signal_store.duckdb，
      (code_hash, params_hash) 双哈希增量 + 信号契约 v1 三列
      schema_version/factor_status/factor_scope）
  → 增量窗口追加（load_or_build_signal_matrix：dates_digest 前缀判定
      + _W_RECALL=500 回退段 + _verify_append 抽样对照验证）
  → Regime-Driven 消费（决策/训练双模式隔离 + 新鲜度校验 + 降级熔断）
```

### 9.3 期货数据同步（Stage1-3）

```
sync_futures_data_job（工作日 17:30）
  Stage1 kline：17 字段 → kline_cache（QuantData/TDX_LOCAL 主源 + 增强层补充）
  Stage2 fundamental：9 字段 → futures_fundamental Parquet
  Stage3 term_structure：4 字段 → futures_term_structure Parquet
  → 字段覆盖校验（futures_field_consumption SSOT）+ gzip 摘要落盘
```

### 9.4 存量因子集中重审流程（v3.0.0）

```
review_legacy_factors.py
  Stage 0 清点分层：按现状态分层（active/shadow/degraded/deleted）
  → 分族 FDR-BH 校正（α=0.05，复检/转正/恢复三族独立）
  → audit 分层抽样（promote 100% / observe 30% / retire 10%）
  → 处置：复检/转正/恢复（dry-run 灰度，FTS_REVIEW_LEGACY_APPLY=1 才落库）
```

---

## 10. 测试体系

- **测试结构**：`tests/core` / `tests/data_sources` / `tests/factor_engine` / `tests/config` / `tests/cli` 等。全量回归 2026-08-20（v3.0.0 验收）实测 **8129 passed / 21 failed**（残留 21 项均为预存 GAP——GAP-158 工作区既有 portfolio_loop 本地改动 / pandas-3 只读 / regime_features，非 plans/57 引入），覆盖率 92%
- **slow 分级**：重量级真实演化/回测测试统一 `@pytest.mark.slow`（日常回归用 `-m "not slow"` 跳过，全量验收必跑）
- **回归测试政策**（2026-08-13 修订）：日常任务仅跑受影响的模块/集成测试；全量回归仅在发布前/晋级里程碑与每月底例行巡检执行
- **DuckDB 约束**：嵌入式单进程写约束下 xdist 多 worker 并发写会锁冲突，日常回归建议单进程执行
- **CI 护栏**：`.github/workflows/ci.yml` 含 `verify_factor_db_untouched.py`（真实因子库零污染快照/check）+ `verify_qa_workflow.py --synthetic`（质检 SOP 端到端）+ lint/mypy
- **关键测试文件**：`test_evolution_loop.py`（演化主循环）、`test_meta_loop.py`、`test_portfolio_loop.py`（L3，240+ 用例）、`test_panel_vector.py`（横截面全矩阵化）、`test_l3_signal_service`（信号矩阵）、`test_subchain_eval.py`（子链质量评估）、`test_energy_qa_review`（评审质检）、`test_aggregator.py`（多源聚合）、`test_regime_gate.py`（子链 Gate）、`test_dual_track.py`（v3.0.0 双轨对账）

---

## 11. 附录 A: 版本历史

当前版本 **v3.0.0+1**。近期关键版本（详见 [CHANGELOG.md](file:///d:/Programs/factor_system/CHANGELOG.md) 与 docs/harness/07-operations.md）：

| 版本 | 关键变更 |
|---|---|
| v3.0.0+2 | **配置漂移修复（2026-08-20）**：`config/settings.yaml` default_market 由残留的 `energy` 同步为 `futures`，消除 v3.0.0+1 代码默认与 YAML 实际生效值的不一致（03-configuration 示例本就为 futures，现全部对齐） |
| v3.0.0+1 | **定时任务全期货重建（2026-08-20）**：settings.default_market 代码默认反转回 futures；TRAE Schedule 删除 4 个 energy 专属任务、新建 5 个全期货任务（8 Active）；energy 因子库转候选池保留 |
| v3.0.0 | **双系统切分架构级发布（plans/57 阶段 0-13 完成，2026-08-20）**：FTS 角色重定位因子生产系统；L3 组合侧退役登记（retired_l3.py 35 项 + warn_if_retired）；信号契约 v1（F.3）；RD 承接 strategy_synthesis/combo_verifier/money_management/crowding_gate；全期货覆盖规划（coverage_priority P0-P3）；存量因子集中重审（review_legacy_factors.py 分族 FDR-BH）；验收 8129 passed（残留 21 项预存 GAP-158） |
| v2.105.0+33 | plans/57 双系统切分：阶段 1 真实双轨对账全门槛通过 + 步骤 12 L3 组合侧退役登记 |
| v2.105.0+32 | 权威数据源 QuantData（GAP-156）+ 外部因子常态化导入管道 |
| v2.105.0 | 能化链 L2 评审质检正式落库（FTS_ENERGY_QA_REVIEW_APPLY=1）、子链张量化 enabled、L3 权重重算评审硬过滤 |
| v2.105.0+16 | 批量子链质量评估工作流（subchain_eval，min_chain_ic 0.10→0.02 校准） |
| v2.105.0+18 | 反沉降通道（同面板指纹判定漂移拒绝落库） |
| v2.104.0+25 | CTA 手册 WorkFlow 端到端工作流（fts/workflow + Web UI） |
| v2.104.0+70 | L1 知识注入增强（plans/41：WebSearch 动态源 + 子链分批 bootstrap） |
| v2.104.0+111 | plans/48 Regime 分层方向 Gate |
| v2.104.0+112 | plans/49 因子×子链质量矩阵（Q10/F6 两级判定 + 单元粒度退化） |
| v2.103.0 | 股票管线剥离完成（fts-stock 独立项目）、E.3/E.4 存储收敛 |

---

## 12. 附录 B: 相关文档

- [README.md](file:///d:/Programs/factor_system/README.md) — 项目快速开始与 CLI 参考
- [AGENTS.md](file:///d:/Programs/factor_system/AGENTS.md) — 项目全局编码规范（四场景红线）
- [CLAUDE.md](file:///d:/Programs/factor_system/CLAUDE.md) — FTS 编码行为准则（HARNESS 工程规范）
- [CHANGELOG.md](file:///d:/Programs/factor_system/CHANGELOG.md) — 版本历史
- [docs/FTS_manual.md](file:///d:/Programs/factor_system/docs/FTS_manual.md) — FTS 使用手册
- `docs/harness/` — HARNESS 工程规范（01-architecture / 02-lifecycle / 06-testing / 07-operations / 08-gap-analysis / 09-advancement-plan 等）
- `docs/archive/design/` — 历史设计决策（A.1 质量卡 / A.2 衰减追踪 / D.1-D.2 模拟交易 / E.3 状态库 / E.4 连接生命周期 / **F.3 信号契约 v1** 等，已归档）
- `docs/archive/plans/` — 历史实施计划（29 存储收敛 / 37 面板向量化 / 40 L3 组合优化 / 41 L1 知识注入 / 45 L2 循环拆分 / 47-49 子链体系 / 51-52 信号矩阵 / 53-57 Regime 与双系统切分，已归档；索引见 `docs/archive/README.md`）
- [DISCLAIMER.md](file:///d:/Programs/factor_system/DISCLAIMER.md) — 免责声明
- [COMPLIANCE.md](file:///d:/Programs/factor_system/COMPLIANCE.md) — 开源合规指南
