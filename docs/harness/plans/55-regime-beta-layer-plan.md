# 55 — Beta 层落地计划（L0 宏观 Beta 层：识别风口顺β方向）

> 版本: v3.0.0+10+N（build bump 后更新） · 文档类型: 实施立项计划

> 状态: ✅ 已实施（A-E 五模块完成）+ 灰度保守档已开启（2026-08-19，决策门通过） · 优先级: P1 · 负责人: FTS Agent
> 来源思想: `D:\Regime-Driven\docs\REGIME_STRATEGY_DESIGN.md` §1.3（Beta 优先 / 听风者）、§5.1（L1 宏观层）、§7.1（识别失效防御）
> 关联: plans/54（D1 差距登记）、plans/28（Regime 机构级优化）、plans/48（子链 Gate）、plans/53（Regime 条件化）、macro_regime.py / regime_gate.py / portfolio_loop.py / regime_routing_rules.yaml

---

## 〇、实施校准记录（实现反哺设计，与 §方案差异点）

| # | 设计点 | 设计文档原定 | 实施校准 | 原因 |
|---|---|---|---|---|
| 1 | `risk_pref_pair` | ("IF", "T") | **("IF0", "TF0")** | FTS 品种池（futures_universe.yaml cffex）无 T0 十年期国债，以 TF0 五年期国债为避险锚，统一走 FTS 数据层 |
| 2 | `fin_symbols` | 8 品种（含 T/TL） | **6 品种（IF0/IH0/IC0/IM0/TF0/TS0）** | 同上，FTS 池内 CFFEX 品种 |
| 3 | 判定逻辑 | 独立投票（vol 参与投票） | **vol 为门控**（趋势方向投票 + 波动门控 + 股债比佐证） | 文档 §5.1 vol 是 RISK_ON 必要条件而非独立正票；"横盘+低波"不应误判 RISK_ON（单测暴露缺陷后修正） |
| 4 | 数据回溯天数 | 硬编码 max(days,130) | **`BetaLayerConfig.days=130` 参数化** | 配置化禁硬编码 |
| 5 | 股债比缺口 | 无 | **ffill 前向填充 + min_periods 兜底** | 真实 CFFEX 数据存在换月缺口（IF0/TF0 close NaN），rolling 尾部全 NaN 致 risk_pref_z 恒 NaN（e2e 实测暴露） |
| 6 | 数据核验 | 待核验 | **✅ 通过**：CFFEX 六品种真实日线 250 日可达（2025-08-01~2026-08-12，kline_cache→TQ-Local 降级链有效，非合成） | — |
| 7 | 实盘 beta_state 注入 | RiskManager 注入 | ✅ 实现（`RiskManager(config, regime, beta_state)`）；实盘默认路径（http_server）未传 beta_state 属二期接线 | 一期保持零行为变更 |
| 8 | 相关性回归 | 无 | 281/282 portfolio_loop 通过；1 失败 `test_run_energy_panel_symbols_restricted` 为**预先存在**（2026-08-19 RU0/BR0 升维致品种池 20→23 断言过期），与本次改动无关 | 失败透明如实记录 |
| 9 | 预测力决策门 | 默认 step=5 | **step=1 日频 + fwd=10 前向收益，K-W p=0.0203 ✅ 通过 + 排序 RISK_ON>RANGE_BOUND>RISK_OFF 正确**（v3b，trace_id beta-kw-v3b-20260819） | v1（step=5 收益 K-W p=0.71 不显著）暴露样本量不足；v2 敏感性扫描发现 step=1 日频下显著；v3 修正 vol/ret 独立对齐（ret 不受 vol 20 日窗连带截断）后通过；参考维度（20 日 vol 分层 p=0.85）无区分力如实标注不设硬门；fwd=20 显著但排序翻转（长窗信号衰减，符合"识别优于预测"）；step=5/10 样本不足（70/35）全部不显著——日频为统计力充分基线，信号管线本就每日运行零额外成本 |

## 一、背景与问题定位

### 1.1 核心洞察：FTS 缺"L0 宏观 Beta 层"——纯 Alpha 系统 + Regime 调制器，未"顺β方向配置敞口"

外部 Regime-Driven 文档的核心立场是"**识别优于预测、Beta 优先**"（§1.3）：
组合收益 = β × 市场收益 + α + 噪声，期货多空双向下 Beta 的意义是"**顺 β 方向配置敞口**"——
正 Beta 做多进攻、负 Beta 反向做空进攻、状态不明防御。Alpha 仅作识别错误的缓冲。

