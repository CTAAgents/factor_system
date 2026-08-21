# 因子全生命周期管理（FTS）

> 版本: v3.1.0+7
> 最后更新: 2026-08-20
> 适用范围: 期货主链路（futures / energy 候选池，股票链路已剥离至 fts-stock）

---

## 1. 目标与核心原则

因子管理覆盖 **L0 供给 → L1 注入 → L2 演化晋升/评审 → L3 信号矩阵 → 持续监控与退役** 的完整闭环。核心原则：

- **不质检不入库 / 不复检不续役 / 不审批不准入**（CTA 手册 6.1）；
- 因子库 **DuckDB 为 SSOT**（`data/factor_catalog_{futures,energy}.duckdb`），评审/评分卡/审计/状态历史均为独立旁路表，禁止 JSON 绕过落库；
- 晋升、评审、冷却、退役为**四条独立流程**，互不替代；
- 零未来函数 / 零数据窥探 / 多重检验校正贯穿全链（AGENTS.md 红线）。

## 2. 生命周期总览

主链路：**L0/L1 供给 → L2 演化晋升 → L2 评审质检（每日 04:00 统一任务，周日重量级 l2_review_job 全量重审 + 衰减淘汰 + 阀门收口，其余日轻量阀门 + 巡检 + 监控）→ approved → 信号矩阵输出 → Regime-Driven 策略合成**（v3.0.0 组合侧退役）。底部持续监控与生命周期闭环（机审阀门 / 巡检降级 / 逻辑监控 / M·F·D 复检 / 退役红线 / 7 状态机 / 冷却期）将降级与退役反馈回评审，冷却期满可回归。

| 阶段 | 调度（TRAE Schedule） | 产出 |
|:-----|:-----|:-----|
| L0/L1 供给（`l1_meta_loop_job`） | 每日 04:00 | 知识补给 + 种子注入 → 候选池 |
| L2 种子评估+演化（`l2_seed_promotion_job`/`l2_evolution_weekday\|weekend_job`） | 每日 | 种子评估晋升 + 演化主循环（工作日 ≈10 代 / 周末 ≈50 代） |
| L2 评审质检统一任务 | 每日 04:00（周日重量级分支） | reaudit 重审 + 衰减淘汰 + approved 落库 / 巡检降级 / 行为漂移 / 数据质量 |
| 信号矩阵输出 | 随 L3 信号矩阵任务 | 因子信号契约 v1 → Regime-Driven |

> **调度源（v2.104.0+99）**：内部 `fts/scheduler` 定时任务默认停用（`FTS_INTERNAL_SCHEDULER_ENABLED` 默认 "0"），周期任务由 **TRAE Schedule 定时自动化**执行。energy 因子库 `factor_catalog_energy.duckdb` 作为**候选池**保留（v3.0.0+1 起 energy 专属任务删除，`l2_energy_qa_review_job` 仅供手动调用）。

### 术语统一：状态命名与对象集合

- **`active elite`**：`factor_catalog.is_elite=1 AND status='active'`（服役中精英因子全集）。
- **`L3 approved`**：`factor_reviews.decision='approved'`（评审质检通过，L3 信号矩阵唯一消费对象）。**包含关系：`L3 approved ⊆ active elite`**。
- **唯一状态字典（7 状态）**：`DRAFT` / `PENDING_QA` / `CORE`（≤30% 权重）/ `CANDIDATE`（≤15%）/ `OBSERVATION`（≤50% 原权重）/ `SUSPENDED`（0%）/ `RETIRED`（0%）。`normalize_status()` 经 `STATUS_ALIAS_MAP` 将历史命名（active/degraded/shadow/retain/retire 等）统一归一（SSOT：`fts/factor_engine/qa/status_board.py`）。

## 3. 晋升门槛完整校验链（L2 → elite）

晋升入口统一收敛于 `_promote_to_elite`。评估阶段产出 `evaluation` 后，晋升前按序通过以下**强制门**：

| 序 | 校验门 | 判定规则 | 不通过后果 |
|:--:|:-------|:---------|:----------|
| 0 | **Verifier**（评估阶段） | IC ≥ 0.03 且 Sharpe ≥ 1.5 | 评估不达标，不进入晋升 |
| 1 | **L2 准入去冗余**（GAP-I206） | 与存量 elite 高相关（≥0.8）且命中 shadow 判定 | 高相关拦截，不落库 |
| 2 | **B.4 高IC筛查** | 16 项检查 × 6 大模块，A≥85 / B 60~84 / C<60；5 项一票否决（V1-V5） | C 级直接拦截；B 级且跳过项 >8 同拦截 |
| 3 | **多重检验** | Bonferroni 校正 passed | 未通过 → 拒绝晋升 |
| 4 | **WalkForward 多窗口 OOS** | `n_windows_completed ≥ 2` | 单窗口/缺失 → 数据窥探嫌疑，禁止晋升 |
| 5 | **6 项审计硬门** | 因果 / OOS / 跨品种 / 压力 / 多重检验 / 数据窥探，非 skipped 项全部通过 | 任一关键项 failed → 禁止晋升 |
| 6 | **质量评分卡**（记录分级，非硬门） | A≥40 / B≥30 / C 淘汰 | 仅记录分级，不单独拦截 |
| 7 | **影子池标记** | 默认 5 交易日观察期（`FTS_EVOLUTION_SHADOW_OBSERVE` 可关闭） | 观察期内 L3 不纳入 |

**晋升落库**：DuckDB 主存储（GAP-032 严格一致——DuckDB 成功后才写 JSON 快照），同步写 `factor_quality_scores` / `factor_audit_reports` / `status_history`。

