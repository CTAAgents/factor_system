# FTS 业务流程图

> 版本: v3.1.0+4
> 最后更新: 2026-08-20

## 全景业务流

```
数据接入层:  QuantData（唯一权威 K 线源，DuckDB 只读）→ data_futures（统一数据接口 get_panel / get_forward_returns）
     ↓
L0 人类设定:  Program.md → ProgramConfig（目标/约束/市场偏好/风险偏好）
     ↓
L1 Meta-Loop: 知识补给（批量采集→粗筛→LLM 深读，≥300 篇/天）→ 种子注入 → 演化方向指引
     ↓
L2 Evolution: 种子池(185) → 演化（GP/深度/算子 DSL/batch）→ 准入链（Verifier→去冗余→B.4→多重检验→WF→审计→评分卡→影子池）→ elite
     ↓
评审质检:    每日 04:00 统一任务（周日重量级 l2_review_job）→ approved（L3 唯一消费对象）
     ↓
信号输出:    L3 信号矩阵（l3_signal_service）→ 因子信号契约 v1 → Regime-Driven（策略合成，v3.0.0 起）
```

## 触发时序（调度源：TRAE Schedule 定时自动化，全期货）

```
每日 04:00  L1 知识补给（l1_meta_loop_job）：知识补给 + Bootstrapping + 种子注入（全期货 17 链分批）
每日        L2 种子评估+演化（l2_seed_promotion_job / l2_evolution_weekday|weekend_job）：种子晋升 + 演化主循环（工作日 ≈10 代 / 周末 ≈50 代）
每日 04:00  L2 监控+评审质检阀门：① pending 机审 + approved 复核 → ② 因子巡检降级（approved 豁免仅标记）→ ③ 逻辑监控 → ④ 数据级监控 → ⑤ 因子级监控；周日重量级 l2_review_job（reaudit 全量重审 + 衰减淘汰 + 阀门收口）
月度        外部因子导入（import_external_factors_job）：extract_* YAML 常态化导入
每 10 分钟  Health Check（状态轮询 / 熔断检测 / 告警）
```

> **调度源（v2.104.0+99）**：内部 `fts/scheduler` 定时任务默认停用（`FTS_INTERNAL_SCHEDULER_ENABLED` 默认 "0"），周期任务由 TRAE Schedule 执行，内部调度器不重复执行。一键启用 `$env:FTS_INTERNAL_SCHEDULER_ENABLED="1"; fts scheduler run`；停用 `Remove-Item Env:FTS_INTERNAL_SCHEDULER_ENABLED`；状态查看 `fts scheduler status`。
> 股票 L3 / 股票信号管道已随股票管线剥离至 fts-stock（2026-08），主系统不再调度。

## 角色边界

| 层级 | 职责 | 不可越界 |
|:-----|:-----|:---------|
| L0 人类 | 设定目标/约束/偏好 | 不干预具体因子演化 |
| L1 Meta-Loop | 知识补给、种子注入、方向指引 | 不修改因子代码 |
| L2 Evolution Loop | 因子发现、评估、演化、审计、质检 | 不构建组合、不输出交易信号 |
| L3 信号矩阵 | 因子信号矩阵输出（v3.0.0 组合侧已登记退役，策略合成职责迁移 Regime-Driven） | 不执行交易 |
| 策略合成层（Regime-Driven） | 三层 Regime + 信号合成 + 组合风控（消费因子信号契约 v1） | 不参与因子发现/演化 |
| 下游系统 | 执行交易 | 不参与因子发现 |

## 熔断机制

| 类型 | 触发条件 | 影响范围 |
|------|----------|----------|
| Token 熔断 | 消耗 > budget × 2.0 | 停止当前 L2 运行 |
| 低 IC 熔断 | 连续 5 代 IC < 0.005 | 暂停演化 |
| 失败率熔断 | 失败率 > 95% | 停止当前 L2 运行 |
| 连续低质量 | 连续 3 代质量分 < 30 | 暂停演化 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件描述 FTS 全景业务流，覆盖 `fts/factor_engine/meta_loop.py`（L1）、`evolution_loop.py`（L2）、`l3_signal_service.py`（信号矩阵）、`fts/scheduler/jobs.py`（TRAE Schedule 通用 job）各模块职责边界与触发时序 |
| 可验证断言 | 业务流含数据接入/L0~L3/信号输出五层；时序为 TRAE Schedule 全期货任务（L1 04:00 / L2 种子+演化每日 / 监控+评审阀门每日 04:00 周日重量级 / 外部因子导入月度）；角色边界 L2 不可构建组合、L3 不执行交易 |
| 检验方式 | 对照 `01-architecture.md` §6 确认层级与调度一致；`fts scheduler status` 查看实际调度数（内部停用默认 0） |
