# 54 — Regime-Driven 思想吸纳与 FTS 改进方向规划

> 版本: v3.0.0+11（当前基线） · 文档类型: 方向规划（推进中）

> 状态: ✅ 全部方向已完成（P0×3 + P1×3 + P2×3 + P3×1，v2.105.0+26） · 优先级: P1 · 负责人: FTS Agent
> 来源文档: `D:\Regime-Driven\docs\REGIME_STRATEGY_DESIGN.md`（v3.0.3 定稿，2026-08-18）
> 关联: plans/28（Regime 机构级优化）、plans/47（子链差异化权重）、plans/48（Regime 分层门控）、plans/53（Regime 条件化因子交易）、plans/55（P1-1 Beta 层落地，已实施）、regime.py / regime_gate.py / regime_routing_rules.yaml / macro_regime.py / position_rank_crowding.py

---

## 一、背景与目的

外部项目 `D:\Regime-Driven` 产出一份 Regime 分层策略方案（v3.0 定稿），核心立场为
"**识别优于预测、Beta 优先顺风、第一性三问审查**"。经对照 FTS 系统现状（regime.py、
regime_gate.py、regime_profile.py、energy_qa_review.py、portfolio_loop.py、monitor 体系、
资金管理模块），FTS 已具备**量价五制度识别 + 子链 Gate + Regime 条件化因子交易 + 置信度仓位缩放**
的完整骨架（plans/28/47/48/53 落地），但仍有若干关键思想未覆盖或未接线。

本计划不涉及具体实施，仅登记**差距清单与改进方向**，作为后续立项依据；立项时按
HARNESS 13 项检查清单 + GAP 登记流程逐个推进。

## 二、来源文档核心思想（九大支柱）

| # | 思想支柱 | 核心论点 | 来源文档章节 |
|:-:|:---------|:---------|:------------|
| 1 | **识别优于预测** | 预测 Alpha 被套利主动消灭（不可逆）；识别 Regime 只被遗忘（可维护）。交易本质 = 状态不确定下的概率决策 | §1.1-1.2 |
| 2 | **Beta 优先（听风者）** | 不预测风、只感知风、顺风（多/空）配置敞口；Alpha 仅作识别错误的缓冲 | §1.3 |
| 3 | **哲学深度第一标准** | 策略质量上限由"凭什么赚钱"的哲学决定，技巧/数学只决定实现效率 | §1.4 |
| 4 | **第一性三问审查** | 是否合理（剥离数学的自然语言假设书）→ 边界在哪（invalid_when 失效清单）→ 怎么应对（防御动作表） | §3 |
| 5 | **三层 Regime** | L1 宏观（规则，股债比 risk_pref）→ L2 产业链（HMM 制度层 + 驱动语义层）→ L3 品种（量价 + OI + 期限代理） | §5 |
| 6 | **策略三铁律** | 条件性绑定（invalid_when）、正交性、软路由（状态→"集合+权重"非唯一映射） | §6 |
| 7 | **三大防御层** | 识别失效（置信度门控 + 观察期 + 对称化仓位）、拥挤透支（6 信号 + 联合门控）、方向不可判（Alpha 缓冲） | §7 |
| 8 | **先假设后数据** | 回测 IC 高不再是准入理由，因子必须回答"经济学故事" | §8 |
| 9 | **前提监控** | 监控"波动结构是否还在"（前提）而非"收益率是否还在"（结果） | §9.2 |

## 三、FTS 现状映射

### 3.1 已覆盖（无需再建）

