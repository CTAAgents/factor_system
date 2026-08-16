# D.1 批量挖掘漏斗 — 详细技术设计（GAP-I201，Stage 1）

> 版本: v2.104.0+89
> 目标版本: v2.65.0
> 关联: [23-institutional-transformation-plan.md](../plans/23-institutional-transformation-plan.md) GAP-I201（挖掘吞吐不足）、`fts/factor_engine/evolution_loop.py`（L2 主循环）
> 状态: **设计完成，待实施**
> 定位: Stage 1 首版，单机多进程并行；分布式扩展（GAP-I502 ExecutorBackend）为 Stage 3 远期预留，不在本设计范围

---

## 1. 目标与范围

### 1.1 问题

`EvolutionLoop.run()`（evolution_loop.py L433）主循环 `for generation in range(...)` 每代仅生成 **1 个**后代因子：单父 UCT 选择 → 单次演化分派（macro→GP→operator 逐级 fallback）→ 单因子运行时校验 → 单因子预筛 → 单因子细评估（optuna 100 trials + 三级评估链 + 4 重审查 + 审计）。一夜 50 代约几十个候选，候选量级与机构挖掘工厂（数万/夜）差 2~3 个数量级。

### 1.2 目标

| 指标 | 现状 | 目标（v2.65.0） |
|:-----|:-----|:----------------|
| 每代候选生成数 | 1 | `batch_size`（默认 20） |
| 每代进入细评估数 | ≤1 | `max_candidates`（默认 5） |
| 粗筛吞吐 | 单因子串行 | 批量并行（ThreadPoolExecutor） |
| 单夜候选吞吐 | 几十 | ≥ 500 粗筛 / ≥ 50 细评估（≥10×） |
| 细评估/审计/准入链 | — | **零改动复用**（保证准入纪律不放松） |

### 1.3 范围

- 新增 `fts/factor_engine/batch_mining.py`：批量漏斗模块（批量生成 / 批量运行时校验 + 预筛 / 结果统计）
- `evolution_loop.py` 重构：抽取 `_evolve_one`（演化分派）与 `_process_candidate`（Step 2-6 准入链）两个公共方法；新增 `_run_batch_generation`（batch 模式一代漏斗）
- `settings.py` 新增 batch 配置项
- 测试与文档同步（design/01-architecture/02-lifecycle/03-configuration/06-testing/07-operations/08-gap-analysis/09-advancement-plan + pyproject + README）

### 1.4 不在范围

- 分布式/GPU 并行（GAP-I502，Stage 3）
- 深度因子学习（GAP-I203，Stage 2）
- 多目标适应度（GAP-I204，v2.70.0）
- 微观演化自适应 trials（GAP-I205，v2.68.0）
- 细评估链（micro/evaluation/audit/审查）逻辑本身**不修改**，仅复用

---

## 2. 架构设计

### 2.1 总体架构（漏斗分层）

```
L2 Evolution Loop (evolution_loop.py)
  └── run() for generation
        ├─ parent = _select_parent_uct(parent_seeds)        # 既有 UCT 选择，不变
        │
        ├─ evolution_mode == "batch" ?
        │     YES ──► _run_batch_generation()               # 新：一代批量漏斗
        │                └─ BatchMiner (batch_mining.py)
        │                     ├─ [粗筛层] generate_batch(): batch_size 个后代
        │                     │    (对同一父因子循环 _evolve_one，方法/seed 交替)
        │                     ├─ [粗筛层] filter_batch(): 批量运行时校验 + 批量预筛
        │                     │    (ThreadPoolExecutor 并行，秒级)
        │                     └─ [精筛层] 通过者 ≤ max_candidates
        │                                 └─ for each candidate:
        │                                     micro → _process_candidate()  # 既有链复用
        │     NO  ──► 既有单因子路径: _evolve_one → runtime → prefilter
        │                     └─ _process_candidate()       # 同一公共方法
```

### 2.2 关键设计决策

