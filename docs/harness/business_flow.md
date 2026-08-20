# FTS 业务流程图

> 版本: v3.0.0+11
> 最后更新: 2026-08-05

## 全景业务流

```
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                          数据接入层                                     │
  │                                                                         │
  │  QuantData (唯一权威 K 线源, DuckDB 只读) + Wind/iFinD 字段增强层          │
  │       │                                                                 │
  │       │ 期货 OHLCV K 线数据（主力连续/换月复权）                        │
  │       ▼                                                                 │
  │  data_futures.py (v3.0.0+1：QuantData 权威主链路 + 显式扩展)              │
  │       │                                                                 │
  │       │ 统一数据接口 (get_panel / get_forward_returns / get_dates)      │
  │       ▼                                                                 │
  └───────┬─────────────────────────────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                     L0 人类设定层                                       │
  │                                                                         │
  │  Program.md (人类编写)                                                  │
  │  ├── 目标: 收益率/夏普/最大回撤                                         │
  │  ├── 约束: 最大持仓数/行业集中度/换手率限制                             │
  │  ├── 市场偏好: 期货主力连续/全品种                                      │
  │  └── 风险偏好: 保守/均衡/进取                                           │
  │       │                                                                 │
  │       │ parse_program_md() → ProgramConfig                              │
  │       ▼                                                                 │
  └───────┬─────────────────────────────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                   L1 Meta-Loop (知识感知层)                              │
  │                                                                         │
  │  meta_loop.py                                                           │
  │       │                                                                 │
  │  ├── BootstrappingChain: 市场知识补给 (Web 感知)                        │
  │  ├── DebateQualityAnalyzer: 辩论质量分析                                │
  │  ├── FactorPoolManager: 因子池管理 (注入种子/淘汰劣质)                  │
  │  ├── BulkKnowledgeExtractor: 批量采集→粗筛→LLM 深读 (≥300 篇/天)      │
  │  ├── KnowledgeRelevanceFilter: embedding 粗筛 + 语义去重 (关键词降级)  │
  │  ├── LLM 编译修复 + l1_rejected_retry: 失败候选复活 (GAP-131)          │
  │  ├── L1Verifier: L1 锁定协议                                            │
  │  └── MetaStateManager: 状态管理                                         │
  │       │                                                                 │
  │       │ 注入种子因子 + 演化方向指引                                      │
  │       ▼                                                                 │
  └───────┬─────────────────────────────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │               L2 Evolution Loop (因子演化层)                             │
  │                                                                         │
  │  evolution_loop.py (L2 主循环协调器)                                    │
  │       │                                                                 │
  │  ├── seed_pool.py: 种子池 (81 期货因子)                                │
  │  │   └── seed_data/: 种子因子定义库                                     │
  │  │       └── 期货种子 81（style_tags 14 类分类）                          │
  │  │       （股票种子库 WQ101/Qlib158/GTJA191/基本面/另类/宏观             │
  │  │        已随股票管线剥离至 fts-stock，2026-08）                       │
  │  │                                                                      │
  │  ├── macro_evolution.py: LLM 宏观演化 (改逻辑)                          │
  │  ├── micro_evolution.py: optuna 微观调参                                │
  │  ├── evaluation_chain.py: 三级评估链                                    │
  │  │   ├── Level 1: 回测验证 (IC/夏普/收益)                               │
  │  │   ├── Level 2: 经济逻辑检验                                          │
  │  │   └── Level 3: 多重检验 (Bonferroni/FDR/adjusted_t)                  │
  │  ├── verifier.py: Verifier 锁定协议                                     │
  │  ├── audit.py: 因子审计 (6 项强制审计)                                   │
  │  │   ├── 因果检验 (Granger/反事实分析)                                   │
  │  │   ├── 样本外验证 (WalkForward OOS)                                   │
  │  │   ├── 跨品种验证 (≥80% 品种 IC 为正)                                 │
  │  │   ├── 压力测试 (极端行情)                                             │
  │  │   ├── 多重检验 (Bonferroni/FDR)                                      │
  │  │   └── 数据窥探检验 (无未来函数)                                       │
  │  ├── factor_quality_inspection.py: 质量评分卡 (50 分制)                 │
  │  │   ├── IC 稳定性/Sharpe/换手率/容量/频率/覆盖率/鲁棒性/经济逻辑        │
  │  │   └── A/B/C 级分级准入                                               │
  │  ├── regime.py: 市场制度检测 (bull/bear/oscillate/high_vol/low_vol)     │
  │  │   └── 28 计划: 全制度概率 regime_probs → 概率混合权重 (regime blend) │
  │  │       → 熵标定 exposure_scale 仓位缩放（见 plans/28-*）             │
  │  └── state.py: 演化状态管理 + trace_id 全链路                           │
  │       │                                                                 │
  │       │ elite 因子 → memory/knowledge/factors/futures_elite/ (JSON)            │
  │       ▼                                                                 │
  └───────┬─────────────────────────────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │               L3 Portfolio Loop (组合构建层)                             │
  │                                                                         │
  │  portfolio_loop.py                                                      │
  │       │                                                                 │
  │  ├── load_approved_factors(): 加载 active elite → 质量门 → 影子池剔除   │
  │  │      → approved 硬过滤（仅 factor_reviews.decision='approved'）      │
  │  ├── orthogonalize_factors(): 因子正交化/去重                            │
  │  ├── decay_test(): 衰减检验                                              │
  │  ├── build_combo(): 构建组合 (等权/夏普加权/Elastic Net)                │
  │  ├── synthesize_signals(): 信号合成                                     │
  │  └── L3Verifier: L3 锁定协议                                            │
  │       │                                                                 │
  │       │ 组合权重快照 + combo_history（DuckDB SSOT）                     │
  │       ▼                                                                 │
  └───────┬─────────────────────────────────────────────────────────────────┘
          │
          ▼
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                          信号输出层                                     │
  │                                                                         │
  │  ScoredSignal (交易信号)                                                │
  │  ├── symbol: 标的代码                                                   │
  │  ├── direction: 方向 (1=多, -1=空)                                      │
  │  ├── weight: 因子加权得分                                               │
  │  ├── score: 综合评分                                                    │
  │  ├── factor_breakdown: 因子贡献明细                                     │
  │  └── trace_id: 全链路追踪 ID                                            │
  │       │                                                                 │
  │       │ 下游系统消费 (FDT 等)                                            │
  │       ▼                                                                 │
  └─────────────────────────────────────────────────────────────────────────┘
```

