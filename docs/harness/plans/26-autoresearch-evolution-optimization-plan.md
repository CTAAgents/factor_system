# 26 号计划 — L2 因子演化优化计划（最终版）

> 版本: v2.104.0+4
> 最后更新: 2026-08-11
> 关联: GAP 登记待执行时补充（08-gap-analysis.md）
> 目标版本: v2.102.0（Phase 1.x 完成后经 scripts/bump_version.py bump）
> 状态: ✅ 核心优化项已全部实施（2026-08-11），待 G2 开启前后晋升率对比报告归档后正式关闭

---

## 1. 背景与目标

对照 Karpathy 式 autoresearch 优化方法论（批量变体生成 → 模拟专家评审 → 演化胜者 → 实验日志），逐项核对 FTS L2 演化链路（L1 Meta-Loop → L2 Evolution → L3 Portfolio Loop）后，确认以下缺口：

| autoresearch 方法论 | FTS 现状 | 缺口 |
|:-----|:-----|:-----|
| Round 2 演化学习：分析胜者模式 → 定向强化 | 仅注入失败教训（experience_chain），无成功模式注入 | **P0-1** |
| Round 3 最弱维度定向优化 | 失败归因仅全局统计，未传递给子代 | **P0-2** |
| 质量门槛分级处置（70-79 再优化一轮） | 质量卡 A/B 直接晋升、C 淘汰 | **P1-1（暂缓）** |
| 完整实验日志（experiments JSON） | traces/state 分散，无统一运行产物 | **P1-2** |
| 达标即停 | 固定跑满 max_generation | **P1-3** |
| 跨元素杂交 | L3 Elastic Net 已实现组合层；L2 杂交重复建设 | **P2-1（暂缓）** |

**目标**：通过 P0-1/P0-2 提升演化命中率（单位预算有效产出↑），P1-2 建立元分析能力，P1-3 节约 token 预算。所有改动仅作用于 L2 演化层，不触及信号管道、已晋升因子与实盘链路（角色边界）。

### 1.1 作用范围与边界（L2 专属）

```
L1 Meta-Loop           L2 Evolution           L3 Portfolio
────────────────────────────────────────────────────────────
候选生成/注入 ──────►  种子评估 + 因子演化 ◄─────   组合合成
   │                      │  ↑ 本次优化全部在此（改动面）
   │                      ▼
   └── 失败轨迹 ──►  经验链（供演化参考）
```

| 环节 | 关系 | 说明 |
|:-----|:-----|:-----|
| **L2（主战场）** | ✅ 全部改动 | `EvolutionLoop`（主循环 + 演化分派 + batch 生成 + 停止条件）、`MacroEvolver`（prompt 注入）、`ExperienceChain`（失败归因接口）、新增 `success_pattern.py` / `experiment_log.py` |
| **L1（间接受益）** | ⭕ 不改代码 | L1 注入候选进入 L2 后走种子评估路径；其失败轨迹记入经验链，未来作为父因子演化时被 P0-2 定向归因复用 |
| **L3（不受影响）** | ❌ 不触碰 | 组合合成、Elastic Net、Regime 权重、信号管道与 elite 存储均保持不变（角色边界） |

**不改动清单**：评估链（evaluation_chain）/ 审计链（audit）/ 质量评分卡 / 回测流水线 / L3 组合 / 信号管道 / 实盘链路 —— 这些环节保持现状，仅作为 P0-1/P0-2 的评估兜底。

## 2. 收益评估摘要（优先级依据）

| 优化 | 收益类型 | 确定性 | 成本 | 结论 |
|:-----|:-----|:-----|:-----|:-----|
| **P0-2** 父代失败归因定向修复 | 命中率↑ | 高（信息已存在，仅传递） | 极低 | 立即做 |
| **P0-1** 成功模式定向演化 | 命中率↑ | 中（有 regime 过拟合风险） | 低 | 高优先级 |
| **P1-2** 结构化实验日志 | 元优化 | 高（数据已有） | 低 | 长期价值 |
| **P1-3** 提前达标停止 | 成本↓ | 高（成本端） | 低 | 条件性（Phase 0 数据决定） |
| **P1-1** B 级定向再演化 | 质量↑ | 中 | 中 | 暂缓（B 级已正贡献） |
| **P2-1** L2 定向跨因子杂交 | 质量↑ | 低 | 高 | 暂缓（与 L3 重复建设） |

**关键前提**：所有"命中率"型优化的边际收益取决于当前演化瓶颈位置（生成质量 vs 评估过严），须先经 Phase 0 基线统计确认，再落地 P0-1。

## 3. 阶段总览

| 阶段 | 内容 | 优先级 | 依赖 | 状态 |
|:-----|:-----|:-----|:-----|:----|
| Phase 0 | 演化基线数据采集（晋升率/失败归因/方法对比/后段产出） | 前置必做 | 无 | ✅ 已完成（2026-08-11，基线报告 + GAP-079 审计排查，§4.2） |
| Phase 1.1 | P0-2 父代失败归因定向修复 | 最高 | Phase 0 | ✅ 已实施（2026-08-11，test_failure_guidance.py 9 用例全绿） |
| Phase 1.2 | P0-1 成功模式定向演化 | 高 | Phase 0 | ✅ 已实施（2026-08-11，test_success_pattern.py 14 用例全绿） |
| **Phase 1.3** | **结构性聚类配额替代 max_per_family 家族配额** | **高** | **GAP-I206 运行验证** | **✅ 已实施（GAP-077，2026-08-11）** |
| Phase 2 | P1-2 结构化实验日志 | 中 | 无 | ✅ 已实施（2026-08-11，test_experiment_log.py 9 用例全绿） |
| Phase 3 | P1-3 提前达标停止 | 条件性 | Phase 0 | ✅ 已实施（2026-08-11，test_evolution_stop.py 8 用例全绿，§8.8） |

