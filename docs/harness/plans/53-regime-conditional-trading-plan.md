# 53 — Regime 条件化因子交易计划（D/A/B/C 四模块）

> 版本: v3.0.0+10（实施完成）

> 状态: ✅ 已完成（2026-08-17，D/A/B/C 四模块实施完成） · 优先级: P1 · 负责人: FTS Agent · 关联: plans/28, plans/47, plans/48, GAP-105, regime_validation.py, config/futures_universe.yaml

## 〇、实施校准记录（实现反哺设计，与 §设计差异点）

| # | 设计点 | 设计文档原定 | 实施校准 | 原因 |
|---|---|---|---|---|
| 1 | `min_abs_ic` 门槛 | 0.03（对齐评估链 min_ic） | **0.05** | 制度内样本（20~60）远小于评估全样本（500），0.03 下独立噪声制度误报率高（60 样本 Spearman 偶然相关实测可达 0.1）；0.05 为护栏滤噪声合理折中 |
| 2 | C 晋升门槛语义 | "覆盖 ≥3 且正向 ≥2" | **有效制度数 ≥ min_positive_regimes（默认 2）** | 用 regime_scope 列表长度近似有效制度数；scope="all"/"unknown" 放行（保守防误杀），仅明确的 1 制度因子拦截——防单制度过拟合同时保留部分链因子（plans/47 实证 52 个部分链因子为常态形态） |
| 3 | D 决策门结果 | 制度标签须有预测力 | **规则检测（lookback=60, step=1）✅ K-W p=0.030**；HMM（lookback=120, step=5）5 制度样本过稀不可检验 | RegimeSeriesBuilder 默认值固化为规则检测 + 60 天窗口（有区分力且快）；HMM 检测可配但默认关 |
| 4 | 评估链接线性能 | 逐因子构建 regime_series | **加开关 `l3_regime_ic_report_enabled`（默认关）+ energy 面板限定** | RegimeSeriesBuilder 滚动检测有秒级成本，批量评估场景不默认开启；晋升链在评估链产出画像后落库 |

## 一、背景与问题定位

### 1.1 业务目标

用户目标（2026-08-17 会话确认）：**在识别到的市场制度（Regime）下，通过因子进行胜率和回报率更高的交易**。

即"Regime 条件化因子交易"——识别当前市场制度 → 只交易/重仓该制度下胜率与回报高的因子，剔除该制度下 IC 为负/弱的因子。区别于现有"全时段持有全部因子 + regime 只调权重倍率（0.7~1.3×）"的微调粒度。

### 1.2 系统现状盘点（已具备能力）

| 层 | 现状 | 代码位置 |
|---|---|---|
| Regime 识别 | 5 制度（bull/bear/oscillate/high_vol/low_vol）+ 概率 + 置信度；投票/HMM/模型选择/统计校准 | `regime.py` / `regime_voting.py` / `regime_hmm.py` / `regime_model_selection.py` / `regime_calibration.py` |
| Regime 预测力验证 | `validate_regime_predictive_power`（Kruskal-Wallis 区分前向收益/波动）——**已实现，零调用方** | `regime_validation.py` L30-70 |
| 分 Regime 评估因子 | `validate_factor_across_regimes`（G7：per-regime IC/ICIR + regime_dependent 标记）——**已实现，零调用方** | `regime_validation.py` L105-169 |
| Regime → 权重 | Step 2.5 按 FactorStyle 倍率调权（REGIME_STYLE_MULTIPLIERS）+ 概率混合 + RegimeSmoother + 置信度仓位缩放 | `portfolio_loop.py` L365-418, L5223-5408 |
| Regime → 风控 | G14 风控参数表（杠杆/止损/单日亏损按制度切换） | `regime_multipliers.py` |
| 子链 Gate | energy 子链避免链整链归零（regime × 子链方向回避） | `regime_gate.py` / plans/48 |
| **子链画像资产化（模板）** | `subchain_ic_profile` / `subchain_scope` 随因子落库，评估链 + 晋升链已接线 | `subchain_profile.py` / `evaluation_chain.py` L1199-1213 / `evolution_promote.py` L1238-1242 |

### 1.3 缺口定义（用户目标 vs 现状）

