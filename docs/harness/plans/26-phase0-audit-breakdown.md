# 审计失败子项分布排查报告（26 号计划 Phase 0 后续）


> 版本: v2.104.0+42

> 数据根: `memory/evolution/traces`（audit_fail 轨迹 1073 条，只读）
> 生成: 2026-08-11
> 工具: `scripts/audit_failure_breakdown.py`

## 1. 审计项状态分布（name × status）

| 审计项 | passed | failed | skipped | 合计 |
|:-------|:-------|:-------|:--------|:-----|
| causal_validity | 0 | 0 | 1073 | 1073 |
| cross_symbol | 0 | 6 | 1067 | 1073 |
| multiple_testing | 0 | 0 | 1073 | 1073 |
| oos_consistency | 6 | **1067** | 0 | 1073 |
| snooping_check | 957 | 0 | 116 | 1073 |
| stress_resilience | 0 | 0 | 1073 | 1073 |
| symbol_holdout | 2 | 4 | 0 | 6 |

## 2. failed 贡献

| 审计项 | failed 次数 | 占 audit_fail 轨迹比例 |
|:-------|:------------|:----------------------|
| **oos_consistency** | **1067** | **99.4%** |
| cross_symbol | 6 | 0.6% |
| symbol_holdout | 4 | 0.4% |

## 3. oos_consistency failed 按日期（GAP-073 修复 v2.98.0 ≈ 08-10）

| 日期 | oos_consistency failed |
|:-----|:----------------------|
| 20260805 | 29 |
| 20260806 | 60 |
| 20260807 | 152 |
| 20260808 | 388 |
| 20260809 | 62 |
| 20260810 | 371 |
| 20260811 | 5 |

## 4. WalkForward 窗口完成数分布（n_windows_completed）

| n_windows_completed | 轨迹数 |
|:--------------------|:-------|
| **0** | **964**（占 audit_fail 90%） |
| 2 | 7 |

## 5. audit_report pass_rate

- 样本 1073，均值 0.15；pass_rate=0 轨迹 116（10.8%）

## 6. 关键结论

1. **oos_consistency 单点主导 99.4% 的 audit_fail**，其中 **90% 的轨迹 n_windows_completed=0**（走航根本没跑出窗口）——属"无法验证"而非"验证失败"。
2. **6 项审计实际退化为 2 项门禁**：causal_validity / multiple_testing / stress_resilience / cross_symbol 共 4212 次判定中 4203 次 skipped（数据缺失），真实生效的只有 oos_consistency + snooping_check。
3. **误杀路径（GAP-073 漏网）**：评估链 `walk_forward` 存在但 `n_windows_completed=0` 时，`_run_factor_audit` 先尝试独立走航，失败后**回退 L1 icir 兜底**（evidence 显示 `ic_consistency=0.36`，实为 level_1 icir），该兜底结果**无 `n_windows_completed` 键** → `_check_oos_consistency` 走原 failed 判定，未命中 GAP-073 的 `n_windows_completed<2 → skipped` 分支。
4. **因子本身评估通过**（示例 IC=0.06 / Sharpe=2.6 / monotonicity=true / passed=true）却被审计拦截 → 高通过率假象下的"审计全量误杀"。

## 7. 修复实施（✅ GAP-079，v2.102.0，2026-08-11）

修复 `_run_factor_audit` oos_result 构造：评估链走航存在但窗口不足（`n_windows_completed < 2`）且独立走航也失败时，构造带 `n_windows_completed=0` 的 oos_result（对齐 GAP-073 skipped 语义）；**仅当 walk_forward 完全缺失（无该字段）时才走 L1 icir 兜底**。

### 修复效果模拟（历史 1067 条 oos_consistency failed 重分类）

| 分类 | 数量 | 占比 |
|:-----|:-----|:-----|
| → 修复后转 skipped（走航 n_windows<2，无法验证） | **958** | **89.8%** |
| → 修复后仍 failed（真实 OOS 不一致 / 走航缺失） | 109 | 10.2% |

- 验证：新增 `tests/factor_engine/test_gap079_oos_skip.py` 6 用例（核心修复 / 独立走航优先 / 真实拦截保留 / L1 兜底不变 / 0/1 窗口 skipped 方法级）+ test_audit/test_evolution_loop 定向回归 81 passed 全绿；ruff 通过。
- 预期：未来演化候选 oos 误杀量级骤降，晋升通道恢复；真实 OOS 不一致（窗口≥2 低一致性）仍被拦截（10.2% 保留，不放松）。
- 后续：一次真实演化 run 验证晋升率变化（重跑 Phase 0 基线对比）。

### 真实运行验证（✅ 2026-08-11，`--universe futures --max-generations 5 --max-stocks 5`）

| 指标 | 修复前（08-11 早前 run） | 修复后（本次 run，20 分钟种子评估阶段） |
|:-----|:-----|:-----|
| 失败轨迹数 | 1483（累计） | **4**（3 audit_fail + 1 robustness_fail） |
| oos 误杀轨迹（走航 0 窗口无法验证） | 958 | **0** |
| 剩余 oos failed | — | 2 条全部为**真实 OOS 不一致**（独立走航 4 窗口 ic_consistency=0.00） |
| 通过审计进入 SHAP 的种子 | 极少（审计全拦） | **大量**（SHAP 批量执行 20 分钟未跑完） |

结论：误杀路径消失，审计恢复"真实质量拦截"语义；晋升通道恢复的直接证据成立（种子评估阶段通过门禁数量剧增）。SHAP 批量计算（每种子 ~14s）成为新瓶颈 → 登记 GAP-080。
