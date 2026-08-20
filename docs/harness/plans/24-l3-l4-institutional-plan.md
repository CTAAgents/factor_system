# L3/L4 机构级追赶专项实施计划（组合构建 + 优化执行 + 反馈闭环）

> 版本: v3.0.0+5
> 最后更新: 2026-08-10
> 状态: 已收尾（GAP-L301~L310 + L401/L402 全部落地，v2.61.0~v2.69.0）
> ⚠️ **归档注记（v2.104.0+25）**：本计划为历史已完成计划，其中 GAP-L308 的 family 相关内容（`REGIME_FAMILY_MULTIPLIERS` 硬编码表 / `RegimeMultiplierEstimator` / `load_data_driven_multipliers` / `_data/l3_regime_multipliers.yaml`）已随 v2.104.0+25 因子家族概念彻底移除而删除/废弃，正文保留原文仅供历史参考。
> 适用范围: L3 Portfolio Loop（组合构建）/ L4 优化与执行反馈层（资金分配·成本·归因·实盘反馈）/ L4 表达式组合算子层

> ⚠️ **计划定位说明**：本计划是 [23-institutional-transformation-plan.md](./23-institutional-transformation-plan.md)（机构级改造总纲，GAP-I001~I503）在 **L3/L4 两个层级**的执行细则。总纲只登记结构性差距；本计划把 L3/L4 相关差距展开到「代码级实施步骤 + 测试方案」粒度，缺陷编号沿用总纲 GAP-I 系列、新增执行级编号 GAP-L3xx / GAP-L4xx 承接，**不重复登记、只引用展开**。与 plans/21（期货）、plans/22（股票）的关系为「层内专项 ↔ 流水线专项」：GAP-S01/S02（股票中性化/风格）、GAP-F 系列（期货成熟度）为前置依赖。

---

## 0. 背景与目标

### 0.1 现状（L3/L4 全链路快照，v2.60.0）

**L3 Portfolio Loop**（`fts/factor_engine/portfolio_loop.py`）现状链路：

```
加载 elite 因子(质量门槛 IC≥0.03/Sharpe≥1.5) → OOS 外推验证 → ACTIVE_FACTOR_CAP=20
→ P1 聚类(0.7) → P2 PCA(默认关) → 信号合成(默认 elastic_net) → Regime 查表调权
→ 正交化(elastic_net 跳过) → 衰减检验 → 粘性约束 → 组合构建(估算指标) → Verifier → 注入 FDT
```

**L4 层现状**：
- **优化与执行层**：`portfolio_optimizer.py`（MVO/风险平价 + 杠杆/集中度/换手/VaR 约束，scipy SLSQP）已实现但**未接入 L3 主流程**；`capital_allocator.py`（fixed/vol_target/risk_parity/kelly + 保证金管理）为孤立模块；`cost_model.py`（滑点/手续费/展期，**无冲击成本**）；`risk_attributor.py`（因子贡献/暴露/VaR/ES）为孤立模块；`feedback_loop.py`（4 张反馈表 + CLI）**无实盘数据回流通道**（下游 FDT 未回传）。
- **表达式组合算子层**：`expr_dsl/registry.py` L4 仅 15 个基础算子（add/sub/mul/div/neg/abs/sign/sqrt/log/exp/min/max/clip/pow），用于 L2 演化表达式复合，无跨标的/横截面/条件类高阶算子。

### 0.2 目标

1. 把 L3/L4 与机构组合构建实践（因子收益序列 → 协方差/风险模型 → 约束优化 → 真实组合回测 → 归因 → 执行反馈）的系统差距登记为可执行缺陷项。
2. 以「组合指标实测化」为第一优先级：**组合夏普/相关性从估算公式改为因子收益序列实测**，Verifier 校验真实指标。
3. 打通 `PortfolioOptimizer`（协方差收缩 + 中性化约束 + 成本目标）进 L3 主流程，期货/股票共用同一组合层框架。
4. 建立 L4 反馈闭环（实盘反馈契约 → IC 对比 → 衰减退役联动），并强化表达式组合算子层。
5. 每项缺陷遵循「文档先行 → 契约优先 → 测试随重构」HARNESS 闭环，同步登记 `08-gap-analysis.md` 与 `09-advancement-plan.md`。

### 0.3 调研方法

- 基于 `d:\Programs\factor_system` 实际代码逐文件勘察（v2.60.0），"缺失/未生效"判定以代码搜索为据（Grep 零命中或调用链未达即视为未生效）。
- 机构对标口径沿用总纲 §0.3 三档基准（T1 中小团队 / T2 国内头部 / T3 海外顶级）。
- 关联文档：总纲 `plans/23`、股票 `plans/22`（GAP-S01/S02/S03/S04）、期货 `plans/21`、设计 `A.3-adaptive-weight-design.md`、`B.2-backtest-pipeline-design.md`。

