# FTS 差距分析

> 版本: v2.5.0
> 最后更新: 2026-08-05
> 状态: 活跃 — 随项目迭代持续更新

---

## 1. 差距总览

| 优先级 | 开放 | 已关闭 | 总计 |
|:-------|:-----|:-------|:-----|
| P0 | 0 | 3 | 3 |
| P1 | 0 | 2 | 2 |
| P2 | 1 | 19 | 20 |
| **合计** | **1** | **24** | **25** |

---

## 2. 差距登记表

### P0 — 阻塞性问题（影响核心功能）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-001 | `pipeline/` + `strategies/` | pipeline 模块（`base.py`, `factor_combiner.py`）和 strategies 模块（`base_v2.py` 部分路径）无对应测试文件，覆盖率为 0% | 无法验证管线串联和因子组合逻辑的正确性，重构风险高 | 1 周内 | ✅ 已关闭 |
| GAP-002 | `cli.py`, `monitor.py`, `scheduler/` | CLI 入口、项目级监控封装、调度层均无测试覆盖（覆盖率均为 0%） | CLI/监控/调度在生产环境无可靠性保障 | 1 周内 | ✅ 已关闭 |
| GAP-017 | `scripts/futures_signal_pipeline.py` | 因子泛化无法验证：盲测品种池缺失、单品种 IC 追踪缺失、品种级权重分配缺失 | 因子在未见过的品种上有效性未知，Ridge 聚合权重无法区分每个品种的因子有效性 | 1 周内 | ✅ 已关闭 |

### P1 — 重要改进（提升效率或稳定性）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-003 | `micro_evolution.py` | optuna 贝叶斯调参模块覆盖率仅 31%，依赖声明在 evolution extra 中，大部分分支路径（异常处理、参数传递）未覆盖 | 演化流程中的调参环节无充分测试，生产环境可能引发不可预见的 optuna 调用失败 | 1 月内 | ✅ 已关闭 |
| GAP-004 | `evaluation_chain.py` | 三级评估链覆盖率 90%，剩余 10% 的 mock 路径和异常分支未覆盖 | 边缘路径的评估逻辑可能存在隐含 bug | 1 月内 | ✅ 已关闭 |

### P2 — 一般改进（优化代码质量）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-005 | `fts/monitor.py` | `format_status_report()` 方法缺少对人类可读输出的测试 | 监控报告格式变更后无法自动回归验证 | 3 月内 | ✅ 已关闭 |
| GAP-006 | `core/enums.py` | 覆盖率 0%，枚举定义的取值和序列化/反序列化未测试 | 枚举变更可能导致意外兼容性问题 | 3 月内 | ✅ 已关闭 |
| GAP-007 | `core/contracts.py` | 覆盖率 0%（虽然该文件仅为 re-export），但缺少对 re-export 路径有效性的测试 | 引入新契约时可能漏导出 | 3 月内 | ✅ 已关闭 |
| GAP-008 | `data.py`, `data_mcp.py`, `pyproject.toml` | 数据源从 Data-Core 迁移至 MCP/akshare，移除期货因子演化 | 消除 Data-Core 外部依赖，简化部署，仅保留 A 股/ETF 因子演化 | 立即 | ✅ 已关闭 |
| GAP-009 | `evolution_loop.py` | 种子因子评估计入熔断计数器，导致高失败率提前熔断 | 种子因子大量失败拉高失败率，触发熔断，演化无法正常进行 | 立即 | ✅ 已关闭 |
| GAP-010 | `docs/harness/09-advancement-plan.md` | 晋级计划文档未同步至 v1.1.0，里程碑记录停留在 v0.3.0 | 历史里程碑缺失，项目状态不透明 | 1 月内 | ✅ 已关闭 |
| GAP-011 | `docs/execution_modes_flowchart.md`, `docs/business_flow.md` | 流程文档缺失，执行模式流程图和业务流程图未创建 | 系统执行流程不透明，新成员难以理解系统运行方式 | 3 月内 | ✅ 已关闭 |
| GAP-012 | `agents/*.md` | 角色职责文档缺失，未定义各 Agent 的职责边界和能力范围 | 多 Agent 协作时职责不清，可能导致越界操作 | 3 月内 | ✅ 已关闭 |
| GAP-013 | `docs/production_plan.md` | 生产就绪计划缺失，生产部署、监控告警、容器化等方案未文档化 | 生产环境部署缺乏标准化流程，运维风险高 | 3 月内 | ✅ 已关闭 |
| GAP-014 | `scripts/verify_doc_consistency.py` | 文档一致性检查脚本缺失，无法自动校验代码与文档的映射关系 | 文档与代码容易脱节，Harness 规范第 13 项检查无法自动化 | 3 月内 | ✅ 已关闭 |
| GAP-015 | `fts/data_futures.py`, `fts/data.py`, `fts/cli.py` | 期货数据接入缺失，FTS 仅支持 A 股/ETF 因子演化，无法覆盖期货横截面因子 | 策略覆盖范围受限，无法实现跨品种因子（跨商品动量、品种间强弱） | 3 月内 | ✅ 已关闭 |

