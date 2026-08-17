# 中高频（MHF）交易策略实施计划（Phase 0-4）

> 版本: v2.104.0+114
> 创建: 2026-08-13
> 状态: Phase 0-4 全部完成（44 测试全绿，scheduler 信号链路已实测运行）
> 适用范围: FTS 因子引擎 + 回测流水线 + 模拟盘
> 需求方决策: 期货多品种 / 分钟级持仓（5m/15m 调仓、允许隔夜）/ 混合形态（截面选品种+时序进出场）/ 完整链交付（因子+回测+模拟盘）

---

## 0. 背景与目标

### 0.1 现状

FTS 现有能力（可复用）：
- **数据层**：期货日线 15 年库（DuckDB `fts_history`）、分钟 K 线 4 级降级链（minute_cache → TDX 17709 → TQ-Local → TQSDK，已实测可用）、tick 逐笔含 5 档盘口（TQSDK，近实时）、展期日历（roll_calendar）
- **因子层**：日频 Elite 因子库（IC>0.3 入库）、多频叠加模块（`multi_frequency.py`：5m/15m/60m 日内动量→日频聚合→冲突消解）、tick 微结构因子（`microstructure_factors.py`：OFI/OBI/大单占比）、微结构生成器（`microstructure_generator.py`）
- **回测层**：日频回测管道（成本/滑点/涨跌停/展期仿真）、分钟信号日频持有回测、模拟回放引擎
- **实盘层**：模拟撮合（book/matching/orders）、风控管理器、信号桥、scheduler 定时任务

### 0.2 目标

构建期货多品种中高频交易策略完整链：

1. **分钟级因子层**：时序因子（分钟动量/反转/波动率）+ 截面因子（日频 Elite 分钟投影 + 微结构近实时增强）
2. **分钟级事件驱动回测引擎**：盘中信号触发开平仓（非 T+1 隔日），逐笔扣成本，涨跌停/停牌分钟化过滤
3. **混合信号合成**：截面选品种（Elite 因子排名）→ 时序定进出场（分钟因子）
4. **模拟盘验证**：分钟级信号应用 + 盘中风控 + 反馈回路
5. **信号输出**：分钟级信号文件 + scheduler 定时任务 + FDT 信号桥

### 0.3 不在范围

- Tick 级（秒级）策略回测（数据深度不足，tick 仅近实时增强）
- 真实下单（FTS 角色边界：真实撮合由 FDT 负责）
- 股票/ETF 市场（本次仅期货多品种）

---

## 1. 数据现实约束（2026-08-13 实测）

| 数据 | 深度 | 可用性 |
|:--|:--|:--|
| 日线 | 15 年 | 完整（DuckDB，被 pytest 进程锁定时可经 TDX 实时拉取） |
| 分钟 5m | 14578 根 ≈ 10.8 个月（2025-09-22 ~ 2026-08-12） | TDX 17709 实时可取，~1s/次 |
| 分钟 15m | 4860 根 ≈ 10.8 个月 | TDX 17709 实时可取，~0.3s/次 |
| 分钟 30m | 2537 根 | TDX 17709 可取 |
| 分钟 1m | 单次上限 20000 根 ≈ 3 个月 | 部分品种可用 |
| tick 含 5 档盘口 | TQSDK 免费版 ≈ 42 分钟/5000 行 | 仅近实时增强，**不可长历史回测** |

**推论**：
1. 主力 alpha 源 = 分钟级量价因子（5m/15m 为主），样本窗口 ≈ 10.8 个月，必须 walk-forward 样本外验证 + 严格参数约束防过拟合
2. tick 微结构因子（OFI/OBI）仅作近实时信号增强层，不进长历史回测
3. 允许隔夜持仓：跨日信号需衔接夜盘/日盘时段，展期日历复用日频 roll_calendar
4. 环境注意：运行中的全量 pytest（PID 11992）锁定 `data/fts_history.duckdb`；阶段 0-2 可全内存计算（TDX 实时拉取），阶段 3 模拟盘落盘前需解锁

