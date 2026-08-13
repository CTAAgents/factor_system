# 34-evolution-loop-refactor-inventory.md — evolution_loop.py 职责盘点与属性耦合清单


> 版本: v2.103.0+9

> 状态: Phase-0 盘点（B 阶段 Mixin 抽取 / C 阶段组合式重构的前置证据）
> 日期: 2026-08-13
> 分析对象: `fts/factor_engine/evolution_loop.py`（5117 行）
> 分析工具: `scripts/analyze_evolution_loop.py`（AST 静态分析，可复现）

---

## 1. 背景与目标

`EvolutionLoop` 是 L2 因子演化主循环，单文件 5117 行、单类约 62 个方法，属典型 God Class。
本次盘点产出两份基础证据，支撑后续重构决策：

1. **职责盘点表**：每个方法的职责分类（内联逻辑 / 编排 / 薄包装 / 纯函数依赖），并映射到领域分组；
2. **属性读点清单**：每个 `self` 实例属性的读/写方法集合，标注"跨领域共享状态"与"领域独享状态"。

**判定口径**（本盘点采用）：
- `self.<attr>` 引用若命中类方法名 → 记为方法调用，不计入属性读点；
- 薄包装 = 方法体主要委托 `__init__` 中装配的组件对象（读属性 ≤ 2）；
- 纯函数依赖 = 读 0 个实例属性（可平移为模块级函数）；
- 领域独享状态 = 该属性全部读写方法落在同一领域内（C 阶段可随组件搬走）。

## 2. 文件总览指标

| 指标 | 数值 |
|---|---|
| 总行数 | 5117 |
| 顶级定义数 | 6（`_add_trading_days`/`_build_shadow_pool`/`_log_consistency_event` 3 个模块函数 + `EvolutionRunResult`/`_QualityInspectionResult`/`_QualityInspectionCompat` 3 个类） |
| EvolutionLoop 类体 | L283–L5056，约 4774 行，62 个方法（含 `__init__`） |
| 巨方法（>280 行） | 5 个：`_promote_to_elite`(386)、`_process_candidate`(373)、`run`(330)、`__init__`(313)、`_evolve_one`(285) |
| 100+ 行方法 | 共 15 个，合计约 3400 行（占类体 71%） |
| 编排中枢（self 调用 ≥6） | 6 个：`run`(18)、`_process_candidate`(21)、`_evaluate_and_promote_seeds`(16)、`_evolve_one`(6)、`_run_batch_generation`(6)、`_promote_to_elite`(6) |
| 外部模块委托 | 仅 3 处 `state` 引用（`run`/`_check_circuit_breaker`/`_maybe_early_stop`）；其余依赖经 `self.<组件>` 装配 |

**结论**：不是"无结构"，而是"编排器职责过载"——多数业务能力已下沉到 `factor_engine/` 下独立模块（`audit.py`/`backtest_pipeline.py`/`shap_analyzer.py` 等），本文件以 `__init__` 装配 + 编排方法串联为主，但编排逻辑与状态大量内联在巨方法中，改动互相牵制。

## 3. 方法职责分类总表（按领域分组）

### 领域 A：主循环编排（保留在 evolution_loop.py）

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `run` | 330 | 编排（主循环） | data/budget/state_manager/seed_pool/_signal_cache/... | 18 |
| `_evolve_one` | 285 | 编排（演化分派） | evolution_mode/macro_evolver | 6 |
| `_run_batch_generation` | 116 | 编排（batch 批量） | batch_*/state_manager | 6 |
| `_batch_generate_one` | 45 | 编排（batch 单候选） | _batch_idx/batch_random_seed | 1 |
| `_batch_prefilter` | 7 | 薄包装 | — | 1 |

### 领域 B：候选准入管线

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `_process_candidate` | 373 | **内联逻辑（准入链 Step 2–6）** | data/forward_returns/budget/verifier/quality_inspector/evaluation_chain/state_manager/_signal_cache/_prior_evaluations/_consecutive_low_ic/... | 21 |

