# FTS 配置管理

> 版本: v3.0.0+6
> 最后更新: 2026-08-10

---

## 1. 配置层次

FTS 配置采用三级优先级（高→低）：

```
高优先级         环境变量 (FTS_* 前缀)
    ↑           YAML 配置文件 (config/settings.yaml)
    ↑           代码默认值 (FTSConfig dataclass)
低优先级
```

## 2. 配置项清单

| 配置项 | 类型 | 默认值 | 环境变量 | 说明 |
|:-------|:-----|:-------|:---------|:-----|
| `memory_dir` | str | `"memory"` | `FTS_MEMORY_DIR` | 运行时状态持久化目录 |
| 存储域契约路径（plans/29 P0） | str（注册表内部） | `docs/harness/_data/storage_landscape.yaml` | `FTS_STORAGE_LANDSCAPE_PATH` | 存储域注册表 YAML 路径（StorageRegistry 加载，13 域；缺省回落内置默认路径，缺失时注册表空不抛错）（GAP-090，v2.101.0） |
| 写路径严格模式（GAP-150） | bool | `"1"`（严格） | `FTS_STORAGE_WRITE_STRICT` | FactorRepository 默认路径写入口契约：未登记 storage_landscape 抛 ValueError 阻断（强制先登记）；置 `"0"` 回退告警模式（v2.105.0+20） |
| `elite_dir` | str | `"memory/knowledge/factors/futures_elite"` | `FTS_ELITE_DIR` | elite 因子存储目录（股票剥离后默认对齐期货精英目录，v2.86.0） |
| `default_market` | str | `"futures"` | `FTS_DEFAULT_MARKET` | 默认市场类型；v2.104.0+101 起为**全局市场开关**——调度任务门控（futures/energy 专属任务仅全局市场匹配时执行）、CLI `--market`/`--universe` 未指定时默认值、`FactorRepository`/`FactorInspector` 构造 `market=None` 时路由均跟随；v2.104.0+103 曾临时默认 energy 能化链，**v3.0.0+1 反转回 futures**（plans/57 双系统切分后 FTS 因子生产默认面向全部期货 84 品种/17 产业链） |
| `llm_backend` | str | `""` | `FTS_LLM_BACKEND` | LLM 后端选择（空=自动检测）|
| `evolution_mode` | str | `"operator_first"` | `FTS_EVOLUTION_MODE` | 演化模式: operator(算子主干) / operator_first(算子优先,LLM/GP兜底, v2.105.0+2 默认) / code(代码创新) / hybrid(混合) / batch(批量挖掘漏斗, GAP-I201, v2.65.0) |
| 演化 CLI 失败率熔断阈值 | float | 1.0 | `FTS_EVOLUTION_CB_FAILURE_RATE` | `fts.cli evolution run` 失败率熔断阈值（v2.103.0+28 默认 0.99→1.0=禁用失败率熔断，夜间演化默认跑满世代数；保留 token 与连续低 IC 熔断兜底；设 <1.0 可恢复失败率熔断） |
| `max_generations` | int | 10 | — | L2 最大演化代数 |
| `population_size` | int | 20 | — | 种群大小 |
| `micro_trials_per_generation` | int | 50 | — | 每代 optuna 试验数 |
| `micro_staged_evolution` | bool | true | `FTS_MICRO_STAGED` | 微观演化两阶段漏斗开关：粗筛快速淘汰低潜力 + 精筛自适应 trials（GAP-I205，v2.70.0） |
| `micro_coarse_trials` | int | 20 | `FTS_MICRO_COARSE_TRIALS` | 粗筛阶段 optuna 试验数（GAP-I205，v2.70.0） |
| `micro_coarse_ic_floor` | float | 0.02 | `FTS_MICRO_COARSE_IC_FLOOR` | 粗筛淘汰阈值：粗筛得分低于该值直接淘汰，不进入精筛（GAP-I205，v2.70.0） |
| `l2_elite_corr_threshold` | float | 0.9 | `FTS_L2_ELITE_CORR_THRESHOLD` | L2 准入去冗余相关性阈值：演化因子晋升前与既有 elite 信号相关绝对值 ≥ 该值拒绝晋升（GAP-I206，v2.71.0） |
| `l2_elite_corr_max_scan` | int | 50 | `FTS_L2_ELITE_CORR_MAX_SCAN` | L2 准入去冗余扫描容量护栏：最多扫描的既有 elite 因子数（GAP-I206，v2.71.0） |
| `l2_elite_corr_debug` | bool | false | `FTS_L2_ELITE_CORR_DEBUG` | L2 准入去冗余调试日志开关（放行时输出 debug 日志）（GAP-I206，v2.71.0） |
| `l2_elite_orthogonalize` | bool | true | `FTS_L2_ELITE_ORTHOGONALIZE` | L2 正交化闭环开关：高相关因子 OLS 残差质量合格则以正交化版本入库，不合格拒绝兜底（GAP-I206 补充，v2.71.0） |
| `l2_orthogonal_residual_corr_max` | float | 0.3 | `FTS_L2_ORTHOGONAL_RESIDUAL_CORR_MAX` | 正交化残差与参照 elite 信号的最大相关性，低于该值视为已正交（GAP-I206 补充，v2.71.0） |
| `l2_orthogonal_min_retained_ratio` | float | 0.3 | `FTS_L2_ORTHOGONAL_MIN_RETAINED_RATIO` | 正交化残差最小保留比（残差 std / 原信号 std），低于该值视为独立信息不足拒绝（GAP-I206 补充，v2.71.0） |
| `l2_orthogonal_basis_enabled` | bool | true | `FTS_L2_ORTHOGONAL_BASIS_ENABLED` | 多因子正交基底开关：L2 准入优先对 Gram-Schmidt 基底迭代残差化（GAP-I206 补充，v2.72.1） |
| `l2_orthogonal_basis_max_size` | int | 10 | `FTS_L2_ORTHOGONAL_BASIS_MAX_SIZE` | 正交基底最大成员数（超出时按 Sharpe 降序淘汰最弱成员）（GAP-I206 补充，v2.72.1） |
| `l2_orthogonal_basis_min_sharpe` | float | 1.0 | `FTS_L2_ORTHOGONAL_BASIS_MIN_SHARPE` | 基底成员最小 Sharpe（低于该值不再入选基底）（GAP-I206 补充，v2.72.1） |
| `structure_cluster_quota_enabled` | bool | true | `FTS_CLUSTER_QUOTA_ENABLED` | 结构性聚类配额开关：以信号相关性聚类配额控制 elite 多样性（v2.104.0+25 起 max_per_family 家族配额已彻底删除，仅保留聚类配额路径）（GAP-077，v2.102.0） |
| `structure_cluster_max` | int | 15 | `FTS_CLUSTER_MAX` | 每结构簇最大因子数：与既有 elite \|corr\| ≥ corr_threshold 的同类成员数达上限拒绝晋升（GAP-077，v2.102.0） |
| `structure_cluster_corr_threshold` | float | 0.85 | `FTS_CLUSTER_CORR_THRESHOLD` | 结构簇"同类"判定相关性阈值（略宽于 GAP-I206 的 0.9：0.9 强拦截 vs 0.85 数量配额）（GAP-077，v2.102.0） |
| `shap_n_extreme` | int | 25 | `FTS_SHAP_N_EXTREME` | SHAP 极端样本数（top+bottom 各 N）：KernelExplainer 每因子评估量 ≈ n_extreme×2×nsamples；GAP-080 降频 50→25（v2.102.0） |
| `shap_n_background` | int | 50 | `FTS_SHAP_N_BACKGROUND` | SHAP KernelExplainer 背景样本数：GAP-080 降频 100→50（v2.102.0） |
| `shap_nsamples` | int | 50 | `FTS_SHAP_NSAMPLES` | SHAP 每极端样本 KernelExplainer 扰动次数（nsamples）：GAP-080 降频 100→50，与 n_extreme 合并 ~4x 评估量下降（v2.102.0） |
| `evolution_success_pattern_enabled` | bool | true | `FTS_SUCCESS_PATTERN_ENABLED` | 成功模式定向演化开关：注入近期成功模式（方法/算子/窗口维度，排除 style_tags）到 MacroEvolver prompt 作 soft 偏向（Phase 1.2 P0-1，26 号计划 §6） |
| `success_pattern_window_days` | int | 14 | `FTS_SUCCESS_PATTERN_WINDOW` | 成功模式滚动窗口（天）：窗口外模式不参与统计（防过拟合） |
| `success_pattern_min_sample` | int | 10 | `FTS_SUCCESS_PATTERN_MIN_SAMPLE` | 成功模式样本下限：窗口内成功轨迹 < 该值 → 空报告（不注入） |
| `evolution_stop_enabled` | bool | false | `FTS_EVOLUTION_STOP_ENABLED` | 提前达标停止开关（保守默认关闭）：连续 K 代零晋升 → 提前结束 run 正常收尾（Phase 3 P1-3，26 号计划 §8） |
| `evolution_stop_consecutive_empty_generations` | int | 5 | `FTS_EVOLUTION_STOP_EMPTY_GENS` | 连续零晋升代数阈值 K：达到即提前结束（验证：修复后真实 run 连续 15 代零晋升，见 plans/26 §8.7.1） |
| `decay_observe_slope` | float | 0.10 | `FTS_DECAY_OBSERVE_SLOPE` | 衰减分级观察斜率阈值：滚动 6M IC 斜率 \|slope\| ≥ 该值进入观察（GAP-I305，v2.72.1） |
| `decay_retire_slope` | float | 0.20 | `FTS_DECAY_RETIRE_SLOPE` | 衰减分级退役斜率阈值：\|slope\| ≥ 该值触发退役（GAP-I305，v2.72.1） |
| `decay_slope_min_points` | int | 6 | `FTS_DECAY_SLOPE_MIN_POINTS` | 衰减分级最小 IC 序列长度（不足视为 normal）（GAP-I305，v2.72.1） |
| `decay_auto_retire_enabled` | bool | true | `FTS_DECAY_AUTO_RETIRE_ENABLED` | 自动退役开关（关闭时仅打日志不实际退役）（GAP-I305，v2.72.1） |
| `max_workers` | int | 4 | `FTS_MAX_WORKERS` | 并行工作数 |
| `batch_size` | int | 20 | `FTS_BATCH_SIZE` | 批量挖掘每代候选生成数（GAP-I201，v2.65.0） |
| `batch_max_candidates` | int | 5 | `FTS_BATCH_MAX_CANDIDATES` | 通过粗筛后进入细评估的最大候选数（预算护栏，GAP-I201，v2.65.0） |
| `batch_max_workers` | int | 4 | `FTS_BATCH_MAX_WORKERS` | 批量粗筛并行线程数（GAP-I201，v2.65.0） |
| `batch_random_seed` | int | 42 | `FTS_BATCH_RANDOM_SEED` | 批量生成随机种子（同父多后代可复现，GAP-I201，v2.65.0） |
| `l1_announcement_extractor_enabled` | bool | true | `FTS_L1_ANNOUNCEMENT_EXTRACTOR_ENABLED` | 另类知识源：公告/舆情提取器开关（原股票管道，GAP-I103，v2.82.0；已随股票管线剥离至 fts-stock（2026-08），配置项保留兼容、主系统不再使用） |
| `l1_macro_extractor_enabled` | bool | true | `FTS_L1_MACRO_EXTRACTOR_ENABLED` | 另类知识源：宏观事件提取器开关（期货管道，GAP-I103，v2.82.0；仍由 L1 Meta-Loop 使用） |
| `l1_extractor_max_factors` | int | 20 | `FTS_L1_EXTRACTOR_MAX_FACTORS` | L1 提取器单次 LLM 最大因子数（plans/41 A3，v2.104.0+71）：管道构造时注入研报/论文/宏观/WebSearch 等 LLM 提取源；天软 tinysoft 为静态 YAML 感知源不参与 |
| `l1_bulk_enabled` | bool | true | `FTS_L1_BULK_ENABLED` | plans/44 P0 批量采集层开关（arXiv/OpenAlex/东财/全球报告/日韩法研报，false 跳过采集层） |
| `l1_source_arxiv_max_results` | int | 50 | `FTS_L1_SOURCE_ARXIV_MAX_RESULTS` | plans/44 arXiv 每类别拉取数（3→50，全球论文扩容） |
| `l1_source_report_page_size` | int | 100 | `FTS_L1_SOURCE_REPORT_PAGE_SIZE` | plans/44 东财研报分页大小（5→100） |
| `l1_embedding_enabled` | bool | true | `FTS_L1_EMBEDDING_ENABLED` | plans/44 embedding 粗筛/语义去重开关（本地多语种模型，缺失降级关键词） |
| `l1_embedding_threshold` | float | 0.30 | `FTS_L1_EMBEDDING_THRESHOLD` | plans/44 相关性粗筛阈值 |
| `l1_dedup_threshold` | float | 0.90 | `FTS_L1_DEDUP_THRESHOLD` | plans/44 语义去重阈值 |
| `l1_knowledge_deepread_max` | int | 60 | `FTS_L1_KNOWLEDGE_DEEPREAD_MAX` | plans/44 深读子集上限（篇/天，token 预算约束） |
| `l1_rejected_retry` | bool | true | `FTS_L1_REJECTED_RETRY` | plans/44 C2 拒绝候选复活开关（规则/LLM 修复后重新验证注入） |
| `l1_dynamic_websearch` | bool | true | `FTS_L1_DYNAMIC_WEBSEARCH` | plans/44 A1 WebSearch 动态 query 开关（知识缺口 + 当日异动） |
| `l1_semantic_dedup` | bool | true | `FTS_L1_SEMANTIC_DEDUP` | plans/44 C4 bootstrap 候选 vs 已注入语义高相似拦截开关 |
| `l1_openalex_languages` | list[str] | 8 语种 | `FTS_L1_OPENALEX_LANGUAGES` | plans/44 OpenAlex 多语种分路语种清单（en/zh/ja/de/fr/ko/es/ru，ISO 639-1） |
| `l1_non_en_reports_enabled` | bool | true | `FTS_L1_NON_EN_REPORTS_ENABLED` | plans/44 非中英语种研报源开关（IEEJ/KEEI/IFPEN 日韩法） |
| `l1_l2_backlog_days` | int | 7 | `FTS_L1_L2_BACKLOG_DAYS` | plans/44 D2 L1→L2 积压 warning 阈值（天） |
| `review_experience_chain`（环境变量直读） | bool | true | `FTS_REVIEW_EXPERIENCE_CHAIN` | 人审驳回意见是否写入经验链（GAP-I102 二期，v2.82.0） |
| `review_mode`（环境变量直读） | str | `"auto"` | `FTS_REVIEW_MODE` | 审查模式：`auto`=机审优先（正常自动批准/低质自动驳回/异常值转人审）/ `manual`=纯人审（C8-2，2026-08-11；manual 下 auto_review 需 --force 显式覆盖） |
| `review_auto_min_ic`（环境变量直读） | float | 0.02 | `FTS_REVIEW_MIN_IC` | 机审 IC 下限：低于视为低质自动驳回（C8-2） |
| `review_auto_max_ic`（环境变量直读） | float | 0.8 | `FTS_REVIEW_MAX_IC` | 机审 IC 上限：高于疑过拟合/未来函数转人审（C8-2） |
| `review_auto_min_sharpe`（环境变量直读） | float | 0.5 | `FTS_REVIEW_MIN_SHARPE` | 机审 Sharpe 下限：低于视为低质自动驳回（C8-2） |
| `review_auto_max_sharpe`（环境变量直读） | float | 30.0 | `FTS_REVIEW_MAX_SHARPE` | 机审 Sharpe 上限：高于疑过拟合/未来函数转人审（C8-2） |
| `executor_backend` | str | `"thread"` | `FTS_EXECUTOR_BACKEND` | 批量粗筛执行器后端：`thread`/`process`/`dask`/`ray`，可插拔分布式扩展预留（GAP-I502，v2.83.0；默认 thread 保持现状，dask/ray 缺依赖自动降级 process） |
| `executor_max_workers` | int | 4 | `FTS_EXECUTOR_MAX_WORKERS` | 执行器后端并行工作数（GAP-I502，v2.83.0） |
| `tick_cache_retention_days` | int | 7 | —（FuturesDataAggregator 构造参数） | tick_cache 保留天数：超过该时长的过期 tick 写入时自动清理（GAP-I503 首期，v2.84.0） |
| `l3_turnover_penalty` | float | 0.0 | `FTS_L3_TURNOVER_PENALTY` | 组合目标函数换手惩罚系数 λ：粘性约束后按 1/(1+λ) 收缩权重变动（0=关闭，λ 越大换手越低，GAP-I303，v2.85.0） |
| l3_weight_recompute_cadence | str | "daily" | FTS_L3_WEIGHT_RECOMPUTE_CADENCE | L3 组合权重重算频率：daily=每日重算 / weekly=仅 l3_weight_recompute_weekday 重算（GAP-072，v2.99.0；v2.104.0+7 默认改 daily；v2.105.0 起仅作用于 PortfolioLoop L3 侧，信号管道不再消费——信号管道因子选择与基础权重直接读 L3 组合 factor_weights.json，不再自训权重） |
| l3_weight_recompute_weekday | int | 4 | FTS_L3_WEIGHT_RECOMPUTE_WEEKDAY | 周度重算日（Python weekday 0=周一...4=周五，默认周五收盘后重算；GAP-072，v2.99.0） |
| `l3_turnover_budget_enabled` | bool | `false` | `FTS_L3_TURNOVER_BUDGET_ENABLED` | G3 换手预算分配开关（v2.103.0+17）：`true`=启用（单日换手 > daily_turnover_cap=0.30 时按边际收益剔除弱信号回退当前持仓）；`false`=关闭（默认，不剔除；换手控制由粘性约束 + 换手惩罚 λ 双通道兜底）。期货周频场景关闭可避免 sharpe 被 SHARPE_CAP 截断后评分并列导致的误剔最强因子（2026-08-13 实测 fut_bias_g18=0.9859 被误剔归零） |
| `l3_g1_enabled` | bool | `true` | `FTS_L3_G1_ENABLED` | G1 同向敞口惩罚开关（v2.104.0+X 配置化，35-gap-closure-plan G1）：`false`=关闭（scale 恒 1.0，不压缩）；默认开启。开启时与置信度仓位缩放（28-T6）在 build_combo 乘性合并：`exposure_final = exposure_scale × aligned_scale` |
| `l3_g1_align_threshold` | float | `0.60` | `FTS_L3_G1_ALIGN_THRESHOLD` | G1 同向敞口触发阈值（v2.104.0+X 配置化）：因子 IC 同向权重占比（max(看多,看空)）≥ 该值触发压缩；取值域 (0,1]，默认 0.60 = 历史硬编码。放宽（如 0.80）属风控决策，需评审后调整 |
| `l3_g1_max_compress` | float | `0.50` | `FTS_L3_G1_MAX_COMPRESS` | G1 最大压缩系数（v2.104.0+X 配置化）：同向占比=1 时压缩至该下限；取值域 (0,1]，默认 0.50 = 历史硬编码（全多组合敞口压至 50%）。放宽（如 0.70）直接放大风险敞口 |
| `l3_g1_compress_curve` | str | `"linear"` | `FTS_L3_G1_COMPRESS_CURVE` | G1 压缩曲线（v2.104.0+X 配置化）：`linear`（线性）/ `sqrt`（更温和）/ `exp`（更激进）；默认 linear = 历史硬编码 |
| `l3.chain_dedup.enabled` | bool | `true` | —（settings.yaml l3 段） | 子链维度去冗余开关（GAP-121 扩展，能源链专属）：`true`=同一子链保留因子数 ≤ max_per_chain；`false`=关闭。仅 market=energy 生效。**v2.105.0+13 升级**：由"symbol_ic 数量截断"改为"链内相关性聚类去冗余"（链内多品种平均相关 → 层次聚类留代表 → 叠加数量上限） |
| `l3.chain_dedup.max_per_chain` | int | `2` | —（settings.yaml l3 段） | 子链去冗余单子链保留因子数上限：链内聚类后仍超限按综合评分截断（同子链因子即使信号相关性低仍共享产业链驱动）；无画像/unknown 因子归通用池直接保留 |
| `l3.chain_dedup.corr_threshold` | float | `0.5` | —（settings.yaml l3 段） | 链内聚类距离阈值（1-\|corr\|）：**v2.105.0+13 新增**，收紧于全局 P1 的 0.7（子链内因子本就较少，更高相关性才归同簇、保留更多因子） |
| `l3.chain_dedup.cluster_top_n` | int | `1` | —（settings.yaml l3 段） | 链内每簇保留代表数上限：**v2.105.0+13 新增**，默认 1=每簇仅取综合评分最优 |
| `l3.subchain_weight.enabled` | bool | `true` | —（settings.yaml l3 段） | 子链差异化权重调制开关（plans/47 §B，v2.104.0+109）：`true`=特异因子在无效子链降权/归零（灰度，CLI `--enable-subchain-weight` 显式传参优先）；`false`=全链统一权重（兼容现状）。仅 market=energy 生效。**v2.105.0 起默认启用（decay_mode=zero）**——未投产环境开启验证，前置已回填 196 因子画像 |
| `l3.subchain_weight.decay_mode` | str | `"zero"` | —（settings.yaml l3 段） | 非 effective 子链权重模式：`zero`=归零 / `soft`=按 \|mean_ic\|/max_chain_ic 相对缩放 |
| `l3.subchain_weight.soft_min_ratio` | float | `0.0` | —（settings.yaml l3 段） | soft 模式最低保留比例（0.0=可归零，1.0=等效全链） |
| `l3.subchain_weight.scope_default` | str | `"all"` | —（settings.yaml l3 段） | 无 subchain_scope 画像因子的默认处理（`all`=全链保留，防误杀） |
| `l3.subchain_weight.max_exposure_ratio` | float | `0.5` | —（settings.yaml l3 段） | 单子链权重暴露占比告警阈值（plans/47 §D2：Step 2b 超阈值 warning + 质量报告 subchain_exposure 段） |
| `l3.subchain_profile.min_symbols` | int | `3` | —（settings.yaml l3 段） | 子链显著性护栏门槛①：子链内最小品种数（NaN 剔除后，不足直接 effective=False） |
| `l3.subchain_profile.min_t_stat` | float | `2.0` | —（settings.yaml l3 段） | 子链显著性护栏门槛②：单样本 t 检验 \|t\| 门槛（df=n−1=2；默认 0.184 双侧 p，刻意宽松滤噪声） |
| `l3.subchain_profile.min_chain_ic` | float | `0.10` | —（settings.yaml l3 段） | 子链显著性护栏门槛③：\|mean_ic\| 绝对值门槛（防"显著但微弱"） |
| `l3.regime_gating.enabled` | bool | `true` | —（settings.yaml l3 段） | 子链方向 Gate 开关（plans/48 §A，v2.104.0+111）：`true`=信号管线 Step 3h1 按子链 regime 做方向 Gate（avoid 剔除/降权 + long/short 方向过滤 + 暴露缩放），同时 **L3 权重层 Step 2.5 将 Gate 决策并入子链调制矩阵**（plans/50 §B1：m'[factor][子链] = m × gate_scale，avoid 链权重源头归零/降权——需 `enable_subchain_weight` 调制矩阵存在，否则保持观测语义）；`false`=全局软票 + 方向偏置（兼容现状）。仅 market=energy 生效；CLI `--enable-regime-gating` 显式传参优先。**v2.105.0 起默认启用（avoid_mode=hard）** |
| `l3.regime_gating.min_confidence` | float | `0.55` | —（settings.yaml l3 段） | 子链方向 Gate 置信度门槛：bull/bear 且置信度 ≥ 门槛才给方向（long/short）；方向判定存在但置信度不足 → avoid（方向不明不参与，防子链 3 品种噪声误 Gate） |
| `l3.regime_gating.avoid_mode` | str | `"hard"` | —（settings.yaml l3 段） | avoid 子链处理模式：`hard`=剔除（零持仓）/ `soft`=按 soft_avoid_ratio 降权（小仓位保留，连续过渡防 cliff） |
| `l3.regime_gating.soft_avoid_ratio` | float | `0.3` | —（settings.yaml l3 段） | soft_avoid 降权系数（0.3=保留 30% 暴露） |
| `l3.regime_gating.blind_default` | str | `"avoid"` | —（settings.yaml l3 段） | 无子链归属品种（盲测池等）默认处理：`avoid`=回避（不放行）/ `neutral`=保留 |
| `l3.regime_gating.exposure_min` | float | `0.4` | —（settings.yaml l3 段） | 暴露缩放映射下限（plans/48 §B）：子链置信度 < exposure_min → 品种暴露 0（不参与） |
| `l3.regime_gating.exposure_sat` | float | `0.7` | —（settings.yaml l3 段） | 暴露缩放映射饱和点：子链置信度 ≥ exposure_sat → 品种暴露 1.0（满仓）；中间线性插值；暴露 = 置信度映射 × 品种-链对齐度 |
| `l3.subchain_quality.enabled` | bool | `true` | —（settings.yaml l3 段） | 因子×子链质量矩阵与生命周期张量化开关（plans/49，v2.104.0+112）：`true`=评审质检（Q10/F6 两级判定 + 机审单链特异放行 + 准入三级权重）与生命周期（单元粒度退化检测 + scope 动态收缩闭环）按 (factor_id, market, chain) 单元评估，质量矩阵入 `subchain_factor_quality` 表；`false`=回退全链原逻辑（向后兼容，无子链画像的股票/期货因子天然走回退）。仅 market=energy 生效。**v2.105.0 起默认启用**（2026-08-17 已回填 196 因子画像 + 784 行质量矩阵） |
| `l3.subchain_quality.decay_threshold` | float | `0.30` | —（settings.yaml l3 段） | 子链有效 IC 衰减触发阈值（对齐全链 `_rolling_stats` 口径）：该子链最近 window 期均值 IC 较早期基准衰减 ≥ 阈值判定失效链 |
| `l3.subchain_quality.drop_severe` | float | `0.50` | —（settings.yaml l3 段） | 子链严重衰减阈值：衰减 ≥ 阈值直接触发退化（不待冷却期确认） |
| `l3.subchain_quality.window_days` | int | `60` | —（settings.yaml l3 段） | 子链退化检测回看天数（内部映射为最近期数窗口 `window=max(1, round(window_days/30))`，期 = 一次评审评估）：当前期 vs 早期基准的对比期数，样本不足返回 None（审计 skipped 不误判） |
| `l3.subchain_quality.min_periods` | int | `5` | —（settings.yaml l3 段） | 退化检测最小期数护栏：历史期数 < min_periods 不做退化判定（风险 1：子链 IC 时序样本不足保护） |
| `l3.subchain_quality.cooldown_days` | int | `30` | —（settings.yaml l3 段） | degraded 因子冷却期（交易日，复用 energy_qa_review 冷却期）：冷却期内不重审，期满重审达标 → 回 active 并重评子链画像（scope 回滚机制） |
| `l3.regime_beta_layer.enabled` | bool | `true` | —（settings.yaml l3 段，2026-08-19 灰度开启） | L0 宏观 Beta 层开关（plans/55，v2.105.0+22）：`true`=识别市场 Beta 方向（RISK_ON/RISK_OFF/RANGE_BOUND）并顺 β 方向配置敞口（信号管线多空不对称偏置 + 组合 beta_scale 乘性缩放 + 实盘风控档位叠加）；`false`=零行为变更。仅 market=energy 生效；信号管线 CLI `--enable-regime-beta` 显式传参优先。**灰度开启依据**：预测力决策门通过（step=1 日频 fwd=10 收益 K-W p=0.0203 显著 + 排序 RISK_ON>RANGE_BOUND>RISK_OFF 正确，trace_id beta-kw-v3b-20260819） |
| `l3.regime_beta_layer.days` | int | `130` | —（settings.yaml l3 段） | 金融期货数据回溯天数（≥ 趋势长窗+余量） |
| `l3.regime_beta_layer.fin_symbols` | list | `[IF0, IH0, IC0, IM0, TF0, TS0]` | —（settings.yaml l3 段） | 金融期货合成指数成分（CFFEX 连续合约格式，FTS 品种池内） |
| `l3.regime_beta_layer.trend_window_short` | int | `20` | —（settings.yaml l3 段） | 合成指数趋势短窗（MA20） |
| `l3.regime_beta_layer.trend_window_long` | int | `60` | —（settings.yaml l3 段） | 合成指数趋势长窗（MA60），趋势分 = (MA20-MA60)/MA60 |
| `l3.regime_beta_layer.vol_window` | int | `20` | —（settings.yaml l3 段） | realized vol 计算窗口（年化） |
| `l3.regime_beta_layer.vol_threshold_percentile` | float | `0.8` | —（settings.yaml l3 段） | 高波动历史分位阈值：最新 vol 高于此 → 波动门控为"高"（不可判 RISK_ON） |
| `l3.regime_beta_layer.risk_pref_pair` | list | `[IF0, TF0]` | —（settings.yaml l3 段） | 股债比（风险资产/避险资产）：FTS 池内无 T0，以 TF0 五年期国债为避险锚 |
| `l3.regime_beta_layer.risk_pref_window` | int | `20` | —（settings.yaml l3 段） | 股债比滚动 z-score 窗口（换月缺口 ffill + min_periods 兜底） |
| `l3.regime_beta_layer.min_confidence` | float | `0.5` | —（settings.yaml l3 段） | Beta 置信度门槛：软投票置信度低于 → RANGE_BOUND（不偏置） |
| `l3.regime_beta_layer.min_votes` | int | `2` | —（settings.yaml l3 段） | 软投票最少一致信号数（trend/vol 门控/risk_pref 三源） |
| `l3.regime_beta_layer.on_scale` | float | `1.0` | —（settings.yaml l3 段） | RISK_ON 组合总敞口倍率（顺正 β，默认不放大） |
| `l3.regime_beta_layer.on_long_boost` | float | `0.10` | —（settings.yaml l3 段） | RISK_ON 多头信号加分（×1.10） |
| `l3.regime_beta_layer.on_short_suppress` | float | `0.10` | —（settings.yaml l3 段） | RISK_ON 空头信号减分（×0.90） |
| `l3.regime_beta_layer.off_scale` | float | `0.7` | —（settings.yaml l3 段，灰度保守档） | RISK_OFF 组合总敞口倍率（灰度保守档：压缩 30%，原设计 0.5；观察稳定后校准调优） |
| `l3.regime_beta_layer.off_long_suppress` | float | `0.25` | —（settings.yaml l3 段，灰度保守档） | RISK_OFF 多头信号抑制（灰度保守档：×0.75，原设计 ×0.60） |
| `l3.regime_beta_layer.off_short_boost` | float | `0.10` | —（settings.yaml l3 段，灰度保守档） | RISK_OFF 空头信号放大（灰度保守档：×1.10，原设计 ×1.20，期货反向进攻） |
| `l3.regime_crowding.enabled` | bool | `false` | —（settings.yaml l3 段，plans/56 默认关） | 拥挤度体系化开关（plans/56，v2.105.0+25）：`true`=6 信号合成拥挤度 + 联合门控 + 多空方向偏置；`false`=零行为变更。仅 market=energy 生效；信号管线 CLI `--enable-regime-crowding` 显式传参优先。**决策门 ❌ 未通过（高拥挤样本不足 + 事件研究命中率 0%），灰度保持关闭，待阈值校准后重跑** |
| `l3.regime_crowding.days` | int | `300` | —（settings.yaml l3 段） | 拥挤度面板回溯天数（≥ 动量长窗 + 分位历史） |
| `l3.regime_crowding.high_crowding` | float | `0.6` | —（settings.yaml l3 段） | 高拥挤阈值（score ≥ → 触发联合门控/方向偏置）；决策门实测高拥挤样本过少（2/105），待校准 |
| `l3.regime_crowding.high_conf_high_crowd_scale` | float | `0.5` | —（settings.yaml l3 段） | 联合门控：高置信+高拥挤 → 敞口减半 |
| `l3.regime_crowding.low_conf_high_crowd_scale` | float | `0.0` | —（settings.yaml l3 段） | 联合门控：低置信+高拥挤 → 离场 |
| `l3.regime_crowding.long_crowd_suppress` | float | `0.30` | —（settings.yaml l3 段） | 多头拥挤：多头信号 ×0.70（减多不抢反弹） |
| `l3.regime_crowding.short_crowd_suppress` | float | `0.30` | —（settings.yaml l3 段） | 空头拥挤：空头信号 ×0.70（减空不追空/不逼空） |
| `l3.owl.enabled` | bool | `false` | —（settings.yaml l3 段） | OWL 因子分组筛选旁路开关（plans/41 方案 A，v2.104.0+84）：`true`=Step 1.8c 执行 OWL 旁路；`false`=零开销零行为变更 |
| `l3.owl.report_only` | bool | `true` | —（settings.yaml l3 段） | OWL 旁路报告模式（默认 true）：`true`=仅输出交叉比对报告（落盘 `memory/portfolio/{universe}/owl/owl_report_{date}.json` + state.owl_report），不修改 factors 列表；`false` 预留契约本期不实现 |
| `l3.owl.weight_scheme` | str | `"linear"` | —（settings.yaml l3 段） | OWL 权重衰减方案：`linear`/`exp`/`log`（非递增，大系数惩罚更重） |
| `l3.owl.weight_tuning` | float | `0.5` | —（settings.yaml l3 段） | OWL 权重衰减强度 (0,1]：越大衰减越陡；0 退化全等权（≈LASSO 变体） |
| `l3.owl.train_frac` | float | `0.7` | —（settings.yaml l3 段） | OWL 样本外切割训练窗比例（(0,1)，内部 clip 至 [0.5,0.95]）：系数只用训练窗拟合，检验窗仅验证稳定性（防数据窥探） |
| `l3.owl.group_corr_threshold` | float | `0.5` | —（settings.yaml l3 段） | OWL 系数分组相关阈值：两两 |corr|≥thr 视为同组 |
| `l3.owl.lambda_` | float | `0.05` | —（settings.yaml l3 段） | OWL 正则化强度（0.5/n 归一尺度，与 sklearn Lasso 对齐） |
| `l3.regime_conditional.enabled` | bool | `false` | —（settings.yaml l3 段） | 因子×制度条件化权重开关（plans/53 §B，仅 market=energy 生效）：`true`=Step 2.5 后当前制度 IC 显著为负（ic < -min_abs_ic）的因子权重归零/降权；`false`=零行为变更 |
| `l3.regime_conditional.decay_mode` | str | `"zero"` | —（settings.yaml l3 段） | 显著负向因子处理模式：`zero`=归零 / `soft`=按 \|ic\|/max_ic 相对缩放（soft_min_ratio 保底） |
| `l3.regime_conditional.soft_min_ratio` | float | `0.0` | —（settings.yaml l3 段） | soft 模式最低保留比例（0.0=可归零，1.0=等效全保留） |
| `l3.regime_conditional.scope_default` | str | `"all"` | —（settings.yaml l3 段） | 无 regime 画像字段因子的默认处理（all=全保留，不误杀） |
| `l3.regime_conditional.min_abs_ic` | float | `0.05` | —（settings.yaml l3 段） | 显著负向 IC 幅度门槛：ic < -min_abs_ic 才触发降权（对齐 regime_profile.min_abs_ic） |
| `l3_regime_ic_report_enabled` | bool | `false` | `FTS_L3_REGIME_IC_REPORT`（settings.py FTSConfig） | 评估链 regime_ic_report 报告段开关（plans/53 §A2）：`true`=横截面评估对 energy 面板构建"因子×制度"画像（秒级成本，批量评估默认关）；`false`=零开销 |
| `regime_profile.min_regime_samples` | int | `20` | —（settings.yaml regime_profile 段） | Regime 画像护栏门槛①：制度内最小样本数（不足直接 effective=False） |
| `regime_profile.min_positive_regimes` | int | `2` | —（settings.yaml regime_profile 段） | 晋升门槛（plans/53 §C1）：regime_ic_report 存在且有效制度数低于此值 → 拒绝晋升（防单制度过拟合因子） |
| `regime_profile.min_abs_ic` | float | `0.05` | —（settings.yaml regime_profile 段） | Regime 画像护栏门槛②：\|IC\| 幅度门槛（制度内样本 20~60 下 0.03 噪声误报率高，实施校准为 0.05） |
| `regime_profile.all_regimes_effective_min` | int | `3` | —（settings.yaml regime_profile 段） | ≥ 此制度数 effective 时 scope='all'（不降权） |
| `l3.synthesis.mode` | str | `"equal_weight"` | —（settings.yaml l3 段，CLI `--synthesis-mode` 优先） | L3 合成模式默认值（v2.104.0+62）：`equal_weight`/`quality_weight`/`sharpe_weight`/`elastic_net`/`adaptive`/`optimizer`/`risk_parity`；直接改配置即切换合成方法无需改代码 |
| `l3.synthesis.optimizer_mode` | str | `"risk_parity"` | —（settings.yaml l3 段） | optimizer 类模式目标默认值：`risk_parity`/`mvo`/`bl`（v2.104.0+62）；`--synthesis-mode risk_parity` 即映射 optimizer + 本目标 |
| `l3.factor_score.equal_weight_floor` | float | `0.5` | —（settings.yaml l3 段） | quality_weight 等权下限系数（配置项 SSOT，调参仅改本配置不改代码）：权重下限 = 系数 / N，防权重极端分化；提高系数可提升权重分散度但放大尾部因子暴露；代码默认 0.5 仅配置缺失兜底；取值域 (0,1] |
| `FTS_L3_AUTO_FACTOR_RETURNS` | str | 未设 | env | L3 自动构建因子收益矩阵开关（v2.104.0+2）：`1`=optimizer 之外的模式也自动构建用于组合实测指标；optimizer/risk_parity 模式默认自动构建但**仅用于权重合成**（risk_parity 只用协方差 Σ，自动矩阵 Sharpe 虚高不影响权重；组合指标口径保持估算，不污染） |
| `evolution_shadow_observe`（环境变量直读） | bool | `false` | `FTS_EVOLUTION_SHADOW_OBSERVE` | 新晋级精英因子影子池观察期开关（v2.103.0+20）：`1`=晋升写入 shadow_pool 标记（观察 5 交易日，L3 观察期内不纳入组合）；`0`/未设=默认关闭（新晋级直接进正式组合）。仅作用于新晋级因子，重审降级因子 shadow_pool 保留不变 |
| `b_grade_observe_enabled`（GradeThreshold 配置） | bool | `false` | — | elite_tracker B 级因子观察期开关（v2.103.0+28 默认关闭）：`false`=B 级因子（30≤score<40）直接 active 入池，不进入 observing；`true`=恢复 3 个月（默认）观察期。与 shadow_pool 5 交易日观察为两套独立机制 |
| `adaptive_config.probability_mix` | bool | `true` | —（AdaptiveWeightConfig） | Regime 制度概率混合开关（regime blend，28-T4）：启用且 regime 含 `regime_probs` 时按概率加权混合全部制度倍率；关闭或缺失 probs 时回退硬查表（28 计划） |
| `adaptive_config.confidence_scale` | bool | `true` | —（AdaptiveWeightConfig） | 置信度仓位缩放开关（28-T4）：启用时 Step 2.5 计算 exposure_scale 供 build_combo 整体缩放；关闭时恒 1.0（28 计划） |
| `adaptive_config.confidence_scale_min` | float | `0.3` | —（AdaptiveWeightConfig） | 熵标定后 exposure_scale 缩放下限（28-T4）：`scaled = confidence × (1 − entropy_penalty × H_norm)` 后 clip 到 [scale_min, 1.0]（28 计划） |
| `adaptive_config.confidence_entropy_penalty` | float | `0.5` | —（AdaptiveWeightConfig） | 熵标定惩罚系数（28-T4）：后验熵越大置信度折扣越大（均匀分布最大折扣）（28 计划） |
| `adaptive_config.smoother.de_risk_alpha` | float | `0.8` | —（AdaptiveWeightConfig.smoother） | RegimeSmoother 风控切换（进入降风险制度）不对称系数（28-T6）：切换更激进平滑（28 计划） |
| `adaptive_config.smoother.re_risk_alpha` | float | `0.1` | —（AdaptiveWeightConfig.smoother） | RegimeSmoother 恢复切换（离开降风险制度）不对称系数（28-T6）：恢复更保守平滑（28 计划） |

