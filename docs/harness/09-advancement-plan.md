# FTS 晋级计划

> 版本: v2.104.0+115
> 最后更新: 2026-08-17
> 状态: 活跃 — 随项目迭代持续更新

> **版本策略（v2.103.0 修订）**：版本号 = 里程碑版本 + build 段（SemVer build 段制）。日常开发（GAP 实现、测试、文档）通过 `python scripts/bump_version.py --build --message "..."` bump build 段（如 2.103.0 → 2.103.0+1，不限次）；满足发布条件（晋级里程碑完成 + 全量回归通过）时通过 `--type patch|minor|major --message "..."` bump 正式版本（build 清零，单日限一次）。详见 [07-operations.md §6 版本升级流程](07-operations.md)。

---

## 1. 晋级总览

```
v0.1.0 ───→ v0.2.0 ───→ v0.3.0 ───→ v1.1.0 ───→ v1.2.0 ───→ v1.5.0 ───→ v1.6.0 ───→ v1.7.0 ───→ v1.8.0 ───→ v1.8.1 ───→ v1.9.0 ───→ v1.10.0 ───→ v1.11.0 ───→ v2.0.0 (当前)
    │          │          │          │          │          │          │          │          │          │          │          │          │
    ├ Phase    ├ CLI 真实  ├ Data-Core ├ MCP 迁移  ├ 种子因子  ├ 期货数据  ├ 期货自治循环├ 策略进化  ├ 信号管道v5├ 演化优化  ├ Phase A  ├ Phase B  ├ Phase C
    │ 1-7 完成 │ 调用      │ 集成      │ (akshare) │ 集成      │ 接入      │ L1/L2/L3 调度├ 动态因子权重├ 多空双向排名├ UCT 父因子├ 消融实验  ├ SHAP 分析  ├ 因果结构审查
    ├ 220 测试 ├ Scheduler ├ FDT 清除  ├ 移除期货  ├ 熔断修复  ├ DuckDB +  ├ 期货基本面  ├ 市场制度  ├ 信号增量  ├ 失败模式  ├ 场景测试  ├ 鲁棒性审查  ├ 持续监控仪表盘
    │ 全绿     │ 引擎      │           │ 种子      │           │ AKShare   │ 数据接入    ├ 自适应     ├ 信号快照   ├ 聚类       ├ 风险标签  ├ 34 新测试  ├ 40 新测试
    └ 71% 覆盖 ├ 89% 覆盖  ├ 原子持久化 ├ 1231 测试 ├ 纯多头    ├ 82 期货   ├ 信号管道    ├ 1601 测试 ├ 1700 测试 ├ 1750 测试
               └ 778 全绿  ├ 96% 覆盖  ├ 99% 覆盖  │ 回测      │ 品种      │ 定时任务    └ 全绿      └ 全绿      └ 全绿
                           └ 969 全绿  └ 1231 全绿 ├ 1325 测试 └ 1502 测试 └ 12 家族 50+
                                                     └ 99% 覆盖  └ 99% 覆盖  子因子
```

**详细路线图见 [plans/production_plan.md](plans/production_plan.md)**，本文档仅记录已完成的里程碑。

---

## 2. 已完成里程碑

### Regime 分层方向 Gate 与品种暴露缩放（2026-08-17，build bump v2.104.0+111，GAP-136，plans/48）

**完成时间**: 2026-08-17

**核心产出**:
- ✅ **A 子链方向 Gate**：新增 `fts/factor_engine/regime_gate.py`——`build_subchain_gates`（bull/bear 且 conf≥min_confidence(0.55)→long/short、conf 不足→avoid、oscillate 等→neutral、盲测池默认回避）+ `apply_subchain_gate`（hard 剔除/soft 降权 0.3/long·short 方向过滤）；信号管线 `main()` Step 3h1 接入（仅 `--chain energy` 且 Gate 开启生效，失败降级不阻断），settings.yaml `l3.regime_gating` 参数化
- ✅ **B 品种暴露缩放**：`map_confidence_to_exposure`（分段 <0.4→0 / 0.4~0.7 线性 / ≥0.7→1.0）+ `apply_exposure_scale`（暴露 = 子链置信度映射 × 品种-链对齐度；score=0 跳过与 avoid-soft 保留降权结果防双重惩罚）；Step 3h1 Gate 之后、3h2 全局方向偏置之前接入
- ✅ **C 收益来源族激活**：`regime_adaptive_weight_adjustment` 新增 `subchain_regimes` 参数——因子按 `subchain_scope`（单链）路由子链 regime 倍率表（首期全局 `REGIME_STYLE_MULTIPLIERS` 复制初始化，无 scope/all/unknown/部分链回退全局向后兼容）；Step 2.5 energy+Gate 开启时 `SectorRegimeSelector.detect_all` 检测子链 regime；`build_subchain_return_source` 画像入 `_regime_meta.subchain_return_source`
- ✅ **D 灰度+串联+监控**：`--enable-regime-gating`（默认关）+ settings.yaml enabled=false 双通道；与 plans/47 幅度层调制正交串联验证（先方向后幅度）；质量报告新增 `subchain_gate_distribution` 段（与 `subchain_exposure` 互补——方向层 vs 幅度层监控）
- ✅ 测试：test_regime_gate.py 26 用例（Gate 判定/映射分段边界/防双重惩罚/盲测）+ test_portfolio_loop_adaptive +9（子链路由/回退/概率混合/画像）+ test_subchain_weight +1（串联正交）= 36 用例全绿；受影响回归 694 passed + ruff 全绿
- ✅ 差距登记：GAP-136 登记并关闭（P1，品种/子链 Regime 未作独立方向 Gate + 置信度未映射暴露）

### 因子×子链质量矩阵：评审质检与生命周期张量化（2026-08-17，build bump v2.104.0+112，GAP-137，plans/49）

**完成时间**: 2026-08-17

**核心产出**:
- ✅ **A 存储底座**：新增 `subchain_factor_quality` 时序表（评估单元=(factor_id, market, chain)，n_symbols/mean_ic/std_ic/t_stat/p_value/effective/source/decision，主键 factor_id+market+chain+evaluated_at）+ `SubchainQualityRepository`（UPSERT 幂等/时序查询/latest/recent，E.4 短连接）+ `build_subchain_quality_rows`（每因子×子链一行，t=inf→None 序列化）；晋升写首行 + 评审作业重算
- ✅ **B 评审张量化**：Q10/F6 两级判定（`judge_q10_subchain`——外层跨产业链方向一致 + 内层子链特异 t 检验护栏（min_symbols=3/min_t_stat=2.0/min_chain_ic=0.10）+ 反向子链 avoid 标记，输出 consistent/subchain_specific/conflicted）；机审单链特异放行（`AutoReviewPolicy` 全链 IC<min 但 effective 子链 t 显著 → 放行且 scope=[有效链]，Sharpe 不放行、QA 门禁不变）；准入三级权重（`SUBCHAIN_SPECIFIC_MAX_WEIGHT=0.10` 受限权重）
- ✅ **C 生命周期张量化**：`compute_subchain_degradation` 单元粒度退化——全部有效链衰减→degrade / 部分链→scope_shrink / 单链特异因子唯一链衰减→degrade / 从未 effective→keep（样本不足 None 不误判）；`_shrink_scope` 剔除失效链更新 `metadata.subchain_scope` → 47 调制矩阵 Step 2b 消费最新 metadata 自动重算闭环；冷却期 cooldown_days=30 防过激收缩
- ✅ **D 闭环+监控+可扩展**：质量报告新增 `subchain_quality_matrix` 段（与 47 `subchain_exposure`/48 `subchain_gate_distribution` 三网合一——幅度/方向/质量三层正交）；子链定义参数化（`config/futures_universe.yaml` 加映射即扩展至黑色/有色/农产品/金融等其它产业链、品种簇）；灰度 `l3.subchain_quality.enabled=false` 默认关回退全链原逻辑
- ✅ 测试：test_subchain_quality_store.py 13 + test_qa_subchain.py 19 + test_lifecycle_subchain.py 14 = 46 用例全绿；受影响回归 448 passed（qa 104 + portfolio 299 + evolution 45）+ ruff 全绿
- ✅ 差距登记：GAP-137 登记并关闭（P1，评审质检与生命周期管理未子链化）

### L3 权重层 Gate 闭环：Gate 并入子链调制矩阵（2026-08-17，build bump v2.104.0+113，GAP-138，plans/50）

**完成时间**: 2026-08-17

**核心产出**:
- ✅ **A gate_scale_map**：`regime_gate.py` 新增纯函数——Gate 决策 → 链级权重缩放系数（avoid+hard→0.0 / avoid+soft→soft_avoid_ratio / long·short·neutral→1.0：方向过滤属信号层 Step 3h1 职责，权重层只回避方向不明链）
- ✅ **B L3 权重源头闭环**：`portfolio_loop.py` Step 2.5 接线 `_merge_gate_scale_into_modulation`——Gate 开启 + energy + 子链 regime 检测成功时将 m'[factor][子链] = m × gate_scale（avoid 链权重源头归零/降权），同步 signals 的 `subchain_weights` 标注与 `factor_weights.json` 输出；依赖 Step 2b 调制矩阵存在（`enable_subchain_weight`）否则保持观测语义零行为变更；与信号层 Step 3h1 乘性串联防双重惩罚
- ✅ **B3 观测**：质量报告新增 `subchain_gate_scale` 段——与 47 `subchain_exposure`（幅度）/48 `subchain_gate_distribution`（决策）/49 `subchain_quality_matrix`（质量）四网合一
- ✅ 测试：test_regime_gate.py TestGateScaleMap 5 + test_subchain_weight.py TestMergeGateScaleIntoModulation 6 = 11 用例全绿；受影响回归 358 passed（portfolio 299 + regime_gate/subchain_weight 59）+ ruff 全绿
- ✅ 差距登记：GAP-138 登记并关闭（P1，L3 权重源头未消费子链方向 Gate）

### 质检结果落库 SSOT 闭环（2026-08-16，build bump v2.104.0+78，GAP-128）

**完成时间**: 2026-08-16

**核心产出**:
- ✅ **管线治本**：`evolution_promote._write_to_duckdb` 落库后追加 `FactorQualityScoreRepository.save_score` / `FactorAuditReportRepository.save_report`（写前按 factor_id 清理旧行保幂等，market 自动路由 stock/futures/energy，失败非阻塞仅告警），`factor_quality_scores`/`factor_audit_reports` 专属表从此随晋升自动落库
- ✅ **存量回填**：新增 `scripts/backfill_factor_quality_audit.py` 全市场幂等回填（factor_catalog.metadata → 两表，先清后插 + 孤儿清理），futures 105/103、energy 306/307 实测回填完成，`factor_audit_reports`/`factor_quality_scores` 两表从 0 条补齐至全量（旧因子无质检记录者如实跳过不伪造）
- ✅ 测试：新增 test_backfill_factor_quality_audit.py + test_evolution_promote_gap128.py 共 7 用例全绿
- ✅ 差距登记：GAP-128 登记并关闭（P1，质检结果未落库 SSOT 缺陷）

### 测试因子库隔离（2026-08-16，build bump v2.104.0+79，GAP-129，plans/43）

**完成时间**: 2026-08-16

**核心产出**:
- ✅ **单挂载点全局隔离**：根 `tests/conftest.py` autouse fixture `_isolated_factor_db` 于 `fts.factor_engine.factor_db.schema.get_db_path` 全局重定向至每测试独立 tmp DuckDB——4 仓储类构造时局部导入该符号一改全生效（futures/energy 分库文件名保留、仓储连接自动 init_database 建表），107 处测试调用零触碰零改造
- ✅ **豁免机制**：`uses_real_factor_db` 标记注册（pytest_configure）+ 5 处豁免（真实路由断言 test_energy_chain/test_factor_db/test_cli_extra/test_evolution_loop + 真实存量因子代码数据依赖 test_bincount_boundary）
- ✅ **验证测试**：新增 tests/test_factor_db_isolation.py 4 用例（隔离/仓储落 tmp/晋升零污染/豁免路由真实）；零污染实测 test_evolution_loop 242 用例运行后真实库 futures 343/105/103、energy 307/306/307 三表 COUNT 与基线完全一致
- ✅ 受影响回归 factor_db 目录 + test_factor_db + test_cli_extra 219 passed、隔离/豁免 7 passed、energy_chain+bincount 286 passed、ruff 全绿
- ✅ 差距登记：GAP-129 登记并关闭（P1，测试组写真实因子库污染生产 SSOT）

### L1 知识注入与因子注入增强（2026-08-16，build bump v2.104.0+70，plans/41）

**完成时间**: 2026-08-16

**核心产出**:
- ✅ **A 层（感知 + 动态源）**：`l1_meta_loop_job` 接入 `web_collector`（市场快照感知 0 → 12 品种）；新增 `WebSearchExtractor` 动态因子源（必应检索量化平台/能化链关键词 → LLM 提取，每轮动态换新知识）；研报/论文/天软 `max_factors 5→8`
- ✅ **C 层（实时链知识）**：`_inject_chain_knowledge` 扩展实时产业状态段（子链价差代理 + 波动聚集 + 库存/基差水位，面板异常自动降级），chain_knowledge 实测 861→1653 字符
- ✅ **D 层（预算 + 分批）**：`DEFAULT_L1_BUDGET_CONFIG` 上调（daily_token_limit 60K、max_bootstraps 30）+ energy 按四子链分批 bootstrap（每批独立 chain_focus，futures 保持单批）
- ✅ 测试：新增 test_web_search_extractor.py 8 用例 + test_meta_loop.py +9 用例；受影响模块 209 passed + ruff 全绿 + 端到端冒烟验证
- ✅ 差距登记：GAP-126（提取器源配置化）、GAP-127（平台 API 直连）登记后续推进

### L3 组合重算性能优化 A/B/C/D 四层（2026-08-16，build bump v2.104.0+63，plans/40）

**完成时间**: 2026-08-16

**核心产出**:
- ✅ **A 层（纯 Python，3–10x）**：L3 全部 8 处信号重算调用点（去重/OOS/聚类/PCA/`_auto_build_factor_returns`/elastic_net/ml_ensemble/`_panel_factor_ic`）接入 `SignalCache`（`L3_SIGNAL_CACHE_ENTRIES=20000` LRU）+ 新增 `_align_signal_to_dates` 向量化对齐（`df.index.get_indexer` 替代 O(n²) `list.index` 日期查找）——单次 run 内信号重算收敛到 1 处
- ✅ **B 层（DuckDB 下沉 2D）**：新增 `l3_signal_service.py`——`SignalMatrixBundle` 2D/3D 信号矩阵 + `build_signal_matrix`（复用缓存 + 向量化对齐）+ DuckDB corr/因子收益矩阵 SQL 下沉（E.4 短连接 + filelock）
- ✅ **C 层（numba 2D 内核）**：`numba_kernels.py` ts_zscore/ts_cvar 1D 内核接入 `feature_ops`/`ops_library`（`cache=True`/`fastmath=False`，依赖缺失回退现值零漂移）
- ✅ **D 层（一等公民 + 增量）**：信号矩阵持久化 DuckDB + `load_or_build_signal_matrix` 增量重算（`code_hash` 判定，仅新晋升/变更因子全量算，存量仅追加近窗口）
- ✅ 测试：新增 test_l3_signal_service.py 16 用例 + test_numba_kernels 32→86 + test_factor_clustering 接 `signal_cache` 参数；受影响回归 880 用例全绿 + factor_engine 全目录 not-slow 4882 passed（2 个 test_risk_tag mock 晋升用例为存量失败，git stash 基线复测确认非本次引入）
- ✅ GAP-124 登记并关闭

### L3 CAP 数量安全阀与聚类顺序修正（2026-08-16，build bump v2.104.0+67，GAP-125）

**完成时间**: 2026-08-16

**核心产出**:
- ✅ **顺序修正**：Step 1.7 CAP 由 P1 聚类前移至聚类 + 子链去冗余之后——聚类输入全部合格因子（不再被 CAP 收缩到 top-20 高分同质集合），修复聚类后代表数骤降不稳定（能源链两轮 20→3 / 20→2 触发 verifier_warning）
- ✅ **CAP 语义弱化**：新增 `_cap_safety_valve` 数量安全阀——聚类后代表数仍超限才截断（防御性数量控制），不再按样本内评分"选优"（数据窥探式选择系统性偏向过拟合因子）
- ✅ **OOS 校正评分**：`_factor_composite_score` 新增 `use_oos_ic` 参数——安全阀排序键 ic 维度优先取 Step 1.5 纯外推验证 `oos_extrapolation.new_ic`（无记录回退样本内）
- ✅ 测试：test_portfolio_loop.py 245→255（composite use_oos_ic +3 / TestCapSafetyValve +4 / 聚类先行·安全阀集成 +3）；受影响模块 325 passed + ruff 全绿
- ✅ GAP-125 登记并关闭

### CTA 手册 WorkFlow 端到端工作流 UI（2026-08-14，build bump v2.104.0+25）

**完成时间**: 2026-08-14

**核心产出**:
- ✅ 后端 `fts/workflow/` 子包：stages.py（11 阶段 + 质检闭环 = 12 节点，动作↔CLI 命令映射含 `{factor_id}`/`{report_dir}` 动态占位符）/ executor.py（WorkflowExecutor 单阶段后台线程 subprocess + 端到端顺序推进失败即停 + 超时熔断 + JSON 产物解析 + 批次状态汇总同步）/ store.py（WorkflowStore SQLite WAL 双表持久化）
- ✅ `fts/monitor/http_server.py` 扩展：GET /workflow 托管 `web/workflow_ui/dist` 构建产物 + WorkFlow API（stages/runs/runs{id}/qa/board + POST runs/run_all/stage-action-run，懒加载单例）
- ✅ 前端 `web/workflow_ui/` React18 + Vite SPA：StageFlow 阶段节点流实时轮询、端到端/单动作执行、批次历史、阶段详情弹窗（日志 + JSON 产物）、质检看板页（QA 7 状态 + 预警），hash 路由零依赖，构建产物 152KB
- ✅ 种子数据修复：27 个缺 params 种子因子补 `params: {}`（GAP-118 关闭），`fts seed validate` 恢复全绿
- ✅ 测试 tests/workflow/ 3 文件 28 用例 + ruff/mypy 全绿
- ✅ 真实端到端实测：s1→s7 全绿（数据基建/因子库挖掘/预处理/IC-IR/Regime/多因子合成/五层调仓回测），s8 暴露存量 catalog verify JSON↔DuckDB 快照漂移（GAP-119 登记）

### v0.1.0（已完成）

**完成时间**: 2026-07-18

**核心产出**:
- ✅ 从 FDT 剥离为独立项目
- ✅ Phase 1-7 全部完成
- ✅ 因子引擎三层循环（L1/L2/L3）完整实现
- ✅ CLI 入口 + 监控 + 调度框架
- ✅ 220 测试用例全绿
- ✅ 总体覆盖率 71%

### v0.2.0（已完成）

**完成时间**: 2026-07-18

**核心产出**:
- ✅ CLI 引擎命令真实调用实现（199 行，87% 覆盖率）
- ✅ Scheduler 引擎（APScheduler 集成）
- ✅ Config + memory 目录初始化
- ✅ 总体覆盖率 89%（超 80% 目标）
- ✅ evolution_loop 覆盖率 99%
- ✅ macro_evolution 覆盖率 100%
- ✅ micro_evolution 覆盖率 92%
- ✅ llm.py 覆盖率 77%
- ✅ 778 测试全绿
- ✅ 7 个差距项全部关闭

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 总体覆盖率 | 80%+ | **89%** |
| pipeline/strategies 覆盖率 | 80%+ | **100%/100%** |
| micro_evolution 覆盖率 | 70%+ | **92%** |
| CLI/monitor/scheduler 覆盖率 | 60%+ | **87%/100%/100%** |
| 总测试用例数 | 350+ | **778** |


### v0.3.0（已完成）

**完成时间**: 2026-07-19

