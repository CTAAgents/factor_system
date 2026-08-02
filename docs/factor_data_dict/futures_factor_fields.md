# FTS 期货专用因子消费数据字段字典

> 版本: v1.2.0
> 适用项目: FTS (Factor Intelligence System)
> 维护: FTS Team
> 状态: **参考归档** — FTS v1.2.0 已移除期货因子支持，当前仅聚焦 A 股/ETF 因子演化
> 存放路径: factor_system/docs/factor_data_dict/futures_factor_fields.md

---

**重要声明**：FTS 自 v1.2.0 起不再支持期货因子演化。本文档仅供历史参考和归档用途，内容基于 v1.1.0 及之前版本的期货因子实现。期货因子（oi_change / basis / inventory_pct / capacity / position_rank / warrant_change）已在 v1.2.0 中从种子池中移除。

---

## 0. 目录

1. [背景与移除原因](#1-背景与移除原因)
2. [原期货因子总览](#2-原期货因子总览)
3. [OHLCV 基础字段（期货使用部分）](#3-ohlcv-基础字段期货使用部分)
4. [期货专用字段（Data-Core 加工）](#4-期货专用字段data-core-加工)
5. [每个期货种子的字段消费清单](#5-每个期货种子的字段消费清单)
6. [字段缺失时的降级策略](#6-字段缺失时的降级策略)
7. [期货数据消费要点](#7-期货数据消费要点)
8. [版本与变更](#8-版本与变更)
9. [一致性元数据](#9-一致性元数据)

---

## 1. 背景与移除原因

FTS 最初同时支持 A 股和期货因子演化，种子池包含 6 个期货专用因子。在 v1.2.0 中，因以下原因移除了期货支持：

| 原因 | 说明 |
|:-----|:------|
| 数据源迁移 | 数据源从 Data-Core 迁移至 MCP/akshare（腾讯/东方财富 API），后者主要提供 A 股/ETF 数据 |
| 期货数据复杂 | 期货需要连续合约、主力合约切换、展期处理等复杂逻辑 |
| 专注 A 股 | 项目聚焦于沪深 300 横截面因子演化，期货因子无实际数据支撑 |

**移除的期货因子**：

| 因子名 | 代码名 | 涉及市场 | 依赖字段 |
|--------|--------|----------|----------|
| 持仓量变化 | `oi_change` | 期货 | `close`, `open_interest` |
| 基差 | `basis` | 期货 | `close`, `basis`（期货专用字段） |
| 库存分位 | `inventory_pct` | 期货 | `inventory_pct`（期货专用字段） |
| 开工率 | `capacity` | 期货 | `capacity`（期货专用字段） |
| 龙虎持仓 | `position_rank` | 期货 | `position_rank`（期货专用字段） |
| 仓单变化 | `warrant_change` | 期货 | `warrant_change`（期货专用字段） |

---

## 2. 原期货因子总览

FTS v1.1.0 种子池共 9 个内置因子，其中 6 个为期货专用。期货因子依赖 Data-Core 的期货数据模块。

| # | 因子名 | 类型 | 必需字段 | 备注 |
|---|--------|------|----------|------|
| 1 | `oi_change` | 期货 | `close`, `open_interest` | 持仓量变化率 |
| 2 | `basis` | 期货 | `close`, `basis` | 基差（现货-期货价差） |
| 3 | `inventory_pct` | 期货 | `close`, `inventory_pct` | 库存分位 |
| 4 | `capacity` | 期货 | `close`, `capacity` | 开工率 |
| 5 | `position_rank` | 期货 | `close`, `position_rank` | 龙虎持仓排名 |
| 6 | `warrant_change` | 期货 | `close`, `warrant_change` | 仓单变化率 |

---

## 3. OHLCV 基础字段（期货使用部分）

期货 K 线数据来自 `DataPayload(data_type=DataType.OHLCV, market=MarketType.FUTURES)`，解包后为 `KlineData`（含 `bars: list[KBar]`）。

| 字段 | 来源 | FTS 因子消费 | 必备 |
|------|------|-------------|------|
| `close` | `KBar.close` | 全部期货因子 | 是 |
| `high` | `KBar.high` | （备用） | 否 |
| `low` | `KBar.low` | （备用） | 否 |
| `open` | `KBar.open` | （备用） | 否 |
| `volume` | `KBar.volume` | （备用） | 否 |
| `open_interest` | `KBar.open_interest` | oi_change | 是（oi_change 专用） |
| `settlement` | `KBar.settlement` | （备用） | 否 |

**期货字段特点**：
- `open_interest` 反映市场持仓兴趣，是期货特有的指标
- `settlement` 为结算价，期货日线包含该字段
- `volume` 单位是**手**（股票是**股**）
- 期货使用**连续合约**或**主力合约**，需处理展期

---

## 4. 期货专用字段（Data-Core 加工）

期货因子依赖 Data-Core 的期货数据模块提供的加工字段。这些字段在 MCP/akshare 数据源中不可用。

| 字段 | Data-Core 来源 | 因子 | 说明 |
|------|----------------|------|------|
| `basis` | `datacore/futures/basis.py` | basis | 基差 = 现货价格 - 期货价格 |
| `inventory_pct` | `datacore/futures/inventory.py` | inventory_pct | 库存历史分位（0~1） |
| `capacity` | `datacore/futures/capacity.py` | capacity | 行业开工率（0~1） |
| `position_rank` | `datacore/futures/position_rank.py` | position_rank | 龙虎榜持仓排名得分 |
| `warrant_change` | `datacore/futures/warrant.py` | warrant_change | 仓单注册量变化率 |

### 4.1 字段注入时序

```text
Data-Core 期货数据层 → 合约管理（连续合约/主力切换） → 加工字段注入 → FTS 因子消费
        ↑                            ↑                           ↑
   期货数据源 ETL              adjust_contract()           data['basis'] = ...
```

---

## 5. 每个期货种子的字段消费清单

### 5.1 oi_change（持仓量变化）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格变化 |
| `open_interest` | KBar | ✅ | 持仓量变化 |

**核心代码**：

```python
oi_ratio = open_interest / max(shift(open_interest, 1), 1e-10)  # 持仓量比
chg = (close - shift(close, 1)) / max(shift(close, 1), 1e-10)
# 增仓+涨 → 趋势强劲；减仓+跌 → 趋势衰竭
score = where(oi_ratio > 1.05, tanh(chg/0.02) * 0.6,
       where(oi_ratio < 0.95, -tanh(chg/0.02) * 0.4, 0))
```

### 5.2 basis（基差）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 期货价格 |
| `basis` | Data-Core 加工 | ✅ | 基差（现货-期货） |

**核心代码**：

```python
# 基差 = 现货 - 期货；正基差（backwardation）→ 偏多
basis_ratio = basis / max(close, 1e-10)
basis_z = (basis_ratio - mean(basis_ratio)) / max(std(basis_ratio), 1e-10)
score = tanh(basis_z * 2.0) * 0.5
```

### 5.3 inventory_pct（库存分位）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格 |
| `inventory_pct` | Data-Core 加工 | ✅ | 库存历史分位（0~1） |

**核心代码**：

```python
# 库存低分位（<0.3）→ 供给紧张 → 偏多
# 库存高分位（>0.7）→ 供给过剩 → 偏空
score = (0.5 - inventory_pct) * 1.0
```

### 5.4 capacity（开工率）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格 |
| `capacity` | Data-Core 加工 | ✅ | 开工率（0~1） |

**核心代码**：

```python
cap_mom = capacity - shift(capacity, 1)
# 开工率上升 → 供给增加 → 偏空
score = -tanh(cap_mom / 0.05) * 0.5
```

### 5.5 position_rank（龙虎持仓）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格 |
| `position_rank` | Data-Core 加工 | ✅ | 龙虎持仓排名得分 |

**核心代码**：

```python
# position_rank > 0.6 → 多头集中 → 偏多
# position_rank < 0.4 → 空头集中 → 偏空
score = (position_rank - 0.5) * 1.0
```

### 5.6 warrant_change（仓单变化）

| 字段 | 来源 | 必选 | 说明 |
|------|------|------|------|
| `close` | KBar | ✅ | 价格 |
| `warrant_change` | Data-Core 加工 | ✅ | 仓单注册量变化率 |

**核心代码**：

```python
# 仓单增加 → 可供交割量上升 → 偏空
# 仓单减少 → 可供交割量下降 → 偏多
score = -tanh(warrant_change / 0.1) * 0.5
```

---

## 6. 字段缺失时的降级策略

### 6.1 通用降级模式

所有期货因子实现"首选 → 降级"双路径，当 Data-Core 加工字段未就绪时退化为纯 `close` 计算：

| 因子 | 首选字段 | 降级路径 |
|------|----------|----------|
| `oi_change` | `open_interest` | 纯 `close` 动量 |
| `basis` | `basis` | 纯 `close` 趋势 |
| `inventory_pct` | `inventory_pct` | 纯 `close` 趋势 |
| `capacity` | `capacity` | 纯 `close` 趋势 |
| `position_rank` | `position_rank` | 纯 `close` 趋势 |
| `warrant_change` | `warrant_change` | 纯 `close` 趋势 |

### 6.2 降级后的影响

所有期货因子在降级后都退化为简单的价格动量或趋势跟踪因子，导致：
- **因子多样性丧失**：6 个因子实际上变成同一个动量因子的变体
- **相关性飙升**：降级后因子间相关性接近 1.0，L3 正交化后可能全部被剔除
- **信号价值下降**：失去期货特有的基本面/持仓信息

---

## 7. 期货数据消费要点

| 维度 | 说明 |
|:-----|:------|
| **OHLCV 必需字段** | `close` |
| **期货特有字段** | `open_interest`（持仓量）、`settlement`（结算价） |
| **加工字段** | `basis` / `inventory_pct` / `capacity` / `position_rank` / `warrant_change` |
| **数据来源** | Data-Core（已废弃），MCP/akshare 暂无期货数据 |
| **合约管理** | 需主力合约识别 + 展期处理（FTS 委托 Data-Core 处理） |
| **横截面品种数** | 3（期货默认 56 品种） |
| **数据频率** | 日频（部分品种提供分钟） |
| **复权方式** | 连续合约 / 主力合约 |
| **截面日期对齐** | 较松（节假日合约不同） |
| **评估单位** | 单品种主力合约次日收益 |

---

## 8. 版本与变更

| 版本 | 日期 | 变更 |
|:-----|:-----|:------|
| v1.0.0 | 2026-07-21 | 初版：与 Data-Core 期货数据模块对齐 |
| v1.2.0 | 2026-08-02 | **归档**：FTS 移除期货支持，本文档标记为参考归档 |

---

## 9. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| File | `docs/factor_data_dict/futures_factor_fields.md` |
| 代码→文档映射 | 无（期货因子代码已在 v1.2.0 中移除，本文档仅作历史参考） |
| 可验证断言 | 文档中所有期货因子（oi_change / basis / inventory_pct / capacity / position_rank / warrant_change）已在 `fts/factor_engine/seed_pool.py` 中移除 |
| 检验方式 | 运行 `grep -r "oi_change\|inventory_pct\|position_rank\|warrant_change" fts/factor_engine/` 确认无匹配 |