# 期货因子流水线成熟度实施优化计划（机构级对标）

> 版本: v2.103.0+9
> 最后更新: 2026-08-09
> 状态: 规划中（文档先行，作为期货流水线缺陷改进唯一推进主线）
> 适用范围: FTS 期货因子流水线（数据层 / 因子层 / 模型层 / 组合层 / 回测层 / 执行实盘 / 运维）

> ⚠️ **计划整合说明（v2.58.0）**：本计划承接 plans/20-futures-roll-adjustment-plan.md 的阶段 B/C 缺陷改进候选清单（20 号已归档为 GAP-046 实施记录）。GAP-042（极值扰动一票否决）登记为 GAP-F15、GAP-041（覆盖率 <90% 补齐）登记为 GAP-F16；GAP-045（adaptive 权重 L3）由 plans/19-adaptive-weight-l3-integration.md 单独推进。

---

## 0. 背景与目标

### 0.1 现状

FTS 期货因子流水线已具备完整链路：5 级 K 线多源降级 → 184 种子因子（20 YAML / 14 家族）横截面演化 → 三级评估链 + 6 项审计 → L3 Elastic Net 组合 + 产业链 Regime 自适应权重 → 回测（成本+展期仿真）→ 信号管道输出。但对照券商金工/头部量化机构的标准，存在 14 项成熟度差距（见 §1 缺陷清单），覆盖执行链路、回测仿真、因子中性化、数据源可靠性、模型深度、组合优化、运维质量门禁等维度。

### 0.2 目标

1. 将所有机构级差距点系统登记为可执行缺陷项（含代码依据、机构级标准、实施步骤、测试方案）。
2. 按 P0/P1/P2/P3 优先级规划分阶段落地，与现有版本路线（v2.59.0+）衔接。
3. 每个缺陷项遵循「文档先行 → 契约优先 → 测试随重构」的 HARNESS 规范闭环。

### 0.3 调研方法

- 基于 `d:\Programs\factor_system` 实际代码逐文件勘察（v2.58.0）。
- "缺失"判定以代码搜索为据（Grep 零命中即视为未实现），禁止推测。
- 相关设计文档：`plans/15-minute-backtest-plan.md`（已落地）、`plans/16-tick-data-source-plan.md`（部分落地）、`plans/17-tick-microstructure-plan.md`、`plans/19-adaptive-weight-l3-integration.md`、`production_plan.md`。

---

## 1. 缺陷清单（机构级对标，按优先级）

### 1.1 P0 — 阻塞性差距（影响核心功能与合规红线）

#### GAP-F01 实盘执行链路缺失（P0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 无 `live_trade/` 目录；仅 `fts/risk/simulated_adapter.py` 模拟成交（直接返回 status='filled'）；`fts/risk/risk_manager.py` 5 项组合级检查（单品种仓位 10% / 回撤 20% / 单日亏损 5% / 杠杆 3x / 集中度 50%），无持仓级止损单管理 |
| **机构级标准** | 真实券商/交易所网关 + 订单生命周期状态机（未成交/部分成交/撤单/异常）+ 下单重试/超时兜底 + 人工干预通道（紧急暂停/一键平仓，权限高于自动化）+ 实盘参数独立隔离 + 灰度发布（小资金试运行→逐步放大） |
| **影响** | 无法实盘落地；违反 AGENTS.md 4.3 实盘红线（风控优先、异常容错、人工干预、灰度发布） |
| **实施步骤** | ① 建 `fts/live_trade/` 骨架（gateway/strategy/risk_control/monitor 子包）；② 定义 `OrderState` 状态机契约（PENDING/PARTIAL/FILLED/CANCELED/REJECTED）；③ `RiskManager` 增加持仓级止损止盈单；④ 人工干预接口（pause/all_close，权限最高）；⑤ 灰度发布流程文档 |
| **测试方案** | 状态机全路径 + 异常回滚 + 风控拦截 + 干预接口权限测试 |

