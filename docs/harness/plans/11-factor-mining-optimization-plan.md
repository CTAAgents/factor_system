# FTS 因子挖掘模块优化计划

> 版本: v2.104.0+77
> 创建: 2026-08-05
> 最后更新: 2026-08-05
> 状态: 规划中

---

## 1. 背景与定位

FTS 已构建了核心的 L1/L2/L3 三层演化引擎，实现了从种子因子加载、LLM 演化、optuna 调参、三级评估到组合构建的基础闭环。但对照端到端工业级因子挖掘流水线的要求，当前模块存在以下系统性差距：

1.  **因子质量体系不完备**：仅基于 pass/fail 判定，缺乏多维度定量评分、衰减追踪和容量评估。
2.  **组合自适应能力弱**：权重分配静态化，未与市场制度（Regime）检测结果联动。
3.  **数据与回测基础设施薄弱**：缺少数据质量监控、完整的回测流水线和标准化审计流程。
4.  **实盘与反馈闭环缺失**：无实盘对接接口，缺乏系统化的表现归因和演化方向调整。

本计划旨在将 FTS 因子挖掘能力从"核心引擎"升级为"端到端闭环系统"。

---

## 2. 实施路线图

```text
Phase A（核心闭环增强，P0）        Phase B（基础设施完善，P1）      Phase C（系统扩展，P2）
├── A.1 因子质量评分卡 (多维度)    ├── B.1 数据质量实时监控        ├── C.1 特征工程中台
├── A.2 因子衰减追踪与自动淘汰    ├── B.2 端到端回测流水线        ├── C.2 实盘对接与实时监控
└── A.3 自适应动态权重调整        └── B.3 因子审计流程标准化      └── C.3 系统化反馈闭环
```

---

## 3. Phase A — 核心闭环增强 (P0)

### A.1 因子质量评分卡

**目标**: 建立 10+ 维度的因子定量评分体系，替代当前的 pass/fail 判定。

**方案**:
- 在 `evaluation_chain.py` 中新增 `FactorQualityCard` 类。
- 评分维度（0-5 分/维，总分 50 分）：
  1.  **有效性**: IC/ICIR（0.08 以上满分）
  2.  **收益性**: Sharpe、Calmar
  3.  **稳定性**: IC 跨期一致性、WalkForward 通过率
  4.  **鲁棒性**: 因子衰减率、压力测试表现
  5.  **容量**: 资金承载量估算
  6.  **交易性**: 换手率、流动性
  7.  **多样性**: 与现有因子相关性
  8.  **逻辑性**: 经济/行为/微观结构解释力
  9.  **实时性**: 数据更新频率与因子响应速度
  10. **兼容性**: 跨品种/跨市场泛化能力
- 输出 `FactorQualityScore` 对象，包含各维度明细和总分。

**改动文件**:
- `fts/factor_engine/evaluation_chain.py` — 新增 `FactorQualityCard` 类
- `fts/factor_engine/factor_db/schema.py` — 新增 `factor_quality_score` 字段
- `fts/factor_engine/factor_db/repository.py` — 新增评分读写方法

**验收标准**:
- 每个因子评估结果包含 10 维度评分明细
- 评分结果可在因子库中查询和排序
- 评估函数接口保持向后兼容

### A.2 因子衰减追踪与自动淘汰

**目标**: 建立因子生命周期管理，自动识别衰减因子并触发降级/淘汰。

**方案**:
- 在 `elite_tracker.py` 中扩展衰减追踪逻辑。
- **定期增量评估**: 每月对所有 active 因子做增量重评估，计算 IC/Sharpe 环比变化。
- **衰减判定**:
  - 连续 3 个月 IC < 0 → 标记 `decaying`
  - 连续 6 个月 Sharpe 下降 > 50% → 标记 `critical_decay`
  - 连续 12 个月无改善 → 触发淘汰流程
- **分级准入/淘汰**:
  - A 级 (总分 ≥ 40) → active 直接入库
  - B 级 (总分 30-40) → 进入观察期 (3 个月)
  - C 级 (总分 < 30) → 淘汰
- **淘汰回流**: 淘汰因子保留历史，进入 `deprecated` 池，支持回滚。

**改动文件**:
- `fts/monitor/elite_tracker.py` — 扩展衰减追踪
- `fts/factor_engine/evolution_loop.py` — 修改 `_promote_to_elite` 增加分级逻辑
- `fts/factor_engine/factor_db/schema.py` — 因子状态机扩展

