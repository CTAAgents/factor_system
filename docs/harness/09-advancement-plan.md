# FTS 晋级计划

> 版本: v2.14.0
> 最后更新: 2026-08-06
> 状态: 活跃 — 随项目迭代持续更新

---

## 1. 晋级总览

```
v0.1.0 ───→ v0.2.0 ───→ v0.3.0 ───→ v1.1.0 ───→ v1.2.0 ───→ v1.5.0 ───→ v1.6.0 ───→ v1.7.0 ───→ v1.8.0 ───→ v1.8.1 ───→ v1.9.0 ───→ v1.10.0 ───→ v1.11.0 ───→ v2.0.0 (当前)
    │          │          │          │          │          │          │          │          │          │          │          │          │
    ├ Phase    ├ CLI 真实  ├ Data-Core ├ MCP 迁移  ├ 种子因子  ├ 期货数据  ├ 期货自治循环├ 策略进化  ├ 信号管道v5├ 演化优化  ├ Phase A  ├ Phase B  ├ Phase C
    │ 1-7 完成 │ 调用      │ 集成      │ (akshare) │ 集成      │ 接入      │ L1/L2/L3 调度├ 动态因子权重├ 多空双向排名├ UCT 父因子├ 消融实验  ├ SHAP 分析  ├ 因果结构审查
    ├ 220 测试 ├ Scheduler ├ FDT 清除  ├ 移除期货  ├ 熔断修复  ├ DuckDB +  ├ 期货基本面  ├ 市场制度  ├ 信号增量  ├ 失败模式  ├ 场景测试  ├ 鲁棒性审查  ├ 持续监控仪表盘
    │ 全绿     │ 引擎      │           │ 种子      │           │ AKShare   │ 数据接入    ├ 自适应     ├ 信号快照   ├ 聚类       ├ 风险标签  ├ 34 新测试  ├ 40 新测试
    └ 71% 覆盖 ├ 89% 覆盖  ├ 原子持久化 ├ 1231 测试 ├ 纯多头    ├ 82 期货   ├ 信号管道    ├ 1601 测试 ├ 1700 测试 ├ 1750 测试
               └ 778 全绿  ├ 96% 覆盖  ├ 99% 覆盖  │ 回测      │ 品种      │ 定时任务    └ 全绿      └ 全绿      └ 全绿
                           └ 969 全绿  └ 1231 全绿 ├ 1325 测试 └ 1502 测试 └ 12 家族 50+
                                                     └ 99% 覆盖  └ 99% 覆盖  子因子
```

**详细路线图见 [docs/production_plan.md](../production_plan.md)**，本文档仅记录已完成的里程碑。

---

## 2. 已完成里程碑

### v0.1.0（已完成）

**完成时间**: 2026-07-18

**核心产出**:
- ✅ 从 FDT 剥离为独立项目
- ✅ Phase 1-7 全部完成
- ✅ 因子引擎三层循环（L1/L2/L3）完整实现
- ✅ CLI 入口 + 监控 + 调度框架
- ✅ 220 测试用例全绿
- ✅ 总体覆盖率 71%

### v0.2.0（已完成）

**完成时间**: 2026-07-18

**核心产出**:
- ✅ CLI 引擎命令真实调用实现（199 行，87% 覆盖率）
- ✅ Scheduler 引擎（APScheduler 集成）
- ✅ Config + memory 目录初始化
- ✅ 总体覆盖率 89%（超 80% 目标）
- ✅ evolution_loop 覆盖率 99%
- ✅ macro_evolution 覆盖率 100%
- ✅ micro_evolution 覆盖率 92%
- ✅ llm.py 覆盖率 77%
- ✅ 778 测试全绿
- ✅ 7 个差距项全部关闭

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 总体覆盖率 | 80%+ | **89%** |
| pipeline/strategies 覆盖率 | 80%+ | **100%/100%** |
| micro_evolution 覆盖率 | 70%+ | **92%** |
| CLI/monitor/scheduler 覆盖率 | 60%+ | **87%/100%/100%** |
| 总测试用例数 | 350+ | **778** |


### v0.3.0（已完成）

**完成时间**: 2026-07-19

