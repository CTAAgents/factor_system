# FTS 开发生命周期

> 版本: v2.104.0+4
> 最后更新: 2026-08-09

---

## 1. 阶段划分

FTS 从 FDT 剥离共经历 16 个 Phase，目前全部完成：

| 阶段 | 内容 | 状态 | 产出物 |
|:-----|:-----|:-----|:-------|
| **Phase 1** | FTS 核心契约 + 因子引擎骨架 + Data-Core 集成验证 | ✅ 完成 | 因子引擎框架 |
| **Phase 2** | 因子引擎完整实现（三层循环 + 种子池数据类型感知） | ✅ 完成 | 可用的因子进化引擎 |
| **Phase 3** | 数据处理管线（pipeline 抽象基类 + factor_combiner） | ✅ 完成 | 衍生数据管线骨架 |
| **Phase 4** | 多因子策略 + CLI + 调度 | ✅ 完成 | 完整可运行系统 |
| **Phase 5** | 测试覆盖 + pylint 清理 + FDT 侧清理 | ✅ 完成 | 交付就绪 |
| **Phase 6** | Memory 重定向：FTS 独立 memory 路径，FDT_PATH 环境变量解耦 | ✅ 完成 | 独立持久化 |
| **Phase 7** | FTS 层级提升为独立项目，claude.md / fts-coding.mdc 割离 | ✅ 完成 | 完整项目独立 |
| **Phase 8** | 种子因子集成：世坤 101 因子 + Qlib 158 因子加入种子池，seed_data 目录统一管理外部因子，支持 include_external 参数控制加载 | ✅ 完成 | 268 种子因子（9 内置 + 259 外部），1325 测试全绿 |
| **Phase 9** | 国泰君安 191 因子集成 + 全量工程测试：全部模块边缘路径覆盖，46/47 模块 100% 覆盖率 | ✅ 完成 | 459 种子因子（9 内置 + 450 外部），1431 测试全绿，仅余 1 空白行 |
| **Phase 10** | 基本面/另类/宏观因子集成：23 个基本面种子因子加入种子池，新增 FundamentalProvider 数据层 | ✅ 完成 | 482 种子因子（9 内置 + 473 外部），1502 测试全绿 |
| **Phase 11** | 期货自治循环：L1/L2/L3 全自动调度 + 期货基本面数据接入（库存/仓单/基差）+ 信号管道定时任务 + 期货全量种子因子库（12 大因子家族 50+ 子因子）+ 顶级因子过滤（IC>0.3）+ 信号管道输出到 reports/{date}/ | ✅ 完成 | 482 种子因子（9 内置 + 101 世坤 + 158 Qlib + 191 国泰君安 + 23 基本面），期货 12 家族 50+ 子因子，8 个定时任务，信号报告输出到 reports/ |
| **Phase 12** | 策略进化：动态因子权重（DynamicWeightStrategy）、市场制度自适应（RegimeAdaptiveStrategy）、多周期信号融合（MultiPeriodSignalFusion） | ✅ 完成 | 3 种策略进化能力，55 个测试用例全绿，strategy_evolution.py 95% 覆盖率。**28 计划深化（2026-08-11）**：Regime 机构级优化——全制度概率分布 regime_probs 输出、制度概率混合权重（regime blend，`mult = Σ p_i × table_i`）、置信度熵标定仓位缩放（exposure_scale）、不对称 de-risk/re-risk 切换、BIC 状态数选择 + 特征标准化、制度有效性样本外验证（详见 plans/28-*） |
| **Phase 13** | 信号管道 v5 多空双向 + 信号增量：信号管道升级为多空双向排名（按绝对值排序），新增信号增量追踪（较昨日变化判断趋势加速/衰竭），信号快照 JSON 持久化 + JSONL 历史追加，L3 Portfolio Loop 自动触发信号管道（全量 82 品种），README 拆分股票/期货种子因子（v2.101.0 GAP-076 股票信号管道增强：--normalize 截面标准化 zscore/rank + 权重快照 normalize 字段 + volatility_reversion_g2 卷积修复；股票信号管道已随股票管线剥离至 fts-stock，2026-08） | ✅ 完成 | 1601 测试全绿，12 大期货因子家族 50+ 子因子 |
| **Phase 14** | Design 全量落地（v2.9.0）：9 个设计文档（A.1-C.3）全部完成——S1 数据层（质量评分/状态历史/审计报告 3 表 + 3 仓储类）、S2 监控调度（Prometheus 指标注册表 + 自适应权重封装 + 数据质量三维指标 + 月度衰减/数据质量 2 任务）、S3 回测流水线（7 阶段类 + run_batch + Builder + CLI）、S4 C.1 CLI（feature list/analyze + gp evolve）、S5 C.2 实盘对接（信号契约 + fts/risk 风控包 + LiveFactorMonitor + HTTP 端点）、S6 C.3 反馈闭环（FeedbackLoop 家族 + 4 反馈表 + CLI）；新增 79 测试用例 | ✅ 完成 | 2066+ 测试，9 设计全部实现（详见 docs/harness/design/） |
| **Phase 15** | 算子演化引擎（v2.10.0，Phase 3+ / C.4）：`OperatorEvolutionEngine` 在 DSL 算子空间（58 算子 L0-L5）做适应度导向进化搜索（种群初始化 validator 校验 → IC+Sharpe 评估（DSL executor + 缓存）→ 锦标赛选择 → 子树交叉/变异（参数受 param_bounds 约束）→ 精英保留），取代 `_generate_operator_factor` 纯随机组合；evolution_loop operator/hybrid 模式接入（无评估数据回退随机生成）；产物为 `kind=OPERATOR` 因子；关闭 GAP-026 | ✅ 完成 | 算子演化引擎 + 13 测试用例（引擎 11 + 集成 2），C.4 设计落地（详见 docs/harness/design/C.4） |
| **Phase 16** | 组合漂移治理（v2.11.0）：L3 组合漂移监控（DriftMonitor 成员重合率 + 权重 L1 变化率 → drift_history/YYYY-MM-DD.json）+ PortfolioManager combo_history 归档 + build_combo 粘性约束（±30% 变动 / 新因子首日封顶）+ L2 影子池（新晋升因子观察 5 个交易日，种子因子直接进正式组合）；新增 20 测试用例 | ✅ 完成 | 82 个 portfolio_loop 测试全绿，漂移数据持久化 memory/portfolio/drift_history/ |
| **Phase 17** | 孤立模块集成 Phase 2（v2.16.0）：`LogicMonitor`/`FactorInspector` 注册为定时任务（每日 22:00/03:00）；`ProcessWatchdog` 集成到 `SchedulerEngine`（`start_watchdog()` 方法）；任务注册表增至 8 个任务 | ✅ 完成 | 2043 回归测试通过，8 个定时任务（L1/L2/L3 + 健康检查 + 月度衰减 + 数据质量 + 逻辑监控 + 因子巡检） |
| **Phase 18** | 因子淘汰主流程集成（v2.17.0）：`FactorRepository.retire_factor()` 实现 DuckDB 状态更新 + JSON 文件迁移至 `_retired/` + 状态变迁记录；`monthly_decay_eval_job` 调用 `retire_factor()` 同步淘汰到主存储；修复 `update_factor`/`update_factor_status` DuckDB ART 索引 bug（DROP → UPDATE → 重建索引） | ✅ 完成 | 因子淘汰正式成为主流程环节，退化因子自动从活跃池移除 |
| **Phase 22** | Elastic Net 信号合成 + ACTIVE_FACTOR_CAP（v2.35.0）：L3 组合构建默认信号合成模式从 equal_weight 切换为 elastic_net（Elastic Net 截面回归，L1+L2 自动变量选择，防止冗余因子稀释组合夏普）；新增 ACTIVE_FACTOR_CAP=20 活跃因子数量上限，因子数超过上限时按 Sharpe 排名保留 Top N，自动过滤低质量因子；期货 CLI 默认 synthesis_mode 同步切换为 elastic_net；109 相关测试全绿，无回归 | ✅ 完成 | 2102+ 回归测试通过，portfolio_loop 90 测试全绿，Elastic Net 自动变量选择 + ACTIVE_FACTOR_CAP=20 因子筛选 |
| **Phase 23** | P1 因子聚类 + P2 PCA 降维（v2.36.0）：新增 `fts/factor_engine/factor_clustering.py` 模块，`FactorClusteringEngine` 实现信号相关性层次聚类 + 代表因子选择（Pearson 相关系数 → 层次聚类 → Sharpe 最高代表），`PCASignalCompressor` 实现 PCA 信号降维压缩（z-score 标准化 → PCA 保留 95% 方差 → 载荷矩阵映射因子权重）；集成到 L3 PortfolioLoop 的 Step 1.8（P1 聚类）和 Step 1.9（P2 PCA，可选）；关闭 GAP-034 和 GAP-035 | ✅ 完成 | 因子聚类模块全量测试通过，portfolio_loop 集成测试通过，P1/P2 可独立控制 |
| **Phase 24** | ML 模型集成层（v2.38.0）：新增 `fts/ml/` 包，封装 LightGBM/XGBoost/Ensemble 三种模型，支持横截面回归/时序预测/集成融合三种训练模式；L3 信号合成新增 `ml_ensemble` 模式，通过可选依赖 [`ml`] extra 控制；新增 [ml] 可选依赖声明 | ✅ 完成 | fts/ml/ 包全量测试通过，L3 ml_ensemble 模式集成测试通过 |
| **Phase 25** | VNPY 信号桥接层（v2.38.0）：新增 `fts/bridge/` 包，SignalBridge 实现 JSON/Redis/REST 三种协议的交易信号格式转换；`fts bridge` CLI 子命令支持 serve/status 操作；新增 [bridge] 可选依赖声明 | ✅ 完成 | fts/bridge/ 包全量测试通过，CLI bridge 子命令集成测试通过 |
| **Phase 26** | 因子库来源子家族拆分（v2.40.0）：`FactorFamily` 新增 qlib/gtja/wq101 三个标准家族（14→17 大类）；`_infer_factor_family` / `_infer_family_from_filename` 按名称前缀/文件名映射；qlib158/gtja191 YAML family 字段对齐；DuckDB 一次性迁移 111 条 cross_section 记录（qlib 43 / gtja 36 / wq101 30 / fut_gp→behavioral 2） | ✅ 完成 | cross_section 家族拆分为 qlib/gtja/wq101，新增 12 测试用例全绿 |
| **Phase 27** | 高IC因子筛查剔除（v2.49.0，Phase B.4）：新增 `fts/factor_engine/high_ic_screener.py`，将「高IC因子筛选打分表」（docs/Knowledge/高IC因子筛选打分表.xlsx）固化为自动筛查流程——16 项检查 × 6 大模块（基础指标/过拟合/冗余风格/落地性/尾部风险/综合稳定性）总分归一化 100 分 + 5 项一票否决（外样本衰减>30%/极值扰动>25%/存量相关>0.7/成本后超额≤0/无业务逻辑）任意触发直接 C 级剔除 + A/B/C/PASS 四级评级（A≥85 入库、B 60~84 暂缓优化、C<60 剔除、PASS 数据不足放行）；集成到 `_promote_to_elite` 入库质检强制 Gate，**期货全品种统一启用**，筛查报告写入 elite 快照 `high_ic_screen` 字段 | ✅ 完成 | 25 个高IC筛查测试全绿，promote/elite 集成测试 16 通过无回归，详见 docs/harness/design/B.4-high-ic-screening-design.md |
| **Phase 28** | 种子因子质检全链对齐 + vwap 通用 IC 门槛（v2.50.0）：① `_evaluate_and_promote_seeds` 补齐与演化因子完全同强度的质检链——新增 Verifier 判定、消融实验、因果结构审查、鲁棒性审查、SHAP 可解释性分析（原种子路径仅质量卡+回测+数据质量+6项审计），L1 注入候选与人工精选种子一视同仁，任一关卡失败即拒绝晋升；② `evaluation_chain.evaluate()` 失败原因汇总新增 vwap 近似因子通用 IC 门槛（code 含 `vwap` 且 abs(IC)<0.08 判失败），审计层统一覆盖种子+演化全路径（原仅种子 loader 打标 `risk_tag` 生效、演化生成器不打标漏检）；③ 种子全链质检测试补强（4 用例） | ✅ 完成 | 新增 7 测试（evaluation_chain vwap 门槛 3 + 种子全链质检 4），相关测试全绿无回归 |
| **Phase 29** | 质检拦截器判定缺陷修复（v2.50.0）：① 消融实验判定语义修正——`shuffle_dates`（时间戳打乱）/成交量置零/VWAP 替换与核心价格列（open/high/low/close/vwap/settle）置零改为「信息型」判定（时序因子依赖时序因果、价格因子依赖价格列属必要特征，不再误判伪相关），仅「非价格列」置零导致 IC 降幅 >50% 才拦截；根因：L2 期货演化 15 代中 5 个通过 Verifier 的候选（IC 0.31~0.52）全部被消融实验（2 次）与鲁棒性缺失值测试（3 次）误杀，失败率 100% 熔断；② `_compute_ic` NaN 掩码兜底——spearmanr/pearsonr 计算前剔除 NaN 对，缺失值鲁棒性测试注入 NaN 后 IC 不再恒为 0；③ `SingleAblation` 新增 `feature` 字段记录置零列；关闭 GAP-043 | ✅ 完成 | 新增/更新 ~18 测试用例，tests/factor_engine/ 回归无新增失败，L2 演化重跑解除熔断 |
| **Phase 30** | **鲁棒性缺失值测试阈值放宽（v2.52.0）：** `robustness.RobustnessTester` 默认 `missing_retention_threshold` 从 0.80 降至 0.50（与 OOD 测试对齐）。根因：L2 期货演化 12 个种子因子全部被鲁棒性缺失值测试拦截——随机单元格级 NaN 注入比真实数据质量问题激进得多（5% 随机 NaN 即使高质量种子 IC=0.49 的保持率也降至 0.56），父因子池为空导致后续 GP 演化全部退化（11 个常数信号因子）、总失败率 100% 熔断。0.50 阈值合理：OOD 测试已用 0.50 阈值，真实数据缺失通常是整列缺失而非单元格随机。关闭 GAP-044 | ✅ 完成 | 鲁棒性缺失值保持率阈值 0.80→0.50，无新增测试，L2 期货演化预期解除熔断 |
| **Phase 19** | 因子家族多样性约束（v2.18.0）：`_promote_to_elite` 新增家族数量检查（`max_per_family=3`），限制单一家族因子过度繁殖；`BudgetConfig` 新增 `max_per_family` 字段；配置文档同步更新 | ✅ 完成 | L2 演化晋升受家族多样性约束，fut_bias 等家族从 8+ 个降至 ≤3 个 |
| **Phase 20** | 分钟级回测 Phase 1（v2.30.0）：三源分钟数据源适配（通达信 TDX HTTP + TQ-Local + 天勤 TQSDK），DuckDB minute_cache 缓存，聚合器扩展支持分钟级数据路径，回测引擎增加 frequency 参数（年化因子/窗口/成本自适应），CLI 增加 --frequency 参数 | ✅ 完成 | 分钟级回测可运行，支持 1m/5m/15m/30m/60m/daily 频率切换 |
| **Phase 21** | 宏观字段增强层（v2.32.0）：`IFindSource.get_macro_series()` 实现 edb_cache 缓存读写（查 → miss 拉取 → 幂等写回）；新增 `fts/data_sources/macro_aligner.py`（`MacroFieldAligner.align()` 月度→交易日 ffill + 发布滞后防未来函数 + `inject_macro_fields()` 批量注入）；`BacktestPipeline._compute_factor()` 因子执行前注入宏观列（export/import_data/cpi/rate/us_bond），宏观因子不再走 close 代理降级 | ⏳ 进行中 | 宏观因子可读取真实 EDB 数据，缓存 + 对齐 + 注入全链路可用 |
| **Phase 34** | 股票因子行业/市值中性化主流程（v2.61.0，GAP-S01；已随股票管线剥离至 fts-stock，2026-08）：`EvolutionLoop(market="stock")` 自动加载 `industry_map.json` + `cap_map`（`stock_neutralization` 默认 true，接通死配置），键归一化（`.SH/.SZ` 后缀 → 裸代码兼容面板 symbol），透传 `cross_section_evaluate_backtest` 做行业去均值 + 市值加权去均值，报告输出中性化前后 IC 对比 | ✅ 完成 | 股票截面因子评估剥离行业/市值系统性偏差，消除伪预测力 |
| **Phase 33** | Adaptive 权重完整接入 L3（v2.56.0，Phase 33 / GAP-045）：① `synthesis_mode` 扩展 `adaptive`，Step 2 委托 `PortfolioConstructor`（回测/生产统一入口）；② `RegimeSmoother(alpha=0.5, min_days=2)` 接入 Step 2.5（Regime 切换权重指数平滑，参数走 `AdaptiveWeightConfig`）；③ 实现原设计 A.3 未落地的 FactorStyle/style_tags 维度——`FactorStyle` 枚举（contracts.py）+ `factor_catalog.style_tags` 列（DuckDB 兼容补列）+ `REGIME_STYLE_MULTIPLIERS` 双维度调整（family×style 乘积 clamp [0.5,1.5]×base） | ⏳ 进行中 | 详见 plans/19-adaptive-weight-l3-integration.md |
| **Phase 35** | Barra 风格因子体系（v2.62.0，GAP-S02）：`fts/factor_engine/barra/` 新包三文件——`barra_style.py`（`BarraStyleEngine` 10 风格暴露计算引擎：size/beta/momentum/residual_vol/nonlinear_size/book_to_price/liquidity/earnings_yield/growth/leverage，逐日截面 rank→z-score 标准化，nonlinear_size 基于 size 暴露矩阵逐日 z³ 对 z 回归残差，字段缺失全 NaN 降级）+ `barra_neutralizer.py`（`barra_neutralize_matrix` 逐日 OLS 风格暴露 + 行业虚拟变量回归取残差，样本不足降级去均值、常数列剔除、正交性保证）；`cross_section_evaluate_backtest` 新增 `style_exposures` 参数 + Step 2.6 Barra 风格中性化（行业去均值后叠加风格回归残差，两级中性化链 GAP-S01/S02） | ✅ 完成 | 13 测试用例全绿（test_barra.py），期货因子横截面评估剥离 10 大风格系统性偏差（Barra 引擎现保留用于期货风格中性化，GAP-I304 `l2_barra_style_neutral` 默认开启；原股票侧应用已随管线剥离至 fts-stock，2026-08），回答"因子赚风格钱还是 alpha 钱" |
| **Phase 36** | A 股行业轮动 + 风格轮动 Regime 检测（v2.65.0，GAP-S03；已随股票管线剥离至 fts-stock，2026-08）：新增 `fts/factor_engine/stock_regime.py`（`StockRegimeSelector`——行业动量横截面离散度 + top-N 集中度 → concentrated/rotating/balanced 三态；大小盘/成长价值比值动量 → large_cap/small_cap + growth/value 双态；复用 `regime_hmm.MultiHorizonHMMDetector` 多周期集成校正置信度，规则动量方向主判定，空面板/样本不足优雅降级）；`REGIME_STYLE_MULTIPLIERS` 新增 6 个股票风格键（large_cap/small_cap/growth/value/sector_concentrated/sector_rotating）；`PortfolioLoop.run(stock_regime=...)` market="stock" 时 Step 2.5 优先驱动风格自适应权重 | ✅ 完成 | 19 测试用例全绿（test_stock_regime.py，含风格切换样本正确率 ≥80%），L3 风格自适应权重在股票场景正式可用 |
| **Phase 37** | 批量挖掘漏斗（v2.65.0，GAP-I201 Stage 1 首版）：新增 `fts/factor_engine/batch_mining.py`（`BatchMiner` 批量生成 → ThreadPoolExecutor 并行粗筛 → 按预筛 IC 排序截断 ≤ max_candidates，依赖注入回调零业务耦合）；`evolution_loop.py` 抽取 `_evolve_one`/`_process_candidate` 公共方法（batch 与单因子路径共用准入链）+ 新增 `_run_batch_generation`（同父多后代：macro 至多 1 次 + GP/operator 交替 + seed 递增，token 护栏，全失败回退）；`_quick_prefilter` 返回 (ok, reason, ic) 三元组；`evolution_mode="batch"` 生效 | ✅ 完成 | 21 新增测试用例全绿（test_batch_mining 11 + batch 集成 10），GAP-I201 关闭，单夜候选吞吐 ≥10× |
| **Phase 39** | 机构级权重学习增强（v2.75.0，GAP-053）：`fts/factor_engine/weight_learning.py` 为 elastic_net 补齐三层机构级处理——① 风险调整权重（`FactorReturnsBuilder` 构建因子收益 → `RiskModelEstimator` Ledoit-Wolf 收缩协方差 → `volatility_scaling`（w∝\|coef\|/σ）或 `risk_parity`（等风险贡献，对角退化为逆波动率））；② 滚动样本外验证（滚动窗口 re-fit，报告权重稳定性 / OOS 组合 IC / 权重衰减）；③ 学习面板按目标交易市场自动匹配（`resolve_panel_market` panel_market="auto"）+ 跨市场迁移 IC 对比 | ✅ 完成 | 28 新增测试用例全绿（test_weight_learning.py），既有 elastic_net 回归 19 passed，组合权重体现"每单位风险的信号贡献" |
| **Phase 40** | DuckDB 并发模型根治（v2.86.0，GAP-056，design/E.1）：`fts/data_futures.py` 新增 `DuckDBWriter`（单写者 + 进程内写锁 + executemany/copy_from_records 显式 BEGIN/COMMIT 整批原子）与 `DuckDBReader`（读连接池，MVCC 快照读写互不阻塞）；`_get_db()` 拆分为 `_get_writer()`/`_get_reader()` + `_release_reader()` 并迁移 3 个调用点；`FTSConfig` 新增 4 并发配置项；调度器写 job 经单写者天然串行 | ✅ 完成 | 20 新增测试用例全绿（writer 10 + reader 5 + config 5），8 线程并发写零冲突，GAP-056 关闭 |
| **Phase 41** | 兜底家族 'other'/'unknown' 多样性上限永久豁免（v2.98.0，GAP-070）：`_promote_to_elite` 家族多样性检查（max_per_family 缺省 15）对兜底家族 `'other'`/`'unknown'` 跳过数量上限拦截——它们是"无法归类"的回收站家族，对其设限等价于对整个演化新因子晋升通道设总量上限；逻辑同质化保护由 L2 准入去冗余（GAP-I206）承担，'other' 上限属重复约束 | ✅ 完成 | TestGapF16PromoteToElite 9→11 用例全绿（other/unknown 达上限仍晋升 + 快照落盘，trend 家族拦截不变），GAP-070 关闭 |
| **Phase 43** | 演化候选短样本 OOS 审计根治（v2.98.0，GAP-073）：① `audit.py` `_check_oos_consistency` 对 WalkForward `n_windows_completed < 2` 标记 skipped（单窗口无法做跨窗口一致性验证，与数据缺失项对齐），L1 兜底无窗口键保持原逻辑；② `cli.py` 期货横截面演化 `days=500→700`（约 2.8 年）使 WalkForward 完整产出 4 窗口（探针验证；勿超 750 行否则落入 3 年默认分支产出 0 窗口） | ✅ 完成 | test_audit.py 28→32 全绿（单窗口 skipped/双窗口正常评估/L1 兜底不变），GAP-073 关闭 |
| **Phase 42** | L3 与信号管道解耦 + 权重每周重算（v2.99.0，GAP-072；v2.101.0 时间与 TRAE Schedule 对齐）：调度解绑——l3_portfolio_loop 期货每周五 19:00、l3_portfolio_loop_stock 每周五 19:30（v2.101.0 由 20:00/08:30 对齐 TRAE Schedule；l3_portfolio_loop_stock 与 daily_signal_pipeline 已随股票管线剥离至 fts-stock，2026-08），新增独立每日任务 futures_signal_pipeline（工作日 20:00）与 daily_signal_pipeline（工作日 08:45，已剥离），两个 L3 job 移除信号管道联动触发；FTSConfig.l3_weight_recompute_cadence/l3_weight_recompute_weekday + is_weight_recompute_day()；PortfolioLoop.run(recompute_weights=None) 冻结日 status="frozen" 不重建组合（冷启动保护）；信号管道 Ridge 权重周五重算存快照、其余日冻结复用仅刷新因子值（save/load_weight_snapshot + filter_factors_by_weights）；CLI --force-recompute | ✅ 完成 | 新增 test_weight_recompute 5 + TestWeightSnapshot 4 + portfolio_loop 冻结/强制/冷启动 3 用例，受影响 375+243 passed 全绿，GAP-072 关闭 |
| **Phase 38** | L3 定时任务显式期货路径（v2.73.0，调度一致性修复）：`fts/scheduler/jobs.py` `l3_portfolio_loop_job` 显式传 `elite_dir=cfg.futures_elite_dir` + `market="futures"`，与 CLI `portfolio run --universe futures` 对齐（此前误用股票 elite 目录，与下游期货信号管道不一致）；新增 `test_uses_futures_path` 断言（test_jobs.py 29→30 用例） | ✅ 完成 | 调度全量 152 passed，自动化任务组合构建与期货信号输出口径对齐 |
| **Phase 45** | 数据持久化与存储架构渐进式收敛 P0+P1+P2（v2.101.0，plans/29，GAP-090）：P0 新建 `fts/store/` 存储域注册表（`StorageRegistry`/`StorageDomain`/`StorageBackend` + `storage_landscape.yaml` 契约加载与校验，13 域）+ tests/store 13 用例；P1 因子资产入库——`scripts/migrate_elite_json_to_catalog.py`（差量补齐+逐字段校验+dry-run/verify-only/`--sync`）：stock 补齐 389（股票因子资产已随管线剥离至 fts-stock，2026-08）/ futures 补齐 139，**差量缺失归零、778 因子 0 不一致**；写路径反转（先 DuckDB 后 JSON 快照）；`add_evaluation` 新增 `update_catalog_status` 参数；tests/scripts 17 用例；P2 运行状态入库——`fts/store/state_db.py` StateKVStore（`state_kv` 当前状态表 + `state_history` 历史追加表双表模型）+ `scripts/migrate_state_to_duckdb.py`（权威状态 glob 规则入库 + 过程痕迹 tar.gz 归档复制语义）：**231 权威状态条目入库、读回对账 231/231 一致**、2307 过程痕迹归档 `data/archive/state_traces_*.tar.gz`、`snapshot()` 支持无 state.json 冷启动；tests/store 11 + tests/scripts 8 用例；P3~P4 按序推进 | ✅ 完成（P0+P1+P2） | fts/store/ + migrate 脚本 + storage_landscape.yaml + data/state.duckdb（SSOT：DuckDB 全量权威，JSON 只读快照；详见 plans/29-storage-convergence-plan.md） |
| **Phase 44** | 市场目录隔离（v2.101.0，输出按市场分目录）：所有任务输出（报告/日志/状态/权重快照/动态池缓存）按市场隔离——期货 → `reports/futures/{date}/`、`memory/logs/{task}/futures/`、`memory/{meta_loop,evolution,portfolio}/futures/`；股票 → `reports/stock/{date}/`、`memory/logs/{task}/stock/`、`memory/{meta_loop,evolution,portfolio}/stock/`（股票输出目录已随股票管线剥离至 fts-stock，2026-08）；信号管道权重快照 `futures_signal_weights.json`/`stock_signal_weights.json`（`stock_*` 已剥离）分目录落盘；动态池缓存 → `memory/portfolio/futures/futures_dynamic_pool.json`；跨市场任务（月度衰减）保留共享 `memory/logs/decay/` | ✅ 完成 | 12 个 TRAE Schedule 任务 Prompt + 代码层（信号管道/cli/jobs/portfolio_loop/5 个期货脚本）路径全部按市场隔离，test_tasks desc 断言同步 |
| **Phase 46** | evolution_loop.py God Class Mixin 化拆分（34 计划 B 阶段，2026-08-13 起）：`EvolutionLoop` 由单文件 62 方法拆为「领域 Mixin 组合」——**Phase 46a 已交付** `evolution_uct.py`（领域 I：UCT 选择 + 熔断/提前停止 5 方法随迁）；**Phase 46b 已交付** `evolution_trace.py`（领域 J：trace 记录 + 经验链 + 实验日志 12 方法随迁 + _QualityInspectionResult 数据类）；**Phase 46c 已交付** `evolution_channels.py`（领域 G：GP/深度/算子 DSL 演化通道 4 方法随迁）；**Phase 46d 已交付** `evolution_seeds.py`（领域 D：种子评估晋升/L1 合并/种子相关性/横截面/Barra 暴露/microstructure 晋升 6 方法随迁）；**Phase 46e 已交付** `evolution_audit.py`（领域 E：审计/走航/消融/鲁棒性/SHAP/因果 9 方法随迁）；**Phase 46f 已交付** `evolution_review.py`（领域 F：定期评审/数据质量 4 方法随迁）；**Phase 46g 已交付** `evolution_prefilter.py`（领域 H：快速预筛/横截面预筛/运行时校验 3 方法随迁）；**Phase 46h 已交付** `evolution_promote.py`（领域 C：精英晋升/DuckDB 持久化/相关性去冗余/正交化闭环/结构簇配额/仓储生命周期 11 方法随迁）；**Phase 46i 已交付** `evolution_candidate.py`（领域 B：候选准入链 _process_candidate 随迁，**B 阶段全部 9 领域拆分收官**）；公开 API 与行为等价不变 | ✅ 完成（46a+46b+46c+46d+46e+46f+46g+46h+46i，B 阶段收官） | `evolution_uct.py` + `evolution_trace.py` + `evolution_channels.py` + `evolution_seeds.py` + `evolution_audit.py` + `evolution_review.py` + `evolution_prefilter.py` + `evolution_promote.py` + `evolution_candidate.py`；`evolution_loop.py` 继承 9 个 Mixin；行数 5117→1470；plans/34-evolution-loop-refactor-inventory.md 证据 |
| **Phase 47** | evolution_loop.py C 阶段组合式重构：Mixin → 协作类 + `__init__` 装配段拆分（34 计划 §8，2026-08-13 起）——**Phase 47a 已交付（v2.103.0+21）** `evolution_uct.py` `EvolutionUctMixin` → `UctSelector` 协作类：主类继承链移除该 Mixin、`__init__` 装配 `_uct_selector`（构造注入 `budget` + `low_ic_box`，`_consecutive_low_ic` 经 box 只读共享）+ 类尾 7 属性 property 转发（`_uct_stats`/`_consecutive_low_ic`/`_evolution_stop_enabled`/`_evolution_stop_k`/`_consecutive_empty_generations`/`_early_stop_last_count`/`_early_stop_reason` 均含 getter+setter）+ 5 方法一行转发桩（`_select_parent_uct`/`_update_uct_stats`/`_update_uct_failure`/`_check_circuit_breaker`/`_maybe_early_stop`）；公开 API 与行为等价不变；**Phase 47b 已交付（v2.103.0+22）** `evolution_prefilter.py` `EvolutionPrefilterMixin` → `CandidatePrefilter` 协作类（领域 H 纯读全局上下文、无领域状态；注入 owner 主类实例动态读取，兼容主类/测试运行时重赋值 `cross_section_data`/`market` 等上下文——34 §8.3 可变上下文修订）+ 3 方法一行转发桩（`_quick_prefilter`/`_cross_section_prefilter`/`_check_factor_runtime`）；**Phase 47c 已交付（v2.103.0+25）** `evolution_promote.py` `EvolutionPromoteMixin` → `EliteStore` 协作类（领域 C 重状态随迁构造 `_repo`/`_cluster_*`/`_l2_*`/`orthogonal_basis`/`high_ic_screener`/`elite_tracker` + 10 方法转发桩 + 17 属性 property 转发含 setter，类级未绑定调用 fallback 兼容）；**Phase 47d 已交付（v2.103.0+26）** `evolution_audit.py` `EvolutionAuditMixin` → `AuditPipeline` 协作类（领域 E 6 组件随迁构造 + **`_signal_cache` 归属落地** + 11 方法转发桩 + 7 属性 property 转发）；**Phase 47e 已交付（v2.103.0+27）** `evolution_trace.py` `EvolutionTraceMixin` → `TraceRecorder` 协作类（领域 J 3 状态随迁构造 + 12 方法转发桩 + 3 属性 property 转发）；**Phase 47f 已交付（v2.103.0+29）** `evolution_review.py` `EvolutionReviewMixin` → `FactorReviewer` 协作类（领域 F 无独享状态，组件 elite_tracker/feedback_loop/logic_monitor/verifier/data_quality_monitor 与上下文全部经 owner 动态读取 + 4 方法转发桩，零 property）；**Phase 47g 已交付（v2.103.0+30）** `evolution_channels.py` `EvolutionChannelsMixin` → `EvolutionChannels` 协作类（领域 G 组件 macro_evolver/feature_ops_engine/feature_importance_analyzer 随迁构造 + 4 方法转发桩 + 3 属性 property 转发）；**Phase 47h 已交付（v2.103.0+32）** `evolution_seeds.py` `EvolutionSeedsMixin` → `SeedManager` 协作类（领域 D 状态 `_barra_exposures_cache`/`_barra_exposures_attempted` 随迁构造；上下文 data/forward_returns/market/cross_section_*/inject_dir/evaluation_chain/verifier/quality_inspector/industry_map/cap_map 经 owner 动态读取——industry_map/cap_map 属可变上下文；跨域方法 16 处经 owner 转发 + 7 方法转发桩 + 2 属性 property 转发含 setter）；**Phase 47i 已交付（v2.103.0+33）** `evolution_candidate.py` `EvolutionCandidateMixin` → `CandidateProcessor` 协作类（领域 B 状态 `_prior_evaluations` 随迁构造；`_signal_cache`（归 AuditPipeline）/`_consecutive_low_ic`（归主循环 low_ic_box）经主类 property 转发读写；跨域方法 21 处经 owner 转发 + 1 方法转发桩 + 1 属性 property 转发含 setter；**继承链清零，`class EvolutionLoop:` 纯组合持有 9 协作类**） | ✅ 完成（Phase 47a-47i，C 阶段收官） | `evolution_uct.py`/`evolution_prefilter.py`/`evolution_promote.py`/`evolution_audit.py`/`evolution_trace.py`/`evolution_review.py`/`evolution_channels.py`/`evolution_seeds.py`/`evolution_candidate.py` 全部重写为协作类；`evolution_loop.py` 零 Mixin 继承 + 组合持有 9 协作类；行数 1470→…→2153、方法数 9→…→141（逻辑 + 转发桩/property）；各 Phase 受影响测试全绿（47+235 / 253 / 336 / 318 / 277 / 277 / 283 / 300 / 314）；里程碑 not-slow 全量回归通过 |


