# 31. 股票基本面字段数据源接入计划（GAP-082）

> 版本: v2.104.0+16（日常开发追加，不 bump 版本）
> 日期: 2026-08-12
> 状态: ✅ 已实施

## 1. 背景

`fts/data_fundamental.py` `FundamentalProvider`：26 字段定义中仅 8 个
（roe/eps/bps/total_market_cap/revenue_growth/profit_growth/gross_margin/net_margin）
在 CLI prepare 路径（mcp_available=True）有真实缓存（`data/fundamental_cache.json`，
整列常量非时变）；默认 `FTSDataProvider()`（mcp_available=False）走 seed=42 随机
合成——信号管道基本面全为随机值；pe_ttm/pb/ps_ttm/turnover_rate/roa/free_market_cap
连缓存都没有（真实路径也不注入）；11 个死字段（pcf_ttm/circulating_market_cap/
volume_ratio/amplitude/debt_to_equity/current_ratio/asset_growth/gdp_growth/
m2_growth/shibor_1y/lpr_1y）定义但永不注入；Barra 4 风格（book_to_price/
liquidity/earnings_yield/leverage）因缺字段恒 NaN 被跳过（依赖 pb/turnover_rate/
pe_ttm+roe/debt_to_equity 字段，见 `fts/factor_engine/barra/barra_style.py`
STYLE_SPECS）——10 风格中性化仅 6 个真实生效。

## 2. 数据源可行性实测（2026-08-12，akshare 1.18.81）

| 字段族 | 接口 | 频率 | 结论 |
|:--|:--|:--|:--|
| 估值/市值 | AKShare `stock_value_em` | 日频（约 2019-2026 每交易日） | ✅ 可用（pe_ttm←PE(TTM)/pb←市净率/ps_ttm←市销率/pcf_ttm←市现率/总市值/流通市值；接口慢约 1-3s/股） |
| 财务/成长 | AKShare `stock_financial_analysis_indicator` | 季度（86 列） | ✅ 可用（roe/roa/gross_margin/net_margin/debt_to_equity/current_ratio/eps/bps/revenue_growth/profit_growth/asset_growth；百分比字段需 ÷100 与合成值量纲一致；列名以实际返回为准需容错） |

## 3. 设计

### 3.1 新建 `fts/data_sources/stock_fundamental_source.py`

`StockFundamentalSource`：股票基本面字段获取 + 缓存 + 对齐注入（架构完全对照
`ashare_special_source.py`，GAP-081 同类实现）。

- 字段族注册：`VALUATION_FIELDS`（7：pe_ttm/pb/ps_ttm/pcf_ttm/total_market_cap/
  free_market_cap/circulating_market_cap）+ `FINANCIAL_FIELDS`（11：roe/roa/
  gross_margin/net_margin/debt_to_equity/current_ratio/eps/bps/revenue_growth/
  profit_growth/asset_growth）+ `FIELD_FAMILY` 路由（valuation/financial）
- `get_field_series(symbol, field)` → DatetimeIndex Series：读 `stock_fundamental_cache`
  → miss 按族一次性拉取 → 写回
- `enrich(df, symbol)` → 对齐注入：日频字段（valuation）精确 reindex；季度字段
  （financial）报告期日 reindex + ffill（后续交易日沿用最近报告期，无未来函数）；
  派生 `turnover_rate = volume / 流通股本 × 100`（流通股本来自 `_fetch_valuation`
  内部缓存）/ `volume_ratio = volume / 20 日均量` / `amplitude = (high-low)/prev_close`
- 拉取器：`_fetch_valuation`（AKShare stock_value_em，映射列 + 保存流通股本内部缓存）/
  `_fetch_financial`（AKShare stock_financial_analysis_indicator，百分比字段 ÷100、
  原始 NaN 跳过不注入 0、列缺失降级不注入）
- 降级：拉取失败/空数据 → 不注入列（因子走 field_check 跳过），绝不抛异常阻断主流程；
  `_safe_float` 兜底（NaN/None/Inf→0.0）；`write_back` 参数（默认 True；False 用于
  dry-run 只读统计——miss 拉取不写缓存）

### 3.2 缓存表 `stock_fundamental_cache`

migrate.py 注册（与 ashare_special_cache 同构）：

```sql
CREATE TABLE IF NOT EXISTS stock_fundamental_cache (
    symbol VARCHAR NOT NULL, date DATE NOT NULL, field VARCHAR NOT NULL,
    value DOUBLE, source VARCHAR, fetched_at TIMESTAMP, trace_id VARCHAR,
    PRIMARY KEY (symbol, date, field)
)
```

### 3.3 回填脚本 `scripts/backfill_stock_fundamental.py`