---

## 1. 机构级目标架构（L3 + L4 参考数据流）

```
┌─────────────────────────── L3 组合构建层 ──────────────────────────────┐
│                                                                        │
│  elite 因子池                                                           │
│     │  ① 因子收益序列层 (FACTOR RETURNS)  ◄── 新增：每因子多空组合收益    │
│     ▼                                                                    │
│  [质量门槛] → [正交化/去冗余] → [因子收益矩阵 R: T×N]                     │
│                                   │                                     │
│            ┌──────────────────────┼──────────────────────┐              │
│            ▼                      ▼                      ▼              │
│   ② Alpha 预测 μ           ③ 风险模型 Σ            ④ 约束条件           │
│   (ElasticNet/Ridge/        (Ledoit-Wolf 收缩 +      (多头/行业中性/      │
│    ML 集成/IC 加权)           PCA 结构化 + 特异风险)    市值中性/换手/成本) │
│            │                      │                      │              │
│            └──────────┬───────────┴──────────┬───────────┘              │
│                       ▼                      ▼                          │
│   ⑤ 组合优化器  max μ'w − λw'Σw − cost(w)  →  权重 w                    │
│       (MVO / 风险平价 / Elastic Net，可切换)                             │
│                       │                                                  │
│                       ▼                                                  │
│   ⑥ 真实组合回测：w × R 逐期加权 → 实测组合夏普/相关性/换手/回撤          │
│                       │                                                  │
│                       ▼                                                  │
│   ⑦ Verifier（实测指标判定）+ 组合层 Walk-Forward + 随机化检验            │
│                       │                                                  │
│                       ▼                                                  │
│   ⑧ 归因：因子贡献/风格暴露/行业暴露 → 组合健康报告                       │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │ 组合权重 + 信号
                                ▼
┌─────────────────────────── L4 优化执行与反馈层 ─────────────────────────┐
│  ⑨ 资金分配(CapitalAllocator) → ⑩ 成本执行(cost_model 含冲击成本)       │
│      → ⑪ 信号产出(ScoredSignal → signal_bridge → FDT)                   │
│      → ⑫ 实盘反馈回流(成交/净值 → 实盘 IC 对比 → 衰减退役联动)           │
└──────────────────────────────────────────────────────────────────────────┘
```

**与现状的关键差异**：
| 环节 | 现状 | 目标 |
|:----|:-----|:-----|
| ① 因子收益序列 | 无（权重来自回归系数绝对值） | 每因子多空组合收益序列，L3 地基 |
| ③ 协方差 | 无（组合相关性为经验公式） | Ledoit-Wolf 收缩 + 正定性保证 |
| ⑤ 优化器 | 有实现但未接线（run() 未传 returns_matrix） | 接入 L3 主流程，三目标可切换 |
| ⑥ 真实回测 | 无（夏普/相关性为估算） | w×R 逐期加权实测指标 |
| ⑧ 归因 | 孤立模块（RiskAttributor 未接入） | L3 输出组合健康报告 |
| ⑫ 反馈 | 无回流（FDT 未回传） | LiveFeedbackRecord 契约 + 实盘 IC 对比 |

---

## 2. 缺陷清单（GAP-L3xx / GAP-L4xx，按优先级）

### 2.1 P0 — 阻塞性差距（直接影响组合信号真实性）

#### GAP-L301 因子收益序列与协方差估计缺失（P0）✅ 已处理（v2.61.0 A 阶段）

| 维度 | 内容 |
|---|---|
| **代码现状** | ① `synthesize_signals`（portfolio_loop.py L662-876）权重来源为 `_compute_elastic_net_weights`（L879-1035）的**截面回归系数绝对值归一化**，全程无"因子收益序列"概念；② `build_combo`（L1562-1682）中组合夏普 = Σ(w·Sharpe)×多样性折扣（L1629-1633）、最大相关性 = `(1-diversity)*0.35 + avg_sharpe*0.015` 经验公式（L1646-1650），无协方差估计；③ 全库 Grep 无因子多空收益序列构建模块 |
| **机构级标准** | 每因子构造横截面多空组合（top/bottom 分位或回归取残差）得到收益序列 r_f(t)；组合层建立因子收益矩阵 R∈R^{T×N} 与协方差 Σ（Ledoit-Wolf 收缩、正定性）；组合指标在 R 上实测 |
| **影响** | 组合夏普/相关性是"算出来的"而非"测出来的"→ Verifier 校验的是估算值；无 Σ 则无法做风险平价/最小方差/风险预算，机构化优化无从谈起（差距根因，对应总纲 GAP-I302 前置） |
| **实施步骤** | ① 新建 `fts/factor_engine/factor_returns.py`：`FactorReturnsBuilder`——对每因子按信号分位（如 10%/90%）或回归法构建多空收益序列，输出 `factor_returns: pd.DataFrame`（T×N）与覆盖日志；② `build_combo` 重构：当传入 `factor_returns` 时，组合收益 = w·R 逐期加权，实测组合夏普/相关性/回撤/换手替代估算公式；③ 新增 `RiskModelEstimator`（见 GAP-L302）输出收缩协方差 Σ；④ 保持无收益矩阵时的估算回退路径（兼容冷启动） |
| **测试方案** | 合成因子信号（已知相关性）→ 多空收益序列相关性 ≈ 已知值；组合夏普实测 vs 估算偏差边界断言；空数据/单因子降级；`pytest tests/factor_engine/test_factor_returns.py -v` |
| **落地** | ✅ `factor_returns.py`（`FactorReturnsBuilder` 横截面多空序列 + `align_to_factors`/`portfolio_returns` w·R）；`_compute_combo_metrics` 实测化 `metrics_source="measured"`（矩阵缺失/不可对齐回退估算）；optimizer/归因/走航三处 w×R 消费；`test_factor_returns.py` 全绿 |