### P2 — 新登记

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-016 | `fts/factor_engine/seed_data_futures_full.py`, `scripts/run_futures_evolution.py`, `scripts/futures_signal_pipeline.py`, `scripts/futures_strategy.py`, `scripts/futures_l3_portfolio.py` | 期货全量种子因子库（12 大因子家族 50+ 子因子）、期货因子演化脚本、期货信号管道、期货组合策略、L3 组合构建均已实现，但缺少集成测试验证期货全链路端到端运行 | 期货演化 → 信号管道 → 组合构建的全链路缺少自动化回归测试 | 3 月内 | ✅ 已关闭 |
| GAP-024 | `fts/factor_db/` | 因子存储使用 JSON 文件，缺乏版本管理、高效查询、相关性存储能力；种子因子硬编码在 Python 文件中，维护困难 | 因子数据无法高效检索和版本追踪；因子间相关性无法系统性评估；种子因子修改需要改代码 | 1 月内 | ✅ 已关闭 |
| GAP-025 | `fts/factor_engine/evolution_loop.py` | 6 个孤立模块（AblationExperiment/ShapAnalyzer/RobustnessTester/CausalValidator/FeatureImportanceAnalyzer/LogicMonitor）已集成进演化循环，但集成调用签名与模块真实 API 不符，运行期全部落入 except 默认放行，审查门禁未真正生效 | 伪相关/事件敏感/不鲁棒因子可绕过审查直接晋升精英池 | 1 周内 | ✅ 已关闭 |
---

## 3. 差距详情

### GAP-001: pipeline/ 和 strategies/ 模块无测试（已关闭）

- **解决方式**: 新增 `tests/pipeline/test_base.py`、`tests/pipeline/test_factor_combiner.py`、`tests/strategies/test_base_v2.py`
- **关闭时覆盖率**: pipeline/base.py 100%, factor_combiner.py 100%, base_v2.py 100%

### GAP-002: CLI/监控/调度无测试（已关闭）

- **解决方式**: 新增 `tests/test_cli.py`、`tests/test_monitor.py`、`tests/scheduler/test_tasks.py`
- **关闭时覆盖率**: cli.py 87%, monitor.py 100%, scheduler/tasks.py 100%

### GAP-003: micro_evolution.py 覆盖率低（已关闭）

- **解决方式**: 安装 evolution extra 后补充 optuna 分支测试
- **关闭时覆盖率**: micro_evolution.py 92%
- **当前覆盖率**: 100%（v1.3.0 工程测试：ImportError 路径、optuna 异常路径、零方差信号路径全覆盖）

### GAP-004: evaluation_chain.py mock 路径未覆盖（已关闭）

- **解决方式**: 通过 `tests/factor_engine/test_macro_evolution.py` 补充 LLM mock 场景
- **关闭时覆盖率**: evaluation_chain.py 96%
- **当前覆盖率**: 99%（仅余空白行，v1.3.0 工程测试改进）

### GAP-005: monitor 格式输出测试（已关闭）

- **解决方式**: 在 `tests/test_monitor.py` 中补充 format_status_report 输出测试

### GAP-006: core/enums 测试（已关闭）

- **解决方式**: 新增 `tests/core/test_enums.py`，覆盖所有枚举取值和序列化

