# FTS 追赶机构水平全面改造计划（Institutional-Level Transformation Plan）

> 版本: v2.104.0+77
> 最后更新: 2026-08-11
> 状态: 执行中（GAP-I 系列 20 项缺陷全部关闭 ✅ v2.85.0；Stage 1 单机达标 ✅ / Stage 2 单机+轻量并行达标 ✅ / Stage 3 首期完成 ✅ v2.83.0~v2.85.0；**Stage 3C 远期项 8 项（C1~C8）已于 2026-08-11 全部首期实施 ✅，含 C8 延续的 C9 算子扩容二期**——代码/测试/单机验证落地，真实多机集群（C4）、GPU 加速（C5）、卫星/供应链另类源（C2）等部署形态后置，待硬件/基建条件成熟按既有抽象接入）
> 适用范围: FTS 全链路（L0 人类设定 / L1 元循环 / L2 演化 / L3 组合 / L4 信号与实盘反馈 / 基础设施）

> ⚠️ **计划定位说明**：本计划是**机构级对标总纲**，将 FTS 与三类机构基准（中小专业量化团队 / 国内头部量化私募 / 海外顶级量化机构）的全链路差距系统登记为 GAP-I001~I503 缺陷项，按 P0/P1/P2 优先级分三阶段追赶。与既有局部计划（plans/10 演化优化、plans/11 因子挖掘优化、plans/21 期货成熟度、plans/22 股票成熟度、plans/20 期货展期）的关系为「总纲 → 细则」，本计划只登记**机构级结构性差距**，单条缺陷的执行细则由对应子计划承载，不重复登记。

---

## 0. 背景与目标

### 0.1 现状（全链路快照，v2.60.0）

FTS 已具备完整五层架构：L0 Program.md 人类设定 → L1 Meta-Loop（每日知识补给 + L1Verifier 锁定）→ L2 Evolution Loop（股票时序 / 期货横截面双模式，macro/GP/operator 三演化方式，UCT 父选择 + optuna 微调 + 三级评估链 + 6 项强制审计 + 消融/因果/鲁棒性/SHAP 四重审查 + 家族多样性约束）→ L3 Portfolio Loop（因子聚类 + PCA 压缩 + 正交化 + 信号合成六模式 + 粘性约束 + 漂移监控 + Regime 自适应权重）→ L4 信号产出（ScoredSignal 契约 → signal_bridge → 下游 FDT 执行）。

关键现状参数：

| 维度 | 现状 |
|:-----|:-----|
| 种子因子库 | 股票 645（6 YAML）/ 期货 184（20 YAML，17 家族），合计 829 |
| 算子库 | expr_dsl 注册表 ~50 算子（L0-L5 分层）；feature_ops 另一套注册表并存（GAP-S10 双轨） |
| 挖掘吞吐 | 单机串行，每代 1 个后代因子，一次运行 50 代 → 候选量级几十个 |
| 微观演化 | optuna 100 trials（micro_evolution.py `DEFAULT_N_TRIALS=100`） |
| 评估口径 | 三级评估链 + 横截面多空回测；行业/市值中性化 + Barra 风格中性化已接入评估链（GAP-S01/S02） |
| 组合层 | 股票/期货统一 L3（PortfolioLoop 复用：Elastic Net + Regime + 优化器/风险平价） |
| 数据深度 | 日线/分钟线为主（DUCKDB→TQ→AKSHARE→SYNTHETIC 多源），Wind/iFinD 增强，tick 源已接入但历史仅 ~42 分钟 |
| 实盘边界 | FTS 只产信号（ScoredSignal），交易执行由下游 FDT 负责；实盘表现数据未回流 |

### 0.2 目标

1. 将 FTS 全链路与三类机构基准的差距系统登记为可执行缺陷项（GAP-I 系列，含代码依据、机构级标准、实施步骤、测试方案）。
2. 按「先单机后扩展」的资源假设规划三阶段追赶：Stage 1 对标中小专业量化团队（单机达标）→ Stage 2 对标国内头部量化私募（单机 + 轻量并行）→ Stage 3 对标海外顶级（分布式 / GPU / 另类数据）。
3. 每项缺陷遵循「文档先行 → 契约优先 → 测试随重构」HARNESS 闭环，并同步登记到 `08-gap-analysis.md` 与 `09-advancement-plan.md`。

### 0.3 三档机构基准定义

| 档位 | 代表 | 能力画像（本计划对标口径） |
|:-----|:-----|:--------------------------|
| **T1 中小专业量化团队** | 独立工作室、小型私募 | 单机/小型服务器，数千~数万候选因子池，严格过拟合控制，因子库管理系统化，组合层有基础优化，回测-实盘强对齐 |
| **T2 国内头部量化私募** | 幻方 / 九坤 / 明汯 | 海量因子库（十万级），并行挖掘工厂（数百核），深度学习因子与另类数据，行业/风格中性化与 Barra 风险体系，组合优化器（协方差估计/风险平价），因子衰减自动退役，分钟级数据全覆盖 |
| **T3 海外顶级量化机构** | WorldQuant / Two Sigma / Renaissance | 分布式 Alpha Factory（数千核 + GPU），tick/Level2 订单簿与另类数据（舆情/卫星/供应链），机器学习组合层（Black-Litterman 类），实时因子监控与在线学习，全资产多频段统一挖掘 |

### 0.4 调研方法

- 基于 `d:\Programs\factor_system` 实际代码逐文件勘察（v2.60.0），"缺失"判定以代码搜索为据，禁止推测。
- 机构对标实证（2026-08）：WorldQuant BRAIN Fast Expression Language（声明式表达式 DSL、天然向量化、市值中性化）；Microsoft Qlib 表达式引擎（Parser → Operator Tree → Executor 分层）；Barra CNE6 风格因子体系（10 风格回归中性化）；Renaissance/Two Sigma 公开资料（并行挖掘工厂、因子正交化、多目标优化）。
- 与既有差距登记衔接：GAP-S01~S13（plans/22 股票成熟度）、GAP-F01~F16（plans/21 期货成熟度）、GAP-037/041/045/046~051 开放项。

---

## 1. 差距总览（L1→L4 × 三档机构对标矩阵）

| 层 | 维度 | FTS 现状 | T1 中小团队 | T2 国内头部 | T3 海外顶级 |
|:---|:-----|:---------|:-----------|:-----------|:-----------|
| **L1** | 知识补给吞吐 | 每日 1 次 LLM 知识补给 + 批量候选契约校验 + 吞吐指标监控（GAP-I101，v2.72.0）；公告/舆情/宏观多源并行（GAP-I103，v2.82.0）；另类数据源新闻+词典法（C2 首期 2026-08-11） | ✅ 达标 | ✅ 达标 | 🟡（C2 首期：舆情另类数据已上线；卫星/供应链远期） |
| **L1** | Alpha 审查 | 自动 L1Verifier + FactorReviewWorkflow 人审骨架（状态机+意见回写+CLI 队列）+ 驳回意见入经验链（GAP-I102，v2.72.0 骨架 / v2.82.0 二期）；在线人审工作台 + 机审/人审可配置 + 异常因子模拟审批全流程（C8/C8-2，2026-08-11） | ✅ 达标 | ✅ 达标 | ✅（C8：在线人审工作台 /review + 批量机审 ReviewMode/AutoReviewPolicy + 5 个异常因子模拟审批走通） |
| **L2** | 挖掘吞吐 | 批量候选生成（batch_size=20）+ 向量化批量粗筛漏斗 + 多进程 ExecutorBackend 批量评估（GAP-I201 批量漏斗 v2.71.0 + GAP-I502 v2.83.0 并行）；Dask LocalCluster 分布式工厂（C4，2026-08-11） | ✅ 达标（吞吐 ≥10×） | ✅（C4：Dask LocalCluster 并行工厂，实测吞吐 ≥ process 3.58x） | 🟡（C4 分布式代码/单机验证就绪，多机集群部署后置） |
| **L2** | 算子库 | ~50 算子 + 组合/跨标的算子双注册表单一事实源（GAP-I202，v2.75.0）；C8/C9/D10~D17 扩容至 512 算子（2026-08-11） | ✅ 达标 | ✅（C8/C9/D10~D17：已扩容至 512，机构级数百达成） | ✅（同上） |
| **L2** | 搜索方法 | macro/GP/operator 三模式 + 多目标适应度 + 符号回归 + Pareto 前沿 + GRU 深度因子（GAP-I204 v2.71.0/v2.78.0 + GAP-I203 v2.73.0）；轻量 Transformer（C5，2026-08-11） | ✅ 达标 | ✅ 达标 | 🟡（C5：轻量 Transformer 已落地；GAN 合成远期） |
| **L2** | 过拟合控制 | walk-forward+多重检验+消融+因果+鲁棒性 + 成本/容量约束建模（GAP-I501 联动）；冲击成本实证标定+融资成本（C7，2026-08-11） | ✅ **局部领先** | ✅ 达标 | 🟡（C7：实证标定+融资成本已实现；实时成本监控远期） |
| **L2** | 中性化 | 行业/市值中性化 + Barra 风格中性化 + 期货板块中性化 + 全市场 Barra 暴露覆盖（GAP-S01/S02/F03 + I304 v2.79.0） | ✅ 达标 | ✅ 达标 | ✅ 达标 |
| **L2** | 去冗余 | 相关性预检+家族上限+正交化闭环+正交基底（L2 高相关 OLS 残差入库，Gram-Schmidt 多因子基底迭代残差化，L3 不重复剔除） | ✅ 达标 | ✅ 达标 | ✅ 达标 |
| **L3** | 组合层 | 股票/期货统一 L3（PortfolioLoop 复用：聚类/PCA/正交化/六模式合成/Regime） | ✅ 达标 | ✅ 达标 | ✅（C3：Black-Litterman 观点融合已接入） |
| **L3** | 组合优化器 | Elastic Net + Regime + 风险平价/均值方差 + Ledoit-Wolf 收缩 + Black-Litterman 闭式后验（optimizer 模式，C3 2026-08-11） | ✅ 达标 | ✅ 达标 | ✅（C3：BL 观点权重，零观点退化性质） |
| **L3** | 衰减管理 | elite_tracker + 定期复审 + 自动退役闭环（滚动 6M IC 斜率分级 + FACTOR_DECAY 反馈联动，v2.72.1 GAP-I305）；decayed 因子自动入重校准队列（C6，2026-08-11） | ✅ 达标 | ✅ 达标 | ✅ 达标 |
| **L4** | 反馈闭环 | 信号→FDT，实盘成交/净值回流 + 实盘 vs 回测 IC 对比 + 退役建议（GAP-I401，v2.71.0）；自动重校准队列实盘反馈驱动微调（C6，2026-08-11） | ✅ 达标 | ✅（C6：自动重校准队列 + elite 参数回写） | 🟡（C6 首期：实盘反馈重校准已实现；在线学习深化远期） |
| **L4** | 在线监控 | live_factor_monitor 接入实盘 IC + 衰减告警（GAP-I402，v2.77.0） | ✅ 达标 | ✅ 达标 | 🟡（在线学习深化/实时仪表盘远期） |
| **基础** | 回测保真 | 涨跌停/停牌/展期/冲击成本/容量约束+容量分析报告已建模；冲击成本实证标定脚本 + 融资成本（C7，2026-08-11） | ✅ 达标 | ✅（C7：冲击成本 log-log 幂回归实证标定） | 🟡（C7：融资成本已实现；实时成本监控远期） |
| **基础** | 数据深度 | 日线/分钟线 + tick 历史缓存增量累积 + Level2 订单流因子（GAP-I503 首期，v2.84.0）；舆情另类数据新闻+词典法 + LLM 精修一致性验证（C2，2026-08-11） | ✅ 达标 | ✅（C2：另类数据已上线） | 🟡（C2 首期：舆情+LLM 精修已实现；卫星/供应链远期） |
| **基础** | 计算资源 | 单机多进程执行器可插拔抽象（ExecutorBackend，GAP-I502 v2.83.0）；Dask 分布式工厂单机验证（C4，2026-08-11） | ✅ 达标 | ✅（C4：LocalCluster 本地并行工厂） | 🟡（C4 分布式代码就绪 + C5 Transformer CPU 已落地；多机/GPU 部署后置） |

> 注：2026-08-11 起上表残留的 🟡/🔴 单元格已由 §3.3 C1~C8 **全部首期实施** 逐项承接——C1 Level2 因子生成器（含评估晋升接线）/ C2 另类数据（舆情，词典法 + LLM 精修一致性验证） / C3 ML 组合层（BL） / C4 分布式工厂（单机 LocalCluster） / C5 轻量 Transformer / C6 自动重校准 / C7 回测保真实证化 / C8 基础设施深化（人审工作台 + 算子扩容 C8 22 + C9 30 + D10~D17 380，DSL 80→**512**）。仍保留 🟡 的单元格为**部署形态后置或远期深化**项：多机集群/GPU 部署（C4/C5）、卫星/供应链另类源（C2）、GAN 合成（C5）、实时成本监控/在线学习深化（C7/C6）——代码与单机验证均已落地，待硬件/基建条件成熟按既有抽象接入。

---

## 2. 缺陷清单（GAP-I 系列，按优先级）

### 2.1 P0 — 阻塞性差距（单机可达，直接影响信号真实性）

#### GAP-I201 挖掘吞吐不足（单机串行，候选量级差 2~3 个数量级）（P0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `EvolutionLoop.run()`（evolution_loop.py L433）主循环 `for generation in range(...)` 每代仅生成 **1 个**后代因子（L565-681），演化方式串行分派（macro→GP→operator 逐级 fallback）；`_quick_prefilter`（L2441）与 `_check_factor_runtime`（L2504）为单因子拦截，无批量通道；评估链 `evaluation_chain.evaluate` 单因子执行 |
| **机构级标准** | 挖掘工厂化：一次运行批量生成（如 WorldQuant 每夜数万候选），粗筛→精筛分级漏斗（秒级粗筛 → 分钟级精筛），批量向量化评估 + 多进程并行 |
| **影响** | FTS 一夜 50 代约几十候选 vs 机构数万 → 同等时间窗口内命中精英因子的期望差 2~3 个数量级；这是与机构差距的核心根因 |
| **实施步骤** | ① 引入「批量候选生成 + 批量粗筛」漏斗：每代生成 N（如 20~50）个后代（LLM 批量产出 / GP 种群批量评估 / operator 随机组合批量生成），`_quick_prefilter` 改为向量化批量拦截；② 粗筛通过者进细评估队列（ProcessPoolExecutor 多进程并行评估，先单机多核）；③ 保持单因子审计链不变（审计为精筛层） |
| **完成记录** | ✅ v2.71.0（批量漏斗）：新建 `fts/factor_engine/batch_mining.py`——`BatchMiner`：`BatchMiningConfig(batch_size=20)` 每代批量生成 N 个后代（`generate_batch` 依赖注入 `generate_cb`，逐 i 生成失败者不计入）+ `filter_batch` 向量化批量粗筛（单因子 `_filter_one` 拦截逻辑向量化对齐）+ `run_iteration` 迭代闭环；`evolution_loop.py`（L1216-1229）接入 `BatchMiner`（`generate_cb=self._batch_generate_one` 方法轮换 + seed 递增），每代 1 个后代 → batch_size 个候选，吞吐 ≥10× 达成；✅ v2.83.0（批量并行）：`BatchMiner.filter_batch` 批量粗筛接入 `ExecutorBackend`（`BatchMiningConfig.executor_backend`/`executor_max_workers`，GAP-I502 联动），批量评估多进程并行；新增 batch_mining 11 + executor_backend 14 用例（四后端行为一致性/异常隔离） |
| **测试方案** | 批量生成数量断言；批量粗筛与单因子拦截结果一致性；多进程评估结果与串行逐一对齐（误差 < 1e-9）；吞吐基准测试（每秒评估因子数，防止性能退化） |