#### GAP-L302 风险模型与协方差收缩估计缺失（P0）✅ 已处理（v2.61.0 A 阶段）

| 维度 | 内容 |
|---|---|
| **代码现状** | `PortfolioOptimizer`（portfolio_optimizer.py L84-125）接收 `cov` 参数，但 L3 主流程从不构造 cov；无 Ledoit-Wolf / 结构化收缩；奇异协方差仅加 εI jitter（L276）无系统性估计 |
| **机构级标准** | 因子协方差估计：Ledoit-Wolf 收缩（样本 Σ → 结构化目标 Σ_target，收缩强度由数据决定）+ 正定性保证 + 特异风险分离；机构以该 Σ 作为优化/风险预算唯一输入 |
| **影响** | 无 Σ 使 GAP-L301 的优化与风险平价无法落地；组合风险度量（VaR/波动率）缺失 |
| **实施步骤** | ① `RiskModelEstimator`：输入因子收益矩阵 R → 输出收缩 Σ（scipy/sklearn `ledoit_wolf` 或 numpy 自实现，异常时回退对角+εI）；② 输出 `risk_model` 报告（特征值/条件数/特异风险）；③ `synthesize_signals` 增加 `optimizer` 模式数据接线（见 GAP-L303） |
| **测试方案** | 收缩协方差正定性；高相关合成数据收缩后条件数改善；与样本协方差对比断言；`test_risk_model_estimator.py` |
| **落地** | ✅ `risk_model.py`（`RiskModelEstimator`：Ledoit-Wolf 收缩 + `_ensure_positive_definite` 正定性兜底 + 特征值/条件数/年化波动率报告）；`synthesize_signals` optimizer 分支 `RiskModelEstimator().estimate(rm).cov` 接线；Ledoit-Wolf 测试随 `test_portfolio_optimizer.py` |

#### GAP-L303 PortfolioOptimizer 未接入 L3 主流程（P0）✅ 已处理（v2.61.0 B 阶段）

| 维度 | 内容 |
|---|---|
| **代码现状** | `PortfolioLoop.run()`（portfolio_loop.py L2807-3206）Step 2 调用 `synthesize_signals(factors, self.synthesis_mode, elite_dir=...)`（L3028）**未传 returns_matrix**；`synthesize_signals` 的 `optimizer` 分支（L825-855）检测到 `returns_matrix is None` 即回退 sharpe_weight → **optimizer 模式实际从未生效**；`run()` 签名无 returns_matrix 参数 |
| **机构级标准** | 组合优化器作为一等模式可切换：MVO（协方差收缩）/ 风险平价 / Elastic Net，约束含多头、行业中性、换手上限；调用链完整 |
| **影响** | 已实现的机构化优化器是"死代码"；用户无法获得风险预算视角的权重 |
| **实施步骤** | ① `run()` 增加 `factor_returns` 参数（GAP-L301 因子收益矩阵），Step 2 透传；② `synthesize_signals` optimizer 分支列对齐 + `expected_returns`（Sharpe 代理）+ 约束配置透传 + Ledoit-Wolf 收缩协方差；③ `PortfolioLoop.__init__` 增加 `optimizer_mode`（risk_parity/mvo）/`optimizer_config`；④ CLI `--mode optimizer` + `--optimizer-mode` 生效；⑤ `build_combo` 透传 factor_returns 实测化联动 |
| **测试方案** | optimizer 模式端到端跑通（约束满足断言）；三模式权重对比；回退路径保持可用；`test_portfolio_loop.py`/`test_portfolio_optimizer.py` 全绿 |

#### GAP-L304 组合层无行业/市值中性化约束（P0）✅ 已处理（v2.61.0 B 阶段）

