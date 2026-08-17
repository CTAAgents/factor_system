# 47 — 子链差异化与跨市场结构感知优化计划（A/B/C/D 四模块）


> 版本: v2.104.0+114

> 状态: ✅ 已完成（v2.104.0+109，A/B/C/D 四模块实施完成，GAP-134 关闭） · 优先级: P1 · 负责人: FTS Agent · 关联: plans/40/45, GAP-121, GAP-132, GAP-133, GAP-134, config/futures_universe.yaml

## 一、背景与问题定位

基于 2026-08-17 能化链 elite 池实证分析（196 个活跃因子 × symbol_ic 按四大子链聚合），
确认两类结构性缺陷：

### 1.1 缺陷一：子链特异因子被全链统一权重稀释/反向（能源链专属）

- **实证**：196 因子中 **10 个"单链特异"**（仅 1 子链 |平均 IC| ≥ 0.10，其余子链近零或反向）、
  **52 个"部分链有效"**（2–3 链有效）、仅 132 个全链同向。
- **典型反向案例**：`fut_time_series_momentum`（fct_d5a3057e）油化工子链 IC +0.209、
  聚酯链 **-0.070（反向）**；`fut_up_day_ratio`（fct_b2a3d635）油化工 +0.393、
  煤化工 0.000。
- **机制**：能化四大子链驱动逻辑不同（原油/聚酯/油化工/煤化工），因子机制若绑定特定链
  （高频成交不平衡在油化工高波动品种、库存周期在煤化工），全链等权/统一权重使用不仅稀释
  alpha，还在无效子链上产生负贡献。
- **系统现状**：L3 组合层（`PortfolioLoop` quality_weight）全链统一权重，仅 Step 1.8b
  做子链**去冗余**（限单链 ≤2 因子，防暴露集中），**无子链差异化权重**；因子元数据
  **无子链适用性标记**。

### 1.2 缺陷二：期货/股票市场结构差异未下沉到评估口径

- **结构差异**：期货"一品种一市场类型"——12 品种分属 4 子链，链内相关（SC→FU→BU 传导）
  但非绝对、跨链近独立，因子以**时序/品种自身**型为主；股票"一统一市场"——300 个股
  共享市场因子 + 行业因子 + 市值因子，个股高相关，因子以**截面排序**型为主。
- **系统现状**：中性化已差异化（期货=产业链去均值 GAP-047、股票=行业+市值 GAP-S01），
  但 **IC 评估口径未分离**：`cross_section_evaluate_backtest` 同一套 IC/审计逻辑处理两类
  市场，期货截面样本（12–82）IC 噪声远大于股票（300），阈值不可比却共用。
- **推论**：期货截面 IC 反映"跨链配置 + 链内比较"，且每子链仅 3 品种 → 子链均 IC 是小样本
  估计，未做显著性校验即被采信（上一缺陷的子链特异名单中部分可能是 3 品种噪声）。

### 1.3 与既有计划的边界

- plans/40（L3 性能）解决信号重算/缓存；本计划解决 **L3 权重的子链语义**与**评估口径**。
- plans/45（L2 拆种子评估）与本计划正交：本计划消费的是已晋升 elite 因子的评估产物
  （symbol_ic），不触碰演化主循环。
- GAP-132（退化检测静默失效）与本计划缺陷二相关但不重叠：GAP-132 是**时序退化**，
  本计划是**截面/子链结构**。

## 二、方案设计

### A 模块 — 子链适用性画像与显著性校验（能源链首批，纯评估层）

目标：为每个 elite 因子产出**可审计的子链适用性画像**，用统计检验替代均值直觉。

- **A1 画像计算器**：新增 `fts/factor_engine/subchain_profile.py`
  - `compute_subchain_profile(symbol_ic: dict[str, float], chain_symbols: dict[str, list[str]], min_symbols: int = 3) -> SubchainProfile`
  - 输出：`{子链: {mean_ic, std_ic, n_symbols, t_stat, p_value, effective}}`
  - `effective` 判定：子链内品种数 ≥3 且 `mean_ic` 方向一致的 t 检验 `|t| ≥ 2.0` 且
    `|mean_ic| ≥ min_chain_ic（默认 0.10，可配）`