#### GAP-I207 股票因子行业/市值中性化未接入主流程（P0）

> 与本计划关联：plans/22 GAP-S01（P0，v2.60.0 阶段 A 处理中）。本计划不重复登记，仅将其纳入 Stage 1 完成门槛。

| 维度 | 内容 |
|---|---|
| **代码现状** | `cross_section_evaluate_backtest` 已实现 `_neutralize_signal_matrix`（evaluation_chain.py L670-750），但 `industry_map`/`cap_map` 默认 None 即跳过；股票 CLI 分支（cli.py L224-254）不传映射；`settings.py` 的 `stock_neutralization=true` 为死配置 |
| **机构级标准** | 截面因子评估前必须行业去均值 + 市值中性化（可选流动性/波动率），IC 在残差上计算，剥离"伪预测力" |
| **影响** | 股票因子 IC 含行业/市值偏好污染 → 污染 L2/L3 全链路结论（详见 plans/22 GAP-S01） |
| **实施步骤** | 按 plans/22 GAP-S01 执行（P0，Stage 1 门槛） |
| **测试方案** | 见 plans/22 GAP-S01 |

#### GAP-I301 股票流水线缺 L3 组合层（P0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 期货侧有完整 L3（`PortfolioLoop` + Elastic Net 合成 + Regime-aware 权重 + 因子聚类 + 正交化 + 粘性约束）；股票侧仅有 `scripts/daily_signal_pipeline.py` 等权求和取信号排名，无权重学习、无组合优化、无 Regime、无成本约束 |
| **机构级标准** | 股票与期货共用同一组合层框架：权重学习（Ridge/ElasticNet）→ 因子正交化 → 组合优化（约束：多头、行业中性、换手上限）→ 信号输出 |
| **影响** | 股票 alpha 无法形成有效组合；信号管道粗糙（方向翻转无校正、无成本约束），实盘落地风险高 |
| **实施步骤** | ① 抽取期货 L3 组合层为公共组件（`portfolio_constructor` 复用）；② 股票演化接入 L3（Elastic Net 合成 + Regime 权重 + 多头约束）；③ 股票信号管道复用期货方向校正 + Ridge 权重学习（GAP-S04 联动） |
| **完成记录** | ✅ v2.68.0：股票 L3 组合层复用期货组件——`PortfolioLoop(market="stock")` + CLI `portfolio run --universe stock` + `load_elite_factors` market 过滤 + `synthesize_signals` Elastic Net/Sharpe 权重 + Step 2.5 stock_regime 风格自适应 + `build_combo` 多头组合/成本模型 net 指标；CLI 股票分支 L3 完成后自动触发 `daily_signal_pipeline`（与期货对称）；`TestStockL3PortfolioLayer` 6 用例 + `TestCmdPortfolioRunStock` 3 用例 |
| **测试方案** | 股票 L3 与期货 L3 组件复用性断言；TopN 组合回测 Sharpe/回撤；成本模型开启后 alpha 仍为正 |

#### GAP-I501 回测成本/容量保真不足（冲击成本 + 容量限制未建模）（P0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 期货侧已建 `cost_model.py` / `cost_simulator.py`（手续费/滑点），`backtest_pipeline.py` 已实现涨跌停拦截 + 停牌过滤 + 展期成本（v2.58.0~v2.59.0）；但无**冲击成本模型**（按成交量占比分段冲击）、无**容量限制**（信号量超过品种日均成交额即降级/截断） |
| **机构级标准** | 回测必须含：冲击成本（square-root 模型或分段线性）+ 容量上限（持仓 ≤ 日均成交额比例）+ 融资成本；大资金策略容量分析是机构立项前置 |
| **影响** | 大权重信号高估收益；策略容量不可知 → 实盘规模无法规划；违反"回测与实盘强对齐"红线 |
| **实施步骤** | ① `cost_model.py` 增加冲击成本函数（输入成交量占比 → 冲击 bp）；② `backtest_pipeline.py` 增加容量约束（signal 持仓市值 ≤ 品种日均成交额 × 系数，超限截断并记录）；③ 回测报告输出容量上限结论 |
| **测试方案** | 冲击成本单调性测试；容量超限截断行为；容量分析报告单测 |

#### GAP-I401 实盘反馈闭环缺失（信号→FDT 后无表现回流）（P0）

| 维度 | 内容 |
|---|---|
| **代码现状** | FTS 通过 `signal_contract.py` ScoredSignal + `bridge/signal_bridge.py` 输出信号给下游 FDT；`feedback_loop.py` 存在 4 张反馈表 + CLI，但**实盘成交/净值数据无自动回流通道**（FDT 未回传，Grep 无实盘表现数据源接入） |
| **机构级标准** | 因子表现闭环：实盘成交 → 归因（信号 vs 执行差异）→ 因子 IC 衰减修正 → 自动退役/重校准 |
| **影响** | L2/L3 因子状态基于历史回测，无法感知实盘漂移；衰减退役无实盘依据 |
| **实施步骤** | ① 定义实盘反馈契约（`LiveFeedbackRecord`：factor_id/信号日/信号值/持仓收益/换手/滑点），提供 CLI 导入与 DuckDB 表；② `feedback_loop.py` 增加实盘 IC 计算与回测 IC 对比报告；③ 衰减退役逻辑（GAP-I305）接入实盘反馈 |
| **完成记录** | ✅ v2.71.0（①②由 GAP-L402 v2.66.0 落地：`LiveFeedbackRecord` 契约 + `validate_live_feedback_record` + `LiveFeedbackImporter`（CSV/dict 批量导入 + DuckDB `feedback_live` 表追加落盘 + 截面 Spearman 实盘 IC）+ `LiveVsBacktestICReport`（实盘 vs 回测 IC 对比 + 衰减判定 decayed/weak/ok）+ CLI `fts feedback import`/`fts feedback live-ic`；③ v2.71.0 补强：`LiveVsBacktestICReport.generate` 输出 `recommend_retire`（decayed→True）/`decay_gap` 字段与 summary `n_recommend_retire`——衰减因子携带退役建议，供 GAP-I305 自动退役闭环消费；test_feedback_loop 25 passed） |
| **测试方案** | 反馈记录契约校验；实盘 IC vs 回测 IC 对比报告；导入异常降级 |

### 2.2 P1 — 重要差距（单机 + 轻量并行可达）

#### GAP-I101 L1 知识补给吞吐与知识源单一（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | `meta_loop.py` 每日一次 BootstrappingChain 知识补给，知识源为券商研报 / arXiv 论文；无批量候选生成、无另类知识源（舆情/公告/宏观事件） |
| **机构级标准** | L1 层多路知识源并行（研报/论文/公告/舆情/宏观日历），每日批量注入候选，人审前置 |
| **影响** | L1 注入 L2 的候选量小、维度单一，种子库增长慢 |
| **实施步骤** | ① 知识源扩展（公告/舆情接入 iFinD 新闻，宏观日历接入 EDB）；② 单次补给批量产出 N 个候选（复用 GAP-I201 批量漏斗）；③ L1 候选吞吐指标监控 |
| **完成记录** | ✅ v2.72.0（首期，①② 中批量候选契约校验 + ③ 吞吐指标）：`meta_loop.py` 新增 `validate_batch_candidates` 批量候选契约校验（candidate_id/name/code/economic_logic.narrative 逐条校验 + total/valid/invalid/invalid_samples 统计，invalid_samples 截断 5）接入 `_run_bootstrap` 前置质量门（契约不合规仅告警不熔断）；`MetaRunResult` 新增 `candidates_per_minute` 吞吐指标（候选数 / 运行分钟，`_make_result` 计算，elapsed=0 防除零）；知识源多路扩展留二期 v2.80.0；新增 `TestValidateBatchCandidates` 8 用例（全合法/空列表/缺必填字段 ×3/非 dict/样本截断/吞吐计算/零耗时） |
| **测试方案** | 多知识源注入单测；批量候选契约校验 |

#### GAP-I102 无 Alpha 审查 / 人机协同工作台（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | 晋升完全自动化（Verifier + 质量卡 + 审计 + 四重审查），无人工审查环节；`factor_inspector.py` 仅提供因子信息查看，非审查工作流 |
| **机构级标准** | WorldQuant 模式：自动 pipeline + Alpha 审查委员会（人审经济逻辑/数据依据/风险），人审结论回写因子库 |
| **影响** | 高 IC 但经济逻辑存疑的因子可能通过自动审查；缺少人工兜底与知识沉淀 |
| **实施步骤** | ① `factor_inspector.py` 升级为审查工作流（审查状态机：pending→approved/rejected，意见回写 DuckDB）；② CLI 增加 `factor review` 子命令 + 审查队列；③ 审查意见接入经验链 |
| **完成记录** | ✅ v2.72.0（骨架）：`factor_inspector.py` 新增 `FactorReviewWorkflow` 审查工作流——`ReviewDecision` 状态机（pending→approved/rejected），`approve`/`reject` 意见回写 DuckDB `factor_reviews` 表（幂等 UPSERT，重复审查覆盖旧决定），`list_pending` 待审查队列（NOT EXISTS 排除已审查 + market 过滤 + limit 上限 + created_at 倒序），`get_status` 状态查询；CLI `fts factor review list/approve/reject` 子命令（--market/--limit/--db/--comment）；schema E.1 `_CREATE_FACTOR_REVIEWS`（factor_id 主键/decision/comment/reviewer/reviewed_at + decision 索引）；审查意见接入经验链留二期 v2.80.0；新增 `TestReviewCliCommands` 4 用例（list 队列/market 过滤/approve 回写/reject 回写）补强 test_review_workflow 7 用例（状态机/回写/队列/幂等/意见落盘） |
| **测试方案** | 审查状态机单测；审查意见回写断言 |

#### GAP-I202 算子库规模与语义体系（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | expr_dsl 注册表 ~50 算子（L0-L5），feature_ops 另一套并存（GAP-S10 已登记双轨）；无 A 股特有算子（GAP-S12 已登记） |
| **机构级标准** | 数百算子，含组合/条件/时序/截面/跨标的/另类数据算子；单一事实源 |
| **影响** | 搜索空间小 → 演化产出多样性不足；双轨漂移（GAP-S10） |
| **实施步骤** | ① 按 plans/22 GAP-S10 合并注册表（单一事实源）；② 算子扩充：组合数学（corr/regression residual/quantile bucketing）、跨标的（cross-section rank/demean）、A 股特有（GAP-S12）；③ 每个算子配套经济语义 + 参数边界 + 单元测试 |
| **完成记录** | ✅ v2.75.0（组合/跨标的算子单一事实源）：① `feature_ops.py` `RollingOps` 新增 `ts_slope`（滚动线性回归斜率，局部趋势强度/方向，NaN 安全降级）与 `ts_quantile`（滚动分位数，q∈[0,1] 越界抛 ValueError）原语；② `feature_ops.OperatorRegistry`（GP 演化侧）注册 8 个组合/跨标的算子（combo 类目）：ts_slope/ts_quantile + GAP-L401 的 regression_residual/quantile_bucket/cross_section_demean/if_else/corr/cross_section_rank——与 expr_dsl 共用 RollingOps/PriceOps 底层原语，双轨漂移消除（此前 L4 组合算子仅 expr_dsl 侧，GP 侧不可用 = 搜索空间未共享）；③ `expr_dsl/registry.py` 注册 ts_slope/ts_quantile（L1，参数边界 + PIT lookback + 经济语义）；`verify_registry_consistency` 新增 `required_shared` 硬约束（8 个组合/跨标的算子必须双注册表共享，仅存在于单侧即判不一致，输出 `unshared_required`）；④ `operator_evolution.py` `_evaluate_fitness` 新增 lookback=0 罚分（`compute_max_lookback==0` 纯字段/无算子表达式如 `rank(close)`，与常信号罚分同档 _PENALTY_WEAK）——算子演化产物必须包含实际算子变换，避免裸字段包装在单调合成数据上以虚假高 IC 占据最优；⑤ 每个新算子配套经济语义 + 参数边界 + 单元测试（新增 7 用例：ts_slope/ts_quantile 元数据/功能/边界 + GP 注册表含组合算子/可调用 + required_shared 一致性 + DSL 执行），315 定向回归全绿（expr_dsl + operator_evolution + evolution_loop + gp_evolver） |
| **测试方案** | 注册表一致性断言；新算子边界测试；GP 与算子演化共用注册表回归 |

#### GAP-I203 深度因子学习缺失（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | `ml/models.py` 已实现 LightGBM/XGBoost/Ensemble + 轻量纯 numpy MLP（GAP-F05，v2.60.0），但无 LSTM/GRU/Transformer 时序深度模型、无 GAN/VAE 因子合成（GAP-037 开放） |
| **机构级标准** | 深度时序特征提取（LSTM/Transformer 端到端收益预测）作为候选因子来源之一；生成式模型合成因子/合成行情做数据增强 |
| **影响** | 候选因子缺少深度非线性特征维度；信号合成停留在浅层模型 |
| **实施步骤** | ① 轻量 LSTM/GRU 因子模型（纯 numpy 或 PyTorch CPU 路径，可解释性约束：输出映射为因子信号 + SHAP 归因）；② 深度因子作为候选源接入 L2 批量漏斗（GAP-I201），过全套审计链；③ 远期：GAN 合成因子 |
| **完成记录** | ✅ v2.73.0（首期，深度时序模型 + L2 接线）：① `fts/ml/models.py` 新增 `GRUFactorModel` 轻量纯 numpy 单层 GRU（update/reset gate + candidate hidden，BPTT + 动量 SGD + L2 正则；输入 (n, seq, f) 滚动窗口序列，训练前 z-score 标准化；样本不足/非数值/维度不匹配/未训练抛 `ModelNotAvailableError` 降级；`get_params` 导出 11 组权重供因子 code 内嵌；`create_gru_model` 工厂 + `__all__` 导出；修复 numpy 2.x 标量转换 `float()`→`.item()`）；② `fts/ml/deep_factor.py` 新增 `DeepFactorGenerator`/`create_deep_factor`（`DeepFactorConfig` lookback/horizon/hidden/epochs/lr/train_ratio/min_samples/seed）——OHLCV 特征（日收益率+量变化率）→ 滚动窗口样本 → 前 train_ratio 训练 GRU → 权重序列化内嵌 `def factor_program(data, params)` code（零未来函数：特征窗口 [t-lookback+1, t] 逐 t 滚动推理 + tanh 压缩输出 ∈ [-1,1]；样本不足/训练失败返回 None 降级；factor 契约完整：factor_id/name/code/signature/economic_logic/source=deep_evolution/family=deep/market/deep_model 元数据含 val_ic）；③ `evolution_loop.py` `_evolve_one` 新增 method_hint="deep" 分派——`_run_deep_evolution`（数据/样本校验 → create_deep_factor → parent_id/generation/trace_id 血缘回填，失败抛 RuntimeError 由调用方降级回退）；`_batch_generate_one` 批次轮换并入 deep（idx%3==2，macro/gp/deep/operator 循环）——深度因子作为候选源接入 L2 批量漏斗（GAP-I201）过全套审计链；④ 新增 `tests/test_gru_factor.py` 28 用例（GRU 模型级 11 + DeepFactor 生成器集成 9 + EvolutionLoop 接线 8，含零未来函数截断一致性验证与生成 code 经 `_execute_factor_code` 可执行验证）；⑤ 远期：GAN 合成因子（GAP-I503 二期/远期 3C） |
| **测试方案** | 深度模型输出信号与标签相关性；过拟合控制（OOS 窗口）；SHAP 归因断言 |