| 维度 | 内容 |
|---|---|
| **代码现状** | ① 股票评估侧已有 `_neutralize_signal_matrix`（evaluation_chain.py L670-750）但默认 None 跳过（GAP-S01 已登记）；② 组合优化器约束仅 max_weight/杠杆/换手/VaR（portfolio_optimizer.py L213-243），**无行业/市值中性约束**；③ `PortfolioOptimizer` 签名无行业暴露矩阵输入 |
| **机构级标准** | 组合优化显式约束行业/市值暴露在基准范围内（|行业偏离| ≤ tol），权重在"中性化后再优化"或"优化中加暴露惩罚"二选一；剥离风格赌注 |
| **影响** | 组合权重可能隐含行业/市值风格赌注，回测收益含风格贡献而非纯 alpha（与 GAP-S01 联动，组合层是 S01 的下游污染出口） |
| **实施步骤** | ① `OptimizerConfig` 增加 `neutralization`（industry/style）与 `exposure_tolerance`；② `PortfolioOptimizer.optimize` 增加 `exposure_matrix`/`target_exposure` 参数 + SLSQP 暴露约束（\|B'w − target\| ≤ tol）；③ `synthesize_signals`/`PortfolioLoop.run` 透传（optimizer_config 含 exposure_matrix）；④ numpy 降级路径记录不校验警告 |
| **测试方案** | 注入行业偏离合成数据 → 约束生效断言（\|B'w\| ≤ tol）；target_exposure 生效；维度不匹配 ValueError；未启用 neutralization 时忽略；`test_portfolio_optimizer.py` |

### 2.2 P1 — 重要差距（影响组合质量与可解释性）

#### GAP-L305 组合目标函数无成本/换手项（P1）✅ 已处理（v2.66.0）

> 承接总纲 GAP-I303（P2 登记）与 GAP-I501（P0 冲击成本）——本项为组合层执行细则。

| 维度 | 内容 |
|---|---|
| **代码现状** | `cost_model.py` 已有滑点/手续费/展期（**无冲击成本函数**，L27 `impact_bps_per_pct` 常量未建模成交量占比）；`synthesize_signals`/`build_combo` 无成本目标项；`_apply_sticky_constraints`（L1422-1461）仅为权重变动钳制，非成本惩罚 |
| **机构级标准** | 优化目标含 `−cost(w)`（佣金+滑点+冲击成本，冲击成本按成交量占比平方根/分段线性建模）；换手惩罚 λ·Σ\|w−w_prev\| 入目标；大权重信号自动降权 |
| **影响** | 高换手/高集中组合成本被忽视，回测收益虚高（违反"回测与实盘强对齐"红线） |
| **实施步骤** | ① `cost_model.py` 增加冲击成本函数 `impact_cost(volume_pct)`（输入持仓占日均成交额比例 → bp，square-root 模型，参数走配置）；② `PortfolioOptimizer` 目标函数增加换手惩罚与成本项（复用 cost_model）；③ `build_combo` 输出 net 指标（扣除成本后的组合夏普/收益）；④ 容量约束：单因子持仓市值 ≤ 品种日均成交额 × 系数（超限截断并记录，衔接总纲 GAP-I501） |
| **测试方案** | 冲击成本单调性测试；换手惩罚生效断言；net vs gross 夏普对比报告；容量超限截断单测 |
| **落地** | ✅ `cost_model.py` 平方根冲击成本函数（按持仓占日均成交额比例）+ Optimizer 换手惩罚/成本项入目标；`build_combo` 输出 net 指标（扣除成本后夏普）；容量约束（持仓市值 ≤ 成交额 × capacity_cap_ratio，超限截断记录，衔接 GAP-I501）；net vs gross 对比报告随 L3 输出 |

#### GAP-L306 组合层 Walk-Forward / 样本外验证缺失（P1）✅ 已处理（v2.66.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `walk_forward.py` 已实现因子级多窗口验证（`WalkForwardOptimizer`），但 **L3 组合层无 walk-forward**：权重一次性在全样本上求，无"权重在前段确定、后段实测"的滚动验证；`_validate_oos_extrapolation`（L2198-2324）是因子级衰减检测，非组合级 |
| **机构级标准** | 组合层滚动验证：每窗口仅用前段数据求权重 → 后段实测组合表现 → 跨窗口一致性（IC 稳定性/夏普波动）；参数冻结纪律 |
| **影响** | 组合权重可能对单段历史过拟合，无组合级 OOS 证据 |
| **实施步骤** | ① 新增 `fts/factor_engine/portfolio_walk_forward.py`：`PortfolioWalkForward`——滚动窗口（train 求权重 → test 实测），输出各窗口组合夏普/IC/相关性；② 报告一致性得分（跨窗口夏普波动 < 阈值）；③ 接入 L3 报告输出（`reports/{date}/portfolio_wf_*.md`） |
| **测试方案** | 合成权重漂移数据 → 一致性得分正确捕获；窗口边界参数化测试；`test_portfolio_walk_forward.py` |
| **落地** | ✅ `portfolio_walk_forward.py`（`PortfolioWalkForward` 滚动窗口：train 求权重 → test 实测，输出各窗口组合夏普/IC + 跨窗口一致性得分）；接入 L3 Step 7.7 报告输出 `reports/{date}/portfolio_wf_*.md` |