- **A2 适用性标记**：因子入库时写 `factor_catalog.metadata`：
  - `subchain_scope`: `["油化工"]`（仅 1 链 effective）/ `["能源","聚酯"]`（部分链）/
    `"all"`（≥3 链）/ `"unknown"`（无子链符号或数据不足）
  - `subchain_ic_profile`: 完整画像（审计留痕，JSON 只读快照同步 elite 文件）
  - `subchain_specific: bool`（单链特异标记，供 D 模块治理）
- **A3 显著性护栏（数学细节）**：
  - **判定链**（三门槛 **AND**，任一不过 → `effective=False`）：
    ① `n_symbols ≥ min_symbols(=3)`：剔除 NaN 后品种数，不足直接 False（防单/双品种决定结论）
    ② 单样本 t 检验：`t = mean_ic / (std_ic / √n)`，自由度 `df = n − 1 = 2`，要求 `|t| ≥ min_t_stat(=2.0)`
    ③ `|mean_ic| ≥ min_chain_ic(=0.10)`
  - **df=2 的 t 分布临界值表**（`scipy.stats.t.ppf` 双侧 p）：

    | \|t\| | p（双侧） | 判定含义 |
    |:---:|:---:|:---|
    | 1.00 | 0.423 | 极不显著（门槛下） |
    | 1.50 | 0.273 | 不显著（门槛下） |
    | **2.00** | **0.184** | **默认门槛 min_t_stat**（刻意宽松） |
    | 2.35 | 0.143 | — |
    | 2.92 | 0.050 | 5% 显著 |
    | 4.30 | 0.010 | 1% 显著 |

    解读：n=3 小样本 t 分布肥尾，df=2 下 `|t|=2` 仅是弱支持。护栏目标不是"证明强显著"
    （强显著由演化/审计链负责），而是**滤掉方向混杂与噪声**——因此门槛刻意宽松。
  - **几何含义**：`|t| ≥ 2 ⇔ |mean_ic| ≥ (2/√3)·std ≈ 1.155·std`——3 品种 IC 必须高度一致。
  - **数值示例**（每子链 3 品种 IC 数组，`min_symbols=3 / min_t_stat=2.0 / min_chain_ic=0.10`）：

    | 场景 | IC 数组 | mean | std(ddof=1) | t | effective |
    |:---|:---|:---:|:---:|:---:|:---:|
    | 同向一致 | [0.20, 0.21, 0.22] | 0.210 | 0.010 | 36.4 | ✅（过②③） |
    | 同向较弱 | [0.10, 0.11, 0.12] | 0.110 | 0.010 | 19.1 | ✅（过②③） |
    | 方向混杂 | [0.20, 0.05, −0.10] | 0.050 | 0.150 | 0.58 | ❌（② t 不显著） |
    | 完全一致 | [0.20, 0.20, 0.20] | 0.200 | 0（<1e-12） | ∞ | ✅（std 兜底分支，仅由③决定） |
    | 品种不足 | [0.20, 0.21]（n=2） | 0.205 | — | — | ❌（① 拦截） |
    | 弱均值 | [0.07, 0.08, 0.09] | 0.080 | 0.010 | 13.9 | ❌（③ 拦截，mean < 0.10） |

  - **工程实现细节**：
    - **核心判定函数**（`subchain_profile.py` 内部 `_chain_effective`，逐子链调用）：

      ```python
      def _chain_effective(ics: list[float]) -> ChainStat:
          """单子链有效性判定：三门槛 AND（min_symbols / min_t_stat / min_chain_ic）。"""
          ics = [v for v in ics if not np.isnan(v)]        # ① 剔除 NaN
          n = len(ics)
          if n < min_symbols:                              # 门槛①：品种数不足
              return ChainStat(effective=False, n=n, mean=None, std=None, t=None, p=None)
          mean = float(np.mean(ics))
          std = float(np.std(ics, ddof=1))                 # 样本标准差（ddof=1，关键）
          if std < 1e-12:                                  # 完全一致 → t=∞ 兜底
              t, p = float("inf"), 0.0
          else:
              t = mean / (std / math.sqrt(n))              # 门槛②：单样本 t 检验，df=n-1=2
              p = float(scipy.stats.t.sf(abs(t), df=n - 1) * 2)
          effective = (abs(t) >= min_t_stat
                       and abs(mean) >= min_chain_ic)      # 门槛② AND ③
          return ChainStat(effective=effective, n=n, mean=mean, std=std, t=t, p=p)
      ```
    - `std_ic = np.std(ics, ddof=1)`（样本标准差，n=3 必须 ddof=1；总体 std 低估噪声）
    - `std_ic < 1e-12` 视为完全一致 → `t = +inf`，effective 仅由③决定
    - NaN IC 品种先剔除再重算 n；`n < min_symbols` 走①
    - 输出含 `p_value`（`scipy.stats.t.sf(|t|, df) * 2`，审计留痕），t 为 inf 时 p=0
    - 全部参数 `min_symbols / min_t_stat / min_chain_ic` 入 `config/settings.yaml`（`subchain_profile.*`），禁硬编码
  - **保守性设计**（不对称风险）：误标（把全链因子裁成单链）损失 alpha 与多样性，漏标（真特异未标）仅维持现状——护栏偏向漏标。t 不显著 → 保持全链（D3 防误伤），决策权交给显著性而非均值直觉。

