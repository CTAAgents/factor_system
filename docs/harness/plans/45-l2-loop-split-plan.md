# 45-l2-loop-split-plan.md — L2 循环拆分设计（三候选全覆盖）


> 版本: v3.0.0+5

> 状态: ✅ 完成（2026-08-16 v2.104.0+85 全量交付，按 ③→①→② 顺序实施，480 受影响测试全绿）
> 状态: 设计待评审（评审通过后按 ③→①→② 顺序实施）
> 日期: 2026-08-16
> 前置: 34-evolution-loop-refactor-inventory.md（B 阶段 9 Mixin 抽取 + C 阶段 9 协作类组合，已完成，`evolution_loop.py` 5117→1470 行）
> 修订: v4 2026-08-16 — 采纳用户调度架构 v4（种子评估晋升每日 02:00 / 工作日 L2 04:00 / 每日先种子评估再演化 / 周末大预算 ≈50 / 评审周日 10:00）

---

## 1. 背景与问题定义

L2 Evolution Loop 的**代码结构问题已解决**（34 计划 C 阶段：`EvolutionLoop` 组合持有 `UctSelector`/`CandidatePrefilter`/`EliteStore`/`AuditPipeline`/`TraceRecorder`/`FactorReviewer`/`EvolutionChannels`/`SeedManager`/`CandidateProcessor` 9 协作类），但**调度时序上的"大循环"问题仍在**：

`l2_evolution_loop_job`（当前 cron `0 0 * * 1-5`）单任务内串行承载 4 类时序上可解耦的工作（`evolution_loop.py run()` L533-862）：

| 阶段 | 协作类 | 职责 | 资源特征 | 行号 |
|---|---|---|---|---|
| ① 种子评估晋升 | SeedManager | Step 0 种子相关性预检 + Step 1 评估晋升（184 因子横截面） | 纯 CPU/本地计算，**最耗时** | L616-651 |
| ② 演化主循环 | EvolutionChannels / CandidateProcessor | 代循环（单因子 + batch 两路径） | LLM token + CPU 密集 | L679-809 |
| ③ 批量挖掘 | BatchMiner / CandidateProcessor | batch 模式一代批量漏斗 | CPU 密集（多 worker） | L707-724 |
| ④ 定期评审 | FactorReviewer | run() finally 精英重审/衰减自动退役 | 低频 | L855 |

### 拆分价值（不是"减行数"，而是三类解耦）

1. **时序解耦**：种子评估（纯 CPU）被卡在夜间等演化主循环，L1 注入的种子闲置超 24 小时。拆出后种子晋升结果当日即被演化消费。
2. **资源错峰**：batch 挖掘 CPU 密集（`executor_backend` thread/process/dask/ray 多 worker），与 LLM 演化抢资源；拆出后错峰到周末。
3. **失败隔离**：batch 失败当前会污染 `_consecutive_low_ic` 触发整夜熔断；定期评审失败在 finally 中会中断 run() 主流程。拆出后各候选失败互不影响演化。

### 范围（三个候选全覆盖，形态"两者都要"）

- 候选 ① 种子评估晋升 → 独立 `l2_seed_promotion_job`（**每日 02:00**，先种子后演化）
- 候选 ② 批量挖掘 → 独立 `l2_batch_mining_job`（**周末**）
- 候选 ③ 定期评审/退役 → 独立 `l2_review_job`（**每周日**），run() 不再每日调用

---

## 2. 目标调度架构（v4 修订版）

用户确认的调度基线：

| 任务 | 时点 | cron | 预算 | 说明 |
|---|---|---|---|---|
| L1 Meta-Loop | 每日 00:00 | `0 0 * * *` | — | 知识补给 + 种子注入（对齐 TRAE Schedule，作为目标基线） |
| 种子评估晋升 | 每日 02:00 | `0 2 * * *` | — | **每日先做**：L1 注入种子 + 种子池评估晋升（独立 job），晋升结果入 elite 池 |
| L2 演化（工作日） | 工作日 04:00 | `0 4 * * 1-5` | **max_generation≈10（小预算）** | 种子评估（02:00）完成后启动，父因子含当天刚晋升种子 |
| L2 演化（周末） | 周六 04:00 | `0 4 * * 6` | **max_generation≈50（大预算）** | 种子评估（02:00）完成后启动，周末集中大规模演化 |
| 批量挖掘 | 周日 06:00 | `0 6 * * 0` | — | 周末集中，CPU 密集错峰（独立 job） |
| 定期评审 | 周日 10:00 | `0 10 * * 0` | — | 独立周度 job，精英重审 + 衰减自动退役 |

