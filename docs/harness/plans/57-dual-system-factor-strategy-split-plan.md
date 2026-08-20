# 57 — 双系统职责重划计划（FTS 因子生产 / Regime-Driven 策略合成）


> 版本: v3.0.0+6

> 版本: 设计稿 v0.3（2026-08-19 细化完成：重审流程 + 全期货覆盖规划） · 文档类型: 立项设计文档（供审阅，未实施）

> 状态: ⏸ 设计阶段（等评审） · 优先级: P1 · 负责人: FTS Agent + RD Agent 协同
> 来源思想: `D:\Regime-Driven` 三层 Regime 策略路由（L1 宏观 / L2 产业链 HMM / L3 品种三信号 + 五要素路由）
> 关联: plans/40（L3 组合优化）、plans/47-49（子链系列）、plans/51-56（L3 信号/regime 系列）、RD `docs/designs/REGIME_STRATEGY_DESIGN.md`
> 本文档为**设计文档**：涵盖切分契约、接口契约、迁移清单、过渡路径、细节设计与风险，供评审后进入实施

> **2026-08-20 实施进度（goal 执行，用户指令推进至全部完成）**：
> - ✅ **步骤 0** 全期货覆盖规划落地：`config/futures_universe.yaml` `coverage_priority` P0-P3 四级（84 品种/17 产业链，T0/TL0 增补至 universe 84），`fts/data_futures.py` `FUTURES_COVERAGE_PLAN` 加载 + 校验（级别互斥/并集=universe），测试 4 用例。
> - ✅ **步骤 1** 信号契约 v1 固化：`docs/harness/design/F.3-signal-contract-v1-design.md`（载体/双模式/增量幂等/新鲜度/trace_id/降级语义 SSOT）。
> - ✅ **步骤 2** FTS 信号服务契约落地：`l3_signal_service.py` `l3_signal_meta` 追加 schema_version/factor_status/factor_scope 三列（DuckDB 1.5 无约束 ADD 幂等迁移）+ `load_signal_meta`/`backfill_signal_matrix`（历史回填，版本锁定双哈希 + 工作区隔离）/`verify_backfill_consistency`（拼接校验 1e-8），测试 8 用例。
> - ✅ **步骤 3** RD `signal_client.py`：fetch_decision/fetch_training 双模式隔离 + 新鲜度校验 + 熔断降级 + 状态加权 + 信号版本指纹，测试 11 用例。
> - ✅ **步骤 4** 因子映射表 + `scripts/verify_factor_mapping.py`：RD 11 因子 ↔ FTS DSL 等价实现映射（10 因子 Spearman=1.0000 A 档入轨；adx/atr 新增 `ts_adx_wilder`/`ts_atr_ratio` 算子精确复刻 RD 口径；roll_yield/near_far_spread_z 待定）；实测报告 `memory/logs/factor_mapping/`，测试 5 用例。
> - ✅ **步骤 5** 阶段 0 A/B 对照：`scripts/ab_stage0_l3_comparison.py` 真实 QuantData 10 能化品种 × 近 1 年，状态一致率 **92.04%**（≥90%）、方向一致率 **97.55%**（≥95%）**双门槛通过**；报告 `memory/logs/ab_stage0/report_final_2026-08-20.json`。
> - ✅ **步骤 6** RD 合成/校验/资金迁移：`strategy_synthesis.py`（等权/ElasticNet/ML/BL + 状态加权 + 相关性/子链去冗余）、`combo_verifier.py`（夏普/随机化/数量阀）、`money_management.py` 并入 capital 分配（vol_target/kelly/risk_parity/min_variance/fixed），测试 20 用例。
> - ✅ **步骤 7** RD 拥挤度权威替换：`crowding_gate.py` 接口不变（CrowdingGate.evaluate/apply_gate/GATE_SCALE），内部 FTS 权威分位口径 + 方向分解（long/short/neutral）+ `build_joint_gate_scale`/`apply_crowding_direction_bias`，测试 14+9 用例。
> - ✅ **步骤 8** RD L2 子链化：`sub_chain.py`（energy_chemicals → 5 子链，命名映射对齐 FTS subchain_scope + 子链级 HMM + 聚合回产业链），测试 7 用例。
> - ✅ **步骤 9** RD backtest 消费信号矩阵：`BacktestEngine(external_factors=...)` 外部因子覆盖（§6.5 回填信号主路径）+ `signal_client.to_backtest_factors` 转换，测试 4 用例。
> - ✅ **步骤 10** 阶段 1 双轨对账：`scripts/ab_stage1_dual_track.py` **真实 QuantData 8 能化品种 × 1 年双轨全链对账（RD 本地 11 因子 vs RD+FTS 信号，BacktestEngine 全链）全门槛通过**：信号级余弦 1.0000、组合级方向一致率 100%/敞口差 0.00%/换手差 0.00%、绩效级 60 日收益差与回撤差 0.000000（≤基准年化波动×20%）；报告 `memory/logs/ab_stage1/report_2026-08-20.json`；`reconcile_dual_track.py` 对账机器测试 9 用例。
> - ✅ **步骤 11** P0 存量因子集中重审管道：`scripts/review_legacy_factors.py`（Stage 0 清点分层 / Stage 5 分族 FDR-BH / Stage 6 结论 dry-run 落库 + audit 抽查），真实 energy 库 dry-run 运行；测试 11 用例。
> - ✅ **步骤 12** 阶段 2 FTS L3 退役（门禁已通过，退役登记完成）：`fts/factor_engine/retired_l3.py` 退役登记（§4.1 全 35 项：futures_signal_pipeline 组合侧 + portfolio_loop 策略侧 + weight_learning/capital_allocator/regime_crowding 三模块标记弃用，import 期 DeprecationWarning + `warn_if_retired` 调用告警，存量调用点兼容不删码）；`scripts/scan_l3_retirement.py` 调用图扫描（只读）；测试 8 用例 + 受影响模块 58 用例全绿。**全量回归（2026-08-20 实测）**：8129 passed / 21 failed（残留 21 项全部预存——GAP-157 严格注册表×测试隔离已修复清零、universe 测试已同步 84/14/9，剩余 GAP-158：工作区既有 portfolio_loop 本地改动 + pandas-3 只读 + regime_features，均非本次计划引入）。**物理删除 + 里程碑 SemVer bump + 提交归发布操作（§5.3，commit 需用户显式授权）**。
> - ✅ **步骤 13** energy 链闭环验收：阶段 0 A/B（92.04%/97.55%）+ 阶段 1 双轨对账（100%）双门槛通过 + 映射 10/12 Spearman=1.0 + 全链路组件测试全绿构成闭环证据；P1-P3 覆盖扩展按 `coverage_priority` 逐链推进（长期运营项）。
>
> 环境缺口登记：GAP-154（numba/numpy 不兼容→njit 透传修复）、GAP-155（pandas 3 read-only legacy 模板因子路径）、GAP-156（lightgbm 小截面零分裂→gain+min_child_samples 修复）已登记 `docs/harness/08-gap-analysis.md`。期间修复：adx 映射真实数据漂移（`ts_adx_wilder` 移除 fillna 对齐 RD 精确 NaN 口径，Spearman 0.93→1.0）、阶段 1 双轨因子预热窗对齐（外部因子须覆盖 start−450d）。