### 领域 C：精英晋升与持久化

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `_promote_to_elite` | 386 | **内联逻辑（去重/配额/相关性/正交化/晋升）** | elite_dir/market/budget/_repo/_cluster_*/_l2_*/elite_tracker/high_ic_screener/inject_dir | 6 |
| `_write_to_duckdb` | 123 | 内联（持久化） | market | 1 |
| `_scan_elite_correlations` | 81 | 内联（elite 相关性） | data/elite_dir | 0 |
| `_orthogonalize_candidate` | 90 | 内联（OLS 正交化） | data/elite_dir/_l2_orthogonal_* | 0 |
| `_orthogonalize_via_basis` | 73 | 薄包装（OrthogonalBasisManager） | data/elite_dir/orthogonal_basis/_l2_orthogonal_basis_enabled | 0 |
| `_check_elite_correlation` | 25 | 内联 | _l2_elite_corr_* | 1 |
| `_count_cluster_members` | 20 | 内联 | _cluster_corr_threshold/_cluster_max_scan | 1 |
| `_load_elite_parent_factors` | 20 | 薄包装（读 elite_dir JSON） | elite_dir | 0 |
| `_write_seed_correlation_index` | 34 | 内联 | elite_dir | 0 |
| `_release_repo_after` | 17 | 工具（装饰器） | _repo | 0 |
| `_get_repo` | 7 | 工具（懒加载仓储） | _repo/factor_db_path/market | 0 |

### 领域 D：种子管理与横截面评估

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `_evaluate_and_promote_seeds` | 225 | 编排（种子评估晋升） | data/forward_returns/verifier/quality_inspector/evaluation_chain/_is_cross_section | 16 |
| `_merge_l1_candidates` | 168 | 内联（L1 候选合并/GAP-031） | inject_dir/market | 0 |
| `run_microstructure_promotion` | 65 | 编排 | — | 2 |
| `_run_seed_correlation_check` | 49 | 内联 | data/cross_section_data/cross_section_dates/_is_cross_section | 0 |
| `_evaluate_cross_section` | 41 | 薄包装（横截面评估） | cross_section_data/cross_section_dates/cap_map/industry_map | 1 |
| `_build_barra_exposures` | 40 | 内联（Barra 暴露缓存） | _barra_exposures_cache/cross_section_*/_is_cross_section | 0 |

### 领域 E：审计与验证管线

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `_run_factor_audit` | 114 | 薄包装（FactorAuditor）+ 内联 OOS 构造 | auditor/data/forward_returns | 1 |
| `_run_walkforward_oos` | 79 | 内联（独立走航） | data | 1 |
| `_run_backtest_pipeline` | 51 | 薄包装（BacktestPipeline） | backtest_pipeline/data | 0 |
| `_run_ablation_check` | 43 | 薄包装（AblationExperiment） | ablation_experiment/_signal_cache | 1 |
| `_run_robustness_check` | 39 | 薄包装（RobustnessTester） | robustness_tester/_signal_cache | 0 |
| `_run_causal_validation` | 34 | 薄包装（CausalValidator） | causal_validator | 0 |
| `_run_shap_analysis` | 32 | 薄包装（ShapAnalyzer） | shap_analyzer/_signal_cache | 0 |
| `_build_wf_config` | 28 | **纯函数** | — | 0 |
| `_is_blocking_ablation` | 13 | **纯函数** | — | 0 |

### 领域 F：定期评审与数据质量

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `_run_periodic_factor_review` | 123 | 编排（衰减自动退役） | data/elite_dir/elite_tracker/feedback_loop/logic_monitor/_decay_auto_retire_enabled | 1 |
| `_register_factor_baseline` | 23 | 薄包装（DataQualityMonitor） | data_quality_monitor | 0 |
| `_check_factor_data_quality` | 28 | 薄包装（DataQualityMonitor） | data_quality_monitor | 0 |
| `_get_factor_data_for_review` | 21 | 薄包装（读 review 数据） | verifier | 0 |

### 领域 G：演化通道（GP / Deep / 算子）

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `_generate_operator_factor` | 154 | 内联（算子演化 DSL） | data/cross_section_data/market/_is_cross_section | 1 |
| `_try_operator_engine_evolution` | 93 | 内联（算子引擎路由） | data/forward_returns/market/_is_cross_section | 0 |
| `_run_gp_evolution` | 89 | 内联（GP 特征演化） | data/forward_returns/market/feature_ops_engine/feature_importance_analyzer | 0 |
| `_run_deep_evolution` | 60 | 内联（GRU/Transformer） | data/forward_returns/market | 0 |

### 领域 H：候选预筛

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `_quick_prefilter` | 77 | 内联（快速预筛） | data/forward_returns/market/_is_cross_section | 1 |
| `_cross_section_prefilter` | 64 | 内联（横截面预筛） | cross_section_data/cross_section_dates/market | 0 |
| `_check_factor_runtime` | 41 | 内联（运行时校验） | data/cross_section_data/_is_cross_section | 0 |

