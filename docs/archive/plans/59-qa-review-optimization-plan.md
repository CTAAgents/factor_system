# 59 — 因子评审质检体系优化计划（QA Review Optimization）

> 版本: v3.1.0+3 · 状态: 📝 计划中（GAP-161~168 已登记）
> 日期: 2026-08-20 · 优先级: P1
> 关联: [factor_lifecycle.md](../factor_lifecycle.md) · [08-gap-analysis.md](../08-gap-analysis.md)（GAP-161~168）· `fts/factor_engine/qa/` · `factor_inspector.py` · `high_ic_screener.py` · `factor_quality_card.py` · `ir_thresholds.py` · `scope_domain/` · `monitor/data_quality_monitor.py`

---

## 1. 背景与体系评审结论

对 FTS 因子评审质检体系（factor_lifecycle.md §3-§7 全链）的审查结论：

**强项（保持不动）**：
- 四象限覆盖完备：预测力（IC/IR/分层）、稳健性（WF/置换/多重检验/窥探）、落地性（成本/容量/换手）、可解释性（经济逻辑/文档化）；
- 纵深防御：晋升 7 道硬门 → approved 阀门 → 每日巡检 → 月/季/半年复检 → 退役红线；
- 生命周期闭环：7 状态机 + 冷却期 + 淘汰库复审回归；
- 特异因子治理：子链/品种级护栏 + 域内口径 + 差异化权重（plans/47-49）。

**薄弱点（本次优化对象）**：

| # | 薄弱点 | 对应 OPT |
|:--:|:-------|:---------|
| 1 | 阈值静态化：IC/Sharpe/IR 门槛全局单值，未按市场 regime 分层；F5 只"标记"不"调门槛" | OPT-01 |
| 2 | 多重检验只控单次批次：跨日跨运行累积家族错误率无控制（无 discovery discount） | OPT-02 |
| 3 | 特异因子统计功效天花板：单链 n≥3 / 单品种 500 交易日硬门槛，无晋升后 OOS 前瞻二次确认 | OPT-03 |
| 4 | IC 类判定多处重复（Verifier/B.4/评分卡/Q4），无多口径差异告警 | OPT-04 |
| 5 | 数据质量监控与因子评审割裂：数据异常期做出的评审结论无"数据存疑"标记 | OPT-05 |
| 6 | 人审（needs_human / D1）无超时 SLA，pending 可长期堆积 | OPT-06 |
| 7 | 参数稳健性：Q3/F3 仅离散档位 + 季度查偏移，无参数平面鲁棒区检测 | OPT-07 |
| 8 | 成本/容量静态：期货合约月份/换月窗口流动性变化失真 | OPT-08 |

## 2. 优化项设计（OPT-01 ~ OPT-08）

### OPT-01 准入门槛 Regime 条件化（Phase 1）

- **问题**：`AutoReviewPolicy`（min_ic/min_sharpe）、`ir_thresholds.py`（量价 0.3/基本面 0.4/期限结构 0.35）、`monthly_check.py` M2/M3 阈值均为全局静态单值；震荡市与趋势市同一标准，regime 敏感因子判定失真。
- **方案**：
  1. 新增 `fts/factor_engine/regime_thresholds.py`：`(regime, factor_class) → {min_ic, min_sharpe, min_ir, decay_warn}` 查表（Pydantic 配置模型），复用已有 `regime_features` / `regime_gate` 输出；
  2. `AutoReviewPolicy.classify`、`ir_thresholds.py`、`qa/monthly_check.py` 三处门槛改为从查表取值（无 regime 上下文时回退现有静态值，向后兼容）；
  3. `quarterly_check.py` F5 从"条件 IC 变化 >50% 标记"升级为"触发门槛调整告警 + 写调整日志"。
- **涉及**：`fts/factor_engine/factor_inspector.py` · `fts/factor_engine/ir_thresholds.py` · `fts/factor_engine/qa/monthly_check.py` · `fts/factor_engine/qa/quarterly_check.py` · `fts/factor_engine/regime_features.py` / `regime_gate.py` · `fts/config/settings.py`
- **验收**：单测覆盖 3 regime × 3 因子类别门槛取值；无 regime 数据时回退静态值不误判；`pytest tests/factor_engine/test_qa_gate.py tests/factor_engine/test_factor_inspector.py -q` 回归全绿。

### OPT-02 跨运行累积 FDR 折扣（discovery discount）（Phase 1）

- **问题**：Bonferroni 只控单次评估批次；因子跨日多次重试后"最终通过"与"首次通过"假阳性风险不等价。
- **方案**：
  1. 因子重试次数来源：`factor_status_history`（factor_id 历史评估次数）或 `factor_catalog.metadata` 累计；
  2. 晋升链多重检验判定改为 `p_eff = p × discount^retries`（折扣系数进配置，默认 1.25）；
  3. `level_3_multiple` 判定用 `p_eff`，命中不通过时 reason 标注"重试折扣后不显著"。