#### GAP-I204 搜索方法单一（缺符号回归 / 多目标优化 / 贝叶斯搜索）（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | GP 适应度单一（`fitness_metric: ic/sharpe/ic_sharpe_combo`，gp_evolver.py），无 Pareto 多目标（IC×换手×衰减×容量）；无符号回归；贝叶斯优化仅用于 optuna 参数（非结构搜索） |
| **机构级标准** | 多目标进化（NSGA-II 类 Pareto 前沿）+ 换手/衰减惩罚项 + 容量感知适应度 |
| **影响** | 单一适应度导致产出高 IC 高换手因子，实盘成本侵蚀收益；无容量感知 |
| **实施步骤** | ① GP 适应度扩展为多目标（IC、换手惩罚、衰减半衰期）；② 增加换手率与成本纳入适应度（复用 GAP-I501 成本模型）；③ 输出 Pareto 前沿供人审 |
| **完成记录** | ✅ v2.71.0（首期，IC×换手×衰减）：`gp_evolver.py` 新增 `multi_objective` 适应度模式——`FitnessResult` 扩展 `turnover`/`decay` 字段（换手=信号逐日绝对变化均值/信号标准差无量纲归一；衰减=训练集按时间等分两半的 \|IC\| 前段相对后段衰减比例）；`GPEvolverConfig` 新增 `turnover_penalty`（默认 0.3）/`decay_penalty`（默认 0.3）系数；合成适应度 `fitness = \|ic\|×0.6 + max(sharpe,0)×0.2 − turnover_penalty×min(turnover,5) − decay_penalty×decay`；`GPEvolveResult` 新增 `best_turnover`/`best_decay`；默认 `ic_sharpe_combo` 模式保持原逻辑不变但同样填充 turnover/decay 指标；新增 `TestGapI204MultiObjective` 7 用例（字段填充/换手度量/换手惩罚/系数放大 ×2/衰减惩罚/端到端 evolve）。**补充（二期，v2.78.0）**：`pareto.py` 新增 `ParetoItem`/`fast_non_dominated_sort`/`compute_pareto_front`（NSGA-II 快速非支配排序，多目标 \|IC\|/Sharpe/−turnover/−decay 统一「越大越好」口径，前沿按 fitness 降序供人审）；`symbolic_regression.py` 新增 `SymbolicRegressionSearcher` 确定性 beam-search 层级搜索（单字段出发逐层一元包装 + 二元组合，复用 `GPEvolver._evaluate_fitness` 同口径多目标评估，每层保留 top-K，固定种子可复现，`SymbolicRegressionConfig` max_depth/beam_width/max_candidates/min_fitness 配置化）；`GPEvolver.evolve()` multi_objective 模式跟踪全部已评估个体提取 Pareto 前沿，`GPEvolveResult` 新增 `pareto_front` 字段（含 source=gp/symbolic 标识），`GPEvolverConfig` 新增 `symbolic_regression_enabled`/`symbolic_max_depth`/`symbolic_beam_width`/`symbolic_max_candidates`（默认关闭，不改变默认行为）；新增 `test_pareto.py` 12 用例 + `test_symbolic_regression.py` 15 用例（含 GP 集成：symbolic 前沿合并、multi_objective 前沿输出） |
| **测试方案** | 多目标适应度单测；换手惩罚生效断言；Pareto 前沿输出 |

#### GAP-I205 微观演化效率（optuna 100 trials 串行）（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | `micro_evolution.py` `DEFAULT_N_TRIALS=100` 串行，随机搜索无早停（L137），每因子约 100 次评估 |
| **机构级标准** | 参数调优与粗筛漏斗融合：粗筛阶段用网格抽样快速淘汰，精筛阶段贝叶斯调优 + 早停 |
| **影响** | 每候选固定 100 trials 的评估成本高，且低潜力候选浪费算力 |
| **实施步骤** | ① 粗筛阶段低 trials（如 20）快速打分，淘汰低潜力；② 精筛阶段 100 trials + 早停（TPE 已有，开启早停）；③ trials 数按粗筛得分自适应 |
| **测试方案** | 早停路径测试；粗筛淘汰率与精筛结果一致性 |

#### GAP-I206 L2 准入去冗余/正交化闭环缺失（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | L2 晋升仅做种子相关性预检（标记不删除，横截面 >50 种子跳过）与家族数量上限；`orthogonalize_factors` / `FactorClusteringEngine` / `PCASignalCompressor` 仅在 L3 组合阶段使用，L2 准入无正交化 |
| **机构级标准** | 因子入库即去冗余：新因子与既有 elite 计算相关矩阵，高相关（>0.9）→ 正交化残差入库或拒绝；维护因子正交基底 |
| **影响** | elite 池相关性膨胀 → L3 组合夏普被稀释、换手成本非线性增长（已记录的教训） |
| **实施步骤** | ① `_promote_to_elite` 增加与既有 elite 的相关性检查（>0.9 拒绝或正交化残差晋升）；② 晋升报告输出相关性明细；③ 正交化在 L2 与 L3 统一口径 |
| **完成记录** | ✅ v2.71.0：L2 准入去冗余——`_check_elite_correlation`（`evolution_loop.py`）：演化因子晋升前扫描既有 elite 快照（排除 `_l2_seed_correlation_index.json`），复用 `BacktestPipeline._execute_factor_code` 逐个计算信号与候选因子做 Pearson 相关，相关绝对值 ≥ 阈值（默认 0.9）记录高相关对（`factor_name_b`/`factor_id_b`/`pearson`/`abs_pearson`，按 abs 降序）拒绝晋升并打日志；无既有 elite / 执行失败 / 全低相关返回 None 静默放行；容量护栏 `l2_elite_corr_max_scan`（默认 50）限制扫描数；种子因子（shadow_observe=False 首轮导入）跳过检查；`settings.py` 新增 `l2_elite_corr_threshold`/`l2_elite_corr_max_scan`/`l2_elite_corr_debug` 配置 + `FTS_L2_ELITE_CORR_*` 环境变量；新增 `tests/factor_engine/test_l2_elite_redundancy.py` 10 用例（高相关拦截/负高相关 abs/低相关放行/空 elite/索引跳过/容量护栏/执行失败容错 + 集成：shadow 拦截/种子跳过/低相关晋升） |
| **补充记录（正交化闭环）** | ✅ v2.71.0：高相关因子不再一刀切拒绝——`_orthogonalize_candidate`（`evolution_loop.py`）：对候选信号关于参照 elite 信号做 OLS 回归取残差，残差与参照因子相关 < `l2_orthogonal_residual_corr_max`（默认 0.3）且保留比 > `l2_orthogonal_min_retained_ratio`（默认 0.3）时，以正交化版本入库（factor JSON 含 `orthogonalized`/`orthogonalized_against`/`orthogonalized_pearson`/`orthogonal_signal` 残差快照，DuckDB metadata 同步持久化）；残差不合格拒绝兜底；`settings.py` 新增 `l2_elite_orthogonalize`/`l2_orthogonal_residual_corr_max`/`l2_orthogonal_min_retained_ratio` 配置 + `FTS_L2_ELITE_ORTHOGONALIZE`/`FTS_L2_ORTHOGONAL_*` 环境变量；L3 消费——`orthogonalize_factors` 对已正交化因子不重复剔除（避免双重去冗余），`load_elite_factors` DuckDB/JSON 双路径透传正交化元数据；新增 `tests/factor_engine/test_l2_orthogonalize.py` 10 用例（残差生成/正交性/保留比不足拒绝/参照缺失降级 + 集成：正交化入库/拒绝兜底/开关关闭 + L3 放行） |
| **补充记录（多因子正交基底）** | ✅ v2.72.1：`orthogonal_basis.py` 新增 `OrthogonalBasisManager`——Gram-Schmidt 多因子正交基底：基底 = 按 Sharpe 降序保留上限（默认 10）的两两近似正交精英因子；L2 准入 `_orthogonalize_via_basis`（`evolution_loop.py`）对候选信号关于基底逐因子 OLS 残差化（迭代投影），残差与基底最大相关 < `l2_orthogonal_residual_corr_max` 且保留比 > `l2_orthogonal_min_retained_ratio` 时以正交化版本入库并注册为新基底成员（`orthogonalized_basis` 基底成员名列表），基底索引持久化 `{memory_dir}/orthogonal_basis.json`；基底不可用/失败回退单参照 OLS；DuckDB metadata 与 L3 `load_elite_factors` 透传 `orthogonalized_basis`；`settings.py` 新增 `l2_orthogonal_basis_enabled`/`l2_orthogonal_basis_max_size`/`l2_orthogonal_basis_min_sharpe` 配置 + `FTS_L2_ORTHOGONAL_BASIS_*` 环境变量；新增 `tests/factor_engine/test_orthogonal_basis.py` 19 用例（IC 斜率/衰减分级/基底读写/注册上限/Gram-Schmidt 正交/弱候选拒绝/L2 集成降级） |
| **测试方案** | 高相关因子晋升拦截；正交化残差与原始因子相关性≈0 |

#### GAP-I302 组合优化器机构化（风险平价 / 协方差收缩 / 均值方差）（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | 期货 L3 用 Elastic Net + Regime 权重（`synthesize_signals` 五模式），`portfolio_optimizer.py` 已实现但未在股票流水线使用；无协方差收缩估计（Ledoit-Wolf）、无风险平价/均值方差目标 |
| **机构级标准** | 组合层提供可选优化器：均值方差（协方差收缩）/ 风险平价 / Elastic Net，约束：多头、行业中性、换手上限；因子协方差矩阵估计稳健化 |
| **影响** | 组合权重对协方差噪声敏感；无风险预算视角的权重分配 |
| **实施步骤** | ① `portfolio_optimizer.py` 增加 Ledoit-Wolf 收缩估计 + 风险平价求解；② 股票 L3（GAP-I301）接入优化器；③ 优化器参数走配置 |
| **完成记录** | ✅ ①② v2.61.0（GAP-L302/L303/L304/L305）：Ledoit-Wolf 收缩协方差（`risk_model.RiskModelEstimator` 纯 numpy，对角结构化目标 + 收缩强度估计，正定性保证）+ 风险平价/均值方差（`PortfolioOptimizer`：risk_parity 迭代等风险贡献 / mean_variance scipy SLSQP，含杠杆/集中度/换手/VaR/暴露中性化/容量约束，无 scipy 降级 numpy 解析解+投影）；L3 接线——`synthesize_signals` optimizer 模式 + `PortfolioLoop.optimizer_mode`（CLI `--optimizer-mode`，mvo 别名）+ Ledoit-Wolf cov 注入 + factor_returns 实测化输入；✅ ③ v2.74.0 补齐：`FTSConfig.portfolio_optimizer_mode`（默认 risk_parity，FTS_PORTFOLIO_OPTIMIZER_MODE env）接入 `fts portfolio run`——CLI `--synthesis-mode` choices 增加 optimizer + `--optimizer-mode`（risk_parity/mvo）+ `--returns-matrix` CSV 加载并透传 `run(factor_returns=...)`（股票/期货对称）；新增 `TestCmdPortfolioRunStock` 3 用例（模式/参数透传、配置默认值、returns-matrix 加载）+ test_config_settings 2 用例（默认值/env 覆盖） |
| **测试方案** | 收缩协方差正定性；风险平价权重与目标风险一致；与 Elastic Net 对比报告 |

#### GAP-I305 因子衰减自动退役闭环（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | `elite_tracker.py` + 定期复审（`_run_periodic_factor_review` L1947）+ `retire_factor()`（v2.17.0）已存在，但退役阈值与重校准为人工配置，无自动闭环（月度 decay 任务存在，未接实盘反馈） |
| **机构级标准** | 自动化 IC 衰减监控（滚动 6M IC 斜率）→ 衰减分级（正常/观察/退役）→ 自动退役 + 报告；与 GAP-I401 实盘反馈联动 |
| **影响** | 衰减因子滞留组合拖累绩效；退役决策滞后 |
| **实施步骤** | ① 衰减状态机（active/observe/retired）接入 elite 池；② 月度任务按滚动 IC 斜率自动迁移状态；③ 退役记录回写 DuckDB + 报告 |
| **完成记录** | ✅ v2.72.1：因子衰减自动退役闭环——`elite_tracker.py` 新增滚动 6M IC 线性回归斜率 `_calc_ic_slope_6m`（归一化 [-1,1]，负值=衰减）+ 衰减分级 `decay_grade`（normal/observe/retired，`observe_slope` 默认 0.10 / `retire_slope` 默认 0.20），`update()` 写入 `decay_grade`/`ic_slope_6m` 快照字段，`auto_retire()` 将 `decay_grade=="retired"` 纳入退役条件；`AutoRetireConfig` 新增 `observe_slope`/`retire_slope`/`slope_min_points`，`AutoRetireManager` 同步配置到 tracker 保证阈值一致；`evolution_loop.py` 定期复审（`_run_periodic_factor_review`）接入 FeedbackLoop FACTOR_DECAY 事件（observe/retired 触发归因分析，`last_feedback` 写回快照），退役受 `decay_auto_retire_enabled` 开关控制；`settings.py` 新增 `decay_observe_slope`/`decay_retire_slope`/`decay_slope_min_points`/`decay_auto_retire_enabled` 配置 + `FTS_DECAY_*` 环境变量；月度任务（`scheduler/jobs.py` `monthly_decay_eval_job`）按斜率自动迁移状态并回写 DuckDB（`retire_factor`）+ 报告；新增 `tests/factor_engine/test_orthogonal_basis.py` 19 用例含衰减分级/斜率/自动退役断言 |
| **测试方案** | 衰减状态迁移单测；自动退役触发条件断言 |

