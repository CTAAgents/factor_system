# AR 因子演化通道对比实验 — README

> 配套脚本：[ar_factor_experiment.py](./ar_factor_experiment.py)（草稿 v0.1）
> 实验方案来源：《用 AutoResearch 的框架挖因子》§3
> 本文档包含 **结果解读口径**（第 7 节），所有指标字段均对齐
> `fts/factor_engine/experiment_log.py` 实际 schema。

---

## 1. 实验目的

检验因子挖掘中三种"变异器"的产出质量，为生产通道权重配置提供数据依据：

- **A 组（AutoResearch 式）**：macro 通道——LLM 语义变异 + 成功模式/失败归因注入
- **B 组（经典 GA 式）**：operator 通道——UCT 父选择 + 确定性算子变异
- **C 组（FTS 生产混合）**：`idx%4` 轮换 macro/gp/deep/transformer/operator

**核心假设**（可证伪）：
- H1：A 组单因子 IC 更高（变异有方向）
- H2：B 组 elite 池多样性更好（探索更均匀）
- H3：C 组在"晋升率 × 多样性 × OOS 保持率"综合最优

## 2. 实验设计

| | A 组 | B 组 | C 组 |
|:--|:--|:--|:--|
| 变异器 | macro（LLM） | operator（UCT+确定性） | idx%4 轮换 |
| 覆盖点 | `_batch_generate_one` → `method_hint="macro"` | 同左 → `"operator"` | 不覆盖（生产原样） |
| 父池 / 评估链 / 审计 / 预算 / seed | 三组一致 | 三组一致 | 三组一致 |
| 隔离 | `futures_elite_ar_a` + `memory/.../futures_ar_a` | 同左 `_ar_b` | 同左 `_ar_c` |

**唯一变量 = 变异器**。下游准入链（并行粗筛 → 微观演化 → 三级评估 → FactorAuditor 7 项审计 → 晋升 → 实验日志）三组完全复用生产代码。

## 3. 前置条件

- Python 3.11+（项目路径 `C:\Program Files\Python312\python.exe`）
- LLM backend 已配置（A 组 macro 通道依赖，见 `fts.llm.get_llm_client`）
- 数据源可用：DUCKDB_CACHE（`data/fts_history.duckdb`）或 TDX 17709（通达信 TdxW 运行中）
- 无额外依赖（复用 FTS 既有包）

## 4. 用法

```bash
# 全部三组（默认 30 代 × 5 候选/代 = 每组 150 候选）
python scripts/ar_factor_experiment.py --rounds 30 --batch-size 5

# 只跑 A 组小规模冒烟（验证链路）
python scripts/ar_factor_experiment.py --only a --rounds 2 --batch-size 2

# 只打印计划不执行
python scripts/ar_factor_experiment.py --dry-run

# INFO 日志
python scripts/ar_factor_experiment.py -v
```

| 参数 | 默认 | 说明 |
|:--|:--|:--|
| `--rounds` | 30 | 最大演化代数 |
| `--batch-size` | 5 | 每代候选数（预算 = rounds × batch-size） |
| `--seed` | 42 | 固定随机种子（三组一致保证可比） |
| `--only` | 全部 | 只跑 a / b / c 单组 |
| `--dry-run` | off | 只打印计划 |
| `-v` | off | INFO 日志 |

> 完整执行耗时较大（每组含 LLM 与全链审计），建议先 `--only a --rounds 2 --batch-size 2` 冒烟。

## 5. 产物

| 路径 | 内容 |
|:--|:--|
| `data/experiments-ar_{a,b,c}-{run_id}.json` | 各组实验日志（`experiment_log.py` 导出） |
| `reports/futures/{date}/ar_channel_comparison.md` | 三组对比表（晋升率 + by_method） |
| `memory/evolution/futures_ar_{a,b,c}/` | 各组演化状态/审计/失败轨迹 |
| `memory/knowledge/factors/futures_elite_ar_{a,b,c}/` | 各组精英因子快照 |

## 6. 数据流

```
ar_factor_experiment.py
  ├─ _prepare_futures_data(days=700)          # 期货横截面 panel
  ├─ EvolutionLoop(market="futures", 隔离目录)
  │    ├─ _fixed_method_generator (A/B) 或 生产 _batch_generate_one (C)
  │    └─ run(max_generation)
  │         └─ _process_candidate 完整准入链
  │              └─ experiment_log → data/experiments-{run_id}.json
  └─ build_report → ar_channel_comparison.md
```

## 7. 结果解读口径（核心）

实验日志 schema（`experiment_log.py` 实测字段）：

```json
{
  "run_id": "...", "trace_id": "...", "market": "futures",
  "generations_completed": 30,
  "rounds": [{
    "generation": 1, "parent_id": "...",
    "variants": [{
      "candidate_id": "...", "method": "macro_evolution",
      "summary": "...", "outcome": "promoted",
      "scores": {"ic": 0.042, "icir": 0.9, "sharpe": 1.8,
                 "max_drawdown": -0.2, "turnover": 3.5, "monotonicity": 0.6}
    }],
    "promoted_count": 1
  }],
  "summary": {
    "total_evaluated": 150, "total_promoted": 4, "promote_rate": 0.027,
    "by_method": {"macro_evolution": {"evaluated": 40, "promoted": 2, "rate": 0.05}}
  }
}
```