**验收标准**:
- 每月自动生成因子衰减报告
- 存在因子从 active → decaying → deprecated 的完整路径
- 淘汰因子可查询历史记录

### A.3 自适应动态权重调整

**目标**: 将 Regime 检测结果反馈到组合权重，实现市场状态自适应。

**方案**:
- 在 `portfolio_loop.py` 中集成 `MarketRegimeDetector`。
- **权重调整规则**:
  - **Bull 趋势市**: 增加动量/趋势因子权重 (权重 × 1.3)
  - **Bear 趋势市**: 增加防御/低波动因子权重，降低高 beta 因子
  - **震荡市**: 增加均值回归/反转因子权重
  - **高波动期 (HV)**: 缩减整体因子池，剔除衰减过快因子
- **动态因子池**: 根据 Regime 结果动态调整参与组合的因子子集。
- **权重更新频率**: 支持日频/周频/月频三种模式。

**改动文件**:
- `fts/factor_engine/portfolio_loop.py` — 集成 Regime 反馈
- `fts/factor_engine/regime.py` — 增强信号输出格式

**验收标准**:
- 组合权重随 Regime 变化自动调整
- 不同市场状态下因子池组成不同
- 回测对比显示自适应权重优于静态权重

---

## 4. Phase B — 基础设施完善 (P1)

### B.1 数据质量实时监控

**目标**: 建立数据完整性、准确性、及时性三维监控。

**方案**:
- 在 `monitor/` 中新增数据质量监控模块。
- **监控指标**:
  - 数据完整性: 缺失率、覆盖品种数、时间戳连续率
  - 数据准确性: 多源交叉验证偏差、异常值检测、跳点识别
  - 数据及时性: 数据更新延迟、缓存命中率
- **Prometheus 指标**: `data_completeness_ratio`、`data_accuracy_deviation`、`data_delay_seconds`
- **告警阈值**: 缺失率 > 5% (Warning)、> 10% (Critical)；偏差 > 2% (Warning)、> 5% (Critical)

**改动文件**:
- `fts/monitor/` — 新增数据质量监控
- `fts/data_sources/aggregator.py` — 增加数据质量埋点
- `docs/harness/05-observability.md` — 更新监控指标文档

**验收标准**:
- 数据质量指标在 HTTP 端点可查询
- 异常数据触发告警
- 数据血缘可追溯

### B.2 端到端回测流水线

**目标**: 建立标准化的"因子→策略→组合→交易"回测管道。

**方案**:
- 新增 `backtest_pipeline.py` 模块。
- **流水线阶段**:
  1. `factor_screening` → 筛选候选因子
  2. `signal_generation` → 生成因子信号
  3. `portfolio_construction` → 组合构建与优化
  4. `cost_simulation` → 真实成本模拟（品种差异化费率）
  5. `risk_attribution` → 风险归因分析
  6. `report_generation` → 自动生成可视化报告
- **批量评估**: 支持 N 个因子统一回测、对比分析、排名输出。
- **资金管理**: 支持固定仓位、波动率目标、风险平价等模式。

**改动文件**:
- `fts/factor_engine/backtest_pipeline.py` — 新增流水线
- `fts/factor_engine/cost_model.py` — 增强成本模拟
- `fts/factor_engine/portfolio_loop.py` — 集成流水线

**验收标准**:
- 因子回测可一键运行完整流水线
- 自动生成包含净值曲线、回撤、IC 时序的完整报告
- 支持批量因子对比排名

### B.3 因子审计流程标准化

**目标**: 建立系统化的因子审计清单与自动化流程。

**方案**:
- 新增 `FactorAuditor` 类，定义审计清单。
- **审计项**:
  1. **因果检验**: Granger 因果 / 反事实分析
  2. **样本外验证**: WalkForward OOS 占比
  3. **跨品种验证**: ≥ 80% 品种 IC 为正
  4. **压力测试**: 极端行情下表现
  5. **多重检验**: Bonferroni / FDR 校正通过
  6. **数据窥探检验**: 无未来函数
- **审计流程**: 所有候选因子必须通过全部审计项才能入库。
- **审计报告**: 自动生成标准化审计报告。

**改动文件**:
- `fts/factor_engine/audit.py` — 新增审计模块
- `fts/factor_engine/evaluation_chain.py` — 集成审计检查
- `fts/factor_engine/causal_validator.py` — 增强因果检验

**验收标准**:
- 因子入库前必须通过全部审计项
- 审计报告可查询
- 审计结果作为因子评分卡的输入项

