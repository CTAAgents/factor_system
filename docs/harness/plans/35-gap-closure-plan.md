# 35-gap-closure-plan.md — 全链路缺口关闭实施方案（G1–G17）

> 版本: v3.0.0+25
> 状态: **全部完成（v2.104.0 里程碑验收通过）**——G1-G17 全部实现+测试；G16 批次末评估=维持暂缓（§5.9）；G17 FDT 交接（§5.10）；全量回归 `pytest tests/ -m "not slow"` **6621 passed 全绿**（2026-08-13）
> ⚠️ **归档注记（v2.104.0+25）**：本计划为历史已完成计划，正文中 family 相关内容（§5.9 G16 契约 `{code, family, ic, icir, robustness_meta}` / `suggested_family` 字段、§2 关键核查基线 "family/style 双维倍率表"）为历史记录——v2.104.0+25 因子家族概念已彻底移除（`REGIME_FAMILY_MULTIPLIERS`/RegimeMultiplierEstimator 删除、分类判定仅用 style_tags、分组/配额走信号聚类），G16 维持暂缓不启用，正文保留原文仅供历史参考。
> 日期: 2026-08-13
> 范围: 对照《完整全链路：原始数据挖掘 → 最终下单可交易信号》SOP 的缺口关闭
> 关联: 34-evolution-loop-refactor-inventory.md（存量重构盘点）、08-gap-analysis.md（差距登记）

---

## 1. 背景与目标

对照团队 CTA 多因子全链路 SOP（8 阶段），对 `d:\Programs\factor_system` 全代码库逐行核查后，
识别出 17 项缺口（G1–G17），按对「局部最优共振踩踏」核心命题的威胁度分为三批次：

- **批次一 P0（组合风控防线，直接对应核心命题）**：G1 同向敞口惩罚 / G2 集中踩踏规避 / G3 换手全局上限
- **批次二 P1（因子筛选严谨性）**：G4 ICIR 与符号反转硬门槛 / G5 Bootstrap / G6 ADF / G7 5-Regime 拆分检验
- **批次三 P2（基础设施与工程底座）**：G8 日历与脏数据 / G9 MAD / G10 中性化 / G11 换手硬剔除 / G12 信号契约 / G13 月度调度 / G14 Regime 风控参数 / G15 方差最小化 / G16 LLM 审核 / G17 柜台对接

**总原则**（继承 AGENTS.md / CLAUDE.md）：
契约优先（先 TypedDict/接口）→ 测试先行（TDD）→ 外科手术式修改 → 每批末 bump build 版本 →
文档同步（13 项检查清单）→ 阈值以数据校准为准，禁止直接套用外部硬值。

---

## 2. 缺口总览

| 批次 | 缺口 | 核心模块 | 现状判定 | 验证方式 |
|---|---|---|---|---|
| P0 | G1 同向敞口惩罚 | portfolio_risk_controls.py + portfolio_loop.build_combo | 未实现 | 组合回测同向共振窗口对比 |
| P0 | G2 集中踩踏止损规避 | portfolio_risk_controls.py + stop_orders.py | 未实现 | 批量平仓节流单测 |
| P0 | G3 换手率全局上限 | portfolio_loop.apply_turnover_penalty | 部分（默认关闭） | 换手预算分配单测 |
| P1 | G4 ICIR/符号反转硬门槛 | evaluation_chain.py + walk_forward.py + high_ic_screener.py | 部分 | 门槛拒收单测 |
| P1 | G5 Bootstrap 自助抽样 | robustness.py | 未实现 | 块抽样 CI 单测 |
| P1 | G6 ADF 平稳性检验 | robustness.py | 未实现 | 平稳/非平稳判别单测 |
| P1 | G7 5-Regime 拆分检验 | regime_validation.py + high_ic_screener.py | 部分（WF 近似） | 制度拆分单测 |
| P2 | G8 交易日历 + 断K/跳空清洗 | data_sources/trading_calendar.py（新）+ data_futures.py | 部分 | 日历/清洗单测 |
| P2 | G9 MAD 进 Standardizer | standardizer.py | 算子层有/方法层无 | 厚尾截断对比单测 |
| P2 | G10 波动率/季节性中性化 | barra/barra_neutralizer.py + evolution_futures.py | 未实现 | 中性化归零单测 |
| P2 | G11 换手 >20% 因子级硬剔除 | evaluation_chain.py + high_ic_screener.py | 未实现 | 阈值淘汰单测 |
| P2 | G12 信号输出统一契约 | signal_contract.py + 三管道 | 部分（字段不全） | validator 全链路单测 |
| P2 | G13 月度重跑自动调度 | scheduler/tasks.py | 机制有/调度无 | dry-run 单测 |
| P2 | G14 杠杆/止损随 Regime | regime_multipliers.py + risk/risk_manager.py | 未实现 | 参数解析单测 |
| P2 | G15 方差最小化仓位 | capital_allocator.py | 未实现 | 解析解单测 |
| P2 | G16 LLM 审核（辅助标注） | llm.py + factor_inspector.py | 未实现 | 辅助标注单测 |
| P2 | G17 真实柜台对接（FDT 交接） | fts/live_trade/gateway.py 契约 | 仿真层 | 契约验收桩 |

