# 52 号计划 — L3 信号矩阵增量窗口追加（D 层性能兑现）

> 版本: v3.1.0+3（创建于 2026-08-17）
> 关联: 40-l3-portfolio-optimization-plan.md（D2 承诺）、51-vectorization-gap-fix-plan.md（B1 激活）、GAP-139
> 状态: ✅ 已完成（2026-08-17，GAP-139 关闭）
> 优先级: P2 · 负责人: FTS Agent

---

## 1. 背景与现状（诚实记录）

plans/51 B1 已把 D 层增量库接入生产（`PortfolioLoop` 配置自动激活 + end_date 由面板
最新交易日推导），但当前增量语义仅为"**同窗口因子级复用**"：

- 增量判定维度 `(factor_id, market, end_date)`，`end_date` = 面板最新交易日；
- 每日窗口推进 → 增量判定全 miss → A2 形状防护触发**全量重算**（正确性已保证）；
- 增量库仅"同日多次运行/重试"命中，跨日运行信号重算量 = 全量。

**plans/40 D2 承诺**：存量因子仅追加最近交易日窗口（如近 30 天）信号，不重算历史——未实现。

**根因**：
1. 持久化按 `(factor, market, end_date, symbol)` 存**整窗信号数组**，写入即整窗覆盖；
2. 增量判定只有"因子级复用"（code/params 一致），无"窗口级追加"路径。

## 2. 目标与收益预期

**目标**：跨日运行时，对"可复用因子"仅重算**新增交易日**（+ 窗口算子回退所需历史段），
与全量重算**逐位一致**。

**收益（诚实口径）**：每日 L3 run 信号重算量从"N 因子 × 全品种 × 全窗口（~3000 日）"
降为"N 因子 × 全品种 × (新增交易日 + max_window 回退) 天"。若每日新增 1-5 交易日、
窗口 3000 日 → 重算量降 2-3 个数量级；端到端 L3 单 run 耗时预期显著下降（提数为准，
受 IC/OOS/聚类等其他环节摊薄，不承诺端到端量级）。

## 3. 技术方案

### 3.1 增量窗口判定（前缀追加）

复用因子（A1 双哈希判定通过后）再经"**前缀一致性**"细分：

| 场景 | 判定 | 动作 |
|:-----|:-----|:-----|
| 库中旧窗口日期 = 当前 `common_dates` 前 `n_old` 天的有序子集（前缀一致）+ 有增量日期 | 增量路径 | 仅重算新增交易日段（§3.2），拼接后整窗回写 |
| 前缀一致 + 无增量日期（同日重复运行） | 现行为 | 直接读库复用，不重算 |
| 前缀不一致（历史数据修订/回填/窗口收缩） | A2 兜底 | 降级全量重算 |

**前缀判定实现**：`l3_signal_meta` 表新增 `dates_digest VARCHAR`（旧窗口日期序列 blake2
指纹，`hash(tuple(dates))`）。增量判定时计算 `hash(tuple(common_dates[:n_old]))` 与库中
`dates_digest` 比对——一致 → 前缀成立；否则全量。指纹成本 O(n)，极低。

### 3.2 增量执行语义（窗口算子回退 + 对照验证）

**关键陷阱**：因子代码是任意 Python（沙箱执行），滚动窗口算子（`ts_zscore`/`ts_rank`/
rolling corr 等）窗口长度在代码内部，**无法静态判定**。若仅用"新增日期"片段执行因子，
窗口算子在片段前缀处会因缺历史数据产生 NaN 或错误值 → 与全量执行不一致。

**增量执行方案**（逐位一致 + 验证兜底）：

```
对每个可增量因子 × 每品种:
    增量日期段 = common_dates 尾部新增交易日（新窗口 - 旧窗口）
    执行切片   = 旧窗口尾部回退段 + 增量日期段          # 回退长度 = 保守窗口上限
    信号       = execute(执行切片) → 截取增量日期段输出
```

