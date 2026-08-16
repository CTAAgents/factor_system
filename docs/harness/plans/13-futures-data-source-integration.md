# 期货数据源集成实施计划（TQ + iFinD + Wind MCP）

> 版本: v2.104.0+100
> 最后更新: 2026-08-05
> 状态: Phase 14.0–14.5 全部完成，详见 [acceptance/v2.3.0/14.5-observability-acceptance.md](acceptance/v2.3.0/14.5-observability-acceptance.md)
> 适用范围: FTS 期货数据层 (`fts/data_futures.py` + 衍生模块)

---

## 0. 背景与目标

### 0.1 现状

FTS 当前期货数据来源单一，仅依赖 **AKShare `futures_zh_daily_sina`** 接口经 `scripts/download_futures.py` 落盘至 `data/fts_history.duckdb` 的 `kline_cache` 表。`FuturesDataProvider` 在 DuckDB 缺数据时降级到 AKShare 即时拉取，失败时再降级到合成数据。

局限：

- **数据字段不全**：AKShare 主连合约缺日内分钟 K、缺 tick 级盘口、缺期权 PCR/IV、缺境外联动指标。
- **数据稳定性差**：AKShare 新浪接口限流严，单次全量 76 品种重拉易中断。
- **基本面缺失**：库存/仓单靠 AKShare 抓取（已实现 `data_futures_fundamental.py`），但缺跨市场交叉验证。
- **可观测性弱**：现有管线缺少"哪个数据源贡献了某行 OHLCV" 的来源追溯。

### 0.2 目标

构建一个**多源聚合**的期货数据层，融合三家数据源的能力：

| 数据源 | 主要价值 |
|:-------|:---------|
| **通达信 TQ**（TQ-Local + TQ-Python） | 本地客户端零延迟 K 线、分钟/快照、合约基础信息、行情订阅 |
| **同花顺 iFinD** | 宏观/商品指数/产业链（EDB）、跨市场板块（指数/基金） |
| **万得 Wind AIFin** | 期货主力合约全字段（含结算价、持仓量、基差），支持分钟/日多周期 |

目标产出：

1. **统一适配器接口** `BaseFuturesSource` — 屏蔽三家接口差异。
2. **数据源优先级调度器** `FuturesDataAggregator` — K 线主路径 `本地缓存 → TQ-Local → TQ-Python → AKShare`，Wind/iFinD 仅作字段增强（settle/oi_change/EDB/期权）。
3. **DuckDB 表结构扩展** — 增加真实 `hold/settle/pre_settle/oi_change` 等期货专属字段。
4. **数据血缘追溯** — 每行 OHLCV 记录 `source` 字段，记录来源 API、trace_id、获取时间。
5. **CLI/调度接入** — 新增 `fts data sync-futures` 命令，L1/L3 调度中调用。

### 0.3 不在范围

- A 股 / ETF 数据（仍由 `data_mcp.py` 处理）。
- 实盘下单接口（仅做行情聚合，不做交易）。
- 境外期货（NQ/ES 等），三家虽可覆盖但本期先聚焦境内 82 品种。

---

## 1. 三方数据源能力矩阵

### 1.1 数据维度对照

| 数据维度 | 通达信 TQ | 同花顺 iFinD | 万得 Wind MCP | 现有 AKShare |
|:---------|:---------|:------------|:-------------|:-------------|
| **连续合约 K 线（日/周/月）** | ✅ `get_kline` | ✅ `bond_market_data` 期货字段 | ✅ `mx_*_finance_data` | ✅ `futures_zh_daily_sina` |
| **具体合约 K 线** | ✅ `get_kline` 含 `合约.SHFE/RB2509` | ⚠️ 需合约代码 | ✅ `stock_data` + 期货字段 | ✅ `futures_zh_daily_sina` |
| **分钟 K 线** | ✅ `get_minute_kline` 1/5/15/30/60 | ⚠️ 部分品种 | ✅ `mx_index_block_finance_data` 期货分时 | ❌ 主连不提供 |
| **实时快照** | ✅ `get_quote` / `get_full_tick` | ✅ `bond_market_data` 实时 | ✅ `stock_data` 实时 | ✅ `futures_zh_realtime` |
| **结算价（settle）** | ✅ K 线含 settle | ✅ 含 settle 字段 | ✅ 含 settle | ✅ `futures_zh_daily_sina` |
| **持仓量（hold/OI）** | ✅ K 线含 hold | ✅ 期货 OI 字段 | ✅ `open_interest` 字段 | ✅ AKShare 主连含 hold |
| **日增仓 / OI 变化** | ⚠️ 需 diff 计算 | ✅ 有现成字段 | ✅ `oi_change` | ❌ 无 |
| **基差（spot-future）** | ❌ 需 spot 数据 | ✅ 部分品种有现货报价 | ✅ `basis` 字段 | ⚠️ `futures_fundamental` |
| **库存/仓单** | ❌ | ✅ `edb` 板块 | ✅ `data_industry_chain` | ✅ `futures_inventory` |
| **期权 PCR/IV/skew** | ✅ 期权 K 线 + T 型报价 | ⚠️ 部分 | ✅ `data_option` | ❌ |
| **产业链上游/下游** | ❌ | ✅ `index_data` 板块 | ✅ `data_industry_chain` | ⚠️ `futures_fundamental` |
| **宏观/EDB** | ❌ | ✅ `edb` 完整接口 | ✅ `mx_macro_data` | ❌ |
| **实时 tick 推送** | ✅ TQ 订阅 | ❌ | ❌ | ❌ |
| **境外联动（美元/CRB）** | ❌ | ✅ EDB | ✅ `mx_macro_data` | ❌ |