#### GAP-L307 归因体系未接入 L3（P1）✅ 已处理（v2.66.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `risk_attributor.py` 已实现因子贡献度/暴露/VaR/ES，但为孤立模块（Grep 无 L3/报告调用）；无风格/行业归因分解；`REGIME_STYLE_MULTIPLIERS`（portfolio_loop.py L411-464）为硬编码查表 |
| **机构级标准** | 组合收益归因：因子贡献 / 风格暴露（Barra 风格）/ 行业暴露三维分解；风险预算（各因子风险贡献占比）；支撑"赚的什么钱"可解释性 |
| **实施步骤** | ① `PortfolioLoop` Step 7.5 接入 `RiskAttributor`：输入组合收益 + 因子收益矩阵 R → 输出归因报告（因子贡献/暴露/VaR/ES）；② 归因报告入 `reports/{date}/portfolio_attribution_*.md`；③ 后续（衔接 GAP-S02 Barra 体系）扩展风格/行业归因维度 |
| **测试方案** | 已知权重合成组合 → 归因贡献度与理论一致（误差 < 1e-6）；VaR/ES 数值断言；`test_risk_attributor.py` |
| **落地** | ✅ `RiskAttributor` 接入 L3 Step 7.6：`portfolio_returns`（w×R）+ `factor_returns` + 权重 → 权重方差分解，输出因子贡献/暴露报告 `reports/{date}/portfolio_attribution_*.md`；贡献度误差 < 1e-6 断言全绿 |

#### GAP-L401 L4 表达式组合算子层薄弱（P1）✅ 已处理（v2.66.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `expr_dsl/registry.py` L4 仅 15 个基础算子（add/sub/mul/div/neg/abs/sign/sqrt/log/exp/min/max/clip/pow，L125-141），无双序列/跨标的/横截面/条件算子；`operator_evolution.py` L150-153 因"规避布尔条件复杂度"主动排除双序列算子 → 演化搜索空间受限 |
| **机构级标准** | 组合算子分层完整：双序列（corr/regression_residual/quantile_bucket）、横截面（cross-section rank/demean/zscore）、条件（if_else/switch）、跨标的（spread/ratio）；单一事实源（GAP-S10 合并后） |
| **影响** | 因子表达式复合能力弱，演化产出多样性不足；横截面/跨标的 alpha 无法表达 |
| **实施步骤** | ① L4 新增双序列算子：`corr(win)`、`regression_residual`、`quantile_bucket`、`cross_section_rank`、`cross_section_demean`、`if_else`（条件保护：NaN 安全）；② 每个算子配套经济语义 + 参数边界 + 单元测试 + 沙箱编译验证；③ `operator_evolution.py` 放开双序列约束（在条件复杂度护栏内）；④ 注册表并入单一事实源（GAP-S10 落地后） |
| **测试方案** | 新算子边界测试（空数据/全 NaN/零除）；沙箱编译通过；GP/算子演化回归（用新算子组合的表达式可正常评估）；`test_registry.py` |
| **落地** | ✅ `registry.py` 新增 `regression_residual`/`quantile_bucket`/`cross_section_demean`/`if_else`/`corr`/`cross_section_rank` 6 算子（NaN 安全 + 经济语义 + 参数边界 + PIT lookback，对齐验收标准 ≥6 算子；corr 双序列滚动相关 / cross_section_rank 截面 0-1 排名）；`operator_evolution.py` 放开双序列约束（条件复杂度护栏内）；沙箱编译 + 边界测试全绿 |