> **2026-08-19 重大决策修订（v0.1 → v0.2）**：经评估，FTS 存量因子资产价值低（energy 库 362 因子存活率 6.6%，24 active 中 8 个无有效子链），路线从"保留 FTS 全部资产"调整为 **"基础设施复用 + 重设计因子生产 + 重建干净生命周期管理"**——保留工程质量资产（信号矩阵服务 / DSL 算子 / 存储 schema / 数据链路），**不继承存量因子资产**，因子生产策略从"广撒网演化"重设计为"状态特征 + 品种特异信号"方向（§6.7）。

> **2026-08-19 补充澄清**："因子价值低"的准确含义是**覆盖面窄**（因子集中于能化链，大部分仅对能化子链有些许用途），非因子质量差。**智能因子系统目标是面向全部期货所有品种**（RD 6 产业链 / 52 品种为骨架），覆盖扩展是改造核心目标（§5.4、§6.7）。

---

## 一、背景与目标

### 1.1 问题定位

| 现状 | 问题 |
|:-----|:-----|
| FTS = 因子生产系统（离线重资产） | 拥有完整因子流水线（注入/5 通道演化/三级评估/QA Q1-Q10/audit 6 项/退化检测/审计血缘/影子池/经验链），但 L3 组合侧（信号合成/组合校验/regime 调制/资金分配）与因子管理职责混在同一系统，系统复杂度持续上升 |
| Regime-Driven = 策略路由系统（在线轻资产） | 三层 Regime（L1 规则/L2 HMM/L3 三信号）→ 五要素路由设计良好且有五年回测验证，但因子层仅 11 个固定因子（无演化/质检/生命周期），无因子资产库 |
| 两系统数据链路 | FTS 数据聚合器已 QUANTDATA 置首（`data_futures.py::_init_default_aggregator`），因子计算与 RD 天然同源（D:\QuantData） |

**核心决策**：将"因子生产"与"策略合成"彻底分层——FTS 退役 L3 组合侧，专注因子管理与信号输出；Regime-Driven 承接策略合成（三层 Regime + 信号合成 + 五要素路由 + 组合风控）。**因子侧以"基础设施复用 + 重设计"的新项目形态推进**（v0.2 修订）：保留工程质量资产；存量因子（含退役）作为候选池保留，新系统建好后集中走一轮演化评审质检（§6.7）；重设计因子生产策略并扩展至全期货（§6.7、§9 步骤 0）。

### 1.2 目标架构（三层）

```
数据层        D:\QuantData（已有）—— 行情/存储，RD 与 FTS 均只读
                ↑ 只读
因子生产层    FTS（保留：注入/演化/质检/生命周期/因子资产库/信号矩阵输出）
                → 输出：因子信号矩阵 + 因子画像（子链/regime/质量）+ 因子状态
                ↓ 接口（因子信号契约 v1）
策略合成层    Regime-Driven（扩展：三层 Regime + 信号合成 + 五要素路由 + 组合风控）
                → 输出：每日交易计划 / 交易管道
```

### 1.3 关键事实（已核查，支撑方案可行性）

1. **数据口径已同源**：FTS 聚合器源顺序 `QUANTDATA → TDX_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC`，因子计算优先直读 D:\QuantData（continuous_daily 主连后复权）。RD 与 FTS 天然同源。
2. **QuantData 有只读 API 服务**（`data_read_api.py` + readonly service plan + watchdog），行情读取可统一走该 API。
3. **weight_learning / capital_allocator 迁移边界干净**：消费方仅 portfolio_loop（弹性网/ML）与 capital_allocator（资金分配），均属策略侧，无因子侧调用点，可整体搬迁。
4. **拥挤度双实现重复**：FTS `regime_crowding.py` 与 RD `crowding_gate.py` 为同一套"拥挤度 6 信号"的两个实现（方法一一对应），合并时必须统一。
5. **历史信号覆盖率缺口**：RD 五年回测（2021-08 ~ 2026-08）需要历史信号，而 FTS 信号矩阵为滚动窗口（300/500 日）。需 FTS 新增"历史回填模式"。