> 时间槽设计依据：
> - **每日"先种子后演化"流水线**：L1(00:00 注入) → 种子评估晋升(02:00) → L2 演化(工作日 04:00 / 周六 04:00)，种子晋升结果当日即被演化消费为父因子，闲置时间压缩到 2 小时。
> - **工作日/周六 04:00**：避开 L1(00:00)、L3(06:00)、信号管道(20:00)、数据同步(17:30) 全部现有任务；种子评估 02:00 → 演化 04:00 时序成立。月度任务 `monthly_decay_eval_job`（每月 1 日 04:00）退役后无冲突（见 §5.3）。
> - **周日 06:00 批量 / 10:00 评审**：批量挖掘与周末演化完全错开（周六 vs 周日）；评审（10:00）独立于批量（06:00），互不阻塞。

### 关键变更点（相对现状）

1. **L1 时点基线**：`l1_meta_loop` 以每日 00:00 为目标基线（与 TRAE Schedule 对齐项一致）。
2. **L2 原 00:00 让出**：当前 `l2_evolution_loop` 工作日 00:00 → 改为工作日 04:00 小预算（≈10）+ 周六 04:00 大预算（≈50）两个入口。
3. **种子评估独立且每日执行**：每日 02:00 独立 job（先种子后演化），种子晋升结果当日入 elite 池。
4. **批量挖掘独立**：新增周日 06:00 job。
5. **评审周度化**：`monthly_decay_eval_job`（每月 1 日 04:00）职责并入周日 `l2_review_job`（10:00），月度任务退役或降级（见 §5.3）。

---

## 3. 现状契约盘点（改造前快照）

### 3.1 run() 依赖链

```
run(max_generation)
 ├─ _signal_cache.clear() / _experiment_variants.clear()      # 进程内残留清理
 ├─ 清理前日因子信号缓存 (memory/cache/factor_signals/*.npy)   # 缓存失效清理
 ├─ state = state_manager.load_or_init → mark_running → 重置计数器
 ├─ data_quality_monitor.validate_market_data()               # critical 熔断
 ├─ Step 0: seed_pool.load_all_seeds → _merge_l1_candidates → _run_seed_correlation_check
 ├─ Step 1: _evaluate_and_promote_seeds → elite_ids
 ├─ parent_seeds = [s for s in seeds if s["factor_id"] in elite_ids]   # ← ① 耦合断点
 ├─ 无合格父因子回退: _load_elite_parent_factors()             # ← 回退路径已存在
 ├─ for gen: 熔断检查 → UCT选父 → batch/_evolve_one → _process_candidate
 ├─ mark_completed → EvolutionRunResult
 └─ finally: _write_seed_correlation_index / _run_periodic_factor_review / _export_experiment_log
```

### 3.2 状态文件契约（state.json，L4 SQLite state.db）

`EvolutionState` 由 `EvolutionStateManager`（state.py）管理，字段：`run_id`/`last_generation`/`total_factors_evaluated`/`total_factors_promoted`/`tokens_consumed`/`evolution_method_counts`/`schema_version` 等。

**关键约束**：`state_manager.mark_running()` 重置计数器（`last_generation=0`/`total_*=0`/`tokens_consumed=0`）。种子评估若拆出独立任务，**不应重置演化计数器**——需新增只评估种子的状态访问路径（见 §5.1 状态处理）。

### 3.3 调度注册契约（tasks.py `TaskSpec`）

```python
@dataclass
class TaskSpec:
    name: str
    cron_expression: str          # 5 字段
    callable_path: str            # "fts.scheduler.jobs.xxx_job"
    description: str = ""
    enabled: bool = True
    trace_id_prefix: str = ""
```