### 2.3 P2 — 一般差距（增强项）
#### GAP-L308 Regime 权重数据化（替代硬编码查表）（P2）✅ 已处理（v2.68.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `REGIME_FAMILY_MULTIPLIERS`/`REGIME_STYLE_MULTIPLIERS`（portfolio_loop.py L363-464）为人工硬编码倍率；`regime_adaptive_weight_adjustment`（L499-615）按倍率表调整，无数据支撑、无置信度 |
| **机构级标准** | 各制度下因子家族有效性由数据估计（样本内滚动回归/状态条件 IC），权重倍率带置信区间；制度切换平滑（RegimeSmoother 已有，衔接 A.3） |
| **实施步骤** | ① 新增条件因子有效性估计：按 regime 分桶统计各家族历史 IC 均值/胜率 → 生成数据驱动倍率表（替代/校准硬编码表）；② 倍率表落盘 `_data/l3_regime_multipliers.yaml`（易变配置进 `docs/harness/_data/` 原则）；③ 输出硬编码 vs 数据驱动对比报告 |
| **测试方案** | 数据驱动倍率与合成 regime 标签一致性；配置缺失回退硬编码表；`test_regime_adaptive_weight.py` |
| **落地** | ✅ `regime_multipliers.py`（RegimeMultiplierEstimator，钳制+最小样本回退+对比报告）；倍率表落盘 `docs/harness/_data/l3_regime_multipliers.yaml`；`load_data_driven_multipliers` 优先接线（缺失回退硬编码）；修复 family_global 跨 regime 桶覆盖 bug；14 测试用例 |

#### GAP-L402 L4 实盘反馈闭环（P2）✅ 已处理（v2.66.0）

> 承接总纲 GAP-I401（P0 登记）——本项为 L4 层执行细则，依赖下游 FDT 配合。

| 维度 | 内容 |
|---|---|
| **代码现状** | `signal_contract.py` ScoredSignal + `bridge/signal_bridge.py` 输出信号给 FDT；`feedback_loop.py` 有 4 张反馈表 + CLI，但 **Grep 无实盘成交/净值数据导入通道**（FDT 未回传） |
| **机构级标准** | 实盘表现闭环：成交/净值回流 → 实盘 IC vs 回测 IC 对比 → 衰减修正 → 自动退役/重校准（联动 GAP-I305） |
| **实施步骤** | ① 定义 `LiveFeedbackRecord` 契约（factor_id/信号日/信号值/持仓收益/换手/滑点）；② `feedback_loop.py` 增加导入 CLI + DuckDB 表 + 实盘 IC 计算；③ 实盘 IC vs 回测 IC 对比报告接入 L3 Step 1.5（OOS 外推验证数据源扩展）；④ 衰减退役逻辑（GAP-I305）接入实盘反馈 |
| **测试方案** | 反馈记录契约校验；实盘 IC 对比报告；导入异常降级；`test_feedback_loop.py` |
| **落地** | ✅ `LiveFeedbackRecord` 契约 + `validate_live_feedback_record` + `LiveFeedbackImporter`（DuckDB 优先/JSONL 回退）+ `LiveVsBacktestICReport` 衰减判定；CLI `fts feedback import/live-ic`；5 测试用例 |

#### GAP-L309 组合层数据规模扩展（P2）✅ 已处理（v2.68.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | `_compute_elastic_net_weights`（L879-1035）硬编码 `days=120, max_stocks=50`（L882-883），CSI300 子集 50 只 × 120 天 → 截面回归统计功效有限；`MIN_EVAL_DAYS=500`（L1476）与 120 天回溯不一致 |
| **机构级标准** | 全市场（3000+ 只）× 数年 Point-in-time 数据（去幸存者偏差）；分钟级因子在分钟数据上验证 |
| **实施步骤** | ① 面板加载参数配置化（`max_stocks`/`days` 进配置，默认提升至全 CSI300 + 500 天）；② 数据缺失时按流动性分层抽样（替代随机 50 只）；③ 记录数据覆盖与幸存者偏差提示 |
| **测试方案** | 参数化面板加载单测；抽样逻辑稳定性；`test_data_provider_panel.py` |
| **落地** | ✅ `PanelLoadingConfig`（默认全 CSI300×500 天）；`_liquidity_stratified_sample` 桶间轮询分层抽样；`_load_panel_with_liquidity_sampling` 覆盖/幸存者偏差日志；两个权重函数默认 days 120→500、max_stocks 50→0；12 测试用例 |

#### GAP-L310 种子加载链缺陷修复（v2.68.0 全量回归暴露）✅ 已处理（v2.68.0）

| 维度 | 内容 |
|---|---|
| **代码现状** | 全量回归 21 例失败：① `seed_loader.py` L23 未导入 `FactorKind` 但 YAML 因子 `kind=FactorKind.*` 引用（NameError → 期货种子 81/184 加载失败）；② 多行 `field_defs` 拼接进函数体后续行无/残留缩进 → unexpected indent（analyst_revision/fundamental 等 38 处编译失败）；③ 测试断言引用已迁移函数 `_estimate_lookback` |
| **落地** | ✅ L23 补 `FactorKind` 导入；`_fundamental_factor_from_yaml` 多行 field_defs strip+统一 4 空格缩进；test_seed_loader 改引 `seed_analyzer.estimate_lookback_static`；test_seed_pool/test_seed_loader 种子计数断言同步 714/898/30 |

---