**股票信号管道脚本参数（`scripts/daily_signal_pipeline.py`，GAP-076，v2.101.0；以下股票专属配置项已随股票管线剥离至 fts-stock（2026-08），主系统 FTSConfig 中已移除，保留列表仅供历史追溯）**：

| 参数 | 取值 | 默认 | 说明 |
|:-----|:-----|:-----|:-----|
| `--normalize` | `none` / `zscore` / `rank` | `none` | 因子信号截面标准化方式：none=原始信号 / zscore=每交易日截面 z 分数（ddof=0）/ rank=每交易日截面百分位秩映射到 [-1,1]。重算日写入权重快照 `normalize` 字段，冻结日读取同值应用保证口径一致（旧快照缺省 none 向后兼容） |
| `--force-recompute` | — | 关 | 强制重算 Ridge 权重并更新快照（GAP-072，默认按 l3_weight_recompute_cadence 自动判定） |
| `--max-weight-cap` | float | `None`（应用默认 `0.30`） | 单因子权重上限（v2.104.0+11）：Ridge 弱信号场景易产生单因子权重过大（如 43.5%），超限因子截断后多余权重按比例重分配给未超限因子再归一化；`None` 表示用代码默认 0.30，可传其他值（如 `0.20`）覆盖，`--max-weight-cap 0` 关闭截断 |
| `portfolio_max_factors` | int | 20 | — | L3 组合最大因子数 |
| `portfolio_top_n` | int | 5 | — | L3 Top N 输出 |
| `portfolio_decay_days` | int | 90 | — | L3 衰减检验窗口 |
| `portfolio_optimizer_mode` | str | `"risk_parity"` | `FTS_PORTFOLIO_OPTIMIZER_MODE` | L3 optimizer 模式目标（`risk_parity`/`mvo`，GAP-I302，v2.74.0） |
| `log_level` | str | `"INFO"` | `FTS_LOG_LEVEL` | 日志级别 |
| `log_file` | str | `""` | `FTS_LOG_FILE` | 日志文件路径 |
| `stock_neutralization` | bool | `true` | `FTS_STOCK_NEUTRALIZATION` | 股票因子横截面评估是否做行业/市值中性化（v2.57.0） |
| `industry_map_path` | str | `"data/industry_map.json"` | `FTS_INDUSTRY_MAP_PATH` | 行业映射文件路径（JSON，`{symbol: industry_name}`，v2.57.0） |
| `cap_map_path` | str | `""`（动态） | `FTS_CAP_MAP_PATH` | 市值映射文件路径（JSON，`{symbol: market_cap}`，v2.57.0；GAP-086 v2.103.0 默认动态化——env 优先；未设置且 `data/cap_map.json` 存在（`scripts/build_cap_map.py` 生成）时自动指向该文件使市值中性化生效，不存在保持空串降级） |
| `stock_signal_neutralize` | str | `"none"` | `FTS_STOCK_SIGNAL_NEUTRALIZE` | 股票信号管道截面中性化方式（`none`/`industry`/`size`/`both`，D.2，v2.101.0） |
| `stock_signal_l3_mode` | str | `"ridge"` | `FTS_STOCK_SIGNAL_L3_MODE` | 股票信号管道 L3 权重学习模式（`ridge`/`elastic_net`，D.2，v2.101.0） |
| `stock_signal_regime` | str | `"none"` | `FTS_STOCK_SIGNAL_REGIME` | 股票信号管道 Regime 自适应权重（`none`/`auto`，D.2 偏差 b，v2.101.0） |
| `cs_panel_min_coverage` | float | `0.8` | `FTS_CS_MIN_COVERAGE` | 股票横截面共同日期覆盖率阈值：某日至少 `ceil(股票数×该值)` 只股票有数据才纳入共同日期（替代全量硬交集，GAP-XXX，v2.103.0） |
| `ashare_special_enabled` | bool | `false` | `FTS_ASHARE_SPECIAL_ENABLED` | A 股特有字段注入开关（GAP-081，v2.103.0）：`true` 时 FundamentalProvider.enrich_ohlcv 注入北向/两融/股东户数/分析师预期字段（读 ashare_special_cache，先执行 scripts/backfill_ashare_special.py 回填；默认关避免未回填时引入网络请求） |
| `stock_fundamental_enabled` | bool | `false` | `FTS_STOCK_FUNDAMENTAL_ENABLED` | 股票基本面字段注入开关（GAP-082，v2.103.0）：`true` 时 FundamentalProvider.enrich_ohlcv 注入估值/市值日频 + 财务/成长季度字段（读 stock_fundamental_cache，先执行 scripts/backfill_stock_fundamental.py 回填；默认关避免未回填时引入网络请求） |
| `futures_adjusted` | bool | `true` | `FTS_FUTURES_ADJUSTED` | 期货连续合约 K 线是否默认返回换月复权序列（因子计算用，v2.58.0） |
| `roll_cost_bps` | float | `2.0` | `FTS_ROLL_COST_BPS` | 展期成本系数（基点/次，回测持仓穿越换月日扣除，v2.58.0） |
| `minute_cache_max_age_days` | int | `1` | `FTS_MINUTE_CACHE_MAX_AGE_DAYS` | 分钟缓存最大新鲜度（天，独立于日线 30 天窗口——避免旧分钟缓存持续命中挡住 TDX 实时拉取，v2.101.0） |
| `eval_horizons` | tuple[int,...] | `(1,5,10,20)` | `FTS_EVAL_HORIZONS` | 多持有期 IC 体系：横截面/时序评估的多持有期列表（空=关闭，v2.90.0，GAP-060） |
| `cost_sensitivity_enabled` | bool | `false` | `FTS_COST_SENSITIVITY_ENABLED` | 可交易性压力层：评估链是否输出成本敏感性扫描（滑点 1/2/4/8 倍，v2.97.0，GAP-061） |
| `inject_overnight_gap_enabled` | bool | `true` | `FTS_INJECT_OVERNIGHT_GAP` | 夜盘/隔夜跳空标记：`get_ohlcv` 是否注入 overnight_gap/overnight_gap_flag 列（v2.97.0 GAP-066；v2.103.0+15 G8 D5 默认 `false`→`true`） |
| `overnight_gap_flag_threshold` | float | `0.01` | `FTS_OVERNIGHT_GAP_THRESHOLD` | 隔夜跳空标记阈值：\|overnight_gap\| ≥ 该值 flag=1（v2.97.0，GAP-066） |
| `inject_data_gap_enabled` | bool | `true` | `FTS_INJECT_DATA_GAP` | G8 断K/跳空清洗标记：`get_futures_panel` 面板 df 是否附加 data_gap/gap_anomaly 列（断K不进因子计算、异常跳空进 QC，v2.103.0+15） |
| `factor_turnover_daily_max` | float\|null | `0.45` | `FTS_FACTOR_TURNOVER_DAILY_MAX` | G11 日换手硬剔除阈值（信号翻转率口径 turnover/(21×2)）：`evaluate_backtest`/`_evaluate_cross_section` 失败原因接入该门槛。**2026-08-13 判定（v2.104.0+9 修订）：期货换手成本低，但换手率过高同样无交易价值 → 期货主系统默认 `0.45` 开启（P95 校准：83 个 active 期货因子真实分布 P95=0.456，仅拦 top ~5% 天天翻仓的极端抖动因子）**（v2.103.0+15 观察期 → v2.104.0+1 定值 0.30 → v2.104.0+5 回默认关闭 → v2.104.0+9 重开 0.45；env：数值=覆盖阈值，"off"/"none"/"0"=关闭，空值=默认 0.45；股票侧 fts-stock 如需更严可设 0.30=P90 参考值；口径说明见 §2.1） |
| `futures_neutralization` | bool | `true` | `FTS_FUTURES_NEUTRALIZATION` | 期货横截面因子评估是否做板块/产业链中性化（GAP-F03，v2.59.0） |
| `futures_enhance_enabled` | bool | `false` | `FTS_FUTURES_ENHANCE_ENABLED` | 字段增强层 iFinD SDK 选项（GAP-083 阶段 C，v2.101.0）：`true` 时在 `tqsdk_sources_enabled` 已启用天勤增强源的基础上**追加** IFindSDKSource（方案 A：iFinD 官方 SDK 直连补 settle/pre_settle 权威值，需本地安装 iFinDPy + .env 凭据 IFIND_TOKEN 或 IFIND_USERNAME/PASSWORD，失败自动降级）。v3.0.0+2 起天勤增强源由 `tqsdk_sources_enabled` 统一门控，本开关仅控制 iFinD SDK 追加 |
| `tqsdk_sources_enabled` | bool | `false` | `FTS_TQSDK_SOURCES_ENABLED` | 天勤 TQSDK 源 opt-in（v3.0.0+2，plans/57 QuantData 主链路彻底解耦）：主链路为 QUANTDATA 时天勤增强源（TQSDKEnhanceSource，close_oi→hold/oi_change）、分钟源（TQSDKSource 5m）、tick 源（TQSDKTickSource）**不再默认挂载**（此前默认注册导致感知链路逐品种自建天勤连接 + 15s wait_update，L1 Meta-Loop 实测每品种 ~20s）；QuantData 下 hold 已为 L0 权威字段（open_interest），天勤增强冗余。需要天勤 fallback/增强时显式设 `true` 恢复旧行为 |
| `backtest_trade_filter` | bool | `true` | `FTS_BACKTEST_TRADE_FILTER` | 回测是否启用涨跌停拦截 + 停牌过滤（GAP-F02，v2.59.0） |
| `futures_limit_pct` | float | `0.08` | `FTS_FUTURES_LIMIT_PCT` | 期货涨跌停判定阈值（单日涨跌幅 ≥ 该值视为涨跌停，GAP-F02，v2.59.0） |
| `cross_section_panel_vector` | bool | `true` | `FTS_CROSS_SECTION_PANEL_VECTOR` | 横截面评估全矩阵化开关（plans/37 Phase 1+3，plans/39 §11 回退后）：`cross_section_evaluate_backtest` 的 `_cs_compute_ics` 分派到 `panel_vector.compute_cs_ics_vectorized`（联合掩码 rank + 行内 Pearson），**信号/收益构建恒逐品种执行**（算子因子面板化 `execute_factor_panel` 经 plans/39 §11 v2.104.0+58 实测真实缺口面板 0.3x <5x 门槛登记豁免摘除，仅保留为独立模块/对照基准）；产出与旧路径逐位一致；**v2.104.0+57 起默认开启**（对照测试全绿 + 缺口面板实测产出一致/性能持平），可设 `false` 关闭 |
| `ops_numba` | bool | `true` | `FTS_OPS_NUMBA` | numba 算子开关（plans/38 批4 + plans/40 C 层，v2.104.0+63）：启用时 `numba_kernels.py` 走 numba 加速路径（ts_rank 1D/2D 内核 + **plans/40 C 层重新接入的 ts_zscore/ts_cvar 1D 内核**，见 `feature_ops.ts_zscore` / `ops_library.ts_cvar_95/99` 快速路径）；关闭或依赖缺失/版本冲突时回退现值实现（零漂移），与 `cross_section_panel_vector` 正交互不耦合 |
| `force_walkforward` | bool | `true` | `FTS_FORCE_WALKFORWARD` | 因子晋升路径是否强制 WalkForward 冷启动样本外验证（GAP-F08，v2.60.0） |
| `margin_rate_map` | dict | 见默认表 | —（YAML） | 品种保证金率表（{symbol: 保证金率}，未配置品种用默认 0.10，GAP-F09，v2.60.0） |
| `max_margin_usage` | float | `0.80` | `FTS_MAX_MARGIN_USAGE` | 最大保证金占用率（保证金占用/总权益，超过触发强平风险告警，GAP-F09，v2.60.0） |
| `mcp_enabled` | bool | `false` | `FTS_MCP_ENABLED` | 是否启用 Wind/iFinD MCP 增强字段（启用时若未注入 MCP 客户端抛 RuntimeError 显式报错，未启用则明确降级跳过增强字段，GAP-F04，v2.60.0） |
| `duckdb_single_writer` | bool | `true` | `FTS_DUCKDB_SINGLE_WRITER` | 是否启用 DuckDB 单写者模式（所有写收敛唯一 writer，false 回退旧多路径，GAP-056，v2.86.0） |
| `duckdb_read_pool_size` | int | `4` | `FTS_DUCKDB_READ_POOL_SIZE` | DuckDB 读连接池大小（读操作与单写者解耦，互不阻塞，GAP-056，v2.86.0） |
| `duckdb_batch_size` | int | `1000` | `FTS_DUCKDB_BATCH_SIZE` | DuckDB 批量写入缓冲行数（批量 COPY 降低 commit 频率，GAP-056，v2.86.0） |
| `duckdb_commit_every` | int | `100` | `FTS_DUCKDB_COMMIT_EVERY` | DuckDB 批量写入 commit 周期（秒，GAP-056，v2.86.0） |
| `l3_signal_store_enabled` | bool | `true` | `FTS_L3_SIGNAL_STORE` | L3 信号矩阵一等公民增量库开关（plans/40 D 层，plans/51 B1 激活）：`PortfolioLoop` 构造自动启用 `l3_signal_service.load_or_build_signal_matrix`，同 (factor, code, params) 信号不再重算（同窗口因子级增量复用，窗口推进经 A2 形状防护安全重算）；`false` 回退纯全量构建零漂移 |
| `l3_signal_store_db` | str | `data/l3_signal_store.duckdb` | `FTS_L3_SIGNAL_STORE_DB` | L3 信号矩阵库路径（登记于 `storage_landscape.yaml` l3_signal_assets 域，plans/51 B2） |
| `l3_signal_cache_entries` | int | `20000` | `FTS_L3_SIGNAL_CACHE_ENTRIES` | L3 信号缓存容量上限（plans/40 A 层；plans/51 C2 配置化，原模块级常量） |
| `l3_signal_store_append_window` | bool | `true` | `FTS_L3_SIGNAL_APPEND_WINDOW` | L3 信号矩阵增量窗口追加（plans/52，GAP-139）：窗口推进时对可复用因子仅重算新增交易日 + 窗口回退段（meta `dates_digest` 前缀判定，抽样对照验证不过自动全量零漂移）；`false` 回退"同窗口因子级复用"现行为（跨日全量重算） |
| QuantData 权威主链路路径（v2.105.0+32，v3.0.0+1 重构） | str | `D:\QuantData` | `FTS_QUANTDATA_HOME` | QuantData 数据仓库根目录：`fts/data_sources/quantdata_provider.py` duckdb 只读短连接直读 continuous_daily/continuous_map/kline_daily；FTS 因子生命周期管理 K 线唯一数据源；默认降级链：DUCKDB_CACHE→QUANTDATA→SYNTHETIC，TDX_LOCAL/AKShare 仅为显式扩展场景，不依赖跨项目 client_v2.py |
| 信号契约 v1 三列（plans/57，v3.0.0） | — | schema_version=1 / factor_status=pending / factor_scope=`{"subchain_scope":"all"}` | — | `l3_signal_meta` 追加列（幂等迁移）：`schema_version`（契约版本，RD 校验不兼容即降级）、`factor_status`（active/degraded/shadow/retired，FTS 状态传播）、`factor_scope`（subchain_scope + subchain_specific 特异因子链范围）；RD `signal_client` 决策/训练双模式拉取 + 增量幂等 + 新鲜度校验 + 降级熔断（design/F.3） |

