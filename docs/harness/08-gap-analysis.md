# FTS 差距分析

> 版本: v3.1.0+4
> 最后更新: 2026-08-20
> 状态: 活跃 — 随项目迭代持续更新

> 校正说明（2026-08-20）：全量瘦身——已关闭条目（v2.104.0 前关闭的早期 GAP-001~113、GAP-X/I/L 系列等）从主表删除，历史关闭记录保留于 07-operations.md 版本历史与 git 历史；已关闭项折叠为 §3 摘要表；编号冲突重编号记录（GAP-152/153→176/177、GAP-154~158→169~173、L 系列 161/162→174/175）。

---

## 1. 差距总览

| 优先级 | 开放 | 已关闭 | 总计 |
|:-------|:-----|:-------|:-----|
| P0 | 0 | 2 | 2 |
| P1 | 5 | 35 | 40 |
| P2 | 12 | 17 | 29 |
| GAP-C（Stage 3C 远期） | 1 | 7 | 8 |
| **合计** | **18** | **61** | **79** |

> 总览口径：开放 = 状态列非「✅」项（🔴 开放 / 🟡 开放中·部分 / 🔄 处理中）；已关闭 = 状态列为「✅ 已关闭/已实施/已修复」。P1 开放 5（GAP-132/150/151/170/173）；P2 开放 12（GAP-089/090/093/127/142/152/153/156/157/158/174/175）；GAP-C 开放 1（C4 真实多机集群后置）。

---

## 2. 开放差距登记表

### P1 — 重要改进（提升效率或稳定性）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-132 | `fts/factor_engine/factor_db/lineage.py` `get_evaluation_trend` + `fts/scheduler/jobs.py` `factor_inspector_job` + `factor_evaluations` 数据积累 | 退化检测因评估历史不足静默失效：`get_evaluation_trend` 要求 ≥2 条含 `level_1_sharpe` 的评估记录才计算趋势，各库 `FACTORS_WITH_2PLUS_EVALS=0` → 全部返回 `insufficient_data`，`detect_degradation` 恒 False | 因子退化安全机制形同虚设：每日巡检静默失效，退化因子无法被识别与降级 | 1 月内（评估多期积累后收口） | 🔴 开放（2026-08-17 登记：`factor_inspector_job` 已改默认全期货 + dry-run 防误降级；评估流水多期累积、趋势检测恢复后重审关闭） |
| GAP-150 | `fts/store/registry.py`（`StorageRegistry.validate_contract`）+ 各数据域写路径 | 存储域契约仅静态校验 YAML 字段，不拦截实际写入：写入库路径/表名由调用方自由指定，无法保证落在 registry 登记域内 → 域漂移/双写只在数据消费侧暴露 | SSOT 契约在写入口形同虚设，新增写路径绕过注册表后契约失效且不可见 | 下个里程碑 | 🟡 部分（v2.105.0+19：`find_by_path` 反查 + 告警模式；+20 严格模式默认开启——`FactorRepository` 默认路径未登记抛 ValueError（`FTS_STORAGE_WRITE_STRICT=0` 回退告警），显式注入豁免；其余写入口（信号库/状态库/行情库）接入排期；测试 +8） |
| GAP-151 | `fts/data_futures.py`（`_read_kline_cache` 等加载路径）+ 数据契约消费方 | 数据契约字段完整性缺运行时断言：`hold` 字段 100% 缺失被三层代理兜底掩盖，加载后无必填字段清单校验 | 必填字段缺失被代理值"合理化"，因子表现"合理但失真"，缺失不可见 | 下个里程碑 | 🟡 部分（v2.105.0+19：`KLINE_REQUIRED_FIELDS` + 加载后完整性告警；+20 分级校验——核心字段缺失→error+跳过，增强字段缺失→warning+代理降级显式暴露；测试 +4） |
| GAP-170 | `fts/factor_engine/factor_program.py`（`_ArrayDataWrapper` + 沙箱回退）+ 环境（pandas 3.0.5） | pandas 3.0 `np.asarray(Series, dtype=float)` 返回只读数组：legacy Python 模板因子中原地赋值抛 `ValueError: assignment destination is read-only`；三层回退全部失败 → `FactorCompileError` → 信号留 NaN | legacy Python 模板因子路径失效（operator/DSL 因子不受影响）；影响存量手写模板因子与对应测试 | 下个里程碑 | 🔴 开放（2026-08-20 登记：修复方向——回退路径对 ndarray 显式 `.copy()` 或模板 `np.asarray(..., dtype=float).copy()`，待专项） |
| GAP-173 | `fts/factor_engine/portfolio_loop.py`（signal_sharpe measured/estimated 语义）+ 相关测试 + 环境（pandas 3.0.5） | 全量回归残留预存失败 21 项（8129 passed / 21 failed）：① portfolio_loop signal_sharpe 断言 `estimated` ≠ `measured`（工作区既有本地改动引入）；② `test_portfolio_walk_forward` 5 项 pandas 3.0 只读数组错误；③ `test_regime_features` 常数序列相关 nan vs 0.0 | 全量回归门禁仍有预存失败（与双系统切分无关，属工作区既有状态与环境兼容） | 下个里程碑 | 🔴 开放（2026-08-20 登记：非双系统切分引入；pandas-3 只读归并 GAP-170 专项） |