**关键核查证据基线**（决定阈值时必须重跑，见 §9）：
- IC 硬门槛现为 0.03 / Sharpe 1.5（evaluation_chain.py L700-716）
- ICIR 合格线 0.5 仅在 high_ic_screener 打分体系，无硬门槛
- 换手现为周度 80% 上限打分（high_ic_screener.py L54），无日换手硬剔除
- 参数扰动阈值 0.01 / 保持率 0.5 / OOS 衰减 ≤30%（robustness.py L222-224）
- 前后半段仅衰减率 decay_6m，无符号反转硬检查（evaluation_chain.py L422-429）
- WFA 仅 IC 一致性 ≥0.5 / 波动 ≤0.3，无 ICIR 门槛（walk_forward.py L164）
- Regime 5 类：bull/bear/oscillate/high_vol/low_vol；family/style 双维倍率表（portfolio_loop.py L362-410）
- 风控参数固定常量：杠杆 3x / 单品种 10% / 集中度 50% / 日亏 5% / 回撤 20%（risk_manager.py）
- 信号契约已有 FactorSignal/SignalValidator（signal_contract.py），缺手数/风险占用等字段

---

## 3. 批次一 P0：全局耦合风控（G1–G3）

### 3.1 G1 同向敞口惩罚

**目标**：多个因子同向发信号时非线性压缩该品种仓位，切断「局部最优共振重仓」。

**契约**（`portfolio_risk_controls.py` 新增）：

```python
@dataclass
class AlignedExposureConfig:
    enabled: bool = True
    lookback: int = 5             # 同向判定窗口（交易日）
    align_threshold: float = 0.6  # 同向占比 ≥60% 触发压缩
    max_compress: float = 0.5     # 最大压缩至原仓位 50%
    compress_curve: str = "linear"  # linear | sqrt | exp

def check_aligned_exposure(
    signals: pd.DataFrame,          # index=日期, columns=因子, 值 ∈ {-1,0,+1}
    config: AlignedExposureConfig = ...,
) -> dict:
    """返回 {triggered, alignment_ratio, compress_scale, symbol}"""
```

**算法**：
1. 对每个品种取最近 `lookback` 日各因子方向（仅用当日及之前，零未来函数）；
2. 同向占比 = max(看多占比, 看空占比)；占比 < `align_threshold` → 不压缩；
3. `compress_scale = 1 - (ratio - threshold)/(1 - threshold) * (1 - max_compress)`；
4. `compress_curve="sqrt"` 时对 scale 开方（更温和），`"exp"` 时指数衰减（更激进）。

**接入点**：`portfolio_loop.build_combo`（L2005）内，粘性约束/换手惩罚之后、`_validate_combo_sharpe` 之前，
乘入每品种 scale（复用现有 `regime_meta`/`exposure_scale` 传递链 L2034-2035 同一位置）。

**配置**：`fts/config/settings.py` 的 FTSConfig 新增 `aligned_exposure: AlignedExposureConfig`，默认开启。

**测试**（`tests/factor_engine/test_portfolio_risk_controls.py` 新增）：
- 5 因子全部同向 → scale=0.5；
- 2 多 2 空 1 平 → 不触发（分歧天然对冲）；
- 边界占比恰好 0.6 → 精确触发；
- 零未来函数：窗口仅含当日及之前数据。

### 3.2 G2 集中踩踏止损规避

**目标**：同一时点批量触发止损时按风险敞口分批平仓，降低冲击成本与踩踏。

**契约**（`portfolio_risk_controls.py` 新增）：

```python
@dataclass
class ExitStampedeConfig:
    enabled: bool = True
    max_same_day_exits: int = 3      # 单日最大同时平仓数
    batch_gap_days: int = 1          # 分批间隔（交易日）
    order_by: str = "exposure_desc"  # exposure_desc | sharpe_asc

def throttle_exit_stampede(
    exit_signals: pd.DataFrame,   # 触发平仓的合约×日期
    exposures: pd.Series,         # 各合约当前敞口
    config: ExitStampedeConfig = ...,
) -> pd.DataFrame:
    """返回分批平仓计划（超出单日上限的合约重排到后续交易日）"""
```

**算法**：按 `order_by` 排序当日触发清单 → 取前 `max_same_day_exits` 当日执行 → 其余按 `batch_gap_days` 顺延，
顺延时若再次触发止损则以最新价执行。仅重排执行顺序，不取消止损触发（保住纪律）。

**接入点**：
- 实盘侧 `fts/live_trade/stop_orders.py` `StopOrderManager.check` 之后、报单前；
- 回测侧 `backtest_pipeline.py` 止损批量触发点（与 `_build_tradeable_mask` 同层）。