---

## 2. 文件命名规范

- **Python 文件**: `snake_case.py`
- **测试文件**: `test_<module_name>.py`
- **配置文件**: `settings.yaml`
- **Markdown 文档**: `NN-topic.md`（NN 为两位数字序号）
- **程序配置文件**: `Program.md`（首字母大写，位于项目根目录）

示例：
```
fts/
├── factor_engine/
│   ├── evolution_loop.py
│   ├── meta_loop.py
│   ├── portfolio_loop.py
│   └── evaluation_chain.py
tests/
├── factor_engine/
│   ├── test_evolution_loop.py
│   ├── test_meta_loop.py
│   └── test_portfolio_loop.py
```

---

## 3. 版本号命名规则

遵循语义化版本号 `MAJOR.MINOR.PATCH`：

| 级别 | 变更类型 | 示例 |
|:-----|:---------|:-----|
| **MAJOR** | 重大架构变更（如 LangGraph 迁移） | v1.0.0 → v2.0.0 |
| **MINOR** | 功能新增或阶段完成 | v0.1.0 → v0.2.0 |
| **PATCH** | bug 修复或文档更新 | v0.1.0 → v0.1.1 |

当前版本：**v2.30.0**

### 版本号同步规则

FTS 包含两个版本号，修改时必须同步：

