# 56 — 拥挤度体系化计划（D3：6 信号 + 联合门控 + 多空方向）

> 版本: v3.1.0+N（build bump 后更新） · 文档类型: 实施立项计划

> 状态: ✅ 已实施（A-D 四模块完成，灰度默认关）· 决策门校准后仍 ❌ 未通过 → **正式降级为纯观测层**（enabled=false，仅 Prometheus/报告观测） · 优先级: P1 · 负责人: FTS Agent
> 来源思想: `D:\Regime-Driven\docs\REGIME_STRATEGY_DESIGN.md` §7.2（拥挤度门控）、附录 A（事件研究法校准）
> 关联: plans/54（P1-2 立项）、plans/55（Beta 层，方向层互补）、position_rank_crowding.py / sector_linkage.py / portfolio_risk_controls.py / futures_signal_pipeline.py / portfolio_loop.py

---

## 〇、实施校准记录（实现反哺设计，与 §方案差异点 + 决策门结果）

| # | 设计点 | 设计文档原定 | 实施校准 | 原因/结果 |
|---|---|---|---|---|
| 1 | corr 滚动相关 | DataFrame.rolling().corr() 无参 | **逐品种对 Series.rolling().corr()** | 无参调用返回全 NaN（单测暴露） |
| 2 | volume_stall 量能 | z-score（vol_ma.std() 稀释） | **ratio 口径（volume/20日均量 > 1.5）** | z-score 被序列方差稀释不触发（单测暴露） |
| 3 | turnover 触发条件 | 含 `current > 1.0` 绝对约束 | **仅历史分位**（去绝对 >1） | 真实换手率=volume/hold 通常 <1，绝对约束使信号永不触发（诊断暴露） |
| 4 | 面板对齐 thresh | `max(2, n//2)` | **`max(1, n//2)`** | 单品种面板 thresh=2 > 列数 1 全 drop（诊断暴露） |
| 5 | 可用信号/fallback 语义 | 混淆 | **值有效或触发明确定义；全不可用 → fallback** | 空面板误判 rule（单测暴露） |
| 6 | **决策门结果** | 期望 K-W 显著 + 排序正确 | **❌ 未通过**：高拥挤组仅 2/105 样本（<3 不可检验）；事件研究命中率 0%（0/10 崩盘前触发）、误报率 100%（2/2）；真实面板 6 信号全不触发（score=0.0） | ① high_crowding=0.6 + 6 信号分位阈值过严，300 日能源链几乎无触发；② 拥挤信号（量价交易拥挤）与崩盘事件（基本面/宏观驱动）语义错配；③ 拥挤为慢变量、崩盘为快事件——"触发后 20 日无 ≥10% 回撤"高误报符合现实 |
| 7 | 灰度状态 | 决策门通过后开启 | **保持 enabled=false（未开启）** | 决策门未通过，按纪律不开启灰度（对齐 plans/53 D 模块"不显著 → 停，排查"） |
| 8 | 下一步建议 | — | ① 校准 6 信号分位阈值 + high_crowding（先让合成 score 在真实数据有分布）再重跑决策门；② 重新定义事件研究窗口（拥挤→崩盘滞后 >5 日）；③ 若校准后仍无区分力，接受"量价拥挤度对能化链崩盘无前兆"结论，降级为纯观测层（GAP-069 持仓拥挤度保留） | 参照 Beta 层 v1→v3b 迭代纪律 |
| 9 | **校准重跑结果（建议 1）** | high_crowding=0.6→0.4、分位 0.9→0.75 | **仍 ❌ 未通过**：样本分布修复（高拥挤 36/105 可检验）、排序正确（高拥挤回撤 -20.35% > 低拥挤 -8.97%），但 K-W p=0.7894 不显著；事件研究命中率 50%（<70%）、误报率 68.6%（>40%）；校准揭示量价拥挤度捕捉的是"高波动/高参与"状态（高拥挤组收益 +22.14% 反而更高）而非崩盘前兆 | 校准值保留（score 分布更合理，观测更有意义）；单测 test_direction_neutral_low_score 显式 high_crowding=0.6 保持原语义 |
| 10 | **最终处置（建议 2）** | — | **正式降级为纯观测层**：enabled=false（不干预组合），拥挤度仅作 Prometheus crowding_scale/crowding_state + 报告观测；GAP-153 关闭为"降级观测层"；GAP-069 持仓拥挤度（会员数据）保留独立价值，若后续接入会员数据源可重新评估 | 决策门两轮未通过（原参数/校准参数），量价拥挤度对能化链崩盘无前兆证据——诚实接受，不做无依据的硬接入 |

