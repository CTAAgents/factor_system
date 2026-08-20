# 49 — 因子×子链质量矩阵：评审质检与生命周期张量化计划（QA/生命周期适配 47/48）

> 版本: v3.0.0+6
> 状态: ✅ 已完成（v2.104.0+112，A/B/C/D 四模块全部实施） · 优先级: P1 · 负责人: FTS Agent · 关联: plans/47（子链差异化权重，已完成）, plans/48（Regime 分层 Gate，已完成）, GAP-136（已关闭）, GAP-137（已登记并关闭）, qa/（评审质检）, factor_lifecycle / energy_qa_review / factor_inspector（生命周期）

## 一、背景与问题定位

### 1.1 现状：评审质检与生命周期是"全链"单值视角，与 47/48 的子链差异化语义不适配

plans/47 已建立"因子×子链"调制矩阵 **m[factor][子链]**（subchain_weight.py），plans/48 已建立子链方向 Gate **g[子链]**（regime_gate.py）。但**评审质检与生命周期管理仍以全链单值 IC/Sharpe 为口径**，二者与 47/48 割裂：

| 环节 | 位置 | 当前口径 | 与子链语义的冲突 |
|---|---|---|---|
| 机审准入 | `factor_inspector.py` `AutoReviewPolicy.classify`（L428-509） | 全链 ic/sharpe 单值 + 完整质检门禁 | 单链特异因子全链 IC 被稀释 < min_ic → 误杀 |
| 入库质检 Q1-Q10 | `qa/pre_entry.py` | Q4/Q5 全链 IC/IR；**Q10 要求"黑色/能化/农产品/有色 IC 方向一致"** | 与"子链特异因子是有价值的"（47 实证 10 个单链特异）直接冲突 |
| 季度复检 F1-F6 | `qa/quarterly_check.py` | **F6 板块方向一致性**（全链视角） | 同 Q10 |
| 月度 M1-M5 / 半年 D1-D4 / 退役红线 | `qa/` | 全链 IC/Sharpe/稳健 | 有效链失效被无效链稀释掩盖 |
| IC 衰减 | `factor_lifecycle.py` `factor_lifecycle_review` | 滚动窗口全链 IC vs OOS 基准（衰减>30% 触发） | 假阴性：有效链衰减被稀释；假阳性：单链失效导致整因子降级 |
| 统一管道退化 | `energy_qa_review.py` `EnergyQaReviewPipeline` | IC 降幅 0.30/0.50、Sharpe 相对变化 -0.20（全链） | 同 factor_lifecycle |
| Inspector 退化 | `factor_inspector.py` `inspect_and_downgrade` / `lineage.detect_degradation` | Sharpe 相对变化、评估趋势（全链） | 同 factor_lifecycle |

### 1.2 核心缺陷（四类）

1. **误杀**：单链特异因子（有效链 t 显著但全链 IC 稀释）被机审/评分卡/Q10 系统性惩罚或拒绝——47 号已证明这类因子是真实收益来源。
2. **掩蔽（双向）**：生命周期全链口径导致 ① 有效链失效不触发降级（假阴性，因子继续在失效链暴露）；② 单链失效触发整因子降级（假阳性，误杀仍健康的链）。
3. **画像静态化**：`subchain_scope`/`subchain_ic_profile` 是晋升时一次性落库（A2），生命周期检测到单链失效后**不能收缩 scope** → 47 号调制矩阵永远用旧画像，质量退化不传导到权重。
4. **无闭环**：评审/生命周期/调制（47）/Gate（48）互不联动——被 Gate 长期回避的子链上的因子、已失效子链上的暴露，无机制从 scope 剔除。

### 1.3 为什么"张量化"是唯一自洽的架构

47 调制矩阵 **m[factor][子链]**、48 Gate **g[子链]** 已构成二维网格，缺的正是第三块 **q[factor][子链]**（评审质检 + 生命周期质量）：

```
           子链 C1   C2   C3   C4
因子 f1    ✓有效  ✗    ✗    ✓有效   ← q[factor][子链]（本计划补齐：质量矩阵）
调制 f1    1.0   0    0    1.0      ← m[factor][子链]（plans/47，已有）
Gate       long avoid ...           ← g[子链]（plans/48，已有）
```