**核心产出**:
- ✅ Data-Core 集成适配层（FTSDataProvider）
- ✅ FDT 残留依赖清除（grep "futures_data_core" fts/ → 空）
- ✅ 原子持久化（fts/core/atomic.py）
- ✅ 覆盖率 96%（超 90% 目标）
- ✅ data.py: 46% → 100%
- ✅ config/settings.py: 64% → 100%
- ✅ scheduler/engine.py: 22% → 100%
- ✅ meta_loop.py: 84% → 99%
- ✅ 969 测试全绿（超 ~820 目标）

### v1.1.0（已完成）

**完成时间**: 2026-07-24

**核心产出**:
- ✅ MCP 数据源迁移：Data-Core → akshare（腾讯/东方财富 API）
- ✅ 移除 6 个期货专用种子因子
- ✅ CLI 移除 `--universe futures`，默认市场改为 stock
- ✅ 1231 测试全绿，99% 覆盖率

### v1.2.0（已完成）

**完成时间**: 2026-08-02

**核心产出**:
- ✅ 种子因子集成：世坤 101 因子 + Qlib 158 因子加入种子池（总计 268 种子）
- ✅ seed_data 目录统一管理外部因子定义
- ✅ 熔断修复：种子评估不计入熔断计数器，跳过 Verifier
- ✅ 纯多头回测策略 + 组合分析（行业暴露/因子归因/市场环境）
- ✅ 1325 测试全绿，99% 覆盖率
- ✅ GitHub 发布（CTAAgents/factor_system）

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 种子因子数 | 9+ | **268（9 内置 + 259 外部）** |
| 演化通过率 | — | **20 代 56 elite（53 种子 + 3 进化）** |
| 纯多头夏普 | — | **4.07（+135.64% 累计收益，9.45% 最大回撤）** |
| 总测试用例数 | 1325+ | **1325** |
| 总体覆盖率 | 99% | **99%** |

### v1.5.0（已完成）

**完成时间**: 2026-08-03

**核心产出**:
- ✅ 期货数据接入：FuturesDataProvider（DuckDB kline_cache + AKShare 降级）
- ✅ CLI --universe futures 支持期货横截面因子演化
- ✅ 82 个期货品种（25 核心 + 57 全量），覆盖大商所/郑商所/上期所/能源中心/中金所/广期所
- ✅ scripts/download_futures.py 断点续传下载脚本
- ✅ 3 级数据降级（DuckDB → AKShare → 合成数据）
- ✅ 1502 测试全绿，99% 覆盖率

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 期货品种数 | 82 | **82（25 核心 + 57 全量）** |
| 数据降级级数 | 3 | **3 级（DuckDB → AKShare → 合成）** |
| 总测试用例数 | 1500+ | **1502** |
| 总体覆盖率 | 99% | **99%** |

### v2.10.0 算子演化引擎（Phase 3+ / C.4）（已完成）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ 新增 `fts/factor_engine/operator_evolution.py`（`OperatorEvolutionEngine`）：在 DSL 算子空间（58 算子 L0-L5）做适应度导向进化搜索——种群初始化（validator 参数边界 + PIT lookback 校验）→ IC+Sharpe 适应度评估（DSL executor，带表达式缓存）→ 锦标赛选择 → 子树交叉/变异（ExprNode 层面，参数受 param_bounds 约束）→ 精英保留，多代迭代后取最优表达式
- ✅ 产物为 `kind=OPERATOR` 因子：`best_factor_program()` 经 `create_operator_factor` 产出，携带 `expression`/`max_lookback`/`parent_id`/`generation`
- ✅ evolution_loop 集成：`_generate_operator_factor` 优先走 `_try_operator_engine_evolution`（operator/hybrid 模式），无评估数据或引擎失败时回退随机组合生成（原逻辑保留）
- ✅ 关闭 GAP-026：进化搜索直接在 DSL 命名空间进行，无需 GP 算子命名映射（GP 引擎维持 feature_ops 路径，双路径并存）
- ✅ 新增 13 测试用例（引擎 11 + 集成 2：初始化合法性/进化收敛/交叉变异产物校验/OPERATOR 产物/常信号罚分/评估缓存/evolution_loop 调用路径/无数据回退）
- ✅ 设计文档 `docs/harness/design/C.4-operator-evolution-engine-design.md` 落地，全量回归通过（排除既有失败文件）