> **📌 计划收尾标注（2026-08-11）**：核心优化项（P0-1/P0-2/P1-2/P1-3 + Phase 1.3 结构簇配额）已全部实施落地，各阶段测试全绿、文档同步、07-operations 备案完成。剩余仅长期运行验证项：**G2 决策门**（P0-1 开启前后 1 周晋升率对比报告，§3.1/§13 终期退出标准）与版本 bump 至 v2.102.0（按发布里程碑制统一执行），二者归档/执行后本计划正式关闭。P1-1/P2-1 为主动暂缓项（触发条件见 §9），不属于未完成。

每阶段标准交付物（对齐 25 号计划）：
1. 契约先行：TypedDict/dataclass 字段扩展
2. 代码：扩展既有模块或新建模块（向量化、NaN 兜底、配置化、无硬编码路径）
3. 测试：新增测试文件，覆盖成功/边界/降级路径
4. 文档：01-architecture / 06-testing / 07-operations / 08-gap-analysis 同步 + 本计划进度
5. 回归：受影响模块定向回归全绿

### 3.1 执行顺序与决策门

```
Phase 0 基线统计（只读）
   │
   ├─ 决策门 G1: 晋升率 ≥ 5% → P0-1/P0-2 边际收益有限，仅做 P0-2（成本≈0）
   │           晋升率 < 5% 或失败归因集中 → 按原计划推进 P0-1/P0-2
   ▼
Phase 1.1 (P0-2) ──► 回归 + bump ──► Phase 1.2 (P0-1)
   │                                      │
   │                              决策门 G2: 开启前后 1 周晋升率对比
   │                                      │（下降 → 关闭开关并复核）
   ▼                                      ▼
Phase 1.3 (结构簇配额) ◄────── Phase 2 (P1-2)
   │                              │
   │ 决策门 G4: GAP-I206 正交化闭环运行验证通过后切换
   ▼                              ▼
Phase 3 (P1-3) ──────────────────┘
   │
   决策门 G3: Phase 0 显示"后段 30% 代次零产出"才实施；否则关闭并登记原因
```

- **G1**：Phase 0 报告给出，决定 P0-1 是否按原收益预期继续（P0-2 无条件执行）。
- **G2**：A/B 对比开启前后晋升率，作为 P0-1 保留/回滚依据。
- **G3**：Phase 0 数据决定 P1-3 是否实施。
- **G4**：结构簇配额默认开启（含开关回退），以 GAP-I206 正交化闭环运行验证作为质量前提。

---

## 4. Phase 0 — 演化基线数据采集

**目标**：用真实数据确认瓶颈位置，为 P0-1（成功模式收益）与 P3（提前停止收益）提供决策依据。

### 4.1 统计口径

只读分析 `memory/evolution/`（traces/success/、traces/failure/、state.json），产出：

| 指标 | 口径 | 决策用途 |
|:-----|:-----|:-----|
| 晋升率 | promoted / evaluated（近 30 天） | 瓶颈判断 |
| 失败归因分布 | FailurePatternAnalyzer 模式计数 | P0-2 目标维度确认 |
| 演化方法晋升率 | macro / gp / operator / deep / transformer 分列 | P0-1 成功模式维度 |
| 后段代次产出占比 | 后 30% 代次晋升数 / 总晋升数 | P1-3 是否值得做 |
| 无产出连续代数 | 最长连续零晋升代数 | P1-3 阈值设定 |

### 4.2 交付（✅ 已完成 2026-08-11）

- 统计脚本：`scripts/evolution_baseline_report.py`（只读，不改生产数据）
- 报告：`docs/harness/plans/26-phase0-baseline-report.md`
- **决策门 G1**：晋升率 0.00% < 5% → **推进 P0-1/P0-2**。归因修正：瓶颈主要在**审计链通过率**（72.4% 审计未通过 + 16.1% 鲁棒性，93.7% 失败在 gen=0 种子/候选评估），P0-1/P0-2 收益上限受审计门槛约束，实施前建议先核对审计子项分布。
- **决策门 G3**：后 30% 代次失败占比 0.1% → **P1-3 维持关闭**（演化主体未深入，后段空转非当前矛盾）。
- **审计子项排查 + 误杀修复（附属 GAP-079）**：审计子项分布确认 oos_consistency 单点主导（99.4%），其中 89.8% 走航 0 窗口属"无法验证"被误杀 → 独立 GAP-079 修复 `_run_factor_audit` 回退路径（详见 26-phase0-audit-breakdown.md）；修复后待真实演化 run 验证晋升率变化。

---

## 5. Phase 1.1 — P0-2 父代失败归因定向修复（最高优先级）

### 5.1 目标

子代演化时继承父因子最近失败归因，LLM 生成/变异时定向修复，避免重复踩坑。

### 5.2 契约（契约先行）

