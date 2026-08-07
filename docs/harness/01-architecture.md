# FTS 系统架构文档

> 版本: v2.22.0
> 最后更新: 2026-08-07

---

## 1. 项目概述

FTS（Factor Intelligence System，因子智能系统）是一个独立的因子策略系统，专注于因子推演、策略组建与交易信号产出。数据层基于腾讯自选股 MCP (akshare) 提供 A 股/ETF/期货行情数据，FTS **本身包含自洽的数据源适配层**，无外部数据项目依赖。

### 项目边界

| 职责 | 归属 |
|:-----|:-----|
| 行情数据获取（A 股/ETF OHLCV） | **FTS（通过 MCP/akshare 接入腾讯/东方财富 API）** |
| 行情数据获取（期货 OHLCV） | **FTS（通过 DuckDB kline_cache + AKShare futures_zh_daily_sina）** |
| 因子推演（挖掘/演化/评估） | **FTS 核心能力** |
| 多因子策略组建 | **FTS 核心能力** |
| 交易信号产出 | **FTS 核心能力** |
| 循环调度与状态管理 | **FTS 核心能力** |
| 健康监控与 HTTP 指标 | **FTS 核心能力** |

## 2. 分层架构

FTS 采用 5 层分层架构，从高层的人类设定到底层的组合执行：

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          入口层 (Entry Layer)                           │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │ cli.py       │  │ scheduler/       │  │ monitor/                 │  │
│  │ 统一命令行入口  │  │ 定时任务调度       │  │ 系统健康监控 + HTTP 端点  │  │
│  └──────┬───────┘  └────────┬─────────┘  └───────────┬──────────────┘  │
└─────────┼───────────────────┼─────────────────────────┼────────────────┘
          │                   │                         │
          ▼                   ▼                         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    L0 人类设定层 (Human Configuration)                   │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ program.py (Program.md)                                         │   │
