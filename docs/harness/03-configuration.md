# FTS 配置管理

> 版本: v2.102.0
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
| `elite_dir` | str | `"memory/knowledge/factors/stocks_elite"` | `FTS_ELITE_DIR` | elite 因子存储目录 |
| `default_market` | str | `"futures"` | `FTS_DEFAULT_MARKET` | 默认市场类型 |
| `llm_backend` | str | `""` | `FTS_LLM_BACKEND` | LLM 后端选择（空=自动检测）|
| `evolution_mode` | str | `"hybrid"` | `FTS_EVOLUTION_MODE` | 演化模式: operator(算子主干) / code(代码创新) / hybrid(混合) / batch(批量挖掘漏斗, GAP-I201, v2.65.0) |
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
| `structure_cluster_quota_enabled` | bool | true | `FTS_CLUSTER_QUOTA_ENABLED` | 结构性聚类配额开关：以信号相关性聚类配额替代 max_per_family 家族配额（family 为来源标签非结构维度）；关闭回退 max_per_family 旧逻辑（GAP-077，v2.102.0） |
| `structure_cluster_max` | int | 15 | `FTS_CLUSTER_MAX` | 每结构簇最大因子数：与既有 elite \|corr\| ≥ corr_threshold 的同类成员数达上限拒绝晋升（GAP-077，v2.102.0） |
| `structure_cluster_corr_threshold` | float | 0.85 | `FTS_CLUSTER_CORR_THRESHOLD` | 结构簇"同类"判定相关性阈值（略宽于 GAP-I206 的 0.9：0.9 强拦截 vs 0.85 数量配额）（GAP-077，v2.102.0） |
| `shap_n_extreme` | int | 25 | `FTS_SHAP_N_EXTREME` | SHAP 极端样本数（top+bottom 各 N）：KernelExplainer 每因子评估量 ≈ n_extreme×2×nsamples；GAP-080 降频 50→25（v2.102.0） |
| `shap_n_background` | int | 50 | `FTS_SHAP_N_BACKGROUND` | SHAP KernelExplainer 背景样本数：GAP-080 降频 100→50（v2.102.0） |
| `shap_nsamples` | int | 50 | `FTS_SHAP_NSAMPLES` | SHAP 每极端样本 KernelExplainer 扰动次数（nsamples）：GAP-080 降频 100→50，与 n_extreme 合并 ~4x 评估量下降（v2.102.0） |
| `evolution_success_pattern_enabled` | bool | true | `FTS_SUCCESS_PATTERN_ENABLED` | 成功模式定向演化开关：注入近期成功模式（方法/算子/窗口维度，排除 family）到 MacroEvolver prompt 作 soft 偏向（Phase 1.2 P0-1，26 号计划 §6） |
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
| `meta_loop_interval_hours` | int | 24 | — | L1 Meta-Loop 间隔 |
| `meta_loop_max_tokens` | int | 8000 | — | L1 单次运行 max token |
| `l1_announcement_extractor_enabled` | bool | true | `FTS_L1_ANNOUNCEMENT_EXTRACTOR_ENABLED` | 另类知识源：公告/舆情提取器开关（股票管道，GAP-I103，v2.82.0） |
| `l1_macro_extractor_enabled` | bool | true | `FTS_L1_MACRO_EXTRACTOR_ENABLED` | 另类知识源：宏观事件提取器开关（股票/期货管道，GAP-I103，v2.82.0） |
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
| l3_weight_recompute_cadence | str | "weekly" | FTS_L3_WEIGHT_RECOMPUTE_CADENCE | 权重重算频率：daily=每日重算 / weekly=仅 l3_weight_recompute_weekday 重算（GAP-072，v2.99.0；解绑 L3 与信号管道——信号管道每日生成信号，权重仅在重算日学习，其余日冻结复用快照） |
| l3_weight_recompute_weekday | int | 4 | FTS_L3_WEIGHT_RECOMPUTE_WEEKDAY | 周度重算日（Python weekday 0=周一...4=周五，默认周五收盘后重算；GAP-072，v2.99.0） |
| `adaptive_config.probability_mix` | bool | `true` | —（AdaptiveWeightConfig） | Regime 制度概率混合开关（regime blend，28-T4）：启用且 regime 含 `regime_probs` 时按概率加权混合全部制度倍率；关闭或缺失 probs 时回退硬查表（28 计划） |
| `adaptive_config.confidence_scale` | bool | `true` | —（AdaptiveWeightConfig） | 置信度仓位缩放开关（28-T4）：启用时 Step 2.5 计算 exposure_scale 供 build_combo 整体缩放；关闭时恒 1.0（28 计划） |
| `adaptive_config.confidence_scale_min` | float | `0.3` | —（AdaptiveWeightConfig） | 熵标定后 exposure_scale 缩放下限（28-T4）：`scaled = confidence × (1 − entropy_penalty × H_norm)` 后 clip 到 [scale_min, 1.0]（28 计划） |
| `adaptive_config.confidence_entropy_penalty` | float | `0.5` | —（AdaptiveWeightConfig） | 熵标定惩罚系数（28-T4）：后验熵越大置信度折扣越大（均匀分布最大折扣）（28 计划） |
| `adaptive_config.smoother.de_risk_alpha` | float | `0.8` | —（AdaptiveWeightConfig.smoother） | RegimeSmoother 风控切换（进入降风险制度）不对称系数（28-T6）：切换更激进平滑（28 计划） |
| `adaptive_config.smoother.re_risk_alpha` | float | `0.1` | —（AdaptiveWeightConfig.smoother） | RegimeSmoother 恢复切换（离开降风险制度）不对称系数（28-T6）：恢复更保守平滑（28 计划） |

