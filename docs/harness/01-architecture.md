# FTS 系统架构文档

> 版本: v2.104.0+73
> 最后更新: 2026-08-10

---

## 1. 项目概述

FTS（Factor Intelligence System，因子智能系统）是一个独立的期货因子策略系统，专注于期货因子推演、策略组建与交易信号产出。数据层基于 DuckDB kline_cache（主源）+ AKShare/通达信/天勤（降级）提供期货行情数据，FTS **本身包含自洽的数据源适配层**，无外部数据项目依赖。股票管线已剥离至独立项目 fts-stock（v0.0.1，2026-08）。

### 项目边界

| 职责 | 归属 |
|:-----|:-----|
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
│    ├ AnnouncementNewsExtractor（公告/舆情，已剥离至 fts-stock）        │
│    ├ MacroEventExtractor（宏观事件，期货管道）                        │
│    └ WebSearchExtractor（动态因子源，plans/41 A，v2.104.0+70：        │
│      必应检索量化平台/能化链关键词 → LLM 提取，每轮动态换新知识）      │
│      max_factors 配置化（A3，v2.104.0+71：l1_extractor_max_factors，  │
│      研报/论文/宏观/WebSearch LLM 源统一配额，天软感知源不参与）       │
│    多源并行收集（BaseExtractorPipeline.extract ThreadPoolExecutor）    │
│  - FactorReviewWorkflow 人审驳回 → ExperienceChain（GAP-I102 二期）     │
│  - 感知层样本：期货 → 五大板块 13 品种；web_collector 期货模式走        │
│    FTSDataProvider.get_futures_ohlcv（v2.100.1 按市场区分的机制已随     │
│    股票样本剥离至 fts-stock，成为历史记录）；l1_meta_loop_job 已接入    │
│    web_collector（plans/41 A，v2.104.0+70）                             │
│  - 能源链实时知识注入（plans/41 C，v2.104.0+70）：_inject_chain_       │
│    knowledge 静态链知识 + 实时产业状态段（子链价差/波动聚集/库存基差     │
│    水位代理，面板异常自动降级）                                         │
│  - 按子链分批 bootstrap（plans/41 D，v2.104.0+70）：energy 市场四子链   │
│    各一批（每批独立 chain_focus 注入 prompt）；futures 保持单批         │
│                                                                         │
│  职责: 每日知识补给 → 种子因子注入 → 市场语境感知 → 演化方向指引        │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │ 注入种子因子 + 演化方向
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  L2 Evolution Loop (演化循环 — 因子核心演化层)                           │
│                                                                         │
│  ┌─ 期货 L2 (横截面面板) ─────────────────────────────────────────┐     │
│  │ parent_selection → evolution_mode → micro_evolution              │     │
│  │ (UCT 树搜索)       (code/hybrid/operator)  (optuna 调参)         │     │
│  │                      ├─ LLM 改逻辑 (code)                        │     │
│  │                      ├─ GP 演化 (code/hybrid fallback)            │     │
│  │                      ├─ 算子演化 (operator/hybrid fallback)       │     │
│  │                      ├─ 深度演化 (GAP-I203, v2.73.0: GRU; C5 2026-08-11: Transformer)         │     │
│  │                      └─ FTS-Expr DSL (Phase C.2)                  │     │
│  │   → cross_section_evaluate → elite                                │     │
│  │   (横截面直接回测)                                                  │     │
│  │                                                                         │
│  │   种子池: 81 期货因子 (14 类: 动量5/期限结构3/持仓3/流动性3/     │     │
│  │          高阶矩3/波动率2/基本面4/拥挤度6/Alpha4/高频6/期权3/       │     │
│  │          市场环境8/CTA补充7/算子字典24)                             │     │
│  │   数据: 82 品种 OHLCV 面板 (common_dates 多数对齐)                 │     │
│  │   评估: cross_section_evaluate_backtest (因子加权=Ridge)           │     │
│  │   相关性预检: 跳过 (横截面无单标的信号)                             │     │
│  └─────────────────────────────────────────────────────────────────────┘     │
│                                                                         │
│  evolution_loop.py — L2 主循环协调器 (通过 cross_section 参数区分模式)    │
│  batch_mining.py — 批量挖掘漏斗 (GAP-I201, v2.65.0, evolution_mode=batch):  │
│    BatchMiner: 批量生成(同父多后代, macro 至多 1 次 + GP/deep/transformer/operator │
│    四方法轮换 idx%4, GAP-I203 v2.73.0 deep 并入 idx%3==2, C5 2026-08-11 并入      │
│    transformer idx%3==3) →                                                       │
│    ExecutorBackend 可插拔粗筛 (thread 默认/process/dask/ray, GAP-I502) →  │
│    按预筛 IC 排序截断 → _process_candidate 准入链
│  预筛与通道修复 (v2.66.0, GAP-X01/X02/X03):                               │
│    - _quick_prefilter 横截面模式走 _cross_section_prefilter: 全面板信号矩阵 │
│      vs 截面 forward 收益 (与 cross_section_evaluate_backtest 同口径)      │
│    - operator 生成常数校验前移: 生成循环内 evaluate 过滤非常数表达式       │
│    - _execute_factor_code exec 后 exec_globals.update(local_vars), 修复    │
│      eval_fts_expr 未定义 (模块级 import 绑定并入 factor_program.__globals__)
│  seed_pool.py — 期货种子池管理（81 因子，style_tags 14 类）                     │
│  factor_program.py — 因子程序（图灵完备代码 + 安全沙箱）                  │
│  verifier.py — Verifier 锁定协议                                       │
│  state.py — 演化状态管理 + trace_id 全链路                              │
│  gp_evolver.py — GP 遗传规划搜索引擎 (Phase C.1)                        │
│    v2.66.0 (GAP-X03): 模板 ts_product 改用 rolling.apply(np.prod)       │
│    (pandas≥2.1 移除 Rolling.prod); _evaluate_fitness 后处理对齐流水线    │
│    (nan_to_num + clip[-10,10] + std<1e-12 常数罚分), 产物与运行时校验对齐│
│    v2.104.0+54 (plans/37 Step 2 批 1): rolling.apply 类算子改            │
│    sliding_window_view 向量化内核（ts_product 等 7 热算子 11–525x）      │
│  ml/deep_factor.py — 深度因子生成器 (GAP-I203, v2.73.0; C5 2026-08-11):      │
│    DeepFactorGenerator: OHLCV 特征(日收益率+量变化率) → 滚动窗口样本 →        │
│    前 train_ratio 训练 GRUFactorModel (纯 numpy BPTT) 或                    │
│    TransformerFactorModel (纯 numpy 单头自注意力 + 因果掩码上三角 −inf,       │
│    C5, DeepFactorConfig.model_kind="gru"|"transformer" 默认 gru) →          │
│    权重序列化内嵌 def factor_program code (零未来函数: 窗口 [t-lookback+1,t]   │
│    逐 t 推理 + tanh 压缩; 样本不足/训练失败返回 None 降级);                   │
│    evolution_loop._run_deep_evolution(model_kind) 接入 L2 (method_hint="deep"│
│    → gru, "transformer" → transformer, 失败降级), 产物过全套审计链            │
│  expr_dsl/ — FTS-Expr 算子表达式语言 (Phase C.2)                        │
│    registry.py — 算子注册表 (L0-L5 分层, 参数边界, 经济语义;            │
│      GAP-S10: verify_registry_consistency 双注册表一致性;               │
│      GAP-S12: A_SHARE_FIELDS 10 A股特有字段 + L5b 4 领域算子           │
│      （A 股特有，已随股票管线剥离至 fts-stock）；                       │
│      GAP-I202: ts_slope/ts_quantile 时序组合算子 + required_shared      │
│      硬约束——440 算子（8 组合/跨标的 + C8 22 + C9 30 + D10~D17 380）必须 │
│      feature_ops.OperatorRegistry 共享；DSL 算子总数 512（2026-08-11     │
│      扩容二期，GP 侧 491）；                                            │
│      目录自动生成 scripts/generate_operator_catalog.py →                │
│      docs/harness/_data/operator_catalog.yaml 幂等)                     │
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
│        (code/hybrid/operator/operator_first/batch;                      │
│         GAP-S11: 算子优先 → LLM → GP 逐级兜底; 方法分布记账)           │
│        → optuna 参数优化 → 评估 → 审计 → 高IC筛查(B.4) → 4 重审查门禁 → 结构性聚类配额(GAP-077: 与既有 elite |corr|≥0.85 同类成员 ≥15 拒绝；v2.104.0+25 起 max_per_family 家族配额已彻底删除，仅聚类配额路径) → elite 因子 →       │
│        [Phase 1.1 P0-2/26 计划] LLM 宏观演化注入父因子失败归因: read_failures_by_parent → ParentFailureContext → prompt 定向修复 │
│        [Phase 1.2 P0-1/26 计划] 成功模式定向演化: success_pattern.py 聚合近期成功模式(方法/算子/窗口，排除 style_tags) → prompt soft 偏向 │
│        [GAP-080] SHAP 批量计算降频: 采样参数经 FTSConfig(shap_n_extreme=25/shap_n_background=50/shap_nsamples=50) 配置 │
│        [Phase 2 P1-2/26 计划] 结构化实验日志: experiment_log.py 聚合 run 内候选全结局 → data/experiments-{run_id}.json（finally 非阻塞导出） │
│        [Phase 3 P1-3/26 计划] 提前达标停止: 连续 K 代零晋升（state 晋升计数 diff）→ early_stopped 正常收尾（保守默认关闭，K=5） │
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
│  - synthesize_signals（信号合成，支持八种模式：equal_weight/sharpe_weight/quality_weight/elastic_net/ml_ensemble/adaptive/ic_weight/optimizer（optimizer 内含 risk_parity/mvo/bl，C3 2026-08-11 并入 Black-Litterman 观点融合）；CLI --synthesis-mode risk_parity 为 optimizer+risk_parity 快捷（v2.104.0+62，矩阵自动构建仅用于权重合成））│
│  - ACTIVE_FACTOR_CAP=20（数量安全阀：P1 聚类 + 子链去冗余后代表数仍超限时，按 OOS 校正综合评分截断；v2.104.0+67 起由"选优"降级为"安全阀"）│
│  - generate_agent_proposals（Agent 提案生成）                          │
│  - load_elite_factors（加载 elite 因子，过滤影子池观察期因子）          │
│  - L3Verifier（L3 锁定协议）                                           │
│  - DriftMonitor（组合漂移监控：成员重合率 + 权重 L1 变化率）            │
│  - _apply_sticky_constraints（粘性约束：±30% 变动 / 新因子首日封顶）    │
│  - adaptive_weight.py（自适应权重 v2.56.0 接入）                        │
│    - AdaptiveWeightConfig（style 维度 + smoother 参数）     │
│    - RegimeSmoother（Regime 切换权重指数平滑，alpha=0.5, min_days=2）   │
│    - REGIME_STYLE_MULTIPLIERS（Regime × FactorStyle 倍率表，style 维度）│
│  - FactorStyle 枚举 + style_tags 字段（contracts.py / factor_catalog） │
│                                                                         │
│  P1 因子聚类流程（v2.104.0+67 顺序调整：聚类 → 子链去冗余 → CAP 安全阀）│
│    Step 1.8: FactorClusteringEngine.run()（在 CAP 之前，输入全部合格因子）│
│      → 使用 FactorExecutor 在参考品种上计算每个因子的信号序列           │
│      → 计算 Pearson 相关系数矩阵                                        │
│      → 层次聚类（average linkage，距离阈值默认 0.7）                    │
│      → 从每个簇中选择综合评分最高的代表因子（plans/36 改进项 2/4）      │
│    Step 1.7: CAP 数量安全阀（ACTIVE_FACTOR_CAP=20，v2.104.0+67 起）：   │
│      → 聚类 + 子链去冗余后代表数仍超限才截断（防御性；不再按样本内评分 │
│        选优——避免数据窥探式选择系统性偏向过拟合因子）                  │
│      → 截断排序键 = OOS 校正综合评分（use_oos_ic=True：因子带           │
│        oos_extrapolation.new_ic 时 ic 维度取样本外 IC，否则回退样本内） │
│    Step 1.8b: 子链维度去冗余（GAP-121 扩展，能源链专属）                │
│      → _dedup_factors_by_chain（portfolio_loop.py）                     │
│      → 基于逐品种 IC（symbol_ic，评估链 GAP-075 输出，elite JSON        │
│        兜底读取）构建"主导子链"画像（子链内平均 |IC| 最高者）           │
│      → 同一子链保留因子数 ≤ l3.chain_dedup.max_per_chain（默认 2），    │
│        链内按综合评分降序截断；symbol_ic 缺失因子归 unknown 组直接保留  │
│      → 与 Step 1.8 信号相关性聚类互补：同子链因子即使信号相关性低，     │
│        仍共享产业链驱动（原油→化工传导），同步放大子链暴露              │
│      → 配置：settings.yaml l3.chain_dedup.{enabled, max_per_chain}      │
│        （仅 market=energy 生效）                                        │
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
  - 期货 L2: 185 期货因子种子池（按 style_tags 分类，YAML 优先加载 seed_loader，横截面模式；硬编码兜底 81 因子/按 style_tags 分类，见 L2 架构图）
  - 种子晋升 elite 质检链与演化因子完全对齐（v2.50.0）：Verifier 判定 + 质量评分卡 + 端到端回测 + 数据质量监控 + FactorAuditor 6 项强制审计 + 消融实验 + 因果结构审查 + 鲁棒性审查 + SHAP 可解释性分析，任一关卡失败即拒绝晋升（种子 L1 注入候选与人工精选种子一视同仁）
- **L2 → L3**: 
  - 期货 L2: elite 因子 + 横截面评估指标 + 因子加权权重（Ridge 回归）

---

## 3. 模块结构