│  │ 人类通过 Program.md 文件设定因子演化的目标、约束、市场偏好、       │   │
│  │ 风险偏好等最高层级指令。L1/L2/L3 均受 program.md 约束。          │   │
│  └──────────────────────────┬──────────────────────────────────────┘   │
└─────────────────────────────┼──────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│    L1 Meta-Loop (元循环 — 知识感知与市场监控层)                          │
│                                                                         │
│  meta_loop.py                       experience_chain.py                │
│  - BootstrappingChain（市场知识补给）  - 经验链存储                       │
│  - DebateQualityAnalyzer（辩论质量分析）                                 │
│  - FactorPoolManager（因子池管理）                                      │
│  - L1Verifier（L1 锁定协议）                                           │
│  - MetaStateManager（状态管理）                                         │
│                                                                         │
│  职责: 每日知识补给 → 种子因子注入 → 市场语境感知 → 演化方向指引        │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │ 注入种子因子 + 演化方向
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  L2 Evolution Loop (演化循环 — 因子核心演化层)                           │
│                                                                         │
│  ┌─ 股票 L2 (单标的时序) ─────────────────────────────────────────┐     │
│  │ seed_correlation_check → parent_selection → macro_evolution    │     │
│  │ (种子因子相关性预检        (UCT 树搜索)      (LLM 改逻辑)       │     │
│  │  时序 Pearson+Spearman)                                              │     │
│  │   → micro_evolution → evaluation_chain → elite                    │     │
│  │   (optuna 调参)      (三级评估链)                                  │     │
│  │                                                                         │
│  │   种子池: 482 因子 (9 内置+WQ101+Qlib158+GTJA191+23 基本面)       │     │
│  │   数据: 单标的 OHLCV 时序                                           │     │
│  │   评估: EvaluationChain 三级评估                                    │     │
│  └─────────────────────────────────────────────────────────────────────┘     │
│                                                                         │
│  ┌─ 期货 L2 (横截面面板) ─────────────────────────────────────────┐     │
│  │ parent_selection → evolution_mode → micro_evolution              │     │
│  │ (UCT 树搜索)       (code/hybrid/operator)  (optuna 调参)         │     │
│  │                      ├─ LLM 改逻辑 (code)                        │     │
│  │                      ├─ GP 演化 (code/hybrid fallback)            │     │
│  │                      ├─ 算子演化 (operator/hybrid fallback)       │     │
│  │                      └─ FTS-Expr DSL (Phase C.2)                  │     │
│  │   → cross_section_evaluate → elite                                │     │
│  │   (横截面直接回测)                                                  │     │
│  │                                                                         │
│  │   种子池: 81 期货因子 (14 家族: 动量5/期限结构3/持仓3/流动性3/     │     │
│  │          高阶矩3/波动率2/基本面4/拥挤度6/Alpha4/高频6/期权3/       │     │
│  │          市场环境8/CTA补充7/算子字典24)                             │     │
│  │   数据: 82 品种 OHLCV 面板 (common_dates 多数对齐)                 │     │
│  │   评估: cross_section_evaluate_backtest (因子加权=Ridge)           │     │
│  │   相关性预检: 跳过 (横截面无单标的信号)                             │     │
│  └─────────────────────────────────────────────────────────────────────┘     │
│                                                                         │
│  evolution_loop.py — L2 主循环协调器 (通过 cross_section 参数区分模式)    │
│  seed_pool.py — 双种子池管理 + compute_seed_correlations() 时序相关性预检 │
│  factor_program.py — 因子程序（图灵完备代码 + 安全沙箱）                  │
│  verifier.py — Verifier 锁定协议                                       │
│  state.py — 演化状态管理 + trace_id 全链路                              │
│  gp_evolver.py — GP 遗传规划搜索引擎 (Phase C.1)                        │
│  expr_dsl/ — FTS-Expr 算子表达式语言 (Phase C.2)                        │
│    registry.py — 58 算子注册表 (L0-L5 分层, 参数边界, 经济语义)          │
│    parser.py — 递归下降解析器 → AST                                    │
│    validator.py — 静态校验 (参数边界, 最大 lookback, PIT)               │
│    executor.py — 解释执行器 (pandas 向量化快速路径)                      │
│    compiler.py — 编译器 (表达式 → 确定性沙箱代码)                       │
│    factory.py — 算子因子工厂 (FTS-Expr → FactorProgram)                │
│                                                                         │
│  GP 演化支持多父代交叉策略:                                              │
│  - 标准双亲交叉 (70% 概率)                                              │
│  - 多父代交叉 (30% 概率, 3 父代融合)                                     │
│  - 锦标赛选择 n 个父代 (_tournament_select_n)                          │
│  - 多父代交叉提升种群多样性, 避免局部最优                                │
│                                                                         │
│  职责: 夜间批量演化 → 父因子选择 → 演化模式分派(code/hybrid/operator) →  │
│        optuna 参数优化 → 评估 → 审计 → 4 重审查门禁 → 家族多样性约束(max_per_family=3) → elite 因子 →       │
│        传递相关性预检结果给 L3                                           │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │ elite 因子
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  L3 Portfolio Loop (组合循环 — 组合构建与信号产出层)                     │
│                                                                         │
│  portfolio_loop.py                                                     │
│  - PortfolioManager（组合管理器，含 combo_history 归档）                │
│  - orthogonalize_factors（因子正交化）                                  │
│  - decay_test（衰减检验）                                              │
│  - build_combo（构建组合，支持粘性约束）                                │
│  - synthesize_signals（信号合成）                                      │
│  - generate_agent_proposals（Agent 提案生成）                          │
│  - load_elite_factors（加载 elite 因子，过滤影子池观察期因子）          │
│  - L3Verifier（L3 锁定协议）                                           │
│  - DriftMonitor（组合漂移监控：成员重合率 + 权重 L1 变化率）            │
│  - _apply_sticky_constraints（粘性约束：±30% 变动 / 新因子首日封顶）    │
│                                                                         │
│  职责: 组合构建 → 正交化 → 衰减检验 → 粘性约束 → 漂移监控 → 信号合成    │
└─────────────────────────────────────────────────────────────────────────┘
```

### 层间交互

- **L0 → L1**: Program.md 设定 L1 的搜索空间、预算、市场偏好
- **L1 → L2**: 注入种子因子 + 演化方向指引（通过 seed_pool.inject()）
  - 股票 L2: 482 股票因子种子池（时序模式）
  - 期货 L2: 81 期货因子种子池（14 家族，横截面模式）
- **L2 → L3**: 
  - 股票 L2: elite 因子（写入 memory/knowledge/factors/elite/）+ 种子因子相关性预检结果（`seed_correlations` 通过 EvolutionRunResult 传递给 L3 组合阶段参考）
  - 期货 L2: elite 因子 + 横截面评估指标 + 因子加权权重（Ridge 回归）

---

## 3. 模块结构

```
fts/
├── __init__.py                 # 包入口 + 版本号 v2.3.0
├── cli.py                      # 统一命令行入口
├── data.py                     # 数据层统一入口（股票/ETF/期货）
├── data_cache.py               # 数据缓存管理
├── data_mcp.py                 # MCP 数据适配层（akshare 腾讯/东方财富）
├── data_mcp_bridge.py          # MCP Bridge 桥接层
├── data_futures.py             # 期货数据适配层（DuckDB kline_cache + AKShare）
├── data_futures_fundamental.py # 期货基本面数据（库存/仓单/基差）
├── data_fundamental.py         # 股票基本面数据层
├── llm.py                      # LLM 客户端（OpenAI/Anthropic/Mock）
├── talib_bridge.py             # TA-Lib 技术指标桥接
├── config/                     # 配置系统
│   └── settings.py             # FTSConfig + load_config()
├── core/                       # 核心契约层
│   ├── contracts.py            # TypedDict 契约 re-export
│   ├── atomic.py               # 原子文件操作
│   └── enums.py                # 枚举定义
├── data_sources/               # 数据源层（多源融合）
│   ├── __init__.py
│   ├── base.py                 # 数据源抽象基类
│   ├── aggregator.py           # 多源聚合器
│   ├── fusion.py               # 数据融合引擎
│   ├── ifind_source.py         # iFinD 数据源
│   ├── wind_source.py          # Wind 数据源
│   ├── tq_source.py            # 通达信数据源
│   └── migrate.py              # 数据迁移工具
├── factor_engine/              # 因子引擎（核心模块）
│   ├── __init__.py             # 模块入口 + 版本号 v1.1.0
│   ├── contracts.py            # 完整 TypedDict 契约（L1+L2+L3）
│   ├── evolution_loop.py       # L2 主循环（股票时序/期货横截面双模式）
│   ├── meta_loop.py            # L1 元循环
│   ├── portfolio_loop.py       # L3 组合循环
│   ├── macro_evolution.py      # LLM 宏观演化
│   ├── micro_evolution.py      # optuna 微观调参
│   ├── evaluation_chain.py     # 三级评估链
│   ├── experience_chain.py     # 经验链存储
│   ├── factor_optimizer.py    # 因子优化器
│   ├── seed_data_futures_full.py # 期货全量种子因子（14 家族 81 因子）
│   ├── seed_pool.py            # 双种子池（股票 482 + 期货 81）+ 种子因子相关性预检
│   ├── factor_program.py       # 因子程序（安全沙箱）
│   ├── standardizer.py        # 因子标准化
│   ├── verifier.py             # Verifier 锁定协议
│   ├── state.py                # 演化状态管理 + trace_id 全链路
│   ├── program.py              # L0 人类设定（Program.md）
│   ├── walk_forward.py         # 走航验证
│   ├── cost_model.py           # 交易成本模型
│   ├── regime.py               # 市场制度检测（RegimeAwareSelector + SectorRegimeSelector 产业链级）
│   ├── stress_test.py          # 压力测试
│   ├── ablation.py             # 输入敏感性消融实验（Phase A 逻辑审查）
│   ├── shap_analyzer.py        # SHAP 局部可解释性分析（Phase B 逻辑审查）
│   ├── robustness.py           # 鲁棒性审查（Phase B 逻辑审查）
│   ├── causal_validator.py     # 因果结构审查（Phase C 逻辑审查）
│   ├── audit.py                # 因子审计（FactorAuditor + FailureClassifier 集成）
│   ├── failure_classifier.py   # 失败模式分类器（10 种失败模式 + 改善建议）
│   ├── factor_lineage.py       # 因子血缘追踪（谱系/趋势/退化检测/批量审计）
│   ├── factor_inspector.py     # 定时巡检（自动检测退化因子并降级）
│   ├── monitor.py              # 循环监控
│   ├── factor_quality_card.py  # 因子质量评分卡（10 维评分，A/B/C 分级准入）
│   ├── adaptive_weight.py      # 自适应权重（AdaptiveWeightManager + RegimeSmoother 热更新）
│   ├── feature_ops.py          # 特征算子注册表（50 算子 / 7 类）
│   ├── feature_importance.py   # 特征重要性分析（置换重要性）
│   ├── gp_evolver.py           # GP 演化器（ExpressionTree + 交叉/变异）
│   ├── operator_evolution.py   # 算子演化引擎 (Phase 3+ / C.4)：DSL 算子空间进化式搜索
│   ├── backtest_pipeline.py    # 回测流水线（B.2）：run_batch 批量对比 + Builder
│   ├── factor_screener.py      # 回测阶段 1：因子筛选
│   ├── signal_generator.py     # 回测阶段 2：时序/横截面信号生成
│   ├── portfolio_constructor.py# 回测阶段 3：等权/Sharpe/自适应组合构建
│   ├── cost_simulator.py       # 回测阶段 4：交易成本模拟（品种差异化费率）
│   ├── risk_attributor.py      # 回测阶段 5：风险归因（因子贡献/暴露/VaR-ES）
│   ├── report_generator.py     # 回测阶段 6：Markdown 报告
│   ├── capital_allocator.py    # 资金分配（fixed/vol_target/risk_parity/kelly）
│   ├── signal_contract.py      # 信号契约（C.2）：FactorSignal + SignalValidator
│   ├── feedback_loop.py        # 反馈闭环（C.3）：Trigger/归因/方向调整/效果评估
│   └── expr_dsl/               # 算子演化基础层 (Phase C.2): FTS-Expr DSL
│       ├── parser.py           # 递归下降解析器 (表达式 → AST)
│       ├── validator.py        # 静态校验 (算子/字段/参数边界/max_lookback PIT)
│       ├── registry.py         # 算子注册表 (语义/梯度/边界, L0-L5 分层)
│       ├── executor.py         # AST 解释执行 (pandas 向量化快速路径)
│       ├── compiler.py         # DSL → 确定性沙箱安全 code
│       ├── runtime.py          # 沙箱 runtime 桥接 (eval_fts_expr)
│       └── factory.py          # 算子因子工厂 (FTS-Expr → FactorProgram)
├── pipeline/                   # 因子推演管线
│   ├── base.py                 # FactorPipeline 抽象基类
│   └── factor_combiner.py      # 因子组合器
├── strategies/                 # 策略层
│   ├── base_v2.py              # BaseStrategyV2
│   ├── multi_factor_strategy.py# 多因子策略
│   └── strategy_evolution.py   # 策略进化（RegimeAdaptive/DynamicWeight/MultiPeriodFusion）
├── monitor/                    # 健康监控
│   ├── __init__.py             # 状态报告函数
│   ├── http_server.py          # HTTP 监控端点（/metrics 含 Prometheus 指标、/api/v1/*）
│   ├── prometheus_metrics.py   # Prometheus 指标注册表（衰减/Regime/权重/质量/Live/风控/反馈）
│   ├── elite_tracker.py        # Elite 因子追踪
│   ├── data_quality_monitor.py # 数据质量监控（B.1）：完整性/准确性/及时性三维指标
│   ├── live_factor_monitor.py  # Live 因子偏离监控（C.2）：30% 偏离阈值
│   ├── logic_monitor.py        # 逻辑监控仪表盘（Phase C）
│   ├── k8s_deploy.py          # K8s 部署配置
│   └── prometheus_setup.py     # Prometheus 指标配置
├── risk/                       # 风控层（C.2）
│   ├── __init__.py             # 导出 RiskManager/TradeAdapter/SimulatedTradeAdapter
│   ├── risk_manager.py         # RiskManager 五项风控规则（仓位/回撤/亏损/杠杆/集中度）
│   ├── trade_adapter.py        # TradeAdapter 抽象基类（Liskov 替换）
│   └── simulated_adapter.py    # SimulatedTradeAdapter 模拟成交
└── scheduler/                  # 调度层
    ├── __init__.py             # 模块入口 + 导出
    ├── engine.py               # SchedulerEngine（APScheduler 包装器）
    ├── tasks.py                # TaskRegistry + TaskSpec + 注册默认任务（6 个）
    ├── jobs.py                 # 任务工作函数（L1/L2/L3/信号管道/健康检查/因子巡检/月度衰减/数据质量）
    ├── hotswap.py              # 热更新支持
    └── watchdog.py             # 看门狗进程
