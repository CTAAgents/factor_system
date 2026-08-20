# F.4 阶段 0 — RD 侧文档同步补丁

> 用途: 将 FTS 定义分发通道（双轨并存）同步到 RD 项目 `D:\Regime-Driven\docs\harness\`。
> 说明: 本文件由 FTS 侧生成（FTS 工作区禁止跨目录写入，以补丁形式交付）。每节标注目标文件与插入锚点，**复制粘贴对应内容**即可。契约 SSOT 见 FTS 侧 `F.4-factor-definition-dispatch-interface.md`。

---

## 1. `01-architecture.md` — §1.2.1 追加双轨并存

**插入位置**: 在 §1.2.1 段落末尾（`...data 输入契约与 DataLoader 输出字段兼容。` 之后、`### 1.3 分层架构` 之前）。

```markdown
**双轨并存（2026-08-20 契约固化）**：FTS 因子供给双通道并行，接口规格以 FTS 侧契约文档为 SSOT：

| 通道 | 契约文档 | 用途 | 状态 |
|:--|:--|:--|:--|
| **定义分发**（主路径） | `D:\Programs\factor_system\docs\harness\design\F.4-factor-definition-dispatch-interface.md` | RD 拉取 code+params 本地自组装计算，注册进 FactorRegistry 按 factor_id 引用（`fts:<factor_id>`） | 阶段 0 契约固化 |
| **信号矩阵直读**（对账/降级） | F.3-signal-contract-v1-design.md | ① 对账基准：证明自组装信号 == FTS 信号（diff < 1e-8）；② FTS 不可用降级通道 | 设计中 |

关键契约要点（详见 F.4）:
- **portable 语义**：FTS 预校验，仅下发可独立执行的 code（排除依赖 FTS runtime 的因子，如 `fct_91eb37e6` 已登记转译）；RD 侧再做 AST 复核 + 受限 exec 沙箱；
- **数据契约**：因子仅消费 `trade_date/open/high/low/close/volume`（`settle` 用代理公式 `(high+low+close)/3`），DataLoader 主力连续序列全覆盖；
- **增量幂等**：`(code_hash, params_hash)` 未变不重编译；
- **对账阻断**：自算信号与信号矩阵偏差超限的因子不进生产池。
```

**配套 1a — 版本历史表顶部插入**（在 `| v0.11.0 | ...` 行上方）：

```markdown
| v0.11.1 | 2026-08-20 | 契约文档同步（阶段 0）：F.4 定义分发接口固化 + 双轨并存架构（定义分发为主 / 信号矩阵对账与降级） |
```

**配套 1b — 一致性元数据表追加**（表格末尾）：

```markdown
| F.4 契约文档（FTS 侧） | §1.2.1 | 定义分发接口契约 SSOT 存在 | `test -f "D:\Programs\factor_system\docs\harness\design\F.4-factor-definition-dispatch-interface.md"` |
```

---

## 2. `02-lifecycle.md` — 新增「FTS 因子生命周期接入」

**插入位置**: 文件末尾（`| 15:00-16:00 | 数据归档 |` 之后）。

```markdown
## 三、FTS 因子生命周期接入（定义分发通道）

FTS 因子作为外部因子源进入 RD 因子池（`sector_rules` 按 `fts:<factor_id>` 引用），生命周期由 `ExternalFactorAdapter.sync()/refresh()` 驱动：

| 阶段 | 触发 | 动作 |
|:--|:--|:--|
| 首次注册 | `sync()` 首次运行 | 拉取 active+elite 定义 → AST 复核 → 受限 exec → 注册 FactorRegistry（key=factor_id） |
| 增量更新 | `sync()` 后续运行 | `(code_hash, params_hash)` 未变跳过；变更重编译重注册 |
| 状态刷新 | 每日 `refresh()` | FTS status → RD FactorStatus（active→ACTIVE；shadow→ACTIVE 半权；degraded→ACTIVE 零权；retired→移除） |
| 对账校验 | 每次变更后 | 自算信号 vs FTS 信号矩阵 diff < 1e-8，超限阻断进入生产池 |
| 降级 | FTS 不可达 | 熔断冷却后回退本地因子池（11 因子规则法），报告注明 `degraded: fts_definition_unavailable` |
```

---

## 3. `06-testing.md` — 新增 §4.10 定义分发与对账测试规划

**插入位置**: §4.9 表格之后（`---` 与 `## 一致性元数据` 之间）。

```markdown
### 4.10 定义分发与对账测试规划 (阶段 2)

| 测试文件 | 用例数 | 覆盖点 |
|:---------|:------|:-------|
| `tests/test_external_adapter.py` | 新增 | 双源拉取（REST/duckdb 兜底）；`(code_hash, params_hash)` 增量不重编译；AST 复核拒绝黑名单 code；受限 exec 沙箱；注册 key=factor_id；状态映射 |
| `tests/test_external_factor.py` | 扩展 | `settle` 代理公式 `(high+low+close)/3` 注入；输出 ndarray→Series(index=trade_date) 对齐 |
| `tests/test_backtest_external_factors.py` | 扩展 | 对账：自算信号 vs FTS `l3_signal_matrix`（fetch_training）diff < 1e-8 |
| `tests/test_sector_regime_rules.py` | 扩展 | `fts:<factor_id>` 前缀解析进 active_factors |
```

**配套 3a — 一致性元数据表追加**（表格末尾）：

```markdown
| `tests/test_external_adapter.py` | §4.10 | 定义分发适配器测试文件存在 | `test -f tests/test_external_adapter.py` |
```

---

## 一致性说明

- 上述补丁对应 FTS 侧 F.4 v1.0.0-draft（§4 状态映射 / §6 适配器指引 / §7 对账 / §8 降级）。
- RD 现有 `FactorDefinitionClient`/`ExternalFactor`（v0.11.0）为本通道基础实现；F.4 §6 为阶段 2 增强目标（AST 复核、双源、对账），落地时以 F.4 为准。
- 应用补丁后如需文档版本一致性校验，运行 RD 侧 `python scripts/verify_doc_consistency.py`。
