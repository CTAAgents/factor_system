# FTS 运维与版本管理

> 版本: v2.83.0
> 最后更新: 2026-08-10

---

## 1. 版本历史

| 版本 | 日期 | 说明 |
|:-----|:-----|:-----|
| **v2.83.0** | **2026-08-10** | **GAP-I502 ExecutorBackend 可插拔执行器抽象（plans/23 Stage 3A，分布式扩展预留）：① 新建 `fts/factor_engine/executor_backend.py`——`ExecutorBackend` ABC（`map`/`shutdown` + 上下文管理）+ `ThreadBackend`（默认，ThreadPoolExecutor）+ `ProcessBackend`（ProcessPoolExecutor + cloudpickle 序列化目标函数，模块级 `_process_worker` 包装，支持 lambda/bound method 跨进程序列化）+ `DaskBackend`/`RayBackend`（缺依赖或创建失败自动降级 ProcessBackend）+ `create_executor_backend` 工厂（未知后端回退 thread）；② `BatchMiner.filter_batch` 批量粗筛接入后端——`BatchMiningConfig` 新增 `executor_backend`/`executor_max_workers`，`backend.map(self._filter_one, proposals, repeat(trace_id))` 保序遍历 + 单任务异常隔离，修复 trace_id 漏传 bug；③ `FTSConfig` 新增 `executor_backend`（默认 thread 保持现状）/`executor_max_workers`（默认 4，`FTS_EXECUTOR_BACKEND`/`FTS_EXECUTOR_MAX_WORKERS` 环境变量），evolution_loop 批量模式构造透传；④ 新增 `test_executor_backend.py` 14 用例（四后端 map 行为一致性/process 支持 lambda 与 bound method/缺依赖降级路径/未知后端回退/并发性/BatchMiner 接入 + process 单任务异常隔离/filter_batch thread 与 process 结果一致），executor_backend 14 + batch_mining 11 合计 25 passed + evolution_loop batch 3 passed 全绿；⑤ 同步 01/03/06/08/09 + 23 计划（GAP-I502 ✅ 关闭）+ pyproject v2.82.0→v2.83.0 |** |
| **v2.82.0** | **2026-08-10** | **L1 知识源多路扩展 + 人审经验链闭环（plans/23 GAP-I103 + I101/I102 二期，与并发会话同版本追加）：① GAP-I103——新建 `fts/factor_engine/extractors/alternative_sources.py`：`AnnouncementNewsExtractor`（东方财富公告中心 API + LLM 提取 A 股事件/舆情因子）+ `MacroEventExtractor`（东方财富宏观日历 API + LLM 提取跨品种宏观方向因子），对齐既有三源模式（继承 BaseExtractor 复用 `_llm_extract_factors`，requests+timeout=15，失败/空数据优雅降级返回空不阻断 L1）；股票管道接入公告+宏观两源（5 源）、期货管道接入宏观源（4 源），`FTSConfig` 新增 `l1_announcement_extractor_enabled`/`l1_macro_extractor_enabled`（`FTS_L1_*_EXTRACTOR_ENABLED` 环境变量默认 True），meta_loop 构造透传，工厂函数同步透传；② GAP-I101 二期——`BaseExtractorPipeline.extract` 改为多源并行收集（ThreadPoolExecutor，单源异常不影响其他源，`_extract_one` 含异常降级与统计日志）；③ GAP-I102 二期——`FactorReviewWorkflow` 新增 `experience_chain` 可选注入 + `_record_rejection`：驳回且 comment 非空时构造 `ExperienceTrace`（success=False + `evaluation.failure_reasons` + lessons 含审查人）写入 `ExperienceChain.record_failure`，`FTS_REVIEW_EXPERIENCE_CHAIN` 开关默认 True，写入异常降级不阻断审查；④ 新增测试 23 用例（test_alternative_sources.py 16 + test_review_experience_chain.py 7），提取器+审查 122 passed + meta_loop/settings 149 passed 全绿；⑤ 同步 01/03/06/08/09 + 23 计划（GAP-I103/I101/I102 二期 ✅ 关闭）+ README |** |
| **v2.82.0** | **2026-08-10** | **AGENTS.md 数据源架构描述对齐 FTS 实际配置（追加记录）：① K 线主路径修正为 `DUCKDB_CACHE → TQ_LOCAL`（通达信 TQ-Local HTTP 127.0.0.1:7721）`→ TQ_PYTHON → AKSHARE → SYNTHETIC`；② 补全分钟级降级链 `minute_cache → TDX_MINUTE`（通达信 HTTP 17709）`→ TQ_LOCAL`（7721）`→ TQSDK`（天勤，分钟/日线）；③ 补全 tick 降级链 `tick_cache → TQSDK_TICK`；④ 实时价路径 `TQ_LOCAL → AKSHARE`；⑤ `WIND`/`IFIND` 限定字段增强层（宏观/基本面）；⑥ 端口修正：通达信 HTTP 为 17709（TDX_MINUTE），TQ-Local 为 7721，两者不同源；同步 AGENTS.md（来源 fts/core/enums.py DataSource 10 成员 + data_futures.py 降级链） |** |
| **v2.82.0** | **2026-08-10** | **AGENTS.md 量化专业化重构（AI 协作规范文档优化）：① 结构升级三场景→四场景（因子挖掘/策略开发回测/实盘/风控治理），全文档对齐；② 新增统计严谨性红线——零数据窥探（禁止全样本统计量选因子/定参，筛选与验证分离时间窗）、多重检验校正（批量因子挖掘强制 Bonferroni/FDR 防幸存者偏差）、因子去冗余（入库前相关性筛查，共线因子只留代表）；③ 回测红线补充幸存者偏差过滤（标的池必须为当期实际可交易标的）；④ 5.1 因子挖掘规范补全标准流水线（假设驱动→数据准备→因子构建→单因子检验→中性化/正交化→去冗余→入库）+ 复权/过滤预处理、前向收益标签对齐、分位数收益/IC 衰减检验、市值/行业/风格中性化；⑤ 5.2 策略开发规范补全策略链路（因子信号→信号合成→组合构建→成本容量评估→回测验证）+ 换手率指标、成本敏感性分析、容量上限评估；⑥ 编码规范新增时间类型强制 datetime64/Timestamp；⑦ 工作流 10→12 步（新增数据可用性确认 + 样本外/回测验证）；CLAUDE.md @AGENTS.md 继承，同步 pyproject v2.81.0→v2.82.0 |** |
| **v2.81.0** | **2026-08-10** | **盲测品种池机构标准扩大（GAP-055）：FUTURES_HOLDOUT 6→15 个。机构三原则：① 固定锁死（保代际可比 + 防品种窥探）；② 按产业链分层抽样，覆盖 10 条产业链（黑色系 JM0/有色 AL0·PB0/能源 BU0/聚酯链 EG0/油化工 L0/煤化工 FG0·UR0/橡胶 NR0·RU0/造纸 SP0/航运 EC0/农产品 JD0·AP0·LH0）；③ 与核心动态池（25）和分层训练集（19）互不重叠（断言校验通过），含大流动性代表 RU0/L0 防小品种选择偏差。DuckDB 数据就绪（15 品种各 120 行）。新增 test_holdout_pool.py 9 用例（规模/不重叠/全量内/去重/产业链覆盖≥8/大流动性代表/L2 训练集充足），49 相关测试全绿。同步 01（盲测池断言）/06（用例数）/08（GAP-055 ✅ 关闭）+ CODE_WIKI + README + pyproject v2.80.0→v2.81.0 |** |
| **v2.80.0** | **2026-08-10** | **FUTURES_CORE_SUBSET 数据驱动动态池（GAP-054）：① 流动性快照脚本 `scripts/liquidity_snapshot.py`——TQ-Local（通达信 17709）真实主力合约（RB2610 格式）口径，主力期最近 5 日窗口（消除换月污染：AU2610 前10日均量 2.2万手 vs 主力期 28.3万手）计算日均成交额 = 量×价×合约乘数，修复乘数表 AU0 带主连后缀失效 bug（AU 少算 1000 倍，2606 亿被算成 2.6 亿）与月均价合约（L-F2610.DCE）误匹配；② 动态池生成 `scripts/sync_liquidity_pool.py`——渐进式替换（池内够格全保留、不够格按排名由池外候补替换 + 产业覆盖约束）落盘 memory/portfolio/futures_dynamic_pool.json；③ 运行期读取 `fts.data_futures.get_dynamic_core_subset()`（缓存缺失/损坏回退静态 25 池，零风险降级），data.py/CLI/L3 portfolio_loop/weight_learning/sync_contract_kline/调度任务默认路径全部接线；④ 调度接入 `sync_liquidity_pool` 每周六 08:00（默认任务 10→11）；⑤ 快照结论（2026-08-10）：25 核心品种全部达标（0 未达标，AU 2606亿/IM 2349亿/AG 1649亿/IF 828亿/SC 613亿/RB 235亿），渐进式替换 25 保留 0 新增；⑥ 新增 test_dynamic_pool.py 10 用例（get_dynamic_core_subset 缺失/非法/损坏回退 6 + build_pool 渐进保留/替代/产业约束/池大小 4） + test_tasks.py 任务数断言 10→11，43+244 定向回归全绿；⑦ 同步 01（动态池断言）/06（用例数）/08（GAP-054 ✅ 关闭）+ README + pyproject v2.79.0→v2.80.0 |** |
| **v2.79.0** | **2026-08-10** | **GAP-F12 CI 质量门禁 + GAP-F15 极值扰动一票否决 + GAP-F10 种子库去重（plans/21 阶段 D，三 GAP 闭环）：① GAP-F12——`.github/workflows/ci.yml` 新增 lint job（`ruff check` + `ruff format --check` fts/tests/scripts）、type-check job（`mypy fts/`）、benchmark job（`pytest tests/benchmarks/ --benchmark-only`，目录存在时启用）、release job（`v*` tag 触发 `python -m build` + 上传 artifact）；`mypy fts/` 全量收敛 Success（150 source files，~121 存量错误清零：TypedDict 契约补字段 `FactorEvaluation`/`PortfolioSignal`/`AgentOptimizationProposal`/`L3MetaLoopState` + `cast` 收敛 + pandas `.to_numpy(dtype)` 统一 + jobs.py `get_default_llm_client`→`get_llm_client` 真实缺陷修复）；ruff 存量违规清零（F401/F821/F841 等）+ 全量 399 文件 `ruff format --check` 通过；pyproject `dev` extra 补 `mypy`/`ruff`/`pytest-benchmark`；② GAP-F15——`evaluation_chain._compute_extreme_perturbation_ic` 极值剔除重算 IC（剔除信号上下 `pct` 百分位极端样本后重算，`pct` 可配置 `FTSConfig.extreme_perturb_pct` 默认 0.01），`evaluate()` 输出 `FactorEvaluation.extreme_perturbation`，`_promote_to_elite` 传入 `HighICScreener` V2 一票否决（`ic_drop > 25%`）真正生效；新增 test_extreme_perturb.py 10 用例；③ GAP-F10——新增 `scripts/verify_seed_dedup.py` 种子去重校验（内嵌 vs YAML 交叉比对）+ 家族上限配置化（`FTSConfig.max_per_family`，env `FTS_MAX_PER_FAMILY` 缺省 15）+ 被拒因子日志；新增 test_seed_dedup.py 13 用例；④ 修复全量回归 8 失败——`schema.py verify_database` 元组表名 SQL 语法回归（`FROM ('attribution_reports',)`，mypy 收敛误改）修复为字符串表名列表、`test_jobs.py` L2 job mock 同步 `get_llm_client`；⑤ 全量回归 4671 passed / 8 failed→修复后清零（test_package_init 版本断言随 bump 自愈），GAP-F10/F12/F15 ✅ 关闭，同步 06/08/21 计划 + pyproject v2.78.1→v2.79.0 + README |** |
| **v2.78.1** | **2026-08-10** | **跨市场 IC 对比默认关闭（GAP-053 增量，避免无关股票面板下载）：① `fts/factor_engine/weight_learning.py` `WeightLearningConfig.cross_market_ic` 默认 True→False（docstring 同步）——默认不再自动加载对侧市场（CSI300）替代面板做跨市场迁移 IC 对比，期货权重学习仅使用期货样本空间，杜绝与目标交易市场无关的数据源请求；需显式 `WeightLearningConfig(cross_market_ic=True)` 或未来配置项开启；② `portfolio_loop.py` Step 6.7 接线不变（`if wl_config.cross_market_ic:` 已按配置执行）；③ 新增 2 用例 `test_cross_market_ic_default_off`/`test_cross_market_ic_can_be_enabled` 断言默认关闭+可显式开启（test_weight_learning.py 28→30 用例）；④ 同步 01（权重学习断言补默认关闭）/06（用例数）/08（GAP-053 说明）+ README + pyproject v2.78.0→v2.78.1 |** |
| **v2.78.0** | **2026-08-10** | **GAP-I204 二期（符号回归补充搜索 + Pareto 前沿输出）：① 新建 `fts/factor_engine/pareto.py`——`ParetoItem`（expression/ic/sharpe/turnover/decay/fitness/source + objectives 属性统一「越大越好」口径 \|IC\|/Sharpe/−turnover/−decay）+ `fast_non_dominated_sort`（NSGA-II 快速非支配排序，逐层剥离被支配个体）+ `compute_pareto_front`（rank0 前沿提取，按 fitness 降序供人审，空表达式过滤）；② 新建 `fts/factor_engine/symbolic_regression.py`——`SymbolicRegressionSearcher` 确定性 beam-search 层级搜索（单字段出发逐层一元包装 + 二元组合，去重 + 复用 `GPEvolver._evaluate_fitness` 同口径多目标评估，每层保留 top-K，固定种子可复现，max_candidates 防组合爆炸），`SymbolicRegressionConfig`（max_depth/beam_width/max_candidates/fitness_metric/turnover_penalty/decay_penalty/min_fitness/seed）+ `SymbolicCandidate`/`SymbolicRegressionResult`；③ `gp_evolver.py` 扩展——`GPEvolverConfig` 新增 `symbolic_regression_enabled`/`symbolic_max_depth`/`symbolic_beam_width`/`symbolic_max_candidates`（默认关闭，不改变默认行为）；`GPEvolver.evolve()` multi_objective 模式跟踪全部已评估个体（evaluated_pool）→ 演化结束提取 Pareto 前沿；启用符号回归时补充搜索并合并（source=gp/symbolic）；`GPEvolveResult` 新增 `pareto_front` 字段；④ 新增 `tests/factor_engine/test_pareto.py` 12 用例（objectives 口径/空集/支配链/前沿提取/换手衰减支配）+ `test_symbolic_regression.py` 15 用例（配置/树构建/beam-search 可复现/深度约束/候选上限/GP 集成 symbolic 前沿合并 + multi_objective 前沿输出），GP 回归 54 passed 全绿；⑤ GAP-I204 二期 ✅ 关闭（Stage 2 退出标准④ Pareto 前沿供人审达成），同步 01/06/07/08/09 + 23 计划 + pyproject v2.77.0→v2.78.0 + README |** |
| **v2.77.0** | **2026-08-10** | **GAP-I402 在线因子性能监控（实盘反馈数据源接入 + 衰减告警 + 指标）：① `fts/monitor/live_factor_monitor.py` 新增 `ingest_live_ic`——消费 GAP-I401 实盘反馈数据源（`LiveFeedbackImporter.compute_live_ic` 输出 + `LiveVsBacktestICReport.generate` 的 status 字段），自动构建因子回测基线/实盘 IC 并触发偏离检查（`set_backtest_baseline`/`update_live_performance` 保留兼容）；② 衰减监控——`_decay` 状态存储（ok/weak/decayed）+ `set_decay_status`/`get_decay_status` + `_check_decay_alerts`（decayed → critical「衰减退役建议（GAP-I305 闭环）」/ weak → warning「持续观察」，`decay_alert_enabled` 可关）；③ Prometheus 兼容指标日志——`METRIC live_factor_ic{factor_id=..}` / `METRIC live_factor_decay{factor_id=..,status=..} 1`；④ 新增 `tests/monitor/test_live_factor_monitor.py` 12 用例（偏离检查 5 + ingest_live_ic 6 + GAP-I401 端到端对接 1），monitor+feedback 253 定向回归全绿；⑤ GAP-I402 ✅ 关闭，同步 06/07/08/09 + 23 计划 + pyproject v2.75.0→v2.77.0 + README |** |
| **v2.75.0** | **2026-08-10** | **GAP-I202 算子库扩充（组合/跨标的算子单一事实源）：① `fts/factor_engine/feature_ops.py` `RollingOps` 新增 `ts_slope`（滚动线性回归斜率，刻画局部趋势强度/方向，NaN 安全降级）与 `ts_quantile`（滚动分位数，q∈[0,1] 越界抛 ValueError）原语；② `feature_ops.OperatorRegistry`（GP 演化侧）注册 8 个组合/跨标的算子（combo 类目）：ts_slope/ts_quantile + GAP-L401 的 regression_residual/quantile_bucket/cross_section_demean/if_else/corr/cross_section_rank——与 expr_dsl 注册表共用 RollingOps/PriceOps 底层原语，GP 演化与算子演化可发现同一组算子（此前 L4 组合算子仅存在 expr_dsl 侧，GP 侧不可用 = 双轨漂移）；③ `expr_dsl/registry.py` 注册 ts_slope/ts_quantile（L1，参数边界 + PIT lookback + 经济语义），`verify_registry_consistency` 新增 `required_shared` 硬约束——8 个组合/跨标的算子必须双注册表共享（仅存在于单侧即判不一致，输出 `unshared_required`）；④ `operator_evolution.py` `_evaluate_fitness` 新增 lookback=0 罚分（`compute_max_lookback==0` 纯字段/无算子表达式，如 `rank(close)`，与常信号罚分同档 _PENALTY_WEAK）——算子演化产物必须包含实际算子变换，避免裸字段包装在单调合成数据上以虚假高 IC 占据最优；⑤ 新增 7 用例（test_registry.py：ts_slope/ts_quantile 元数据/功能/边界 + GP 注册表含组合算子/可调用 + required_shared 一致性 + DSL 执行），315 定向回归全绿（expr_dsl + operator_evolution + evolution_loop + gp_evolver）；⑥ GAP-I202 ✅ 关闭，同步 06/07/08/09 + 23 计划 + pyproject v2.73.0→v2.75.0 + README |** |
| **v2.74.0** | **2026-08-10** | **GAP-I302 组合优化器机构化核实关闭（补齐参数走配置，与 GAP-L302/L303/L304/L305 组成闭环）：① 诊断确认核心能力（`RiskModelEstimator` Ledoit-Wolf 收缩协方差 + `PortfolioOptimizer` risk_parity/mvo）已由 GAP-L302/L303/L304/L305（v2.61.0）落地，剩余缺口为「优化器参数未走配置」；② `settings.py` `FTSConfig` 新增 `portfolio_optimizer_mode`（默认 risk_parity，env `FTS_PORTFOLIO_OPTIMIZER_MODE`）；③ CLI `_cmd_portfolio_run` 读取 `--optimizer-mode`/配置默认 + `--returns-matrix` CSV 加载传入 `loop.run(factor_returns=...)`；`portfolio run --synthesis-mode optimizer` 生效；④ 新增 5 用例（test_cli_extra 3：optimizer 模式透传/配置默认值/returns-matrix 加载 + test_config_settings 2：默认值与 env 覆盖）；⑤ GAP-I302 ✅ 关闭，同步 03/08 + 23 计划 |** |
| **v2.73.0** | **2026-08-10** | **L3 定时任务显式期货路径（调度一致性修复）：① `fts/scheduler/jobs.py` `l3_portfolio_loop_job` 显式传 `elite_dir=cfg.futures_elite_dir` + `market="futures"`（此前误用股票 elite 目录 `cfg.elite_dir` 与默认 market="stock"，与下游期货信号管道不一致）；② 新增 `test_uses_futures_path` 断言调度任务走期货路径（test_jobs.py 29→30 用例），调度全量 152 passed；③ 同步 01（L3 调度期货路径）/06（用例数）/08（GAP-052 ✅ 关闭）+ README + pyproject v2.72.1→v2.73.0 |** |
| **v2.75.0** | **2026-08-10** | **机构级权重学习增强（GAP-053，elastic_net 组合构建）：① 新建 `fts/factor_engine/weight_learning.py`（`WeightLearningConfig`：risk_adjust/rolling_validation/rolling_windows/min_window_dates/panel_market/cross_market_ic）——风险调整权重（`risk_adjust_from_panel` 经 `FactorReturnsBuilder`+`RiskModelEstimator` Ledoit-Wolf 收缩协方差 → `volatility_scaling`（w∝\|coef\|/σ，每单位风险信号贡献）/`risk_parity`（等风险贡献循环坐标下降，对角协方差退化为逆波动率），零系数因子保持 0）；滚动样本外验证（`rolling_oos_validate`：时间轴等分 rolling_windows 段、段内 70/30 train/test、逐日截面 Spearman OOS 组合 IC + 相邻窗口权重相关稳定性 + 首尾窗口 L1 衰减）；学习面板市场自动匹配（`resolve_panel_market`：panel_market="auto" 跟随目标交易市场 futures→FUTURES_CORE_SUBSET / stock→CSI300）+ 跨市场迁移 IC 对比（`cross_market_ic_check`：学习面板 vs 对侧市场面板逐因子 IC + 迁移差距）；② `portfolio_loop._compute_elastic_net_weights` 接入（回归循环提取 `_fit_elasticnet_coefs` 复用，Step 6.5 风险调整/6.6 滚动验证/6.7 跨市场 IC），`synthesize_signals` 新增 `market`/`weight_config` 透传，`PortfolioLoop` 新增 `weight_config` 参数；③ 新增 `tests/factor_engine/test_weight_learning.py` 28 用例（市场解析 6 + Spearman 4 + 风险调整 7 + ERC 求解器 2 + 面板风险调整 2 + 滚动验证 3 + 回归系数 2 + 跨市场 IC 2），既有 elastic_net 回归 19 passed 全绿；④ 同步 01（权重学习架构块）/02（Phase 39）/06（用例数）/08（GAP-053 ✅ 关闭）+ README + pyproject v2.73.0→v2.75.0 |** |
| **v2.73.0** | **2026-08-10** | **GAP-I203 深度因子学习（Stage 2 首项，深度时序模型 + 接入 L2 漏斗）：① `fts/ml/models.py` 新增 `GRUFactorModel` 轻量纯 numpy 单层 GRU（update/reset gate + candidate hidden，BPTT + 动量 SGD + L2 正则；输入 (n, seq, f) 滚动窗口序列，训练前 z-score 标准化；样本不足/非数值/维度不匹配/未训练抛 `ModelNotAvailableError` 供调用方降级；`get_params` 导出 11 组权重供因子 code 内嵌；`create_gru_model` 工厂 + `__all__` 导出）；修复 numpy 2.x 标量转换（形状 (1,) 数组 `float()` → `.item()`，`_forward_single`/`_backward`）；② `fts/ml/deep_factor.py` 新增 `DeepFactorGenerator`/`create_deep_factor`（`DeepFactorConfig` lookback/horizon/hidden/epochs/lr/train_ratio/min_samples/seed）——由 OHLCV 构造特征（日收益率+量变化率）→ 滚动窗口样本 → 前 train_ratio 训练 GRU → 权重序列化内嵌 `def factor_program(data, params)` code（零未来函数：特征窗口 [t-lookback+1, t] 逐 t 滚动推理 + tanh 压缩输出 ∈ [-1,1]，前 lookback-1 位零；样本不足/训练失败返回 None 降级）；factor 契约完整（factor_id/name/code/signature/economic_logic/source=deep_evolution/family=deep/market/deep_model 元数据含 val_ic）；③ `evolution_loop.py` `_evolve_one` 新增 method_hint="deep" 分派——`_run_deep_evolution`（数据/样本校验 → create_deep_factor → parent_id/generation/trace_id 血缘回填，失败抛 RuntimeError 由调用方降级回退）；`_batch_generate_one` 批次轮换并入 deep（idx%3==2，macro/gp/deep/operator 循环，token 护栏保持）；④ 新增 `tests/test_gru_factor.py` 28 用例（GRU 模型级 11：训练/预测形状/seq_len·n_features 属性/线性记忆目标学习相关 >0.3/同 seed 可复现/样本不足/未训练/非数值/维度不匹配/常数列兜底/权重导出形状/工厂；DeepFactor 集成 9：契约字段/生成 code 经 `_execute_factor_code` 可执行且 |out|≤1/零未来函数截断一致性（t 位置不受 t 后数据影响）/短序列/非数值/长度不齐/缺 volume 兜底/同 seed code 确定性/训练失败降级；EvolutionLoop 接线 8：`_run_deep_evolution` 成功血缘/无数据抛错/样本不足抛错/`_evolve_one` deep 分派/失败返回 None/批次轮换断言）；⑤ GAP-I203 ✅ 关闭（Stage 2 退出标准①深度因子在 L2 出过 ≥1 精英因子，由 batch 漏斗 deep 通道持续供给），同步 06/07/08/09 + 23 计划 + pyproject v2.72.1→v2.73.0 + README |** |
| **v2.72.1** | **2026-08-10** | **GAP-I206 补充（多因子正交基底维护）+ GAP-I305 提前完成（因子衰减自动退役闭环）：① 正交基底——新建 `fts/factor_engine/orthogonal_basis.py`（`OrthogonalBasisManager`：Gram-Schmidt 多因子正交基底，基底 = 按 Sharpe 降序保留上限（默认 10）的两两近似正交精英因子，索引持久化 `{memory_dir}/orthogonal_basis.json`）；`evolution_loop._orthogonalize_via_basis` L2 准入优先对候选信号关于基底逐因子 OLS 残差化（迭代投影），残差与基底最大相关 < 0.3 且保留比 > 0.3 时以正交化版本入库并注册为新基底成员，基底不可用/失败回退单参照 OLS；DuckDB metadata 与 L3 `load_elite_factors` 透传 `orthogonalized_basis`；`settings.py` 新增 `l2_orthogonal_basis_enabled`/`l2_orthogonal_basis_max_size`/`l2_orthogonal_basis_min_sharpe` 配置；② 自动退役闭环——`elite_tracker.py` 新增滚动 6M IC 线性回归斜率 `_calc_ic_slope_6m`（归一化 [-1,1]）+ 衰减分级 `decay_grade`（normal/observe/retired，`observe_slope` 0.10 / `retire_slope` 0.20）写入快照，`auto_retire()` 纳入 `decay_grade=="retired"` 退役条件；`AutoRetireConfig`/`AutoRetireManager` 分级阈值配置同步；`evolution_loop._run_periodic_factor_review` 接线 FeedbackLoop FACTOR_DECAY 事件（`last_feedback` 写回快照），退役受 `decay_auto_retire_enabled` 开关控制；`monthly_decay_eval_job` 按斜率迁移状态 + `retire_factor` 回写 DuckDB/JSON + 报告；`settings.py` 新增 `decay_observe_slope`/`decay_retire_slope`/`decay_slope_min_points`/`decay_auto_retire_enabled` 配置；③ 新增 `tests/factor_engine/test_orthogonal_basis.py` 19 用例（IC 斜率 5 + 衰减分级 5 + 基底管理器 7 + L2 集成 2），test_elite_tracker/test_l2_orthogonalize/test_l2_elite_redundancy 全绿；④ 同步 03/06/08/09 + 23 计划（GAP-I206 补充/GAP-I305 ✅ 关闭，L64 去冗余/L67 衰减管理 T2/T3 ✅ 达标）+ README |** |
| **v2.72.1** | **2026-08-10** | **GAP-F13 漂移告警闭环（P2）：① `portfolio_loop.py` DriftMonitor 补全告警闭环——`check_and_alert` 超阈值告警（成员重合率 < `overlap_threshold` 0.50 或权重 L1 变化率 > `weight_l1_threshold` 0.40 触发，`DriftAlertConfig` 阈值可配置）+ Prometheus 兼容指标日志（`METRIC drift_alert{overlap=..,weight=..,o_th=..,w_th=..} 1`）；`PortfolioLoop.run()` Step 5.5 接入（state 标记 `drift_alerted`/`drift_alert_info`，异常降级 warning 不阻断）+ Step 7 `generate_rebalance_proposal` 粘性重平衡建议（`trigger_rebalance=True` 时附加 `source=drift_monitor` proposal 注入 FDT）；② 新增 `DriftAlertConfig`/`DEFAULT_DRIFT_ALERT_CONFIG` 契约 + 9 用例（TestDriftMonitorAlert 7 + run 集成 2），test_portfolio_loop 213 passed 全绿；③ 同步 05（新增 L3 组合漂移告警章节）/08（GAP-029 完成说明）+ 21 计划 + pyproject v2.72.0→v2.72.1 |** |
| **v2.72.0** | **2026-08-10** | **GAP-I101/I102 首期（Stage 1D，L1 批量候选 + 审查工作流骨架）：① GAP-I101——`meta_loop.py` 新增 `validate_batch_candidates` 批量候选契约校验（candidate_id/name/code/economic_logic.narrative 逐条校验 + total/valid/invalid/invalid_samples 统计，invalid_samples 截断 5）接入 `_run_bootstrap` 前置质量门（契约不合规仅告警不熔断）；`MetaRunResult` 新增 `candidates_per_minute` 吞吐指标（候选数 / 运行分钟，`_make_result` 计算，elapsed=0 防除零）；知识源多路扩展留二期 v2.80.0；新增 `TestValidateBatchCandidates` 8 用例；② GAP-I102——`factor_inspector.py` 新增 `FactorReviewWorkflow` 审查工作流（`ReviewDecision` 状态机 pending→approved/rejected + `approve`/`reject` 意见回写 DuckDB `factor_reviews` 表幂等 UPSERT + `list_pending` 待审查队列（NOT EXISTS 排除已审查 + market 过滤 + limit 上限）+ `get_status` 状态查询）；CLI `fts factor review list/approve/reject` 子命令（--market/--limit/--db/--comment）；schema E.1 `_CREATE_FACTOR_REVIEWS` 表；审查意见接入经验链留二期 v2.80.0；新增 `TestReviewCliCommands` 4 用例补强 test_review_workflow 7 用例（共 11 passed 全绿）；③ GAP-I101/I102 ✅ 关闭，同步 06/08/09 + 23 计划 + pyproject v2.71.0→v2.72.0 + README |** |
| **v2.71.0** | **2026-08-10** | **GAP-I401 实盘反馈闭环（Stage 1D，追加记录）：① ①② 由 GAP-L402（v2.66.0）已落地——`LiveFeedbackRecord` 契约（factor_id/signal_date/signal_value/position_return/turnover/slippage/market）+ `validate_live_feedback_record` + `LiveFeedbackImporter`（CSV/dict 批量导入 + DuckDB `feedback_live` 表追加落盘 + 截面 Spearman 实盘 IC）+ `LiveVsBacktestICReport`（实盘 vs 回测 IC 对比 + 衰减判定 decayed/weak/ok）+ CLI `fts feedback import`/`fts feedback live-ic`；② ③ 补强（本次）：`LiveVsBacktestICReport.generate` 输出 `recommend_retire`（status=decayed → True）/`decay_gap`（\|回测 IC\|−\|实盘 IC\|）字段与 summary `n_recommend_retire`——衰减因子携带退役建议，供 GAP-I305 自动退役闭环消费；③ test_feedback_loop 25 passed（扩展对比报告测试断言 recommend_retire/decay_gap/n_recommend_retire）；④ GAP-I401 ✅ 关闭，同步 08/09 + 23 计划 + README |** |
| **v2.71.0** | **2026-08-10** | **GAP-I204 GP 多目标适应度首期（IC×换手×衰减，追加记录）：① `gp_evolver.py` 新增 `multi_objective` 适应度模式——`FitnessResult` 扩展 `turnover`/`decay` 字段：换手=信号逐日绝对变化均值/信号标准差（无量纲归一，衡量调仓频繁度，联动 GAP-I501 冲击成本）；衰减=训练集按时间等分两半分别算 \|IC\| 的前段相对后段衰减比例（0=无衰减 1=完全衰减，联动 GAP-I305 衰减退役）；② `GPEvolverConfig` 新增 `turnover_penalty`（默认 0.3）/`decay_penalty`（默认 0.3）系数；合成适应度 `fitness = \|ic\|×0.6 + max(sharpe,0)×0.2 − turnover_penalty×min(turnover,5) − decay_penalty×decay`（换手/衰减惩罚项使高换手、快衰减表达式被压低排名）；③ `GPEvolveResult` 新增 `best_turnover`/`best_decay`（最优因子换手/衰减指标随演化结果输出，日志同步），`_evaluate_best_metrics` 扩展返回 4 元组；④ 默认 `ic_sharpe_combo` 模式保持原逻辑不变，但同样填充 turnover/decay 指标供报告；⑤ 新增 `TestGapI204MultiObjective` 7 用例（FitnessResult 字段填充/换手度量平滑 vs 振荡/同 IC 量级换手惩罚/系数放大 ×2/衰减惩罚/端到端 evolve），test_gp_evolver 54 passed + test_evolution_loop GP 相关 9 passed；⑥ GAP-I204 首期 ✅ 关闭（Pareto 前沿/符号回归留二期 v2.78.0），同步 06/08/09 + 23 计划 + README |** |
| **v2.71.0** | **2026-08-10** | **GAP-I206 L2 准入去冗余/正交化闭环（Stage 1C，追加记录）：① `evolution_loop.py` 新增 `_check_elite_correlation`——演化因子晋升前扫描既有 elite 快照（排除 `_l2_seed_correlation_index.json`，容量护栏 `l2_elite_corr_max_scan` 默认 50），复用 `BacktestPipeline._execute_factor_code` 逐个计算信号与候选因子做 Pearson 相关，相关绝对值 ≥ `l2_elite_corr_threshold`（默认 0.9）记录高相关对（`factor_name_b`/`factor_id_b`/`pearson`/`abs_pearson` 按 abs 降序）拒绝晋升并打拦截日志；无既有 elite / 新因子执行失败 / 全低相关返回 None 静默放行（首次晋升场景不阻断）；② `_promote_to_elite` 接入：`shadow_observe=True`（演化因子）调用相关性检查命中即返回 None 不落盘；种子因子（`shadow_observe=False` 首轮导入）跳过检查全量放行；③ `settings.py` 新增 `l2_elite_corr_threshold`/`l2_elite_corr_max_scan`/`l2_elite_corr_debug` 配置 + `FTS_L2_ELITE_CORR_*` 环境变量（异常回退默认值）；④ 新增 `tests/factor_engine/test_l2_elite_redundancy.py` 10 用例（方法级 7：高相关/负高相关 abs/低相关/空 elite/索引跳过/容量护栏/执行失败容错 + 集成 3：shadow 高相关拦截/种子跳过/低相关晋升），新用例 11 passed 全绿；⑤ GAP-I206 ✅ 关闭，同步 03/06/08/09 + 23 计划 + pyproject + README |** |
| **v2.71.0** | **2026-08-10** | **压力测试索引修复 + 组合因子补审计：① 修复 `stress_test.py` 预存 bug——`run_scenario` 切片日期范围 `df.index >= pd.Timestamp` 在字符串/Range 索引上抛出 `'>=' not supported between instances of 'numpy.ndarray' and 'Timestamp'`，导致 FactorAuditor 第 4 项 stress_resilience 在真实数据审计中恒 skipped；修复为索引统一 `pd.to_datetime(df.index, errors="coerce")` 后再比较；② 补审计组合 2 因子（15 核心品种 × 500 日真实数据，6 项强制审计）：`fut_basis_momentum`（fct_5bf469e0）**实质失败**——mean_ic=-0.3169、正 IC 品种 0/15、OOS 一致性 0.00（其演化版 g38 同源逻辑最近 500 日预测方向相反），按审计规范**退役至 `futures_elite/_retired/`**；`fut_basis_momentum_g38`（fct_83d42ab0）通过 5/6（mean_ic=+0.5521、跨品种 100%、OOS 通过），仅 multiple_testing 失败（p 值基于 ICIR 粗糙近似，历史已知方法局限），audit_report 落盘因子 JSON；③ 新增 `scripts/audit_portfolio_factors.py`（指定因子审计+落盘工具）与 `test_stress_test.py` 字符串索引回归 1 用例（29 通过）；④ 同步 06/07 + pyproject v2.70.0→v2.71.0 |** |
| **v2.70.0** | **2026-08-10** | **Stage 1B 收官（GAP-I301 股票 L3 组合层 + GAP-I205 微演化两阶段漏斗）：① GAP-I301——CLI `_cmd_portfolio_run` 股票分支对称触发：`portfolio run --universe stock` L3 完成后自动调用 `scripts.daily_signal_pipeline.main(max_stocks=50, days=120)`（此前仅期货分支触发 futures_signal_pipeline；状态集 passed/verifier_warning/completed 一致、非零 rc 打印告警降级）；组件复用性确认：`PortfolioLoop(market="stock")` + `load_elite_factors(market="stock")` market 过滤 + `synthesize_signals`（equal_weight/sharpe_weight/elastic_net/adaptive）+ Step 2.5 `stock_regime` 风格自适应（GAP-S03）+ `build_combo(market="stock", cost_config)` 多头组合 net 指标；新增 `TestStockL3PortfolioLayer` 6 用例 + `TestCmdPortfolioRunStock` 3 用例；② GAP-I205——`micro_evolution.py` 两阶段漏斗：`optimize_params_staged` 粗筛低 trials（默认 20）随机搜索快速打分，得分低于 `COARSE_IC_FLOOR`（0.02）直接淘汰（passed=False）；通过者进入精筛，trials 按粗筛得分自适应（得分达 `COARSE_REF_IC` 0.10 跑满 n_trials）+ TPE 早停（早停机制既有）；`evolve_micro` 新增 `use_staged` 参数，`EvolutionLoop` 接入并默认启用（`settings.py` 新增 `micro_staged_evolution`/`micro_coarse_trials`/`micro_coarse_ic_floor` 配置 + FTS_MICRO_* 环境变量）；新增 `TestStagedFunnel` 5 用例；③ 修复预存 bug：`evolution_loop.py` L325 `get_config()` 仅在 `market is None` 分支内导入，显式传 market 时 UnboundLocalError（GAP-S11 引入）——提前模块级导入修复；④ GAP-I301/I205 ✅ 关闭，同步 01/06/08/09 + 23 计划 + pyproject + README |** |
| **v2.69.0** | **2026-08-10** | **股票流水线成熟度收尾（plans/22 GAP-S09/S10/S11/S12 全部落地，13 项缺陷闭环）：① GAP-S09 种子表达式静态 PIT 审计——新建 `fts/factor_engine/expr_dsl/seed_analyzer.py`（WQ 风格表达式递归下降解析器：二元/逻辑运算、科学计数法、`np.` 复合标识符；静态提取 max_lookback 仅统计窗口算子常量参数，排除幂次/分支常量；fields/operators/depth 结构指标），`seed_loader._expression_factor_from_yaml` 与 `seed_data.loader.make_factor_program` 改走 `estimate_lookback_static`（替换正则粗糙估计），全量 705 表达式扫描仅 1 个 fundamental 切片语法需显式 lookback；② GAP-S10 双注册表一致性——`expr_dsl.registry.verify_registry_consistency()` 重叠算子（20+）同输入断言输出一致（rtol 1e-6），`only_dsl`/`only_gp` 单侧合法放行；③ GAP-S11 股票演化 operator-first——`evolution_mode` 新增 `operator_first`（EVOLUTION_MODES 5 模式），`EvolutionLoop` 股票默认 operator_first（算子优先 → LLM 兜底 → GP 兜底），`EvolutionStateManager.record_evolution_method` 演化方法分布记账（state `evolution_method_counts`）；④ GAP-S12 A 股特有算子——`A_SHARE_FIELDS` 10 字段（北向/两融/股东户数/分析师预期，L0 访问器 + 经济语义）+ L5b 4 领域算子（nb_momentum/margin_change/holder_concentration/analyst_revision_ratio）；⑤ 新增测试：test_seed_analyzer.py 14 + test_registry.py GAP-S10/S12 6 + TestGapS11OperatorFirst 7（共 27 新用例）；修正 test_seed_loader 股票种子计数 645→714；同步 06/07/08/09 + 22 计划（13 项全部 ✅）+ pyproject v2.68.0→v2.69.0 |** |
| **v2.68.0** | **2026-08-10** | **L3/L4 专项收尾（GAP-L308 Regime 数据化 + GAP-L309 面板规模 + GAP-L310 种子链修复，细则 plans/24）：① GAP-L308——新建 `fts/factor_engine/regime_multipliers.py`（`RegimeMultiplierEstimator` 按 regime×family 聚合 IC 均值/胜率生成数据驱动倍率，钳制 [0.5,1.5] + 最小样本回退 + 硬编码对比报告），修复 family_global 跨 regime 桶被覆盖 bug；倍率表落盘 `docs/harness/_data/l3_regime_multipliers.yaml`（易变配置进 _data 原则），`portfolio_loop.load_data_driven_multipliers` 优先接线（数据驱动表存在时优先、缺失/损坏回退硬编码表）；② GAP-L309——新建 `PanelLoadingConfig`（默认全 CSI300 子集 × 500 天，对齐 MIN_EVAL_DAYS），`_liquidity_stratified_sample` 流动性分层抽样（平均成交额代理 + 桶间轮询保证高低流动性覆盖，无 volume 退化等权），`_load_panel_with_liquidity_sampling` 覆盖日志 + 幸存者偏差提示；`_compute_elastic_net_weights`/`_compute_ml_ensemble_weights` 默认 days 120→500、max_stocks 50→0（全量）；③ GAP-L310 种子加载链修复——`seed_loader.py` L23 补 `FactorKind` 导入（YAML 因子 `kind=FactorKind.*` NameError 致期货种子 81/184 加载失败）；`_fundamental_factor_from_yaml` 多行 `field_defs` strip+统一 4 空格缩进（unexpected indent 38 处编译失败）；test_seed_loader 改引 `seed_analyzer.estimate_lookback_static`（`_estimate_lookback` 已迁移）；种子计数断言同步 714/898/30（新增 4 个股票 YAML：analyst_revision/holder_count/margin_trade/northbound 共 69 因子）；④ 新增测试 26 用例（test_regime_multipliers 14 + test_data_provider_panel 12），受影响模块回归全绿 |** |
| **v2.67.1** | **2026-08-10** | **GAP-F02 修复（limit_up/limit_down 统计传递）：① `backtest_pipeline.py` `_compute_strategy_returns` 新增 `precomputed_blocked_stats` 参数，使用 `_build_tradeable_mask` 预计算的细分统计（limit_up/limit_down）代替全归入 halt，修复 `test_report_contains_blocked_trades` 断言失败；② 更新 21-futures-maturity-optimization-plan.md 标记 GAP-F03/F02/F01/F08/F09/F11 全部 ✅ 已完成；③ 更新 08-gap-analysis.md GAP-049 ✅ 已关闭、GAP-050 F09 ✅ 已关闭 |** |
| **v2.67.0** | **2026-08-10** | **GAP-I501 回测容量约束（Stage 1A）：① `backtest_pipeline.py` 容量约束实现——`_compute_strategy_returns` 新增 `volume`/`close_price`/`capacity_cap_ratio`/`initial_capital` 参数，持仓市值 ≤ 品种日均成交额 × capacity_cap_ratio 滚动 20 日窗口截断，超限等比缩放并记录违规统计（`capacity_violations`/`capacity_avg_reduction`/`capacity_max_reduction`）；② `_evaluate_performance` 透传容量数据，报告 summary 新增 `capacity_analysis` 章节；③ `settings.py` 新增 `backtest_capacity_cap`（默认 true）/`capacity_cap_ratio`（默认 0.01）配置；④ 新增 `TestGapI501CapacityConstraint` 5 用例（大仓位截断/关闭跳过/缺量跳过/违规统计/端到端报告）；⑤ GAP-I501 ✅ 关闭，同步 01/06/07/08/09 + 23 计划 + pyproject + README |** |
| **v2.66.0** | **2026-08-09** | **GP/operator 通道修复三连 + 横截面预筛真实化（GAP-X01/X02/X03）：① GAP-X03 `eval_fts_expr 未定义` 根因修复——`BacktestPipeline._execute_factor_code` 的 `exec` 模块级 import（`from ...runtime import eval_fts_expr`）绑定落在 local_vars，而 `factor_program.__globals__` 指向 globals dict，函数调用时 NameError，`_execute_factor_code` 降级返回全零 → operator 因子在运行时校验/预筛阶段被全数当作「常数信号」拦截，GP/operator CPU 演化通道空转；修复为执行后 `exec_globals.update(local_vars)`（与 `FactorExecutor.compile` 同模式）；② GAP-X02 operator 生成常数校验前移——`_generate_operator_factor` fallback 生成循环内 `evaluate` 表达式并过滤非常数信号（finite 为空或 nanstd<1e-8 拦截），10 次尝试全拦截抛 RuntimeError，避免下游运行时校验/预筛白跑；③ GAP-X01 横截面预筛真实截面收益——`_quick_prefilter` 横截面模式新增 `_cross_section_prefilter`：全面板信号矩阵 vs 截面 forward 收益（复用 `_cs_execute_factors`/`_cs_build_matrices`/`_cs_compute_ics`，与 `cross_section_evaluate_backtest` 同口径），替代原单标的时序 IC（forward_returns 长度不齐时常被跳过、无法反映截面区分能力）；④ GP 通道增强——`gp_evolver` 模板 `ts_product` 改用 `rolling.apply(np.prod)`（pandas≥2.1 移除 `Rolling.prod`，修复 ts_product 因子全数运行时报错降零）+ `_evaluate_fitness` 后处理对齐流水线（`nan_to_num`+`clip[-10,10]`+std<1e-12 常数罚分，防止 GP 选中下游会被裁剪为常数/近常数的表达式）；⑤ 新增 `scripts/throughput_gp_channel.py` 吞吐实测——operator 因子全链路通过率 0%→100%，GP 产物运行时校验通过率 1/3→3/3、单次耗时 6.15s→2.45s，batch 漏斗 0.4 候选/s（生成主导）；⑥ 新增测试 6 用例（test_compiler 编译产物经流水线执行 +1、test_evolution_loop 常数前置拦截/真实截面 IC/无截面能力拦截 +3、test_gp_evolver ts_product 模板/适应度 clip 对齐 +2）；⑦ 同步 01/06/07/08/09 + pyproject + README |** |
| **v2.65.0** | **2026-08-09** | **GAP-I201 批量挖掘漏斗（Stage 1 首版）：① 新增 `fts/factor_engine/batch_mining.py`——`BatchMiner` 批量漏斗（`BatchMiningConfig` batch_size/max_candidates/max_workers/random_seed + `BatchedProposal`/`BatchGenerationResult` 契约 + 批量生成/并行粗筛/排序截断），依赖注入回调（generate/runtime_check/prefilter）零业务耦合；② `evolution_loop.py` 抽取公共方法 `_evolve_one`（演化分派，支持 method_hint + seed）/`_process_candidate`（Step 2-6 准入链，batch 与单因子路径共用），新增 `_run_batch_generation`（一代批量漏斗：同父多后代方法轮换 macro 至多 1 次 + GP/operator 交替 + seed 递增，token 护栏，全失败回退单因子路径）与 `_batch_generate_one`/`_batch_prefilter`；③ `_quick_prefilter` 返回 (ok, reason, ic) 三元组供排序截断；④ `settings.py` evolution_mode 新增 batch + `batch_size`/`batch_max_candidates`/`batch_max_workers`/`batch_random_seed` 配置（`FTS_BATCH_*` 环境变量）；⑤ 设计文档 `design/D.1-batch-mining-design.md` + 新增 `tests/factor_engine/test_batch_mining.py` 11 用例 + test_evolution_loop batch 集成 10 用例（新增 21 用例），GAP-I201 ✅ 关闭；⑥ 全量回归 + 一致性 13/13 |** |
| **v2.65.0** | **2026-08-09** | **股票流水线 GAP-S03（A 股行业轮动 + 风格轮动 Regime 检测，GAP-I301 Regime 子项）：① 新增 `fts/factor_engine/stock_regime.py`——`StockRegimeSelector` 双维度检测：行业轮动（行业收益面板 → 动量横截面离散度 rotation_strength + top-N 集中度 → concentrated/rotating/balanced 三态）+ 风格切换（大小盘/成长价值指数面板 → 比值尾部动量方向 → large_cap/small_cap + growth/value 双态），复用 `regime_hmm.MultiHorizonHMMDetector` 多周期集成（比值序列构造合成 OHLCV 送 HMM 校正置信度，规则动量方向主判定），空面板/样本不足优雅降级；② `REGIME_STYLE_MULTIPLIERS` 新增 6 个股票风格键（large_cap/small_cap/growth/value/sector_concentrated/sector_rotating，倍率 [0.3,1.5]）；③ `PortfolioLoop.run()` 新增 `stock_regime` 可选参数，market="stock" 且传入时 Step 2.5 优先使用风格 regime 驱动自适应权重（覆盖通用 RegimeAwareSelector）；④ 新增测试 `tests/factor_engine/test_stock_regime.py` 19 用例（行业三态/风格四方向/风格切换样本正确率 ≥80%/空面板降级/HMM 复用回退/multipliers 键与值域/PortfolioLoop 集成 2），test_style_classifier 32 + test_portfolio_loop 197 全绿；⑤ 更新 `01-architecture.md`（股票 Regime 架构块 + 元数据）/`02-lifecycle.md`（Phase 36）/`06-testing.md`/`08-gap-analysis.md`（GAP-I301 部分处理）/`09-advancement-plan.md` + 22 号计划 GAP-S03 ✅；⑥ 全量回归 4321 passed——修复 `test_coverage_edge_cases.py` mock 同步 `_quick_prefilter` 三值签名（GAP-I201 batch 引入的 2→3 元组签名，该测试 mock 未同步导致 `test_consecutive_low_ic_reset_after_promotion` 熔断暂停，mock 改为 `(True, "", 0.05)` 后 25 用例全绿） |** |
| **v2.62.0** | **2026-08-09** | **股票流水线 GAP-S02（Barra 风格因子体系，GAP-I304）：① `fts/factor_engine/barra/` 新包三文件——`barra_style.py`（`BarraStyleEngine` 10 风格暴露计算引擎：size（ln 市值）/beta（市场回归 252d）/momentum（12-1 动量）/residual_vol（回归残差波动）/nonlinear_size（引擎层基于 size 暴露矩阵逐日 z³ 对 z 回归残差，截面依赖二次计算）/book_to_price（1/PB）/liquidity（ln 换手）/earnings_yield（1/PE 与 ROE 合成）/growth/leverage，逐日截面 rank→z-score 标准化，字段缺失全 NaN 降级）+ `barra_neutralizer.py`（`barra_neutralize_matrix` 逐日 OLS `np.linalg.lstsq` 风格暴露 + 行业虚拟变量回归取残差，样本不足降级去均值、常数列剔除、正交性保证）+ `__init__.py`（导出 `barra_neutralize_matrix`/`BarraStyleEngine`/`STYLE_FACTOR_NAMES`/`STYLE_SPECS`/`StyleFactorSpec`）；② 评估链集成——`cross_section_evaluate_backtest` 新增 `style_exposures` 可选参数 + Step 2.6 Barra 风格中性化（行业去均值后叠加风格回归残差，两级中性化链 GAP-S01/S02）；③ 新增测试 `tests/factor_engine/test_barra.py` 13 用例（TestBarraStyleEngine 10 风格齐全/形状/size 单调/未知风格抛错/字段缺失降级 + TestBarraNeutralizeMatrix 残差形状/正交 corr<0.15/size 暴露剥离/空暴露原样/行业叠加/小样本降级 + TestCrossSectionBarraIntegration）；④ 更新 `01-architecture.md`（股票截面中性化架构块两级中性化链）/`02-lifecycle.md`/`06-testing.md`/`08-gap-analysis.md`（GAP-I304 ✅ 已处理）/`09-advancement-plan.md` + 22 号计划 GAP-S02 ✅ |** |
| **v2.61.0** | **2026-08-09** | **L3/L4 机构级追赶 B 阶段（GAP-L303/L304，细则 plans/24）：① GAP-L303 optimizer 接线——`PortfolioLoop.run()` 新增 `factor_returns`/`exposure_matrix` 参数，Step 2 透传 `synthesize_signals`（optimizer 分支按 factor_id 列对齐 + Ledoit-Wolf 收缩协方差（GAP-L302 联动）+ expected_returns Sharpe 代理 + 约束配置透传 + "mvo"→"mean_variance" 别名）；`PortfolioLoop.__init__` 新增 `optimizer_mode`（risk_parity/mvo）/`optimizer_config`；CLI `--mode optimizer` + `--optimizer-mode` 生效；`build_combo` 透传 factor_returns 实测化联动；② GAP-L304 暴露中性化——`OptimizerConfig` 新增 `neutralization`（industry/style）/`exposure_tolerance`，`PortfolioOptimizer.optimize` 新增 `exposure_matrix`/`target_exposure` 参数 + SLSQP 暴露约束（\|B'w − target\| ≤ tol），numpy 降级路径记录不校验警告；③ 新增测试 7 用例（optimizer 端到端 2 + mvo 对比 1 + 中性化 4），test_portfolio_loop 213 + test_portfolio_optimizer 全绿 |** |
| **v2.61.0** | **2026-08-09** | **L3/L4 机构级追赶 A 阶段（GAP-L301/L302，细则 plans/24-l3-l4-institutional-plan.md）：① GAP-L301 因子收益序列层——新增 `fts/factor_engine/factor_returns.py`（`FactorReturnsBuilder` 横截面多空组合收益序列构建，含 `align_to_factors`/`portfolio_returns`/`annualized_sharpe`/`max_abs_correlation` 组合层辅助），`build_combo` 新增 `factor_returns`/`annualize_factor` 参数：提供因子收益矩阵且可对齐（≥20 观测）时组合夏普/相关性由 w×R 实测（`metrics_source="measured"`），缺失或样本不足回退 diversity-adjusted 估算；`PortfolioCombo` 新增 `metrics_source` 字段；② GAP-L302 风险模型——新增 `fts/factor_engine/risk_model.py`（`RiskModelEstimator` 纯 numpy Ledoit-Wolf 收缩协方差：对角结构化目标 + 收缩强度估计，正定性保证 + 特征值/条件数/年化波动率输出，无 sklearn/scipy 硬依赖）；③ 专项计划落盘 + 08/09 登记（GAP-L 系列 11 项：P0×4 / P1×4 / P2×3）；④ 新增测试 21 用例（test_factor_returns 17 + test_portfolio_loop TestBuildCombo 实测化 4），相关回归全绿 |** |
| **v2.61.0** | **2026-08-09** | **股票流水线 GAP-S01（行业/市值中性化主流程，GAP-I207）：① `EvolutionLoop` 新增股票横截面自动注入——`market="stock"` + `cross_section_data` 且 `industry_map` 未显式传入时，读取 `FTSConfig.stock_neutralization`（默认 true）自动加载 `load_industry_map()`（`data/industry_map.json`）+ `cap_map`（`cap_map_path` 配置，缺失返回空 dict），接通 v2.57.0 遗留死配置；② 键归一化——映射键 `600519.SH`/`600519.SZ` 自动剥离后缀生成裸代码键（面板 symbol 为裸代码 `600519`），同时保留原始键，兼容两种格式；③ 空/缺失映射降级——加载返回空 dict 或股票无行业映射时归入 UNKNOWN 组，不抛异常；④ 中性化前后 IC 对比——`cross_section_evaluate_backtest` 返回 `ic`（中性化后）并记录中性化前 IC，供报告对比剥离效果；⑤ 新增测试用例（test_evolution_loop 4 自动注入/键归一化/关闭跳过/空映射降级）；⑥ 更新 `01-architecture.md`/`02-lifecycle.md`/`06-testing.md`/`07-operations.md`/`08-gap-analysis.md`（GAP-I207 ✅ 已处理）/`09-advancement-plan.md` |** |
| **v2.60.0** | **2026-08-09** | **期货流水线机构级缺陷修复（阶段 C，GAP-F01/F08/F09）：① GAP-F01 实盘执行链路——新增 `fts/live_trade/` 包（信号侧完备性，真实网关由 FDT 负责）：`OrderState` 订单生命周期状态机（PENDING/SUBMITTED/PARTIAL/FILLED/CANCELED/REJECTED + 非法转移拦截 + 异常回滚）、`StopOrderManager` 持仓级止损止盈单（触发检查 + 平仓指令）、`InterventionController` 人工干预接口（紧急暂停/一键平仓，权限高于自动化）、`AbstractGateway`/`SimulatedGateway`（下单重试/超时兜底/状态回查）；② GAP-F08 样本外强制——演化晋升路径强制 WalkForward 冷启动验证（`FTSConfig.force_walkforward` 默认 true，可配置跳过并记录原因），`_run_audit` 用多窗口 WalkForwardResult 替代 L1 单段 ICIR 近似；③ GAP-F09 保证金建模——品种保证金率表 + `CapitalAllocator` 保证金占用约束 + 强平风险告警（`FTSConfig.margin_rate_map`/`max_margin_usage`）；④ 修复预存失败 `test_robustness_failure_blocks_promotion`（显式锁定 stock 语境）；登记 GAP-049/051 处理中、GAP-050 部分处理；新增测试用例（live_trade 状态机/止损/干预 + walk_forward 强制 + 保证金约束） |** |
| **v2.60.0** | **2026-08-09** | **期货流水线机构级缺陷修复（阶段 C 第二批，GAP-F04/F05/F06/F07）：① GAP-F04 数据源降级加固——`FTSConfig.mcp_enabled`（默认 false）+ Wind/iFinD 数据源 `set_mcp_handler` 注入 + `_call_mcp` 三级行为（注入→直接调用；启用未注入→抛 RuntimeError 显式报错；未启用→返回 None 明确降级跳过增强字段），`is_available` 改为 `raw is not None`；② GAP-F05 深度时序模型——`fts/ml/models.py` 新增 `MLPFactorModel` 轻量纯 numpy 单隐层 MLP（z-score 标准化 + 动量梯度下降 + L2 正则，无 torch 重依赖），`create_mlp_model` 工厂 + `ModelKind.MLP`，样本不足/非数值抛 `ModelNotAvailableError` 供调用方降级；③ GAP-F06 数据质量监控——新增 `fts/monitor/data_level_monitor.py` 数据级监控器（`DataLevelMonitor`：全表/关键字段缺失率、3σ 异常值比例、复权因子一致性、多源 close 分歧中位数，阈值全部可配置 + 冷却 + 回调），接入 scheduler 每日 04:00 `data_level_monitor_job`（新增 `data_level_monitor` 任务，默认任务 9→10）；④ GAP-F07 组合优化器——新增 `fts/factor_engine/portfolio_optimizer.py`（`PortfolioOptimizer`：risk_parity numpy 迭代等风险贡献 + mean_variance scipy SLSQP 约束优化，含杠杆/集中度/换手/VaR 约束，无 scipy 降级为解析解+投影），接入 `synthesize_signals` 新增 `optimizer` 模式（需 returns_matrix，缺失回退 sharpe_weight）；⑤ 新增测试 62 用例（test_mlp_factor 12 + test_data_level_monitor 22 + test_portfolio_optimizer 19 + test_mcp_degradation 6 + scheduler 任务数断言 9→10/10→11 更新）；⑥ 更新 `01-architecture.md`/`03-configuration.md`/`04-resilience.md`/`05-observability.md`/`06-testing.md`/`08-gap-analysis.md`（GAP-050 处理中：F04/F06/F07 完成、tick 缓存回放后续；GAP-037 处理中：GAP-F05 轻量 MLP 落地，LSTM/RL 远期） |** |
| **v2.60.0** | **2026-08-09** | **期货流水线机构级缺陷修复（阶段 C 第二批，GAP-F04/F05/F06/F07）：① GAP-F04 数据源降级加固——`FTSConfig.mcp_enabled`（默认 false）+ Wind/iFinD 数据源 `set_mcp_handler` 注入 + `_call_mcp` 三级行为（注入→直接调用；启用未注入→抛 RuntimeError 显式报错；未启用→返回 None 明确降级跳过增强字段），`is_available` 改为 `raw is not None`；② GAP-F05 深度时序模型——`fts/ml/models.py` 新增 `MLPFactorModel` 轻量纯 numpy 单隐层 MLP（z-score 标准化 + 动量梯度下降 + L2 正则，无 torch 重依赖），`create_mlp_model` 工厂 + `ModelKind.MLP`，样本不足/非数值抛 `ModelNotAvailableError` 供调用方降级；③ GAP-F06 数据质量监控——新增 `fts/monitor/data_level_monitor.py` 数据级监控器（`DataLevelMonitor`：全表/关键字段缺失率、3σ 异常值比例、复权因子一致性、多源 close 分歧中位数，阈值全部可配置 + 冷却 + 回调），接入 scheduler 每日 04:00 `data_level_monitor_job`（新增 `data_level_monitor` 任务，默认任务 9→10）；④ GAP-F07 组合优化器——新增 `fts/factor_engine/portfolio_optimizer.py`（`PortfolioOptimizer`：risk_parity numpy 迭代等风险贡献 + mean_variance scipy SLSQP 约束优化，含杠杆/集中度/换手/VaR 约束，无 scipy 降级为解析解+投影），接入 `synthesize_signals` 新增 `optimizer` 模式（需 returns_matrix，缺失回退 sharpe_weight）；⑤ 新增测试 62 用例（test_mlp_factor 12 + test_data_level_monitor 22 + test_portfolio_optimizer 19 + test_mcp_degradation 6 + scheduler 任务数断言 9→10/10→11 更新）；⑥ 更新 `01-architecture.md`/`03-configuration.md`/`04-resilience.md`/`05-observability.md`/`06-testing.md`/`08-gap-analysis.md`（GAP-050 处理中：F04/F06/F07 完成、tick 缓存回放后续；GAP-037 处理中：GAP-F05 轻量 MLP 落地，LSTM/RL 远期） |** |
| **v2.59.0** | **2026-08-09** | **期货流水线机构级缺陷修复（阶段 B，GAP-F03 + GAP-F02）：① GAP-F03 期货截面因子板块中性化主流程——`EvolutionLoop(market="futures")` 自动从 `FUTURES_SECTOR_MAP` 反向构建 `{symbol: sector}` 板块映射注入 `cross_section_evaluate_backtest`（industry_map），截面信号按板块去均值剥离产业链/板块系统性偏差；新增 `FTSConfig.futures_neutralization`（默认 true）；② GAP-F02 回测真实性仿真——`BacktestPipeline._compute_strategy_returns` 新增可交易掩码：涨跌停拦截（close 单日涨跌幅 ≥ `futures_limit_pct` 默认 8% 持仓保持）+ 停牌过滤（volume==0 持仓保持），报告 summary 新增「被拦截成交统计」（涨跌停/停牌次数）；新增 `FTSConfig.backtest_trade_filter`（默认 true）/ `futures_limit_pct`；`BacktestInput` 增加 `trade_filter`/`limit_pct` 可选参数；配置关闭时跳过拦截回归兼容；登记并处理 GAP-047/048（plans/21-futures-maturity-optimization-plan.md）；新增 ~12 测试用例 |** |
| **v2.58.0** | **2026-08-09** | **期货连续合约复权 + 展期仿真（Phase 34 / GAP-046，阶段 A + 阶段 B 文档先行）：① `RollCalendar` 换月日历模块（`fts/data_sources/roll_calendar.py`）——从 `contract_kline` 具体合约日线按每日最大成交量判定主力，构建换月事件序列（date/old_contract/new_contract/adj_ratio），比率法后复权因子（adj_ratio = 切换日新合约收盘/旧合约收盘）；② 数据层——`migrate_schema` 幂等补 `kline_cache.adj_factor` 列 + 建 `contract_kline` 表（原仅外部管道写入、FTS 无建表/写入逻辑），`sync_futures_data_job` 补拉具体合约日线；`FuturesDataProvider.get_ohlcv(adjusted=True)` 默认返回复权序列，`contract_kline` 缺失时降级返回原始拼接序列；③ 回测层——`TransactionCostModel` 新增展期成本项（`CostConfig.roll_cost_bps` 期货默认 2.0，`adjust(dates/roll_dates)` 持仓穿越换月日扣 \|position\| × roll_cost_bps，AdjustedMetrics 新增 `roll_cost_bps` 统计字段），`BacktestPipeline` 持仓穿越换月日扣除展期价差，报告新增「展期成本统计」（换月次数/年化展期成本）；④ 配置——`futures_adjusted`（默认 true）/ `roll_cost_bps`（默认 2.0）；登记并处理 GAP-046；新增 ~22 测试用例；阶段规划落盘 `plans/20-futures-roll-adjustment-plan.md`（阶段 B P1 缺陷改进候选清单） |** |
| **v2.57.0** | **2026-08-09** | **股票因子行业/市值中性化预处理（Phase 34，股票流水线补强方向①落地）：① `CrossSymbolOps` 新增 `industry_cap_neutral` 双重中性化算子——先按行业分组去均值消除行业系统性偏差，再按市值加权去均值消除市值偏差（行业列 NaN 归入 UNKNOWN 组，避免 groupby 丢弃 NaN 分组），算子注册表同步注册 `industry_cap_neutral`；② `cross_section_evaluate_backtest` 新增 `industry_map` / `cap_map` 可选参数，提供时对信号矩阵做行业（或行业+市值）中性化后再计算截面 IC 与多空收益，新增 `_neutralize_signal_matrix` 辅助函数（行业去均值 + 市值加权去均值，NaN 与 UNKNOWN 行业优雅处理）；③ `EvolutionLoop` 新增 `industry_map` / `cap_map` 注入参数并在 `_evaluate_cross_section` 传递；④ `FTSConfig` 新增 `stock_neutralization`（默认 true）/ `industry_map_path`（默认 data/industry_map.json）/ `cap_map_path` 配置项 + `load_industry_map()` 加载函数（非 dict 根/空白键过滤/文件缺失返回空 dict）；⑤ 新建 `data/industry_map.json` 示例行业映射（申万一级分类，24 行业 80 标的）；新增 ~17 测试用例（feature_ops 2 + evaluation_chain 7 + config_settings 8），166 相关测试全绿 + 横截面评估路径 2 用例全绿 |** |
| **v2.56.0** | **2026-08-09** | **Adaptive 权重完整接入 L3（Phase 33 / GAP-045）：① `synthesis_mode` 扩展 `adaptive`，L3 Step 2 委托 `PortfolioConstructor`（回测/生产统一入口，消除两套路径不一致）；② `RegimeSmoother(alpha=0.5, min_days=2)` 接入 Step 2.5——Regime 切换权重指数平滑，参数走新增 `AdaptiveWeightConfig`（dimension=family/style/both）；③ 落地原设计 A.3 未实现的 FactorStyle/style_tags 维度——`FactorStyle` 枚举 + `factor_catalog.style_tags` 列（DuckDB 兼容补列）+ `REGIME_STYLE_MULTIPLIERS` 双维度调整（family×style 乘积 clamp [0.5,1.5]×base）。登记 GAP-045（P1，1 月内）|** |
| **v2.54.0** | **2026-08-09** | **精英因子全员质量巡检（Phase 32）：新增 `scripts/elite_quality_inspection.py` 质检脚本，使用 HighICScreener 对存量 230 精英因子执行全员质量巡检（含 5 项一票否决 + 16 项打分 + A/B/C 评级）。修复种子因子 `evaluation.level_2_economic` 全 0 占位值误触 V5 否决问题（`_build_screener_kwargs` 增加 `factor.economic_logic` fallback）。结果：股票 129/129 合格（15 A + 114 B），期货 100/101 合格（9 A + 91 B），`volume_price_efficiency_ratio`（fct_4d2d6c01，institutional=1 < 2.0）出库至 `_retired/`。质检报告保存至 `reports/2026-08-09/` |** |
| **v2.53.0** | **2026-08-09** | **L2 期货演化 JSON 序列化修复 + 成功运行（Phase 31）：修复 `experience_chain.py` `_write_trace` 中 `json.dumps` 缺少 `default=str` 导致 `TypeError: Object of type bool is not JSON serializable` 错误（经验链记录非可序列化类型时崩溃、演化中断）。修复后成功执行 50 代期货演化：`elite_count=2`（`fct_575a7d05`、`fct_97b79846` 晋升精英），`shadow_pool=89` 因子进入观察期；4 个因子通过 Verifier 质检但因家族多样性限制（'other' 家族已达上限 15）被拦截；`fut_gp_alpha1_g49` 因子 IC=0.49 但鲁棒性审查未通过；消融/鲁棒性判定已全部处理（`SingleAblation` feature 字段、`_compute_ic` NaN 掩码、`_run_ablation_check` 信息型/拦截型判定） |** |
| **v2.52.0** | **2026-08-09** | **鲁棒性缺失值测试阈值放宽（Phase 30，解除 L2 期货演化 100% 失败率熔断）：`robustness.RobustnessTester` 默认 `missing_retention_threshold` 从 0.80 降至 0.50（与 OOD 测试对齐）。根因：L2 期货演化（v2.50.0）12 个种子因子全部被鲁棒性缺失值测试拦截——随机单元格级 NaN 注入比真实数据质量问题激进得多（5% 随机 NaN 即使高质量种子 IC=0.49 的保持率也降至 0.56），父因子池为空导致后续 GP 演化全部退化（11 个常数信号因子）、总失败率 100% 熔断。0.50 阈值合理：OOD 测试已用 0.50 阈值，真实数据缺失通常是整列缺失而非单元格随机。新增 GAP-044，关闭 GAP-044；无新增测试 |** |
| **v2.51.0** | **2026-08-09** | **预存失败清零 + 依赖管理统一：① 修复 3 个 test_evolution_loop 预存跳过——`promote_to_elite`/`failure_rate_circuit_breaker` 解除 GAP-030 无条件 skip（后者适配 v2.50.0 种子 Verifier 共用：按种子/演化阶段计数区分判定，种子阶段通过晋升提供父因子、演化因子拒绝触发失败率熔断），`record_experience_traces` 补种子评估+审查 mock 消除 MockLLM 合成数据随机 skip；② 安装并声明 `hmmlearn`（regime HMM 依赖），激活 test_regime/test_regime_hmm 依赖缺失 skipif，regime 相关 129 用例全绿；③ 依赖管理统一——pyproject.toml 补全 `duckdb` 核心依赖（原仅环境装有未声明），新增 `regime`/`monitor`/`data`/`all` 四个 extra（hmmlearn/statsmodels/fastapi/uvicorn/requests/tqdm/pyarrow 等），新建 `requirements.txt` 统一一键安装入口（`-e .[all,dev]`，唯一版本源 pyproject.toml），README 可选依赖表同步；全量回归 4020 passed / 0 failed / 0 skipped（1 例 test_package_init 版本断言因并行会话回归中途 bump 2.52.0 失配，重跑 3/3 通过，非代码问题） |** |
| **v2.50.0** | **2026-08-09** | **种子质检全链对齐 + 质检拦截器判定缺陷修复（P0，解除 L2 演化 100% 失败率熔断）：① Phase 28 种子因子质检全链对齐——`_evaluate_and_promote_seeds` 补齐与演化因子完全同强度的质检链（新增 Verifier 判定、消融实验、因果结构审查、鲁棒性审查、SHAP 分析），L1 注入候选与人工精选种子一视同仁；② 消融实验判定语义修正——`shuffle_dates`（时间戳打乱）/成交量置零/VWAP 替换与核心价格列（open/high/low/close/vwap/settle）置零改为「信息型」判定（时序因子依赖时序因果、价格因子依赖价格列属必要特征，不再误判伪相关），仅「非价格列」置零导致 IC 降幅 >50% 才拦截；根因：L2 期货演化 15 代中 5 个通过 Verifier 的候选（IC 0.31~0.52）全部被消融实验（2 次，shuffle_dates 打乱时序使 IC 归零）与鲁棒性缺失值测试（3 次，IC 计算无 NaN 兜底致保持率恒 0）误杀，失败率 100% 熔断；③ `_compute_ic` NaN 掩码兜底——spearmanr/pearsonr 计算前剔除 NaN 对，缺失值鲁棒性测试注入 NaN 后 IC 不再恒为 0，测试真实生效；④ `SingleAblation` 新增 `feature` 字段记录置零列；⑤ 上一轮已部署的期货审计放宽（`min_oos_pass_ratio` 0.5→0.3）与种子 lookback 243→120 参数调整本轮验证生效（4 种子晋升）；⑥ vwap 近似因子通用 IC 门槛（审计层统一）——`evaluation_chain.evaluate()` 失败原因汇总新增检查：code 含 `vwap` 且 abs(IC)<0.08 判失败，覆盖种子+演化全路径（原仅种子 loader `risk_tag` 打标生效、演化生成器不打标漏检）；种子全链质检测试补强 4 用例（Verifier/消融/因果/鲁棒任一失败均拒绝晋升）；关闭 GAP-043；新增/更新 ~18 测试用例 + vwap/种子全链 7 测试，tests/factor_engine/ 回归无新增失败 |** |
| **v2.49.0** | **2026-08-09** | **高IC因子筛查剔除（Phase B.4）：新增 `fts/factor_engine/high_ic_screener.py`，将「高IC因子筛选打分表」（docs/Knowledge/高IC因子筛选打分表.xlsx）固化为自动筛查流程——16 项检查 × 6 大模块总分归一化 100 分 + 5 项一票否决（外样本衰减>30%/极值扰动>25%/存量相关>0.7/成本后超额≤0/无业务逻辑）任意触发直接 C 级剔除 + A/B/C/PASS 四级评级（A≥85 入库、B 60~84 暂缓优化、C<60 剔除、PASS 数据不足放行）；集成到 `_promote_to_elite` 入库质检强制 Gate（所有市场统一启用），筛查报告写入 elite 快照 `high_ic_screen` 字段；25 个筛查测试全绿 + promote/elite 集成测试 16 通过无回归 |** |
| **v2.48.0** | **2026-08-09** | **L1 Meta-Loop 注入质量优化（P0-P2）：① P0 经济逻辑评分量规——`bootstrap_factors`/`_llm_extract_factors` 双 prompt 明确 0-5 评分量规（3-5 分需机制论证、禁止默认 2 分、institutional 期货评分口径），修复 LLM 锚定偏差（theory 恒 2 / institutional 恒 0-1），复跑注入率 45%→95%，熔断不再触发；② P1a 熔断逻辑区分硬失败/软失败——编译失败/重复（硬失败）计入连续低质量计数，经济逻辑评分/narrative 不达标（软失败）不计入，避免 LLM 评分波动误触熔断连带丢弃合格候选；③ P1b extractor `_llm_extract_factors` complete 路径落盘 debug 原始响应（`debug_llm_response_{trace_id}_{source}.txt`）；④ P1c verify 拒绝日志输出具体编译错误 detail（暂存 bootstrap 阶段 failure_reasons，避免被 verifier 覆盖）；⑤ P2 extractor prompt 沙箱约束声明（仅白名单模块、ML 框架降级 numpy）；新增 5 测试（meta_loop 3 + extractors 2），251 相关测试全绿 |** |
| **v2.47.0** | **2026-08-09** | **测试覆盖率冲刺 v1（77%→94%）+ 14 个真实 bug 修复 + 全量回归清零：① 新增/重写 ~600 测试用例（factor_optimizer 46、standardizer 41、portfolio_loop +90、extractors base/stock/futures 80、jobs 29、risk_manager 26、prometheus_metrics 38、cli_extra 102、data_futures 72、regime +59、regime_hmm 24、aggregator +62、llm +26、http_server +45、tqsdk_source 18 等），测试文件 52→116，测试数 2157→3836；② 修复 14 个真实产品 bug（P0：regime.py 模块级 logger 未定义 NameError、http_server.py DuckDB 列名获取 fetchall 无 description；P1：data_futures retry 并发异常类捕获、cli.py catalog stats 数值格式、cli.py cross_market import 位置、cli.py seeds 函数名、data.py 基本面 provider 未初始化、llm.py 嵌套 JSON 截断补全、portfolio_loop OOS 验证缺 pd/np 导入、factor_optimizer 单品种 min_len 算法 bug 等）；③ 覆盖率 77%→94%（20326 statements，31 模块 100%，<90% 缺口 16 模块登记待补）；④ 全量回归 3836 passed, 0 failed, 3 skipped |** |
| **v2.46.0** | **2026-08-08** | **产业链品种覆盖补漏：① `FUTURES_SECTOR_MAP` 有色金属链补入 AL0（铝）——既有遗漏（基本金属含铜/铝/锌/铅/锡/镍，铝本体此前未归链），修复后 82 品种全部有链归属、无跨链重复；② 同步 12-factor-generalization-plan.md（7 类→13 类，消除与链列表的内部矛盾）；③ 同步 06-testing.md（test_sector_regime 用例数 9→15）；08-gap-analysis GAP-018 为已关闭历史记录，保持 7 类原样；无新增测试，test_sector_regime 回归全绿 |** |
| **v2.45.0** | **2026-08-08** | **铂钯归位贵金属：`FUTURES_SECTOR_MAP` 贵金属链由黄金/白银扩为黄金(AU0)/白银(AG0)/铂(PT0)/钯(PD0)，铂钯自"新能源/新材料"链移入（铂族金属 PGM 与黄金白银同属贵金属板块，大宗商品本质分类修正）；"新能源/新材料"链保留碳酸锂(LC0)/工业硅(SI0)/多晶硅(PS0)；产业链总数保持 13；同步 01-architecture.md；无新增测试，test_sector_regime 回归全绿 |** |
| **v2.44.0** | **2026-08-08** | **产业链分类调整 + OP0 品种名修正：① `FUTURES_SECTOR_MAP` 按产业链逻辑拆分原"纸浆集运"链为"造纸/林浆纸"（SP0 纸浆 / LG0 原木 / FB0 纤维板 / OP0 双胶纸，林浆纸一体化）与"航运"（EC0 集运欧线单列，航运运价驱动独立于商品产业链）两链；② OP0 从上期所"铜/铝衍生"（有色金属链）移入造纸链，并修正 `FUTURES_SYMBOL_NAMES` 品种名错误映射（"钨"→"胶版印刷纸/双胶纸"，与 `_SYMBOL_MARK_NAMES` 一致）；③ 农产品链移除 LG0/FB0；产业链总数 12→13；同步 01-architecture.md 与 alignment_backtest.py 说明；无新增测试，test_sector_regime 回归全绿 |** |
| **v2.43.0** | **2026-08-08** | **UI 新增候选因子展示区块：① 后端新增 `_build_candidate_list` 读取 factor_pool.json，输出 count/pending_count/factors（含 evaluation_status/status/source/priority/parent_topic，pending 优先排序），`/api/candidates` 路由；② dashboard 新增"候选因子（L1 池 · 未评估）"区块（表格 + 最大高度滚动，按来源分组，状态标签待注入/已注入/已精英 + 未评估/已评估徽标），refresh 每 10 秒加载；③ 实测返回 1207 条候选（pending 8 / injected 519 / elite 680，来源 l1_bootstrapping 457 / l1_extractor_pipeline 70 / l2_evolution 680）；新增 3 测试用例（_build_candidate_list 空池/列表/旧记录缺省），4/4 全绿 |** |
| **v2.42.0** | **2026-08-08** | **展示层"未评估"标注：① `/api/factors` 后端两路径（DuckDB + JSON fallback）输出 `evaluation_status`（ic/sharpe>0 → evaluated，否则 pending）；② dashboard 因子表 IC 列显示"未评估"徽标（pending 时）、详情页新增"评估状态"行；③ CLI `fts factor list` 对无评估指标因子输出"未评估"（兼容 DuckDB 顶层字段与 JSON 嵌套两种结构）；④ 新增 2 测试用例（http_server fallback 标注 + CLI 未评估显示），验证 DuckDB 302 条精英全部 evaluated 无误标 |** |
| **v2.41.0** | **2026-08-08** | **空指标治理 + 候选因子未评估标记：① 清理 2 条测试污染精英记录（`fct_promote_test`/`fct_promote_test_unique`，source=manual，ic/sharpe=0，级联删除 factor_evaluations/factor_versions/factor_catalog 各 2 行，迁移前备份）；② `FactorPoolEntry` 契约新增 `evaluation_status` 字段（pending=未评估/evaluated=已评估），`FactorPoolManager.add_entry` 入池默认补 `pending`；③ factor_pool.json 一次性迁移：1207 条候选因子全部补齐 `evaluation_status="pending"`（迁移前备份）；新增 2 测试用例，TestFactorPoolManager 7/7 全绿 |** |
| **v2.40.0** | **2026-08-08** | **因子库来源子家族拆分（qlib/gtja/wq101）：① `FactorFamily` 新增 3 个标准家族（qlib/gtja/wq101，14→17 大类）；② `_infer_factor_family` 按名称前缀映射（`qlib_`→qlib、`gtja_`→gtja、`alpha_`/`wq_`→wq101，`fut_` 保持 trend）；③ `seed_loader._infer_family_from_filename` 与 YAML 种子对齐（qlib158.yaml/gtja191.yaml family 字段更新）；④ DuckDB 一次性数据迁移：111 条 `cross_section` 记录按前缀拆分（qlib 43 / gtja 36 / wq101 30 / fut_gp_*→behavioral 2），迁移前已备份；新增 12 测试用例（contracts 8 + seed_loader 4），相关测试全绿 |** |
| **v2.39.0** | **2026-08-08** | **GAP-038 种子相关性预检卡死修复：`_run_seed_correlation_check` 横截面模式增加规模保护（种子数 >50 时跳过相关性预检）。根因：184 种子 × 25 品种 × 500 日横截面 Spearman 相关计算量过大（>10 分钟），ThreadPoolExecutor timeout 无法中断卡在 numpy/scipy C 扩展的线程；跳过仅放弃"标记不删除"的轻量预检，冗余控制由 L3 组合层承担（ACTIVE_FACTOR_CAP=20 + Elastic Net + 因子聚类）。演化流程恢复正常（13 代后失败率熔断属预期保护）；登记并关闭 GAP-038；无新增测试 |** |
| **v2.38.0** | **2026-08-08** | **ML 模型集成层 + VNPY 信号桥接层：① Phase 24 新增 `fts/ml/` 包（`MLSignalModel` 封装 LightGBM/XGBoost/Ensemble 三种模型，`SignalModelTrainer` 支持横截面回归/时序预测/集成融合三种训练模式，可选依赖 [`ml`] extra，缺依赖时优雅降级回退）；L3 信号合成新增 `ml_ensemble` 模式；② Phase 25 新增 `fts/bridge/` 包（`SignalBridge` 实现 JSON/Redis/REST 三种协议的交易信号格式转换，`fts bridge` CLI 子命令支持 serve/status 操作，可选依赖 [`bridge`] extra）；③ 记录未实现功能：深度学习时序模型（LSTM/GRU/Transformer）与强化学习（RL，DQN/PPO/SAC）登记为 GAP-037；新增 ~55 测试用例 |** |
| **v2.37.0** | **2026-08-08** | **GAP-036 L1 注入候选文件激进清理：`_merge_l1_candidates` 消费后立即删除 + `_promote_to_elite` 晋升后立即删除 + 历史遗留一次性清理（对比 factor_pool.json 已消费状态），非阻塞设计，删除失败仅记录 warning；无新增测试 |**
| **v2.36.0** | **2026-08-08** | **P1 因子聚类 + P2 PCA 降维：新增 `fts/factor_engine/factor_clustering.py` 模块，`FactorClusteringEngine` 实现信号相关性层次聚类 + 代表因子选择（Pearson 相关系数 → 层次聚类 → Sharpe 最高代表），`PCASignalCompressor` 实现 PCA 信号降维压缩（z-score 标准化 → PCA 保留 95% 方差 → 载荷矩阵映射因子权重）；集成到 L3 PortfolioLoop 的 Step 1.8（P1 聚类，默认启用）和 Step 1.9（P2 PCA，可选关闭）；关闭 GAP-034 和 GAP-035；新增 34 测试用例 |**
| **v2.35.0** | **2026-08-08** | **Elastic Net 信号合成 + ACTIVE_FACTOR_CAP：L3 组合构建默认信号合成模式从 equal_weight 切换为 elastic_net（Elastic Net 截面回归，L1+L2 自动变量选择，防止冗余因子稀释组合夏普）；新增 ACTIVE_FACTOR_CAP=20 活跃因子数量上限，因子数超过上限时按 Sharpe 排名保留 Top N，自动过滤低质量因子；期货 CLI 默认 synthesis_mode 同步切换为 elastic_net（原 sharpe_weight）；109 相关测试全绿，无回归** |
| **v2.34.0** | 2026-08-08 | 全活跃因子入口 NaN 防护批量同步 + 回测无效代码显式失败：① 基于 v2.33.0 修复结果，将 g10/g11/g13 同族因子的边界处理逻辑推广至全部 77 个缺防护活跃因子（76 A 型原始赋值 + 1 B 型 np.asarray 入口），统一插入 `np.asarray(float64)` + 首个有限值 NaN 填充；逐因子验证语法合法、正常输入输出与修复前逐位一致（0 mismatch）、前部/尾部/周期 NaN 输入不崩溃且输出全有限（231 场景全绿）；② 修复 `BacktestPipeline._execute_factor_code()` 静默失败——因子代码顶层 exec 异常不再降级返回零值数组，改为上抛由外层包装为 FactorComputeError 使回测显式失败（无效代码 `raise RuntimeError` 场景，消除静默失败掩盖真实错误）；新增 232 测试用例（test_bincount_boundary.py 21→253，动态扫描 77 因子 × 3），回测流水线 6 测试全绿 |
| **v2.33.0** | 2026-08-08 | 宏观因子降级 + 适用场景重设计 + np.bincount 边界审计：① fut_macro_export 家族 6 因子（fct_01f132dc/fct_5d783863/fct_e10560e2/fct_0591e8e3/fct_1fad8dfc/fct_2bcd330b）全部 retire——真实 EDB 数据对比证实代理模式 Sharpe 7.68 为假象，真实数据 IC≈0（单品种时序无预测力）；② 角色边界重设计：宏观因子限定跨品种/板块层面（SectorRegimeSelector / 组合风险预算 / 跨市场泛化），禁止进入单品种时序信号管道；③ 修复 `FactorRepository.get_factor()` 残留读事务阻塞其他连接 DDL（fetchone→fetchall 完整消费），`update_factor_status()` 旧库缺列幂等补列；④ 审计 3 个含 np.bincount 精英因子（fct_70d783d1/fct_71372ef2/fct_7b251afa）输入边界：入口 NaN 清理 + bincount 输入 nan_to_num/clip 防御，实测 NaN 输入不再传播非有限输出；⑤ 同步入口 NaN 防护到活跃池同族因子（fut_hf_trade_imbalance_g10/fut_bias_g11/fut_option_pcr_g10/fut_gp_alpha1_g13）；新增 24 测试用例（repository 3 + bincount 9 + g 因子 NaN 防护 12），全绿 |
| **v2.32.0** | 2026-08-08 | 宏观字段增强层：① `IFindSource.get_macro_series()` 实现 edb_cache 缓存读写（查 → miss 调 fetch_edb → 幂等写回）；② 新增 `fts/data_sources/macro_aligner.py`（`MacroFieldAligner.align()` 月度→交易日 ffill + 发布滞后防未来函数 + `inject_macro_fields()` 批量注入 + `MACRO_FIELD_QUERIES` 映射表）；③ `BacktestPipeline._compute_factor()` 因子执行前注入宏观列（export/import_data/cpi/rate/us_bond），宏观因子不再走 close 代理降级；④ `FTSConfig` 新增 `macro_field_injection` / `macro_lag_days` 配置；新增 ~8 个测试用例 |
| **v2.31.0** | 2026-08-08 | 分钟级回测 Phase 2 完成（分钟级微观结构特征分析）+ 数据源修复 + tick 数据源接入：① 修复 TDXMinuteSource 主力连续代码映射（RB0→RBL8.SHF/IF0→IFL0.CFF）与列字典解析、60m 周期参数（1h）、聚合器分钟源按请求频率动态重建；② 新增 `scripts/minute_microstructure_analysis.py`（多频率对比/日内波动/IC 衰减/信号自相关/换手率分析）；③ 新增 TQSDK tick 逐笔数据源（`TQSDKTickSource` 通过 `get_tick_serial` 获取 5 档盘口，tick_cache 表缓存，`FuturesDataAggregator.get_ticks()` + `FuturesDataProvider.get_tick_data()` 接口，DataSource 新增 TQSDK_TICK 枚举）；新增 29 个 TDX 适配器测试 + 10 个 tick 数据源测试；实测通达信 17709 提供 5m 10000 根（≈7.5 个月），TQSDK tick 5000 行（≈42 分钟）；同步更新文档 |
| **v2.30.0** | 2026-08-08 | 分钟级回测 Phase 1: 三源分钟数据源适配（通达信 TDX HTTP 17709 + TQ-Local 7721 + 天勤 TQSDK），DuckDB minute_cache 缓存，聚合器扩展支持分钟级数据路径，回测引擎增加 frequency 参数（年化因子/窗口/成本自适应），CLI 增加 --frequency 参数；同步更新 28 文档版本至 v2.30.0 |
| **v2.29.0** | 2026-08-08 | P2 已知问题修复三连：① `business_flow.md` / `execution_modes_flowchart.md` 补全一致性元数据章节；② 回测信号统一为 20 日滚动窗口 z-score（`_compute_strategy_returns` 新增 `zscore_window` 参数，默认 20，与 `signal_generator` 实盘信号一致）；③ forward_returns 末尾 period 天零值截断（`_compute_factor` 中 truncate 逻辑，确保 IC/策略收益/绩效指标不引入零值偏差）；同步更新 28 文档版本至 v2.29.0 |
| **v2.28.0** | 2026-08-08 | 回测流水线计算错误修复：P0 换手率指标修正（`_calculate_metrics` 从 `positions` 而非 `returns` 计算 turnover，修复 15x 低估）；P1 成本时序对齐修正（`_compute_strategy_returns` 使用 `positions[i-1] * forward_returns[i-1]` 正确对齐）；P1 IC 计算方法统一（`_compute_ic_series` 从 Pearson 改为 Spearman 秩相关系数，与系统其他模块一致）；同步更新 docs/harness/06-testing.md 和 07-operations.md 版本号至 v2.28.0 |
| **v2.27.0** | 2026-08-07 | 跨市场泛化验证 Phase A（P0，期货→股票/ETF 泛化验证引擎 + 数据适配层 + 报告输出 + CLI 集成）：新增 `fts/cross_market/` 模块（`CrossMarketDataAdapter` 统一数据格式 + `CrossMarketEngine` 跨市场 IC 计算与因子分类）、`scripts/cross_market_revalidation.py` 独立脚本、`fts factor cross-market` CLI 子命令（三个方向：futures-to-stock/futures-to-etf/stock-to-futures）、20 测试全绿 |
| **v2.25.0** | 2026-08-07 | 自动化因子提取管道 P0-P4 全部完成：P0 天软因子提取器（27 个新因子，tinysoft.yaml）；P1 券商研报因子提取器（7 个新因子，broker_reports.yaml）；P2 学术论文因子提取器（6 个新因子，academic_papers.yaml）；P3 统一转换器 + 验证器（unified_factor_converter.py，支持跨文件去重/语法验证/格式一致性/统计报告生成）；P4 集成到 CLI（`fts seed validate/report/dedup` 子命令组，支持 `--market futures/stock`）；期货种子因子 121 个（17 文件），股票种子因子 645 个（6 文件），总计 766 个种子因子，无跨文件重复 |
| **v2.24.0** | 2026-08-07 | 新增 seed_lineage 种子溯源表（L0→L2 全链路）：schema.py 新增第 12 张表 seed_lineage（lineage_id/seed_name/seed_family/seed_market/evolved_factor_id/evolved_factor_name/generation/parent_id/trace_id/promoted_at + 4 索引）；verify_database 新增 seed_lineage 统计（行数/家族分布）；迁移脚本 scripts/migrate_add_seed_lineage.py（幂等，支持 --dry-run）；01-architecture 文档同步表数 11→12 |
| **v2.22.0** | 2026-08-07 | L1 候选因子评分缺陷修复 4 项：P1 经济逻辑评分默认值统一为 3（evaluation_chain.py evaluate_economic_logic 默认值 0→3，evolution_loop.py _merge_l1_candidates 预填默认值）；P2 换手率缺失保护（L1 候选因子 turnover=0 时默认 0.5）；P3 种子因子 WalkForward 缺失补全（轻量 2 窗口验证）；P4 评分映射函数可配置化（factor_quality_card.py 从 config 读取阈值，factor_quality_card_config.py 扩展 to_factor_quality_card_config 输出）；150 测试全绿 |
| **v2.21.0** | 2026-08-07 | 存储方案优化 4 项：P1 _promote_to_elite 去重仅依赖 DuckDB 权威数据源，移除冗余 JSON 文件 glob 扫描；P2 新增 oversub-type 参数；P3 晋升/淘汰操作自动写 catalog_consistency.jsonl 一致性日志；P4 ART 索引 workaround 添加 DROP/CREATE 计数器日志；新增 `fts catalog stats/verify/backup` CLI 命令组；11 测试全绿 |
| **v2.20.2** | 2026-08-07 | 修复 Step 7.5 质量报告 DuckDB 失败：`_generate_quality_report` 新增 DuckDB C 扩展加载失败时 combo 文件回退机制（双路径 A→B），增加异常堆栈 debug 日志和报告 source 字段标记数据来源；90 测试全绿 |
| **v2.20.1** | 2026-08-07 | 修复测试：`_mock_seed_evaluation_pass` mock key 从 `"multiple_test"` 更正为 `"level_3_multiple"`；`test_promote_to_elite` 系列测试补全 `level_3_multiple={"passed": True}` 避免假阴性；`test_promote_to_elite_audit_fails_blocks_promotion` 修正为验证审计报告写入而非 `path is None`（审计阻塞在 `run()` 中执行）；218 测试全绿 |
| **v2.20.0** | 2026-08-07 | 产业链级市场制度检测：新增 `SectorRegimeSelector` 类对每个产业链独立检测市场制度（bull/bear/oscillate/high_vol/low_vol），从品种面板构建合成 OHLCV（close 截面均值 + volume 截面和），每个产业链使用独立 `RegimeAwareSelector` 实例保持状态隔离；`futures_signal_pipeline.py` 集成 `SectorRegimeSelector`，Step 2b 替换为产业链级检测 + 品种数加权主制度计算；报告新增产业链 Breakdown（各产业链制度/置信度/品种数/方向建议）；新增 `test_sector_regime.py` 9 测试用例全绿；文档同步（01-architecture 产业链级检测章节 + 06-testing 测试条目） |
| **v2.19.0** | 2026-08-07 | P0/P1 过拟合修复：A. 组合夏普改为 diversity-adjusted 加权（HHI 权重集中度折扣）；B. 因子 Sharpe 上限截断 3.0（原始值保留 `_sharpe_raw`）；C. 评价窗口从 120d 扩展至 500d（`MIN_EVAL_DAYS=500`）；D. L3Verifier 新增 `max_sharpe=3.5` 过拟合保护检查；E. 随机化测试改为 Dirichlet 权重重采样；F. 质量卡 Sharpe>10 线性惩罚（Sharpe=20 归零）；修复 `build_combo` 中 `n_ret` 引用前赋值 bug；修复 `EvolutionLoop` 引用的已删除 `pipeline.FactorQualityInspection` 回归错误（`_QualityInspectionCompat` 兼容包装）；`test_portfolio_loop.py` 新增 6 测试用例，90 全绿；`test_factor_quality_card.py` 105 全绿 |
| **v2.18.0** | 2026-08-07 | 因子家族多样性约束：`_promote_to_elite` 新增家族数量检查（`max_per_family=3`），限制单一家族因子过度繁殖；`BudgetConfig` 新增 `max_per_family` 字段；配置文档同步更新 |
| **v2.17.0** | 2026-08-07 | 因子淘汰主流程集成：`FactorRepository.retire_factor()` 方法实现 DuckDB 状态更新 + JSON 文件迁移至 `_retired/` + 状态变迁记录；`monthly_decay_eval_job` 调用 `retire_factor()` 将 AutoRetireManager 标记的淘汰因子同步到主存储；修复 `update_factor`/`update_factor_status` DuckDB ART 索引 bug（DROP → UPDATE → 重建索引）；因子淘汰正式成为主流程环节 |
| **v2.16.0** | 2026-08-06 | 孤立模块集成 Phase 2：清理 30+ 死代码文件（strategies/pipeline/data_cache 等）+ LogicMonitor 注册为每日 22:00 调度任务 + FactorInspector 注册为每日 03:00 调度任务 + ProcessWatchdog 集成到 SchedulerEngine.start_watchdog() 后台守护线程 + 文档同步 |
| **v2.15.0** | 2026-08-06 | P0 修复：GP 演化/算子演化数据泄露（`train_mask` 隔离训练集适应度，阻止 OOS 数据泄漏）；IC 衰减硬编码修复（`_compute_decay_6m` 滑动窗口 IC 线性回归）；GAP-033 关闭；`test_evolution_loop.py` 128 测试全绿 |
| **v2.14.0** | 2026-08-06 | GAP-030 测试隔离根治：EvolutionLoop 新增 `factor_db_path` 注入点，test_evolution_loop.py 全部 run() 集成测试注入临时 DuckDB，杜绝测试写入真实 factor_catalog（此前每次全量回归写入约 44 条重复 seed 记录，fut_option_pcr 累计 267 条）；一次性清理 catalog 重复 seed 记录（保留每 name 最早一条 + 快照引用保护） |
| **v2.13.0** | 2026-08-06 | GAP-032 L2 晋升产物双写一致性：`_write_to_duckdb` 返回 bool（失败不再吞异常）；`_promote_to_elite` 严格一致——DuckDB（主存储）写入失败回滚已写 JSON 快照并判定晋升失败，杜绝"快照有、catalog 无"孤儿；一次性数据修复：补入 1 个真缺失演化产物（fut_mobile_big_data_g5）+ 归档 515 个同名重复快照至 `_archive/`；新增双写原子化测试 |
| **v2.12.1** | 2026-08-06 | session_id 全链路补齐：`state.py` 新增 `generate_session_id()`（格式 `session_<8hex>_<timestamp>`，与 trace_id 同构）；`fts/cli.py` 入口 `main()` 生成 session_id 并挂载到 `args.session_id`，作用域为整个 CLI 会话；evolution/meta-loop/portfolio 子命令启动日志输出 session_id 作为日志聚合标识；02-lifecycle 章节 4 校正 trace_id/run_id 格式描述（`{prefix}_{8hex}_{timestamp}`）并补充 session_id 实现说明；新增 3 测试用例（CLI 挂载/输出 + state 格式） |
| **v2.12.0** | 2026-08-06 | GAP-031 L1→L2 数据流打通：EvolutionLoop 启动时合并 L1 注入候选（`_merge_l1_candidates`，pending 门控 + market 过滤 + 名称去重 + 幂等更新 factor_pool.json pending→injected）；L1 `_inject_candidate` 注入时写入 market 标记；`SeedCandidate` 契约新增可选 `market` 字段；新增 `test_evolution_l1_merge.py` 8 用例全绿；业务流文档同步 L1→L2 衔接 |
| **v2.11.0** | 2026-08-06 | L3 组合漂移治理：新增 DriftMonitor（成员重合率 Jaccard + 权重 L1 变化率，持久化 drift_history/YYYY-MM-DD.json）+ PortfolioManager combo_history 归档 + build_combo 粘性约束（默认启用：±30% 变动 / 新因子首日封顶 0.10）+ L2 影子池（新晋升因子 shadow_pool 观察 5 个交易日，种子因子直接进正式组合）；新增 20 测试用例，82 个 portfolio_loop 测试全绿 |
| **v2.10.1** | 2026-08-06 | 冷启动机制修正：L1/L2/L3 状态文件冷启动判定从 `EVOLUTION_VERSION`（系统版本）改为 `STATE_SCHEMA_VERSION`（状态结构版本），新增 `STATE_SCHEMA_VERSION` 常量；状态文件字段 `version` → `schema_version`（MetaStateManager / EvolutionStateManager / PortfolioStateManager 三处同步）；功能版本号变更不再触发冷启动，避免小版本升级清空演化进度；新增 `test_schema_version_compatible_keeps_state` 测试，78 个 meta_loop 测试全绿 |
| **v2.10.0** | 2026-08-06 | 算子演化引擎（Phase 3+ / C.4）：新增 `fts/factor_engine/operator_evolution.py`（`OperatorEvolutionEngine`，DSL 算子空间适应度导向进化搜索——种群初始化（validator 校验）/IC+Sharpe 适应度评估（DSL executor，带缓存）/锦标赛选择/子树交叉与变异（参数受 param_bounds 约束）/精英保留），取代 `_generate_operator_factor` 纯随机组合；evolution_loop `_try_operator_engine_evolution` 接入 operator/hybrid 模式（无评估数据或引擎失败回退随机生成）；产物为 `kind=OPERATOR` 因子；关闭 GAP-026；新增 13 测试用例（引擎 11 + 集成 2）；设计文档 C.4 落地 |
| **v2.9.0** | 2026-08-06 | Design 全量落地（docs/harness/design 9 设计全部完成）：S1 数据层（factor_quality_scores/factor_status_history/factor_audit_reports 三表 + 3 仓储类 + factor_catalog 生命周期字段）；S2 监控调度（Prometheus 衰减/Regime 指标 + adaptive_weight 封装 + 数据质量三维指标 + monthly_decay_eval/data_quality_eval 任务）；S3 回测流水线增强（7 阶段类 FactorScreener/SignalGenerator/PortfolioConstructor/CostSimulator/RiskAttributor/ReportGenerator/CapitalAllocator + run_batch + BacktestPipelineBuilder + CLI fts backtest）；S4 C.1 CLI（fts feature list/analyze + fts gp evolve）；S5 C.2 实盘对接（signal_contract/SignalValidator + fts/risk 风控包 + LiveFactorMonitor + HTTP 端点 + live/risk 指标）；S6 C.3 反馈闭环（FeedbackLoop 家族 + 4 张反馈表 + CLI fts feedback + 反馈指标）；新增 79 测试用例（S1 11 + S2 19 + S3 27 + S4 5 + S5 27 + S6 20 去重后 79）；全量回归通过（排除既有 3 个失败测试文件） |
| **v2.8.5** | 2026-08-06 | P0/P1 演化质量修复与 OPERATOR 演化模式基础层：OPT-001 快速预筛选层（Step 1.4，nunique>10 / abs(IC)>0.02 / std>1e-6，过滤常数信号和伪相关）；OPT-002 种子因子晋升修复（重复判断 + EliteFactorTracker 初始化）；OPT-003 精英因子重评估保护（跳过不存在的跟踪记录）；OPT-004 期货质量评分卡差异化配置（get_futures_config，IC/Sharpe/换手率阈值下调适应日频期货）；OPT-005 LLM Prompt 增强（添加质量约束、OOS 一致性、因果链要求）；OPT-007 多父代交叉策略（GP 演化 3-parent crossover，锦标赛选择 n 父代，30% 概率）；OPT-008 FTS-Expr DSL OPERATOR 演化模式集成（_generate_operator_factor 方法，基于算子注册表随机生成合法表达式，10 次尝试上限）；OPT-006 OOS 审计误判修复（ICIR 一致性计算替代 oos_ratio）；新增 38+ 测试用例；全量回归测试通过 |
| **v2.8.2** | 2026-08-06 | 回测流水线兼容修复：`_execute_factor_code` 支持标准 `factor_program(data, params)` 代码约定（此前仅支持 `output` 变量约定，导致所有 YAML 种子/GP 因子返回全零「未设置 output 变量」）；`_compute_factor` 滚动 IC 与日期构造在无 `date` 列时回退到 DatetimeIndex（修复期货面板 KeyError: 'date'）；新增 tests/factor_engine/test_backtest_pipeline.py（5 用例，覆盖标准约定/传统约定/显式 date 列/无效代码/缺列），流水线覆盖率 88%→90% |
| **v2.8.1** | 2026-08-06 | 孤立模块集成修正：按真实 API 修正 6 处集成调用点（AblationExperiment.run / CausalValidator.validate / RobustnessTester.run / ShapAnalyzer.analyze 均改为 `(factor, data, forward_returns)`；FeatureImportanceAnalyzer.analyze 改为 `(factor_series, data, target_col)`；LogicMonitor 改用 `run(factor, data, switch_dates)` 从 elite 快照加载因子程序）；4 个审查门禁 passed 判定落地（消融 IC 降幅超基线 50% 判伪相关、因果 n_anomalous>0 判事件敏感、鲁棒性总体通过率≥90%、SHAP 恒通过）；新增门禁判定测试与 `_mock_review_pass` 端到端 mock helper；109 测试全绿 |
| **v2.8.0** | 2026-08-06 | Phase 3 CLI 增强：FactorRepository 新增 5 个高级查询 API（get_by_family/get_eligible/get_diverse_factors/get_factor_lineage/get_family_distribution）；CLI `factor list` 支持 DuckDB 查询模式（`--family`/`--min-ic`/`--min-sharpe`/`--diverse`/`--total-count`/`--max-per-family`/`--limit`/`--json`），无参数时回退目录直读模式；新增 `factor stats` 子命令输出家族分布统计；新增 `factor lineage <id>` 子命令查询因子演化血缘；新增 24 个测试用例（11 仓库 API + 13 CLI 增强）；全量 85 相关测试绿 |
| **v2.7.0** | 2026-08-05 | 因子审计闭环：FailureClassifier 接入 FactorAuditor.audit() 主流程（审计失败自动输出改善建议，含 failure_analysis 字段 + 6 项可执行建议）；实现 FactorInspector 定时巡检任务（基于 FactorLineage.batch_audit() 自动检测退化因子并降级，支持 dry_run/降级/复活三模式）；新增 E2E 测试 test_factor_lifecycle.py（14 用例覆盖因子入库→血缘追踪→失败分类→改善建议完整闭环）；修复 FactorLineage.batch_audit SQL 查询（_execute 确保列元数据）；修复 _add_evaluations 时间序列数据方向（最旧→最新排列）；174 相关测试全绿 |
| **v2.6.0** | 2026-08-05 | Phase 3 因子血缘与审计闭环：实现因子数据血缘审计（FactorLineage），支持演化谱系查询/评估趋势分析/质量退化检测/批量血缘审计；实现因子失败模式分类器（FailureClassifier），支持 10 种失败模式自动识别（负 IC、IC 衰减、OOS 不稳定、跨品种失败、多重检验、数据窥探、压力脆弱、因果弱、Sharpe 偏低、高换手）+ 针对性改善建议生成；修复 FactorRepository.create_factor family NOT NULL 约束（默认 'other'）；新增 57 个测试用例（27 lineage + 30 failure_classifier）；核心因子库 140 测试全绿 |
| **v2.5.0** | 2026-08-05 | Phase 1 种子因子 YAML 化 + Phase 2 精英因子 DuckDB 迁移：种子因子从硬编码迁移到 YAML 文件（19 个文件，563 因子）；精英因子从 JSON 文件迁移到 DuckDB（680 因子，4 张表）；实现因子仓库层（FactorRepository）支持 CRUD/版本管理/相关性存储；生成因子相关性矩阵（4950 对相关性记录）；新增 54 个测试用例；155+ 测试全绿 |
| **v2.4.0** | 2026-08-05 | 默认市场改为期货：settings.yaml/settings.py/meta_loop.py/cli.py 四层同步 default_market="futures"；L1 期货知识注入新增全链路日志（初始化/种子池/Step1-4/验证/持久化/完成）；118 测试全绿 |
| **v2.2.0** | 2026-08-04 | Phase B 因子泛化优化 — 品种分层训练 + 精英因子全量重验证：新增 `FUTURES_SECTOR_MAP` 产业链分类映射（7 类覆盖所有期货品种）；新增 `FUTURES_STRATIFIED_SUBSET` 分层训练集（19 品种覆盖 7 大产业链）；L2 演化循环使用分层训练集排除盲测品种；新增 `scripts/futures_factor_revalidation.py` 精英因子全量重验证脚本（自动计算全量品种截面 IC、自动降级 CRITICAL 退化因子、输出验证报告到 reports/）；首次重验证自动降级 2 个退化因子（fut_basis_momentum_g1, fut_basis_momentum）；19 个差距全部关闭 |
| **v2.1.0** | 2026-08-04 | Phase A 因子泛化优化 — 盲测品种池 + 单品种 IC 追踪 + 品种级权重分配：新增 `FUTURES_HOLDOUT` 盲测品种池（6 品种覆盖各产业链），L2 演化训练排除盲测品种；新增 `_compute_holdout_validation()` 盲测验证报告（盲测 IC vs 训练 IC 对比 + 保持率警告）；新增 `_compute_per_variety_ic_matrix()` 品种-因子 IC 矩阵（18 因子 × 25+ 品种）；新增 `_compute_per_variety_weights()` 品种级权重分配（全局 Ridge 权重 × 品种 IC 自适应调整）；修改 `_compute_composite_scores()` 支持品种级权重参数；报告新增「品种-因子有效性矩阵 (IC)」章节含 3 个子表；控制台输出品种级 vs 全局排名一致性 Spearman ρ；17 个差距全部关闭 |
| **v2.0.0** | 2026-08-04 | Phase C 逻辑审查 — 因果结构审查 + 持续监控仪表盘：新增因果验证器（causal_validator.py）通过自然实验事件验证因子预测的因果意义（6 个预定义事件，3σ 异常检测，方向一致性校验）；新增逻辑监控仪表盘（logic_monitor.py）覆盖因子行为漂移检测（与动量/均值回归基准的相关性）、极端预测占比报警（>2σ 占比超 5%）、换月日信号异常检测（3σ 阈值）；新增 6 个自然实验事件定义（A 股 4 个 + 期货 2 个）；新增 ~40 个测试用例，1850+ 测试全绿；pyproject.toml 中 fts.__version__ 同步至 v2.0.0 |
| **v1.9.0** | 2026-08-03 | Phase A 演化优化 — UCT 父因子选择 + 失败模式聚类：父因子选择从轮询改为 UCT 树搜索，宏观演化引入失败模式聚类分析注入 LLM prompt，新增 32 个测试用例（test_uct_selection.py 10 + test_failure_pattern.py 22） |
| **v1.8.1** | 2026-08-03 | Market Regime 集成到信号管道：新增 `_build_composite_ohlcv()` 从品种面板构建市场综合 OHLCV，管道调用 `RegimeAwareSelector.detect()` 检测当前市场制度（5 种：bull/bear/high_vol/low_vol/oscillate），控制台输出制度名称+置信度+特征值，报告新增「市场制度」章节含 Regime 调整后的交易建议（趋势友好→放大仓位、震荡→反向操作、高波动→缩小仓位+增量绝对值>0.15）；版本号 1.8.0→1.8.1 |
| **v1.8.0** | 2026-08-03 | 信号管道 v5 多空双向 + 信号增量：管道升级为多空双向排名（按信号强度绝对值排序），新增信号增量追踪（较昨日变化判断趋势加速/衰竭），信号快照 JSON 持久化 + JSONL 历史追加，L3 Portfolio Loop 自动触发信号管道（全量 82 品种），README 拆分股票/期货种子因子；版本号 1.7.3→1.8.0 |
| **v1.7.3** | 2026-08-03 | 信号管道 Ridge 回归加权：`_compute_ridge_weights` 基于 L2 正则化学习因子权重（替代 IC>0.3 硬过滤+等权），弱因子保留不丢弃；新增 21 个测试用例（`tests/test_futures_signal_pipeline.py`）；调度任务修复（`futures_signal_pipeline` 默认注册）；基本面数据测试适配 FundamentalProvider API 变更 |
| **v1.7.2** | 2026-08-03 | 信号管道全量商品池：L3/定时任务改为 `--universe all`（82 品种 FUTURES_SUBSET 剔除金融期货，剔除停更/陈旧品种后 72 品种参与评分）；报告输出品种中文名称（FUTURES_SYMBOL_NAMES）、主力合约代码（get_dominant_contracts：contract_kline 最新交易日最大成交量 + AKShare futures_zh_realtime 持仓量 fallback）、盘中实时价（AKShare 分时最新 close，覆盖 72/72）；新增 8 测试用例（名称映射 + 主力合约判定 + AKShare fallback） |
| **v1.7.1** | 2026-08-03 | 期货全量信号修复：`get_futures_panel()` common_dates 改为多数对齐（≥ 品种数//2），修复 WH0/JR0/RI0/LR0 停更品种清空交集导致方向校正失效的问题；信号管道方向校正改为按日期定位（df.index.get_loc）；管道新增 `--universe all` 支持全量 76 商品期货（FUTURES_SUBSET 剔除金融期货）；信号管道剔除停更/陈旧品种（最新交易日早于共同日期末端）；Elite 因子 MA 计算修复（np.convolve mode='same' 尾部边界 bug → rolling mean）；新增 12 测试用例（test_data_futures_panel.py） |
| **v1.6.0** | 2026-08-03 | 期货自治循环：L1/L2/L3 全自动调度（APScheduler 定时任务）+ 期货基本面数据接入（库存/仓单/基差）+ 信号管道定时任务 + 5 个注册任务（L1:08:30 / L2:23:00 / L3:20:00 / 信号管道:20:30 / 健康检查:每10m）+ 期货全量种子因子库（12 大因子家族 50+ 子因子）+ 期货因子演化脚本 + 顶级因子过滤（IC>0.3）接入信号管道 + 信号报告输出到 reports/{date}/ |
| **v0.1.0** | 2026-07-18 | 从 FDT 剥离，完成 Phase 1-7，220 测试全绿 |
| **v1.5.1** | 2026-08-03 | 期货组合构建与信号生成：L3 PortfolioLoop 构建组合策略（组合夏普 5.43），新增 scripts/futures_signal_pipeline.py 期货横截面信号管道（66 期货 Elite 因子），生成 25 核心品种信号报告（含 Top 20 排名与因子贡献分析） |
| **v1.5.0** | 2026-08-03 | 期货数据接入：新增 FuturesDataProvider（DuckDB kline_cache + AKShare futures_zh_daily_sina），FTSDataProvider 集成 get_futures_ohlcv/get_futures_panel，CLI 扩展 --universe futures 支持期货横截面因子演化，新增 scripts/download_futures.py 断点续传下载脚本，82 个期货品种（25 核心 + 57 全量），3 级数据降级（DuckDB → AKShare → 合成） |
| **v1.4.0** | 2026-08-03 | 基本面/另类/宏观因子加入种子池（482 种子）；新增 FundamentalProvider 数据层 + 23 个基本面种子因子（估值/质量/成长/市值/换手率/宏观/另类复合）；seed_data 新增 fundamental_seeds.py；loader 支持基本面种子加载；1502 测试全绿，99% 覆盖率 |
| **v1.3.2** | 2026-08-03 | 代码审核提升：消除 `_evaluate_and_promote_seeds` 重复横截面逻辑，提取 3 个公共 Mock fixture（`mock_trial`/`mock_optuna_study`/`mock_evolve_micro`）；1432 测试全绿，99% 覆盖率，47/47 模块 100% 覆盖率 |
| **v1.3.1** | 2026-08-03 | 代码审核提升：重构 `parse_program_md` 为数据驱动解析（76→48 行），提取 `_evaluate_cross_section` 方法（178→155 行），拆分 Eager Test；1432 测试全绿，99% 覆盖率，46/47 模块 100% 覆盖率 |
| **v1.3.0** | 2026-08-03 | 国泰君安 191 因子加入种子池（459 种子）；seed_data 新增 gtja191.py；loader 支持 gtja191 批量加载；工程测试全覆盖：1431 测试全绿，46/47 模块 100% 覆盖率，仅余 1 空白行未覆盖 |
| **v1.2.0** | 2026-08-02 | 种子因子集成：世坤 101 因子 + Qlib 158 因子加入种子池（268 种子）；seed_data 目录统一管理；熔断修复（种子评估不计入计数器）；纯多头回测策略；1325 测试全绿，99% 覆盖率 |
| **v1.1.0** | 2026-07-24 | MCP 数据源迁移：Data-Core → akshare(腾讯/东方财富)；移除 6 个期货专用种子因子；CLI 移除 `--universe futures`；默认市场改为 stock；1231 测试全绿 |
| **v1.0.0** | 2026-07-19 | 本地原生部署：进程守护/热重载/HTTP 监控/Windows 服务/CI/CD/E2E 测试/部署文档、1231 测试全绿 |
| **v0.4.0** | 2026-07-19 | EliteFactorTracker、AutoRetireManager、WalkForwardOptimizer、EvaluationChain 走航集成、1104 测试全绿 |
| **v0.3.0** | 2026-07-19 | Data-Core 集成适配、FDT 残留清除、覆盖率提升至 96%、969 测试全绿、原子持久化 |
| **v0.2.0** | 2026-07-18 | CLI 引擎真实调用、Config+memory 目录、Scheduler 引擎、覆盖率提升至 89%、778 测试全绿 |