### v2.11.0 组合漂移治理（已完成 — 当前版本）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ 漂移监控（DriftMonitor）：每次 L3 组合构建后对比上次组合，记录成员重合率（Jaccard）与权重 L1 变化率，持久化到 `memory/portfolio/drift_history/YYYY-MM-DD.json`；`PortfolioManager.save_combo` 自动归档旧组合到 `combo_history/` 供对比；冷启动（无上次组合）L1 变化记为 0
- ✅ 组合粘性约束（_apply_sticky_constraints）：build_combo 权重归一化前施加 — 存量因子权重相对上次组合变动 clamp 在 ±30%（`StickyConfig.max_delta`），新因子首日权重封顶（`new_factor_cap` 默认 0.10）；`PortfolioLoop` 默认启用（`DEFAULT_STICKY_CONFIG`），可显式传 `sticky_config` 覆盖
- ✅ L2 影子池（shadow_pool）：新晋升因子写入 `shadow_pool` 标记（promoted_at/observe_trading_days=5/observe_until，跳过周末），L3 `load_elite_factors` 过滤观察期内因子；种子因子 `shadow_observe=False` 直接进正式组合；DuckDB metadata + JSON 双存储
- ✅ 新增 20 测试用例（粘性约束 7 + 漂移监控 7 + 影子池 6），82 个 portfolio_loop 测试全绿；evolution_loop 既有环境性失败（6 个）经 git stash 验证与本改动无关

### v2.9.0 Design 全量落地（已完成 — 当前版本）

**完成时间**: 2026-08-06

**核心产出**（docs/harness/design/ 9 个设计全部实现）:
- ✅ S1 数据层（A.1/A.2/B.3）：`factor_quality_scores`/`factor_status_history`/`factor_audit_reports` 三表 + FactorQualityScoreRepository/FactorStatusRepository/FactorAuditReportRepository 3 仓储类 + factor_catalog 生命周期字段扩展（幂等，规避 DuckDB 1.1.x ART 索引 UPDATE bug）
- ✅ S2 监控调度（A.2/A.3/B.1）：prometheus_metrics 衰减/Regime/权重/质量指标注册表并挂载 /metrics；adaptive_weight 封装 AdaptiveWeightManager+RegimeSmoother（热更新）；data_quality_monitor 完整性/准确性/及时性三维指标函数；scheduler 新增 monthly_decay_eval（每月 1 日）与 data_quality_eval（每 5 分钟）任务
- ✅ S3 回测流水线（B.2）：7 阶段类（FactorScreener/SignalGenerator/PortfolioConstructor/CostSimulator/RiskAttributor/ReportGenerator/CapitalAllocator）+ BacktestPipeline.run_batch 批量排名 + BacktestPipelineBuilder + CLI `fts backtest run/batch/compare`
- ✅ S4 C.1 CLI：`fts feature list`（50 算子/7 类）+ `fts feature analyze`（置换重要性）+ `fts gp evolve`（GP 演化）
- ✅ S5 C.2 实盘对接：signal_contract（FactorSignal/SignalValidator 契约）+ fts/risk 风控包（RiskManager 五项规则/TradeAdapter 抽象/SimulatedTradeAdapter 模拟成交）+ LiveFactorMonitor（30% 偏离阈值）+ HTTP 端点（signal submit/risk status/live factors）+ Prometheus live/risk 指标
- ✅ S6 C.3 反馈闭环：FeedbackLoop 家族（Trigger/AttributionAnalyzer/DirectionAdjuster/Effectiveness）+ 4 张反馈表（feedback_events/attribution_reports/feedback_processing_results/feedback_reports）+ CLI `fts feedback trigger/process/report/stats` + Prometheus 反馈指标
- ✅ 新增 79 测试用例（S1 11 + S2 19 + S3 27 + S4 5 + S5 27 + S6 20 去重后 79），本次相关用例全绿

**说明**: 9 个设计文档状态已同步为「已实现」（B.2/C.1/C.2/C.3 标注 v2.9.0），实现方向与文档细节差异已在各文档「实现现状」标注。

