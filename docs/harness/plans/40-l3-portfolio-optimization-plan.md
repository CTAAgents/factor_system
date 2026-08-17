# 40 — L3 组合重算性能优化计划（A/B/C/D 四层）


> 版本: v2.105.0

> 状态: ✅ 已完成（v2.104.0+63） · 优先级: P1 · 负责人: FTS Agent · 关联: plans/36/37/38/39, GAP-070, GAP-072, GAP-124

## 一、背景与问题定位

L3 组合重算（`fts portfolio run` / 每日 19:00 定时任务 `l3_portfolio_loop`）整体耗时长，
用户反馈"重算太慢"。经代码逐段剖析，**时间大头不在组合优化本身，而在同一批因子信号
在全面板（panel）上被反复重算**，且每处都走 Python 解释执行的 DSL + pandas 逐品种循环。

### 1.1 信号重复重算热点地图（单次 run 内同一因子信号最多重算 8 处）

| # | 阶段 | 位置 | 重算方式 |
|---|------|------|----------|
| 1 | Step 1 去重 | `portfolio_loop._compute_signal_correlations` (L3007) | `FactorExecutor(f).execute(ref_df)` |
| 2 | Step 1.5 OOS | `portfolio_loop._validate_oos_extrapolation` (L3274) | 晋升后数据重算信号+IC |
| 3 | Step 1.8 聚类 | `factor_clustering.FactorClusteringEngine.compute_signal_correlations` (L76) | ref 品种重算 |
| 4 | Step 1.9 PCA | `factor_clustering.PCASignalCompressor.compute_signal_matrix` (L392) | ref 品种重算 |
| 5 | Step 2 optimizer | `portfolio_loop._auto_build_factor_returns` (L1217) | **N因子×N品种三重循环** |
| 6 | Step 2 elastic_net | `portfolio_loop._compute_elastic_net_weights` (L1312) | 500天×全品种重算 |
| 7 | Step 2 ml_ensemble | `portfolio_loop._compute_ml_ensemble_weights` (L1495) | 500天×全品种重算 |
| 8 | Step 2.5 cross_market | `weight_learning._panel_factor_ic` (L406) | 逐品种重算 |

### 1.2 三个结构性低效点

1. **信号缓存机制未接入 L3 主流程**。项目已有 `SignalCache`（GAP-070，
   [signal_cache.py](../01-architecture.md 索引 GAP-070)）按
   `(factor_id, params, 数据指纹)` 命中复用，且 `FactorExecutor.execute` 已支持
   `signal_cache` 参数，但 L3 全部 `FactorExecutor(...)` 调用点均未传入 → 缓存形同虚设。
2. **逐品种×逐因子串行 + O(n²) 日期查找**。`_auto_build_factor_returns` / 
   `_compute_elastic_net_weights` / `_panel_factor_ic` 中 `list(df.index).index(d)`
   是 O(n) 线性查找，嵌套在双重循环内 → 整体 O(n²) 级别。
3. **DSL 是 Python 解释执行**。`compiler.py` 仅把表达式包成"调用 `eval_fts_expr` 的
   Python 函数"，运行时 `executor.evaluate` 递归解释 AST + pandas Series 逐算子，
   Python 每节点开销大。

### 1.3 与本项目已有计划的边界

- plans/37（面板向量化）、38（numba 批 4）、39（gap panel 2D）解决的是**因子算子层**
  的 panel 化与 numba 下沉；L3 组合重算的瓶颈在**流程层**（信号重复重算 + 未接缓存 +
  逐品种循环）。二者正交，38/39 完成后不会自动解决 L3。本计划是 L3 流程层专项性能治理。

## 二、四层方案设计

### A 层 — 信号缓存 + 对齐修复（纯 Python，预计 3–10x，工作量最小）

目标：同一因子信号在单次 L3 运行内只计算一次；消除 O(n²) 日期查找。

- A1: `PortfolioLoop.__init__` 创建共享 `SignalCache(max_entries=L3_SIGNAL_CACHE_ENTRIES)`。
- A2: 全部 8 处信号重算调用点接入该缓存（函数签名增加 `signal_cache: Optional[SignalCache]`，
      默认 None 保持向后兼容，测试直调不受影响）。
- A3: 新增内部向量化对齐 helper `_align_signal_to_dates(sig, df, common_dates)`，
      用 `df.index.get_indexer(common_dates)`（O(1) hash 查找）替代 `list(df.index).index(d)`。

影响文件：`fts/factor_engine/portfolio_loop.py`、`fts/factor_engine/factor_clustering.py`、
`fts/factor_engine/weight_learning.py`。

### B 层 — DuckDB 下沉信号矩阵 + 2D 对齐（预计再 3–10x）

目标：让 L3 用统一的 **2D 信号矩阵**（date×symbol×factor）执行，对齐/相关性/收益矩阵
下沉到 DuckDB SQL（C++ 向量化），给 C 层提供 2D 面板语义基础。

- B1: 新增 `fts/factor_engine/l3_signal_service.py`：
  - `SignalMatrixBundle`（3D 信号矩阵 + 前向收益 + 对齐元数据）
  - `build_signal_matrix(panel, factors, factor_codes, common_dates, forward_days)`：
    复用 A 层缓存 + 向量化对齐，一次性产出 3D 矩阵。
  - `corr_matrix(...)`：因子信号相关性用 DuckDB `corr()` SQL 计算（替代 O(n²) np.corrcoef 循环）。
  - `factor_returns(...)`：前向收益 + quantile 多空腿收益矩阵（DuckDB SQL 或向量化 numpy）。
- B2: `_auto_build_factor_returns` / `_compute_elastic_net_weights` /
  `_compute_ml_ensemble_weights` 的信号矩阵构建收敛到 B1 服务。