- **涉及**：`fts/factor_engine/evolution_promote.py` · `fts/factor_engine/factor_db/repository.py` · `fts/factor_engine/verifier.py`
- **验收**：单测验证重试 1/3/5 次后门槛收紧曲线；首次通过因子不受影响；`tests/factor_engine/test_evolution_promote_gap128.py` 回归全绿。

### OPT-03 特异因子 OOS 前瞻二期确认（Phase 2）

- **问题**：子链/品种特异"一次判定终身画像"；护栏只能"宁漏标不误标"，无晋升后真实样本外二次确认；单链 n≥3、单品种 500 交易日硬门槛牺牲真实次新/双品种特异。
- **方案**：
  1. 特异画像（`subchain_specific` / `symbol_specific`）设"特异观察期"（复用 shadow 池机制，默认 20 交易日）；
  2. 观察期内真实 OOS IC 复核：仍显著 → 固化画像；衰减 → 撤销特异画像回退全链口径；
  3. 对 n_symbols<3 候选引入贝叶斯收缩评分（向全链均值收缩），不直接硬拒。
- **涉及**：`fts/factor_engine/scope_domain/guard.py` · `scope_domain/hooks.py` · `fts/factor_engine/subchain_profile.py` · `fts/factor_engine/factor_inspector.py`
- **验收**：`tests/factor_engine/test_qa_subchain.py` 增补"观察期后固化/撤销"用例；`test_lifecycle_subchain.py` 回归全绿。

### OPT-04 质检口径收敛 + 多口径差异告警（Phase 2）

- **问题**：IC 类信号在 Verifier / B.4 `ic_mean` / 评分卡 `ic_score` / Q4 四处重复判定，一处口径漂移全链级联，且无差异可见性。
- **方案**：
  1. 定义**评分卡为 IC 类判定唯一权威口径**，Verifier/B.4/Q4 改为引用其结果（保留各自一票否决语义）；
  2. 评审作业新增"口径一致性校验"：四处 IC 值偏差 > 容差（0.005）→ 输出告警项 + 转人审提示（仿 `verify_doc_consistency` 模式）。
- **涉及**：`fts/factor_engine/factor_quality_card.py` · `evolution_promote.py` · `high_ic_screener.py` · `qa/pre_entry.py` · `factor_inspector.py`
- **验收**：构造"评分卡与 B.4 口径不一致"用例断言告警触发；晋升/评审既有用例全绿。

### OPT-05 数据质量-评审联动（Phase 2）

- **问题**：`data_quality_monitor` 与因子评审割裂，数据异常期做出的 approved 会污染评审历史。
- **方案**：
  1. 评审作业启动时注入当期 `data_quality` 快照（缺失率/异常值/多源分歧分）；
  2. 数据质量分 < 阈值 → 本次评审标记 `data_degraded`，不写 approved（延迟下期判定），`factor_reviews.comment` 记录原因。
- **涉及**：`fts/monitor/data_quality_monitor.py` · `fts/factor_engine/factor_inspector.py` · `fts/factor_engine/qa/report_template.py`
- **验收**：注入坏数据快照用例断言"数据存疑不落 approved"；正常数据回归不受影响。

### OPT-06 人审 SLA 自动降级（Phase 2）

- **问题**：`needs_human` / D1 逻辑复审依赖人工且无超时路径，pending 可长期堆积。
- **方案**：pending/needs_human 超 N 交易日（默认 5）未处理 → 自动降权 50%；超 2N → 退回 L2 冷却池（`evolution_seeds` 冷却通道），全部留痕 `factor_reviews`。
- **涉及**：`fts/factor_engine/factor_inspector.py` · `qa/status_board.py` · `fts/scheduler/jobs.py`
- **验收**：时钟注入用例验证超时升级路径；冷却期后回归正常。

### OPT-07 参数稳健区动态化（Phase 3）

- **问题**：Q3/F3 只要求 ≥3 组离散档位 + 季度查偏移，参数平面"连续可行域"未检测。
- **方案**：晋升与季度复检改为参数平面鲁棒区检测（邻域内绩效衰减 < 阈值占比），F3 纳入月度复检。
- **涉及**：`fts/factor_engine/qa/pre_entry.py` · `qa/quarterly_check.py` · `qa/monthly_check.py`
- **验收**：构造"窄峰参数"因子断言鲁棒区占比低被标记；档位兼容旧数据。

### OPT-08 成本/容量动态化（Phase 3）

- **问题**：`capacity_score`/`tradability_score` 静态，期货合约月份/换月窗口流动性变化失真。
- **方案**：`capacity_score`/`tradability_score` 接入实时流动性快照（合约月份、换月窗口，复用 `scripts/liquidity_snapshot.py`），移仓期自动下调容量分。
- **涉及**：`fts/factor_engine/factor_quality_card.py` · `fts/config/factor_quality_card_config.py` · `scripts/liquidity_snapshot.py`
- **验收**：移仓窗口用例断言容量分下调；正常期取值稳定。

## 3. 执行批次与依赖

