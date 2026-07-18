# FTS — Factor Trading System 实施计划

> 版本: v2.2 (草案)
> 目标: 将 FDT 的 `loop_engine` + `multi_factor_strategy` 剥离为独立的因子策略系统，支持国内期货、A股股票、ETF、可转债、REITs 等全市场品种。
> 核心设计变更 (v2.2): 明确项目边界 — Data-Core 负责数据采集+数据加工（含新闻分类/LLM情绪打分/情绪聚合/market_regime），FTS 只负责数据消费+因子推演+策略组建+交易信号产出；LLM 是三项目的基本能力，不作为边界划分标准。
> 核心设计变更 (v2.1): 明确项目边界 — Data-Core 负责数据采集+数据加工（含新闻分类），FTS 只负责数据消费+因子推演+策略组建+交易信号产出。
> 核心设计变更 (v2.0): FDT/FTS/Data-Core 三项目互相独立；FTS 数据层完全外置到 Data-Core，FDT 保留自有数据层；仅剥离因子引擎+多因子策略。
> 剥离统计: 8,259 行代码 / 28 文件，占 FDT 总量 8.2%

---

## 0. 架构决策说明

### 三项目独立关系

FTS、FDT、Data-Core 是三个**互相独立**的项目：

```
┌──────────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│       FDT（独立）     │     │       FTS（独立）     │     │    Data-Core（独立）  │
│ 9-Agent 辩论          │     │ 因子引擎              │     │ 统一数据入口          │
│ 8 策略管线             │     │ 多因子策略            │     │ 多源降级/缓存         │
│ futures_data_core/    │     │ 数据处理管线          │     │ 符号注册表            │
│ (自有数据层)           │     │                      │     │                      │
└──────────────────────┘     └──────────┬───────────┘     └──────────────────────┘
                                        │ pip install datacore
                                        └──────────────────────┘
```

- **FDT**: 完全独立，自有 `futures_data_core/`，不依赖 FTS 或 Data-Core
- **FTS**: 数据层通过 `pip install datacore` 接入，不包含任何数据源实现
- **Data-Core**: 完全独立，自有采集器和存储层

### 项目边界（v2.2 更新）

明确三项目的职责边界，避免功能重叠：

| 职责类别 | FDT | FTS | Data-Core |
|:---------|:----|:----|:----------|
| **数据采集** | ✅ 自有（期货） | ❌ | ✅ 全市场（期货/A股/新闻/公告） |
| **数据加工**（新闻分类/结构化） | ❌ | ❌ | ✅ 数据加工层（含LLM） |
| **数据加工**（LLM情绪打分/规则基线） | ❌ | ❌ | ✅ 数据加工层（含LLM） |
| **数据加工**（情绪聚合器/market_regime） | ❌ | ❌ | ✅ 数据加工层（含LLM） |
| **数据存储** | ✅ 自有 | ❌ | ✅ 自有 |
| **指标计算** | ✅ 自有 | ❌ | ❌ |
| **基本面分析** | ✅ 自有 | ❌ | ❌ |
| **因子推演**（挖掘/演化/评估） | ❌ | ✅ 核心能力 | ❌ |
| **多因子策略组建** | ❌ | ✅ 核心能力 | ❌ |
| **交易信号产出** | ❌ | ✅ 核心能力 | ❌ |
| **9-Agent 辩论** | ✅ 核心能力 | ❌ | ❌ |
| **CTP 信号输出** | ✅ | ❌ | ❌ |

**边界原则**:
- **Data-Core**：数据采集 + 数据加工（产出 NEWS/ANNOUNCEMENT/SENTIMENT/MARKET_STATE 等），含 LLM 调用
- **FTS**：数据消费 + 因子推演 + 策略组建 + 交易信号产出（消费 Data-Core 已加工数据）
- **FDT**：期货交易决策（9-Agent 辩论 + CTP 信号输出，自有数据层）
- **LLM 是三项目的基本能力**：不作为边界划分标准，边界仅基于能力与职责

