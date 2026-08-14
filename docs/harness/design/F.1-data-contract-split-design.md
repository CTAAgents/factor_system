# F.1 数据契约拆分（FusedOHLCV → StockOHLCV / FuturesOHLCV）— 详细技术设计

> 版本: v2.104.0+39-draft
> 创建: 2026-08-12
> 状态: **设计中**（待评审，未实现）
> 关联: [29-storage-convergence-plan.md](file:///d:/Programs/factor_system/docs/harness/plans/29-storage-convergence-plan.md)（六层存储）、[F.2-evolution-engine-fork-design.md](./F.2-evolution-engine-fork-design.md)（引擎分叉，联动本设计）
> 背景: 期货/股票双管线独立化改造的一部分——先解除**数据契约层的市场混合**，为引擎分叉与数据供应分离提供契约基础。

---

## 1. 目标与范围

**目标**: 将当前混合承载期货与股票字段的 `FusedOHLCV` 契约，拆分为市场独立的 `StockOHLCV` / `FuturesOHLCV` 两份契约，并迁移全部消费方。

**范围**:
- 契约层重构（`fts/core/contracts.py`）
- 构造方迁移（`fts/data_sources/fusion.py`）
- 读字段方迁移（`fts/cli.py`、`fts/factor_engine/expr_dsl/registry.py`）
- 存储表对齐确认（`stock_kline_cache` 已存在，本轮不改 DDL，仅契约对齐）

**不在范围**:
- 行情存储表迁移（`stock_kline_cache` 已存在，见 §5.1 说明）
- 演化引擎分叉（见 F.2）
- 数据源接入层拆分（tdx_local/akshare 等薄封装保留共享，另行处理）

---

## 2. 现状盘点（调研结论）

### 2.1 契约现状

| 契约 | 位置 | 现状 |
|---|---|---|
| `FusedOHLCV` | [contracts.py](file:///d:/Programs/factor_system/fts/core/contracts.py#L70-L99) | **混合契约**：必填 6 字段 + 融合元数据（contributing_sources/fusion_strategy/disagreement_pct）+ 期货字段（settle/hold/oi_change/pre_settle/vwap，全 Optional） |
| `FuturesOHLCV` | [contracts.py](file:///d:/Programs/factor_system/fts/core/contracts.py#L105-L129) | **已存在**：期货专用，含 hold/settle/pre_settle/oi_change/vwap + amount/source/fetched_at，**无融合元数据** |

**关键结论**: `FuturesOHLCV` 已存在，本轮**不新建期货契约**，而是：
1. 新建 `StockOHLCV`（股票侧，含复权因子 adjust_factor）
2. 为 `FuturesOHLCV` 补齐融合元数据（contributing_sources/fusion_strategy/disagreement_pct）——或经公共基契约继承
3. `FusedOHLCV` 降级为 `Union[StockOHLCV, FuturesOHLCV]` 类型别名（避免爆破 import 面）

### 2.2 存储现状（已分表）

[migrate.py](file:///d:/Programs/factor_system/fts/data_sources/migrate.py#L265-L269) 已物理分离：

| 表 | 归属 | 说明 |
|---|---|---|
| `kline_cache` | 期货 | 17 列，含 hold/settle/pre_settle/oi_change/vwap |
| `contract_kline` | 期货 | 具体合约日线（换月日历基础） |
| `stock_kline_cache` | 股票/ETF | v2.86.0 已建，**无期货字段**，含复权因子 |

契约拆分与存储现状天然对齐：契约层落后于存储层，本轮是"契约追平存储"。

### 2.3 消费方盘点

| 文件 | 行号 | 角色 | 用途 |
|---|---|---|---|
| `fts/data_sources/fusion.py` | L99/L172/L226/L329 | **构造方** | `fuse_row`/`fuse_dataframe`/`_passthrough` 构造 FusedOHLCV |
| `fts/cli.py` | L535/L574 | **读字段方** | `fts data fuse` cast 与读取 |
| `fts/core/contracts.py` | L161 | **类型声明** | `FusionReport.rows` |
| `fts/factor_engine/expr_dsl/registry.py` | L31 | **读字段方** | 期货专用字段 `settle` 注册 |

---

## 3. 目标契约结构

### 3.1 契约分层

```
┌─ OHLCVBase（共享层，无市场形状）───────────────────┐
│  symbol/date/open/high/low/close/volume/trace_id   │
└────────────────────┬─────────────────────────────┘
                     │ 继承
        ┌────────────┴────────────┐
        │                         │
┌───────▼────────┐        ┌───────▼────────┐
│  StockOHLCV    │        │  FuturesOHLCV  │
│  +amount       │        │  +amount       │
│  +adjust_factor│        │  +hold         │
│  +source       │        │  +settle       │
│  +fetched_at   │        │  +pre_settle   │
│  +融合元数据    │        │  +oi_change    │
│                │        │  +vwap         │
│                │        │  +source       │
│                │        │  +fetched_at   │
│                │        │  +融合元数据    │
└────────────────┘        └────────────────┘

FusedOHLCV = Union[StockOHLCV, FuturesOHLCV]  # 兼容别名
```

### 3.2 具体契约定义

```python
# fts/core/contracts.py — 目标态

class OHLCVBase(TypedDict, total=False):
    """公共 OHLCV 字段（无市场形状，共享层）。"""
    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    trace_id: str


class FusionMeta(TypedDict, total=False):
    """融合元数据（两市场共用，共享层）。"""
    contributing_sources: list[str]
    fusion_strategy: str
    disagreement_pct: float


class StockOHLCV(OHLCVBase, FusionMeta, total=False):
    """股票/ETF 单条 K 线契约（股票特有形状：复权因子）。"""
    amount: float
    adjust_factor: float      # 复权因子（新增，股票特有）
    source: str
    fetched_at: str


class FuturesOHLCV(OHLCVBase, FusionMeta, total=False):
    """期货单条 K 线契约（期货特有形状：结算/持仓/换月）。"""
    amount: float
    hold: float
    settle: float
    pre_settle: float
    oi_change: float
    vwap: float
    source: str
    fetched_at: str


# 兼容别名：FusedOHLCV 不删除，降级为 Union 类型别名
FusedOHLCV = Union[StockOHLCV, FuturesOHLCV]
```

> **注**: TypedDict 支持多继承（mypy 1.x + typing_extensions），`StockOHLCV(OHLCVBase, FusionMeta)` 语法合法。若工具链不支持多继承，退化为扁平结构 + 共享字段重复声明（见 §7 风险）。

### 3.3 关键决策

| # | 决策 | 理由 |
|---|---|---|
| D1 | `FuturesOHLCV` 已存在，不新建，补齐融合元数据 | 避免重复契约漂移 |
| D2 | 新增 `adjust_factor` 至 `StockOHLCV` | 复权是股票特有形状，当前 FusedOHLCV 缺失此字段 |
| D3 | `FusedOHLCV` 保留为通用兼容契约（字段冻结），不删除 | 避免爆破 import 面；新代码逐步改用市场契约 |
| D4 | 融合元数据（FusionMeta）进共享层 | 融合算法市场无关，两市场融合输出都有 |
| D5 | **不改字段名** | DSL/因子代码零改动，settle/hold 等在期货契约中保持原名 |
| D6 | **实现偏差（2026-08-12）**: fusion.py 构造方**不加 market 参数**，返回类型保持 `FusedOHLCV` 兼容契约 | 融合算法市场无关，按 market 分支属过度设计；输出字段全 Optional 同时兼容两种市场契约，契约下沉到类型层而非运行时分支 |

> **实现说明（2026-08-12）**: 采用 Python 3.10 兼容的**扁平 TypedDict** 结构（`StockOHLCV`/`FuturesOHLCV` 独立声明 + `OHLCVBase`/`FusionMeta` 为共享参考契约），不依赖 TypedDict 多继承；`FusedOHLCV` 保留为通用兼容契约，融合器与 cli 零改动。

---

## 4. 消费方迁移清单

| 文件 | 行号 | 动作 | 具体修改 |
|---|---|---|---|
| `fts/data_sources/fusion.py` | L99/L172/L226/L329 | **修改** | `fuse_row`/`fuse_dataframe`/`_passthrough` 增加 `market: str` 参数，按市场构造 `StockOHLCV`/`FuturesOHLCV`；返回值类型改为 `Union[StockOHLCV, FuturesOHLCV]` |
| `fts/cli.py` | L535/L574/L577 | **修改** | L535 import 改为市场契约；L574 cast 改为按 market 判定；L577 `rows[0]["date"]` 共用字段不变 |
| `fts/core/contracts.py` | L70-L99/L161 | **修改** | FusedOHLCV 定义替换为 OHLCVBase/FusionMeta/StockOHLCV；FusionReport.rows 改 `list[Union[StockOHLCV, FuturesOHLCV]]` |
| `fts/factor_engine/expr_dsl/registry.py` | L31 | **修改** | `settle` 字段注册移入期货侧 DSL 注册表（若 registry 无市场区分，则保留并注明仅期货因子使用） |
| `fts/cross_market/data_adapter.py` | L34 | **核对** | `FUTURES_SPECIFIC_FIELDS = ["open_interest", "hold", "settle"]` 与期货契约对齐，无需改动（读取面板而非契约） |

**迁移原则**: 只改类型标注与构造字面量，**不改字段名**；所有消费方编译期/测试期可发现遗漏。

---

## 5. 存储对齐（本轮不改 DDL）

### 5.1 现状

`stock_kline_cache` 已存在且无期货字段（v2.86.0），与 `StockOHLCV` 天然对齐；`kline_cache` 与 `FuturesOHLCV` 对齐。**本轮不修改任何 DDL**。

### 5.2 数据读取路径确认

- 股票面板读取（`get_stock_panel`/`get_csi300_panel`）走 `stock_kline_cache` → 输出 `StockOHLCV`
- 期货面板读取（`get_futures_panel`/`get_futures_ohlcv`）走 `kline_cache` → 输出 `FuturesOHLCV`
- 数据提供层（`FTSDataProvider`）当前返回 DataFrame，契约拆分后 **DataFrame 层不改**，仅逐行 dict 构造处对齐

---

## 6. 验证方案

| # | 验证项 | 方法 | 通过标准 |
|---|--------|------|----------|
| V1 | 类型正确 | `mypy src/` | 无新错误（FusedOHLCV 别名兼容旧 import） |
| V2 | 融合输出市场正确 | `tests/data_sources/test_fusion.py` | 全绿；fuse_row 按 market 返回对应契约 |
| V3 | 现有消费方无遗漏 | `grep -rn "FusedOHLCV" fts/` | 仅剩 Union 别名定义处引用 |
| V4 | CLI 行为不变 | `python -m fts.cli data fuse --symbol <期货代码>` + `<股票代码>` 各跑一次 | 输出字段集合与拆分前一致（期货含 settle/hold，股票含 adjust_factor） |
| V5 | 回归 | `pytest tests/data_sources/ tests/cli/ -v` | 全绿 |

---

## 7. 技术约束与风险

| 风险/约束 | 说明 | 缓解 |
|---|---|---|
| TypedDict 多继承兼容性 | mypy/typing_extensions 版本需支持 | 若失败，退化为扁平结构（共享字段重复声明，靠 V1 类型检查兜底） |
| Union 别名掩盖误用 | 新代码可能继续依赖 FusedOHLCV 的"混合可访问" | 评审强制：新代码一律使用市场契约，FusedOHLCV 仅兼容旧 import |
| FusionMeta 缺失 | FuturesOHLCV 当前无融合元数据，补入后可能影响依赖严格契约的调用方 | 元数据字段均 Optional（total=False），读不到即为缺省 |
| `fts data fuse` 的 market 判定 | CLI 需按 symbol 判定市场（前缀/symbol 表） | 复用 `FTSDataProvider` 现有 market 判定逻辑 |

---

## 8. 文件改动清单

| 文件 | 动作 |
|------|------|
| `fts/core/contracts.py` | **修改** — OHLCVBase/FusionMeta/StockOHLCV 新增，FusedOHLCV 改 Union 别名，FusionReport.rows 更新 |
| `fts/data_sources/fusion.py` | **修改** — 构造方加 market 参数，返回市场契约 |
| `fts/cli.py` | **修改** — `_cmd_data_fuse` import/cast 对齐 |
| `fts/factor_engine/expr_dsl/registry.py` | **修改** — settle 字段注册注明期货专用 |
| `tests/data_sources/test_fusion.py` | **修改/新增** — 按市场契约断言 |
| `tests/cli/test_data_cli.py` | **修改** — fuse 命令市场契约断言 |

---

## 9. 验收标准

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | `StockOHLCV`/`FuturesOHLCV` 定义完成，FusedOHLCV 为 Union 别名 | 代码审查 |
| 2 | fusion.py 构造方按 market 返回正确契约 | V2 |
| 3 | 全部消费方迁移，grep FusedOHLCV 仅剩别名定义 | V3 |
| 4 | 期货/股票 `fts data fuse` 输出字段与拆分前一致 | V4 |
| 5 | 受影响模块测试全绿 | V5 |
| 6 | 字段名零改动（settle/hold/adjust_factor 语义不变） | 代码审查 |

---

## 10. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 关联文档 | [29-storage-convergence-plan.md](file:///d:/Programs/factor_system/docs/harness/plans/29-storage-convergence-plan.md)（六层存储）、[F.2-evolution-engine-fork-design.md](./F.2-evolution-engine-fork-design.md)（引擎分叉） |
| 依赖模块 | `fts/data_sources/fusion.py`、`fts/data_sources/migrate.py`、`fts/cli.py`、`fts/factor_engine/expr_dsl/registry.py` |
| 前置条件 | 存储层已分表（stock_kline_cache 已存在，v2.86.0） |
| 后置影响 | 引擎分叉（F.2）可直接消费 StockOHLCV/FuturesOHLCV；数据供应分离以本契约为接口 |
| 可验证断言 | mypy 无新错误；test_fusion 全绿；grep FusedOHLCV 仅剩别名定义；fuse 命令双市场输出一致 |
| 检验方式 | §6 验证方案 V1-V5 |
