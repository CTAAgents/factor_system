# FTS 测试策略

> 版本: v2.62.0
> 最后更新: 2026-08-09

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
| 单元测试 | 90+ | ~2600 | 各模块独立测试（含基本面数据层 + 信号管道 + 消融实验 + 风险标签 + 场景测试 + SHAP分析 + 鲁棒性 + 因果验证 + 逻辑监控 + 种子因子相关性预检 + DuckDB因子仓库 + 因子相关性矩阵 + 因子血缘审计 + 失败模式分类 + 因子巡检 + 生命周期E2E闭环 + 回测流水线 + 后代因子运行时校验 + FTS-Expr DSL 算子因子 + GP 多父代交叉 + 快速预筛选 + 评分卡配置 + **算子演化引擎（C.4，13 用例）** + **L1→L2 候选合并（GAP-031，8 用例）** + **L2 晋升双写原子化（GAP-032，4 用例）** + **factor_db_path 测试隔离注入（GAP-030，2 用例）** + **SectorRegimeSelector 产业链级制度检测（15 用例）** + **L1 候选因子评分缺陷修复（150 测试全绿）** + **分钟级回测频率自适应（3 用例）** + **ML 模型层（v2.38.0，~30 用例）** + **SignalBridge 信号桥接（v2.38.0，~25 用例）** + **L1 注入质量优化（v2.48.0，5 用例：硬/软失败分类 + 软失败不熔断 + verify 编译 detail 日志 + extractor debug 落盘 2 项）** + **高IC筛查剔除（B.4，v2.49.0，25 用例：16 项打分 + 5 项一票否决 + A/B/C/PASS 评级边界 + 市场统一性 + B级优化建议）** + **质检拦截器判定修复（v2.50.0，~18 用例：消融信息型/拦截型判定 + IC NaN 掩码 + SingleAblation feature 契约）** + **vwap 通用 IC 门槛 + 种子全链质检（v2.50.0，7 用例：vwap code abs(IC)<0.08 拦截 3 + 种子 Verifier/消融/因果/鲁棒失败拒绝晋升 4）** + **精英因子全员质量巡检（v2.54.0，质检脚本 `scripts/elite_quality_inspection.py`——230 因子全量 HighICScreener 质检，含 V5 经济逻辑 fallback 修复，229 合格 1 出库）** + **FactorStyle 分类器 + L3 adaptive 权重（v2.56.0，40 用例：test_style_classifier.py 32 用例（名称/代码/签名推断 + 显式 style_tags 优先 + REGIME_STYLE_MULTIPLIERS 覆盖）+ test_portfolio_loop_adaptive.py 8 用例（adaptive 基权重=sharpe + AdaptiveWeightConfig 契约 + PortfolioLoop 端到端））** + **股票因子行业/市值中性化（v2.57.0，~17 用例：feature_ops 2（industry_cap_neutral 双重中性化 + NaN 行业归 UNKNOWN）+ evaluation_chain 7（横截面行业/双重中性化 2 + _neutralize_signal_matrix 5：行业去均值/市值加权去均值/NaN/空列表/UNKNOWN）+ config_settings 8（stock_neutralization 配置默认值与 env 覆盖 + load_industry_map 有效/缺失/格式错误/非 dict/空白键过滤/默认路径））** + **Barra 风格体系（v2.62.0，13 用例：test_barra.py——10 风格暴露引擎 5（齐全/形状/size 单调/未知风格抛错/字段缺失降级）+ 截面中性化 7（残差形状/正交 corr<0.15/size 剥离/空暴露/行业叠加/小样本降级/常数列剔除）+ 评估链集成 1（style_exposures 生效））** + **期货换月复权与展期仿真（v2.58.0，~22 用例：test_roll_calendar.py——换月日历构建（最大成交量主力判定）、复权因子计算（比率法）、复权序列应用（get_ohlcv adjusted）、contract_kline 缺失降级、展期成本扣除（BacktestPipeline 持仓穿越换月）、报告展期成本统计、配置默认值；test_cost_model.py 展期成本项 7 用例——期货默认 roll_cost_bps=2.0/股票 ETF=0、展期成本计入 total_cost、无换月日期/空仓/长度不匹配不扣、net_sharpe 惩罚、AdjustedMetrics.roll_cost_bps 字段）**） |
| 集成测试 | 5 | ~200 | strategies 策略层 + 演化循环集成 + 数据源聚合 + 期货同步 |
| E2E | 2 | 24 | test_e2e.py(10) + test_factor_lifecycle.py(14) |
|│ 合计 | 100+ | 2220 | 2359+ passed（20 cross-market tests all green，v2.33.0 新增 bincount 边界 12 用例 + g 因子 NaN 防护 12 用例 + repository 事务修复 3 用例，v2.34.0 批量防护 232 用例，v2.38.0 新增 ML 模型层 ~30 用例 + SignalBridge ~25 用例） |

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
├── factor_engine/                   # 25 个测试文件
│   ├── __init__.py
│   ├── conftest.py                  # factor_engine fixture
│   ├── test_backtest_pipeline.py    # 端到端回测流水线测试（标准/传统代码约定 + DatetimeIndex 兼容）
│   ├── test_contracts.py            # 契约定义测试
│   ├── test_evaluation_chain.py     # 三级评估链测试
│   ├── test_evolution_loop.py       # L2 主循环测试
│   ├── test_experience_chain.py     # 经验链测试
│   ├── test_factor_program.py       # 因子程序（安全沙箱）测试
│   ├── test_failure_pattern.py      # 失败模式聚类分析测试
│   ├── test_macro_evolution.py      # 宏观演化测试
│   ├── test_meta_loop.py            # L1 元循环测试
│   ├── test_monitor.py              # factor_engine monitor 测试
│   ├── test_portfolio_loop.py       # L3 组合循环测试
│   ├── test_program.py              # Program.md 测试
│   ├── test_regime.py               # 市场制度检测（RegimeAwareSelector）
│   ├── test_sector_regime.py        # 产业链级制度检测（SectorRegimeSelector，15 用例）
│   ├── test_seed_pool.py            # 种子池测试
│   ├── test_uct_selection.py        # UCT 树搜索父因子选择测试
│   ├── test_factor_inspector.py     # FactorInspector 定时巡检测试
│   ├── test_factor_lifecycle.py     # E2E 生命周期闭环测试（14 用例）
│   ├── test_verifier.py             # Verifier 锁定协议测试
│   │
│   ├── operator_evolution/          # 算子演化引擎测试（C.4，13 用例）
│   │   └── test_operator_evolution.py  # 初始化合法性/进化收敛/交叉变异/OPERATOR 产物/罚分/缓存/集成
│   │
│   └── expr_dsl/                    # FTS-Expr DSL 测试（6 文件）
│       ├── test_parser.py           # 解析器测试
│       ├── test_registry.py         # 算子注册表测试
│       ├── test_validator.py        # 校验器测试
│       ├── test_compiler.py         # 编译器测试
│       ├── test_executor.py         # 执行器测试
│       └── test_factory.py          # 算子因子工厂测试
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
├── test_data_futures_panel.py       # 期货面板 common_dates 多数对齐 + 方向校正日期定位测试
├── test_futures_signal_pipeline.py  # 期货信号管道 Ridge 回归加权 + 方向校正 + 组合合成测试
├── test_cross_market.py             # 跨市场泛化验证测试（20 用例，含数据适配/分类/报告/集成）
├── test_llm.py                      # LLM 客户端测试
└── test_monitor.py                  # 项目级 monitor 测试
```

---

## 3. pytest 配置

定义在 `pyproject.toml`：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=fts --cov-report=term-missing -v"
```