#### GAP-I402 在线因子性能监控（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | `live_factor_monitor.py` / `monitor/` 框架存在（HTTP 端点 + Prometheus 指标），但无实盘因子表现数据源（依赖 GAP-I401） |
| **机构级标准** | 因子在线 IC/衰减/暴露实时仪表盘，异常告警 |
| **影响** | 因子实盘漂移不可见 |
| **实施步骤** | 随 GAP-I401 一并落地：实盘反馈 → 在线指标 → 告警（复用 alertmanager） |
| **完成记录** | ✅ v2.77.0：`fts/monitor/live_factor_monitor.py` 新增 `ingest_live_ic(live_ic_result, backtest_ic_map, decay_status_map)`——消费 GAP-I401 实盘反馈数据源（`LiveFeedbackImporter.compute_live_ic` 输出 + `LiveVsBacktestICReport.generate` 的 status 字段），自动构建因子回测基线/实盘 IC 并触发偏离检查；衰减监控——`_decay` 状态存储（ok/weak/decayed）+ `set_decay_status`/`get_decay_status` + `_check_decay_alerts`（decayed → critical「衰减退役建议（GAP-I305 闭环）」/ weak → warning「持续观察」，`decay_alert_enabled` 可关）；Prometheus 兼容指标日志 `METRIC live_factor_ic{factor_id=..}` / `METRIC live_factor_decay{factor_id=..,status=..} 1`；新增 `tests/monitor/test_live_factor_monitor.py` 12 用例（偏离检查 5 + ingest_live_ic 6 + GAP-I401 端到端对接 1），monitor+feedback 253 定向回归全绿 |
| **测试方案** | 指标注册与告警规则断言 |

### 2.3 P2 — 一般差距（扩展期落地）

#### GAP-I103 知识源扩展（公告/舆情/宏观事件）（P2）

| 维度 | 内容 |
|---|---|
| **代码现状** | L1 知识源仅券商研报 / arXiv 论文 |
| **机构级标准** | 公告/舆情/宏观日历多源并行 |
| **影响** | 候选维度单一（与 GAP-I101 联动） |
| **实施步骤** | iFinD 新闻/公告 API 接入 L1 知识补给；宏观事件注入 L2 演化方向 |
| **测试方案** | 新知识源注入单测 |
| **完成记录** | ✅ v2.82.0：另类知识源多路——新建 `fts/factor_engine/extractors/alternative_sources.py`：`AnnouncementNewsExtractor`（东方财富公告中心 API + LLM 提取 A 股事件/舆情因子）+ `MacroEventExtractor`（东方财富宏观日历 API + LLM 提取跨品种宏观方向因子）；对齐既有三源模式（继承 `BaseExtractor` 复用 `_llm_extract_factors`，失败/空数据优雅降级返回空不阻断 L1）；股票管道接入公告+宏观两源（5 源）、期货管道接入宏观源（4 源），`FTSConfig` 新增 `l1_announcement_extractor_enabled`/`l1_macro_extractor_enabled` 开关；`BaseExtractorPipeline.extract` 多源并行收集（ThreadPoolExecutor，GAP-I101 二期）；新增 `test_alternative_sources.py` 16 用例 |

#### GAP-I303 组合层成本/换手约束显式化（P2）

| 维度 | 内容 |
|---|---|
| **代码现状** | L3 有粘性约束（±30% 变动 / 首日封顶），但无显式换手成本目标项 |
| **机构级标准** | 组合优化目标含换手惩罚项（λ·换手率），或换手上限约束 |
| **影响** | 高频调仓换手成本未被组合层显式优化 |
| **实施步骤** | 组合目标函数增加换手惩罚（复用 cost_model）；参数走配置 |
| **测试方案** | 换手惩罚生效断言 |
| **完成记录** | ✅ v2.85.0：`portfolio_loop.py` 新增 `apply_turnover_penalty`——组合目标函数显式换手惩罚项 λ，粘性约束后、权重归一化前执行 `w_new' = w_old + (w_new − w_old)/(1+λ)` 收缩权重变动（λ=0 关闭、λ 越大换手越低、新因子不惩罚）；`build_combo`/`PortfolioLoop` 参数透传 + `FTSConfig.l3_turnover_penalty`（env `FTS_L3_TURNOVER_PENALTY` 默认 0.0）；新增 `test_turnover_penalty.py` 12 用例（含换手惩罚生效断言：Σ\|Δw\| 严格更小且 λ 单调递减） |

#### GAP-I304 风格暴露控制（Barra 风格体系）（P2）

> 关联 plans/22 GAP-S02（P0，v2.60.0 阶段 A 处理中）。本计划将其纳入 Stage 2 门槛，不重复登记。

**完成记录**: ✅ v2.62.0（GAP-S02 落地 10 风格暴露 + 评估链 style_exposures 参数）；v2.79.0 补充全市场覆盖（evolution_loop `_build_barra_exposures` + L2 评估链 Barra 风格回归残差，`l2_barra_style_neutral` 配置默认 True）

#### GAP-I502 分布式扩展预留（Dask/Ray 接口）（P2）

| 维度 | 内容 |
|---|---|
| **代码现状** | 全部单进程；`evolution_loop` / `evaluation_chain` 无并行执行器抽象 |
| **机构级标准** | 挖掘/评估执行器可插拔（本地进程池 → Dask/Ray 集群），调用方无感知 |
| **影响** | Stage 3 吞吐再扩容需要架构预留 |
| **实施步骤** | ① 定义 `ExecutorBackend` 抽象（process/dask/ray 三实现）；② GAP-I201 批量评估走 ExecutorBackend；③ Stage 3 部署 Dask 集群 |
| **测试方案** | 三后端行为一致性断言 |

#### GAP-I503 数据深度（Level2 订单簿 / 另类数据）（P2）

| 维度 | 内容 |
|---|---|
| **代码现状** | tick 源已接入（tqsdk_tick_source），但历史仅 ~42 分钟（GAP-050）；无 Level2 订单簿、无另类数据 |
| **机构级标准** | Level2 逐笔/订单簿因子（订单流不平衡、大单占比）+ 另类数据因子 |
| **影响** | 微观结构 alpha 缺失；高频因子无数据支撑 |
| **实施步骤** | ① tick 历史缓存扩展（分钟 tick 落 DuckDB）；② 订单流不平衡/大单占比算子（进 GAP-I202 算子库）；③ 另类数据评估（舆情 NLP 因子）为远期方向 |
| **测试方案** | tick 缓存回放；订单流因子有效性检验 |
| **完成记录** | ✅ v2.84.0（首期）：① tick_cache 增量累积——`aggregator._write_tick_cache` 按 (symbol, datetime) 去重写入 + `tick_cache_retention_days` 保留清理（默认 7 天）+ `get_ticks`/`_try_tick_cache` 时间窗口查询（start_time/end_time）；② 新建 `fts/factor_engine/microstructure_factors.py`——`classify_tick_direction`（价差方向，持平沿用前向）/`order_flow_imbalance`（OFI 滚动主动买卖量差归一化）/`order_book_imbalance`（OBI 5 档深度）/`large_trade_ratio`（大单占比）+ `compute_microstructure_factors` 统一入口（FACTOR_COLUMNS 契约，缺列/不足 min_rows 降级空）；③ 新增 31 用例。二期（另类数据因子：舆情 NLP/卫星/供应链）按 Stage 3C 远期排期 |

---

## 3. 分阶段追赶路线图（三档递进，先单机后扩展）

### Stage 1 — 对标中小专业量化团队（T1，单机达标）v2.65.0 ~ v2.72.0

> 前提：plans/22（GAP-S01~S13）与 plans/21（GAP-F01~F16）当前版本路线（v2.60.0~v2.64.0）完成后启动。

| 版本 | 阶段 | 缺陷项 | 核心内容 |
|:-----|:-----|:-------|:---------|
| v2.65.0 | 1A | GAP-I201 首期 | 批量候选生成 + 向量化批量粗筛（单机多进程），吞吐目标 ≥ 10× ✅ 已关闭（v2.71.0 实际落地 `BatchMiner` 批量漏斗 + v2.83.0 `ExecutorBackend` 多进程并行） |
| v2.67.0 | 1A | GAP-I501 | 冲击成本模型 + 容量限制接入回测流水线 ✅ 已关闭 |
| v2.68.0 | 1B | GAP-I301 | 股票 L3 组合层（复用期货组件）✅ 已关闭 |
| v2.70.0 | 1B | GAP-I205 | 微演化自适应 trials + 早停 ✅ 已关闭 |
| v2.71.0 | 1C | GAP-I206 | L2 准入去冗余/正交化闭环 ✅ 已关闭 |
| v2.71.0 | 1C | GAP-I204 首期 | GP 多目标适应度（IC×换手×衰减）✅ 已关闭 |
| v2.71.0 | 1D | GAP-I401 | 实盘反馈契约 + 回流通道 + 对比报告 ✅ 已关闭 |
| v2.72.0 | 1D | GAP-I101/I102 首期 | L1 批量候选 + 审查工作流骨架 + 全量回归 ✅ 已关闭 |
| v2.72.1 | 1C | GAP-I206 补充 | 多因子正交基底维护（Gram-Schmidt 迭代残差化 + 基底注册/持久化）✅ 已关闭 |
| v2.72.1 | 2B | GAP-I305 提前 | 因子衰减自动退役闭环（滚动 6M IC 斜率分级 + FACTOR_DECAY 反馈联动）✅ 已关闭 |

**Stage 1 退出标准**：① 吞吐 ≥ 10×（基准：每夜 ≥ 500 候选粗筛、≥ 50 细评估）；② 股票 L3 上线且回测 Sharpe/回撤达标；③ 回测含冲击成本与容量约束；④ 全量回归通过 + 一致性 13/13；⑤ 全部 P0（GAP-I201/I207/I301/I501/I401）关闭。

### Stage 2 — 对标国内头部量化私募（T2，单机 + 轻量并行）v2.73.0 ~ v2.80.0

| 版本 | 阶段 | 缺陷项 | 核心内容 |
|:-----|:-----|:-------|:---------|
| v2.73.0 | 2A | GAP-I203 | 轻量 LSTM/GRU 深度因子模型（接入 L2 漏斗 + 全套审计）✅ 已关闭（v2.73.0：`GRUFactorModel` 纯 numpy GRU + `DeepFactorGenerator` 权重内嵌 code + `_evolve_one` deep 分派/批次轮换接入 L2） |
| v2.74.0 | 2A | GAP-I302 | 组合优化器机构化（Ledoit-Wolf 收缩 + 风险平价）✅ 已关闭（① ② v2.61.0 GAP-L302/L303/L304/L305 + ③ v2.74.0 参数走配置） |
| v2.75.0 | 2B | GAP-I202 | 算子库扩充（组合/跨标的/A 股特有算子，单一事实源）✅ 已关闭（v2.75.0：ts_slope/ts_quantile + 8 组合/跨标的算子双注册表共享 + required_shared 硬约束 + lookback=0 罚分；A 股特有算子已随 GAP-S12 v2.69.0 落地） |
| v2.76.0 | 2B | GAP-I305 | 因子衰减自动退役状态机（接实盘反馈）→ **提前至 v2.72.1 完成** ✅ |
| v2.77.0 | 2C | GAP-I402 | 在线因子性能监控 + 告警（随 I401 数据源）✅ 已关闭（v2.77.0：`ingest_live_ic` 接入 GAP-I401 + 衰减告警 + Prometheus 指标） |
| v2.78.0 | 2C | GAP-I204 二期 | 符号回归补充搜索 + Pareto 前沿输出 ✅ 已关闭（v2.78.0） |
| v2.79.0 | 2D | GAP-I304 | Barra 风格暴露控制落地（联动 GAP-S02）✅ 已关闭（v2.62.0 GAP-S02 落地 10 风格暴露 + 评估链 style_exposures 参数；v2.79.0 补充全市场覆盖：`evolution_loop._build_barra_exposures` 自动构建 10 风格暴露接入 L2 `_evaluate_cross_section`（行业中性化后叠加 Barra 风格回归残差），`l2_barra_style_neutral` 配置默认 True + `test_barra_l2_integration.py` 7 用例） |
| v2.80.0 | 2D | GAP-I101/I102 二期 + I103 | 知识源多路扩展 + 人审工作台正式启用 ✅ 已关闭（v2.82.0 实际落地：GAP-I103 另类知识源——`AnnouncementNewsExtractor`（公告/舆情）+ `MacroEventExtractor`（宏观事件）接入股票 5 源/期货 4 源管道（`FTS_L1_*_EXTRACTOR_ENABLED` 开关）；GAP-I101 二期——`BaseExtractorPipeline.extract` 多源并行收集（ThreadPoolExecutor）；GAP-I102 二期——驳回意见写经验链 `ExperienceChain.record_failure`（`FTS_REVIEW_EXPERIENCE_CHAIN` 开关）；新增 16+7 用例） |

**Stage 2 退出标准**：① 深度因子在 L2 出过 ≥ 1 个通过全套审计的精英因子；② 组合层支持均值方差/风险平价；③ 衰减自动退役运行 ≥ 1 个月且决策与人工复核一致率 ≥ 90%；④ 覆盖 11 项 P1 差距中的 9 项；⑤ 全量回归 + 覆盖率达标。

### Stage 3 — 对标海外顶级量化机构（T3，分布式 / GPU / 另类数据）v2.81.0+（远期）

