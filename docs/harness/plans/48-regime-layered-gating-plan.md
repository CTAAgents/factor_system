# 48 — Regime 分层方向 Gate 与品种暴露缩放计划（A/B/C/D 四模块）

> 版本: v3.0.0+6

> 状态: ✅ 已完成（v2.104.0+111，A/B/C/D 四模块全部实施） · 优先级: P1 · 负责人: FTS Agent · 关联: plans/47（子链差异化权重，已完成）, GAP-136（登记并关闭）, regime.py / futures_signal_pipeline.py / portfolio_loop.py

## 一、背景与问题定位

### 1.1 核心洞察：期货 Regime 的价值高于股票，且应分"方向 × 收益来源 × 实现手段"三层

期货品种是**独立市场类型**（一品种一市场，跨链相关低），其 Regime（趋势/波动/基差结构）
直接提供该市场的**独立定价语境**——既回答"这品种方向对不对、该不该参与"（**能否赚钱**），
也定义"该市场状态下存在哪些可被提取的收益来源"（**赚什么钱**：趋势跟随收益 / 反转收益 /
carry 展期收益 / 波动率溢价）；而股票个股无独立 Regime（行业/个股制度是市场因子的投影），
行业/个股 Regime 的增量信息弱一个维度。因此：

- **第 1 层（Regime）决定"能否赚钱 + 赚什么钱"**：
  - 能否：品种/子链的方向判定与参与 Gate（long/short/avoid，含置信度连续化）
  - 赚什么钱：该 regime 下激活的**收益来源族**（趋势/反转/carry/波动率溢价）——
    收益来源由市场状态定义，Regime 决定"这里有什么钱可赚"
- **第 2 层（因子）决定"用什么办法赚钱 + 赚多少"**：
  - 什么办法：收益来源族内的**实现工具**——同一趋势收益可由 5 日/22 日/基差动量等
    不同因子提取；因子选择（IC/换手/容量/子链适用性）决定用哪个工具最有效率
  - 赚多少：因子权重 × 子链调制（plans/47）决定同一来源下的收益幅度

因果结构：**收益来源是被提取的"目标"（Regime 定义），因子是提取它的"工具"（手段+效率）**。
两层应**正交**：Regime（方向 Gate + 来源族激活）→ 因子（工具选择 + 权重调制）。

### 1.2 系统现状：检测已具备，消费方式是"合成 + 微调"，缺"独立 Gate"

| 现有能力 | 位置 | 当前消费方式 | 缺口 |
|---|---|---|---|
| 子链级 Regime + 置信度 | `SectorRegimeSelector.detect_all`（regime.py L664） | ① 软票合成全局主制度（品种数×置信度）② 品种-链对齐度 | **未按子链独立 Gate**（低置信度子链不回避） |
| 品种级 Regime | `SectorRegimeSelector.compute_alignment`（`_variety_selectors[sym].detect`） | 仅算对齐度 → 信号权重 ±0.20 blend 微调 | **未映射暴露缩放**（品种置信度不决定仓位） |
| 全局主制度方向偏置 | `_apply_regime_direction_bias`（signal_pipeline L435） | 全局 bear/bull 乘法偏置（bias = 0.30 × confidence） | **全局单制度**——子链差异被抹平 |
| 因子 Style×制度激活 | `REGIME_STYLE_MULTIPLIERS`（portfolio_loop L365，FactorStyle 维度；v2.104.0+25 family 维度已移除） | 全局制度下 style 乘数（`regime_adaptive_weight_adjustment` 消费） | **未下钻子链**（该链 bear 下哪些 style/来源有效未知） |

### 1.3 问题影响（实证语境）

- 子链分歧：能化四子链（能源/聚酯/油化工/煤化工）驱动逻辑不同，某日可能能源 bull、
  煤化工 bear——全局软票合成会**中和掉分歧信息**，方向偏置对两边同时作用（错误的全局干预）。
- 置信度浪费：品种级 Regime 置信度已算（compute_alignment），仅用于 ±0.20 对齐度微调，
  未发挥"高置信度满暴露、低置信度收缩"的仓位职能。