### 版本号位置

FTS 项目版本号定义在两个位置，变更时必须同步更新：

| 文件 | 字段 |
|:-----|:-----|
| `fts/__init__.py` | `__version__ = "2.30.0"`（从 pyproject.toml 动态读取） |
| `pyproject.toml` | `version = "2.30.0"` |

异常引擎内部版本号位于 `fts/factor_engine/__init__.py` 的 `EVOLUTION_VERSION`（当前 v1.1.0），与 FTS 项目版本同步。

---

## 2. 安装方式

### 基础安装

```bash
# 从项目根目录安装
pip install .

# 带可选依赖
pip install .[evolution]    # 带 optuna 演化支持
pip install .[llm]          # 带 LLM 支持
pip install .[mcp]         # 带 MCP 数据支持（akshare 腾讯/东方财富）
pip install .[dev]          # 带开发工具（pytest）
pip install .[evolution,llm,mcp,dev]  # 全部
```

### 核心依赖

| 依赖 | 版本要求 | 用途 |
|:-----|:---------|:-----|
| numpy | >=1.24 | 数值计算 |
| pandas | >=2.0 | 数据处理 |
| pyyaml | >=6.0 | YAML 配置读取 |

### 可选依赖

| extra | 依赖 | 用途 |
|:------|:-----|:-----|
| evolution | optuna>=3.0 | 贝叶斯调参 |
| llm | openai>=1.0, anthropic>=0.20 | LLM 因子演化 |
| data | datacore | Data-Core 数据接入 |
| dev | pytest>=7.4, pytest-cov>=4.1 | 测试工具 |