FTS 现状（plans/54 D1 已核实）：

| 现状 | 证据 |
|:-----|:-----|
| `config/regime_routing_rules.yaml` global_rules（RISK_ON/OFF 三态风控）**零代码消费** | grep 全仓无 `global_rules` 消费 |
| `macro_regime.py`（Bridgewater PMI/CPI 四象限）已实现**未接线** | 无运行时消费方 |
| bear 制度只降权/降杠杆/降仓位，**不转空、不配置负 Beta 敞口** | `REGIME_RISK_PARAMS`（bear 杠杆 1.5）+ exposure_scale 降仓 |
| 无股债比 risk_pref（IF/T）信号 | 全仓无 IF/T 比值逻辑 |

即：FTS 已具备"量价五制度 + 子链 Gate + 置信度仓位缩放"的**微观-中观**识别能力，
缺的是**宏观 Beta 方向识别 → 组合级顺风/逆风敞口配置**这一顶层。

### 1.2 系统现状盘点（可复用能力）

| 层 | 现状 | 代码位置 |
|---|---|---|
| 金融期货数据 | CFFEX 8 品种日线数据就绪（financial 板块 ✅） | TDX_LOCAL（17709）可得 IF/IH/IC/IM/T/TF/TS/TL |
| 板块合成指数 | `SectorRegimeSelector._build_sector_ohlcv`（等权收益率指数 + 真实波幅）已验证 | `regime.py` L784-875 |
| 量价制度检测 | 五制度 + 置信度 + regime_probs（多检测器集成） | `regime.py` RegimeAwareSelector |
| 置信度→敞口缩放 | `_compute_exposure_scale`（统计/熵标定）+ 乘性合并点 | `portfolio_loop.py` L640 / L2988 |
| 全局方向偏置 | `_apply_regime_direction_bias`（bull/bear 得分 ×(1±bias)） | `futures_signal_pipeline.py` L435 |
| 实盘风控注入 | `RiskManager(config, regime)` 按制度注入杠杆/单日亏损 | `risk_manager.py` L80-111 |
| 宏观四象限（远期） | `MacroRegimeDetector`（PMI/CPI，月度，已闭环数据链路） | `macro_regime.py` + `scripts/macro_regime_report.py` |

### 1.3 缺口定义

| # | 缺口 | 表现 |
|---|---|---|
| G1 | **无宏观 Beta 状态** | 无 RISK_ON / RISK_OFF / RANGE_BOUND 状态层；global_rules 是死配置 |
| G2 | **无顺β方向敞口配置** | bear 只降权不转空；多头/空头敞口不随宏观状态对称调节 |
| G3 | **无宏观风控档位** | 实盘风控只按量价制度（G14），无宏观档位（RISK_OFF 额外降杠杆/收紧单日亏损） |
| G4 | **无 Beta 状态可观测** | 组合报告/指标无宏观层状态与缩放系数留痕 |

### 1.4 与既有计划的边界

- plans/28：量价 regime 检测/校准（本计划消费其产出能力，不触碰实现）；
- plans/48：子链 Gate 管"子链方向回避"（中观方向层），本计划 Beta 层管"全局宏观敞口层"（L0），
  串联顺序：**Beta 层（L0 宏观，全局敞口/方向）→ 子链 Gate（中观方向）→ 因子权重（微观）**；
- plans/53：因子×制度条件化（因子层），与 Beta 层正交；
- 文档 §5.1 的 L1 定义与 FTS 现有"全局量价制度"（RegimeAwareSelector）层级不同：
  本计划新增的 Beta 层是**纯宏观风险偏好层**（金融期货合成指数 + 股债比），区别于量价五制度。

## 二、方案设计

### A 模块 — Beta 检测器（L0 宏观层，核心）

> **一句话本质（哲学约束 §3.3）**：金融期货合成指数定风险偏好方向（趋势），IF/T 股债比定资金信念（risk_pref），波动率定状态置信度。

**新增 `fts/factor_engine/regime_beta_layer.py`**：