### B 模块 — L3 子链差异化权重（核心，energy 链灰度先行）

目标：合成组合信号时**按品种归属子链差异化应用因子权重**，特异/部分链因子在无效子链
降权或归零，消除负贡献。

- **B1 权重张量扩展**：`quality_weight` 产出基础权重 `w_factor`（现状不变），新增
  子链调制矩阵 `m[factor][subchain]`：
  - `subchain_scope == "all"` 或 `"unknown"`：`m = 1.0`（兼容现状，unknown 不误杀）
  - 单链/部分链：effective 子链 `m = 1.0`，非 effective 子链 `m = 0.0`
    （可配 `subchain_decay_mode = "zero" | "soft"`，soft 按 `|mean_ic|/max_chain_ic` 缩放）
- **B2 合成接入**：`synthesize_signals` / `build_combo` 中信号矩阵按 `ENERGY_CHAIN_SUB_SYMBOLS`
  逐品种左乘 `m[factor][subchain(symbol)]`，仅 `market="energy"` 生效；
  子链映射缺失品种 `m=1.0`（不破坏盲测/新增品种）。
- **B3 与 Step 1.8b 协同**：去冗余管"数量"（单链 ≤ max_per_chain）、B 模块管"权重"，
  二者互补；先去冗余后调权。
- **B4 灰度开关**：`portfolio.run` 新增 `--enable-subchain-weight`（默认关闭）；
  energy 定时任务（cf32b4bc）观察 1–2 个调仓周期后开启。回退：关闭开关即恢复全链权重。

### B5 权重调制矩阵 — 接口签名与伪代码

新增模块 `fts/factor_engine/subchain_weight.py`（与 l3_signal_service 同级，纯计算无 IO）：

