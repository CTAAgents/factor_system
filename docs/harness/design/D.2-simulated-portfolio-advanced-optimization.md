# D.2 模拟交易模块 — 进阶优化方案

> 版本: v3.0.0+5
> 状态: **已实现**（P0.1 股票中性化+L3 补齐 → P0.2 组合级风控 → P1.1 tick 撮合 → P1.2 PARTIAL+标定 → P2 限价单/竞价，2026-08-11）
> 关联: [D.1-simulated-portfolio-design.md](file:///d:/Programs/factor_system/docs/harness/design/D.1-simulated-portfolio-design.md)（模拟仓基础设计）、[C.3-feedback-loop-design.md](file:///d:/Programs/factor_system/docs/harness/design/C.3-feedback-loop-design.md)（反馈闭环）
> 前置: D.1 已实现（SQLite 持久化 + 反馈闭环已上线）

---

## 1. 背景与定位

### 1.1 模块定位（与券商模拟盘 / 头部机构的差异）

FTS 模拟仓是**因子研究闭环的收益核算器**，而非交易执行仿真器：

| 维度 | 主流券商模拟盘 | FTS 模拟仓 |
|------|----------------|------------|
| 本质 | 交易执行仿真器（模拟柜台撮合） | 因子信号 → `position_return` → 演化反馈 |
| 撮合 | 核心目标 | 中间环节（目标是指标核算） |
| 强项 | 撮合规则真实、可练盘 | 因子归因、反馈闭环、无未来函数纪律 |

与头部机构对比，可弥补的差距集中在**方法论类**（统计严谨性、回测保真、风控纪律），而非重资产类（独家数据、低延迟、超算）。

### 1.2 现状与三大缺口

| 缺口 | 现状 | 后果 |
|------|------|------|
| **组合级风控粗放** | 仅单笔限额/单标的/总仓/单日止损/连续亏损（`RiskManager`） | 无组合波动率/相关性/敞口监控，极端行情回撤失控风险 |
| **撮合保真度低** | `_execution_price` 固定 bps 折算，提交即全量成交 | 滑点/容量失真，回测与实盘对齐度不足 |
| **股票 L3 组合层缺失** | 股票管道仅 Ridge 权重+方向校正，未接共享 L3 的 elastic_net/optimizer 中性化/regime | 因子未剥离行业/市值偏好，伪预测力；无 regime 自适应 |

本文档给出这三项缺口的完整解决方案 + 分阶段实施路线。

---

## 2. 总体架构（目标态）

```
                    ┌─────────────────────────────────────────────┐
                    │              模拟交易模块（目标态）            │
                    │                                             │
  ScoredSignal ──►  │ ① 风控层（RiskManager 组合级扩展）            │
                    │    单笔/单标的/总仓 + 波动率/VaR/相关性/敞口  │
                    │              │                              │
                    │ ② 撮合层（bps → tick 盘口仿真，可切换）      │
                    │    bps 降级 ◄── OrderBookMatchingEngine     │
                    │              │                              │
                    │ ③ 组合层（股票 L3 补齐）                     │
                    │    中性化（行业/市值）→ elastic_net 合成      │
                    │    → Regime 自适应权重 → 组合级风控          │
                    │              │                              │
                    │ ④ 核算层（SimulatedPortfolio + SQLite）      │
                    │    盯市/归因/持久化（D.1 已实现，不变）       │
                    └──────────────┬──────────────────────────────┘
                                   ▼
              LiveFeedbackRecord → DuckDB → 衰减判定 → 演化调整
```

设计原则：**分层解耦、每层可独立开关、失败逐级降级**——新增能力不破坏现有闭环。

---

## 3. 组合级风控精化 — 指标监控清单

### 3.1 目标

在既有单笔/单标的/总仓位风控之上，补齐**组合级**风险维度，覆盖"波动、集中度、相关性、敞口、损益、流动性、执行"七个维度，按严重程度分三级预警（WARN / BLOCK / FORCE_CLOSE）。

### 3.2 监控指标清单（七个维度 × 三级预警）

#### 3.2.1 杠杆与仓位维度

| 指标 | 公式/口径 | WARN | BLOCK / FORCE |
|------|-----------|------|---------------|
| 总仓位占比 | `Σ margin_used / equity`（期货）/ `Σ notional / equity`（股票） | > 80% | > 95%（FORCE_CLOSE） |
| 单标的仓位上限 | `notional_sym / equity` | > 15% | > 20%（BLOCK） |
| 单产业链/行业敞口 | `Σ notional_sector / equity`（复用 FUTURES_SECTOR_MAP / 行业映射） | > 40% | > 50%（BLOCK） |
| 保证金占用率 | `margin_used / max_margin_budget` | > 85% | > 95%（BLOCK） |
| 杠杆率（期货） | `Σ notional / equity` | > 3.0x | > 4.0x（BLOCK） |

#### 3.2.2 波动率与尾部风险维度

| 指标 | 公式/口径 | WARN | BLOCK / FORCE |
|------|-----------|------|---------------|
| 组合年化波动率 | 持仓加权 sigma（EWMA 252d，λ=0.94） | > 25% | > 40%（BLOCK 新开） |
| 单日 VaR(95%) | 组合 PnL 历史分位 | 超阈值 | — |
| CVaR(95%) | 尾部均值（超过 VaR 的 PnL 均值） | 超阈值 | — |
| 波动率突变 | `σ_t / σ_{t-20}` | > 2.5x | > 3.5x（BLOCK 新开） |

#### 3.2.3 相关性维度

| 指标 | 公式/口径 | WARN | BLOCK / FORCE |
|------|-----------|------|---------------|
| 持仓两两平均相关 | 收益相关矩阵均值（剔除自身） | > 0.6 | > 0.75（BLOCK 新开） |
| 有效持仓数 | `1 / Σ w_i²`（HHI 倒数） | < 5 | < 3（BLOCK 新开） |
| 最大单对相关 | max corr(i,j) | > 0.85 | > 0.95（BLOCK 新开） |
| 因子暴露集中度 | `Σ |β_factor|` 单因子占比 | > 40% | > 55%（BLOCK） |

#### 3.2.4 损益维度

| 指标 | 公式/口径 | WARN | BLOCK / FORCE |
|------|-----------|------|---------------|
| 单日最大亏损 | `daily_pnl / equity` | < -2% | < -5%（FORCE_CLOSE 全平） |
| 连续亏损次数 | 连续 N 日 PnL<0 | ≥ 5 | ≥ 8（暂停交易） |
| 回撤深度 | `(peak - current) / peak` | > 10% | > 20%（FORCE_CLOSE） |
| 回撤速度 | 单周回撤 | > 6% | > 10%（BLOCK 新开） |
| 盈亏比退化 | 近 20 日 win/loss ratio | < 1.0 | < 0.6（暂停交易） |

#### 3.2.5 流动性维度

| 指标 | 公式/口径 | WARN | BLOCK / FORCE |
|------|-----------|------|---------------|
| 持仓流动性占比 | 单持仓名义 / 该标的日成交额 | > 5% | > 10%（BLOCK） |
| 冲击成本占比 | `impact_cost / expected_pnl` | > 20% | > 35%（BLOCK） |
| 组合换手率（日） | `Σ |trade_notional| / equity` | > 60% | > 100%（BLOCK 新开） |

#### 3.2.6 执行质量维度（tick 撮合后启用）

| 指标 | 公式/口径 | WARN | BLOCK / FORCE |
|------|-----------|------|---------------|
| 滑点偏离 | `(实际均价 − 基准价) / 基准价` vs 理论 bps | > 3x 理论 | > 5x（BLOCK） |
| 部分成交比例 | `Σ unfilled / Σ ordered` | > 10% | > 25%（BLOCK） |
| 成交率 | `Σ filled / Σ ordered` | < 90% | < 70%（BLOCK） |

### 3.3 落地要点

- **数据来源**：组合级指标基于 `SimulatedPortfolio` 内部状态（`_positions`/`_cash`/`_daily_records`）计算，行情缺失按上一收盘价估算，不中断。
- **检测频率**：盘中每个信号/盯市 tick 触发（WARN 记日志）；收盘批量复核（BLOCK/FORCE 决策）。
- **动作语义**：WARN=记录+告警；BLOCK=拒绝新开仓（持仓保留）；FORCE_CLOSE=触发人工干预 `InterventionController.all_close`（权限最高，符合 AGENTS.md 4.3）。
- **复用**：暴露中性化（3.2.3 因子暴露）与 `PortfolioOptimizer`（GAP-L304 `exposure_matrix`）共享同一套暴露矩阵，避免重复计算。

---

## 4. tick 撮合保真 — 详细工程落地方案

### 4.1 目标与边界

将成交价从固定 bps 折算升级为 **tick 级盘口逐档撮合**，滑点由盘口缺口自然产生；无 tick 数据时**逐级降级回 bps**，现有行为零破坏。

### 4.2 分层设计（契约先行）

```
fts/live_trade/
├── book.py           # 新增：盘口契约 + tick→盘口构造
├── matching.py       # 新增：OrderBookMatchingEngine（逐档撮合 + 降级）
├── gateway.py        # 改造：SimulatedGateway 支持 book 模式（可选注入 engine）
└── simulated_portfolio.py  # 改造：_execution_price 增加 book 分发
```

### 4.3 契约层 `book.py`

```python
"""fts.live_trade.book — tick 盘口契约（D.2 新增）。"""

from __future__ import annotations

from typing import TypedDict

class BookLevel(TypedDict, total=False):
    price: float
    quantity: float  # 该档位数量（股/手）

class OrderBookSnapshot(TypedDict, total=False):
    symbol: str
    ts: str                     # 快照时间（ISO）
    bid_levels: list[BookLevel]  # 买盘档（价格降序，最优在前）
    ask_levels: list[BookLevel]  # 卖盘档（价格升序，最优在前）
    last_price: float
    tick_size: float             # 最小变动价位（期货品种已知，股票 0.01）

def build_book_from_ticks(symbol: str, tick_rows: list[dict], depth: int = 5) -> OrderBookSnapshot | None:
    """由 tick 行（含 bid/ask 价量）构造盘口；无有效行返回 None。

    聚合规则：同价位去重累加量，取最优 depth 档。
    tick 行缺失盘口字段时回退 last_price 单档构造（量取 0）。
    """
```

### 4.4 撮合引擎 `matching.py`

```python
"""fts.live_trade.matching — tick 盘口逐档撮合引擎（D.2 新增）。"""

from __future__ import annotations

from typing import TypedDict

from .book import OrderBookSnapshot

class MatchResult(TypedDict, total=False):
    filled_qty: float       # 实际成交数量
    avg_price: float        # 加权成交均价
    unfilled_qty: float     # 深度不足剩余量（>0 表示部分成交）
    slippage_actual: float  # 实际滑点（avg_price vs base_price 的 bps）
    book_used: bool         # 是否走盘口（False=降级 bps）

class OrderBookMatchingEngine:
    """市价单逐档撮合：从对手盘最优档开始消耗，深度不足部分成交。

    降级语义: 盘口缺失/异常/成交价非法时，由调用方回退 bps 路径（本引擎不抛错）。
    """

    def __init__(self, depth: int = 5) -> None: ...

    def match_market(
        self,
        book: OrderBookSnapshot | None,
        side: str,          # "buy" / "sell"
        qty: float,
        base_price: float,  # 基准价（t+1 开盘/实时价），用于滑点计算
    ) -> MatchResult:
        """市价单撮合。

        算法（buy 为例）:
            remaining = qty; cost = 0; filled = 0
            for lv in ask_levels（价格升序）:
                if remaining <= 1e-9: break
                take = min(remaining, lv.quantity)
                cost += take * lv.price; filled += take; remaining -= take
            avg_price = cost / filled（filled=0 时返回降级标志）
            unfilled = remaining
        """
        # ── 实现要点 ──
        # 1. book 为 None / 空档位 → 返回 book_used=False（调用方降级 bps）
        # 2. filled=0（对手盘量为 0）→ 同样降级
        # 3. 成交均价不做人为 bps 偏移；滑点由 avg_price vs base_price 差自然产生
        # 4. qty<=0 防御直接返回全空结果
        ...

    def estimate_impact(self, book: OrderBookSnapshot | None, qty: float) -> float:
        """大单冲击估算：深度不足部分按 √冲击模型（复用 TransactionCostModel.impact_cost）外推。"""
```

### 4.5 网关集成 `gateway.py`

```python
class SimulatedGateway(AbstractGateway):
    def __init__(
        self,
        fill_on_submit: bool = True,
        fail_submit: bool = False,
        reject_rate: float = 0.0,
        latency_seconds: float = 0.0,
        matching: OrderBookMatchingEngine | None = None,  # D.2 新增
    ) -> None:
        self._matching = matching  # None = 保持现状（bps 路径）

    def submit_order(self, order: Order) -> str:
        # D.2：若注入 matching 且订单携带 book 上下文 → 走盘口撮合，部分成交转 PARTIAL
        ...
```

### 4.6 组合层集成 `simulated_portfolio.py`

```python
# _execution_price 增加 book 分发（D.2）:
def _execution_price(self, side: str, base_price: float, slippage_bps: float = 0.0) -> float:
    if self._matching is not None and self._book_provider is not None:
        book = self._book_provider.get_book(symbol)          # 注入的 tick 盘口提供方
        res = self._matching.match_market(book, "buy" if side in (_OPEN_LONG, _CLOSE_SHORT) else "sell",
                                          self._pending_qty, base_price)
        if res.get("book_used"):
            return res["avg_price"]                          # 盘口自然滑点
    slip = slippage_bps / 10000.0                            # 降级：bps 路径（现状不变）
    return base_price * (1 + slip) if side in (_OPEN_LONG, _CLOSE_SHORT) else base_price * (1 - slip)
```

### 4.7 数据链路与降级链

| 层 | 来源 | 降级 |
|----|------|------|
| tick 盘口 | `tick_cache → TQSDK_TICK`（[AGENTS.md](file:///d:/Programs/factor_system/AGENTS.md) 已定义） | 无 tick → bps |
| 期货优先 | 主力合约（RB0/AU0 等），乘数剥离后缀 | 非主力低流动 → 仍取该合约自身盘口 |
| 股票/ETF | 依赖盘口快照源（同花顺/东财 REST），非必须 | 无快照 → bps |
| 撮合异常 | 引擎内部 try/except | 不抛错，返回降级标志 |

### 4.8 测试策略

| # | 场景 | 断言 |
|---|------|------|
| 1 | 逐档消耗 | buy 3 手吃 1+1+1 三档，avg_price=加权均价，unfilled=0 |
| 2 | 深度不足 | buy 5 手仅 3 手盘口 → filled=3, unfilled=2（PARTIAL） |
| 3 | 空盘口/异常 | book=None → book_used=False（降级标志） |
| 4 | 滑点自然性 | 盘口缺口大时 slippage_actual 显著 > 理论 bps，且无人工偏移 |
| 5 | 降级路径 | 未注入 matching → `_execution_price` 输出与现状完全一致 |
| 6 | 部分成交状态机 | PARTIAL → 后续补单 → FILLED（复用 OrderLifecycle） |
| 7 | 契约完整性 | build_book_from_ticks 聚合去重/档位排序/depth 截断 |
| 8 | 回归 | 既有 18 用例全绿（默认 bps 行为不变） |

### 4.9 实证标定（P1）

book vs bps 成交价差对比报告（复用 `scripts/calibrate_impact_cost.py` 思路）：同一信号序列分别走两条路径，统计 avg_price 差异、滑点分布、部分成交频率，产出参数取舍依据。

---

## 5. 股票因子中性化 + L3 组合层补齐 — 具体代码实现

### 5.1 现状确认

- `daily_signal_pipeline.py` Step 4 用 `compute_ridge_weights` + `compute_composite_scores` 合成，**无中性化、无 elastic_net、无 regime**。
- 共享 L3 层（`portfolio_loop.synthesize_signals`）已支持 `elastic_net` / `optimizer`（含 GAP-L304 暴露中性化）/ `ic_weight`，期货已用，股票未接线。
- `evolution_loop.py` 已有 `stock_neutralization` 配置 + 行业/市值映射加载（GAP-S01），但**信号管道未复用**。

### 5.2 A. 因子信号层中性化模块（新增 `fts/factor_engine/neutralization.py`）

```python
"""fts.factor_engine.neutralization — 横截面因子中性化（D.2 股票 L3 补齐）。

在信号层剥离行业/市值偏好，消除因子作为行业/市值 proxy 的"伪预测力"。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def industry_neutralize(
    signal: pd.Series,                       # index=symbol（裸代码）
    industry_map: dict[str, str],            # {symbol: industry}
) -> pd.Series:
    """行业中性化：组内去均值（组内 mean 归零，跨行业量纲保留）。

    - 无行业映射的 symbol：保留原值（不误杀）
    - 行业组内仅 1 只：该股信号归零（无法做相对比较）
    - 全 NaN / 全常数：原样返回
    """
    out = signal.copy().astype(float)
    groups = pd.Series([industry_map.get(s, "unknown") for s in signal.index], index=signal.index)
    for ind, idx in groups.groupby(groups).groups.items():
        if ind == "unknown":
            continue
        vals = out.loc[idx]
        if len(vals) <= 1:
            out.loc[idx] = 0.0
            continue
        mean = np.nanmean(vals)
        if np.isnan(mean):
            continue
        out.loc[idx] = vals - mean
    return out


def size_neutralize(
    signal: pd.Series,
    cap_map: dict[str, float],               # {symbol: log市值}
) -> pd.Series:
    """市值中性化：对 log 市值做 OLS 回归，取残差。

    signal_resid = signal − (a + b * log_cap)
    市值缺失的 symbol：保留原值（不误杀）
    市值方差为 0：返回原值
    """
    out = signal.copy().astype(float)
    caps = pd.Series([cap_map.get(s, np.nan) for s in signal.index], index=signal.index, dtype=float)
    mask = caps.notna() & out.notna()
    if mask.sum() < 5:                        # 样本不足不做回归
        return out
    x = caps[mask].values
    y = out[mask].values
    if np.nanstd(x) < 1e-12:                  # 市值无区分度
        return out
    a, b = np.polyfit(x, y, 1)
    out.loc[mask] = y - (a + b * x)
    return out


def cross_section_neutralize(
    signal_matrix: pd.DataFrame,             # index=date, columns=symbol（原始信号）
    industry_map: Optional[dict[str, str]] = None,
    cap_map: Optional[dict[str, float]] = None,
) -> pd.DataFrame:
    """按交易日逐行施加行业+市值中性化（行业先去均值，再市值回归残差）。

    任一映射缺失/为空 → 对应步骤跳过；两者皆无 → 原样返回（向后兼容）。
    """
    out = signal_matrix.copy()
    for dt, row in out.iterrows():
        r = row.dropna()
        if r.empty:
            continue
        if industry_map:
            r = industry_neutralize(r, industry_map)
        if cap_map:
            r = size_neutralize(r, cap_map)
        out.loc[dt, r.index] = r
    return out
```

### 5.3 B. 股票信号管道接线（`daily_signal_pipeline.py` Step 4a-0 插入）

```python
# ── 4a-0b: 因子信号层中性化（D.2 补齐，剥离行业/市值偏好）──
neutralize = args.neutralize                    # "none" | "industry" | "size" | "both"（新 CLI 参数，默认 "both"）
if neutralize in ("industry", "both", "size"):
    from fts.config.settings import load_cap_map, load_industry_map
    industry_map = _normalize_keys(load_industry_map()) if neutralize in ("industry", "both") else None
    cap_map = _normalize_keys(load_cap_map()) if neutralize in ("size", "both") else None
    cross_section_neutralize(signal_matrix, industry_map, cap_map)
    print(f"      [中性化] {neutralize} 完成（行业/市值偏好已剥离）")
```

> 说明：`load_industry_map` / `load_cap_map` 复用 `evolution_loop.py` GAP-S01 已接入的配置；`_normalize_keys` 复用其键归一化逻辑（`600519.SH` → `600519`）。

### 5.4 C. L3 组合层补齐（`daily_signal_pipeline.py` Step 4c 升级）

将"Ridge 权重 + compute_composite_scores"升级为共享 L3 合成（elastic_net + Regime 自适应权重）：

```python
# ── 4c: 共享 L3 合成（D.2：Ridge → elastic_net + Regime 权重）──
from fts.factor_engine.portfolio_loop import (
    regime_adaptive_weight_adjustment,
    synthesize_signals,
)
from fts.factor_engine.regime_hmm import RegimeAwareSelector   # 股票 regime（GAP-S03 StockRegimeSelector）

print("      L3 合成: elastic_net（L1+L2 自动变量选择）...")
synthesized, _, _ = synthesize_signals(
    factors=[{"factor_id": f["factor_id"], "name": f["name"],
              "sharpe": f.get("sharpe", 0.0), "ic": f.get("ic", 0.0),
              "turnover": f.get("turnover", 0.0), "decay_6m": f.get("decay_6m", 0.0)}
             for f in all_factors],
    mode="elastic_net",
    elite_dir=ELITE_STOCK_DIR,               # 股票精英因子目录（复用期货 pipeline 同款入口）
    market="stock",
)
factor_weights = {s["factor_id"]: s["weight"] for s in synthesized}

# Regime 自适应权重（复用 regime_adaptive_weight_adjustment，股票 regime 由 StockRegimeSelector 提供）
regime = stock_regime_detector.detect(panel, common_dates)   # 复用 GAP-S03
if regime:
    factor_weights = regime_adaptive_weight_adjustment(factor_weights, regime, market="stock")

stock_scores, stock_details = compute_composite_scores(
    signal_matrix, factor_sign_flips, all_factors, factor_weights,
)
```

**可选升级（P1）**：`synthesize_signals` 的 `optimizer` 模式（`optimizer_mode="risk_parity"` + `optimizer_config={"neutralization": "industry"}` + `exposure_matrix`）可在合成层再做组合级暴露中性化——与 5.2 信号层中性化形成"双保险"，但依赖 `returns_matrix`（因子历史收益面板），初期可不启用。

### 5.5 配置项（`fts/config/settings.py` 新增）

| 配置 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `stock_signal_neutralize` | str | "both" | 股票信号管道中性化（none/industry/size/both） |
| `stock_signal_l3_mode` | str | "elastic_net" | 股票 L3 合成模式（elastic_net/optimizer/ic_weight） |

### 5.6 测试策略

| # | 场景 | 断言 |
|---|------|------|
| 1 | 行业中性化 | 同行业两股信号差不变、组均值归零；单股行业归零；无映射保留 |
| 2 | 市值中性化 | 回归残差与 log_cap 相关系数 ≈ 0；市值缺失保留原值 |
| 3 | 全链路 | 中性化后股票信号 IC vs 中性化前对比（行业 proxy 消除，真实 alpha 保留） |
| 4 | elastic_net 接线 | 股票管道产出非零权重因子数 ≤ 20（L3 活跃上限）且 ≤ 因子总数 |
| 5 | regime 调整 | 注入 bull/oscillate 后权重倍率符合 REGIME_FAMILY_MULTIPLIERS 表 |
| 6 | 降级 | 行业/市值映射缺失 → 跳过对应步骤，管道不中断；elastic_net 失败回退 sharpe_weight |

---

## 6. 分阶段实施路线图

| 阶段 | 内容 | 验收标准 | 依赖 |
|------|------|----------|------|
| **P0.1** | 股票信号管道：中性化（5.2+5.3）+ L3 elastic_net/regime 接线（5.4） | 中性化后 IC 报告、非零权重 ≤20、既有管道回归全绿 | 无 |
| **P0.2** | 组合级风控扩展（§3）：指标计算模块 + 三级预警接入 `RiskManager`/`InterventionController` | 7 维度指标全部可算、WARN/BLOCK/FORCE 触发单测通过 | P0.1 |
| **P1.1** | tick 撮合（§4）：`book.py` + `matching.py` + gateway/portfolio 接线（默认 bps 降级） | 8 项撮合测试 + 既有 18 用例回归全绿 | 无 |
| **P1.2** | 实证标定（4.9）：book vs bps 差异报告 + 部分成交状态机完整化 | 标定报告产出、PARTIAL→FILLED 流转通过 | P1.1 |
| **P2** | 限价单排队/撤单时机/集合竞价；与 FDT 执行层联动验证 | 柜台级细节 + 回测-实盘对齐校验 | P1.2 + FDT |

**优先级依据**：股票 L3 缺口直接产生"伪预测力"（影响因子真实性）→ 最高优先；组合级风控防极端行情回撤 → 次之；tick 撮合提升对齐度，但当前反馈闭环瓶颈是数据积累 → 可后置但能力储备先行。

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 中性化过度剥离导致真实 alpha 丢失 | 中性化前后 IC 对比报告 + `stock_signal_neutralize` 可回退 "none" |
| tick 数据源质量差/缺失 | 完整降级链（无盘口→bps），撮合不中断 |
| 组合风控误杀（正常波动触发 FORCE） | 三级阈值全部可配置，默认取保守值；FORCE 走人工干预通道可复核 |
| 改动破坏既有闭环 | 每阶段"默认行为不变"（降级/回退开关），回归测试为硬门槛 |

---

## 8. 文件改动清单（方案预估）

| 文件 | 动作 | 说明 |
|------|------|------|
| `fts/live_trade/book.py` | **新增** | 盘口契约 + tick→盘口构造 |
| `fts/live_trade/matching.py` | **新增** | 逐档撮合引擎（含降级标志） |
| `fts/factor_engine/neutralization.py` | **新增** | 行业/市值横截面中性化 |
| `fts/live_trade/gateway.py` | **修改** | SimulatedGateway 支持 book 模式 |
| `fts/live_trade/simulated_portfolio.py` | **修改** | `_execution_price` book 分发 + 组合风控钩子 |
| `fts/risk/risk_manager.py` | **修改** | 组合级指标接入（波动/VaR/相关/敞口/损益/流动性/执行） |
| `scripts/daily_signal_pipeline.py` | **修改** | 中性化接线 + L3 elastic_net/regime 升级 |
| `fts/config/settings.py` | **修改** | 新增 2 配置项 |
| 测试 | **新增/修改** | neutralization/matching/book/风控指标 4 个新测试文件 + 管道接线测试 |

---

## 9. 实施记录与偏差（2026-08-11 已实现）

各阶段交付与测试见 `docs/harness/07-operations.md`（v2.101.0 变更记录）。**实现偏差**（相对本文方案）：

| # | 方案 | 实际 | 原因与影响 |
|---|------|------|-----------|
| 1 | §5.3 `--neutralize` 默认 `both` | 默认 `none`（显式 `--neutralize both`/env 启用） | "默认行为不变"原则（外科手术式修改），避免存量快照口径突变；中性化能力完整可用 |
| 2 | §5.4 regime 自适应权重接线 | **已实现**（偏差 b 补充，2026-08-11）：`--regime auto`（默认 `none` 保持现状），管道 Step 4b-1 自聚合行业/风格面板 → `StockRegimeSelector.detect` → `apply_stock_regime_weights`（style 维度） | 面板由管道自身 CSI300 面板 + `load_industry_map`/`load_cap_map` 聚合构造（零外部依赖），映射键双端归一化（`600519.SH`→`600519`）；数据不足自动降级不中断；实测 `sector_concentrated (conf=50.00%, method=stock_rule)` |
| 3 | §4.6 portfolio 层部分成交核算 | 撮合走 book 加权均价（已反映盘口成本），持仓核算仍全量成交；gateway 层 PARTIAL→FILLED 状态机完整 | portfolio 层部分成交的持仓核算影响调用方语义，留待真实执行对接（FDT）时统一；能力已在 gateway 层就绪 |
| 4 | §3 组合风控相关性维度 | 用持仓集中度（有效持仓数 HHI）代理 | 模拟仓无 per-symbol 历史收益矩阵；集中度代理已覆盖主要集中度风险 |

---

## 10. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 关联文档 | [D.1-simulated-portfolio-design.md](file:///d:/Programs/factor_system/docs/harness/design/D.1-simulated-portfolio-design.md)、[C.3-feedback-loop-design.md](file:///d:/Programs/factor_system/docs/harness/design/C.3-feedback-loop-design.md)、`AGENTS.md`（4.2 回测对齐 / 4.3 风控红线） |
| 依赖模块 | `fts/live_trade/*`（D.1）、`fts/risk/risk_manager.py`、`fts/factor_engine/portfolio_loop.py`（synthesize_signals/regime_adaptive_weight_adjustment）、`fts/factor_engine/portfolio_optimizer.py`（GAP-L304）、`fts/factor_engine/evolution_loop.py`（GAP-S01 映射加载）、`fts/factor_engine/cost_model.py`（impact_cost） |
| 可验证断言 | ① 股票管道开启中性化后因子 IC 的行业 proxy 分量下降；② `matching.match_market` 深度不足返回 unfilled>0；③ 组合风控 7 维度指标在极端行情模拟中 BLOCK/FORCE 触发；④ 未注入新能力时默认路径输出与 D.1 完全一致 |
| 检验方式 | `pytest tests/ -v` 定向回归 + 中性化 IC 对比报告 + 撮合差异标定报告 |
| 角色边界 | FTS 只模拟核算与信号合成，真实撮合由 FDT 负责；组合风控仅作用于模拟仓，实盘风控权限归属下游 |
