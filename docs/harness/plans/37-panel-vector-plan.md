# 37 号计划 — 横截面评估全矩阵化提速（panel_vector）

> 版本: v2.104.0+90（创建于 2026-08-15）
> 关联 GAP: GAP-121（08-gap-analysis.md P2 新登记）/ GAP-I502 执行器后端
> 状态: ✅ Phase 1 + Phase 2（Step 1 + 批 1/2/3）+ Phase 3 全部完成；计划可归档

---

## 1. 背景与目标

L2 演化横截面评估路径（`evaluation_chain.cross_section_evaluate_backtest` 内部
`_cs_execute_factors` / `_cs_build_matrices` / `_cs_compute_ics`）存在两层性能瓶颈：

1. **逐品种 1D 执行**：对每个期货品种单独执行因子 + 构建 Series/reindex 对齐；
2. **逐日 spearmanr 循环**：每截面期调用一次 `scipy.stats.spearmanr`（3000+ 日 × 每次
   ~30-50µs 调用开销），且重复排序。

原型基准（`scripts/benchmark_panel_ic.py`，真实 149 品种 × 3062 日）实测：
「预对齐 2D 面板 + 全矩阵化 IC」相对旧路径单候选加速 **5-13x**，且逐日 IC
max|ΔIC| ~ 1e-16 完全一致。

**目标**：将全矩阵化实现（`fts/factor_engine/panel_vector.py`）分阶段接入主链路，
逐步兑现提速，全程保证评估产出与旧路径逐位一致（零语义漂移）。

## 2. 基准数据（2026-08-15 实测）

| 场景 | 逐候选加速 | 5 候选总加速 | 含预对齐摊薄 |
|:-----|:-----|:-----|:-----|
| 合成 104 品种 × 5163 日（全量 IC，原型） | 8.1–13.4x | 11.0x | 7.1x |
| 真实 149 品种 × 3062 日（全量 IC，原型） | 7.8–12.2x | 9.5x | 6.7x |
| **生产口径（真实数据，OOS 30% + 滚动因子）** | **1.2x** | — | — |

正确性：全部场景新旧路径逐日 IC 一致（max|ΔIC| ≈ 1e-16，机器精度）。

> ⚠️ 诚实口径说明：**9.5x 是全量 IC + 2D 因子执行的联合收益**。生产路径
> `cross_section_evaluate_backtest` 的 IC 仅在 OOS 切片（30% 日期，~918/3062 日）
> 上计算，且逐品种因子执行 + Series/reindex 构建（Phase 1 未改）占主导，故
> **Phase 1（仅 IC 矩阵化）生产实测 ~1.2x**。IC 的完全一致（diff ~3e-18）已
> 在真实数据上验证。完整提速需 Phase 2（2D 因子执行 + 全面板预计算）。

## 3. 阶段拆分与范围

| 阶段 | 内容 | 收益 | 状态 |
|:-----|:-----|:-----|:-----|
| **Phase 1** | `cross_section_evaluate_backtest` 接入 IC 矩阵化开关（`cross_section_panel_vector`，默认关闭）；signal/fwd 仍走生产逐品种路径（保语义），仅 `_cs_compute_ics` 分派到 `panel_vector.compute_cs_ics_vectorized` | IC 计算消除（生产实测 ~1.2x，受 OOS 切片限制） | ✅ 已完成 |
| **Phase 2 Step 1** | 面板化因子执行引擎 `execute_factor_panel`（算子因子 DSL 在 (union_dates × symbols) 矩阵按列求值）+ 动态抽样验证 + 安全回退 + 接入 `cross_section_evaluate_backtest` 快速路径 | 无内部缺口面板上消除逐品种循环；**真实期货面板（内部缺口）回退 → 实测 ~1x** | ✅ 已完成（详见 §4.5 诚实结论） |
| Phase 2 Step 2 | DSL 算子 native 向量化重写（消灭 `rolling.apply` Python 回调）+ 缺口无关的逐品种循环消除 | 真正兑现算子因子提速（**批 1+2+3 完成：P0 热算子 7 处 + 伪 apply 9 处 + Alpha101 16 处 + ops_library 真滚动回调 14 处 + regime/gp 2 处，实测 4.8–525x**，详见 §4.6） | ✅ 批 1·批 2·批 3 完成 |
| Phase 3 | 走航/审计复用预对齐面板 + 对照全绿后切换默认开启 | 全链路 | ✅ 已完成（v2.104.0+57：cross_section_panel_vector 默认开启；缺口面板实测 on/off 产出一致 + 性能持平 1.0-1.05x） |

