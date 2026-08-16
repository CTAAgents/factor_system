# 28 — Regime 机构级优化 — 最终实施报告

> 版本: v2.104.0+84
>
> 计划文档：`docs/harness/plans/28-regime-institutional-optimization-plan.md`（版本 v2.103.0，日常开发追加，不 bump）
> 实施日期：2026-08-11 ｜ 执行方式：子代理逐任务 + 主会话审查
> 关联：GAP-092 ~ GAP-095 登记（P2 远期）｜ 状态：✅ 10/10 任务完成，端到端抽查与多制度复核通过

---

## 1. 背景与目标

**原始需求**（用户确认）：把 FTS 的 Regime 置信度接入仓位缩放，并改为制度概率混合权重，对齐头部机构（Man AHL / Two Sigma / AQR）与学术界（HMM 平滑概率 / BIC 状态选择 / 后验熵标定）。

**改造前差距**（已核实代码）：

| 维度 | 改造前 | 目标 |
|:-----|:-------|:-----|
| 制度输出 | 单点 `regime + confidence` | ✅ 全制度概率分布 `regime_probs`（和为 1） |
| 权重调整 | 硬查当前制度倍率表 | ✅ 概率混合 `mult = Σ p_i × table_i`（regime blend） |
| 仓位 | 归一化后不缩放（置信度无影响） | ✅ 熵标定 `exposure_scale` 缩放总暴露并落盘 |
| 切换 | 对称指数平滑 | ✅ de-risk 快 / re-risk 慢不对称切换 |
| HMM | 状态数固定 4、特征未标准化 | ✅ BIC 状态选择 + 训练段标准化 |
| 置信度 | 后验概率/启发式直接使用 | ✅ 归一化熵标定 |
| 验证 | 无 | ✅ 制度有效性样本外验证模块 |

---

## 2. 实施总览（10 任务 → 交付物）