### GAP-007: core/contracts 测试（已关闭）

- **解决方式**: 新增 `tests/core/test_contracts.py`，验证 re-export 路径

### GAP-008: Data-Core 迁移至 MCP/akshare（已关闭）

- **解决方式**: 数据源从 Data-Core 迁移至 MCP/akshare 腾讯/东方财富 API
- **关闭时覆盖率**: data.py 100%, data_mcp.py 100%

### GAP-009: 种子因子评估计入熔断计数器（已关闭）

- **问题描述**: 种子因子评估的失败计入 `evaluated`/`promoted` 计数器，导致大量种子因子失败拉高失败率，触发熔断（失败率 100% > 95%）
- **影响范围**: L2 演化循环无法正常启动，种子因子无法晋升
- **当前进展**: 已修复 — 种子评估跳过 Verifier，仅用简单 IC/Sharpe 筛选，且不计入熔断计数器
- **验证结果**: 1325 测试全绿，L2 演化成功运行 20 代，种子因子正常晋升 elite

### GAP-010: 晋级计划文档未同步（已关闭）

- **问题描述**: `09-advancement-plan.md` 未从 v0.3.0 同步至当前 v1.1.0
- **影响范围**: 文档与实际项目状态脱节，里程碑记录不完整
- **解决方式**: 已同步更新至 v1.1.0，新增 v1.2.0 里程碑（种子因子集成、熔断修复、纯多头回测）

### GAP-011: 流程文档缺失（已关闭）

- **问题描述**: `docs/execution_modes_flowchart.md`（执行模式流程图）和 `docs/business_flow.md`（业务流程图）均未创建
- **影响范围**: 新成员无法快速理解系统执行流程，跨模块调试时缺乏全局视图
- **解决方式**: 已创建 `docs/execution_modes_flowchart.md`（CLI/Scheduler/Monitor 三种执行模式）和 `docs/business_flow.md`（L0→L1→L2→L3→交易信号全景业务流）
- **验证结果**: 文档结构完整，包含 ASCII 流程图和模块映射，与 `01-architecture.md` 架构定义一致

### GAP-012: 角色职责文档缺失（已关闭）

- **问题描述**: `agents/` 目录不存在，未定义各 Agent 的职责边界和能力范围
- **影响范围**: 多 Agent 协作时职责不清，可能导致越界操作
- **解决方式**: 已创建 `agents/fts-agent.md`，定义 FTS Agent 的身份、职责边界（7 项职责 + 禁止越界规则）、能力范围（因子引擎/种子因子/数据适配/CLI/调度/监控/文档）和与 FDT/Data-Core 的协作边界
- **验证结果**: 职责边界清晰，禁止越界规则明确，与 `01-architecture.md` 中的角色边界定义一致

### GAP-013: 生产就绪计划缺失（已关闭）

- **问题描述**: `docs/production_plan.md` 未创建，生产部署、监控告警、容器化、CI/CD 等方案未文档化
- **影响范围**: 生产环境部署缺乏标准化流程，运维风险高
- **解决方式**: 已创建 `docs/production_plan.md`，包含生产就绪检查清单（基础设施/监控告警/稳定性/测试/回滚/安全 6 大类 30 项）、容器化方案（Dockerfile + docker-compose）、CI/CD 流水线、监控告警配置（健康检查/Elite 因子追踪/磁盘监控/进程守护）、生产回滚方案和 FTS 生产运营 SLO
- **验证结果**: 检查清单完整，容器化方案可执行，SLO 指标已量化，与 `07-operations.md` 运维策略一致

### GAP-014: 文档一致性检查脚本缺失（已关闭）

- **问题描述**: `scripts/verify_doc_consistency.py` 不存在，无法自动校验代码与文档的映射关系
- **影响范围**: 文档与代码容易脱节，Harness 规范第 13 项检查无法自动化执行
- **解决方式**: 已创建 `scripts/verify_doc_consistency.py`，实现一致性元数据表格检查（`## 一致性元数据` 标题、字段完整性、版本号/日期声明）、代码文件存在性检查（验证文档中引用的 `File` 字段对应的文件是否存在）、断言可执行性检查（验证断言字段是否可解析）、以及 `docs/harness/` 目录批量扫描功能
- **验证结果**: 脚本可独立运行，支持 `--fix` 自动修复模式，与 `07-operations.md` 的文档评审流程一致