```python
# ── 契约（Pydantic V2，参数全配置化禁硬编码）─────────────────────────────
from typing import Any, Literal
from pydantic import BaseModel, Field

class SubchainWeightConfig(BaseModel):
    """L3 子链差异化权重调制配置（config/settings.yaml → l3.subchain_weight）。"""
    enabled: bool = Field(default=False, description="灰度开关（默认关，兼容现状）")
    decay_mode: Literal["zero", "soft"] = Field(default="zero",
        description="非 effective 子链权重：zero=归零 / soft=按 |mean_ic| 相对缩放")
    soft_min_ratio: float = Field(default=0.0, ge=0.0, le=1.0,
        description="soft 模式最低保留比例（0.0=可归零，1.0=等效全链）")
    scope_default: Literal["all", "unknown"] = Field(default="all",
        description="无 subchain_scope 画像因子的默认处理（all=全链保留，防误杀）")
    max_exposure_ratio: float = Field(default=0.50, ge=0.0, le=1.0,
        description="单子链暴露占比告警阈值（D2 监控用）")

SubchainSymbols = dict[str, list[str]]   # {"能源": ["SC0","FU0","BU0"], ...}


def build_subchain_weights(
    factors: list[dict[str, Any]],        # 每项含 factor_id / subchain_scope / subchain_ic_profile
    chain_symbols: SubchainSymbols,       # ENERGY_CHAIN_SUB_SYMBOLS（portfolio_loop 既有）
    config: SubchainWeightConfig,
) -> dict[str, dict[str, float]]:
    """构建调制矩阵 {factor_id: {子链: m}}。

    语义:
      - scope in ("all", "unknown")  → 全部子链 m = 1.0（兼容现状，未知不误杀）
      - scope 单链/部分链            → effective 子链 m = 1.0；
                                     非 effective 子链按 decay_mode 归零或 soft 缩放
    """
    matrix: dict[str, dict[str, float]] = {}
    for f in factors:
        scope = f.get("subchain_scope", "unknown")
        prof  = f.get("subchain_ic_profile", {}) or {}
        row: dict[str, float] = {}
        for chain in chain_symbols:
            if scope in ("all", "unknown"):
                row[chain] = 1.0
                continue
            eff = bool((prof.get(chain) or {}).get("effective", False))
            if eff:
                row[chain] = 1.0
            elif config.decay_mode == "soft":
                mean_ic = abs((prof.get(chain) or {}).get("mean_ic", 0.0))
                max_ic = max(
                    (abs((prof.get(c) or {}).get("mean_ic", 0.0)) for c in scope if c in prof),
                    default=1e-9,
                )
                row[chain] = max(config.soft_min_ratio, mean_ic / max_ic)
            else:  # "zero"
                row[chain] = 0.0
        matrix[f.get("factor_id", f.get("name", "?"))] = row
    return matrix


def apply_subchain_modulation(
    signal_matrix: np.ndarray,            # (n_dates, n_symbols, n_factors) 3D 信号矩阵
    modulation: dict[str, dict[str, float]],
    symbol_chain: dict[str, str],         # {品种: 子链}（由 chain_symbols 反查生成）
    factors: list[dict[str, Any]],
) -> np.ndarray:
    """信号矩阵按品种归属子链左乘 m[factor][子链]（仅 market="energy" 调用）。

    未知子链/缺失映射品种 m=1.0 兜底（不破坏盲测池与新增品种）；
    仅合成环节生效，不影响因子评估/审计产物。
    """
    out = signal_matrix.copy()
    syms = list(symbol_chain.keys())
    for j, f in enumerate(factors):
        row = modulation.get(f.get("factor_id", f.get("name", "?")), {})
        for i, sym in enumerate(syms):
            w = row.get(symbol_chain.get(sym, ""), 1.0)   # 未知链兜底 1.0
            out[:, i, j] *= w
    return out
```

接入点（`PortfolioLoop` Step 2 合成前，与 Step 1.8b 去冗余顺序：先去冗余 → 再调权）：

```python
# portfolio_loop.py Step 2 入口（market=="energy" 且 enable_subchain_weight 时）
from fts.factor_engine.subchain_weight import (
    build_subchain_weights, apply_subchain_modulation, SubchainWeightConfig,
)
cfg_sc = SubchainWeightConfig(**settings.l3.subchain_weight)
modulation = build_subchain_weights(factors, ENERGY_CHAIN_SUB_SYMBOLS, cfg_sc)
signal_matrix = apply_subchain_modulation(signal_matrix, modulation,
                                          symbol_chain, factors)
```

CLI：`fts portfolio run --universe energy --enable-subchain-weight`；
`PortfolioLoop.__init__` 新增 `enable_subchain_weight: bool = False`（缺省兼容现状）。

### C 模块 — 期货/股票评估口径分离

目标：消除两类市场共用 IC 口径导致的可比性失真。