| # | 缺口 | 具体表现 |
|---|---|---|
| G1 | **分 Regime 画像未资产化** | G7 的 `per_regime` IC/ICIR 算完即弃，无"因子×制度"画像落库字段（对比 `symbol_ic`/`subchain_ic_profile` 已落库）。运行时无法按当前 regime 查询"哪个因子此刻有效" |
| G2 | **组合层是"权重微调"而非"因子条件化"** | regime 只把权重乘 0.7~1.3×，不会剔除当前 regime 下 IC 为负的因子（除 energy 子链 gate 的整链归零） |
| G3 | **胜率不是显式维度** | 权重/评估目标是夏普与 IC，无 win rate 画像 |
| G4 | **晋升无 regime 门槛** | 因子可在单一 regime 有效即晋升 elite，存在"单制度过拟合因子"混入组合的风险（GAP-105 的 G7 检验未接线为硬门槛） |

### 1.4 与既有计划的边界

- plans/28（Regime 机构级优化）：本计划消费其产出（regime 检测/校准），不触碰其实现；
- plans/47（子链差异化）：本计划是**同构复刻**——把"因子×子链"画像/调制机制推广到"因子×Regime"维度，复用其已验证的落库/接线模板，不重叠；
- plans/48（Regime 分层门控）：regime_gate 管"子链方向回避"（整链/方向层），本计划 B 模块管"因子权重条件化"（因子层），互补；
- GAP-105（5-Regime 拆分检验）：G7 检验函数已实现，本计划 A 模块将其升级为资产化画像并接线，C 模块将其接线为晋升硬门槛。

## 二、方案设计

### D 模块 — 制度预测力验证先行（决策门）

**目的**：任何条件化交易都以"制度标签对前向收益/波动有真实区分力"为前提——制度标签无预测力，后续 A/B/C 全部作废（防空中楼阁）。

**D1 RegimeSeriesBuilder（新模块）**：由市场面板构建历史 `regime_series`
- 输入：energy 面板（`ENERGY_CHAIN_SYMBOLS ∪ ENERGY_CHAIN_HOLDOUT`，20 品种，SSOT config/futures_universe.yaml）
- 构建市场合成 OHLCV：复用 `SectorRegimeSelector._build_sector_ohlcv`（portfolio_loop Step 0.5b 已验证路径）
- 滚动检测：`RegimeAwareSelector.detect` 在合成 OHLCV 上滚动输出制度标签序列（逐日或每 5 交易日快照，参数化）
- 输出：`pd.Series`（DatetimeIndex → regime 标签），供 A 模块复用

**D2 预测力检验**：`validate_regime_predictive_power`（已实现）在 energy 面板跑通：
- 按制度分桶统计前向收益/前向波动 → Kruskal-Wallis 组间差异检验

**D3 决策门**：
- ✅ K-W p < 0.05 且制度间条件均值存在差异 → 制度标签可用，进入 A；
- ❌ 不显著 → 停，排查检测器/窗口参数，不继续烧钱。

**交付**：验证脚本 `scripts/regime_predictive_power_check.py` + 报告 `reports/{market}/{date}/regime_predictive_power.md`。

### A 模块 — Regime 画像资产化（地基，仿 subchain_profile.py）

**目的**：把"因子×5 制度 × {IC, ICIR, 胜率, n}"做成资产化画像随因子落库，作为 B/C 的数据地基。

**A1 画像计算器**：新增 `fts/factor_engine/regime_profile.py`
- `compute_regime_profile(factor_id, signal, fwd_returns, regime_series, cfg) -> RegimeProfile`
- 复用 `validate_factor_across_regimes`（G7）的 per-regime IC/ICIR 计算，扩展：
  - **win_rate**：该制度下前向收益为正的样本占比（新增维度，对应业务目标"胜率"）
  - **effective 判定**：n ≥ min_regime_samples 且 ICIR > 0 且 |ic| ≥ min_abs_ic（三门槛 AND，方向为 G7 语义 + 幅度门槛）
- `regime_scope` 派生：全部制度 effective → "all"；部分 → effective 制度列表；无 → "unknown"（保守，不误杀）
- `regime_dependent`：任一覆盖制度 ICIR < -0.5（沿用 G7 语义）