#### GAP-F02 回测仿真不完整（无涨跌停/停牌/部分成交）（P0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `backtest_pipeline.py` Grep `涨跌停|停牌|limit_up|limit_down|halt|suspension` 零命中；回测仅做成本扣减，信号直接按收盘/结算成交，无成交可能性建模 |
| **机构级标准** | AGENTS.md 4.2 红线要求强制开启滑点、手续费、**涨跌停拦截、停牌过滤**、资金容量限制；机构回测还建模部分成交/成交概率 |
| **影响** | 回测结果偏乐观（涨跌停日无法成交被当作可成交）；违反回测-实盘强对齐红线 |
| **实施步骤** | ① 定义涨停/跌停判定（基于 pre_settle 与涨跌停板幅度表，按品种）；② 回测撮合增加限价/涨跌停不可成交逻辑（position 目标被拦截）；③ 停牌日过滤（volume=0 或行情缺失日跳过调仓）；④ 报告输出被拦截成交统计 |
| **测试方案** | 涨跌停日信号不成交 + 停牌日跳过 + 正常日不受影响回归 |

#### GAP-F03 因子无中性化主流程（P0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 行业/市值中性化仅存在于 `cross_section_evaluate_backtest` 可选参数（evaluation_chain.py L518-541，industry_map/cap_map 为 None 即跳过）；期货演化路径未传行业映射；记忆确认"股票和期货因子流水线均未在主流程中实际调用中性化" |
| **机构级标准** | 机构级截面因子必须做行业 + 市值 +（可选）流动性/波动率中性化，剥离系统性风格暴露，避免"伪预测力"（因子只是 proxy 了板块/市值） |
| **影响** | 截面因子 IC 含板块/风格暴露污染；跨品种可比性失真 |
| **实施步骤** | ① 建期货品种→产业链/板块映射（复用 `FUTURES_SECTOR_MAP` 产业链归属）；② `cross_section_evaluate_backtest` 在期货演化路径默认启用板块中性化（去均值）；③ 可配置市值代理（成交额/持仓量）中性化；④ 报告输出中性化前后 IC 对比 |
| **测试方案** | 板块去均值后 IC 变化 + NaN/空映射降级 + 期货演化路径启用验证 |

### 1.2 P1 — 重要差距（影响稳定性与有效性）

#### GAP-F04 数据源生产可用性脆弱（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | WIND/IFIND 两源 `_call_mcp` 默认抛 `RuntimeError`（wind_source.py L40-54 / ifind_source.py 同），必须通过 FTS 启动钩子注入 MCP 客户端才生效；tick 历史仅 ~42 分钟（tqsdk_tick_source.py 头注释） |
| **机构级标准** | 数据源开箱即用或明确降级路径；tick 历史可回放 |
| **影响** | 生产环境 WIND/IFIND 增强字段缺失，产业链 Regime/EDB 数据不可用 |
| **实施步骤** | ① MCP 客户端注入改为可配置（env 开关 + 显式初始化报错提示）；② 无 MCP 时明确降级（跳过增强字段，仅用主路径）；③ tick 历史评估可回放性（缓存当日 tick 到 DuckDB） |
| **测试方案** | 无 MCP 客户端时降级不抛异常 + 注入后增强字段生效 |

#### GAP-F05 深度学习/RL 空白（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | Grep `torch|tensorflow|keras|stable_baselines` 零命中；仅 LGBM/XGBoost/Ensemble 树模型（fts/ml/models.py）；无神经网络因子挖掘（AlphaNet 类）、无 RL 调仓 |
| **机构级标准** | 头部量化普遍使用深度时序模型（LSTM/GRU/Transformer）与 RL 优化调仓 |
| **影响** | 因子挖掘停留在手工/GP 演化，无法利用深度时序特征（对应 GAP-037） |
| **实施步骤** | ① 评估引入 PyTorch 轻量时序层（可选依赖 extra）；② AlphaNet 式神经网络因子（MLP/LSTM 预测横截面收益）；③ 不引入重依赖前提下以 MLP 为主；RL 登记为远期 |
| **测试方案** | 模型训练/推理/降级路径（缺 torch 时优雅降级） |

#### GAP-F06 数据质量监控错位（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | `data_quality_monitor.py` 仅监控因子 IC 漂移（Z-Score 2/3）+ 容量突变（50%/80%），是因子级而非数据级监控；无数据缺失率/异常值/停牌/复权一致性监控器 |
| **机构级标准** | 数据质量监控应覆盖数据完整性（缺失率）、准确性（异常值/对账）、及时性（延迟）三维 |
| **影响** | 数据缺失/异常未被及时发现，污染因子计算与回测 |
| **实施步骤** | ① 新增数据级监控器（缺失率、异常值 z-score、复权因子一致性、多源分歧率）；② 接入 scheduler 定时任务；③ 阈值可配置 + 告警 |
| **测试方案** | 构造缺失/异常面板验证告警触发 + 阈值边界 |