### 2.1 G11 日换手口径说明（信号翻转率）

G11 的"日换手"衡量的是**因子信号本身的变号频率**，**不是期货合约换手率**：

| 概念 | 口径 | 衡量对象 |
|:-----|:-----|:---------|
| G11 日换手 | `turnover_daily = mean(\|Δsign(信号)\|)/2`（时序路径 `turnover/(21×2)`，横截面路径 `np.nanmean(\|Δsign\|)/2`） | 因子信号在相邻交易日翻转符号的占比 → 因子层面调仓频率（摩擦成本代理） |
| 期货合约换手率 | `成交量 ÷ 持仓量` | 市场交易活跃度 / 流动性 |

- 值域 `[0,1]`：0=从不翻转（买入持有），1=每日变号（每日全仓反转）。
- 与市场活跃度无关：只看因子信号翻不翻，不看市场成交放不放量。
- 语义判断（2026-08-13，v2.104.0+9 修订）：换手硬剔除建立在"高换手 = 高摩擦成本"前提上，该前提在股票（印花税 + 佣金 + T+1，单次往返 ≈0.12%）成立、在期货（手续费为主，单次往返 ≈0.02%）明显弱化——但换手率过高在期货同样无交易价值，故期货主系统 `factor_turnover_daily_max` 默认 `0.45` 开启（P95 校准：83 个 active 期货因子真实分布 P95=0.456，仅拦 top ~5% 天天翻仓的极端抖动因子，较股票 0.30=P90 参考值更宽松）；env 可配置（数值=覆盖，"off"/"none"/"0"=关闭）。评估链/演化审计实现中 `FTSConfig.factor_turnover_daily_max` 默认 0.45（settings.py 生效），仅当 env 显式关闭时硬剔除不生效。