```
fts/
├── __init__.py                 # 包入口 + 版本号 v2.3.0
├── cli.py                      # 统一命令行入口
├── data.py                     # 数据层统一入口（期货）
├── data_cache.py               # 数据缓存管理
├── data_mcp.py                 # MCP 数据适配层（akshare 腾讯/东方财富）
├── data_mcp_bridge.py          # MCP Bridge 桥接层
├── data_futures.py             # 期货数据适配层（DuckDB kline_cache + AKShare）
├── data_futures_fundamental.py # 期货基本面数据（库存/仓单/基差）
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
│   ├── tdx_local_source.py     # 通达信本地 HTTP 统一源（端口 17709，日线+分钟+快照，v2.87.0；GAP-084 股票列扩展已随股票管线剥离至 fts-stock）
│   ├── tqsdk_source.py         # 天勤 TQSDK 数据源（分钟/日线）
│   ├── tqsdk_enhance_source.py # 天勤 TQSDK 字段增强源（GAP-083 阶段 C：close_oi→hold、差分→oi_change）
│   ├── ifind_sdk_source.py     # iFinD 官方 SDK 字段增强源（GAP-083 方案 A 框架保留：settle/pre_settle 权威，无 iFinDPy 权限不启用，pre_settle 走零依赖派生）
│   ├── macro_aligner.py        # 宏观字段增强层（EDB 时序对齐 + 注入，v2.32.0；v2.101.0 默认源切 EastmoneyMacroSource）
│   ├── macro_eastmoney_source.py # 东财/中债登宏观数据源（GAP-088 数据源闭环，v2.101.0：东财 RPT_ECONOMY_CPI/CUSTOMS + akshare 中债登 1 年期/美债 10 年期，edb_cache 与 IFindSource 同构互操作）
│   └── migrate.py              # 数据迁移工具
├── factor_engine/              # 因子引擎（核心模块）
│   ├── __init__.py             # 模块入口 + 版本号 v1.1.0
│   ├── contracts.py            # 完整 TypedDict 契约（L1+L2+L3）
│   ├── evolution_loop.py       # L2 主循环（期货横截面模式）；34 计划起拆分为多 Mixin 组合（UCT/trace/channels/seeds/audit/review/prefilter/promote/candidate），公开 API 不变
│   ├── evolution_uct.py        # UctSelector 协作类（34 计划领域 I，C 阶段 Phase 47a 由 Mixin 组合式重构）：_select_parent_uct/_update_uct_stats/_update_uct_failure + _check_circuit_breaker/_maybe_early_stop，状态 _uct_stats/_evolution_stop_*/_consecutive_empty_generations/_early_stop_* 随迁，_consecutive_low_ic 经 low_ic_box 注入；主类组合持有 + 转发桩
│   ├── evolution_trace.py      # trace/经验链 Mixin（34 计划领域 J，2026-08-13）：12 方法（_record_*_trace ×6 + _build_parent_failure_ctx/_build_success_pattern_report/_record_experiment_variant/_export_experiment_log/_log_inspection_detail + _QualityInspectionResult 数据类），内部状态 _success_pattern_cache/_experiment_log_dir/_experiment_variants
│   ├── evolution_channels.py   # 演化通道 Mixin（34 计划领域 G，2026-08-13）：_run_gp_evolution/_run_deep_evolution/_generate_operator_factor/_try_operator_engine_evolution（GP/深度/算子 DSL 三通道），组件 feature_ops_engine/feature_importance_analyzer 装配于主类
│   ├── evolution_seeds.py      # 种子/横截面 Mixin（34 计划领域 D，2026-08-13）：_evaluate_and_promote_seeds（种子评估晋升编排，跨调 E/F/C/J 域方法）/ _merge_l1_candidates（GAP-031 L1 注入候选合并）/ _run_seed_correlation_check / _build_barra_exposures（GAP-I304 风格暴露缓存）/ _evaluate_cross_section / run_microstructure_promotion（C1 公开入口），组件 cap_map/industry_map/_barra_exposures_cache 装配于主类；**v2.104.0+48 GAP-121 补全：_evaluate_cross_section 由走航结果派生 ic_volatility/decay_6m 并透传 extreme_perturbation/cross_symbol_positive_ratio/backtest_pipeline 至 FactorEvaluation（HighICScreener 消费字段，跳过项 9→2）**
│   ├── evolution_audit.py      # 审计/验证 Mixin（34 计划领域 E，2026-08-13）：_run_factor_audit（FactorAuditor 编排 + GAP-F08 冷启动走航优先）/ _run_walkforward_oos（独立走航）/ _run_backtest_pipeline（BacktestPipeline 薄包装）/ _run_ablation_check（消融，_ABLATION_* 类常量 + _is_blocking_ablation）/ _run_robustness_check / _run_shap_analysis / _run_causal_validation / _build_wf_config（staticmethod 纯函数），组件 auditor/backtest_pipeline/ablation_experiment/robustness_tester/shap_analyzer/causal_validator 装配于主类；_signal_cache 与 B 域共享保留引用
│   ├── evolution_review.py     # 定期评审/数据质量 Mixin（34 计划领域 F，2026-08-13）：_run_periodic_factor_review（精英因子定期重评估：自动淘汰 GAP-I305 + 衰减反馈联动 + LogicMonitor 集成 + 状态报告）/ _get_factor_data_for_review / _register_factor_baseline / _check_factor_data_quality（均 DataQualityMonitor 薄包装），组件 data_quality_monitor 与 A 域共享；elite_tracker/feedback_loop/logic_monitor 装配于主类
│   ├── evolution_prefilter.py  # 候选预筛 Mixin（34 计划领域 H，2026-08-13）：_quick_prefilter（快速预筛三元组：信号变化/标准差/快速 IC，市场自适应阈值）/ _cross_section_prefilter（横截面真实截面 IC，GAP-X01）/ _check_factor_runtime（后代运行时校验，复用 BacktestPipeline 执行路径），纯读全局上下文 data/market/forward_returns/cross_section_*/_is_cross_section
│   ├── evolution_promote.py   # 精英晋升/持久化 Mixin（34 计划领域 C，2026-08-13）：_promote_to_elite（精英晋升全流程：去重/结构簇配额/L2 准入去冗余/正交化闭环/高IC筛查/多重检验/影子池（v2.103.0+20 起默认关闭，FTS_EVOLUTION_SHADOW_OBSERVE=1 恢复）/种子溯源/追踪器注册，@_release_repo_after）/ _write_to_duckdb（DuckDB 主存储幂等写入）/ _scan_elite_correlations / _check_elite_correlation / _count_cluster_members / _orthogonalize_via_basis（Gram-Schmidt 基底）/ _orthogonalize_candidate（单参照 OLS）/ _load_elite_parent_factors / _write_seed_correlation_index / _get_repo（延迟初始化仓储）/ _release_repo_after（E.4 S1 写锁释放装饰器），领域独享状态 _repo/_cluster_*/_l2_*/orthogonal_basis/high_ic_screener/elite_tracker 装配于主类
│   ├── evolution_candidate.py # 候选准入链 Mixin（34 计划领域 B，2026-08-13，B 阶段收官）：_process_candidate（Step 2-6 准入链：微观演化→三级评估→UCT 反馈→Verifier→质量评分卡→端到端回测→数据质量→6 项强制审计→消融→因果→鲁棒性→SHAP→晋升/淘汰→状态持久化），21 个跨域方法经 Callable 类型声明经 MRO 派发
│   ├── orthogonal_basis.py     # 多因子正交基底（GAP-I206 补充, v2.72.1）：Gram-Schmidt 迭代残差化 + 基底注册/持久化
│   ├── batch_mining.py         # 批量挖掘漏斗（GAP-I201, v2.65.0）：BatchMiner 批量生成 + 并行粗筛 + 排序截断，evolution_mode="batch" 时每代批量候选
│   ├── executor_backend.py     # 可插拔执行器抽象（GAP-I502, v2.83.0；C4 2026-08-11 增强）：ExecutorBackend（thread/process/dask/ray）+ create_executor_backend 工厂，BatchMiner.filter_batch 接入；DaskBackend：cluster 句柄注入/address 优先（单机 LocalCluster 等价多节点调度语义，真实集群 tcp://scheduler:8786 接入后置）、worker_count（scheduler_info 视角诊断）/kill_worker（故障注入）/alive_workers 均降级不抛、map 经 _DaskResultIterator 单任务异常隔离（对齐 concurrent.futures.map 契约，生成器异常会关闭丢失后续任务）；缺依赖自动降级 ProcessBackend
│   ├── microstructure_factors.py # Level2 订单流因子（GAP-I503 首期, v2.84.0）：OFI 订单流不平衡/OBI 盘口不平衡/大单占比 + compute_microstructure_factors 统一入口（FACTOR_COLUMNS 契约）
│   ├── microstructure_generator.py # 微观结构因子生成器（C1 2026-08-11）：tick→日频聚合→FactorProgram 独立候选源（get_ticks → compute_microstructure_factors → 日聚合 ofi_mean/obi_mean/ltr_mean/ofi_std → 日期查找 code 零未来；CLI fts factor micro-generate；执行器对 DatetimeIndex 注入 datetime 列供日期对齐；L2 晋升接线 evolution_loop.run_microstructure_promotion——生成候选→横截面评估（ic≥0.03&sharpe≥1.5）→FactorAuditor 6 项审计→_promote_to_elite 护栏，CLI fts factor micro-evaluate）
│   ├── alternative_sentiment.py # 舆情情感因子生成器（C2 2026-08-11）：新闻→词典打分→日频聚合→FactorProgram 独立候选源（FinancialSentimentLexicon 内置金融情感词典±强度+否定反转 score_text ∈[-1,1]；EastmoneyNewsProvider 新闻搜索（失败降级空）；日聚合 sent_mean/sent_std/sent_chg；可选 DuckDB sentiment_daily 落库；CLI fts factor senti-generate；LLM 精修 LlmSentimentScorer（LLMClient.complete 约束 [-1,1] 异常降级）+ evaluate_lexicon_consistency（词典-LLM 一致性 ≥0.7 验收，CLI fts factor senti-consistency）
│   ├── meta_loop.py            # L1 元循环
│   ├── portfolio_loop.py       # L3 组合循环（plans/40 接入 SignalCache + 向量化对齐 + 信号矩阵一等公民增量）
│   ├── l3_signal_service.py    # L3 信号矩阵服务（plans/40 B/D 层，v2.104.0+63）：SignalMatrixBundle 2D/3D 信号矩阵 + build_signal_matrix（复用信号缓存 + df.index.get_indexer 向量化对齐）+ DuckDB corr/因子收益矩阵 SQL 下沉 + load_or_build_signal_matrix 增量重算（code_hash 判定，仅新因子全量/存量追加窗口），E.4 短连接 + filelock 纪律，依赖缺失逐品种现值回退零漂移
│   ├── macro_evolution.py      # LLM 宏观演化
│   ├── micro_evolution.py      # optuna 微观调参
│   ├── evaluation_chain.py     # 三级评估链（CTA 手册阶段4：IR 按因子类别分级门槛，v2.104.0+19 接入 ir_thresholds）
│   ├── panel_vector.py         # 横截面评估全矩阵化（plans/37）：AlignedPanel 预对齐面板 + compute_cs_ics_vectorized 全矩阵 IC（联合掩码 rank + 行内 Pearson）+ execute_factor_panel 面板化因子执行（算子因子 DSL 按列求值，动态抽样验证 + 安全回退），跨截面评估开关 cross_section_panel_vector 默认开启（v2.104.0+57）；plans/39 §11（v2.104.0+58）真实缺口面板算子面板化实测 0.3x <5x 门槛 → 评估链信号构建摘除面板化恒逐品种执行，仅 IC 计算走矩阵化，execute_factor_panel 保留为独立模块/对照基准（缺口感知滚动内核 gap_aware_mode + _GapAwareFrame） |
│   ├── ir_thresholds.py        # 因子 IR 分类门槛（CTA 手册阶段4，v2.104.0+19）：量价 0.30/基本面 0.40/期限结构 0.35，按 style_tags 判定，未知回退最宽松档
│   ├── signal_cache.py         # 质检信号缓存（GAP-071，v2.98.2）：LRU 信号复用
│   ├── experience_chain.py     # 经验链存储
│   ├── ml/deep_factor.py       # 深度因子生成器（GAP-I203, v2.73.0；C5 2026-08-11 并入 Transformer）：GRU/Transformer 训练 → 权重内嵌零未来函数 code（DeepFactorConfig.model_kind 分派）
│   ├── factor_optimizer.py    # 因子优化器（FactorSignalCache 信号缓存 Parquet 化：put 写单列 Parquet（DuckDB 零依赖）+ checksum 校验 + .npy 只读兼容回退重建，plans/29 P3-A）
│   ├── seed_data_futures_full.py # 期货全量种子因子（style_tags 14 类，81 因子）
│   ├── seed_pool.py            # 期货种子池（81 因子，style_tags 14 类）
│   ├── factor_program.py       # 因子程序（安全沙箱；fix_factor_code 自动修复器 v2.104.0+40 新增 IndentationError 缩进修复：反缩进语句/多余缩进/unindent mismatch/缺缩进块）
│   ├── standardizer.py        # 因子标准化
│   ├── verifier.py             # Verifier 锁定协议
│   ├── state.py                # 演化状态管理 + trace_id 全链路
│   ├── program.py              # L0 人类设定（Program.md）
│   ├── walk_forward.py         # 走航验证
│   ├── cost_model.py           # 交易成本模型（C7 2026-08-11：load_market_cost_config FTS_COST_* 配置化 + 融资成本 financing_cost_bps + cost_breakdown 5 分项明细；CTA 手册阶段1 品种差异化成本 v2.104.0+19：VarietyCostConfig 按比例/固定金额手续费 + 平今仓倍率 + 滑点/冲击覆盖，get_effective_cost_bps + adjust(symbol=)）
│   ├── regime.py               # 市场制度检测（RegimeAwareSelector + SectorRegimeSelector 产业链级）
│   ├── rebalance_controller.py # 五层调仓控制器（CTA 手册阶段7，v2.104.0+18 首版 / +20 扩展）：缓冲带（核心/缓冲/不持仓区 + auto_buffer_k 三档）/混合触发（周期/边界/风控/移仓/强制）/换手阈值拦截（预期收益>成本×λ）/防僵尸（最大持仓天数+强制全量再平衡）/分批执行 + 多空不对称缓冲 k_long/k_short（trend/oscillation 自适应）+ plan_turnover_control 换手率超限自动调整，RebalanceController + RebalanceConfig/RebalanceState/RebalanceDecision
│   ├── permutation_test.py     # 因子有效性置换检验（CTA 手册阶段4/6/9，v2.104.0+18）：factor_ic_permutation_test 打乱标签 IC 双侧显著性 + portfolio_sharpe_permutation_test 合成得分组合夏普显著性，固定种子可复现/NaN 兜底
│   ├── shift_leak_test.py      # Shift 错位泄漏校验（CTA 手册阶段2，v2.104.0+20）：shift(-k) 错位构造下 IC 应归零（不泄漏时 |IC| 趋近 0），超阈值标记泄漏；已知 shift 错位为 CTA 因子回测头号隐性错误
│   ├── factor_document.py      # 因子文档化（CTA 手册阶段2，v2.104.0+20）：FactorDocument 生成因子定义/逻辑/参数/适用周期/经济逻辑说明，入库随因子元数据保存
│   ├── stress_ic.py            # 极端行情 IC 失效检验（CTA 手册阶段4，v2.104.0+20）：STRESS_PERIODS 内置（原油负价格2020-04~05/俄乌扰动2022-02~05/疫情冲击2020），stress_period_ic_test 对比常态/压力期 IC，压力期 IC 绝对值低于常态×0.5 或符号翻转 → failed
│   ├── regime_voting.py        # Regime 五指标投票检测器（CTA 手册阶段5，v2.104.0+20）：ADX/Hurst(R/S)/波动率分位数/趋势一致性比率/截面离散度 ≥3 票 majority → trend/oscillation/transition；RegimeVotingDetector 防抖 + 连续不稳复审(≥7日) + transition_position_scale 过渡降仓 + conditional_ic + regime_switch_benefit 动态vs固定对比
│   ├── futures_risk_events.py  # 期货专属风控事件（CTA 手册阶段8，v2.104.0+20）：盘中保证金占用≥70% 降仓指令 + 交易所提保/涨跌停熔断/主力切换异常事件捕获，与 RiskManager/portfolio_risk_controls 组合级风控衔接
│   ├── oos_checks.py           # 过拟合排查与绩效归因（CTA 手册阶段9，v2.104.0+20）：performance_decay_check 训练/验证夏普衰减≤30% + period_consistency_check 2015-2018/2019-2022/2023-2026 三段净值一致性 + annual_returns/sector_returns_contribution 分年度/分板块报告指标
│   ├── factor_lifecycle.py     # 因子生命周期管理（CTA 手册阶段11.3，v2.104.0+20）：factor_lifecycle_review 滚动60日 IC 均值较样本外训练期衰减>30% 或 IR 跌破 0.30 → 归零权重进入复审队列；样本不足保守保活
│   ├── qa/                     # 因子质检工作流程（CTA 手册第六章 v1.3，v2.104.0+21）：四段闭环质检 SOP，对照 cta_factor_system/qa/ 架构
│   │   ├── pre_entry.py        # 入库前质检 Q1-Q10 统一执行器（手册 6.2）：Q1-Q3 一票否决（未来函数/文档化/参数网格），Q4-Q10 评分项（IC/IR门槛/分层/置换/极端行情/敏感度/板块），输出结论与报告
│   │   ├── admission.py        # 三级准入分类（手册 6.3）：核心库(得分≥4,权重≤30%)/候选库(3~4,≤15%)/淘汰，IR 未达分类门槛直接淘汰
│   │   ├── report_template.py  # 9 部分《因子质检报告》生成（手册 6.4）：基本信息/经济逻辑/参数遍历/IC-IR/分层/板块/Regime条件IC/过拟合/准入结论
│   │   ├── monthly_check.py    # 月度滚动复检 M1-M5（手册 6.5）：60日IC均值/60日IR/IC衰减/当月分层/因子-持仓一致性；处置路径 1项降权50%/2项30%/3项归零/连续3月退役
│   │   ├── quarterly_check.py  # 季度全量复检 F1-F6（手册 6.6）：全样本IC-IR重算/分层重测/参数最优性/相关矩阵/Regime条件IC/板块拆解
│   │   ├── semi_annual.py      # 半年度深度复检 D1-D4（手册 6.6）：经济学逻辑复审/全样本回测重跑/品种池重构评估/淘汰库复审
│   │   ├── retirement.py       # 退役判定 5 条红线（手册 6.7）：连续3月预警/60日IC较入库时降>50%/IR<0.15/逻辑失效/数据源中断，输出退役流程
│   │   └── status_board.py     # 7 状态机+质检看板（手册 6.8）：DRAFT/PENDING_QA/CORE/CANDIDATE/OBSERVATION/SUSPENDED/RETIRED 流转合法性 + 看板统计；apply_status_transition 封装 FactorStatusRepository 落库（history+status），存量 'active' 兼容映射 CORE
│   ├── stress_test.py          # 压力测试
│   ├── ablation.py             # 输入敏感性消融实验（Phase A 逻辑审查；v2.50.0 判定语义：shuffle_dates/成交量/VWAP 消融与核心价格列置零为信息型不拦截，仅非价格列置零 IC 降幅>50% 判伪相关）
│   ├── shap_analyzer.py        # SHAP 局部可解释性分析（Phase B 逻辑审查）
│   ├── robustness.py           # 鲁棒性审查（Phase B 逻辑审查）
│   ├── causal_validator.py     # 因果结构审查（Phase C 逻辑审查）
│   ├── audit.py                # 因子审计（FactorAuditor + FailureClassifier 集成）
│   ├── high_ic_screener.py     # 高IC筛查剔除（B.4）：16项检查×6模块，5项一票否决，A/B/C/PASS评级
│   ├── failure_classifier.py   # 失败模式分类器（10 种失败模式 + 改善建议）
│   ├── factor_lineage.py       # 因子血缘追踪（谱系/趋势/退化检测/批量审计）
│   ├── factor_inspector.py     # 定时巡检（自动检测退化因子并降级）+ FactorReviewWorkflow 审查工作流（GAP-I102：pending→approved/rejected 状态机 + factor_reviews 表意见回写 + CLI factor review 队列 + C8 2026-08-11 Web 人审工作台复用 + C8-2 机审/人审可配置：ReviewMode（FTS_REVIEW_MODE 默认 auto）+ AutoReviewPolicy 三态判定（正常批准/低质驳回/异常转人审）+ auto_review 批量机审 reviewer=auto）
│   ├── monitor.py              # 循环监控
│   ├── factor_quality_card.py  # 因子质量评分卡（10 维评分，A/B/C 分级准入）
│   ├── adaptive_weight.py      # 自适应权重（AdaptiveWeightManager + RegimeSmoother 热更新）
│   ├── feature_ops.py          # 特征算子注册表（50 算子 / 7 类）
│   ├── numba_kernels.py        # numba 算子内核（plans/38，v2.104.0+59 回退后仅保留 ts_rank 1D/2D 内核；ts_cvar/ts_zscore 已回退现值实现；依赖缺失/FTS_OPS_NUMBA=false 时经 ops_numba 开关回退现值实现零漂移）
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
│   ├── feedback_loop.py        # 反馈闭环（C.3）：Trigger/归因/方向调整/效果评估（C6 2026-08-11：LiveVsBacktestICReport 接入重校准触发源）
│   ├── black_litterman.py      # Black-Litterman 观点融合（C3 2026-08-11）：implied_returns 逆优化隐含收益 + black_litterman_weights 闭式后验（零观点退化=先验）+ build_auto_views（IC→观点尺度化）+ 约束投影；portfolio_loop synthesize_signals optimizer 分支 mode_internal="bl"（失败回退 risk_parity）
│   ├── recalibration.py        # 在线重校准队列（C6 2026-08-11）：RecalibrationQueue 状态机（pending/processing/done/skipped/failed）+ JSON 幂等落盘 + recalibrate_factor（复用 optimize_params_staged 两阶段漏斗）+ process_recalibration_queue（elite JSON/DuckDB 元数据回写）；CLI fts factor recalibrate list/run
│   ├── experiment_log.py       # 结构化实验日志（Phase 2 P1-2/26 计划 §7，2026-08-11）：ExperimentLogWriter 聚合 run 内候选（预筛拦截/失败/晋升全结局）→ data/experiments-{run_id}.json（run_id 幂等覆盖，按 generation+parent_id 分组 rounds + summary by_method，轻量契约校验非法 warning 不阻断）；evolution_loop run() finally 自动导出
│   ├── success_pattern.py      # 成功模式定向演化（Phase 1.2 P0-1/26 计划 §6，2026-08-11）：analyze_success_patterns 滚动窗口+时间衰减聚合近期成功模式（by_method 晋升率/算子/窗口分箱，排除 style_tags）→ format_report_for_llm prompt soft 偏向（样本 <min_sample 空报告不注入）
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
│   ├── http_server.py          # HTTP 监控端点（/metrics 含 Prometheus 指标、/api/v1/*；C8 2026-08-11 人审工作台：GET /review + /api/review/pending|history + POST /api/review/approve|reject 复用 FactorReviewWorkflow，纯标准库内联样式；C8-2 机审端点：pending 返回 mode+needs_human 标注、POST /api/review/auto 批量机审；2026-08-14 WorkFlow UI：GET /workflow 托管 web/workflow_ui 构建产物 + /api/workflow/stages|runs|qa/board + POST /api/workflow/runs|run_all|stage/action/run）
│   ├── prometheus_metrics.py   # Prometheus 指标注册表（衰减/Regime/权重/质量/Live/风控/反馈）
│   ├── elite_tracker.py        # Elite 因子追踪
│   ├── reaudit.py              # 新标准准入复审（2026-08-13）：run_reaudit（复用演化准入链 横截面评估→Verifier→审计→鲁棒性→评分卡）+ apply_reaudit_results（retain/shadow/retire 回写 + status_history 留痕）；月度任务 monthly_decay_eval_job Step A + 手动 CLI 共用
│   ├── data_quality_monitor.py # 数据质量监控（B.1）：完整性/准确性/及时性三维指标
│   ├── data_level_monitor.py   # 数据级质量监控（GAP-F06，v2.60.0）：缺失率/异常值/复权一致性/多源分歧
│   ├── data_level_monitor.py   # 数据级质量监控（GAP-F06，v2.60.0）：缺失率/异常值/复权一致性/多源分歧
│   ├── live_factor_monitor.py  # Live 因子偏离监控（C.2，v2.77.0）：30% 偏离阈值 + ingest_live_ic 实盘反馈数据源接入 + 衰减告警（GAP-I402）
│   ├── logic_monitor.py        # 逻辑监控仪表盘（Phase C；v2.104.0+72 极端检测双口径：连续信号 z-score / 离散信号主导档位退化，修复离散突破因子系统性误报）
│   ├── k8s_deploy.py          # K8s 部署配置
│   └── prometheus_setup.py     # Prometheus 指标配置
├── ml/                         # ML 模型层（v2.38.0）
│   ├── __init__.py             # 包入口
│   ├── models.py               # ML 模型封装（LightGBM/XGBoost/Ensemble，可选依赖）
│   └── trainer.py              # 训练管线（横截面回归/时序预测/集成融合三种模式）
├── bridge/                     # 信号桥接层（v2.38.0）
│   ├── __init__.py             # 包入口
│   └── signal_bridge.py        # SignalBridge 信号格式转换（JSON/Redis/REST 协议）
├── store/                      # 数据持久化访问层（plans/29 Phase 0，2026-08-11，GAP-090）
│   ├── __init__.py             # 导出 StorageBackend/StorageDomain/StorageRegistry/load_storage_landscape/StateKVStore/DEFAULT_STATE_DB
│   ├── registry.py             # 存储域注册表：storage_landscape.yaml 契约加载（FTS_STORAGE_LANDSCAPE_PATH env 覆盖，13 域）+ StorageDomain 契约（domain/description/backend/path/tables/partition_key/retention/status/migrated_from·to）+ validate_contract（必填字段/后端枚举/相对路径/状态合法/legacy·planned 迁移血缘方向）；SSOT 单一事实源第一步，零存量数据变更
│   └── state_db.py             # 运行状态 KV 存储（plans/29 Phase 2，2026-08-11；E.3 S2 后端切换 SQLite WAL 2026-08-13）：StateKVStore 双表（state_kv 当前状态表 namespace+key 复合主键 UPSERT + state_history 历史追加表 seq AUTOINCREMENT 自增可回放），value JSON 序列化（TEXT 存储），upsert/get/get_all/snapshot（全量 dump 供无 state.json 冷启动）/history；upsert 单事务包裹双表原子、WAL 下写连接存活不阻塞外部读、seq 单调；默认库 data/state.db（L4 运行状态库，SQLite，零新增依赖）
├── workflow/                   # CTA 手册 WorkFlow 端到端工作流（2026-08-14，v2.104.0+25）
│   ├── stages.py               # 11 阶段 + 质检闭环定义（StageAction/Stage，动作↔CLI 命令映射，{factor_id}/{report_dir} 动态占位符）
│   ├── executor.py             # WorkflowExecutor：单阶段（后台线程 subprocess）与端到端执行（顺序推进、失败停止、超时熔断），JSON 产物解析，批次状态按 stage_runs 汇总同步
│   └── store.py                # WorkflowStore：SQLite WAL 持久化（workflow_runs + stage_runs 双表，崩溃可回放）
│   └── __init__.py             # 导出 get_stages/WorkflowExecutor/WorkflowStore
├── risk/                       # 风控层（C.2）
│   ├── __init__.py             # 导出 RiskManager/TradeAdapter/SimulatedTradeAdapter
│   ├── risk_manager.py         # RiskManager 五项风控规则（仓位/回撤/亏损/杠杆/集中度）
│   ├── portfolio_metrics.py    # 组合级风控指标（D.2 §3，2026-08-11）：杠杆仓位/波动尾部(VaR-CVaR)/集中度/损益/流动性/执行六维度 + evaluate_metrics 三级预警（WARN/BLOCK/FORCE_CLOSE）；空数据降级不抛错
│   ├── trade_adapter.py        # TradeAdapter 抽象基类（Liskov 替换）
│   └── simulated_adapter.py    # SimulatedTradeAdapter 模拟成交
└── scheduler/                  # 调度层
    ├── __init__.py             # 模块入口 + 导出
    ├── engine.py               # SchedulerEngine（APScheduler 包装器）
    ├── tasks.py                # TaskRegistry + TaskSpec + 注册默认任务（10 个）
    ├── jobs.py                 # 任务工作函数（L1/L2/L3/信号管道/健康检查/因子巡检/月度治理[衰减+新标准重审]/数据质量/数据级监控）
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
    ├─→ executor.py  执行器向量化计算: pandas Series 快速路径 (复用 feature_ops 81 算子)
    └─→ compiler.py  编译器生成确定性沙箱 code → runtime.py 桥接 (eval_fts_expr)
```