**测试**：10 合约同日触发 → 输出 4 批计划，每天 ≤3 单，优先平最大敞口；单日 ≤3 单不重排。

### 3.3 G3 换手率全局上限约束（含边际收益剔除）

**目标**：单日总换手超上限时剔除边际收益最低的弱信号，降低摩擦成本。

**契约**（`portfolio_loop.py` 附近新增独立函数，或新建 `portfolio_turnover.py`）：

```python
@dataclass
class TurnoverBudgetConfig:
    daily_turnover_cap: float = 0.30   # 单日组合换手上限（30%）
    prioritize_by: str = "sharpe"      # sharpe | icir | score
    drop_weakest: bool = True

def allocate_turnover_budget(
    target_weights: dict[str, float],
    current_weights: dict[str, float],
    factor_scores: dict[str, float],   # 每品种综合得分
    config: TurnoverBudgetConfig = ...,
) -> dict[str, float]:
    """返回裁剪后目标权重；超限时按 prioritize_by 逐品种剔除/降仓"""
```

**算法**：计算 `turnover = Σ|target - current|/2`；≤cap 直接返回；超限时按 `prioritize_by` 排序，
从最弱品种开始逐步剔除（drop_weakest）或等比降仓（drop_weakest=False）直至达标。

**接入点**：替换 `portfolio_loop.apply_turnover_penalty`（L1876）调用点；保留旧函数做兼容回退。
现有 `turnover_penalty` 配置默认值从 0 改为 0.15（λ 换手惩罚启用）。

**测试**：构造 20 品种全换仓 → 换手压至 ≤30%；验证被剔除的是最低 Sharpe 品种；
旧 `apply_turnover_penalty` 回归不受影响。

---

## 4. 批次二 P1：因子筛选严谨性（G4–G7）

### 4.1 G4 ICIR 硬门槛 + 前后半段符号反转硬检查

**目标**：补齐筛选三处硬门槛——ICIR 硬门槛、WFA ICIR 门槛、前后半段符号反转一票否决。

**配置**（新增 `ScreeningThresholds`，放 `contracts.py` 或 `factor_quality_card_config.py`）：

```python
@dataclass
class ScreeningThresholds:
    ic_min: float = 0.03            # 沿用现有
    icir_min: float = 0.30          # 新增硬门槛（框架淘汰线 0.3）
    icir_pass: float = 0.50         # 打分合格线（沿用 high_ic_screener）
    half_split_sign_flip: bool = True  # 前后半段符号反转 → 一票否决
```

**实现**：
1. **ICIR 计算补齐**：`evaluation_chain.py` 的 `_block_ic_stats`（L182-206）与横截面路径需输出
   `icir = ic_mean / ic_std`（std 极小值保护 1e-10）；
2. **ICIR 硬门槛**：`evaluation_chain.py` L700 区域新增
   `if abs(icir) < icir_min: fail("Level 1: ICIR < 0.30")`；
3. **WFA ICIR 门槛**：`walk_forward.py` `DEFAULT_WALK_FORWARD_CONFIG` 新增 `min_oos_icir=0.25`，
   `passed` 判定（L164）加入 `icir >= min_oos_icir`（窗口级）；
4. **符号反转检查**：`evaluation_chain.py` L422-429 旁新增
   `sign_flip = ic_first * ic_second < 0`，True 时计入失败原因并与 high_ic_screener V1 一票否决联动。

**⚠️ 数据校准前置**（见 §9）：先跑现有因子库 ICIR 分布，用分位数定阈值，禁止直接套 0.30。

**测试**：构造 IC 高但 ICIR<0.3 的因子 → 拒收；前正后负因子 → 符号反转否决；现有全量因子通过率变化记录对比。

### 4.2 G5 Bootstrap 自助抽样检验

**目标**：随机剔除样本后绩效不崩塌的统计验证。

**契约**（`robustness.py` 新增）：

```python
class BootstrapConfig:
    n_bootstrap: int = 500
    block_size: int = 20          # 时间块长（保序列相关）
    ci_level: float = 0.95
    min_ci_lower_ic: float = 0.0

def bootstrap_ic_ci(
    factor_signal: np.ndarray, forward_returns: np.ndarray,
    n_bootstrap: int = 500, seed: int = 42,
) -> dict:
    """块抽样 → IC 分布 → 95%CI → {ic_mean, ci_lower, ci_upper, passed}"""
```

**算法**：块抽样（block bootstrap，块长 20）保留自相关结构，禁止 iid 重抽样（会高估显著性）；
固定 `seed=42` 保证可复现；`ci_lower < min_ci_lower_ic` → 不通过。

**接入点**：`high_ic_screener` 打分体系新增一票否决项；`audit.py` 6 项强制审计清单追加。

**测试**：真实高 IC 因子 CI 下界>0 通过；仅 5 个样本的因子 CI 过宽 → 拒绝。

### 4.3 G6 ADF 平稳性检验