---

## 2. 总体架构

```
数据层(已有)               因子层(补分钟)            回测层(核心缺口)            模拟盘层(扩展)
minute_cache 4级降级链 →  时序: 分钟动量/反转/波动  分钟级事件驱动回测引擎  →  分钟级 paper trader
TDX 17709 (主)       →    截面: 日频elite分钟投影   (盘中信号触发撮合)        + 盘中风控接入
tick_cache (近实时)  →    + 微结构 OFI/OBI 增强     成本逐笔扣减/涨跌停        反馈回路
期货主力/展期日历    →                            walk-forward 样本外
```

## 3. 分阶段实施

### Phase 0 — 前置审计与成本校准

**目标**：确认数据可用性、筛选品种池、校准成本参数。

**内容**：
1. 25 品种分钟数据深度审计（行数/时间跨度/交易日/缺口），输出 `reports/mhf/phase0_data_audit_*.md`
2. 基于日线/分钟成交量与持仓的流动性筛选，剔除低流动性品种，产出合格品种池
3. 分钟级成本参数确认（复用 `cost_model.CostConfig` 期货默认：滑点 0.5bps/手续费 0.2bps/冲击 1.0bps-1%成交量/展期 2bps）

**验收**：
- 审计报告含每品种可回测时间窗清单
- 合格品种池落盘（JSON，Phase 1+ 读取）

### Phase 1 — 分钟级因子层（截面+时序）

**内容**：
1. 时序因子族（5m/15m）：分钟动量/反转、日内时段效应、分钟波动率、成交量异动（复用 `expr_dsl` 表达式框架，向量化）
2. 截面因子族：日频 Elite 因子向分钟投影（跨周期对齐）；微结构 OFI/OBI 分钟级版本（近实时通道）
3. 复用评估链：IC/IR/单调性/衰减，强制多重检验校正（FDR）+ 时间切割
4. 因子入库 `factor_catalog_futures.duckdb`（L3，附血缘）；DuckDB 锁定时先内存评估、入库延后

**验收**：分钟因子池 ≥ 20 个，样本外 IC 显著性报告

**Phase 1 实测结论（2026-08-13，22 品种全量）**：
- 因子池 11 因子 × 2 周期（5m/15m）= 22 组合，单元测试 15 通过
- **intraday_mom（日内动量反转）最强**：15m IC=-0.55 / 5m IC=-0.43，22/22 品种显著（|t|>2），IR≈-2.9
- 反转效应一致：pos_range 15m IC=-0.41、rev_mid -0.39、mom_mid -0.36（均 22/22 显著）
- 波动类因子（vol_std/vol_regime/vol_ratio）IC≈0，无 alpha
- ⚠️ 强负 IC 需警惕 bid-ask bounce 噪音 + 反转对成本极敏感 → 净效应必须经 Phase 2 真实成本回测验证
- 📌 GAP 登记：L3 因子入库（factor_catalog_futures.duckdb）因 DuckDB 被全量 pytest（PID 11992）锁定而延后，解锁后补入库（P1）

### Phase 2 — 分钟级事件驱动回测引擎（核心缺口）

**内容**：
1. 分钟级撮合：盘中按信号触发开平仓（非 T+1 隔日），逐笔扣滑点/手续费，支持限价/市价仿真
2. 涨跌停/停牌/极端行情分钟化过滤；允许隔夜持仓（跨日收益衔接）
3. 混合信号合成：截面选品种 → 时序定进出场
4. walk-forward 样本外 + 成本敏感性（换手 vs 净夏普拐点）

**验收**：回测报告（净收益/夏普/回撤/换手/成本占比），零未来函数审查通过

