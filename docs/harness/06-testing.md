# FTS 测试策略

> 版本: v2.39.0
> 最后更新: 2026-08-08

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
| 单元测试 | 90+ | ~2600 | 各模块独立测试（含基本面数据层 + 信号管道 + 消融实验 + 风险标签 + 场景测试 + SHAP分析 + 鲁棒性 + 因果验证 + 逻辑监控 + 种子因子相关性预检 + DuckDB因子仓库 + 因子相关性矩阵 + 因子血缘审计 + 失败模式分类 + 因子巡检 + 生命周期E2E闭环 + 回测流水线 + 后代因子运行时校验 + FTS-Expr DSL 算子因子 + GP 多父代交叉 + 快速预筛选 + 评分卡配置 + **算子演化引擎（C.4，13 用例）** + **L1→L2 候选合并（GAP-031，8 用例）** + **L2 晋升双写原子化（GAP-032，4 用例）** + **factor_db_path 测试隔离注入（GAP-030，2 用例）** + **SectorRegimeSelector 产业链级制度检测（9 用例）** + **L1 候选因子评分缺陷修复（150 测试全绿）** + **分钟级回测频率自适应（3 用例）** + **ML 模型层（v2.38.0，~30 用例）** + **SignalBridge 信号桥接（v2.38.0，~25 用例）**） |
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
│   ├── test_sector_regime.py        # 产业链级制度检测（SectorRegimeSelector，9 用例）
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

## 覆盖统计（v2.22.0）

### 总体统计

| 指标 | 值 |
|:-----|:---|
| Total statements | 6200+ |
| Overall coverage | 92%+ |
| 测试用例数 | 2102+ passed, 0 failed, 0 skipped |
| 测试文件数 | 52+ |
| 种子因子数 | 563（YAML 化管理，19 个文件） |
| 精英因子数 | 674（DuckDB 存储，v2.33.0 淘汰 fut_macro_export 家族 6 个） |
| 期货专用种子 | 12 大因子家族 50+ 子因子 |
| 因子相关性记录 | 4950 条（100 因子 × 两两组合） |

### 模块覆盖详情

```
Name                                       Stmts   Miss  Cover
──────────────────────────────────────────────────────────────
fts\__init__.py                               1      0   100%
fts\cli.py                                  234      0   100%
fts\config\__init__.py                         3      0   100%
fts\config\settings.py                        70      0   100%
fts\core\__init__.py                           0      0   100%
fts\core\atomic.py                            44      0   100%
fts\core\contracts.py                          3      0   100%
fts\core\enums.py                             17      0   100%
fts\data.py                                   75      0   100%
fts\data_fundamental.py                      161     44    73%   205-223, 240-268, 272-301
fts\data_mcp.py                              120      0   100%
fts\factor_engine\__init__.py                 16      0   100%
fts\factor_engine\contracts.py               258      0   100%
fts\factor_engine\cost_model.py               68      0   100%
fts\factor_engine\evaluation_chain.py        238      1    99%   565(空白行)
fts\factor_engine\evolution_loop.py          212      0   100%
fts\factor_engine\experience_chain.py        121      0   100%
fts\factor_engine\factor_program.py          104      0   100%
fts\factor_engine\macro_evolution.py          63      0   100%
fts\factor_engine\meta_loop.py               440      0   100%
fts\factor_engine\micro_evolution.py          74      0   100%
fts\factor_engine\monitor.py                 103      0   100%
fts\factor_engine\portfolio_loop.py          323      0   100%
fts\factor_engine\program.py                 101      0   100%
fts\factor_engine\regime.py                   94      0   100%
fts\factor_engine\seed_data\__init__.py        2      0   100%
fts\factor_engine\seed_data\alpha_ops.py      82      0   100%
fts\factor_engine\seed_data\gtja191.py         3      0   100%
fts\factor_engine\seed_data\loader.py         68      0   100%
fts\factor_engine\seed_data\qlib158.py         3      0   100%
fts\factor_engine\seed_data\wq101.py           3      0   100%
fts\factor_engine\seed_pool.py                58      0   100%
fts\factor_engine\state.py                    95      0   100%
fts\factor_engine\stress_test.py              94      0   100%
fts\factor_engine\verifier.py                 65      0   100%
fts\factor_engine\walk_forward.py            103      0   100%
fts\llm.py                                   122      0   100%
fts\monitor\__init__.py                       64      0   100%
fts\monitor\elite_tracker.py                 142      0   100%
fts\monitor\http_server.py                   105      0   100%
fts\pipeline\__init__.py                       4      0   100%
fts\pipeline\base.py                          49      0   100%
fts\pipeline\factor_combiner.py               94      0   100%
fts\scheduler\__init__.py                      4      0   100%
fts\scheduler\engine.py                       86      0   100%
fts\scheduler\hotswap.py                      61      0   100%
fts\scheduler\tasks.py                        49      0   100%
fts\scheduler\watchdog.py                     57      0   100%
fts\strategies\__init__.py                     4      0   100%
fts\strategies\base_v2.py                    156      0   100%
fts\strategies\multi_factor_strategy.py      230      0   100%
fts\strategies\strategy_evolution.py         271     13    95%
fts\strategies\rules\__init__.py               1      0   100%
fts\factor_db\__init__.py                      5      0   100%
fts\factor_db\schema.py                       86      0   100%
fts\factor_db\repository.py                  320      0   100%
fts\factor_db\yaml_loader.py                 120      0   100%
fts\factor_db\correlation.py                 180      0   100%
fts\seed_data\__init__.py                     10      0   100%
fts\seed_data\loader.py                       65      0   100%
──────────────────────────────────────────────────────────────
TOTAL                                       6229    533    91%
```