| 任务 | 内容 | 交付物 |
|:-----|:-----|:-------|
| T1 | `MarketRegime` 契约 + 规则/单周期HMM/兜底输出 `regime_probs` | [regime.py](file:///d:/Programs/factor_system/fts/factor_engine/regime.py) / [regime_calibration.py](file:///d:/Programs/factor_system/fts/factor_engine/regime_calibration.py) |
| T2 | 多周期 HMM 置信度公式修正 + `regime_probs` 输出 | [regime_hmm.py](file:///d:/Programs/factor_system/fts/factor_engine/regime_hmm.py) |
| T3 | regime blend 制度概率混合权重 | [portfolio_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py) |
| T4 | `AdaptiveWeightConfig` 配置扩展 + Step 2.5 接线 | [contracts.py](file:///d:/Programs/factor_system/fts/factor_engine/contracts.py) |
| T5 | `RegimeConfidenceCalibrator` 熵标定 | [regime_calibration.py](file:///d:/Programs/factor_system/fts/factor_engine/regime_calibration.py) |
| T6 | 置信度仓位缩放 `exposure_scale` 接入组合构建 | [portfolio_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py) / `PortfolioCombo` 新字段 |
| T7 | `RegimeSmoother` 不对称 de-risk/re-risk 切换 | [adaptive_weight.py](file:///d:/Programs/factor_system/fts/factor_engine/adaptive_weight.py) |
| T8 | HMM 状态数 BIC 选择 + 特征标准化 | [regime_model_selection.py](file:///d:/Programs/factor_system/fts/factor_engine/regime_model_selection.py) |
| T9 | 制度有效性样本外验证 | [regime_validation.py](file:///d:/Programs/factor_system/fts/factor_engine/regime_validation.py) / [validate_regime.py](file:///d:/Programs/factor_system/scripts/validate_regime.py) |
| T10 | 观测指标 + HARNESS 文档同步 + 定向回归 | [prometheus_metrics.py](file:///d:/Programs/factor_system/fts/monitor/prometheus_metrics.py) / docs/harness/* |

---

## 3. 核心机制落地（最终实现）

### 3.1 制度概率契约与输出（T1/T2）

- `MarketRegime` 新增 `regime_probs: dict[str, float]`（全 5 制度，和为 1）。
- **多周期 HMM 置信度公式修正**：`clip(vote_share × avg_conf × 2.0)`（启发式）→ **主制度后验概率的加权平均**（`clip(Σ w×p_best / Σw, 0, 0.99)`），有概率语义且具区分度；部分周期被 gate 时总概率 < total_weight，置信度自然折扣。
- 单周期 HMM 状态概率 → 制度聚合（同制度多状态求和）；MSM 平滑概率映射；规则方法由软投票得分构造伪概率；兜底 `{oscillate: 1.0}`。

### 3.2 regime blend 概率混合（T3/T4）

- `regime_adaptive_weight_adjustment` 新增 `probability_mix`（默认 True）：启用且 `regime_probs` 存在时，倍率 = `Σ p_i × table_i`（**跨制度查全表**，数据驱动表 GAP-L308 优先）；否则回退硬查表。
- `AdaptiveWeightConfig` 新增 `probability_mix / confidence_scale / confidence_scale_min / confidence_entropy_penalty`；`DEFAULT_ADAPTIVE_CONFIG` 默认全开。

### 3.3 置信度熵标定仓位缩放（T5/T6）

- `RegimeConfidenceCalibrator`：`scale = clip(conf × (1 − 0.5 × H_norm), 0.3, 1.0)`，H_norm 为 5 制度分布的归一化熵。
- `build_combo` 归一化后应用 `exposure_scale`，随 `PortfolioCombo.exposure_scale / regime_meta` 落盘；`_compute_exposure_scale` 未启用/异常时回退 1.0。

### 3.4 不对称切换平滑（T7）

- `RegimeSmoother`：进入风险制度（bear/high_vol）用 `de_risk_alpha=0.8` 快速降，离开风险制度用 `re_risk_alpha=0.1` 缓慢加，其余默认 α。

### 3.5 BIC 状态选择 + 特征标准化（T8）

- `select_n_states`：候选 (2,3,4) 拟合 GaussianHMM，`BIC = −2·loglik + n_params·ln(N)` 取最小；hmmlearn 不可用或样本 <60 回退 4。
- `fit_standardizer`：只对训练段 fit mean/std，predict 用同一参数 transform（防数据窥探）。

### 3.6 制度有效性验证（T9）

- `validate_regime_predictive_power`：Kruskal-Wallis + 各制度条件前向收益/波动统计；CLI `validate_regime.py --data --json`。

### 3.7 观测指标（T10）

- `MetricsRegistry.record_regime_metrics`：`fts_regime_confidence / entropy_norm / exposure_scale / blend_hhi / name` 指标，PortfolioLoop Step 2.5 上报。

---

## 4. 实施中发现并修复的问题（4 处，均已回归锁定）

| # | 发现环节 | 问题 | 修复 |
|:-:|:--------|:-----|:-----|
| 1 | T2 审查 | 新置信度公式 `sum(p)/total_weight` 在全部周期通过 gate 时恒为 1.0（退化为"有效参与占比"，无区分度） | 改为**主制度概率的加权平均** |
| 2 | T3 审查 | 计划代码用单制度表 `family_multipliers.get(r, …)` 按制度名 r 取键，恒返回 1.0（blend 失效） | **跨制度查全表** `family_tables.get(r, {}).get(family, 1.0)` |
| 3 | T7 审查 | `elif self._current_regime in risk_regimes` 为死代码（`_current_regime` 已被覆盖为 detected_regime），re-risk 慢加实际未实现 | 先保存 `prev_regime` 再更新状态 |
| 4 | 端到端抽查 | HMM/MSM 路径 `regime_probs` 只写 features，未提升到 `MarketRegime` 顶层 → 真实管线中 blend 与熵标定实际走回退路径 | `detect` 末尾统一从 `features["regime_probs"]` 提升顶层；补测试 `test_detect_promotes_hmm_regime_probs_to_top_level` |

---

## 5. 端到端抽查与多制度复核结果

### 5.1 真实管线（`fts portfolio run --universe futures --force-recompute`）

- 前置阻塞：duckdb `_duckdb` C 扩展损坏 → 清华源 `--force-reinstall duckdb==1.5.5` 修复。
- 运行结果：`status=verifier_warning factors=4 sharpe=1.4000`（退出码 0）。

| 检查项 | 日志/落盘证据 |
|:-------|:-------------|
| 市场 OHLCV → Regime 启用 | `[L3] Step 0.5b: 市场合成 OHLCV 构建完成 (666 交易日)` |
| regime blend 生效 | `[L3-Regime] 自适应权重调整完成 [regime=oscillate, dim=both, adjusted=2/5]`；`fut_basis_momentum_g38: 0.5900 → 0.4720 (×0.80)` |
| exposure_scale 日志 | `[L3] Step 2.5: Regime=oscillate (confidence=0.70) ... exposure_scale=0.70`；`[L3-WEIGHT] 置信度仓位缩放: exposure_scale=0.7000` |
| 仓位缩放生效 | 最终权重和 = 0.3812+0.1879+0.0815+0.0494 = **0.70** |
| 落盘可追溯 | `memory/portfolio/futures/current_combo.json`: `"exposure_scale": 0.7` + `"regime_meta": {regime: oscillate, confidence: 0.7, exposure_scale: 0.7}` |

### 5.2 多制度分布熵标定（输入 confidence=0.9）

| 制度分布 | exposure_scale | 行为 |
|:---------|:---------------|:-----|
| 单点（bull=1.0） | 0.9000 | 不折扣 ✓ |
| 准单点（bull=0.92） | 0.7925 | 轻微折扣 |
| 双制度（0.6/0.1/0.25/…） | 0.6017 | 显著折扣 |
| 三制度（0.5/0.3/0.2） | 0.6121 | 显著折扣 |
| 均匀（无信息） | 0.4500 | 最大折扣（与 T5 单测一致） |

折扣随归一化熵**单调递减**，符合"低确定性降暴露"。

### 5.3 regime blend 跨制度加权（分散分布 `{bull:0.6, bear:0.2, oscillate:0.1, high_vol:0.05, low_vol:0.05}`）

- 实际 weight = **0.1310** = 手算 `0.10 × 1.175(family) × 1.115(style)`，精确命中 `Σ p_i × table_i`；
- 对照硬查表（oscillate）weight = **0.0640**，两者差 2 倍——概率混合显著改变权重，制度误判只按概率摊薄。

### 5.4 规则方法真实检测（非单点分布）

| 行情 | confidence | regime_probs | exposure_scale |
|:-----|:-----------|:-------------|:---------------|
| 震荡 | 0.90 | {bear:0.77, high_vol:0.23} | 0.748（-17%） |
| 缓跌 | 0.53 | {bear:0.51, high_vol:0.23, low_vol:0.26} | 0.360（-32%） |
| 强跌 | 0.99 | {bear:0.79, high_vol:0.21} | 0.832（-16%） |

折扣幅度与手算熵一致（如震荡行 `0.8995×(1−0.5×0.336)=0.748`），**非单点分布下熵折扣真实生效**。

---

## 6. 验证与验收

| 项 | 结果 |
|:---|:-----|
| 定向回归（11 个相关模块） | **511 passed**（实施后）→ 修复后再跑 **230 passed**（regime/portfolio/stock/sector/monitor 定向集），无回归 |
| 文档一致性 | `verify_doc_consistency.py` **13/13 通过** |
| 静态检查 | ruff 全通过 |
| 文档 | 01-architecture / 03-configuration / 05-observability / 06-testing / 07-operations / 08-gap-analysis / 09-advancement-plan / README 已同步 |
| 行为开关验证 | `probability_mix=False` 回退硬查表；`confidence_scale=False` → `exposure_scale=1.0`；旧格式 regime 无 probs 自动回退 |

---

## 7. 遗留差距与后续建议

### P2 远期（已登记 08-gap-analysis GAP-092~095）
1. **宏观制度四象限**（Bridgewater 增长×通胀）——需宏观数据面板（复用 18-macro-field-enhancement）；
2. **RL 制度条件决策层**（SSRN 5785443）——需实盘反馈闭环（simulated_portfolio 已铺垫）；
3. **置信度 isotonic/Platt 校准**——需足够历史 regime 标签（T9 验证模块提供基础）；
4. **regime blend 幂次调节**（`blend_power`）——若实测倍率被拉平则启用。

### 观察项（非 bug）
1. **规则伪概率结构**：输出总集中在趋势侧（bull/bear）+ 波动侧（high_vol），`oscillate` 因余量公式基本为 0——语义合理但规则路径熵折扣幅度有限（16%~32%）；
2. **规则方法对随机游走表观趋势敏感**（均值 0 行情被判 bear/0.90 置信）——既有行为，可作规则阈值调优观察项；
3. **多周期 HMM 置信度上限**：所有周期通过 gate 时 `regime_probs` 趋近单点 → 熵标定不折扣（合理）；建议后续观察真实多周期输出是否长期偏单点。

---

## 8. 文件改动清单（本次 28 计划）

**新建**：`fts/factor_engine/regime_calibration.py`、`regime_model_selection.py`、`regime_validation.py`、`scripts/validate_regime.py`、`tests/factor_engine/test_regime_calibration.py`、`test_regime_model_selection.py`、`test_regime_validation.py`、`docs/harness/plans/28-*.md`

**修改**：`fts/factor_engine/regime.py`、`regime_hmm.py`、`portfolio_loop.py`、`adaptive_weight.py`、`contracts.py`、`fts/monitor/prometheus_metrics.py`、`tests/factor_engine/test_regime.py`、`test_regime_hmm.py`、`test_portfolio_loop.py`、`test_portfolio_loop_adaptive.py`、`tests/monitor/test_prometheus_metrics.py`、`docs/harness/01-architecture.md`、`03-configuration.md`、`05-observability.md`、`06-testing.md`、`07-operations.md`、`08-gap-analysis.md`、`09-advancement-plan.md`、`README.md`

> 注：工作区存在大量与 28 计划无关的既有未提交改动（历史任务遗留）；提交时建议仅 stage 上述清单，分批提交避免混入无关变更。