├── factor_db/                   # DuckDB 因子数据库层
    ├── schema.py               # 数据库 Schema 定义（12 张表，含质量评分/状态历史/审计报告/反馈 4 表 + seed_lineage 溯源）
    ├── repository.py           # FactorRepository CRUD（含 `retire_factor()` 因子淘汰方法）
    ├── quality_repository.py   # FactorQualityScoreRepository（质量评分持久化）
    ├── status_repository.py    # FactorStatusRepository（生命周期状态历史，记录状态变迁日志）
    ├── audit_repository.py     # FactorAuditReportRepository（审计报告持久化）
    ├── lineage.py              # FactorLineage 血缘追踪 + 批量审计
    └── correlations.py         # 因子相关性矩阵
```

### 算子演化基础层（Phase C.2）

算子因子与代码因子都表现为 `FactorProgram`（对上层透明）。本区块落地算子因子的"第一公民"基础能力：FTS-Expr DSL、算子注册表元数据、FactorProgram kind 扩展、FactorExecutor 按 kind 分派。

1. **FTS-Expr DSL 层**：`fts/factor_engine/expr_dsl/` 包（parser → validator → executor/compiler → runtime），表达式为受控函数调用形式，如 `rank(ts_zscore(close, 60))`。解析器（递归下降）转 AST，校验器做静态分析，执行器直接解释 AST 走算子快速路径（复用 `feature_ops.py` 既有算子实现，pandas 向量化），编译器生成确定性沙箱安全代码。
2. **因子双表达**：`FactorProgram` 新增可选字段 `kind`/`expression`/`operator_depth`/`operator_count`/`max_lookback`；`kind` 枚举 `operator`/`code`/`hybrid`，存量因子经 `normalize_factor_program` 默认 `code`（向后兼容，对上层零破坏）。算子因子保留确定性生成的 `code`，持久化/评估链/Verifier 零改动。
3. **执行分派**：`FactorExecutor.execute()` 按 `kind` 分派——`operator` 走 DSL 解释执行（快速路径，异常回退沙箱），`code` 走现有沙箱路径。评估链/Verifier 接口不变。
4. **接口契约**：FactorKind 枚举与新增可选字段说明见第 5 节「关键契约」— `### FactorKind 枚举与 FactorProgram 可选字段（Phase C.2）`。
5. **架构数据流**：