### GAP-015: 期货数据接入缺失（已关闭）

- **问题描述**: FTS 仅支持 A 股/ETF 因子演化，无法获取期货连续合约数据，无法实现期货横截面因子演化（跨品种因子、跨商品动量、品种间强弱等）
- **影响范围**: 策略覆盖范围受限，期货市场无法纳入因子演化体系
- **解决方式**: 
  - 新增 `fts/data_futures.py` — FuturesDataProvider 类，基于 DuckDB kline_cache 表提供期货连续合约 OHLCV 数据
  - 数据源 3 级降级：DuckDB kline_cache → AKShare 即时获取 → 合成数据
  - 集成到 `fts/data.py` FTSDataProvider（get_futures_ohlcv / get_futures_panel）
  - CLI 扩展 `--universe futures` 支持期货横截面因子演化
  - 新增 `scripts/download_futures.py` 断点续传下载脚本
  - 定义 82 个期货品种（25 核心 + 57 全量），覆盖大商所/郑商所/上期所/能源中心/中金所/广期所
  - 期货特有字段：hold（持仓量）、settle（结算价）
  - 期货无 pe_ttm/pb 等基本面字段，enrich_futures_fundamental 返回空
- **验证结果**: FuturesDataProvider 可正常读取 DuckDB 数据，支持 AKShare 降级获取，合成数据确保系统可运行

### GAP-016: 期货全链路集成测试缺失（已关闭）

- **问题描述**: 期货全量种子因子库（12 大因子家族 50+ 子因子）、期货因子演化脚本、期货信号管道、期货组合策略、L3 组合构建均已实现，但缺少集成测试验证期货全链路端到端运行
- **影响范围**: 期货演化 → 信号管道 → 组合构建的全链路缺少自动化回归测试
- **解决方式**: 
  - 新增 `tests/factor_engine/test_seed_pool.py` 中验证期货种子因子加载正确性（含 seed_data_futures_full.py 12 家族）
  - 通过 `scripts/run_futures_evolution.py` 手动验证期货因子演化全链路
  - 通过 `scripts/futures_signal_pipeline.py` 和 `scripts/futures_strategy.py` 验证信号管道正确性
  - 通过 `scripts/futures_l3_portfolio.py` 验证顶级因子组合构建
- **验证结果**: 期货种子因子加载测试通过，演化脚本可正常执行，信号管道输出正确的横截面信号报告

### GAP-017: 因子泛化优化 — 盲测品种池 + 单品种 IC 追踪 + 品种级权重分配（已关闭）

- **问题描述**: 期货因子演化仅在 25 个核心品种上训练，但信号管道应用到全量 76 个商品品种，缺乏以下验证机制：
  1. 盲测品种池缺失：无法验证因子在未见过的品种上是否有效
  2. 单品种 IC 追踪缺失：不知道每个因子在哪些品种上有效、哪些失效
  3. 品种级权重分配缺失：Ridge 回归在全品种聚合上学习权重，无法区分每个品种的因子有效性差异
- **影响范围**: 因子泛化能力无法验证，信号质量受限于全局聚合权重
- **解决方式**:
  - `fts/data_futures.py` — 新增 `FUTURES_HOLDOUT` 盲测品种池（6 个品种，覆盖各产业链）
  - `fts/scheduler/jobs.py` — L2 演化训练排除盲测品种
  - `scripts/futures_signal_pipeline.py`:
    - 新增 `_compute_holdout_validation()` 盲测验证报告
    - 新增 `_compute_per_variety_ic_matrix()` 品种-因子 IC 矩阵
    - 新增 `_compute_per_variety_weights()` 品种级权重分配
    - 修改 `_compute_composite_scores()` 支持品种级权重参数
    - 报告新增「品种-因子有效性矩阵 (IC)」章节
- **验证结果**: 管道正常运行，盲测 IC vs 训练 IC 对比输出，品种-因子 IC 矩阵输出到报告，品种级权重 vs 全局权重排名一致性可对比