```python
# experience_chain.py 新增接口
def read_failures_by_parent(
    self, parent_id: str, limit: int = 5,
) -> list[ExperienceTrace]:
    """按 parent_id 过滤最近失败轨迹（时间倒序）。

    Args:
        parent_id: 父因子 factor_id
        limit: 返回条数上限（默认 5）

    Returns:
        按 recorded_at 倒序的失败轨迹列表；无记录返回 []。
    """

# 失败归因上下文（注入 LLM 的结构）
@dataclass
class ParentFailureContext:
    parent_id: str
    failure_reasons: list[str]      # 最近失败原因（raw）
    patterns: list[str]             # FailurePatternAnalyzer 聚类模式（如 "换手率过高"）
    latest_failed_at: Optional[str] # 最近失败时间
```

### 5.3 数据流

```
EvolutionLoop._evolve_one(parent, ...)
  └─ experience_chain.read_failures_by_parent(parent_id)  → ParentFailureContext
       └─ MacroEvolver.evolve(..., parent_failure_ctx=ctx)  → _build_prompt 注入
       └─ (Phase 1.2 可选) 生成回调 soft 偏向
```

### 5.4 Prompt 注入（macro_evolution.py `_build_prompt`）

```
父因子最近失败归因: [IC 过低 / 换手率过高 / 多重检验未通过 / ...]
定向修复要求: 针对上述失败维度调整因子逻辑，避免重复该问题。
（失败归因为参考信息；逻辑改动须保持可解释性。）
```

- 无失败记录时省略该段落（不改变现有行为，兼容 Mock 路径）。
- 失败归因**仅作参考**，不硬性约束，评估链兜底。

### 5.5 涉及文件

- `fts/factor_engine/experience_chain.py`（新增接口）
- `fts/factor_engine/macro_evolution.py`（prompt 注入 + 参数扩展）
- `fts/factor_engine/evolution_loop.py`（_evolve_one 传递失败上下文）
- 测试：`tests/factor_engine/test_experience_chain.py`、`tests/factor_engine/test_macro_evolution.py`

### 5.6 测试

- `read_failures_by_parent`：按 parent 过滤 / 时间倒序 / 无记录返回 [] / limit 生效
- Prompt：包含失败归因段落 / 无归因时省略 / economic_logic 不因注入失效
- 回归：`pytest tests/factor_engine/test_macro_evolution.py tests/factor_engine/test_experience_chain.py -v`

### 5.6.1 实施记录（2026-08-11）

- ✅ `experience_chain.py`：新增 `ParentFailureContext` dataclass + `read_failures_by_parent(parent_id, limit=5)`（复用 `_read_dir` mtime 倒序）
- ✅ `macro_evolution.py`：`evolve(..., parent_failure_ctx=None)` + `_build_prompt` 注入段落（`_format_parent_failure_guidance`，无 ctx 返回空串不改变现有行为）
- ✅ `evolution_loop.py`：新增 `_build_parent_failure_ctx(parent)`（读取失败轨迹聚合去重原因，无记录返回 None），`_evolve_one` 三处 macro 调用点透传
- ✅ 测试 `tests/factor_engine/test_failure_guidance.py` 9 用例全绿；ruff check 通过
- ✅ 同步 01（晋升链失败归因注入）/06（+9 用例）/07-operations 记录

### 5.6.2 验证结果（2026-08-11）

| 验证项 | 结果 |
|:-------|:-----|
| 新测试 `test_failure_guidance.py` | **9/9 passed**（红→绿：`read_failures_by_parent` 4 + prompt 注入 3 + `_evolve_one` 传递 2） |
| 既有套件回归（experience_chain + macro_evolution） | **58 passed**（无行为回归；无 ctx 时 prompt 与现状逐字节一致） |
| `_evolve_one` 快速分派用例（method_hint macro/operator/gp） | **3 passed**（macro 分支含新 ctx 接线，operator/gp 分支不受影响） |
| ruff check | 通过（清理 2 处未使用 import） |
| 文档一致性 | **13/13 通过** |
| test_evolution_loop 全量 | 历史已知 20+ min 慢套件（项目先例：另行全量跑）；中断前跑到 40% 零失败 |

**结论**：P0-2 定向修复落地，prompt 注入与传递链路验证通过；无 ctx 路径保持既有行为（向后兼容）。

### 5.7 验证方式

单测全绿 + 一次小规模演化 run（max-generation 5）冒烟，确认 prompt 生效、无异常。

### 5.8 风险

低。信息已存在（traces 已有 parent_id + failure_reasons），仅传递 + prompt 变化。

---

## 6. Phase 1.2 — P0-1 成功模式定向演化

### 6.1 目标

注入近期成功模式统计，让 LLM / 生成回调在成功方向上 **soft 偏向**，提升命中率。

### 6.2 契约

```python
@dataclass
class SuccessPatternReport:
    window_days: int                    # 滚动窗口（默认 14）
    decay: float                        # 时间衰减因子（默认 0.9）
    by_method: dict[str, PromotionStat] # 各演化方法晋升率
    top_operators: list[str]            # 高频成功算子（限 top 5）
    top_window_bins: list[str]          # 高频成功窗口区间
    sample_count: int                   # 统计样本数（< min_sample 返回空报告）

@dataclass
class PromotionStat:
    promoted: int
    evaluated: int
    rate: float                         # promoted / evaluated

@dataclass
class SuccessPatternConfig:
    enabled: bool = True                # 总开关（FTSConfig.evolution_success_pattern_enabled）
    window_days: int = 14
    decay: float = 0.9
    min_sample: int = 10                # 样本 < 阈值 → 空报告（不注入）
    max_operators: int = 5
```

