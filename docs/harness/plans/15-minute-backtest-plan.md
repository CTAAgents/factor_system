# 分钟级回测实施计划（Phase 1 + Phase 2）

> 版本: v2.105.0+7
> 最后更新: 2026-08-08
> 状态: Phase 1 完成，Phase 2 完成（分钟级微观结构分析）
> 适用范围: FTS 回测流水线 + 数据层

---

## 0. 背景与目标

### 0.1 现状

当前 FTS 回测系统仅支持日线粒度回测，数据源为 DuckDB 缓存(kline_cache) + 通达信 TQ-Local + AKShare 降级。日线回测存在以下局限：

- 无法捕捉日内信号变化（如开盘跳空、盘中反转）
- 换手率/交易成本模型在高频场景下严重失真
- 无法验证日内策略（如分钟级止盈止损、高频信号）
- 无法与实盘信号管道（分钟级信号生成）对齐验证

### 0.2 目标

构建分钟级回测流水线，实现：

1. **分钟级数据获取**：通过通达信 TQ-Local(7721) + 通达信 HTTP(17709) + 天勤 TQSDK 三个数据源获取分钟 K 线
2. **多源时间对齐**：处理不同数据源的取数顺序差异，保证时间轴一致性
3. **分钟级缓存**：DuckDB minute_cache 表持久化分钟数据
4. **频率感知回测**：回测引擎支持 `--frequency` 参数，自动切换日线/分钟线逻辑
5. **年化因子自适应**：分钟级年化因子、z-score 窗口、成本模型自动适配

### 0.3 不在范围

- Tick 级回测（Phase 2）
- 实时行情订阅
- 跨品种分钟级同步（仅做单个品种的分钟级序列回测）

---

## 1. 架构设计

### 1.1 数据流