### 算子演化基础层（Phase C.2）（已完成）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ FTS-Expr DSL 落地：`fts/factor_engine/expr_dsl/` 包（parser → validator → executor/compiler → runtime）；递归下降解析器 + AST，解释执行器复用 `feature_ops.py` 既有算子（pandas 向量化快速路径），编译器生成确定性沙箱代码
- ✅ 算子注册表：FTS-Expr 算子注册表元数据（语义/梯度/边界）+ L0-L5 分层
- ✅ FactorProgram kind 扩展：FactorKind 枚举（`operator`/`code`/`hybrid`）+ 可选字段（`expression`/`operator_depth`/`operator_count`/`max_lookback`）；向后兼容，存量因子经 `normalize_factor_program` 默认 `code`，对上层零破坏
- ✅ FactorExecutor 按 kind 分派：`operator` 走 DSL 解释执行快速路径（异常回退沙箱），`code` 走现有沙箱路径；评估链/Verifier 接口不变
- ✅ evolution_mode 配置：`settings.py` + `config/settings.yaml` 新增 `evolution_mode`（`operator`/`code`/`hybrid`），支持 `FTS_EVOLUTION_MODE` 环境变量
- ✅ 新增 expr_dsl 六个测试文件 + test_contracts_kind / test_executor_dispatch / test_config_settings 等用例，全量回归中本次相关用例全绿

**说明**: 本里程碑为后续「算子演化引擎」计划的前置基础层——算子因子与代码因子统一表现为 `FactorProgram`，对上层（持久化/评估链/Verifier）透明。

### v2.8.5（已完成 — 当前版本）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ P0/P1 演化质量修复：快速预筛选层（Step 1.4，nunique>10/IC>0.02/std>1e-6 过滤常数信号和伪相关）
- ✅ 种子因子晋升修复（重复判断 + EliteFactorTracker 初始化）
- ✅ 精英因子重评估保护（跳过不存在的跟踪记录）
- ✅ 期货质量评分卡差异化配置（get_futures_config，IC/Sharpe/换手率阈值下调适应日频期货）
- ✅ LLM Prompt 增强（添加质量约束、OOS 一致性、因果链要求）
- ✅ 多父代交叉策略（GP 演化 3-parent crossover，锦标赛选择 n 父代，30% 概率）
- ✅ FTS-Expr DSL OPERATOR 演化模式集成（_generate_operator_factor 方法）
- ✅ OOS 审计误判修复（ICIR 一致性计算替代 oos_ratio）
- ✅ 新增 38+ 测试用例，全量回归测试通过

### v2.8.1（已完成）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ 孤立模块集成修正：按真实 API 修正 EvolutionLoop 中 6 处集成调用点
- ✅ 4 个审查门禁 passed 判定落地（消融伪相关 / 因果事件敏感 / 鲁棒性通过率 / SHAP 信息型）
- ✅ 特征重要性分析接入 GP 管线；LogicMonitor 接入精英因子定期重评估
- ✅ 新增门禁判定测试与端到端 mock helper，109 测试全绿

### v2.5.0（已完成 — 当前版本）

**完成时间**: 2026-08-05

**核心产出**:
- ✅ Phase 1 种子因子 YAML 化：19 个 YAML 文件管理 563 种子因子，支持版本化维护
- ✅ Phase 2 精英因子 DuckDB 迁移：680 精英因子从 JSON 迁移到 DuckDB，4 张表（factor_metadata/factor_versions/factor_correlations/factor_evaluations）
- ✅ 因子仓库层（FactorRepository）：完整 CRUD、版本管理、相关性存储、搜索过滤
- ✅ 因子相关性矩阵：100 因子 × 4950 对相关性记录（Pearson + Spearman），支持组合去冗余
- ✅ 元数据自动更新：因子自动关联最大相关系数和高相关因子列表
- ✅ 回测引擎兼容性验证：680 因子加载/执行/筛选/搜索全部通过
- ✅ 新增 54 个测试用例，155+ 测试全绿

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 种子因子 YAML 文件数 | 19 | **19** |
| 精英因子总数 | 680 | **680** |
| 相关性记录数 | 4950 | **4950** |
| 新增测试用例数 | 50+ | **54** |
| 回测引擎因子加载率 | 100% | **100%（680/680）** |

### v2.4.0（已完成）

**完成时间**: 2026-08-05

**核心产出**:
- ✅ 默认市场改为期货：settings.yaml/settings.py/meta_loop.py/cli.py 四层同步 default_market="futures"
- ✅ L1 期货知识注入新增全链路日志（初始化/种子池/Step1-4/验证/持久化/完成）
- ✅ 118 测试全绿

### v1.11.0（已完成）

**完成时间**: 2026-08-04