### 算子演化引擎（Phase 3+ / C.4）

在 DSL 算子空间做**适应度导向的进化式搜索**，取代 `_generate_operator_factor` 的纯随机组合。`fts/factor_engine/operator_evolution.py` 提供 `OperatorEvolutionEngine`：种群初始化（validator 校验通过）→ 适应度评估（DSL executor → IC/Sharpe）→ 锦标赛选择 → 子树交叉/变异（ExprNode 层面，参数受 `param_bounds` 约束）→ 精英保留，多代迭代后取最优表达式经 `create_operator_factor` 产出 `kind=OPERATOR` 因子。设计文档见 [C.4-operator-evolution-engine-design.md](design/C.4-operator-evolution-engine-design.md)。GAP-026（GP 算子命名与 DSL 未对齐）随本引擎落地关闭。

**多样性护栏（GAP-074，v2.100.0）**：① 父因子 UCT 失败反馈——`_select_parent_uct` 的 UCT 统计在演化失败/运行时校验失败/快速预筛失败三条 continue 路径上同步更新（`_update_uct_failure`，visits+1 无正奖励），避免失败父因子 `visits` 恒 0 导致永远选中 `parents[0]` 的选择坍缩；② 算子演化种子注入代际——`_try_operator_engine_evolution` 随机种子由 `md5(factor_id)` 改为 `md5(factor_id::generation)`，同父因子不同代产生不同搜索轨迹，消除 50 代重复执行同一确定性子任务的空转。

---

## 4. 数据流

### 全局数据流