```
┌──────────────────────────────────────────────────────────────┐
│                    FTS 回测流水线 (BacktestPipeline)           │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐   │
│  │ DataLoad    │→ │ FactorCompute│→ │ Performance        │   │
│  │ Stage       │  │ Stage        │  │ Stage              │   │
│  └──────┬──────┘  └──────────────┘  └────────────────────┘   │
│         │ frequency='1m' / '5m' / 'daily'                     │
└─────────┼────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────────┐
│              FuturesDataAggregator (分钟级扩展)                 │
│                                                              │
│  ┌───────────────┐  ┌───────────────┐  ┌────────────────┐    │
│  │ 分钟数据路径    │  │ 日线数据路径    │  │ 字段增强层      │    │
│  │ (frequency)   │  │ (默认)         │  │ (Wind/iFinD)   │    │
│  └───────┬───────┘  └───────┬───────┘  └────────────────┘    │
│          │                  │                                  │
│          ▼                  ▼                                  │
│  ┌──────────────────────────────────────────────────┐        │
│  │  DuckDB                                          │        │
│  │  minute_cache  ← 分钟数据                        │        │
│  │  kline_cache   ← 日线数据                        │        │
│  └──────────────────────────────────────────────────┘        │
│          ▲                  ▲                                  │
│          │                  │                                  │
│  ┌───────┴───────┐  ┌───────┴───────┐                        │
│  │ 通达信 TDX    │  │ 通达信 TQ-Local│                        │
│  │ HTTP 17709    │  │ HTTP 7721     │                        │
│  │ (分钟)         │  │ (分钟+日线)    │                        │
│  └───────────────┘  └───────┬───────┘                        │
│                             │                                  │
│                     ┌───────┴───────┐                        │
│                     │ 天勤 TQSDK    │                        │
│                     │ (tqsdk 包)    │                        │
│                     │ (分钟+日线)    │                        │
│                     └───────────────┘                        │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 三源数据对比

| 维度 | 通达信 TDX HTTP (17709) | 通达信 TQ-Local (7721) | 天勤 TQSDK |
|:----|:----------------------|:---------------------|:----------|
| 协议 | HTTP JSON-RPC | HTTP JSON-RPC | Python SDK |
| 分钟周期 | 1m/5m/15m/30m/60m(参数 1h) | 1m/5m/15m/30m/60m | 1m/5m/15m/30m/60m |
| 数据范围 | 5m 约 10000 根（≈7.5 个月，实测） | 不限 | 免费版仅最近 100 根 |
| 时效性 | 实时 | 实时 | 实时 |
| 依赖 | 通达信客户端 | 通达信客户端 | pip install tqsdk |
| 时间戳格式 | Date+Time 字段 | datetime 字段 | datetime 字段 |
| 排序顺序 | 正序(旧→新) | 倒序(新→旧) | 正序(旧→新) |
| 返回格式 | 列字典 {代码: {列: [值]}} | 行列表 | DataFrame |

> 实测（2026-08-08）: 通达信客户端启动后 17709 端口可用，RB0 5m 可回取 10000 根 K 线（2025-12-26 → 2026-08-07），远优于 TQSDK 免费版。

### 1.3 时间对齐策略

不同数据源返回的分钟数据可能存在时间顺序差异：

- **通达信 TDX HTTP**: 正序返回，`datetime` 精确到秒
- **通达信 TQ-Local**: 倒序返回（需反转），`datetime` 精确到秒
- **天勤 TQSDK**: 正序返回，`datetime` 精确到秒

对齐策略：
1. 所有源统一返回 `datetime` 列，精确到秒
2. 聚合器内部按 `datetime` 升序排序
3. 多源交叉验证时按 `datetime` 精确匹配
4. 缓存写入前统一排序

### 1.4 通达信主力连续代码映射

FTS 使用 `{品种}0` 连续合约代码（如 RB0），通达信主力连续格式不同：

| 市场 | FTS 代码 | 通达信代码 |
|:----|:--------|:----------|
| 商品期货（SHFE/DCE/CZCE/INE） | RB0 / M0 / TA0 | RBL8.SHF / ML8.DCE / TAL8.CZC |
| 中金所（CFFEX） | IF0 / T0 | IFL0.CFF / TL0.CFF |

规则：`{品种}L8.{交易所后缀}`（商品）、`{品种}L0.CFF`（金融期货）。60m 周期通达信参数为 `1h`。

---

## 2. 模块变更清单

### 2.1 新增文件

| 文件 | 职责 |
|:----|:-----|
| `fts/data_sources/tqsdk_source.py` | 天勤 TQSDK 分钟数据源适配器 |
| `scripts/minute_microstructure_analysis.py` | Phase 2 分钟级微观结构特征分析脚本 |

### 2.2 修改文件

| 文件 | 变更内容 |
|:----|:---------|
| `fts/data_sources/tq_source.py` | 扩展 TQLocalSource 支持 `period` 参数（"day"/"1m"/"5m"等） |
| `fts/data_sources/tdx_minute_source.py` | 修复主力连续代码映射（RB0→RBL8.SHF）；解析列字典返回格式；60m 周期参数 1h |
| `fts/data_sources/aggregator.py` | 新增 `get_minute_ohlcv()` 方法；分钟源按请求频率动态重建（修复多频率对比） |
| `fts/data_sources/migrate.py` | 确保 minute_cache 表索引已创建 |
| `fts/data_sources/__init__.py` | 导出 TQSDKSource |
| `fts/factor_engine/backtest_pipeline.py` | BacktestInput 增加 `frequency` 字段；_compute_strategy_returns 支持频率自适应年化/窗口 |
| `fts/cli.py` | backtest run 增加 `--frequency` 参数 |
| `fts/core/enums.py` | 新增 `TQSDK` 数据源枚举成员 |
| `fts/data_futures.py` | FuturesDataProvider 增加 `get_minute_ohlcv()` 方法 |

### 2.3 数据契约变更

`BacktestInput` 新增字段：
```python
@dataclass
class BacktestInput:
    ...
    frequency: str = "daily"  # "daily" | "1m" | "5m" | "15m" | "30m" | "60m"