| 版本 | 阶段 | 缺陷项 | 核心内容 |
|:-----|:-----|:-------|:---------|
| v2.81.0+ | 3A | GAP-I502 | ExecutorBackend 抽象（process→dask/ray）✅ 已关闭（v2.83.0：`fts/factor_engine/executor_backend.py`——`ExecutorBackend` 抽象（map/shutdown + 上下文管理）+ `ThreadBackend`/`ProcessBackend`（cloudpickle 序列化，lambda/bound method 跨进程）/`DaskBackend`/`RayBackend`（缺依赖自动降级 ProcessBackend）+ `create_executor_backend` 工厂；`BatchMiner.filter_batch` 批量粗筛接入（`BatchMiningConfig.executor_backend`/`executor_max_workers`），配置 `FTSConfig.executor_backend`（默认 thread 保持现状）+ `FTS_EXECUTOR_BACKEND`/`FTS_EXECUTOR_MAX_WORKERS`；新增 `test_executor_backend.py` 14 用例（四后端行为一致性/process lambda+bound method/降级/未知回退/BatchMiner 接入+异常隔离），executor_backend 14 + batch_mining 11 合计 25 passed） |
| v2.82.0+ | 3A | GAP-I503 首期 | tick 历史缓存扩展 + Level2 订单流因子 ✅ 已关闭（v2.84.0：① tick_cache 增量累积——`aggregator._write_tick_cache` 按 (symbol, datetime) 去重写入 + `tick_cache_retention_days` 保留清理（默认 7 天），`get_ticks`/`_try_tick_cache` 支持 `start_time`/`end_time` 时间窗口查询，跨会话多次拉取累积成更长 tick 历史，无重复污染、不膨胀；② 新建 `fts/factor_engine/microstructure_factors.py`——`MicrostructureConfig`（window/large_threshold_abs/large_threshold_mult/min_rows）+ `classify_tick_direction`（价差方向，持平沿用前向）+ `order_flow_imbalance`（滚动窗口主动买卖量差归一化 OFI ∈[-1,1]）+ `order_book_imbalance`（5 档深度 OBI）+ `large_trade_ratio`（绝对/相对阈值大单成交量占比）+ `compute_microstructure_factors` 统一入口（FACTOR_COLUMNS 契约，缺列/不足 min_rows 优雅降级空）；③ 新增 `test_microstructure_factors.py` 20 用例 + `test_tick_cache_accumulate.py` 11 用例，31 passed + 既有 tick/aggregator/migrate 125 passed 全绿） |
| v2.83.0+ | 3B | GAP-I303 | 组合目标换手惩罚项 ✅ 已关闭（v2.85.0：`portfolio_loop.py` 新增 `apply_turnover_penalty`——组合目标函数显式换手惩罚项 λ，在粘性约束后、权重归一化前执行 `w_new' = w_old + (w_new − w_old)/(1+λ)` 收缩权重变动（λ=0 关闭保持原样、λ 越大换手越低、新因子不惩罚）；`build_combo` 新增 `turnover_penalty` 参数透传，`PortfolioLoop` 新增构造参数（None 从 `FTSConfig.l3_turnover_penalty` 读取，env `FTS_L3_TURNOVER_PENALTY` 默认 0.0 关闭）；复用成本约束语义不新增成本模型；新增 `test_turnover_penalty.py` 12 用例（单元 4 + 换手惩罚生效断言 3——惩罚后 Σ\|Δw\| 严格更小且 λ 单调递减 + build_combo 集成 2 + 配置读取 3），portfolio_loop 213 + 新测试 12 合计 225 passed 全绿） |
| 远期 | 3C | 剩余差距 C1~C8 | 见 §3.3 剩余差距弥补建议与实施方案（Level2 因子入 elite / 另类数据因子 / ML 组合层 / 多节点分布式 / GPU 深度模型 / 在线学习 / 回测实证化 / 基础设施深化） |

**Stage 3 退出标准**：① 分布式挖掘工厂部署（多节点）；② Level2 微观结构因子进入 elite；③ 另类数据因子 ≥ 1 类上线；④ 组合层支持机器学习权重（Black-Litterman 类）。以上 4 项分别由 C4 / C1 / C2 / C3 承接。

### 3.3 Stage 3C — 剩余差距弥补建议与实施方案（C1~C8，远期）

> 定位：本计划 GAP-I 系列已全部关闭（✅ v2.85.0），以下为对标 T3 海外顶级机构的**剩余结构性差距**。每项按 HARNESS 闭环（文档先行 → 契约优先 → 测试随重构）推进，落地时登记至 `08-gap-analysis.md` 与 `09-advancement-plan.md`，日常开发仅在 `07-operations.md` 追加记录、不 bump 版本号，满足发布条件时统一 `scripts/bump_version.py`。
>
> 前置依赖核实（2026-08-11 代码勘察）：`microstructure_factors.py`（Level2 因子）已实现但 `compute_microstructure_factors` 无调用方（未接入 L2）；`executor_backend.py`（Dask/Ray 抽象）已存在但无集群实战；`alternative_sources.py`（公告/宏观）仅产出 L1 想法候选、无结构化舆情因子；`cost_model.impact_cost` 参数为人工设定、无实证标定；无 Black-Litterman / Transformer / GAN / 在线学习实现。
>
> **实施策略决策（2026-08-11 用户确认：全部先实现、部署后置）**：C1/C2/C8（数据/工程类，与硬件无关）立即实施；C4（分布式）代码与测试在单机先行（LocalCluster + Docker 模拟多节点），多机部署仅改配置、吞吐验收基准待真实集群；C5 GAN 先做可解释性/审计兼容性设计评审 + CPU 实现，GPU 仅作加速后置。部署形态（多机拓扑/GPU 配置）不阻塞开发，硬件到位即插即用。

#### C1 Level2 微观结构因子接入 L2 演化并入 elite（🔴，Stage 3 退出标准②）

| 维度 | 内容 |
|---|---|
| **现状（代码依据）** | `fts/factor_engine/microstructure_factors.py`（GAP-I503 首期，v2.84.0）已实现 `classify_tick_direction`/`order_flow_imbalance`/`order_book_imbalance`/`large_trade_ratio` + `compute_microstructure_factors` 统一入口，但**仅模块级存在，无演化/评估链调用方**（Grep 全库 `compute_microstructure_factors` 零命中） |
| **机构级标准** | 微观结构因子作为独立候选源接入挖掘工厂（tick 级输入 → 因子值 → 全套审计链 → 晋升 elite） |
| **弥补建议** | ① tick 数据通道落地：扩展 `tqsdk_tick_source` 历史累积（tick_cache 已具备，v2.84.0）至覆盖核心品种最近 ≥ 1 个季度；② 新增 `MicrostructureFactorGenerator`（对齐 `DeepFactorGenerator` 模式）：tick 缓存 → 日频聚合微观因子值（OFI/OBI/大单占比）→ 输出标准因子契约（code 内嵌 tick 聚合逻辑，零未来函数：当日因子仅用当日及历史 tick）；③ L2 接入：`evolution_loop._batch_generate_one` 批次轮换并入 microstructure（对齐 deep 分派模式），走全套评估链 + 6 项强制审计；④ 晋升后纳入正交基底与 L3 聚类去冗余 |
| **验收标准** | ≥ 1 个微观结构因子通过全套审计晋升 elite（Stage 3 退出标准②达成）；因子代码无未来函数（t 日因子仅用 ≤ t 数据） |
| **测试方案** | 因子代码可执行性验证；tick 缓存回放一致性；审计链门禁通过断言；与既有 elite 相关 ≤ 阈值 |

**C1 实施设计（契约优先，2026-08-11）**

> **关键设计修正**：原弥补建议「`_batch_generate_one` 批次轮换并入 microstructure（对齐 deep 分派）」不可行——演化 batch 模式数据输入为日频 OHLCV 面板，无 tick 数据通道（`get_ticks` 仅在 `DataAggregator` 层可用，tick_cache 默认保留 7 天）。且 tick→日频聚合属于数据准备层职责（FTS 分层）。修正为**独立候选源注入**：离线生成器产出日频微观结构因子候选 → 走 L2 评估链 + 6 项审计 → 晋升 elite（与种子注入同链）。

- **数据通道**：`DataAggregator.get_ticks(symbol, count, start_time, end_time)`（tick_cache → tick_sources 降级）。C1 需 ≥1 季度 tick：`MicrostructureGeneratorConfig.tick_lookback_days=63`，tick_cache 保留期由聚合任务配置（不动全局默认 7 天，避免膨胀）
- **新增模块** `fts/factor_engine/microstructure_generator.py`（对齐 `DeepFactorGenerator` 模式）：
  - `MicrostructureGeneratorConfig`（dataclass）：`symbols: list[str] = 动态池 25 品种` / `tick_lookback_days=63` / `min_tick_rows=200` / `micro_window=20`（透传 `MicrostructureConfig`）/ `min_factor_rows=20`（最少有效交易日，不足降级）
  - `MicrostructureFactorGenerator`：`generate(symbol) -> Optional[MicrostructureFactorCandidate]`——`get_ticks` → `compute_microstructure_factors`（ofi/obi/large_trade_ratio 契约列）→ 按交易日聚合（日均值 ofi_mean/obi_mean/ltr_mean + 日波动 ofi_std）→ 生成因子契约（`FactorProgram`：`factor_id=micro_{symbol}_{kind}`、`family="microstructure"`、`economic_logic` 冻结）
  - **因子 code 零未来模式**：params 含 `dates`/`values`（日频聚合值数组，数据准备层用 ≤t tick 聚合后固定）；code 为确定性查找函数——`sig = pd.Series(np.nan, index=data.index)` → `data.index.intersection(dates)` 定位取值 → `ffill().fillna(0.0)` 输出与面板等长信号；窗口自适应（任意评估窗口）
  - 数据不足（tick 行数 < min_tick_rows 或有效交易日 < min_factor_rows）→ 返回 None 降级（不阻断主流程）
- **L2 接入（独立候选源）**：生成器产出候选直接走评估链 `evaluation_chain`（复用 `_evaluate_cross_section`/`evaluate_backtest` 路径）+ FactorAuditor 6 项强制审计 → 晋升 elite 走 `_promote_to_elite`（复用家族多样性/正交化/相关性预检）；CLI `fts factor micro-generate`（--market futures / --limit / --dry-run）批量生成候选并统计
- **测试** `tests/factor_engine/test_microstructure_generator.py`（约 15 用例）：聚合正确性（合成 tick 对照日频均值/波动）/code 可执行（`_execute_factor_code`）/零未来函数截断一致性（t 日信号不依赖 t+1 tick）/窗口自适应（不同长度 data）/数据不足降级（空 tick/少日数）/契约字段/命名与家族/审计链集成/注入链路/与既有 elite 相关性预检

- **✅ 实施状态（2026-08-11 首期完成 + 评估晋升接线）**：`microstructure_generator.py` 已落地（`MicrostructureGeneratorConfig` + `MicrostructureFactorGenerator.generate/generate_batch` + 4 因子/品种 + 零未来日期查找 code）；`BacktestPipeline._execute_factor_code` 对 DatetimeIndex 注入 `datetime` 列（日期对齐通道）；CLI `fts factor micro-generate`；`test_microstructure_generator.py` 20 用例全绿。**评估晋升接线（二期，2026-08-11）**：`evolution_loop.run_microstructure_promotion`（`MicrostructureFactorGenerator.generate_batch` → `_evaluate_cross_section` 横截面评估（内置 ic≥0.03 & sharpe≥1.5 门槛）→ FactorAuditor 6 项审计（数据缺失 skipped 尽力而为不拦截）→ `_promote_to_elite`（重复/家族/去冗余护栏），返回 generated/evaluated/passed/promoted 统计 + `promoted_ids`）；CLI `fts factor micro-evaluate`（--symbols/--limit/--max-symbols，构造 futures EvolutionLoop 走完整链路）；`test_microstructure_promotion.py` 7 用例全绿（无候选全 skipped / passed 晋升 / failed 不晋升 / eval 异常单跳过 / audit 异常降级仍晋升 / limit 截断 / CLI parser 注册）。**剩余**：真实评估需 tick 数据积累（≥1 季度真实 tick），候选评估晋升链路已就绪。

#### C2 另类数据因子上线（舆情 NLP，🔴，Stage 3 退出标准③）

| 维度 | 内容 |
|---|---|
| **现状（代码依据）** | `extractors/alternative_sources.py`（GAP-I103，v2.82.0）已接入公告/宏观事件两路 L1 知识源，但产出为 LLM 文本想法候选（SeedCandidate），**无结构化舆情因子值**；无卫星/供应链数据（数据合规评估中，见 §6） |
| **机构级标准** | 另类数据因子 ≥ 1 类上线：结构化舆情情感分（新闻/公告/互动易文本 → 情感得分序列 → 因子值）接入因子库 |
| **弥补建议** | ① 新建 `fts/factor_engine/alternative_sentiment.py`：`SentimentFactorGenerator`——公开新闻/公告标题+摘要批量抓取（复用 eastmoney API）→ 轻量情感打分（词典法 v1：金融情感词典 ± 强度，无需 LLM，成本可控；LLM 精修留二期）→ 按标的聚合日频情感序列 → 输出因子契约（情感均值/离散度/变化率 3 因子）；② 数据落库：DuckDB `sentiment_daily` 表（增量去重，对齐 tick_cache 模式）；③ L2 接入：作为种子候选源注入 `SeedPool.inject_from_l1`（复用 GAP-031 注入链路）+ 走全套审计；④ 卫星/供应链留数据评估后再立项（§6 不采购红线不变） |
| **验收标准** | ≥ 1 个舆情情感因子通过审计晋升 elite（Stage 3 退出标准③达成）；词典打分与 LLM 抽样标注一致性 ≥ 0.7（v1 抽样 50 条） |
| **测试方案** | 情感打分单测（积极/消极/中性样本）；聚合时序对齐；因子零未来断言；数据缺列降级 |

**C2 实施设计（契约优先，2026-08-11）**

> **设计对齐**：复用 C1「独立候选源注入」模式（演化 batch 模式无新闻数据通道；新闻→日频聚合属数据准备层职责）。词典法 v1 无需 LLM/API key，公开新闻源失败优雅降级。

- **新增模块** `fts/factor_engine/alternative_sentiment.py`：
  - `FinancialSentimentLexicon`：内置金融情感词典（正/负词条 + 强度权重，不依赖外部词库），`score_text(text) -> float ∈ [-1,1]`——命中词加权求和 → tanh 压缩；无命中 0
  - `NewsRecord`（dataclass）：`date`（ISO）/ `title` / `summary`（可为空）/ `source`
  - `NewsProvider` 协议：`fetch_news(symbol, lookback_days, trace_id) -> pd.DataFrame`（列 date/title/summary）；默认实现 `EastmoneyNewsProvider`——按品种关键词（品种名/代码）调用东方财富新闻搜索 API（`search-api-web.eastmoney.com`，公开免鉴权，复用 `_HTTP_HEADERS` 模式），失败/空/网络异常降级返回空 DataFrame（不阻断）
  - `SentimentGeneratorConfig`（dataclass）：`symbols`（默认动态池）/ `lookback_days=63` / `min_records=5`（总记录数下限）/ `min_factor_rows=20`（有效情感交易日下限）/ `save_sentiment_db=False`（落库开关）
  - `SentimentFactorGenerator`：`generate(symbol)` / `generate_batch(symbols)`——fetch_news → 词典打分（逐条 score_text）→ 按日聚合（`sent_mean` 日均值 / `sent_std` 日离散 / `sent_chg` 日变化率）→ 生成 FactorProgram（`sent_{symbol}_{kind}`，family="behavioral"，3 因子/品种）
  - **因子 code 零未来模式**：同 C1——dates/values 内嵌 params，code 按 `data['datetime']` 日期确定性查找 + ffill（复用执行器 datetime 注入通道）；聚合在数据准备层用 ≤t 新闻完成
  - 数据不足（无新闻/有效情感日 < min_factor_rows）→ 返回 None 降级
- **数据落库（可选）**：`save_sentiment_db=True` 时写 DuckDB `sentiment_daily` 表（symbol/date/sent_mean/sent_std/sent_chg，UPSERT 增量去重，对齐 tick_cache 模式）；默认关（避免无谓写库）
- **L2 接入（独立候选源）**：同 C1——生成器产出候选走评估链 + FactorAuditor 6 项审计 → `_promote_to_elite`；CLI `fts factor senti-generate`（--symbols/--limit/--json）
- **测试** `tests/factor_engine/test_alternative_sentiment.py`（约 18 用例）：词典打分（积极/消极/中性/混合/否定/无命中）/聚合（均值/离散/变化率手算对照）/按日对齐/零未来截断一致性/窗口自适应/降级（空新闻/少日数/缺列）/契约字段/命名与家族/批量生成/坏品种跳过/CLI 3 用例