三者同网格后：Gate 决定"该子链现在能不能做"（方向层）、质量矩阵决定"该因子在该子链还值不值得做"（质量层）、调制决定"该因子在该子链做多少"（幅度层）——正交且自洽。子链是期货的自然评估单元（一品种一市场、同链相关、跨链独立），"因子×子链"质量评估在语义上比"因子×全局"更正确。

## 二、方案设计

### A 模块 — 评估单元抽象与质量矩阵存储（张量底座）

- **A1 评估单元**：`(factor_id, market, chain)`——子链定义 SSOT 在 `config/futures_universe.yaml`（47 已用 `ENERGY_CHAIN_SUB_SYMBOLS`），market 隔离 futures/energy 的子链集合。
- **A2 质量矩阵时序存储**：DuckDB L3 因子库新增表 `subchain_factor_quality`（一数一源，SSOT）：
  ```sql
  CREATE TABLE IF NOT EXISTS subchain_factor_quality (
    factor_id  VARCHAR, market VARCHAR, chain VARCHAR,
    evaluated_at TIMESTAMP, period_end DATE,
    n_symbols INT, mean_ic DOUBLE, std_ic DOUBLE,
    t_stat DOUBLE, p_value DOUBLE, effective BOOLEAN,
    source VARCHAR,        -- promotion | review | inspect | lifecycle
    decision VARCHAR,      -- keep | scope_shrink | degrade | retire（该单元判定）
    PRIMARY KEY (factor_id, market, chain, evaluated_at)
  );
  ```
  复用 `subchain_profile.ChainStat` 判定（min_symbols=3 / min_t_stat=2.0 / min_chain_ic=0.10），写入 E.4 短连接。
- **A3 重算路径**：评审/巡检作业内对因子重算 `symbol_ic`（逐品种 IC，复用 `_compute_current_ic` 的逐品种扩展）→ `build_subchain_metadata` 聚合 → 写 `subchain_factor_quality` 一行/单元/期。评估链晋升时（A2 落库点）同步写首行。

### B 模块 — 评审质检张量化（qa/）

- **B1 Q10 重构**（`qa/pre_entry.py`）：从"跨板块方向一致"改为两级判定——
  - 外层：跨产业链方向一致（防反向因子，保留：能化 vs 黑色 vs 有色 vs 农产品方向同向）
  - 内层：产业链内**子链特异可接受**——单链/部分链有效需通过 47 三门槛（t_stat≥2.0 且 |mean_ic|≥0.10 且 n_symbols≥3），反向子链标记 `avoid_chain`（该链禁用，不判 Q10 失败）
  - 输出：`q10_verdict`（consistent | subchain_specific | conflicted）
- **B2 机审单链特异放行**（`factor_inspector.py` `AutoReviewPolicy.classify`）：全链 IC < min_ic 但 `subchain_ic_profile` 存在 effective 子链（t 显著）→ 放行 APPROVED，scope=[effective 链]，reason 标注"单链特异放行"。QA 门禁其余项不变（audit/WF/多重/评分卡/Q1Q10）。
- **B3 准入分类子链化**（`qa/admission.py`）：三级准入按 scope 判定——全链有效 → 全链权重；单链特异 → 受限权重（上限下调，如 ≤10%）；部分链 → 按有效链集合。与 47 调制矩阵对齐。
- **B4 季度 F6 重构**（`qa/quarterly_check.py`）：同 B1 两级判定，F6 输出子链方向一致性变化 + 有效链集合漂移（对比入库基准），漂移 > 阈值 → 标记 scope 复核。

### C 模块 — 生命周期张量化

- **C1 单元粒度退化检测**：新增 `compute_subchain_degradation(factor_id, market)`——查询 `subchain_factor_quality` 时序，按 factor_id×chain 取最近 window 期 vs 早期基准：
  - 每有效链独立算衰减（复用 `_rolling_stats` 口径，衰减阈值参数化 0.30）
  - 判定：**全部有效链衰减** → degrade（整因子）；**部分有效链衰减** → scope_shrink（失效链从 scope 剔除，写 decision）；单链特异因子其唯一链衰减 → degrade。