### 剥离范围

仅从 FDT 剥离以下模块到 FTS：

| 剥离模块 | 原FDT路径 | 代码量 |
|:---------|:----------|:-------|
| 因子演化引擎 | `loop_engine/` | 5,089 行 / 15 文件 |
| 因子引擎测试 | `tests/loop_engine/` | 2,840 行 / 11 文件 |
| 多因子策略测试 | `tests/strategies/multi_factor*` | 330 行 / 2 文件 |

**FDT 保留**：`futures_data_core/`（数据采集、指标计算、基本面分析）、`fdt_langgraph/`（9-Agent 辩论）、其余7条策略管线。

### 为何采用"数据类型优先"而非"市场优先"？

按**数据类型**组织数据层，而非按市场：

```
❌ 旧设计（按市场划分）        ✅ 新设计（按数据类型划分）
adapters/                     data-core/
├── futures_adapter.py         ├── ohlcv/          # K线数据
├── stock_adapter.py           ├── fundamental/    # 基本面数据
├── etf_adapter.py             ├── news/           # 新闻资讯
├── cb_adapter.py              └── sentiment/      # 市场情绪
└── reit_adapter.py
```

原因：

1. **不同市场的数据类型交集 > 差集** — 期货、股票、ETF、可转债、REITs 都有 OHLCV，差异在于具体字段（是否有 OI），而非数据获取模式
2. **新闻/情绪是市场无关的数据类型** — 一条关于降准的新闻同时影响期货和股票，不应该分市场去抓取
3. **因子引擎的天然消费模式是按数据类型检索** — 一个因子可能需要 "过去 20 天的 OHLCV + 最近 3 天的新闻情绪得分"，而不是 "过去 20 天的期货数据 + 过去 3 天的股票新闻"
4. **扩展性更好** — 增加新市场只需要注册符号映射和实现缺失的数据源，无需新增适配器类

---

## 一、Data-Core 架构（外部依赖，非 FTS 内部）

> Data-Core 是独立项目，位于 `d:\Programs\data-core\`，FTS 通过 `pip install datacore` 引入。
> 以下架构描述仅供 FTS 开发者理解 Data-Core 的接口契约，实际实现以 Data-Core 项目为准。

```
┌──────────────────────────────────────────────────────────────────────────┐
│                   Data-Core (独立项目，pip install datacore)               │
│                                                                          │
│  ┌──────────────┬───────────────┬──────────────┬──────────────────┐      │
│  │  DataType.OHLCV  │ DataType.FUNDA │ DataType.NEWS  │ DataType.SENTIMENT │
│  │  K线/行情/量价    │ 基本面/财务     │ 新闻资讯        │ 市场情绪           │
│  └──────┬───────┴──────┬────────┴──────┬───────┴─────────┬────────┘      │
│         │              │              │                │                │
│         ▼              ▼              ▼                ▼                │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    UnifiedDataProvider                            │   │
│  │  统一入口: get(symbol, data_type, params) → DataPayload           │   │
│  │  自动路由到对应的 backends, 多源降级, 缓存命中                     │   │
│  └────────────────────────┬─────────────────────────────────────────┘   │
│                           │                                              │
│         ┌─────────────────┼────────────────────┐                        │
│         ▼                 ▼                    ▼                        │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐                │
│  │ DataSource  │  │ DataSource   │  │    存储层         │                │
│  │ TDX-LC      │  │ 东方财富/腾讯 │  │ DuckDB + Redis   │                │
│  │ 通达信      │  │ 国信证券      │  │ + Memory         │                │
│  └────────────┘  └──────────────┘  └──────────────────┘                │
│                                                                          │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  SymbolRegistry — 统一的符号注册表                                │   │
│  │  symbol → {market, name, data_sources, sector, ...}              │   │
│  │  "RB" → market=futures, name="螺纹钢", sources=["tdx","eastmoney"]│   │
│  └──────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 二、完整目录结构