---

## 二、切分契约（`因子→信号` 归 FTS，`信号→组合` 归 RD）

### 2.1 切分原则

凡函数输入是"单个因子信号序列" → 归 FTS；凡输入是"因子集合 / 组合状态" → 归 RD。

### 2.2 FTS 保留（**基础设施**，函数级锁定）——存量因子资产不继承

> v0.2 修订：保留以下工程质量资产（复用，与"因子价值低"无关）。**存量因子（含退役因子）不作为最终资产直接继承，但也不丢弃——作为候选池保留，待新系统建好后集中走一轮演化评审质检**（能化链因子对能化子链仍有些许用途，重审后有价值的保留、无价值的退役，§6.7）。

| 能力 | 位置 |
|:-----|:-----|
| 信号矩阵构建 / 增量 | `l3_signal_service.build_signal_matrix` / `load_or_build_signal_matrix` |
| 信号序列计算 | `futures_signal_pipeline._compute_signal_matrix` |
| 正交化 / 中性化 | `portfolio_loop.orthogonalize_factors` |
| DSL 算子库（因子表达式基础） | `ops_library` / `expr_dsl/registry` |
| 数据链路 | `FTSDataProvider` → D:\QuantData（QUANTDATA 置首） |

### 2.3 RD 承接（策略侧）

| 能力 | 迁移来源 | RD 落点 |
|:-----|:-----|:-----|
| 信号合成（弹性网/ML/等权/BL） | `portfolio_loop.synthesize_signals` / `_compute_elastic_net_weights` / `_compute_ml_ensemble_weights` / `_synthesize_bl_weights` | 新模块 `strategy_synthesis.py` |
| 组合级选因子（去冗余/质量门控） | `_greedy_select_by_correlation` / `_dedup_factors_by_chain*` / `_filter_*` | 并入合成前处理 |
| 权重学习 / 资金分配 | `weight_learning.py` 整体 + `capital_allocator.py`（vol_target/kelly/fixed） | 并入 RD `money_management.py` |
| 组合校验（夏普/随机化/相关性） | `_validate_combo_sharpe` / `_run_sharpe_randomization_test` / `_cap_safety_valve` | 并入 router 风控链 |
| 衰减 / 换手 / 粘性约束 | `decay_test` / `apply_turnover_penalty` / `_apply_sticky_constraints` | 并入 router |
| Regime 调制 | `regime_adaptive_weight_adjustment` / `_apply_regime_weight_adjustment` / `_apply_regime_direction_bias` | RD crowding/observation 链扩展 |
| 拥挤度门控 | **FTS `regime_crowding.py` 为权威版本**（Pydantic 配置化 + direction bias），替换 RD `crowding_gate.py` | `crowding_gate.py` 接口不变，实现替换 |
| 组合级回测 | `backtest/`（组合侧） | RD `backtest_engine` 扩展为消费信号矩阵 |

### 2.4 边界争议项处理

- **因子去冗余两级拆**：因子级（入库前相关性筛查，避免共线性污染）留 FTS；组合级（为当前组合选择因子子集）迁 RD。
- **L3 品种状态识别不重写**：RD `l3_identifier.py` 三信号（trend_score/vol_score/oi_signal/term_z）是"市场状态特征"，FTS 信号矩阵是"可交易 alpha 信号"，两者语义不同层。L3 保持 RD 本地 11 因子规则法，**新增**"FTS 信号 → 组合权重"平行路径（在 router 合成层），不替换 L3 判定。

---

## 三、接口契约草案（因子信号接口 v1）

### 3.1 载体与表结构

以现有 `l3_signal_store.duckdb` 为载体（`l3_signal_service.py::_init_tables`），追加版本化字段，不重建：

```
l3_signal_meta（追加 3 列）:
  schema_version: int = 1          # 契约版本（FTS 侧递增，RD 校验不兼容即报警）
  factor_status: str               # active/degraded/shadow/retired（FTS 状态传播）
  factor_scope: json               # subchain_scope + subchain_specific（特异因子链范围）
l3_signal_matrix: 不变             # (factor_id, market, end_date, symbol) → signal DOUBLE[]
主键 UPSERT 幂等；dates_digest 前缀一致 → 增量追加，否则全量
```

### 3.2 双模式读取（隔离，防未来函数）

```
决策模式：WHERE market=? AND factor_id IN (?) AND end_date = 最近 ≤ 决策日-1
          切片 → 合成层（active 全权 + shadow 半权，degraded 零权/剔除，retired 强制剔除）
训练模式：WHERE market=? AND factor_id IN (?) AND end_date 覆盖历史窗（如 500 日）
          与决策模式严格隔离（回测/权重学习专用，不读未来）
```

### 3.3 增量 / 幂等 / 版本

