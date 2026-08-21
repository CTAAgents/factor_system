# FTS 开发生命周期

> 版本: v3.1.0+7
> 最后更新: 2026-08-20

---

## 1. 阶段划分

FTS 从 FDT 剥离至今经历多轮 Phase。**当前里程碑：Phase 48 双系统切分（v3.0.0）已交付**，进入"因子生产系统"稳态阶段（策略合成迁移 Regime-Driven）。

### 当前阶段（v3.0.0+ 起）

| 阶段 | 内容 | 状态 | 产出物 |
|:-----|:-----|:-----|:-------|
| **Phase 48** | 双系统切分（plans/57）：FTS 角色重定位为因子生产系统——① 信号契约 v1 固化（design/F.3）；② RD 承接（strategy_synthesis/combo_verifier/money_management/crowding_gate/backtest_engine/L2 子链化 5 子链）；③ FTS L3 组合侧退役登记（retired_l3.py 35 项，存量兼容不删码）；④ 全期货覆盖规划（coverage_priority P0-P3，84 品种/17 产业链）；⑤ 存量因子集中重审管道（review_legacy_factors.py 分族 FDR-BH）；⑥ 验收：阶段 0 A/B（状态一致率 92.04% / 方向一致率 97.55%）+ 阶段 1 双轨对账（信号余弦 1.0000 / 组合方向 100% / 绩效差 0.000000）+ 全量回归 8129 passed | ✅ 完成（阶段 0-13） | plans/57 + design/F.3 + retired_l3.py + scan_l3_retirement.py |
| **v3.1.0 评审质检体系优化** | plans/59 八项全部完成（GAP-161~168）：Regime 条件化门槛 / FDR 折扣 / 特异观察期 / IC 口径收敛 / 数据门禁 / 人审 SLA / 参数稳健区 / 流动性环境 | ✅ 完成 | qa/regime_thresholds.py + fdr_discount.py + specific_observe.py + ic_consistency.py + data_gate.py + review_sla.py + param_robustness.py + liquidity_env.py |
| **v3.1.0+3 换月日历根治** | plans/60 阶段A：主链路统一消费 QuantData continuous_daily 复权序列（消除双重复权 + 缓存一致性 + 幂等覆盖） | ✅ 完成 | quantdata_provider + aggregator 缓存 source 保留 + cache_max_age_days=1 |

### 历史阶段摘要（v2.x，完整明细见 07-operations.md 版本历史）

| 阶段 | 主题 | 关键产出 |
|:-----|:-----|:---------|
| Phase 47 | evolution_loop Mixin→协作类组合式重构（C 阶段） | 9 个 Mixin 全部退役为协作类，`class EvolutionLoop:` 零继承纯组合 |
| Phase 46 | evolution_loop Mixin 化拆分（B 阶段） | 9 领域 Mixin 拆分，行数 5117→1470 |
| Phase 45 | 数据持久化收敛 P0+P1+P2（plans/29，GAP-090） | StorageRegistry 存储域注册表 + 因子资产入库 + StateKVStore |
| Phase 49 | 因子×子链质量矩阵（v2.104.0+112，plans/49） | subchain_factor_quality 表 + 评审/生命周期张量化 |
| Phase 50 | L3 权重层 Gate 闭环（v2.104.0+113，plans/50） | gate_scale_map + Step 2.5 调制合并 |
| Phase 37 | 批量挖掘漏斗（v2.65.0，GAP-I201） | BatchMiner + _process_candidate 公共准入链 |
| Phase 35 | Barra 风格因子体系（v2.62.0，GAP-S02） | barra_style.py 10 风格暴露 + barra_neutralizer（期货保留用于风格中性化） |
| Phase 34 | 股票因子行业/市值中性化（v2.61.0，GAP-S01） | 已随股票管线剥离至 fts-stock |
| Phase 40 | DuckDB 并发模型根治（v2.86.0，GAP-056） | DuckDBWriter 单写者 + DuckDBReader 读池（后演进为 E.4 短连接 + filelock） |
| Phase 42 | L3 与信号管道解耦 + 权重重算（v2.99.0，GAP-072） | 调度解绑 + l3_weight_recompute_cadence |
| Phase 28-30 | 种子质检全链对齐 + 质检拦截器判定修复 + 鲁棒性阈值放宽 | L2 演化熔断解除 |
| Phase 22-24 | Elastic Net 合成 / P1 聚类+P2 PCA / ML 模型层 | L3 组合构建机构化基础 |
| Phase 16 | 组合漂移治理（v2.11.0） | DriftMonitor + 粘性约束 + L2 影子池 |
| Phase 15 | 算子演化引擎（v2.10.0） | OperatorEvolutionEngine + FTS-Expr DSL 基础层 |
| Phase 14 | Design 全量落地（v2.9.0） | 9 个设计文档全部实现 |
| Phase 1-13 | 核心契约/引擎/管线/策略/种子集成/期货自治循环/信号管道 | FTS 从 FDT 剥离到完整期货自治系统 |