**目标**：分布平稳性校验（ADF + 滚动矩漂移双通道）。

**契约**（`robustness.py` 新增）：

```python
def check_stationarity(
    factor_returns: np.ndarray,      # ★ 因子收益序列（非因子值，见算法注）
    adf_significance: float = 0.05,
    use_adf: bool = True,
) -> dict:
    """ADF p<0.05 或滚动矩漂移比<0.2 判通过 → {passed, adf_stat, adf_p, drift_ratio}"""
```

**算法**：statsmodels `adfuller` 可用时优先；不可用降级滚动矩漂移
（前/后半段均值差 / 全段 std，阈值 0.2）。
**关键约束**：检验对象是**因子收益序列**——因子值常含趋势（如动量累积）ADF 必然拒绝原假设，会误杀趋势因子，docstring 必须写死。

**接入点**：`high_ic_screener` 打分项追加；`audit.py` 清单追加。

**测试**：随机游走 → 不通过；白噪声 → 通过；趋势型因子值 → 提示需用收益序列。

### 4.4 G7 显式 5-Regime 拆分检验

**目标**：替代 WF 正占比近似，按 bull/bear/oscillate/high_vol/low_vol 显式拆分验证因子。

**契约**（`regime_validation.py` 新增，与既有 `validate_regime_predictive_power` 互补）：

```python
def validate_factor_across_regimes(
    factor_signal: pd.Series, forward_returns: pd.Series,
    regime_series: pd.Series,
    min_positive_regimes: int = 3,
    min_regime_samples: int = 20,
) -> dict:
    """各制度 IC/ICIR + 判定 {passed, regime_dependent, per_regime: {regime: {ic, icir, n}}}"""
```

**判定规则**：通过 = 覆盖 ≥3 个制度（样本≥min_regime_samples）且正向 ICIR 制度数 ≥3；
某制度 ICIR<-0.5 → 打 `regime_dependent` 标签入库（不否决，避免误杀区间型因子）。

**接入点**：`high_ic_screener._check_multi_regime`（L787-806）替换为真实 Regime 拆分，
Regime 序列由 `regime_hmm.MultiHorizonHMMDetector` / `regime._detect_by_rule` 逐日生成（复用，不新写）。

**测试**：bull 强 bear 负的动量因子 → 判定环境依赖非否决；全制度正向 → 通过；仅覆盖 1 制度 → 不通过。

---

## 5. 批次三 P2：基础设施与工程底座（G8–G17）

### 5.1 G8 统一交易日历 + 断K/跳空脏数据清洗

**契约**（`fts/data_sources/trading_calendar.py` 新建）：

```python
class TradingCalendar:
    def get_trading_days(self, start, end) -> list[pd.Timestamp]: ...
    def is_trading_day(self, day) -> bool: ...
    def align(self, series: pd.Series) -> pd.Series: ...  # 剔除休市日，停牌前向填充

# data_futures.py 面板构建处新增清洗：
# - 单品种缺失交易日占比 >5% 或连续缺失 >3 日 → 打 data_gap 标记，不进因子计算
# - 跳空清洗：|gap| > 5×ATR(20) 且无对应成交量 → 标异常（进 QC 报告，不删除）
```

**日历来源**：优先 TQ-Local trade_cal 接口；降级 = 82 品种日期并集中出现频率 ≥80% 的日期。
**跳空**：`overnight_gap.py` 注入列默认关闭改为默认开启标记。

**测试**：节假日序列剔除正确；断K品种打标记不进面板；10×ATR 跳空标异常。

### 5.2 G9 MAD 中位数去极值进 Standardizer

**契约**（`standardizer.py` 扩展）：

```python
StandardizeMethod = Literal[..., "mad_winsorize", "mad_then_zscore"]
StandardizerConfig.mad_k: float = 3.0
# mad_winsorize:  |x - med| > k*1.4826*MAD 截断到边界
# mad_then_zscore: 先 MAD 截断再 zscore
```

**算法**：`med = nanmedian; mad = nanmedian(|x-med|); bound = k*1.4826*mad; clip(x, med±bound)`；
1.4826 系数与 `ops_library.cs_mad_zscore` 对齐；`SUPPORTED_METHODS` 扩至 8 种，默认仍 zscore（向后兼容）。

**测试**：厚尾分布下 MAD 截断保留极端值少于 3σ 截断；与算子层输出一致性。

### 5.3 G10 波动率/季节性中性化

**契约**（`barra/barra_neutralizer.py` 扩展）：

```python
def barra_neutralize_matrix(
    signal_matrix, symbols_list, style_exposures,
    industry_map=None, cap_map=None, min_samples_factor=1.5,
    vol_map: Optional[dict[str, float]] = None,   # {symbol: 年化波动率} 截面列
    dates: Optional[pd.DatetimeIndex] = None,     # 时序去季节化所需日期索引
    include_vol_neutral: bool = True,             # 截面加波动率列
    include_season_neutral: bool = True,          # 时序月度去季节化
    ...
)
```

