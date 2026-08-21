# FTS 晋级计划

> 版本: v3.1.0+7
> 最后更新: 2026-08-20
> 状态: 活跃 — 随项目迭代持续更新

> **版本策略（v2.103.0 修订）**：版本号 = 里程碑版本 + build 段（SemVer build 段制）。日常开发（GAP 实现、测试、文档）通过 `python scripts/bump_version.py --build --message "..."` bump build 段（不限次）；满足发布条件（晋级里程碑完成 + 全量回归通过）时通过 `--type patch|minor|major --message "..."` bump 正式版本（build 清零，单日限一次）。详见 [07-operations.md §6 版本升级流程](07-operations.md)。

---

## 1. 晋级总览

当前基线：**v3.1.0+3**（2026-08-20）。主线演进：

```
v0.1.0 → v1.x（期货自治/信号管道/逻辑审查）→ v2.x（DuckDB 因子库/机构级追赶/Regime 优化）
     → v2.105.0（子链张量化四层闭环）→ v3.0.0（双系统切分：FTS 因子生产 / Regime-Driven 策略合成）
     → v3.1.0（评审质检体系优化，plans/59 八项全部完成）→ 当前 v3.1.0+3
```

- **v3.0.0 架构拐点（plans/57）**：FTS 角色重定位为**因子生产系统**，策略合成职责迁移外部 Regime-Driven；定时任务全部面向全期货（84 品种/17 产业链）。
- **数据主链路**：QuantData 唯一权威 K 线源（duckdb 只读短连接），降级链 `QUANTDATA → DUCKDB_CACHE → TDX_LOCAL → TQ_PYTHON → AKSHARE → SYNTHETIC`。

---

## 2. 已完成里程碑（近期）

### 换月日历根治（2026-08-20，build v3.1.0+3，plans/60 阶段A）

主链路统一消费 QuantData continuous_daily 复权序列：get_ohlcv QUANTDATA 来源跳过 RollCalendar 二次复权（消除双重复权）；aggregator 缓存命中保留真实 source；日线 cache_max_age_days 30→1；_write_cache 幂等覆盖；fetch_ohlcv 透传 adj_factor。验证：全 88 品种复权序列与 QuantData 0 偏差、346 因子全量信号重算、active 86 因子符号同向率 65.1%；523 测试全绿。

### 评审质检体系优化（2026-08-20，里程碑版本 v3.1.0，plans/59 八项全部完成）

GAP-161~168 关闭：Regime 条件化门槛 / 跨运行累积 FDR 折扣 / 特异因子观察期与 OOS 前瞻复核 / IC 口径一致性校验 / 数据质量-评审联动 / 人审 SLA 自动降级 / 参数稳健区动态化 / 容量-交易性评分流动性环境动态化。+124 新用例，全量回归 8454 passed。

### 双系统切分：FTS 因子生产 / Regime-Driven 策略合成（2026-08-20，里程碑版本 v3.0.0，plans/57）

**架构意义**：FTS 角色重定位为**因子生产系统**，策略合成职责整体迁移外部 Regime-Driven——① 信号契约 v1 固化（design/F.3：l3_signal_meta 追加 schema_version/factor_status/factor_scope 三列 + 历史回填 + 决策/训练双模式隔离 + 增量幂等 + 新鲜度校验 + 降级熔断）；② FTS L3 组合侧退役登记（retired_l3.py 35 项 + DeprecationWarning + warn_if_retired 告警，存量调用兼容不删码；物理删除为后续独立里程碑）；③ RD 承接（strategy_synthesis/combo_verifier/money_management/crowding_gate 权威替换/backtest_engine 消费信号矩阵/L2 子链化 5 子链）；④ 全期货覆盖规划（coverage_priority P0-P3，84 品种/17 产业链）；⑤ 存量因子集中重审管道（review_legacy_factors.py 分族 FDR-BH + audit 分层抽样）。

**验收证据（2026-08-20 实测）**：阶段 0 A/B（状态一致率 92.04% / 方向一致率 97.55% 双门槛通过）+ 阶段 1 双轨对账（信号余弦 1.0000 / 组合方向 100% / 敞口差 0.00% / 绩效差 0.000000 全门槛通过）+ 因子映射 10/12 Spearman=1.0000 + 全量回归 8129 passed（残留 21 项预存，登记 GAP-158）。

### QuantData 权威主链路 + 外部因子常态化导入 + 期货结构约束（2026-08-19，build v2.105.0+32，GAP-156/157/158）

