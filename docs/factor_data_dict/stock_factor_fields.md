# FTS 股票专用因子消费数据字段字典

> 版本: v1.2.0
> 适用项目: FTS (Factor Intelligence System)
> 维护: FTS Team
> 状态: 已与 MCP/akshare 数据源对齐
> 存放路径: factor_system/docs/factor_data_dict/stock_factor_fields.md

本文档聚焦于 FTS 在 `--universe single` / `--universe csi300` 模式下，因子计算所消费的 MCP/akshare 数据字段。股票专用因子（value / quality / size）与全市场通用因子的区别在于：其 `signature.input_fields` 倾向于使用 `close` + `volume` 构造价量代理，而非依赖专属字段。

FTS 数据源已从 Data-Core 迁移至 MCP/akshare（腾讯自选股 / 东方财富 API），数据消费层统一由 `FTSDataProvider` 封装。

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

FTS 种子池共 **268 个种子因子**（9 内置 + 101 世坤 + 158 Qlib），通过 `seed_pool.load_all_seeds(include_external=True)` 加载。其中与股票（A 股/ETF）相关的因子分为以下三类：

### 1.1 内置种子因子（9 个）

| # | 因子名 | 必需字段 | 备注 |
|---|--------|----------|------|
| 1 | `momentum` | `close` | 价格动量 |
| 2 | `volatility_reversion` | `close` | 波动率回归 |
| 3 | `volume_flow` | `close`, `volume` | 量价资金流 |
| 4 | `pmi_proxy` | `close` | PMI 代理（价量近似） |
| 5 | `low_volatility` | `close` | 低波因子 |
| 6 | `value_factor` | `close`, `volume` | 价值因子（低价+放量） |
| 7 | `quality_factor` | `close` | 质量因子（低波+稳升） |
| 8 | `size_factor` | `close`, `volume` | 市值因子（量价代理） |
| 9 | `macro_regime` | `close` | 宏观制度（价量近似） |

### 1.2 外部种子因子（259 个）

外部种子因子通过 `seed_data/` 目录统一管理，以标准化因子表达式形式定义，运行时由 `loader.py` 动态转换为 `FactorProgram` 对象：

| 来源 | 文件 | 数量 | 典型字段消费 |
|------|------|:----:|-------------|
| 世坤 Alpha | `seed_data/wq101.py` | 101 | `close`, `volume`, `high`, `low`, `open`, `amount` |
| Qlib 因子 | `seed_data/qlib158.py` | 158 | `close`, `volume`, `high`, `low`, `vwap` |

所有外部因子表达式均使用 `alpha_ops.py` 公共操作库（`ts_mean`, `ts_std`, `ts_rank`, `correlation`, `covariance` 等 60+ 操作），编译后通过安全沙箱执行。

> 期货专用因子（oi_change / basis / inventory_pct / capacity / position_rank / warrant_change）已在 v1.2.0 中移除，FTS 当前仅聚焦 A 股/ETF 因子演化。

---

## 2. OHLCV 基础字段（股票使用部分）

股票 K 线数据来自 `FTSDataProvider.get_ohlcv()` / `MCPDataProvider.get_ohlcv()`，返回 pandas DataFrame，包含以下列：

| 字段 | MCP/akshare 来源 | FTS 因子消费 | 必备 |
|------|-----------------|-------------|------|
| `close` | `akshare` 东方财富/腾讯 API | 全部 268 个种子因子 | 是 |
| `high` | `akshare` 东方财富/腾讯 API | 外部因子（wq101/qlib158） | 否 |
| `low` | `akshare` 东方财富/腾讯 API | 外部因子（wq101/qlib158） | 否 |
| `open` | `akshare` 东方财富/腾讯 API | 外部因子（wq101/qlib158） | 否 |
| `volume` | `akshare` 东方财富/腾讯 API | volume_flow / value_factor / size_factor + 外部因子 | 否（有则更优） |
| `amount` | `akshare` 东方财富/腾讯 API | 可替代 volume | 否 |
| `high_limit` | `akshare` 东方财富/腾讯 API | — | 否 |
| `low_limit` | `akshare` 东方财富/腾讯 API | — | 否 |
| `pre_close` | `akshare` 东方财富/腾讯 API | — | 否 |

**股票数据结构**：FTS 不再依赖 Data-Core 的 `KBar` / `KlineData` / `DataPayload` 等封装类型，直接消费 `akshare` 返回的 pandas DataFrame，列名统一为 `date`, `open`, `high`, `low`, `close`, `volume`, `amount`。

**股票 vs 期货字段差异**：FTS v1.2.0 已移除期货因子支持，仅保留 A 股/ETF 数据源。

---

## 3. 股票专用字段（MCP/akshare 扩展）

股票因子在 A 股场景下可消费 MCP 扩展字段。当前 FTS 内置种子因子**不依赖**这些字段，仅用 `close` + `volume` 构造近似。但 **外部因子（wq101/qlib158）** 和 **L2 演化层** 自动生成的因子程序可能消费以下字段。

