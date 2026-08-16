# 28 — Regime 机构级优化实施计划（置信度仓位缩放 + 制度概率混合权重）


> 版本: v2.104.0+105

> 状态: ✅ 已实施（T1~T10 全部完成，2026-08-11；实施记录见 [07-operations.md](../07-operations.md) v2.101.0「Regime 机构级优化计划」条目，远期差距登记 GAP-092~095）
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐步实现本计划。步骤使用复选框（`- [ ]`）语法跟踪。

**Goal:** 将 FTS 的 Market Regime 从"硬切换制度查表 + 单点置信度"升级为"制度概率分布 + 概率混合权重 + 置信度驱动的仓位缩放"，对齐头部量化机构（Man AHL / Two Sigma / AQR）与学术界（HMM 平滑概率 / BIC 状态选择 / 后验熵标定）的成熟实践。

**Architecture:** 四层改造，保持现有 5 层降级链骨架不变：
1. **契约层** — `MarketRegime` 增加 `regime_probs`（全制度概率分布，和为 1），各检测路径（multi_hmm / msm / hmm / rule / fallback）统一输出；
2. **权重层** — `regime_adaptive_weight_adjustment` 由"查当前制度倍率表"改为"按 `regime_probs` 对全部制度倍率表加权混合"（regime blend），无概率时回退原逻辑；
3. **暴露层** — 组合归一化后应用 `exposure_scale`（置信度经熵标定后的仓位缩放因子），写入 `PortfolioCombo` 新字段；
4. **模型质量层** — 多周期 HMM 置信度公式去启发式、HMM 特征标准化、BIC 状态数选择、制度有效性样本外验证。

**Tech Stack:** Python 3.11+ / numpy / pandas / hmmlearn（可选）/ statsmodels（可选）/ Pydantic V2 TypedDict 契约 / pytest（TDD）/ ruff。

---

## 0. 现状基线（改造前已核实）