- **C2 scope 动态更新闭环**：scope_shrink 后更新 `factor_catalog.metadata.subchain_scope`（剔除失效链）→ 47 号调制矩阵 m[factor][子链] 在 Step 2b 消费最新 metadata 自动重算；`subchain_specific` 同步刷新。恢复机制：degraded 因子冷却期（复用 energy_qa_review 冷却期 30 交易日）后重审达标 → 回 active 并重评子链画像。
- **C3 接入点**：`energy_qa_review.EnergyQaReviewPipeline`（[2] 退化检测段）改按 C1 判定；`factor_lifecycle_review` 增加子链 IC 序列输入重载（保留全链向后兼容）；`factor_inspector.inspect_and_downgrade` / `lineage.detect_degradation` 增加 market 子链路由（无子链画像的股票/期货全链回退原逻辑）。
- **C4 退役红线**：退役判定改为"全部有效单元失效"（而非全链 IC 恶化）——单链特异因子有效链失效即退役；全链因子保持原红线。

### D 模块 — 与 47/48 闭环 + 可扩展性

- **D1 闭环流程**：评审/巡检写 `subchain_factor_quality` → scope 动态更新 metadata → 47 调制矩阵重算 → 48 Gate 不变（方向层正交）。一次评审作业完成"质量矩阵刷新 + 权重传导"。
- **D2 可扩展**：评估单元 = (market, chain)，子链定义参数化。扩展到黑色/有色/农产品/金融产业链时只需在 `futures_universe.yaml` 加子链映射——评审/生命周期/调制/Gate 全部按单元粒度自动生效，无逻辑变更。
- **D3 监控**：质量报告新增 `subchain_quality_matrix` 段（各因子×子链最近期 effective 快照 + scope 变化历史），与 47 `subchain_exposure`、48 `subchain_gate_distribution` 三网合一。

## 三、数据模型与契约（变更点）

| 对象 | 变更 | 说明 |
|---|---|---|
| `factor_db/schema.py` | + `subchain_factor_quality` 表 | 质量矩阵时序，SSOT，E.4 短连接 |
| `subchain_profile.py` | 复用 `ChainStat`/`build_subchain_metadata`；新增 `build_subchain_quality_matrix`（多期聚合） | 张量计算底座 |
| `factor_db/repository.py` | + `save_subchain_quality` / `query_subchain_quality` | 存储读写（幂等 UPSERT） |
| `qa/pre_entry.py` | Q10 两级重构 | 子链特异可接受 + 反向子链 avoid 标记 |
| `factor_inspector.py` | `AutoReviewPolicy` + 单链特异放行分支 | 全链 IC 低但有效子链 t 显著 → 放行 |
| `qa/admission.py` | 准入分类按 scope | 全链/单链特异/部分链三级权重 |
| `qa/quarterly_check.py` | F6 两级重构 + 有效链集合漂移 | 对比入库基准 |
| `energy_qa_review.py` / `factor_lifecycle.py` / `factor_inspector.py` | 退化检测单元粒度（C1/C3） | scope 收缩 / 全部失效降级 / 退役红线 |
| `config/settings.yaml` | + `l3.subchain_quality.*`（衰减阈值/窗口/冷却期复用） | 参数化，禁硬编码 |

## 四、实施步骤（已完成 ✅）

1. ✅ A 模块：`subchain_factor_quality` 建表 + repository 读写 + 重算路径（评估晋升点 + 评审作业）
   - 验证：晋升因子写首行；评审作业重算聚合与 `build_subchain_metadata` 一致 ✅
2. ✅ B 模块：Q10/F6 重构 + 机审单链特异放行 + 准入分类子链化
   - 验证：单链特异因子不误杀；Q10 反向子链不判失败 ✅
3. ✅ C 模块：单元粒度退化检测 + scope 动态更新 + 退役红线
   - 验证：部分链失效 → scope_shrink；全部失效 → degrade；特异因子单链失效 → 退役 ✅
4. ✅ D 模块：闭环接线 + 监控段 + 可扩展（子链定义参数化复核）
   - 验证：scope 更新传导到 47 调制矩阵；质量矩阵入质量报告 ✅
5. ✅ 回归 + 文档同步（GAP-137 登记关闭 / 01-04/06-09 / 版本 bump v2.104.0+112）

## 五、测试方案（实测 ✅）