---

## 3. CLI 入口

### 统一入口

```bash
python -m fts.cli <command> [options]
```

或通过注册的脚本命令：

```bash
fts <command> [options]
```

### 子命令列表

| 子命令 | 选项 | 说明 |
|:-------|:-----|:-----|
| `version` | — | 打印版本号 |
| `monitor` | `--json` | 检查所有循环健康状态 |
| `evolution run` | `--max-generations N`, `--universe {single,csi300,futures}`, `--max-stocks N` | 启动 L2 因子演化主循环（支持单标/沪深300/期货横截面） |
| `meta-loop run` | — | 启动 L1 Meta-Loop |
| `portfolio run` | — | 启动 L3 组合构建 |
| `scheduler run` | — | 启动调度器后台运行（APScheduler） |
| `scheduler list` | — | 列出所有已注册定时任务 |
| `factor list` | `--elite-dir PATH` | 列出 elite 因子 |
| `factor show <factor_id>` | `--elite-dir PATH` | 查看单个因子详情 |

### 使用示例

```bash
# 查看版本
python -m fts.cli version

# 健康检查
python -m fts.cli monitor
python -m fts.cli monitor --json    # JSON 格式输出

# 因子演化
python -m fts.cli evolution run --max-generations 20

# 因子管理
python -m fts.cli factor list
python -m fts.cli factor show factor_abc123
```