### 3.1 角色边界

- 仅改动 `evaluation_chain` 横截面评估路径 + `FTSConfig` 新配置项 + `panel_vector` 模块；
- 不改动：L1/L3 循环、因子执行沙箱、审计链、组合层、信号管道、实盘链路；
- Phase 2 Step 1 的面板化执行仅新增引擎（不改 expr_dsl 既有语义），代码因子收敛 DSL 仍为后续独立工程。

## 4. Phase 1 设计

### 4.1 新配置项（FTSConfig）

```
cross_section_panel_vector: bool = env FTS_CROSS_SECTION_PANEL_VECTOR（默认 false）
```

- `None`（调用方未显式指定）→ 读取配置；显式传入优先（测试可控）。
- 默认关闭：不改变任何现有行为；开启前须对照测试全绿。

### 4.2 接入点（evaluation_chain.cross_section_evaluate_backtest）

- 新增可选参数 `use_panel_vector: Optional[bool] = None`；
- 新增模块级分派 `_cs_compute_ics_dispatch(oos_signal, oos_ret, use_panel_vector)`，
  返回 `list[float]` 与 `_cs_compute_ics` 同构（下游消费零改动）；
- 替换 2 处 `_cs_compute_ics` 调用（中性化前 `pre_ics` 与中性化后 `ics`）。

### 4.3 等价性保证

- `compute_cs_ics_vectorized` 联合掩码（signal∩ret 有限）内 rank + 行内 Pearson，
  与逐日 `spearmanr` 在联合有效子集上完全一致；
- 常数守卫（原始值 std < 1e-10）与有效样本下限（< 5）与旧路径同口径；
- 接入对照测试：开关 on/off 评估产出（ic/icir/t_stat/win_rate 等）逐位一致。

## 4.5 Phase 2 Step 1 — 面板化因子执行引擎（诚实结论）

**交付**：`panel_vector.execute_factor_panel`（算子因子 DSL 在 (union_dates × symbols)
矩阵按列求值）+ `build_forward_return_matrix`（逐品种先算后对齐）+ 动态抽样验证
（最早/最晚/中位上市 3 品种逐列比对）+ 验证结果按 (expression, 面板缺口签名) 缓存 +
`cross_section_evaluate_backtest` 快速路径接入（代码因子/验证不通过安全回退）。

**正确性**：真实期货面板算子因子开关 on/off IC diff = 0.00；30 面板测试 + 67 评估链
测试全绿；缺口面板（含内部缺口）与 DataFrame 不可按列算子（zscore 标量守卫）均安全
回退 None（零漂移由验证兜底）。

**性能（诚实口径）**：真实期货面板（149 品种，A 等品种内部缺 7/3062 交易日）存在
**内部缺口** → 面板化滚动（NaN 填充）与逐品种滚动（缺行）语义不一致 → 验证回退 →
算子因子生产提速 **~1x**。进一步发现：DSL 算子 `ts_zscore`/`ts_skewness` 等用
`rolling.apply`（Python 回调，每窗口一次 lambda），这才是算子因子单候选 ~35s 的
真正瓶颈——**面板化无法消灭 apply 回调**，对 apply 类算子即使无缺口也仅小幅提速。

**Phase 2 Step 2 方向（真正兑现算子提速）**：
1. `feature_ops` 中 `rolling.apply` 类算子 native 向量化重写（rolling rank/zscore/
   skew/kurt/slope 等改用 pandas 原生窗口方法或 numba 内核）；
2. 缺口无关的逐品种循环消除（`_cs_execute_factors` 的 Series/reindex 构建，与
   面板化解耦）；
3. 面板化引擎保留：对无内部缺口面板（部分单市场/股票面板）仍可消除逐品种循环。

## 4.6 Phase 2 Step 2 — 算子 native 向量化重写（详细实施计划）