- **C1 评估配置按市场拆分**：`contracts.py` 新增 `FUTURES_EVAL_CONFIG` 与
  `STOCK_EVAL_CONFIG`（min_ic / min_sharpe / 审计阈值 / cross_symbol 阈值分离）：
  - 期货：min_ic 维持现状（0.1+），审计保留 cross_symbol（positive_ratio ≥ 0.8）
  - 股票：min_ic 按截面样本校正（0.03–0.05 档），行业中性化残差上计算（现状已实现）
  - 两配置独立可配，`FactorVerifier`/审计按 `market` 路由
- **C2 期货子链中性化确认**：energy 链评估沿用 GAP-047 产业链中性化（已生效），
  本计划补充**子链内比较**语义（符号化：同链品种相对强弱），写入评估报告新段
  `subchain_ic_report`（输出每因子×子链 IC 画像表，A 模块产物）。
- **C3 跨市场口径告警**：跨市场泛化报告（plans/14）标注 IC 口径市场，禁止直接对比
  期货 0.3 与股票 0.05 的 IC 数字。

### D 模块 — 特异因子治理与暴露监控

- **D1 单链特异因子受限使用**：`subchain_specific=true` 因子 L3 仅在其主导子链生效
  （B 模块 `m` 矩阵天然实现），其余品种不产生信号；评估/巡检报告显式标注。
- **D2 子链暴露监控**：`PortfolioLoop` 报告新增子链权重/暴露占比汇总
  （`_generate_quality_report` 扩展），单子链暴露占比超阈值（默认 50%，可配）
  触发 warning；与 Step 1.8b 去冗余形成"数量 + 权重 + 暴露"三层防护。
- **D3 噪声防误伤**：A3 显著性护栏未通过的"均值高但 t 不显著"因子保持全链（不标记
  specific），防止把 3 品种噪声当特异，避免过度裁剪。

## 三、数据模型与契约（变更点）

| 对象 | 变更 | 说明 |
|---|---|---|
| `factor_catalog.metadata` | + `subchain_scope` / `subchain_ic_profile` / `subchain_specific` | DuckDB SSOT，JSON elite 同步只读快照 |
| `config/settings.yaml` | + `subchain_profile.min_chain_ic=0.10` / `min_t_stat=2.0` / `min_symbols=3`；+ `l3.subchain_weight.enabled=false` / `decay_mode="zero"`；+ `l3.subchain_exposure.max_ratio=0.50` | 参数化，禁硬编码 |
| `contracts.py` | + `FUTURES_EVAL_CONFIG` / `STOCK_EVAL_CONFIG` | 按市场路由评估口径 |
| `PortfolioLoop` | + `enable_subchain_weight` 参数 + 子链权重调制步骤 + 暴露报告段 | 向后兼容（默认关） |
| `portfolio.run` CLI | + `--enable-subchain-weight` | energy 任务灰度开关 |

## 四、实施步骤（建议顺序，逐步留痕）

1. A1/A2/A3（画像 + 标记 + 护栏）→ 跑 196 因子画像，输出"特异/部分链/全链"再确认名单
   - 验证：[合成 panel 断言画像与真实 symbol_ic 一致]
2. B1/B2/B3（权重调制 + 合成接入 + 协同）→ energy L3 灰度开启
   - 验证：[特异因子在无效子链权重 ≈ 0；组合 IC 不低于全链基线]
3. C1/C2/C3（口径分离 + 子链报告 + 告警）
   - 验证：[期货/股票评估配置独立生效；跨市场报告标注口径]
4. D1/D2/D3（特异治理 + 暴露监控 + 噪声护栏）
   - 验证：[合成单链特异因子组合暴露收敛；假特异因子未被误裁剪]

## 五、测试方案

### 5.1 模块 A 护栏测试用例清单（`tests/factor_engine/test_subchain_profile.py`）