`register_default_tasks()` 现有任务（L87-）：l1_meta_loop（`59 7 * * 1-5`）、l2_evolution_loop（`0 0 * * 1-5`）、l3_portfolio_loop（`0 6 * * 1-5`）、futures_signal_pipeline（`0 20 * * 1-5`）、sync_futures_data（`30 17 * * 1-5`）、health_check、monthly_decay_eval（`0 4 1 * *`）、data_quality_eval、logic_monitor、factor_inspector、data_level_monitor 等。

### 3.4 相关测试（受影响面）

- `tests/factor_engine/test_evolution_loop.py`（19 slow + 大量普通用例，直接 import 私有符号）
- `tests/factor_engine/test_evolution_l1_merge.py`（L1 候选合并）
- `tests/factor_engine/test_microstructure_promotion.py`（种子晋升路径）
- `tests/factor_engine/test_l2_elite_redundancy.py` / `test_l2_orthogonalize.py` / `test_structure_cluster_quota.py` / `test_orthogonal_basis.py`（EliteStore 晋升链）
- `tests/factor_engine/test_batch_mining.py`（batch 漏斗）
- `tests/factor_engine/test_experiment_log.py` / `test_success_pattern.py`（TraceRecorder）
- `tests/scheduler/`（任务注册表，若有）

---

## 4. 目标与验收

### 4.1 目标

- **组件化（阶段一）**：`run()` 拆分出 3 个可独立调用入口（`run_seed_stage` / `run_batch_stage` / `run_review_stage`），行为等价、调度不变，全部测试全绿。
- **迁出调度（阶段二）**：新增 3 个独立 job（种子每日 02:00 / 批量周日 / 评审周日）；L2 演化拆工作日小预算 + 周末大预算两入口；run() 删除种子段、batch 段、评审段。
- **公开 API 不变**：`EvolutionLoop`/`EvolutionRunResult`/`_add_trading_days`/`_build_shadow_pool`/`_QualityInspectionResult`/`main` 及全部 `_*` 实例方法签名不变（新入口为**追加**，不删除）。

### 4.2 验收口径

| 项 | 验收标准 |
|---|---|
| 组件化等价 | 拆分前后 `run()` 行为等价（种子晋升数/elite_ids/演化结果一致），受影响测试全绿（`pytest tests/factor_engine/ -m "not slow"`） |
| 预算分层 | 工作日 L2 max_generation≈10；周末 L2 max_generation≈50（预算经配置注入，不硬编码） |
| 流水线时序 | 每日链路成立：L1(00:00 注入) → 种子评估(02:00 晋升) → 工作日 L2(04:00 小预算 ≈10)；周末链路：周六 04:00 大预算 ≈50，周日批量/评审错峰 |
| 失败隔离 | 种子/batch/评审独立任务失败均不触发 L2 熔断（`_consecutive_low_ic` 不再被 batch 污染） |
| 评审周度化 | `_run_periodic_factor_review` 每日调用删除，周日 10:00 `l2_review_job` 独立覆盖精英重审 + 衰减自动退役 + 数据质量监控 + LogicMonitor + 状态报告 |
| 月度任务退役 | `monthly_decay_eval_job` 职责并入周度任务后退役（保留任务壳或删除，见 §5.3） |
| trace 链路 | 新 job 各自 `generate_trace_id`（`l2_seed`/`l2_batch`/`l2_review` 前缀），与 L2 主 run_id 关联可查 |

---

## 5. 候选设计

### 5.1 候选 ① 种子评估晋升（最高优先级）

**组件化**：从 `run()` 抽出 `run_seed_stage(seeds, trace_id, state, elite_ids, seed_correlations) -> tuple[int, list[str], list[FactorProgram]]`，内部复用 `SeedManager._evaluate_and_promote_seeds` + `_merge_l1_candidates` + `_run_seed_correlation_check`（均为协作类现有方法）。主类新增一行转发桩。

**关键耦合断点**：`parent_seeds = [s for s in seeds if s["factor_id"] in elite_ids]`（L653）→ 演化主循环父因子来源改为**直接读 elite 池**（`_load_elite_parent_factors()`，L657 已有回退路径，天然兼容）。工作日/周末演化均不内置种子段。

