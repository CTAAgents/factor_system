# TQSDK Tick 数据源接入实施计划（Phase 3）

> 版本: v2.105.0+2
> 最后更新: 2026-08-08
> 状态: 已完成（TQSDK tick 数据源已接入，10 测试全绿）
> 适用范围: FTS 数据层 + 回测流水线

---

## 0. 背景与目标

### 0.1 现状

FTS 已接入分钟级数据源（TDX 17709 / TQ-Local 7721 / TQSDK），支持 1m/5m/15m/30m/60m 回测。但分钟级 K 线由交易所聚合，丢失了逐笔微观结构信息：

- 无盘口深度（bid/ask 五档）变化
- 无法度量买卖价差（spread）与冲击成本
- 无法精确模拟成交（滑点、部分成交）
- 分钟级 IC 衰减/信号自相关受聚合噪声污染

### 0.2 目标

将 TQSDK 的 **tick 逐笔数据**作为 FTS 数据源接入，实现：

1. **tick 数据适配器**：`TQSDKTickSource` 通过 `get_tick_serial` 获取逐笔行情
2. **tick 缓存**：DuckDB `tick_cache` 表持久化逐笔数据
3. **tick 聚合**：`FuturesDataAggregator.get_ticks()` 支持 tick 级数据路径
4. **tick → OHLCV 重采样**：由 tick 合成任意周期分钟 K 线（备用路径）
5. **Provider 接口**：`FuturesDataProvider.get_tick_data()`

### 0.3 不在范围

- Tick 级逐笔回测引擎（Phase 4，需另行设计成交撮合模型）
- 实时 tick 订阅推送（仅按需拉取）
- 盘口深度因子计算（Phase 5）

### 0.4 TQSDK 免费账号限制（实测 2026-08-08）

| 维度 | 实测值 |
|:----|:------|
| 最大行数 | 5000 行（`data_length` 上限 10000） |
| 覆盖时长 | ≈42 分钟（RB0，2026-08-07 14:18 → 14:59） |
| 返回格式 | `get_tick_serial` → DataFrame |
| 关键列 | datetime/last_price/average/highest/lowest/volume/amount/open_interest |
| 盘口 | bid_price1-5 + ask_price1-5 + bid_volume1-5 + ask_volume1-5 |

> 注意：免费账号 tick 历史极短（分钟级），仅适合实时/近实时分析；历史 tick 需付费账号。

---

## 1. 架构设计

### 1.1 数据流

```
┌──────────────────────────────────────────────────────────────┐
│                FuturesDataProvider                            │
│   get_tick_data(symbol, count, trace_id)                      │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│           FuturesDataAggregator (tick 扩展)                    │
│                                                              │
│   1. tick_cache（DuckDB）命中且新鲜 → 返回                     │
│   2. TQSDKTickSource.fetch_ticks() → 写入 tick_cache          │
│   3. 全部失败 → 空 DataFrame                                  │
└──────────────────────────┬───────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────┐
│                TQSDKTickSource                                │
│   api.get_tick_serial(tq_symbol, data_length=count)           │
│   TqAuth(TQSDK_USERNAME, TQSDK_PASSWORD)                     │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 tick 数据契约（tick_cache 表）

```sql
CREATE TABLE IF NOT EXISTS tick_cache (
    symbol          VARCHAR,
    datetime        TIMESTAMP,       -- tick 时间（纳秒精度 → 截断微秒）
    last_price      DOUBLE,
    average         DOUBLE,
    highest         DOUBLE,
    lowest          DOUBLE,
    volume          DOUBLE,          -- 当日累计成交量
    amount          DOUBLE,          -- 当日累计成交额
    open_interest   DOUBLE,          -- 持仓量
    bid_price1      DOUBLE, bid_volume1  DOUBLE,
    ask_price1      DOUBLE, ask_volume1  DOUBLE,
    source          VARCHAR,
    fetched_at      TIMESTAMP,
    trace_id        VARCHAR
)
```

### 1.3 返回 DataFrame schema

`get_tick_data` 返回列（与 tick_cache 对齐）：
```
symbol, datetime, last_price, average, highest, lowest,
volume, amount, open_interest,
bid_price1, bid_volume1, ask_price1, ask_volume1,
source, fetched_at, trace_id
```

---

## 2. 模块变更清单

### 2.1 新增文件

| 文件 | 职责 |
|:----|:-----|
| `fts/data_sources/tqsdk_tick_source.py` | TQSDK tick 逐笔数据适配器 |

### 2.2 修改文件

| 文件 | 变更内容 |
|:----|:---------|
| `fts/data_sources/migrate.py` | 新增 `tick_cache` 表 DDL + 迁移逻辑 |
| `fts/data_sources/aggregator.py` | 新增 `tick_sources` 参数 + `get_ticks()` 方法 |
| `fts/data_sources/__init__.py` | 导出 `TQSDKTickSource` |
| `fts/data_futures.py` | `FuturesDataProvider` 新增 `get_tick_data()` 方法 |

### 2.3 DataSource 枚举

`fts/core/enums.py` 的 `DataSource` 新增成员：
```python
TQSDK_TICK = "TQSDK_TICK"   # 天勤 TQSDK tick 逐笔数据源
```

---

## 3. TQSDKTickSource 契约

```python
class TQSDKTickSource(BaseFuturesSource):
    """天勤 TQSDK tick 逐笔数据适配器。"""

    source_name: str = "TQSDK_TICK"

    def fetch_ticks(self, symbol: str, count: int = 5000,
                    trace_id: str = "") -> Optional[pd.DataFrame]:
        """获取 tick 逐笔数据。
        - 复用 _SYMBOL_MAP 连续合约映射（KQ.m@SHFE.rb）
        - TqAuth 认证（TQSDK_USERNAME/TQSDK_PASSWORD 环境变量）
        - get_tick_serial(tq_symbol, data_length=count)
        - wait_update(deadline=15s) 等待数据
        - 返回正序（旧→新）tick 序列
        """