- 按股票拉取两族（每族一次请求/股）→ 批量 INSERT OR REPLACE `stock_fundamental_cache`（幂等）
- CLI：`--symbols/--families/--dry-run/--json/--trace-id`，trace_id 贯穿
  （格式 `fts.backfill_stock_fund.{ts}.{hex}`）；`--dry-run` 走 `write_back=False`
  只统计不写库（含 source 内部 miss 拉取写回路径，真正只读）

### 3.4 接线

- `FundamentalProvider` 新增 `stock_fundamental_enabled`/`stock_fundamental_source`
  参数（`enrich_ohlcv` 尾部追加注入，与 ashare 块并列，失败不阻断）
- `get_fundamental_provider` 读 env `FTS_STOCK_FUNDAMENTAL_ENABLED`（默认关闭，
  回填后置 1；与 FTS_ASHARE_SPECIAL_ENABLED 同款）
- 种子 YAML / Barra 无需修改：字段按列名注入匹配；无源字段天然 field_check 跳过；
  Barra 4 风格（book_to_price/liquidity/earnings_yield/leverage）随字段补齐真实生效

## 4. 字段可用性矩阵

| 字段 | 数据源 | 频率 | 可用性 |
|:--|:--|:--|:--|
| pe_ttm/pb/ps_ttm/pcf_ttm | AKShare stock_value_em | 日频 | ✅ |
| total_market_cap/free_market_cap/circulating_market_cap | AKShare stock_value_em | 日频 | ✅ |
| roe/roa/gross_margin/net_margin/debt_to_equity/current_ratio | AKShare 财务分析指标 | 季度 | ✅ |
| eps/bps | AKShare 财务分析指标 | 季度 | ✅ |
| revenue_growth/profit_growth/asset_growth | AKShare 财务分析指标 | 季度 | ✅ |
| turnover_rate（派生） | volume/流通股本 | 日频 | ✅（估值族拉取后可用） |
| volume_ratio/amplitude（派生） | OHLCV 计算 | 日频 | ✅ |
| gdp_growth/m2_growth/shibor_1y/lpr_1y | 无真实源（宏观） | — | ❌ 因子保持 field_check 跳过（GAP-087 关联） |

## 5. 测试

- `tests/data_sources/test_stock_fundamental_source.py` 23 用例：字段族注册/FIELD_FAMILY
  路由、_fetch_valuation 列名映射 + 流通股本缓存、_fetch_financial 列名映射 + 百分比/100
  归一化 + NaN 跳过 + 缺列降级、时序对齐（日频精确 + 季度 ffill 防未来函数）、缓存 miss
  拉取写回、降级（失败/未知字段）、enrich 派生（turnover_rate/volume_ratio/amplitude）、
  FundamentalProvider 接线（enabled 注入/disabled 跳过/异常不阻断）、migrate 建表
- `test_migrate.py` tables_created 8→9（+stock_fundamental_cache）
- 回归：test_migrate 11 + test_data_fundamental 60 + test_data 77 + 新测试 23 = 171 passed

## 6. 实施记录

- 2026-08-12：数据源可行性实测 → 新建 source/回填脚本/migrate 表 → FundamentalProvider
  接线（stock_fundamental_enabled + FTS_STOCK_FUNDAMENTAL_ENABLED）→ 23 测试全绿 +
  回归 171 passed + ruff 全绿 → 文档同步
- **遗留**：真实回填 + FTS_STOCK_FUNDAMENTAL_ENABLED=1 端到端验证待执行；
  turnover_rate 派生公式（volume/流通股本×100）与合成量纲存在口径差异，待真实数据校准；
  宏观死字段（gdp_growth/m2_growth/shibor_1y/lpr_1y）仍无源（GAP-087 关联）

## 一致性元数据

| 字段 | 值 |
|:--|:--|
| 代码→文档映射 | `fts/data_sources/stock_fundamental_source.py`（StockFundamentalSource/FIELD_FAMILY/VALUATION_FIELDS/FINANCIAL_FIELDS）· `fts/data_sources/migrate.py`（STOCK_FUNDAMENTAL_CACHE_DDL）· `fts/data_fundamental.py`（stock_fundamental_enabled 接线）· `scripts/backfill_stock_fundamental.py` · `tests/data_sources/test_stock_fundamental_source.py` |
| 可验证断言 | GAP-082 登记状态 ✅ 已关闭；`migrate_schema` 新建表含 stock_fundamental_cache；`FundamentalProvider` 支持 stock_fundamental_enabled 参数；回填脚本 CLI 可运行（--dry-run 验证） |
| 检验方式 | `pytest tests/data_sources/test_stock_fundamental_source.py -v`（23 passed）；`python scripts/backfill_stock_fundamental.py --dry-run`；`pytest tests/data_sources/test_migrate.py -v` |