### 7.1 晋升率（直接提取）

| 指标 | 字段路径 | 判定口径 |
|:--|:--|:--|
| 总体晋升率 | `summary.promote_rate` | 三组横向对比；A/B 若显著低于 C，说明该通道"变异质量不足"或"被审计拦截" |
| 分方法晋升率 | `summary.by_method.{method}.rate` | 看 A 组 macro 是否真在"promoted"结局上占优 |
| 结局分布 | 各 `outcome` 计数（prefilter_rejected / verifier_failed / audit_failed / promoted） | **定位瓶颈在哪个环节**：预筛拦截多 = 变异太弱；audit_failed 多 = 过拟合/不鲁棒 |

### 7.2 因子质量（直接提取）

| 指标 | 字段路径 | 判定口径 |
|:--|:--|:--|
| IC / ICIR / Sharpe | `rounds[].variants[].scores.{ic,icir,sharpe}` | 取 **promoted** 因子的中位数（勿取全体——含大量被拦低质候选，均值被拉低） |
| 换手 | `scores.turnover` | 与 IC 组合看"单位换手的信息含量"；A 组（LLM 变异）易继承父因子结构导致高换手，需横向对比 |
| 单调性 | `scores.monotonicity` | ≥0.6 视为有分层能力 |

### 7.3 样本外保持率（需补充提取，脚本当前不直接输出）

experiment_log 内**没有**测试段 IC——OOS 指标在晋升因子通过审计时已**门槛化判定**（walkforward 一致性 / cross_symbol / symbol_holdout），但要量化"保持率"需补充一步：

```bash
# 对三组晋升因子，在测试段（数据窗尾部 20%）单独重跑回测，取 IC
# 复用 fts CLI 批量回测：
python -m fts.cli factor backtest batch --universe futures \
    --elite-dir memory/knowledge/factors/futures_elite_ar_a --days 700
```

**判定口径**：
- `OOS 保持率 = 测试段 IC / 训练段 IC`，≥ 60% 合格
- `盲测正率`：对 `FUTURES_HOLDOUT`（15 个未训练品种）重跑，IC>0 品种占比 ≥ 80%
- 若某组训练段 IC 高但保持率 < 60% → **过拟合**（对应 autoresearch"模拟分高 ≠ 真实有效"）

### 7.4 多样性（需补充提取）

elite 池相关从 DuckDB `factor_correlations` 或信号矩阵计算：

```python
# 从三组 elite 目录加载因子，计算信号两两 Pearson 相关
# 判定口径：elite 池平均 |corr| < 0.6 视为多样性合格
```

**判定口径**：
- 平均相关 < 0.6：多样性合格
- B 组（经典 GA）预期相关最低（H2 验证）；A 组若 > 0.8 → 通道坍缩（历史教训：50 代产出同一 `ts_quantile`）

### 7.5 综合结论判定（验收标准）

| 编号 | 标准 | 判定 |
|:--|:--|:--|
| V1 | 三组各产出 ≥1 个全审计通过因子 | 任一组 0 晋升 → 该通道空转，标记"通道坍缩" |
| V2 | 测试段 IC 保持率 ≥ 60% | 不达标组判"过拟合"，即使训练段 IC 最高也不采用 |
| V3 | 盲测品种 IC 正率 ≥ 80% | 跨标的泛化门槛 |
| V4 | elite 池平均相关 < 0.6 | 多样性门槛 |
| V5 | 三组可复现（固定 seed 重跑一致） | 复现性 |

**结论输出**（写入对比报告）：
1. H1/H2/H3 各自成立与否 + 数据证据
2. 是否建议调整生产 `idx%4` 通道权重（如 macro 占比上调/下调）——**仅作建议，不直接改生产默认行为**

## 8. 风险与限制

| 风险 | 对策 |
|:--|:--|
| 预算小（150 候选）统计功效不足 | 每通道 ≥150 候选；可跑 3 个 seed 取中位数 |
| LLM 变异收敛同质表达 | 正交化门槛硬拦 + V4 多样性监控 |
| 短样本审计误杀（GAP-073/079 已知） | 审计窗口不足标记 skipped 而非 failed（生产已含） |
| 三组隔离目录 + 内部方法覆盖耦合演化循环实现 | 草稿性质：review 后再执行；演化循环重构需同步 |
| 完整执行耗时大 | 先冒烟（`--only a --rounds 2`） |

## 9. 复现与扩展

- **复现**：同一 seed + 同一数据快照，三组结果应一致（V5）
- **扩展**：
  - 增组 D：`method_hint="gp"`（B 组的另一种经典 GA 变体）
  - 增维度：按 `parent_id` 看"哪些父因子最肥沃"（`by_method` 之外的父因子级晋升率）
  - 接入 L3：把三组晋升因子分别做组合，对比组合级夏普（检验"通道质量 → 组合质量"传导）

## 10. 关联文档

- 脚本：`scripts/ar_factor_experiment.py`
- 实验日志 schema：`fts/factor_engine/experiment_log.py`
- 演化循环：`fts/factor_engine/evolution_loop.py`（`_batch_generate_one` / `_evolve_one` / `_process_candidate`）
- 方案文章：《用 AutoResearch 的框架挖因子》（含 §3 实验方案）
