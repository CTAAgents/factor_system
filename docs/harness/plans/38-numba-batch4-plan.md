# 38 号计划 — numba 内核定点引进（批 4）

> 版本: v3.0.0+10（创建于 2026-08-15）
> 关联: 37-panel-vector-plan.md（Phase 1-3 已归档）；GAP-121（评估链性能跟踪）
> 状态: ✅ 已关闭（§4.5 回退后仅保留 ts_rank 1D/2D 内核；ts_cvar/ts_zscore 已回退现值）

---

## 1. 背景与目标

37 计划批 1-3 已消灭 `rolling.apply` Python 回调的大头
（`sliding_window_view` 向量化，实测 4.8–525x，v2.104.0+54/55/56），并完成横截面
评估全矩阵化默认切换（v2.104.0+57）。剩余可优化面收敛为 **Python 循环 + pandas
对象构建**三类：

1. **逐列 pandas 包装**：`ops_library._native_apply` 面板分支对每个品种执行
   `series.apply(lambda col: ...)` + `to_numpy`/`pd.Series` 包装（每个品种一次
   Python 调用 + 两次对象构建）；
2. **含 NaN 多趟聚合走逐行回调**：`feature_ops._rolling_apply_native` 的
   `row_fn` 路径（头部前缀区逐窗口 + 主区间含 NaN 行逐行回退）——真实缺口面板
   每列都触发该路径；
3. **前缀区逐窗口循环**：`regime_features._rolling_autocorr` 等算子 min_periods
   头部区仍为 Python 循环。

**目标**：以 numba `@njit` 定点清除上述循环，限定"**含 NaN 多趟聚合 + 面板 2D**
"窄子集，设定逐算子 ≥10x 门槛与整体 <1.5x 叫停阈值，全程零语义漂移。

> **与 37 计划衔接（诚实口径）**：37 §4.6.2 方案 3"不采用 numba"是当时的
> pyproject 依赖纪律约束；批 1-3 已吃到 numpy 向量化大头（4.8–525x），numba 批 4
> 是**顺序衔接的独立增量**（剩余 Python 循环的定点清除），不推翻已归档路线的
> 任何结论。是否落地以本计划门槛实测为准。

## 2. 已测基准数据（2026-08-15 本会话实证）

| 项目 | 实测 | 结论 |
|:-----|:-----|:-----|
| numba 1D 算子 | **151x** | 单列含 NaN 多趟聚合收益巨大 |
| numba 面板 2D | **12.7x** | 矩阵直传 + 单次输出包装的增益（含逐列包装消除） |
| 首次 JIT 编译 | ~1.1s/函数 | 短生命周期 CLI 每次都要付 → 必须 `cache=True` |
| pandas 简单聚合 | 已是 C 增量最优（numpy 朴素矩阵化反慢 5.7x） | **这些算子禁止 numba/numpy 化** |
| 结构瓶颈 | 逐列 pandas 对象构建，numba 无法解决 | 必须把逐列 `apply` 循环下沉进 2D njit |

## 3. 范围与边界

**做**：
- 5 个候选算子（见 §4.1）2D njit 内核改写；
- `_native_apply` 面板分支 2D 化（消除逐列循环）；
- `ops_numba` 配置开关 + import 失败/版本冲突降级回退现值；
- pyproject 锁定 numba/llvmlite 依赖（§4.5）。

**不做**：
- pandas 原生 C 聚合（`pct_change`/`rolling.sum/mean/std` 等，实测 numpy 化反慢 5.7x）；
- GP 演化、IC 缓存 IO、其他非面板环节（独立优化域，不混入本批次）；
- `numba-scipy` 等扩展、`fastmath` 激进编译、GPU/parallel 调度。

## 4. 详细设计

### 4.1 候选算子清单（含准入门槛）

**准入标准（硬门槛）**：对真实规模面板（149 品种 × 3000 日）跑对照 benchmark，
**<10x 不采纳**，回归 pandas/numpy 现值并记录豁免理由。