| 文档思想 | FTS 对应实现 | 位置 |
|:---------|:-------------|:-----|
| 状态识别（多检测器） | HMM（单/多周期）+ MSM + 规则法五制度 + 子链/品种二级检测 | `fts/factor_engine/regime.py`（detect 链 L933） |
| 置信度门控 + 参与仓位 | `_compute_exposure_scale`（统计/熵标定）+ `map_confidence_to_exposure` 分段 | `portfolio_loop.py` L640 / `regime_gate.py` L161 |
| 子链方向 Gate / 软路由 | long/short/avoid/neutral + 暴露缩放 + 收益来源族激活 | `regime_gate.py`（plans/48 完成） |
| Regime 条件化因子交易 | K-W 决策门 + regime 画像资产化 + 条件化降权 + 晋升门槛 | `regime_profile.py` / `regime_conditional_weight.py`（plans/53 完成） |
| 因子失效熔断 | L2 退化检测（shadow/degraded/retire）+ 30 日冷却 + scope 收缩 | `energy_qa_review.py` |
| HMM 漂移监控 | 熵/转移矩阵/KL 散度预警 + 定期 refit | `regime.py` RegimeTransitionWarner |
| 经济学故事审查（部分） | 经济逻辑四维评分（≥3 维达标）+ narrative 必填 + Q2 一票否决 + 因果反事实 | `evaluation_chain.py` L517 / `qa/pre_entry.py` L54 / `causal_validator.py` |
| 降档而非反手（近似） | 子链 avoid = "不参与"而非反向 | `regime_gate.py` |
| 波动率制度→风控参数 | G14 参数表（杠杆/止损/单日亏损按制度切换） | `regime_multipliers.py` |

### 3.2 关键差距（D1-D10，即改进方向）

| # | 差距 | 现状证据（已核实） |
|:-:|:-----|:-------------------|
| D1 | **Beta 层缺失**（识别风口顺β方向） | `config/regime_routing_rules.yaml` global_rules（RISK_ON/OFF 三态）**零代码消费**；`macro_regime.py`（CPI/PMI 四象限）已实现**未接线**；bear 制度仅降权/降杠杆，不转空/不配置负 Beta 敞口；无 IF/T 股债比信号 |
| D2 | **invalid_when 失效条件清单缺失** | 全仓零匹配 `invalid_when`；因子不声明"什么形态下亏钱"，仅系统侧 excluded_factors/avoid 配置（`regime_routing_rules.yaml`） |
| D3 | **拥挤度体系不成体系** | `position_rank_crowding.py`（会员持仓排名）**未接入 L3 主流程**；6 信号仅 turnover_overheat 直接对应（`fut_crowd_turnover`），volume_stall/vol_structure **完全缺失**；**无拥挤×置信度联合门控**；crowding_score 全 abs() 取模，**多空方向被抹掉**（无法识别逼空） |
| D4 | **无 Regime 跳变观察期** | 仅概率平滑（0.7 保留）+ 同日防抖 + 连续 7 日不稳→降仓复审；无"跳变后观察 N 日确认再切换" |
| D5 | **无对称化仓位** | 减仓完善（exposure_scale / transition 70%），但"低置信→缩小策略种类"仅子链粒度 scope 收缩，无策略族粒度 |
| D6 | **无 Alpha 缓冲** | 无 Beta 层，自然无"置信度低→切低 Beta 高 Alpha 模式" |
| D7 | **半衰期未入生命周期** | 仅准入筛选有半衰期下限（`high_ic_screener.py` L719）；退化检测全固定阈值（IC 降幅 30%/50%、6M 斜率） |
| D8 | **前提监控未到市场前提** | `logic_monitor.py` 监控因子行为前提、`data_level_monitor.py` 监控数据前提；无"波动结构/趋势结构是否还在"类市场前提 |
| D9 | **无风险预算分配 + 头寸公式** | 有风险平价/波动目标/最小方差/凯利/BL 五种算法，但默认 `synthesis.mode: equal_weight`；MHF 固定等权 target_pct，无 f(信号,置信度,波动,预算) |
| D10 | **驱动语义层（L2）未接线** | `regime_routing_rules.yaml` sector_rules 的 cost/demand/crude 驱动配置零消费；FTS 用子链维度（47/48）替代了驱动语义维度 |