> 结论：三家在 K 线/快照层面**互补**（TQ 最快、Wind 最全、iFinD 宏观最强），适合做多源融合 + 交叉验证。

### 1.2 代码格式与限额

| 源 | 代码格式 | 限额 | 鉴权 |
|:--|:---------|:-----|:-----|
| TQ | `RB2509.SHFE` / `RB0.SHFE`（连续） / `RB` | 本地无限额，需客户端登录 | 客户端账户 |
| iFinD | `RB2509.SHF`（注意 SFE 变 SHF） / `RB0` | 按 API 配额 | API Key + 密码 |
| Wind | `RB2509.SHFE` / `RB0.SHFE` | 终端共享配额 | 终端登录 |

### 1.3 接入形态

- **TQ**：本地 HTTP JSON-RPC（`http://127.0.0.1:7721`）+ TQ-Python SDK。
- **iFinD**：`run_mcp` 调用 `mcp_plugin_iFinD_hexin-ifind-ds-bond-mcp/bond_market_data`（期货走 `bond_*` 服务，但工具集是 `stock_market_data` 别名，详见 1.4）。
- **Wind**：`run_mcp` 调用 `mcp_plugin_full-link-stock-analysis_mx-ds-mcp/mx_comprehensive_finance_data` 与 `mx_ashare_finance_data`（Wind MCP 没有独立期货服务，但 `mx_*_finance_data` 支持期货代码）。

### 1.4 重要细节

- iFinD 的 MCP 工具命名是 `mcp_plugin_iFinD_hexin-ifind-ds-stock-mcp` 提供 `stock_highfreq_quotes`、`get_stock_financials` 等"股票工具"，但通过 `wind_code` 字段可传入期货代码。Wind MCP 类似，需以"股票/指数/商品" 工具承载期货数据。
- TQ-Local 适合做"行情实时层"，TQ-Python 适合做"批处理下载"。
- iFinD 的 `edb` 服务是宏观/产业链的核心入口，必须单独建模。

---

## 2. 架构设计

### 2.1 抽象分层

```
┌──────────────────────────────────────────────────────────────────────────┐
│                       FTS 因子引擎 / 信号管道                              │
│              (data_futures.get_ohlcv() 接口签名保持不变)                  │
└─────────────────────────────┬────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│           FuturesDataAggregator（新）  — 数据源调度                       │
│  - 优先级排序：DuckDB 缓存 → TQ-Local → TQ-Python → AKShare（K 线主路径）   │
│  - 字段增强层：Wind MCP（settle/oi_change/期权） + iFinD MCP（EDB/产业链）    │
│  - 多源交叉验证（同日同合约多源比对，差异 > 阈值告警）                       │
│  - 失败熔断（连续 N 次失败降级到合成数据）                                  │
│  - 数据血缘（lineage）：每行 OHLCV 记录 source + trace_id                 │
└────────────┬─────────────┬─────────────┬─────────────────────────────────┘
             │             │             │
             ▼             ▼             ▼
    ┌─────────────┐ ┌─────────────┐ ┌─────────────┐
    │ TQ-Local    │ │ Wind MCP    │ │ iFinD MCP   │
    │ Adapter     │ │ Adapter     │ │ Adapter     │
    │ (新)        │ │ (新)        │ │ (新)        │
    │ HTTP/JSON   │ │ MCP         │ │ MCP         │
    │ + TQ-Python │ │             │ │             │
    └─────────────┘ └─────────────┘ └─────────────┘
             │             │             │
             ▼             ▼             ▼
    ┌──────────────────────────────────────────┐
    │  DuckDB kline_cache (data/fts_history)   │
    │  + 新增 hold_real / settle_real /        │
    │    oi_change / source / fetched_at        │
    │  + 新增 edb_cache (产业链/库存)            │
    │  + 新增 option_chain (期权)                │
    └──────────────────────────────────────────┘
```

### 2.2 模块清单

| 新增文件 | 职责 |
|:---------|:-----|
| `fts/data_sources/__init__.py` | 子包入口 |
| `fts/data_sources/base.py` | `BaseFuturesSource` 抽象基类 + `FuturesOHLCV` TypedDict |
| `fts/data_sources/tq_source.py` | TQ-Local + TQ-Python 适配器 |
| `fts/data_sources/wind_source.py` | Wind MCP 适配器 |
| `fts/data_sources/ifind_source.py` | iFinD MCP 适配器（期货 + EDB） |
| `fts/data_sources/aggregator.py` | `FuturesDataAggregator` 多源调度 |
| `fts/data_sources/lineage.py` | 数据血缘记录（trace_id + source） |
| `fts/data_sources/edb_source.py` | iFinD EDB 宏观/产业链独立适配器 |
| `fts/data_sources/migrate.py` | 一次性数据迁移脚本（AKShare → 新 schema） |
| `tests/data_sources/` | 各适配器单元测试 + 集成测试 |
| `scripts/sync_futures_data.py` | 调度入口脚本（CLI 触发） |