| 算子 | 现状 | numba 收益点 | 门槛预期 |
|:-----|:-----|:-----|:-----|
| `ts_cvar_95/99` | batch=linspace 分位数+掩码均值 | 分位数内循环 → 单趟 njit | 高置信 |
| `ts_rank` | row_fn 逐行排序 | 每行排序 → njit 单趟 | 高置信 |
| `ts_zscore` | row_fn 逐行均方差 | 每行 mean/std → njit 单趟 | 高置信 |
| `regime_features._rolling_autocorr` | 主区间 batch + 前缀区循环 | 前缀区逐窗口 → njit | 待实测 |
| `_native_apply` 面板分支（所有算子共同外壳） | 逐列 `apply` + pandas 包装 | **最大单项**：面板整体一次 njit | 待实测 |

**明确排除**：`ts_linear_trend_score`/`ts_aroon`/`ts_volume_cycle` 等现已是
numpy batch 路径的算子（增量小，**待实测**确认）；`ts_product`（单次 prod）。

### 4.2 内核范式：面板级 2D njit（唯一推荐形态）

不写 1D 逐列版本（保留 151x 但无法绕开逐列包装）。统一在面板矩阵上单次调用：

```python
from numba import njit, prange
import numpy as np

@njit(cache=True, fastmath=False)  # fastmath=False：数值严格对齐 pandas/numpy
def _panel_cvar_njit(panel: np.ndarray, window: int, min_periods: int, q: float) -> np.ndarray:
    """(rows, cols) 全 NaN 面板 → 同形输出。含 NaN 窗口按非 NaN 计数判定。"""
    n, m = panel.shape
    out = np.full((n, m), np.nan)
    for i in prange(m):                       # 列并行（默认单线程保守起步，实测后决策）
        for j in range(min_periods - 1, n):
            lo, hi = max(0, j - window + 1), j + 1
            cnt = 0
            for k in range(lo, hi):
                if not np.isnan(panel[k, i]):
                    cnt += 1
            if cnt < min_periods:
                continue
            # 窗口切片 → 该算子专用统计（逐算子单内核，不做通用回调）
            ...
    return out
```

要点：
- **逐算子单内核**，不做 njit 版通用 `row_fn` 回调（numba 闭包回调会触发重编译，
  收益打骨折）；
- **语义严格对齐**：inf→NaN、窗口保留 NaN、按非 NaN 计数判定——与
  `feature_ops._rolling_apply_native`（37 批 3 实测 pandas 语义）同规；
- **面板直传**：`execute_factor_panel` / `_native_apply` 内部就是矩阵，njit 内核
  吃矩阵、只包一次输出，绕开逐列 pandas 构建（§2 结构瓶颈的唯一解法）；
- `prange` 列并行仅在列数大且写无竞争时开启，默认单线程保守起步。

### 4.3 编译策略

| 项 | 决策 | 理由 |
|:-----|:-----|:-----|
| `cache=True` | 必开 | 首进程 ~1.1s/函数落盘 `__pycache__`，后续进程零编译（L2 CLI 短生命周期必需） |
| 冷启动 | 模块导入时统一 warm-up（后台线程） | 避免首个算子吃 1.1s |
| 版本漂移 | numba/llvmlite 升级触发重编译 | 依赖锁定后为一次性成本 |
| `fastmath` | 关闭 | 保数值逐位一致，不做牺牲精度的激进编译 |

### 4.4 配置与降级

- 新增 `ops_numba: bool = env FTS_OPS_NUMBA（默认 true，仅依赖安装后生效）`；
- import 失败 / 版本冲突 / `FTS_OPS_NUMBA=false` → 回退现值实现，**零漂移**；
- 与 37 计划的 `cross_section_panel_vector` 正交，互不耦合。

### 4.5 依赖纪律（pyproject）

现状：环境已装 numba 0.66.0 / llvmlite 0.48.0，**pyproject 未声明**。决策：