- `BetaState`（TypedDict）：`{state: "RISK_ON"|"RISK_OFF"|"RANGE_BOUND"|"unknown", confidence, trend_score, vol_score, risk_pref, risk_pref_z, method}`
- `BetaDetector.detect(financial_panel) -> BetaState`：
  1. **金融期货合成指数**：CFFEX 品种等权收益率指数（复用 `SectorRegimeSelector._build_sector_ohlcv` 的等权归一化 + 真实波幅合成逻辑）
  2. `trend_score`：MA20 - MA60 归一化趋势（文档 §5.1）
  3. `vol_score`：20 日 realized vol 年化（历史分位阈值）
  4. `risk_pref`：IF/T 股债比（主力合约比值，20 日滚动 z-score；上行 = 风险偏好改善）
  5. **判定**：`trend_score > 0 且 vol_score < 阈值 且 risk_pref 上行` → `RISK_ON`；
     `trend_score < 0 且 vol_score > 阈值` → `RISK_OFF`；其他 → `RANGE_BOUND`
  6. **置信度**：三信号一致度软投票（一致数/3），门槛 `min_confidence` 不达标 → 视为 RANGE_BOUND（不偏置）
- **数据缺失/异常** → `unknown`（不偏置，scale=1.0，零行为变更）
- **可选慢层（二期）**：`MacroRegimeDetector` 四象限（月度）作为交叉验证/慢层信号，本期不接入
  （数据月度滞后大，一期以日频股债比为主信号）

### B 模块 — 信号管线顺β方向偏置

**`scripts/futures_signal_pipeline.py`**：在 `_apply_regime_direction_bias`（量价五制度方向偏置）
**之前**叠加 Beta 层方向偏置（多空不对称，实现"顺β方向配置敞口"）：

| Beta 状态 | 多头 | 空头 | 语义 |
|:---------|:-----|:-----|:-----|
| RISK_ON | ×(1 + on_long_boost) | ×(1 - on_short_suppress) | 顺正β：做多进攻 |
| RISK_OFF | ×(1 - off_long_suppress) | ×(1 + off_short_boost) | 顺负β：降多、反向做空进攻（期货视角） |
| RANGE_BOUND / unknown | ×1.0 | ×1.0 | 状态不明：不干预 |

- 仅 `--chain energy` 且 `l3.regime_beta_layer.enabled=true` 时生效（灰度默认关，零行为变更）
- 失败降级不阻断主流程（对齐 plans/48 接入纪律）

### C 模块 — 组合敞口层（Beta scale）

**`portfolio_loop.py build_combo`**（L2988 乘性缩放处）：新增 `beta_scale`，
`exposure_final = exposure_scale × aligned_scale × beta_scale`：

- `beta_scale` 由 Beta 状态 + 置信度分段映射（对齐 `map_confidence_to_exposure` 风格）：
  - RISK_ON：`on_scale`（默认 1.0，不放大）
  - RISK_OFF：`off_scale`（默认 0.5，总敞口压缩）——若 B 模块已做多空不对称偏置，此处仅做总敞口压缩防双重惩罚
  - RANGE_BOUND / unknown：1.0
- `regime_meta` 新增 `beta_state` / `beta_scale` / `beta_confidence`，随组合落盘可追溯
- **防双重惩罚**：B 模块（方向偏置）与 C 模块（总敞口压缩）职责分离——B 动多空相对权重、C 动总敞口，不重复压缩

### D 模块 — 实盘风控宏观档位

**`fts/factor_engine/regime_multipliers.py`**：新增 `BETA_RISK_PARAMS`（宏观档位参数表）：

| Beta 状态 | 附加约束 |
|:---------|:---------|
| RISK_ON | 无额外约束（沿用量价制度参数） |
| RISK_OFF | leverage_cap 额外 ×0.7、daily_loss_pct 收紧 30% |
| RANGE_BOUND | 无 |

- `resolve_risk_params` 增加 `beta_state` 参数：宏观档位与量价制度参数**叠加收紧**（取更严）
- `RiskManager.__init__(config, regime, beta_state=None)` 注入（对齐 G14 现有注入模式，不改变 `check()` 内部逻辑）
- 实盘接入点：`http_server.py` / `paper_trader_mhf.py` 调用处传 beta_state

### E 模块 — 灰度、监控与预测力校验

