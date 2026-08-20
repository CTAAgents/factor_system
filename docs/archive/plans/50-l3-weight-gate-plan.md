# 50 — L3 权重层 Gate 闭环：Gate 并入子链调制矩阵（权重源头生效）

> 版本: v3.1.0+3
> 状态: ✅ 已完成（v2.104.0+113，A/B 模块全部实施） · 优先级: P1 · 负责人: FTS Agent · 关联: plans/47（子链调制矩阵 m，已完成）, plans/48（子链方向 Gate，已完成）, plans/49（子链质量矩阵 q，已完成）, GAP-136/137（已关闭）, GAP-138（已登记并关闭）

## 一、背景与问题定位

### 1.1 现状：Gate 在信号管道硬生效，但 L3 权重源头不感知

plans/48 的子链方向 Gate（`regime_gate.py`）目前仅在**信号管道** Step 3h1 硬生效：

```
L3（权重源头）                     信号管道（信号输出口）
  factor_weights.json  ──────────→  _compute_composite_scores（按品种×子链调制）
  （含 subchain_weights = m）          → Step 3h1 apply_subchain_gate（硬 Gate：avoid 剔除/降权）
                                       → apply_exposure_scale
```

L3 的调制矩阵 **m[factor][子链]**（plans/47，Step 2b 构建）只携带"质量×幅度"语义（`build_subchain_weights` 由 subchain_ic_profile 派生），**未并入 Gate 的方向回避决策**。结果：

- 某子链 Regime 方向不明（avoid）时，L3 产出的 `factor_weights.json` 中该链调制系数仍为 1.0 → 权重源头未回避，完全依赖信号管道 Step 3h1 补救。
- `subchain_weights` 与 `subchain_gate_distribution` 两段在 L3 中割裂：一个管"做多少"、一个只做观测，未形成"质量×幅度×方向"综合权重。

### 1.2 核心缺陷

1. **权重源头无 Gate**：avoid 链在 L3 产出的调制矩阵中不归零/不降权，Gate 语义只在信号层补救（职责倒挂）。
2. **三层矩阵未闭合**：q（质量）/m（幅度）在调制矩阵、g（方向）在 Gate 观测段——同网格但未合并，组合权重未消费完整张量。
3. **可扩展性**：扩展到其它产业链时，若新链处于 avoid，权重源头同样不回避，只能靠信号层。

### 1.3 目标

在 L3 **Step 2.5**（Gate 开启 + energy + 子链 regime 检测成功）时，将 Gate 决策派生为链级缩放系数并入调制矩阵：

```
m'[factor][子链] = m[factor][子链] × gate_scale[子链]
gate_scale:  avoid+hard → 0.0（剔除） / avoid+soft → soft_avoid_ratio（降权）
             long/short/neutral → 1.0（不干预；方向过滤属信号层职责，不重复）
             无 Gate 子链 → 按 blind_default（avoid→0 / neutral→1.0）
```

达成：**Gate 在权重源头（factor_weights.json → 信号管道权重）生效**，与信号层 Step 3h1 天然串联（权重层已回避、信号层再方向过滤，不冲突不双重惩罚）。

## 二、方案设计

### A 模块 — Gate 缩放系数派生（纯函数，可单测）

- **A1** `regime_gate.py` 新增 `gate_scale_map(gates, config) -> dict[str, float]`：
  - `avoid` + `avoid_mode=hard` → `0.0`
  - `avoid` + `avoid_mode=soft` → `soft_avoid_ratio`
  - `long` / `short` / `neutral` → `1.0`（方向层职责在信号管道 Step 3h1，权重层不重复过滤）
  - `gates` 缺失的子链（无检测/盲测）→ `blind_default=="avoid"` → `0.0`，`"neutral"` → `1.0`
  - 纯函数：输入 gates+config → 输出 {子链: scale}，无副作用。

### B 模块 — L3 Step 2.5 接线（Gate 并入调制矩阵）

- **B1** `portfolio_loop.py` Step 2.5：Gate 开启（`l3.regime_gating.enabled` 或 CLI `--enable-regime-gating`）+ `market=="energy"` + 子链 regime 检测成功时：
  1. 构建 `gates = build_subchain_gates(...)`（已有，目前仅用于报告段）
  2. `scale = gate_scale_map(gates, _gconf)`
  3. **若 `self._subchain_modulation` 非空**（即 `enable_subchain_weight` 已构建 m）：对每因子行 `mod[f][chain] *= scale[chain]`，同步更新 `s["subchain_weights"]`（Step 2b 标注的因子级调制）
  4. 记录 `self._subchain_gate_scale = scale`（质量报告观测）
  5. 日志：`[L3] Step 2.5: Gate 并入调制矩阵（avoid 链归零/降权）: {子链: scale}`
- **B2** 依赖语义：Gate 的权重层生效**依赖调制矩阵存在**（子链权重通道）；`enable_subchain_weight` 未开时 m 不存在，Gate 保持"观测段"语义（`subchain_gate_distribution`），不改变任何输出（零行为变更）。
- **B3** 质量报告：新增 `subchain_gate_scale` 段（各子链 gate 缩放系数快照），与 `subchain_gate_distribution`（决策）、`subchain_quality_matrix`（质量）、`subchain_exposure`（暴露）四网合一。

## 三、数据模型与契约（变更点）

