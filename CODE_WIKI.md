# FTS (Factor Trading System) — Code Wiki

> **版本**: v2.14.0 | **最后更新**: 2026-08-06
>
> 本文档基于源代码分析生成，是 FTS 项目的代码级参考文档，面向开发者阅读。

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
   - 4.8 [风控层 `fts.risk`](#48-风控层-ftsrisk)
   - 4.9 [调度层 `fts.scheduler`](#49-调度层-ftsscheduler)
   - 4.10 [监控层 `fts.monitor`](#410-监控层-ftsmonitor)
   - 4.11 [LLM 客户端 `fts.llm`](#411-llm-客户端-ftsllm)
   - 4.12 [CLI 入口 `fts.cli`](#412-cli-入口-ftscli)
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

**FTS (Factor Trading System)** 是一个 AI 原生的量化因子智能系统，从 FDT 项目剥离的独立因子策略系统。专注于多因子挖掘、评估、组合与演化。

### 核心定位

```
数据源（DuckDB/TQ-Local/AKShare/腾讯/Wind/iFinD）
    ↓
FTS（因子智能系统 → 交易信号）
    ├── L1 Meta-Loop（每日知识补给 + Bootstrapping）
    ├── L2 Evolution Loop（因子演化 + 三级评估 + 质检 + 审计）
    ├── L3 Portfolio Loop（组合构建 + 信号产出 + 风控检查）
    └── 反馈闭环（归因分析 + 演化方向调整）
    ↓
下游消费系统（FDT / 手动执行）
```

### 核心能力

| 能力 | 说明 |
|------|------|
| **三层循环** | L1 Meta-Loop（每日 08:30 知识补给）+ L2 Evolution Loop（23:00 夜间演化）+ L3 Portfolio Loop（20:00 组合构建） |
| **多市场支持** | A 股、ETF、期货（25 核心 + 57 全量品种），按市场隔离演化路径 |
| **因子种子库** | 563 个种子因子（股票 482 + 期货 81），覆盖 14 大因子家族 |
| **FTS-Expr DSL** | 算子表达式语言，58 个算子 7 大类别（L0-L5 分层），支持表达式因子 |
| **GP 遗传规划** | 基于遗传规划的因子搜索引擎（锦标赛选择/交叉/变异/精英保留） |
| **算子演化引擎** | 独立于 GP 的算子空间进化搜索，ExprNode 层面交叉/变异 |
| **五层逻辑审查** | 消融实验 + 场景测试 + SHAP 分析 + 鲁棒性审查 + 因果验证 |
| **全自动调度** | 基于 APScheduler 的 L1/L2/L3 定时任务 + 进程看门狗 + 热重载 |
| **多源数据融合** | DuckDB → TQ_LOCAL → TQ_PYTHON → AKShare → SYNTHETIC 五级降级，5 种融合策略 |
| **因子质检** | 10 维质量评分卡（A/B/C 三级准入），6 项强制审计 |
| **回测流水线** | 4 阶段端到端回测（DataLoad → FactorCompute → Performance → Report），批量对比 |
| **反馈闭环** | 6 种反馈事件类型，5 种根因归因，演化方向自适应调整，月度迭代报告 |
| **风控系统** | 5 项风控规则（仓位/回撤/亏损/杠杆/集中度），拦截不合格信号 |
| **Prometheus 监控** | 数据源/因子/系统三维指标，HTTP 端点暴露，K8s 部署支持 |

### 技术栈

- **语言**: Python 3.10+
- **核心依赖**: numpy, pandas, scipy, pyyaml, shap
- **可选依赖**: optuna（演化）, openai/anthropic（LLM）, akshare（MCP 数据）, scikit-learn（组合构建）
- **数据存储**: DuckDB（因子目录 + 期货 K 线 `kline_cache` + `contract_kline` 两张表）
- **调度**: APScheduler
- **监控**: 纯标准库 HTTP 内置仪表盘 + Prometheus 端点（`/metrics`）
- **包管理**: setuptools, `pyproject.toml` 定义项目元数据

---

## 2. 系统架构

### 2.1 三层循环架构

```
┌─────────────────────────────────────────────────────────────────────┐
│                    L1 Meta-Loop（每日 08:30）                         │
│  agentic 市场感知 → debate 分析 → Bootstrapping Agent 链 →           │
│  L1 Verifier 判定 → 注入种子池 + factor_pool.json                    │
│ 输出：种子候选注入 L2 种子池                                          │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ 种子候选
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L2 Evolution Loop（每日 23:00）                       │
│  DataQualityMonitor 数据校验 → State 加载 → 熔断预检查                 │
│  循环（每代）:                                                       │
│    UCT 父因子选择 → 宏观演化（LLM）→ 微观演化（optuna 100 trials）    │
│    → 三级评估链（L1 回测 → L2 经济逻辑 → L3 多重检验）                │
│    → BacktestPipeline 回测验证 → Verifier 判定 → 质量评分卡（10 维）  │
│    → FactorAuditor 审计（6 项强制）→ 经验链记录 → 分级准入             │
│    → DuckDB 同步（idempotent write）                                  │
│  可选：GP 遗传规划 / 算子演化 / 代码演化                              │
└─────────────────────────────────┬───────────────────────────────────┘
                                  │ 精英因子
                                  ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  L3 Portfolio Loop（每日 20:00）                       │
│  加载精英因子（DuckDB / JSON 回退）→ 信号合成                         │
│  （equal_weight / sharpe_weight / elastic_net）                       │
│  → Regime 自适应权重调整 → 正交化 → 衰减检验 → 组合构建              │
│  （含粘性约束）→ Verifier 判定 → 漂移监控记录 → 风控检查              │
│  → 输出信号 / 注入 FDT                                               │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 五层逻辑审查框架

```
L1 输入敏感性     ablation.py         5 种消融模式（volume_zero / vwap_replace
                                       / time_shuffle / noise_inject / feature_permute）
L2 宏观行为       scenarios/          23 个典型市场场景
L3 局部可解释     shap_analyzer.py    SHAP 极端样本归因
L4 鲁棒性         robustness.py       对抗样本/缺失值/分布外
L5 因果结构       causal_validator.py 6 个预定义自然实验事件验证
```

### 2.3 数据流架构

```
期货数据源（5 级降级 K 线主路径）:
  DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
                    ↓
             字段增强层（并行，K 线主路径之后）:
               WIND   → settle / oi_change / 期权 IV/PCR
               IFIND  → EDB 宏观 / 产业链数据
                    ↓
              OHLCVFusion（5 种融合策略: MEDIAN/MEAN/WEIGHTED/
                           HIERARCHICAL/TRIMMED_MEAN）
                    ↓
              熔断器（每源连续 5 次失败 → UNAVAILABLE → 6h 冷却 → 探活恢复）
                    ↓
              交叉验证（≥2 源同日 close 差异 > 0.5% 记录 disagreement）
                    ↓
              FTSDataProvider（统一数据入口）
                    ↓
              因子引擎（三层循环）
                    ↓
              BacktestPipeline（4 阶段回测）
                    ↓
              RiskManager（5 项风控检查）
                    ↓
              交易信号（FactorSignal / ScoredSignal）
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
fts/                          # 核心源码包（pyproject.toml: include = ["fts*"]）
├── __init__.py               # 包初始化，版本定义 v2.14.0，自动加载 .env
├── cli.py                    # 统一命令行入口（argparse，约 1250 行）
│
├── config/                   # 配置系统
│   ├── __init__.py           # 导出 FTSConfig / 评分卡配置
│   ├── settings.py           # FTSConfig 数据类，YAML+环境变量+默认值（dataclass）
│   └── factor_quality_card_config.py  # 质量评分卡维度权重/阈值配置（预设/自定义）
│
├── core/                     # 核心契约层
│   ├── __init__.py
│   ├── enums.py              # FTS 特有枚举（EvolutionStage/FactorPriority/FactorStatus）
│   ├── contracts.py          # 因子引擎契约重导出（from factor_engine.contracts）
│   └── atomic.py             # 原子文件写入/读取（临时文件+rename+备份轮转）
│
├── factor_engine/            # 因子引擎（核心模块，约 50+ 文件）
│   ├── __init__.py           # 导出所有核心组件（约 440 行）
│   ├── contracts.py          # L1/L2/L3 三层 TypedDict 契约（约 780 行）
│   ├── factor_program.py     # 因子程序接口（安全沙箱编译执行，按 kind 分派）
│   ├── seed_pool.py          # 种子池管理（9 内置 + 473 外部种子，按市场隔离）
│   ├── seed_loader.py        # YAML 种子因子加载器（code/expression/fundamental 三类型）
│   ├── macro_evolution.py    # 宏观演化（LLM 改因子逻辑）
│   ├── micro_evolution.py    # 微观演化（optuna 贝叶斯调参）
│   ├── evaluation_chain.py   # 三级评估链（L1 回测/L2 经济逻辑/L3 多重检验）
│   ├── verifier.py           # Verifier 协议（锁定评估机制，初始化后不可修改）
│   ├── standardizer.py       # 6 种标准化方法
│   ├── experience_chain.py   # 经验链存储（成功/失败轨迹，满 100 淘汰最旧 20）
│   ├── state.py              # 演化状态管理 + trace_id 全链路生成
│   ├── evolution_loop.py     # L2 主循环（夜间因子演化，UCT 父因子选择）
│   ├── meta_loop.py          # L1 主循环（每日知识补给 + Bootstrapping）
│   ├── portfolio_loop.py     # L3 主循环（组合构建 + 正交化 + 信号产出）
│   ├── monitor.py            # 循环状态检查（底层 check_loop/check_all）
│   ├── program.py            # 程序配置与加载
│   ├── factor_quality_card.py # 因子质量评分卡（10 维评分，0-50 分）
│   ├── audit.py              # 6 项强制审计（渐进式，缺失项 skipped）
│   ├── factor_inspector.py   # 因子定时巡检与自动降级
│   ├── ablation.py           # 消融实验（5 种模式）
│   ├── shap_analyzer.py      # SHAP 分析
│   ├── robustness.py         # 鲁棒性审查
│   ├── causal_validator.py   # 因果结构验证（6 个预定义事件）
│   ├── walk_forward.py       # 走航验证（多窗口样本外稳定性验证）
│   ├── stress_test.py        # 压力测试
│   ├── regime.py             # Market Regime 检测（5 种状态：bull/bear/oscillate/high_vol/low_vol）
│   ├── cost_model.py         # 成本模型
│   │
│   ├── expr_dsl/             # FTS-Expr DSL 算子表达式语言（Phase C.2）
│   │   ├── __init__.py       # 统一导出 12 个核心函数/类
│   │   ├── ast.py            # ExprNode AST 定义（op/args/kind 三字段）
│   │   ├── parser.py         # 表达式解析器（字符串 → ExprNode AST）
│   │   ├── registry.py       # 58 算子注册表（L0-L5 分层，复用 feature_ops）
│   │   ├── validator.py      # 静态验证器（类型检查+参数合法性+PIT max_lookback）
│   │   ├── executor.py       # 运行时执行器（解释 AST，向量化计算）
│   │   ├── compiler.py       # 表达式编译为 Python 代码
│   │   ├── factory.py        # 因子工厂（create_operator_factor）
│   │   └── runtime.py        # 运行时模块（eval_fts_expr 入口，沙箱白名单唯一放行 FTS 模块）
│   │
│   ├── factor_db/            # DuckDB 因子数据库
│   │   ├── __init__.py
│   │   ├── schema.py         # 表结构定义（factor_catalog/evaluations/versions/correlations）
│   │   ├── repository.py     # CRUD 操作 + 批量查询 + 排行榜 + 多样性选择
│   │   ├── lineage.py        # 因子血缘追踪（血缘查询/评估趋势/质量退化检测/批量审计）
│   │   └── migrate_from_json.py  # JSON 迁移脚本
│   │
│   ├── seed_data/            # 外部种子因子数据源
│   │   ├── __init__.py       # 导出 6 个加载函数
│   │   ├── wq101.py          # 101 个 WorldQuant Alpha 因子
│   │   ├── qlib158.py        # 158 个 Qlib 因子
│   │   ├── gtja191.py        # 191 个国泰君安 Alpha 因子
│   │   ├── fundamental_seeds.py  # 23 个基本面/另类/宏观因子
│   │   ├── alpha_ops.py      # Alpha 算子库
│   │   ├── alpha_ops_numba.py # Numba 加速算子
│   │   └── loader.py         # 动态加载器（load_all_external_seeds 等）
│   │
│   ├── seed_data_futures_full.py  # 期货种子因子完整加载
│   │
│   ├── feature_ops.py        # 特征工程算子引擎（C.1，50+ 算子 6 大类）
│   ├── feature_importance.py # 特征重要性分析
│   ├── gp_evolver.py         # GP 遗传规划搜索引擎（C.1，TreeNode/ExpressionTree/GPEvolver）
│   ├── operator_evolution.py # 算子演化引擎（ExprNode 层面交叉/变异，独立于 GP）
│   │
│   ├── backtest_pipeline.py  # 回测管线（B.2，4 阶段流水线）
│   ├── factor_screener.py    # 因子筛选器
│   ├── signal_generator.py   # 信号生成器
│   ├── portfolio_constructor.py  # 组合构建器
│   ├── cost_simulator.py     # 成本模拟器
│   ├── risk_attributor.py    # 风险归因器
│   ├── report_generator.py   # 报告生成器
│   ├── capital_allocator.py  # 资金分配器
│   ├── signal_contract.py    # 实盘信号契约（C.2，FactorSignal/SignalValidator）
│   ├── feedback_loop.py      # 反馈闭环（C.3，6 种事件类型/5 种根因）
│   ├── adaptive_weight.py    # 自适应权重
│   └── factor_optimizer.py   # 因子优化器
│
├── pipeline/                 # 因子推演管线
│   ├── __init__.py
│   ├── base.py               # FactorPipeline 抽象基类 + ProcessingStage 协议
│   ├── factor_combiner.py    # 多因子加权/融合器
│   ├── factor_quality_inspection.py  # 因子质检过滤层
│   └── batch_quality_inspector.py    # 批量质检
│
├── strategies/               # 策略层
│   ├── rules/                # 策略规则知识库
│   │   └── __init__.py
│   ├── __init__.py
│   ├── base_v2.py            # v2 策略可插拔框架（compute → filter → score 三段式）
│   ├── multi_factor_strategy.py  # 四维因子加权打分策略
│   └── strategy_evolution.py     # 策略进化（制度自适应/动态权重/多周期融合）
│
├── risk/                     # 风控层（C.2）
│   ├── __init__.py           # 导出 RiskManager / TradeAdapter
│   ├── risk_manager.py       # 5 项风控规则检查（仓位/回撤/亏损/杠杆/集中度）
│   ├── trade_adapter.py      # 交易适配器抽象基类（Liskov 替换原则）
│   └── simulated_adapter.py  # 模拟交易适配器
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
│   ├── data_quality_monitor.py  # 数据质量监控（IC 漂移/容量突变告警）
│   ├── elite_tracker.py      # 精英因子样本外跟踪（状态转换: active→decaying→retired）
│   ├── http_server.py        # 内置 Web UI 仪表盘（纯标准库，端口 9100）
│   ├── live_factor_monitor.py # 实盘因子监控（C.2）
│   ├── logic_monitor.py      # 因子行为逻辑监控（contract_switch/drift/extreme_prediction）
│   ├── k8s_deploy.py         # K8s 部署配置
│   ├── prometheus_metrics.py # Prometheus 指标注册表
│   └── prometheus_setup.py   # Prometheus 监控集成
│
├── data.py                   # FTSDataProvider（统一数据入口，组合 MCP/基本面/期货提供者）
├── data_futures.py           # FuturesDataProvider（DuckDB kline_cache + AKShare）
├── data_futures_fundamental.py  # 期货基本面数据（库存/基差/仓单）
├── data_fundamental.py       # A 股基本面数据层（估值/财务/宏观）
├── data_mcp.py               # MCP 数据适配层（腾讯 HTTP API qt.gtimg.cn）
├── data_mcp_bridge.py        # MCP 桥接层
├── data_cache.py             # 数据缓存
├── llm.py                    # LLM 客户端统一接口（OpenAI/Anthropic/Mock）
└── talib_bridge.py           # TA-Lib 桥接

data_sources/                 # 期货多源数据适配器（v2.3.0）
├── __init__.py
├── base.py                   # BaseFuturesSource 抽象基类（3 个抽象方法）
├── aggregator.py             # FuturesDataAggregator（多源调度 + 熔断器 + 交叉验证）
├── fusion.py                 # OHLCVFusion（5 种融合策略 + 源权重配置）
├── tq_source.py              # 通达信本地数据源（TQ_LOCAL / TQ_PYTHON 两种路径）
├── wind_source.py            # Wind 数据源（字段增强层）
├── ifind_source.py           # iFinD 数据源（字段增强层）
└── migrate.py                # 数据库迁移

seeds/                        # YAML 种子因子定义文件
├── stock/                    # 股票种子因子 YAML
│   ├── builtin.yaml          # 9 个内置因子
│   ├── wq101.yaml            # 101 个世坤 Alpha
│   ├── qlib158.yaml          # 158 个 Qlib 因子
│   ├── gtja191.yaml          # 191 个国泰君安 Alpha
│   └── fundamental.yaml      # 23 个基本面因子
└── futures/                  # 期货种子因子 YAML（15 个文件）
    ├── momentum.yaml         # 动量因子
    ├── volatility.yaml       # 波动率因子
    ├── term_structure.yaml   # 期限结构因子
    ├── high_frequency.yaml   # 高频因子
    └── ...（共 15 个文件）

config/                       # 配置文件
├── settings.yaml             # FTS 全局配置 YAML
├── prometheus.yml            # Prometheus 配置
├── prometheus_alerts.yml     # Prometheus 告警规则
└── alertmanager.yml          # Alertmanager 配置

deploy/k8s/                   # K8s 部署配置（8 个文件）
├── 00-namespace.yml
├── 01-configmap-fts-prometheus-config.yml
├── 02-configmap-fts-prometheus-alerts.yml
├── 03-configmap-fts-alertmanager-config.yml
├── 04-deployment-fts-prometheus.yml
├── 05-deployment-fts-alertmanager.yml
├── 06-service-fts-prometheus.yml
├── 07-service-fts-alertmanager.yml
└── 08-ingress-fts-monitoring.yml

tests/                        # 测试目录（103+ 文件，2900+ 用例）
├── core/                     # 核心契约层测试（3 个文件）
├── data_sources/             # 数据源测试（7 个文件）
├── factor_engine/            # 因子引擎测试（54 个文件，核心）
│   ├── expr_dsl/             # DSL 测试（6 个文件）
│   ├── factor_db/            # 数据库测试
│   └── operator_evolution/   # 算子演化测试
├── monitor/                  # 监控测试（8 个文件）
├── pipeline/                 # 管线测试（5 个文件）
├── scheduler/                # 调度测试（5 个文件）
├── strategies/               # 策略测试（3 个文件）
├── scenarios/                # 场景测试（2 个文件）
├── cli/                      # CLI 测试（1 个文件）
└── 根目录                    # 端到端/数据/HTTP/监控/LLM 等（10+ 文件）

scripts/                      # 工具脚本（50+ 个）
├── verify_doc_consistency.py # 文档一致性校验
├── daily_signal_pipeline.py  # 日线信号管道
├── futures_signal_pipeline.py # 期货信号管道
├── run_futures_evolution.py  # 期货演化运行
├── sync_futures_data.py      # 期货数据同步
└── ...（共 50+ 脚本）

docs/                         # 项目文档
├── harness/                  # HARNESS 工程规范目录
│   ├── 01-architecture.md    # 架构文档
│   ├── 02-lifecycle.md       # 生命周期
│   ├── ...（共 13+ 规范文档）
│   ├── design/               # 设计文档（A.1-C.4 共 10 个设计）
│   └── acceptance/           # 验收测试文档
├── FTS_manual.md             # 完整用户手册
├── business_flow.md          # 业务流程说明
└── production_plan.md        # 生产就绪计划

memory/                       # 运行时持久化（自动创建）
├── evolution/                # L2 演化状态
├── meta_loop/                # L1 状态
├── portfolio/                # L3 状态
├── tracking/                 # 精英因子跟踪
└── knowledge/factors/        # 因子存储
    ├── elite/                # 股票精英因子
    └── futures_elite/        # 期货精英因子
```

---

## 4. 核心模块详解

### 4.1 核心契约层 `fts.core`

**文件**: `fts/core/`

**职责**: 定义 FTS 特有的核心枚举和原子操作，因子引擎的完整契约由 `fts.factor_engine.contracts` 提供，本层只做 re-export。

#### `fts.core.enums`

| 枚举 | 值 | 说明 |
|------|-----|------|
| `EvolutionStage` | `l0_human` / `l1_meta_loop` / `l2_evolution` / `l3_portfolio` | 因子演化阶段标识 |
| `FactorPriority` | `high` / `medium` / `low` | 因子优先级（基于 L1 debate_gap + 经济逻辑） |
| `FactorStatus` | `pending` / `injected` / `decayed` / `rejected` | 种子池因子状态 |

#### `fts.core.atomic`

| 函数 | 签名 | 说明 |
|------|------|------|
| `atomic_write` | `(path, data, *, make_dir=True, encoding="utf-8")` | 原子写入 JSON（临时文件 + `os.replace` rename） |
| `atomic_read` | `(path, *, default=None, encoding="utf-8")` | 安全读取 JSON（不存在/不合法返回 default） |
| `atomic_write_state` | `(path, state, *, backup_count=3)` | 原子写入状态文件 + 备份轮转（`.bak.0`, `.bak.1`, ...） |

#### `fts.core.contracts`

Re-export `fts.factor_engine.contracts` 中的所有契约，提供统一导入入口。包含：
- 版本号: `EVOLUTION_VERSION`
- 因子程序契约: `FactorProgram`, `FactorSignature`, `EconomicLogic`
- 评估契约: `BacktestMetrics`, `EconomicScore`, `MultipleTestResult`, `FactorEvaluation`
- L1 契约: `SeedCandidate`, `L1MetaLoopState`, `FactorPool`, `L1VerifierConfig`
- L3 契约: `PortfolioSignal`, `PortfolioCombo`, `L3VerifierConfig`, `StickyConfig`, `DriftMetrics`
- 默认配置: `DEFAULT_VERIFIER_CONFIG`, `DEFAULT_BUDGET_CONFIG`, `DEFAULT_L1_VERIFIER_CONFIG`, `DEFAULT_L3_VERIFIER_CONFIG`

---

### 4.2 因子引擎 `fts.factor_engine`

**文件**: `fts/factor_engine/`（约 50+ 文件，核心模块）

**职责**: FTS 的核心，实现 L1/L2/L3 三层循环 + 因子全生命周期管理。

#### 4.2.1 契约层 `contracts.py`

定义所有核心 TypedDict 契约（约 780 行），是 HARNESS §契约优先的核心。

**版本号**: `EVOLUTION_VERSION` 动态读取自 `fts.__version__`。
**状态 schema 版本**: `STATE_SCHEMA_VERSION = "1"`（字段变更时手动递增，用于冷启动决策）

**市场与家族枚举**:

| 类型 | 说明 |
|------|------|
| `FactorMarket` | `Literal["futures", "stock", "etf", "bond", "multi"]` |
| `FactorFamily` | `Literal["trend", "mean_reversion", "carry", "seasonality", "cross_section", "fundamental", "technical", "microstructure", "macro", "behavioral", "liquidity", "volatility", "volume", "multi_factor", "other"]`（14 大类） |

**因子表达类型**:

| 枚举 | 说明 |
|------|------|
| `FactorKind` | `OPERATOR`（算子表达式）/ `CODE`（代码级因子）/ `HYBRID`（算子外壳+代码内核，预留） |

**L1/L2/L3 核心契约**:

| 契约 | 用途 | 关键字段 |
|------|------|---------|
| `FactorProgram` | 因子程序定义 | factor_id, name, code, params, signature, economic_logic, source, parent_id, generation, created_at, trace_id, risk_tag, market, family, symbols, factor_version, is_multi_symbol, kind, expression, operator_depth, operator_count, max_lookback |
| `FactorSignature` | 因子输入/输出签名 | input_fields, output_type, frequency, lookback |
| `EconomicLogic` | 四维经济逻辑评分 | theory(0-5), behavioral(0-5), microstructure(0-5), institutional(0-5), narrative |
| `BacktestMetrics` | L1 回测指标 | ic, icir, sharpe, max_drawdown, monotonicity, turnover, oos_ic, oos_icir, walk_forward |
| `EconomicScore` | L2 经济逻辑评分 | theory, behavioral, microstructure, institutional, dimensions_passed |
| `MultipleTestResult` | L3 多重检验 | bonferroni_p, fdr_q, adjusted_t, passed |
| `FactorEvaluation` | 三级评估链输出 | factor_id, factor_name, level_1_backtest, level_2_economic, level_3_multiple, passed, evaluated_at, trace_id |
| `ExperienceTrace` | 经验链轨迹 | trace_id, factor_id, parent_id, mutation_type, evaluation, success, lessons, failure_reasons |
| `EvolutionState` | 演化状态 | run_id, status, last_generation, generation, tokens_consumed, best_ic, best_sharpe, elite_count, start_time, trace_id |
| `VerifierConfig` | Verifier 配置 | min_ic, min_sharpe, max_drawdown, max_turnover, min_monotonicity, min_oos_ic |
| `VerifierResult` | Verifier 判定结果 | passed, failure_reasons, checked_against, checked_at |
| `BudgetConfig` | 预算配置 | nightly_token_limit, max_generation, circuit_breaker_* |

**L1 特有契约**:

| 契约 | 用途 | 关键字段 |
|------|------|---------|
| `SeedCandidate` | L1 种子候选 | candidate_id, name, code, params, signature, economic_logic, source, parent_topic |
| `L1MetaLoopState` | L1 状态 | run_id, status, candidates_count, tokens_consumed, last_run_at |
| `FactorPoolEntry` | 因子池条目 | factor_id, priority, status, injected_at |
| `FactorPool` | 因子池顶层 | version, factors, total_count, last_updated |
| `L1VerifierConfig` | L1 宽松 Verifier | min_economic_score, min_narrative_length, max_candidates_per_run |
| `L1VerifierResult` | L1 判定结果 | passed, failure_reasons, checked_against, checked_at |

**L3 特有契约**:

| 契约 | 用途 | 关键字段 |
|------|------|---------|
| `PortfolioSignal` | 信号合成输出 | factor_id, name, weight, sharpe, ic, turnover, decay_6m, retained |
| `PortfolioCombo` | 组合构建输出 | combo_id, signals, combo_sharpe, combo_turnover, max_correlation, synthesis_mode, created_at, trace_id, n_factors |
| `AgentOptimizationProposal` | 优化建议 | proposal_id, agent_name, suggested_changes, rationale |
| `L3VerifierConfig` | L3 Verifier 配置 | min_sharpe, max_correlation, max_turnover, max_decay_rate, min_n_factors |
| `StickyConfig` | 组合粘性约束 | enabled, max_delta, new_factor_cap, adaptation_speed |
| `DriftMetrics` | 漂移监控 | member_overlap_rate, weight_l1_change, new_factor_ratio |

#### 4.2.2 因子程序 `factor_program.py`

**职责**: 因子代码的图灵完备表示 + 安全沙箱编译执行。支持 OPERATOR 和 CODE 两种因子类型分派。

| 关键类/函数 | 签名 | 说明 |
|------------|------|------|
| `FactorCompileError` | `Exception` | 因子程序编译/验证失败异常 |
| `FactorExecutor` | `class` | 因子执行器，`execute(factor, data)` 按 `kind` 分派：CODE 走沙箱 `exec`，OPERATOR 走 DSL runtime |
| `create_factor_program` | `(name, code, params, signature, economic_logic, ...) → FactorProgram` | 创建 FactorProgram 实例 |
| `generate_factor_id` | `(name, code) → str` | 生成唯一因子 ID（`fct_<8hex>`，基于 name + code + secrets 随机熵 SHA1 哈希） |
| `validate_factor_code` | `(code) → tuple[bool, list[str]]` | 安全沙箱验证（AST 静态分析，检查语法/函数定义/黑名单 import/黑名单内置函数） |

**安全沙箱约束**:
- `ALLOWED_IMPORTS`: `{"numpy", "np", "pandas", "pd", "scipy", "statsmodels", "talib", "math", "statistics"}`
- `FORBIDDEN_NAMES`: `{"open", "exec", "eval", "compile", "globals", "locals", "getattr", "setattr", ...}`
- `FORBIDDEN_MODULES`: `{"os", "sys", "subprocess", "socket", "requests", "ctypes", "pickle", ...}`
- 沙箱放行唯一 FTS 模块: `fts.factor_engine.expr_dsl.runtime`（用于 OPERATOR 类型因子执行）

#### 4.2.3 种子池 `seed_pool.py` & `seed_loader.py`

**职责**: 管理种子因子池，提供 L2 演化的初始种群。按市场隔离（股票/期货）。

| 关键类/函数 | 说明 |
|------------|------|
| `SeedPool` | 种子池管理（`add/remove/list/get`，按市场隔离，支持相关性预检） |
| `get_default_seed_pool(market)` | 获取默认种子池（`futures` 或 `stock`） |
| `compute_seed_correlations(seed_pool)` | 种子因子相关性计算 |
| `compute_cross_section_correlations(seed_pool, panel)` | 横截面相关性计算 |
| `load_all_yaml_seeds(trace_id)` | 从 `seeds/` 目录 YAML 加载所有种子 |
| `load_factors_from_dir(directory)` | 从目录加载 YAML 种子 |
| `load_factors_from_yaml(filepath)` | 从单个 YAML 文件加载因子 |
| `verify_yaml_integrity()` | 校验 YAML 文件完整性 |

**种子因子来源**:
- 股票: 9 内置 + 101 WQ101 + 158 Qlib + 191 GTJA + 23 基本面 = **482 个**
- 期货: **81 个**（14 大因子家族，覆盖动量/波动率/期限结构/高频等）

**9 个内置种子因子**: momentum, volatility_reversion, volume_flow, macro_regime, rate_proxy, pmi_proxy, value_factor, quality_factor, size_factor

#### 4.2.4 宏观演化 `macro_evolution.py`

**职责**: LLM 驱动的因子逻辑变更（宏观演化）。

| 关键类/函数 | 说明 |
|------------|------|
| `MacroEvolver` | 宏观演化器（`evolve(factor, experience_chain, llm_client) → FactorProgram`，LLM 改逻辑） |
| `MockLLMClient` | 模拟 LLM 客户端（测试用，继承自 `fts.llm.LLMClient`） |
| `get_default_llm_client()` | 获取默认 LLM 客户端（通过 `fts.llm.get_llm_client()` 自动检测） |

#### 4.2.5 微观演化 `micro_evolution.py`

**职责**: optuna 贝叶斯调参（微观演化）。

| 关键类/函数 | 说明 |
|------------|------|
| `evolve_micro(factor, data, forward_returns, n_trials)` | 微观演化主函数，返回优化后的 FactorProgram |
| `optimize_params(factor, data, forward_returns, n_trials)` | 参数优化，返回优化后的 params dict |

#### 4.2.6 三级评估链 `evaluation_chain.py`

**职责**: agentic 三级评估链（L1 回测 → L2 经济逻辑 → L3 多重检验），可选 WalkForward。

| 关键类/函数 | 说明 |
|------------|------|
| `EvaluationChain` | 三级评估链编排器。`evaluate(factor, data, forward_returns) → FactorEvaluation` |
| `_compute_ic(signal, fwd_ret, method)` | 计算 IC 和 ICIR（spearman/pearson，常数输入返回 0.0） |
| `_compute_sharpe(returns, periods_per_year)` | 计算年化夏普比率（假设无风险利率=0） |
| `_compute_max_drawdown(cumulative)` | 计算最大回撤（0~1，净值法） |
| `_check_monotonicity(signal, returns, n_buckets)` | 检查因子信号预测单调性（Spearman 秩相关 >= 0.5） |
| `_evaluate_level_1(factor, data, forward_returns)` | L1 回测验证（IC/ICIR/Sharpe/MDD/单调性/样本外/换手率） |
| `_evaluate_level_2(factor, llm_client)` | L2 经济逻辑评分（4 维 LLM 评分 0-5） |
| `_evaluate_level_3(factor, data)` | L3 多重检验校正（Bonferroni/FDR/调整 t 统计量） |
| `cross_section_evaluate_backtest(factor, panel, ...)` | 横截面回测评估（期货多品种） |

**L1 阈值**: IC > 0.03, Sharpe > 1.5, 单调性 >= 0.5, 样本外 >= 30%

#### 4.2.7 Verifier `verifier.py`

**职责**: 锁定的评估机制 — 初始化后不可修改。

| 关键类 | 说明 |
|-------|------|
| `FactorVerifier` | 锁定 Verifier，`__init__` 时 `_locked = True`，`check(evaluation) → VerifierResult` |
| `VerifierAlreadyLockedError` | 锁定后修改配置抛出 |
| `VerifierNotLockedError` | 未锁定时调用 `check()` 抛出 |
| `get_global_verifier()` | 获取全局 Verifier 单例 |

**核心原则**: Verifier 一旦锁定，任何 LLM 调用、参数演化、人类干预都不可修改判定逻辑。

#### 4.2.8 L2 Evolution Loop `evolution_loop.py`

**职责**: 夜间因子演化主循环（UCT 父因子选择 + 宏观/微观演化 + 评估 + 质检 + 晋升）。

| 关键类 | 说明 |
|-------|------|
| `EvolutionLoop` | L2 演化循环主类。初始化时配置 Verifier/SeedPool/MacroEvolver/EvaluationChain/ExperienceChain/FactorAuditor/BacktestPipeline 等 |
| `EvolutionRunResult` | 演化运行结果（run_id, trace_id, generations_completed, total_factors_evaluated, elite_factor_ids, new_elite_count, avg_ic, avg_sharpe, circuit_broken, error） |

**`EvolutionLoop.__init__` 构造函数参数**:
- `memory_dir`, `elite_dir`, `seed_pool`, `verifier`, `llm_client`, `macro_evolver`, `eval_chain`, `experience_chain`, `state_manager`, `config`, `budget`, `auditor`, `inspector`, `pipeline`, `tracker`, `data_provider`, `market`

**`EvolutionLoop.run()` 执行流程**:
1. 加载/初始化演化状态 + DataQualityMonitor 数据完整性校验
2. 熔断预检查（token 预算/失败率/连续低 IC）
3. **循环**（每代）:
   a. `_select_parent_uct()` — UCT 树搜索选择父因子（UCB 算法，探索常数 C=1.0）
   b. 宏观演化 — LLM 修改因子逻辑
   c. 微观演化 — optuna 100 次 trial
   d. 三级评估链 — L1 回测 / L2 经济逻辑 / L3 多重检验
   e. 回测管线验证 — BacktestPipeline
   f. Verifier 判定 — 6 项检查
   g. 质量评分卡 — 10 维评分，A/B/C 分级
   h. FactorAuditor — 6 项强制审计，阻断不合格晋升
   i. 经验链记录
   j. 分级准入（A/B 级晋升精英，C 级淘汰）
   k. 影子池机制 — 新晋升因子先进入影子池观察 5 个交易日
   l. 状态持久化 + 精英因子 DuckDB 同步（idempotent write）
4. 生成精英因子质量报告 + EliteFactorTracker 重新评估

**UCT 选择**:
- `UCT_EXPLORATION_C = 1.0`
- `_select_parent_uct()` — 平衡探索与利用
- `_update_uct_stats(parent_id, child_result)` — 更新父因子 UCT 统计

#### 4.2.9 L1 Meta Loop `meta_loop.py`

**职责**: 每日知识补给 + Bootstrapping + debate 分析。

| 关键类 | 说明 |
|-------|------|
| `MetaLoop` | L1 元循环主类。`run() → MetaRunResult` |
| `MetaRunResult` | 元循环运行结果（run_id, trace_id, candidates_generated, candidates_injected, error） |
| `L1Verifier` | L1 宽松 Verifier（`check(candidate, seed_pool) → L1VerifierResult`，判定维度：economic_logic >= 2/4 + is_executable + not_duplicate + narrative >= 20 字） |
| `MetaStateManager` | L1 状态管理（`load_or_init() / save()`） |
| `FactorPoolManager` | 因子池管理（`load() / save() / add_entry()`） |
| `DebateQualityAnalyzer` | 辩论质量分析器 |
| `BootstrappingChain` | Bootstrapping 链（提取Agent → 验证Agent → 代码生成Agent 链） |

**`MetaLoop.run()` 执行流程**:
1. `_perceive_market()` — agentic 市场感知（Web 收集市场快照）
2. `_analyze_debate()` — debate 分析（识别薄弱维度）
3. `_run_bootstrap()` — Bootstrapping 生成候选因子（LLM bootstrap_factors）
4. `_verify_and_inject()` — L1 Verifier + 注入种子池

#### 4.2.10 L3 Portfolio Loop `portfolio_loop.py`

**职责**: 组合构建 + 信号产出 + 粘性约束 + 漂移监控。

| 关键类/函数 | 说明 |
|------------|------|
| `PortfolioLoop` | L3 组合循环主类。`run() → PortfolioRunResult` |
| `PortfolioRunResult` | 组合运行结果（run_id, trace_id, combo_id, n_signals, n_factors, combo_sharpe, max_correlation, combo_turnover, approved, error） |
| `L3Verifier` | L3 组合 Verifier（判定维度：combo_sharpe >= min_sharpe, max_correlation <= max_correlation, combo_turnover <= max_turnover, decay_6m <= max_decay_rate, n_factors >= min_n_factors） |
| `synthesize_signals(factors, mode)` | 信号合成（equal_weight / sharpe_weight / elastic_net） |
| `orthogonalize_factors(signals, data)` | 因子正交化（非 elastic_net 模式） |
| `decay_test(signals, lookback)` | 衰减检验（6 个月衰减率 > 0.3 剔除） |
| `build_combo(signals, prev_combo, sticky_config)` | 组合构建（含粘性约束） |
| `generate_agent_proposals(combo, trace_id)` | 生成 Agent 优化建议 |
| `load_elite_factors(elite_dir, market)` | 加载精英因子（含去重） |
| `inject_to_fdt(combo, proposals, memory_dir)` | 注入 FDT |

**影子池**: `SHADOW_OBSERVE_TRADING_DAYS = 5` — L2 新晋升因子先进入影子池观察，期满后才进正式组合。

**`PortfolioLoop.run()` 执行流程**:
1. 加载精英因子（DuckDB 或 JSON 回退）
2. 信号合成
3. Regime 自适应权重调整（可选）
4. 因子正交化（非 elastic_net 模式）
5. 衰减检验
6. 组合构建（含粘性约束）
7. Verifier 判定
8. 漂移监控记录
9. 注入 FDT / 生成信号（期货模式自动触发信号管道）

#### 4.2.11 因子质量评分卡 `factor_quality_card.py`

**职责**: 10 维因子质量评分，0-50 分，A/B/C 三级准入。

| 关键类/函数 | 说明 |
|------------|------|
| `FactorQualityCard` | 评分卡计算器。`evaluate(factor_id, ic, sharpe, walk_forward_result, decay_rate, turnover, correlation_max, logic_score, data_frequency, cross_symbol_coverage, capacity_estimate) → FactorQualityScore` |
| `FactorQualityCardConfig` | 评分卡配置（max_per_dimension, total_max, grade_A_threshold, grade_B_min, decay_discount_rate） |
| `FactorQualityScore` | 评分结果（score_id, factor_id, total_score, dimension_scores, evaluated_at, score_version, grade） |
| `DimensionScore` | 单维度评分（name, raw_value, score(0-5), description） |
| `compute_total_score(scores, weights)` | 计算加权总分 |
| `determine_grade(total_score, config)` | 确定等级（A/B/C） |

**10 维度评分**:

| 维度 | 权重 | 说明 |
|------|------|------|
| IC 得分 | 1.0 | IC/ICIR 指标 |
| Sharpe 得分 | 1.0 | Sharpe/Calmar 比率 |
| 稳定性 | 0.8 | WalkForward 结果（4 分量：IC 一致性+IC 波动率+综合评分+窗口数量） |
| 鲁棒性 | 0.8 | 衰减率 |
| 容量 | 0.6 | 容量估算 |
| 交易性 | 0.6 | 换手率（自动格式检测：≤10 小数模式，>10 百分比模式） |
| 多样性 | 0.5 | 最大相关性 |
| 逻辑性 | 0.5 | 经济逻辑分 |
| 实时性 | 0.4 | 数据频率 |
| 兼容性 | 0.4 | 跨品种覆盖率 |

**分级阈值**: A ≥ 35（默认）, B ≥ 25（默认）, C < 25（期货放宽：B ≥ 24）

#### 4.2.12 因子审计 `audit.py`

**职责**: 6 项强制审计，阻断不合格因子晋升精英池。

| 关键类 | 说明 |
|-------|------|
| `FactorAuditor` | 审计执行器。`audit(factor, data, forward_returns, symbol_ic_map, ...) → FactorAuditReport` |
| `FactorAuditConfig` | 审计配置 |
| `FactorAuditReport` | 审计报告（factor_id, factor_name, audited_at, items, passed, pass_rate, summary, failure_analysis） |
| `AuditItemResult` | 单项审计结果（name, status(passed/failed/skipped), evidence, score, details） |

**6 项审计**:
1. 因果检验（Granger / 反事实分析，通过 `CausalValidator`）
2. 样本外验证（WalkForward OOS，通过 `WalkForwardOptimizer`）
3. 跨品种验证（≥80% 品种 IC 为正）
4. 压力测试（极端行情下表现，通过 `StressTester`）
5. 多重检验（Bonferroni / FDR 校正）
6. 数据窥探检验（无未来函数）

**渐进式审计**: 每个审计项独立传入所需数据，缺失时标记 `skipped`，不阻塞流程。

#### 4.2.13 因子巡检 `factor_inspector.py`

**职责**: 定时巡检精英因子，自动降级退化因子。

| 关键类 | 说明 |
|-------|------|
| `FactorInspector` | 巡检执行器。`inspect_and_downgrade(threshold, commit)` — 巡检 + 降级 |
| `DowngradeRecord` | 降级记录 |
| `reactivate_factor(factor_id)` | 重新激活已降级因子 |

#### 4.2.14 FTS-Expr DSL `expr_dsl/`（Phase C.2）

**职责**: 算子表达式语言，支持 58 个算子 7 大类别（L0-L5 分层）。

| 关键类/函数 | 签名 | 说明 |
|------------|------|------|
| `ExprNode` | `@dataclass` | AST 节点（`op: str`, `args: list[ExprNode]`, `kind: str` 为 "op"/"field"/"const"） |
| `parse_expression(expr_str)` | `str → ExprNode` | 表达式解析器（字符串 → AST） |
| `validate_expr(node, registry)` | `(ExprNode, dict) → bool` | 静态验证（类型检查 + 参数合法性） |
| `compute_max_lookback(node)` | `ExprNode → int` | PIT 最大 lookback 静态分析 |
| `collect_fields(node)` | `ExprNode → set[str]` | 收集表达式所需数据字段 |
| `evaluate(node, data, registry)` | `(ExprNode, DataFrame, dict) → Series/float` | 运行时执行（解释 AST，向量化计算） |
| `compile_expr_to_code(expr_str)` | `str → str` | 表达式编译为 Python 代码 |
| `analyze_expression(expr_str)` | `str → ExprAnalysis` | 完整表达式分析 |
| `eval_fts_expr(expression, data, params)` | `(str, DataFrame, dict) → np.ndarray` | 沙箱运行时入口函数 |
| `OperatorMeta` | `@dataclass(frozen=True)` | 算子元数据（name, func, category, params, int_params, float_params, param_bounds, lookback_param, differentiable, economic_meaning） |
| `build_registry()` | `→ dict[str, OperatorMeta]` | 构建完整算子注册表（复用 feature_ops 实现） |

**L0 基础数据字段**: `("open", "high", "low", "close", "volume", "vwap", "amount", "returns", "hold", "settle")`

**58 算子 7 大类别**:

| 类别 | 算子数 | 示例 |
|------|--------|------|
| time_series | 6 | ts_mean, ts_std, ts_zscore, ts_rank, ts_sum, ts_delta |
| price | 7 | returns, log_return, high_low_ratio, vwap, typical_price, gap, price_to_sma |
| rolling | 9 | rolling_mean, rolling_std, rolling_max, rolling_min, rolling_rank, rolling_corr, rolling_cov, rolling_beta, rolling_skew |
| technical | 7 | rsi, macd, bollinger_upper, bollinger_lower, bollinger_width, atr, adx |
| cross_section | 5 | cs_rank, cs_zscore, cs_normalize, cs_sum, cs_mean |
| cross_symbol | 3 | cs_rank_global, cs_zscore_global, cs_quantile_global |
| composite | 13 | rank, scale, sign, abs, log, sqrt, add, sub, mul, div, max, min, neg |

**逻辑运算符**: `and_`, `or_`, `not_`（DSL 专用，避免与 Python 关键字冲突）

#### 4.2.15 GP 遗传规划 `gp_evolver.py`（Phase C.1）

**职责**: 基于遗传规划在算子空间搜索最优因子表达式。

| 关键类/函数 | 说明 |
|------------|------|
| `TreeNode` | 树节点（op_name, operand, children, is_terminal） |
| `ExpressionTree` | 表达式树（root, depth, size, expression, fitness） |
| `FitnessResult` | 适应度结果（ic, sharpe, fitness, factor_code, evaluation_time_ms） |
| `GPEvolverConfig` | 演化配置（population_size, max_generations, tournament_size, crossover_rate, mutation_rate, max_tree_depth, elitism_size, fitness_metric, multi_parent_crossover_rate） |
| `GenerationSnapshot` | 代数快照（generation, best_fitness, best_expression, avg_fitness, population_diversity） |
| `GPEvolveResult` | 演化结果（best_tree, best_fitness, best_expression, best_ic, best_sharpe, generations_completed, history, total_evaluations） |
| `GPEvolver` | GP 演化器（锦标赛选择/交叉/变异/精英保留/多父代交叉） |
| `tree_to_factor_program(expr_tree, ...)` | 表达式树转因子程序 |

#### 4.2.16 算子演化引擎 `operator_evolution.py`（Phase C.4）

**职责**: 独立于 GP 的算子空间进化搜索，在 ExprNode 层面做交叉/变异。

| 关键类/函数 | 说明 |
|------------|------|
| `OperatorEvolutionConfig` | 算子演化配置（population_size, max_generations, tournament_size, crossover_rate, mutation_rate, max_tree_depth, elitism_size, fitness_metric, max_attempts, random_seed） |
| `OperatorGenerationSnapshot` | 每代快照（generation, best_fitness, best_expression, avg_fitness, population_diversity） |
| `OperatorEvolveResult` | 演化结果 |
| `OperatorEvolver` | 算子演化器（种群初始化 → 适应度评估 → 锦标赛选择 → 子树交叉/变异 → 精英保留） |

#### 4.2.17 特征工程 `feature_ops.py` & `feature_importance.py`

**职责**: 特征算子引擎和特征重要性分析。

| 关键类/函数 | 说明 |
|------------|------|
| `OperatorInfo` | 算子元信息（name, category, params, description, signature, version, added_at） |
| `OperatorRegistry` | 算子注册表（50+ 算子，6 大类） |
| `TimeSeriesOps` | 时序算子类（ts_mean, ts_std, ts_max, ts_min, ...） |
| `PriceOps` | 价格算子类（returns, log_return, high_low_ratio, vwap, ...） |
| `RollingOps` | 滚动算子类（rolling_mean, rolling_std, rolling_max, ...） |
| `TechnicalOps` | 技术指标算子类（rsi, macd, bollinger, atr, adx, ...） |
| `CrossSectionOps` | 截面算子类（cs_rank, cs_zscore, cs_normalize, ...） |
| `CrossSymbolOps` | 跨品种算子类 |
| `CompositeOps` | 复合算子类（add, sub, mul, div, rank, scale, ...） |
| `FeatureOpsEngine` | 特征工程引擎入口 |
| `FeatureImportanceAnalyzer` | 特征重要性分析器（随机森林/GBDT/线性回归） |
| `FeatureImportanceResult` | 分析结果 |

#### 4.2.18 回测流水线 `backtest_pipeline.py`（Phase B.2）

**职责**: 端到端回测流水线，4 阶段标准化流程。

| 关键类/函数 | 说明 |
|------------|------|
| `BacktestPipeline` | 回测主管线（4 阶段：DataLoad → FactorCompute → Performance → Report） |
| `BacktestInput` | 回测输入参数（factor, data, benchmark, forward_period, cost_rate, slippage, initialization_capital, date_range） |
| `BacktestReport` | 回测报告（含 PerformanceMetrics 全量指标） |
| `BacktestResult` | 回测结果（success/error/output） |
| `BacktestPipelineBuilder` | 管线构建器（Builder 模式） |
| `PipelineResult` | 管线运行结果 |
| `PipelineStage` | 管线阶段枚举（DATA_LOAD / FACTOR_COMPUTE / PERFORMANCE / REPORT） |
| `PerformanceMetrics` | 绩效指标（总收益/年化/Sharpe/最大回撤/IC/ICIR/换手率等） |
| `FactorOutput` | 因子计算输出（values, dates, forward_returns, ic_series, metadata） |
| `PipelineConfig` | 管线配置 |

#### 4.2.19 实盘信号契约 `signal_contract.py`（Phase C.2）

**职责**: 定义标准信号 JSON Schema 和验证器。

| 关键类/函数 | 说明 |
|------------|------|
| `FactorSignal` | 完整因子信号包（signal_id, portfolio_id, timestamp, frequency, universe, signals, meta） |
| `SignalMeta` | 信号元数据（trace_id, factor_count, regime, source_version） |
| `SignalDetail` | 单个品种信号详情（symbol, direction, position, confidence, price, stop_loss, take_profit, contributing_factors） |
| `FactorContribution` | 因子贡献详情（factor_id, weight, signal） |
| `SignalValidator` | 格式验证器（必填字段/方向枚举/置信度范围） |

**信号方向**: `DIRECTIONS = ("long", "short", "flat")`
**信号频率**: `FREQUENCIES = ("tick", "1m", "5m", "15m", "30m", "1h", "4h", "1d")`

**FTS 角色边界**: 本模块只负责信号格式契约与验证，交易执行由下游系统负责。

#### 4.2.20 反馈闭环 `feedback_loop.py`（Phase C.3）

**职责**: "因子表现→归因→演化方向调整" 完整闭环。

| 关键类/函数 | 说明 |
|------------|------|
| `FeedbackEventType` | 反馈事件类型枚举（6 种：LIVE_DEVIATION / DATA_ANOMALY / MARKET_EVENT / PERIODIC_EVAL / AUDIT_FAILURE / FACTOR_DECAY / USER_TRIGGERED） |
| `RootCause` | 根本原因枚举（5 种：FACTOR_DECAY / REGIME_MISMATCH / DATA_QUALITY / IMPLEMENTATION_BUG / NORMAL_FLUCTUATION / UNKNOWN） |
| `FeedbackTrigger` | 触发条件检查器。`check(factor_id, live_monitor, ...) → list[FeedbackEvent]` |
| `AttributionAnalyzer` | 归因分析器。`analyze(event) → RootCause`（5 种根因判定） |
| `EvolutionDirectionAdjuster` | 演化方向调整器。`adjust(root_cause, config) → dict`（根据归因调整演化配置） |
| `EvolutionEffectiveness` | 月度迭代效果评估。`evaluate(month) → dict` |
| `FeedbackLoop` | 闭环主类（`process_feedback() / trigger_manual_feedback() / generate_report() / get_stats()`） |

#### 4.2.21 其他分析模块

| 模块 | 关键类/函数 | 说明 |
|------|-------------|------|
| `ablation.py` | `AblationExperiment` | 5 种消融模式（volume_zero / vwap_replace / time_shuffle / noise_inject / feature_permute） |
| `shap_analyzer.py` | `ShapAnalyzer` | SHAP 局部可解释性分析 |
| `robustness.py` | `RobustnessTester` | 对抗样本/缺失值/分布外测试 |
| `causal_validator.py` | `CausalValidator` | 因果结构验证（6 个预定义事件，Granger 因果检验/反事实分析） |
| `walk_forward.py` | `WalkForwardOptimizer`, `WalkForwardConfig`, `WalkForwardResult` | 走航验证（多窗口样本外稳定性验证） |
| `stress_test.py` | `StressTester` | 压力测试 |
| `regime.py` | `RegimeAwareSelector` | 5 种市场状态检测（bull/bear/oscillate/high_vol/low_vol），因子选择性激活 |
| `standardizer.py` | `Standardizer`, `StandardizeMethod`, `standardize()` | 6 种标准化方法（z-score / min-max / rank 等） |
| `experience_chain.py` | `ExperienceChain`, `create_trace_from_evaluation()` | 经验链持久化（成功/失败轨迹，满 100 淘汰最旧 20，失败模式聚类） |
| `cost_model.py` | `CostModel` | 交易成本模型 |
| `cost_simulator.py` | `CostSimulator` | 成本模拟器 |
| `risk_attributor.py` | `RiskAttributor` | 风险归因器 |
| `report_generator.py` | `ReportGenerator` | 报告生成器 |
| `capital_allocator.py` | `CapitalAllocator` | 资金分配器 |
| `portfolio_constructor.py` | `PortfolioConstructor` | 组合构建器 |
| `signal_generator.py` | `SignalGenerator` | 信号生成器 |
| `factor_screener.py` | `FactorScreener` | 因子筛选器 |
| `adaptive_weight.py` | `AdaptiveWeight` | 自适应权重 |

#### 4.2.22 因子数据库 `factor_db/`

**职责**: DuckDB 持久化层。

**表结构**:

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `factor_catalog` | 因子主表 | factor_id, name, code, sharpe, ic, market, family, status, is_elite, kind, expression |
| `factor_evaluations` | 评估历史 | eval_id, factor_id, level_1_*, level_2_*, walk_forward |
| `factor_versions` | 代码版本历史 | version_id, factor_id, code, code_hash, version |
| `factor_correlations` | 相关性矩阵 | factor_id_a, factor_id_b, pearson, spearman |

**关键类**:

| 类 | 方法 | 说明 |
|----|------|------|
| `FactorRepository` | `create_factor(factor)` | 创建因子 |
| | `get_factor(factor_id)` | 查询因子 |
| | `update_factor(factor_id, updates)` | 更新因子 |
| | `list_factors(market, min_ic, min_sharpe)` | 列表查询（支持 market/family/generation/status 筛选） |
| | `get_eligible(market, min_ic, min_sharpe)` | 合格因子查询 |
| | `get_diverse_factors(market, total_count, ...)` | 多样性选择（按家族分布） |
| | `get_family_distribution(market)` | 家族分布统计 |
| | `get_leaderboard(market, metric, top_n)` | 排行榜 |
| `FactorLineage` | `get_lineage(factor_id)` | 血缘查询 |
| | `get_evaluation_trend(factor_id)` | 评估趋势分析 |
| | `detect_quality_degradation(factor_id)` | 质量退化检测 |
| | `batch_audit(factor_ids)` | 批量审计 |

---

### 4.3 配置系统 `fts.config`

**文件**: `fts/config/`

**职责**: 全局配置管理（YAML → 环境变量 → 默认值）。

#### 关键类

| 类 | 说明 |
|----|------|
| `FTSConfig` | `@dataclass` 全局配置数据类。字段：memory_dir, elite_dir, futures_elite_dir, default_market, evolution_mode, max_generations, population_size, micro_trials_per_generation, max_workers, meta_loop_interval_hours, meta_loop_max_tokens, portfolio_max_factors, portfolio_top_n, portfolio_decay_days, verifier, log_level, log_file |
| `get_config()` | 获取全局配置实例（延迟初始化单例） |
| `load_config(config_path)` | 加载配置（YAML 解析 + 环境变量覆盖） |
| `validate_evolution_mode(mode)` | 校验演化模式合法性（`EVOLUTION_MODES = ("operator", "code", "hybrid")`） |

**配置优先级**: 环境变量 (`FTS_*`) > YAML 配置文件 > Python 默认值

**`FTSConfig` 关键字段**:

| 字段 | 默认值 | 环境变量 | 说明 |
|------|--------|---------|------|
| `memory_dir` | `memory` | `FTS_MEMORY_DIR` | 内存目录 |
| `elite_dir` | `memory/knowledge/factors/elite` | `FTS_ELITE_DIR` | 股票精英因子目录 |
| `futures_elite_dir` | `memory/knowledge/factors/futures_elite` | `FTS_FUTURES_ELITE_DIR` | 期货精英因子目录 |
| `default_market` | `futures` | `FTS_DEFAULT_MARKET` | 默认市场 |
| `evolution_mode` | `hybrid` | `FTS_EVOLUTION_MODE` | 演化模式（operator/code/hybrid） |
| `max_generations` | 10 | — | 最大演化代数 |
| `population_size` | 20 | — | 演化种群大小 |
| `micro_trials_per_generation` | 50 | — | 每代微调 trial 数 |
| `max_workers` | 4 | `FTS_MAX_WORKERS` | 并行工作数 |
| `log_level` | `INFO` | `FTS_LOG_LEVEL` | 日志级别 |

**`FTSConfig.get_elite_dir(market)`**: 按市场获取对应的 elite 目录（`"futures"` → `futures_elite_dir`，其他 → `elite_dir`）

---

### 4.4 管线层 `fts.pipeline`

**文件**: `fts/pipeline/`

**职责**: 因子推演管线（数据处理管线）。

| 类/函数 | 说明 |
|---------|------|
| `DataPayload` | 数据载荷（data_type, symbol, payload, metadata, trace_id） |
| `ProcessingStage` | `@runtime_checkable Protocol` 管线阶段协议（input_type, output_type, process） |
| `FactorPipeline` | 管线抽象基类 |
| `PipelineResult` | 管线运行结果 |
| `FactorCombiner` | 多因子加权/融合器 |
| `CombinerConfig` | 组合器配置 |
| `WeightedFactor` | 加权因子条目 |
| `FactorQualityInspection` | 因子质检过滤层（`inspect()` / `batch_inspect()` / `filter_passed()` / `inspect_factor()`） |
| `InspectionResult` | 质检结果 |
| `BatchQualityInspector` | 批量质检器 |

---

### 4.5 策略层 `fts.strategies`

**文件**: `fts/strategies/`

**职责**: 策略层，包含 v2 可插拔策略框架和多因子策略。

| 类/函数 | 说明 |
|---------|------|
| `BaseStrategyV2` | v2 策略抽象基类（`compute → filter → score` 三段式） |
| `RawSignal` | 原始信号（symbol, direction, signal_type, raw_score, strategy_name, meta） |
| `ScoredSignal` | 打分信号（symbol, direction, signal_type, strategy_name, total, abs_score, grade(STRONG/WATCH/WEAK/NOISE), weight, 技术指标字段） |
| `StrategyV1Adapter` | v1 兼容适配器 |
| `MultiFactorStrategy` | 四维因子加权打分策略 |
| `RegimeAdaptiveStrategy` | 制度自适应策略 |
| `DynamicWeightStrategy` | 动态权重策略 |
| `MultiPeriodSignalFusion` | 多周期信号融合 |

---

### 4.6 数据层 `fts.data`

**文件**: `fts/data.py`, `fts/data_futures.py`, `fts/data_fundamental.py`, `fts/data_mcp.py`, `fts/data_futures_fundamental.py`

**职责**: 统一数据访问层，整合多个子提供者。

#### `FTSDataProvider` (data.py)

统一数据提供者，组合 MCP、基本面、期货等多个子提供者。

**构造函数**: `FTSDataProvider(mcp_provider, fundamental_provider, futures_provider, futures_fundamental_provider)` — 每个参数可选，自动创建默认实例。

| 方法 | 签名 | 说明 |
|------|------|------|
| `get_ohlcv` | `(symbol, *, days=500, adjust="qfq", trace_id="", fundamental=False) → DataFrame` | 获取股票/ETF OHLCV K 线数据 |
| `get_etf_ohlcv` | `(symbol, days=500, trace_id="") → DataFrame` | 获取 ETF OHLCV |
| `get_csi300_panel` | `(days=500, max_stocks=50, trace_id="") → (dict[str, DataFrame], DatetimeIndex, np.ndarray)` | 获取沪深 300 面板数据 |
| `get_futures_panel` | `(symbols, days=500, trace_id="") → (dict[str, DataFrame], DatetimeIndex, np.ndarray)` | 获取期货面板数据 |
| `get_futures_ohlcv` | `(symbol, days=500, trace_id="") → DataFrame` | 获取期货连续合约 OHLCV |
| `enrich_with_fundamental` | `(df, symbol, *, trace_id="") → DataFrame` | 基本面字段注入（pe_ttm, pb, total_market_cap 等） |
| `enrich_futures_fundamental` | `(df, symbol, *, trace_id="") → DataFrame` | 期货基本面字段注入（库存/基差/仓单） |
| `get_stock_panel` | `(symbols, days=500, trace_id="") → (dict[str, DataFrame], DatetimeIndex, np.ndarray)` | 任意股票面板数据 |
| `search_symbol` | `(query) → list[dict]` | 搜索股票/ETF 代码 |
| `synthesize_ohlcv` | `(n_days, *, trace_id="") → DataFrame` | 合成数据（无网络时降级） |

#### `FuturesDataProvider` (data_futures.py)

**数据源**: DuckDB (`data/fts_history.duckdb`) kline_cache 表

**构造函数**: `FuturesDataProvider(use_akshare_fallback=True)`

| 方法 | 说明 |
|------|------|
| `get_ohlcv(symbol, days=500, trace_id="")` | 获取期货连续合约 OHLCV（含 hold/settle） |
| `get_futures_panel(symbols, days=500, trace_id="")` | 获取期货面板数据 |

**数据源优先级**: DuckDB kline_cache → TQ-Local → AKShare → 合成数据

**期货特有字段**: `hold`（持仓量）, `settle`（结算价）

**VWAP 计算逻辑**:
- 精确 VWAP（有成交额时）: `amount / volume`
- AKShare 路径（有 settle 时）: `(H + L + C + settle) / 4`
- DuckDB 路径（无 settle 时）: `(H + L + C) / 3`

**核心品种**: 25 个核心品种（黑色/有色/能化/农产品/股指）

#### `MCPDataProvider` (data_mcp.py)

| 方法 | 说明 |
|------|------|
| `get_ohlcv(code, days=500, adjust="qfq", trace_id="")` | 腾讯 HTTP API 获取 OHLCV |
| `get_stock_panel(symbols, days=500, trace_id="")` | 批量获取面板数据 |

**API**: 腾讯自选股 HTTP API (`qt.gtimg.cn` / `web.ifzq.gtimg.cn`)

---

### 4.7 数据源适配器 `fts.data_sources`

**文件**: `fts/data_sources/`（v2.3.0 多源集成）

**职责**: 期货多源数据适配器。

#### 关键类

| 类 | 说明 |
|----|------|
| `BaseFuturesSource` | 抽象基类（3 个抽象方法：`fetch_ohlcv()` / `fetch_quote()` / `is_available()`） |
| `FuturesDataAggregator` | 多源调度器。`get_ohlcv(symbol, days, trace_id) → DataFrame` |
| `OHLCVFusion` | 多源融合器。`__init__(strategy=FusionStrategy.MEDIAN, source_weights=None, outlier_threshold=0.005)` |
| `SourceUnavailable` | 数据源不可用异常（`source`, `reason`） |
| `TQLocalSource` | 通达信本地数据源（TQ_LOCAL / TQ_PYTHON 两种路径） |
| `WindSource` | Wind 数据源（字段增强层，补充 settle/oi_change/期权 IV） |
| `IFindSource` | iFinD 数据源（EDB 宏观/产业链） |

**K 线主路径（5 级降级）**:
```
DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
```

**字段增强层（并行）**:
```
WIND  → settle / oi_change / 期权 IV/PCR
IFIND → EDB 宏观/产业链 / 期货全字段
```

**熔断器**: `BreakerState`（consecutive_failures, circuit_open, opened_at, total_success, total_failure）
- 任一源连续 5 次失败 → `circuit_open = True`
- 6 小时冷却（`COOLDOWN_SECONDS = 21600`）
- 冷却后探活恢复

**融合策略**: `FusionStrategy` 枚举
- `MEDIAN`（默认）: 每字段取中位数，抗异常值
- `MEAN`: 算术平均
- `WEIGHTED`: 按源权重加权平均
- `HIERARCHICAL`: 优先级优先，与中位数分歧时降级到中位数
- `TRIMMED_MEAN`: 去掉最高/最低后取均值（N≥3 时最稳健）

**默认源权重**: TQ_LOCAL=2.0, TQ_PYTHON=2.0, WIND=1.5, IFIND=1.0, AKSHARE=0.5, DUCKDB_CACHE=1.0, SYNTHETIC=0.0

**融合字段**: `("open", "high", "low", "close", "volume", "amount", "settle")`（hold/oi_change/pre_settle 不融合）

**交叉验证**: ≥2 源同日 close 差异 > 0.5% 时记录 disagreement 至 `data/data_source_disagreements.jsonl`

---

### 4.8 风控层 `fts.risk`

**文件**: `fts/risk/`

**职责**: 实时风控检查与交易适配（C.2）。

| 类/函数 | 说明 |
|---------|------|
| `RiskManager` | 5 项风控规则检查器。`check(signal, account, positions) → RiskCheckResult` |
| `RiskConfig` | 风控配置（single_position_limit_pct, max_portfolio_drawdown_pct, daily_loss_limit_pct, max_leverage, max_concentration_pct, max_open_positions） |
| `RiskCheckItem` | 风控检查项（check_name, passed, current_value, limit_value, message, severity） |
| `RiskCheckResult` | 风控检查结果（approved, checks, blocking_violations, timestamp, signal_id） |
| `TradeAdapter` | 交易适配器抽象基类（Liskov 替换原则） |
| `TradeOrderResult` | 交易订单结果 |
| `PositionInfo` | 持仓信息 |
| `AccountStatus` | 账户状态 |
| `SimulatedTradeAdapter` | 模拟交易适配器（测试与仿真） |

**5 项风控规则（默认阈值）**:
1. 单品种仓位上限: 10%
2. 组合最大回撤: 20%
3. 单日最大亏损: 5%
4. 杠杆倍数限制: 3x
5. 集中度限制: 前 3 大品种 ≤ 50%

---

### 4.9 调度层 `fts.scheduler`

**文件**: `fts/scheduler/`

**职责**: 定时任务注册 + APScheduler 调度。

| 类/函数 | 说明 |
|---------|------|
| `TaskSpec` | `@dataclass` 定时任务规格（name, cron_expression, callable_path, description, enabled, trace_id_prefix） |
| `TaskRegistry` | 任务注册表（`register()` / `unregister()` / `get()` / `list_all()` / `list_enabled()`） |
| `SchedulerEngine` | 调度器引擎（APScheduler 包装，`start()` / `stop()` / `add_job()` / `remove_job()`） |
| `ProcessWatchdog` | 进程级看门狗 |
| `HotSwapWatcher` | 热重载支持 |
| `run_scheduler()` | 启动调度器快捷函数 |

**默认任务**:

| 任务名 | cron 表达式 | 说明 |
|--------|-------------|------|
| `l1_meta_loop` | `30 8 * * *` (08:30) | L1 知识补给 + 种子注入 |
| `l2_evolution_loop` | `0 23 * * *` (23:00) | L2 夜间因子演化 |
| `l3_portfolio_loop` | `0 20 * * *` (20:00) | L3 组合构建 + 信号 |
| `health_check` | `*/10 * * * *` (每 10 分钟) | 健康检查 |

---

### 4.10 监控层 `fts.monitor`

**文件**: `fts/monitor/`

**职责**: 系统健康监控 + 精英因子跟踪 + Web UI。

| 类/函数 | 签名 | 说明 |
|---------|------|------|
| `check_loop_status(loop_name)` | `(str) → LoopStatusReport` | 检查单个循环状态 |
| `check_all_status()` | `→ SystemStatusReport` | 检查所有循环状态（L1/L2/L3） |
| `format_status_report(report)` | `(SystemStatusReport) → str` | 格式化状态报告 |
| `status_report_to_json(report)` | `(SystemStatusReport) → str` | JSON 格式状态报告 |
| `LoopStatusReport` | `@dataclass` | 循环状态报告（loop_name, healthy, last_run_at, status, last_error, version, run_id, tokens_consumed, age_hours） |
| `SystemStatusReport` | `@dataclass` | 系统级状态报告（healthy, loops, checked_at, fts_version, any_circuit_broken, any_stale, total_tokens_today） |

**Web UI 端点**（`FTSDashboardServer`，纯标准库 `http.server`）:
- `GET /` — 现代仪表盘 HTML（暗色主题，卡片布局）
- `GET /api/status` — 系统状态 JSON
- `GET /api/factors` — elite 因子列表 JSON
- `GET /health` — 健康检查 JSON
- `GET /metrics` — Prometheus 完整指标
- `GET /metrics/data-sources` — 数据源专用指标

**`EliteFactorTracker`**:
- `init_tracker(factor_id, name, entry_ic, entry_sharpe, grade, quality_score)` — 初始化跟踪
- `update(factor_id, current_ic)` — 更新样本外 IC
- `get_decaying(max_consecutive)` — 获取衰减因子列表
- `auto_retire()` — 自动淘汰失效因子
- `report()` — 生成跟踪报告

**精英因子状态转换**:
- A 级 (score>=40) → `active`
- B 级 (30<=score<40) → `observing` → 观察期结束 → `active` / `decaying`
- C 级 (score<30) → `rejected`
- `active` → 连续 IC 衰减 → `decaying` → 严重衰减 → `critical_decay` → `retired`

**`DataQualityMonitor`**:
- `register_factor(factor_id, baseline_ic, baseline_capacity)` — 注册因子
- `check(factor_id, current_ic, current_capacity) → QualityAlert` — 检查质量告警
- 告警类型: `ic_drift`（IC 漂移）, `capacity_shock`（容量突变）
- 告警级别: `warning`, `critical`

**`LogicMonitor`**: 因子行为逻辑监控（`ContractSwitchResult`, `DriftCheckResult`, `ExtremePredictionResult`）
**`LiveFactorMonitor`**: 实盘因子监控（C.2，在主循环中 `update()` 持续监控）

---

### 4.11 LLM 客户端 `fts.llm`

**文件**: `fts/llm.py`

**职责**: 统一的 LLM 调用接口。

| 类/函数 | 说明 |
|---------|------|
| `LLMClient` | 抽象基类（`complete(prompt, max_tokens) → (str, int)` 抽象方法） |
| `OpenAIClient` | OpenAI API 客户端（`chat.completions.create`，支持重试） |
| `AnthropicClient` | Anthropic Claude API 客户端（`messages.create`，支持重试） |
| `MockLLMClient` | 模拟客户端（开发/测试用，返回预设响应） |
| `LLMError` | LLM 调用失败异常 |
| `LLMCallRecord` | `@dataclass` 单次调用记录（prompt, response, model, tokens_in, tokens_out, duration_ms, error, trace_id） |
| `get_llm_client(backend)` | 工厂函数。自动检测顺序：`OPENAI_API_KEY` → OpenAI, `ANTHROPIC_API_KEY` → Anthropic, 均无 → MockLLMClient |

**`LLMClient` 扩展方法**:
- `generate_json(prompt, max_tokens) → dict` — 生成 JSON 响应（自动解析 markdown 代码块）
- `bootstrap_factors(market_snapshot, debate_gaps, max_candidates, trace_id) → list[dict]` — L1 Bootstrapping 生成种子候选

**环境变量配置**:
- `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`（默认 `gpt-4o`）
- `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`（默认 `claude-sonnet-4-20250514`）

---

### 4.12 CLI 入口 `fts.cli`

**文件**: `fts/cli.py`（约 1250 行）

**职责**: 统一命令行入口，所有子命令启动时生成 trace_id。通过 `pyproject.toml` 注册为 `fts` 命令。

**子命令列表**:

| 子命令 | 子子命令 | 说明 |
|--------|---------|------|
| `fts version` | — | 打印版本号 |
| `fts monitor` | — | 检查所有循环健康状态（支持 `--json`） |
| `fts evolution` | `run` | 启动 L2 因子演化（`--universe futures/csi300/single`, `--max-generations`, `--max-stocks`, `--synthesis-mode`） |
| `fts meta-loop` | `run` | 启动 L1 Meta-Loop（`--market`, `--symbols`） |
| `fts portfolio` | `run` | 启动 L3 组合构建（`--universe`, `--synthesis-mode`） |
| `fts ui` | — | 启动 Web UI 仪表盘（`--port 9100`） |
| `fts scheduler` | `run` / `list` | 调度器管理 |
| `fts factor` | `list` / `show` / `seeds` / `stats` / `lineage` | 因子管理（支持 `--market`, `--family`, `--min-sharpe`, `--diverse`, `--json`） |
| `fts backtest` | `run` / `batch` / `compare` | 回测流水线（B.2） |
| `fts feature` | `list` / `analyze` | 特征工程中台（C.1，`--category`, `--json`） |
| `fts gp` | `evolve` | GP 遗传规划演化（C.1，`--population`, `--generations`） |
| `fts feedback` | `trigger` / `process` / `report` / `stats` | 反馈闭环（C.3） |

**`_prepare_data(symbol, days)`**: 准备演化所需数据（腾讯 API 优先 → 合成数据降级）
**`_prepare_cross_section_data(universe, days, max_stocks)`**: 准备横截面演化所需面板数据

---

## 5. 关键类/函数速查表

### 5.1 核心引擎类

| 类 | 所在模块 | 职责 |
|----|---------|------|
| `EvolutionLoop` | `factor_engine.evolution_loop` | L2 因子演化主循环（UCT 父因子选择 + 三级评估 + 质量评分 + 审计） |
| `MetaLoop` | `factor_engine.meta_loop` | L1 元循环（知识补给 + Bootstrapping + debate 分析） |
| `PortfolioLoop` | `factor_engine.portfolio_loop` | L3 组合循环（粘性约束 + 漂移监控 + 信号产出） |
| `FactorVerifier` | `factor_engine.verifier` | 锁定 Verifier（初始化后不可修改） |
| `L1Verifier` | `factor_engine.meta_loop` | L1 宽松 Verifier（2/4 维度达标） |
| `L3Verifier` | `factor_engine.portfolio_loop` | L3 组合 Verifier（夏普/相关性/换手率/衰减/因子数） |
| `EvaluationChain` | `factor_engine.evaluation_chain` | 三级评估链（L1 回测 → L2 经济逻辑 → L3 多重检验） |
| `SeedPool` | `factor_engine.seed_pool` | 种子池管理（按市场隔离，支持相关性预检） |
| `MacroEvolver` | `factor_engine.macro_evolution` | 宏观演化（LLM 改因子逻辑） |
| `FactorExecutor` | `factor_engine.factor_program` | 因子执行器（按 kind 分派：CODE 沙箱 / OPERATOR DSL） |
| `FactorQualityCard` | `factor_engine.factor_quality_card` | 质量评分卡（10 维，0-50 分，A/B/C 级） |
| `FactorAuditor` | `factor_engine.audit` | 因子审计（6 项强制，渐进式审计） |
| `FactorInspector` | `factor_engine.factor_inspector` | 因子巡检（自动降级退化因子） |
| `FactorRepository` | `factor_engine.factor_db.repository` | 因子数据库 CRUD（排行榜/多样性选择/家族分布） |
| `FactorLineage` | `factor_engine.factor_db.lineage` | 因子血缘追踪（评估趋势/质量退化/批量审计） |
| `FTSDataProvider` | `data` | 统一数据提供者（组合 MCP/基本面/期货子提供者） |
| `FuturesDataProvider` | `data_futures` | 期货数据提供者（DuckDB kline_cache + AKShare） |
| `FuturesDataAggregator` | `data_sources.aggregator` | 多源数据调度器（5 级降级 + 熔断器 + 交叉验证） |
| `OHLCVFusion` | `data_sources.fusion` | 多源融合器（5 种策略，N=1 退化为透传） |
| `FTSDashboardServer` | `monitor.http_server` | Web UI 仪表盘（纯标准库，端口 9100） |
| `SchedulerEngine` | `scheduler.engine` | 调度器引擎（APScheduler 包装） |
| `ProcessWatchdog` | `scheduler.watchdog` | 进程看门狗 |
| `RiskManager` | `risk.risk_manager` | 风控检查器（5 项规则，拦截不合格信号） |
| `FeedbackLoop` | `factor_engine.feedback_loop` | 反馈闭环（6 种事件/5 种根因/月度评估） |
| `BacktestPipeline` | `factor_engine.backtest_pipeline` | 回测管线（4 阶段：DataLoad → FactorCompute → Performance → Report） |
| `GPEvolver` | `factor_engine.gp_evolver` | GP 遗传规划搜索引擎（锦标赛选择/交叉/变异/精英保留） |
| `OperatorEvolver` | `factor_engine.operator_evolution` | 算子演化引擎（ExprNode 层面交叉/变异） |
| `FeatureOpsEngine` | `factor_engine.feature_ops` | 特征工程引擎（50+ 算子 6 大类） |
| `SignalValidator` | `factor_engine.signal_contract` | 信号格式验证器（方向枚举/置信度范围/必填字段） |
| `LogicMonitor` | `monitor.logic_monitor` | 逻辑监控器（contract_switch/drift/extreme_prediction） |
| `EliteFactorTracker` | `monitor.elite_tracker` | 精英因子跟踪（active→decaying→retired 状态转换） |
| `DataQualityMonitor` | `monitor.data_quality_monitor` | 数据质量监控（IC 漂移/容量突变告警） |
| `RegimeAwareSelector` | `factor_engine.regime` | 市场制度感知选择器（5 种状态，因子选择性激活） |
| `ExperienceChain` | `factor_engine.experience_chain` | 经验链持久化（成功/失败轨迹，满 100 淘汰最旧 20） |
| `EvolutionStateManager` | `factor_engine.state` | 演化状态管理器（加载/保存/备份/冷启动） |
| `Standardizer` | `factor_engine.standardizer` | 标准化器（6 种方法） |
| `LLMClient` | `llm` | LLM 客户端抽象基类 |
| `OpenAIClient` | `llm` | OpenAI API 客户端 |
| `AnthropicClient` | `llm` | Anthropic Claude API 客户端 |
| `MockLLMClient` | `llm` | 模拟 LLM 客户端（测试用） |
| `BaseStrategyV2` | `strategies.base_v2` | v2 策略基类（compute → filter → score） |
| `MultiFactorStrategy` | `strategies.multi_factor_strategy` | 四维因子加权打分策略 |

### 5.2 关键函数

| 函数 | 所在模块 | 签名 | 说明 |
|------|---------|------|------|
| `generate_trace_id(prefix)` | `factor_engine.state` | `(prefix="l2") → str` | 生成全链路 trace_id: `<prefix>_<8hex>_<timestamp>` |
| `generate_run_id()` | `factor_engine.state` | `→ str` | 生成运行 ID: `run_<8hex>_<timestamp>` |
| `generate_session_id()` | `factor_engine.state` | `→ str` | 生成 CLI 会话 ID |
| `create_factor_program(name, code, ...)` | `factor_engine.factor_program` | `→ FactorProgram` | 创建因子程序 |
| `validate_factor_code(code)` | `factor_engine.factor_program` | `(str) → tuple[bool, list[str]]` | 安全沙箱验证 |
| `evolve_micro(factor, data, fwd_ret, n_trials)` | `factor_engine.micro_evolution` | `→ FactorProgram` | 微观参数优化 |
| `synthesize_signals(factors, mode)` | `factor_engine.portfolio_loop` | `→ list[PortfolioSignal]` | 信号合成 |
| `orthogonalize_factors(signals, data)` | `factor_engine.portfolio_loop` | `→ list[PortfolioSignal]` | 因子正交化 |
| `decay_test(signals, lookback)` | `factor_engine.portfolio_loop` | `→ list[PortfolioSignal]` | 衰减检验 |
| `build_combo(signals, prev_combo, sticky_config)` | `factor_engine.portfolio_loop` | `→ PortfolioCombo` | 组合构建 |
| `load_elite_factors(elite_dir, market)` | `factor_engine.portfolio_loop` | `→ list[FactorProgram]` | 加载精英因子 |
| `atomic_write(path, data)` | `core.atomic` | `(str/Path, Any) → None` | 原子写入 |
| `atomic_read(path, default)` | `core.atomic` | `→ Any` | 安全读取 |
| `parse_expression(expr_str)` | `factor_engine.expr_dsl.parser` | `(str) → ExprNode` | 表达式解析 |
| `evaluate(node, data, registry)` | `factor_engine.expr_dsl.executor` | `→ Series/float` | 表达式执行 |
| `validate_expr(node, registry)` | `factor_engine.expr_dsl.validator` | `→ bool` | 表达式验证 |
| `compute_max_lookback(node)` | `factor_engine.expr_dsl.validator` | `→ int` | PIT 最大 lookback 分析 |
| `collect_fields(node)` | `factor_engine.expr_dsl.validator` | `→ set[str]` | 收集表达式所需字段 |
| `eval_fts_expr(expr, data, params)` | `factor_engine.expr_dsl.runtime` | `→ np.ndarray` | 沙箱运行时入口 |
| `get_config()` | `config.settings` | `→ FTSConfig` | 获取全局配置单例 |
| `get_llm_client(backend)` | `llm` | `(str) → LLMClient` | 获取 LLM 客户端 |
| `load_all_yaml_seeds(trace_id)` | `factor_engine.seed_loader` | `→ list[FactorProgram]` | 从 YAML 加载所有种子 |

---

## 6. 依赖关系图

### 6.1 模块依赖关系

```
cli.py (统一入口，生成 session_id trace_id)
  ├── config.settings  ──→ FTSConfig（dataclass + YAML + 环境变量）
  ├── core.atomic ──→ 原子文件操作
  ├── data ──→ FTSDataProvider
  │           ├── data_mcp ──→ 腾讯 HTTP API
  │           ├── data_fundamental ──→ MCP westock API
  │           ├── data_futures ──→ DuckDB (kline_cache)
  │           └── data_futures_fundamental ──→ AKShare
  ├── data_sources ──→ 多源期货数据适配器
  │           ├── aggregator ──→ 熔断器 + 交叉验证
  │           ├── fusion ──→ 5 种融合策略
  │           ├── tq_source ──→ 通达信本地
  │           └── wind_source / ifind_source ──→ 字段增强
  ├── factor_engine ──→ 核心因子引擎
  │   ├── contracts ──→ 所有模块依赖的核心 TypedDict 契约
  │   ├── evolution_loop ──→ macro_evolution → micro_evolution
  │   │                        → evaluation_chain → verifier
  │   │                        → audit → factor_quality_card
  │   │                        → backtest_pipeline → seed_pool
  │   │                        → experience_chain → state
  │   ├── meta_loop ──→ BootstrappingChain → L1Verifier
  │   ├── portfolio_loop ──→ signal_contract → factor_db.repository
  │   ├── expr_dsl ──→ parser → validator → executor → runtime
  │   ├── gp_evolver ──→ feature_ops（50+ 算子）
  │   ├── operator_evolution ──→ expr_dsl（ExprNode 层面）
  │   ├── factor_db ──→ DuckDB (factor_catalog)
  │   └── feedback_loop ──→ experience_chain
  ├── monitor ──→ factor_engine.monitor（底层 check_loop/check_all）
  │           ├── http_server ──→ 纯标准库 HTTPServer
  │           ├── elite_tracker ──→ core.atomic
  │           └── data_quality_monitor ──→ 独立监控
  ├── scheduler ──→ APScheduler
  ├── risk ──→ 独立风控检查（无外部依赖）
  ├── strategies ──→ base_v2 → multi_factor_strategy
  └── llm ──→ OpenAI / Anthropic SDK

pipeline/ ──→ 因子推演管线
  ├── base ──→ ProcessingStage Protocol
  └── factor_quality_inspection ──→ factor_engine
```

### 6.2 外部依赖

| 依赖 | 用途 | 必选/可选 | 安装组 |
|------|------|-----------|--------|
| numpy>=1.24 | 数值计算 | 必选 | 核心 |
| pandas>=2.0 | 数据处理 | 必选 | 核心 |
| scipy>=1.10 | 统计分析 | 必选 | 核心 |
| pyyaml>=6.0 | YAML 配置 | 必选 | 核心 |
| shap>=0.46 | SHAP 分析 | 必选 | 核心 |
| optuna>=3.0 | 贝叶斯调参 | 可选 | `[evolution]` |
| openai>=1.0 | LLM 调用 | 可选 | `[llm]` |
| anthropic>=0.20 | LLM 调用 | 可选 | `[llm]` |
| akshare>=1.18.64 | 金融数据 | 可选 | `[mcp]` |
| scikit-learn>=1.3 | 组合构建 | 可选 | `[portfolio]` |
| pytest>=7.4 | 测试 | 开发 | `[dev]` |
| APScheduler | 定时调度 | 运行时 | 需手动安装 |
| duckdb | 数据存储 | 运行时 | 需手动安装 |
| httpx | HTTP 客户端 | 运行时 | 需手动安装 |

---

## 7. 项目运行方式

### 7.1 安装

```bash
# 基础安装
pip install -e .

# 全部可选依赖（推荐开发环境）
pip install -e ".[evolution,llm,mcp,portfolio,dev]"

# 额外运行时依赖
pip install apscheduler duckdb httpx
```

### 7.2 环境配置

创建 `.env` 文件（位于项目根目录）：

```env
# LLM 配置（必需才可用 LLM 功能）
OPENAI_API_KEY=sk-xxx
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o

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

# 启动 L1 Meta-Loop（市场感知 + 种子注入）
fts meta-loop run
fts meta-loop run --market futures --symbols rb,i,au,sc

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
fts factor list --market futures --diverse --total-count 10
fts factor list --market futures --family trend --min-sharpe 1.0

# 查看种子因子
fts factor seeds --market futures

# 因子统计
fts factor stats --market futures
fts factor stats --market futures --min-sharpe 1.0 --json

# 查看单个因子详情
fts factor show <factor_id>

# 查询因子演化血缘
fts factor lineage <factor_id>

# 启动调度器（后台运行所有定时任务）
fts scheduler run

# 查看调度器任务列表
fts scheduler list

# 启动 Web UI 仪表盘
fts ui
fts ui --port 8080

# 回测单个因子
fts backtest run --factor-id <factor_id> --market futures --days 500

# 批量回测
fts backtest batch --market futures --grade B --limit 20

# 对比回测
fts backtest compare --factor-ids "fct_abc,fct_def" --market futures

# 特征工程：列出算子
fts feature list
fts feature list --category time_series --json

# 特征重要性分析
fts feature analyze --factor-id <factor_id> --market futures --days 500

# GP 遗传规划演化
fts gp evolve --universe futures --population 200 --generations 50

# 反馈闭环
fts feedback trigger --factor-id <factor_id> --reason "manual review"
fts feedback process
fts feedback report --month 2026-08
fts feedback stats
```

### 7.4 开发模式

```bash
# 运行测试
pytest
pytest tests/factor_engine/ -v              # 因子引擎专项测试
pytest tests/ -k "test_evolution_loop" -v   # 单测试文件

# 查看覆盖率
pytest --cov=fts --cov-report=term-missing

# 代码检查
ruff check fts/
```

---

## 8. 配置系统

### 8.1 配置层次

```
环境变量 (FTS_*) → YAML 配置文件 → Python 默认值（FTSConfig dataclass）
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
| `FTS_LLM_BACKEND` | `` | LLM 后端类型 |
| `FTS_LOG_LEVEL` | `INFO` | 日志级别 |
| `FTS_LOG_FILE` | `` | 日志文件路径 |
| `FTS_CONFIG_FILE` | `` | 配置文件路径 |

### 8.3 YAML 配置文件

`config/settings.yaml`:

```yaml
memory_dir: memory
elite_dir: memory/knowledge/factors/elite
futures_elite_dir: memory/knowledge/factors/futures_elite
default_market: futures
evolution_mode: hybrid
max_generations: 10
max_workers: 4
population_size: 20
micro_trials_per_generation: 50
meta_loop_interval_hours: 24
portfolio_max_factors: 20
portfolio_top_n: 5
portfolio_decay_days: 90
log_level: INFO
```

### 8.4 质量评分卡配置

配置位于 `fts/config/factor_quality_card_config.py`，支持：

- **预设配置**: `get_conservative_config()`, `get_aggressive_config()`, `get_permissive_config()`
- **自定义配置**: `create_config(weights, thresholds)`
- **期货配置**: `get_futures_config()`（降低 IC/Sharpe 阈值，适配期货低信噪比）

---

## 9. 数据流与执行流程

### 9.1 因子演化全流程

```
L1 Meta-Loop (08:30)
  │
  ├── agentic 市场感知（FTSDataProvider 获取新闻与市场快照）
  ├── debate 分析（识别论证薄弱维度）
  ├── Bootstrapping（LLM bootstrap_factors → 候选因子）
  ├── L1 Verifier（economic_logic >= 2/4 + is_executable + not_duplicate）
  └── 注入 seed_pool + factor_pool.json
        │
        ▼
L2 Evolution Loop (23:00)
  │
  ├── DataQualityMonitor 数据完整性校验
  ├── State 加载 + 熔断预检查（token 预算/失败率/连续低 IC）
  ├── UCT 父因子选择（UCB 算法，探索常数 C=1.0）
  ├── 宏观演化（LLM 修改因子逻辑）
  ├── 微观演化（optuna 100 次 trial）
  ├── 三级评估链（L1 回测 → L2 经济逻辑 → L3 多重检验）
  ├── BacktestPipeline 回测验证
  ├── Verifier 判定（6 项检查）
  ├── 质量评分卡（10 维评分，A/B/C 分级）
  ├── FactorAuditor 审计（6 项强制审计，渐进式审计）
  ├── 经验链记录（成功/失败轨迹）
  ├── 分级准入（A/B 级晋升精英，C 级淘汰）
  ├── 影子池机制（新晋升因子 5 日观察期）
  └── 状态持久化 + DuckDB 同步（idempotent write）
        │
        ▼
L3 Portfolio Loop (20:00)
  │
  ├── 加载精英因子（DuckDB / JSON 回退）
  ├── 信号合成（equal_weight / sharpe_weight / elastic_net）
  ├── Regime 自适应权重调整（可选）
  ├── 因子正交化
  ├── 衰减检验（6 个月衰减率 > 0.3 剔除）
  ├── 组合构建（含粘性约束）
  ├── Verifier 判定
  ├── 漂移监控记录
  ├── 风控检查（RiskManager: 5 项规则）
  └── 输出信号 / 注入 FDT
        │
        ▼
反馈闭环（持续）
  │
  ├── 触发条件检查（Live 偏离 / 数据异常 / 定期评估 / 审计失败 / 因子衰减）
  ├── 归因分析（5 种根因判定）
  ├── 演化方向调整
  └── 月度迭代效果报告
```

### 9.2 数据获取流程

```
期货 K 线:
  DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC
                    ↓
             字段增强（WIND / IFIND 并行）
                    ↓
             融合（MEDIAN / MEAN / WEIGHTED / HIERARCHICAL / TRIMMED_MEAN）
                    ↓
             熔断器（连续 5 次失败 → UNAVAILABLE → 6h 冷却）
                    ↓
             交叉验证（≥2 源差异 > 0.5% 记录 disagreement）
                    ↓
             数据血缘（data/data_source_disagreements.jsonl）

A 股/ETF K 线:
  腾讯 HTTP API (qt.gtimg.cn) → 合成数据降级
  基本面: MCP westock API → 合成数据降级
```

### 9.3 因子质检流程

```
因子计算 → 三级评估 → Verifier 判定
                    ↓
              质量评分卡（10 维）
                    ↓
          A 级（≥35）→ 晋升精英 + 质量分
          B 级（≥25）→ 晋升精英 + 质量分（期货 B ≥ 24）
          C 级（<25）→ 淘汰 + 记录失败轨迹
                    ↓
              FactorAuditor 审计（6 项强制）
                     ↓
              6 项全部通过 → DuckDB 同步（idempotent write）
              任一未通过 → 阻断晋升
```

---

## 10. 测试体系

### 10.1 测试概况

- **测试文件**: 103+ 个
- **测试用例**: 2900+ 个
- **覆盖率**: 92%+
- **测试框架**: pytest + pytest-cov
- **测试配置**: `pyproject.toml` 中 `[tool.pytest.ini_options]`

### 10.2 测试目录结构

```
tests/                          # 103+ 测试文件，2900+ 测试用例
├── core/                       # 核心契约层测试（3 个文件）
│   ├── test_atomic.py          # 原子文件操作
│   ├── test_contracts.py       # 契约定义
│   └── test_enums.py           # 枚举定义
├── data_sources/               # 数据源测试（7 个文件）
│   ├── test_aggregator.py      # 多源调度器
│   ├── test_base.py            # 基类
│   ├── test_fusion.py          # 融合器
│   ├── test_ifind_source.py    # iFinD 源
│   ├── test_migrate.py         # 迁移
│   ├── test_tq_source.py       # 通达信源
│   └── test_wind_source.py     # Wind 源
├── factor_engine/              # 因子引擎测试（54 个文件，核心）
│   ├── expr_dsl/               # DSL 测试（6 个文件）
│   │   ├── test_parser.py      # 解析器
│   │   ├── test_registry.py    # 注册表
│   │   ├── test_validator.py   # 验证器
│   │   ├── test_executor.py    # 执行器
│   │   ├── test_compiler.py    # 编译器
│   │   └── test_factory.py     # 工厂
│   ├── factor_db/              # 数据库测试
│   │   └── test_data_layer_repos.py
│   ├── operator_evolution/     # 算子演化测试
│   │   └── test_operator_evolution.py
│   ├── test_evolution_loop.py  # L2 主循环
│   ├── test_meta_loop.py       # L1 元循环
│   ├── test_portfolio_loop.py  # L3 组合循环
│   ├── test_backtest_pipeline.py  # 回测管线
│   ├── test_feedback_loop.py   # 反馈闭环
│   ├── test_gp_evolver.py      # GP 演化
│   ├── test_quality_card.py    # 质量评分卡
│   ├── test_audit.py           # 审计
│   ├── test_executor_dispatch.py # 执行器分派
│   ├── test_contracts_kind.py  # 契约类型
│   └── ...（共 54 个测试文件）
├── monitor/                    # 监控测试（8 个文件）
│   ├── test_data_quality_monitor.py
│   ├── test_elite_tracker.py
│   ├── test_elite_tracker_edge.py
│   ├── test_logic_monitor.py
│   ├── test_prometheus_setup.py
│   └── test_stage2_monitor_scheduler.py
├── pipeline/                   # 管线测试（5 个文件）
│   ├── test_base.py
│   ├── test_factor_combiner.py
│   ├── test_factor_quality_inspection.py
│   ├── test_batch_quality_inspector.py
│   └── test_multi_symbol_compatibility.py
├── scheduler/                  # 调度测试（5 个文件）
│   ├── test_engine.py
│   ├── test_tasks.py
│   ├── test_watchdog.py
│   ├── test_hotswap.py
│   └── test_sync_futures_task.py
├── strategies/                 # 策略测试（3 个文件）
│   ├── test_base_v2.py
│   ├── test_multi_factor.py
│   └── test_strategy_evolution.py
├── scenarios/                  # 场景测试（2 个文件）
│   ├── test_natural_experiments.py
│   └── test_scenarios.py
├── cli/                        # CLI 测试（1 个文件）
│   └── test_data_cli.py
├── test_e2e.py                 # 端到端测试
├── test_llm.py                 # LLM 测试
├── test_data.py                # 数据层测试
├── test_data_cache.py          # 缓存测试
├── test_data_fundamental.py    # 基本面测试
├── test_talib_bridge.py        # TA-Lib 桥接测试
├── test_http_server.py         # HTTP 服务器测试
├── test_monitor.py             # 监控测试
├── test_config_settings.py     # 配置测试
├── test_cli.py                 # CLI 通用测试
├── test_cli_feature_gp.py      # CLI 特征/GP 测试
├── test_elite_tracker.py       # 精英因子跟踪（根目录）
├── test_futures_signal_pipeline.py # 期货信号管道
├── test_stage5_risk_live.py    # Phase 5 风险实盘测试
├── test_data_futures_panel.py  # 期货面板数据测试
└── generate_html_report.py     # HTML 报告生成
```

### 10.3 运行测试

```bash
# 全量测试
pytest

# 带覆盖率
pytest --cov=fts --cov-report=term-missing

# 指定模块
pytest tests/factor_engine/ -v
pytest tests/factor_engine/expr_dsl/ -v

# 指定测试文件
pytest tests/factor_engine/test_evolution_loop.py -v

# 关键字过滤
pytest -k "test_evolution" -v
```

---

## 11. 附录 A: 版本历史

| 版本 | 日期 | 主要变更 |
|------|------|---------|
| v2.14.0 | 2026-08-06 | 当前版本，全面算子演化支持 + 9 设计实现 |
| v2.9.0 | 2026-07-31 | 新增 S1-S6 设计：数据层/监控/回测/CLI/实盘/反馈 |
| v2.7.0 | 2026-07-24 | 多源数据融合、期货基本面、数据血缘 |
| v2.3.0 | 2026-07-10 | 期货多源数据适配器、熔断器、5 种融合策略 |
| v1.1.0 | 2026-06-15 | L1/L2/L3 三层循环架构初始实现 |
| v0.1.0 | 2026-05-01 | 初始版本，基础因子引擎 |

## 12. 附录 B: 相关文档

| 文档 | 位置 | 说明 |
|------|------|------|
| FTS 手册 | `docs/FTS_manual.md` | 完整用户手册 |
| 业务流程图 | `docs/business_flow.md` | 业务流程说明 |
| 生产计划 | `docs/production_plan.md` | 生产就绪计划 |
| 架构文档 | `docs/harness/01-architecture.md` | Harness 架构规范 |
| 生命周期 | `docs/harness/02-lifecycle.md` | 阶段生命周期定义 |
| 配置文档 | `docs/harness/03-configuration.md` | 配置系统规范 |
| 弹性文档 | `docs/harness/04-resilience.md` | 熔断/降级/超时 |
| 可观测性 | `docs/harness/05-observability.md` | 指标/日志/监控 |
| 测试文档 | `docs/harness/06-testing.md` | 测试用例与覆盖率 |
| 操作文档 | `docs/harness/07-operations.md` | 版本历史与操作手册 |
| 差距管理 | `docs/harness/08-gap-analysis.md` | 技术债务登记 |
| 晋级计划 | `docs/harness/09-advancement-plan.md` | 晋级里程碑 |
| 设计文档 | `docs/harness/design/` | A.1-C.4 共 10 个设计文档 |
| 验收测试 | `docs/harness/acceptance/` | 各阶段验收测试文档 |