- 增量：FTS 追加新窗口 → `dates_digest` 前缀对比 → RD 拉 delta；前缀不一致 → 全量重拉。
- 幂等：主键 UPSERT，重复拉取无副作用；拉取带 trace_id 落 RD 日志。
- 历史回填：FTS 信号服务新增"按 (date_range, factor_ids) 历史回算"入口（复用 `build_signal_matrix` 核心，输入换 QuantData 全历史面板），落 RD 侧回测缓存。存储量估算：14 品种 × 1223 日 × 100 因子 ≈ 13.7 MB（Parquet 列式后更小），无存储压力。

### 3.4 时序与新鲜度

| 时点 | FTS | RD |
|:-----|:-----|:-----|
| 收盘后 | 更新因子状态（退化检测）→ 增量追加信号矩阵（新 end_date）→ 就绪标记 | — |
| 开盘前 | — | 拉取最新信号（end_date ≤ 决策日-1）→ 合成 → 生成交易计划 |

RD 必须验证信号新鲜度：end_date ≥ 决策日-1，否则视为过期走降级（见 §6.6）。

### 3.5 trace_id 贯穿

FTS 信号更新 → 契约 meta 记录 trace_id → RD 拉取承接（关联父 trace_id）→ RD 决策日志记录"信号版本指纹"（end_date + dates_digest + schema_version），供阶段 1 对账精确定位消费版本。

### 3.6 接入边界

- 行情数据：走 QuantData 只读 API（RD 已有）。
- 信号矩阵：走 FTS 接口（初期 duckdb `read_only` 短连接直读；规模上来后仿 QuantData 做只读 API 服务——开放项，见 §八）。

---

## 四、迁移清单

### 4.1 FTS 退役清单

- `futures_signal_pipeline.py` 组合侧：`_compute_composite_scores` / `_compute_per_variety_weights` / `_apply_regime_weight_adjustment` / `_apply_regime_direction_bias` / `_generate_trading_advice*` / `_compute_holdout_validation` / `_load_l3_combo_*`。
- `portfolio_loop.py` 策略侧：`synthesize_signals` / `_compute_elastic_net_weights` / `_compute_ml_ensemble_weights` / `_synthesize_bl_weights` / `regime_adaptive_weight_adjustment` / `build_combo` / `_cap_safety_valve` / `_validate_combo_sharpe` / `_run_sharpe_randomization_test` / `decay_test` / `apply_turnover_penalty` / `_apply_sticky_constraints` / `_compute_subchain_exposure` / `_merge_gate_scale_into_modulation` / 组合级选因子 `_greedy_select_by_correlation` / `_dedup_factors_by_chain*` / `_filter_*`。
- `weight_learning.py`、`capital_allocator.py`、`regime_crowding.py`（平移后 FTS 侧删除或标记弃用）。

### 4.2 RD 新增清单

- `signal_client.py`：接口拉取（双模式）+ 增量 + 幂等 + 新鲜度校验 + 降级。
- `strategy_synthesis.py`：信号合成（弹性网/ML/等权/BL）+ 组合级选因子。
- `combo_verifier.py`：组合校验（夏普/随机化/相关性熔断）。
- `money_management.py` 扩展：并入 `weight_learning` + `capital_allocator`。
- `crowding_gate.py` 实现替换为 FTS 权威版本。
- L3 平行路径：router 合成层新增"FTS 信号 → 组合权重"分支（L3 判定本身不动）。
- `backtest_engine` 扩展：消费信号矩阵（历史回填缓存）。
- RD L2 子链化：energy_chemicals 拆 5 子链（能源/聚酯/油化工/煤化工/橡胶），子链索引 + 子链 HMM + 决策链降粒度 + 子链命名映射表（对齐 FTS subchain_scope）。

### 4.3 接口层新增

- 契约文档（schema_version / 增量语义 / 幂等规则 / 双模式隔离）。
- 口径对齐验证脚本（FTS 因子信号 vs RD 同源数据一致性）。
- `scripts/verify_factor_mapping.py`：RD 11 因子 ↔ FTS 等价实现映射表（Spearman 相关性 ≥0.95/0.90/0.80 三档；<0.95 标"待定"不入对账）。

---

## 五、过渡路径（3 阶段 + 验收标准）

### 5.1 阶段 0：契约先行（FTS L3 不动）

- 固化信号契约 v1 + RD `signal_client` 只读接口。
- RD 用真实信号矩阵做 A/B：信号矩阵消费 vs 固定 11 因子 → L3 状态结果对照。

**验收门槛**：状态一致率 ≥ 90%，方向一致率 ≥ 95%。不达标 → 定位因子映射问题，修复后重跑。

### 5.2 阶段 1：双轨对账（中等工作量）

- RD 全链跑通（三层 Regime + 合成 + 五要素）；FTS L3 继续运行作基准。
- 两边输出逐日对账（按信号版本指纹对齐）。

| 层级 | 指标 | 门槛 |
|:-----|:-----|:-----|
| 信号级 | 因子权重向量余弦相似度（同因子集） | ≥ 0.85 |
| 组合级 | 方向一致率 / 敞口差 / 换手差 | ≥ 95% / ≤ 5% / ≤ 20% |
| 绩效级 | 滚动 60 日累计收益差 / 回撤差 | ≤ 基准年化波动 20% |

**不一致处理**：任一层超门槛 → 暂停退役，按"信号层 → 合成层 → 校验层"定位，修复后重新开对账窗口（不边修边退役）。

### 5.3 阶段 2：退役（独立里程碑，FTS 侧流程成本集中于此）