### 6.3 模块

- 新增 `fts/factor_engine/success_pattern.py`：
  - `analyze_success_patterns(chain: ExperienceChain, config) -> SuccessPatternReport`：
    从成功轨迹（source=macro/gp/operator/deep 各路径 + mutation_summary）聚合，滚动窗口 + 时间衰减。
  - 统计失败（数据缺失/解析异常）→ 返回空报告（降级，不阻断演化）。
- 集成：
  - `MacroEvolver._build_prompt` 新增"近期成功模式"段落（标注**参考非硬性约束**）。
  - `EvolutionLoop._evolve_one` 每次演化前构造报告（进程内缓存，避免重复读取）。
  - v2 可选项：`_batch_generate_one` 方法选择按近 14 天晋升率加权（保留现有轮换为基线）。

### 6.4 优化维度选择与防过拟合控制（关键）

**注入维度（仅结构信息，明确排除 family）**：

| 维度 | 是否注入 | 理由 |
|:-----|:-----|:-----|
| 演化方法（macro/gp/operator/deep） | ✅ | 结构信息，直接反映搜索空间有效性 |
| 算子组合（top_operators） | ✅ | 结构信息，反映有效逻辑原语 |
| 窗口参数区间（top_window_bins） | ✅ | 结构信息，反映有效时间尺度 |
| **family（家族）** | ❌ 排除 | family 是**知识注入时的来源/主题标签**（wq101/gtja/momentum/term_structure…），非正交结构维度：同家族因子可高相关，跨家族因子也可高相关。真实结构关系由**信号相关性**刻画（factor_clustering + 正交化已承担）。对标签维度做偏向无信息量，且引入标签噪声 |

**防过拟合控制**：

1. **soft 偏向**：成功模式仅注入 prompt 作参考，不硬编码采样概率。
2. **时间衰减**：近期权重 > 远期，避免放大历史 regime 特征。
3. **滚动窗口**：默认 14 天，窗口外模式不参与。
4. **配置开关**：`evolution_success_pattern_enabled` 默认 true，可即时关闭。
5. **样本下限**：`min_sample=10`，样本不足不注入。

### 6.5 涉及文件

- 新增 `fts/factor_engine/success_pattern.py`
- `fts/factor_engine/macro_evolution.py`、`fts/factor_engine/evolution_loop.py`
- `fts/config/settings.py`（FTSConfig 新字段）
- 测试：`tests/factor_engine/test_success_pattern.py`

### 6.6 测试

- 统计正确性：窗口截断 / 时间衰减权重 / 晋升率计算 / 空数据返回空报告 / 降级不抛异常
- Prompt：注入成功模式段落 / 空报告不注入 / 开关关闭不注入
- 回归：受影响模块定向回归

### 6.7 验证方式

开启前后各 1 周运行，对比晋升率（Phase 0 同口径）。偏差 > ±1pp 时复核统计口径。

### 6.8 风险

中（regime 过拟合）。由 6.4 维度排除（family 不注入）+ 五重控制 + 评估链兜底约束。

### 6.8.1 实施记录（2026-08-11）

- ✅ 新建 `fts/factor_engine/success_pattern.py`：`SuccessPatternConfig` / `PromotionStat` / `SuccessPatternReport` / `analyze_success_patterns`（滚动窗口 14 天 + 时间衰减 decay**days_ago + by_method 晋升率 + 算子/窗口分箱提取，样本 < min_sample 空报告，坏数据降级）+ `format_report_for_llm`
- ✅ `FTSConfig` 新增 `evolution_success_pattern_enabled` / `success_pattern_window_days` / `success_pattern_min_sample`（env `FTS_SUCCESS_PATTERN_*`）
- ✅ `macro_evolution.py`：`evolve(..., success_pattern=None)` + `_build_prompt` 注入"近期成功模式（参考，非硬性约束）"段落（空报告不注入）
- ✅ `evolution_loop.py`：`_build_success_pattern_report()`（进程内缓存）+ 三处 macro 调用点透传
- ✅ 测试 `tests/factor_engine/test_success_pattern.py` 14 用例全绿；ruff check 通过
- ✅ 同步 01（晋升链 Phase 1.2）/03（+3 配置项）/06（+14 用例）/07-operations 记录

### 6.8.2 验证结果（2026-08-11）

| 验证项 | 结果 |
|:-------|:-----|
| 新测试 `test_success_pattern.py` | **14/14 passed**（红→绿：聚合 8 + prompt 注入 4 + `_evolve_one` 传递与缓存 2） |
| 既有套件回归（experience_chain + macro_evolution） | **63 passed**（无行为回归；无 report 时 prompt 不含成功模式段落） |
| GAP-080 专项（test_shap_optimization 7 + test_shap_analyzer 14） | **21 passed**（SHAP 降频接线 + 默认断言同步） |
| ruff check | 通过 |
| test_evolution_loop 全量 | 历史已知 20+ min 慢套件（另行全量跑） |

**结论**：P0-1 soft 偏向落地，成功模式仅注入 prompt 作参考（不硬编码采样概率）；family 维度按 §6.4 明确排除；样本不足/无数据不注入（防过拟合）。