**核心产出**:
- ✅ Data-Core 集成适配层（FTSDataProvider）
- ✅ FDT 残留依赖清除（grep "futures_data_core" fts/ → 空）
- ✅ 原子持久化（fts/core/atomic.py）
- ✅ 覆盖率 96%（超 90% 目标）
- ✅ data.py: 46% → 100%
- ✅ config/settings.py: 64% → 100%
- ✅ scheduler/engine.py: 22% → 100%
- ✅ meta_loop.py: 84% → 99%
- ✅ 969 测试全绿（超 ~820 目标）

### v1.1.0（已完成）

**完成时间**: 2026-07-24

**核心产出**:
- ✅ MCP 数据源迁移：Data-Core → akshare（腾讯/东方财富 API）
- ✅ 移除 6 个期货专用种子因子
- ✅ CLI 移除 `--universe futures`，默认市场改为 stock
- ✅ 1231 测试全绿，99% 覆盖率

### v1.2.0（已完成）

**完成时间**: 2026-08-02

**核心产出**:
- ✅ 种子因子集成：世坤 101 因子 + Qlib 158 因子加入种子池（总计 268 种子）
- ✅ seed_data 目录统一管理外部因子定义
- ✅ 熔断修复：种子评估不计入熔断计数器，跳过 Verifier
- ✅ 纯多头回测策略 + 组合分析（行业暴露/因子归因/市场环境）
- ✅ 1325 测试全绿，99% 覆盖率
- ✅ GitHub 发布（CTAAgents/factor_system）

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 种子因子数 | 9+ | **268（9 内置 + 259 外部）** |
| 演化通过率 | — | **20 代 56 elite（53 种子 + 3 进化）** |
| 纯多头夏普 | — | **4.07（+135.64% 累计收益，9.45% 最大回撤）** |
| 总测试用例数 | 1325+ | **1325** |
| 总体覆盖率 | 99% | **99%** |

### v1.5.0（已完成）

**完成时间**: 2026-08-03

**核心产出**:
- ✅ 期货数据接入：FuturesDataProvider（DuckDB kline_cache + AKShare 降级）
- ✅ CLI --universe futures 支持期货横截面因子演化
- ✅ 82 个期货品种（25 核心 + 57 全量），覆盖大商所/郑商所/上期所/能源中心/中金所/广期所
- ✅ scripts/download_futures.py 断点续传下载脚本
- ✅ 3 级数据降级（DuckDB → AKShare → 合成数据）
- ✅ 1502 测试全绿，99% 覆盖率

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 期货品种数 | 82 | **82（25 核心 + 57 全量）** |
| 数据降级级数 | 3 | **3 级（DuckDB → AKShare → 合成）** |
| 总测试用例数 | 1500+ | **1502** |
| 总体覆盖率 | 99% | **99%** |

### v2.10.0 算子演化引擎（Phase 3+ / C.4）（已完成）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ 新增 `fts/factor_engine/operator_evolution.py`（`OperatorEvolutionEngine`）：在 DSL 算子空间（58 算子 L0-L5）做适应度导向进化搜索——种群初始化（validator 参数边界 + PIT lookback 校验）→ IC+Sharpe 适应度评估（DSL executor，带表达式缓存）→ 锦标赛选择 → 子树交叉/变异（ExprNode 层面，参数受 param_bounds 约束）→ 精英保留，多代迭代后取最优表达式
- ✅ 产物为 `kind=OPERATOR` 因子：`best_factor_program()` 经 `create_operator_factor` 产出，携带 `expression`/`max_lookback`/`parent_id`/`generation`
- ✅ evolution_loop 集成：`_generate_operator_factor` 优先走 `_try_operator_engine_evolution`（operator/hybrid 模式），无评估数据或引擎失败时回退随机组合生成（原逻辑保留）
- ✅ 关闭 GAP-026：进化搜索直接在 DSL 命名空间进行，无需 GP 算子命名映射（GP 引擎维持 feature_ops 路径，双路径并存）
- ✅ 新增 13 测试用例（引擎 11 + 集成 2：初始化合法性/进化收敛/交叉变异产物校验/OPERATOR 产物/常信号罚分/评估缓存/evolution_loop 调用路径/无数据回退）
- ✅ 设计文档 `docs/harness/design/C.4-operator-evolution-engine-design.md` 落地，全量回归通过（排除既有失败文件）

### v2.49.0 高IC因子筛查剔除（Phase B.4）（已完成）

**完成时间**: 2026-08-09

**核心产出**:
- ✅ 新增 `fts/factor_engine/high_ic_screener.py`（`HighICScreener`）：将「高IC因子筛选打分表」（docs/Knowledge/高IC因子筛选打分表.xlsx）固化为自动筛查流程——16 项检查 × 6 大模块（基础指标校验 20 分 / 过拟合排查 25 分 / 冗余风格排查 20 分 / 落地性排查 20 分 / 尾部风险排查 10 分 / 综合稳定性 5 分 + 单调性 5 分），总分归一化 100 分
- ✅ 5 项一票否决（V1 外样本IC衰减>30% / V2 极值扰动IC降幅>25% / V3 存量因子相关>0.7 / V4 扣成本后超额≤0 / V5 无业务逻辑）任意触发直接 C 级剔除
- ✅ A/B/C/PASS 四级评级：A≥85 正常入库、B 60~84 暂缓优化（含正交化/中性化建议）、C<60 直接剔除、PASS 数据不足放行（不误杀原则）
- ✅ 集成到 `_promote_to_elite` 入库质检强制 Gate（**所有市场股票/期货统一启用**），筛查报告写入 elite 快照 `high_ic_screen` 字段
- ✅ 25 个筛查单元测试全绿（打分/一票否决/评级边界/市场统一性/B级建议）+ promote/elite 集成测试 16 通过无回归
- ✅ 设计文档 `docs/harness/design/B.4-high-ic-screening-design.md` 落地；登记 GAP-042（极值扰动数据源缺口）

### v2.60.0 期货流水线机构级缺陷修复（阶段 C，处理中）

**计划时间**: 2026-08-09

**核心产出（推进中）**:
- 🔄 GAP-F01（GAP-049）实盘执行链路：`fts/live_trade/` 骨架（订单生命周期状态机 `OrderState` + 持仓级止损止盈单 + 人工干预接口 + 网关抽象/模拟 + 重试超时兜底）；信号侧完备性（角色边界：真实网关由 FDT 负责）
- 🔄 GAP-F08（GAP-051）样本外强制：演化晋升路径强制 WalkForward 冷启动验证（配置开关 `force_walkforward` 默认 true）+ OOS 报告
- 🔄 GAP-F09（GAP-050 部分）保证金建模：品种保证金率表 + `CapitalAllocator` 保证金占用约束 + 强平风险告警
- ✅ 修复预存失败 `test_robustness_failure_blocks_promotion`（显式锁定 stock 语境，解除 default_market=futures 阈值放宽干扰）
- ✅ GAP-F04（GAP-050 部分）数据源降级加固：`FTSConfig.mcp_enabled` + Wind/iFinD `set_mcp_handler` 注入 + `_call_mcp` 三级行为（注入调用/启用未注入显式报错/未启用明确降级）
- ✅ GAP-F05（GAP-037 部分）深度时序模型：`MLPFactorModel` 轻量纯 numpy MLP 因子（无 torch 重依赖，缺样本/非法输入抛 `ModelNotAvailableError` 降级）
- ✅ GAP-F06（GAP-050 部分）数据质量监控：`DataLevelMonitor` 数据级监控器（缺失率/异常值/复权一致性/多源分歧）+ scheduler 每日 04:00 任务接入
- ✅ GAP-F07（GAP-050 部分）组合优化器：`PortfolioOptimizer` 风险平价/均值方差 + 换手/集中度/杠杆/VaR 约束 + scipy 降级，接入 `synthesize_signals` optimizer 模式
- ✅ GAP-F04（GAP-050 部分）数据源降级加固：`FTSConfig.mcp_enabled` + Wind/iFinD `set_mcp_handler` 注入 + `_call_mcp` 三级行为（注入调用/启用未注入显式报错/未启用明确降级）
- ✅ GAP-F05（GAP-037 部分）深度时序模型：`MLPFactorModel` 轻量纯 numpy MLP 因子（无 torch 重依赖，缺样本/非法输入抛 `ModelNotAvailableError` 降级）
- ✅ GAP-F06（GAP-050 部分）数据质量监控：`DataLevelMonitor` 数据级监控器（缺失率/异常值/复权一致性/多源分歧）+ scheduler 每日 04:00 任务接入
- ✅ GAP-F07（GAP-050 部分）组合优化器：`PortfolioOptimizer` 风险平价/均值方差 + 换手/集中度/杠杆/VaR 约束 + scipy 降级，接入 `synthesize_signals` optimizer 模式

### v2.79.0 GAP-F12/F15/F10 收尾（阶段 D，plans/21，已完成）

**完成时间**: 2026-08-10

**核心产出**:
- ✅ GAP-F12（CI 质量门禁）— `.github/workflows/ci.yml` 新增 lint/type-check/benchmark/release 四 job（ruff check + format --check / mypy fts/ / --benchmark-only / v* tag 构建发布）；`mypy fts/` 全量收敛 Success（150 source files，~121 存量错误清零）；ruff 存量违规清零 + 全量 399 文件格式统一；pyproject `dev` extra 补 mypy/ruff/pytest-benchmark
- ✅ GAP-F15（GAP-042，极值扰动一票否决）— `evaluation_chain._compute_extreme_perturbation_ic` 极值剔除重算 IC（pct 可配置 `FTSConfig.extreme_perturb_pct` 默认 0.01）→ `FactorEvaluation.extreme_perturbation` → `_promote_to_elite` 传入 `HighICScreener` V2 一票否决（ic_drop > 25%）真正生效；新增 test_extreme_perturb.py 10 用例
- ✅ GAP-F10（种子库去重）— `scripts/verify_seed_dedup.py` 内嵌 vs YAML 种子交叉去重校验 + 家族上限配置化（`FTSConfig.max_per_family` env 可配，缺省 15）+ 被拒因子日志；新增 test_seed_dedup.py 13 用例
- ✅ 全量回归 4671 passed（修复 mypy 收敛引入的 schema.py verify_database 元组表名 SQL 回归 + test_jobs mock 同步）

### v2.82.0 L1 知识源多路扩展 + 人审经验链闭环（GAP-I103 + I101/I102 二期，Stage 2 2D，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I103 + GAP-I101/I102 二期）**:
- ✅ GAP-I103 另类知识源多路——新建 `fts/factor_engine/extractors/alternative_sources.py`：`AnnouncementNewsExtractor`（东方财富公告中心 API + LLM 提取 A 股事件/舆情因子）+ `MacroEventExtractor`（东方财富宏观日历 API + LLM 提取跨品种/跨板块宏观方向因子）；对齐既有三源模式（继承 `BaseExtractor` 复用 `_llm_extract_factors`，requests+timeout=15，失败/空数据优雅降级返回空不阻断 L1）；股票管道接入公告+宏观两源（5 源）、期货管道接入宏观源（4 源），`FTSConfig` 新增 `l1_announcement_extractor_enabled`/`l1_macro_extractor_enabled`（`FTS_L1_*_EXTRACTOR_ENABLED` 环境变量，默认 True），meta_loop 构造透传
- ✅ GAP-I101 二期多源并行注入——`BaseExtractorPipeline.extract` 改为多源并行收集（ThreadPoolExecutor，单源异常不影响其他源，`_extract_one` 含异常降级与统计日志），多路知识源合并等待时间显著缩短
- ✅ GAP-I102 二期审查意见接入经验链——`FactorReviewWorkflow` 新增 `experience_chain` 可选注入 + `_record_rejection`：驳回且 comment 非空时构造 `ExperienceTrace`（success=False + `evaluation.failure_reasons` + lessons 含审查人）写入 `ExperienceChain.record_failure`；`FTS_REVIEW_EXPERIENCE_CHAIN` 开关默认 True，写入异常降级不阻断审查流程
- 📋 新增测试 23 用例：`test_alternative_sources.py` 16（公告 API 成功/空/异常/暂停/LLM 提取/管道接入+开关）+ `test_review_experience_chain.py` 7（驳回写链/批准不写/空 comment 不写/开关关闭/异常降级/审查人入 lessons/幂等）；提取器+审查 122 passed + meta_loop/settings 149 passed 全绿
- ✅ 文档同步：01/03/06/07/08/09 + 23 计划（GAP-I103/I101/I102 二期 ✅ 关闭）+ pyproject bump v2.81.0 → v2.82.0

### v2.83.0 ExecutorBackend 可插拔执行器抽象（GAP-I502，Stage 3A，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I502）**:
- ✅ 新建 `fts/factor_engine/executor_backend.py`——`ExecutorBackend` ABC（`map`/`shutdown` + 上下文管理）+ `ThreadBackend`（默认，ThreadPoolExecutor）+ `ProcessBackend`（ProcessPoolExecutor + cloudpickle 序列化目标函数，模块级 `_process_worker` 包装，支持 lambda/bound method 跨进程序列化）+ `DaskBackend`/`RayBackend`（缺依赖或创建失败自动降级 ProcessBackend）+ `create_executor_backend` 工厂（未知后端回退 thread）
- ✅ `BatchMiner.filter_batch` 批量粗筛接入后端——`BatchMiningConfig` 新增 `executor_backend`/`executor_max_workers`，`backend.map(self._filter_one, proposals, repeat(trace_id))` 保序遍历 + 单任务异常隔离，修复 trace_id 漏传 bug
- ✅ `FTSConfig` 新增 `executor_backend`（默认 thread 保持现状）/`executor_max_workers`（默认 4，`FTS_EXECUTOR_BACKEND`/`FTS_EXECUTOR_MAX_WORKERS`），evolution_loop 批量模式构造透传
- 📋 新增 `test_executor_backend.py` 14 用例（四后端 map 行为一致性/process 支持 lambda 与 bound method/缺依赖降级/未知后端回退/并发性/BatchMiner 接入 + process 单任务异常隔离/filter_batch thread 与 process 结果一致），executor_backend 14 + batch_mining 11 合计 25 passed + evolution_loop batch 3 passed 全绿
- ✅ 文档同步：01/03/06/07/08 + 23 计划（GAP-I502 ✅ 关闭）+ pyproject bump v2.82.0 → v2.83.0

### v2.84.0 tick 历史缓存增量累积 + Level2 订单流因子（GAP-I503 首期，Stage 3A，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I503 首期）**:
- ✅ tick_cache 增量累积——`FuturesDataAggregator._write_tick_cache` 按 (symbol, datetime) 去重写入（DELETE 已存在行再 INSERT）+ `tick_cache_retention_days` 保留清理（默认 7 天，写入时自动清理过期 tick），跨会话多次拉取累积成更长 tick 历史、无重复污染不膨胀；`get_ticks`/`_try_tick_cache` 新增 `start_time`/`end_time` 时间窗口查询（向后兼容）
- ✅ 新建 `fts/factor_engine/microstructure_factors.py`——`MicrostructureConfig`（window/large_threshold_abs/large_threshold_mult/min_rows）+ `classify_tick_direction`（价差方向，持平沿用前向）+ `order_flow_imbalance`（滚动窗口主动买卖量差归一化 OFI）+ `order_book_imbalance`（5 档深度 OBI）+ `large_trade_ratio`（绝对/相对阈值大单占比）+ `compute_microstructure_factors` 统一入口（FACTOR_COLUMNS 契约，缺列/不足 min_rows 优雅降级空）
- 📋 新增 `test_microstructure_factors.py` 20 用例 + `test_tick_cache_accumulate.py` 11 用例（31 passed），既有 tick/aggregator/migrate 125 passed 全绿
- ✅ 文档同步：01/03/06/07/08 + 23 计划（GAP-I503 首期 ✅ 关闭）+ pyproject bump v2.83.0 → v2.84.0

### v2.85.0 组合目标函数换手惩罚项显式化（GAP-I303，Stage 3B，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I303）**:
- ✅ `portfolio_loop.py` 新增 `apply_turnover_penalty`——组合目标函数显式换手惩罚项 λ·换手率：粘性约束后、权重归一化前执行 `w_new' = w_old + (w_new − w_old)/(1+λ)` 收缩权重变动（λ=0 关闭保持原样、λ 越大换手越低、新因子不惩罚）
- ✅ `build_combo`/`PortfolioLoop.__init__` 新增 `turnover_penalty` 参数透传；`FTSConfig` 新增 `l3_turnover_penalty`（env `FTS_L3_TURNOVER_PENALTY` 默认 0.0 关闭）
- 📋 新增 `test_turnover_penalty.py` 12 用例（单元 4 + 换手惩罚生效断言 3——Σ\|Δw\| 严格更小且 λ 单调递减 + build_combo 集成 2 + 配置读取 3），portfolio_loop 213 + 12 合计 225 passed 全绿
- ✅ 文档同步：01/03/06/07/08 + 23 计划（GAP-I303 ✅ 关闭）+ pyproject bump v2.84.0 → v2.85.0

### v2.88.0 GAP-F16 覆盖率 <90% 模块补齐（plans/21 阶段 D，GAP-041 闭环，已完成）

**完成时间**: 2026-08-10

**核心产出**:
- ✅ 组A 数据源测试补齐（139 用例）：`test_ifind_source`(+31)/`test_wind_source`(+13)/`test_tqsdk_tick_source`(+15)/`test_data`(+8)/`test_data_quality_monitor`(+34)——外部数据源网络异常/鉴权失败/超时/降级兜底 mock + `test_tdx_minute_source`(+25)/`test_tq_source`(+13)（随 v2.87.0 TDX_LOCAL 合并删除，能力迁入 test_tdx_local_source）
- ✅ 组B factor_engine 测试补齐（139 用例）：`test_evolution_loop`(+87，TestGapF16* 11 类：L2 晋升双写原子化/熔断重置/家族多样性拦截/消融信息型判定/meta_loop 失败回退等兜底分支) + `test_contracts_normalize`(+5) + `test_factor_screener`(新建 35：V1 一票否决 + V2 否决链含极值扰动 ic_drop>25%/打分分级/报告) + `test_causal_validator`(+7) + `test_factor_clustering`(+5)
- ✅ 组C 跨市场/DB/ML 测试补齐（63 用例）：`test_migrate_from_json`(新建 19) + `test_data_layer_repos`(+31) + `test_ml_models`(+8) + `test_mlp_factor`(+2) + `test_gru_factor`(+3)
- ✅ 修复外部会话新增测试文件 ruff 违规 8 处（F401 未使用 import 6 + E741 变量名 2）
- ✅ 全量回归 5132 passed 全绿（5 个竞态失败——DuckDB 外部进程占锁 ×4 + pyproject 版本并发 bump ×1——重跑验证后全绿）
- ✅ 覆盖率 TOTAL 94.31% 达标（`--cov-fail-under=90` 通过），14 个 <90% 缺口模块清零
- ✅ 文档同步：06（覆盖率汇总缺口清零 + 用例统计 +341）/08（GAP-041 ✅ 关闭）/21 计划（GAP-F16 ✅ 完成）+ pyproject bump v2.87.0 → v2.88.0 + README

### E.4 S1 L2/L3 DuckDB 连接生命周期根治（2026-08-13，日常开发，未 bump）

**完成时间**: 2026-08-13