## 3. YAML 配置文件

`config/settings.yaml` 示例：

```yaml
default_market: "futures"
llm_backend: "openai"
max_generations: 10
micro_trials_per_generation: 50
portfolio_max_factors: 20
evolution_mode: "operator_first"   # operator(算子主干) / operator_first(算子优先,LLM/GP兜底) / code(代码创新) / hybrid(混合) / batch(批量挖掘漏斗, GAP-I201)
batch_size: 20             # batch 模式每代候选生成数
batch_max_candidates: 5    # batch 模式进入细评估的最大候选数
batch_max_workers: 4       # batch 粗筛并行线程数
batch_random_seed: 42      # batch 随机种子
```

> **evolution_mode 说明（Phase C.2 / GAP-I201）**：取值 `operator`（算子主干）/ `operator_first`（算子优先，LLM/GP 兜底，v2.105.0+2 起默认）/ `code`（代码创新）/ `hybrid`（混合）/ `batch`（批量挖掘漏斗），默认 `operator_first`，支持环境变量 `FTS_EVOLUTION_MODE` 覆盖。`operator_first` 模式优先走本地算子演化（零 token 消耗），算子失败/空结果时 LLM macro 与 GP 兜底，配合生成端去重前置（Step 1.35）减少重复评估算力。`batch` 模式（v2.65.0）每代对同一父因子批量生成 `batch_size` 个后代（macro 至多 1 次 + GP/operator 交替 + seed 递增），ThreadPoolExecutor 并行粗筛后按预筛 IC 排序截断 `batch_max_candidates` 个进入细评估准入链；token 护栏（每代至多 1 次 LLM）与既有熔断协同。