```
DuckDB kline_cache (期货, data/fts_history.duckdb)     AKShare / 通达信 / TQSDK（降级源）
    │                                                       │
    │ OHLCV 日线 (期货连续合约, 复权)                        │ 日线/分钟 即时获取
    │                                                       │
    ▼                                                       ▼
FTS (因子推演) — 支持期货横截面因子演化
    │
    ├── 因子引擎 → 策略组建 → 信号合成
    │       │
    │       ├── equal_weight（默认，等权 1/N；enable_pca=True 时以 Step 1.9 P2 PCA 载荷×解释方差权重替换均匀等权，v2.103.0+24）
    │       ├── elastic_net（Elastic Net 截面回归；因子代码经 _build_factor_code_map 加载：内存 code 优先 → DuckDB 补拉 → JSON 快照兜底，v2.103.0+5）
    │       ├── sharpe_weight（按 Sharpe 归一化加权）
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
│ scripts/futures_signal_pipeline.py（横截面信号管道，v2.105.0 起因子  │
│ 选择与基础权重由 L3 组合提供 factor_weights.json，信号管道仅做信号   │
│ 计算 + Regime 档位缩放权重调整，移除截面 IC 方向校正与 Ridge 回归；  │
│ Market Regime 检测 = RegimeAwareSelector，品种-链对齐度修正 =        │
│ compute_alignment；因子来源 = L3 组合因子（DuckDB factor_catalog）； │
│ v2.104.0+69 增量跨因子组合校验：快照含 factor_signature（因子名集合 │
│ 签名），增量仅在前后因子组合一致时计算，组合变更标记无效防虚假增量；│
│ 综合得分语义 = 品种级 IC 翻转后的相对强弱评分（负分=回归预期非方向））│
│    │                                                                │
│    ▼                                                                │
│ reports/{date}/futures_signals_{date}.md                            │
└─────────────────────────────────────────────────────────────────────┘

┌─ 换月复权与展期仿真（v2.58.0，GAP-046） ─────────────────────────┐
│ contract_kline（具体合约日线，sync_tq_contract_kline.py 同步：TQ 主源 + AKShare 降级，全合约含非主力）│
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

┌─ DuckDB 并发模型（v2.86.0，GAP-056，design/E.1；v2.101.0 分库扩展；v2.103.0 E.4 S1 连接生命周期根治） ─┐
│ 写窗口短生命周期 + filelock 跨进程互斥 + 读路径 read_only（E.4 S1，2026-08-13）      │
│    │                                                                                │
│    │ _write_scope() → filelock（fts/store/duckdb_lock.py，data/.locks/*.lock）       │
│    │   + 短生命周期写连接（写完即关，秒级；演化/同步进程其余时间零写连接）            │
│    │   → execute / executemany / copy_from_records（BEGIN/COMMIT 包裹，批量原子）    │
│    │   → 写路径：_write_contract_kline / _write_cache / repository 4 类               │
│    ▼                                                                                │
│ _open_read_conn() / DuckDBReader → read_only=True 短连接（读完成即关）               │
│    │   → _from_kline_cache / get_dominant_contracts 等读路径                        │
│    │   → MVCC 快照：写提交期间读侧不阻塞（lock_configuration=true 读写共存）        │
│    ▼                                                                                │
│ 因子目录分库（v2.101.0，GAP-056 扩展，design/E.1 §2.4）：                            │
│   get_db_path(market) 路由 → factor_catalog_futures.duckdb（futures+                │
│   multi）；物理隔离消除跨市场文件锁竞争（factor_catalog_stock.duckdb                 │
│   已随股票管线剥离至 fts-stock）；行情数据（kline_cache）仍单库                      │
│   统一存于 data/fts_history.duckdb                                                    │
│ 兼容层：repository _get_conn 补 lock_configuration=true（旧版静默降级）；             │
│ retry_on_conflict/AsyncWriteQueue 保留为防御兜底；duckdb_single_writer=false 回退旧  │
│ 多路径；_get_db()/DuckDBReader 读路径 read_only=True（E.4 S1 起）；_get_writer 已     │
│ 降级为一次性短连接 deprecated（旧脚本兼容）                                           │
└──────────────────────────────────────────────────────────────────────────────────────┘

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

┌─ 能源产业链专属工作流（v2.104.0+33，GAP-121） ──────────────────┐
│ 独立于通用期货工作流：链专属训练链 + 链外盲测池 + 独立存储路由      │
│ 链配置（SSOT: config/futures_universe.yaml，v2.104.0+38；           │
│   fts/data_futures.py 内置默认兜底，缺失/损坏/校验失败回退并告警）： │
│   ENERGY_CHAIN_SYMBOLS=12 化工链品种（四大子链各 3：能源 SC/FU/BU│
│     聚酯 PX/TA/PF、油化工 L/PP/PG、煤化工 MA/UR/SA；v2.104.0+37  │
│     扩池降训练池内相关性）                                         │
│   ENERGY_CHAIN_TRAIN=12 全训；ENERGY_CHAIN_HOLDOUT=其余化工链 8   │
│     品种（化工链成员 − 训练 12，链外泛化盲测，随配置自动派生）     │
│   ENERGY_CHAIN_MARKET="energy"（因子库路由标记）                    │
│ 全量品种池 FUTURES_SUBSET/CORE/HOLDOUT/STRATIFIED 与 17 产业链     │
│   分类同源 config/futures_universe.yaml（"炼化聚酯链"由训练池自动   │
│   生成置首位，与 ENERGY_CHAIN_SYMBOLS 对齐）                       │
│ 存储路由：get_db_path("energy")→factor_catalog_energy.duckdb；      │
│   get_elite_dir("energy")→energy_chain_elite（memory/evolution/     │
│   energy_chain）；通用路径行为不变                                   │
│ 入口（三处 CLI 均支持 --chain energy / --symbols 显式列表）：        │
│   ① fts evolution run --chain energy   → 演化落能源库（market=energy│
│      保持期货验证配置，板块中性化不注入——链内相对信号）             │
│   ② verify_qa_workflow.py --chain energy → 12 品种全链质检           │
│   ③ futures_signal_pipeline.py --chain energy → 因子源切能源库、    │
│      面板=12训练+8盲测、链外盲测 IC 对比、链内综合得分，             │
│      输出 reports/energy_chain/{date}/（独立于 reports/futures/）；   │
│      因子选择与基础权重由链级 L3 组合决定（memory/portfolio/energy/   │
│      factor_weights.json，v2.104.0+39 起 active，4 因子：             │
│      fut_cross_carry/fut_bias/fut_trend_strength/                    │
│      eng_chain_linkage_momentum），按名从能源库加载定义（严格模式）， │
│      不再全量精英因子等权（v2.104.0+41 切换，与通用模式对齐）        │
│ FUTURES_SECTOR_MAP 新增"炼化聚酯链"分组（置于首位）：通用中性化反向 │
│   映射{后序覆盖前序}下 12 品种仍归属 能源/油化工/聚酯链，通用语义不变│
│ 训练链/盲测池再优化（v2.104.0+34，A+B+C）：                        │
│   A 数据补全 scripts/sync_energy_chain_depth.py（AKShare 全历史→  │
│     kline_cache，先删旧缓存防 SYNTHETIC 污染）——LU0 1492 行        │
│     (2020-06)/PR0 473 行(2024-08)/PL0 260 行，共同窗口 120→473 行 │
│   B 盲测池化工链分层：信号管道 --chain energy 输出聚酯链/油化工/   │
│     煤化工 分层平均 IC + 有效占比（控制台+报告双路）               │
│   C 品种阈值 ENERGY_CHAIN_MIN_TRAIN_ROWS=300 + check_energy_chain_ │
│     depth() 审计（排除 SYNTHETIC）；补全后训练链 12 品种全达标零降级│
│ 训练池扩池降相关性（v2.104.0+37）：训练链 9→12（能源3 SC/FU/BU +  │
│   聚酯3 PX/TA/PF + 油化工3 L/PP/PG + 煤化工3 MA/UR/SA，覆盖四大   │
│   化工子链）；LU/PR 与 FU/PF 高相关换出至盲测池；盲测池自动重算为 │
│   8 品种（BZ/EB/EG/FG/PL/PR/SH/V）；链知识/感知品种/ec_* 种子同步 │
│ L1 知识输入双线混入（v2.104.0+35）：energy 模式一次运行注入        │
│   ① 通用期货市场知识+因子：seeds/futures 种子 + 提取器管道          │
│      （天软/研报/论文/宏观）                                        │
│   ② 能化专属市场知识+因子：seeds/energy 专属种子（eng_*/ec_*，      │
│      裂解价差/聚酯加工差/库存基差/链内联动/季节性开工）+ LLM        │
│      bootstrap prompt 注入 chain_knowledge（12 训练品种/品种链条    │
│      位置/盲测池/能化机制设计要求）；默认感知 12 能化品种；          │
│      输出落 factor_pool_energy.json / l1_injected_energy/ 独立隔离  │
└─────────────────────────────────────────────────────────────────────┘

┌─ 多持有期 IC 体系（v2.90.0，GAP-060） ─────────────────────────┐
│ fts/factor_engine/horizon_analysis.py（时序，单标的）              │
│    │  compute_multi_horizon_ic(signal, close, horizons=(1,5,10,20))│
│    │    → 各持有期 IC/ICIR/胜率 + best_horizon + decay_curve       │
│    ▼                                                               │
│ evaluation_chain.cross_section_evaluate_backtest（横截面，期货） │
│    │  compute_cs_multi_horizon_ic(oos_signal, panel, symbols, ...) │
│    │    → 各持有期 h 日前向收益矩阵 → 逐期截面 Spearman IC 聚合     │
│    │    → 输出 multi_horizon（ic_by_horizon/icir_by_horizon/       │
│    │       win_rate_by_horizon/best_horizon/decay_curve/           │
│    │       monotonic_decay）                                       │
│    │  配置: FTSConfig.eval_horizons（默认 "1,5,10,20" 启用，        │
│    │         FTS_EVAL_HORIZONS 环境变量覆盖，空=关闭）              │
│    │  用途: 最优调仓频率数据化（best_horizon）、因子有效期诊断     │
└─────────────────────────────────────────────────────────────────────┘

┌─ 质检/组合补全链（v2.97.0 GAP-061/063~067；v2.101.0 GAP-068/069） ─┐
│ ① 可交易性压力层（GAP-061，cost_sensitivity.py）                  │
│    run_slippage_stress(signal, close, mults=(1,2,4,8))            │
│      → 复用 TransactionCostModel 扫描滑点/手续费倍数               │
│      → 净夏普/盈亏平衡倍数/positive_at_max_stress                  │
│      → evaluation_chain.evaluate_backtest 接线 cost_sensitivity    │
│        （FTSConfig.cost_sensitivity_enabled 默认关）               │
│ ② 组合质检三标准（GAP-063，portfolio_loop.build_combo）            │
│    qc_standards: synthesis_gain（组合/最佳单因子夏普）              │
│      + diversification_gain（组合/权重加权夏普）                    │
│      + drawdown_control_ratio（组合/成分因子均值回撤，cumprod 实测）│
│      → PortfolioCombo.qc_standards 契约                            │
│ ②b 双指标夏普（方案③，portfolio_loop.build_combo，v2.104.0+2）      │
│    signal_sharpe（缩放前信号质量夏普）：exposure_scale 仓位缩放前      │
│      权重口径（Regime 降仓 × G1 同向敞口压缩前）                      │
│    combo_sharpe（风控后净暴露夏普）：缩放后权重口径（原有字段）        │
│    measured 口径下 portfolio_returns 内部归一化，两者相等             │
│    → PortfolioCombo.signal_sharpe 契约（estimated 差异可解释）       │
│ ②b2 L3 Verifier 判定口径（GAP-122，portfolio_loop Step 6，           │
│      v2.104.0+42）：min_sharpe/max_sharpe 判定信号质量，经            │
│      _verifier_view 用缩放前 signal_sharpe 替换风控后 combo_sharpe    │
│      （Regime 降仓 × G1 敞口压缩为暴露决策，乘性压低 combo_sharpe，   │
│      原始口径致风控一启用即恒不达 min_sharpe=2.0——期货/能源 L3 长期  │
│      verifier_warning 根因）；相关性/换手/衰减/因子数维度不变；       │
│      config/settings.yaml verifier.min_sharpe=1.9（信号质量下限，       │
│      原始 2.0 已由 SHARPE_CAP=2.0 截断的因子等权合成贴线）、           │
│      max_turnover=12.0（对齐因子月度换手次/月量纲，原 0.5 为比例量纲   │
│      与库内 4.48~17.40 次/月不匹配）                                  │
│ ②b2a signal_sharpe raw 口径（v2.104.0+73，portfolio_loop                │
│      build_combo）：signal_sharpe 的 pre_weighted_sharpe 改用截断前      │
│      原始 Sharpe（s._sharpe_raw，quality_weight 分支透传）而非          │
│      SHARPE_CAP=2.0 截断值——截断口径下全部因子 sharpe 压平为 2.0，       │
│      signal_sharpe 恒 = 2.0 × diversity（< 2.0），1.9 阈值在             │
│      quality_weight 小样本下几乎不可达（连续 verifier_warning 根因），  │
│      raw 口径恢复"真实信号质量"语义；权重计算/展示仍用截断值（防         │
│      过拟合信息不丢失），缺 _sharpe_raw 时回退截断值（向后兼容）；      │
│      max_sharpe 上限 3.5→12.0（raw 口径信号质量上界由 2.0 提升，           │
│      3.5 会恒触发过拟合警告；8.0 实测仍被 8.64 触及，12.0 过滤异常虚高，    │
│      配随机化测试兜底）                                                    │
│ ②b3 G1 参数配置化（v2.104.0+X，portfolio_risk_controls                │
│      AlignedExposureConfig + portfolio_loop.PortfolioLoop）            │
│      config/settings.yaml l3_g1_enabled/align_threshold/               │
│      max_compress/compress_curve（或 FTS_L3_G1_* 环境变量）            │
│      → FTSConfig → PortfolioLoop.__init__ 构建 self._g1_config        │
│      → build_combo aligned_exposure_config 乘性合并                    │
│      （exposure_final = 置信度缩放 × G1 aligned_scale）                │
│      默认 0.60/0.50/linear 与历史硬编码一致，零行为变更；              │
│      AlignedExposureConfig.__post_init__ 契约校验非法值快速失败        │
│ ②b4 plans/36 因子选择综合评分改进（v2.104.0+50）：                     │
│    ① 选入标准综合评分化——L2 截断排序与 L3 聚类代表排序由单一           │
│      Sharpe 改为 _factor_composite_score（portfolio_loop.py）           │
│      维度：sharpe_cap 0.30 + icir 0.30 + ic 0.20 + turnover_inv 0.20   │
│      （percentile rank 截面归一化，缺失维度剔除后权重重归一），          │
│      权重可配 config/settings.yaml l3.factor_score.weights             │
│    ② quality_weight 定权模式——synthesize_signals 新增合成模式          │
│      （综合评分定权 + 0.5×等权下限 max(w, 0.5/n) 归一化，防单因子      │
│      权重塌缩），CLI --synthesis-mode quality_weight 可选              │
│    ③ 聚类阈值参数化 + 簇内 top-N——cluster_threshold 0.7 与            │
│      cluster_top_n（默认 1 保持现行为）可配（l3.cluster 段），          │
│      top-N>1 时与已选代表互相关<0.5 约束才允许多保留（补充因子         │
│      factor_clustering.select_representative_factors 新参数）；         │
│      阈值敏感性扫描脚本 scripts/l3_cluster_sensitivity.py             │
│      （0.60~0.80 五档 Jaccard 重合度报告）                             │
│    ④ 组合层滚动 OOS + 质量报告口径统一——build_combo 新增              │
│      rolling_oos（60 交易日滚动组合夏普 + decay_ratio，                │
│      FactorReturnsBuilder.align_to_factors 对齐，滚动窗口             │
│      不足首段 NaN 不参与衰减评估）；quality_report 增加 passed_gate    │
│      统计（abs(ic)≥0.03 且 sharpe≥1.5，与 Step 1a 加载口径一致）      │
│    → 契约：PortfolioCombo.synthesis_mode 增加 quality_weight、        │
│      新增 rolling_oos: Optional[dict] 字段                             │
│ ②c 实测化输入（方案①，_auto_build_factor_returns，v2.104.0+2）       │
│    --returns-matrix 手动 CSV 优先（CLI 传入 factor_returns）          │
│    自动构建默认关闭（env FTS_L3_AUTO_FACTOR_RETURNS=1 启用）：          │
│      面板+因子代码 → 横截面信号矩阵 + 5 日前向收益 → FactorReturnsBuilder│
│      实测 Sharpe 虚高 20.06（v2.104.0+2 验证，quantile=0.2 腿过小），  │
│      默认回退估算口径（metrics_source），自动构建质量问题登记 GAP-I306 │
│ ③ IC 协方差加权合成（GAP-064，weight_learning.ic_covariance_weights）│
│    w=(Σ+λI)⁻¹μ（Ledoit-Wolf 收缩 + 对角正则 + w/Σ|w| 归一化）       │
│    → synthesize_signals ic_matrix 参数 + ic_weight 模式             │
│      （样本<20/奇异回退 IC 均值加权 w∝|ic|）                        │
│ ④ 品种板块联动（GAP-065，sector_linkage.py）                       │
│    compute_sector_linkage: 板块内两两相关均值/最大                  │
│      + 跨板块相关 + 因子截面分散度 + high_linkage                   │
│      → factor_dispersion_by_sector                                 │
│ ⑤ 夜盘隔夜跳空（GAP-066，data_sources/overnight_gap.py）           │
│    compute_overnight_gap: open[t]/close[t-1]-1                      │
│      → data_futures.get_ohlcv 注入 overnight_gap/overnight_gap_flag │
│        （FTSConfig.inject_overnight_gap_enabled 默认关）            │
│ ⑥ 组合级风控（GAP-067，portfolio_risk_controls.py）                │
│    check_drawdown_stop（净值 cumprod 回撤 >10% 减仓建议）            │
│      + check_correlation_circuit_breaker（窗口 60 均值相关 >0.8）   │
│      → run_portfolio_risk_controls → PortfolioLoop Step 7.8         │
│        state["risk_alerts"]（异常降级不中断）                       │
│ ⑦ 会员持仓排名拥挤度（GAP-069，position_rank_crowding.py）         │
│    PositionRankProvider 协议 + AKSharePositionRankProvider          │
│      （品种前缀→dce/shfe/czce/cffex 路由，接口缺失/异常/行数不足     │
│        降级返回空，不阻断主流程）                                   │
│    compute_crowding: 前 N 净持仓集中度 CR + 多空比 + 净占比          │
│      → crowding_score（0.5·|CR|+0.3·min(|多空比-1|,1)+0.2·min(|净占比|,1)）│
│      → position_rank_crowding_signal（低拥挤+1/高拥挤-1/中间 0）    │
│ ⑧ 多频信号叠加与冲突消解（GAP-068，multi_frequency.py）            │
│    build_minute_signal（日内动量）→ aggregate_minute（last/mean/max/min）│
│      → blend_signals（日频权重 daily_weight 与分钟权重 1-w 均分叠加）│
│      → resolve_conflict（weighted/penalty/discard 三规则）          │
│    compute_multi_frequency_signal 统一入口复用 get_minute_ohlcv     │
│      （minute_cache→TDX 17709→TQ-Local→TQSDK 4 级降级链）           │
│    backtest_minute_signal（T+1 日频持有，滑点+手续费双向）          │
│ ⑨ 跨标的稳健性检查（GAP-075，v2.101.0 收尾）                       │
│    cross_section_evaluate_backtest 输出 symbol_ic（逐标的时序 IC）  │
│      + symbol_holdout（行业分层留出 20% 验证，train 定方向/留出集  │
│        IC 保持率）→ _run_factor_audit 传 symbol_ic_map 激活审计    │
│      cross_symbol（≥80% 标的 IC 为正，或二项检验显著/平均 IC      │
│        达阈值且符号比例≥下限，A+C 双机制 OR 判定，v2.103.0）        │
│        + 新增审计项 symbol_holdout                                  │
│      （留出集 IC > 0 且保持率 ≥ 阈值）；数据缺失→skipped           │
└─────────────────────────────────────────────────────────────────────┘

┌─ 实盘执行链路 + 样本外强制 + 保证金建模（v2.60.0，GAP-F01/F08/F09）─┐
│ ① 样本外强制（GAP-F08）:                                             │
│   EvolutionLoop 晋升路径 → WalkForwardOptimizer 冷启动多窗口验证     │
│     → force_walkforward=true 强制（可配置跳过并记录原因）            │
│     → 多窗口 OOS IC 一致性替代 L1 单段 ICIR 近似，审计 oos_consistency│
│     → v2.104.0+44（GAP-121 评估链修复）: 横截面分支接入              │
│       cross_section_walk_forward（短样本 _build_wf_config 自适应 ≥2  │
│       窗口）；晋升 WalkForward 强制门（n_windows<2 拒绝晋升）+ 审计   │
│       oos_consistency 缺失/窗口<2 硬拦截（反转 GAP-073 skipped 放行） │
│ ①.5 L2 质检性能（GAP-071，v2.98.2）:                                  │
│   双重 WalkForward 合并——审计 `_run_factor_audit` 优先复用三级评估链 │
│   走航结果（evaluation["walk_forward"]，配置同源 `_build_wf_config`），│
│   缺失/窗口=0 时兜底独立计算；评估链走航窗口 IC 修正为 oos 段内自算    │
│   fwd（与审计同口径）且每窗口仅执行 oos 信号；质检信号缓存            │
│   `signal_cache.SignalCache`（LRU，key=factor_id+params+数据全列值指纹│
│   ）接入 `FactorExecutor`，三级评估/极值扰动/消融/鲁棒性/SHAP 共享，   │
│   完整数据信号全链命中，消除重复沙箱执行                             │
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
│     ├─ gateway.py       AbstractGateway + SimulatedGateway           │
│     │                   （下单重试/超时兜底/状态回查；D.2 限价单/      │
│     │                    部分成交 PARTIAL/集合竞价 auction_open）     │
│     ├─ contracts.py     Sim* 契约 + 合约乘数表 + 市场推断（D.1）      │
│     ├─ simulated_portfolio.py                                       │
│     │    SimulatedPortfolio 模拟仓：apply_signal（干预→风控→         │
│     │    reconcile 加/减/平/反手）→ mark_to_market（期货保证金+       │
│     │    盯市）→ attribute_factor_returns（权重×下期              │
│     │    收益 → LiveFeedbackRecord 归因）→ portfolio_risk_status     │
│     │    （D.2 组合级风控：6 维度指标 + WARN/BLOCK/FORCE 三级预警）   │
│     │    + set_book_provider（D.2 盘口撮合注入，无盘口降级 bps）      │
│     ├─ book.py          OrderBookSnapshot/BookLevel 盘口契约 +       │
│     │                   build_book_from_ticks（五档聚合/排序/截断）   │
│     ├─ matching.py      OrderBookMatchingEngine 市价逐档撮合：        │
│     │                   加权均价、深度不足部分成交、滑点自然产生、     │
│     │                   无盘口/异常降级 bps（D.2 §4）                 │
│     └─ simulated_engine.py   SimulatedReplayEngine（t+1 开盘成交/     │
│                              逐日盯市/无未来）+ SimulatedPaperTrader  │
│                              （实时纸面，SQLite 持久化）（D.1）       │
│     ├─ sqlite_store.py       SimSQLiteStore（D.1）：模拟仓账户/持仓/  │
│                              成交/权益四表 SQLite 持久化（WAL+事务，  │
│                              替代 paper_state.json，std 库零依赖）    │
│     ├─ simulation_gap.py     仿真 vs 回测净值偏差对比（CTA 手册阶段10│
│                              v2.104.0+20）：simulation_backtest_gap_ │
│                              check 归一化首日净值后比对重叠期偏差，   │
│                              max_gap ≤ ±5% 判定通过，重叠不足不判通过 │
│     └─ capital_ramp.py       资金三级爬坡（CTA 手册阶段11.1，        │
│                              v2.104.0+20）：10%小仓(30天)→50%半仓(连 │
│                              续月度稳定)→100%全额；can_advance 升级  │
│                              判定 + ramp_status 状态汇总，杜绝一次性 │
│                              满仓上线                               │
└─────────────────────────────────────────────────────────────────────┘

┌─ 分钟级数据路径（v2.87.0+） ──────────────────────────────────────────┐
│ 三源分钟数据获取:                                                      │
│   通达信本地 HTTP (17709) — 正序，day/1m/5m/15m/30m/60m（统一源）     │
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

┌─ 宏观字段增强层（v2.32.0+；v2.101.0 默认源切 EastmoneyMacroSource；v2.103.0 注入端闭环）─┐
│ EastmoneyMacroSource（东财 RPT_ECONOMY_CPI/CUSTOMS + akshare 中债登   │
│   1 年期/美债 10 年期）→ edb_cache (indicator/date/value 七列)        │
│    │（iFinD EDB 需 API Key 实测不可用，显式传 source 可切回）          │
│    │ get_macro_series()（缓存查 → miss 拉取 → 幂等写回）              │
│    ▼                                                                  │
│ MacroFieldAligner.align()（月度→交易日 ffill + 发布滞后防未来函数）    │
│    │                                                                  │
│    ▼ 注入为 K 线 DataFrame 列（export/import_data/cpi/rate/us_bond）  │
│ 注入端三条消费链（GAP-088 v2.103.0 注入端闭环）:                       │
│ ① BacktestPipeline._compute_factor() → _execute_factor_code()         │
│ ② futures_signal_pipeline._inject_macro_to_panel（--no-macro-injection 可关）│
│ ③ cli._prepare_futures_data（横截面演化面板，失败降级不阻断）          │
│    → 宏观因子 data.get('export') 读取真实宏观数据（不再走 close 代理） │
└───────────────────────────────────────────────────────────────────────┘
```

