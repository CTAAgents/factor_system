# 子链张量化影响范围评估报告（plans/47 + 48 + 49）


> 版本: v3.0.0+6

> 生成日期: 2026-08-17
> 对比基线: 旧版 `e49c84f`（v2.104.0+108，子链张量化前） ↔ 新版 `a4b8ef4`（v2.104.0+110，plans/47+48+49 全部实现）
> 方法: `git diff e49c84f a4b8ef4`
> 说明: plans/51/52（L3 信号矩阵服务、增量窗口）在 a4b8ef4 之后提交，与子链张量化正交，不纳入本报告。
> 后续执行: 2026-08-17 按本报告评估结果完成开关启用（v2.105.0+1）——回填 196 因子画像 + 784 行质量矩阵，三组开关全开（zero+hard），L3 实测生效，详见 07-operations.md。

## 一、变更总览

| 类别 | 数量 | 明细 |
|---|---|---|
| 新增模块 | 4 | `subchain_profile.py`(292) / `subchain_weight.py`(193) / `regime_gate.py`(267) / `subchain_lifecycle.py`(250) |
| 存量改造 | 12 | portfolio_loop / qa×3 / factor_inspector / energy_qa_review / factor_lifecycle / evolution_promote / evaluation_chain / factor_db×2 / contracts / futures_signal_pipeline |
| 配置 | 1 | `settings.yaml` + 4 组配置（l3.subchain_weight / l3.regime_gating / l3.subchain_quality / subchain_profile） |
| 数据模型 | 2 | 新表 `subchain_factor_quality`；`factor_catalog.metadata` + `subchain_scope/ic_profile/specific` |
| 测试 | 8 | 新增 6 个测试文件 + 修改 2 个（test_portfolio_loop / test_portfolio_loop_adaptive） |

核心原则：**三组功能开关默认全关**（`enabled: false`），关闭时行为与旧版逐位一致——存量路径不改动，仅新增开关与分支。

## 二、portfolio_loop.py（重点，+366 行）

### 2.1 旧版（e49c84f）现状

- `PortfolioLoop.__init__` 无任何子链相关参数
- `regime_adaptive_weight_adjustment` 仅消费全局 regime 倍率表 `REGIME_STYLE_MULTIPLIERS`，无子链路由
- `load_elite_factors` 不透传子链画像字段
- `inject_to_fdt` 输出的 `factor_weights.json` 仅含基础权重
- 质量报告无子链相关监控段

### 2.2 新版（a4b8ef4）改动清单

| 位置 | 改动 | 灰度 |
|---|---|---|
| `regime_adaptive_weight_adjustment` | 新增 `subchain_regimes` 参数；单链 scope 因子路由到其子链 regime 倍率表（§C），all/unknown/部分链回退全局 | 默认关 |
| `PortfolioLoop.__init__` | + `enable_subchain_weight` / `subchain_weight_config`；状态 `_subchain_modulation` / `_subchain_symbol_chain` / `_subchain_gate_scale` | 默认关 |
| `load_elite_factors` | 透传 `subchain_scope` / `subchain_ic_profile`（47 §A2 落库字段） | 恒生效（只读透传） |
| Step 2b（新增） | 子链差异化权重调制：`build_subchain_weights` → 逐信号标注 `subchain_weights` → `compute_chain_exposure` 暴露监控 + 超阈值告警 | energy 且开关开启 |
| Step 2.5（扩展） | 子链 regime 检测 → `build_subchain_gates` 构建 Gate 分布 → plans/50 `_merge_gate_scale_into_modulation` 将 Gate 缩放并入调制矩阵 | energy 且 regime_gating 开启 |
| `inject_to_fdt` | 新增 `subchain_weights` / `symbol_chain` 参数，写入 `factor_weights.json` 供信号管线消费 | energy 且调制开启 |
| 质量报告 | 新增 4 段监控：`subchain_exposure`（47 §D2）/ `subchain_gate_distribution`（48 §D3）/ `subchain_gate_scale`（50 §B3）/ `subchain_quality_matrix`（49 §D3） | 各段随开关有值 |

