# FTS 演化优化计划

> 版本: v2.104.0+66
> 创建: 2026-08-03
> 最后更新: 2026-08-05
> 状态: 执行中 — Phase A

---

## 1. 背景与动机

当前 L2 Evolution Loop 存在以下问题：

| 问题 | 现象 | 根因 |
|------|------|------|
| 熔断过早 | 4 代即熔断（连续 3 代 IC < 0.01） | 父因子选择无探索/利用平衡，轮询效率低 |
| LLM 生成质量不稳定 | 代际 IC 波动大，失败模式重复 | 经验链仅做文本列表注入，缺少结构化失败模式分析 |
| 搜索空间单一 | 仅 LLM 代码变异一种探索方式 | 缺少其他搜索策略互补 |

---

## 2. 实施路线图

```
Phase A（立即，v1.9.0）          Phase B（短期，v1.10.0）          Phase C（中期，v2.x）
├── A.1 UCT 树搜索父因子选择     ├── B.1 多岛进化                   ├── C.1 GP/gplearn 补充搜索
├── A.2 经验链失败模式聚类       ├── B.2 多 Agent 协作              └── C.2 演化策略可插拔化
└── A.3 文档 + 测试更新          └── B.3 文档 + 测试更新
```

---

## 3. Phase A — 立即实施（v1.9.0）

### A.1 UCT 树搜索父因子选择

**目标**: 替换当前轮询选择，引入 UCT 平衡探索与利用。

**当前代码**（`evolution_loop.py` L203）:
```python
parent = parent_seeds[(generation - 1) % len(parent_seeds)]
```

**改进方案**:
- 为每个父因子维护 `visits`（被选中次数）和 `total_reward`（累计奖励）
- 奖励 = 子因子 IC（成功）/ 0（失败）
- UCT 公式: `UCB = avg_reward + c * sqrt(ln(total_visits) / visits)`
- 探索常数 c = 1.0（可调），平衡探索未知父因子 vs 利用已知好父因子

**改动文件**:
- `fts/factor_engine/evolution_loop.py` — 新增 `_select_parent_uct()` 方法，替换 L203 的轮询

**验证标准**:
- 父因子选择不再固定轮询，高 IC 父因子获得更多进化机会
- 新父因子（visits=0）在 UCT 下自动获得探索机会

### A.2 经验链失败模式聚类增强

**目标**: 将失败轨迹做聚类分析，在 LLM prompt 中结构化注入常见失败模式。

**当前代码**（`macro_evolution.py` `_build_prompt`）:
- 仅列出最近 5 条成功/失败轨迹的文本

**改进方案**:
- 新增 `FailurePatternAnalyzer` 在 `experience_chain.py` 中
- 对失败轨迹的 failure_reasons 做关键词聚类
- 识别高发失败模式（如"IC 过低"、"信号零方差"、"夏普不达标"）
- 在 LLM prompt 中注入结构化失败模式统计（"最近 10 次失败中，6 次因为 IC 过低，3 次因为信号零方差"）

**改动文件**:
- `fts/factor_engine/experience_chain.py` — 新增 `FailurePatternAnalyzer` 类
- `fts/factor_engine/macro_evolution.py` — 修改 `_build_prompt` 使用聚类结果

**验证标准**:
- LLM prompt 包含失败模式统计而非仅列轨迹
- 失败模式聚集时 LLM 能针对性调整策略

### A.3 文档与测试更新

**目标**: 同步所有 Harness 文档，补充测试。

**改动文件**:
- `docs/harness/01-architecture.md` — 更新演化架构图，标记 UCT 选择 + 失败模式分析
- `docs/harness/06-testing.md` — 更新测试用例数
- `docs/harness/07-operations.md` — 追加版本历史 v1.9.0
- `docs/harness/08-gap-analysis.md` — 登记新差距（如有）
- `docs/harness/09-advancement-plan.md` — 追加 v1.9.0 里程碑
- `pyproject.toml` — 版本号 bump 至 1.9.0
- 新增测试: `tests/factor_engine/test_uct_selection.py`、`tests/factor_engine/test_failure_pattern.py`

---

## 4. Phase B — 短期（v1.10.0）

### B.1 多岛进化

**目标**: 运行 2-3 个独立进化岛，定期迁移 top 因子，增加多样性。

**方案**:
- `MultiIslandEvolutionLoop` 管理 N 个独立 `EvolutionLoop` 实例
- 每 `migration_interval` 代（默认 7），各岛选出 top-3 因子互迁
- 一个岛熔断不影响其他岛，最终合并所有 elite 因子

### B.2 多 Agent 协作

**目标**: 将单个 LLM 调用拆分为 IdeaAgent → CodeAgent → ReviewAgent 协作。

**方案**:
- IdeaAgent: 分析父因子 + 经验链 → 生成变异方向
- CodeAgent: 将变异方向转化为可执行代码
- ReviewAgent: 审查代码质量 + 经济逻辑合理性
- 各 Agent 可独立调用（减少单次 token 消耗），也可链式串联

---

## 5. Phase C — 中期（v2.x）

### C.1 GP/gplearn 补充搜索

**目标**: 引入遗传规划作为 LLM 演化之外的补充搜索方法。

**方案**:
- 新增 `gp_evolution.py`，基于 gplearn 在表达式空间搜索
- 表达式树 → FactorProgram 代码转换器
- 进入现有评估链，与 LLM 因子混合竞争

### C.2 演化策略可插拔化

**目标**: 将演化方法抽象为 `EvolutionStrategy` 接口，支持运行时切换。

**方案**:
- 定义 `EvolutionStrategy` 协议
- 现有 `MacroEvolver` → `LLMEvolutionStrategy`
- 新增 `GPEvolutionStrategy`、`HybridEvolutionStrategy`
- CLI 支持 `--strategy llm|gp|hybrid`

---

## 6. 验收标准

| 阶段 | 标准 |
|------|------|
| Phase A | UCT 选择生效，失败模式分析注入 prompt，所有测试全绿 |
| Phase B | 多岛并发运行，多 Agent 协作产出因子，测试全绿 |
| Phase C | GP 因子与 LLM 因子混合竞争，策略可切换，测试全绿 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件定义 FTS 演化优化路线图（Phase A/B/C），对应 `evolution_loop.py`、`macro_evolution.py`、`experience_chain.py` 的改进计划 |
| 可验证断言 | Phase A 实施中，A.1 UCT 选择 + A.2 失败模式聚类 |
| 检验方式 | 检查 `evolution_loop.py` 是否存在 `_select_parent_uct` 方法，`macro_evolution.py` 是否使用失败模式聚类 |