### 3.1 品种池/产业链配置（config/futures_universe.yaml，v2.104.0+38）

品种池与产业链分类单一事实源（SSOT），驱动 `fts.data_futures` 全部池常量。加载优先级：
**`config/futures_universe.yaml` > 内置默认**（YAML 缺失/损坏/校验失败时回退内置默认并告警，保证无配置环境可运行）。

```yaml
universe:            # FUTURES_SUBSET（按交易所分组，展平后 82 品种）
  dce: [V0, ...]     # 大商所
  czce: [...]        # 郑商所
  shfe: [...]        # 上期所
  ine: [...]         # 能源中心
  cffex: [...]       # 中金所
  gfex: [...]        # 广期所
core_subset: [...]   # FUTURES_CORE_SUBSET（25）
holdout: [...]       # FUTURES_HOLDOUT（15 盲测池，GAP-055）
stratified_subset: [...]       # FUTURES_STRATIFIED_SUBSET（19 分层训练集）
sector_map: {...}    # FUTURES_SECTOR_MAP 主体（17 产业链；"炼化聚酯链"由 energy 训练池自动生成置首位）
workflows:           # 产业链专属工作流（ENERGY_CHAIN_*）
  energy:
    chain_symbols: [...]        # 训练池（ENERGY_CHAIN_SYMBOLS，12）
    chemical_sectors: [...]     # 泛化范围（ENERGY_CHAIN_CHEMICAL_SECTORS）
    market: energy              # 因子库路由标记（ENERGY_CHAIN_MARKET）
    min_train_rows: 300         # 训练品种深度阈值（ENERGY_CHAIN_MIN_TRAIN_ROWS）
    min_holdout_rows: 250       # 盲测池最小历史门槛（ENERGY_CHAIN_MIN_HOLDOUT_ROWS，v2.104.0+81 GAP-130；盲测 IC 验证跳过历史不足品种）
    l1_*: [...]                 # L1 独立输出目录（ENERGY_CHAIN_L1_*）
```