- `tests/factor_engine/test_subchain_quality_store.py`（**13 用例**）：建表/幂等 UPSERT/时序查询（按 factor×chain）/空库 + build_subchain_quality_rows（每链行/t=inf→None/空 IC/时间戳默认）
- `tests/factor_engine/test_qa_subchain.py`（**19 用例**）：Q10 两级判定（全链一致/子链特异/反向子链 avoid/跨链冲突）/机审单链特异放行（放行/Sharpe 不放行/QA 门禁仍执行/无 profile 兼容）/准入三级分类（受限权重）/F6 两级判定（含有效链漂移）
- `tests/factor_engine/test_lifecycle_subchain.py`（**14 用例**）：部分链失效 scope_shrink/全部失效 degrade/特异因子单链失效 retire/全链因子回退原逻辑/scope 更新传导调制矩阵/配置加载
- 回归：`pytest tests/factor_engine/test_qa*.py tests/factor_engine/test_energy_qa_review.py tests/factor_engine/test_factor_lifecycle.py tests/factor_engine/test_factor_inspector.py tests/factor_engine/test_subchain_weight.py -m "not slow"` —— **448 全绿**（新增 46 + qa 104 + portfolio 299 + evolution 45）+ ruff 全绿
- 分级测试政策：不跑全量 ✅

## 六、验证标准（验收 ✅）

1. 单链特异因子（油化工 t 显著、全链 IC<0.02）机审放行且 scope=[油化工]，不再误杀。✅（test_qa_subchain 机审放行用例 + `AutoReviewPolicy.classify(subchain_profile=...)`）
2. 有效链 IC 衰减 40%、无效链平稳：因子 scope_shrink（剔除失效链）而非整因子降级；全部有效链衰减 → degrade。✅（test_lifecycle_subchain 全分支用例）
3. scope 更新后 47 调制矩阵重算生效（Step 2b 消费最新 metadata），与 48 Gate 无冲突。✅（TestModulationClosedLoop 集成用例）
4. 新增测试全绿 + 受影响回归无破坏 + ruff 通过；GAP-137 登记并关闭。✅（GAP-137 v2.104.0+112 关闭）

## 七、风险与回退

- **风险 1（子链 IC 时序样本不足）**：每期评审才写一行，退化检测早期期数不足 → 复用 `_rolling_stats` 的 `len < max(5, window//10)` 保护，样本不足返回 None（审计 skipped，不误判）。
- **风险 2（scope 收缩过激）**：单期噪声致有效链误标失效 → 需要连续 N 期（冷却期，复用 30 交易日）确认才收缩；scope 回滚需重审达标。
- **风险 3（存储膨胀）**：因子×子链×期次行数增长 → 按 factor_id+chain 分区 + 保留窗口裁剪（仅保留最近 N 期 + 早期基准快照）。
- **回退路径**：`l3.subchain_quality.enabled=false` 时评审/生命周期回退全链原逻辑（向后兼容，股票/无画像因子天然走回退）。

## 八、一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| §A2 质量表 | `subchain_factor_quality` 建表 + UPSERT 幂等 | `grep -n "subchain_factor_quality" factor_db/schema.py` + 单测 |
| §B1 Q10 重构 | 单链特异不判失败；反向子链标记 avoid_chain | 单测断言 verdict |
| §B2 单链特异放行 | 全链 IC<min 但有效子链 t 显著 → APPROVED | 单测断言 |
| §C1 单元退化 | 部分链失效 scope_shrink；全部失效 degrade | 单测断言 |
| §C2 scope 闭环 | metadata.subchain_scope 更新 → Step 2b 调制矩阵重算 | 单测断言 + 集成 |
| §D1 可扩展 | 子链定义来自 futures_universe.yaml（参数化） | grep + 配置断言 |

## 九、实施后文档同步清单（Harness 13 项，已完成 ✅）

1. ✅ `docs/harness/01-architecture.md`（质量矩阵数据流 + 三层正交图）
2. ✅ `docs/harness/02-lifecycle.md`（Phase 49 产出物）
3. ✅ `docs/harness/03-configuration.md`（`l3.subchain_quality.*` 配置项）
4. ✅ `docs/harness/04-resilience.md`（样本不足/冷却期/回退路径）
5. ✅ `docs/harness/05-observability.md`（`subchain_quality_matrix` 段）
6. ✅ `docs/harness/06-testing.md`（新测试文件/用例数）
7. ✅ `docs/harness/07-operations.md`（版本历史）
8. ✅ `docs/harness/08-gap-analysis.md`（登记并关闭 GAP-137：评审质检/生命周期未子链化）
9. ✅ `docs/harness/09-advancement-plan.md`（晋级里程碑）
10. ✅ `pyproject.toml`（版本 bump v2.104.0+112）
11. ✅ `README.md`（工程指标）
