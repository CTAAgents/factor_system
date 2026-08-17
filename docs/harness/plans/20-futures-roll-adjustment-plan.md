# 期货连续合约复权与展期仿真实施计划（GAP-046）

> 版本: v2.105.0+3
> 最后更新: 2026-08-09
> 状态: ✅ 已归档（阶段 A 完成；阶段 B/C 缺陷改进已移交 plans/21-futures-maturity-optimization-plan.md）
> 适用范围: FTS 期货数据层 + 回测层 + 成本模型

> ⚠️ **归档说明（v2.58.0）**：本计划核心使命（GAP-046 换月复权 + 展期仿真）已完成。阶段 B/C 的缺陷改进候选清单经机构级对标（plans/21-futures-maturity-optimization-plan.md）后已并入 21 号计划统一推进，本文件仅保留为 GAP-046 实施记录，不再作为推进主线。

---

## 0. 背景与目标

### 0.1 现状问题（GAP-046）

FTS 期货主力连续合约（`{symbol}0`）由 akshare `futures_zh_daily_sina` 直接拼接生成，存在三个缺陷：

| 维度 | 现状 | 问题 |
|---|---|---|
| 换月跳空 | 主力切换日价格直接拼接，无复权调整 | 换月跳空污染因子值/IC，因子在换月日产生伪信号 |
| 展期成本 | 回测无展期成本仿真 | 持仓穿越换月日不扣展期价差，回测高估收益 |
| 换月日历 | `contract_kline` 具体合约表无建表/写入逻辑 | 无法构建真实换月日历（主力判定） |

### 0.2 目标

1. **换月复权**：从 `contract_kline` 具体合约日线按最大成交量判定主力，构建换月日历，比率法后复权（adj_ratio = 切换日新合约收盘/旧合约收盘），消除换月跳空对因子值的污染。
2. **展期成本仿真**：回测持仓穿越换月日扣除 `|position| × roll_cost_bps`，与因子计算的复权序列分离（交易仿真与因子计算解耦）。
3. **数据链路**：`contract_kline` 建表 + 写入逻辑 + 调度集成。

### 0.3 降级策略（GAP-046）

- `contract_kline` 表缺失/无数据 → 返回空换月日历，复权因子为 None，调用方回退原始拼接序列（不报错、不阻断）。
- 切换日任一价格缺失 → 跳过该换月事件（不复权），不传播 NaN。
- `dates`/`roll_dates` 缺失、长度不匹配或空仓 → 展期成本为 0（防越界）。

---

## 1. 阶段 A（GAP-046，已完成 v2.58.0）

### 1.1 实施内容

| # | 模块 | 产出 |
|---|---|---|
| A1 | `fts/data_sources/roll_calendar.py`（新建） | `RollCalendar` 换月日历构建（最大成交量主力判定）+ `RollEvent` 事件契约 + 比率法复权因子 + `apply_adjustment` 复权序列（adj_factor 列） |
| A2 | `fts/data_sources/migrate.py` | `kline_cache` 幂等补 `adj_factor` 列 + 新建 `contract_kline` 表（建表 DDL） |
| A3 | `fts/data_futures.py` | `get_ohlcv(adjusted=True)` 默认返回复权序列；新增 `sync_contract_kline` 具体合约日线写入 |
| A4 | `fts/factor_engine/backtest_pipeline.py` | `_compute_strategy_returns` 持仓穿越换月日扣展期成本；报告新增展期成本统计（roll_dates_count/roll_cost_bps） |
| A5 | `fts/config/settings.py` | `futures_adjusted`（默认 true）/ `roll_cost_bps`（默认 2.0） |
| A6 | `fts/scheduler/jobs.py` | `sync_futures_data_job` 补拉具体合约日线 |
| A7 | `fts/factor_engine/cost_model.py` | `TransactionCostModel` 展期成本项：`CostConfig.roll_cost_bps`（期货默认 2.0）+ `adjust(dates/roll_dates)` 展期成本计入 |

### 1.2 测试（v2.58.0，~22 用例）

- `tests/data_sources/test_roll_calendar.py`：换月日历构建、复权因子、复权序列、降级、展期成本扣除、报告统计、配置默认值
- `tests/factor_engine/test_cost_model.py`：展期成本项 7 用例（默认配置/计入 total/无换月不扣/空仓不扣/长度不匹配防越界/net_sharpe 惩罚/字段存在）
- `tests/data_sources/test_migrate.py`：`tables_created=6`、`columns_added=9`

### 1.3 回归状态

- 相关回归 82 passed（backtest_pipeline / roll_calendar / cost_model / e2e / coverage_edge）
- 文档一致性 13/13 通过

---

## 2. 阶段 B（P1 缺陷改进，进行中）

### 2.1 说明

上一会话整理的 P1 缺陷改进方案（P1-01~P1-11）与缺陷待办事项总表（P0-01~P2-08）未落盘。基于当前开放缺口（`docs/harness/08-gap-analysis.md`）重建候选清单，待确认后按「文档先行 → 实施」推进。