- `pyproject.toml` 新增锁定 `numba==0.66.0` + `llvmlite==0.48.0`；
- 不引入 `numba-scipy` 等扩展，保持最小面；
- 依赖纪律变更需经专项确认（本项目受保护项：依赖管理在 AGENTS.md 三. 通用规范）。

## 5. 分批落地（每批独立可验证、独立 bump）

| 批次 | 范围 | 验证 |
|:-----|:-----|:-----|
| 4.1 | 骨架：`fts/factor_engine/numba_kernels.py` + `ops_numba` 开关 + 降级 + warm-up | 面板路径回归全绿（开关 off 行为与现值逐位一致） |
| 4.2 | cvar_95/99 + ts_rank + ts_zscore 三个高置信算子改写 | 逐算子对照测试全绿（oracle 照抄现值实现） |
| 4.3 | `_native_apply` 面板分支改 2D njit 入口（消除逐列循环） | 面板 on/off 产出一致 + 实测 ≥10x |
| 4.4 | 真实 L2 单候选耗时对比（热算子数 × 面板规模） | 汇报提数：单候选总耗时降幅 |
| 4.5 | 未达门槛算子回退现值 + 文档同步（01-arch/03-config/06-testing/08-gap）+ build bump | verify_doc_consistency 13/13 |

每步独立可回滚，互不阻塞；任一算子 <10x 门槛即按 §7 回退。

## 6. 语义一致性保障（零漂移铁律）

1. **逐算子对照测试**：oracle 照抄现值实现（37 批 3 已验证的 `_rolling_apply_native`
   语义：窗口保留 NaN / inf→NaN / 非 NaN 计数判定），参数化覆盖随机 / 含 NaN /
   常数 / 单调 / 短 / 空 / 单元素 × 多 window × 多 min_periods，`np.allclose` +
   `equal_nan=True` 逐位比对；
2. **面板 on/off 对照**：`execute_factor_panel` 开关 on/off 产出逐位一致；
3. **评估链对照**：`cross_section_evaluate_backtest` 开关 on/off 指标逐位一致；
4. **性能门槛（分场景）**：逐算子 benchmark（真实 149×3000 面板）≥10x；整体
   门槛分场景——真实期货缺口面板端到端 ≥1.3x、无缺口面板 ≥1.5x，未达标叫停（§7）。

## 7. 风险与边界（诚实口径，2026-08-15 收益校准）

> **收益校准（2026-08-15）**：初版"预期 2-5x"隐含无缺口面板假设，偏乐观。
> 按真实期货面板回退机制逐项校准后，预期改为**分场景**口径：

| 场景 | 预期提升 | 依据 |
|:--|:--|:--|
| 真实期货缺口面板（主系统） | **1.0-1.3x**（接近持平） | ① 三算子主区间已 batch 向量化（numba ≈ 持平），仅前缀+缺口行受益，缺口占比 ~7/3062≈0.2% 稀释到个位数；② `_native_apply` 2D 化（最大单项）依赖 `execute_factor_panel` 不回退，而真实缺口面板**验证回退逐品种**（Phase 3 实测 operator 1.00x / code 1.05x），2D njit 执行不到 → 12.7x 无法兑现 |
| 无缺口面板（股票/单市场/合成） | **2-3x** | 2D njit 理想 12.7x，被对齐/IO 等其他环节摊薄 |

**校准结论**：plan38 单独引入，主系统期货缺口面板预期 ≈1.1x，按本计划 §6 门槛
（整体 <1.5x）**触发叫停回退**；真实适用对象是**无缺口面板场景**。主系统真正的
提速路径是**缺口面板 2D 化**（39 计划，另立项）。

- **叫停门槛（分场景）**：真实期货缺口面板端到端 ≥1.3x 才达标；不达标按 §6 回退，
  不强行交付。无缺口面板场景仍按逐算子 ≥10x 门槛执行；
- numba 不解决 GP 演化 / IC 缓存 IO 等其他环节（独立优化域）；
- `fastmath=False` 保证数值与 pandas/numpy 逐位一致；
- 依赖新增有环境兼容风险 → 降级路径保证零漂移。

