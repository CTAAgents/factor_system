# 25 号计划 — 期货因子质检补全计划

> 版本: v2.104.0+97
> 最后更新: 2026-08-11
> 关联: GAP-060~GAP-069（登记于 08-gap-analysis.md）
> 目标版本: v2.90.0 → v2.97.0（A-H）；v2.101.0（I/J）
> 状态: ✅ 全部完成（A-J 十阶段闭环，GAP-060~069 全部关闭）

---

## 1. 背景与目标

对照《期货因子质检六层框架 + 三阶段组合构建》逐项核对 FTS 现状后，确认以下缺口需要补齐。
本计划按「先质检层后组合层」推进，每阶段独立 bump 版本并同步 HARNESS 文档。

## 2. 阶段划分（10 阶段）

| 阶段 | 内容 | 版本 | GAP | 状态 |
|:-----|:-----|:-----|:----|:----|
| A | 多持有期体系（1/5/10/20 日 IC + IC 衰减曲线 + 最佳持有期） | v2.90.0 | GAP-060 | ✅ 完成 |
| B | 可交易性压力层（成本敏感性扫描 + 滑点放大压力测试） | v2.91.0 | GAP-061 | ✅ 完成（并入 v2.97.0 收尾） |
| C | 评估链统计补全（IC t 值 / 日度 IC 胜率 / 最大连续亏损 / Q1-Q5 完整分组 / 信号翻转频率 / 因子截面分散度） | v2.92.0 | GAP-062 | ✅ 完成（并入 v2.97.0 收尾） |
| D | 组合质检三标准（合成增益 / 分散化增益 / 回撤控制） | v2.93.0 | GAP-063 | ✅ 完成（并入 v2.97.0 收尾） |
| E | IC 协方差加权合成（w=Σ⁻¹μ 模式） | v2.94.0 | GAP-064 | ✅ 完成（并入 v2.97.0 收尾） |
| F | 品种间板块联动检测（板块内相关矩阵 + 联动强度 + 因子跨联动分散度） | v2.95.0 | GAP-065 | ✅ 完成（并入 v2.97.0 收尾） |
| G | 夜盘/隔夜跳空标记（overnight_gap 列注入） | v2.96.0 | GAP-066 | ✅ 完成（并入 v2.97.0 收尾） |
| H | 组合级回撤止损 + 相关性熔断（风控输出） | v2.97.0 | GAP-067 | ✅ 完成 |
| I | 会员持仓排名拥挤度（前 20 会员，框架 + 数据降级） | v2.101.0 | GAP-069 | ✅ 完成 |
| J | 多频信号叠加与冲突解决（分钟信号 + 日频聚合 + 叠加消解 + 分钟回测） | v2.101.0 | GAP-068 | ✅ 完成 |

## 3. 延期登记（已实施，保留记录）

| 项 | 原延期原因 | GAP | 现状 |
|:---|:-----|:----|:----|
| 多频信号叠加与冲突解决 | 依赖分钟级数据完备性，属后续专项 | GAP-068 | ✅ 已实施（阶段 J，v2.101.0） |
| 会员持仓排名拥挤度（前 20 会员） | 依赖交易所持仓排名外部数据源，数据层未接入 | GAP-069 | ✅ 已实施（阶段 I，v2.101.0） |
| TWAP/VWAP 执行优化 | 角色边界：执行由 FDT 负责，FTS 仅输出 ScoredSignal | — | 维持延期 |

## 4. 每阶段标准交付物

1. 契约先行：TypedDict/dataclass 字段扩展（contracts.py / 新建模块契约）
2. 代码：新建模块或扩展既有模块（向量化、NaN 兜底、可配置）
3. 测试：新增测试文件，覆盖成功/边界/降级路径
4. 文档：01-architecture / 06-testing / 07-operations / 08-gap-analysis 同步 + 本计划进度
5. 版本：pyproject + 全文档版本头同步（scripts/update_doc_versions.py）
6. 回归：受影响模块定向回归全绿

## 5. 阶段 A 详细设计（多持有期体系，GAP-060）

### 5.1 契约

```python
@dataclass
class HorizonAnalysisResult:
    horizons: list[int]
    ic_by_horizon: dict[int, float]       # 各持有期均值 IC（Spearman）
    icir_by_horizon: dict[int, float]     # ICIR = IC 均值 / IC 标准差
    win_rate_by_horizon: dict[int, float] # 正 IC 占比
    ic_series_by_horizon: dict[int, list] # 各持有期 IC 序列
    best_horizon: int                     # 最大化 ICIR 的持有期
    decay_curve: dict[int, float]         # 持有期 -> IC(h)/IC(1) 相对衰减
    monotonic_decay: bool                 # IC 随持有期单调衰减
```