#### GAP-F07 组合优化层薄（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | 组合合成仅 Elastic Net 截面回归（portfolio_loop.py synthesize_signals）；资本分配仅 vol_target/kelly/fixed（capital_allocator.py）；无 risk parity/均值方差约束优化；无组合级 VaR/ES 约束进优化器（risk_attributor.py 仅为事后归因 VaR95/ES95） |
| **机构级标准** | 机构组合优化器支持均值方差/风险平价/风险预算，含 VaR/ES/换手/集中度约束 |
| **影响** | 组合风险调整能力弱，无法按风险预算精确控制各品种/产业链风险敞口 |
| **实施步骤** | ① 新增 `PortfolioOptimizer`（均值方差 + 风险平价模式，含换手/集中度/杠杆约束）；② 接入 L3 合成模式（`optimizer` 模式）；③ VaR/ES 约束可选；④ 回测/生产统一入口 |
| **测试方案** | 约束满足性 + 与 elastic_net 输出对比 + 无 scipy 时降级 |

#### GAP-F08 样本外纪律执行不彻底（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | walk_forward 为审计可选环节（audit.py 中调用），调度主流程 `l2_evolution_loop_job`（jobs.py L54-119）未强制冷启动验证 |
| **机构级标准** | 每批新因子晋升前必须预留独立冷启动数据段做最终验证（AGENTS.md 4.2） |
| **影响** | 晋升因子可能依赖参数优化段过拟合 |
| **实施步骤** | ① 演化晋升路径强制 WalkForward（默认开启，可配置跳过并记录原因）；② 冷启动段独立于训练/验证段；③ 报告输出 OOS 验证结果 |
| **测试方案** | 晋升流程强制 walk-forward + 配置跳过路径 |

#### GAP-F09 期货保证金/资金效率建模缺失（P1）

| 维度 | 内容 |
|---|---|
| **代码现状** | `capital_allocator.py` 无 margin 参数；回测未考虑保证金占用、强平风险 |
| **机构级标准** | 期货杠杆交易必须建模保证金占用（影响资金效率与最大持仓）与强平风险 |
| **影响** | 资金分配未反映真实可用资金；高杠杆品种风险低估 |
| **实施步骤** | ① 品种保证金率表（可配置）；② CapitalAllocator 增加保证金占用约束；③ 回测杠杆/强平风险告警 |
| **测试方案** | 保证金约束下持仓上限 + 强平阈值触发 |

### 1.3 P2 — 一般差距（优化代码质量与效率）

#### GAP-F10 因子库冗余控制不足（P2）

| 维度 | 内容 |
|---|---|
| **代码现状** | 81 个内嵌种子（seed_data_futures_full.py L2326-2360）与 184 个 YAML 种子（seeds/futures/）两套库并存，重复/一致性无自动校验；家族多样性上限 15 硬编码压制演化空间（记忆：generation 40/48 合格因子因"其他家族达 15 上限"被拒） |
| **机构级标准** | 种子因子单一权威源 + 自动去重校验；家族上限可配置 |
| **实施步骤** | ① 种子去重校验脚本（内嵌 vs YAML 交叉比对）；② 家族上限配置化（BudgetConfig 已支持，缺省值沿用 15）；③ 上报被拒因子日志 |
| **测试方案** | 去重脚本命中重复 + 家族上限可配置生效 |
| **完成记录** | v2.79.0：① 新增 `scripts/verify_seed_dedup.py` 种子去重校验脚本——内嵌种子（`seed_data_futures_full.py`）与 YAML 种子（`seeds/futures/`）交叉比对，识别重复因子 name/表达式与一致性差异并输出差异报告；② 家族上限配置化——`BudgetConfig.max_per_family` 优先，未显式传入 budget 时回退 `FTSConfig.max_per_family`（env `FTS_MAX_PER_FAMILY`，缺省沿用 15）；③ 家族多样性拦截原因随演化日志输出；新增 `tests/scripts/test_seed_dedup.py` 13 用例 |

#### GAP-F11 展期成本未与换月日历联动（P2）

| 维度 | 内容 |
|---|---|
| **代码现状** | `cost_model.py` roll_cost_bps=2.0 常量；roll_calendar.py 与 cost_model 无直接调用，展期成本按固定 bps/次估算，未按换月日历事件逐笔精细计费 |
| **机构级标准** | 展期成本按每次换月事件的实际价差/流动性计费，与换月日历联动 |
| **实施步骤** | ① RollCalendar 换月事件（含 adj_ratio/old_close/new_close）传给 TransactionCostModel；② 单次展期成本可基于实际价差估算（超出固定 bps 时用价差）；③ 报告输出逐次换月成本明细 |
| **测试方案** | 换月事件驱动计费 + 无换月不扣 + 价差超阈值用价差 |