**实现要点（2026-08-13 设计修正）**：
- **波动率中性化（截面）**：`vol_map` 作为静态截面暴露（对标股票市值），逐日截面回归 X 中追加
  `[vol_map[sym]]` 列，剥离信号与品种波动率水平的相关性。
- **季节性中性化（时序）**：⚠️ 原设计"12 个月虚拟变量进截面 X"在主力连续合约下退化——同一交易日
  所有品种月份相同 → 哑变量为常数列被剔除、功能空转。修正为**逐品种时序回归**
  `signal_t ~ Σ 月哑变量(2..12) + 截距` 取残差（`dates.month` 推导月份），真正剥离日历季节性
  （如"一月效应"）。`dates=None` 或样本不足（<15）自动跳过，向后兼容。
- **接入点**：`evolution_futures.py` 板块注入处新增 `_build_vol_map()`（全样本日收益年化波动率，
  静态暴露），随 `cross_section_evaluate_backtest(vol_map=...)` 传入；与 `l2_barra_style_neutral`
  同一配置门控；`cross_section_evaluate_backtest` 增加 `vol_map` 参数并透传 `dates`。

**测试**：合成「波动率单调影响信号」面板 → 中性化后残差与波动率相关归零；强一月效应面板 →
去季节化后一月偏移消失；无 vol_map/dates → 行为不变（向后兼容）。

### 5.4 G11 换手 >20% 因子级硬剔除

**契约**：`evaluation_chain.py` 月度换手计算处（L416-420）新增日换手口径
`turnover_daily = 信号翻转率`，`>0.20` 硬淘汰。与周度 80% 打分共存。

**⚠️ 数据校准前置**（§9）：期货日频因子日换手偏高，>20% 可能误杀，先跑分布再定值。

**测试**：日换手 25% → 淘汰；15% → 通过。

**定值启用（2026-08-13，v2.104.0+1）**：`turnover_daily_max = 0.30`——经 `scripts/backfill_turnover.py` 对
factor_catalog_futures 83 个 active 因子以统一横截面面板（25 品种×200 日真实数据）重算校准：
P50=0.138 / P75=0.228 / **P90=0.320** / P95=0.456，≤0.30 通过率 88.0%（剔除 top ~12% 极端换手，
不误伤主流，优于框架参考 0.20——0.20 会误剔 33.7%）。**换手回填入库完成**（69 个因子
turnover_monthly/level_1_turnover 更新，修复横截面路径曾硬编码 turnover=0 的键缺失）；
**横截面晋升判定 `_evaluate_cross_section` 补 G11 硬剔除**（时序路径 L720-727 已有）。

### 5.5 G12 信号输出统一契约（8 字段）

**契约**（`signal_contract.py` 扩展，`total=False` 向后兼容）：

```python
class SignalDetail(TypedDict, total=False):
    symbol: str
    direction: str
    position: float          # 目标资金占比（现有）
    target_lots: int         # 新增：目标手数
    current_lots: int        # 新增：当前持仓手数
    delta_lots: int          # 新增：调仓手数 = target - current
    score: float             # 新增：触发因子得分（Composite Score）
    regime: str              # 新增：市场状态标签
    risk_usage: float        # 新增：风险占用比例 ∈ [0,1]
    confidence: float
    price: float
    stop_loss: float
    take_profit: float
    contributing_factors: list[FactorContribution]
```

**统一出口**：`tqsdk_mhf_executor.run_once`、`mhf_signal_pipeline.generate_mhf_signals`、
`futures_signal_pipeline` 三处全部生成 `FactorSignal` 并经 `SignalValidator`；
`risk_usage` 由 `capital_allocator` 的 margin_usage 回填；
新增 `to_lots(position, equity, price, multiplier)` 辅助：资金占比→手数，四舍五入、资金不足→0 手、超上限→截断。

**接线范围（2026-08-13 实施说明）**：
- `generate_mhf_signals` 已接入：`signal_map_to_factor_signal` 组装 FactorSignal →
  `SignalValidator` 校验，结果附入 payload `factor_signal` / `validation_errors`
  （legacy `signals` 字典保留，tqsdk 执行器兼容）。
- `tqsdk_mhf_executor.run_once` 已接入：payload 含 `factor_signal` 时先过校验（不阻断执行）。
- `futures_signal_pipeline` 为**报告型脚本**（输出多空排名 md），其 FactorSignal
  发射依赖报告 payload 契约化改造，暂缓（G17 FDT 消费入口以 MHF 链路先行）。

**测试**：`to_lots` 边界（0 手 / 截断）；转换器 + validator 零错误；新字段校验（risk_usage 越界 / delta 不一致）。

### 5.6 G13 月度重跑自动调度

**契约**（`scheduler/tasks.py` `register_default_tasks` 新增）：