```

`minute_cache` 表结构（已存在，需确认索引）：
```sql
CREATE TABLE IF NOT EXISTS minute_cache (
    symbol      VARCHAR,
    period      VARCHAR,
    datetime    TIMESTAMP,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    volume      DOUBLE,
    hold        DOUBLE,
    source      VARCHAR,
    fetched_at  TIMESTAMP,
    trace_id    VARCHAR
)
```

---

## 3. 分钟级适配器契约

### 3.1 TQLocalSource 扩展

```python
class TQLocalSource(BaseFuturesSource):
    """通达信 TQ-Local HTTP 适配器（端口 7721）。
    
    扩展: 支持 period 参数，可选 "day" / "1m" / "5m" / "15m" / "30m" / "60m"
    """
    
    def __init__(self, period: str = "day"):
        self._period = period
    
    def fetch_ohlcv(self, symbol, days, trace_id="") -> Optional[pd.DataFrame]:
        # period 参数透传 tq_get_kline period 字段
        # 分钟数据返回 datetime 列，日线数据返回 date 列
```

### 3.2 TQSDKSource 契约

```python
class TQSDKSource(BaseFuturesSource):
    """天勤 TQSDK 数据源适配器（Python SDK）。
    
    使用 tqsdk 包直接获取期货行情数据。
    支持分钟/日线 K 线。
    """
    
    source_name: str = "TQSDK"
    
    def __init__(self, period: str = "day"):
        self._period = period
    
    def fetch_ohlcv(self, symbol, days, trace_id="") -> Optional[pd.DataFrame]:
        # 使用 tqsdk 的 get_kline_data() 接口
        # 注意: tqsdk 取数可能正序返回，与其他源对齐
    
    def is_available(self) -> bool:
        # 检查 tqsdk 是否已安装 + 认证
```

### 3.3 分钟数据统一 schema

分钟数据返回列（与 TDXMinuteSource 一致）：
```
symbol, period, datetime, open, high, low, close, volume, source, fetched_at, trace_id
```

相比日线 schema 差异：
- 使用 `datetime` 替代 `date`（分钟级精度）
- 不含 `amount`/`settle`/`pre_settle`/`oi_change`/`vwap`（分钟级无结算价）

---

## 4. 频率自适应逻辑

### 4.1 年化因子

| 频率 | 年化因子 | 说明 |
|:----|:--------|:-----|
| daily | 252 | 交易日数 |
| 60m | 252 × 6.5 = 1638 | 每日 6.5 小时交易 |
| 30m | 252 × 13 = 3276 | 每日 13 根 30m K 线 |
| 15m | 252 × 26 = 6552 | 每日 26 根 15m K 线 |
| 5m | 252 × 78 = 19656 | 每日 78 根 5m K 线 |
| 1m | 252 × 390 = 98280 | 每日 390 根 1m K 线 |

### 4.2 z-score 窗口

对应约 20 个交易日的滚动窗口：
```python
def get_default_zscore_window(frequency: str) -> int:
    annual = get_annualization_factor(frequency)
    return max(20, int(20 * annual / 252.0))