### 2.2 候选清单（当前开放缺口 + 机构级对标）

机构级对标缺陷清单见 [plans/21-futures-maturity-optimization-plan.md](21-futures-maturity-optimization-plan.md)（14 项：P0×3 / P1×6 / P2×4 / P3×1）。阶段 B 优先承接其中 P0 项：

| 编号 | 模块 | 描述 | 优先级 |
|---|---|---|---|
| P1-01 | `fts/factor_engine/portfolio_loop.py` + `adaptive_weight.py` | GAP-045：adaptive 权重 L3 生产路径对齐（`AdaptiveWeightManager`/`RegimeSmoother`/`PortfolioConstructor(weight_method="adaptive")` 仅测试引用），见 `plans/19-adaptive-weight-l3-integration.md` | P1 |
| P1-02 | `fts/factor_engine/high_ic_screener.py` + 回测流水线 | GAP-042：极值样本扰动测试恒 skipped，需回测流水线增加极值剔除重算 IC 能力 | P1/P2 |
| P1-03 | `fts/ml/` | GAP-037：深度学习时序模型（LSTM/GRU/Transformer）与 RL（DQN/PPO/SAC）未实现（= 21 号计划 GAP-F05） | P2 |
| P1-04 | 16 个模块 | GAP-041：覆盖率 <90% 模块补齐 | P2 |
| P1-05 | — | 上一会话 P1-01~P1-11 其余方案项（清单未落盘，待用户提供） | P1 |
| P1-06 | `fts/factor_engine/evaluation_chain.py` + 期货演化路径 | GAP-F03：期货截面因子板块/市值中性化主流程（机构级 P0） | P0 |
| P1-07 | `fts/factor_engine/backtest_pipeline.py` | GAP-F02：回测涨跌停拦截 + 停牌过滤 + 被拦截成交统计（机构级 P0） | P0 |

### 2.3 阶段 B 里程碑（v2.59.0 目标）

| 项 | 说明 |
|---|---|
| 版本 | bump v2.58.0 → v2.59.0 |
| 文档 | 01/02/03/04/06/07/08/09 同步 + 本 plan 更新 |
| 测试 | 新增对应缺陷修复的单元测试，回归全绿 |

---

## 3. 阶段 C（P2 缺陷 + 流水线补强，规划中）

### 3.1 P2 缺口修复（08-gap-analysis 开放项 + 机构级对标）

| 编号 | 模块 | 描述 | 优先级 |
|---|---|---|---|
| C1-01 | `fts/ml/` | GAP-037：深度学习时序模型（LSTM/GRU/Transformer）与强化学习（RL，DQN/PPO/SAC）——需引入 PyTorch/TensorFlow/gym 重依赖，训练成本高、可解释性低（= 21 号计划 GAP-F05） | P2 |
| C1-02 | 16 个模块 | GAP-041：覆盖率 <90% 模块补齐——`cross_market/data_adapter(55%)` `factor_clustering(64%)` `tdx_minute_source(67%)` `tqsdk_tick_source(73%)` `factor_db/migrate_from_json(73%)` `evolution_loop(80%)` `tq_source(81%)` `data_quality_monitor(82%)` `ifind_source(84%)` `data(85%)` `factor_db/repository(85%)` `ml/models(86%)` `wind_source(87%)` `factor_screener(87%)` `causal_validator(89%)` `contracts(89%)` | P2 |
| C1-03 | `fts/factor_engine/high_ic_screener.py` | GAP-042（承接 B）：极值样本扰动一票否决在 L2 入库质检中真正生效（回测流水线极值剔除重算 IC） | P2 |
| C1-04 | `fts/data_sources/wind_source.py` + `ifind_source.py` + `tqsdk_tick_source.py` | GAP-F04：数据源生产可用性加固（WIND/IFIND MCP 注入可配置化 + 降级路径 + tick 可回放） | P1 |
| C1-05 | `fts/monitor/data_quality_monitor.py` | GAP-F06：数据级质量监控（缺失率/异常值/复权一致性/多源分歧率，当前仅因子级 IC/容量） | P1 |
| C1-06 | `fts/factor_engine/evolution_loop.py` 晋升路径 | GAP-F08：样本外纪律强制——晋升前强制 WalkForward 冷启动验证 | P1 |
| C1-07 | `fts/factor_engine/cost_model.py` + `roll_calendar.py` | GAP-F11：展期成本与换月日历事件联动（按实际价差逐笔计费） | P2 |
| C1-08 | `fts/factor_engine/portfolio_loop.py` + `capital_allocator.py` | GAP-F07/F09/F13：组合优化器（均值方差/风险平价）+ 保证金建模 + 漂移告警闭环 | P1 |

### 3.2 股票流水线补强（project_memory 三个方向）