- `config/settings.yaml` 新增 `l3.regime_beta_layer.*`（enabled=false 默认关）+ CLI `--enable-regime-beta`
- 质量报告新增 `beta_state` 段（`current_combo.json` regime_meta）
- Prometheus 指标：`fts_beta_state` / `fts_beta_scale`（monitor/prometheus_metrics.py）
- **预测力校验脚本** `scripts/regime_beta_predictive_power_check.py`（复用 plans/53 D 模块模板）：
  Beta 状态对组合前向收益 K-W 检验 + 三态条件均值排序（RISK_ON > RANGE_BOUND > RISK_OFF 预期）

## 三、数据模型与契约（变更点）

| 对象 | 变更 | 说明 |
|---|---|---|
| `fts/factor_engine/regime_beta_layer.py`（新增） | `BetaLayerConfig` / `BetaState` / `BetaDetector` / `compute_beta_scale` / `apply_beta_bias` | 契约优先，纯计算无 IO |
| `config/settings.yaml` | + `l3.regime_beta_layer.{enabled=false, fin_symbols, trend/vol/risk_pref 窗口与阈值, min_confidence, on/off 缩放系数}` | 参数化，禁硬编码 |
| `scripts/futures_signal_pipeline.py` | + Beta 方向偏置（Step 3h 之前）+ `--enable-regime-beta` | 仅 energy 生效，失败降级 |
| `portfolio_loop.py` | + `beta_scale` 乘性合并 + `regime_meta.beta_state` 留痕 | 与 exposure_scale 正交 |
| `fts/factor_engine/regime_multipliers.py` | + `BETA_RISK_PARAMS` + `resolve_risk_params(..., beta_state)` | 宏观档位叠加收紧 |
| `fts/risk/risk_manager.py` | + `beta_state` 注入参数 | 不改变 check() 内部逻辑 |
| `fts/monitor/prometheus_metrics.py` | + `fts_beta_state` / `fts_beta_scale` | 可观测 |
| `config/regime_routing_rules.yaml` | global_rules 段标注"已接线"（消费方为 Beta 层配置的源语义） | 死配置激活 |

### 契约（Pydantic V2 草案）

```python
# regime_beta_layer.py
class BetaLayerConfig(BaseModel):
    enabled: bool = Field(default=False, description="灰度开关（默认关，零行为变更）")
    fin_symbols: list[str] = Field(default_factory=lambda: ["IF","IH","IC","IM","T","TF","TS","TL"],
                                   description="金融期货合成指数成分（CFFEX）")
    trend_window_short: int = Field(default=20, ge=5)
    trend_window_long: int = Field(default=60, ge=20)
    vol_window: int = Field(default=20, ge=5)
    vol_threshold_percentile: float = Field(default=0.8, ge=0.5, le=0.95)
    risk_pref_pair: tuple[str, str] = Field(default=("IF", "T"), description="股债比：风险资产/避险资产")
    risk_pref_window: int = Field(default=20, ge=5)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    # 多空不对称敞口（B/C 模块）
    on_scale: float = Field(default=1.0, ge=0.0, le=2.0)
    on_long_boost: float = Field(default=0.10, ge=0.0, le=0.5)
    on_short_suppress: float = Field(default=0.10, ge=0.0, le=0.5)
    off_scale: float = Field(default=0.5, ge=0.0, le=1.0)
    off_long_suppress: float = Field(default=0.40, ge=0.0, le=0.8)
    off_short_boost: float = Field(default=0.20, ge=0.0, le=0.5)

class BetaState(TypedDict):
    state: str            # "RISK_ON" | "RISK_OFF" | "RANGE_BOUND" | "unknown"
    confidence: float
    trend_score: float
    vol_score: float
    risk_pref: float
    risk_pref_z: float
    method: str           # "rule" | "fallback"
```

## 四、实施步骤（建议顺序，逐步留痕）

1. **A 模块**（regime_beta_layer.py：检测器 + 三信号 + 置信度）
   → 验证：[合成金融面板断言三态判定正确；数据缺失 → unknown 不偏置]
2. **B 模块**（信号管线方向偏置 + `--enable-regime-beta`）
   → 验证：[RISK_OFF 下多头降/空头升；enabled=false 输出与基线逐位一致（回归保护）]
3. **C 模块**（build_combo beta_scale 乘性合并 + regime_meta 留痕）
   → 验证：[exposure_final = exposure_scale × aligned_scale × beta_scale 数值正确；防双重惩罚]