```

注：`BaseFuturesSource` 已有 `fetch_ohlcv/fetch_quote/is_available` 三个抽象方法。
`TQSDKTickSource` 实现 `fetch_ohlcv` 返回 tick 数据（兼容契约），
另提供 `fetch_ticks` 语义化入口；`fetch_quote` 返回最新 tick 快照。

---

## 4. 执行计划

### Step 1: 契约定义（本文档 §1.2/§1.3）
- tick_cache 表 DDL + 返回 schema 冻结

### Step 2: migrate.py 新增 tick_cache
- `TICK_CACHE_CREATE_DDL` 常量
- `migrate_schema` 中创建表

### Step 3: TQSDKTickSource 实现
- 复用 `_SYMBOL_MAP` / TqAuth / `get_tick_serial`
- 纳秒 datetime → Timestamp
- 字段标准化（last_price/average/盘口）

### Step 4: Aggregator 扩展
- 构造函数新增 `tick_sources: list | None`
- `get_ticks(symbol, count, trace_id)`：tick_cache → tick_sources 降级
- `_read_tick_cache` / `_write_tick_cache`

### Step 5: Provider 接口 + 测试
- `FuturesDataProvider.get_tick_data()`
- 测试用例：映射复用 / 数据解析 / 缓存读写 / 降级

---

## 5. 反模式检查

| 检查项 | 状态 |
|:------|:-----|
| AP01 巨型 Prompt | 通过（本计划 < 300 行） |
| AP02 跳过审核 | 待用户审核 |
| AP06 无独立验证 | 通过（Step 5 测试 + 实测验证） |
| AP10 一个 PR 改所有 | 4 文件 + 1 新增，< 20 文件 |

---

## 6. 一致性元数据

| 代码映射 | 可验证断言 | 检验方式 |
|:--------|:----------|:--------|
| `fts/data_sources/tqsdk_tick_source.py` → §3 | TQSDKTickSource.fetch_ticks 返回 tick schema | `pytest tests/data_sources/test_tqsdk_tick_source.py` |
| `fts/data_sources/migrate.py` → §1.2 | tick_cache 表可创建且含 15 列 | `pytest tests/data_sources/test_migrate.py` |
| `fts/data_sources/aggregator.py` → §1.1 | Aggregator.get_ticks 返回 tick 数据 | `pytest tests/data_sources/test_aggregator.py` |
| `fts/data_futures.py` → §1.1 | FuturesDataProvider.get_tick_data 可用 | `python scripts/probe_tick.py` |
| `fts/core/enums.py` → §2.3 | DataSource 含 TQSDK_TICK 成员 | `pytest tests/core/test_enums.py` |
| `docs/harness/plans/16-tick-data-source-plan.md` → 本文件 | 计划与实现一致 | `python scripts/verify_doc_consistency.py` |