| # | 决策 | 理由 |
|---|------|------|
| D1 | **准入链零改动复用**：细评估/审计/4 重审查/晋升逻辑全部抽取为 `_process_candidate`，batch 与单因子路径共用 | 保证"批量提速但不放松准入纪律"（回测-实盘强对齐红线） |
| D2 | **同父多后代**：一代内对同一父因子用不同演化方法/seed 生成多个后代 | UCT 父选择逻辑不变，改动最小；多后代提升单父搜索密度 |
| D3 | **粗筛并行、精筛串行**：运行时校验 + 预筛用 ThreadPoolExecutor（numpy/scipy 纯计算）；细评估因共享可变状态（UCT 统计/state/elite_ids）保持串行 | 粗筛是吞吐瓶颈（批量拦截），精筛每候选分钟级且数量受 max_candidates 控制 |
| D4 | **max_candidates 预算护栏**：通过粗筛数超过上限时按预筛 IC 排序截断 | 防止单代细评估爆炸，token/算力可控（与既有 token 熔断协同） |
| D5 | **batch 模式可回退**：`evolution_mode="batch"` 时 batch 生成全部失败则回退单因子路径（hybrid 语义） | 容错兜底，避免批量失败导致整代空转 |

### 2.3 数据流

```
parent_seed (FactorProgram)
   │ _evolve_one × batch_size（method 轮换: macro/gp/operator + seed 递增）
   ▼
proposals: list[BatchedProposal]          # 每个含 factor/method/summary/tokens
   │ filter_batch(): ① _check_factor_runtime ② _quick_prefilter（并行）
   ▼
passed: list[BatchedProposal]             # 截断 ≤ max_candidates，按预筛 IC 降序
   │ for each proposal:
   ▼
_process_candidate(factor, ...)           # 既有链: micro → eval → UCT → verifier
   │                                        → 质检卡 → 回测 → DQ → 审计 → 4 重审查 → promote
   ▼
elite_ids + state 更新（既有）
```

---

## 3. 接口契约（契约优先）

### 3.1 批量挖掘配置

```python
@dataclass
class BatchMiningConfig:
    batch_size: int = 20                 # 每代批量候选生成数
    max_candidates: int = 5              # 通过粗筛后进入细评估的最大候选数
    max_workers: int = 4                 # 粗筛并行线程数
    ic_threshold: float | None = None    # 预筛 IC 阈值（None = 按市场自适应，复用 _quick_prefilter）
    random_seed: int = 42                # 随机种子（可复现）
```

### 3.2 批量候选契约

```python
class BatchedProposal(TypedDict, total=False):
    factor: FactorProgram                # 后代因子
    parent_id: str                       # 父因子 ID（UCT 反馈用）
    method: str                          # 演化方法: macro_evolution/gp_evolution/operator_evolution
    summary: str                         # 演化摘要（经验链/失败轨迹用）
    tokens: int                          # LLM token 消耗（state 计数用）
    prefilter_ok: bool                   # 粗筛通过标记
    prefilter_reason: str                # 未通过原因（记录用）
    prefilter_ic: float                  # 预筛 IC（排序截断依据，未评估时 0.0）
```

### 3.3 批量结果契约

```python
@dataclass
class BatchGenerationResult:
    generation: int                      # 代数
    total_generated: int                 # 生成候选总数
    total_passed: int                    # 通过粗筛数
    total_rejected: int                  # 被粗筛拦截数
    passed: list[BatchedProposal]        # 进入细评估的候选（≤ max_candidates）
    rejected: list[BatchedProposal]      # 被拦截候选（含原因）
    tokens_consumed: int                 # 本代 LLM token 消耗
    duration_ms: float                   # 粗筛耗时（监控用）
```

### 3.4 挖掘器类

```python
class BatchMiner:
    def __init__(
        self,
        config: BatchMiningConfig | None = None,
        *,
        generate_cb: Callable[[FactorProgram, int, str], BatchedProposal | None] | None = None,
        runtime_check_cb: Callable[[FactorProgram], tuple[bool, str]] | None = None,
        prefilter_cb: Callable[[FactorProgram, str], tuple[bool, str, float]] | None = None,
    ) -> None: ...

    def generate_batch(self, parent: FactorProgram, generation: int, trace_id: str) -> list[BatchedProposal]:
        """批量生成 batch_size 个后代（依赖注入 generate_cb，循环调用）。"""

    def filter_batch(self, proposals: list[BatchedProposal], trace_id: str) -> BatchGenerationResult:
        """批量运行时校验 + 批量预筛（ThreadPoolExecutor 并行），按 prefilter_ic 排序截断。"""

    def run_iteration(self, parent: FactorProgram, generation: int, trace_id: str) -> BatchGenerationResult:
        """一代完整漏斗：generate_batch → filter_batch。"""
```