**宏观因子角色边界（v2.33.0）**：
- 宏观因子**禁止**进入单品种时序回测/信号管道。真实 EDB 数据对比证实
  fut_macro_export 类因子在单品种（RB0）时序上 IC≈0（历史 Sharpe 7.68 为
  close 代理假象），v2.33.0 已全部 retire。
- 宏观数据注入层保留，仅作为**跨品种/板块层面**数据供给：
  ① SectorRegimeSelector 产业链 regime 选择；② 组合风险预算归因。

**common_dates 语义（v1.7.1）**：
- `get_futures_panel()` 返回的 `common_dates` 由「全品种日期交集」改为「多数对齐」：
  取至少 `max(2, 品种数//2)` 个品种共有的日期。
- `get_csi300_panel()`（股票横截面，GAP-XXX，v2.103.0）`common_dates` 覆盖率阈值对齐实现已随股票管线剥离至 fts-stock（2026-08）。
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
- L3 `run()` Step 0.5 期货分支自动构建市场级合成 OHLCV（v2.98.1，方案 B）：未显式传 `market_ohlcv` 时，用 `SectorRegimeSelector._build_sector_ohlcv`（全品种 close 截面均值 + volume 截面和）构建市场级合成 OHLCV，激活 Step 2.5 Regime 自适应权重调整（此前定时任务/CLI 期货路径因 `market_ohlcv=None` 恒跳过）；数据不足/构建异常降级置 None 保持原跳过路径；仅期货路径生效（股票路径已随股票管线剥离至 fts-stock）。
- Regime 机构级优化链路（plans/28，2026-08-11）：`regime_hmm.MultiHorizonHMMDetector`（多周期 HMM 后验概率输出 regime_probs，28-T2）→ `regime_calibration.RegimeConfidenceCalibrator`（置信度熵标定 exposure_scale 仓位缩放：`scaled = confidence × (1 − entropy_penalty × H_norm)`，28-T4，`_compute_exposure_scale` 在 Step 2.5 计算、build_combo 消费）→ `regime_model_selection.BICStateSelector`（BIC 状态数选择 + StateMapStabilizer 防翻转，28-T7）→ `regime_validation.RegimeOutOfSampleValidator`（制度有效性样本外验证 + 全制度概率比对，28-T9）。`PortfolioLoop` Step 2.5 组合链路：regime_probs 概率混合权重（`probability_mix` 开关，28-T3，无 probs / 关闭时回退硬查表）→ RegimeSmoother 不对称切换（`de_risk_alpha`/`re_risk_alpha`，28-T6）→ exposure_scale 置信度仓位缩放（`confidence_scale` 开关，关闭时恒 1.0）→ `prometheus_metrics.record_regime_metrics` 观测指标上报（fts_regime_confidence/entropy_norm/exposure_scale/blend_hhi/name，28-T10，失败不阻断主流程）。
- 股票 L3 定时任务（v2.98.3，已剥离）：`l3_portfolio_loop_stock`（每周五 19:30 重算组合权重）与 `daily_signal_pipeline`（工作日 08:45）等股票调度已随股票管线剥离至 fts-stock（2026-08），主系统仅保留期货调度。

**因子选择与基础权重（v2.105.0 — L3 组合权威源）**：
- 因子选择与基础权重分配由 L3 组合层负责（`memory/portfolio/futures/factor_weights.json`），
  信号管道不再自选全部精英因子、不再自训 Ridge 回归权重。
- 信号管道仅做信号计算 + 按 Market Regime 对基础权重做档位缩放（`_apply_regime_weight_adjustment`）：
  bull/bear 放大 trend 类因子、oscillate 放大 reversal 类、high_vol 整体收缩；缩放不丢弃因子。
- 方向以 L3 组合语义为准，移除截面 IC 方向校正（`_compute_factor_sign_flips` 已删除，v2.105.0）。
- 品种级 IC 自适应保留（全局权重 × 品种 IC，`_compute_per_variety_weights`）。
- 严格模式：L3 组合权重缺失/为空 → 信号管道报错退出，不自行回退。

**Market Regime 检测（v1.8.1 / v2.20.0 产业链级）**：
- 信号管道在数据加载后、信号计算前，调用 `SectorRegimeSelector.detect_all()` 按产业链独立检测市场制度。
- 检测方法：对每个产业链，从品种面板构建合成 OHLCV（取所有品种 close 截面均值作为产业链综合价格序列），计算 MA20 斜率、ATR/价格、量比、收益自相关，分层判定制度类型。
- 制度类型：bull（趋势上涨）/ bear（趋势下跌）/ high_vol（高波动）/ low_vol（低波动）/ oscillate（震荡）。
- 主制度计算：品种数加权投票（各产业链按其品种数决定权重，消除全市场单一制度对不同产业链结构性机会的掩盖）。
- 报告输出：主制度名称 + 置信度 + 产业链 Breakdown（各产业链制度/置信度/品种数/方向建议）+ Regime 调整后的交易建议。
- 趋势友好（bull/bear）→ 优先做空/做多增量最强的品种，可放大仓位；震荡（oscillate）→ 反向操作；高波动（high_vol）→ 缩小仓位，只做增量绝对值 > 0.15 的品种。
- 实现：`SectorRegimeSelector` 在 `fts/factor_engine/regime.py`，每个产业链使用独立的 `RegimeAwareSelector` 实例保持状态隔离。
- 产业链分类：`FUTURES_SECTOR_MAP` 定义 17 个产业链（黑色系/有色金属/能源/聚酯链/油化工/煤化工/橡胶/造纸林浆纸/航运/油脂油料/谷物/畜牧/软商品/果蔬/贵金属/新能源新材料/金融期货），每产业链品种不足 2 个或数据不足 20 行时跳过。造纸林浆纸链包含纸浆(SP0)/原木(LG0)/纤维板(FB0)/双胶纸(OP0)，航运链单列集运欧线(EC0)（v2.40.0 拆分，原"纸浆集运"链按产业链逻辑拆分为两链）。贵金属链包含黄金(AU0)/白银(AG0)/铂(PT0)/钯(PD0)，铂钯自"新能源/新材料"链归入贵金属板块（v2.45.0，铂族金属 PGM 与黄金白银同属贵金属）。原"农产品" 23 品种大桶按价格驱动拆分（v2.101.0）：油脂油料（豆系/菜系/棕榈油/花生，9）、谷物（玉米/淀粉/稻米/麦，7）、畜牧（生猪/鸡蛋，2）、软商品（白糖/棉花/棉纱，3）、果蔬（苹果/红枣，2），消除不同驱动子链在合成板块 OHLCV 与板块中性化中的信号互抵。

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
    │                              ┌─ 期货 L2 Evolution Loop ─┐
    │                              │ parent_selection (UCT)   │
    │                              │ → macro_evolution (LLM)  │
    │                              │ → micro_evolution (optuna)│
    │                              │ → cross_section_evaluate │
    │                              │ → elite (81因子 × style 14 类)│
    │                              └───────────────────────────┘
    │                                       │
    │                                       ▼
    │                              │ elite 因子（DuckDB catalog 主存储 SSOT + JSON 只读快照；plans/29 P1 写路径反转：先写 DuckDB 成功后写 JSON，GAP-032）
    │                                       │
    │                                       ▼
    └──────────────────────→ L3 Portfolio Loop
                              ├── 正交化
                              ├── 衰减检验
                              ├── 组合构建
                              ├── 品种-链对齐度计算
                              └── 信号合成（含对齐度权重修正）