- **✅ 实施状态（2026-08-11 首期完成 + LLM 精修）**：`alternative_sentiment.py` 已落地（`FinancialSentimentLexicon` 30 正/30 负词条 + 否定反转 + `score_text`；`EastmoneyNewsProvider` 新闻搜索 JSONP 解析失败降级空；`SentimentFactorGenerator.generate/generate_batch` + 3 因子/品种 + 零未来日期查找 code；`save_sentiment_db` 可选 DuckDB `sentiment_daily` 落库）；CLI `fts factor senti-generate`；`test_alternative_sentiment.py` 26 用例全绿。**LLM 精修（v2，2026-08-11）**：`LlmSentimentScorer`（复用 `LLMClient.complete` 约束输出 [-1,1]，异常/解析失败 None 降级词典）+ `evaluate_lexicon_consistency(samples, llm, min_consistency=0.7, match_threshold=0.25)`（词典 vs LLM 逐条一致性：任一侧中性或同号视为一致，输出 total/valid/agreement/agreement_rate/passed 判定）；CLI `fts factor senti-consistency`（--symbols/--sample/--lookback-days/--max-symbols/--min-consistency，`EastmoneyNewsProvider` 抓取 → 一致性评估，空 symbols 回退动态池，无文本 return 1）——C2 验收"词典-LLM 一致性 ≥0.7"验证接口就绪。**剩余**：候选评估链晋升接线（对齐 C1 `run_microstructure_promotion` 模式，待数据积累后启用）。

#### C3 ML 组合层（Black-Litterman 类权重融合，🔴，Stage 3 退出标准④）

| 维度 | 内容 |
|---|---|
| **现状（代码依据）** | `portfolio_optimizer.py` 支持 risk_parity/mvo（Ledoit-Wolf 收缩协方差）+ `synthesize_signals` 六模式（含 ic_weight，v2.97.0 GAP-064）；**无观点融合类 ML 权重**（Black-Litterman / 贝叶斯收缩） |
| **机构级标准** | 组合层支持机器学习权重：先验权重（风险平价/等权）+ 因子观点（预期收益向量）+ 观点置信度 → 后验权重 |
| **弥补建议** | ① 新建 `portfolio_optimizer.black_litterman`：`BlackLittermanOptimizer`——输入（Σ=Ledoit-Wolf 收缩协方差、先验 π=风险平价权重隐含收益、观点矩阵 P/Q/Ω=因子 IC 均值×置信度）→ 后验 μ 与 Σ 解析解 → 后验权重（复用既有约束：多头/暴露中性化/换手上限）；② `synthesize_signals` 新增 bl 模式（失败回退 risk_parity）；③ `PortfolioLoop`/CLI `--optimizer-mode bl` 接线 + 配置 `FTS_PORTFOLIO_OPTIMIZER_MODE`；④ 因子观点来源：L3 组合内因子滚动 IC 均值 + 实盘反馈 IC（GAP-I401 数据源） |
| **验收标准** | Stage 3 退出标准④达成；BL 后验权重与观点方向一致；观点置信度 ↑ → 权重偏离先验幅度 ↑（单调性断言） |
| **测试方案** | 观点融合解析解正确性；置信度单调性；无观点退化 = 风险平价；约束生效；回退路径 |

**C3 实施设计（契约优先，2026-08-11）**