### 领域 I：UCT 选择与熔断停止

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `_select_parent_uct` | 26 | 内联（UCT 树搜索） | _uct_stats | 0 |
| `_update_uct_stats` | 13 | 内联 | _uct_stats | 0 |
| `_update_uct_failure` | 11 | 内联 | _uct_stats | 0 |
| `_check_circuit_breaker` | 23 | 内联（熔断） | _consecutive_low_ic/budget | 0 |
| `_maybe_early_stop` | 30 | 内联（提前停止） | _evolution_stop_*/_consecutive_empty_generations/_early_stop_* | 0 |

### 领域 J：trace 记录与经验链

| 方法 | 行 | 分类 | 读属性 | self 调用 |
|---|---|---|---|---|
| `_build_success_pattern_report` | 29 | 内联（缓存读写） | experience_chain/_success_pattern_cache | 0 |
| `_build_parent_failure_ctx` | 37 | 薄包装 | experience_chain | 0 |
| `_record_experiment_variant` | 37 | 内联（实验日志聚合） | _experiment_variants | 0 |
| `_record_success_trace` | 25 | 薄包装 | experience_chain/state_manager | 0 |
| `_record_failure_trace` | 41 | 薄包装 | experience_chain | 0 |
| `_record_quality_filtered_trace` | 40 | 薄包装 | experience_chain | 0 |
| `_record_audit_failed_trace` | 46 | 薄包装 | memory_dir | 0 |
| `_record_ablation_failed_trace` | 34 | 薄包装 | memory_dir | 0 |
| `_record_robustness_failed_trace` | 34 | 薄包装 | memory_dir | 0 |
| `_record_causal_failed_trace` | 34 | 薄包装 | memory_dir | 0 |
| `_log_inspection_detail` | 39 | **纯函数** | — | 0 |
| `_export_experiment_log` | 20 | 薄包装 | _experiment_log_dir/_experiment_variants/market | 0 |

### 领域 K：装配

| 方法 | 行 | 分类 |
|---|---|---|
| `__init__` | 313 | **内联装配**（写入 76 个实例属性，含配置回退双份逻辑） |

## 4. 属性读点清单

### 4.1 跨领域共享状态（B 阶段保留在主类，C 阶段须显式注入）

| 属性 | 读方法数 | 写方法数 | 读方法（领域） | 备注 |
|---|---|---|---|---|
| `data` | 17 | 1 | A/B/C/D/E/F/G/H 全域 | 全局行情上下文 |
| `market` | 11 | 1 | A/C/D/E/F/G/H | 全局市场上下文 |
| `_is_cross_section` | 9 | 1 | B/D/G/H | 全局模式开关 |
| `forward_returns` | 8 | 1 | A/B/D/E/G/H | 全局标签 |
| `cross_section_data` | 8 | 1 | B/D/G/H | 横截面上下文 |
| `experience_chain` | 7 | 1 | A/J | 经验链（trace 域+主循环） |
| `budget` | 5 | 1 | A/B/C/I | 预算配置 |
| `memory_dir` | 5 | 1 | A/J | trace 落盘目录 |
| `_signal_cache` | 5 | 1 | B/E | 质检信号缓存（B/E 共享） |
| `data_quality_monitor` | 4 | 1 | A/F | 数据质量监控 |
| `state_manager` | 4 | 1 | A/B/J | 状态持久化 |
| `cross_section_dates` | 4 | 1 | D/G/H | 横截面日期 |
| `_consecutive_low_ic` | 3 | 2 | A/B/I（写: __init__/_process_candidate；读: run/_check_circuit_breaker/_process_candidate） | 熔断共享状态 |
| `_experiment_variants` | 3 | 1 | A/J | run 聚合 + trace 写入 |
| `verifier` | 3 | 1 | B/D/F | Verifier 组件 |
| `_uct_stats` | 3 | 1 | I 域独享（_select_parent_uct/_update_uct_stats/_update_uct_failure） | **可随 I 域搬走** |
| `_repo` | 3 | 4 | C 域独享（_get_repo/_promote_to_elite/_release_repo_after） | **可随 C 域搬走** |

### 4.2 领域独享状态（B 阶段随 Mixin 整体搬迁）