**核心产出（design/E.4 实施，E.2 推荐路线 S1+S2 之 S1，L2/L3 剩余锁痛点根治）**:
- ✅ 新增 `fts/store/duckdb_lock.py` 跨进程写锁 `duckdb_write_lock`（msvcrt/fcntl 标准库零依赖，`data/.locks/{name}.lock`，超时抛 TimeoutError）+ `tests/store/test_duckdb_lock.py` 4 用例
- ✅ L2 `fts/data_futures.py`：删 `_WRITER`/`_DB` 模块级常驻写连接（仅留 `_READER` 池）；新增 `_write_scope()`（filelock + 短生命周期写连接，写完即关秒级）；读池 `_get_db()`/`DuckDBReader.acquire()` 改 `read_only=True`；`_write_contract_kline` 迁移短连接
- ✅ L2 `fts/data_sources/aggregator.py`：删 `_get_cache_conn`/`_cache_conn` 常驻连接；读路径 `_open_read_conn()`（read_only 短连接 + finally close）+ 写路径（kline/minute/tick）`_write_scope()`；`close()` 幂等 no-op
- ✅ L3 `fts/factor_engine/factor_db/repository.py` 4 类：`_get_conn` 补 `lock_configuration=true`（旧版静默降级）+ `retire_factor` 嵌套 repo 用后即关
- ✅ 演化晋升：`evolution_loop.py`/`evolution_futures.py` `_promote_to_elite` 注入 `@_release_repo_after`（方法退出 finally 释放 repo 写锁）
- ✅ 同步脚本 `sync_tq_contract_kline.py`/`sync_tq_futures_15y.py` 写段迁移 `_write_scope`（含 dry-run 分支释放）
- ✅ 受影响模块 **653 passed** + ruff 全绿 + mypy 通过（剩 2 处 HEAD 预存不顺手修）；顺带修复预存 tick_cache TIMESTAMP Binder Error（CAST）与注入破坏的类结构（100+ mypy attr-defined → 归零）
- ✅ 验收达成：演化进程零长驻写连接、跨进程写 filelock 串行、写后连接即关、读路径 read_only

### 34 计划 Phase 46a evolution_loop.py Mixin 化拆分第一步（2026-08-13，日常开发，build bump v2.103.0+4）

**完成时间**: 2026-08-13

**核心产出（plans/34-evolution-loop-refactor-inventory.md 盘点落地，B 阶段第一步）**:
- ✅ 职责盘点交付：`scripts/analyze_evolution_loop.py` AST 分析工具 + 34 盘点文档（11 领域分组 / 属性读点清单 / 纯函数候选）
- ✅ 新建 `fts/factor_engine/evolution_uct.py` `EvolutionUctMixin`（领域 I）：`_select_parent_uct`/`_update_uct_stats`/`_update_uct_failure`/`_check_circuit_breaker`/`_maybe_early_stop` 5 方法原样迁移，领域独享状态（`_uct_stats`/`_evolution_stop_*`/`_consecutive_empty_generations`/`_early_stop_*`）随迁
- ✅ `evolution_loop.py` `class EvolutionLoop(EvolutionUctMixin)`；公开 API 与行为等价不变（测试引用经 evolution_loop 模块兼容）
- ✅ 验证：`analyze_evolution_loop.py` 基线 + 受影响测试全绿；01/02/06/07/08/09/34 文档同步
- ⏳ 后续：channels/seeds/audit/review/prefilter/promote/candidate 8 领域 Mixin 按 34 盘点顺序推进；C 阶段组合式重构另立 plan

### 34 计划 Phase 46b evolution_loop.py Mixin 化拆分第二步（2026-08-13，日常开发，build bump v2.103.0+6）

**完成时间**: 2026-08-13

**核心产出（34 盘点领域 J trace 抽取，B 阶段第二步）**:
- ✅ 新建 `fts/factor_engine/evolution_trace.py` `EvolutionTraceMixin`（领域 J）：12 方法迁移——`_build_parent_failure_ctx`/`_build_success_pattern_report`/`_record_experiment_variant`/`_export_experiment_log`/`_record_audit_failed_trace`/`_record_ablation_failed_trace`/`_record_robustness_failed_trace`/`_record_causal_failed_trace`/`_record_success_trace`/`_record_failure_trace`/`_log_inspection_detail`/`_record_quality_filtered_trace` + `_QualityInspectionResult` 数据类（被 trace 方法与 `_QualityInspectionCompat` 共用）
- ✅ 领域独享状态随迁：`_success_pattern_cache`/`_experiment_log_dir`/`_experiment_variants`（mixin 类型声明，主类 `__init__` 装配）；`run` 的 `_experiment_variants.clear()` 经继承属性声明兼容保留
- ✅ `evolution_loop.py` `class EvolutionLoop(EvolutionUctMixin, EvolutionTraceMixin)`；`_QualityInspectionResult` re-export（测试 import 兼容）；公开 API 与行为等价不变
- ✅ 验证：`analyze_evolution_loop.py` 基线 + 受影响测试全绿；01/02/08/09/34 文档同步
- ⏳ 后续：seeds/audit/review/prefilter/promote/candidate 6 领域 Mixin 按 34 盘点顺序推进

### 34 计划 Phase 46c evolution_loop.py Mixin 化拆分第三步（2026-08-13，日常开发，build bump v2.103.0+7）

**完成时间**: 2026-08-13

**核心产出（34 盘点领域 G 演化通道抽取，B 阶段第三步）**:
- ✅ 新建 `fts/factor_engine/evolution_channels.py` `EvolutionChannelsMixin`（领域 G）：4 方法迁移——`_run_gp_evolution`（GP 遗传规划，feature_ops_engine + feature_importance_analyzer）/`_run_deep_evolution`（GRU/Transformer 深度因子，GAP-I203/C5）/`_generate_operator_factor`（FTS-Expr DSL 随机生成 + 常数信号前置拦截）/`_try_operator_engine_evolution`（算子演化引擎搜索，C.4）
- ✅ 组件随领域声明：`feature_ops_engine`/`feature_importance_analyzer`（mixin 类型声明，主类 `__init__` 装配）；跨领域共享（`data`/`forward_returns`/`market`/`cross_section_data`/`_is_cross_section`）留在主类
- ✅ `evolution_loop.py` `class EvolutionLoop(EvolutionUctMixin, EvolutionTraceMixin, EvolutionChannelsMixin)`；公开 API 与行为等价不变
- ✅ 验证：`analyze_evolution_loop.py` 基线 + 受影响测试全绿；01/02/08/09/34 文档同步
- ⏳ 后续：seeds/audit/review/prefilter/promote/candidate 6 领域 Mixin 按 34 盘点顺序推进

### 34 计划 Phase 46d evolution_loop.py Mixin 化拆分第四步（2026-08-13，日常开发，build bump v2.103.0+11）

**完成时间**: 2026-08-13

**核心产出（34 盘点领域 D 种子/横截面抽取，B 阶段第四步）**:
- ✅ 新建 `fts/factor_engine/evolution_seeds.py` `EvolutionSeedsMixin`（领域 D）：6 方法迁移——`_evaluate_and_promote_seeds`（种子评估晋升编排，跨调 E/F/C/J 域方法）/`_merge_l1_candidates`（GAP-031 L1 注入候选合并 + GAP-036 历史遗留清理 + pending 门控 + market 过滤）/`_run_seed_correlation_check`（L2 种子相关性预检，>50 种子跳过 + 横截面/时序双模式）/`_build_barra_exposures`（GAP-I304 风格暴露缓存）/`_evaluate_cross_section`（横截面评估，ic≥0.03 & sharpe≥1.5 门槛）/`run_microstructure_promotion`（C1 公开入口）
- ✅ 组件随领域声明：`cap_map`/`industry_map`/`_barra_exposures_cache`/`_barra_exposures_attempted`（mixin 类型声明，主类 `__init__` 装配）；跨领域共享（`data`/`forward_returns`/`market`/`cross_section_data`/`cross_section_dates`/`_is_cross_section`/`evaluation_chain`/`verifier`/`quality_inspector`/`inject_dir`）留在主类
- ✅ `evolution_loop.py` `class EvolutionLoop(EvolutionUctMixin, EvolutionTraceMixin, EvolutionChannelsMixin, EvolutionSeedsMixin)`；公开 API 与行为等价不变
- ✅ 验证：`analyze_evolution_loop.py` 基线（方法数 43→37）+ 受影响测试全绿 + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：audit/review/prefilter/promote/candidate 5 领域 Mixin 按 34 盘点顺序推进

### 34 计划 Phase 46e evolution_loop.py Mixin 化拆分第五步（2026-08-13，日常开发，build bump v2.103.0+12）

**完成时间**: 2026-08-13

**核心产出（34 盘点领域 E 审计/验证抽取，B 阶段第五步）**:
- ✅ 新建 `fts/factor_engine/evolution_audit.py` `EvolutionAuditMixin`（领域 E）：9 方法迁移——`_run_factor_audit`（FactorAuditor 编排 + GAP-F08 冷启动走航优先 + GAP-079 窗口不足保留事实）/`_run_walkforward_oos`（独立走航，force_walkforward 门控 + 数据 <125 行跳过）/`_run_backtest_pipeline`（BacktestPipeline 薄包装）/`_run_ablation_check`（消融，仅拦截型判定伪相关）/`_run_robustness_check`（鲁棒性，期货 0.7 通过率阈值）/`_run_shap_analysis`（SHAP 信息型审查）/`_run_causal_validation`（因果异常判定）+ `_build_wf_config`/`_is_blocking_ablation` 两个 @staticmethod 纯函数 + `_ABLATION_PRICE_CORE_COLS`/`_ABLATION_INFORMATIONAL_MODES` 类常量随迁
- ✅ 组件随领域声明：`auditor`/`backtest_pipeline`/`ablation_experiment`/`robustness_tester`/`shap_analyzer`/`causal_validator`（mixin 类型声明，主类 `__init__` 装配）；`_signal_cache` 与 B 域共享保留引用；`data`/`forward_returns` 跨领域共享留在主类
- ✅ `evolution_loop.py` `class EvolutionLoop(EvolutionUctMixin, EvolutionTraceMixin, EvolutionChannelsMixin, EvolutionSeedsMixin, EvolutionAuditMixin)`；公开 API 与行为等价不变；`_build_wf_config`/`_is_blocking_ablation` 静态方法经 MRO 保持 `EvolutionLoop.` 直接调用兼容（测试 test_evolution_loop.py L2770-5250 不受影响）
- ✅ 验证：`analyze_evolution_loop.py` 基线（方法数 37→28）+ 受影响测试全绿 + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：review/prefilter/promote/candidate 4 领域 Mixin 按 34 盘点顺序推进

### 34 计划 Phase 46f evolution_loop.py Mixin 化拆分第六步（2026-08-13，日常开发，build bump v2.103.0+14）

**完成时间**: 2026-08-13

**核心产出（34 盘点领域 F 定期评审/数据质量抽取，B 阶段第六步）**:
- ✅ 新建 `fts/factor_engine/evolution_review.py` `EvolutionReviewMixin`（领域 F）：4 方法迁移——`_run_periodic_factor_review`（精英因子定期重评估：GAP-I305 自动淘汰开关 + 衰减分级反馈联动 feedback_loop + LogicMonitor 集成 + 状态/等级报告）/`_get_factor_data_for_review`（Verifier 数据读取薄包装）/`_register_factor_baseline`（DataQualityMonitor 基准注册）/`_check_factor_data_quality`（DataQualityMonitor 质量检查 + 告警打印），原样剪切迁移（不改逻辑）
- ✅ 组件随领域声明：`data_quality_monitor`（与 A 域共享，保留引用）/`elite_tracker`/`feedback_loop`/`logic_monitor`/`verifier`（mixin 类型声明，主类 `__init__` 装配）；`data`/`elite_dir`/`_decay_auto_retire_enabled` 跨领域共享留在主类
- ✅ `evolution_loop.py` `class EvolutionLoop(EvolutionUctMixin, EvolutionTraceMixin, EvolutionChannelsMixin, EvolutionSeedsMixin, EvolutionAuditMixin, EvolutionReviewMixin)`；公开 API 与行为等价不变
- ✅ 验证：`analyze_evolution_loop.py` 基线（方法数 28→24）+ 受影响测试全绿 + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：prefilter/promote/candidate 3 领域 Mixin 按 34 盘点顺序推进

### 34 计划 Phase 46g evolution_loop.py Mixin 化拆分第七步（2026-08-13，日常开发，build bump v2.103.0+16）

**完成时间**: 2026-08-13

**核心产出（34 盘点领域 H 候选预筛抽取，B 阶段第七步）**:
- ✅ 新建 `fts/factor_engine/evolution_prefilter.py` `EvolutionPrefilterMixin`（领域 H）：3 方法迁移——`_quick_prefilter`（快速预筛三元组 (ok, reason, ic)：信号变化 nunique>10 + 标准差 + 快速 Spearman IC，期货/股票市场自适应阈值 0.01/0.02，横截面模式转发）/`_cross_section_prefilter`（横截面真实截面 IC，与 cross_section_evaluate_backtest 同口径，GAP-X01）/`_check_factor_runtime`（后代运行时校验：广播错误/长度不匹配/常数信号，复用 BacktestPipeline._execute_factor_code），原样剪切迁移（不改逻辑）
- ✅ 纯读跨领域共享（`data`/`market`/`forward_returns`/`cross_section_data`/`cross_section_dates`/`_is_cross_section`），无领域独享状态、无组件装配
- ✅ `evolution_loop.py` `class EvolutionLoop(EvolutionUctMixin, EvolutionTraceMixin, EvolutionChannelsMixin, EvolutionSeedsMixin, EvolutionAuditMixin, EvolutionReviewMixin, EvolutionPrefilterMixin)`；公开 API 与行为等价不变；run/_process_candidate/_batch_prefilter 调用点经 MRO 动态派发
- ✅ 验证：`analyze_evolution_loop.py` 基线（方法数 24→21）+ 受影响测试全绿 + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：promote/candidate 2 领域 Mixin 按 34 盘点顺序推进

### 34 计划 Phase 46h evolution_loop.py Mixin 化拆分第八步（2026-08-13，日常开发，build bump v2.103.0+18）

**完成时间**: 2026-08-13

**核心产出（34 盘点领域 C 精英晋升/持久化抽取，B 阶段第八步）**:
- ✅ 新建 `fts/factor_engine/evolution_promote.py` `EvolutionPromoteMixin`（领域 C）：11 方法迁移——`_promote_to_elite`（精英晋升全流程：去重/结构簇配额回退 max_per_family/L2 准入去冗余+正交化闭环/高IC筛查强制门/多重检验/影子池/种子溯源/追踪器注册/一致性日志，`@_release_repo_after` 装饰）/`_write_to_duckdb`（DuckDB 主存储幂等写入 + 评估记录）/`_scan_elite_correlations`（新因子信号单次计算 + 既有 elite 相关性扫描）/`_check_elite_correlation`/`_count_cluster_members`/`_orthogonalize_via_basis`（Gram-Schmidt 基底）/`_orthogonalize_candidate`（单参照 OLS 残差）/`_load_elite_parent_factors`/`_write_seed_correlation_index`/`_get_repo`（延迟初始化 FactorRepository）/`_release_repo_after`（E.4 S1 仓储写锁释放装饰器），原样剪切迁移（不改逻辑）
- ✅ 领域独享状态 `_repo`/`_cluster_*`/`_l2_*`/`orthogonal_basis`/`high_ic_screener`/`elite_tracker` 装配于主类 `__init__`，mixin 类型声明；模块级符号 `_build_shadow_pool`/`_SHADOW_OBSERVE_TRADING_DAYS`/`_log_consistency_event` 经函数体内延迟导入（防模块级循环导入，契约第 4 条）
- ✅ `evolution_loop.py` `class EvolutionLoop(EvolutionUctMixin, EvolutionTraceMixin, EvolutionChannelsMixin, EvolutionSeedsMixin, EvolutionAuditMixin, EvolutionReviewMixin, EvolutionPrefilterMixin, EvolutionPromoteMixin)`；公开 API 与行为等价不变；run/_evolve_one/_process_candidate 调用点经 MRO 动态派发
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 2734→1842、方法数 21→10）+ 受影响测试 **602 passed** + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：candidate 1 领域 Mixin 按 34 盘点顺序推进（B 阶段收官）

### 34 计划 Phase 46i evolution_loop.py Mixin 化拆分第九步（2026-08-13，日常开发，build bump v2.103.0+19，B 阶段收官）

**完成时间**: 2026-08-13

**核心产出（34 盘点领域 B 候选准入链抽取，B 阶段第九步/收官）**:
- ✅ 新建 `fts/factor_engine/evolution_candidate.py` `EvolutionCandidateMixin`（领域 B）：`_process_candidate` 单方法迁移（Step 2-6 准入链：微观演化→三级评估→UCT 反馈→Verifier→质量评分卡→端到端回测→数据质量→6 项强制审计→消融→因果→鲁棒性→SHAP→晋升/淘汰→状态持久化），原样剪切迁移（不改逻辑）
- ✅ 14 属性类型声明（data/forward_returns/cross_section_data/_is_cross_section/evaluation_chain/verifier/quality_inspector/state_manager/budget/n_trials_micro/_micro_staged_evolution/_prior_evaluations/_signal_cache/_consecutive_low_ic）+ 21 个跨域方法 Callable 类型声明（trace J 11 个/audit E 7 个/seeds D 1 个/uct I 1 个/review F 2 个/promote C 1 个），运行时 MRO 动态派发
- ✅ `evolution_loop.py` `class EvolutionLoop(EvolutionUctMixin, EvolutionTraceMixin, EvolutionChannelsMixin, EvolutionSeedsMixin, EvolutionAuditMixin, EvolutionReviewMixin, EvolutionPrefilterMixin, EvolutionPromoteMixin, EvolutionCandidateMixin)`；测试模块级补丁 `evolve_micro` 目标迁移至 `evolution_candidate.py`（conftest + test_evolution_loop ×2 + test_coverage_edge_cases）；顶层孤儿 import `evolve_micro` 移除
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 1842→1470、方法数 10→9，**B 阶段收官：5117 行/62 方法 → 1470 行/9 方法**）+ 受影响测试 **279 passed** + ruff/mypy 通过；01/02/08/09/34 文档同步
- ✅ B 阶段 9 领域全部完成（I/J/G/D/E/F/H/C/B），C 阶段（Mixin→协作类组件化）为后续增强项（plans/34 §6）

### 34 计划 Phase 47a evolution_loop.py C 阶段组合式重构第一步（2026-08-13，日常开发，build bump v2.103.0+21，C 阶段启动）

**完成时间**: 2026-08-13

**核心产出（34 计划 §8 C 阶段：Mixin → 协作类，领域 I 先行）**:
- ✅ `fts/factor_engine/evolution_uct.py` `EvolutionUctMixin` → **`UctSelector` 协作类**（构造注入 `budget` + `low_ic_box`，领域状态 `_uct_stats`/`_evolution_stop_*`/`_consecutive_empty_generations`/`_early_stop_*` 随迁；`_consecutive_low_ic` 经 `low_ic_box` property 只读共享，主循环持有）
- ✅ `evolution_loop.py`：继承链移除 `EvolutionUctMixin`（9→8 Mixin）；`__init__` 解析 `_stop_enabled`/`_stop_k` 后装配 `self._low_ic_box + self._uct_selector = UctSelector(...)`；类尾新增 7 属性 property 转发（`_uct_stats`/`_consecutive_low_ic`/`_evolution_stop_enabled`/`_evolution_stop_k`/`_consecutive_empty_generations`/`_early_stop_last_count`/`_early_stop_reason`，均含 getter+setter）+ 5 方法一行转发桩（`_select_parent_uct`/`_update_uct_stats`/`_update_uct_failure`/`_check_circuit_breaker`/`_maybe_early_stop`）
- ✅ 公开 API 与行为等价不变（`UCT_EXPLORATION_C` 单一事实源保留于 evolution_uct.py，evolution_loop.py re-export）；25+ 测试文件零改动（test_uct_selection/test_evolution_stop/test_gap074 直接读写 `_uct_stats`/`_evolution_stop_*` 经 property 兼容）
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 1470→1561、方法数 9→28，逻辑方法 + 转发桩/property 16）+ 受影响测试 **47+235 passed** + ruff/mypy 通过；01/02/08/09/34 文档同步
- ✅ **回归验收（2026-08-13，交付前）**：全量 not-slow 回归 `pytest tests/ -m "not slow" -n 4 --dist loadfile` —— **6602 passed + 12 failed + 1 skipped（22:25）**；12 个失败全部分类归因：**4 并行竞争**（bincount_boundary ×2 + executor_backend BrokenProcessPool ×1 + operator_evolution ×1，单进程复跑 246 passed 全绿）+ **8 预存**（verify_doc_consistency ×6 种子池语义断言 + duckdb_reader ×1 DuckDB 嵌入式固有行为 + version_format ×1 build 段格式），**零代码回归**；`verify_doc_consistency.py` 13/13 通过，bump v2.103.0+21
- ⏳ 后续：47b-47i 按 plans/34 §8.6 顺序推进（CandidatePrefilter → EliteStore → AuditPipeline → TraceRecorder → FactorReviewer → EvolutionChannels → SeedManager → CandidateProcessor + 主循环/`__init__` 精简）