| 位置 | 用途 | 当前值 |
|:-----|:-----|:-------|
| `fts/__init__.py` | FTS 项目版本 | `"2.30.0"` |
| `pyproject.toml` | 包版本 | `"2.30.0"` |

`fts/factor_engine/contracts.py` 中的 `EVOLUTION_VERSION` 动态同步 `fts.__init__.__version__`（当前 v2.30.0），随 FTS 项目版本自动更新。

### 状态 schema 版本与冷启动规则

`fts/factor_engine/contracts.py` 中的 `STATE_SCHEMA_VERSION` 控制 L1/L2/L3 状态文件（`state.json`）的冷启动判定：

| 机制 | 说明 |
|:-----|:-----|
| `STATE_SCHEMA_VERSION` | 状态结构版本（`"1"`），仅在 `L1MetaLoopState` / `EvolutionState` / `L3MetaLoopState` TypedDict 字段变更时手动递增 |
| 冷启动触发条件 | `state.json` 中 `schema_version` 与 `STATE_SCHEMA_VERSION` 不一致（含缺失）→ 重新初始化状态 |
| 不触发冷启动 | FTS 功能版本号变更（`EVOLUTION_VERSION` / `__version__` 递增）不影响状态文件，避免小版本升级清空演化进度 |

状态文件字段：`schema_version`（替代旧 `version` 字段），涉及 `MetaStateManager`（L1）、`EvolutionStateManager`（L2）、`PortfolioStateManager`（L3）。