## 触发时序

```
时间线（调度源：TRAE Schedule 定时自动化；内部 fts/scheduler 默认停用，v2.104.0+99）
  │
00:00  L1 Meta-Loop 启动（每日，l1_meta_loop）
  │      ├── Web 感知 + 批量采集（arXiv/OpenAlex/东财/全球报告 ≥300 篇/天）→ 市场知识补给
  │      ├── embedding 粗筛 → LLM 深读提取（≤l1_knowledge_deepread_max）
  │      ├── 编译失败候选 LLM 修复/复活（l1_rejected_retry）
  │      ├── 种子因子注入 → l1_injected/ + factor_pool.json（pending）
  │      └── 演化方向指引 → L2
  │
02:00  L2 种子评估晋升（每日，l2_seed_promotion）
  │      └── L1 注入种子相关性预检 + 评估晋升入 elite 池（供当日 04:00 演化消费为父因子，不重置演化状态计数器）
  │
03:00  L2 Evolution Loop 启动（工作日 l2_evolution_weekday ≈10 代 / 周六 l2_evolution_weekend ≈50 代）
  │      ├── 加载种子因子（81 期货专用）/ elite 父池（UCT 选择）
  │      ├── 合并 L1 注入候选（pending 门控 + market 过滤 + 去重）
  │      ├── GP/深度/算子 DSL 演化 → 新因子生成
  │      ├── optuna 微观调参 → 参数优化
  │      ├── 准入链（Verifier → 去冗余 → B.4 高IC → 多重检验 → WF → 审计 → 评分卡 → 影子池）
  │      └── 熔断检查 → 保护机制
  │
05:00  L3 Portfolio Loop 启动（工作日，energy：fts portfolio run --universe energy）
  │      ├── 加载 active elite → 质量门 → 影子池剔除 → approved 硬过滤（仅 factor_reviews.decision='approved'，消费当日 04:00 机审结果）
  │      ├── 去重/聚类 → quality_weight 综合评分 → Step 2b 子链调制 + Step 2.5 Gate
  │      └── 信号合成 → ScoredSignal（Verifier 校验）→ factor_weights.json
  │
周日06:00  L2 批量挖掘（l2_batch_mining）
  │      └── BatchMiner 批量漏斗（同父多后代 → 并行粗筛 → 准入链），熔断隔离不污染演化状态
  │
周日09:00  L2 批量子链评估（l2_subchain_quality_job，v2.105.0+16）
  │      └── 全部 active 因子逐品种 IC → 子链画像 → 落库 subchain_factor_quality 质量矩阵
  │           （min_chain_ic=0.02；无有效链因子标记 pending_validation 不自动降级，04:00 评审质检前刷新画像）
  │
17:30  期货多源数据同步（工作日每日，Phase 14.5）→ 行情缓存更新（供次日 01:00 L2 使用）
  │
20:00  信号管线启动（工作日每日，消费 factor_weights.json）→ 横截面信号报告
  │
每日04:00  FTS L2 因子生命周期管理+监控统一任务（v3.0.0 合并原「周日 10:00 L2 周度评审」+「每日 04:00 阀门+三项监控」，TRAE Schedule 3f5d5da3）
  │      ├──【周日重量级分支】（l2_review_job）
  │      │    ├── Step A reaudit 新标准重审（retain/shadow/retire）
  │      │    ├── Step B 衰减评估 + AutoRetire 自动淘汰
  │      │    └── Step C review_l3_pool 复核 L3 池 + list_pending 机审（approved 唯一收口出口，组合防抖）
  │      │         （energy 走 l2_energy_qa_review_job 手动调用，退化检测消费 09:00 子链画像）
  │      └──【每日轻量五步】
  │           ├── ① pending 机审 + approved 复核（_review_gate_weekly：新因子当日 approved）
  │           ├── ② 因子巡检降级（factor_inspector：Sharpe↓20%；approved 因子豁免仅标记待周日收口）
  │           ├── ③ 逻辑监控（logic_monitor：行为漂移 / 极端预测 / 换月异常 / 市场前提）
  │           ├── ④ 数据级监控（data_level_monitor：缺失率 / 异常值 / 多源分歧）
  │           └── ⑤ 因子级监控（factor_level_monitor：完整性 / 一致性 / 血缘 / 逻辑 / 实盘偏离）
  │           （周日执行重量级分支后 Step C 已覆盖轻量①，可跳过；05:00 起消费当日 04:00 机审结果）
  │
每5分钟   数据质量评估（data_quality_eval）
每10分钟  Health Check（状态轮询 / 熔断检测 / 告警通知）
  │
按需      WorkFlow UI（fts ui，2026-08-14 v2.104.0+25）
         ├── 用户在 /workflow 看板点击「创建并端到端执行」
         ├── WorkflowExecutor 按 11 阶段顺序真实执行 fts cli 动作
         ├── 失败即停、批次状态 SQLite 回放
         └── 质检看板 /api/workflow/qa/board 聚合 QA 7 状态分布
（股票 L3 19:30 / 股票信号管道 08:45 已随股票管线剥离至 fts-stock，2026-08，主系统不再调度）

调度源说明（v2.104.0+99）：内部 fts/scheduler 定时任务默认停用（FTS_INTERNAL_SCHEDULER_ENABLED 未设或 "0"），
周期任务由 TRAE Schedule 定时自动化执行（时间与上表一致），内部调度器不重复执行。
一键启用：$env:FTS_INTERNAL_SCHEDULER_ENABLED="1"; fts scheduler run
一键停用：Remove-Item Env:FTS_INTERNAL_SCHEDULER_ENABLED; fts scheduler run
状态查看：fts scheduler status
```