### 34 计划 Phase 47b evolution_loop.py C 阶段组合式重构第二步（2026-08-13，日常开发，build bump v2.103.0+22）

**完成时间**: 2026-08-13

**核心产出（34 计划 §8 C 阶段：Mixin → 协作类，领域 H 候选预筛）**:
- ✅ `fts/factor_engine/evolution_prefilter.py` `EvolutionPrefilterMixin` → **`CandidatePrefilter` 协作类**（领域 H：`_quick_prefilter`/`_cross_section_prefilter`/`_check_factor_runtime` 3 方法，无领域独享状态）
- ✅ **可变上下文模式修订（34 §8.3 先例）**：领域 H 纯读全局上下文，但主类/测试可能在构造后运行时重赋值（如 `loop.cross_section_data = {...}`/`loop.market = "stock"`）——构造注入值快照会脱节 → 协作类注入 **owner（主类实例）**，方法内动态 `self._owner.<attr>` 读取（owner 经主类组装注入，Any 标注防循环 ForwardRef）
- ✅ `evolution_loop.py`：继承链移除 `EvolutionPrefilterMixin`（8→7 Mixin）；`__init__` 装配 `self._candidate_prefilter = CandidatePrefilter(owner=self)`；类尾新增 3 方法一行转发桩（`_quick_prefilter`/`_cross_section_prefilter`/`_check_factor_runtime`）
- ✅ 公开 API 与行为等价不变；25+ 测试文件零改动（TestGapF16PrefilterAndRuntime 等直接调用/重赋值上下文经转发桩 + owner 动态读取兼容）
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 1561→1586、方法数 28→31）+ 受影响测试 **253 passed** + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：47c-47i 按 plans/34 §8.6 顺序推进（EliteStore → AuditPipeline → TraceRecorder → FactorReviewer → EvolutionChannels → SeedManager → CandidateProcessor + 主循环/`__init__` 精简）

### 34 计划 Phase 47c evolution_loop.py C 阶段组合式重构第三步（2026-08-13，日常开发，build bump v2.103.0+25）

**完成时间**: 2026-08-13

**核心产出（34 计划 §8 C 阶段：Mixin → 协作类，领域 C 精英晋升/持久化，最重协作类）**:
- ✅ `fts/factor_engine/evolution_promote.py` `EvolutionPromoteMixin` → **`EliteStore` 协作类**（11 方法中 10 个随迁 + `_release_repo_after` 装饰器保留）
- ✅ **重领域状态随迁构造**：`_repo`/`_cluster_quota_enabled`/`_cluster_max`/`_cluster_corr_threshold`/`_cluster_max_scan`/`_l2_*`×9/`orthogonal_basis`/`high_ic_screener`/`elite_tracker` 全部随迁 `EliteStore.__init__`（原主类 __init__ L430-475 配置解析 + L507-558 组件实例化迁移；elite_tracker/orthogonal_basis 依赖 owner.memory_dir/`_decay_*` 动态读取）
- ✅ `evolution_loop.py`：继承链移除 `EvolutionPromoteMixin`（7→6 Mixin）；`__init__` 删除对应装配段（`_l2_*`/`_cluster_*` 解析、high_ic_screener/elite_tracker/orthogonal_basis 实例化、`_repo=None`）改为装配 `self._elite_store = EliteStore(owner=self)`；类尾新增 **10 方法转发桩**（`_promote_to_elite`/`_write_to_duckdb`/`_scan_elite_correlations`/`_check_elite_correlation`/`_count_cluster_members`/`_orthogonalize_via_basis`/`_orthogonalize_candidate`/`_load_elite_parent_factors`/`_write_seed_correlation_index`/`_get_repo`）+ **17 属性 property 转发含 setter**（`_repo`/`_cluster_*`×4/`_l2_*`×9/`orthogonal_basis`/`high_ic_screener`/`elite_tracker`）
- ✅ **测试 mock 兼容关键设计**：内部 `_get_repo()`/`_scan_elite_correlations()`/`_check_elite_correlation()`/`_count_cluster_members()`/`_orthogonalize_*()`/`_write_to_duckdb()` 调用统一改经 `self._owner.X()` 转发——测试 `loop._get_repo = MagicMock(...)` 实例属性覆盖主类转发桩后经 owner 调用生效（EliteStore 方法体内 `repo = self._owner._get_repo()`）；`_orthogonalize_via_basis` 转发桩含 store 检测 fallback（`EvolutionLoop._orthogonalize_via_basis(mock_loop, ...)` 类级未绑定调用兼容），协作类方法体 `owner = getattr(self, "_owner", self)` 兜底
- ✅ 公开 API 与行为等价不变；25+ 测试文件零改动（test_l2_elite_redundancy/test_l2_orthogonalize/test_structure_cluster_quota/test_orthogonal_basis/test_microstructure_promotion/test_evolution_stop 等）
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 1586→1744、方法数 31→75）+ 受影响测试 **336 passed** + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：47d-47i 按 plans/34 §8.6 顺序推进（AuditPipeline → TraceRecorder → FactorReviewer → EvolutionChannels → SeedManager → CandidateProcessor + 主循环/`__init__` 精简）

### 34 计划 Phase 47d evolution_loop.py C 阶段组合式重构第四步（2026-08-13，日常开发，build bump v2.103.0+26）

**完成时间**: 2026-08-13

**核心产出（34 计划 §8 C 阶段：Mixin → 协作类，领域 E 审计/验证管线，`_signal_cache` 归属落地）**:
- ✅ `fts/factor_engine/evolution_audit.py` `EvolutionAuditMixin` → **`AuditPipeline` 协作类**（9 方法随迁 + 2 static `_build_wf_config`/`_is_blocking_ablation` 保留）
- ✅ **`_signal_cache` 归属落地（34 §8.3 第 2 条）**：质检链信号缓存随迁 `AuditPipeline`（构造内 `SignalCache(max_entries=_QC_SIGNAL_CACHE_MAX_ENTRIES)`，常量经 evolution_loop 延迟导入规避循环）；CandidateProcessor（B 域 Mixin）经主类 property 转发共享同一引用；run() 的 `self._signal_cache.clear()` 经 property 转发兼容
- ✅ 领域 E 6 组件随迁构造：`auditor`（`audit_config` 为 __init__ 参数→构造注入 `AuditPipeline(owner=self, audit_config=audit_config)`）/`backtest_pipeline`/`ablation_experiment`/`shap_analyzer`（GAP-080 采样参数动态读取）/`robustness_tester`/`causal_validator`；data/forward_returns 经 owner 动态读取（含 4 方法 `getattr(self, "data"...)` 模式改 `getattr(self._owner, ...)`）
- ✅ `evolution_loop.py`：继承链移除 `EvolutionAuditMixin`（6→5 Mixin）；`__init__` 删除对应装配段；类尾新增 **11 方法转发桩**（9 方法 + 2 static）+ **7 属性 property 转发含 setter**（`_signal_cache`/`auditor`/`backtest_pipeline`/`ablation_experiment`/`robustness_tester`/`shap_analyzer`/`causal_validator`）
- ✅ **测试 mock 兼容**：`_run_factor_audit` 内部 `self._run_walkforward_oos()` 经 `self._owner._run_walkforward_oos()` 转发（测试 `minimal_loop._run_walkforward_oos = MagicMock(...)` 生效）；`_build_wf_config` 内部调用同理
- ✅ 公开 API 与行为等价不变；25+ 测试文件零改动（test_evolution_loop 审计链/WalkForward 专项 2770-2898 行等）
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 1744→1827、方法数 75→98）+ 受影响测试 **318 passed** + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：47e-47i 按 plans/34 §8.6 顺序推进（TraceRecorder → FactorReviewer → EvolutionChannels → SeedManager → CandidateProcessor + 主循环/`__init__` 精简）

### 34 计划 Phase 47e evolution_loop.py C 阶段组合式重构第五步（2026-08-13，日常开发，build bump v2.103.0+27）

**完成时间**: 2026-08-13

**核心产出（34 计划 §8 C 阶段：Mixin → 协作类，领域 J trace/经验链/实验日志，接口面广被 7 域依赖）**:
- ✅ `fts/factor_engine/evolution_trace.py` `EvolutionTraceMixin` → **`TraceRecorder` 协作类**（12 方法随迁；`_QualityInspectionResult` 数据类模块级保留，evolution_loop re-export 不变）
- ✅ 领域 J 3 状态随迁构造：`_success_pattern_cache`/`_experiment_log_dir`/`_experiment_variants`（原主类 __init__ L455/458-459 段迁移；`experiment_log_dir` 为 __init__ 参数构造注入 `TraceRecorder(owner=self, experiment_log_dir=experiment_log_dir)`）
- ✅ 跨领域共享状态经 owner 动态读取：`experience_chain`/`memory_dir`/`state_manager`/`market`（12 处替换）
- ✅ `evolution_loop.py`：继承链移除 `EvolutionTraceMixin`（5→4 Mixin）；`__init__` 删除对应装配段（run() 的 `_experiment_variants.clear()` 经 property 转发兼容）；类尾新增 **12 方法转发桩** + **3 属性 property 转发含 setter**（`_success_pattern_cache`/`_experiment_log_dir`/`_experiment_variants`）
- ✅ 公开 API 与行为等价不变；25+ 测试文件零改动（test_experiment_log 读写 `_experiment_variants`/`_experiment_log_dir` 经 property；test_evolution_loop L5308 `_record_audit_failed_trace` 经转发桩）
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 1827→1965、方法数 98→116）+ 受影响测试 **277 passed** + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：47f-47i 按 plans/34 §8.6 顺序推进（FactorReviewer → EvolutionChannels → SeedManager → CandidateProcessor + 主循环/`__init__` 精简）

### 34 计划 Phase 47f evolution_loop.py C 阶段组合式重构第六步（2026-08-13，日常开发，build bump v2.103.0+29）

**完成时间**: 2026-08-13

**核心产出（34 计划 §8 C 阶段：Mixin → 协作类，领域 F 定期评审/数据质量）**:
- ✅ `fts/factor_engine/evolution_review.py` `EvolutionReviewMixin` → **`FactorReviewer` 协作类**（4 方法随迁：`_run_periodic_factor_review`/`_get_factor_data_for_review`/`_register_factor_baseline`/`_check_factor_data_quality`）
- ✅ **领域 F 无独享状态**（34 §8.2 设计确认）：组件 elite_tracker/feedback_loop/logic_monitor/verifier/data_quality_monitor 与上下文 data/elite_dir/_decay_auto_retire_enabled 全部经 owner 动态读取（19 处替换），**零 property 需求**；`_get_factor_data_for_review` 方法体内部调用经 `self._owner._get_factor_data_for_review` 转发（测试 mock 生效）
- ✅ `evolution_loop.py`：继承链移除 `EvolutionReviewMixin`（4→3 Mixin）；`__init__` 删除对应装配段；类尾新增 **4 方法转发桩**（FactorReviewer 零 property）
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 1965→1994、方法数 116→120）+ 受影响测试 **277 passed** + ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：47g-47i 按 plans/34 §8.6 顺序推进（EvolutionChannels → SeedManager → CandidateProcessor + 主循环/`__init__` 精简）

### 34 计划 Phase 47g evolution_loop.py C 阶段组合式重构第七步（2026-08-13，日常开发，build bump v2.103.0+30）

**完成时间**: 2026-08-13

**核心产出（34 计划 §8 C 阶段：Mixin → 协作类，领域 G GP/深度/算子 DSL 演化通道）**:
- ✅ `fts/factor_engine/evolution_channels.py` `EvolutionChannelsMixin` → **`EvolutionChannels` 协作类**（4 方法随迁：`_run_gp_evolution`/`_run_deep_evolution`/`_generate_operator_factor`/`_try_operator_engine_evolution`）
- ✅ 领域 G 组件随迁构造：`macro_evolver`（`MacroEvolver(llm_client=owner.llm_client, experience_chain=owner.experience_chain, max_tokens_per_call=owner.budget[...])`——构造依赖 owner 全局上下文，主类装配序须在 llm_client/experience_chain 之后）/`feature_ops_engine`/`feature_importance_analyzer`
- ✅ 跨领域共享数据经 owner 动态读取：`data`/`forward_returns`/`market`/`cross_section_data`/`_is_cross_section`（与 47b 可变上下文先例一致）
- ✅ `evolution_loop.py`：继承链移除 `EvolutionChannelsMixin`（3→2 Mixin，剩余 Seeds/Candidate）；`__init__` 删除对应装配段（`MacroEvolver` import 清理，仅保留 `get_default_llm_client`）；类尾新增 **4 方法转发桩** + **3 属性 property 转发**（`macro_evolver`/`feature_ops_engine`/`feature_importance_analyzer`，兼容测试 `loop.macro_evolver.evolve = mock`/`loop.feature_ops_engine.run_gp_search = mock` 属性修改）
- ✅ 公开 API 与行为等价不变；25+ 测试文件零改动（test_evolution_loop GP/operator/deep 路径、test_gap074_operator_diversity、operator_evolution、test_gru_factor、test_transformer_factor 等）
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 1994→2037、方法数 120→127）+ 受影响测试 **283 passed**（operator_evolution 1 例既有随机波动——时间戳种子随机生成，单跑 4/4 通过，非重构引入）+ ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：47h-47i 按 plans/34 §8.6 顺序推进（SeedManager → CandidateProcessor + 主循环/`__init__` 精简）

### 34 计划 Phase 47h evolution_loop.py C 阶段组合式重构第八步（2026-08-13，日常开发，build bump v2.103.0+32）

**完成时间**: 2026-08-13

**核心产出（34 计划 §8 C 阶段：Mixin → 协作类，领域 D 种子管理/横截面评估）**:
- ✅ `fts/factor_engine/evolution_seeds.py` `EvolutionSeedsMixin` → **`SeedManager` 协作类**（7 方法随迁：`_evaluate_and_promote_seeds`/`_merge_l1_candidates`/`_run_seed_correlation_check`/`_build_barra_exposures`/`_build_vol_map`/`_evaluate_cross_section`/`run_microstructure_promotion`）
- ✅ 领域 D 状态随迁构造：`_barra_exposures_cache`/`_barra_exposures_attempted`（原主类 __init__ GAP-I304 段迁移）；`_build_barra_exposures` 内 `hasattr(self, "_barra_exposures_attempted")` 守卫简化为实例属性恒在判定（构造即初始化，行为等价）
- ✅ 跨领域共享数据经 owner 动态读取：`data`/`forward_returns`/`market`/`cross_section_data`/`cross_section_dates`/`_is_cross_section`/`inject_dir`/`evaluation_chain`/`verifier`/`quality_inspector`/`industry_map`/`cap_map`——**industry_map/cap_map 属可变上下文**（industry_map 主类 __init__ 内经期货板块映射自动注入、可测试重赋值），走 47b owner 动态读取先例
- ✅ **跨域方法 16 处经 owner 转发**使测试 `loop._X = MagicMock` 实例打桩生效：`_promote_to_elite`/`_run_backtest_pipeline`/`_run_factor_audit`/`_run_ablation_check`/`_run_causal_validation`/`_run_robustness_check`/`_run_shap_analysis`/`_record_failure_trace`/`_record_audit_failed_trace`/`_record_ablation_failed_trace`/`_record_causal_failed_trace`/`_record_robustness_failed_trace`/`_log_inspection_detail`/`_register_factor_baseline`/`_check_factor_data_quality`/`_evaluate_cross_section`（test_microstructure_promotion 对 `loop._evaluate_cross_section` 打桩、test_risk_tag 对 `loop._promote_to_elite` 打桩均生效）
- ✅ `evolution_loop.py`：继承链移除 `EvolutionSeedsMixin`（2→1 Mixin，仅剩 Candidate）；`__init__` 删除 Barra 缓存装配段；类尾新增 **7 方法转发桩** + **2 属性 property 转发含 setter**（`_barra_exposures_cache`/`_barra_exposures_attempted`）
- ✅ 公开 API 与行为等价不变；25+ 测试文件零改动（test_evolution_l1_merge/test_microstructure_promotion/test_risk_tag/test_evolution_stop 等）
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 2037→2110、方法数 127→138）+ 受影响测试 **300 passed**（65 专项 + 235 test_evolution_loop not-slow）+ ruff/mypy 通过；01/02/08/09/34 文档同步
- ⏳ 后续：47i（CandidateProcessor + 主循环 `run`/`_evolve_one` 编排改造 + `__init__` 精简）——C 阶段收官，完成后继承链清零 + 里程碑全量回归

### 34 计划 Phase 47i evolution_loop.py C 阶段组合式重构收官（2026-08-13，日常开发，build bump v2.103.0+33）

**完成时间**: 2026-08-13

**核心产出（34 计划 §8 C 阶段：Mixin → 协作类，领域 B 候选准入链 + 继承链清零）**:
- ✅ `fts/factor_engine/evolution_candidate.py` `EvolutionCandidateMixin` → **`CandidateProcessor` 协作类**（`_process_candidate` 单方法随迁，Step 2-6 准入链）
- ✅ 领域 B 状态随迁构造：`_prior_evaluations`（34 §8.3 状态所有权确认归本协作类，原主类 __init__ 段迁移）
- ✅ 跨领域共享数据经 owner 动态读取：`data`/`forward_returns`/`cross_section_data`/`_is_cross_section`/`evaluation_chain`/`verifier`/`quality_inspector`/`state_manager`/`budget`/`n_trials_micro`/`_micro_staged_evolution`；**`_signal_cache`**（归 AuditPipeline，主类 property 转发共享同一引用）与 **`_consecutive_low_ic`**（归主循环，主类 property 转发到 low_ic_box 与 UctSelector 共享）经主类 property 读写
- ✅ **跨域方法 21 处经 owner 转发**使测试实例打桩生效（test_evolution_stop/test_evolution_loop batch 路径对 `loop._process_candidate` 打桩、`loop._consecutive_low_ic += 1` 经 property 写 low_ic_box 与熔断联动）
- ✅ `evolution_loop.py`：**继承链清零，`class EvolutionLoop:`**（MRO 仅 object）；`__init__` 删除 `_prior_evaluations` 装配段；类尾新增 **1 方法转发桩** `_process_candidate` + **1 属性 property 转发含 setter** `_prior_evaluations`；**C 阶段 9 协作类全部组合持有**（_uct_selector/_candidate_prefilter/_audit_pipeline/_trace_recorder/_factor_reviewer/_evolution_channels/_seed_manager/_elite_store/_candidate_processor）
- ✅ 公开 API 与行为等价不变；25+ 测试文件零改动（batch_mining 调用 `loop._process_candidate` 走转发桩）
- ✅ 验证：`analyze_evolution_loop.py` 基线（行数 2110→2153、方法数 138→141）+ 受影响测试 **314 passed** + ruff/mypy 通过 + **里程碑 not-slow 全量回归通过（6617 passed）**
- ✅ **34 计划 C 阶段（Phase 47a-47i）全部完成，GAP-099 进入验收**：`evolution_loop.py` 5117 行/62 方法 → 2153 行/141 方法（逻辑 + 转发桩/property），9 协作类可独立构造/测试
- 📋 里程碑回归处置（4 failed → 3 修复 + 1 登记）：① `test_symbol_holdout::test_run_factor_audit_wires_symbol_ic_and_holdout`（`object.__new__` 绕过 __init__ 场景）——`auditor` property + `_run_factor_audit` 转发桩补 `_audit_pipeline` 缺失 fallback（47c 先例），`AuditPipeline._run_factor_audit` 内部 `self._owner` → `owner = getattr(self, "_owner", self)`（15 passed）；② `test_package_init::test_version_format`——v2.103.0 SemVer build 段制（`x.y.z[+N]`）未适配，测试改为剥离 build 段校验（3 passed）；③ `test_read_while_writer_open`——E.4 S1 read_only 读池与旧「读池用普通连接」测试设计冲突（写 lock_configuration + 读 read_only 同文件配置不兼容），非本次重构引入，登记至 GAP-090 遗留（P2 级，待适配）

