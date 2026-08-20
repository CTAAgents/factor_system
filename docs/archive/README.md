# FTS 文档归档索引

> 版本: v3.0.0+5
> 最后更新: 2026-08-20
> 归档时间: 2026-08-20（Harness 精简压缩，v3.1.0 规划基线）

本目录存放从 `docs/harness/` 移出的历史文档。归档原因：Harness 精简压缩——`docs/harness/` 只保留"当前现状基线"（编号主文档 + 流程文档 + 活跃数据），历史计划/设计/验收/评审文档全部移入本目录完整保留，git 历史可回溯。

**归档迁移对照**：`docs/harness/plans` → `docs/archive/plans`，`docs/harness/design` → `docs/archive/design`，`docs/harness/acceptance` → `docs/archive/acceptance`，`docs/harness/tech-review` → `docs/archive/tech-review`。

代码/测试 docstring 中对旧路径 `docs/harness/{plans,design}` 的引用已同步更新为 `docs/archive/{plans,design}`。

## plans/ — 历史实施计划（66 篇）

编号计划（10-60）按实施主题排列，正文含完整背景、设计与验收标准。主文档中 `plans/NN` 引用一律指向本索引。

| 文件 | 主题 |
|---|---|
| 10-evolution-optimization-plan.md | 演化优化 |
| 11-factor-mining-optimization-plan.md | 因子挖掘优化 |
| 11-logic-review-plan.md | 五层逻辑审查 |
| 12-factor-generalization-plan.md | 因子泛化 |
| 13-futures-data-source-integration.md | 期货数据源集成（TQ+iFinD+Wind MCP） |
| 14-cross-market-generalization-plan.md | 跨市场泛化验证 |
| 15-minute-backtest-plan.md | 分钟级回测 |
| 16-tick-data-source-plan.md | TQSDK Tick 数据源 |
| 17-tick-microstructure-plan.md | tick 微观结构 |
| 18-macro-field-enhancement-plan.md | 宏观字段增强 |
| 19-adaptive-weight-l3-integration.md | Adaptive 权重接入 L3 |
| 20-futures-roll-adjustment-plan.md | 期货复权与展期仿真 |
| 21-futures-maturity-optimization-plan.md | 期货流水线成熟度 |
| 22-stock-pipeline-maturity-plan.md | 股票流水线成熟度 |
| 23-institutional-transformation-plan.md | 机构级全面改造 |
| 24-l3-l4-institutional-plan.md | L3/L4 机构级追赶 |
| 25-futures-factor-qc-complement-plan.md | 期货因子质检补全 |
| 26-autoresearch-evolution-optimization-plan.md | L2 因子演化优化（+26-phase0-*） |
| 27-futures-hold-settle-integration-plan.md | 期货持仓/结算数据源（+27-phase-c-*） |
| 28-regime-institutional-optimization-plan.md | Regime 机构级优化（+28-phase-final-*） |
| 29-storage-convergence-plan.md | 数据持久化与存储收敛 |
| 30-ashare-special-fields-plan.md | A 股特有字段 |
| 31-stock-fundamental-fields-plan.md | 股票基本面字段 |
| 32-stock-extraction-plan.md | 股票管线剥离 |
| 33-mhf-trading-plan.md | 中高频（MHF）交易策略 |
| 34-evolution-loop-refactor-inventory.md | evolution_loop 职责盘点与 Mixin 拆分 |
| 35-gap-closure-plan.md | 全链路缺口关闭（G1-G17） |
| 36-factor-selection-composite-improvement-plan.md | 因子选择与组合构建改进 |
| 37-panel-vector-plan.md | 横截面评估全矩阵化（panel_vector） |
| 38-numba-batch4-plan.md | numba 内核定点引进 |
| 39-gap-panel-2d-plan.md | 缺口面板 2D 化 |
| 40-l3-portfolio-optimization-plan.md | L3 组合重算性能优化 |
| 41-l1-knowledge-injection-boost.md | L1 知识注入增强（+41-owl-factor-screening-plan.md） |
| 42-seed-lifecycle-cooldown-reentry-plan.md | 种子生命周期冷却回归 |
| 43-test-factor-db-isolation-plan.md | 测试因子库隔离（GAP-129） |
| 44-l1-knowledge-supply-upgrade.md | L1 每日知识补给增强 |
| 45-l2-loop-split-plan.md | L2 循环拆分 |
| 46-source-auto-discovery-plan.md | 知识源自动发现 |
| 47-subchain-market-structure-plan.md | 子链差异化与市场结构 |
| 48-regime-layered-gating-plan.md | Regime 分层方向 Gate |
| 49-subchain-quality-lifecycle-plan.md | 因子×子链质量矩阵 |
| 50-l3-weight-gate-plan.md | L3 权重层 Gate 闭环 |
| 51-vectorization-gap-fix-plan.md | 张量化衔接缺口修复 |
| 52-l3-signal-append-window-plan.md | L3 信号矩阵增量窗口追加 |
| 53-regime-conditional-trading-plan.md | Regime 条件化因子交易 |
| 54-regime-driven-improvement-roadmap.md | Regime-Driven 改进方向 |
| 55-regime-beta-layer-plan.md | L0 宏观 Beta 层 |
| 56-regime-crowding-plan.md | 拥挤度体系化 |
| 57-dual-system-factor-strategy-split-plan.md | 双系统职责重划（FTS 因子生产 / Regime-Driven 策略合成） |
| 58-push-governance-plan.md | GitHub 推送范围治理 |
| 59-qa-review-optimization-plan.md | 因子评审质检体系优化 |
| 60-quantdata-roll-calendar-root-cause-plan.md | 换月日历根治（QuantData continuous_map） |
| factor-management-optimization-plan.md | 因子管理优化 |
| impact-assess-subchain-tensorization.md | 子链张量化影响评估 |
| performance-implementation-roadmap.md | 性能优化实施路线图 |
| production_plan.md | 生产就绪计划 |
| regression-fix-list-20260808.md | 全量回归失败项修复清单 |