> FTS 不包含数据层（`data/`），数据基础设施由独立项目 Data-Core 提供。

```
D:\Programs\factor_system\
├── fts/                           # 主包
│   ├── __init__.py                # 包入口 + 版本号
│   │
│   ├── core/                      # 核心契约（FTS 自身契约，非数据契约）
│   │   ├── __init__.py
│   │   ├── contracts.py           # 因子引擎的 TypedDict（从 loop_engine/contracts.py 迁移）
│   │   └── enums.py               # FTS 特有枚举（如 EvolutionStage）
│   │   # 注: DataType/MarketType/SourceGrade 由 datacore.models.enums 提供
│   │
│   ├── pipeline/                  # 因子推演管线（FTS 因子计算层，消费 Data-Core 已加工数据）
│   │   ├── __init__.py
│   │   ├── base.py                # FactorPipeline 抽象基类
│   │   └── factor_combiner.py     # 因子组合器（多因子加权/融合）
│   │   # 注: sentiment_llm.py/sentiment_rule.py/sentiment_aggregator.py/market_regime.py/fundamental_llm.py
│   │   #       已迁移到 Data-Core 数据加工层（含 LLM 调用）
│   │
│   ├── strategies/                # 策略层
│   │   ├── __init__.py
│   │   ├── base_v2.py             # BaseStrategyV2（从 FDT 迁移）
│   │   ├── multi_factor_strategy.py  # 多因子策略（数据类型感知改造）
│   │   └── rules/                 # 策略规则知识库
│   │       └── __init__.py
│   │
│   ├── factor_engine/             # 因子引擎（从 loop_engine/ 迁移）
│   │   ├── __init__.py
│   │   ├── evolution_loop.py      # L2 主循环
│   │   ├── meta_loop.py           # L1 元循环（通过 UnifiedDataProvider 感知市场）
│   │   ├── portfolio_loop.py      # L3 组合构建
│   │   ├── macro_evolution.py     # LLM 演化
│   │   ├── micro_evolution.py     # optuna 调参
│   │   ├── evaluation_chain.py    # 三级评估链
│   │   ├── experience_chain.py    # 经验链
│   │   ├── seed_pool.py           # 种子池（数据类型感知改造）
│   │   ├── factor_program.py      # 安全沙箱
│   │   ├── verifier.py            # Verifier
│   │   ├── state.py               # 状态管理
│   │   └── program.py             # L0 人类设定
│   │
│   ├── scheduler/                 # 调度层
│   │   ├── __init__.py
│   │   └── tasks.py               # 定时任务注册
│   │
│   ├── cli.py                     # 统一命令行入口
│   └── monitor.py                 # 健康监控
│
├── memory/                        # 运行时持久化
│   ├── evolution/                 # L2 状态
│   ├── meta_loop/                 # L1 状态
│   ├── portfolio/                 # L3 状态
│   └── knowledge/factors/         # 因子知识库
│       ├── elite/                 # 精英因子
│       ├── factor_pool.json       # 因子池元数据
│       └── l1_injected/           # L1 注入因子
│
├── config/                        # 配置
│   ├── settings.py                # 全局配置
│   └── settings.yaml              # YAML 配置文件
│
├── pyproject.toml                 # 包管理 (依赖 datacore)
├── PLAN.md                        # 本计划文档
└── CODE_WIKI.md                   # Code Wiki 技术文档
```

---

## 三、核心接口设计

> FTS 的数据层接口全部由 Data-Core 提供（`from datacore import ...`），以下仅描述 FTS 自有的核心接口。

### 3.1 数据类型枚举（由 Data-Core 定义，FTS 直接导入）

