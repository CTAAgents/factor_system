# F.3 因子信号接口契约 v1（FTS → Regime-Driven）— 详细设计

> 版本: v3.0.0+11-draft
> 创建: 2026-08-20
> 状态: **设计中**（随 plans/57 阶段 1 实施，步骤 2-3 据此落地）
> 关联: [57-dual-system-factor-strategy-split-plan.md](../plans/57-dual-system-factor-strategy-split-plan.md)（§三 接口契约草案）、[l3_signal_service.py](file:///d:/Programs/factor_system/fts/factor_engine/l3_signal_service.py)（FTS 侧实现）、RD `signal_client.py`（RD 侧实现）
> 背景: 双系统切分（因子生产 / 策略合成）的**接口层 SSOT**——固化"因子信号矩阵"如何从 FTS 流向 Regime-Driven，含 schema、双模式、增量/幂等、新鲜度、trace_id、接入边界。

---

## 1. 目标与范围

**目标**: 固化 FTS → RD 的因子信号接口契约 v1，使：
- FTS 侧（步骤 2）能落地 schema_version/factor_status/factor_scope 三列 + 历史回填模式；
- RD 侧（步骤 3）能实现 `signal_client.py`（双模式拉取 + 增量 + 幂等 + 新鲜度校验 + 降级熔断）。

**范围**: 载体与表结构、双模式读取语义、增量/幂等/版本、时序与新鲜度、trace_id 贯穿、接入边界、错误处理与降级语义。

**不在范围**: 因子生产策略本身（§6.7）、组合合成算法（RD strategy_synthesis）、拥挤度权威版替换（§2.3/步骤 7）、L2 子链化（步骤 8）。

---

## 2. 载体与表结构

以现有 `l3_signal_store.duckdb` 为载体（`l3_signal_service._init_tables` 幂等建表），**追加列、不重建**。

### 2.1 `l3_signal_meta`（追加 3 列）

```
schema_version: INTEGER NOT NULL DEFAULT 1   -- 契约版本（FTS 侧递增；RD 校验不兼容即告警降级）
factor_status:  VARCHAR NOT NULL DEFAULT 'pending'  -- active/degraded/shadow/retired（FTS 状态传播）
factor_scope:   JSON NOT NULL DEFAULT '{"subchain_scope": "all", "subchain_specific": []}'
                -- 通用因子 {subchain_scope: "all", subchain_specific: []}
                -- 特异因子 {subchain_scope: ["聚酯链"], subchain_specific: ["TA0","PF0"]}
```

迁移 SQL（幂等，`dates_digest` 已有先例）：

```sql
ALTER TABLE l3_signal_meta ADD COLUMN IF NOT EXISTS schema_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE l3_signal_meta ADD COLUMN IF NOT EXISTS factor_status VARCHAR NOT NULL DEFAULT 'pending';
ALTER TABLE l3_signal_meta ADD COLUMN IF NOT EXISTS factor_scope JSON NOT NULL DEFAULT '{"subchain_scope":"all","subchain_specific":[]}';
```

### 2.2 `l3_signal_matrix`（不变）

```
PRIMARY KEY (factor_id, market, end_date, symbol), signal DOUBLE[]
主键 UPSERT 幂等；dates_digest 前缀一致 → 增量追加，否则全量。
```

---

## 3. 双模式读取（隔离，防未来函数）

| 模式 | 语义 | 用途 | 隔离要求 |
|:--|:--|:--|:--|
| 决策模式 | `WHERE market=? AND factor_id IN (?) AND end_date = 最近 ≤ 决策日-1`；切片 → 合成层按状态加权（active 全权 + shadow 半权，degraded 零权/剔除，retired 强制剔除） | 每日交易计划 | 只读"昨日及以前"信号，禁止触碰当日未闭合窗口 |
| 训练模式 | `WHERE market=? AND factor_id IN (?) AND end_date 覆盖历史窗（如 500 日）` | 回测 / 权重学习 | 与决策模式严格隔离；回测/权重学习专用，不读未来 |

**实现约束**：RD `signal_client` 两个模式必须是独立方法（`fetch_decision` / `fetch_training`），禁止共用路径后靠参数切换——防止回测侧误用决策口径产生未来函数。

---

## 4. 增量 / 幂等 / 版本

- **增量**：FTS 追加新窗口 → `l3_signal_meta.dates_digest` 前缀对比 → RD 拉 delta；前缀不一致 → 全量重拉（回退 `load_signal_matrix` 全量语义）。
- **幂等**：主键 UPSERT，重复拉取无副作用；拉取带 trace_id 落 RD 日志（`signal_client` 每次拉取记录 `{trace_id, market, factor_ids, end_date, rows}`）。
- **历史回填**（步骤 2 落地）：FTS 信号服务新增"按 (date_range, factor_ids) 历史回算"入口（复用 `build_signal_matrix` 核心，输入 QuantData 全历史面板），落 RD 侧回测缓存；信号为"代码+参数"确定性函数，`code_hash+params_hash` 双哈希保证回填版本与实盘一致。
- **版本**：`schema_version` 由 FTS 侧在契约变更时递增；RD 拉取时校验，不兼容 → 告警 + 降级本地规则法（§7）。

---

## 5. 时序与新鲜度

| 时点 | FTS | RD |
|:--|:--|:--|
| 收盘后 | 更新因子状态（退化检测）→ 增量追加信号矩阵（新 end_date）→ 就绪标记 | — |
| 开盘前 | — | 拉取最新信号（end_date ≤ 决策日-1）→ 合成 → 生成交易计划 |

**新鲜度规则**：RD 校验 `end_date ≥ 决策日-1`，否则视为过期 → 走降级（§7），并在报告注明 `degraded: fts_signal_unavailable`。

---

## 6. trace_id 贯穿

```
FTS 信号更新 → 契约 meta 记录 trace_id → RD 拉取承接（关联父 trace_id）
→ RD 决策日志记录"信号版本指纹"（end_date + dates_digest + schema_version）
```

阶段 1 双轨对账按信号版本指纹对齐（plans/57 §3.5）。

---

## 7. 错误处理与降级语义（RD 容错底线）

RD `signal_client` 拉取失败时：
1. 连续失败 N 次（默认 3）触发熔断，冷却 5 分钟后自动重试恢复；
2. 熔断期间降级到 RD 本地 11 因子规则法（现有全链路，纯本地可运行）；
3. RD 无 FTS 也可完整运行 = 天然回滚通道，是阶段 1 安全双轨的底层保障；
4. 降级在报告注明 `degraded: fts_signal_unavailable`。

---

## 8. 接入边界

- **行情数据**：走 QuantData 只读 API（RD 已有，`data_read_api.py`）。
- **信号矩阵**：走 FTS 接口——初期 duckdb `read_only` 短连接直读（RD 侧以只读方式打开 `l3_signal_store.duckdb`）；规模上来后仿 QuantData 做只读 API 服务（开放项，plans/57 §八-1）。
- **存储访问契约**：FTS 侧信号库路径经 `FTS_L3_SIGNAL_STORE_DB` 配置（`l3_signal_service._default_db_path()`），禁止硬编码绕过注册表（storage_landscape.yaml `l3_signal_assets` 域）。

---

## 9. 验收断言（可验证）

| 断言 | 验证方式 |
|:--|:--|
| `l3_signal_meta` 含 schema_version/factor_status/factor_scope 三列 | `duckdb DESCRIBE l3_signal_meta` |
| 决策模式只返回 end_date ≤ 决策日-1 的切片 | RD `fetch_decision` 单测 + 查询 SQL 断言 |
| 训练模式与决策模式为独立方法 | RD `signal_client` 代码审查 / 单测 |
| 重复拉取幂等（UPSERT） | FTS `persist_signal_matrix` 幂等单测 + RD 双拉 rows 一致 |
| 历史回填不引入未来函数 | 回填入口仅消费 `end_date ≤ date_range 上界` 的数据 |
| 新鲜度校验与降级熔断生效 | RD 单测（构造过期/失败场景） |
| trace_id 贯穿拉取日志 | RD 拉取日志含 trace_id + 信号版本指纹 |

---

## 一致性元数据

| 代码 → 文档映射 | 可验证断言 | 检验方式 |
|:--|:--|:--|
| `l3_signal_service._init_tables` → §2 | l3_signal_meta 含三列且迁移幂等 | `duckdb DESCRIBE l3_signal_meta` / 重复迁移无错 |
| `l3_signal_service.build_signal_matrix` 历史回填入口 → §4 | (date_range, factor_ids) 入口可回算 | 回填单测 + 拼接校验 |
| RD `signal_client.py` → §3/§7 | fetch_decision/fetch_training 独立 + 熔断降级 | RD 单测 |
| RD 决策日志 → §6 | 拉取日志含 trace_id + 信号版本指纹 | 日志断言 |