---

## 4. 状态检查

### 健康监控命令

```bash
python -m fts.cli monitor
```

输出示例：

```
=== FTS System Status ===
Overall healthy : YES
Checked at      : 2026-07-18T10:30:00
FTS version     : 1.1.0
Circuit broken  : NO
Stale (>24h)    : NO
Tokens today    : 0

=== Loop Status ===
[OK]   L1  | status=running          | run_id=run_1658136000_a1b2c3     | age=0.0h
[OK]   L2  | status=completed        | run_id=run_1658136000_d4e5f6     | age=0.0h
[OK]   L3  | status=completed        | run_id=run_1658136000_g7h8i9     | age=0.0h
```

### 监控指标

| 指标 | 说明 | 告警阈值 |
|:-----|:-----|:---------|
| healthy | 整体健康状态 | False = 告警 |
| circuit_broken | 熔断状态 | True = 紧急 |
| stale | 超过 24h 未更新 | True = 告警 |
| age_hours | 距上次运行小时数 | >24h = stale |
| tokens_consumed | Token 消耗 | 按 budget 阈值 |
| status | 运行/暂停/完成/熔断 | circuit_broken = 紧急 |

### 状态文件位置

各循环的状态持久化到 `memory/` 目录：

| 循环 | 状态文件 |
|:-----|:---------|
| L1 Meta-Loop | `memory/meta_loop/state.json` |
| L2 Evolution Loop | `memory/evolution/state.json` |
| L3 Portfolio Loop | `memory/portfolio/state.json` |