### P2 — 一般改进（优化代码质量）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-089 | `fts/data_sources/tqsdk_tick_source.py` + `migrate.py` | 高频数据深度不足：tick_cache 32 列仅 5 档盘口且 TQSDK 实测仅 ~42 分钟样本；无逐笔委托/成交、无 10 档深度、无订单簿重建 | 微观结构因子受数据深度限制，高频因子体系无法对标头部机构 | 3 月内 | 🟡 开放（**受限登记**：数据深度受外部数据源制约，剩余深度扩展待数据源条件成熟，跨会话 tick_cache 增量累积已落地） |
| GAP-090 | 全链路数据层（`fts/store/`）+ `storage_landscape.yaml` | 数据持久化治理差距：8+ 种形态并存、JSON 泛滥、同类数据双写漂移、信号缓存裸 .npy 无 schema | 数据管理无统一目录（SSOT 未达成）；状态类与资产类混放 | 3 月内 | 🟡 开放（P0~P4 大部实施：存储域注册表 + 因子资产入库 + StateKVStore(SQLite WAL) + 信号 Parquet 化 + 冷热归档 + L2/L3 写连接短生命周期；**P4 清理项**（elite JSON 快照/状态 JSON/.npy/experiments 与旧 state.duckdb）受「冻结期≥1 发布周期」约束待执行） |
| GAP-093 | `fts/factor_engine/regime*.py`（远期）+ RL 决策层 | RL 制度条件决策层缺失：制度判定→组合决策为规则/概率链，无强化学习条件决策层 | 制度→仓位/权重映射为静态规则，非数据驱动优化 | 远期（P2） | 🔴 开放（2026-08-11 plans/28 登记；需实盘反馈闭环，simulated_portfolio 已铺垫） |
| GAP-127 | `fts/factor_engine/extractors/` | 量化平台 API 直连：聚宽/米筐/BigQuant 等平台因子库 API 多数闭源/需授权，未做逐平台适配 | 平台独有因子数据无法程序化接入 | 远期 | 🔴 开放（2026-08-16 登记，收益受平台开放度制约） |
| GAP-142 | `fts/factor_engine/energy_qa_review.py` | 能化链评审与定期质检职责重叠、判据竞争，且各自重复准备面板与 reaudit | 合并为统一管道（宁严勿松单维度降级 + 冷却期 30 日自动回归 + 单一状态机），消除重复计算与判据冲突 | 灰度 4 周 | 🔄 处理中（v2.104.0+87 统一管道已实施并接入 jobs.py，冷却期 30 交易日回归 + 单一状态机，灰度对比观察中） |
| GAP-152 | `fts/data_sources/tqsdk_tick_source.py` + `data/fts_history.duckdb`（kline_cache.hold） | hold（持仓量）真实数据源缺口：kline_cache hold 100% 缺失，当前走 20 日滚动均量代理（显式降级但非真实持仓） | hold 依赖因子使用代理值，无真实持仓数据的日子「合理但失真」 | 外部依赖（TQSDK 环境/权限） | 🔴 开放（TQSDK 环境可用后验证 hold 覆盖，缺失率经 proxy_missing_ratio 可观测下降） |
| GAP-153 | `fts/factor_engine/` 因子消费链路 + `config/futures_field_consumption.py` | hold 依赖因子无消费侧数据充足性门禁：代理率超阈值时，依赖 hold 的因子仍正常参与计算/组合 | 代理失真传导至因子信号与组合权重 | 排期（P2） | 🔴 开放（用字段消费字典 consumers 对 hold 依赖因子做数据充足性门禁/质量标记） |
| GAP-156 | `fts/data_sources/quantdata_provider.py`（缺失）+ `fts/core/enums.py` + `aggregator.py` | FTS 与 QuantData 权威数据源零接入（文档曾声称集成但代码零引用）：`DataSource` 枚举无 QUANTDATA 成员，aggregator 降级链无 QuantData 源 | 权威性原则无落地载体，因子消费数据来源权威性受质疑 | 本次实施（主链路切换） | 🔴 开放（2026-08-19 登记，随 QuantData 主链路切换计划实施——**注：v2.105.0+32 已实施，本条状态待核对**） |
| GAP-157 | `D:\QuantData`（无 fundamental 表）+ `fts/data_futures_fundamental.py` + `futures_field_consumption.py` | 库存/仓单/现货基差无权威数据源：QuantData 实测无 fundamental/basis 表 | fundamental 类因子与结构约束只能基于非权威数据，违背权威性原则 | 待 QuantData 侧补数据源 | 🔴 开放（字段权威矩阵 L2 禁依赖；需 QuantData 补齐库存/仓单/基差采集后接线） |
| GAP-158 | `D:\QuantData\market_data\kline_history.duckdb`（kline_daily 表） | settle/amount/vwap/pre_settle 无权威源：QuantData kline_daily 仅 OHLCV+open_interest | 依赖 settle/amount 的因子只能走非权威降级/代理 | 持续（L1 降级层） | 🟡 部分（字段权威矩阵 L1 降级·非权威，标注来源不硬拒；QuantData 侧补 settle 采集后可升 L0） |
| GAP-174 | `fts/factor_engine/scope_domain/`（P2，v3.0.0+14） | 品种级特异因子无真伪鉴别：单品种高 IC 无法与过拟合噪声区分 | 品种特异与噪声混同，特异因子价值无法释放或被误杀 | P2 处理中 | 🔄 部分关闭（`evaluate_symbol_scope` + `symbol_scope_guard` 真伪护栏已交付，Q10 品种级分支生效；**剩余**：演化生成端无品种级通道，真实品种特异挖掘依赖后续生成端改造） |
| GAP-175 | `fts/factor_engine/scope_domain/specific_fields.py` + `config/specific_fields.yaml` + `scripts/collect_specific_fields.py` | 品种特有数据源缺失：fundamental 9 字段为全品种统一 schema，无法表达品种特有逻辑 | 品种特异因子的数据前提缺失 | P3 框架先行 | 🔄 部分关闭（注册表 + 加载器 + 真实 parquet 加载通道 + 采集脚本骨架已交付，SC0.sc_freight_premium 首个启用字段；**剩余**：真实行情源接入，placeholder 位待扩展） |

