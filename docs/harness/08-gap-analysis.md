# FTS 差距分析

> 版本: v0.2.0
> 最后更新: 2026-07-18
> 状态: 活跃 — 随项目迭代持续更新

---

## 1. 差距总览

| 优先级 | 开放 | 已关闭 | 总计 |
|:-------|:-----|:-------|:-----|
| P0 | 0 | 2 | 2 |
| P1 | 0 | 2 | 2 |
| P2 | 0 | 3 | 3 |
| **合计** | **0** | **7** | **7** |

---

## 2. 差距登记表

### P0 — 阻塞性问题（影响核心功能）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-001 | `pipeline/` + `strategies/` | pipeline 模块（`base.py`, `factor_combiner.py`）和 strategies 模块（`base_v2.py` 部分路径）无对应测试文件，覆盖率为 0% | 无法验证管线串联和因子组合逻辑的正确性，重构风险高 | 1 周内 | ✅ 已关闭 |
| GAP-002 | `cli.py`, `monitor.py`, `scheduler/` | CLI 入口、项目级监控封装、调度层均无测试覆盖（覆盖率均为 0%） | CLI/监控/调度在生产环境无可靠性保障 | 1 周内 | ✅ 已关闭 |

### P1 — 重要改进（提升效率或稳定性）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-003 | `micro_evolution.py` | optuna 贝叶斯调参模块覆盖率仅 31%，依赖声明在 evolution extra 中，大部分分支路径（异常处理、参数传递）未覆盖 | 演化流程中的调参环节无充分测试，生产环境可能引发不可预见的 optuna 调用失败 | 1 月内 | ✅ 已关闭 |
| GAP-004 | `evaluation_chain.py` | 三级评估链覆盖率 90%，剩余 10% 的 mock 路径和异常分支未覆盖 | 边缘路径的评估逻辑可能存在隐含 bug | 1 月内 | ✅ 已关闭 |

### P2 — 一般改进（优化代码质量）

| ID | 模块 | 差距描述 | 影响 | 处理期限 | 状态 |
|:---|:-----|:---------|:-----|:---------|:-----|
| GAP-005 | `fts/monitor.py` | `format_status_report()` 方法缺少对人类可读输出的测试 | 监控报告格式变更后无法自动回归验证 | 3 月内 | ✅ 已关闭 |
| GAP-006 | `core/enums.py` | 覆盖率 0%，枚举定义的取值和序列化/反序列化未测试 | 枚举变更可能导致意外兼容性问题 | 3 月内 | ✅ 已关闭 |
| GAP-007 | `core/contracts.py` | 覆盖率 0%（虽然该文件仅为 re-export），但缺少对 re-export 路径有效性的测试 | 引入新契约时可能漏导出 | 3 月内 | ✅ 已关闭 |

---

## 3. 差距详情

### GAP-001: pipeline/ 和 strategies/ 模块无测试（已关闭）

- **解决方式**: 新增 `tests/pipeline/test_base.py`、`tests/pipeline/test_factor_combiner.py`、`tests/strategies/test_base_v2.py`
- **关闭时覆盖率**: pipeline/base.py 100%, factor_combiner.py 100%, base_v2.py 100%

### GAP-002: CLI/监控/调度无测试（已关闭）

- **解决方式**: 新增 `tests/test_cli.py`、`tests/test_monitor.py`、`tests/scheduler/test_tasks.py`
- **关闭时覆盖率**: cli.py 87%, monitor.py 100%, scheduler/tasks.py 100%

### GAP-003: micro_evolution.py 覆盖率低（已关闭）

- **解决方式**: 安装 evolution extra 后补充 optuna 分支测试
- **关闭时覆盖率**: micro_evolution.py 92%

### GAP-004: evaluation_chain.py mock 路径未覆盖（已关闭）

- **解决方式**: 通过 `tests/factor_engine/test_macro_evolution.py` 补充 LLM mock 场景
- **关闭时覆盖率**: evaluation_chain.py 96%

### GAP-005: monitor 格式输出测试（已关闭）

- **解决方式**: 在 `tests/test_monitor.py` 中补充 format_status_report 输出测试

### GAP-006: core/enums 测试（已关闭）

- **解决方式**: 新增 `tests/core/test_enums.py`，覆盖所有枚举取值和序列化

### GAP-007: core/contracts 测试（已关闭）

- **解决方式**: 新增 `tests/core/test_contracts.py`，验证 re-export 路径

---

## 4. 优先级定义

| 优先级 | 定义 | 处理时限 | 验证标准 |
|:-------|:-----|:---------|:---------|
| **P0** | 阻塞性问题，影响核心功能的正确性和可靠性 | 1 周内 | 新增测试覆盖率达到 80%+，相关模块无 P0 bug |
| **P1** | 重要改进，提升系统效率或稳定性 | 1 月内 | 新增测试覆盖率达到 70%+，关键路径全覆盖 |
| **P2** | 一般改进，优化代码质量和可维护性 | 3 月内 | 新增测试覆盖率达到 50%+ |

---

## 5. 差距关闭流程

1. 编写测试代码并通过 PR 审查
2. 运行完整测试套件确认全部通过（778 passed, 0 failed）
3. 更新本文件中的差距状态
4. 更新 `06-testing.md` 中的覆盖统计
5. 如果涉及架构变更，更新 `01-architecture.md`