| 修改文件 | 变更内容 |
|:---------|:---------|
| `fts/data_futures.py` | `FuturesDataProvider._from_*` 改为委托 `FuturesDataAggregator`，保持公开 API 不变 |
| `fts/core/contracts.py` | 新增 `FuturesOHLCV` / `FuturesDataLineage` TypedDict |
| `fts/core/enums.py` | 新增 `DataSource` 枚举（`TQ_LOCAL` / `WIND` / `IFIND` / `AKSHARE` / `SYNTHETIC`） |
| `pyproject.toml` | 新增 `tqsdk` 与可选 extra `tq`；保留 MCP extras 命名 |
| `fts/scheduler/tasks.py` | 新增 `sync_futures_data` 任务（每日 17:30 收盘后运行） |
| `fts/scheduler/jobs.py` | 实现 `sync_futures_data_job()` |
| `fts/cli.py` | 新增 `fts data sync-futures` 子命令 |
| `docs/harness/01-architecture.md` | 同步数据层架构图与数据流 |
| `docs/harness/02-lifecycle.md` | 登记新阶段 `Phase 14: 多源数据集成` |
| `docs/harness/04-resilience.md` | 同步降级路径与熔断阈值 |
| `docs/harness/05-observability.md` | 同步数据血缘指标（每源覆盖率/失败率） |
| `docs/harness/06-testing.md` | 同步测试用例数 |
| `docs/harness/07-operations.md` | 登记 v2.3.0 版本历史 |
| `docs/harness/08-gap-analysis.md` | 登记 GAP-021 多源集成 |
| `README.md` | 同步模块列表与快速参考 |

---

## 3. 契约优先（TypedDict 接口）

### 3.1 `FuturesOHLCV`（`fts/core/contracts.py`）

```python
class FuturesOHLCV(TypedDict):
    """期货标准化 OHLCV 行（v2.3.0 新增字段）。"""
    symbol: str           # 品种代码，如 "RB0" 或 "RB2509"
    date: str             # ISO 日期 "2026-08-04"
    open: float
    high: float
    low: float
    close: float
    volume: float         # 成交量（手）
    amount: float         # 成交额（元），缺失时为 NaN
    hold: float           # 持仓量（手）
    settle: float         # 结算价
    pre_settle: float     # 昨结算（仅 TQ/Wind 必有，AKShare 可能缺）
    oi_change: float      # 日增仓（= 当日 hold - 昨日 hold，可由 hold diff 计算）
    vwap: float           # 加权平均价（amount/volume 优先，否则 (H+L+C+settle)/4）
    source: str           # 数据源枚举值（DataSource.name）
    fetched_at: str       # ISO 时间戳，记录何时拉取
    trace_id: str         # 关联到本次同步任务
```

### 3.2 `FuturesDataLineage`（`fts/core/contracts.py`）

```python
class FuturesDataLineage(TypedDict):
    """单次同步任务的元数据。"""
    trace_id: str
    started_at: str
    finished_at: str
    symbols: list[str]
    rows_written: int
    sources_used: dict[str, int]   # {"WIND": 1234, "TQ_LOCAL": 800, ...}
    sources_failed: dict[str, str] # {"IFIND": "timeout", ...}
    disagreements: int             # 多源比对不一致行数
```

### 3.3 `DataSource` 枚举（`fts/core/enums.py`）

```python
class DataSource(str, Enum):
    TQ_LOCAL = "TQ_LOCAL"        # 通达信本地 HTTP
    TQ_PYTHON = "TQ_PYTHON"      # TQ-Python SDK
    WIND = "WIND"                # 万得 MCP
    IFIND = "IFIND"              # 同花顺 iFinD MCP
    AKSHARE = "AKSHARE"          # 兼容旧数据
    SYNTHETIC = "SYNTHETIC"      # 合成降级
    DUCKDB_CACHE = "DUCKDB_CACHE"  # 命中本地缓存
```

### 3.4 `BaseFuturesSource`（`fts/data_sources/base.py`）

```python
class BaseFuturesSource(ABC):
    """期货数据源抽象基类。所有适配器必须实现 fetch_ohlcv / fetch_quote / source_name。"""

    source_name: DataSource   # 子类赋值

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, days: int, trace_id: str) -> Optional[pd.DataFrame]: ...

    @abstractmethod
    def fetch_quote(self, symbol: str, trace_id: str) -> Optional[dict[str, Any]]: ...

    @abstractmethod
    def is_available(self) -> bool: ...   # 探活：TQ 看 7721 端口，Wind/iFinD 看认证

    def fetch_ohlcv_or_none(self, symbol, days, trace_id) -> Optional[pd.DataFrame]:
        """带超时与异常的包装：失败返回 None，不抛异常（由 Aggregator 决定降级）。"""
```