---

## 2. 文件命名规范

- **Python 文件**: `snake_case.py`
- **测试文件**: `test_<module_name>.py`
- **配置文件**: `settings.yaml`
- **Markdown 文档**: `NN-topic.md`（NN 为两位数字序号）
- **程序配置文件**: `Program.md`（首字母大写，位于项目根目录）

---

## 3. 版本号命名规则

遵循语义化版本号 + build 段（SemVer build 段制，v2.103.0 修订）：

| 级别 | 变更类型 | 示例 |
|:-----|:---------|:-----|
| **MAJOR** | 重大架构变更（如双系统切分） | v2.105.0+33 → v3.0.0 |
| **MINOR** | 功能新增或阶段完成 | v2.104.0 → v2.105.0 |
| **PATCH** | 发布后修复（bugfix） | v2.103.1 |
| **BUILD** | 日常开发（GAP/测试/文档/数据修复） | v2.103.0+1 → +2 |

当前版本：**v3.1.0+3**。版本号唯一源头 = `pyproject.toml`，`fts/__init__.py` 动态读取，统一经 `scripts/bump_version.py` 管理（禁止手工修改）。

### 状态 schema 版本与冷启动规则

`STATE_SCHEMA_VERSION`（`contracts.py`）控制 L1/L2/L3 状态文件冷启动判定：`state.json` 中 `schema_version` 与 `STATE_SCHEMA_VERSION` 不一致 → 重新初始化；FTS 功能版本号变更（`EVOLUTION_VERSION`/`__version__`）**不触发**冷启动（避免小版本升级清空演化进度）。

---

## 4. session_id 与 trace_id 生成规则

```
trace_id = "{prefix}_{8hex}_{timestamp}"    # 例: l2_3f9a2b1c_20260718T001230
run_id   = "run_{8hex}_{timestamp}"         # 例: run_a1b2c3d4_20260718T001230
session_id = "session_{8hex}_{timestamp}"   # CLI 会话聚合标识
```

**全链路传播规则**：CLI 入口生成 `trace_id` 和 `session_id` → 传递给 factor_engine 各模块 → 管线各 stage 传播（`DataPayload.trace_id`）→ 监控和日志必须包含 `trace_id` → scheduler 任务执行时生成独立带前缀 trace_id。

---

## 5. 角色定义

| 角色 | 职责 | 定义文件 |
|:-----|:-----|:-----|
| **AI Agent** | FTS 全链路开发：编码、测试、文档、部署 | `agents/fts-agent.md` |

**角色边界**：AI Agent 不得执行人类审核员职责（解锁 Verifier）、不得修改已锁定 Program.md、不得越级执行交易决策（由下游 Regime-Driven/FDT 负责）、不得删除未过期 elite 因子、不得修改生产环境配置、不得省略或绕过 trace_id。

---

## 6. 状态机

各循环状态流转：

```
[stopped] → [running] → [paused/completed] → [circuit_broken] → [recovered/stopped]
```

- **循环**：L1 Meta-Loop / L2 Evolution Loop / L3（信号矩阵，组合侧已登记退役）。
- **因子状态**：active / shadow / degraded / pending / failed / deleted / retired / archived / deprecated（9 状态合法集，写入口枚举校验）。
- **未实现功能登记**：深度学习时序模型与强化学习（RL）登记 GAP-093（P2 远期，需实盘反馈闭环）。

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | `fts/factor_engine/contracts.py` → §3 版本规则（EVOLUTION_VERSION/STATE_SCHEMA_VERSION）；`fts/factor_engine/state.py` → §4 trace_id/session_id 生成；`fts/factor_engine/retired_l3.py` → §1 Phase 48 |
| 可验证断言 | 当前版本 v3.1.0+3 与 pyproject.toml 一致；Phase 48 双系统切分已交付（retired_l3.py 35 项登记存在） |
| 检验方式 | `python scripts/verify_doc_consistency.py`（版本号一致性 PASS）；`python -c "import fts; print(fts.__version__)"` 与版本头比对 |