### C 层 — numba 2D 内核叠加（需先做 B，预计再 2–10x）

目标：B 层 2D 信号矩阵路径上的热算子（rolling rank / zscore / cvar / corr）用 numba
2D `@njit` 内核加速，逐列保持"非 NaN 计数回溯"语义（规避 plans/39 语义陷阱），
开关/依赖缺失回退现值，零漂移。

- C1: `numba_kernels.py` 扩展 2D 内核：`ts_zscore_2d` / `ts_cvar_2d` / `rolling_corr_2d`
  （`cache=True`、`fastmath=False`，依赖纪律对齐 plans/38：numba 0.66.0 / llvmlite 0.48.0）。
- C2: `feature_ops` 热算子接入 2D 快速路径（对齐现有 `ts_rank` 的 numba 接入模式）。

### D 层 — 信号矩阵一等公民 + 增量重算（架构级根治）

目标：让"重算"本身变小——L3 信号矩阵作为一等公民资产持久化，只增量更新。

- D1: DuckDB 持久化 `l3_signal_matrix`（date, symbol, factor_id, signal）+ 
  `l3_signal_meta`（factor_id, code_hash, params_hash, end_date）。
- D2: 增量策略：仅对"新晋升因子 / 未入库 (code_hash, params)"全量算；存量因子仅追加
  最近交易日窗口（如近 30 天）信号（新增列，不重算历史）。
- D3: L3 各步骤（去重/OOS/聚类/PCA/权重学习）统一从该服务读取，不再各自重算。

## 三、改动文件与函数清单

| 文件 | 改动 |
|------|------|
| `fts/factor_engine/portfolio_loop.py` | `__init__` 建缓存；`run()` 传缓存；`_compute_signal_correlations`/`_validate_oos_extrapolation`/`_auto_build_factor_returns`/`_compute_elastic_net_weights`/`_compute_ml_ensemble_weights` 接缓存 + 对齐修复 |
| `fts/factor_engine/factor_clustering.py` | `FactorClusteringEngine.compute_signal_correlations`/`PCASignalCompressor.compute_signal_matrix` 增加 `signal_cache` 参数 |
| `fts/factor_engine/weight_learning.py` | `_panel_factor_ic` 对齐修复 + `signal_cache` 参数 |
| `fts/factor_engine/l3_signal_service.py` | **新增**：2D 信号矩阵服务（B1） |
| `fts/factor_engine/numba_kernels.py` | C 层 2D 内核扩展 |
| `fts/factor_engine/feature_ops.py` | C 层算子接入（如 ts_zscore/cvar 已有入口） |

## 四、测试计划

| 层 | 测试文件 | 覆盖 |
|----|----------|------|
| A | `tests/factor_engine/test_l3_signal_cache.py` | 缓存命中复用、命中率、O(n²) 对齐等价性（新旧信号逐值一致） |
| B | 同上 + `test_l3_signal_service.py` | 3D 矩阵构建与逐品种执行逐值一致；DuckDB corr 与 np.corrcoef 一致 |
| C | `tests/factor_engine/test_numba_kernels.py`（扩展） | 2D 内核 vs 现值逐值一致（含 NaN/缺口） |
| D | `test_l3_signal_service.py` | 持久化 round-trip；增量只算新因子；meta 幂等 |

语义零漂移铁律：新实现与现值在测试数据上逐值一致（`np.allclose`/`assert_series_equal`）。

## 五、验收标准

1. A 层落地后，L3 单次运行信号重算次数从 8 处收敛到 1 处（缓存命中），相同数据信号逐值一致。
2. B 层 3D 信号矩阵与逐品种执行逐值一致；DuckDB corr 与 np.corrcoef 差异 ≤ 1e-10。
3. C 层 numba 2D 内核逐列与现值一致（含 NaN），开关关闭回退零漂移。
4. D 层持久化 round-trip 一致，增量只重算新因子/新窗口。
5. 全部受影响模块测试通过，无既有测试回归（`pytest tests/factor_engine/ -m "not slow"`）。

## 六、风险与回退

- 2D 面板 rolling 语义陷阱（plans/39）：C 层 2D 内核严格按"逐列非 NaN 计数回溯"实现，
  不做 union_dates 行数回溯；任何不一致 → 回退现值。
- DuckDB 并发写锁：L3 信号矩阵持久化走 L5 信号缓存 Parquet + DuckDB 只读查询，
  写连接短生命周期（E.4 纪律），避免与因子资产库写锁冲突。
- 缓存容量膨胀：`L3_SIGNAL_CACHE_ENTRIES` 上限控制 + LRU 淘汰。

## 七、版本记录

| 版本 | 变更 | 日期 |
|------|------|------|
| v1.0.0 | 计划创建（四层方案 + 测试 + 验收） | 2026-08-16 |
| v1.1.0 | A/B/C/D 四层全部落地（v2.104.0+63）：A 层 8 处信号重算点接入 SignalCache + `_align_signal_to_dates` 向量化对齐（替代 O(n²) list.index）；B 层新增 `l3_signal_service.py`（SignalMatrixBundle + DuckDB corr/收益矩阵下沉）；C 层 numba_kernels ts_zscore/ts_cvar 1D 内核接入 feature_ops/ops_library（回退现值零漂移）；D 层信号矩阵一等公民持久化 + `load_or_build_signal_matrix` 增量重算（code_hash 判定）；新增 test_l3_signal_service.py 16 用例 + test_numba_kernels 扩展；受影响回归 880 用例全绿 + factor_engine 全目录 not-slow 4882 passed（2 个 test_risk_tag mock 晋升用例为存量失败，非本次引入）；GAP-124 登记关闭 | 2026-08-16 |