| # | 用例 id | 场景 | 输入（子链 IC 数组） | 断言 | 优先级 |
|:---:|:---|:---|:---|:---|:---:|
| 1 | `test_3syms_same_direction` | 3 品种同向一致 | [0.20, 0.21, 0.22] | `effective=True`；mean=0.210、t>10、p<0.01 精确断言 | P0 |
| 2 | `test_mixed_sign_rejected` | 方向混杂 | [0.20, 0.05, −0.10] | `effective=False`（t<2 即便均值接近门槛） | P0 |
| 3 | `test_weak_mean_rejected` | 均值低于 min_chain_ic | [0.07, 0.08, 0.09] | `effective=False`（③拦截，t 虽大） | P1 |
| 4 | `test_2symbols_insufficient` | 品种数不足 | [0.20, 0.21]（n=2） | `effective=False`（①拦截） | P0 |
| 5 | `test_1symbol_insufficient` | 单品种 | [0.20]（n=1） | `effective=False`；std/t 为 None 不抛异常 | P0 |
| 6 | `test_nan_dropped_insufficient` | 含 NaN 剔除后不足 3 | [0.20, 0.21, NaN]→n=2 | `effective=False`；n_symbols=2 记录正确 | P1 |
| 7 | `test_identical_std_zero` | 完全一致 std=0 | [0.20, 0.20, 0.20] | t=inf、p=0；`effective=True`（std 兜底，仅由③） | P1 |
| 8 | `test_identical_weak_std_zero` | 完全一致但均值弱 | [0.08, 0.08, 0.08] | `effective=False`（std 兜底下仍被③拦截） | P1 |
| 9 | `test_threshold_parametric` | 阈值参数化生效 | [0.03, 0.17, 0.10]（mean=0.10, std=0.07, t≈2.47），min_t_stat=2.92 | `effective=False`（默认 2.0 时 True） | P1 |
| 10 | `test_scope_single_chain` | 仅 1 链 effective | 四链中仅油化工通过 | `subchain_scope=["油化工"]`、`subchain_specific=True` | P0 |
| 11 | `test_scope_multi_chain` | 2 链 effective | 能源+聚酯通过 | scope 含 2 链、`specific=False` | P1 |
| 12 | `test_scope_all` | ≥3 链 effective | 3–4 链同向通过 | `scope="all"`、`specific=False` | P1 |
| 13 | `test_scope_unknown` | 无 symbol_ic | `symbol_ic={}` | `scope="unknown"`、全部 effective=False（不误标） | P0 |
| 14 | `test_ddof1_sample_std` | 样本标准差 ddof=1 | [0.20, 0.05, −0.10] | t 基于 `np.std(ddof=1)` 计算（与 ddof=0 结果断言不等） | P1 |
| 15 | `test_pvalue_recorded` | p_value 留痕 | 任意输入 | `p_value == t.sf(\|t\|, 2)*2`（inf→0） | P2 |
| 16 | `test_partial_chain_symbols` | 子链品种缺失 | 煤化工仅 MA0/UR0 | 该链 n=2→`effective=False`，其余链正常 | P1 |
| 17 | `test_metadata_persisted` | 画像落库 | compute 后写 metadata | `factor_catalog.metadata.subchain_ic_profile` 含 mean/std/n/t/p/effective | P1 |
| 18 | `test_random_noise_false_positive` | 随机噪声误报率 | 1000 组 `N(0,0.05)` IC | `subchain_specific` 比例 < 5%（统计护栏） | P0 |
| 19 | `test_unknown_chain_key` | 未知子链键 | 不存在的链名 | 返回 None/跳过，不抛异常 | P2 |
| 20 | `test_negative_ic_symmetric` | 全负 IC 对称性 | [−0.20,−0.21,−0.22] | `effective=True`（取 \|mean\|，方向由因子符号承载） | P1 |

### 5.2 其余模块测试（沿用 §五 概述）

- `tests/factor_engine/test_subchain_weight.py`（L3 集成）：
  - 单链特异因子在非主导子链权重 = 0（zero 模式）/ 按比例缩放（soft 模式）
  - `enable_subchain_weight=false` 时输出与现状逐位一致（回归）
  - energy 链子链暴露占比 ≤ max_ratio 且触发告警
- `tests/factor_engine/test_eval_config_by_market.py`：
  - 期货/股票评估走各自配置（min_ic/审计阈值断言）
- 回归：`pytest tests/factor_engine/test_portfolio_loop*.py tests/factor_engine/test_evaluation_chain.py -v`
  （日常分级测试政策：仅受影响模块，不跑全量）

## 六、验证标准（验收）