- 与 plans/47 割裂：plans/47 已实现"特异因子在无效子链降权"（幅度层），但**方向层无 Gate**——
  "regime 决定能不能做 × 因子决定做多少"只完成了一半。

### 1.4 与 plans/47 的边界（方向/来源层 vs 手段/幅度层）

- plans/47（已完成）：**手段 + 幅度层**——因子在子链的适用性（subchain_weights 调制）
  回答"用什么办法（哪个因子）赚、赚多少（权重）"。
- 本计划（48）：**方向 + 收益来源层**——Regime 的参与 Gate（能否）与收益来源族激活
  （赚什么钱），其中 C 模块定义"该子链该 regime 下激活哪些收益来源族"。
- 三层串联：Regime Gate（48 A）→ 收益来源族激活（48 C，赚什么钱）→
  因子工具选择与权重调制（47，什么办法 + 赚多少），构成"能否 × 赚什么 × 怎么赚 × 赚多少"正交。

## 二、方案设计

### A 模块 — 子链级方向 Gate（参与判定，核心）

> ✅ 已实施（v2.104.0+111）：新增 `fts/factor_engine/regime_gate.py`（`GateConfig`/`GateDecision`/
> `build_symbol_chain_map`/`build_subchain_gates`/`apply_subchain_gate`），信号管线 `main()` Step 3h1
> 接入（仅 `--chain energy` 且 `regime_gating.enabled=true` 生效，失败降级不阻断主流程），
> settings.yaml `l3.regime_gating` 参数化。测试 `tests/factor_engine/test_regime_gate.py`（A 模块 13 用例）。

目标：把 `detect_all` 产出的子链 Regime 从"合成全局"升级为"**子链独立参与 Gate**"。

- **A1 Gate 判定器**：新增 `fts/factor_engine/regime_gate.py`
  - `build_subchain_gates(sector_regimes, chain_symbols, config) -> {子链: GateDecision}`
  - `GateDecision = {regime, confidence, decision}`，decision ∈ `"long" | "short" | "avoid" | "neutral"`：
    - bull 且 `confidence ≥ min_confidence` → `long`
    - bear 且 `confidence ≥ min_confidence` → `short`
    - `confidence < min_confidence` → `avoid`（方向不明确不参与，与全局制度解耦）
    - 其余（oscillate/low_vol 等）→ `neutral`（不 gate，交因子层决定）
- **A2 信号管线接入**：energy 链模式下，`_compute_composite_scores` 按品种归属子链应用 Gate：
  - `avoid` 子链品种：综合得分不参与多空候选（或强降权）
  - `long/short` 子链：仅放开对应方向信号（long 子链过滤负信号、short 子链过滤正信号）
- **A3 置信度门槛参数化**：`min_confidence`（默认 0.55，可配），与 `_apply_regime_direction_bias`
  的 0.30×conf 全局偏置并存（Gate 是硬约束，偏置是软调整）。

### B 模块 — 品种级置信度 → 暴露缩放

> ✅ 已实施（v2.104.0+111）：`regime_gate.py` 新增 `map_confidence_to_exposure`（分段映射
> `<exposure_min(0.4)→0 / ≥exposure_sat(0.7)→1.0 / 中间线性`）+ `apply_exposure_scale`
> （暴露 = 子链置信度映射 × 品种-链对齐度 `alignment_scores`，score==0 跳过防双重惩罚、
> avoid-soft 链保留 A 模块降权结果不二次缩放），settings.yaml 补 `exposure_min: 0.4`/
> `exposure_sat: 0.7`，信号管线 Step 3h1 Gate 之后、3h2 全局方向偏置之前接入。
> 测试扩展 +13 用例（映射分段边界 + 对齐度乘积 + 防双重惩罚 + 盲测 default）。

目标：把品种级 Regime 置信度从"对齐度微调"升级为"**暴露缩放**"。