### 因子淘汰流（v2.17.0，v2.72.1 增加衰减分级闭环，C6 2026-08-11 增加重校准分支）

因子淘汰是主流程的正式环节，通过月度衰减评估触发，确保退化因子从活跃池中移除；C6 起衰减因子先进入重校准队列（参数微调观察），微调无改善才退役：

```
monthly_decay_eval_job (每月1日 02:00)
    │
    ├── Step A (2026-08-13 起合并): fts.monitor.reaudit.run_reaudit() 新标准全量重审
    │       ├── 复用演化准入链: 横截面评估 → Verifier → 审计 → 鲁棒性(11项) → 质量评分卡
    │       └── 处置: retain 保留 / shadow 降级观察池(5交易日) / retire 淘汰
    │            （FTS_MONTHLY_REAUDIT_ENABLED=0 关闭）
    │
    ├── Step B: EliteFactorTracker.run_monthly_evaluation() → 快照状态标记
    │       └── v2.72.1: update() 写入 decay_grade（normal/observe/retired）
    │            由滚动 6M IC 线性回归斜率 _calc_ic_slope_6m 判定
    │            （|slope|>=observe_slope 0.10 → observe；>=retire_slope 0.20 → retired）
    ├── AutoRetireManager.run() → 识别需淘汰因子
    │       └── v2.72.1: auto_retire() 纳入 decay_grade=="retired" 退役条件
    │
    ├── FeedbackLoop FACTOR_DECAY 联动（evolution_loop._run_periodic_factor_review）
    │       └── observe/retired 因子触发归因分析，last_feedback 写回跟踪快照
    │       └── C6 2026-08-11: LiveVsBacktestICReport.recommend_retire=True（decayed）
    │             且 recalibration_enabled → RecalibrationQueue.enqueue(factor_id, reason="decayed")
    │             （衰减因子进入重校准而非直接退役；重校准期间保持 observe 观察）
    │
    ├── RecalibrationQueue.process_recalibration_queue()（C6，CLI fts factor recalibrate run）
    │       └── recalibrate_factor 复用 optimize_params_staged 两阶段漏斗（低 trials + TPE 早停）
    │       └── new_ic - baseline_ic >= min_ic_gap → done 并回写 elite JSON/DuckDB 元数据
    │             （recalibrated_at / recalibrated_ic / recalibrated_params）
    │       └── 无提升 → skipped（继续原退役路径）；无 optuna/异常 → failed 降级不阻断
    │
    └── FactorRepository.retire_factor(factor_id, reason, elite_dir)
            │
            ├── 1. FactorStatusRepository.update_factor_status() → DuckDB status = "retired"
            ├── 2. FactorStatusRepository.log_transition() → 记录状态变迁（old_status → retired）
            ├── 3. 移动 JSON 快照到 {elite_dir}/_retired/{factor_id}.json（期货=futures_elite，v2.86.0）
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

**L2 Verifier Level 1 换手校验（方案 A，v2.104.0+13 / GAP-114）**：`max_turnover_monthly` 从「绝对阈值硬剔」升级为「成本敏感净收益校验」——换手超 5.0（次/月）时不再直接判失败，改按 `净夏普 = 毛夏普 − 月换手×12×2×单边成本率 / 年化波动`（`one_side_cost_rate=0.0005`、`assumed_annual_vol=0.15`，与 `cost_model` 净夏普口径一致）判定：净夏普仍 ≥ `min_sharpe` 即准入并输出 `cost_adjusted` 审计明细。与评估链日换手硬剔除 `factor_turnover_daily_max=0.45`（P95 校准拦极端）分层：外层拦天天翻仓、内层对中高换手因子做成本覆盖判定。动机：库内 active 因子 75.7% 换手>5.0（中位数 6.27），绝对阈值导致审计通过的高 IC 因子系统性被拒、夜间演化 0 晋升。

**EvolutionLoop 熔断预算传播契约（v2.104.0+14 / GAP-115）**：`EvolutionLoop.budget` 为 **property**（setter 重绑时同步传播 `_uct_selector.budget`——UctSelector 是唯一经构造注入 budget 的协作类，其余协作类经 `owner.budget` 动态读主类引用不受影响）。因此任何入口重绑 budget（如 cli.py 夜间任务 `loop.budget = budget`，含 `FTS_EVOLUTION_CB_FAILURE_RATE` env 覆盖失败率阈值）都会自动生效到熔断判定，修复"cli 重绑不传播导致按 DEFAULT 0.95 失败率熔断提前终止"缺陷。

### EvolutionLoop Mixin 拆分契约（34 计划，2026-08-13 起）

`evolution_loop.py` God Class 重构为「领域 Mixin 组合」模式（先 B 后 C 路径的 B 阶段）：

```python
class EvolutionLoop:
    # C 阶段 47i 后继承链清零——组合持有 9 个协作类实例（见下方状态所有权）
