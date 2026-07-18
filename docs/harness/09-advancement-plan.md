# FTS 晋级计划

> 版本: v0.1.0
> 最后更新: 2026-07-18

---

## 1. 晋级总览

```
v0.1.0 (当前) ───→ v0.2.0 (目标) ───→ v0.3.0 (远期)
    │                    │                    │
    ├ Phase 1-7 完成     ├ 测试覆盖增强        ├ Data-Core 集成
    ├ 220 测试全绿       ├ micro_evol 修复    ├ E2E 测试
    └ 71% 总体覆盖       └ 差距关闭           └ 生产部署
```

---

## 2. 阶段台阶

### v0.1.0（当前 — 已完成）

**完成时间**: 2026-07-18

**核心产出**:
- ✅ 从 FDT 剥离为独立项目
- ✅ Phase 1-7 全部完成
- ✅ 因子引擎三层循环（L1/L2/L3）完整实现
- ✅ CLI 入口 + 监控 + 调度框架
- ✅ 220 测试用例全绿
- ✅ 总体覆盖率 71%

**已验证能力**:
- 因子演化全链路：种子池 → macro/micro evolution → 三级评估 → verifier 锁定 → elite 入库
- L1 Meta-Loop 知识补给 + Bootstrapping + 种子注入
- L3 Portfolio Loop 组合构建 + 正交化 + 衰减检验 + 信号合成
- CLI 子命令：version / monitor / evolution / meta-loop / portfolio / factor

---

### v0.2.0（近期目标 — 测试覆盖增强）

**目标时间**: 2026-08-18（v0.1.0 后 1 个月）

**目标指标**:

| 指标 | 当前值 | 目标值 |
|:-----|:-------|:-------|
| 总体覆盖率 | 71% | **80%+** |
| pipeline/strategies 覆盖率 | 0% / 51% | **80%+** |
| micro_evolution 覆盖率 | 31% | **70%+** |
| CLI/monitor/scheduler 覆盖率 | 0% | **60%+** |
| 总测试用例数 | 220 | **350+** |

**目标产出**:
| 产出 | 说明 |
|:-----|:------|
| pipeline 测试 | 3 个测试文件（base, factor_combiner, 集成测试），~80 用例 |
| strategies 测试增强 | 补充 base_v2.py 缺失路径，~30 用例 |
| micro_evolution 测试补全 | 安装 evolution extra 后补充异常路径，~30 用例 |
| CLI 集成测试 | 核心子命令（version/monitor/factor list）集成测试，~30 用例 |
| scheduler 单元测试 | TaskRegistry 基本操作测试，~20 用例 |
| monitor 测试 | format_status_report 人类可读输出测试，~10 用例 |
| 差距关闭 | GAP-001, GAP-003, GAP-005 关闭 |

**关键依赖**:
- `pip install .[evolution]` 安装 optuna 以运行 micro_evolution 测试

---

### v0.3.0（远期目标 — Data-Core 集成与端到端）

**目标时间**: v0.2.0 后 2-3 个月

**目标指标**:

| 指标 | 当前值 | 目标值 |
|:-----|:-------|:-------|
| 总体覆盖率 | 80%+ | **85%+** |
| 端到端测试 | 0 | **10+ E2E 用例** |
| 接口覆盖率 | 未度量 | **90%+** |

**目标产出**:
| 产出 | 说明 |
|:-----|:------|
| Data-Core UnifiedDataProvider 集成 | FTS 管线通过 datacore 获取真实数据 |
| 端到端数据流测试 | Data-Core → FTS pipeline → 因子计算 → 策略信号 |
| E2E 测试套件 | 10+ 全流程端到端测试用例 |
| 生产环境部署文档 | 调度配置、故障恢复、监控告警 |

**前置条件**:
- Data-Core 项目提供 `pip install datacore` 可用包
- UnifiedDataProvider 接口稳定
- GAP-001, GAP-002, GAP-003, GAP-004 全部关闭

---

## 3. 门禁条件

### 进入 v0.2.0 的门禁

| 条件 | 验证方法 | 通过标准 |
|:-----|:---------|:---------|
| 总体覆盖率 >= 80% | `python -m pytest tests/ --cov=fts --cov-report=term-missing` | 总体覆盖率 >= 80% |
| pipeline 模块有测试覆盖 | 覆盖率报告 | pipeline/base.py >= 80%, factor_combiner.py >= 80% |
| micro_evolution 覆盖率 >= 70% | 覆盖率报告 | micro_evolution.py >= 70% |
| 全部测试通过 | `python -m pytest tests/ -v` | 0 failed, 0 errors |
| 差距登记表更新 | 检查 08-gap-analysis.md | 已登记的 P0 差距关闭或 update |
| 版本号更新 | 检查 fts/__init__.py + pyproject.toml | 版本号为 v0.2.0 |
| 文档同步 | 检查 12 项清单 | 全部通过 |

### 进入 v0.3.0 的门禁

| 条件 | 验证方法 | 通过标准 |
|:-----|:---------|:---------|
| 总体覆盖率 >= 85% | pytest --cov | >= 85% |
| datacore 依赖可用 | `pip install datacore` | 安装成功 |
| E2E 测试套件就绪 | pytest tests/ | 10+ E2E 用例通过 |
| 所有 P0/P1 差距关闭 | 08-gap-analysis.md | 全部标记为「已关闭」 |
| 版本号更新 | pyproject.toml | v0.3.0 |

---

## 4. 版本历史

| 版本 | 日期 | 说明 |
|:-----|:-----|:-----|
| **v0.1.0** | 2026-07-18 | 从 FDT 剥离，Phase 1-7 完成，220 测试全绿 |
| **v0.2.0** | 目标 2026-08-18 | 测试覆盖增强，pipeline/strategies 80%+, micro_evolution 70%+ |
| **v0.3.0** | 目标 TBD | Data-Core 集成，端到端数据流测试，生产部署就绪 |