### GAP-C 系列 — Stage 3C 远期机构级差距

> 承接总纲 23 号计划 §1 差距矩阵残留项。C1/C2/C3/C5/C6/C7/C8 已实施（2026-08-11 首期）；C4 单机 LocalCluster 已落地，真实多机集群待硬件/基建条件成熟后按 DaskBackend 抽象接入。

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| C4 | `fts/factor_engine/executor_backend.py`（GAP-I502 扩展） | 多节点分布式挖掘工厂（Dask 集群实战部署） | 单机吞吐上限（Stage 3 退出标准①） | Stage 3C | 🟡 已实施首期/二期多机后置开放（DaskBackend 增强 + `BatchMiner.filter_batch` dask 接线 + `distributed` extra + 吞吐基准 17 用例；真实多机集群部署后置，按 `DaskBackend(address="tcp://scheduler:8786")` 接入） |

---

## 3. 已关闭差距摘要（v2.104.0 起）

> 完整关闭明细（含实施方案与验收证据）见对应 07-operations.md 版本历史条目；v2.104.0 前关闭的早期条目由 git 历史保留。

### P0（2）

| ID | 主题 | 关闭版本 |
|:---|:-----|:---------|
| GAP-177 | 拥挤度体系（plans/56 A-D 四模块，决策门未过降级纯观测层） | v2.105.0+25 |
| GAP-081 | A 股特有字段接入（迁移 fts-stock 实施） | v2.104.0+1 |

### P1（35）