```
FTS-Expr 表达式 (如 rank(ts_zscore(close, 60)))
    │
    ▼ parser.py (递归下降)
AST (ExprNode 树)
    │
    ├─→ validator.py 校验器静态分析: 算子存在性 / 参数边界 / 最大 lookback (PIT 防未来函数)
    ├─→ executor.py  执行器向量化计算: pandas Series 快速路径 (复用 feature_ops 50 算子)
    └─→ compiler.py  编译器生成确定性沙箱 code → runtime.py 桥接 (eval_fts_expr)
```

### 算子演化引擎（Phase 3+ / C.4）

在 DSL 算子空间做**适应度导向的进化式搜索**，取代 `_generate_operator_factor` 的纯随机组合。`fts/factor_engine/operator_evolution.py` 提供 `OperatorEvolutionEngine`：种群初始化（validator 校验通过）→ 适应度评估（DSL executor → IC/Sharpe）→ 锦标赛选择 → 子树交叉/变异（ExprNode 层面，参数受 `param_bounds` 约束）→ 精英保留，多代迭代后取最优表达式经 `create_operator_factor` 产出 `kind=OPERATOR` 因子。设计文档见 [C.4-operator-evolution-engine-design.md](design/C.4-operator-evolution-engine-design.md)。GAP-026（GP 算子命名与 DSL 未对齐）随本引擎落地关闭。

