# FTS 期货因子消费数据字段字典

> 版本: v1.5.1
> 适用项目: FTS (Factor Intelligence System)
> 维护: FTS Team
> 状态: **活跃** — FTS v1.5.0 起恢复期货横截面因子演化，基于纯 OHLCV 量价因子
> 存放路径: factor_system/docs/factor_data_dict/futures_factor_fields.md

---

## 0. 目录

1. [背景与数据策略](#1-背景与数据策略)
2. [期货数据源架构](#2-期货数据源架构)
3. [数据字段定义](#3-数据字段定义)
4. [数据获取流程](#4-数据获取流程)
5. [期货因子消费方式](#5-期货因子消费方式)
6. [数据降级策略](#6-数据降级策略)
7. [期货品种列表](#7-期货品种列表)
8. [版本与变更](#8-版本与变更)
9. [一致性元数据](#9-一致性元数据)

---

## 1. 背景与数据策略

FTS 在 v1.5.0 重新引入期货横截面因子演化，采用与 A 股不同的数据策略：

| 维度 | A 股 | 期货 |
|:-----|:-----|:-----|
| 数据源 | akshare 腾讯自选股 API | akshare `futures_zh_daily_sina` |
| 存储 | DuckDB kline_cache | DuckDB kline_cache（同一张表） |
| 截面定义 | 50 只 CSI300 成分股 × 同一日期 | 不同品种 × 同一日期（跨品种截面） |
| 因子类型 | 量价 + 基本面（估值/质量/成长） | 纯量价（OHLCV + 持仓量） |
| 种子因子 | 482 个（含 23 个基本面） | 482 个（仅通过 IC 筛选的量价子集） |
| 典型品种数 | 50 | 25（核心）/ 82（全量） |

**关键差异**：期货无 `pe_ttm` / `pb` / `roe` 等基本面字段，`enrich_with_fundamental` 返回空。

---

## 2. 期货数据源架构

```text
AKShare futures_zh_daily_sina
    │
    ▼
scripts/download_futures.py  ──→  DuckDB (data/fts_history.duckdb)
    │                                └── kline_cache 表
    │                                    ├── symbol = "RB0"  (连续合约)
    │                                    ├── date, open, high, low, close
    │                                    ├── volume, hold, settle, amount
    │                                    └── ...
    │
    ▼
FuturesDataProvider (fts/data_futures.py)
    │
    ├── get_ohlcv(symbol, days)
    │       └── 1. DuckDB kline_cache → 2. AKShare 即时 → 3. 合成降级
    │
    ├── get_futures_panel(symbols, days)
    │       └── 批量获取 + 日期对齐 → (panel, common_dates)
    │
    ▼
FTSDataProvider (fts/data.py)
    │
    ├── get_futures_ohlcv()    → 单品种 OHLCV
    └── get_futures_panel()    → 多品种面板
```

### 2.1 合约管理

FTS 使用 AKShare 的 `futures_zh_daily_sina` 接口获取**连续合约**数据，合约代码格式为 `{symbol}0`（如 `RB0`、`CU0`、`IF0`）。

连续合约特点：
- 由 AKShare 自动处理主力合约切换和展期
- 数据连续无跳空，适合量化因子计算
- 无需手动处理展期

---

## 3. 数据字段定义

### 3.1 DuckDB kline_cache 表字段

| 字段 | 类型 | 来源 | 因子计算使用 | 说明 |
|------|------|------|-------------|------|
| `symbol` | VARCHAR | AKShare | — | 连续合约代码（如 `RB0`） |
| `date` | DATE | AKShare | ✅ 日期索引 | 交易日 |
| `open` | DOUBLE | AKShare | ✅ | 开盘价 |
| `high` | DOUBLE | AKShare | ✅ | 最高价 |
| `low` | DOUBLE | AKShare | ✅ | 最低价 |
| `close` | DOUBLE | AKShare | ✅ **核心字段** | 收盘价 |
| `volume` | DOUBLE | AKShare | ✅ | 成交量（手） |
| `hold` | DOUBLE | AKShare | ✅ | 持仓量（手） |
| `settle` | DOUBLE | AKShare | ✅ | 结算价 |
| `amount` | DOUBLE | AKShare | 备选 | 成交额（元） |

### 3.2 字段映射规则

FuturesDataProvider 从 DuckDB 读取后统一映射为 Pandas DataFrame：

| 标准化列名 | 来源 | 因子算子使用 |
|-----------|------|-------------|
| `open` | kline_cache.open | `opn(col)`, `open` |
| `high` | kline_cache.high | `high(col)`, `hig` |
| `low` | kline_cache.low | `low(col)`, `low` |
| `close` | kline_cache.close | `close(col)`, `cls` |
| `volume` | kline_cache.volume | `vol(col)`, `volume` |
| `hold` | kline_cache.hold | `hold`（持仓量，期货特有） |
| `settle` | kline_cache.settle | `settle`（结算价，期货特有） |
| `amount` | kline_cache.amount | `amount`（成交额，备选） |
| `vwap` | 计算 `amount / volume` | `vwap(col)`（日内均价） |
| `returns` | 计算 `close.pct_change()` | `returns(col)`（收益率） |

### 3.3 期货特有字段特点

- `hold`（持仓量）：反映市场持仓兴趣，是期货特有的量能指标，类比股票的成交量
- `settle`（结算价）：期货日线特有字段，为当日结算价（与收盘价有差异）
- `volume` 单位是**手**（股票是**股**）
- `amount` 部分品种可能为空，此时由 `volume * close` 估算

---

## 4. 数据获取流程

### 4.1 FuturesDataProvider 核心方法

```python
class FuturesDataProvider:
    """期货数据提供者 — 基于 DuckDB kline_cache 表。

    数据源优先级:
        1. DuckDB kline_cache（连续合约，已持久化）
        2. AKShare 即时获取（futures_zh_daily_sina API）
        3. 合成数据降级（保证系统可运行）
    """

    def get_ohlcv(
        self,
        symbol: str,       # 如 "RB0"
        days: int = 500,   # 回溯天数
        trace_id: str = "",
    ) -> pd.DataFrame:
        """获取期货连续合约 OHLCV 日 K 线数据。"""

    def get_futures_panel(
        self,
        symbols: list[str] | None = None,  # 默认 FUTURES_CORE_SUBSET
        days: int = 500,
        trace_id: str = "",
    ) -> tuple[dict[str, pd.DataFrame], pd.DatetimeIndex]:
        """获取多期货品种 OHLCV 面板数据。"""
```

### 4.2 数据使用时序

```
数据下载 → DuckDB 持久化 → FTS 启动 → FuturesDataProvider 读取 → 因子计算
    ↑                            ↑
 初次运行需下载              每次计算前自动对齐日期
```

### 4.3 日期对齐

期货品种因节假日差异（如国内与国际品种），截面日期对齐方式：

```
panel, common_dates = provider.get_futures_panel(symbols, days=120)
# common_dates 是所有品种共有的交易日集合
# 每个品种的 DataFrame 以 common_dates 为索引
```

---

## 5. 期货因子消费方式

### 5.1 因子来源

期货因子演化使用与 A 股相同的种子因子库（共 482 个），但通过 IC≥0.03 和 Sharpe≥1.5 筛选出在期货截面有效的子集：

| 来源 | 数量 | 期货演化通过数 |
|:-----|:-----|:--------------|
| 内置因子 | 9 | 视 IC 筛选结果 |
| 世坤 101 (wq101) | 101 | 视 IC 筛选结果 |
| Qlib 158 | 158 | 视 IC 筛选结果 |
| 国泰君安 191 (gtja191) | 191 | 视 IC 筛选结果 |
| 基本面因子 | 5 | 不适用（期货无基本面） |

**注意**：期货因子仅使用量价字段（`open/high/low/close/volume/hold/settle`），`pe_ttm` / `pb` / `roe` 等基本面字段在期货截面中不可用。

### 5.2 因子计算流程

```python
# 1. 获取期货面板数据
panel, common_dates = provider.get_futures_panel(symbols, days=120)

# 2. 对每个品种计算因子信号
for sym, df in panel.items():
    executor = FactorExecutor(factor_data)
    signals = executor.execute(df, factor_data.get("params", {}))
    # 取最新信号值
    val = float(signals[-1])

# 3. 等权合成多因子信号
composite = sum(w * val for each factor)
```

### 5.3 典型因子示例

以下因子在期货横截面演化中表现出有效性（以当前 Elite 因子为例）：

| 因子名 | 来源 | 期货 Sharpe | 期货 IC | 逻辑类型 |
|--------|------|-------------|---------|----------|
| `gtja_120` | 国泰君安 | 高 | 高 | 趋势跟踪 |
| `gtja_063` | 国泰君安 | 高 | 高 | 动量 |
| `qlib_057` | Qlib | 高 | 高 | 价格结构 |
| `qlib_031` | Qlib | 高 | 高 | 波动率 |
| `momentum` | 内置 | 中 | 中 | 跨品种动量 |
| `volatility_reversion` | 内置 | 高 | 高 | 波动率反转 |

---

## 6. 数据降级策略

### 6.1 三级降级

| 优先级 | 数据源 | 触发条件 | 说明 |
|--------|--------|----------|------|
| **L1** | DuckDB kline_cache | 优先使用 | 已持久化的连续合约数据，需先运行 `scripts/download_futures.py` |
| **L2** | AKShare 即时获取 | DuckDB 无数据或查询失败 | 通过 `futures_zh_daily_sina` 实时获取，调用间隔 0.5s 防封 |
| **L3** | 合成数据 | 前两者均不可用 | 基于 `numpy` 生成随机 OHLCV，`base_price=3000.0`，仅保证系统可运行 |

### 6.2 断点续传

`scripts/download_futures.py` 支持断点续传：

```bash
# 首次下载 25 个核心品种
python scripts/download_futures.py --subset

# 强制刷新（忽略已有数据）
python scripts/download_futures.py --subset --force

# 下载全部 82 个品种
python scripts/download_futures.py
```

下载逻辑：
1. 检查 `kline_cache` 表是否已有该品种数据
2. 已有 → 跳过（断点续传）
3. 无 → 调用 AKShare 下载并写入 DuckDB
4. 每次调用间隔 0.5s，避免被封

---

## 7. 期货品种列表

### 7.1 核心品种（25 个）

```python
FUTURES_CORE_SUBSET = [
    "RB0",  # 螺纹钢
    "CU0",  # 铜
    "AU0",  # 黄金
    "AG0",  # 白银
    "I0",   # 铁矿石
    "M0",   # 豆粕
    "TA0",  # PTA
    "MA0",  # 甲醇
    "SC0",  # 原油
    "HC0",  # 热卷
    "NI0",  # 镍
    "SN0",  # 锡
    "P0",   # 棕榈油
    "Y0",   # 豆油
    "C0",   # 玉米
    "A0",   # 豆一
    "CF0",  # 棉花
    "SR0",  # 白糖
    "SA0",  # 纯碱
    "IF0",  # 沪深300股指
    "IC0",  # 中证500股指
    "IH0",  # 上证50股指
    "IM0",  # 中证1000股指
    "LC0",  # 碳酸锂
    "SI0",  # 工业硅
]
```

### 7.2 全量品种（82 个）

除核心品种外，还包括以下品种（完整列表见 `fts/data_futures.py` 中的 `FUTURES_ALL_SYMBOLS`）：

- 能源化工：EG0（乙二醇）、PP0（聚丙烯）、L0（聚乙烯）、V0（PVC）、EB0（苯乙烯）、UR0（尿素）、FG0（玻璃）、SM0（锰硅）、SF0（硅铁）
- 农产品：B0（豆二）、RM0（菜粕）、OI0（菜油）、AP0（苹果）、CJ0（红枣）、CS0（玉米淀粉）、JD0（鸡蛋）、LH0（生猪）、PG0（液化气）
- 黑色系：JM0（焦煤）、J0（焦炭）、SS0（不锈钢）
- 有色金属：BC0（国际铜）
- 贵金属：（无新增）
- 金融期货：TS0（2年期国债）、TF0（5年期国债）、T0（10年期国债）、TL0（30年期国债）
- 股指期权：HO0（沪深300期权）、MO0（中证1000期权）、ZO0（中证500期权）

---

## 8. 版本与变更

| 版本 | 日期 | 变更 |
|:-----|:-----|:------|
| v1.0.0 | 2026-07-21 | 初版：与 Data-Core 期货数据模块对齐，6 个期货专用因子 |
| v1.2.0 | 2026-08-02 | **归档**：FTS 移除期货支持，标记为参考归档 |
| **v1.5.1** | **2026-08-03** | **重写**：FTS 恢复期货横截面因子演化，基于纯 OHLCV 量价因子 + DuckDB + AKShare 数据源。移除旧 6 因子描述，替换为当前量价因子数据架构 |

---

## 9. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| File | `docs/factor_data_dict/futures_factor_fields.md` |
| 代码→文档映射 | `fts/data_futures.py`（FuturesDataProvider）、`fts/data.py`（FTSDataProvider.get_futures_ohlcv/get_futures_panel）、`fts/cli.py`（--universe futures）、`scripts/download_futures.py` |
| 可验证断言 | `FUTURES_CORE_SUBSET` 定义在 `fts/data_futures.py` 中，共 25 个核心品种 |
| 检验方式 | `python -c "from fts.data_futures import FUTURES_CORE_SUBSET; print(len(FUTURES_CORE_SUBSET))"` 返回 25 |