| 字段 | MCP/akshare 来源 | 注入方式 | 典型用途 |
|------|-----------------|----------|----------|
| `pe_ttm` | `akshare.stock_financial_abstract` | 扩展 K 线 DataFrame 列 | 估值因子 |
| `pb` | `akshare.stock_financial_abstract` | 扩展 K 线 DataFrame 列 | 估值因子 |
| `ps_ttm` | `akshare.stock_financial_abstract` | 扩展 K 线 DataFrame 列 | 估值因子 |
| `total_market_cap` | `akshare.stock_individual_info` | 扩展 K 线 DataFrame 列 | 市值因子 |
| `turnover_rate` | `akshare.stock_individual_info` | 扩展 K 线 DataFrame 列 | 换手率因子 |
| `free_market_cap` | 由 `total_market_cap` 推算 | 扩展 K 线 DataFrame 列 | 流通市值因子 |

> **当前状态**：FTS 种子池中 A 股三因子（value / quality / size）**不依赖**上述字段，仅用 `close` + `volume` 构造近似。这是为了保证在 MCP 扩展字段未就绪时仍可运行。
> 后续 L1 知识注入 + L2 演化的因子程序可消费上述字段。

### 3.1 字段注入时序

```text
MCP/akshare 基础 K 线 → pandas DataFrame → 扩展字段注入 → 因子程序消费
         ↑                          ↑                     ↑
    akshare API              data['pe_ttm'] = ...    因子代码读取
```

---

## 4. 横截面评估的数据要求

### 4.1 单只股票模式（`--universe single`）

- 数据需求：单只股票的 K 线（≥60 根）
- 评估方式：时间序列 IC（不推荐使用，会触发 IC=0 熔断）
- **生产环境禁用**：HARNESS 规范要求使用横截面模式

### 4.2 CSI 300 成分股模式（`--universe csi300`）

- 数据需求：CSI 300 成分股列表（`MCPDataProvider.list_csi300()`）+ 每只股票 ≥120 根 K 线
- 评估方式：横截面 Spearman IC
- 横截面最小品种数：3（已从原 10 调低）
- 截面日期对齐：所有股票必须有当日数据，否则该截面跳过

**FTS 横截面数据加载代码**（`fts/data.py` 和 `fts/data_mcp.py`）：

```python
# FTSDataProvider.get_csi300_panel() — 实际实现
def get_csi300_panel(self, days: int = 250, max_stocks: int = 300) -> dict[str, pd.DataFrame]:
    """获取沪深 300 成分股面板数据。"""
    symbols = self._mcp.list_csi300()[:max_stocks]
    panels: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            df = self._mcp.get_ohlcv(symbol, days=days)
            if df is not None and len(df) >= 60:
                panels[symbol] = df
        except Exception:
            continue
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
| `close` | akshare DataFrame | ✅ | 价格变化率 + MA 斜率 |

**核心代码**：

```python
chg = (close - roll(close, 20)) / max(roll(close, 20), 1e-10)  # 20 日动量
ma_slope = (ma - shift(ma, 1)) / max(shift(ma, 1), 1e-10)
score = 0.5 * tanh(chg / 0.05) + 0.3 * tanh(ma_slope * 30) + 0.2 * tanh(chg / 0.1)
```

### 5.2 volatility_reversion（波动率回归）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | akshare DataFrame | ✅ | 布林带位置 |

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
| `close` | akshare DataFrame | ✅ | 价格变化 |
| `volume` | akshare DataFrame | ✅ | 量比 |

**核心代码**：

```python
avg_vol = convolve(volume, ones(10)/10)  # 10 日均量
vol_ratio = volume / max(avg_vol, 1e-10)
chg = (close - shift(close, 1)) / max(shift(close, 1), 1e-10)
# 放量+涨 → 偏多；放量+跌 → 偏空
score = where(vol_ratio > 1.3, tanh(chg/0.02)*0.5,
       where(vol_ratio < 0.7, tanh(chg/0.05)*0.3, 0))