| 批次 | OPT | 依赖 | 说明 |
|:-----|:----|:-----|:-----|
| Phase 1 | OPT-01 → OPT-02 | OPT-01 独立；OPT-02 复用 OPT-01 的配置框架（可选） | 统计严谨性红线最薄弱处，优先 |
| Phase 2 | OPT-03 → OPT-04/05/06 | OPT-03 复用 shadow 池；OPT-04/05/06 相互独立 | 评审结论可信度 + 运维收敛 |
| Phase 3 | OPT-07 → OPT-08 | 相互独立 | 精细度提升 |

> 批次内可并行（互不共享状态）；批次间严格顺序推进（每个 Phase 完成后定向回归 + bump）。

## 4. 落地约束（CLAUDE.md 13 项检查清单）

每个 OPT 落地前强制：
1. 登记/更新 GAP（08-gap-analysis.md）；
2. 文档先行：`01-architecture.md`（如涉数据流/接口）、`05-observability.md`（如涉新指标/日志）、`06-testing.md`（用例数）、`07-operations.md`（版本历史）；
3. 契约优先：先 TypedDict/配置模型，再实现；
4. 测试随重构：先写测试再实现，测试全绿进入下一步；
5. `python scripts/bump_version.py --build --message "..."` 版本追加；
6. `python scripts/verify_doc_consistency.py` 文档一致性校验。

## 5. GAP 登记映射

| OPT | GAP | 优先级 | 模块 |
|:----|:----|:-------|:-----|
| OPT-01 | GAP-161 | P1 | regime_thresholds / factor_inspector / ir_thresholds / monthly_check / quarterly_check |
| OPT-02 | GAP-162 | P1 | evolution_promote / verifier / factor_db |
| OPT-03 | GAP-163 | P1 | scope_domain / subchain_profile / factor_inspector |
| OPT-04 | GAP-164 | P1 | factor_quality_card / high_ic_screener / pre_entry / factor_inspector |
| OPT-05 | GAP-165 | P1 | data_quality_monitor / factor_inspector |
| OPT-06 | GAP-166 | P1 | factor_inspector / status_board / scheduler |
| OPT-07 | GAP-167 | P2 | qa/pre_entry / quarterly_check / monthly_check |
| OPT-08 | GAP-168 | P2 | factor_quality_card / config |

## 6. 验证

```bash
# 定向回归（每 Phase 完成后）
pytest tests/factor_engine/test_qa_gate.py tests/factor_engine/test_factor_inspector.py -q
pytest tests/factor_engine/qa/ -q
pytest tests/factor_engine/test_qa_subchain.py tests/factor_engine/test_lifecycle_subchain.py -q
pytest tests/factor_engine/test_evolution_promote_gap128.py -q

# 静态与一致性
ruff check fts/ tests/
python scripts/verify_doc_consistency.py
```

## 7. 后续建议（超出本计划）

- 幸存者偏差自动化：D4 淘汰库复审从半年度人工项升级为自动定期扫描（复用评审链）——已覆盖于 OPT-03 观察期机制的扩展位；
- 评审输出标准化：各 OPT 落地后统一评审报告模板字段（`qa/report_template.py`），保证可机器比对。

---

## 一致性元数据

| 字段 | 值 |
|:-----|:---|
| 代码→文档映射 | OPT-01：`fts/factor_engine/regime_thresholds.py`（新增）· `factor_inspector.py` `AutoReviewPolicy.classify` · `ir_thresholds.py` · `qa/monthly_check.py` · `qa/quarterly_check.py`；OPT-02：`evolution_promote.py` `_promote_to_elite` · `verifier.py` · `factor_db/repository.py`；OPT-03：`scope_domain/guard.py` · `subchain_profile.py` · `factor_inspector.py`；OPT-04：`factor_quality_card.py` · `high_ic_screener.py` · `qa/pre_entry.py`；OPT-05：`monitor/data_quality_monitor.py` · `factor_inspector.py`；OPT-06：`factor_inspector.py` · `qa/status_board.py` · `scheduler/jobs.py`；OPT-07：`qa/pre_entry.py` · `qa/quarterly_check.py` · `qa/monthly_check.py`；OPT-08：`factor_quality_card.py` · `config/factor_quality_card_config.py` |
| 可验证断言 | ① OPT-01：无 regime 上下文时回退静态值（向后兼容）；三门槛查表函数 `threshold_for(regime, factor_class)` 单测全覆盖；② OPT-02：`p_eff` 折扣随 retries 单调收紧；③ OPT-03：观察期后固化/撤销画像双路径；④ OPT-04：四处 IC 偏差 > 0.005 触发告警；⑤ OPT-05：数据质量分 < 阈值不写 approved；⑥ OPT-06：超 N/2N 交易日自动降权/退冷却池；⑦ OPT-07：窄峰参数鲁棒区占比低被标记；⑧ OPT-08：移仓窗口容量分下调 |
| 检验方式 | `pytest tests/factor_engine/test_qa_gate.py tests/factor_engine/test_factor_inspector.py tests/factor_engine/qa/ -q`；`pytest tests/factor_engine/test_qa_subchain.py tests/factor_engine/test_lifecycle_subchain.py -q`；`pytest tests/factor_engine/test_evolution_promote_gap128.py -q`；`ruff check fts/ tests/`；`python scripts/verify_doc_consistency.py` |
