# 因子全生命周期管理（FTS）

> 版本: v2.104.0+89
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

主链路：**L0/L1 供给 → L2 演化晋升 → L2 周度评审（L3 强制 approved 闸门）→ L3 组合构建**，底部持续监控与生命周期闭环（巡检降级 / 逻辑监控 / M·F·D 复检 / 退役红线 / 7 状态机 / 冷却期）将降级与退役反馈回 L2 评审，冷却期满可回归。

| 阶段 | 调度 | 产出 |
|:-----|:-----|:-----|
| L0/L1 供给 | 每日 00:00 `l1_meta_loop` | 知识补给 + 种子注入 → 候选池 |
| L2 种子评估晋升 | 每日 02:00 `l2_seed_promotion` | 种子相关性预检 → 评估 → 晋升 elite |
| L2 演化 | 工作日 03:00（≈10 代）/ 周六 03:00（≈50 代） | GP 演化 + UCT 选择 → 晋升 elite |
| L2 批量挖掘 | 周日 06:00 `l2_batch_mining` | BatchMiner 批量漏斗 → L1→L2 合并 |
| L2 周度评审 | 周日 10:00 `l2_review` | reaudit 重审 + 衰减评估 + 自动淘汰 |
| L3 组合构建 | 工作日 06:00 `l3_portfolio_loop` | 加载 approved 因子 → 去重 → 权重重算 |
| 信号管道 | 工作日 20:00 | 消费 L3 权重生成信号报告 |
| 巡检/监控 | 每日 04:00 巡检降级 · 4:30 逻辑监控 · 05:00 数据级监控 | 退化检测 / 行为漂移 / 数据质量 |

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

`l2_review_job` Step C（周日 10:00）调用 `review_l3_pool(market)`（[factor_inspector.py](file:///d:/Programs/factor_system/fts/factor_engine/factor_inspector.py#L673)）：对 `factor_reviews.decision='approved'`（L3 池）因子按**最新 IC/Sharpe + 完整质检结论**重新复核，不合格（rejected）或质检失效（needs_human）→ 撤销 approved，**因子退回 L2 冷却池**，不再流向 L3。

### 4.4 存量批量执行

`scripts/backfill_qa_review.py`：对存量 active elite 因子从 JSON 快照 + `factor_quality_scores`/`factor_audit_reports`/`factor_evaluations` 表重建 `metadata.qa_review`（high_ic 缺失按晋升强制门推断 B）→ 按升级门禁复核（approved 保持 / rejected 回写 / 质检缺失撤销 approved 退回待审）。

### 4.5 reaudit 重审与衰减评估（L2 周度评审配套）

`l2_review_job` Step A 新标准重审（[reaudit.py](file:///d:/Programs/factor_system/fts/monitor/reaudit.py)）：retain / shadow（robustness 失败→观察池）/ retire（audit 失败或评估不合格）。
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

实现：[energy_qa_review.py](file:///d:/Programs/factor_system/fts/factor_engine/energy_qa_review.py)，周日 10:00 `l2_energy_qa_review_job`。冷却期 **30 交易日**：到期达标 → 回归 active；**两次不达标 → 退役**；未到期保持 shadow/degraded。判定原则**宁严勿松**（active/shadow/degraded 单维度命中取严）。

## 6. 7 状态机流转

实现：[status_board.py](file:///d:/Programs/factor_system/fts/factor_engine/qa/status_board.py)，CTA 手册 6.8。

### 6.1 状态定义与权重上限

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
| 因子巡检降级 | `factor_inspector_job`（每日 04:00） | 扫描 elite 退化因子（质量分/IC 衰减），检测退化 → `status='degraded'` + `is_elite=False` |
| 逻辑监控 | `logic_monitor_job`（每日 4:30） | 行为漂移 / 极端预测 / 换月日异常 |
| 月度复检 M1-M5 | `monthly_recheck` | 五指标（IC/IR/分层/秩偏离等），1·2·3 项预警降权 50/30/0，连续 3 月退役 |
| 季度复检 F1-F6 | `quarterly_recheck` | 全样本重算标记 |
| 半年度复检 D1-D4 | `semi_annual_recheck` | 逻辑复审 / 回测重跑 / 池重构 / 淘汰库复审 |
| 退役红线（5 条） | `check_retirement` | 独立触发（衰减超限 / IR 跌破 / 连续亏损等），NaN 保守不误判 |
| L3 纯外推衰减（P2） | `_validate_oos_extrapolation` | 晋升后每次 L3 检查新数据 IC，连续 3 次衰减 >20% 标记待降级 |

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
| 代码→文档映射 | 生命周期调度：`fts/scheduler/tasks.py` REGISTRY（cron）· `fts/scheduler/jobs.py`（l1_meta_loop_job / l2_seed_promotion_job / l2_evolution_*_job / l2_batch_mining_job / l2_review_job / l3_portfolio_loop_job）；晋升链：`fts/factor_engine/evolution_promote.py` `_promote_to_elite`；评审：`fts/factor_engine/factor_inspector.py` `FactorReviewWorkflow` / `AutoReviewPolicy`、`fts/monitor/reaudit.py`、`fts/monitor/elite_tracker.py`；冷却：`fts/factor_engine/evolution_seeds.py` `_within_degraded_cooldown`、`fts/factor_engine/portfolio_loop.py` `_is_shadow_pending`、`fts/factor_engine/energy_qa_review.py`；状态机：`fts/factor_engine/qa/status_board.py` |
| 可验证断言 | ① 晋升硬门：`_promote_to_elite` 中 high_ic_screen grade=C 或（B 且 skipped>8）时返回 None、`level_3_multiple.passed=False` 返回 None、`walk_forward.n_windows_completed<2` 返回 None、`audit_report.passed=False` 返回 None；② L3 仅 approved：`_filter_review_approved` 仅保留 `factor_reviews.decision='approved'`；③ 冷却：`_within_degraded_cooldown` 以 `(now-updated_at).days < 30` 判定；④ 状态机：`STATUS_TRANSITIONS` 合法流转 + `STATUS_MAX_WEIGHT` 权重上限 |
| 检验方式 | `pytest tests/factor_engine/test_portfolio_loop.py -k "ReviewApproved or LoadEliteDuckdb"`；`pytest tests/factor_engine/test_review_workflow.py`；`pytest tests/factor_engine/test_risk_tag.py`（冷却期）；`pytest tests/factor_engine/qa/ -v`（状态机/复检/退役）；`python scripts/verify_doc_consistency.py` |