**股票信号管道脚本参数（`scripts/daily_signal_pipeline.py`，GAP-076，v2.101.0）**：

| 参数 | 取值 | 默认 | 说明 |
|:-----|:-----|:-----|:-----|
| `--normalize` | `none` / `zscore` / `rank` | `none` | 因子信号截面标准化方式：none=原始信号 / zscore=每交易日截面 z 分数（ddof=0）/ rank=每交易日截面百分位秩映射到 [-1,1]。重算日写入权重快照 `normalize` 字段，冻结日读取同值应用保证口径一致（旧快照缺省 none 向后兼容） |
| `--force-recompute` | — | 关 | 强制重算 Ridge 权重并更新快照（GAP-072，默认按 l3_weight_recompute_cadence 自动判定） |
| `portfolio_max_factors` | int | 20 | — | L3 组合最大因子数 |
| `portfolio_top_n` | int | 5 | — | L3 Top N 输出 |
| `portfolio_decay_days` | int | 90 | — | L3 衰减检验窗口 |
| `portfolio_optimizer_mode` | str | `"risk_parity"` | `FTS_PORTFOLIO_OPTIMIZER_MODE` | L3 optimizer 模式目标（`risk_parity`/`mvo`，GAP-I302，v2.74.0） |
| `log_level` | str | `"INFO"` | `FTS_LOG_LEVEL` | 日志级别 |
| `log_file` | str | `""` | `FTS_LOG_FILE` | 日志文件路径 |
| `stock_neutralization` | bool | `true` | `FTS_STOCK_NEUTRALIZATION` | 股票因子横截面评估是否做行业/市值中性化（v2.57.0） |
| `industry_map_path` | str | `"data/industry_map.json"` | `FTS_INDUSTRY_MAP_PATH` | 行业映射文件路径（JSON，`{symbol: industry_name}`，v2.57.0） |
| `cap_map_path` | str | `""` | `FTS_CAP_MAP_PATH` | 市值映射文件路径（JSON，`{symbol: market_cap}`，可选，v2.57.0） |
| `stock_signal_neutralize` | str | `"none"` | `FTS_STOCK_SIGNAL_NEUTRALIZE` | 股票信号管道截面中性化方式（`none`/`industry`/`size`/`both`，D.2，v2.101.0） |
| `stock_signal_l3_mode` | str | `"ridge"` | `FTS_STOCK_SIGNAL_L3_MODE` | 股票信号管道 L3 权重学习模式（`ridge`/`elastic_net`，D.2，v2.101.0） |
| `stock_signal_regime` | str | `"none"` | `FTS_STOCK_SIGNAL_REGIME` | 股票信号管道 Regime 自适应权重（`none`/`auto`，D.2 偏差 b，v2.101.0） |
| `futures_adjusted` | bool | `true` | `FTS_FUTURES_ADJUSTED` | 期货连续合约 K 线是否默认返回换月复权序列（因子计算用，v2.58.0） |
| `roll_cost_bps` | float | `2.0` | `FTS_ROLL_COST_BPS` | 展期成本系数（基点/次，回测持仓穿越换月日扣除，v2.58.0） |
| `eval_horizons` | tuple[int,...] | `(1,5,10,20)` | `FTS_EVAL_HORIZONS` | 多持有期 IC 体系：横截面/时序评估的多持有期列表（空=关闭，v2.90.0，GAP-060） |
| `cost_sensitivity_enabled` | bool | `false` | `FTS_COST_SENSITIVITY_ENABLED` | 可交易性压力层：评估链是否输出成本敏感性扫描（滑点 1/2/4/8 倍，v2.97.0，GAP-061） |
| `inject_overnight_gap_enabled` | bool | `false` | `FTS_INJECT_OVERNIGHT_GAP` | 夜盘/隔夜跳空标记：`get_ohlcv` 是否注入 overnight_gap/overnight_gap_flag 列（v2.97.0，GAP-066） |
| `overnight_gap_flag_threshold` | float | `0.01` | `FTS_OVERNIGHT_GAP_THRESHOLD` | 隔夜跳空标记阈值：\|overnight_gap\| ≥ 该值 flag=1（v2.97.0，GAP-066） |
| `futures_neutralization` | bool | `true` | `FTS_FUTURES_NEUTRALIZATION` | 期货横截面因子评估是否做板块/产业链中性化（GAP-F03，v2.59.0） |
| `futures_enhance_enabled` | bool | `false` | `FTS_FUTURES_ENHANCE_ENABLED` | 字段增强层 iFinD SDK 选项（GAP-083 阶段 C，v2.101.0）：`false` 时仅默认注册天勤 TQSDKEnhanceSource（close_oi→hold/oi_change，零额外依赖）；`true` 时追加 IFindSDKSource（方案 A：iFinD 官方 SDK 直连补 settle/pre_settle 权威值，需本地安装 iFinDPy + .env 凭据 IFIND_TOKEN 或 IFIND_USERNAME/PASSWORD，失败自动降级） |
| `backtest_trade_filter` | bool | `true` | `FTS_BACKTEST_TRADE_FILTER` | 回测是否启用涨跌停拦截 + 停牌过滤（GAP-F02，v2.59.0） |
| `futures_limit_pct` | float | `0.08` | `FTS_FUTURES_LIMIT_PCT` | 期货涨跌停判定阈值（单日涨跌幅 ≥ 该值视为涨跌停，GAP-F02，v2.59.0） |
| `force_walkforward` | bool | `true` | `FTS_FORCE_WALKFORWARD` | 因子晋升路径是否强制 WalkForward 冷启动样本外验证（GAP-F08，v2.60.0） |
| `margin_rate_map` | dict | 见默认表 | —（YAML） | 品种保证金率表（{symbol: 保证金率}，未配置品种用默认 0.10，GAP-F09，v2.60.0） |
| `max_margin_usage` | float | `0.80` | `FTS_MAX_MARGIN_USAGE` | 最大保证金占用率（保证金占用/总权益，超过触发强平风险告警，GAP-F09，v2.60.0） |
| `mcp_enabled` | bool | `false` | `FTS_MCP_ENABLED` | 是否启用 Wind/iFinD MCP 增强字段（启用时若未注入 MCP 客户端抛 RuntimeError 显式报错，未启用则明确降级跳过增强字段，GAP-F04，v2.60.0） |
| `duckdb_single_writer` | bool | `true` | `FTS_DUCKDB_SINGLE_WRITER` | 是否启用 DuckDB 单写者模式（所有写收敛唯一 writer，false 回退旧多路径，GAP-056，v2.86.0） |
| `duckdb_read_pool_size` | int | `4` | `FTS_DUCKDB_READ_POOL_SIZE` | DuckDB 读连接池大小（读操作与单写者解耦，互不阻塞，GAP-056，v2.86.0） |
| `duckdb_batch_size` | int | `1000` | `FTS_DUCKDB_BATCH_SIZE` | DuckDB 批量写入缓冲行数（批量 COPY 降低 commit 频率，GAP-056，v2.86.0） |
| `duckdb_commit_every` | int | `100` | `FTS_DUCKDB_COMMIT_EVERY` | DuckDB 批量写入 commit 周期（秒，GAP-056，v2.86.0） |