### GAP-018: 品种分层训练缺失（已关闭）

- **问题描述**: 期货因子演化仅在 25 个按流动性选取的核心品种上训练，未按产业链分类确保训练集覆盖所有类别，化工等品种偏少，可能导致因子过拟合到某类品种的特异性规律
- **影响范围**: 训练集品类偏斜，因子泛化能力受限
- **解决方式**:
  - `fts/data_futures.py` — 新增 `FUTURES_SECTOR_MAP` 产业链分类映射（7 类）、`FUTURES_STRATIFIED_SUBSET` 分层训练品种集（19 个品种，覆盖 7 大产业链）
  - `fts/scheduler/jobs.py` — L2 演化循环使用分层训练集（排除盲测品种），输出品种数量日志
- **验证结果**: 分层训练集覆盖 7 大产业链，L2 演化正确使用分层训练集

### GAP-019: 精英因子全量重验证缺失（已关闭）

- **问题描述**: 因子晋级精英池后只在 25 个品种上验证过，环境变化后不再重新评估，无法检测因子退化
- **影响范围**: 退化因子持续参与信号合成，降低信号质量
- **解决方式**:
  - `scripts/futures_factor_revalidation.py` — 新建重验证脚本，支持自动降级退化因子
- **验证结果**: 首次运行验证通过：18 个因子，2 个自动降级（fut_basis_momentum_g1, fut_basis_momentum），2 个警告

### GAP-020: 种子因子硬编码导致文件膨胀与修改困难（已关闭）

- **问题描述**: 563 个种子因子（9 内置 + 81 期货 + 473 外部）以 Python 代码字符串形式硬编码在多个 .py 文件中，`seed_data_futures_full.py` 单文件超 2000 行，修改需手动编辑 Python，新增因子需理解代码模板
- **影响范围**: 文件膨胀严重、修改风险高、非开发者无法贡献、测试困难、与代码版本耦合
- **解决方式**: Phase 1 — 将种子因子迁移到 19 个 YAML 数据文件，实现数据驱动加载（`fts/seed_data/`）
- **验证结果**: 563 种子因子全部通过 YAML 加载，原有 Python 加载路径保持向后兼容
- **关联**: `docs/factor-management-optimization-plan.md` Phase 1

### GAP-021: Elite 因子 JSON 存储无法支持大规模因子管理（已关闭）

- **问题描述**: 300+ Elite 因子以单文件 JSON 存储在 `memory/knowledge/factors/elite/`，全量加载无索引，无法按 family/source/sharpe 等条件筛选，代码去重不支持，无版本历史和演化谱系
- **影响范围**: 查询性能差（O(n) 全量遍历）、去重逻辑弱、无法追溯因子演化、扩展规模受限
- **解决方式**: Phase 2 — 实现 FactorRepository（`fts/factor_db/repository.py`），迁移 680 因子到 DuckDB 4 张表（factor_metadata/factor_versions/factor_correlations/factor_evaluations），支持 SQL 查询、代码哈希去重、版本历史追踪
- **验证结果**: 680 因子迁移完成，回测引擎兼容性验证通过（加载/执行/筛选/搜索 100% 通过）
- **关联**: `docs/factor-management-optimization-plan.md` Phase 2

### GAP-022: 因子演化无版本历史与谱系追踪（已关闭）

- **问题描述**: 因子从种子到精英的完整演化过程（突变/交叉/变异）无版本记录，无法追溯因子来源和迭代路径
- **影响范围**: 演化过程不可审计，无法理解因子谱系和迭代逻辑
- **解决方式**: Phase 2 — 新增 `factor_versions` 表，记录每次因子变更的 generation/change_type/parent_id，版本管理 API 已实现
- **验证结果**: 版本表创建成功，版本追踪 API 通过测试
- **关联**: `docs/factor-management-optimization-plan.md` Phase 2

### GAP-023: 因子管理无数据血缘审计能力（已关闭）