## 3. 分阶段落地路线图（与总纲 Stage 1 对齐，v2.65.0~v2.72.0）

> 版本衔接：当前 v2.60.0。plans/22（v2.60.0~v2.64.0）完成股票中性化/风格基础（GAP-S01/S02/S04 为本计划前置）；本计划从 v2.65.0 起与总纲 Stage 1 并行推进（总纲 Stage 1 排期 GAP-I301/I302/I303/I401/I501 于 v2.65.0~v2.72.0，本计划为其 L3/L4 执行细则）。

| 版本 | 阶段 | 缺陷项 | 核心交付 |
|:-----|:-----|:-------|:---------|
| v2.65.0 | A | GAP-L301 + L302 | 因子收益序列层（`factor_returns.py`）+ 风险模型估计器（收缩 Σ）→ **组合夏普/相关性实测化**，Verifier 校验实测指标 ✅ **提前至 v2.61.0 完成** |
| v2.66.0 | A | GAP-L305 | 冲击成本函数 + 换手惩罚入优化目标 + net 指标输出（衔接 GAP-I501/I303）✅ **提前完成** |
| v2.67.0 | B | GAP-L303 | `PortfolioOptimizer` 接入 L3 主流程（returns_matrix 接线 + optimizer_mode/optimizer_config + CLI）✅ **提前至 v2.61.0 完成** |
| v2.68.0 | B | GAP-L304 | 组合层行业/市值中性化约束（期货板块映射 + 股票行业映射，联动 GAP-S01/S02）✅ **提前至 v2.61.0 完成** |
| v2.69.0 | C | GAP-L307 | 归因体系接入 L3（RiskAttributor → 组合健康报告）✅ **提前至 v2.66.0 完成** |
| v2.70.0 | C | GAP-L306 | 组合层 Walk-Forward（滚动权重验证 + 一致性得分）✅ **提前至 v2.66.0 完成** |
| v2.71.0 | D | GAP-L402 | L4 实盘反馈契约 + 回流通道 + 实盘 IC 对比（衔接总纲 GAP-I401）✅ **提前至 v2.66.0 完成** |
| v2.72.0 | D | GAP-L401 + L308 | L4 表达式组合算子扩充 + Regime 权重数据化 + 全量回归 ✅ **L401 提前至 v2.66.0 / L308 完成于 v2.68.0** |

**阶段退出标准**：
- **A 阶段（v2.66.0 后）**：`build_combo` 支持实测模式；收缩 Σ 正定性；net/gross 指标同窗输出；全部 L3 单测 + 回归全绿。✅ 已达成
- **B 阶段（v2.68.0 后）**：optimizer 三模式端到端跑通；行业/市值约束生效；股票/期货 L3 统一框架。✅ 已达成（v2.61.0）
- **C 阶段（v2.70.0 后）**：L3 每次运行输出归因 + Walk-Forward 报告；一致性得分纳入 Verifier 参考。✅ 已达成（v2.66.0）
- **D 阶段（v2.72.0 后）**：实盘反馈通道可用；组合算子库扩充完成；数据驱动倍率表落地；全量回归 + 一致性 13/13。✅ 已达成（v2.68.0 收尾，L308/L309 落地）

---

## 4. 股票 / 期货并行差异

| 维度 | 期货（成熟度高） | 股票（成熟度低，需补） |
|:-----|:----------------|:---------------------|
| L3 组合层现状 | 完整（Elastic Net + Regime + 正交化 + 信号管道） | 缺（仅等权信号管道，GAP-I301） |
| 中性化 | 板块映射 `FUTURES_SECTOR_MAP` 已有 | 行业/市值映射未接（GAP-S01 前置） |
| 面板数据 | `get_futures_panel`（多品种） | CSI300 子集 50 只（GAP-L309） |
| 信号方向 | 多空双向（已有方向校正 + Ridge，futures_signal_pipeline.py） | 仅多头（需复用方向校正，GAP-S04） |
| 落地顺序 | 先做 A/B 阶段（GAP-L301~L305 期货先行验证） | 依赖 GAP-S01/S02 完成后接入统一 L3 框架（B 阶段） |

> 原则：**统一框架、两市场复用**——L3 组合层组件（因子收益序列/风险模型/优化器/归因/走航）与市场无关，先期货验证、后股票复用；市场差异收敛为数据映射（板块/行业）与信号方向（多空/多头）两个配置维度。

---

## 5. 验收标准（量化指标）