**核心产出**:
- ✅ SHAP 局部可解释性分析：新增 `shap_analyzer.py`，使用 KernelExplainer 对极端预测样本进行特征归因，输出 top-5 贡献特征，JSON 报告持久化
- ✅ 鲁棒性审查模块：新增 `robustness.py`，覆盖对抗样本测试（4 种扰动因子）、缺失值测试（5%/10%/20%）、分布外测试（高波动/低波动/强趋势/高噪声 4 场景）
- ✅ 新增 34 个测试用例（test_shap_analyzer.py 14 + test_robustness.py 20），1750+ 测试全绿
- ✅ robustness.py 100% 覆盖率
- ✅ pyproject.toml 新增 shap 依赖
- ✅ 架构/测试/运营文档同步更新

### v1.10.0（已完成）

**完成时间**: 2026-08-04

**核心产出**:
- ✅ 输入敏感性消融实验：新增 `ablation.py`，5 种消融模式（成交量置零、VWAP 替换 close/settle、时间戳打乱、单特征归零）
- ✅ 宏观行为场景测试：23 个典型市场场景（趋势/反转/流动性/事件/震荡/期货），场景验证器可独立运行
- ✅ 风险标签闭环验证：vwap_approx 因子 IC 阈值 0.08 晋升过滤
- ✅ 新增 20 个场景测试 + 10 个风险标签测试 + 20 个消融实验测试

### v1.9.0（已完成）

**完成时间**: 2026-08-03

**核心产出**:
- ✅ UCT 父因子选择：父因子选择从轮询改为 UCT（Upper Confidence Bound for Trees）树搜索，智能探索-利用平衡
- ✅ 失败模式聚类分析：宏观演化引入失败模式聚类分析，聚类结果注入 LLM prompt 提升演化质量
- ✅ 新增 32 个测试用例（test_uct_selection.py 10 + test_failure_pattern.py 22），全绿

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 新增测试用例数 | 32 | **32（UCT 10 + 失败模式 22）** |
| 总测试用例数 | 1633 | **1633** |
| 总体覆盖率 | 92% | **92%** |

### v1.7.0（已完成）

**完成时间**: 2026-08-03

**核心产出**:
- ✅ 动态因子权重（DynamicWeightStrategy）：基于因子历史表现（IC 代理）自动调整权重
- ✅ 市场制度自适应（RegimeAdaptiveStrategy）：识别 bull/bear/oscillate/high_vol/low_vol 市场状态，动态切换因子权重配置
- ✅ 多周期信号融合（MultiPeriodSignalFusion）：融合短/中/长周期信号，支持方向一致性检查
- ✅ 55 个测试用例全绿，strategy_evolution.py 95% 覆盖率

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 策略进化类型 | 3 | **3（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion）** |
| 测试用例数 | 55 | **55** |
| strategy_evolution.py 覆盖率 | 90%+ | **95%** |
| 总测试用例数 | 1557 | **1557** |

### v1.6.0（已完成）

**完成时间**: 2026-08-03

**核心产出**:
- ✅ 期货全量种子因子库：12 大因子家族 50+ 子因子（seed_data_futures_full.py）
- ✅ 期货因子演化脚本（scripts/run_futures_evolution.py）
- ✅ 期货信号管道（scripts/futures_signal_pipeline.py）— 只接入 IC>0.3 顶级因子
- ✅ 期货组合策略（scripts/futures_strategy.py）— 三种加权合成模式
- ✅ 期货 L3 组合构建（scripts/futures_l3_portfolio.py）— 正交化 + 组合优化
- ✅ L1/L2/L3 全自动调度（APScheduler 5 个定时任务）
- ✅ 期货基本面数据接入（库存/仓单/基差 — AKShare）
- ✅ 信号报告输出到 reports/{date}/
- ✅ 因子方向自动校正（截面 IC 法）
- ✅ 用户手册更新 + 所有 Harness 文档同步

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 期货种子因子数 | 50+ | **12 大因子家族 50+ 子因子** |
| 定时任务数 | 5 | **5（L1:08:30 / L2:23:00 / L3:20:00 / 信号管道:20:30 / 健康检查:每10m）** |
| 总测试用例数 | 1502 | **1502** |
| 总体覆盖率 | 99% | **99%** |

---

## 3. 下阶段目标（v2.0.0）

参见 [docs/production_plan.md](../production_plan.md) 完整路线图。