校验规则（任一失败即回退内置默认）：universe 无重复、各池 ⊆ universe、盲测池 ∩ 分层训练集 = ∅、
泛化范围子链名存在于 sector_map。**energy 盲测池（ENERGY_CHAIN_HOLDOUT）自动派生** =
泛化范围全部成员 − 训练池，改 `chain_symbols`/`chemical_sectors` 即自动重算，无需手写盲测池。

## 4. Verifier 配置（锁定不可修改）

L2 Verifier 默认配置（定义在 `contracts.py` 中，初始化后锁定）：

| 字段 | 默认值 | 说明 |
|:-----|:-------|:-----|
| `min_ic` | 0.03 | 最小 IC |
| `min_icir` | 0.5 | 最小 ICIR |
| `min_sharpe` | 1.5 | 最小夏普 |
| `max_drawdown` | 0.20 | 最大回撤 |
| `min_economic_score` | 3 | 最小经济逻辑达标维度 |
| `min_t_stat` | 3.0 | 最小 t 统计量 |
| `max_fdr` | 0.05 | 最大 FDR |
| `min_oos_ratio` | 0.30 | 最小样本外比例 |
| `max_turnover_monthly` | 5.0 | 最大月度换手率（次/月，= turnover_daily×42，G11 日换手口径）；`turnover_cost_net=True` 时为**成本敏感净收益校验触发线**（换手超线不再硬剔，改判成本后净夏普），`False` 时回退绝对阈值硬剔 |
| `one_side_cost_rate` | 0.0005 | 期货单边往返成本率（含滑点+手续费+冲击，默认 5bps；可经 `FTS_COST_*` 实证标定值覆盖），用于成本敏感净收益校验 |
| `assumed_annual_vol` | 0.15 | 年化波动率假设（与 `cost_model._ASSUMED_ANNUAL_VOL` 一致），用于将年化成本侵蚀换算为夏普惩罚 |
| `turnover_cost_net` | True | 成本敏感净收益校验开关（方案 A，v2.104.0+13）：换手超 `max_turnover_monthly` 时，按 `净夏普 = 毛夏普 − 年化成本侵蚀/年化波动` 判定（年化成本侵蚀 = 月换手×12×2×单边成本率），净夏普仍 ≥ `min_sharpe` 即准入并记录 `cost_adjusted` 明细；`False` 时回退旧绝对阈值硬剔 |

