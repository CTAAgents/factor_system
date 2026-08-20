# 39 号计划 — 缺口面板 2D 化（取消面板化回退，主系统真提速）

> 版本: v3.0.0+10（创建于 2026-08-15）
> 关联: 37-panel-vector-plan.md（已归档）；38-numba-batch4-plan.md（numba 批 4，收益受本计划制约）
> 状态: 📦 已回退关闭（v2.104.0+58 按 §11 回退逐品种并登记豁免——真实缺口面板算子因子面板化实测 0.3x <5x 门槛）

---

## 1. 背景与根因（为什么 Phase 2/3 面板化只有 1.0x）

37 计划 Phase 2 Step 1 引入 `execute_factor_panel`（`panel_vector.py`），在
(union_dates × symbols) 矩阵上按列求值算子因子，消除逐品种 Python 循环 + 沙箱
exec。**但真实期货面板实测提速只有 ~1.0-1.05x**，根因是动态验证回退：

1. `_PanelData` 把每个品种 reindex 到 **union_dates**（多品种交易日并集），
   内部缺口（如品种 A 在某日停牌/未上市，其他品种有数据）→ 该品种列在该位置
   出现 **NaN 行**；
2. 逐品种执行时，滚动窗口在**品种自身连续日历**上滑动（无 NaN 行）；面板化执行
   时，`_rolling_apply_native`（`feature_ops.py`）用 `sliding_window_view` 在
   **固定行窗口**上滑窗，窗口内混入 NaN 行；
3. 两者窗口内的**有效观测集合不同** → 计算结果不同 → `_verify_panel_safe`
   （抽样 3 品种逐列比对）不通过 → 回退逐品种路径 → 面板化收益归零。

### 缺口语义差异示例（window=3，数据行 t0/t1/t3，t2 为 union 缺口）

| 路径 | 窗口轴 | 窗口在 t3 | 有效观测 |
|:--|:--|:--|:--|
| 逐品种 | 品种自身 [t0,t1,t3] | {t0,t1,t3} | {t0,t1,t3} |
| 面板化（现值） | union [t0,t1,t2,t3] | {t1,t2(NaN),t3} | **{t1,t3}** ← t0 被 NaN 挤出 |

**结论**：面板化窗口按"固定行数"回溯，遇到 NaN 行会**丢弃更早的观测**，与
逐品种"按非 NaN 观测回溯"语义不一致 → 这是 1.0x 的真正瓶颈，也是主系统
真正的提速钥匙（numba 批 4 的 2D 化收益同样被它锁死，见 38 §7 校准）。

## 2. 目标与收益预期

**目标**：把 `_rolling_apply_native` 的滚动窗口语义从"固定行窗"改为
"**按每列非 NaN 观测计数回溯**"（与逐品种完全一致）→ 缺口面板下验证通过 →
**取消回退** → 面板化执行真正生效。

**收益预期（诚实口径）**：

| 场景 | 现值 | 预期 | 依据 |
|:--|:--|:--|:--|
| 真实期货缺口面板（主系统） | ~1.0x | **5-13x**（算子因子面板执行） | 消除 149 品种逐品种 Python 循环 + 沙箱 exec + Series/reindex 构建（37 计划 §4.5 已指出这是生产主导项） |
| 端到端单候选（受 OOS 切片/IC/IO 摊薄） | — | 参考 Phase 1 摊薄规律，**2-5x** | IC 仅 OOS 30% 日期计算，滚动因子另算 |

## 3. 核心方案：非 NaN 计数回溯窗

对 `feature_ops._rolling_apply_native` 及 `ops_library._native_apply` 面板分支
做语义改造，**无缺口列行为逐位不变（零漂移）**：

### 3.1 窗口边界定位（每列）

```python
# 每列：非 NaN 累计计数
cum = np.cumsum(~np.isnan(col))                # (n,)
# 位置 j 的窗口起点 = 最近 window 个非 NaN 观测的起始行
start = np.searchsorted(cum, cum - window + 1, side="left")  # (n,) 向量化
```

- **无缺口列**：`cum` 连续递增 → `start[j] = j - window + 1`，与现值
  `sliding_window_view` 完全一致 → **该列零漂移**；
- **有缺口列**：`start` 跨过 NaN 行回溯，取最近 window 个非 NaN 观测，与逐品种
  语义逐位一致 → 验证通过。

### 3.2 统计量计算（按窗口区间）

- **线性统计**（sum/mean/std 的平方和分解等）：用前缀累积 + 差分，O(n) 向量化；
- **可 reduceat 统计**（sum/min/max/ptp/count 等）：`np.add.reduceat` /
  `np.minimum.reduceat` 批量，O(n log n)；