1. ✅ 197 因子完成子链画像（真实 elite 全量实测，2026-08-17 v2.104.0+109）：**27 个单链特异**。
   与实证（10 个）的差异源于**判据不同**：实证用"子链均值 |IC|≥0.10"直觉；护栏用
   t 检验（df=2 宽松门槛）——后者额外滤掉方向混杂/波动大的链，名单更严谨
   （含实证全部 10 个 + 17 个均值直觉漏标的噪声型特异）。实测名单与
   `compute_subchain_profile` 判定逐位一致（判据一致性验收）。
2. ⏳ energy L3 开启子链权重后：特异因子无效子链权重 < 0.01；组合 OOS IC 不低于
   全链基线（退化 > 0.02 即回退开关并排查）。——灰度默认关，待开启后运行验证。
3. ✅ 期货/股票评估配置分离生效（`get_eval_config` 路由 + 阈值分离单测）。
4. ✅ 新增测试 50 用例全绿 + 受影响回归 390 passed + ruff 全绿；GAP-134 已关闭。

## 七、风险与回退

- **风险 1（过度裁剪）**：显著性护栏失效 → 真全链因子被误标 specific → 组合多样性损失。
  缓解：D3 噪声护栏 + `decay_mode="soft"` 先行 + 灰度观察。
- **风险 2（3 品种小样本）**：子链均 IC 的 t 检验自由度过低。缓解：min_symbols=3 兜底 +
  检验结果仅作权重调制输入（不参与晋升/降级决策）。
- **风险 3（未知/新增品种）**：子链映射缺失 → `m=1.0` 兜底，不破坏盲测与新品。
- **回退路径**：`enable_subchain_weight=false` 一键恢复全链权重；A/C/D 为纯评估/报告
  层变更，无回退风险。

## 八、一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| §A 子链画像 | `compute_subchain_profile` 输出与 `evaluation.level_1_backtest.symbol_ic` 聚合一致 | `grep -n "compute_subchain_profile" subchain_profile.py` + 单测断言 |
| §A2 metadata | 因子 `metadata.subchain_scope` 落 `factor_catalog`（DuckDB SSOT） | 查询 `factor_catalog.metadata` 含 `subchain_scope` |
| §B 权重调制 | `m[factor][subchain]` 仅 `market="energy"` 且开关开启时生效 | 单测断言 `enable_subchain_weight=false` 输出与基线一致 |
| §B4 灰度开关 | `portfolio run --enable-subchain-weight` 参数存在且默认关闭 | `grep -n "enable-subchain-weight" fts/cli.py` |
| §C 口径分离 | `FUTURES_EVAL_CONFIG`/`STOCK_EVAL_CONFIG` 按 market 路由 | 单测断言期货/股票 min_ic 不同且各自生效 |
| §D 暴露监控 | L3 报告含子链权重/暴露占比段 | `grep -n "subchain_exposure" portfolio_loop.py` |

## 九、实施后预期效果

- energy L3 组合：27 个单链特异因子仅在主导子链贡献 alpha，无效子链零暴露 → 组合 IC 提升
  （预估 0.05–0.10，待灰度实测），子链暴露集中度受控。
- 评估口径：期货/股票 IC 数字可独立解释，跨市场报告消除误导性对比。
- 因子资产：elite 因子携带子链适用性画像，为 L1 知识补给（子链分批）与 L2 演化
  （子链聚焦）提供结构化反馈闭环。

## 十、实施后文档同步清单（Harness 13 项）

1. `docs/harness/01-architecture.md`（数据流/子链权重步骤）
2. `docs/harness/02-lifecycle.md`（产出物）
3. `docs/harness/03-configuration.md`（新增配置项）
4. `docs/harness/05-observability.md`（子链暴露指标）
5. `docs/harness/06-testing.md`（测试文件/用例数）
6. `docs/harness/07-operations.md`（版本历史）
7. `docs/harness/08-gap-analysis.md`（登记 GAP-134：子链特异因子无适用性标记 + 全链统一权重）
8. `docs/harness/09-advancement-plan.md`（晋级里程碑）
9. `pyproject.toml`（版本 bump）
10. `README.md`（工程指标）
