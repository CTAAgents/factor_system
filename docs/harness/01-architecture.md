# FTS 系统架构文档

> 版本: v2.86.0
> 最后更新: 2026-08-10

---

## 1. 项目概述

FTS（Factor Intelligence System，因子智能系统）是一个独立的因子策略系统，专注于因子推演、策略组建与交易信号产出。数据层基于腾讯自选股 MCP (akshare) 提供 A 股/ETF/期货行情数据，FTS **本身包含自洽的数据源适配层**，无外部数据项目依赖。

### 项目边界

| 职责 | 归属 |
|:-----|:-----|
| 行情数据获取（A 股/ETF OHLCV） | **FTS（通过 MCP/akshare 接入腾讯/东方财富 API）** |
| 行情数据获取（期货 OHLCV） | **FTS（通过 DuckDB kline_cache/分钟 + AKShare futures_zh_daily_sina + 通达信/TQSDK 分钟数据）** |
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
│  - validate_batch_candidates（批量候选契约校验，GAP-I101）              │
│  - MetaRunResult.candidates_per_minute（L1 吞吐指标，GAP-I101）         │
│  - extractors/ 多路知识源管道（GAP-I103，v2.82.0）                      │
│    ├ 研报/论文/天软/YAML 三源 + 另类源：                               │
│    ├ AnnouncementNewsExtractor（公告/舆情，股票管道）                  │
│    └ MacroEventExtractor（宏观事件，股票+期货管道）                    │
│    多源并行收集（BaseExtractorPipeline.extract ThreadPoolExecutor）    │
│  - FactorReviewWorkflow 人审驳回 → ExperienceChain（GAP-I102 二期）     │
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
│  │   股票横截面模式 (cross_section_data + industry_map/cap_map):        │     │
│  │   cross_section_evaluate_backtest 支持行业/市值中性化               │     │
│  │   (v2.57.0): _neutralize_signal_matrix 行业去均值 + 市值加权去均值 │     │
│  │   (v2.61.0, GAP-S01): EvolutionLoop(market="stock") 自动加载       │     │
│  │   industry_map.json + cap_map（stock_neutralization=true，          │     │
│  │   键归一化：后缀 .SH/.SZ → 裸代码兼容面板 symbol）                 │     │
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
│  │                      ├─ 深度演化 (GAP-I203, v2.73.0: GRU)         │     │
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
│  batch_mining.py — 批量挖掘漏斗 (GAP-I201, v2.65.0, evolution_mode=batch):  │
│    BatchMiner: 批量生成(同父多后代, macro 至多 1 次 + GP/deep/operator 三方法 │
│    轮换, GAP-I203 v2.73.0 deep 并入 idx%3==2) →                           │
│    ExecutorBackend 可插拔粗筛 (thread 默认/process/dask/ray, GAP-I502) →  │
│    按预筛 IC 排序截断 → _process_candidate 准入链
│  预筛与通道修复 (v2.66.0, GAP-X01/X02/X03):                               │
│    - _quick_prefilter 横截面模式走 _cross_section_prefilter: 全面板信号矩阵 │
│      vs 截面 forward 收益 (与 cross_section_evaluate_backtest 同口径)      │
│    - operator 生成常数校验前移: 生成循环内 evaluate 过滤非常数表达式       │
│    - _execute_factor_code exec 后 exec_globals.update(local_vars), 修复    │
│      eval_fts_expr 未定义 (模块级 import 绑定并入 factor_program.__globals__)
│  seed_pool.py — 双种子池管理 + compute_seed_correlations() 时序相关性预检 │
│  factor_program.py — 因子程序（图灵完备代码 + 安全沙箱）                  │
│  verifier.py — Verifier 锁定协议                                       │
│  state.py — 演化状态管理 + trace_id 全链路                              │
│  gp_evolver.py — GP 遗传规划搜索引擎 (Phase C.1)                        │
│    v2.66.0 (GAP-X03): 模板 ts_product 改用 rolling.apply(np.prod)       │
│    (pandas≥2.1 移除 Rolling.prod); _evaluate_fitness 后处理对齐流水线    │
│    (nan_to_num + clip[-10,10] + std<1e-12 常数罚分), 产物与运行时校验对齐│
│  ml/deep_factor.py — 深度因子生成器 (GAP-I203, v2.73.0):                │
│    DeepFactorGenerator: OHLCV 特征(日收益率+量变化率) → 滚动窗口样本 →   │
│    前 train_ratio 训练 GRUFactorModel (纯 numpy BPTT) → 权重序列化内嵌   │
│    def factor_program code (零未来函数: 窗口 [t-lookback+1,t] 逐 t 推理 + │
│    tanh 压缩; 样本不足/训练失败返回 None 降级); evolution_loop._run_deep_evolution│
│    接入 L2, 产物过全套审计链                                            │
│  expr_dsl/ — FTS-Expr 算子表达式语言 (Phase C.2)                        │
│    registry.py — 算子注册表 (L0-L5 分层, 参数边界, 经济语义;            │
│      GAP-S10: verify_registry_consistency 双注册表一致性;               │
│      GAP-S12: A_SHARE_FIELDS 10 A股特有字段 + L5b 4 领域算子;           │
│      GAP-I202: ts_slope/ts_quantile 时序组合算子 + required_shared      │
│      硬约束——8 组合/跨标的算子必须 feature_ops.OperatorRegistry 共享)   │
│    parser.py — 递归下降解析器 → AST                                    │
│    validator.py — 静态校验 (参数边界, 最大 lookback, PIT)               │
│    executor.py — 解释执行器 (pandas 向量化快速路径)                      │
│    compiler.py — 编译器 (表达式 → 确定性沙箱代码)                       │
│    factory.py — 算子因子工厂 (FTS-Expr → FactorProgram)                │
│    seed_analyzer.py — 种子表达式静态 PIT 审计 (GAP-S09:                │
│      WQ 风格表达式递归下降解析 + 静态 lookback/fields/operators)        │
│                                                                         │
│  GP 演化支持多父代交叉策略:                                              │
│  - 标准双亲交叉 (70% 概率)                                              │
│  - 多父代交叉 (30% 概率, 3 父代融合)                                     │
│  - 锦标赛选择 n 个父代 (_tournament_select_n)                          │
│  - 多父代交叉提升种群多样性, 避免局部最优                                │
│                                                                         │
│  职责: 夜间批量演化 → 父因子选择 → 演化模式分派                       │
│        (code/hybrid/operator/operator_first/batch; 股票默认 operator_first  │
│         GAP-S11: 算子优先 → LLM → GP 逐级兜底; 方法分布记账)           │
│        → optuna 参数优化 → 评估 → 审计 → 高IC筛查(B.4) → 4 重审查门禁 → 家族多样性约束(max_per_family=3) → elite 因子 →       │
│        传递相关性预检结果给 L3                                           │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │ elite 因子
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  L3 Portfolio Loop (组合循环 — 组合构建与信号产出层)                     │
│                                                                         │
│  portfolio_loop.py                                                     │
│  - PortfolioManager（组合管理器，含 combo_history 归档）                │
│  - factor_clustering.py（P1 因子聚类模块）                              │
│    - FactorClusteringEngine（信号相关性层次聚类 + 代表因子选择）         │
│    - PCASignalCompressor（PCA 信号降维压缩）                            │
│  - orthogonalize_factors（因子正交化）                                  │
│  - decay_test（衰减检验）                                              │
│  - build_combo（构建组合，支持粘性约束）                                │
│  - synthesize_signals（信号合成，支持五种模式：equal_weight/sharpe_weight/elastic_net/ml_ensemble/adaptive）│
│  - ACTIVE_FACTOR_CAP=20（活跃因子数量上限，超出时按 Sharpe 排名保留 Top N）│
│  - generate_agent_proposals（Agent 提案生成）                          │
│  - load_elite_factors（加载 elite 因子，过滤影子池观察期因子）          │
│  - L3Verifier（L3 锁定协议）                                           │
│  - DriftMonitor（组合漂移监控：成员重合率 + 权重 L1 变化率）            │
│  - _apply_sticky_constraints（粘性约束：±30% 变动 / 新因子首日封顶）    │
│  - adaptive_weight.py（自适应权重 v2.56.0 接入）                        │
│    - AdaptiveWeightConfig（维度 family/style/both + smoother 参数）     │
│    - RegimeSmoother（Regime 切换权重指数平滑，alpha=0.5, min_days=2）   │
│    - REGIME_STYLE_MULTIPLIERS（Regime × FactorStyle 倍率表，style 维度）│
│  - FactorStyle 枚举 + style_tags 字段（contracts.py / factor_catalog） │
│                                                                         │
│  P1 因子聚类流程:                                                       │
│    Step 1.8: FactorClusteringEngine.run()                               │
│      → 使用 FactorExecutor 在参考品种上计算每个因子的信号序列           │
│      → 计算 Pearson 相关系数矩阵                                        │
│      → 层次聚类（average linkage，距离阈值默认 0.7）                    │
│      → 从每个簇中选择 Sharpe 最高的代表因子                             │
│                                                                         │
│  P2 PCA 降维流程:                                                       │
│    Step 1.9: PCASignalCompressor.run() (可选，通过 enable_pca 控制)      │
│      → 构建因子信号矩阵 (n_dates × n_factors)                          │
│      → 标准化 (z-score)                                                 │
│      → PCA 拟合，保留解释 95% 方差的主成分（最多 10 个）                │
│      → 通过载荷矩阵将主成分映射回因子权重                               │
│                                                                         │
│  职责: 组合构建 → 因子聚类(P1) → PCA压缩(P2) → 信号合成 → 衰减检验 → 粘性约束 → 漂移监控 │
└─────────────────────────────────────────────────────────────────────────┘
```

### 层间交互

- **L0 → L1**: Program.md 设定 L1 的搜索空间、预算、市场偏好
- **L1 → L2**: 注入种子因子 + 演化方向指引（通过 seed_pool.inject()）
  - 股票 L2: 482 股票因子种子池（时序模式）
  - 期货 L2: 184 期货因子种子池（17 家族，横截面模式）
  - 种子晋升 elite 质检链与演化因子完全对齐（v2.50.0）：Verifier 判定 + 质量评分卡 + 端到端回测 + 数据质量监控 + FactorAuditor 6 项强制审计 + 消融实验 + 因果结构审查 + 鲁棒性审查 + SHAP 可解释性分析，任一关卡失败即拒绝晋升（种子 L1 注入候选与人工精选种子一视同仁）
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
│   ├── tq_source.py            # 通达信 TQ-Local 数据源（支持分钟级）
│   ├── tqsdk_source.py         # 天勤 TQSDK 数据源（分钟/日线）
│   ├── tdx_minute_source.py    # 通达信 TDX HTTP 分钟数据源（端口 17709）
│   ├── macro_aligner.py        # 宏观字段增强层（EDB 时序对齐 + 注入，v2.32.0）
│   └── migrate.py              # 数据迁移工具
├── factor_engine/              # 因子引擎（核心模块）
│   ├── __init__.py             # 模块入口 + 版本号 v1.1.0
│   ├── contracts.py            # 完整 TypedDict 契约（L1+L2+L3）
│   ├── evolution_loop.py       # L2 主循环（股票时序/期货横截面双模式）
│   ├── orthogonal_basis.py     # 多因子正交基底（GAP-I206 补充, v2.72.1）：Gram-Schmidt 迭代残差化 + 基底注册/持久化
│   ├── batch_mining.py         # 批量挖掘漏斗（GAP-I201, v2.65.0）：BatchMiner 批量生成 + 并行粗筛 + 排序截断，evolution_mode="batch" 时每代批量候选
│   ├── executor_backend.py     # 可插拔执行器抽象（GAP-I502, v2.83.0）：ExecutorBackend（thread/process/dask/ray，分布式扩展预留）+ create_executor_backend 工厂，BatchMiner.filter_batch 接入
│   ├── microstructure_factors.py # Level2 订单流因子（GAP-I503 首期, v2.84.0）：OFI 订单流不平衡/OBI 盘口不平衡/大单占比 + compute_microstructure_factors 统一入口（FACTOR_COLUMNS 契约）
│   ├── meta_loop.py            # L1 元循环
│   ├── portfolio_loop.py       # L3 组合循环
│   ├── macro_evolution.py      # LLM 宏观演化
│   ├── micro_evolution.py      # optuna 微观调参
│   ├── evaluation_chain.py     # 三级评估链
│   ├── experience_chain.py     # 经验链存储
│   ├── ml/deep_factor.py       # 深度因子生成器（GAP-I203, v2.73.0）：GRU 训练 → 权重内嵌零未来函数 code
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
│   ├── ablation.py             # 输入敏感性消融实验（Phase A 逻辑审查；v2.50.0 判定语义：shuffle_dates/成交量/VWAP 消融与核心价格列置零为信息型不拦截，仅非价格列置零 IC 降幅>50% 判伪相关）
│   ├── shap_analyzer.py        # SHAP 局部可解释性分析（Phase B 逻辑审查）
│   ├── robustness.py           # 鲁棒性审查（Phase B 逻辑审查）
│   ├── causal_validator.py     # 因果结构审查（Phase C 逻辑审查）
│   ├── audit.py                # 因子审计（FactorAuditor + FailureClassifier 集成）
│   ├── high_ic_screener.py     # 高IC筛查剔除（B.4）：16项检查×6模块，5项一票否决，A/B/C/PASS评级
│   ├── failure_classifier.py   # 失败模式分类器（10 种失败模式 + 改善建议）
│   ├── factor_lineage.py       # 因子血缘追踪（谱系/趋势/退化检测/批量审计）
│   ├── factor_inspector.py     # 定时巡检（自动检测退化因子并降级）+ FactorReviewWorkflow 审查工作流（GAP-I102：pending→approved/rejected 状态机 + factor_reviews 表意见回写 + CLI factor review 队列）
│   ├── monitor.py              # 循环监控
│   ├── factor_quality_card.py  # 因子质量评分卡（10 维评分，A/B/C 分级准入）
│   ├── adaptive_weight.py      # 自适应权重（AdaptiveWeightManager + RegimeSmoother 热更新）
│   ├── feature_ops.py          # 特征算子注册表（50 算子 / 7 类）
│   ├── feature_importance.py   # 特征重要性分析（置换重要性）
│   ├── gp_evolver.py           # GP 演化器（ExpressionTree + 交叉/变异 + multi_objective 适应度 + Pareto 前沿输出，v2.78.0）
│   ├── pareto.py               # Pareto 多目标前沿（NSGA-II 快速非支配排序，GAP-I204 二期 v2.78.0）
│   ├── symbolic_regression.py  # 符号回归补充搜索（确定性 beam-search，GAP-I204 二期 v2.78.0）
│   ├── operator_evolution.py   # 算子演化引擎 (Phase 3+ / C.4)：DSL 算子空间进化式搜索
│   ├── backtest_pipeline.py    # 回测流水线（B.2）：run_batch 批量对比 + Builder
│   ├── factor_screener.py      # 回测阶段 1：因子筛选
│   ├── signal_generator.py     # 回测阶段 2：时序/横截面信号生成
│   ├── portfolio_constructor.py# 回测阶段 3：等权/Sharpe/自适应组合构建
│   ├── portfolio_optimizer.py  # 组合优化器（GAP-F07，v2.60.0）：均值方差/风险平价 + 换手/集中度/杠杆/VaR 约束 + scipy 降级
│   ├── portfolio_optimizer.py  # 组合优化器（GAP-F07，v2.60.0）：均值方差/风险平价 + 换手/集中度/杠杆/VaR 约束 + scipy 降级
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
│   ├── data_level_monitor.py   # 数据级质量监控（GAP-F06，v2.60.0）：缺失率/异常值/复权一致性/多源分歧
│   ├── data_level_monitor.py   # 数据级质量监控（GAP-F06，v2.60.0）：缺失率/异常值/复权一致性/多源分歧
│   ├── live_factor_monitor.py  # Live 因子偏离监控（C.2，v2.77.0）：30% 偏离阈值 + ingest_live_ic 实盘反馈数据源接入 + 衰减告警（GAP-I402）
│   ├── logic_monitor.py        # 逻辑监控仪表盘（Phase C）
│   ├── k8s_deploy.py          # K8s 部署配置
│   └── prometheus_setup.py     # Prometheus 指标配置
├── ml/                         # ML 模型层（v2.38.0）
│   ├── __init__.py             # 包入口
│   ├── models.py               # ML 模型封装（LightGBM/XGBoost/Ensemble，可选依赖）
│   └── trainer.py              # 训练管线（横截面回归/时序预测/集成融合三种模式）
├── bridge/                     # 信号桥接层（v2.38.0）
│   ├── __init__.py             # 包入口
│   └── signal_bridge.py        # SignalBridge 信号格式转换（JSON/Redis/REST 协议）
├── risk/                       # 风控层（C.2）
│   ├── __init__.py             # 导出 RiskManager/TradeAdapter/SimulatedTradeAdapter
│   ├── risk_manager.py         # RiskManager 五项风控规则（仓位/回撤/亏损/杠杆/集中度）
│   ├── trade_adapter.py        # TradeAdapter 抽象基类（Liskov 替换）
│   └── simulated_adapter.py    # SimulatedTradeAdapter 模拟成交
└── scheduler/                  # 调度层
    ├── __init__.py             # 模块入口 + 导出
    ├── engine.py               # SchedulerEngine（APScheduler 包装器）
    ├── tasks.py                # TaskRegistry + TaskSpec + 注册默认任务（10 个）
    ├── jobs.py                 # 任务工作函数（L1/L2/L3/信号管道/健康检查/因子巡检/月度衰减/数据质量/数据级监控）
    ├── hotswap.py              # 热更新支持
    └── watchdog.py             # 看门狗进程
├── factor_db/                   # DuckDB 因子数据库层
    ├── schema.py               # 数据库 Schema 定义（13 张表，含质量评分/状态历史/审计报告/反馈 4 表 + seed_lineage 溯源 + factor_reviews 审查表）
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
    ├── 因子引擎 → 策略组建 → 信号合成
    │       │
    │       ├── elastic_net（默认，Elastic Net 截面回归）
    │       ├── sharpe_weight（按 Sharpe 归一化加权）
    │       ├── equal_weight（等权 1/N）
    │       └── ml_ensemble（ML 模型集成融合，通过 fts/ml/ 可选依赖）
    │
    ├── SignalBridge (fts/bridge/) → 交易信号输出
    │       │
    │       ├── JSON 文件协议（默认，无外部依赖）
    │       ├── Redis 协议（需 redis-py）
    │       └── REST 协议（HTTP POST/GET）
    │
    ▼
下游系统（VNPY/FDT/其他信号消费方）
```