```python
TaskSpec(
    name="monthly_factor_rescreen",
    schedule="0 2 1 * *",          # 每月 1 日 02:00
    func=monthly_factor_rescreen,  # 新增：high_ic_screener 全库重筛 + elite_tracker.update(is_monthly=True)
    trace=True,
)
```

**产出**：`reports/monthly/factor_rescreen_{date}.md`；支持 `--dry-run` 手动触发。

**测试**：`tests/scheduler/test_tasks.py`——注册存在、dry-run 跑通、产物生成。

### 5.7 G14 杠杆/止损止盈参数随 Regime 变化

**契约**（`regime_multipliers.py` 扩展第二张表）：

```python
REGIME_RISK_PARAMS = {
    "bull":     {"leverage_cap": 2.5, "stop_loss_pct": 0.015, "daily_loss_pct": 0.020},
    "bear":     {"leverage_cap": 1.5, "stop_loss_pct": 0.010, "daily_loss_pct": 0.015},
    "oscillate":{"leverage_cap": 2.0, "stop_loss_pct": 0.012, "daily_loss_pct": 0.018},
    "high_vol": {"leverage_cap": 1.0, "stop_loss_pct": 0.008, "daily_loss_pct": 0.010},
    "low_vol":  {"leverage_cap": 2.0, "stop_loss_pct": 0.015, "daily_loss_pct": 0.020},
}
def resolve_risk_params(regime: str, base: RiskParams) -> RiskParams: ...
```

**接入点**：`risk/risk_manager.py` 初始化时由当前 Regime 注入参数（不改 `check()` 内部逻辑）；
`paper_trader_mhf.py` 止损参数参数化；参数变化按 `adaptive_weight.RegimeSmoother` 日插值平滑防跳变。

**测试**：high_vol 下杠杆 1.0 生效；切换平滑无跳变；无 regime 输入回退常量。

### 5.8 G15 方差最小化仓位模式

**契约**（`capital_allocator.py` 扩展）：

```python
def _min_variance_allocation(returns: pd.DataFrame, **kw) -> pd.Series:
    """w = Σ⁻¹1 / (1'Σ⁻¹1)；Ledoit-Wolf 收缩防奇异（复用 weight_learning）"""
```

`mode` 枚举扩为 5 种（fixed/vol_target/risk_parity/kelly/min_variance）。

**测试**：对角协方差 → 权重反比方差；奇异矩阵 → 收缩后可解。

### 5.9 G16 LLM 审核（辅助标注，默认暂缓）

**契约**：LLM 只做辅助标注，不做裁决——抽取 `{code, family, ic, icir, robustness_meta}` → LLM 输出
`{logic_verdict, reason, suggested_family}` → 仅写入 `factor_reviews.review_note`，不改变
approved/rejected 状态（AP06 反模式约束：AI 输出永不自动改变入库状态）。

**建议**：不阻塞主线，批次三末尾视团队需求决定是否启用。

**批次末评估结论（2026-08-13，v2.103.0 批次）**：**维持暂缓（不启用）**，理由：
1. **无增量拦截能力**：P1 批次 G4-G7 已落地统计硬门槛（ICIR≥0.30/符号反转一票否决/Bootstrap CI/ADF/5-Regime 拆分），
   审计链 7 项 + SHAP 信息型审查已覆盖，LLM 辅助标注对 approved/rejected 无影响（契约只写 review_note）；
2. **AP06 约束**：AI 输出永不自动改变入库状态，G16 收益仅为标注信息，成本（LLM API/延迟/维护）> 收益；
3. **角色边界**（AGENTS.md §5.6）：FTS 专注因子发现/评估/组合与演化，LLM 审核标注属可选增强，
   团队需要时可按本节契约以独立模块接入，不占主线预算；
4. **G17 交接前置**：真实柜台（FDT）落地后，若信号质量需人工/LLM 复核标注再评估启用。

**启用条件（后续触发）**：团队决定需要 → 新建 `fts/factor_engine/llm_review.py`（按本节契约，仅写
`factor_reviews.review_note`），配置 `FTS_LLM_REVIEW_ENABLED` 默认关闭。

### 5.10 G17 真实柜台对接（FDT 交接，非本仓库编码）

**边界**：按角色边界（AGENTS.md），真实柜台由下游 FDT 实现 `AbstractGateway` 子类（如 QmtGateway/xtquant）。
FTS 交付物 = G12 统一信号契约（FDT 消费入口）+ 既有 `Order`/`OrderLifecycle` 契约 +
`SimulatedGateway` 作为 FDT 实现的对拍基准。

---

## 6. 实施批次与验证

```
批次一 P0（G1-G3）
  → 验证：pytest tests/factor_engine/test_portfolio_risk_controls.py tests/factor_engine/test_portfolio_loop*.py -q
批次二 P1（G4-G7）
  → 验证：pytest tests/factor_engine/test_evaluation_chain.py tests/factor_engine/test_walk_forward.py \
          tests/factor_engine/test_robustness.py tests/factor_engine/test_regime_validation.py -q
批次三 P2（G8-G16）
  → 验证：pytest tests/{data_sources,factor_engine,scheduler,scripts}/ -m "not slow" -q -o addopts="" -p no:cacheprovider
每批完成 → python scripts/bump_version.py --build --message "..."
全部完成 → 里程碑 bump + 全量回归（pytest tests/ -m "not slow"）
```