> 依赖注入设计：`generate_cb`/`runtime_check_cb`/`prefilter_cb` 由 `evolution_loop` 提供（封装既有 `_evolve_one`/`_check_factor_runtime`/`_quick_prefilter`），BatchMiner 保持零业务耦合、可独立单测。

### 3.5 evolution_loop 公共方法签名（抽取）

```python
def _evolve_one(
    self,
    parent: FactorProgram,
    generation: int,
    trace_id: str,
    *,
    seed: int | None = None,             # batch 模式传不同 seed 保证多样性
) -> tuple[FactorProgram, str, str] | None:
    """演化分派：生成 1 个后代（macro→GP→operator 逐级 fallback）。返回 (factor, method, summary) 或 None。"""

def _process_candidate(
    self,
    factor: FactorProgram,
    parent: FactorProgram,
    generation: int,
    evolution_method: str,
    evolution_summary: str,
    state: dict[str, Any],
    elite_ids: list[str],
    trace_id: str,
    seed_correlations: list[FactorCorrelation],
) -> bool:
    """Step 2-6 准入链：micro → 三级评估 → UCT 反馈 → Verifier → 质检卡 → 回测 → DQ → 审计 → 4 重审查 → 晋升。返回是否晋升。"""

def _run_batch_generation(
    self,
    parent: FactorProgram,
    generation: int,
    trace_id: str,
    state: dict[str, Any],
    elite_ids: list[str],
    seed_correlations: list[FactorCorrelation],
) -> bool:
    """batch 模式一代漏斗：BatchMiner.run_iteration → 对 passed 候选循环 _process_candidate。返回是否至少 1 个晋升。"""
```

---

## 4. 批量生成策略

### 4.1 方法轮换

同一父因子、同一代内，`generate_cb` 按以下顺序轮换演化方法，保证多后代多样性：

| 轮次 | 方法 | 说明 |
|:-----|:-----|:-----|
| 1 | macro_evolution | LLM 改逻辑（每 batch 至多 1 次，token 成本高） |
| 2~k | gp_evolution | GP 遗传规划（CPU 成本，seed 递增） |
| k+1~N | operator_evolution | DSL 算子演化（CPU 成本，seed 递增） |

> token 护栏：batch 模式每代仅允许 1 次 macro（LLM）调用，其余后代走 GP/operator（纯 CPU），避免 token 预算爆炸触发既有熔断（`nightly_token_limit` 2x 熔断不变）。

### 4.2 seed 递增

batch 内 GP/operator 演化以 `random_seed + i`（i=后代序号）驱动，保证同父多后代可复现且不重复。

---

## 5. 校验与安全

| 防护 | 机制 |
|:-----|:-----|
| 未来函数 | 复用 `_check_factor_runtime` + `_quick_prefilter`（与单因子路径同一执行路径，零差异） |
| token 熔断 | batch 每代至多 1 次 macro（LLM）调用；`state.add_tokens` 计数与既有 `_check_circuit_breaker` 协同 |
| 细评估爆炸 | `max_candidates` 截断（按 prefilter_ic 降序） |
| 全失败回退 | batch 生成全失败 → 记录失败轨迹 → 回退单因子路径（hybrid 语义） |
| 并行安全 | 粗筛并行仅读（probe_data/forward_returns 只读），无共享可变状态；细评估串行保留 UCT/state 一致性 |
| 可复现 | `random_seed` 配置化，batch 内 seed 确定性递增 |

---

## 6. 与 L2 Evolution Loop 集成

`evolution_loop.py` 改动（外科手术式）：

```
run() for generation 内:
  parent = self._select_parent_uct(parent_seeds)          # 不变
  if _evo_mode == "batch":
      self._run_batch_generation(parent, generation, trace_id, state, elite_ids, seed_correlations)
      # batch 模式：token/低IC熔断与 state 持久化沿用既有逻辑（generation 末尾）
  else:
      new_factor, method, summary = self._evolve_one(parent, generation, trace_id)
      if new_factor is None:
          self._record_failure_trace(...)                # 既有失败轨迹
          continue
      runtime_ok, reason = self._check_factor_runtime(new_factor)
      if not runtime_ok: ... continue
      prefilter_ok, reason = self._quick_prefilter(new_factor, trace_id)
      if not prefilter_ok: ... continue
      self._process_candidate(new_factor, parent, generation, method, summary, state, elite_ids, trace_id, seed_correlations)
```

