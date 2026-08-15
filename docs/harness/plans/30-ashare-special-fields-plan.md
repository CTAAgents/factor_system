# 30. A 股特有字段数据源接入计划（GAP-081）

> 版本: v2.104.0+63（日常开发追加，不 bump 版本）
> 日期: 2026-08-12
> 状态: ✅ 已实施

## 1. 背景

`A_SHARE_FIELDS` 10 字段（northbound_flow/northbound_hold_pct/margin_balance/margin_net_buy/margin_short_balance/holder_count/analyst_up_count/analyst_down_count/analyst_total_count/analyst_eps_revision）此前仅注册 L0 恒等算子，`fts/data_sources` 与 `FundamentalProvider` 零接入；`seeds/stock/{northbound,margin_trade,holder_count,analyst_revision}.yaml` 69 个种子因子因面板缺列全部空转（`_ArrayDataWrapper.__getitem__` 抛 KeyError → 信号跳过）。

## 2. 数据源可行性实测（2026-08-12）

| 字段族 | 接口 | 频率 | 结论 |
|:--|:--|:--|:--|
| 两融 | AKShare `stock_margin_detail_sse/szse` | 日频 | ✅ 可用（沪深列名不同需归一化：沪有融资偿还额/融券卖出量，深有融券余额） |
| 股东户数 | AKShare `stock_zh_a_gdhs_detail_em` | 季度披露 | ✅ 可用（含公告日期） |
| 北向 | westock MCP `data_north_holding`（agent 侧） | 季度 | ⚠️ 个股日频已停更（2024-08 政策），东财 `RPT_MUTUAL_STOCK_NORTHSTA` 实测 0 行，唯一路径为 agent 预填充缓存 |
| 分析师 | AKShare `stock_research_report_em`（研报） | 滚动快照 | ⚠️ 部分可用（盈利预测接口 `stock_profit_forecast_em` 已坏） |

## 3. 设计

### 3.1 新建 `fts/data_sources/ashare_special_source.py`

`AshareSpecialSource`：A 股特有字段获取 + 缓存 + 对齐注入。

- 字段族注册：`MARGIN_FIELDS`（8 字段）/`HOLDER_FIELDS`/`NORTHBOUND_FIELDS`/`ANALYST_FIELDS` + `FIELD_FAMILY` 路由（含内部中间列 `short_shares`）
- `get_field_series(symbol, field)` → DatetimeIndex Series：读 `ashare_special_cache` → miss 拉取 → 写回
- `enrich(df, symbol)` → 对齐注入：日频字段（margin）精确 reindex；低频字段（holder/northbound/analyst）披露日 ffill（防未来函数）；派生 `short_balance = short_shares × close`（沪市无融券余额列，沪深同口径）
- 拉取器：`_fetch_margin`（AKShare 沪深逐日，`_extract_margin_row` 列名归一化）/`_fetch_holder`（公告日期为可用日）/`_fetch_northbound`（agent JSON 缓存，无缓存降级空）/`_fetch_analyst`（研报解析）

### 3.2 缓存表 `ashare_special_cache`

migrate.py 注册（与 edb_cache 同构，symbol 维度替代 indicator）：

```sql
CREATE TABLE IF NOT EXISTS ashare_special_cache (
    symbol VARCHAR NOT NULL, date DATE NOT NULL, field VARCHAR NOT NULL,
    value DOUBLE, source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR,
    PRIMARY KEY (symbol, date, field)
)
```

### 3.3 回填脚本 `scripts/backfill_ashare_special.py`

- margin：按日期拉 sse/szse 全市场 → 批量写目标股票（~days×2 请求覆盖全部目标股）
- holder/analyst：按股票拉取
- northbound：从 agent 预填充 JSON 导入（无缓存跳过登记受限）
- CLI：`--symbols/--families/--days/--dry-run/--json`，trace_id 贯穿

### 3.4 接线

- `FundamentalProvider` 新增 `ashare_special_enabled`/`ashare_special_source` 参数，`enrich_ohlcv` 尾部追加注入（独立于基本面路径，失败不阻断）
- `get_fundamental_provider` 读 env `FTS_ASHARE_SPECIAL_ENABLED`（默认关闭，回填后置 1）
- 种子 YAML 无需修改：字段按列名注入匹配；无源字段（northbound_turnover/total_turnover/top10_holder_pct/institution_hold_pct/analyst_up_count/down_count/analyst_buy_ratio/analyst_eps_std/analyst_eps_actual/analyst_target_price）对应因子天然 field_check 跳过

## 4. 字段可用性矩阵（种子 YAML）

| 字段 | 数据源 | 频率 | 可用性 |
|:--|:--|:--|:--|
| margin_balance/margin_buy/margin_sell/short_balance/short_sell/short_cover/margin_net_buy/margin_short_balance | AKShare 两融 | 日频 | ✅ |
| holder_count | AKShare gdhs | 季度 | ✅ |
| northbound_hold_pct/northbound_flow | westock agent 缓存 | 季度 | ⚠️ 需 agent 预填充 |
| analyst_total_count/analyst_eps_consensus | AKShare 研报 | 快照 | ✅ |
| northbound_turnover/total_turnover/top10_holder_pct/institution_hold_pct/analyst_up_count/analyst_down_count/analyst_buy_ratio/analyst_eps_std/analyst_eps_actual/analyst_target_price | 无真实源 | — | ❌ 因子保持 field_check 跳过 |

## 5. 测试

- `tests/data_sources/test_ashare_special_source.py` 26 用例：辅助函数/沪深列名归一化/时序对齐（日频精确 + 低频 ffill 防未来函数）/缓存 miss 拉取写回/降级/股东户数/北向 agent 缓存/研报解析/enrich short_balance 派生/FundamentalProvider 接线/migrate 建表
- `test_migrate.py` tables_created 7→8
- 回归：test_data_sources 494 passed（4 个 tick_cache 既有失败除外）+ test_data 137 + test_data_fundamental 全绿

## 6. 实施记录

- 2026-08-12：数据源可行性实测 → 新建 source/回填脚本/migrate 表 → FundamentalProvider 接线 → 26 测试全绿 → 文档同步
- **遗留**：northbound 仅季度频率（agent 预填充）；analyst up/down 精确计数无源；FTS_ASHARE_SPECIAL_ENABLED 开启后待真实回填数据验证因子晋升

## 一致性元数据

| 字段 | 值 |
|:--|:--|
| 代码→文档映射 | `fts/data_sources/ashare_special_source.py`（AshareSpecialSource/FIELD_FAMILY/ALL_ASHARE_SPECIAL_FIELDS）· `fts/data_sources/migrate.py`（ASHARE_SPECIAL_CACHE_DDL）· `fts/data_fundamental.py`（ashare_special_enabled 接线）· `scripts/backfill_ashare_special.py` · `tests/data_sources/test_ashare_special_source.py` |
| 可验证断言 | GAP-081 登记状态 ✅ 已关闭；`migrate_schema` 新建表含 ashare_special_cache；`FundamentalProvider` 支持 ashare_special_enabled 参数；回填脚本 CLI 可运行（--dry-run 验证） |
| 检验方式 | `pytest tests/data_sources/test_ashare_special_source.py -v`（26 passed）；`python scripts/backfill_ashare_special.py --families northbound --dry-run`；`pytest tests/data_sources/test_migrate.py -v` |