## 一、背景与问题定位

### 1.1 核心洞察：Beta 方向识别之后，还要识别方向是否被透支

外部 Regime-Driven §7.2：Beta 优先 ≠ 永远满仓顺向敞口——识别 Beta 方向（多/空）之后
还要识别它是否已被**拥挤透支**。期货多空双向下，拥挤既可能发生在多头，也可能发生在空头
（如大举做空后的逼空）。三大防御层中"拥挤透支"是独立于"识别失效"的一层：
价值不在"经常对"，而在"错的时候亏得少"。

### 1.2 系统现状盘点（已核实）

| 现状 | 证据 |
|:-----|:-----|
| `position_rank_crowding.py`（会员持仓排名拥挤度，GAP-069）已实现**未接入 L3 主流程** | 全仓仅自身与测试引用；portfolio_loop/signal_pipeline 无引用 |
| 量价拥挤度种子家族存在（`fut_crowd_volume/volatility/turnover/bias/composite`）但**不可控** | seed 形式，是否被演化选中进组合不可控 |
| 6 信号仅 `turnover_overheat` 直接对应（`fut_crowd_turnover`） | volume_stall/vol_structure 完全缺失；corr/momentum_decay/oi 为近似实现 |
| **无拥挤×置信度联合门控** | 全仓无 confidence×crowding 组合门控矩阵 |
| `crowding_score` 全 `abs()` 取模，**多空拥挤方向被抹掉**（无法识别逼空） | position_rank_crowding L270-279 |
| **无拥挤期止损收窄** | 止损只由 regime（高波动）驱动，非拥挤度驱动 |
| 板块相关性熔断已存在 | `check_correlation_circuit_breaker`（portfolio_risk_controls，均值相关 >0.8）——但仅组合级熔断，非 6 信号体系 |

### 1.3 缺口定义（对应 plans/54 D3）

| # | 缺口 | 表现 |
|---|---|---|
| G1 | **6 信号不全** | volume_stall/vol_structure 缺失；corr_convergence 为静态强度非动态趋近；oi_concentration 无 OI 天量信号 |
| G2 | **无联合门控** | 拥挤度与置信度各自独立，无"高置信+高拥挤=减半 / 低置信+高拥挤=离场"矩阵 |
| G3 | **多空方向被抹掉** | 无法区分多头拥挤（减多仓不抢反弹）与空头拥挤/逼空（减空仓不追空） |
| G4 | **未接入主流程** | position_rank_crowding 独立模块空转；量价拥挤度仅靠 seed 不可控 |
| G5 | **无拥挤期止损收窄** | 拥挤度高时止损阈值不收紧 |

### 1.4 与既有计划的边界

- plans/55（Beta 层）：Beta 层管**方向识别与敞口缩放**（L0 宏观），拥挤度管**透支防御**
  （L0.5 微观-中观）——Beta 层决定"顺不顺势"，拥挤度决定"势有没有被透支"，正交互补；
  两者并入同一乘性链（exposure_final = exposure_scale × aligned_scale × beta_scale × crowding_scale）；
- GAP-069（position_rank_crowding）：本计划将其从"独立模块"升级为 6 信号体系的一个
  数据源/信号（oi_concentration），缺失降级不阻断；
- GAP-067（相关性熔断）：组合级熔断保留，6 信号体系是更细粒度的拥挤度观测层。

## 二、方案设计

### A 模块 — 6 信号合成器（核心）

**新增 `fts/factor_engine/regime_crowding.py`**：

- `CrowdingSignalConfig`（Pydantic）：6 信号各自窗口/阈值 + 合成权重
- `CrowdingSignalResult`（TypedDict）：{crowding_score ∈ [0,1], direction: "long"/"short"/"neutral", signals: {信号名: 是否触发}, signal_values}
- `compute_crowding_signals(panel, config) -> CrowdingSignalResult`（板块/组合面板级）：