- **回退长度**：保守取 `min(n_old, W_RECALL)`，`W_RECALL` 覆盖 DSL/feature_ops 最大
  支持窗口（如 500 天）；无法覆盖的超长窗口因子由对照验证兜底。
- **对照验证**（零漂移铁律）：增量拼接结果与全量重算在**抽样品种 × 抽样日期**逐位比对
  （`np.allclose` + `equal_nan=True`）；任一不一致 → 该因子降级全量重算（复用
  `_verify_panel_safe` 验证模式，plans/37 先例）。
- **前向收益**：现值已按当前 panel 全量重建（不依赖库），保持不动。

### 3.3 持久化与元数据

- `_L3_SIGNAL_META_TABLE` 增列 `dates_digest`（`_init_tables` 幂等迁移，存量库缺列时
  `ALTER TABLE ADD COLUMN IF NOT EXISTS` 或读取时降级——增量判定对无 digest 的旧行
  一律按"前缀未知"降级全量，安全兼容）。
- 写入保持整窗覆盖（新 end_date 覆盖写），表结构不变；**计算量**仅限新增段。

### 3.4 模块改动清单

| 文件 | 改动 |
|------|------|
| `fts/factor_engine/l3_signal_service.py` | `_init_tables` 增 `dates_digest` 列；`persist_signal_matrix` 计算并写 `dates_digest`；`load_or_build_signal_matrix` 重构增量路径：`incremental_factor_ids` 复用因子 → 前缀判定 → 增量拼接（新增 `_append_window_signals`：切片执行 + 截断 + 抽样对照验证，验证不过全量）；`incremental_factor_ids`/`load_signal_matrix` 兼容无 digest 旧库 |
| `fts/config/settings.py` | 新增 `l3_signal_store_append_window: bool = env FTS_L3_SIGNAL_APPEND_WINDOW`（默认 true；验证兜底保证零漂移，保守可默认 false 灰度） |
| `fts/factor_engine/portfolio_loop.py` | 无改动（signal_store 已激活；`_auto_build_factor_returns` end_date 推导不变） |

### 3.5 与既有机制的关系

- **A1 双哈希**（plans/51）：code/params 变化 → 全量（不受增量影响，优先级更高）；
- **A2 形状防护**（plans/51）：前缀不一致/读取失败 → 全量（增量路径的前缀判定是其细化）；
- **SignalCache**：增量段的执行切片数据指纹与全量执行不同，不冲突（缓存按数据指纹区分）。

## 4. 测试计划（测试随重构）

| 用例 | 覆盖 |
|------|------|
| 增量拼接 vs 全量重算逐位一致（含 rolling 窗口算子、缺口品种、尾部新增 1/5/30 天） | `np.allclose` + `equal_nan` |
| 前缀不一致（历史数据修订/窗口收缩）→ 降级全量 | 结果与全量一致 + warning 日志 |
| code/params 变化 → 全量（A1 优先级高于增量） | 断言重算 |
| 同日重复运行（无增量）→ 直读库 | 零重算 |
| 对照验证失败（超长窗口无法覆盖）→ 该因子全量 | 自动降级 |
| 存量库无 `dates_digest` 列 → 降级全量（兼容） | 迁移安全 |
| 端到端二次运行（end_date 推进）日志显示"增量追加 N 天 / 全量重算 M 因子" | 可观测 |

## 5. 验收标准

1. 增量路径产出与全量逐位一致（新增测试全绿）；
2. 二次运行（窗口推进）增量命中：日志显示仅重算新增段，未重算历史；
3. 性能提数：增量重算耗时相对全量显著下降（单 run 信号重算段对比）；
4. 受影响模块回归全绿（`pytest tests/factor_engine/ -m "not slow"`）+ ruff/mypy 通过；
5. 12 项检查清单 + verify_doc_consistency 13/13 + build bump；
6. GAP-139 关闭登记。

## 6. 风险与边界（诚实口径）

- **任意因子代码窗口不可静态判定** → 保守回退窗口 + 抽样对照验证兜底，验证不过全量
  （零漂移）；超长窗口因子增量收益打折，不承诺全覆盖；