| ID | 主题 | 关闭版本 |
|:---|:-----|:---------|
| GAP-176 | 宏观 Beta 层（plans/55 A-E 五模块，灰度保守档） | v2.105.0+22 |
| GAP-147 | 评审质检阀门错库（_conn 按 market 路由） | v2.105.0+12 |
| GAP-144 | L2 晋升入口子链 IC 豁免放行（subchain_waiver） | v2.105.0+8 |
| GAP-145 | Regime 条件化因子交易（plans/53 D/A/B/C，K-W p=0.030 通过） | v2.105.0+9 |
| GAP-143 | L3 子链差异化权重调制键不一致修复 | v2.105.0+5 |
| GAP-140 | 全量回归 93 预存失败清零（caplog 污染根治为核心） | v2.105.0+7 |
| GAP-138 | L3 权重源头消费子链方向 Gate（plans/50 A/B） | v2.104.0+113 |
| GAP-137 | 评审质检与生命周期管理子链化（plans/49 A-D） | v2.104.0+112 |
| GAP-135 | GP 因子 QA 门禁 + 审计代码读取 + 表达式去重 | v2.105.0 |
| GAP-136 | 品种/子链 Regime 独立方向 Gate（plans/48 A-D） | v2.104.0+111 |
| GAP-134 | 子链特异因子被全链统一权重稀释（plans/47 四模块） | v2.104.0+109 |
| GAP-133 | 能化链 500 日短样本 1 窗口审计拦截（750 面板 + 短窗回退） | v2.104.0+106/107 |
| GAP-130 | 盲测/IC 矩阵新上市品种信号-收益错位（align_signal_to_dates） | v2.104.0+81 |
| GAP-129 | 测试组写真实因子库（tests/conftest get_db_path 重定向 tmp） | v2.104.0+79 |
| GAP-128 | 质检结果未落库 SSOT（save_score/save_report 接线 + 回填） | v2.104.0+78 |
| GAP-125 | 信号管道 IC 方向丢弃 + Regime 零影响（五修复） | v2.104.0+65 |
| GAP-123 | L1 候选经济逻辑论证质量不足（四方案落地） | v2.104.0+45/46 |
| GAP-122 | L3 Verifier 判定口径错配（_verifier_view 缩放前判定） | v2.104.0+42 |
| GAP-118 | 种子因子缺 params 字段（27 因子补 `params: {}`） | v2.104.0+25 |
| GAP-117 | 阈值校准脚本日换手反推口径错误（21→42） | v2.104.0+16 |
| GAP-116 | symbol_holdout 留存率判定噪声主导（min_train_ic=0.05） | v2.104.0+15 |
| GAP-115 | 熔断预算传播缺陷（budget property setter 传播） | v2.104.0+14 |
| GAP-114 | Verifier Level 1 换手绝对阈值错配（成本敏感净收益校验） | v2.104.0+13 |
| GAP-I306 | 自动构建因子收益矩阵 Sharpe 虚高（三件套修复） | v2.105.0 |
| GAP-I307 | L1 Step 2.5 去重口径失效（读 factor_pool.json SSOT） | v2.104.0+10 |
| GAP-149 | 因子状态枚举缺运行时校验（VALID_CATALOG_STATUS） | v2.105.0+19/20 |
| GAP-169 | numba 版本不兼容整模块 import 崩溃（njit 透传装饰器） | v2.105.0+32 前后 |
| GAP-171 | lightgbm 4.6 小截面零分裂（importance_type=gain + min_child_samples=5） | v2.105.0+32 前后 |
| GAP-172 | GAP-150 严格模式与测试隔离冲突（项目根外路径豁免） | v2.105.0+32 前后 |
| GAP-161 | 阈值静态化按 regime 分层（plans/59 OPT-01，regime_thresholds） | v3.0.0+18 |
| GAP-162 | 跨运行累积 FDR 折扣（plans/59 OPT-02，fdr_discount） | v3.0.0+20 |
| GAP-163 | 特异因子观察期与 OOS 前瞻复核（plans/59 OPT-03） | v3.0.0+21 |
| GAP-164 | IC 口径一致性校验（plans/59 OPT-04，ic_consistency） | v3.0.0+22 |
| GAP-165 | 数据质量-评审联动（plans/59 OPT-05，data_gate） | v3.0.0+23 |
| GAP-166 | 人审 SLA 自动降级（plans/59 OPT-06，review_sla） | v3.0.0+23 |

### P2（17）