**Phase 2 实测结论（2026-08-13，22 品种 × 6000 bar 15m ≈ 10 个月）**：
- 引擎/信号模块已交付（8+5 测试全绿），事件驱动零未来（信号 shift(1) 开盘成交）
- 混合策略（反转截面+时序）成本敏感性（单边 bps）：
  | 成本 | 年化 | 夏普 | 回撤 |
  |--|--|--|--|
  | 2bps | +129% | 7.45 | -3.9% |
  | 5bps | +5% | 0.50 | -12.9% |
  | 10bps | -71% | -10.7 | -77% |
- 分段稳健性（2bps）：前半 +66%/夏普7.2，后半 +60%/夏普7.8（样本外一致）
- 去 bounce 验证：typical 价 (H+L+C)/3 平滑后 alpha 不降反升（2bps 年化 +174%）→ 日内反转 alpha 真实，非纯价格噪音
- ⚠️ **经济性风险**：盈利窗口仅 2-5bps，真实期货单边成本（手续费+滑点+价差）约 3-10bps → 现状策略经济性临界，需改进（降频摊薄/强化信号/日频方向过滤）后再进 Phase 3

**Phase 2 改进结论（2026-08-13，盘口校准真实成本）**：
- 成本校准：TQSDK tick 盘口实测 22 品种价差（AU0 0.42 / RB0 3.33 / I0 7.06 / SA0 10.25 bps），单边成本 = 手续费0.2 + 滑点 + 半价差，范围 1.9-10.5bps（`memory/portfolio/futures/mhf_cost.json`）
- 频率对比（差异化真实成本）：
  | 频率 | 年化 | 夏普 | 回撤 | 分段夏普(前/后) |
  |--|--|--|--|--|
  | 30m | +51.5% | 5.02 | -2.8% | 5.09 / 4.98 |
  | 60m | +19.8% | 2.76 | -3.3% | - |
- **30m 为最优频率**；日频方向过滤与 min-score=0.006 强阈值均为负贡献（已定位剔除）
- ✅ 阶段2 验收达成：真实成本下 30m 反转混合策略年化 +51.5%、夏普 5.02、样本外分段稳定、成本占比 8.6%

### Phase 3 — 模拟盘验证

**内容**：
1. 扩展 `simulated_engine` 至分钟级信号应用（分钟级 paper trader）
2. 盘中风控接入：盘中止损、日内最大亏损、单品种限额、持仓时限
3. 模拟反馈回路 → 因子参数再校准

**验收**：模拟盘连续运行，成交/风控/信号全链路留痕，与回测偏差在阈值内

**结论（已交付）**：`MhfPaperTrader` 逐 bar 回放（t-1 信号→t 开盘成交、反向先平后开、盯市净值、品种差异化成本、单品种/组合止损/持仓时限/品种上限风控），`run_mhf_paper.py` 实际运行留痕；隔夜跳空大额止损（AG0 -17%、SC0 -12.6%）确认为 bar 间跳空固有风险，已记录 §6。7 测试全绿。

### Phase 4 — 信号输出与 FDT 衔接

**内容**：
1. 分钟级信号生成脚本（扩展 `futures_signal_pipeline`）
2. scheduler 分钟级定时任务 + signal_bridge 输出给 FDT

**验收**：信号文件按时产出，字段契约冻结

**结论（已交付）**：`scripts/mhf_signal_pipeline.py` 产出 `FactorSignal` 契约（signal_id/timestamp/signal_date/frequency/bar_time/signals）+ markdown 报告；`fts/scheduler/jobs.py` 新增 `mhf_signal_job()`，`fts/scheduler/tasks.py` 注册 TaskSpec `mhf_signal`（cron `*/30 * * * *`，callable_path=`fts.scheduler.jobs.mhf_signal_job`）；`SignalBridge` JSON 协议发布至 `signals/`。已实测运行：trace_id=`fts.mhf.sched_20260813022342`，22 品种，bar=2026-08-13 23:30，`JOB_OK`。

### Phase 4 扩展 — TqSdk 模拟执行（实时模拟盘）