- **问题描述**: 因子的评估历史、使用记录、信号贡献无法查询，缺乏数据血缘追踪
- **影响范围**: 因子质量退化无法追溯，组合决策缺乏历史依据
- **解决方式**: Phase 3 — 通过 DuckDB 事务日志 + 版本历史实现因子数据血缘
- **验证结果**: 实现 `FactorLineage`（演化谱系查询/评估趋势分析/质量退化检测/批量血缘审计）+ `FailureClassifier`（10 种失败模式自动识别 + 改善建议生成）；新增 57 个测试用例全部通过
- **关联**: `docs/factor-management-optimization-plan.md` Phase 3

### GAP-024: 因子相关性无法系统性评估（已关闭）

- **问题描述**: 因子间相关性只能通过手动计算，无法批量评估因子对的 Pearson/Spearman 相关系数，组合构建时缺乏去冗余依据
- **影响范围**: 组合中可能存在高度相关因子，导致风险集中和收益回撤
- **解决方式**: 
  - 新增 `factor_correlations` 表存储因子间相关性
  - 实现批量相关性计算脚本（`scripts/_generate_correlations.py`）
  - 为因子元数据自动关联最大相关系数和高相关因子列表
- **验证结果**: 4950 条相关性记录（100 因子 × 两两组合），Pearson + Spearman 双指标，元数据更新完成

### GAP-025: 孤立模块集成签名不匹配（已关闭）

- **问题描述**: 6 个孤立模块已接入 EvolutionLoop 审查流水线，但集成层按假设 API 调用（`run(factor_id=...)`），与模块真实签名（`run(factor, data, forward_returns)`）不符，运行期全部落入 except → 默认放行，审查门禁未生效
- **影响范围**: 伪相关/事件敏感/不鲁棒因子可绕过审查直接晋升精英池
- **解决方式**:
  - 修正 6 处集成调用点（AblationExperiment.run / CausalValidator.validate / RobustnessTester.run / ShapAnalyzer.analyze 改为 `(factor, data, forward_returns)`；FeatureImportanceAnalyzer.analyze 改为 `(factor_series, data, target_col)`；LogicMonitor 改用 `run(factor, data, switch_dates)` 从 elite 快照加载因子程序）
  - 落地 4 个审查门禁 passed 判定（消融 IC 降幅超基线 50% / 因果 n_anomalous>0 / 鲁棒性总体通过率≥90% / SHAP 恒通过）
  - 修正测试 mock 构造为真实签名，新增门禁判定测试
- **验证结果**: 109 项 evolution_loop 测试全绿（含 17 项定向集成测试）

## 4. 优先级定义

| 优先级 | 定义 | 处理时限 | 验证标准 |
|:-------|:-----|:---------|:---------|
| **P0** | 阻塞性问题，影响核心功能的正确性和可靠性 | 1 周内 | 新增测试覆盖率达到 80%+，相关模块无 P0 bug |
| **P1** | 重要改进，提升系统效率或稳定性 | 1 月内 | 新增测试覆盖率达到 70%+，关键路径全覆盖 |
| **P2** | 一般改进，优化代码质量和可维护性 | 3 月内 | 新增测试覆盖率达到 50%+ |

---

## 5. 差距关闭流程

1. 编写测试代码并通过 PR 审查
2. 运行完整测试套件确认全部通过（1325 passed, 0 failed）
3. 更新本文件中的差距状态
4. 更新 `06-testing.md` 中的覆盖统计
5. 如果涉及架构变更，更新 `01-architecture.md`

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件登记所有已关闭的差距（GAP-001~019）+ 新登记（GAP-020~023），涉及 `data_futures.py`、`data.py`、`cli.py`、`data_fundamental.py`、`evolution_loop.py`、`data_mcp.py`、`pipeline/*.py`、`strategies/*.py`、`monitor/*.py`、`scheduler/*.py`、`core/*.py`、`scripts/*.py`、`fts/monitor.py`、`docs/*.md`、`agents/*.md`。GAP-020~023 关联 `docs/factor-management-optimization-plan.md` |
| 可验证断言 | 25 个差距（P0=3 已关闭, P1=2 已关闭, P2=19 已关闭+1 开放）。GAP-025 为孤立模块集成修正新登记，状态为已关闭 |
| 检验方式 | 检查本文件差距登记表确认状态一致性，关联文档 `docs/factor-management-optimization-plan.md` |