```

### 4.3 交易成本

分钟级回测使用与日线相同的成本模型，但换手率计算按分钟频率重新计算。

---

## 5. 执行计划

### Phase 1.1: 数据源适配（预计 1 小时）
1. 创建 TQSDKSource
2. 扩展 TQLocalSource 支持 period 参数
3. 更新 DataSource 枚举
4. 更新 __init__.py 导出

### Phase 1.2: 聚合器扩展（预计 0.5 小时）
1. Aggregator 新增 get_minute_ohlcv 方法
2. 分钟级缓存读写（minute_cache 表）
3. 分钟数据路径调度（TDX → TQ-Local → TQSDK）

### Phase 1.3: 回测引擎（预计 0.5 小时）
1. BacktestInput 增加 frequency 字段
2. 年化因子/窗口自适应
3. 数据加载阶段支持分钟级 schema

### Phase 1.4: CLI + 测试（预计 1 小时）
1. CLI 增加 --frequency 参数
2. 测试用例
3. 文档同步

### Phase 1.5: 验证（预计 0.5 小时）
1. 运行分钟级回测
2. 结果对比验证

### Phase 2: 分钟级微观结构特征分析（已完成，v2.30.0）

分析维度（`scripts/minute_microstructure_analysis.py`）：
1. **多频率对比** — 1m/5m/15m/30m/60m/日线 Sharpe/IC/换手率分布
2. **日内波动模式** — 不同交易时段波动率、收益率、成交量分布
3. **信号 IC 衰减** — 不同持有期（1/2/5/10/20/50 根 K 线）的 IC 衰减曲线
4. **信号自相关** — 分钟级信号持续性（半衰期）
5. **换手率分析** — 信号方向切换频率与持仓稳定性

2026-08-08 实测结果（RB0 / fut_bias 因子 / 通达信 17709 数据源）：
- 多频率回测: 1m Sharpe -8.9（噪音主导）、30m Sharpe 14.3、60m Sharpe 16.8、日线 Sharpe 3.17
- 日内波动: 夜盘 21:00 波动率最高（std 0.083%），尾盘 14:00 振幅最大（1.10%）
- IC 衰减: 5m 持有期下短期 IC 为负（-0.0013 @1 根），50 根后转正（0.0106）
- 信号自相关: 半衰期 7 根 K 线（≈35 分钟），衰减期 9 根，信号持续性中等
- 换手率: 5m 信号方向变化率 19.2%，信号中位数接近 0，多空信号分布不对称

> 风险提示：分钟级数据量（500-1000 根）统计显著性有限；1m 级年化因子过度放大指标；分钟级实盘交易成本显著更高。

---

## 6. 反模式检查

| 检查项 | 状态 |
|:------|:-----|
| AP01 巨型 Prompt | 通过（本计划 < 300 行） |
| AP02 跳过审核 | 待用户审核 |
| AP06 无独立验证 | 通过（Phase 1.5 验证） |
| AP10 一个 PR 改所有 | 5 个文件 + 1 个新增，< 20 文件 |

---

## 7. 一致性元数据

| 代码映射 | 可验证断言 | 检验方式 |
|:--------|:----------|:--------|
| `fts/data_sources/tqsdk_source.py` → §3.2 | TQSDKSource 实现 BaseFuturesSource 3 个抽象方法 | `pytest tests/test_tqsdk_source.py` |
| `fts/data_sources/tdx_minute_source.py` → §1.4 | RB0 → RBL8.SHF 且能解析列字典返回 | 运行 `python scripts/minute_microstructure_analysis.py` |
| `fts/data_sources/tq_source.py` → §3.1 | TQLocalSource 支持 period 参数 | `pytest tests/test_tq_source.py` |
| `fts/data_sources/aggregator.py` → §1.1 | Aggregator.get_minute_ohlcv 按频率返回正确周期数据 | `pytest tests/test_aggregator_minute.py` |
| `fts/factor_engine/backtest_pipeline.py` → §4 | BacktestInput.frequency 自适应年化/窗口 | `pytest tests/test_backtest_frequency.py` |
| `fts/cli.py` → §5 | CLI `--frequency` 参数透传 | `python -m fts.cli backtest run --help` |
| `scripts/minute_microstructure_analysis.py` → §5 Phase 2 | 脚本可运行并产出 Markdown 报告 | `python scripts/minute_microstructure_analysis.py --symbol RB0 --days 1000` |
| `docs/harness/plans/15-minute-backtest-plan.md` → 本文件 | 计划与实现一致 | `python scripts/verify_doc_consistency.py` |