> 既有 `_evolve_one` 内部逻辑（evolution_mode 分派 + UCT fallback + 失败记录）从 run() 原样平移，行为不变。`_process_candidate` 从 run() Step 2-6 原样平移，行为不变。**仅增加 `_run_batch_generation` 分支**。

---

## 7. 文件改动清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `fts/factor_engine/batch_mining.py` | **新增** | 批量漏斗模块（BatchMiner/Config/契约） |
| `fts/factor_engine/evolution_loop.py` | **修改** | 抽取 `_evolve_one`/`_process_candidate`；新增 `_run_batch_generation`；run() 增加 batch 分支 |
| `fts/config/settings.py` | **修改** | `evolution_mode` 校验集新增 `batch`；新增 `batch_size`/`batch_max_candidates`/`batch_max_workers` 配置 |
| `tests/factor_engine/test_batch_mining.py` | **新增** | BatchMiner 单测（生成/过滤/截断/并行/回退） |
| `tests/factor_engine/test_evolution_loop.py` | **修改** | batch 模式集成测试 + 抽取方法回归 |
| `docs/harness/design/D.1-batch-mining-design.md` | **新增** | 本设计文档 |
| `docs/harness/01-architecture.md` | **修改** | 模块树 + L2 架构图（batch 漏斗） |
| `docs/harness/02-lifecycle.md` | **修改** | L2 阶段描述 |
| `docs/harness/03-configuration.md` | **修改** | 新配置项 |
| `docs/harness/06-testing.md` | **修改** | 测试用例数 |
| `docs/harness/07-operations.md` | **修改** | 版本历史 v2.65.0 |
| `docs/harness/08-gap-analysis.md` | **修改** | GAP-I201 关闭（v2.65.0） |
| `docs/harness/09-advancement-plan.md` | **修改** | v2.65.0 里程碑 |
| `pyproject.toml` / `fts/__init__.py` | **修改** | 版本 2.60.0 → 2.65.0 |
| `README.md` | **修改** | 工程指标/模块列表 |

---

## 8. 验收标准

| # | 标准 | 检验方式 |
|---|------|----------|
| 1 | `BatchMiner.generate_batch` 生成 ≤ batch_size 个合法后代，method 轮换 + seed 递增生效 | 单元测试 |
| 2 | `filter_batch` 并行过滤与串行结果逐一对齐（通过/拦截完全一致） | 单元测试 |
| 3 | passed 截断 ≤ max_candidates 且按 prefilter_ic 降序 | 单元测试 |
| 4 | batch 模式每代至多 1 次 macro（LLM）调用（token 护栏） | mock 断言 |
| 5 | batch 生成全失败时回退单因子路径且记录失败轨迹 | 单元测试 |
| 6 | `_process_candidate` 抽取后既有单因子路径行为不变（回归全绿） | 全量回归 |
| 7 | run() batch 模式集成测试：一代内处理多个候选、晋升 ≥ 0 且状态持久化正确 | 集成测试 |
| 8 | 吞吐基准：batch 模式每代粗筛 ≥ batch_size 候选，单夜 ≥ 500 粗筛（基准断言） | 性能基准测试 |
| 9 | 全量回归无既有失败新增 + 一致性 13/13 | pytest + verify_doc_consistency |

---

## 9. 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/factor_engine/batch_mining.py`（新增）；`fts/factor_engine/evolution_loop.py`（_evolve_one/_process_candidate/_run_batch_generation）；`fts/config/settings.py`（batch 配置）；`tests/factor_engine/test_batch_mining.py`（新增测试） |
| 可验证断言 | batch 模式每代生成 batch_size 个候选、粗筛并行与串行一致、passed ≤ max_candidates、token 护栏（每代 ≤1 次 macro）、全失败回退单因子路径、_process_candidate 抽取后单因子路径回归全绿、GAP-I201 关闭（v2.65.0） |
| 检验方式 | `python scripts/verify_doc_consistency.py`；`pytest tests/factor_engine/test_batch_mining.py -v`；全量回归 |
