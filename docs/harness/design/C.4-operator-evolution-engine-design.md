# C.4 算子演化引擎 — 详细技术设计（Phase 3+）

> 版本: v3.0.0+7
> 关联: [C.1-feature-engineering-platform-design.md](file:///d:/Programs/factor_system/docs/harness/design/C.1-feature-engineering-platform-design.md)（特征工程中台）、`fts/factor_engine/expr_dsl/`（FTS-Expr DSL 基础层）
> 状态: **已实现**（v2.10.0）
> 实现说明: 由 `fts/factor_engine/operator_evolution.py`（`OperatorEvolutionEngine`）承担，在 DSL 算子空间做适应度导向的进化式搜索，产物为 **OPERATOR 类型因子**（`kind=OPERATOR`、携带 `expression`/`max_lookback`）。关闭 GAP-026（GP 引擎算子命名与 DSL 对齐：引擎直接以 DSL 注册表为算子空间）。

---

## 1. 目标与范围

在 **FTS-Expr DSL 算子空间**（58 算子，L0-L5）内实现**适应度导向的进化式搜索**，取代当前 `_generate_operator_factor` 的纯随机组合生成，产出携带 `kind=OPERATOR` 的因子。

**核心差异（相对现状）**:

| 维度 | 现状 `_generate_operator_factor` | 目标 OperatorEvolutionEngine |
|:-----|:-----|:-----|
| 搜索方式 | 单次随机组合（10 次尝试） | 种群进化（初始化→评估→选择→交叉→变异→精英，多代迭代） |
| 适应度导向 | 无（不过滤质量） | IC + Sharpe 组合适应度 |
| 算子空间 | DSL 注册表（仅用于生成） | DSL 注册表（生成 + 校验 + 语义边界） |
| 产物 | OPERATOR 因子 | OPERATOR 因子（同类） |
| 质量保障 | 仅语法校验 | 语法校验 + PIT lookback + 适应度筛选 |

**范围**:
- `OperatorEvolutionEngine` 引擎实现（初始化/评估/选择/交叉/变异/精英）
- DSL 表达式空间进化算子（ExprNode 层面交叉/变异）
- 与 L2 Evolution Loop 集成（operator/hybrid 模式）
- 测试与文档同步

**不在范围**:
- 横截面评估（期货面板 IC）— 引擎第一版支持单序列评估，横截面场景由 L2 循环选取代表序列，后续版本再扩展
- LLM 演化、`GPEvolver`（feature_ops 版）改造 — 两者保持独立
- DSL 注册表算子扩充

---

## 2. 架构设计

### 2.1 总体架构

```
L2 Evolution Loop (evolution_loop.py)
  └── _generate_operator_factor()  ── operator/hybrid 模式入口
        │
        ▼
OperatorEvolutionEngine (fts/factor_engine/operator_evolution.py)
  ├── 算子空间: expr_dsl/registry.build_registry()  (58 算子 L0-L5)
  ├── 个体: FTS-Expr 字符串 + ExprNode (AST)
  ├── 适应度: expr_dsl/executor.evaluate() → IC/Sharpe
  ├── 校验: expr_dsl/validator.validate_expr()  (参数边界 + PIT lookback)
  └── 产物: expr_dsl/factory.create_operator_factor()  → OPERATOR 因子
```

### 2.2 数据流

```
data_panel + target_col (forward_return)
        │
        ▼
[初始化] 随机生成 population_size 个合法 FTS-Expr（validator 校验通过）
        │
        ▼
[评估]   DSL executor 计算因子值 → 与 target 对齐 → IC / Sharpe → fitness
        │
        ▼  ┌────────────── 达到 max_generations? ─────────────┐
        │  ▼ 否                                              ▼ 是
   [选择] 锦标赛选择父代 ──► [交叉] 子树交换 + 校验     [输出] 最优表达式
        │                  [变异] 子树替换/参数扰动 + 校验     │
        │                  精英保留 + 无效个体重试              ▼
        ▼                                                   create_operator_factor()
  下一代种群 ◄────────────────────────────────────────      OPERATOR 因子
```

---

## 3. 接口契约（契约优先）

### 3.1 配置

```python
@dataclass
class OperatorEvolutionConfig:
    population_size: int = 100        # 种群大小
    max_generations: int = 20         # 最大代数
    tournament_size: int = 3          # 锦标赛大小
    crossover_rate: float = 0.7       # 交叉率
    mutation_rate: float = 0.15       # 变异率
    max_tree_depth: int = 5           # 表达式最大深度
    elitism_size: int = 5             # 精英保留数
    fitness_metric: Literal["ic", "sharpe", "ic_sharpe_combo"] = "ic_sharpe_combo"
    max_attempts: int = 30            # 无效个体（校验失败）重试上限
    random_seed: int = 42             # 随机种子（可复现）
```

### 3.2 结果

```python
@dataclass
class OperatorGenerationSnapshot:
    generation: int
    best_fitness: float
    best_expression: str
    avg_fitness: float
    population_diversity: float       # 唯一表达式占比

@dataclass
class OperatorEvolutionResult:
    best_expression: str
    best_fitness: float
    best_ic: float
    best_sharpe: float
    generations_completed: int
    history: list[OperatorGenerationSnapshot]
    total_evaluations: int
```

### 3.3 引擎类

```python
class OperatorEvolutionEngine:
    def __init__(
        self,
        data_panel: pd.DataFrame,
        target_col: str,
        registry: dict[str, OperatorMeta] | None = None,   # 默认 build_registry()
        config: OperatorEvolutionConfig | None = None,
    ) -> None: ...

    def evolve(self) -> OperatorEvolutionResult: ...        # 执行进化搜索

    def best_factor_program(
        self,
        result: OperatorEvolutionResult,
        *,
        name: str,
        market: str,
        family: str,
        narrative: str,
        trace_id: str | None = None,
        parent_id: str | None = None,
        generation: int = 0,
        source: str = "operator_evolution",
    ) -> FactorProgram: ...                                 # 最优表达式 → OPERATOR 因子
```

---

## 4. 进化算子设计

### 4.1 个体表示

- 个体 = FTS-Expr 字符串（如 `rank(ts_zscore(close, 60))`）
- 内部操作对象 = `ExprNode`（`expr_dsl/ast.py`，kind: op/field/const）
- 字符串 ↔ AST 互转：`parse_expression()` / 序列化函数

### 4.2 初始化（`_random_expression`）

- 从 registry 按层级采样：L1 时序算子（1-2 个字段，窗口按 5 的倍数采样，受 `param_bounds` 约束）→ 可选 L4 组合算子 → 可选 L2 横截面封装
- 每个候选必须通过 `validate_expr`（参数边界 + PIT lookback），失败重试至 `max_attempts`

### 4.3 适应度评估（`_evaluate_fitness`）

- `expr_dsl.executor.evaluate(node, data_panel, registry)` → 因子值 Series
- 与 `target_col` 对齐（dropna，样本 ≥ 20），计算 IC（Pearson）
- Sharpe：因子值 `pct_change` 的年化 Sharpe
- fitness = `abs(ic) * 0.6 + max(sharpe, 0) * 0.4`（`ic_sharpe_combo`，默认），全 NaN 或样本不足 → 罚分
- 结果缓存（按表达式字符串），避免重复评估

### 4.4 选择（锦标赛）

- 每轮从种群随机抽 `tournament_size` 个个体，取 fitness 最高者为父代
- 交叉 / 变异按概率执行

### 4.5 交叉（`_crossover`）

- 解析两个父代 AST，各自随机选一个内部节点，交换子树
- 深度超限或 `validate_expr` 失败 → 重试（至 `max_attempts`），仍失败则原样继承父代

### 4.6 变异（`_mutate`）

三种变异策略（随机选一）：
1. **子树替换**：随机选节点替换为同参数量合法随机子树
2. **参数扰动**：常量参数在 `param_bounds` 内 ±20% 扰动
3. **字段替换**：L0 字段替换为另一字段
- 变异常量窗口取整（int 参数）、受边界约束；`validate_expr` 失败重试

### 4.7 精英保留

- 每代按 fitness 排序，前 `elitism_size` 个原样进入下一代

---

## 5. 校验与 PIT 防护

| 防护 | 机制 |
|:-----|:-----|
| 参数边界 | 初始化/变异均受 `OperatorMeta.param_bounds` 约束 |
| 未来函数 | `validate_expr` → `compute_max_lookback` 静态 PIT 校验，所有个体强制通过 |
| 非法表达式 | 解析失败/未知算子/参数数量不符 → 重试机制 |
| 常信号因子 | 适应度评估对全 NaN / 常数信号施加罚分 |

---

## 6. 与 L2 Evolution Loop 集成

`evolution_loop._generate_operator_factor` 改造：

```
operator 模式 / hybrid fallback:
  if 可评估（data + forward_returns 有效）:
      engine = OperatorEvolutionEngine(data, target=forward_returns)
      result = engine.evolve()
      return engine.best_factor_program(result, ...)     # 适应度导向
  else:
      回退随机组合生成（原逻辑保留，作为无评估数据时的 fallback）
```

- 引擎评估数据源与 `_run_gp_evolution` 一致（`self.data` + `self.forward_returns`）
- 横截面模式用代表序列评估（与 micro_evolution 选 `cross_section_data` 首序列一致）
- 产物仍走 Step 1.3 运行时校验 → 预筛选 → 微观演化 → 评估链 → Verifier → 准入的既有链路，**零侵入**

---

## 6.1 P0 修复契约（GAP-074，v2.100.0）

> 背景: 2026-08-11 csi300 演化实测 50 代 elite_count=0。根因两处：① L2 父因子 UCT 统计仅在评估通过时更新，失败路径导致 `visits` 恒 0、`_select_parent_uct` 永远返回 `parents[0]`；② 算子演化种子仅由父因子 factor_id MD5 派生，同父因子不同代生成完全相同的表达式。

### 6.1.1 UCT 失败反馈（P0-1）

新增 `EvolutionLoop._update_uct_failure(parent)`：visits+1、正奖励不加（与 `_update_uct_stats` 的 reward 语义区分，失败不得获得正奖励）。接线三条 continue 路径：

| 路径 | 位置 | 语义 |
|:-----|:-----|:-----|
| 演化失败 | `_evolve_one` 返回 None | 父因子被选中但未产出因子 |
| 运行时校验失败 | Step 1.3 `runtime_ok=False` | 产物不合格 |
| 快速预筛选失败 | Step 1.4 `prefilter_ok=False` | 产物 IC 不达标 |

修复后 UCT 在 `visits==0` 优先探索的前提下，失败父因子 `visits` 递增，下一轮 `_select_parent_uct` 自然切换到下一个未访问父因子，129 个 elite 父因子逐轮覆盖，消除选择坍缩。

### 6.1.2 种子注入代际（P0-2）

`_try_operator_engine_evolution` 种子派生由 `md5(factor_id)` 改为 `md5(f"{factor_id}::{generation}")`。同父因子不同代产生不同搜索轨迹；同父同代仍完全可复现（确定性保留）。

### 6.1.3 验收

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | 失败父因子 visits 递增，`_select_parent_uct` 切换至下一未访问父因子 | 单元测试 |
| 2 | 同父因子不同 generation → 引擎 random_seed 不同 | 单元测试 |
| 3 | 同父因子同 generation → 种子一致（可复现性保留） | 单元测试 |

---

## 7. 文件改动清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `fts/factor_engine/operator_evolution.py` | **新增** | 算子演化引擎（核心交付） |
| `fts/factor_engine/evolution_loop.py` | **修改** | `_generate_operator_factor` 接入引擎 |
| `tests/factor_engine/operator_evolution/test_operator_evolution.py` | **新增** | 引擎单测（初始化/进化/交叉/变异/产物） |
| `tests/factor_engine/test_executor_dispatch.py` | **修改** | 引擎产物为 OPERATOR 因子的分派验证 |
| `docs/harness/01-architecture.md` | **修改** | 模块树 + 架构图 |
| `docs/harness/02-lifecycle.md` | **修改** | Phase 15 |
| `docs/harness/06-testing.md` | **修改** | 测试用例数 |
| `docs/harness/07-operations.md` | **修改** | 版本历史 v2.10.0 |
| `docs/harness/08-gap-analysis.md` | **修改** | 关闭 GAP-026 |
| `docs/harness/09-advancement-plan.md` | **修改** | v2.10.0 里程碑 |
| `pyproject.toml` / `fts/__init__.py` | **修改** | 版本 2.9.0 → 2.10.0 |
| `README.md` | **修改** | 工程指标/文档链接 |

---

## 8. 验收标准

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | 初始化种群全部为合法 FTS-Expr（validator 通过） | 单元测试 |
| 2 | evolve 返回适应度 > 0 的最优表达式，多代后 fitness 单调不降 | 单元测试 |
| 3 | 交叉/变异产物全部通过 validator（参数边界 + PIT） | 单元测试 |
| 4 | `best_factor_program` 产出 `kind=OPERATOR`、携带 `expression`/`max_lookback` 的因子 | 单元测试 |
| 5 | 无效个体重试机制生效（超限后优雅降级） | 单元测试 |
| 6 | evolution_loop operator 模式走引擎（mock 验证调用） | 集成测试 |
| 7 | 全量回归无既有失败新增 | pytest 回归 |

---

## 9. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 关联文档 | C.1 特征工程中台（GP 搜索引擎的 DSL 版）、[02-lifecycle.md](../02-lifecycle.md) Phase 15 |
| 关联计划 | [11-factor-mining-optimization-plan.md](../11-factor-mining-optimization-plan.md) → Phase C.2（算子演化） |
| 依赖模块 | `expr_dsl/registry.py`（算子空间）、`expr_dsl/validator.py`（PIT）、`expr_dsl/executor.py`（评估）、`expr_dsl/factory.py`（产物）、`expr_dsl/parser.py`（AST） |
| 前置条件 | FTS-Expr DSL 基础层可用（v2.9.0 已就绪）、evolution_loop 的 data/forward_returns 可用 |
| 后置影响 | 算子演化从"随机生成"升级为"适应度导向进化"，GAP-026 关闭 |
| 技术依赖 | pandas / numpy（无新增第三方依赖） |