### v2.104.0 里程碑：35-gap-closure 全链路缺口关闭完成（G1–G17）（已完成）

**完成时间**: 2026-08-13

**核心产出**（[plans/35-gap-closure-plan.md](plans/35-gap-closure-plan.md) 全链路缺口关闭 SOP 对齐 + 34 计划 C 阶段收官合流）:
- ✅ **P0 批次（G1-G3）**：同向敞口惩罚（check_aligned_exposure）+ 集中踩踏止损规避（throttle_exit_stampede）+ 换手全局上限（turnover_budget）全部实现 + 测试（v2.103.0+9/+17）
- ✅ **P1 批次（G4-G7）**：ICIR/符号反转硬门槛 + Bootstrap CI + ADF 平稳性 + 5-Regime 拆分检验实现 + 测试（v2.103.0+10），阈值经 §9.1 校准（icir_min=0.30）
- ✅ **P2 批次（G8-G15）**：交易日历/断K清洗 + MAD + 波动率/季节中性化 + 日换手硬剔除 + 信号统一契约 + 月度调度 + Regime 风控参数 + 方差最小化仓位实现 + 测试（v2.103.0+15，55 用例），GAP-106~113 登记关闭
- ✅ **G16 LLM 审核**：批次末评估=**维持暂缓（不启用）**——G4-G7 已覆盖增量拦截、AP06 约束、角色边界、G17 前置（§5.9 记录启用条件）
- ✅ **G17 柜台对接**：FDT 交接——FTS 交付 G12 信号契约 + Order/OrderLifecycle + SimulatedGateway 对拍基准（§5.10）
- ✅ **34 计划 C 阶段收官合流**（Phase 47a-47i，并发会话）：`evolution_loop.py` 9 Mixin → 协作类（继承链清零，6621 用例基线）
- ✅ **里程碑全量回归**：`pytest tests/ -m "not slow"` **6621 passed / 0 failed / 26 deselected**（25 分 35 秒，2026-08-13）；3 个回归失败全部处置——test_symbol_holdout（auditor property fallback）+ test_package_init（SemVer build 段适配，均为并发会话修复）+ test_read_while_writer_open（**本里程碑修复**：E.4 S1 语义适配，`test_read_after_writer_closed` 验证写短生命周期后读可打开，关闭 GAP-090 遗留项）
- ✅ **文档同步**：35 计划头部状态（全部完成+验收）+ 08（GAP-106~113 关闭 + GAP-090 遗留关闭）+ 06（P2 55 用例 + 修复注记）+ 07（v2.104.0 版本历史）+ 01/09（34 计划 C 阶段收官）

### E.3 S2 L4 状态库 SQLite 化（2026-08-13，日常开发，未 bump）

**完成时间**: 2026-08-13

**核心产出（design/E.3 实施，E.2 推荐路线 S1+S2 之 S2）**:
- ✅ `fts/store/state_db.py` `StateKVStore` 后端 DuckDB → **SQLite WAL**：写连接存活期间外部只读不阻塞（WAL 多读单写，解决演化进程持锁连只读亦被锁）；upsert 单事务双表原子；seq AUTOINCREMENT 单调；**API 契约不变，5 个调用模块零改动**
- ✅ 新增 `scripts/migrate_state_to_sqlite.py`（迁移 + 行数校验 + 幂等保护 + --force 覆盖 + 源库锁占用降级拒绝，旧库保留冻结期）
- ✅ storage_landscape `run_state` 域 backend=sqlite / path=data/state.db
- ✅ tests **33 passed**（test_state_db 14 + test_migrate_state_to_sqlite 6 + registry 13）+ 调用方回归 **74 passed** + ruff check 全绿 + mypy 2 文件 Success
- 📋 遗留：旧 `data/state.duckdb` 冻结期（≥1 发布周期）后按 plans/29 约定清理；**S1（L2/L3 DuckDB 库写连接短生命周期 + filelock 跨进程写互斥）已实施（见 E.4 S1）**

### v2.86.0 DuckDB 并发模型根治（GAP-056，数据基础设施，已完成）

**完成时间**: 2026-08-10

**核心产出（design/E.1 实施）**:
- ✅ `fts/data_futures.py` 新增 `DuckDBWriter`——单写者（唯一可写连接 + 进程内写锁串行）；`executemany`/`copy_from_records` 显式 BEGIN/COMMIT 包裹（DuckDB executemany 为逐条执行非单事务），任一条失败整批 ROLLBACK 不留半写入；批量 COPY 降低 commit 频率减少 checkpoint 阻塞
- ✅ 新增 `DuckDBReader`——读连接池（普通连接复用，池满关闭；MVCC 快照使写提交期间读侧不阻塞；DuckDB 不允许同文件并存可写 + read_only=True 连接，读语义由代码纪律保证）
- ✅ `_get_db()` 拆分为 `_get_writer()`/`_get_reader()` + `_release_reader()`，迁移 3 个调用点（`_from_kline_cache`/`get_dominant_contracts` 读 → reader，`_write_contract_kline` 写 → writer），`_get_db` 保留兼容入口
- ✅ `FTSConfig` 新增 4 配置项（`duckdb_single_writer`/`duckdb_read_pool_size`/`duckdb_batch_size`/`duckdb_commit_every`，`FTS_DUCKDB_*` 环境变量）
- 📋 新增 test_duckdb_writer.py 10 用例（8 线程并发写零冲突/executemany 唯一约束冲突整批回滚/批量 COPY 与逐条一致/错误恢复）+ test_duckdb_reader.py 5 用例（池复用/池满关闭/写连接打开时读共存）+ test_config_settings 5 用例
- ✅ 文档同步：01（并发模型架构块）/03（配置）/04（并发韧性）/06（用例）/07/08（GAP-056 ✅ 关闭）+ design/E.1 + pyproject bump v2.85.0 → v2.86.0

### v2.61.0 股票流水线 GAP-S01（行业/市值中性化主流程，已完成）

**完成时间**: 2026-08-09

**核心产出**:
- ✅ GAP-S01（GAP-I207）股票截面因子行业/市值中性化主流程：`EvolutionLoop(market="stock")` 自动加载 `industry_map.json` + `cap_map`（`stock_neutralization` 默认 true，接通死配置），键归一化（`.SH/.SZ` 后缀 → 裸代码兼容面板 symbol），透传 `cross_section_evaluate_backtest` 做行业去均值 + 市值加权去均值，报告输出中性化前后 IC 对比
- ✅ 配套测试：股票自动注入启用/关闭/键归一化/空映射降级 + 中性化前后 IC 对比
- ✅ 文档同步：01/06/07/08/09 + pyproject bump v2.60.0 → v2.61.0

### v2.62.0 股票流水线 GAP-S02（Barra 风格因子体系，已完成）

**完成时间**: 2026-08-09

**核心产出**:
- ✅ GAP-S02（GAP-I304）Barra 风格暴露计算引擎：`fts/factor_engine/barra/barra_style.py` 实现 10 大风格（size/beta/momentum/residual_vol/nonlinear_size/book_to_price/liquidity/earnings_yield/growth/leverage），逐日截面 rank→z-score 标准化；nonlinear_size 基于 size 暴露矩阵逐日 z³ 对 z 回归残差（截面依赖因子引擎层二次计算）；字段缺失全 NaN 降级
- ✅ Barra 截面中性化器：`fts/factor_engine/barra/barra_neutralizer.py` 逐日 OLS（`np.linalg.lstsq`）风格暴露 + 行业虚拟变量回归取残差，样本不足降级去均值、常数列剔除、正交性保证
- ✅ 评估链集成：`cross_section_evaluate_backtest` 新增 `style_exposures` 参数 + Step 2.6 Barra 风格回归残差（行业去均值后叠加风格剥离，两级中性化链 GAP-S01/S02）
- ✅ 配套测试：`tests/factor_engine/test_barra.py` 13 用例（10 风格齐全/形状/size 单调/残差与风格正交/行业+风格叠加/小样本降级等）
- ✅ 文档同步：01/06/07/08/09 + pyproject bump v2.61.0 → v2.62.0

### v2.65.0 GAP-I201 批量挖掘漏斗（Stage 1 首版，已完成）

**完成时间**: 2026-08-09

**核心产出（细则见 [design/D.1-batch-mining-design.md](design/D.1-batch-mining-design.md) + plans/23 GAP-I201）**:
- ✅ 新增 `fts/factor_engine/batch_mining.py`：`BatchMiner` 批量漏斗（批量生成 → ThreadPoolExecutor 并行粗筛 → 按预筛 IC 排序截断 ≤ max_candidates）+ `BatchMiningConfig`（batch_size=20/max_candidates=5/max_workers=4/random_seed）+ `BatchedProposal`/`BatchGenerationResult` 契约，依赖注入回调（generate/runtime_check/prefilter）零业务耦合
- ✅ `evolution_loop.py` 抽取公共方法 `_evolve_one`（演化分派，支持 method_hint + seed，原 run() Step 1 平移）与 `_process_candidate`（Step 2-6 准入链，batch 与单因子路径共用），新增 `_run_batch_generation`（同父多后代方法轮换：macro 至多 1 次 + GP/operator 交替 + seed 递增；token 护栏；全失败回退单因子路径）与 `_batch_generate_one`/`_batch_prefilter`；`_quick_prefilter` 返回 (ok, reason, ic) 三元组
- ✅ `settings.py` evolution_mode 新增 batch + `batch_size`/`batch_max_candidates`/`batch_max_workers`/`batch_random_seed` 配置（`FTS_BATCH_*` 环境变量）
- ✅ 设计文档 `design/D.1-batch-mining-design.md` + 新增 21 用例（test_batch_mining.py 11 + test_evolution_loop batch 集成 10），关键回归全绿
- ✅ GAP-I201 关闭 + 文档同步 01/02/03/06/07/08/09 + pyproject bump v2.62.0 → v2.65.0

### v2.65.0 股票流水线 GAP-S03（A 股行业轮动 + 风格轮动 Regime 检测，已完成）

**完成时间**: 2026-08-09

**核心产出**:
- ✅ GAP-S03（GAP-I301 Regime 子项）新增 `fts/factor_engine/stock_regime.py`：`StockRegimeSelector` —— 行业轮动维度（申万行业动量横截面离散度 → rotation_strength + top-N 集中度 → concentrated/rotating/balanced 三态）；风格切换维度（大小盘比值 + 成长价值比值动量方向 → large_cap/small_cap + growth/value 双态），复用 `regime_hmm.MultiHorizonHMMDetector` 多周期集成（比值序列构造合成 OHLCV 送 HMM 校正置信度，规则动量方向主判定），空面板/样本不足优雅降级
- ✅ L3 集成：`REGIME_STYLE_MULTIPLIERS` 新增 6 个股票风格键（large_cap/small_cap/growth/value/sector_concentrated/sector_rotating）；`PortfolioLoop.run()` 新增 `stock_regime` 可选参数，market="stock" 时 Step 2.5 优先使用 StockRegimeSelector 结果驱动风格自适应权重
- ✅ 配套测试：`tests/factor_engine/test_stock_regime.py` 19 用例（行业三态/风格四方向/风格切换样本正确率 ≥80%/空面板降级/HMM 复用回退/multipliers 键与值域/PortfolioLoop 集成 2）
- ✅ 文档同步：01/02/06/07/08/09 + pyproject bump v2.62.0 → v2.65.0

### v2.68.0 L3/L4 专项收尾（GAP-L308 Regime 数据化 + GAP-L309 面板规模，已完成）

**完成时间**: 2026-08-10

**核心产出（细则见 [plans/24-l3-l4-institutional-plan.md](plans/24-l3-l4-institutional-plan.md)）**:
- ✅ GAP-L308 Regime 权重数据化：新建 `regime_multipliers.py`（`RegimeMultiplierEstimator` 按 regime×family 聚合 IC 均值/胜率生成数据驱动倍率，钳制 [0.5,1.5] + 最小样本回退）；修复 family_global 跨 regime 桶被覆盖 bug；倍率表落盘 `docs/harness/_data/l3_regime_multipliers.yaml`（易变配置进 _data 原则）；`portfolio_loop.load_data_driven_multipliers` 优先接线（数据驱动表存在时优先、缺失回退硬编码）
- ✅ GAP-L309 面板数据规模参数化：新建 `PanelLoadingConfig`（默认全 CSI300 子集 × 500 天，对齐 MIN_EVAL_DAYS）；`_liquidity_stratified_sample` 流动性分层抽样（桶间轮询保证高低流动性覆盖，无 volume 退化等权）；`_load_panel_with_liquidity_sampling` 覆盖日志 + 幸存者偏差提示；`_compute_elastic_net_weights`/`_compute_ml_ensemble_weights` 默认 days 120→500、max_stocks 50→0（全量）
- ✅ GAP-L310 种子加载链修复：`seed_loader.py` 补 `FactorKind` 导入 + 多行 `field_defs` strip+统一缩进 + 测试引用迁移 `estimate_lookback_static` + 种子计数断言同步 714/898/30（全量回归 21 例失败清零）
- ✅ 同步关闭 08 中 L305/L306/L307/L401/L402 已落地项状态（v2.66.0 完成）
- 📋 新增测试 26 用例（test_regime_multipliers 14 + test_data_provider_panel 12）；受影响模块 230 测试回归全绿

### v2.69.0 股票流水线成熟度收尾（GAP-S09/S10/S11/S12，13 项缺陷闭环，已完成）

**完成时间**: 2026-08-10

**核心产出（细则见 [plans/22-stock-pipeline-maturity-plan.md](plans/22-stock-pipeline-maturity-plan.md)）**:
- ✅ GAP-S09 种子表达式静态 PIT 审计：新建 `fts/factor_engine/expr_dsl/seed_analyzer.py`（WQ 风格表达式递归下降解析器，静态提取 max_lookback/fields/operators/depth），`seed_loader` 与 `seed_data.loader` 改走 `estimate_lookback_static`（替换正则粗糙估计），705 表达式全量扫描仅 1 个 fundamental 切片语法需显式 lookback
- ✅ GAP-S10 双注册表一致性：`verify_registry_consistency()` 重叠算子（20+）同输入断言输出一致（rtol 1e-6），`expr_dsl/__init__` 导出
- ✅ GAP-S11 股票演化 operator-first：`evolution_mode` 新增 `operator_first`；`EvolutionLoop` 股票默认 operator_first（算子 → LLM → GP 三层兜底）；`record_evolution_method` 演化方法分布记账
- ✅ GAP-S12 A 股特有算子：`A_SHARE_FIELDS` 10 字段（L0 访问器）+ L5b 4 领域算子（nb_momentum/margin_change/holder_concentration/analyst_revision_ratio）
- 📋 新增测试 27 用例（test_seed_analyzer 14 + test_registry GAP-S10/S12 6 + TestGapS11OperatorFirst 7）；修正 test_seed_loader 股票种子计数 645→714
- ✅ 文档同步：06/07/08/09 + 22 计划（13 项全部 ✅）+ pyproject bump v2.68.0 → v2.69.0

### v2.70.0 股票 L3 组合层 + 微演化两阶段漏斗（GAP-I301 + GAP-I205，Stage 1B 收官，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I301 + GAP-I205）**:
- ✅ GAP-I301 股票 L3 组合层：CLI `_cmd_portfolio_run` 股票分支对称触发——`portfolio run --universe stock` L3 完成后自动调用 `scripts.daily_signal_pipeline.main(max_stocks=50, days=120)`（此前仅期货分支触发 futures_signal_pipeline；状态集 passed/verifier_warning/completed 一致、非零 rc 打印告警降级）；组件复用性确认：`PortfolioLoop(market="stock")` + `load_elite_factors(market="stock")` market 过滤 + `synthesize_signals`（equal_weight/sharpe_weight/elastic_net/adaptive）+ Step 2.5 `stock_regime` 风格自适应（GAP-S03）+ `build_combo(market="stock", cost_config)` 多头组合 net 指标
- ✅ GAP-I301 配套测试：`TestStockL3PortfolioLayer` 6 用例（load_elite_factors market 过滤/synthesize_signals 组件复用/build_combo 成本模型 net 为正/无成本 None/PortfolioLoop stock run/stock_regime 驱动）+ `TestCmdPortfolioRunStock` 3 用例（stock 触发信号管道/非零 rc 告警/状态不触发）
- ✅ GAP-I205 微演化两阶段漏斗：`micro_evolution.py` 新增 `optimize_params_staged`——粗筛低 trials（默认 20）随机搜索快速打分，得分低于 `COARSE_IC_FLOOR`（0.02）直接淘汰（passed=False）；通过者进入精筛，trials 按粗筛得分自适应（得分达 `COARSE_REF_IC` 0.10 跑满 n_trials）+ TPE 早停（早停机制既有）；`evolve_micro` 新增 `use_staged` 参数，`EvolutionLoop` 接入并默认启用（`settings.py` 新增 `micro_staged_evolution`/`micro_coarse_trials`/`micro_coarse_ic_floor` 配置 + FTS_MICRO_* 环境变量）
- ✅ GAP-I205 配套测试：`TestStagedFunnel` 5 用例（粗筛淘汰/精筛通过/no-optuna 回退/evolve_micro staged 与非 staged）
- ✅ 修复预存 bug：`evolution_loop.py` L325 `get_config()` 仅在 `market is None` 分支内导入，显式传 market 时 UnboundLocalError（GAP-S11 引入）——提前模块级导入修复（TestShadowPool 6 用例恢复通过）
- ✅ 文档同步：01/06/07/08/09 + 23 计划（GAP-I301/GAP-I205 ✅ 关闭，v2.70.0）+ pyproject bump v2.69.0 → v2.70.0

### v2.71.0 L2 准入去冗余/正交化闭环（GAP-I206，Stage 1C，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I206）**:
- ✅ `evolution_loop.py` 新增 `_check_elite_correlation`：演化因子晋升前扫描既有 elite 快照（排除 `_l2_seed_correlation_index.json`，容量护栏 `l2_elite_corr_max_scan` 默认 50），复用 `BacktestPipeline._execute_factor_code` 逐个计算信号与候选因子做 Pearson 相关，相关绝对值 ≥ `l2_elite_corr_threshold`（默认 0.9）记录高相关对（`factor_name_b`/`factor_id_b`/`pearson`/`abs_pearson` 按 abs 降序）并拒绝晋升（打拦截日志）；无既有 elite / 新因子执行失败 / 全低相关返回 None 静默放行（首次晋升场景不阻断）
- ✅ `_promote_to_elite` 接入：`shadow_observe=True`（演化因子）命中高相关即返回 None 不落盘；种子因子（`shadow_observe=False` 首轮导入）跳过检查全量放行
- ✅ `settings.py` 新增 `l2_elite_corr_threshold`/`l2_elite_corr_max_scan`/`l2_elite_corr_debug` 配置 + `FTS_L2_ELITE_CORR_*` 环境变量（异常回退默认值）
- ✅ 配套测试：新增 `tests/factor_engine/test_l2_elite_redundancy.py` 10 用例——方法级 7（高相关命中/负高相关 abs 判断/低相关放行/空 elite 放行/索引文件跳过/容量护栏/执行失败容错）+ 集成 3（shadow 高相关拦截不落盘/种子跳过检查正常晋升/低相关正常晋升），11 passed 全绿
- ✅ 文档同步：03/06/07/08/09 + 23 计划（GAP-I206 ✅ 关闭，v2.71.0 追加记录）+ pyproject（v2.71.0，与并发会话同版本追加）+ README

