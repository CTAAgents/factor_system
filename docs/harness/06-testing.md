# FTS 测试策略

> 版本: v2.104.0+77
> 最后更新: 2026-08-15

---

## 1. 测试金字塔

```
         ┌──────────┐
         │  E2E 测试 │    ← 全流程端到端验证（14 个闭环用例）
         │          │
        ┌┴──────────┴┐
        │ 集成测试    │    ← 策略层集成验证（1 个测试文件）
        │            │
       ┌┴────────────┴┐
       │ 单元测试      │    ← 各模块独立测试（25 个测试文件）
       │              │
       └──────────────┘
```

| 层级 | 测试文件数 | 用例数 | 说明 |
|:-----|:----------|:-------|:-----|
| 单元测试 | 90+ | 7329 | 各模块独立测试（含基本面数据层 + 信号管道 + 消融实验 + 风险标签 + 场景测试 + SHAP分析 + 鲁棒性 + 因果验证 + 逻辑监控 + 种子因子相关性预检 + DuckDB因子仓库 + 因子相关性矩阵 + 因子血缘审计 + 失败模式分类 + 因子巡检 + 生命周期E2E闭环 + 回测流水线 + 后代因子运行时校验 + FTS-Expr DSL 算子因子 + GP 多父代交叉 + 快速预筛选 + 评分卡配置 + **算子演化引擎（C.4，13 用例）** + **L1→L2 候选合并（GAP-031，8 用例）** + **L2 晋升双写原子化（GAP-032，4 用例）** + **factor_db_path 测试隔离注入（GAP-030，2 用例）** + **SectorRegimeSelector 产业链级制度检测（15 用例）** + **L1 候选因子评分缺陷修复（150 测试全绿）** + **分钟级回测频率自适应（3 用例）** + **ML 模型层（v2.38.0，~30 用例）** + **SignalBridge 信号桥接（v2.38.0，~25 用例）** + **L1 注入质量优化（v2.48.0，5 用例：硬/软失败分类 + 软失败不熔断 + verify 编译 detail 日志 + extractor debug 落盘 2 项）** + **高IC筛查剔除（B.4，v2.49.0，25 用例：16 项打分 + 5 项一票否决 + A/B/C/PASS 评级边界 + 市场统一性 + B级优化建议）** + **质检拦截器判定修复（v2.50.0，~18 用例：消融信息型/拦截型判定 + IC NaN 掩码 + SingleAblation feature 契约）** + **vwap 通用 IC 门槛 + 种子全链质检（v2.50.0，7 用例：vwap code abs(IC)<0.08 拦截 3 + 种子 Verifier/消融/因果/鲁棒失败拒绝晋升 4）** + **精英因子全员质量巡检（v2.54.0，质检脚本 `scripts/elite_quality_inspection.py`——230 因子全量 HighICScreener 质检，含 V5 经济逻辑 fallback 修复，229 合格 1 出库）** + **FactorStyle 分类器 + L3 adaptive 权重（v2.56.0，40 用例：test_style_classifier.py 32 用例（名称/代码/签名推断 + 显式 style_tags 优先 + REGIME_STYLE_MULTIPLIERS 覆盖）+ test_portfolio_loop_adaptive.py 8 用例（adaptive 基权重=sharpe + AdaptiveWeightConfig 契约 + PortfolioLoop 端到端））** + **股票因子行业/市值中性化（v2.57.0，~17 用例：feature_ops 2（industry_cap_neutral 双重中性化 + NaN 行业归 UNKNOWN）+ evaluation_chain 7（横截面行业/双重中性化 2 + _neutralize_signal_matrix 5：行业去均值/市值加权去均值/NaN/空列表/UNKNOWN）+ config_settings 8（stock_neutralization 配置默认值与 env 覆盖 + load_industry_map 有效/缺失/格式错误/非 dict/空白键过滤/默认路径））** + **Barra 风格体系（v2.62.0，13 用例：test_barra.py——10 风格暴露引擎 5（齐全/形状/size 单调/未知风格抛错/字段缺失降级）+ 截面中性化 7（残差形状/正交 corr<0.15/size 剥离/空暴露/行业叠加/小样本降级/常数列剔除）+ 评估链集成 1（style_exposures 生效））** + **期货换月复权与展期仿真（v2.58.0，~22 用例：test_roll_calendar.py——换月日历构建（最大成交量主力判定）、复权因子计算（比率法）、复权序列应用（get_ohlcv adjusted）、contract_kline 缺失降级、展期成本扣除（BacktestPipeline 持仓穿越换月）、报告展期成本统计、配置默认值；test_cost_model.py 展期成本项 7 用例——期货默认 roll_cost_bps=2.0/股票 ETF=0、展期成本计入 total_cost、无换月日期/空仓/长度不匹配不扣、net_sharpe 惩罚、AdjustedMetrics.roll_cost_bps 字段）** + **L2 准入去冗余（GAP-I206，v2.71.0，10 用例：test_l2_elite_redundancy.py——高相关命中/负高相关 abs 判断/低相关放行/空 elite 放行/索引文件跳过/容量护栏/执行失败容错 + shadow 高相关拦截不落盘/种子跳过检查正常晋升/低相关正常晋升）** + **正交基底+衰减分级（GAP-I206 补充 + GAP-I305，v2.72.0，19 用例：test_orthogonal_basis.py——IC 斜率/衰减分级 normal/observe/retired/基底读写/注册上限/Gram-Schmidt 正交/弱候选拒绝/L2 集成降级）** + **L1 批量候选 + 审查工作流（GAP-I101/I102，v2.72.0，12 用例：test_meta_loop.py 批量候选契约校验 8（全合法/空列表/缺必填字段 ×3/非 dict/样本截断/吞吐计算/零耗时）+ test_review_workflow.py CLI 命令 4（list 队列/market 过滤/approve 回写/reject 回写））** + **深度因子学习（GAP-I203，v2.73.0，28 用例：test_gru_factor.py——GRU 模型级 11（形状/学习/可复现/降级 4/常数列/权重导出/工厂）+ DeepFactor 生成器集成 9（契约/code 可执行/零未来函数截断一致性/降级 3/确定性）+ EvolutionLoop 接线 8（`_run_deep_evolution` 血缘与降级/`_evolve_one` deep 分派/批次轮换））** + **组合/跨标的算子单一事实源（GAP-I202，v2.75.0，7 用例：test_registry.py——ts_slope/ts_quantile 元数据/功能/边界 + GP 注册表含组合算子/可调用 + required_shared 硬约束一致性 + DSL 执行）** + **在线因子性能监控（GAP-I402，v2.77.0，12 用例：test_live_factor_monitor.py——偏离检查 5 + ingest_live_ic 6 + GAP-I401 端到端对接 1）** + **数据驱动动态池（GAP-054，v2.80.0，10 用例：test_dynamic_pool.py——get_dynamic_core_subset 缺失/非法/损坏回退 + 白名单过滤 6 + build_pool 渐进保留/替代/产业约束/池大小 4 + test_tasks.py 任务数断言 10→11）** + **盲测池机构标准（GAP-055，v2.81.0，9 用例：test_holdout_pool.py——规模 12~15/与核心池·训练集不重叠/全量内/去重/产业链覆盖≥8/大流动性代表/训练集充足）** + **多持有期 IC 体系（GAP-060，v2.90.0，25 用例：test_horizon_analysis.py 12——形状/正 IC/最佳持有期/衰减归一化/常数信号/短样本/NaN/无效持有期/序列化/确定性/最佳选择/默认持有期 + test_cross_section_horizon.py 13——横截面形状/序列化/短样本降级/无效持有期/NaN close 兜底/确定性/评估链 multi_horizon 输出/空 horizons 关闭 + **P0 面板向量化零漂移对照 5（v2.104.0+61）：全有限/缺口面板 on/off 逐字段一致 + 短样本退化路径 + h>=oos_n 双 None + 评估链开关集成一致）**）** + **可交易性压力层（GAP-061，v2.97.0，12 用例：test_cost_sensitivity.py——盈亏平衡倍数/净夏普单调/最大倍数仍正/毛夏普口径/市场参数透传/退化 4 项）** + **评估链统计补全（GAP-062 补测，v2.97.0，10 用例：test_qc_stats_completion.py——max_consecutive_losses 基础/全正 + block_ic_stats 预测力/常数回 None + cs_quintile_returns 单调/小面板空 + evaluate_backtest/cross_section GAP-062 字段 + 多持有期开关 + backtest_pipeline 连亏）** + **组合质检三标准（GAP-063，v2.97.0，6 用例：test_portfolio_qc.py——synthesis_gain/diversification_gain/drawdown_control 判定 + 空组合 qc_standards + 契约序列化）** + **IC 协方差加权合成（GAP-064，v2.97.0，10 用例：test_ic_weight.py——w=Σ⁻¹μ 形状/归一化/收缩正则/NaN 行剔除/奇异回退/样本<20 回退/单因子 None/synthesize ic_weight 模式/失败回退 IC 均值加权）** + **品种板块联动（GAP-065，v2.97.0，8 用例：test_sector_linkage.py——板块内相关/跨板块/因子截面分散度/high_linkage 标记/空板块/序列化）** + **夜盘隔夜跳空标记（GAP-066，v2.97.0，8 用例：test_overnight_gap.py——跳空列注入/首日 NaN/flag 阈值边界/开关关闭/注入幂等）** + **组合级回撤止损 + 相关性熔断（GAP-067，v2.97.0，10 用例：test_portfolio_risk_controls.py——回撤触发/阈值可配/上升不触发/危机相关触发/独立不触发/短窗不触发/综合告警/无输入不崩溃/to_dict 序列化）** + **兜底家族豁免（GAP-070，v2.98.0，2 用例：test_evolution_loop.py TestGapF16PromoteToElite——other/unknown 兜底家族达上限仍晋升 + JSON 快照落盘，trend 家族拦截不变）** + **短样本 OOS 审计判定（GAP-073，v2.98.0，4 用例：test_audit.py TestOOSConsistency——单窗口 skipped/双窗口正常评估（低一致性照常失败）/L1 兜底无窗口键保持原逻辑/原有 None skipped）**）** + **GAP-121 评估链修复测试（v2.104.0+44，+11 用例）：test_audit.py 4 语义反转（GAP-073 放宽反转硬拦截——oos 缺失/单窗口由 skipped 改 failed/空输入 oos failed 其余 6 skipped）+ test_evaluation_chain.py 3（TestCrossSectionWalkForward 横截面走航 2——短样本 `_build_wf_config` 多窗口 ≥2 / 默认配置短样本 0 窗口 + `evaluate_multiple_tests` n_tested 批次对齐 1——n=64 时 effective_n=64、bonferroni_p 收紧、adjusted_t 缩小 8 倍）+ test_evolution_loop.py 4 晋升硬门（WalkForward 缺失 0 窗口拦截/单窗口 1 拦截/HighIC B 级跳过 14>8 拦截/跳过 4≤8 放行）+ test_roll_calendar.py 2 回归（VARCHAR date 匹配 close/volume=0 排除主力判定）** + **算子演化多样性修复（GAP-074，v2.100.0，4 用例：test_gap074_operator_diversity.py——UCT 失败反馈切换父因子/失败不授予正奖励/同父不同代种子差异/同父同代种子可复现）** + **东财宏观数据源（GAP-088 数据源闭环，v2.101.0，9 用例：test_macro_eastmoney_source.py——东财 CPI/进出口归一化、空返回 None、中债登 1 年期分年拼接去重、美债 10 年期、未知指标 None、拉取失败降级 None、edb_cache 写读不二次请求、Aligner 默认源端到端注入）** + **字段真实性巡检（v2.101.0，3 用例：test_data_futures.py _from_aggregator_df settle=0 占位代理典型价 + hold=0 滚动均量代理；test_data_futures_fundamental.py 库存东财 symbol 大小写敏感兜底）** + **分钟缓存独立新鲜度（v2.101.0，3 用例：test_aggregator.py _try_minute_cache 独立 minute_cache_max_age_days 不受日线 30 天窗口影响 + test_config_settings.py 默认 1 天/env 覆盖）** + **跨品种 A+C 双机制（GAP-096，v2.103.0，4 用例：test_audit.py TestCrossSymbol——软门控A 平均 IC 强且比例≥下限通过/平均 IC 不足失败/软门控C 二项检验显著通过/三机制全不满足失败）** + **L1 失败率熔断时序修复（GAP-098，v2.103.0，3 用例：test_meta_loop.py——未验证批不误熔断（0/19 已验证返回 None）/整批 20 全失败熔断保留 + 3 注入 85% 不熔断/20 有效候选集成回归 run() completed+20 注入）** + **同向敞口惩罚+踩踏规避+换手预算（GAP-099/100/101，v2.103.0+9，34 用例：test_portfolio_risk_controls.py +22（G1 check_aligned_exposure 15——全同向最大压缩/分歧不触发/阈值边界/部分压缩/权重加权/曲线/禁用/空输入 + G2 throttle_exit_stampede 7——单日节流分批/配额内不重排/禁用直通/空输入/多日分散/计划日耗尽回填/敞口优先）+ test_turnover_budget.py 13——未超限直通/超限剔除最弱/保留最强/禁用/空输入/重归一化/恰等 cap 不剔除/剔除顺序/确定性/不改输入/NaN 评分安全/build_combo G3 关闭保留全部（v2.103.0+17））） + **P2 批次（35-gap-closure-plan G8-G15，v2.103.0+15，55 用例：test_trading_calendar.py 15（日历/断K/跳空/面板集成/配置默认）+ test_barra_vol_season_neutral.py 8（波动率/季节中性化）+ test_evolution_loop.py _build_vol_map 3 + test_regime_risk_params.py 14（Regime 风控参数/平滑/RiskManager 注入/MhfConfig）+ test_signal_contract_g12.py 15（to_lots/转换器/validator 新字段））** + **五层调仓控制器（CTA 手册阶段7，v2.104.0+18，20 用例：test_rebalance_controller.py——auto k 三档/区域划分/缓冲保持/NaN 剔除/周期·边界·强制·风控触发/成本拦截·放行·无变化直通/防僵尸平仓与保留/分批拆分/首日建仓状态重置/无变化不触发/持仓天数累计/step 拦截保持/周期再平衡/强制绕过成本门/决策序列化）** + **因子有效性置换检验（手册阶段4/6/9，v2.104.0+18，17 用例：test_permutation_test.py——随机不显著/强因子显著/负相关双侧/种子可复现/置换稳定/NaN 兜底/长度不匹配/样本不足/序列化 + 组合夏普随机不显著/强得分显著/可复现/NaN/长度不匹配）** + **IR 分类门槛（CTA 手册阶段4，v2.104.0+19，8 用例：test_ir_thresholds.py——门槛值/style_tags 映射（carry→期限结构 0.35、value·quality·sentiment→基本面 0.40、momentum 与未知→量价 0.30 默认档）/无元数据默认/dict 与对象一致/缺 style_tags 不崩溃；v2.104.0+25 family 映射用例随家族概念移除删除 13→8）** + **品种差异化成本（CTA 手册阶段1，v2.104.0+19，10 用例：test_cost_model.py +10——按比例覆盖/固定金额转 bps/价格缩放/滑点覆盖/平今仓加收与优惠/无 symbol 兼容/未配置品种回退/自定义表覆盖内置/adjust(symbol=) 生效/冲击覆盖）** + **五层调仓扩展（CTA 手册阶段7，v2.104.0+20，test_rebalance_controller.py 20→31：多空不对称缓冲 k_long/k_short（trend/oscillation 自适应方向）+ 换手率超限自动调整 plan_turnover_control（扩缓冲/降频/双管齐下））** + **Shift错位泄漏校验（CTA 手册阶段2，v2.104.0+20，6 用例：test_shift_leak_test.py——正向对齐通过/错位构造 IC 归零/错位泄漏标记/阈值可配/零未来函数确认/已知泄漏场景）** + **因子文档化（CTA 手册阶段2，v2.104.0+20，8 用例：test_factor_document.py——结构完整性/必填字段/经济逻辑可读/周期与参数写入/序列化/缺省兜底/非法输入/去重更新）** + **极端行情IC失效检验（CTA 手册阶段4，v2.104.0+20，6 用例：test_stress_ic.py——强因子全周期通过/压力期失效拦截/符号翻转拦截/压力样本不足跳过/NaN与长度不匹配兜底/内置压力期定义完整）** + **Regime五指标投票（CTA 手册阶段5，v2.104.0+20，15 用例：test_regime_voting.py——Hurst 计算/ADX 品种级中位数/五指标投票分类/防抖/连续不稳复审/过渡降仓/条件IC/动态vs固定对比/面板构造）** + **期货专属风控事件（CTA 手册阶段8，v2.104.0+20，12 用例：test_futures_risk_events.py——保证金70%降仓指令/提保/熔断/主力切换异常捕获/边界/降级）** + **过拟合排查与绩效归因（CTA 手册阶段9，v2.104.0+20，10 用例：test_oos_checks.py——训练验证夏普衰减≤30% 通过/超限失败/分母零兜底/三段一致性全正/两正一负/无重叠跳过/分年度/分板块贡献）** + **因子生命周期管理（CTA 手册阶段11.3，v2.104.0+20，8 用例：test_factor_lifecycle.py——健康保活/IC衰减触发复审/IR跌破0.3触发/样本不足保守/基准零不除零/NaN跳过/自定义窗口阈值/机制说明）** + **仿真vs回测净值偏差（CTA 手册阶段10，v2.104.0+20，8 用例：test_simulation_gap.py——±5% 内通过/超限失败/不同初始资金归一化/重叠不足/序列输入/空曲线/最终偏差/NaN 不崩溃）** + **资金三级爬坡（CTA 手册阶段11.1，v2.104.0+20，13 用例：test_capital_ramp.py——计划表/比例映射/未知阶段兜底/小仓30天升级/半仓月度稳定/全额不升级/状态汇总/配置一致性）** + **因子质检工作流程（CTA 手册第六章 v1.3，v2.104.0+21，62 用例：tests/factor_engine/qa/ 8 文件——test_pre_entry 7（Q1-Q3 一票否决/Q4-Q10 评分项通过率/空输入/报告）+ test_admission 8（三级准入分类/IR否决/权重上限 30·15·0）+ test_report_template 4（9 部分报告/QA 明细/缺省兜底）+ test_monthly_check 9（M1-M5 五指标/1·2·3 项预警降权 50·30·0/连续3月退役/样本不足不误判）+ test_quarterly_check 8（F1-F6 全样本重算标记/空输入不判失败）+ test_semi_annual 6（D1-D4 逻辑复审/回测重跑/池重构/淘汰库复审）+ test_retirement 9（5 条退役红线独立触发/边界/多红线/NaN 保守）+ test_status_board 11（7 状态流转合法性/存量 active 映射 CORE/看板统计/落库封装））**）** + **因子分组全流程信号聚类化（v2.104.0+24，11 用例：test_http_server.py TestFactorClustering 7——聚类分布区块渲染/簇筛选 chips/分组表头/聚类 TTL 缓存命中/行情不可用降级/簇标注与重排序/HTML 聚类区块；test_factor_clustering.py TestClusterFactorsBySignal 4——cluster_factors_by_signal 全流程统一入口：因子<2 直接 None/参考品种行情不可用降级/高相关信号同簇·低相关分簇/无信号因子独立成簇）**）** + **L3 CAP 安全阀与 OOS 校正评分（v2.104.0+67，+10 用例：test_portfolio_loop.py——TestFactorCompositeScore +3（use_oos_ic=True 时 ic 维度取 oos_extrapolation.new_ic 覆盖样本内/无 OOS 记录回退样本内/默认 False 行为不变）+ TestCapSafetyValve +4（代表数≤cap 不截断/超限按 OOS 校正评分截断/精确截到 cap/空输入）+ TestPortfolioLoop +3（聚类先行接收全部合格因子 25 个/聚类后代表数不超限 CAP 不触发/聚类未减少时 CAP 安全阀 25→20 截断））** + **load_elite_factors JSON 兜底 icir 提取（v2.104.0+68，+3 用例：test_portfolio_loop.py TestLoadEliteFactors——icir 在 evaluation.level_1_backtest 提取/icir 在顶层回退提取（旧版仅查 bt 致顶层 icir 丢失为 0）/两处均缺失默认 0.0 不抛错））** + **信号管道跨因子组合增量校验与语义标注（v2.104.0+69，+8 用例：test_futures_signal_pipeline.py TestFactorSetSignature 3——相同集合签名一致且顺序无关/不同集合签名不同（L3 8→7 因子可检出）/16 位 hex 格式；TestComputeSignalDeltas 5——组合一致正常算增量/组合不一致标记无效且保留昨日得分/无昨日快照兜底/旧格式无签名兼容计算增量））** + **signal_sharpe raw 口径（v2.104.0+73，+3 用例：test_portfolio_loop.py——quality_weight 透传 `_sharpe_raw`（test_raw_sharpe_forwarded）/`pre_weighted_sharpe` 用截断前原始值计算 signal_sharpe 反映真实质量且 1.9 可达（test_signal_sharpe_uses_raw_not_capped）/raw 口径 8.64 在 1.9~12.0 区间通过（test_passes_high_sharpe_within_cap）；原 3.5 max_sharpe 上限测试改 12.0 口径（test_fails_high_sharpe 4.0→13.0））** |
| 集成测试 | 5 | ~200 | strategies 策略层 + 演化循环集成 + 数据源聚合 + 期货同步 |
| E2E | 1 | 10 | test_e2e.py(10) |
|│ 合计 | 100+ | 用例数 | 2220 | 2364+ passed（20 cross-market tests all green，v2.33.0 新增 bincount 边界 12 用例 + g 因子 NaN 防护 12 用例 + repository 事务修复 3 用例，v2.34.0 批量防护 232 用例，v2.38.0 新增 ML 模型层 ~30 用例 + SignalBridge ~25 用例；最新全量 v2.81.0 = 4650+ passed（GAP-055 新增 test_holdout_pool.py 9 用例）（GAP-054 新增 test_dynamic_pool.py 10 用例 + test_tasks.py 任务断言 1 更新）：GAP-I203 新增 test_gru_factor.py 28 用例 + GAP-I202 新增 test_registry.py 7 用例 + GAP-I402 新增 test_live_factor_monitor.py 12 用例 + GAP-I204 二期新增 test_pareto.py 12 用例 + test_symbolic_regression.py 15 用例 + GAP-054 新增 test_dynamic_pool.py 10 用例 + GAP-055 新增 test_holdout_pool.py 9 用例 + GAP-060 新增 test_horizon_analysis.py 12 用例 + test_cross_section_horizon.py 8 用例 + GAP-061/063/064/065/066/067 新增 6 测试文件 64 用例（test_cost_sensitivity.py 12 + test_qc_stats_completion.py 10 + test_portfolio_qc.py 6 + test_ic_weight.py 10 + test_sector_linkage.py 8 + test_overnight_gap.py 8 + test_portfolio_risk_controls.py 10） + GAP-074 新增 test_gap074_operator_diversity.py 4 用例 + GAP-077 新增 test_structure_cluster_quota.py 11 用例 + GAP-079 新增 test_gap079_oos_skip.py 6 用例 + Phase 1.1 P0-2 新增 test_failure_guidance.py 9 用例 + GAP-080 新增 test_shap_optimization.py 7 用例 + Phase 1.2 P0-1 新增 test_success_pattern.py 14 用例 + D.1 新增 test_simulated_portfolio.py 14 用例 + GAP-088 新增 test_macro_eastmoney_source.py 9 用例）） + **P2 PCA 权重接入 equal_weight（v2.103.0+24，5 用例：test_portfolio_loop.py TestSynthesizeSignals 3——equal_weight 消费 Step 1.9 pca_weight 替换均匀等权 + orthogonalized 透传/无 pca_weight 回退 1N + orthogonalized=False/pca_weight 全零回退不除零；test_cli.py 2——portfolio run --enable-pca 存在 + 默认 False）** + **elite_tracker B 级观察期默认关闭（v2.103.0+28，1 用例：test_elite_tracker.py test_b_grade_enters_observing 拆为 test_b_grade_default_active（默认 active）+ test_b_grade_enters_observing_when_enabled（observe_tracker 显式开启）+ 新增 observe_tracker fixture；report/get_by_status 断言适配默认 active）** + **GAP-094/095 统计校准与 blend_power（v2.103.0+34，25 用例：test_regime_calibration.py +10（StatisticalRegimeCalibrator isotonic/platt/binning 拟合预测、未拟合透传、非法标签/NaN 拒绝、未知方法降级、save/load 往返、损坏文件安全加载、Brier）+ test_portfolio_loop_adaptive.py +10（GAP-095 blend_power 幂次归一化/锐化/钝化/默认线性回归保护/端到端锐化 6 + GAP-094 calibration_path 接线 4）+ test_fit_regime_calibration.py 5（hit 标签判定/滚动检测/main dry-run 不落盘/保存+生产消费/样本不足报错））** + **GAP-092 Bridgewater 四象限宏观制度层（v2.104.0+3，19 用例：test_macro_regime.py 15（四象限判定参数化/边界零分归高侧/联合软概率和=1/置信度单调/得分 clip/空数据与 NaN 返回 None/取最新有效值/默认与自定义配置/象限画像完整）+ test_macro_regime_report.py 4（端到端 overheat 判定/--json/数据缺失 unavailable/自定义阈值））** + **信号管道单因子权重上限（v2.104.0+11，4 用例：test_futures_signal_pipeline.py——cap=0.30 截断超限因子且其余承接重分配/None 保持原始/低于等权回退等权/单因子忽略；另 trading_advice 因子集中风险判定加浮点容差 1e-9 避免 cap=0.30 恰好误报）** + **Verifier 换手成本敏感净收益校验（GAP-114，v2.104.0+13，5 用例：test_verifier.py 12→17——成本覆盖放行/成本不足拦截/极端换手净夏普拦截/开关回退绝对硬剔/cost_adjusted 明细字段，Level 1 换手校验由绝对阈值 5.0 升级为净夏普判定）** + **熔断预算传播修复（GAP-115，v2.104.0+14，3 用例：test_evolution_loop.py——重绑传播至 UctSelector（cli 模式失败率阈值 1.0 生效）/100% 失败率禁用态不熔断/重绑保留熔断态按新阈值触发 + owner 动态读取一致性，`EvolutionLoop.budget` property setter 重绑同步传播）** + **阈值校准脚本口径修复（GAP-117，v2.104.0+16，7 用例：test_gap_threshold_calibration.py——换算系数=42（G11 日换手 = mean(\|Δsign\|)/2、月度 = 日 × 42）/catalog·elite·evaluations 日换手反推 /42/候选阈值通过率（le 方向），修复 `/21` 日换手高估 2 倍）** + **信号管道架构对齐（v2.105.0，v2.104.0+17，净 -5 用例）：test_futures_signal_pipeline.py 删 Ridge 权重 16 用例 + 截面 IC 方向校正 3 用例，新增 L3 组合权重严格模式 4（正常/缺失退出/损坏退出/空权重退出）+ L3 组合因子过滤 2 + 因子类别分类 4 + Regime 档位缩放 5；test_data_futures_panel.py 删方向校正日期定位 1 用例** + **WorkFlow 端到端工作流（CTA 手册全流程 UI，v2.104.0+25，28 用例：tests/workflow/ 3 文件——test_stages 7（12 节点/索引连续/依赖合法/动作契约/质检 script 动作/API 形状/未知阶段）+ test_store 8（批次 CRUD/列表排序/阶段动作生命周期/日志追加/JSON 产物解析/纯文本回退/分组）+ test_executor 13（_extract_json 4/占位符解析 3/argv 构造 2/单动作成功·失败同步批次/未知动作/端到端失败停止·成功完成））** + **catalog verify JSON 快照回填对齐（GAP-119，v2.104.0+26，9 用例：tests/test_catalog_verify.py——`_scan_json_snapshots` 3（空目录返回空/跳过下划线文件/损坏 JSON 忽略不阻断）+ `_backfill_json_snapshots` 3（从 DuckDB 写缺失快照/跳过已存在快照不覆盖/未知因子跳过告警）+ `_cmd_catalog_verify --backfill` 集成 3（无 backfill 不一致 rc=1/backfill 后一致 rc=0 且快照生成/ + **family 废弃测试同步（v2.104.0+28，净 -7 用例）：test_factor_db/test_data_layer_repos 删除 7 个已废弃 API 测试（get_by_family x3 / get_family_distribution x3 / get_by_family_with_all_filters x1，family 分组统一走信号聚类）+ 按新签名同步 6 个 write_seed_lineage（去 seed_family 参数）/resolve_seed_lineage（6 参新签名 + 返回键去 seed_family）用例，13 个 v2.104.0+25 预存失败清零，定向 110 passed 全绿** + **WorkFlow UI assets 静态托管 + run 状态同步（v2.104.0+29，净 +7 用例）：test_http_server.py TestWorkflowStaticAssets 5（/workflow 返回 index.html / assets JS 200 text/javascript / CSS text/css / 未知 assets 回退 index / 路径越界拒绝回退）+ test_executor.py +2（_sync_run_status 同阶段取最新记录：旧 failed 不主导 / 最新 failed 仍判失败）** + **字段消费字典驱动的每日全字段同步（v2.104.0+31，37 用例：tests/config/test_futures_field_consumption.py 11——三组字段完整性（kline 17/fundamental 9/term_structure 4）/元数据必填/唯一性/重复报错/分组·通道映射/期限结构消费方契约/新增消费字段必须登记；tests/test_futures_fundamental_sync.py 18——计量单位换算 7（同单位/克·千克→吨/吨→克/股指点/单位不匹配/品种单位映射）+ 现货价 WebSearch 补充三项校验 5（成功/正确性偏离拒绝/单位不匹配拒绝/新鲜度过期拒绝/解析失败）+ 面板构建 2（合并列结构/provider 失败 NaN 列）+ sync 集成 4（成功流/现货缺失触发 filler 写入/校验失败记 missing_spot/upsert 去重）；tests/test_futures_term_structure.py 8——合约月份解析 3/截面计算 3（价差与展期收益/单合约不足/库缺失，含合约日期不同步与历史旧合约过滤）/同步集成 2（success·no_section/upsert 去重）） + **横截面评估全矩阵化（plans/37，24 用例：test_panel_vector.py——预对齐面板口径/向量化 IC vs 逐日 spearmanr 对照（NaN 缺口·常数·低覆盖率·随机回归）/完整流水线对照/接入开关 on·off 评估产出一致 + 中性化路径）** + **算子 native 向量化改写（plans/37 Phase 2 Step 2 批 1，v2.104.0+54，+28 用例：test_rolling_native.py——7 个 P0 热算子（ts_product/ts_zscore/ts_min_max_diff/ts_cum_max/max_drawdown/ts_argmin/self_corr）sliding_window_view 内核 vs 旧 rolling.apply oracle 逐位对照（随机/含 NaN/常数/单调/头部缺口/短序列 × 多窗口 + 空序列边界），min_periods 按非 NaN 观测计数语义对齐；ops_library 9 处伪 apply(np.sqrt) 直改 np.sqrt()）** + **seed 模板算子向量化改写（plans/37 Step 2 批 2，v2.104.0+55，+56 用例：test_seed_ops_native.py——registry.ts_argmax/ts_decay_linear + seed_loader/seed_data 双模板 7 算子（ts_argmax/ts_argmin/ts_rank/ts_product/decay_linear/highday/lowday）min_periods=1 语义（非 NaN 观测计数门控）+ 空/单元素边界逐位对照；registry 对照 8 用例并入 test_rolling_native.py）** + **L3 组合重算性能优化（plans/40，v2.104.0+63，+54 用例：test_numba_kernels.py 32→86（ts_zscore/ts_cvar 1D 内核重新接入 feature_ops/ops_library——算子级 wired vs oracle 零漂移 × 多窗口 × 场景 + 面板 2D 对照 + 依赖缺失回退现值零漂移）+ 新增 test_l3_signal_service.py 16 用例（SignalMatrixBundle 契约/3D 矩阵 vs 逐品种逐值一致/前向收益口径/DuckDB corr 对照/增量重算只算新因子/round-trip/use_store=False 回退）+ test_factor_clustering 接 signal_cache 参数；受影响回归 880 用例全绿 + factor_engine 全目录 not-slow 4882 passed）** + **能源链退化检测 apply 落库（v2.104.0+64，+6 用例：test_energy_chain_degradation_apply.py——CRITICAL 降级 is_elite=false/status=degraded + JSON 移入 _deprecated + status_history 留痕 / CRITICAL 无 JSON 仍降级 / WARN→shadow 观察池 JSON 保留 / OK→retain 追加留痕 / 因子不存在 failed 不中断 / TeeLogger 可调用写文件；隔离 DuckDB + tmp_path，6 passed 全绿）** |