```python
# 从 datacore 导入，FTS 不重复定义
from datacore.models.enums import DataType, MarketType, SourceGrade

# DataType 枚举值参考（由 Data-Core 定义）：
# OHLCV, QUOTE, TECHNICAL, FINANCIAL, FUNDAMENTAL,
# MACRO, NEWS, ANNOUNCEMENT, SENTIMENT, MARKET_STATE

# MarketType 枚举值参考（由 Data-Core 定义）：
# FUTURES, STOCK, ETF, CB, REIT

# SourceGrade 枚举值参考（由 Data-Core 定义）：
# PRIMARY, DAILY, CACHED, STALE, UNAVAILABLE
```

### 3.2 统一数据入口（由 Data-Core 提供）

```python
# 从 datacore 导入
from datacore import UnifiedDataProvider

# UnifiedDataProvider 由 Data-Core 项目提供，FTS 通过 pip 依赖引入。
# 以下为接口参考，实际实现见 d:\Programs\data-core\

class UnifiedDataProvider:

### 3.3 数据源后端（由 Data-Core 实现，FTS 不涉及）

> Data-Core 的数据源后端（TDX-LC/东方财富/腾讯/国信证券等）由 Data-Core 项目独立实现和维护。
> FTS 不包含任何数据源实现代码，仅通过 `UnifiedDataProvider.get()` 消费数据。
> 详见 [Data-Core 项目](file:///d:/Programs/data-core/)

### 3.4 数据处理管线（FTS 自有，因子计算层）

```python
# fts/pipeline/base.py

class ProcessingStage(ABC):
    """数据处理管线阶段（FTS 因子计算层）。
    
    输入 Data-Core 已加工的结构化数据 → 输出因子输入（如带标签的新闻 → 情绪分数）。
    管线可组合串联。LLM 是管线中的一个 stage，不是独立的消费方。
    
    边界: 数据采集和基础加工（新闻分类、实体抽取）由 Data-Core 完成，
          FTS 管线从已结构化的数据开始。
    """
    
    input_type: DataType   # 从 Data-Core 获取的数据类型（已加工）
    output_type: DataType  # 管线产出的因子输入数据类型
    
    @abstractmethod
    def process(self, 
                input_data: DataPayload,
                symbol: str | None = None,
                ) -> DataPayload:
        """处理数据。"""


# 管线串联示例
# 注: NewsCollector 和 NewsClassifier 已由 Data-Core 完成，NEWS 返回时携带 tags
pipeline = (
    SentimentLLMStage()              # LLM 情绪打分（消费带 tags 的 NEWS）
    >> SentimentAggregatorStage()    # 按品种/时间聚合
)
# 输出: symbol→{date→{score, volume, topics, ...}}

# 零成本模式：跳过 LLM，使用词典法
pipeline_zero_cost = (
    SentimentRuleStage()             # 规则情绪基线（词典法）
    >> SentimentAggregatorStage()    # 按品种/时间聚合
)
```

### 3.5 统一符号注册表（由 Data-Core 提供）

```python
# 从 datacore 导入
from datacore.registry.symbol_registry import SymbolRegistry

# SymbolRegistry 由 Data-Core 项目提供，FTS 直接使用，不维护自己的注册表。

SymbolEntry = TypedDict("SymbolEntry", {
    "symbol": str,              # 统一代码
    "name": str,                # 中文名
    "market": MarketType,       # 市场类型
    "sector": str,              # 行业/板块/产业链
    "data_sources": dict[DataType, list[str]],  # 各类数据的最佳数据源
    "is_active": bool,          # 是否活跃可交易
    "tags": list[str],          # 标签（"主力合约", "沪深300成分", ...）
    "related_symbols": list[str],  # 关联品种（期权/期货正股/ETF成分）
})

class SymbolRegistry:
    """统一符号注册表 — 单一真相源。"""
    
    def lookup(self, symbol: str) -> SymbolEntry:
        """解析符号。支持别名（RB = futures.RB = 螺纹钢）。"""
    
    def list_by_market(self, market: MarketType) -> list[SymbolEntry]:
        """按市场列出所有符号。"""
    
    def list_by_sector(self, sector: str) -> list[SymbolEntry]:
        """按行业/板块列出。"""
    
    def search(self, query: str) -> list[SymbolEntry]:
        """模糊搜索（代码/名称/别名）。"""