### 5.2 模块

- 新建 `fts/factor_engine/horizon_analysis.py`：
  - `compute_multi_horizon_ic(signal, close, horizons=(1,5,10,20), min_samples=30)`
  - `compute_ic_decay_curve(result)`
  - `select_best_horizon(result)`
- 集成：`evaluation_chain` 横截面/时序路径可选输出 `multi_horizon` 字段；`FTSConfig.eval_horizons`

### 5.3 测试

- `tests/factor_engine/test_horizon_analysis.py`（~15 用例）：各持有期 IC 形状 / 常量信号兜底 / 短样本降级 / 单调衰减 / 最佳持有期 / 确定性

## 6. 阶段 B 详细设计（可交易性压力层，GAP-061）

### 6.1 契约

```python
@dataclass
class CostSensitivityResult:
    slippage_mults: list[float]           # [1,2,4,8]
    net_sharpe_by_mult: dict[float, float]
    net_ic_by_mult: dict[float, float]
    breakeven_mult: Optional[float]       # 净夏普转负的滑点倍数
    positive_at_max_stress: bool          # 最大倍数下净夏普仍为正
```

### 6.2 模块

- 新建 `fts/factor_engine/cost_sensitivity.py`：复用 `BacktestPipeline` + `TransactionCostModel`，
  对滑点/手续费参数按倍数扫描（1/2/4/8），输出成本后净夏普/净 IC 与盈亏平衡倍数。
- 集成：`evaluation_chain` 可选输出 `cost_sensitivity`；`FTSConfig.cost_sensitivity_enabled`

### 6.3 测试

- `tests/factor_engine/test_cost_sensitivity.py`（~12 用例）

## 7. 阶段 C~H 要点

- C：`BacktestMetrics`/`FactorEvaluation` 契约扩展字段（ic_t_stat / win_rate / max_consecutive_losses /
  quintile_returns / sign_flip_rate / cs_dispersion），`backtest_pipeline._calculate_metrics` 与
  `evaluation_chain` 计算补全。
- D：`portfolio_loop` 组合质检报告（合成增益 = 组合 ICIR/最佳单因子 ICIR；分散化增益 = 组合夏普/权重加权
  因子夏普；回撤控制 = 组合回撤/子策略回撤中位数）。
- E：`weight_learning` 新增 `ic_weight` 合成模式：μ = IC 均值向量、Σ = Ledoit-Wolf 收缩协方差，
  w = (Σ+λI)⁻¹μ，样本 <20 回退 IC 均值加权；`synthesize_signals` 接线。
- F：`sector_linkage.py`：板块内品种收益相关矩阵 + 联动强度（板块内均值相关）+ 因子跨联动板块分散度。
- G：数据层 `get_ohlcv` 输出注入 `overnight_gap` 列（open/prev_close-1），因子输入可选注入；配置开关。
- H：`portfolio_loop` 组合滚动回撤止损（>阈值减仓建议）+ 组合成员收益相关飙升熔断（危机模式平仓建议），
  输出至组合状态/报告。

## 8. 退出标准

- 每阶段：新用例 + 既有受影响模块回归全绿；07 版本历史记录；GAP 关闭登记
- 终期：全量回归通过（预计 5135 + 新增 ~100 用例）、verify_doc_consistency.py 全绿、版本 v2.97.0

---

## 9. 阶段 I 详细设计（会员持仓排名拥挤度，GAP-069，v2.101.0）

### 9.1 目标与范围

接入交易所会员持仓排名（前 N 会员多空持仓）→ 计算持仓集中度/拥挤度指标 → 输出拥挤度信号。**数据源为可选接入**：Provider 接口抽象 + AKShare 实现，数据不可用时优雅降级（返回空 → 因子跳过），不阻断主流程。

### 9.2 契约

```python
@dataclass
class PositionRankConfig:
    top_n: int = 20            # 前 N 会员
    min_rank_rows: int = 5     # 单日最少会员行数，不足降级跳过
    lookback_days: int = 5     # 时序拥挤度滚动窗口

@dataclass
class CrowdingResult:
    symbol: str
    date: str
    cr_top_n: float           # 前 N 会员净持仓占会员总净持仓比（集中度）
    long_short_ratio: float   # 前 N 多头持仓 / 空头持仓
    net_holding_ratio: float  # 前 N 净持仓 / 会员总净持仓
    crowding_score: float     # 综合拥挤度 ∈ [0,1]（越高越拥挤）
    rank_available: bool      # 数据可用标记（False=降级跳过）
    detail: dict[str, Any]
```