- **逐窗口非线性**（rank/分位数/slope 等）：按 `start` 边界逐窗口 numpy 计算
  （每列 O(n·w)，但窗口内无 NaN 且比现值"含 NaN 行逐行回退"更优，可与 38 计划
  numba 2D 内核复用同一窗口边界）。

### 3.3 无缺口快路径保留

改造后检测列无内部缺口（`cum[-1] == n`）→ 仍走现值 `sliding_window_view`
batch 路径，不引入任何额外开销。

### 3.4 验证链路的配套修改

- `_verify_panel_safe` 抽样比对应转绿 → `_PANEL_SAFE_CACHE` 命中 safe=True →
  面板化执行生效；
- `build_forward_return_matrix` 已逐品种先算后对齐（缺口语义天然一致），不受影响；
- 横截面算子（rank/截面 zscore 等行内运算）窗口无关，缺口对齐语义已正确，验证兜底。

## 4. 范围与边界

**做**：
- `_rolling_apply_native` 滚动内核改计数回溯窗（保持无缺口零漂移）；
- `ops_library` 面板分支的线性/reduceat/逐窗口三类统计适配；
- 面板验证链路 + 缺口签名缓存更新（窗口边界依赖缺口分布，缓存键需含缺口位置签名）；
- 对照测试：缺口面板 on/off 逐位一致 + 无缺口面板回归零漂移。

**不做**：
- 不改逐品种路径（`_cs_execute_factors` 保留为回退/审计基线）；
- 不引入 numba（与 38 计划解耦；39 优先，38 后续叠加）；
- 不改 IC 计算 / IO 缓存 / GP 演化（独立优化域）。

## 5. 分批落地（每批独立可验证、独立 bump）

| 批次 | 范围 | 验证 |
|:--|:--|:--|
| 5.1 | `_rolling_apply_native` 计数回溯窗改造 + 无缺口快路径保留 | 对照测试：无缺口逐位不变 + 合成缺口面板与逐品种一致 |
| 5.2 | 线性/reduceat 统计适配（sum/mean/min/max/ptp/count 等热算子） | 逐算子对照全绿（oracle=逐品种） |
| 5.3 | 逐窗口非线性适配（rank/分位数/slope/cvar 等） | 逐算子对照全绿 |
| 5.4 | 面板验证链路 + 缺口签名缓存键更新 | 真实 149×3000 缺口面板 on/off 产出一致 + 端到端提数 |
| 5.5 | 未达标算子回退 + 文档（01-arch/03-config/06-testing/08-gap）+ build bump | verify_doc_consistency 13/13 |

每步独立可回滚，互不阻塞。

## 6. 语义一致性保障（零漂移铁律）

1. **无缺口零漂移**：无缺口列 `start = j-window+1`，数学等价现值 → 既有
   `test_rolling_native.py` / `test_ops_native_batch3.py` 全部保持全绿；
2. **缺口面板对照**：真实缺口面板 on/off 指标逐位一致（复用 Phase 3 的
   max|Δ指标|=0 口径）+ 合成缺口（随机删行）逐品种一致；
3. **评估链对照**：`cross_section_evaluate_backtest` 开关 on/off 逐位一致；
4. **缓存键**：`_PANEL_SAFE_CACHE` 键从 `(expr, n_syms, n_dates, close_nan)`
   升级为含**缺口位置签名**（缺口语义取决于缺口分布而非仅计数，见 §1 示例）。

## 7. 风险与边界（诚实口径）

- **统计覆盖不全**：逐窗口非线性算子若无法保持逐位一致 → 保留面板化回退，登记
  豁免（不强行改写），该算子仍走逐品种路径（现状即如此，无回归）；
- **窗口边界数值边界**：`searchsorted` 对全 NaN 列/前缀区（非 NaN 数 < min_periods）
  需与 pandas `min_periods` 判定完全对齐（复用 37 批 3 已验证语义：窗口保留 NaN、
  inf→NaN、按非 NaN 计数）；
- **收益不承诺端到端量级**：2-5x 端到端为估算，受 OOS 切片/滚动因子摊薄；算子
  因子面板执行 5-13x 为主指标，5.4 提数为准；
- 本计划与 38（numba）解耦：39 先落地解锁面板化，38 再叠加 2D njit（届时
  2D 化收益才真正兑现，38 §7 已校准）。

## 8. 验收标准