| ID | 主题 | 关闭版本 |
|:---|:-----|:---------|
| GAP-091 | 期货仓单数据接入（CZCE/GFEX 官方接口 + 东财 RPT 补 SHFE/DCE/INE） | v2.104.0+ |
| GAP-092 | 宏观制度维度（Bridgewater 四象限 MacroRegimeDetector） | v2.104.0+3 |
| GAP-094 | 置信度 isotonic/Platt 概率校准 | v2.104.0+1 |
| GAP-095 | regime blend 幂次调节（blend_power） | v2.104.0+1 |
| GAP-119 | catalog verify JSON↔DuckDB 快照漂移（DuckDB SSOT 单向 + 回填） | v2.104.0+26 |
| GAP-120 | 信号增量有效性未验证（validate_signal_delta.py 五步验证） | v2.105.0 |
| GAP-121 | 横截面评估性能瓶颈（plans/37 IC 矩阵化 + 算子向量化批 1-3 + 缺口面板回退豁免） | v2.104.0+54~58 |
| GAP-126 | 提取器源注册表配置化（config/extractors.yaml SSOT） | v2.105.0 |
| GAP-131 | L1 拒绝候选不可追溯（rejected_dir 落盘 + fix 策略 6） | v2.104.0+82 |
| GAP-139 | L3 信号矩阵增量窗口追加（plans/52） | v2.104.0+ |
| GAP-148 | 双符号变体混行致监控误报 + 样本缩水（完整度优先去重 + 数据治理） | v2.105.0+17 |
| GAP-154 | TQSDK 增强源品种映射缺 IM0 | v2.105.0+28 |
| GAP-155 | high_vol 制度标签与前提监控口径分歧（前提交叉验证） | v2.105.0+30 |
| GAP-159 | 天勤源默认挂载违背 QuantData 解耦（tqsdk_sources_enabled 门控） | v3.0.0+2 |
| GAP-160 | 演化审计链跨品种验证不适配全期货异构池（盲测池接入 + 板块覆盖率） | v3.0.0+7 |
| GAP-167 | 参数稳健性仅离散档位（plans/59 OPT-07，param_robustness） | v3.0.0+24 |
| GAP-168 | 成本/容量静态失真（plans/59 OPT-08，liquidity_env） | v3.0.0+25 |

### GAP-C（7）

| ID | 主题 | 状态 |
|:---|:-----|:-----|
| C1 | Level2 微观结构因子接入 L2 演化（首期 + 评估晋升接线） | ✅ 已实施 |
| C2 | 另类数据因子（舆情 NLP 词典 + LLM 精修一致性） | ✅ 已实施 |
| C3 | Black-Litterman 观点融合权重 | ✅ 已实施 |
| C5 | 轻量 Transformer 因子（纯 numpy 因果注意力，GAN 远期） | ✅ 已实施 |
| C6 | 在线学习与自动重校准 | ✅ 已实施 |
| C7 | 回测保真实证化（冲击成本实证标定 + 融资成本） | ✅ 已实施 |
| C8 | 基础设施深化（人审工作台 + 算子库扩容 DSL 132） | ✅ 已实施 |

---

## 4. 优先级定义

| 优先级 | 定义 | 处理时限 | 验证标准 |
|:-------|:-----|:---------|:---------|
| **P0** | 阻塞性问题，影响核心功能的正确性和可靠性 | 1 周内 | 新增测试覆盖率达到 80%+，相关模块无 P0 bug |
| **P1** | 重要改进，提升系统效率或稳定性 | 1 月内 | 新增测试覆盖率达到 70%+，关键路径全覆盖 |
| **P2** | 一般改进，优化代码质量和可维护性 | 3 月内 | 新增测试覆盖率达到 50%+ |

---

## 5. 差距关闭流程

1. 编写测试代码并通过 PR 审查
2. 运行完整测试套件确认全部通过（8469+ collected，0 failed）
3. 更新本文件中的差距状态（开放→关闭时移入 §3 摘要表）
4. 更新 `06-testing.md` 中的覆盖统计
5. 如果涉及架构变更，更新 `01-architecture.md`

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件登记 **79 项差距**（P0 2 + P1 40 + P2 29 + GAP-C 8），开放 18 / 已关闭 61；覆盖 `fts/factor_engine/`、`fts/data_sources/`、`fts/scheduler/jobs.py`、`fts/monitor/`、`fts/store/`、`fts/cli.py`、`scripts/` 等模块 |
| 可验证断言 | 开放项状态列含 🔴/🟡/🔄 标记；已关闭项含 ✅ 标记；总览合计 79 与登记表条数一致 |
| 检验方式 | 检查差距登记表状态列标注准确；总数与总览口径一致；编号无重复；关闭记录关联 07-operations.md 版本历史 |