- FTS 退役 L3 组合侧，仅保留因子管理 + 信号矩阵输出。
- FTS 测试调整（709 测试中 L3 相关）+ 13 项文档同步 + 版本 bump（SemVer）→ 全量回归（符合"全量回归仅发布前执行"政策）。
- RD 稳定性验收：独立运行连续 N 交易日无接口错误/幂等冲突。

### 5.4 覆盖范围边界

**目标：智能因子系统面向全部期货所有品种**（以 RD 的 6 产业链 / 52 品种为全市场骨架，FTS 因子生产覆盖所有产业链，非仅能化链）。

现有 FTS 因子资产集中于能化链（大部分仅对能化子链有些许用途），对其他产业链覆盖近乎为零——**这正是存量资产"价值有限"的真实含义（非因子质量差，是覆盖面窄）**。覆盖扩展是改造核心目标之一：按产业链逐个建立因子覆盖（能源化工 → 黑色 → 有色 → 农产品 → 贵金属 → 金融），RD 本地 11 因子在扩展完成前对未覆盖链兜底。

---

## 六、关键细节设计

### 6.1 信号合成迁移（弹性网/ML）

依赖三块：① 面板数据（零迁移——聚合器已 QUANTDATA 置首，RD 直接同源读或走 QuantData API）；② 信号矩阵+前向收益（留 FTS，经接口拉历史切片）；③ 纯算法 `weight_learning`（整体平移 RD）。训练输入用历史切片（训练模式），决策用截至昨日的切片 + 固化权重（决策模式），两模式严格隔离。

### 6.2 L3 平行路径（不重写）

新增"FTS 信号 → 合成层 → 因子权重"路径，与 L3 状态路由并列，联合输出交易计划。RD 现有 `SIGNAL_MISSING_WEIGHTS` + `MISSING_SCALE_FLOOR=0.30` 扩展为因子级：`factor_status` 进权重计算（shadow 半权、degraded 零权、retired 剔除）；`observation_gate` 复用为"因子状态变化观察期"防抖。

### 6.3 因子分层协同（双系统闭环核心）

```
FTS 退化检测/生命周期 → meta.factor_status 更新 → RD 合成层按状态降权
FTS subchain_scope（特异因子链范围）→ RD L2 子链层做品种激活（与产业链成员表对齐）
通用（scope=all）：全品种参与合成；特异（scope=[链]）：仅 scope 链内激活 + 权重上限 10%；unknown：观察
```

### 6.4 RD L2 子链化（energy_chemicals → 5 子链）

子链索引（复用 `build_sector_index`）、子链 HMM（复用 `_load_or_fit_model`，窗口 63/126/252）、决策链降粒度（`apply_sector_regime` 逻辑降子链）、子链命名映射表（对齐 FTS）。注意子链样本比产业链更稀疏，需按 RD 既有 `test_hmm_drift` / 回测验证。

### 6.5 历史回填模式

FTS 信号服务新增"按 (date_range, factor_ids) 历史回算"入口（复用 `build_signal_matrix`，输入 QuantData 全历史面板）。信号为"代码+参数"确定性函数，历史回填不引入未来函数；`code_hash+params_hash` 双哈希保证回填版本与实盘一致。RD 回测用回填信号（主）+ 映射表本地算（交叉验证）。

### 6.6 降级与熔断（RD 容错底线）

FTS 信号拉取失败 → 熔断（连续 N 次 + 冷却）→ 降级到 RD 本地 11 因子规则法（现有全链路，纯本地可运行）→ 冷却后自动重试恢复。RD 无 FTS 也可完整运行 = 天然回滚通道，是阶段 1 安全双轨的底层保障。降级需在报告注明 `degraded: fts_signal_unavailable`。

### 6.7 因子生产策略重设计（v0.2 新项目形态核心）

**根因判断（2026-08-19 澄清修正）**：FTS 存量因子"价值有限"的真实原因 = **覆盖范围窄**——因子资产集中于能化链（大部分仅对能化子链有些许用途），对全期货 6 产业链 / 52 品种的覆盖面近乎为零，**而非因子质量差或"期货截面 alpha 稀缺"**。因此生产策略重设计的核心目标是**从能化链扩展至全期货所有品种**（§5.4）。

**因子角色重新定位**（从"通用 alpha 源"到"策略输入"）：

| 角色 | 生产方向 | 评估目标函数 |
|:-----|:-----|:-----|
| 通用因子 | 状态识别特征（趋势/波动/持仓/期限结构） | 对 Regime 状态识别的增量贡献（非截面 IC 唯一标准） |
| 特异因子 | 子链/品种特异信号（链内相对强弱等） | 品种特异显著性（三护栏，见品种级画像设计） |

**生产策略**：从"算子广撒网演化（5 通道）"收敛为"少而精"——知识注入（人工种子 + 领域因子）为主、定向演化（围绕状态特征/特异信号方向）为辅，不做无方向的大规模随机组合。

**生命周期管理（重建为干净闭环）**：注入 → 评估（增量贡献 + 特异显著性）→ 退化检测 → 退役，状态机 active/shadow/degraded/retired，存储复用 factor_catalog 精简 schema。

**新项目成本结构**：保留基础设施（信号矩阵/DSL/存储/数据链路）+ 重设计生产策略 + 重建生命周期 = 显著低于"重建 FTS 60-70%"，且不背存量因子价值低的包袱。

### 6.8 P0 存量因子集中重审流程（新系统建好后执行）

**触发时机**：阶段 11（因子周期管理系统新建）完成后，作为新系统**首轮资产填充**；RD 本地 11 因子在重审完成前持续兜底。复用 FTS 评估/质检组件，结论写入新系统（FTS 库冻结归档）。