1. 无缺口面板回归零漂移（既有 native 对照测试全绿）；
2. 真实缺口面板 on/off 产出逐位一致（max|Δ指标|=0）；
3. 算子因子面板执行实测 ≥5x（真实 149×3000，对照逐品种基线）；
4. 端到端单候选提数（目标 2-5x，诚实口径）；
5. ruff check + mypy（改动文件）通过；
6. verify_doc_consistency 13/13 + 文档同步 + build bump。

## 9. 待办与依赖

- [x] 5.1 计数回溯窗内核 + 无缺口快路径（已完成 2026-08-15）
  - `_rolling_apply_native` 增 `gap_aware_mode` 上下文 + 压缩-散射路径；
  - `panel_vector.execute_factor_panel` 验证/执行作用域开启缺口感知；
  - 测试 `tests/factor_engine/test_gap_panel_2d.py`（39 用例）全绿：
    无缺口 on/off 零漂移 + 内部/头部/尾部缺口与逐品种逐位一致 +
    全 NaN/窗口超长/n<min_periods/DataFrame 面板对照 + 算子面板化/回退族对照；
  - 既有 native 对照回归零漂移：test_rolling_native +
    test_ops_native_batch3 + test_panel_vector = 115 passed。
- [x] 5.2 线性/reduceat 统计适配（已完成 2026-08-15，随 5.1 合并交付）
- [x] 5.3 逐窗口非线性适配（已完成 2026-08-15，随 5.1 合并交付）
- [x] 5.4 面板验证链路 + 缺口签名缓存 + 真实提数（已完成 2026-08-15）
  - 真实期货面板（149 品种 × 3062 日，98% 列内部缺口）实测面板化
    **0.3x（3-5x 变慢）**，远低于 5x 门槛 → 判定未达标（见 §11 回退）。
- [x] 5.5 回退判定 + 文档 + bump（已完成 v2.104.0+58，见 §11）

## 10. 12 项检查清单映射（实施时逐项核对）

| # | 检查项 | 对应文档 | 状态 |
|:--|:--|:--|:--|
| 1 | 数据流/架构变更 | docs/harness/01-architecture.md（滚动内核缺口语义 + 面板验证链路） | 实施时 |
| 2 | 阶段/产出物 | docs/harness/02-lifecycle.md | 实施时 |
| 3 | 新配置项 | docs/harness/03-configuration.md（如需缺口处理开关） | 实施时 |
| 4 | 降级/熔断路径 | docs/harness/04-resilience.md（未达标算子回退） | 实施时 |
| 5 | 新指标/日志 | docs/harness/05-observability.md（验证转绿/回退计数） | 实施时 |
| 6 | 测试文件/用例数 | docs/harness/06-testing.md | 实施时 |
| 7 | 版本号/历史 | docs/harness/07-operations.md + pyproject.toml | 实施时 |
| 8 | 差距登记 | docs/harness/08-gap-analysis.md | 实施时 |
| 9 | 晋级里程碑 | docs/harness/09-advancement-plan.md | 实施时 |
| 10 | 流程文档 | docs/production_plan.md | 实施时 |
| 11 | CLAUDE.md 职责 | CLAUDE.md | 实施时 |
| 12 | README 工程指标 | README.md（测试数/版本徽章） | 实施时 |

## 11. 版本与归档

- 本计划创建：仅文档，不 bump（无代码/测试/行为变更）；
- 实施时：每批完成 build bump（日常开发），不触发里程碑 bump；
- **计划关闭（v2.104.0+58）**：按 §7 回退并登记豁免。5.4 真实期货缺口面板
  （149 品种 × 3062 日）实测算子因子面板化 **0.3x（3-5x 变慢）**，远低于 5x
  门槛（98% 列内部缺口 → 压缩-散射开销反超，逐品种 Python 循环收益被吞没）→
  判定未达标。按 §7 回退：**评估链信号构建摘除 `execute_factor_panel`，恒逐品种
  执行；仅 IC 计算保留全矩阵化（`compute_cs_ics_vectorized`）**，产出与旧路径
  逐位一致、性能 on/off 持平。`execute_factor_panel` 保留为独立模块（缺口感知
  滚动内核 `gap_aware_mode` + `_GapAwareFrame` 供独立调用方/对照基准复用），
  登记豁免（见 docs/harness/08-gap-analysis.md GAP-121）。实施记录：
  ① evaluation_chain 信号构建摘除面板化（v2.104.0+58）；② panel_vector/
  settings 文档同步；③ 新增 test_chain_operator_panel_fallback_per_symbol
  （面板化打桩抛错契约）→ test_panel_vector 32 用例 + test_gap_panel_2d 39 用例
  全绿（受影响 138 passed + ruff/mypy 通过）；④ 全量回归通过后归档。