> 目标：消灭 DSL 算子执行路径中 `rolling.apply` 的 Python 回调开销（每窗口一次
> lambda 调用），解除算子因子单候选 ~35s 的真正瓶颈。依赖约束：pyproject 无
> numba（本次不引入新依赖，首选 numpy 滑动窗口内核）。
>
> **执行状态（v2.104.0+55）**：批 1 完成 —— feature_ops 7 个 P0 热算子改写为
> sliding_window_view 内核 + ops_library 9 处伪 `apply(np.sqrt)` 直改 `np.sqrt()`；
> 新增 test_rolling_native.py 28 对照用例全绿；相关模块 476 用例全绿。实测
> （149 品种 × 3000 日，逐品种循环）：ts_product 72x / ts_zscore 219x /
> ts_min_max_diff 183x / ts_cum_max 349x / max_drawdown 525x / ts_argmin 11x /
> self_corr 87x；面板路径（2D 单次调用）全品种 ~0.1-0.5s。
>
> **批 2 完成（v2.104.0+55）** —— registry.ts_argmax/ts_decay_linear +
> seed_loader/seed_data 模板 14 处 Alpha101 算子（ts_argmax/ts_argmin/ts_rank/
> ts_product/decay_linear/highday/lowday × 2 模板）改写为自包含向量化；
> 新增 test_seed_ops_native.py 56 对照用例 + registry 对照 8 用例全绿；
> 实测 4.8–96.8x（ts_product 96.8x / decay_linear 86.6x / highday·lowday 15x /
> argmax·argmin 14x / ts_rank 6.5x），registry 两算子 ~0.06s。
> 存量失败（非本次引入，git stash 基线验证）：test_risk_tag.py 2 个 mock
> 晋升用例（Verifier GAP-114 v2.104.0+13 换手成本校验变更后 mock 未同步）。
>
> **批 3 完成（v2.104.0+56）** —— ops_library 真滚动回调 14 个函数（ts_cvar_95/99、
> CCI md、ts_aroon_up/down、ts_linear_trend_score、ts_range_expansion 内 amp、
> ts_sideways_flag 内 amp_ratio、cs_trim_mean_diff、cs_gini_score、cs_concentration、
> cs_top_bottom_spread、ts_volume_concentration、ts_volume_cycle）+ regime_features
> `_rolling_autocorr`（提升模块级）+ gp_evolver 模板 `ts_product` 改写为
> `feature_ops._rolling_apply_native` 通用内核（前缀逐窗口 + 主区间
> sliding_window_view 批量 + 含 NaN 行精确回退）；**关键语义对齐**：pandas
> `rolling.apply` 实测窗口值保留 NaN（min_periods 仅按非 NaN 计数判定输出），
> inf 一律视为 NaN —— 批 1/2 因 min_periods=window 全有效窗口恰好等价，批 3
> 显式修正内核；新增 test_ops_native_batch3.py 48 对照用例（17 处 × 3 窗口 ×
> 7 场景 + DataFrame 面板路径 + acf lag 1/3 + gp 模板 exec）全绿；相关模块
> 1145 + 评估链/expr_dsl 146 用例全绿 + ruff/mypy 通过；实测（3000 日 1D）：
> cvar 24-28x / aroon 5.1x / linear_trend 68x / gini 45x / vol_conc 40x /
> regime autocorr 147x。
>
> **Phase 3 完成（v2.104.0+57）** —— `cross_section_panel_vector` 默认开启
> （FTS_CROSS_SECTION_PANEL_VECTOR 默认 true）：走航（cross_section_walk_forward
> 窗口内调用 cross_section_evaluate_backtest 读配置，自动继承）与审计（横截面
> 评估复用评估链走航结果）均复用预对齐面板路径；切换默认前实测缺口面板
> （149 品种 × 3000 日，8 品种内部缺口）on/off **产出一致（max|Δ指标|=0）+
> 性能持平（operator 1.00x / code 1.05x）**，且算子因子面板化有动态验证兜底
> （验证不通过回退逐品种）→ 零漂移；新增 test_default_panel_vector_enabled
> （未配置默认开启 + 默认调用产出与显式 on 一致）；顺带修复 GAP-121 评估链
> 修复引入的 mock 装配缺陷（test_g11/g4 object.__new__ 缺 _prior_evaluations，
> git stash 干净基线复测确认 HEAD 通过/工作区失败，非 Phase 3 引入）。

### 4.6.1 范围（39 处 `rolling.apply` 调用点分类）

核心原则：**按热路径优先**。L2 演化执行算子经 `expr_dsl/registry.py`（import
`feature_ops.RollingOps` 等）分派，热算子集中在 feature_ops.py；ops_library 大量
为冷算子（D 系列），另有 9 处为**伪 apply**（`Series.apply(np.sqrt)` 逐元素 ufunc，
非滚动回调）。

