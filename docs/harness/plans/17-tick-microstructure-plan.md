# tick 盘口微观结构特征分析实施计划（Phase 5）

> 版本: v2.81.0
> 最后更新: 2026-08-08
> 状态: 已完成（盘口微观结构分析脚本已交付，12 测试全绿）
> 适用范围: FTS 数据层 + 分析脚本

---

## 0. 背景与目标

### 0.1 现状

TQSDK tick 数据源已接入（v2.31.0，见 `16-tick-data-source-plan.md`），可获取逐笔行情含 5 档盘口。
但当前 `tick_cache` 表与适配器仅保留 1 档盘口（bid_price1/ask_price1），
而盘口微观结构分析需要完整的 5 档深度信息。

### 0.2 目标

1. **扩展 tick 数据契约**：tick_cache 表与 TQSDKTickSource 保留完整 5 档盘口
2. **盘口微观结构分析脚本**：`scripts/tick_microstructure_analysis.py`
3. **分析维度**：
   - 买卖价差（spread）：绝对价差 / 相对价差 / 时序分布
   - 盘口深度（depth）：五档总深度 / 深度不平衡（OBI）
   - 冲击成本（impact）：Amihud 非流动性 / 有效价差 / Kyle's Lambda
   - 价差-深度联动：价差收窄与深度变化关系
4. **输出报告**：Markdown 分析报告

### 0.3 不在范围

- Tick 级逐笔回测引擎（Phase 4，另立计划）
- 盘口微观结构因子入池（需因子审计流程）
- 实时 tick 订阅

---

## 1. 数据契约扩展

### 1.1 tick_cache 表（新增 5 档盘口）

```sql
CREATE TABLE IF NOT EXISTS tick_cache (
    symbol          VARCHAR,
    datetime        TIMESTAMP,
    last_price      DOUBLE,
    average         DOUBLE,
    highest         DOUBLE,
    lowest          DOUBLE,
    volume          DOUBLE,
    amount          DOUBLE,
    open_interest   DOUBLE,
    bid_price1      DOUBLE, bid_volume1  DOUBLE,
    ask_price1      DOUBLE, ask_volume1  DOUBLE,
    -- v2.31.0 Phase 5 新增 5 档盘口
    bid_price2      DOUBLE, bid_volume2  DOUBLE,
    ask_price2      DOUBLE, ask_volume2  DOUBLE,
    bid_price3      DOUBLE, bid_volume3  DOUBLE,
    ask_price3      DOUBLE, ask_volume3  DOUBLE,
    bid_price4      DOUBLE, bid_volume4  DOUBLE,
    ask_price4      DOUBLE, ask_volume4  DOUBLE,
    bid_price5      DOUBLE, bid_volume5  DOUBLE,
    ask_price5      DOUBLE, ask_volume5  DOUBLE,
    source          VARCHAR,
    fetched_at      TIMESTAMP,
    trace_id        VARCHAR
)
```

### 1.2 TICK_COLUMNS 扩展

`fts/data_sources/tqsdk_tick_source.py` 的 `TICK_COLUMNS` 从 16 列扩展至 36 列（含 5 档盘口）。

---

## 2. 分析维度设计

### 2.1 买卖价差（Spread）

| 指标 | 公式 | 意义 |
|:----|:-----|:-----|
| 绝对价差 | ask1 - bid1 | 直接买卖价差（最小报价单位） |
| 相对价差 | (ask1 - bid1) / mid | 剔除价格水平的价差 |
| 中点价 mid | (bid1 + ask1) / 2 | 参考成交价 |

### 2.2 盘口深度（Depth）

| 指标 | 公式 | 意义 |
|:----|:-----|:-----|
| 五档买深 | sum(bid_vol1..5) | 买方承接力 |
| 五档卖深 | sum(ask_vol1..5) | 卖方供给 |
| 深度不平衡 OBI | (买深 - 卖深) / (买深 + 卖深) | 方向性压力 |

### 2.3 冲击成本（Impact）

| 指标 | 公式 | 意义 |
|:----|:-----|:-----|
| Amihud | \|return\| / amount | 非流动性度量（每元成交的价格冲击） |
| 有效价差 | 2 \* \|last_price - mid\| | 实际成交价偏离中点的程度 |
| Kyle's Lambda | Δlast_price / signed_volume | 单位成交量的价格冲击斜率 |

---

## 3. 模块变更清单

### 3.1 修改文件

| 文件 | 变更内容 |
|:----|:---------|
| `fts/data_sources/tqsdk_tick_source.py` | TICK_COLUMNS 扩展至 5 档盘口 |
| `fts/data_sources/migrate.py` | tick_cache DDL 扩展 5 档盘口列 |
| `fts/data_sources/aggregator.py` | tick_cache 读写 SQL 同步扩展列 |

### 3.2 新增文件

| 文件 | 职责 |
|:----|:-----|
| `scripts/tick_microstructure_analysis.py` | 盘口微观结构分析脚本 |
| `tests/data_sources/test_tick_microstructure.py` | 分析函数单元测试 |

---

## 4. 执行计划

### Step 1: 契约扩展（本文档 §1）
- TICK_COLUMNS + tick_cache DDL 扩展到 5 档

### Step 2: 数据层同步
- migrate.py / aggregator.py 读写 SQL 同步
- 重新拉取 tick 数据（旧缓存仅 1 档，需重建）

### Step 3: 分析脚本实现
- `scripts/tick_microstructure_analysis.py` 五大维度分析
- 输出 Markdown 报告

### Step 4: 测试 + 文档
- 单元测试（spread/depth/impact 计算）
- 运行真实数据验证
- 更新 06-testing / 07-operations / 16 号计划

---

## 5. 反模式检查

| 检查项 | 状态 |
|:------|:-----|
| AP01 巨型 Prompt | 通过（本计划 < 300 行） |
| AP02 跳过审核 | 待用户审核 |
| AP06 无独立验证 | 通过（Step 4 测试 + 实测） |
| AP10 一个 PR 改所有 | 4 文件 + 2 新增，< 20 文件 |

---

## 6. 一致性元数据

| 代码映射 | 可验证断言 | 检验方式 |
|:--------|:----------|:--------|
| `fts/data_sources/tqsdk_tick_source.py` → §1.2 | TICK_COLUMNS 含 5 档盘口（bid_price1-5/ask_price1-5） | `pytest tests/data_sources/test_tqsdk_tick_source.py` |
| `fts/data_sources/migrate.py` → §1.1 | tick_cache 表含 bid_price5/ask_price5 列 | `pytest tests/data_sources/test_migrate.py` |
| `fts/data_sources/aggregator.py` → §1.1 | get_ticks 返回含 5 档盘口 | `pytest tests/data_sources/test_tqsdk_tick_source.py` |
| `scripts/tick_microstructure_analysis.py` → §2 | 脚本可运行并产出 Markdown 报告 | `python scripts/tick_microstructure_analysis.py` |
| `docs/harness/plans/17-tick-microstructure-plan.md` → 本文件 | 计划与实现一致 | `python scripts/verify_doc_consistency.py` |