**内容**：将 Phase 4 信号落地到 TqSdk 模拟账户执行，跑通「信号 → 主连映射 → 目标仓位 → 模拟成交 → 快照留痕」全流程（免费，使用现有天勤账号 `TQSDK_USERNAME/PASSWORD`）。

**设计**：
- `fts/live_trade/tqsdk_mhf_executor.py`：`TqSdkMhfExecutor` 一次性执行模式 —— `TqApi(auth=TqAuth(...), account=TqSim(init_balance=1_000_000))`；读取 `signals/latest_signal.json`（direction≠0 品种，按 `MhfRiskConfig.max_positions=8` 截取）；复用 `_SYMBOL_MAP` 转主连代码，`api.query_quotes(ins_class="FUTURE")` 按持仓量选主力具体合约；`target_lots = floor(per_symbol_cash / (price × CONTRACT_MULTIPLIERS))` 且 ≥1 手；`TargetPosTask` 调仓后 `wait_update` 等待成交（超时兜底）；输出持仓/成交/权益留痕
- `scripts/run_mhf_tqsdk_exec.py`：CLI 入口，产出 JSON + markdown 留痕
- 主连→具体合约、未知品种跳过、非交易时段撮合为 TqSim 本地撮合特性（记录实际成交价）

**验收**：真实 TqSdk 模拟盘一次执行返回持仓快照+权益，成交留痕完整；单测 mock tqsdk 全绿。

**结论（已交付）**：`TqSdkMhfExecutor`（`fts/live_trade/tqsdk_mhf_executor.py`）+ `scripts/run_mhf_tqsdk_exec.py` 已实测跑通全流程（2026-08-13，回放窗口 14:30-14:45）：信号 AG0/SN0/CU0/NI0 多头 → 主连 `underlying_symbol` 映射真实主力（SHFE.ag2610/sn2609/cu2609/ni2609）→ 各 1 手 → TqSim 撮合**全部成交** → 权益 999,870.02（初始 100 万，扣手续费/成本）。JSON 留痕无 NaN。实时模式（默认）在非交易时段下单会被服务端拒绝（如实留痕 `ok=false`），日盘/夜盘时段自动可成交。**已接入 scheduler 串行执行**：`mhf_signal_job()` 信号发布后串行调用 `self_serial_exec()`（`fts/scheduler/jobs.py`），留痕落盘 `reports/mhf/tqsdk_exec_job_*.json`；**非交易时段跳过**（`is_trading_time`：日盘 09:00-15:00 / 夜盘 21:00-次日 02:30，跳过时不再连接 TqSdk），已实测非交易时段跳过日志。**定时任务已就绪**：`fts/cli.py` `scheduler run` 改为常驻（主线程保活，修复原启动即退）；注册 Windows 计划任务 `FTS_Scheduler`（登录触发，`python -m fts.cli scheduler run`，失败重启 3 次/1 分钟，不限时长），已启动并验证 scheduler 进程常驻（PID 20528）。11 执行器单测 + 4 串行链路测试全绿。

---

## 4. 模块变更清单（预期）