### 期货数据流

```
┌─ 日线数据路径 ───────────────────────────────────────────────────┐
│ AKShare futures_zh_daily_sina                                      │
│    │                                                                │
│    │ scripts/download_futures.py（断点续传）                        │
│    ▼                                                                │
│ DuckDB kline_cache (data/fts_history.duckdb)                        │
│    │                                                                │
│    │ FuturesDataProvider._from_kline_cache()     ← 优先级1          │
│    │ AKShare 即时获取（降级）                       ← 优先级2        │
│    │ 合成数据（降级）                                ← 优先级3        │
│    ▼                                                                │
│ FTSDataProvider.get_futures_ohlcv() / get_futures_panel()            │
│    │                                                                │
│    │ --universe futures                                              │
│    ▼                                                                │
│ EvolutionLoop（期货横截面因子演化，跨品种因子计算）                  │
│    │                                                                │
│    ▼                                                                │
│ scripts/futures_signal_pipeline.py（横截面信号管道，方向校正 = 截面 IC │
│ 法，因子加权 = Ridge 回归 L2 正则化，Market Regime 检测 =           │
│ RegimeAwareSelector，品种-链对齐度修正 = compute_alignment）         │
│    │                                                                │
│    ▼                                                                │
│ reports/{date}/futures_signals_{date}.md                            │
└─────────────────────────────────────────────────────────────────────┘

┌─ 换月复权与展期仿真（v2.58.0，GAP-046） ─────────────────────────┐
│ contract_kline（具体合约日线，sync_futures_data_job 补拉写入）      │
│    │                                                                │
│    │ RollCalendar.build_roll_calendar(symbol)                       │
│    │   → 每日最大成交量判定主力 → 换月事件序列                      │
│    │   → adj_factor = 切换日新合约收盘 / 旧合约收盘（比率法后复权）  │
│    ▼                                                                │
│ kline_cache 新增 adj_factor 列（migrate_schema 幂等补列）            │
│    │                                                                │
│    │ FuturesDataProvider.get_ohlcv(adjusted=True)（默认）           │
│    │   → 原始 OHLC × 累积复权因子 → 复权序列（因子计算用）          │
│    ▼                                                                │
│ BacktestPipeline + TransactionCostModel                             │
│    │   → 持仓穿越换月日扣除展期价差成本（交易仿真用）               │
│    │   → 报告新增「展期成本统计」（换月次数/年化展期成本）          │
│    │   → contract_kline 缺失时降级：返回原始拼接序列（不报错）      │
└─────────────────────────────────────────────────────────────────────┘

┌─ DuckDB 并发模型（v2.86.0，GAP-056，design/E.1） ────────────────┐
│ 单写者 + 读连接池（读写分离，互不阻塞）                             │
│    │                                                                │
│    │ _get_writer() → DuckDBWriter（唯一可写连接，进程内写锁）       │
│    │   → execute / executemany / copy_from_records（BEGIN/COMMIT    │
│    │     包裹，批量原子，异常整批 ROLLBACK）                         │
│    │   → 写路径：_write_contract_kline / 数据同步                   │
│    ▼                                                                │
│ _get_reader() → DuckDBReader（连接池，普通连接，读语义纪律保证）    │
│    │   → _from_kline_cache / get_dominant_contracts 等读路径        │
│    │   → MVCC 快照：写提交期间读侧不阻塞                            │
│    ▼                                                                │
│ 兼容层：_get_db() 保留（旧调用方）；retry_on_conflict/AsyncWriteQueue│
│ 保留为防御兜底；duckdb_single_writer=false 回退旧多路径              │
└─────────────────────────────────────────────────────────────────────┘

┌─ 期货截面中性化 + 回测真实性仿真（v2.59.0，GAP-F03/F02） ────────┐
│ 板块映射（FUTURES_SECTOR_MAP 反向构建 {symbol: sector}）            │
│    │                                                                │
│    │ EvolutionLoop(market="futures") 自动注入 industry_map          │
│    │   → cross_section_evaluate_backtest 截面信号板块去均值         │
│    │   → 剥离产业链/板块系统性偏差，消除伪预测力（GAP-F03）         │
│    ▼                                                                │
│ BacktestPipeline._compute_strategy_returns                          │
│    │   → 涨跌停拦截：close 涨跌幅 ≥ limit_pct 当日持仓保持（GAP-F02）│
│    │   → 停牌过滤：volume==0 当日持仓保持                           │
│    │   → 报告新增「被拦截成交统计」（涨跌停/停牌次数）              │
│    │   → trade_filter=False 时跳过拦截（回归兼容）                  │
└─────────────────────────────────────────────────────────────────────┘

┌─ 股票截面中性化主流程（v2.61.0+，GAP-S01/S02） ────────────────┐
│ 行业映射 data/industry_map.json + 市值映射 cap_map                │
│    │                                                                │
│    │ EvolutionLoop(market="stock", cross_section) 自动加载          │
│    │   → 读取 FTSConfig.stock_neutralization（默认 true）           │
│    │   → load_industry_map() + cap_map_path（缺失股票标 UNKNOWN）   │
│    │   → 键归一化：映射键 "600519.SH" → 裸代码 "600519"（面板键）    │
│    ▼                                                                │
│ cross_section_evaluate_backtest（industry_map/cap_map 透传）        │
│    │   → _neutralize_signal_matrix 行业去均值 + 市值加权去均值       │
│    │   → 剥离行业/市值系统性偏差，消除伪预测力（GAP-S01）           │
│    │   → 报告输出中性化前后 IC 对比（ic_pre_neutral）               │
│    ▼                                                                │
│ Barra 风格中性化（v2.62.0，GAP-S02）                               │
│    │   → fts/factor_engine/barra/                                   │
│    │     ├─ barra_style.py        10 风格因子截面暴露（CNE6 简化）  │
│    │     │  size/beta/momentum/residual_vol/nonlinear_size/         │
│    │     │  book_to_price/liquidity/earnings_yield/growth/leverage  │
│    │     └─ barra_neutralizer.py  逐日截面 OLS 回归取残差           │
│    │   → 残差 = 纯 alpha（剥离风格暴露，回答"风格钱还是 alpha 钱"）  │
│    │   → 行业去均值 → 风格回归残差 两级中性化链                    │
└─────────────────────────────────────────────────────────────────────┘

┌─ 股票行业轮动 + 风格轮动 Regime（v2.65.0，GAP-S03） ──────────────┐
│ fts/factor_engine/stock_regime.py（StockRegimeSelector）            │
│    │  行业轮动维度: 行业收益面板 → 动量横截面离散度                  │
│    │    → rotation_strength + top-N 集中度                          │
│    │    → concentrated / rotating / balanced 三态                   │
│    │  风格切换维度: 风格指数面板（large/small/growth/value）        │
│    │    → 大小盘比值 + 成长价值比值 尾部动量方向                     │
│    │    → large_cap / small_cap + growth / value 双态               │
│    │  多周期集成: 复用 regime_hmm.MultiHorizonHMMDetector           │
│    │    → 比值序列构造合成 OHLCV → HMM 趋势态校正置信度              │
│    │  降级: hmmlearn 缺失/样本不足 → 规则动量判定 / fallback        │
│    ▼                                                                │
│ PortfolioLoop.run(stock_regime=...)（market="stock"）               │
│    │   → Step 2.5 优先使用 StockRegimeSelector 结果                  │
│    │   → REGIME_STYLE_MULTIPLIERS 新增 6 股票风格键:                 │
│    │     large_cap/small_cap/growth/value/                           │
│    │     sector_concentrated/sector_rotating → 风格自适应权重        │
│    ▼                                                                │
│ CLI `fts portfolio run --universe stock`（GAP-I301, v2.68.0）       │
│    │   → L3 组合构建完成（状态 passed/verifier_warning/completed）   │
│    │   → 自动触发 scripts/daily_signal_pipeline（股票信号管道，      │
│    │     方向校正=截面IC + Ridge 权重学习 + 仅做多 TopN）            │
└─────────────────────────────────────────────────────────────────────┘

┌─ 实盘执行链路 + 样本外强制 + 保证金建模（v2.60.0，GAP-F01/F08/F09）─┐
│ ① 样本外强制（GAP-F08）:                                             │
│   EvolutionLoop 晋升路径 → WalkForwardOptimizer 冷启动多窗口验证     │
│     → force_walkforward=true 强制（可配置跳过并记录原因）            │
│     → 多窗口 OOS IC 一致性替代 L1 单段 ICIR 近似，审计 oos_consistency│
│ ② 保证金建模（GAP-F09）:                                             │
│   CapitalAllocator.allocate(margin_rates) → 保证金占用 ≤ 可用资金    │
│     → 强平风险告警（保证金占用 > max_margin_usage）                  │
│ ③ 实盘执行链路（GAP-F01，信号侧完备性，真实网关由 FDT 负责）:       │
│   fts/live_trade/                                                    │
│     ├─ orders.py        OrderState 状态机（PENDING/SUBMITTED/        │
│     │                   PARTIAL/FILLED/CANCELED/REJECTED）           │
│     ├─ stop_orders.py   StopOrderManager 持仓级止损止盈单            │
│     ├─ intervention.py  InterventionController 紧急暂停/一键平仓     │
│     │                   （权限高于自动化）                           │
│     └─ gateway.py       AbstractGateway + SimulatedGateway           │
│                         （下单重试/超时兜底/状态回查）               │
└─────────────────────────────────────────────────────────────────────┘

┌─ 分钟级数据路径（v2.30.0+） ──────────────────────────────────────────┐
│ 三源分钟数据获取:                                                      │
│   通达信 TDX HTTP (17709) — 正序，1m/5m/15m/30m/60m                  │
│   通达信 TQ-Local (7721) — 倒序（统一反转），1m/5m/15m/30m/60m       │
│   天勤 TQSDK (tqsdk 包) — 正序，1m/5m/15m/30m/60m                    │
│    │                                                                  │
│    ▼ 时间对齐（统一 datetime 升序排序）                                │
│ DuckDB minute_cache (data/fts_history.duckdb)                         │
│    │                                                                  │
│    │ BacktestPipeline (frequency='1m'/'5m'/'daily')                   │
│    │   → 年化因子自适应                                              │
│    │   → z-score 窗口自适应                                           │
│    │   → 成本模型自适应                                               │
│    ▼                                                                  │
│ 分钟级回测报告                                                        │
└───────────────────────────────────────────────────────────────────────┘

┌─ 宏观字段增强层（v2.32.0+） ──────────────────────────────────────────┐
│ iFinD EDB (get_edb_data MCP) → edb_cache (indicator/date/value)      │
│    │                                                                  │
│    │ IFindSource.get_macro_series()（缓存查 → miss 拉取 → 幂等写回）   │
│    ▼                                                                  │
│ MacroFieldAligner.align()（月度→交易日 ffill + 发布滞后防未来函数）    │
│    │                                                                  │
│    ▼ 注入为 K 线 DataFrame 列（export/import_data/cpi/rate/us_bond）  │
│ BacktestPipeline._compute_factor() → _execute_factor_code()           │
│   → 宏观因子 data.get('export') 读取真实宏观数据（不再走 close 代理）  │
└───────────────────────────────────────────────────────────────────────┘
```