---

## 4. 数据流

### 全局数据流

```
MCP/akshare (腾讯自选股/东方财富 API)     DuckDB kline_cache (期货)
    │                                          │
    │ OHLCV K 线数据 (A 股 / ETF)              │ OHLCV 日线 (期货连续合约)
    │                                          │
    ▼                                          ▼
FTS (因子推演) — 支持 A 股/ETF/期货横截面因子演化
    │
    │ 因子引擎 → 策略组建 → 交易信号
    ▼
下游系统（信号消费方）
```

### 期货数据流

```
AKShare futures_zh_daily_sina
    │
    │ scripts/download_futures.py（断点续传）
    ▼
DuckDB kline_cache (data/fts_history.duckdb)
    │
    │ FuturesDataProvider._from_kline_cache()     ← 优先级1
    │ AKShare 即时获取（降级）                       ← 优先级2
    │ 合成数据（降级）                                ← 优先级3
    ▼
FTSDataProvider.get_futures_ohlcv() / get_futures_panel()
    │
    │ --universe futures
    ▼
EvolutionLoop（期货横截面因子演化，跨品种因子计算）
    │
    ▼
scripts/futures_signal_pipeline.py（横截面信号管道，方向校正 = 截面 IC 法，因子加权 = Ridge 回归 L2 正则化，Market Regime 检测 = RegimeAwareSelector）
    │
    ▼
reports/{date}/futures_signals_{date}.md
```