### 3.5 `FuturesDataAggregator` 接口

```python
class FuturesDataAggregator:
    """多源期货数据聚合器（v2.3.0+ 含交叉验证）。"""

    def __init__(
        self,
        sources: Optional[list[BaseFuturesSource]] = None,
        enhancers: Optional[list[BaseFuturesSource]] = None,
        db_path: Optional[Path] = None,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_cooldown_seconds: float = 6 * 3600,
        cache_max_age_days: int = 1,
        enable_cross_check: bool = True,        # 14.2 新增：是否启用多源交叉验证
        cross_check_threshold: float = 0.005,   # 14.2 新增：价格差异告警阈值（0.5%）
        disagreement_log_path: Optional[Path] = None,  # 14.2 新增：告警日志路径
    ): ...

    def get_ohlcv(self, symbol: str, days: int = 500, trace_id: str = "") -> pd.DataFrame:
        """对外主接口，签名与 FuturesDataProvider.get_ohlcv 保持一致。
        优先级（K 线主路径，Wind/iFinD 不参与）:
          1. DuckDB 缓存（最新交易日 == today 或 today-1）
          2. TQ-Local（HTTP 127.0.0.1:7721）
          3. TQ-Python SDK（主源备，TQ-Local 不可用时）
          4. AKShare（K 线兜底）
          5. 全部失败 → 合成数据（仅日志，不落盘）
        注：settle/oi_change 等期货专属字段从 Wind 字段增强层补齐，K 线主路径不依赖。
        14.2 新增：成功拉取 K 线后，自动对最近 N 天数据做多源交叉验证（如果 enable_cross_check=True）。
        """

    def get_futures_panel(self, symbols, days, trace_id) -> tuple[dict, pd.DatetimeIndex]:
        """批量面板，复用现有 common_dates 多数对齐逻辑。"""

    def sync_all(self, symbols: list[str], days: int = 500, trace_id: str = "") -> FuturesDataLineage:
        """主动全量同步：用于调度任务。返回本次同步血缘（含 disagreements 计数）。"""

    def cross_check(
        self,
        symbol: str,
        date: str,
        sources: Optional[list[BaseFuturesSource]] = None,
        trace_id: str = "",
    ) -> list[MultiSourceDisagreement]:
        """多源交叉验证：同日期同合约多源 close 对比，差异超阈值返回告警。

        Args:
            symbol: 品种代码（如 "RB0"）
            date: ISO 日期 "2026-08-04"
            sources: 参与交叉验证的源列表（默认 = 全部 enhancers + K 线源）
            trace_id: 链路追踪 ID

        Returns:
            MultiSourceDisagreement 列表。每条记录:
              {
                "symbol": str,
                "date": str,
                "prices": dict[str, float],  # {"TQ_LOCAL": 3540.5, "WIND": 3555.0, ...}
                "median": float,              # 中位数（参考价）
                "max_diff_pct": float,        # 偏离中位数的最大百分比
                "outliers": list[str],        # 偏离超阈值的源名列表
                "trace_id": str,
              }

        行为:
          1) 并行调用每个源获取该 symbol+date 的 close
          2) 计算中位数与每个源相对中位数的偏离百分比
          3) 偏离 > cross_check_threshold (默认 0.5%) 的源记为 outlier
          4) 若 outliers 非空 → 写入 disagreements 日志 + 返回列表
          5) 单源调用失败不中断其他源（异常被吞，返回 None）
        """
```

### 3.6 `MultiSourceDisagreement` 契约（v2.3.0+ 14.2 新增）

```python
class MultiSourceDisagreement(TypedDict):
    """多源交叉验证告警（v2.3.0+ 14.2 新增）。

    当同一 symbol+date 的多源 close 偏离中位数超过阈值时记录。
    """
    symbol: str                       # 品种代码
    date: str                         # ISO 日期
    prices: dict[str, float]          # {"TQ_LOCAL": 3540.5, "WIND": 3555.0, ...}
    median: float                     # 中位数（参考价）
    max_diff_pct: float               # 偏离中位数的最大百分比（小数，如 0.008 = 0.8%）
    outliers: list[str]               # 偏离超阈值的源名列表
    trace_id: str                     # 链路追踪 ID
    detected_at: str                  # ISO 时间戳
```

---

## 4. DuckDB 表结构扩展

### 4.1 `kline_cache` 扩展（向后兼容）

在现有 `kline_cache` 表上**新增列**（不破坏既有数据）：

```sql
ALTER TABLE kline_cache ADD COLUMN IF NOT EXISTS hold REAL;
ALTER TABLE kline_cache ADD COLUMN IF NOT EXISTS settle REAL;
ALTER TABLE kline_cache ADD COLUMN IF NOT EXISTS pre_settle REAL;
ALTER TABLE kline_cache ADD COLUMN IF NOT EXISTS oi_change REAL;
ALTER TABLE kline_cache ADD COLUMN IF NOT EXISTS vwap REAL;
ALTER TABLE kline_cache ADD COLUMN IF NOT EXISTS source VARCHAR DEFAULT 'AKSHARE';
ALTER TABLE kline_cache ADD COLUMN IF NOT EXISTS fetched_at TIMESTAMP;
ALTER TABLE kline_cache ADD COLUMN IF NOT EXISTS trace_id VARCHAR;
```