| # | 信号 | 定义（Tier A 量价可算） | 现状基础 |
|:-:|:-----|:------------------------|:---------|
| 1 | `corr_convergence` | 板块内品种两两相关（20 日滚动）vs 历史分位，趋近 1 且创新高 → 拥挤 | `sector_linkage.compute_sector_linkage`（增强为动态趋近） |
| 2 | `volume_stall` | 放量滞涨（量能比 z>阈值 且 价格涨幅近零/为负）/ 缩量阴跌 | 无（新增） |
| 3 | `momentum_decay` | 多周期动量差值（20-60 日动量差走弱）或动量强度 vs 波动偏离 | `ops_library.ts_momentum_crowding`（代理，增强） |
| 4 | `vol_structure` | realized vol 突升（最新 vs 20 日分位）或波动率 z-score 异常 | 无（新增） |
| 5 | `oi_concentration` | OI 天量分位（hold > 历史 90% 分位）或会员净持仓集中（CR_top_n） | `position_rank_crowding`（会员数据可选源，缺失降级） |
| 6 | `turnover_overheat` | 换手率（volume/hold 或成交额）vs 历史 90% 分位 | `fut_crowd_turnover`（复刻为信号函数） |

- **方向分解**：基于品种收益符号/净持仓方向 → `direction`（多头拥挤 / 空头拥挤 / 中性）——
  修复 G3（不再全 abs()）

### B 模块 — 拥挤×置信度联合门控

**`portfolio_loop.py` Step 2.5 + `build_combo`**：

- 联合门控矩阵（对齐文档 §7.2）：

| 置信度 | 拥挤度 | 有效敞口倍率 |
|:------|:------|:------------|
| 高 | 低 | 1.0（满仓顺向） |
| 高 | 高 | `crowding_high_conf_scale`（默认 0.5 减半） |
| 低 | 高 | `crowding_low_conf_scale`（默认 0.0 离场） |
| 其余 | — | 1.0 |

- 接入：`build_combo` 乘性链扩为
  `exposure_final = exposure_scale × aligned_scale × beta_scale × crowding_scale`
- **降档而非反手**：只收缩不反向（拥挤多头减多仓不抢反弹、拥挤空头减空仓不追空）
- 拥挤度来源：A 模块合成（组合面板 = energy 面板），数据缺失 → 1.0（零行为变更）

### C 模块 — 多空方向偏置（逼空识别）

**`futures_signal_pipeline.py` Step 3h**（Beta 层 Step 3h1.5 之后）：

- 按 `direction` 分品种方向缩放（与 Beta 层 apply_beta_bias 同构但语义相反方向）：
  - `long_crowding`：多头信号 ×(1 - long_crowd_suppress)、空头不动（减多仓不抢反弹）
  - `short_crowding`：空头信号 ×(1 - short_crowd_suppress)、多头不动（减空仓不追空/不逼空）
- 灰度开关：`l3.regime_crowding.enabled`（默认关）+ CLI `--enable-regime-crowding`

### D 模块 — 配置、监控与校验

- `config/settings.yaml`：`l3.regime_crowding.*`（enabled=false 默认关 + 6 信号阈值 + 联合门控系数 + 方向抑制系数）
- 质量报告新增 `crowding_state` 段（crowding_score/direction/触发信号列表）
- Prometheus：`fts_crowding_score` / `fts_crowding_direction`
- **校验脚本** `scripts/regime_crowding_predictive_power_check.py`（仿 Beta 层 v3 决策门）：
  拥挤度对后续回撤/收益的区分度（K-W p<0.05 且排序正确）+ 事件研究法阈值校准（文档附录 A.1：
  崩盘事件命中率 ≥70%、误报率 <40%，阈值 ±1 突变弃用）
- **拥挤期止损收窄**（G5，二期）：crowding 高分时 `stop_loss_pct` 按倍率收紧（复用 regime_multipliers 模式，本期登记不实现）

## 三、数据模型与契约（变更点）

