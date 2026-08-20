# 42 — 种子生命周期：预跳过 + 冷却期 + 回归通道计划

> 版本: v3.1.0
> 状态: ✅ 已完成 · 优先级: P1 · 负责人: FTS Agent · 关联: GAP-121, plans/34, plans/35
> 落地版本: v2.104.0+75（预跳过）、v2.104.0+76（冷却期 + 回归通道）

## 一、背景与问题定位

2026-08-16 能化链（GAP-121）L2 演化实测暴露两个问题：

| # | 问题 | 现状 | 影响 |
|---|------|------|------|
| 1 | 种子评估算力浪费 | YAML 种子每次运行全量评估，去重仅在 `_promote_to_elite`（完整评估之后）执行 | 实测 63 个唯一名重复因子 × ~2.5 分钟 ≈ **34% 种子评估算力浪费** |
| 2 | 退化因子被永久锁死 | 定期质检不合格（`status=degraded`）记录仍留在 factor_catalog，但两道存在性去重（预跳过 + 晋升去重）只判"库中存在"不区分状态 | **degraded 因子永远无法重新评估/晋升**，市场环境恢复后也无回归通道 |

## 二、方案设计

### 2.1 预跳过（v2.104.0+75）— 解决算力浪费

`SeedManager._evaluate_and_promote_seeds`（主演化路径，经 `EvolutionLoop._seed_manager` 转发）评估前经 `_owner._get_repo().get_factor_by_name(name, market)` DuckDB 查重，已入库种子直接 `continue` 拦截。查重异常吞掉不阻断评估（由 `_promote_to_elite` 兜底）。函数尾部新增汇总日志（已入库/实际评估/晋升数）。

### 2.2 冷却期 + 回归通道（v2.104.0+76）— 解决退化因子锁死

预跳过升级为**三态判定**：

| 状态 | 判定 | 行为 |
|------|------|------|
| active | 已入库且有效 | 跳过评估（省算力） |
| degraded + 冷却期内 | `(now - updated_at).days < COOLDOWN_DAYS` | 跳过评估（1 个月内不参与） |
| degraded + 冷却期满 | 降级 ≥ 30 天 | 放行重新评估（回归通道） |

配套晋升侧 `EliteStore._promote_to_elite`：degraded 记录放行晋升并**复用原 factor_id**（保留血缘与状态历史，不新建同名行）；`_write_to_duckdb` 幂等 update 分支对非 active 记录显式回写 `status=active`（防 `is_elite=True` 与 `status=degraded` 并存不一致）。

### 2.3 冷却期满回归伪代码

```
# ═══════════════════════════════════════════════════════
# 流程 1：种子评估前 —— 预跳过检查（SeedManager._evaluate_and_promote_seeds）
# ═══════════════════════════════════════════════════════

常量 COOLDOWN_DAYS = 30          # 冷却期（天）

for seed in 种子列表:
    existing = repo.get_factor_by_name(seed.name, market=self.market)

    if existing 为 None:
        goto 完整评估                     # 未入库 → 正常评估

    if existing.status == "active":
        记录日志 "预跳过已入库种子（active）"
        continue                         # ① 有效种子省算力，不评估

    if existing.status == "degraded":
        if 在冷却期内(existing):          # ← 核心判断
            记录日志 "冷却期内跳过退化种子"
            continue                     # ② 1 个月内不再参与评估

    # ③ 冷却期满（或时间戳不可用）→ 放行，落入完整评估


# ── 冷却期判断（_within_degraded_cooldown） ──
函数 在冷却期内(record):
    raw = record.updated_at              # 降级时间戳（update_factor 自动回写）
    if raw 为空: return False            # 缺失 → 放行（宁多评估不锁死）
    try:
        t_downgrade = 解析时间(raw)
        t_now       = 当前时间(对齐时区)
        return (t_now - t_downgrade).days < COOLDOWN_DAYS
    except 解析失败:
        return False                     # 解析失败 → 放行


# ═══════════════════════════════════════════════════════
# 流程 2：评估通过后 —— 晋升去重 + 重新激活（EliteStore._promote_to_elite）
# ═══════════════════════════════════════════════════════

existing = repo.get_factor_by_name(factor.name, market=self.market)

if existing 为 None:
    → 新建因子（create_factor，is_elite=True, status=默认 active）

elif existing.status == "degraded":      # ← 核心：退化因子回归通道
    记录日志 "退化因子重新激活（复用 factor_id）"
    factor.factor_id = existing.factor_id    # 复用原 ID，保留血缘与状态历史
    → 走幂等写入（update 而非 create，避免同名重复行）

else:  # active 等其他状态
    记录日志 "跳过重复因子"
    return 拒绝晋升


# ── 幂等写入（_write_to_duckdb） ──
写入因子:
    updates = 组装因子字段(is_elite=True, 最新评估指标...)
    if repo.get_factor(factor.factor_id).status != "active":
        updates.status = "active"        # 显式回写激活，防状态不一致
    repo.update_factor(factor.factor_id, updates)


# ═══════════════════════════════════════════════════════
# 完整闭环
# ═══════════════════════════════════════════════════════

degraded ──(冷却期 < 30 天)──→ 跳过评估（流程 1 ②）
degraded ──(冷却期 ≥ 30 天)──→ 重新评估（流程 1 ③）
                            └─→ 评估通过 → 复用 factor_id 激活回 active（流程 2）
                            └─→ 评估失败 → 保持 degraded，下轮再试
```

## 三、影响文件

| 文件 | 变更 |
|:-----|:-----|
| `fts/factor_engine/evolution_seeds.py` | `SeedManager`：预跳过三态 + `_degraded_cooldown_days=30` 类常量 + `_within_degraded_cooldown` 判定 + 汇总日志 |
| `fts/factor_engine/evolution_promote.py` | `EliteStore._promote_to_elite` 去重放行 degraded 并复用 factor_id；`_write_to_duckdb` update 分支显式回写 `status=active` |
| `tests/factor_engine/test_risk_tag.py` | 12→16 用例：active 预跳过 / 未入库正常评估 / 冷却期内跳过 / 冷却期满重新激活 |

## 四、测试验证

- `test_risk_tag`：16 passed（含 4 个新用例）
- 受影响模块回归（SeedManager/EliteStore 相关 8 文件）：325 passed
- ruff：All checks passed
- 回归执行命令：`pytest tests/factor_engine/test_risk_tag.py -q`

## 五、版本记录

| 版本 | 内容 |
|:-----|:-----|
| v2.104.0+75 | 种子预跳过（active 拦截，省算力） |
| v2.104.0+76 | 冷却期 30 天 + 退化因子回归通道（复用 factor_id 重新激活） |

## 六、边界与后续项

- `updated_at` 作为降级时间戳为近似方案（`factor_inspector` 降级经 `update_factor` 自动回写）；`factor_status_history` 无 degraded 变迁记录，如需精确降级时间可补写 `log_transition`（潜在改进项）。
- 冷却期天数 `_degraded_cooldown_days=30` 为类常量（可配置，与 logic_monitor 阈值先例一致），后续可按需迁移 `settings.yaml`。