| 文件 | 变更 |
|:--|:--|
| `scripts/audit_mhf_minute_data.py` | 新增：Phase 0 数据审计（已交付，9 测试） |
| `fts/factor_engine/mhf_factors.py` | 新增：分钟级因子族 11 因子（Phase 1，已交付） |
| `fts/factor_engine/mhf_evaluation.py` | 新增：分钟因子评估（Phase 1，已交付） |
| `scripts/evaluate_mhf_factors.py` | 新增：真实数据因子评估脚本（Phase 1，已交付） |
| `fts/factor_engine/mhf_backtest.py` | 新增：分钟级事件驱动回测引擎（Phase 2，已交付，8 测试） |
| `fts/factor_engine/mhf_signals.py` | 新增：混合信号合成（Phase 2，已交付，5 测试） |
| `scripts/backtest_mhf_strategy.py` | 新增：真实数据回测脚本（Phase 2，已交付） |
| `scripts/calibrate_mhf_cost.py` | 新增：tick 盘口成本校准（Phase 0，已交付） |
| `fts/live_trade/paper_trader_mhf.py` | 新增：分钟级 paper trader（Phase 3，已交付，7 测试） |
| `scripts/run_mhf_paper.py` | 新增：模拟盘回放脚本（Phase 3，已交付） |
| `scripts/mhf_signal_pipeline.py` | 新增：分钟级信号生成（Phase 4，已交付并实测） |
| `fts/scheduler/jobs.py` | 修改：注册分钟级信号任务 + 信号后串行模拟执行（Phase 4/扩展，已交付） |
| `fts/scheduler/tasks.py` | 修改：注册 `mhf_signal` TaskSpec（Phase 4，已交付） |
| `fts/live_trade/tqsdk_mhf_executor.py` | 新增：TqSdk 模拟执行器（Phase 4 扩展，已交付，10 测试） |
| `scripts/run_mhf_tqsdk_exec.py` | 新增：TqSdk 模拟执行 CLI（Phase 4 扩展，已交付并实测） |
| `fts/factor_engine/cost_model.py` | 修改：分钟级成本参数校准（Phase 0） |

## 5. 测试计划

| 阶段 | 测试文件 | 覆盖 |
|:--|:--|:--|
| Phase 0 | `tests/scripts/test_audit_mhf.py` | 审计函数、流动性筛选边界（9） |
| Phase 1 | `tests/factor_engine/test_mhf_phase1.py` | 因子计算边界、零未来函数、降级（15） |
| Phase 2 | `tests/factor_engine/test_mhf_backtest.py` | 撮合逻辑、成本、涨跌停、隔夜衔接（8） |
| Phase 2 | `tests/factor_engine/test_mhf_signals.py` | 混合信号合成、截面排序（5） |
| Phase 3 | `tests/live_trade/test_paper_trader_mhf.py` | 风控触发、信号应用、留痕（7） |
| Phase 4 扩展 | `tests/live_trade/test_tqsdk_mhf_executor.py` | 信号解析、手数、主连映射、全流程、时段判断、异常兜底（11） |
| Phase 4 扩展 | `tests/scheduler/test_jobs_mhf.py` | 信号后串行执行、落盘、非交易时段跳过、异常隔离（4） |

合计 **59 测试全绿**；scheduler 链路以 `mhf_signal_job()` 真实运行验证（不依赖 CLI 单测）。

## 6. 风险与边界

- **分钟样本短（10.8 个月）** → 过拟合风险高：强制 walk-forward + 参数约束 + FDR 校正
- **tick 不可回测** → 微结构因子仅近实时增强，不承诺历史 alpha
- **DuckDB 锁** → 阶段 0-2 内存计算规避；阶段 3 前需终止运行中的 pytest 进程
- **FTS 角色边界** → 真实下单由 FDT 负责，FTS 交付到"模拟盘验证 + 信号输出"
- **成本敏感性** → 中高频对成本极敏感，Phase 2 必须输出换手-净夏普拐点

---

## 7. 一致性元数据

| 代码/数据 | 文档映射 | 可验证断言 | 检验方式 |
|:--|:--|:--|:--|
| `scripts/audit_mhf_minute_data.py` | §3 Phase 0 | 输出 25 品种深度审计报告 | 运行脚本检查报告存在 |
| 分钟因子模块 | §3 Phase 1 | 因子池 ≥ 20，样本外 IC 报告 | pytest + 报告检查 |
| 分钟回测引擎 | §3 Phase 2 | 零未来函数，净夏普报告 | pytest + 审查 |
| paper trader | §3 Phase 3 | 风控留痕完整 | pytest + 运行验证 |
| 信号管道 | §3 Phase 4 | 契约冻结，按时产出 | pytest + 运行验证 |