- **B1 暴露系数映射**：`map_confidence_to_exposure(confidence, config) -> float`
  - 线性映射 `exposure = confidence`（或分段：`< 0.4 → 0`，`0.4~0.7 线性`，`≥ 0.7 → 1.0`）
  - 与全局 `exposure_scale`（28-T4 置信度仓位缩放）**乘性合并**：`exposure_total = exposure_scale × 品种置信度暴露`
- **B2 接入点**：`synthesize_signals`/信号管线按品种对因子加权得分再乘品种暴露系数；
  替代当前仅 ±0.20 对齐度 blend 的粗粒度。
- **B3 防双重惩罚**：与 A 模块 Gate 协同——`avoid` 子链品种暴露直接 0；`long/short` 子链
  品种暴露 = 品种置信度缩放（不叠加 Gate 的 hard 0）。

### C 模块 — 收益来源族激活矩阵（赚什么钱，下钻子链）

> ✅ 已实施（v2.104.0+111，首期"子链→全局回退"）：`portfolio_loop.regime_adaptive_weight_adjustment`
> 新增可选参数 `subchain_regimes`——因子按 `subchain_scope`（单链名/单元素列表）路由到
> **其子链 regime** 的倍率表（首期以全局 `REGIME_STYLE_MULTIPLIERS` 复制初始化，数据不足回退全局，
> 向后兼容）；无 scope/all/unknown/部分链因子回退全局。Step 2.5（energy + Gate 开启时
> `SectorRegimeSelector.detect_all(panel, sector_map=ENERGY_CHAIN_SUB_SYMBOLS)`）检测子链 regime
> 并传入；新增 `build_subchain_return_source` 画像函数（{子链: {regime, confidence, active_styles}}）
> 入 `_regime_meta.subchain_return_source`。测试 `test_portfolio_loop_adaptive.py` +10 用例
> （单链路由/部分链回退/未知链回退/缺省 None 兼容/概率混合/高波缩减按子链/画像结构）。

目标：把 `REGIME_STYLE_MULTIPLIERS` 从"全局 FactorStyle 乘数"升级为"**子链 × 制度下的
收益来源族激活**"——Regime 定义该子链此刻存在哪些可提取的收益来源（趋势/反转/carry/
波动率溢价），因子家族是该来源的实现工具（手段层交给 plans/47 因子选择）。

- **C1 来源族矩阵扩展**：`{子链: {制度: {来源族: 激活强度}}}`——"该子链该 regime 下
  赚什么钱"。来源族 → 因子家族映射（如 趋势族 = {动量/基差动量/时序回归}，
  carry 族 = {展期收益/基差水平}），激活强度决定族内因子权重的基座。
- **C2 数据来源**：基于 `symbol_ic × 子链 regime` 历史聚合（A 模块 Gate 产出后累积），
  或先以全局 `REGIME_STYLE_MULTIPLIERS` 复制初始化（数据不足回退全局，向后兼容）。
- **C3 消费**：portfolio_loop Step 2.5 的 regime 自适应权重调整
  （`regime_adaptive_weight_adjustment`）增加 `market="energy"` 时按子链路由来源族乘数；
  数据不足子链回退全局乘数。输出 `subchain_return_source` 画像（各子链当前激活来源族）
  入质量报告（与 D3 的 Gate 分布段互补）。

### D 模块 — 灰度、协同与监控

> ✅ 已实施（v2.104.0+111）：**D1** `futures_signal_pipeline.py` main() 新增
> `--enable-regime-gating`（默认关，`gate_cfg.enabled or enable_regime_gating` 双通道开启；
> settings.yaml `l3.regime_gating.enabled=false` 默认关，关闭即恢复现状）。**D2** 与 plans/47
> 串联正交性验证（test_subchain_weight.py TestPlan48ChainedD2：幅度层调制仅动因子权重、
> 方向层 Gate 仅动品种得分，互不覆盖——先方向后幅度顺序契约）。**D3** Step 2.5 构建
> `_subchain_gate_distribution`（各子链 decision），质量报告新增 `subchain_gate_distribution`
> 段（与 plans/47 §D2 `subchain_exposure` 段互补——方向层 vs 幅度层监控）。