| 对象 | 变更 | 说明 |
|---|---|---|
| `fts/factor_engine/regime_crowding.py`（新增） | `CrowdingSignalConfig` / `CrowdingSignalResult` / `compute_crowding_signals` / `build_joint_gate_scale` / `apply_crowding_direction_bias` | 契约优先，纯计算无 IO |
| `config/settings.yaml` | + `l3.regime_crowding.*`（enabled=false 默认关） | 参数化，禁硬编码 |
| `portfolio_loop.py` | Step 2.5 计算 crowding_scale + build_combo 乘性链并入 | 与 beta_scale 正交 |
| `scripts/futures_signal_pipeline.py` | Step 3h1.6 方向偏置 + `--enable-regime-crowding` | 仅 energy，失败降级 |
| `fts/monitor/prometheus_metrics.py` | + `fts_crowding_score` / `fts_crowding_direction` | 可观测 |
| `scripts/regime_crowding_predictive_power_check.py`（新增） | K-W 决策门 + 事件研究阈值校准 | 仿 Beta 层 v3 |

### 契约（Pydantic V2 草案）

```python
# regime_crowding.py
class CrowdingSignalConfig(BaseModel):
    enabled: bool = Field(default=False, description="灰度开关（默认关，零行为变更）")
    # 6 信号窗口与阈值
    corr_window: int = 20
    corr_percentile: float = 0.9          # 相关历史分位（创新高 → 触发）
    volume_z_threshold: float = 2.0       # 量能比 z（放量）
    price_stall_threshold: float = 0.01   # 放量滞涨：|涨幅| < 阈值
    momentum_diff_window: tuple[int, int] = (20, 60)  # 动量差窗口
    vol_spike_percentile: float = 0.9     # 波动突升分位
    oi_percentile: float = 0.9            # OI 天量分位（hold 缺失降级）
    turnover_percentile: float = 0.9      # 换手透支分位
    # 合成权重（和=1）
    signal_weights: dict[str, float] = {"corr_convergence": 0.2, "volume_stall": 0.15,
        "momentum_decay": 0.2, "vol_structure": 0.15, "oi_concentration": 0.15,
        "turnover_overheat": 0.15}
    high_crowding: float = 0.6            # 高拥挤阈值
    # 联合门控（B 模块）
    high_conf_high_crowd_scale: float = 0.5  # 高置信+高拥挤 → 减半
    low_conf_high_crowd_scale: float = 0.0   # 低置信+高拥挤 → 离场
    confidence_threshold: float = 0.5     # 置信度高/低分界
    # 多空方向抑制（C 模块）
    long_crowd_suppress: float = 0.30     # 多头拥挤：多头信号 ×0.70
    short_crowd_suppress: float = 0.30    # 空头拥挤：空头信号 ×0.70

class CrowdingSignalResult(TypedDict):
    crowding_score: float     # ∈ [0,1]
    direction: str            # "long" / "short" / "neutral"
    signals: dict[str, bool]  # 信号名 → 是否触发
    signal_values: dict[str, float]
```

## 四、实施步骤（建议顺序，逐步留痕）

1. **A 模块**（regime_crowding.py：6 信号 + 合成器 + 方向分解）
   → 验证：[合成面板断言 6 信号触发/合成 score/方向正确；数据缺失 → score 0 不崩溃]
2. **B 模块**（联合门控 + build_combo 乘性链并入）
   → 验证：[门控矩阵数值断言；enabled=false 输出与基线逐位一致（回归保护）]
3. **C 模块**（信号管线方向偏置 + `--enable-regime-crowding`）
   → 验证：[long_crowding 多头收缩/空头不动；关闭时与基线一致]
4. **D 模块**（配置 + Prometheus + 校验脚本）
   → 验证：[K-W 决策门报告产出；事件研究阈值校准表]
5. **回归 + 文档同步**（HARNESS 13 项）

## 五、测试方案

### 5.1 模块 A（`tests/factor_engine/test_regime_crowding.py`）

| # | 用例 | 断言 | 优先级 |
|:-:|:-----|:-----|:------:|
| 1 | 6 信号分别触发（构造各信号特征面板） | 对应信号 True，其余 False | P0 |
| 2 | 合成 score 加权正确 | score ∈ [0,1] 数值断言 | P0 |
| 3 | 方向分解（多头拥挤/空头拥挤/中性） | direction 正确 | P0 |
| 4 | 空/不足数据 | score=0.0，不崩溃 | P0 |
| 5 | 缺失 hold（OI 信号） | oi 信号降级跳过，不阻断 | P1 |

