# FTS 期货专用因子消费数据字段字典

> 版本: v1.0.0
> 适用项目: FTS (Factor Transformation System)
> 维护: FTS Team
> 状态: 已与 Data-Core 字段对齐
> 存放路径: factor_system/docs/factor_data_dict/futures_factor_fields.md

本文档聚焦于 FTS 在 `--universe futures` 模式下，因子计算所消费的 Data-Core 字段。期货专用因子与全市场通用因子的区别在于：其 `signature.input_fields` 中含期货特定字段（`basis_pct` / `inventory_pct` / `open_interest` / `top5_ratio` / `warrant_change_pct` 等）。

---

## 0. 目录

1. [种子池期货因子总览](#1-种子池期货因子总览)
2. [OHLCV 基础字段（期货使用部分）](#2-ohlcv-基础字段期货使用部分)
3. [期货专用字段](#3-期货专用字段)
4. [派生字段（Data-Core 加工，FTS 直接消费）](#4-派生字段data-core-加工fts-直接消费)
5. [每个种子因子的字段消费清单](#5-每个种子因子的字段消费清单)
6. [字段缺失时的降级策略](#6-字段缺失时的降级策略)
7. [版本与变更](#7-版本与变更)

---

## 1. 种子池期货因子总览

FTS `seed_pool.py` 中期货相关种子因子（与 A 股因子分离）：

| # | 因子名 | 类型 | 必需字段 | 降级字段（缺失时使用） |
|---|--------|------|----------|------------------------|
| 4 | `oi_change` | 期货 | `close`, `open_interest` | `volume` |
| 5 | `basis` | 期货 | `basis_pct` | `high`, `low` |
| 6 | `inventory_pct` | 期货 | `inventory_pct` | `volume` |
| 7 | `capacity` | 期货 | `capacity_pct` | `close`（派生波动率） |
| 11 | `position_rank` | 期货 | `top5_ratio` | `volume` |
| 12 | `warrant_change` | 期货 | `warrant_change_pct` | `volume` |

> 注：种子因子编号与 `seed_pool.py` 中 `_SEED_DEFINITIONS` 列表顺序一致。

---

## 2. OHLCV 基础字段（期货使用部分）

期货 K 线数据来自 `DataPayload(data_type=DataType.OHLCV, market=MarketType.FUTURES)`，解包后为 `KlineData`（含 `bars: list[KBar]`）。

| 字段 | 来源 | FTS 因子消费 | 必备 |
|------|------|-------------|------|
| `close` | `KBar.close` | oi_change / basis / inventory_pct / capacity / position_rank / warrant_change | 是 |
| `high` | `KBar.high` | basis（降级路径） | 否 |
| `low` | `KBar.low` | basis（降级路径） | 否 |
| `open` | `KBar.open` | — | 否 |
| `volume` | `KBar.volume` | oi_change（降级）/ inventory_pct（降级）/ position_rank（降级）/ warrant_change（降级） | 否 |
| `amount` | `KBar.amount` | — | 否 |
| `open_interest` | `KBar.open_interest` | oi_change（首选） | 否（有则优先，无则降级） |
| `settlement` | `KBar.settlement` | — | 否 |

**消费示例**（`oi_change` 因子）：

```python
if 'open_interest' in data.columns:
    oi = data['open_interest'].values  # 首选路径
    # ... 计算 OI 变化率 ...
else:
    volume = data['volume'].values  # 降级路径
    # ... 用 volume 变化率代理 ...
```

---

## 3. 期货专用字段

来自 `DataPayload(data_type ∈ {FUTURES_BASIS, FUTURES_WAREHOUSE_RECEIPT, FUTURES_POSITION})`，解包为对应 dataclass。

### 3.1 basis_pct（基差百分比）

**Data-Core 来源**：`BasisData.basis_pct`（`data_type=FUTURES_BASIS`）

| 项 | 值 |
|----|----|
| 单位 | %（数值，不带 % 号） |
| 缺失值 | None → FTS 视为字段不存在，触发降级 |
| 默认值 | 0.0 |
| 计算公式 | `basis / futures_price × 100` |
| 因子消费 | `basis` |

**消费示例**：

```python
if 'basis_pct' in data.columns:
    basis_pct = data['basis_pct'].values
    # 基差 > 阈值 → 期现套利空间 → 反向信号
    score = np.where(basis_pct > threshold, -0.6, ...)
```

### 3.2 inventory_pct（库存分位）

**Data-Core 来源**：`WarehouseReceiptData.inventory_pct`（`data_type=FUTURES_WAREHOUSE_RECEIPT`）

| 项 | 值 |
|----|----|
| 单位 | 0~1 数值（0.3 = 30% 分位） |
| 缺失值 | None → 触发降级 |
| 默认值 | 0.0 |
| 因子消费 | `inventory_pct` |

**消费示例**：

```python
if 'inventory_pct' in data.columns:
    pct = data['inventory_pct'].values
    # 累库（pct>0.5）偏空，去库（pct<0.5）偏多
    score = (0.5 - pct) * 2.0
```

### 3.3 capacity_pct（开工率分位）

**Data-Core 来源**：**当前 Data-Core 未提供该字段**，需上游 ETL 加工。FTS 视为可选字段。

| 项 | 值 |
|----|----|
| 单位 | 0~1 数值 |
| 缺失值 | None → 触发降级（用价格波动率近似） |
| 因子消费 | `capacity` |

**降级逻辑**：

```python
if 'capacity_pct' in data.columns:
    pct = data['capacity_pct'].values
    score = (0.5 - pct) * 2.0
else:
    # 用 30 日滚动波动率反推开工率
    vol_std = rolling_std(close, 30)
    avg_std = rolling_mean(vol_std, 30)
    vol_ratio = vol_std / max(avg_std, 1e-10)
    score = tanh((1 - vol_ratio) * 1.5) * 0.5
```

### 3.4 open_interest（持仓量）

**Data-Core 来源**：`KBar.open_interest`（OHLCV 内嵌字段，非独立 DataType）

| 项 | 值 |
|----|----|
| 单位 | 手 |
| 缺失值 | 0.0（Data-Core 默认） |
| 因子消费 | `oi_change`（首选） |

**说明**：OHLCV 内嵌而非独立 DataType，是因为持仓量是期货每根 K 线的标准字段。FTS 因子读取时直接判断 `data['open_interest']` 是否存在且非全 0。

### 3.5 top5_ratio（前 5 名持仓集中度变化）

**Data-Core 来源**：`PositionRankData`（`data_type=FUTURES_POSITION`），需 FTS 上游 ETL 计算派生

| 项 | 值 |
|----|----|
| 单位 | 比率（-1 ~ +1，正值=多头集中、负值=空头集中） |
| 缺失值 | None → 触发降级 |
| 因子消费 | `position_rank` |

**派生公式**（在 FTS 上游 ETL 中实现）：

```python
top5_long_vol = sum(item.volume for item in position_rank.long_ranks[:5])
top5_short_vol = sum(item.volume for item in position_rank.short_ranks[:5])
top5_ratio = (top5_long_vol - top5_short_vol) / (top5_long_vol + top5_short_vol)
```

### 3.6 warrant_change_pct（仓单变化百分比）

**Data-Core 来源**：`WarehouseReceiptData.change / total_receipts`（派生自 `data_type=FUTURES_WAREHOUSE_RECEIPT`）

| 项 | 值 |
|----|----|
| 单位 | %（已是数值） |
| 缺失值 | None → 触发降级 |
| 因子消费 | `warrant_change` |

**派生公式**：

```python
warrant_change_pct = warehouse_receipt.change / max(warehouse_receipt.total_receipts, 1e-10) * 100
```

---

## 4. 派生字段（Data-Core 加工，FTS 直接消费）

部分 FTS 因子消费的字段需要 Data-Core 加工层（`datacore/processing/`）做预处理后注入。FTS 不做加工，直接读 `data['xxx']`。

| 字段 | Data-Core 加工路径 | 注入方式 |
|------|--------------------|----------|
| `basis_pct` | `datacore/tools/basis.py` 实时计算 | 注入 K 线 panel 的列 |
| `inventory_pct` | `datacore/tools/ohlcv.py` 加载时计算 | 注入 K 线 panel 的列 |
| `capacity_pct` | （待实现） | 注入 K 线 panel 的列 |
| `top5_ratio` | （待实现） | 注入 K 线 panel 的列 |
| `warrant_change_pct` | `datacore/tools/ohlcv.py` 加载时计算 | 注入 K 线 panel 的列 |
| `macro_signal` | `datacore/processing/market_regime.py` | 注入 K 线 panel 的列（每日一行） |
| `rate_mom` | `datacore/tools/macro.py` | 注入 K 线 panel 的列 |
| `pmi` / `pmi_mom` | `datacore/tools/macro.py` | 注入 K 线 panel 的列 |

**FTS 消费方式**：因子程序接收 `data: pd.DataFrame`，列名即为字段名。例如：

```python
def factor_program(data, params):
    # data 是 pd.DataFrame，列名包括 OHLCV + 上述派生字段
    if 'basis_pct' in data.columns:
        ...
```

---

## 5. 每个种子因子的字段消费清单

### 5.1 oi_change（持仓量变化）

| 路径 | 字段 | 来源 | 必选 |
|------|------|------|------|
| 首选 | `close` | KBar | ✅ |
| 首选 | `open_interest` | KBar | ✅ |
| 降级 | `volume` | KBar | ⚠️（open_interest 缺失时使用） |

**首选逻辑**：

```python
oi_ratio = (oi - oi_prev) / max(oi_prev, 1e-10)
chg = (close - close_prev) / max(close_prev, 1e-10)
# 持仓+价格上涨 → 强势多头 → +0.6
# 持仓+价格下跌 → 强势空头 → -0.6
# 持仓-价格上涨 → 空头平仓 → +0.3
# 持仓-价格下跌 → 多头平仓 → -0.3
```

### 5.2 basis（基差）

| 路径 | 字段 | 来源 | 必选 |
|------|------|------|------|
| 首选 | `basis_pct` | BasisData | ✅ |
| 降级 | `close`, `high`, `low` | KBar | ⚠️（基差数据缺失时使用） |

**首选逻辑**：

```python
# basis_pct 上升 → 期货升水 → 期现回归预期偏空
# basis_pct 下降 → 期货贴水 → 期现回归预期偏多
score = where(basis_pct > +1.0, -0.6,
       where(basis_pct > +0.5, -0.3,
       where(basis_pct < -1.0, +0.6,
       where(basis_pct < -0.5, +0.3, 0))))
```

**降级逻辑**：用 `close` 在 `(high, low)` 区间分位近似期现位置。

### 5.3 inventory_pct（库存分位）

| 路径 | 字段 | 来源 | 必选 |
|------|------|------|------|
| 首选 | `inventory_pct` | WarehouseReceiptData | ✅ |
| 降级 | `volume`, `close` | KBar | ⚠️ |

**首选逻辑**：

```python
# 库存分位 > 0.5 → 累库 → 偏空
# 库存分位 < 0.5 → 去库 → 偏多
score = (0.5 - inventory_pct) * 2.0
```

**降级逻辑**：用量比反推供需（放量=累库=偏空，缩量=去库=偏多）。

### 5.4 capacity（开工率）

| 路径 | 字段 | 来源 | 必选 |
|------|------|------|------|
| 首选 | `capacity_pct` | （待实现） | ✅ |
| 降级 | `close` | KBar | ⚠️（用滚动波动率反推） |

### 5.5 position_rank（持仓集中度）

| 路径 | 字段 | 来源 | 必选 |
|------|------|------|------|
| 首选 | `top5_ratio` | PositionRankData（派生） | ✅ |
| 降级 | `volume`, `close` | KBar | ⚠️ |

**首选逻辑**：

```python
# top5_ratio > 0.4 → 多头集中 → +0.3
# top5_ratio > 0.3 → 多头集中 → +0.15
# top5_ratio < -0.3 → 空头集中 → -0.3
# top5_ratio < -0.2 → 空头集中 → -0.15
```

### 5.6 warrant_change（仓单变化）

| 路径 | 字段 | 来源 | 必选 |
|------|------|------|------|
| 首选 | `warrant_change_pct` | WarehouseReceiptData（派生） | ✅ |
| 降级 | `volume` | KBar | ⚠️ |

**首选逻辑**：

```python
# 仓单增加 → 可交割供应增多 → 偏空
# 仓单减少 → 可交割供应减少 → 偏多
score = -tanh(warrant_change_pct / 5.0) * 0.4
```

---

## 6. 字段缺失时的降级策略

### 6.1 通用降级模式

FTS 种子因子全部实现"首选 → 降级"双路径：

```python
def factor_program(data, params):
    if 'preferred_field' in data.columns:
        # 首选路径
        ...
    else:
        # 降级路径（用 OHLCV 基础数据近似）
        ...
```

### 6.2 字段缺失的日志

FTS 在因子计算时，若检测到降级路径，会在 `trace_id` 关联的日志中记录：

```python
logger.warning(f"[降级] 因子 {name}: 字段 {field} 缺失，使用 {fallback} 替代")
```

### 6.3 字段缺失对评估的影响

- **首选字段缺失 + 降级可用** → 因子仍可计算，但 IC/稳定性可能下降
- **降级路径也失败** → 因子返回全 0 信号，触发 `evaluation_chain` 的常量输入检查（`np.std < 1e-10`），跳过该因子

### 6.4 期货横截面评估的额外约束

横截面评估（`cross_section_evaluate_backtest`）要求：
- 同截面至少 **3 个品种**（已从原 10 调低）
- 截面日期覆盖完整
- OHLCV 字段全 0 时返回 nan 而非崩溃

---

## 7. 版本与变更

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.0 | 2026-07-21 | 初版：与 Data-Core v1.0.0 数据字典对齐 |

维护：当 Data-Core 字段新增/废弃/重命名时，必须同步更新本文档。