**每批次内固定步骤**：
1. 契约/配置先落 01-architecture.md 与 03-configuration.md；
2. 写测试（TDD）→ 实现 → 跑模块测试全绿；
3. 更新 06-testing.md（用例数）、07-operations.md（版本历史）、08-gap-analysis.md（登记/关闭）；
4. bump build 版本。

---

## 7. 决策点与默认假设（已按建议方案定，实施中可变更）

| # | 决策点 | 默认方案 |
|---|---|---|
| D1 | G16 LLM 审核 | 暂缓，批次三末尾评估 |
| D2 | G17 真实柜台 | 仅契约对齐，不写 FDT 代码 |
| D3 | 实施节奏 | 按 P0→P1→P2 分批交付，每批验收后进下一批 |
| D4 | 硬阈值（G4 ICIR/G11 换手） | 先跑因子库分布统计，以数据校准为准，禁止直接套外部硬值 |
| D5 | 配置默认开关 | G3 turnover_penalty 默认开启（0.15）；G1 默认开启；G8 跳空标记默认开启 |

---

## 8. 文档同步清单（HARNESS 13 项映射）

| 检查项 | 对应文档 | 本方案落点 |
|---|---|---|
| 1 数据流/架构变更 | 01-architecture.md | G8 日历层、G12 信号契约、G10 中性化注入 |
| 2 阶段/文件名/产出物 | 02-lifecycle.md | 月度调度产物 reports/monthly/ |
| 3 新配置项 | 03-configuration.md | 全部 G 的 config 新增 |
| 4 降级/熔断/超时 | 04-resilience.md | G5 块抽样、G6 statsmodels 降级、G8 日历降级链 |
| 5 新指标/日志 | 05-observability.md | G1-G3 告警字段、G4-G7 检验指标 |
| 6 测试文件/用例数 | 06-testing.md | 每批更新 |
| 7 版本号/历史 | 07-operations.md | 每批 bump |
| 8 差距登记/关闭 | 08-gap-analysis.md | G1-G17 登记与关闭 |
| 9 晋级里程碑 | 09-advancement-plan.md | 三批次里程碑 |
| 10 流程文档 | business_flow.md / execution_modes_flowchart.md | 信号产出流程更新 |
| 11 角色 MD | agents/fts-agent.md | G17 边界说明 |
| 12/13 README | README.md | 版本/测试数/快速参考 |

---

## 9. 阈值校准前置步骤（首批实施前必须执行）

1. 写统计脚本 `scripts/gap_threshold_calibration.py`（一次性，可留仓库）：
   - 对现有因子库（factor_catalog_futures.duckdb）重算 IC / ICIR / 日换手 / 前后半段符号分布；
   - 输出分位数表（P25/P50/P75/P90/P95）供定值；
2. 依据分布定 G4 `icir_min` 与 G11 `turnover_daily_max` 的实际值；
3. 校准结果回填本文档 §4.1 / §5.4 并记录到 08-gap-analysis.md。

### 9.1 校准执行结果（2026-08-13，v2.103.0+9）

**脚本**：`scripts/gap_threshold_calibration.py`（已实现并运行，报告落盘 `reports/gap/threshold_calibration_20260813.md`）

| 指标 | P25 | P50 | P75 | P90 | P95 | 结论 |
|---|---|---|---|---|---|---|
| \|ICIR\|（factor_catalog 全量） | 0.217 | 0.984 | 1.361 | 2.050 | 4.001 | **0.30 门槛淘汰底部 ~29%**，与框架标准吻合 |
| \|ICIR\|（factor_evaluations 历史） | 0.240 | 0.984 | 1.378 | 2.006 | 3.662 | 同口径验证一致 |
| 日换手（turnover_monthly/21） | 0 | 0 | 0 | 0 | 0 | **库中 turnover 字段全为 0，无法校准** |
| ICIR ≥ 0.30 通过率 | — | — | — | — | — | 70.9%（全量） |
| 日换手 ≤ 0.20 通过率 | — | — | — | — | — | 98.2%（无效值虚高，不采信） |

**定值决策**：
- **G4 `icir_min = 0.30`**：数据支持（P25≈0.22，0.30 恰好淘汰底部 1/4 左右，与框架淘汰线一致）；
- **G11 `turnover_daily_max`**：**暂缓定值**——factor_catalog 无实际换手数据，需在 evaluation_chain 落地日换手计算后，以首批真实分布复核（初始沿用框架 0.20 观察）。

**日换手缺失根因**：`turnover_monthly` 字段在因子库中未回填（评估链 L416-420 计算但未入库），G11 实施时将一并修复「换手回填入库」。