| 领域 | 属性 | 读写分布 |
|---|---|---|
| C（晋升/持久化） | `_repo`、`_cluster_quota_enabled`、`_cluster_max`、`_cluster_corr_threshold`、`_cluster_max_scan`、`_l2_elite_corr_threshold`、`_l2_elite_corr_max_scan`、`_l2_elite_corr_debug`、`_l2_elite_orthogonalize`、`_l2_orthogonal_residual_corr_max`、`_l2_orthogonal_min_retained_ratio`、`_l2_orthogonal_basis_enabled`、`_l2_orthogonal_basis_max_size`、`_l2_orthogonal_basis_min_sharpe`、`elite_tracker`、`high_ic_screener` | 全部读写限于 C 域（`__init__` 装配除外） |
| D（种子/横截面） | `_barra_exposures_cache`、`_barra_exposures_attempted`、`cap_map`、`industry_map`、`orthogonal_basis`、`seed_pool` | 读写限 D 域 + `run` 读 `seed_pool` |
| E（审计/验证） | `auditor`、`backtest_pipeline`、`ablation_experiment`、`robustness_tester`、`shap_analyzer`、`causal_validator`、`feature_importance_analyzer`、`feature_ops_engine` | 组件对象，读写限 E 域 |
| G（演化通道） | `feature_ops_engine`、`feature_importance_analyzer`、`macro_evolver` | 组件对象，读写限 G 域 + `_evolve_one` 读 `macro_evolver` |
| I（UCT/熔断） | `_uct_stats`、`_evolution_stop_enabled`、`_evolution_stop_k`、`_consecutive_empty_generations`、`_early_stop_last_count`、`_early_stop_reason` | 读写限 I 域 + `run` 写 `_consecutive_empty_generations/_early_stop_*` |
| J（trace） | `_success_pattern_cache`、`_experiment_log_dir`、`_experiment_variants` | 读写限 J 域 + `run` 读 `_experiment_variants` |
| A（batch） | `batch_size`、`batch_max_candidates`、`batch_max_workers`、`batch_random_seed`、`_batch_idx` | 读写限 A 域（batch 子流程） |
| B（候选管线） | `_prior_evaluations`、`n_trials_micro`、`_micro_staged_evolution`、`_micro_coarse_trials`、`_micro_coarse_ic_floor` | 读写限 B 域 |

### 4.3 纯函数候选（可直接平移为模块级函数，0 依赖）

`_build_wf_config`(28)、`_is_blocking_ablation`(13)、`_log_inspection_detail`(39)

## 5. B 阶段 Mixin 抽取分组建议

保持 `from fts.factor_engine.evolution_loop import EvolutionLoop` 公开 API 不变，按领域抽 Mixin（继承组合），执行顺序按耦合度从低到高：

| 顺序 | Mixin 文件 | 领域 | 预计行数 | 迁移方法数 | 迁移前置条件 |
|---|---|---|---|---|---|
| 1 | `evolution_uct.py` | I | ~105 | 5 | ✅ **Phase 46a 已交付（2026-08-13，v2.103.0+4）**：`_select_parent_uct`/`_update_uct_stats`/`_update_uct_failure`/`_check_circuit_breaker`/`_maybe_early_stop` 迁移完成，`_uct_stats` 等 6 属性随迁；受影响测试 22+244 passed 全绿；UCT_EXPLORATION_C 单一事实源迁至 evolution_uct.py（evolution_loop re-export） |
| 2 | `evolution_trace.py` | J | ~420 | 12 | OK Phase 46b 已交付（2026-08-13，v2.103.0+6）：12 方法 + _QualityInspectionResult 数据类迁移完成；_success_pattern_cache/_experiment_log_dir/_experiment_variants 随迁（类型声明于 mixin，装配于主类 __init__）；run 的 _experiment_variants.clear() 经 mixin 属性声明兼容保留；受影响测试全绿 |
| 3 | `evolution_channels.py` | G | ~400 | 4 | OK Phase 46c 已交付（2026-08-13，v2.103.0+7）：4 方法迁移完成；feature_ops_engine/feature_importance_analyzer 组件装配于主类（mixin 类型声明）；受影响测试全绿 |
| 4 | `evolution_seeds.py` | D | ~590 | 6 | `_evaluate_and_promote_seeds` 跨 B/E/C/J 调用，先迁移其调用的方法或保留 self 派发 |
| 5 | `evolution_audit.py` | E | ~430 | 9 | `_signal_cache` 与 B 域共享，抽取时须保留共享引用 |
| 6 | `evolution_review.py` | F | ~200 | 4 | `data_quality_monitor` 与 A 域共享 |
| 7 | `evolution_prefilter.py` | H | ~180 | 3 | 无特殊（读全局上下文） |
| 8 | `evolution_promote.py` | C | ~880 | 11 | `_repo`/`_cluster_*`/`_l2_*` 随迁；`__init__` 装配段同步拆分 |
| 9 | `evolution_candidate.py` | B | ~375 | 1 | `_process_candidate` 读 14 属性/调 21 方法，最后迁移；须先完成 1–8 使依赖方法就位 |