**宏观因子角色边界（v2.33.0）**：
- 宏观因子**禁止**进入单品种时序回测/信号管道。真实 EDB 数据对比证实
  fut_macro_export 家族在单品种（RB0）时序上 IC≈0（历史 Sharpe 7.68 为
  close 代理假象），v2.33.0 已全部 retire。
- 宏观数据注入层保留，仅作为**跨品种/板块层面**数据供给：
  ① SectorRegimeSelector 产业链 regime 选择；② 组合风险预算归因；
  ③ 跨市场泛化验证（futures→ETF 方向）。

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
- L3 定时任务（20:00）组合构建显式走期货路径（v2.73.0）：`elite_dir=futures_elite_dir` + `market="futures"`，与 CLI `portfolio run --universe futures` 对齐；完成后触发信号管道使用 `--universe all` 全量商品池。

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
- 产业链分类：`FUTURES_SECTOR_MAP` 定义 13 个产业链（黑色系/有色金属/能源/聚酯链/油化工/煤化工/橡胶/造纸林浆纸/航运/农产品/贵金属/新能源新材料/金融期货），每产业链品种不足 2 个或数据不足 20 行时跳过。造纸林浆纸链包含纸浆(SP0)/原木(LG0)/纤维板(FB0)/双胶纸(OP0)，航运链单列集运欧线(EC0)（v2.40.0 拆分，原"纸浆集运"链按产业链逻辑拆分为两链）。贵金属链包含黄金(AU0)/白银(AG0)/铂(PT0)/钯(PD0)，铂钯自"新能源/新材料"链归入贵金属板块（v2.45.0，铂族金属 PGM 与黄金白银同属贵金属）。