## 四、改进方向（按"先证明识别 → 再证明应对 → 最后放大仓位"排序）

### P0 — 地基补齐（成本低、直接呼应文档核心纪律）

#### P0-1（D2）invalid_when 失效条件清单 — ✅ 已实施（v2.105.0+25，纯观测层）
- **目标**：让因子显式声明"在什么市场形态下会亏钱"，把失效条件从系统侧配置下沉为因子资产属性。
- **落地要点**：仿 `subchain_scope`/`regime_scope` 资产化模式，`factor_catalog.metadata` 增
  `invalid_when`（失效状态/触发信号/失效级别，对应文档 §3 第二问）；L2 评审 `_shrink_scope`/
  退化检测复用，失效条件触发时自动降权/剔除。
- **验证标准**：因子 metadata 可查询失效条件；L2 评审输出含失效条件命中统计。
- **实施结果**：`contracts.py` 新增 `InvalidWhen` TypedDict + `FactorProgram.invalid_when`（可选）；
  `energy_qa_review.py` 新增 `check_invalid_when` 纯函数（顶层字段或 metadata 兼容，字符串容错）+
  `_stage_invalid_when`（当前 regime 一次检测 + 遍历因子比对）+ 报告 `invalid_when 失效条件命中` 段
  （纯观测不干预 disposition）；新增 test_invalid_when.py 8 用例 + energy_qa_review 回归 28 全绿；
  **自动降权/剔除（远期）**：命中→shadow 观察或权重归零留待接入会员数据源/灰度验证后扩展。

#### P0-2（D7）半衰期接入生命周期 — ✅ 已实施（v2.105.0+25）
- **目标**：退化判定从"固定阈值"升级为"滚动窗口 IC 半衰期估计"。
- **落地要点**：将 `high_ic_screener._check_signal_halflife`（半衰期公式 `ln(0.5)/ln(1-decay)*126`）
  推广为生命周期退化检测维度，与现有 IC 降幅/6M 斜率并列（文档 §8.3 半衰期估计）。
- **验证标准**：退化检测新增半衰期维度输出；与固定阈值判定一致性对照。
- **实施结果**：`factor_lifecycle.py` 新增 `estimate_ic_half_life(decay_6m)` 公共函数（与
  high_ic_screener 同口径，零衰减 inf/完全衰减 1.0/无效 None 边界防护）；
  `energy_qa_review.py` `decide_factor` 新增 `half_life_days` 维度（< cfg.half_life_min_days=63
  → shadow + "半衰期过短" 原因，宁严勿松叠加），`_stage_degradation` 从因子 evaluation.decay_6m
  估算传入（缺失 → None 不触发向后兼容）；新增 test_half_life.py 10 用例 + 相关回归 46 全绿。

#### P0-3（D8）前提监控增强（市场前提） — ✅ 已实施（v2.105.0+25）
- **目标**：落实"优先监控前提条件，而非输出指标"（文档 §9.2）。
- **落地要点**：`logic_monitor.py` 增加"市场前提"维度——趋势结构（ADX/多周期趋势分）、
  波动结构（realized vol 分位）是否仍在激活制度内；与因子行为监控并列，前提消失报警先行。
- **验证标准**：市场前提维度触发与因子结果衰减的先后关系可统计（前提先行率）。
- **实施结果**：`logic_monitor.py` 新增 `check_market_premise(panel, active_regime)` 面板级函数
  + `MarketPremiseResult`——等权指数 trend_score（MA20-MA60）+ vol 历史分位，按制度约束对应
  维度（bull/bear 约束趋势结构、high_vol 约束 vol≥下限、oscillate/low_vol 约束 vol≤上限、
  未知/面板不足不误报），前提消失输出"市场前提消失[regime]: 趋势结构消失/波动结构异常"告警；
  新增 test_market_premise.py 7 用例 + logic_monitor 回归 28 全绿。