**A 类 — P0 热算子（feature_ops.py，被 DSL registry 直接引用，7 处）**

| 算子 | 行号 | 现状 | 改写方案 |
|:-----|:-----|:-----|:-----|
| ts_product | L74 | `apply(np.prod)` | 无原生 rolling.prod → sliding_window_view + np.prod（含 NaN 语义与 np.prod 一致） |
| ts_zscore | L125 | `apply((last-mean)/std)` | `(x - rolling.mean()) / rolling.std()` + std>0 守卫（NaN 语义需对照核对） |
| ts_min_max_diff | L157 | `apply(max-min)` | `rolling.max() - rolling.min()`（完全等价，风险最低） |
| ts_cum_max | L162 | `apply(x.cummax().iloc[-1])` | `rolling.max()`（窗口内 cummax 末值 = 窗口 max，完全等价） |
| max_drawdown | L336 | `apply((x/cummax-1).min())` | `(x / rolling.max() - 1)` 后取窗口内滚动 min（数学等价，对照验证） |
| ts_argmin | L1189 | `apply(np.argmin)` | sliding_window_view + np.argmin（min_periods=2 头部处理） |
| self_corr | L1549 | `apply(_lag1)` lag-1 自相关 | 数学组合：cov(x_t,x_{t+1})/var = (E[xy]-E[x]E[y])/σ²，rolling.mean 组合向量化（ddof/NaN 对照验证） |

**B 类 — P1 Alpha101 语义算子（expr_dsl/registry.py 2 处 + seed_loader 7 处 +
seed_data/loader 7 处，共 16 处）**

- registry：`ts_decay_linear`（L289，线性加权滑窗）、`ts_argmax`（L301）；
- seed_loader / seed_data/loader（同构各 7 处）：`ts_argmax`/`ts_argmin`/`ts_rank`
  （argsort 排名）/`ts_product`/`decay_linear`/`highday`/`lowday`，均为
  min_periods=1 的 WorldQuant Alpha101 语义算子。若演化候选池大量使用 Alpha101
  seed 则为热路径，否则 P1。

**C 类 — P2 冷算子（ops_library 23 处 + regime_features 1 + gp_evolver 1）**

- **C0 伪 apply（9 处，ops_library）**：`Series.apply(np.sqrt)` 逐元素 ufunc →
  直接 `np.sqrt()`（零回调、零语义风险，可随批 1 顺手替换）；
- **C1 真滚动回调（14 处）**：`ts_cvar_95/99`（L199/211）、CCI `md`（L557）、
  `ts_aroon_up/down`（L766/774）、`ts_linear_trend_score`（L1025）、`ts_amp`
  （L1154）、`ts_amp_ratio`（L1234）、`cs_trim_mean_diff`（L1539）、`cs_gini_score`
  （L1574）、`cs_concentration`（L1593）、`cs_top_bottom_spread`（L1605）、
  `ts_volume_concentration`（L2498）、`ts_volume_cycle`（L2504）；
- regime_features `_rolling_autocorr`（L299）、gp_evolver `ts_product`（L987）。

### 4.6.2 技术方案：无 numba 的滑动窗口向量化内核

**方案 1（首选）：`numpy.lib.stride_tricks.sliding_window_view` 通用滚动内核**

```python
def _rolling_map(arr, window, min_periods, func, **kwargs):
    """通用向量化滚动窗口映射（保留 min_periods 头部语义）。"""
    n = len(arr)
    out = np.full(n, np.nan, dtype=float)
    if n < min_periods:
        return out
    view = np.lib.stride_tricks.sliding_window_view(arr, window)  # (n-w+1, w)
    out[window - 1:] = func(view, axis=-1, **kwargs)
    for k in range(min_periods, window):          # 头部前缀，迭代数 < window
        out[k - 1] = func(np.asarray(arr[:k]), axis=-1, **kwargs)
    return out
```

- func 为 numpy ufunc / nan-ufunc（`np.max/np.min/np.ptp/np.nanquantile/np.argmax` 等），
  零 Python 回调；
- 头部前缀每次迭代 ≤ window-1 次（量级 <60），开销可忽略；
- skipna 语义与原始逐窗口 numpy 调用一致（均用 nan-ufunc）。

**方案 2（局部）：pandas 原生窗口方法**

- 仅用于可精确等价的算子（ts_min_max_diff / ts_cum_max / ts_zscore 无 NaN 输入时）；
- 风险：pandas rolling 遇 NaN 直接传播 NaN，与 apply 的 skipna 语义不一致 →
  仅经对照测试确认等价后使用。