- **新增模块** `fts/factor_engine/black_litterman.py`：
  - `BlackLittermanConfig`（dataclass）：`tau=0.05`（先验协方差缩放）/ `omega_scale=0.1`（观点不确定性标量）/ `risk_aversion=1.0` / `max_weight=0.3` / `max_leverage=1.0`
  - `BlackLittermanResult`（dataclass）：`mu_posterior` / `sigma_posterior` / `weights` / `prior_mu` / `view_q`
  - `implied_returns(cov, prior_weights, risk_aversion) -> np.ndarray`：逆优化隐含收益 π=λΣ·w_prior
  - `black_litterman_weights(cov, prior_weights, views_q, views_p=None, config=None) -> BlackLittermanResult`：标准 BL 闭式——`M_inv=(inv(τΣ)+P'Ω⁻¹P)⁻¹`；`μ_post=M_inv(inv(τΣ)π+P'Ω⁻¹Q)`；`Σ_post=Σ+M_inv`；Ω=diag(diag(P(τΣ)P'))×omega_scale（正定下限 1e-12）；权重=无约束最大夏普 `w=Σ_post⁻¹μ_post` → 截断 `max_weight` → 归一化 `max_leverage`；**零观点退化性质**：Q=0 ⇒ μ_post=π ⇒ w∝w_prior（=风险平价先验）
  - `build_auto_views(factors, pi) -> (views_p, views_q)`：默认绝对观点 P=I，Q=原始 IC（`_ic_raw` 优先）× (mean\|π\|/max\|IC\|) 尺度
- **接线** `portfolio_loop.py` `synthesize_signals` optimizer 分支：`optimizer_mode=="bl"` 时——先验权重=同协方差风险平价解（复用 `PortfolioOptimizer(risk_parity)`）；views 显式（`optimizer_config["views_p"]/["views_q"]`）或 `build_auto_views` 自动构建；BL 后验权重入 `PortfolioSignal`（retained=w>0）；协方差仍走 Ledoit-Wolf；**失败回退 risk_parity**
- **配置/CLI**：`PortfolioOptimizer.__init__` 模式校验不扩展（bl 由 synthesize 分支处理）；CLI `--optimizer-mode` choices 增加 `bl`；`FTS_PORTFOLIO_OPTIMIZER_MODE` 注释更新
- **测试** `tests/factor_engine/test_black_litterman.py`（约 14 用例）：BL 闭式正确性（手算对照）/零观点退化=先验/Q=0 时权重≈风险平价/观点方向一致性（IC↑ 权重↑）/置信度单调性（omega_scale↓ 偏离先验↑）/维度校验/奇异协方差兜底/NaN 清理/auto-views 构建/合成信号 bl 集成/显式 views 透传/回退路径/CLI 接线

- **✅ 实施状态（2026-08-11 首期完成）**：`black_litterman.py` 已落地（`BlackLittermanConfig/Result` + `implied_returns` + `black_litterman_weights` 闭式后验 + `build_auto_views` + `_project_weights` 约束投影）；`portfolio_loop.py` optimizer 分支 `mode_internal=="bl"` 接线（失败回退 risk_parity）；CLI `--optimizer-mode` choices 增加 bl；`tests/factor_engine/test_black_litterman.py` 22 用例全绿（原设计约 14，实做 22）；测试报告见 07-operations §1 追加记录。**剩余**：因子观点来源接 GAP-I401 实盘反馈 IC（现为滚动 IC 均值）待实盘回流后启用。

#### C4 多节点分布式挖掘工厂（Dask 集群实战，🔴）

| 维度 | 内容 |
|---|---|
| **现状（代码依据）** | `executor_backend.py`（GAP-I502，v2.83.0）已抽象 `DaskBackend`/`RayBackend`（缺依赖降级 ProcessBackend），`BatchMiner.filter_batch` 已接入；**无多节点集群部署与实战验证** |
| **机构级标准** | 分布式 Alpha Factory：多节点 Dask 集群跑批量挖掘/评估，调用方无感知（ExecutorBackend 抽象已满足调用方无感知） |
| **弥补建议** | ① 集群部署方案落文档（`docs/production_plan.md` 增补）：Dask 集群（scheduler + N workers）+ DuckDB 单写者部署拓扑（写节点唯一，GAP-056 架构下读节点多副本）；② 接线验证：`BatchMiningConfig.executor_backend="dask"` + `executor_max_workers` 在集群实测批量粗筛与细评估吞吐；③ 新增吞吐基准测试（`tests/benchmarks/`）：集群 vs 单机多进程吞吐对比，纳入 Stage 3 验收报告；④ Ray 后端保持抽象存在，实际以 Dask 先行 |
| **验收标准** | 双节点以上集群批量评估吞吐 ≥ 单机 ProcessBackend ×3；任务失败隔离（单 worker 故障不中断整批）；一致性（集群结果与串行误差 < 1e-9） |
| **测试方案** | 集群端到端回测；故障注入；结果一致性断言；吞吐基准 |

**C4 实施设计（契约优先，2026-08-11，策略：全部先实现、部署后置——单机 LocalCluster 模拟多节点调度语义，真实多机部署待硬件/基建）**

> 用户决策（AskUserQuestion 确认）：分布式/GPU 差距**全部先实现，部署后置**——代码与测试先落地，真实多机集群/Docker 部署在硬件条件成熟后按既有 DaskBackend 抽象接入，调用方无感知。

- **① 依赖与配置**：`pyproject.toml` 新增 `distributed` extra（`distributed>=2024.3`，自带 dask 核心）并入 `all`；`FTSConfig.executor_backend="dask"` + `executor_max_workers`（既有，GAP-I502）不变
- **② DaskBackend 增强**（`fts/factor_engine/executor_backend.py`，对齐 ProcessBackend 语义）：
  - `__init__(max_workers=4, address=None, cluster=None)`——新增 `cluster` 句柄注入（测试/复用外部 LocalCluster 用，`address` 优先于 `cluster`）；缺省无 address 时 `Client(n_workers, threads_per_worker=1, processes=True)` 建本地集群（单机多进程=模拟多节点调度语义，部署后置）
  - 新增诊断/故障注入接口（均 try/except 降级不抛）：`worker_count` 属性（`client.cluster.workers` 或 `client.ncores` 兜底）、`kill_worker()`（`client.cluster.kill_worker` 随机杀一个 worker，供故障注入测试）、`alive_workers()`（剩余 worker 数）
  - `map` 保持按输入顺序返回（futures 按序 result，既有实现已满足）；异常隔离：单任务失败仅该任务抛（dask 默认），不中断整批
- **③ 单机 LocalCluster 实战验证**（部署后置的"集群语义"验证）：
  - `DaskBackend(n_workers=2)` 批量评估（复用 `BatchMiner.filter_batch` 既有接入点）——验证分布式调度链路（任务分派/结果回收/顺序保真）
  - **故障注入**：`kill_worker()` 后继续提交任务——dask 调度器将任务重派给存活 worker，整批不中断，`alive_workers() >= 1` 且后续结果正确（worker 故障隔离验收）
  - **一致性**：dask 批量粗筛结果与 thread 串行逐一对齐（误差 < 1e-9）
- **④ 吞吐基准**：新建 `scripts/benchmark_executor.py`（dask LocalCluster vs process vs thread 对合成批量评估任务测吞吐，输出对比表；dask 未装/集群创建失败降级跳过并提示）——Stage 3 验收报告数据来源
- **⑤ 集群部署拓扑落文档**：`docs/production_plan.md` 增补"Dask 集群拓扑"小节——scheduler + N workers 部署图、`DaskBackend(address="tcp://scheduler:8786")` 接线、DuckDB **单写者多读副本**架构（写节点唯一避免锁冲突，读节点多副本，GAP-056 架构延续）、任务失败重试与监控告警建议
- **测试** `tests/factor_engine/test_executor_dask.py`（约 20 用例，dask 可用时全跑、缺失时降级断言仍通过）：本地集群 map 顺序/结果正确/一致性（vs thread 对齐 < 1e-9）/worker 故障注入（kill 后剩余任务不中断、alive>=1、后续结果正确）/`cluster` 句柄注入复用/`address` 优先/工厂 `create_executor_backend("dask")`/缺 dask 依赖降级 ProcessBackend（monkeypatch import 失败）/与 `BatchMiner.filter_batch` 集成（executor_backend="dask" 批量粗筛与 process 结果一致）/shutdown 幂等/worker_count 诊断
- **验收标准**（对齐 C4 表格）：LocalCluster 批量评估吞吐 ≥ 单机 ProcessBackend（同机多进程近似，部署后置不追求 ×3 硬指标，以调度链路正确 + 吞吐基准数据为准）；worker 故障不中断整批；集群结果与串行误差 < 1e-9

#### C5 GPU 深度模型（Transformer 因子 / GAN 合成，🟡 远期）

| 维度 | 内容 |
|---|---|
| **现状（代码依据）** | `fts/ml/models.py` + `deep_factor.py`（GAP-I203，v2.73.0）仅纯 numpy 单层 GRU（CPU）；无 Transformer/GAN |
| **机构级标准** | 深度时序特征（Transformer 端到端）作为候选因子源之一；生成式模型合成因子/行情做数据增强 |
| **弥补建议** | ① 保持"不引入重依赖"约束下先落地轻量 Transformer：单层自注意力 + 位置编码纯 numpy 实现（对齐 GRUFactorModel 模式：权重内嵌 code、OOS 样本外验证、SHAP 归因）；② 接入 L2 批量漏斗（复用 GAP-I203 deep 分派模式）；③ GAN 合成行情/因子为数据增强方向，评估可解释性与审计兼容性后再立项（GAP-037 远期延续） |
| **验收标准** | Transformer 因子出 ≥ 1 个通过全套审计的精英（对比 GRU 有增量 IC）；OOS 窗口验证；无重依赖降级兼容 |
| **测试方案** | 轻量 Transformer 前向/训练单测；权重序列化可执行；零未来函数；与 GRU 对比基准 |

**C5 实施设计（契约优先，2026-08-11）**

- **新增模型** `fts/ml/models.py` `TransformerFactorModel`（对齐 `GRUFactorModel` 模式，纯 numpy、零新依赖）：
  - 结构：单头自注意力单层——`Q=XW_q, K=XW_k, V=XW_v` → `attn=softmax(QK'/√d_k)V`（因果掩码上三角 −inf 保证零未来函数）→ 残差 + LayerNorm（简化：减均值除标准差）→ 输出层 `y=attn·W_o`；位置编码：可学习正弦/可训练向量
  - 接口对齐 `GRUFactorModel`：`fit(X)` / `predict(X)` / `get_params()`（导出 W_q/W_k/W_v/W_o/位置编码等权重组，供因子 code 内嵌）/ 输入 `(n, seq, f)` 滚动窗口，训练前 z-score 标准化；样本不足/非数值/维度不匹配/未训练 → `ModelNotAvailableError` 降级
  - 训练：动量 SGD + L2 正则 + 固定种子（可复现）
- **生成器** `fts/ml/deep_factor.py` 扩展：`DeepFactorConfig` 新增 `model_kind="gru"|"transformer"`（默认 gru 行为不变）；`create_deep_factor(..., model_kind="transformer")` 复用既有 OHLCV 特征/滚动窗口/前 train_ratio 训练/权重内嵌 `def factor_program(data, params)` code（`params` 含 `model_kind="transformer"`，`_execute_factor_code` 侧按 kind 分派推理函数）；零未来函数截断一致性复用既有测试框架
- **L2 接线** `evolution_loop.py`：`_run_deep_evolution` 支持 `method_hint="transformer"`（数据校验 → create_deep_factor(model_kind="transformer") → 血缘回填，失败降级回退）；`_batch_generate_one` 批次轮换并入 transformer（idx%4==2 或 3：macro/gp/deep/operator/transformer 循环）
- **测试** `tests/ml/test_transformer_factor.py`（约 20 用例）：模型前向形状/训练收敛/因果掩码（t 时刻输出不依赖 t+1 输入）/权重导出可执行（生成 code 经 `_execute_factor_code` 验证）/零未来函数截断一致性/降级 3 项/生成器契约/EvolutionLoop transformer 分派接线/与 GRU 对比基线
- **GAN 合成**：仅登记远期（GAP-037 延续），本轮不做（可解释性与审计兼容性未评估）

- **✅ 实施状态（2026-08-11 首期完成）**：`TransformerFactorModel` + `create_transformer_model` 工厂已落地（`fts/ml/models.py` + `fts/ml/__init__.py` 导出）；`deep_factor.py` `DeepFactorConfig.model_kind` 分派 + `_build_code_transformer`；`evolution_loop.py` `_run_deep_evolution(model_kind)` 透传 + `method_hint="transformer"` 分派（失败降级）+ 批次轮换 idx%3→idx%4；`tests/test_transformer_factor.py` 21 用例全绿（模型级 11 / DeepFactor 集成 6 / EvolutionLoop 3）；既有批次轮换断言同步（test_gru_factor/test_evolution_loop）。**剩余**：GAN 合成仍远期；轻量 Transformer 与 GRU 的精英级对比基准待 L2 长跑积累。

#### C6 在线学习与自动重校准（🟡 远期）

| 维度 | 内容 |
|---|---|
| **现状（代码依据）** | GAP-I401 实盘反馈（`LiveFeedbackImporter`/`LiveVsBacktestICReport`）+ GAP-I402 在线监控（`ingest_live_ic` 偏离告警）+ GAP-I305 衰减退役（滚动 6M IC 斜率）已闭环；**无在线增量重校准**（权重/参数随实盘数据增量更新） |
| **机构级标准** | 在线学习：实盘反馈驱动因子重校准（观点更新 → 组合权重微调）而非仅告警/退役 |
| **弥补建议** | ① 落地轻量在线重校准：`LiveVsBacktestICReport` decayed 因子触发"重校准候选"（非直接退役），进入 `micro_evolution` 精筛参数微调（低 trials，复用 GAP-I205 两阶段漏斗）；② 重校准结果回写 elite 快照（`recalibrated_at`/`recalibrated_ic` 元数据）；③ 全量模型在线学习（RL/增量梯度）留远期，先以"观点更新驱动 BL 权重（C3）+ 因子级微调"渐进实现 |
| **验收标准** | decayed 因子自动进入重校准队列并输出微调参数；重校准后 OOS IC 提升或维持；全程留痕可审计 |
| **测试方案** | 重校准队列状态机；微调参数与基线对比；回写元数据断言 |

**C6 实施设计（契约优先，2026-08-11）**

- **新增模块** `fts/factor_engine/recalibration.py`：
  - `RecalibrationConfig`（dataclass）：`enabled=True` / `max_queue=50` / `coarse_trials=20`（复用 GAP-I205 两阶段漏斗粗筛）/ `min_ic_gap=0.0`（微调后 IC 提升下限，低于则 skipped）/ `queue_path`（默认 `{memory_dir}/portfolio/recalibration_queue.json`）
  - `RecalibrationStatus`（str Enum）：`pending` / `processing` / `done` / `skipped` / `failed`
  - `RecalibrationItem`（dataclass）：`factor_id` / `name` / `status` / `reason`（decayed=触发源）/ `created_at` / `updated_at` / `best_params` / `recalibrated_ic` / `baseline_ic`
  - `RecalibrationQueue`：load/save JSON（幂等），`enqueue(factor_id, reason)`（重复 pending 去重），`list_pending()`，`transition(item_id, status)`
  - `recalibrate_factor(factor, data, forward_returns, config) -> (best_params, new_ic, status)`：复用 `optimize_params_staged`（`coarse_trials` 低 trials + TPE 早停），`new_ic - baseline_ic >= min_ic_gap` 判定 done/skipped；无 optuna/异常 → failed 降级（不阻断）
  - `process_recalibration_queue(elite_dir, data, config) -> dict`：遍历 pending → recalibrate → 回写 elite JSON 元数据（`recalibrated_at` / `recalibrated_ic` / `recalibrated_params`）+ DuckDB metadata 同步（复用 factor_db repository update）
- **触发源接线** `feedback_loop.py` `LiveVsBacktestICReport.generate`：`recommend_retire=True`（decayed）且 `recalibration_enabled` 时，不再仅建议退役——同时 `RecalibrationQueue.enqueue(factor_id, reason="decayed")`（GAP-I305 退役逻辑保持：重校准期间进入 observe 观察，微调无改善再退役）
- **配置**：`FTSConfig` 新增 `recalibration_enabled` / `recalibration_coarse_trials` / `recalibration_min_ic_gap`（`FTS_RECALIBRATION_*` env）
- **CLI**：`fts factor recalibrate list/run`（--market/--limit/--dry-run）
- **测试** `tests/factor_engine/test_recalibration.py`（约 12 用例）：队列状态机/入队去重/幂等持久化/recalibrate 判定（提升→done/无提升→skipped/异常→failed）/elite 元数据回写/DuckDB 同步/开关关闭跳过/CLI 命令/与 LiveVsBacktestICReport 触发联动

- **✅ 实施状态（2026-08-11 首期完成）**：`recalibration.py` 已落地（`RecalibrationConfig/Status/Item` + `RecalibrationQueue` JSON 幂等落盘 + `recalibrate_factor` 复用 `optimize_params_staged` + `process_recalibration_queue` elite/DuckDB 回写）；`feedback_loop.py` `LiveVsBacktestICReport` 触发源接线（`recalibration_enabled`/`recalibration_queue_path` 参数 + 行内 `recalibration_queued` 字段）；`settings.py` 配置 + CLI `fts factor recalibrate list/run`；`tests/factor_engine/test_recalibration.py` 18 用例全绿（原设计约 12，实做 18）。**剩余**：全量模型在线学习（RL/增量梯度）仍远期，现以"因子级参数微调 + BL 观点更新（C3）"渐进实现。

#### C7 回测保真实证化（冲击成本实证标定 + 融资成本，🟡）

| 维度 | 内容 |
|---|---|
| **现状（代码依据）** | `cost_model.impact_cost`/`_estimate_impact`（GAP-L305，v2.66.0）平方根冲击成本已建模但**参数（常量乘子/指数）为人工设定**，无实证标定；无融资成本项 |
| **机构级标准** | 冲击成本实证标定（真实盘口/成交滑点回归 → 模型参数）+ 融资成本（保证金占用 × 资金利率）纳入回测 |
| **弥补建议** | ① 实证标定：用 TQ-Local 17709 真实主力合约历史（v2.80.0 动态池同源）统计"信号量占比 vs 实现滑点"回归，更新 `CostConfig` 参数（`impact_*`），产出标定报告；② 融资成本：`TransactionCostModel.adjust` 增加融资成本项（持仓市值 × 保证金率 × 年化利率 × 持有天数），期货按品种保证金率差异化；③ 回测报告输出成本构成明细（手续费/滑点/冲击/融资/展期） |
| **验收标准** | 标定报告覆盖 ≥ 10 个核心品种；融资成本开启后净收益下降且量级合理（利率敏感断言）；参数走配置（`FTS_COST_*`） |
| **测试方案** | 标定回归单测；融资成本单调性；成本构成明细字段断言 |

**C7 实施设计（契约优先，2026-08-11）**

- **① 成本参数配置化** `cost_model.py`：`CostConfig` 字段不变（slippage/commission/impact_bps_per_pct/min_cost/roll_cost），新增 `load_market_cost_config(market, overrides: dict | None) -> CostConfig`——从 `FTSConfig`（`FTS_COST_SLIPPAGE_BPS` / `FTS_COST_COMMISSION_BPS` / `FTS_COST_IMPACT_BPS_PER_PCT` / `FTS_COST_MIN_COST_BPS` / `FTS_COST_ROLL_COST_BPS` env，缺省回落 `_DEFAULT_*` 常量）；`TransactionCostModel.__init__` 支持 `overrides` 注入（构造时调用 `load_market_cost_config`）
- **② 融资成本项**：`CostConfig` 新增 `financing_rate_annual=0.0`（默认关闭，`FTS_COST_FINANCING_RATE_ANNUAL`）；`TransactionCostModel.adjust` 增加融资成本——`成本_bps += 名义持仓市值(平均占用) × 保证金率(margin_rate_map 按品种，缺省 0.12) × 年化利率 × 持有天数/365`；计入 `total_cost_bps` 与 `net_sharpe` 惩罚；回测报告新增 `financing_cost_bps` 分项（AdjustedMetrics 扩展）
- **③ 回测成本构成明细**：`backtest_pipeline.py`/报告输出 `cost_breakdown`（手续费/滑点/冲击/融资/展期 5 分项 bps），供容量分析与成本敏感性（GAP-061）复用
- **④ 实证标定脚本** `scripts/calibrate_impact_cost.py`：TQ-Local 17709 真实主力合约（动态池 25 品种同源）近 N 交易日——按日聚合"成交量占比（信号量/日成交量）vs 实现滑点"样本 → 线性/幂回归 `impact = a × (vol_ratio)^b` → 输出标定参数建议 + 更新 `load_market_cost_config` 缺省或生成标定报告（`memory/reports/impact_calibration_*.json`）；数据不足/API 失败优雅降级
- **测试** `tests/factor_engine/test_cost_calibration.py`（约 12 用例）：配置注入（env/overrides/缺省优先级）/融资成本单调性（利率↑ 成本↑）/margin 按品种差异化/AdjustedMetrics 分项字段/回测成本构成明细/标定回归函数（合成样本直线拟合斜率）/数据不足降级/开关关闭路径

- **✅ 实施状态（2026-08-11 首期完成）**：`load_market_cost_config`（`FTS_COST_*` env > overrides > 默认）+ 融资成本项（`CostConfig.margin_rate/financing_rate_annual` + `AdjustedMetrics.financing_cost_bps/cost_breakdown` + `adjust` 接入）+ `scripts/calibrate_impact_cost.py`（log-log 幂回归 + 动态池/静态池符号 + 报告输出）已落地；`tests/factor_engine/test_cost_calibration.py` 20 用例全绿（原设计约 12，实做 20）。**剩余**：真实盘口实证标定需 TQ-Local 17709 实盘行情窗口（脚本已就绪，随数据积累运行产出标定报告）；回测报告成本构成明细输出与 backtest_pipeline 报告生成器联动待接入。

#### C8 基础设施深化（在线人审协作工作台 + 算子库扩容至数百，🟡）

| 维度 | 内容 |
|---|---|
| **现状（代码依据）** | GAP-I102 `FactorReviewWorkflow`（v2.72.0/v2.82.0）为 CLI 审查队列 + 经验链回写，**无 Web 工作台**；expr_dsl 注册表 + feature_ops 组合算子（GAP-I202，v2.75.0 双注册表共享）合计 ~50+ 算子，**规模距"数百"仍有差** |
| **机构级标准** | 在线人审协作（Web 界面：候选列表/一键审批/意见回写/历史可查）；数百算子单一事实源 |
| **弥补建议** | ① 人审工作台：基于既有 `monitor/http_server.py` 扩展审查端点（list/approve/reject 复用 `FactorReviewWorkflow`），前端只读轻量页面（遵循 memory 用户规则：微信排版兼容的内联样式，无外链资源）；② 算子扩容：批量登记 20~30 个高价值算子（时序统计：ts_skew/ts_kurt/ts_argmax/ts_argmin/滚动分位差；截面：cs_zscore/cs_rank_diff；条件：cross_where/ts_consecutive_count），每个算子配套经济语义 + 参数边界 + 单元测试 + 双注册表 required_shared 校验（对齐 GAP-I202 v2.75.0 模式）；③ 算子文档自动生成（registry 元数据 → `docs/harness/_data/operator_catalog.yaml`，文档引用保持一致） |
| **验收标准** | 工作台完成 ≥ 1 轮真实审查（approve/reject 全流程走通）；算子注册表 ≥ 100 且双注册表一致性校验通过；新算子 100% 配套测试 |
| **测试方案** | 审查端点 API 测试；新算子功能/边界/一致性测试；目录生成幂等 |

**C8 实施设计（契约优先，2026-08-11）**

- **① 人审工作台**（`fts/monitor/http_server.py` 扩展，纯标准库零新依赖）：
  - 端点：`GET /review`（工作台页面，内嵌内联样式无外链资源）/ `GET /api/review/pending`（`FactorReviewWorkflow.list_pending(market, limit)` 队列 JSON）/ `POST /api/review/approve`（body: factor_id/comment/reviewer）/ `POST /api/review/reject`（同构）
  - 页面：待审查队列表格（factor_id/name/market/source/ic/sharpe）+ 每行意见输入 + 批准/驳回按钮 + 已审查历史列表（get_status 逐个查询）
  - 复用 `FactorReviewWorkflow`（factor_inspector.py，GAP-I102）——审批/驳回回写 DuckDB factor_reviews 表 + 经验链，Web 与 CLI 同一后端
- **② 算子扩容**：新增 22 个高价值算子（feature_ops 原语 + `OperatorRegistry` + `expr_dsl.registry` 双登记 + required_shared 更新）：
  - L1 时序统计 12：`ts_argmin`（最小值位置）/ `ts_ema`（指数移动平均）/ `ts_mad`（中位数绝对偏差，稳健离散）/ `ts_range`（振幅 (max-min)/mean）/ `ts_iqr`（四分位距）/ `ts_quantile_range`（分位差 q_hi−q_lo）/ `ts_return_over_max`（距滚动高点回撤）/ `ts_min_max_ratio`（max/min 比）/ `ts_std_ratio`（短/长波动比，均值回归强度）/ `ts_roc_sum`（窗口收益累加）/ `ts_breakout`（突破滚动新高）/ `ts_cumulative_return`（累计收益）
  - L2 截面 4：`cs_rank_diff`（排名变化）/ `cs_zscore_diff`（zscore 变化）/ `cs_extreme_ratio`（|z|>n_std 占比）/ `cs_median_dev`（与截面中位数偏离）
  - L3 条件 3：`where_gt`（x>阈值取 a 否则 b）/ `consecutive_true`（连续满足条件计数）/ `sign_flip`（符号翻转计数）
  - L5 领域 3：`mean_reversion_z`（均值回归强度 −zscore）/ `trend_strength`（趋势强度 |slope| 归一化）/ `volume_pressure`（量价压力 量比×涨跌幅）
  - 每个算子：经济语义 + 参数边界（param_bounds）+ 单测；`verify_registry_consistency` 强制双注册表共享（新增算子全部加入 required_shared）
- **③ 算子目录自动生成**：新建 `scripts/generate_operator_catalog.py`——读 `expr_dsl.registry.build_registry()` 元数据 → 生成 `docs/harness/_data/operator_catalog.yaml`（name/category/params/bounds/economic_meaning），幂等（重复生成内容一致）
- - **测试**：`tests/factor_engine/test_operator_expansion.py`（每算子 ≥1 功能 + 边界 ≈ 45 用例：22 算子 × 2 + 注册表一致性 + 目录生成幂等）；`tests/monitor/test_http_server.py` 增补审查端点（pending 列表/approve/reject/404）

**C8-2 机审/人审可配置（实施设计，2026-08-11）**

> 用户决策（AskUserQuestion 确认）：① 机审正常因子 → **自动批准落库**（reviewer=auto）；② 低质因子 → **自动驳回落库**（reviewer=auto）；③ 触发方式 → **手动批量触发**（CLI + Web 按钮）。默认机审，个别异常值（IC/Sharpe 极端偏高或缺失）转人审。

- **① 审查模式**：`ReviewMode` 枚举（`factor_inspector.py`）——`auto`（默认，机审优先）/ `manual`（纯人审，现状 GAP-I102）。读取 `FTS_REVIEW_MODE` env（默认 auto，不触碰受保护 settings.py，对齐 `FTS_REVIEW_EXPERIENCE_CHAIN` 既有惯例）
- **② 机审规则** `AutoReviewPolicy`（dataclass，env 可覆盖 `FTS_REVIEW_MIN_IC=0.02`/`FTS_REVIEW_MAX_IC=0.8`/`FTS_REVIEW_MIN_SHARPE=0.5`/`FTS_REVIEW_MAX_SHARPE=30.0`）：
  - `classify(ic, sharpe) -> (decision, reason)` 三态判定——**缺失**（ic/sharpe 为 None/NaN，无法机审）→ 转人审；**极端偏高**（ic>max_ic 或 sharpe>max_sharpe，疑过拟合/未来函数）→ 转人审（"个别异常值人审"）；**低质**（ic<min_ic 或 sharpe<min_sharpe）→ 自动驳回；**正常** → 自动批准
- **③ `FactorReviewWorkflow.auto_review(limit=200, policy=None, force=False)`**：遍历 pending 队列逐因子 classify——APPROVED/REJECTED 调既有 `_decide`（reviewer="auto"，comment 自动生成原因，复用幂等 UPSERT + 经验链）；HUMAN 跳过保持 pending；返回统计 `{mode, auto_approved, auto_rejected, needs_human, skipped}`；`manual` 模式默认拒绝执行（报错提示），`--force` 显式覆盖（用户决定权）
- **④ Web 工作台**：`/api/review/pending` 响应增 `mode` 字段 + 每因子 `needs_human`/`review_reason` 标注（classify 判定，不落库）；新增 `POST /api/review/auto`（auto_review）；页面增"当前模式"徽标 + "运行机审"按钮 + 异常行"需人工"标记（内联样式，微信排版兼容）
- **⑤ CLI**：`fts factor review auto [--limit N] [--force]`
- **测试**：`tests/factor_engine/test_auto_review.py`（约 20 用例：classify 全分支 6/缺失·极端·低质·正常/auto_review 批量落库 approved·rejected（reviewer=auto）/needs_human 保持 pending/manual 拒绝 + force 覆盖/幂等（重复 auto 不重复处理）/env 覆盖阈值/CLI 命令）+ `tests/test_http_server.py` 增补 auto 端点与 pending mode 字段

- **✅ C8-2 实施状态（2026-08-11 完成）**：`ReviewMode`（`FTS_REVIEW_MODE` 默认 auto）+ `AutoReviewPolicy`（`FTS_REVIEW_*` 四阈值 env 覆盖，classify 三态：缺失/超上限→人审、低于下限→自动驳回、正常→自动批准）+ `auto_review`（复用 `_decide` 落库 reviewer=auto，manual 模式拒绝 + `--force` 覆盖）；CLI `fts factor review auto [--limit] [--force]`；Web `/api/review/pending` 增 `mode`+`needs_human` 标注 + `POST /api/review/auto` + 页面"运行机审"按钮/模式徽标/需人工标记；`test_auto_review.py` 28 用例 + http_server 增补 5 用例全绿（141 passed 定向回归），同步 01/03/06/07/08。**剩余**：机审结果人工复核流程（可选，待实际使用反馈）与审计链对机审 auto-approve 因子的抽样复核。 

- **✅ 实施状态（2026-08-11 首期完成 + 浏览器冒烟走通 + C9 扩容二期）**：① 人审工作台落地 `fts/monitor/http_server.py`（`REVIEW_HTML` 内联样式零外链 + `GET /review` + `/api/review/pending|history` + `POST /api/review/approve|reject`，复用 `FactorReviewWorkflow` 与 CLI 同一后端，15s 自动刷新）；② 22 算子扩容落地（`C8Ops` L1 12 + L2 4 + L3 3 + L5 3，`OperatorRegistry` category=c8 + `expr_dsl.registry` `_c8_ops` + `required_shared` 22 项并入）——DSL 算子 80→**102**、双注册表 `consistent=True`（验收「注册表 ≥100 且一致性通过」达成）；③ 目录自动生成落地 `scripts/generate_operator_catalog.py` → `docs/harness/_data/operator_catalog.yaml`（幂等验证通过）；④ 测试：`test_operator_expansion.py` 57 用例 + `test_http_server.py` 增补 14 用例全绿（验收「新算子 100% 配套测试」达成），定向回归 296+374 passed；⑤ **浏览器冒烟走通（2026-08-11）**：`python -m fts.cli ui --port 9100` 启动 → 浏览器打开 `/review` 渲染 200 条真实待审队列（factor_id/name/market/source/ic/sharpe + 意见输入 + 批准/驳回按钮）→ 真实 approve 全流程（POST 落库 factor_reviews + 队列移除 + 历史可查）验证通过（验收「工作台完成 ≥1 轮真实审查」达成）。⑥ **C9 算子扩容二期（2026-08-11）**：新增 30 算子（`C9Ops` L1 时序 14 + L2 截面 5 + L3 条件 4 + L5 领域 7，含 ts_pct_rank_window/ts_zscore_rolling/ts_kurt（std.where 常数序列→0）/ts_slope_pct/cs_rank_norm/where_between/cross_above/vol_regime/sign_entropy 等，全部滚动窗口 NaN 兜底 + 双注册表 `required_shared` 并入 30 项）——DSL 102→**132**、GP 81→**111**、`verify_registry_consistency` consistent=True/mismatched=0；`test_operator_expansion_c9.py` 39 用例全绿（L1 时序 14 + L2 截面 6 + L3 条件 4 + L5 领域 7 + 注册表一致性 4 + 目录覆盖 4），实测 DSL 132 / GP 111 双注册表一致。**剩余**：算子库距数百仍剩远期扩容量；工作台鉴权/外网暴露部署待基建。

> **优先级与资源建议**：C1/C3/C4 直接对 Stage 3 退出标准（②④①），建议首批推进；C2 数据合规成本低（公开新闻 API + 词典法）可作为突破点；C5/C6/C7 属深化项，依赖 C1/C3 落地后衔接；C8 与日常开发并行，低风险高可见度。

> 版本号说明：本计划从 v2.65.0 起排期，与 plans/22（v2.60.0~v2.64.0）、plans/21 当前处理中项（GAP-F01~F16）并行推进；实际版本号以 pyproject.toml 为准，每个阶段完成后 bump 并同步 07-operations.md。

---

## 4. 与既有计划的关联与去重

| 关联项 | 关系 |
|:-------|:-----|
| plans/10-evolution-optimization-plan.md | 演化优化细则（UCT/多岛/多Agent/GP 补充）——本计划 GAP-I201/I204 的既有基础 |
| plans/11-factor-mining-optimization-plan.md | 挖掘优化细则（质量卡/衰减/自适应权重/回测流水线/审计）——本计划 GAP-I305/I302 的既有基础 |
| plans/21-futures-maturity-optimization-plan.md | 期货流水线机构级缺陷（GAP-F01~F16）——Stage 1 前提，与本计划不重叠 |
| plans/22-stock-pipeline-maturity-plan.md | 股票流水线成熟度（GAP-S01~S13）——本计划 GAP-I207/I301/I304 引用其 GAP-S01/S02/S04 |
| plans/20-futures-roll-adjustment-plan.md | 期货展期（GAP-046）——回测保真基础，GAP-I501 前置 |
| plans/19-adaptive-weight-l3-integration.md | L3 自适应权重（GAP-045）——✅ 已关闭，GAP-I302 前置完成 |
| GAP-037（深度学习未实现） | 本计划 GAP-I203 的既有登记——首期（GRU 深度因子）✅ v2.73.0 关闭；GAN 合成/Transformer 远期（GAP-I503 二期） |
| GAP-041（覆盖率 <90%） | Stage 1 回归门槛（16 模块补测） |
| GAP-045（adaptive 权重完整接入 L3） | ✅ 已关闭（v2.56.0，Phase 33：FactorStyle/style_tags 维度 + RegimeSmoother 平滑 + family×style 双维调整）——GAP-I302 前置完成 |
| production_plan.md | 生产就绪路线——GAP-I401/I402 与生产监控联动 |

> 去重原则：本计划只登记**机构级结构性差距**（吞吐/深度/中性化/组合器/反馈闭环等），单条执行细则由对应子计划承载；plans/21/22 已登记的 GAP-S/F 系列仅引用不重复登记。

---

## 5. 验收标准（量化指标）

| 维度 | Stage 1（T1） | Stage 2（T2） | Stage 3（T3） |
|:-----|:--------------|:--------------|:--------------|
| 候选吞吐 | ≥ 500/夜粗筛、≥ 50 细评估 | ≥ 5,000/夜 | ≥ 50,000/夜（分布式） |
| elite 因子库 | ≥ 300 | ≥ 1,000 | ≥ 10,000（含深度因子） |
| 组合层 | 股票/期货统一 L3 | 风险平价/均值方差可选 | ML 组合层 |
| 回测保真 | 冲击成本 + 容量约束 | 完整成本 + 容量报告 | 实时成本监控 |
| 反馈闭环 | 实盘数据回流 + 对比报告 | 衰减自动退役 | 在线学习（C6 自动重校准） |
| 测试 | 全量回归通过 + 覆盖率 ≥ 92% | 深度模型测试全绿 | 分布式一致性测试（C4 集群） |

---

## 6. 不在范围

- 真实券商/交易所网关与订单执行（下游 FDT 角色边界，FTS 只产信号）
- 全市场股票覆盖（当前 CSI300 子集，全市场为远期方向）
- 另类数据采购与数据合规（Stage 3 评估阶段不采购）
- 现有 elite 因子库全量重建（以新增与去冗余为主）
- 各子计划已登记的执行细则（plans/10/11/20/21/22）不重复展开

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/factor_engine/evolution_loop.py`（GAP-I201/I206 + C1 `run_microstructure_promotion`）；`fts/factor_engine/evaluation_chain.py`（GAP-I207）；`fts/factor_engine/micro_evolution.py`（GAP-I205）；`fts/factor_engine/gp_evolver.py`（GAP-I204）；`fts/factor_engine/portfolio_loop.py` + `portfolio_optimizer.py` + `portfolio_constructor.py`（GAP-I301/I302/I303）；`fts/factor_engine/cost_model.py` + `backtest_pipeline.py`（GAP-I501）；`fts/factor_engine/meta_loop.py`（GAP-I101）；`fts/factor_engine/factor_inspector.py`（GAP-I102 + C8-2 ReviewMode/AutoReviewPolicy）；`fts/factor_engine/expr_dsl/registry.py` + `feature_ops.py`（GAP-I202 + C8/C9 算子扩容）；`fts/ml/models.py`（GAP-I203）；`fts/factor_engine/elite_tracker.py` + `feedback_loop.py` + `signal_contract.py` + `bridge/signal_bridge.py`（GAP-I401/I305/I402）；`fts/factor_engine/factor_db/repository.py`（GAP-I305/I206）；`fts/factor_engine/factor_db/schema.py`（factor_reviews UPSERT 索引修复）；`fts/monitor/live_factor_monitor.py`（GAP-I402）；§3.3 C1~C8 映射——`fts/factor_engine/microstructure_generator.py` + `evolution_loop.run_microstructure_promotion`（C1）、`fts/factor_engine/alternative_sentiment.py`（C2，含 `LlmSentimentScorer` LLM 精修）、`fts/factor_engine/black_litterman.py`（C3）、`fts/factor_engine/executor_backend.py` + `docs/production_plan.md`（C4）、`fts/ml/models.py` + `deep_factor.py`（C5，TransformerFactorModel）、`fts/factor_engine/recalibration.py` + `feedback_loop.py`（C6）、`fts/factor_engine/cost_model.py` + `scripts/calibrate_impact_cost.py`（C7）、`fts/monitor/http_server.py` + `factor_inspector.py` + `expr_dsl/registry.py` + `feature_ops.py`（C8/C9） |
| 可验证断言 | 20 项 GAP-I 全部登记（P0×5 / P1×10 / P2×5）且 I101~I503 全部关闭（✅ v2.85.0）；差距总览矩阵 16 行中 13 行状态按最新落地刷新（v2.87.0）——L1 知识补给/Alpha 审查、L2 挖掘吞吐/算子库/搜索方法/过拟合/中性化、L4 反馈闭环/在线监控、基础数据深度/计算资源 差距消除或降档，残留差距均有明确依据（实证标定/另类数据/在线学习/分布式部署）；GAP-I201 完成记录补充（`BatchMiner` 批量漏斗 v2.71.0 + `ExecutorBackend` v2.83.0）；Stage 1/2 达标、Stage 3 首期完成；剩余差距拆分为 §3.3 C1~C8 实施方案（v2.100.1 修订：Level2 入 elite / 另类数据 / ML 组合层 / 多节点分布式 / GPU 深度模型 / 在线学习 / 回测实证化 / 基础设施深化），每项含现状代码依据/机构标准/弥补建议/实施步骤/验收标准/测试方案，与 Stage 3 退出标准①~④ 逐项映射（C4/C1/C2/C3）；**2026-08-11 首期实施状态断言（v2.101.0 日常开发追加）**——C1~C8 全部首期实施 ✅（C1 评估晋升接线 `run_microstructure_promotion` / C2 词典法+`LlmSentimentScorer` LLM 精修一致性验证 / C3 BL 闭式后验 / C4 Dask LocalCluster / C5 Transformer 因果掩码 / C6 重校准队列 / C7 实证标定脚本+融资成本 / C8 人审工作台+机审可配置）；C8 延续 C9 算子扩容二期 30 算子落地——DSL 102→**132**、GP 81→**111**、`verify_registry_consistency` consistent=True/mismatched=0、`test_operator_expansion_c9.py` 39 用例；5 个待人工确认异常因子模拟审批测试固化（`test_simulated_approval.py` 9 用例：机审五类异常全转人审 → auto_review 保持 pending → 模拟批准/驳回落库 → 队列收敛）；路线图版本衔接 v2.65.0+ 与 plans/22 无冲突；实施按 §3 分阶段推进 |
| 检验方式 | `python scripts/verify_doc_consistency.py`；各缺陷项落地时配套 `pytest tests/... -v` 回归；每个 Stage 退出时运行全量回归 + 一致性 13/13 |