```

### 3.6 数据载荷格式（由 Data-Core 提供）

```python
# 从 datacore 导入
from datacore.models.payload import DataPayload

@dataclass
class DataPayload:
    """统一数据载荷信封（替代 FDC 的 A2APayload）。"""
    symbol: str
    data_type: DataType
    market: MarketType
    
    # 核心数据（各数据类型不同）
    data: dict | list[dict] | pd.DataFrame
    
    # 元数据
    source: str                     # 数据源名称
    grade: SourceGrade              # PRIMARY / DAILY / CACHED / STALE / UNAVAILABLE
    collected_at: float             # 采集时间戳
    
    # 扩展信息
    meta: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
```

---

## 四、情绪数据管线设计（v2.2 更新）

新闻资讯和市场情绪是 Data-Core 区别于传统量化数据层的最大增量。

### 4.1 项目边界划分（v2.2 更新）

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Data-Core (数据采集 + 数据加工)                   │
│                                                                     │
│  数据源层                              数据加工层(含LLM)            │
│  ┌─────────────┐                      ┌──────────────────────┐      │
│  │ 财联社快讯    │                      │ 新闻分类器            │      │
│  │ 华尔街见闻   │──────────────────────→│ (宏观/产业/公司/政策) │      │
│  │ 东方财富研报  │                      └──────────┬───────────┘      │
│  │ 交易所公告    │                                 │                 │
│  │ 雪球/微博    │                                 ▼                 │
│  └─────────────┐                      ┌──────────────────────┐      │
│                │                      │ LLM 情绪打分          │      │
│                │                      │ (NEWS → SENTIMENT)    │      │
│                │                      └──────────┬───────────┘      │
│                │                                 │                 │
│                │                                 ▼                 │
│                │                      ┌──────────────────────┐      │
│                │                      │ 情绪聚合器            │      │
│                │                      │ (按品种/日/周聚合)     │      │
│                │                      └──────────┬───────────┘      │
│                │                                 │                 │
│                │                                 ▼                 │
│                │                      ┌──────────────────────┐      │
│                │                      │ 市场制度识别          │      │
│                │                      │ (OHLCV → MARKET_STATE)│      │
│                │                      └──────────┬───────────┘      │
│                │                                 │                 │
│                └─────────────────────────────────┼─────────────────┘
│                                                  ▼                 │
│                              DataType.NEWS / SENTIMENT / MARKET_STATE
└──────────────────────────────────────┬──────────────────────────────┘
                                       │ from datacore import ...
                                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    FTS (数据消费 + 因子推演)                         │
│                                                                     │
│  因子计算层                                                          │
│  ┌──────────────┐    ┌──────────────┐                              │
│  │ 因子组合器    │    │ 因子推演引擎  │                              │
│  │ (多因子加权)  │    │ (挖掘/演化)   │                              │
│  └──────────────┘    └──────────────┘                              │
│         ▲                    ▲                                     │
│         └────────────────────┘                                     │
│                    │                                               │
│                    ▼                                               │
│              交易信号产出                                            │
└─────────────────────────────────────────────────────────────────────┘
```

**边界原则**:
- **Data-Core 负责**: 数据源采集 + 数据加工（新闻分类/LLM情绪打分/规则情绪基线/情绪聚合器/market_regime），产出 NEWS/ANNOUNCEMENT/SENTIMENT/MARKET_STATE
- **FTS 负责**: 数据消费 + 因子推演 + 策略组建 + 交易信号产出（消费 Data-Core 已加工数据）
- **LLM 是三项目的基本能力**：Data-Core 的数据加工层包含 LLM 调用，不作为边界划分标准

### 4.2 情绪打分策略