---

## 7. Phase 2 — P1-2 结构化实验日志

### 7.1 目标

每次 L2 run 导出统一 `data/experiments-{run_id}.json`（对齐 autoresearch experiments 结构），支撑事后元分析（哪种演化方法/参数模式最有效）。

### 7.2 契约

```python
# experiments JSON schema（写入 01-architecture.md）
{
  "run_id": "run_xxxx_yyyy",
  "trace_id": "...",
  "market": "futures | stock",
  "started_at": "...",
  "generations_completed": int,
  "rounds": [
    {
      "generation": int,
      "parent_id": str,
      "variants": [
        {
          "candidate_id": str,
          "method": "macro_evolution | gp_evolution | operator_evolution | deep_evolution",
          "summary": str,
          "scores": {
            "ic": float, "icir": float, "sharpe": float,
            "max_drawdown": float, "turnover": float, "monotonicity": bool,
            "quality_grade": "A|B|C"
          },
          "outcome": "prefilter_rejected | verifier_failed | audit_failed | promoted | retired"
        }
      ],
      "promoted_count": int
    }
  ],
  "summary": {
    "total_evaluated": int, "total_promoted": int,
    "promote_rate": float, "by_method": {...}
  }
}
```

### 7.3 模块

- 新增 `fts/factor_engine/experiment_log.py`：
  - `ExperimentLogWriter`：聚合 run 内全部候选（含预筛拦截/失败/晋升）→ 导出 JSON。
  - 幂等：run_id 唯一，重复导出覆盖同 run。
  - Pydantic 契约校验后再落盘（非法 schema 记录 warning，不阻断 run）。
- 集成：`EvolutionLoop.run()` finally 块调用 writer（与 `_run_periodic_factor_review` 并列）。

### 7.4 涉及文件

- 新增 `fts/factor_engine/experiment_log.py`
- `fts/factor_engine/evolution_loop.py`
- `docs/harness/01-architecture.md`（schema 定义）
- 测试：`tests/factor_engine/test_experiment_log.py`

### 7.5 测试

- schema 校验（合法/非法数据）
- 聚合正确性：预筛拦截/失败/晋升三类候选均收录 / by_method 汇总 / 幂等覆盖
- 降级：导出失败记录 warning，不阻断 run

### 7.6 验证方式

一次 run 后人工检查 JSON 结构与 `verify_doc_consistency.py` 全绿。

### 7.7 风险

低。只读聚合 + 非阻塞写入。

### 7.7.1 实施记录（2026-08-11）

- ✅ 新建 `fts/factor_engine/experiment_log.py`：`ALLOWED_OUTCOMES`（prefilter_rejected/verifier_failed/audit_failed/promoted/retired）+ `extract_scores`（ic/icir/sharpe/max_drawdown/monotonicity + turnover←turnover_monthly）+ `ExperimentLogWriter`（按 generation+parent_id 分组 rounds + summary（total_evaluated/total_promoted/promote_rate/by_method）+ 轻量契约校验非法 warning 跳过不阻断 + 幂等覆盖同 run_id）
- ✅ `evolution_loop.py` 集成：`__init__` 新增 `experiment_log_dir` 参数（默认 "data"）+ `_experiment_variants` 缓冲；新增 `_record_experiment_variant`（quality_grade 合并进 scores）+ `_export_experiment_log`（finally 块调用，导出失败降级 console warning）；埋点覆盖——单因子路径运行时校验失败（verifier_failed）/快速预筛失败（prefilter_rejected）+ `_process_candidate` 全 10 分支（微观演化失败 verifier_failed / 数据质量严重告警 audit_failed / 质检过滤 C 级淘汰 audit_failed+grade / 审计未通过 audit_failed / 消融失败 audit_failed / 因果失败 audit_failed / 鲁棒性失败 audit_failed / 名称重复跳过 audit_failed+grade / Verifier 未通过 verifier_failed / 晋升成功 promoted+grade）+ batch 全失败回退逐候选 prefilter_rejected
- ✅ 测试 `tests/factor_engine/test_experiment_log.py` 9 用例全绿（红阶段 9 failed → 绿阶段 9 passed）；ruff check 通过
- ✅ 同步 01（实验日志 schema 与晋升链 Phase 2）/06（+1 测试文件 9 用例）/07-operations 记录

### 7.7.2 验证结果（2026-08-11）

| 验证项 | 结果 |
|:-------|:-----|
| 新测试 `test_experiment_log.py` | **9/9 passed**（红→绿：writer schema 4 + extract_scores 2 + EvolutionLoop 集成 3） |
| `test_run_finally_exports` 集成 | **passed**（run() finally 自动导出落盘，验证 finally 路径） |
| ruff check（evolution_loop + experiment_log） | 通过 |
| 并发会话遗留附加修复 | portfolio_loop.py:3136 IndentationError（缺 total_count 赋值行）+ 2 处既有测试滞后（GAP-077 未同步 / minimal_loop 真实库污染非幂等），修复后 test_portfolio_loop duckdb 9 + TestGapF16PromoteToElite 11 + audit/batch/prefilter 子集全绿 |
| 文档一致性 | **13/13 通过** |
| test_evolution_loop 全量 | 历史已知 20+ min 慢套件（另行全量跑） |