---

## 2. 测试目录结构

```
tests/
├── __init__.py
├── conftest.py                      # 全局 fixture
│
├── core/                            # 3 个测试文件
│   ├── __init__.py
│   ├── test_atomic.py               # 原子操作测试
│   ├── test_contracts.py            # core contracts 测试
│   └── test_enums.py                # enums 测试
│
├── scenarios/                      # 1 个测试包（20 用例）
│   ├── __init__.py
│   ├── definitions.py              # 23 个宏观行为场景定义
│   ├── validator.py                # 场景验证器
│   └── test_scenarios.py           # 场景测试用例
│
├── factor_engine/                   # 26 个测试文件
│   ├── __init__.py
│   ├── conftest.py                  # factor_engine fixture
│   ├── test_backtest_pipeline.py    # 端到端回测流水线测试（标准/传统代码约定 + DatetimeIndex 兼容）
│   ├── test_contracts.py            # 契约定义测试
│   ├── test_evaluation_chain.py     # 三级评估链测试
│   ├── test_panel_vector.py         # 横截面评估全矩阵化测试（plans/37+39，32 用例：预对齐面板/向量化 IC vs 逐日 spearmanr 对照/完整流水线对照/接入开关 on·off 产出一致/面板化执行引擎/plans/39 §11 回退契约——评估链不调用 execute_factor_panel 打桩抛错）
│   ├── test_gap_panel_2d.py         # 缺口面板 2D 化滚动内核对照测试（plans/39，39 用例：计数回溯窗无缺口零漂移/内部·头部·尾部缺口与逐品种逐位一致/全 NaN/窗口超长/n<min_periods/DataFrame 面板/算子面板化与回退族对照）
│   ├── test_rolling_native.py       # 算子 native 向量化改写对照测试（plans/37 Phase 2 Step 2 批 1，28 用例：7 个 P0 热算子 × 新旧实现逐位对照——随机/含 NaN/常数/单调/头部缺口/短序列 × 多窗口 + 空序列边界）
│   ├── test_seed_ops_native.py      # seed 模板算子向量化改写对照测试（plans/37 Step 2 批 2，56 用例：ts_argmax/ts_argmin/ts_rank/ts_product/decay_linear/highday/lowday × seed_loader·seed_data 双模板 × 新旧逐位对照 + 空/单元素边界）
│   ├── test_ops_native_batch3.py    # 算子 native 向量化改写对照测试（plans/37 Step 2 批 3，48 用例：ops_library 真滚动回调 14 个函数 + regime_features._rolling_autocorr + gp_evolver 模板 ts_product × 3 窗口 × 7 场景逐位对照 + DataFrame 面板路径 + acf lag 1/3）
│   ├── test_numba_kernels.py        # numba 算子内核对照测试（plans/38 + plans/40 C 层，86 用例：ts_rank 算子级 wired vs oracle 零漂移 × 8 场景 × 3 窗口 + 1D 内核 vs pandas oracle + 接入断言走快速路径 + 面板 2D 零漂移/路径接入/缺口回退 + DataFrame 回退不回归；plans/40 重新接入 ts_zscore/ts_cvar 1D 内核——feature_ops/ops_library 快速路径 + 依赖缺失回退现值零漂移 + 面板 2D 对照）
│   ├── test_l3_signal_service.py    # L3 信号矩阵服务测试（plans/40 B/D 层，16 用例：SignalMatrixBundle 契约/3D 矩阵构建 vs 逐品种逐值一致/参考品种 2D 提取/前向收益口径/DuckDB corr 与 np.corrcoef 对照/增量重算只算新因子/未变更因子复用/持久化 round-trip/use_store=False 回退）
│   ├── test_evolution_loop.py       # L2 主循环测试
│   ├── test_experience_chain.py     # 经验链测试
│   ├── test_factor_program.py       # 因子程序（安全沙箱）测试
│   ├── test_failure_pattern.py      # 失败模式聚类分析测试
│   ├── test_macro_evolution.py      # 宏观演化测试
│   ├── test_meta_loop.py            # L1 元循环测试（plans/41 +9 用例：子链分批/实时链知识/LLM 分批/预算配置）
│   ├── test_web_search_extractor.py # WebSearch 动态因子源测试（plans/41 A/A3，10 用例：无 llm 空/暂停空/搜索→LLM 提取/prompt 去标签/搜索失败空/管道注册/max_factors 默认 20/显式覆盖 30/FTSConfig 注入 35/prompt chain_focus）
│   ├── test_monitor.py              # factor_engine monitor 测试
│   ├── test_portfolio_loop.py       # L3 组合循环测试（含 CAP 安全阀 / OOS 校正评分，v2.104.0+67）
│   ├── test_program.py              # Program.md 测试
│   ├── test_regime.py               # 市场制度检测（RegimeAwareSelector）
│   ├── test_sector_regime.py        # 产业链级制度检测（SectorRegimeSelector，15 用例）
│   ├── test_seed_pool.py            # 种子池测试
│   ├── test_uct_selection.py        # UCT 树搜索父因子选择测试（34 计划 Phase 46a 起覆盖 evolution_uct.py EvolutionUctMixin，行为等价无新增用例）
│   ├── test_factor_inspector.py     # FactorInspector 定时巡检测试
│   ├── test_factor_lifecycle.py     # 因子生命周期管理测试（CTA 手册阶段11.3，8 用例）
│   ├── test_verifier.py             # Verifier 锁定协议测试
│   ├── test_rebalance_controller.py # 五层调仓控制器测试（CTA 手册阶段7，31 用例）
│   ├── test_permutation_test.py     # 因子有效性置换检验测试（17 用例）
│   ├── test_ir_thresholds.py        # 因子 IR 分类门槛测试（CTA 手册阶段4，13 用例）
│   ├── test_shift_leak_test.py      # Shift 错位泄漏校验测试（CTA 手册阶段2，6 用例）
│   ├── test_factor_document.py      # 因子文档化测试（CTA 手册阶段2，8 用例）
│   ├── test_stress_ic.py            # 极端行情 IC 失效检验测试（CTA 手册阶段4，6 用例）
│   ├── test_regime_voting.py        # Regime 五指标投票检测测试（CTA 手册阶段5，15 用例）
│   ├── test_futures_risk_events.py  # 期货专属风控事件测试（CTA 手册阶段8，12 用例）
│   ├── test_oos_checks.py           # 过拟合排查与绩效归因测试（CTA 手册阶段9，10 用例）
│   │
│   ├── qa/                          # 因子质检工作流程测试（CTA 手册第六章 v1.3，62 用例）
│   │   ├── __init__.py
│   │   ├── test_pre_entry.py        # 入库前质检 Q1-Q10（7 用例：全过/一票否决Q1·Q3/评分项通过率/空输入/报告）
│   │   ├── test_admission.py        # 三级准入分类（8 用例：核心/候选/淘汰/IR否决/权重上限/标签/汇总）
│   │   ├── test_report_template.py  # 9 部分质检报告（4 用例：章节/完整因子/QA明细/缺省兜底/参数块）
│   │   ├── test_monthly_check.py    # 月度复检 M1-M5（9 用例：全健康/1·2·3项预警/连续3月退役/样本不足/IR/M3衰减）
│   │   ├── test_quarterly_check.py  # 季度复检 F1-F6（8 用例：全健康/F1·F3·F4·F5·F6标记/空输入/多标记）
│   │   ├── test_semi_annual.py      # 半年度复检 D1-D4（6 用例：全健康/逻辑失效/回测衰减/池重构/淘汰库复审）
│   │   ├── test_retirement.py       # 退役判定 5 红线（9 用例：无红线/各红线独立触发/边界/多红线/NaN/配置/报告）
│   │   └── test_status_board.py     # 7 状态机+看板+落库（11 用例：合法/非法流转/active兼容/权重/看板统计/落库封装）
│   │
│   ├── operator_evolution/          # 算子演化引擎测试（C.4，13 用例）
│   │   └── test_operator_evolution.py  # 初始化合法性/进化收敛/交叉变异/OPERATOR 产物/罚分/缓存/集成
│   │
│   └── expr_dsl/                    # FTS-Expr DSL 测试（7 文件）
│       ├── test_parser.py           # 解析器测试
│       ├── test_registry.py         # 算子注册表测试（含 GAP-S10 双注册表一致性 + GAP-S12 A 股特有算子 + GAP-L401 高阶算子 + GAP-I202 ts_slope/ts_quantile/单一事实源）
│       ├── test_validator.py        # 校验器测试
│       ├── test_compiler.py         # 编译器测试
│       ├── test_executor.py         # 执行器测试
│       ├── test_factory.py          # 算子因子工厂测试
│       └── test_seed_analyzer.py    # 种子表达式静态 PIT 审计（GAP-S09，14 用例）
│
├── pipeline/                        # 2 个测试文件
│   ├── __init__.py
│   ├── test_base.py                 # 管线基础测试
│   └── test_factor_combiner.py      # 因子组合器测试
│
├── scheduler/                       # 4 个测试文件
│   ├── __init__.py
│   ├── test_engine.py               # 调度引擎测试
│   └── test_tasks.py                # 调度任务测试
│
├── strategies/                      # 3 个测试文件
│   ├── __init__.py
│   ├── test_base_v2.py              # 策略基类测试
│   ├── test_multi_factor.py         # 多因子策略测试
│   └── test_strategy_evolution.py   # 策略进化测试
│
├── factor_db/                       # 4 个测试文件
│   ├── __init__.py
│   ├── test_schema.py               # DuckDB Schema 测试
│   ├── test_repository.py           # FactorRepository CRUD 测试
│   ├── test_correlations.py          # 因子相关性矩阵测试
│   └── test_yaml_loader.py          # YAML 种子因子加载测试
│
├── test_cli.py                      # CLI 入口测试
├── test_config_settings.py          # 配置管理测试
├── test_data.py                     # 数据层测试
├── test_data_futures_panel.py       # 期货面板 common_dates 多数对齐测试
├── test_futures_signal_pipeline.py  # 期货信号管道 L3 组合权重 + Regime 档位缩放 + 组合合成测试
│
├── scripts/                         # 管道脚本测试（2 个文件）
│   ├── test_futures_signal_pipeline_db.py     # 期货信号管道 DuckDB 因子资产库读取测试（GAP-097，v2.103.0）
│   └── test_futures_signal_pipeline_macro.py  # 期货信号管道宏观注入接线测试（GAP-088）
│
├── workflow/                        # CTA 手册 WorkFlow 端到端工作流测试（v2.104.0+29，30 用例）
│   ├── __init__.py
│   ├── test_stages.py               # 阶段定义（11 阶段 + 质检闭环 = 12 节点/依赖合法性/动作契约/API 形状）
│   ├── test_store.py                # SQLite 持久化（批次 CRUD/阶段动作生命周期/日志追加/JSON 产物解析）
│   └── test_executor.py             # 执行器（JSON 提取/占位符解析/argv 构造/单动作真实执行/run 状态按阶段最新同步/失败停止/端到端成功）
│
├── test_cross_market.py             # 跨市场泛化验证测试（20 用例，含数据适配/分类/报告/集成）
├── test_llm.py                      # LLM 客户端测试
├── test_monitor.py                  # 项目级 monitor 测试
│
├── live_trade/                      # 5 个测试文件（D.1 + D.2 + CTA 手册阶段10/11.1）
│   ├── __init__.py
│   ├── test_simulated_portfolio.py  # 模拟仓测试（19 用例：开/加/减/平/反手、盯市、风控/干预拦截、因子归因、回放引擎、合约乘数/市场推断 + SQLite 存取/恢复/PaperTrader 持久化 + D.2 组合级风控集成）
│   ├── test_book_matching.py        # tick 盘口撮合测试（26 用例：build_book_from_ticks 聚合/排序/截断 + 逐档消耗/深度不足/空盘口降级/滑点自然性 + gateway 部分成交 PARTIAL→FILLED + P2 限价单/集合竞价）
│   ├── test_simulation_gap.py       # 仿真 vs 回测净值偏差对比测试（CTA 手册阶段10，8 用例：±5% 内通过/超限失败/不同初始资金归一化/重叠不足/序列输入/空曲线/最终偏差/NaN 不崩溃）
│   └── test_capital_ramp.py         # 资金三级爬坡测试（CTA 手册阶段11.1，13 用例：计划表/比例映射/未知阶段兜底/小仓 30 天升级/半仓月度稳定升级/全额不升级/状态汇总 4 分支/配置一致性）
│
├── factor_engine/                   # 因子引擎测试（D.2 新增 1 文件）
│   └── test_neutralization.py       # 横截面中性化测试（14 用例：行业组内去均值/单股行业归零/市值回归残差/逐日/映射缺失降级/入参不可变）
│
├── risk/                            # 风控测试（D.2 新增 1 文件）
│   └── test_portfolio_metrics.py    # 组合级风控指标测试（17 用例：杠杆/仓位/保证金/有效持仓/波动-VaR-CVaR/回撤/连续亏损 + 三级预警 WARN/BLOCK/FORCE + 空数据降级）
│
├── scripts/                         # 脚本测试（D.2 新增 1 文件）
│   └── test_calibrate_book_vs_bps.py# book vs bps 标定测试（7 用例：可复现/价差敏感性单调/部分成交率/滑点分布/报告章节/bps 折算）
```