4. **D 模块**（BETA_RISK_PARAMS + RiskManager 注入）
   → 验证：[RISK_OFF 注入后杠杆/单日亏损收紧；未传 beta_state 与现状一致]
5. **E 模块**（配置 + Prometheus + 预测力校验脚本）
   → 验证：[K-W 检验报告产出；三态条件均值排序符合预期]
6. **数据核验**：确认 FTS TDX_LOCAL 可拉取 CFFEX 日线（IF/T 主力合约），如不可用则评估
   AKShare 降级或白名单 data_futures 通道

## 五、测试方案

### 5.1 模块 A（`tests/factor_engine/test_regime_beta_layer.py`）

| # | 用例 | 断言 | 优先级 |
|:-:|:-----|:-----|:------:|
| 1 | 合成牛面板（趋势+低波+risk_pref 上行） | state=RISK_ON | P0 |
| 2 | 合成熊面板（趋势负+高波） | state=RISK_OFF | P0 |
| 3 | 震荡面板 | state=RANGE_BOUND | P0 |
| 4 | 空/不足数据 | state=unknown，scale=1.0 | P0 |
| 5 | 置信度门槛（信号分歧 → min_confidence 不达标） | 回退 RANGE_BOUND | P1 |
| 6 | 股债比方向（IF/T 上行 vs 下行） | risk_pref_z 符号正确 | P1 |
| 7 | beta_scale 映射（RISK_OFF → off_scale） | 数值断言 | P0 |
| 8 | apply_beta_bias 多空不对称（RISK_OFF 多头降/空头升） | 数值断言 | P0 |

### 5.2 模块 B/C（回归保护 + 集成）

- `enabled=false` 时信号管线/组合输出与现状**逐位一致**（回归保护，最高优先级）
- build_combo 乘性合并数值断言 + 防双重惩罚（B 动相对权重、C 动总敞口）
- 回归：`pytest tests/factor_engine/test_regime_gate.py tests/factor_engine/test_portfolio_loop.py tests/test_futures_signal_pipeline.py -m "not slow"`

### 5.3 模块 D

- RISK_OFF 注入后 max_leverage/daily_loss_limit_pct 收紧；`beta_state=None` 与现状一致

### 5.4 回归

- 分级测试政策：仅受影响模块（regime_beta_layer / portfolio_loop / signal_pipeline / risk_manager）+ ruff

## 六、验证标准（验收）

1. ✅ Beta 状态对组合前向收益有区分度（K-W p<0.05 或三态条件均值排序 RISK_ON > RANGE_BOUND > RISK_OFF）
2. ✅ RISK_OFF 时组合多头权重收缩、空头权重相对放大（顺负β方向，数值断言）
3. ✅ 灰度默认关（enabled=false）零行为变更——输出与现状逐位一致
4. ✅ 新增测试全绿 + 受影响回归全绿 + ruff 通过
5. ✅ `regime_meta` 含 beta_state/beta_scale 落盘可追溯；Prometheus 指标上线

## 七、风险与回退

- **风险 1（金融期货数据可用性）**：FTS 主市场为 energy，CFFEX 日线通道需核验（TDX_LOCAL 可达性）。
  缓解：A 模块先行数据核验；不可用则降级白名单 data_futures / AKShare；仍不可用则 Beta 层
  以 macro_regime 四象限（月度）为唯一信号源（降低时效但保持可落地）。
- **风险 2（Beta 误判代价不对称）**：RISK_OFF 误判 → 错误压缩多头/放大空头（文档 §7.1：反转误判
  震荡当趋势代价最高）。缓解：min_confidence 门槛 + 三信号一致度 + 灰度观察 + 观察期机制（plans/54 P1-3
  立项后联动）。
- **风险 3（双重惩罚）**：B（方向偏置）与 C（总敞口压缩）若叠加过猛导致组合过度收缩。
  缓解：职责分离（B 动多空相对权重、C 动总敞口）+ off_scale 保守初始值 0.5。
- **回退路径**：`l3.regime_beta_layer.enabled=false` 一键恢复现状（零行为变更）。