**结论**：P1-2 落地，每次 L2 run 自动导出 `data/experiments-{run_id}.json`（非阻塞降级）；预筛/Verifier/审计/晋升全结局可事后元分析（哪种演化方法/参数模式最有效）。

---

## 8. Phase 3 — P1-3 提前达标停止（条件性）

**前置**：仅当 Phase 0 数据显示"后段 30% 代次零产出"时才实施；否则关闭本阶段并登记原因。

### 8.1 目标

连续 K 代零晋升时提前结束 run，节约 token 预算。

### 8.2 契约

```python
# contracts.py BudgetConfig 扩展（✅ 已实施，§8.8）
evolution_stop_enabled: bool = False                      # 默认关闭（保守），FTS_EVOLUTION_STOP_ENABLED
evolution_stop_consecutive_empty_generations: int = 5     # 连续 K 代零晋升 → 提前结束（FTS_EVOLUTION_STOP_EMPTY_GENS）
```

- 仅"连续零晋升"触发，**不设**累计达标停止（避免截断晚熟因子）。

### 8.3 模块

- `EvolutionLoop.run()` 循环内检测（置于现有熔断检查旁）：
  每代结束后若 `promoted == 0` 计数 +1，`> K` 则标记 `early_stopped` 并正常收尾（写 state + 报告）。
- 状态：`EvolutionRunResult` 新增 `early_stopped: bool` 字段与原因。

### 8.4 涉及文件

- `fts/factor_engine/evolution_loop.py`
- `fts/factor_engine/contracts.py`（BudgetConfig + EvolutionRunResult）
- `fts/config/settings.py`
- 测试：`tests/factor_engine/test_evolution_loop.py`

### 8.5 测试

- 连续零晋升 K 代触发 / 中断后恢复不触发 / 开关关闭不触发 / early_stopped 标记正确

### 8.6 验证方式

对比开启前后：token 消耗↓ 且 晋升总数不下降（下降即回滚开关）。

### 8.7 风险

中（晚熟因子截断）。保守阈值 + 默认关闭 + 开关回滚。

### 8.7.1 验证记录（2026-08-11，真实 L2 run）

**验证方式**：修复链（GAP-079/080 + Phase 1.1/1.2/1.3 + Phase 2）落地后，跑一次真实期货横截面 L2 run 采集代次晋升分布（Phase 2 实验日志自动落盘）。

**验证 run**：`run_fb3de6c3_20260811T174523`（期货动态池 25 品种 × 700 日，15 代，LLM=OpenAIClient/deepseek，42,015 token，status=completed）

| 验证项 | 结果 |
|:-------|:-----|
| 代次晋升分布 | 15 代每代 1 候选：**晋升 0**；结局 = verifier_failed 7 / prefilter_rejected 6 / audit_failed 2 |
| 连续零晋升代数 | **15 代（全程）**，远超契约阈值 K=5 |
| 后段 30% 代次产出（gen 11-15） | **0 晋升**（后段空转成立） |
| 历史佐证 | Phase 0 `run_16899e88`（修复前）28 代 0 晋升；两次 run 均全程零晋升 |
| token 节约估算 | 本次 42,015 token，若 K=5 提前停止约节约 ~2/3（macro LLM 调用 + 评估时间） |
| 修复后行为对比 | 预筛拦截占比 40%（6/15），低于修复前 96%——候选更深走完链路，verifier/audit 拦截为真实质量拦截非误杀 |

**结论**：P1-3 前置条件（"后段 30% 代次零产出"）**成立**——修复后真实 run 与历史 run 均显示连续 10+ 代零晋升是常态，提前达标停止具备节约价值。**已按保守方案实施（见 §8.8）**，遵守：默认关闭（enabled=False）+ 保守阈值（K=5）+ 实验日志持续监控（若开启后出现"提前停止后段内晋升"即回滚开关）。

### 8.8 实施记录（2026-08-11，按保守方案）

**决策**：前置条件 §8.7.1 成立 → 实施，但保守默认关闭（enabled=False）+ 阈值 K=5 + 开关可回滚。

#### 8.8.1 实施记录

- ✅ 契约先行：`contracts.py` `BudgetConfig` 新增 `evolution_stop_enabled: bool` + `evolution_stop_consecutive_empty_generations: int`；`EvolutionRunResult` 新增 `early_stopped: bool = False` + `early_stop_reason: Optional[str] = None`（`to_dict()` 含两字段）
- ✅ 配置：`settings.py` `FTSConfig` 新增 `evolution_stop_enabled`（`FTS_EVOLUTION_STOP_ENABLED`，默认 "0" 关闭）+ `evolution_stop_consecutive_empty_generations`（`FTS_EVOLUTION_STOP_EMPTY_GENS`，默认 5）；`evolution_loop.__init__` 读取 FTSConfig 为兜底，`budget` 字典字段优先覆盖
- ✅ `evolution_loop.py` 集成：新增 `_maybe_early_stop(state)`（置于 `_check_circuit_breaker` 旁）——基于 `state.total_factors_promoted` 差值判断每代是否晋升，连续 K 代零晋升置 `_early_stop_reason` 返回 True；`run()` 在 4 个 continue 分支（batch 生成后 / `_evolve_one` 返回 None / 运行时校验失败 / 预筛失败）与每代末尾 `_process_candidate` 后均加检查点；提前停止时写 `state["early_stopped"]` + reason，`mark_completed` 正常收尾，返回 `generations_completed` 正确计数 + `early_stopped` 标记
- ✅ 预算/熔断/停止三机制并列：`_check_circuit_breaker`（token/低 IC/失败率，熔断中断）+ `_maybe_early_stop`（连续零晋升，正常收尾），互不干扰
- ✅ 测试 `tests/factor_engine/test_evolution_stop.py` 8 用例全绿（红阶段 4 failed → 绿阶段 8 passed）
- ✅ ruff check 通过；模块/集成定向回归 91 passed（evolution_stop 8 + experiment_log 9 + evolution_loop 快速子集 + config 63，未跑全量）
- ✅ 同步 01（晋升链图加提前停止行）/03（+2 配置项行）/06（+test_evolution_stop.py 8 用例行）/07-operations 备案