### 2.3 新增私有 helper

- `_compute_subchain_exposure` — 子链权重暴露占比（复用 subchain_weight.compute_chain_exposure）
- `_merge_gate_scale_into_modulation` — Gate 缩放并入调制矩阵（plans/50 §B1）
- `_build_quality_matrix_snapshot` — 质量矩阵快照（plans/49 §D3，懒加载防循环依赖）

### 2.4 行为兼容性

- `enable_subchain_weight=false` → Step 2b 完全跳过，输出与旧版一致
- `regime_adaptive_weight_adjustment(subchain_regimes=None)` → 逐位回退全局逻辑（有回归保护测试）
- 子链调制/画像计算异常 → 捕获告警不阻断主流程（非致命跳过）

## 三、qa 模块（重点，三文件 +71/+21/+29 行）

### 3.1 qa/pre_entry.py — Q10 两级重构

| 维度 | 旧版 | 新版 |
|---|---|---|
| Q10 criterion | "分板块（黑色/能化/农产品/有色）IC 方向一致" | "跨产业链方向一致（外层）+ 产业链内子链特异可接受（内层）" |
| 判定函数 | 无 | 新增 `judge_q10_subchain(symbol_ic, chain_symbols, cfg)` |
| 输出 | 布尔 | `{verdict: consistent\|subchain_specific\|conflicted, effective_chains, avoid_chains, passed, detail}` |
| 反向子链 | 整项判失败 | 标记 `avoid_chain`（该链禁用），**不判 Q10 失败** |
| 无有效子链+显著反向 | — | `conflicted` → 判失败 |

### 3.2 qa/admission.py — 准入分类子链化

- `max_weight_for(level)` → `max_weight_for(level, subchain_specific=False)`
- 单链特异因子（`subchain_specific=True`）权重上限收紧至 **10%**（`SUBCHAIN_SPECIFIC_MAX_WEIGHT`）
- `admission_summary` 透传 subchain_specific，报告 max_weight 随之变化
- 默认 `subchain_specific=False` → 与原逻辑一致（向后兼容）

### 3.3 qa/quarterly_check.py — F6 两级重构

- `quarterly_recheck` 新增 `subchain_verdict` / `baseline_effective_chains` 参数
- F6 判定顺序：外层跨产业链不一致 → flagged；内层 `conflicted` → flagged；有效链集合漂移（当前 vs 入库基准）→ 标记 scope 复核
- 两参数均缺省 None → 走原 sector_consistent 语义（兼容）

## 四、其余受影响模块

| 模块 | 改动 | 影响 |
|---|---|---|
| `contracts.py` | + `STOCK_EVAL_CONFIG` / `FUTURES_EVAL_CONFIG` / `get_eval_config(market)`（47 §C1 口径分离） | 期货/股票评估阈值按市场路由 |
| `factor_inspector.py` | `AutoReviewPolicy.classify` + `subchain_profile` 参数；全链 IC<min 但存在 effective 子链 → 单链特异放行（49 §B2）；**Sharpe 不放行** | 机审误杀消除 |
| `energy_qa_review.py` | + `_subchain_lifecycle_cfg` / `_compute_curr_symbol_ic` / `_subchain_degradation` / `_shrink_scope`；退化段按单元粒度旁路（49 §C3/C2） | 部分链失效 → scope 收缩；全有效链失效 → degrade |
| `factor_lifecycle.py` | + `factor_lifecycle_review_subchain`（49 §C3 子链重载） | 保留全链原函数兼容 |
| `evolution_promote.py` | 晋升写子链画像 metadata + 质量矩阵首行（47 §A2 + 49 §A2） | energy 链因子入库时落张量底座 |
| `evaluation_chain.py` | + `subchain_ic_report` 报告段（47 §C2，子链画像入评估报告） | 无子链语义的股票评估不输出（n=0 过滤） |
| `factor_db/schema.py` | + `subchain_factor_quality` 表 + 3 索引 | QA/生命周期张量时序 SSOT |
| `factor_db/repository.py` | + `SubchainQualityRepository`（save/query） | 幂等 UPSERT |
| `scripts/futures_signal_pipeline.py` | + `_load_l3_subchain_meta`；合成时子链调制；Step 3h1 子链 Gate + 暴露缩放；`--enable-regime-gating` | energy 链信号合成按品种应用 m[factor][子链] |
| `settings.yaml` | 4 组配置，全部默认关 | 参数化禁硬编码 |