### 9.2 全库校准复核（2026-08-14，v2.104.0+16，GAP-117）

> **口径修正**：日换手反推由 `turnover_monthly/21` 改为 `/42`——库内月度换手 = 日换手 × 42（G11 信号翻转率口径 `turnover_daily = mean(|Δsign|)/2`、`turnover_monthly = mean(|Δsign|)×21`，evaluation_chain 时序/横截面两路径 + `scripts/backfill_turnover.py` 一致）。§9.1（2026-08-13）快照时库内 turnover 全为 0 无法校准，口径误差未暴露；本次库内已有真实换手（337 因子中 79 非零），`/21` 会将日换手高估 2 倍，须以 `/42` 为准（GAP-117 登记关闭）。

**脚本**：`scripts/gap_threshold_calibration.py`（重跑，报告落盘 `reports/gap/threshold_calibration_20260814.md`）

| 指标 | P25 | P50 | P75 | P90 | P95 | 结论 |
|---|---|---|---|---|---|---|
| \|ICIR\|（factor_catalog 全量 n=337） | 0.240 | 0.973 | 1.342 | 2.047 | 3.961 | G4 0.30 门槛淘汰底部 ~28.5%（通过率 71.5%） |
| \|ICIR\|（factor_evaluations 历史） | 0.240 | 0.974 | 1.367 | 1.953 | 3.591 | 同口径验证一致 |
| 日换手（turnover_monthly/42，全量 n=337） | 0 | 0 | 0 | 0.162 | 0.246 | 76.5%（258/337）因子无换手记录；有换手因子集中在 ~0.16~0.25 |
| 日换手（factor_evaluations 历史） | 0 | 0 | 0.048 | 0.211 | 0.251 | 评估历史口径一致 |
| ICIR ≥ 0.30 通过率 | — | — | — | — | — | 71.5%（全量） |
| 日换手 ≤ 0.20 通过率 | — | — | — | — | — | 91.1%（全量） |

**复核结论**：
- **G4 `icir_min = 0.30` 维持**：全库 \|ICIR\| P25=0.240，0.30 恰好淘汰底部 ~28.5%，与框架淘汰线一致（与 §9.1 定值一致）；
- **G11 `turnover_daily_max = 0.30` 维持**：修正口径后全库日换手 P90=0.162（有换手因子量级 ~0.16~0.25），≤0.30 通过率 91.1%，与 §5.4 定值基准（active 83 因子 P90=0.320）量级一致；2026-08-14 新增 elite 3 因子 turnover_daily 0.16~0.28 均 ≤0.30 通过，与晋升评估一致。

---

## 10. 一致性元数据

| 代码对象 | 文档引用 | 可验证断言 | 检验方式 |
|---|---|---|---|
| portfolio_risk_controls.check_aligned_exposure | §3.1 | 同向占比≥0.6 时 compress_scale∈[0.5,1) | pytest test_portfolio_risk_controls.py |
| portfolio_risk_controls.throttle_exit_stampede | §3.2 | 单日平仓数≤max_same_day_exits | pytest test_portfolio_risk_controls.py |
| portfolio_loop.allocate_turnover_budget | §3.3 | 组合换手≤daily_turnover_cap | pytest test_portfolio_loop.py |
| evaluation_chain ICIR 硬门槛 | §4.1 | ICIR<0.30 因子拒收 | pytest test_evaluation_chain.py |
| walk_forward min_oos_icir | §4.1 | WFA passed 含 ICIR≥0.25 | pytest test_walk_forward.py |
| robustness.bootstrap_ic_ci | §4.2 | 块抽样 CI 可复现（seed=42） | pytest test_robustness.py |
| robustness.check_stationarity | §4.3 | 白噪声 passed / 随机游走拒绝 | pytest test_robustness.py |
| regime_validation.validate_factor_across_regimes | §4.4 | 5 制度 ICIR 拆分判定 | pytest test_regime_validation.py |
| data_sources/trading_calendar.py | §5.1 | 日历对齐剔除休市日 | pytest tests/data_sources/ |
| standardizer mad_winsorize | §5.2 | 厚尾截断边界正确 | pytest test_standardizer.py |
| barra_neutralize_matrix vol/season | §5.3 | 中性化后 IC-vol 相关归零 | pytest test_barra 相关 |
| signal_contract.SignalDetail 扩展字段 | §5.5 | 三管道 validator 零错误 | pytest test_signal_contract.py |
| scheduler monthly_factor_rescreen | §5.6 | dry-run 产物生成 | pytest tests/scheduler/test_tasks.py |
| regime_multipliers.REGIME_RISK_PARAMS | §5.7 | high_vol 杠杆=1.0 | pytest test_regime_multipliers.py |
| capital_allocator min_variance | §5.8 | 对角协方差反比方差 | pytest test_capital_allocator_margin.py |
| scripts/gap_threshold_calibration.py | §9 | 输出 ICIR/换手分位数表 | 运行脚本校验输出 |