新增索引：

```sql
CREATE INDEX IF NOT EXISTS idx_kline_symbol_date_source
  ON kline_cache (symbol, date DESC, source);
```

### 4.2 新增 `edb_cache` 表（iFinD 宏观/产业链）

```sql
CREATE TABLE IF NOT EXISTS edb_cache (
  indicator VARCHAR NOT NULL,   -- 如 "中国PMI" / "螺纹钢社会库存"
  date DATE NOT NULL,
  value REAL,
  unit VARCHAR,                 -- 单位
  source VARCHAR DEFAULT 'IFIND',
  fetched_at TIMESTAMP,
  trace_id VARCHAR,
  PRIMARY KEY (indicator, date, source)
);
```

### 4.3 新增 `option_chain_cache` 表（Wind 主导）

```sql
CREATE TABLE IF NOT EXISTS option_chain_cache (
  underlying VARCHAR NOT NULL,  -- 如 "RB"
  contract VARCHAR NOT NULL,     -- 如 "RB2509C3500"
  date DATE NOT NULL,
  type VARCHAR,                 -- "C" / "P"
  strike REAL,
  last REAL,
  bid REAL,
  ask REAL,
  volume INTEGER,
  oi INTEGER,
  iv REAL,
  source VARCHAR DEFAULT 'WIND',
  fetched_at TIMESTAMP,
  trace_id VARCHAR,
  PRIMARY KEY (contract, date, source)
);
```

### 4.4 迁移策略

- `fts/data_sources/migrate.py` 提供 `migrate_schema()` 函数，幂等可重入。
- 启动时通过 `fts/__init__.py` 钩子自动检测并执行。
- 旧数据保留 `source='AKSHARE'`，新数据从多源写入。

---

## 5. 分阶段实施

> **总体节奏**：P0 数据层就绪 → P1 适配器接入 → P2 多源聚合 → P3 可观测性 → P4 收尾。
> 每阶段必须满足 HARNESS §5.4（测试随重构）：测试先写，全绿再进下一阶段。
> 每阶段结束 bump 版本号（§5.8）。

### Phase 14.0：契约与表结构（P0，1 周）

**目标**：定义好 TypedDict、枚举、DuckDB schema，不动业务逻辑。

**口径**：本阶段只产契约 + schema，K 线主路径仅 TQ + AKShare，Wind/iFinD 在后续阶段（14.2/14.3）作为字段增强层接入，本阶段不涉及。

- [ ] 写 `tests/core/test_contracts.py` 覆盖 `FuturesOHLCV` / `FuturesDataLineage` 字段
- [ ] 写 `tests/core/test_enums.py` 覆盖 `DataSource` 枚举
- [ ] 实现 `fts/data_sources/__init__.py` 子包入口
- [ ] 实现 `fts/data_sources/base.py`（`BaseFuturesSource` + `FuturesOHLCV` 校验器）
- [ ] 实现 `fts/data_sources/migrate.py` + 测试
- [ ] `fts/data_futures.py` 不变，保持兼容
- **验证**：`pytest tests/core tests/data_sources -q` 全绿
- **版本**：v2.3.0-alpha.0

### Phase 14.1：TQ 适配器（P0，1 周）

**目标**：TQ-Local（HTTP）优先，必要时回退到 TQ-Python。

- [ ] 实现 `fts/data_sources/tq_source.py`
  - `is_available()`: 探测 `http://127.0.0.1:7721`
  - `fetch_ohlcv()`: 调用 `tq_get_kline`（HTTP JSON-RPC 协议）
  - `fetch_quote()`: 调用 `tq_get_quote`
  - 代码格式转换：FTS `RB0` ↔ TQ `RB0.SHFE`（按交易所前缀）
- [ ] 写 `tests/data_sources/test_tq_source.py`
  - 用 `unittest.mock` 模拟 HTTP 响应
  - 覆盖：连接失败、解析失败、字段缺失、空数据
- [ ] 在 `tests/data_sources/conftest.py` 提供 mock 服务器 fixture
- **验证**：单测覆盖率 ≥ 90%，集成测试用本地 TQ 实测（可选）
- **版本**：v2.3.0-alpha.1

### Phase 14.2：Wind 适配器（P0，1 周）—— ✅ 已完成（合并到 14.0.8）

**目标**：通过 `run_mcp` 调用 Wind MCP 工具获取期货全字段。

- [x] 实现 `fts/data_sources/wind_source.py`
  - `is_available()`: 检查 MCP 鉴权
  - `fetch_ohlcv()`: 调用 `mx_comprehensive_finance_data`（`wind_code=RB2509.SHFE`, `period=day`）
  - `fetch_quote()`: 调用 `stock_highfreq_quotes`（Wind 期货实时价）
  - 字段映射：Wind `open/high/low/close/volume/amt/oi/settle` → `FuturesOHLCV`