① 主链路切换：`quantdata_provider.py`（duckdb 只读短连接直读 continuous_daily/continuous_map/kline_daily，FTS_QUANTDATA_HOME 配置）置首 aggregator 降级链，82/82 品种映射全覆盖，复权用 QuantData 自带 adj_factor（RollCalendar 降级避免双重复权），settle 非权威标注；② 期限结构权威构建：continuous_map 近远月映射 → term_spread/roll_yield，D15 算子转可用；③ 外部因子导入管道：6 个 extract_* YAML 升级为常态化导入（字段权威校验 L2 缺失禁依赖 → 去重 → 注入 → L2 种子评估链准入），月度调度；④ 演化挖掘期货结构约束（R1 字段可得性 / R2 子链有效性 / R3 信号去冗余 / R4 家族筛选 / R5 期限结构接线）。

**数据边界（诚实标注）**：QuantData 无 fundamental（库存/仓单/现货基差，GAP-157 禁依赖）、无 settle/amount（GAP-158 L1 降级）、主力连续历史 ~2019 起（长历史由 kline_cache 兜底）。

### 子链张量化四层闭环（2026-08-17，里程碑版本 v2.105.0，plans/47/48/49/50）

子链差异化与跨市场结构感知（47）/ Regime 分层方向 Gate 与品种暴露缩放（48）/ 因子×子链质量矩阵与生命周期张量化（49）/ L3 权重层 Gate 闭环（50）四层全部落地。退化检测走单元粒度（因子×子链），部分链失效→scope 收缩（metadata.subchain_scope + 47 调制矩阵），全部有效链失效→degrade。v2.105.0 起 `l3.subchain_quality.enabled=true`。

---

## 3. 历史里程碑摘要

已完成的主线里程碑（v3.0.0 之前，完整明细见 07-operations.md §1 版本历史与 git 历史）：

| 版本 | 主题 | 关键产出 |
|:-----|:-----|:---------|
| v2.105.0 | 子链张量化四层闭环 | 47/48/49/50 计划全部落地，退化检测单元粒度化 |
| v2.104.x | 组合权重 Gate / 质检落库 SSOT / 测试因子库隔离 | GAP-125~138 系列关闭 |
| v2.88.0 | 覆盖率 <90% 模块补齐 | GAP-F16，+341 用例，TOTAL 94.31% |
| v2.79.0 | CI 质量门禁 + 极值扰动否决 + 种子去重 | GAP-F12/F15/F10，mypy/ruff 全量收敛 |
| v2.60.0~2.75.0 | 期货流水线机构级缺陷修复 / 深度因子学习 / 权重学习 | GAP-F01~F16 系列 + GAP-I2xx 系列 |
| v2.58.0 | 期货连续合约复权 + 展期仿真 | RollCalendar + 展期成本仿真（GAP-046） |
| v2.49.0 | 高 IC 因子筛查剔除 | HighICScreener（A/B/C/PASS 四级评级） |
| v2.28.0 | 回测流水线计算错误修复 | 换手率/成本时序对齐/IC 方法统一 |
| v2.10.0 | 算子演化引擎 | OperatorEvolutionEngine + FTS-Expr DSL 基础层 |
| v2.9.0 | Design 全量落地 | 9 个设计文档全部实现 |
| v2.5.0 | 种子 YAML 化 + 精英因子 DuckDB 迁移 | 563 种子 YAML + 680 精英 4 表 |
| v2.0.0 | 因果结构审查 + 持续监控仪表盘 | causal_validator + logic_monitor |
| v1.6.0 | 期货自治循环 | L1/L2/L3 全自动调度 + 期货种子库 + 信号管道 |
| v1.2.0 | 种子因子集成 | 世坤 101 + Qlib 158（268 种子） |
| v1.1.0 | MCP 数据源迁移 | Data-Core → akshare |
| v0.1.0 | 从 FDT 剥离 | Phase 1-7，220 测试全绿 |

---

## 4. 下阶段方向

当前演进方向（细则见 [production_plan.md](../archive/plans/production_plan.md) 与 [08-gap-analysis.md](08-gap-analysis.md) 开放差距）：

1. **FTS L3 组合侧物理退役**：v3.0.0 已完成退役登记（retired_l3.py 35 项），物理删除为后续独立里程碑。
2. **全期货覆盖（coverage_priority P0-P3）**：84 品种/17 产业链分级落地，验证外部因子常态化导入质量。
3. **开放差距收敛**：08-gap-analysis 中开放 GAP（P0/P1/P2）按优先级推进，登记/关闭流程见 08。
4. **测试与覆盖率**：当前 8469 测试（2026-08-20 收集口径），随改动持续补充。

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件定义 FTS 版本晋级路线与已完成里程碑，里程碑版本号与 `pyproject.toml` / `docs/harness/07-operations.md` §1 版本历史一致 |
| 可验证断言 | 版本头 `> 版本: v3.1.0+3` 与 pyproject.toml 一致；§2 近期里程碑均为 2026-08-20 前已完成项 |
| 检验方式 | `python scripts/verify_doc_consistency.py`（版本号一致性 PASS）；`python -c "import fts; print(fts.__version__)"` 与版本头比对 |