---

## 5. 定时任务调度器

### 启动方式

```bash
# 后台运行（APScheduler）
python -m fts.cli scheduler run

# 列出所有已注册任务
python -m fts.cli scheduler list
```

### 注册任务清单

| 任务名 | cron 表达式 | 时间 | 说明 |
|:-------|:------------|:-----|:-----|
| `l1_meta_loop` | `30 8 * * *` | 每日 08:30 | L1 Meta-Loop：知识补给 + Bootstrapping + 种子注入 |
| `l2_evolution_loop` | `0 23 * * *` | 每日 23:00 | L2 Evolution Loop：夜间因子演化（LLM + optuna + 横截面） |
| `l3_portfolio_loop` | `0 20 * * *` | 每日 20:00 | L3 Portfolio Loop（期货路径：futures_elite + market=futures）：因子筛选(ACTIVE_FACTOR_CAP=20) + 信号合成(默认elastic_net) + Verifier 校验 |
| `futures_signal_pipeline` | `30 20 * * *` | 每日 20:30 | 期货信号管道：独立生成横截面信号报告 |
| `health_check` | `*/10 * * * *` | 每 10 分钟 | 健康检查：监控所有循环状态 |

### 依赖

调度器依赖 APScheduler：

```bash
pip install apscheduler
```