| 版本 | 主题 | 核心产出 |
|:-----|:-----|:---------|
| **算子演化引擎（Phase 3+ / C.4）** | ✅ 已实现（v2.10.0） | 基于 Phase C.2 基础层（FTS-Expr DSL / 算子注册表 / kind 分派 / evolution_mode），实现算子级因子创新与演化（`OperatorEvolutionEngine`，见第 2 节 v2.10.0 里程碑） |
| **v2.0.0** | 生产部署 | 监控告警完善、容器化、CI/CD 流水线、期货全链路 E2E 测试 |

---

## 4. 版本历史

| 版本 | 日期 | 说明 |
|:-----|:-----|:-----|
| **v2.14.0** | 2026-08-06 | GAP-030 测试隔离根治：EvolutionLoop `factor_db_path` 注入点 + run() 测试隔离 DuckDB + catalog 重复 seed 清理 |
| **v2.13.0** | 2026-08-06 | GAP-032 L2 晋升产物双写一致性：`_write_to_duckdb` 返回 bool + `_promote_to_elite` 严格一致（DuckDB 失败回滚 JSON 快照，晋升失败）；数据修复补入 fut_mobile_big_data_g5 + 归档 515 个同名重复快照 |
| **v2.12.1** | 2026-08-06 | session_id 全链路补齐：`generate_session_id()` + CLI 入口生成并挂载 `args.session_id` + 子命令日志聚合；02-lifecycle 校正 trace_id/run_id 格式描述；新增 3 测试用例 |
| **v2.12.0** | 2026-08-06 | GAP-031 L1→L2 数据流打通：EvolutionLoop 启动合并 L1 注入候选（pending 门控 + market 过滤 + 去重 + 幂等）；L1 注入写入 market 标记；SeedCandidate 契约扩展；新增 8 测试用例 |
| **v2.10.0** | 2026-08-06 | 算子演化引擎（Phase 3+ / C.4）：`OperatorEvolutionEngine` DSL 算子空间进化搜索（种群初始化/IC+Sharpe 评估/锦标赛选择/交叉变异/精英保留）+ evolution_loop 集成 + 关闭 GAP-026；新增 13 测试用例 |
| **v2.9.0** | 2026-08-06 | Design 全量落地（docs/harness/design 9 设计全部完成）：S1 数据层三表+3 仓储类；S2 监控调度（Prometheus 指标注册表 + 自适应权重 + 数据质量三维指标 + monthly_decay_eval/data_quality_eval 任务）；S3 回测流水线（7 阶段类 + run_batch + Builder + CLI）；S4 C.1 CLI（feature list/analyze + gp evolve）；S5 C.2 实盘对接（信号契约 + fts/risk 风控包 + LiveFactorMonitor + HTTP 端点 + live/risk 指标）；S6 C.3 反馈闭环（FeedbackLoop 家族 + 4 反馈表 + CLI + 反馈指标）；新增 79 测试用例 |
| **v2.8.5** | 2026-08-06 | P0/P1 演化质量修复 + OPERATOR 演化模式集成：快速预筛选层、种子因子晋升修复、精英因子重评估保护、期货质量评分卡差异化配置、LLM Prompt 增强、多父代交叉策略、FTS-Expr DSL OPERATOR 演化模式集成、OOS 审计误判修复；新增 38+ 测试用例 |
| **v2.5.0** | 2026-08-05 | Phase 1 种子因子 YAML 化 + Phase 2 精英因子 DuckDB 迁移：种子因子 YAML 文件（563 因子）；精英因子 DuckDB（680 因子，4 张表）；因子仓库层 FactorRepository；因子相关性矩阵（4950 对）；54 新测试 |
| **v2.4.0** | 2026-08-05 | 默认市场改为期货：四层同步 default_market="futures"；L1 期货知识注入全链路日志；118 测试全绿 |
| **v2.0.0** | 2026-08-04 | Phase C 逻辑审查 — 因果结构审查 + 持续监控仪表盘 |
| **v1.11.0** | 2026-08-04 | Phase B 逻辑审查 — SHAP 分析 + 鲁棒性审查 |
| **v1.10.0** | 2026-08-04 | Phase A 逻辑审查 — 消融实验 + 场景测试 + 风险标签 |
| **v1.9.0** | 2026-08-03 | Phase A 演化优化 — UCT 父因子选择 + 失败模式聚类 |失败模式聚类：父因子选择从轮询改为 UCT 树搜索，宏观演化引入失败模式聚类分析注入 LLM prompt，新增 32 个测试用例（test_uct_selection.py 10 + test_failure_pattern.py 22） |
| **v1.8.1** | 2026-08-03 | Market Regime 集成到信号管道：新增 `_build_composite_ohlcv()` 从品种面板构建市场综合 OHLCV，管道调用 `RegimeAwareSelector.detect()` 检测当前市场制度（5 种：bull/bear/high_vol/low_vol/oscillate），控制台输出制度名称+置信度+特征值，报告新增「市场制度」章节含 Regime 调整后的交易建议（趋势友好→放大仓位、震荡→反向操作、高波动→缩小仓位+增量绝对值>0.15）；版本号 1.8.0→1.8.1 |
| **v1.8.0** | 2026-08-03 | 信号管道 v5 多空双向 + 信号增量：管道升级为多空双向排名（按信号强度绝对值排序），新增信号增量追踪（较昨日变化判断趋势加速/衰竭），信号快照 JSON 持久化 + JSONL 历史追加，L3 Portfolio Loop 自动触发信号管道（全量 82 品种），README 拆分股票/期货种子因子；版本号 1.7.3→1.8.0 |
| **v0.1.0** | 2026-07-18 | 从 FDT 剥离，Phase 1-7 完成，220 测试全绿 |
| **v0.2.0** | 2026-07-18 | CLI 引擎真实调用、Scheduler 引擎、89% 覆盖率、778 测试全绿、7 项差距全部关闭 |
| **v0.3.0** | 2026-07-19 | Data-Core 集成适配层、FDT 残留清除、原子持久化、96% 覆盖率、969 测试全绿 |
| **v1.1.0** | 2026-07-24 | MCP 数据源迁移（akshare 腾讯/东方财富）、移除期货种子、1231 测试全绿、99% 覆盖率 |
| **v1.2.0** | 2026-08-02 | 种子因子集成（世坤101+Qlib158，268 种子）、熔断修复、纯多头回测、1325 测试全绿、GitHub 发布 |
| **v1.3.0** | 2026-08-03 | 国泰君安 191 因子加入种子池（459 种子）；工程测试全覆盖：1431 测试全绿，46/47 模块 100% 覆盖率 |
| **v1.4.0** | 2026-08-03 | 基本面/另类/宏观因子加入种子池（482 种子）；新增 23 个基本面种子因子 + FundamentalProvider 数据层；1435 测试全绿 |
| **v1.5.0** | 2026-08-03 | 期货数据接入：FuturesDataProvider（DuckDB kline_cache + AKShare 降级）、CLI --universe futures、82 个期货品种横截面因子演化、scripts/download_futures.py 断点续传下载脚本 |
| **v1.7.0** | 2026-08-03 | 策略进化：动态因子权重（DynamicWeightStrategy）+ 市场制度自适应（RegimeAdaptiveStrategy）+ 多周期信号融合（MultiPeriodSignalFusion）+ 55 测试用例全绿，strategy_evolution.py 95% 覆盖率 |
| **v1.6.0** | 2026-08-03 | 期货自治循环：L1/L2/L3 全自动调度（APScheduler 5 个定时任务）+ 期货全量种子因子库（12 大因子家族 50+ 子因子）+ 期货因子演化 + 信号管道（IC>0.3 顶级因子）+ 组合策略 + L3 组合构建 + 期货基本面数据接入 + 信号报告输出到 reports/{date}/ |
| **v1.3.1** | 2026-08-03 | 代码审核提升：重构 `parse_program_md` 为数据驱动（76→48 行），提取 `_evaluate_cross_section`，拆分 Eager Test；1432 测试全绿，99% 覆盖率，46/47 模块 100% 覆盖率 |
| **v1.3.2** | 2026-08-03 | 代码审核提升：消除 `_evaluate_and_promote_seeds` 重复横截面逻辑，提取 3 个公共 Mock fixture（`mock_trial`/`mock_optuna_study`/`mock_evolve_micro`）；1432 测试全绿，99% 覆盖率，47/47 模块 100% 覆盖率 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件定义 FTS 版本路线图（v2.10.0 算子演化引擎 + v2.8.5 演化质量修复 + OPERATOR 演化模式基础层），里程碑记录引用 `docs/harness/07-operations.md` 版本历史 |
| 可验证断言 | 当前版本 v2.10.0 里程碑已登记，v2.0.0 按路线图推进 |
| 检验方式 | 检查本文件下阶段目标表和版本历史确认当前版本和路线图 |
