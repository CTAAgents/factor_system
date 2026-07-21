# Data-Core 数据字段字典

> 版本: v1.0.0
> 适用项目: Data-Core
> 维护: Data-Core Team
> 状态: 已对齐 FTS / FDT 消费需求
> 临时位置: factor_system/docs/pending_for_datacore/DATA_DICTIONARY.md（待用户拷贝至 d:\Programs\data-core\docs\）

本文档汇总 Data-Core 当前可对外提供的数据字段，是 FTS / FDT 等下游消费方对接数据源的**唯一权威参考**。

---

## 0. 目录

1. [载荷信封（DataPayload）](#1-载荷信封datapayload)
2. [数据类型枚举（DataType / MarketType / SourceGrade）](#2-数据类型枚举)
3. [OHLCV 行情数据](#3-ohlcv-行情数据)
4. [期货专用数据](#4-期货专用数据)
5. [宏观数据](#5-宏观数据)
6. [基本面数据](#6-基本面数据)
7. [情绪/新闻数据](#7-情绪新闻数据)
8. [市场制度数据](#8-市场制度数据)
9. [字段命名与单位规范](#9-字段命名与单位规范)

---

## 1. 载荷信封（DataPayload）

所有 Data-Core 返回数据均以 `DataPayload` 信封包装，是数据消费的统一入口。

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 品种代码（A股 6 位、期货 2~3 位大写、宏观为空） |
| data_type | DataType | 数据类型（见第 2 节枚举） |
| market | MarketType | 市场类型（futures / stock / etf / cb / reit） |
| data | Any | 实际数据对象（KlineData / QuoteData / BasisData / ...） |
| source | str | 数据来源（eastmoney / sina / tqsdk / llm / rule ...） |
| grade | SourceGrade | 数据质量等级（primary / daily / cached / stale / unavailable） |
| collected_at | float | 数据采集时间戳（Unix 时间，秒） |
| meta | dict | 元数据（如复权方式、原始字段映射） |
| errors | list[str] | 采集错误列表 |
| warnings | list[str] | 采集警告列表 |

**消费方判断逻辑**：
- `grade == SourceGrade.UNAVAILABLE` 或 `data is None` → 视为无数据
- `errors` 非空 → 视为降级数据（按 stale 处理）
- `warnings` 非空 → 视为可用但需要提示

---

## 2. 数据类型枚举

### DataType（数据类型）

| 枚举值 | data_type | 适用市场 | 数据形态 |
|--------|-----------|----------|----------|
| `OHLCV` | `"ohlcv"` | 全市场 | KlineData |
| `QUOTE` | `"quote"` | 全市场 | QuoteData |
| `TECHNICAL` | `"technical"` | 全市场 | 指标 DataFrame |
| `FINANCIAL` | `"financial"` | A股 / ETF | 财务报表 |
| `FUNDAMENTAL` | `"fundamental"` | A股 | FundamentalSummary |
| `MACRO` | `"macro"` | 全市场 | MacroData |
| `NEWS` | `"news"` | 全市场 | NewsData |
| `ANNOUNCEMENT` | `"announcement"` | A股 | 公告列表 |
| `SENTIMENT` | `"sentiment"` | 全市场 | SentimentData |
| `MARKET_STATE` | `"market_state"` | 全市场 | MarketStateData |
| `FUTURES_CONTRACT_CHAIN` | `"futures_contract_chain"` | 期货 | ContractChain |
| `FUTURES_TERM_STRUCTURE` | `"futures_term_structure"` | 期货 | TermStructure |
| `FUTURES_SPREAD` | `"futures_spread"` | 期货 | SpreadData |
| `FUTURES_BASIS` | `"futures_basis"` | 期货 | BasisData |
| `FUTURES_POSITION` | `"futures_position"` | 期货 | PositionRankData |
| `FUTURES_WAREHOUSE_RECEIPT` | `"futures_warehouse_receipt"` | 期货 | WarehouseReceiptData |
| `ETF_NAV` | `"etf_nav"` | ETF | ETF NAV |
| `ETF_PREMIUM` | `"etf_premium"` | ETF | ETF 折溢价 |
| `ETF_FUND_FLOW` | `"etf_fund_flow"` | ETF | ETF 资金流 |
| `CB_CONVERSION` | `"cb_conversion"` | 可转债 | 转股价值 |
| `CB_TERMS` | `"cb_terms"` | 可转债 | 条款 |
| `CB_PURE_BOND` | `"cb_pure_bond"` | 可转债 | 纯债价值 |
| `F10_REPORT` | `"f10_report"` | A股 | F10 聚合报告 |

### MarketType（市场类型）

| 枚举值 | 说明 |
|--------|------|
| `FUTURES` | 期货（中金所/上期/大商/郑商） |
| `STOCK` | 股票（A 股沪深京） |
| `ETF` | ETF 基金 |
| `CB` | 可转换债券 |
| `REIT` | 公募 REITs |

### SourceGrade（数据质量等级）

| 枚举值 | 等级排序 | 说明 |
|--------|----------|------|
| `PRIMARY` | 1（最高） | 实时原始数据，未经任何加工 |
| `DAILY` | 2 | 日频收盘后数据 |
| `CACHED` | 3 | 缓存数据（可能略有延迟） |
| `STALE` | 4 | 过期数据（>1 周期未更新） |
| `UNAVAILABLE` | 5（最低） | 数据不可用 |

---

## 3. OHLCV 行情数据

### 3.1 KBar（单根 K 线）

定义位置: `datacore/models/ohlcv.py`

| 字段 | 类型 | 默认值 | 单位 | 说明 |
|------|------|--------|------|------|
| `date` | str | — | — | K 线日期，格式 `YYYY-MM-DD` |
| `open` | float | — | 元 | 开盘价 |
| `high` | float | — | 元 | 最高价 |
| `low` | float | — | 元 | 最低价 |
| `close` | float | — | 元 | 收盘价 |
| `volume` | float | 0.0 | 股 / 手 | 成交量（A股=股，期货=手） |
| `amount` | float | 0.0 | 元 | 成交额 |
| `open_interest` | float | 0.0 | 手 | 持仓量（期货专用，A股=0） |
| `settlement` | float | 0.0 | 元 | 结算价（期货专用，A股=0） |

### 3.2 KlineData（K 线数据集）

| 字段 | 类型 | 说明 |
|------|------|------|
| `symbol` | str | 品种代码 |
| `period` | str | 周期（`1m` / `5m` / `15m` / `30m` / `1h` / `1d` / `1w` / `1M`） |
| `bars` | list[KBar] | K 线列表（按时间升序） |
| `source` | str | 数据来源 |
| `contract` | str | 期货合约代码（主力合约；A股为空） |

### 3.3 QuoteData（实时行情快照）

| 字段 | 类型 | 默认值 | 单位 | 说明 |
|------|------|--------|------|------|
| `symbol` | str | — | — | 品种代码 |
| `source` | str | "" | — | 数据来源 |
| `last_price` | float | None | 元 | 最新成交价 |
| `open` | float | None | 元 | 今日开盘价 |
| `high` | float | None | 元 | 今日最高价 |
| `low` | float | None | 元 | 今日最低价 |
| `pre_close` | float | None | 元 | 昨收价 |
| `volume` | float | None | 股/手 | 累计成交量 |
| `amount` | float | None | 元 | 累计成交额 |
| `bid_price` | list[float] | [] | 元 | 五档买价 |
| `ask_price` | list[float] | [] | 元 | 五档卖价 |
| `change_pct` | float | None | % | 涨跌幅（昨收为基准） |
| `update_time` | str | None | — | 数据更新时间 |
| `collected_at` | float | time.time() | 秒 | 采集时间戳 |

**方法**：
- `to_dict()` → 返回 `dict`（含 symbol / source / last_price / open / high / low / pre_close / volume / collected_at），与 FDT `QuoteData.to_dict()` 格式一致。

---

## 4. 期货专用数据

定义位置: `datacore/models/futures.py`

### 4.1 ContractInfo（合约基本信息）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `code` | str | — | 合约代码（如 `RB2501`） |
| `month` | str | — | 交割月份（如 `2501`） |
| `is_main` | bool | False | 是否主力合约 |
| `open_interest` | float | 0.0 | 持仓量 |
| `last_price` | float | 0.0 | 最新价 |

### 4.2 ContractChain（合约链）

| 字段 | 类型 | 说明 |
|------|------|------|
| `symbol` | str | 品种代码 |
| `contracts` | list[str] | 合约代码列表（按持仓量降序） |
| `klines` | dict[str, KlineData] | 各合约 K 线数据（key=合约代码） |

**属性**：
- `main_contract` → str | None：主力合约代码

### 4.3 TermStructurePoint（期限结构点）

| 字段 | 类型 | 说明 |
|------|------|------|
| `contract` | str | 合约代码 |
| `month` | str | 月份（如 `2501`） |
| `price` | float | 价格 |
| `yield_from_front` | float | 近月收益率（(本点-近月)/近月） |
| `yield_annual` | float | 年化收益率 |

### 4.4 TermStructure（期限结构）

| 字段 | 类型 | 说明 |
|------|------|------|
| `symbol` | str | 品种代码 |
| `points` | list[TermStructurePoint] | 期限结构点列表（按月份升序） |
| `snapshot_at` | float | 快照时间戳 |

**属性**：
- `is_contango` → bool：升水结构（最远月 > 最近月）
- `slope` → float：期限斜率（最远-最近）/最近

### 4.5 SpreadData（跨期价差）

| 字段 | 类型 | 说明 |
|------|------|------|
| `symbol` | str | 品种代码 |
| `near_contract` | str | 近月合约代码 |
| `far_contract` | str | 远月合约代码 |
| `spread_series` | list[dict] | 价差序列（每条含 `date` / `near_price` / `far_price` / `spread`） |

**属性**：
- `latest_spread` → float：最新价差

### 4.6 BasisData（基差数据）

| 字段 | 类型 | 默认值 | 单位 | 说明 |
|------|------|--------|------|------|
| `symbol` | str | — | — | 品种代码 |
| `spot_price` | float | 0.0 | 元 | 现货价格 |
| `futures_price` | float | 0.0 | 元 | 期货价格 |
| `basis` | float | 0.0 | 元 | 基差（= 现货 - 期货） |
| `basis_rate` | float | 0.0 | % | 基差率（= basis / 现货） |
| `basis_pct` | float | 0.0 | % | 基差百分比（= basis / 期货 × 100） |
| `spot_source` | str | "" | — | 现货来源 |
| `futures_source` | str | "" | — | 期货来源 |

### 4.7 PositionRankItem（持仓排名条目）

| 字段 | 类型 | 说明 |
|------|------|------|
| `rank` | int | 排名（1=第1名） |
| `broker` | str | 会员名称（如"中信期货"） |
| `volume` | float | 持仓量 |
| `volume_change` | float | 持仓变化量 |
| `direction` | str | 方向（`long` / `short`） |

### 4.8 PositionRankData（持仓排名数据）

| 字段 | 类型 | 说明 |
|------|------|------|
| `symbol` | str | 品种代码 |
| `contract` | str | 合约代码 |
| `date` | str | 数据日期 |
| `long_ranks` | list[PositionRankItem] | 多头持仓排名（前 20） |
| `short_ranks` | list[PositionRankItem] | 空头持仓排名（前 20） |
| `volume_ranks` | list[PositionRankItem] | 成交量排名（前 20） |

### 4.9 WarehouseReceiptData（仓单数据）

| 字段 | 类型 | 默认值 | 单位 | 说明 |
|------|------|--------|------|------|
| `symbol` | str | — | — | 品种代码 |
| `date` | str | — | — | 数据日期 |
| `total_receipts` | float | 0.0 | 张 | 仓单总量 |
| `change` | float | 0.0 | 张 | 仓单变化量（今日 - 昨日） |
| `inventory_pct` | float | 0.0 | 0~1 | 库存分位（历史分位数） |
| `warehouse_detail` | list[dict] | [] | — | 仓库明细（每条含 `warehouse` / `receipts` / `change`） |

---

## 5. 宏观数据

定义位置: `datacore/macro/models.py`

### 5.1 MacroIndicator（宏观指标数据点）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `indicator` | str | — | 指标代码（如 `gdp_yoy` / `cpi_yoy` / `pmi` / `m2_yoy`） |
| `period` | str | — | 统计周期（如 `2024Q1` / `2024-01`） |
| `value` | float | — | 当前值 |
| `prev_value` | float | 0.0 | 前一期值 |
| `yoy` | float | 0.0 | 同比（%） |
| `mom` | float | 0.0 | 环比（%） |
| `source` | str | "" | 数据来源（国家统计局 / 央行 / 财新 / ...） |
| `unit` | str | "" | 单位（如 `%` / `亿元` / `点`） |

### 5.2 MacroData（宏观数据集）

| 字段 | 类型 | 说明 |
|------|------|------|
| `indicator` | str | 指标代码 |
| `total` | int | 数据条数 |
| `data` | list[MacroIndicator] | 数据点列表（按 period 倒序） |

**方法**：
- `latest()` → MacroIndicator | None：最新一期数据

**已支持的指标代码**（节选）：

| indicator | 说明 | 单位 | 频率 |
|-----------|------|------|------|
| `gdp_yoy` | GDP 同比增速 | % | 季度 |
| `cpi_yoy` | CPI 同比 | % | 月 |
| `ppi_yoy` | PPI 同比 | % | 月 |
| `pmi` | 制造业 PMI | 点 | 月 |
| `m2_yoy` | M2 同比 | % | 月 |
| `lpr_1y` | LPR 1 年期 | % | 月 |
| `lpr_5y` | LPR 5 年期 | % | 月 |
| `social_finance` | 社会融资规模 | 亿元 | 月 |
| `retail_sales_yoy` | 社零同比 | % | 月 |
| `industrial_yoy` | 工业增加值同比 | % | 月 |

---

## 6. 基本面数据

定义位置: `datacore/processing/fundamental/models.py`

### 6.1 ReportSummary（研报摘要）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `title` | str | "" | 研报标题 |
| `symbol` | str | "" | 品种代码 |
| `direction` | str | "" | 方向（`看多` / `看空` / `中性`） |
| `strength` | str | "" | 强度（`强烈` / `一般` / `谨慎`） |
| `time_horizon` | str | "" | 时间维度（`短期` / `中期` / `长期`） |
| `key_points` | list[str] | [] | 核心观点列表 |
| `risk_factors` | list[str] | [] | 风险因素列表 |
| `source` | str | "" | 研报来源（券商/网站） |
| `published_at` | str | "" | 发布时间 |

### 6.2 EarningSummary（财报摘要）

| 字段 | 类型 | 默认值 | 单位 | 说明 |
|------|------|--------|------|------|
| `symbol` | str | "" | — | 品种代码 |
| `period` | str | "" | — | 报告期（如 `2024Q3`） |
| `revenue` | float | None | 元 | 营业收入 |
| `revenue_yoy` | float | None | % | 营收同比 |
| `revenue_qoq` | float | None | % | 营收环比 |
| `profit` | float | None | 元 | 净利润 |
| `profit_yoy` | float | None | % | 净利润同比 |
| `profit_qoq` | float | None | % | 净利润环比 |
| `roe` | float | None | % | 净资产收益率 |
| `cash_flow` | float | None | 元 | 经营性现金流 |
| `source` | str | "" | — | 数据来源 |
| `summary` | str | "" | — | 摘要文本 |

### 6.3 FundamentalSummary（综合基本面摘要）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `symbol` | str | "" | 品种代码 |
| `reports` | list[ReportSummary] | [] | 研报列表 |
| `earnings` | list[EarningSummary] | [] | 财报列表 |
| `composite_score` | float | 0.0 | 综合评分（-1.0 ~ +1.0） |
| `confidence` | float | 0.0 | 置信度（0.0 ~ 1.0） |
| `source` | str | "llm" | 来源（`llm` / `rule`） |
| `collected_at` | float | 0.0 | 采集时间戳 |

---

## 7. 情绪/新闻数据

### 7.1 NewsItem（单条新闻）

定义位置: `datacore/news/models.py`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `title` | str | — | 新闻标题 |
| `content` | str | "" | 新闻正文 |
| `published_at` | str | "" | 发布时间（`YYYY-MM-DD HH:MM:SS`） |
| `source` | str | "" | 来源（财联社 / 新浪财经 / ...） |
| `url` | str | "" | 原文链接 |
| `tags` | list[str] | [] | 分类标签（如 `业绩` / `并购` / `宏观`） |
| `related_symbols` | list[str] | [] | 关联品种代码列表 |
| `summary` | str | "" | 摘要 |

### 7.2 NewsData（新闻数据集）

| 字段 | 类型 | 说明 |
|------|------|------|
| `symbol` | str | None | 品种代码（None 表示全市场） |
| `total` | int | 新闻总条数 |
| `items` | list[NewsItem] | 新闻列表 |

**方法**：
- `filter_by_tag(tag: str) -> list[NewsItem]`：按标签过滤
- `filter_by_symbol(symbol: str) -> list[NewsItem]`：按品种过滤

### 7.3 SentimentItem（情绪打分条目）

定义位置: `datacore/processing/models.py`

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `text` | str | "" | 原始文本（标题+摘要） |
| `score` | float | 0.0 | 情绪分数（-1.0 ~ +1.0） |
| `confidence` | float | 0.0 | 置信度（0.0 ~ 1.0） |
| `source` | str | "" | 打分来源（`llm` / `rule`） |
| `symbol` | str | "" | 关联品种 |
| `tags` | list[str] | [] | 新闻分类标签 |
| `published_at` | float | 0.0 | 发布时间戳 |
| `collected_at` | float | 0.0 | 打分时间戳 |

### 7.4 SentimentData（品种情绪聚合）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `symbol` | str | "" | 品种代码 |
| `items` | list[SentimentItem] | [] | 情绪条目列表 |
| `daily` | dict[str, dict] | {} | 按日期聚合 `{date_str: {score, volume, topics}}` |
| `overall_score` | float | 0.0 | 加权平均情绪分数 |
| `total_volume` | int | 0 | 新闻总条数 |
| `topics` | list[str] | [] | 涉及的主题 |

**方法**：
- `add_item(item: SentimentItem)`：添加一条情绪数据

---

## 8. 市场制度数据

### 8.1 MarketRegime（市场制度枚举）

| 枚举值 | 值 | 说明 |
|--------|-----|------|
| `BULL` | `"bull"` | 牛市：上升趋势 |
| `BEAR` | `"bear"` | 熊市：下降趋势 |
| `SIDEWAYS` | `"sideways"` | 震荡：无明显趋势 |
| `UNKNOWN` | `"unknown"` | 未知/数据不足 |

### 8.2 MarketStateData（市场制度状态数据）

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `symbol` | str | "" | 品种代码 |
| `regime` | MarketRegime | UNKNOWN | 市场制度 |
| `confidence` | float | 0.0 | 置信度（0.0 ~ 1.0） |
| `trend_strength` | float | 0.0 | 趋势强度 |
| `volatility` | float | 0.0 | 波动率 |
| `volume_trend` | float | 0.0 | 成交量趋势 |
| `features` | dict | {} | 原始特征字典（用于扩展） |
| `collected_at` | float | 0.0 | 采集时间戳 |

**属性**：
- `is_bull` → bool
- `is_bear` → bool
- `is_sideways` → bool

---

## 9. 字段命名与单位规范

### 9.1 命名约定

| 类别 | 命名规则 | 示例 |
|------|----------|------|
| 价格字段 | snake_case，英文单词 | `close` / `pre_close` / `last_price` |
| 百分比字段 | 后缀 `_pct` 或 `_yoy` / `_qoq` / `_mom` | `basis_pct` / `revenue_yoy` |
| 比率字段 | 后缀 `_ratio` | `vol_ratio` / `top5_ratio` |
| 排名字段 | 后缀 `_rank` | `rank` / `volume_rank` |
| 时间戳字段 | 后缀 `_at`（Unix 秒） 或 `_time`（字符串） | `collected_at` / `update_time` |
| 字符串枚举 | 小写 snake_case | `bull` / `bear` / `sideways` |

### 9.2 单位约定

| 字段类型 | 单位 | 示例 |
|----------|------|------|
| 股票价格 | 元 | 600519.SH close=1680.50 |
| 期货价格 | 元 | RB2501 close=3500 |
| 股票成交量 | 股 | 600519.SH volume=10_000_000 |
| 期货成交量 | 手 | RB2501 volume=120_000 |
| 成交额 | 元 | amount=500_000_000 |
| 持仓量 | 手 | open_interest=200_000 |
| 涨跌幅 | %（已是数值，不带 % 号） | change_pct=2.5 表示 2.5% |
| 基差率 | % | basis_pct=0.5 表示 0.5% |
| 同比/环比 | % | revenue_yoy=10.5 |
| 库存分位 | 0~1 数值 | inventory_pct=0.3 表示 30% 分位 |
| 情绪分数 | -1.0 ~ +1.0 | score=0.5 |
| 置信度 | 0.0 ~ 1.0 | confidence=0.85 |

### 9.3 时间约定

| 字段 | 格式 | 说明 |
|------|------|------|
| `date` | `YYYY-MM-DD` | 纯日期 |
| `period` | `YYYY-MM` / `YYYYQn` / `YYYYMMDD` | 周期 |
| `published_at` | `YYYY-MM-DD HH:MM:SS` | 字符串时间 |
| `collected_at` / `update_time` / `snapshot_at` | Unix 秒（float） | 时间戳 |
| `bars[].date` | `YYYY-MM-DD` | K 线日期 |

### 9.4 缺失值约定

- 数值字段：None 表示"未知/未采集"，0.0 表示"值为零"
- 字符串字段：空串 `""` 表示"未知/未采集"
- 列表字段：空列表 `[]` 表示"无数据"
- 字典字段：空字典 `{}` 表示"无数据"

**消费方判断**：
- 优先判断 `payload.grade == SourceGrade.UNAVAILABLE`
- 次判断 `payload.data is None`
- 单字段判断 `field is None` 或 `field == 0.0`（根据业务含义区分）

---

## 附录 A：FTS 消费字段快速索引

| FTS 消费字段 | Data-Core 字段 | 来源 |
|--------------|----------------|------|
| `close` | `KBar.close` | OHLCV |
| `volume` | `KBar.volume` | OHLCV |
| `open_interest` | `KBar.open_interest` | OHLCV |
| `high` / `low` / `open` | `KBar.high/low/open` | OHLCV |
| `amount` | `KBar.amount` | OHLCV |
| `settlement` | `KBar.settlement` | OHLCV |
| `basis_pct` | `BasisData.basis_pct` | 期货 |
| `inventory_pct` | `WarehouseReceiptData.inventory_pct` | 期货 |
| `capacity_pct` | （加工字段，需上游 ETL 提供） | — |
| `macro_signal` | `MarketStateData.regime` | 市场制度 |
| `rate_mom` | `MacroIndicator(LPR1Y).mom` | 宏观 |
| `pmi` / `pmi_mom` | `MacroIndicator(PMI)` | 宏观 |
| `top5_ratio` | （加工字段，从 `PositionRankData` 派生） | 期货 |
| `warrant_change_pct` | `WarehouseReceiptData.change / total_receipts` | 期货 |
| `sentiment_score` | `SentimentData.overall_score` | 情绪 |
| `regime` | `MarketStateData.regime` | 市场制度 |

---

## 附录 B：版本与变更

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.0.0 | 2026-07-21 | 初版：与 FTS 消费需求对齐 |

维护：当 Data-Core 新增/废弃字段时，必须同步更新本文档并通知 FTS / FDT。