### v2.71.0 GP 多目标适应度首期（GAP-I204，Stage 1C，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I204 首期）**:
- ✅ `gp_evolver.py` 新增 `multi_objective` 适应度模式：`FitnessResult` 扩展 `turnover`/`decay` 字段——换手=信号逐日绝对变化均值/信号标准差（无量纲归一，衡量调仓频繁度，联动 GAP-I501 冲击成本）；衰减=训练集按时间等分两半分别算 |IC| 的前段相对后段衰减比例（0=无衰减 1=完全衰减，联动 GAP-I305 衰减退役）
- ✅ `GPEvolverConfig` 新增 `turnover_penalty`（默认 0.3）/`decay_penalty`（默认 0.3）系数；合成适应度 `fitness = |ic|×0.6 + max(sharpe,0)×0.2 − turnover_penalty×min(turnover,5) − decay_penalty×decay`——高换手、快衰减表达式被压低排名，抑制实盘成本侵蚀
- ✅ `GPEvolveResult` 新增 `best_turnover`/`best_decay`（最优因子换手/衰减指标随演化结果输出，日志同步），`_evaluate_best_metrics` 扩展返回 4 元组
- ✅ 默认 `ic_sharpe_combo` 模式保持原逻辑不变，但同样填充 turnover/decay 指标供报告
- ✅ 配套测试：`TestGapI204MultiObjective` 7 用例（FitnessResult 字段填充/换手度量平滑 vs 振荡/同 IC 量级换手惩罚/系数放大 ×2/衰减惩罚/端到端 evolve），test_gp_evolver 54 passed + test_evolution_loop GP 相关 9 passed
- ✅ 文档同步：06/08/09 + 23 计划（GAP-I204 首期 ✅ 关闭，v2.71.0 追加记录）+ README（Pareto 前沿/符号回归留二期 v2.78.0）

### v2.71.0 实盘反馈闭环（GAP-I401，Stage 1D，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I401）**:
- ✅ ①② 由 GAP-L402（v2.66.0）落地：`LiveFeedbackRecord` 契约（factor_id/signal_date/signal_value/position_return/turnover/slippage/market）+ `validate_live_feedback_record` + `LiveFeedbackImporter`（CSV/dict 批量导入 + DuckDB `feedback_live` 表追加落盘 + 截面 Spearman 实盘 IC）+ `LiveVsBacktestICReport`（实盘 vs 回测 IC 对比 + 衰减判定 decayed/weak/ok）+ CLI `fts feedback import`/`fts feedback live-ic`
- ✅ ③ 补强（本次）：`LiveVsBacktestICReport.generate` 输出 `recommend_retire`（status=decayed → True）/`decay_gap`（|回测 IC|−|实盘 IC|）字段与 summary `n_recommend_retire`——衰减因子携带退役建议，供 GAP-I305（Stage 2 v2.76.0）自动退役闭环消费
- ✅ 配套测试：test_feedback_loop 25 passed（扩展对比报告测试断言 recommend_retire/decay_gap/n_recommend_retire）
- ✅ 文档同步：08/09 + 23 计划（GAP-I401 ✅ 关闭，v2.71.0 追加记录）+ README

### v2.78.0 符号回归补充搜索 + Pareto 前沿输出（GAP-I204 二期，Stage 2 2C，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I204 二期）**:
- ✅ 新建 `fts/factor_engine/pareto.py`——`ParetoItem`/`fast_non_dominated_sort`/`compute_pareto_front`（NSGA-II 快速非支配排序，多目标 \|IC\|/Sharpe/−turnover/−decay 统一「越大越好」口径，前沿按 fitness 降序供人审）
- ✅ 新建 `fts/factor_engine/symbolic_regression.py`——`SymbolicRegressionSearcher` 确定性 beam-search 层级搜索（单字段出发逐层一元包装 + 二元组合，复用 `GPEvolver._evaluate_fitness` 同口径多目标评估，每层保留 top-K，固定种子可复现，max_candidates 防组合爆炸），配置 `SymbolicRegressionConfig` 化
- ✅ `gp_evolver.py` 扩展：`GPEvolver.evolve()` multi_objective 模式跟踪全部已评估个体 → 提取 Pareto 前沿；`GPEvolveResult` 新增 `pareto_front` 字段（source=gp/symbolic）；`GPEvolverConfig` 新增符号回归补充搜索配置（默认关闭，不改变默认行为）
- ✅ 配套测试：`test_pareto.py` 12 用例 + `test_symbolic_regression.py` 15 用例（含 GP 集成：symbolic 前沿合并、multi_objective 前沿输出），GP 回归 54 passed 全绿
- ✅ 文档同步：01/06/07/08/09 + 23 计划（GAP-I204 二期 ✅ 关闭）+ pyproject v2.77.0→v2.78.0 + README

### v2.77.0 在线因子性能监控（GAP-I402，Stage 2 2C，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I402）**:
- ✅ `fts/monitor/live_factor_monitor.py` 新增 `ingest_live_ic(live_ic_result, backtest_ic_map, decay_status_map)`——消费 GAP-I401 实盘反馈数据源（`LiveFeedbackImporter.compute_live_ic` 输出 + `LiveVsBacktestICReport.generate` 的 status 字段），自动构建因子回测基线/实盘 IC 并触发偏离检查（`set_backtest_baseline`/`update_live_performance` 保留兼容）
- ✅ 衰减监控：`_decay` 状态存储（ok/weak/decayed）+ `set_decay_status`/`get_decay_status` + `_check_decay_alerts`（decayed → critical「衰减退役建议（GAP-I305 闭环）」/ weak → warning「持续观察」，`decay_alert_enabled` 可关）
- ✅ Prometheus 兼容指标日志：偏离告警 `METRIC live_factor_ic{factor_id=..}`、衰减告警 `METRIC live_factor_decay{factor_id=..,status=..} 1`
- ✅ 配套测试：`tests/monitor/test_live_factor_monitor.py` 12 用例（既有偏离检查 5 + ingest_live_ic 6 + GAP-I401 端到端对接 1），monitor+feedback 253 定向回归全绿
- ✅ 文档同步：06/07/08/09 + 23 计划（GAP-I402 ✅ 关闭）+ pyproject v2.75.0→v2.77.0 + README

### v2.75.0 算子库扩充——组合/跨标的算子单一事实源（GAP-I202，Stage 2 2B，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I202）**:
- ✅ `fts/factor_engine/feature_ops.py` `RollingOps` 新增 `ts_slope`（滚动线性回归斜率，局部趋势强度/方向，NaN 安全降级）与 `ts_quantile`（滚动分位数，q∈[0,1] 越界抛 ValueError）
- ✅ `feature_ops.OperatorRegistry`（GP 演化侧）注册 8 个组合/跨标的算子（combo 类目）：ts_slope/ts_quantile + GAP-L401 的 regression_residual/quantile_bucket/cross_section_demean/if_else/corr/cross_section_rank——与 expr_dsl 共用 RollingOps/PriceOps 底层原语，双轨漂移消除（此前 L4 组合算子仅 expr_dsl 侧，GP 侧不可用）
- ✅ `expr_dsl/registry.py` 注册 ts_slope/ts_quantile（L1，参数边界 + PIT lookback + 经济语义）；`verify_registry_consistency` 新增 `required_shared` 硬约束（8 个组合/跨标的算子必须双注册表共享，仅存在于单侧即判不一致，输出 `unshared_required`）
- ✅ `operator_evolution.py` `_evaluate_fitness` 新增 lookback=0 罚分（`compute_max_lookback==0` 纯字段/无算子表达式如 `rank(close)`，与常信号罚分同档 _PENALTY_WEAK）——算子演化产物必须包含实际算子变换，避免裸字段包装在单调合成数据上以虚假高 IC 占据最优
- ✅ 配套测试：新增 7 用例（test_registry.py：ts_slope/ts_quantile 元数据/功能/边界 + GP 注册表含组合算子/可调用 + required_shared 一致性 + DSL 执行）；315 定向回归全绿（expr_dsl + operator_evolution + evolution_loop + gp_evolver）
- ✅ 文档同步：01/06/07/08/09 + 23 计划（GAP-I202 ✅ 关闭，能力矩阵 L65 算子库 T2 ✅ 达标）+ pyproject v2.73.0→v2.75.0 + README

### v2.74.0 组合优化器机构化核实关闭（GAP-I302，Stage 2 2A，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I302）**:
- ✅ 诊断确认核心能力（`RiskModelEstimator` Ledoit-Wolf 收缩协方差 + `PortfolioOptimizer` risk_parity/mvo）已由 GAP-L302/L303/L304/L305（v2.61.0）落地，剩余缺口为「优化器参数未走配置」
- ✅ `settings.py` `FTSConfig` 新增 `portfolio_optimizer_mode`（默认 risk_parity，env `FTS_PORTFOLIO_OPTIMIZER_MODE`）；CLI `_cmd_portfolio_run` 读取 `--optimizer-mode`/配置默认 + `--returns-matrix` CSV 加载传入 `loop.run(factor_returns=...)`；`portfolio run --synthesis-mode optimizer` 生效
- ✅ 配套测试：新增 5 用例（test_cli_extra 3：optimizer 模式透传/配置默认值/returns-matrix 加载 + test_config_settings 2：默认值与 env 覆盖）
- ✅ 文档同步：03/08 + 23 计划（GAP-I302 ✅ 关闭）+ 07

### v2.73.0 深度因子学习（GAP-I203，Stage 2 首项，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I203）**:
- ✅ `fts/ml/models.py` 新增 `GRUFactorModel` 轻量纯 numpy 单层 GRU：update/reset gate + candidate hidden，BPTT + 动量 SGD + L2 正则；输入 (n, seq, f) 滚动窗口序列，训练前 z-score 标准化；样本不足/非数值/维度不匹配/未训练抛 `ModelNotAvailableError` 降级；`get_params` 导出 11 组权重供因子 code 内嵌；`create_gru_model` 工厂；修复 numpy 2.x 标量转换（`float()` → `.item()`）
- ✅ `fts/ml/deep_factor.py` 新增 `DeepFactorGenerator`/`create_deep_factor`：OHLCV 特征（日收益率+量变化率）→ 滚动窗口样本 → 前 train_ratio 训练 GRU → 权重序列化内嵌 `def factor_program(data, params)` code——零未来函数（特征窗口 [t-lookback+1, t] 逐 t 滚动推理 + tanh 压缩 ∈ [-1,1]），factor 契约完整（factor_id/name/code/signature/economic_logic/source=deep_evolution/family=deep/deep_model 元数据含 val_ic），样本不足/训练失败返回 None 降级
- ✅ `evolution_loop.py` L2 接线：`_evolve_one` 新增 method_hint="deep" 分派（`_run_deep_evolution`：数据/样本校验 → create_deep_factor → parent_id/generation/trace_id 血缘回填，失败抛 RuntimeError 由调用方降级回退）；`_batch_generate_one` 批次轮换并入 deep（idx%3==2，macro/gp/deep/operator 循环）——深度因子作为候选源接入 L2 批量漏斗（GAP-I201）过全套审计链
- ✅ 配套测试：`tests/test_gru_factor.py` 28 用例（GRU 模型级 11 + DeepFactor 生成器集成 9 + EvolutionLoop 接线 8），含零未来函数截断一致性验证、生成 code 经 `_execute_factor_code` 可执行验证、批次轮换断言；定向回归 50+28 passed 全绿
- ✅ 文档同步：06/07/08/09 + 23 计划（GAP-I203 ✅ 关闭，GAP-037 首期关闭）+ pyproject v2.72.1→v2.73.0 + README

### v2.72.1 L2 正交基底维护 + 因子衰减自动退役闭环（GAP-I206 补充 + GAP-I305，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23）**:
- ✅ GAP-I206 补充——新建 `fts/factor_engine/orthogonal_basis.py`（`OrthogonalBasisManager`）：Gram-Schmidt 多因子正交基底，基底 = 按 Sharpe 降序保留上限（默认 10）的两两近似正交精英因子，索引持久化 `{memory_dir}/orthogonal_basis.json`；`evolution_loop._orthogonalize_via_basis` L2 准入优先对候选信号关于基底逐因子 OLS 残差化（迭代投影），残差与基底最大相关 < 0.3 且保留比 > 0.3 时以正交化版本入库并注册为新基底成员（`orthogonalized_basis` 元数据），基底不可用/失败回退单参照 OLS；DuckDB metadata 与 L3 `load_elite_factors` 透传 `orthogonalized_basis`（L3 不重复剔除）
- ✅ GAP-I305——`elite_tracker.py` 滚动 6M IC 线性回归斜率 `_calc_ic_slope_6m`（归一化 [-1,1]）+ 衰减分级 `decay_grade`（normal/observe/retired，`observe_slope` 0.10 / `retire_slope` 0.20）写入快照；`auto_retire()` 纳入 `decay_grade=="retired"` 退役条件；`AutoRetireConfig`/`AutoRetireManager` 分级阈值配置同步
- ✅ GAP-I305——`evolution_loop._run_periodic_factor_review` 接线 FeedbackLoop FACTOR_DECAY 事件（observe/retired 触发归因分析，`last_feedback` 写回快照），退役受 `decay_auto_retire_enabled` 开关控制；`monthly_decay_eval_job` 按斜率迁移状态 + `retire_factor` 回写 DuckDB/JSON + 报告
- ✅ 配套测试：`tests/factor_engine/test_orthogonal_basis.py` 19 用例（IC 斜率 5 + 衰减分级 5 + 基底管理器 7 + L2 集成 2），test_elite_tracker/test_l2_orthogonalize/test_l2_elite_redundancy 全绿
- ✅ 文档同步：03/06/07/08/09 + 23 计划（GAP-I206 补充/GAP-I305 ✅ 关闭，能力矩阵 L64 去冗余/L67 衰减管理 T2/T3 ✅ 达标）+ pyproject（v2.72.1，与并发会话同版本追加）+ README（4543+ 测试用例）

### v2.72.0 L1 批量候选 + 审查工作流骨架（GAP-I101/I102 首期，Stage 1D，已完成）

**完成时间**: 2026-08-10

**核心产出（总纲 plans/23 GAP-I101/I102 首期）**:
- ✅ GAP-I101——`meta_loop.py` 新增 `validate_batch_candidates` 批量候选契约校验：逐条校验 SeedCandidate 必需字段（candidate_id/name/code/economic_logic.narrative），输出 total/valid/invalid/invalid_samples 统计（invalid_samples 截断 5），接入 `_run_bootstrap` 前置质量门（契约不合规仅告警不熔断，不阻断注入链路）
- ✅ GAP-I101——`MetaRunResult` 新增 `candidates_per_minute` 吞吐指标（候选数 / 运行分钟，`_make_result` 计算，elapsed=0 防除零）——L1 注入吞吐可监控，对齐机构级标准③
- ✅ GAP-I102——`factor_inspector.py` 新增 `FactorReviewWorkflow` 审查工作流：`ReviewDecision` 状态机（pending→approved/rejected）+ `approve`/`reject` 意见回写 DuckDB `factor_reviews` 表（幂等 UPSERT，重复审查覆盖旧决定）+ `list_pending` 待审查队列（NOT EXISTS 排除已审查 + market 过滤 + limit 上限 + created_at 倒序）+ `get_status` 状态查询
- ✅ GAP-I102——CLI `fts factor review list/approve/reject` 子命令（--market/--limit/--db/--comment）；schema E.1 `_CREATE_FACTOR_REVIEWS` 表（factor_id 主键/decision/comment/reviewer/reviewed_at + decision 索引）
- ✅ 配套测试：`TestValidateBatchCandidates` 8 用例（全合法/空列表/缺必填字段 ×3/非 dict/样本截断/吞吐计算/零耗时）+ `TestReviewCliCommands` 4 用例（list 队列/market 过滤/approve 回写/reject 回写）补强 test_review_workflow 7 用例（状态机/回写/队列/幂等/意见落盘），共 11 passed 全绿
- ✅ 知识源多路扩展（GAP-I101 ①）与审查意见接入经验链（GAP-I102 ③）留二期 v2.80.0
- ✅ 文档同步：06/08/09 + 23 计划（GAP-I101/I102 ✅ 关闭，v2.72.0）+ pyproject v2.71.0→v2.72.0 + README

### v2.66.0 GP/operator 通道修复三连 + 横截面预筛真实化（GAP-X01/X02/X03，已完成）

**完成时间**: 2026-08-09

**核心产出（处理中，登记 08-gap-analysis GAP-X01/X02/X03）**:
- ✅ GAP-X03 `eval_fts_expr 未定义` 根因修复：`BacktestPipeline._execute_factor_code` exec 后 `exec_globals.update(local_vars)`（模块级 import 绑定并入 `factor_program.__globals__`，与 `FactorExecutor.compile` 同模式）；另修复 GP 模板 `ts_product` 改用 `rolling.apply(np.prod)`（pandas≥2.1 移除 `Rolling.prod`）+ `_evaluate_fitness` 后处理对齐流水线（`nan_to_num` + `clip[-10,10]` + std<1e-12 常数罚分）——operator/GP 因子不再全数降零被「常数信号」拦截，CPU 演化通道恢复
- ✅ GAP-X02 operator 生成常数校验前移：`_generate_operator_factor` fallback 生成循环内 evaluate 表达式并过滤非常数信号（finite 为空或 nanstd<1e-8 拦截），10 次尝试全拦截抛 RuntimeError
- ✅ GAP-X01 横截面预筛真实截面收益：`_quick_prefilter` 横截面模式走新增 `_cross_section_prefilter`（全面板信号矩阵 vs 截面 forward 收益，复用 `_cs_execute_factors`/`_cs_build_matrices`/`_cs_compute_ics`，与 `cross_section_evaluate_backtest` 同口径），替代原单标的时序 IC
- ✅ 吞吐实测 `scripts/throughput_gp_channel.py`：operator 因子全链路通过率 0%→100%；GP 产物运行时校验通过率 1/3→3/3、单次耗时 6.15s→2.45s；batch 漏斗 0.4 候选/s
- ✅ 新增测试 6 用例（test_compiler 1 + test_evolution_loop 3 + test_gp_evolver 2），受影响文件回归全绿
- ✅ 文档同步：01/06/07/08/09 + pyproject bump v2.65.0 → v2.66.0

### v2.61.0 L3/L4 机构级追赶 A+B 阶段（因子收益序列 + 风险模型 + optimizer 接线，已完成）

**完成时间**: 2026-08-09

**核心产出（细则见 [plans/24-l3-l4-institutional-plan.md](plans/24-l3-l4-institutional-plan.md)）**:
- ✅ GAP-L301 因子收益序列层：`FactorReturnsBuilder`（因子多空组合收益序列）→ 组合夏普/相关性实测化（w×R 替代经验公式，metrics_source）
- ✅ GAP-L302 风险模型估计器：Ledoit-Wolf 收缩协方差 Σ（纯 numpy，正定性保证）
- ✅ GAP-L303 optimizer 接线：`PortfolioLoop.run()` 透传 factor_returns/exposure_matrix + `optimizer_mode`/`optimizer_config` + CLI `--mode optimizer`
- ✅ GAP-L304 暴露中性化：`OptimizerConfig.neutralization/exposure_tolerance` + SLSQP 暴露约束（\|B'w − target\| ≤ tol）
- ⏳ GAP-L305 冲击成本 + 换手惩罚（待后续批次，08 登记处理中）
- 📋 配套测试 28 用例（A 阶段 21 + B 阶段 7）+ 文档同步

### v2.59.0 期货流水线机构级缺陷修复（阶段 B，已完成）

**完成时间**: 2026-08-09

**核心产出**:
- ✅ GAP-F03（GAP-047）期货截面因子板块中性化主流程：`EvolutionLoop(market="futures")` 自动从 `FUTURES_SECTOR_MAP` 反向构建 `{symbol: sector}` 板块映射注入 `cross_section_evaluate_backtest`（industry_map），截面信号按板块去均值剥离产业链/板块系统性偏差，消除"伪预测力"；新增 `FTSConfig.futures_neutralization`（默认 true）
- ✅ GAP-F02（GAP-048）回测真实性仿真：`_compute_strategy_returns` 新增可交易掩码——涨跌停拦截（close 单日涨跌幅 ≥ `futures_limit_pct` 默认 8% 持仓保持）+ 停牌过滤（volume==0 持仓保持）；报告 summary 新增「被拦截成交统计」；新增 `FTSConfig.backtest_trade_filter`（默认 true）/ `futures_limit_pct`；`BacktestInput.trade_filter`/`limit_pct` 可选参数；配置关闭时跳过拦截回归兼容
- ✅ 登记并处理 GAP-047/048（机构级对标 plans/21-futures-maturity-optimization-plan.md）；新增 ~12 测试用例
- ✅ 文档同步：01/03/04/06/07/08/09 + pyproject bump v2.58.0 → v2.59.0