| 维度 | 验收指标 |
|:-----|:---------|
| 组合指标 | 组合夏普/相关性由实测（w×R）产出，估算公式仅作冷启动回退；Verifier 校验实测值 |
| 风险模型 | 收缩 Σ 正定性 100%；条件数较样本协方差改善（合成高相关数据） |
| 优化器 | mvo/risk_parity/elastic_net 三模式端到端可用；约束（杠杆/集中度/换手/中性化）全部生效 |
| 成本 | 冲击成本函数单调；net 夏普 vs gross 差异报告随每次 L3 输出 |
| 归因 | 因子贡献度误差 < 1e-6（已知权重合成数据） |
| 走航 | 组合层跨窗口一致性得分输出，纳入报告 |
| L4 反馈 | 实盘反馈导入契约校验通过；实盘 vs 回测 IC 对比报告生成 |
| 算子库 | L4 新增 ≥ 6 个双序列/横截面/条件算子，全部过沙箱编译与单测 |
| 测试 | 新增/更新测试用例全绿；全量回归通过；一致性 13/13 |

---

## 6. 风险与依赖

| 项 | 说明 | 缓解 |
|:---|:-----|:-----|
| 依赖 GAP-S01/S02（股票中性化/风格） | L304 股票侧依赖其行业/市值映射与 Barra 基础 | A/B 阶段期货先行，股票侧映射以 GAP-S01/S02 交付为前置 |
| 依赖下游 FDT 回传数据 | L402 实盘反馈依赖 FDT 配合（角色边界：FTS 只产信号） | 契约先行（`LiveFeedbackRecord`），无实盘数据时保持回测基线并降级提示 |
| 实测化改动风险 | `build_combo` 指标口径变化可能改变 Verifier 判定结果 | 保留估算回退 + 前后对比报告（实测 vs 估算）双输出一个版本窗口 |
| 数据规模扩展成本 | GAP-L309 全 CSI300 × 500 天增加计算量 | 抽样策略配置化 + 向量化实现 + 性能基准测试 |
| 版本排期冲突 | 与 plans/22（v2.60.0~v2.64.0）、总纲 Stage 1（v2.65.0+）并行 | 本计划 A 阶段依赖前置项最小化，逐版本核对 09-advancement-plan.md |

---

## 7. 不在范围

- 真实券商/交易所网关与订单执行（下游 FDT 角色边界）
- 全市场股票覆盖（全 CSI300 提升属 GAP-L309 渐进，不做全市场）
- Level2 订单簿 / 另类数据因子（总纲 GAP-I503，远期）
- 分布式优化求解（总纲 GAP-I502 预留）
- 各层已登记缺陷的重复展开（总纲 GAP-I 系列只引用，细则在本计划或对应子计划）

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/factor_engine/portfolio_loop.py`（GAP-L301/L303/L304/L305/L307/L308/L309：synthesize_signals L662-876、build_combo L1562-1682、run L2807-3206、regime 表 L363-464、PanelLoadingConfig/抽样 L1030+）；`fts/factor_engine/portfolio_optimizer.py`（GAP-L302/L303/L304/L305：L84-125、L193-264）；`fts/factor_engine/portfolio_constructor.py`（L3 复用基线）；`fts/factor_engine/capital_allocator.py` + `cost_model.py` + `risk_attributor.py`（GAP-L305/L307/L402 孤立模块接线）；`fts/factor_engine/walk_forward.py`（GAP-L306 组合层扩展）+ `portfolio_walk_forward.py`（新增）；`fts/factor_engine/expr_dsl/registry.py` + `operator_evolution.py`（GAP-L401）；`fts/factor_engine/signal_contract.py` + `bridge/signal_bridge.py` + `feedback_loop.py`（GAP-L402）；`fts/factor_engine/regime_multipliers.py` + `docs/harness/_data/l3_regime_multipliers.yaml`（GAP-L308，新增）；`fts/factor_engine/seed_loader.py`（GAP-L310：L23 `FactorKind` 导入 + `_fundamental_factor_from_yaml` 多行 field_defs 缩进修复）；`fts/factor_engine/evaluation_chain.py`（L670-750 `_neutralize_signal_matrix`，GAP-L304 前置） |
| 可验证断言 | 12 项执行级缺陷全部登记且 **12/12 已关闭**（P0×4：L301~L304 / P1×4：L305~L307+L401 / P2×4：L308+L309+L310+L402）；承接总纲 GAP-I301/I302/I303/I305/I401/I501 并引用不重复；阶段 A~D 退出标准全部达成（实测指标/收缩正定性/三模式/净成本/归因误差/走航一致性/反馈契约/算子沙箱）；版本衔接 v2.65.0~v2.72.0 与总纲 Stage 1 对齐 |
| 检验方式 | `python scripts/verify_doc_consistency.py`；每缺陷项落地配套 `pytest tests/... -v`（L308: test_regime_multipliers.py 14 用例、L309: test_data_provider_panel.py 12 用例、L310: test_seed_pool+test_seed_loader 83 用例）；每阶段退出运行全量回归 + 一致性 13/13；同步登记 `08-gap-analysis.md` 与 `09-advancement-plan.md` |