执行命令：

```bash
# 运行全部测试并显示覆盖率
python -m pytest tests/ --cov=fts --cov-report=term-missing

# 运行指定模块测试
python -m pytest tests/factor_engine/ -v

# 运行单文件测试
python -m pytest tests/factor_engine/test_verifier.py -v
```

---

## 覆盖统计（v2.47.0）

### 总体统计

| 指标 | 值 |
|:-----|:---|
| Total statements | 20797 |
| Overall coverage | 94% |
| 测试用例数 | 4256 passed, 0 failed, 0 skipped（v2.60.0 全量回归实测） |
| 测试文件数 | 116+ |
| 种子因子数 | 563（YAML 化管理，19 个文件） |
| 精英因子数 | 674（DuckDB 存储，v2.33.0 淘汰 fut_macro_export 家族 6 个） |
| 期货专用种子 | 12 大因子家族 50+ 子因子 |
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
fts\scheduler\jobs.py                        246      0   100%
fts\scheduler\tasks.py                        49      0   100%
fts\scheduler\watchdog.py                     57      0   100%
TOTAL                                      20326   1254    94%
```

### 模块覆盖统计

> 完整逐模块清单见上方"模块覆盖详情"表。以下按覆盖率区间汇总（v2.47.0 实测，TOTAL 94%）。

| 覆盖率区间 | 模块数 | 代表模块 |
|:-----------|:-------|:---------|
| **100%** | 31 | `core/atomic` `core/contracts` `core/enums` `data_mcp` `data_sources/{aggregator,base,migrate,tqsdk_source,akshare_minute_source}` `factor_engine/{cost_model,feature_ops,monitor,program,robustness,state,stress_test,walk_forward}` `expr_dsl/{compiler,factory,registry}` `factor_db/schema` `llm` `monitor/prometheus_metrics` `risk/risk_manager` `scheduler/{engine,hotswap,jobs,tasks,watchdog}` |
| **95%-99%** | 22 | `cli(99%)` `data_futures(99%)` `portfolio_loop(99%)` `extractors/base(99%)` `report_generator(99%)` `experience_chain(99%)` `data_mcp_bridge(98%)` `extractors/futures_pipeline(98%)` `factor_quality_card(98%)` `regime(98%)` `elite_tracker(98%)` `live_factor_monitor(98%)` `ablation(97%)` `http_server(97%)` `micro_evolution(97%)` `operator_evolution(97%)` `regime_features(97%)` `seed_data_futures_full(97%)` `evaluation_chain(96%)` `adaptive_weight(96%)` `gp_evolver(96%)` `regime_hmm(96%)` |
| **90%-94%** | 27 | `meta_loop(94%)` `data_fundamental(93%)` `cross_market/engine(93%)` `factor_inspector(93%)` `feedback_loop(93%)` `seed_loader(93%)` `backtest_pipeline(92%)` `macro_evolution(92%)` `ml/trainer(92%)` `logic_monitor(92%)` `portfolio_constructor(92%)` `seed_pool(91%)` `capital_allocator(91%)` `simulated_adapter(91%)` `bridge/signal_bridge(90%)` `fusion(90%)` `feature_importance(90%)` `expr_dsl/validator(90%)` 等 |
| **<90%（缺口）** | 16 | `cross_market/data_adapter(55%)` `factor_clustering(64%)` `tdx_minute_source(67%)` `tqsdk_tick_source(73%)` `factor_db/migrate_from_json(73%)` `evolution_loop(80%)` `tq_source(81%)` `data_quality_monitor(82%)` `ifind_source(84%)` `data(85%)` `factor_db/repository(85%)` `ml/models(86%)` `wind_source(87%)` `factor_screener(87%)` `causal_validator(89%)` `contracts(89%)` |

> 注：<90% 缺口模块多为外部数据源（网络/鉴权路径需集成环境）或异常分支兜底代码；`cross_market/data_adapter` 与 `factor_clustering` 为近期新增模块，缺口语句集中在参数校验与降级分支，后续按 P1/P2 优先级补充。

---

## 5. 测试用例统计

| 测试文件 | 用例数 | 覆盖模块 |
|:---------|:-------|:---------|
| `tests/data_sources/test_tqsdk_tick_source.py` | 10 | TQSDK tick 数据源（品种映射/tick 解析/tick_cache 迁移/降级链/Provider 接口） |
| `tests/data_sources/test_tdx_minute_source.py` | 29 | 通达信分钟适配器（主力连续代码映射/列字典解析/周期映射） |
| `tests/data_sources/test_macro_aligner.py` | ~8 | 宏观字段增强层（EDB 缓存读写/时序对齐/发布滞后/缺数据降级/批量注入） |
| `tests/factor_engine/test_bincount_boundary.py` | 253 | np.bincount 输入边界 + 同族因子 NaN 防护（3 bincount 因子 × 3 + 4 g 因子 × 3 + v2.34.0 批量防护 77 因子 × 3 动态扫描 + 1 扫描断言） |
| `tests/factor_engine/test_backtest_frequency.py` | 26 | 分钟级回测频率自适应（年化因子/z-score 窗口/成本模型/绩效年化） |
| `tests/core/test_atomic.py` | ~32 | 原子操作 |
| `tests/core/test_contracts.py` | ~39 | core contracts |
| `tests/core/test_enums.py` | ~17 | enums |
| `tests/test_config_settings.py` | ~39 | 配置管理（含 v2.59.0 期货中性化/回测真实性仿真 4 配置用例 + v2.60.0 样本外强制/保证金 3 配置用例：force_walkforward/max_margin_usage 默认值与 env 覆盖） |
| `tests/test_mlp_factor.py` | 12 | MLP 因子模型（GAP-F05）：训练/推理形状 + 线性目标学习 + 标准化/常数列 + 降级路径（样本不足/未训练/非数值/维度）+ 工厂/可复现 |
| `tests/monitor/test_data_level_monitor.py` | 22 | 数据级质量监控（GAP-F06）：缺失率/异常值/复权一致性/多源分歧 + 阈值边界/冷却/回调 + scheduler 接入 |
| `tests/factor_engine/test_portfolio_optimizer.py` | 19 | 组合优化器（GAP-F07）：风险平价/均值方差 + 换手/VaR/集中度/杠杆约束 + scipy 降级 + synthesize_signals optimizer 模式接入 |
| `tests/data_sources/test_mcp_degradation.py` | 6 | MCP 降级（GAP-F04）：未启用返回 None / 启用未注入抛错 / 注入正常调用 |
| `tests/test_mlp_factor.py` | 12 | MLP 因子模型（GAP-F05）：训练/推理形状 + 线性目标学习 + 标准化/常数列 + 降级路径（样本不足/未训练/非数值/维度）+ 工厂/可复现 |
| `tests/monitor/test_data_level_monitor.py` | 22 | 数据级质量监控（GAP-F06）：缺失率/异常值/复权一致性/多源分歧 + 阈值边界/冷却/回调 + scheduler 接入 |
| `tests/factor_engine/test_portfolio_optimizer.py` | 19 | 组合优化器（GAP-F07）：风险平价/均值方差 + 换手/VaR/集中度/杠杆约束 + scipy 降级 + synthesize_signals optimizer 模式接入 |
| `tests/data_sources/test_mcp_degradation.py` | 6 | MCP 降级（GAP-F04）：未启用返回 None / 启用未注入抛错 / 注入正常调用 |
| `tests/factor_engine/test_ablation.py` | ~20 | 消融实验（五种消融模式 + 边界情况） |
| `tests/factor_engine/test_risk_tag.py` | ~10 | 风险标签闭环验证 |
| `tests/factor_engine/test_shap_analyzer.py` | ~14 | SHAP 局部可解释性分析 |
| `tests/factor_engine/test_robustness.py` | ~20 | 鲁棒性审查（对抗样本/缺失值/分布外） |
| `tests/factor_engine/test_causal_validator.py` | ~14 | 因果结构审查（自然实验/预测误差） |
| `tests/scenarios/test_natural_experiments.py` | ~10 | 自然实验事件定义 |
| `tests/factor_engine/test_contracts.py` | ~16 | 契约定义 |
| `tests/factor_engine/test_evaluation_chain.py` | ~50 | 三级评估链（含 v2.62.0 GAP-S02 Barra 风格中性化集成：`style_exposures` 参数生效 + 行业+风格叠加） |
| `tests/factor_engine/test_barra.py` | 13 | Barra 风格体系（GAP-S02）：10 风格暴露引擎（齐全/形状/size 单调/未知风格抛错/字段缺失降级）+ 截面中性化（残差形状/残差与风格正交 corr<0.15/size 暴露剥离/空暴露原样/行业叠加/小样本降级）+ 评估链集成 |
| `tests/factor_engine/test_factor_quality_card.py` | ~100 | 因子质量评分卡（10 维评分，A/B/C 分级，可配置映射阈值） |
| `tests/factor_engine/test_evolution_loop.py` | ~121 | L2 主循环（含孤立模块集成审查门禁测试：消融/因果/鲁棒/SHAP/特征重要性/逻辑监控 + 端到端流水线 + v2.59.0 GAP-F03 期货板块中性化注入 3 用例 + v2.60.0 GAP-F08 样本外强制 7 用例：WalkForward 冷启动/配置开关/审计优先 + v2.61.0 GAP-S01 股票中性化自动注入 4 用例：启用注入/键归一化/关闭跳过/空映射降级） |
| `tests/factor_engine/test_experience_chain.py` | ~19 | 经验链 |
| `tests/factor_engine/test_factor_program.py` | ~32 | 因子程序 |
| `tests/factor_engine/test_failure_pattern.py` | ~22 | 失败模式聚类分析 |
| `tests/factor_engine/test_macro_evolution.py` | ~30 | 宏观演化 |
| `tests/factor_engine/test_meta_loop.py` | ~78 | L1 元循环（含 schema 版本兼容冷启动测试） |
| `tests/factor_engine/test_micro_evolution.py` | ~8 | 微观演化（含 ImportError 覆盖） |
| `tests/factor_engine/test_monitor.py` | ~45 | 因子引擎监控 |
| `tests/monitor/test_logic_monitor.py` | ~15 | 逻辑监控仪表盘（漂移检测/极端预测/换月日） |
| `tests/factor_engine/test_portfolio_loop.py` | ~90 | L3 组合循环（含粘性约束 5 + 漂移监控 7 + 影子池 6 + 过拟合保护 6） |
| `tests/factor_engine/test_program.py` | ~16 | Program.md |
| `tests/factor_engine/test_seed_pool.py` | ~16 | 种子池（含 GTJA191） |
| `tests/factor_engine/test_stress_test.py` | ~32 | 压力测试 |
| `tests/factor_engine/test_uct_selection.py` | ~10 | UCT 树搜索父因子选择 |
| `tests/factor_engine/test_verifier.py` | ~12 | Verifier |
| `tests/factor_engine/test_walk_forward.py` | ~57 | 走航验证 |
| `tests/factor_engine/test_regime.py` | ~25 | 市场体制 |
| `tests/scenarios/test_scenarios.py` | ~20 | 宏观行为场景测试 |
| `tests/pipeline/test_base.py` | ~25 | 管线基础 |
| `tests/pipeline/test_factor_combiner.py` | ~33 | 因子组合器 |
| `tests/scheduler/test_engine.py` | ~35 | 调度引擎 |
| `tests/scheduler/test_hotswap.py` | ~21 | 热加载 |
| `tests/scheduler/test_tasks.py` | ~31 | 调度任务 |
| `tests/scheduler/test_watchdog.py` | ~22 | 看门狗 |
| `tests/strategies/test_base_v2.py` | ~55 | 策略基类 |
| `tests/strategies/test_multi_factor.py` | ~88 | 多因子策略 |
| `tests/strategies/test_strategy_evolution.py` | ~55 | 策略进化（动态因子权重/市场制度自适应/多周期信号融合） |
| `tests/test_cli.py` | ~64 | CLI 入口 |
| `tests/test_data.py` | ~49 | 数据层 |
| `tests/test_data_fundamental.py` | ~62 | 基本面数据层 |
| `tests/test_e2e.py` | ~10 | 端到端集成 |
| `tests/test_elite_tracker.py` | ~72 | Elite 因子跟踪 |
| `tests/test_http_server.py` | ~31 | Web UI 仪表盘 |
| `tests/test_cross_market.py` | ~20 | 跨市场泛化验证（数据适配/分类/报告/集成/边缘情况） |
| `tests/test_futures_signal_pipeline.py` | ~21 | 信号管道 Ridge 回归加权 + 方向校正 + 组合合成 |
| `tests/test_llm.py` | ~36 | LLM 客户端 |
| `tests/test_monitor.py` | ~46 | 项目级监控 |
| `tests/factor_db/test_schema.py` | ~12 | DuckDB Schema 测试 |
| `tests/factor_db/test_repository.py` | ~28 | FactorRepository CRUD + 搜索 + 版本管理 |
| `tests/factor_db/test_correlations.py` | ~8 | 因子相关性矩阵计算测试 |
| `tests/factor_db/test_yaml_loader.py` | ~6 | YAML 种子因子加载测试 |
| `tests/factor_engine/test_factor_lineage.py` | ~27 | 因子数据血缘审计（演化谱系/评估趋势/退化检测/批量审计） |
| `tests/factor_engine/test_failure_classifier.py` | ~30 | 失败模式自动分类 + 改善建议生成 |
| `tests/factor_engine/test_gp_evolver.py` | ~45 | GP 演化（含 GP 因子代码可执行性 5 用例：标准 factor_program 约定/回测流水线执行/FactorExecutor 沙箱/运行时校验放行/Series 返回） |
| `tests/factor_engine/test_backtest_pipeline.py` | ~9 | 端到端回测流水线（标准/传统代码约定 + DatetimeIndex 兼容 + v2.59.0 GAP-F02 回测真实性仿真 4 用例：涨跌停/停牌掩码判定 + 拦截日持仓保持 + 报告 blocked_trades） |
| `tests/factor_engine/test_backtest_stage3.py` | ~27 | B.2 回测流水线增强（7 阶段类 + run_batch 排名 + Builder + CLI）（v2.9.0） |
| `tests/factor_engine/test_feedback_loop.py` | ~20 | C.3 反馈闭环（Trigger/归因/方向调整/幂等/月度报告/CLI/指标/schema）（v2.9.0） |
| `tests/test_cli_feature_gp.py` | ~5 | C.1 CLI（feature list/gp evolve/feature analyze 解析）（v2.9.0） |
| `tests/test_stage5_risk_live.py` | ~27 | C.2 实盘对接（信号契约/风控/模拟适配/Live 偏离/指标/HTTP 端点）（v2.9.0） |
| `tests/test_ml_models.py` | ~30 | ML 模型层（LightGBM/XGBoost 模型封装/训练管线/导入防护/降级回退）（v2.38.0） |
| `tests/test_bridge.py` | ~25 | SignalBridge 信号桥接（JSON/Redis/REST 协议/格式转换/CLI 桥接命令/Redis 降级）（v2.38.0） |
| `tests/factor_engine/test_factor_optimizer.py` | ~46 | 因子优化器（Phase1/Phase2 去重/剪枝/相关性缓存/数据版本/分类）（v2.47.0） |
| `tests/factor_engine/test_standardizer.py` | ~41 | 因子标准化器（6 种方法 fit/transform/边界/NaN）（v2.47.0） |
| `tests/factor_engine/test_regime_hmm.py` | ~24 | 隐马尔可夫/马尔可夫切换市场制度（fit/predict/状态推断）（v2.47.0） |
| `tests/factor_engine/test_regime_features.py` | ~3 | 市场制度特征工程（补充用例）（v2.47.0） |
| `tests/factor_engine/extractors/test_base.py` | ~26 | 提取器基类与管道抽象（LLM 提取全路径/YAML 转换/暂停持久化）（v2.47.0） |
| `tests/factor_engine/extractors/test_stock_pipeline.py` | ~27 | 股票提取管道（v2.47.0） |
| `tests/factor_engine/extractors/test_futures_pipeline.py` | ~27 | 期货提取管道（v2.47.0） |
| `tests/scheduler/test_jobs.py` | ~29 | 调度任务定义（job 注册/运行/周期）（v2.47.0） |
| `tests/risk/test_risk_manager.py` | ~26 | 实盘风控管理器（限额/止损/连续亏损暂停/多层校验）（v2.47.0） |
| `tests/monitor/test_prometheus_metrics.py` | ~38 | Prometheus 指标采集（指标注册/标签/时序）（v2.47.0） |
| `tests/test_cli_extra.py` | ~102 | CLI 补充（catalog/stats/cross-market/seeds 命令路径）（v2.47.0） |
| `tests/test_data_futures.py` | ~72 | 期货数据层（K 线主路径/缓存/重试/并发写入队列）（v2.47.0） |
| `tests/data_sources/test_tqsdk_source.py` | ~18 | 天勤 TQSDK 数据源（周期/探活/映射/fetch_ohlcv 全路径/认证）（v2.47.0） |
| **合计** | **3998+** | |
|

---

## 6. 测试原则

1. **测试随重构**：每阶段先写测试，测试全绿才能进入下一阶段
2. **mock 外部依赖**：LLM 调用使用 MockLLMClient，数据层使用 mock
3. **trace_id 验证**：测试必须验证 trace_id 是否正确传播
4. **Verifier 锁定测试**：必须测试锁定后的只读行为
5. **覆盖率门禁**：新增代码必须有对应测试，覆盖率不得低于模块当前水平

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `test_futures_signal_pipeline.py` → 21 个信号管道测试用例（Ridge 回归加权 + 方向校正 + 组合合成）；`test_data_fundamental.py` → 62 个基本面数据层测试用例；`test_loader.py` → 5 个种子加载测试（含基本面）；`test_seed_pool.py` → 种子池测试（含期货种子）；`factor_db/test_*` → 54 个 DuckDB 因子仓库测试用例；`test_gp_evolver.py::TestGpFactorExecutable` → 5 个 GP 因子代码可执行性测试用例（v2.8.4）；`test_expr_*.py` → FTS-Expr DSL 算子因子测试（v2.8.5）；`test_backtest_stage3.py` → 27 个 B.2 回测增强用例（v2.9.0）；`test_feedback_loop.py` → 20 个 C.3 反馈闭环用例（v2.9.0）；`test_cli_feature_gp.py` → 5 个 C.1 CLI 用例（v2.9.0）；`test_stage5_risk_live.py` → 27 个 C.2 实盘对接用例（v2.9.0）；`test_portfolio_loop.py` → 20 个漂移治理用例（粘性约束 7 + 漂移监控 7 + 影子池 6，v2.11.0）；`test_evolution_loop.py` → 4 个 L2 晋升双写原子化用例（DuckDB 失败回滚 JSON，v2.13.0）+ 2 个 factor_db_path 注入用例（GAP-030 测试隔离，v2.14.0）；`test_cross_market.py` → 20 个跨市场泛化验证用例（数据适配/分类/报告/加载/边缘情况/集成，v2.27.0）；`test_tdx_minute_source.py` → 29 个通达信分钟适配器用例（主力连续代码映射/列字典解析/周期映射，v2.30.0）；`test_tqsdk_tick_source.py` → 10 个 TQSDK tick 数据源用例（品种映射/tick 解析/tick_cache 迁移/降级链/Provider 接口，v2.31.0） |
| 可验证断言 | 总测试数 = 4020+ passed, 0 failed, 0 skipped（v2.51.0 基线）；v2.54.0 精英因子全员质量巡检 230 因子——229 合格/1 出库（`volume_price_efficiency_ratio` V5 经济逻辑维度最低 1<2.0），质检报告 `reports/2026-08-09/elite_quality_inspection_20260809_075754.md`；v2.55.0 回溯分析确认 V5 为 LLM 评分缺陷（institutional 真实值应为 4），更新评分后重新质检通过 V5，因子归库；v2.57.0 行业/市值中性化 ~17 用例（feature_ops 2 + evaluation_chain 7 + config_settings 8）全绿；v2.58.0 换月复权/展期仿真 ~15 用例全绿；v2.59.0 GAP-F02/GAP-F03 用例全绿（test_backtest_pipeline 4 涨跌停/停牌拦截 + test_evolution_loop 3 板块中性化注入 + test_config_settings 4 配置默认值/env 覆盖）；v2.61.0 GAP-S01 股票中性化主流程用例全绿（test_evolution_loop 4 自动注入 + test_evaluation_chain 中性化前后 IC 对比） |
| 检验方式 | `python -m pytest tests/ --no-cov -q 2>&1 | Select-String "passed"` |