| 模块 | 现状 | 差距（对标） |
|---|---|---|
| `MarketRegime`（[regime.py](file:///d:/Programs/factor_system/fts/factor_engine/regime.py#L66-L74)） | 仅 `regime + confidence` 单点 | 无全制度概率分布 → 无法概率混合 |
| 单周期 HMM `predict` | `confidence = probs[state]`，features 含 `hmm_probs`（状态索引数组） | 有状态概率但未映射为制度概率、未输出 |
| 多周期 HMM `predict`（[regime_hmm.py](file:///d:/Programs/factor_system/fts/factor_engine/regime_hmm.py#L234-L285)） | `confidence = clip(vote_share × avg_conf × 2.0, 0, 0.99)` | `×2.0` 为启发式放大，非概率语义 |
| `_detect_by_rule` | 软投票给出单一制度 + 启发式置信度 | 无概率分布 |
| `regime_adaptive_weight_adjustment`（[portfolio_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py#L615-L732)） | `mult = table[regime_name][family]` 硬查表 | 硬切换，制度误判时全错 |
| `build_combo`（[portfolio_loop.py](file:///d:/Programs/factor_system/fts/factor_engine/portfolio_loop.py#L2094-L2177)） | 归一化 `weight = weight/total_w` | 归一化抵消任何全局缩放 → 置信度无法影响总仓位 |
| `RegimeSmoother`（[adaptive_weight.py](file:///d:/Programs/factor_system/fts/factor_engine/adaptive_weight.py#L124-L165)） | 对称指数平滑（α=0.3，稳定 3 日） | 无 de-risk/re-risk 不对称 |
| HMM 训练 | 状态数固定 4、特征未标准化 | BIC 未选择、量纲污染高斯协方差 |
| 置信度 | 直接使用 | 未做熵标定（机构：低确定度降权） |
| 验证 | 无 | 制度有效性无样本外检验 |

---

## 1. 对标基准（顶级机构 + 学术界，依据本项目知识库）

| 实践 | 来源 | 本计划对应任务 |
|---|---|---|
| 平滑概率 (smoothed probability) 作为制度概率分布 | 学术界正统（Hamilton 1989；Ang & Bekaert 2002） | T1/T2 |
| 状态数用 BIC / 对数似然选择，三状态优于二状态 | [SSRN 5785443：Regime-Based Portfolio Allocation Using HMM + RL](file:///D:/Knowledge/paper/2026-07-29_Regime-Based%20Portfolio%20Allocation%20Using%20.md)（BIC 选择、可解释性、一日执行延迟） | T8 |
| 特征只 fit 训练段、防数据泄露 | [VAE 市场画像](file:///D:/Knowledge/quant/method/2026-07-21_用变分自编码器给市场画像：两个坐标读懂A股情绪的量化思路.md)（StandardScaler 只在训练集 fit；重构误差 = 状态陌生度/置信度信号） | T5/T8 |
| regime blend：按制度概率对条件权重做加权混合，而非硬切换 | Two Sigma / AQR 实务；SSRN 5785443 的 RL 制度条件动作 | T3 |
| 低确定性降仓（熵高 → 减暴露） | 知识库 VAE 一文重构误差定位"置信度/警示信号" | T5/T6 |
| 波动率目标 (vol targeting) 作为隐性制度响应 | Man Group AHL / Winton CTA 实务 | T6（简化：conf→exposure） |
| de-risk 快 / re-risk 慢的滞后确认 | Man AHL、PIMCO 类战术配置实务 | T7 |
| 制度标签需样本外验证 | VAE 一文局限性与学术界通则 | T9 |

> 本计划不引入 Bridgewater 式宏观四象限（需宏观数据面板）与 RL 决策层（需实盘反馈），二者登记为 P2 远期差距（见 §7）。

---

## 2. 文件改动地图

```
fts/factor_engine/
├── regime.py                    # 修改：MarketRegime 契约 + 各路径 regime_probs 填充
├── regime_hmm.py                # 修改：MultiHorizonHMM 置信度公式 + probs 输出；_LightHMM 概率映射
├── regime_calibration.py        # 新建：RegimeConfidenceCalibrator（熵标定）+ 规则伪概率构造
├── regime_model_selection.py    # 新建：BIC 状态数选择 + 特征标准化
├── regime_validation.py         # 新建：制度有效性样本外验证
├── portfolio_loop.py            # 修改：regime blend + build_combo exposure_scale
├── adaptive_weight.py           # 修改：RegimeSmoother 不对称切换
├── contracts.py                 # 修改：AdaptiveWeightConfig 新字段；PortfolioCombo 新字段
fts/config/settings.py           # 修改：新配置项透传（如需）
fts/monitor/prometheus_metrics.py# 修改：regime 观测指标
docs/harness/                    # 修改：01-architecture / 06-testing / 07-operations / 08-gap / 09-advancement / README
tests/factor_engine/
├── test_regime.py               # 修改/新增
├── test_regime_hmm.py           # 修改/新增
├── test_regime_calibration.py   # 新建
├── test_regime_model_selection.py  # 新建
├── test_regime_validation.py    # 新建
├── test_portfolio_loop_adaptive.py # 修改/新增
```

---

## 3. 任务分解

### Task 1: `MarketRegime` 契约扩展 + 规则/单周期/兜底路径输出 `regime_probs`

**Files:**
- Modify: `fts/factor_engine/regime.py`（契约 + `_detect_by_rule` + `HMMRegimeDetector.predict` + `RegimeAwareSelector.detect` 兜底）
- Test: `tests/factor_engine/test_regime.py`

**对标：** 平滑概率 → 制度概率分布（Ang & Bekaert 2002）。

- [ ] **Step 1: 写失败测试**

```python
# tests/factor_engine/test_regime.py 追加
def test_market_regime_has_regime_probs_distribution():
    """规则方法必须输出全制度概率分布（和为 1，覆盖 5 制度）。"""
    ohlcv = _make_ohlcv(n=300, trend=0.001)
    result = _detect_by_rule(ohlcv, prev_regime=None)
    probs = result["regime_probs"]
    assert set(probs.keys()) == {"bull", "bear", "oscillate", "high_vol", "low_vol"}
    assert abs(sum(probs.values()) - 1.0) < 1e-6
    assert all(0.0 <= v <= 1.0 for v in probs.values())
    # 主制度概率应为其置信度的主导项
    assert probs[result["regime"]] > 0.0


def test_fallback_regime_probs():
    """兜底路径 regime_probs 应为 {oscillate: 1.0}。"""
    result = RegimeAwareSelector().detect(pd.DataFrame())  # 空数据 → fallback
    assert result["regime_probs"] == {"oscillate": 1.0}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/factor_engine/test_regime.py::test_market_regime_has_regime_probs_distribution -v`
Expected: FAIL — `KeyError: 'regime_probs'`

- [ ] **Step 3: 实现契约与填充**

```python
# fts/factor_engine/regime.py — MarketRegime 契约
class MarketRegime(TypedDict):
    """市场制度检测结果。"""
    regime: str
    confidence: float
    detected_at: str
    features: dict
    method: str
    regime_probs: dict[str, float]  # 新增：全制度概率分布（和为 1）
```

规则路径构造伪概率（软投票打分反向映射，逻辑与 `_detect_by_rule` 软投票一致）：

```python
# fts/factor_engine/regime_calibration.py（新建，含规则伪概率；Task 5 复用此文件）
_ALL_REGIMES = ("bull", "bear", "oscillate", "high_vol", "low_vol")


def build_rule_regime_probs(
    trend_score: float,  # -1 ~ 1
    vol_score: float,  # 0 ~ 1
) -> dict[str, float]:
    """由软投票得分构造全制度伪概率（和=1）。

    逻辑与 _detect_by_rule 软投票一致:
      - 趋势得分贡献 bull/bear，波动得分贡献 high_vol，
        低波无趋势贡献 low_vol，余量归 oscillate。
    """
    raw: dict[str, float] = {
        "bull": max(0.0, trend_score) * (1.0 - vol_score),
        "bear": max(0.0, -trend_score) * (1.0 - vol_score),
        "high_vol": vol_score * 0.6,
        "low_vol": (1.0 - vol_score) * (1.0 - abs(trend_score)) * 0.5,
        "oscillate": 0.0,
    }
    total = sum(raw.values())
    if total <= 1e-12:  # 全零 → 无信息分布
        return {"oscillate": 1.0}
    probs = {k: v / total for k, v in raw.items()}
    probs["oscillate"] = max(0.0, 1.0 - sum(v for k, v in probs.items() if k != "oscillate"))
    return probs
```

- [ ] **Step 4: 接线填充（`_detect_by_rule` 返回处、`HMMRegimeDetector.predict`、兜底分支）**

```python
# regime.py _detect_by_rule 末尾（return 前）
return MarketRegime(
    regime=regime,
    confidence=round(confidence, 4),
    detected_at=datetime.now().isoformat(),
    features=features,
    method="rule",
    regime_probs=build_rule_regime_probs(trend_score, vol_score),
)
```

`HMMRegimeDetector.predict` 中将状态概率映射为制度概率：

```python
# regime.py HMMRegimeDetector.predict（已有 probs = self._model.predict_proba(features)[-1]）
state = int(self._model.predict(features)[-1])
probs = self._model.predict_proba(features)[-1]
regime = self._state_map.get(state, "oscillate")
confidence = float(min(1.0, max(0.0, probs[state])))
regime_probs = _states_to_regime_probs(probs, self._state_map)  # 新增辅助
self._last_confidence = confidence
return regime, confidence, {"hmm_state": state, "hmm_probs": probs.tolist(), "regime_probs": regime_probs}
```

辅助函数（同文件）：

```python
def _states_to_regime_probs(state_probs: np.ndarray, state_map: dict[int, str]) -> dict[str, float]:
    """HMM 状态概率数组 → 制度概率分布（同制度多状态求和）。"""
    agg: dict[str, float] = {}
    for s, p in enumerate(state_probs):
        r = state_map.get(s, "oscillate")
        agg[r] = agg.get(r, 0.0) + float(p)
    total = sum(agg.values()) or 1.0
    return {r: p / total for r, p in agg.items()}
```

兜底分支：`regime_probs={"oscillate": 1.0}`（`RegimeAwareSelector.detect` 的两处 fallback 返回 + `_detect_by_rule` 空数据兜底）。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/factor_engine/test_regime.py -v`
Expected: PASS（含既有用例，回归不破坏）

> **端到端修复注记（2026-08-11 抽查发现）**：HMM/MSM 路径的 `regime_probs` 起初只写入 `features`，未提升到 `MarketRegime` 顶层，导致真实管线中 regime blend 与熵标定实际走回退路径。修复：`RegimeAwareSelector.detect` 末尾统一提升——当 `result["regime_probs"]` 为 None 时从 `features["regime_probs"]` 提取。配套测试 `test_detect_promotes_hmm_regime_probs_to_top_level` 锁定该行为。

- [ ] **Step 6: Commit**

```bash
git add fts/factor_engine/regime.py fts/factor_engine/regime_calibration.py tests/factor_engine/test_regime.py
git commit -m "feat(regime): MarketRegime 输出 regime_probs 全制度概率分布（规则/单周期HMM/兜底）"
```

---

### Task 2: 多周期 HMM 置信度公式修正 + `regime_probs` 输出

**Files:**
- Modify: `fts/factor_engine/regime_hmm.py`（`MultiHorizonHMMDetector.predict` + `_LightHMM.predict`）
- Test: `tests/factor_engine/test_regime_hmm.py`

**对标：** 置信度应是有概率语义的后验加权平均，去掉 `×2.0` 启发式；学术平滑概率口径。

- [ ] **Step 1: 写失败测试**

```python
# tests/factor_engine/test_regime_hmm.py 追加
def test_multi_hmm_confidence_is_weighted_posterior():
    """多周期 HMM 置信度 = 各周期后验概率的加权平均，且 ∈[0,1]，非 ×2 启发式。"""
    det = MultiHorizonHMMDetector(horizons=[63, 126], weights={63: 0.4, 126: 0.6})
    ohlcv = _make_ohlcv(n=300, trend=0.002)
    regime, conf, feats = det.predict(ohlcv)
    assert 0.0 <= conf <= 1.0
    assert "regime_probs" in feats
    probs = feats["regime_probs"]
    assert abs(sum(probs.values()) - 1.0) < 1e-6


def test_multi_hmm_probs_agree_with_regime():
    """主制度应为其概率最大项。"""
    det = MultiHorizonHMMDetector()
    ohlcv = _make_ohlcv(n=400, trend=-0.003)  # 明确下跌
    regime, conf, feats = det.predict(ohlcv)
    probs = feats["regime_probs"]
    assert probs[regime] == max(probs.values())
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/factor_engine/test_regime_hmm.py::test_multi_hmm_confidence_is_weighted_posterior -v`
Expected: FAIL — `KeyError: 'regime_probs'`

- [ ] **Step 3: `_LightHMM.predict` 输出制度概率**

```python
# regime_hmm.py _LightHMM.predict 返回处
state = int(self._model.predict(features)[-1])
probs = self._model.predict_proba(features)[-1]
regime = self._state_map.get(state, "oscillate")
confidence = float(min(1.0, max(0.0, probs[state])))
regime_probs = _light_states_to_regime_probs(probs, self._state_map)
return regime, confidence, {"hmm_state": state, "regime_probs": regime_probs}


def _light_states_to_regime_probs(state_probs, state_map):
    agg: dict[str, float] = {}
    for s, p in enumerate(state_probs):
        r = state_map.get(s, "oscillate")
        agg[r] = agg.get(r, 0.0) + float(p)
    total = sum(agg.values()) or 1.0
    return {r: p / total for r, p in agg.items()}
```

- [ ] **Step 4: 修正 `MultiHorizonHMMDetector.predict` 置信度与概率输出**

```python
# regime_hmm.py MultiHorizonHMMDetector.predict — 替换投票计数逻辑
for h, det in self._detectors.items():
    w = self.weights.get(h, 1.0)
    try:
        det.maybe_fit(ohlcv)
        regime, conf, feats = det.predict(ohlcv)
        if regime != "unknown" and conf >= 0.3:
            votes[regime] = votes.get(regime, 0.0) + w
            confidences.append(conf * w)
            # 各周期制度概率 → 加权聚合
            for r, p in (feats.get("regime_probs") or {}).items():
                regime_probs_agg[r] = regime_probs_agg.get(r, 0.0) + w * p
            horizon_details[h] = {"regime": regime, "confidence": conf, "weight": w}
        else:
            horizon_details[h] = {"regime": "unknown", "confidence": 0.0, "weight": w}
    except Exception as e:
        logger.debug("Horizon %d HMM 预测失败: %s", h, e)
        horizon_details[h] = {"regime": "error", "confidence": 0.0, "weight": w}

if not votes:
    return "unknown", 0.0, {"horizon_details": {str(h): v for h, v in horizon_details.items()}}

# 新置信度：各周期后验概率的加权平均（有概率语义，去掉 ×2 启发式）
total_weight = sum(self.weights.values())
confidence = float(np.clip(sum(regime_probs_agg.values()) / total_weight, 0.0, 0.99))

# 制度概率分布归一化
regime_probs = {r: p / total_weight for r, p in regime_probs_agg.items()}
regime_probs = {r: p / (sum(regime_probs.values()) or 1.0) for r, p in regime_probs.items()}

best_regime = max(regime_probs, key=regime_probs.get)

features = {
    "multi_hmm_votes": {k: round(v, 2) for k, v in votes.items()},
    "multi_hmm_vote_share": round(regime_probs[best_regime], 4),
    "multi_hmm_avg_confidence": round(confidence, 4),
    "regime_probs": {k: round(v, 4) for k, v in regime_probs.items()},
    "horizon_details": {str(h): v for h, v in horizon_details.items()},
}
return best_regime, confidence, features
```

> 注意：`votes` 仍保留用于日志；`regime_probs_agg` 需在循环前初始化。`vote_share` 语义改为 `regime_probs[best_regime]`。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/factor_engine/test_regime_hmm.py -v`
Expected: PASS（含既有用例）

- [ ] **Step 6: Commit**

```bash
git add fts/factor_engine/regime_hmm.py tests/factor_engine/test_regime_hmm.py
git commit -m "feat(regime): 多周期HMM置信度改为加权后验平均并输出 regime_probs"
```

---

### Task 3: regime blend — 制度概率混合权重

**Files:**
- Modify: `fts/factor_engine/portfolio_loop.py`（`regime_adaptive_weight_adjustment`）
- Test: `tests/factor_engine/test_portfolio_loop_adaptive.py`

**对标：** Two Sigma / AQR regime blend：`mult = Σ p_i × table_i`，替代硬查表，制度误判只按概率摊薄而非全错。

- [ ] **Step 1: 写失败测试**

```python
# tests/factor_engine/test_portfolio_loop_adaptive.py 追加
def test_regime_blend_mixes_probability_weighted_multipliers():
    """regime_probs 存在时按概率混合倍率，而非硬查表。"""
    signals = _make_signals([("f1", "trend", 0.10)])
    regime = {
        "regime": "oscillate",
        "confidence": 0.5,
        "regime_probs": {"bull": 0.6, "oscillate": 0.4, "bear": 0.0, "high_vol": 0.0, "low_vol": 0.0},
    }
    adjusted = regime_adaptive_weight_adjustment(signals, regime, _make_factors([("f1", "trend")]))
    # bull trend 倍率 1.3 × 0.6 + oscillate trend 倍率 0.8 × 0.4 = 1.10
    assert abs(adjusted[0]["weight"] - 0.10 * 1.10) < 1e-6


def test_regime_blend_fallback_hardcoded():
    """无 regime_probs 时回退硬查表（向后兼容）。"""
    signals = _make_signals([("f1", "trend", 0.10)])
    regime = {"regime": "bull", "confidence": 0.8}
    adjusted = regime_adaptive_weight_adjustment(signals, regime, _make_factors([("f1", "trend")]))
    assert abs(adjusted[0]["weight"] - 0.10 * 1.3) < 1e-6  # bull/trend=1.3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/factor_engine/test_portfolio_loop_adaptive.py::test_regime_blend_mixes_probability_weighted_multipliers -v`
Expected: FAIL（现逻辑 weight=0.10×0.8=0.08，期望 0.11）

- [ ] **Step 3: 实现概率混合**

```python
# portfolio_loop.py regime_adaptive_weight_adjustment 签名追加参数
def regime_adaptive_weight_adjustment(
    signals: list[PortfolioSignal],
    regime: dict[str, Any],
    factors: list[dict[str, Any]],
    min_weight: float = 0.01,
    dimension: str = "family",
    min_clamp: float = 0.5,
    max_clamp: float = 1.5,
    probability_mix: bool = True,  # 新增：制度概率混合开关
) -> list[PortfolioSignal]:
```

查表段改造（Step 2.5 调用处默认传 `probability_mix=aconfig.get("probability_mix", True)`）：

```python
    regime_name = regime.get("regime", "oscillate")
    family_multipliers = (
        _DATA_DRIVEN_FAMILY_MULTIPLIERS.get(regime_name, {})
        if _DATA_DRIVEN_FAMILY_MULTIPLIERS
        else REGIME_FAMILY_MULTIPLIERS.get(regime_name, {})
    )
    style_multipliers = REGIME_STYLE_MULTIPLIERS.get(regime_name, {})

    # regime blend（T3）：按制度概率对各制度倍率表加权混合
    regime_probs: dict[str, float] | None = regime.get("regime_probs") if probability_mix else None
    if not family_multipliers and not style_multipliers and regime_probs is None:
        logger.info("[L3-Regime] 无制度倍率配置，跳过自适应调整 [regime=%s]", regime_name)
        return signals
```

信号循环内倍率取值段改造（**修正：blend 必须跨制度查全表**，`family_multipliers` 仅含当前制度一个键，不能用它按制度名 r 取表）：

```python
        # 获取各维度倍率（默认 1.0）；启用概率混合时按制度概率跨制度加权
        if regime_probs:
            family_mult = sum(
                p * (family_tables.get(r, {}).get(family, 1.0) if family_tables else 1.0)
                for r, p in regime_probs.items()
            )
            style_mult = sum(
                p * (style_tables.get(r, {}).get(style, 1.0) if style_tables else 1.0)
                for r, p in regime_probs.items()
            )
        else:
            family_mult = family_multipliers.get(family, 1.0)
            style_mult = style_multipliers.get(style, 1.0)
```

其中 blend 全表需在函数开头定义（保留 GAP-L308 数据驱动优先）：

```python
    # blend 需跨制度取表（mult = Σ p_i × table_i）：数据驱动全表优先，缺失回退硬编码全表
    family_tables: dict[str, dict[str, float]] = (
        _DATA_DRIVEN_FAMILY_MULTIPLIERS if _DATA_DRIVEN_FAMILY_MULTIPLIERS else REGIME_FAMILY_MULTIPLIERS
    )
    style_tables: dict[str, dict[str, float]] = REGIME_STYLE_MULTIPLIERS
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/factor_engine/test_portfolio_loop_adaptive.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fts/factor_engine/portfolio_loop.py tests/factor_engine/test_portfolio_loop_adaptive.py
git commit -m "feat(regime): regime_adaptive_weight_adjustment 支持制度概率混合权重(regime blend)"
```

---

### Task 4: `AdaptiveWeightConfig` 配置扩展 + Step 2.5 接线

**Files:**
- Modify: `fts/factor_engine/contracts.py`（`AdaptiveWeightConfig` + `DEFAULT_ADAPTIVE_CONFIG`）
- Modify: `fts/factor_engine/portfolio_loop.py`（Step 2.5 调用处传参）
- Test: `tests/test_config_settings.py` / `tests/factor_engine/test_portfolio_loop_adaptive.py`

**对标：** 机构配置纪律——新行为默认开启但可灰度回退。

- [ ] **Step 1: 写失败测试**

```python
# tests/factor_engine/test_portfolio_loop_adaptive.py
def test_default_adaptive_config_has_probability_mix():
    cfg = DEFAULT_ADAPTIVE_CONFIG
    assert cfg.get("probability_mix", False) is True
    assert 0.0 < cfg.get("confidence_scale_min", 0.3) <= 1.0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/factor_engine/test_portfolio_loop_adaptive.py::test_default_adaptive_config_has_probability_mix -v`
Expected: FAIL

- [ ] **Step 3: 契约扩展**

```python
# contracts.py AdaptiveWeightConfig
class AdaptiveWeightConfig(TypedDict, total=False):
    enabled: bool
    dimension: str
    smoother: dict[str, float]
    min_weight: float
    min_clamp: float
    max_clamp: float
    probability_mix: bool  # 新增：制度概率混合（regime blend，默认 True）
    confidence_scale: bool  # 新增：置信度仓位缩放（默认 True）
    confidence_scale_min: float  # 新增：缩放下限（默认 0.3）
    confidence_entropy_penalty: float  # 新增：熵标定惩罚系数（默认 0.5）


DEFAULT_ADAPTIVE_CONFIG: AdaptiveWeightConfig = AdaptiveWeightConfig(
    enabled=True,
    dimension="both",
    smoother={"alpha": 0.5, "min_days": 2},
    min_weight=0.01,
    min_clamp=0.5,
    max_clamp=1.5,
    probability_mix=True,
    confidence_scale=True,
    confidence_scale_min=0.3,
    confidence_entropy_penalty=0.5,
)
```

- [ ] **Step 4: Step 2.5 接线（`portfolio_loop.py:4213` 附近）**

```python
signals = regime_adaptive_weight_adjustment(
    signals,
    regime,
    factors,
    min_weight=aconfig.get("min_weight", 0.01),
    dimension=aconfig.get("dimension", "both"),
    min_clamp=aconfig.get("min_clamp", 0.5),
    max_clamp=aconfig.get("max_clamp", 1.5),
    probability_mix=aconfig.get("probability_mix", True),
)
# 计算并传递置信度仓位缩放（Task 6 消费）
self._regime_exposure_scale = _compute_exposure_scale(
    regime,
    enabled=aconfig.get("confidence_scale", True),
    scale_min=aconfig.get("confidence_scale_min", 0.3),
    entropy_penalty=aconfig.get("confidence_entropy_penalty", 0.5),
)
```

- [ ] **Step 5: 运行确认通过 + Commit**

Run: `python -m pytest tests/factor_engine/test_portfolio_loop_adaptive.py -v`
Expected: PASS

```bash
git add fts/factor_engine/contracts.py fts/factor_engine/portfolio_loop.py tests/factor_engine/test_portfolio_loop_adaptive.py
git commit -m "feat(regime): AdaptiveWeightConfig 新增 probability_mix/confidence_scale 配置并接线"
```

---

### Task 5: `RegimeConfidenceCalibrator` 熵标定

**Files:**
- Create: `fts/factor_engine/regime_calibration.py`（追加标定器）
- Test: `tests/factor_engine/test_regime_calibration.py`

**对标：** 知识库 VAE 重构误差定位"置信度/警示信号"；后验熵 → 确定性折扣。分布越分散（制度越模糊）置信度折扣越大。

- [ ] **Step 1: 写失败测试**

```python
# tests/factor_engine/test_regime_calibration.py
import numpy as np
from fts.factor_engine.regime_calibration import RegimeConfidenceCalibrator, build_rule_regime_probs


def test_calibrator_penalizes_entropy():
    cal = RegimeConfidenceCalibrator(entropy_penalty=0.5, scale_min=0.3)
    sharp = {"bull": 0.95, "bear": 0.01, "oscillate": 0.02, "high_vol": 0.01, "low_vol": 0.01}
    flat = {"bull": 0.2, "bear": 0.2, "oscillate": 0.2, "high_vol": 0.2, "low_vol": 0.2}
    s = cal.calibrate(0.9, sharp)
    f = cal.calibrate(0.9, flat)
    assert s > f  # 尖锐分布保持高置信，平坦分布被折扣
    assert 0.3 <= s <= 1.0 and 0.3 <= f <= 1.0


def test_calibrator_no_probs_passthrough():
    cal = RegimeConfidenceCalibrator()
    assert abs(cal.calibrate(0.8, None) - 0.8) < 1e-9


def test_rule_regime_probs_normalized():
    probs = build_rule_regime_probs(trend_score=0.5, vol_score=0.2)
    assert abs(sum(probs.values()) - 1.0) < 1e-6
    assert probs["bull"] > probs["bear"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/factor_engine/test_regime_calibration.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现标定器**

```python
# fts/factor_engine/regime_calibration.py
"""Regime 置信度标定与规则伪概率（28 计划 T1/T5）。

机构实践：低确定性（高后验熵）时折扣置信度，进而降低暴露。
"""
from __future__ import annotations

import numpy as np

_ALL_REGIMES = ("bull", "bear", "oscillate", "high_vol", "low_vol")


class RegimeConfidenceCalibrator:
    """置信度熵标定器。

    思路（对标知识库 VAE 重构误差 / 后验熵）：
      校准置信度 = confidence × (1 − entropy_penalty × 归一化熵)
    归一化熵 H_norm = H / ln(N)：均匀分布 → 1（最大折扣），单点分布 → 0（不折扣）。
    """

    def __init__(self, entropy_penalty: float = 0.5, scale_min: float = 0.3) -> None:
        self.entropy_penalty = float(entropy_penalty)
        self.scale_min = float(scale_min)

    def calibrate(self, confidence: float, regime_probs: dict[str, float] | None = None) -> float:
        """返回熵标定后的置信度（∈[scale_min, 1.0]）。"""
        if regime_probs is None or len(regime_probs) < 2:
            return float(np.clip(confidence, self.scale_min, 1.0))
        p = np.array([max(v, 1e-12) for v in regime_probs.values()], dtype=float)
        p = p / p.sum()
        n = len(p)
        entropy = -float(np.sum(p * np.log(p)))
        h_norm = entropy / np.log(n) if n > 1 else 0.0
        scaled = confidence * (1.0 - self.entropy_penalty * h_norm)
        return float(np.clip(scaled, self.scale_min, 1.0))


def build_rule_regime_probs(trend_score: float, vol_score: float) -> dict[str, float]:
    """由软投票得分构造全制度伪概率（见 Task 1）。"""
    raw: dict[str, float] = {
        "bull": max(0.0, trend_score) * (1.0 - vol_score),
        "bear": max(0.0, -trend_score) * (1.0 - vol_score),
        "high_vol": vol_score * 0.6,
        "low_vol": (1.0 - vol_score) * (1.0 - abs(trend_score)) * 0.5,
        "oscillate": 0.0,
    }
    total = sum(raw.values())
    if total <= 1e-12:
        return {"oscillate": 1.0}
    probs = {k: v / total for k, v in raw.items()}
    probs["oscillate"] = max(0.0, 1.0 - sum(v for k, v in probs.items() if k != "oscillate"))
    return probs
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/factor_engine/test_regime_calibration.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add fts/factor_engine/regime_calibration.py tests/factor_engine/test_regime_calibration.py
git commit -m "feat(regime): 新增 RegimeConfidenceCalibrator 熵标定与规则伪概率构造"
```

---

### Task 6: 置信度仓位缩放（conf-scaled exposure）

**Files:**
- Modify: `fts/factor_engine/portfolio_loop.py`（`build_combo` 签名 + 归一化后应用 + Step 5 传参）
- Modify: `fts/factor_engine/contracts.py`（`PortfolioCombo` 新字段）
- Test: `tests/factor_engine/test_portfolio_loop.py`

**对标：** vol targeting 简化版——低确定性（高熵/低置信）降低总暴露；`exposure_scale` 随组合落盘可追溯。

- [ ] **Step 1: 写失败测试**

```python
# tests/factor_engine/test_portfolio_loop.py 追加
def test_build_combo_applies_exposure_scale():
    signals = [
        {"factor_id": "a", "name": "f1", "weight": 2.0, "sharpe": 1.0, "ic": 0.05,
         "turnover": 0.1, "decay_6m": 0.0, "retained": True},
        {"factor_id": "b", "name": "f2", "weight": 1.0, "sharpe": 1.2, "ic": 0.06,
         "turnover": 0.1, "decay_6m": 0.0, "retained": True},
    ]
    combo = build_combo(signals, "equal_weight", "trace-x", exposure_scale=0.5)
    total = sum(s["weight"] for s in combo["signals"])
    assert abs(total - 0.5) < 1e-6  # 归一化 1.0 × scale 0.5
    assert combo.get("exposure_scale") == 0.5
    assert combo.get("regime_meta", {}).get("exposure_scale") == 0.5
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/factor_engine/test_portfolio_loop.py::test_build_combo_applies_exposure_scale -v`
Expected: FAIL（`exposure_scale` 无此参数/字段）

- [ ] **Step 3: 契约与实现**

```python
# contracts.py PortfolioCombo 追加
    exposure_scale: Optional[float]  # 置信度仓位缩放因子（28-T6，None=未启用）
    regime_meta: Optional[dict]  # regime 元信息 {regime, confidence, exposure_scale, entropy_norm}
```

```python
# portfolio_loop.py build_combo 签名追加
def build_combo(
    signals: list[PortfolioSignal],
    mode: Literal["equal_weight", "sharpe_weight", "lightgbm"],
    trace_id: Optional[str] = None,
    prev_weights: Optional[dict[str, float]] = None,
    sticky_config: Optional[dict] = None,
    factor_returns: Optional[pd.DataFrame] = None,
    annualize_factor: float = 252.0,
    market: str = "futures",
    cost_config: Optional[dict] = None,
    turnover_penalty: float = 0.0,
    exposure_scale: Optional[float] = None,  # 新增
    regime_meta: Optional[dict] = None,  # 新增
) -> PortfolioCombo:
```

归一化后应用（`portfolio_loop.py:2166` 归一化块后追加）：

```python
    # 置信度仓位缩放（28-T6）：归一化后统一缩放总暴露，随组合落盘可追溯
    if exposure_scale is not None and total_w > 0:
        for s in retained:
            s["weight"] = s["weight"] * exposure_scale
        logger.info("[L3-WEIGHT] 置信度仓位缩放: exposure_scale=%.4f", exposure_scale)
```

返回值内追加字段（`PortfolioCombo(...)` 构造处）：

```python
        exposure_scale=round(exposure_scale, 4) if exposure_scale is not None else None,
        regime_meta=regime_meta,
```

- [ ] **Step 4: Step 5 调用处传参（`portfolio_loop.py:4309`）**

```python
            combo = build_combo(
                signals,
                self.synthesis_mode,
                trace_id,
                prev_weights=prev_weights or None,
                sticky_config=self.sticky_config,
                factor_returns=factor_returns,
                market=self.market,
                cost_config=self.cost_config,
                turnover_penalty=self.turnover_penalty,
                exposure_scale=self._regime_exposure_scale,  # Task 4 计算
                regime_meta=getattr(self, "_regime_meta", None),
            )
```

Step 2.5 处配套计算 `_regime_exposure_scale` 与 `_regime_meta`（Task 4 的 `_compute_exposure_scale` 占位处实现）：

```python
def _compute_exposure_scale(
    regime: dict[str, Any],
    enabled: bool = True,
    scale_min: float = 0.3,
    entropy_penalty: float = 0.5,
) -> float:
    """由制度置信度计算总仓位缩放因子（None 语义：未启用时返回 1.0）。"""
    if not enabled:
        return 1.0
    from .regime_calibration import RegimeConfidenceCalibrator
    cal = RegimeConfidenceCalibrator(entropy_penalty=entropy_penalty, scale_min=scale_min)
    return cal.calibrate(regime.get("confidence", 0.5), regime.get("regime_probs"))
```

- [ ] **Step 5: 运行确认通过**

Run: `python -m pytest tests/factor_engine/test_portfolio_loop.py -v`
Expected: PASS（含既有用例）

- [ ] **Step 6: Commit**

```bash
git add fts/factor_engine/portfolio_loop.py fts/factor_engine/contracts.py tests/factor_engine/test_portfolio_loop.py
git commit -m "feat(regime): 置信度熵标定仓位缩放 exposure_scale 接入组合构建"
```

---

### Task 7: `RegimeSmoother` 不对称切换（de-risk 快 / re-risk 慢）

**Files:**
- Modify: `fts/factor_engine/adaptive_weight.py`（`RegimeSmoother`）
- Modify: `fts/factor_engine/portfolio_loop.py`（Step 2.5 smoother 实例化传参）
- Test: `tests/factor_engine/test_portfolio_loop_adaptive.py`

**对标：** Man AHL / PIMCO 战术配置——进入风险制度（bear/high_vol）快速降权，回归安全制度（bull/low_vol）缓慢加仓，滞后确认防震荡。

- [ ] **Step 1: 写失败测试**

```python
# tests/factor_engine/test_portfolio_loop_adaptive.py 追加
def test_smoother_asymmetric_de_risk_faster():
    # 注意：min_days 必须 ≥1——should_apply 中 regime 刚切换时 stable_days=0，
    # min_days=0 时 "0 < 0" 为 False，会走稳定期直接采用分支而非过渡期
    smoother = RegimeSmoother(alpha=0.3, min_days=1, de_risk_alpha=0.8, re_risk_alpha=0.1)
    prev = {"a": 1.0}
    # 进入风险制度：快速下降
    w1 = smoother.should_apply("high_vol", prev, {"a": 0.2})
    assert w1["a"] < 0.5  # 0.8×0.2+0.2×1.0=0.36
    # 回归安全制度：缓慢上升
    w2 = smoother.should_apply("bull", w1, {"a": 1.0})
    assert w2["a"] < 0.9  # 0.1×1.0+0.9×0.36=0.424
    assert w2["a"] > w1["a"]
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/factor_engine/test_portfolio_loop_adaptive.py::test_smoother_asymmetric_de_risk_faster -v`
Expected: FAIL（现为对称 α=0.3）

- [ ] **Step 3: 实现不对称平滑**

```python
# adaptive_weight.py RegimeSmoother
_RISK_REGIMES = ("bear", "high_vol")


class RegimeSmoother:
    def __init__(
        self,
        alpha: float = 0.3,
        min_days: int = 3,
        de_risk_alpha: float = 0.8,  # 进入风险制度：快速降权
        re_risk_alpha: float = 0.1,  # 回归安全制度：缓慢加仓
        risk_regimes: tuple[str, ...] = _RISK_REGIMES,
    ) -> None:
        self._alpha = float(alpha)
        self._min_days = int(min_days)
        self._de_risk_alpha = float(de_risk_alpha)
        self._re_risk_alpha = float(re_risk_alpha)
        self._risk_regimes = risk_regimes
        self._current_regime: Optional[str] = None
        self._regime_since: Optional[Any] = None

    def should_apply(self, detected_regime, current_weights, new_weights):
        from datetime import datetime, timezone
        if detected_regime != self._current_regime:
            self._current_regime = detected_regime
            self._regime_since = datetime.now(timezone.utc)
        if self._regime_since is not None:
            stable_days = (datetime.now(timezone.utc) - self._regime_since).days
        else:
            stable_days = 0

        if stable_days < self._min_days:
            # 过渡期：按方向选择不对称平滑系数
            # 修正：必须先保存 prev_regime 再更新 _current_regime，否则
            # "离开风险制度 → re_risk_alpha" 分支恒为死代码（_current_regime 已被覆盖）
            if detected_regime in self._risk_regimes:
                eff_alpha = self._de_risk_alpha  # 进入风险 → 快速降
            elif prev_regime in self._risk_regimes:
                eff_alpha = self._re_risk_alpha  # 离开风险 → 缓慢加
            else:
                eff_alpha = self._alpha
            return {
                fid: eff_alpha * new_weights.get(fid, 0.0) + (1.0 - eff_alpha) * current_weights.get(fid, 0.0)
                for fid in set(current_weights) | set(new_weights)
            }
        return dict(new_weights)
```

- [ ] **Step 4: Step 2.5 实例化传参（`portfolio_loop.py:4228`）**

```python
self._regime_smoother = RegimeSmoother(
    alpha=float(sm.get("alpha", 0.5)),
    min_days=int(sm.get("min_days", 2)),
    de_risk_alpha=float(sm.get("de_risk_alpha", 0.8)),
    re_risk_alpha=float(sm.get("re_risk_alpha", 0.1)),
)
```

- [ ] **Step 5: 运行确认通过 + Commit**

Run: `python -m pytest tests/factor_engine/test_portfolio_loop_adaptive.py -v`
Expected: PASS

```bash
git add fts/factor_engine/adaptive_weight.py fts/factor_engine/portfolio_loop.py tests/factor_engine/test_portfolio_loop_adaptive.py
git commit -m "feat(regime): RegimeSmoother 不对称 de-risk/re-risk 切换"
```

---

### Task 8: HMM 状态数选择（BIC）+ 特征标准化

**Files:**
- Create: `fts/factor_engine/regime_model_selection.py`
- Modify: `fts/factor_engine/regime.py`（`HMMRegimeDetector.fit/predict` 标准化）
- Test: `tests/factor_engine/test_regime_model_selection.py`

**对标：** SSRN 5785443 用 BIC 选状态数（三状态优于二状态）；VAE 一文强调特征只 fit 训练段防泄露。

- [ ] **Step 1: 写失败测试**

```python
# tests/factor_engine/test_regime_model_selection.py
import numpy as np
from fts.factor_engine.regime_model_selection import select_n_states, fit_standardizer


def test_select_n_states_returns_valid_candidate():
    rng = np.random.default_rng(42)
    rets = np.concatenate([
        rng.normal(0.0005, 0.005, 150),
        rng.normal(-0.0005, 0.02, 150),
    ])
    features = np.column_stack([rets, np.abs(rets)])
    n = select_n_states(features, candidates=(2, 3, 4))
    assert n in (2, 3, 4)


def test_standardizer_fit_predict_consistent():
    mean, std = fit_standardizer(np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]]))
    scaled = (np.array([[1.0, 2.0]]) - mean) / std
    assert np.allclose(scaled.mean(), 0.0, atol=1e-6)  # 首行被映射为 -1（0 均值标准化）
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/factor_engine/test_regime_model_selection.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
# fts/factor_engine/regime_model_selection.py
"""HMM 状态数选择（BIC）与特征标准化（28 计划 T8）。

对标: SSRN 5785443 用 BIC 选状态数；VAE 一文特征只 fit 训练段防泄露。
"""
from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

_HMM_AVAILABLE: bool = False
try:
    from hmmlearn import hmm
    _HMM_AVAILABLE = True
except ImportError:
    pass


def fit_standardizer(features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """fit 训练段均值和标准差（只统计训练段，防数据窥探）。"""
    mean = features.mean(axis=0)
    std = features.std(axis=0) + 1e-8
    return mean, std


def select_n_states(
    features: np.ndarray,
    candidates: tuple[int, ...] = (2, 3, 4, 5),
    random_seed: int = 42,
) -> int:
    """用 BIC 选择最优状态数（训练稳定 + BIC 最小者优先）。"""
    if not _HMM_AVAILABLE or len(features) < 60:
        return 4  # 默认回退现状
    best_n: int = candidates[0]
    best_bic: float = float("inf")
    for n in candidates:
        try:
            model = hmm.GaussianHMM(
                n_components=n, covariance_type="diag",
                n_iter=100, tol=1e-4, random_state=random_seed,
            )
            model.fit(features)
            loglik = model.score(features)
            n_params = n * (n - 1) + 2 * n * features.shape[1] * 1  # 转移 + 均值/方差对角
            bic = -2.0 * loglik + n_params * np.log(len(features))
            logger.info("[RegimeModelSelection] n_states=%d BIC=%.2f", n, bic)
            if bic < best_bic:
                best_bic, best_n = bic, n
        except Exception as e:  # noqa: BLE001
            logger.warning("[RegimeModelSelection] n=%d 拟合失败: %s", n, e)
    return best_n
```

`HMMRegimeDetector.fit` 改造（`regime.py`）：训练前标准化、记住参数、训练用选择出的状态数（BIC）；`predict` 用同一参数 transform：

```python
# regime.py HMMRegimeDetector.fit — 特征构建后
        train_features = features[-min(self.lookback, len(features)) :]
        self._scaler_mean, self._scaler_std = fit_standardizer(train_features)
        train_features = (train_features - self._scaler_mean) / self._scaler_std
        # BIC 状态数选择（状态数变化时重建模型）
        if getattr(self, "_last_n_states", None) != self.n_states:
            self.n_states = select_n_states(train_features, candidates=(2, 3, 4))
            self._last_n_states = self.n_states
            self._state_map = {}  # 状态数变化 → 映射失效，重新推断
```

```python
# regime.py HMMRegimeDetector.predict — features 构建后
        features = compute_hmm_feature_vector(ohlcv, base_features=base_features)
        if features.size == 0:
            features = base_features
        if hasattr(self, "_scaler_mean"):
            features = (features - self._scaler_mean) / self._scaler_std
```

- [ ] **Step 4: 运行确认通过 + Commit**

Run: `python -m pytest tests/factor_engine/test_regime_model_selection.py tests/factor_engine/test_regime.py -v`
Expected: PASS

```bash
git add fts/factor_engine/regime_model_selection.py fts/factor_engine/regime.py tests/factor_engine/test_regime_model_selection.py
git commit -m "feat(regime): HMM 状态数 BIC 选择 + 特征标准化(训练段 fit 防窥探)"
```

---

### Task 9: 制度有效性样本外验证

**Files:**
- Create: `fts/factor_engine/regime_validation.py`
- Create: `scripts/validate_regime.py`（CLI 入口）
- Test: `tests/factor_engine/test_regime_validation.py`

**对标：** 学术通则（VAE 一文局限性："Regime 标签需样本外验证"）——制度标签须能区分前向收益/前向波动，否则不应驱动仓位。

- [ ] **Step 1: 写失败测试**

```python
# tests/factor_engine/test_regime_validation.py
import numpy as np
import pandas as pd
from fts.factor_engine.regime_validation import validate_regime_predictive_power


def test_validate_detects_predictive_regimes():
    rng = np.random.default_rng(0)
    n = 300
    regimes = ["bull"] * 150 + ["bear"] * 150
    fwd = np.concatenate([rng.normal(0.001, 0.01, 150), rng.normal(-0.001, 0.01, 150)])
    fwd_vol = np.abs(fwd)
    result = validate_regime_predictive_power(pd.Series(regimes), pd.Series(fwd), pd.Series(fwd_vol))
    assert result["n"] == n
    assert result["bull"]["mean_fwd_return"] > result["bear"]["mean_fwd_return"]
    assert "kruskal_p" in result  # 组间差异统计量
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/factor_engine/test_regime_validation.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现**

```python
# fts/factor_engine/regime_validation.py
"""制度有效性样本外验证（28 计划 T9）。

检验制度标签对前向收益/前向波动的区分能力（Kruskal-Wallis + 条件均值/波动差异）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    from scipy.stats import kruskal
    _SCIPY = True
except ImportError:
    _SCIPY = False


def validate_regime_predictive_power(
    regime_series: pd.Series,
    forward_returns: pd.Series,
    forward_vol: pd.Series,
) -> dict:
    """按制度分桶统计前向收益/波动，输出区分度指标。

    Returns:
        {n, kruskal_p, bull: {mean_fwd_return, mean_fwd_vol, count}, ...}
    """
    df = pd.DataFrame({"regime": regime_series, "fwd": forward_returns, "fwd_vol": forward_vol}).dropna()
    if df.empty:
        return {"n": 0, "error": "empty"}
    groups = {r: g for r, g in df.groupby("regime")["fwd"]}
    out: dict = {"n": int(len(df))}
    if _SCIPY and len(groups) >= 2 and all(len(g) > 1 for g in groups.values()):
        stat, p = kruskal(*groups.values())
        out["kruskal_stat"] = float(stat)
        out["kruskal_p"] = float(p)
    for r, g in df.groupby("regime"):
        out[str(r)] = {
            "count": int(len(g)),
            "mean_fwd_return": float(g["fwd"].mean()),
            "mean_fwd_vol": float(g["fwd_vol"].mean()),
            "fwd_return_std": float(g["fwd"].std()),
        }
    return out
```

`scripts/validate_regime.py`：加载历史 regime 序列（从 `memory/portfolio/` 历史信号或运行期落盘），对每个品种输出验证报告。

- [ ] **Step 4: 运行确认通过 + Commit**

Run: `python -m pytest tests/factor_engine/test_regime_validation.py -v`
Expected: PASS

```bash
git add fts/factor_engine/regime_validation.py scripts/validate_regime.py tests/factor_engine/test_regime_validation.py
git commit -m "feat(regime): 制度有效性样本外验证模块与 CLI"
```

---

### Task 10: 观测性 + 文档同步 + 定向回归

**Files:**
- Modify: `fts/monitor/prometheus_metrics.py`（regime 指标）
- Modify: `docs/harness/01-architecture.md`（Regime 数据流：regime_probs → blend → exposure_scale）
- Modify: `docs/harness/06-testing.md`（新增测试用例数）
- Modify: `docs/harness/07-operations.md`（追加变更记录）
- Modify: `docs/harness/08-gap-analysis.md`（登记 P2 远期：宏观四象限 / RL 制度条件决策 / 概率校准 isotonic）
- Modify: `docs/harness/09-advancement-plan.md`（晋级里程碑）
- Modify: `README.md`（快速参考）
- Test: `tests/monitor/test_prometheus_metrics.py`

**对标：** 机构观测纪律——regime 决策必须可审计（置信度、熵、exposure_scale、blend 权重全部落盘 + 指标化）。

- [ ] **Step 1: 指标实现（`prometheus_metrics.py` 追加）**

```python
# fts/monitor/prometheus_metrics.py — 新增指标定义
_REGIME_CONFIDENCE = Gauge("fts_regime_confidence", "当前市场制度置信度", ["market", "regime"])
_REGIME_ENTROPY = Gauge("fts_regime_entropy_norm", "制度后验归一化熵(0~1, 越高越不确定)", ["market"])
_REGIME_EXPOSURE_SCALE = Gauge("fts_regime_exposure_scale", "置信度仓位缩放因子", ["market"])
_REGIME_BLEND_HHI = Gauge("fts_regime_blend_hhi", "制度概率分布集中度(HHI)", ["market"])
```

配套记录函数 `record_regime_metrics(market, regime, confidence, probs, exposure_scale)`，在 `PortfolioLoop` Step 2.5/Step 5 后调用（scrape 路径参考现有 metrics 注册模式）。

- [ ] **Step 2: 测试**

```python
# tests/monitor/test_prometheus_metrics.py 追加
def test_regime_metrics_recorded():
    record_regime_metrics("futures", "bear", 0.7, {"bear": 0.7, "bull": 0.1, "oscillate": 0.1,
                                                  "high_vol": 0.05, "low_vol": 0.05}, 0.6)
    sample = list(_REGIME_CONFIDENCE.collect())[0].samples
    assert any(s.labels["market"] == "futures" and s.labels["regime"] == "bear" for s in sample)
```

- [ ] **Step 3: 文档同步（HARNESS 13 项检查清单逐项核对）**

1. 架构变更 → `01-architecture.md`：Regime 章节补充 regime_probs → blend → exposure_scale 数据流与接口定义；
2. 阶段/产出物 → `02-lifecycle.md` 如有阶段名变更；
3. 新配置项 → `03-configuration.md`：`probability_mix / confidence_scale / confidence_scale_min / confidence_entropy_penalty / smoother.de_risk_alpha / smoother.re_risk_alpha`；
4. 降级/熔断 → `01-architecture.md`（blend 无 probs 回退硬查表、calibrator 无 probs 直通）；
5. 新指标/日志 → `05-observability.md`：`fts_regime_*` 指标 + `[L3-WEIGHT] 置信度仓位缩放` 日志；
6. 测试数 → `06-testing.md`：新增用例数统计；
7. 版本历史 → `07-operations.md` 追加（日常开发不 bump，见 v2.101.0 纪律）；
8. 差距登记 → `08-gap-analysis.md`：P2 远期（宏观四象限、RL 制度条件、isotonic 校准）；
9. 晋级里程碑 → `09-advancement-plan.md`；
10. 流程文档 → `execution_modes_flowchart.md / business_flow.md` 如涉及；
11. 角色职责 → `agents/*.md` 如涉及；
12. README 快速参考 → `README.md`。

校验：`python scripts/verify_doc_consistency.py`（13/13 通过）。

- [ ] **Step 4: 定向回归（分级测试政策）**

Run:
```bash
python -m pytest tests/factor_engine/test_regime.py tests/factor_engine/test_regime_hmm.py tests/factor_engine/test_regime_calibration.py tests/factor_engine/test_regime_model_selection.py tests/factor_engine/test_regime_validation.py tests/factor_engine/test_portfolio_loop.py tests/factor_engine/test_portfolio_loop_adaptive.py tests/factor_engine/test_stock_regime.py tests/factor_engine/test_sector_regime.py tests/monitor/test_prometheus_metrics.py tests/test_config_settings.py -v
```
Expected: 全 PASS（未跑全量，符合分级测试政策）

同时：`python -m ruff check fts/factor_engine/regime.py fts/factor_engine/regime_hmm.py fts/factor_engine/regime_calibration.py fts/factor_engine/regime_model_selection.py fts/factor_engine/regime_validation.py fts/factor_engine/portfolio_loop.py fts/factor_engine/adaptive_weight.py fts/factor_engine/contracts.py fts/monitor/prometheus_metrics.py tests/factor_engine/ scripts/validate_regime.py`

- [ ] **Step 5: Commit**

```bash
git add fts/monitor/prometheus_metrics.py docs/harness/ tests/monitor/test_prometheus_metrics.py README.md
git commit -m "feat(regime): 观测指标 + HARNESS 文档同步 + 定向回归通过"
```

---

## 4. 验证与交付（全局成功标准）

1. 所有任务测试通过（定向回归全绿，未跑全量，报告注明）；
2. `python scripts/verify_doc_consistency.py` 13/13 通过；
3. 运行一次真实信号管线（`scripts/futures_signal_pipeline.py`）抽查：
   - 报告中出现 `regime_probs`、`exposure_scale`、`regime_blend` 日志；
   - `memory/portfolio/current_combo.json` 含 `exposure_scale` / `regime_meta`；
4. 行为开关验证：
   - `probability_mix=False` 时权重与改造前硬查表一致（回归快照）；
   - `confidence_scale=False` 时 `exposure_scale=1.0`；
   - 无 `regime_probs` 的历史 regime 字典（如 stock_regime 旧格式）自动回退，不抛异常。

## 5. 风险与回退

| 风险 | 缓解 |
|---|---|
| regime blend 拉平倍率差异（全部接近 1.0） | 概率分布通常尖锐（HMM 后验），实测验证；如过平则 `blend_power` 参数（后续 P2） |
| BIC 状态数选择不稳定（每次 refit 变化） | 状态数变化时冻结映射（StateMapStabilizer 已防翻转）+ 变化日志告警 |
| 多周期 HMM 新置信度偏低 → 触发 conf<0.3 gate 回退规则 | gate 阈值可配置；观察期用旧公式对比 |
| 仓位缩放影响组合夏普口径（权重和 ≠ 1） | exposure_scale 仅整体缩放，相对权重不变；指标口径不变 |
| 规则伪概率与软投票判定不一致 | 伪概率由同一 trend_score/vol_score 构造，测试锁定一致性 |

## 6. 远期差距（登记 08-gap-analysis.md，P2）

- ~~宏观制度（Bridgewater 增长×通胀四象限）——需宏观数据面板接入（18-macro-field-enhancement 基础）~~ **✅ 已落地（GAP-092，v2.104.0+3）**：新建 `fts/factor_engine/macro_regime.py` `MacroRegimeDetector`——增长（制造业 PMI，akshare）× 通胀（CPI 当月同比，东财已闭环）水平阈值四象限（overheat/goldilocks/stagflation/recession）+ 置信度（主象限联合概率）+ 象限画像；`scripts/macro_regime_report.py` 报告 CLI；真实实测 2026-07 = recession 衰退（PMI 49.2 + CPI 0.5%，置信 0.507）；月度发布天然滞后防未来函数，量价/宏观双维度独立并存。
- RL 制度条件决策层（SSRN 5785443）——需实盘反馈闭环（simulated_portfolio 已铺垫）；
- ~~置信度 isotonic/Platt 校准——需足够历史 regime 标签（T9 验证模块提供基础）~~ **✅ 已落地（GAP-094，v2.104.0+1）**：`StatisticalRegimeCalibrator`（isotonic/Platt/binning + sklearn 缺失降级 + save/load）+ `scripts/fit_regime_calibration.py` 离线拟合（滚动检测重放 + 制度方向预期命中标签 + Brier/阶梯诊断）+ `_compute_exposure_scale` calibration_path 接线（默认熵标定，文件有效优先统计校准）；真实 RB0 校准产物 `data/regime_calibration.json`——阶梯表 [0.6,0.8) 置信 0.679↔命中 0.673，置信度具备频率语义。
- ~~regime blend 幂次调节（`blend_power`）——视实测倍率拉平情况启用~~ **✅ 已落地（GAP-095，v2.104.0+1）**：`_power_normalize_probs` 幂次归一化 + `regime_adaptive_weight_adjustment(blend_power=...)` + `AdaptiveWeightConfig.blend_power`（默认 1.0 线性向后兼容）；>1 锐化大概率制度、<1 钝化趋平。