**common_dates 语义（v1.7.1）**：
- `get_futures_panel()` 返回的 `common_dates` 由「全品种日期交集」改为「多数对齐」：
  取至少 `max(2, 品种数//2)` 个品种共有的日期。
- 原因：全交集在 76 个商品期货（FUTURES_SUBSET）下会因个别停更品种
  （WH0/JR0/RI0/LR0 数据止于 2022-2023）将交集清空，导致横截面方向校正
  （截面 IC 法）静默失效，全部因子 flip=1.0。
- 方向校正按日期定位（`df.index.get_loc`）而非位置索引，避免品种间
  日期错位污染 IC 计算。
- 信号管道剔除数据陈旧品种：最新交易日早于共同日期末端（如已停更的
  WH0/JR0/RI0/LR0）的品种不参与横截面排名，防止陈旧价格混入当前信号。
- 信号报告输出品种中文名称（FUTURES_SYMBOL_NAMES 映射）与主力合约代码
  （get_dominant_contracts() 按 contract_kline 最新交易日最大成交量判定）。
- L3 定时任务（20:00）触发信号管道时使用 `--universe all` 全量商品池。

**因子加权方法（v1.7.3 — Ridge 回归）**：
- 基于 Shen & Xiu 的弱信号理论：当因子信号普遍较弱时，L2 正则化（Ridge）优于 L1 选择（Lasso/硬阈值）。
- 使用全部精英因子（不按 IC 过滤），以 Ridge 回归学习差异化权重：
  强因子自动获得高权重，弱因子获得接近零的权重但不被丢弃。
- 这替代了 v1.7.2 的 IC>0.3 硬过滤 + 等权合成。
- 实现：`_compute_ridge_weights()` 在 `scripts/futures_signal_pipeline.py`。

**Market Regime 检测（v1.8.1 / v2.20.0 产业链级）**：
- 信号管道在数据加载后、信号计算前，调用 `SectorRegimeSelector.detect_all()` 按产业链独立检测市场制度。
- 检测方法：对每个产业链，从品种面板构建合成 OHLCV（取所有品种 close 截面均值作为产业链综合价格序列），计算 MA20 斜率、ATR/价格、量比、收益自相关，分层判定制度类型。
- 制度类型：bull（趋势上涨）/ bear（趋势下跌）/ high_vol（高波动）/ low_vol（低波动）/ oscillate（震荡）。
- 主制度计算：品种数加权投票（各产业链按其品种数决定权重，消除全市场单一制度对不同产业链结构性机会的掩盖）。
- 报告输出：主制度名称 + 置信度 + 产业链 Breakdown（各产业链制度/置信度/品种数/方向建议）+ Regime 调整后的交易建议。
- 趋势友好（bull/bear）→ 优先做空/做多增量最强的品种，可放大仓位；震荡（oscillate）→ 反向操作；高波动（high_vol）→ 缩小仓位，只做增量绝对值 > 0.15 的品种。
- 实现：`SectorRegimeSelector` 在 `fts/factor_engine/regime.py`，每个产业链使用独立的 `RegimeAwareSelector` 实例保持状态隔离。
- 产业链分类：`FUTURES_SECTOR_MAP` 定义 7 个产业链（黑色系/有色金属/能源化工/农产品/软商品/贵金属/金融期货），每产业链品种不足 2 个或数据不足 20 行时跳过。

### FTS 内部数据流