**状态处理**：种子评估独立任务**不调用 `mark_running()`**（避免重置演化计数器）。新增只读状态访问（`state_manager.load_or_init` 后仅读取，不重置）；或种子任务使用独立状态键（`state_kv` 分域，L4 SQLite WAL 已支持多读单写）。种子(每日 02:00) 与演化(04:00) 不同时跑，无并发写冲突。

**调度**：新增 `l2_seed_promotion_job()`（jobs.py）→ 注册 `l2_seed_promotion`（tasks.py，`0 2 * * *` 每日 02:00，trace_id_prefix=`fts.l2_seed`）。

**run() 改造**：工作日/周末演化 run() 删除 Step 0/Step 1 种子段；父因子直接 `_load_elite_parent_factors()`（含当日 02:00 刚晋升种子）。若 elite 池为空（首夜无种子）→ 保持现状回退语义（无父因子跳过演化）。

### 5.2 候选 ② 批量挖掘（周末错峰）

**组件化**：从 `_run_batch_generation` 抽出 `run_batch_stage(parent, generation, trace_id, state, elite_ids, seed_correlations)`，内部复用 `BatchMiner` + `ExecutorBackend` + `_process_candidate`（CandidateProcessor）。

**关键耦合断点**：
- 父因子来源：独立任务自行 `_select_parent_uct(parent_seeds)` 或读 elite 池选父（不再依赖演化代循环的 UCT 状态）；
- 熔断隔离：batch 任务的失败**只记录独立状态/告警**，不再写 `_consecutive_low_ic`（当前 batch 路径 `_process_candidate` 失败会污染该计数 → 需在 batch 独立入口注入独立的熔断计数或置空）。

**调度**：新增 `l2_batch_mining_job()` → 注册 `l2_batch_mining`（tasks.py，`0 6 * * 0` 周日 06:00，trace_id_prefix=`fts.l2_batch`）。

**run() 改造**：删除 batch 分支（L707-724）对 `_run_batch_generation` 的调用；`evolution_mode="batch"` 配置语义迁移到独立任务（配置项保留，独立任务读取）。

### 5.3 候选 ③ 定期评审（周度化 + 月度任务退役）

**周度化**：新增 `l2_review_job()`（jobs.py）→ 注册 `l2_review`（tasks.py，`0 10 * * 0` 周日 10:00，trace_id_prefix=`fts.l2_review`），承担 `FactorReviewer._run_periodic_factor_review` 全部职责：精英重审（衰减自动退役 GAP-I305）+ 数据质量监控 + LogicMonitor 集成 + 状态报告。

**月度任务退役**：现有 `monthly_decay_eval_job`（每月 1 日 04:00）的"新标准全量重审 + 月度衰减 + 自动淘汰"职责并入周度 `l2_review`。退役策略：保留 `monthly_decay_eval_job` 任务壳但 disabled，或删除注册（实施时确认——若保留壳，需在 tasks.py 置 `enabled=False` 并注释原因，避免误触）。

**run() 改造**：删除 L854-855 finally 中的每日 `_run_periodic_factor_review` 调用；`_export_experiment_log` 保留在 finally（非评审职责）。

---

## 6. 实施顺序（③→①→②）与每步验证

| 步骤 | 候选 | 动作 | 验证方式 |
|---|---|---|---|
| 45.1 | ③ 评审周度化 | 新增 `l2_review_job` + tasks.py 注册 `0 10 * * 0` + run() 删除每日调用 + 月度任务退役 | `pytest tests/factor_engine/ -k "evolution or review or decay" -m "not slow"` |
| 45.2 | ① 种子组件化 | run() 抽出 `run_seed_stage` + 父因子改读 elite 池 | 等价性对比（种子晋升数/elite_ids）+ `test_evolution_l1_merge`/`test_microstructure_promotion` 全绿 |
| 45.3 | ① 种子调度 | 新增 `l2_seed_promotion_job` + tasks.py 注册 `0 2 * * *` + run() 删除种子段 | 调度注册测试 + 手动触发 job 验证每日链路 |
| 45.4 | ② 批量组件化 | 抽出 `run_batch_stage` + 熔断隔离 | `test_batch_mining` 全绿 + batch 失败不污染 `_consecutive_low_ic` |
| 45.5 | ② 批量调度 | 新增 `l2_batch_mining_job` + tasks.py 注册 `0 6 * * 0` + run() 删除 batch 分支 | 调度注册测试 + 手动触发 job |
| 45.6 | 调度基线落地 | L1 cron 对齐每日 00:00；L2 演化拆工作日 `0 4 * * 1-5`（≈10）+ 周六 `0 4 * * 6`（≈50）；TRAE Schedule 注释同步 | 任务注册表测试 + 手动触发各 job 验证时间槽无冲突 |