- **历史数据修订** → 前缀判定不通过自动全量（正确性优先，不做"智能合并"）；
- **meta 迁移** → 幂等 + 缺列降级，存量库安全；
- **收益不承诺端到端量级**：增量收益集中在信号构建段，受 IC/OOS/聚类等环节摊薄，
  以第 5.3 提数为准。

## 6.1 实施记录（2026-08-17 落地，GAP-139 关闭）

| 项 | 落地 |
|------|------|
| meta `dates_digest` | `_init_tables` 建表增列 + `ALTER TABLE ADD COLUMN IF NOT EXISTS` 幂等迁移（存量库缺列 → `_classify_reusable` 降级全量安全兼容）；`persist_signal_matrix` 写 `_dates_digest(bundle.dates)`（blake2） |
| 前缀判定 | `_classify_reusable(reusable, market, end_date, common_dates, db_path, append_enabled)` → (direct_reuse, append_plan{fid: n_old}, fallback)；digest 缺失/前缀不符 → fallback 全量 |
| 增量执行 | `_append_window_signals`：旧窗尾部 `_W_RECALL=500` 回退段 + 新增交易日切片执行 + 截断拼接；`_verify_append` 抽样 2 品种全量执行对照新段逐位一致（不过 → 该因子全量 + warning） |
| 接线 | `load_or_build_signal_matrix`：direct/append 分开读库（行数语义不同）+ 合并列序对齐；增量因子 `_persist_factor_bundle` 单因子整窗回写（更新整窗 + meta digest） |
| 配置 | `l3_signal_store_append_window`（settings，默认 true；`FTS_L3_SIGNAL_APPEND_WINDOW=false` 回退现行为——`append_plan` 为空走 direct，行数不符降级全量） |
| 测试 | test_l3_signal_service +6（同源切片构造窗口推进：增量 vs 全量逐位一致 / rolling window=50 回退 / 前缀不一致降级 / 验证失败兜底 / 旧库无 digest 兼容 / shape_mismatch 改造）+ test_config_settings +1；26 + 400 affected passed + ruff/mypy 通过 |

> 测试数据修正说明：`_mk_panel(n_days)` 每次调用独立 re-seed，不同天数版本随机序列不相关，
> 不构成"同源窗口推进"——增量一致性测试改用单源超长面板 `iloc[:n]` 切片（前缀一致的前提）。

## 7. 12 项检查清单映射（实施时逐项核对）

| # | 检查项 | 对应文档 | 状态 |
|:--|:-----|:-----|:-----|
| 1 | 数据流/架构变更 | docs/harness/01-architecture.md（l3_signal_assets 增量窗口语义） | 实施时 |
| 2 | 阶段/产出物 | docs/harness/02-lifecycle.md | 实施时 |
| 3 | 新配置项 | docs/harness/03-configuration.md（l3_signal_store_append_window） | 实施时 |
| 4 | 降级/熔断路径 | docs/harness/04-resilience.md（对照验证不过/前缀不一致降级全量） | 实施时 |
| 5 | 新指标/日志 | docs/harness/05-observability.md（增量追加/全量重算计数日志） | 实施时 |
| 6 | 测试文件/用例数 | docs/harness/06-testing.md | 实施时 |
| 7 | 版本号/历史 | docs/harness/07-operations.md + pyproject.toml | 实施时 |
| 8 | 差距登记/关闭 | docs/harness/08-gap-analysis.md（GAP-139 关闭） | 实施时 |
| 9 | 晋级里程碑 | docs/harness/09-advancement-plan.md | 实施时 |
| 10 | 流程文档 | docs/production_plan.md | 实施时 |
| 11 | CLAUDE.md 职责 | CLAUDE.md | 实施时 |
| 12 | README 工程指标 | README.md | 实施时 |

## 8. 版本与归档

- 实施时：每批完成 build bump（日常开发，不触发里程碑 bump）；
- 计划关闭：验收全过 + GAP-139 关闭后归档。
