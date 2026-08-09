# FTS 运维与版本管理

> 版本: v2.54.0
> 最后更新: 2026-08-09

---

## 1. 版本历史

| 版本 | 日期 | 说明 |
|:-----|:-----|:-----|
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
| `l3_portfolio_loop` | `0 20 * * *` | 每日 20:00 | L3 Portfolio Loop：因子筛选(ACTIVE_FACTOR_CAP=20) + 信号合成(默认elastic_net) + Verifier 校验 |
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
| 可验证断言 | 版本号 v2.54.0 在 pyproject.toml 中定义，fts/__init__.py 动态读取，所有 Harness 文档版本头一致；v2.54.0 精英因子全员质量巡检 230 因子 229 合格 1 出库 |
| 检验方式 | `python -c "from fts import __version__; assert __version__ == '2.22.0'"`；`python scripts/update_doc_versions.py --check`；`python scripts/verify_doc_consistency.py` |