#### GAP-F12 CI 质量门禁弱（P2）

| 维度 | 内容 |
|---|---|
| **代码现状** | `.github/workflows/ci.yml` 仅 pytest+coverage+codecov；无 ruff/mypy 静态与类型检查、无性能基准测试（--benchmark-only）、无发布流水线 |
| **机构级标准** | AGENTS.md 运维命令规范要求 `ruff format/check` + `mypy src/` + 分场景测试 + 性能基准 |
| **实施步骤** | ① CI 增加 ruff check/format 步骤；② 增加 mypy（可先限关键包）；③ 增加基准测试 job；④ 发布流水线（tag 触发 build） |
| **测试方案** | CI 新增步骤在本地等价命令验证通过 |
| **完成记录** | v2.79.0：① CI 新增 lint job（`ruff check` + `ruff format --check` fts/tests/scripts）、type-check job（`mypy fts/`）、benchmark job（`pytest tests/benchmarks/ --benchmark-only`，目录存在时启用）、release job（`v*` tag 触发 `python -m build` + 上传 artifact）；② `mypy fts/` 全量收敛 Success（150 source files，~121 存量错误清零：TypedDict 契约补字段 + `cast` 收敛 + pandas `.to_numpy(dtype)` 统一）；③ ruff 存量违规清零（F401/F821/F841 等）+ 全量 399 文件格式统一（`ruff format --check` 通过）；④ pyproject `dev` extra 补 `mypy`/`ruff`/`pytest-benchmark`；本地等价命令全部通过（CI 门禁前置验证） |

#### GAP-F13 组合漂移监控仅记录不告警（P2）✅ 已完成

| 维度 | 内容 |
|---|---|
| **代码现状** | `L3PortfolioDriftMonitor`（portfolio_loop.py L1650-1746）仅持久化 drift_history，无阈值告警与自动调节闭环 |
| **机构级标准** | 组合漂移超阈值应告警并触发调节（粘性约束已有，但漂移监控应联动） |
| **实施步骤** | ① 漂移阈值可配置（成员重合率/权重 L1 变化率）；② 超阈值告警（日志+Prometheus 指标）；③ 可选自动触发粘性约束重平衡 |
| **测试方案** | 构造高漂移组合验证告警 + 阈值边界 |
| **完成记录** | v2.72.1：`DriftAlertConfig` 阈值可配置 + `check_and_alert` 超阈值告警（日志 + Prometheus 指标）+ `generate_rebalance_proposal` 粘性重平衡建议；`PortfolioLoop` Step 5.5 接入告警（state 标记 `drift_alerted`）+ Step 7 附加重平衡建议；新增 9 用例（TestDriftMonitorAlert 7 + run 集成 2） |

#### GAP-F15 极值扰动一票否决未生效（P2，承接 GAP-042）

| 维度 | 内容 |
|---|---|
| **代码现状** | `high_ic_screener.py` 的「极值样本扰动测试（V2/检查项 5）」依赖外部传入 `extreme_perturbation.ic_drop`，`_promote_to_elite` 未计算该数据 → 该项恒为 skipped，极值扰动一票否决（>25% 降幅）在 L2 入库质检中未真正生效（GAP-042） |
| **机构级标准** | 高IC因子不得仅依赖少数极端样本支撑；极值剔除后重算 IC，降幅 >25% 一票否决 |
| **实施步骤** | ① 回测流水线新增极值剔除重算 IC 能力（剔除信号/收益极值百分位样本后重算）；② `_promote_to_elite` 计算 `extreme_perturbation.ic_drop` 并传入 screener；③ 报告输出扰动前后 IC 对比 |
| **测试方案** | 构造极值依赖因子验证否决触发 + 无极值依赖因子放行 |
| **完成记录** | v2.79.0：① `evaluation_chain` 新增 `_compute_extreme_perturbation_ic` 极值剔除重算 IC（剔除信号上下 `pct` 百分位极端样本后重算，返回 `ic_before/ic_after/ic_drop/n_total/n_removed`，数据不足/常数输入返回 None 由 screener 按数据缺失处理），`evaluate()` 输出 `FactorEvaluation.extreme_perturbation`（`pct` 可配置 `FTSConfig.extreme_perturb_pct` 默认 0.01）；② `_promote_to_elite` 经 evaluation 整体传入 `HighICScreener`，V2 极值扰动一票否决（`ic_drop > 25%`，`HighICScreenConfig.extreme_drop_max` 默认 0.25）真正生效；③ 筛查报告输出扰动前后 IC 对比（ic_before/ic_after）；新增 `tests/factor_engine/test_extreme_perturb.py` 10 用例 |