#### 8.8.2 验证结果（2026-08-11）

| 验证项 | 结果 |
|:-------|:-----|
| `test_evolution_stop.py`（新） | **8/8 passed**（TestMaybeEarlyStop 3 + TestRunEarlyStop 3 + TestEvolutionStopConfig 2） |
| 红→绿 TDD | 红阶段 4 failed 确认（早停触发/归零/关闭恒 False/run 集成路径）→ 绿阶段 8 passed |
| `_maybe_early_stop` 边界 | K=3 连续零晋升触发 + reason 正确 / 晋升中断计数归零 / 关闭恒 False |
| `run()` 集成 | 连续 K 代提前结束（generations=3、early_stopped=True、status=completed）/ 关闭跑满 / 提前停止后实验日志仍导出 |
| 配置 | 默认关闭 K=5 / env（`FTS_EVOLUTION_STOP_ENABLED`、`FTS_EVOLUTION_STOP_EMPTY_GENS`）覆盖生效 |
| ruff check | 通过 |
| 模块/集成回归 | **91 passed**（未跑全量，按用户要求仅模块/集成） |
| 文档一致性 | verify_doc_consistency 通过 |

**结论**：P1-3 落地完成。默认关闭不改变现有行为（回测/实盘零影响）；开启后连续 K 代零晋升提前正常收尾并导出 `early_stopped` 标记，配合 Phase 2 实验日志可事后验证"提前停止后段内晋升"风险，触发即回滚开关。

---

## 9. 暂缓项与理由

| 项 | 理由 | 触发条件（何时可重开） |
|:-----|:-----|:-----|
| **P1-1** B 级定向再演化 | B 级因子已过全评估链且正贡献，再演化一轮 token 成本与收益不匹配 | 实验日志显示 B 级因子 3 个月后衰减率显著高于 A 级 |
| **P2-1** L2 定向跨因子杂交 | L3 Elastic Net 已实现组合层合成；L2 杂交搜索空间设计难度高、收益不确定 | 实验日志显示"强因子对"具备可杂交的稳定互补结构 |

### 9.1 Phase 1.3 实施设计：结构性聚类配额替代 max_per_family（✅ 已实施，GAP-077）

**实施完成（2026-08-11）**：`_count_cluster_members` + `_scan_elite_correlations`（从 `_check_elite_correlation` 提取）+ `_promote_to_elite` 配额替换已落地；FTSConfig 3 新配置项；test_structure_cluster_quota.py 11 用例 + test_l2_elite_redundancy 11 回归 + test_evolution_loop 快速子集 44 全绿；ruff 通过；01/03/06/07/08 已同步。GAP 编号 077（076 已被信号管道标准化占用）。

**背景（设计决策）**：family 是知识注入时的来源/主题标签（股票：wq101/gtja/qlib/jq/fundamental…；期货：momentum/term_structure/tinysoft…），**非正交结构维度**——同家族因子可高相关，跨家族因子也可高相关。真实因子结构关系应由**信号相关性**刻画（factor_clustering 已提供 `cluster_by_correlation` + `select_representative_factors`）。`max_per_family` 配额（GAP-F10）是对标签的粗粒度多样性控制，与正交化闭环（GAP-I206）重复且可能误伤高价值来源；GAP-070 对 other/unknown 豁免更留下膨胀漏洞。

**结论**：以**结构簇配额**替代 `max_per_family`——按信号相关性判定"同类"，配额限制每类因子数量；family 字段保留为血缘溯源标签，不再承担多样性控制职责。

#### 9.1.1 机制设计（增量式结构簇配额，最小改动）

在 `_promote_to_elite` 中，将标签配额检查替换为结构簇配额检查：

```
旧: factor_family = factor.get("family"); 按 family 查 DuckDB 计数 ≥ max_per_family → 拒绝
新: cluster_size = _count_cluster_members(factor)  # 与既有 elite |corr| ≥ 阈值 的成员数
    cluster_size ≥ max_per_cluster → 拒绝（family 字段不再参与判断）
```

- **与 GAP-I206 分工**：正交化闭环（单对 |corr| ≥ 0.9 → 正交化/拒绝）管**即时强相关性**；结构簇配额管**结构类长期数量上限**（防同质化膨胀）。两层并行，互不替代。
- **成本控制**：复用 `_check_elite_correlation` 的信号计算循环（同一执行路径），扫描上限沿用 `_l2_elite_corr_max_scan`；新因子信号只算一次。
- **other/unknown 豁免自然消除**：结构与标签无关，无需豁免（修复 GAP-070 漏洞）。
- **DuckDB 不迁移**：配额判断运行期由信号相关性计算，不依赖 `factor_catalog.family`；family 字段仅作血缘溯源。