### 5.2 模块 B/C（回归保护）

- 联合门控矩阵数值断言（高置信高拥挤 0.5 / 低置信高拥挤 0.0 / 其余 1.0）
- `enabled=false` 时 build_combo/信号管线输出与基线逐位一致（最高优先级）
- 乘性链：exposure_final = exposure_scale × aligned × beta_scale × crowding_scale
- 回归：`pytest tests/factor_engine/test_portfolio_loop.py tests/test_futures_signal_pipeline.py -m "not slow"`

### 5.3 分级回归

- 仅受影响模块 + ruff；全量回归按分级测试政策（发布前/月度）执行

## 六、验证标准（验收）

1. ✅ 6 信号合成器：各信号特征面板触发正确 + score 加权 + 方向分解（含逼空场景）
2. ✅ 联合门控：矩阵数值断言；`enabled=false` 零行为变更（输出逐位一致）
3. ✅ 多空方向：多头拥挤减多不抢反弹、空头拥挤减空不追空（C 模块数值断言）
4. ✅ 决策门：拥挤度对后续回撤/收益区分度 K-W p<0.05 或事件研究命中率 ≥70%/误报率 <40%
   （数据支撑不足时如实标注，不编造）
5. ✅ 新增测试全绿 + 受影响回归全绿 + ruff 通过；版本 bump + 文档 13 项同步

## 七、风险与回退

- **风险 1（拥挤信号误报）**：量价拥挤度噪声大，误触发过度收缩损失机会 → 联合门控仅降档
  不反手 + 事件研究法阈值校准（±1 突变弃用）+ 灰度默认关。
- **风险 2（数据依赖）**：OI/会员持仓数据缺失 → 对应信号降级跳过（6 信号不全时用可用子集
  合成），score 归一化按实际可用信号数。
- **风险 3（双重惩罚）**：与 Beta 层（off_scale）/G1（同向敞口）叠加过度收缩 → 乘性链各层
  职责明确（Beta=宏观方向、拥挤=透支防御、G1=同向共振），保守初始系数 + 灰度观察。
- **回退路径**：`l3.regime_crowding.enabled=false` 一键恢复现状（零行为变更）。

## 八、一致性元数据

| 代码→文档映射 | 可验证断言 | 检验方式 |
|---|---|---|
| `fts/factor_engine/regime_crowding.py` compute_crowding_signals | 6 信号合成函数存在 | `grep -n "compute_crowding_signals" fts/factor_engine/regime_crowding.py` |
| `config/settings.yaml` l3.regime_crowding | 配置段存在且 enabled 默认 false | `grep -n "regime_crowding" config/settings.yaml` |
| `portfolio_loop.py` build_combo | crowding_scale 参与乘性链 | `grep -n "crowding_scale" fts/factor_engine/portfolio_loop.py` |
| `scripts/futures_signal_pipeline.py` | `--enable-regime-crowding` 参数存在 | `grep -n "enable-regime-crowding" scripts/futures_signal_pipeline.py` |
| 来源文档 | §7.2 拥挤度门控 / 附录 A 事件研究 | `test -f D:/Regime-Driven/docs/REGIME_STRATEGY_DESIGN.md` |

## 九、实施后文档同步清单（HARNESS 13 项）

1. `docs/harness/01-architecture.md`（拥挤度信号流 + 乘性链）
2. `docs/harness/02-lifecycle.md`（产出物）
3. `docs/harness/03-configuration.md`（l3.regime_crowding.*）
4. `docs/harness/04-resilience.md`（数据缺失/拥挤误报回退路径）
5. `docs/harness/05-observability.md`（fts_crowding_score/direction 指标）
6. `docs/harness/06-testing.md`（测试文件/用例数）
7. `docs/harness/07-operations.md`（版本历史）
8. `docs/harness/08-gap-analysis.md`（登记并关闭 GAP：拥挤度体系缺位，plans/54 D3）
9. `docs/harness/09-advancement-plan.md`（晋级里程碑）
10. `docs/production_plan.md`（流程同步）
11. `CLAUDE.md`（职责变更，如适用）
12. `README.md`（工程指标）
13. `scripts/verify_doc_consistency.py`（一致性校验通过）+ `pyproject.toml`（版本 bump）