| 编号 | 方向 | 描述 | 状态 |
|---|---|---|---|
| C2-01 | 因子中性化 | 行业/市值双重中性化（`CrossSymbolOps.industry_cap_neutral` + `cross_section_evaluate_backtest` industry_map/cap_map） | ✅ v2.57.0 已完成 |
| C2-02 | A股市场风格/行业轮动 Regime 检测 | 股票流水线缺市场风格/行业轮动 Regime 检测体系（期货有完整产业链 Regime：`SectorRegimeSelector` + HMM 多周期集成） | ⏳ 未开始 |
| C2-03 | 股票 L3 组合策略 | 股票流水线缺 L3 组合策略层（期货有完整 Elastic Net 合成 + Regime-aware 权重调整） | ⏳ 未开始 |
| C2-04 | 股票种子因子扩充 | 股票种子因子文件数（6 个 YAML）远少于期货（20 个 YAML），且多为通用量价因子，缺乏股票特有维度（基本面/资金流/分析师预期） | ⏳ 未开始 |

### 3.3 阶段 C 里程碑（v2.60.0 目标）

| 项 | 说明 |
|---|---|
| 版本 | bump v2.59.0 → v2.60.0 |
| 文档 | 01/02/03/04/06/07/08/09 同步 + 本 plan 更新 |
| 测试 | 新增对应修复的单元测试，回归全绿；覆盖率门禁维持 ≥90% |

---

## 4. 其它待办工作（生产就绪路线，plans/production_plan.md）

### 4.1 Phase 1 基础部署（P0）

| 编号 | 项目 | 描述 |
|---|---|---|
| D1-01 | Dockerfile | 基于 python:3.12-slim，依赖安装 + 配置文件挂载 + 非 root 运行 |
| D1-02 | docker-compose 编排 | 编排 FTS + Prometheus + AlertManager + Grafana 多服务 |
| D1-03 | 熔断告警通知 | Prometheus 告警规则 + AlertManager 绑定真实熔断器状态（`fts_circuit_open` 当前硬编码 0），配置通知通道 |
| D1-04 | 进程守护服务化 | systemd / Windows 服务配置 + 开机自启集成 |

### 4.2 Phase 2 可观测性（P1）

| 编号 | 项目 | 描述 |
|---|---|---|
| D2-01 | 结构化日志 | JSON 格式（含 trace_id/模块/级别/时间戳）+ 按天轮转 + 保留 30 天 |
| D2-02 | Grafana 仪表盘模板 | 历史趋势图表 + 告警状态面板 + Regime/权重变化趋势 |
| D2-03 | 告警通道适配器 | 企业微信/钉钉/Slack 通知适配器（AlertManager webhook 当前为占位符） |

### 4.3 Phase 3 持续集成（P1）

| 编号 | 项目 | 描述 |
|---|---|---|
| D3-01 | CI 增强 | GitHub Actions 补 ruff/mypy lint + Docker 镜像构建推送 + 镜像标签管理（latest + v\*.\*.\*） |
| D3-02 | 自动回归测试 | 每日定时运行（通过率 <95% 告警，失败归档 reports/regression/{date}/） |
| D3-03 | 备份调度 | `fts catalog backup` 定时自动化 + 备份轮转（保留最近 N 个）+ 远程备份评估 |

### 4.4 Phase 4 生产加固（P2）

| 编号 | 项目 | 描述 |
|---|---|---|
| D4-01 | 容器资源限制 | CPU/内存 limits |
| D4-02 | 健康检查 | liveness/readiness probe |
| D4-03 | 配置热重载增强 | 基于已有基础版增强 |
| D4-04 | 多实例部署 | 分布式锁支持 |
| D4-05 | 密钥管理升级 | Phase 4a：SecretProvider 抽象层（`.env`/Vault/AWS），条件触发（接入交易执行/多环境/多人团队） |

---

## 5. 版本路线图

| 版本 | 内容 | 状态 |
|---|---|---|
| v2.58.0 | 阶段 A：GAP-046 换月复权 + 展期仿真 | ✅ 已完成 |
| v2.59.0 | 阶段 B：P1 缺陷改进（候选见 §2.2） | ⏳ 进行中 |
| v2.60.0 | 阶段 C：P2 缺口 + 股票流水线补强（候选见 §3） | ⏳ 规划中 |
| v2.61.0+ | 生产就绪路线（候选见 §4） | ⏳ 规划中 |

---

## 6. 不在范围

- 换月日历的实盘实时生成（当前为日线级别，数据同步由调度任务驱动）
- 深度学习/RL 模型引入（GAP-037，重依赖、可解释性低，优先级 P2，列入阶段 C）
- 股票流水线中性化（v2.57.0 已完成）；股票 Regime 检测 / L3 组合策略列入阶段 C

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/data_sources/roll_calendar.py`；`fts/factor_engine/cost_model.py`；`fts/data_sources/migrate.py`；`fts/data_futures.py`；`fts/factor_engine/backtest_pipeline.py` |
| 可验证断言 | GAP-046 处理中；阶段 A 完成（复权 + 展期成本 + 报告统计）；阶段 B/C 候选清单见 §2.2/§3；生产就绪见 §4 |
| 检验方式 | `pytest tests/data_sources/test_roll_calendar.py tests/factor_engine/test_cost_model.py -v`；`python scripts/verify_doc_consistency.py` |
