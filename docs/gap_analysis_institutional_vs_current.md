# FTS 系统 vs 机构标准做法 — 差异对比与修复优先级

> 生成日期: 2026-08-06 | 版本: v2.15.0 | 状态: 部分已修复

---

## 一、核心原则对比

| 维度 | 机构标准 | FTS 当前做法 | 差距 | 严重程度 | 修复状态 |
|------|---------|-------------|:----:|:--------:|:--------:|
| 数据使用边界 | 训练/验证/测试严格分离，GP 仅限训练集 | GP 在全量数据上搜索，OOS 数据被复用 | **数据泄露** | **P0** | ✅ **已修复** |
| 样本外评估 | 多窗口 Walk-Forward 均值 | 单一 70/30 时间分割 | 统计稳定性不足 | P1 | ⏳ 待修复 |
| 测试集隔离 | 完全隔离，最终报告仅一次 | OOS 实为全量数据末尾切片 | 理论无效 | **P0** | ✅ **已修复** |
| 多重检验校正 | 强制过滤门（FDR/Bonferroni） | 已实现 `MultipleTestResult` 但非强制门 | 缺少执行 | P1 | ⏳ 待修复 |
| IC 衰减监控 | 月更新，衰减 > 5%/月 自动淘汰 | 有 `decay_6m` 字段，但大部分填 0.05 | **形同虚设** | **P0** | ✅ **已修复** |
| 实盘前验证 | 纸面交易 6-12 个月 | 无此环节 | 缺少 | P2 | ⏳ 待修复 |

---

## 二、逐项详细对比

### 2.1 数据泄露 — P0 (最高优先级) ✅ 已修复

**当前问题**:

```
GP 演化流程:
  _evaluate_fitness(tree, self._data)  ← 使用整个 DataFrame
  → 选择 Top 因子
  → evaluate_backtest() 做 70/30 分割
  → 报告的 OOS IC 是最后 30%

但 GP 已经在全量数据上做了 20 代 × 500 个因子的搜索，
最后 30% 的数据也被用于选择，OOS 不独立。
```

**机构做法**:

```
训练集 (60%)    验证集 (20%)    测试集 (20%)  ← 完全隔离
[===============][=============][=============]
  GP 搜索范围      早停/选择     仅一次报告
```

**修复方案**:

| 步骤 | 改动 | 工作量 | 状态 |
|------|------|:------:|:----:|
| 1 | GP 构造时传入 `train_mask`，`_evaluate_fitness` 只计算训练集 IC | 2 天 | ✅ 已修复 |
| 2 | 算子演化引擎 `OperatorEvolutionEngine._evaluate_fitness` 使用 `train_mask` | 1 天 | ✅ 已修复 |
| 3 | 演化循环中 `_run_gp_evolution` / `_try_operator_engine_evolution` 构建 `train_mask` 并透传 | 1 天 | ✅ 已修复 |

**修复详情**:

- `GPEvolver.__init__`: 已存在 `train_mask` 参数，`_evaluate_fitness` 使用 `eval_data = self._data[self._train_mask]` 
- `OperatorEvolutionEngine.__init__`: 已存在 `train_mask` 参数，`_evaluate_fitness` 使用 `eval_data = self._data[self._train_mask]`
- `FeatureOpsEngine.run_gp_search`: 新增 `train_mask` 参数并透传到 `GPEvolver`
- `evolution_loop._run_gp_evolution`: 构建 `train_mask`（前 60% 数据）并传入 `run_gp_search`
- `evolution_loop._try_operator_engine_evolution`: 构建 `train_mask`（前 60% 数据）并传入 `OperatorEvolutionEngine`