| 模式 | LLM 需求 | 精度 | 成本 |
|------|---------|------|------|
| 规则基线 | 不需要 | 中等（~60% 准确） | 零成本 |
| LLM 增强 | 少量调用 | 高（~80%+ 准确） | 按 token 计费 |
| LLM 全量 | 大量调用 | 最高 | 高 |

默认运行**规则基线模式**（词典法 + 情感词库），LLM 增强作为可选选项。由 Data-Core 的数据加工层实现。

---

## 五、因子体系 — 数据类型到因子的映射

重新以"数据类型"视角看待因子：

### 5.1 从 OHLCV 数据派生（全市场通用）

| 因子 | 原始数据类型 | 计算方式 |
|------|------------|---------|
| momentum | OHLCV | 价格变化率 + MA 斜率 |
| volatility_reversion | OHLCV | 布林带位置 + ATR |
| volume_flow | OHLCV | 成交量 + OBV |
| macro_regime | OHLCV | 价格相对 MA120 |
| 趋势强度 ADX | OHLCV | Wilder DMI 计算 |
| 均线排列 | OHLCV | MA5/10/20/60 排序 |

### 5.2 从基本面数据派生

| 因子 | 原始数据类型 | 适用市场 | 数据源 |
|------|------------|---------|--------|
| basis | FUNDAMENTAL | 期货 | Data-Core (东方财富/100ppi) |
| inventory_pct | FUNDAMENTAL | 期货 | Data-Core (交易所仓单) |
| capacity | FUNDAMENTAL | 期货 | Data-Core (隆众/Mysteel) |
| position_rank | FUNDAMENTAL | 期货/A 股 | Data-Core (交易所) |
| pe_ep | FINANCIAL | A 股股票 | Data-Core (东方财富) |
| pb_ratio | FINANCIAL | A 股股票 | Data-Core (东方财富) |
| dividend_yield | FINANCIAL | 股票/REITs | Data-Core (东方财富) |
| premium_discount | FUNDAMENTAL | ETF/可转债 | Data-Core (东方财富) |
| conversion_premium | FUNDAMENTAL | 可转债 | Data-Core (东方财富) |

### 5.3 从新闻/情绪数据派生（新增价值）

| 因子 | 原始数据类型 | 计算方式 | 预期价值 |
|------|------------|---------|---------|
| news_sentiment | NEWS → SENTIMENT | 新闻情绪聚合得分 | 短期事件驱动 |
| sentiment_divergence | SENTIMENT | 情绪与价格走势背离 | 反转信号 |
| news_volume | NEWS | 新闻量异常暴增 | 事件预警 |
| topic_intensity | NEWS | 特定主题热度变化 | 主题投资 |
| social_momentum | SOCIAL_FEED | 社交媒体讨论热度 | 情绪扩散 |

### 5.4 从宏观数据派生

| 因子 | 原始数据类型 | 计算方式 | 适用市场 |
|------|------------|---------|---------|
| pmi_proxy | MACRO | PMI > 50 → 偏多 | 全市场 |
| rate_proxy | MACRO | LPR 升降方向 | 全市场 |
| macro_momentum | MACRO | PMI 环比变化方向 | 全市场 |

### 5.5 因子注册表 — 数据类型感知

```python
FACTOR_REGISTRY: dict[str, FactorDef] = {
    # ── 量价因子（全市场，从 OHLCV 计算） ──
    "momentum": {
        "fn": _calc_momentum,
        "data_types": [DataType.OHLCV],
        "markets": "*",
    },
    
    # ── 产业因子（期货专用，从 FUNDAMENTAL 计算） ──
    "basis": {
        "fn": _calc_basis,
        "data_types": [DataType.FUNDAMENTAL],
        "markets": [MarketType.FUTURES],
        "required_fields": ["basis_pct"],
    },
    
    # ── 情绪因子（全市场，从 SENTIMENT 计算） ──
    "news_sentiment": {
        "fn": _calc_news_sentiment,
        "data_types": [DataType.SENTIMENT],
        "markets": "*",
        "priority": "low",         # 初始低优先级
        "pending": True,            # 等待新闻源就绪后激活
    },
}
```