#### 9.1.2 契约

```python
# contracts.py BudgetConfig 扩展
"structure_cluster_quota": {
    "enabled": True,              # 总开关（关闭 → 回退 max_per_family 旧逻辑，平滑迁移）
    "max_per_cluster": 15,        # 每结构簇最大因子数（对齐原 max_per_family 默认 15）
    "corr_threshold": 0.85,       # 判定"同类"的相关性阈值（略宽于 GAP-I206 的 0.9：
                                  #   0.9 强拦截 vs 0.85 数量配额，语义不同）
    "max_scan": 50,               # 信号计算扫描上限（复用 _l2_elite_corr_max_scan 语义）
}

# evolution_loop.py 新增方法
def _count_cluster_members(self, factor: FactorProgram) -> int:
    """统计与既有 elite 信号相关性 ≥ corr_threshold 的成员数（结构簇规模代理）。

    复用 _check_elite_correlation 的信号计算与扫描逻辑；信号异常/NaN 兜底跳过；
    无既有 elite 返回 0（放行）。
    """
```

#### 9.1.3 涉及文件

- `fts/factor_engine/evolution_loop.py`（配额逻辑替换 + `_count_cluster_members`）
- `fts/factor_engine/contracts.py`（BudgetConfig 扩展）
- `fts/config/settings.py`（FTSConfig 新字段映射）
- 测试：`tests/factor_engine/test_evolution_loop.py`、`tests/factor_engine/test_l2_elite_redundancy.py`

#### 9.1.4 测试

- `_count_cluster_members`：阈值判定 / 扫描上限 / 信号 NaN 兜底 / 无 elite 返回 0
- 配额触发：同类成员 ≥ max_per_cluster → 拒绝（含 other/unknown 因子不再豁免）
- 配额未触发：同类成员 < max_per_cluster → 放行
- 开关回退：`enabled=False` → 完全复现 max_per_family 旧行为（回归兼容）
- 与 GAP-I206 共存：单对 |corr| ≥ 0.9 仍走正交化闭环
- 全量回归：`pytest tests/ -v`

#### 9.1.5 验证方式

开启前后精英池对比：结构簇分布、家族分布、IC 均值、max_per_cluster 拒绝计数；`verify_doc_consistency.py` 全绿。

#### 9.1.6 决策门 G4 与风险

- **G4**：默认开启（含开关回退），以 GAP-I206 正交化闭环稳定运行作为质量前提。
- 风险：中（核心晋升逻辑改动）。控制：配置开关回退、单测全覆盖、全量回归、不动 DuckDB schema。

---

## 10. HARNESS 文档更新清单（每阶段完成后执行）

| # | 文档 | 更新内容 |
|:--|:-----|:-----|
| 1 | `01-architecture.md` | 演化链路数据流：失败归因传递、成功模式统计、experiments 日志 schema |
| 2 | `02-lifecycle.md` | 阶段产物补充 |
| 3 | `03-configuration.md` | 新配置项（success_pattern、stop_conditions） |
| 4 | `04-resilience.md` | 降级路径（成功模式统计失败 / 实验日志写入失败不阻断） |
| 5 | `05-observability.md` | 新指标/日志（模式统计、early_stopped 事件） |
| 6 | `06-testing.md` | 测试文件与用例数 |
| 7 | `07-operations.md` | 版本历史追加 |
| 8 | `08-gap-analysis.md` | GAP 登记/关闭 |
| 9 | `09-advancement-plan.md` | 里程碑 |
| 10 | `production_plan.md` | 流程同步 |
| 11 | `CLAUDE.md` | 职责变更（如有） |
| 12 | `README.md` | 工程指标同步 |

## 11. 版本与回归管理

- **版本纪律**：不手改 pyproject.toml。Phase 1.1 + 全量回归通过后，经 `scripts/bump_version.py` bump（minor，`--message` 必填）；同日仅一次，并发追加当日条目。
- **回归**：每阶段 `pytest tests/ -v` 全量回归 + `scripts/verify_doc_consistency.py` 校验。
- **代码规范**：类型注解完整、Pydantic 配置管理、无硬编码绝对路径（`Path(__file__)`/相对路径）、NaN/Inf 兜底、trace_id 贯穿、禁用原生 print（日志分级）。

## 12. 风险与回滚

| 风险 | 控制手段 |
|:-----|:-----|
| 成功模式 regime 过拟合 | 滚动窗口 + 时间衰减 + soft 偏向 + min_sample + 配置开关 |
| LLM 过度修复引入新问题 | 失败归因仅作参考 + 完整评估链兜底 |
| 提前停止截断晚熟因子 | 仅"连续零晋升"触发 + 默认关闭 + 开关回滚 |
| 影响实盘/已晋升因子 | 角色边界：只改 L2 演化层，不触及信号管道与 elite 存储 |
| 版本膨胀 | 发布里程碑制 + 同日单次 bump |

## 13. 退出标准

- 每阶段：新用例 + 既有受影响模块回归全绿；07 版本历史记录；GAP 关闭登记；本计划状态更新
- 终期：全量回归通过、verify_doc_consistency.py 全绿、实验日志可用、P0-1 开启前后晋升率对比报告归档
