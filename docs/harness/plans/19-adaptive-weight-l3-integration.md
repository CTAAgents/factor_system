# Adaptive 权重完整接入 L3 实施计划（Phase 33 / v2.56.0）

> 版本: v3.1.0
> 最后更新: 2026-08-09
> 状态: 进行中（P1 文档先行）
> 适用范围: FTS L3 Portfolio Loop + 因子元数据 Schema

---

## 0. 背景与目标

### 0.1 现状

FTS 当前存在两套 Regime 自适应权重机制：

1. **L3 主循环已激活**：`PortfolioLoop.run()` Step 2.5 直接调用 `regime_adaptive_weight_adjustment()`（`fts/factor_engine/portfolio_loop.py`），按 `REGIME_FAMILY_MULTIPLIERS`（Regime × FactorFamily 倍率表）对合成后权重做乘法修正。
2. **未接线组件**：`fts/factor_engine/adaptive_weight.py` 的 `AdaptiveWeightManager` / `RegimeSmoother`、`fts/factor_engine/portfolio_constructor.py` 的 `PortfolioConstructor(weight_method="adaptive")` 均已实现，但仅被测试引用，L3 生产路径未接入。

原设计文档 `docs/harness/design/A.3-adaptive-weight-design.md` 中声明的 **FactorStyle / style_tags 维度** 始终未实现（仅实现 FactorFamily 维度）。

### 0.2 问题（GAP-045）

| 维度 | 现状 | 问题 |
|---|---|---|
| 回测 vs 生产一致性 | 回测管线 B.2 用 `PortfolioConstructor`（含 adaptive），L3 生产用裸 `regime_adaptive_weight_adjustment` | 两套入口逻辑不同步，违反"回测与实盘强对齐"红线 |
| Regime 切换平滑 | L3 权重应用层无平滑（平滑只存在于 regime 检测层 `_REGIME_PERSISTENCE_FACTOR`） | Regime 切换时权重可瞬时跳变 |
| 调整维度 | 仅 FactorFamily（17 家族） | 缺失原设计 FactorStyle（momentum/value/low_vol 等风格维度） |

### 0.3 目标

1. **RegimeSmoother 接入 L3 Step 2.5**：Regime 切换时权重指数平滑，参数 `alpha=0.5, min_days=2`（更灵敏）。
2. **PortfolioConstructor 统一入口**：`synthesis_mode` 新增 `adaptive`，L3 Step 2 可委托 constructor，回测/生产路径同源。
3. **实现 FactorStyle / style_tags 维度**：落地原设计 A.3 未实现项。

### 0.4 不在范围

- Regime 检测算法本身（已在 `regime.py` 实现，不改动）
- 因子正交化 / 衰减检验 / 粘性约束现有逻辑
- 删除既有 FactorFamily 机制（family 与 style 并行，`dimension="both"` 乘积）

---

## 1. 契约设计

### 1.1 新增 FactorStyle 枚举（`fts/factor_engine/contracts.py`）

```python
FactorStyle = Literal[
    "momentum", "mean_reversion", "carry", "value", "low_vol",
    "high_beta", "defensive", "growth", "quality", "sentiment",
    "volatility", "open_interest", "cross_section", "intraday",
]
```

### 1.2 新增 style_tags 字段

**DuckDB**（`fts/factor_engine/factor_db/schema.py`）：

```sql
ALTER TABLE factor_catalog ADD COLUMN style_tags JSON;
```

**FactorProgram 契约**：新增可选字段 `style_tags: list[str]`，缺省由 `FactorStyleClassifier` 从 code 关键词推断。

### 1.3 新增 REGIME_STYLE_MULTIPLIERS 倍率表（portfolio_loop.py）

与 `REGIME_FAMILY_MULTIPLIERS` 并行，维度为 style（未覆盖 style 默认 1.0 中性）：

| style | bull | bear | oscillate | high_vol | low_vol |
|:---|:---:|:---:|:---:|:---:|:---:|
| momentum | 1.3 | 0.8 | 0.8 | 0.7 | 1.2 |
| mean_reversion | 0.7 | 1.2 | 1.3 | 1.1 | 1.0 |
| carry | 1.1 | 1.0 | — | 1.0 | — |
| volatility | 0.9 | 1.3 | 1.1 | 1.3 | 0.7 |
| defensive | 1.0 | 1.3 | 1.1 | 1.3 | 1.0 |
| quality | 1.1 | 1.1 | 1.0 | 0.9 | 1.1 |
| sentiment | 1.2 | 0.8 | 1.0 | 0.8 | 1.0 |
| cross_section | 1.1 | 0.9 | 1.0 | 0.8 | 1.0 |
| low_vol | 0.9 | 1.1 | 1.1 | 1.2 | 1.0 |
| value | 0.9 | 1.2 | 1.0 | 0.9 | 1.1 |