**重审对象与分层**：

| 层 | 现状态 | 数量 | 重审策略 |
|:--|:-----|:---|:-----|
| L1 | active | 24 | 新标准全量复检（保留 / 降级） |
| L2 | shadow | 172 | 影子期转正评估（转正 / 转衰 / 维持观察） |
| L3 | degraded | 190 | 恢复评估（误杀恢复 / 真退化确认） |
| L4 | deleted / 其他 | 1 | 豁免（除非 scope 独特有特殊价值） |

可扩展：futures 库中覆盖能化品种（SC0/FU0 等）的因子，按 symbol 交集识别纳入。

**差异化评估标准（核心，区别于旧标准）**：

```
旧标准（截面 alpha）：IC 均值 / IR / 衰减 / 半衰期
新标准（策略输入，§6.7 角色重定位）：
  通用因子维度：状态识别增量贡献（对 Regime 状态区分的 AUC/互信息增量）+ 特征覆盖度
  特异因子维度：品种/子链特异显著性（三护栏：时序 t 检验 / 链内相对优势 / 动态 Bonferroni）
  兼容维度：基础 IC/IR 保留为最低门槛（防纯噪音因子混入）
```

**管道（8 阶段）**：

| 阶段 | 内容 | 复用组件 |
|:--|:-----|:-----|
| 0 | 清点分层：按现状态 + 覆盖子链分组，生成重审清单（trace_id 贯穿） | FactorRepository 查询 |
| 1 | 公共面板重建：P0 全 24 品种 × 数据窗（训练窗 + OOS 窗切割） | FTSDataProvider（QUANTDATA） |
| 2 | 信号重算：因子代码/参数不变，code_hash+params_hash 保证版本一致，历史回填模式 | l3_signal_service |
| 3 | 多周期评估：训练窗 + OOS 窗分别算 IC/IR/衰减/半衰期 + 状态识别贡献 | evaluation_chain / factor_lifecycle |
| 4 | 特异性重审：子链画像重建 + 品种级画像（三护栏） | subchain_profile / 品种级画像 |
| 5 | 统计护栏：362 因子集中重审必须做 FDR/BH 校正 + audit 6 项抽查 | audit / 多重检验 |
| 6 | 结论落库：promote / observe / retire 写入新系统因子资产库，状态历史留痕 | FactorRepository（新系统） |
| 7 | 报告与血缘：各层通过率 + 保留清单 + 血缘追溯（parent/评估历史迁移） | 报告模板 |

**关键设计决策**：

1. **重审是"新系统首轮资产填充"，非 FTS 库迁移**——结论写入新系统，FTS 库冻结归档，避免两库漂移。
2. **差异化标准是重审核心创新**——重新发现"旧截面 IC 标准漏掉的策略贡献因子"（如波动率类因子对状态识别有贡献但截面 IC 平庸），这是存量因子重审的最大价值。
3. **数据窗严格切割**：训练窗（~250 日）+ OOS 窗（~60 日）独立，防重审本身过拟合。
4. **多重检验放大风险**：362 因子集中重审 = 天然的多重比较场景，必须 FDR/BH 校正（这正是原体系防线，集中重审时最易放大假阳性）。
5. **灰度开关**：复用 FTS_ENERGY_QA_REVIEW_APPLY 模式（先 dry-run 后落库）。

**通过率预期（诚实声明，宁严勿松）**：总保留率 15~25%（约 55~90 因子）——active 保留 ~60-80%（24→14-19）、shadow 转正 5-10%、degraded 恢复 <5%。通过率低是正常的（重审标准是策略输入视角，比截面 alpha 更贴合 RD 消费）。

### 6.8.1 Stage 3 状态识别贡献计算（细化）

**定义**：因子信号对"区分市场状态"的增量信息量。三维度，前两个主判据、第三个辅助。

**维度 1 条件区分度（KW 检验，复用 `regime_validation.validate_regime_predictive_power`）**：

```
H = 12/(N(N+1)) × Σₖ nₖ(R̄ₖ − (N+1)/2)²    # 状态 k 样本 nₖ、平均秩 R̄ₖ
p < 0.05（df=4，5 状态）→ 因子能区分状态
```

**维度 2 增量贡献（ΔAUC，"增量"核心度量）**：

```
基线特征集 B = regime_features（volume_shock/偏度/峰度/自相关/波幅比/跨品种相关）
分类器：LightGBM 多分类预测状态（OVR 聚合 macro-AUC）
训练：时间切分（前 70% / 后 30% OOS），3 次不同切分取均值
ΔAUC = AUC(B ∪ {因子}) − AUC(B)；门槛：ΔAUC ≥ 0.005~0.01 且 OOS 均值非负
```

防偏关键：**必须时间切分 OOS**（Regime 是时序状态，随机 K 折泄漏未来状态）；基线 B 固定（与候选因子正交比较）。

**维度 3 状态预测力（领先性，辅助）**：

```
x_t → s_{t+lag}（lag=5/10 日，状态为低频慢变量）
Spearman(x_t, 未来状态得分) 或单变量逻辑回归系数显著性
门槛：领先 5 日 |ρ| ≥ 0.05
```

**组合判定**：

```
contribution_score = 0.4×I(KW显著) + 0.4×I(ΔAUC≥门槛) + 0.2×I(领先显著)
状态识别有效 ⇔ KW 显著 AND ΔAUC ≥ 门槛
            或 KW 显著 AND 领先预测显著（ΔAUC 不过但具预测力）
```