### 降级策略

如果 APScheduler 未安装，`SchedulerEngine.start()` 静默返回 False，所有任务不执行，系统正常运行。

---

## 6. 版本升级流程

### 常规升级步骤

1. **更新版本号**
   - 修改 `fts/__init__.py` 中的 `__version__`
   - 修改 `pyproject.toml` 中的 `version`

2. **更新文档**
   - 在本文件（`07-operations.md`）版本历史中添加新版本记录
   - 如有架构变更，更新 `01-architecture.md`
   - 如有测试变更，更新 `06-testing.md`
   - 如有差距关闭，更新 `08-gap-analysis.md`

3. **同步 README.md**
   - 更新版本徽章
   - 更新测试数和覆盖率
   - 同步 API 使用示例、模块列表、文档链接
   - 确认 13 项 commit 检查清单第 12 项（README 同步）通过

4. **运行测试**
   ```bash
   python -m pytest tests/ --cov=fts --cov-report=term-missing
   ```
   确认全部通过

5. **提交并打标签**
   ```bash
   git tag v0.2.0
   ```

### 版本号变更规则

| 变更类型 | 示例 | 条件 |
|:---------|:-----|:-----|
| MAJOR | v1.0.0 | 重大架构变更 |
| MINOR | v0.2.0 | 功能新增 / 阶段完成 |
| PATCH | v0.1.1 | bug 修复 / 文档更新 |