- **落地补盲（v2.105.0+30，GAP-155）**：① `check_market_premise` 新增 `vol_window=252` 默认参数
  固定分位窗口（对齐 regime.py `_VOL_HISTORY_DAYS`，消除 180d/252d/437d 窗口分位漂移
  0.45/0.57/0.71 的阈值边界不稳）；② `regime.py` 新增 `high_vol_premise_check`——规则法 vol
  前提交叉验证（EWMA≥q80 或 20d 波动分位≥0.5 任一成立即前提有效，数据不足不误报）；
  ③ portfolio_loop Step 2.5 接线（仅 energy）：组合 high_vol 标签前提不成立 → 标签覆盖
  oscillate + conf×0.6 + method 标注 premise_override，regime_meta 新增 `premise_cross_check`
  段（ok/ewma_vol/eff_high/vol_percentile/reason/overridden_to）透明留痕；
  新增测试 7 用例（test_market_premise +2 + test_regime_premise_check.py 5）。

### P1 — 核心思想落地（文档的灵魂）

#### P1-1（D1）Beta 层落地（识别风口顺β方向） — ✅ 已实施（plans/55，v2.105.0+24，灰度保守档开启）
- **目标**：为 FTS 增加"L0 宏观 Beta 层"——识别市场 Beta 方向（RISK_ON/OFF），
  期货多空双向下顺 β 方向配置全局敞口（文档 §1.3 Beta 优先）。
- **落地要点**：
  1. 接线 `macro_regime.py`（PMI/CPI 四象限）为宏观层来源；或增加股债比 risk_pref
     （IF/T 指数比，文档 §5.1 L1）作为风险偏好信号；
  2. 激活 `global_rules` 死配置（总仓位上限/单品种上限/做空开关/杠杆），
     RISK_OFF 时允许反向做空（负 Beta 进攻）而非仅降权；
  3. 与现有 L1-L3 串联：L0 定全局敞口 → L1-L3 定方向与因子（Beta 是 alpha 的放大/收缩层）。
- **验证标准**：RISK_ON/OFF 标签对组合前向收益有区分度（K-W 检验，复用 plans/53 D 模块模板）；
  global_rules 消费点存在且可测。
- **实施结果（plans/55）**：`regime_beta_layer.py` BetaDetector（金融合成指数趋势 + vol 门控 +
  IF0/TF0 股债比 z-score 软投票三态）；信号管线 Step 3h1.5 多空不对称偏置 + build_combo
  beta_scale 乘性缩放 + BETA_RISK_PARAMS 实盘风控档位；决策门通过（step=1 日频 fwd=10 收益
  K-W p=0.0203 显著 + 排序正确，trace_id beta-kw-v3b-20260819）；灰度保守档已开启
  （enabled=true + off_scale 0.7）。**校准差异**：risk_pref_pair 用 IF0/TF0（FTS 池无 T0）；
  macro_regime 四象限作二期慢层未接线；global_rules 未激活（一期以 Beta 层参数化替代）。

#### P1-2（D3）拥挤度体系化 — ✅ 已实施（plans/56，v2.105.0+25，灰度默认关；决策门 ❌ 未通过）
- **目标**：补齐文档 §7.2 拥挤度门控——6 信号合成 + 拥挤×置信度联合门控 + 多空方向。
- **落地要点**：
  1. 补齐信号：新增 volume_stall（放量滞涨/缩量阴跌）、vol_structure（波动结构异常）；
     corr_convergence 复用 `sector_linkage.compute_sector_linkage` 增强为动态趋近；
     oi_concentration 增加 OI 天量分位信号；
  2. 接入主流程：`position_rank_crowding` + 6 信号合成 → `build_combo`（portfolio_loop L2988
     仓位缩放处）与 `exposure_scale` 构成**拥挤×置信度联合门控**（高置信+高拥挤=减半、低置信+高拥挤=离场）；
  3. 恢复多空方向：crowding_score 不再全 abs()，保留 long_short_ratio 方向 → 区分多头拥挤
     （减多仓不抢反弹）与空头拥挤/逼空（减空仓不追空）。