## 五、数据模型

```sql
-- 新表：因子×子链质量矩阵时序（SSOT）
CREATE TABLE subchain_factor_quality (
  factor_id VARCHAR, market VARCHAR, chain VARCHAR,
  evaluated_at TIMESTAMP, period_end DATE,
  n_symbols INT, mean_ic DOUBLE, std_ic DOUBLE,
  t_stat DOUBLE, p_value DOUBLE, effective BOOLEAN,
  source VARCHAR,        -- promotion | review | inspect | lifecycle
  decision VARCHAR,      -- keep | scope_shrink | degrade | retire
  PRIMARY KEY (factor_id, market, chain, evaluated_at)
);
-- factor_catalog.metadata 新增：subchain_scope / subchain_ic_profile / subchain_specific
```

## 六、测试影响

- 新增：`test_subchain_profile.py`(254) / `test_subchain_weight.py`(381) / `test_regime_gate.py`(264) / `test_qa_subchain.py`(206) / `test_lifecycle_subchain.py`(186) / `test_subchain_quality_store.py`(176)
- 修改：`test_portfolio_loop.py`(+4/-1) / `test_portfolio_loop_adaptive.py`(+127)
- 已通过回归：qa 104 + portfolio 299 + evolution 45 + 新增 46（plans/49 验收 448 全绿）；plans/47 验收 390 + plans/48 验收 694

## 七、回归风险评估与建议验证命令

**风险点**
1. `regime_adaptive_weight_adjustment` 重构了倍率取值路径（全局→子链路由），需重点回归 probability_mix / high_vol 缩减分支
2. Q10/F6 判定语义变化，需确认旧因子历史判定不受影响（新逻辑仅新评审生效）
3. `evolution_promote` metadata 构建改为显式 dict，需确认非 energy 因子 metadata 内容逐字段一致

**验证命令**

```bash
# 子链相关模块
pytest tests/factor_engine/test_subchain_profile.py tests/factor_engine/test_subchain_weight.py tests/factor_engine/test_regime_gate.py tests/factor_engine/test_qa_subchain.py tests/factor_engine/test_lifecycle_subchain.py tests/factor_engine/test_subchain_quality_store.py -m "not slow"
# portfolio_loop 主链路回归
pytest tests/factor_engine/test_portfolio_loop.py tests/factor_engine/test_portfolio_loop_adaptive.py tests/test_futures_signal_pipeline.py -m "not slow"
# qa 全量
pytest tests/factor_engine/test_qa*.py tests/factor_engine/test_energy_qa_review.py tests/factor_engine/test_factor_lifecycle.py tests/factor_engine/test_factor_inspector.py -m "not slow"
```

## 八、一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| §二 Step 2b 调制 | `enable_subchain_weight=false` 输出与旧版逐位一致 | `grep -n "enable_subchain_weight" portfolio_loop.py` + 回归测试 |
| §三 Q10 两级 | `judge_q10_subchain` verdict 三态正确 | 单测断言 |
| §三 F6 漂移 | `quarterly_recheck` 有效链集合漂移标记 | 单测断言 |
| §四 口径分离 | `get_eval_config` 按 market 路由 | 单测断言期货/股票阈值不同 |
| §五 质量表 | `subchain_factor_quality` 建表 + UPSERT 幂等 | grep schema.py + 单测 |