权重进新系统配置，双主判据同时过体现宁严勿松。

### 6.8.2 Stage 5 FDR 校正与 audit 抽查口径（细化）

**FDR 校正（group-wise，防 362 集中重审放大假阳性）**——按检验族分层，非 362 个 p 混一池：

| 检验族 | 因子数 | 主检验 |
|:--|:---|:-----|
| 族 1：L1 复检 | 24 | IC 时序 t 检验 p |
| 族 2：L2 转正 | 172 | 影子期 IC 检验 p |
| 族 3：L3 恢复 | 190 | 恢复窗口 IC 检验 p |

```
每因子一个"主检验"p 进入本族 FDR（避免因子内 IC/KW/ΔAUC 多 p 混入再放大）
BH 校正：qᵢ = pᵢ × m / rankᵢ，q ≤ 0.05 通过
分族理由：三族假设独立（复检/转正/恢复是不同检验问题），混池互相稀释
```

**audit 抽查（分层抽样 + 风险导向）**：

```
全量必审：promote 候选（100%）—— 进新系统的因子一个不落
分层抽样：observe 30%（临界优先：决策置信度 < 0.7 的因子权重 ×2）
         retire 10%（低风险）
最小样本：每族 ≥ max(20, 族数×10%)
```

**抽查目的与边界**：验证自动化评估管线的正确性（发现管线 bug/口径错误），**非再筛一轮因子**。不一致处理：1 例显著不一致 → 该因子降级复核 + 管线检查；≥3 例 → **暂停重审、修管线后重跑**。与 FDR 互补不替代（FDR 管统计假阳性，audit 管流程正确性）。

### 6.8.3 Stage 2 信号重算与历史回填衔接（细化）

**输入**：362 因子代码（读 code/code_hash/params_hash）+ P0 24 品种 × 日期范围（训练窗 250 日 + OOS 窗 60 日）。

```
执行流程：
① 版本锁定：回填执行 code 与库中 code 双哈希校验——一致 → 直接回填；
   不一致（因子已演化变更）→ 用当前 code 重算（重审本就是重新评估）
② 历史面板：FTSDataProvider（QUANTDATA 置首）读 QuantData 全历史 continuous_daily
③ 批量回填：复用 build_signal_matrix，按 (date_range, factor_ids) 分片，断点续跑（幂等）
④ 拼接校验：回填历史矩阵（310 日）与存量滚动矩阵重叠区一致性校验（容差 1e-8）；
   不一致 → 以回填为准（统一口径）

存储隔离：回填信号存新系统"重审工作区"（Parquet），不污染生产 l3_signal_store
并行约束：DuckDB 写约束下单进程批量 + 幂等续跑（对齐单进程回归纪律）
衔接关键：日期轴统一（全因子同一日期序列）、复权口径统一（后复权）、缺失 NaN 保留、与 RD 消费口径一致
```

**核心衔接点**：版本锁定（双哈希）保证"回填的就是库里那个因子"；拼接校验保证"历史窗与滚动窗无缝"；工作区隔离保证"重审不污染生产信号"。

### 6.8.4 Stage 6 落库 schema 映射（细化）

**新系统因子资产库（精简 schema）**——基于 factor_catalog 裁剪，保留生命周期核心 + 画像（RD 消费输入）：

| FTS 字段 | 新系统字段 | 映射规则 |
|:-----|:-----|:-----|
| factor_id | source_factor_id | 原样保留（血缘字段） |
| code | code | 原样 |
| code_hash / params_hash | code_hash / params_hash | 原样（版本一致性） |
| market=energy | market=futures + chain | 扩展为全期货 + 子链标注 |
| status | status | promote→active / observe→shadow / retire→retired |
| is_elite | is_core | 按新标准重判 |
| metadata | metadata | 选择性迁移（重审后仍有效的画像） |
| factor_versions | 不迁移 | 仅留最新版（历史归档） |
| factor_audit_reports | 不迁移 | 重审重新 audit |
| seed_lineage | 不迁移 | 新系统源头变化 |
| subchain_factor_quality | 同构迁移 | Stage 4 重建后写入 |
| factor_evaluations | 同构写入 | Stage 3 新评估 |

**关键决策**：不迁移版本历史/旧审计/旧血缘（重审产生新记录）；原 factor_id 作为血缘字段保证可追溯；UPSERT 幂等（重复重审不产生脏数据）；**FTS 库冻结只读归档（不删，供追溯）**。

---

## 七、风险与对策

| # | 风险 | 等级 | 对策 |
|:--|:-----|:----|:-----|
| 1 | 数据口径错位（FTS 信号 vs RD 执行） | 高 | 已基本消除（聚合器 QUANTDATA 置首）；固化"因子计算强制 QuantData、禁落 kline_cache"约束 + 口径对齐验证脚本 |
| 2 | 因子映射语义漂移（RD 11 因子 ↔ FTS 等价） | 高 | 映射表带 ≥0.95 相关性验证门槛，阶段 0 A/B 对照兜底 |
| 3 | 信号矩阵 schema 变更破坏 RD | 中 | schema_version 约束 + 增量追加 + 幂等 |
| 4 | FTS 大量 L3 函数退役的调用纠缠 | 中 | 阶段 2 按调用图逐层剥离，709 测试回归护航 |
| 5 | 拥挤度双实现不一致 | 中 | 以 FTS 版为权威替换 RD，接口契约不变 |
| 6 | 历史回填与实盘信号漂移 | 低 | 双哈希版本一致性 + 回填/实盘交叉验证（≥0.95） |
| 7 | 子链化样本稀疏 | 低 | 子链 HMM 复用既有验证流程（test_hmm_drift/回测） |
| 8 | 因子生产扩展不达预期（全期货覆盖后新因子产出质量低） | 高 | 覆盖扩展分链推进（§9 步骤 0 规划 + §5.4 顺序），每链先小规模验证再铺开；RD 本地 11 因子在扩展完成前兜底，保证系统始终可运行 |