### v2.58.0 期货连续合约复权 + 展期仿真（阶段 A，已完成）

**完成时间**: 2026-08-09

**核心产出**:
- ✅ GAP-046 处理：期货主力连续合约换月复权（比率法后复权）——新增 `RollCalendar` 换月日历模块（`fts/data_sources/roll_calendar.py`），从 `contract_kline` 每日最大成交量判定主力、构建换月事件序列、计算复权因子；`migrate_schema` 幂等补 `kline_cache.adj_factor` 列 + 建 `contract_kline` 表（补写入逻辑到 `sync_futures_data_job`）
- ✅ 消费端启用：`FuturesDataProvider.get_ohlcv(adjusted=True)` 默认返回复权序列（因子计算消除换月跳空伪信号）；`contract_kline` 缺失时降级返回原始拼接序列（不阻断）
- ✅ 展期成本仿真：`TransactionCostModel` 新增展期成本项（`CostConfig.roll_cost_bps` 期货默认 2.0；`adjust(dates/roll_dates)` 持仓穿越换月日扣 `|position| × roll_cost_bps`；`AdjustedMetrics.roll_cost_bps` 统计字段），`BacktestPipeline` 持仓穿越换月日扣除展期价差（交易仿真用，与因子计算的复权序列分离）；报告新增「展期成本统计」
- ✅ 配置：`futures_adjusted`（默认 true）/ `roll_cost_bps`（默认 2.0）；新增 ~22 测试用例
- ✅ 文档同步：01/02/03/04/06/07/08/09 + pyproject bump v2.57.0 → v2.58.0；阶段规划落盘 `plans/20-futures-roll-adjustment-plan.md`（阶段 B P1 缺陷改进候选清单）

### v2.50.0 种子质检全链对齐 + 质检拦截器判定缺陷修复（已完成）

**完成时间**: 2026-08-09

**核心产出**:
- ✅ Phase 28 种子因子质检全链对齐：`_evaluate_and_promote_seeds` 补齐与演化因子完全同强度的质检链（新增 Verifier 判定、消融实验、因果结构审查、鲁棒性审查、SHAP 分析），L1 注入候选与人工精选种子一视同仁
- ✅ 消融实验判定语义修正（GAP-043）：`shuffle_dates`/成交量/VWAP 消融与核心价格列（open/high/low/close/vwap/settle）置零改为「信息型」判定（时序因子依赖时序因果、价格因子依赖价格列属必要特征），仅「非价格列」置零 IC 降幅 >50% 判伪相关拦截——解除 L2 期货演化 15 代 5 个通过 Verifier 候选（IC 0.31~0.52）全部被误杀导致的 100% 失败率熔断
- ✅ `_compute_ic` NaN 掩码兜底：spearmanr/pearsonr 计算前剔除 NaN 对，缺失值鲁棒性测试注入 NaN 后 IC 不再恒为 0，测试真实生效
- ✅ `SingleAblation` 新增 `feature` 字段记录置零列；关闭 GAP-043；新增/更新 ~18 测试用例
- ✅ 期货审计放宽（`min_oos_pass_ratio` 0.5→0.3）与种子 lookback 243→120 参数调整验证生效（4 种子晋升）；L2 演化重跑解除熔断

### v2.11.0 组合漂移治理（已完成）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ 漂移监控（DriftMonitor）：每次 L3 组合构建后对比上次组合，记录成员重合率（Jaccard）与权重 L1 变化率，持久化到 `memory/portfolio/drift_history/YYYY-MM-DD.json`；`PortfolioManager.save_combo` 自动归档旧组合到 `combo_history/` 供对比；冷启动（无上次组合）L1 变化记为 0
- ✅ 组合粘性约束（_apply_sticky_constraints）：build_combo 权重归一化前施加 — 存量因子权重相对上次组合变动 clamp 在 ±30%（`StickyConfig.max_delta`），新因子首日权重封顶（`new_factor_cap` 默认 0.10）；`PortfolioLoop` 默认启用（`DEFAULT_STICKY_CONFIG`），可显式传 `sticky_config` 覆盖
- ✅ L2 影子池（shadow_pool）：新晋升因子写入 `shadow_pool` 标记（promoted_at/observe_trading_days=5/observe_until，跳过周末），L3 `load_elite_factors` 过滤观察期内因子；种子因子 `shadow_observe=False` 直接进正式组合；DuckDB metadata + JSON 双存储
- ✅ 新增 20 测试用例（粘性约束 7 + 漂移监控 7 + 影子池 6），82 个 portfolio_loop 测试全绿；evolution_loop 既有环境性失败（6 个）经 git stash 验证与本改动无关

### v2.99.0 差距收尾：08-gap-analysis 开放差距全部关闭（GAP-046/045/050/X01/X02/X03/037/041/058）（已完成）

**完成时间**: 2026-08-10

**核心产出**（08-gap-analysis.md 状态闭环，98 项已关闭 / 2 项延期研究项）:
- ✅ GAP-046（P0）换月复权+展期成本：阶段 A v2.58.0（roll_calendar.py/cost_model.py/contract_kline）+ plans/21 承接项（GAP-F03/F02/F08/F11）全落地
- ✅ GAP-045（P1）adaptive 权重 L3：synthesis_mode="adaptive" 统一入口 + RegimeSmoother 平滑 + FactorStyle/style_tags 双维度（64 用例全绿）
- ✅ GAP-050（P1）数据源生产可用性：GAP-F04 MCP 可配置注入/降级 + tick 缓存回放（GAP-I503 v2.84.0）+ F06 数据级监控 + F07 优化器 + F09 保证金
- ✅ GAP-X01/X02/X03（P1）演化预筛三件套：横截面预筛同口径 / operator 常数前置拦截 / exec_globals 合并 + ts_product pandas2 修复（29 用例全绿）
- ✅ GAP-037（P2）深度因子学习：MLP（v2.60.0）+ GRU（v2.73.0）已落地，RL 登记远期
- ✅ GAP-041（P2）覆盖率补齐：v2.88.0 GAP-F16（+341 用例、TOTAL 94.31%），本次仅同步状态
- ✅ GAP-058（P2）测试竞态：根因=并发会话竞态，测试已加固 mock，规避方式记录
- 📋 全量回归 5267 passed（唯一失败 test_package_init pyproject 版本并发 bump 竞态，重跑即绿）
- ✅ 文档同步：08（总览 98 关/2 延期 + 一致性元数据）/07（版本历史）

### v2.99.0 L3 与信号管道解耦 + 权重每周重算（GAP-072）（已完成）

**完成时间**: 2026-08-10

**核心产出**:
- ✅ 调度解绑：l3_portfolio_loop 期货每周五 19:00（v2.99.0 由每日 20:00 → 每周五 20:00，v2.101.0 对齐 TRAE Schedule 调整至 19:00）、l3_portfolio_loop_stock 每周五 19:30（v2.99.0 由每日 08:30 → 每周五 08:30，v2.101.0 对齐 TRAE Schedule 调整至 19:30）；新增独立每日任务 futures_signal_pipeline（工作日 20:00）与 daily_signal_pipeline（工作日 08:45）；jobs.py 两个 L3 job 移除对信号管道的联动触发（信号管道不再依赖 L3 运行）
- ✅ 权重重算节奏配置化：FTSConfig.l3_weight_recompute_cadence（daily/weekly，默认 daily，v2.104.0+7 由 weekly 改 daily）+ l3_weight_recompute_weekday（默认 4=周五）+ is_weight_recompute_day()；PortfolioLoop.run(recompute_weights=None) 按配置判定，冻结日返回 status="frozen" 且不重建组合、不落盘 combo（冷启动保护：无上次组合时冻结日仍全量构建）；CLI ts portfolio run --force-recompute 强制重算
- ✅ 信号管道权重冻结：scripts/_signal_common.py save/load_weight_snapshot + ilter_factors_by_weights；futures/daily 信号管道按 cadence 重算 Ridge 权重存快照 memory/portfolio/futures/futures_signal_weights.json 与 memory/portfolio/stock/stock_signal_weights.json（v2.101.0 起按市场隔离），其余日复用快照仅刷新因子值（快照外新因子等待下次重算进入）；--force-recompute 强制重算
- ✅ 新增测试：	est_weight_recompute.py 5 用例 + 	est_signal_common.py TestWeightSnapshot 4 用例 + 	est_portfolio_loop.py 冻结/强制/冷启动 3 用例；更新 test_tasks（12→14 任务）/test_jobs（L3 解绑断言）/test_cli_extra（解绑+冻结）/test_engine（add_job 12→15），受影响 375+243 passed 全绿

### v2.9.0 Design 全量落地（已完成）

**完成时间**: 2026-08-06

**核心产出**（docs/harness/design/ 9 个设计全部实现）:
- ✅ S1 数据层（A.1/A.2/B.3）：`factor_quality_scores`/`factor_status_history`/`factor_audit_reports` 三表 + FactorQualityScoreRepository/FactorStatusRepository/FactorAuditReportRepository 3 仓储类 + factor_catalog 生命周期字段扩展（幂等，规避 DuckDB 1.1.x ART 索引 UPDATE bug）
- ✅ S2 监控调度（A.2/A.3/B.1）：prometheus_metrics 衰减/Regime/权重/质量指标注册表并挂载 /metrics；adaptive_weight 封装 AdaptiveWeightManager+RegimeSmoother（热更新）；data_quality_monitor 完整性/准确性/及时性三维指标函数；scheduler 新增 monthly_decay_eval（每月 1 日）与 data_quality_eval（每 5 分钟）任务
- ✅ S3 回测流水线（B.2）：7 阶段类（FactorScreener/SignalGenerator/PortfolioConstructor/CostSimulator/RiskAttributor/ReportGenerator/CapitalAllocator）+ BacktestPipeline.run_batch 批量排名 + BacktestPipelineBuilder + CLI `fts backtest run/batch/compare`
- ✅ S4 C.1 CLI：`fts feature list`（50 算子/7 类）+ `fts feature analyze`（置换重要性）+ `fts gp evolve`（GP 演化）
- ✅ S5 C.2 实盘对接：signal_contract（FactorSignal/SignalValidator 契约）+ fts/risk 风控包（RiskManager 五项规则/TradeAdapter 抽象/SimulatedTradeAdapter 模拟成交）+ LiveFactorMonitor（30% 偏离阈值）+ HTTP 端点（signal submit/risk status/live factors）+ Prometheus live/risk 指标
- ✅ S6 C.3 反馈闭环：FeedbackLoop 家族（Trigger/AttributionAnalyzer/DirectionAdjuster/Effectiveness）+ 4 张反馈表（feedback_events/attribution_reports/feedback_processing_results/feedback_reports）+ CLI `fts feedback trigger/process/report/stats` + Prometheus 反馈指标
- ✅ 新增 79 测试用例（S1 11 + S2 19 + S3 27 + S4 5 + S5 27 + S6 20 去重后 79），本次相关用例全绿

**说明**: 9 个设计文档状态已同步为「已实现」（B.2/C.1/C.2/C.3 标注 v2.9.0），实现方向与文档细节差异已在各文档「实现现状」标注。

### 算子演化基础层（Phase C.2）（已完成）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ FTS-Expr DSL 落地：`fts/factor_engine/expr_dsl/` 包（parser → validator → executor/compiler → runtime）；递归下降解析器 + AST，解释执行器复用 `feature_ops.py` 既有算子（pandas 向量化快速路径），编译器生成确定性沙箱代码
- ✅ 算子注册表：FTS-Expr 算子注册表元数据（语义/梯度/边界）+ L0-L5 分层
- ✅ FactorProgram kind 扩展：FactorKind 枚举（`operator`/`code`/`hybrid`）+ 可选字段（`expression`/`operator_depth`/`operator_count`/`max_lookback`）；向后兼容，存量因子经 `normalize_factor_program` 默认 `code`，对上层零破坏
- ✅ FactorExecutor 按 kind 分派：`operator` 走 DSL 解释执行快速路径（异常回退沙箱），`code` 走现有沙箱路径；评估链/Verifier 接口不变
- ✅ evolution_mode 配置：`settings.py` + `config/settings.yaml` 新增 `evolution_mode`（`operator`/`code`/`hybrid`），支持 `FTS_EVOLUTION_MODE` 环境变量
- ✅ 新增 expr_dsl 六个测试文件 + test_contracts_kind / test_executor_dispatch / test_config_settings 等用例，全量回归中本次相关用例全绿

**说明**: 本里程碑为后续「算子演化引擎」计划的前置基础层——算子因子与代码因子统一表现为 `FactorProgram`，对上层（持久化/评估链/Verifier）透明。

### v2.8.5（已完成）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ P0/P1 演化质量修复：快速预筛选层（Step 1.4，nunique>10/IC>0.02/std>1e-6 过滤常数信号和伪相关）
- ✅ 种子因子晋升修复（重复判断 + EliteFactorTracker 初始化）
- ✅ 精英因子重评估保护（跳过不存在的跟踪记录）
- ✅ 期货质量评分卡差异化配置（get_futures_config，IC/Sharpe/换手率阈值下调适应日频期货）
- ✅ LLM Prompt 增强（添加质量约束、OOS 一致性、因果链要求）
- ✅ 多父代交叉策略（GP 演化 3-parent crossover，锦标赛选择 n 父代，30% 概率）
- ✅ FTS-Expr DSL OPERATOR 演化模式集成（_generate_operator_factor 方法）
- ✅ OOS 审计误判修复（ICIR 一致性计算替代 oos_ratio）
- ✅ 新增 38+ 测试用例，全量回归测试通过

### v2.8.1（已完成）

**完成时间**: 2026-08-06

**核心产出**:
- ✅ 孤立模块集成修正：按真实 API 修正 EvolutionLoop 中 6 处集成调用点
- ✅ 4 个审查门禁 passed 判定落地（消融伪相关 / 因果事件敏感 / 鲁棒性通过率 / SHAP 信息型）
- ✅ 特征重要性分析接入 GP 管线；LogicMonitor 接入精英因子定期重评估
- ✅ 新增门禁判定测试与端到端 mock helper，109 测试全绿

### v2.5.0（已完成）

**完成时间**: 2026-08-05

**核心产出**:
- ✅ Phase 1 种子因子 YAML 化：19 个 YAML 文件管理 563 种子因子，支持版本化维护
- ✅ Phase 2 精英因子 DuckDB 迁移：680 精英因子从 JSON 迁移到 DuckDB，4 张表（factor_metadata/factor_versions/factor_correlations/factor_evaluations）
- ✅ 因子仓库层（FactorRepository）：完整 CRUD、版本管理、相关性存储、搜索过滤
- ✅ 因子相关性矩阵：100 因子 × 4950 对相关性记录（Pearson + Spearman），支持组合去冗余
- ✅ 元数据自动更新：因子自动关联最大相关系数和高相关因子列表
- ✅ 回测引擎兼容性验证：680 因子加载/执行/筛选/搜索全部通过
- ✅ 新增 54 个测试用例，155+ 测试全绿

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 种子因子 YAML 文件数 | 19 | **19** |
| 精英因子总数 | 680 | **680** |
| 相关性记录数 | 4950 | **4950** |
| 新增测试用例数 | 50+ | **54** |
| 回测引擎因子加载率 | 100% | **100%（680/680）** |

### v2.4.0（已完成）

**完成时间**: 2026-08-05

**核心产出**:
- ✅ 默认市场改为期货：settings.yaml/settings.py/meta_loop.py/cli.py 四层同步 default_market="futures"
- ✅ L1 期货知识注入新增全链路日志（初始化/种子池/Step1-4/验证/持久化/完成）
- ✅ 118 测试全绿

### v1.11.0（已完成）

**完成时间**: 2026-08-04

**核心产出**:
- ✅ SHAP 局部可解释性分析：新增 `shap_analyzer.py`，使用 KernelExplainer 对极端预测样本进行特征归因，输出 top-5 贡献特征，JSON 报告持久化
- ✅ 鲁棒性审查模块：新增 `robustness.py`，覆盖对抗样本测试（4 种扰动因子）、缺失值测试（5%/10%/20%）、分布外测试（高波动/低波动/强趋势/高噪声 4 场景）
- ✅ 新增 34 个测试用例（test_shap_analyzer.py 14 + test_robustness.py 20），1750+ 测试全绿
- ✅ robustness.py 100% 覆盖率
- ✅ pyproject.toml 新增 shap 依赖
- ✅ 架构/测试/运营文档同步更新

### v1.10.0（已完成）

**完成时间**: 2026-08-04

**核心产出**:
- ✅ 输入敏感性消融实验：新增 `ablation.py`，5 种消融模式（成交量置零、VWAP 替换 close/settle、时间戳打乱、单特征归零）
- ✅ 宏观行为场景测试：23 个典型市场场景（趋势/反转/流动性/事件/震荡/期货），场景验证器可独立运行
- ✅ 风险标签闭环验证：vwap_approx 因子 IC 阈值 0.08 晋升过滤
- ✅ 新增 20 个场景测试 + 10 个风险标签测试 + 20 个消融实验测试

### v1.9.0（已完成）

**完成时间**: 2026-08-03

**核心产出**:
- ✅ UCT 父因子选择：父因子选择从轮询改为 UCT（Upper Confidence Bound for Trees）树搜索，智能探索-利用平衡
- ✅ 失败模式聚类分析：宏观演化引入失败模式聚类分析，聚类结果注入 LLM prompt 提升演化质量
- ✅ 新增 32 个测试用例（test_uct_selection.py 10 + test_failure_pattern.py 22），全绿

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 新增测试用例数 | 32 | **32（UCT 10 + 失败模式 22）** |
| 总测试用例数 | 1633 | **1633** |
| 总体覆盖率 | 92% | **92%** |

### v1.7.0（已完成）

**完成时间**: 2026-08-03

**核心产出**:
- ✅ 动态因子权重（DynamicWeightStrategy）：基于因子历史表现（IC 代理）自动调整权重
- ✅ 市场制度自适应（RegimeAdaptiveStrategy）：识别 bull/bear/oscillate/high_vol/low_vol 市场状态，动态切换因子权重配置
- ✅ 多周期信号融合（MultiPeriodSignalFusion）：融合短/中/长周期信号，支持方向一致性检查
- ✅ 55 个测试用例全绿，strategy_evolution.py 95% 覆盖率

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 策略进化类型 | 3 | **3（RegimeAdaptiveStrategy/DynamicWeightStrategy/MultiPeriodSignalFusion）** |
| 测试用例数 | 55 | **55** |
| strategy_evolution.py 覆盖率 | 90%+ | **95%** |
| 总测试用例数 | 1557 | **1557** |

### v1.6.0（已完成）

**完成时间**: 2026-08-03

**核心产出**:
- ✅ 期货全量种子因子库：12 大因子家族 50+ 子因子（seed_data_futures_full.py）
- ✅ 期货因子演化脚本（scripts/run_futures_evolution.py）
- ✅ 期货信号管道（scripts/futures_signal_pipeline.py）— 只接入 IC>0.3 顶级因子
- ✅ 期货组合策略（scripts/futures_strategy.py）— 三种加权合成模式
- ✅ 期货 L3 组合构建（scripts/futures_l3_portfolio.py）— 正交化 + 组合优化
- ✅ L1/L2/L3 全自动调度（APScheduler 5 个定时任务）
- ✅ 期货基本面数据接入（库存/仓单/基差 — AKShare）
- ✅ 信号报告输出到 reports/{date}/
- ✅ 因子方向自动校正（截面 IC 法）
- ✅ 用户手册更新 + 所有 Harness 文档同步

**实际指标**:

| 指标 | 目标值 | 实际值 |
|:-----|:-------|:-------|
| 期货种子因子数 | 50+ | **12 大因子家族 50+ 子因子** |
| 定时任务数 | 5 | **5（L1:08:30 / L2:23:00 / L3:20:00 / 信号管道:20:30 / 健康检查:每10m）** |
| 总测试用例数 | 1502 | **1502** |
| 总体覆盖率 | 99% | **99%** |