**A2 落库**（仿子链画像接线）：
- `evaluation_chain.py`：symbol_ic 产出后新增"regime 画像报告段"（仿 L1199-1213 subchain_ic_report，写入 `metrics["regime_ic_report"]`）
- `evolution_promote.py`：晋升时 `metadata.update(build_regime_metadata(...))`（仿 L1238-1242）

### B 模块 — 组合层 Regime 条件化因子选择（仿 subchain_weight.py）

**目的**：组合运行时按当前制度对因子做条件化——当前制度下 IC 显著为负/弱的因子权重归零或降权，提升该制度下组合的胜率与回报。

**B1 调制系数**：新增 `fts/factor_engine/regime_conditional_weight.py`
- `build_regime_conditioned_weights(factors, current_regime, cfg) -> {factor_id: m}`
- 语义（因子维度，与 subchain 的"因子×链"矩阵同构但降为"因子×当前制度"）：
  - `regime_scope == "all"` / `"unknown"` / 含当前制度 → `m = 1.0`（未知不误杀）
  - 当前制度 IC 显著为负 → `m = 0.0`（decay_mode="zero"，默认）或按 `|ic|/max_ic` 缩放（"soft"）
- 无 regime 画像字段的因子 → `scope_default="all"` 全保留（兼容现状）

**B2 合成接入**：`PortfolioLoop` Step 2.5（regime_adaptive_weight_adjustment）之后乘性应用 `m`（与现有 regime 倍率叠加），仅 `market="energy"` 且开关开启时生效；开关 `l3.regime_conditional.enabled` 默认 false（零行为变更）。

### C 模块 — 晋升门槛（≥N 制度有效）

**目的**：防"只在单一 regime 有效的过拟合因子"晋升 elite——G7 检验接线为晋升硬门槛。

- **C1 门槛**：晋升时要求"覆盖制度 ≥ 3 且 正向制度 ≥ min_positive_regimes（默认 2）"；不满足 → 标记 `regime_gate_failed`，不允许晋升（与 L2 评审 approved 同层级硬门槛）
- **C2 接入**：`evolution_promote.py` 晋升判定处（评估链有 regime_series 且画像完整时生效；无 regime 数据 → 不拦截，向后兼容）
- **C3 参数**：`regime_profile.min_positive_regimes`（SSOT 配置）

## 三、数据模型与契约（变更点）

| 对象 | 变更 | 说明 |
|---|---|---|
| `factor_catalog.metadata` | + `regime_ic_profile` / `regime_scope` / `regime_dependent` | DuckDB SSOT；JSON elite 同步只读快照；t=±inf 序列化 None（JSON 安全） |
| `config/settings.yaml` | + `regime_profile.{min_regime_samples=20, min_positive_regimes=2, min_abs_ic=0.03, all_regimes_effective_min=3}`；+ `l3.regime_conditional.{enabled=false, decay_mode="zero", soft_min_ratio=0.0, scope_default="all"}` | 参数化，禁硬编码 |
| `fts/factor_engine/regime_profile.py` | 新增：RegimeProfileConfig / RegimeStat / RegimeProfile / compute_regime_profile / build_regime_metadata / RegimeSeriesBuilder | 契约优先 |
| `fts/factor_engine/regime_conditional_weight.py` | 新增：RegimeConditionalConfig / build_regime_conditioned_weights | 契约优先 |
| `evaluation_chain.py` | + regime 画像报告段（`metrics["regime_ic_report"]`） | 仿 subchain_ic_report，失败降级不阻断 |
| `evolution_promote.py` | + 晋升 metadata 落库 + C 门槛接线 | 仿 build_subchain_metadata 接线 |
| `PortfolioLoop` | + `enable_regime_conditional`（默认关，走配置） | 向后兼容 |

### 契约（Pydantic V2 草案）