---

## 3. pytest 配置

定义在 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=fts --cov-report=term-missing -v"
markers = ["slow: 重量级真实演化/回测测试（日常回归默认跳过，全量验收必跑）"]
```

执行命令：

```bash
# 运行全部测试并显示覆盖率
python -m pytest tests/ --cov=fts --cov-report=term-missing

# 日常回归：跳过 slow 重量级测试（真实演化/回测），单进程执行规避 DuckDB 写锁冲突
python -m pytest tests/ -m "not slow" -q -o addopts="" -p no:cacheprovider

# 全量验收（含 slow 真实演化测试）：xdist 并行加速（锁冲突类测试单进程定向复核）
python -m pytest tests/ -q -o addopts="" --timeout=600 --tb=line -p no:cacheprovider -n 4 --dist loadfile

# 运行指定模块测试
python -m pytest tests/factor_engine/ -v

# 运行单文件测试
python -m pytest tests/factor_engine/test_verifier.py -v
```

**slow 分级说明（2026-08-13）**：`@pytest.mark.slow` 标记重量级真实演化/回测测试（当前 26 个，集中于 `test_evolution_loop.py`），单进程下单个测试可达数分钟至数十分钟（真实种子评估 + elite 相关性扫描）。日常回归用 `-m "not slow"` 跳过；全量验收必跑。**DuckDB 并发约束**：xdist 多 worker 并发写 `data/state.duckdb` / `data/factor_catalog_futures.duckdb` 会触发文件锁冲突（`IOException: 另一个程序正在使用此文件`，非代码缺陷），锁冲突类测试需单进程定向复核。

---

## 覆盖统计（v2.47.0）

### 总体统计

| 指标 | 值 |
|:-----|:---|
| Total statements | 20797 |
| Overall coverage | 94% |
| 测试用例数 | 4453 基线（4403 + v2.72.0 新增 50：GAP-I206 L2 准入去冗余 10（test_l2_elite_redundancy.py——方法级 7：高相关命中/负高相关 abs 判断/低相关放行/空 elite 放行/索引文件跳过/容量护栏/执行失败容错 + 集成 3：shadow 高相关拦截不落盘/种子跳过检查正常晋升/低相关正常晋升）+ GAP-I204 GP 多目标适应度 7（TestGapI204MultiObjective——turnover/decay 字段填充/换手度量平滑 vs 振荡/同 IC 量级换手惩罚/系数放大 ×2/衰减惩罚/端到端 evolve multi_objective）+ GAP-I206 正交化闭环 10（test_l2_orthogonalize.py——方法级 4：残差生成/残差与参照正交/保留比不足拒绝/参照缺失降级 + 集成 3：正交化版本入库/残差不合格拒绝兜底/开关关闭拒绝 + 配置 1 + L3 消费 2：正交化因子不重复剔除/非正交化仍剔除）+ GAP-I206 正交基底 19（test_orthogonal_basis.py——IC 斜率 5：上升正斜率/下降负斜率/平坦近零/点数不足回零/归一化范围 + 衰减分级 5：normal/observe/retired 判定/update 写入分级字段/auto_retire 按级退役 + 基底管理器 7：空基底/注册加载/上限淘汰/重复注册更新/Gram-Schmidt 正交性/弱候选拒绝/无基底返回 None + L2 集成 2：开关关闭返回 None/基底成员缺失降级）+ GAP-L401 corr/cross_section_rank 算子 4（元数据 2 + 功能 2，对齐验收 ≥6 算子）；v2.69.0 新增 27：test_seed_analyzer 14 + test_registry GAP-S10/S12 6 + TestGapS11OperatorFirst 7 + v2.70.0 新增 14：GAP-I301 股票 L3 组合层 9（TestStockL3PortfolioLayer 6 组件复用性/market 过滤/成本模型 net 为正/stock run/stock_regime + TestCmdPortfolioRunStock 3 股票分支触发信号管道/非零 rc 告警/状态不触发）+ GAP-I205 微演化两阶段漏斗 5（TestStagedFunnel 粗筛淘汰/精筛通过/no-optuna 回退/staged 与非 staged evolve_micro）） |
| 测试文件数 | 118+ |
| 种子因子数 | 期货 YAML 185（20 文件，按 style_tags 分类，含 fut_macro_import）+ 硬编码兜底 81（按 style_tags 分类）；股票种子定义保留兼容（9 内置 + 636 外部，管线已剥离至 fts-stock） |
| 精英因子数 | 674（DuckDB 存储，v2.33.0 淘汰 fut_macro_export 类因子 6 个） |
| 期货专用种子 | YAML 185 因子（style_tags 分类，主路径）/ 硬编码 81 因子（style_tags 分类，兜底） |
| 因子相关性记录 | 4950 条（100 因子 × 两两组合） |

### 模块覆盖详情

```
Name                                       Stmts   Miss  Cover
──────────────────────────────────────────────────────────────
fts\__init__.py                               17      0   100%
fts\bridge\signal_bridge.py                  133     13    90%
fts\cli.py                                 1221      1    99%
fts\config\settings.py                        96      1    99%
fts\core\atomic.py                            44      0   100%
fts\core\contracts.py                         58      0   100%
fts\core\enums.py                            34      0   100%
fts\cross_market\data_adapter.py              78     35    55%
fts\cross_market\engine.py                   270     18    93%
fts\data.py                                  108     16    85%
fts\data_fundamental.py                      147     11    93%
fts\data_futures.py                          482      2    99%
fts\data_mcp.py                              120      0   100%
fts\data_mcp_bridge.py                       193      3    98%
fts\data_sources\aggregator.py               393      0   100%
fts\data_sources\akshare_minute_source.py     68      0   100%
fts\data_sources\base.py                      46      0   100%
fts\data_sources\fusion.py                   152     15    90%
fts\data_sources\ifind_source.py             199     31    84%
fts\data_sources\macro_aligner.py             39      2    95%
fts\data_sources\migrate.py                   71      0   100%
fts\data_sources\tdx_minute_source.py        144     48    67%
fts\data_sources\tq_source.py                142     27    81%
fts\data_sources\tqsdk_source.py              86      0   100%
fts\data_sources\tqsdk_tick_source.py         74     20    73%
fts\data_sources\wind_source.py               95     12    87%
fts\factor_engine\ablation.py                139      4    97%
fts\factor_engine\adaptive_weight.py          55      2    96%
fts\factor_engine\audit.py                   294     14    95%
fts\factor_engine\backtest_pipeline.py       461     39    92%
fts\factor_engine\capital_allocator.py        92      8    91%
fts\factor_engine\causal_validator.py        141     16    89%
fts\factor_engine\contracts.py               416     45    89%
fts\factor_engine\cost_model.py               68      0   100%
fts\factor_engine\cost_simulator.py           57      6    89%
fts\factor_engine\evaluation_chain.py        275     12    96%
fts\factor_engine\evolution_loop.py         1167    229    80%
fts\factor_engine\experience_chain.py        169      2    99%
fts\factor_engine\expr_dsl\compiler.py        32      0   100%
fts\factor_engine\expr_dsl\executor.py        45      9    80%
fts\factor_engine\expr_dsl\factory.py         19      0   100%
fts\factor_engine\expr_dsl\parser.py          63      1    98%
fts\factor_engine\expr_dsl\registry.py        74      0   100%
fts\factor_engine\expr_dsl\validator.py       69      7    90%
fts\factor_engine\extractors\base.py         137      2    99%
fts\factor_engine\extractors\futures_pipeline.py 173    3   98%
fts\factor_engine\extractors\stock_pipeline.py   162    6   96%
fts\factor_engine\factor_clustering.py       236     86    64%
fts\factor_engine\factor_db\lineage.py       154      7    95%
fts\factor_engine\factor_db\migrate_from_json.py 117  32    73%
fts\factor_engine\factor_db\repository.py    569     88    85%
fts\factor_engine\factor_db\schema.py         78      0   100%
fts\factor_engine\factor_inspector.py         95      7    93%
fts\factor_engine\factor_optimizer.py        548     29    95%
fts\factor_engine\factor_program.py          194      9    95%
fts\factor_engine\factor_quality_card.py     250      4    98%
fts\factor_engine\factor_screener.py          71      9    87%
fts\factor_engine\feature_importance.py       59      6    90%
fts\factor_engine\feature_ops.py             258      0   100%
fts\factor_engine\feedback_loop.py           188     14    93%
fts\factor_engine\gp_evolver.py              347     15    96%
fts\factor_engine\macro_evolution.py          90      7    92%
fts\factor_engine\meta_loop.py               624     39    94%
fts\factor_engine\micro_evolution.py          86      3    97%
fts\factor_engine\monitor.py                 103      0   100%
fts\factor_engine\operator_evolution.py      261      7    97%
fts\factor_engine\portfolio_constructor.py    60      5    92%
fts\factor_engine\portfolio_loop.py         1358      3    99%
fts\factor_engine\program.py                  86      0   100%
fts\factor_engine\regime.py                  580      9    98%
fts\factor_engine\regime_features.py         134      4    97%
fts\factor_engine\regime_hmm.py              312     14    96%
fts\factor_engine\report_generator.py         80      1    99%
fts\factor_engine\risk_attributor.py          61      7    89%
fts\factor_engine\robustness.py              153      0   100%
fts\factor_engine\seed_data\loader.py         99      0   100%
fts\factor_engine\seed_data_futures_full.py  119      3    97%
fts\factor_engine\seed_loader.py             183     13    93%
fts\factor_engine\seed_pool.py               259     24    91%
fts\factor_engine\shap_analyzer.py           152     17    89%
fts\factor_engine\signal_contract.py          89      4    96%
fts\factor_engine\signal_generator.py         70      8    89%
fts\factor_engine\standardizer.py            192      7    96%
fts\factor_engine\state.py                    97      0   100%
fts\factor_engine\stress_test.py              94      0   100%
fts\factor_engine\verifier.py                 65      3    95%
fts\factor_engine\walk_forward.py            103      0   100%
fts\llm.py                                   339      0   100%
fts\ml\models.py                              80     11    86%
fts\ml\trainer.py                             93      7    92%
fts\monitor\data_quality_monitor.py          271     49    82%
fts\monitor\elite_tracker.py                 260      6    98%
fts\monitor\http_server.py                   547     18    97%
fts\monitor\live_factor_monitor.py            51      1    98%
fts\monitor\logic_monitor.py                 181     15    92%
fts\monitor\prometheus_metrics.py            192      0   100%
fts\risk\risk_manager.py                     100      0   100%
fts\risk\simulated_adapter.py                 34      3    91%
fts\scheduler\engine.py                      100      0   100%
fts\scheduler\hotswap.py                      61      0   100%
fts\scheduler\jobs.py                        292     35    88%
fts\scheduler\tasks.py                        49      0   100%
fts\scheduler\watchdog.py                     57      0   100%
TOTAL                                      20326   1254    94%
```

### 模块覆盖统计

> 完整逐模块清单见上方"模块覆盖详情"表。以下按覆盖率区间汇总（v2.47.0 实测，TOTAL 94%；v2.88.0 GAP-F16 后 TOTAL 94.31%，14 个缺口模块全部 ≥90%）。

| 覆盖率区间 | 模块数 | 代表模块 |
|:-----------|:-------|:---------|
| **100%** | 31 | `core/atomic` `core/contracts` `core/enums` `data_mcp` `data_sources/{aggregator,base,migrate,tqsdk_source,akshare_minute_source}` `factor_engine/{cost_model,feature_ops,monitor,program,robustness,state,stress_test,walk_forward}` `expr_dsl/{compiler,factory,registry}` `factor_db/schema` `llm` `monitor/prometheus_metrics` `risk/risk_manager` `scheduler/{engine,hotswap,jobs,tasks,watchdog}` |
| **95%-99%** | 22 | `cli(99%)` `data_futures(99%)` `portfolio_loop(99%)` `extractors/base(99%)` `report_generator(99%)` `experience_chain(99%)` `data_mcp_bridge(98%)` `extractors/futures_pipeline(98%)` `factor_quality_card(98%)` `regime(98%)` `elite_tracker(98%)` `live_factor_monitor(98%)` `ablation(97%)` `http_server(97%)` `micro_evolution(97%)` `operator_evolution(97%)` `regime_features(97%)` `seed_data_futures_full(97%)` `evaluation_chain(96%)` `adaptive_weight(96%)` `gp_evolver(96%)` `regime_hmm(96%)` |
| **90%-94%** | 27 | `meta_loop(94%)` `data_fundamental(93%)` `cross_market/engine(93%)` `factor_inspector(93%)` `feedback_loop(93%)` `seed_loader(93%)` `backtest_pipeline(92%)` `macro_evolution(92%)` `ml/trainer(92%)` `logic_monitor(92%)` `portfolio_constructor(92%)` `seed_pool(91%)` `capital_allocator(91%)` `simulated_adapter(91%)` `bridge/signal_bridge(90%)` `fusion(90%)` `feature_importance(90%)` `expr_dsl/validator(90%)` 等 |
| **<90%（缺口）** | 0 | 无（v2.88.0 GAP-F16 补齐：`cross_market/data_adapter`/`factor_clustering`/`tqsdk_tick_source`/`factor_db/migrate_from_json`/`evolution_loop`/`data_quality_monitor`/`ifind_source`/`data`/`factor_db/repository`/`ml/models`/`wind_source`/`factor_screener`/`causal_validator`/`contracts` 14 个缺口模块全部 ≥90%） |

> 注：v2.88.0（GAP-F16）三分组补齐 14 个 <90% 模块测试 +341 用例（外部数据源网络/鉴权/超时/降级兜底 mock + 核心引擎异常分支 + 参数校验降级路径），全量回归 5132 passed，TOTAL 覆盖率 94.31%，缺口清零。
>
> 注：v2.89.0（同步范围扩大）——`test_sync_futures_task.py` `test_default_symbols_is_core_subset` 更名为 `test_default_symbols_is_full_universe`（默认同步断言由 core 25 改为 FUTURES_SUBSET 全品种 82），用例总数不变（5132）。

> 注：v2.99.0（GAP-072 L3 与信号管道解耦）——新增 	ests/config/test_weight_recompute.py 5 用例（权重重算日判定 daily/weekly/未知 cadence 回退）+ 	ests/scripts/test_signal_common.py TestWeightSnapshot 4 用例（快照存读往返/缺失/损坏降级/冻结过滤）+ 	ests/factor_engine/test_portfolio_loop.py 冻结/强制重算/冷启动保护 3 用例；更新 	ests/scheduler/test_tasks.py（默认任务 12→14，L3 cron 改每周五 + 新增期货/股票信号管道独立任务）/	est_jobs.py（L3 job 不再联动信号管道、独立任务入口 called_once）/	est_cli_extra.py（解绑 + 冻结提示）/	est_engine.py（add_job 12→15），受影响 375+243 passed 全绿，全量回归 5266+ passed。v2.104.0+7 补充：test_weight_recompute.py +1（默认 cadence=daily）+ test_futures_signal_pipeline.py TestClassifyDeltaMoves 6 + TestLoadSignalFactors 2 + test_futures_signal_pipeline_db.py 1（JSON 回退移除断言），受影响 43+388 passed 全绿。

> 注：v2.101.0（GAP-076 信号管道截面标准化 + 卷积修复）——新增 tests/scripts/test_signal_common.py 10 用例：TestNormalizeSignalMatrix 8（none 不改 / zscore 截面均值 0 方差 1（ddof=0）/ 常数截面置 0 / rank 值域 [-1,1] 与保序 / NaN 写回 0 / 非法 method 抛 ValueError / 空矩阵降级 / DatetimeIndex 输入回归）+ TestWeightSnapshot 2（normalize 字段存读往返 / 旧快照无 normalize 默认 none 向后兼容），test_signal_common 17→26 全绿；volatility_reversion_g2 因子卷积 bug 修复后沙箱编译通过、最新截面恢复区分度（std 0→0.238）；受影响定向回归全绿。

> 注：v2.101.0（D.2 偏差 b：Regime 自适应权重接线）——新增 tests/scripts/test_signal_common.py 17 用例（当前 50）：TestBuildStockRegimePanels 6（行业面板等权聚合 / symbol 后缀对齐 / 映射后缀对齐（真实数据格式回归）/ 风格 large/small 分位 / 映射缺失空面板 / 样本不足降级）+ TestApplyStockRegimeWeights 6（style 倍率 large_cap momentum×0.9 quality×1.2 / 键集合保持 / 空 regime 原样返回 / 未知 regime 倍率 1.0 / family 维度兼容）+ TestRegimeChainIntegration 1（面板构造→StockRegimeSelector 检测→权重调整全链路）+ TestWeightSnapshot 2（regime 字段存读往返 / 旧快照无 regime 默认 none）+ TestNeutralizeSignalMatrix 2（后缀键行业/市值对齐回归——修复 P0.1 中性化适配器静默空转：`data/industry_map.json` 后缀键 vs 面板纯代码键，修复后真实数据实测「industry 已应用到 129 因子×80 交易日」）；tests/test_config_settings.py +2 用例（stock_signal_regime 默认 none / FTS_STOCK_SIGNAL_REGIME env 覆盖 auto），test_config_settings 65 passed；真实管道冒烟 `--regime auto`（CSI300 6 只）实测检测 `sector_concentrated (conf=50%, method=stock_rule)` 并完成权重调整；受影响模块/集成测试 + ruff check 通过。

> 注：v2.101.0（GAP-078 TQ 探活进程级重试）——新增 tests/test_data.py TestTqStockAvailable 6 用例（首次成功缓存 / 瞬时抖动重试恢复 / 全失败冷却不重探 / 冷却期满重探恢复 / 合法响应解析 / 异常降级），test_data.py 75 passed 全绿；附带 zscore vs rank 历史对比（81 交易日 IC 差 0.0015 噪声量级，rank 略稳健）。

> 注：v2.104.0（35-gap-closure 全链路缺口关闭完成里程碑）——全量回归 `pytest tests/ -m "not slow"` **6621 passed / 0 failed / 26 deselected**（25 分 35 秒）；3 个回归失败全部处置：① test_symbol_holdout（auditor property fallback，34 计划 47i 修复）；② test_package_init::test_version_format（SemVer build 段制适配，v2.103.0 修订）；③ tests/test_duckdb_reader.py `test_read_while_writer_open` 更名 `test_read_after_writer_closed`（E.4 S1 语义适配——写短生命周期后读池 read_only 可打开并读到最新数据，写连接打开期间 read_only 打不开属 DuckDB 配置约束，GAP-090 遗留项关闭）。

> 注：v2.104.0+1（G11 换手回填 + 阈值启用）——① `cross_section_evaluate_backtest` 修复横截面路径硬编码 `turnover_monthly=0.0` 的键缺失：现计算 `turnover_daily`（信号翻转率 mean(|Δsign|)/2，NaN 安全 np.nanmean）与 `turnover_monthly=daily×42`；② `_evaluate_cross_section`（evolution_futures）补 G11 日换手硬剔除判定（时序路径 L720-727 已有）；③ `FTSConfig.factor_turnover_daily_max` 默认 None → **0.30**（`scripts/backfill_turnover.py` 对 83 active 期货因子真实横截面面板校准：P50=0.138/P90=0.320/P95=0.456，≤0.30 通过率 88%，剔除 top ~12%）；④ 换手回填入库：69 个因子 turnover_monthly/level_1_turnover 更新；⑤ test_g11_turnover_gate.py **+4 横截面用例**（turnover 注入/常数兜底/G11 拒收/默认关闭），受影响回归 75+258 passed 全绿。

> 注：v2.104.0+5（G11 默认关闭，配置类变更）——`FTSConfig.factor_turnover_daily_max` 默认 0.30→None（G11 换手硬剔除语义属股票市场，期货换手成本低默认关闭，env `FTS_FACTOR_TURNOVER_DAILY_MAX` 可覆盖保留可配置）。测试零改动：test_g11_turnover_gate.py 用例全部 monkeypatch `fts.config.get_config`（None=关闭 / 0.20 / 0.05=启用），不依赖生产默认值；定向回归 test_g11_turnover_gate + test_g4_screening_gates 17 passed + test_config_settings 全绿。

> 注：v2.104.0+6（G4 门槛升级块数感知 |ic_t|）——时序 `evaluate_backtest` 与横截面 `_evaluate_cross_section` 的 G4 IC 硬门槛由 `|ICIR|<0.30` 改为显著性等价的 `|ic_t|=|ICIR|×√N < 1.65`（N=IC 块数/有效截面期数，自动适配样本长度）；`ic_t_stat` 缺失（<2 块）回退旧 |ICIR|≥0.30 口径。test_g4_screening_gates.py 新增 test_cross_section_g4_t_stat_block_count_aware（25 期面板 ICIR=0.66 但 ic_t=0.94 被拒 / 120 期 ic_t=2.05 放行），既有用例断言字符串更新（截面|ICIR| → 截面|ic_t|）；定向回归 test_g4 + test_walk_forward + test_qc_stats_completion + test_evolution_loop(not slow) 316 passed。

> 注：v2.104.0+9（G11 阈值默认重开 0.45，用户修正：期货换手率过高同样无交易价值）——`FTSConfig.factor_turnover_daily_max` 默认 None→**0.45**（P95 校准：83 个 active 期货因子真实分布 P95=0.456，仅拦 top ~5% 天天翻仓的极端抖动）；env 语义：数值=覆盖、"off"/"none"/"0"=关闭、空值=0.45。测试：test_config_settings **+2 用例**（默认 0.45 / env 覆盖含 off 关闭）；test_g11_turnover_gate **+1 用例**（test_turnover_gate_on_by_default：真实默认下日换手 1.0 因子被拒）+ 2 用例更名 off_when_null（原 disabled_by_default，因默认已非关闭）；定向回归 test_g11 + test_g4 + test_config_settings 73 passed。

---

## 5. 测试用例统计

| 测试文件 | 用例数 | 覆盖模块 |
|:---------|:-------|:---------|
| `tests/factor_engine/test_factor_screener.py` | 35 | 高IC筛查器（GAP-F16，v2.88.0，新建）：V1 一票否决（IC<门槛/样本不足/常数信号）+ V2 否决链（稳健性/极值扰动 ic_drop>25%/因子衰减）+ 打分/分级/报告输出 + 配置读取与边界 |
| `tests/factor_engine/factor_db/test_migrate_from_json.py` | 19 | JSON→DuckDB 因子迁移（GAP-F16，v2.88.0，新建）：记录迁移字段映射/契约校验/幂等/表不存在/重复记录/类型转换/异常兜底 |
| `tests/factor_engine/test_duckdb_writer.py` | 10 | DuckDB 单写者（GAP-056，v2.86.0）：连接可写/单条读写/executemany 批量/execute 原子性/executemany 原子性（唯一约束冲突整批回滚）/错误恢复/8 线程并发写零冲突/批量 COPY 与逐条一致/空 COPY no-op/close 释放 |
| `tests/test_duckdb_reader.py` | 5 | DuckDB 读连接池（GAP-056，v2.86.0）：acquire 查询/池复用/池满关闭/写连接打开时读共存/close 全关 |
| `tests/factor_engine/test_turnover_penalty.py` | 12 | 组合换手惩罚项（GAP-I303，v2.85.0）：apply_turnover_penalty 单元 4（λ=0 不变/λ>0 收缩/新因子不惩罚/无 prev 直返）+ 换手惩罚生效断言 3（惩罚后 Σ\|Δw\| 严格更小/λ 单调递减/大 λ 贴近 prev）+ build_combo 集成 2（惩罚生效且归一化/默认 0 不变）+ PortfolioLoop 配置 3（读配置/默认 0/显式参数覆盖） |
| `tests/factor_engine/test_microstructure_factors.py` | 20 | Level2 订单流因子（GAP-I503 首期，v2.84.0）：方向分类（升/降/持平延续/缺列降级）+ OFI（纯买=+1/纯卖=-1/混合界内/空输入）+ OBI（买深重/卖深重/缺深度=0）+ 大单占比（绝对/相对阈值/无大单=0）+ compute 契约列/trade_volume 差分/降级/排序 + 配置校验 |
| `tests/data_sources/test_tick_cache_accumulate.py` | 11 | tick_cache 增量累积（GAP-I503 首期，v2.84.0）：去重写入（重复写/部分重叠/读无重复）+ 时间窗口查询（start/end/双界/无参数兼容）+ 保留清理（过期清除/新鲜保留） |
| `tests/factor_engine/test_executor_backend.py` | 14 | 可插拔执行器抽象（GAP-I502，v2.83.0）：四后端 map 行为一致性（thread/process/dask/ray 输出一致 + 顺序保持）/process 支持 lambda 与 bound method（cloudpickle 序列化）/缺依赖降级 ProcessBackend/dask-ray 创建失败降级/未知后端回退 thread/并发性断言/BatchMiner.filter_batch 接入 + process 单任务异常隔离（单候选失败不影响其余）/filter_batch thread 与 process 结果一致 |
| `tests/factor_engine/extractors/test_alternative_sources.py` | 16 | 另类知识源（GAP-I103，v2.82.0）：公告提取器 8（暂停/API 成功/空数据/无标题跳过/异常/非200/无数据降级/LLM 提取 + parent_topic）+ 宏观提取器 6（暂停/成功/空/异常/缺失 key/期货 LLM 提取）+ 管道接入 4（股票含公告+宏观/开关关闭/期货含宏观/关闭） |
| `tests/factor_engine/test_review_experience_chain.py` | 7 | 审查意见接入经验链（GAP-I102 二期，v2.82.0）：驳回写 failure 轨迹（success=False/failure_reasons/lessons）/批准不写/空 comment 不写/开关关闭不写/经验链异常降级/审查人入 lessons/幂等不重复写 |
| `tests/data_sources/test_tqsdk_tick_source.py` | 10 | TQSDK tick 数据源（品种映射/tick 解析/tick_cache 迁移/降级链/Provider 接口） |
| `tests/data_sources/test_ashare_special_source.py` | 28 | A 股特有字段增强源（GAP-081，v2.103.0）：代码归一化/沪深两融列名归一化/时序对齐（日频精确 + 低频披露日 ffill 防未来函数）/缓存 miss 拉取写回/降级/股东户数/北向 agent 缓存/研报解析/enrich short_balance 派生/FundamentalProvider 接线/migrate 建表 + 缓存优先 get_fields 2（全命中零网络/部分命中读缓存+缺失拉取写回） |
| `tests/data_sources/test_stock_fundamental_source.py` | 25 | 股票基本面字段增强源（GAP-082，v2.103.0）：字段族注册/FIELD_FAMILY 路由/估值列名映射 + 流通股本缓存/财务列名映射 + 百分比 ÷100 归一化 + NaN 跳过 + 缺列降级/时序对齐（日频精确 + 季度报告期 ffill 防未来函数）/缓存 miss 拉取写回/降级/enrich 派生（turnover_rate/volume_ratio/amplitude）/FundamentalProvider 接线（enabled 注入/disabled 跳过/异常不阻断）/migrate 建表 + 缓存优先 get_fields 2（全命中零网络/部分命中读缓存+缺失拉取写回） |
| `tests/data_sources/test_stock_ohlcv_columns.py` | 13 | 股票 OHLCV 扩展列（GAP-084，v2.103.0）：腾讯路径 `_kline_to_df` 6（8 列输出/amount 0.0 如实降级/vwap 典型价 (H+L+C)/3 回退/pre_close 前移首行 NaN/无 high·low_limit 列/短行跳过）+ TQ 路径 `fetch_stock_ohlcv` 5（8+change_pct 列/vwap=amount÷volume 精确/缺 amount 补 0.0 回退/pre_close+change_pct 保留/SourceUnavailable→None）+ 合成路径 2（synthesize_ohlcv 保持 5 列/全部真实源失败回退仍 5 列） |
| `tests/scripts/test_build_cap_map.py` | 15 | 市值映射构建（GAP-086，v2.103.0）：缓存导出每 symbol 最新 total_market_cap（过滤非市值字段）/库缺失·损坏返回 {} /实时快照前缀剥离 + 剔除非正市值/缺列·异常返回 {} /写 JSON 文件（可 load 键值正确）/auto 缓存空实时补齐/缓存权威不覆盖/dry-run 不落盘/全空不写 + 端到端（load_cap_map 读取生成文件非空 / size_neutralize 空 map no-op → cap_map 可用时生效剥离市值偏好 / 未映射 symbol 保留原值） |
| `tests/data_sources/test_tdx_minute_source.py` | 29 | 通达信分钟适配器（主力连续代码映射/列字典解析/周期映射） |
| `tests/data_sources/test_macro_aligner.py` | ~8 | 宏观字段增强层（EDB 缓存读写/时序对齐/发布滞后/缺数据降级/批量注入） |
| `tests/data_sources/test_macro_panel_injection.py` | 8 | 面板级宏观注入 helper（GAP-088，v2.103.0）：多标的 5 列注入 + 跨标的共享序列只拉一次 / 发布滞后防未来函数 / 字段缺失不注入 / 单标的失败不阻断（flaky align monkeypatch）/ 拉取失败全部降级 / panel None 与空 fields noop + cli `_prepare_futures_data` 横截面演化接线 2（注入成功列保留 / 异常降级不阻断） |
| `tests/scripts/test_futures_signal_pipeline_macro.py` | 3 | 期货信号管道宏观注入接线（GAP-088，v2.103.0）：默认开调用面板级 helper 且注入列保留 / 关闭（--no-macro-injection）不调用 / helper 异常降级返回原面板不抛异常 |
| `tests/scripts/test_futures_signal_pipeline_db.py` | 11 | 期货信号管道 DuckDB 因子资产库读取（GAP-097，v2.103.0）：DB 加载基础（market/elite/status 过滤 + 字段构造 + metadata.evaluation 复用）/ 顶层评估列构造 / IC 阈值过滤 / 代码哈希去重（sharpe desc 保留高版本）/ stat 去重 / 空库·文件缺失·损坏库返回 [] / _load_signal_factors DuckDB 唯一加载源 2（DB 优先不落 JSON；v2.104.0+7 移除 JSON 降级回退，DB 空返回 [] 不误调） |
| `tests/factor_engine/test_bincount_boundary.py` | 253 | np.bincount 输入边界 + 同族因子 NaN 防护（3 bincount 因子 × 3 + 4 g 因子 × 3 + v2.34.0 批量防护 77 因子 × 3 动态扫描 + 1 扫描断言） |
| `tests/factor_engine/test_backtest_frequency.py` | 26 | 分钟级回测频率自适应（年化因子/z-score 窗口/成本模型/绩效年化） |
| `tests/core/test_atomic.py` | ~32 | 原子操作 |
| `tests/core/test_contracts.py` | ~39 | core contracts |
| `tests/core/test_enums.py` | ~17 | enums |
| `tests/test_config_settings.py` | 69 | 配置管理（含 v2.59.0 期货中性化/回测真实性仿真 4 配置用例 + v2.60.0 样本外强制/保证金 3 配置用例：force_walkforward/max_margin_usage 默认值与 env 覆盖 + D.2 偏差 b stock_signal_regime 2 配置用例 + plans/28 28-T4 配置用例：probability_mix/confidence_scale 等默认值与契约 + GAP-XXX cs_panel_min_coverage 2 配置用例 + GAP-086 cap_map_path 动态默认 3 用例：文件不存在空串/存在指向 data/cap_map.json/env 优先） |
| `tests/factor_engine/test_g1_config.py` | 18 | L3 G1 同向敞口惩罚参数配置化（v2.104.0+X，35-gap-closure-plan G1）：默认值与历史硬编码一致（回归锚点）+ YAML 覆盖/部分保留默认 + FTS_L3_G1_* 环境变量覆盖 + AlignedExposureConfig 契约校验（非法 threshold/max_compress/curve 快速失败 + 边界合法值）+ check_aligned_exposure 自定义参数 scale（mc=0.7 全多→0.7 / th=0.8 部分→不触发 / th=0.6 部分→0.8125 对照锚点）+ PortfolioLoop 从配置消费 _g1_config 2（自定义读取/默认一致）+ 向后兼容 2（全多默认压缩 0.5 / enabled=False scale=1.0） |
| `tests/test_mlp_factor.py` | 12 | MLP 因子模型（GAP-F05）：训练/推理形状 + 线性目标学习 + 标准化/常数列 + 降级路径（样本不足/未训练/非数值/维度）+ 工厂/可复现 |
| `tests/test_gru_factor.py` | 28 | GAP-I203 深度因子学习（v2.73.0）：GRU 模型级 11（训练/预测形状 + seq_len·n_features 属性 + 线性记忆目标学习相关 >0.3 + 同 seed 可复现 + 样本不足/未训练/非数值/维度不匹配降级 + 常数列兜底 + 权重导出形状 + 工厂）+ DeepFactor 生成器集成 9（契约字段 + 生成 code 经 `_execute_factor_code` 可执行且 \|out\|≤1 + 零未来函数截断一致性 + 短序列/非数值/长度不齐降级 + 缺 volume 兜底 + 同 seed code 确定性 + 训练失败降级）+ EvolutionLoop 接线 8（`_run_deep_evolution` 成功血缘/无数据/样本不足抛错 + `_evolve_one` deep 分派/失败返回 None + 批次轮换断言） |
| `tests/monitor/test_data_level_monitor.py` | 24 | 数据级质量监控（GAP-F06 + GAP-085）：缺失率/异常值/复权一致性/多源分歧 + 阈值边界/冷却/回调 + scheduler 接入 + hold 缺失检测/无 hold 列跳过（v2.101.0） |
| `tests/monitor/test_live_factor_monitor.py` | 12 | 在线因子性能监控（GAP-I402，v2.77.0）：偏离检查 5（无偏离/中度 warning/重度 critical/报告/因子列表）+ ingest_live_ic 6（基线构建/衰减告警/开关关闭/状态存取/空数据降级/指标日志）+ GAP-I401 端到端对接 1（compute_live_ic+report 输出可被 ingest 消费） |
| `tests/factor_engine/test_pareto.py` | 12 | Pareto 多目标前沿（GAP-I204 二期，v2.78.0）：objectives 口径 2（越大越好/默认值）+ 快速非支配排序 5（单个体/双层/互不支配同层/相同个体/链式支配）+ 前沿提取 5（空集/空表达式过滤/fitness 降序/被支配剔除/换手衰减支配） |
| `tests/factor_engine/test_symbolic_regression.py` | 15 | 符号回归补充搜索（GAP-I204 二期，v2.78.0）：配置 2（默认/自定义）+ 初始化 2（排除目标列/multi_objective 复用）+ 候选生成 2（一元包装/二元组合）+ 搜索 7（结果结构/排序/字段填充/最优候选/固定种子可复现/深度约束/beam 上限）+ GP 集成 2（symbolic 前沿合并/multi_objective 前沿输出） |
| `tests/factor_engine/test_portfolio_optimizer.py` | 19 | 组合优化器（GAP-F07）：风险平价/均值方差 + 换手/VaR/集中度/杠杆约束 + scipy 降级 + synthesize_signals optimizer 模式接入 |
| `tests/data_sources/test_mcp_degradation.py` | 6 | MCP 降级（GAP-F04）：未启用返回 None / 启用未注入抛错 / 注入正常调用 |
| `tests/test_mlp_factor.py` | 12 | MLP 因子模型（GAP-F05）：训练/推理形状 + 线性目标学习 + 标准化/常数列 + 降级路径（样本不足/未训练/非数值/维度）+ 工厂/可复现 |
| `tests/monitor/test_data_level_monitor.py` | 24 | 数据级质量监控（GAP-F06 + GAP-085）：缺失率/异常值/复权一致性/多源分歧 + 阈值边界/冷却/回调 + scheduler 接入 + hold 缺失检测/无 hold 列跳过（v2.101.0） |
| `tests/factor_engine/test_portfolio_optimizer.py` | 19 | 组合优化器（GAP-F07）：风险平价/均值方差 + 换手/VaR/集中度/杠杆约束 + scipy 降级 + synthesize_signals optimizer 模式接入 |
| `tests/data_sources/test_mcp_degradation.py` | 6 | MCP 降级（GAP-F04）：未启用返回 None / 启用未注入抛错 / 注入正常调用 |
| `tests/factor_engine/test_ablation.py` | ~20 | 消融实验（五种消融模式 + 边界情况） |
| `tests/factor_engine/test_risk_tag.py` | 16 | 风险标签闭环验证（v2.104.0+74 存量失败修复：mock walk_forward 补 n_windows_completed=2，对齐 GAP-121 WalkForward 强制门；v2.104.0+75 新增种子预跳过 2 用例：已入库 active 种子评估前拦截、未入库种子正常评估晋升；v2.104.0+76 新增退化因子 2 用例：冷却期内（<30 天）跳过评估、冷却期满重新评估并复用原 factor_id 激活晋升） |
| `tests/factor_engine/test_shap_analyzer.py` | ~14 | SHAP 局部可解释性分析 |
| `tests/factor_engine/test_robustness.py` | ~20 | 鲁棒性审查（对抗样本/缺失值/分布外） |
| `tests/factor_engine/test_causal_validator.py` | ~14 | 因果结构审查（自然实验/预测误差） |
| `tests/scenarios/test_natural_experiments.py` | ~10 | 自然实验事件定义 |
| `tests/factor_engine/test_contracts.py` | ~16 | 契约定义 |
| `tests/factor_engine/test_evaluation_chain.py` | ~56 | 三级评估链（含 v2.62.0 GAP-S02 Barra 风格中性化集成：`style_exposures` 参数生效 + 行业+风格叠加；v2.98.2 GAP-071 +3：走航窗口 IC 由 oos 段内收益计算（非全局尾部）/每窗口仅执行 oos 信号/evaluate 共享缓存二次调用命中；v2.104.0+44 GAP-121 +3：TestCrossSectionWalkForward 横截面走航 2——短样本 `_build_wf_config` 多窗口 ≥2 / 默认配置短样本 0 窗口 + `evaluate_multiple_tests` n_tested 批次对齐 1——n=64 时 effective_n=64、bonferroni_p 收紧、adjusted_t 缩小 8 倍） |
| `tests/factor_engine/test_signal_cache.py` | 14 | 质检信号缓存（GAP-071，v2.98.2 新增）：命中语义 5（同 factor+同数据命中/扰动列 miss/单特征归零 miss/不同 factor miss/不同 params miss）+ LRU 与 clear 3（容量淘汰最久未用/clear 清空/put 存副本防下游修改污染）+ 边界 3（空数据不缓存/缺 factor_id 不缓存/列顺序无关命中）+ FactorExecutor 集成 3（二次执行命中跳过沙箱/扰动数据不命中正常重算/无缓存向后兼容） |
| `tests/factor_engine/test_barra.py` | 13 | Barra 风格体系（GAP-S02）：10 风格暴露引擎（齐全/形状/size 单调/未知风格抛错/字段缺失降级）+ 截面中性化（残差形状/残差与风格正交 corr<0.15/size 暴露剥离/空暴露原样/行业叠加/小样本降级）+ 评估链集成 |
| `tests/factor_engine/test_stock_regime.py` | 19 | A 股行业轮动 + 风格轮动 Regime（GAP-S03）：行业三态（concentrated/rotating/balanced）/风格四方向（large_cap/small_cap/growth/value）/风格切换样本正确率 ≥80%/空面板降级/HMM 复用回退/multipliers 键与值域/PortfolioLoop 集成 2 |
| `tests/factor_engine/test_batch_mining.py` | 11 | 批量挖掘漏斗（GAP-I201，v2.65.0）：BatchMiner 批量生成/并行粗筛/排序截断/依赖注入回调/契约 |
| `tests/factor_engine/test_executor_backend.py` | 17 | 可插拔执行器后端（GAP-I502，v2.83.0；C4 2026-08-11 dask 降级用例改强制 import 失败）：四后端工厂创建 + thread/process 一致性 + process lambda/bound method 跨进程序列化 + dask/ray 缺依赖降级 process + 未知后端回退 thread + BatchMiner.filter_batch 接入与单任务异常隔离 + 配置字段 |
| `tests/factor_engine/test_executor_dask.py` | 17 | C4 多节点分布式挖掘工厂（2026-08-11 新增）：DaskBackend LocalCluster map 顺序/结果/worker_count/shutdown 幂等与归零/cluster 句柄注入/address 优先/工厂创建 + 故障注入 4（kill 后 alive≥1 且减少/kill 后 map 正确/单 worker 集群/降级返回 0）+ 一致性 vs thread <1e-9 + 缺 dask 依赖降级 ProcessBackend（monkeypatch import 失败）+ BatchMiner.filter_batch dask vs process 一致与单任务异常隔离 + vs ThreadBackend 对齐 |
| `tests/factor_engine/test_l2_elite_redundancy.py` | 10 | L2 准入去冗余（GAP-I206，v2.71.0）：方法级 7（高相关命中/负高相关 abs 判断/低相关放行/空 elite 放行/索引文件跳过/容量护栏/执行失败容错）+ 集成 3（shadow 高相关拦截不落盘/种子跳过检查正常晋升/低相关正常晋升） |
| `tests/factor_engine/test_structure_cluster_quota.py` | 8 | 结构性聚类配额（GAP-077，v2.102.0；v2.104.0+25 家族概念移除后 max_per_family 回退类用例删除 11→8）：方法级 5（`_count_cluster_members` 同类计数/空 elite/扫描上限/新因子执行失败/索引跳过）+ 集成 3（配额满拒绝不落盘/未满放行/低相关簇放行） |
| `tests/factor_engine/test_gap079_oos_skip.py` | 6 | oos_consistency 误杀修复（GAP-079，v2.102.0；v2.104.0+44 GAP-121 判定反转硬拦截）：`_run_factor_audit` 集成 4（评估链走航 0 窗口+独立走航失败→failed（原 skipped，0 窗口=样本外验证缺失禁止放行）/ 独立走航成功优先 / 2 窗口低一致性仍 failed / walk_forward 缺失保持 L1 兜底）+ `_check_oos_consistency` 方法级 2（0/1 窗口 failed） |
| `tests/factor_engine/test_failure_guidance.py` | 9 | 父代失败归因定向修复（Phase 1.1 P0-2/26 计划 §5.2）：`read_failures_by_parent` 4（按 parent_id 过滤 / limit 生效 / 无匹配返回 [] / 空链返回 []）+ MacroEvolver prompt 注入 3（有 ctx 含"父因子最近失败归因"+"定向修复要求" / 无 ctx 不含该段落现有行为不变 / 带 ctx 仍正常产出因子）+ `_evolve_one` macro 分支传递 2（有失败记录构造 ctx 传给 evolver / 无记录传 None 照常调用） |
| `tests/factor_engine/test_shap_optimization.py` | 7 | SHAP 批量计算降频（GAP-080，v2.102.0）：默认参数降频 3（n_extreme 25 / n_background 50 / nsamples 50 + 自定义参数）+ nsamples 透传 shap_values 1 + summary n_nsamples 1 + FTSConfig 三项 2（默认 / env 覆盖）+ EvolutionLoop 接线 1（用配置值构造 ShapAnalyzer） |
| `tests/factor_engine/test_success_pattern.py` | 14 | 成功模式定向演化（Phase 1.2 P0-1/26 计划 §6）：`analyze_success_patterns` 8（空链空报告 / 窗口截断 / by_method 晋升率 / 时间衰减权重 / top_operators 提取 / top_window_bins 分箱 / min_sample 不足空报告 / 坏 recorded_at 降级）+ MacroEvolver prompt 注入 4（有 report 含"近期成功模式"段落 / 无 report 不含 / 空 report 不注入 / 带 report 正常产出因子）+ `_evolve_one` 传递与缓存 2（macro 分支传 report / 进程内缓存二次调用不重读经验链） |
| `tests/factor_engine/test_orthogonal_basis.py` | 19 | GAP-I206 补充（v2.72.1）正交基底 + GAP-I305 衰减分级：IC 斜率 5（上升正斜率/下降负斜率/平坦近零/点数不足回零/归一化范围）+ 衰减分级 5（normal/observe/retired 判定/update 写入分级字段/auto_retire 按级退役）+ 基底管理器 7（空基底/注册加载/上限淘汰/重复注册更新/Gram-Schmidt 正交性/弱候选拒绝/无基底返回 None）+ L2 集成 2（开关关闭返回 None/基底成员缺失降级） |
| `tests/factor_engine/test_factor_quality_card.py` | ~100 | 因子质量评分卡（10 维评分，A/B/C 分级，可配置映射阈值） |
| `tests/factor_engine/test_evolution_loop.py` | ~129 | L2 主循环（含孤立模块集成审查门禁测试：消融/因果/鲁棒/SHAP/特征重要性/逻辑监控 + 端到端流水线 + v2.59.0 GAP-F03 期货板块中性化注入 3 用例 + v2.60.0 GAP-F08 样本外强制 7 用例：WalkForward 冷启动/配置开关/审计优先 + v2.61.0 GAP-S01 股票中性化自动注入 4 用例：启用注入/键归一化/关闭跳过/空映射降级 + v2.65.0 GAP-I201 batch 集成 10 用例 + v2.66.0 GAP-X01/X02 3 用例：常数前置拦截/真实截面 IC/无截面能力拦截 + v2.98.2 GAP-071 审计复用 2 用例：复用评估链走航不调用兜底/缺失回退独立计算 + v2.104.0+14 GAP-115 熔断预算传播 3 用例：重绑传播到 UctSelector/熔断判定按最新阈值（0.99 触发、1.0 放行）/协作类 owner 动态读取一致） |
| `tests/factor_engine/test_experience_chain.py` | ~19 | 经验链 |
| `tests/factor_engine/test_factor_program.py` | ~36 | 因子程序（v2.104.0+40 修复器新增 IndentationError 缩进修复 4 用例：反缩进语句 unexpected indent / 语句多余缩进 / unindent does not match / expected an indented block） |
| `tests/factor_engine/test_failure_pattern.py` | ~22 | 失败模式聚类分析 |
| `tests/factor_engine/test_macro_evolution.py` | ~30 | 宏观演化 |
| `tests/factor_engine/test_meta_loop.py` | 101 | L1 元循环（含 schema 版本兼容冷启动测试；v2.72.0 GAP-I101 批量候选契约校验 + 吞吐指标 8 用例；v2.100.1 感知层样本按市场区分 6 用例：股票默认 CSI300[:13]/期货默认 13 品种不变/显式覆盖 + stock collector 走股票 OHLCV 不取实时价/期货原路径+实时价/股票 OHLCV 失败降级；v2.103.0 GAP-098 失败率熔断时序修复 3 用例：未验证批不误熔断（0/19 已验证 None）/整批 20 全失败熔断保留 + 3 注入 85% 不熔断/20 有效候选集成回归 completed+20 注入（tmp_state_store 隔离）；v2.104.0+10 GAP-I307 Step 2.5 去重口径修复 +2：scan_injected_names 读 factor_pool 按 market 过滤（l1_injected 清空仍可去重，stock 排除、无 market 历史纳入）/空 pool 返回空集；run 注入 entry 断言 market=futures）；v2.104.0+46 GAP-123 P1③ 经济逻辑重写闭环 +6 用例（_try_fix_economic_logic 成功更新候选/LLM 不支持 None/返回 None/重写仍不达标/调用异常/端到端软失败重写后注入） |
| `tests/factor_engine/test_review_workflow.py` | 11 | Alpha 审查工作流（GAP-I102，v2.72.0）：状态机 7（approve/reject 决策回写 + get_status 查询/未审查 None + 队列排除已审查 + market 过滤 + 幂等 UPSERT + 意见与审查人落盘）+ CLI 命令 4（list 队列输出/market 过滤/approve 回写/reject 回写） |
| `tests/factor_engine/test_micro_evolution.py` | ~13 | 微观演化（含 ImportError 覆盖 + 两阶段漏斗 5（GAP-I205）） |
| `tests/factor_engine/test_monitor.py` | ~45 | 因子引擎监控 |
| `tests/monitor/test_logic_monitor.py` | 19 | 逻辑监控仪表盘（漂移检测/极端预测/换月日；v2.104.0+72 +4 用例：离散三态突破因子不误报/退化离散信号告警/连续信号保持 zscore/离散阈值可配置） |
| `tests/factor_engine/test_portfolio_loop.py` | 245 | L3 组合循环（含粘性约束 5 + 漂移监控 7 + 影子池 6 + 过拟合保护 6 + 股票 L3 组合层 6（GAP-I301）+ plans/28 28-T4/T6/T10 用例扩增：exposure_scale 计算与消费/RegimeSmoother 不对称/指标上报接线 + v2.103.0+5 _build_factor_code_map 4 用例：内存 code 优先/DuckDB 补拉/JSON 快照兜底/全缺空映射 + **方案③① 双指标+实测自动构建（v2.104.0+2，8 用例：signal_sharpe 双指标 4——缩放分离/无缩放相等/measured 相等/空组合 None + _auto_build_factor_returns 自动构建 4——矩阵构建/数据不足回退/空面板/无 code 回退）+ GAP-122（v2.104.0+42，4 用例：_verifier_view 替换 signal_sharpe/缺失原样返回/风控缩放组合经视图通过/低信号质量仍失败）** + **plans/36（v2.104.0+50，10 用例：TestFactorCompositeScore 5——综合评分归一化/缺失维度剔除重归一/权重排序/降级回退/配置透传 + TestQualityWeightMode 3——quality_weight 定权下限/权重归一/等权等价 + TestRollingOos 2——滚动 OOS 组合收益/decay_ratio 与数据不足降级）** + **子链维度去冗余（v2.104.0+60，6 用例：TestChainDedup——同链超限按综合评分截断/各链≤上限全保留/symbol_ic 缺失归 unknown 保留/max_per_chain=1 每链单因子/空输入/elite JSON symbol_ic 兜底读取）** + **risk_parity 配置化（v2.104.0+62，2 用例：test_run_optimizer_auto_matrix——optimizer 无显式矩阵自动构建仅用于权重合成+指标保持估算 / test_synthesize_optimizer_risk_parity——risk_parity 权重非负且 ≤1 非全仓特征）**） |
| `tests/factor_engine/test_portfolio_loop_adaptive.py` | 22 | L3 adaptive 权重（v2.56.0 8 用例 + plans/28 28-T3/T6 扩增：probability_mix 概率混合/平滑参数契约 + **GAP-095 blend_power 幂次调节 6 用例 + GAP-094 calibration_path 接线 4 用例（v2.103.0+34）**） |
| `tests/factor_engine/test_regime_multipliers.py` | ~14 | GAP-L308 数据驱动 Regime 倍率（估计/钳制/样本回退/YAML 往返/接线回退，v2.68.0 新增） |
| `tests/factor_engine/test_data_provider_panel.py` | ~12 | GAP-L309 面板数据规模（PanelLoadingConfig/分层抽样/覆盖日志/默认参数透传，v2.68.0 新增） |
| `tests/factor_engine/test_program.py` | ~16 | Program.md |
| `tests/factor_engine/test_seed_pool.py` | ~16 | 种子池（含 GTJA191） |
| `tests/factor_engine/test_stress_test.py` | 29 | 压力测试（v2.71.0 新增字符串/非 Datetime 索引回归 1 用例，修复索引类型比较 bug） |
| `tests/factor_engine/test_symbol_holdout.py` | 18 | 标的留出验证（GAP-075）+ 弱信号保护（v2.104.0+15 GAP-116 +3：弱信号 \|train_ic\|<min_train_ic 返回 None（审计 skipped）/min_train_ic=0 向后兼容/强信号不受影响） |
| `tests/factor_engine/test_uct_selection.py` | ~10 | UCT 树搜索父因子选择 |
| `tests/factor_engine/test_verifier.py` | 17 | Verifier（v2.104.0+13 GAP-114 换手成本敏感净收益校验 +5：高换手成本覆盖准入/成本未覆盖拦截/极端高换手净夏普必败/turnover_cost_net=False 回退绝对硬剔/cost_adjusted 明细字段完整） |
| `tests/factor_engine/test_walk_forward.py` | ~57 | 走航验证 |
| `tests/factor_engine/test_regime.py` | 87 | 市场体制（plans/28 机构级优化：regime blend 概率混合/置信度仓位缩放/不对称切换/回退路径等 T1~T6 用例扩增 + 端到端修复用例 test_detect_promotes_hmm_regime_probs_to_top_level） |
| `tests/scenarios/test_scenarios.py` | ~20 | 宏观行为场景测试 |
| `tests/pipeline/test_base.py` | ~25 | 管线基础 |
| `tests/pipeline/test_factor_combiner.py` | ~33 | 因子组合器 |
| `tests/scheduler/test_engine.py` | ~35 | 调度引擎 |
| `tests/scheduler/test_hotswap.py` | ~21 | 热加载 |
| `tests/scheduler/test_tasks.py` | ~32 | 调度任务 |
| `tests/scheduler/test_watchdog.py` | ~22 | 看门狗 |
| `tests/strategies/test_base_v2.py` | ~55 | 策略基类 |
| `tests/strategies/test_multi_factor.py` | ~88 | 多因子策略 |
| `tests/strategies/test_strategy_evolution.py` | ~55 | 策略进化（动态因子权重/市场制度自适应/多周期信号融合） |
| `tests/test_cli.py` | ~64 | CLI 入口 |
| `tests/test_data.py` | ~51 | 数据层（含 CSI300 面板覆盖率阈值对齐 2 用例，GAP-XXX，v2.103.0；`_kline_to_df` 断言 5→8 列同步 GAP-084） |
| `tests/test_data_fundamental.py` | ~69 | 基本面数据层（含 GAP-087 `_fetch_macro` 真实宏观源 7 用例：cpi/pmi 真实值/源失败跳过/全失败回退常量/不伪造死字段/单次拉取缓存） |
| `tests/test_e2e.py` | ~10 | 端到端集成 |
| `tests/test_elite_tracker.py` | ~72 | Elite 因子跟踪 |
| `tests/test_http_server.py` | ~31 | Web UI 仪表盘 |
| `tests/test_cross_market.py` | ~20 | 跨市场泛化验证（数据适配/分类/报告/集成/边缘情况） |
| `tests/test_futures_signal_pipeline.py` | 39 | 信号管道 L3 组合权重严格模式（v2.105.0：正常/缺失退出/损坏退出/空权重退出）+ L3 组合因子过滤（缺失跳过/全缺失退出/market-db_path 链库路由透传 v2.104.0+41）+ 因子类别分类 + Regime 档位缩放 + 品种级 IC 自适应 + 组合合成（方向校正恒空 dict）+ delta 增量分类（v2.104.0+7）+ 加载源（v2.104.0+7）；Ridge 权重与方向校正测试已随 v2.105.0 移除 |
| `tests/test_llm.py` | ~43 | LLM 客户端（含 GAP-123 P0 bootstrap prompt 论证一致性规则 +2 用例 + P1③ fix_economic_logic +5 用例，v2.104.0+45/+46） |
| `tests/test_monitor.py` | ~46 | 项目级监控 |
| `tests/factor_db/test_schema.py` | ~12 | DuckDB Schema 测试 |
| `tests/factor_db/test_repository.py` | ~28 | FactorRepository CRUD + 搜索 + 版本管理 |
| `tests/factor_engine/test_extreme_perturb.py` | 10 | 极值扰动一票否决（GAP-F15，v2.79.0）：极值剔除重算 IC（ic_before/ic_after/ic_drop 计算 + 数据不足/常数输入返回 None）+ 构造极值依赖因子验证否决触发 + 无极值依赖因子放行 + pct 可配置生效 |
| `tests/scripts/test_seed_dedup.py` | 9 | 种子库去重校验（GAP-F10，v2.79.0；v2.104.0+25 家族上限配置化用例随 max_per_family 移除 13→9）：内嵌 vs YAML 种子交叉比对命中重复/一致性差异 + 去重脚本 CLI 输出 |
| `tests/scripts/test_verify_doc_consistency.py` | 30 | 文档一致性校验脚本（v2.100.1）：find_docs 扫描/元数据表格检查/文件存在性/01·06·07·08 断言/流程文档存在性/版本号一致性/run_all_checks 汇总/main CLI（--file 作用域 + --json + --fix-versions 成功与失败路径 + subprocess 列表参数回归——解释器路径含空格不再被 cmd 拆分） |
| `tests/scripts/test_validate_sector_clusters.py` | 6 | 产业链分类聚类校验脚本（GAP-S05，v2.101.0）：return_corr 相关填 0 / 层次聚类分组 / ARI 一致性 / 主导簇纯度（单品种链跳过）/ 板块内外相关 / Markdown 报告章节 |
| `tests/scripts/test_fit_regime_calibration.py` | 5 | Regime 统计校准离线拟合脚本（GAP-094，v2.103.0+34）：_hit_label 各制度方向预期判定（bull/bear/oscillate/high_vol/low_vol/未知跳过）/ 滚动检测序列 / main 合成数据 dry-run 不落盘 / 保存校准 JSON 且被 _compute_exposure_scale 消费 / 有效样本不足报错 |
| `tests/scripts/test_macro_regime_report.py` | 4 | 四象限宏观制度报告 CLI（GAP-092，v2.104.0+3 新增）：合成数据端到端 overheat 判定+画像 / --json 结构化输出 / 数据缺失 quadrant=unavailable 不伪造 / 自定义增长荣枯线 55 → stagflation |
| `tests/scripts/test_sync_energy_chain_depth.py` | 6 | 能源链深度补全脚本（GAP-121 A，v2.104.0+34）：_symbol_variants 双格式 / _current_rows 真实行·SYNTHETIC 行·最新日期分离统计 / _build_cache_df AKShare→17 列（缺列默认 0.0 广播）/ 真实达标且无 SYNTHETIC→skip 不写库 / dry-run 不落盘 / CLI --dry-run 退出码 0 |
| `tests/scripts/test_energy_chain_degradation_apply.py` | 6 | 能源链退化检测 apply 落库（v2.104.0+64 新建）：CRITICAL 降级（is_elite=false/status=degraded + JSON 移入 _deprecated + status_history 留痕）/ CRITICAL 无 JSON 仍降级 / WARN→shadow 观察池且 JSON 保留 / OK→retain 追加留痕状态不变 / 因子不存在 failed 不中断 / TeeLogger 可调用写文件 |
| `scripts/verify_qa_workflow.py` | CLI 验证 | 因子质检工作流程端到端验证（CTA 手册第六章 v1.3，v2.104.0+22）：真实面板（天勤，SYNTHETIC 兜底）→ Q1-Q10 入库质检（一票否决）→ 三级准入 → 9 部分质检报告 → 月度 M1-M5 / 季度 F1-F6 / 半年度 D1-D4 复检 → 退役 5 红线 → 7 状态机流转 + factor_db 临时库落库 → 看板统计；`--synthetic` 合成面板确定性运行（CI/无网络），`--strict` 严格判定（本地人工质检用），默认退出码绑定链路执行成功；已接入 `.github/workflows/ci.yml` `qa-verify` job（lint/type-check 全绿后执行） |
| `scripts/energy_chain_degradation_dryrun.py` | CLI 落库 | 能源链精英因子真实周期质检（退化检测，v2.104.0+64，GAP-121 能源链独立工作流）：三路检测（A reaudit Q1-Q10 全量重审 / B IC 退化重验证 / C FactorInspector Sharpe 血缘）+ `--apply` 真实落库——仅 B 路为落库判据（CRITICAL→is_elite=false/status=degraded + elite JSON 移入 _deprecated，WARN→status=shadow 观察池，OK→追加 revalidation 留痕）；A 路能源链 300 天窗口 oos_consistency n_windows<2 系统性硬拦截（GAP-121 评估链修复反转放宽）仅留痕不落库防短样本误杀；C 路血缘表（factor_audit_reports/factor_status_history）为空时跳过；报告输出 reports/energy_chain/{date}/qa/（reaudit_*.log/json + degradation_revalidation_*.csv/log + inspector_*.log + energy_degradation_summary_*.md）；定时任务「能化产业链因子定期质检（每周日 06:00）」已切换至本脚本 --apply 执行 |
| `tests/factor_engine/test_holdout_pool.py` | 9 | 盲测池机构标准（v2.81.0 建，v2.101.0 拆分农产品后核心链断言 农产品→畜牧）：规模 12~15/不与核心池·训练集重叠/全量内/去重/产业链覆盖≥8/核心链覆盖/大流动性代表/L2 训练充足 |
| `tests/factor_engine/test_universe_config.py` | 9 | 品种池/产业链配置 SSOT 加载（v2.104.0+38）：YAML 有效应用且与内置默认等价 / YAML 缺失回退内置默认 / YAML 损坏回退 / 池越界 universe 拒绝 / universe 重复拒绝 / 盲测∩分层重叠拒绝 / 泛化子链缺失拒绝 / 改训练池后盲测池自动重算（核心诉求）/ 炼化聚酯链分组自动生成置首位 |
| `tests/factor_engine/test_energy_chain.py` | 18 | 能源产业链专属工作流（v2.104.0+33；v2.104.0+34 追加深度阈值 3 用例；v2.104.0+35 追加 TestEnergyChainL1Knowledge 4 用例；v2.104.0+37 扩池 9→12 断言更新 + 新增四子链覆盖用例；v2.104.0+38 池定义随 config/futures_universe.yaml 加载）：训练链=12 化工品种全训（能源3 SC/FU/BU+聚酯3 PX/TA/PF+油化工3 L/PP/PG+煤化工3 MA/UR/SA，四大子链各 3 覆盖）/盲测池=其余化工链 8 品种无重叠·全量内·覆盖聚酯·油化工·煤化工/get_db_path·get_elite_dir 独立路由/FUTURES_SECTOR_MAP 新增炼化聚酯链置于首位且通用中性化反向映射不变/load_futures_elite_factors_from_db market=energy 独立库路由/ENERGY_CHAIN_MIN_TRAIN_ROWS=300 阈值+check_energy_chain_depth 审计契约（ok+below=12/真实行情品种≥1）/L1 知识输入双线混入（SeedPool energy 混入 8+ 能化种子、energy 默认感知 12 品种、chain_knowledge 注入含裂解/聚酯/库存/链传导、bootstrap prompt 含能化知识段） |
| `tests/factor_engine/test_experiment_log.py` | 9 | 结构化实验日志（Phase 2 P1-2/26 计划 §7）：writer schema 4（分组与 by_method 汇总/幂等覆盖/非法 payload 跳过+warning/嵌套目录自动创建）+ `extract_scores` 2（指标映射 turnover←turnover_monthly/空评估默认 {}）+ EvolutionLoop 集成 3（record_variant 字段完整含 quality_grade 合并/export 落盘/run() finally 自动导出） |
| `tests/factor_engine/test_evolution_stop.py` | 8 | 提前达标停止（Phase 3 P1-3/26 计划 §8）：`_maybe_early_stop` 3（连续 K 代零晋升触发+reason/中断晋升归零需重新累计/开关关闭恒 False 不累计）+ run() 集成 3（连续 K 代提前结束 early_stopped+代数正确/开关关闭跑满/提前停止后实验日志仍导出）+ FTSConfig 2（保守默认关闭 K=5/env 覆盖生效） |
| `tests/factor_db/test_correlations.py` | ~8 | 因子相关性矩阵计算测试 |
| `tests/factor_db/test_yaml_loader.py` | ~6 | YAML 种子因子加载测试 |
| `tests/factor_engine/test_factor_lineage.py` | ~27 | 因子数据血缘审计（演化谱系/评估趋势/退化检测/批量审计） |
| `tests/factor_engine/test_failure_classifier.py` | ~30 | 失败模式自动分类 + 改善建议生成 |
| `tests/factor_engine/test_gp_evolver.py` | ~54 | GP 演化（含 GP 因子代码可执行性 5 用例：标准 factor_program 约定/回测流水线执行/FactorExecutor 沙箱/运行时校验放行/Series 返回 + v2.66.0 GAP-X03 2 用例：ts_product 模板兼容 pandas≥2.1/适应度 clip 后处理对齐 + v2.71.0 GAP-I204 多目标适应度 7 用例：turnover/decay 字段填充/换手度量平滑 vs 振荡/同 IC 量级换手惩罚/系数放大 ×2/衰减惩罚/端到端 evolve multi_objective） |
| `tests/factor_engine/test_backtest_pipeline.py` | ~14 | 端到端回测流水线（标准/传统代码约定 + DatetimeIndex 兼容 + v2.59.0 GAP-F02 回测真实性仿真 4 用例：涨跌停/停牌掩码判定 + 拦截日持仓保持 + 报告 blocked_trades + v2.67.0 GAP-I501 容量约束 5 用例：大仓位截断/关闭跳过/缺量跳过/违规统计/端到端报告） |
| `tests/factor_engine/test_backtest_stage3.py` | ~27 | B.2 回测流水线增强（7 阶段类 + run_batch 排名 + Builder + CLI）（v2.9.0） |
| `tests/factor_engine/test_feedback_loop.py` | ~20 | C.3 反馈闭环（Trigger/归因/方向调整/幂等/月度报告/CLI/指标/schema）（v2.9.0） |
| `tests/test_cli_feature_gp.py` | ~5 | C.1 CLI（feature list/gp evolve/feature analyze 解析）（v2.9.0） |
| `tests/test_stage5_risk_live.py` | ~27 | C.2 实盘对接（信号契约/风控/模拟适配/Live 偏离/指标/HTTP 端点）（v2.9.0） |
| `tests/test_ml_models.py` | ~30 | ML 模型层（LightGBM/XGBoost 模型封装/训练管线/导入防护/降级回退）（v2.38.0） |
| `tests/test_bridge.py` | ~25 | SignalBridge 信号桥接（JSON/Redis/REST 协议/格式转换/CLI 桥接命令/Redis 降级）（v2.38.0） |
| `tests/factor_engine/test_factor_optimizer.py` | 51 | 因子优化器（Phase1/Phase2 去重/剪枝/相关性缓存/数据版本/分类）（v2.47.0；plans/29 P3-A 2026-08-11 同步断言 .npy→.parquet + 新增 TestFactorSignalCacheParquet 5：put 写 parquet 非 npy/磁盘重开读回/checksum 篡改判 miss 并删除/.npy 兼容回退并自动重建/clear 双格式清理） |
| `tests/factor_engine/test_standardizer.py` | ~41 | 因子标准化器（6 种方法 fit/transform/边界/NaN）（v2.47.0） |
| `tests/factor_engine/test_regime_hmm.py` | 47 | 隐马尔可夫/马尔可夫切换市场制度（fit/predict/状态推断）（v2.47.0；plans/28 多周期 HMM 后验 regime_probs T2 用例扩增） |
| `tests/factor_engine/test_regime_calibration.py` | 13 | 置信度熵标定 + 规则伪概率（plans/28 28-T5 新增）：熵惩罚折扣/无 probs 直通/规则伪概率归一化 + **GAP-094 StatisticalRegimeCalibrator 统计概率校准 10 用例（isotonic/platt/binning 拟合预测、未拟合透传、非法标签/NaN 拒绝、未知方法降级 binning、save/load 往返、损坏文件安全加载、Brier 评估，v2.103.0+34）** |
| `tests/factor_engine/test_regime_model_selection.py` | 2 | BIC 状态数选择（plans/28 28-T7 新增）：状态数确定/映射冻结防翻转 |
| `tests/factor_engine/test_regime_validation.py` | 3 | 制度样本外有效性验证（plans/28 28-T9 新增）：IC/方向准确率/全制度概率比对 |
| `tests/factor_engine/test_macro_regime.py` | 15 | Bridgewater 四象限宏观制度层（GAP-092，v2.104.0+3 新增）：四象限判定（overheat/goldilocks/stagflation/recession 参数化）/边界零分归高侧/联合软概率和=1/置信度随信号强度单调/联合概率语义/得分 clip/空数据与全 NaN 返回 None/取最新有效值/默认与自定义配置/象限画像完整性 |
| `tests/factor_engine/test_regime_features.py` | ~3 | 市场制度特征工程（补充用例）（v2.47.0） |
| `tests/factor_engine/extractors/test_base.py` | ~26 | 提取器基类与管道抽象（LLM 提取全路径/YAML 转换/暂停持久化）（v2.47.0） |
| `tests/factor_engine/extractors/test_stock_pipeline.py` | ~27 | 股票提取管道（v2.47.0） |
| `tests/factor_engine/extractors/test_futures_pipeline.py` | ~27 | 期货提取管道（v2.47.0） |
| `tests/scheduler/test_jobs.py` | ~37 | 调度任务定义（job 注册/运行/周期）（v2.47.0；v2.73.0 L3 期货路径 1 用例；v2.98.3 股票信号管道 3 用例 + 股票 L3 联动断言；v2.99.0 GAP-072 解绑断言——L3 job 不再联动信号管道（assert_not_called）、独立信号管道任务入口 called_once；v2.103.0+5 月度任务合并新标准重审 +2：Step A 重审 invoked（默认启用）/FTS_MONTHLY_REAUDIT_ENABLED=0 关闭） |
| `tests/monitor/test_reaudit.py` | 9 | 新标准准入复审（v2.103.0+5，新建）：summarize_result 处置规则 5（全过 retain/鲁棒性失败 shadow/审计失败 retire/评估失败 retire/error 优先）+ build_factor_program 2（code 缺失 None/正常构造）+ apply_reaudit_results 2（隔离 DuckDB：retain·shadow·retire 三处置 + error 跳过 + status_history 留痕 / retire 状态变迁记录） |
| `tests/factor_engine/test_evolution_l1_merge.py` | 14 | L1→L2 候选合并 + 精英晋升（v2.47.0 基础；v2.103.0+20 新增 2 用例——默认 shadow_observe 关闭（env 未设晋升记录无 shadow_pool）/FTS_EVOLUTION_SHADOW_OBSERVE=1 恢复（记录含 shadow_pool 且 observe_trading_days==5）；v2.104.0+10 GAP-I307 +1：消费后 total_count/pending_count 重算，不残留过期值） |
| `tests/factor_engine/test_weight_learning.py` | ~30 | 机构级权重学习（风险调整/滚动样本外验证/面板市场自动匹配/跨市场 IC）（v2.75.0；v2.78.1 默认关闭断言 2 用例） |

| `tests/risk/test_risk_manager.py` | ~26 | 实盘风控管理器（限额/止损/连续亏损暂停/多层校验）（v2.47.0） |
| `tests/monitor/test_prometheus_metrics.py` | 42 | Prometheus 指标采集（指标注册/标签/时序）（v2.47.0；plans/28 28-T10 +4：record_regime_metrics 记录/render 输出 fts_regime_*/无 probs 确定性回退/同市场覆盖/空 market unknown 桶） |
| `tests/test_cli_extra.py` | ~102 | CLI 补充（catalog/stats/cross-market/seeds 命令路径）（v2.47.0） |
| `tests/test_data_futures.py` | ~73 | 期货数据层（K 线主路径/缓存/重试/并发写入队列）（v2.47.0，v2.101.0 TestFromKlineCache 适配 11 列） |
| `tests/test_data_futures_hold.py` | 15 | 期货持仓/结算接入（GAP-083 阶段 A+C，v2.101.0）：_from_kline_cache 真实优先/0 占位代理/NULL 代理/混合/双格式 RB0 优先/8 列契约 + 增强层注册（默认注册 TQSDK/启用注册 TQSDK+iFinD SDK/实例化失败跳过/导入失败降级）+ 有效值覆盖 4（hold/settle 正数-only、oi_change 任意有效、缺列 noop） |
| `tests/data_sources/test_tqsdk_enhance_source.py` | 14 | 天勤 TQSDK 字段增强源（GAP-083 阶段 C，v2.101.0）：close_oi→hold+差分→oi_change/open_oi 回退/无持仓字段 None/零值→NaN/无账号 None/无映射 None/天勤异常 None + 符号映射 + is_available 三态 |
| `tests/data_sources/test_ifind_sdk_source.py` | 20 | iFinD 官方 SDK 字段增强源（GAP-083 阶段 C 方案 A，v2.101.0）：符号映射（主连剥 0/具体合约/指数 CFX/未知 None）+ futures_get 解析（DataFrame/dict/无效值清理）+ 认证双模式（token/账号密码）+ 全降级路径（无 SDK/无凭据/无映射/登录失败/接口异常/空结果）+ is_available 三态 |
| `tests/scripts/test_backfill_futures_hold.py` | 13 | AKShare 持仓/结算回填脚本（GAP-083 阶段 B，v2.101.0）：resolve_symbols/双格式 UPDATE/无效值跳过/dry-run/异常跳过/CLI + TestFetchHoldSettle 3（索引对齐回归/缺列补 0.0/空或缺 date） |
| `tests/scripts/test_gap_threshold_calibration.py` | 7 | 阈值校准脚本口径测试（GAP-117，v2.104.0+16）：换算系数=42（G11 日换手 = mean(\|Δsign\|)/2、月度 = 日 × 42）/catalog 日换手反推 /42/elite 反推 /42/evaluations 反推 /42/候选阈值通过率（日换手 le 方向）/ICIR 候选（ge 方向）/零换手因子处理 |
| `tests/data_sources/test_tqsdk_source.py` | ~18 | 天勤 TQSDK 数据源（周期/探活/映射/fetch_ohlcv 全路径/认证）（v2.47.0） |
| `tests/factor_engine/test_position_rank_crowding.py` | ~15 | 会员持仓排名拥挤度（GAP-069 v2.101.0：契约字段/指标计算/信号方向/列映射归一化/交易所路由/AKShare Provider 降级） |
| `tests/factor_engine/test_multi_frequency.py` | ~22 | 多频信号叠加与冲突消解（GAP-068 v2.101.0：分钟信号计算/四种聚合/叠加权重/三种冲突消解规则/分钟回测含成本方向/数据不足降级） |
| `tests/factor_engine/test_black_litterman.py` | 22 | Black-Litterman 观点融合组合层（C3，2026-08-11）：闭式性质（隐含收益/后验 μ/Σ 解析解）/零观点退化=风险平价先验/观点方向一致性/置信度单调（omega_scale↓ 偏离先验↑）/约束投影/维度校验/奇异协方差兜底/NaN 清理/auto-views 构建/合成信号 bl 集成/显式 views 透传/回退路径 |
| `tests/factor_engine/test_recalibration.py` | 18 | 在线重校准队列（C6，2026-08-11）：队列状态机（pending/processing/done/skipped/failed）/入队去重/JSON 幂等持久化/recalibrate 三态判定（提升→done/无提升→skipped/异常→failed）/process 队列回写 elite 元数据/DuckDB 同步/开关关闭跳过/CLI 命令/与 LiveVsBacktestICReport 触发联动 |
| `tests/factor_engine/test_cost_calibration.py` | 20 | 回测成本实证化（C7，2026-08-11）：配置优先级（env>overrides>默认）/融资成本单调性（利率↑成本↑）/margin 按品种差异化/AdjustedMetrics 分项字段/成本构成明细/标定回归函数（合成样本直线拟合）/样本收集/数据不足降级/开关关闭 |
| `tests/test_transformer_factor.py` | 21 | 轻量 Transformer 深度模型（C5，2026-08-11）：模型级 11（前向形状/seq·n_features 属性/线性目标学习/同 seed 可复现/因果掩码/常数列/未训练/非数值/维度/权重导出/工厂）+ DeepFactor 集成 6（契约字段/生成 code 经 `_execute_factor_code` 可执行/零未来函数截断一致性/确定性/短序列/默认 gru）+ EvolutionLoop 3（transformer 分派/失败降级/批次轮换含 transformer） |
| `tests/factor_engine/test_microstructure_generator.py` | 20 | 微观结构因子生成器（C1，2026-08-11）：聚合正确性（全涨/全跌 tick → OFI 方向）/排除当日/批量生成全部 kinds/坏品种跳过/降级 3 项（空 tick/少行/少日）/code 可执行与窗口自适应/日期对齐/零未来截断一致性/覆盖外归零/执行器 datetime 注入 + CLI micro-generate 3 用例（无候选 rc=1/候选输出/JSON） |
| `tests/factor_engine/test_alternative_sentiment.py` | 26 | 舆情情感因子生成器（C2，2026-08-11）：词典打分 7（积极/消极/中性/空/否定反转/混合/越界）/聚合（均值手算对照/变化率首日 0）/契约字段/全积极正向/全消极负向/批量全部 kinds/坏品种跳过/降级 4 项（空新闻/少记录/少日/缺列）/code 可执行与窗口自适应/零未来截断一致性/覆盖外归零 + CLI senti-generate 3 用例 |
| `tests/factor_engine/test_operator_expansion.py` | 57 | C8 算子扩容（2026-08-11）：22 算子功能与边界（L1 时序 12：ts_argmin 窗口最左/ts_ema 平滑·常数恒等/ts_mad·ts_iqr 常数 0·波动正/ts_range 振幅·零均值兜底/ts_quantile_range q1-q0=max-min·非负/ts_return_over_max 恒≤0·新高≈0/ts_min_max_ratio/ts_std_ratio 平稳→高波动>1·常数 NaN/ts_roc_sum/ts_breakout 新高触发·常数不触发/ts_cumulative_return + L2 截面 4：rank·zscore 差分常数 0·手算一致/极端占比 [0,1]·尖峰检出/中位数偏离 + L3 条件 3：where_gt 边界·等于取 b/连续计数 1,2,0,1,2·截断·非 Series 降级/符号翻转计数 + L5 领域 3：均值回归=−zscore/趋势强度 [0,1]/量价压力正负·零量兜底）+ 双注册表一致（GP category=c8 22 项/DSL 102 项/verify consistent·mismatched 0/required_shared 全覆盖/GP 按名调用）+ 目录生成幂等（rows 覆盖 102/确定性/render 一致/含经济含义/main rc=0/subprocess CLI） |
| `tests/test_http_server.py`（C8 增补） | +19 | 人审工作台端点（2026-08-11 C8 +14：REVIEW_HTML 内容 3/GET `/review`/GET `/api/review/pending`·异常降级/GET `/api/review/history`·连接关闭/POST approve·reject/缺 factor_id 400/未知路径 404；C8-2 机审 +5：pending mode+needs_human 标注/POST `/api/review/auto` 成功·manual 403·异常 500/页面含机审控件） |
| `tests/factor_engine/test_auto_review.py` | 28 | C8-2 机审/人审可配置（2026-08-11）：classify 三态全分支 13（缺失 None/NaN/非数值/非有限/IC·Sharpe 超上限→人审；低质 IC·Sharpe 低于下限→驳回；正常→批准；边界相等→正常；env 覆盖·非法回退/默认）+ auto_review 主流程 10（正常批准 reviewer=auto/低质驳回/异常·缺失保持 pending/统计返回/manual 拒绝 ValueError/manual+force 覆盖/幂等二次/空队列/limit/load_review_mode 默认·env）+ CLI 4（成功 rc=0/manual 拒绝 rc=2/异常 rc=1/parser 含 auto 子命令）+ http_server 增补 5（pending mode+needs_human 标注/auto 成功/manual 403/异常 500/页面含机审控件） |
| `tests/factor_engine/test_operator_expansion_c9.py` | 39 | C9 算子扩容二期（2026-08-11）：30 算子功能与边界（L1 时序 14：ts_pct_rank_window 常数 0.5 兜底/ts_zscore_rolling/ts_skew/ts_kurt 常数序列 std.where→0/ts_slope_pct/ts_position_in_range fillna 0.5/ts_down_ratio/ts_up_ratio/ts_gain_loss_ratio/ts_bias_ma/ts_boll_position fillna 0.0/ts_ma_diff/ts_vol_shrink/ts_tail_risk + L2 截面 5：cs_winsor_flag/cs_demean_ratio/cs_rank_norm 2*rank(pct)−1/cs_med_ratio/cs_extreme_gap fillna 0.0 + L3 条件 4：where_between/cross_above/cross_below/momentum_break + L5 领域 7：vol_regime 三态/mean_reversion_signal/price_volume_div/liquidity_dryup/self_corr/sign_entropy/reversal_strength）+ 双注册表一致（GP category=c9 30 项/DSL 132 项/verify consistent·mismatched 0/required_shared 全覆盖）+ 目录覆盖（rows≥132/确定性/含经济含义） |
| `tests/factor_engine/test_operator_expansion_d10.py` | 85 | D10 波动/风险族扩容（2026-08-11，55 算子）：波动率估计 12（realized/ewma/parkinson/garman_klass/rogers_satchell/yang_zhang/downside/upside/vol_of_vol/bipower/range/harmonic 常数序列→0·NaN 兜底）+ 回撤类 8（drawdown/max_drawdown/avg_drawdown/drawdown_duration/ulcer_index 单调性·常数 0）+ VaR/CVaR 4（var_95/99 负值·cvar 尾部·常数 0）+ 比率类 15（sharpe/sortino 纯升序列≥0/calmar/profit_factor/omega/kelly 等有限兜底）+ 结构类 16（worst_day/best_day/win_rate/loss_rate/avg_gain/avg_loss/expectancy/recovery_factor/risk_return/downside_deviation/vol_ratio_ewma/realized_vol_pct/vol_zscore/vol_percentile/garch_proxy/vol_asymmetry/leverage_effect/baseline_vol/long_term_vol/short_term_vol/vol_term_structure/max_loss_ratio/beta_vol）+ 双注册表一致（GP category=d10 55 项/DSL 含全量/verify consistent·mismatched 0/required_shared 全覆盖） |
| `tests/factor_engine/test_operator_expansion_d11.py` | 104 | D11 技术指标族扩容（2026-08-11，60 算子）：MACD 系 5（macd/signal/histogram 金叉死叉方向性）+ RSI 系 4（rsi/rsi_ma/rsi_slope/wilder_rsi 边界 [0,100]）+ 随机指标 3（%K/%D/%J 边界）+ 趋势 8（OBV/obv_slope/cci/atr/atr_pct/natr/bollinger_upper/lower 与 band_width/percent_b 边界）+ 摆动 10（aroon_up/down/oscillator/stoch_rsi 等）+ 高级 30（kst/vortex/ichimoku/sar/ppo/trix 等方向性·常数序列不抛异常）+ 双注册表一致（GP category=d11 60 项/DSL 全量/verify consistent·mismatched 0） |
| `tests/factor_engine/test_operator_expansion_d12.py` | 121 | D12 动量/趋势族扩容（2026-08-11，55 算子）：动量多尺度 10（ts_mom_n/速度/加速度/急动度 单调性·方向性）+ 趋势强度 8（trend_strength/trend_persistence/momentum_consistency/curvature 趋势序列判定）+ 新高新低 10（new_high/low/count/ratio 单调性·常数 0）+ 唐奇安 6（donchian_upper/lower/mid/width 突破）+ ADX 系 6（adx/+di/−di 方向性）+ 分形/其他 15（fractal_dim/entropy 等有限兜底）+ 双注册表一致（GP category=d12 55 项/DSL 全量/verify consistent·mismatched 0） |
| `tests/factor_engine/test_operator_expansion_d13_d17.py` | 432 | D13~D17 五族扩容（2026-08-11，215 算子，inspect 自动构造参数）：D13 截面/排名族 45（cs_rank_pct/cs_demean/cs_inv_rank/cs_signed_rank/cs_zscore 截面中心化·cs_rank_stability/cs_breadth_position）+ D14 条件/事件族 40（cross_above/below/ts_consecutive_increase 连续计数/state_duration/转折点/zizag/金叉死叉/突破跌破）+ D15 组合/跨序列族 50（cs_ratio/min/max/spread/ts_pair_corr/ts_beta/ts_alpha/ts_lead_lag_corr/ts_granger_proxy/ts_cointegration_proxy/基差 zscore 常数序列 _corr_clean 兜底）+ D16 量价/流动性族 40（amihud 非流动性/换手率/流动性 zscore/emv/量突破/量熵）+ D17 市场结构/分布族 35（市场广度/涨跌比/新高新低比/分散度/情绪得分/恐惧贪婪/制度持续）+ 每算子随机序列有限·常数序列不抛异常（inspect.signature 按参数名分配 series/volume/high/low/amount）+ 双注册表一致（GP category=d13~d17 215 项/DSL 全量/verify consistent·mismatched 0/required_shared 全覆盖）+ 冒烟（`scripts/verify_operator_expansion.py` -W error::RuntimeWarning 380 新算子 bad: NONE） |
| `tests/factor_engine/test_microstructure_promotion.py` | 7 | C1 微观结构评估晋升接线（2026-08-11）：无候选全 skipped/评估 passed 晋升/failed 不晋升/eval 异常单跳过/audit 异常降级仍晋升/limit 截断/CLI parser 含 micro-evaluate |
| `tests/factor_engine/test_simulated_approval.py` | 9 | 5 个待人工确认异常因子模拟审批（2026-08-11）：机审 classify 五类异常（缺失 IC/缺失 Sharpe/超上限 IC/超上限 Sharpe/NaN）全转人审/auto_review 后全部保持 pending 且入 needs_human（未落库）/模拟人工批准全流程落库 approved+队列清空（reviewer=alpha-board）/模拟人工驳回全流程落库 rejected/幂等覆盖（批准后驳回覆盖旧决定）/意见与审查人落盘 |
| `tests/store/test_storage_registry.py` | 13 | 存储域注册表（plans/29 P0 基建，2026-08-11）：默认 storage_landscape.yaml 加载覆盖全部存储域/必填字段齐全/已知域路由/未知域抛错/契约校验零违规/legacy 必须声明 migrated_to/planned 必须声明 migrated_from/无绝对路径/summary 聚合/env 路径覆盖/缺失契约降级空表/from_dict 默认值与非法后端校验 |
| `tests/scripts/test_migrate_elite_json_to_catalog.py` | 17 | P1 因子资产入库迁移脚本（plans/29 P1，2026-08-11）：差量补齐+status 子目录映射/幂等重跑/已存在校验零差异/字段差异报告/sync 更新漂移（update_catalog_status=False 保持 archived）/dry-run 不写/verify-only 只读/坏 JSON 跳过/孤儿报告/futures 市场路由/build_factor_dict·eval·verify 构建/verify 一致性与差异检测/CLI dry-run |
| `tests/store/test_state_db.py` | 14 | 状态 KV 存储层（plans/29 P2，2026-08-11；E.3 S2 SQLite 后端 2026-08-13）：upsert 写当前表+追加历史返回 seq/get 命中与缺失/get_all namespace 聚合/同 key 覆盖 UPSERT/历史追加多条可回放/snapshot 全量 dump 对账/persist 重开连接读回/非 JSON 值序列化/历史过滤 limit（9 API 用例）+ WAL 生效/写连接存活外部只读不阻塞（对照 DuckDB 报 File is already open）/upsert 原子回滚（触发器注入 history 失败）/seq 单调/8 线程并发写串行 seq 不重复（5 SQLite 特性用例） |
| `tests/scripts/test_migrate_state_to_duckdb.py` | 8 | P2 运行状态迁移脚本（plans/29 P2，2026-08-11）：发现规则 glob 展开+resolve 去重/迁移入库+读回对账/幂等重跑零重复/process 痕迹归档打包（复制语义不删源）/dry-run 不写/verify-only 只读/坏 JSON 跳过 |
| `tests/scripts/test_archive_history_cold.py` | 7 | P3-B 行情库冷热归档脚本（plans/29 P3，2026-08-12）：年份统计（VARCHAR date cast）/min_year/dry-run 计数/空表抛错/归档-verify 闭环（cold_rows 一致+hot_remaining=0）/幂等重跑（文件跳过+零删除）/不一致检测（缺年份冷层） |
| `tests/scripts/test_migrate_state_to_sqlite.py` | 6 | E.3 S2 状态库后端迁移脚本（2026-08-13）：迁移闭环（DuckDB 源→SQLite 目标行数一致/值 JSON 可解析）/seq 保序 + AUTOINCREMENT 接续（新增 upsert 从 max+1 继续）/幂等保护（目标非空未 --force 拒绝）/--force 覆盖重建清脏数据/源库写锁占用降级拒绝/源缺失报错 |
| `tests/test_data_futures_fundamental.py` | 40 | 期货基本面 provider（库存/基差/仓单，GAP-083 补充 + GAP-091 关闭，2026-08-11）：品种解析 5 + 库存归一化 4 + 基差归一化 2 + 库存获取 7 + 基差获取 4 + 仓单 17（CZCE dict 聚合/缺品种/空表/非 dict + CZCE 成功/GFEX 成功/SHFE·DCE 东财路由/INE 东财小写码/无映射 EC 空/股指空/未知空/缓存/部分日失败跳过/核心子集全部路由/阶段 1 交易所映射覆盖/东财映射全覆盖/东财映射无孤儿）+ FTSDataProvider 挂接 2，全部 monkeypatch 隔离网络 |
| `tests/store/test_duckdb_lock.py` | 4 | E.4 S1 跨进程写锁组件（2026-08-13）：互斥（A 持锁 B 超时获取失败，释放后主线程可再获取）/重复获取超时抛 TimeoutError/锁文件创建于 data/.locks/ 且窗口结束释放后可再获取/重复获取（同线程不可重入语义） |
| **合计** | **5377+** | plans/28 追加：test_regime.py →86（T1~T6 扩增）、test_regime_hmm.py →47（T2 扩增）、新增 test_regime_calibration.py 3 + test_regime_model_selection.py 2 + test_regime_validation.py 3、test_portfolio_loop.py →217、test_portfolio_loop_adaptive.py →12、test_config_settings.py →65、test_prometheus_metrics.py →42（T10 +4）；plans/29 P1 追加：test_migrate_elite_json_to_catalog.py 17；plans/29 P2 追加：test_state_db.py 11 + test_migrate_state_to_duckdb.py 8；plans/29 P3-A 追加：test_factor_optimizer.py →51（TestFactorSignalCacheParquet +5）；plans/29 P3-B 追加：test_archive_history_cold.py 7；GAP-091 阶段 2 追加：test_data_futures_fundamental.py 36→40（+4：东财路由/INE/EC 空/映射覆盖）；E.3 S2 追加：test_state_db.py 11→14（+3 SQLite 特性） + test_migrate_state_to_sqlite.py 6；E.4 S1 追加：test_duckdb_lock.py 4；GAP-117 追加：test_gap_threshold_calibration.py 7；**plans/36（v2.104.0+43）追加：test_portfolio_loop.py 227→237（+10：综合评分 5 + quality_weight 3 + 滚动 OOS 2）+ test_factor_clustering.py 43→48（+5：TestSelectRepresentativeScoreMap——score_map 优先于 sharpe/回退兼容/top-2 低相关保留/相关≥0.5 拦截/run 透传）**；**plans/37（v2.104.0+54~+57）追加：test_rolling_native.py 28 + test_seed_ops_native.py 56 + test_ops_native_batch3.py 48 + test_panel_vector.py 30→31（Phase 3 默认开启回归）**；**v2.104.0+77（GAP-121 扩展）追加：test_portfolio_loop.py 261→262（+1：energy 市场 Step 0.5 面板收缩至能源化工 20 品种（ENERGY_CHAIN_SYMBOLS ∪ ENERGY_CHAIN_HOLDOUT），futures 市场保持默认全池）** |
|

---

## 6. 测试原则

1. **测试随重构**：每阶段先写测试，测试全绿才能进入下一阶段
2. **mock 外部依赖**：LLM 调用使用 MockLLMClient，数据层使用 mock
3. **trace_id 验证**：测试必须验证 trace_id 是否正确传播
4. **Verifier 锁定测试**：必须测试锁定后的只读行为
5. **覆盖率门禁**：新增代码必须有对应测试，覆盖率不得低于模块当前水平
6. **分级回归（2026-08-11 修订）**：日常任务只跑受影响的**模块/集成测试**；**全量回归**仅两类时机执行——① 发布前（版本 bump/晋级里程碑）必跑；② 每月底例行巡检一次。日常任务输出报告注明"模块/集成定向回归 N passed（未跑全量）"。
7. **slow 分级（2026-08-13）**：重量级真实演化/回测测试标记 `@pytest.mark.slow`（26 个），日常回归 `-m "not slow"` 跳过、全量验收必跑；DuckDB 嵌入式单进程写约束——xdist 多 worker 并发写 `state.duckdb`/`factor_catalog_futures.duckdb` 触发文件锁冲突（非代码缺陷），日常回归单进程执行，锁冲突类测试单进程定向复核。

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `test_futures_signal_pipeline.py` → 21 个信号管道测试用例（Ridge 回归加权 + 方向校正 + 组合合成）；`test_data_fundamental.py` → 62 个基本面数据层测试用例；`test_loader.py` → 5 个种子加载测试（含基本面）；`test_seed_pool.py` → 种子池测试（含期货种子）；`factor_db/test_*` → 54 个 DuckDB 因子仓库测试用例；`test_gp_evolver.py::TestGpFactorExecutable` → 5 个 GP 因子代码可执行性测试用例（v2.8.4）；`test_expr_*.py` → FTS-Expr DSL 算子因子测试（v2.8.5）；`test_backtest_stage3.py` → 27 个 B.2 回测增强用例（v2.9.0）；`test_feedback_loop.py` → 20 个 C.3 反馈闭环用例（v2.9.0）；`test_cli_feature_gp.py` → 5 个 C.1 CLI 用例（v2.9.0）；`test_stage5_risk_live.py` → 27 个 C.2 实盘对接用例（v2.9.0）；`test_portfolio_loop.py` → 20 个漂移治理用例（粘性约束 7 + 漂移监控 7 + 影子池 6，v2.11.0）；`test_evolution_loop.py` → 4 个 L2 晋升双写原子化用例（DuckDB 失败回滚 JSON，v2.13.0）+ 2 个 factor_db_path 注入用例（GAP-030 测试隔离，v2.14.0）；`test_cross_market.py` → 20 个跨市场泛化验证用例（数据适配/分类/报告/加载/边缘情况/集成，v2.27.0）；`test_tdx_minute_source.py` → 29 个通达信分钟适配器用例（主力连续代码映射/列字典解析/周期映射，v2.30.0）；`test_tqsdk_tick_source.py` → 10 个 TQSDK tick 数据源用例（品种映射/tick 解析/tick_cache 迁移/降级链/Provider 接口，v2.31.0） |
| 可验证断言 | 总测试数 = 4020+ passed, 0 failed, 0 skipped（v2.51.0 基线）；v2.54.0 精英因子全员质量巡检 230 因子——229 合格/1 出库（`volume_price_efficiency_ratio` V5 经济逻辑维度最低 1<2.0），质检报告 `reports/2026-08-09/elite_quality_inspection_20260809_075754.md`；v2.55.0 回溯分析确认 V5 为 LLM 评分缺陷（institutional 真实值应为 4），更新评分后重新质检通过 V5，因子归库；v2.57.0 行业/市值中性化 ~17 用例（feature_ops 2 + evaluation_chain 7 + config_settings 8）全绿；v2.58.0 换月复权/展期仿真 ~15 用例全绿；v2.59.0 GAP-F02/GAP-F03 用例全绿（test_backtest_pipeline 4 涨跌停/停牌拦截 + test_evolution_loop 3 板块中性化注入 + test_config_settings 4 配置默认值/env 覆盖）；v2.61.0 GAP-S01 股票中性化主流程用例全绿（test_evolution_loop 4 自动注入 + test_evaluation_chain 中性化前后 IC 对比）；v2.88.0 GAP-F16 全量回归 5132 passed 全绿，覆盖率 TOTAL 94.31%（`--cov-fail-under=90` 达标），14 个 <90% 缺口模块清零；v2.98.1 新增 test_portfolio_loop_market_ohlcv.py 3 用例（L3 期货路径 Step 0.5b 自动构建市场合成 OHLCV → Step 2.5 regime 调整触发 / 面板空跳过 / 显式传入优先），portfolio_loop 相关 238 passed 全绿；v2.98.3 股票 L3 早间调度 + 信号管道联动：test_jobs.py TestDailySignalPipeline 3 用例（成功/异常捕获/job 入口）+ TestL3PortfolioLoopStockJob.test_stock_path 联动断言，test_tasks.py l3_portfolio_loop_stock cron 08:30 断言，调度 72 passed 全绿；2026-08-11 算子库扩容：DSL 512 / GP 491，`verify_registry_consistency` consistent=True·mismatched=0，D10~D17 380 新算子 `scripts/verify_operator_expansion.py` -W error::RuntimeWarning 冒烟 bad: NONE，算子专项测试 838 passed（operator_expansion 57 + c9 39 + d10 85 + d11 104 + d12 121 + d13_d17 432），目录 `scripts/generate_operator_catalog.py` 512 行；v2.104.0+48 GAP-121 补数据治本 7 用例全绿：test_evaluation_chain TestCrossSectionEvaluateBacktest 2（横截面评估产出 cross_symbol_positive_ratio/extreme_perturbation/net_excess_return + 常数信号降级兜底）+ test_evolution_loop TestGapF16CrossSectionAndEvolution 3（_evaluate_cross_section 透传 HIC 字段并派生 ic_volatility/decay_6m / 无 wf 不覆盖 / energy V5 放宽对齐 futures）+ test_high_ic_screener TestScoringDetail 2（decay_6m=0 半衰期无穷满分·不除零 / decay_6m=1 极低分不崩溃）；v2.104.0+50 plans/36 定向回归 397 passed 全绿（test_factor_clustering.py 48 + test_portfolio_loop.py 237 + test_cli_extra.py + test_portfolio_loop_adaptive.py）+ ruff 全过；v2.104.0+67 CAP 安全阀与 OOS 校正评分定向（test_portfolio_loop.py 237→247：TestFactorCompositeScore +3 / TestCapSafetyValve +4 / TestPortfolioLoop +3 全绿）；v2.104.0+68 load_elite_factors JSON 兜底 icir 提取定向（test_portfolio_loop.py 255→258：TestLoadEliteFactors +3 全绿） |
| 检验方式 | `python -m pytest tests/ --no-cov -q 2>&1 | Select-String "passed"` |