### 版本号统一管理规范（v2.22.0+）

FTS 从 v2.22.0 起使用 **单一真实源（Single Source of Truth）** 管理版本号：

1. **唯一源头**：`pyproject.toml` 中的 `version` 字段
2. **代码动态读取**：`fts/__init__.py` 通过 `tomllib`/`tomli` 从 `pyproject.toml` 读取，避免手动同步
3. **文档自动同步**：`scripts/update_doc_versions.py` 自动扫描并更新所有 Harness 文档的版本头
4. **CI 检查**：`scripts/verify_doc_consistency.py` 内置版本号一致性检查，commit 前自动执行

**操作流程**：
```bash
# 1. 修改版本号（仅需修改 pyproject.toml）
# 2. 同步文档版本号
python scripts/update_doc_versions.py --apply
# 3. 验证一致性
python scripts/verify_doc_consistency.py
python scripts/verify_doc_consistency.py --fix-versions  # 自动修复版本号不一致
```

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `pyproject.toml` version = "2.22.0"（单一真实源）；`fts/__init__.py` 动态读取；`fts/factor_engine/contracts.py` EVOLUTION_VERSION 动态读取；`scripts/update_doc_versions.py` 自动同步文档版本 |
| 可验证断言 | 版本号 v2.59.0 在 pyproject.toml 中定义，fts/__init__.py 动态读取，所有 Harness 文档版本头一致；v2.54.0 精英因子全员质量巡检 230 因子 229 合格 1 出库；v2.55.0 VPER 因子 institutional 评分修正（1→4）后重新质检通过 V5 归库；v2.57.0 股票因子行业/市值中性化（CrossSymbolOps.industry_cap_neutral + cross_section_evaluate_backtest industry_map/cap_map + EvolutionLoop 注入 + FTSConfig.stock_neutralization）；v2.58.0 期货换月复权与展期仿真（GAP-046：RollCalendar + kline_cache.adj_factor + 展期成本 + contract_kline 建表/写入）；v2.59.0 期货截面中性化 + 回测真实性仿真（GAP-F03：EvolutionLoop futures 自动注入板块映射；GAP-F02：涨跌停拦截 + 停牌过滤 + 被拦截成交统计）；v2.60.0 第二批（GAP-F04 数据源降级加固 + GAP-F05 轻量纯 numpy MLP 因子 + GAP-F06 数据级监控器 + scheduler 接入 + GAP-F07 组合优化器 + synthesize_signals optimizer 模式）；v2.60.0 第二批（GAP-F04 数据源降级加固 + GAP-F05 轻量纯 numpy MLP 因子 + GAP-F06 数据级监控器 + scheduler 接入 + GAP-F07 组合优化器 + synthesize_signals optimizer 模式） |
| 检验方式 | `python -c "from fts import __version__; assert __version__ == '2.22.0'"`；`python scripts/update_doc_versions.py --check`；`python scripts/verify_doc_consistency.py` |