**品种-链对齐度增强（v2.22.0）**：
- 品种-链对齐度计算：在 `SectorRegimeSelector.detect_all()` 检测产业链制度后，调用 `compute_alignment()` 方法计算每个品种与其所属产业链的制度对齐度（0~1）。
- 对齐度计算逻辑：为每个品种独立创建 `RegimeAwareSelector` 实例检测其市场制度，与产业链综合制度对比：
  - 制度相同：对齐度 = 品种置信度 × 产业链置信度（置信度乘积，反映两者同时确定性强）
  - 制度不同：对齐度 = (1 - |置信度差|) × 0.5（差异越大对齐度越低，上限 0.5）
  - 数据不足（<20 行）：对齐度 = 0.5（默认中等对齐度，不修正信号权重）
- 对齐度修正信号权重：在信号管道中，品种信号权重按对齐度调整：
  - 修正公式：`weight' = weight × (1 + _ALIGNMENT_BLEND × (align - 0.5))`
  - 默认 `_ALIGNMENT_BLEND = 0.20`（修正强度，0.0=关闭，0.3=最大）
  - 高对齐度（≥0.7）品种信号权重上调，低对齐度（<0.5）品种信号权重下调
- 报告输出：信号报告中按对齐度等级（高/中/低）分组展示品种列表，标注对齐度修正强度与受影响品种数。
- 实现：`SectorRegimeSelector.compute_alignment()` 在 `fts/factor_engine/regime.py`，信号管道集成在 `scripts/futures_signal_pipeline.py`。

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
                              ├── 品种-链对齐度计算
                              └── 信号合成（含对齐度权重修正）