| 对象 | 变更 | 说明 |
|---|---|---|
| `regime_gate.py` | + `gate_scale_map(gates, config)` | Gate 决策 → 链级缩放系数（纯函数） |
| `portfolio_loop.py` | Step 2.5 接线（B1） | m' = m × gate_scale；`_subchain_gate_scale` 记录 |
| `__all__`（regime_gate.py） | + `gate_scale_map` | 对外契约 |

无存储/配置变更（复用 `l3.regime_gating.*` 与 `l3.subchain_weight.*` 既有配置）。

## 四、实施步骤（建议顺序）

1. A1：`gate_scale_map` 纯函数 + `__all__` 更新
   - 验证：单测覆盖 avoid-hard/avoid-soft/long/short/neutral/缺链 blind 各分支
2. B1：portfolio_loop Step 2.5 接线
   - 验证：Gate 开启 + energy + 子链检测成功 + 调制矩阵非空 → m' 含 gate_scale；未开启/非 energy/无调制矩阵 → 零行为变更
3. B3：质量报告 `subchain_gate_scale` 段
   - 验证：报告 JSON 含该段；Gate 未开启时为空段
4. 回归 + 文档同步（GAP-138 登记关闭 / 01/02/03/06/07/08/09 / 版本 bump）

## 五、测试方案

- `tests/factor_engine/test_regime_gate.py`（扩展）：`gate_scale_map` 全分支（avoid-hard→0.0 / avoid-soft→soft_avoid_ratio / long·short·neutral→1.0 / 缺链 blind_default=avoid→0.0·neutral→1.0 / 空输入）
- `tests/factor_engine/test_portfolio_loop_adaptive.py`（扩展）：L3 接线——Gate 开启+energy+调制矩阵存在 → 调制矩阵乘 gate_scale（avoid-hard 链因子调制归零 / avoid-soft 按比例降权 / long·short 链不变）；Gate 未开启 / 非 energy / 无调制矩阵 → 输出与 Gate 无关（零行为变更断言）
- 回归：`pytest tests/factor_engine/test_regime_gate.py tests/factor_engine/test_portfolio_loop_adaptive.py tests/factor_engine/test_portfolio_loop.py tests/factor_engine/test_subchain_weight.py -m "not slow"`
- 分级测试政策：不跑全量

## 六、验证标准（验收）

1. `gate_scale_map` 纯函数全分支正确（avoid-hard=0 / avoid-soft=ratio / 其余=1.0 / 缺链按 blind_default）。
2. Gate 开启 + energy + 调制矩阵非空：avoid 链因子调制系数归零（hard）或按 soft_avoid_ratio 降权；long/short/neutral 链不变。
3. Gate 未开启 / 非 energy / `enable_subchain_weight` 未开：L3 输出与 Gate 无关（逐位一致，灰度零行为变更）。
4. 新增测试全绿 + 受影响回归无破坏 + ruff 通过；GAP-138 登记并关闭。

## 七、风险与回退

- **风险 1（依赖调制矩阵）**：Gate 权重层生效依赖 `enable_subchain_weight`（m 存在）。若仅开 Gate 不开子链权重，Gate 保持观测语义（不改变输出）——文档明示依赖关系，不引入隐式行为。
- **风险 2（双重惩罚）**：权重层 gate_scale 已回避 avoid 链、信号层 Step 3h1 再处理 avoid——两者为乘性串联（权重源头已为 0/降权，信号层对 0 得分跳过/降权不重复），无双重惩罚（复用 plans/48 B3 防双重惩罚语义：score=0 跳过）。
- **风险 3（方向语义错位）**：long/short 在权重层不干预（权重无方向），方向过滤保持信号层单一职责——避免权重层误做方向过滤。
- **回退路径**：`l3.regime_gating.enabled=false` 或 `enable_subchain_weight=false` → 完全回退现状（Gate 仅观测段 / 无 Gate）。

## 八、一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| §A1 gate_scale_map | avoid-hard→0.0 / avoid-soft→ratio / 其余→1.0 / 缺链按 blind_default | `grep -n "def gate_scale_map" regime_gate.py` + 单测 |
| §B1 接线 | Gate 开启+energy+调制矩阵非空 → m' 乘 gate_scale | 单测断言（avoid 链归零/降权） |
| §B1 零变更 | Gate 未开/非 energy/无调制矩阵 → 输出与 Gate 无关 | 单测断言（逐位一致） |
| §B3 观测段 | 质量报告含 `subchain_gate_scale` 段 | 单测断言报告结构 |

## 九、实施后文档同步清单（Harness 13 项，已完成 ✅）

1. ✅ `docs/harness/01-architecture.md`（Step 2.5 补充 gate_scale 并入调制矩阵）
2. ✅ `docs/harness/02-lifecycle.md`（Phase 50 产出物）
3. ✅ `docs/harness/03-configuration.md`（复用既有配置，无新增；依赖语义说明）
4. ✅ `docs/harness/04-resilience.md`（回退路径：Gate 未开/无调制矩阵 → 现状）
5. ✅ `docs/harness/05-observability.md`（`subchain_gate_scale` 段）
6. ✅ `docs/harness/06-testing.md`（新测试用例数）
7. ✅ `docs/harness/07-operations.md`（版本历史）
8. ✅ `docs/harness/08-gap-analysis.md`（登记并关闭 GAP-138：L3 权重层未消费 Gate）
9. ✅ `docs/harness/09-advancement-plan.md`（晋级里程碑）
10. ✅ `pyproject.toml`（版本 bump v2.104.0+113）
11. ✅ `README.md`（工程指标）