**修改文件**:
- [fts/factor_engine/operator_evolution.py](file:///d:/Programs/factor_system/fts/factor_engine/operator_evolution.py): `_evaluate_fitness` 使用 `eval_data`
- [fts/factor_engine/feature_ops.py](file:///d:/Programs/factor_system/fts/factor_engine/feature_ops.py): `run_gp_search` 新增 `train_mask` 参数
- [fts/factor_engine/evolution_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_loop.py): `_run_gp_evolution` / `_try_operator_engine_evolution` 构建并透传 `train_mask`

---

### 2.2 IC 衰减字段形同虚设 — P0 ✅ 已修复

**当前问题**:

```
current_combo.json 中 118 个因子:
  - 115 个因子 decay_6m = 0.05
  - 3 个因子 decay_6m = 0.0

decay_6m = 0.05 意味着 6 个月后 IC 保留 95%，
这对任何真实因子都是不可能的。
```

**根源**: `decay_6m` 在 `_write_to_duckdb` 中默认值为 0.05，`BacktestMetrics` 无 `decay_6m` 字段导致该值从未被填充。

**修复方案**:

| 步骤 | 改动 | 工作量 | 状态 |
|------|------|:------:|:----:|
| 1 | `BacktestMetrics` 添加 `decay_6m` 字段 | 0.5 天 | ✅ 已修复 |
| 2 | `evaluate_backtest` 从 OOS 前后两半 IC 对比计算真实 `decay_6m` | 1 天 | ✅ 已修复 |
| 3 | `_write_to_duckdb` 默认值改为 0.0（不再使用 0.05 占位） | 0.5 天 | ✅ 已修复 |

**修复详情**:

- `BacktestMetrics` 新增 `decay_6m: float` 字段
- `evaluate_backtest` 计算 OOS 前后两半 IC 对比：`decay_6m = max(0, 1 - |IC_second| / |IC_first|)`
- `_write_to_duckdb` 默认值从 `0.05` 改为 `0.0`，避免误导性占位

**修改文件**:
- [fts/factor_engine/contracts.py](file:///d:/Programs/factor_system/fts/factor_engine/contracts.py): `BacktestMetrics` 新增 `decay_6m` 字段
- [fts/factor_engine/evaluation_chain.py](file:///d:/Programs/factor_system/fts/factor_engine/evaluation_chain.py): `evaluate_backtest` 返回真实 `decay_6m`
- [fts/factor_engine/evolution_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/evolution_loop.py): `_write_to_duckdb` 默认值改为 0.0

---

### 2.3 多重检验校正非强制门 — P1 ⏳ 待修复

**当前问题**:

```python
# evaluation_chain.py:344-346
temp_eval = FactorEvaluation(
    ...
    level_3_multiple=MultipleTestResult(),  # 占位 — 空结果
    passed=False,
    ...
)
```

`MultipleTestResult` 已经实现了 FDR 和 Bonferroni 校正，但 **在因子晋升流程中，它只是一个占位符，结果没有被用于过滤**。因子是否晋升由 `passed` 字段决定，但多重检验结果从未影响 `passed`。

**修复方案**:

| 步骤 | 改动 | 工作量 | 状态 |
|------|------|:------:|:----:|
| 1 | 在 `_promote_to_elite` 中检查 `level_3_multiple.passed`，未通过则禁止晋升 | 0.5 天 | ⏳ 待修复 |
| 2 | 确保 `evaluate_backtest` 的 p_value 正确传递到多重检验校正器 | 0.5 天 | ⏳ 待修复 |
| 3 | 在组合构建的 Phase 2 中，对 `p_adj > 0.05` 的因子做额外标记 | 1 天 | ⏳ 待修复 |

---

### 2.4 单窗口 OOS 评估 — P1 ⏳ 待修复

**当前问题**:

```python
# evaluation_chain.py:136-137
oos_signal = signal[-oos_n:]       # 仅一个窗口
oos_returns = forward_returns[-oos_n:]
ic, icir = _compute_ic(oos_signal, oos_returns)
```

单个 OOS 窗口的 IC 对市场状态敏感。如果该窗口恰好是趋势市场，趋势类因子 IC 会系统性偏高。

**机构做法**: 5 折或 12 月滚动 Walk-Forward。

```
Window 1: [1-240] 训练 → [241-300] 测试 → IC₁
Window 2: [61-300] 训练 → [301-360] 测试 → IC₂
...
IC_report = mean(IC₁, IC₂, IC₃, IC₄, IC₅)
```

`walk_forward.py` 中已经实现了 `WalkForwardEvaluator.evaluate()`，但它在 `EvaluationChain.evaluate()` 中只是**可选参数**，不是强制流程。

**修复方案**:

| 步骤 | 改动 | 工作量 | 状态 |
|------|------|:------:|:----:|
| 1 | 从 `evaluate_backtest` 内部调用 WalkForward，替代单窗口 OOS | 2 天 | ⏳ 待修复 |
| 2 | 存储的 IC 改为多窗口 IC 均值 + IC 标准差（用于判断稳定性） | 1 天 | ⏳ 待修复 |
| 3 | WalkForward 的 IC 标准差 > 0.1 的因子标记为不稳定 | 0.5 天 | ⏳ 待修复 |

---

### 2.5 组合 Sharpe 虚高 — P1 ⏳ 待修复

**当前问题**: 组合 Sharpe = 6.67。行业经验范围：

| 策略类型 | 合理 Sharpe 范围 |
|---------|:---------------:|
| 高频做市 | 4-8 |
| 统计套利（中频） | 2-4 |
| 期货 CTA（中低频） | 1.0-2.5 |
| 股票多头 | 0.5-1.5 |

期货 CTA 组合 Sharpe 6.67 远超出合理范围，**强烈暗示过拟合或数据泄露**。

**修复方案**:

| 步骤 | 改动 | 工作量 | 状态 |
|------|------|:------:|:----:|
| 1 | 设置组合 Sharpe 警戒线：> 3.5 自动标记并触发独立验证 | 0.5 天 | ⏳ 待修复 |
| 2 | 在组合报告中加入 IC 的 95% 置信区间 | 1 天 | ⏳ 待修复 |
| 3 | 加入随机化测试（shuffle 收益后重跑，看是否还能得到高 Sharpe） | 2 天 | ⏳ 待修复 |
| 4 | 将 IC 按时间分段报告（展示 IC 在不同市场状态下的表现） | 1 天 | ⏳ 待修复 |

---

### 2.6 缺少纯外推验证 — P2 ⏳ 待修复

**当前问题**: 因子从 GP 演化到晋升 elite 再到组合构建，全部基于同一份历史数据。没有"冻结因子后在新数据上运行"的环节。

**机构做法**: 因子确定后，在**后续的真实新数据**上运行至少 6 个月（纸面交易），只有 IC 保持稳定才进入实盘。

**修复方案**:

| 步骤 | 改动 | 工作量 | 状态 |
|------|------|:------:|:----:|
| 1 | 在 `evolution_loop.py` 的 `_promote_to_elite` 中加入时间戳标记 | 0.5 天 | ⏳ 待修复 |
| 2 | 每次 L3 运行时，对比上次运行以来的新数据因子 IC | 2 天 | ⏳ 待修复 |
| 3 | 连续 3 次 L3 运行 IC 衰减 > 20% 的因子自动降级 | 1 天 | ⏳ 待修复 |

---

### 2.7 IC 上限截断 — P2 ✅ 已修复

**当前问题**: 118 个因子中，Top 10 因子的 IC 在 0.39-0.57 之间。期货截面 IC 的行业合理范围：

| 市场 | 合理截面 IC | 当前 FTS IC |
|------|:----------:|:-----------:|
| 美股股票 | 0.02-0.08 | — |
| A 股股票 | 0.03-0.10 | — |
| 中国期货 | 0.03-0.08 | 0.03-0.57 |
| 国际期货 | 0.02-0.06 | — |

**修复方案**: 在组合构建的 `synthesize_signals` 中加入 IC 上限截断。

**修复详情**:

- `synthesize_signals` 对所有因子 IC 进行上限截断：IC > 0.15 的按 ±0.15 计算权重
- 原始 IC 值保留在 `_ic_raw` 字段中供审计追溯
- 截断逻辑在权重计算之前执行，确保三种权重模式（equal_weight / sharpe_weight / elastic_net）均受约束

**修改文件**:
- [fts/factor_engine/portfolio_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py): `synthesize_signals` 入口处添加 IC 上限截断逻辑

---

## 三、修复优先级汇总

| 优先级 | 问题 | 影响 | 工作量 | 修复后预期效果 | 状态 |
|:------:|------|:----:|:-----:|--------------|:----:|
| **P0** | 数据泄露（GP 在全量数据搜索） | 所有 IC/Sharpe 指标不可信 | 6 天 | 因子 IC 回归合理范围 | ✅ |
| **P0** | IC 衰减字段形同虚设 | 无法识别失效因子 | 5 天 | 组合能自动淘汰衰减因子 | ✅ |
| P1 | 单窗口 OOS 评估 | IC 统计稳定性差 | 3.5 天 | IC 报告包含置信区间 | ⏳ |
| P1 | 多重检验非强制门 | 随机因子可能混入组合 | 2 天 | 降低虚假发现的概率 | ⏳ |
| P1 | 组合 Sharpe 虚高 | 误导性的业绩预期 | 4.5 天 | 组合信披更真实 | ⏳ |
| P2 | 缺少纯外推验证 | 因子上线后可能快速失效 | 3.5 天 | 生产环境因子更稳定 | ⏳ |
| P2 | IC 上限截断 | 权重分配被过拟合因子主导 | 1 天 | 权重更分散、更稳健 | ✅ |

**建议执行顺序**: P0(已修复) → P1 → P2，剩余 P1+P2 约 13.5 天。

---

## 四、修复后的预期效果

修复 P0 后，预期因子 IC 分布会从当前的：

```
当前: IC 范围 [0.0345, 0.5722], 均值 0.1815
      Top 10 因子 IC 均值 ≈ 0.49
```

变为更接近行业正常水平的：

```
预期: IC 范围 [0.01, 0.12], 均值 0.04-0.06
      Top 10 因子 IC 均值 ≈ 0.08-0.10
```

组合 Sharpe 也会从 6.67 回落到更合理的 1.5-2.5 区间。

---

## 五、已修复项汇总

| 差距 | 优先级 | 修复时间 | 涉及文件 | 验证方式 |
|:----:|:------:|:--------:|---------|---------|
| 数据泄露 | P0 | 2026-08-06 | operator_evolution.py, feature_ops.py, evolution_loop.py | GP 搜索仅使用前 60% 训练集数据 |
| IC 衰减 | P0 | 2026-08-06 | contracts.py, evaluation_chain.py, evolution_loop.py | OOS 前后两半 IC 对比计算真实衰减 |
| IC 上限截断 | P2 | 2026-08-06 | portfolio_loop.py | IC > 0.15 因子按 ±0.15 计算权重 |

---

*本报告基于 FTS v2.15.0 代码分析生成，部分差距已修复，剩余 P1/P2 项建议纳入后续迭代计划。*