---

## 5. Phase C — 系统扩展 (P2)

### C.1 特征工程中台

**目标**: 建立算子库，支持特征自动构造，作为 LLM 演化的补充搜索路径。

**方案**:
- 新增 `feature_ops/` 模块，实现特征算子库。
- **算子类型**:
  - 基础算子: `rank`, `zscore`, `delta`, `ts_mean`, `ts_std`, `ts_max`, `ts_min`
  - 组合算子: 支持算子嵌套组合（如 `rank(ts_mean(close, 20))`）
  - 跨品种算子: 截面排名、行业中性化
- **特征搜索**: 基于 GP (gplearn) 在算子空间搜索最优表达式。
- **特征重要性**: SHAP 值分析识别关键特征。

**改动文件**:
- `fts/factor_engine/feature_ops/` — 新增算子库
- `fts/factor_engine/gp_evolution.py` — 新增 GP 演化（Phase C.1 of 10-plan）
- `fts/factor_engine/evaluation_chain.py` — 特征归因分析

**验收标准**:
- 可通过算子组合自动生成特征
- GP 搜索产出因子进入评估链竞争
- SHAP 分析报告特征重要性排名

### C.2 实盘对接与实时监控

**目标**: 定义信号契约，预留实盘交易接口，建立实时风控。

**方案**:
- **信号格式契约**: 定义标准信号 JSON Schema（品种、方向、仓位、置信度、因子 ID、时间戳）。
- **交易接口**: 预留 CTP/XTP 等期货 API 适配层。
- **实时风控**:
  - 单品种仓位上限
  - 组合最大回撤限制
  - 单日最大亏损限制
  - 杠杆率限制
- **Live 因子监控**: 实时对比 live 表现与回测表现，计算实现偏差。

**改动文件**:
- `fts/factor_engine/signal_contract.py` — 信号契约
- `fts/risk/` — 新增实时风控模块
- `fts/monitor/live_monitor.py` — 新增 Live 监控

**验收标准**:
- 信号格式标准化，可被下游交易系统消费
- 风控规则在信号生成后自动检查
- Live 因子表现偏差实时告警

### C.3 系统化反馈闭环

**目标**: 建立"因子表现→归因→演化方向调整"的完整闭环。

**方案**:
- **反馈触发**: 当 Live 表现偏离回测预期 > 30% 时自动触发归因分析。
- **归因分析**: 识别偏离原因（因子失效、市场状态切换、数据问题、实现 bug）。
- **方向调整**: 根据归因结果调整下一轮演化的搜索方向（如增加均值回归因子搜索权重）。
- **主动学习**: 重大市场事件（政策变化、流动性危机）主动触发因子重评估。
- **迭代评估**: 每月量化迭代效果（新因子数、有效率、Sharpe 提升、衰减率下降）。

**改动文件**:
- `fts/factor_engine/feedback_loop.py` — 新增反馈模块
- `fts/factor_engine/evolution_loop.py` — 集成反馈触发
- `fts/monitor/evolution_effectiveness.py` — 迭代效果评估

**验收标准**:
- 因子表现偏离自动触发归因分析
- 归因结果反馈到演化方向调整
- 每月生成迭代效果报告

---

## 6. 验收标准

| 阶段 | 核心标准 | 可量化指标 |
|------|----------|------------|
| **Phase A** | 质量体系完善、权重自适应 | 10 维度评分覆盖率 100%、衰减因子自动降级、Regime 切换时权重自动调整 |
| **Phase B** | 基础设施完备、审计标准化 | 数据质量告警准确率 > 95%、回测流水线端到端运行、审计通过率可追溯 |
| **Phase C** | 闭环形成、生态扩展 | 特征库 ≥ 50 算子、信号契约被下游消费、反馈闭环完整运行 |

---

## 一致性元数据

| 字段 | 值 |
|:-----|:----|
| 代码→文档映射 | 本文件定义 FTS 因子挖掘全流程优化路线图（Phase A/B/C），对应 `evaluation_chain.py`、`portfolio_loop.py`、`elite_tracker.py`、`monitor/`、`audit.py` 等模块的改进计划 |
| 可验证断言 | Phase A 实施中：因子质量评分卡、衰减追踪、自适应权重 |
| 检验方式 | 检查 `evaluation_chain.py` 是否存在 `FactorQualityCard`，`portfolio_loop.py` 是否集成 Regime 反馈，`elite_tracker.py` 是否有衰减追踪逻辑 |