## 三、数据模型与契约（变更点）

| 对象 | 变更 | 说明 |
|---|---|---|
| `fts/factor_engine/regime_gate.py`（新增） | `GateConfig` / `GateDecision` / `build_symbol_chain_map` / `build_subchain_gates` / `apply_subchain_gate` / `map_confidence_to_exposure` / `apply_exposure_scale` | 方向层纯计算，无 IO |
| `config/settings.yaml` | + `l3.regime_gating.enabled=false` / `min_confidence=0.55` / `avoid_mode` / `soft_avoid_ratio=0.3` / `blind_default=avoid` / `exposure_min=0.4` / `exposure_sat=0.7` | 参数化，禁硬编码 |
| `futures_signal_pipeline.py` | + 子链 Gate 过滤（`avoid` 剔除/方向限制）+ 暴露缩放 + `--enable-regime-gating` | 仅 energy 链生效 |
| `portfolio_loop.py` | + `regime_adaptive_weight_adjustment(subchain_regimes=...)` 子链路由 + `build_subchain_return_source` + 质量报告 `subchain_gate_distribution` 段 | 数据不足回退全局 |

## 四、实施步骤（已完成）

1. ✅ A1/A2/A3（Gate 判定器 + 信号管线接入 + 参数化）→ 验证：[test_regime_gate.py 13 用例全绿；avoid 品种不参与]
2. ✅ B1/B2/B3（暴露缩放 + 接入 + 防双重惩罚）→ 验证：[映射分段/边界用例全绿；avoid 与缩放不重复惩罚]
3. ✅ C1/C2/C3（子链路由 + 全局回退）→ 验证：[数据不足子链回退全局乘数；energy 子链路由生效（9 用例）]
4. ✅ D1/D2/D3（灰度 + 串联 + 监控）→ 验证：[enable=false 输出与现状逐位一致（缺省 None 回归保护）]

## 五、测试方案（已实施）

- `tests/factor_engine/test_regime_gate.py`（**26 用例全绿**）：
  - 子链 bull/bear + 置信度 → decision 正确（long/short/avoid/neutral）
  - `min_confidence` 门槛参数化（0.55 下 0.50 置信度 → avoid）
  - 暴露映射分段（<0.4→0 / 0.4~0.7 线性 / ≥0.7→1.0）+ 边界（=min/=sat）+ 参数化门槛
  - 防双重惩罚：avoid-soft 链保留 A 模块降权结果不二次缩放；已剔除（score=0）跳过
  - 盲测 default（avoid/neutral 两态）+ 对齐度缺失保守默认 0.5
- `tests/factor_engine/test_portfolio_loop_adaptive.py`（C 模块 **+10 用例**）：
  - 单链 scope（字符串/单元素列表）路由子链 regime；all/unknown/部分链/未知链回退全局
  - 缺省 `subchain_regimes=None` 与现状逐位一致（回归保护）
  - 子链 regime_probs 概率混合 + 高波动衰减按因子路由子链判定
  - `build_subchain_return_source` 画像结构 + 空输入
- `tests/factor_engine/test_subchain_weight.py`（D2 串联 **+1 用例**）：
  - 幅度层调制（47）仅动因子权重、方向层 Gate（48）仅动品种得分——正交不冲突
- 回归：`pytest tests/test_futures_signal_pipeline.py tests/factor_engine/test_portfolio_loop.py
  tests/test_cli_extra.py -m "not slow"`（68 + 268 + 358 全绿，分级测试政策不跑全量）+ ruff 全绿

## 六、验证标准（验收，已达成）

1. ✅ Gate 判定：能源 bull 0.9 → long、煤化工 bear 0.2 → avoid，avoid 子链品种不参与（方向层正确）。
2. ✅ 暴露映射：0.55 → 0.5 线性中点、0.3 → 0、0.8 → 1.0，乘对齐度合并数值正确（暴露层正确）。
3. ✅ energy L3 开启后：子链 Gate 分布入质量报告（`subchain_gate_distribution` 段）+ 来源族激活
   画像（`subchain_return_source`）入 `_regime_meta`；灰度默认关不改变现状。
