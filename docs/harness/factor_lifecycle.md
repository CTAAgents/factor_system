# 因子全生命周期管理（FTS）

> 版本: v3.0.0+25
> 最后更新: 2026-08-17
> 适用范围: 期货主链路（futures / energy，股票链路已剥离至 fts-stock）
> 关联文档: [01-architecture.md](file:///d:/Programs/factor_system/docs/harness/01-architecture.md) · [05-observability.md](file:///d:/Programs/factor_system/docs/harness/05-observability.md) · [07-operations.md](file:///d:/Programs/factor_system/docs/harness/07-operations.md) · [plan 45](file:///d:/Programs/factor_system/docs/harness/plans/45-l2-loop-split-plan.md)

---

## 1. 目标与核心原则

因子管理覆盖 **L0 供给 → L1 注入 → L2 演化晋升/评审 → L3 组合构建 → 持续监控与退役** 的完整闭环。核心原则：

- **不质检不入库 / 不复检不续役 / 不审批不准入**（CTA 手册 6.1）；
- 因子库 **DuckDB 为 SSOT**（`data/factor_catalog_{futures,energy}.duckdb`），评审、评分卡、审计、状态历史均为独立旁路表，禁止 JSON 绕过落库；
- 晋升、评审、冷却、退役为**四条独立流程**，互不替代，各司其职；
- 零未来函数 / 零数据窥探 / 多重检验校正贯穿全链（AGENTS.md 红线）。

## 2. 生命周期总览

![因子全生命周期流程图](factor_lifecycle_flow.svg)

主链路：**L0/L1 供给 → L2 演化晋升 → L2 评审质检统一任务（每日 04:00：周日执行重量级 l2_review_job 全量重审 + 衰减淘汰 + 阀门收口，其余日执行轻量阀门 + 巡检 + 监控，L3 强制 approved 闸门）→ approved → Regime-Driven 策略合成**（v3.0.0 组合侧退役），底部持续监控与生命周期闭环（机审阀门 / 巡检降级 / 逻辑监控 / M·F·D 复检 / 退役红线 / 7 状态机 / 冷却期）将降级与退役反馈回评审，冷却期满可回归。

| 阶段 | 调度 | 产出 |
|:-----|:-----|:-----|
| L0/L1 供给 | 每日 00:00 `l1_meta_loop` | 知识补给 + 种子注入 → 候选池 |
| L2 种子评估晋升 | 每日 01:00（并入「L2 种子评估+因子演化」合并任务第一步，v2.105.0+3 起） | 种子相关性预检 → 评估 → 晋升 elite |
| L2 演化 | 每日 01:00（合并任务第二步：工作日 ≈10 代 / 周末 ≈50 代，v2.105.0+3 起） | GP 演化 + UCT 选择 → 晋升 elite（Step 1.35 生成端去重前置） |
| L2 批量挖掘 | 周日 06:00 `l2_batch_mining` | BatchMiner 批量漏斗 → L1→L2 合并 |
| L2 评审质检统一任务 | 每日 04:00（v3.0.0 起合并原「L2 周度评审 周日 10:00」+「评审质检阀门+监控 04:00」：周日执行重量级 `l2_review_job` 全量重审 + 衰减淘汰 + 阀门收口，其余日轻量阀门 + 巡检 + 监控） | reaudit 重审 + 衰减评估 + 自动淘汰 + approved 落库 / 退化检测 / 行为漂移 / 数据质量 |
| L3 组合构建 | 工作日 05:00 `fts portfolio run --universe energy` | 加载 approved 因子 → 去重 → 权重重算 |
| 信号管道 | 工作日 20:00 | 消费 L3 权重生成信号报告 |

> **调度源（v2.104.0+99）**：内部 `fts/scheduler` 定时任务默认全部停用（`INTERNAL_SCHEDULER_ENABLED` 读 `FTS_INTERNAL_SCHEDULER_ENABLED`，默认 "0"），周期任务由 **TRAE Schedule 定时自动化**执行（时间与上表一致），内部调度器不重复执行。一键启停：`$env:FTS_INTERNAL_SCHEDULER_ENABLED="1"; fts scheduler run`（启用）· `Remove-Item Env:FTS_INTERNAL_SCHEDULER_ENABLED; fts scheduler run`（停用）· `fts scheduler status`（查看状态）。

### 2.1 端到端操作流程矩阵（周期环节 × 输入输出 × 验证 × 异常）

| 环节 | 触发（任务 + 调度） | 输入 | 核心动作 | 输出落库 | 验证命令 | 异常处置 |
|:-----|:--------------------|:-----|:---------|:---------|:---------|:---------|
| **L0/L1 供给** | `l1_meta_loop` 每日 00:00 | 知识库/网页/种子源 + LLM + 市场快照（web_collector） | MetaLoop 知识补给 + Bootstrapping + 种子注入，市场快照注入 bootstrap prompt | 候选池 `factor_pool.json` + 注入 `l1_injected/`（`injected_candidate_ids`） | `pytest tests/factor_engine/test_meta_loop.py -q` | 异常仅日志不阻断次日；无数据跳过 |
| **L2 种子评估** | `l2_seed_promotion` 每日 01:00（并入「L2 种子评估+因子演化」合并任务第一步，v2.105.0+3） | L1 注入种子 + 种子池 + 期货横截面训练集（排除盲测池） | `run_seed_stage`：相关性预检 → 全链质检（Verifier/消融/审计/WF）→ 晋升 elite（不重置演化计数器） | `factor_catalog`（futures）+ elite 快照 + `shadow_pool` | `pytest tests/factor_engine/test_evolution_loop.py -k "seed or promote" -q` | 任一关卡失败拒绝晋升退回候选池；训练品种 <10 跳过 |
| **L2 演化** | 每日 01:00（合并任务第二步，工作日 ≈10 代 / 周末 ≈50 代，v2.105.0+3 起，原 `l2_evolution_weekday`/`l2_evolution_weekend` 已并入） | elite 父池（UCT 选择）+ 期货横截面面板 | 生成端去重前置（Step 1.35）→ GP/深度/算子 DSL 演化通道 → 准入链（Verifier → 去冗余 → B.4 高IC → 多重检验 → WF → 审计 → 评分卡 → 影子池） | `factor_catalog` + elite 快照 | `pytest tests/factor_engine/test_evolution_loop.py -q` | 熔断隔离（`_consecutive_low_ic` 保存/恢复）；数据不足跳过 |
| **L2 批量挖掘** | `l2_batch_mining` 周日 06:00 | elite 父因子（UCT）+ 期货横截面面板 | `run_batch_stage`：BatchMiner 同父多后代批量生成 → 并行粗筛 → 逐个走准入链 | `factor_catalog` + elite 快照 | `pytest tests/factor_engine/test_batch_mining.py -q` | 熔断隔离（batch 失败不污染演化状态）；无父因子跳过 |
| **L2 周度评审（重量级）** | 每日 04:00 统一任务·周日分支（`l2_review_job`，v3.0.0 起并入） | 全部 active elite（含 L3 池 approved 子集 `factor_reviews.approved`） | Step A reaudit 新标准重审（retain/shadow/retire）→ Step B 衰减评估 + AutoRetire 同步 DuckDB → Step C `_review_gate_weekly`（`review_l3_pool` 复核 L3 池 + `list_pending` 机审兜底） | `factor_reviews` + `factor_status_history` + `factor_catalog`（retire/demote）+ tracking 快照 | `pytest tests/factor_engine/test_review_workflow.py tests/factor_engine/test_qa_gate.py tests/factor_engine/qa/ -q` | Step A 失败不阻断 B/C；rejected/质检失效退回 L2 冷却池（宁缺毋滥） |
| **L3 组合构建** | `fts portfolio run --universe energy` 工作日 05:00（energy 链，v2.105.0+3；原 `l3_portfolio_loop` 期货 06:00 保持期货侧） | **仅 approved**：`factor_reviews.decision='approved'`（L3 唯一消费对象；从 active elite 加载，经质量门/影子池后硬过滤，rejected/未评审剔除） | 加载 active elite → 质量门 → 影子池剔除 → **approved 硬过滤** → 去重/聚类/PCA → 权重重算（quality_weight 默认）→ Step 2b 子链调制 + Step 2.5 Gate → Verifier 校验 | 组合权重快照 + `combo_history` | `pytest tests/factor_engine/test_portfolio_loop.py -k "ReviewApproved or LoadEliteDuckdb" -q` | 失败仅日志；冷启动保护 / 冻结日 `status='frozen'` 不重建 |
| **信号管道** | `futures_signal_pipeline` 工作日 20:00 | L3 权重（周五重算快照，其余日冻结复用）+ 全量品种行情 | Ridge 权重（周五重算）→ 多空双向信号排名 → 报告 | `reports/futures/{date}/futures_signals_*.md` | `pytest tests/test_futures_signal_pipeline.py -q` | 权重冻结仅刷新因子值；失败仅日志 |
| **评审质检统一任务（轻量子流程）** | 每日 04:00（v3.0.0 起并入原 `factor_inspector` 04:00 · `logic_monitor` 04:30 · `data_level_monitor` 05:00，周日叠加重量级分支）：① `_review_gate_weekly` 机审 → ② 巡检降级 → ③ 逻辑监控 → ④ 数据级监控 → ⑤ 因子级监控 · `data_quality_eval` 每 5min · 月度/季度/半年度复检 | elite 因子 + 行情/因子库 | 机审（pending→approved/rejected）· 退化检测（`inspect_and_downgrade`，Sharpe 降 20% → 降级；**approved 因子豁免仅标记待周度评审收口**）· 行为漂移/极端预测/换月异常 · 缺失率/异常值/多源分歧 · M1-M5/F1-F6/D1-D4 复检 · 退役 5 红线 | `factor_catalog`（`status='degraded'`/RETIRED）+ tracking 快照 + 监控报告 | `pytest tests/factor_engine/test_qa_gate.py tests/factor_engine/qa/ -q` | **approved 因子豁免每日降级（周日重量级 `review_l3_pool` 收口）**；非 approved 退化 → degraded + 30 日冷却；退役 → RETIRED；缓存缺失跳过不中断调度 |

> 注：energy 能化链专属定时任务已删除（v3.0.0+1，plans/57 双系统切分），energy 因子库 `factor_catalog_energy.duckdb` 作为**候选池**保留，`l2_energy_qa_review_job` 等 energy job 函数仅供手动调用（`FTS_ENERGY_QA_REVIEW_APPLY=1` 落库），冷却期 30 **交易日**、两次不达标退役（宁严勿松），详见 §5.3。全期货评审质检由每日 04:00 统一任务执行（周日重量级分支调 `l2_review_job`）。调度注册表全量核对见 `fts/scheduler/tasks.py`（内部已停用，TRAE Schedule 为唯一调度源——当前 6 个 Active 全期货任务，其中「FTS L2 因子生命周期管理+监控」每日 04:00 已合并接管原周日评审与每日阀门/监控）。

## 2.2 术语统一：状态命名与对象集合

> 单一事实源：`fts/factor_engine/qa/status_board.py` `FactorStatus`（7 状态唯一名）· `STATUS_LABELS`（中文名）· `STATUS_ALIAS_MAP`（全量别名归一）· `normalize_status()`（契约层统一，v2.104.0+95）。

### 2.2.1 因子对象集合

| 术语 | 定义 | 维度 |
|:-----|:-----|:-----|
| `active elite` | `factor_catalog.is_elite=1 AND status='active'`（服役中精英因子全集，状态机归一 CORE/CANDIDATE） | 生命周期状态 |
| `L3 approved` | `factor_reviews.decision='approved'`（评审质检通过，L3 组合唯一消费对象） | 评审决策（正交维度） |

**包含关系：`L3 approved ⊆ active elite`**——评审只写 `factor_reviews`（`review_inplace`/`review_l3_pool` 不改 `factor_catalog.status/is_elite`），故 approved 因子必然仍在 active elite 中；反之 active elite 还包括未评审（PENDING_QA）、影子池观察期、被撤销 approved 但未降级/退役的因子。每日巡检/监控（04:00 `factor_inspector` / 04:30 `logic_monitor`）对象为 **active elite 全量（不区分 approved）**；周日评审 Step C 对象为其中 **approved 子集（L3 池）+ pending 待审**。

### 2.2.2 统一状态字典（7 状态唯一名）

| 唯一状态 | 中文名 | 现状等价命名（别名 → 归一） |
|:---------|:-------|:----------------------------|
| `DRAFT` | 草稿 | — |
| `PENDING_QA` | 待质检 | — |
| `CORE` | 核心服役 | 主表 `active` · reaudit `retain` · tracker `active` |
| `CANDIDATE` | 候选服役 | — |
| `OBSERVATION` | 观察期 | 主表 `degraded` · reaudit `shadow` / 历史拼接 `active(shadow)`（v2.104.0+95 起新写入即 `OBSERVATION`）· tracker `observing`/`decaying`/`critical_decay` |
| `SUSPENDED` | 暂停 | — |
| `RETIRED` | 退役 | 主表 `retired` · reaudit `retire` · tracker `retired`/`deprecated` |

**归一机制**：`normalize_status()` 经 `STATUS_ALIAS_MAP` 将主表小写 `status`、reaudit 处置、tracker 快照等历史命名统一映射到上表唯一状态；未知值原样返回（不误归一）。状态含义与组合权重上限见 §6.1；`factor_reviews.decision`（approved/rejected）为评审**决策维度**，不并入状态机。看板以「唯一状态(中文名)」输出，保证识别不混淆。

### 2.2.3 同名标识符消除（一名多义根治）

历史上 `FactorStatus` **一名三义**（同名冲突，v2.104.0+95 起根治）：

| 位置 | 原标识符 | 语义 | 处理后 |
|:-----|:---------|:-----|:-------|
| `fts/core/enums.py` | `FactorStatus`（PENDING/INJECTED/DECAYED/REJECTED） | 种子池候选状态 | → `CandidateStatus` |
| `fts/factor_engine/qa/status_board.py` | `FactorStatus`（7 状态） | 生命周期服役状态 **SSOT** | 保留 |
| `fts/monitor/elite_tracker.py` | `FactorStatus`（Literal） | 衰减追踪快照状态 | → `TrackerStatus` |

现在全项目 `FactorStatus` 仅指生命周期 7 状态机（唯一含义，不混淆）。

## 3. 晋升门槛完整校验链（L2 → elite）

晋升入口统一收敛于 `_promote_to_elite`（[evolution_promote.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_promote.py#L510)）。评估阶段产出 `evaluation` 后，晋升前按序通过以下**强制门**：

| 序 | 校验门 | 判定规则 | 不通过后果 |
|:--:|:-------|:---------|:----------|
| 0 | **Verifier**（评估阶段） | IC ≥ 0.03 且 Sharpe ≥ 1.5（`DEFAULT_VERIFIER_CONFIG`） | 评估不达标，不进入晋升 |
| 1 | **L2 准入去冗余**（GAP-I206） | 与存量 elite 高相关（≥0.8）且命中 shadow 判定 | 高相关拦截，不落库 |
| 2 | **B.4 高IC筛查**（[high_ic_screener.py](file:///d:/Programs/factor_system/fts/factor_engine/high_ic_screener.py)） | 16 项检查 × 6 大模块，总分归一 100 分；A≥85 入库 / B 60~84 暂缓 / C<60 剔除 | C 级直接拦截；**B 级且跳过项 >8（信息不足）同拦截** |
| 3 | **多重检验** | `level_3_multiple.passed == True`（Bonferroni 校正） | 未通过多重检验校正 → 拒绝晋升 |
| 4 | **WalkForward 多窗口 OOS**（GAP-121） | `n_windows_completed ≥ 2` | 单窗口/缺失 → 数据窥探嫌疑，禁止晋升 |
| 5 | **6 项审计硬门**（[audit.py](file:///d:/Programs/factor_system/fts/factor_engine/audit.py)） | `audit_report.passed == True`，且非 skipped 项全部通过 | 任一关键项 failed → 禁止晋升 |
| 6 | **质量评分卡**（记录分级，非硬门） | 50 分制：A≥40 / B≥30 / C 淘汰（写入 `factor_quality_scores`） | 仅记录分级，不单独拦截 |
| 7 | **影子池标记** | 晋升写入 `shadow_pool.observe_until`（默认 5 交易日，`FTS_EVOLUTION_SHADOW_OBSERVE` 可关闭） | 观察期内 L3 不纳入组合 |

> **B.4 高IC筛查 5 项一票否决（V1-V5）**：OOS 外样本 IC 衰减 >0.30 · 极值扰动 IC 降幅 >0.25 · 与存量因子相关 >0.70 · 扣成本后超额收益 <0 · 经济逻辑维度 <2.0。任意触发直接 C 级剔除。

> **6 项审计（FactorAuditor）**：① 因果检验（Granger/反事实）② 样本外验证（WalkForward OOS）③ 跨品种验证（≥80% 品种 IC 为正 + A/C 双机制软门控）④ 压力测试（极端行情）⑤ 多重检验（Bonferroni/FDR）⑥ 数据窥探检验（零未来函数）。非 skipped 项须全部通过。

**晋升落库**：DuckDB 主存储（`_write_to_duckdb`，GAP-032 严格一致——DuckDB 成功后才写 JSON 快照，DuckDB 失败不写 JSON 直接判晋升失败），同步写 `factor_quality_scores` / `factor_audit_reports` / `status_history`。

## 4. 评审质检（独立于 L2 的 L2→L3 阀门模块）

**定位**：评审质检不是 L2 内部环节，而是独立于 L2 的 **L2→L3 阀门模块**（实现：[factor_inspector.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_inspector.py#L449) `FactorReviewWorkflow`）：

- **通过（approved）** → 因子从 L2 流向 L3（L3 仅消费 approved 因子，硬过滤 v2.104.0+88）；
- **不合格（rejected / 质检记录缺失）** → 因子退回 L2 冷却池（撤销 approved，回到待审队列）；
- 执行方式：**就地审核**（新晋升 elite 因子即时审核）+ **批量执行**（存量回填 / 周度巡检）。

### 4.1 完整质检门禁（机审升级，v2.104.0+89）

`AutoReviewPolicy.classify` 除 IC/Sharpe 外复核**完整质检结论**（`metadata.qa_review`）：

| 复核项 | 数据源 | 缺失/未通过处理 |
|:-------|:-------|:---------------|
| 6 项审计（audit_passed） | `factor_audit_reports` / metadata | 缺失→转人审；未通过→rejected |
| 质量评分卡（quality_grade，A/B/C） | `factor_quality_scores` / metadata | C 级→rejected |
| 高IC筛查（high_ic_grade，A/B/C） | 晋升链 `qa_review` | C 级→rejected |
| 多重检验（multiple_passed） | `level_3_multiple.passed` | 未通过→rejected |
| WalkForward（walk_forward_windows） | `evaluation.walk_forward` | <2 窗口→rejected |
| Q1-Q10 入库质检（q1_q10_passed） | `build_qa_review`（Q1-Q3 一票否决 + 评分通过率≥0.6） | 未通过→rejected |
| IC / Sharpe | `factor_catalog` 字段 | 超上限（疑过拟合）→转人审 |

**任一关键项缺失 → 转人审（宁缺毋滥）**；全部通过 + IC/Sharpe 正常 → approved。

### 4.2 功能 1：就地审核（新晋升 elite 因子即时审核）

晋升落库后自动触发 `review_inplace(factor_id)`（[factor_inspector.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_inspector.py#L625)）：
- **approved** → 写 `factor_reviews`，因子可流向 L3；
- **rejected** → 写 `factor_reviews`，因子退回 L2；
- **质检记录缺失（needs_human）** → 删除既有 approved，退回 L2 待审队列；
- 失败非阻塞（未审核因子被 L3 approved 硬过滤拦截，宁缺毋滥）。

Q1-Q10 由晋升链 `build_qa_review`（[evolution_promote.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_promote.py#L45)）构造并落库 `metadata.qa_review`：Q1 未来函数←audit snooping_check、Q2 逻辑文档化←economic_logic、Q3 参数网格←params（晋升已过 WF 门）、Q4 IC←level_1.ic、Q5 IR←ir_thresholds、Q6 单调性←monotonicity、Q7 置换←多重检验、Q8 极端行情←audit stress_resilience、Q9 敏感度←robustness_check、Q10 板块←cross_symbol_ratio（audit items 缺失回退整体 passed）。

### 4.3 功能 2：周末定期巡检 L3 池

`l2_review_job` Step C（v3.0.0 起由每日 04:00 统一任务**周日重量级分支**调用）执行 `review_l3_pool(market)`（[factor_inspector.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_inspector.py#L722)）：对 `factor_reviews.decision='approved'`（L3 池）因子按**最新 IC/Sharpe + 完整质检结论 + 相对退化检测**重新复核（v2.104.0+97 防抖升级，双重门槛，任一命中即撤销 approved）——绝对低质（rejected）/ 质检失效（needs_human）/ **相对退化（Sharpe 相对趋势下降 ≥ threshold，默认 -0.2）** → 撤销 approved，**因子退回 L2 冷却池**，不再流向 L3。**每日巡检（`inspect_and_downgrade`）已对 approved 因子豁免直接降级**，本方法为 approved 因子唯一收口出口，保证 L3 组合每周至多变动一次（组合防抖）。

### 4.4 存量批量执行

`scripts/backfill_qa_review.py`：对存量 active elite 因子从 JSON 快照 + `factor_quality_scores`/`factor_audit_reports`/`factor_evaluations` 表重建 `metadata.qa_review`（high_ic 缺失按晋升强制门推断 B）→ 按升级门禁复核（approved 保持 / rejected 回写 / 质检缺失撤销 approved 退回待审）。

### 4.5 reaudit 重审与衰减评估（评审统一任务周日重量级分支配套）

`l2_review_job`（每日 04:00 统一任务周日重量级分支执行）Step A 新标准重审（[reaudit.py](file:///d:/Programs/factor_system/fts/monitor/reaudit.py)）：retain / shadow（robustness 失败→观察池）/ retire（audit 失败或评估不合格）。
`l2_review_job` Step B 衰减评估：`EliteFactorTracker.run_monthly_evaluation()` + `AutoRetireManager` 自动淘汰同步 DuckDB + JSON。

## 5. 冷却规则

冷却机制共三处，独立判定：

### 5.1 影子池观察期（晋升后冷却）

实现：[portfolio_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py#L3449) `_is_shadow_pending`。晋升时写 `shadow_pool = {"promoted_at", "observe_trading_days", "observe_until"}`，L3 `_filter_shadow_pending` 在 `observe_until` 前剔除（观察期内不进正式组合）。默认 `_SHADOW_OBSERVE_TRADING_DAYS = 5` 交易日；种子因子直进正式组合（`shadow_observe=False`），新演化因子走观察池。

### 5.2 degraded 30 日冷却期（降级后冷却）

实现：[evolution_seeds.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_seeds.py#L67) `_within_degraded_cooldown`。`status='degraded'` 因子以 `updated_at` 为降级时间戳，`(now - updated_at).days < _degraded_cooldown_days(30)` 判定冷却期内：

- **冷却期内**：种子评估/演化路径跳过（质检不合格因子 1 个月内不再参与 L2 评估）；
- **冷却期满**：放行重新评估；重新评估合格晋升时**复用原 factor_id**（保留血缘与状态历史，不新建同名行）；
- 时间戳缺失/解析失败 → 默认放行（宁多评估不锁死回归通道）。

### 5.3 energy 冷却期回归（能化链专属）

实现：[energy_qa_review.py](file:///d:/Programs/factor_system/fts/factor_engine/energy_qa_review.py)，energy 专属定时任务已删除（v3.0.0+1），`l2_energy_qa_review_job` 仅供手动调用（`FTS_ENERGY_QA_REVIEW_APPLY=1` 落库）。冷却期 **30 交易日**：到期达标 → 回归 active；**两次不达标 → 退役**；未到期保持 shadow/degraded。判定原则**宁严勿松**（active/shadow/degraded 单维度命中取严）。

## 6. 7 状态机流转

实现：[status_board.py](file:///d:/Programs/factor_system/fts/factor_engine/qa/status_board.py)，CTA 手册 6.8。

### 6.1 状态定义与权重上限

> 唯一状态名与别名归一见 §2.2.2（契约层统一，`STATUS_ALIAS_MAP`）。下表含义/权重为权威口径。

| 状态 | 含义 | 组合权重上限 |
|:-----|:-----|:------------|
| DRAFT | 草稿（未质检） | 0% |
| PENDING_QA | 待质检 | 0% |
| CORE | 核心服役（存量 `active` 兼容映射） | ≤30% |
| CANDIDATE | 候选服役 | ≤15% |
| OBSERVATION | 观察期（衰减/复检预警） | ≤50% 原权重 |
| SUSPENDED | 暂停（风险/不确定） | 0% |
| RETIRED | 退役 | 0% |

### 6.2 合法流转表

```
DRAFT → PENDING_QA → CORE ⇄ CANDIDATE
                       │      │
                 OBSERVATION ⇄ SUSPENDED
                       │
                    RETIRED →（复审重新有效）→ PENDING_QA
```

| 源状态 | 合法目标 |
|:-------|:---------|
| PENDING_QA | CORE, CANDIDATE, RETIRED |
| CORE | CANDIDATE, OBSERVATION, RETIRED |
| CANDIDATE | CORE, OBSERVATION, RETIRED |
| OBSERVATION | CORE, CANDIDATE, SUSPENDED, RETIRED |
| SUSPENDED | OBSERVATION, RETIRED |
| RETIRED | PENDING_QA |

### 6.3 看板输出

`status_board()`：各状态因子数量统计 + 状态变动记录 + 预警因子清单；服役中 = CORE + CANDIDATE。存量库 `status='active'` 兼容归一为 CORE。

## 7. 持续监控与退役

| 机制 | 实现/调度 | 规则 |
|:-----|:----------|:-----|
| 因子巡检降级 | `factor_inspector_job`（每日 04:00） | 扫描 elite 退化因子（质量分/IC 衰减），检测退化 → `status='degraded'` + `is_elite=False`；**approved（L3 池）因子豁免每日降级**（`_is_approved` 判定，仅标记 deferred 待周度评审收口，组合防抖 v2.104.0+97） |
| 逻辑监控 | `logic_monitor_job`（每日 4:30） | 行为漂移 / 极端预测 / 换月日异常 |
| 月度复检 M1-M5 | `monthly_recheck` | 五指标（IC/IR/分层/秩偏离等），1·2·3 项预警降权 50/30/0，连续 3 月退役 |
| 季度复检 F1-F6 | `quarterly_recheck` | 全样本重算标记 |
| 半年度复检 D1-D4 | `semi_annual_recheck` | 逻辑复审 / 回测重跑 / 池重构 / 淘汰库复审 |
| 退役红线（5 条） | `check_retirement` | 独立触发（衰减超限 / IR 跌破 / 连续亏损等），NaN 保守不误判 |
| L3 纯外推衰减（P2） | `_validate_oos_extrapolation` | 晋升后每次 L3 检查新数据 IC，连续 3 次衰减 >20% 标记待降级 |
| 单元粒度退化（子链，灰度） | `energy_qa_review` 退化段 + `factor_lifecycle_review_subchain`（plans/49，v2.104.0+112） | 按 (factor_id, market, chain) 评估——全部有效链衰减→整因子 degrade / 部分链失效→scope_shrink（`_shrink_scope` 剔除失效链更新 `metadata.subchain_scope`，47 调制矩阵自动重算）/ 单链特异因子唯一链失效→degrade；从未 effective→keep；样本不足（< `l3.subchain_quality.min_periods`）→None 不误判；冷却期 `cooldown_days=30` 防过激收缩，期满重审达标回 active。`l3.subchain_quality.enabled=false` 回退全链原逻辑 |
| L3 组合侧退役登记（plans/57，v3.0.0） | `fts/factor_engine/retired_l3.py` + `scripts/scan_l3_retirement.py`（只读） | 35 项 L3 组合侧函数登记弃用（`futures_signal_pipeline` 组合侧 + `portfolio_loop` 策略侧 + `weight_learning`/`capital_allocator`/`regime_crowding` 三模块）；import 期 DeprecationWarning + `warn_if_retired` 调用告警；存量调用点兼容不删码（物理删除为后续独立里程碑）；**因子生命周期本身不受影响**——FTS 保留因子管理 + 信号矩阵输出，策略合成消费侧迁 RD（因子信号契约 v1，design/F.3） |

## 8. 与存储的映射

| 数据 | 表/文件 |
|:-----|:--------|
| 因子主表 | `factor_catalog`（DuckDB SSOT，status/is_elite/market/shadow_pool/metadata） |
| 评审 | `factor_reviews`（factor_id, decision, comment, reviewer, reviewed_at） |
| 评分卡 | `factor_quality_scores` |
| 审计 | `factor_audit_reports` |
| 状态历史 | `factor_status_history` |
| 溯源 | `seed_lineage`（L0→L2） |
| 旧格式 JSON | 仅只读快照/降级回退（生产路径不依赖） |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:---|
| 代码→文档映射 | 生命周期调度：`fts/scheduler/tasks.py` REGISTRY（cron，16 任务，v2.104.0+99 起内部停用）· `fts/scheduler/jobs.py`（l1_meta_loop_job / l2_seed_promotion_job / l2_evolution_*_job / l2_batch_mining_job / l2_review_job / l3_portfolio_loop_job / futures_signal_pipeline_job / factor_inspector_job / logic_monitor_job / data_level_monitor_job）；晋升链：`fts/factor_engine/evolution_promote.py` `_promote_to_elite`；评审：`fts/factor_engine/factor_inspector.py` `FactorReviewWorkflow` / `AutoReviewPolicy` / `_is_approved`（approved 每日巡检豁免）· `review_l3_pool`（叠加相对退化收口，组合防抖 v2.104.0+97）、`fts/scheduler/jobs.py` `_review_gate_weekly`（Step C：review_l3_pool + list_pending 机审）、`fts/monitor/reaudit.py`、`fts/monitor/elite_tracker.py`；冷却：`fts/factor_engine/evolution_seeds.py` `_within_degraded_cooldown`、`fts/factor_engine/portfolio_loop.py` `_is_shadow_pending`、`fts/factor_engine/energy_qa_review.py`；状态机与命名统一：`fts/factor_engine/qa/status_board.py`（`FactorStatus` 7 唯一名 · `STATUS_LABELS` 中文名 · `STATUS_ALIAS_MAP` 全量别名归一 · `normalize_status`）· reaudit shadow 处置以 `OBSERVATION` 写入 status_history（替代历史拼接 `active(shadow)`） |
| 可验证断言 | ① 晋升硬门：`_promote_to_elite` 中 high_ic_screen grade=C 或（B 且 skipped>8）时返回 None、`level_3_multiple.passed=False` 返回 None、`walk_forward.n_windows_completed<2` 返回 None、`audit_report.passed=False` 返回 None；② L3 仅 approved：`_filter_review_approved` 仅保留 `factor_reviews.decision='approved'`；③ 冷却：`_within_degraded_cooldown` 以 `(now-updated_at).days < 30` 判定；④ 状态机：`STATUS_TRANSITIONS` 合法流转 + `STATUS_MAX_WEIGHT` 权重上限 + `STATUS_ALIAS_MAP` 别名归一（`degraded`/`shadow`/`active(shadow)`/`observing`/`decaying`/`critical_decay`→`OBSERVATION`，`active`/`retain`→`CORE`，`retired`/`retire`/`deprecated`→`RETIRED`）；⑤ 端到端矩阵调度：tasks.py REGISTRY cron（L1 00:00 / 种子 02:00 / 演化 03:00 / 批量 周日 06:00 / 评审 周日 10:00——已并入 TRAE Schedule 每日 04:00 统一任务周日重量级分支 / L3 工作日 06:00 / 信号 20:00 / 巡检 04:00 · 逻辑 04:30 · 数据级 05:00——均已并入每日 04:00 统一任务）与 §2.1 矩阵一致；⑥ 组合防抖（v2.104.0+97）：`inspect_and_downgrade` 对 `factor_reviews.decision='approved'` 因子不降级（action=deferred、summary.deferred_approved 计数），`review_l3_pool` 叠加 `FactorLineage.detect_degradation` 相对退化（threshold=-0.2）命中撤销 approved；⑦ 内部调度停用（v2.104.0+99）：`INTERNAL_SCHEDULER_ENABLED` 读 `FTS_INTERNAL_SCHEDULER_ENABLED`（默认 "0"），`register_default_tasks` 后 REGISTRY 全部任务 `enabled=该开关`，`SchedulerEngine` 以 TRAE Schedule 为唯一调度源不重复执行；`fts scheduler status` 查看实际调度数 |
| 检验方式 | `pytest tests/factor_engine/test_portfolio_loop.py -k "ReviewApproved or LoadEliteDuckdb"`；`pytest tests/factor_engine/test_review_workflow.py`；`pytest tests/factor_engine/test_qa_gate.py`（评审阀门/相对退化收口）；`pytest tests/factor_engine/test_factor_inspector.py -k "approved or non_approved"`（approved 豁免）；`pytest tests/factor_engine/test_risk_tag.py`（冷却期）；`pytest tests/factor_engine/test_meta_loop.py`（L1）；`pytest tests/factor_engine/test_batch_mining.py`（批量）；`pytest tests/test_futures_signal_pipeline.py`（信号管道）；`pytest tests/factor_engine/qa/ -v`（状态机/复检/退役）；`pytest tests/factor_engine/qa/test_status_board.py -k "alias or labels"`（命名统一）；`pytest tests/monitor/test_reaudit.py -k "apply"`（shadow 统一状态）；`python scripts/verify_doc_consistency.py` |