- **验证标准**：联合门控表数值断言；逼空场景方向正确；关闭开关零行为变更。

#### P1-3（D4）Regime 观察期机制 — ✅ 已实施（v2.105.0+25）
- **目标**：状态跳变不立即切换，观察 N 日确认（文档 §7.1，"跳变违背持续性，默认怀疑需证据"）。
- **落地要点**：`regime.py`/`regime_voting.py` 增加显式观察窗——候选新制度连续 N 日（可配）
  保持才确认切换；观察期内维持旧制度仓位或降至最小观察仓位；与现有概率平滑/防抖互补。
- **验证标准**：模拟跳变序列下切换延迟 = N 日；观察期内不放大仓位。
- **实施结果**：`RegimeAwareSelector` 新增 `observe_days` 参数（默认 0=关闭兼容现状）+ 观察状态
  （`_observe_candidate/_observe_count`）：detect() 末尾应用观察窗——跳变时候选计数，`count < N`
  维持旧制度（result 标记 observed/candidate_regime/observe_count），连续 N 次确认切换（confirmed）；
  观察期内候选中途变回 → 计数重置；新增 test_regime_observe.py 5 用例 + regime 回归 92 全绿。

### P2 — 防御与仓位深化

| 方向 | 内容 | 依赖 |
|:-----|:-----|:-----|
| P2-1（D5）对称化仓位 — ✅ 已实施 | 低置信度不仅减仓，还缩小策略族/因子种类（文档 §7.1）；`capital_allocator.shrink_factor_diversity`（低置信 <0.4 保留 top 50% 因子重归一化，高置信不干预）；纯函数组件待组合层灰度接入 | 无 |
| P2-2（D6）Alpha 缓冲 — ✅ 已实施 | 置信度低时组合从高 Beta 切低 Beta 高 Alpha 模式（文档 §7.3）；`regime_beta_layer.alpha_buffer_scale`（高置信 1.0 纯 Beta / 中置信 0.5 减半 / 低置信 0.0 切 Alpha）；乘性作用于 Beta 层敞口，纯函数组件待接入 | P1-1 Beta 层（已完成） |
| P2-3（D9）风险预算分配 + 头寸公式 — ✅ 已实施 | 头寸 f(信号强度, 置信度, 波动率, 风险预算)（文档 §2.3 缺口）；`capital_allocator.compute_position_target`（|信号|×置信度×(预算/波动率)，clip 边界，波动率缺失保守）；与既有风险平价/波动目标等 5 算法互补 | 无 |

### P3 — 纪律强化

#### P3-1（文档 §3）策略假设书三问审查 — ✅ 已实施（v2.105.0+25，灰度默认关）
- **目标**：新策略/因子纳入时强制"剥离数学的自然语言陈述"审查（是否合理→边界在哪→怎么应对）。
- **落地要点**：开启 `contracts.py` 中默认关闭的 narrative 机制关键词深度校验
  （`require_argument_consistency`，GAP-123 P2④）；评估链/晋升链增加"假设书完整性"门槛维度
  （经济学解释 + 前提条件 + 反例证据，对应文档 §3.1 模板一）。
- **验证标准**：narrative 含机制关键词才放行；假设书三要素缺失的因子标记打回。
- **实施结果**：机制已存在（`meta_loop.L1Verifier.check` GAP-123 P2④：维度评分 ≥3 必须 narrative
  含机制关键词，`require_argument_consistency` 默认关零行为变更）；新增 test_combo_deepening.py
  验证开启后行为（高分低论证被拦 / 含机制词放行 / 关闭不拦）+ 文档登记灰度开启建议
  （`invalid_when` 声明（P0-1）承载"边界在哪"第二问，评审报告段承载审计）——三问闭环：
  假设书（narrative+机制词）→ 失效条件（invalid_when）→ 防御动作（评审/退化/半衰期 P0-2）。