---

## 六、可扩展性分析

### 6.1 增加新数据源

> **由 Data-Core 项目负责**，FTS 无需改动。
> Data-Core 新增数据源后，FTS 自动获得新数据能力。
> 详见 [Data-Core 项目](file:///d:/Programs/data-core/)

### 6.2 增加新数据类型

> **由 Data-Core 项目负责**，FTS 无需改动。
> Data-Core 新增 DataType 后，FTS 的因子可在种子池中声明依赖新类型。
> 详见 [Data-Core 项目](file:///d:/Programs/data-core/)

### 6.3 增加新市场

> **主要由 Data-Core 负责**（注册符号到 SymbolRegistry）。
> FTS 中选择已有数据类型（OHLCV 自动适配），现有因子自动运行。

核心思想：**新市场 = Data-Core 注册符号映射 + FTS 选择已有数据类型**。

### 6.4 增加新因子

```python
# 1. 编写因子函数
def _calc_economic_calendar(t: dict, calender_data: dict) -> float:
    """经济日历因子：实际值 vs 预期值的偏差方向。"""
    ...

# 2. 注册到 FACTOR_REGISTRY
FACTOR_REGISTRY["economic_calendar"] = {
    "fn": _calc_economic_calendar,
    "data_types": [DataType.ECONOMIC_CALENDAR],
    "markets": "*",
}

# 3. 策略自动发现并集成
```

---

## 七、实施阶段（更新版）

> 前置条件：Data-Core 项目已提供 `pip install datacore` 可用包。

| 阶段 | 内容 | 前置条件 | 产出物 |
|------|------|----------|--------|
| **Phase 1** | FTS 核心契约 + 因子引擎骨架 + Data-Core 集成验证 | Data-Core v0.1.0+ | 因子引擎框架 |
| **Phase 2** | 因子引擎完整实现（三层循环 + 种子池数据类型感知） | Phase 1 | 可用的因子进化引擎 |
| **Phase 3** | 数据处理管线（新闻/情绪/市场制度） | Phase 2 | 衍生数据管线 |
| **Phase 4** | 多因子策略 + CLI + 调度 | Phase 2, 3 | 完整可运行系统 |
| **Phase 5** | 测试 + 文档 + FDT 侧清理（仅移除 loop_engine/ + multi_factor） | Phase 4 | 交付就绪 |

> **注意**: FDT 侧清理仅移除 `loop_engine/` 和 `multi_factor` 相关代码，`futures_data_core/` 全部保留。

---

## 八、核心设计原则总结

| 原则 | 说明 |
|------|------|
| **三项目独立** | FDT/FTS/Data-Core 互相独立；FDT 自有数据层，FTS 通过 Data-Core 获取数据 |
| **数据采集加工归 Data-Core** | 数据采集和数据加工（新闻分类/LLM情绪打分/情绪聚合/market_regime）由 Data-Core 负责，FTS 只消费已加工数据 |
| **FTS 专注因子智能** | FTS 职责为数据消费 + 因子推演 + 策略组建 + 交易信号产出，不涉及数据采集和加工 |
| **数据类型优先** | 数据层接口按 DataType 组织，非 MarketType |
| **数据层外置** | FTS 数据获取/存储/符号管理全部委托给 Data-Core，不重复造轮子 |
| **LLM 是基本能力** | LLM 是三个项目的基本能力，不作为边界划分标准；Data-Core 数据加工层包含 LLM 调用 |
| **符号全局唯一** | 由 Data-Core 的 SymbolRegistry 统一管理 |
| **因子声明所需数据类型** | 每个因子显式声明依赖哪些 DataType，引擎通过 Data-Core 自动编排 |
| **情绪默认零成本** | 规则基线模式可运行，LLM 增强可选，由 Data-Core 数据加工层实现 |