### 7.1 决策留痕：推翻 plan37（全量 numba）vs 保留 plan37 + 定点 numba（2026-08-15 评估）

**结论**：推翻 plan37（放弃 `sliding_window_view` 全量改 numba）相对现有 plan37
**≈1.0x，无整体提升，部分算子反更慢**；不采纳。

| 执行路径 | plan37 现状（sliding_window_view） | numba 全量重写 | 相对提升 |
|:--|:--|:--|:--|
| 主区间全有效行 batch | C 层，实测 11–525x（zscore 219x / cum_max 349x / max_drawdown 525x） | ≈持平（同为 C 层内存带宽） | ~1.0x |
| 前缀区逐窗口（min_periods<window） | Python 循环 | njit 可加速 | 提升点 |
| 含 NaN 行逐行回退（缺口面板） | Python 循环 | njit 可加速 | 提升点 |
| 逐列 pandas 包装（`series.apply`） | Python 循环 | 2D njit 消除 | 提升点 |
| 简单聚合（pct_change/rolling.mean 等） | pandas C | numpy 化反慢 5.7x | 负 |

**关键证据**：numba 1D 实测 **151x，低于 sliding_window_view 已实现的 zscore 219x /
cum_max 349x / max_drawdown 525x**——numba 在 batch 路径只是另一份 C 代码，对已
被 numpy ufunc 吃满内存带宽处无增益甚至略慢。

**Amdahl 视角**：推翻方案大部分工作落在已饱和的主区间（≈0 收益）+ 附带清 Python
残留，整体上限 1.0-1.3x 且付出全量重写风险（已全绿的 4.8-525x 成果 + 零漂移铁律）；
保留 plan37 + 定点（38/39）只清 Python 残留 + 39 解锁面板化，整体 2-5x，改动面
最小。推翻唯一例外是放弃逐位一致 + `fastmath=True` 激进编译，但破坏 37 零漂移铁律
与全部对照测试，且违反"因子可解释 + 统计严谨"红线，不采纳。

## 8. 验收标准

1. 逐算子对照测试全绿（oracle = 现值实现）；
2. 面板 + 评估链开关 on/off 产出逐位一致；
3. 逐算子 benchmark（真实 149×3000 面板）≥10x 门槛，未达者回退并记录豁免；
4. 真实 L2 单候选总耗时降幅提数（分场景：缺口面板 ≥1.3x / 无缺口面板 ≥1.5x）；
5. ruff check + mypy（改动文件）通过；
6. verify_doc_consistency 13/13 + 文档同步 + build bump。

## 9. 待办与依赖

- [ ] 4.1 numba_kernels.py 骨架 + ops_numba 开关 + 降级（未实施，待确认）
- [ ] 4.2 cvar_95/99 + ts_rank + ts_zscore 改写（未实施，待确认）
- [ ] 4.3 _native_apply 面板分支 2D 化（未实施，待确认）
- [ ] 4.4 真实 L2 提数（未实施，待确认）
- [ ] 4.5 回退判定 + 文档 + bump（未实施，待确认）
- [ ] pyproject 锁定 numba/llvmlite（需专项确认依赖纪律变更）

## 10. 12 项检查清单映射（实施时逐项核对）

| # | 检查项 | 对应文档 | 状态 |
|:--|:-----|:-----|:-----|
| 1 | 数据流/架构变更 | docs/harness/01-architecture.md（numba_kernels 模块 + 面板执行分派） | 实施时 |
| 2 | 阶段/产出物 | docs/harness/02-lifecycle.md | 实施时 |
| 3 | 新配置项 | docs/harness/03-configuration.md（ops_numba） | 实施时 |
| 4 | 降级/熔断路径 | docs/harness/04-resilience.md（import 失败回退） | 实施时 |
| 5 | 新指标/日志 | docs/harness/05-observability.md（warm-up/编译耗时日志） | 实施时 |
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
- 计划关闭：4.5 完成 + 全量回归通过后归档；若门槛实测整体 <1.5x 叫停，登记
  豁免后关闭（不强行交付）。