### 模块覆盖统计

| 模块 | 覆盖率 | 说明 |
|:-----|:-------|:-----|
| **100% 模块（46 个）** | | |
| `__init__.py` (fts) | **100%** | |
| `cli.py` | **100%** | CLI 全覆盖 |
| `config/__init__.py` | **100%** | |
| `config/settings.py` | **100%** | 配置管理全覆盖 |
| `core/atomic.py` | **100%** | 原子操作全覆盖 |
| `core/contracts.py` | **100%** | |
| `core/enums.py` | **100%** | |
| `data.py` | **100%** | 数据层全覆盖 |
| `data_fundamental.py` | **73%** | 基本面数据层（MCP 路径需网络环境） |
| `data_mcp.py` | **100%** | MCP 数据适配全覆盖 |
| `factor_engine/__init__.py` | **100%** | |
| `factor_engine/contracts.py` | **100%** | 契约定义全覆盖 |
| `factor_engine/cost_model.py` | **100%** | 成本模型全覆盖 |
| `factor_engine/evolution_loop.py` | **100%** | L2 主循环全覆盖 |
| `factor_engine/experience_chain.py` | **100%** | 经验链全覆盖 |
| `factor_engine/factor_program.py` | **100%** | 安全沙箱全覆盖 |
| `factor_engine/macro_evolution.py` | **100%** | 宏观演化全覆盖 |
| `factor_engine/meta_loop.py` | **100%** | L1 元循环全覆盖 |
| `factor_engine/micro_evolution.py` | **100%** | 微观演化全覆盖 |
| `factor_engine/monitor.py` | **100%** | 因子引擎监控全覆盖 |
| `factor_engine/portfolio_loop.py` | **100%** | L3 组合循环全覆盖 |
| `factor_engine/program.py` | **100%** | |
| `factor_engine/regime.py` | **100%** | 市场体制全覆盖 |
| `factor_engine/seed_data/__init__.py` | **100%** | |
| `factor_engine/seed_data/alpha_ops.py` | **100%** | 因子操作函数全覆盖 |
| `factor_engine/seed_data/gtja191.py` | **100%** | |
| `factor_engine/seed_data/loader.py` | **100%** | 种子加载全覆盖 |
| `factor_engine/seed_data/qlib158.py` | **100%** | |
| `factor_engine/seed_data/wq101.py` | **100%** | |
| `factor_engine/seed_pool.py` | **100%** | 种子池全覆盖 |
| `factor_engine/state.py` | **100%** | 状态管理全覆盖 |
| `factor_engine/stress_test.py` | **100%** | 压力测试全覆盖 |
| `factor_engine/verifier.py` | **100%** | Verifier 全覆盖 |
| `factor_engine/walk_forward.py` | **100%** | 走航验证全覆盖 |
| `llm.py` | **100%** | LLM 客户端全覆盖 |
| `monitor/__init__.py` | **100%** | |
| `monitor/elite_tracker.py` | **100%** | Elite 因子跟踪全覆盖 |
| `monitor/http_server.py` | **100%** | Web UI 全覆盖 |
| `monitor/logic_monitor.py` | **100%** | 逻辑监控全覆盖 |
| `pipeline/__init__.py` | **100%** | |
| `pipeline/base.py` | **100%** | |
| `pipeline/factor_combiner.py` | **100%** | |
| `scheduler/__init__.py` | **100%** | |
| `scheduler/engine.py` | **100%** | |
| `scheduler/hotswap.py` | **100%** | |
| `scheduler/tasks.py` | **100%** | |
| `scheduler/watchdog.py` | **100%** | |
| `strategies/__init__.py` | **100%** | |
| `strategies/base_v2.py` | **100%** | |
| `strategies/multi_factor_strategy.py` | **100%** | 多因子策略全覆盖 |
| `strategies/rules/__init__.py` | **100%** | |
| `factor_db/__init__.py` | **100%** | 因子仓库层入口 |
| `factor_db/schema.py` | **100%** | DuckDB Schema 定义 |
| `factor_db/repository.py` | **100%** | FactorRepository CRUD |
| `factor_db/yaml_loader.py` | **100%** | YAML 种子因子加载 |
| `factor_db/correlation.py` | **100%** | 因子相关性计算 |
| `seed_data/__init__.py` | **100%** | 种子数据入口 |
| `seed_data/loader.py` | **100%** | 统一种子加载器 |
| **≥73% 模块（3 个）** | | |
| `data_fundamental.py` | **73%** | MCP 网络路径需集成测试环境 |
| `factor_engine/evaluation_chain.py` | **99%** | 仅余 1 个空白行 |
| `strategy_evolution.py` | **95%** | 13 行未覆盖（异常/边界路径） |