#### GAP-F16 覆盖率 <90% 模块补齐（P2，承接 GAP-041）✅ 已完成

| 维度 | 内容 |
|---|---|
| **代码现状** | 14 个模块覆盖率 <90%（v2.87.0 后，`tdx_minute_source`/`tq_source` 已合并删除、新源 `tdx_local_source` 93% 达标）：`cross_market/data_adapter(55%)` `factor_clustering(64%)` `tqsdk_tick_source(73%)` `factor_db/migrate_from_json(73%)` `evolution_loop(80%)` `data_quality_monitor(82%)` `ifind_source(84%)` `data(85%)` `factor_db/repository(85%)` `ml/models(86%)` `wind_source(87%)` `factor_screener(87%)` `causal_validator(89%)` `contracts(89%)`（GAP-041） |
| **机构级标准** | 关键路径异常分支须有测试覆盖（外部数据源网络/鉴权路径与异常兜底分支） |
| **实施步骤** | ① 按模块补齐缺失分支测试（外部源网络异常/鉴权失败/降级兜底）；② 覆盖率目标 ≥90%（优先 P1 涉及模块）；③ 更新 06-testing.md 用例统计 |
| **测试方案** | `pytest --cov=fts --cov-fail-under=90` 分模块验证 |
| **完成记录** | v2.88.0：三分组补齐 14 个 <90% 模块测试，新增/扩展 18 个测试文件合计 +341 用例——组A 数据源（139）：`test_ifind_source`(+31)/`test_wind_source`(+13)/`test_tqsdk_tick_source`(+15)/`test_data`(+8)/`test_data_quality_monitor`(+34) + `test_tdx_minute_source`(+25)/`test_tq_source`(+13)（随 v2.87.0 TDX_LOCAL 合并删除，能力迁入 `test_tdx_local_source`）；组B factor_engine（139）：`test_evolution_loop`(+87，TestGapF16* 11 类)/`test_contracts_normalize`(+5)/`test_factor_screener`(新建 35)/`test_causal_validator`(+7)/`test_factor_clustering`(+5)；组C 跨市场/DB/ML（63）：`test_migrate_from_json`(新建 19)/`test_data_layer_repos`(+31)/`test_ml_models`(+8)/`test_mlp_factor`(+2)/`test_gru_factor`(+3)；全量回归 5132 passed（5 个竞态失败——DuckDB 外部进程占锁 + pyproject 版本并发 bump——重跑验证后全绿），覆盖率 TOTAL 94.31% 达标（`--cov-fail-under=90` 通过），GAP-041 关闭 |

### 1.4 P3 — 远期差距（研究探索性）

#### GAP-F14 tick 微观结构研究无回测闭环（P3）

| 维度 | 内容 |
|---|---|
| **代码现状** | tick 数据仅用于近实时分析脚本（scripts/tick_microstructure_analysis.py），无法驱动因子/回测；tick 历史受限 |
| **机构级标准** | tick 微观结构因子可进入因子库并回测 |
| **实施步骤** | ① tick 因子提取接入因子库；② 分钟/tick 回测闭环（复用 frequency 参数）；③ tick 历史缓存扩展 |
| **测试方案** | tick 因子入库 + 分钟回测端到端 |

---

## 2. 实施路线图（与版本路线衔接）