> 注：`__init__`(313 行) 的配置装配段在任意领域迁移时同步拆出（随各 Mixin 提供各自 `_load_config` 或注入工厂），最终 `__init__` 仅保留 76 个属性中真正全局的部分（~20 个）。

**完成后**：`evolution_loop.py` 预计降至 **~1000 行**（类外辅助 + `__init__` 精简 + `run`/`_evolve_one` 编排核心 + 类外小类）。

## 6. C 阶段组件化映射提示（组合式重构）

B 阶段完成后，每个 Mixin 天然对应 C 阶段的一个协作类：

| C 阶段组件 | 来源 Mixin | 内部状态 | 对外接口 |
|---|---|---|---|
| `UctSelector` | evolution_uct | `_uct_stats` | select/update_stats/update_failure/check_circuit_breaker |
| `TraceRecorder` | evolution_trace | `_success_pattern_cache`/`_experiment_variants` | record_*_trace/export_log |
| `EvolutionChannels` | evolution_channels | `macro_evolver` 等 | evolve(parent, method_hint) |
| `SeedManager` | evolution_seeds | `_barra_exposures_cache`/`industry_map`/`cap_map` | evaluate_and_promote/merge_l1/run_corr_check |
| `AuditPipeline` | evolution_audit | 各 tester/analyzer 组件 | run_full_audit(factor, evaluation) |
| `EliteStore` | evolution_promote | `_repo`/`_cluster_*`/`_l2_*` | promote/write_duckdb/load_elite |
| `CandidateProcessor` | evolution_candidate | `_prior_evaluations`/`_signal_cache` | process(factor, parent, ...) |

**C 阶段关键约束**：全局上下文（`data`/`market`/`forward_returns`/`cross_section_*`/`budget`）经构造注入；`_signal_cache` 与 `_consecutive_low_ic` 为跨组件共享状态，须显式设计所有权（建议：`_signal_cache` 归 `AuditPipeline`，`_consecutive_low_ic` 归主循环持有并在各回调传入）。

## 7. 风险与约束

1. **公开 API 不变**：`EvolutionLoop`/`EvolutionRunResult`/`UCT_EXPLORATION_C`/`_add_trading_days`/`_build_shadow_pool`/`_QualityInspectionResult`/`main` 被 25+ 测试文件引用（含 `test_evolution_loop.py` 直接 import 私有符号）。
2. **19 个 slow 测试**集中在 `test_evolution_loop.py`，每步迁移后须定向复核（`-k` 过滤 + slow 抽验）。
3. **模块级 monkeypatch 依赖**：测试用 `from fts.factor_engine import evolution_loop as evolution_loop_mod` 做模块级补丁（如 `_add_trading_days`），迁移后原模块须保留转发符号。
4. **Mixin 方法名全局唯一**，避免多继承 MRO 冲突。
5. **循环导入**：迁移方法引用的模块级常量（`_QC_SIGNAL_CACHE_MAX_ENTRIES`、`DEFAULT_BUDGET_CONFIG`、`UCT_EXPLORATION_C` 等）应下沉 `evolution_constants.py` 或保持单向依赖。

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| 方法清单与行号 | 文档 §3 与 `scripts/analyze_evolution_loop.py` 输出一致 | `python scripts/analyze_evolution_loop.py`（JSON 对比行号/行数） |
| 属性读写分布 | 文档 §4 属性集合 = 脚本 `attr_readers`/`attr_writers` 全集 | 同上（JSON 对比） |
| 领域分组 | 文档 §3 分组 = 文件内注释 `# ── 领域 X ──` 标注（抽取时逐块落注释） | 抽取后 `grep -c "领域"` 对照 |
| 纯函数候选 | `_build_wf_config`/`_is_blocking_ablation`/`_log_inspection_detail` 读 0 实例属性 | 脚本输出 `reads==[]` 校验 |