```

### 5.4 low_volatility（低波因子）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | akshare DataFrame | ✅ | 波动率计算 |

**核心代码**：

```python
returns = (close - shift(close, 1)) / max(shift(close, 1), 1e-10)
vol = rolling_std(returns, 20)  # 20 日波动率
# 低波动 = 偏多
score = -tanh(vol * 20)
```

### 5.5 pmi_proxy（PMI 代理）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | akshare DataFrame | ✅ | 价格趋势近似 |

**核心代码**：用 60 日价格趋势模拟 PMI 周期。

```python
ma = convolve(close, ones(60)/60)
trend = (ma - shift(ma, 20)) / max(shift(ma, 20), 1e-10)
# 趋势向上 → 偏多（近似 PMI 扩张）
score = tanh(trend * 30) * 0.5
```

### 5.6 value_factor（价值因子，A 股专用）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | akshare DataFrame | ✅ | 价格分位 |
| `volume` | akshare DataFrame | ✅ | 量比 |

**核心代码**：

```python
# 价格分位（0=最低价，1=最高价）
pct_rank = argsort(argsort(close)) / max(n-1, 1)
avg_vol = convolve(volume, ones(20)/20)
vol_ratio = volume / max(avg_vol, 1e-10)
# 低价+放量 → 价值凸显
score = (1 - pct_rank) * tanh(vol_ratio * 0.5) - 0.3
```

**注意**：当前为代理实现，**未消费真实估值字段**（`pe_ttm` / `pb`）。生产环境应升级为消费 MCP/akshare 扩展的估值字段。

### 5.7 quality_factor（质量因子，A 股专用）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | akshare DataFrame | ✅ | 价格稳定性 + 趋势 |

**核心代码**：

```python
returns = (close - shift(close, 1)) / max(shift(close, 1), 1e-10)
rolling_vol = rolling_std(returns, 20)  # 20 日波动率
ma = convolve(close, ones(20)/20)
ma_slope = (ma - shift(ma, 1)) / max(shift(ma, 1), 1e-10)
# 低波动+正斜率 = 高质量
quality_score = tanh(-rolling_vol * 20 + 0.5) + tanh(ma_slope * 30)
```

**注意**：当前为代理实现，**未消费真实盈利字段**（需通过 MCP/akshare 扩展注入）。

### 5.8 size_factor（市值因子，A 股专用）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | akshare DataFrame | ✅ | 价格水平 |
| `volume` | akshare DataFrame | ✅ | 成交量偏离 |

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

FTS 内置种子因子全部实现"首选 → 降级"双路径，当 MCP/akshare 扩展字段未注入时退化为纯 `close` 计算。

### 6.2 估值/质量/市值字段缺失的影响

当 `pe_ttm` / `total_market_cap` 等 MCP 扩展字段未注入时：

| 因子 | 实际行为 |
|------|----------|
| `value_factor` | 用价格分位 + 量比近似（精度低） |
| `quality_factor` | 用价格波动率 + 趋势近似（精度低） |
| `size_factor` | 用价格 + 成交量近似（精度低） |

**改进建议**：
1. 在 `FTSDataProvider.get_csi300_panel()` 中注入 `pe_ttm` / `pb` / `total_market_cap` 等列
2. 修改 `value_factor` / `quality_factor` / `size_factor` 优先消费真实字段

### 6.3 字段注入位置

`FTSDataProvider.get_csi300_panel()` 中，获取 K 线 DataFrame 后调用扩展注入函数：

```python
def get_csi300_panel(self, days=250, max_stocks=300):
    panels = {}
    for symbol in self._mcp.list_csi300()[:max_stocks]:
        df = self._mcp.get_ohlcv(symbol, days=days)
        if df is not None and len(df) >= 60:
            df = _enrich_with_fundamental(df, symbol)
            panels[symbol] = df
    return panels
```

---

## 7. 股票数据消费要点

FTS v1.2.0 已移除期货因子，当前仅聚焦 A 股/ETF 因子演化：

| 维度 | 说明 |
|------|------|
| **OHLCV 必需字段** | `close` |
| **常用字段** | `close` + `volume`（外部因子还消费 `high`/`low`/`open`/`amount`） |
| **扩展字段** | `pe_ttm` / `pb` / `total_market_cap` / `turnover_rate`（通过 MCP/akshare 注入） |
| **横截面品种数** | 3（CSI 300 默认 300 只） |
| **数据频率** | 日频 |
| **复权方式** | 前复权（akshare 默认） |
| **截面日期对齐** | 严格（停牌股票需跳过） |
| **评估单位** | 单只股票次日收益 |

---

## 8. 版本与变更

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.0 | 2026-07-21 | 初版：与 Data-Core v1.0.0 数据字典对齐 |
| v1.2.0 | 2026-08-02 | 全面更新：数据源从 Data-Core 迁移至 MCP/akshare；种子池从 9 因子扩展至 268 因子（含世坤 101 + Qlib 158）；移除期货因子支持；更新代码示例、字段来源和降级策略 |

维护：当 MCP/akshare 数据源字段新增/废弃/重命名时，必须同步更新本文档。

---

## 9. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| File | `docs/factor_data_dict/stock_factor_fields.md` |
| 代码→文档映射 | `fts/data_mcp.py` (MCPDataProvider 字段定义)、`fts/factor_engine/seed_data/` (种子因子字段消费) |
| 可验证断言 | 文档中所有字段来源（akshare DataFrame）与 `fts/data_mcp.py` 中 `MCPDataProvider.get_ohlcv()` 返回值列名一致 |
| 检验方式 | 运行 `python -c "from fts.data_mcp import MCPDataProvider; p = MCPDataProvider(); df = p.get_ohlcv('000001', days=5); print(list(df.columns))"` 验证列名 |