## 3. YAML 配置文件

`config/settings.yaml` 示例：

```yaml
default_market: "futures"
llm_backend: "openai"
max_generations: 10
micro_trials_per_generation: 50
portfolio_max_factors: 20
evolution_mode: "hybrid"   # operator(算子主干) / code(代码创新) / hybrid(混合) / batch(批量挖掘漏斗, GAP-I201)
batch_size: 20             # batch 模式每代候选生成数
batch_max_candidates: 5    # batch 模式进入细评估的最大候选数
batch_max_workers: 4       # batch 粗筛并行线程数
batch_random_seed: 42      # batch 随机种子
```

> **evolution_mode 说明（Phase C.2 / GAP-I201）**：取值 `operator`（算子主干）/ `code`（代码创新）/ `hybrid`（混合）/ `batch`（批量挖掘漏斗），默认 `hybrid`，支持环境变量 `FTS_EVOLUTION_MODE` 覆盖。`batch` 模式（v2.65.0）每代对同一父因子批量生成 `batch_size` 个后代（macro 至多 1 次 + GP/operator 交替 + seed 递增），ThreadPoolExecutor 并行粗筛后按预筛 IC 排序截断 `batch_max_candidates` 个进入细评估准入链；token 护栏（每代至多 1 次 LLM）与既有熔断协同。

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
| `max_turnover_monthly` | 0.50 | 最大月度换手率 |

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
| 单次 token 上限 | 50K | 200K | 100K |
| 月度 token 上限 | 1.5M | 6M | — |
| 最大演化代数 | — | 50 | — |
| 熔断 token 比例 | 2.0x | 2.0x | — |
| 连续低 IC/质量熔断 | 5 次 | 3 代 | — |
| 失败率熔断 | 95% | 90% | — |
| 单一家族最大精英因子数 | — | 3 | — |

---

## 一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|:-------------|:-----------|:---------|
| `fts/config/settings.py:FTSConfig` | 所有字段有默认值 | `python -c "from fts.config.settings import FTSConfig; assert hasattr(FTSConfig, 'memory_dir')"` |
| `fts/config/settings.py:load_industry_map` | 行业映射 JSON 可被加载（非 dict 根/文件缺失返回空 dict，空白键过滤） | `python -c "from fts.config.settings import load_industry_map; m = load_industry_map('data/industry_map.json'); assert len(m) > 0"` |
| `config/settings.yaml` | YAML 可被 `load_config()` 解析 | `python -c "from fts.config.settings import load_config; cfg = load_config('config/settings.yaml')"` |
| `contracts.py:VerifierConfig` | 默认值与本文档一致 | 手动比对 |