| 版本 | 阶段 | 缺陷项 | 内容 |
|---|---|---|---|
| v2.59.0 | 阶段 B | GAP-F03 优先 | 期货截面因子板块中性化主流程（P0 中风险最低、收益最直接） ✅ 已完成 |
| v2.59.0 | 阶段 B | GAP-F02 | 回测涨跌停拦截 + 停牌过滤 + 被拦截成交统计 ✅ 已完成 |
| v2.60.0 | 阶段 C | GAP-F01 | 实盘执行链路（live_trade 骨架 + 订单状态机 + 止损单 + 人工干预 + 灰度） ✅ 已完成 |
| v2.60.0 | 阶段 C | GAP-F08 | 样本外强制（晋升路径强制 WalkForward + 配置开关 + OOS 报告） ✅ 已完成 |
| v2.60.0 | 阶段 C | GAP-F09 | 保证金建模（保证金率表 + CapitalAllocator 约束 + 强平告警） ✅ 已完成 |
| v2.60.0 | 阶段 C | GAP-F04 | 数据源降级加固（MCP 可配置注入 + 无 MCP 明确降级；tick 缓存回放后续） ✅ 已完成 |
| v2.60.0 | 阶段 C | GAP-F05 | 深度时序模型（轻量纯 numpy MLP 因子，可选依赖，缺依赖优雅降级） ✅ 已完成 |
| v2.60.0 | 阶段 C | GAP-F06 | 数据质量监控（数据级缺失率/异常值/复权一致性/多源分歧 + scheduler 接入） ✅ 已完成 |
| v2.60.0 | 阶段 C | GAP-F07 | 组合优化器（均值方差/风险平价 + 换手/集中度/杠杆/VaR 约束 + scipy 降级） ✅ 已完成 |
| v2.60.0 | 阶段 C | GAP-F11 | 展期成本联动换月日历（后续批） ✅ 已完成 |
| v2.60.0 | 阶段 C | GAP-F13/F15 | 漂移告警闭环 + 极值扰动一票否决（后续批） ✅ 已完成（F13 v2.72.1、F15 v2.79.0） |
| v2.72.1 | 阶段 D | GAP-F13 | 漂移告警闭环（阈值可配置 + 超阈值告警 + 粘性重平衡建议） ✅ 已完成 |
| v2.61.0+ | 阶段 D | GAP-F12/F14/F16 | CI 质量门禁 + tick 回测闭环 + 覆盖率补齐 ✅ 已完成（F12 v2.79.0；F16 v2.88.0；F14 开放） |
| 远期 | — | GAP-F10 | 种子库合并去重 + 家族上限配置化 ✅ 已完成（v2.79.0） |

> 注：GAP-F01（实盘执行）为 P0 但依赖下游交易系统边界（角色边界：FTS 只产信号、FDT 执行），实施重点是"信号侧完备性"（订单契约、人工干预接口、参数隔离），真实网关由下游负责。

---

## 3. 与既有计划的关联

| 关联项 | 说明 |
|---|---|
| GAP-037 | 对应 GAP-F05（深度学习/RL 空白） |
| GAP-041 | 对应 GAP-F16（16 模块覆盖率 <90% 补齐，本计划已登记） |
| GAP-042 | 对应 GAP-F15（极值扰动一票否决未生效，本计划已登记） |
| GAP-045 | adaptive 权重 L3 生产路径对齐，由 plans/19-adaptive-weight-l3-integration.md 单独推进（不在本计划） |
| GAP-046 | 换月复权 + 展期仿真（阶段 A 已完成，plans/20 已归档）；本清单 GAP-F11 为其精细化延伸 |
| plans/20-futures-roll-adjustment-plan.md | 已归档为 GAP-046 实施记录；阶段 B/C 缺陷候选已并入本计划 |
| production_plan.md | GAP-F01 实盘链路与生产就绪路线（Phase 1/4）联动 |

---

## 4. 不在范围

- 真实券商/交易所网关实现（下游 FDT 角色边界）
- 深度学习重依赖引入（GAP-F05 以轻量 MLP 起步，RL 远期登记）
- 因子库全量重建（GAP-F10 以去重校验为主，不重写种子）

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/factor_engine/backtest_pipeline.py`；`fts/factor_engine/evaluation_chain.py`；`fts/factor_engine/cost_model.py`；`fts/data_sources/roll_calendar.py`；`fts/risk/risk_manager.py`；`fts/factor_engine/portfolio_loop.py`；`fts/factor_engine/capital_allocator.py`；`fts/monitor/data_quality_monitor.py`；`fts/data_sources/wind_source.py`；`fts/factor_engine/high_ic_screener.py`；`.github/workflows/ci.yml` |
| 可验证断言 | 16 项差距全部登记（P0×3 / P1×6 / P2×6 / P3×1）；承接 plans/20 阶段 B/C 缺陷候选；GAP-042→GAP-F15、GAP-041→GAP-F16；实施按 §2 路线图推进；每项含代码依据与测试方案 |
| 检验方式 | `python scripts/verify_doc_consistency.py`；各缺陷项落地时配套 `pytest tests/... -v` 回归 |