## design/ — 历史技术设计（25 篇）

| 文件 | 主题 |
|---|---|
| A.1-factor-quality-card-design.md | 因子质量评分卡 |
| A.2-factor-decay-tracking-design.md | 因子衰减追踪与淘汰 |
| A.3-adaptive-weight-design.md | 自适应动态权重 |
| B.1-data-quality-monitor-design.md | 数据质量实时监控 |
| B.2-backtest-pipeline-design.md | 端到端回测流水线 |
| B.3-factor-audit-design.md | 因子审计流程 |
| B.4-high-ic-screening-design.md | 高 IC 因子筛查剔除 |
| C.1-feature-engineering-platform-design.md | 特征工程中台 |
| C.2-live-trading-integration-design.md | 实盘对接与实时监控 |
| C.3-feedback-loop-design.md | 系统化反馈闭环 |
| C.4-operator-evolution-engine-design.md | 算子演化引擎 |
| D.1-batch-mining-design.md | 批量挖掘漏斗（GAP-I201） |
| D.1-simulated-portfolio-design.md | 模拟仓模块 |
| D.2-simulated-portfolio-advanced-optimization.md | 模拟交易进阶优化 |
| E.1-duckdb-concurrency-design.md | DuckDB 并发模型根治（GAP-056） |
| E.2-storage-backend-comparison.md | 存储后端对比评估 |
| E.3-sqlite-state-store-design.md | L4 状态库 SQLite 化 |
| E.4-duckdb-connection-lifecycle-design.md | L2/L3 DuckDB 连接生命周期根治 |
| F.1-data-contract-split-design.md | 数据契约拆分（Fused→Stock/Futures） |
| F.2-evolution-engine-fork-design.md | 演化引擎分叉 |
| F.3-signal-contract-v1-design.md | 因子信号接口契约 v1 |
| F.4-factor-definition-dispatch-interface.md | 因子定义分发接口（+F.4-rd-side-sync-patch.md） |
| OWL-factor-screening-evaluation.md | OWL 因子分组筛选评估 |

## acceptance/ — 历史验收报告（10 篇，v2.3.0）

| 文件 | 主题 |
|---|---|
| 14.0.6-migrate-acceptance.md | DuckDB 表结构迁移验收 |
| 14.0.7-tq-local-acceptance.md | TQ-Local 数据源适配器验收 |
| 14.0.8-wind-mcp-acceptance.md | Wind MCP 数据源适配器验收 |
| 14.0.9-ifind-mcp-acceptance.md | iFinD MCP 数据源适配器验收 |
| 14.1-aggregator-acceptance.md | 数据源优先级调度器验收 |
| 14.2-cross-validation-acceptance.md | 多源交叉验证验收 |
| 14.3-data-fusion-acceptance.md | 多源数据融合验收 |
| 14.4-cli-integration-acceptance.md | CLI 集成与多源联调验收 |
| 14.5-observability-acceptance.md | 调度注册 + 可观测性端点验收 |
| 14.6-prometheus-watchdog-acceptance.md | APScheduler + Prometheus 端点验收 |

## tech-review/ — 历史技术评审（1 篇）

| 文件 | 主题 |
|---|---|
| P1_P2_clustering_pca_tech_review.md | P1 因子聚类 + P2 PCA 降维技术评审 |

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| docs/archive/ 目录结构与 docs/harness/ 归档迁移 | docs/archive/{plans,design,acceptance,tech-review} 存在且非空 | `Get-ChildItem docs/archive -Directory` 返回 4 个目录 |
| 索引表与文件实际标题 | 索引列出文件均存在于对应子目录 | 抽查任意索引条目 `Test-Path` |