## 角色边界

| 层级 | 职责 | 不可越界 |
|:-----|:-----|:---------|
| L0 人类 | 设定目标/约束/偏好 | 不干预具体因子演化 |
| L1 Meta-Loop | 知识补给、种子注入、方向指引 | 不修改因子代码 |
| L2 Evolution Loop | 因子发现、评估、演化、审计、质检 | 不构建组合、不输出交易信号 |
| L3 Portfolio Loop | ⚠️ v3.0.0 已登记退役（plans/57）：组合构建/信号合成职责迁移 Regime-Driven；FTS 侧保留信号矩阵输出 | 不执行交易 |
| 策略合成层（Regime-Driven） | ⚠️ v3.0.0 起承接策略合成：三层 Regime + 信号合成 + 五要素路由 + 组合风控（消费 FTS 因子信号契约 v1） | 不参与因子发现/演化 |
| 下游系统 | 执行交易 | 不参与因子发现 |

## 熔断机制

| 类型 | 触发条件 | 影响范围 |
|------|----------|----------|
| Token 熔断 | 消耗 > budget * 2.0 | 停止当前 L2 运行 |
| 低 IC 熔断 | 连续 5 代 IC < 0.005 | 暂停演化 |
| 失败率熔断 | 失败率 > 95% | 停止当前 L2 运行 |
| 连续低质量 | 连续 3 代质量分 < 30 | 暂停演化 |

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件描述 FTS 全景业务流，覆盖 `meta_loop.py`（L1）、`extractors/bulk_collector.py`+`bulk_knowledge.py`+`knowledge_filter.py`（L1 批量三层管线）、`evolution_loop.py`（L2）、`portfolio_loop.py`（L3）、`scheduler/engine.py`（调度器）、`monitor/`（监控）各模块的职责边界和触发时序 |
| 可验证断言 | 业务流包含 L0~L3 四层 + 信号输出层共 5 层架构；时序图为实际 cron：L1 00:00 / 种子评估 02:00 / 演化 03:00（工作日≈10 代·周六≈50 代）/ 批量 周日06:00 / L3 工作日06:00（approved 硬过滤）/ 信号管道 20:00 / 评审质检统一任务 每日04:00（周日重量级：l2_review_job 全量重审+衰减+review_l3_pool 收口；平日轻量：①机审→②巡检 approved 豁免→③逻辑→④数据级→⑤因子级）/ 数据质量 每5min / 健康检查 每10min；内部调度停用（`FTS_INTERNAL_SCHEDULER_ENABLED` 默认 "0"），TRAE Schedule 为唯一调度源；L1 时序含批量采集(≥300 篇/天)→embedding 粗筛→LLM 深读与失败复活（l1_rejected_retry）；股票 L3 19:30 与股票信号管道 08:45 已随股票管线剥离至 fts-stock（2026-08）；角色边界表中 L1 不可修改因子代码、L2 不可构建组合、L3 不执行交易 |
| 检验方式 | 对照 `01-architecture.md` 架构文档确认层级定义一致；对照 `fts/scheduler/tasks.py` REGISTRY 16 任务 cron 确认时间点一致；`fts scheduler status` 查看实际调度数（内部停用默认 0）