## 五、落地路径与优先级

| 序 | 阶段 | 动作 | 建议 |
|:-:|:-----|:-----|:-----|
| 1 | 立项 | 按 P0 → P1 → P2 → P3 顺序，每方向独立立项（plans/55+），登记 GAP | 先行 P1-1（Beta 层）或 P1-2（拥挤度），价值最高 |
| 2 | 数据 | P1-1 需确认宏观数据源（PMI/CPI 已有 edb/akshare 链路；IF/T 需下载 CFFEX 日线）；P1-2 需 OI/换手数据（已有） | 数据可用性先行核查 |
| 3 | 契约 | 每方向先定 Pydantic 契约 + metadata 字段，再实现 | 遵循契约优先 |
| 4 | 测试 | 每方向配套单测 + 灰度开关（默认关，零行为变更） | 遵循分级测试政策 |
| 5 | 文档 | 每方向按 HARNESS 13 项检查清单同步 | 文档先行 |

## 六、风险与回退

- **风险 1（Beta 层引入方向性风险）**：FTS 是 Alpha 因子系统，Beta 层激活做空可能放大回撤 →
  灰度默认关 + RISK_OFF 上限约束 + 与 exposure_scale 正交合并，异常一键回退。
- **风险 2（拥挤度误报）**：拥挤信号噪声大 → 联合门控仅降档不反手，阈值事件研究法校准
  （文档附录 A.1），阈值 ±1 表现突变则弃用。
- **风险 3（观察期滞后）**：观察 N 日可能错过趋势初段 → 观察期仅约束放大不约束减仓
  （de-risk 快、re-risk 慢，对齐 RegimeSmoother）。
- **回退路径**：所有方向均以灰度开关落地（默认关），关闭即恢复现状。

## 七、一致性元数据

| 代码/配置 → 文档映射 | 关键断言/可验证事实 | 检验方式 |
|:---------------------|:---------------------|:---------|
| `config/regime_routing_rules.yaml` global_rules（§3.2 D1） | 当前零代码消费（改进方向 P1-1 待接线） | `grep -rn "global_rules" fts/ scripts/` 无命中 |
| `fts/factor_engine/macro_regime.py` MacroRegimeDetector（§3.2 D1） | 已实现未接线（改进方向 P1-1 接线） | `grep -rn "MacroRegimeDetector" fts/ --include=*.py` 仅自身/seed 命中 |
| `fts/factor_engine/position_rank_crowding.py`（§3.2 D3） | 未接入 L3 主流程（改进方向 P1-2 接入） | `grep -rn "position_rank_crowding" fts/factor_engine/portfolio_loop.py` 无命中 |
| 全仓 `invalid_when`（§3.2 D2） | 零匹配（改进方向 P0-1 新增） | `grep -rn "invalid_when" fts/ config/` 无命中 |
| `fts/factor_engine/high_ic_screener.py` `_check_signal_halflife`（§3.2 D7） | 半衰期公式已存在，仅准入层用（改进方向 P0-2 推广） | `grep -n "half_life" fts/factor_engine/high_ic_screener.py` |
| `fts/monitor/logic_monitor.py`（§3.2 D8） | 无市场前提维度（改进方向 P0-3 新增） | `grep -n "regime\|trend_structure\|vol_structure" fts/monitor/logic_monitor.py` 无命中 |
| `fts/factor_engine/regime.py` / `regime_voting.py`（§3.2 D4） | 无显式观察窗（改进方向 P1-3 新增） | `grep -rn "observe.*day\|observation.*window" fts/factor_engine/regime*.py` 无命中 |
| 来源文档 | `D:\Regime-Driven\docs\REGIME_STRATEGY_DESIGN.md` v3.0.3 存在 | `test -f D:/Regime-Driven/docs/REGIME_STRATEGY_DESIGN.md` |