- [x] 写 `tests/data_sources/test_wind_source.py`
  - mock MCP 调用返回示例 JSON
  - 覆盖：API 超时、字段缺失、错误码
- **验证**：单测覆盖率 ≥ 90%（21 个测试用例，14.0.8 报告验收）
- **版本**：v2.3.0-alpha.2

### Phase 14.3：iFinD 适配器（P1，1 周）—— ✅ 已完成（合并到 14.0.9）

**目标**：期货数据 + EDB 宏观/产业链双轨。

- [x] 实现 `fts/data_sources/ifind_source.py`
  - 期货 K 线：通过 `stock_market_data` 系列工具（`wind_code` 字段传期货代码）
  - 实时：通过 `stock_highfreq_quotes`
- [x] 实现 `fts/data_sources/edb_source.py`
  - `edb` 服务：`get_edb_data` 拉宏观（PMI/CPI/工业增加值/库存）
  - 写 `edb_cache` 表
- [x] 写 `tests/data_sources/test_ifind_source.py` + `test_edb_source.py`
- **验证**：单测覆盖率 ≥ 90%（25 个测试用例，14.0.9 报告验收）
- **版本**：v2.3.0-alpha.3

### Phase 14.4：聚合器 + 多源交叉验证（P1，1 周）—— 拆分为 14.1（聚合器）+ 14.2（交叉验证）

> **2026-08-04 更新**：原 14.4 拆分为两个阶段执行。
> - **14.1（已完成）**：`FuturesDataAggregator` 5 级降级 + 字段增强 + 熔断器
> - **14.2（当前）**：多源交叉验证 `cross_check()` —— 本页 §3.5 描述

**目标**：`FuturesDataAggregator` 调度 + 血缘记录 + 多源交叉验证。

**14.1 已完成**（详见 [acceptance/v2.3.0/14.1-aggregator-acceptance.md](acceptance/v2.3.0/14.1-aggregator-acceptance.md)）：

- [x] 实现 `fts/data_sources/aggregator.py`
  - K 线主路径优先级：`DUCKDB_CACHE` → `TQ_LOCAL` → `TQ_PYTHON` → `AKSHARE` → `SYNTHETIC`
  - 字段增强层：`WIND`（settle/oi_change/期权） + `IFIND`（EDB/产业链），并行独立调用
  - 熔断器：每源独立计数器 + 冷却时间
- [x] 写 `tests/data_sources/test_aggregator.py`（14 个测试，14.1 报告验收）
- [x] 端到端验证 `scripts/verify_aggregator.py`（7 场景）
- **版本**：v2.3.0-beta.0

**14.2 进行中**（多源交叉验证，详见 §3.5）：

- [ ] 扩展 `fts/core/contracts.py`：新增 `MultiSourceDisagreement` TypedDict 契约
- [ ] `FuturesDataAggregator` 构造函数加 3 个参数（`enable_cross_check` / `cross_check_threshold` / `disagreement_log_path`）
- [ ] 实现 `cross_check(symbol, date, sources, trace_id) -> list[MultiSourceDisagreement]`
- [ ] 在 `get_ohlcv()` 主路径尾部接入自动交叉验证
- [ ] `FuturesDataLineage.disagreements` 字段填充
- [ ] 写 5+ 测试（见 §3.5 行为列表）
- [ ] 端到端验证 `scripts/verify_cross_check.py`
- **版本**：v2.3.0-beta.1

### Phase 14.5：CLI、调度与可观测性（P1，1 周）—— 拆分为 14.4（CLI + 联调）+ 14.5（调度 + 可观测性）

> **2026-08-04 更新**：原 14.5 拆分为两个阶段执行。
> - **14.4（当前）**：CLI 子命令集成 + 真实多源联调（详见 §5.1）
> - **14.5（后续）**：调度注册 + 可观测性指标端点

**14.4 CLI + 联调**（本期已完成，详见 [acceptance/v2.3.0/14.4-cli-integration-acceptance.md](acceptance/v2.3.0/14.4-cli-integration-acceptance.md)）：

- [x] 新增 `fts/cli.py` 子命令：`fts data status` / `fts data sync-futures` / `fts data cross-check` / `fts data fuse`
- [x] `OHLCVFusion` 通过 CLI 暴露（参数化 5 策略）
- [x] `FuturesDataAggregator.cross_check` 通过 CLI 暴露
- [x] 实现 `scripts/verify_multi_source.py` 真实多源联调（按 14.1-14.3 链路串联，5 场景全绿）
- [x] 写 `tests/cli/test_data_cli.py`（17 个子命令参数 / trace_id / JSON 输出 / 契约验证测试）
- [x] 文档同步：02-lifecycle.md / 13-futures-data-source-integration.md / [acceptance/v2.3.0/14.4-cli-integration-acceptance.md](acceptance/v2.3.0/14.4-cli-integration-acceptance.md)
- **验证**：`python -m fts.cli data status` / `data fuse` 成功；`pytest tests/cli tests/data_sources -q` 全绿（17 + 137 = 154 个测试全绿）
- **版本**：v2.3.0-rc.0（已发布）