---

## 4. session_id 与 trace_id 生成规则

### trace_id

```
trace_id = "{prefix}_{8hex}_{timestamp}"
```

- `{prefix}`: 模块/任务前缀（如 `l2`、`session`、`fts.l1.sched`）
- `{8hex}`: `secrets.token_hex(4)`，8 位十六进制随机串
- `{timestamp}`: `YYYYMMDDTHHMMSS`，本地时间戳

示例：`l2_3f9a2b1c_20260718T001230`

### run_id

```
run_id = "run_{8hex}_{timestamp}"
```

示例：`run_a1b2c3d4_20260718T001230`

### session_id

session_id 用于区分 CLI 每次执行：

- CLI 启动时由 `main()` 调用 `generate_session_id()` 自动生成（格式 `session_<8hex>_<timestamp>`）
- 挂载到 `args.session_id`，作用域为整个 CLI 会话
- 传递到各子命令，作为日志聚合标识（evolution / meta-loop / portfolio 启动日志均输出）

### 全链路传播

所有模块必须遵循以下规则：

1. CLI 入口生成 `trace_id` 和 `session_id`
2. 传递给 `factor_engine` 各模块（通过函数参数）
3. 管线各 stage 必须传播 `trace_id`（通过 `DataPayload.trace_id`）
4. 监控和日志记录必须包含 `trace_id`
5. scheduler 任务执行时生成独立的带前缀的 trace_id