---

## 八、开放项（规模后决策，暂不阻塞设计）

| # | 开放项 | 当前处理 |
|:--|:-----|:-----|
| 1 | 信号接口服务化（只读 API / 鉴权 / 限流） | 初期 duckdb `read_only` 直读，规模上升后再仿 QuantData 只读 API |
| 2 | 全期货覆盖节奏（P0-P3 扩展进度） | 按 §9 步骤 0 优先级分链推进，每链先小规模验证再铺开；RD 本地 11 因子在扩展完成前兜底 |

---

## 九、实施顺序（评审确认后按序推进，不含时间）

0. **全期货覆盖规划（2026-08-19 修订，替代原根因实证——用户确认无需探查因子价值）**：以 RD 全市场骨架（6 产业链）为策略消费基准、FTS 17 产业链为因子生产细化，覆盖 84 品种，扩展优先级 P0→P3：

   - **P0 能源化工（24 品种，先行）**：能源 SC/FU/BU/LU、聚酯链 TA/PF/EG/PX/PR、油化工 L/PP/V/PG/EB/BZ/PL、煤化工 MA/SA/UR/FG/SH、橡胶 RU/BR/NR。存量因子（含退役）集中重审衔接。
   - **P1 黑色系（8）+ 有色金属（9）（17 品种）**：RB/HC/I/J/JM/SS/SM/SF；CU/AL/ZN/NI/PB/SN/BC/AO/AD。量价适用强、流动性好。
   - **P2 农产品群 + 贵金属（27 品种）**：油脂油料 M/Y/A/B/P/OI/RM/RS/PK、谷物 C/CS/RR/WH/JR/RI/LR、软商品 SR/CF/CY、畜牧 LH/JD、果蔬 AP/CJ、贵金属 AU/AG/PT/PD。
   - **P3 金融期货（8）+ 新能源/新材料（3）+ 造纸/航运（5）（16 品种）**：IF/IH/IC/IM/T/TF/TS/TL；LC/SI/PS；SP/LG/FB/OP/EC。逻辑特殊或数据浅，先做 QuantData 可用性核查。
1. 固化信号契约 v1 + 接口文档（§三）。
2. FTS 信号服务新增历史回填模式 + `schema_version`/`factor_status`/`factor_scope` 列（§三、§6.5）。
3. RD `signal_client.py`（双模式拉取 + 幂等 + 新鲜度 + 降级熔断）（§6.6）。
4. 因子映射表 + `verify_factor_mapping.py`（§4.3）。
5. 阶段 0 A/B 对照（§5.1）。
6. RD `strategy_synthesis.py` + `combo_verifier.py` + 权重/资金迁移（weight_learning/capital_allocator）（§2.3、§6.1）。
7. RD 拥挤度实现替换（FTS 版为权威）（§2.3）。
8. RD L2 子链化（§6.4）。
9. RD `backtest_engine` 消费信号矩阵（§6.5）。
10. 阶段 1 双轨对账（§5.2）。
11. 因子周期管理系统新建（v0.2：复用基础设施 + 重设计生产 §6.7 + 重建生命周期），FTS 因子资产库切换至新系统（§6.7）。
12. 阶段 2 FTS 退役 L3 组合侧 + 测试/文档/版本 + 全量回归（§5.3、§四）。
13. energy 链完整闭环验收 → 评估扩展路线（§5.4、§八）→ 特异因子品种级激活增强（§6.3 两层解耦）。

---

## 一致性元数据

| 代码 → 文档映射 | 可验证断言 | 检验方式 |
|:-----|:-----|:-----|
| `l3_signal_service.py::_init_tables` → §三 契约载体 | l3_signal_meta 含 schema_version/factor_status/factor_scope 三列 | `duckdb DESCRIBE l3_signal_meta` |
| `data_futures.py::_init_default_aggregator` → §1.3-1 | 聚合器源顺序 QUANTDATA 置首 | 代码审查 / 单测 |
| `weight_learning.py` / `capital_allocator.py` / `regime_crowding.py` → §2.3、§4.1 | 迁移后 FTS 侧无策略侧消费引用 | `grep` 调用点 |
| `l3_identifier.py`（RD）→ §2.4 | L3 判定逻辑未改动，仅 router 新增平行路径 | git diff 审查 |
| 过渡路径 §五 → §9 实施顺序 | 阶段 0/1/2 验收门槛逐条通过 | 对账脚本 + 测试 |
| §6.8 重审流程 → 新系统首轮资产 | 重审 8 阶段管道可实现；Stage 5 FDR 分族（复检/转正/恢复）q ≤ 0.05；audit promote 100% 必审 | 重审运行日志 + FDR/audit 报告 |
| §9 步骤 0 覆盖规划 → 全期货因子生产 | P0-P3 四优先级清单落地（84 品种 / 17 产业链） | 配置 + 品种清单核对 |