```
Program.md (L0 人类设定)
    │
    ▼
L1 Meta-Loop ──→ 知识补给 + 种子注入 ──→ seed_pool.py
    │                                       │
    │                                       ▼
    │                              ┌─ 股票 L2 Evolution Loop ─┐
    │                              │ seed_correlation_check    │
    │                              │ (时序 Pearson+Spearman)   │
    │                              │ → parent_selection (UCT) │
    │                              │ → macro_evolution (LLM)  │
    │                              │ → micro_evolution (optuna)│
    │                              │ → evaluation_chain (3级) │
    │                              │ → elite + correlations   │
    │                              └───────────────────────────┘
    │                                       │
    │                                       ▼
    │                              ┌─ 期货 L2 Evolution Loop ─┐
    │                              │ parent_selection (UCT)   │
    │                              │ → macro_evolution (LLM)  │
    │                              │ → micro_evolution (optuna)│
    │                              │ → cross_section_evaluate │
    │                              │ → elite (81因子 × 14家族)│
    │                              └───────────────────────────┘
    │                                       │
    │                                       ▼
    │                              elite 因子 (JSON 快照 + DuckDB catalog 双写，GAP-032)
    │                                       │
    │                                       ▼
    └──────────────────────→ L3 Portfolio Loop
                              ├── 正交化
                              ├── 衰减检验
                              ├── 组合构建
                              └── 信号合成

### 因子淘汰流（v2.17.0）

因子淘汰是主流程的正式环节，通过月度衰减评估触发，确保退化因子从活跃池中移除：

```
monthly_decay_eval_job (每月1日 02:00)
    │
    ├── EliteFactorTracker.run_monthly_evaluation() → 快照状态标记
    ├── AutoRetireManager.run() → 识别需淘汰因子
    │
    └── FactorRepository.retire_factor(factor_id, reason, elite_dir)
            │
            ├── 1. FactorStatusRepository.update_factor_status() → DuckDB status = "retired"
            ├── 2. FactorStatusRepository.log_transition() → 记录状态变迁（old_status → retired）
            ├── 3. 移动 JSON 快照到 elite/_retired/{factor_id}.json
            │
            └── 因子从活跃池移除，不再参与 L3 组合构建与信号合成
```

---

## 5. 关键契约

### TraceID 全链路

`trace_id` 必须贯穿所有模块、文档和日志。生成规则：

```python
# fts.factor_engine.state.generate_trace_id()
trace_id = f"{prefix}_{8hex}_{timestamp}"
```

所有 CLI 子命令在启动时生成 `trace_id`，通过参数或全局变量传递到各层循环。

### Verifier 锁定协议

Verifier 是 FTS 的核心安全机制，锁定后不可逆：

- **L1 Verifier**: 控制 L1 种子注入和知识补给
- **L2 Verifier**: 控制 L2 因子演化流程
- **L3 Verifier**: 控制 L3 组合构建和信号产出
- 锁定后只能读取，无法修改配置

### FactorCorrelation 契约

L2 种子因子相关性预检产物，记录高相关因子对（仅标记不删除，供 L3 组合阶段参考）：

```python
class FactorCorrelation(TypedDict):
    factor_id_a: str      # 因子 A ID
    factor_id_b: str      # 因子 B ID
    pearson: float         # Pearson 相关系数
    spearman: float        # Spearman 秩相关系数
```

阈值默认 0.95，仅标记 `max(|pearson|, |spearman|) >= 0.95` 的因子对。

### Program.md 约定

人类通过 `Program.md` 文件设定 FTS 的最高层级指令：

- ProgramConfig: 目标、约束、市场偏好、风险偏好
- `parse_program_md()`: 解析 Program.md → ProgramConfig
- `load_program()`: 加载并验证 Program 配置

### FactorKind 枚举与 FactorProgram 可选字段（Phase C.2）

算子演化基础层为 `FactorProgram` TypedDict 追加可选字段（契约向后兼容扩展，全字段可选，存量因子经 `normalize_factor_program` 默认 `kind=CODE`）：

```python
class FactorKind(str, Enum):
    """因子表达类型。
    - OPERATOR: 算子表达式 (FTS-Expr DSL)，经 OperatorRegistry 解释执行
    - CODE: 代码级因子 (Python 沙箱)，现有默认类型
    - HYBRID: 算子外壳 + 代码内核 (预留，本计划仅定义枚举，消费在后续计划实现)
    """
    OPERATOR = "operator"
    CODE = "code"
    HYBRID = "hybrid"
```

`FactorProgram` 新增可选字段（`is_multi_symbol` 之后）：

```python
    kind: Optional[FactorKind]     # 因子表达类型 (默认 code, 向后兼容)
    expression: Optional[str]      # 算子因子表达式 (FTS-Expr DSL)
    operator_depth: Optional[int]  # 表达式 AST 深度
    operator_count: Optional[int]  # 算子个数
    max_lookback: Optional[int]    # 最大 lookback (PIT 静态分析, 防未来函数)