## 4. 评审质检（L2→L3 阀门模块）

`FactorReviewWorkflow`（`factor_inspector.py`）是独立于 L2 的 **L2→L3 阀门**：approved → 流向 L3；rejected / 质检记录缺失 → 退回 L2 冷却池。

**完整质检门禁（`AutoReviewPolicy.classify`，v2.104.0+89）**：复核 6 项审计 / 质量评分卡 / 高IC筛查 / 多重检验 / WalkForward / Q1-Q10 入库质检 / IC·Sharpe，任一关键项缺失 → 转人审（宁缺毋滥），未通过 → rejected。

**功能 1 就地审核**：新晋升 elite 因子即时 `review_inplace`（approved / rejected / 质检缺失撤销 approved 退回待审）。

**功能 2 周度 L3 池巡检**：`review_l3_pool` 对 approved 因子按最新 IC/Sharpe + 完整质检 + **相对退化检测**（Sharpe 相对趋势下降 ≥ threshold 默认 -0.2）重新复核，命中即撤销 approved 退回 L2 冷却池（组合防抖：每日巡检已对 approved 因子豁免直接降级，此为唯一收口出口）。

**周日重量级评审（`l2_review_job`）**：Step A reaudit 新标准重审（retain/shadow/retire）→ Step B 衰减评估 + AutoRetire → Step C 阀门周度收口。

## 5. 冷却规则（三处独立判定）

| 机制 | 规则 |
|:-----|:-----|
| 影子池观察期（晋升后） | 新演化因子 5 交易日观察（`shadow_pool.observe_until`）；种子因子直进正式组合 |
| degraded 30 日冷却期（降级后） | 冷却期内种子/演化跳过该因子；期满放行重评估，合格晋升**复用原 factor_id**（保留血缘） |
| energy 冷却期回归 | 30 **交易日**；到期达标回归 active，两次不达标退役（宁严勿松） |

## 6. 7 状态机流转

```
DRAFT → PENDING_QA → CORE ⇄ CANDIDATE
                       │      │
                 OBSERVATION ⇄ SUSPENDED
                       │
                    RETIRED →（复审重新有效）→ PENDING_QA
```

合法流转（SSOT `STATUS_TRANSITIONS`）：PENDING_QA→{CORE,CANDIDATE,RETIRED}；CORE/CANDIDATE→{CANDIDATE/CORE,OBSERVATION,RETIRED}；OBSERVATION→{CORE,CANDIDATE,SUSPENDED,RETIRED}；SUSPENDED→{OBSERVATION,RETIRED}；RETIRED→{PENDING_QA}。状态权重上限见 §2（CORE≤30% / CANDIDATE≤15% / OBSERVATION≤50% / 其余 0%）。存量 `status='active'` 兼容归一 CORE。

## 7. 持续监控与退役

| 机制 | 调度 | 规则 |
|:-----|:-----|:-----|
| 因子巡检降级 | 每日 04:00 | 扫描 elite 退化（质量分/IC 衰减）→ degraded + 冷却 30 日；**approved 因子豁免每日降级**（仅标记待周度评审收口） |
| 逻辑监控 | 每日 04:00 | 行为漂移 / 极端预测 / 换月日异常 |
| 月度复检 M1-M5 | 月度 | 五指标预警，1·2·3 项降权 50/30/0，连续 3 月退役 |
| 季度/半年度复检 | 季度/半年度 | F1-F6 全样本重算 / D1-D4 逻辑复审·池重构 |
| 退役红线（5 条） | — | 独立触发，NaN 保守不误判 |
| 单元粒度退化（子链，灰度） | 评审任务 | (factor_id, chain) 粒度：全链衰减→degrade / 部分链→scope_shrink / 冷却期 30 日防过激 |
| L3 组合侧退役登记（plans/57，v3.0.0） | `retired_l3.py` + `scan_l3_retirement.py` | 35 项 L3 组合侧函数登记弃用（存量兼容不删码，物理删除为后续里程碑）；**因子生命周期本身不受影响** |

## 8. 与存储的映射

| 数据 | 表/文件 |
|:-----|:--------|
| 因子主表 | `factor_catalog`（DuckDB SSOT） |
| 评审 | `factor_reviews`（factor_id, decision, comment, reviewer, reviewed_at） |
| 评分卡 / 审计 / 状态历史 / 溯源 | `factor_quality_scores` / `factor_audit_reports` / `factor_status_history` / `seed_lineage` |
| 旧格式 JSON | 仅只读快照/降级回退 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:---|
| 代码→文档映射 | 晋升链：`fts/factor_engine/evolution_promote.py` `_promote_to_elite`；评审：`fts/factor_engine/factor_inspector.py` `FactorReviewWorkflow`/`AutoReviewPolicy`/`review_l3_pool` + `fts/scheduler/jobs.py` `_review_gate_weekly` + `fts/monitor/reaudit.py`；冷却：`evolution_seeds.py _within_degraded_cooldown` / `portfolio_loop.py _is_shadow_pending` / `energy_qa_review.py`；状态机：`qa/status_board.py`（7 唯一名 + 别名归一） |
| 可验证断言 | 晋升硬门：grade=C 或 multiple 未通过或 WF<2 窗口或 audit 失败 → 拒绝；L3 仅 approved：`_filter_review_approved`；冷却：`(now-updated_at).days < 30`；组合防抖：`inspect_and_downgrade` 对 approved 不降级 |
| 检验方式 | `pytest tests/factor_engine/test_qa_gate.py tests/factor_engine/test_review_workflow.py tests/factor_engine/qa/ -q`；`python scripts/verify_doc_consistency.py` |