```python
# regime_profile.py
class RegimeProfileConfig(BaseModel):
    min_regime_samples: int = Field(default=20, ge=1, description="制度最小样本数（门槛①）")
    min_positive_regimes: int = Field(default=2, ge=1, description="晋升门槛：最少正向制度数（C 模块）")
    min_abs_ic: float = Field(default=0.03, ge=0.0, description="|IC| 幅度门槛（门槛②，对齐评估 min_ic）")
    all_regimes_effective_min: int = Field(default=3, ge=1, description="≥ 此制度数 effective 时 scope='all'")

class RegimeStat(BaseModel):
    n: int
    ic: Optional[float] = None
    icir: Optional[float] = None
    win_rate: Optional[float] = None   # 该制度下前向收益为正的样本占比（业务目标"胜率"）
    effective: bool

class RegimeProfile(BaseModel):
    factor_id: str
    regime_stats: dict[str, RegimeStat]
    regime_scope: RegimeScope   # "all" / [制度列表] / "unknown"
    regime_dependent: bool
```

```python
# regime_conditional_weight.py
class RegimeConditionalConfig(BaseModel):
    enabled: bool = Field(default=False, description="灰度开关（默认关，零行为变更）")
    decay_mode: DecayMode = Field(default="zero", description="当前制度 IC 显著为负的因子：zero=归零 / soft=按 |ic| 相对缩放")
    soft_min_ratio: float = Field(default=0.0, ge=0.0, le=1.0, description="soft 模式最低保留比例")
    scope_default: str = Field(default="all", description="无 regime 画像字段因子的默认处理（all=全保留）")
```

## 四、实施步骤（建议顺序，逐步留痕）

1. **D**（RegimeSeriesBuilder + 预测力验证脚本）→ 跑 energy 面板输出报告
   - 验证：[K-W p < 0.05 决策门通过 / 报告含各制度条件均值与波动]
2. **A**（regime_profile.py + 评估链报告段 + 晋升落库）→ 跑真实因子画像
   - 验证：[合成 panel 断言画像与 G7 手算一致；win_rate 口径正确；metadata 落库]
3. **B**（regime_conditional_weight.py + Step 2.5 接入）→ energy L3 灰度
   - 验证：[当前制度 IC 显著为负因子权重 ≈ 0；开关关闭时输出与基线逐位一致]
4. **C**（晋升门槛接线）→ 门槛单测
   - 验证：[仅 1 制度有效的因子被拦；无 regime 数据不拦截（向后兼容）]

## 五、测试方案

### 5.1 模块 A 画像测试（`tests/factor_engine/test_regime_profile.py`）

| # | 用例 id | 场景 | 断言 | 优先级 |
|:---:|:---|:---|:---|:---:|
| 1 | `test_single_regime_effective` | 仅 bull 有效 | scope 含 bull、specific 语义正确 | P0 |
| 2 | `test_multi_regime_scope` | 2 制度有效 | scope 含 2 制度 | P1 |
| 3 | `test_scope_all` | ≥3 制度有效 | scope="all" | P1 |
| 4 | `test_scope_unknown` | 无 regime 数据 | scope="unknown"，不误杀 | P0 |
| 5 | `test_win_rate_computed` | 胜率口径 | win_rate = 正收益样本占比 | P0 |
| 6 | `test_regime_dependent_flag` | 任一制度 ICIR<-0.5 | regime_dependent=True | P1 |
| 7 | `test_series_builder` | RegimeSeriesBuilder | 输出序列与逐窗口 detect 一致 | P1 |
| 8 | `test_metadata_persisted` | 画像落库 | metadata 含 regime_ic_profile | P1 |

### 5.2 模块 B 条件化测试（`tests/factor_engine/test_regime_conditional_weight.py`）

- 当前制度 IC 显著为负因子权重 = 0（zero）/ 缩放（soft）
- `enabled=false` 时输出与现状逐位一致（回归）
- unknown 因子不降权（不误杀）

### 5.3 模块 C 门槛测试

- 仅 1 制度有效因子晋升被拦；无 regime 数据不拦截
- 覆盖制度 ≥3 且正向 ≥2 正常晋升

### 5.4 回归

- `pytest tests/factor_engine/test_portfolio_loop.py tests/factor_engine/test_evaluation_chain.py tests/factor_engine/test_regime_validation.py -v`
- （日常分级测试政策：仅受影响模块，不跑全量）

## 六、验证标准（验收）