每步独立 bump（`python scripts/bump_version.py --build --message "..."`）+ 文档同步（07-operations.md 版本历史 / 06-testing.md 用例数 / 01-architecture.md L2 架构图 + 调度表 / 02-lifecycle.md / tasks.py 注释）。

---

## 7. 风险与约束

1. **公开 API 不变**：`EvolutionLoop` 25+ 测试文件直接引用私有符号；新入口为追加，不删除任何现有方法签名。
2. **父因子来源变更**（种子晋升 → elite 池）可能改变演化路径 → 45.2 必须做拆前拆后等价性对比，记录差异。
3. **L1 时点基线**：L1 以每日 00:00 为目标基线；需确认 TRAE Schedule 期货 L1 对齐项一致，避免双调度冲突。
4. **预算分层不硬编码**：工作日 ≈10 / 周末 ≈50 的 max_generation 经配置注入（budget 或 FTSConfig），禁止硬编码。
5. **状态文件并发**：各任务时点错开（00:00/02:00/04:00/周日06:00/10:00）无并发写；但需确保种子任务不重置演化计数器（见 §5.1）。
6. **batch 熔断隔离**：独立 batch 任务须有独立的失败状态/告警，避免污染 L2 的 `_consecutive_low_ic`。
7. **slow 测试**：`test_evolution_loop.py` 19 个 slow 测试，每步后 `-k` 定向抽验 + 里程碑全量验收。
8. **模块级 monkeypatch 依赖**：测试用 `from fts.factor_engine import evolution_loop as evolution_loop_mod` 模块级补丁，迁移后原模块保留转发符号。
9. **循环导入**：新 job 函数引用 evolution_loop 组件时使用延迟导入（`from fts.factor_engine.evolution_loop import ...` 函数体内），与现有 jobs.py 风格一致。

---

## 8. 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| run() 阶段划分 | §1 表中 4 阶段行号 = `evolution_loop.py run()` 实际行号 | `grep -n "def run" evolution_loop.py` 对照 |
| 调度基线 | §2 表 cron 与 §6 步骤 = `tasks.py register_default_tasks()` 实施后实际值 | `grep -n "cron_expression" tasks.py` 对照 |
| 预算分层 | 工作日 ≈10 / 周末 ≈50 max_generation 经配置注入（非硬编码） | `grep -n "max_generation" evolution_loop.py jobs.py` 对照 |
| 耦合断点 | L653 `parent_seeds` 过滤 + L657 `_load_elite_parent_factors` 回退 | 45.3 后 `grep -n "parent_seeds"` 验证断点消除 |
| 评审职责归属 | 45.1 后 run() 无 `_run_periodic_factor_review` 调用，周度任务覆盖 | `grep -n "periodic_factor_review" evolution_loop.py` 仅转发桩残留 |

---

## 9. 实施后预期效果

- 每日链路：L1(00:00 注入) → 种子评估晋升(02:00) → 演化（工作日 04:00 小预算 ≈10 / 周六 04:00 大预算 ≈50），种子晋升结果当日即被消费为父因子，闲置仅 2 小时。
- 批量挖掘（周日 06:00）与评审（周日 10:00）独立错峰，CPU 密集与 LLM 演化互不抢资源。
- 种子晋升提前完成且失败不拖垮演化；batch 失败独立告警，不再熔断 L2。
- 每日评审算力释放（低频职责归周度），run() finally 只保留实验日志导出。