```

---

## 6. 各层循环运行时间

| 循环 | 触发时间 | 频率 | 职责 |
|:-----|:---------|:-----|:-----|
| L1 Meta-Loop | 08:30 | 每日 | 知识补给 + 种子注入 |
| L2 Evolution Loop | 23:00 | 每日 | 夜间因子演化 |
| L3 Portfolio Loop | 20:00 | 每日 | 组合构建 + 正交化 + 信号合成 |
| 期货信号管道 | 20:30 | 每日 | 横截面信号报告（全量因子 Ridge 回归加权） |
| 因子巡检 (FactorInspector) | 21:00 | 每日 | 基于 batch_audit 自动检测退化因子并降级 |
| Health Check | 每 10 分钟 | 高频 | 状态监控 |

---

## 7. 技术栈

- **语言**: Python 3.10+
- **核心依赖**: numpy, pandas, pyyaml
- **演化依赖（可选）**: optuna (evolution extra)
- **LLM 依赖（可选）**: openai, anthropic (llm extra)
- **数据依赖（可选）**: akshare >= 1.18.64 (mcp extra)
- **期货数据（可选）**: duckdb >= 0.8.0, akshare >= 1.18.64
- **测试**: pytest 7.4+, pytest-cov 4.1+
- **打包**: setuptools, pyproject.toml

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `seed_pool.py` → 双种子池（股票 482 因子：9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面；期货 81 因子：14 家族，见 seed_data_futures_full.py）；种子因子相关性预检（compute_seed_correlations，仅股票时序模式，≥0.95 标记高相关对）；`data_fundamental.py` → FundamentalProvider 基本面数据层；`data_futures.py` → FuturesDataProvider 期货数据层（82 品种 FUTURES_SUBSET + 59 个品种 DuckDB 缓存 + AKShare 降级，`get_futures_panel()` common_dates 多数对齐 ≥ 品种数//2，FUTURES_SYMBOL_NAMES 名称映射，get_dominant_contracts() 主力合约判定；`FUTURES_SECTOR_MAP` 7 产业链分类）；`data_futures_fundamental.py` → FuturesFundamentalProvider 期货基本面数据（库存/仓单/基差）；`scheduler/` → 调度层（5 个 APScheduler 定时任务：L1:08:30 / L2:23:00 / L3:20:00 / 信号管道:20:30 / 健康检查:每10m）；`scripts/futures_signal_pipeline.py` → 横截面信号管道（方向校正 = 截面 IC 法，因子加权 = Ridge 回归 L2 正则化，Market Regime 检测 = SectorRegimeSelector 产业链级分层判定，按日期定位，`--universe all` 全量商品池，输出品种名称/主力合约 + 产业链 Breakdown + Regime 调整交易建议）；`fts/factor_engine/regime.py` → RegimeAwareSelector 市场制度感知（5 种制度：bull/bear/high_vol/low_vol/oscillate，MA20 斜率 + ATR/价格 + 量比 + 收益自相关）+ SectorRegimeSelector 产业链级制度检测（每个产业链独立构建合成 OHLCV，品种数加权投票计算主制度）；`strategies/strategy_evolution.py` → 策略进化（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion） |
| 可验证断言 | 股票种子池总数 = 482；期货种子池总数 = 81（14 家族）；期货数据层支持 82 个连续合约品种，数据源优先级 3 级（DuckDB → AKShare → 合成）；common_dates 多数对齐（WH0 等停更品种不清空交集）；方向校正按日期定位；信号管道因子加权 = Ridge 回归（全量因子，L2 正则化）；主力合约判定 = contract_kline 最新交易日最大成交量；调度器注册 8 个任务（L1/L2/L3 + 健康检查 + 月度衰减 + 数据质量 + 逻辑监控 + 因子巡检）；信号管道集成 Market Regime 检测（5 种制度分层判定，输出 Regime 调整交易建议）；策略进化模块包含 3 种策略（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion）；股票 L2 启用种子因子相关性预检（≥0.95 标记），期货 L2 跳过；L3 组合支持粘性约束（StickyConfig ±30% / 新因子首日封顶）+ 漂移监控（DriftMonitor → drift_history/YYYY-MM-DD.json）；L2 新晋升因子进影子池（shadow_pool 观察 5 交易日，种子因子 shadow_observe=False 直接进正式组合）；SchedulerEngine 支持 `start_watchdog()` 进程看门狗 |
| 检验方式 | `python -c "from fts.scheduler.tasks import list_tasks; assert len(list_tasks()) == 8"` |