---

## 5. 角色定义

### 当前角色

| 角色 | 职责 | 定义文件 |
|:-----|:-----|:-----|
| **AI Agent** | FTS 全链路开发：编码、测试、文档、部署（详见能力边界） | `agents/fts-agent.md`（v1.0.0） |

### 未来角色（预留）

| 角色 | 职责 | 引入状态 |
|:-----|:-----|:-------------|
| **人类审核员** | Verifier 锁定审核、Program.md 批准、P0 差距审查 | 未引入（截至 v2.12.0） |
| **运维工程师** | 生产环境部署、调度配置、故障恢复 | 未引入（截至 v2.12.0，部署由 start_fts.ps1 脚本承担） |

### 角色边界

- AI Agent 不得执行人类审核员的职责（如解锁 Verifier）
- AI Agent 不得修改已锁定的 Program.md
- AI Agent 不得越级执行交易决策（由下游系统 FDT 负责）
- AI Agent 不得删除未过期的 elite 因子
- AI Agent 不得修改生产环境配置（需通过文档审查）
- AI Agent 不得将 trace_id 省略或绕过

---

## 6. 状态机

FTS 项目整体状态：

```
[初始] → Phase 1 → Phase 2 → ... → Phase 16 → ... → Phase 19 → Phase 20 → [v2.30.0（当前版本）]
```