4. ✅ 新增测试 26+9+1 = 36 用例全绿 + 受影响回归 694 全绿 + ruff 通过；GAP-136 登记并关闭。

## 七、风险与回退

- **风险 1（子链 regime 噪声）**：子链由 3 品种合成，制度判定噪声大 → `min_confidence`
  门槛防误 Gate（与 plans/47 显著性护栏同纪律）。
- **风险 2（方向误判代价不对称）**：Gate 是硬约束，误判导致整链回避损失机会 →
  `avoid` 用"不参与"而非"反向"，且可配置 `soft_avoid`（仅降权不剔除）。
- **风险 3（与全局偏置冲突）**：全局 bear 偏置 + 子链 bull Gate 并存 → 明确 Gate 优先级
  （子链 Gate 优先于全局偏置，全局偏置仅作用于未 Gate 的 neutral 子链）。
- **回退路径**：`--enable-regime-gating` 关闭即恢复现状（全局软票 + 方向偏置）。

## 八、一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| §A Gate 判定 | `build_subchain_gates` 输出与输入 sector_regimes 一致 | `grep -n "build_subchain_gates" fts/factor_engine/regime_gate.py` + 单测断言 |
| §A2 信号管线 | `avoid` 子链品种不进入多空候选（仅 energy 且开关开启） | 单测断言 enable=false 与基线一致 |
| §B 暴露缩放 | `map_confidence_to_exposure` 分段 + `apply_exposure_scale` 对齐度乘积 | 单测数值断言 |
| §C 来源族矩阵 | 数据不足子链回退全局乘数；`subchain_return_source` 画像入 `_regime_meta` | 单测断言回退路径 + 画像结构 |
| §D1 灰度 | `futures_signal_pipeline --enable-regime-gating` 参数存在且默认关闭 | `grep -n "enable-regime-gating" scripts/futures_signal_pipeline.py` |

## 九、实施后预期效果

- 方向层：子链分歧不再被全局软票中和——能源 bull 做多、煤化工不明朗回避，
  "该不该参与"由该链自己的制度决定（与股票统一市场的全局 Regime 语义根本区分）。
- 来源层：各子链报告"当前赚什么钱"（激活收益来源族画像 `subchain_return_source`）——
  趋势 regime 下识别趋势收益可提取、Back 结构下识别 carry 可提取，来源族激活强度作为
  族内因子权重的基座（Regime 定义目标，因子选择工具）。
- 暴露层：品种置信度直接决定仓位，低置信度品种收缩，与全局 exposure_scale 正交合并。
- 三层正交："能否（Gate）→ 赚什么（来源族）→ 怎么赚+赚多少（因子工具+权重）"，
  与 plans/47 构成完整闭环。

## 十、实施后文档同步清单（Harness 13 项，已完成）

1. ✅ `docs/harness/01-architecture.md`（Step 2.5 Regime Gate 数据流）
2. ✅ `docs/harness/02-lifecycle.md`（Phase 48 产出物）
3. ✅ `docs/harness/03-configuration.md`（`l3.regime_gating.*` 配置项）
4. ✅ `docs/harness/04-resilience.md`（Gate 误判回退路径）
5. ✅ `docs/harness/05-observability.md`（子链×制度 Gate 分布指标）
6. ✅ `docs/harness/06-testing.md`（新测试文件/用例数）
7. ✅ `docs/harness/07-operations.md`（版本历史）
8. ✅ `docs/harness/08-gap-analysis.md`（登记并关闭 GAP-136：品种/子链 Regime 未作独立方向 Gate + 置信度未映射暴露）
9. ✅ `docs/harness/09-advancement-plan.md`（晋级里程碑）
10. ✅ `pyproject.toml`（版本 bump v2.104.0+111）
11. ✅ `README.md`（工程指标）