```

> C 阶段（Phase 47 系列）逐领域将 Mixin 组合式重构为协作类（主类组合持有 + 转发桩 + property 转发，见 34 §8）。**全部 9 个 Mixin 已退役为协作类**：`EvolutionUctMixin` → `UctSelector`（47a，v2.103.0+21）；`EvolutionPrefilterMixin` → `CandidatePrefilter`（47b，+22）；`EvolutionPromoteMixin` → `EliteStore`（47c，+25）；`EvolutionAuditMixin` → `AuditPipeline`（47d，+26，`_signal_cache` 归本类）；`EvolutionTraceMixin` → `TraceRecorder`（47e，+27）；`EvolutionReviewMixin` → `FactorReviewer`（47f，+29）；`EvolutionChannelsMixin` → `EvolutionChannels`（47g，+30）；`EvolutionSeedsMixin` → `SeedManager`（47h，+32）；`EvolutionCandidateMixin` → `CandidateProcessor`（47i，+33）。**C 阶段收官（Phase 47a-47i）**：`class EvolutionLoop:` 零 Mixin 继承，组合持有 9 协作类实例（_uct_selector/_candidate_prefilter/_audit_pipeline/_trace_recorder/_factor_reviewer/_evolution_channels/_seed_manager/_elite_store/_candidate_processor）+ 全部 `_*` 方法转发桩 + 状态属性 property 转发，公开 API 与行为等价不变。

**契约约束（不可破坏）**：
1. 公开导入路径不变：`from fts.factor_engine.evolution_loop import EvolutionLoop, EvolutionRunResult, UCT_EXPLORATION_C` 等全部保持；
2. 公开方法签名与返回值不变：`run(max_generation)` / `run_microstructure_promotion(...)` 等行为等价（纯移动不改逻辑）；
3. 私有符号兼容：被测试 import 的 `_add_trading_days`/`_build_shadow_pool`/`_QualityInspectionResult`/`main` 等须在 `evolution_loop.py` 保留转发符号或同步迁移；
4. Mixin 方法名全局唯一，避免多继承 MRO 冲突；Mixin 间通过 `self` 动态派发互调，不互相 import；C 阶段协作类间经主类构造注入引用互调（不互相 import 模块）；
5. 跨领域共享状态（`data`/`market`/`forward_returns`/`budget`/`_signal_cache`/`_consecutive_low_ic` 等）留在主类实例，由 `__init__` 装配；C 阶段经构造注入协作类；
6. 领域独享状态随 Mixin/协作类整体搬迁（如 `_uct_stats`/`_evolution_stop_*` → `UctSelector`）；
7. 模块级常量/辅助函数归属：被多领域引用的常量下沉 `evolution_constants.py` 或保持单向依赖（禁止协作类模块 import `evolution_loop.py` 引发循环导入）。

C 阶段：每个 Mixin 演化为独立协作类（`UctSelector` 已完成；后续 `TraceRecorder`/`EliteStore`/...），全局上下文构造注入；主类保留转发桩与 property 兼容测试。盘点证据见 plans/34-evolution-loop-refactor-inventory.md。

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
| L1 Meta-Loop | 07:59 | 工作日每日 | 知识补给 + 种子注入（对齐 TRAE Schedule 期货 L1） |
| L2 Evolution Loop | 00:00 | 工作日每日 | 夜间因子演化（对齐 TRAE Schedule 期货 L2） |
| L3 Portfolio Loop | 19:00 | 工作日每日 | 期货路径（futures_elite + market=futures，v2.73.0）：因子筛选（P1 聚类先行 + CAP 安全阀，v2.104.0+67） + 信号合成(默认equal_weight，v2.103.0+23) + Verifier 校验；GAP-072 v2.99.0 与期货信号管道解绑，工作日每日收盘后重算组合权重（equal_weight 等权漂移小每日重算稳定；对齐 TRAE Schedule 期货 L3 19:00） |
| 期货信号管道 | 20:00 | 工作日每日 | 独立调度（GAP-072 v2.99.0 与 L3 解绑）：因子选择与基础权重由 L3 组合（factor_weights.json）提供（v2.105.0），信号管道仅做信号计算 + Regime 档位缩放权重调整；品种级 IC 自适应保留；方向校正与 Ridge 权重学习已移除；L3 组合缺失/为空 → 严格模式报错退出 |
| 因子巡检 (FactorInspector) | 03:00 | 每日 | 基于 batch_audit 自动检测退化因子并降级 |
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
| 代码→文档映射 | `seed_pool.py` → 期货种子池（81 因子：style_tags 14 类，见 seed_data_futures_full.py）；`data_futures.py` → FuturesDataProvider 期货数据层（82 品种 FUTURES_SUBSET + 59 个品种 DuckDB 缓存 + AKShare 降级，`get_futures_panel()` common_dates 多数对齐 ≥ 品种数//2，FUTURES_SYMBOL_NAMES 名称映射，get_dominant_contracts() 主力合约判定；`FUTURES_SECTOR_MAP` 7 产业链分类）；`data_futures_fundamental.py` → AkshareFuturesFundamentalProvider 期货基本面数据（库存/基差/仓单；仓单 CZCE/GFEX 官方接口 + SHFE/DCE/INE 东财 RPT_FUTU_STOCKDATA，GAP-091 已关闭）；`scheduler/` → 调度层（13 个 APScheduler 定时任务：L1:07:59 / L2:00:00 / L3期货:工作日06:00 / 期货信号管道:工作日20:00 / 健康检查:每10m / 期货数据同步:工作日17:30 / 月度衰减:每月1日04:00 / 数据质量:每5m / 数据级监控:每日04:00 / 逻辑监控:每日22:00 / 因子巡检:每日03:00 / 动态池:周六08:00 / MHF信号:每30m；股票调度已剥离至 fts-stock）；`scripts/futures_signal_pipeline.py` → 横截面信号管道（方向校正 = 截面 IC 法，因子加权 = Ridge 回归 L2 正则化，Market Regime 检测 = SectorRegimeSelector 产业链级分层判定，品种-链对齐度修正 = compute_alignment + _ALIGNMENT_BLEND=0.20，按日期定位，`--universe all` 全量商品池，输出品种名称/主力合约 + 产业链 Breakdown + Regime 调整交易建议 + 对齐度等级分组）；`scripts/daily_signal_pipeline.py`（股票/ETF 信号管道）已随股票管线剥离至 fts-stock（2026-08）；`fts/factor_engine/regime.py` → RegimeAwareSelector 市场制度感知（5 种制度：bull/bear/high_vol/low_vol/oscillate，MA20 斜率 + ATR/价格 + 量比 + 收益自相关）+ SectorRegimeSelector 产业链级制度检测（每个产业链独立构建合成 OHLCV，品种数加权投票计算主制度）+ `compute_alignment()` 品种-链对齐度计算（单品种独立检测与产业链对比，制度相同=置信度乘积，不同=上限0.5）；`strategies/strategy_evolution.py` → 策略进化（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion）；`fts/factor_engine/barra/barra_style.py`（BarraStyleEngine 10 风格暴露）与 `fts/factor_engine/barra/barra_neutralizer.py`（逐日 OLS 风格残差中性化）仍在主系统期货横截面模式使用（`evaluation_chain.py` Step 2.6 调用，GAP-I304 v2.79.0 `l2_barra_style_neutral` 默认开启，面板字段缺失风格自动跳过不阻断评估）；`fts/factor_engine/stock_regime.py`（StockRegimeSelector 股票行业轮动 + 风格轮动检测，GAP-S03）及 `fts/factor_engine/portfolio_loop.py` L3 Step 2.5 股票风格自适应（REGIME_STYLE_MULTIPLIERS 6 股票风格键）均已随股票管线剥离至 fts-stock（2026-08，GAP-S01/S02 股票侧应用成为历史记录）；`fts/factor_engine/regime_hmm.py` → MultiHorizonHMMDetector 多周期 HMM 制度后验（3 周期 HMM 独立拟合 + 伪计数加权投票，hmmlearn 缺失降级规则），regime_probs 全制度概率输出（28-T2）；`fts/factor_engine/regime_calibration.py` → RegimeConfidenceCalibrator 置信度熵标定（`scaled = confidence × (1 − entropy_penalty × H_norm)`，单点分布不折扣/均匀分布最大折扣，无 probs 直通）+ build_rule_regime_probs 规则伪概率构造（28-T4/T5）；`fts/factor_engine/regime_model_selection.py` → BICStateSelector 状态数选择（2~4 态 BIC 贪心搜索 + StateMapStabilizer 映射冻结防翻转，28-T7）；`fts/factor_engine/regime_validation.py` → RegimeOutOfSampleValidator 制度样本外有效性验证（IC/方向准确率/全制度概率比对，28-T9）；`fts/factor_engine/portfolio_loop.py` Step 2.5 → `_compute_exposure_scale` 置信度仓位缩放（28-T4）+ `prometheus_metrics.record_regime_metrics` 观测指标上报（28-T10）；v2.104.0+31 字段消费字典驱动的每日全字段同步：`fts/config/futures_field_consumption.py` → FuturesFieldConsumptionConfig 字段消费字典（SSOT，30 字段三组：kline 17/fundamental 9/term_structure 4，含 channel/source/coverage/consumers 元数据 + 唯一性校验）；`fts/data_futures_fundamental_sync.py` → 基本面每日同步（Stage 2：库存/基差/仓单 9 字段 → memory/cache/futures_fundamental/{symbol}.parquet upsert；基差现货价缺失时 SpotPriceFiller WebSearch 补充三项校验——新鲜度 gap≤3 天/正确性 与最新 close 偏离≤30%/计量单位对齐 AU 元/克·AG 元/千克·股指 点·其余元/吨，校验不过记 missing_spot 不入库）；`fts/data_futures_term_structure.py` → 期限结构每日同步（Stage 3：contract_kline 多合约截面按合约取最新 bar，优先未交割最近两个合约计算 term_spread/roll_yield → memory/cache/futures_term_structure/{symbol}.parquet，全 82 品种）；`fts/scheduler/jobs.py` sync_futures_data_job 三阶段（Stage1 行情→kline_cache / Stage2 基本面→Parquet / Stage3 期限结构→Parquet）+ _verify_field_coverage 字典字段覆盖校验（registered=30） |
| 可验证断言 | 期货种子池总数 = 81（按 style_tags 分类）；期货数据层支持 82 个连续合约品种，数据源优先级 3 级（DuckDB → AKShare → 合成）；common_dates 多数对齐（WH0 等停更品种不清空交集）；方向校正按日期定位；信号管道因子加权 = Ridge 回归（全量因子，L2 正则化）；主力合约判定 = contract_kline 最新交易日最大成交量；调度器注册 13 个任务（L1/L2/L3期货工作日每日06:00 + 期货信号管道每日 + 健康检查 + 月度衰减 + 数据质量 + 逻辑监控 + 因子巡检 + 数据级监控 + 期货数据同步 + 动态池刷新 + MHF信号）；信号管道集成 Market Regime 检测（5 种制度分层判定，输出 Regime 调整交易建议）；品种-链对齐度计算支持 3 种对齐度等级（高≥0.7/中0.5~0.7/低<0.5），默认 _ALIGNMENT_BLEND=0.20；策略进化模块包含 3 种策略（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion）；股票 L2 种子因子相关性预检（≥0.95 标记）已随股票管线剥离至 fts-stock，期货 L2 跳过；L3 组合支持粘性约束（StickyConfig ±30% / 新因子首日封顶）+ 漂移监控（DriftMonitor → drift_history/YYYY-MM-DD.json）；L2 新晋升因子默认不进影子池（v2.103.0+20 起 `_promote_to_elite` shadow_observe 默认 None → 读 `FTS_EVOLUTION_SHADOW_OBSERVE`，默认 "0"=观察期关闭，env=1 恢复 shadow_pool 观察 5 交易日；重审降级因子 shadow_pool 保留观察期不变，种子因子 shadow_observe=False 直接进正式组合）；SchedulerEngine 支持 `start_watchdog()` 进程看门狗；L3 信号合成默认 elastic_net 模式（Elastic Net 截面回归，L1+L2 自动变量选择）；v2.73.0 调度器 `l3_portfolio_loop_job` 显式期货路径（elite_dir=futures_elite_dir + market=futures，`fts/scheduler/jobs.py`）；v2.75.0 机构级权重学习（`fts/factor_engine/weight_learning.py`）：Elastic Net 系数叠加 Ledoit-Wolf 收缩协方差风险调整（volatility_scaling/risk_parity）+ 滚动样本外验证（OOS IC/稳定性/衰减）+ 学习面板按目标交易市场自动匹配（futures→FUTURES_CORE_SUBSET；stock→CSI300 已随股票管线剥离至 fts-stock）+ 跨市场迁移 IC 对比（v2.78.1 起默认关闭 cross_market_ic=False，需显式开启）；ACTIVE_FACTOR_CAP=20（数量安全阀，v2.104.0+67：P1 聚类 + 子链去冗余后代表数仍超限时按 OOS 校正综合评分截断，排序键 use_oos_ic=True 优先取 oos_extrapolation.new_ic，无记录回退样本内 ic）；v2.58.0 换月复权与展期仿真（GAP-046）：`kline_cache` 含 `adj_factor` 列，`get_ohlcv(adjusted=True)` 默认复权，BacktestPipeline 持仓穿越换月日扣展期价差成本，contract_kline 缺失时降级返回原始序列；v2.59.0 期货截面中性化 + 回测真实性仿真（GAP-F03/F02）：EvolutionLoop(market="futures") 自动注入板块映射（FUTURES_SECTOR_MAP 反向构建 {symbol: sector}），截面信号板块去均值；BacktestPipeline 涨跌停拦截（close 涨跌幅 ≥ limit_pct 持仓保持）+ 停牌过滤（volume==0 持仓保持），报告含「被拦截成交统计」；v2.62.0 Barra 风格体系（GAP-S02，主系统保留期货横截面应用）：barra 包 10 风格暴露 + 逐日 OLS 回归残差 + `cross_section_evaluate_backtest` Step 2.6 风格中性化在主系统期货横截面模式继续使用（GAP-I304 v2.79.0 `l2_barra_style_neutral` 默认开启，风格缺失自动跳过；test_barra.py 13 用例随股票侧迁移至 fts-stock，主系统由 evolution_futures/evaluation_chain 集成测试覆盖）；v2.65.0 股票 Regime（GAP-S03，已剥离）：StockRegimeSelector 行业轮动三态 + 风格切换双态 + REGIME_STYLE_MULTIPLIERS 6 股票风格键已随股票管线剥离至 fts-stock（test_stock_regime.py 19 用例迁移）；v2.69.0 股票流水线成熟度收尾（GAP-S09~S12）：`expr_dsl/seed_analyzer.py` 种子表达式静态 PIT 审计（estimate_lookback_static 替换正则，705 表达式扫描仅 1 个 fundamental 切片语法需显式 lookback）；`verify_registry_consistency` 双注册表重叠算子一致性（test_registry 断言 overlapping ≥ 10 且 zero mismatch）；`evolution_mode` 新增 operator_first（历史记录，股票演化默认算子优先 → LLM → GP 逐级兜底已随股票管线剥离至 fts-stock，state `evolution_method_counts` 方法分布记账保留）；`A_SHARE_FIELDS` 10 A 股特有字段 + L5b 4 领域算子（已随股票管线剥离至 fts-stock）；新增 test_seed_analyzer 14 + TestGapS11OperatorFirst 7 用例；v2.79.0 阶段 D 收尾（GAP-F10/F12/F15）：CI 质量门禁（`ruff check` + `ruff format --check` fts/tests/scripts + `mypy fts/` 150 files Success + `pytest tests/benchmarks/ --benchmark-only` + `v*` tag 构建发布，`.github/workflows/ci.yml`）；极值扰动一票否决（`evaluation_chain._compute_extreme_perturbation_ic` 极值剔除重算 IC → `FactorEvaluation.extreme_perturbation` → HighICScreener V2 `ic_drop>25%` 拦截真正生效，`FTSConfig.extreme_perturb_pct` 默认 0.01）；种子库去重校验（`scripts/verify_seed_dedup.py` 内嵌 vs YAML 交叉比对）+ 家族上限配置化（`FTSConfig.max_per_family` env `FTS_MAX_PER_FAMILY` 缺省 15）；v2.80.0 数据驱动动态池（GAP-054）：`get_dynamic_core_subset()` 读取 memory/portfolio/futures/futures_dynamic_pool.json（v2.101.0 起按市场隔离到 memory/portfolio/futures/；缺失/损坏回退静态 FUTURES_CORE_SUBSET 25 品种），data.py/CLI/L3 portfolio_loop/weight_learning/sync_contract_kline/调度任务默认路径全部走动态池；流动性快照口径 = TQ-Local（通达信 17709）真实主力合约最近 5 日主力窗口成交额（量×价×合约乘数），每周六 08:00 `sync_liquidity_pool` 渐进式替换刷新（池内够格全保留 + 产业覆盖约束）；v2.81.0 盲测池（GAP-055）：`FUTURES_HOLDOUT` 6→15 个按产业链分层抽样（覆盖 10 条产业链），与核心动态池/分层训练集互不重叠，L2 演化训练排除盲测池后训练品种 >= 10；v2.84.0 tick 历史缓存增量累积 + Level2 订单流因子（GAP-I503 首期）：`aggregator._write_tick_cache` 按 (symbol, datetime) 去重写入 + `tick_cache_retention_days` 保留清理（默认 7 天），`get_ticks`/`_try_tick_cache` 支持 `start_time`/`end_time` 时间窗口查询（跨会话多次拉取累积成更长 tick 历史）；新建 `fts/factor_engine/microstructure_factors.py`——`classify_tick_direction`（价差方向，持平沿用前向）/`order_flow_imbalance`（滚动窗口主动买卖量差归一化 OFI）/`order_book_imbalance`（5 档深度 OBI）/`large_trade_ratio`（绝对/相对阈值大单占比）/`compute_microstructure_factors` 统一入口（FACTOR_COLUMNS 契约 datetime/direction/trade_volume/ofi/obi/large_trade_ratio，缺列/不足 min_rows 优雅降级空）；v2.85.0 组合目标函数换手惩罚项（GAP-I303）：`portfolio_loop.apply_turnover_penalty`——粘性约束后、归一化前 `w_new' = w_old + (w_new − w_old)/(1+λ)` 收缩权重变动（λ=0 关闭、λ 越大换手越低、新因子不惩罚），`build_combo`/`PortfolioLoop` 参数透传 + `FTSConfig.l3_turnover_penalty`（env `FTS_L3_TURNOVER_PENALTY` 默认 0.0）；v2.89.0 期货多源同步范围扩大——`sync_futures_data_job` 默认同步 `FUTURES_SUBSET` 全品种 82 个（替代原 25 核心/动态池路径，调度任务 17:30 + `fts data sync-futures` CLI 同步生效），`scripts/sync_futures_data.py` `--universe` 默认 all（`--universe core` 仍可手动仅同步核心）；v2.98.0 兜底家族豁免（GAP-070）：`_promote_to_elite` 家族多样性检查（max_per_family 缺省 15）跳过 'other'/'unknown'，其他家族仍受上限约束，TestGapF16PromoteToElite 9→11 用例；v2.98.1 L3 期货路径 Step 0.5b 自动构建市场合成 OHLCV（`SectorRegimeSelector._build_sector_ohlcv`，全品种 close 截面均值 + volume 截面和）——未显式传 `market_ohlcv` 时激活 Step 2.5 Regime 自适应权重调整，面板数据不足/构建异常降级置 None 跳过，仅期货路径生效（股票路径已随股票管线剥离至 fts-stock），test_portfolio_loop_market_ohlcv.py 3 用例全绿；v2.98.2 L2 质检性能（GAP-071）：审计 `_run_factor_audit` 优先复用 `evaluation["walk_forward"]`（n_windows_completed>0 直接构建 oos_result，否则兜底 `_run_walkforward_oos`）；EvolutionLoop 调 `evaluation_chain.evaluate` 传 `walk_forward_config=_build_wf_config(data)`；`evaluate_walk_forward._evaluate_window` 样本外收益 oos 段内自算 fwd 且每窗口仅执行 oos 信号；`signal_cache.SignalCache`（LRU，key=factor_id+params+数据全列值指纹）接入 `FactorExecutor`，`evaluate_backtest`/`evaluate_walk_forward`/`EvaluationChain.evaluate`/`ShapAnalyzer.analyze` 透传，EvolutionLoop 共享缓存（容量 16）注入消融/鲁棒性/SHAP；`walk_forward._df_boundary_date` 空 train 段容错；新增 test_signal_cache.py 14 用例 + test_evaluation_chain TestGap071 3 + test_evolution_loop 审计复用 2 全绿；v2.98.3 股票 L3 早间调度（`l3_portfolio_loop_stock` cron `30 8 * * *`）+ `daily_signal_pipeline_job` 等股票调度已随股票管线剥离至 fts-stock（2026-08）；v2.98.0 GAP-073 短样本 OOS 审计根治：`audit.py` `_check_oos_consistency` 对走航结果 `n_windows_completed < 2` 标记 skipped（L1 兜底无窗口键保持原逻辑），期货横截面演化 `days=500→700` 使 WalkForward 完整产出 4 窗口（探针验证 n_windows_completed=4），test_audit.py 28→32 全绿；v2.100.1 感知层样本按市场区分（历史记录，股票侧 `CSI300_SUBSET[:13]` 已随股票管线剥离至 fts-stock；期货保持 13 期货品种），test_meta_loop 97 用例全绿；v2.101.0 GAP-076 信号管道截面标准化（已随股票管线剥离至 fts-stock）：`daily_signal_pipeline` `--normalize`/`normalize_signal_matrix`/`save_weight_snapshot` 实现迁移至 fts-stock；v2.101.0 GAP-078 TQ 探活进程级重试（已随股票管线剥离至 fts-stock）：`_tq_stock_available()` 股票探活实现迁移至 fts-stock；v2.101.0 市场目录隔离（输出按市场分目录，日常开发追加）：`scripts/futures_signal_pipeline.py` 期货信号报告/信号历史 → `reports/futures/{date}/`、权重快照 → `memory/portfolio/futures/futures_signal_weights.json`；`fts/scheduler/jobs.py` L1 期货 → `memory/meta_loop/futures/`；`fts/cli.py` evolution → `memory/evolution/futures/`、meta_loop → `memory/meta_loop/`、portfolio → `memory/portfolio/`；`fts/factor_engine/portfolio_loop.py` 归因/走航报告 → `reports/{market}/{date}/`；`fts/data_futures.py` 动态池缓存 → `memory/portfolio/futures/futures_dynamic_pool.json`；`scripts/sync_liquidity_pool.py` + 5 个期货专属脚本（futures_strategy/futures_seed_diagnostic/futures_l3_portfolio/futures_factor_revalidation/futures_elite_diagnostic）报告目录 → `reports/futures/{date}/`；跨市场任务（月度衰减）保留共享目录 memory/logs/decay/ 不拆分；v2.101.0 GAP-083 期货持仓/结算接入（plans/27）：`_from_kline_cache` SQL SELECT 增加 hold/settle 列 + 双格式对齐（symbol IN (RB,RB0)，ORDER BY date DESC + 0 后缀优先 → drop_duplicates 保留 RB0=TQ 15 年）+ 真实优先/代理兜底（无效值 NULL/≤0 才用代理 settle=(H+L+C)/3、hold=volume 20 日均量），输出 8 列契约不变；`scripts/backfill_futures_hold.py` AKShare 真实 hold/settle 按日期 UPDATE 双格式回填（幂等/限速/dry-run/异常跳过）；`FTSConfig.futures_enhance_enabled`（FTS_FUTURES_ENHANCE_ENABLED 默认 false）控制 aggregator 注册 IFindSource/WindSource 增强源（_enhance_fields 补 settle/pre_settle/oi_change/hold，需 mcp_enabled+set_mcp_handler 认证，失败降级不阻断）；v2.101.0 GAP-085 数据级监控字段修正：`data_level_monitor.key_fields` 由 `("close","volume","open_interest")` 修正为 `("close","volume","hold")`（期货日线字段为 hold，持仓量缺失/异常监控生效）；v2.101.0 pre_settle 零依赖派生（GAP-083 收尾，plans/27 §12 决策变更）：方案 A（iFinD SDK）因无 iFinDPy 权限不真实接入（`ifind_sdk_source.py` 框架保留），改 `aggregator._derive_pre_settle` 运行时派生 `pre_settle[t]=settle[t-1]`（缺失回退 close[t-1]、首行回退当日 close、仅覆盖无效行、内部按 date 升序派生后还原原行序——兼容缓存路径倒序/源路径升序）接入 get_ohlcv 两路径；`scripts/backfill_futures_hold.py --derive-presettle` 库内幂等回写（双格式 RB/RB0，仅覆盖无效行，dry-run 只读统计）；v2.101.0 字段缺口补充收尾：get_ohlcv 输出扩为 9 列（amount：TDX/kline_cache 真实、AKShare/合成补 0.0）；`fts/data_futures_fundamental.py` `AkshareFuturesFundamentalProvider` 接入 `FTSDataProvider._futures_fundamental`（`enrich_futures_fundamental` 注入 fut_inventory/fut_inventory_chg/fut_spot_price/fut_near_basis/fut_dom_basis/fut_near_basis_rate/fut_dom_basis_rate 7 列，库存 AKShare em/99 双源降级、基差 100ppi 并行逐日（25 自然日窗口））；`fut_macro_import` 种子因子（GAP-088 消费端闭环，期货种子 184→185）；v2.101.0 仓单全品种闭环（GAP-091 关闭）：`AkshareFuturesFundamentalProvider.get_warehouse_receipt` 注入 fut_warehouse_receipt/fut_warehouse_receipt_chg 2 列（enrich 共 9 列）——CZCE/GFEX 走 AKShare 官方接口（ThreadPoolExecutor 6 线程并行逐日 + 跳过周末 + 25 自然日窗口，SR 实测 72,243 手）；SHFE/DCE/INE 走东财 RPT_FUTU_STOCKDATA（`EM_WAREHOUSE_MAP` 品种→SECURITY_CODE：SHFE/DCE 大写、INE 小写 sc/nr/lu/bc，`_fetch_warehouse_receipt_em` 单接口全历史 200 自然日窗口，ON_WARRANT_NUM→warehouse_receipt/ADDCHANGE→change，RB 实测 36,512/M 25,100/NR 16,632/SC 2,961,000）；中金所股指无商品仓单降级空；v2.104.0+31 字段消费字典断言：FUTURES_FIELD_CONSUMPTION 登记 30 字段（kline 17/fundamental 9/term_structure 4）且唯一；sync_futures_data_job 三阶段输出同步摘要含 kline/fundamental/term_structure/coverage 分块，coverage.missing 为空 = 30/30 全字段覆盖；基本面 Parquet 落 memory/cache/futures_fundamental/、期限结构落 memory/cache/futures_term_structure/（按 symbol 分文件，date 去重 upsert） |
| 检验方式 | `python -c "from fts.scheduler.tasks import list_tasks; assert len(list_tasks()) == 13"` |
| 存储域注册表（plans/29 P0，2026-08-11） | `fts/store/registry.py` StorageRegistry 加载 `docs/harness/_data/storage_landscape.yaml`（13 域，version=2026-08-11）；`validate_contract()` 返回空（必填字段/后端枚举/相对路径/状态合法/legacy·planned 迁移血缘方向全部合规）；`FTS_STORAGE_LANDSCAPE_PATH` env 可覆盖 | `python -c "from fts.store import StorageRegistry; assert StorageRegistry().validate_contract() == []"` |
| P1 因子资产入库（plans/29，2026-08-11） | `scripts/migrate_elite_json_to_catalog.py` 差量补齐 + 逐字段校验（`--verify-only` 复核零写入零不一致）；写路径反转 `_promote_to_elite`（先 DuckDB 后 JSON 快照）；`add_evaluation(update_catalog_status=False)` 保持归档 lifecycle | `python scripts/migrate_elite_json_to_catalog.py --market all --verify-only`（exit 0 = 778 因子逐字段一致） |
| P2 运行状态入库（plans/29，2026-08-11；E.3 S2 后端切换 SQLite 2026-08-13） | `fts/store/state_db.py` StateKVStore（state_kv + state_history 双表，**E.3 S2 后端由 DuckDB 切换 SQLite WAL，默认库 data/state.db**）；`scripts/migrate_state_to_duckdb.py` 权威状态 glob 规则入库 + 过程痕迹 tar.gz 归档（复制语义）；231 条目入库读回对账 231/231 一致；`--archive` 产出 data/archive/state_traces_*.tar.gz；`scripts/migrate_state_to_sqlite.py`（E.3 S2）state.duckdb → state.db 迁移 + 行数校验（幂等/锁占用降级），旧库保留冻结期 | `python scripts/migrate_state_to_duckdb.py --verify-only`（exit 0 = 231/231 一致）；`python -c "import sqlite3; c=sqlite3.connect('data/state.db'); assert c.execute('SELECT COUNT(*) FROM state_kv').fetchone()[0] > 0"`；`python -m pytest tests/store/test_state_db.py tests/scripts/test_migrate_state_to_sqlite.py`（33 passed） |
| E.3 L4 状态库 SQLite 化（S2，2026-08-13） | `fts/store/state_db.py` SQLite WAL 后端（upsert 单事务双表原子、seq AUTOINCREMENT 单调、WAL 写连接存活不阻塞外部读）；`scripts/migrate_state_to_sqlite.py` 迁移；storage_landscape `run_state` 域 backend=sqlite/path=data/state.db；API 契约不变，5 个调用模块零改动 | `python -m pytest tests/store/ tests/scripts/test_migrate_state_to_sqlite.py`（33 passed）；`python scripts/migrate_state_to_sqlite.py`（exit 0 = 迁移完成） |
| P3 信号缓存 Parquet 化（plans/29 P3-A，2026-08-11） | `fts/factor_engine/factor_optimizer.py` FactorSignalCache：put 写 `{cache_id}.parquet`（DuckDB 单列 Parquet），signal_index.json 元数据含 backend="parquet"/version=2/checksum；get 优先 Parquet 校验 checksum、.npy 只读兼容回退并自动重建；clear/invalidate 双格式清理 | `python -m pytest tests/factor_engine/test_factor_optimizer.py`（51 passed，含 TestFactorSignalCacheParquet 5 用例）；`python -c "from fts.factor_engine.factor_optimizer import PARQUET_CACHE_VERSION; assert PARQUET_CACHE_VERSION == 2"` |
| G3 换手预算开关（v2.103.0+17，2026-08-13） | `fts/config/settings.py` → `FTSConfig.l3_turnover_budget_enabled`（env `FTS_L3_TURNOVER_BUDGET_ENABLED`，默认 `"0"` 关闭）；`portfolio_loop.py` PortfolioLoop `__init__` 读取后按开关传 `turnover_budget_config`（关闭=传 None，build_combo 跳过 G3 换手预算分配；开启=TurnoverBudgetConfig() 默认 daily_turnover_cap=0.30）；G3 关闭后组合换手控制由粘性约束（±30% clamp）+ 换手惩罚 λ（FTS_L3_TURNOVER_PENALTY）双通道兜底 | `python -c "from fts.config.settings import get_config as c; assert c().l3_turnover_budget_enabled is False"`；`python -m pytest tests/factor_engine/test_turnover_budget.py tests/factor_engine/test_portfolio_loop.py -q`（G3 关闭路径测试全绿） |
| P3 行情库冷热归档（plans/29 P3-B，2026-08-12） | `scripts/archive_history_cold.py`：kline_cache 按 `year(date::DATE)` 冷热归档，≤until-year 导出 `data/archive/history_kline_cache_{year}.parquet` 并从热库 DELETE；dry-run/verify-only/archive 三模式，幂等，写锁占用降级拒绝 | `python scripts/archive_history_cold.py --verify-only --until-year 2013`（exit 0 = cold_rows=44134/hot_remaining=0/consistent=true）；`python -m pytest tests/scripts/test_archive_history_cold.py`（7 passed） |
| P4 读路径切换（plans/29 P4，2026-08-12） | JSON 双读兼容期收紧为 DuckDB SSOT 优先、JSON 仅回退：`get_dynamic_core_subset` 三级降级（state.duckdb → JSON → 静态池）；CLI `factor list/show` 默认 DuckDB 查询（`--elite-dir` 目录仅回退，`factor show` 支持 `--market`）；`state.py`/`portfolio_loop`/`meta_loop`/`extractors` 状态读写走 `StateKVStore`（`get_state_store()` 进程级单例 + 可注入 store 测试隔离） | `python -m pytest tests/factor_engine/test_dynamic_pool.py`（10 passed）；`python -m pytest tests/factor_engine/test_evolution_loop.py tests/factor_engine/test_meta_loop.py tests/factor_engine/test_portfolio_loop.py tests/factor_engine/extractors/ -q`（状态类 SSOT 隔离全绿）；`python -m pytest tests/test_cli_extra.py::TestCmdFactorListExtra`（DuckDB 回退目录模式用例） |
| GAP-084/086/087 股票数据层三 P1 关闭（v2.103.0，已剥离） | `fetch_stock_ohlcv` 列扩展（amount/vwap/pre_close）、`scripts/build_cap_map.py` 市值映射、`data_fundamental.py` 股票宏观源等股票数据层增强已随股票管线剥离至 fts-stock（2026-08） | 验收命令与测试用例（test_stock_ohlcv_columns / test_build_cap_map）迁移至 fts-stock |
| 分钟级数据流 | `fts/data_sources/aggregator.py` 新增 `get_minute_ohlcv()` 方法；`fts/data_sources/tdx_local_source.py` 通达信本地 HTTP 统一源（端口 17709，日线+分钟+快照，v2.87.0，`TDX_RPC_URL=http://127.0.0.1:17709/`，`SUPPORTED_PERIODS={"day":"1d","1m":"1m","5m":"5m","15m":"15m","30m":"30m","60m":"1h"}`，日线 17 列/minute 11 列，`source_name="TDX_LOCAL"`）；`fts/data_sources/tqsdk_source.py` TQSDK 分钟数据源；`fts/factor_engine/backtest_pipeline.py` `BacktestInput.frequency` 字段；`fts/cli.py` `--frequency` 参数 | `minute_cache` 表结构存在 (symbol/period/datetime/open/high/low/close/volume/source)；`BacktestInput.frequency` 支持 "daily"/"1m"/"5m"/"15m"/"30m"/"60m"；年化因子自适应 252(daily)→98280(1m) | `pytest tests/test_backtest_frequency.py` |
| EvolutionLoop Mixin 拆分（34 计划 B 阶段，2026-08-13） | `evolution_uct.py` EvolutionUctMixin（领域 I：_select_parent_uct/_update_uct_stats/_update_uct_failure/_check_circuit_breaker/_maybe_early_stop）；`evolution_trace.py` EvolutionTraceMixin（领域 J：_build_parent_failure_ctx/_build_success_pattern_report/_record_experiment_variant/_export_experiment_log/_record_audit_failed_trace/_record_ablation_failed_trace/_record_robustness_failed_trace/_record_causal_failed_trace/_record_success_trace/_record_failure_trace/_log_inspection_detail/_record_quality_filtered_trace + _QualityInspectionResult）；`evolution_channels.py` EvolutionChannelsMixin（领域 G：_run_gp_evolution/_run_deep_evolution/_generate_operator_factor/_try_operator_engine_evolution）；`evolution_seeds.py` EvolutionSeedsMixin（领域 D：_evaluate_and_promote_seeds/_merge_l1_candidates/_run_seed_correlation_check/_build_barra_exposures/_evaluate_cross_section/run_microstructure_promotion）；`evolution_audit.py` EvolutionAuditMixin（领域 E：_run_factor_audit/_run_walkforward_oos/_run_backtest_pipeline/_run_ablation_check/_run_robustness_check/_run_shap_analysis/_run_causal_validation/_build_wf_config/_is_blocking_ablation + _ABLATION_* 类常量）；`evolution_review.py` EvolutionReviewMixin（领域 F：_run_periodic_factor_review/_get_factor_data_for_review/_register_factor_baseline/_check_factor_data_quality）；`evolution_prefilter.py` EvolutionPrefilterMixin（领域 H：_quick_prefilter/_cross_section_prefilter/_check_factor_runtime）；`evolution_promote.py` EvolutionPromoteMixin（领域 C：_write_seed_correlation_index/_scan_elite_correlations/_check_elite_correlation/_count_cluster_members/_orthogonalize_via_basis/_orthogonalize_candidate/_load_elite_parent_factors/_release_repo_after/_get_repo/_promote_to_elite/_write_to_duckdb）；`evolution_candidate.py` EvolutionCandidateMixin（领域 B：_process_candidate，B 阶段收官）；`evolution_loop.py` `class EvolutionLoop(EvolutionTraceMixin, EvolutionChannelsMixin, EvolutionSeedsMixin, EvolutionAuditMixin, EvolutionReviewMixin, EvolutionPrefilterMixin, EvolutionPromoteMixin, EvolutionCandidateMixin)`；公开 API（EvolutionLoop/EvolutionRunResult/UCT_EXPLORATION_C/_add_trading_days/_build_shadow_pool/_QualityInspectionResult/main）与行为等价不变 | `python scripts/analyze_evolution_loop.py`（方法/属性分布基线，B 阶段收官 1470 行/9 方法）；Phase 46h 定向：`pytest tests/factor_engine/test_structure_cluster_quota.py tests/factor_engine/test_orthogonal_basis.py tests/factor_engine/test_l2_orthogonalize.py tests/factor_engine/test_l2_elite_redundancy.py tests/factor_engine/test_evolution_l1_merge.py tests/factor_engine/test_microstructure_promotion.py -m "not slow" -q`；Phase 46i 定向：`pytest tests/factor_engine/test_evolution_loop.py tests/factor_engine/test_evolution_stop.py tests/factor_engine/test_coverage_edge_cases.py tests/factor_engine/test_batch_mining.py -m "not slow" -q` |
| 35-gap-closure-plan P2 批次（v2.103.0+15，2026-08-13） | `fts/data_sources/trading_calendar.py`（新）→ G8 统一交易日历层（`TradingCalendar` get_trading_days/is_trading_day/align 停牌 ffill + `mark_panel_data_gaps` 断K标记 + `mark_gap_anomalies` 跳空异常标记），`fts/data_futures.py get_futures_panel` 接入（data_gap/gap_anomaly 列，`inject_data_gap_enabled` 默认开，清洗失败降级）；`fts/factor_engine/barra/barra_neutralizer.py` → G10 中性化注入（`vol_map` 截面波动率列 + `dates` 时序月度去季节化 `_deseasonalize_time_series`，波动率列在场补截距），`cross_section_evaluate_backtest(vol_map=...)` 透传，`evolution_seeds.py`/`evolution_futures.py` `_build_vol_map` 双路径接入（与 `l2_barra_style_neutral` 同门控）；`fts/factor_engine/signal_contract.py` → G12 信号契约（`SignalDetail` 扩 target_lots/current_lots/delta_lots/score/regime/risk_usage + `to_lots` + `signal_map_to_factor_signal` 统一转换器 + validator 新字段校验），`scripts/mhf_signal_pipeline.py`/`fts/live_trade/tqsdk_mhf_executor.py` 接入；`fts/factor_engine/regime_multipliers.py` → G14 Regime 风控参数（`REGIME_RISK_PARAMS` 第二张表 + `resolve_risk_params` 平滑），`fts/risk/risk_manager.py`/`paper_trader_mhf.py` 接入；`fts/factor_engine/standardizer.py` → G9 MAD（mad_winsorize/mad_then_zscore）；`fts/factor_engine/capital_allocator.py` → G15 min_variance（Ledoit-Wolf 收缩）；`fts/factor_engine/evaluation_chain.py` → G11 日换手硬门槛 | `python -c "from fts.data_sources.trading_calendar import TradingCalendar, mark_panel_data_gaps, mark_gap_anomalies; assert len(TradingCalendar.from_symbol_dates({})._days) == 0"`；`pytest tests/data_sources/test_trading_calendar.py tests/factor_engine/test_barra_vol_season_neutral.py tests/factor_engine/test_regime_risk_params.py tests/factor_engine/test_signal_contract_g12.py -q`（53 passed）；`python -c "from fts.factor_engine.regime_multipliers import REGIME_RISK_PARAMS; assert REGIME_RISK_PARAMS['high_vol']['leverage_cap'] == 1.0"` |
| ②b2a signal_sharpe raw 口径（v2.104.0+73，2026-08-16） | `fts/factor_engine/portfolio_loop.py` build_combo：`pre_weighted_sharpe` 改用 `s.get("_sharpe_raw", s.get("sharpe"))`（截断前原始值优先，缺失回退截断值）；`synthesize_signals` quality_weight 分支透传 `_sharpe_raw`；`fts/factor_engine/contracts.py` DEFAULT_L3_VERIFIER_CONFIG.max_sharpe 3.5→8.0 + `fts/config/settings.py`/`config/settings.yaml` verifier.max_sharpe=8.0（raw 口径信号质量上界 2.0→提升，3.5 会恒触发过拟合警告）；权重计算/展示仍用 SHARPE_CAP=2.0 截断值 | `python -c "from fts.factor_engine.contracts import DEFAULT_L3_VERIFIER_CONFIG as c; assert c['max_sharpe'] == 8.0"`；`python -m pytest tests/factor_engine/test_portfolio_loop.py -q`（261 passed，含 test_signal_sharpe_uses_raw_not_capped / test_raw_sharpe_forwarded / test_passes_high_sharpe_within_cap） |