### 1.4 新契约

```python
class AdaptiveWeightConfig(TypedDict):
    enabled: bool                        # 默认 True
    dimension: Literal["family", "style", "both"]  # 默认 "both"
    smoother: dict[str, float]           # {alpha: 0.5, min_days: 2}
    min_weight: float                    # 0.01

# PortfolioLoop.__init__ 新增
synthesis_mode: str = "elastic_net"      # 扩展可选 "adaptive"
adaptive_config: Optional[AdaptiveWeightConfig] = None
```

---

## 2. 分步实施

| 阶段 | 内容 | 验证 |
|---|---|---|
| **P1 文档先行** | 新建本 plan + 更新 01-architecture / 02-lifecycle / 06-testing / 07-operations / 08-gap-analysis / A.3-design | `verify_doc_consistency.py` 全绿 |
| **P2 契约+Schema** | FactorStyle 枚举、style_tags 列（含兼容补列）、FactorStyleClassifier、REGIME_STYLE_MULTIPLIERS；先写测试 | `pytest tests/factor_engine/ -k "style or contract"` |
| **P3 统一入口** | PortfolioConstructor 支持 `weight_method="adaptive"` 双维度；PortfolioLoop Step 2 支持 `synthesis_mode="adaptive"` 委托 constructor；`load_elite_factors` 读入 style_tags | 集成测试 `test_portfolio_loop_adaptive.py` |
| **P4 平滑接入** | RegimeSmoother(alpha=0.5, min_days=2) 接入 Step 2.5，参数走 adaptive_config | `test_adaptive_weight.py` 平滑用例全绿 |
| **P5 调度/CLI** | jobs.py 透传、CLI `--mode adaptive` | `tests/scheduler/test_jobs.py` |
| **P6 收尾** | 全量回归 `pytest tests/ -v`、版本 bump、13 项检查清单、README 同步 | 4000+ passed、一致性 13/13 |

---

## 3. 风险与防护

1. **style 与 family 双倍率相乘过度调权**：`dimension="both"` 乘积后 clamp 到 `[0.5×base, 1.5×base]`。
2. **旧因子无 style_tags**：分类器从 code 关键词推断 + 缺省 neutral，不阻塞老库兼容。
3. **smoother 与 regime 检测平滑双重钝化**：alpha=0.5 灵敏档，min_days=2 缩短过渡期，实施时回归验证权重跳变幅度。

---

## 4. 检查清单映射

| # | 检查项 | 本方案落地 |
|---|---|---|
| 1 | 数据流/架构变更 → 01-architecture.md | ✅ L3 章节补 adaptive/smoother/style 节点 |
| 2 | 阶段/产出物 → 02-lifecycle.md | ✅ Phase 33 |
| 6 | 测试 → 06-testing.md | ✅ 新增用例数 |
| 7 | 版本 → 07-operations.md + pyproject.toml | ✅ v2.56.0 |
| 8 | 差距 → 08-gap-analysis.md | ✅ GAP-045 登记 |
| 9 | 晋级 → 09-advancement-plan.md | ✅ 里程碑 |
| 12 | README | ✅ 合成模式列表 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `FactorStyle` 枚举 + `REGIME_STYLE_MULTIPLIERS` → `fts/factor_engine/contracts.py`、`fts/factor_engine/portfolio_loop.py`；`style_tags` → `fts/factor_engine/factor_db/schema.py`；`AdaptiveWeightConfig` → `fts/factor_engine/portfolio_loop.py`；平滑 → `fts/factor_engine/adaptive_weight.py` |
| 可验证断言 | `synthesis_mode` 支持 `"adaptive"` 且 `PortfolioLoop.__init__` 接受 `adaptive_config`；`factor_catalog` 表含 `style_tags` 列；`REGIME_STYLE_MULTIPLIERS` 覆盖 10 个 style × 5 种 regime；v2.56.0 在 pyproject.toml 定义 |
| 检验方式 | `pytest tests/factor_engine/test_portfolio_loop_adaptive.py -v`；`python scripts/verify_doc_consistency.py` |