### 因子淘汰流（v2.17.0，v2.72.1 增加衰减分级闭环）

因子淘汰是主流程的正式环节，通过月度衰减评估触发，确保退化因子从活跃池中移除：

```
monthly_decay_eval_job (每月1日 02:00)
    │
    ├── EliteFactorTracker.run_monthly_evaluation() → 快照状态标记
    │       └── v2.72.1: update() 写入 decay_grade（normal/observe/retired）
    │            由滚动 6M IC 线性回归斜率 _calc_ic_slope_6m 判定
    │            （|slope|>=observe_slope 0.10 → observe；>=retire_slope 0.20 → retired）
    ├── AutoRetireManager.run() → 识别需淘汰因子
    │       └── v2.72.1: auto_retire() 纳入 decay_grade=="retired" 退役条件
    │
    ├── FeedbackLoop FACTOR_DECAY 联动（evolution_loop._run_periodic_factor_review）
    │       └── observe/retired 因子触发归因分析，last_feedback 写回跟踪快照
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
| L3 Portfolio Loop | 20:00 | 每日 | 期货路径（futures_elite + market=futures，v2.73.0）：因子筛选(ACTIVE_FACTOR_CAP=20) + 信号合成(默认elastic_net) + Verifier 校验 |
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
- **ML 依赖（可选）**: lightgbm >= 4.0, xgboost >= 2.0 (ml extra)
- **桥接依赖（可选）**: redis-py >= 5.0 (bridge extra)
- **测试**: pytest 7.4+, pytest-cov 4.1+
- **打包**: setuptools, pyproject.toml

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `seed_pool.py` → 双种子池（股票 482 因子：9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面；期货 81 因子：14 家族，见 seed_data_futures_full.py）；种子因子相关性预检（compute_seed_correlations，仅股票时序模式，≥0.95 标记高相关对）；`data_fundamental.py` → FundamentalProvider 基本面数据层；`data_futures.py` → FuturesDataProvider 期货数据层（82 品种 FUTURES_SUBSET + 59 个品种 DuckDB 缓存 + AKShare 降级，`get_futures_panel()` common_dates 多数对齐 ≥ 品种数//2，FUTURES_SYMBOL_NAMES 名称映射，get_dominant_contracts() 主力合约判定；`FUTURES_SECTOR_MAP` 7 产业链分类）；`data_futures_fundamental.py` → FuturesFundamentalProvider 期货基本面数据（库存/仓单/基差）；`scheduler/` → 调度层（5 个 APScheduler 定时任务：L1:08:30 / L2:23:00 / L3:20:00 / 信号管道:20:30 / 健康检查:每10m）；`scripts/futures_signal_pipeline.py` → 横截面信号管道（方向校正 = 截面 IC 法，因子加权 = Ridge 回归 L2 正则化，Market Regime 检测 = SectorRegimeSelector 产业链级分层判定，品种-链对齐度修正 = compute_alignment + _ALIGNMENT_BLEND=0.20，按日期定位，`--universe all` 全量商品池，输出品种名称/主力合约 + 产业链 Breakdown + Regime 调整交易建议 + 对齐度等级分组）；`fts/factor_engine/regime.py` → RegimeAwareSelector 市场制度感知（5 种制度：bull/bear/high_vol/low_vol/oscillate，MA20 斜率 + ATR/价格 + 量比 + 收益自相关）+ SectorRegimeSelector 产业链级制度检测（每个产业链独立构建合成 OHLCV，品种数加权投票计算主制度）+ `compute_alignment()` 品种-链对齐度计算（单品种独立检测与产业链对比，制度相同=置信度乘积，不同=上限0.5）；`strategies/strategy_evolution.py` → 策略进化（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion）；`fts/factor_engine/barra/barra_style.py` → BarraStyleEngine 10 风格暴露计算引擎（size/beta/momentum/residual_vol/nonlinear_size/book_to_price/liquidity/earnings_yield/growth/leverage，逐日截面 rank→z-score，字段缺失全 NaN 降级，nonlinear_size 引擎层基于 size 暴露矩阵逐日 z³ 对 z 回归残差）；`fts/factor_engine/barra/barra_neutralizer.py` → barra_neutralize_matrix 逐日 OLS（np.linalg.lstsq）风格暴露 + 行业虚拟变量回归取残差（样本不足降级去均值、常数列剔除、正交性保证）；`fts/factor_engine/evaluation_chain.py` → 评估链 Step 2.6 Barra 风格中性化（style_exposures 可选参数，行业去均值后叠加风格回归残差，两级中性化链 GAP-S01/S02）；`fts/factor_engine/stock_regime.py` → StockRegimeSelector 股票行业轮动 + 风格轮动检测（GAP-S03）：行业动量横截面离散度 + top-N 集中度 → concentrated/rotating/balanced 三态；大小盘/成长价值比值动量 → large_cap/small_cap + growth/value 双态；复用 regime_hmm.MultiHorizonHMMDetector 多周期集成（比值序列合成 OHLCV 校正置信度，规则动量方向主判定，空面板/样本不足降级）；`fts/factor_engine/portfolio_loop.py` → L3 Step 2.5 股票风格自适应（REGIME_STYLE_MULTIPLIERS 新增 6 股票风格键 large_cap/small_cap/growth/value/sector_concentrated/sector_rotating，run(stock_regime=...) market=stock 优先驱动） |
| 可验证断言 | 股票种子池总数 = 482；期货种子池总数 = 81（14 家族）；期货数据层支持 82 个连续合约品种，数据源优先级 3 级（DuckDB → AKShare → 合成）；common_dates 多数对齐（WH0 等停更品种不清空交集）；方向校正按日期定位；信号管道因子加权 = Ridge 回归（全量因子，L2 正则化）；主力合约判定 = contract_kline 最新交易日最大成交量；调度器注册 8 个任务（L1/L2/L3 + 健康检查 + 月度衰减 + 数据质量 + 逻辑监控 + 因子巡检）；信号管道集成 Market Regime 检测（5 种制度分层判定，输出 Regime 调整交易建议）；品种-链对齐度计算支持 3 种对齐度等级（高≥0.7/中0.5~0.7/低<0.5），默认 _ALIGNMENT_BLEND=0.20；策略进化模块包含 3 种策略（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion）；股票 L2 启用种子因子相关性预检（≥0.95 标记），期货 L2 跳过；L3 组合支持粘性约束（StickyConfig ±30% / 新因子首日封顶）+ 漂移监控（DriftMonitor → drift_history/YYYY-MM-DD.json）；L2 新晋升因子进影子池（shadow_pool 观察 5 交易日，种子因子 shadow_observe=False 直接进正式组合）；SchedulerEngine 支持 `start_watchdog()` 进程看门狗；L3 信号合成默认 elastic_net 模式（Elastic Net 截面回归，L1+L2 自动变量选择）；v2.73.0 调度器 `l3_portfolio_loop_job` 显式期货路径（elite_dir=futures_elite_dir + market=futures，`fts/scheduler/jobs.py`）；v2.75.0 机构级权重学习（`fts/factor_engine/weight_learning.py`）：Elastic Net 系数叠加 Ledoit-Wolf 收缩协方差风险调整（volatility_scaling/risk_parity）+ 滚动样本外验证（OOS IC/稳定性/衰减）+ 学习面板按目标交易市场自动匹配（futures→FUTURES_CORE_SUBSET / stock→CSI300）+ 跨市场迁移 IC 对比（v2.78.1 起默认关闭 cross_market_ic=False，避免无关股票面板下载，需显式开启）；ACTIVE_FACTOR_CAP=20，超出上限时按 Sharpe 排名保留 Top 20；v2.58.0 换月复权与展期仿真（GAP-046）：`kline_cache` 含 `adj_factor` 列，`get_ohlcv(adjusted=True)` 默认复权，BacktestPipeline 持仓穿越换月日扣展期价差成本，contract_kline 缺失时降级返回原始序列；v2.59.0 期货截面中性化 + 回测真实性仿真（GAP-F03/F02）：EvolutionLoop(market="futures") 自动注入板块映射（FUTURES_SECTOR_MAP 反向构建 {symbol: sector}），截面信号板块去均值；BacktestPipeline 涨跌停拦截（close 涨跌幅 ≥ limit_pct 持仓保持）+ 停牌过滤（volume==0 持仓保持），报告含「被拦截成交统计」；v2.62.0 Barra 风格体系（GAP-S02）：barra 包 10 风格暴露 + 逐日 OLS 回归残差（样本不足降级去均值、常数列剔除、正交性保证），`cross_section_evaluate_backtest` Step 2.6 风格中性化（行业去均值后叠加，两级中性化链），test_barra.py 13 用例全绿；v2.65.0 股票 Regime（GAP-S03）：StockRegimeSelector 行业轮动三态 + 风格切换双态（规则动量方向主判定 + MultiHorizonHMMDetector 多周期集成校正置信度），REGIME_STYLE_MULTIPLIERS 含 6 股票风格键（large_cap/small_cap/growth/value/sector_concentrated/sector_rotating），PortfolioLoop.run(stock_regime=...) 驱动 L3，test_stock_regime.py 19 用例全绿；v2.69.0 股票流水线成熟度收尾（GAP-S09~S12）：`expr_dsl/seed_analyzer.py` 种子表达式静态 PIT 审计（estimate_lookback_static 替换正则，705 表达式扫描仅 1 个 fundamental 切片语法需显式 lookback）；`verify_registry_consistency` 双注册表重叠算子一致性（test_registry 断言 overlapping ≥ 10 且 zero mismatch）；`evolution_mode` 新增 operator_first（股票演化默认算子优先 → LLM → GP 逐级兜底，state `evolution_method_counts` 方法分布记账）；`A_SHARE_FIELDS` 10 A 股特有字段 + L5b 4 领域算子（nb_momentum/margin_change/holder_concentration/analyst_revision_ratio）；新增 test_seed_analyzer 14 + TestGapS11OperatorFirst 7 用例；v2.79.0 阶段 D 收尾（GAP-F10/F12/F15）：CI 质量门禁（`ruff check` + `ruff format --check` fts/tests/scripts + `mypy fts/` 150 files Success + `pytest tests/benchmarks/ --benchmark-only` + `v*` tag 构建发布，`.github/workflows/ci.yml`）；极值扰动一票否决（`evaluation_chain._compute_extreme_perturbation_ic` 极值剔除重算 IC → `FactorEvaluation.extreme_perturbation` → HighICScreener V2 `ic_drop>25%` 拦截真正生效，`FTSConfig.extreme_perturb_pct` 默认 0.01）；种子库去重校验（`scripts/verify_seed_dedup.py` 内嵌 vs YAML 交叉比对）+ 家族上限配置化（`FTSConfig.max_per_family` env `FTS_MAX_PER_FAMILY` 缺省 15）；v2.80.0 数据驱动动态池（GAP-054）：`get_dynamic_core_subset()` 读取 memory/portfolio/futures_dynamic_pool.json（缺失/损坏回退静态 FUTURES_CORE_SUBSET 25 品种），data.py/CLI/L3 portfolio_loop/weight_learning/sync_contract_kline/调度任务默认路径全部走动态池；流动性快照口径 = TQ-Local（通达信 17709）真实主力合约最近 5 日主力窗口成交额（量×价×合约乘数），每周六 08:00 `sync_liquidity_pool` 渐进式替换刷新（池内够格全保留 + 产业覆盖约束）；v2.81.0 盲测池（GAP-055）：`FUTURES_HOLDOUT` 6→15 个按产业链分层抽样（覆盖 10 条产业链），与核心动态池/分层训练集互不重叠，L2 演化训练排除盲测池后训练品种 >= 10；v2.84.0 tick 历史缓存增量累积 + Level2 订单流因子（GAP-I503 首期）：`aggregator._write_tick_cache` 按 (symbol, datetime) 去重写入 + `tick_cache_retention_days` 保留清理（默认 7 天），`get_ticks`/`_try_tick_cache` 支持 `start_time`/`end_time` 时间窗口查询（跨会话多次拉取累积成更长 tick 历史）；新建 `fts/factor_engine/microstructure_factors.py`——`classify_tick_direction`（价差方向，持平沿用前向）/`order_flow_imbalance`（滚动窗口主动买卖量差归一化 OFI）/`order_book_imbalance`（5 档深度 OBI）/`large_trade_ratio`（绝对/相对阈值大单占比）/`compute_microstructure_factors` 统一入口（FACTOR_COLUMNS 契约 datetime/direction/trade_volume/ofi/obi/large_trade_ratio，缺列/不足 min_rows 优雅降级空）；v2.85.0 组合目标函数换手惩罚项（GAP-I303）：`portfolio_loop.apply_turnover_penalty`——粘性约束后、归一化前 `w_new' = w_old + (w_new − w_old)/(1+λ)` 收缩权重变动（λ=0 关闭、λ 越大换手越低、新因子不惩罚），`build_combo`/`PortfolioLoop` 参数透传 + `FTSConfig.l3_turnover_penalty`（env `FTS_L3_TURNOVER_PENALTY` 默认 0.0）|
| 检验方式 | `python -c "from fts.scheduler.tasks import list_tasks; assert len(list_tasks()) == 10"` |
| 分钟级数据流 | `fts/data_sources/aggregator.py` 新增 `get_minute_ohlcv()` 方法；`fts/data_sources/tq_source.py` 支持 `period` 参数；`fts/data_sources/tqsdk_source.py` TQSDK 分钟数据源；`fts/data_sources/tdx_minute_source.py` 通达信分钟数据源；`fts/factor_engine/backtest_pipeline.py` `BacktestInput.frequency` 字段；`fts/cli.py` `--frequency` 参数 | `minute_cache` 表结构存在 (symbol/period/datetime/open/high/low/close/volume/source)；`BacktestInput.frequency` 支持 "daily"/"1m"/"5m"/"15m"/"30m"/"60m"；年化因子自适应 252(daily)→98280(1m) | `pytest tests/test_backtest_frequency.py` |
