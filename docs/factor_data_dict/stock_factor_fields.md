# FTS 股票专用因子消费数据字段字典

> 版本: v1.0.0
> 适用项目: FTS (Factor Transformation System)
> 维护: FTS Team
> 状态: 已与 Data-Core 字段对齐
> 存放路径: factor_system/docs/factor_data_dict/stock_factor_fields.md

本文档聚焦于 FTS 在 `--universe single` / `--universe csi300` 模式下，因子计算所消费的 Data-Core 字段。股票专用因子（value / quality / size）与全市场通用因子的区别在于：其 `signature.input_fields` 倾向于使用 `close` + `volume` 构造价量代理，而非依赖期货/宏观/情绪等专属字段。

---

## 0. 目录

1. [种子池股票因子总览](#1-种子池股票因子总览)
2. [OHLCV 基础字段（股票使用部分）](#2-ohlcv-基础字段股票使用部分)
3. [股票专用字段（Data-Core 加工）](#3-股票专用字段data-core-加工)
4. [横截面评估的数据要求](#4-横截面评估的数据要求)
5. [每个股票种子的字段消费清单](#5-每个股票种子的字段消费清单)
6. [字段缺失时的降级策略](#6-字段缺失时的降级策略)
7. [股票 vs 期货数据消费对比](#7-股票-vs-期货数据消费对比)
8. [版本与变更](#8-版本与变更)

---

## 1. 种子池股票因子总览

FTS `seed_pool.py` 中股票相关种子因子（含全市场通用 + A 股专用）：

| # | 因子名 | 类型 | 必需字段 | 备注 |
|---|--------|------|----------|------|
| 1 | `momentum` | 全市场 | `close` | 价格动量 |
| 2 | `volatility_reversion` | 全市场 | `close` | 波动率回归 |
| 3 | `volume_flow` | 全市场 | `close`, `volume` | 量价资金流 |
| 8 | `macro_regime` | 全市场 | `macro_signal`（首选） / `close`（降级） | 宏观制度 |
| 9 | `rate_proxy` | 全市场 | `rate_mom`（首选） / `close`（降级） | 利率代理 |
| 10 | `pmi_proxy` | 全市场 | `pmi`, `pmi_mom`（首选） / `close`（降级） | PMI 代理 |
| 13 | `value_factor` | A 股 | `close`, `volume` | 价值因子（低价+放量） |
| 14 | `quality_factor` | A 股 | `close` | 质量因子（低波+稳升） |
| 15 | `size_factor` | A 股 | `close`, `volume` | 市值因子（量价代理） |

> 期货专用因子（oi_change / basis / inventory_pct / capacity / position_rank / warrant_change）见 `futures_factor_fields.md`。

---

## 2. OHLCV 基础字段（股票使用部分）

股票 K 线数据来自 `DataPayload(data_type=DataType.OHLCV, market=MarketType.STOCK)`，解包后为 `KlineData`（含 `bars: list[KBar]`）。

| 字段 | 来源 | FTS 因子消费 | 必备 |
|------|------|-------------|------|
| `close` | `KBar.close` | 全部 9 个股票相关因子 | 是 |
| `high` | `KBar.high` | （备用） | 否 |
| `low` | `KBar.low` | （备用） | 否 |
| `open` | `KBar.open` | （备用） | 否 |
| `volume` | `KBar.volume` | volume_flow / value_factor / size_factor | 否（有则更优） |
| `amount` | `KBar.amount` | （可替代 volume） | 否 |
| `open_interest` | `KBar.open_interest` | — （股票为 0） | 否 |
| `settlement` | `KBar.settlement` | — （股票为 0） | 否 |

**股票 vs 期货字段差异**：
- 股票的 `open_interest` 始终为 0（Data-Core 默认）
- 股票的 `settlement` 始终为 0
- 股票的 `volume` 单位是**股**（期货是**手**）

---

## 3. 股票专用字段（Data-Core 加工）

股票因子在 A 股场景下可消费 Data-Core 的额外加工字段（金融工程领域常见的"派生 alpha 因子"）。当前 FTS 种子池暂未直接消费这些字段，但**演化层**会自动注入 LLM 生成的因子程序，可能消费以下字段。

| 字段 | Data-Core 来源 | 注入方式 | 典型用途 |
|------|----------------|----------|----------|
| `pe_ttm` | `datacore/equity/financial.py` | 注入 K 线 panel 列 | 估值因子 |
| `pb` | `datacore/equity/financial.py` | 注入 K 线 panel 列 | 估值因子 |
| `ps_ttm` | `datacore/equity/financial.py` | 注入 K 线 panel 列 | 估值因子 |
| `roe` | `EarningSummary.roe` | 注入 K 线 panel 列 | 质量因子 |
| `revenue_yoy` | `EarningSummary.revenue_yoy` | 注入 K 线 panel 列 | 成长因子 |
| `profit_yoy` | `EarningSummary.profit_yoy` | 注入 K 线 panel 列 | 成长因子 |
| `total_market_cap` | `datacore/equity/equity_provider.py` | 注入 K 线 panel 列 | 市值因子 |
| `free_market_cap` | `datacore/equity/equity_provider.py` | 注入 K 线 panel 列 | 流通市值因子 |
| `turnover_rate` | `datacore/equity/equity_provider.py` | 注入 K 线 panel 列 | 换手率因子 |
| `composite_score` | `FundamentalSummary.composite_score` | 注入 K 线 panel 列 | 基本面综合 |
| `report_direction` | `ReportSummary.direction` | 注入 K 线 panel 列 | 研报情绪 |
| `sentiment_score` | `SentimentData.overall_score` | 注入 K 线 panel 列 | 市场情绪 |

> **当前状态**：FTS 种子池中 A 股三因子（value / quality / size）**不依赖**上述字段，仅用 `close` + `volume` 构造近似。这是为了保证在 Data-Core 加工字段未就绪时仍可运行。
> 后续 L1 知识注入 + L2 演化的因子程序可消费上述字段。

### 3.1 字段注入时序

```text
Data-Core 加工字段 → K 线 panel 多加列 → FTS 因子程序消费
        ↑                      ↑                    ↑
   加工层 ETL            data['field']       因子代码读取
```

---

## 4. 横截面评估的数据要求

### 4.1 单只股票模式（`--universe single`）

- 数据需求：单只股票的 K 线（≥60 根）
- 评估方式：时间序列 IC（不推荐使用，会触发 IC=0 熔断）
- **生产环境禁用**：HARNESS 规范要求使用横截面模式

### 4.2 CSI 300 成分股模式（`--universe csi300`）

- 数据需求：CSI 300 成分股列表（Data-Core 动态获取）+ 每只股票 ≥120 根 K 线
- 评估方式：横截面 Spearman IC
- 横截面最小品种数：3（已从原 10 调低）
- 截面日期对齐：所有股票必须有当日数据，否则该截面跳过

**FTS 横截面数据加载代码**（`fts/data.py:get_csi300_panel`）：

```python
def get_csi300_panel(start_date, end_date, max_stocks=300):
    """获取 CSI 300 横截面 panel。"""
    symbols = list_csi300_symbols()[:max_stocks]
    panels = {}
    for symbol in symbols:
        payload = data_core.get_ohlcv(symbol, start_date, end_date)
        if payload.available:
            panels[symbol] = _payload_to_ohlcv_df(payload)
    return panels
```

### 4.3 评估流程

```text
每只股票加载 K 线面板
  ↓
按日期对齐截面（merge on date）
  ↓
截面内计算每只股票的因子值
  ↓
横截面 Spearman IC（因子值 vs 次日收益）
  ↓
IC 时序聚合：mean / std / t-stat
  ↓
通过 IC>0.03 + Sharpe>1.5 进入 L2 评估
```

---

## 5. 每个股票种子的字段消费清单

### 5.1 momentum（动量）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格变化率 + MA 斜率 |

**核心代码**：

```python
chg = (close - roll(close, 20)) / max(roll(close, 20), 1e-10)  # 20 日动量
ma_slope = (ma - shift(ma, 1)) / max(shift(ma, 1), 1e-10)
score = 0.5 * tanh(chg / 0.05) + 0.3 * tanh(ma_slope * 30) + 0.2 * tanh(chg / 0.1)
```

### 5.2 volatility_reversion（波动率回归）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 布林带位置 |

**核心代码**：

```python
ma = convolve(close, ones(20)/20)  # 20 日 MA
std = rolling_std(close, 20)
upper = ma + 2*std
lower = ma - 2*std
bb_pos = (close - lower) / max(upper - lower, 1e-10)
# 高位（bb_pos → 1）偏空，低位（bb_pos → 0）偏多
score = (0.5 - bb_pos) * 1.0
```

### 5.3 volume_flow（资金流）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格变化 |
| `volume` | KBar | ✅ | 量比 |

**核心代码**：

```python
avg_vol = convolve(volume, ones(10)/10)  # 10 日均量
vol_ratio = volume / max(avg_vol, 1e-10)
chg = (close - shift(close, 1)) / max(shift(close, 1), 1e-10)
# 放量+涨 → 偏多；放量+跌 → 偏空
score = where(vol_ratio > 1.3, tanh(chg/0.02)*0.5,
       where(vol_ratio < 0.7, tanh(chg/0.05)*0.3, 0))
```

### 5.4 macro_regime（宏观制度）

| 路径 | 字段 | 来源 | 必选 |
|------|------|------|------|
| 首选 | `macro_signal` | MarketStateData.regime | ✅ |
| 降级 | `close` | KBar | ⚠️ |

**首选代码**：

```python
# macro_signal ∈ {bull, bear, sideways}
score = where(macro_signal == 'bull', +0.5,
       where(macro_signal == 'bear', -0.5, 0))
```

**降级代码**：用 60 日价格趋势反推。

### 5.5 rate_proxy（利率代理）

| 路径 | 字段 | 来源 | 必选 |
|------|------|------|------|
| 首选 | `rate_mom` | MacroIndicator(LPR1Y).mom | ✅ |
| 降级 | `close` | KBar | ⚠️ |

**首选代码**：

```python
# 利率上升 → 估值压制 → 偏空
score = -tanh(rate_mom / 0.25)
```

### 5.6 pmi_proxy（PMI 代理）

| 路径 | 字段 | 来源 | 必选 |
|------|------|------|------|
| 首选 | `pmi`, `pmi_mom` | MacroIndicator(PMI) | ✅ |
| 降级 | `close` | KBar | ⚠️ |

**首选代码**：

```python
level = tanh((pmi - 50.0) / 5.0)  # 50 为荣枯线
mom = tanh(pmi_mom / 1.0) * 0.5
score = level * 0.6 + mom * 0.4
```

### 5.7 value_factor（价值因子，A 股专用）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格分位 |
| `volume` | KBar | ✅ | 量比 |

**核心代码**：

```python
# 价格分位（0=最低价，1=最高价）
pct_rank = argsort(argsort(close)) / max(n-1, 1)
avg_vol = convolve(volume, ones(20)/20)
vol_ratio = volume / max(avg_vol, 1e-10)
# 低价+放量 → 价值凸显
score = (1 - pct_rank) * tanh(vol_ratio * 0.5) - 0.3
```

**注意**：当前为代理实现，**未消费真实估值字段**（`pe_ttm` / `pb`）。生产环境应升级为消费 Data-Core 加工后的估值字段。

### 5.8 quality_factor（质量因子，A 股专用）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格稳定性 + 趋势 |

**核心代码**：

```python
returns = (close - shift(close, 1)) / max(shift(close, 1), 1e-10)
rolling_vol = rolling_std(returns, 20)  # 20 日波动率
ma = convolve(close, ones(20)/20)
ma_slope = (ma - shift(ma, 1)) / max(shift(ma, 1), 1e-10)
# 低波动+正斜率 = 高质量
quality_score = tanh(-rolling_vol * 20 + 0.5) + tanh(ma_slope * 30)
```

**注意**：当前为代理实现，**未消费真实盈利字段**（`roe` / `profit_yoy`）。

### 5.9 size_factor（市值因子，A 股专用）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格水平 |
| `volume` | KBar | ✅ | 成交量偏离 |

**核心代码**：

```python
avg_vol = convolve(volume, ones(20)/20)
vol_deviation = volume / max(avg_vol, 1e-10)  # 量比
price_level = close / max(mean(close[:20]), 1e-10)  # 价格相对水平
# 低量+低价 = 偏小盘
size_proxy = tanh(1.0 / (price_level + 0.1)) * tanh(1.0 / (vol_deviation + 0.1))
# 小盘溢价 → 做多小盘
score = size_proxy * 0.5
```

**注意**：当前为代理实现，**未消费真实市值字段**（`total_market_cap` / `free_market_cap`）。

---

## 6. 字段缺失时的降级策略

### 6.1 通用降级模式

与期货因子相同：A 股因子全部实现"首选 → 降级"双路径，但 A 股因子的降级路径几乎都退化为纯 `close` 计算。

### 6.2 估值/质量/市值字段缺失的影响

当 `pe_ttm` / `roe` / `total_market_cap` 等加工字段未注入时：

| 因子 | 实际行为 |
|------|----------|
| `value_factor` | 用价格分位 + 量比近似（精度低） |
| `quality_factor` | 用价格波动率 + 趋势近似（精度低） |
| `size_factor` | 用价格 + 成交量近似（精度低） |

**改进建议**：
1. 在 `fts/data.py` 的 `get_csi300_panel` 中注入 `pe_ttm` / `pb` / `roe` / `total_market_cap` 等列
2. 修改 `value_factor` / `quality_factor` / `size_factor` 优先消费真实字段

### 6.3 字段注入位置

`fts/data.py` 中 `_payload_to_ohlcv_df` 之后，调用 `enrich_with_fundamental(df, payload)`：

```python
def get_csi300_panel(start_date, end_date, max_stocks=300):
    panels = {}
    for symbol in symbols:
        payload = data_core.get_ohlcv(symbol, ...)
        if not payload.available:
            continue
        df = _payload_to_ohlcv_df(payload)
        df = enrich_with_fundamental(df, data_core.get_fundamental(symbol))
        panels[symbol] = df
    return panels
```

---

## 7. 股票 vs 期货数据消费对比

| 维度 | 股票（A 股） | 期货 |
|------|-------------|------|
| **OHLCV 必需字段** | `close` | `close` |
| **常用字段** | `close` + `volume` | `close` + `open_interest` + `volume` |
| **专用字段来源** | 加工层（PE/PB/ROE/市值） | 期货特异类型（基差/库存/仓单） |
| **横截面最小品种数** | 3（CSI 300 默认 300） | 3（期货默认 56 品种） |
| **数据频率** | 日频（部分 ETF 分钟级） | 日频（部分品种提供分钟） |
| **复权方式** | 前复权 / 后复权 | 连续合约 / 主力合约 |
| **截面日期对齐** | 严格（停牌股票需跳过） | 较松（节假日合约不同） |
| **评估单位** | 单只股票次日收益 | 单品种主力合约次日收益 |

---

## 8. 版本与变更

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.0 | 2026-07-21 | 初版：与 Data-Core v1.0.0 数据字典对齐 |

维护：当 Data-Core 字段新增/废弃/重命名时，必须同步更新本文档。