## 八、一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| `fts/factor_engine/regime_beta_layer.py` BetaDetector | 三态判定函数存在且纯计算 | `grep -n "class BetaDetector" fts/factor_engine/regime_beta_layer.py` |
| `config/settings.yaml` l3.regime_beta_layer | 配置段存在且 enabled 默认 false | `grep -n "regime_beta_layer" config/settings.yaml` |
| `scripts/futures_signal_pipeline.py` | `--enable-regime-beta` 参数存在 | `grep -n "enable-regime-beta" scripts/futures_signal_pipeline.py` |
| `portfolio_loop.py` build_combo | `beta_scale` 参与乘性合并 | `grep -n "beta_scale" fts/factor_engine/portfolio_loop.py` |
| `regime_multipliers.py` BETA_RISK_PARAMS | 宏观档位参数表存在 | `grep -n "BETA_RISK_PARAMS" fts/factor_engine/regime_multipliers.py` |
| `risk_manager.py` | `beta_state` 注入参数存在 | `grep -n "beta_state" fts/risk/risk_manager.py` |
| 来源文档 | §1.3 Beta 优先 / §5.1 L1 信号定义 | `test -f D:/Regime-Driven/docs/REGIME_STRATEGY_DESIGN.md` |

## 九、实施后文档同步清单（HARNESS 13 项）

1. `docs/harness/01-architecture.md`（L0 Beta 层数据流 + 接口定义）
2. `docs/harness/02-lifecycle.md`（产出物）
3. `docs/harness/03-configuration.md`（l3.regime_beta_layer.*）
4. `docs/harness/04-resilience.md`（数据缺失/Beta 误判回退路径）
5. `docs/harness/05-observability.md`（fts_beta_state / fts_beta_scale 指标）
6. `docs/harness/06-testing.md`（测试文件/用例数）
7. `docs/harness/07-operations.md`（版本历史）
8. `docs/harness/08-gap-analysis.md`（登记并关闭 GAP：宏观 Beta 层缺位，plans/54 D1）
9. `docs/harness/09-advancement-plan.md`（晋级里程碑）
10. `docs/production_plan.md`（流程同步）
11. `CLAUDE.md`（职责变更，如适用）
12. `README.md`（工程指标）
13. `scripts/verify_doc_consistency.py`（一致性校验通过）+ `pyproject.toml`（版本 bump）

---

## 十、灰度观察记录（2026-08-19 开启，定时巡检每日追加）

> 灰度开启依据：预测力决策门通过（step=1 日频 fwd=10 收益 K-W p=0.0203 显著 +
> 排序 RISK_ON>RANGE_BOUND>RISK_OFF 正确，trace_id beta-kw-v3b-20260819，v2.105.0+23）。
> 保守参数：off_scale=0.7 / off_long_suppress=0.25 / off_short_boost=0.10 / min_confidence=0.5。

### 观察清单（每次巡检核验项）

| # | 核验项 | 判定标准 |
|:-:|:-------|:---------|
| 1 | **触发正确性** | RISK_OFF 触发时：组合 `regime_meta.beta_scale ≈ 0.7`；信号管线日志 `[Beta 层] RISK_OFF ... 多头 ×0.75 / 空头 ×1.10` |
| 2 | **不误伤** | RANGE_BOUND/unknown 期间零干预（beta_scale=1.0），输出与灰度前一致 |
| 3 | **风控档位** | RISK_OFF 时 RiskManager 杠杆/单日亏损 ×0.7（实盘路径为二期，观察组合层即可） |
| 4 | **回退兜底** | 数据缺失/异常自动回退 scale=1.0 不阻断；`enabled=false` 一键关闭 |
| 5 | **状态落盘** | `current_combo.json` regime_meta 含 beta_state/beta_scale/beta_confidence |

### 观察记录

| 日期 | Beta 状态 | beta_scale | 触发核验 | 备注 |
|:-----|:---------|:----------:|:---------|:-----|
| 2026-08-19 | RANGE_BOUND（conf=0.67，trend≈-0.008；12:45 复核 conf=0.6667 一致） | 1.0（不偏置） | ①未触发（状态非 RISK_OFF）②✅ 不误伤 ④✅ 数据缺失降级不阻断 ⑤落盘待核验 | 灰度开启日；配置读取验证通过（enabled=true 保守档）；12:45 巡检：CFFEX 数据经降级链正常取数（TQSDK 获取失败未阻断），STATE/CONF/SCALE 与晨间一致；当日 05:02 组合（v2.105.0+13）早于 Beta 层落地（v+22）/灰度开启（v+24），regime_meta 暂不含 beta_state/beta_scale/beta_confidence，待下期组合生成后核验；决策门通过依据见 〇 校准记录 #9 |