**14.5 调度 + 可观测性**（本期已完成，详见 [acceptance/v2.3.0/14.5-observability-acceptance.md](acceptance/v2.3.0/14.5-observability-acceptance.md)）：

- [x] `scripts/sync_futures_data.py` 包装（CLI 触发 → 调度触发）— 支持 `--symbol/--days/--universe/--json/--verbose`
- [x] `fts/scheduler/jobs.py` 实现 `sync_futures_data_job()` — trace_id=fts.sync.*，落盘 `data/_lineage/sync_summary_<ts>.json`
- [x] `fts/scheduler/tasks.py` 注册 `sync_futures_data` 任务（cron `30 17 * * 1-5` 工作日 17:30 收盘后）
- [x] `fts/monitor/http_server.py` 新增 `/metrics/data-sources` 端点：返回每源成功率/行数/最近失败/熔断状态
- [x] `fts/monitor/__init__.py` 新增 `check_data_sources_status()` Python 接口
- [x] `fts/monitor/http_server.py::_build_health` 集成到 `/health` 报告（任一源熔断 → `status="degraded"`）
- [x] `tests/scheduler/test_sync_futures_task.py` — 8 个测试用例
- [x] `tests/monitor/test_data_source_metrics.py` — 15 个测试用例
- [x] 文档同步：[acceptance/v2.3.0/14.5-observability-acceptance.md](acceptance/v2.3.0/14.5-observability-acceptance.md) / 02-lifecycle.md / 07-operations.md / 08-gap-analysis.md
- **验证**：`pytest tests/scheduler/ tests/monitor/ -q` 153 个测试全绿；端到端 `python scripts/sync_futures_data.py --symbol RB0 --days 30` 输出非空；`curl http://localhost:9100/metrics/data-sources` 返回 JSON
- **版本**：v2.3.0-rc.1（已发布）

### Phase 14.6：文档同步与收尾（P1，1 周）

**目标**：HARNESS 13 项检查全过。

- [ ] `docs/harness/01-architecture.md`：更新 §3 模块结构 + §4 数据流（图示）
- [ ] `docs/harness/02-lifecycle.md`：登记 Phase 14 阶段
- [ ] `docs/harness/04-resilience.md`：补多源降级路径
- [ ] `docs/harness/05-observability.md`：补数据血缘指标
- [ ] `docs/harness/06-testing.md`：同步测试数（预计 +200 用例）
- [ ] `docs/harness/07-operations.md`：登记 v2.3.0 版本历史
- [ ] `docs/harness/08-gap-analysis.md`：登记并关闭 GAP-021
- [ ] `docs/harness/09-advancement-plan.md`：补多源集成为晋级里程碑
- [ ] `README.md`：更新模块列表与快速参考
- [ ] `pyproject.toml`：新增 `[tq]` extra + 版本号 2.3.0
- [ ] 运行 `python scripts/verify_doc_consistency.py` 通过
- **验证**：13 项 commit 检查清单全过
- **版本**：v2.3.0

### 关键里程碑

| 日期 | 版本 | 验收点 |
|:-----|:-----|:-------|
| W1 | v2.3.0-alpha.0 | 契约 + schema 落地，单测全绿 |
| W2 | v2.3.0-alpha.1 | TQ 适配器 |
| W3 | v2.3.0-alpha.2 | Wind 适配器 |
| W4 | v2.3.0-alpha.3 | iFinD + EDB 适配器 |
| W5 | v2.3.0-beta.0 | 聚合器 + 交叉验证 |
| W6 | v2.3.0-rc.0 | CLI + 调度 + 监控 |
| W7 | v2.3.0 | 文档全同步，13 项检查通过 |

---

## 6. 风险与对策

| 风险 | 影响 | 对策 |
|:-----|:-----|:-----|
| **TQ 客户端未启动** | TQ 源不可用 | `is_available()` 探活，自动降级到 Wind |
| **iFinD API 配额耗尽** | 部分品种拉取失败 | 调度器在 `runtime_overhead` 监控配额，发现近 80% 自动切换主源 |
| **Wind MCP 鉴权失效** | Wind 源整体不可用 | 启动时检查连接，失败时记录 `WIND_UNAVAILABLE` 状态到 `/health` |
| **多源价格分歧** | 因子计算不稳定 | 14.2 阈值告警 + 人工仲裁（写 `data/data_source_disagreements.jsonl` + `MultiSourceDisagreement` 契约） |
| **AKShare 字段缺失导致旧数据迁移异常** | `ALTER TABLE` 失败 | `migrate.py` 用 `ADD COLUMN IF NOT EXISTS` 幂等 |
| **MCP 工具命名差异** | 误用接口 | 在每个适配器文件顶部加 `MCP_TOOL_NAME` 常量 + 注释 |
| **三方代码格式不一致**（`.SHFE` vs `.SHF`） | 查询无结果 | 在 `aggregator.py` 集中做代码格式转换（`code_normalize.py`） |

---

## 7. 反模式自检

> HARNESS §AP01–AP10 自检

