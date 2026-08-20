# D.1 模拟仓模块 — 详细技术设计

> 版本: v3.1.0+3
> 状态: **已实现**（`fts/live_trade/` 新增 `contracts.py` / `simulated_portfolio.py` / `simulated_engine.py` / `sqlite_store.py` + 回放脚本 + 测试）
> 关联: [C.2-live-trading-integration-design.md](file:///d:/Programs/factor_system/docs/harness/design/C.2-live-trading-integration-design.md)（实盘对接）、[C.3-feedback-loop-design.md](file:///d:/Programs/factor_system/docs/harness/design/C.3-feedback-loop-design.md)（反馈闭环）
> 前置: C.2 已实现（信号契约/风控/模拟适配/Live 反馈闭环）

---

## 1. 背景与问题

FTS 定位为**因子智能系统**，输出 `ScoredSignal` / `FactorSignal` 供下游交易系统（FDT）消费。反馈闭环（GAP-L402）的入口是 `LiveFeedbackRecord`（含 `factor_id / signal_date / signal_value / position_return / turnover / market`），由 `LiveFeedbackImporter` 落盘并计算实盘 IC，`LiveVsBacktestICReport` 据此判定因子衰减并触发退役/重校准。

**断点**：当前没有任何模块从"信号 → 模拟成交 → 逐日盯市 → 因子收益归因"生成 `position_return` 反馈记录。现有 `SimulatedTradeAdapter` 仅在信号价格处填一个持仓快照，无 PnL/保证金/权益核算，无法产出反馈闭环所需数据。

**目标**：新增**模拟仓模块**，用仿真交易替代真实账户，打通"信号 → 模拟撮合 → 盯市核算 → 因子归因 → 反馈闭环"的完整链路，使因子表现回流到演化方向调整。

---

## 2. 角色边界

- FTS 只产信号 + **模拟核算**，不接真实账户；真实撮合仍由下游 FDT 负责。
- 本模块的模拟撮合纪律与回测仿真对齐（滑点/手续费/保证金/涨跌停），避免"回测宽松、实盘严格"。
- 风控/干预逻辑复用既有 `RiskManager` / `InterventionController`，下单前强制校验。

---

## 3. 模块划分

```
fts/live_trade/
├── contracts.py             # 模拟仓契约（新增）
├── simulated_portfolio.py   # 模拟仓核心（新增）：持仓/盯市/撮合/风控/归因/闭环
├── simulated_engine.py      # 回放引擎 + 实时纸面（新增）
├── sqlite_store.py          # SQLite 持久化层（新增）：账户/持仓/成交/权益四表
└── (复用) gateway / orders / intervention / stop_orders
```

```
scripts/simulated_replay.py  # 历史回放落地脚本（新增）
tests/live_trade/test_simulated_portfolio.py  # 单元测试（新增）
```

---

## 4. 契约设计（contracts.py）

### 4.1 持仓/账户/日度记录

```python
class SimPosition(TypedDict, total=False):
    symbol: str
    market: str            # futures | stock | etf
    direction: str         # long | short
    quantity: float        # 期货=手数，股票/ETF=股数
    avg_price: float       # 开仓均价（含滑点/手续费影响折算）
    multiplier: float      # 合约乘数（期货），股票=1.0
    margin_rate: float     # 保证金率（期货），股票=1.0
    opened_at: str
    realized_pnl: float    # 该持仓已实现盈亏（部分平仓累计）

class SimDailyRecord(TypedDict, total=False):
    date: str
    equity: float          # 总权益 = cash + position_value - margin_used_term + unrealized
    cash: float
    margin_used: float     # 保证金占用（期货）
    position_value: float  # 持仓市值
    realized_pnl: float
    unrealized_pnl: float
    daily_pnl: float
    turnover: float        # 当日换手
    n_positions: int

class SimApplyResult(TypedDict, total=False):
    signal_id: str
    date: str
    approved: bool
    fills: list[SimFill]
    blocked_reasons: list[str]
    trace_id: str
```

### 4.2 撮合结果

```python
class SimFill(TypedDict, total=False):
    order_id: str
    symbol: str
    side: str              # open_long / open_short / close_long / close_short
    quantity: float
    fill_price: float      # 含滑点
    fee: float
    slippage_cost: float
    timestamp: str
```

### 4.3 合约规格（保真核算依赖）

- 合约乘数：`CONTRACT_MULTIPLIERS`（交易所公开固定规格，与 `scripts/liquidity_snapshot.py` 一致；支持 `AU0→AU` 后缀剥离，未知品种回退 1.0）。
- 保证金率：优先 `FTSConfig.margin_rate_map`，缺省回落 `CostConfig.margin_rate`（期货 0.12 / 股票 1.0）。
- 成本：`TransactionCostModel` 提供按市场的 `slippage_bps / commission_bps / impact_bps_per_pct`。

```python
def contract_multiplier(symbol: str) -> float:
    """返回品种合约乘数；未知品种 1.0。支持主连后缀（AU0 -> AU）。"""

def infer_market(symbol: str) -> str:
    """按代码形态推断市场：期货（字母+数字，如 RB0）/ 股票（6位数字）。
    优先使用显式 market 参数，无法推断时返回配置默认 market。
    """
```

---

## 5. 核心设计（simulated_portfolio.py）

### 5.1 `SimulatedPortfolio` 主类

```python
class SimulatedPortfolio:
    def __init__(self, config: SimPortfolioConfig,
                 gateway=None, cost_model=None,
                 risk_manager=None, intervention=None,
                 store: SimSQLiteStore | None = None) -> None:
        # store 注入后启动即从 SQLite 恢复既有账户/持仓/权益；
        # None 表示不持久化（回放引擎使用，保持幂等）。
        ...

    def apply_signal(self, signal: dict, prices: dict, date: str) -> SimApplyResult: ...
    def mark_to_market(self, date: str, prices: dict) -> SimDailyRecord: ...
    def close_symbol(self, symbol: str, price: float) -> SimFill | None: ...
    def all_close(self, prices: dict) -> list[SimFill]: ...
    def account_status(self) -> dict: ...
    def positions(self) -> dict: ...
    def equity_curve(self) -> list[SimDailyRecord]: ...
    def attribute_factor_returns(self, signal: dict, next_return: dict) -> list[dict]: ...
```

### 5.2 撮合流程（apply_signal）

1. **干预门**：`intervention.should_block()` 为真 → 拦截，记录 `blocked_reasons=['intervention']`。
2. **目标仓位映射**：对每个 `SignalDetail`（symbol/direction/position/price），与当前持仓对比生成目标数量：
   - `long` 目标 +qty，`short` 目标 −qty，`flat` 目标 0。
3. **风控门**：`risk_manager.check(signal, account_status, positions)`，`approved=False` → 拦截并留痕 `blocked_reasons`。
4. **撮合**：对每个符号，delta = 目标 − 当前。delta>0 开仓/加仓，delta<0 平仓/反手。复用 `SimulatedGateway.submit_order` + `OrderLifecycle`，成交价含滑点，扣手续费。
5. **持仓更新**：更新 `_positions` 与 `_cash`（期货开仓扣保证金，平仓释放并结转已实现盈亏；股票全额扣现金）。

### 5.3 盯市核算（mark_to_market）

- 逐持仓计算未实现盈亏：
  - 期货：`(close − avg_price) × multiplier × quantity × (1 if long else −1)`
  - 股票：`(close − avg_price) × quantity`
- 汇总 `equity = cash + unrealized − margin_used`（期货），股票 `equity = cash + position_value`。
- 更新峰值权益、单日盈亏、换手，追加 `SimDailyRecord`。

### 5.4 因子收益归因（attribute_factor_returns）

对每个信号日：

1. 计算每符号方向收益 `symbol_return = next_return × direction_sign`（long:+ / short:−）。
2. 对每个出现在 `contributing_factors` 的因子 `f`：
   - `signal_value = Σ(position × factor_signal) / Σ position`（按仓位加权的因子信号均值）
   - `position_return = Σ(position × symbol_return) / Σ position`（该因子贡献符号集的组合收益）
3. 产出 `LiveFeedbackRecord(factor_id, signal_date, signal_value, position_return, turnover, market)`。

### 5.5 反馈闭环落盘

归因记录 → `LiveFeedbackImporter.import_records`（DuckDB `feedback_live` 表）→ `LiveVsBacktestICReport.generate` 判定衰减 → 退役/`RecalibrationQueue`。闭环数据流：

```
因子信号 → 模拟撮合 → 逐日盯市 → 因子归因 → LiveFeedbackRecord
    → feedback_live 表 → LiveVsBacktestICReport → 衰减判定 → 演化方向调整
```

### 5.6 SQLite 持久化（sqlite_store.py）

账户/成交/权益等**核算状态**用 SQLite 持久化（`sqlite3` 标准库，零额外依赖），替代 `SimulatedPaperTrader` 此前 `paper_state.json` 轻量快照。四表：

| 表 | 键 | 写入时机 | 说明 |
|----|----|----------|------|
| `sim_account` | 单行 | 每次信号/盯市 | 初始资金/现金/峰值权益/上一权益/当日盈亏/累计已实现/上期换手 |
| `sim_positions` | `symbol` | 每次信号/盯市 | 持仓明细（全量替换） |
| `sim_fills` | `order_id` | 每次信号成交 | 成交流水（追加，冲突忽略） |
| `sim_equity_curve` | `date` | 每次盯市 | 逐日权益（追加，冲突忽略） |

- 事务保证：每个写操作在独立事务内提交；WAL 模式提升并发读写。
- 恢复语义：`load_*` 无数据返回空，缺失/损坏零风险不抛出。
- 与反馈闭环解耦：`LiveFeedbackRecord` 仍走 DuckDB/JSONL（`LiveFeedbackImporter`），不受 SQLite 影响。
- 兼容性：`SimulatedReplayEngine` 不注入 store（保持幂等、可复现）；`SimulatedPaperTrader` 注入 store 实现跨会话状态延续。

---

## 6. 回放引擎（simulated_engine.py）

### 6.1 `SimulatedReplayEngine`

```python
class SimulatedReplayEngine:
    def replay(self, signals: list[dict], panel: dict[str, DataFrame],
               portfolio: SimulatedPortfolio) -> ReplayResult:
        """按时间顺序回放：
        - t 日应用信号，t+1 开盘价成交（避免未来函数）
        - t+1 收盘盯市
        - 用 t→t+1 收益做因子归因，累积反馈记录
        """
```

- 严格时间单向：`t` 日信号只用 `t+1` 开盘价成交，杜绝未来函数。
- 返回 `ReplayResult { equity_curve, feedback_records, fills, summary }`。
- 反馈记录可经 `LiveFeedbackImporter` 一次落盘。

### 6.2 `SimulatedPaperTrader`

实时纸面包装：每日 `apply_signal` + `mark_to_market`，账户/持仓/成交/权益经注入的 `SimSQLiteStore` 持久化到 `memory/portfolio/simulated/sim_state.db`，供持续闭环与跨会话状态延续。

---

## 7. 数据流与降级

| 依赖 | 降级策略 |
|------|----------|
| 行情缺失 | 跳过该日盯市，不中断 |
| 信号缺失 | 保留既有持仓，仅记日志 |
| DuckDB 不可用 | 反馈记录回退 JSONL（复用 `LiveFeedbackImporter` 既有逻辑） |
| 风控/干预异常 | 保守拦截（`approved=False`），不成交 |

---

## 8. 测试要点

| # | 场景 | 断言 |
|---|------|------|
| 1 | 开多头仓位 | 持仓/现金/保证金正确 |
| 2 | 加仓/平仓/反手 | 数量与已实现盈亏正确 |
| 3 | 逐日盯市 | 权益/未实现盈亏正确 |
| 4 | 风控拦截 | `apply_signal` 返回 `approved=False` 且 `blocked_reasons` 非空 |
| 5 | 干预拦截 | 暂停后信号被拦 |
| 6 | 因子归因 | 归因记录 `position_return` 与手算一致 |
| 7 | 回放引擎 | 权益曲线单调、记录数正确、无未来函数 |
| 8 | 合约乘数/市场推断 | `AU0→1000`、`RB→10`、股票→1.0；市场推断正确 |
| 9 | SQLite 存取 | 账户/持仓/成交/权益四表 round-trip 一致；重开连接可加载 |
| 10 | SQLite 恢复 | 重启后恢复持仓数量/现金/权益曲线 |
| 11 | PaperTrader 持久化 | 信号/盯市后 SQLite 落盘，跨会话延续 |

---

## 9. 文件改动清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `fts/live_trade/contracts.py` | **新增** | 模拟仓契约 + 合约乘数/市场推断辅助 |
| `fts/live_trade/simulated_portfolio.py` | **新增** | 模拟仓核心（持仓/盯市/撮合/风控/归因/闭环）+ 可选 store 持久化 |
| `fts/live_trade/simulated_engine.py` | **新增** | 历史回放引擎 + 实时纸面包装（PaperTrader 用 SQLite 持久化） |
| `fts/live_trade/sqlite_store.py` | **新增** | SQLite 持久化层（账户/持仓/成交/权益四表，WAL+事务） |
| `fts/live_trade/__init__.py` | **修改** | 导出新模块符号（含 `SimSQLiteStore`） |
| `scripts/simulated_replay.py` | **新增** | 历史回放落地脚本（期货+股票） |
| `tests/live_trade/test_simulated_portfolio.py` | **新增** | 模拟仓单元测试 |
| `docs/harness/design/D.1-simulated-portfolio-design.md` | **新增** | 本文档 |
| `docs/harness/01-architecture.md` 等 | **修改** | 同步架构/生命周期/测试/版本（清单同步） |

---

## 10. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 关联文档 | [C.2-live-trading-integration-design.md](file:///d:/Programs/factor_system/docs/harness/design/C.2-live-trading-integration-design.md)、[C.3-feedback-loop-design.md](file:///d:/Programs/factor_system/docs/harness/design/C.3-feedback-loop-design.md) |
| 依赖模块 | `gateway` / `orders` / `intervention` / `stop_orders`、`fts.risk.RiskManager`、`fts.factor_engine.cost_model`、`fts.factor_engine.feedback_loop`（GAP-L402） |
| 前置条件 | C.2 已实现 |
| 后置影响 | 完成信号→模拟交易→反馈闭环 |
| 角色边界 | FTS 只模拟核算，真实撮合由 FDT 负责 |