> 口径修正（v2.104.0+13，GAP-114）：`max_turnover_monthly` 实现值一直为 5.0，本文档与 `contracts.py:VerifierConfig` 注释此前误标 0.50，已统一为 5.0 并明确单位「次/月」（= 月均信号翻转次数，G11 口径 ×42）。与另一层日换手硬剔除 `factor_turnover_daily_max=0.45`（P95 校准，拦 top ~5% 天天翻仓极端因子）形成分层：外层拦极端、内层 verifier 对中高换手因子做成本覆盖判定。

## 5. 因子质量评分卡配置

因子质量评分卡配置定义在 `fts/config/factor_quality_card_config.py` 中，通过 `FactorQualityCardConfig` 数据类管理。支持市场专用调整，通过 `get_futures_config()` 获取期货专用配置。

### 5.1 评分维度权重

| 维度 | 默认权重 | 说明 |
|:-----|:---------|:-----|
| IC 有效性 (ic_score) | 1.0 | 信息系数 |
| 收益性 (sharpe_score) | 1.0 | Sharpe Ratio |
| 稳定性 (stability_score) | 0.8 | WalkForward 验证 |
| 鲁棒性 (robustness_score) | 0.8 | 跨品种/压力测试 |
| 容量 (capacity_score) | 0.6 | 市场容量 |
| 交易性 (tradability_score) | 0.8 | 换手率评估 |
| 多样性 (diversity_score) | 0.5 | 因子相关性 |
| 逻辑性 (logic_score) | 0.8 | 经济逻辑评分 |
| 实时性 (timeliness_score) | 0.4 | 衰减程度 |
| 兼容性 (compatibility_score) | 0.4 | 组合兼容性 |