### 35-gap-closure P2 批次（G8–G17，基础设施与工程底座）（已完成）

**完成时间**: 2026-08-13

**核心产出**（[plans/35-gap-closure-plan.md](plans/35-gap-closure-plan.md)，P0+P1+P2 三批次全部完成，G1-G15 已实现+测试）:
- ✅ G8 统一交易日历 + 断K/跳空清洗：`data_sources/trading_calendar.py` 新建（TradingCalendar / mark_panel_data_gaps / mark_gap_anomalies）+ data_futures 接入，test_trading_calendar.py 15 用例
- ✅ G9 MAD 进 Standardizer：mad_winsorize / mad_then_zscore 方法层落地（test_standardizer.py 扩增）
- ✅ G10 波动率/季节性中性化：barra_neutralizer + evolution_futures 注入（test_barra_vol_season_neutral.py 8 用例 + evolution_loop _build_vol_map 3 用例）
- ✅ G11 换手 >20% 因子级硬剔除：evaluation_chain 日换手口径（test_g11_turnover_gate.py）
- ✅ G12 信号输出统一契约：signal_contract 扩展 to_lots/转换器/validator 新字段，三管道 validator 接入（test_signal_contract_g12.py 15 用例）
- ✅ G13 月度重跑自动调度：monthly_decay_eval_job 已覆盖，判定完成（机制有+调度在）
- ✅ G14 杠杆/止损止盈随 Regime：regime_multipliers + risk_manager + MhfConfig 注入（test_regime_risk_params.py 14 用例）
- ✅ G15 方差最小化仓位：capital_allocator 解析解（test_capital_allocator_margin.py 扩增）
- ✅ G16 LLM 审核（批次末评估，v2.103.0+31）：**维持暂缓（不启用）**——G4-G7 已覆盖增量拦截、受 AP06 盲目信任约束、角色边界（FTS 专注因子发现评估）、G17 交接前置；启用条件见 35 计划 §5.9
- ✅ G17 柜台对接：FDT 交接（仿真层 gateway 契约保留，实盘柜台在 FDT 侧推进）
- ✅ 回归验证：P2 受影响模块定向回归 377 passed 全绿 + scheduler 独立 163 passed + verify_doc_consistency 30 passed；修复 test_portfolio_loop_adaptive 数据源隔离（Step 0.5 logging 污染，412s→2.44s）与 verify_doc_consistency 断言同步（482→185）
- ✅ 文档同步：08-gap-analysis（GAP-106~113 登记关闭）/06-testing（P2 55 用例）/07-operations（版本历史）/35 计划（G16 评估落库）

---

## 3. 下阶段目标（v2.0.0）

参见 [plans/production_plan.md](plans/production_plan.md) 完整路线图。

| 版本 | 主题 | 核心产出 |
|:-----|:-----|:---------|
| **算子演化引擎（Phase 3+ / C.4）** | ✅ 已实现（v2.10.0） | 基于 Phase C.2 基础层（FTS-Expr DSL / 算子注册表 / kind 分派 / evolution_mode），实现算子级因子创新与演化（`OperatorEvolutionEngine`，见第 2 节 v2.10.0 里程碑） |
| **v2.33.0** | 宏观因子降级 + 适用场景重设计 | ✅ 已实现（v2.33.0） | fut_macro_export 家族 6 因子全部 retire（真实 EDB 数据 IC≈0 证实代理假象）；角色边界重设计（宏观因子限定跨品种/板块层面）；np.bincount 输入边界审计修复（3 因子入口 NaN 清理 + bincount 防御）+ FactorRepository 事务修复 |
| **v2.30.0** | 分钟级回测 Phase 1 | ✅ 已实现（v2.30.0） | 三源分钟数据源适配 + DuckDB minute_cache + 聚合器扩展 + 回测引擎 frequency 参数 + CLI --frequency 参数 |
| **v2.0.0** | 生产部署 | 监控告警完善、容器化、CI/CD 流水线、期货全链路 E2E 测试 |
| **v2.65.0+ 三阶段（plans/23）** | 追赶机构水平全面改造（规划中） | 全链路机构级对标（GAP-I 系列 20 项）：Stage 1 对标中小团队 v2.65.0~v2.72.0（吞吐 ≥10×、股票 L3、冲击成本/容量、实盘反馈闭环、中性化门槛）→ Stage 2 对标国内头部 v2.73.0~v2.80.0（深度因子学习、组合优化器机构化、衰减自动退役、Barra 风格暴露）→ Stage 3 对标海外顶级 v2.81.0+（分布式/GPU、Level2/另类数据）；详见 [plans/23-institutional-transformation-plan.md](plans/23-institutional-transformation-plan.md) |
| **v2.61.0~v2.72.0 L3/L4 专项（plans/24）** | L3/L4 机构级追赶（规划中，A 阶段随 v2.61.0 启动） | L3 因子收益序列层 + 风险模型（Ledoit-Wolf 收缩 Σ）+ 组合指标实测化 + optimizer 接入主流程 + 中性化/成本约束 + 归因/组合层走航 + L4 实盘反馈闭环 + 表达式组合算子扩充（GAP-L 系列 11 项）；详见 [plans/24-l3-l4-institutional-plan.md](plans/24-l3-l4-institutional-plan.md) |
| **plans/28 Regime 机构级优化** | ✅ 已实施（2026-08-11，T1~T10 全部完成） | 多周期 HMM regime_probs 概率输出 → regime blend 概率混合权重（probability_mix）→ RegimeSmoother 不对称切换（de_risk/re_risk alpha）→ 置信度熵标定 exposure_scale 仓位缩放（confidence_scale）→ BIC 状态数选择（StateMapStabilizer 防翻转）→ 制度有效性样本外验证（regime_validation + validate_regime CLI）→ fts_regime_* 观测指标（T10）；详见 [plans/28-regime-institutional-optimization-plan.md](plans/28-regime-institutional-optimization-plan.md) |

---

## 4. 版本历史

| 版本 | 日期 | 说明 |
|:-----|:-----|:-----|
| **plans/28（日常开发）** | 2026-08-11 | Regime 机构级优化（T1~T10 全部完成）：多周期 HMM 后验 regime_probs（`regime_hmm.py`）→ regime blend 概率混合权重（`probability_mix` 开关，28-T3）→ RegimeSmoother 不对称切换（`de_risk_alpha`/`re_risk_alpha`，28-T6）→ 置信度熵标定 exposure_scale 仓位缩放（`regime_calibration.py` + `portfolio_loop` Step 2.5 计算/build_combo 消费，28-T4）→ BIC 状态数选择（`regime_model_selection.py` + StateMapStabilizer 防翻转，28-T7）→ 制度有效性样本外验证（`regime_validation.py` + `validate_regime` CLI，28-T9）→ 观测指标 fts_regime_*（`prometheus_metrics.record_regime_metrics`，28-T10）；详见 [plans/28-regime-institutional-optimization-plan.md](plans/28-regime-institutional-optimization-plan.md) |
| **v2.82.0** | 2026-08-10 | L1 知识源多路扩展 + 人审经验链闭环（plans/23 GAP-I103 + I101/I102 二期）：另类知识源 `AnnouncementNewsExtractor`（公告/舆情）+ `MacroEventExtractor`（宏观事件）接入股票 5 源/期货 4 源管道；`BaseExtractorPipeline.extract` 多源并行收集；驳回意见写经验链 `ExperienceChain.record_failure`；新增 23 测试用例 |
| **v2.69.0** | 2026-08-10 | 股票流水线成熟度收尾（plans/22 GAP-S09~S12 全部落地，13 项缺陷闭环）：seed_analyzer.py 种子表达式静态 PIT 审计 + estimate_lookback_static 替换正则；verify_registry_consistency 双注册表一致性；operator_first 模式（股票演化默认算子优先 + 方法分布记账）；A_SHARE_FIELDS 10 字段 + L5b 4 A 股领域算子；新增 27 测试用例 |
| **v2.65.0** | 2026-08-09 | 股票流水线 GAP-S03：A 股行业轮动 + 风格轮动 Regime 检测——`fts/factor_engine/stock_regime.py`（`StockRegimeSelector`：行业动量离散度→集中/轮动/均衡三态；大小盘+成长价值比值动量→风格双态；复用 MultiHorizonHMMDetector 多周期集成校正置信度）；`REGIME_STYLE_MULTIPLIERS` 新增 6 股票风格键；`PortfolioLoop.run(stock_regime=...)` 驱动 L3 风格自适应权重；新增 test_stock_regime.py 19 用例 |
| **v2.62.0** | 2026-08-09 | 股票流水线 GAP-S02：Barra 风格因子体系——`fts/factor_engine/barra/` 三文件（barra_style.py 10 风格暴露引擎 + barra_neutralizer.py 逐日 OLS 回归残差 + __init__ 导出）；`cross_section_evaluate_backtest` 新增 `style_exposures` 参数 + Step 2.6 风格回归残差（GAP-S01 行业去均值后叠加风格剥离，两级中性化链）；nonlinear_size 引擎层截面二次计算；新增 test_barra.py 13 用例 |
| **v2.61.0** | 2026-08-09 | 股票流水线 GAP-S01：行业/市值中性化主流程——`EvolutionLoop(market="stock")` 自动加载行业/市值映射（接通 `stock_neutralization` 死配置），键归一化兼容面板 symbol，透传评估链做行业去均值 + 市值加权去均值，报告输出中性化前后 IC 对比（`ic_pre_neutral`） |
| **v2.33.0** | 2026-08-08 | 宏观因子降级 + 适用场景重设计 + np.bincount 边界审计：fut_macro_export 家族 6 因子 retire（真实 EDB 数据证实代理 Sharpe 7.68 为假象，IC≈0）；角色边界重设计（宏观因子限定跨品种/板块层面，禁止单品种时序信号）；FactorRepository 事务修复（get_factor fetchall + status 补列）；3 个 bincount 因子边界修复（NaN 清理 + 输入防御）；同步入口 NaN 防护到 4 个活跃 g 因子；新增 24 测试用例 |
| **v2.30.0** | 2026-08-08 | 分钟级回测 Phase 1：三源分钟数据源适配 + DuckDB minute_cache + 聚合器扩展 + 回测引擎 frequency 参数 + CLI --frequency 参数 |
| **v2.19.0** | 2026-08-07 | P0/P1 过拟合修复：A. combosharp  diversity-adjusted 加权；B. 因子 Sharpe 上限截断 3.0；C. 评价窗口 120d→500d；D. L3Verifier max_sharpe=3.5；E. Dirichlet 随机化测试；F. 质量卡 Sharpe>10 惩罚；修复 `build_combo` `n_ret` 赋值顺序 bug；修复 `EvolutionLoop` pipeline 引用回归；`test_portfolio_loop.py` 90 全绿 |
| **v2.18.0** | 2026-08-07 | 因子家族多样性约束：`_promote_to_elite` 新增家族数量检查（`max_per_family=3`），限制单一家族因子过度繁殖；`BudgetConfig` 新增 `max_per_family` 字段；配置文档同步更新 |
| **v2.17.0** | 2026-08-07 | 因子淘汰主流程集成：`FactorRepository.retire_factor()` DuckDB 状态更新 + JSON 迁移 _retired/ + 状态变迁记录；`monthly_decay_eval_job` 调用 `retire_factor()`；修复 DuckDB ART 索引 bug |
| **v2.16.0** | 2026-08-06 | 孤立模块集成 Phase 2：`LogicMonitor`/`FactorInspector` 注册为定时任务（每日 22:00/03:00）；`ProcessWatchdog` 集成到 `SchedulerEngine`；任务注册表增至 8 个任务 |
| **v2.15.0** | 2026-08-06 | P0 修复：GP 演化/算子演化数据泄露（`train_mask` 隔离训练集）；IC 衰减硬编码修复（`_compute_decay_6m` 滑动窗口 IC 线性回归）；GAP-033 关闭 |
| **v2.14.0** | 2026-08-06 | GAP-030 测试隔离根治：EvolutionLoop `factor_db_path` 注入点 + run() 测试隔离 DuckDB + catalog 重复 seed 清理 |
| **v2.13.0** | 2026-08-06 | GAP-032 L2 晋升产物双写一致性：`_write_to_duckdb` 返回 bool + `_promote_to_elite` 严格一致（DuckDB 失败回滚 JSON 快照，晋升失败）；数据修复补入 fut_mobile_big_data_g5 + 归档 515 个同名重复快照 |
| **v2.12.1** | 2026-08-06 | session_id 全链路补齐：`generate_session_id()` + CLI 入口生成并挂载 `args.session_id` + 子命令日志聚合；02-lifecycle 校正 trace_id/run_id 格式描述；新增 3 测试用例 |
| **v2.12.0** | 2026-08-06 | GAP-031 L1→L2 数据流打通：EvolutionLoop 启动合并 L1 注入候选（pending 门控 + market 过滤 + 去重 + 幂等）；L1 注入写入 market 标记；SeedCandidate 契约扩展；新增 8 测试用例 |
| **v2.10.0** | 2026-08-06 | 算子演化引擎（Phase 3+ / C.4）：`OperatorEvolutionEngine` DSL 算子空间进化搜索（种群初始化/IC+Sharpe 评估/锦标赛选择/交叉变异/精英保留）+ evolution_loop 集成 + 关闭 GAP-026；新增 13 测试用例 |
| **v2.9.0** | 2026-08-06 | Design 全量落地（docs/harness/design 9 设计全部完成）：S1 数据层三表+3 仓储类；S2 监控调度（Prometheus 指标注册表 + 自适应权重 + 数据质量三维指标 + monthly_decay_eval/data_quality_eval 任务）；S3 回测流水线（7 阶段类 + run_batch + Builder + CLI）；S4 C.1 CLI（feature list/analyze + gp evolve）；S5 C.2 实盘对接（信号契约 + fts/risk 风控包 + LiveFactorMonitor + HTTP 端点 + live/risk 指标）；S6 C.3 反馈闭环（FeedbackLoop 家族 + 4 反馈表 + CLI + 反馈指标）；新增 79 测试用例 |
| **v2.8.5** | 2026-08-06 | P0/P1 演化质量修复 + OPERATOR 演化模式集成：快速预筛选层、种子因子晋升修复、精英因子重评估保护、期货质量评分卡差异化配置、LLM Prompt 增强、多父代交叉策略、FTS-Expr DSL OPERATOR 演化模式集成、OOS 审计误判修复；新增 38+ 测试用例 |
| **v2.5.0** | 2026-08-05 | Phase 1 种子因子 YAML 化 + Phase 2 精英因子 DuckDB 迁移：种子因子 YAML 文件（563 因子）；精英因子 DuckDB（680 因子，4 张表）；因子仓库层 FactorRepository；因子相关性矩阵（4950 对）；54 新测试 |
| **v2.4.0** | 2026-08-05 | 默认市场改为期货：四层同步 default_market="futures"；L1 期货知识注入全链路日志；118 测试全绿 |
| **v2.0.0** | 2026-08-04 | Phase C 逻辑审查 — 因果结构审查 + 持续监控仪表盘 |
| **v1.11.0** | 2026-08-04 | Phase B 逻辑审查 — SHAP 分析 + 鲁棒性审查 |
| **v1.10.0** | 2026-08-04 | Phase A 逻辑审查 — 消融实验 + 场景测试 + 风险标签 |
| **v1.9.0** | 2026-08-03 | Phase A 演化优化 — UCT 父因子选择 + 失败模式聚类 |失败模式聚类：父因子选择从轮询改为 UCT 树搜索，宏观演化引入失败模式聚类分析注入 LLM prompt，新增 32 个测试用例（test_uct_selection.py 10 + test_failure_pattern.py 22） |
| **v1.8.1** | 2026-08-03 | Market Regime 集成到信号管道：新增 `_build_composite_ohlcv()` 从品种面板构建市场综合 OHLCV，管道调用 `RegimeAwareSelector.detect()` 检测当前市场制度（5 种：bull/bear/high_vol/low_vol/oscillate），控制台输出制度名称+置信度+特征值，报告新增「市场制度」章节含 Regime 调整后的交易建议（趋势友好→放大仓位、震荡→反向操作、高波动→缩小仓位+增量绝对值>0.15）；版本号 1.8.0→1.8.1 |
| **v1.8.0** | 2026-08-03 | 信号管道 v5 多空双向 + 信号增量：管道升级为多空双向排名（按信号强度绝对值排序），新增信号增量追踪（较昨日变化判断趋势加速/衰竭），信号快照 JSON 持久化 + JSONL 历史追加，L3 Portfolio Loop 自动触发信号管道（全量 82 品种），README 拆分股票/期货种子因子；版本号 1.7.3→1.8.0 |
| **v0.1.0** | 2026-07-18 | 从 FDT 剥离，Phase 1-7 完成，220 测试全绿 |
| **v0.2.0** | 2026-07-18 | CLI 引擎真实调用、Scheduler 引擎、89% 覆盖率、778 测试全绿、7 项差距全部关闭 |
| **v0.3.0** | 2026-07-19 | Data-Core 集成适配层、FDT 残留清除、原子持久化、96% 覆盖率、969 测试全绿 |
| **v1.1.0** | 2026-07-24 | MCP 数据源迁移（akshare 腾讯/东方财富）、移除期货种子、1231 测试全绿、99% 覆盖率 |
| **v1.2.0** | 2026-08-02 | 种子因子集成（世坤101+Qlib158，268 种子）、熔断修复、纯多头回测、1325 测试全绿、GitHub 发布 |
| **v1.3.0** | 2026-08-03 | 国泰君安 191 因子加入种子池（459 种子）；工程测试全覆盖：1431 测试全绿，46/47 模块 100% 覆盖率 |
| **v1.4.0** | 2026-08-03 | 基本面/另类/宏观因子加入种子池（482 种子）；新增 23 个基本面种子因子 + FundamentalProvider 数据层；1435 测试全绿 |
| **v1.5.0** | 2026-08-03 | 期货数据接入：FuturesDataProvider（DuckDB kline_cache + AKShare 降级）、CLI --universe futures、82 个期货品种横截面因子演化、scripts/download_futures.py 断点续传下载脚本 |
| **v1.7.0** | 2026-08-03 | 策略进化：动态因子权重（DynamicWeightStrategy）+ 市场制度自适应（RegimeAdaptiveStrategy）+ 多周期信号融合（MultiPeriodSignalFusion）+ 55 测试用例全绿，strategy_evolution.py 95% 覆盖率 |
| **v1.6.0** | 2026-08-03 | 期货自治循环：L1/L2/L3 全自动调度（APScheduler 5 个定时任务）+ 期货全量种子因子库（12 大因子家族 50+ 子因子）+ 期货因子演化 + 信号管道（IC>0.3 顶级因子）+ 组合策略 + L3 组合构建 + 期货基本面数据接入 + 信号报告输出到 reports/{date}/ |
| **v1.3.1** | 2026-08-03 | 代码审核提升：重构 `parse_program_md` 为数据驱动（76→48 行），提取 `_evaluate_cross_section`，拆分 Eager Test；1432 测试全绿，99% 覆盖率，46/47 模块 100% 覆盖率 |
| **v1.3.2** | 2026-08-03 | 代码审核提升：消除 `_evaluate_and_promote_seeds` 重复横截面逻辑，提取 3 个公共 Mock fixture（`mock_trial`/`mock_optuna_study`/`mock_evolve_micro`）；1432 测试全绿，99% 覆盖率，47/47 模块 100% 覆盖率 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件定义 FTS 版本路线图（v2.10.0 算子演化引擎 + v2.8.5 演化质量修复 + OPERATOR 演化模式基础层），里程碑记录引用 `docs/harness/07-operations.md` 版本历史 |
| 可验证断言 | 当前版本 v2.16.0 里程碑已登记，v2.0.0 按路线图推进；v2.60.0 登记 GAP-I 系列总纲（plans/23）+ v2.61.0 登记 L3/L4 专项 A 阶段（plans/24，GAP-L301/L302/L305 处理中） |
| 检验方式 | 检查本文件下阶段目标表和版本历史确认当前版本和路线图 |