各循环的状态：

```
[stopped] → [running] → [paused/completed] → [circuit_broken] → [recovered/stopped]
```

### 未实现功能记录（v2.38.0）

本次升级（Phase 24 + Phase 25）未实现的深度学习模型与强化学习模块：

| 模型 | 说明 | 计划 |
|:-----|:------|:-----|
| **深度学习时序模型** | LSTM/GRU/Transformer 等端到端深度学习时序预测模型 | 未纳入本次计划，需引入 PyTorch/TensorFlow 依赖，存在训练成本高、可解释性低的权衡 |
| **强化学习（RL）** | 基于 RL 的交易策略优化（如 DQN/PPO/SAC），通过环境交互学习最优持仓决策 | 未纳入本次计划，需引入 gym 式环境 + RL 算法库，与 FTS 因子驱动的信号产出范式差异较大 |

以上功能登记为 **GAP-037**，优先级 P2，在 `docs/harness/08-gap-analysis.md` 中跟踪。

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | Phase 11 → `data_futures.py` + `data_futures_fundamental.py` + `seed_data_futures_full.py` + `scheduler/jobs.py` + `scripts/`；v2.58.0 → `data_sources/migrate.py`（kline_cache.adj_factor 列 + contract_kline 建表）+ `data_sources/roll_calendar.py`（RollCalendar 换月日历/复权）+ `factor_engine/backtest_pipeline.py`（展期成本）+ `cost_model.py`；v2.62.0 → `factor_engine/barra/barra_style.py`（BarraStyleEngine 10 风格暴露）+ `factor_engine/barra/barra_neutralizer.py`（barra_neutralize_matrix 逐日 OLS 回归残差）+ `factor_engine/evaluation_chain.py`（style_exposures 参数 + Step 2.6 风格中性化）；v2.65.0 → `factor_engine/stock_regime.py`（StockRegimeSelector 行业轮动/风格切换检测，已随股票管线剥离至 fts-stock，2026-08）+ `factor_engine/portfolio_loop.py`（REGIME_STYLE_MULTIPLIERS 股票风格键 + run(stock_regime) 驱动，股票侧已剥离） |
| 可验证断言 | Phase 11 产出物：482 种子因子（9+101+158+191+23），期货 12 家族 50+ 子因子，8 个定时任务；v2.58.0 GAP-046：kline_cache 含 adj_factor 列、contract_kline 可建表写入、BacktestPipeline 支持展期成本；v2.62.0 GAP-S02：barra 包 10 风格暴露 + 逐日 OLS 回归残差，`cross_section_evaluate_backtest` Step 2.6 两级中性化链，test_barra.py 13 用例全绿；v2.65.0 GAP-S03：StockRegimeSelector 行业轮动三态 + 风格切换双态 + HMM 多周期集成，REGIME_STYLE_MULTIPLIERS 6 股票风格键，PortfolioLoop.run(stock_regime) 驱动 L3，test_stock_regime.py 19 用例全绿（均已随股票管线剥离至 fts-stock，2026-08） |
| 检验方式 | `python -m pytest tests/factor_engine/test_seed_pool.py --no-cov -q 2>&1 | findstr "passed"` |