> 注：evaluation_chain.py 565 行为空白行，属于 coverage.py 报告的格式问题，不影响实际可执行代码覆盖率。

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
| `tests/test_config_settings.py` | ~32 | 配置管理 |
| `tests/factor_engine/test_ablation.py` | ~20 | 消融实验（五种消融模式 + 边界情况） |
| `tests/factor_engine/test_risk_tag.py` | ~10 | 风险标签闭环验证 |
| `tests/factor_engine/test_shap_analyzer.py` | ~14 | SHAP 局部可解释性分析 |
| `tests/factor_engine/test_robustness.py` | ~20 | 鲁棒性审查（对抗样本/缺失值/分布外） |
| `tests/factor_engine/test_causal_validator.py` | ~14 | 因果结构审查（自然实验/预测误差） |
| `tests/scenarios/test_natural_experiments.py` | ~10 | 自然实验事件定义 |
| `tests/factor_engine/test_contracts.py` | ~16 | 契约定义 |
| `tests/factor_engine/test_evaluation_chain.py` | ~50 | 三级评估链 |
| `tests/factor_engine/test_factor_quality_card.py` | ~100 | 因子质量评分卡（10 维评分，A/B/C 分级，可配置映射阈值） |
| `tests/factor_engine/test_evolution_loop.py` | ~111 | L2 主循环（含孤立模块集成审查门禁测试：消融/因果/鲁棒/SHAP/特征重要性/逻辑监控 + 端到端流水线） |
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
| `tests/factor_engine/test_backtest_pipeline.py` | ~5 | 端到端回测流水线（标准/传统代码约定 + DatetimeIndex 兼容） |
| `tests/factor_engine/test_backtest_stage3.py` | ~27 | B.2 回测流水线增强（7 阶段类 + run_batch 排名 + Builder + CLI）（v2.9.0） |
| `tests/factor_engine/test_feedback_loop.py` | ~20 | C.3 反馈闭环（Trigger/归因/方向调整/幂等/月度报告/CLI/指标/schema）（v2.9.0） |
| `tests/test_cli_feature_gp.py` | ~5 | C.1 CLI（feature list/gp evolve/feature analyze 解析）（v2.9.0） |
| `tests/test_stage5_risk_live.py` | ~27 | C.2 实盘对接（信号契约/风控/模拟适配/Live 偏离/指标/HTTP 端点）（v2.9.0） |
| `tests/test_ml_models.py` | ~30 | ML 模型层（LightGBM/XGBoost 模型封装/训练管线/导入防护/降级回退）（v2.38.0） |
| `tests/test_bridge.py` | ~25 | SignalBridge 信号桥接（JSON/Redis/REST 协议/格式转换/CLI 桥接命令/Redis 降级）（v2.38.0） |
| **合计** | **2157+** | |
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
| 可验证断言 | 总测试数 = 2102+ passed（portfolio_loop 90 测试全绿，scheduler tasks 验证通过，Elastic Net + ACTIVE_FACTOR_CAP 无回归）；v2.38.0 新增 ML 模型层 + SignalBridge 测试后总测试数 ≥ 2157+ |
| 检验方式 | `python -m pytest tests/ --no-cov -q 2>&1 | Select-String "passed"` |