### 9.3 模块（`fts/factor_engine/position_rank_crowding.py`）

- `PositionRankProvider`（Protocol）：`get_rank(symbol, start_date=None, end_date=None) -> pd.DataFrame`
  返回 `date/member/long_position/short_position/long_change/short_change`；异常/空返回空 DataFrame。
- `AKSharePositionRankProvider`：按品种前缀路由四交易所（dce/shfe/czce/cffex），akshare 接口异常
  捕获返回空（降级）。
- `compute_crowding(rank_df, config) -> pd.DataFrame`：逐日 cr_top_n / long_short_ratio / net_holding_ratio。
- `crowding_score(cr, lsr, net) -> float`：综合拥挤度（集中度与多空失衡加权，clamp [0,1]）。
- `position_rank_crowding_signal(symbol, config, provider) -> pd.Series | None`：低拥挤=看多（1）、
  高拥挤=反转风险（-1）；数据不可用返回 None。

### 9.4 降级路径

Provider 抛异常 / 空数据 / 单日行数 < min_rank_rows → 对应日期行丢弃 → 全部不可用返回 None，调用方跳过。

### 9.5 测试

契约字段 / 指标计算（合成 rank 数据）/ 因子方向（低拥挤=1 高拥挤=-1）/ 降级（Provider 异常、空数据、行数不足）。

---

## 10. 阶段 J 详细设计（多频信号叠加与冲突解决，GAP-068，v2.101.0）

### 10.1 目标与范围

分钟级信号生成 → 日频聚合 → 与日频信号加权叠加 → 方向冲突消解 → 分钟回测验证。全链路复用
`data_futures.get_minute_ohlcv`（minute_cache → TDX 17709 → TQ-Local → TQSDK 4 级降级链），不新增数据源。

### 10.2 契约

```python
@dataclass
class MultiFrequencyConfig:
    minute_freqs: list[str] = field(default_factory=lambda: ["5m", "15m", "60m"])
    agg_method: str = "last"       # 分钟→日频聚合: last/mean/max/min
    daily_weight: float = 0.6      # 日频信号权重（分钟权重 = 1 - daily_weight，按频率均分）
    conflict_rule: str = "weighted"  # 冲突消解: weighted / penalty / discard
    min_minute_rows: int = 60      # 单日分钟样本下限，不足该日跳过

@dataclass
class MultiFrequencyResult:
    date: pd.Timestamp
    daily_signal: float
    minute_agg: dict[str, float]   # freq -> 聚合分钟信号
    blended: float                 # 叠加后信号
    has_conflict: bool             # 日频与分钟主信号方向是否冲突
```

### 10.3 模块（`fts/factor_engine/multi_frequency.py`）

- `build_minute_signal(ohlcv_minute: pd.DataFrame) -> pd.Series`：分钟 alpha（close/prev_close-1 日内动量，
  或 vwap 偏离，NaN 兜底）。
- `aggregate_minute(minute_signal: pd.Series, method: str) -> pd.Series`：分钟→日频（last/mean/max/min）。
- `blend_signals(daily: pd.Series, minute_agg: dict[str, pd.Series], config) -> tuple[pd.Series, pd.Series]`：
  返回 (叠加信号, 冲突标记)。
- `resolve_conflict(daily, minute_agg, rule)`：方向冲突消解——weighted=按权重合成（默认）/ penalty=
  冲突时削弱分钟贡献 / discard=冲突时丢弃分钟信号。
- `compute_multi_frequency_signal(symbol, config, daily_signal=None) -> MultiFrequencyResult | None`：
  统一入口（get_minute_ohlcv → build → aggregate → blend → conflict）。
- `backtest_minute_signal(symbol, config, cost_bps=2.0) -> dict`：分钟信号日频持有回测（T+1、滑点/手续费），
  输出累计收益/年化/Sharpe/最大回撤/胜率。

### 10.4 降级路径

单日分钟样本 < min_minute_rows → 该日跳过；整个 symbol 无分钟数据 → 返回 None（不阻断日频路径）。

### 10.5 测试

分钟信号计算 / 四种聚合方法 / 叠加权重 / 三种冲突消解规则 / 分钟回测（含成本、方向）/ 数据不足降级。