### 5.2 评分映射阈值（可配置）

评分映射函数（`_map_ic_to_score`、`_map_sharpe_to_score` 等）支持从配置读取阈值，默认值如下。可通过 `factor_quality_card_config.py` 的 `to_factor_quality_card_config()` 方法输出完整配置字典。

| 映射函数 | 阈值参数 | 默认值 |
|:---------|:---------|:-------|
| IC 映射 | `ic_high/mid/low` | 0.08/0.03/0.01 |
| ICIR 映射 | `icir_high/mid/low` | 1.0/0.5/0.1 |
| Sharpe 映射 | `sharpe_high/mid/low` | 2.0/1.0/0.0 |
| Calmar 映射 | `calmar_high/mid/low` | 2.0/1.0/0.0 |
| 衰减映射 | `decay_mid` | 0.3 |
| 容量映射 | `capacity_high/mid/low` | 50M/10M/1M |
| 换手率映射 | `turnover_high/mid/low` | 0.3/0.5/0.8 |
| 相关性映射 | `corr_high/mid/low` | 0.3/0.5/0.7 |
| 覆盖度映射 | `coverage_high/mid/low` | 0.8/0.5/0.2 |

### 5.3 分级阈值

| 等级 | 总分阈值 | 说明 |
|:-----|:---------|:-----|
| A 级 | ≥ 35 | 精英因子，直接晋升 |
| B 级 | [25, 35) | 合格因子，可晋升 |
| C 级 | < 25 | 淘汰因子 |

## 6. Budget 配置

| 配置 | L1 默认值 | L2 默认值 | L3 默认值 |
|:-----|:----------|:----------|:----------|
| 单次 token 上限 | 60K（plans/41 D1，v2.104.0+70：50K→60K） | 200K | 100K |
| 月度 token 上限 | 1.5M | 6M | — |
| 最大演化代数 | — | 50 | — |
| 熔断 token 比例 | 2.0x | 2.0x | — |
| 连续低 IC/质量熔断 | 5 次 | 3 代 | — |
| 失败率熔断 | 95% | 90% | — |
| 单结构簇最大精英因子数 | — | 15 | — |

---

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|:-------------|:-----------|:---------|
| `fts/config/settings.py:FTSConfig` | 所有字段有默认值 | `python -c "from fts.config.settings import FTSConfig; assert hasattr(FTSConfig, 'memory_dir')"` |
| `config/settings.yaml` | YAML 可被 `load_config()` 解析 | `python -c "from fts.config.settings import load_config; cfg = load_config('config/settings.yaml')"` |
| `config/futures_universe.yaml` | 品种池/产业链配置可被 `fts.data_futures` 加载且与内置默认等价 | `python -c "import fts.data_futures as df; assert len(df.FUTURES_SUBSET)==82 and len(df.ENERGY_CHAIN_SYMBOLS)==12"` |
| `contracts.py:VerifierConfig` | 默认值与本文档一致 | 手动比对 |
| `fts/factor_engine/symbol_holdout.py:SymbolHoldoutConfig` | `min_train_ic` 默认 0.05 与本文档一致 | 手动比对 |
| `fts/factor_engine/subchain_lifecycle.py:SubchainLifecycleConfig` | `l3.subchain_quality` 配置默认与本文档一致（enabled=False / decay_threshold=0.30 / drop_severe=0.50 / window_days=60 / min_periods=5 / cooldown_days=30） | `python -c "from fts.config.settings import load_config; cfg = load_config('config/settings.yaml'); assert cfg.l3['subchain_quality']['enabled'] is False"`；`python -m pytest tests/factor_engine/test_lifecycle_subchain.py -q`（14 passed，含配置加载用例） |