- [x] **AP02** 先写计划文档（本文档）再写代码
- [x] **AP03** 本文档纳入 30 天维护周期
- [x] **AP04** MCP 接入总数：现 2（iFinD + Wind），新 +0（仍复用现有 server），< 10
- [x] **AP05** 新文件均 < 200 行（base.py ≤ 80，aggregator.py ≤ 150）
- [x] **AP06** 每阶段有独立验证（测试 + 健康检查）
- [x] **AP07** 每阶段有明确停止条件（W 任务清单全 ✅）
- [x] **AP08** 状态文件走 `data/_lineage/` JSONL，不与 STATE 共享
- [x] **AP10** 单 PR 文件数 < 20（按 Phase 拆 PR）

---

## 8. 角色边界（§5.6）

| 角色 | 职责 | 边界 |
|:-----|:-----|:-----|
| **FTS 数据层** | 多源数据适配、聚合、缓存、血缘 | 不做交易下单、不做因子计算 |
| **Data-Core**（外部） | 历史数据加工、清洗 | 本期不涉及，FTS 自洽 |
| **FDT** | 交易决策 | FTS 输出信号给 FDT 消费 |

---

## 9. 验证方式（贯穿所有阶段）

每阶段必须满足：

1. **单元测试**：`pytest tests/data_sources/ -q` 全绿
2. **覆盖率**：`pytest --cov=fts/data_sources --cov-report=term-missing`，新模块 ≥ 90%
3. **既有回归**：`pytest -q` 全量通过（1601+ 用例）
4. **手动冒烟**：`python -m fts.cli data sync-futures --days 30` 输出非空
5. **多源交叉验证（14.2）**：
   - 5+ 单元测试覆盖（阈值内/外/单源失败/日志写入/主路径触发）
   - 端到端：`python scripts/verify_cross_check.py` 5 场景全过
   - 告警 JSONL 文件存在且符合 `MultiSourceDisagreement` 契约
6. **13 项检查清单**（HARNESS §5.2）：commit 前逐项确认
7. **文档一致性**：`python scripts/verify_doc_consistency.py` 通过

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/data_sources/base.py` → `BaseFuturesSource` 抽象基类 + `FuturesOHLCV` TypedDict；`fts/data_sources/tq_source.py` → TQ 适配器（HTTP 7721 + TQ-Python）；`fts/data_sources/wind_source.py` → Wind MCP 适配器；`fts/data_sources/ifind_source.py` → iFinD 期货适配器；`fts/data_sources/edb_source.py` → iFinD 宏观/产业链 EDB 适配器；`fts/data_sources/aggregator.py` → `FuturesDataAggregator` 多源聚合器（5 级优先级 + 字段增强 + 熔断器 + **cross_check 14.2**）；`fts/core/contracts.py` → 新增 `MultiSourceDisagreement` 契约（14.2）；`fts/data_sources/migrate.py` → DuckDB schema 迁移；`fts/data_futures.py` → `FuturesDataProvider` 内部委托聚合器，公开 API 不变；`fts/scheduler/tasks.py` → 注册 `sync_futures_data` 任务（14.5）；`fts/scheduler/jobs.py::sync_futures_data_job` → 14.5 日终多源同步；`fts/monitor/http_server.py::_build_data_source_metrics` → 14.5 `/metrics/data-sources` 端点；`fts/monitor/__init__.py::check_data_sources_status` → 14.5 Python 接口；`scripts/sync_futures_data.py` → 14.5 手动 CLI 包装；`fts/cli.py` → 新增 `data sync-futures` 子命令；`scripts/verify_cross_check.py` → 14.2 端到端验证；`tests/data_sources/test_aggregator.py` → 27 个单元测试（19 现有 + 5 交叉验证 + 3 双 schema 兼容）；`tests/scheduler/test_sync_futures_task.py` → 14.5 新增 8 个测试；`tests/monitor/test_data_source_metrics.py` → 14.5 新增 15 个测试 |
| 可验证断言 | 适配器抽象基类 3 个抽象方法；K 线主路径 5 级 = DUCKDB_CACHE → TQ_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC；字段增强层 = WIND + IFIND；**多源交叉验证（14.2）** = `cross_check()` 返回 `MultiSourceDisagreement` 列表，阈值默认 0.5%，告警写入 `data/data_source_disagreements.jsonl`；DuckDB 扩列 8 个；多源交叉验证告警 ≤ 0.5% 价格差异忽略；新模块覆盖率 ≥ 90%；**14.5 调度 + 可观测性** = `sync_futures_data` cron `30 17 * * 1-5` + `/metrics/data-sources` 端点 + `/health` 集成 + `check_data_sources_status` Python 接口；版本号 v2.3.0-rc.1 |
| 检验方式 | `python -c "from fts.data_sources.aggregator import FuturesDataAggregator; print(FuturesDataAggregator)"`；`pytest tests/data_sources/ tests/scheduler/test_sync_futures_task.py tests/monitor/test_data_source_metrics.py --cov=fts/data_sources --cov-fail-under=90`；`python scripts/verify_cross_check.py`；`python -m fts.cli data sync-futures --days 30`；`python scripts/sync_futures_data.py --symbol RB0 --days 30`；`curl http://localhost:9100/metrics/data-sources`；`python scripts/verify_doc_consistency.py` |