**方案 3（不采用）：引入 numba** —— pyproject 依赖锁定纪律，本次不新增依赖；
若未来需要极限性能再单独评估。

### 4.6.3 语义一致性保障（零漂移铁律）

1. **逐算子对照测试**：每个改写算子新增参数化测试（`@pytest.mark.parametrize`），
   覆盖随机序列（含 NaN / 常数 / 单调 / 头部缺口）× 多 seed × 多 window × 多
   min_periods，新旧实现逐位比对（`np.allclose` + `equal_nan=True`）；
2. **DSL 全链路**：改写后跑 `tests/factor_engine/expr_dsl/` 全量 +
   `test_feature_ops.py` 全量 + `test_panel_vector.py`；
3. **评估链对照**：`cross_section_evaluate_backtest` 开关 on/off 产出逐位一致；
4. **性能验证**：热算子因子单候选耗时对比（目标 <5s，较 ~35s 提速 ≥7x，诚实口径）。

### 4.6.4 分批落地顺序（每批独立可验证、独立 bump）

| 批次 | 范围 | 验证 |
|:-----|:-----|:-----|
| 批 1 | feature_ops P0 热算子 7 处 + ops_library 伪 apply 9 处 | ✅ 完成（test_rolling_native 28 对照用例 + 相关模块 476 用例全绿，v2.104.0+54） |
| 批 2 | registry + seed_loader + seed_data/loader Alpha101 算子 16 处 | ✅ 完成（test_seed_ops_native 56 对照用例 + registry 8 对照全绿，实测 4.8–96.8x，v2.104.0+55） |
| 批 3 | ops_library 真滚动回调 14 处 + regime_features + gp_evolver | ✅ 完成（test_ops_native_batch3.py 48 对照用例全绿，实测 5.1–147x，v2.104.0+56） |

### 4.6.5 风险与回退

- **NaN skipna 语义差异**（最大风险）：所有含 NaN 对照测试兜底；若某算子改写后
  无法保持逐位一致 → 保留原 apply 实现，在模块 docstring 记录豁免原因（诚实登记，
  不强行改写）；
- **min_periods 头部差异**：`_rolling_map` 前缀处理覆盖；
- **不引入 numba**，避免依赖膨胀。

### 4.6.6 验收标准（Step 2）

1. 批 1 完成：test_feature_ops.py 全绿（含新增逐算子对照参数化用例）；
2. expr_dsl 全量 + test_panel_vector 全绿；
3. 性能：热算子因子单候选实测提速 ≥7x（诚实口径，真实数据）；
4. ruff check + mypy（改动文件）通过；
5. 文档同步（37 计划状态 + 01-architecture 涉及处）+ build bump。

## 5. 验收标准

1. `tests/factor_engine/test_panel_vector.py`（30 用例）全绿；
2. `cross_section_evaluate_backtest(use_panel_vector=True)` 与 `False` 产出逐位一致
   （代码因子 + 算子因子双覆盖）；
3. 默认（未配置）行为与现状完全一致（回归零漂移）；
4. `scripts/benchmark_panel_ic.py --real` 保持通过；
5. ruff check 全绿。

## 6. 待办与依赖

- [x] 原型基准验证（scripts/benchmark_panel_ic.py）
- [x] 生产模块 panel_vector.py + 单元测试
- [x] Phase 1 接入主链路（IC 矩阵化，v2.104.0+52）
- [x] Phase 2 Step 1 面板化执行引擎（v2.104.0+53，真实面板受缺口限制 ~1x）
- [x] Phase 2 Step 2 批 1：P0 热算子 native 改写（v2.104.0+54，实测 11–525x，GAP-121 跟踪）
- [x] Phase 2 Step 2 批 2：registry + seed Alpha101 算子 16 处（v2.104.0+55，实测 4.8–96.8x）
- [x] Phase 2 Step 2 批 3：ops_library 真滚动回调 + regime_features + gp_evolver（v2.104.0+56，实测 5.1–147x）
- [x] Phase 3 默认切换 + 全链路对照（v2.104.0+57：cross_section_panel_vector 默认开启，缺口面板 on/off 产出一致 + 性能持平）

## 7. 版本与归档

- 本次：build bump（日常开发），不触发里程碑 bump。
- 计划关闭：Phase 3 切换默认并全量回归通过后归档。