1. ✅ D：energy 面板制度预测力报告产出——**规则检测（lookback=60, step=1）K-W p=0.030 通过**，
   3 制度（high_vol +1.38% / oscillate -0.29% / low_vol -0.07% 条件前向收益）；HMM 5 制度样本过稀
   不可检验（low_vol 仅 1 样本）。报告落盘 `reports/energy/2026-08-17/regime_predictive_power.md`；
2. ✅ A：`regime_profile.py` 画像计算器（IC/ICIR/胜率/scope）+ `RegimeSeriesBuilder` +
   评估链报告段（开关控制）+ 晋升 metadata 落库——13 用例全绿；
3. ⏳ B：`regime_conditional_weight.py`（zero/soft 降权）+ Step 2.5 接入——10 单测 + 2 集成全绿，
   灰度默认关（`l3.regime_conditional.enabled=false`），待 energy 实盘灰度验证后开启；
4. ✅ C：`regime_gate_passed` 晋升门槛（有效制度数 ≥ min_positive_regimes）+ 晋升链接线——6 用例全绿；
5. ✅ 受影响回归 380 passed + ruff 全绿。

## 七、风险与回退

- **风险 1（regime_series 历史构建不稳）**：制度检测当前为"单点"（detect 当前时刻），滚动历史序列的稳定性未实测——本方案最大不确定性。缓解：D 模块先行验证，不稳即停。
- **风险 2（制度样本不足）**：G7 对样本 <20 日期的制度自动跳过，画像可能覆盖不全。缓解：RegimeProfileConfig 参数化 + 报告标注覆盖情况。
- **风险 3（过度裁剪）**：画像误标 → 真全链因子被降权 → 组合多样性损失。缓解：`decay_mode="soft"` 先行 + `scope_default="all"` + 灰度观察。
- **风险 4（单制度噪声）**：某制度样本少导致 IC 虚高/虚低。缓解：min_regime_samples 兜底 + min_abs_ic 幅度门槛。
- **回退路径**：`l3.regime_conditional.enabled=false` 一键恢复现状；A/C 为评估/晋升层变更，灰度关闭即零行为变更。

## 八、一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| §A 画像计算器 | `compute_regime_profile` per-regime IC/ICIR 与 `validate_factor_across_regimes` 一致 | `grep -n "compute_regime_profile" regime_profile.py` + 单测断言 |
| §A2 metadata | 因子 `metadata.regime_ic_profile` 落 `factor_catalog`（DuckDB SSOT） | 查询 `factor_catalog.metadata` 含 `regime_ic_profile` |
| §B 条件化 | `m[factor]` 仅 `market="energy"` 且开关开启时生效 | 单测断言 `enabled=false` 输出与基线一致 |
| §C 晋升门槛 | 晋升判定含 regime 门槛 | 单测断言仅 1 制度有效因子被拦 |
| §D 决策门 | 验证脚本输出 K-W p 与制度条件均值 | `grep -n "regime_predictive_power_check" scripts/` |

## 九、实施后预期效果

- 组合层：当前制度下 IC 显著为负/弱的因子被剔除或降权 → 该制度下组合胜率与回报提升（预估待灰度实测），避免"逆制度因子"拖累；
- 因子资产：elite 因子携带 regime 画像，为 L1 知识补给与 L2 演化提供"制度条件化"反馈闭环；
- 晋升防线：单一 regime 过拟合因子无法混入组合，组合跨制度稳定性提升。

## 十、实施后文档同步清单（Harness 13 项）

1. `docs/harness/01-architecture.md`（数据流/regime 条件化步骤 + 接口定义）
2. `docs/harness/02-lifecycle.md`（产出物）
3. `docs/harness/03-configuration.md`（regime_profile.* / l3.regime_conditional）
4. `docs/harness/05-observability.md`（regime 条件化指标）
5. `docs/harness/06-testing.md`（测试文件/用例数）
6. `docs/harness/07-operations.md`（版本历史）
7. `docs/harness/08-gap-analysis.md`（登记 GAP：regime 条件化因子交易缺位）
8. `docs/harness/09-advancement-plan.md`（晋级里程碑）
9. `docs/production_plan.md`（流程同步）
10. `CLAUDE.md`（职责变更，如适用）
11. `README.md`（工程指标/快速参考）
12. `scripts/verify_doc_consistency.py`（一致性校验通过）
13. `pyproject.toml`（版本 bump）
